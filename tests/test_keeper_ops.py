# -*- coding: utf-8 -*-
"""keeper_ops 体检判据守卫（组织第一批，2026-11-11）。

核心守卫（老大 11-11 修订①）：重复判重必须双口径——单口径「0」是假阴性会骗人。
- 严格：完全同名 + 粉丝差 <5%
- 宽松：归一化名相似度 ≥0.60（剥括号后缀；粉丝差不门槛，只陈列）
- 真案例钉死：七月姐姐/七月姐姐（赚钱买房）（粉丝差 29%，严格口径必漏）、
  小美穿搭日记/小美搭配日记（相似度 0.83）。
DB 全落 tmp（monkeypatch keeper_ops.DB），零 prod 接触。
"""
import sqlite3
import time

import pytest

import tools.keeper_ops as ko


@pytest.fixture()
def talent_db(tmp_path, monkeypatch):
    db = tmp_path / 'k.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE talents (
        id TEXT, name TEXT, followers INTEGER, douyin_id TEXT,
        main_category TEXT, monthly_settlement REAL,
        contact TEXT, contact_wechat TEXT,
        updated_at INTEGER, archived INTEGER)''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(ko, 'DB', db)
    return db


def _seed(db, rows):
    conn = sqlite3.connect(str(db))
    conn.executemany(
        'INSERT INTO talents VALUES (?,?,?,?,?,?,?,?,?,?)', rows)
    conn.commit()
    conn.close()


_NOW = int(time.time() * 1000)


def test_dup_dual_tier_real_cases(talent_db):
    """老大点名的真案例：两组都必须在宽松档出现（严格档 0 是已知盲区，不许再报「干净」）。"""
    _seed(talent_db, [
        ('t1', '七月姐姐', 17000, 'dy1', '鞋靴', None, None, None, _NOW, None),
        ('t2', '七月姐姐（赚钱买房）', 12000, 'dy2', '鞋靴', None, None, None, _NOW, None),
        ('t3', '小美穿搭日记', 245000, 'dy3', '女装', None, None, None, _NOW, None),
        ('t4', '小美搭配日记', 580000, 'dy4', '女装', None, None, None, _NOW, None),
        # 对照：名字完全不像，不该上榜
        ('t5', '洁洁爱户外', 3000, 'dy5', '户外', None, None, None, _NOW, None),
        ('t6', '海阔凭鱼跃', 90000, 'dy6', '百货', None, None, None, _NOW, None),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
    finally:
        conn.close()
    loose_pairs = {frozenset(x['names']) for x in r['dup_loose']}
    assert frozenset(('七月姐姐', '七月姐姐（赚钱买房）')) in loose_pairs, \
        '剥括号归一后同名必须进宽松档（粉丝差 29% 也不能漏）'
    assert frozenset(('小美穿搭日记', '小美搭配日记')) in loose_pairs, \
        '相似名 0.83 必须进宽松档'
    assert frozenset(('洁洁爱户外', '海阔凭鱼跃')) not in loose_pairs, '名字不像不许上榜'
    # 输出形态：粉丝数只陈列不门槛、带相似度
    sample = next(x for x in r['dup_loose']
                  if frozenset(x['names']) == frozenset(('七月姐姐', '七月姐姐（赚钱买房）')))
    assert sample['sim'] == 1.0 and sorted(sample['followers']) == [12000, 17000]


def test_dup_sure_same_douyin_id(talent_db):
    _seed(talent_db, [
        ('t1', '甲', 100, 'same_dy', 'x', None, None, None, _NOW, None),
        ('t2', '乙', 200, 'same_dy', 'x', None, None, None, _NOW, None),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
    finally:
        conn.close()
    assert len(r['dup_sure']) == 1 and r['dup_sure'][0]['douyin_id'] == 'same_dy'


def test_no_duplicates_reports_empty_not_none(talent_db):
    """干净库也要返回双口径键（值空列表）——下游日报不许因为键缺失而少报一行。"""
    _seed(talent_db, [
        ('t1', '甲', 100, 'dy1', 'x', None, None, None, _NOW, None),
        ('t2', '乙', 200, 'dy2', 'y', None, None, None, _NOW, None),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
    finally:
        conn.close()
    assert r['dup_sure'] == [] and r['dup_maybe'] == [] and r['dup_loose'] == []
