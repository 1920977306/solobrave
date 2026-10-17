#!/usr/bin/env python3
"""
阶段一：将旧 knowledge 表中「独有且有效」的 8 条 ok 知识迁移到 kb_entries。
- 新条目 id = kbmig_<旧id 去前缀>，source 标记 category='legacy_migration'
- 正文头部附 [旧表迁移·原分类=xxx] 说明行，保留原 category 可追溯
- emp_id 沿用旧值，scope 沿用旧值，status='ok'
- 分段 500/overlap 100（_save_kb_chunks_without_embedding）
- 向量化 embedding-2（_vectorize_kb_chunks）
- 幂等：已存在 kbmig_ 条目则跳过
回退方式：
  SELECT * FROM kb_entries WHERE category='legacy_migration';
  DELETE FROM kb_entry_chunks WHERE entry_id LIKE 'kbmig_%';
  DELETE FROM kb_entries WHERE category='legacy_migration';
"""
import os, sys, json, sqlite3, time

# 1. 显式加载 .env（与生产服务同配置源）
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
with open(env_path) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        if line.startswith('SOLOBRAVE_EMBEDDING_'):
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

import knowledge_service as ks

# 2. 盘点结论：迁移这 8 条（旧表独有且有召回价值）
MIGRATE_IDS = [
    'kb_d3647094',  # 方脸小妹货盘结构与出单甜区
    'kb_f4493a6e',  # 赵西瓜达人档案（基本盘事实）
    'kb_bac9ab4f',  # 达人档案：小钢牙阡阡（结构化全档）
    'kb_c6ed22d1',  # COOLCHAP中高价位鞋靴达人合作策略与案例
    'kb_0eedd7f8',  # 安娜轻奢路线达人画像与投放打法
    'kb_d9f17270',  # 崔思瑶（Kiko）真实货盘画像
    'kb_1a323c6b',  # 达人-产品匹配度评估参考（方法论）
    'kb_54d64bb3',  # 松糕拖鞋达人合作评估要点
]

LEFTOVER_NOTE = {
    'kb_d3647094': '方脸小妹',
    'kb_f4493a6e': '赵西瓜',
    'kb_bac9ab4f': '小钢牙阡阡',
    'kb_c6ed22d1': 'COOLCHAP鞋靴策略',
    'kb_0eedd7f8': '安娜轻奢',
    'kb_d9f17270': '崔思瑶Kiko',
    'kb_1a323c6b': '匹配度评估方法论',
    'kb_54d64bb3': '松糕拖鞋评估',
}

conn = ks._db_conn()
try:
    rows = conn.execute(
        f"SELECT * FROM knowledge WHERE status='ok' AND id IN ({','.join('?'*len(MIGRATE_IDS))})",
        MIGRATE_IDS
    ).fetchall()
finally:
    conn.close()
rows = {r['id']: r for r in rows}
missing = [i for i in MIGRATE_IDS if i not in rows]
if missing:
    print('FATAL: missing old rows:', missing); sys.exit(1)

emb_cfg = ks.get_embedding_config(None)
assert emb_cfg['model'] == 'embedding-2', f"unexpected model {emb_cfg['model']}"
print(f"[cfg] provider={emb_cfg['provider']} model={emb_cfg['model']} baseUrl={emb_cfg['baseUrl']}")

results = []
for idx, old_id in enumerate(MIGRATE_IDS, 1):
    row = rows[old_id]
    new_id = 'kbmig_' + old_id.replace('kb_', '')
    conn = ks._db_conn()
    try:
        exist = conn.execute('SELECT 1 FROM kb_entries WHERE id=?', (new_id,)).fetchone()
    finally:
        conn.close()
    if exist:
        print(f'[{idx}/8] SKIP (exists) {new_id}')
        results.append((old_id, new_id, 'skip'))
        continue

    title = (row['title'] or '').strip()
    content = (row['content'] or '').strip()
    emp_id = row['emp_id'] or ''
    scope = row['scope'] or 'global'
    team_id = row['team_id'] or ''
    group_ids = row['group_ids'] or '[]'
    old_cat = row['category'] or ''
    now = ks._now_ms()

    # 头部加迁移标记行（可追溯原分类）
    marked_content = f"> [旧表迁移] 本条由旧 knowledge 表迁移，原分类「{old_cat or '无'}」，原ID {old_id}。\n\n{content}"

    conn = ks._db_conn()
    try:
        conn.execute('''
            INSERT INTO kb_entries
            (id, title, content, category, scope, team_id, group_ids, emp_id, status, chunk_count, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'legacy_migration', ?, ?, ?, ?, 'pending', 0, 'legacy_migration', ?, ?)
        ''', (new_id, title, marked_content, scope, team_id, group_ids, emp_id, now, now))
        conn.commit()
    finally:
        conn.close()

    try:
        ks._save_kb_chunks_without_embedding(new_id, emp_id, marked_content, 500, 100)
        ks._vectorize_kb_chunks(new_id, emp_id, emb_cfg['apiKey'], emb_cfg['provider'],
                                emb_cfg['model'], base_url=emb_cfg['baseUrl'])
        conn = ks._db_conn()
        try:
            cur = conn.execute('SELECT chunk_count FROM kb_entries WHERE id=?', (new_id,)).fetchone()
            cc = cur['chunk_count']
            conn.execute("UPDATE kb_entries SET status='ok', updated_at=? WHERE id=?", (now, new_id))
            conn.commit()
        finally:
            conn.close()
        ks.kb_entry_log_operation(new_id, 'create', 'legacy_migration', {
            'source': 'old_knowledge', 'old_id': old_id, 'old_category': old_cat,
        })
        print(f'[{idx}/8] OK {new_id} chunks={cc} title={title[:36]}')
        results.append((old_id, new_id, f'ok chunks={cc}'))
    except Exception as e:
        print(f'[{idx}/8] FAIL {new_id}: {e}')
        conn = ks._db_conn()
        try:
            conn.execute("UPDATE kb_entries SET status='embedding_failed' WHERE id=?", (new_id,))
            conn.commit()
        finally:
            conn.close()
        results.append((old_id, new_id, f'FAIL {e}'))

print('\n===== SUMMARY =====')
for r in results:
    print(r)
