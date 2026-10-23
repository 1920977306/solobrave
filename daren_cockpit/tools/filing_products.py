#!/usr/bin/env python3
"""商品建档：把团长端「在出单」的商品补进我们的 products 表。

为什么必须建档（2026-10-23 实证）
--------------------------------
团长端 46 个有出单的商品里，我们库**只对得上 2 个**（按名字 n-gram 覆盖率），
**44 个根本没建档** —— 而这 44 个占机构出单 GMV 的 **99.97%**。
反过来：我们库 91 个商品里 **90 个零出单**。
⇒ 我们在推荐的东西，和真正在赚钱的东西，几乎不重叠。

数据来源（两端分工，各取所长）
------------------------------
- **团长端** `product/list`（直连 fetch，**不需要签名**）：
  `platform_product_id` / `name` / `avatar`(主图) / `order_daren_count`(我方机构出单达人数)
  / `order_pay_num` / `pay_gmv` / `settle_gmv`
- **达人端** `material_list`（**必须带页面签名**，走 buyin_worker 常驻浏览器控制口）：
  到手价 / 佣金率 / 佣金金额 / 类目三级 / 月销 / 店铺 / 店铺体验分 / 好评率
  / **平台带货达人数** / 近 30 天销量曲线 / 平台标签

落库原则
--------
1. **平台侧标识单独成列** `platform_product_id` —— 不复用语义不对的历史字段。
2. **已有商品只回填缺失列，不覆盖**已有 price/name（铁律：merge 保留老值）。
3. 价格/佣金**只取接口真值**，取不到就跳过该条并如实报告 —— **不造数**。
4. 「三个达人数」口径不同，**必须分列**，不许混：
   - `influencer_count`  = 达人端 `cooper_author_num` = **平台全商品带货达人数**
   - `talent_count`      = **我方合作达人数**（由 `product_talent_match` 自动维护 ⇒ 新商品填 0）
   - `platform_order_daren_count` = 团长端 `order_daren_count` = **我方机构出单达人数**

用法
----
    python3 daren_cockpit/tools/filing_products.py --dry-run     # 只看，不写库（默认）
    python3 daren_cockpit/tools/filing_products.py --apply        # 真写（自动备份 products 表）

回滚
----
`--apply` 前自动把 products 表导出到 `data/products-backup-<ts>.csv`；
真要回滚：`python3 -c "import sqlite3; ..."` 按 csv 复原，或从备份 db 整表还原。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daren_cockpit.tools.captain_stats import name_coverage  # noqa: E402

DB = ROOT / "data" / "solobrave.db"
CACHE = ROOT / "data" / "filing-enrich-cache.json"   # 达人端补数据缓存（data/ 已 gitignore）
WORKER = "http://127.0.0.1:8095"

# 新增列：平台侧标识 + 我方出单快照（全部 additive，不动任何现有列的语义）
NEW_COLUMNS = [
    ("platform_product_id", "TEXT DEFAULT ''"),
    ("platform_pay_gmv", "REAL DEFAULT 0"),
    ("platform_settle_gmv", "REAL DEFAULT 0"),
    ("platform_order_pay_num", "INTEGER DEFAULT 0"),
    ("platform_order_daren_count", "INTEGER DEFAULT 0"),
    ("platform_stat_date", "TEXT DEFAULT ''"),
]

MATCH_THRESHOLD = 0.6


# ────────────────────────────── schema ──────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> list:
    """加列（幂等）。返回实际新增的列名。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    added = []
    for name, decl in NEW_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE products ADD COLUMN {name} {decl}")
            added.append(name)
    # 部分唯一索引：空串不参与唯一约束（存量 91 行没有平台ID）
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_platform_pid "
        "ON products(platform_product_id) WHERE platform_product_id != ''")
    conn.commit()
    return added


