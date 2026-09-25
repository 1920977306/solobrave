# -*- coding: utf-8 -*-
"""
RAG 索引完整性单测 (refactor/rag-index-integrity)

策略: 镜像 knowledge_service.py 的 verify + repair 函数, exec 到独立 namespace 跑
(避开 server 整个 import 链, 只注入 sqlite3 + 必要 stdlib)。

测试场景 (跟 commit message 对齐):
1. verify 检测 4 类不一致 (missing_chunks / chunk_count_mismatch / model_drift / orphan_chunks)
2. repair dry-run 模式: 返回 actions_planned 但不真改
3. repair confirm 模式: 删孤儿 + 重建 entries 实际生效
4. repair 重建失败时 entry 标 'error', 其他 entry 不受影响

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-rag && python tests/rag_index_test.py
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
    """初始化 ns + _db (提取 + stub + exec), 返回 (ns, _db) tuple 供 setUpClass 复用."""
    KS_PY = 'knowledge_service.py'
    text = open(KS_PY, encoding='utf-8').read()


    def extract_function(name, source):
        """提取 def name(...): 开始的函数 (含 docstring + 函数体), 用 {} 配平找到函数体结束"""
        m = re.search(rf'^def {re.escape(name)}\(', source, re.MULTILINE)
        if not m:
            return None
        start = m.start()
        i = source.index(':', m.end()) + 1
        depth = 0
        in_string = False
        triple = False
        while i < len(source):
            c = source[i]
            if not in_string and c == '#':
                while i < len(source) and source[i] != '\n':
                    i += 1
                continue
            if c == '"' or c == "'":
                if source[i:i+3] in ('"""', "'''"):
                    triple = not triple
                    i += 3
                    continue
                if not triple:
                    in_string = not in_string
            if not in_string and not triple:
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                elif c == '\n' and depth == 0:
                    rest = source[i+1:].lstrip()
                    if rest.startswith('def ') or rest.startswith('class ') or rest.startswith('# ') or rest.startswith('#!'):
                        return source[start:i+1]
            i += 1
        return source[start:]


    verify_fn = extract_function('kb_entry_verify_index', text)
    repair_fn = extract_function('kb_entry_repair_index', text)

    if not (verify_fn and repair_fn):
        print('FATAL: 提取函数失败, knowledge_service.py 改动没生效?')
        print(f'  verify_fn: {bool(verify_fn)}, repair_fn: {bool(repair_fn)}')
        raise RuntimeError(
            f'提取失败, knowledge_service.py 改动没生效? 检查提取结果是否为空'

    print(f'提取: kb_entry_verify_index={len(verify_fn)} chars, kb_entry_repair_index={len(repair_fn)} chars')


    # 2. 准备 stub namespace
    import uuid
    ns = {
        '__name__': 'rag_test',
        'sqlite3': sqlite3,
        'time': time,
        'uuid': uuid,
        'json': json,
    }

    _db = sqlite3.connect(':memory:', check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.execute('PRAGMA foreign_keys = OFF')

    def _db_conn():
        return _db
    ns['_db_conn'] = _db_conn

    # get_embedding_config: mock 返回空 api_key (repair 不会真调向量化)
    ns['get_embedding_config'] = lambda emp_id=None: {
        'apiKey': '', 'provider': 'openai', 'model': 'mock-embed', 'baseUrl': None
    }

    # _save_kb_chunks_without_embedding: mock (不真分 chunk,直接写 2 个固定 chunk 用于测)
    def _mock_save_chunks(entry_id, emp_id, content, chunk_size, overlap):
        conn = _db_conn()
        try:
            conn.execute('DELETE FROM kb_entry_chunks WHERE entry_id = ?', (entry_id,))
            for i, c in enumerate(['mock-chunk-1', 'mock-chunk-2']):
                conn.execute(
                    '''INSERT INTO kb_entry_chunks (id, entry_id, emp_id, chunk_index, content, embedding, embedding_model, created_at)
                       VALUES (?, ?, ?, ?, ?, NULL, '', ?)''',
                    (f'{entry_id}_c{i}', entry_id, emp_id, i, c, int(time.time() * 1000))
                )
            conn.execute('UPDATE kb_entries SET chunk_count = 2 WHERE id = ?', (entry_id,))
            conn.commit()
        finally:
            conn.close()
    ns['_save_kb_chunks_without_embedding'] = _mock_save_chunks

    # _vectorize_kb_chunks: mock (写入固定 embedding bytes + 当前 config model)
    def _mock_vectorize(entry_id, emp_id, api_key, provider, model, base_url=None):
        if not api_key:
            # 无 api_key 时, 真实代码会抛 RuntimeError; 但 mock 环境下没有 api_key,
            # 我们让 verify 测试不依赖它, repair confirm 测试用 mock api_key 走全流程
            raise RuntimeError('mock: no api_key')
        conn = _db_conn()
        try:
            emb_bytes = b'\x00' * 8  # 2 floats
            rows = conn.execute(
                'SELECT id FROM kb_entry_chunks WHERE entry_id = ? AND embedding IS NULL',
                (entry_id,)
            ).fetchall()
            for r in rows:
                conn.execute(
                    'UPDATE kb_entry_chunks SET embedding = ?, embedding_model = ? WHERE id = ?',
                    (emb_bytes, model, r['id'])
                )
            conn.commit()
        finally:
            conn.close()
    ns['_vectorize_kb_chunks'] = _mock_vectorize

    # kb_entry_log_operation: stub (避免连写 audit log)
    ns['kb_entry_log_operation'] = lambda *args, **kwargs: None

    # _now_ms: 直接 stub
    ns['_now_ms'] = lambda: int(time.time() * 1000)

    # 3. exec 函数到 namespace
    combined_src = '\n\n'.join([verify_fn, repair_fn])
    exec(combined_src, ns)
    print(f'exec combined: {len(combined_src)} chars')
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


# 5. 测试场景
import unittest


def _insert_entry(eid, title='T', content='C', status='ok', chunk_count=0):
    now_ms = int(time.time() * 1000)
    _db.execute(
        '''INSERT INTO kb_entries (id, title, content, scope, status, chunk_count, created_at, updated_at)
           VALUES (?, ?, ?, 'global', ?, ?, ?, ?)''',
        (eid, title, content, status, chunk_count, now_ms, now_ms)
    )


def _insert_chunk(cid, eid, content='chunk', embedding=None, model=''):
    _db.execute(
        '''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding, embedding_model)
           VALUES (?, ?, ?, ?, ?)''',
        (cid, eid, content, embedding, model)
    )


class TestVerifyDetectsIssues(unittest.TestCase):
    """场景 1: verify 检测 4 类不一致"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        # e1: status=ok 但 0 chunks → missing_chunks
        _insert_entry('e1', title='Missing', status='ok', chunk_count=0)
        # e2: chunk_count=3 但实际 2 chunks → chunk_count_mismatch
        _insert_entry('e2', title='Mismatch', status='ok', chunk_count=3)
        _insert_chunk('c2a', 'e2', 'a')
        _insert_chunk('c2b', 'e2', 'b')
        # e3: 同 entry 用 2 个 model → model_drift
        _insert_entry('e3', title='Drift', status='ok', chunk_count=2)
        _insert_chunk('c3a', 'e3', 'a', embedding=b'\x00' * 4, model='text-embedding-3-small')
        _insert_chunk('c3b', 'e3', 'b', embedding=b'\x00' * 4, model='text-embedding-ada-002')
        # e4: 健康 entry, 不应出现在 issues
        _insert_entry('e4', title='Healthy', status='ok', chunk_count=2)
        _insert_chunk('c4a', 'e4', 'a', embedding=b'\x00' * 4, model='text-embedding-3-small')
        _insert_chunk('c4b', 'e4', 'b', embedding=b'\x00' * 4, model='text-embedding-3-small')
        # orphan chunks (e1 已存在, 这里加个指向不存在的 entry)
        _insert_chunk('c-orphan-1', 'e-nonexistent', 'ghost', embedding=b'\x00' * 4, model='m1')
        self._db.commit()

    def test_verify_detects_missing_chunks(self):
        result = ns['kb_entry_verify_index'](is_admin=True)
        ids = [x['entry_id'] for x in result['issues']['missing_chunks']]
        self.assertIn('e1', ids, 'e1 (ok 但 0 chunks) 应在 missing_chunks')
        self.assertNotIn('e4', ids, 'e4 (健康) 不应在 missing_chunks')

    def test_verify_detects_chunk_count_mismatch(self):
        result = ns['kb_entry_verify_index'](is_admin=True)
        items = [x for x in result['issues']['chunk_count_mismatch'] if x['entry_id'] == 'e2']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['expected'], 3)
        self.assertEqual(items[0]['actual'], 2)

    def test_verify_detects_model_drift(self):
        result = ns['kb_entry_verify_index'](is_admin=True)
        items = [x for x in result['issues']['model_drift'] if x['entry_id'] == 'e3']
        self.assertEqual(len(items), 1)
        self.assertIn('text-embedding-3-small', items[0]['models'])
        self.assertIn('text-embedding-ada-002', items[0]['models'])

    def test_verify_detects_orphan_chunks(self):
        result = ns['kb_entry_verify_index'](is_admin=True)
        items = [x for x in result['issues']['orphan_chunks'] if x['chunk_id'] == 'c-orphan-1']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['entry_id'], 'e-nonexistent')

    def test_verify_healthy_entry_not_in_any_issues(self):
        result = ns['kb_entry_verify_index'](is_admin=True)
        for issue_type, items in result['issues'].items():
            ids = [x.get('entry_id') for x in items]
            self.assertNotIn('e4', ids, f'e4 不应出现在 {issue_type}')

    def test_verify_requires_admin(self):
        with self.assertRaises(PermissionError):
            self.ns['kb_entry_verify_index'](is_admin=False)

    def test_verify_respects_limit_per_type(self):
        result = ns['kb_entry_verify_index'](limit_per_type=0, is_admin=True)
        # limit=0 极端测试, 看 truncated 标记; 各 issues 应为 0 条
        # 注: limit_per_type=0 时 SQL LIMIT 0 会拿空结果
        for issue_type, items in result['issues'].items():
            self.assertEqual(len(items), 0, f'{issue_type} 在 limit=0 应为 0')


class TestRepairDryRun(unittest.TestCase):
    """场景 2: repair dry-run 返回 plan 但不真改"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        _insert_entry('e1', title='Missing', status='ok', chunk_count=0)
        _insert_entry('e2', title='Healthy', status='ok', chunk_count=2)
        _insert_chunk('c2a', 'e2', 'a', embedding=b'\x00' * 4, model='m1')
        _insert_chunk('c2b', 'e2', 'b', embedding=b'\x00' * 4, model='m1')
        _insert_chunk('c-orphan', 'ghost', 'x')
        self._db.commit()

    def test_dry_run_returns_plan_no_changes(self):
        result = ns['kb_entry_repair_index'](confirm=False, is_admin=True)
        self.assertTrue(result['dry_run'])
        self.assertIn('actions_planned', result)
        self.assertNotIn('actions_executed', result)

        # 验证数据库未被修改
        chunk_count = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks").fetchone()['c']
        self.assertEqual(chunk_count, 3, 'dry-run 不应删任何 chunk')

    def test_dry_run_plans_orphan_delete(self):
        result = ns['kb_entry_repair_index'](confirm=False, is_admin=True)
        plan_types = [a['type'] for a in result['actions_planned']]
        self.assertIn('delete_orphan_chunks', plan_types)

        orphan_action = next(a for a in result['actions_planned'] if a['type'] == 'delete_orphan_chunks')
        self.assertEqual(orphan_action['count'], 1)
        self.assertEqual(orphan_action['chunk_ids'], ['c-orphan'])

    def test_dry_run_plans_rebuild_for_missing(self):
        result = ns['kb_entry_repair_index'](confirm=False, is_admin=True)
        rebuild_actions = [a for a in result['actions_planned'] if a['type'] == 'rebuild_entry_chunks']
        rebuild_ids = [a['entry_id'] for a in rebuild_actions]
        self.assertIn('e1', rebuild_ids, 'e1 (missing_chunks) 应被 plan 重建')
        self.assertNotIn('e2', rebuild_ids, 'e2 (健康) 不应被 plan')

    def test_repair_requires_admin(self):
        with self.assertRaises(PermissionError):
            self.ns['kb_entry_repair_index'](is_admin=False)


class TestRepairConfirm(unittest.TestCase):
    """场景 3: repair confirm 真改 — 删孤儿 + 重建 entries"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        # e1: missing_chunks (status=ok 但 0 chunks)
        _insert_entry('e1', title='Missing', status='ok', chunk_count=0, content='content-1')
        # e3: model_drift
        _insert_entry('e3', title='Drift', status='ok', chunk_count=2, content='content-3')
        _insert_chunk('c3a', 'e3', 'a', embedding=b'\x00' * 4, model='old-model')
        _insert_chunk('c3b', 'e3', 'b', embedding=b'\x00' * 4, model='new-model')
        # 孤儿
        _insert_chunk('c-orphan', 'ghost', 'x')
        # 注入 mock api_key (否则 _vectorize_kb_chunks 抛错, repair 重建会失败)
        self.ns['get_embedding_config'] = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai',
            'model': 'current-model', 'baseUrl': None
        }
        self._db.commit()

    def test_confirm_deletes_orphans(self):
        result = ns['kb_entry_repair_index'](confirm=True, is_admin=True)
        self.assertFalse(result['dry_run'])
        self.assertIn('actions_executed', result)
        self.assertGreaterEqual(result['stats']['orphan_deleted'], 1)

        # 验证孤儿已删
        count = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE id='c-orphan'").fetchone()['c']
        self.assertEqual(count, 0, '孤儿 chunk 应被删')

    def test_confirm_rebuilds_missing_chunks(self):
        result = ns['kb_entry_repair_index'](confirm=True, is_admin=True)
        self.assertGreaterEqual(result['stats']['rebuild_succeeded'], 1)

        # 验证 e1 现在有 chunks (mock 写了 2 个)
        count = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(count, 2, 'e1 重建后应有 2 个 chunks')
        # 验证 chunk_count 已更新
        row = _db.execute("SELECT chunk_count, status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['chunk_count'], 2)
        self.assertEqual(row['status'], 'ok', '重建后 status 恢复 ok')

    def test_confirm_rebuilds_model_drift(self):
        result = ns['kb_entry_repair_index'](confirm=True, is_admin=True)

        # 验证 e3 重建后只用 current-model
        models = _db.execute(
            "SELECT DISTINCT embedding_model FROM kb_entry_chunks WHERE entry_id='e3' AND embedding_model != ''"
        ).fetchall()
        model_list = [m['embedding_model'] for m in models]
        self.assertEqual(model_list, ['current-model'], f'重建后只该用 current-model, 实际 {model_list}')

    def test_confirm_preserves_pending_status(self):
        """pending 条目重建后保持 pending (审核闸: 不自动过审)"""
        self._db.execute("UPDATE kb_entries SET status='pending' WHERE id='e1'")
        self._db.commit()
        self.ns['kb_entry_repair_index'](confirm=True, is_admin=True)
        row = _db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'pending', 'pending 条目重建后应保持 pending')


class TestRepairFailureIsolation(unittest.TestCase):
    """场景 4: 某条 entry 重建失败不影响其他"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        # e1: 正常可重建
        _insert_entry('e1', title='Normal', status='ok', chunk_count=0, content='c1')
        # e2: 构造一个会触发 rebuild 失败的 entry (status='deleted' 在 repair 内被过滤, _save 会返回 0 rows 不出错;
        # 这里用更直接的方式: 把它的 content 设为 None, mock 不会出错但 _save 会写空 chunks;
        # 改用更稳的方法: 让 _vectorize_kb_chunks 对 e2 抛错, 模拟 embedding API 失败)
        _insert_entry('e2', title='WillFail', status='ok', chunk_count=0, content='c2')
        # 让 _vectorize_kb_chunks 对 e2 抛错
        orig_vectorize = ns['_vectorize_kb_chunks']
        def selective_vectorize(entry_id, *args, **kwargs):
            if entry_id == 'e2':
                raise RuntimeError('mock embedding API failure for e2')
            return orig_vectorize(entry_id, *args, **kwargs)
        self.ns['_vectorize_kb_chunks'] = selective_vectorize
        # 注入 mock api_key
        self.ns['get_embedding_config'] = lambda emp_id=None: {
            'apiKey': 'mock-key', 'provider': 'openai',
            'model': 'm', 'baseUrl': None
        }
        self._db.commit()

    def test_one_failure_does_not_block_others(self):
        result = ns['kb_entry_repair_index'](confirm=True, is_admin=True)
        # e1 应成功, e2 应失败
        executed = result['stats']
        self.assertGreaterEqual(executed['rebuild_succeeded'], 1, 'e1 应被成功重建')
        self.assertGreaterEqual(executed['rebuild_failed'], 1, 'e2 应失败')
        self.assertGreater(len(executed['errors']), 0, '应有 error 信息')

        # e1 现在有 chunks, e2 没 (因为 _vectorize 失败后 status 标 error)
        e1_chunks = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(e1_chunks, 2, 'e1 应有 2 个 mock chunks')
        e2_status = _db.execute("SELECT status FROM kb_entries WHERE id='e2'").fetchone()['status']
        self.assertEqual(e2_status, 'error', 'e2 重建失败应标 error')


if __name__ == '__main__':
    _init_ns()
        unittest.main(verbosity=2)
