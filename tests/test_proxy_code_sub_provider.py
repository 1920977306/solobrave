# -*- coding: utf-8 -*-
"""代理 ProviderChain 接入 **MiniMax Code 订阅**（= 贾维斯自己）的契约测试。

★ 为什么有这档（2026-10-27，真实事故）：
  2026-10-26 23:2x 实测：kimi 月度额度耗尽（429）+ minimax Token Plan 到顶（429）
  ⇒ **木木等 AI 员工的飞书聊天链两个候选全灭**
     （primary `kimi_proxy_mumu/k3` 429 + fallback `minimax/MiniMax-M3` 429）
  ⇒ 「所有可用 Provider 均失败」⇒ 员工彻底回不出话。
  而 MiniMax **Code 订阅**通道（agent.minimax.cn）当时**实测还活着**，
  只是**没挂在代理链上** ⇒ 补一档即可（零成本、openclaw 侧零改动）。

本文件锁住 7 条**可被静默破坏**的契约：

A. **代理链必须自动带上 code_sub 档**（内置兜底安全网）——
   不依赖 settings.json（运行时文件不入库 ⇒ 新部署也必须有）。

B. **鉴权形态不可猜**：Code 订阅端点只认 `Authorization: Bearer`；
   发 `x-api-key` 实测 401（2026-10-27 实测）。

C. **凭据绝不落配置**：配置里存的是**标记** `__CODE_SUB_TOKEN__`，
   运行时**实时读盘**换真 token（token 30 分钟量级 ⇒ 缓存必然过期）。

D. **没有有效凭据 ⇒ 跳过这一档**（不让用户等一次注定 401 的请求）。

E. **401 ⇒ 重读凭据再试一次**（读盘到发请求之间可能刚好过期），且**只试一次**。

F. **代理链专属档位不污染共用档位表** `llm.providers`
   （那条表被 5 条对话链/视觉/重管道共用 ⇒ 往里加会波及它们）。

G. **显式关闭开关** `enabled: false` ⇒ 不加内置档（可回滚点明确）。
"""
import importlib.util
import json
import os
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import code_sub_credential as codesub  # noqa: E402
import provider_adapters as pa  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    'solobrave_server_codesub', os.path.join(ROOT, 'solobrave-server.py'))
srv_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv_mod)


# ═══════════════════════ mock 上游 ═══════════════════════

class _Mock(BaseHTTPRequestHandler):
    """记录收到的请求头；按 RULES 决定返回什么。"""

    RULES = {}          # path -> [code, code, ...]（按次序消费，最后一个重复）
    received = []

    def do_POST(self):  # noqa: N802
        _Mock.received.append({
            'path': self.path,
            'auth': self.headers.get('Authorization'),
            'x_api_key': self.headers.get('x-api-key'),
            'anthropic_version': self.headers.get('anthropic-version'),
            'body': self.rfile.read(int(self.headers.get('Content-Length') or 0)),
        })
        rules = _Mock.RULES.get(self.path.split('?')[0]) or [200]
        code = rules.pop(0) if len(rules) > 1 else rules[0]
        if code == 200:
            payload = {
                'id': 'msg_cs1', 'type': 'message', 'role': 'assistant',
                'content': [{'type': 'text', 'text': '来自 code_sub 的回复'}],
                'model': 'MiniMax-M3', 'stop_reason': 'end_turn',
                'usage': {'input_tokens': 5, 'output_tokens': 3},
            }
        else:
            payload = {'error': {'message': f'mock {code}', 'type': 'mock'}}
        b = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def log_message(self, *a):
        pass


class _Client:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.chunks = []

    def write_head(self, status, ct, extra):
        self.status = status
        self.headers.update(extra or {})

    def write_chunk(self, b):
        self.chunks.append(b)

    @property
    def body(self):
        return b''.join(self.chunks)


@pytest.fixture()
def mock_upstream():
    srv = HTTPServer(('127.0.0.1', 0), _Mock)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Mock.received = []
    _Mock.RULES = {}
    yield f'http://127.0.0.1:{port}'
    srv.shutdown()


def _run_chain(body, fallback_providers, **kw):
    c = _Client()
    r = srv_mod._proxy_chain_forward(
        body, request_format='anthropic', is_streaming=False,
        write_head=c.write_head, write_chunk=c.write_chunk,
        target_path='/v1/messages', primary_key=None, key_pool=None,
        fallback_providers=fallback_providers, timeout=10, **kw)
    return r, c


BODY = {'model': 'MiniMax-M3', 'max_tokens': 64,
        'messages': [{'role': 'user', 'content': 'hi'}]}


