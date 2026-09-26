# -*- coding: utf-8 -*-
"""
RAG 索引完整性单测 (refactor/rag-index-integrity)

策略: 直接 import knowledge_service (产品函数 globals 永远是 ks.__dict__,
stub 注入真模块 dict 让产品代码自然命中). 弃用 ns 复制 + types.FunctionType
rebind 间接注入 (两张皮 — stub 注入 ns, 产品代码看 ks.__dict__, 13 个内部调
_db_conn 的用例全 NameError).

测试场景 (跟 commit message 对齐):
1. verify 检测 4 类不一致 (missing_chunks / chunk_count_mismatch / model_drift / orphan_chunks)
2. repair dry-run 模式: 返回 actions_planned 但不真改
3. repair confirm 模式: 删孤儿 + 重建 entries 实际生效
4. repair 重建失败时 entry 标 'error', 其他 entry 不受影响

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-mini-test-repair && python3 -m pytest tests/test_rag_index.py -v
"""
import os, sys, json, sqlite3, time, re
import unittest
import knowledge_service as ks  # ★ fix/mini-test-code-repair-20260925 19:17: 直接 import, 产品函数 globals 永远是 ks.__dict__


# ★ fix/mini-test-code-repair-20260925 工单 FINAL 17:27: 产品代码 close() 不生效代理.
# 治根因: 产品代码 (kb_entry_cleanup_dangling / kb_entries_reindex_pending)
# 在事务结束后调 self._db_conn().close() 关连接, 但测试下一 setUp 又开
# 新 in-memory 连接, 新连接被产品 close 抛 'Cannot operate on a closed database'.
class _ImmuneConn:
    def __init__(self, real):
        self._real = real
    def close(self):
        pass  # 产品代码 close 不生效
    def __getattr__(self, name):
        return getattr(self._real, name)


# 初始化测试表 (照抄 knowledge_service.init_db() 真实 schema 含 emp_id)
def init_test_tables(conn):
    conn.execute('DROP TABLE IF EXISTS kb_entries')
    conn.execute('DROP TABLE IF EXISTS kb_entry_chunks')
    conn.execute('DROP TABLE IF EXISTS kb_operation_log')
    conn.execute('''
        CREATE TABLE kb_entries (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            category TEXT DEFAULT '',
            category_id INTEGER,
            project_id TEXT DEFAULT '',
            scope TEXT DEFAULT 'global',
            team_id TEXT DEFAULT '',
            group_ids TEXT DEFAULT '[]',
            emp_id TEXT DEFAULT '',
            status TEXT DEFAULT 'ok',
            chunk_count INTEGER DEFAULT 0,
            created_by TEXT DEFAULT '',
            created_at INTEGER,
            updated_at INTEGER
        )
    ''')
    conn.execute('''
        CREATE TABLE kb_entry_chunks (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL,
            emp_id TEXT DEFAULT '',
            chunk_index INTEGER,
            content TEXT NOT NULL,
            embedding BLOB,
            embedding_model TEXT DEFAULT '',
            created_at INTEGER
        )
    ''')
    conn.execute('''
        CREATE TABLE kb_operation_log (
            id TEXT PRIMARY KEY,
            entry_id TEXT,
            operation TEXT NOT NULL,
            operator_id TEXT DEFAULT '',
            details TEXT DEFAULT '{}',
            created_at INTEGER
        )
    ''')
    conn.commit()


def _insert_entry(db, eid, title='T', content='C', status='ok', chunk_count=0):
    """helper: 插入测试 entry (上一轮 commit 17 加 db 形参保留)."""
    now_ms = int(time.time() * 1000)
    db.execute(
        '''INSERT INTO kb_entries (id, title, content, scope, status, chunk_count, created_at, updated_at)
           VALUES (?, ?, ?, 'global', ?, ?, ?, ?)''',
        (eid, title, content, status, chunk_count, now_ms, now_ms)
    )


