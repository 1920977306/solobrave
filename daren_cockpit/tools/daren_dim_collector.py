#!/usr/bin/env python3
"""商品维度补齐器：把达人端商品真值补进 `products` 表。

背景（2026-10-24 老大拍板「两边都要」）
--------------------------------------
老大给了抖音达人 AI 助手的商品推荐截图，推荐理由用了 9 个维度。
我们库里的商品维度**严重缺失**：

| 维度 | 补齐前 | 本工具补齐来源（**全部达人端**） |
|---|---|---|
| 店铺体验分 | 0/47 | `material_list` → `shop_info.shop_score_info.shop_score.score` |
| 好评率 | 0/47 | `material_list` → `product_info.good_ratio` |
| 近7日短视频销量 | 0/47 | 联盟榜单 `board_pmt`（date_type=7d, genre_type=video） |
| 榜单排名 | 0/47 | `material_list` → `recommend_reason.extra`（**平台自带的榜单元数据**） |
| 近7日总销量 | 无 | `material_list` → `sale_axis` 末 7 天自算 |

⚠️ **转化率没有平台通用真值**（这轮探针结论，别再猜）：
- `/api/anchor/product_diagnose/search|detail` 只诊断**我们自己达人账号**的商品
  （实测 `total:0`），不是任意商品的指标；
- `/pc/diagnose/get_total` 返回风控码 `11001 当前网络不稳定`。
⇒ 转化率列保持 NULL（= 未知），评分器会把缺失维度**排除并重新归一化**，
  不会拿 0 冒充。

⚠️ **两个「近7日销量」口径不同，必须分列**：
- `video_sales_7d` = 达人端**榜单**口径（按体裁筛短视频，只覆盖榜内商品）
- `sales_7d`       = `sale_axis` 自算的**商品总销量**（覆盖全部商品）

用法
----
    python3 daren_cockpit/tools/daren_dim_collector.py --dry-run   # 只看，不写库（默认）
    python3 daren_cockpit/tools/daren_dim_collector.py --apply      # 真写（自动备份）
    python3 daren_cockpit/tools/daren_dim_collector.py --apply --limit 3 --refresh

回滚
----
`--apply` 前自动把 products 表导出到 `data/products-backup-<ts>.csv`。
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

DB = ROOT / "data" / "solobrave.db"
CACHE = ROOT / "data" / "daren-dim-cache.json"     # data/ 已 gitignore
WORKER = "http://127.0.0.1:8095"

# 新增列（additive，不动任何现有列的语义）
# ★ 全部**允许 NULL**：NULL = 没有真值；0 = 平台真的给了 0。
#   把「没采到」写成 0 = 造数（评分器会当 0 分惩罚商品）。
NEW_COLUMNS = [
    ("shop_score", "REAL"),                    # 店铺体验分 0-100
    ("good_ratio", "REAL"),                    # 好评率 %
    ("sales_7d", "INTEGER"),                   # 近7日总销量（sale_axis 自算）
    ("video_sales_7d", "INTEGER"),             # 近7日短视频销量（榜单口径）
    ("board_rank", "INTEGER"),                 # 榜单排名
    ("board_rank_type", "TEXT"),               # 榜单类型 sale/sales/trend/seller
    ("board_date_type", "TEXT"),               # 榜单周期 7d/30d/90d/1d…
    ("board_genre_type", "TEXT"),              # 体裁 video/live/window/artc
    ("board_name", "TEXT"),                    # 榜单名（如「露趾凉鞋榜单」）
    ("board_reason", "TEXT"),                  # 平台推荐理由原文
    ("sale_axis_json", "TEXT"),                # 30 天日销量轴 JSON
    ("category_ids", "TEXT"),                  # 一/二/三级类目 id（逗号分隔）
    ("daren_synced_at", "INTEGER"),            # 采集时间戳(ms)
]

# 榜单口径：近7日 × 短视频（对齐达人端推荐理由里的「7天短视频销量」）
BOARD_DATE_TYPE = "7d"
BOARD_GENRE_TYPE = "video"
BOARD_RANK_TYPE = "sale"
BOARD_CATE_LEVEL = 3          # 三级类目榜（比一级类目榜精准得多）
BOARD_PAGE_SIZE = 50


# ────────────────────────────── schema ──────────────────────────────
def ensure_schema(conn: sqlite3.Connection) -> list:
    have = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    added = []
    for name, decl in NEW_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE products ADD COLUMN {name} {decl}")
            added.append(name)
    conn.commit()
    return added


# ────────────────────────────── HTTP ──────────────────────────────
def _http_json(url: str, payload: dict | None = None, timeout: int = 30) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _wait_job(cid: str, timeout_s: int = 180) -> tuple[dict | None, str]:
    """轮询 worker job。返回 (结果, 错误说明)。"""
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
                return (json.loads(raw) if isinstance(raw, str) else raw), ""
            except json.JSONDecodeError as e:
                return None, f"结果不是 JSON：{e}"
        if st in ("failed", "error"):
            return None, f"worker 采集失败：{job.get('stage') or job.get('error')}"
    return None, "超时"


def fetch_product(pid: str, timeout_s: int = 180) -> tuple[dict | None, str]:
    """平台商品ID → 达人端商品结构化数据（走 worker 常驻浏览器，签名天然具备）。"""
    try:
        resp = _http_json(f"{WORKER}/collect-product-id",
                           {"product_id": pid, "size": 5})
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, f"worker 不可达：{e}"
    cid = resp.get("collect_id")
    if not cid:
        return None, f"worker 拒绝：{resp}"
    data, err = _wait_job(cid, timeout_s)
    if data is None:
        return None, err
    if not data.get("name"):
        return None, "结果里没有商品名（可能搜不到）"
    return data, ""


def fetch_board(cate_id: str, cate_level: int = BOARD_CATE_LEVEL,
                date_type: str = BOARD_DATE_TYPE,
                genre_type: str = BOARD_GENRE_TYPE,
                rank_type: str = BOARD_RANK_TYPE,
                timeout_s: int = 180) -> tuple[dict | None, str]:
    """类目榜 → {平台商品ID: {rank, sale_num, content}}。"""
    body = {"rank_type": rank_type, "date_type": date_type,
            "genre_type": genre_type, "cate_id": int(cate_id),
            "cate_level": int(cate_level), "cursor": 0, "count": BOARD_PAGE_SIZE}
    try:
        resp = _http_json(f"{WORKER}/collect-daren-api",
                           {"path": "/pc/leaderboard_26/board_pmt",
                            "method": "POST", "body": body})
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        return None, f"worker 不可达：{e}"
    cid = resp.get("collect_id")
    if not cid:
        return None, f"worker 拒绝：{resp}"
    data, err = _wait_job(cid, timeout_s)
    if data is None:
        return None, err
    if (data.get("code") not in (0, "0")):
        return None, f"code={data.get('code')} msg={data.get('msg')}"
    return parse_board(data), ""


def parse_board(payload: dict) -> dict:
    """board_pmt 响应 → {平台商品ID: {rank, sale_num, content}}。"""
    out: dict[str, dict] = {}
    data = (payload or {}).get("data") or {}
    for item in (data.get("summary_promotions") or []):
        pid = str(item.get("product_id") or "")
        if not pid:
            continue
        sm = ((item.get("custom_model") or {}).get("selection_square_model") or {})
        bd = sm.get("board_data") or {}
        bs = bd.get("board_sale") or {}
        out[pid] = {
            "rank": bd.get("rank"),
            "sale_num": bs.get("sale_num"),
            "content": bs.get("content") or "",
            "pay_cnt_ratio": bd.get("pay_cnt_ratio") or "",
        }
    return out


# ────────────────────────────── 解析 ──────────────────────────────
def _num(text) -> float:
    if text is None:
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    m = re.search(r"[\d.]+", str(text))
    return float(m.group()) if m else 0.0


def sum_last_days(sale_axis: list, days: int = 7) -> int:
    """日销量轴 → 近 N 天销量合计。

    ⚠️ 按**日期字符串排序**取最后 N 个点（平台给的是 30 天日序列）。
    拿不到就返回 0（= 未知，调用方写 NULL 不写 0）。
    """
    pts = []
    for p in (sale_axis or []):
        if not isinstance(p, dict):
            continue
        d = str(p.get("date") or "")
        q = p.get("qty")
        if not d:
            continue
        try:
            pts.append((d, float(q or 0)))
        except (TypeError, ValueError):
            continue
    if not pts:
        return 0
    pts.sort(key=lambda kv: kv[0])
    tail = pts[-days:]
    return int(round(sum(v for _, v in tail)))


def build_dims(daren: dict, board_hit: dict | None) -> dict:
    """达人端商品数据 + 榜单命中 → products 新列的值。"""
    d = daren or {}
    board = d.get("board") or {}
    rank = board.get("rank")
    try:
        rank = int(float(rank)) if rank not in (None, "") else None
    except (TypeError, ValueError):
        rank = None
    # 榜单排名：优先用平台自带的 recommend_reason（覆盖榜外商品），
    # 其次用类目榜命中值（近7日短视频口径）。
    if rank is None and board_hit and board_hit.get("rank"):
        try:
            rank = int(board_hit["rank"])
        except (TypeError, ValueError):
            rank = None

    video_7d = None
    if board_hit and board_hit.get("sale_num") not in (None, ""):
        try:
            video_7d = int(float(board_hit["sale_num"]))
        except (TypeError, ValueError):
            video_7d = None

    sale_axis = d.get("sale_axis") or []
    sales_7d = sum_last_days(sale_axis, 7)
    shop_score = d.get("shop_score")
    good_ratio = d.get("good_ratio")
    cat_ids = [str(d.get(k) or "") for k in
               ("category_first_id", "category_second_id", "category_third_id")]
    cat_ids = ",".join([c for c in cat_ids if c])

    def _num_or_none(v):
        n = _num(v)
        return n if n > 0 else None

    return {
        "shop_score": _num_or_none(shop_score),
        "good_ratio": _num_or_none(good_ratio),
        "sales_7d": sales_7d if sales_7d > 0 else None,
        "video_sales_7d": video_7d,
        "board_rank": rank,
        "board_rank_type": board.get("rank_type") or (BOARD_RANK_TYPE if rank else ""),
        "board_date_type": board.get("date_type") or (BOARD_DATE_TYPE if video_7d else ""),
        "board_genre_type": board.get("genre_type") or (BOARD_GENRE_TYPE if video_7d else ""),
        "board_name": board.get("leaderboard_name") or "",
        "board_reason": d.get("recommend_reason") or "",
        "sale_axis_json": json.dumps(sale_axis, ensure_ascii=False) if sale_axis else None,
        "category_ids": cat_ids or None,
    }


# ────────────────────────────── 缓存 ──────────────────────────────
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


# ────────────────────────────── 备份 ──────────────────────────────
def backup_products(conn: sqlite3.Connection) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    path = ROOT / "data" / f"products-backup-{ts}.csv"
    rows = conn.execute(
        "SELECT * FROM products").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM products LIMIT 0").description]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        w.writerows(rows)
    return str(path)


# ────────────────────────────── 主流程 ──────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="达人端商品维度补齐（→ products 表）")
    ap.add_argument("--dry-run", action="store_true", help="只看，不写库（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真写库（会先自动备份）")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 个商品（调试用）")
    ap.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新抓")
    ap.add_argument("--skip-board", action="store_true", help="不拉榜单（只补商品自身维度）")
    args = ap.parse_args()
    apply_mode = args.apply and not args.dry_run

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    added = ensure_schema(conn)
    print(f"[schema] 新增列：{added or '（已存在）'}")

    rows = conn.execute(
        "SELECT id, name, platform_product_id FROM products "
        "WHERE platform_product_id IS NOT NULL AND platform_product_id != '' "
        "ORDER BY id").fetchall()
    if args.limit:
        rows = rows[: args.limit]
    print(f"[范围] {len(rows)} 个已挂平台ID 的商品")

    cache = _cache_load()
    dims_by_pid: dict[str, dict] = {}
    cat_ids: set[str] = set()
    failures: list[tuple[str, str]] = []

    # ── ① 商品自身维度（material_list）──
    for i, r in enumerate(rows, 1):
        pid = r["platform_product_id"]
        if not args.refresh and pid in cache:
            daren = cache[pid]
            why = "（缓存）"
        else:
            daren, why = fetch_product(pid)
            if daren:
                cache[pid] = daren
                _cache_save(cache)
        if not daren:
            failures.append((pid, why))
            print(f"  {i:>2}/{len(rows)} {pid} ✘ {why}")
            continue
        third = str(daren.get("category_third_id") or "")
        if third:
            cat_ids.add(third)
        print(f"  {i:>2}/{len(rows)} {pid} ✔ 店分{_num(daren.get('shop_score')):g} "
              f"好评{_num(daren.get('good_ratio')):g}% "
              f"7日销{sum_last_days(daren.get('sale_axis') or [], 7)} "
              f"榜{(daren.get('board') or {}).get('rank') or '-'} | "
              f"{(daren.get('name') or '')[:30]}")

    # ── ② 类目榜（近7日 × 短视频）──
    board_map: dict[str, dict] = {}
    if not args.skip_board and cat_ids:
        print(f"\n[榜单] 拉 {len(cat_ids)} 个三级类目榜"
              f"（{BOARD_DATE_TYPE} × {BOARD_GENRE_TYPE}）…")
        for cid_ in sorted(cat_ids):
            hit, err = fetch_board(cid_)
            if hit is None:
                print(f"  类目 {cid_} ✘ {err}")
                continue
            board_map.update(hit)
            print(f"  类目 {cid_} ✔ {len(hit)} 条")
    elif args.skip_board:
        print("\n[榜单] 已跳过（--skip-board）")

    # ── ③ 组装 + 写库 ──
    for r in rows:
        pid = r["platform_product_id"]
        daren = cache.get(pid)
        if not daren:
            continue
        dims_by_pid[pid] = build_dims(daren, board_map.get(pid))

    print(f"\n[汇总] 成功 {len(dims_by_pid)} / {len(rows)}"
          + (f"，失败 {len(failures)}" if failures else ""))
    if failures:
        for pid, why in failures[:10]:
            print(f"  ✘ {pid}: {why}")

    if not apply_mode:
        print("\n[dry-run] 未写库。加 --apply 真写。")
        sample = list(dims_by_pid.items())[:3]
        for pid, d in sample:
            print(f"  {pid}: {json.dumps(d, ensure_ascii=False)[:220]}")
        return 0

    backup = backup_products(conn)
    print(f"\n[备份] {backup}")
    now = int(time.time() * 1000)
    cols = [c[0] for c in NEW_COLUMNS]
    upd = 0
    for pid, d in dims_by_pid.items():
        d = dict(d)
        d["daren_synced_at"] = now
        sets = ", ".join(f"{c}=?" for c in cols)
        vals = [d.get(c) for c in cols]
        conn.execute(f"UPDATE products SET {sets} WHERE platform_product_id=?",
                     vals + [pid])
        upd += 1
    conn.commit()
    print(f"[写入] 更新 {upd} 行")

    # 复核：写进去的覆盖率
    check = conn.execute(
        "SELECT COUNT(*) t, "
        "SUM(CASE WHEN shop_score IS NOT NULL THEN 1 ELSE 0 END) shop, "
        "SUM(CASE WHEN good_ratio IS NOT NULL THEN 1 ELSE 0 END) good, "
        "SUM(CASE WHEN sales_7d IS NOT NULL THEN 1 ELSE 0 END) s7, "
        "SUM(CASE WHEN video_sales_7d IS NOT NULL THEN 1 ELSE 0 END) v7, "
        "SUM(CASE WHEN board_rank IS NOT NULL THEN 1 ELSE 0 END) br "
        "FROM products WHERE platform_product_id != ''").fetchone()
    print(f"[复核] 店铺分 {check['shop']}/{check['t']} "
          f"好评率 {check['good']}/{check['t']} "
          f"近7日总销量 {check['s7']}/{check['t']} "
          f"近7日短视频销量 {check['v7']}/{check['t']} "
          f"榜单排名 {check['br']}/{check['t']}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
