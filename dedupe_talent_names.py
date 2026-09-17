#!/usr/bin/env python3
"""talents name 重复去重 (dev/feat: talents 修复 #4).

策略: 同 name 的多条 active 行, 保留 created_at 最新 1 条 (primary),
其它标 status='archived' (talents.status 主状态, 不影响 cooperation_status).

执行: python3 dedupe_talent_names.py (dry-run 默认, --apply 真正改库)
"""
import argparse
import os
import sqlite3
import sys

DB_PATH = '/Users/qichen/solobrave-prod/data/solobrave.db'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正 UPDATE, 默认 dry-run')
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # 找出 active 重复 name
        dupes = con.execute("""
            SELECT name, COUNT(*) AS n FROM talents
            WHERE status = 'active' AND name IS NOT NULL AND name != ''
            GROUP BY name HAVING COUNT(*) > 1
            ORDER BY name
        """).fetchall()
        print(f'  发现 {len(dupes)} 个重复 name (共 {sum(d["n"] for d in dupes)} 条):')
        archived_ids = []
        for d in dupes:
            rows = con.execute(
                "SELECT id, name, douyin_id, created_at, updated_at, followers, status "
                "FROM talents WHERE name = ? AND status = 'active' "
                # 综合排序: 有 douyin_id 优先 (真主) > followers 大 > updated_at 新 > created_at 新
                # SQLite CASE: douyin_id 非空 → 0 (排前), 空 → 1
                "ORDER BY (CASE WHEN douyin_id IS NOT NULL AND douyin_id != '' THEN 0 ELSE 1 END) ASC, "
                "         followers DESC, updated_at DESC, created_at DESC",
                (d['name'],),
            ).fetchall()
            print(f'    {d["name"]!r} ({d["n"]} 条):')
            for i, r in enumerate(rows):
                tag = '   KEEP' if i == 0 else 'ARCHIVE'
                douyin = r["douyin_id"] or "(空)"
                print(f'      [{tag}] {r["id"]}  douyin_id={douyin:<15}  followers={r["followers"]:>8}  updated_at={r["updated_at"]}')
                if i > 0:
                    archived_ids.append((r['id'], d['name']))
        print()
        print(f'  将 archived {len(archived_ids)} 条')
        if not args.apply:
            print('  [DRY-RUN] 加 --apply 真正改库')
            return
        if not archived_ids:
            return
        import time as _t
        now_ms = int(_t.time() * 1000)
        for tid, name in archived_ids:
            con.execute(
                "UPDATE talents SET status = 'archived', updated_at = ? WHERE id = ?",
                (now_ms, tid),
            )
        con.commit()
        print(f'  [DONE] archived {len(archived_ids)} 行')
    finally:
        con.close()


if __name__ == '__main__':
    main()