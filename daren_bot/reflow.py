"""feat/daren-reflow (2026-10-18, 老大已批 daren-reflow-design.md): 采集数据回流 SoloBrave。

三条管道全走 localhost HTTP + X-Internal-Secret 共享密钥, daren_bot 永不直接写
solobrave.db (executors.py 的只读隔离是特性, 本模块不破)。

⚠️ 2026-10-21：只读连接的**姿势**也改了 —— 原来用
`sqlite3.connect(f"file:{DB}?mode=ro", uri=True)`，CLT sqlite 3.54 打不开 WAL 库的
只读 URI（`unable to open database file`），异常被上层静默吞掉 → 达人快照永远取不到。
现在统一走 `executors._ro_conn()`（普通连接 + `PRAGMA query_only=ON`）。

- 管道 3: 采集成功后把达人快照 JSON 落 bot_collect_tasks.payload (30 天滚动清理在 db.purge_old_payloads)
- 管道 1: POST /internal/talents/upsert 按 douyin_id 去重回写达人库 10 列白名单
- 管道 2: POST /internal/kb/ingest 推提炼结论, 服务端过价值闸, 不过则拒

所有推送失败只记日志, 绝不阻塞主流程 / 不返还积分 (数据回流是尽力而为的增强)。
"""
import json
import logging
import os
import sqlite3
import time
import urllib.request

from . import db
from . import executors

log = logging.getLogger("daren-bot.reflow")

# 可被 DAREN_SOLO_BASE 覆盖（测试 conftest 会把它指向死端口，防止测试污染 prod 知识库；
# 部署侧不设 = 默认 localhost:8080，行为与改前一致）。**必须运行期读**——模块 import
# 早于 pytest monkeypatch，顶层常量读不到测试的 env。
SOLO_BASE_DEFAULT = "http://127.0.0.1:8080"


def _solo_base() -> str:
    return os.environ.get("DAREN_SOLO_BASE", SOLO_BASE_DEFAULT)
_INTERNAL_SECRET_PATH = executors.SOLOBRAVE_DB.parent / "internal_secret.txt"

# 快照列 = 管道 1 upsert 白名单 (跟 solobrave-server.py 的 _DAREN_UPSERT_COLUMNS 对齐,
# 多出的列服务端会忽略, 这里读窄一点省流量)
_SNAPSHOT_COLUMNS = [
    "douyin_id", "name", "avatar", "followers", "total_gmv",
    "total_gmv_text", "video_gpm_text", "video_settlement_ratio",
    "main_category", "ai_rating", "ai_summary", "ocr_raw_fields",
]

# kb ingest 三类白名单 (design 管道 2: 只收画像洞察/匹配规律/达人显式反馈)
KB_KINDS = ("talent_insight", "match_pattern", "talent_feedback")


def _internal_secret() -> str:
    """读共享密钥; 文件不存在则创建 (0o600, 不进 git)。两端各自容错, 极小竞态下两端读同一份。"""
    try:
        if not _INTERNAL_SECRET_PATH.exists():
            import secrets as _secrets
            _INTERNAL_SECRET_PATH.write_text(_secrets.token_hex(32))
            _INTERNAL_SECRET_PATH.chmod(0o600)
        return _INTERNAL_SECRET_PATH.read_text().strip()
    except OSError as e:
        log.warning("internal secret 不可用: %s", e)
        return ""


def _post(path: str, payload: dict):
    req = urllib.request.Request(
        _solo_base() + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Internal-Secret": _internal_secret(),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read() or b"{}"
        return r.status, json.loads(body.decode("utf-8"))


def snapshot_from_solobrave(douyin_id: str):
    """从 solobrave talents (ro) 按 douyin_id 取快照; 找不到退回按名字兜底, 都没有返回 None。"""
    cols = ", ".join(_SNAPSHOT_COLUMNS)
    conn = executors._ro_conn()
    try:
        row = conn.execute(
            f"SELECT {cols} FROM talents WHERE douyin_id = ? AND douyin_id != '' "
            "ORDER BY updated_at DESC LIMIT 1", (douyin_id,),
        ).fetchone()
        if row:
            return {k: row[i] for i, k in enumerate(_SNAPSHOT_COLUMNS)}
        return None
    finally:
        conn.close()


def push_talent_upsert(snapshot: dict):
    return _post("/internal/talents/upsert", {"snapshot": snapshot})


def push_kb_insight(kind: str, text: str, title: str = "") -> bool:
    """推一条提炼结论进知识库。返回值=False 表示被价值闸拒或失败, 调用方只记日志。"""
    if kind not in KB_KINDS or not text or len(text.strip()) < 10:
        return False
    try:
        status, _ = _post("/internal/kb/ingest",
                          {"kind": kind, "text": text.strip()[:2000], "title": title})
        return status == 200
    except Exception as e:
        log.warning("kb ingest push failed (%s): %s", kind, e)
        return False


# ─── 「聊天即知识」：达人助理产生的分析/问答进知识库（2026-10-27 老大拍板）────────
# 不是消息计数，是聊天里产出的真东西：选品判断结论、账号分析问答。
# 全部走 push_kb_insight（白名单+价值闸+pending 人工审），拒收不报错只记日志。

def push_product_verdict(tenant_key: str, product_title: str, md: str) -> bool:
    """选品判断结论 → match_pattern（商品↔达人的匹配规律）。
    只在**新算出来的结论**处调用（缓存命中不算，没消耗算力也没新知）。"""
    text = (md or "").strip()
    if len(text) < 10:
        return False
    title = f"[选品判断] {product_title or '商品'}（{tenant_key} {time.strftime('%Y-%m-%d')}）"
    return push_kb_insight("match_pattern", text, title=title)


def push_qa_exchange(tenant_key: str, question: str, answer: str) -> bool:
    """一次有实质内容的问答 → talent_insight（达人画像/经营洞察）。
    太短的寒暄、拒答、报错话术不进。"""
    ans = (answer or "").strip()
    if len(ans) < 30 or ans.startswith("拒"):
        return False
    text = f"达人问：{(question or '').strip()[:300]}\nAI答：{ans[:1600]}"
    title = f"[问答] {(question or '').strip()[:40]}（{tenant_key}）"
    return push_kb_insight("talent_insight", text, title=title)


def after_douyin_collect(task: dict) -> None:
    """douyin 采集成功后: 管道 3 落 payload → 管道 1 回写达人库 → 管道 2 推报告洞察。
    任一步失败只记日志, 不影响主流程。"""
    douyin_id = (task.get("target") or "").strip()
    if not douyin_id:
        return
    snap = snapshot_from_solobrave(douyin_id)
    if not snap:
        log.info("reflow: no talent row for %s, skip", douyin_id)
        return
    # 管道 3: 本地留存 (断链重放原料, 保底必做)
    try:
        db.save_task_payload(task["id"], json.dumps(snap, ensure_ascii=False))
    except Exception:
        log.exception("reflow save payload failed: %s", task["id"])
    # 管道 1: 达人库 upsert
    try:
        status, resp = push_talent_upsert(snap)
        if status != 200:
            log.warning("reflow upsert %s -> %s %s", douyin_id, status, resp)
    except Exception:
        log.exception("reflow upsert failed: %s", douyin_id)
    # 管道 2: 报告摘要 → 画像洞察 (服务端过价值闸)
    summary = (snap.get("ai_summary") or "").strip()
    if len(summary) >= 20:
        push_kb_insight("talent_insight", summary,
                        title=f"[达人采集] {snap.get('name') or douyin_id}")
