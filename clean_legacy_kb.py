#!/usr/bin/env python3
"""清空旧 knowledge 表 + knowledge_chunks 表，防止已删条目被恢复"""
import sqlite3, os, shutil

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'solobrave.db')
conn = sqlite3.connect(DB_PATH)

# 先看下旧表有多少条
old_count = conn.execute('SELECT COUNT(*) FROM knowledge').fetchone()[0]
old_chunks = conn.execute('SELECT COUNT(*) FROM knowledge_chunks').fetchone()[0] if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='knowledge_chunks'").fetchone() else 0
new_count = conn.execute('SELECT COUNT(*) FROM kb_entries').fetchone()[0]

print(f'旧 knowledge 表: {old_count} 条')
print(f'旧 knowledge_chunks 表: {old_chunks} 条')
print(f'新 kb_entries 表: {new_count} 条')

# 确认新表有数据才清旧表
if new_count == 0:
    print('⚠️ kb_entries 为空，不清空旧表！请先确认迁移完成。')
    conn.close()
    exit(1)

# 备份旧表数据
backup_path = DB_PATH + '.bak.knowledge_cleanup'
if not os.path.exists(backup_path):
    shutil.copy2(DB_PATH, backup_path)
    print(f'已备份: {backup_path}')
else:
    print(f'备份已存在: {backup_path}')

# 清空旧表
conn.execute('DELETE FROM knowledge_chunks')
conn.execute('DELETE FROM knowledge')
conn.commit()

# 验证
after_old = conn.execute('SELECT COUNT(*) FROM knowledge').fetchone()[0]
after_new = conn.execute('SELECT COUNT(*) FROM kb_entries').fetchone()[0]
print(f'清理后 — 旧 knowledge 表: {after_old} 条, kb_entries 表: {after_new} 条')
conn.close()
print('✅ 完成')
