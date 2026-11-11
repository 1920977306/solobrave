#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""库管家体检脚本（组织第一批 2026-11-11）——达人库/商品库两个管家的每日只读体检。

铁律：
- 全程只读（SELECT），没有任何写库路径——合并/删除/补全是人做的，管家只产清单；
- 判据写死在本文件的常量里（老大的口径：日报三清单怎么算， here）；
- agent 在 openclaw turn 里跑 `python3 tools/keeper_ops.py --agent emp_xxx`，
  拿到输出后自己组织日报，走 /square-report 交付。

三判据（与 emp_duties.judge_basis 同口径）：
达人库（emp_talent_keeper）：
  字段缺口：核心五字段（抖音号/粉丝数/主营类目/30天结算额/联系方式可得性）缺 ≥2 项上榜；
  重复疑似对（双口径，假阴性比假阳性危险——单口径「0」会骗人）：
  严格：完全同名且粉丝差 <5%；宽松：归一化名相似度 ≥0.60（剥括号后缀再比对，粉丝差不门槛、只陈列），需人工确认；
  同抖音号 = 确定重复（只报告，不合并）；
  过期：updated_at 超 14 天未动 = 过期标记（只标记，不改库）。
商品库（emp_goods_keeper）：
  佣金过期：daren_synced_at 超 7 天 = 佣金口径可能失效；
  字段缺口：四核心（到手价/佣金率/类目/店铺分）缺一即缺口；
  疑似下架：超 14 天未更新 且 无任何榜单记录 且 7日销量为空。

运行：.venv/bin/python3 tools/keeper_ops.py --agent emp_talent_keeper [--json]
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'solobrave.db'

DAY_MS = 24 * 3600 * 1000
TALENT_STALE_DAYS = 14      # 达人档案过期线
DUP_LOOSE_NAME_SIM = 0.60   # 宽松口径：归一名相似度阈值（difflib ratio，老大 11-11：
                            # 假阴性比假阳性危险——真案例里 4 组重复三组粉丝差 >20%，
                            # 粉丝差不作宽松档门槛，报出来让人看）
DUP_LOOSE_MAX_REPORT = 20   # 宽松档最多报 20 组，防噪音淹没


def _norm_name(name):
    """归一化达人名：剥括号后缀（七月姐姐（赚钱买房）→ 七月姐姐）再比对。"""
    n = str(name or '').strip()
    return re.sub(r'[（(【\[].*$', '', n).strip()
GOODS_COMMISSION_STALE_DAYS = 7   # 佣金数据时效线（对齐周报黄线口径）
GOODS_OFF_SHELF_DAYS = 14         # 疑似下架线

AGENT_TABLE = {
    'emp_talent_keeper': 'talents',
    'emp_goods_keeper': 'products',
}


