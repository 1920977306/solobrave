# -*- coding: utf-8 -*-
"""派单一（2026-11-12）：k3 路由丢 tool_use 根因修复守卫。

根因复盘：kimi 主路由失败后降级 minimax（非流式调用），模型在降级响应里
**已经决定调工具**（返回 tool_use 块），但 fallback 的 SSE 包装只抠文本
（parse_response → text）+ stop_reason 写死 end_turn ⇒ OpenClaw 收到纯文本
计划、工具链全断。小拓两轮 run tool.call=0，换直连立刻恢复（45 次调用）。

测试矩阵：
1. extract_tool_calls：anthropic tool_use / openai tool_calls（arguments JSON 串）都抠得出
2. anthropic_sse_from_content：tool_use 块 + input_json_delta + stop_reason='tool_use' + 文本保留
3. openai_sse_from_content：tool_calls delta + finish_reason='tool_calls'
4. wrap_sse_from_response：有工具→tool-aware；无工具→维持 text-only（向后兼容）
5. transform_openai_resp_to_anthropic / transform_anthropic_to_openai：双向保留工具调用
6. to_openai_messages：tool_use/tool_result 历史块不丢
7. OpenAIChatAdapter.prepare_body：anthropic tools 转 openai 形态透传
8. AST 守卫：_proxy_chain_forward 必须调 wrap_sse_from_response（防 refactor 退回 wrap_sse）

运行：/usr/bin/python3 -m pytest tests/test_fallback_tool_use.py -v
"""
import ast
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
import sys
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import provider_adapters as pa


# ── 1. extract_tool_calls ─────────────────────────────────────────────

def test_extract_tool_calls_anthropic():
    resp = {'content': [
        {'type': 'text', 'text': '我来查一下。'},
        {'type': 'tool_use', 'id': 'call_1', 'name': 'square_scan',
         'input': {'category': '鞋靴箱包'}},
    ], 'stop_reason': 'tool_use'}
    tcs = pa.extract_tool_calls(resp, 'anthropic')
    assert len(tcs) == 1
    assert tcs[0]['id'] == 'call_1' and tcs[0]['name'] == 'square_scan'
    assert tcs[0]['input'] == {'category': '鞋靴箱包'}


