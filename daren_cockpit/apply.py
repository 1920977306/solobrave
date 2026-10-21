"""商家招商「减负」提交 + 自动商品解析。

背景（2026-10-21 老大需求）：商家嫌招商表字段多不愿填。
原路径是飞书多维表格分享表单（8 个字段，其中「商品名/链接/图片」都没有）。
新路径：商家只给
    ① 品牌/商家（必填）② 联系方式（必填）
    ③ 以下任一：商品链接 / 商品图片 / 一段话描述
AI 负责把③变成运营能用的商品字段（商品名/价格/佣金/类目）。

设计约束（与项目既有铁律一致）：
- **只丢不造**：解析不出来就留空 + status='partial'，绝不编商品字段。
- **异步**：链接采集要走团长端浏览器（1-2 分钟，且浏览器是全局单例），
  绝不能让商家在网页上干等 → 提交即刻返回编号，解析在后台线程做。
- **管道复用**：解析结果同步回飞书合作机会表 → 运营的「待评估→有意向/已合作」
  状态流转和 coop_notify 主动推送逻辑**完全不用改**。
"""
import base64
import json
import logging
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

from daren_bot import db as botdb
from daren_bot import executors

log = logging.getLogger("daren-cockpit.apply")

ROOT = Path(__file__).resolve().parent
IMAGE_DIR = ROOT.parent / "data" / "daren-bot" / "apply_images"

# 飞书样板库（合作机会管道）——与 daren_cockpit/server.py 保持一致
DEMO_BASE = "MZEdbVJMeah4x9sYYzncuhnbnnb"
COOP_TABLE = "tblolFFeJTNfyInn"

MAX_IMAGE_BYTES = 12 * 1024 * 1024     # base64 前的原始字节上限
LINK_RE = re.compile(r"^https?://", re.I)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)

# 提交编号字符集：去掉 0/O/1/I/L 等易混字符（商家要念/截图编号）
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _new_code() -> str:
    return "AP-" + "".join(secrets.choice(_CODE_ALPHABET) for _ in range(6))


# ---------------- 限流（防刷） ----------------

_RATE: dict = {}
_RATE_LOCK = threading.Lock()
RATE_LIMIT = 8          # 同一 IP 每小时最多提交次数
RATE_WINDOW = 3600


def _rate_ok(ip: str) -> bool:
    now = time.time()
    with _RATE_LOCK:
        hits = [t for t in _RATE.get(ip, []) if now - t < RATE_WINDOW]
        if len(hits) >= RATE_LIMIT:
            _RATE[ip] = hits
            return False
        hits.append(now)
        _RATE[ip] = hits
        return True


# ---------------- 提交 ----------------

def submit_apply(brand: str, contact: str, link: str = "", desc: str = "",
                 image_b64: str = "", ip: str = "") -> tuple:
    """落一条招商提交并启动后台解析。返回 (code, err)。"""
    brand = (brand or "").strip()[:60]
    contact = (contact or "").strip()[:80]
    link = (link or "").strip()[:500]
    desc = (desc or "").strip()[:1000]

    if not brand:
        return "", "请填写品牌/商家名称"
    if not contact:
        return "", "请留下联系方式（微信号或手机号都行）"

    image_path = ""
    if image_b64:
        # 前端已压过（长边 ≤1400px），这里只做兜底体积校验
        raw = image_b64.split(",", 1)[-1]
        try:
            blob = base64.b64decode(raw, validate=False)
        except Exception:
            return "", "图片数据读不出来，请重新上传"
        if len(blob) > MAX_IMAGE_BYTES:
            return "", "图片太大了，请上传小于 12MB 的图"
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        image_path = str(IMAGE_DIR / f"{_new_code().replace('-', '')}.jpg")
        try:
            Path(image_path).write_bytes(blob)
        except Exception:
            log.exception("apply image save failed")
            image_path = ""

    if not link and not image_path and not desc:
        return "", "商品链接、商品图片、商品描述 —— 至少给一项"

    if link:
        m = _URL_RE.search(link)
        if not m:
            return "", "商品链接格式不对（应以 http:// 或 https:// 开头）"
        link = m.group(0)

    if not _rate_ok(ip or "unknown"):
        return "", "提交太频繁了，稍后再试"

    code = _new_code()
    try:
        botdb.create_coop_apply(code, brand, contact, link, image_path, desc)
    except Exception as e:
        log.exception("coop apply insert failed")
        return "", f"提交失败（{e}）"

    threading.Thread(target=_resolve_apply, args=(code,), daemon=True).start()
    return code, ""


def _user_hint(row: dict) -> str:
    """给商家看的人话提示（真实原因 error 留给运营看）。

    ⚠️ 区分「商家的问题」和「我们的问题」：链接识别失败九成是团长端登录态过期，
       这不是商家的错 —— 提示里不该让商家背锅。
    """
    err = str(row.get("error") or "")
    st = row.get("status")
    if st == "done":
        return ""
    if st == "pending":
        return "已收到，等待识别"
    if st == "parsing":
        return "正在识别商品信息…"
    if "登录态" in err or "采集器" in err:
        return "商品链接暂时无法自动识别（我们这边的采集账号需要重新登录），"
        return "已记录，运营老师会人工帮你看"
    if st == "failed":
        return "商品信息没识别出来，运营老师会人工帮你看"
    if err:
        return "商品信息只识别出一部分，运营老师会人工帮你看"
    return ""


