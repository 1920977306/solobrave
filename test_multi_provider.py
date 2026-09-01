# -*- coding: utf-8 -*-
"""单元测试：多 Provider LLM 降级路由
覆盖：
1. minimax 正常 → 直接返回，不走 kimi
2. minimax 失败 + kimi 正常 → 降级到 kimi
3. kimi 失败 + minimax 正常 → 降级到 minimax（按 priority 顺序尝试）
4. 两个都挂 → 返回 None
5. 连续失败 3 次后 provider 进入 degraded 状态被跳过
6. 成功后失败计数被清空
7. 兼容旧 settings.json（无 providers 字段，只有单 provider 字段）
8. settings.json 无可用 provider → 返回 None
9. _resolve_ai_base_url / _resolve_ai_model 支持 minimax
10. _PROVIDER_FAIL_COUNTS 跨调用正确累加和重置

运行: python test_multi_provider.py
"""
import importlib.util
import json
import os
import shutil
import urllib.error
import sys
import tempfile

sys.stdout.reconfigure(encoding='utf-8')

# ── 加载 server 模块 ─────────────────────────────────
spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

# 临时目录
tmpdir = tempfile.mkdtemp(prefix='sb_test_multi_provider_')
srv.DATA_DIR = tmpdir
srv.SETTINGS_FILE = os.path.join(tmpdir, 'settings.json')
srv.CHATS_DIR = os.path.join(tmpdir, 'chats')
os.makedirs(srv.CHATS_DIR, exist_ok=True)

PASS = 0
FAIL = 0
ERRORS = []


def _write_settings(llm_cfg):
    cfg = {
        'vision': {'provider': 'kimi', 'model': 'kimi-for-coding', 'apiKey': 'sk-v', 'baseUrl': 'https://x'},
        'embedding': {'provider': 'siliconflow', 'apiKey': 'sk-e'},
    }
    if llm_cfg is not None:
        cfg['llm'] = llm_cfg
    with open(srv.SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False)


def _reset_degrade():
    srv._PROVIDER_FAIL_COUNTS.clear()


def _run(name, fn):
    global PASS, FAIL
    _reset_degrade()
    try:
        fn()
        PASS += 1
        print(f'  ✅ {name}')
    except AssertionError as e:
        FAIL += 1
        ERRORS.append((name, str(e)))
        print(f'  ❌ {name}: {e}')
    except Exception as e:
        import traceback
        FAIL += 1
        ERRORS.append((name, f'{type(e).__name__}: {e}'))
        print(f'  ❌ {name}: {type(e).__name__}: {e}')
        traceback.print_exc()


# ── mock 工具 ────────────────────────────────────────
class _MockProvider:
    def __init__(self):
        self.calls = []
        self.next_response = None  # 字符串返回内容，None 表示失败

    def __call__(self, *args, **kwargs):
        # 记录 (name, base_url, model) 之类的元组
        self.calls.append((args, kwargs))
        return self.next_response


def _patch_minimax():
    m = _MockProvider()
    srv._call_minimax_messages = m
    return m


def _patch_kimicode():
    m = _MockProvider()
    srv._call_kimicode_messages = m
    return m


MSGS = [{'role': 'user', 'content': 'hi'}]


# ═══ 测试 1：minimax 正常 → 直接返回，不走 kimi ═══
def test_minimax_success():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = 'minimax 回复'
    kc.next_response = 'kimi 不该被调用'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'minimax 回复', f'期望 minimax 回复，实际 {result!r}'
    assert len(mm.calls) == 1, f'minimax 应被调用 1 次，实际 {len(mm.calls)}'
    assert len(kc.calls) == 0, f'kimi 不该被调用，实际 {len(kc.calls)}'


# ═══ 测试 2：minimax 失败 + kimi 正常 → 降级到 kimi ═══
def test_minimax_fail_kimi_ok():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = None  # 失败
    kc.next_response = 'kimi 兜底回复'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'kimi 兜底回复', f'期望 kimi 回复，实际 {result!r}'
    assert len(mm.calls) == 1
    assert len(kc.calls) == 1
    # 失败计数：minimax=1
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 1
    assert 'kimicode' not in srv._PROVIDER_FAIL_COUNTS


