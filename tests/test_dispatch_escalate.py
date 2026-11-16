# -*- coding: utf-8 -*-
"""P2-③ 守卫：升级推送机制的判定逻辑 + 采集告警双锚形态锁。

根因（§158④ 实证）：p1_escalated_at 自始只有 schema 无人写——「升级通知通道坏」
实为「机制不存在」。dispatch_escalate.py 补建后，这些断言防它退化成旧状态：
① 升级判定纯函数的行为钉死（优先级/状态/幂等/账龄锚点）；
② collector_watch 双锚（商品同步/达人采集）不得合并回单锚（11-16 实证单锚被
   另一条链的「成功」误读成误报）。
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    'dispatch_escalate', ROOT / 'scripts' / 'dispatch_escalate.py')
de = importlib.util.module_from_spec(spec)
spec.loader.exec_module(de)

NOW = 1_800_000_000_000
H = 3600 * 1000


def _row(**kw):
    base = dict(id='dq_x', priority=1, status='待领',
                created_at=NOW - 25 * H, claimed_at=0,
                p1_escalated_at=0, summary='s')
    base.update(kw)
    return base


def test_p1_overdue_due():
    select = de.select_escalations([_row()], NOW)
    assert len(select) == 1


def test_p2_never_escalates():
    assert de.select_escalations([_row(priority=2)], NOW) == []


def test_done_and_void_never_escalate():
    for st in ('完成', '已作废', '待验'):
        assert de.select_escalations([_row(status=st)], NOW) == []


def test_already_escalated_skipped():
    assert de.select_escalations([_row(p1_escalated_at=NOW - H)], NOW) == []


def test_fresh_claimed_not_due():
    # 认领才 1 小时（锚点=claimed_at 优先于 created_at）→ 不升级
    assert de.select_escalations([_row(claimed_at=NOW - 1 * H)], NOW) == []


def test_boundary_24h():
    # 恰好 24h：不升级（严格大于）
    assert de.select_escalations([_row(created_at=NOW - 24 * H)], NOW) == []
    # 24h+1ms：升级
    assert len(de.select_escalations([_row(created_at=NOW - 24 * H - 1)], NOW)) == 1


def test_no_claim_falls_back_to_created():
    # claimed_at=0（未认领）→ 锚 created_at
    due = de.select_escalations([_row(claimed_at=0)], NOW)
    assert len(due) == 1


def test_collector_watch_dual_anchor():
    src = (ROOT / 'collector_watch.py').read_text(encoding='utf-8')
    assert "'商品同步'" in src and "'达人采集'" in src, '双锚被退回单锚'
    assert 'daren_synced_at' in src and 'talent_collects' in src
