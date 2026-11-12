# -*- coding: utf-8 -*-
"""四层健康探针守卫（2026-11-12 派单②）。

铁律测试矩阵：
1. 模型层：有 tool.call=✅；tool.call=0=❌（k3 静默失败签名）；
   轨迹无 run=➖；run 超龄=⚠️
2. 工具层：24h 内有成功 result=✅；全部失败=❌；无 result=⚠️
3. 会话层：usage.input 超阈值=⚠️；无 usage=➖
4. 投递层：last_run_at 今日 + 今日通知有日报标识=✅；
   未交付=❌；已交付但通知通道无标识=⚠️
5. provider/modelId 必须同源（同 run 内 fallback 切换不得拼错位）
6. 全程只读：探针以 mode=ro 打开业务库
7. main：有 ❌ → 退出码 1；全过 → 0
"""
import datetime
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'agent_health_check',
    Path(__file__).resolve().parent.parent / 'scripts' / 'agent_health_check.py')
ahc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ahc)

NOW = 1794470400000  # 固定“现在”，避免测试随时间漂移
TODAY_START = ahc.day_start_ms()


def _ev(etype, ts, run_id, seq, provider='kimi_proxy_xiaotuo', model='k3',
        data=None):
    return (f'sess-{run_id[:4]}', seq, run_id, json.dumps({
        'type': etype, 'ts': ts, 'runId': run_id,
        'provider': provider, 'modelId': model,
        'data': data or {}}, ensure_ascii=False), ts)


@pytest.fixture()
def traj(tmp_path):
    """造一个最小轨迹库：k3 路由、1 个 run、2 次工具调用、usage 正常。"""
    db = tmp_path / 'openclaw-agent.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE trajectory_runtime_events (
          session_id TEXT NOT NULL, seq INTEGER NOT NULL, run_id TEXT,
          event_json TEXT NOT NULL, created_at INTEGER NOT NULL,
          PRIMARY KEY (session_id, seq));
        CREATE INDEX idx_agent_trajectory_runtime_run
          ON trajectory_runtime_events(session_id, run_id, created_at,
                                       octet_length(event_json));
    ''')
    ts = NOW - 600_000  # 10 分钟前
    rows = [
        _ev('session.started', ts - 1000, 'runk3ok01', 1),
        _ev('prompt.submitted', ts, 'runk3ok01', 2),
        _ev('tool.call', ts + 1000, 'runk3ok01', 3,
            data={'name': 'scan'}),
        _ev('tool.result', ts + 2000, 'runk3ok01', 4,
            data={'success': True}),
        _ev('tool.call', ts + 3000, 'runk3ok01', 5),
        _ev('tool.result', ts + 4000, 'runk3ok01', 6,
            data={'success': True}),
        _ev('trace.artifacts', ts + 5000, 'runk3ok01', 7,
            data={'usage': {'input': 4635, 'output': 200}}),
    ]
    con.executemany(
        'INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)', rows)
    con.commit()
    con.close()
    return db


@pytest.fixture()
def biz(tmp_path):
    """造最小业务库：emp_duties + notifications。"""
    db = tmp_path / 'solobrave.db'
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE emp_duties (
          agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '',
          last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
          duty_status TEXT DEFAULT '');
        CREATE TABLE notifications (
          id TEXT PRIMARY KEY, user_id TEXT, agent_id TEXT, type TEXT,
          title TEXT, content TEXT, read INTEGER DEFAULT 0,
          created_at INTEGER);
        CREATE TABLE credit_accounts (
          agent_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0,
          total_recharged INTEGER DEFAULT 0, total_consumed INTEGER DEFAULT 0,
          updated_at TEXT DEFAULT '');
        CREATE TABLE credit_usage_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
          credits_used INTEGER DEFAULT 0,
          created_at TEXT DEFAULT '');
    ''')
    con.commit()
    con.close()
    return db


def _mk_traj(tmp_path, name, rows):
    d = tmp_path / name / 'agent'
    d.mkdir(parents=True)
    db = d / 'openclaw-agent.sqlite'
    con = sqlite3.connect(db)
    con.executescript('''
        CREATE TABLE trajectory_runtime_events (
          session_id TEXT NOT NULL, seq INTEGER NOT NULL, run_id TEXT,
          event_json TEXT NOT NULL, created_at INTEGER NOT NULL,
          PRIMARY KEY (session_id, seq));
    ''')
    con.executemany(
        'INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)', rows)
    con.commit()
    con.close()
    return tmp_path


