# -*- coding: utf-8 -*-
"""小拓闹钟守卫（修订2 2026-11-10 增量派单：日报循环改为员工驱动）。

铁律测试矩阵：
1. build_task_message：带类目/配额/流程，且明确「建议人工过目不许默认写无」
2. deliver_task：subprocess 带 node PATH 环境、CLI 参数正确；CLI 失败不抛出
3. report_delivered：last_run_at ≥ 本次投递时刻 = 交了；更早/空 = 没交
4. main 主流程：投了且交作业 → 0 退出、无告警；投了没交 → 管理员告警落库
   （闹钟绝不代写日报——日报只能由 agent 经 /square-report 写入）
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'xiaotuo_daily', Path(__file__).resolve().parent.parent / 'xiaotuo_daily.py')
xd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(xd)

DUTY = {'agent_id': 'emp_xiaotuo', 'category': '鞋靴箱包', 'quota': 5,
        'boundary': '联系达人、谈佣金、寄样、结算'}


# ── 1. 任务书 ──

def test_task_message_carries_duty_and_discipline():
    m = xd.build_task_message(DUTY)
    assert '类目=鞋靴箱包' in m and '配额 5' in m
    assert 'scan' in m and 'decide' in m and 'report' in m
    assert '建议人工过目' in m and '不许默认写「无」' in m


def test_task_message_default_category():
    m = xd.build_task_message({'category': '', 'quota': 3})
    assert '类目=全部' in m and '配额 3' in m


# ── 2. 投递 ──

def _fake_run(stdout='{}', rc=0, stderr=''):
    import types
    return types.SimpleNamespace(returncode=rc, stdout=stdout, stderr=stderr)


def test_deliver_passes_node_env_and_args(monkeypatch):
    captured = {}

    def fake_run(args, **kw):
        captured['args'] = args
        captured['env'] = kw.get('env')
        return _fake_run(json.dumps({'ok': True, 'runId': 'r1'}))
    monkeypatch.setattr(xd.subprocess, 'run', fake_run)
    note, run_id = xd.deliver_task('干活')
    assert run_id == 'r1' and note == 'turn 已结束'
    assert captured['args'][:4] == [xd.OPENCLAW_CLI, 'agent', '--agent', 'emp_xiaotuo']
    assert '--message' in captured['args'] and '干活' in captured['args']
    assert '/opt/homebrew/bin' in captured['env']['PATH'].split(':')


def test_deliver_never_raises(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError('no cli')
    monkeypatch.setattr(xd.subprocess, 'run', boom)
    note, run_id = xd.deliver_task('x')
    assert run_id is None and '失败' in note

    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: _fake_run(rc=127, stderr='node?'))
    note, _ = xd.deliver_task('x')
    assert '127' in note


def test_deliver_gateway_accepted_keeps_runid(monkeypatch):
    out = json.dumps({'ok': False, 'runId': 'abc',
                      'error': {'message': 'gateway timeout'}})
    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: _fake_run(stdout=out))
    note, run_id = xd.deliver_task('x')
    assert run_id == 'abc' and '已接受' in note


# ── 3. 看门 ──

@pytest.fixture()
def duty_db(tmp_path, monkeypatch):
    db = tmp_path / 'a.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
                    updated_at INTEGER)''')
    conn.execute('''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id TEXT,
                    type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', db)
    return db


def test_report_delivered_after_dispatch(duty_db):
    dispatch_ms = int(__import__('time').time() * 1000) - 1000
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '', 0)",
                 (dispatch_ms + 1,))   # 投递之后才回写 ⇒ 交了
    conn.commit()
    conn.close()
    assert xd.report_delivered(dispatch_ms) is True


def test_report_delivered_stale_or_missing(duty_db):
    dispatch_ms = int(__import__('time').time() * 1000)
    assert xd.report_delivered(dispatch_ms) is False       # 无行
    conn = sqlite3.connect(str(duty_db))
    # 同一天但早于本次投递（当天跑过另一轮）⇒ 不算交
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '', 0)",
                 (dispatch_ms - 1,))
    conn.commit()
    conn.close()
    assert xd.report_delivered(dispatch_ms) is False


# ── 4. main 主流程 ──

def _patch_common(monkeypatch, duty_db, cli_result):
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY))
    if cli_result == 'raise':
        def boom(*a, **k):
            raise RuntimeError('x')
        monkeypatch.setattr(xd.subprocess, 'run', boom)
    else:
        monkeypatch.setattr(xd.subprocess, 'run',
                            lambda *a, **k: _fake_run(stdout=cli_result or '{}'))
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)


def test_main_delivered_and_reported_no_alert(tmp_path, monkeypatch, duty_db):
    _patch_common(monkeypatch, duty_db, json.dumps({'ok': True, 'runId': 'r'}))
    # report 端点在投递之后才回写 last_run_at ⇒ 必须晚于 main() 里的 dispatch 时刻
    future_ms = int(__import__('time').time() * 1000) + 3600_000
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (future_ms,))
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE agent_id = 'emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert n == 0, '交作业就不许告警'


def test_main_no_report_writes_admin_alert(tmp_path, monkeypatch, duty_db):
    _patch_common(monkeypatch, duty_db, 'raise')   # CLI 挂了也要看门告警
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title, content FROM notifications "
                        "WHERE agent_id = 'emp_xiaotuo'").fetchall()
    conn.close()
    assert len(rows) == 1 and '未交日报' in rows[0][0] and '@小拓' in rows[0][1]


def test_main_no_duty_row_skips_quietly(monkeypatch):
    monkeypatch.setattr(xd, 'load_duty', lambda: None)
    monkeypatch.setattr(xd.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('不该投任务')))
    assert xd.main() == 0


# ── 5. 派单二（2026-11-12）：当日去重闸 + 看门狗竞态复查 ──

def test_dedup_gate_skips_manual_redispatch(monkeypatch, duty_db):
    """缺陷1：今日已交过日报 ⇒ 手动重投被跳过（不投任务、不告警、不烧规划 token）。"""
    now_ms = int(__import__('time').time() * 1000)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (now_ms,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY, last_run_at=now_ms))
    monkeypatch.setattr(xd.subprocess, 'run',
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError('不该投任务')))
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    n = conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
    conn.close()
    assert n == 0


def test_dedup_gate_ignores_yesterday(monkeypatch, duty_db):
    """闸只认今天：last_run_at 是昨天 ⇒ 照常投递（9:30 自然跑不受误伤）。"""
    yesterday_ms = xd._today_start_ms() - 3600_000
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY, last_run_at=yesterday_ms))
    _patch_common(monkeypatch, duty_db, json.dumps({'ok': True, 'runId': 'r'}))
    # 投递后交作业 ⇒ 无告警正常收工
    conn = sqlite3.connect(str(duty_db))
    future_ms = int(__import__('time').time() * 1000) + 3600_000
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (future_ms,))
    conn.commit()
    conn.close()
    assert xd.main() == 0


def test_watchdog_recheck_silences_race_alert(tmp_path, monkeypatch, duty_db):
    """缺陷2：看门超时但日报通知恰在竞态窗口落库 ⇒ 复查命中，告警静默跳过。

    复刻 11-11 20:46 误报：日报通知 20:45:47 落库（title 含「日报」），
    看门狗 20:46:05 判超时——复查应看到当日日报通知存在 ⇒ 不写 escalation。
    """
    _patch_common(monkeypatch, duty_db, 'raise')
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    monkeypatch.setattr(xd, 'report_delivered', lambda since_ms: False)  # last_run_at 没赶上
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.execute(
        "INSERT INTO notifications VALUES ('notif_race', 'user_7acb72ff', 'emp_xiaotuo', "
        "'task_reminder', '达人筛选专员日报 · 11月12日', '今日深看5', 0, ?)",
        (int(__import__('time').time() * 1000),))
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title FROM notifications WHERE title LIKE '%未交日报%'").fetchall()
    conn.close()
    assert rows == [], '竞态窗口内日报已落库 ⇒ 不许写未交日报告警'


def test_watchdog_still_alerts_when_truly_missing(tmp_path, monkeypatch, duty_db):
    """反向对照：既无 last_run_at 又无当日日报通知 ⇒ 告警照写（真未报场景不静默）。"""
    _patch_common(monkeypatch, duty_db, 'raise')
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title FROM notifications WHERE title LIKE '%未交日报%'").fetchall()
    conn.close()
    assert len(rows) == 1
