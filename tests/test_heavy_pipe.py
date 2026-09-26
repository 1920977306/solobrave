# -*- coding: utf-8 -*-
"""
Heavy Pipe 后台任务 + 超时 + 进度 + 取消 单测 (refactor/heavy-pipe-timeout)

策略: 镜像 knowledge_service.py 的 HeavyPipeTask / HeavyPipeTaskManager + 任务 runner,
exec 到独立 namespace 跑 (避开 server 整个 import 链)。

测试场景 (跟 commit message 对齐):
1. 进度上报: progress_cb 调用后 task.progress 正确更新, completed/total 同步
2. 取消: cancel_task 后, runner 在下个 check 点抛 HeavyPipeCancelled, status='cancelled'
3. 超时: 缩短 TIMEOUT_HEAVY_PIPE_MS, 跑超时 runner, status='timeout' + error 信息
4. 失败: runner 抛普通异常, status='failed' + error 信息
5. 同步路径: kb_entries_reindex_pending() 无 progress/cancel 参数, 行为不变 (向后兼容)
6. 端点路径: reindex 任务 (异步) + 同步模式 ?sync=true 返回结构不同

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-pipe && python tests/heavy_pipe_test.py
"""
import os, sys, json, sqlite3, time, re
import unittest  # ★ fix/mini-test-code-repair-20260925: 补 import (class TestXxx(unittest.TestCase) + unittest.main 引用)
import threading  # ★ fix/mini-test-code-repair-20260925 19:17: 补 import (commit 12 重写时丢, threading.Event 用于 cancel_event)
import knowledge_service  # ★ fix/mini-test-code-repair-20260925: 真 import, 替代 exec 拼字符串 (Mac 端 6 setup error 治法, 老 brief 12:37 拍板 real_import_light_stub)







def init_test_tables(conn):
    """★ fix/mini-test-code-repair-20260925 工单 FINAL: 照抄 knowledge_service.init_db() 真实 schema.

    治 KB 3 39 failed (setUp 调 self.ns['init_test_tables'] 但 _init_ns 没注入键)
    + 治 heavy_pipe B2 no such column: emp_id (TestReindexBackwardCompat.setUp
      旧 CREATE TABLE 缺 emp_id 列; kb_entries_reindex_pending 函数
      SELECT id, emp_id, ... FROM kb_entries 需要).

    Schema: kb_entries / kb_entry_chunks / kb_operation_log 三表
    (KB 3 跟 heavy_pipe 共享), 含 emp_id 列 + knowledge_service.init_db() 全部列.
    """
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


def _init_ns():
    """★ real_import_light_stub: 顶部已 import knowledge_service, ns = module.__dict__.copy()

    老 brief 12:37 拍板 real_import_light_stub: 真 import knowledge_service,
    替代原 regex extract_block + exec combined 拼字符串模式 (Mac 端 6 setup
    error 治法).

    ns = knowledge_service.__dict__.copy() 含所有 module 顶层 def + import.
    测试代码 self.ns['HeavyPipeTaskManager']() 等不变 — ns 是 module dict 副本,
    拿出来的类实例化时调 module globals (因为类定义 globals = module globals).

    stub 注入走 _HeavyPipeTestBase.setUp + addCleanup (module.X = mock_fn).
    类 globals 是 module (exec 模式隐式重绑已无), setUpClass 不再设 stub
    (避免污染 module 全局).

    Returns: (ns, _db) tuple 供 class TestXxx setUpClass 复用.
             _db 供 TestReindexBackwardCompat 复用.
    """
    _db = sqlite3.connect(":memory:", check_same_thread=False)
    _db.row_factory = sqlite3.Row
    def _db_conn():
        return _db

    ns = knowledge_service.__dict__.copy()
    ns['__name__'] = 'heavy_pipe_test'
    ns['_db'] = _db
    ns['_db_conn'] = _db_conn

    return ns, _db