# ── 1. 模型层 ──

def test_model_pass_when_tool_calls_present(traj):
    con = ahc.open_ro(traj)
    status, basis = ahc.check_model(con, NOW, 30)
    con.close()
    assert status == ahc.PASS and 'tool.call=2' in basis
    assert 'kimi_proxy_xiaotuo/k3' in basis  # 路由标签如实呈现


def test_model_fail_zero_tool_calls_is_silent_failure(tmp_path):
    ts = NOW - 60_000
    rows = [_ev('session.started', ts, 'runquiet01', 1),
            _ev('prompt.submitted', ts + 1000, 'runquiet01', 2),
            _ev('model.completed', ts + 27_000, 'runquiet01', 3)]
    d = _mk_traj(tmp_path, 'a1', rows)
    con = ahc.open_ro(d / 'a1' / 'agent' / 'openclaw-agent.sqlite')
    status, basis = ahc.check_model(con, NOW, 30)
    con.close()
    assert status == ahc.FAIL and '静默失败' in basis and 'tool.call=0' in basis


def test_model_warn_when_run_stale(tmp_path):
    ts = NOW - 40 * 3_600_000  # 40 小时前
    rows = [_ev('tool.call', ts, 'runstale001', 1)]
    d = _mk_traj(tmp_path, 'a2', rows)
    con = ahc.open_ro(d / 'a2' / 'agent' / 'openclaw-agent.sqlite')
    status, _ = ahc.check_model(con, NOW, 30)
    con.close()
    assert status == ahc.WARN


def test_model_na_when_no_runs(tmp_path):
    d = _mk_traj(tmp_path, 'a3', [])
    con = ahc.open_ro(d / 'a3' / 'agent' / 'openclaw-agent.sqlite')
    status, _ = ahc.check_model(con, NOW, 30)
    con.close()
    assert status == ahc.NA


# ── 2. 工具层 ──

def test_tool_pass_with_success_results(traj):
    con = ahc.open_ro(traj)
    status, basis = ahc.check_tool(con, NOW)
    con.close()
    assert status == ahc.PASS and '成功 2 次' in basis


def test_tool_fail_when_all_failed(tmp_path):
    ts = NOW - 60_000
    rows = [_ev('tool.result', ts, 'runfail001', 1, data={'success': False}),
            _ev('tool.result', ts + 1000, 'runfail001', 2, data={'success': False})]
    d = _mk_traj(tmp_path, 'a4', rows)
    con = ahc.open_ro(d / 'a4' / 'agent' / 'openclaw-agent.sqlite')
    status, _ = ahc.check_tool(con, NOW)
    con.close()
    assert status == ahc.FAIL


def test_tool_warn_when_no_recent_results(tmp_path):
    ts = NOW - 30 * 3_600_000  # 30h 前，超 24h 窗
    rows = [_ev('tool.result', ts, 'runold00101', 1, data={'success': True})]
    d = _mk_traj(tmp_path, 'a5', rows)
    con = ahc.open_ro(d / 'a5' / 'agent' / 'openclaw-agent.sqlite')
    status, _ = ahc.check_tool(con, NOW)
    con.close()
    assert status == ahc.WARN


# ── 3. 会话层 ──

def test_session_pass_under_threshold(traj):
    con = ahc.open_ro(traj)
    status, basis = ahc.check_session(con, 200_000)
    con.close()
    assert status == ahc.PASS and '4,635' in basis


def test_session_warn_over_threshold(tmp_path):
    ts = NOW - 60_000
    rows = [_ev('trace.artifacts', ts, 'runbig00101', 1,
                data={'usage': {'input': 433_776}})]
    d = _mk_traj(tmp_path, 'a6', rows)
    con = ahc.open_ro(d / 'a6' / 'agent' / 'openclaw-agent.sqlite')
    status, basis = ahc.check_session(con, 200_000)
    con.close()
    assert status == ahc.WARN and '433,776' in basis


def test_session_na_without_usage(tmp_path):
    d = _mk_traj(tmp_path, 'a7', [_ev('tool.call', NOW - 1000, 'runx1', 1)])
    con = ahc.open_ro(d / 'a7' / 'agent' / 'openclaw-agent.sqlite')
    status, _ = ahc.check_session(con, 200_000)
    con.close()
    assert status == ahc.NA


# ── 4. 投递层 ──

