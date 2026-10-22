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
        CREATE INDEX IF NOT EXISTS idx_pts_douyin ON platform_talent_stats(douyin_id);
        CREATE INDEX IF NOT EXISTS idx_pts_stat ON platform_talent_stats(stat_date);
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
        ctx.close()

    rows = talent_rows(items, stat_date, start_time, end_time)
    result = {
        "stat_date": stat_date, "period": [start_time, end_time],
        "inst": inst, "raw_count": len(items), "talent_count": len(rows),
        "talent_rows": rows,
    }
    if dry_run:
        return result

    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        ensure_schema(conn)
        save_inst_stats(conn, inst, stat_date)
        saved = save_talent_stats(conn, rows)
        conn.commit()
    finally:
        conn.close()
    result["saved"] = saved
    return result


def match_local_talents(rows: list[dict]) -> dict:
    """跟本地 talents 对拍，**双口径**都试。

    ⚠️ 口径坑：`talents.douyin_id` 这个字段名有误导性 —— 它实际存的是
    **平台达人 id**（`Wgg292` / `hlb725` / `69097875157`），不是抖音号。
    所以拿平台的 `user_source_id`（真抖音号）去比，**恒为 0 命中**；
    真正能对上的是 `platform_talent_id`。
    """
    out = {"total": 0, "by_douyin": 0, "by_platform_id": 0, "by_name": 0}
    if not rows or not DB_PATH.exists():
        return out
    conn = sqlite3.connect(str(DB_PATH), timeout=15)
    try:
        dy = [r["douyin_id"] for r in rows if r.get("douyin_id")]
        pid = [r["platform_talent_id"] for r in rows if r.get("platform_talent_id")]
        if dy:
            q = ",".join("?" * len(dy))
            out["by_douyin"] = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT douyin_id FROM talents "
                f"WHERE douyin_id IN ({q}))", dy).fetchone()[0]
        if pid:
            q = ",".join("?" * len(pid))
            out["by_platform_id"] = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT douyin_id FROM talents "
                f"WHERE douyin_id IN ({q}))", pid).fetchone()[0]
        names = [r["name"] for r in rows if r.get("name")]
        if names:
            q = ",".join("?" * len(names))
            out["by_name"] = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT DISTINCT name FROM talents "
                f"WHERE name IN ({q}))", names).fetchone()[0]
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
    print(f"  按 platform_talent_id（平台达人id）命中 {m['by_platform_id']}")
    print(f"  按 user_source_id（真抖音号）  命中 {m['by_douyin']}"
          f"  ← 恒 0：talents.douyin_id 实际存的是平台达人id")
    print(f"  按名字精确匹配                 命中 {m['by_name']}")
    if not args.dry_run:
        print(f"已落库：platform_inst_stats 1 行 + platform_talent_stats {result.get('saved', 0)} 行")
    else:
        print("（dry-run，未落库）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