# ────────────────────────────── 读取 ──────────────────────────────
def load_platform_rows(conn: sqlite3.Connection) -> list:
    """团长端出单商品（取每个平台商品ID的最新统计日）。"""
    rows = conn.execute("""
        SELECT s.platform_product_id, s.name, s.avatar,
               s.order_daren_count, s.order_pay_num, s.pay_gmv, s.settle_gmv,
               s.stat_date
        FROM platform_product_stats s
        JOIN (SELECT platform_product_id, MAX(stat_date) d
              FROM platform_product_stats WHERE order_pay_num > 0
              GROUP BY platform_product_id) m
          ON m.platform_product_id = s.platform_product_id AND m.d = s.stat_date
        WHERE s.order_pay_num > 0
        ORDER BY s.pay_gmv DESC
    """).fetchall()
    return [dict(r) for r in rows]


def load_our_products(conn: sqlite3.Connection) -> list:
    rows = conn.execute(
        "SELECT id, name, price, brand, category, commission_rates, platform_product_id "
        "FROM products").fetchall()
    return [dict(r) for r in rows]


def name_matched_candidates(platform_rows: list, our_rows: list) -> dict:
    """n-gram 覆盖率找出「可能对应」的我方记录 —— **只是候选，不自动挂**。

    ⚠️ 为什么不能自动挂（2026-10-23 实测踩坑）：
    平台名是全名、我们库里是 OCR 简称，n-gram 覆盖率对「**同系列不同款**」区分度不够 ——
    实测 `Coolchap2026秋季新品裸穿微光系列玛丽珍单鞋7800-2327` 与
    `Coolchap2026秋季新品繁花编织系列玛丽珍单鞋7800-2315` 覆盖率 **0.79**，
    共享前缀「Coolchap2026秋季新品…系列玛丽珍单鞋7800-2」贡献了大部分 gram，
    **但它们是两个不同型号的商品** ⇒ 自动挂载会静默把平台商品接到错的记录上，
    而且一旦挂上就**锁死**后续精确匹配。

    ⇒ 所以这里只产出候选，最终挂载由 `strong_evidence()` 裁决。

    ⚠️ 每个平台商品**独立**找最佳候选，**不做贪心排除** ——
    贪心会让「A 抢走了 B 的候选」导致 B 被重复建档（实测踩过：
    裸穿微光抢走了繁花编织的行 ⇒ 繁花编织被多建了一条重复记录）。
    冲突交给 `resolve_conflicts()` 统一消解。
    """
    cand = []
    for pr in platform_rows:
        best = max(((name_coverage(o["name"], pr["name"]), o) for o in our_rows),
                   key=lambda t: t[0], default=(0.0, None))
        if best[1] is not None and best[0] >= MATCH_THRESHOLD:
            cand.append({"platform": pr, "our": best[1], "coverage": best[0]})
    return {"candidates": cand}


def resolve_conflicts(matches: list) -> tuple[list, list]:
    """同一条我方记录只能挂**一个**平台商品。

    ⇒ 冲突时：覆盖率最高者胜；覆盖率相同则 GMV 高者胜；其余改为新建。
    返回 (胜出者, 落败者)。
    """
    by_our: dict = {}
    for c in matches:
        by_our.setdefault(c["our"]["id"], []).append(c)
    winners, losers = [], []
    for group in by_our.values():
        group.sort(key=lambda c: (-c["coverage"], -c["platform"].get("pay_gmv", 0)))
        winners.append(group[0])
        losers.extend(group[1:])
    return winners, losers