def _seed_duty(con, aid, last_run_at, summary='今日深看5'):
    con.execute(
        "INSERT INTO emp_duties (agent_id, duty_name, last_run_at,"
        " last_run_summary) VALUES (?, '专员', ?, ?)",
        (aid, last_run_at, summary))


def test_delivery_pass_when_delivered_and_notified(biz):
    con = sqlite3.connect(biz)
    _seed_duty(con, 'emp_a', TODAY_START + 1000)
    con.execute("INSERT INTO notifications (id, user_id, agent_id, title,"
                " created_at) VALUES ('n1', 'admin', '', '小拓今日深看5', ?)",
                (TODAY_START + 2000,))
    con.commit()
    status, basis = ahc.check_delivery(con, 'emp_a', TODAY_START)
    con.close()
    assert status == ahc.PASS and '日报通知 1 条' in basis


def test_delivery_fail_when_not_delivered(biz):
    con = sqlite3.connect(biz)
    _seed_duty(con, 'emp_a', TODAY_START - 86_400_000)  # 昨天
    con.commit()
    status, _ = ahc.check_delivery(con, 'emp_a', TODAY_START)
    con.close()
    assert status == ahc.FAIL


def test_delivery_warn_when_delivered_but_no_notification(biz):
    con = sqlite3.connect(biz)
    _seed_duty(con, 'emp_a', TODAY_START + 1000)
    con.commit()
    status, basis = ahc.check_delivery(con, 'emp_a', TODAY_START)
    con.close()
    assert status == ahc.WARN and '投递到别处' in basis


def test_delivery_na_unknown_agent(biz):
    con = sqlite3.connect(biz)
    status, _ = ahc.check_delivery(con, 'emp_ghost', TODAY_START)
    con.close()
    assert status == ahc.NA


# ── 5. provider/modelId 同源（fallback 切换不得拼错位） ──

def test_route_label_from_same_event_not_max(tmp_path):
    """run 内先 k3 后 fallback 到 minimax：标签必须是最后实际服务方，
    不得出现 MAX() 拼出来的 'minimax/k3' 错位。"""
    ts = NOW - 60_000
    rows = [
        _ev('prompt.submitted', ts, 'runfb00001', 1,
            provider='kimi_proxy_goods_keeper', model='k3'),
        _ev('prompt.submitted', ts + 1000, 'runfb00001', 2,
            provider='minimax', model='MiniMax-M3'),
        _ev('tool.call', ts + 2000, 'runfb00001', 3,
            provider='minimax', model='MiniMax-M3'),
    ]
    d = _mk_traj(tmp_path, 'a8', rows)
    con = ahc.open_ro(d / 'a8' / 'agent' / 'openclaw-agent.sqlite')
    status, basis = ahc.check_model(con, NOW, 30)
    con.close()
    assert status == ahc.PASS
    assert 'minimax/MiniMax-M3' in basis and 'minimax/k3' not in basis


# ── 6. 只读 ──

def test_opens_business_db_readonly(biz):
    con = ahc.open_ro(biz)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("UPDATE emp_duties SET last_run_at=0")
    con.close()


# ── 7. 端到端 + 退出码 ──

def _seed_full(tmp_path, aid, zero_tools=False, delivered=True):
    tdir = tmp_path / 'traj'
    ts = NOW - 600_000
    if zero_tools:
        rows = [_ev('prompt.submitted', ts, 'runquiet02', 1),
                _ev('model.completed', ts + 27_000, 'runquiet02', 2)]
    else:
        rows = [_ev('tool.call', ts, 'runok00001', 1),
                _ev('tool.result', ts + 1000, 'runok00001', 2,
                    data={'success': True}),
                _ev('trace.artifacts', ts + 2000, 'runok00001', 3,
                    data={'usage': {'input': 1000}})]
    _mk_traj(tdir, aid, rows)
    biz = tmp_path / 'solobrave.db'
    con = sqlite3.connect(biz)
    con.executescript('''
        CREATE TABLE emp_duties (
          agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '',
          last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
          duty_status TEXT DEFAULT '');
        CREATE TABLE notifications (
          id TEXT PRIMARY KEY, user_id TEXT, agent_id TEXT, type TEXT,
          title TEXT, content TEXT, read INTEGER DEFAULT 0,
          created_at INTEGER);
        CREATE TABLE credit_accounts (
          agent_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0,
          total_recharged INTEGER DEFAULT 0, total_consumed INTEGER DEFAULT 0,
          updated_at TEXT DEFAULT '');
        CREATE TABLE credit_usage_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
          credits_used INTEGER DEFAULT 0,
          created_at TEXT DEFAULT '');
    ''')
    _seed_duty(con, aid, TODAY_START + 1000 if delivered else 0)
    con.execute("INSERT INTO notifications (id, title, created_at)"
                " VALUES ('n1', '日报', ?)", (TODAY_START + 2000,))
    con.commit()
    con.close()
    return biz, tdir


