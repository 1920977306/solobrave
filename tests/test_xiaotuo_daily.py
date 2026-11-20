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
    note, run_id, rc, _t = xd.deliver_task('干活')
    assert run_id == 'r1' and note == 'turn 已结束'
    assert captured['args'][:4] == [xd.OPENCLAW_CLI, 'agent', '--agent', 'emp_xiaotuo']
    assert '--message' in captured['args'] and '干活' in captured['args']
    assert '/opt/homebrew/bin' in captured['env']['PATH'].split(':')
    # 每日新会话键（2026-11-12）：默认会话膨胀 → preflight compaction flaky
    # 失败直接退出码 1（9:30/11:22 两轮实证），投递必须带当天 session-key
    assert '--session-key' in captured['args']
    key = captured['args'][captured['args'].index('--session-key') + 1]
    assert key == xd.daily_session_key()
    assert key.startswith('agent:emp_xiaotuo:daily-')
    from datetime import datetime as _dt
    assert key.endswith(_dt.now().strftime('%Y%m%d'))


def test_deliver_never_raises(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError('no cli')
    monkeypatch.setattr(xd.subprocess, 'run', boom)
    note, run_id, rc, _t = xd.deliver_task('x')
    assert run_id is None and '失败' in note

    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: _fake_run(rc=127, stderr='node?'))
    note, _, _rc, _t = xd.deliver_task('x')
    assert '127' in note


def test_deliver_gateway_accepted_keeps_runid(monkeypatch):
    out = json.dumps({'ok': False, 'runId': 'abc',
                      'error': {'message': 'gateway timeout'}})
    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: _fake_run(stdout=out))
    note, run_id, rc, _t = xd.deliver_task('x')
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
        (int(__import__('time').time() * 1000) + 3600_000,))  # 班次加密修订：通知须晚于本班投递时刻
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


# ── 5. 交付工具点名铁律（11-13 实证：fresh 会话+k3 下发现式交付空转 41 次） ──

def test_task_message_names_delivery_tool_and_bans_search_discovery():
    m = xd.build_task_message(DUTY)
    assert 'xiaotuo_ops.py' in m and 'report' in m   # 交付工具点名
    assert 'tool_search' in m and '不允许' in m      # 禁止现场发现
    assert '赌博' in m                                # 后果定性


# ── 5b. 反调研铁律（11-18 1730 班实锤：读源码/翻历史通知耗尽长度预算未交卷）──

def test_task_message_bans_research_detours():
    m = xd.build_task_message(DUTY)
    assert '禁止读源码' in m and '禁止翻历史通知' in m
    assert 'scan/decide' in m or 'scan' in m          # 依据锚点=工具输出
    assert '直接写日报交卷' in m


# ── 6. 外部 SIGTERM 自愈重试（§165，2026-11-15）──────────────────────────────
# 11-14 09:37 实锤：CLI 被外部 kill（exit 143）→ 自请求 chat.abort，run 无病。
# 闹钟必须立即换新会话键重试一次（而非等 45min 看门狗），且仅一次防双烧。

def test_external_kill_retries_once_with_fresh_session_key(tmp_path, monkeypatch, duty_db):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        if len(calls) == 1:
            return _fake_run(rc=143, stderr='terminated')      # 首投被外部 SIGTERM
        return _fake_run(stdout=json.dumps({'ok': True, 'runId': 'r2'}))
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY))
    monkeypatch.setattr(xd.subprocess, 'run', fake_run)
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    # 重试前未交（防误判已交而不重试）；看门阶段判定已交（future_ms 晚于投递）⇒ 无告警收工
    # 状态化假看门：重试前那次检查返回 False（未交→触发重试），此后 True（已交→收工）
    _gate = {'n': 0}
    def _rd(since_ms):
        _gate['n'] += 1
        return _gate['n'] >= 2
    monkeypatch.setattr(xd, 'report_delivered', _rd)
    monkeypatch.setattr(xd, 'report_notification_exists_today', lambda *a, **k: False)
    future_ms = int(__import__('time').time() * 1000) + 3600_000
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (future_ms,))
    conn.commit()
    conn.close()
    assert xd.main() == 0
    assert len(calls) == 2, '143 后必须恰好重试一次'
    k1 = calls[0][calls[0].index('--session-key') + 1]
    k2 = calls[1][calls[1].index('--session-key') + 1]
    assert k2 == k1 + '-r2', '重试必须换新会话键（同会话重试=重蹈漫游红线）'


