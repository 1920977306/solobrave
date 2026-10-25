#!/usr/bin/env python3
"""真库验证：重复请求不再重复扣费（2026-10-25 真漏洞的回归验证）。

★ 用**独立探针 target**（`probe_xxx`）⇒ 不会撞上任何历史任务
  ⇒ 验证完只删自己建的那一条，不动业务数据。

用法：`.venv-daren/bin/python scripts/probe_credits_real_db.py`
"""
import os
import sqlite3
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from daren_bot import config, credits, db  # noqa: E402

TK = "daren_boss"
DB = os.path.join(ROOT, "data", "daren-bot", "daren_bot.db")


def main():
    target = f"probe_{uuid.uuid4().hex[:8]}"   # 全新 target
    bal0 = db.get_balance(TK)
    print(f"起始余额：{bal0}")

    # ① 正常路径（消耗算力 ⇒ 应扣分 + 建任务）
    st1, tid1, bal1 = credits.charge_and_enqueue(
        TK, "douyin", target, "collect_douyin")
    print(f"第1次：status={st1}  扣后余额={bal1}  task={tid1[:8]}")

    # ② 同一 target 重复请求（幂等命中 ⇒ 不该再扣）
    st2, tid2, bal2 = credits.charge_and_enqueue(
        TK, "douyin", target, "collect_douyin")
    print(f"第2次：status={st2}  扣后余额={bal2}  task={tid2[:8]}")

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    n_tasks = conn.execute(
        "SELECT COUNT(*) FROM bot_collect_tasks WHERE target=?",
        (target,)).fetchone()[0]
    n_ledger = conn.execute(
        "SELECT COUNT(*) FROM bot_credits_ledger WHERE ref_task_id=?",
        (tid1,)).fetchone()[0]
    row = conn.execute(
        "SELECT action,cost,ref_task_id FROM bot_credits_ledger "
        "WHERE ref_task_id=?", (tid1,)).fetchone()
    conn.close()

    print()
    print(f"⇒ 实际扣费：{bal0 - bal2} 分（应 = {config.COST_COLLECT_DOUYIN}）")
    print(f"⇒ 幂等命中未重复扣费：{bal1 == bal2}")
    print(f"⇒ 复用同一任务：{tid1 == tid2}")
    print(f"⇒ 该 target 任务数：{n_tasks}（应 1）")
    print(f"⇒ 关联流水条数：{n_ledger}（应 1）")
    print(f"⇒ 流水内容：{dict(row) if row else None}")

    ok = (bal0 - bal2 == config.COST_COLLECT_DOUYIN and bal1 == bal2
          and tid1 == tid2 and n_tasks == 1 and n_ledger == 1)

    # 清理探针数据 + 退回扣的分
    conn = sqlite3.connect(DB)
    conn.execute("DELETE FROM bot_collect_tasks WHERE target=?", (target,))
    conn.execute("DELETE FROM bot_credits_ledger WHERE ref_task_id=?", (tid1,))
    conn.commit()
    conn.close()
    db.refund(TK, config.COST_COLLECT_DOUYIN, "refund")
    print(f"\n✅ 已清理探针数据，余额：{db.get_balance(TK)}（起始 {bal0}）")
    print("✅ 真库验证通过" if ok else "❌ 真库验证未通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
