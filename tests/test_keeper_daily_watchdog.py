# -*- coding: utf-8 -*-
"""派单二（2026-11-12）keeper_daily 看门狗竞态复查守卫。

与小拓闹钟同款竞态：日报恰在最后一次轮询后、告警落笔前落库 ⇒ 误报。
修复：notify_admin 落笔前 report_landed 双路复查（last_run_at + 当日日报通知）。

运行：/usr/bin/python3 -m pytest tests/test_keeper_daily_watchdog.py -v
"""
import importlib.util
import sqlite3
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    'keeper_daily', ROOT / 'keeper_daily.py')
kd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(kd)


@pytest.fixture()
def duty_db(tmp_path, monkeypatch):
    db = tmp_path / 'k.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
                    updated_at INTEGER)''')
    conn.execute('''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id TEXT,
                    type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(kd, 'DB', db)
    return db


def _patch_main(monkeypatch, duty_db, last_run_at=0, notify_spy=None):
    monkeypatch.setattr(kd, 'load_duty',
                        lambda aid: {'agent_id': aid, 'duty_name': '达人库管家',
                                     'last_run_at': last_run_at})
    monkeypatch.setattr(kd, 'deliver_alarm', lambda aid, duty: (True, False))
    monkeypatch.setattr(kd, 'watch_last_run', lambda aid, before: False)  # 看门超时
    calls = []
    monkeypatch.setattr(kd, 'notify_admin',
                        lambda aid, text: calls.append((aid, text)))
    if notify_spy is not None:
        calls.extend(notify_spy)
    return calls


def test_race_report_landed_silences_alert(duty_db, monkeypatch):
    """日报通知已落库（竞态窗口）⇒ 复查命中，告警静默。"""
    calls = _patch_main(monkeypatch, duty_db)
    conn = sqlite3.connect(str(duty_db))
    conn.execute(
        "INSERT INTO notifications VALUES ('notif_r', 'user_7acb72ff', 'emp_talent_keeper', "
        "'task_reminder', '达人库管家日报 · 11月12日', '字段缺口3', 0, ?)",
        (int(time.time() * 1000),))
    conn.commit()
    conn.close()
    monkeypatch.setattr('sys.argv', ['keeper_daily.py', '--agent', 'emp_talent_keeper'])
    kd.main()
    assert calls == [], '日报已落库 ⇒ 不许写未交日报告警'


def test_truly_missing_still_alerts(duty_db, monkeypatch):
    """反向对照：真未报（无任何日报痕迹）⇒ 告警照写。"""
    calls = _patch_main(monkeypatch, duty_db)
    monkeypatch.setattr('sys.argv', ['keeper_daily.py', '--agent', 'emp_talent_keeper'])
    kd.main()
    assert len(calls) == 1 and '未交日报' in calls[0][1] or '45 分钟' in calls[0][1]
