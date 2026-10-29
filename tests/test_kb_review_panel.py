# -*- coding: utf-8 -*-
"""
KB 审核面板 单测（2026-10-29，优化菜单②）

背景（生产实测）：daren_bot 聊天回流条目入库时只写 kb_entries、不建 chunks
（21 条 pending 里 20 条 chunks=0）。纯改 status='ok' 过审 ⇒ 条目永无向量 ⇒
RAG 召回条件是 embedding IS NOT NULL ⇒ 永远召不回 = 「假绿灯」（§14 铁律）。

守卫点（全是行为断言，变异对照过）：
1. 过审（pending→ok）且无向量 chunk ⇒ 自动重建 chunks + 向量化，成功转 ok
2. 已有带向量 chunk 的条目过审 ⇒ 不重复向量化（幂等，spy 断言）
3. 无 embedding API key ⇒ 过审中止抛 RuntimeError，status 保持 pending（不留假绿灯）
4. 向量化失败 ⇒ status 被标 embedding_failed（既有 helper 语义），异常上抛
5. 打回（status='pending'）不触发向量化
6. status 单状态过滤：kb_entry_list(status='pending') 只回 pending，缺省回 ok+pending，
   非法值回退缺省闭集（不开放 deleted/superseded 查询面）
7. 拒绝 = 软删（status='deleted' + 清 chunks），列表不再出现
8. 非 admin 不能改 global 条目（既有权限语义锁）

探针纪律（§8.11）：spy 必须盯「真在用的入口名」——补向量入口是
kb_entry_ensure_vectorized / _save_kb_chunks_without_embedding，不是别的名字。
"""
import os, sys, json, sqlite3, time, struct
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import knowledge_service as ks  # 直接 import：产品函数 globals 永远是 ks.__dict__


class _ImmuneConn:
    """产品代码 close() 不生效代理（同 test_kb_vectorization，防跨用例连接被关）"""
    def __init__(self, real):
        self._real = real
    def close(self):
        pass
    def __getattr__(self, name):
        return getattr(self._real, name)


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
            updated_at INTEGER,
            domain TEXT NOT NULL DEFAULT 'tuangzhang'
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


def _insert(db, eid, status='pending', scope='global', title='T', content='C'):
    now = int(time.time() * 1000)
    db.execute(
        'INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)', (eid, title, content, scope, status, now, now))
    db.commit()


class _Base(unittest.TestCase):
    def setUp(self):
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)

        # 默认: 有 embedding key；_save_kb_chunks 插一条 NULL-embedding chunk（模拟重建）；
        # _vectorize_kb_chunks 成功（写 fake embedding）。用例可按需替换。
        self.save_calls = []
        self.vec_calls = []
        self._orig_save = ks._save_kb_chunks_without_embedding
        self._orig_vec = ks._vectorize_kb_chunks
        self._orig_emb = ks.get_embedding_config

        def fake_save(entry_id, emp_id, content, chunk_size, overlap):
            self.save_calls.append(entry_id)
            self._db.execute(
                'INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model) '
                'VALUES (?, ?, ?, NULL, \'\')', ('c-' + entry_id, entry_id, (content or '')[:80]))
            self._db.commit()

        def ok_vec(entry_id, *a, **kw):
            self.vec_calls.append(entry_id)
            self._db.execute(
                'UPDATE kb_entry_chunks SET embedding=?, embedding_model=? WHERE entry_id=?',
                (struct.pack('2f', 0.1, 0.2), 'mock-model', entry_id))
            self._db.commit()

        ks._save_kb_chunks_without_embedding = fake_save
        ks._vectorize_kb_chunks = ok_vec
        ks.get_embedding_config = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai', 'model': 'mock-model', 'baseUrl': None}
        self.addCleanup(setattr, ks, '_save_kb_chunks_without_embedding', self._orig_save)
        self.addCleanup(setattr, ks, '_vectorize_kb_chunks', self._orig_vec)
        self.addCleanup(setattr, ks, 'get_embedding_config', self._orig_emb)

    def _status(self, eid):
        return self._db.execute('SELECT status FROM kb_entries WHERE id=?', (eid,)).fetchone()['status']

    def _emb_count(self, eid):
        return self._db.execute(
            'SELECT COUNT(*) c FROM kb_entry_chunks WHERE entry_id=? AND embedding IS NOT NULL',
            (eid,)).fetchone()['c']


class TestApproveTriggersVectorize(_Base):
    """守卫 1：pending→ok 且无向量 chunk ⇒ 自动补向量"""

    def test_approve_pending_without_chunks_vectorizes_and_ok(self):
        _insert(self._db, 'e1', status='pending')
        doc = ks.kb_entry_update('e1', status='ok', is_admin=True)
        self.assertEqual(doc['status'], 'ok')
        self.assertEqual(self._status('e1'), 'ok')
        self.assertEqual(self.save_calls, ['e1'], '应重建 chunks')
        self.assertEqual(self.vec_calls, ['e1'], '应做向量化')
        self.assertGreaterEqual(self._emb_count('e1'), 1, '过审后必须有向量（不假绿灯）')

    def test_approve_already_vectorized_no_rework(self):
        """守卫 2：已有向量 chunk ⇒ 幂等，不重复向量化"""
        _insert(self._db, 'e2', status='pending')
        self._db.execute(
            'INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model) '
            'VALUES (?, ?, ?, ?, ?)', ('c-e2', 'e2', 'x', struct.pack('2f', 0.1, 0.2), 'm'))
        self._db.commit()
        doc = ks.kb_entry_update('e2', status='ok', is_admin=True)
        self.assertEqual(doc['status'], 'ok')
        self.assertEqual(self.save_calls, [], '已有向量不得重建 chunks')
        self.assertEqual(self.vec_calls, [], '已有向量不得重复向量化')


