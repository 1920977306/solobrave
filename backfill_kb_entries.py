#!/usr/bin/env python3
"""kb_entries 数据回填 (dev/feat: kb_entries 修复 #2).

策略:
  1. 64 行 emp_id 空的 orphan, 默认归属 emp_1780199176680 (出现 46 次的主 agent)
  2. 133 行 created_by 空的, 标 'system:pre_fix' (修复前历史数据, 不混入未来 'auto:' 路径)

执行: python3 backfill_kb_entries.py (dry-run 默认, --apply 真正改库)
"""
import argparse
import os
import sqlite3
import sys

DB_PATH = '/Users/qichen/solobrave-prod/data/solobrave.db'
DEFAULT_AGENT_ID = 'emp_1780199176680'
DEFAULT_CREATED_BY = 'system:pre_fix'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正改库')
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # 1. orphan (emp_id 空) — kb_entries 没有 'active', 只有 ok/pending/deleted/superseded
        orphans = con.execute(
            "SELECT id, title, emp_id, created_by FROM kb_entries "
            "WHERE (emp_id IS NULL OR emp_id='') AND status NOT IN ('deleted', 'superseded')"
        ).fetchall()
        print(f'  [orphan emp_id 空] {len(orphans)} 行')
        for r in orphans[:5]:
            print(f'    {r["id"]}  title={r["title"][:40]!r}')
        if len(orphans) > 5:
            print(f'    ... +{len(orphans)-5} more')

        # 2. created_by 空
        no_creator = con.execute(
            "SELECT COUNT(*) AS n FROM kb_entries WHERE created_by IS NULL OR created_by=''"
        ).fetchone()
        print(f'\n  [created_by 空] {no_creator["n"]} 行')

        if not args.apply:
            print('\n  [DRY-RUN] 加 --apply 真正改库')
            return

        import time as _t
        now_ms = int(_t.time() * 1000)
        # 1. 回填 emp_id (排除 deleted/superseded)
        if orphans:
            cur = con.execute(
                "UPDATE kb_entries SET emp_id = ?, updated_at = ? "
                "WHERE (emp_id IS NULL OR emp_id='') AND status NOT IN ('deleted', 'superseded')",
                (DEFAULT_AGENT_ID, now_ms),
            )
            print(f'\n  [done] emp_id: {cur.rowcount} 行 -> {DEFAULT_AGENT_ID}')
        # 2. 回填 created_by (避免覆盖有值的行, 只填空)
        cur2 = con.execute(
            "UPDATE kb_entries SET created_by = ? "
            "WHERE created_by IS NULL OR created_by = ''",
            (DEFAULT_CREATED_BY,),
        )
        print(f'  [done] created_by: {cur2.rowcount} 行 -> {DEFAULT_CREATED_BY!r}')
        con.commit()

        # 3. 验证
        print('\n  [verify]')
        still_orphan = con.execute(
            "SELECT COUNT(*) AS n FROM kb_entries WHERE (emp_id IS NULL OR emp_id='') "
            "AND status NOT IN ('deleted', 'superseded')"
        ).fetchone()['n']
        still_no_creator = con.execute(
            "SELECT COUNT(*) AS n FROM kb_entries WHERE created_by IS NULL OR created_by=''"
        ).fetchone()['n']
        print(f'    仍 emp_id 空: {still_orphan}')
        print(f'    仍 created_by 空: {still_no_creator}')
    finally:
        con.close()


if __name__ == '__main__':
    main()