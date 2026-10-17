#!/usr/bin/env python3
"""将 66 条 ok 但仍为 BAAI/bge-large-zh-v1.5 向量的 kb_entries 用 embedding-2 重新向量化。

做法（与上一轮 47 条重建相同）：
  1. _save_kb_chunks_without_embedding(eid, emp_id, content, 500, 100) 重新分段（清旧 chunk）
  2. _vectorize_kb_chunks(...) 用当前生效的 embedding-2 配置向量化

安全：
  - 全程保持 status='ok'
  - 单条失败：从事前备份恢复该条目的旧 chunks，跳过并记录，不中断整体
  - 不碰 embedding-2 已就绪条目、deleted / superseded
"""
import os
import sqlite3
import sys
import time
import traceback

BASE = '/Users/qichen/solobrave-prod'
DB_PATH = os.path.join(BASE, 'data', 'solobrave.db')
BACKUP_PATH = os.path.join(BASE, 'data', 'backups', 'pre-kb-unify-20261001-004829.db')

sys.path.insert(0, BASE)
# 加载 .env（与服务进程一致：SOLOBRAVE_EMBEDDING_*）；文件名带横线，用 importlib
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(BASE, 'solobrave-server.py'))
_server_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_server_mod)
_server_mod._load_dotenv(os.path.join(BASE, '.env'))
import knowledge_service as ks

TARGET_SQL = """
SELECT e.id, e.title, e.emp_id, e.content
FROM kb_entries e
WHERE e.status='ok'
  AND EXISTS (SELECT 1 FROM kb_entry_chunks c
              WHERE c.entry_id=e.id AND c.embedding_model='BAAI/bge-large-zh-v1.5')
  AND NOT EXISTS (SELECT 1 FROM kb_entry_chunks c
                  WHERE c.entry_id=e.id AND c.embedding_model='embedding-2'
                    AND c.embedding IS NOT NULL AND c.embedding != '')
ORDER BY e.id
"""


def snapshot_old_chunks(backup_con, entry_id):
    return backup_con.execute(
        'SELECT id, entry_id, emp_id, chunk_index, content, embedding, embedding_model, created_at '
        'FROM kb_entry_chunks WHERE entry_id=? ORDER BY chunk_index',
        (entry_id,)
    ).fetchall()


def restore_old_chunks(entry_id, old_rows):
    con = sqlite3.connect(DB_PATH, timeout=30)
    try:
        con.execute('DELETE FROM kb_entry_chunks WHERE entry_id=?', (entry_id,))
        con.executemany(
            'INSERT INTO kb_entry_chunks (id, entry_id, emp_id, chunk_index, content, embedding, embedding_model, created_at) '
            'VALUES (?,?,?,?,?,?,?,?)',
            old_rows,
        )
        con.execute('UPDATE kb_entries SET chunk_count=? WHERE id=?', (len(old_rows), entry_id))
        con.commit()
    finally:
        con.close()


def main():
    cfg = ks.get_embedding_config(None)
    print(f"[cfg] provider={cfg['provider']} model={cfg['model']} baseUrl={cfg['baseUrl']} key={'set' if cfg['apiKey'] else 'EMPTY'}")
    if cfg['model'] != 'embedding-2':
        print('[ABORT] 当前生效 embedding 模型不是 embedding-2，停止')
        sys.exit(1)

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    targets = con.execute(TARGET_SQL).fetchall()
    con.close()
    print(f'[target] {len(targets)} entries')
    if not (0 < len(targets) <= 66):
        print('[ABORT] 名单条数异常，停止')
        sys.exit(1)

    backup_con = sqlite3.connect(BACKUP_PATH)
    backup_con.row_factory = sqlite3.Row

    ok_n, fail_n = 0, 0
    failures = []
    for i, row in enumerate(targets, 1):
        eid, title, emp_id, content = row['id'], row['title'], row['emp_id'] or '', row['content']
        old_rows = [tuple(r) for r in snapshot_old_chunks(backup_con, eid)]
        if not old_rows:
            print(f'[{i}/66] {eid} 备份中无旧 chunks，跳过以防丢数')
            fail_n += 1
            failures.append((eid, title, 'no old chunks in backup'))
            continue
        try:
            ks._save_kb_chunks_without_embedding(eid, emp_id, content, 500, 100)
            ks._vectorize_kb_chunks(eid, emp_id, cfg['apiKey'], cfg['provider'],
                                    cfg['model'], base_url=cfg['baseUrl'])
            # 实测确认该条目全部 chunk 为 embedding-2 非空向量
            chk = sqlite3.connect(DB_PATH)
            n_total, n_ok = chk.execute(
                "SELECT COUNT(*), SUM(CASE WHEN embedding_model='embedding-2' AND embedding IS NOT NULL AND length(embedding)>0 THEN 1 ELSE 0 END) "
                'FROM kb_entry_chunks WHERE entry_id=?', (eid,)
            ).fetchone()
            chk.close()
            if n_total == 0 or n_ok != n_total:
                raise RuntimeError(f'post-check mismatch total={n_total} embedding2_ok={n_ok}')
            ok_n += 1
            print(f'[{i}/66] OK {eid} chunks={n_total}  {title[:30]}', flush=True)
        except Exception as e:
            fail_n += 1
            reason = f'{type(e).__name__}: {e}'
            try:
                restore_old_chunks(eid, old_rows)
                reason += '; old chunks restored from backup'
            except Exception as re:
                reason += f'; RESTORE FAILED: {type(re).__name__}: {re}'
            failures.append((eid, title, reason))
            print(f'[{i}/66] FAIL {eid} {reason}', flush=True)
            traceback.print_exc()
        time.sleep(0.3)

    backup_con.close()
    print('\n========== SUMMARY ==========')
    print(f'success={ok_n} fail={fail_n}')
    for eid, title, reason in failures:
        print(f'  FAIL {eid} | {title[:40]} | {reason}')


if __name__ == '__main__':
    main()
