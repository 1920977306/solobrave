# -*- coding: utf-8 -*-
"""LLM 降级链收敛（daren_bot/llm_channels.py）的契约测试。

★ 2026-10-22 老大拍板「kimi 现在没额度，只能你来」后新增。

本文件锁住三条**可被静默破坏**的契约：

A. **唯一事实来源**：问答链（executors）与采集/摘要链（buyin_worker）
   必须构造出**同一套环** —— 之前它们是两份独立拷贝（第三例「同源同 bug」），
   采集链只支持 OpenAI 协议 ⇒ 装不下「贾维斯自己」那条 Anthropic 环。

B. **「贾维斯自己」必须在采集/摘要链上**：Code 订阅 OAuth 环
   （agent.minimax.cn，Anthropic 协议）—— 套餐 key 失效时的唯一备份。
   掐掉套餐 key 后，采集链必须仍能靠它跑通。

C. **协议必须真的分开**：Anthropic 环打 /messages、OpenAI 环打 /chat/completions；
   Anthropic 的 system 是**顶层字段**而非 messages 里的一条 ——
   塞错会丢掉 system 提示词（模型「不知道要干什么」）。

另加一条能力边界记录：Code 订阅环**不能读图**（529 overloaded），
所以识图环里不能有它。
"""
import os
import sys
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from daren_bot import llm_channels  # noqa: E402


# ─────────────────── 工具：加载 buyin_worker（独立脚本，非包） ───────────────────

