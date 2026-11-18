#!/usr/bin/env python3
"""触达卡 v2 守卫（dq_556ca64c241c，2026-11-18）。

锁的形态：
1. 有匹配记录 ⇒ 每个推荐达人一行带品卡，含佣金与价格；
2. match_score 高者优先；
3. 无匹配记录 ⇒ 回退「已挂平台ID且有真实GMV」的热销品，不崩不编造；
4. 两边都无 ⇒ 明示「暂无可配品」，不静默丢达人；
5. 佣金口径 = product_facts（分档结构取最高档），卡片不另写解析；
6. _square_write_report 集成：小拓日报正文含【带品建议】段；
7. 非小拓 agent ⇒ 不加卡（B 表同闸语义一致）。
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daren_cockpit.tools import outreach_card as oc  # noqa: E402
import buyin_worker as bw  # noqa: E402


def _mk_db(tmp_path, with_match=True):
    db = tmp_path / 'oc.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE products (
        id TEXT PRIMARY KEY, name TEXT, price REAL, commission_rates TEXT,
        platform_product_id TEXT, platform_pay_gmv REAL, category TEXT)''')
    conn.execute('''CREATE TABLE product_talent_match (
        id TEXT PRIMARY KEY, product_id TEXT, talent_id TEXT,
        match_score REAL, match_reason TEXT)''')
    conn.executemany(
        'INSERT INTO products VALUES (?,?,?,?,?,?,?)', [
            ('p1', '骑士靴时尚款', 549.0, '{"default": 5}', 'pid_1', 1589342.0, None),
            ('p2', '人字拖沙滩款', 159.0, '{"投放期": 10, "常规活动期": 5, "其他": 5}',
             'pid_2', 20000.0, None),
            ('p3', '无佣金品', 99.0, '{}', 'pid_3', 999999.0, None),
        ])
    if with_match:
        conn.executemany(
            'INSERT INTO product_talent_match VALUES (?,?,?,?,?)', [
                ('m1', 'p1', 'tal_a', 88.0, '类目一致'),
                ('m2', 'p2', 'tal_a', 60.0, '价格带契合'),
            ])
    conn.commit()
    return db, conn


# ── 1. 基本形态：有匹配 ⇒ 带品卡含佣金与价格 ──────────────────────
def test_card_basic_shape(tmp_path):
    db, conn = _mk_db(tmp_path)
    text = oc.build_outreach_cards(conn, [
        {'talent_id': 'tal_a', 'talent_name': '番茄🍅炒蛋'}])
    assert text.startswith(oc.CARD_HEADING)
    assert '番茄🍅炒蛋' in text
    assert '骑士靴时尚款' in text and '¥549' in text and '佣金5%' in text
    conn.close()


# ── 2. match_score 优先 ─────────────────────────────────────────
def test_match_score_ordering(tmp_path):
    db, conn = _mk_db(tmp_path)
    prods = oc.match_products_for_talent(conn, 'tal_a', top_n=2)
    assert [p['name'] for p in prods][:2] == ['骑士靴时尚款', '人字拖沙滩款']
    conn.close()


# ── 3. 分档佣金取最高档（product_facts 口径）─────────────────────
def test_tiered_commission_uses_max(tmp_path):
    db, conn = _mk_db(tmp_path)
    prods = oc.match_products_for_talent(conn, 'tal_a', top_n=2)
    ren = next(p for p in prods if '人字拖' in p['name'])
    assert ren['rate'] == 10.0, '分档结构取最高档（投放期 10%）'
    conn.close()


# ── 4. 无匹配记录 ⇒ 热销回退；无佣金品被跳过 ─────────────────────
def test_fallback_to_gmv_products(tmp_path):
    db, conn = _mk_db(tmp_path, with_match=False)
    prods = oc.match_products_for_talent(conn, 'tal_zzz', top_n=2)
    names = [p['name'] for p in prods]
    assert '骑士靴时尚款' in names, '回退取 platform_pay_gmv 最高的有佣金品'
    assert '无佣金品' not in names, '佣金真值缺失的不推'
    conn.close()


# ── 4b. P3 类目过滤：达人 category 命中时优先类目池，真无命中才热销回退 ──
def _mk_db_with_talent(tmp_path, talent_cat):
    db, conn = _mk_db(tmp_path, with_match=False)
    conn.execute('''CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT,
                    category TEXT, main_category TEXT, top_categories TEXT)''')
    conn.execute('INSERT INTO talents VALUES (?,?,?,?,?)',
                 ('tal_cat', '类目达人', talent_cat, '', '[]'))
    conn.execute("INSERT INTO products VALUES ('p4','家清爆款',39.0,"
                 "'{\"default\": 20}','pid_4',500.0,'个护家清/纸品')")
    conn.execute("UPDATE products SET category='鞋靴/靴子' WHERE id IN ('p1','p3')")
    conn.execute("UPDATE products SET category='个护家清/纸品' WHERE id='p4'")
    conn.execute("UPDATE products SET category='鞋靴/拖鞋' WHERE id='p2'")
    conn.commit()
    return db, conn


def test_fallback_prefers_talent_category(tmp_path):
    """达人类目（个护家清）在池中有命中 ⇒ 优先推类目品，不推GMV更高的靴。"""
    db, conn = _mk_db_with_talent(tmp_path, '食品饮料/个护家清')
    prods = oc.match_products_for_talent(conn, 'tal_cat', top_n=2)
    assert prods and all(p['source'] == 'fallback_cat' for p in prods)
    assert '家清爆款' in [p['name'] for p in prods], '类目命中的品先进卡'
    assert all('骑士靴' not in p['name'] for p in prods), '有类目命中就不回退热销'
    conn.close()


def test_fallback_no_category_match_uses_hot_sale(tmp_path):
    """达人类目池中真无命中 ⇒ 回退热销（机制不变，宁回退不错配）。"""
    db, conn = _mk_db_with_talent(tmp_path, '生鲜/母婴')
    prods = oc.match_products_for_talent(conn, 'tal_cat', top_n=2)
    assert prods and all(p['source'] == 'fallback' for p in prods)
    assert '骑士靴时尚款' in [p['name'] for p in prods]
    conn.close()


def test_whole_token_match_not_substring(tmp_path):
    """整 token 包含：「食品」撞「特医食品」不算命中（防弱子串错配）。"""
    db, conn = _mk_db(tmp_path, with_match=False)
    conn.execute('''CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT,
                    category TEXT, main_category TEXT, top_categories TEXT)''')
    conn.execute("INSERT INTO talents VALUES ('tal_f','食饮','食品饮料','','[]')")
    conn.execute("INSERT INTO products VALUES ('p5','特医钙片',59.0,"
                 "'{\"default\": 15}','pid_5',100.0,NULL)")
    conn.execute("UPDATE products SET category='营养保健/特医食品/维生素' WHERE id='p5'")
    conn.execute("UPDATE products SET category='鞋靴/靴子' WHERE id IN ('p1','p3')")
    conn.execute("UPDATE products SET category='鞋靴/拖鞋' WHERE id='p2'")
    conn.commit()
    prods = oc.match_products_for_talent(conn, 'tal_f', top_n=2)
    assert all(p['source'] == 'fallback' for p in prods), '子串不算命中⇒热销回退'
    conn.close()


# ── 5. 两边都无 ⇒ 明示暂无可配品，达人不丢 ──────────────────────
def test_empty_pool_explicit_note(tmp_path):
    db = tmp_path / 'empty.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT, price REAL,'
                 ' commission_rates TEXT, platform_product_id TEXT, platform_pay_gmv REAL)')
    conn.execute('CREATE TABLE product_talent_match (id TEXT PRIMARY KEY,'
                 ' product_id TEXT, talent_id TEXT, match_score REAL, match_reason TEXT)')
    conn.commit()
    text = oc.build_outreach_cards(conn, [{'talent_id': 'tal_x', 'talent_name': '边老师'}])
    assert '边老师' in text and '暂无可配品' in text
    conn.close()


# ── 6/7. _square_write_report 集成：小拓加卡 / 他人不加 ──────────
def _duty_db(tmp_path):
    db = tmp_path / 'r.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id,
                    type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)''')
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    duty_name TEXT DEFAULT '达人筛选专员',
                    supervisor_agent_id TEXT DEFAULT '', zero_streak INTEGER DEFAULT 0,
                    last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
                    updated_at INTEGER)''')
    conn.execute('''CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT, price REAL,
                    commission_rates TEXT, platform_product_id TEXT, platform_pay_gmv REAL,
                    category TEXT)''')
    conn.execute('''CREATE TABLE product_talent_match (id TEXT PRIMARY KEY,
                    product_id TEXT, talent_id TEXT, match_score REAL, match_reason TEXT)''')
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', '达人筛选专员', '', 0, 0, '', 0)")
    conn.execute("INSERT INTO products VALUES ('p1','骑士靴',549.0,'{\"default\": 5}','pid_1',100.0,'鞋靴/靴子')")
    conn.execute("INSERT INTO product_talent_match VALUES ('m1','p1','tal_a',88.0,'类目一致')")
    conn.execute('''CREATE TABLE talent_recommendations (
        rec_id TEXT PRIMARY KEY, rec_date TEXT NOT NULL, talent_id TEXT NOT NULL,
        talent_name TEXT NOT NULL DEFAULT '', matched_rules TEXT NOT NULL DEFAULT '[]',
        reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
        pipeline_status TEXT NOT NULL DEFAULT 'pending', deal_pid TEXT, gmv REAL,
        refund_flag INTEGER, run_id TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(rec_date, talent_id))''')
    conn.commit()
    return db


_RECS = [{'talent_id': 'tal_a', 'talent_name': '番茄🍅炒蛋',
          'matched_rules': ['r1'], 'reason_text': '月结算过万', 'snapshot': {}}]


def test_write_report_appends_card_for_xiaotuo(tmp_path, monkeypatch):
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report('值得联系 1', '详情正文', 'emp_xiaotuo',
                            recommendations=_RECS, run_id='run_t')
    conn = sqlite3.connect(str(db))
    content = conn.execute(
        "SELECT content FROM notifications WHERE agent_id='emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert '【带品建议】' in content and '骑士靴' in content and '佣金5%' in content


def test_write_report_no_card_for_other_agent(tmp_path, monkeypatch):
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report('值得联系 1', '详情正文', 'emp_someone_else',
                            recommendations=_RECS, run_id='run_t')
    conn = sqlite3.connect(str(db))
    content = conn.execute(
        "SELECT content FROM notifications WHERE agent_id='emp_someone_else'").fetchone()[0]
    conn.close()
    assert '【带品建议】' not in content, '非小拓不加卡（与 B 表同闸）'


def test_card_assembly_failure_never_blocks_report(tmp_path, monkeypatch):
    """卡片组装异常 ⇒ 日报照交（旁路语义与主管投递一致）。"""
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    real = oc.build_outreach_cards
    monkeypatch.setattr(oc, 'build_outreach_cards',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom')))
    monkeypatch.setitem(sys.modules, 'daren_cockpit.tools.outreach_card', oc)
    bw._square_write_report('值得联系 1', '详情正文', 'emp_xiaotuo',
                            recommendations=_RECS, run_id='run_t')
    monkeypatch.setattr(oc, 'build_outreach_cards', real)
    conn = sqlite3.connect(str(db))
    n = conn.execute(
        "SELECT COUNT(*) FROM notifications WHERE agent_id='emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert n == 1, '日报主交付不被卡片故障阻断'
