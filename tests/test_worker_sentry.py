# -*- coding: utf-8 -*-
"""worker_sentry（buyin_worker 线程病哨兵）守卫测试（2026-11-18，协调边指令包第 3 条）。

覆盖：
  1. 签名扫描：行时间戳增量（旧行不算新轮）、双日志并集、无戳行忽略
  2. 状态机：单轮不 kick / 连续 2 轮 kick / 签名消失病轮清零
  3. 冷却：30 分钟内不重复 kick
  4. kick 副作用：维护窗 marker 落盘（until=+15min）、worker_thread_sick 立案、
     kickstart 按 §8.2 顺序（graceful-daren → kick → open-daren）
  5. daren_login_watch 维护窗抑制：窗内不立案且不改状态文件
全部 tmp，零 prod 接触；kickstart/网络/desktop 全 monkeypatch。
"""
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
import duty_notify as dn
import daren_login_watch as dlw
import worker_sentry as ws

NOW_MS = 1_800_000_000_000
YEAR = datetime.fromtimestamp(NOW_MS / 1000).year


def _ts(m, d, hh, mm, ss):
    return int(datetime(YEAR, m, d, hh, mm, ss).timestamp() * 1000)


def _sig_line(ms):
    dt = datetime.fromtimestamp(ms / 1000)
    return (f'[{dt:%m-%d %H:%M:%S}] [Control] 打开达人端浏览器失败: '
            f'It looks like you are using Playwright Sync API inside the '
            f'asyncio loop.\n')


@pytest.fixture()
def logdir(tmp_path):
    (tmp_path / 'worker.log').write_text('', encoding='utf-8')
    (tmp_path / 'launchd.out.log').write_text('', encoding='utf-8')
    return tmp_path


@pytest.fixture()
def solodb(tmp_path):
    db = sqlite3.connect(str(tmp_path / 'solobrave.db'))
    db.execute('CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT,'
               ' agent_id TEXT, type TEXT, title TEXT, content,'
               ' read INTEGER DEFAULT 0, created_at INTEGER)')
    dn.ensure_case_tables(db)
    return db


# ── 1. 签名扫描 ─────────────────────────────────────────────
def test_scan_finds_new_signature(logdir):
    (logdir / 'worker.log').write_text(_sig_line(NOW_MS - 60_000),
                                       encoding='utf-8')
    found, max_ts = ws.scan_logs(str(logdir), ws.LOG_FILES, since_ms=0, now_ms=NOW_MS)
    assert found is True
    assert max_ts == NOW_MS - 60_000


def test_scan_old_lines_not_new(logdir):
    (logdir / 'worker.log').write_text(_sig_line(NOW_MS - 3600_000),
                                       encoding='utf-8')
    found, _ = ws.scan_logs(str(logdir), ws.LOG_FILES, since_ms=NOW_MS - 600_000,
                            now_ms=NOW_MS)
    assert found is False   # 位点之前的旧签名 ≠ 新一轮病


def test_scan_union_across_two_logs(logdir):
    # 同一事件双写：两行并集只算一次新（位点推进后第二轮不再命中）
    line = _sig_line(NOW_MS - 60_000)
    (logdir / 'worker.log').write_text(line, encoding='utf-8')
    (logdir / 'launchd.out.log').write_text(line, encoding='utf-8')
    found1, max_ts = ws.scan_logs(str(logdir), ws.LOG_FILES, since_ms=0, now_ms=NOW_MS)
    found2, _ = ws.scan_logs(str(logdir), ws.LOG_FILES, since_ms=max_ts, now_ms=NOW_MS)
    assert found1 is True and found2 is False


def test_scan_missing_log_is_not_sick(tmp_path):
    found, max_ts = ws.scan_logs(str(tmp_path / 'nope'), (), since_ms=0,
                                 now_ms=NOW_MS)
    assert found is False and max_ts == 0


# ── 2-4. 主状态机 ───────────────────────────────────────────
def _wire(monkeypatch, tmp_path, solodb, logdir):
    monkeypatch.setattr(ws, 'LOG_DIR', str(logdir))
    monkeypatch.setattr(ws, 'STATE_FILE', str(tmp_path / 'ws.state'))
    monkeypatch.setattr(ws, 'KICK_MARKER', str(tmp_path / 'kick.marker'))
    monkeypatch.setattr(ws, 'DB', str(tmp_path / 'solobrave.db'))
    calls = {'kick': 0, 'post': []}
    monkeypatch.setattr(ws, 'kickstart',
                        lambda: calls.__setitem__('kick', calls['kick'] + 1)
                        or True)
    monkeypatch.setattr(ws, '_post', lambda p, timeout=10:
                        calls['post'].append(p) or True)
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    return calls


def test_single_round_no_kick(monkeypatch, tmp_path, solodb, logdir):
    calls = _wire(monkeypatch, tmp_path, solodb, logdir)
    (logdir / 'worker.log').write_text(_sig_line(NOW_MS - 60_000),
                                       encoding='utf-8')
    assert ws.main(now_ms=NOW_MS) == 0
    assert calls['kick'] == 0                      # 单次抖动不重启
    assert solodb.execute('SELECT COUNT(*) FROM duty_cases').fetchone()[0] == 0


