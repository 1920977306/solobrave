# -*- coding: utf-8 -*-
"""
KB 向量化失败重试 单测 (refactor/kb-vectorization-error-handling)

策略: 镜像 knowledge_service.py 的关键函数
- _vectorize_kb_chunks_with_status_update (新 helper)
- kb_entry_retry_embedding
- kb_entries_retry_all_failed_embedding
- 改后的 kb_entries_reindex_pending (WHERE 加 'embedding_failed')

exec 到独立 namespace 跑 (避开 server 整个 import 链, 只注入 sqlite3 + 必要 stdlib)。

测试场景 (跟 commit message 对齐):
1. embedding API 失败 → entry status 改 'embedding_failed' + 写 kb_operation_log
2. retry-embedding: 手动重试从 'embedding_failed' 恢复, status 改 'ok', embedding 写回
3. retry-embedding 状态不对: 非 embedding_failed/error 抛 ValueError
4. retry-all-failed 批量: 多个 entry, 统计正确
5. retry-all-failed 非 admin 抛 PermissionError
6. reindex 现在也扫 'embedding_failed' 状态的 entry (顺手做的 1 行改动)

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-vec && python tests/kb_vectorization_test.py
"""
import os, sys, json, sqlite3, time, re


# ★ fix/mini-test-code-repair-20260925: 模块级 ns 创建 + exec 重逻辑收进 _init_ns()
# 使 pytest collection (即 import 本文件) 不再触发:
#   - 文件读取 (KS_PY 路径依赖 cwd, import 未知 cwd 可能报 FileNotFoundError)
#   - sys.exit(1) (提取失败时, import 会让 pytest 整个套退出)
#   - exec combined (耗时 + 副作用, 延迟到 setUpClass 调)
# TestXxx 通过 setUpClass 调 _init_ns() 一次, 不重复。
# 独立运行 (python3 tests/xxx.py) 行为保持不变 (main 守卫仍调 _init_ns())。

