#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-④ 存量迁移（一次性，2026-11-16）：talent_recommendations 口径整改落地。

四条整改在代码层（buyin_worker 写入 + DDL + check 脚本）已改，存量 2 行本脚本迁：
  ① rec_date 毫秒串 → 'YYYY-MM-DD'：表重建换列亲和（INTEGER→TEXT），
     毫秒值按「当日 00:00 本地时区」还原成日期串；
  ② run_id 回填：11-16 两行 trajectory 实证唯一交付班
     8ca29af4-a0ee-4e28-b94f-c5bb665178b2（09:30 班，36/1 健康，含 7 个 report 事件）；
  ③ pipeline_status pending → recommended（与写入侧初值对齐）。

幂等：跑第二次时 rec_date 已是 TEXT，脚本识别后只报「无需迁移」。
用法：python3 scripts/migrate_b_table_recdate.py [--db PATH] [--apply]
默认 --dry-run（只打印）；--apply 才写（自动备份整库到 ~/.solobrave-backups/）。
"""
import argparse
import datetime as dt
import shutil
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
BACKFILL_RUN_ID = '8ca29af4-a0ee-4e28-b94f-c5bb665178b2'  # 11-16 唯一交付班（trajectory 实证）


def ms_to_ymd(ms):
    return dt.datetime.fromtimestamp(int(ms) / 1000).strftime('%Y-%m-%d')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db, timeout=15)
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='talent_recommendations'"
    ).fetchone()
    if not exists:
        print('表不存在，无需迁移')
        conn.close()
        return 0

    cols = {r[1]: r[2] for r in conn.execute('PRAGMA table_info(talent_recommendations)')}
    rows = conn.execute(
        'SELECT rec_id, rec_date, typeof(rec_date), run_id, pipeline_status'
        ' FROM talent_recommendations').fetchall()
    int_rows = [r for r in rows if r[2] == 'integer']
    if not int_rows and cols.get('rec_date', '').upper() == 'TEXT':
        print('rec_date 已是 TEXT 且无毫秒存量行——无需迁移')
        conn.close()
        return 0

    print(f'待迁移 {len(int_rows)} 行（rec_date 毫秒存量）+ 表重建换列亲和')
    for r in int_rows:
        print(f'  {r[0]}: {r[1]} → {ms_to_ymd(r[1])}  run_id={r[3] or BACKFILL_RUN_ID}'
              f'  pipeline={r[4]} → recommended')
    if not args.apply:
        print('（dry-run，加 --apply 落库）')
        conn.close()
        return 0

    bak = Path.home() / f'.solobrave-backups/solobrave.db.bak-{time.strftime("%Y%m%d-%H%M%S")}-pre-b-table-migrate'
    shutil.copy2(args.db, bak)
    print(f'备份 → {bak}')

    conn.execute('BEGIN IMMEDIATE')
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS talent_recommendations_new (
                rec_id TEXT PRIMARY KEY,
                rec_date TEXT NOT NULL,
                talent_id TEXT NOT NULL,
                talent_name TEXT NOT NULL DEFAULT '',
                matched_rules TEXT NOT NULL DEFAULT '[]',
                reason_text TEXT NOT NULL DEFAULT '',
                snapshot_json TEXT,
                pipeline_status TEXT NOT NULL DEFAULT 'pending',
                deal_pid TEXT,
                gmv REAL,
                refund_flag INTEGER,
                run_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(rec_date, talent_id)
            )''')
        # 日期换算走 Python 本地时区（与写入侧 fromtimestamp 同口径——SQL strftime
        # 的 unixepoch/localtime 在 launchd 环境 TZ 不可控，踩过时区坑，不冒险）
        src = conn.execute(
            'SELECT * FROM talent_recommendations').fetchall()
        keys = [d[0] for d in conn.execute(
            'SELECT * FROM talent_recommendations LIMIT 0').description]
        for r in src:
            d = dict(zip(keys, r))
            rec_date = (ms_to_ymd(d['rec_date'])
                        if isinstance(d['rec_date'], int) else str(d['rec_date']))
            conn.execute('''
                INSERT OR REPLACE INTO talent_recommendations_new
                    (rec_id, rec_date, talent_id, talent_name, matched_rules, reason_text,
                     snapshot_json, pipeline_status, deal_pid, gmv, refund_flag,
                     run_id, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (d['rec_id'], rec_date, d['talent_id'], d['talent_name'],
                 d['matched_rules'], d['reason_text'], d['snapshot_json'],
                 'recommended', d['deal_pid'], d['gmv'], d['refund_flag'],
                 d['run_id'] or BACKFILL_RUN_ID, d['created_at'], d['updated_at']))
        conn.execute('DROP TABLE talent_recommendations')
        conn.execute('ALTER TABLE talent_recommendations_new RENAME TO talent_recommendations')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tr_date ON talent_recommendations(rec_date)')
        conn.commit()
    except Exception:
        conn.execute('ROLLBACK')
        raise
    for r in conn.execute('SELECT rec_id, rec_date, run_id, pipeline_status FROM talent_recommendations'):
        print('迁后:', r)
    conn.close()
    print('迁移完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