def test_two_consecutive_rounds_kick_and_file(monkeypatch, tmp_path, solodb,
                                               logdir):
    calls = _wire(monkeypatch, tmp_path, solodb, logdir)
    t1 = _ts(11, 17, 22, 0, 0)
    t2 = _ts(11, 17, 22, 5, 0)
    (logdir / 'worker.log').write_text(_sig_line(t1), encoding='utf-8')
    ws.main(now_ms=t1)
    with open(logdir / 'worker.log', 'a', encoding='utf-8') as f:
        f.write(_sig_line(t2))
    assert ws.main(now_ms=t2) == 0
    assert calls['kick'] == 1                      # 连续 2 轮才 kick
    marker = json.load(open(tmp_path / 'kick.marker', encoding='utf-8'))
    assert marker['until_ms'] == t2 + 15 * 60 * 1000   # 维护窗 +15min
    rows = solodb.execute('SELECT root_cause FROM duty_cases').fetchall()
    assert rows == [('worker_thread_sick',)]       # 立案走通知链（可见可回溯）
    esc = solodb.execute(
        "SELECT COUNT(*) FROM notifications WHERE title LIKE '[值班升级]%'") \
        .fetchone()[0]
    assert esc == 0   # 该码首案不升级；连击升级等第二次立案（24h 幂等）


def test_signature_gone_resets_rounds(monkeypatch, tmp_path, solodb, logdir):
    _wire(monkeypatch, tmp_path, solodb, logdir)
    t1 = _ts(11, 17, 22, 0, 0)
    t2 = _ts(11, 17, 22, 5, 0)
    (logdir / 'worker.log').write_text(_sig_line(t1), encoding='utf-8')
    ws.main(now_ms=t1)                             # 1 轮
    assert ws.main(now_ms=t2) == 0                 # 无新签名 → 清零
    t3 = _ts(11, 17, 22, 10, 0)
    with open(logdir / 'worker.log', 'a', encoding='utf-8') as f:
        f.write(_sig_line(t3))
    ws.main(now_ms=t3)
    st = json.load(open(tmp_path / 'ws.state', encoding='utf-8'))
    assert st['sick_rounds'] == 1                  # 重新计轮，不累积旧账


def test_cooldown_blocks_second_kick(monkeypatch, tmp_path, solodb, logdir):
    calls = _wire(monkeypatch, tmp_path, solodb, logdir)
    t1 = _ts(11, 17, 22, 0, 0)
    t2 = _ts(11, 17, 22, 5, 0)
    t3 = _ts(11, 17, 22, 10, 0)
    t4 = _ts(11, 17, 22, 15, 0)
    for t in (t1, t2, t3, t4):
        with open(logdir / 'worker.log', 'a', encoding='utf-8') as f:
            f.write(_sig_line(t))
        ws.main(now_ms=t)
    # t2 = kick #1；t3/t4 冷却 30min 内 → 不 kick #2
    assert calls['kick'] == 1
    kicks = solodb.execute(
        "SELECT COUNT(*) FROM duty_cases WHERE root_cause="
        "'worker_thread_sick'").fetchone()[0]
    assert kicks == 1                              # 冷却期内也不重复立案

    # 冷却过后可再 kick（30min 之后）
    t5 = _ts(11, 17, 22, 50, 0)
    t6 = _ts(11, 17, 22, 55, 0)
    for t in (t5, t6):
        with open(logdir / 'worker.log', 'a', encoding='utf-8') as f:
            f.write(_sig_line(t))
        ws.main(now_ms=t)
    assert calls['kick'] == 2


# ── 5. 维护窗抑制（daren_login_watch 侧）──────────────────
def test_login_watch_suppressed_during_maintenance(monkeypatch, tmp_path,
                                                     solodb):
    monkeypatch.setattr(dlw, 'DB', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(dlw, 'STATE_FILE', str(tmp_path / 'st.json'))
    monkeypatch.setattr(dlw, 'KICK_MARKER', str(tmp_path / 'kick.marker'))
    monkeypatch.setattr(dlw, 'probe', lambda: (True, False))   # 登录态死
    monkeypatch.setattr(dn, 'desktop_push', lambda t, b='': True)
    now = int(time.time() * 1000)
    with open(tmp_path / 'kick.marker', 'w', encoding='utf-8') as f:
        json.dump({'until_ms': now + 600_000}, f)
    assert dlw.main() == 0                         # 窗内：不立案、不置死态
    assert solodb.execute('SELECT COUNT(*) FROM duty_cases').fetchone()[0] == 0
    assert not os.path.exists(tmp_path / 'st.json')   # 状态文件不动——沿保留
    # 窗过期后沿检测照常触发（不吞真实死态）
    with open(tmp_path / 'kick.marker', 'w', encoding='utf-8') as f:
        json.dump({'until_ms': now - 1}, f)
    assert dlw.main() == 1
    rows = solodb.execute('SELECT root_cause FROM duty_cases').fetchall()
    assert rows == [('need_login',)]