def test_extract_tool_calls_openai():
    resp = {'choices': [{'message': {
        'role': 'assistant', 'content': '我来查一下。',
        'tool_calls': [{'id': 'tc_1', 'type': 'function',
                        'function': {'name': 'square_scan',
                                     'arguments': '{"category": "鞋靴箱包"}'}}],
    }, 'finish_reason': 'tool_calls'}]}
    tcs = pa.extract_tool_calls(resp, 'openai')
    assert len(tcs) == 1
    assert tcs[0]['name'] == 'square_scan'
    assert tcs[0]['input'] == {'category': '鞋靴箱包'}
    # arguments 是坏 JSON ⇒ input 兜底 {} 不抛异常
    resp['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{bad json'
    tcs = pa.extract_tool_calls(resp, 'openai')
    assert tcs[0]['input'] == {}


def test_extract_tool_calls_empty():
    assert pa.extract_tool_calls({'content': [{'type': 'text', 'text': 'ok'}]}, 'anthropic') == []
    assert pa.extract_tool_calls({}, 'openai') == []
    assert pa.extract_tool_calls(None, 'anthropic') == []


# ── 2/3. tool-aware SSE 包装 ──────────────────────────────────────────

def _sse_events(payload_bytes):
    """把 SSE 字节串拆成 [(event, data_obj)] 列表，便于断言。"""
    out = []
    for block in payload_bytes.decode('utf-8').strip().split('\n\n'):
        ev, data = None, None
        for line in block.split('\n'):
            if line.startswith('event: '):
                ev = line[7:]
            elif line.startswith('data: '):
                data = json.loads(line[6:])
        out.append((ev, data))
    return out


def test_anthropic_sse_from_content_with_tool_use():
    payload = pa.anthropic_sse_from_content(
        '我来查一下。', [{'id': 'call_1', 'name': 'square_scan', 'input': {'category': 'x'}}],
        'minimax-fallback', input_tokens=100, output_tokens=50)
    events = _sse_events(payload)
    types = [e[0] for e in events]
    assert types[0] == 'message_start' and types[-1] == 'message_stop'
    # tool_use 块：start(input={}) → delta(input_json_delta) → stop
    starts = [d for e, d in events if e == 'content_block_start']
    assert any(d['content_block'].get('type') == 'tool_use'
               and d['content_block']['id'] == 'call_1'
               and d['content_block']['name'] == 'square_scan' for d in starts)
    deltas = [d for e, d in events if e == 'content_block_delta']
    json_deltas = [d for d in deltas if d['delta'].get('type') == 'input_json_delta']
    assert len(json_deltas) == 1
    assert json.loads(json_deltas[0]['delta']['partial_json']) == {'category': 'x'}
    # 文本块仍在
    text_deltas = [d for d in deltas if d['delta'].get('type') == 'text_delta']
    assert text_deltas and text_deltas[0]['delta']['text'] == '我来查一下。'
    # stop_reason 必须是 tool_use（这是整条修复的命门）
    md = [d for e, d in events if e == 'message_delta'][0]
    assert md['delta']['stop_reason'] == 'tool_use'


def test_anthropic_sse_from_content_text_only_backward_compat():
    payload = pa.anthropic_sse_from_content('只是文本', [], 'm')
    events = _sse_events(payload)
    md = [d for e, d in events if e == 'message_delta'][0]
    assert md['delta']['stop_reason'] == 'end_turn'
    # 与旧 text-only 包装输出结构一致（同事件序列）
    assert [e[0] for e in events] == [e[0] for e in _sse_events(
        pa.anthropic_sse_from_text('只是文本', 'm'))]


def test_openai_sse_from_content_with_tool_calls():
    payload = pa.openai_sse_from_content(
        '我来查一下。', [{'id': 'tc_1', 'name': 'square_scan', 'input': {'category': 'x'}}], 'm')
    chunks = [json.loads(l[6:]) for l in payload.decode('utf-8').strip().split('\n')
              if l.startswith('data: ') and l != 'data: [DONE]']
    tc_chunks = [c for c in chunks
                 if c['choices'][0]['delta'].get('tool_calls')]
    assert len(tc_chunks) == 1
    call = tc_chunks[0]['choices'][0]['delta']['tool_calls'][0]
    assert call['id'] == 'tc_1' and call['function']['name'] == 'square_scan'
    assert json.loads(call['function']['arguments']) == {'category': 'x'}
    finish = [c for c in chunks if c['choices'][0].get('finish_reason')]
    assert finish[-1]['choices'][0]['finish_reason'] == 'tool_calls'


# ── 4. wrap_sse_from_response 分发 ────────────────────────────────────

def test_wrap_sse_from_response_tool_aware():
    ad = pa.adapter_for('minimax')  # anthropic 格式
    resp = {'content': [
        {'type': 'text', 'text': '我来查一下。'},
        {'type': 'tool_use', 'id': 'call_1', 'name': 'square_scan', 'input': {}},
    ], 'stop_reason': 'tool_use', 'usage': {'input_tokens': 10, 'output_tokens': 5}}
    payload = ad.wrap_sse_from_response(resp, '我来查一下。', 'MiniMax-M3', 'anthropic',
                                        {'input_tokens': 10, 'output_tokens': 5})
    events = _sse_events(payload)
    md = [d for e, d in events if e == 'message_delta'][0]
    assert md['delta']['stop_reason'] == 'tool_use', '有工具调用必须标 tool_use'
    # openai 目标格式
    oad = pa.adapter_for('openai')
    oresp = {'choices': [{'message': {'role': 'assistant', 'content': 'x',
                                      'tool_calls': [{'id': 'tc_1', 'type': 'function',
                                                      'function': {'name': 'f', 'arguments': '{}'}}]},
                          'finish_reason': 'tool_calls'}]}
    payload2 = oad.wrap_sse_from_response(oresp, 'x', 'm', 'openai', {})
    assert b'"tool_calls"' in payload2 and b'[DONE]' in payload2


def test_wrap_sse_from_response_text_only_unchanged():
    ad = pa.adapter_for('minimax')
    resp = {'content': [{'type': 'text', 'text': '没有工具'}], 'stop_reason': 'end_turn'}
    payload = ad.wrap_sse_from_response(resp, '没有工具', 'm', 'anthropic', {})
    md = [d for e, d in _sse_events(payload) if e == 'message_delta'][0]
    assert md['delta']['stop_reason'] == 'end_turn'


# ── 5. 双向响应转换保留工具调用 ───────────────────────────────────────

def test_transform_openai_resp_to_anthropic_keeps_tool_calls():
    resp = {'id': 'c1', 'model': 'm', 'choices': [{'message': {
        'role': 'assistant', 'content': '查一下',
        'tool_calls': [{'id': 'tc_1', 'type': 'function',
                        'function': {'name': 'square_scan', 'arguments': '{"q": 1}'}}]},
        'finish_reason': 'tool_calls'}],
        'usage': {'prompt_tokens': 5, 'completion_tokens': 6}}
    out = pa.transform_openai_resp_to_anthropic(resp)
    tus = [b for b in out['content'] if b['type'] == 'tool_use']
    assert len(tus) == 1 and tus[0]['name'] == 'square_scan' and tus[0]['input'] == {'q': 1}
    assert out['stop_reason'] == 'tool_use'


def test_transform_anthropic_to_openai_keeps_tool_use():
    resp = {'id': 'm1', 'model': 'm', 'content': [
        {'type': 'text', 'text': '查一下'},
        {'type': 'tool_use', 'id': 'call_1', 'name': 'square_scan', 'input': {'q': 1}}],
        'stop_reason': 'tool_use', 'usage': {'input_tokens': 5, 'output_tokens': 6}}
    out = pa.transform_anthropic_to_openai(resp)
    msg = out['choices'][0]['message']
    assert msg['tool_calls'][0]['function']['name'] == 'square_scan'
    assert json.loads(msg['tool_calls'][0]['function']['arguments']) == {'q': 1}
    assert out['choices'][0]['finish_reason'] == 'tool_calls'


# ── 6/7. 请求侧：历史工具块 + tools 透传 ──────────────────────────────

def test_to_openai_messages_keeps_tool_blocks():
    body = {'system': 's', 'max_tokens': 100, 'messages': [
        {'role': 'user', 'content': '去筛达人'},
        {'role': 'assistant', 'content': [
            {'type': 'text', 'text': '我调工具'},
            {'type': 'tool_use', 'id': 'call_1', 'name': 'square_scan', 'input': {'q': 1}}]},
        {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'call_1', 'content': '结果：3个候选'}]},
    ]}
    msgs, _ = pa.to_openai_messages(body)
    roles = [m['role'] for m in msgs]
    assert roles == ['system', 'user', 'assistant', 'tool'], 'tool_use/tool_result 历史块不许丢'
    assert msgs[2]['tool_calls'][0]['function']['name'] == 'square_scan'
    assert msgs[3]['tool_call_id'] == 'call_1' and msgs[3]['content'] == '结果：3个候选'