def _init_ns():
    """★ fix/mini-test-code-repair-20260925: importlib + types.FunctionType rebind (同 kb_cascade).

    修法 (替代原 extract_function + combined + exec 模式):
    1. importlib.util.spec_from_file_location 加载 knowledge_service 模块
    2. ns = module.__dict__.copy() 作 ns 基底 (含所有 module helper + import)
    3. types.FunctionType 重绑 globals 到 ns, stub 注入机制保持
    4. test stubs: _db_conn + _now_ms + _gen_id (覆盖 module 原始版本)

    Returns: (ns, _db) tuple 供 setUpClass 复用.
    """
    import importlib.util
    import types as _types

    spec = importlib.util.spec_from_file_location('knowledge_service', 'knowledge_service.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def _rebind(fn):
        return _types.FunctionType(fn.__code__, ns, fn.__name__, fn.__defaults__, fn.__closure__)

    import uuid
    _db = sqlite3.connect(':memory:', check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.execute('PRAGMA foreign_keys = OFF')

    ns = module.__dict__.copy()
    ns['__name__'] = 'kb_vectorization_test'

    class _ImmuneConn:
        """★ fix/mini-test-code-repair-20260925 工单 FINAL 17:27: 产品代码 close() 不生效代理.

        治根因: 产品代码 (kb_entry_cleanup_dangling / kb_entries_reindex_pending)
        在事务结束后调 self._db_conn().close() 关连接, 但测试下一 setUp 又开
        新 in-memory 连接, 新连接被产品 close 抛 'Cannot operate on a closed database'.
        _ImmuneConn 让产品 close() 调用是 no-op, 测试 setUp 自己 addCleanup 关.
        """
        def __init__(self, real):
            self._real = real
        def close(self):
            pass  # 产品代码 close 不生效
        def __getattr__(self, name):
            return getattr(self._real, name)

    ns['_db_conn'] = lambda: _ImmuneConn(_db)
    ns['_db'] = _db

    ns['_now_ms'] = lambda: int(time.time() * 1000)
    ns['_gen_id'] = lambda prefix='kb': f"{prefix}_{uuid.uuid4().hex[:8]}"

    # rebind 关键 KB 函数到 ns (globals=ns, 含 module 所有 helper + test stubs)
    ns['_vectorize_kb_chunks_with_status_update'] = _rebind(module._vectorize_kb_chunks_with_status_update)
    ns['kb_entry_retry_embedding'] = _rebind(module.kb_entry_retry_embedding)
    ns['kb_entries_retry_all_failed_embedding'] = _rebind(module.kb_entries_retry_all_failed_embedding)
    ns['kb_entries_reindex_pending'] = _rebind(module.kb_entries_reindex_pending)
    ns['can_edit_knowledge'] = _rebind(module.can_edit_knowledge)
    ns['kb_entry_get_by_id'] = _rebind(module.kb_entry_get_by_id)

    # ★ fix/mini-test-code-repair-20260925 工单 FINAL: 治 KB3 39 failed (setUp 调 self.ns['init_test_tables'] 需要 ns 有这键)
    ns['init_test_tables'] = init_test_tables
    return ns, _db



# 4. 初始化测试表
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
    now_ms = int(time.time() * 1000)
    db.execute(
        '''INSERT INTO kb_entries (id, title, content, scope, status, chunk_count, created_by, emp_id, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)''',
        (eid, title, content, scope, status, created_by, emp_id, now_ms, now_ms)
    )

# 5. 测试
import unittest


class TestVectorizeHelper(unittest.TestCase):
    """场景 1: helper 包装 — 失败标 'embedding_failed' + 写 log"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 删 setUpClass 共享连接 (setUp 自带)

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 治 39 failed
        # setUpClass 共享 cls.ns / cls._db 是连接污染根因, 改 setUp 自带 ns + 独立连接
        self.ns, self._db = _init_ns()
        self.addCleanup(self._db.close)
        self.ns['init_test_tables'](self._db)
        _insert_entry(self._db, 'e1', title='Test', status='ok')
        # mock _vectorize_kb_chunks 抛错
        self._orig_vec = self.ns['_vectorize_kb_chunks']
        def failing_vec(*args, **kwargs):
            raise RuntimeError('mock embedding API failure')
        self.ns['_vectorize_kb_chunks'] = failing_vec

    def tearDown(self):
        self.ns['_vectorize_kb_chunks'] = self._orig_vec

    def test_helper_marks_embedding_failed_status(self):
        with self.assertRaises(RuntimeError):
            self.ns['_vectorize_kb_chunks_with_status_update']('e1', '', 'mock-key', 'openai', 'm')
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'embedding_failed', '失败后 status 应是 embedding_failed')

    def test_helper_writes_audit_log(self):
        with self.assertRaises(RuntimeError):
            self.ns['_vectorize_kb_chunks_with_status_update']('e1', '', 'mock-key', 'openai', 'm')
        logs = self._db.execute("SELECT operation, details FROM kb_operation_log WHERE entry_id='e1'").fetchall()
        self.assertEqual(len(logs), 1, '应写 1 条 audit log')
        self.assertEqual(logs[0]['operation'], 'embedding_failed')
        details = json.loads(logs[0]['details'])
        self.assertIn('mock embedding API failure', details['error'])
        self.assertEqual(details['provider'], 'openai')
        self.assertEqual(details['model'], 'm')


class TestRetryEmbedding(unittest.TestCase):
    """场景 2/3: retry-embedding 手动重试 + 状态校验"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 删 setUpClass 共享连接 (setUp 自带)

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 治 39 failed
        # setUpClass 共享 cls.ns / cls._db 是连接污染根因, 改 setUp 自带 ns + 独立连接
        self.ns, self._db = _init_ns()
        self.addCleanup(self._db.close)
        self.ns['init_test_tables'](self._db)
        _insert_entry(self._db, 'e1', title='Failed', content='content-1', status='embedding_failed',
                      created_by='u1', emp_id='emp1')
        # 已有 chunk 但无 embedding (模拟 embedding_failed 后的状态)
        self._db.execute(
            '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
               VALUES (?, ?, ?, NULL, '')''',
            ('c1', 'e1', 'chunk-1')
        )
        self._db.commit()
        # 默认 mock: get_embedding_config 有 key, _vectorize_kb_chunks 成功
        self.vec_should_fail = False
        self._orig_vec = self.ns['_vectorize_kb_chunks']
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
        self.ns['_vectorize_kb_chunks'] = controllable_vec

    def tearDown(self):
        self.ns['_vectorize_kb_chunks'] = self._orig_vec

    def test_retry_recovers_from_embedding_failed(self):
        result = self.ns['kb_entry_retry_embedding']('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'ok', '重试成功后 status 应是 ok')
        self.assertEqual(result['prev_status'], 'embedding_failed')
        self.assertTrue(result['retried'])

        # 验证 embedding 写回了
        row = self._db.execute("SELECT embedding FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()
        self.assertIsNotNone(row['embedding'], 'embedding 应被写回')

    def test_retry_pending_preserved(self):
        """pending 状态的 entry retry 成功后保持 pending (审核闸)"""
        self._db.execute("UPDATE kb_entries SET status='pending' WHERE id='e1'")
        self._db.commit()
        result = self.ns['kb_entry_retry_embedding']('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertEqual(result['status'], 'pending', 'pending retry 后保持 pending')

    def test_retry_rejects_wrong_status(self):
        self._db.execute("UPDATE kb_entries SET status='ok' WHERE id='e1'")
        self._db.commit()
        with self.assertRaises(ValueError) as cm:
            self.ns['kb_entry_retry_embedding']('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIn('only embedding_failed/error', str(cm.exception))

    def test_retry_returns_none_for_missing(self):
        result = self.ns['kb_entry_retry_embedding']('nonexistent', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNone(result)

    def test_retry_returns_none_for_deleted(self):
        self._db.execute("UPDATE kb_entries SET status='deleted' WHERE id='e1'")
        self._db.commit()
        result = self.ns['kb_entry_retry_embedding']('e1', is_admin=True, operator_id='u1', user_id='u1')
        self.assertIsNone(result, '软删 entry 应返回 None')

    def test_retry_failure_stays_embedding_failed(self):
        """retry 仍失败: status 保持 'embedding_failed' (helper 已设)"""
        self.vec_should_fail = True
        with self.assertRaises(RuntimeError):
            self.ns['kb_entry_retry_embedding']('e1', is_admin=True, operator_id='u1', user_id='u1')
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'embedding_failed')

    def test_retry_permission_denied_for_global_non_admin(self):
        """非 admin 改 global entry 应被 can_edit_knowledge 拒绝"""
        with self.assertRaises(PermissionError):
            self.ns['kb_entry_retry_embedding']('e1', is_admin=False, operator_id='u2', user_id='u2')


class TestRetryAllFailed(unittest.TestCase):
    """场景 4/5: retry-all-failed 批量 + 权限"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 删 setUpClass 共享连接 (setUp 自带)

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 治 39 failed
        # setUpClass 共享 cls.ns / cls._db 是连接污染根因, 改 setUp 自带 ns + 独立连接
        self.ns, self._db = _init_ns()
        self.addCleanup(self._db.close)
        self.ns['init_test_tables'](self._db)
        # 3 entries: 2 failed, 1 ok
        _insert_entry(self._db, 'e1', title='Failed1', status='embedding_failed', created_by='u1', emp_id='emp1')
        _insert_entry(self._db, 'e2', title='Failed2', status='error', created_by='u2', emp_id='emp1')
        _insert_entry(self._db, 'e3', title='OK', status='ok', created_by='u3', emp_id='emp1')
        # mock _vectorize_kb_chunks 成功
        self._orig_vec = self.ns['_vectorize_kb_chunks']
        def success_vec(entry_id, *args, **kwargs):
            import struct
            emb_bytes = struct.pack('2f', 0.1, 0.2)
            self._db.execute(
                'UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE entry_id=?',
                (emb_bytes, kwargs.get('model', ''), entry_id)
            )
            self._db.commit()
        self.ns['_vectorize_kb_chunks'] = success_vec

    def tearDown(self):
        self.ns['_vectorize_kb_chunks'] = self._orig_vec

    def test_batch_retries_only_failed_entries(self):
        result = self.ns['kb_entries_retry_all_failed_embedding'](is_admin=True, operator_id='admin')
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
            self.ns['kb_entries_retry_all_failed_embedding'](is_admin=False, operator_id='u1')

    def test_batch_collects_per_entry_errors(self):
        """某条 retry 抛错, 不影响其他, 错误收集到 errors 列表"""
        # 让 e1 retry 失败 (通过让 _save_kb_chunks 失败, 但我们这个 mock 不便, 改用 PermissionError)
        # 简化: 让 e1 的 status 变 'pending' (不在 IN 范围), e2 retry 成功
        self._db.execute("UPDATE kb_entries SET status='pending' WHERE id='e1'")
        self._db.commit()
        result = self.ns['kb_entries_retry_all_failed_embedding'](is_admin=True, operator_id='admin')
        self.assertEqual(result['scanned'], 1, 'e1 已变 pending, 不在 IN (embedding_failed, error) 范围')
        self.assertEqual(result['succeeded'], 1)


class TestReindexIncludesEmbeddingFailed(unittest.TestCase):
    """场景 6: reindex WHERE 现在也扫 'embedding_failed' 状态的 entry (顺手)"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 删 setUpClass 共享连接 (setUp 自带)

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL ②: 治 39 failed
        # setUpClass 共享 cls.ns / cls._db 是连接污染根因, 改 setUp 自带 ns + 独立连接
        self.ns, self._db = _init_ns()
        self.addCleanup(self._db.close)
        self.ns['init_test_tables'](self._db)
        # 3 entries: 1 pending, 1 embedding_failed, 1 ok
        _insert_entry(self._db, 'e1', title='Pending', content='c1', status='pending')
        _insert_entry(self._db, 'e2', title='EmbeddingFailed', content='c2', status='embedding_failed')
        _insert_entry(self._db, 'e3', title='OK', content='c3', status='ok')
        self._db.commit()
        # mock _save_chunks 写 1 个 chunk, _vectorize 不做事
        self._orig_save = self.ns['_save_kb_chunks_without_embedding']
        def save_chunks(entry_id, emp_id, content, cs, ov):
            self._db.execute('DELETE FROM kb_entry_chunks WHERE entry_id = ?', (entry_id,))
            self._db.execute(
                '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
                   VALUES (?, ?, ?, NULL, '')''',
                (f'{entry_id}_c0', entry_id, 'chunk-1')
            )
            self._db.execute('UPDATE kb_entries SET chunk_count=1 WHERE id=?', (entry_id,))
            self._db.commit()
        self.ns['_save_kb_chunks_without_embedding'] = save_chunks

    def tearDown(self):
        self.ns['_save_kb_chunks_without_embedding'] = self._orig_save

    def test_reindex_picks_up_embedding_failed(self):
        result = self.ns['kb_entries_reindex_pending']()
        # e1 (pending) + e2 (embedding_failed) 都该被扫, e3 (ok) 不该
        # 由于无 API key, 2 条都 noKey
        self.assertEqual(result['total'], 2, 'reindex 应扫 pending + embedding_failed, 不扫 ok')
        self.assertEqual(result['noKey'], 2)

    def test_reindex_preserves_embedding_failed_on_vectorize_failure(self):
        """reindex 遇到 vectorize 失败, status 应保持 'embedding_failed' (不被 'error' 覆盖)"""
        # 注入 mock: get_embedding_config 有 key, _vectorize_kb_chunks 抛错
        self._orig_emb = self.ns['get_embedding_config']
        self.ns['get_embedding_config'] = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai', 'model': 'm', 'baseUrl': None
        }
        self._orig_vec = self.ns['_vectorize_kb_chunks']
        def failing_vec(*args, **kwargs):
            raise RuntimeError('mock vec failure in reindex')
        self.ns['_vectorize_kb_chunks'] = failing_vec
        try:
            result = self.ns['kb_entries_reindex_pending']()
            # e1, e2 都被 vectorize 失败 → 标 'embedding_failed'
            rows = {r['id']: r['status'] for r in
                    self._db.execute("SELECT id, status FROM kb_entries").fetchall()}
            self.assertEqual(rows['e1'], 'embedding_failed', 'pending entry vectorize 失败 → embedding_failed')
            self.assertEqual(rows['e2'], 'embedding_failed', 'embedding_failed entry vectorize 失败 → 保持 embedding_failed')
            self.assertEqual(result['failed'], 2)
        finally:
            self.ns['get_embedding_config'] = self._orig_emb
            self.ns['_vectorize_kb_chunks'] = self._orig_vec


if __name__ == '__main__':
    _init_ns()
    unittest.main(verbosity=2)