def test_external_kill_no_retry_when_already_delivered(tmp_path, monkeypatch, duty_db):
    calls = []

    def fake_run(args, **kw):
        calls.append(args)
        return _fake_run(rc=143, stderr='terminated')
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY))
    monkeypatch.setattr(xd.subprocess, 'run', fake_run)
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)
    # 作业已交付（last_run_at 晚于投递）⇒ 中止后不重试、不告警
    future_ms = int(__import__('time').time() * 1000) + 3600_000
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (future_ms,))
    conn.commit()
    conn.close()
    assert xd.main() == 0
    assert len(calls) == 1, '作业已交则不许重试（防双烧）'


def test_external_kill_double_143_falls_to_watchdog(tmp_path, monkeypatch, duty_db):
    """两次都 143 ⇒ 不再无限重试，落入正常看门告警流程。"""
    calls = []
    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY))
    monkeypatch.setattr(xd.subprocess, 'run',
                        lambda *a, **k: (calls.append(a), _fake_run(rc=143))[1])
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_dutues VALUES ('emp_xiaotuo', 0, '', 0)") if False else None
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.commit()
    conn.close()
    assert xd.main() == 0
    assert len(calls) == 2, '重试仅一次，二次 143 不得再投'
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title FROM notifications WHERE title LIKE '%未交日报%'").fetchall()
    conn.close()
    assert len(rows) == 1, '两次被kill后看门告警照常落库'


# ── 6. 外部 SIGTERM 自愈重试 · 验收③四口径守卫（§165 + 第四方背书 11-15）──

def test_sigterm_retry_once_new_key_retried_from_and_notice_mark(tmp_path, monkeypatch, duty_db):
    """验收①③合体：exit 143 → 恰重试一次、新键 -r2、任务书注明重试班、
    retried_from 双锚点（JSONL + 通知标题）、日报通知被打「重试班」标。"""
    calls = []

    def seq_run(*a, **k):
        calls.append(a)
        if len(calls) == 1:
            return _fake_run(rc=143, stderr='SIGTERM')          # 首投被外部杀
        return _fake_run(stdout=json.dumps({'ok': True, 'runId': 'r2'}))

    monkeypatch.setattr(xd, 'load_duty', lambda: dict(DUTY))
    monkeypatch.setattr(xd.subprocess, 'run', seq_run)
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)
    monkeypatch.setattr(xd, 'RETRY_LOG', tmp_path / 'retry.jsonl')
    seen = {'n': 0}

    def fake_delivered(since_ms):
        seen['n'] += 1
        return seen['n'] > 1   # 重试决策点=没交（触发重试）；看门轮询=已交（重试班交了）

    monkeypatch.setattr(xd, 'report_delivered', fake_delivered)
    monkeypatch.setattr(xd, 'report_notification_exists_today', lambda *a, **k: False)
    now_ms = int(__import__('time').time() * 1000)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    # 通知 created_at 必须晚于 main() 里的 dispatch 时刻（对齐本文件 last_run_at
    # 的 future_ms 写法）——毫秒级同刻竞态：单跑常同毫秒过、全量跑必超毫秒挂
    future_ms = now_ms + 3600_000
    conn.execute(
        "INSERT INTO notifications VALUES ('notif_r2', 'user_7acb72ff', 'emp_xiaotuo', "
        "'task_reminder', '达人筛选专员日报', '今日深看5', 0, ?)", (future_ms,))
    conn.commit()
    conn.close()

    assert xd.main() == 0
    # 验收①：恰一次重试 + 新会话键
    assert len(calls) == 2
    flat1 = [str(x) for x in calls[1][0]]
    assert any('-r2' in x for x in flat1), '重试必须换新会话键'
    # 任务书注明「本班为重试班」（模型侧锚点，DB 侧另由 mark_retry_delivery 兜底）
    assert any('本班为重试班' in x for x in flat1)
    # 验收③：retried_from 留痕（JSONL 锚点）
    lines = (tmp_path / 'retry.jsonl').read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec['retry_session_key'] == rec['first_session_key'] + '-r2'
    assert rec['first_rc'] == 143
    # 验收③：通知标题锚点（幂等防双交卷的可见标记）
    conn = sqlite3.connect(str(duty_db))
    title = conn.execute(
        "SELECT title FROM notifications WHERE id='notif_r2'").fetchone()[0]
    conn.close()
    assert '重试班' in title and 'retried_from=' in title


