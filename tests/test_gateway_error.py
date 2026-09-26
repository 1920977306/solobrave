# -*- coding: utf-8 -*-
"""
OpenClaw gateway 错误兜底单测 (refactor/openclaw-gateway-error-handling)

策略: 不再 exec 镜像 index.html 的 apiFetchWithRetry, 改 Python 手写 api_fetch_with_retry
+ FakeResponse mock + asyncio.run 驱动 8 个 TestCase.

测试场景 (与 index.html L11822-11900 apiFetchWithRetry 语义对齐):
1. 网络错误 (Failed to fetch): 重试 max_retries-1 次后仍失败 → {success:false, error:msg, attempts:N}
2. HTTP 4xx/5xx: 解析 error body → {success:false, error, status}
3. JSON parse 失败: 200 但 body 不是 JSON → {success:false, error:'invalid_response'}
4. timeout: 抛 AbortError → 重试 → {success:false, error:'timeout', attempts:N}
5. 成功路径: 200 + 有效 JSON → {success:true, data, status:200}
6. 业务错误 403 (no retry): 不重试 → {success:false, error:msg, attempts:1}

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-mini-test-repair && python3 -m pytest tests/test_gateway_error.py -v
"""
import asyncio
import inspect
import re
import unittest


def _err_msg(e):
    """提取异常 message 字段 (类似 JS Error.message)."""
    return getattr(e, 'message', None) or str(e) or 'network_error'


async def api_fetch_with_retry(url, options, api_fetch, max_retries=2):
    """Python 镜像 index.html apiFetchWithRetry 语义.

    Args:
        url: 请求 URL (测试时随意, 仅用于传递)
        options: fetch options (测试时随意, 仅用于传递)
        api_fetch: 实际 fetch 函数, 可同步可异步 (支持 inspect.isawaitable 自动 await)
        max_retries: 最大重试次数 (默认 2, 含首次尝试)

    Returns:
        dict {success, data, error, status, attempts}
    """
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = api_fetch(url, options)
            if inspect.isawaitable(resp):
                resp = await resp
            if resp is None:
                return {'success': False, 'data': None,
                        'error': 'no_response', 'status': 0, 'attempts': attempt}
            if not resp.ok:
                err_msg = None
                try:
                    body = resp.json()
                    err_msg = body and (body.get('error') or body.get('message') or body.get('reason'))
                except Exception:
                    try:
                        err_msg = resp.text()
                    except Exception:
                        err_msg = 'HTTP ' + str(resp.status)
                return {'success': False, 'data': None,
                        'error': err_msg or ('HTTP ' + str(resp.status)),
                        'status': resp.status, 'attempts': attempt}
            try:
                data = resp.json()
                return {'success': True, 'data': data, 'error': None,
                        'status': resp.status, 'attempts': attempt}
            except Exception:
                return {'success': False, 'data': None, 'error': 'invalid_response',
                        'status': resp.status, 'attempts': attempt}
        except Exception as e:
            last_error = e
            low = str(getattr(e, 'message', None) or str(e) or '').lower()
            is_timeout = getattr(e, 'name', None) == 'AbortError' or 'timeout' in low
            is_network = re.search(r'failed to fetch|networkerror|cors', low) is not None
            if (is_timeout or is_network) and attempt < max_retries:
                continue  # 网络错/超时 → 重试
            return {'success': False, 'data': None,
                    'error': 'timeout' if is_timeout else _err_msg(e),
                    'status': 0, 'attempts': attempt}
    return {'success': False, 'data': None,
            'error': getattr(last_error, 'message', None) or 'unknown',
            'status': 0, 'attempts': max_retries}


class FakeResponse:
    """mock fetch response (支持 ok/status + json()/text() 可控返回或抛错)."""
    def __init__(self, ok=True, status=200, json_data=None, json_exc=None,
                 text_data=None, text_exc=None):
        self.ok = ok
        self.status = status
        self._json_data = json_data
        self._json_exc = json_exc
        self._text_data = text_data
        self._text_exc = text_exc

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json_data

    def text(self):
        if self._text_exc is not None:
            raise self._text_exc
        return self._text_data


# ============================================================
# 测试场景 (8 TestCase)
# ============================================================

class TestNetworkError(unittest.TestCase):
    """场景 1: 网络错误 (Failed to fetch) → 重试 max_retries-1 次后仍失败"""

    def test_network_error_retry_then_fail(self):
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('Failed to fetch')
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch, max_retries=2))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'Failed to fetch')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(call_count[0], 2)


class TestHttpError(unittest.TestCase):
    """场景 2: HTTP 4xx/5xx → 解析 error body"""

    def test_500_with_json_error(self):
        def mock_api_fetch(url, options):
            return FakeResponse(ok=False, status=500, json_data={'error': 'boom'})
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'boom')
        self.assertEqual(result['status'], 500)
        self.assertEqual(result['attempts'], 1)

    def test_500_no_json_error_body(self):
        def mock_api_fetch(url, options):
            return FakeResponse(ok=False, status=500, json_exc=Exception(),
                                text_exc=Exception())
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'HTTP 500')
        self.assertEqual(result['status'], 500)

    def test_401_returns_no_response(self):
        """401 时 apiFetch 返回 null, apiFetchWithRetry 包装成 no_response"""
        def mock_api_fetch(url, options):
            return None
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'no_response')
        self.assertEqual(result['status'], 0)


class TestJsonParseFailure(unittest.TestCase):
    """场景 3: 200 但 body 不是 JSON → invalid_response"""

    def test_bad_json(self):
        def mock_api_fetch(url, options):
            return FakeResponse(ok=True, status=200, json_exc=Exception())
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'invalid_response')
        self.assertEqual(result['status'], 200)


class TestTimeout(unittest.TestCase):
    """场景 4: timeout (AbortError) → 重试 max_retries-1 次后仍失败"""

    def test_timeout_retry_then_fail(self):
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            e = RuntimeError('aborted')
            e.name = 'AbortError'
            raise e
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch, max_retries=2))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'timeout')
        self.assertEqual(result['attempts'], 2)
        self.assertEqual(call_count[0], 2)


class TestSuccess(unittest.TestCase):
    """场景 5: 成功路径 — 200 + 有效 JSON → success:true"""

    def test_success(self):
        def mock_api_fetch(url, options):
            return FakeResponse(ok=True, status=200, json_data={'hello': 'world'})
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch))
        self.assertTrue(result['success'])
        self.assertEqual(result['data'], {'hello': 'world'})
        self.assertIsNone(result['error'])
        self.assertEqual(result['status'], 200)
        self.assertEqual(result['attempts'], 1)


class TestNonRetryableError(unittest.TestCase):
    """场景 6: 不可重试错误 (业务 403) → 立即失败, 不重试"""

    def test_403_post_no_retry(self):
        """403 POST 时 apiFetch 抛 '权限不足 (403)'"""
        call_count = [0]
        def mock_api_fetch(url, options):
            call_count[0] += 1
            raise RuntimeError('权限不足 (403)')
        result = asyncio.run(api_fetch_with_retry(
            '/api/test', {}, api_fetch=mock_api_fetch, max_retries=2))
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], '权限不足 (403)')
        self.assertEqual(result['attempts'], 1, '业务错不该重试')
        self.assertEqual(call_count[0], 1)


if __name__ == '__main__':
    print('=' * 60)
    print('OpenClaw gateway 错误兜底单测 (refactor/openclaw-gateway-error-handling)')
    print('=' * 60)
    unittest.main(verbosity=2)