# ═══ 测试 3：kimi 失败 + minimax 正常 → 降级到 minimax ═══
def test_kimi_fail_minimax_ok():
    """优先级高的 kimi 挂掉，自动降级到 minimax"""
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 1},
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    kc.next_response = None  # 失败
    mm.next_response = 'minimax 接手'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'minimax 接手', f'期望 minimax 回复，实际 {result!r}'
    assert len(kc.calls) == 1
    assert len(mm.calls) == 1
    assert srv._PROVIDER_FAIL_COUNTS.get('kimicode') == 1


# ═══ 测试 4：两个都挂 → 返回 None ═══
def test_both_fail():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = None
    kc.next_response = None

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result is None, f'期望 None，实际 {result!r}'
    assert len(mm.calls) == 1
    assert len(kc.calls) == 1
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 1
    assert srv._PROVIDER_FAIL_COUNTS.get('kimicode') == 1


# ═══ 测试 5：连续失败 3 次后 provider 进入 degraded 状态 ═══
def test_degrade_after_threshold():
    """minimax 一直失败 3 次后进入 degraded；kimi 保持正常仍可被调用"""
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = None  # minimax 一直挂
    kc.next_response = 'kimi 兜底成功'  # kimi 正常

    # 第一次
    srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert not srv._is_provider_degraded('minimax')
    # 第二次
    srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert not srv._is_provider_degraded('minimax')
    # 第三次
    srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert srv._is_provider_degraded('minimax'), '连续失败 3 次后 minimax 应进入 degraded'

    # 第四次：minimax 应被跳过，kimi 接管
    mm.calls.clear()
    kc.calls.clear()
    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert len(mm.calls) == 0, 'degraded 的 minimax 不该被调用'
    assert len(kc.calls) == 1, 'kimi 应被调用'
    assert result == 'kimi 兜底成功'


# ═══ 测试 6：成功后失败计数被清空 ═══
def test_success_resets_count():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
        ],
    })
    mm = _patch_minimax()

    # 失败 2 次
    mm.next_response = None
    srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 2

    # 成功 1 次
    mm.next_response = 'OK'
    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'OK'
    assert 'minimax' not in srv._PROVIDER_FAIL_COUNTS, '成功后失败计数应被清空'
    assert not srv._is_provider_degraded('minimax')


# ═══ 测试 7：兼容旧 settings.json（无 providers 字段）═══
def test_legacy_settings_single_provider():
    """旧格式：llm 字段没有 providers 数组，只有 provider/apiKey/baseUrl/model"""
    _write_settings({
        'provider': 'kimicode',
        'apiKey': 'sk-k-legacy',
        'baseUrl': 'https://api.kimi.com/coding/v1',
        'model': 'kimi-for-coding',
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    kc.next_response = 'legacy kimi OK'
    mm.next_response = '不该被调'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'legacy kimi OK', f'期望 legacy kimi OK，实际 {result!r}'
    assert len(kc.calls) == 1
    assert len(mm.calls) == 0


# ═══ 测试 8：settings.json 无可用 provider → 返回 None ═══
def test_no_provider():
    _write_settings({})  # llm 字段为空
    mm = _patch_minimax()
    kc = _patch_kimicode()

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result is None
    assert len(mm.calls) == 0
    assert len(kc.calls) == 0


# ═══ 测试 9：_resolve_ai_base_url / _resolve_ai_model 支持 minimax ═══
def test_resolve_minimax():
    assert srv._resolve_ai_base_url('minimax') == 'https://api.minimax.chat/v1/text'
    assert srv._resolve_ai_model('minimax') == 'MiniMax-Text-01'
    # 显式传 model 不应被默认覆盖
    assert srv._resolve_ai_model('minimax', 'custom-model') == 'custom-model'


# ═══ 测试 10：priority 排序正确（priority 数字小的先尝试）═══
def test_priority_order():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            # 故意把 kimi 放第一个，但 priority 写大
            {'name': 'kimicode', 'apiKey': 'sk-k', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 10},
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 5},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = 'minimax first'
    kc.next_response = 'kimi 不该被调'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'minimax first', f'期望 minimax 先被尝试，实际 {result!r}'
    assert len(mm.calls) == 1
    assert len(kc.calls) == 0, 'kimi 优先级低，不应被调用'


# ═══ 测试 11：provider 缺关键字段时跳过 ═══
def test_provider_skip_missing_field():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'broken', 'apiKey': '', 'baseUrl': 'https://x', 'model': 'm', 'priority': 1},  # apiKey 空
            {'name': 'minimax', 'apiKey': 'sk-m', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 2},
        ],
    })
    mm = _patch_minimax()
    kc = _patch_kimicode()
    mm.next_response = 'minimax 接住'

    result = srv._call_chat_completion_with_fallback(MSGS, timeout=10, max_tokens=100)
    assert result == 'minimax 接住', f'缺 apiKey 的 provider 应被跳过，实际 {result!r}'
    assert len(mm.calls) == 1


