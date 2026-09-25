# -*- coding: utf-8 -*-
"""
KB 软删级联单测 (refactor/kb-soft-delete-cascade)

策略: 镜像 knowledge_service.py 的关键函数 (kb_entry_delete 软删/级联/事务 +
kb_entry_cleanup_dangling 兜底清理), exec 到独立 namespace 跑 (避开 server 整个
import 链,只注入 sqlite3 + 必要 stdlib)。

测试场景 (跟 commit message 对齐):
1. 软删级联: kb_entry_delete 后 status='deleted', chunks 清空, audit log 有记录
2. 事务回滚: chunks DELETE 失败时 status 不变 (sqlite 强制外键或 NOT NULL 失败模拟)
3. 清理 API: kb_entry_cleanup_dangling 只删 status='deleted' 且 updated_at < cutoff
4. 单条 GET 过滤: 软删后 kb_entry_get_by_id 返回 None

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-kb-cascade && python tests/kb_cascade_test.py
   Node.js 端 sanity check (.tmp/kb_cascade_sanity.js) 验证 Python 镜像逻辑正确性
"""
import os, sys, json, sqlite3, time, re

# 1. 找到 knowledge_service.py 里的新代码 (kb_entry_delete 改造 + 新函数)
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

    # 抓 "def kb_entry_delete" (新版本含软删) 到 "def kb_entry_cleanup_dangling" 函数体结束
    # 简化: 直接 regex 提取 3 个目标函数
    def extract_function(name, source):
        """提取 def name(...):  开始的函数 (含 docstring + 函数体), 用 {} 配平找到函数体结束"""
        m = re.search(rf'^def {re.escape(name)}\(', source, re.MULTILINE)
        if not m:
            return None
        start = m.start()
        # 找函数体结束: 配平 {}
        i = source.index(':', m.end()) + 1
        depth = 0
        in_string = False
        triple = False
        while i < len(source):
            c = source[i]
            if not in_string and c == '#':
                # 注释到行尾
                while i < len(source) and source[i] != '\n':
                    i += 1
                continue
            if c == '"' or c == "'":
                # 检查三引号
                if source[i:i+3] in ('"""', "'''"):
                    triple = not triple
                    i += 3
                    continue
                if not triple:
                    in_string = not in_string
            if not in_string and not triple:
                if c == '{': depth += 1
                elif c == '}': depth -= 1
                elif c == '\n' and depth == 0:
                    # 顶层行开始, 检查下一个 def/class
                    rest = source[i+1:].lstrip()
                    if rest.startswith('def ') or rest.startswith('class ') or rest.startswith('# ') or rest.startswith('#!'):
                        return source[start:i+1]
            i += 1
        return source[start:]


    delete_fn = extract_function('kb_entry_delete', text)
    hard_delete_fn = extract_function('kb_entry_hard_delete', text)
    cleanup_fn = extract_function('kb_entry_cleanup_dangling', text)
    get_by_id_fn = extract_function('kb_entry_get_by_id', text)

    if not (delete_fn and hard_delete_fn and cleanup_fn and get_by_id_fn):
        print('FATAL: 提取函数失败, knowledge_service.py 改动没生效?')
        print(f'  delete_fn: {bool(delete_fn)}, hard_delete_fn: {bool(hard_delete_fn)}, cleanup_fn: {bool(cleanup_fn)}, get_by_id_fn: {bool(get_by_id_fn)}')
        raise RuntimeError(
            f'提取失败, knowledge_service.py 改动没生效? 检查提取结果是否为空'

    print(f'提取: kb_entry_delete={len(delete_fn)} chars, kb_entry_hard_delete={len(hard_delete_fn)} chars,')
    print(f'      kb_entry_cleanup_dangling={len(cleanup_fn)} chars, kb_entry_get_by_id={len(get_by_id_fn)} chars')

    # 2. 准备 stub namespace: sqlite3 in-memory + 必要依赖
    import uuid
    ns = {
        '__name__': 'ks_test',
        'sqlite3': sqlite3,
        'time': time,
        'uuid': uuid,
        'json': json,
    }

    # 提供 _db_conn (in-memory 单连接,所有 conn 共享)
    _db = sqlite3.connect(':memory:', check_same_thread=False)
    _db.row_factory = sqlite3.Row
    _db.execute('PRAGMA foreign_keys = OFF')  # 简化测试
    def _db_conn():
        return _db
    ns['_db_conn'] = _db_conn

    # 提供 audit log helper stub (软删/物理删/清理 3 处会调)
    ns['kb_entry_log_operation'] = lambda entry_id, op, op_id, details: None

    # 3. exec 函数到 namespace
    combined_src = '\n\n'.join([delete_fn, hard_delete_fn, cleanup_fn, get_by_id_fn])
    exec(combined_src, ns)
    print(f'exec combined: {len(combined_src)} chars')
    return ns, _db



# 4. 初始化测试表 (kb_entries + kb_entry_chunks + kb_operation_log)
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


class TestSoftDeleteCascade(unittest.TestCase):
    """场景 1: 软删级联 — status='deleted' + chunks 清空 + audit log 记录"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        now_ms = int(time.time() * 1000)
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'ok', ?, ?)''',
                    ('e1', 'Test Doc', 'Test content', now_ms, now_ms))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c1', 'e1', 'chunk 1', b'\x00\x01\x02'))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c2', 'e1', 'chunk 2', b'\x00\x03\x04'))
        self._db.commit()

    def test_soft_delete_marks_status_and_clears_chunks(self):
        result = ns['kb_entry_delete']('e1', is_admin=True, operator_id='u1')
        self.assertTrue(result)

        # 1. status 改 'deleted'
        row = _db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'deleted', '软删后 status 应该是 deleted')

        # 2. chunks 清空 (防 RAG 命中)
        count = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(count, 0, '软删后该 doc 的 chunks 应该清空')

        # 3. audit log 有 soft_delete 记录
        logs = _db.execute("SELECT operation, operator_id FROM kb_operation_log WHERE entry_id='e1'").fetchall()
        self.assertEqual(len(logs), 1, '应该写 1 条 audit log')
        self.assertEqual(logs[0]['operation'], 'soft_delete')
        self.assertEqual(logs[0]['operator_id'], 'u1')

    def test_soft_delete_idempotent(self):
        """同一 entry 重复软删: 第二次返回 False, 不重复写 audit log"""
        self.ns['kb_entry_delete']('e1', is_admin=True, operator_id='u1')
        result = ns['kb_entry_delete']('e1', is_admin=True, operator_id='u1')
        self.assertFalse(result, '第二次软删应该返回 False (idempotent)')
        logs = _db.execute("SELECT COUNT(*) AS c FROM kb_operation_log WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(logs, 1, '重复软删不该重复写 audit log')

    def test_get_by_id_filters_deleted(self):
        """软删后 kb_entry_get_by_id 返回 None"""
        self.ns['kb_entry_delete']('e1', is_admin=True, operator_id='u1')
        result = ns['kb_entry_get_by_id']('e1')
        self.assertIsNone(result, '软删后 GET 单条应该返回 None')

    def test_get_by_id_returns_active(self):
        """未软删文档正常返回"""
        result = ns['kb_entry_get_by_id']('e1')
        self.assertIsNotNone(result)
        self.assertEqual(result['id'], 'e1')
        self.assertEqual(result['status'], 'ok')


class TestTransactionRollback(unittest.TestCase):
    """场景 2: 事务回滚 — chunks DELETE 失败时 status 不变"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        now_ms = int(time.time() * 1000)
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'ok', ?, ?)''',
                    ('e2', 'Test Doc 2', 'Test content 2', now_ms, now_ms))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c1', 'e2', 'chunk', b'\x00'))
        self._db.commit()

    def test_audit_log_failure_rolls_back_status_change(self):
        """mock audit log 写入失败 → 整个事务回滚, status 仍 'ok'"""
        # patch kb_entry_log_operation 抛异常
        def failing_log(entry_id, op, op_id, details):
            raise RuntimeError('mock audit failure')
        old_log = ns.get('kb_entry_log_operation')
        # 注意: 函数体内直接调了 INSERT,不是 kb_entry_log_operation,
        # 但 exec combined_src 时用了我们提供的 stub
        # 在测试 namespace 里覆盖 INSERT 失败的方式: patch audit 行
        # 简化: 直接 patch 一个不存在的 symbol, 让 INSERT 失败
        # 实际函数体里用 INSERT 直接写, 我们要让它失败
        # 方案: DROP kb_operation_log 让 INSERT 抛 "no such table" 异常
        self._db.execute('DROP TABLE kb_operation_log')
        try:
            self.ns['kb_entry_delete']('e2', is_admin=True, operator_id='u1')
            self.fail('应该抛异常 (audit INSERT 失败)')
        except Exception as e:
            # 事务回滚, status 仍 ok
            row = _db.execute("SELECT status FROM kb_entries WHERE id='e2'").fetchone()
            self.assertEqual(row['status'], 'ok', '事务回滚, status 应仍为 ok')
            chunks = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e2'").fetchone()['c']
            self.assertEqual(chunks, 1, '事务回滚, chunks 应未清')


class TestCleanupDangling(unittest.TestCase):
    """场景 3: 兜底清理 — 物理删 status='deleted' 超过 N 天的记录"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        now_ms = int(time.time() * 1000)
        old_ms = now_ms - 10 * 24 * 60 * 60 * 1000  # 10 天前

        # 3 条 status='deleted': 1 条 10 天前(应被清理), 1 条 3 天前(保留), 1 条 8 天前(应被清理)
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'deleted', ?, ?)''',
                    ('old1', 'Old 1', 'content', old_ms, old_ms))
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'deleted', ?, ?)''',
                    ('recent', 'Recent', 'content', now_ms, now_ms - 3 * 86400000))
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'deleted', ?, ?)''',
                    ('old2', 'Old 2', 'content', old_ms, old_ms - 86400000))

        # 加 chunks (要确认 chunks 也被清)
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c1', 'old1', 'chunk', b'\x00'))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c2', 'recent', 'chunk', b'\x00'))
        self._db.commit()

    def test_cleanup_with_7_days_old(self):
        """days_old=7: 应物理删 old1 (10d) + old2 (8d), 保留 recent (3d)"""
        stats = ns['kb_entry_cleanup_dangling'](days_old=7, is_admin=True)
        self.assertEqual(stats['scanned'], 2, 'old1+old2 在 cutoff 内, recent 不扫描, scanned=2')
        self.assertEqual(stats['hard_deleted'], 2, 'old1 + old2 共 2 条物理删')
        self.assertEqual(set(stats['deleted_entry_ids']), {'old1', 'old2'})
        self.assertEqual(stats['error_count'], 0)

        # recent 应保留
        row = _db.execute("SELECT id FROM kb_entries WHERE id='recent'").fetchone()
        self.assertIsNotNone(row, 'recent (3 天前) 应保留')

        # old1/old2 应物理删
        self.assertIsNone(_db.execute("SELECT id FROM kb_entries WHERE id='old1'").fetchone())
        self.assertIsNone(_db.execute("SELECT id FROM kb_entries WHERE id='old2'").fetchone())

        # chunks 也应清
        chunks = _db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id IN ('old1','old2')").fetchone()['c']
        self.assertEqual(chunks, 0, '物理删后 chunks 应清空')

    def test_cleanup_requires_admin(self):
        """非 admin 调用应抛 PermissionError"""
        with self.assertRaises(PermissionError):
            self.ns['kb_entry_cleanup_dangling'](days_old=7, is_admin=False)

    def test_cleanup_no_candidates(self):
        """没有 status='deleted' 时 hard_deleted=0"""
        self._db.execute("DELETE FROM kb_entries WHERE status='deleted'")
        self._db.commit()
        stats = ns['kb_entry_cleanup_dangling'](days_old=7, is_admin=True)
        self.assertEqual(stats['scanned'], 0)
        self.assertEqual(stats['hard_deleted'], 0)


