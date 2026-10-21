#!/usr/bin/env python3
"""飞书全链路验证：模拟达人在飞书发商品链接 → handler 判定 → worker 采集判结论 → 推卡片。

第 1 轮：清缓存 → 真采集（扣 20 分）→ 飞书收到完整分析卡片
第 2 轮：再发一次 → 命中结论缓存（扣 5 分）→ 飞书收到秒回卡片

★ 用租户自己的凭证发（严禁混用）；目标租户是 daren_test2（老大的测试号）。
★ 本脚本只做「读 + 入队 + 等结果」，不改业务逻辑；真发消息这一步是
  老大手动建应用前唯一没法自动化的环节，用真实链路先证明它通。
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from daren_bot import crypto_util, db, handler          # noqa: E402
from daren_bot.ws_manager import Sender                 # noqa: E402

TK = "daren_test2"
URL = "https://www.jinritemai.com/product/3446348913060228570"


def _sender(tk):
    t = db.get_tenant(tk)
    mk = crypto_util.load_or_create_master_key()
    secret = crypto_util.decrypt_secret(mk, t["app_secret_enc"])
    return Sender(t["app_id"], secret)


def _router(s):
    class _WM:
        def get_sender(self, tenant_key):
            return s
    return handler.Router(_WM())


def _clear_cache(pk):
    """清掉这个商品的缓存，保证第 1 轮是真采集。"""
    with db.db_ctx() as conn:
        conn.execute("DELETE FROM bot_product_cache WHERE product_key=?", (pk,))
        conn.execute("DELETE FROM bot_product_verdicts WHERE tenant_key=? "
                     "AND product_key=?", (TK, pk))


def _wait_task(before_id, timeout=300):
    """等 worker 把这一轮任务跑完，返回任务行。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        with db.db_ctx() as conn:
            r = conn.execute(
                "SELECT * FROM bot_collect_tasks WHERE tenant_key=? AND type='product' "
                "AND id!=? ORDER BY created_at DESC LIMIT 1",
                (TK, before_id)).fetchone()
        if r and r["status"] in ("done", "failed", "need_login", "not_found"):
            return dict(r)
        time.sleep(2)
    return None


def _last_task_id():
    with db.db_ctx() as conn:
        r = conn.execute("SELECT id FROM bot_collect_tasks WHERE tenant_key=? "
                         "ORDER BY created_at DESC LIMIT 1", (TK,)).fetchone()
    return r["id"] if r else ""


def main():
    db.init_db()
    pk = handler.normalize_product_url(URL)
    chat_id = db.get_latest_chat_id(TK)
    bal0 = db.get_balance(TK)
    print(f"租户={TK} 余额={bal0} chat={chat_id}")
    print(f"商品={pk}\n")

    s = _sender(TK)
    router = _router(s)

    # ---------- 第 1 轮：真采集 ----------
    _clear_cache(pk)
    print("── 第 1 轮：清缓存后发链接（应真采集，扣 20 分）──")
    before = _last_task_id()
    router._route_link({"tenant_key": TK}, chat_id, URL, s)
    t = _wait_task(before)
    bal1 = db.get_balance(TK)
    if t:
        print(f"   任务 {t['id'][:22]} status={t['status']} "
              f"cache_hit={t['cache_hit']} cost={t['credits_cost']}")
        print(f"   余额 {bal0} → {bal1}（扣 {bal0 - bal1}）")
        if t["status"] != "done":
            print(f"   ⚠️ 没跑成功，看 worker 日志")
            return 1
    else:
        print("   ⚠️ 超时未完成")
        return 1

    # ---------- 第 2 轮：命中结论缓存 ----------
    print("\n── 第 2 轮：同一链接再发一次（应命中结论缓存，扣 5 分）──")
    before = _last_task_id()
    router._route_link({"tenant_key": TK}, chat_id, URL, s)
    t2 = _wait_task(before)
    bal2 = db.get_balance(TK)
    if t2:
        print(f"   任务 {t2['id'][:22]} status={t2['status']} "
              f"cache_hit={t2['cache_hit']} cost={t2['credits_cost']}")
        print(f"   余额 {bal1} → {bal2}（扣 {bal1 - bal2}）")
        ok = (t2["cache_hit"] == 1 and (bal1 - bal2) == 5)
    else:
        ok = False
        print("   ⚠️ 超时未完成")

    print("\n" + "=" * 60)
    print(f"第 1 轮扣 {bal0 - bal1} 分（真采集+真判）")
    print(f"第 2 轮扣 {bal1 - bal2} 分（结论缓存命中，省 {bal0 - bal1 - (bal1 - bal2)} 分"
          f" + 一次采集 + 一次 LLM）")
    print(f"✅ 全链路通" if ok else "❌ 第 2 轮没命中缓存")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
