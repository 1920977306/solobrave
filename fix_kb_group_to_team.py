#!/usr/bin/env python3
"""
修复 kb_entries 中 scope='group' 的历史数据。
系统当前只支持 global/personal/team，把这 20 条项目组级别数据统一更新为 team。
用法：在测试/生产服务器上执行
  python3 fix_kb_group_to_team.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'solobrave.db')

TARGET_IDS = [
    'know_0ae22070', 'know_f0f42d16', 'know_731d06eb', 'know_2bc88e37', 'know_e2b02d7e',
    'know_168edeb1', 'know_0f90da27', 'know_fcec6add', 'know_f4064db0', 'know_e4b857ff',
    'know_a083e944', 'know_04c8c291', 'know_311d6feb', 'know_07aba5df', 'know_daa444d8',
    'know_e2df2567', 'know_08bea75b', 'know_457c1120', 'know_1ea18f32', 'know_374bc2e7',
]


def main():
    if not os.path.exists(DB_PATH):
        print(f'Database not found: {DB_PATH}')
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        # 更新前统计
        before_total = conn.execute("SELECT COUNT(*) FROM kb_entries").fetchone()[0]
        before_group = conn.execute("SELECT COUNT(*) FROM kb_entries WHERE scope='group'").fetchone()[0]
        print(f'Before update: total={before_total}, group={before_group}')

        placeholders = ','.join('?' for _ in TARGET_IDS)
        cur = conn.execute(
            f"UPDATE kb_entries SET scope='team' WHERE id IN ({placeholders})",
            TARGET_IDS
        )
        conn.commit()
        print(f'Updated rows: {cur.rowcount}')

        # 更新后统计
        after_group = conn.execute("SELECT COUNT(*) FROM kb_entries WHERE scope='group'").fetchone()[0]
        after_team = conn.execute("SELECT COUNT(*) FROM kb_entries WHERE scope='team'").fetchone()[0]
        print(f'After update: group={after_group}, team={after_team}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
