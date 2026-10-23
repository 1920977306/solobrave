#!/usr/bin/env python3
"""团长端（抖音电商·精选联盟）机构/达人出单数据采集器。

**为什么是团长端**（老大 2026-10-23 纠正）：
  「达人数据用团长端看，商品才会用达人端看。」
  - 团长端 = buyin.jinritemai.com，登录身份「团长/机构」→ 达人出单、成交、经营概览
  - 达人端 = 抖店达人工作台（profile_daren）→ 只有商品数据（选品广场/商品详情）
  ⚠️ 同一 host 同一路径，**用哪个 profile 就是哪个身份**：拿达人端 profile 打
  /dashboard 会进「达人自己的罗盘」（compass_api/author/**），不是团长视角。

接口（页面上下文 fetch，实测可直连，**不需要 a_bogus**）：
  GET /ecom/captain/institution/statistics/overview
      → business_info(支付GMV/结算GMV/服务费/订单量) + order_info(成交达人/出单商品/出单商家)
  GET /ecom/captain/institution/statistics/promoter/list
      → promoter_infos[]（达人出单明细，user_source_id = 抖音号）

金额单位：平台返回**分**（与达人端 material_list 一致），落库统一换算成**元**。

用法：
  python3 daren_cockpit/tools/captain_stats.py --days 30          # 采集并入库
  python3 daren_cockpit/tools/captain_stats.py --days 30 --dry-run # 只打印不入库
  python3 daren_cockpit/tools/captain_stats.py --days 30 --no-clone # 直接用原 profile
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

PROD_DIR = Path(__file__).resolve().parents[2]
DB_PATH = PROD_DIR / "data" / "solobrave.db"
BROWSER_DIR = PROD_DIR / "data" / "buyin_browser"
PROFILE_DIR = BROWSER_DIR / "profile"          # 团长端登录态
DATA_PARK_URL = "https://buyin.jinritemai.com/dashboard/data-table/manage-overview"

OVERVIEW_API = "/ecom/captain/institution/statistics/overview"
PROMOTER_API = "/ecom/captain/institution/statistics/promoter/list"
PRODUCT_API = "/ecom/captain/institution/statistics/product/list"

PAGE_SIZE = 20
MAX_PAGES = 60          # 1200 个达人，够用；别无限翻

# 平台 field_key → 落库列（business_info / order_info）
OVERVIEW_FIELDS = {
    # business_info
    "pay_gmv": "pay_gmv",
    "settle_gmv": "settle_gmv",
    "estimated_income_service_fee": "estimated_income_service_fee",
    "real_income_service_fee": "real_income_service_fee",
    "estimated_expense_service_fee": "estimated_expense_service_fee",
    "real_expense_service_fee": "real_expense_service_fee",
    "order_pay_num": "order_pay_num",
    "avg_service_rate": "avg_service_rate",
    # order_info
    "order_daren_count": "order_daren_count",
    "order_second_colonel_count": "order_second_colonel_count",
    "order_product_count": "order_product_count",
    "order_shop_count": "order_shop_count",
    "order_first_colonel_count": "order_first_colonel_count",
    "avg_commission_rate": "avg_commission_rate",
}
# 这些是「分」，落库要除 100 变成元
CENTS_FIELDS = {
    "pay_gmv", "settle_gmv", "estimated_income_service_fee", "real_income_service_fee",
    "estimated_expense_service_fee", "real_expense_service_fee",
}
# 这些是计数，整数
INT_FIELDS = {
    "order_pay_num", "order_daren_count", "order_second_colonel_count",
    "order_product_count", "order_shop_count", "order_first_colonel_count",
}
# 这些是百分比，**必须保留小数**（9.29% 不能截成 9）
RATE_FIELDS = {"avg_service_rate", "avg_commission_rate"}


# ───────────────────────────── 纯函数（可测） ─────────────────────────────

def cents_to_yuan(value) -> float:
    """平台金额单位是分 → 元。空/非数返回 0.0。"""
    if value in (None, ""):
        return 0.0
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def to_int(value) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value) -> float:
    """百分比等需要保留小数的字段。"""
    if value in (None, ""):
        return 0.0
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def period_range(days: int, end: datetime | None = None) -> tuple[str, str]:
    """返回 (start_time, end_time)，平台格式 `YYYY/MM/DD HH:MM:SS`。"""
    end = end or datetime.now()
    start = end - timedelta(days=days)
    return start.strftime("%Y/%m/%d %H:%M:%S"), end.strftime("%Y/%m/%d %H:%M:%S")


def overview_row(data: dict) -> dict:
    """把 overview 响应摊平成一行（金额已换算成元）。"""
    row: dict = {}
    for section in ("business_info", "order_info"):
        block = (data or {}).get(section) or {}
        for item in block.get("data_details") or []:
            key = item.get("field_key")
            if key not in OVERVIEW_FIELDS:
                continue
            raw = item.get("value")
            col = OVERVIEW_FIELDS[key]
            if col in CENTS_FIELDS:
                row[col] = cents_to_yuan(raw)
            elif col in INT_FIELDS:
                row[col] = to_int(raw)
            elif col in RATE_FIELDS:
                row[col] = to_float(raw)
            else:
                row[col] = raw
    row["data_cutoff_time"] = (data or {}).get("data_cutoff_time") or ""
    return row


def talent_rows(items: list, stat_date: str, period_start: str, period_end: str) -> list[dict]:
    """promoter_infos[] → 落库行。user_source_id 是抖音号，作为对拍钥匙。

    ⚠️ 平台会返回**完全重复的行**（实测：同一条达人记录出现 2 次，字段一字不差），
    所以这里**显式按抖音号去重**（保留第一条），不能指望 DB 的 UNIQUE 兜底 ——
    静默合并会让人以为「抓全了」，实际看不出平台给了多少原始行。
    """
    out = []
    seen: set = set()
    for it in items or []:
        douyin_id = str(it.get("user_source_id") or "").strip()
        if not douyin_id:
            continue          # 没有抖音号没法跟 talents 对拍，丢掉（不造数据）
        if douyin_id in seen:
            continue          # 平台重复行
        seen.add(douyin_id)
        out.append({
            "douyin_id": douyin_id,
            "platform_talent_id": str(it.get("id") or ""),
            "name": it.get("name") or "",
            "avatar": it.get("avatar") or "",
            "order_product_count": to_int(it.get("order_product_count")),
            "order_pay_num": to_int(it.get("order_pay_num")),
            "pay_gmv": cents_to_yuan(it.get("pay_gmv")),
            "settle_gmv": cents_to_yuan(it.get("settle_gmv")),
            "data_visible_status": to_int(it.get("data_visible_status")),
            "stat_date": stat_date,
            "period_start": period_start,
            "period_end": period_end,
        })
    return out


# ───────────────────────────── 商品维度（出单商品明细） ─────────────────────────────

def product_rows(items: list, stat_date: str, period_start: str, period_end: str) -> list[dict]:
    """product/list 的 product_order_infos[] → 落库行。**`id` 是平台商品ID**（可精确对拍）。"""
    out = []
    seen: set = set()
    for it in items or []:
        pid = str(it.get("id") or "").strip()
        if not pid:
            continue
        if pid in seen:
            continue          # 平台重复行
        seen.add(pid)
        out.append({
            "platform_product_id": pid,
            "name": it.get("name") or "",
            "avatar": it.get("avatar") or "",
            "order_daren_count": to_int(it.get("order_daren_count")),
            "order_second_colonel_count": to_int(it.get("order_second_colonel_count")),
            "order_pay_num": to_int(it.get("order_pay_num")),
            "pay_gmv": cents_to_yuan(it.get("pay_gmv")),
            "settle_gmv": cents_to_yuan(it.get("settle_gmv")),
            "stat_date": stat_date,
            "period_start": period_start,
            "period_end": period_end,
        })
    return out


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\u3000]+", "", s)
    return re.sub(r"[^0-9a-z\u4e00-\u9fa5]", "", s)


def _grams(s: str, n: int = 2) -> set:
    s = _norm(s)
    if not s:
        return set()
    if len(s) < n:
        return {s}
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def name_coverage(ours: str, theirs: str, n: int = 2) -> float:
    """我方名（通常是简称/OCR 简称）在平台全名里的**覆盖率**。

    为什么不用「包含」：平台名是全名（`Coolchap嘭嘭爱心系列2026沙滩度假女士平底拖鞋人字拖CCF1001`），
    我方名是简称（`嘭嘭爱心系列人字拖`）—— 简称**不是**全名的连续子串，
    纯 `in` 判断恒 False ⇒ 会把「明明是同一个商品」判成「没这批人」。
    ⇒ 改用 n-gram 覆盖率（我方每个 2-gram 有多少出现在平台名里）。
    """
    a, b = _norm(ours), _norm(theirs)
    if not a or not b:
        return 0.0
    if a in b:
        return 1.0
    ga, gb = _grams(a, n), _grams(b, n)
    if not ga:
        return 0.0
    return len(ga & gb) / len(ga)


def match_products(platform_rows_: list, our_rows: list, threshold: float = 0.6) -> dict:
    """平台出单商品 ↔ 我们库商品 对拍。

    **主键优先**：我们库已挂 `platform_product_id` 的 ⇒ **精确命中**（不再靠猜名字）。
    只有没挂平台ID的行才退回 n-gram 覆盖率匹配（平台名是全名、我们库是 OCR 简称，
    纯 `in` 判断恒 False）。

    ★ `our_rows` 用 **dict**（`{"id","name","platform_product_id"}`）而不是元组 ——
      元组靠下标取字段，改一次列顺序就会静默错位（位置约定漂移）。

    返回:
      pairs      [(coverage, platform_row, our_row)]
      ours_hit   我们库中被对上的商品 id 集合
      missing    平台在出单、我们库里没有的（★ 该建档）
      ours_cold  我们库里有、但平台无出单记录的
    """
    by_pid = {}
    for o in our_rows:
        pid = (o.get("platform_product_id") or "").strip()
        if pid:
            by_pid[pid] = o

    pairs = []
    used = set()
    for pr in platform_rows_:
        pid = (pr.get("platform_product_id") or "").strip()
        if pid and pid in by_pid:
            pairs.append((1.0, pr, by_pid[pid]))
            used.add(by_pid[pid]["id"])

    for pr in platform_rows_:
        if any(p[1] is pr for p in pairs):
            continue
        cand = [o for o in our_rows if o["id"] not in used]
        best = max(((name_coverage(o["name"], pr["name"]), o) for o in cand),
                   key=lambda t: t[0], default=(0.0, None))
        if best[1] is not None and best[0] >= threshold:
            pairs.append((best[0], pr, best[1]))
            used.add(best[1]["id"])

    ours_hit = {p[2]["id"] for p in pairs}
    matched_platform = {p[1]["platform_product_id"] for p in pairs}
    missing = [pr for pr in platform_rows_ if pr["platform_product_id"] not in matched_platform]
    cold = [o for o in our_rows if o["id"] not in ours_hit]
    return {"pairs": pairs, "ours_hit": ours_hit, "missing": missing, "ours_cold": cold}


# ───────────────────────────── 建表 / 落库 ─────────────────────────────

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS platform_inst_stats (
            id TEXT PRIMARY KEY,
            stat_date TEXT NOT NULL UNIQUE,
            pay_gmv REAL DEFAULT 0,
            settle_gmv REAL DEFAULT 0,
            estimated_income_service_fee REAL DEFAULT 0,
            real_income_service_fee REAL DEFAULT 0,
            estimated_expense_service_fee REAL DEFAULT 0,
            real_expense_service_fee REAL DEFAULT 0,
            order_pay_num INTEGER DEFAULT 0,
            avg_service_rate REAL DEFAULT 0,
            order_daren_count INTEGER DEFAULT 0,
            order_second_colonel_count INTEGER DEFAULT 0,
            order_product_count INTEGER DEFAULT 0,
            order_shop_count INTEGER DEFAULT 0,
            order_first_colonel_count INTEGER DEFAULT 0,
            avg_commission_rate REAL DEFAULT 0,
            data_cutoff_time TEXT DEFAULT '',
            captured_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS platform_talent_stats (
            id TEXT PRIMARY KEY,
            stat_date TEXT NOT NULL,
            period_start TEXT DEFAULT '',
            period_end TEXT DEFAULT '',
            douyin_id TEXT NOT NULL,
            platform_talent_id TEXT DEFAULT '',
            name TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            order_product_count INTEGER DEFAULT 0,
            order_pay_num INTEGER DEFAULT 0,
            pay_gmv REAL DEFAULT 0,
            settle_gmv REAL DEFAULT 0,
            data_visible_status INTEGER DEFAULT 0,
            captured_at INTEGER,
            UNIQUE(stat_date, douyin_id)
        );
        CREATE TABLE IF NOT EXISTS platform_product_stats (
            id TEXT PRIMARY KEY,
            stat_date TEXT NOT NULL,
            period_start TEXT DEFAULT '',
            period_end TEXT DEFAULT '',
            platform_product_id TEXT NOT NULL,
            name TEXT DEFAULT '',
            avatar TEXT DEFAULT '',
            order_daren_count INTEGER DEFAULT 0,
            order_second_colonel_count INTEGER DEFAULT 0,
            order_pay_num INTEGER DEFAULT 0,
            pay_gmv REAL DEFAULT 0,
            settle_gmv REAL DEFAULT 0,
            captured_at INTEGER,
            UNIQUE(stat_date, platform_product_id)
        );
        CREATE INDEX IF NOT EXISTS idx_pts_douyin ON platform_talent_stats(douyin_id);
        CREATE INDEX IF NOT EXISTS idx_pts_stat ON platform_talent_stats(stat_date);
        CREATE INDEX IF NOT EXISTS idx_pps_pid ON platform_product_stats(platform_product_id);
        """
    )


