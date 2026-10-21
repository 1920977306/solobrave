#!/usr/bin/env python3
"""选品判断审计 —— 回答「AI 判断能力到底是不是对的」。

用法：
    python3 daren_bot/tools/audit_verdict.py --tenant daren_boss
    python3 daren_bot/tools/audit_verdict.py --tenant daren_boss --repeat 3 --limit 6
    python3 daren_bot/tools/audit_verdict.py --tenant daren_boss --mode matched --json

审计四件事（每一项都能单独判死）：

1. **盲判检测**：`_talent_context()` 是否为空。
   空 = AI 根本没拿到达人画像，它在盲判 —— 这不是「判断准不准」的问题，
   是「有没有输入」的问题。（2026-10-21 实测踩过：sqlite `mode=ro` 打不开
   WAL 库 → 异常被静默吞掉 → 上下文恒空 → 一直盲判且无日志。）

2. **一致性**：同一商品重复跑 N 次，结论是否稳定。
   结论飘 = 同一个商品给用户两个答案，决策不可用。

3. **结论分布**：是不是「一律能带」或「一律慎带」——
   全判慎带往往意味着 prompt 的兜底规则被误触发（缺字段就慎带），
   判断能力等于零。

4. **与规则引擎对照**（--mode matched）：`product_talent_match.match_score`
   （0-100 规则分）vs AI 结论。规则分高（≥75）的商品 AI 是否也判「能带」。
   分歧大 = AI 结论要么没信息量，要么方向反了。

⚠️ 本脚本**不能证明 AI「准」** —— 那需要历史实际带货结果回测，
   而 `deals` 表目前是空的（零历史成交记录）。
   本脚本证明的是「AI 有输入、输出稳定、且与规则引擎不矛盾」。
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from daren_bot import executors  # noqa: E402

VERDICT_RE = re.compile(r'(能带|慎带|别带)')


def _conn():
    con = sqlite3.connect(str(executors.SOLOBRAVE_DB), timeout=10)
    con.execute("PRAGMA query_only=ON")
    con.row_factory = sqlite3.Row
    return con


def _talent_id(douyin_id: str) -> str:
    con = _conn()
    try:
        r = con.execute("SELECT id FROM talents WHERE douyin_id=? "
                        "ORDER BY created_at DESC LIMIT 1", (douyin_id,)).fetchone()
        return r["id"] if r else ""
    finally:
        con.close()


def _comm(rates) -> str:
    try:
        d = json.loads(rates or "{}")
        return f"{max(d.values()):.0f}%" if d else ""
    except Exception:
        return ""


def _matched_samples(talent_id: str, limit: int) -> list:
    """该达人的规则匹配商品（--mode matched）。

    ⚠️ 实测多数达人只有 1-2 条规则匹配记录（`product_talent_match` 覆盖太稀疏），
      统计上没有意义 → 默认用 gradient 模式。
    """
    con = _conn()
    try:
        rows = con.execute(
            "SELECT m.match_score, p.name, p.price, p.commission_rates, "
            "       p.monthly_sales, p.category "
            "FROM product_talent_match m JOIN products p ON p.id=m.product_id "
            "WHERE m.talent_id=? ORDER BY m.match_score DESC LIMIT ?",
            (talent_id, limit)).fetchall()
        return [{"rule_score": r["match_score"], "tag": "匹配",
                 "stats": {"name": r["name"], "price": r["price"],
                           "commission": _comm(r["commission_rates"]),
                           "sales": r["monthly_sales"] or 0,
                           "category": r["category"] or ""}} for r in rows]
    finally:
        con.close()


def _gradient_samples(limit: int) -> list:
    """梯度样本：覆盖不同价格带 / 类目 / 佣金的商品。

    梯度样本能真正测出两件事：
      ① **区分度** —— 明显该带的（同类目+高佣+价格带吻合）能不能被判「能带」
      ② **稳定性** —— 同一样本重复跑结论会不会飘
    """
    con = _conn()
    try:
        rows = con.execute(
            "SELECT name, price, commission_rates, monthly_sales, category "
            "FROM products WHERE name IS NOT NULL AND name != '' "
            "ORDER BY monthly_sales DESC").fetchall()
        buckets = {"低价": [], "中价": [], "高价": []}
        for r in rows:
            try:
                price = float(r["price"] or 0)
            except (TypeError, ValueError):
                price = 0
            tag = "低价" if price < 60 else ("中价" if price < 300 else "高价")
            buckets[tag].append({
                "rule_score": None, "tag": tag,
                "stats": {"name": r["name"], "price": r["price"],
                          "commission": _comm(r["commission_rates"]),
                          "sales": r["monthly_sales"] or 0,
                          "category": r["category"] or ""}})
        per = max(1, limit // 3)
        out = []
        for tag, lst in buckets.items():
            out.extend(lst[:per])
        return out[:limit]
    finally:
        con.close()


def audit(tenant_key: str, repeat: int, limit: int, mode: str) -> dict:
    from daren_bot import db
    tenant = db.get_tenant(tenant_key) or {}
    dyid = tenant.get("bind_douyin_id") or ""
    report = {"tenant": tenant_key, "douyin_id": dyid, "mode": mode, "items": []}

    # ① 盲判检测
    ctx = executors._talent_context(tenant_key)
    report["context_ok"] = bool(ctx.strip())
    report["context_len"] = len(ctx)
    report["context_head"] = ctx[:200]
    if not report["context_ok"]:
        report["fatal"] = ("达人上下文为空 —— AI 在盲判，本次审计不成立。"
                           "先修数据读取（daren_bot/executors.py 的 _ro_conn）。")
        return report

    if mode == "matched":
        tid = _talent_id(dyid)
        samples = _matched_samples(tid, limit) if tid else []
    else:
        samples = _gradient_samples(limit)
    if not samples:
        report["fatal"] = "样本为空"
        return report

    t0 = time.time()
    for s in samples:
        verdicts = []
        for _ in range(repeat):
            out = executors._product_verdict(tenant_key, s["stats"])
            m = VERDICT_RE.search(out)
            verdicts.append(m.group(1) if m else "未出结果")
        s["verdicts"] = verdicts
        s["stable"] = len(set(verdicts)) == 1
        s["verdict"] = verdicts[0]
        rs = s.get("rule_score")
        if rs is not None:
            s["rule_expect"] = "能带" if rs >= 75 else ("别带" if rs <= 45 else None)
        else:
            s["rule_expect"] = None
        s["agree"] = (s["rule_expect"] is None
                      or s["verdict"] == s["rule_expect"])
        report["items"].append(s)
    report["elapsed"] = round(time.time() - t0, 1)

    # ②③ 稳定性 + 分布（分布按**全部次**统计，不只看第一次）
    dist = {}
    for s in report["items"]:
        for v in s["verdicts"]:
            dist[v] = dist.get(v, 0) + 1
    report["distribution"] = dist
    report["stability"] = round(
        sum(1 for s in report["items"] if s["stable"]) / len(report["items"]), 3)
    comparable = [s for s in report["items"] if s["rule_expect"]]
    report["agreement"] = (round(
        sum(1 for s in comparable if s["agree"]) / len(comparable), 3)
        if comparable else None)
    return report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="daren_boss")
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--mode", default="gradient", choices=["gradient", "matched"])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = audit(args.tenant, args.repeat, args.limit, args.mode)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return
    print(f"=== 选品判断审计 · {rep['tenant']}（抖音号 {rep['douyin_id']}）"
          f"· 模式 {rep['mode']} ===")
    if rep.get("fatal"):
        print("❌", rep["fatal"])
        sys.exit(1)
    print(f"① 达人上下文：{'✅ 有数据' if rep['context_ok'] else '❌ 空（盲判）'}"
          f"（{rep['context_len']} 字符）")
    print(f"   {rep['context_head'][:120]}…")
    print(f"\n② 一致性（每商品跑 {args.repeat} 次）：{rep['stability'] * 100:.0f}% 稳定")
    print(f"③ 结论分布（按全部 {args.repeat * len(rep['items'])} 次统计）：{rep['distribution']}")
    if rep["agreement"] is not None:
        print(f"④ 与规则分对照：一致率 {int(rep['agreement'] * 100)}%")
    else:
        print("④ 与规则分对照：无对照样本（gradient 模式）")
    print(f"\n{'商品':30} {'档':>4} {'规则分':>6} {'AI结论':>6} {'各次':>14} {'稳定':>5}")
    for s in rep["items"]:
        rs = '-' if s.get("rule_score") is None else s["rule_score"]
        print(f"{str(s['stats']['name'])[:28]:30} {s.get('tag',''):>4} {str(rs):>6} "
              f"{s['verdict']:>6} {str(s['verdicts']):>14} "
              f"{'✅' if s['stable'] else '⚠️飘移':>5}")
    print(f"\n耗时 {rep['elapsed']}s")
    print("\n⚠️ 本审计**不能证明 AI「准」** —— 那需要历史实际带货结果回测，"
          "而 deals 表目前为空（零历史成交记录）。")


if __name__ == "__main__":
    main()
