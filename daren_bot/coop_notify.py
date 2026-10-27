"""合作机会 → 主动通知匹配达人（工单 docs/BACKLOG-coop-automation-工单.md，2026-10-21 认领）。

三层分工（DAREN-ARCHITECTURE.md）：触达=daren_bot / 工作台=7188 /
档案=达人经营驾驶舱（2026-10-27 起；原 bitable_sync 每租户多维表格已废弃）。
本模块是**触达层**的核心增量：不等达人来问，主动把匹配的合作机会推给合适的达人。

流程：
  读飞书「合作机会」表 → 类目匹配达人 → 过滤已绑定租户 + 灰度
  → 去重(record_id+tenant_key) + 频控(每周≤N条) → 推文本卡片（含专属驾驶舱链接）

工单硬要求（本模块逐条落实）：
  ① 防打扰：同一达人每周至多 2 条；同一条记录对同一人只推一次
  ② 灰度：默认只推 daren_boss + daren_test2，跑满一个完整周期再放真达人
  ③ 推送走 daren_bot Sender（按租户自己的凭证，严禁混用）
  ④ 读表复用 lark_records 模式（lark-cli --as user + 120s 缓存）

⚠️ 只丢不造：匹配不到达人 → **不推**。宁可漏推，也不推没有依据的机会。
⚠️ 本期不做：达人侧「接受/拒绝」回写（需 submit 事件，二期）；品牌方视角通知。
"""
import json
import logging
import os
import re
import sqlite3
import threading
import time

from . import config, db
from . import cockpit_link

log = logging.getLogger("daren-bot.coop")

# ---- 灰度名单（硬要求②）--------------------------------------------------------
# 上线默认只推这两个。放量前改这里或设环境变量 DAREN_COOP_GRAY_TENANTS。
COOP_GRAY_TENANTS = tuple(
    t.strip() for t in os.environ.get(
        "DAREN_COOP_GRAY_TENANTS", "daren_boss,daren_test2").split(",") if t.strip())

# 频控：同一达人每周至多推 N 条合作机会（硬要求①）
COOP_WEEKLY_LIMIT = int(os.environ.get("DAREN_COOP_WEEKLY_LIMIT", "2"))
# 一条机会最多推给几个达人（避免一条机会轰炸所有人）
COOP_MAX_PER_RECORD = int(os.environ.get("DAREN_COOP_MAX_PER_RECORD", "3"))
# 扫描间隔（秒）
COOP_SCAN_INTERVAL = int(os.environ.get("DAREN_COOP_SCAN_INTERVAL", "1800"))

# 触发状态：只推「有意向」「已合作」。
# ⚠️ 收敛理由（工单触发条件①"新增记录"与②"状态流转"合并成一条判据）：
#   「待评估」= 运营还没评估过，推给达人既是打扰也可能推错；
#   运营把新记录直接录成「有意向」时同样满足本判据 → 一条条件覆盖两种触发。
COOP_TRIGGER_STATUSES = ("有意向", "已合作")

SOLOBRAVE_DB = config.ROOT / "data" / "solobrave.db"


# ---------------- 读表（复用 7188 侧 lark_records：lark-cli --as user + 120s 缓存） ----

def coop_records() -> list:
    """读飞书「合作机会」表，返回 [{字段名: 值, '__rid': record_id}]。失败返回 []。"""
    try:
        from daren_cockpit.server import lark_records, DEMO_BASE, COOP_TABLE
    except Exception as e:
        log.warning("[coop] 读表模块 import 失败: %s", e)
        return []
    try:
        rows = lark_records(DEMO_BASE, COOP_TABLE) or []
    except Exception as e:
        log.warning("[coop] lark_records 失败: %s", e)
        return []
    return rows


def _status_of(row: dict) -> str:
    """状态字段可能是字符串或单元素列表（飞书单选字段）。"""
    st = row.get("状态") or ""
    if isinstance(st, list):
        st = st[0] if st else ""
    return str(st or "").strip()


def _text_of(row: dict, key: str) -> str:
    v = row.get(key) or ""
    if isinstance(v, list):
        v = v[0] if v else ""
    return str(v).strip()


# ---------------- 匹配：类目 → 达人 -----------------------------------------------

def _category_keywords(category: str) -> list:
    """把合作类目拆成匹配关键词。例：「休闲零食 / 食品饮料」→ ['休闲零食','食品饮料']。"""
    cat = (category or "").strip()
    if not cat:
        return []
    parts = [p.strip() for p in re.split(r"[/、,，\s]+", cat) if p.strip()]
    kws = [p for p in parts if len(p) >= 2]
    return kws or ([cat] if len(cat) >= 2 else [])


def _gray_tenants() -> dict:
    """灰度名单内的租户 → {tenant_key: bind_douyin_id}。"""
    out = {}
    for t in db.get_active_tenants():
        tk = t.get("tenant_key") or ""
        if tk in COOP_GRAY_TENANTS:
            dyid = (t.get("bind_douyin_id") or "").strip()
            if dyid:
                out[tk] = dyid
    return out