def test_mark_retry_delivery_idempotent(tmp_path, monkeypatch, duty_db):
    """同一通知只标一次：重看门/复查双路径都调 mark 也不会叠后缀。"""
    now_ms = int(__import__('time').time() * 1000)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.execute(
        "INSERT INTO notifications VALUES ('notif_x', 'user_7acb72ff', 'emp_xiaotuo', "
        "'task_reminder', '达人筛选专员日报', 'x', 0, ?)", (now_ms,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', duty_db)
    xd.mark_retry_delivery('agent:emp_xiaotuo:daily-20990101', now_ms - 1000)
    xd.mark_retry_delivery('agent:emp_xiaotuo:daily-20990101', now_ms - 1000)
    conn = sqlite3.connect(str(duty_db))
    title = conn.execute(
        "SELECT title FROM notifications WHERE id='notif_x'").fetchone()[0]
    conn.close()
    assert title.count('重试班') == 1


def test_retry_delivery_uses_db_anchor_not_prompt_faith(tmp_path, monkeypatch, duty_db):
    """验收③兜底语义：即使模型没在日报里自称重试班，交付后通知仍被标注——
    检查在 AI 拦不住的地方（与验卷同哲学）。"""
    now_ms = int(__import__('time').time() * 1000)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.execute(
        "INSERT INTO notifications VALUES ('notif_y', 'user_7acb72ff', 'emp_xiaotuo', "
        "'task_reminder', '达人筛选专员日报', '今日深看5（模型没提重试班）', 0, ?)",
        (now_ms,))
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', duty_db)
    xd.mark_retry_delivery('agent:emp_xiaotuo:daily-20990102', now_ms - 1000)
    conn = sqlite3.connect(str(duty_db))
    title = conn.execute(
        "SELECT title FROM notifications WHERE id='notif_y'").fetchone()[0]
    conn.close()
    assert '重试班' in title and '20990102' in title


# ── 7. 班次加密守卫（2026-11-18 老大 18:29 拍板：每日 3 班 9:30/13:30/17:30）──

def test_class_slot_mapping():
    """时刻→班次映射：09:30–13:29=0930 班；13:30–17:29=1330 班；17:30 起=1730 班；
    早于首班=今日 00:00 下界（旧「今日」语义）+ 当前 HHMM 标签。"""
    from datetime import datetime as dt
    d = dt(2026, 11, 19)
    s, lbl = xd.class_slot(d.replace(hour=8, minute=0))
    assert lbl == '0800' and s == d.replace(hour=0, minute=0)
    s, lbl = xd.class_slot(d.replace(hour=9, minute=30))
    assert lbl == '0930' and s == d.replace(hour=9, minute=30)
    s, lbl = xd.class_slot(d.replace(hour=12, minute=59))
    assert lbl == '0930'
    s, lbl = xd.class_slot(d.replace(hour=13, minute=30))
    assert lbl == '1330'
    s, lbl = xd.class_slot(d.replace(hour=23, minute=59))
    assert lbl == '1730'


def test_class_ordinal():
    assert xd.class_ordinal('0930') == 1
    assert xd.class_ordinal('1330') == 2
    assert xd.class_ordinal('1730') == 3
    assert xd.class_ordinal('0800') is None   # 清晨手动跑不在排班表


def test_session_key_per_class():
    """同一天三班三个不同会话键；班级后缀在位（班级隔离，上一班上下文不进下一班）。"""
    assert xd.daily_session_key('0930').endswith('-0930')
    assert xd.daily_session_key('1330').endswith('-1330')
    assert xd.daily_session_key('1730').endswith('-1730')
    # 无参形态保持旧契约（向后兼容）
    from datetime import datetime as _dt
    assert xd.daily_session_key().endswith(_dt.now().strftime('%Y%m%d'))


def test_dedup_gate_class_aware_via_load_duty(monkeypatch):
    """去重闸只挡本班：9:30 班已交 ⇒ 13:00（1330 班）照常（三班互不当误）；
    本班已交 ⇒ 挡；清晨（早于首班）保持旧「今日 00:00」语义。显式 now 全时段确定。"""
    from datetime import datetime as dt
    d = dt(2026, 11, 19)
    ms = lambda t: int(t.timestamp() * 1000)
    # 场景1：9:30 班 10:00 交付，13:31 起属 1330 班 ⇒ 不挡（三班互不阻塞）。
    # （13:00 仍属 0930 班窗口 [09:30,13:30)，那时段重投照挡——语义正确）
    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'last_run_at': ms(d.replace(hour=10, minute=0))})
    assert xd.already_reported_today(now=d.replace(hour=13, minute=31)) is False
    # 场景2：13:40 交付，13:45 同属 1330 班 ⇒ 挡
    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'last_run_at': ms(d.replace(hour=13, minute=40))})
    assert xd.already_reported_today(now=d.replace(hour=13, minute=45)) is True
    # 场景3：清晨 8:00（早于首班）旧「今日」语义——8:30 交付后 8:45 挡
    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'last_run_at': ms(d.replace(hour=8, minute=30))})
    assert xd.already_reported_today(now=d.replace(hour=8, minute=45)) is True
    # 场景4：昨天交付，今天任何班都不挡
    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'last_run_at': ms(d.replace(hour=10, minute=0)) - 86400_000})
    assert xd.already_reported_today(now=d.replace(hour=9, minute=31)) is False