# ════════════════════════════════════════════════════════════
# Proxy 兑底测试：_try_minimax_proxy_fallback
# （Anthropic Messages 格式 → 调 minimax → 转回 Anthropic 格式）
# ════════════════════════════════════════════════════════════

class _FakeURLResponse:
    def __init__(self, status, raw):
        self.status = status
        self._raw = raw
    def read(self):
        return self._raw


class _FakeHTTPError(urllib.error.HTTPError):
    def __init__(self, status, body=b'{}'):
        super().__init__('http://x', status, 'X', {}, None)
        self._body = body
    def read(self):
        return self._body


def _patch_urlopen(monkey_target):
    """monkey_patch urllib.request.urlopen，monkey_target 是个对象，提供 .response 或 .do_raise 决定行为。"""
    captured = {'last_req': None, 'call_count': 0}

    def fake_urlopen(req, timeout=None, context=None):
        captured['call_count'] += 1
        captured['last_req'] = req
        if hasattr(monkey_target, 'do_raise') and monkey_target.do_raise is not None:
            raise monkey_target.do_raise
        return _FakeURLResponse(monkey_target.status, monkey_target.body)

    srv.urllib.request.urlopen = fake_urlopen
    return captured


def _enable_minimax_settings():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': 'sk-m-test', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
            {'name': 'kimicode', 'apiKey': 'sk-k-test', 'baseUrl': 'https://api.kimi.com/coding/v1',
             'model': 'kimi-for-coding', 'priority': 2},
        ],
    })


# ═══ 测试 12：proxy 兑底成功（Anthropic 文本 → minimax → Anthropic 文本）═══
def test_proxy_fallback_success_text_only():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = json.dumps({
        'id': 'minimax-xxx',
        'choices': [{'message': {'role': 'assistant', 'content': '兜底回复内容'}}],
        'usage': {'input_tokens': 10, 'output_tokens': 5},
    }).encode('utf-8')
    target.do_raise = None
    cap = _patch_urlopen(target)

    body_json = {
        'model': 'kimi-for-coding',
        'max_tokens': 2048,
        'system': '你是 Helen',
        'messages': [
            {'role': 'user', 'content': '你好'},
            {'role': 'assistant', 'content': '我好'},
            {'role': 'user', 'content': '介绍下你自己'},
        ],
    }

    result = srv._try_minimax_proxy_fallback(body_json, log_prefix='Test')
    assert result is not None, 'minimax 兜底应成功'
    parsed = json.loads(result.decode('utf-8'))
    # 验证转回 Anthropic 格式
    assert parsed['role'] == 'assistant'
    assert parsed['type'] == 'message'
    assert parsed['content'][0]['type'] == 'text'
    assert parsed['content'][0]['text'] == '兜底回复内容'
    assert parsed['stop_reason'] == 'end_turn'
    # 验证发送给 minimax 的 body 是 OpenAI 格式
    sent_body = json.loads(cap['last_req'].data.decode('utf-8'))
    assert sent_body['model'] == 'MiniMax-Text-01'
    assert sent_body['stream'] is False
    roles = [m['role'] for m in sent_body['messages']]
    assert roles == ['system', 'user', 'assistant', 'user']
    assert sent_body['messages'][0]['content'] == '你是 Helen'
    # 验证 Authorization header 用 Bearer
    auth = cap['last_req'].headers.get('Authorization')
    assert auth == 'Bearer sk-m-test', f'Authorization header 不对: {auth}'
    # 验证 URL
    assert cap['last_req'].full_url == 'https://api.minimax.chat/v1/text/chatcompletion_v2'