def test_end_to_end_exit_codes(tmp_path, monkeypatch):
    import datetime as _dt
    # 固定“今天”让 day_start 对齐 NOW
    fake_today = _dt.datetime.fromtimestamp(NOW / 1000).date()
    monkeypatch.setattr(ahc.datetime, 'date',
                        type('D', (), {'today': staticmethod(lambda: fake_today)}))
    monkeypatch.setattr(ahc.datetime, 'datetime', _dt.datetime)
    empty_launch = tmp_path / 'launch'   # 空目录 = 被动型，投递层豁免
    empty_launch.mkdir()
    biz, tdir = _seed_full(tmp_path, 'emp_ok')
    assert ahc.main(['--db', str(biz), '--traj-dir', str(tdir),
                     '--launch-agents-dir', str(empty_launch),
                     '--agent', 'emp_ok']) == 0
    biz2 = tmp_path / 'solobrave2.db'
    tdir2 = tmp_path / 'traj2'
    import shutil
    shutil.copy(biz, biz2)
    shutil.copytree(tdir, tdir2)
    con = sqlite3.connect(biz2)
    con.execute("UPDATE emp_duties SET last_run_at=0")
    con.commit()
    con.close()
    assert ahc.main(['--db', str(biz2), '--traj-dir', str(tdir2),
                     '--launch-agents-dir', str(empty_launch),
                     '--agent', 'emp_ok']) == 0  # 被动型豁免投递层


def test_end_to_end_scheduled_agent_delivery_fail(tmp_path, monkeypatch):
    """主动型（plist 里 --agent 指向它）投递层真查：last_run_at 空 → ❌ 退出码 1。"""
    import datetime as _dt
    fake_today = _dt.datetime.fromtimestamp(NOW / 1000).date()
    monkeypatch.setattr(ahc.datetime, 'date',
                        type('D', (), {'today': staticmethod(lambda: fake_today)}))
    monkeypatch.setattr(ahc.datetime, 'datetime', _dt.datetime)
    launch = tmp_path / 'launch2'
    launch.mkdir()
    import plistlib
    with open(launch / 'com.solobrave.fake.plist', 'wb') as f:
        plistlib.dump({'Label': 'com.solobrave.fake',
                       'ProgramArguments': ['/usr/bin/python3',
                                            '/x/keeper_daily.py',
                                            '--agent', 'emp_ok']}, f)
    biz, tdir = _seed_full(tmp_path, 'emp_ok', delivered=False)
    assert ahc.main(['--db', str(biz), '--traj-dir', str(tdir),
                     '--launch-agents-dir', str(launch),
                     '--agent', 'emp_ok']) == 1


def test_render_table_marks(tmp_path, monkeypatch):
    import datetime as _dt
    fake_today = _dt.datetime.fromtimestamp(NOW / 1000).date()
    monkeypatch.setattr(ahc.datetime, 'date',
                        type('D', (), {'today': staticmethod(lambda: fake_today)}))
    empty_launch = tmp_path / 'launch3'
    empty_launch.mkdir()
    biz, tdir = _seed_full(tmp_path, 'emp_ok')
    out = ahc.render_table(ahc.run_checks(
        str(biz), str(tdir), ['emp_ok'], now_ms=NOW,
        launch_agents_dir=str(empty_launch)))
    assert 'emp_ok' in out and '✅' in out and '被动' in out


# ── 8. 主动型判定（两种 plist 形态） ──