def test_notification_recheck_class_lower_bound(duty_db):
    """看门复查下界=本班投递时刻：9:30 班的通知不算 13:30 班的交付（跨班串判）。"""
    import time as _t
    day0 = xd._today_start_ms()
    n0930 = day0 + 10 * 3600_000          # 今天 10:00（0930 班的日报通知）
    conn = sqlite3.connect(str(duty_db))
    conn.execute(
        "INSERT INTO notifications VALUES ('n1', 'user_7acb72ff', 'emp_xiaotuo', "
        "'task_reminder', '达人筛选专员日报 · 三班验证', 'x', 0, ?)", (n0930,))
    conn.commit()
    conn.close()
    assert xd.report_notification_exists_today(n0930 - 1000) is True    # 本班窗口内
    assert xd.report_notification_exists_today(n0930 + 1000) is False   # 下一班视角


def test_plist_three_calendar_entries():
    """排班表 3 档在位（launchd 部署工件守卫）：9:30 / 13:30 / 17:30。
    plist 在本机用户域，文件缺失则跳过（其他环境）。"""
    import plistlib
    p = Path.home() / 'Library' / 'LaunchAgents' / 'com.solobrave.xiaotuo-daily.plist'
    if not p.exists():
        pytest.skip('launchd plist 不在本机')
    with open(p, 'rb') as f:
        pl = plistlib.load(f)
    entries = pl.get('StartCalendarInterval')
    if isinstance(entries, dict):
        entries = [entries]
    got = sorted((e.get('Hour'), e.get('Minute')) for e in entries)
    assert got == [(9, 30), (13, 30), (17, 30)], f'排班表应为 3 档，实际 {got}'