INST_COLUMNS = [
    "pay_gmv", "settle_gmv",
    "estimated_income_service_fee", "real_income_service_fee",
    "estimated_expense_service_fee", "real_expense_service_fee",
    "order_pay_num", "avg_service_rate",
    "order_daren_count", "order_second_colonel_count",
    "order_product_count", "order_shop_count", "order_first_colonel_count",
    "avg_commission_rate", "data_cutoff_time",
]


def save_inst_stats(conn: sqlite3.Connection, row: dict, stat_date: str) -> None:
    cols = [c for c in INST_COLUMNS]
    values = [row.get(c, "") for c in cols]
    updates = ",".join(f"{c}=excluded.{c}" for c in cols)
    conn.execute(
        f"INSERT INTO platform_inst_stats (id, stat_date, {','.join(cols)}) "
        f"VALUES (?,?,{','.join('?' * len(cols))}) "
        f"ON CONFLICT(stat_date) DO UPDATE SET {updates}",
        [f"inst_{stat_date}", stat_date] + values,
    )


def save_talent_stats(conn: sqlite3.Connection, rows: list[dict]) -> int:
    n = 0
    now = int(datetime.now().timestamp())
    for r in rows:
        conn.execute(
            """INSERT INTO platform_talent_stats
               (id, stat_date, period_start, period_end, douyin_id, platform_talent_id,
                name, avatar, order_product_count, order_pay_num, pay_gmv, settle_gmv,
                data_visible_status, captured_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stat_date, douyin_id) DO UPDATE SET
                 period_start=excluded.period_start, period_end=excluded.period_end,
                 platform_talent_id=excluded.platform_talent_id, name=excluded.name,
                 avatar=excluded.avatar, order_product_count=excluded.order_product_count,
                 order_pay_num=excluded.order_pay_num, pay_gmv=excluded.pay_gmv,
                 settle_gmv=excluded.settle_gmv,
                 data_visible_status=excluded.data_visible_status,
                 captured_at=excluded.captured_at""",
            (f"pts_{r['stat_date']}_{r['douyin_id']}", r["stat_date"], r["period_start"],
             r["period_end"], r["douyin_id"], r["platform_talent_id"], r["name"],
             r["avatar"], r["order_product_count"], r["order_pay_num"], r["pay_gmv"],
             r["settle_gmv"], r["data_visible_status"], now),
        )
        n += 1
    return n


