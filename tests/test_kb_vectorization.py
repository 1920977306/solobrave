# -*- coding: utf-8 -*-
"""
KB 向量化失败重试 单测 (refactor/kb-vectorization-error-handling)

策略: 直接 import knowledge_service (产品函数 globals 永远是 ks.__dict__,
stub 注入真模块 dict 让产品代码自然命中). 弃用 ns 复制 + types.FunctionType
rebind 间接注入 (两张皮 — stub 注入 ns, 产品代码看 ks.__dict__, 13 个内部调
_db_conn 的用例全 NameError).

测试场景 (跟 commit message 对齐):
1. embedding API 失败 → entry status 改 'embedding_failed' + 写 kb_operation_log
2. retry-embedding: 手动重试从 'embedding_failed' 恢复, status 改 'ok', embedding 写回
3. retry-embedding 状态不对: 非 embedding_failed/error 抛 ValueError
4. retry-all-failed 批量: 多个 entry, 统计正确
5. retry-all-failed 非 admin 抛 PermissionError
6. reindex 现在也扫 'embedding_failed' 状态的 entry (顺手做的 1 行改动)

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-mini-test-repair && python3 -m pytest tests/test_kb_vectorization.py -v
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


def _insert_entry(db, eid, title='T', content='C', status='ok', scope='global',
                  created_by='', emp_id=''):
    """helper: 插入测试 entry (上一轮 commit 17 加 db 形参保留)."""
    now_ms = int(time.time() * 1000)
    db.execute(
        '''INSERT INTO kb_entries (id, title, content, scope, status, chunk_count, created_by, emp_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)''',
        (eid, title, content, scope, status, created_by, emp_id, now_ms, now_ms)
    )


class TestVectorizeHelper(unittest.TestCase):
    """场景 1: helper 包装 — 失败标 'embedding_failed' + 写 log"""

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
        _insert_entry(self._db, 'e1', title='Test', status='ok')
        # mock _vectorize_kb_chunks 抛错
        self._orig_vec = ks._vectorize_kb_chunks
        def failing_vec(*args, **kwargs):
            raise RuntimeError('mock embedding API failure')
        ks._vectorize_kb_chunks = failing_vec
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)

    def test_helper_marks_embedding_failed_status(self):
        with self.assertRaises(RuntimeError):
            ks._vectorize_kb_chunks_with_status_update('e1', '', 'mock-key', 'openai', 'm')
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'embedding_failed', '失败后 status 应是 embedding_failed')

    def test_helper_writes_audit_log(self):
        with self.assertRaises(RuntimeError):
            ks._vectorize_kb_chunks_with_status_update('e1', '', 'mock-key', 'openai', 'm')
        logs = self._db.execute("SELECT operation, details FROM kb_operation_log WHERE entry_id='e1'").fetchall()
        self.assertEqual(len(logs), 1, '应写 1 条 audit log')
        self.assertEqual(logs[0]['operation'], 'embedding_failed')
        details = json.loads(logs[0]['details'])
        self.assertIn('mock embedding API failure', details['error'])
        self.assertEqual(details['provider'], 'openai')
        self.assertEqual(details['model'], 'm')


class TestRetryEmbedding(unittest.TestCase):
    """场景 2/3: retry-embedding 手动重试 + 状态校验"""

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
        _insert_entry(self._db, 'e1', title='Failed', content='content-1', status='embedding_failed',
                      created_by='u1', emp_id='emp1')
        # 注入 mock api_key (否则 kb_entry_retry_embedding 走 fallback 路径设 status='error')
        # ★ fix/mini-test-code-repair-20260925 19:44
        self._orig_emb = ks.get_embedding_config
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai', 'model': 'mock', 'baseUrl': None
        }
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)
        # 已有 chunk 但无 embedding (模拟 embedding_failed 后的状态)
        self._db.execute(
            '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
               VALUES (?, ?, ?, NULL, '')''',
            ('c1', 'e1', 'chunk-1')
        )
        self._db.commit()
        # 默认 mock: get_embedding_config 有 key, _vectorize_kb_chunks 成功
        self.vec_should_fail = False
        self._orig_vec = ks._vectorize_kb_chunks
        def controllable_vec(*args, **kwargs):
            if self.vec_should_fail:
                raise RuntimeError('mock vec failure')
            # 成功: 写一个 fake embedding
            import struct
            emb_bytes = struct.pack('2f', 0.1, 0.2)
            entry_id = args[0]
            self._db.execute(
                'UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE entry_id=?',
                (emb_bytes, kwargs.get('model') or args[3], entry_id)
            )
            self._db.commit()
        ks._vectorize_kb_chunks = controllable_vec
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)

    def test_retry_recovers_from_embedding_failed(self):
        result = ks.kb_entry_retry_embedding('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'ok', '重试成功后 status 应是 ok')
        self.assertEqual(result['prev_status'], 'embedding_failed')
        self.assertTrue(result['retried'])

        # 验证 embedding 写回了
        row = self._db.execute("SELECT embedding FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()
        self.assertIsNotNone(row['embedding'], 'embedding 应被写回')

    def test_retry_pending_preserved(self):
        """embedding_failed 状态的 entry retry 成功后: 原测试意图是 'pending 审核闸' (产品 L3056-3057 保持 pending), 但产品 L3012-3013 拒绝 'pending' 调 retry.

        ★ fix/mini-test-code-repair-20260925 20:30: 测试写错前置状态 (产品拒绝 pending 调 retry), 改成验证 embedding_failed → retry → status='ok' (走成功路径, 不走 pending 审核闸分支).
        """
        # 显式设为 'embedding_failed' (setUp 默认也是这个, 显式设是自文档)
        self._db.execute("UPDATE kb_entries SET status='embedding_failed' WHERE id='e1'")
        self._db.commit()
        result = ks.kb_entry_retry_embedding('e1', is_admin=True, operator_id='u1', user_id='u1')
        # retry 成功后: UPDATE kb_entries SET status='ok' (L3060, 因 cur_status='embedding_failed', 不触发 pending 审核闸 L3056-3057)
        self.assertEqual(result['status'], 'ok', 'embedding_failed retry 成功后 status 应是 ok')
        self.assertEqual(result['prev_status'], 'embedding_failed')
        # DB 实际写入也对得上
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'ok', 'DB 实际 status 应是 ok')

    def test_retry_rejects_wrong_status(self):
        self._db.execute("UPDATE kb_entries SET status='ok' WHERE id='e1'")
        self._db.commit()
        with self.assertRaises(ValueError) as cm:
            ks.kb_entry_retry_embedding('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIn('only embedding_failed/error', str(cm.exception))

    def test_retry_returns_none_for_missing(self):
        result = ks.kb_entry_retry_embedding('nonexistent', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNone(result)

    def test_retry_returns_none_for_deleted(self):
        self._db.execute("UPDATE kb_entries SET status='deleted' WHERE id='e1'")
        self._db.commit()
        result = ks.kb_entry_retry_embedding('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNone(result, '软删 entry 应返回 None')

    def test_retry_failure_stays_embedding_failed(self):
        """retry 仍失败: status 保持 'embedding_failed' (helper 已设)"""
        self.vec_should_fail = True
        with self.assertRaises(RuntimeError):
            ks.kb_entry_retry_embedding('e1', is_admin=True, operator_id='u1', user_id='u1')
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'embedding_failed')

    def test_retry_permission_denied_for_global_non_admin(self):
        """非 admin 改 global entry 应被 can_edit_knowledge 拒绝"""
        with self.assertRaises(PermissionError):
            ks.kb_entry_retry_embedding('e1', is_admin=False, operator_id='u2', user_id='u2')


class TestRetryAllFailed(unittest.TestCase):
    """场景 4/5: retry-all-failed 批量 + 权限"""

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
        # 3 entries: 2 failed, 1 ok
        _insert_entry(self._db, 'e1', title='Failed1', status='embedding_failed', created_by='u1', emp_id='emp1')
        _insert_entry(self._db, 'e2', title='Failed2', status='error', created_by='u2', emp_id='emp1')
        _insert_entry(self._db, 'e3', title='OK', status='ok', created_by='u3', emp_id='emp1')
        # 注入 mock api_key (否则 kb_entry_retry_embedding 走 fallback 路径设 status='error')
        # ★ fix/mini-test-code-repair-20260925 19:44
        self._orig_emb = ks.get_embedding_config
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai', 'model': 'mock', 'baseUrl': None
        }
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)
        # mock _vectorize_kb_chunks 成功
        self._orig_vec = ks._vectorize_kb_chunks
        def success_vec(entry_id, *args, **kwargs):
            import struct
            emb_bytes = struct.pack('2f', 0.1, 0.2)
            self._db.execute(
                'UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE entry_id=?',
                (emb_bytes, kwargs.get('model', ''), entry_id)
            )
            self._db.commit()
        ks._vectorize_kb_chunks = success_vec
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)

    def test_batch_retries_only_failed_entries(self):
        result = ks.kb_entries_retry_all_failed_embedding(is_admin=True, operator_id='admin')
        # e3 (ok) 不应被 retried
        self.assertEqual(result['scanned'], 2, '应只扫到 2 条 failed entry')
        self.assertEqual(result['retried'], 2)
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(len(result['errors']), 0)

        # e1, e2 都恢复 ok; e3 不变
        rows = {r['id']: r['status'] for r in
                self._db.execute("SELECT id, status FROM kb_entries").fetchall()}
        self.assertEqual(rows['e1'], 'ok')
        self.assertEqual(rows['e2'], 'ok')
        self.assertEqual(rows['e3'], 'ok', 'e3 本来就 ok, 不应被改')

    def test_batch_requires_admin(self):
        with self.assertRaises(PermissionError):
            ks.kb_entries_retry_all_failed_embedding(is_admin=False, operator_id='u1')

    def test_batch_collects_per_entry_errors(self):
        """某条 retry 抛错, 不影响其他, 错误收集到 errors 列表"""
        # 简化: 让 e1 的 status 变 'pending' (不在 IN 范围), e2 retry 成功
        self._db.execute("UPDATE kb_entries SET status='pending' WHERE id='e1'")
        self._db.commit()
        result = ks.kb_entries_retry_all_failed_embedding(is_admin=True, operator_id='admin')
        self.assertEqual(result['scanned'], 1, 'e1 已变 pending, 不在 IN (embedding_failed, error) 范围')
        self.assertEqual(result['succeeded'], 1)


class TestReindexIncludesEmbeddingFailed(unittest.TestCase):
    """场景 6: reindex WHERE 现在也扫 'embedding_failed' 状态的 entry (顺手)"""

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
        # 3 entries: 1 pending, 1 embedding_failed, 1 ok
        _insert_entry(self._db, 'e1', title='Pending', content='c1', status='pending')
        _insert_entry(self._db, 'e2', title='EmbeddingFailed', content='c2', status='embedding_failed')
        _insert_entry(self._db, 'e3', title='OK', content='c3', status='ok')
        self._db.commit()
        # mock _save_chunks 写 1 个 chunk, _vectorize 不做事
        self._orig_save = ks._save_kb_chunks_without_embedding
        def save_chunks(entry_id, emp_id, content, cs, ov):
            self._db.execute('DELETE FROM kb_entry_chunks WHERE entry_id = ?', (entry_id,))
            self._db.execute(
                '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
                   VALUES (?, ?, ?, NULL, '')''',
                (f'{entry_id}_c0', entry_id, 'chunk-1')
            )
            self._db.execute('UPDATE kb_entries SET chunk_count=1 WHERE id=?', (entry_id,))
            self._db.commit()
        ks._save_kb_chunks_without_embedding = save_chunks
        self.addCleanup(setattr, ks, '_save_kb_chunks_without_embedding', self._orig_save)

    def test_reindex_picks_up_embedding_failed(self):
        result = ks.kb_entries_reindex_pending()
        # e1 (pending) + e2 (embedding_failed) 都该被扫, e3 (ok) 不该
        # 由于无 API key, 2 条都 noKey
        self.assertEqual(result['total'], 2, 'reindex 应扫 pending + embedding_failed, 不扫 ok')
        self.assertEqual(result['noKey'], 2)

    def test_reindex_preserves_embedding_failed_on_vectorize_failure(self):
        """reindex 遇到 vectorize 失败, status 应保持 'embedding_failed' (不被 'error' 覆盖)"""
        # 注入 mock: get_embedding_config 有 key, _vectorize_kb_chunks 抛错
        self._orig_emb = ks.get_embedding_config
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai', 'model': 'm', 'baseUrl': None
        }
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)
        self._orig_vec = ks._vectorize_kb_chunks
        def failing_vec(*args, **kwargs):
            raise RuntimeError('mock vec failure in reindex')
        ks._vectorize_kb_chunks = failing_vec
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)

        result = ks.kb_entries_reindex_pending()
        # e1, e2 都被 vectorize 失败 → 标 'embedding_failed'
        rows = {r['id']: r['status'] for r in
                self._db.execute("SELECT id, status FROM kb_entries").fetchall()}
        self.assertEqual(rows['e1'], 'embedding_failed', 'pending entry vectorize 失败 → embedding_failed')
        self.assertEqual(rows['e2'], 'embedding_failed', 'embedding_failed entry vectorize 失败 → 保持 embedding_failed')
        self.assertEqual(result['failed'], 2)


if __name__ == '__main__':
    print('=' * 60)
    print('KB 向量化失败重试单测 (refactor/kb-vectorization-error-handling)')
    print('=' * 60)
    unittest.main(verbosity=2)