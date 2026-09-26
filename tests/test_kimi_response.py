# -*- coding: utf-8 -*-
"""
Kimi 响应格式防御性加固单测
===========================
覆盖 3 种场景 (全 mock 上游, 不打真实 Kimi):
  A. 正常 OpenAI 兼容 chat/completions 响应
  B. Kimi 字段名变化 (text / content / reasoning_content)
  C. Kimi 返回非 JSON (HTML 错误页 / 截断流 / 网关错误)

复用 proxy_engine_test.py 风格:
- importlib 加载 solobrave-server.py
- FakeHandler mock 整个 HTTP handler
- MockUpstream 提供不同响应

⚠️ Windows 端无 Python, Mac 端请跑:
   cd .worktree-kimi && python tests/kimi_response_test.py
"""
import importlib.util
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# importlib 加载 server.py
spec = importlib.util.spec_from_file_location('solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv_mod)

PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'  [{"PASS" if cond else "FAIL"}] {name}' + (f'  [{detail}]' if detail and not cond else ''))


# ============ 单元测试 1: _extract_kimi_message_content 字段兼容 ============
print('=' * 60)
print('场景 A + B: _extract_kimi_message_content 字段名兼容')
print('=' * 60)

# A1 正常 OpenAI 兼容 content 字段
main, reasoning = srv_mod._extract_kimi_message_content({
    'choices': [{'message': {'role': 'assistant', 'content': 'hello'}}]
})
check('A1 content 字段 (新标准)', main == 'hello' and reasoning == '')

# A2 reasoning_content 思维链单独返回
main, reasoning = srv_mod._extract_kimi_message_content({
    'choices': [{'message': {'role': 'assistant', 'content': 'final answer', 'reasoning_content': 'thinking chain...'}}]
})
check('A2 content + reasoning_content 分离', main == 'final answer' and reasoning == 'thinking chain...')

# B1 旧 text 字段 (回落到 text)
main, reasoning = srv_mod._extract_kimi_message_content({
    'choices': [{'message': {'role': 'assistant', 'text': 'old format'}}]
})
check('B1 回落 text 字段 (旧格式)', main == 'old format' and reasoning == '')

# B2 字段全缺失 (None)
main, reasoning = srv_mod._extract_kimi_message_content({
    'choices': [{'message': {'role': 'assistant'}}]
})
check('B2 字段全缺失 → 返回空串', main == '' and reasoning == '')

# B3 字段名奇怪 (KeyError 兜底)
main, reasoning = srv_mod._extract_kimi_message_content({
    'choices': [{'weird_field': 'data'}]
})
check('B3 异常结构 → 兜底空串', main == '' and reasoning == '')

# B4 完全损坏 (不是 dict)
main, reasoning = srv_mod._extract_kimi_message_content('not a dict')
check('B4 完全损坏 (str) → 兜底空串', main == '' and reasoning == '')

# B5 choices 是空数组
main, reasoning = srv_mod._extract_kimi_message_content({'choices': []})
check('B5 choices 空 → 兜底空串', main == '' and reasoning == '')


# ============ 单元测试 2: _extract_text_from_garbage fallback 文本提取 ============
print('=' * 60)
print('场景 C: _extract_text_from_garbage 纯文本 fallback')
print('=' * 60)

# C1 空 raw
result = srv_mod._extract_text_from_garbage(b'')
check('C1 空 raw → 默认提示', result == 'Kimi 服务返回空响应, 请稍后重试')

result = srv_mod._extract_text_from_garbage(None)
check('C1b None → 默认提示', result == 'Kimi 服务返回空响应, 请稍后重试')

# C2 错误关键词匹配
result = srv_mod._extract_text_from_garbage(b'error: 502 Bad Gateway')
check('C2 error 关键词 → 包装提示', 'Kimi 服务提示' in result and '502 Bad Gateway' in result)

result = srv_mod._extract_text_from_garbage(b'Exception: rate limit exceeded')
check('C2b Exception 关键词 → 包装提示', 'Kimi 服务提示' in result and 'rate limit' in result)

# C3 HTTP 状态码
result = srv_mod._extract_text_from_garbage(b'<html>503 Service Unavailable</html>')
check('C3 5xx 状态码匹配', 'Kimi 服务提示' in result and '503' in result)

# C4 HTML 标签 + 截断
result = srv_mod._extract_text_from_garbage(b'<html><body>Some random text here that is not an error but garbage data</body></html>')
check('C4 HTML 标签去除 + 取前 300 字', 'Kimi 返回非标准格式' in result)
check('C4b 不含 <html> 标签', '<html>' not in result)