def strong_evidence(pr: dict, our: dict, daren: dict | None) -> tuple[bool, str]:
    """n-gram 候选 → 真·同一商品的**强证据**校验。

    判据：**到手价一致** 且 **佣金率一致**（两个字段都必须有真值才算证据）。

    实测两个案例：
    - `嘭嘭爱心系列人字拖`：我方 ¥129 / 自然流佣金 20%  ==  平台 ¥129 / 20%  ⇒ **是同一商品**
    - `裸穿微光 7800-2327` vs `繁花编织 7800-2315`：价格/佣金对不上  ⇒ **不是同一商品**

    ⇒ 宁可漏挂（多建一条记录），不可错挂（把平台数据接到错的商品上）。
    """
    if not daren:
        return False, "达人端无真值，无法判定"
    p_price = _yuan(daren.get("price"))
    o_price = float(our.get("price") or 0)
    if p_price <= 0 or o_price <= 0:
        return False, "价格缺失，无法判定"
    if abs(p_price - o_price) > 0.01:
        return False, f"价格不一致（我方 ¥{o_price:g} / 平台 ¥{p_price:g}）"
    p_rate = _pct(daren.get("commission_rate"))
    o_rate = _our_commission_rate(our.get("commission_rates"))
    if p_rate <= 0 or o_rate <= 0:
        return False, "佣金率缺失，无法判定"
    if abs(p_rate - o_rate) > 0.01:
        return False, f"佣金率不一致（我方 {o_rate:g}% / 平台 {p_rate:g}%）"
    return True, f"价格与佣金率均一致（¥{p_price:g} / {p_rate:g}%）"


def _our_commission_rate(raw) -> float:
    """我方 commission_rates（JSON dict）→ 最高佣金率（前端取 max 的口径）。"""
    if not raw:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return 0.0
    if not isinstance(d, dict):
        return 0.0
    vals = [float(v) for v in d.values() if isinstance(v, (int, float))]
    return max(vals) if vals else 0.0


# ────────────────────────── 达人端补数据 ──────────────────────────
def _http_json(url: str, payload: dict | None = None, timeout: int = 20) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _cache_load() -> dict:
    try:
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def _cache_save(cache: dict) -> None:
    try:
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def enrich_from_daren(pid: str, timeout_s: int = 120) -> tuple[dict | None, str]:
    """平台商品ID → 达人端商品数据（走 buyin_worker 常驻浏览器，签名天然具备）。

    返回 (数据, 说明)。失败**不重试到底**：常驻浏览器若会话失效，重试也没用。
    """
    try:
        resp = _http_json(f"{WORKER}/collect-product-id", {"product_id": pid, "size": 5})
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, f"worker 不可达：{e}"
    cid = resp.get("collect_id")
    if not cid:
        return None, f"worker 拒绝：{resp}"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(3)
        try:
            job = _http_json(f"{WORKER}/jobs/{cid}")
        except Exception as e:  # noqa: BLE001
            return None, f"查 job 失败：{e}"
        st = job.get("status")
        if st == "done":
            raw = job.get("result")
            if not raw:
                return None, "worker 返回空结果"
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except json.JSONDecodeError as e:
                return None, f"结果不是 JSON：{e}"
            if not data.get("name"):
                return None, "结果里没有商品名（可能搜不到）"
            return data, ""
        if st in ("failed", "error"):
            return None, f"worker 采集失败：{job.get('stage') or job.get('error')}"
    return None, "超时"


# ────────────────────────── 解析与建行 ──────────────────────────
def _yuan(text) -> float:
    """'¥549' / '¥27.45' → 549.0 / 27.45（达人端金额单位是**元**，字符串已带 ¥）。"""
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"[\d.]+", str(text).replace(",", ""))
    return float(m.group()) if m else 0.0


def _int(text) -> int:
    try:
        return int(float(str(text).strip()))
    except (TypeError, ValueError):
        return 0


def _pct(text) -> float:
    """'5%' / '92.5%' → 5.0 / 92.5（去掉 %，保留小数）。"""
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"[\d.]+", str(text))
    return float(m.group()) if m else 0.0


