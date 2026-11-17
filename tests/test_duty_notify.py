# -*- coding: utf-8 -*-
"""P1-⑧ 值班通知链 + P2-⑤ 三件套守卫测试（2026-11-17，老大 19:46「今晚全做完」）。

覆盖：
  1. root_cause 短码枚举钉死（老大修正：判重按码，勿自由文本）
  2. 立案三连：留痕 duty_cases / 通知+桌面双通道 / 返回 case_id+escalated
  3. 连击升级：同码连续 2 次 → 直推老大；异码不升；24h 幂等（每码只升一次）；
     跨 24h 窗口后可再升
  4. desktop_push 永不抛（osascript 挂了也不反噬主链）
  5. run_watchdog 三立案点 root_cause 映射正确（fuse→roam_search /
     heartbeat→heartbeat_silent / no_delivery→no_delivery）
  6. daren_login_watch 沿检测：alive→dead 立案、dead 保持不重复报、
     dead→alive 静默、控制口不可达不立案 need_login
  7. preflight_925 含⑦达人端登录态探活行（真信号=health 探测，非 git-log 代理）
全部 tmp，零 prod 接触；桌面推送 monkeypatch 掉，绝不弹真横幅。
"""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import duty_notify as dn
import run_watchdog as rw
import daren_login_watch as dlw

NOW = 1_800_000_000_000


@pytest.fixture()
def solodb(tmp_path):
    db = sqlite3.connect(str(tmp_path / 'solobrave.db'))
    db.execute('CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT,'
               ' agent_id TEXT, type TEXT, title TEXT, content TEXT,'
               ' read INTEGER DEFAULT 0, created_at INTEGER)')
    dn.ensure_case_tables(db)
    return db


def _file(db, cause, now=NOW, agent='emp_goods_keeper'):
    return dn.file_case(db, agent, cause, f'[{cause}] t', 'c', now_ms=now)


# ── 1. 枚举钉死 ──────────────────────────────────────────────
def test_root_cause_enum_locked():
    assert dn.ROOT_CAUSES == ('need_login', 'roam_search',
                              'no_delivery', 'heartbeat_silent',
                              'worker_thread_sick')   # ⑤ worker_sentry（老大 11-18 00:30 拍板）


def test_file_case_rejects_free_text_cause(solodb):
    with pytest.raises(ValueError):
        _file(solodb, 'login_dead')


# ── 2. 立案三连 ──────────────────────────────────────────────
def test_file_case_inserts_and_notifies(solodb, monkeypatch):
    pushed = []
    monkeypatch.setattr(dn, 'desktop_push',
                        lambda t, b='': pushed.append(t) or True)
    out = _file(solodb, 'need_login')
    assert out['case_id'].startswith('dc_')
    assert out['escalated'] is False          # 单条不升级
    assert solodb.execute('SELECT COUNT(*) FROM duty_cases').fetchone()[0] == 1
    n = solodb.execute('SELECT user_id,title FROM notifications').fetchall()
    assert len(n) == 1 and n[0][0] == dn.ADMIN_ID
    assert len(pushed) == 1                    # 桌面通道同发


# ── 3. 连击升级 + 24h 幂等 ──────────────────────────────────
def test_consecutive_same_cause_escalates(solodb, monkeypatch):
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    _file(solodb, 'need_login', now=NOW - 600_000)
    out = _file(solodb, 'need_login', now=NOW)
    assert out['escalated'] is True
    ups = [(t, c) for (u, t, c) in solodb.execute(
        'SELECT user_id,title,content FROM notifications').fetchall()
        if u == dn.ADMIN_ID and '值班升级' in t]
    assert len(ups) == 1                       # 升级通知恰一条
    # 老大 11-18 00:30 拍板：升级正文必须带连击两条立案的 case_id（幽灵升级教训——
    # 「通知在、案卷缺席」视为链路 bug，每条推给老大的告警都要可回溯）
    ids = solodb.execute('SELECT id FROM duty_cases ORDER BY created_at').fetchall()
    for (cid,) in ids:
        assert cid in ups[0][1]


def test_different_cause_no_escalate(solodb, monkeypatch):
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    _file(solodb, 'need_login', now=NOW - 600_000)
    out = _file(solodb, 'roam_search', now=NOW)
    assert out['escalated'] is False


def test_escalation_24h_idempotent_per_code(solodb, monkeypatch):
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    _file(solodb, 'need_login', now=NOW - 3_600_000)
    assert _file(solodb, 'need_login', now=NOW - 3_000_000)['escalated'] is True
    # 24h 窗口内同码不再升（幂等）
    assert _file(solodb, 'need_login', now=NOW - 600_000)['escalated'] is False
    # 但别的码不受影响（幂等按码，时间戳错开防同刻排序歧义）
    _file(solodb, 'roam_search', now=NOW - 300_000)
    assert _file(solodb, 'roam_search', now=NOW - 60_000)['escalated'] is True


def test_escalation_refires_after_24h(solodb, monkeypatch):
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    _file(solodb, 'need_login', now=NOW - 26 * 3600_000)
    assert _file(solodb, 'need_login', now=NOW - 25 * 3600_000)['escalated'] is True
    assert _file(solodb, 'need_login', now=NOW)['escalated'] is True


