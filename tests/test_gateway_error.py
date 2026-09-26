# -*- coding: utf-8 -*-
"""
OpenClaw gateway 错误兜底单测 (refactor/openclaw-gateway-error-handling)

策略: 镜像 index.html L8897-8982 的 apiFetchWithRetry 函数 (含 401/403/409 mock
行为), exec 到独立 namespace 跑。模拟 fetch 在不同场景下的行为,验证 4 个核心场景。

测试场景:
1. 网络错误 (Failed to fetch): 重试 1 次后仍失败 → {success:false, error:'Failed to fetch', attempts:2}
2. HTTP 4xx/5xx: 解析 error body → {success:false, error, status}
3. JSON parse 失败: 200 状态但 body 不是 JSON → {success:false, error:'invalid_response'}
4. timeout: AbortController 10s 超时 → 重试 1 次后 → {success:false, error:'timeout', attempts:2}

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-gateway-err && python tests/gateway_error_test.py
   Node.js 端 sanity check (.tmp/gateway_error_sanity.js) 验证 Python 镜像逻辑正确性
"""
import os, sys, json, re

def _init_ns():
    """★ fix/mini-test-code-repair-20260925 P0-4: 初始化 ns (提取 + stub + exec).

    原先在模块级直接执行的 import 时副作用全部收进函数:
      1. text = open(IDX_HTML) (cwd 依赖)
      2. re.search + sys.exit(1) (import 时让 pytest 整个套退出)
      3. ns 创建 + mockResponses + exec MOCK_API_FETCH + exec fn_text

    Returns: ns dict 供 class TestXxx setUpClass 复用.
    """
    text = open(IDX_HTML, encoding="utf-8").read()

    m = re.search(r'async function apiFetchWithRetry\(', text)
    if not m:
        # 不再 sys.exit (会让 import 退出), 改 raise RuntimeError
        raise RuntimeError('找不到 apiFetchWithRetry 函数定义 (index.html 改动没生效?)')
    start = m.start()
    # 配平 {} 找函数体结束
    i = text.index('{', m.end())
    depth = 0
    while i < len(text):
        c = text[i]
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_text = text[start:i+1]
    print(f'提取 apiFetchWithRetry: {len(fn_text)} chars')

    ns = {
        '__name__': 'gw_test',
        'localStorage': {},  # mock, apiFetch 不调
        'console': console,
        'AbortController': None,  # 用 setTimeout 模拟, 不引入 AbortController
    }

    # 模拟 apiFetch (复制 index.html 里的, 401/403/409 特殊处理)
    MOCK_API_FETCH = """
async function apiFetch(url, options) {
  var resp = mockResponses[url];
  if (!resp) { throw new Error('No mock for ' + url); }
  if (resp.type === 'throw') { throw new Error(resp.error); }
  if (resp.type === '401') { return null; }
  if (resp.type === '403_get') { return { ok: false, status: 403, json: async () => { throw new Error('parse fail'); } }; }
  if (resp.type === '403_post') { throw new Error('权限不足 (403)'); }
  if (resp.type === '200_ok') { return { ok: true, status: 200, json: async () => resp.body }; }
  if (resp.type === '200_bad_json') { return { ok: true, status: 200, json: async () => { throw new Error('SyntaxError'); } }; }
  if (resp.type === '500') { return { ok: false, status: 500, json: async () => resp.body || { error: 'server error' } }; }
  return resp;
}
"""
    ns['mockResponses'] = {}  # 测试时注入

    exec(MOCK_API_FETCH, ns)
    exec(fn_text, ns)
    print(f'exec apiFetchWithRetry: {fn_text[:80]}...')
    return ns


class FakeAbortController:
    def __init__(self):
        self.signal = {'aborted': False}
        self._timeout = None
    def abort(self):
        self.signal['aborted'] = True