class TestHardDelete(unittest.TestCase):
    """kb_entry_hard_delete — 物理删(供 admin 工具/兜底手动清理)"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        self.ns['init_test_tables'](self._db_conn())
        now_ms = int(time.time() * 1000)
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'ok', ?, ?)''',
                    ('h1', 'Hard Delete', 'content', now_ms, now_ms))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c1', 'h1', 'chunk', b'\x00'))
        self._db.commit()

    def test_hard_delete_physically_removes(self):
        """硬删应该完全物理删 + chunks 清空 + audit 记录 'hard_delete'"""
        result = ns['kb_entry_hard_delete']('h1', is_admin=True, operator_id='admin1')
        self.assertTrue(result)
        self.assertIsNone(_db.execute("SELECT id FROM kb_entries WHERE id='h1'").fetchone())
        self.assertEqual(_db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='h1'").fetchone()['c'], 0)
        logs = _db.execute("SELECT operation, operator_id FROM kb_operation_log WHERE entry_id='h1'").fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['operation'], 'hard_delete')
        self.assertEqual(logs[0]['operator_id'], 'admin1')


if __name__ == '__main__':
    print('=' * 60)
    print('KB 软删级联单测 (refactor/kb-soft-delete-cascade)')
    print('=' * 60)
    _init_ns()
        unittest.main(verbosity=2)