def match_talents(category: str, douyin_ids: list) -> list:
    """按合作类目匹配达人，返回 [{talent_id,name,douyin_id,score,reason,source}]。

    两条路径，取并集（同一达人只保留分高的那条）：
      路径 A「商品桥接」：类目 → products.category → product_talent_match → 达人
        —— 自带 match_score 和 match_reason（现成的人话理由，最可信）
      路径 B「类目直配」：talents 的类目字段 → 达人
        —— 没有现成理由时用（理由由本模块基于真数据生成，不编数字）

    ⚠️ 匹配不到就是匹配不到 → 返回空列表，调用方不推。
    """
    if not douyin_ids:
        return []
    kws = _category_keywords(category)
    if not kws:
        return []
    if not SOLOBRAVE_DB.exists():
        log.warning("[coop] 找不到 %s，跳过匹配", SOLOBRAVE_DB)
        return []
    like = "%" + kws[0] + "%"
    out = {}
    try:
        con = sqlite3.connect(str(SOLOBRAVE_DB))
        # query_only 引擎级禁写（比 mode=ro URI 更强，且不受 sqlite 版本×WAL 只读限制）
        con.execute("PRAGMA query_only=ON")
        con.row_factory = sqlite3.Row
        ph = ",".join("?" * len(douyin_ids))

        # ── 路径 A：类目 → 商品 → match 表 ──
        for kw in kws:
            for m in con.execute(
                    "SELECT m.talent_id, m.match_score, m.match_reason, "
                    "       t.name, t.douyin_id, t.main_category, t.category, "
                    "       t.followers, t.city, t.level "
                    "FROM product_talent_match m "
                    "JOIN products p ON p.id=m.product_id "
                    "JOIN talents t ON t.id=m.talent_id "
                    "WHERE p.category LIKE ? AND t.douyin_id IN (" + ph + ") "
                    "ORDER BY m.match_score DESC",
                    [like] + list(douyin_ids)):
                r = dict(m)
                tid = r["talent_id"]
                score = float(r.get("match_score") or 0)
                if tid in out and out[tid]["score"] >= score:
                    continue
                out[tid] = {
                    "talent_id": tid, "name": r.get("name") or "",
                    "douyin_id": r.get("douyin_id") or "",
                    "score": score,
                    "reason": (r.get("match_reason") or "").strip(),
                    "source": "商品匹配",
                }

        # ── 路径 B：类目直配 talents ──
        for kw in kws:
            for t in con.execute(
                    "SELECT id, name, douyin_id, main_category, category, "
                    "       followers, city, level "
                    "FROM talents WHERE douyin_id IN (" + ph + ") "
                    "AND (main_category LIKE ? OR category LIKE ?) "
                    "ORDER BY COALESCE(followers, 0) DESC",
                    list(douyin_ids) + [like, like]):
                r = dict(t)
                tid = r["id"]
                if tid in out:
                    continue
                cat = r.get("main_category") or r.get("category") or ""
                out[tid] = {
                    "talent_id": tid, "name": r.get("name") or "",
                    "douyin_id": r.get("douyin_id") or "",
                    "score": 60.0,          # 类目命中给一个保守的基准分
                    "reason": f"主营类目「{cat}」与本次合作类目一致",
                    "source": "类目匹配",
                }
        con.close()
    except Exception as e:
        log.warning("[coop] 匹配失败: %s", e)
        return []
    ranked = sorted(out.values(), key=lambda x: -x["score"])
    return ranked[:COOP_MAX_PER_RECORD]


# ---------------- 推送 -------------------------------------------------------------

def _fmt_coop_text(record: dict, match: dict, tenant_key: str) -> str:
    """合作机会通知正文（为什么适合你 = 匹配理由 + 真实数据片段）。"""
    brand = _text_of(record, "品牌/商家") or "合作方"
    cat = _text_of(record, "合作类目") or "—"
    comm = _text_of(record, "预算/佣金") or "—"
    desc = _text_of(record, "合作说明") or ""
    reason = match.get("reason") or "类目与你的账号匹配"
    link = cockpit_link.tenant_cockpit_link(tenant_key)
    lines = [
        f"🎯 有一条新的合作机会适合你",
        "",
        f"品牌：{brand}",
        f"合作类目：{cat}",
        f"预算/佣金：{comm}",
    ]
    if desc:
        lines.append(f"合作说明：{desc}")
    lines += [
        "",
        f"为什么适合你：{reason}",
    ]
    if link:
        lines += ["", f"👇 你的专属工作台：{link}"]
    return "\n".join(lines)


