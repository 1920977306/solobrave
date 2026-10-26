# -*- coding: utf-8 -*-
"""MiniMax 协议一致性 + 「第一环死 ≠ 全员死」契约（2026-10-26）。

★ 本轮挖出的三条真 bug —— 全是**「声明端 ≠ 使用端」**形态：

  ① `_resolve_ai_base_url` 里 custom_endpoint 被内置 mapping **覆盖**
     ⇒ settings.json 里配的 `baseUrl` 静默失效
     ⇒ minimax 实际打老开放平台端点 `api.minimax.chat/v1/text` → **HTTP 404**
     （实测 `PatternInduce-Cron` 因此一直「LLM 调用失败」）

  ② `_call_minimax_messages` / `_try_minimax_proxy_fallback` **写死 OpenAI 协议**
     （`/chatcompletion_v2` + `Authorization: Bearer` + 硬编码 `choices` 判定）
     ⇒ minimax 档换成**套餐 Anthropic 端点**后，协议 / 端点 / 模型三者全错位

  ③ kimi key 池耗尽时在**链之前**就 503
     ⇒ 「第一环死」被写成「全员死」：kimi 月度额度一耗尽，
       4 把 key 全拉黑 ⇒ **每一个**后续请求都被提前 503 拦掉，
       兜底档（minimax 套餐 M3，实测 200 可用）根本没机会接管
     ⇒ 实测症状：木木 15:12 那条消息直接空回复

★ 本文件锁住这三条 —— 每条都有**能失败**的行为测试，不是恒真断言。
"""
import io
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _load_server():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules['solobrave_server'] = mod
    return mod


# ─────────────────── ① 声明端（settings）必须真的被使用端认 ───────────────────

def test_custom_endpoint_beats_builtin_mapping():
    """★ 配置里的 customEndpoint **优先于**内置默认映射。

    原来只有 provider=='custom' 时才认 custom_endpoint，
    具名 provider 一律用内置 mapping ⇒ settings 配的 baseUrl 是死的。
    """
    srv = _load_server()
    assert srv._resolve_ai_base_url('minimax', 'https://configured.example/v1') == \
        'https://configured.example/v1'
    assert srv._resolve_ai_base_url('zhipu', 'https://z.example/v4') == 'https://z.example/v4'
    # 没配才回落到内置默认
    assert srv._resolve_ai_base_url('minimax') == 'https://api.minimax.cn/anthropic/v1'


def test_minimax_default_endpoint_and_model():
    """minimax 的默认端点/模型 = **套餐 Anthropic 端点 + 真 M3**。"""
    srv = _load_server()
    assert srv._resolve_ai_base_url('minimax') == 'https://api.minimax.cn/anthropic/v1'
    assert srv._resolve_ai_model('minimax') == 'MiniMax-M3'


# ─────────────────── ② minimax 调用必须说 Anthropic 协议 ───────────────────

class _FakeResp:
    """最小 urllib 响应替身。"""

    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode('utf-8')
        self.status = status

    def read(self, *_a):
        return self._body

    def close(self):
        pass


def _capture_urlopen(monkeypatch, srv, payload, status=200):
    """patch urlopen，记录请求并回一个固定响应。"""
    seen = {}

    def fake_urlopen(req, timeout=None, context=None, **_kw):
        seen['url'] = getattr(req, 'full_url', req)
        seen['headers'] = {k.lower(): v for k, v in dict(req.headers).items()}
        seen['body'] = json.loads(req.data.decode('utf-8')) if req.data else {}
        return _FakeResp(payload, status)

    monkeypatch.setattr(srv.urllib.request, 'urlopen', fake_urlopen)
    return seen


def test_call_minimax_messages_uses_anthropic_protocol(monkeypatch):
    """★ `_call_minimax_messages` 必须发 **Anthropic Messages** 请求。

    URL = {base}/messages、鉴权 x-api-key、system 提到顶层、messages 里无 system role。
    """
    srv = _load_server()
    seen = _capture_urlopen(monkeypatch, srv, {
        'id': 'msg_1', 'type': 'message', 'role': 'assistant',
        'content': [{'type': 'text', 'text': 'M3 回复'}],
        'usage': {'input_tokens': 3, 'output_tokens': 2},
    })

    out = srv._call_minimax_messages(
        'https://api.minimax.cn/anthropic/v1', 'MiniMax-M3', 'sk-cp-test',
        [{'role': 'system', 'content': '你是木木'},
         {'role': 'user', 'content': '你好'}],
        timeout=10, max_tokens=128)

    assert out == 'M3 回复', out
    assert seen['url'] == 'https://api.minimax.cn/anthropic/v1/messages', seen['url']
    assert seen['headers'].get('x-api-key') == 'sk-cp-test', seen['headers']
    assert seen['headers'].get('anthropic-version'), seen['headers']
    assert seen['body']['model'] == 'MiniMax-M3'
    assert seen['body']['system'] == '你是木木', seen['body']
    assert all(m['role'] != 'system' for m in seen['body']['messages']), seen['body']


