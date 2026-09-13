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

# 1. 提取 apiFetchWithRetry 函数
IDX_HTML = 'index.html'
text = open(IDX_HTML, encoding='utf-8').read()

# 抓 apiFetchWithRetry 函数体 (regex)
m = re.search(r'async function apiFetchWithRetry\(', text)
if not m:
    print('FATAL: 找不到 apiFetchWithRetry 函数定义')
    sys.exit(1)
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

# 2. 准备 stub namespace
ns = {
    '__name__': 'gw_test',
    'localStorage': {},  # mock, apiFetch 不调
    'console': console,
    'AbortController': None,  # 用 setTimeout 模拟, 不引入 AbortController
}

# 模拟 apiFetch (复制 index.html 里的, 401/403/409 特殊处理)
MOCK_API_FETCH = '''
async function apiFetch(url, options) {
  // mock apiFetch, 接收测试时注入的 mockResponses (按 URL 精确匹配)
  var resp = mockResponses[url];
  if (!resp) {
    throw new Error('No mock for ' + url);
  }
  if (resp.type === 'throw') {
    throw new Error(resp.error);
  }
  if (resp.type === '401') {
    return null;  // apiFetch 401 返回 null
  }
  if (resp.type === '403_get') {
    return { ok: false, status: 403, statusText: 'Forbidden', json: async () => { throw new Error('parse fail'); }, text: async () => 'forbidden' };
  }
  if (resp.type === '403_post') {
    throw new Error('权限不足 (403)');
  }
  if (resp.type === '200_ok') {
    return { ok: true, status: 200, statusText: 'OK', json: async () => resp.body };
  }
  if (resp.type === '200_bad_json') {
    return { ok: true, status: 200, statusText: 'OK', json: async () => { throw new Error('SyntaxError'); } };
  }
  if (resp.type === '500') {
    return { ok: false, status: 500, statusText: 'Internal Server Error', json: async () => resp.body || { error: 'server error' }, text: async () => 'server error' };
  }
  return resp;
}
'''
ns['mockResponses'] = {}  # 测试时注入

exec(MOCK_API_FETCH, ns)
exec(fn_text, ns)
print(f'exec apiFetchWithRetry: {fn_text[:80]}...')


# 3. 准备 AbortController 模拟
class FakeAbortController:
    def __init__(self):
        self.signal = {'aborted': False}
        self._timeout = None
    def abort(self):
        self.signal['aborted'] = True


# 4. 注入 AbortController (替换 None stub)
# apiFetchWithRetry 用 'typeof AbortController !== undefined', 我们给它传 fake controller
# 实际我们 wrap 原函数让 AbortController 注入


# 5. 测试
import unittest


def call_with_mock(mock_map, url, options=None, max_retries=2, abort_controller_factory=None):
    """执行 apiFetchWithRetry, 用 mock_map 替换 mockResponses"""
    ns['mockResponses'] = mock_map
    # inject AbortController if provided
    if abort_controller_factory:
        ns['AbortController'] = abort_controller_factory
    return ns['apiFetchWithRetry'](url, options or {}, max_retries)


class TestNetworkError(unittest.TestCase):
    """场景 1: 网络错误 → 重试 1 次后仍失败"""

    def test_network_error_retry_then_fail(self):
        call_count = [0]
        def factory():
            call_count[0] += 1
            return FakeAbortController()
        # 模拟所有尝试都抛 Failed to fetch
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('Failed to fetch')
        ns['apiFetch'] = mock_api_fetch

        result = call_with_mock({}, '/api/test', abort_controller_factory=factory)
        self.assertFalse(result['success'])
        self.assertIn('Failed to fetch', result['error'])
        self.assertEqual(result['attempts'], 2, '应该 1+1=2 次调用')
        self.assertEqual(call_count[0], 2)


class TestHttpError(unittest.TestCase):
    """场景 2: HTTP 4xx/5xx → 解析 error body"""

    def test_500_with_json_error(self):
        ns['mockResponses'] = {
            '/api/test': {'type': '500', 'body': {'error': 'something bad'}}
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'something bad')
        self.assertEqual(result['status'], 500)
        self.assertEqual(result['attempts'], 1)

    def test_500_no_json_error_body(self):
        ns['mockResponses'] = {
            '/api/test': {'type': '500', 'body': None}  # body parse 失败, fallback to text
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertIn('server error', result['error'])
        self.assertEqual(result['status'], 500)

    def test_401_returns_no_response(self):
        """401 时 apiFetch 返回 null, apiFetchWithRetry 包装成 no_response"""
        ns['mockResponses'] = {'/api/test': {'type': '401'}}
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'no_response')
        self.assertEqual(result['status'], 0)


class TestJsonParseFailure(unittest.TestCase):
    """场景 3: 200 但 body 不是 JSON → invalid_response"""

    def test_bad_json(self):
        ns['mockResponses'] = {
            '/api/test': {'type': '200_bad_json'}
        }
        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'invalid_response')
        self.assertEqual(result['status'], 200)


class TestTimeout(unittest.TestCase):
    """场景 4: timeout → AbortController 10s 超时 → 重试 1 次"""

    def test_timeout_retry_then_fail(self):
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            # 模拟 AbortError
            e = RuntimeError('aborted')
            e.name = 'AbortError'
            raise e
        ns['apiFetch'] = mock_api_fetch

        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(call_count[0], 2, '超时应该重试 1 次')


class TestSuccess(unittest.TestCase):
    """成功路径: 200 + 有效 JSON → success: true"""

    def test_success(self):
        ns['mockResponses'] = {
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

    def test_403_post_no_retry(self):
        """403 POST 时 apiFetch 抛 '权限不足 (403)'"""
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('权限不足 (403)')
        ns['apiFetch'] = mock_api_fetch

        result = ns['apiFetchWithRetry']('/api/test', {}, 2)
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], '权限不足 (403)')
        self.assertEqual(result['attempts'], 1, '403 业务错不该重试')
        self.assertEqual(call_count[0], 1)


if __name__ == '__main__':
    print('=' * 60)
    print('OpenClaw gateway 错误兜底单测 (refactor/openclaw-gateway-error-handling)')
    print('=' * 60)
    unittest.main(verbosity=2)
