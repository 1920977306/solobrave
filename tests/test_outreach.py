#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单2 触达管道守卫（dq_8846ebdd308f）：状态机单向锁 / 漏斗可复算 / 校验闸门。

全部是黑盒测试——通过公共 API 驱动，不测实现细节（探针必须能失败）。
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'outreach', ROOT / 'daren_cockpit/tools/outreach.py')
outreach = importlib.util.module_from_spec(spec)
spec.loader.exec_module(outreach)


def _db(tmp_path):
    db = sqlite3.connect(str(tmp_path / 't.db'), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE deals (id TEXT PRIMARY KEY, talent_id TEXT,
                  product_id TEXT, status TEXT DEFAULT 'pending',
                  result_note TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER)''')
    db.execute('''CREATE TABLE talent_recommendations (
        rec_id TEXT PRIMARY KEY, rec_date TEXT NOT NULL, talent_id TEXT NOT NULL,
        talent_name TEXT NOT NULL DEFAULT '', matched_rules TEXT NOT NULL DEFAULT '[]',
        reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
        pipeline_status TEXT NOT NULL DEFAULT 'pending', deal_pid TEXT, gmv REAL,
        refund_flag INTEGER, run_id TEXT, created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL, UNIQUE(rec_date, talent_id))''')
    outreach.ensure_schema(db)
    return db


def _seed(db, week='2026-11-17'):
    """3 达人 3 推荐 + 2 触达 + 2 deals 的样例世界（漏斗手算可验）。"""
    ms = lambda d, h: int(__import__('datetime').datetime.strptime(
        f'{d} {h}', '%Y-%m-%d %H').timestamp() * 1000)
    for i, tid in enumerate(('tal_a', 'tal_b', 'tal_c'), 1):
        db.execute("INSERT INTO talent_recommendations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (f'rec{i}', week, tid, f'达人{i}', '[]', 'r', None, 'recommended',
                    None, None, None, f'run{i}', ms(week, 9), ms(week, 9)))
    db.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?)",
               ('deal_a', 'tal_a', 'p1', 'pending', '', ms(week, 8), ms(week, 8)))
    db.execute("INSERT INTO deals VALUES (?,?,?,?,?,?,?)",
               ('deal_b', 'tal_b', 'p2', 'negotiating', '', ms(week, 8), ms(week, 8)))
    db.commit()


def test_result_gate_rejects_invalid(tmp_path):
    db = _db(tmp_path)
    for bad in ('成交了', '', '未回复'):
        try:
            outreach.record_outreach(db, talent_id='tal_a', channel='douyin_dm',
                                     result=bad)
            raise AssertionError(f'非法 result {bad!r} 竟落库')
        except ValueError:
            pass
    assert db.execute('SELECT COUNT(*) FROM outreach_log').fetchone()[0] == 0


def test_dm_version_format_lock(tmp_path):
    db = _db(tmp_path)
    outreach.record_outreach(db, talent_id='tal_a', channel='douyin_dm',
                             result='洽谈', dm_version='v20261119-1')
    try:
        outreach.record_outreach(db, talent_id='tal_a', channel='douyin_dm',
                                 result='洽谈', dm_version='20261119')
        raise AssertionError('非法话术版本竟落库')
    except ValueError:
        pass


def test_deal_single_direction_lock(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    # 正常推进
    assert outreach.advance_deal(db, 'deal_a', 'negotiating')['to'] == 'negotiating'
    # 回退被拒
    try:
        outreach.advance_deal(db, 'deal_a', 'pending')
        raise AssertionError('回退竟成功')
    except ValueError:
        pass
    # 终态锁：成交后拒绝再动
    outreach.advance_deal(db, 'deal_a', 'completed')
    try:
        outreach.advance_deal(db, 'deal_a', 'failed')
        raise AssertionError('终态流转竟成功')
    except ValueError:
        pass
    # 幂等同位不炸
    assert outreach.advance_deal(db, 'deal_b', 'negotiating')['from'] == 'negotiating'


def test_outcome_pushes_deals_and_skips_finished(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    pushed = outreach.apply_outcome_to_deals(db, talent_id='tal_a', result='寄样')
    assert len(pushed) == 1 and pushed[0]['to'] == 'sample_sent'
    # 成交推终态后再来结果=幂等跳过
    outreach.apply_outcome_to_deals(db, talent_id='tal_a', result='成交')
    again = outreach.apply_outcome_to_deals(db, talent_id='tal_a', result='拒绝')
    assert all('skipped' in x for x in again), '终态 deal 必须跳过不再动'


def test_funnel_recomputable(tmp_path):
    """3 推荐（tal_a/b/c）、2 触达（a=洽谈 b=未回）⇒ 手算：
    触达 2/3，回应 1/2，洽谈 1/2，成交 0/2。"""
    db = _db(tmp_path)
    _seed(db)
    base = int(__import__('datetime').datetime.strptime(
        '2026-11-17 10', '%Y-%m-%d %H').timestamp() * 1000)
    outreach.record_outreach(db, talent_id='tal_a', channel='douyin_dm',
                             result='洽谈', contacted_at=base, dm_version='v20261119-1')
    outreach.record_outreach(db, talent_id='tal_b', channel='douyin_dm',
                             result='未回', contacted_at=base)
    f = outreach.render_funnel(db, '2026-11-17')
    assert (f['recommended'], f['touched'], f['replied'], f['talking'], f['won']) \
        == (3, 2, 1, 1, 0)
    assert f['touch_rate'] == round(2 / 3, 4) and f['reply_rate'] == 0.5


def test_funnel_dedup_takes_best_result(tmp_path):
    """同达人周内多次触达按 1 人计、取最优结果（未回→成交 只算成交）。"""
    db = _db(tmp_path)
    _seed(db)
    base = int(__import__('datetime').datetime.strptime(
        '2026-11-17 10', '%Y-%m-%d %H').timestamp() * 1000)
    outreach.record_outreach(db, talent_id='tal_a', channel='douyin_dm',
                             result='未回', contacted_at=base)
    outreach.record_outreach(db, talent_id='tal_a', channel='wechat',
                             result='成交', contacted_at=base + 86400000)
    f = outreach.render_funnel(db, '2026-11-17')
    assert f['touched'] == 1 and f['won'] == 1 and f['reply_rate'] == 1.0
