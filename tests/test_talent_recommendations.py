# -*- coding: utf-8 -*-
"""③号单（dq_aa7f03662de9，2026-11-15）：talent_recommendations B 表守卫。

规格锚：交接单 §143 + check_daily_recommendations.py 四条验收。
  1. DDL：UNIQUE(rec_date, talent_id) = 同日去重；rec_date = 当日 00:00 ms
  2. 归一化：钉 5 人上限 / 缺 talent_id 跳过 / matched_rules 非数组⇒'[]'（不编造）
     / snapshot 非 dict⇒None
  3. 写入：与日报同事务；只认 SQUARE_DUTY_AGENT；同日重投幂等（行数不增、值覆盖）
"""
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location('buyin_worker', _ROOT / 'buyin_worker.py')
bw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bw)

NOW_MS = 1762992000000  # 2025-11-13 00:00:00 +08:00 附近的固定锚（随意但稳定）
# 用真实 now 也可以：归一化用 datetime.fromtimestamp 本地时区，测试不依赖具体值
import time
REAL_NOW_MS = int(time.time() * 1000)

DDL_RE = re.compile(
    r"CREATE TABLE IF NOT EXISTS talent_recommendations\s*\(.*?\)\s*'''",
    re.S)


def test_ddl_shape_in_server_source():
    src = (_ROOT / 'solobrave-server.py').read_text(encoding='utf-8')
    m = re.search(
        r"CREATE TABLE IF NOT EXISTS talent_recommendations.*?\n\s*'''",
        src, re.S)
    assert m, 'server 初始化必须含 talent_recommendations DDL'
    ddl = m.group(0)
    assert 'UNIQUE(rec_date, talent_id)' in ddl, '同日去重约束缺失'
    for col in ('rec_id', 'rec_date', 'talent_id', 'matched_rules',
                'reason_text', 'snapshot_json', 'pipeline_status'):
        assert col in ddl, f'缺列 {col}'


def test_normalize_caps_at_five_and_skips_junk():
    recs = [{'talent_id': f't{i}', 'reason_text': f'理由{i}'}
            for i in range(7)]
    rows = bw._square_normalize_recs(recs, REAL_NOW_MS)
    assert len(rows) == 5, '规格钉死 5 人，多退'


def test_normalize_never_fabricates_rules():
    recs = [
        {'talent_id': 't1', 'matched_rules': ['gmv_1000_plus'], 'reason_text': 'r'},
        {'talent_id': 't2', 'matched_rules': 'not-a-list', 'reason_text': 'r'},
        {'talent_id': 't3', 'reason_text': 'r'},                     # 缺 rules 字段
        {'talent_id': 't4', 'matched_rules': [], 'reason_text': 'r'},
        {'talent_id': 't5', 'matched_rules': None, 'reason_text': 'r'},
    ]
    rows = bw._square_normalize_recs(recs, REAL_NOW_MS)
    assert [r[4] for r in rows] == [
        json.dumps(['gmv_1000_plus'], ensure_ascii=False), '[]', '[]', '[]', '[]']


def test_normalize_snapshot_only_dict():
    rows = bw._square_normalize_recs(
        [{'talent_id': 't1', 'snapshot': {'gmv': '1k-5k'}},
         {'talent_id': 't2', 'snapshot': '全表快照字符串'}], REAL_NOW_MS)
    assert json.loads(rows[0][6]) == {'gmv': '1k-5k'}
    assert rows[1][6] is None, 'snapshot 非 dict 必须落 NULL（勿全表快照）'


def test_normalize_rec_id_and_day_start():
    rows = bw._square_normalize_recs([{'talent_id': 'tal_abc'}], REAL_NOW_MS)
    rid, day_start, tid = rows[0][0], rows[0][1], rows[0][2]
    assert tid == 'tal_abc'
    assert rid.startswith('rec_') and rid.endswith('_tal_abc')
    from datetime import datetime
    expect = int(datetime.combine(
        datetime.fromtimestamp(REAL_NOW_MS / 1000).date(),
        datetime.min.time()).timestamp() * 1000)
    assert day_start == expect, 'rec_date 必须当日 00:00 ms（check 脚本按日范围查）'


@pytest.fixture()
def rec_db(tmp_path, monkeypatch):
    db = tmp_path / 't.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    supervisor_agent_id TEXT DEFAULT '', zero_streak INTEGER DEFAULT 0,
                    duty_name TEXT DEFAULT '员工', last_run_at INTEGER DEFAULT 0,
                    last_run_summary TEXT DEFAULT '', updated_at INTEGER)''')
    conn.execute('''CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT,
                    agent_id TEXT, type TEXT, title TEXT, content TEXT,
                    read INTEGER DEFAULT 0, created_at INTEGER)''')
    conn.execute('''CREATE TABLE talent_recommendations (
                    rec_id TEXT PRIMARY KEY, rec_date INTEGER NOT NULL,
                    talent_id TEXT NOT NULL, talent_name TEXT NOT NULL DEFAULT '',
                    matched_rules TEXT NOT NULL DEFAULT '[]',
                    reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
                    pipeline_status TEXT NOT NULL DEFAULT 'pending',
                    deal_pid TEXT, gmv REAL, refund_flag INTEGER,
                    run_id TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
                    UNIQUE(rec_date, talent_id))''')
    conn.execute("INSERT INTO emp_duties (agent_id) VALUES ('emp_xiaotuo')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    monkeypatch.setattr(bw, '_square_deliver_to_agent', lambda *a, **k: None)
    return db


RECS = [{'talent_id': f'tal_{i}', 'talent_name': f'达人{i}',
         'matched_rules': ['gmv_band'], 'reason_text': f'近30天结算1{i}00元'}
        for i in range(5)]


def test_write_report_persists_recs_same_tx(rec_db):
    bw._square_write_report('今日深看5 | 值得联系 5', 'detail', 'emp_xiaotuo',
                            recommendations=RECS)
    conn = sqlite3.connect(str(rec_db))
    n = conn.execute('SELECT COUNT(*) FROM talent_recommendations').fetchone()[0]
    conn.close()
    assert n == 5
    # 日报与推荐同事务：日报落库（last_run_at 回写）⇒ 推荐必已落
    conn = sqlite3.connect(str(rec_db))
    delivered = conn.execute(
        "SELECT last_run_at FROM emp_duties WHERE agent_id='emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert delivered > 0


def test_redeliver_same_day_replaces_not_duplicates(rec_db):
    bw._square_write_report('s1', 'd1', 'emp_xiaotuo', recommendations=RECS)
    recs2 = [dict(r, reason_text='改过的理由') for r in RECS]
    bw._square_write_report('s2', 'd2', 'emp_xiaotuo', recommendations=recs2)
    conn = sqlite3.connect(str(rec_db))
    n = conn.execute('SELECT COUNT(*) FROM talent_recommendations').fetchone()[0]
    reasons = {tuple(r) for r in conn.execute(
        'SELECT talent_id, reason_text FROM talent_recommendations').fetchall()}
    conn.close()
    assert n == 5, '同日重投必须幂等覆盖，行数不增'
    assert reasons == {(f'tal_{i}', '改过的理由') for i in range(5)}


def test_non_xiaotuo_agent_does_not_touch_b_table(rec_db):
    bw._square_write_report('s', 'd', 'emp_someone_else', recommendations=RECS)
    conn = sqlite3.connect(str(rec_db))
    n = conn.execute('SELECT COUNT(*) FROM talent_recommendations').fetchone()[0]
    conn.close()
    assert n == 0, 'B 表只认小拓——别的 agent 日报不得 contaminates 指标线'
