# -*- coding: utf-8 -*-
"""积分对账护栏（2026-10-29 自查实案）守卫测试

实案：daren_boss 账面 112 vs 流水重放 212——一笔退款记账了但余额没到账，
差了 100 分没有任何报警，对账全靠人眼。

不变式（本文件守住）：**账面余额 == 初始 500 − 非系统账流水重放**。
  - 系统账行（weekly_auto_collect / ref sys: / legacy weekly_report cost=1）
    只记成本不动余额 ⇒ 重放必须跳过；
  - reconcile_credits() 发现漂移 ⇒ 校正账面 + 记 reconcile_adjust 流水，
    且校正后再重放**自洽**（幂等：跑两次第二次零漂移）。

★ 每条守卫按 §16 Y 第 4 条执行「能不能红」自检（注释里标对照）。
"""

import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TK = "tk_reconcile"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DAREN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAREN_DB", str(tmp_path / "daren_bot.db"))
    monkeypatch.delenv("DAREN_USE_KIMI", raising=False)
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    for m in (cfg, ddb):
        importlib.reload(m)
    ddb.init_db()
    ddb.add_tenant(f"app_{TK}", TK, "enc")
    return ddb


class TestReplay:
    def test_fresh_tenant_replays_to_initial(self, bot):
        assert bot.replay_balance(bot.db_ctx().__enter__(), TK) if False else True
        with bot.db_ctx() as conn:
            assert bot.replay_balance(conn, TK) == 500

    def test_charge_refund_grant_replay(self, bot):
        bot.deduct(TK, 100, "collect_douyin", ref_task_id="t1")
        bot.refund(TK, 100, ref_task_id="t1")
        bot.grant(TK, 50)
        # grant 50 = 入账 ⇒ 重放 500-100+100+50
        with bot.db_ctx() as conn:
            assert bot.replay_balance(conn, TK) == 550

    def test_system_rows_skipped(self, bot):
        """系统账行（auto_collect / sys: / legacy cost=1）不进重放。
        （对照：_is_system_cost_row 任一分支删了 ⇒ 本用例红）"""
        bot.deduct(TK, 100, "collect_douyin", ref_task_id="t1")
        bot.log_system_cost(TK, "weekly_auto_collect", 100, ref_task_id="t2")
        bot.log_system_cost(TK, "weekly_report", 1, ref_task_id="sys:tips")
        bot.log_system_cost(TK, "weekly_report", 1)  # legacy：ref 空 cost 1
        with bot.db_ctx() as conn:
            assert bot.replay_balance(conn, TK) == 400

    def test_real_weekly_report_charge_counts(self, bot):
        """真实周报扣费（cost=30）必须进重放（与系统账 cost=1 区分）。"""
        bot.deduct(TK, 30, "weekly_report", ref_task_id="tw1")
        with bot.db_ctx() as conn:
            assert bot.replay_balance(conn, TK) == 470


class TestReconcile:
    def _drift(self, bot):
        """人为制造漂移：账面直接改少 100（模拟「记账未到账」的历史 bug）。"""
        with bot.db_ctx() as conn:
            conn.execute(
                "UPDATE bot_credits_accounts SET balance=balance-100 "
                "WHERE tenant_key=?", (TK,))

    def test_drift_auto_corrected_with_audit_row(self, bot):
        bot.deduct(TK, 100, "collect_douyin", ref_task_id="t1")
        self._drift(bot)   # 账面 300，重放 400
        fixes = bot.reconcile_credits()
        assert fixes == [(TK, 100)]
        assert bot.get_balance(TK) == 400
        with bot.db_ctx() as conn:
            row = conn.execute(
                "SELECT cost, balance_after, ref_task_id FROM bot_credits_ledger "
                "WHERE tenant_key=? AND action='reconcile_adjust'", (TK,)).fetchone()
        # cost=0 纯审计行（参与重放会把校正当真钱再算一遍）；
        # 漂移量与校正值留在行里供审计
        assert row and row[0] == 0 and row[1] == 400 and "drift:+100" in row[2]

    def test_second_run_is_noop(self, bot):
        """校正后再对账必须零漂移（重放含 adjust 行后自洽）。
        （对照：reconcile 写的 adjust 行 sign 写反 ⇒ 本用例红）"""
        self._drift(bot)
        assert bot.reconcile_credits() == [(TK, 100)]
        assert bot.reconcile_credits() == []

    def test_no_drift_noop(self, bot):
        bot.deduct(TK, 20, "collect_product", ref_task_id="t9")
        assert bot.reconcile_credits() == []
        assert bot.get_balance(TK) == 480

    def test_init_db_hook_survives_failure(self, bot, monkeypatch):
        """对账抛异常不能挡 init_db（护栏性质）。"""
        def _boom():
            raise RuntimeError("reconcile down")
        monkeypatch.setattr(bot, "reconcile_credits", _boom)
        bot.init_db()  # 不抛即过

    def test_init_db_hook_fixes_drift(self, bot):
        """启动路径端到端：init_db 里挂着对账，漂移在启动时被校正。"""
        self._drift(bot)
        bot.init_db()
        assert bot.get_balance(TK) == 500  # 无流水时重放=500
