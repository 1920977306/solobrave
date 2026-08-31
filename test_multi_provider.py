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