def save_product_stats(conn: sqlite3.Connection, rows: list[dict]) -> int:
    now = int(datetime.now().timestamp())
    for r in rows:
        conn.execute(
            """INSERT INTO platform_product_stats
               (id, stat_date, period_start, period_end, platform_product_id, name, avatar,
                order_daren_count, order_second_colonel_count, order_pay_num, pay_gmv,
                settle_gmv, captured_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(stat_date, platform_product_id) DO UPDATE SET
                 period_start=excluded.period_start, period_end=excluded.period_end,
                 name=excluded.name, avatar=excluded.avatar,
                 order_daren_count=excluded.order_daren_count,
                 order_second_colonel_count=excluded.order_second_colonel_count,
                 order_pay_num=excluded.order_pay_num, pay_gmv=excluded.pay_gmv,
                 settle_gmv=excluded.settle_gmv, captured_at=excluded.captured_at""",
            (f"pps_{r['stat_date']}_{r['platform_product_id']}", r["stat_date"],
             r["period_start"], r["period_end"], r["platform_product_id"], r["name"],
             r["avatar"], r["order_daren_count"], r["order_second_colonel_count"],
             r["order_pay_num"], r["pay_gmv"], r["settle_gmv"], now),
        )
    return len(rows)


# ───────────────────────────── 采集 ─────────────────────────────