def apply_status(code: str) -> dict:
    """商家查进度：状态 + 已识别到的商品字段。"""
    code = (code or "").strip().upper()
    row = botdb.get_coop_apply(code)
    if not row:
        return {}
    return {
        "code": row.get("code", ""),
        "brand": row.get("brand", ""),
        "status": row.get("status", "pending"),
        "error": row.get("error", ""),
        "user_hint": _user_hint(row),
        "product": {
            "name": row.get("product_name", ""),
            "price": row.get("price", ""),
            "commission": row.get("commission", ""),
            "sales": row.get("sales", ""),
            "category": row.get("category", ""),
            "shop": row.get("shop", ""),
        },
        "created_at": row.get("created_at", 0),
    }


# ---------------- 后台解析 ----------------

APPLY_DESC_PROMPT = '''你是招商信息提取器。商家用一段话描述了想合作的商品，只输出 JSON（不要其他文字）：
{"name":"商品名","category":"商品类目（如 服饰内衣 / 休闲零食 / 美妆护肤）","commission":"佣金或预算（如 佣金20%起，没有就空字符串）","price":"价格（没有就空字符串）"}
规则：
1. 商家话里明确提到的信息才填，没提到的填空字符串，**绝对不要编造**。
2. category 用最贴近的通用类目词，不要写长句子。
3. 如果整段话完全没提商品（只是打招呼），全部填空字符串。'''


def _resolve_apply(code: str) -> None:
    """后台解析一条提交：链接 → 采集；图片 → vision；描述 → LLM 抽字段。"""
    row = botdb.get_coop_apply(code)
    if not row:
        return
    botdb.update_coop_apply(code, status="parsing", error="")
    stats = {}
    sources = []
    errors = []

    # ---- ① 商品链接 → 团长端浏览器采集 ----
    if row.get("link"):
        try:
            stats = executors.collect_product_by_url(row["link"], timeout=300)
            sources.append("link")
        except Exception as e:
            errors.append(f"链接识别失败: {str(e)[:120]}")
            log.warning("[apply] %s link resolve failed: %s", code, e)

    # ---- ② 商品图片 → vision ----
    if not stats and row.get("image_path"):
        p = Path(row["image_path"])
        if p.is_file():
            try:
                b64 = base64.b64encode(p.read_bytes()).decode()
                stats = executors._vision_extract_product(b64)
                sources.append("image")
            except Exception as e:
                errors.append(f"图片识别失败: {str(e)[:120]}")
                log.warning("[apply] %s image resolve failed: %s", code, e)
        else:
            errors.append("图片文件找不到了")

    # ---- ③ 一段话描述 → LLM 抽类目/商品名 ----
    if row.get("desc_text"):
        try:
            out = executors._qa_llm(APPLY_DESC_PROMPT + "\n\n商家原话：\n" + row["desc_text"][:800])
            d = executors._extract_json_obj(out)
            if d:
                # 图片/链接已解析出字段时，描述只补空缺（不覆盖更准的来源）
                for k in ("name", "category", "commission", "price"):
                    if not stats.get(k) and d.get(k):
                        stats[k] = d[k]
                sources.append("desc")
        except Exception as e:
            errors.append(f"描述识别失败: {str(e)[:120]}")
            log.warning("[apply] %s desc resolve failed: %s", code, e)

    # ---- 回填（只丢不造：没解析出来的字段留空）----
    status = "done" if stats.get("name") else ("partial" if sources or errors else "failed")
    botdb.update_coop_apply(
        code,
        status=status,
        source=",".join(sources),
        product_name=str(stats.get("name") or "")[:120],
        price=str(stats.get("price") or "")[:60],
        commission=str(stats.get("commission") or stats.get("commission_rate") or "")[:60],
        sales=str(stats.get("sales") or "")[:60],
        category=str(stats.get("category") or "")[:60],
        shop=str(stats.get("shop") or "")[:60],
        product_url=str(stats.get("resolved_url") or row.get("link") or "")[:500],
        error="；".join(errors)[:400],
    )

    # ---- 同步到飞书合作机会表（走运营既有管道）----
    try:
        rid = _sync_to_lark(botdb.get_coop_apply(code) or row)
        if rid:
            botdb.update_coop_apply(code, coop_record_id=rid)
    except Exception as e:
        log.exception("[apply] %s lark sync failed: %s", code, e)
        cur = botdb.get_coop_apply(code)
        if cur and not cur.get("error"):
            botdb.update_coop_apply(code, error=f"飞书同步失败: {str(e)[:120]}")

    log.info("[apply] %s resolved status=%s source=%s", code, status, ",".join(sources))


# ---------------- 飞书同步 ----------------