def build_row(pr: dict, daren: dict | None) -> dict:
    """一条 products 行。**只填拿得到的真值**，拿不到的留 0/空，不造。"""
    d = daren or {}
    name = (d.get("name") or pr["name"] or "").strip()
    brand = ""
    # ★ 平台商品名里品牌**后面不带空格**（`Coolchap2026秋冬新品…`），
    #   所以只取「开头一段连续拉丁字母」；纯中文品牌（温途/环球/鮀品）取不准
    #   ⇒ 宁可留空，**不猜**（只丢不造）。
    m = re.match(r"^([A-Za-z]+)", pr["name"] or "")
    if m:
        brand = m.group(1).upper()
    cat_first = d.get("category_first") or ""
    cat_third = d.get("category_third") or ""
    category = "/".join([c for c in (cat_first, cat_third) if c])
    rate = _pct(d.get("commission_rate"))
    if rate and rate == int(rate):
        rate = int(rate)          # 整数佣金率存成 5 而不是 5.0（跟存量口径一致）
    price = _yuan(d.get("price"))
    tags = d.get("tags") or []
    shop = d.get("shop") or ""
    subtitle = " · ".join([x for x in (brand, shop, category) if x])[:120]

    return {
        "name": name,
        "subtitle": subtitle,
        "main_image": d.get("main_img") or pr.get("avatar") or "",
        "price": price,
        "price_range": (f"¥{price:g}" if price else ""),
        "brand": brand,
        "category": category,
        "monthly_sales": _int(d.get("sales")),
        "commission_rates": json.dumps({"default": rate} if rate else {}, ensure_ascii=False),
        "commission_amount": _yuan(d.get("commission")),
        # 平台带货达人数（达人端）≠ 我方机构出单达人数（团长端）→ 分列
        "influencer_count": _int(d.get("cooper_author_num")),
        "talent_count": 0,   # 由 product_talent_match 自动维护，新商品填 0
        "tags": json.dumps(tags, ensure_ascii=False),
        "selling_points": "、".join(tags),
        "status": "active",
        "platform_product_id": pr["platform_product_id"],
        "platform_pay_gmv": pr["pay_gmv"],
        "platform_settle_gmv": pr["settle_gmv"],
        "platform_order_pay_num": pr["order_pay_num"],
        "platform_order_daren_count": pr["order_daren_count"],
        "platform_stat_date": pr["stat_date"],
        "_shop": shop,
        "_shop_score": d.get("shop_score") or "",
        "_good_ratio": d.get("good_ratio") or "",
        "_detail_url": d.get("resolved_url") or "",
        "_sale_axis": d.get("sale_axis") or [],
    }


def resolve_owner(conn: sqlite3.Connection) -> str:
    """这批「机构级」商品该归属谁？

    ⚠️ 归属不是装饰：`/api/products` 对**非管理员**会按 owner 过滤
    （`visible_ids = {uid} | 该用户的 AI 员工 id`）⇒ `created_by` 为空的行
    **对租户端完全不可见**，建档就白做了。

    ⇒ 取存量商品里**最常见的非空 created_by** 作为「机构主账号」（不写死 ID）。
    """
    row = conn.execute(
        "SELECT created_by, COUNT(*) c FROM products "
        "WHERE created_by IS NOT NULL AND created_by!='' "
        "GROUP BY created_by ORDER BY c DESC LIMIT 1").fetchone()
    return row[0] if row else ""


def new_product_id(brand: str, pid: str, taken: set) -> str:
    """内部 id：`prod_{brand小写}_{平台ID后6位}`，冲突则加序号。"""
    base = f"prod_{(brand or 'p').lower()}_{pid[-6:]}"
    cand, i = base, 1
    while cand in taken:
        i += 1
        cand = f"{base}_{i}"
    taken.add(cand)
    return cand