def _conn():
    # 只读体检：普通 rw 句柄但只执行 SELECT（mode=ro 在 WAL 下可能建不了 -shm，§16 Y）
    conn = sqlite3.connect(str(DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _cols(conn, table):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}


def _col(row, key, default=''):
    v = row.get(key)
    return v if v not in (None, '') else default


def talent_healthcheck(conn):
    """达人库体检：字段缺口 / 重复疑似对 / 过期标记。返回纯 dict（可 JSON 序列化）。"""
    cols = _cols(conn, 'talents')
    now = int(time.time() * 1000)
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM talents WHERE archived IS NULL OR archived = 0'
        if 'archived' in cols else 'SELECT * FROM talents').fetchall()]

    core = {'douyin_id': '抖音号', 'followers': '粉丝数', 'main_category': '主营类目',
            'monthly_settlement': '30天结算额',
            'contact': '联系方式'}
    available = {k: (k in cols) for k in core}
    contact_cols = [c for c in ('contact', 'contact_phone', 'contact_wechat',
                                'contact_email', 'wechat', 'phone', 'email') if c in cols]

    gaps, stale = [], []
    for r in rows:
        missing = [core[k] for k in ('douyin_id', 'followers', 'main_category',
                                     'monthly_settlement')
                   if available[k] and not _col(r, k)]
        has_contact = any(_col(r, c) for c in contact_cols)
        if not has_contact:
            missing.append('联系方式')
        if len(missing) >= 2:
            gaps.append({'id': r.get('id'), 'name': r.get('name'),
                         'missing': missing})
        updated = r.get('updated_at') or 0
        try:
            updated = int(updated)
        except (TypeError, ValueError):
            updated = 0
        if updated and now - updated > TALENT_STALE_DAYS * DAY_MS:
            stale.append({'id': r.get('id'), 'name': r.get('name'),
                          'days': (now - updated) // DAY_MS})

    # 重复疑似对（双口径，2026-11-11 老大修订：单口径「0」是假阴性，会骗人）：
    # - dup_sure：同抖音号 = 确定重复；
    # - dup_maybe（严格）：完全同名 + 粉丝差 <5% = 疑似；
    # - dup_loose（宽松）：归一化同名（剥括号后缀）+ 粉丝差 <20% = 疑似，需人工确认。
    by_dy, dup_sure, dup_maybe = {}, [], []
    for r in rows:
        did = str(_col(r, 'douyin_id')).strip()
        if did:
            by_dy.setdefault(did, []).append(r)
    for did, group in by_dy.items():
        if len(group) > 1:
            dup_sure.append({'douyin_id': did,
                             'names': [g.get('name') for g in group]})
    by_name = {}
    for r in rows:
        name = str(_col(r, 'name')).strip()
        if name:
            by_name.setdefault(name, []).append(r)
    for name, group in by_name.items():
        if len(group) > 1:
            fans = [float(g.get('followers') or 0) for g in group]
            if max(fans) > 0 and (max(fans) - min(fans)) / max(fans) < 0.05:
                dup_maybe.append({'name': name, 'count': len(group)})

    # 宽松档：名字像（归一后相似度 ≥0.6）即报，粉丝数只陈列不门槛
    from difflib import SequenceMatcher
    named = [(r, _norm_name(r.get('name'))) for r in rows]
    named = [(r, n) for r, n in named if n]
    seen_pairs = set()
    dup_loose = []
    for i in range(len(named)):
        for j in range(i + 1, len(named)):
            r1, n1 = named[i]
            r2, n2 = named[j]
            if n1 == n2:
                sim = 1.0
            else:
                sim = SequenceMatcher(None, n1, n2).ratio()
            if sim < DUP_LOOSE_NAME_SIM:
                continue
            key = tuple(sorted((r1.get('id') or '', r2.get('id') or '')))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            dup_loose.append({
                'sim': round(sim, 2),
                'names': [str(r1.get('name')).strip(), str(r2.get('name')).strip()],
                'followers': [int(float(r1.get('followers') or 0)),
                              int(float(r2.get('followers') or 0))],
            })
    dup_loose.sort(key=lambda x: -x['sim'])
    dup_loose = dup_loose[:DUP_LOOSE_MAX_REPORT]

    return {'total': len(rows), 'gaps': gaps, 'dup_sure': dup_sure,
            'dup_maybe': dup_maybe, 'dup_loose': dup_loose, 'stale': stale}


def goods_healthcheck(conn):
    """商品库体检：佣金过期 / 字段缺口 / 疑似下架。返回纯 dict。"""
    now = int(time.time() * 1000)
    rows = [dict(r) for r in conn.execute('SELECT * FROM products').fetchall()]

    commission_stale, gaps, suspect_off = [], [], []
    for r in rows:
        synced = r.get('daren_synced_at') or 0
        try:
            synced = int(float(synced))
        except (TypeError, ValueError):
            synced = 0
        updated = r.get('updated_at') or 0
        try:
            updated = int(updated)
        except (TypeError, ValueError):
            updated = 0
        if synced and now - synced > GOODS_COMMISSION_STALE_DAYS * DAY_MS:
            commission_stale.append({'id': r.get('id'), 'name': r.get('name'),
                                     'days': (now - synced) // DAY_MS})
        missing = [label for col, label in
                   (('price', '到手价'), ('commission_rates', '佣金率'),
                    ('category', '类目'), ('shop_score', '店铺分'))
                   if not _col(r, col)]
        if missing:
            gaps.append({'id': r.get('id'), 'name': r.get('name'),
                         'missing': missing})
        no_board = not _col(r, 'board_rank')
        no_7d_sales = r.get('sales_7d') in (None, '')
        if (updated and now - updated > GOODS_OFF_SHELF_DAYS * DAY_MS
                and no_board and no_7d_sales):
            suspect_off.append({'id': r.get('id'), 'name': r.get('name'),
                                'days': (now - updated) // DAY_MS})
    return {'total': len(rows), 'commission_stale': commission_stale,
            'gaps': gaps, 'suspect_off': suspect_off}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True, choices=sorted(AGENT_TABLE))
    ap.add_argument('--json', action='store_true', help='只输出 JSON（agent 解析用）')
    args = ap.parse_args()

    conn = _conn()
    try:
        result = (talent_healthcheck(conn) if args.agent == 'emp_talent_keeper'
                  else goods_healthcheck(conn))
    finally:
        conn.close()
    result['agent'] = args.agent
    result['checked_at'] = time.strftime('%Y-%m-%d %H:%M:%S')

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