def _insert_chunk(db, cid, eid, content='chunk', embedding=None, model=''):
    """helper: 插入测试 chunk (上一轮 commit 17 加 db 形参保留)."""
    db.execute(
        '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
           VALUES (?, ?, ?, ?, ?)''',
        (cid, eid, content, embedding, model)
    )


class TestVerifyDetectsIssues(unittest.TestCase):
    """场景 1: verify 检测 4 类不一致"""

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 19:17: monkeypatch ks._db_conn
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = OFF')
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)
        # e1: status=ok 但 0 chunks → missing_chunks
        _insert_entry(self._db, 'e1', title='Missing', status='ok', chunk_count=0)
        # e2: chunk_count=3 但实际 2 chunks → chunk_count_mismatch
        _insert_entry(self._db, 'e2', title='Mismatch', status='ok', chunk_count=3)
        _insert_chunk(self._db, 'c2a', 'e2', 'a')
        _insert_chunk(self._db, 'c2b', 'e2', 'b')
        # e3: 同 entry 用 2 个 model → model_drift
        _insert_entry(self._db, 'e3', title='Drift', status='ok', chunk_count=2)
        _insert_chunk(self._db, 'c3a', 'e3', 'a', embedding=b'\x00' * 4, model='text-embedding-3-small')
        _insert_chunk(self._db, 'c3b', 'e3', 'b', embedding=b'\x00' * 4, model='text-embedding-ada-002')
        # e4: 健康 entry, 不应出现在 issues
        _insert_entry(self._db, 'e4', title='Healthy', status='ok', chunk_count=2)
        _insert_chunk(self._db, 'c4a', 'e4', 'a', embedding=b'\x00' * 4, model='text-embedding-3-small')
        _insert_chunk(self._db, 'c4b', 'e4', 'b', embedding=b'\x00' * 4, model='text-embedding-3-small')
        # orphan chunks (e1 已存在, 这里加个指向不存在的 entry)
        _insert_chunk(self._db, 'c-orphan-1', 'e-nonexistent', 'ghost', embedding=b'\x00' * 4, model='m1')
        self._db.commit()

    def test_verify_detects_missing_chunks(self):
        result = ks.kb_entry_verify_index(is_admin=True)
        ids = [x['entry_id'] for x in result['issues']['missing_chunks']]
        self.assertIn('e1', ids, 'e1 (ok 但 0 chunks) 应在 missing_chunks')
        self.assertNotIn('e4', ids, 'e4 (健康) 不应在 missing_chunks')

    def test_verify_detects_chunk_count_mismatch(self):
        result = ks.kb_entry_verify_index(is_admin=True)
        items = [x for x in result['issues']['chunk_count_mismatch'] if x['entry_id'] == 'e2']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['expected'], 3)
        self.assertEqual(items[0]['actual'], 2)

    def test_verify_detects_model_drift(self):
        result = ks.kb_entry_verify_index(is_admin=True)
        items = [x for x in result['issues']['model_drift'] if x['entry_id'] == 'e3']
        self.assertEqual(len(items), 1)
        self.assertIn('text-embedding-3-small', items[0]['models'])
        self.assertIn('text-embedding-ada-002', items[0]['models'])

    def test_verify_detects_orphan_chunks(self):
        result = ks.kb_entry_verify_index(is_admin=True)
        items = [x for x in result['issues']['orphan_chunks'] if x['chunk_id'] == 'c-orphan-1']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['entry_id'], 'e-nonexistent')

    def test_verify_healthy_entry_not_in_any_issues(self):
        result = ks.kb_entry_verify_index(is_admin=True)
        for issue_type, items in result['issues'].items():
            ids = [x.get('entry_id') for x in items]
            self.assertNotIn('e4', ids, f'e4 不应出现在 {issue_type}')

    def test_verify_requires_admin(self):
        with self.assertRaises(PermissionError):
            ks.kb_entry_verify_index(is_admin=False)

    def test_verify_respects_limit_per_type(self):
        result = ks.kb_entry_verify_index(limit_per_type=0, is_admin=True)
        # limit=0 极端测试, 看 truncated 标记; 各 issues 应为 0 条
        # 注: limit_per_type=0 时 SQL LIMIT 0 会拿空结果
        for issue_type, items in result['issues'].items():
            self.assertEqual(len(items), 0, f'{issue_type} 在 limit=0 应为 0')


class TestRepairDryRun(unittest.TestCase):
    """场景 2: repair dry-run 返回 plan 但不真改"""

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 19:17: monkeypatch ks._db_conn
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = OFF')
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)
        _insert_entry(self._db, 'e1', title='Missing', status='ok', chunk_count=0)
        _insert_entry(self._db, 'e2', title='Healthy', status='ok', chunk_count=2)
        _insert_chunk(self._db, 'c2a', 'e2', 'a', embedding=b'\x00' * 4, model='m1')
        _insert_chunk(self._db, 'c2b', 'e2', 'b', embedding=b'\x00' * 4, model='m1')
        _insert_chunk(self._db, 'c-orphan', 'ghost', 'x')
        self._db.commit()

    def test_dry_run_returns_plan_no_changes(self):
        result = ks.kb_entry_repair_index(confirm=False, is_admin=True)
        self.assertTrue(result['dry_run'])
        self.assertIn('actions_planned', result)
        self.assertNotIn('actions_executed', result)

        # 验证数据库未被修改
        chunk_count = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks").fetchone()['c']
        self.assertEqual(chunk_count, 3, 'dry-run 不应删任何 chunk')

    def test_dry_run_plans_orphan_delete(self):
        result = ks.kb_entry_repair_index(confirm=False, is_admin=True)
        plan_types = [a['type'] for a in result['actions_planned']]
        self.assertIn('delete_orphan_chunks', plan_types)

        orphan_action = next(a for a in result['actions_planned'] if a['type'] == 'delete_orphan_chunks')
        self.assertEqual(orphan_action['count'], 1)
        self.assertEqual(orphan_action['chunk_ids'], ['c-orphan'])

    def test_dry_run_plans_rebuild_for_missing(self):
        result = ks.kb_entry_repair_index(confirm=False, is_admin=True)
        rebuild_actions = [a for a in result['actions_planned'] if a['type'] == 'rebuild_entry_chunks']
        rebuild_ids = [a['entry_id'] for a in rebuild_actions]
        self.assertIn('e1', rebuild_ids, 'e1 (missing_chunks) 应被 plan 重建')
        self.assertNotIn('e2', rebuild_ids, 'e2 (健康) 不应被 plan')

    def test_repair_requires_admin(self):
        with self.assertRaises(PermissionError):
            ks.kb_entry_repair_index(is_admin=False)


class TestRepairConfirm(unittest.TestCase):
    """场景 3: repair confirm 真改 — 删孤儿 + 重建 entries"""

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 19:17: monkeypatch ks._db_conn
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = OFF')
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)
        # 注入 mock api_key + 成功 mock vectorize + 写 mock chunks
        self._orig_emb = ks.get_embedding_config
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai',
            'model': 'current-model', 'baseUrl': None
        }
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)
        self._orig_vec = ks._vectorize_kb_chunks
        def success_vec(entry_id, *args, **kwargs):
            import struct
            emb_bytes = struct.pack('2f', 0.1, 0.2)
            self._db.execute(
                'UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE entry_id=?',
                (emb_bytes, kwargs.get('model') or args[3], entry_id)
            )
            self._db.commit()
        ks._vectorize_kb_chunks = success_vec
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)
        self._orig_save = ks._save_kb_chunks_without_embedding
        def save_chunks(entry_id, emp_id, content, cs, ov):
            self._db.execute('DELETE FROM kb_entry_chunks WHERE entry_id = ?', (entry_id,))
            for i in range(2):
                self._db.execute(
                    '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
                       VALUES (?, ?, ?, NULL, '')''',
                    (f'{entry_id}_c{i}', entry_id, f'mock-chunk-{i+1}')
                )
            self._db.execute('UPDATE kb_entries SET chunk_count = 2 WHERE id = ?', (entry_id,))
            self._db.commit()
        ks._save_kb_chunks_without_embedding = save_chunks
        self.addCleanup(setattr, ks, '_save_kb_chunks_without_embedding', self._orig_save)
        # e1: missing_chunks (status=ok 但 0 chunks)
        _insert_entry(self._db, 'e1', title='Missing', status='ok', chunk_count=0, content='content-1')
        # e3: model_drift
        _insert_entry(self._db, 'e3', title='Drift', status='ok', chunk_count=2, content='content-3')
        _insert_chunk(self._db, 'c3a', 'e3', 'a', embedding=b'\x00' * 4, model='old-model')
        _insert_chunk(self._db, 'c3b', 'e3', 'b', embedding=b'\x00' * 4, model='new-model')
        # 孤儿
        _insert_chunk(self._db, 'c-orphan', 'ghost', 'x')
        self._db.commit()

    def test_confirm_deletes_orphans(self):
        result = ks.kb_entry_repair_index(confirm=True, is_admin=True)
        self.assertFalse(result['dry_run'])
        self.assertIn('actions_executed', result)
        self.assertGreaterEqual(result['stats']['orphan_deleted'], 1)

        # 验证孤儿已删
        count = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE id='c-orphan'").fetchone()['c']
        self.assertEqual(count, 0, '孤儿 chunk 应被删')

    def test_confirm_rebuilds_missing_chunks(self):
        result = ks.kb_entry_repair_index(confirm=True, is_admin=True)
        self.assertGreaterEqual(result['stats']['rebuild_succeeded'], 1)

        # 验证 e1 现在有 chunks (mock 写了 2 个)
        count = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(count, 2, 'e1 重建后应有 2 个 chunks')
        # 验证 chunk_count 已更新
        row = self._db.execute("SELECT chunk_count, status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['chunk_count'], 2)
        self.assertEqual(row['status'], 'ok', '重建后 status 恢复 ok')

    def test_confirm_rebuilds_model_drift(self):
        result = ks.kb_entry_repair_index(confirm=True, is_admin=True)

        # 验证 e3 重建后只用 current-model
        models = self._db.execute(
            "SELECT DISTINCT embedding_model FROM kb_entry_chunks WHERE entry_id='e3' AND embedding_model != ''"
        ).fetchall()
        model_list = [m['embedding_model'] for m in models]
        self.assertEqual(model_list, ['current-model'], f'重建后只该用 current-model, 实际 {model_list}')

    def test_confirm_preserves_pending_status(self):
        """pending 条目重建后保持 pending (审核闸: 不自动过审)"""
        self._db.execute("UPDATE kb_entries SET status='pending' WHERE id='e1'")
        self._db.commit()
        ks.kb_entry_repair_index(confirm=True, is_admin=True)
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'pending', 'pending 条目重建后应保持 pending')


