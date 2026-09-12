# -*- coding: utf-8 -*-
"""
ProviderChain / provider_adapters mock 上游测试
================================================

覆盖四类场景（全部走本地 mock，不打真实上游）：
  A. 多 Provider：kimi 直连 200 / openai 格式 / anthropic 格式
  B. 流式：SSE 透传 + usage 解析、流式首包非 SSE 降级、兜底结果 SSE 包装
  C. 降级链：403→minimax、401/429 key 轮换、冷却+半开恢复、X-Proxy-Provider 头
  D. 错误规范化：400/403/500/全失败统一结构，不原样透传上游 body
  E. 积分扣费：各分支恰好扣一次（key轮换/降级/SSE包装/流式透传）

运行：python tests/proxy_engine_test.py   （从仓库根目录）
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

import provider_adapters as pa  # noqa: E402

spec = importlib.util.spec_from_file_location('solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv_mod)

PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'  [{"PASS" if cond else "FAIL"}] {name}' + (f'  [{detail}]' if detail and not cond else ''))


# ─── Mock 上游 ───────────────────────────────────────────

def anthropic_sse_events(text, model='kimi-for-coding', in_tok=20, out_tok=9):
    return pa.anthropic_sse_from_text(text, model, input_tokens=in_tok, output_tokens=out_tok)


class MockUpstream(BaseHTTPRequestHandler):
    """行为由 path 前缀决定：
    /ok_anthropic   → 200 Anthropic 非流式
    /ok_openai      → 200 OpenAI 非流式
    /sse_anthropic  → 200 Anthropic SSE 流式
    /sse_garbage    → 200 但返回 HTML 垃圾（流式首包校验用）
    /403 /401 /429 /500 → 对应状态码错误
    /echo           → 200 回显请求体（验证转发字段用）
    """
    received = []  # [(path, headers_subset, body_dict)]

    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode('utf-8'))
        except Exception:
            body = {}
        MockUpstream.received.append({
            'path': self.path,
            'x-api-key': self.headers.get('x-api-key', ''),
            'authorization': self.headers.get('Authorization', ''),
            'anthropic-version': self.headers.get('anthropic-version', ''),
            'body': body,
        })
        path = self.path
        if path.startswith('/echo'):
            self._json(200, {'echo': body})
        elif path.startswith('/ok_anthropic'):
            self._json(200, {
                'id': 'msg_mock1', 'type': 'message', 'role': 'assistant',
                'content': [{'type': 'text', 'text': 'kimi 回复内容'}],
                'model': 'kimi-for-coding', 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 20, 'output_tokens': 9}})
        elif path.startswith('/ok_openai'):
            self._json(200, {
                'id': 'mm1', 'object': 'chat.completion',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'minimax 兜底回复'},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}})
        elif path.startswith('/sse_anthropic'):
            payload = anthropic_sse_events('流式回复内容', in_tok=20, out_tok=9)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            # 分 3 片写，模拟真实流式
            third = max(1, len(payload) // 3)
            for i in range(0, len(payload), third):
                self.wfile.write(payload[i:i + third])
                self.wfile.flush()
                time.sleep(0.01)
        elif path.startswith('/sse_garbage'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'<html><body>Bad Gateway</body></html>')
        elif path.startswith('/sse_openai'):
            payload = pa.openai_sse_from_text('openai 流式回复', 'gpt-4o-mini',
                                              input_tokens=15, output_tokens=6)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            third = max(1, len(payload) // 3)
            for i in range(0, len(payload), third):
                self.wfile.write(payload[i:i + third])
                self.wfile.flush()
                time.sleep(0.01)
        elif path.startswith('/anthropic_ok'):
            self._json(200, {
                'id': 'msg_anthropic1', 'type': 'message', 'role': 'assistant',
                'content': [{'type': 'text', 'text': 'anthropic 官方回复'}],
                'model': 'claude-3-5-sonnet-20241022', 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 8, 'output_tokens': 4}})
        else:
            seg = path.strip('/').split('/')[0]
            code = int(seg) if seg.isdigit() else 500
            self._json(code, {'error': {'message': f'mock upstream {code}', 'type': 'upstream_raw'}})

    def _json(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class Client:
    """捕获 write_head / write_chunk 回调"""

    def __init__(self):
        self.status = None
        self.content_type = None
        self.headers = {}
        self.chunks = []

    def write_head(self, status, ct, extra):
        self.status = status
        self.content_type = ct
        self.headers.update(extra or {})

    def write_chunk(self, b):
        self.chunks.append(b)

    @property
    def body(self):
        return b''.join(self.chunks)

    def json(self):
        return json.loads(self.body.decode('utf-8'))


def run_chain(body, *, request_format='anthropic', is_streaming=False, primary_key='sk-test-primary-0001',
              key_pool=None, fallback_providers=None, port=None, target_path='/v1/messages', repair_400=None):
    c = Client()
    r = srv_mod._proxy_chain_forward(
        body, request_format=request_format, is_streaming=is_streaming,
        write_head=c.write_head, write_chunk=c.write_chunk,
        target_path=target_path, primary_key=primary_key, key_pool=key_pool,
        fallback_providers=fallback_providers, timeout=10, repair_400=repair_400)
    return r, c


def main():
    mock = HTTPServer(('127.0.0.1', 0), MockUpstream)
    port = mock.server_address[1]
    threading.Thread(target=mock.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'

    FB = [{'name': 'minimax', 'api_key': 'mm-key-0001', 'base_url': f'{base}/ok_openai', 'model': 'MiniMax-Text-01'}]
    KIMI_BODY = {'model': 'kimi-for-coding', 'messages': [{'role': 'user', 'content': 'hi'}]}

    # ═══ A. 多 Provider ═══
    print('A. 多 Provider')
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_anthropic'
    MockUpstream.received.clear()
    r, c = run_chain(KIMI_BODY, fallback_providers=[])
    d = c.json()
    check('A1 kimi 直连 200 透传 anthropic 格式', r['ok'] and r['provider'] == 'kimi'
          and d['content'][0]['text'] == 'kimi 回复内容' and c.status == 200)
    check('A2 命中头 X-Proxy-Provider=kimi 且无降级头',
          c.headers.get('X-Proxy-Provider') == 'kimi' and 'X-Proxy-Fallback-From' not in c.headers)
    sent = MockUpstream.received[-1]
    check('A3 kimi 鉴权头 x-api-key + anthropic-version',
          sent['x-api-key'] == 'sk-test-primary-0001' and sent['anthropic-version'] == '2023-06-01'
          and not sent['authorization'])
    check('A4 max_tokens 默认补 4096（对外链路）', sent['body'].get('max_tokens') == 4096)
    check('A5 usage 解析', r['usage'] == {'input_tokens': 20, 'output_tokens': 9})

    # openai 格式请求 → 转 anthropic 上游 → 响应转回 openai
    MockUpstream.received.clear()
    oai_body = {'model': 'kimi-for-coding', 'messages': [
        {'role': 'system', 'content': 'sys'}, {'role': 'user', 'content': 'hi'}]}
    r, c = run_chain(oai_body, request_format='openai', fallback_providers=[])
    d = c.json()
    sent = MockUpstream.received[-1]
    check('A6 openai 请求转 anthropic 上游（system 顶层抽取）',
          sent['body'].get('system') == 'sys'
          and all(m['role'] != 'system' for m in sent['body']['messages']))
    check('A7 anthropic 响应转回 openai 格式',
          d.get('object') == 'chat.completion' and d['choices'][0]['message']['content'] == 'kimi 回复内容')

    # ═══ B. 流式 ═══
    print('B. 流式')
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/sse_anthropic'
    stream_body = dict(KIMI_BODY, stream=True)
    r, c = run_chain(stream_body, is_streaming=True, fallback_providers=[])
    check('B1 流式透传 200 + text/event-stream', r['ok'] and c.status == 200
          and c.content_type == 'text/event-stream')
    check('B2 流式内容完整', '流式回复内容'.encode() in c.body and b'message_stop' in c.body)
    check('B3 流式 usage 从 SSE 事件解析', r['usage'] == {'input_tokens': 20, 'output_tokens': 9})

    # 流式首包垃圾 → 降级 minimax（非流式结果包装成 SSE）
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/sse_garbage'
    r, c = run_chain(stream_body, is_streaming=True, fallback_providers=FB)
    check('B4 流式首包非 SSE → 降级兜底', r['ok'] and r['provider'] == 'minimax'
          and c.headers.get('X-Proxy-Fallback-From') == 'kimi')
    check('B5 兜底结果包装为合法 anthropic SSE',
          c.content_type == 'text/event-stream' and b'message_start' in c.body
          and 'minimax 兜底回复'.encode() in c.body and b'message_stop' in c.body)
    check('B6 SSE 包装携带兜底 usage', r['usage'] == {'input_tokens': 11, 'output_tokens': 7})

    # ═══ C. 降级链 ═══
    print('C. 降级链')
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
    r, c = run_chain(KIMI_BODY, fallback_providers=FB)
    d = c.json()
    check('C1 kimi 403 → minimax 兜底 200', r['ok'] and r['provider'] == 'minimax'
          and c.status == 200 and d['content'][0]['text'] == 'minimax 兜底回复')
    check('C2 降级头 X-Proxy-Fallback-From=kimi', c.headers.get('X-Proxy-Fallback-From') == 'kimi')

    # 401/429 key 轮换：池里第一个 key 429，第二个 200
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/rotate'
    state = {'calls': 0}

    class RotateMock(MockUpstream):
        def do_POST(self):
            n = int(self.headers.get('Content-Length', 0))
            self.rfile.read(n)
            state['calls'] += 1
            key = self.headers.get('x-api-key', '')
            if 'bad1' in key:
                self._json(429, {'error': 'rate limited'})
            else:
                self._json(200, {
                    'id': 'm', 'type': 'message', 'role': 'assistant',
                    'content': [{'type': 'text', 'text': '轮换后成功'}],
                    'model': 'kimi-for-coding', 'stop_reason': 'end_turn',
                    'usage': {'input_tokens': 5, 'output_tokens': 3}})

    mock2 = HTTPServer(('127.0.0.1', 0), RotateMock)
    port2 = mock2.server_address[1]
    threading.Thread(target=mock2.serve_forever, daemon=True).start()
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'http://127.0.0.1:{port2}/rotate'
    pool = srv_mod.KimiKeyPool(['sk-test-bad1-xxxx', 'sk-test-good2-yyyy'])
    r, c = run_chain(KIMI_BODY, primary_key=None, key_pool=pool, fallback_providers=[])
    check('C3 429 key 轮换后成功', r['ok'] and c.json()['content'][0]['text'] == '轮换后成功'
          and state['calls'] == 2, f'r={r} calls={state["calls"]}')
    check('C4 失败 key 已被池拉黑', pool.get_key() == 'sk-test-good2-yyyy')

    # 冷却 + 半开恢复（tracker 直接测）
    tracker = srv_mod._PROVIDER_COOLDOWN
    name = 'cooldown-test'
    for _ in range(3):
        tracker.mark_failed(name)
    check('C5 连续失败 3 次进入冷却', not tracker.allow_request(name))
    tracker._cooldown_until[name] = time.time() - 1  # 模拟冷却到期
    check('C6 冷却到期进入半开（允许一个探测）', tracker.allow_request(name))
    check('C7 半开态只放一个探测', not tracker.allow_request(name))
    tracker.mark_failed(name)  # 探测失败 → 重新冷却
    check('C8 探测失败重新冷却', not tracker.allow_request(name))
    tracker._cooldown_until[name] = time.time() - 1
    tracker.allow_request(name)
    tracker.mark_ok(name)  # 探测成功 → 恢复
    check('C9 探测成功恢复（放行所有请求）', tracker.allow_request(name) and tracker.allow_request(name))

    # ═══ D. 错误规范化 ═══
    print('D. 错误规范化')
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/400'
    r, c = run_chain(KIMI_BODY, fallback_providers=FB)
    d = c.json()
    check('D1 400 客户端错误不降级、直接返回', not r['ok'] and c.status == 400
          and not any(a.get('provider') == 'minimax' and 'status' in a for a in r['attempts']))
    check('D2 400 规范化结构（anthropic 外壳）', d.get('type') == 'error'
          and d['error']['type'] == 'invalid_request' and d['error']['provider'] == 'kimi'
          and d['error']['upstream_status'] == 400 and d['error']['retryable'] is False)
    check('D3 上游原始错误体不透传', 'upstream_raw' not in c.body.decode())

    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/500'
    r, c = run_chain(KIMI_BODY, fallback_providers=FB)
    check('D4 500 触发降级（legacy 不降，chain 覆盖 5xx）', r['ok'] and r['provider'] == 'minimax')

    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
    r, c = run_chain(KIMI_BODY, fallback_providers=[])
    d = c.json()
    check('D5 全失败返回规范化错误', not r['ok'] and c.status == 403
          and d['error']['retryable'] is True and '所有可用 Provider' in d['error']['message'])

    # openai 格式错误外壳
    srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/400'
    r, c = run_chain({'model': 'm', 'messages': [{'role': 'user', 'content': 'x'}]},
                     request_format='openai', fallback_providers=[])
    d = c.json()
    check('D6 openai 格式错误外壳（无顶层 type 字段）', 'type' not in d and d['error']['type'] == 'invalid_request')

    # 网络错误（连接拒绝）→ 降级
    srv_mod.KIMI_PROXY_REAL_BASE_URL = 'http://127.0.0.1:1/dead'
    r, c = run_chain(KIMI_BODY, fallback_providers=FB)
    check('D7 上游网络错误 → 降级兜底', r['ok'] and r['provider'] == 'minimax')

    # ═══ E. 积分扣费（单扣验证）═══
    print('E. 积分扣费分支（通过 _record_credit_usage 计数）')
    credit_calls = []
    orig_record = srv_mod._record_credit_usage
    srv_mod._record_credit_usage = lambda conn, agent_id, it, ot, crt, **kw: credit_calls.append(
        {'agent_id': agent_id, 'input': it, 'output': ot})
    try:
        # E1: 直接成功非流式
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_anthropic'
        credit_calls.clear()
        r, c = run_chain(KIMI_BODY, fallback_providers=[])
        # 模拟 handler 扣费点：result ok 且 usage 非零 → 扣一次
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E1 直连成功扣 1 次 20+9', len(credit_calls) == 1
              and credit_calls[0]['input'] == 20 and credit_calls[0]['output'] == 9)

        # E2: key 轮换后成功
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'http://127.0.0.1:{port2}/rotate'
        credit_calls.clear()
        pool2 = srv_mod.KimiKeyPool(['sk-test-bad1-xxxx', 'sk-test-good2-yyyy'])
        r, c = run_chain(KIMI_BODY, primary_key=None, key_pool=pool2, fallback_providers=[])
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E2 key 轮换后扣 1 次（按最终成功 key 的 usage）', len(credit_calls) == 1
              and credit_calls[0]['input'] == 5 and credit_calls[0]['output'] == 3)

        # E3: 403 降级 minimax 非流式
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
        credit_calls.clear()
        r, c = run_chain(KIMI_BODY, fallback_providers=FB)
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E3 provider 降级扣 1 次（按 minimax usage 11+7）', len(credit_calls) == 1
              and credit_calls[0]['input'] == 11 and credit_calls[0]['output'] == 7)

        # E4: 流式请求降级 → minimax 非流式包装 SSE
        credit_calls.clear()
        r, c = run_chain(dict(KIMI_BODY, stream=True), is_streaming=True, fallback_providers=FB)
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E4 SSE 包装分支扣 1 次（minimax usage）', len(credit_calls) == 1
              and credit_calls[0]['input'] == 11 and credit_calls[0]['output'] == 7)

        # E5: 流式透传
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/sse_anthropic'
        credit_calls.clear()
        r, c = run_chain(dict(KIMI_BODY, stream=True), is_streaming=True, fallback_providers=[])
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E5 流式透传扣 1 次（SSE usage 20+9）', len(credit_calls) == 1
              and credit_calls[0]['input'] == 20 and credit_calls[0]['output'] == 9)

        # E6: 全失败不扣费
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
        credit_calls.clear()
        r, c = run_chain(KIMI_BODY, fallback_providers=[])
        if r['ok'] and (r['usage']['input_tokens'] or r['usage']['output_tokens']):
            srv_mod._record_credit_usage(None, 'emp1', r['usage']['input_tokens'], r['usage']['output_tokens'], 0)
        check('E6 全失败 0 次扣费', len(credit_calls) == 0)
    finally:
        srv_mod._record_credit_usage = orig_record

    # ═══ F. handler 级端到端（_handle_proxy_kimi chain 路径 + legacy 回切）═══
    print('F. handler 级 _handle_proxy_kimi')

    class DummyRow:
        def fetchone(self):
            return None

    class DummyConn:
        def execute(self, *a, **k):
            return DummyRow()

        def commit(self):
            pass

        def close(self):
            pass

    class FakeHandler:
        path = '/api/proxy/kimi/v1/messages'

        def __init__(self, body_dict, key='proxy_emp1'):
            self.headers = {'x-api-key': key}
            self._body_dict = body_dict
            self.status = None
            self.headers_out = {}
            self.wfile = io.BytesIO()

        def _normalize_path(self, p):
            return p.split('?')[0]

        def _read_body(self):
            return self._body_dict

        def send_response(self, s):
            self.status = s

        def send_header(self, k, v):
            self.headers_out[k] = v

        def end_headers(self):
            pass

        def _send_json_error(self, code, msg):
            self.status = code

        def out_body(self):
            return self.wfile.getvalue()

    # patch 业务前置依赖（DB/记忆/达人）为 no-op，只留转发链路；
    # 兜底 provider 列表也注入 mock（否则引擎会读真实 data/settings.json 打真上游）
    _patches = {
        '_get_agent_api_key': lambda aid: None,
        '_check_credit_balance': lambda aid: (100, True),
        '_db_conn': lambda: DummyConn(),
        '_talent_presearch': lambda conn, q: '',
        '_retrieve_entity_report_context': lambda *a, **k: '',
        '_load_chat': lambda aid: [],
        '_chain_fallback_providers': lambda exclude=('kimi', 'kimicode'): FB,
    }
    _origs = {k: getattr(srv_mod, k) for k in _patches}
    for k, v in _patches.items():
        setattr(srv_mod, k, v)
    _mp_orig = (srv_mod.memory_pipeline.recall_with_budget,
                srv_mod.memory_pipeline.save_conversation,
                srv_mod.memory_pipeline.check_and_run_pipeline)
    srv_mod.memory_pipeline.recall_with_budget = lambda *a, **k: {}
    srv_mod.memory_pipeline.save_conversation = lambda *a, **k: None
    srv_mod.memory_pipeline.check_and_run_pipeline = lambda *a, **k: None
    credit_calls = []
    orig_record = srv_mod._record_credit_usage
    srv_mod._record_credit_usage = lambda conn, agent_id, it, ot, crt, **kw: credit_calls.append(
        {'agent_id': agent_id, 'input': it, 'output': ot})
    orig_engine = srv_mod.PROXY_ENGINE
    srv_mod.PROXY_ENGINE = 'chain'
    try:
        # F1 非流式直连
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_anthropic'
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY))
        srv_mod._handle_proxy_kimi(h)
        d = json.loads(h.out_body().decode())
        check('F1 handler 非流式直连 200', h.status == 200 and d['content'][0]['text'] == 'kimi 回复内容')
        check('F2 handler 扣费恰好 1 次', len(credit_calls) == 1
              and credit_calls[0]['input'] == 20 and credit_calls[0]['output'] == 9)
        check('F3 handler 响应头 X-Proxy-Provider=kimi', h.headers_out.get('X-Proxy-Provider') == 'kimi')

        # F4 403 → minimax 降级
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY))
        srv_mod._handle_proxy_kimi(h)
        d = json.loads(h.out_body().decode())
        check('F4 handler 403 降级 minimax', h.status == 200
              and d['content'][0]['text'] == 'minimax 兜底回复'
              and h.headers_out.get('X-Proxy-Fallback-From') == 'kimi')
        check('F5 handler 降级扣费恰好 1 次(minimax usage)', len(credit_calls) == 1
              and credit_calls[0]['input'] == 11 and credit_calls[0]['output'] == 7)

        # F6 流式请求 → 降级 SSE 包装
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY, stream=True))
        srv_mod._handle_proxy_kimi(h)
        check('F6 handler 流式降级 SSE 包装', h.status == 200
              and h.headers_out.get('Content-Type') == 'text/event-stream'
              and b'message_start' in h.out_body() and b'message_stop' in h.out_body())
        check('F7 handler 流式降级扣费恰好 1 次', len(credit_calls) == 1
              and credit_calls[0]['input'] == 11)

        # F8 流式透传
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/sse_anthropic'
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY, stream=True))
        srv_mod._handle_proxy_kimi(h)
        check('F8 handler 流式透传', h.status == 200
              and '流式回复内容'.encode() in h.out_body())
        check('F9 handler 流式扣费恰好 1 次(SSE usage)', len(credit_calls) == 1
              and credit_calls[0]['input'] == 20 and credit_calls[0]['output'] == 9)

        # F10 全失败不扣费 + 规范错误
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/403'
        srv_mod._PROVIDER_COOLDOWN.mark_failed('minimax')  # 模拟兜底也不可用
        srv_mod._PROVIDER_COOLDOWN.mark_failed('minimax')
        srv_mod._PROVIDER_COOLDOWN.mark_failed('minimax')
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY))
        srv_mod._handle_proxy_kimi(h)
        d = json.loads(h.out_body().decode())
        check('F10 handler 全失败 403 规范错误 + 0 扣费', h.status == 403
              and d.get('type') == 'error' and len(credit_calls) == 0)
        srv_mod._PROVIDER_COOLDOWN.mark_ok('minimax')

        # F11 legacy 开关回切：旧路径行为不变（无 X-Proxy-Engine 头）
        srv_mod.PROXY_ENGINE = 'legacy'
        srv_mod.KIMI_PROXY_REAL_BASE_URL = f'{base}/ok_anthropic'
        credit_calls.clear()
        h = FakeHandler(dict(KIMI_BODY))
        srv_mod._handle_proxy_kimi(h)
        d = json.loads(h.out_body().decode())
        check('F11 legacy 路径仍可用且无 chain 头', h.status == 200
              and d['content'][0]['text'] == 'kimi 回复内容'
              and 'X-Proxy-Engine' not in h.headers_out)
        check('F12 legacy 扣费 1 次(旧逻辑)', len(credit_calls) == 1
              and credit_calls[0]['input'] == 20)
    finally:
        srv_mod.PROXY_ENGINE = orig_engine
        srv_mod._record_credit_usage = orig_record
        for k, v in _origs.items():
            setattr(srv_mod, k, v)
        (srv_mod.memory_pipeline.recall_with_budget,
         srv_mod.memory_pipeline.save_conversation,
         srv_mod.memory_pipeline.check_and_run_pipeline) = _mp_orig

    # ═══ G. /api/proxy 通用代理 chain 接入 + anthropic 鉴权修复 ═══
    print('G. /api/proxy chain + anthropic 修复')

    class FakeAuth:
        is_authenticated = True
        user_id = 'u1'
        error = ''
        status = 200

    class ProxyFakeHandler(FakeHandler):
        path = '/api/proxy'

        def __init__(self, body_bytes, headers):
            self.headers = headers
            self.rfile = io.BytesIO(body_bytes)
            self.client_address = ('127.0.0.1', 0)
            self.status = None
            self.headers_out = {}
            self.wfile = io.BytesIO()

        def _add_cors_headers(self):
            pass

        def _send_auth_error(self, err, status):
            self.status = status

    oai_body_bytes = json.dumps(
        {'model': 'gpt-4o-mini', 'messages': [{'role': 'user', 'content': 'hi'}]}).encode()

    def proxy_headers(target, extra=None):
        h = {'X-Target-URL': target, 'X-Agent-Id': 'emp1',
             'X-AI-Provider': 'openai', 'X-AI-API-Key': 'oai-key-0001',
             'Content-Length': str(len(oai_body_bytes))}
        h.update(extra or {})
        return h

    usage_logs = []
    _g_patches = {
        '_authenticate': lambda headers, ip, self=None: FakeAuth(),
        '_log_proxy_token_usage': lambda *a, **k: usage_logs.append(a),
        '_get_agent_api_key': lambda aid: None,
        '_chain_fallback_providers': lambda exclude=('kimi', 'kimicode'): FB,
    }
    _g_origs = {k: getattr(srv_mod, k) for k in _g_patches}
    for k, v in _g_patches.items():
        setattr(srv_mod, k, v)
    orig_engine = srv_mod.PROXY_ENGINE
    srv_mod.PROXY_ENGINE = 'chain'

    # /api/proxy 仅允许 https:// 目标（安全策略）；mock 是 http，
    # 测试期 patch urlopen 把指向 mock 端口的 https 请求重写成 http
    import urllib.request as _ur
    orig_urlopen = _ur.urlopen

    def _rewrite_urlopen(req, timeout=None, context=None, **kw):
        url = getattr(req, 'full_url', req)
        prefix = f'https://127.0.0.1:{port}'
        if isinstance(url, str) and url.startswith(prefix):
            url = 'http://' + url[len('https://'):]
            req = _ur.Request(url, data=getattr(req, 'data', None),
                              headers=dict(req.header_items()), method='POST')
        return orig_urlopen(req, timeout=timeout, context=context)

    _ur.urlopen = _rewrite_urlopen
    try:
        # G1 openai 目标非流式透传（行为不变）
        MockUpstream.received.clear()
        h = ProxyFakeHandler(oai_body_bytes, proxy_headers(f'{base}/ok_openai/chat/completions'.replace('http://','https://')))
        srv_mod._handle_proxy(h)
        d = json.loads(h.out_body().decode())
        sent = MockUpstream.received[-1]
        check('G1 openai 非流式透传 200 + 同格式响应', h.status == 200
              and d['choices'][0]['message']['content'] == 'minimax 兜底回复')
        check('G2 Bearer 鉴权头透传', sent['authorization'] == 'Bearer oai-key-0001')
        check('G3 token usage 仍记录', len(usage_logs) == 1)

        # G4 openai 目标流式透传（新增能力）
        usage_logs.clear()
        sb = json.dumps({'model': 'gpt-4o-mini', 'stream': True,
                         'messages': [{'role': 'user', 'content': 'hi'}]}).encode()
        hh = proxy_headers(f'{base}/sse_openai/chat/completions'.replace('http://','https://'))
        hh['Content-Length'] = str(len(sb))
        h = ProxyFakeHandler(sb, hh)
        srv_mod._handle_proxy(h)
        check('G4 openai 流式透传 text/event-stream', h.status == 200
              and h.headers_out.get('Content-Type') == 'text/event-stream'
              and 'openai 流式回复'.encode() in h.out_body() and b'[DONE]' in h.out_body())
        check('G5 流式 usage 记录', len(usage_logs) == 1)

        # G6 openai 流式 + 上游 500 → minimax 降级包装 openai SSE
        hh = proxy_headers(f'{base}/500/chat/completions'.replace('http://','https://'))
        hh['Content-Length'] = str(len(sb))
        h = ProxyFakeHandler(sb, hh)
        srv_mod._handle_proxy(h)
        check('G6 流式 500 降级包装 openai SSE', h.status == 200
              and h.headers_out.get('X-Proxy-Fallback-From') == 'openai'
              and b'chat.completion.chunk' in h.out_body()
              and 'minimax 兜底回复'.encode() in h.out_body())

        # G7 kimi coding 目标:openai 格式请求转 anthropic 上游,响应转回 openai
        MockUpstream.received.clear()
        srv_mod_orig_resolve = srv_mod._resolve_kimi_coding_target_url
        srv_mod._resolve_kimi_coding_target_url = lambda provider: f'{base}/ok_anthropic/v1/messages'
        try:
            hh = proxy_headers(f'{base}/ok_anthropic/v1/chat/completions'.replace('http://','https://'),
                               {'X-AI-Provider': 'kimi'})
            h = ProxyFakeHandler(oai_body_bytes, hh)
            srv_mod._handle_proxy(h)
            d = json.loads(h.out_body().decode())
            sent = MockUpstream.received[-1]
            check('G7 kimi coding 目标 openai→anthropic→openai',
                  h.status == 200 and d.get('object') == 'chat.completion'
                  and sent['x-api-key'] == 'oai-key-0001'
                  and sent['body'].get('max_tokens') == 4096)
        finally:
            srv_mod._resolve_kimi_coding_target_url = srv_mod_orig_resolve

        # G8 legacy 开关:/api/proxy 旧路径不变
        srv_mod.PROXY_ENGINE = 'legacy'
        MockUpstream.received.clear()
        h = ProxyFakeHandler(oai_body_bytes, proxy_headers(f'{base}/ok_openai/chat/completions'.replace('http://','https://')))
        srv_mod._handle_proxy(h)
        check('G8 legacy /api/proxy 无 chain 头', h.status == 200
              and 'X-Proxy-Engine' not in h.headers_out)
    finally:
        _ur.urlopen = orig_urlopen
        srv_mod.PROXY_ENGINE = orig_engine
        for k, v in _g_origs.items():
            setattr(srv_mod, k, v)

    # G9 anthropic 鉴权修复:_call_chat_completion('anthropic') 走 /messages + x-api-key
    MockUpstream.received.clear()
    orig_base = srv_mod._resolve_ai_base_url
    srv_mod._resolve_ai_base_url = lambda p, c='': f'{base}/anthropic_ok' if p == 'anthropic' else orig_base(p, c)
    try:
        text = srv_mod._call_chat_completion('anthropic', 'anthropic-key-0001', '', '',
                                             [{'role': 'system', 'content': 'sys'},
                                              {'role': 'user', 'content': 'hi'}])
        sent = MockUpstream.received[-1]
        check('G9 anthropic 走 /messages + x-api-key 鉴权',
              text == 'anthropic 官方回复'
              and sent['path'].endswith('/messages')
              and sent['x-api-key'] == 'anthropic-key-0001'
              and sent['anthropic-version'] == '2023-06-01'
              and not sent['authorization'])
        check('G10 anthropic system 提到顶层 + max_tokens 保持内部默认 2000',
              sent['body'].get('system') == 'sys'
              and sent['body'].get('max_tokens') == 2000
              and all(m['role'] != 'system' for m in sent['body']['messages']))
    finally:
        srv_mod._resolve_ai_base_url = orig_base

    mock.shutdown()
    mock2.shutdown()

    print()
    print(f'通过 {len(PASS)}/{len(PASS) + len(FAIL)}')
    if FAIL:
        print('失败用例:', FAIL)
        sys.exit(1)
    print('ALL GREEN')


if __name__ == '__main__':
    main()