class TestApproveNoFakeGreen(_Base):
    """守卫 3/4：无 key / 向量化失败 ⇒ 过审中止，不留「ok 但无向量」假绿灯"""

    def test_no_api_key_aborts_approve(self):
        _insert(self._db, 'e3', status='pending')
        ks.get_embedding_config = lambda emp_id=None: {}
        with self.assertRaises(RuntimeError):
            ks.kb_entry_update('e3', status='ok', is_admin=True)
        self.assertEqual(self._status('e3'), 'pending', '无 key 时 status 必须保持 pending')
        self.assertEqual(self._emb_count('e3'), 0)

    def test_vectorize_failure_marks_embedding_failed(self):
        _insert(self._db, 'e4', status='pending')

        def failing_vec(entry_id, *a, **kw):
            raise RuntimeError('mock embedding API down')
        ks._vectorize_kb_chunks = failing_vec
        with self.assertRaises(RuntimeError):
            ks.kb_entry_update('e4', status='ok', is_admin=True)
        self.assertEqual(self._status('e4'), 'embedding_failed',
                         '向量化失败必须标 embedding_failed（可 retry），不是 ok 假绿灯')
        logs = self._db.execute(
            "SELECT operation FROM kb_operation_log WHERE entry_id='e4'").fetchall()
        self.assertTrue(any(l['operation'] == 'embedding_failed' for l in logs))


class TestRejectDoesNotVectorize(_Base):
    """守卫 5：打回（status='pending'）不触发向量化（幂等打回）"""

    def test_reject_back_to_pending_no_vectorize(self):
        _insert(self._db, 'e5', status='pending')
        doc = ks.kb_entry_update('e5', status='pending', is_admin=True)
        self.assertEqual(doc['status'], 'pending')
        self.assertEqual(self.save_calls, [])
        self.assertEqual(self.vec_calls, [])

    def test_non_admin_cannot_approve_global(self):
        """守卫 8：权限语义锁"""
        _insert(self._db, 'e8', status='pending', scope='global')
        with self.assertRaises(PermissionError):
            ks.kb_entry_update('e8', status='ok', is_admin=False, created_by='u1')
        self.assertEqual(self._status('e8'), 'pending')


class TestStatusFilter(_Base):
    """守卫 6：status 单状态过滤（审核面板待审核视图的数据口径）"""

    def test_list_status_pending_only(self):
        _insert(self._db, 'p1', status='pending')
        _insert(self._db, 'p2', status='pending')
        _insert(self._db, 'o1', status='ok')
        docs = ks.kb_entry_list(status='pending')['docs']
        ids = sorted(d['id'] for d in docs)
        self.assertEqual(ids, ['p1', 'p2'], 'status=pending 只能回 pending')
        docs_all = ks.kb_entry_list()['docs']
        self.assertEqual(len(docs_all), 3, '缺省 = ok+pending 闭集')

    def test_list_status_ok_only(self):
        _insert(self._db, 'p3', status='pending')
        _insert(self._db, 'o2', status='ok')
        docs = ks.kb_entry_list(status='ok')['docs']
        self.assertEqual([d['id'] for d in docs], ['o2'])

    def test_invalid_status_falls_back_to_closed_set(self):
        """非法 status 值（如 deleted/superseded 探测）⇒ 回退缺省闭集，不开放隐藏状态查询面"""
        _insert(self._db, 'd1', status='ok')
        self._db.execute("UPDATE kb_entries SET status='deleted' WHERE id='d1'")
        self._db.commit()
        _insert(self._db, 'p4', status='pending')
        docs = ks.kb_entry_list(status='deleted')['docs']
        ids = [d['id'] for d in docs]
        self.assertNotIn('d1', ids, 'deleted 条目任何情况下都不得出现在列表')
        self.assertIn('p4', ids, '非法 status 值回退缺省闭集（ok+pending）')


class TestRejectSoftDelete(_Base):
    """守卫 7：拒绝 = 软删 + 清 chunks（RAG 不得命中）"""

    def test_reject_soft_deletes_and_clears_chunks(self):
        _insert(self._db, 'e7', status='pending')
        self._db.execute(
            'INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model) '
            'VALUES (?, ?, ?, ?, ?)', ('c-e7', 'e7', 'x', struct.pack('2f', 0.1, 0.2), 'm'))
        self._db.commit()
        result = ks.kb_entry_delete('e7', is_admin=True, operator_id='admin')
        self.assertTrue(result)
        row = self._db.execute('SELECT status FROM kb_entries WHERE id=?', ('e7',)).fetchone()
        self.assertEqual(row['status'], 'deleted')
        chunks = self._db.execute('SELECT COUNT(*) c FROM kb_entry_chunks WHERE entry_id=?', ('e7',)).fetchone()['c']
        self.assertEqual(chunks, 0, '软删必须级联清 chunks（防 RAG 命中）')
        docs = ks.kb_entry_list()['docs'] + ks.kb_entry_list(status='pending')['docs']
        self.assertNotIn('e7', [d['id'] for d in docs])


if __name__ == '__main__':
    unittest.main()