# ═══ 测试 13：proxy 兑底忽略图片块 ═══
def test_proxy_fallback_strips_images():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = json.dumps({
        'choices': [{'message': {'role': 'assistant', 'content': '只看到文本'}}],
    }).encode('utf-8')
    target.do_raise = None
    cap = _patch_urlopen(target)

    body_json = {
        'model': 'kimi-for-coding',
        'messages': [
            {'role': 'user', 'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'xxxx'}},
                {'type': 'text', 'text': '看图说话'},
            ]},
            {'role': 'assistant', 'content': '看到一只猫'},
        ],
    }
    result = srv._try_minimax_proxy_fallback(body_json, log_prefix='Test')
    assert result is not None
    sent_body = json.loads(cap['last_req'].data.decode('utf-8'))
    # 图片被丢，文本被保留
    assert sent_body['messages'][0]['content'] == '看图说话'
    assert sent_body['messages'][1]['content'] == '看到一只猫'
    # 没有图片字段
    assert 'image' not in json.dumps(sent_body, ensure_ascii=False)


# ═══ 测试 14：minimax 返回 HTTPError → 返回 None + 计数 +1 ═══
def test_proxy_fallback_minimax_http_error():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.do_raise = _FakeHTTPError(403, b'{"error": "rate limit"}')
    _patch_urlopen(target)

    body_json = {'messages': [{'role': 'user', 'content': 'hi'}]}
    result = srv._try_minimax_proxy_fallback(body_json, log_prefix='Test')
    assert result is None
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 1


# ═══ 测试 15：minimax 返回格式异常 → 返回 None ═══
def test_proxy_fallback_minimax_bad_format():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = b'{"some_other_field": "x"}'  # 没有 choices
    target.do_raise = None
    _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is None
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 1


# ═══ 测试 16：minimax 返回空 content → 返回 None ═══
def test_proxy_fallback_empty_content():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = json.dumps({'choices': [{'message': {'role': 'assistant', 'content': ''}}]}).encode('utf-8')
    target.do_raise = None
    _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is None


# ═══ 测试 17：settings.json 无 minimax 配置 → 直接返回 None（不调网络）═══
def test_proxy_fallback_no_minimax_config():
    _write_settings({})  # 没有 llm 字段
    target = type('T', (), {})()
    target.status = 200
    target.body = b'{}'
    target.do_raise = None
    cap = _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is None
    assert cap['call_count'] == 0, '无 minimax 配置时不该发网络请求'


# ═══ 测试 18：minimax 已 degraded → 跳过 ═══
def test_proxy_fallback_minimax_degraded():
    _enable_minimax_settings()
    srv._mark_provider_failed('minimax')
    srv._mark_provider_failed('minimax')
    srv._mark_provider_failed('minimax')  # 累计 3 次，degraded
    assert srv._is_provider_degraded('minimax')

    target = type('T', (), {})()
    target.status = 200
    target.body = b'{}'
    target.do_raise = None
    cap = _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is None
    assert cap['call_count'] == 0


# ═══ 测试 19：兜底成功 → 计数清零 ═══
def test_proxy_fallback_success_resets_count():
    _enable_minimax_settings()
    srv._mark_provider_failed('minimax')
    srv._mark_provider_failed('minimax')  # 失败 2 次
    assert srv._PROVIDER_FAIL_COUNTS.get('minimax') == 2

    target = type('T', (), {})()
    target.status = 200
    target.body = json.dumps({'choices': [{'message': {'content': 'OK'}}]}).encode('utf-8')
    target.do_raise = None
    _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is not None
    assert 'minimax' not in srv._PROVIDER_FAIL_COUNTS


# ═══ 测试 20：body_json 不是 dict → 返回 None ═══
def test_proxy_fallback_invalid_body():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = b'{}'
    target.do_raise = None
    cap = _patch_urlopen(target)

    assert srv._try_minimax_proxy_fallback(None, log_prefix='Test') is None
    assert srv._try_minimax_proxy_fallback('not a dict', log_prefix='Test') is None
    assert srv._try_minimax_proxy_fallback([], log_prefix='Test') is None
    assert cap['call_count'] == 0


