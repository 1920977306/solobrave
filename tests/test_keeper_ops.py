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


# ── 撞名关系标注（老大 11-13：抖音号是身份键，撞名≠重复，合并才制造错误数据） ──

def _seed_relations(db, rows):
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE IF NOT EXISTS talent_relations (
        id TEXT PRIMARY KEY, a_talent_id TEXT NOT NULL, b_talent_id TEXT NOT NULL,
        relation_label TEXT NOT NULL, labeled_by TEXT NOT NULL,
        labeled_at INTEGER NOT NULL, evidence TEXT DEFAULT '', note TEXT DEFAULT '',
        UNIQUE (a_talent_id, b_talent_id), CHECK (a_talent_id < b_talent_id))''')
    conn.executemany(
        'INSERT OR IGNORE INTO talent_relations VALUES (?,?,?,?,?,?,?,?)', rows)
    conn.commit()
    conn.close()


def test_labeled_different_is_silenced(talent_db):
    """已标 different 的 pair 从 dup_loose 消失（降噪——标过还每天重报 =
    逼人对同一问题标第二次）。真案例：小美穿搭 vs 小美搭配（0.83 相似度）。"""
    _seed(talent_db, [
        ('t1', '小美穿搭日记', 245000, 'dy3', '女装', None, None, None, _NOW, None),
        ('t2', '小美搭配日记', 580000, 'dy4', '女装', None, None, None, _NOW, None),
    ])
    _seed_relations(talent_db, [
        ('r1', 't1', 't2', 'different', 'boss', _NOW,
         'douyin dy3 vs dy4 号不同', ''),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
        names = [tuple(d['names']) for d in r['dup_loose']]
        assert ('小美穿搭日记', '小美搭配日记') not in names
    finally:
        conn.close()


def test_labeled_same_team_kept_with_prefix(talent_db):
    """已标 same_team 的 pair 仍列出（供商务参考）但带 labeled 前缀。真案例：明赫 5/6/7。"""
    _seed(talent_db, [
        ('t1', '明赫箱包5', 26578, 'dy1', '箱包', 2500, None, None, _NOW, None),
        ('t2', '明赫箱包6', 4135, 'dy2', '箱包', 1500, None, None, _NOW, None),
        ('t3', '明赫箱包7', 2094, 'dy3', '箱包', 300, None, None, _NOW, None),
    ])
    _seed_relations(talent_db, [
        ('r1', 't1', 't2', 'same_team', 'boss', _NOW, '同店矩阵', ''),
        ('r2', 't1', 't3', 'same_team', 'boss', _NOW, '同店矩阵', ''),
        ('r3', 't2', 't3', 'same_team', 'boss', _NOW, '同店矩阵', ''),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
        labeled = [d for d in r['dup_loose'] if d.get('labeled') == 'same_team']
        assert len(labeled) == 3   # 三对都在且都带前缀
    finally:
        conn.close()


def test_unknown_pair_still_reported(talent_db):
    """unknown（待补证据）不降噪——人还没裁完，必须继续出现在清单里。"""
    _seed(talent_db, [
        ('t1', '七月姐姐', 17000, '', '鞋靴', None, None, None, _NOW, None),
        ('t2', '七月姐姐（赚钱买房）', 12000, '', '鞋靴', None, None, None, _NOW, None),
    ])
    _seed_relations(talent_db, [
        ('r1', 't1', 't2', 'unknown', 'executor', _NOW,
         '均无抖音号，粉丝差29%', ''),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
        assert len(r['dup_loose']) == 1 and 'labeled' not in r['dup_loose'][0]
    finally:
        conn.close()


def test_shell_records_triple_condition(talent_db):
    """空壳三条件：无抖音号 + 0粉 + 无结算。鸡腿堡案：空壳与实号分开列。"""
    _seed(talent_db, [
        ('t1', '鸡腿堡', 0, '', '百货', None, None, None, _NOW, None),
        ('t2', '劲脆鸡腿堡', 4620, 'dy9', '鞋靴箱包', 100, None, None, _NOW, None),
        ('t3', '有号零粉', 0, 'dy10', '百货', None, None, None, _NOW, None),  # 有号不算
        ('t4', '无号有粉', 5000, '', '百货', None, None, None, _NOW, None),   # 有粉不算
        ('t5', '无号零粉有结算', 0, '', '百货', 88, None, None, _NOW, None),  # 有结算不算
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
        shells = {s['id'] for s in r['shells']}
        assert shells == {'t1'}
    finally:
        conn.close()


def test_relations_table_missing_is_safe(talent_db):
    """旧库无 talent_relations 表：体检照常，不炸（幂等迁移前兼容）。"""
    _seed(talent_db, [
        ('t1', '七月姐姐', 17000, 'dy1', '鞋靴', None, None, None, _NOW, None),
        ('t2', '七月姐姐（赚钱买房）', 12000, 'dy2', '鞋靴', None, None, None, _NOW, None),
    ])
    conn = ko._conn()
    try:
        r = ko.talent_healthcheck(conn)
        assert len(r['dup_loose']) == 1
    finally:
        conn.close()
