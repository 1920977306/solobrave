# -*- coding: utf-8 -*-
"""
KB 软删级联单测 (refactor/kb-soft-delete-cascade)

策略: 直接 import knowledge_service (产品函数 globals 永远是 ks.__dict__,
stub 注入真模块 dict 让产品代码自然命中). 弃用 ns 复制 + types.FunctionType
rebind 间接注入 (两张皮 — stub 注入 ns, 产品代码看 ks.__dict__, 13 个内部调
_db_conn 的用例全 NameError).

测试场景 (跟 commit message 对齐):
1. 软删级联: kb_entry_delete 后 status='deleted', chunks 清空, audit log 有记录
2. 事务回滚: chunks DELETE 失败时 status 不变 (sqlite 强制外键或 NOT NULL 失败模拟)
3. 清理 API: kb_entry_cleanup_dangling 只删 status='deleted' 且 updated_at < cutoff
4. 单条 GET 过滤: 软删后 kb_entry_get_by_id 返回 None

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-mini-test-repair && python3 -m pytest tests/test_kb_cascade.py -v
"""
import os, sys, json, sqlite3, time, re
import unittest
import knowledge_service as ks  # ★ fix/mini-test-code-repair-20260925 19:17: 直接 import, 产品函数 globals 永远是 ks.__dict__


# ★ fix/mini-test-code-repair-20260925 工单 FINAL 17:27: 产品代码 close() 不生效代理.
# 治根因: 产品代码 (kb_entry_cleanup_dangling / kb_entries_reindex_pending)
# 在事务结束后调 self._db_conn().close() 关连接, 但测试下一 setUp 又开
# 新 in-memory 连接, 新连接被产品 close 抛 'Cannot operate on a closed database'.
# _ImmuneConn 让产品 close() 调用是 no-op, 测试 setUp 自己 addCleanup 关.
class _ImmuneConn:
    def __init__(self, real):
        self._real = real
    def close(self):
        pass  # 产品代码 close 不生效
    def __getattr__(self, name):
        return getattr(self._real, name)


# 初始化测试表 (kb_entries + kb_entry_chunks + kb_operation_log)
# 照抄 knowledge_service.init_db() 真实 schema (含 emp_id)
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