def test_openai_adapter_prepare_body_forwards_tools():
    ad = pa.adapter_for('openai')
    body = {'system': 's', 'model': 'm', 'max_tokens': 50,
            'messages': [{'role': 'user', 'content': 'hi'}],
            'tools': [{'name': 'square_scan', 'description': '扫广场',
                       'input_schema': {'type': 'object', 'properties': {'q': {}}}}]}
    out = ad.prepare_body(body)
    assert out['tools'][0]['type'] == 'function'
    assert out['tools'][0]['function']['name'] == 'square_scan'
    assert out['tools'][0]['function']['parameters'] == {'type': 'object', 'properties': {'q': {}}}
    # 入参不被就地修改
    assert 'tools' in body and body['tools'][0].get('input_schema'), '入参 tools 保持 anthropic 形态'


# ── 8. AST 守卫：chain forward 必须走 tool-aware 包装 ─────────────────

def test_chain_forward_uses_tool_aware_wrap():
    src = open(os.path.join(ROOT, 'solobrave-server.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == '_proxy_chain_forward':
            calls = {n.func.attr for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            assert 'wrap_sse_from_response' in calls, \
                '_proxy_chain_forward 必须调 wrap_sse_from_response（tool-aware），防退回 wrap_sse'
            return
    raise AssertionError('_proxy_chain_forward 未找到')
