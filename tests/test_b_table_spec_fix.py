# -*- coding: utf-8 -*-
"""P2-④ 守卫：B 表口径整改四条钉死（rec_date TEXT / run_id 回填 / pipeline 初值 / 验收判据）。

整改背景：rec_date 曾毫秒串、run_id 恒空、pipeline_status 落库靠 DDL 默认 pending、
验收判据②钉死 =5（零候选日逼 AI 凑数）。守卫锁写入侧、DDL、验收脚本三处形态，
防口径漂回旧态。
"""
import importlib.util
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent

spec = importlib.util.spec_from_file_location(
    'bw_btable', ROOT / 'buyin_worker.py')
bw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bw)

NOW_MS = 1794792791338  # 2026-11-16 10:33 本地


def test_normalize_recdate_is_ymd_text():
    rows = bw._square_normalize_recs(
        [{'talent_id': 'tal_x', 'talent_name': 'n'}], NOW_MS, '')
    assert rows[0][1] == '2026-11-16', f'rec_date 非 YYYY-MM-DD: {rows[0][1]!r}'


def test_normalize_run_id_passthrough_and_none():
    rows = bw._square_normalize_recs(
        [{'talent_id': 't1'}, {'talent_id': 't2'}], NOW_MS, 'run-abc')
    assert rows[0][7] == 'run-abc'
    assert rows[1][7] == 'run-abc'
    rows2 = bw._square_normalize_recs([{'talent_id': 't3'}], NOW_MS, '')
    assert rows2[0][7] is None  # 无 run_id 落 NULL，不编造


def test_writer_sets_pipeline_recommended():
    src = (ROOT / 'buyin_worker.py').read_text(encoding='utf-8')
    assert "'recommended'" in src and 'pipeline_status' in src.split('INSERT OR REPLACE INTO talent_recommendations')[1][:400]
    assert 'pipeline_status, run_id, created_at, updated_at' in src


def test_server_ddl_recdate_text():
    src = (ROOT / 'solobrave-server.py').read_text(encoding='utf-8')
    ddl = src.split('CREATE TABLE IF NOT EXISTS talent_recommendations')[1][:400]
    assert 'rec_date TEXT NOT NULL' in ddl, 'DDL rec_date 退回 INTEGER'


def test_check_script_criteria():
    src = (ROOT / 'scripts' / 'check_daily_recommendations.py').read_text(encoding='utf-8')
    assert "where rec_date=?" in src, '验收脚本未改 TEXT 直等口径'
    assert '1 <= len(rows) <= 5' in src, '判据②未改宁缺毋滥口径'
    assert 'len(rows) == len(entries)' in src, '缺 2b 落库数对拍'
    assert 'len(entries) >= 1' in src, '检查1 下界未随判据②整改'


def test_migrated_rows_shape(test_data_dir):
    """迁移后存量行形态——用 conftest 快照副本（prod_db_guard 禁直连 prod）。"""
    db = sqlite3.connect(str(Path(test_data_dir) / 'solobrave.db'))
    try:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='talent_recommendations'").fetchone()
        if not exists:
            import pytest
            pytest.skip('测试库无 B 表（干净模式）')
        for rec_date, run_id, ps in db.execute(
                'SELECT rec_date, run_id, pipeline_status FROM talent_recommendations'):
            assert isinstance(rec_date, str) and len(rec_date) == 10 and rec_date[4] == '-'
            assert run_id, '存量行 run_id 未回填'
            assert ps == 'recommended'
    finally:
        db.close()
