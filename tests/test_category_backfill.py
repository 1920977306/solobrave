#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单1 池外回源 apply 守卫（dq_f37b4b1538b1）：幂等不双写 / 真值不全不建档 / pid 全填。"""
import importlib.util
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'fp', ROOT / 'daren_cockpit/tools/filing_products.py')
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)


def _db(tmp_path):
    db = sqlite3.connect(str(tmp_path / 't.db'), timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT,
        subtitle TEXT DEFAULT '', main_image TEXT DEFAULT '', price REAL DEFAULT 0,
        price_range TEXT DEFAULT '', brand TEXT DEFAULT '', category TEXT DEFAULT '',
        monthly_sales INTEGER DEFAULT 0, commission_rates TEXT DEFAULT '{}',
        commission_amount REAL DEFAULT 0, influencer_count INTEGER DEFAULT 0,
        talent_count INTEGER DEFAULT 0, tags TEXT DEFAULT '[]',
        selling_points TEXT DEFAULT '', status TEXT DEFAULT 'active',
        platform_product_id TEXT DEFAULT '', platform_pay_gmv REAL,
        platform_settle_gmv REAL, platform_order_pay_num INTEGER,
        platform_order_daren_count INTEGER, platform_stat_date TEXT DEFAULT '',
        created_by TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER)''')
    db.execute("INSERT INTO products VALUES ('prod_seed_1','存量品','','',0,'','',"
               "'鞋靴箱包',0,'{}',0,0,0,'[]','','active','111',NULL,NULL,NULL,NULL,'',"
               "'boss_1',0,0)")
    db.commit()
    return db


def _candidates(tmp_path, items):
    p = tmp_path / 'cand.json'
    p.write_text(json.dumps({'enriched': items, 'failed': [], 'belong': {}},
                            ensure_ascii=False), encoding='utf-8')
    return str(p)


_GOOD = {'3850428401399759343': {
    'product_id': '3850428401399759343', 'name': '黄油曲奇饼干', 'price': '¥19.9',
    'commission_rate': '5%', 'commission': '¥1', 'sales': '845862',
    'shop': '奥力给官方旗舰店', 'category_first': '食品饮料',
    'category_third': '酥性饼干', 'shop_score': '87', 'cooper_author_num': '5750'}}


def test_apply_inserts_and_ownership(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(fp, 'DB', tmp_path / 't.db')
    stats = fp.apply_category_backfill(db, _candidates(tmp_path, _GOOD))
    assert len(stats['added']) == 1
    row = db.execute("SELECT * FROM products WHERE platform_product_id='3850428401399759343'").fetchone()
    assert row['created_by'] == 'boss_1', '归属必须取存量最常见非空（租户可见性铁律）'
    assert row['price'] == 19.9 and '5' in row['commission_rates']
    assert row['platform_pay_gmv'] is None, '池外品 GMV 恒空是正常（§8.2 口径）'


def test_apply_idempotent_no_double_write(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(fp, 'DB', tmp_path / 't.db')
    fp.apply_category_backfill(db, _candidates(tmp_path, _GOOD))
    again = fp.apply_category_backfill(db, _candidates(tmp_path, _GOOD))
    assert again['added'] == [] and len(again['skipped_dup']) == 1
    n = db.execute("SELECT COUNT(*) FROM products WHERE platform_product_id="
                   "'3850428401399759343'").fetchone()[0]
    assert n == 1, '幂等：重复 apply 不双写'


def test_apply_skips_items_missing_truth(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(fp, 'DB', tmp_path / 't.db')
    bad = {'999': {'product_id': '999', 'name': '无佣品', 'price': '¥9.9',
                   'commission_rate': '', 'category_first': '食品饮料'}}
    stats = fp.apply_category_backfill(db, _candidates(tmp_path, bad))
    assert stats['added'] == [] and stats['skipped_bad'] == ['999'], \
        '价格/佣金真值缺一不建档（不造数）'


# ── 一级类目反查守卫（11-19 apply 后实证修：达人画像写一级，商品须带一级才匹配）──
_MINI_TREE = {'data': {'industry_list': [{'child_cate_list': [
    {'cate_name': '食品饮料', 'cate_level': 0, 'child_cate_list': [
        {'cate_id': '1000000724', 'cate_name': '休闲食品', 'cate_level': 1}]}]}]}}

_ITEM_SECOND_ONLY = {'3850428401399759343': {
    'product_id': '3850428401399759343', 'name': '黄油曲奇饼干', 'price': '¥19.9',
    'commission_rate': '5%', 'category_first': '休闲食品',   # 候选原值 = 二级名
    'category_first_id': '1000000724', 'category_third': '酥性饼干'}}


def test_apply_first_level_backfilled(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(fp, 'DB', tmp_path / 't.db')
    stats = fp.apply_category_backfill(db, _candidates(tmp_path, _ITEM_SECOND_ONLY),
                                       tree=_MINI_TREE)
    row = db.execute("SELECT category FROM products WHERE platform_product_id="
                     "'3850428401399759343'").fetchone()
    assert row['category'].split('/')[0] == '食品饮料', \
        '候选只带二级 id 时，apply 必须反查树把一级名落进 category 首段（触达卡匹配锚）'
    assert '酥性饼干' in row['category'], '三级名保留'


def test_apply_first_level_lookup_miss_keeps_original(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(fp, 'DB', tmp_path / 't.db')
    item = dict(_ITEM_SECOND_ONLY)
    item['3850428401399759343'] = dict(item['3850428401399759343'],
                                       category_first_id='99999999999')  # 树里没有
    stats = fp.apply_category_backfill(db, _candidates(tmp_path, item),
                                       tree=_MINI_TREE)
    row = db.execute("SELECT category FROM products WHERE platform_product_id="
                     "'3850428401399759343'").fetchone()
    assert stats['added'], '反查失败不阻断建档'
    assert row['category'].split('/')[0] == '休闲食品', \
        '树反查不到 ⇒ 保留候选原值（只丢不造，不编造一级名）'
