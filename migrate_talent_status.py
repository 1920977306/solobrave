#!/usr/bin/env python3
"""talents 表 cooperation_status 一次性迁移脚本 (dev/feat: talents 审计 #1).

历史 DB 中英混杂 13 种状态值, 全部通过 _normalize_cooperation_status 收敛为
6 种内部 enum (available/cooperating/communicating/following/blacklist/resting/archived).

执行: python3 migrate_talent_status.py
幂等: 重复执行不会有副作用 (已收敛的值在 alias 里命中自己).
"""

import os
import sqlite3
import sys

# 直接连 DB 避免 import solobrave-server.py 触发 main()/cron
DB_PATH = os.environ.get('SOLOBRAVE_DB', '/Users/qichen/solobrave-prod/data/solobrave.db')

# 与 solobrave-server.py:_TALENT_COOPERATION_ALIASES 完全一致 (改 alias 时两边同步!)
_TALENT_COOPERATION_ALIASES = {
    'available': 'available',
    'cooperating': 'cooperating',
    'communicating': 'communicating',
    'following': 'following',
    'blacklist': 'blacklist',
    'resting': 'resting',
    'archived': 'archived',
    '可合作': 'available', '可开发票': 'available', '可邀约': 'available', '发送邀约': 'available',
    '沟通中': 'communicating', '在线沟通': 'communicating',
    '待触达': 'communicating', '待跟进': 'communicating',
    '已合作': 'cooperating', '潜在合作': 'cooperating', '高效履约': 'cooperating',
    '关注': 'following',
    '黑名单': 'blacklist',
    '暂休': 'resting',
    '归档': 'archived',
}


def _normalize(raw, default='available'):
    if raw is None:
        return default
    key = str(raw).strip()
    if not key:
        return default
    return _TALENT_COOPERATION_ALIASES.get(key, default)


def main():
    if not os.path.exists(DB_PATH):
        print(f'  DB not found: {DB_PATH}', file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 1. 迁移前 audit
        before = conn.execute(
            'SELECT cooperation_status, COUNT(*) AS n FROM talents GROUP BY cooperation_status ORDER BY n DESC'
        ).fetchall()
        print('  [before]')
        for r in before:
            print(f'    {r["cooperation_status"]!r:<20} {r["n"]}')

        # 2. 全部 SELECT 出来 → normalize → UPDATE
        rows = conn.execute('SELECT id, cooperation_status FROM talents').fetchall()
        updated = 0
        skipped = 0
        for r in rows:
            old = r['cooperation_status']
            new = _normalize(old)
            if old != new:
                conn.execute(
                    'UPDATE talents SET cooperation_status = ?, updated_at = ? WHERE id = ?',
                    (new, int(__import__('time').time() * 1000), r['id']),
                )
                updated += 1
            else:
                skipped += 1
        conn.commit()

        # 3. 迁移后 audit
        after = conn.execute(
            'SELECT cooperation_status, COUNT(*) AS n FROM talents GROUP BY cooperation_status ORDER BY n DESC'
        ).fetchall()
        print(f'  [after]  updated={updated}, already-normalized={skipped}')
        for r in after:
            print(f'    {r["cooperation_status"]!r:<20} {r["n"]}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()