#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商家号谓词单一真源（P2 商家号隔离 2026-11-23，dq_36f8a1236e79）：
形态学 v1 词表入码，复用 talent_shell.py 模式（SQL 谓词 + Python 判定同口径）。

背景（老大 11-20 16:19 口径）：「达人名字是公司名或店名直接 pass」。
实证：11-23 形态学扫描 10/227 全中（海贸箱包有限公司 154 万粉占全库头部），
全部鞋靴/箱包行业工厂·总店·有限公司形态，0 真达人误伤：
  海贸箱包有限公司/红谷箱包贵阳总店/佳佳箱包工厂/乐姐箱包工厂店10/
  渡诺班尼鞋靴工厂店/桑坡胖飞飞鞋靴工厂/千喜箱包工厂/轩尼哲米乐乐箱包总店/
  温州曜恒鞋靴工厂/嵩翎箱包2店

判定只看 name（组织形态后缀）——不按类目限死，误杀面由词表形态学保证；
真达人负样例锁在 tests/test_talent_merchant.py（防后续加词误杀）。

消费方：
  - buyin_worker._square_insert_talent（增量打标 remark.merchant，不丢弃）
  - buyin_worker._square_decide（merchant 硬闸：不 upsert B 表推荐位）
  - solobrave-server /api/products/recommend（商家号不进匹配池）
  - tools/keeper_ops.py 日报口径（可选）
"""
import re
import sqlite3

# v1 词表（2026-11-23 定稿；加词必须同步守卫负样例集）
MERCHANT_PATTERNS = [
    r'有限公司',
    r'集团',
    r'实业',
    r'商贸',
    r'贸易公司',
    r'鞋业',
    r'皮具',
    r'工厂',
    r'厂$',        # 组织词根结尾（鞋厂/箱包厂/厂）
    r'店$',        # 店铺形态结尾（总店/旗舰店/工厂店/2店）
    r'清仓',
]
_MERCHANT_RX = re.compile('|'.join(MERCHANT_PATTERNS))

# SQL 近似（SQLite 无 REGEXP；LIKE 组合与正则同口径——守卫全表对拍锁死）
MERCHANT_SQL = (
    "(name LIKE '%有限公司%'"
    " OR name LIKE '%集团%'"
    " OR name LIKE '%实业%'"
    " OR name LIKE '%商贸%'"
    " OR name LIKE '%贸易公司%'"
    " OR name LIKE '%鞋业%'"
    " OR name LIKE '%皮具%'"
    " OR name LIKE '%工厂%'"
    " OR name LIKE '%厂'"
    " OR name LIKE '%店'"
    " OR name LIKE '%清仓%')"
)


def is_merchant_name(name):
    """昵称形态学判定：命中组织形态后缀 → 商家号。空名/非字符串 → False。"""
    return bool(_MERCHANT_RX.search(str(name or '')))


def is_merchant(row):
    """row: dict 或 sqlite Row（取 name 字段）。与 MERCHANT_SQL 严格同口径。"""
    g = row.get if hasattr(row, 'get') else (
        lambda k, d=None: row[k] if k in row.keys() else d)
    return is_merchant_name(g('name'))


def merchant_stats(conn):
    """(商家号数, 总数)——日报/验收口径。"""
    n_m = conn.execute(f'SELECT COUNT(*) FROM talents WHERE {MERCHANT_SQL}').fetchone()[0]
    n_all = conn.execute('SELECT COUNT(*) FROM talents').fetchone()[0]
    return n_m, n_all