def wait_for_status(mgr, task_id, target_status, timeout=3.0):
    """poll task 状态直到变成 target_status 或超时"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        task = mgr.get_task(task_id)
        if task and task['status'] == target_status:
            return task
        time.sleep(0.05)
    return mgr.get_task(task_id)


class _HeavyPipeTestBase(unittest.TestCase):
    """heavy_pipe 测试基类.

    ★ real_import_light_stub: setUpClass 一次 _init_ns() (加载 module),
    setUp 注入公共 stub (TIMEOUT + _now_ms) 走 module.X = mock_fn + addCleanup
    自动恢复. exec combined 模式隐式重绑类 globals=ns 已无, 类方法 globals
    = module globals, monkeypatch module 是 stub 注入机制.

    TestReindexBackwardCompat 重写 setUp 加 kb_entries_reindex_pending 特定
    stub (get_embedding_config / _save_kb_chunks / _vectorize / _db_conn).
    """

    @classmethod
    def setUpClass(cls):
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL: 每测试独立连接 (治 B3)
        # 类级共享连接被前一测试 cleanup 关闭 → 后一测试 'Cannot operate on a closed database'
        # 改每测试新建 sqlite3 in-memory + addCleanup 关, 弃用 setUpClass cls._db 共享
        _db = sqlite3.connect(':memory:', check_same_thread=False)
        _db.row_factory = sqlite3.Row
        self._db = _db
        self.addCleanup(_db.close)

        # 注入 _db_conn stub 走 self._db (kb_entries_reindex_pending 调 module._db_conn)
        orig_db_conn = getattr(knowledge_service, '_db_conn', None)
        setattr(knowledge_service, '_db_conn', lambda: _db)
        self.addCleanup(setattr, knowledge_service, '_db_conn', orig_db_conn)

        # 初始化 KB 表 (照抄 knowledge_service.init_db() 真实 schema, 含 emp_id - 治 B2)
        init_test_tables(_db)

        # 公共 stub: 类 globals 注入, 函数 globals=module 调到 stub
        for name, mock in [
            ('TIMEOUT_HEAVY_PIPE_MS', 500),  # 缩短默认 120s → 500ms 测试可控
            ('_now_ms', lambda: int(time.time() * 1000)),
        ]:
            orig = getattr(knowledge_service, name)
            setattr(knowledge_service, name, mock)
            self.addCleanup(setattr, knowledge_service, name, orig)


class TestProgress(_HeavyPipeTestBase):
    """场景 1: 进度上报 — progress_cb 实时更新"""

    def test_progress_callbacks_update_task_state(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            for i in range(5):
                if cancel_event.is_set():
                    raise self.ns['HeavyPipeCancelled']()
                time.sleep(0.05)
                progress_cb((i + 1) * 20, completed=i + 1, total=5)
            return {'done': 5}
        task_id = mgr.start_task('test_progress', {}, runner)
        # 等到完成
        final = wait_for_status(mgr, task_id, 'success', timeout=2.0)
        self.assertEqual(final['status'], 'success')
        self.assertEqual(final['progress'], 100, 'success 时 progress 应被 auto-fill 到 100')
        self.assertEqual(final['total'], 5)
        self.assertEqual(final['completed'], 5)
        self.assertEqual(final['result'], {'done': 5})

    def test_progress_reported_mid_run(self):
        """跑一半时 poll, 应看到 progress 在 0-100 之间"""
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            for i in range(10):
                time.sleep(0.1)
                progress_cb((i + 1) * 10, completed=i + 1, total=10)
            return {}
        task_id = mgr.start_task('test_progress', {}, runner)
        time.sleep(0.35)  # 让 3-4 步完成
        mid = mgr.get_task(task_id)
        self.assertEqual(mid['status'], 'running')
        self.assertGreater(mid['progress'], 0)
        self.assertLess(mid['progress'], 100)
        # 等完成
        wait_for_status(mgr, task_id, 'success', timeout=3.0)


class TestCancel(_HeavyPipeTestBase):
    """场景 2: 取消 — cancel_task 后 runner 在下个 check 点抛 HeavyPipeCancelled"""

    def test_cancel_short_circuits_runner(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            for i in range(20):
                if cancel_event.is_set():
                    raise self.ns['HeavyPipeCancelled']()
                time.sleep(0.1)
                progress_cb((i + 1) * 5, completed=i + 1, total=20)
            return {'done': 20}
        task_id = mgr.start_task('test_cancel', {}, runner)
        time.sleep(0.25)  # 跑 2-3 步
        # 取消
        self.assertTrue(mgr.cancel_task(task_id))
        # 等待 status=cancelled
        final = wait_for_status(mgr, task_id, 'cancelled', timeout=2.0)
        self.assertEqual(final['status'], 'cancelled')
        self.assertIsNotNone(final['ended_at'])
        # 进度应该 < 100 (被中断)
        self.assertLess(final['progress'], 100)

    def test_cancel_unknown_task_returns_false(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        self.assertFalse(mgr.cancel_task('pipe_nonexistent'))


class TestTimeout(_HeavyPipeTestBase):
    """场景 3: 超时 — runner 跑超过 TIMEOUT_HEAVY_PIPE_MS, 状态变 timeout"""

    def test_runner_exceeds_timeout_marks_timeout(self):
        # TIMEOUT_HEAVY_PIPE_MS 已被缩短为 500ms
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            # runner 不响应 cancel_event (模拟卡死/没 check)
            # watchdog 500ms 后 set cancel, 但 runner 继续跑
            # 我们让 runner 1.5s 后才 check cancel
            for i in range(15):
                time.sleep(0.1)
                # 这里 check cancel 才能让 runner 跑完取消
                if cancel_event.is_set():
                    raise self.ns['HeavyPipeCancelled']()
            return {'should_not_reach': True}
        task_id = mgr.start_task('test_timeout', {}, runner)
        # 等待 timeout (500ms watchdog + 1.5s runner)
        final = wait_for_status(mgr, task_id, 'timeout', timeout=3.0)
        self.assertEqual(final['status'], 'timeout', f'应为 timeout, 实际 {final["status"]}')
        self.assertIn('timeout after', final.get('error', ''), 'error 信息应包含 timeout 时长')

    def test_task_id_format(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            return {}
        task_id = mgr.start_task('test', {}, runner)
        self.assertTrue(task_id.startswith('pipe_'), f'task_id 应以 pipe_ 开头, 实际 {task_id}')


class TestFailure(_HeavyPipeTestBase):
    """场景 4: 失败 — runner 抛普通异常"""

    def test_runner_exception_marks_failed(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            raise RuntimeError('mock runner failure')
        task_id = mgr.start_task('test_fail', {}, runner)
        final = wait_for_status(mgr, task_id, 'failed', timeout=2.0)
        self.assertEqual(final['status'], 'failed')
        self.assertIn('mock runner failure', final['error'])
        self.assertIn('RuntimeError', final['error'])


class TestListRecent(_HeavyPipeTestBase):
    """场景 5: 任务列表 / 查询"""

    def test_list_recent_returns_completed_tasks(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            return {'x': 1}
        ids = [mgr.start_task('t1', {}, runner) for _ in range(3)]
        for tid in ids:
            wait_for_status(mgr, tid, 'success', timeout=2.0)
        # ★ fix/mini-test-code-repair-20260925 工单 FINAL B1: 删 sleep, 改显式写时间戳
        # 真 list_recent 是 sorted(self._tasks.values(), key=lambda t: t.started_at or 0, reverse=True)
        # 显式设 started_at 让排序确定性 (不靠 thread 调度运气)
        for i, tid in enumerate(ids):
            mgr._tasks[tid].started_at = 1000 + i
        recent = mgr.list_recent(limit=10)
        self.assertGreaterEqual(len(recent), 3)
        # 应按 started_at 倒序
        self.assertEqual(recent[0]['id'], ids[-1], '最新应在前')

    def test_get_unknown_task_returns_none(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        self.assertIsNone(mgr.get_task('pipe_unknown'))


class TestReindexBackwardCompat(_HeavyPipeTestBase):
    """场景 6: kb_entries_reindex_pending 同步路径 (无 progress/cancel) 行为不变"""


    def setUp(self):
        """★ real_import_light_stub: kb_entries_reindex_pending 特定 stub (addCleanup 恢复).

        base class 已注入 TIMEOUT + _now_ms (公共 stub).
        这里加 get_embedding_config + _save_kb_chunks + _vectorize + _db_conn
        让 kb_entries_reindex_pending 调 module globals 时查到 stub 而非真实实现.
        addCleanup 自动恢复 (不污染 module 全局).
        """
        super().setUp()
        for name, mock in [
            ('get_embedding_config', lambda emp_id=None: {
                'apiKey': '', 'provider': 'openai', 'model': 'mock', 'baseUrl': None
            }),
            ('_save_kb_chunks_without_embedding', lambda *a, **k: None),
            ('_vectorize_kb_chunks', lambda *a, **k: None),
            ('_db_conn', lambda: self._db),
        ]:
            orig = getattr(knowledge_service, name)
            setattr(knowledge_service, name, mock)
            self.addCleanup(setattr, knowledge_service, name, orig)

        # ★ fix/mini-test-code-repair-20260925 工单 FINAL:
        # base class setUp 已调 init_test_tables(self._db) 建表 (含 emp_id),
        # 这里只插 2 条 pending entry 给 kb_entries_reindex_pending 测试用
        now_ms = int(time.time() * 1000)
        for i in range(2):
            self._db.execute(
                'INSERT INTO kb_entries (id, title, content, status, chunk_count, created_at, updated_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (f'e{i}', f'Doc{i}', f'content{i}', 'pending', 0, now_ms, now_ms)
            )
        self._db.commit()

    def test_sync_call_returns_legacy_stats(self):
        """无 progress_cb/cancel_event 时, 行为跟老版完全一致 (无 cancelled 字段污染)"""
        result = self.ns['kb_entries_reindex_pending']()
        # 同步路径下 cancelled 应该 False, noKey 应该 2 (mock 无 api_key)
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['ok'], 0)
        self.assertEqual(result['noKey'], 2)
        self.assertEqual(result['failed'], 0)
        self.assertFalse(result['cancelled'])

    def test_sync_call_with_progress_and_cancel(self):
        """传 progress_cb + cancel_event, 应被调用"""
        progress_calls = []
        cancel_event = threading.Event()
        def progress_cb(pct, completed=None, total=None):
            progress_calls.append((pct, completed, total))
        result = self.ns['kb_entries_reindex_pending'](
            progress_cb=progress_cb, cancel_event=cancel_event
        )
        self.assertEqual(len(progress_calls), 3, '应 callback 3 次 (0 + 2 entries)')
        # 最后一次 progress 应是 100% (2/2)
        self.assertEqual(progress_calls[-1][0], 100)
        self.assertEqual(progress_calls[-1][1], 2)
        self.assertEqual(progress_calls[-1][2], 2)
        self.assertFalse(result['cancelled'])


if __name__ == '__main__':
    # ★ fix/pytest-heavy-pipe-collection: 原有独立运行行为保持不变 (python3 tests/heavy_pipe_test.py)
    # 在将模块级重逻辑收进 _init_ns 后, 独立运行路径仍然先调一次初始化
    _init_ns()
    unittest.main(verbosity=2)