def _load_buyin_worker():
    """importlib 加载 buyin_worker.py（它是独立脚本，不是包模块）。"""
    import importlib.util
    path = os.path.join(ROOT, 'buyin_worker.py')
    spec = importlib.util.spec_from_file_location('buyin_worker_under_test', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ─────────────────── A. 唯一事实来源 ───────────────────

def test_text_rings_shape_is_ring_dicts():
    """环必须是 dict（含 protocol/base/key/model/auth/label）——
    4 元组表达不了 Anthropic 协议环（这正是老 bug 的根因）。"""
    rings = llm_channels.text_rings()
    assert isinstance(rings, list)
    for r in rings:
        assert isinstance(r, dict), f'环不是 dict: {r}'
        for field in ('protocol', 'base', 'key', 'model', 'label'):
            assert field in r, f'环缺字段 {field}: {r}'
        assert r['protocol'] in ('openai', 'anthropic'), r


def test_executors_and_buyin_worker_share_same_rings():
    """两条链必须拿到**同一套环**（唯一事实来源）。"""
    from daren_bot import executors
    bw = _load_buyin_worker()

    a = [(r['label'], r['protocol'], r['base'], r['model']) for r in executors._fallback_rings_daren()]
    b = [(r['label'], r['protocol'], r['base'], r['model']) for r in bw._text_fallback_cfgs()]
    assert a == b, f'两条链的环不一致:\n  executors={a}\n  buyin_worker={b}'


def test_buyin_worker_has_no_local_ring_copy():
    """buyin_worker 里不许再留一份自己拼的环（防「同源同 bug」复发）。"""
    bw = _load_buyin_worker()
    src = open(os.path.join(ROOT, 'buyin_worker.py'), encoding='utf-8').read()
    # 环构造必须委托给 llm_channels
    assert 'llm_channels.text_rings()' in src
    assert 'llm_channels.vision_rings()' in src
    # 不许再有手写的 4 元组环
    assert 'for base, key, model, temp in _text_fallback_cfgs()' not in src
    assert 'for fbase, fkey, fmodel, ftemp in _fallback_cfgs()' not in src
    assert bw._text_fallback_cfgs() is not None


def test_vision_rings_do_not_contain_code_sub():
    """能力边界：Code 订阅环**不能读图**（实测 529 overloaded_error）
    → 识图环里不能有它，否则每次识图都要白等一次失败。"""
    rings = llm_channels.vision_rings()
    assert not any(r.get('label') == 'code-sub' for r in rings), \
        'Code 订阅环不支持读图，不该进识图链'


# ─────────────────── B. 「贾维斯自己」必须在采集链上 ───────────────────

def test_code_sub_ring_present_in_text_rings(monkeypatch, tmp_path):
    """「贾维斯自己」= Code 订阅 OAuth 环（Anthropic 协议）必须在文字链上。

    ⚠️ 不能因为「当前环境没有有效 token」就 skip —— 那样**环境一变，
    契约就没人测了**。用假 token 盘（monkeypatch）构造，断言**结构**成立。
    """
    auth_dir = tmp_path / 'auth' / 'z' / 'mcode-public'
    auth_dir.mkdir(parents=True)
    (auth_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_FAKE", "expiresAtMs": 99999999999999}}}',
        encoding='utf-8')
    monkeypatch.setattr(llm_channels, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')

    rings = llm_channels.text_rings()
    code = [r for r in rings if r.get('label') == 'code-sub']
    assert code, f'文字链里没有 code-sub 环: {[r.get("label") for r in rings]}'
    ring = code[0]
    assert ring['protocol'] == 'anthropic', 'Code 订阅必须走 Anthropic 协议'
    assert ring['base'] == llm_channels.CODE_SUB_BASE
    assert ring['auth'] == 'bearer', 'Code OAuth token 用 Bearer 鉴权'
    assert ring['refresh'] is True, 'Code 环必须允许 401 后重读凭据重试'


def test_buyin_worker_chain_includes_code_sub(monkeypatch, tmp_path):
    """采集/摘要链也必须有「贾维斯自己」—— 这正是老大那句话的落点。"""
    auth_dir = tmp_path / 'auth' / 'w' / 'mcode-public'
    auth_dir.mkdir(parents=True)
    (auth_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_FAKE2", "expiresAtMs": 99999999999999}}}',
        encoding='utf-8')
    monkeypatch.setattr(llm_channels, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')

    bw = _load_buyin_worker()
    rings = bw._text_fallback_cfgs()
    labels = [r.get('label') for r in rings]
    assert 'code-sub' in labels, f'采集链缺 code-sub 环: {labels}'
    # 且它必须是 Anthropic 协议 —— OpenAI 协议打不通 /messages
    code = [r for r in rings if r['label'] == 'code-sub'][0]
    assert code['protocol'] == 'anthropic'


def test_buyin_worker_survives_without_package_key(monkeypatch, tmp_path):
    """★ 核心验收：掐掉套餐 key（模拟套餐失效）后，采集/摘要链**仍能跑通**。

    这是老大「kimi 没额度只能你来」的真实验收：
    之前采集链只有套餐 key 一个活环，套餐一挂就全灭，
    而「报告摘要」正是这条链生成的。

    ⚠️ 分两层断言，**都不能因为环境缺 token 而 skip**：
      ① 结构层（用假 token 盘）：掐掉套餐 key 后 code-sub 环仍在链上
      ② 行为层（用真 token）：真发一次多消息请求并拿到非空回复；
         真 token 拿不到时才 skip —— 这是**能力**而非**契约**。
    """
    real = llm_channels.env_api_key
    monkeypatch.setattr(
        llm_channels, 'env_api_key',
        lambda name: '' if name == 'MINIMAX_CP_API_KEY' else real(name))

    # ---- ① 结构层（不依赖环境）----
    fake_dir = tmp_path / 'auth' / 'v' / 'mcode-public'
    fake_dir.mkdir(parents=True)
    (fake_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_FAKE3", "expiresAtMs": 99999999999999}}}',
        encoding='utf-8')
    monkeypatch.setattr(llm_channels, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')

    rings = llm_channels.text_rings()
    assert not any(r['label'].startswith('minimax-cp') for r in rings), '套餐环还在！'
    assert any(r['label'] == 'code-sub' for r in rings), '贾维斯环不在链上！'

    # ---- ② 行为层（需要真 token）----
    real_token = llm_channels.code_sub_token()
    if not real_token:
        pytest.skip('当前环境无有效 Code 订阅 token（能力层，契约层已验证）')

    # 恢复真 token 盘再验行为
    monkeypatch.undo()
    rings = llm_channels.text_rings()
    ring = [r for r in rings if r['label'] == 'code-sub'][0]
    out = llm_channels.call_ring_messages(ring, [
        {'role': 'system', 'content': '只回复两个字：可用'},
        {'role': 'user', 'content': '现在回复。'},
    ], max_tokens=64)
    assert out.strip(), '贾维斯环返回空'
    assert '<think>' not in out, '推理块没剥干净'


# ─────────────────── C. 协议分流正确 ───────────────────

def test_anthropic_ring_uses_messages_endpoint(monkeypatch):
    """Anthropic 环必须打 /messages，不能打 /chat/completions。"""
    calls = []

    class _Resp:
        def __init__(self, payload):
            self._p = payload
        def read(self):
            import json
            return json.dumps(self._p).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, dict(req.headers), req.data.decode()))
        return _Resp({'content': [{'type': 'text', 'text': '你好'}]})

    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)

    ring = {'protocol': 'anthropic', 'base': 'https://example.invalid/anthropic',
            'key': 'k', 'model': 'm', 'auth': 'bearer', 'temp': 1.0}
    llm_channels.call_anthropic_messages(ring, [
        {'role': 'system', 'content': 'SYS'},
        {'role': 'user', 'content': 'USR'},
    ], max_tokens=100)

    url, headers, body = calls[0]
    assert url.endswith('/messages'), url
    # Anthropic 的 system 是顶层字段，不是 messages 里的一条
    import json
    payload = json.loads(body)
    assert payload.get('system') == 'SYS', payload
    assert [m['role'] for m in payload['messages']] == ['user'], payload
    assert payload['messages'][0]['content'] == 'USR'


def test_openai_ring_uses_chat_completions(monkeypatch):
    """OpenAI 环必须打 /chat/completions，且 system 留在 messages 里。"""
    calls = []

    class _Resp:
        def read(self):
            import json
            return json.dumps({'choices': [{'message': {'content': 'ok'}}]}).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls.append((req.full_url, req.data.decode()))
        return _Resp()

    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)

    ring = {'protocol': 'openai', 'base': 'https://example.invalid/v1',
            'key': 'k', 'model': 'm', 'temp': 1.0}
    llm_channels.call_openai_messages(ring, [
        {'role': 'system', 'content': 'SYS'},
        {'role': 'user', 'content': 'USR'},
    ], max_tokens=100)

    url, body = calls[0]
    assert url.endswith('/chat/completions'), url
    import json
    payload = json.loads(body)
    assert [m['role'] for m in payload['messages']] == ['system', 'user'], payload


