# -*- coding: utf-8 -*-
"""商家号隔离守卫（P2 2026-11-23，dq_36f8a1236e79）。

矩阵：
1. 词表 vs SQL 全表对拍（同口径铁律，同 test_talent_shell 模式）
2. 10 商家正样例全命中（协调边名单复现）
3. 真达人负样例零误杀（美洋生活好物/唐森森/赵总外贸家居/七月姐姐等）
4. 空名/None 不炸
5. 谓词与 worker 硬闸源码锚（decide 不 upsert / insert 打标）
"""
import importlib.util
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'talent_merchant', os.path.join(ROOT, 'tools/talent_merchant.py'))
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)

# 协调边形态学扫描 10 商家实证名单（11-23 A 边独立复现一致）
MERCHANT_POS = [
    '海贸箱包有限公司', '红谷箱包贵阳总店', '佳佳箱包工厂', '乐姐箱包工厂店10',
    '渡诺班尼鞋靴工厂店', '桑坡胖飞飞鞋靴工厂', '千喜箱包工厂',
    '轩尼哲米乐乐箱包总店', '温州曜恒鞋靴工厂', '嵩翎箱包2店',
]
# 真达人负样例（库里真实推荐过的达人——误杀一个都是事故）
MERCHANT_NEG = [
    '美洋生活好物', '唐森森𝐈𝐜𝐞', '赵总外贸家居', '七月姐姐', '仙女不是先生',
    '方也Vicky', '挖宝小泽🌟', '小新大王', '奇居良品家居生活美学',
    '是叔不是猪', '大脸丹的穿搭日记', '原来是安娜',
]


def test_positives_all_hit():
    miss = [n for n in MERCHANT_POS if not tm.is_merchant_name(n)]
    assert not miss, f'商家正样例未命中: {miss}'


def test_negatives_zero_false_kill():
    bad = [n for n in MERCHANT_NEG if tm.is_merchant_name(n)]
    assert not bad, f'真达人被误杀: {bad}'


def test_blank_safe():
    assert tm.is_merchant_name('') is False
    assert tm.is_merchant_name(None) is False
    assert tm.is_merchant({'name': None}) is False


def test_sql_python_same_contract(tmp_path):
    """SQL 谓词与 Python 判定全表对拍（同口径铁律）。"""
    db = tmp_path / 'm.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE talents (id TEXT, name TEXT)')
    names = MERCHANT_POS + MERCHANT_NEG + ['', None, '普通名字', 'Wgg292', '鞋靴',
                                           '工厂直发吗', '店面装修']  # 边界
    conn.executemany('INSERT INTO talents VALUES (?,?)',
                     [(f't{i}', n) for i, n in enumerate(names)])
    conn.commit()
    sql_ids = {r[0] for r in conn.execute(
        f'SELECT id FROM talents WHERE {tm.MERCHANT_SQL}')}
    py_ids = {f't{i}' for i, n in enumerate(names) if tm.is_merchant_name(n)}
    conn.close()
    assert sql_ids == py_ids, f'SQL/Python 口径分叉: sql-only={sql_ids-py_ids} py-only={py_ids-sql_ids}'


def test_worker_hard_gate_wired():
    """源码锚：decide 硬闸（merchant 不 upsert B 表）+ 入库打标接上了谓词。"""
    src = open(os.path.join(ROOT, 'buyin_worker.py'), encoding='utf-8').read()
    assert '_talent_merchant' in src, 'worker 必须接谓词单一真源'
    assert 'merchant' in src
    dec = src.split('def _square_decide', 1)[1].split('def _wan', 1)[0]
    assert 'merchant' in dec, 'decide 内必须有 merchant 硬闸'