def push_to_tenant(ws, record: dict, match: dict, tenant_key: str) -> bool:
    """把一条合作机会推给某个达人。返回是否推送成功。"""
    chat_id = db.get_latest_chat_id(tenant_key)
    if not chat_id:
        log.info("[coop] tenant=%s 无最近会话，跳过推送", tenant_key)
        return False
    sender = ws.get_sender(tenant_key)
    if sender is None:
        log.warning("[coop] tenant=%s 无 sender，跳过推送", tenant_key)
        return False
    try:
        sender.send_text(chat_id, _fmt_coop_text(record, match, tenant_key))
    except Exception:
        log.exception("[coop] 推送失败 tenant=%s", tenant_key)
        return False
    db.log_coop_push(str(record.get("__rid") or ""), tenant_key,
                     _text_of(record, "品牌/商家"), _text_of(record, "合作类目"),
                     db.current_week_no())
    log.info("[coop] 已推送: tenant=%s brand=%s score=%.1f source=%s",
             tenant_key, _text_of(record, "品牌/商家"),
             match.get("score") or 0, match.get("source") or "")
    return True


# ---------------- 主流程 -----------------------------------------------------------

def scan_and_push(ws, dry_run: bool = False) -> dict:
    """扫一遍合作机会表，把匹配的机会推给达人。

    dry_run=True 时只做匹配和决策、**不推送不落日志**（用于安全自检）。
    返回统计 {scanned, matched, pushed, skipped, details}。
    """
    stat = {"scanned": 0, "matched": 0, "pushed": 0, "skipped": 0, "details": []}
    rows = coop_records()
    if not rows:
        log.info("[coop] 合作机会表读不到记录")
        return stat
    gray = _gray_tenants()
    if not gray:
        log.info("[coop] 灰度名单内没有已绑定抖音号的租户 → 本轮不推")
        return stat
    by_dyid = {dyid: tk for tk, dyid in gray.items()}
    week_no = db.current_week_no()
    # ⚠️ dry-run 的决策必须和真实决策**完全一致**，否则 dry-run 就是假探针。
    #   坑：dry-run 不落库 → 频控计数恒为 0 → 频控永远不触发，看起来"都能推"。
    #   修：本周计数与本轮"已推"集合都用**内存副本**模拟，真推时再落库。
    week_counts = {tk: db.coop_push_count_week(tk, week_no) for tk in gray}
    seen_this_round = set()

    for row in rows:
        stat["scanned"] += 1
        rid = str(row.get("__rid") or "")
        status = _status_of(row)
        category = _text_of(row, "合作类目")
        brand = _text_of(row, "品牌/商家")
        if status not in COOP_TRIGGER_STATUSES:
            stat["skipped"] += 1
            stat["details"].append({"rid": rid, "why": f"状态={status or '空'} 不在触发集"})
            continue
        matches = match_talents(category, list(by_dyid.keys()))
        if not matches:
            # 只丢不造：类目匹配不到人 → 不推
            stat["skipped"] += 1
            stat["details"].append({"rid": rid, "why": f"类目「{category}」匹配不到灰度达人"})
            continue
        stat["matched"] += 1
        for m in matches:
            tk = by_dyid.get(m.get("douyin_id") or "")
            if not tk:
                continue
            if rid in seen_this_round or db.coop_push_exists(rid, tk):
                stat["skipped"] += 1
                stat["details"].append({"rid": rid, "tenant": tk, "why": "已推过"})
                continue
            if week_counts.get(tk, 0) >= COOP_WEEKLY_LIMIT:
                stat["skipped"] += 1
                stat["details"].append({"rid": rid, "tenant": tk, "why": "本周已达频控上限"})
                continue
            # 模拟占用配额（dry-run 与真推走同一条路径）
            seen_this_round.add(rid)
            week_counts[tk] = week_counts.get(tk, 0) + 1
            if dry_run:
                stat["details"].append({"rid": rid, "tenant": tk, "why": "dry-run 将推",
                                        "brand": brand, "score": m.get("score")})
                continue
            if push_to_tenant(ws, row, m, tk):
                stat["pushed"] += 1
    if dry_run:
        log.info("[coop] dry-run 统计: %s", stat)
    else:
        log.info("[coop] 扫描统计: %s", {k: v for k, v in stat.items() if k != "details"})
    return stat


class CoopScheduler(threading.Thread):
    """合作机会主动通知调度器（每 COOP_SCAN_INTERVAL 秒扫一次）。"""

    def __init__(self, ws):
        super().__init__(daemon=True, name="coop-scheduler")
        self.ws = ws
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        log.info("coop scheduler started (interval=%ds, gray=%s)",
                 COOP_SCAN_INTERVAL, ",".join(COOP_GRAY_TENANTS))
        # 启动后先等一轮，避免和刚起来的其他模块抢资源
        while not self._stop.wait(COOP_SCAN_INTERVAL):
            try:
                scan_and_push(self.ws)
            except Exception:
                log.exception("coop scheduler tick failed")