# ═══ 测试 21：messages 全是空 → 返回 None（不调网络）═══
def test_proxy_fallback_no_text_messages():
    _enable_minimax_settings()
    target = type('T', (), {})()
    target.status = 200
    target.body = b'{}'
    target.do_raise = None
    cap = _patch_urlopen(target)

    # 只有图片，没有文本
    result = srv._try_minimax_proxy_fallback({
        'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'xxxx'}}
        ]}],
    }, log_prefix='Test')
    assert result is None
    assert cap['call_count'] == 0, '无可转文本时不该发网络请求'


# ═══ 测试 22：minimax apiKey 缺 → 返回 None ═══
def test_proxy_fallback_minimax_missing_apikey():
    _write_settings({
        'mode': 'fallback',
        'providers': [
            {'name': 'minimax', 'apiKey': '', 'baseUrl': 'https://api.minimax.chat/v1/text',
             'model': 'MiniMax-Text-01', 'priority': 1},
        ],
    })
    target = type('T', (), {})()
    target.status = 200
    target.body = b'{}'
    target.do_raise = None
    cap = _patch_urlopen(target)

    result = srv._try_minimax_proxy_fallback({'messages': [{'role': 'user', 'content': 'hi'}]}, log_prefix='Test')
    assert result is None
    assert cap['call_count'] == 0


# ═══ 运行所有测试 ═══
def main():
    global PASS, FAIL
    print('=' * 60)
    print('SoloBrave 多 Provider LLM 降级路由 单元测试')
    print('=' * 60)
    tests = [
        ('minimax 正常 → 直接返回', test_minimax_success),
        ('minimax 失败 + kimi 正常 → 降级到 kimi', test_minimax_fail_kimi_ok),
        ('kimi 失败 + minimax 正常 → 降级到 minimax', test_kimi_fail_minimax_ok),
        ('两个都挂 → 返回 None', test_both_fail),
        ('连续失败 3 次后 degraded 跳过', test_degrade_after_threshold),
        ('成功后失败计数清空', test_success_resets_count),
        ('兼容旧 settings.json 单 provider', test_legacy_settings_single_provider),
        ('settings.json 无可用 provider', test_no_provider),
        ('_resolve_ai_base_url / _resolve_ai_model 支持 minimax', test_resolve_minimax),
        ('priority 数字小的先尝试', test_priority_order),
        ('provider 缺关键字段跳过', test_provider_skip_missing_field),
        # Proxy 兑底
        ('proxy 兑底成功（Anthropic 文本 → minimax）', test_proxy_fallback_success_text_only),
        ('proxy 兑底忽略图片块', test_proxy_fallback_strips_images),
        ('proxy 兑底 minimax HTTPError → None', test_proxy_fallback_minimax_http_error),
        ('proxy 兑底 minimax 格式异常 → None', test_proxy_fallback_minimax_bad_format),
        ('proxy 兑底 minimax 空内容 → None', test_proxy_fallback_empty_content),
        ('proxy 兑底 无 minimax 配置 → 跳过网络', test_proxy_fallback_no_minimax_config),
        ('proxy 兑底 minimax degraded → 跳过', test_proxy_fallback_minimax_degraded),
        ('proxy 兑底 成功 → 计数清零', test_proxy_fallback_success_resets_count),
        ('proxy 兑底 body 无效 → None', test_proxy_fallback_invalid_body),
        ('proxy 兑底 无可转文本 → 跳过网络', test_proxy_fallback_no_text_messages),
        ('proxy 兑底 minimax apiKey 缺 → 跳过', test_proxy_fallback_minimax_missing_apikey),
    ]
    for name, fn in tests:
        _run(name, fn)
    print('=' * 60)
    print(f'通过 {PASS} / 失败 {FAIL} / 总计 {PASS + FAIL}')
    if ERRORS:
        print('\n失败详情:')
        for name, err in ERRORS:
            print(f'  - {name}: {err}')
        sys.exit(1)
    print('\n🎉 全部通过')
    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
