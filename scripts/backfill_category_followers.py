#!/usr/bin/env python3
"""类目+粉丝缺口写回（dq_3733edc03e03，2026-11-24）——承接单A′ dq_c2ea591004ba 尾巴。

缺口实况（P3 dry-run 分布）：73 人无类目 + 80 人 followers=0（77 人 remark 带
douyin_id_backfilled = 11-23 回填工具写了抖音号没写粉丝/类目，数据源就在当时的
apply 报告里）。本工具从回填报告的 proposed 命中候选补写两列。

纪律（spec 原文）：
- --dry-run 先出分布报告（默认），不写库；
- 同名多人（fanmatch）只用命中候选值，并在 remark 带来源标记（category_
  backfilled_via=fanmatch / exact 等）；
- --apply 自动备份（表 CSV + 整库副本）+ 回读验证；
- 真没有就登记不造值；
- ★ INTEGER 空值口径：followers 是 INTEGER 列，判缺口用 =0（IS NULL 对
  INTEGER 列恒空集 = 假清零）；category 是 TEXT 列用 IS NULL OR ''。统计必须
  对 remark 分桶（回填标记/自然零），不许混为一谈。
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / 'data' / 'solobrave.db'
REPORT = ROOT / 'data' / 'douyin-id-backfill-report-20261123-1437.json'
MARKER = 'category_followers_backfilled'


def _proposed_candidate(entry):
    """回填报告 proposed（抖音号字符串）→ 命中候选 dict；无匹配返 None。"""
    if not entry:
        return None
    p = entry.get('proposed')
    cands = entry.get('candidates') or []
    return next((c for c in cands if str(c.get('抖音号')) == str(p)), None)


def gap_stats(conn):
    """缺口总账（INTEGER 口径纪律 + remark 分桶）。"""
    rows = conn.execute(
        "SELECT id, category, followers, remark FROM talents").fetchall()
    n_cat = n_fol = 0
    fol_buckets = {'回填标记仍 0': 0, '自然零': 0}
    for _id, cat, fol, rm in rows:
        if cat is None or cat == '':
            n_cat += 1
        if fol == 0:                      # INTEGER 列：=0 是真空值口径
            fol_buckets['回填标记仍 0' if 'douyin_id_backfilled' in (rm or '')
                       else '自然零'] += 1
            n_fol += 1
    return n_cat, n_fol, fol_buckets


def plan(conn, report_path=REPORT):
    """补写计划：逐缺口达人取 proposed 命中候选的类目/粉丝。"""
    rep = json.loads(Path(report_path).read_text(encoding='utf-8'))
    by_tid = {x['talent_id']: x for x in (rep.get('searched') or [])}
    plan_rows = []
    for tid, cat, fol, rm in conn.execute(
            'SELECT id, category, followers, remark FROM talents'):
        need_cat = cat is None or cat == ''
        need_fol = fol == 0
        if not (need_cat or need_fol):
            continue
        entry = by_tid.get(tid)
        cand = _proposed_candidate(entry)
        if not cand:
            plan_rows.append({'talent_id': tid, 'need_cat': need_cat,
                              'need_fol': need_fol, 'skip': '报告无命中候选'})
            continue
        cats = cand.get('主推类目') or []
        fans = cand.get('粉丝') or 0
        # 空串候选（['']）不是值：join 前过滤，过滤后为空 ⇒ 真没有，不造值
        joined = '/'.join(str(c).strip() for c in cats if str(c).strip())
        new_cat = joined if (need_cat and joined) else None
        new_fol = int(fans) if (need_fol and fans) else None
        if new_cat is None and new_fol is None:
            plan_rows.append({'talent_id': tid, 'need_cat': need_cat,
                              'need_fol': need_fol, 'skip': '候选值空（真没有，不造）'})
            continue
        plan_rows.append({'talent_id': tid, 'need_cat': need_cat,
                          'need_fol': need_fol, 'cat': new_cat,
                          'fol': new_fol,
                          'via': (entry.get('match_how') or 'exact')})
    return plan_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=str(DB))
    ap.add_argument('--report', default=str(REPORT))
    ap.add_argument('--apply', action='store_true', help='实际写库（默认 dry-run）')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    n_cat, n_fol, buckets = gap_stats(conn)
    print(f'缺口总账：无类目 {n_cat} | followers=0 {n_fol}（分桶 {buckets}）')

    rows = plan(conn, args.report)
    ok = [r for r in rows if 'skip' not in r]
    skip = [r for r in rows if 'skip' in r]
    n_cat_fix = sum(1 for r in ok if r['cat'])
    n_fol_fix = sum(1 for r in ok if r['fol'])
    print(f'补写计划：可补 {len(ok)} 人（类目 {n_cat_fix}、粉丝 {n_fol_fix}）；'
          f'不可补 {len(skip)} 人（真没有，登记不造值）')
    from collections import Counter
    print('不可补原因:', dict(Counter(r['skip'] for r in skip)))
    print('来源分布:', dict(Counter(r['via'] for r in ok)))

    if not args.apply:
        print('\n(dry-run：加 --apply 写库。apply 自动备份+回读验证)')
        conn.close()
        return

    ts = time.strftime('%Y%m%d-%H%M%S')
    csv_bak = f'/tmp/solobrave-talents-backup-{ts}.csv'
    with open(csv_bak, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['id', 'category', 'followers', 'remark'])
        for r in conn.execute('SELECT id, category, followers, remark FROM talents'):
            w.writerow(r)
    db_bak = f'/tmp/solobrave-backup-{ts}.db'
    shutil.copy2(args.db, db_bak)
    print(f'备份：表 CSV {csv_bak} + 整库 {db_bak}')

    done_cat = done_fol = 0
    for r in ok:
        sets, vals = [], []
        if r['cat']:
            sets.append('category = ?')
            vals.append(r['cat'])
            done_cat += 1
        if r['fol']:
            sets.append('followers = ?')
            vals.append(r['fol'])
            done_fol += 1
        sets.append('updated_at = ?')
        vals.append(int(time.time() * 1000))
        cur = conn.execute(
            f'UPDATE talents SET {", ".join(sets)} WHERE id = ?',
            vals + [r['talent_id']])
        if cur.rowcount:
            _stamp_source(conn, r['talent_id'], r['via'])
    conn.commit()

    # 回读验证（同口径复查）
    v_cat, v_fol, _ = gap_stats(conn)
    print(f'回读验证：无类目 {n_cat}→{v_cat} | followers=0 {n_fol}→{v_fol}')
    assert v_cat == n_cat - done_cat or v_cat < n_cat, '类目缺口应收窄'
    assert v_fol <= n_fol - done_fol + sum(
        1 for r in ok if r['fol'] and not r['need_fol']), '粉丝缺口应收窄'
    conn.close()
    print(f'写回完成：类目 {done_cat} 人、粉丝 {done_fol} 人（来源标记入 remark）。')


def _stamp_source(conn, tid, via):
    """remark 追加来源标记（幂等：已标不重复）。"""
    row = conn.execute('SELECT remark FROM talents WHERE id = ?', (tid,)).fetchone()
    try:
        rm = json.loads((row[0] if row else '') or '{}') or {}
    except Exception:
        rm = {'_raw': row[0] if row else ''}
    if MARKER in rm:
        return
    rm[MARKER] = {'via': via, 'at': int(time.time() * 1000),
                  'source': 'douyin-id-backfill-report-20261123-1437'}
    conn.execute('UPDATE talents SET remark = ? WHERE id = ?',
                 (json.dumps(rm, ensure_ascii=False), tid))


if __name__ == '__main__':
    main()
