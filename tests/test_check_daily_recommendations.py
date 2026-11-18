# -*- coding: utf-8 -*-
"""check_daily_recommendations 守卫（班次加密 + P2 小修后，2026-11-18）。

四象限：正例（两班三 run 全对拍）/ 旧模板段（检查1 必红）/ run_id 空（2a 红）/
 单 run 越界（2b 红）/ 段库不一致（2c 红）。
验收脚本本身不许写库（只 SELECT）——临时库用完即弃。
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'check_daily_recommendations.py'

SCHEMA_NOTIF = '''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id,
 type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)'''
SCHEMA_REC = '''CREATE TABLE talent_recommendations (
 rec_id TEXT PRIMARY KEY, talent_id TEXT, talent_name TEXT,
 rec_date TEXT, run_id TEXT, matched_rules TEXT, reason_text TEXT,
 pipeline_status TEXT DEFAULT 'recommended', snapshot_json TEXT DEFAULT '')'''

DAY0 = 1795000000000  # 固定毫秒锚（2026-11-18 附近），day_range 用 date 参数对齐


def _mk_db(tmp_path, notifs, recs, date='2026-11-18'):
    db = tmp_path / 'chk.db'
    conn = sqlite3.connect(str(db))
    conn.execute(SCHEMA_NOTIF)
    conn.execute(SCHEMA_REC)
    for i, (title, content, off) in enumerate(notifs):
        conn.execute('INSERT INTO notifications VALUES (?,?,?,?,?,?,0,?)',
                     (f'n{i}', 'user_7acb72ff', 'emp_xiaotuo',
                      'task_reminder', title, content, DAY0 + off))
    for i, (run_id, mr, rt) in enumerate(recs):
        conn.execute('INSERT INTO talent_recommendations '
                     '(rec_id, talent_id, rec_date, run_id, matched_rules, '
                     ' reason_text) VALUES (?,?,?,?,?,?)',
                     (f'r{i}', f'tal_{i}', date, run_id, mr, rt))
    conn.commit()
    conn.close()
    return db


def _run(db, date='2026-11-18'):
    r = subprocess.run([sys.executable, str(SCRIPT), '--db', str(db),
                        '--date', date],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout


CANON = ('【每日推荐】\n1. 甲（123456）：1万粉\n2. 乙（234567）：2万粉\n'
         '- 存疑 1：丙（345678）\n风控：零 URL 重放。')
LEGACY = ('- 值得联系 2：甲（123456）、乙（234567）\n- 存疑 0\n风控：零 URL 重放。')
MR = '["rule_a"]'


def test_two_classes_all_match(tmp_path):
    """正例：两班（runA 2 人 / runB 3 人），canonical 段 + B 表全对拍 ⇒ 全过。"""
    notifs = [
        ('达人筛选专员日报', f'【广场班A】run=runA\n逐人判定：\n{CANON}', 1000),
        ('达人筛选专员日报', f'【广场班B】run=runB\n逐人判定：\n'
         '【每日推荐】\n1. 丙（345678）：3万粉\n2. 丁（456789）：4万粉\n'
         '3. 戊（567890）：5万粉\n风控：零 URL 重放。', 2000),
    ]
    recs = [('runA', MR, '结算 1万'), ('runA', MR, '结算 2万'),
            ('runB', MR, '结算 3万'), ('runB', MR, '结算 4万'),
            ('runB', MR, '结算 5万')]
    rc, out = _run(_mk_db(tmp_path, notifs, recs))
    assert rc == 0, out
    assert '检查2c 对拍 2 班' in out


def test_legacy_section_rejected(tmp_path):
    """旧模板「值得联系」段不算 canonical ⇒ 检查1 必红（13:09 格式偏差锁形态）。"""
    notifs = [('达人筛选专员日报', f'【广场班A】run=runA\n逐人判定：\n{LEGACY}', 1000)]
    rc, out = _run(_mk_db(tmp_path, notifs, [('runA', MR, '结算 1万')]))
    assert rc == 1 and '无一封含 canonical' in out


def test_null_run_id_flagged(tmp_path):
    """run_id 空行 ⇒ 检查2a 红（P2-④ ③回填锁形态）。"""
    notifs = [('达人筛选专员日报', f'【广场班A】run=runA\n{CANON}', 1000)]
    recs = [('runA', MR, '结算 1万'), ('', MR, '结算 2万')]
    rc, out = _run(_mk_db(tmp_path, notifs, recs))
    assert rc == 1 and '检查2a' in out


def test_single_run_over_cap(tmp_path):
    """单 run 6 行（>5 落库裁剪上限）⇒ 检查2b 红（防一班凑数）。"""
    notifs = [('达人筛选专员日报', f'【广场班A】run=runA\n{CANON}', 1000)]
    recs = [('runA', MR, f'结算 {i}万') for i in range(6)]
    rc, out = _run(_mk_db(tmp_path, notifs, recs))
    assert rc == 1 and '检查2b' in out


def test_section_db_mismatch(tmp_path):
    """段 2 人 vs 库 1 行 ⇒ 检查2c 红。"""
    notifs = [('达人筛选专员日报', f'【广场班A】run=runA\n{CANON}', 1000)]
    rc, out = _run(_mk_db(tmp_path, notifs, [('runA', MR, '结算 1万')]))
    assert rc == 1 and '检查2c' in out


def test_zero_candidate_day_honest_red(tmp_path):
    """零候选日（无段无行）如实报红——P2-④ 披露的「宁缺毋滥」代价，锁形态。"""
    rc, out = _run(_mk_db(tmp_path, [], []))
    assert rc == 1 and '无一封含 canonical' in out
