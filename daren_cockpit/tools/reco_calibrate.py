#!/usr/bin/env python3
"""推荐指数星级阈值 —— **可复现的校准脚本**。

为什么需要这个脚本
------------------
星级阈值（`product_reco.STAR_THRESHOLDS`）是按**真实数据分布**定的，
但 2026-10-24 那次是手算的直方图 —— 数据一变（补了维度、换了佣金口径、
商品池扩容）就失准，而且没人能复现「当时按什么定的」。

这个脚本把校准变成**一条命令**：
    python3 daren_cockpit/tools/reco_calibrate.py            # 看分布 + 是否还成立
    python3 daren_cockpit/tools/reco_calibrate.py --json     # 导出直方图（贴进测试）
    python3 daren_cockpit/tools/reco_calibrate.py --check    # 只做门禁（CI 用）

铁律（来自「自建评分器」那套）
------------------------------
· **绝对分**当地板（防「池里个个都很差」被排成 5★ 的假绿灯）
· **池内分位**当区分度（防「清一色 5★」没区别）
· 阈值必须按真实分布校准 ⇒ 分布变了要重跑本脚本并更新
  `tests/test_product_reco.py` 里的校准回归直方图

只读库，不写任何东西。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from daren_cockpit.tools import product_reco as reco  # noqa: E402

DB = _ROOT / "data" / "solobrave.db"

# 5★ 占比上限（超过就说明阈值失去区分度）
MAX_FIVE_STAR_RATIO = 0.25
# 星级档位下限（低于就说明区分度不足）
MIN_STAR_LEVELS = 4


def _load(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        talents = [dict(r) for r in conn.execute(
            "SELECT * FROM talents WHERE created_at IS NOT NULL")]
        products = [dict(r) for r in conn.execute(
            "SELECT * FROM products WHERE status!='archived'")]
    finally:
        conn.close()
    return talents, products


def collect_samples(talents, products) -> list[float]:
    """所有「策略 × 商品」的评分样本（策略池里的全部候选）。"""
    samples: list[float] = []
    for t in talents:
        profile = reco.build_profile(t)
        strategies = reco.build_strategies(profile)
        if not strategies:
            continue
        pool_cats = [c for c in (reco._category_of(p) for p in products) if c]
        for st in strategies:
            resolved = reco._resolve_pool_category(st.category, pool_cats)
            if not resolved:
                continue
            st.category = resolved
            cands = [p for p in products
                     if reco._in_category(p, st.category) and reco._in_price_band(p, st)]
            for r in reco.score_products(cands, st):
                samples.append(r.score)
    return samples


def collect_displayed(talents, products, top_n: int = 6) -> list[dict]:
    """**实际展示给用户**的推荐（每条策略取 top_n，跨策略去重）。

    ★ 这才是校准星级阈值的**目标population**：
      「清一色 5★」「假绿灯」都是**用户看得见的列表**上的说法，
      拿「策略池全体候选」当校准对象会得出误导结论
      （池内候选有几百个，用户只看到每条策略的前 6 个）。
    """
    best: dict[str, dict] = {}
    for t in talents:
        profile = reco.build_profile(t)
        strategies = reco.build_strategies(profile)
        if not strategies:
            continue
        pool_cats = [c for c in (reco._category_of(p) for p in products) if c]
        # ⚠️ 去重字典**按达人分开** —— 同一个商品对不同达人的分数/星级不同，
        #    全局去重会把不同达人的推荐混成一批（口径就错了）。
        per_talent: dict[str, dict] = {}
        for st in strategies:
            resolved = reco._resolve_pool_category(st.category, pool_cats)
            if not resolved:
                continue
            st.category = resolved
            cands = [p for p in products
                     if reco._in_category(p, st.category) and reco._in_price_band(p, st)]
            scored = reco.score_products(cands, st)
            for r in scored[:top_n]:
                old = per_talent.get(r.product_id)
                if old is None or r.score > old["score"]:
                    per_talent[r.product_id] = {"score": r.score, "stars": r.stars}
        best.update({f"{t.get('id', '')}::{k}": v for k, v in per_talent.items()})
    return list(best.values())


def histogram(samples: list[float]) -> dict:
    """5 分一档的直方图（键形如 "65-69"）。"""
    buckets = Counter()
    for s in samples:
        lo = int(s // 5) * 5
        buckets[f"{lo}-{lo + 4}"] += 1
    return dict(sorted(buckets.items(), key=lambda kv: int(kv[0].split("-")[0])))


def star_counts(samples: list[float]) -> dict:
    c = Counter(reco.stars_of(s) for s in samples)
    return {f"{k}★": c.get(k, 0) for k in range(1, 6)}


def check(samples: list[float]) -> tuple[bool, list[str]]:
    """当前阈值在**展示population**上是否还成立。返回 (是否通过, 问题列表)。"""
    problems: list[str] = []
    if not samples:
        return False, ["没有样本 —— 库是空的？先跑采集器"]
    stars = [reco.stars_of(s) for s in samples]
    n5 = stars.count(5)
    ratio = n5 / len(stars)
    if ratio > MAX_FIVE_STAR_RATIO:
        problems.append(
            f"5★ 占比 {ratio:.0%}（{n5}/{len(stars)}）> {MAX_FIVE_STAR_RATIO:.0%}"
            " —— 阈值失去区分度，要重校准")
    if len(set(stars)) < MIN_STAR_LEVELS:
        problems.append(
            f"只有 {len(set(stars))} 档星级（{sorted(set(stars))}），"
            f"区分度不足（要 ≥{MIN_STAR_LEVELS} 档）")
    if stars.count(1) < 1:
        problems.append("没有 1★ —— 低分商品被抬成假绿灯")
    return (not problems), problems


def check_displayed(displayed: list[dict]) -> tuple[bool, list[str]]:
    """展示population的星级分布是否健康（**这才是校准的主口径**）。"""
    problems: list[str] = []
    if not displayed:
        return False, ["没有展示样本"]
    stars = [d["stars"] for d in displayed]
    n = len(stars)
    ratio = stars.count(5) / n
    if ratio > MAX_FIVE_STAR_RATIO:
        problems.append(
            f"用户实际看到的推荐里 5★ 占 {ratio:.0%}"
            f"（{stars.count(5)}/{n}）> {MAX_FIVE_STAR_RATIO:.0%} —— 星级失去区分度")
    if len(set(stars)) < MIN_STAR_LEVELS:
        problems.append(
            f"用户实际看到的推荐只有 {len(set(stars))} 档星级"
            f"（{sorted(set(stars))}），区分度不足")
    if stars.count(1) < 1:
        problems.append("展示列表里没有 1★ —— 低分档缺失")
    return (not problems), problems


def main() -> int:
    ap = argparse.ArgumentParser(description="推荐指数星级阈值校准")
    ap.add_argument("--db", default=str(DB), help="sqlite 路径")
    ap.add_argument("--json", action="store_true", help="导出直方图 JSON")
    ap.add_argument("--check", action="store_true",
                    help="只做门禁：阈值不成立就 exit 1")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.is_file():
        print(f"[!] 库不存在: {db_path}", file=sys.stderr)
        return 2
    talents, products = _load(db_path)
    samples = collect_samples(talents, products)
    displayed = collect_displayed(talents, products)
    if not samples or not displayed:
        print("[!] 没有样本（达人画像不足或商品池为空）", file=sys.stderr)
        return 2

    samples.sort()
    hist = histogram(samples)
    stars = star_counts(samples)
    d_stars = Counter(d["stars"] for d in displayed)
    d_hist = {"score": f"{min(d['score'] for d in displayed):.1f}~"
                      f"{max(d['score'] for d in displayed):.1f}",
              "stars": {f"{k}★": d_stars.get(k, 0) for k in range(1, 6)}}
    ok, problems = check_displayed(displayed)
    ok_all, problems_all = check(samples)

    if args.json:
        print(json.dumps({
            "n_candidates": len(samples),
            "n_displayed": len(displayed),
            "candidate_histogram": hist,
            "candidate_stars": stars,
            "displayed": d_hist,
            "thresholds": [list(t) for t in reco.STAR_THRESHOLDS],
            "ok": ok,
            "problems": problems,
            "candidate_side_problems": problems_all,
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"阈值 {reco.STAR_THRESHOLDS}")
    print(f"\n【主口径】用户实际看到的推荐 {len(displayed)} 条")
    print(f"  分数区间 {d_hist['score']}｜星级分布 "
          + " ".join(f"{k}★:{d_stars.get(k, 0)}({d_stars.get(k, 0) / len(displayed):.0%})"
                     for k in range(1, 6)))
    print(f"\n【参考】策略池全体候选 {len(samples)} 个"
          f"（min {samples[0]:.1f} / p25 {samples[len(samples) // 4]:.1f} / "
          f"median {samples[len(samples) // 2]:.1f} / "
          f"p90 {samples[int(len(samples) * 0.9)]:.1f} / max {samples[-1]:.1f}）")
    print("  直方图（5 分一档）:")
    for k, v in hist.items():
        print(f"    {k}: {v}")
    print("  绝对分星级: "
          + " ".join(f"{k}:{stars[k]}" for k in stars))

    if problems:
        print("\n[!] 展示口径不成立：")
        for p in problems:
            print(f"  · {p}")
        print("  ⇒ 改 product_reco.STAR_THRESHOLDS，并同步更新 "
              "tests/test_product_reco.py 的校准直方图")
        return 1
    print("\n[OK] 展示口径成立（5★ 占比达标、星级档位≥4、有 1★）")
    if problems_all:
        print("[i] 候选池整体口径提示（参考，不作为门禁）：")
        for p in problems_all:
            print(f"  · {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