# ═══════════════════════ A. 内置安全网 ═══════════════════════

def test_chain_auto_includes_code_sub_provider(monkeypatch, tmp_path):
    """★ 代理链必须**自动**带上 code_sub 档（不依赖 settings.json）。

    ⚠️ 为什么不能依赖 settings.json：那是运行时文件（不入库）⇒
      新部署 / 干净 clone 会**静默丢掉这一档** ⇒ 木木又死。
    """
    monkeypatch.setattr(srv_mod, '_read_json', lambda *a, **k: {'llm': {'providers': []}})
    out = srv_mod._chain_fallback_providers()
    names = [p['name'] for p in out]
    assert 'code_sub' in names, f'代理链没有 code_sub 档: {names}'
    cs = [p for p in out if p['name'] == 'code_sub'][0]
    assert cs['api_key'] == codesub.CODE_SUB_TOKEN_MARKER, \
        '配置里必须存标记而不是真 token（token 30 分钟就过期）'
    assert cs['base_url'] == codesub.CODE_SUB_BASE
    assert cs['model'] == 'MiniMax-M3'


def test_code_sub_is_last_resort(monkeypatch):
    """code_sub 必须是**最后一档**（前面都死了才用它）。"""
    monkeypatch.setattr(srv_mod, '_read_json', lambda *a, **k: {
        'llm': {'providers': [
            {'name': 'minimax', 'apiKey': 'mm-key', 'baseUrl': 'https://x/v1', 'priority': 2},
        ]}})
    out = srv_mod._chain_fallback_providers()
    assert out[-1]['name'] == 'code_sub', f'code_sub 不在最后: {[p["name"] for p in out]}'


# ═══════════════════════ B. 鉴权形态 ═══════════════════════

def test_code_sub_adapter_uses_bearer_not_x_api_key():
    """★ Code 订阅端点只认 `Authorization: Bearer`（发 x-api-key 实测 401）。"""
    ad = pa.adapter_for('code_sub')
    h = ad.build_headers('mmoat_TESTTOKEN')
    assert h.get('Authorization') == 'Bearer mmoat_TESTTOKEN', h
    assert 'x-api-key' not in h, f'不该发 x-api-key: {h}'
    assert h.get('anthropic-version') == pa.ANTHROPIC_VERSION


def test_code_sub_adapter_is_anthropic_protocol():
    """协议仍是 Anthropic（打 /messages）—— 不是 OpenAI。"""
    ad = pa.adapter_for('code_sub')
    assert ad.api_format == 'anthropic'
    assert ad.chat_path == '/messages'
    assert ad.build_url('https://agent.minimax.cn/mavis/api/v1/llm/v1') == \
        'https://agent.minimax.cn/mavis/api/v1/llm/v1/messages'


def test_code_sub_base_is_canonical():
    """★ base 端点是**外部契约**，必须钉住。

    ⚠️ 看着像同义反复（拿常量比字面量），但作用实打实：
      端点 URL **手滑写错时没有任何类型/结构检查会报错**，
      只会表现为「请求 404 → 404 被当『能力边界』skip 掉 → 静默少一个环」，
      最后变成「木木又不回话了但没人知道为什么」。
      ⇒ 必须有一个锚点把它钉死（`scripts/mutate_llm_ring_guards.py` 的 M11 盯着它）。
    """
    assert codesub.CODE_SUB_BASE == 'https://agent.minimax.cn/mavis/api/v1/llm/v1'


# ═══════════════════════ C/D. 实时读盘 / 无凭据跳过 ═══════════════════════

def test_marker_replaced_by_fresh_token_at_send_time(mock_upstream, monkeypatch):
    """★ 标记必须在**发送时**换成实时读盘的 token（绝不缓存配置里的值）。"""
    _Mock.RULES = {'/v1/messages': [200]}
    reads = []

    def fake_token():
        reads.append(1)
        return 'mmoat_FRESH%d' % len(reads)

    monkeypatch.setattr(srv_mod.codesub, 'code_sub_token', fake_token)
    fb = [{'name': 'code_sub', 'api_key': codesub.CODE_SUB_TOKEN_MARKER,
           'base_url': f'{mock_upstream}/v1', 'model': 'MiniMax-M3'}]
    r, c = _run_chain(BODY, fb)
    assert r['ok'] is True, r
    assert len(reads) == 1, f'应实时读盘一次: {reads}'
    assert _Mock.received[0]['auth'] == 'Bearer mmoat_FRESH1', _Mock.received[0]
    assert _Mock.received[0]['x_api_key'] is None