# ── 8. 投递语义 P2 守卫（dq_83e3e05ee394，2026-11-20 探针实样定字段）──────
# CLI --json 终态 schema（真机探针两个会话实测）：
#   健康  = {status:'ok', summary:'completed', result:{meta:{stopReason:'stop'}}}
#   病签名 = 顶层 status 非 ok / meta.stopReason='length'（11-18 双病班）/ meta.error 非空。
# 11-18 实证盲区：terminalError turn 返 rc=0，病班 19:54 死、20:39 才告警（45min）。

def _cli_out(status='ok', stop='stop', err=None, summary='completed'):
    meta = {'stopReason': stop}
    if err:
        meta['error'] = err
    return json.dumps({'runId': 'run_x', 'status': status, 'summary': summary,
                       'result': {'meta': meta, 'payloads': []}})


def test_terminal_error_alerts_immediately_not_watchdog(tmp_path, monkeypatch, duty_db):
    """守卫①：终态 error → 立即走直判告警（不等 45min 看门）。"""
    _patch_common(monkeypatch, duty_db, _cli_out(status='error', stop='error',
                                                 err={'message': 'model fallback exhausted'},
                                                 summary='failed'))
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 99999)   # 看门永不应触发——直判必须先响
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title, content FROM notifications "
                        "WHERE agent_id='emp_xiaotuo'").fetchall()
    conn.close()
    # alert_admin 标题恒为「小拓未交日报」格式（note 进 content）——
    # 直判证据（终态异常/run_x/error）必须落在 content，title 保留未交日报标识
    assert len(rows) == 1 and '未交日报' in rows[0][0], '告警标题保留未交日报格式'
    assert '终态异常' in rows[0][1] and 'error' in rows[0][1] and 'run_x' in rows[0][1]


def test_terminal_length_truncation_is_sick(tmp_path, monkeypatch, duty_db):
    """守卫④（11-18 病班签名）：status ok 但 stopReason='length' = 截断病班 ⇒ 直判。"""
    _patch_common(monkeypatch, duty_db, _cli_out(status='ok', stop='length'))
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 99999)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title, content FROM notifications "
                        "WHERE agent_id='emp_xiaotuo'").fetchall()
    conn.close()
    assert len(rows) == 1 and '未交日报' in rows[0][0]
    assert '终态异常' in rows[0][1] and 'length' in rows[0][1]


def test_healthy_turn_keeps_watchdog_path(tmp_path, monkeypatch, duty_db):
    """守卫②：健康终态（status ok + stopReason stop）→ 不看门直判，交卷照常收工。"""
    _patch_common(monkeypatch, duty_db, _cli_out())
    future_ms = int(__import__('time').time() * 1000) + 3600_000
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', ?, '已交', 0)", (future_ms,))
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title FROM notifications "
                        "WHERE agent_id='emp_xiaotuo'").fetchall()
    conn.close()
    assert rows == [], '健康 turn 不得误报终态异常'


def test_legacy_shape_falls_back_to_watchdog(tmp_path, monkeypatch, duty_db):
    """守卫③（向后兼容锁）：旧网关形态 {'ok':true,runId} 无 status/meta
    ⇒ terminal=None 回落看门行为——不炸、不误报。"""
    _patch_common(monkeypatch, duty_db, json.dumps({'ok': True, 'runId': 'r'}))
    monkeypatch.setattr(xd, 'WATCHDOG_SEC', 0)
    conn = sqlite3.connect(str(duty_db))
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', 0, '', 0)")
    conn.commit()
    conn.close()
    assert xd.main() == 0
    conn = sqlite3.connect(str(duty_db))
    rows = conn.execute("SELECT title FROM notifications "
                        "WHERE agent_id='emp_xiaotuo'").fetchall()
    conn.close()
    assert len(rows) == 1 and '未交日报' in rows[0][0], \
        '旧形态必须回落看门告警（未交日报），不得出终态异常误报'
