#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""达人「空壳」单一真源（P2-①，2026-11-16）：可计算口径 + SQL 谓词，全仓唯一。

定义（七信号全中才是空壳——任一信号有值即视为有实质内容）：
  ① 无抖音号            douyin_id 空白
  ② 无平台达人关联       platform_talent_id 空白
  ③ 粉丝 0/空           followers 0 或 NULL 或空白（0=没采到）
  ④ 总 GMV 0/空         total_gmv 0 或 NULL 或空白
  ⑤ 无月结算额          monthly_settlement 空白
  ⑥ ⑦ 无任何联系方式    contact_wechat/contact_phone/wechat/phone 全空白

背景：keeper_ops 原有「无抖音号+0粉+无结算」三信号口径与派单建议的「核心五字段」
并存且不一致——本模块是合并后的唯一口径（keeper 旧口径 ⊂ 本口径）。
实测 2026-11-16：23/184 = 12.5%。

消费方：
  - solobrave-server.py /api/talents 列表隔离（默认排除空壳）
  - tools/keeper_ops.py 日报空壳率
守卫：tests/test_talent_shell.py（SQL 谓词 vs Python 判定全表对拍）。
"""
import sqlite3

CONTACT_COLS = ('contact_wechat', 'contact_phone', 'wechat', 'phone')

# SQL 谓词（消费方拼进 WHERE；空壳=真）
SHELL_SQL = (
    "(COALESCE(NULLIF(TRIM(CAST(douyin_id AS TEXT)), ''), '') = ''"
    " AND COALESCE(NULLIF(TRIM(CAST(platform_talent_id AS TEXT)), ''), '') = ''"
    " AND COALESCE(CAST(NULLIF(TRIM(CAST(followers AS TEXT)), '') AS INTEGER), 0) = 0"
    " AND COALESCE(CAST(NULLIF(TRIM(CAST(total_gmv AS TEXT)), '') AS REAL), 0.0) = 0.0"
    " AND COALESCE(NULLIF(TRIM(CAST(monthly_settlement AS TEXT)), ''), '') = ''"
    " AND COALESCE(NULLIF(TRIM(CAST(contact_wechat AS TEXT)), ''), '') = ''"
    " AND COALESCE(NULLIF(TRIM(CAST(contact_phone AS TEXT)), ''), '') = ''"
    " AND COALESCE(NULLIF(TRIM(CAST(wechat AS TEXT)), ''), '') = ''"
    " AND COALESCE(NULLIF(TRIM(CAST(phone AS TEXT)), ''), '') = '')"
)


def _blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def _zeroish(v):
    if _blank(v):
        return True
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return False


def is_shell(row):
    """row: dict 或 sqlite Row。七信号全中 → 空壳。与 SHELL_SQL 严格同口径。"""
    g = row.get if hasattr(row, 'get') else (lambda k, d=None: row[k] if k in row.keys() else d)
    return (_blank(g('douyin_id')) and _blank(g('platform_talent_id'))
            and _zeroish(g('followers')) and _zeroish(g('total_gmv'))
            and _blank(g('monthly_settlement'))
            and all(_blank(g(c)) for c in CONTACT_COLS))


def shell_stats(conn):
    """(空壳数, 总数)——消费方报空壳率用。"""
    n_shell = conn.execute(f'SELECT COUNT(*) FROM talents WHERE {SHELL_SQL}').fetchone()[0]
    n_all = conn.execute('SELECT COUNT(*) FROM talents').fetchone()[0]
    return n_shell, n_all