def test_chain_reads_credential_per_request(mock_upstream, monkeypatch):
    """★ 链层：每次请求都要**重新取**凭据（不许沿用上一次那把）。

    ⚠️ 本条 monkeypatch 掉的是**读盘实现**（链层行为）——
      读盘实现本身「会不会缓存」由下一条**真实现**的测试负责。
    """
    _Mock.RULES = {'/v1/messages': [200]}
    reads = []

    def fake_token():
        reads.append(1)
        return 'mmoat_T%d' % len(reads)

    monkeypatch.setattr(srv_mod.codesub, 'code_sub_token', fake_token)
    fb = [{'name': 'code_sub', 'api_key': codesub.CODE_SUB_TOKEN_MARKER,
           'base_url': f'{mock_upstream}/v1', 'model': 'MiniMax-M3'}]
    _run_chain(BODY, fb)
    _run_chain(BODY, fb)
    assert len(reads) == 2, f'凭据被缓存了: {reads}'
    assert _Mock.received[0]['auth'] != _Mock.received[1]['auth'], '第二次请求仍用了旧 token'


def test_real_token_reader_rereads_disk_each_call(tmp_path, monkeypatch):
    """★★ 读盘层（**真实现**）：必须每次重新读盘（token 30 分钟量级 ⇒ 缓存必然过期）。

    ⚠️⚠️ 这条**绝对不许 monkeypatch 掉被测函数本身** ——
      上一版把 `code_sub_token` 换成假函数再断言「没有缓存」⇒
      **断言的是假函数的性质**，真实实现的缓存缺陷**根本测不到**
      ⇒ 变异 M13（把真实现改成缓存）跑出来**全绿**（假绿）。
      ⇒ 这正是「测试形状 ≠ 生产形状」的又一次现形：
      **被测对象必须是真的**，替身只能替「它依赖的东西」（这里是凭据盘）。
    """
    d = tmp_path / 'auth' / 'x' / 'mcode-public'
    d.mkdir(parents=True)
    f = d / 'auth.json'

    def write(tok):
        f.write_text(json.dumps(
            {'records': {'r1': {'accessToken': tok, 'expiresAtMs': 99999999999999}}}),
            encoding='utf-8')

    write('mmoat_ONE')
    monkeypatch.setattr(codesub, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')
    first = codesub.code_sub_token()
    write('mmoat_TWO')
    second = codesub.code_sub_token()

    assert first == 'mmoat_ONE', first
    assert second == 'mmoat_TWO', '真实现把凭据缓存了 —— 第二次没重新读盘'


def test_real_token_reader_skips_expired(tmp_path, monkeypatch):
    """★ 真实现：剩余有效期不足 60s 的凭据**跳过**（别让用户等一次注定 401）。"""
    import time as _time
    d = tmp_path / 'auth' / 'x' / 'mcode-public'
    d.mkdir(parents=True)
    (d / 'auth.json').write_text(json.dumps(
        {'records': {'r1': {'accessToken': 'mmoat_SOON',
                            'expiresAtMs': int(_time.time() * 1000) + 5_000}}}),
        encoding='utf-8')
    monkeypatch.setattr(codesub, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')
    assert codesub.code_sub_token() == '', '快过期的凭据不该被用'


def test_no_credential_skips_provider_without_request(mock_upstream, monkeypatch):
    """★ 没有有效凭据 ⇒ **跳过这一档**（不发请求、不崩）。"""
    monkeypatch.setattr(srv_mod.codesub, 'code_sub_token', lambda: '')
    fb = [{'name': 'code_sub', 'api_key': codesub.CODE_SUB_TOKEN_MARKER,
           'base_url': f'{mock_upstream}/v1', 'model': 'MiniMax-M3'}]
    r, c = _run_chain(BODY, fb)
    assert _Mock.received == [], '没凭据却发了请求'
    assert r['ok'] is False
    assert any(a.get('skipped') == 'no_credential' for a in r['attempts']), r['attempts']


# ═══════════════════════ E. 401 重读一次 ═══════════════════════

def test_401_rereads_credential_once(mock_upstream, monkeypatch):
    """★ 401 ⇒ 重读凭据再试**一次**（读盘到发请求之间可能刚好过期）。"""
    _Mock.RULES = {'/v1/messages': [401, 200]}
    reads = []

    def fake_token():
        reads.append(1)
        return 'mmoat_T%d' % len(reads)

    monkeypatch.setattr(srv_mod.codesub, 'code_sub_token', fake_token)
    fb = [{'name': 'code_sub', 'api_key': codesub.CODE_SUB_TOKEN_MARKER,
           'base_url': f'{mock_upstream}/v1', 'model': 'MiniMax-M3'}]
    r, c = _run_chain(BODY, fb)
    assert r['ok'] is True, r
    assert len(reads) == 2, f'401 后应重读一次: {reads}'
    assert _Mock.received[0]['auth'] == 'Bearer mmoat_T1'
    assert _Mock.received[1]['auth'] == 'Bearer mmoat_T2'


def test_401_retry_is_capped_at_one(mock_upstream, monkeypatch):
    """★ 重试**只一次** ⇒ 连续 401 时不死循环。"""
    _Mock.RULES = {'/v1/messages': [401]}
    reads = []

    def fake_token():
        reads.append(1)
        return 'mmoat_T%d' % len(reads)

    monkeypatch.setattr(srv_mod.codesub, 'code_sub_token', fake_token)
    fb = [{'name': 'code_sub', 'api_key': codesub.CODE_SUB_TOKEN_MARKER,
           'base_url': f'{mock_upstream}/v1', 'model': 'MiniMax-M3'}]
    r, c = _run_chain(BODY, fb)
    assert r['ok'] is False
    assert len(reads) == 2, f'401 重试超过一次: {reads}'


# ═══════════════════════ F/G. 不污染共用表 + 关闭开关 ═══════════════════════

def test_code_sub_does_not_pollute_shared_llm_providers(monkeypatch):
    """★ 代理链专属档位**不写进** `llm.providers`（那条表被 5 条管线共用）。

    ⚠️ 共用方：_call_chat_completion_with_fallback / _try_minimax_proxy_fallback /
      _call_minimax_vision_fallback / _heavy_minimax_fallback / _resolve_induce_llm_config
      ⇒ 往里加 Code 订阅档会**波及**它们（它们假设 x-api-key 长期凭据）。
    """
    captured = {}

    def fake_read_json(*a, **k):
        captured['read'] = True
        return {'llm': {'providers': [
            {'name': 'minimax', 'apiKey': 'mm-key', 'baseUrl': 'https://x/v1', 'priority': 2},
        ]}}

    monkeypatch.setattr(srv_mod, '_read_json', fake_read_json)
    out = srv_mod._chain_fallback_providers()
    # 代码**只读** settings，不写 ⇒ 共用表不会被污染
    src = open(os.path.join(ROOT, 'solobrave-server.py'), encoding='utf-8').read()
    assert 'proxyChainProviders' in src, '应读独立 key proxyChainProviders'
    assert "llm.setdefault('providers'" not in src and \
        "llm['providers'] =" not in src, '不许回写共用档位表'
    assert out[-1]['name'] == 'code_sub'


def test_code_sub_can_be_disabled_explicitly(monkeypatch):
    """★ `enabled: false` ⇒ 不加内置档（可回滚点明确）。"""
    monkeypatch.setattr(srv_mod, '_read_json', lambda *a, **k: {
        'llm': {'proxyChainProviders': [{'name': 'code_sub', 'enabled': False}]}})
    out = srv_mod._chain_fallback_providers()
    assert not any(p['name'] == 'code_sub' for p in out), \
        f'显式关闭却仍加了: {[p["name"] for p in out]}'


# ═══════════════════════ live：真打一次 Code 订阅 ═══════════════════════

def test_code_sub_endpoint_live_returns_text():
    """★ live：真打 Code 订阅端点 ⇒ 拿到非空回复（证明这档真的能用）。

    ⚠️ 拿不到 token 时 **skip**（那是「能力」不是「契约」）：
      契约已由上面的结构层/行为层用假凭据锁住。
    """
    tok = codesub.code_sub_token()
    if not tok:
        pytest.skip('环境无有效 Code 订阅凭据（Code 桌面端未登录/已过期）')
    req = urllib.request.Request(
        codesub.CODE_SUB_BASE + '/messages',
        data=json.dumps({'model': 'MiniMax-M3', 'max_tokens': 32,
                         'messages': [{'role': 'user', 'content': '回复「在」一个字'}]}).encode(),
        headers={'Content-Type': 'application/json', 'anthropic-version': pa.ANTHROPIC_VERSION,
                 'Authorization': f'Bearer {tok}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=90) as resp:
        d = json.loads(resp.read().decode('utf-8'))
    text = ''.join(b.get('text', '') for b in (d.get('content') or []) if isinstance(b, dict))
    assert text.strip(), f'Code 订阅回了空: {d}'