def _lark_cli() -> str:
    import shutil
    p = shutil.which("lark-cli")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/lark-cli", "/usr/local/bin/lark-cli"):
        if Path(cand).exists():
            return cand
    return "lark-cli"


def _lark_env() -> dict:
    import os
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin" \
        + os.pathsep + env.get("PATH", "")
    return env


def _coop_has_field(name: str) -> bool:
    """合作机会表是否已有某个字段（加字段是可选增强，缺字段时降级写进合作说明）。"""
    try:
        out = subprocess.run(
            [_lark_cli(), "base", "+field-list",
             "--base-token", DEMO_BASE, "--table-id", COOP_TABLE,
             "--as", "user", "--format", "json"],
            capture_output=True, text=True, timeout=30, env=_lark_env())
        d = json.loads(out.stdout or "{}")
        fields = ((d.get("data") or {}).get("fields") or [])
        return any(f.get("name") == name for f in fields)
    except Exception:
        return False


def _split_contact(contact: str) -> tuple:
    """把商家填的一个联系方式字段拆成（联系人, 联系方式）。

    商家只想填一格（减负），但运营在飞书表里分两列看。
    保守拆分：只有当**明确是「名字 + 账号」两段**时才拆 —— 不做任何猜测式补全。
    """
    c = (contact or "").strip()
    if not c:
        return "", ""
    for sep in (" ", "　", "\t", ","):
        if sep in c:
            parts = [p.strip() for p in c.split(sep) if p.strip()]
            if len(parts) == 2 and len(parts[0]) <= 12:
                return parts[0], parts[1]
    return "", c


def _find_record(brand: str, contact: str) -> str:
    """按「品牌 + 联系方式」回查刚创建的记录 id。

    ⚠️ record-upsert 的响应里 record_id 路径**不固定**：update 响应只有
       {"record":{"update":{...}},"updated":true}，压根不带 id；create 响应的
       路径又可能不同。拿不到 id 就回查，别让「同步成功但拿不到 id」
       变成静默失败（那样运营在表里看得到记录、但本地关联不上）。
    """
    try:
        from daren_cockpit.server import lark_records, _lark_invalidate
        _lark_invalidate(DEMO_BASE, COOP_TABLE)     # 刚写的，缓存里还是旧的
        rows = lark_records(DEMO_BASE, COOP_TABLE) or []
    except Exception:
        return ""
    best, best_ts = "", ""
    for r in rows:
        if str(r.get("品牌/商家") or "") != brand:
            continue
        if contact and str(r.get("联系方式") or "") not in (contact, ""):
            continue
        ts = str(r.get("提交时间") or "")
        if ts >= best_ts:
            best_ts, best = ts, str(r.get("__rid") or "")
    return best


def _sync_to_lark(row: dict) -> str:
    """把一条提交写进飞书合作机会表（新记录）。返回 record_id。"""
    if not row:
        return ""
    brand = row.get("brand") or ""
    contact = row.get("contact") or ""
    category = row.get("category") or ""
    budget = row.get("commission") or ""
    link = row.get("product_url") or row.get("link") or ""
    name = row.get("product_name") or ""

    # 合作说明 = 商家原话 + 商品链接（只丢不造：全是原始输入，不编）
    note_parts = []
    if row.get("desc_text"):
        note_parts.append(str(row["desc_text"]))
    if link:
        note_parts.append(f"商品链接：{link}")
    if name:
        note_parts.append(f"商品：{name}")
    note = " | ".join(p for p in note_parts if p)[:900]

    person, phone = _split_contact(contact)
    fields = {
        "品牌/商家": brand,
        "提交时间": int(time.time() * 1000),
        "合作类目": category,
        "联系方式": phone or contact,
        "联系人": person,
        "预算/佣金": budget,
        "状态": ["待评估"],
        "合作说明": note,
    }
    # 可选字段：表里有就写，没有就降级（不硬失败）
    if name and _coop_has_field("商品名"):
        fields["商品名"] = name
    if link and _coop_has_field("商品链接"):
        fields["商品链接"] = link

    out = subprocess.run(
        [_lark_cli(), "base", "+record-upsert",
         "--base-token", DEMO_BASE, "--table-id", COOP_TABLE,
         "--json", json.dumps(fields, ensure_ascii=False),
         "--as", "user", "--format", "json"],
        capture_output=True, text=True, timeout=60, env=_lark_env())
    d = json.loads(out.stdout or "{}")
    if not d.get("ok"):
        raise RuntimeError((out.stderr or out.stdout or "lark-cli 失败")[:200])
    data = d.get("data") or {}
    rec = data.get("record") if isinstance(data.get("record"), dict) else {}
    rid_list = rec.get("record_id_list") if isinstance(rec.get("record_id_list"), list) else []
    rid = str(data.get("record_id") or rec.get("record_id")
              or (rid_list[0] if rid_list else "")
              or (rec.get("record") or {}).get("record_id") or "")
    if not rid:
        rid = _find_record(brand, phone or contact)
        if not rid:
            log.warning("[apply] 飞书同步成功但拿不到 record_id（品牌=%s）", brand)
    return rid
