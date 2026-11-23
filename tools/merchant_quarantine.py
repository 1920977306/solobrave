#!/usr/bin/env python3
"""商家号隔离 · 存量打标 + 污染清理（dq_36f8a1236e79 一次性配套）

动作（幂等，重复跑安全）：
  1. 整库备份到 /tmp（--apply 时）
  2. 10 个商家号 talents.remark JSON 合并 "merchant": true（只标不删）
  3. product_talent_match 解除挂载（先 CREATE TABLE ..._backup_<ts> 备份，再删）
  4. B 表历史行标注不删：reason_text 前缀【商家号已隔离】+ snapshot_json 加
     "merchant_quarantined": true + pipeline_status 置 'quarantined'

词表与名单的唯一真源 = tools/talent_merchant.py（与 worker/server 同一份）。

用法：
  python3 tools/merchant_quarantine.py            # dry-run（默认，一个字不写）
  python3 tools/merchant_quarantine.py --apply    # 备份 + 执行 + 回读验证
"""
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import talent_merchant as tm

DB = Path(__file__).resolve().parent.parent / 'data' / 'solobrave.db'
QUAR_PREFIX = '【商家号已隔离】'


def _backup_db(ts: str) -> str:
    dst = f'/tmp/solobrave-merchant-backup-{ts}.db'
    shutil.copy2(DB, dst)
    return dst


def _mark_talents(conn, dry_run: bool):
    rows = conn.execute("SELECT id, name, remark FROM talents").fetchall()
    targets = [(r[0], r[1], r[2]) for r in rows if tm.is_merchant({'name': r[1]})]
    print(f'[1] 商家号打标：词表命中 {len(targets)} 人')
    for tid, name, remark in targets:
        try:
            meta = json.loads(remark) if remark else {}
        except (TypeError, ValueError):
            meta = {'_unparsed_remark': remark or ''}
        if meta.get('merchant') is True:
            print(f'    - {name}({tid}) 已标 merchant，跳过')
            continue
        meta['merchant'] = True
        if not dry_run:
            conn.execute('UPDATE talents SET remark = ? WHERE id = ?',
                         (json.dumps(meta, ensure_ascii=False), tid))
        print(f'    {"[dry]" if dry_run else "[标]"} {name}({tid})')
    return len(targets)


def _unmount_matches(conn, ts: str, dry_run: bool):
    rows = conn.execute(
        "SELECT m.id, m.talent_id, t.name FROM product_talent_match m "
        "JOIN talents t ON t.id = m.talent_id").fetchall()
    targets = [(r[0], r[1], r[2]) for r in rows
               if tm.is_merchant({'name': r[2]})]
    print(f'[2] 匹配解除挂载：命中 {len(targets)} 行 / {len({t[1] for t in targets})} 人')
    if targets and not dry_run:
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS "product_talent_match_backup_{ts}" '
            'AS SELECT * FROM product_talent_match')
        print(f'    备份表 product_talent_match_backup_{ts} 已建')
    for mid, tid, name in targets:
        if not dry_run:
            conn.execute('DELETE FROM product_talent_match WHERE id = ?', (mid,))
        print(f'    {"[dry]" if dry_run else "[删]"} {name} 挂载 {mid}')
    return len(targets)


def _annotate_b_table(conn, dry_run: bool):
    rows = conn.execute(
        "SELECT rec_id, talent_id, talent_name, reason_text, snapshot_json "
        "FROM talent_recommendations").fetchall()
    targets = [r for r in rows if tm.is_merchant({'name': r[2]})]
    print(f'[3] B 表历史行标注：命中 {len(targets)} 行（不删，只标注）')
    for rec_id, tid, name, reason, snap in targets:
        new_reason = reason if (reason or '').startswith(QUAR_PREFIX) \
            else QUAR_PREFIX + (reason or '')
        try:
            snap_obj = json.loads(snap) if snap else {}
        except (TypeError, ValueError):
            snap_obj = {'_unparsed': snap or ''}
        snap_obj['merchant_quarantined'] = True
        if not dry_run:
            conn.execute(
                'UPDATE talent_recommendations SET reason_text = ?, '
                'snapshot_json = ?, pipeline_status = ? WHERE rec_id = ?',
                (new_reason, json.dumps(snap_obj, ensure_ascii=False),
                 'quarantined', rec_id))
        print(f'    {"[dry]" if dry_run else "[注]"} {name}({rec_id})')
    return len(targets)


def _verify(conn):
    print('[4] 回读验证：')
    still = conn.execute(
        "SELECT count(*) FROM product_talent_match m JOIN talents t "
        "ON t.id = m.talent_id").fetchone()[0]
    marked = conn.execute(
        "SELECT count(*) FROM talents WHERE remark LIKE '%\"merchant\": true%'").fetchone()[0]
    b_left = conn.execute(
        "SELECT count(*) FROM talent_recommendations r JOIN talents t "
        "ON t.id = r.talent_id "
        "WHERE t.name LIKE '%有限公司%' OR t.name LIKE '%工厂%' "
        "OR t.name LIKE '%总店%'").fetchone()[0]
    print(f'    匹配表剩余总行数 = {still}（商家行应为 0，下面逐人复核）')
    print(f'    remark 带 merchant:true 人数 = {marked}')
    print(f'    B 表疑似商家名残留行 = {b_left}')


def main():
    dry_run = '--apply' not in sys.argv
    ts = time.strftime('%Y%m%d-%H%M%S')
    print(f'模式: {"DRY-RUN（不写库）" if dry_run else "APPLY"}  基点库: {DB}')
    conn = sqlite3.connect(DB)
    try:
        if not dry_run:
            bak = _backup_db(ts)
            print(f'整库备份 → {bak}')
        n1 = _mark_talents(conn, dry_run)
        n2 = _unmount_matches(conn, ts, dry_run)
        n3 = _annotate_b_table(conn, dry_run)
        if not dry_run:
            conn.commit()
            print('已 commit')
        _verify(conn)
        print(f'汇总：打标 {n1} 人 / 解挂 {n2} 行 / B 表标注 {n3} 行')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