def clone_profile(src: Path) -> Path:
    """APFS 克隆 profile（COW，秒级、不占额外空间），避免和常驻 worker 抢同一个目录。"""
    dst = Path(tempfile.mkdtemp(prefix="captain_profile_")) / "profile"
    subprocess.run(["cp", "-Rc", str(src), str(dst)], check=True)
    return dst


FETCH_JS = """async (paths) => {
  const out = [];
  for (const p of paths) {
    try {
      const r = await fetch(p, {credentials: 'include'});
      const t = await r.text();
      let j = null;
      try { j = JSON.parse(t); } catch (e) {}
      out.push({path: p, status: r.status, json: j, raw: j ? null : t.slice(0, 400)});
    } catch (e) {
      out.push({path: p, status: -1, json: null, raw: String(e).slice(0, 200)});
    }
  }
  return out;
}"""


def collect(days: int, profile: Path, dry_run: bool) -> dict:
    from playwright.sync_api import sync_playwright

    start_time, end_time = period_range(days)
    stat_date = end_time.split(" ")[0].replace("/", "-")
    q = f"start_time={start_time.replace(' ', '%20')}&end_time={end_time.replace(' ', '%20')}&date_type=1"

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=True, viewport={"width": 1600, "height": 1200})
        pg = ctx.pages[0]
        pg.goto(DATA_PARK_URL, wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(12000)

        res = pg.evaluate(FETCH_JS, [f"{OVERVIEW_API}?{q}"])
        ov = res[0]
        if not ov["json"] or ov["json"].get("code") not in (0, None):
            raise RuntimeError(f"overview 拉取失败 status={ov['status']} body={ov['raw']}")
        inst = overview_row(ov["json"].get("data") or {})

        # 达人出单榜：翻页
        items: list = []
        for page in range(1, MAX_PAGES + 1):
            pq = (f"{PROMOTER_API}?{q}&sort_type=0&sort_field=order_product_count"
                  f"&page={page}&page_size={PAGE_SIZE}&promoter_type=0"
                  f"&author_query=&colonel_query=&date_type=1")
            r = pg.evaluate(FETCH_JS, [pq])[0]
            if not r["json"] or r["json"].get("code") not in (0, None):
                print(f"  第 {page} 页拉取失败：{str(r['raw'])[:120]}")
                break
            batch = ((r["json"].get("data") or {}).get("promoter_infos")) or []
            if not batch:
                break
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            pg.wait_for_timeout(400)

        # 商品维度出单明细（product_order_infos）
        pitems = []
        for page in range(1, MAX_PAGES + 1):
            pq = (f"{PRODUCT_API}?{q}&sort_type=0&sort_field=order_daren_count"
                  f"&page={page}&page_size={PAGE_SIZE}&product_id=&date_type=1")
            r = pg.evaluate(FETCH_JS, [pq])[0]
            if not r["json"] or r["json"].get("code") not in (0, None):
                print(f"  商品第 {page} 页拉取失败：{str(r['raw'])[:120]}")
                break
            batch = ((r["json"].get("data") or {}).get("product_order_infos")) or []
            if not batch:
                break
            pitems.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            pg.wait_for_timeout(400)
        ctx.close()

    rows = talent_rows(items, stat_date, start_time, end_time)
    prows = product_rows(pitems, stat_date, start_time, end_time)
    result = {
        "stat_date": stat_date, "period": [start_time, end_time],
        "inst": inst, "raw_count": len(items), "talent_count": len(rows),
        "talent_rows": rows,
        "product_raw_count": len(pitems), "product_count": len(prows),
        "product_rows": prows,
    }
    if dry_run:
        return result

    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        ensure_schema(conn)
        save_inst_stats(conn, inst, stat_date)
        saved = save_talent_stats(conn, rows)
        saved_p = save_product_stats(conn, result["product_rows"])
        conn.commit()
    finally:
        conn.close()
    result["saved"] = saved
    result["saved_product"] = saved_p
    return result


def match_local_talents(rows: list[dict]) -> dict:
    """跟本地 talents 对拍，**四口径**都试。

    ⚠️ 口径坑（2026-10-23 更正）：`talents.douyin_id` 这一列**混装了三类值** ——
    ① 平台达人 id（`Wgg292`/`hlb725`/`372168571`/`69097875157`）
    ② 真抖音号（`31693707031`/`372443191`）
    ③ 脏值（`@limantou200w`/`原来是安娜`/`__speedtest__`）
    **不能靠格式判别** —— 平台达人 id 也可能是纯数字。

    ⇒ 所以对拍要按**四个键**分别试，命中哪个算哪个：
      - `by_platform_id` : `talents.platform_talent_id` == 平台 `platform_talent_id`（**最准**）
      - `by_douyin`      : `talents.douyin_uid` == 平台 `user_source_id`（真抖音号）
      - `by_legacy_dy`   : `talents.douyin_id` == 平台 `platform_talent_id`（历史口径）
      - `by_name`        : 昵称精确匹配
    """
    out = {"total": 0, "by_douyin": 0, "by_platform_id": 0,
           "by_legacy_dy": 0, "by_name": 0}
    if not rows or not DB_PATH.exists():
        return out
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        dy = [r["douyin_id"] for r in rows if r.get("douyin_id")]
        pid = [r["platform_talent_id"] for r in rows if r.get("platform_talent_id")]

        def count(col, vals):
            if not vals:
                return 0
            q = ",".join("?" * len(vals))
            return conn.execute(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT {col} FROM talents "
                f"WHERE {col} IN ({q}))", vals).fetchone()[0]

        out["by_platform_id"] = count("platform_talent_id", pid)
        out["by_legacy_dy"] = count("douyin_id", pid)
        names = [r["name"] for r in rows if r.get("name")]
        out["by_name"] = count("name", names)
        # 真抖音号口径：用平台 user_source_id 对我们的 douyin_uid
        real = [r["douyin_id"] for r in rows if r.get("douyin_id")]
        out["by_douyin"] = count("douyin_uid", real)
        out["total"] = len(rows)
    finally:
        conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="团长端机构/达人出单数据采集")
    ap.add_argument("--days", type=int, default=30, help="统计区间天数（默认 30）")
    ap.add_argument("--profile", default=str(PROFILE_DIR), help="团长端 profile 路径")
    ap.add_argument("--no-clone", action="store_true",
                    help="直接用原 profile（会与常驻 worker 抢目录，默认用克隆）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不落库")
    args = ap.parse_args()

    src = Path(args.profile)
    if not src.exists():
        print(f"!! 团长端 profile 不存在：{src}")
        return 2

    profile = src if args.no_clone else clone_profile(src)
    try:
        result = collect(args.days, profile, args.dry_run)
    finally:
        if profile != src and profile.parent.exists():
            shutil.rmtree(profile.parent, ignore_errors=True)

    inst = result["inst"]
    print(f"\n=== 团长端出单数据（{result['period'][0]} ~ {result['period'][1]}）===")
    print(f"统计日：{result['stat_date']}   数据截止：{inst.get('data_cutoff_time') or '-'}")
    print(f"  支付GMV      {inst.get('pay_gmv', 0):>18,.2f} 元")
    print(f"  结算GMV      {inst.get('settle_gmv', 0):>18,.2f} 元")
    print(f"  支付订单量   {inst.get('order_pay_num', 0):>18,}")
    print(f"  成交达人     {inst.get('order_daren_count', 0):>18,}")
    print(f"  出单商品     {inst.get('order_product_count', 0):>18,}")
    print(f"  出单商家     {inst.get('order_shop_count', 0):>18,}")
    print(f"  平均佣金率   {inst.get('avg_commission_rate', 0):>17.2f}%")
    print(f"\n达人出单明细：平台原始 {result['raw_count']} 条 → 去重 {result['talent_count']} 个达人"
          f"（平台会返回完全重复的行，实测 2 条）")
    for r in result["talent_rows"][:5]:
        print(f"  {r['douyin_id']:<16} {r['name'][:14]:<16} 商品{r['order_product_count']:>3} "
              f"单{r['order_pay_num']:>6} 支付GMV {r['pay_gmv']:>14,.2f}")

    m = match_local_talents(result["talent_rows"])
    print(f"\n与本地 talents 对拍（{m['total']} 个平台达人）：")
    print(f"  按 platform_talent_id 命中 {m['by_platform_id']}   ← 最准（新列）")
    print(f"  按历史 douyin_id      命中 {m['by_legacy_dy']}")
    print(f"  按真抖音号 douyin_uid 命中 {m['by_douyin']}")
    print(f"  按名字精确匹配         命中 {m['by_name']}")

    # ── 商品维度 + 与我们推的商品对拍 ──
    prows = result["product_rows"]
    print(f"\n=== 商品维度出单明细 ===")
    print(f"平台原始 {result['product_raw_count']} 条 → 去重 {result['product_count']} 个商品")
    for r in sorted(prows, key=lambda x: -x["pay_gmv"])[:5]:
        print(f"  {r['platform_product_id']:<20} {r['name'][:34]:<36} 达人{r['order_daren_count']:>3} "
              f"单{r['order_pay_num']:>6} GMV {r['pay_gmv']:>14,.2f}")

    our_rows = []
    if DB_PATH.exists():
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            our_rows = [dict(r) for r in conn.execute(
                "SELECT id,name,brand,platform_product_id FROM products "
                "WHERE status!='deleted'")]
        finally:
            conn.close()
    if our_rows and prows:
        mt = match_products(prows, our_rows)
        print(f"\n=== 与我们推的商品对拍（我们 {len(our_rows)} 个 / 平台出单 {len(prows)} 个）===")
        print(f"  对上 {len(mt['pairs'])} 个：")
        for cov, pr, o in sorted(mt["pairs"], key=lambda x: -x[0]):
            print(f"    {cov:.2f} [{o['name'][:16]:<18}] ← 平台[{pr['name'][:36]}] "
                  f"GMV {pr['pay_gmv']:>12,.2f}")
        print(f"  ★ 平台在出单、我们库里没有（该建档）：{len(mt['missing'])} 个")
        for pr in sorted(mt["missing"], key=lambda x: -x["pay_gmv"])[:10]:
            print(f"    {pr['platform_product_id']:<20} {pr['name'][:40]:<42} "
                  f"达人{pr['order_daren_count']:>3} 单{pr['order_pay_num']:>6} "
                  f"GMV {pr['pay_gmv']:>12,.2f}")
        print(f"  我们库里有、平台无出单记录：{len(mt['ours_cold'])} 个")

    if not args.dry_run:
        print(f"\n已落库：platform_inst_stats 1 行 + platform_talent_stats "
              f"{result.get('saved', 0)} 行 + platform_product_stats "
              f"{result.get('saved_product', 0)} 行")
    else:
        print("\n（dry-run，未落库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