class TestRepairFailureIsolation(unittest.TestCase):
    """场景 4: 某条 entry 重建失败不影响其他"""

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 19:17: monkeypatch ks._db_conn
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = OFF')
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)
        self._orig_emb = ks.get_embedding_config
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai',
            'model': 'm', 'baseUrl': None
        }
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)
        # e1: 正常可重建
        _insert_entry(self._db, 'e1', title='Normal', status='ok', chunk_count=0, content='c1')
        # e2: 构造一个会触发 rebuild 失败的 entry
        _insert_entry(self._db, 'e2', title='WillFail', status='ok', chunk_count=0, content='c2')
        # 让 _vectorize_kb_chunks 对 e2 抛错 (e1 走原始逻辑)
        orig_vectorize = ks._vectorize_kb_chunks  # 保存原始, addCleanup 恢复
        def selective_vectorize(entry_id, *args, **kwargs):
            if entry_id == 'e2':
                raise RuntimeError('mock embedding API failure for e2')
            return orig_vectorize(entry_id, *args, **kwargs)
        ks._vectorize_kb_chunks = selective_vectorize
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', orig_vectorize)
        # mock _save_kb_chunks_without_embedding 写 2 个 mock chunks
        self._orig_save = ks._save_kb_chunks_without_embedding
        def save_chunks(entry_id, emp_id, content, cs, ov):
            self._db.execute('DELETE FROM kb_entry_chunks WHERE entry_id = ?', (entry_id,))
            for i in range(2):
                self._db.execute(
                    '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
                       VALUES (?, ?, ?, NULL, '')''',
                    (f'{entry_id}_c{i}', entry_id, f'mock-chunk-{i+1}')
                )
            self._db.execute('UPDATE kb_entries SET chunk_count = 2 WHERE id = ?', (entry_id,))
            self._db.commit()
        ks._save_kb_chunks_without_embedding = save_chunks
        self.addCleanup(setattr, ks, '_save_kb_chunks_without_embedding', self._orig_save)
        self._db.commit()

    def test_one_failure_does_not_block_others(self):
        result = ks.kb_entry_repair_index(confirm=True, is_admin=True)
        # e1 应成功, e2 应失败
        executed = result['stats']
        self.assertGreaterEqual(executed['rebuild_succeeded'], 1, 'e1 应被成功重建')
        self.assertGreaterEqual(executed['rebuild_failed'], 1, 'e2 应失败')
        self.assertGreater(len(executed['errors']), 0, '应有 error 信息')

        # e1 现在有 chunks, e2 没 (因为 _vectorize 失败后 status 标 error)
        e1_chunks = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(e1_chunks, 2, 'e1 应有 2 个 mock chunks')
        e2_status = self._db.execute("SELECT status FROM kb_entries WHERE id='e2'").fetchone()['status']
        self.assertEqual(e2_status, 'error', 'e2 重建失败应标 error')


if __name__ == '__main__':
    print('=' * 60)
    print('RAG 索引完整性单测 (refactor/rag-index-integrity)')
    print('=' * 60)
    unittest.main(verbosity=2)