# C5 极长 HTML 截断
long_html = '<html>' + ('<p>content</p>' * 200) + '</html>'
result = srv_mod._extract_text_from_garbage(long_html.encode())
check('C5 长内容截断到 300 字符', len(result) < 350)


# ============ 端到端 mock: _handle_proxy_kimi 非流式 + Kimi 返回非 JSON ============
print('=' * 60)
print('场景 C 端到端: Kimi upstream 返回非 JSON → 降级包装')
print('=' * 60)


class MockUpstreamKimi(BaseHTTPRequestHandler):
    """Kimi mock upstream - 返回不同格式响应"""
    received = []

    def log_message(self, format, *args):
        pass  # 静默

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(n)
        MockUpstreamKimi.received.append({'path': self.path, 'body_len': len(body)})
        path = self.path
        if '/ok_normal' in path:
            # 正常 OpenAI 兼容 Kimi 响应
            self._json(200, {
                'id': 'cmpl-1',
                'object': 'chat.completion',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'Kimi 正常回复'}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}
            })
        elif '/ok_text_field' in path:
            # 旧字段名 text (回落)
            self._json(200, {
                'id': 'cmpl-2',
                'choices': [{'message': {'role': 'assistant', 'text': 'old field format'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}
            })
        elif '/ok_reasoning' in path:
            # reasoning_content
            self._json(200, {
                'id': 'cmpl-3',
                'choices': [{'message': {'role': 'assistant', 'content': 'final', 'reasoning_content': 'thinking step by step...'}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 5}
            })
        elif '/bad_html' in path:
            # HTML 错误页 (非 JSON)
            self.send_response(502)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(b'<html><body>502 Bad Gateway</body></html>')
        elif '/empty' in path:
            # 空响应
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'')
        elif '/garbage_text' in path:
            # 200 + 纯文本 (非 JSON)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Some unexpected plain text response from Kimi')
        else:
            self._json(200, {'id': 'cmpl-default', 'choices': [{'message': {'content': 'default'}}], 'usage': {}})

    def _json(self, code, body):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())


class FakeConn:
    def cursor(self): return self
    def execute(self, *a, **k): return self
    def fetchone(self): return (100, 1)
    def fetchall(self): return []
    def commit(self): pass
    def close(self): pass


class FakeHandler:
    """Mock _handle_proxy_kimi 需要的 handler"""
    def __init__(self, body, headers=None):
        self.path = '/api/proxy/kimi/v1/chat/completions'
        self.command = 'POST'
        self.headers = headers or {}
        self.client_address = ('127.0.0.1', 12345)
        self._body = body
        self.status = None
        self.headers_out = {}
        self.wfile = io.BytesIO()

    def _normalize_path(self, p):
        return p.split('?')[0]

    def _read_body(self):
        return self._body

    def send_response(self, s):
        self.status = s

    def send_header(self, k, v):
        self.headers_out[k] = v

    def end_headers(self):
        pass

    def _send_json_error(self, code, msg):
        self.status = code
        self.wfile.write(json.dumps({'error': msg}).encode())

    def out_body(self):
        return self.wfile.getvalue()


# patch 业务依赖
_patches = {
    '_get_agent_api_key': lambda aid: 'test-kimi-key',
    '_check_credit_balance': lambda aid: (100, True),
    '_db_conn': lambda: FakeConn(),
    '_talent_presearch': lambda conn, q: '',
    '_retrieve_entity_report_context': lambda *a, **k: '',
    '_load_chat': lambda aid: [],
    '_record_credit_usage': lambda *a, **k: None,
    'KIMI_KEY_POOL': type('MockPool', (), {
        'size': 1, 'get_key': lambda self: 'test-kimi-key', 'mark_failed': lambda self, k: None
    })(),
}
_origs = {k: getattr(srv_mod, k) for k in _patches}
for k, v in _patches.items():
    setattr(srv_mod, k, v)
# memory pipeline stub
_origs['memory_pipeline'] = srv_mod.memory_pipeline
srv_mod.memory_pipeline = type('MockMP', (), {
    'recall_with_budget': lambda *a, **k: {},
    'save_conversation': lambda *a, **k: None,
    'check_and_run_pipeline': lambda *a, **k: None
})()

# 启动 mock upstream
server = HTTPServer(('127.0.0.1', 0), MockUpstreamKimi)
port = server.server_address[1]
t = threading.Thread(target=server.serve_forever, daemon=True)
t.start()
base = f'http://127.0.0.1:{port}'

try:
    body = {
        'model': 'kimi-for-coding',
        'messages': [{'role': 'user', 'content': 'test'}]
    }
    headers = {'x-api-key': 'proxy_test_agent_001'}

    # 端到端: 正常响应
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_normal'
    h = FakeHandler(dict(body), dict(headers))
    srv_mod._handle_proxy_kimi(h)
    out = h.out_body()
    try:
        d = json.loads(out.decode())
        if 'choices' in d and d['choices']:
            content = d['choices'][0].get('message', {}).get('content', '')
            check('E2E 正常响应透传 (Kimi 给 "Kimi 正常回复")', 'Kimi 正常回复' in content or content == 'Kimi 正常回复')
        else:
            check('E2E 正常响应 (choices 存在)', False, f'no choices, body={out[:200]}')
    except Exception as e:
        check('E2E 正常响应解析', False, str(e))

    # 端到端: 字段名回落 (text 字段)
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_text_field'
    h = FakeHandler(dict(body), dict(headers))
    srv_mod._handle_proxy_kimi(h)
    out = h.out_body()
    try:
        d = json.loads(out.decode())
        if 'choices' in d and d['choices']:
            content = d['choices'][0].get('message', {}).get('content', '')
            check('E2E text 字段回落 → content', content == 'old field format')
        else:
            check('E2E text 字段回落', False, f'no choices')
    except Exception as e:
        check('E2E text 字段回落 解析', False, str(e))

    # 端到端: reasoning_content 提取 (server 不解析 content, 透传, 但 usage 应记录)
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_reasoning'
    h = FakeHandler(dict(body), dict(headers))
    srv_mod._handle_proxy_kimi(h)
    out = h.out_body()
    try:
        d = json.loads(out.decode())
        if 'choices' in d:
            content = d['choices'][0].get('message', {}).get('content', '')
            check('E2E reasoning_content → content 透传', content == 'final')
            check('E2E usage 仍记录 (prompt_tokens=10)', d.get('usage', {}).get('prompt_tokens') == 10)
        else:
            check('E2E reasoning_content 透传', False, f'no choices')
    except Exception as e:
        check('E2E reasoning_content 解析', False, str(e))

    # 端到端: Kimi 返回 HTML 错误页 (502) → 触发 urllib.error.HTTPError, 走 401/429 重试 (不是)
    # 实际 502 不在重试逻辑里 → 直接 502 透传, 不走我们的加固。改测 200+非 JSON
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/garbage_text'
    h = FakeHandler(dict(body), dict(headers))
    srv_mod._handle_proxy_kimi(h)
    out = h.out_body()
    try:
        d = json.loads(out.decode())
        # 应该是降级包装的 OpenAI 格式
        if 'choices' in d and d['choices']:
            content = d['choices'][0].get('message', {}).get('content', '')
            check('E2E Kimi 返回纯文本 → 降级包装 (非 500)', h.status == 200)
            check('E2E 降级 content 含降级标记', 'Kimi 返回非标准格式' in content or 'Kimi 服务提示' in content, f'content={content[:100]}')
        else:
            check('E2E Kimi 纯文本降级', False, f'no choices in response: {out[:200]}')
    except Exception as e:
        check('E2E Kimi 纯文本降级 解析', False, str(e))

    # 端到端: Kimi 返回空响应 (200 + 空 body)
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/empty'
    h = FakeHandler(dict(body), dict(headers))
    srv_mod._handle_proxy_kimi(h)
    out = h.out_body()
    try:
        d = json.loads(out.decode())
        if 'choices' in d and d['choices']:
            content = d['choices'][0].get('message', {}).get('content', '')
            check('E2E 空响应 → 降级包装 (200)', h.status == 200)
            check('E2E 空响应降级 content 含"空响应"', '空响应' in content, f'content={content[:100]}')
        else:
            check('E2E 空响应降级', False, f'no choices')
    except Exception as e:
        check('E2E 空响应降级 解析', False, str(e))

finally:
    server.shutdown()
    for k, v in _origs.items():
        if k != 'memory_pipeline':
            setattr(srv_mod, k, v)


# ============ 总结 ============
# ★ fix/mini-test-code-repair-20260925: 收进 if __name__ == '__main__': 守卫,
#   使 import / pytest collection 不再触发 sys.exit(1) (commit 6 P0-4 同模式).
#   不转 check (def check → def test) — 那是下一轮独立任务.
if __name__ == '__main__':
    print('=' * 60)
    print(f'测试结果: {len(PASS)} PASS, {len(FAIL)} FAIL')
    if FAIL:
        print('失败项:')
        for f in FAIL:
            print(f'  - {f}')
        sys.exit(1)
    print('全部通过 ✓')
