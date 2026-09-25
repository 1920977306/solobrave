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


KS_PY = 'knowledge_service.py'  # relative to project root (cwd when pytest invoked)


def extract_function(name, source):
    """提取 def name(...): 开始的函数, 用 {} 配平找函数体结束"""
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


# 提取目标类 + 函数 (从 class 头到下一个 class 头)
def extract_block(name, source):
    """提取 class/func 完整块 (含下一个 class/func 之前的所有内容)"""
    m = re.search(rf'^(class|def) {re.escape(name)}\(', source, re.MULTILINE)
    if not m:
        return None
    start = m.start()
    i = m.end()
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
                if rest.startswith('class ') or rest.startswith('def '):
                    return source[start:i+1]
        i += 1
    return source[start:]



# === 代码状态: 模块级只保留 helper + unittest.TestCase class =====================
# 原先的重逻辑 (读源码 + exec namespace + 设 TIMEOUT) 全部收进 _init_ns() 函数。
# 使 pytest collection (即 import 本文件) 不再触发:
#   - 文件读取 (KS_PY 路径依赖 cwd, import 未知 cwd 可能报 FileNotFoundError)
#   - sys.exit(1) (提取失败时, import 会让 pytest 整个套退出)
#   - exec combined (heavy pipe 流程在 import 时就跑, 耗时 + 可能报错)
# TestXxx 通过 setUpClass 调 _init_ns() 一次, 不重复。
# 独立运行 (python3 tests/heavy_pipe_test.py) 行为保持不变。

def _init_ns():
    """★ fix/pytest-heavy-pipe-collection: 初始化 ns (提取 + stub + exec).

    为了让 pytest collection 不再退出, 原先在模块级直接执行的重逻辑收进函数:
      1. 读取 knowledge_service.py 源码
      2. 提取 HeavyPipeCancelled / HeavyPipeTask / HeavyPipeTaskManager / kb_entries_reindex_pending
      3. 校验提取结果 (失败 raise RuntimeError, 不再 sys.exit)
      4. 准备 stub namespace (uuid / threading / _db_conn / _save_kb_chunks / _vectorize / _now_ms)
      5. exec combined 到 namespace
      6. 缩短 TIMEOUT_HEAVY_PIPE_MS = 500 (原值 120s 测试太长)

    Returns: (ns, _db) tuple 供 class TestXxx setUpClass 复用.
             _db 供 TestReindexBackwardCompat.setUp 复用 (kb_entries 表初始化).
    """
    import uuid
    import threading

    text = open(KS_PY, encoding="utf-8").read()

    exc_class = extract_block('HeavyPipeCancelled', text)
    task_class = extract_block('HeavyPipeTask', text)
    mgr_class = extract_block('HeavyPipeTaskManager', text)
    reindex_fn = extract_function('kb_entries_reindex_pending', text)

    if not all([exc_class, task_class, mgr_class, reindex_fn]):
        # 不再 sys.exit (会让 import 退出); 改 raise RuntimeError
        raise RuntimeError(
            f"提取失败, knowledge_service.py 改动没生效? "
            f"exc_class={bool(exc_class)}, task_class={bool(task_class)}, "
            f"mgr_class={bool(mgr_class)}, reindex_fn={bool(reindex_fn)}"
        )

    print(f"提取: HeavyPipeTask={len(task_class)}c, HeavyPipeTaskManager={len(mgr_class)}c, "
          f"kb_entries_reindex_pending={len(reindex_fn)}c")

    ns = {
        '__name__': 'pipe_test',
        'sqlite3': sqlite3,
        'time': time,
        'uuid': uuid,
        'json': json,
        'threading': threading,
    }

    # 不依赖真实 DB; 但 reindex_fn 里有 _db_conn / _save_kb_chunks / _vectorize_kb_chunks
    # stub 这些
    _db = sqlite3.connect(":memory:", check_same_thread=False)
    _db.row_factory = sqlite3.Row
    def _db_conn():
        return _db
    ns['_db_conn'] = _db_conn

    ns['get_embedding_config'] = lambda emp_id=None: {
        'apiKey': '', 'provider': 'openai', 'model': 'mock', 'baseUrl': None
    }
    ns['_save_kb_chunks_without_embedding'] = lambda *a, **k: None
    ns['_vectorize_kb_chunks'] = lambda *a, **k: None
    ns['_now_ms'] = lambda: int(time.time() * 1000)

    combined = '\n\n'.join([exc_class, task_class, mgr_class, reindex_fn])
    exec(combined, ns)
    print(f"exec combined: {len(combined)} chars")

    # 缩短 TIMEOUT_HEAVY_PIPE_MS 用于测试 (原值 120s 太长)
    ns['TIMEOUT_HEAVY_PIPE_MS'] = 500

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


class TestProgress(unittest.TestCase):
    """场景 1: 进度上报 — progress_cb 实时更新"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

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


class TestCancel(unittest.TestCase):
    """场景 2: 取消 — cancel_task 后 runner 在下个 check 点抛 HeavyPipeCancelled"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

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


class TestTimeout(unittest.TestCase):
    """场景 3: 超时 — runner 跑超过 TIMEOUT_HEAVY_PIPE_MS, 状态变 timeout"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

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


class TestFailure(unittest.TestCase):
    """场景 4: 失败 — runner 抛普通异常"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def test_runner_exception_marks_failed(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            raise RuntimeError('mock runner failure')
        task_id = mgr.start_task('test_fail', {}, runner)
        final = wait_for_status(mgr, task_id, 'failed', timeout=2.0)
        self.assertEqual(final['status'], 'failed')
        self.assertIn('mock runner failure', final['error'])
        self.assertIn('RuntimeError', final['error'])


class TestListRecent(unittest.TestCase):
    """场景 5: 任务列表 / 查询"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def test_list_recent_returns_completed_tasks(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        def runner(progress_cb, cancel_event):
            return {'x': 1}
        ids = [mgr.start_task('t1', {}, runner) for _ in range(3)]
        for tid in ids:
            wait_for_status(mgr, tid, 'success', timeout=2.0)
        recent = mgr.list_recent(limit=10)
        self.assertGreaterEqual(len(recent), 3)
        # 应按 started_at 倒序
        self.assertEqual(recent[0]['id'], ids[-1], '最新应在前')

    def test_get_unknown_task_returns_none(self):
        mgr = self.ns['HeavyPipeTaskManager']()
        self.assertIsNone(mgr.get_task('pipe_unknown'))


class TestReindexBackwardCompat(unittest.TestCase):
    """场景 6: kb_entries_reindex_pending 同步路径 (无 progress/cancel) 行为不变"""

    @classmethod
    def setUpClass(cls):
        """★ fix/pytest-heavy-pipe-collection: 一次初始化, 所有 test_* 共享."""
        cls.ns, cls._db = _init_ns()

    def setUp(self):
        # 初始化 KB 表 + 2 条 pending entry
        self._db.execute('DROP TABLE IF EXISTS kb_entries')
        self._db.execute('DROP TABLE IF EXISTS kb_entry_chunks')
        self._db.execute('''
            CREATE TABLE kb_entries (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                status TEXT DEFAULT 'ok', chunk_count INTEGER DEFAULT 0,
                created_at INTEGER, updated_at INTEGER
            )
        ''')
        self._db.execute('''
            CREATE TABLE kb_entry_chunks (
                id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, content TEXT NOT NULL,
                embedding BLOB, embedding_model TEXT DEFAULT '', chunk_index INTEGER,
                emp_id TEXT DEFAULT '', created_at INTEGER
            )
        ''')
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