def test_try_minimax_fallback_body_is_anthropic_shape(monkeypatch, tmp_path):
    """★ `_try_minimax_proxy_fallback` 的请求体是 Anthropic 形状。

    （原来固定拼 OpenAI 形状：system 混在 messages 里、role=system
      ⇒ Anthropic 上游必 400：messages[*].role 只能是 user/assistant）
    """
    srv = _load_server()
    settings = tmp_path / 'settings.json'
    settings.write_text(json.dumps({'llm': {'providers': [
        {'name': 'minimax', 'apiKey': 'sk-cp-test',
         'baseUrl': 'https://api.minimax.cn/anthropic/v1',
         'model': 'MiniMax-M3', 'priority': 1}]}}), encoding='utf-8')
    monkeypatch.setattr(srv, 'SETTINGS_FILE', str(settings))
    seen = _capture_urlopen(monkeypatch, srv, {
        'id': 'msg_2', 'type': 'message', 'role': 'assistant',
        'content': [{'type': 'text', 'text': '兜底回复'}],
        'usage': {'input_tokens': 1, 'output_tokens': 1},
    })

    raw = srv._try_minimax_proxy_fallback(
        {'model': 'kimi-for-coding', 'max_tokens': 256, 'system': '你是 Helen',
         'messages': [{'role': 'user', 'content': '你好'}]}, log_prefix='Test')

    assert raw is not None, 'Anthropic 形状的响应被误判成「格式异常」'
    assert seen['url'] == 'https://api.minimax.cn/anthropic/v1/messages', seen['url']
    assert seen['headers'].get('x-api-key') == 'sk-cp-test', seen['headers']
    assert seen['body']['system'] == '你是 Helen', seen['body']
    assert all(m['role'] != 'system' for m in seen['body']['messages']), seen['body']


# ─────────────────── ③ 第一环死 ≠ 全员死 ───────────────────

class _FakeHandler:
    """最小 HTTP handler 替身（只实现代理链用到的几个方法）。"""
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


class _DummyConn:
    def execute(self, *_a, **_k):
        class _Row:
            rowcount = 0
        return _Row()

    def commit(self):
        pass

    def close(self):
        pass


def _patch_business_deps(monkeypatch, srv, fallback_providers):
    """把业务前置（DB / 记忆 / 达人 / 积分）打成 no-op，只留转发链路。"""
    monkeypatch.setattr(srv, '_get_agent_api_key', lambda aid: None)
    monkeypatch.setattr(srv, '_check_credit_balance', lambda aid: (100, True))
    monkeypatch.setattr(srv, '_db_conn', lambda: _DummyConn())
    monkeypatch.setattr(srv, '_talent_presearch', lambda conn, q: '')
    monkeypatch.setattr(srv, '_retrieve_entity_report_context', lambda *a, **k: '')
    monkeypatch.setattr(srv, '_load_chat', lambda aid: [])
    monkeypatch.setattr(srv, '_chain_fallback_providers',
                        lambda exclude=('kimi', 'kimicode'): fallback_providers)
    monkeypatch.setattr(srv, '_record_credit_usage',
                        lambda conn, agent_id, it, ot, crt, **kw: None)
    monkeypatch.setattr(srv.memory_pipeline, 'recall_with_budget', lambda *a, **k: {})
    monkeypatch.setattr(srv.memory_pipeline, 'save_conversation', lambda *a, **k: None)
    monkeypatch.setattr(srv.memory_pipeline, 'check_and_run_pipeline', lambda *a, **k: None)


def test_empty_kimi_key_pool_still_falls_back_to_next_provider(monkeypatch):
    """★★ kimi key 池耗尽 ⇒ **不能**提前 503，必须让兜底 provider 接管。

    这是木木 15:12 空回复的真实成因（kimi 月度额度耗尽 ⇒ 4 把 key 全拉黑
    ⇒ 每个请求都在链之前被 503 拦掉 ⇒ 兜底档没机会上场）。
    """
    srv = _load_server()
    monkeypatch.setattr(srv, 'PROXY_ENGINE', 'chain')
    _patch_business_deps(monkeypatch, srv, [{
        'name': 'minimax', 'api_key': 'mm-key-0001',
        'base_url': 'https://api.minimax.cn/anthropic/v1', 'model': 'MiniMax-M3'}])
    # key 池全拉黑 ⇒ get_key() 返回 None
    monkeypatch.setattr(srv.KIMI_KEY_POOL, 'get_key', lambda: None)

    seen = _capture_urlopen(monkeypatch, srv, {
        'id': 'msg_fb', 'type': 'message', 'role': 'assistant',
        'content': [{'type': 'text', 'text': '兜底接管'}],
        'usage': {'input_tokens': 11, 'output_tokens': 7}})

    h = _FakeHandler({'model': 'k3', 'max_tokens': 64,
                      'messages': [{'role': 'user', 'content': 'hi'}]})
    srv._handle_proxy_kimi(h)

    assert h.status == 200, f'key 池空就提前返回了 {h.status}（应为 200，由兜底档接管）'
    body = json.loads(h.out_body().decode())
    assert body['content'][0]['text'] == '兜底接管', body
    assert seen['url'] == 'https://api.minimax.cn/anthropic/v1/messages', seen['url']


def test_legacy_engine_still_returns_503_when_pool_empty(monkeypatch):
    """legacy 引擎没有链可退 ⇒ key 池空时**仍然** 503（行为不变）。

    这条保证「放宽提前 503」没有把 legacy 的保护拆掉。
    """
    srv = _load_server()
    monkeypatch.setattr(srv, 'PROXY_ENGINE', 'legacy')
    _patch_business_deps(monkeypatch, srv, [])
    monkeypatch.setattr(srv.KIMI_KEY_POOL, 'get_key', lambda: None)

    h = _FakeHandler({'model': 'k3', 'max_tokens': 64,
                      'messages': [{'role': 'user', 'content': 'hi'}]})
    srv._handle_proxy_kimi(h)

    assert h.status == 503, h.status
    payload = json.loads(h.out_body().decode())
    assert payload['error']['type'] == 'api_key_pool_exhausted', payload
