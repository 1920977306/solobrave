#!/usr/bin/env python3
"""商品链接 → 平台采集 端到端探针（2026-11-04）。

为什么需要它：平台页面改版/登录态失效时，「达人发商品链接 → 出选品结论」
这条链路会**悄悄失效**（HTTP 不报错、日志不炸，只是达人再也收不到结论）。
探针每天真打一遍，挂了立刻暴露。

做什么：
  1. 从生产任务表里挑一条**达人真实发过**的商品链接（优先 v.douyin.com
     短链——形态最复杂、最容易被平台改版打断；没有则退 haohuo 长链）；
  2. 走和线上一模一样的链路：extract_product_id → collect_product_by_url
     （达人端浏览器页面内 fetch material_list）；
  3. 断言拿到真商品数据（品名 + 到手价）⇒ exit 0；
     任何一步挂了 ⇒ exit 1 + 打印死因（可接 cron/launchd 告警）。

铁律（同 cache_probe.py）：探针必须能失败 —— 断言的是「链路真的通」，
不是「脚本跑完了」。

用法：
  python3 daren_bot/tools/product_link_probe.py            # 挑链接跑一遍
  python3 daren_bot/tools/product_link_probe.py <url>      # 指定链接跑
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from daren_bot import config            # noqa: E402
from daren_bot import db                # noqa: E402
from daren_bot import product_link      # noqa: E402
from daren_bot import executors as ex   # noqa: E402


def pick_sample_url() -> str:
    """从生产任务表挑一条达人真实发过的商品链接（短链优先）。"""
    with db.db_ctx() as conn:
        row = conn.execute(
            "SELECT target FROM bot_collect_tasks "
            "WHERE target LIKE '%v.douyin.com/%' AND type='product' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            return row["target"]
        row = conn.execute(
            "SELECT target FROM bot_collect_tasks "
            "WHERE target LIKE '%haohuo.jinritemai%' AND type='product' "
            "ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            return row["target"]
    return ""


def main() -> int:
    url = (sys.argv[1] if len(sys.argv) > 1 else "").strip() or pick_sample_url()
    if not url:
        print("❌ 生产任务表里找不到任何商品链接样本，探针无从跑起")
        return 1
    print(f"样本链接: {url}")

    t0 = time.time()
    pid = product_link.extract_product_id(url)
    if not pid:
        print(f"❌ 第一步就死：extract_product_id 抽不到商品ID（{time.time()-t0:.1f}s）"
              "——短链 302 解析或平台改版")
        return 1
    print(f"商品ID: {pid}（{time.time()-t0:.2f}s）")

    t1 = time.time()
    try:
        stats = ex.collect_product_by_url(url, timeout=240)
    except Exception as e:
        print(f"❌ 采集挂了（{time.time()-t1:.1f}s）{type(e).__name__}: {e}")
        return 1

    name, price = (stats.get("name") or "").strip(), (stats.get("price") or "").strip()
    if not name or not price:
        print(f"❌ 采到了但数据是空的：name={name!r} price={price!r}"
              "——material_list 契约可能变了")
        return 1

    print(f"✅ 链路全通（总耗时 {time.time()-t0:.1f}s）")
    print(f"   品名: {name}")
    print(f"   到手价: {price}  店铺分: {stats.get('shop_score')}  "
          f"好评率: {stats.get('good_ratio')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