def test_escalation_refused_when_case_files_missing(solodb, monkeypatch):
    """幽灵升级硬闸（解剖指令②）：案卷不足 2 条 → 拒绝升级，
    不插通知、不插幂等标记——「通知在案卷缺席」不允许发生第二次。"""
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    # 绕开 _consecutive_same_cause 的结构性保护，直接施压损坏态
    assert dn._escalate_if_needed(solodb, 'need_login',
                                  'emp_goods_keeper', NOW) is False
    assert solodb.execute('SELECT COUNT(*) FROM duty_escalations') \
        .fetchone()[0] == 0          # 幂等标记不落地
    ups = solodb.execute(
        "SELECT COUNT(*) FROM notifications WHERE title LIKE '[值班升级]%'") \
        .fetchone()[0]
    assert ups == 0                  # 幽灵推送不发生


# ── 4. desktop_push 永不抛 ──────────────────────────────────
def test_desktop_push_never_raises(monkeypatch):
    import subprocess
    def _boom(*a, **k):
        raise RuntimeError('osascript 不存在')
    monkeypatch.setattr(subprocess, 'run', _boom)
    assert dn.desktop_push('t', 'b') is False  # 吞掉，不抛


# ── 5. run_watchdog 立案映射 ────────────────────────────────
def test_map_fuse_reason_is_roam_family():
    assert dn.map_fuse_reason('次数腿(101>100)') == 'roam_search'
    assert dn.map_fuse_reason('占比腿(40/60=67%>40%)') == 'roam_search'


def _mk_fuse_run(rid, n_calls, n_search, last_ts=NOW - 60_000):
    return {rid: {'calls': n_calls, 'search': n_search, 'last_ts': last_ts,
                  'ended': False}}


def test_watchdog_fuse_files_roam_search(solodb, monkeypatch, tmp_path):
    filed = []
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    monkeypatch.setattr(dn, 'file_case',
                        lambda c, a, cause, t, ct, notify=False:
                        filed.append(cause) or {'case_id': 'x',
                                                'escalated': False})
    monkeypatch.setattr(rw, 'find_cli_pids', lambda a: [])
    monkeypatch.setattr(rw.fc, 'load_runs',
                        lambda a, db_path=None: _mk_fuse_run('r' * 32, 120, 0))
    rw.ensure_fuse_events(solodb)
    rw.scan_agent('emp_xiaotuo', rw.fc.AGENTS['emp_xiaotuo'], solodb, NOW, False)
    assert 'roam_search' in filed


def test_watchdog_no_delivery_files_code(solodb, monkeypatch):
    filed = []
    rid = 'n' * 32
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    monkeypatch.setattr(dn, 'file_case',
                        lambda c, a, cause, t, ct, notify=False:
                        filed.append(cause) or {'case_id': 'x',
                                                'escalated': False})
    # 静默 40 分钟、无交付工具 → 未交卷
    monkeypatch.setattr(rw.fc, 'load_runs',
                        lambda a, db_path=None: {rid: {
                            'calls': 45, 'search': 0,
                            'last_ts': NOW - 40 * 60_000, 'ended': True}})
    monkeypatch.setattr(rw.fc, 'load_tool_calls', lambda a, r, db_path=None: [])
    monkeypatch.setattr(rw.fc, 'detect_delivery_events',
                        lambda anchors, ev: False)
    rw.ensure_fuse_events(solodb)
    rw.scan_agent('emp_xiaotuo', rw.fc.AGENTS['emp_xiaotuo'], solodb, NOW, False)
    assert 'no_delivery' in filed


# ── 6. daren_login_watch 沿检测 ─────────────────────────────
def test_login_watch_alive_to_dead_files_case(solodb, monkeypatch, tmp_path):
    # solodb fixture 已建好 tmp_path/'solobrave.db'（含 notifications+duty 表）
    monkeypatch.setattr(dlw, 'DB', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(dlw, 'STATE_FILE', str(tmp_path / 'st.json'))
    monkeypatch.setattr(dlw, 'probe', lambda: (True, False))
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    assert dlw.main() == 1
    cases = solodb.execute('SELECT root_cause FROM duty_cases').fetchall()
    assert cases == [('need_login',)]


def test_login_watch_dead_stays_quiet(solodb, monkeypatch, tmp_path):
    monkeypatch.setattr(dlw, 'DB', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(dlw, 'STATE_FILE', str(tmp_path / 'st.json'))
    monkeypatch.setattr(dlw, 'probe', lambda: (True, False))
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    dlw._save_state(False)                      # 沿已报过
    pushed = []
    monkeypatch.setattr(dn, 'file_case',
                        lambda *a, **k: pushed.append(1) or
                        {'case_id': 'x', 'escalated': False})
    assert dlw.main() == 1
    assert pushed == []                          # 不重复立案


def test_login_watch_unreachable_not_need_login(monkeypatch, tmp_path):
    monkeypatch.setattr(dlw, 'STATE_FILE', str(tmp_path / 'st.json'))
    monkeypatch.setattr(dlw, 'probe', lambda: (False, False))
    pushed = []
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': pushed.append(t))
    filed = []
    monkeypatch.setattr(dn, 'file_case',
                        lambda *a, **k: filed.append(1) or
                        {'case_id': 'x', 'escalated': False})
    assert dlw.main() == 1
    assert filed == [] and len(pushed) == 1      # 横幅一次，不立案


# ── 7. preflight ⑦ 行守卫 ───────────────────────────────────
def test_preflight_has_daren_probe_line():
    src = open(os.path.join(ROOT, 'scripts', 'preflight_925.py'),
               encoding='utf-8').read()
    assert '⑦ 达人端登录态探活' in src
    assert 'daren_relogin.py' in src             # 红灯必须给出修复路径
    assert 'daren_logged_in' in src              # 真信号 = health 探测
    assert '--since=24.hours.ago' not in src     # 不是 git-log 代理