# ────────────────────────────── 主流程 ──────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="商品建档（团长端出单商品 → products 表）")
    ap.add_argument("--dry-run", action="store_true", help="只看，不写库（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真写库（会先自动备份）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个待建档商品（调试用）")
    ap.add_argument("--skip-enrich", action="store_true",
                    help="不调达人端（只用团长端数据建档 ⇒ 价格/佣金为空）")
    ap.add_argument("--refresh", action="store_true",
                    help="忽略达人端补数据缓存，强制重新抓")
    ap.add_argument("--owner", default="",
                    help="新建商品的 created_by（默认自动取存量里最常见的归属）")
    args = ap.parse_args()
    apply_mode = args.apply and not args.dry_run

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    added_cols = ensure_schema(conn)
    print(f"[schema] 新增列：{added_cols or '（已存在，无需新增）'}")

    owner = args.owner or resolve_owner(conn)
    plat = load_platform_rows(conn)
    ours = load_our_products(conn)
    print(f"[归属] 新建商品归属 owner = {owner or '(空 —— 租户端将不可见!)'}")
    by_pid = {r["platform_product_id"]: r for r in ours if r.get("platform_product_id")}
    linked = [pr for pr in plat if pr["platform_product_id"] in by_pid]
    rest = [pr for pr in plat if pr["platform_product_id"] not in by_pid]
    cand = name_matched_candidates(rest, ours)

    print(f"\n[分类] 团长端出单商品 {len(plat)} 个")
    print(f"  ├─ 已挂平台ID（跳过）        : {len(linked)}")
    print(f"  ├─ n-gram 候选（待强证据裁决） : {len(cand['candidates'])}")
    print(f"  └─ 库里没有（待建档）         : {len(rest) - len(cand['candidates'])}")
    tot = sum(p["pay_gmv"] for p in plat)
    print(f"  未挂平台ID 的部分合计 GMV ¥{sum(p['pay_gmv'] for p in rest):,.2f}")

    # ── 达人端补数据：候选 + 待建档都要（候选要靠真值裁决）──
    cand_pids = {c["platform"]["platform_product_id"] for c in cand["candidates"]}
    need = [c["platform"] for c in cand["candidates"]] + [
        pr for pr in rest if pr["platform_product_id"] not in cand_pids]
    todo = need[: args.limit] if args.limit else need
    rows_to_write: list = []
    enrich_cache: dict = {}
    failures: list = []

    if todo:
        cache = _cache_load()
        print(f"\n[达人端补数据] 共 {len(todo)} 个商品…")
        for i, pr in enumerate(todo, 1):
            pid = pr["platform_product_id"]
            if args.skip_enrich:
                daren, why = None, "按 --skip-enrich 跳过"
            elif not args.refresh and pid in cache:
                daren, why = cache[pid], "（缓存）"
            else:
                daren, why = enrich_from_daren(pid)
                if daren:
                    cache[pid] = daren
                    _cache_save(cache)
            if daren:
                enrich_cache[pid] = daren
                got = daren.get("product_id")
                if got and str(got) != pid:
                    failures.append((pid, f"返回的 product_id 不一致：{got}"))
                print(f"  {i:>2}/{len(todo)} {pid} ✔ ¥{_yuan(daren.get('price')):g} "
                      f"佣金{_pct(daren.get('commission_rate')):g}% "
                      f"月销{_int(daren.get('sales'))} | {daren.get('name', '')[:36]}")
            else:
                failures.append((pid, why))
                print(f"  {i:>2}/{len(todo)} {pid} ✘ {why}")

    # ── 候选裁决：强证据（价格 + 佣金率）才认，否则当新商品建档 ──
    strong_matches = []
    weak_matches = []
    for c in cand["candidates"]:
        pid = c["platform"]["platform_product_id"]
        daren = enrich_cache.get(pid)
        ok, why = strong_evidence(c["platform"], c["our"], daren)
        if ok:
            strong_matches.append(c)
        else:
            weak_matches.append((c, why))

    # ── 冲突消解：同一条我方记录只能挂一个平台商品 ──
    accepted, conflicted = resolve_conflicts(strong_matches)
    for c in conflicted:
        weak_matches.append((c, f"与另一平台商品争同一条我方记录 "
                                f"（cov={c['coverage']:.2f}）"))

    backfills = []
    for c in accepted:
        pr, our = c["platform"], c["our"]
        backfills.append({
            "id": our["id"], "name": our["name"], "cov": c["coverage"],
            "platform_product_id": pr["platform_product_id"],
            "platform_pay_gmv": pr["pay_gmv"],
            "platform_settle_gmv": pr["settle_gmv"],
            "platform_order_pay_num": pr["order_pay_num"],
            "platform_order_daren_count": pr["order_daren_count"],
            "platform_stat_date": pr["stat_date"],
        })

    done_pids = {b["platform_product_id"] for b in backfills}
    to_create = []
    for c, why in weak_matches:
        print(f"  ⚠️ 候选未通过强证据（cov={c['coverage']:.2f}）："
              f"平台「{c['platform']['name'][:28]}」 vs 我方「{c['our']['name'][:20]}」 —— {why}"
              f" ⇒ **按新商品建档，不错挂**")
        to_create.append(c["platform"])
    for pr in rest:
        if pr["platform_product_id"] not in done_pids:
            to_create.append(pr)

    # ── 组装待写入行 ──
    taken = {r["id"] for r in ours}
    now = int(time.time() * 1000)
    for pr in to_create:
        if args.limit and pr["platform_product_id"] not in {
                t["platform_product_id"] for t in todo}:
            continue
        daren = enrich_cache.get(pr["platform_product_id"])
        if daren is None:
            continue          # 补不到真值就不建档 —— 不造数
        row = build_row(pr, daren)
        row["id"] = new_product_id(row["brand"], pr["platform_product_id"], taken)
        row["created_by"] = owner
        row["created_at"] = now
        row["updated_at"] = now
        rows_to_write.append(row)

    # ── 报告 ──
    print(f"\n[计划写入]")
    print(f"  新建商品：{len(rows_to_write)} 条")
    print(f"  回填平台ID：{len(backfills)} 条")
    for b in backfills:
        print(f"    {b['name'][:22]:<24} ← {b['platform_product_id']} (cov={b['cov']:.2f})")
    if failures:
        print(f"\n[未取到真值 → 不建档] {len(failures)} 条")
        for pid, why in failures[:10]:
            print(f"    {pid}  {why}")

    if not apply_mode:
        print("\n[dry-run] 未写库。确认无误后加 --apply 执行（会自动备份 products 表）。")
        conn.close()
        return 0

    # ── 备份 ──
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = ROOT / "data" / f"products-backup-{ts}.csv"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(products)")]
    with open(bak, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in conn.execute("SELECT * FROM products"):
            w.writerow(list(r))
    db_bak = DB.with_suffix(f".bak-{ts}")
    shutil.copy2(DB, db_bak)
    print(f"\n[备份] {bak.relative_to(ROOT)}  +  {db_bak.name}")

    # ── 写库 ──
    insert_cols = [
        "id", "name", "subtitle", "main_image", "price", "price_range", "brand",
        "category", "monthly_sales", "commission_rates", "commission_amount",
        "influencer_count", "talent_count", "tags", "selling_points", "status",
        "platform_product_id", "platform_pay_gmv", "platform_settle_gmv",
        "platform_order_pay_num", "platform_order_daren_count", "platform_stat_date",
        "created_by", "created_at", "updated_at",
    ]
    for row in rows_to_write:
        conn.execute(
            f"INSERT INTO products ({','.join(insert_cols)}) "
            f"VALUES ({','.join('?' * len(insert_cols))})",
            [row.get(c) for c in insert_cols])
    for b in backfills:
        conn.execute(
            "UPDATE products SET platform_product_id=?, platform_pay_gmv=?, "
            "platform_settle_gmv=?, platform_order_pay_num=?, "
            "platform_order_daren_count=?, platform_stat_date=?, updated_at=? WHERE id=?",
            (b["platform_product_id"], b["platform_pay_gmv"], b["platform_settle_gmv"],
             b["platform_order_pay_num"], b["platform_order_daren_count"],
             b["platform_stat_date"], now, b["id"]))
    # ★ 补归属：已挂平台ID 但 created_by 为空的行 ⇒ 租户端按 owner 过滤时**看不见**
    #   （`/api/products` 非管理员只返回 visible_ids 内的行）
    if owner:
        conn.execute(
            "UPDATE products SET created_by=? WHERE platform_product_id!='' "
            "AND (created_by IS NULL OR created_by='')",
            (owner,))
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    linked = conn.execute(
        "SELECT COUNT(*) FROM products WHERE platform_product_id != ''").fetchone()[0]
    print(f"\n[完成] products 共 {total} 行，其中 {linked} 行已挂平台商品ID")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
