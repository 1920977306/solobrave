# -*- coding: utf-8 -*-
"""
provider_adapters 适配层单元测试（纯模块，无 server 依赖）

覆盖：双向转换 / max_tokens 分场景默认 / system 顶层抽取 / SSE 构造与解析 /
     错误规范化 / 冷却+半开探测 / 适配器注册表与鉴权头。

运行：python tests/provider_adapters_test.py   （从仓库根目录）
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import provider_adapters as pa  # noqa: E402

PASS = []
FAIL = []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'  [{"PASS" if cond else "FAIL"}] {name}' + (f'  [{detail}]' if detail and not cond else ''))


def main():
    print('1. 请求转换 openai→anthropic')
    body = {'model': 'kimi-for-coding', 'messages': [
        {'role': 'system', 'content': 'sys'},
        {'role': 'user', 'content': [
            {'type': 'text', 'text': 'hi'},
            {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,QUJD'}}]}]}
    a = pa.transform_openai_to_anthropic(body, default_max_tokens=pa.DEFAULT_MAX_TOKENS_PROXY)
    check('system 提到顶层且从 messages 移除', a.get('system') == 'sys'
          and all(m['role'] != 'system' for m in a['messages']))
    check('image_url 转 anthropic base64 image',
          a['messages'][0]['content'][1] == {'type': 'image', 'source': {
              'type': 'base64', 'media_type': 'image/png', 'data': 'QUJD'}})
    check('proxy 场景 max_tokens 默认 4096', a['max_tokens'] == 4096)
    a2 = pa.transform_openai_to_anthropic(body, default_max_tokens=pa.DEFAULT_MAX_TOKENS_INTERNAL)
    check('内部场景 max_tokens 默认 2000', a2['max_tokens'] == 2000)
    check('显式 max_tokens 不被覆盖', pa.transform_openai_to_anthropic(
        dict(body, max_tokens=123))['max_tokens'] == 123)

    print('2. anthropic_normalize_request（a71c8f5 修复收编）')
    n = pa.anthropic_normalize_request({'model': 'm', 'messages': [
        {'role': 'system', 'content': [{'type': 'text', 'text': 's1'}]},
        {'role': 'user', 'content': 'x'}]})
    check('缺 max_tokens 补 4096', n['max_tokens'] == 4096)
    check('system 列表块合并到顶层', n['system'] == 's1')
    n2 = pa.anthropic_normalize_request({'model': 'm', 'system': 'keep', 'max_tokens': 5,
                                         'messages': [{'role': 'system', 'content': 'new'},
                                                      {'role': 'user', 'content': 'x'}]})
    check('已有顶层 system 不覆盖', n2['system'] == 'keep')
    check('max_tokens 非法值纠正', pa.anthropic_normalize_request(
        {'max_tokens': 0, 'messages': []})['max_tokens'] == 4096)

    print('3. 响应转换 anthropic↔openai')
    anth_resp = {'id': 'm1', 'type': 'message', 'role': 'assistant',
                 'content': [{'type': 'text', 'text': 'hello'}],
                 'model': 'kimi-for-coding', 'stop_reason': 'end_turn',
                 'usage': {'input_tokens': 3, 'output_tokens': 4}}
    o = pa.transform_anthropic_to_openai(anth_resp)
    check('anthropic→openai: choices/usage/finish_reason',
          o['choices'][0]['message']['content'] == 'hello'
          and o['usage'] == {'prompt_tokens': 3, 'completion_tokens': 4, 'total_tokens': 7}
          and o['choices'][0]['finish_reason'] == 'stop')
    oai_resp = {'id': 'c1', 'object': 'chat.completion',
                'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'world'},
                             'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 5, 'completion_tokens': 6, 'total_tokens': 11}}
    ar = pa.transform_openai_resp_to_anthropic(oai_resp)
    check('openai→anthropic: content/usage',
          ar['type'] == 'message' and ar['content'][0]['text'] == 'world'
          and ar['usage'] == {'input_tokens': 5, 'output_tokens': 6})

    print('4. SSE 构造与解析')
    sse = pa.anthropic_sse_from_text('txt', 'minimax-fallback', input_tokens=10, output_tokens=5)
    check('anthropic SSE 事件序列完整',
          all(x in sse for x in (b'message_start', b'content_block_start', b'text_delta',
                                 b'content_block_stop', b'message_delta', b'message_stop')))
    p = pa.AnthropicSSEParser()
    third = len(sse) // 3
    p.feed(sse[:third]); p.feed(sse[third:third * 2]); p.feed(sse[third * 2:])
    check('anthropic SSE 分片解析 usage', p.usage == {'input_tokens': 10, 'output_tokens': 5})
    osse = pa.openai_sse_from_text('txt', 'minimax', input_tokens=2, output_tokens=3)
    check('openai SSE 含 chunk 与 [DONE]', b'chat.completion.chunk' in osse and b'[DONE]' in osse)
    op = pa.OpenAISSEParser(); op.feed(osse)
    check('openai SSE 解析 usage', op.usage == {'input_tokens': 2, 'output_tokens': 3})
    check('looks_like_sse 校验', pa.looks_like_sse(b'event: message_start\ndata: {}')
          and pa.looks_like_sse(b'data: {}\n\n')
          and not pa.looks_like_sse(b'<html>Bad Gateway</html>'))
    p2 = pa.AnthropicSSEParser(); p2.feed(b'data: {broken json\n\n')
    check('解析坏事件不抛异常', p2.parse_errors == 1)

    print('5. 错误规范化')
    st, ct, payload = pa.normalize_error_payload('anthropic', 403, provider='kimi')
    d = json.loads(payload)
    check('anthropic 外壳 + 结构字段', d['type'] == 'error'
          and d['error']['type'] == 'permission_denied' and d['error']['provider'] == 'kimi'
          and d['error']['upstream_status'] == 403 and d['error']['retryable'] is True)
    st, ct, payload = pa.normalize_error_payload('openai', 400, provider='minimax')
    d = json.loads(payload)
    check('openai 外壳（无顶层 type）+ 400 不可重试',
          'type' not in d and d['error']['retryable'] is False)
    check('状态分类: 401/429 换 key', pa.classify_upstream_status(401) == pa.ERROR_ACTION_ROTATE_KEY
          and pa.classify_upstream_status(429) == pa.ERROR_ACTION_ROTATE_KEY)
    check('状态分类: 403/5xx 降级', pa.classify_upstream_status(403) == pa.ERROR_ACTION_FALLBACK
          and pa.classify_upstream_status(500) == pa.ERROR_ACTION_FALLBACK
          and pa.classify_upstream_status(503) == pa.ERROR_ACTION_FALLBACK)
    check('状态分类: 400/404 客户端错误', pa.classify_upstream_status(400) == pa.ERROR_ACTION_CLIENT
          and pa.classify_upstream_status(404) == pa.ERROR_ACTION_CLIENT)

    print('6. 冷却 + 半开探测')
    t = pa.CooldownTracker(cooldown_seconds=0.1, threshold=3)
    t.mark_failed('p'); t.mark_failed('p')
    check('未达阈值仍放行', t.allow_request('p'))
    t.mark_failed('p')
    check('达阈值进冷却', not t.allow_request('p'))
    t._cooldown_until['p'] = time.time() - 1
    check('冷却到期半开放一个探测', t.allow_request('p') and not t.allow_request('p'))
    t.mark_failed('p')
    check('探测失败重新冷却', not t.allow_request('p'))
    t._cooldown_until['p'] = time.time() - 1
    t.allow_request('p'); t.mark_ok('p')
    check('探测成功完全恢复', t.allow_request('p') and t.allow_request('p'))

    print('7. 适配器注册表与鉴权')
    check('kimi/kimicode/anthropic 都是 anthropic 格式',
          all(pa.adapter_for(n).api_format == 'anthropic' for n in ('kimi', 'kimicode', 'anthropic')))
    check('openai/minimax/deepseek 是 openai 格式',
          all(pa.adapter_for(n).api_format == 'openai' for n in ('openai', 'minimax', 'deepseek')))
    check('anthropic 鉴权头 x-api-key + anthropic-version',
          pa.adapter_for('anthropic').build_headers('k')['x-api-key'] == 'k'
          and 'Authorization' not in pa.adapter_for('anthropic').build_headers('k'))
    check('openai 鉴权头 Bearer', pa.adapter_for('openai').build_headers('k')['Authorization'] == 'Bearer k')
    check('minimax path /chatcompletion_v2',
          pa.adapter_for('minimax').build_url('http://x/v1/text') == 'http://x/v1/text/chatcompletion_v2')
    check('anthropic path /messages',
          pa.adapter_for('anthropic').build_url('http://x/v1') == 'http://x/v1/messages')
    check('未注册 provider 默认 openai 兼容', pa.adapter_for('some-new').api_format == 'openai')

    msgs, mt = pa.to_openai_messages({'system': 's', 'max_tokens': 100, 'messages': [
        {'role': 'user', 'content': [{'type': 'text', 'text': 'hi'},
                                     {'type': 'image', 'source': {'type': 'base64'}}]}]})
    check('to_openai_messages: anthropic→openai 文本归一',
          msgs == [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'hi'}] and mt == 100)

    print()
    print(f'通过 {len(PASS)}/{len(PASS) + len(FAIL)}')
    if FAIL:
        print('失败用例:', FAIL)
        sys.exit(1)
    print('ALL GREEN')


if __name__ == '__main__':
    main()
