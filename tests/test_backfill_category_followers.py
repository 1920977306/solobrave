# -*- coding: utf-8 -*-
"""dq_3733edc03e03 守卫：类目+粉丝写回工具的口径纪律。

核心纪律（spec 原文）：空值检查对 INTEGER 列（followers）不许用 IS NULL/=""
（恒空集假清零），用 =0 并对 remark 分桶；真没有就登记不造值（空串候选 ['']
不算可补）；dry-run 零写库。
"""
import importlib.util
import json
import os
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'bcf', os.path.join(ROOT, 'scripts', 'backfill_category_followers.py'))
bcf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bcf)

_SCHEMA = '''CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, category TEXT, followers INTEGER,
    remark TEXT, updated_at INTEGER)'''

_ENTRY = {
    'talent_id': 't1', 'name': '甲', 'proposed': 'dy001',
    'match_how': 'name_exact',
    'candidates': [{'昵称': '甲', '抖音号': 'dy001', '粉丝': 88000,
                    '主推类目': ['服饰内衣']}],
}


def _mk_db(tmp_path):
    db = tmp_path / 't.db'
    conn = sqlite3.connect(str(db))
    conn.execute(_SCHEMA)
    conn.execute('INSERT INTO talents VALUES (?,?,?,?,?,?)',
                 ('t1', '甲', None, 0, json.dumps({'douyin_id_backfilled': {}}), 0))
    conn.commit()
    conn.close()
    return db


def test_integer_gap_uses_eq_zero_not_is_null():
    """INTEGER 口径锚：followers 判缺口用 =0，IS NULL 是恒空集假清零。"""
    src = open(bcf.__file__, encoding='utf-8').read()
    assert 'fol == 0' in src or 'followers = 0' in src
    assert 'followers IS NULL' not in src, 'INTEGER 列 IS NULL = 恒空集假清零'
    # 分桶纪律：followers=0 必须按 remark 分桶（回填标记 vs 自然零）
    assert '回填标记仍 0' in src and '自然零' in src


def test_dry_run_writes_nothing(tmp_path):
    """dry-run 只出报告，零写库（含 remark 来源标记也不写）。"""
    db = _mk_db(tmp_path)
    report = tmp_path / 'r.json'
    report.write_text(json.dumps({'searched': [_ENTRY]}), encoding='utf-8')
    before = sqlite3.connect(str(db)).execute(
        'SELECT category, followers, remark FROM talents').fetchall()
    bcf.gap_stats(sqlite3.connect(str(db)))
    rows = bcf.plan(sqlite3.connect(str(db)), str(report))
    assert rows and rows[0].get('cat') == '服饰内衣' and rows[0].get('fol') == 88000
    after = sqlite3.connect(str(db)).execute(
        'SELECT category, followers, remark FROM talents').fetchall()
    assert before == after, 'dry-run 阶段（plan/gap_stats）绝不许写库'


def test_empty_string_candidate_not_fabricated(tmp_path):
    """空串候选 [''] 不是可补值——真没有就登记不造值。"""
    db = _mk_db(tmp_path)
    entry = dict(_ENTRY, proposed='dy002',
                 candidates=[{'昵称': '甲', '抖音号': 'dy002', '粉丝': 0,
                              '主推类目': ['']}])
    report = tmp_path / 'r.json'
    report.write_text(json.dumps({'searched': [entry]}), encoding='utf-8')
    conn = sqlite3.connect(str(db))
    rows = bcf.plan(conn, str(report))
    # followers=0 且候选粉丝=0、类目 [''] ⇒ 全不可补（登记不造值）
    assert not [r for r in rows if 'skip' not in r], \
        f"空串候选不许造值：{rows}"
    conn.close()