class TestSoftDeleteCascade(unittest.TestCase):
    """场景 1: 软删级联 — status='deleted' + chunks 清空 + audit log 记录"""

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 19:17: monkeypatch ks._db_conn (产品函数 globals 永远是 ks.__dict__)
        self._db = sqlite3.connect(':memory:', check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA foreign_keys = OFF')
        self.addCleanup(self._db.close)
        init_test_tables(self._db)
        # _ImmuneConn: 让产品代码 close() 是 no-op
        self._orig_db_conn = ks._db_conn
        ks._db_conn = lambda timeout=30: _ImmuneConn(self._db)
        self.addCleanup(setattr, ks, '_db_conn', self._orig_db_conn)
        # audit log helper stub (软删/物理删/清理 3 处会调)
        self._orig_log_op = ks.kb_entry_log_operation
        ks.kb_entry_log_operation = lambda entry_id, op, op_id, details=None: None
        self.addCleanup(setattr, ks, 'kb_entry_log_operation', self._orig_log_op)
        # 测试数据
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
        result = ks.kb_entry_delete('e1', is_admin=True, operator_id='u1')
        self.assertTrue(result)

        # 1. status 改 'deleted'
        row = self._db.execute("SELECT status FROM kb_entries WHERE id='e1'").fetchone()
        self.assertEqual(row['status'], 'deleted', '软删后 status 应该是 deleted')

        # 2. chunks 清空 (防 RAG 命中)
        count = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(count, 0, '软删后该 doc 的 chunks 应该清空')

        # 3. audit log 有 soft_delete 记录
        logs = self._db.execute("SELECT operation, operator_id FROM kb_operation_log WHERE entry_id='e1'").fetchall()
        self.assertEqual(len(logs), 1, '应该写 1 条 audit log')
        self.assertEqual(logs[0]['operation'], 'soft_delete')
        self.assertEqual(logs[0]['operator_id'], 'u1')

    def test_soft_delete_idempotent(self):
        """同一 entry 重复软删: 第二次返回 False, 不重复写 audit log"""
        ks.kb_entry_delete('e1', is_admin=True, operator_id='u1')
        result = ks.kb_entry_delete('e1', is_admin=True, operator_id='u1')
        self.assertFalse(result, '第二次软删应该返回 False (idempotent)')
        logs = self._db.execute("SELECT COUNT(*) AS c FROM kb_operation_log WHERE entry_id='e1'").fetchone()['c']
        self.assertEqual(logs, 1, '重复软删不该重复写 audit log')

    def test_get_by_id_filters_deleted(self):
        """软删后 kb_entry_get_by_id 返回 None"""
        ks.kb_entry_delete('e1', is_admin=True, operator_id='u1')
        result = ks.kb_entry_get_by_id('e1')
        self.assertIsNone(result, '软删后 GET 单条应该返回 None')

    def test_get_by_id_returns_active(self):
        """未软删文档正常返回"""
        result = ks.kb_entry_get_by_id('e1')
        self.assertIsNotNone(result)
        self.assertEqual(result['id'], 'e1')
        self.assertEqual(result['status'], 'ok')


class TestTransactionRollback(unittest.TestCase):
    """场景 2: 事务回滚 — chunks DELETE 失败时 status 不变"""

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
        self._orig_log_op = ks.kb_entry_log_operation
        ks.kb_entry_log_operation = lambda entry_id, op, op_id, details=None: None
        self.addCleanup(setattr, ks, 'kb_entry_log_operation', self._orig_log_op)
        now_ms = int(time.time() * 1000)
        self._db.execute('''INSERT INTO kb_entries (id, title, content, scope, status, created_at, updated_at)
                       VALUES (?, ?, ?, 'global', 'ok', ?, ?)''',
                    ('e2', 'Test Doc 2', 'Test content 2', now_ms, now_ms))
        self._db.execute('''INSERT INTO kb_entry_chunks (id, entry_id, content, embedding)
                       VALUES (?, ?, ?, ?)''',
                    ('c1', 'e2', 'chunk', b'\x00'))
        self._db.commit()

    def test_audit_log_failure_rolls_back_status_change(self):
        """audit log INSERT 失败 → 整个事务回滚, status 仍 'ok'"""
        # 方案: DROP kb_operation_log 让 INSERT 抛 "no such table" 异常
        self._db.execute('DROP TABLE kb_operation_log')
        try:
            ks.kb_entry_delete('e2', is_admin=True, operator_id='u1')
            self.fail('应该抛异常 (audit INSERT 失败)')
        except Exception:
            # 事务回滚, status 仍 ok
            row = self._db.execute("SELECT status FROM kb_entries WHERE id='e2'").fetchone()
            self.assertEqual(row['status'], 'ok', '事务回滚, status 应仍为 ok')
            chunks = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='e2'").fetchone()['c']
            self.assertEqual(chunks, 1, '事务回滚, chunks 应未清')


class TestCleanupDangling(unittest.TestCase):
    """场景 3: 兜底清理 — 物理删 status='deleted' 超过 N 天的记录"""

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
        self._orig_log_op = ks.kb_entry_log_operation
        ks.kb_entry_log_operation = lambda entry_id, op, op_id, details=None: None
        self.addCleanup(setattr, ks, 'kb_entry_log_operation', self._orig_log_op)
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
        stats = ks.kb_entry_cleanup_dangling(days_old=7, is_admin=True)
        self.assertEqual(stats['scanned'], 2, 'old1+old2 在 cutoff 内, recent 不扫描, scanned=2')
        self.assertEqual(stats['hard_deleted'], 2, 'old1 + old2 共 2 条物理删')
        self.assertEqual(set(stats['deleted_entry_ids']), {'old1', 'old2'})
        self.assertEqual(stats['error_count'], 0)

        # recent 应保留
        row = self._db.execute("SELECT id FROM kb_entries WHERE id='recent'").fetchone()
        self.assertIsNotNone(row, 'recent (3 天前) 应保留')

        # old1/old2 应物理删
        self.assertIsNone(self._db.execute("SELECT id FROM kb_entries WHERE id='old1'").fetchone())
        self.assertIsNone(self._db.execute("SELECT id FROM kb_entries WHERE id='old2'").fetchone())

        # chunks 也应清
        chunks = self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id IN ('old1','old2')").fetchone()['c']
        self.assertEqual(chunks, 0, '物理删后 chunks 应清空')

    def test_cleanup_requires_admin(self):
        """非 admin 调用应抛 PermissionError"""
        with self.assertRaises(PermissionError):
            ks.kb_entry_cleanup_dangling(days_old=7, is_admin=False)

    def test_cleanup_no_candidates(self):
        """没有 status='deleted' 时 hard_deleted=0"""
        self._db.execute("DELETE FROM kb_entries WHERE status='deleted'")
        self._db.commit()
        stats = ks.kb_entry_cleanup_dangling(days_old=7, is_admin=True)
        self.assertEqual(stats['scanned'], 0)
        self.assertEqual(stats['hard_deleted'], 0)


class TestHardDelete(unittest.TestCase):
    """kb_entry_hard_delete — 物理删(供 admin 工具/兜底手动清理)"""

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
        self._orig_log_op = ks.kb_entry_log_operation
        ks.kb_entry_log_operation = lambda entry_id, op, op_id, details=None: None
        self.addCleanup(setattr, ks, 'kb_entry_log_operation', self._orig_log_op)
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
        result = ks.kb_entry_hard_delete('h1', is_admin=True, operator_id='admin1')
        self.assertTrue(result)
        self.assertIsNone(self._db.execute("SELECT id FROM kb_entries WHERE id='h1'").fetchone())
        self.assertEqual(self._db.execute("SELECT COUNT(*) AS c FROM kb_entry_chunks WHERE entry_id='h1'").fetchone()['c'], 0)
        logs = self._db.execute("SELECT operation, operator_id FROM kb_operation_log WHERE entry_id='h1'").fetchall()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]['operation'], 'hard_delete')
        self.assertEqual(logs[0]['operator_id'], 'admin1')


if __name__ == '__main__':
    print('=' * 60)
    print('KB 软删级联单测 (refactor/kb-soft-delete-cascade)')
    print('=' * 60)
    unittest.main(verbosity=2)