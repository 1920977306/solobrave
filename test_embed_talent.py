#!/usr/bin/env python3
"""单条 talent embedding 端到端测试 (绕过 backfill 框架)"""
import os, re, sys, time

with open('/Users/qichen/solobrave-prod/.env') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([A-Z_][A-Z0-9_]*)=(.*)$', line)
        if m:
            k, v = m.group(1), m.group(2).strip().strip('"').strip("'")
            os.environ[k] = v

sys.path.insert(0, '/Users/qichen/solobrave-prod')
from knowledge_service import get_embedding_cached, get_embedding_config, _db_conn, _save_embedding_cache
import struct

cfg = get_embedding_config()
print('provider:', cfg['provider'], 'model:', cfg['model'])

con = _db_conn()
row = con.execute("SELECT id, name, bio FROM talents WHERE status='active' AND bio != '' LIMIT 1").fetchone()
print('test:', row['id'], row['name'])

# 模拟 backfill 用 get_embedding_cached 路径
content = f"{row['name']} | {row['bio']}"
t0 = time.time()
emb = get_embedding_cached(content, cfg['apiKey'], cfg['provider'], cfg['model'], base_url=cfg['baseUrl'])
t1 = time.time()
print(f'get_embedding_cached: returned={emb is not None}, len={len(emb) if emb else 0}, took={t1-t0:.2f}s')

if emb:
    # 模拟 talents UPDATE
    emb_bytes = struct.pack(f'{len(emb)}f', *emb)
    print(f'emb_bytes: {len(emb_bytes)} bytes')
    t2 = time.time()
    con.execute('UPDATE talents SET embedding = ?, embedding_model = ? WHERE id = ?', (emb_bytes, cfg['model'], row['id']))
    con.commit()
    t3 = time.time()
    print(f'UPDATE+commit: took={t3-t2:.4f}s')
    # verify
    v = con.execute("SELECT LENGTH(embedding) FROM talents WHERE id = ?", (row['id'],)).fetchone()
    print(f'verified DB: embedding size = {v[0]}')
con.close()
print('done')