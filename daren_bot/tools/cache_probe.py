#!/usr/bin/env python3
"""复用层探针 + 看板（2026-10-21）。

老大要求：「第一次抓取数据必须存到我们的系统知识库，下次达人需要数据直接调用
存储的数据即可（省算力），一周之后再重新抓取。」

这个脚本回答两个问题：
  --mode probe   端到端验证「同一个问题问两次，第二次命中缓存、不烧 LLM、不扣积分」
  --mode report  看板上生产环境的复用情况：命中率 + 到底省了多少积分

★ 探针必须能失败：任何一环对不上就 exit 1 + 打印「失败原因」，
  绝不打印「看起来没问题」就收工（这轮的教训：光看代码说它短路了不算数，
  得把 LLM 换成「一调用就炸」证明它真的没被调）。

★ probe 模式会自己清理探针租户的行，不留垃圾数据、不发任何飞书消息
  （chat_id 传空，worker 不会推给真人）。

用法:
  python3 daren_bot/tools/cache_probe.py --mode probe
  python3 daren_bot/tools/cache_probe.py --mode report
  python3 daren_bot/tools/cache_probe.py --mode report --tenant daren_boss
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from daren_bot import config            # noqa: E402
from daren_bot import db                # noqa: E402
from daren_bot import executors         # noqa: E402
from daren_bot import handler           # noqa: E402

PROBE_TENANT = "cache_probe_tmp"
PROBE_QUESTION = "我的粉丝画像和选品方向怎么定"
PROBE_PRODUCT_URL = "https://www.jinritemai.com/product/3446348913060228570"


class _Sender:
    """假 sender：只记录，不发飞书。"""

    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))


def _router():
    class _WM:
        def get_sender(self, tenant_key):
            return _Sender()

    return handler.Router(_WM())


def _last_ledger(tk):
    with db.db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_credits_ledger WHERE tenant_key=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (tk,)).fetchone()
    return dict(r) if r else {}


def _last_task(tk, ttype):
    with db.db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_collect_tasks WHERE tenant_key=? AND type=? "
            "ORDER BY created_at DESC, rowid DESC LIMIT 1", (tk, ttype)).fetchone()
    return dict(r) if r else {}


def _cleanup(tk, product_key=None):
    """清掉探针留下的行（探针不该在生产库留垃圾）。

    ⚠️ `bot_product_cache` 是**全局表**（没有 tenant_key —— 同一个商品对所有
       达人都一样），要按 product_key 删，不能按租户删。
    """
    with db.db_ctx() as conn:
        for table in ("bot_qa_cache", "bot_credits_ledger",
                      "bot_credits_accounts", "bot_collect_tasks",
                      "bot_product_verdicts"):
            conn.execute(f"DELETE FROM {table} WHERE tenant_key=?", (tk,))
        if product_key:
            conn.execute("DELETE FROM bot_product_cache WHERE product_key=?",
                         (product_key,))


# ---------------- probe：端到端验证 ----------------

def mode_probe(_args) -> int:
    tk = PROBE_TENANT
    q = PROBE_QUESTION
    fails = []
    db.init_db()
    _cleanup(tk)
    db.grant(tk, 50)
    balance0 = db.get_balance(tk)
    print(f"[probe] 租户={tk} 初始余额={balance0} 问题={q!r}")

    # ---- 第 1 次：真跑（真实 LLM）→ 答案应进缓存 ----
    print("[probe] 第 1 次问答（真调 LLM）…")
    t0 = time.time()
    ans1 = executors.execute_qa({"tenant_key": tk, "target": q, "chat_id": ""})
    dt1 = time.time() - t0
    print(f"[probe]   耗时 {dt1:.1f}s，答案 {len(ans1)} 字：{ans1[:60]}…")
    if not ans1 or ans1.startswith("AI 刚才没反应过来"):
        fails.append(f"第 1 次问答没拿到真实答案（LLM 可能不可用）：{ans1[:40]}")
    cached = db.qa_cache_get(tk, q)
    if not cached:
        fails.append("第 1 次跑完没有进 QA 缓存（qa_cache_get 返回空）")
    else:
        print("[probe]   ✓ 已写入 QA 缓存")

    # ---- 第 2 次：走 handler 入队处判定 → 应命中缓存、扣 0 分 ----
    print("[probe] 第 2 次问答（同一问题，应命中缓存）…")
    router = _router()
    router._route_qa({"tenant_key": tk}, "", q, _Sender())
    balance1 = db.get_balance(tk)
    led = _last_ledger(tk)
    task = _last_task(tk, "qa")
    print(f"[probe]   余额 {balance0} → {balance1}（扣 {balance0 - balance1}）"
          f"，ledger action={led.get('action')} cache_hit={led.get('cache_hit')}")
    if balance0 - balance1 != 0:
        fails.append(f"命中缓存却扣了 {balance0 - balance1} 分（应为 0）")
    if int(led.get("cache_hit") or 0) != 1:
        fails.append("ledger 的 cache_hit 不是 1")
    payload = json.loads(task.get("payload") or "{}")
    if not payload.get("cached_answer"):
        fails.append("入队任务没带 cached_answer")
    else:
        print("[probe]   ✓ 命中缓存，扣 0 分")

    # ---- 消费者：把 LLM 换成「一调用就炸」→ 证明真的没调 ----
    orig_llm = executors._qa_llm

    def _boom(*a, **kw):
        raise AssertionError("缓存命中时不应该调 LLM")

    executors._qa_llm = _boom
    try:
        out = executors.execute_qa(task)
    except AssertionError as e:
        out = ""
        fails.append(str(e))
    finally:
        executors._qa_llm = orig_llm
    if out and cached and out.strip() != cached.strip():
        fails.append("消费者返回的答案和缓存里的不一致")
    if out:
        print("[probe]   ✓ 消费者直接返回缓存答案，LLM 一次都没调")

    _cleanup(tk)
    print()
    if fails:
        print("[probe] ❌ 失败：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("[probe] ✅ 全部通过：第 1 次真跑入库，第 2 次命中缓存、不烧 LLM、不扣积分")
    return 0


# ---------------- probe-product：商品路径的端到端验证 ----------------

def mode_probe_product(_args) -> int:
    """商品链路：第一次真采集+真判 → 入缓存；第二次命中结论缓存，不采集不调 LLM。

    ⚠️ 需要达人端登录态有效（登录态失效 → 会话失效 → 这一关过不了）。
    """
    tk = PROBE_TENANT
    fails = []
    db.init_db()
    _cleanup(tk)
    db.grant(tk, 50)
    pk = handler.normalize_product_url(PROBE_PRODUCT_URL)
    balance0 = db.get_balance(tk)
    print(f"[probe-product] 租户={tk} 商品={pk}")

    # ---- 第 1 次：真采集 + 真判结论 → 应写商品缓存 + 结论缓存 ----
    print("[probe-product] 第 1 次：真采集 + 真判（会开浏览器、等平台接口）…")
    t0 = time.time()
    try:
        out1 = executors.execute_product_collect(
            {"tenant_key": tk, "target": pk, "chat_id": ""})
    except Exception as e:
        out1 = ""
        fails.append(f"第 1 次采集/判定失败：{e}")
    dt1 = time.time() - t0
    print(f"[probe-product]   耗时 {dt1:.1f}s")
    if out1:
        d1 = json.loads(out1)
        print(f"[probe-product]   商品={d1.get('title')}"
              f"  结论首行={d1.get('md', '').splitlines()[:2]}")
    v_hit = db.verdict_cache_get(tk, pk)
    if not v_hit:
        fails.append("第 1 次跑完没有写结论缓存")
    else:
        print(f"[probe-product]   ✓ 结论缓存已写入"
              f"（verdict={v_hit.get('verdict')}）")

    # ---- 第 2 次：走 handler 入队处 → 应命中结论缓存 ----
    print("[probe-product] 第 2 次：同一商品，应命中结论缓存…")
    router = _router()
    router._route_link({"tenant_key": tk}, "", PROBE_PRODUCT_URL, _Sender())
    balance1 = db.get_balance(tk)
    led = _last_ledger(tk)
    task = _last_task(tk, "product")
    print(f"[probe-product]   余额 {balance0} → {balance1}（扣 {balance0 - balance1}）"
          f"，ledger action={led.get('action')} cache_hit={led.get('cache_hit')}")
    if balance0 - balance1 != bot_config_cost_cached():
        fails.append(f"命中结论缓存却扣了 {balance0 - balance1} 分"
                     f"（应为 {bot_config_cost_cached()}）")
    if int(led.get("cache_hit") or 0) != 1:
        fails.append("ledger 的 cache_hit 不是 1")
    payload = json.loads(task.get("payload") or "{}")
    if not payload.get("cached_verdict"):
        fails.append("入队任务没带 cached_verdict")

    # ---- 消费者：采集 + LLM 都换成「一调用就炸」→ 证明真的没调 ----
    orig_collect = executors.collect_product_by_url
    orig_verdict = executors._llm_product_verdict

    def _boom(*a, **kw):
        raise AssertionError("结论缓存命中时不应该再采集/调 LLM")

    executors.collect_product_by_url = _boom
    executors._llm_product_verdict = _boom
    try:
        out2 = executors.execute_product_collect(task)
    except AssertionError as e:
        out2 = ""
        fails.append(str(e))
    finally:
        executors.collect_product_by_url = orig_collect
        executors._llm_product_verdict = orig_verdict
    if out2:
        d2 = json.loads(out2)
        print(f"[probe-product]   ✓ 消费者直接返回缓存结论"
              f"（{d2.get('title')}），采集和 LLM 一次都没调")

    _cleanup(tk, pk)
    print()
    if fails:
        print("[probe-product] ❌ 失败：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("[probe-product] ✅ 全部通过：第 1 次真采集入库，第 2 次命中结论缓存、"
          "不采集不调 LLM")
    return 0


def bot_config_cost_cached():
    from daren_bot import config as _cfg
    return _cfg.COST_PRODUCT_CACHED


# ---------------- report：生产复用情况 ----------------

def reuse_savings(n_qa_hit: int, n_p_hit_verdict: int, n_p_hit_data: int) -> dict:
    """复用层到底省了多少 —— **按每种命中自己的真实消耗算，不能合并成一个数**。

    ★ 2026-10-26 修（B 边的 a7b7113 把两种命中合并统计后暴露的）：原来把
      「结论缓存命中」与「商品数据缓存命中」合并成一个 n_p_hit，按
      「省 20-5=15 分 + 一次采集 + 一次 LLM」算 ⇒ **两个都错**：

        · **结论缓存命中**是**零消耗**（0 分，不调浏览器也不调 LLM）
          ⇒ 省的是**整笔 20 分**（不是 15 分），且**省一次采集 + 一次 LLM**；
        · **商品数据缓存命中**只省了「开浏览器采集」，**LLM 照跑**
          ⇒ 省 20-5=**15 分** + 一次采集，**没省 LLM**。

    ⇒ 「省了多少」必须**按消耗量分口径**，否则看板会长期报错。

    返回：saved（省下积分）/ saved_llm（省下 LLM 次数）/ saved_collect（省下采集次数）
    """
    saved_product = (
        n_p_hit_verdict * config.COST_COLLECT_PRODUCT
        + n_p_hit_data * (config.COST_COLLECT_PRODUCT - config.COST_PRODUCT_CACHED)
    )
    return {
        "saved": n_qa_hit * config.COST_QA + saved_product,
        # ⚠️ 商品数据缓存命中那部分 LLM 照跑 ⇒ 不计入
        "saved_llm": n_qa_hit + n_p_hit_verdict,
        "saved_collect": n_p_hit_verdict + n_p_hit_data,
    }


def mode_report(args) -> int:
    db.init_db()
    tk = args.tenant or ""
    where = "WHERE tenant_key=?" if tk else ""
    params = (tk,) if tk else ()

    def q(sql, p=None):
        with db.db_ctx() as conn:
            if p is None:
                p = params if "?" in sql else ()
            return [dict(r) for r in conn.execute(sql, p)]

    print("=" * 66)
    print("复用层看板（老大：省算力）")
    print(f"库：{config.DB_PATH}" + (f"   租户：{tk}" if tk else ""))
    print("=" * 66)

    rows = q("SELECT action, cost, cache_hit, COUNT(*) n, "
             "SUM(cost) total_cost FROM bot_credits_ledger "
             f"{where} GROUP BY action, cost, cache_hit ORDER BY n DESC", )
    if not rows:
        print("(还没有积分流水)")
    else:
        print("\n【积分流水按动作分组】")
        print(f"{'action':<28}{'单价':>6}{'命中':>6}{'次数':>8}{'总扣分':>10}")
        for r in rows:
            hit = "✓" if int(r["cache_hit"] or 0) else "-"
            print(f"{r['action']:<28}{r['cost']:>6}{hit:>6}"
                  f"{r['n']:>8}{r['total_cost']:>10}")

    # 命中统计
    qa_total = q("SELECT COUNT(*) c FROM bot_credits_ledger "
                 f"{where}{' AND' if tk else ' WHERE'} action='qa'")
    qa_hit = q("SELECT COUNT(*) c FROM bot_credits_ledger "
               f"{where}{' AND' if tk else ' WHERE'} action='qa_cached'")
    prod_total = q("SELECT COUNT(*) c FROM bot_credits_ledger "
                   f"{where}{' AND' if tk else ' WHERE'} action='collect_product'")
    # ★ 2026-10-26：结论缓存命中（0 分）与商品数据缓存命中（5 分）**分两个 action 记流水**
    #   ⇒ 计数**两个都算**（少算 = 复用率偏低），但**算「省了多少」必须分开**（两者消耗量不同）。
    prod_hit_verdict = q("SELECT COUNT(*) c FROM bot_credits_ledger "
                         f"{where}{' AND' if tk else ' WHERE'} "
                         "action='collect_product_verdict_cached'")
    prod_hit_data = q("SELECT COUNT(*) c FROM bot_credits_ledger "
                       f"{where}{' AND' if tk else ' WHERE'} "
                       "action='collect_product_cached'")
    n_qa = qa_total[0]["c"] if qa_total else 0
    n_qa_hit = qa_hit[0]["c"] if qa_hit else 0
    n_p = prod_total[0]["c"] if prod_total else 0
    n_p_hit_verdict = prod_hit_verdict[0]["c"] if prod_hit_verdict else 0
    n_p_hit_data = prod_hit_data[0]["c"] if prod_hit_data else 0
    n_p_hit = n_p_hit_verdict + n_p_hit_data
    st = reuse_savings(n_qa_hit, n_p_hit_verdict, n_p_hit_data)

    print("\n【复用命中率】")
    print(f"  问答  : 命中 {n_qa_hit} / 共 {n_qa} 次"
          f"（{(n_qa_hit / n_qa * 100 if n_qa else 0):.0f}%）"
          f"  每次命中省 {config.COST_QA} 分 + 一次 LLM")
    print(f"  商品  : 命中 {n_p_hit} / 共 {n_p + n_p_hit} 次"
          f"（{(n_p_hit / (n_p + n_p_hit) * 100 if (n_p + n_p_hit) else 0):.0f}%）")
    print(f"          ├ 结论命中 {n_p_hit_verdict} 次：省 {config.COST_COLLECT_PRODUCT} 分"
          f"（零消耗）+ 一次采集 + 一次 LLM")
    print(f"          └ 商品数据命中 {n_p_hit_data} 次：省 "
          f"{config.COST_COLLECT_PRODUCT - config.COST_PRODUCT_CACHED} 分 + 一次采集"
          f"（**LLM 照跑** ⇒ 没省 LLM）")

    print(f"\n  合计省下 {st['saved']} 积分"
          f"（另有 {st['saved_llm']} 次 LLM + {st['saved_collect']} 次采集 未调用）")

    print("\n【缓存存量】")
    for sql, label in (
        ("SELECT COUNT(*) c FROM bot_qa_cache", "QA 缓存条数"),
        ("SELECT COUNT(*) c FROM bot_product_verdicts", "选品结论档案条数"),
        ("SELECT COUNT(*) c FROM bot_product_cache", "商品数据缓存条数"),
    ):
        c = q(sql)[0]["c"]
        print(f"  {label:<16}{c}")

    print("\n【缓存 TTL】")
    print(f"  问答 {db.QA_CACHE_TTL // 3600} 小时"
          f" | 选品结论 {db.VERDICT_CACHE_TTL // 86400} 天"
          f" | 商品数据 {db.PRODUCT_CACHE_TTL // 86400} 天")
    print("=" * 66)
    return 0


def main():
    ap = argparse.ArgumentParser(description="复用层探针 / 看板")
    ap.add_argument("--mode", choices=["probe", "probe-product", "report"],
                    default="report")
    ap.add_argument("--tenant", default="", help="report 模式限定租户")
    args = ap.parse_args()
    if args.mode == "probe":
        return mode_probe(args)
    if args.mode == "probe-product":
        return mode_probe_product(args)
    return mode_report(args)


if __name__ == "__main__":
    sys.exit(main())
