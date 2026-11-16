# -*- coding: utf-8 -*-
"""熔断/验卷看门狗守卫测试（P1-⑦，2026-11-16）。

覆盖：
  1. 复合判据边界：次数腿（100/101）、占比腿（calls=30/31 × share=40%/41%）
  2. 基线七样本回放（HEALTH-BASELINE + 今晨 8ca29af4）：≥6/7（钉死 7/7）
  3. 轨迹加载双格式：JSON 事件 + Python 字面量事件（含 true/false/null）
  4. 验卷：xiaotuo report 子命令命中 / 缺失；keeper_ops 命中
  5. ps 进程定位三条件匹配（防误杀）
  6. 看门狗端到端（tmp 轨迹库 + tmp solobrave.db）：
     熔断中止 + 通知 + fuse_events、幂等（重跑不重报）、心跳只告警不中止、
     未交卷判定、已结束 run 不熔断
全部 tmp，零 prod 接触；熔断路径 monkeypatch find_cli_pids，绝不碰真进程。
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import fuse_core as fc
import run_watchdog as rw

NOW = 1_800_000_000_000  # 冻结时钟


def _mk_traj(path, events):
    """events = [(run_id, type, name, args, created_at)] → tmp 轨迹库。
    event_json 双格式覆盖：偶数条写 JSON、奇数条写 Python 字面量。"""
    db = sqlite3.connect(path)
    db.execute('DROP TABLE IF EXISTS trajectory_runtime_events')
    db.execute('CREATE TABLE trajectory_runtime_events ('
               'session_id TEXT, seq INTEGER, run_id TEXT, event_json TEXT,'
               ' created_at INTEGER)')
    for i, (rid, etype, name, args, cat) in enumerate(events):
        if etype == 'tool.call':
            d = {'type': 'tool.call', 'data': {'name': name, 'arguments': args}}
        else:
            d = {'type': etype}
        if i % 2:
            ej = json.dumps(d)                      # JSON 格式
        else:
            ej = str(d).replace('True', 'true').replace('False', 'false')
            if not ej.startswith('{'):
                ej = str(d)
            ej = ej.replace("'", '"')               # Python 字面量 → 类 JSON（ast 可解）
        db.execute('INSERT INTO trajectory_runtime_events VALUES (?,?,?,?,?)',
                   (f's_{rid[:8]}', i, rid, ej, cat))
    db.commit()
    db.close()
    return str(path)


def _tool_run(rid, n_calls, n_search=0, last_ts=NOW - 60_000, delivered=True):
    """造一个 n_calls 次调用的 run 事件列表（尾部 delivered 决定交付工具）。"""
    ev = []
    for i in range(n_calls):
        name = 'tool_search' if i < n_search else f'tool_{i}'
        ev.append((rid, 'tool.call', name, {'i': i}, last_ts - 1000 + i))
    if delivered:
        ev.append((rid, 'tool.call', 'xiaotuo_ops', {'cmd': 'report'}, last_ts))
    return ev


@pytest.fixture()
def solodb(tmp_path):
    db = sqlite3.connect(str(tmp_path / 'solobrave.db'))
    db.execute('CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT,'
               ' agent_id TEXT, type TEXT, title TEXT, content TEXT,'
               ' read INTEGER DEFAULT 0, created_at INTEGER)')
    rw.ensure_fuse_events(db)
    return db


def _notifs(db):
    return db.execute('SELECT agent_id, title FROM notifications').fetchall()


# ─── 1. 判据边界 ────────────────────────────────────────────────────────────

def test_count_leg_boundary():
    assert fc.verdict(100, 0)[0] is True
    assert fc.verdict(101, 0)[0] is False


def test_share_leg_boundary():
    # calls=30 不触发（须 >30），即使 share 超
    ok, _ = fc.verdict(30, 13)   # 43%
    assert ok is True
    ok, why = fc.verdict(31, 13)  # 42%
    assert ok is False and '占比腿' in why
    # share=40% 整不触发（须 >40%）
    ok, _ = fc.verdict(100, 40)
    assert ok is True
    ok, _ = fc.verdict(100, 41)
    assert ok is False


def test_zero_calls_safe():
    assert fc.verdict(0, 0) == (True, '放行(0次,search占比0%)')


# ─── 2. 基线七样本回放 ───────────────────────────────────────────────────────

def test_baseline_replay_seven_samples():
    bad = []
    for prefix, calls, search, want, note in fc.BASELINE_SAMPLES:
        ok, why = fc.verdict(calls, search)
        if ok != want:
            bad.append(f'{prefix}({note}) 期望{"放行" if want else "中止"} 实得{why}')
    assert not bad, f'{len(bad)} 个样本判错：{bad}'
    assert len(fc.BASELINE_SAMPLES) == 7


def test_baseline_numbers_match_live_trajectory():
    """钉死口径：基线表里小拓的四个 run 号，实测统计必须与表一致（严格口径复算）。"""
    runs = fc.load_runs('emp_xiaotuo')
    checks = {'8fd9fa77': (45, 0), '2eca23af': (109, 41),
              '3aff1c5f': (334, 147), 'cf2304f3': (51, 0),
              '2101b438': (38, 0), '8ca29af4': (36, 1)}
    for prefix, (wc, ws) in checks.items():
        rid = next((r for r in runs if r.startswith(prefix)), None)
        assert rid, f'实况库缺样本 run {prefix}'
        assert runs[rid]['calls'] == wc, f'{prefix} calls 实况{runs[rid]["calls"]}≠基线{wc}'
        assert runs[rid]['search'] == ws, f'{prefix} search 实况{runs[rid]["search"]}≠基线{ws}'


# ─── 3. 双格式事件解析 + 统计 ────────────────────────────────────────────────

def test_load_runs_dual_format(tmp_path):
    ev = _tool_run('r_dual', 5, n_search=2, delivered=False)
    p = _mk_traj(tmp_path / 'traj.db', ev)
    runs = fc.load_runs('any', db_path=p)
    assert runs['r_dual']['calls'] == 5
    assert runs['r_dual']['search'] == 2
    assert runs['r_dual']['ended'] is False


def test_load_runs_marks_session_ended(tmp_path):
    ev = _tool_run('r_end', 3, delivered=True)
    ev.append(('r_end', 'session.ended', None, None, NOW))
    p = _mk_traj(tmp_path / 'traj.db', ev)
    runs = fc.load_runs('any', db_path=p)
    assert runs['r_end']['ended'] is True


# ─── 4. 验卷 ────────────────────────────────────────────────────────────────

def test_delivery_xiaotuo_report_subcommand():
    wants = [{'name': 'xiaotuo_ops', 'arg_contains': 'report'}]
    assert fc.detect_delivery_events(wants, [('xiaotuo_ops', {'cmd': 'report'})]) is True
    assert fc.detect_delivery_events(wants, [('xiaotuo_ops', {'cmd': 'scan'})]) is False
    assert fc.detect_delivery_events(wants, [('tool_search', {})]) is False


def test_delivery_keeper_any_call():
    wants = [{'name': 'keeper_ops', 'arg_contains': None}]
    assert fc.detect_delivery_events(wants, [('keeper_ops', {'op': 'checkup'})]) is True
    assert fc.detect_delivery_events(wants, []) is False


# ─── 5. ps 定位三条件 ───────────────────────────────────────────────────────

def test_parse_openclaw_pids_three_conditions():
    ps = (
        '  101 node /opt/homebrew/bin/openclaw agent --agent emp_xiaotuo --json\n'
        '  102 node /opt/homebrew/bin/openclaw agent --agent emp_talent_keeper --json\n'
        '  103 /usr/bin/python3 something --agent emp_xiaotuo\n'          # 非 openclaw
        '  104 node /opt/homebrew/bin/openclaw gateway\n'                  # 非 agent 命令
        '  105 node /opt/homebrew/bin/openclaw agent --agent emp_xiaotuo-extra\n'
    )
    assert rw.fc.parse_openclaw_pids(ps, 'emp_xiaotuo') == [101]


# ─── 6. 看门狗端到端 ────────────────────────────────────────────────────────

def _scan(tmp_path, solodb, ev, agent='emp_probe', cfg=None, now=NOW):
    traj = _mk_traj(tmp_path / f'traj_{agent}.db', ev)
    c = cfg or {'label': '探针岗', 'delivery': [{'name': 'xiaotuo_ops',
                                                'arg_contains': 'report'}]}
    killed = []
    mp = pytest.MonkeyPatch()
    mp.setattr(rw, 'find_cli_pids', lambda aid: [424242])
    mp.setattr(rw.os, 'kill', lambda pid, sig: killed.append((pid, sig)))
    try:
        rw.scan_agent(agent, c, solodb, now, dry=False, traj_db=traj)
    finally:
        mp.undo()
    return killed


def test_fuse_aborts_active_run_and_notifies(tmp_path, solodb):
    ev = _tool_run('r_fuse', 150, delivered=False)  # 次数腿命中，无交付
    killed = _scan(tmp_path, solodb, ev)
    assert killed == [(424242, 15)], f'SIGTERM 应发出一次，实有 {killed}'
    rows = solodb.execute("SELECT kind, reason, acted FROM fuse_events"
                          " WHERE run_id='r_fuse'").fetchall()
    assert rows and rows[0][0] == 'fuse' and rows[0][2] == 1, '应记 fuse 且 acted'
    ns = _notifs(solodb)
    assert any('[熔断]' in t for _, t in ns), f'应有[熔断]通知，实有 {ns}'
    # 已熔断 run 不再重复报也不再杀
    killed2 = _scan(tmp_path, solodb, ev)
    assert killed2 == [], '已熔断 run 不得再次 SIGTERM'
    assert len(_notifs(solodb)) == 1, '同 run 熔断通知必须幂等'


def test_heartbeat_alerts_without_abort(tmp_path, solodb):
    ev = _tool_run('r_hb', 10, last_ts=NOW - 600_000, delivered=False)  # 静默 10 分钟
    _scan(tmp_path, solodb, ev, now=NOW)
    kinds = [k for k, in solodb.execute("SELECT kind FROM fuse_events WHERE run_id='r_hb'")]
    assert kinds == ['heartbeat'], f'只应有 heartbeat，实有 {kinds}'
    assert any('[心跳]' in t for _, t in _notifs(solodb))
    assert not any('[熔断]' in t for _, t in _notifs(solodb))


def test_no_delivery_recorded_after_quiet(tmp_path, solodb):
    ev = _tool_run('r_nod', 20, last_ts=NOW - 40 * 60_000, delivered=False)
    ev.append(('r_nod', 'session.ended', None, None, NOW - 40 * 60_000))
    _scan(tmp_path, solodb, ev, now=NOW)
    kinds = [k for k, in solodb.execute("SELECT kind FROM fuse_events WHERE run_id='r_nod'")]
    assert 'no_delivery' in kinds, f'已结束且无交付工具应记未交卷，实有 {kinds}'
    assert any('[未交卷]' in t for _, t in _notifs(solodb))


def test_delivered_run_not_flagged(tmp_path, solodb):
    ev = _tool_run('r_ok', 38, last_ts=NOW - 40 * 60_000, delivered=True)
    ev.append(('r_ok', 'session.ended', None, None, NOW - 40 * 60_000))
    _scan(tmp_path, solodb, ev, now=NOW)
    n = solodb.execute("SELECT COUNT(*) FROM fuse_events WHERE run_id='r_ok'").fetchone()[0]
    assert n == 0, f'正常交卷 run 不应记任何事件，实有 {n} 条'


def test_ended_run_not_fused(tmp_path, solodb):
    """已结束的 run 即使超腿也不再中止（中止无意义），交给回放/验卷。"""
    ev = _tool_run('r_dead', 200, delivered=False)
    ev.append(('r_dead', 'session.ended', None, None, NOW - 50_000))
    _scan(tmp_path, solodb, ev, now=NOW)
    fused = solodb.execute("SELECT COUNT(*) FROM fuse_events WHERE run_id='r_dead'"
                           " AND kind='fuse'").fetchone()[0]
    assert fused == 0, '已结束 run 不得熔断'