def test_http_200_with_error_body_is_not_success(monkeypatch):
    """「HTTP 200 ≠ 成功」：body 里塞 base_resp.status_code 必须报错。"""
    class _Resp:
        def read(self):
            import json
            return json.dumps({
                'base_resp': {'status_code': 1008, 'status_msg': 'insufficient balance'},
                'choices': [{'message': {'content': ''}}],
            }).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=None: _Resp())

    ring = {'protocol': 'openai', 'base': 'https://example.invalid/v1',
            'key': 'k', 'model': 'm', 'temp': 1.0}
    with pytest.raises(RuntimeError) as ei:
        llm_channels.call_openai_messages(ring, [{'role': 'user', 'content': 'x'}])
    assert 'insufficient balance' in str(ei.value)


def test_moonshot_temperature_forced_to_one():
    """kimi/moonshot 是 reasoning 模型，强制 temp=1.0；其余尊重传入值。"""
    assert llm_channels._resolve_temp({'model': 'moonshot-v1-32k'}, 0.0) == 1.0
    assert llm_channels._resolve_temp({'model': 'MiniMax-M3'}, 0.0) == 0.0
    assert llm_channels._resolve_temp({'model': 'MiniMax-M3'}, None) == 1.0


def test_split_system_handles_missing_user():
    """没有 user 消息时补一条空的，避免整环白跑。"""
    system, convo = llm_channels._split_system([{'role': 'system', 'content': 'S'}])
    assert system == 'S'
    assert convo == [{'role': 'user', 'content': ''}]


def test_code_sub_token_not_cached(monkeypatch, tmp_path):
    """Code 订阅 token 必须**每次实时读盘**（30 分钟量级凭据会被重写）。"""
    auth_dir = tmp_path / 'auth' / 'x' / 'mcode-public'
    auth_dir.mkdir(parents=True)
    (auth_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_AAAA", "expiresAtMs": 99999999999999}}}',
        encoding='utf-8')
    monkeypatch.setattr(llm_channels, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')

    assert llm_channels.code_sub_token() == 'mmoat_AAAA'
    # 改盘 → 立刻读到新值（证明没缓存）
    (auth_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_BBBB", "expiresAtMs": 99999999999999}}}',
        encoding='utf-8')
    assert llm_channels.code_sub_token() == 'mmoat_BBBB'


def test_expired_token_is_skipped(monkeypatch, tmp_path):
    """过期 token（剩余 < 60s）要跳过 —— 宁可少一环，也别让用户等一次 401。"""
    import time
    auth_dir = tmp_path / 'auth' / 'y' / 'mcode-public'
    auth_dir.mkdir(parents=True)
    (auth_dir / 'auth.json').write_text(
        '{"records": {"r1": {"accessToken": "mmoat_EXPIRED", "expiresAtMs": %d}}}'
        % (int(time.time() * 1000) + 10_000),
        encoding='utf-8')
    monkeypatch.setattr(llm_channels, 'CODE_SUB_AUTH_DIR', tmp_path / 'auth')
    assert llm_channels.code_sub_token() == ''


def test_no_secret_hardcoded_in_module():
    """模块里不许出现明文密钥（AGENTS.md §7）。

    ⚠️ 判据要精确：注释里写「套餐 key（sk-cp-…）」这类**前缀说明**是合法的
    （带省略号，不是密钥本体）—— 早先版本用 `'sk-cp-' not in src` 判，
    结果把自己的注释判成泄漏（假信号）。改成：
      只抓「前缀 + 足够长度的随机串」，且排除带省略号的说明。
    """
    import re
    src = open(os.path.join(ROOT, 'daren_bot', 'llm_channels.py'), encoding='utf-8').read()
    # 真密钥形态：前缀 + 一长串无分隔的字符（省略号结尾的说明不算）
    for prefix in ('sk-cp-', 'sk-api-'):
        hits = [m.group(0) for m in re.finditer(re.escape(prefix) + r'[A-Za-z0-9_\-]{12,}', src)]
        assert not hits, f'发现疑似明文密钥: {hits}'
    # OAuth token 形态：mmoat_ + 长串
    hits = [m.group(0) for m in re.finditer(r'mmoat_[A-Za-z0-9_\-]{12,}', src)]
    assert not hits, f'发现疑似明文 token: {hits}'
    # 通用 Authorization: Bearer <长串>
    hits = [m.group(0) for m in re.finditer(r'Bearer\s+[A-Za-z0-9_\-]{16,}', src)]
    assert not hits, f'发现疑似明文 bearer: {hits}'