def test_scheduled_agents_both_forms(tmp_path):
    import plistlib
    launch = tmp_path / 'LaunchAgents'
    launch.mkdir()
    # 形态①：--agent 参数
    with open(launch / 'com.solobrave.keeper.plist', 'wb') as f:
        plistlib.dump({'ProgramArguments': ['/usr/bin/python3',
                                            '/x/keeper_daily.py',
                                            '--agent', 'emp_talent_keeper']}, f)
    # 形态②：日报脚本无参，AGENT_ID 硬编码
    script = tmp_path / 'xiaotuo_daily.py'
    script.write_text("AGENT_ID = 'emp_xiaotuo'\n", encoding='utf-8')
    with open(launch / 'com.solobrave.xiaotuo.plist', 'wb') as f:
        plistlib.dump({'ProgramArguments': ['/usr/bin/python3', str(script)]}, f)
    sched = ahc.scheduled_agents(str(launch))
    assert 'emp_talent_keeper' in sched and 'emp_xiaotuo' in sched


def test_scheduled_agents_empty_dir(tmp_path):
    launch = tmp_path / 'empty-launch'
    launch.mkdir()
    assert ahc.scheduled_agents(str(launch)) == {}


# ── 9. 摘岗标（designing）不进在岗名单 ──

def test_designing_excluded_from_active_roster(tmp_path):
    biz = tmp_path / 'solobrave.db'
    con = sqlite3.connect(biz)
    con.executescript('''
        CREATE TABLE emp_duties (
          agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '',
          last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
          duty_status TEXT DEFAULT '');
        CREATE TABLE notifications (
          id TEXT PRIMARY KEY, user_id TEXT, agent_id TEXT, type TEXT,
          title TEXT, content TEXT, read INTEGER DEFAULT 0,
          created_at INTEGER);
        CREATE TABLE credit_accounts (
          agent_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0,
          total_recharged INTEGER DEFAULT 0, total_consumed INTEGER DEFAULT 0,
          updated_at TEXT DEFAULT '');
        CREATE TABLE credit_usage_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
          credits_used INTEGER DEFAULT 0,
          created_at TEXT DEFAULT '');
    ''')
    con.execute("INSERT INTO emp_duties VALUES ('emp_active', '管家', 0, '', '')")
    con.execute("INSERT INTO emp_duties VALUES ('knowledge_admin', '知识库管家',"
                " 0, '', 'designing')")
    con.commit()
    con.close()
    launch = tmp_path / 'launch'
    launch.mkdir()
    res = ahc.run_checks(str(biz), str(tmp_path / 'no-traj'),
                         launch_agents_dir=str(launch))
    ids = [r['agent_id'] for r in res]
    assert ids == ['emp_active']  # designing 被摘岗，不进探针名单


# ── 10. 资源层（余额 < 2×近 7 日日均消耗 → ⚠️） ──

def _seed_credits(con, aid, balance, used_rows=()):
    con.execute("INSERT INTO credit_accounts (agent_id, balance)"
                " VALUES (?, ?)", (aid, balance))
    for created_at, used in used_rows:
        con.execute("INSERT INTO credit_usage_log (agent_id, credits_used,"
                    " created_at) VALUES (?, ?, ?)", (aid, used, created_at))


def _recent(days_ago, used):
    d = datetime.datetime.fromtimestamp(NOW / 1000) - datetime.timedelta(days=days_ago)
    return (d.strftime('%Y-%m-%d %H:%M:%S'), used)


def test_credits_warn_below_two_days(biz):
    con = sqlite3.connect(biz)
    # 近 7 日耗 700（日均 100）→ 2×日均=200；余额 150 → ⚠️
    _seed_credits(con, 'emp_a', 150, [_recent(i, 100) for i in range(7)])
    con.commit()
    status, basis = ahc.check_credits(con, 'emp_a', NOW)
    con.close()
    assert status == ahc.WARN and '150' in basis


def test_credits_pass_adequate(biz):
    con = sqlite3.connect(biz)
    _seed_credits(con, 'emp_a', 2092, [_recent(i, 58 * 7 // 7) for i in range(7)])
    con.commit()
    status, basis = ahc.check_credits(con, 'emp_a', NOW)
    con.close()
    assert status == ahc.PASS and '2092' in basis


def test_credits_warn_zero_balance_no_usage(biz):
    """无消耗记录且余额 0 = 上岗即欠费（knowledge_admin 上岗检查单同款）。"""
    con = sqlite3.connect(biz)
    _seed_credits(con, 'emp_a', 0)
    con.commit()
    status, basis = ahc.check_credits(con, 'emp_a', NOW)
    con.close()
    assert status == ahc.WARN and '上岗即欠费' in basis


def test_credits_na_unknown_agent(biz):
    con = sqlite3.connect(biz)
    status, _ = ahc.check_credits(con, 'emp_ghost', NOW)
    con.close()
    assert status == ahc.NA