def call_with_mock(ns, mock_map, url, options=None, max_retries=2, abort_controller_factory=None):
    """执行 apiFetchWithRetry, 用 mock_map 替换 mockResponses."""
    ns['mockResponses'] = mock_map
    if abort_controller_factory:
        self.ns['AbortController'] = abort_controller_factory
    return ns['apiFetchWithRetry'](url, options or {}, max_retries)


import unittest


class TestNetworkError(unittest.TestCase):
    """场景 1: 网络错误 → 重试 1 次后仍失败"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_network_error_retry_then_fail(self):
        call_count = [0]
        def factory():
            call_count[0] += 1
            return FakeAbortController()
        # 模拟所有尝试都抛 Failed to fetch
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('Failed to fetch')
        self.ns['apiFetch'] = mock_api_fetch

        result = call_with_mock(self.ns, {}, '/api/test', abort_controller_factory=factory)
        self.assertFalse(result['success'])
        self.assertIn('Failed to fetch', result['error'])
        self.assertEqual(result['attempts'], 2, '应该 1+1=2 次调用')
        self.assertEqual(call_count[0], 2)


class TestHttpError(unittest.TestCase):
    """场景 2: HTTP 4xx/5xx → 解析 error body"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_500_with_json_error(self):
        self.ns['mockResponses'] = {
            '/api/test': {'type': '500', 'body': {'error': 'something bad'}}
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'something bad')
        self.assertEqual(result['status'], 500)
        self.assertEqual(result['attempts'], 1)

    def test_500_no_json_error_body(self):
        self.ns['mockResponses'] = {
            '/api/test': {'type': '500', 'body': None}  # body parse 失败, fallback to text
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertIn('server error', result['error'])
        self.assertEqual(result['status'], 500)

    def test_401_returns_no_response(self):
        """401 时 apiFetch 返回 null, apiFetchWithRetry 包装成 no_response"""
        self.ns['mockResponses'] = {'/api/test': {'type': '401'}}
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'no_response')
        self.assertEqual(result['status'], 0)


class TestJsonParseFailure(unittest.TestCase):
    """场景 3: 200 但 body 不是 JSON → invalid_response"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_bad_json(self):
        self.ns['mockResponses'] = {
            '/api/test': {'type': '200_bad_json'}
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'invalid_response')
        self.assertEqual(result['status'], 200)


class TestTimeout(unittest.TestCase):
    """场景 4: timeout → AbortController 10s 超时 → 重试 1 次"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_timeout_retry_then_fail(self):
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            # 模拟 AbortError
            e = RuntimeError('aborted')
            e.name = 'AbortError'
            raise e
        self.ns['apiFetch'] = mock_api_fetch

        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(call_count[0], 2, '超时应该重试 1 次')


class TestSuccess(unittest.TestCase):
    """成功路径: 200 + 有效 JSON → success: true"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_success(self):
        self.ns['mockResponses'] = {
            '/api/test': {'type': '200_ok', 'body': {'hello': 'world'}}
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertTrue(result['success'])
        self.assertEqual(result['data'], {'hello': 'world'})
        self.assertIsNone(result['error'])
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['attempts'], 1)


class TestNonRetryableError(unittest.TestCase):
    """不可重试错误: apiFetch 抛 '权限不足 (403)' 等业务错 → 立即失败, 不重试"""

    @classmethod
    def setUpClass(cls):
        """★ fix/mini-test-code-repair-20260925: 一次初始化, 所有 test_* 共享."""
        cls.ns = _init_ns()

    def test_403_post_no_retry(self):
        """403 POST 时 apiFetch 抛 '权限不足 (403)'"""
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('权限不足 (403)')
        self.ns['apiFetch'] = mock_api_fetch

        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], '权限不足 (403)')
        self.assertEqual(result['attempts'], 1, '403 业务错不该重试')
        self.assertEqual(call_count[0], 1)


if __name__ == '__main__':
    print('=' * 60)
    print('OpenClaw gateway 错误兜底单测 (refactor/openclaw-gateway-error-handling)')
    print('=' * 60)
    _init_ns()
        unittest.main(verbosity=2)
