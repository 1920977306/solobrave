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

    # ★ 先**重试一次**再判成败 —— 瞬时抖动（限流/过载）重试就好，
    #   比「一失败就 skip」更诚实（skip 会掩盖真问题，见下）。
    import time as _time
    out = ''
    last_exc = None
    for attempt in (1, 2):
        try:
            out = llm_channels.call_ring_messages(ring, [
                {'role': 'system', 'content': '只回复两个字：可用'},
                {'role': 'user', 'content': '现在回复。'},
            ], max_tokens=64)
            last_exc = None
            break
        except Exception as e:                            # noqa: BLE001
            last_exc = e
            if attempt == 1:
                _time.sleep(2.0)

    if last_exc is not None:
        msg = str(last_exc)
        low = msg.lower()
        # ⚠️ **顺序很重要：先判「配置/契约错」，再判「瞬时」**。
        #
        # ★ 这条判据是被变异 M11 逼出来的（2026-10-25 实测）：
        #   base 写错时服务端返回的是 **HTTP 503**
        #   （body: `direct_route_not_configured`）——
        #   早先的判据只看状态码 '503' ⇒ 把它当「瞬时」skip 掉
        #   ⇒ 变异的测试**仍然全绿** ⇒ **恒真闸门**（测试再也不报警）。
        #   ⇒ 现在按**错误语义**分：配置错（`not_configured` / 404 / 401 …）
        #     必须 raise 让测试红；只有明确的限流/过载才 skip。
        config_err = any(m in low for m in (
            'not_configured', 'direct_route', 'not found', '404',
            'unauthorized', '401', 'invalid model', 'model not exist'))
        transient = (not config_err) and any(m in low for m in (
            'too many requests', '429', 'overloaded', '529', 'rate limit',
            'timed out', 'timeout'))
        if transient:
            pytest.skip(f'Code 订阅通道瞬时不可用（能力层，非契约问题）: {msg[:140]}')
        raise last_exc

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


# ─────────────────── D. 第二家独立服务商（智谱）───────────────────
#
# ★ 背景（2026-10-25 老大问「要再买 API 吗」的实测结论）：
#   改造前降级链有 6 个环、3 个「可用」—— 实则**全是 MiniMax 一家**
#   （套餐 API / Code 订阅 / 同一把 key 的另一条协议）。
#   ⇒ 一家挂了全线崩，**冗余是假的**。
#
# ★ 2026-10-26 老大拍板：智谱资源包**只做知识库 embedding**，不拿来做 LLM 对话
#   ⇒ 文字链 / 识图链的智谱环**全部摘除**。
#   ⇒ 下面这组测试从「智谱必须在链上」**反转**成「智谱不许回到链上」，
#     并把「独立服务商数」这条契约改成**如实报告现状**（当前 = 1 家）。
#
# 下面这组测试锁住三件事：
#   ① 链上**不许**出现智谱环（老大口径，防复活）
#   ② `llm_channels` 里**不许**残留任何智谱凭据/厂商/额度池符号
#   ③ 探针报的**独立服务商数**必须等于链上真实的 vendor 数（口径单一真源）


def test_no_zhipu_ring_in_text_rings():
    """★ 老大 2026-10-26 拍板：智谱只做知识库 embedding，不做 LLM 对话 ⇒ 文字链不许有智谱环。"""
    rings = llm_channels.text_rings()
    z = [r for r in rings if r.get('vendor') == 'zhipu']
    assert not z, (
        '文字链里又出现智谱环了 —— 智谱资源包是知识库的，不该被对话流量偷额度: '
        + str([r.get('label') for r in z]))
    assert not any('zhipu' in (r.get('label') or '') for r in rings), \
        '文字链里出现了带 zhipu 标签的环'


def test_no_zhipu_ring_in_vision_rings():
    """同上 —— 识图链也不许有智谱环（daren_bot 的识图链，不是服务端视觉 QA）。"""
    rings = llm_channels.vision_rings()
    z = [r for r in rings if r.get('vendor') == 'zhipu']
    assert not z, '识图链里又出现智谱环了: ' + str([r.get('label') for r in z])


def test_zhipu_symbols_removed_from_module():
    """★ 防复活：`llm_channels` 里不许残留任何智谱符号。

    ⚠️ 为什么连常量都要删干净（而不是留个不用的 `VENDOR_ZHIPU='zhipu'`）：
      留着符号 = 留着一条「随时可以把它接回来」的后门，而「谁接回来、为什么接回来」
      正是老大拍板过的口径 ⇒ 口径变更必须**改代码**才能生效，不是一个常量就能绕过去的。
    """
    for name in ('VENDOR_ZHIPU', 'POOL_ZHIPU_PACK', 'ZHIPU_BASE',
                 'ZHIPU_TEXT_MODELS', 'ZHIPU_VISION_MODEL', '_zhipu_channel'):
        assert not hasattr(llm_channels, name), \
            f'llm_channels 里还留着智谱符号 {name} —— 口径变更要改代码才能生效'


def test_every_ring_declares_vendor_and_pool(monkeypatch):
    """每个环都必须声明 `vendor` + `pool`（口径单一真源，不许靠下游反推）。"""
    for name, rings in (('text', llm_channels.text_rings()),
                        ('vision', llm_channels.vision_rings())):
        for r in rings:
            assert r.get('vendor'), f'{name} 环缺 vendor: {r.get("label")}'
            assert r.get('pool'), f'{name} 环缺 pool: {r.get("label")}'


def test_probe_vendor_count_matches_ring_vendor_set():
    """★ 探针报的**独立服务商数**必须等于链上真实的 vendor 数（口径单一真源）。

    ⚠️ 反例：链上只有 MiniMax 一家，探针报「2 家，有冗余」⇒
      老大问「要再买 API 吗」时拿到的是**假安全感**。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = llm_channels.text_rings()
    real_vendors = {r.get('vendor') for r in rings if r.get('vendor')}
    stat = probe._independence(rings, rings, [], [])
    assert set(stat['alive_vendors']) == real_vendors, \
        f'探针报的独立服务商数与链上真实 vendor 不符: {stat["alive_vendors"]} vs {real_vendors}'
    assert stat['vendor_redundant'] is (len(real_vendors) >= 2), stat


def test_chain_structure_kimi_gated_and_last(monkeypatch):
    """★ 2026-10-27 环序新政（老大拍板：kimi 额度留给开发）结构不变量：

      ① 默认（不设 DAREN_USE_KIMI）：两条链**没有 kimi 环**（运行时不烧 kimi）
      ② DAREN_USE_KIMI=1：kimi 环出现且**必须排在所有 minimax 环之后**（应急也是最后兜底）
      ③ 链上始终有可用环（配置没丢）——「此刻谁活着」仍归 live 探针管，这里不判。
    """
    # ① 默认关闭
    monkeypatch.delenv('DAREN_USE_KIMI', raising=False)
    for name, rings in (('text', llm_channels.text_rings()),
                        ('vision', llm_channels.vision_rings())):
        vendors = {r.get('vendor') for r in rings if r.get('vendor')}
        assert llm_channels.VENDOR_KIMI not in vendors, (
            f'{name} 链默认竟含 kimi 环 ⇒ 运行时又在烧 kimi 额度（开发资源）')
        assert vendors == {llm_channels.VENDOR_MINIMAX}, (
            f'{name} 链出现未知 vendor {vendors} ⇒ 环序被改乱了')
        assert rings, f'{name} 链一个环都没有 ⇒ 配置丢了'

    # ② 放行时 kimi 排最末
    monkeypatch.setenv('DAREN_USE_KIMI', '1')
    for name, rings in (('text', llm_channels.text_rings()),
                        ('vision', llm_channels.vision_rings())):
        labels = [r.get('label') for r in rings]
        if 'kimi' in labels:
            ki = labels.index('kimi')
            assert all(r.get('vendor') == llm_channels.VENDOR_KIMI or i < ki
                       for i, r in enumerate(rings)), (
                f'{name} 链 kimi 不在最末位（{labels}）⇒ 违反「kimi 最后兜底」新政')


def test_chain_structure_keeps_a_second_vendor_ring(monkeypatch):
    """★ 环**结构**上必须还留着第二家服务商（kimi）的环 —— 这条**不会过期**。

    ★★ 2026-10-27 环序新政后：kimi 环默认被 DAREN_USE_KIMI 闸门关闭 ⇒
      本测试在**放行态**（monkeypatch DAREN_USE_KIMI=1）下断言结构不变量：
      链上必须还有**非 minimax 的 vendor**（= 第二家服务商还在链上，没被摘掉）。
      「此刻谁活着」一律用 `daren_bot/tools/probe_llm_channels.py` 实测，
      能探就探，别写死谁死了（同款纪律：判据里写死死活 = 会过期的假绿）。
    """
    monkeypatch.setenv('DAREN_USE_KIMI', '1')
    for name, rings in (('text', llm_channels.text_rings()),
                        ('vision', llm_channels.vision_rings())):
        vendors = {r.get('vendor') for r in rings if r.get('vendor')}
        assert vendors - {llm_channels.VENDOR_MINIMAX}, (
            f'{name} 链上**只剩 minimax 一家 vendor**（{vendors}）⇒ '
            f'第二家服务商的环被摘掉了 ⇒ **冗余真的归零**，该买第二家 API 了'
            f'（若这是有意摘除 ⇒ 请同步更新 AGENTS.md §11 环序表与结论）')


LIVE_LLM = os.environ.get('SOLO_BRAVE_LIVE', '') == '1'


@pytest.mark.live
@pytest.mark.skipif(not LIVE_LLM, reason='live 门禁：需 SOLO_BRAVE_LIVE=1（真打上游）')
def test_live_chain_has_alive_independent_vendor():
    """★ live 门禁：真探针 ⇒ **此刻**活着的独立服务商 ≥ 1（默认 skip，`SOLO_BRAVE_LIVE=1` 才跑）。

    ⇒ **live 状态不写死在任何地方** ⇒ 额度生死由这里**实测**。
    ⇒ 判据：至少 1 家活 ⇒ 链不是全员死；并把**实测到的集合打出来**（供人核对认知）。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = probe.executors._fallback_rings_daren()
    assert rings, '降级链一个环都没构造出来 ⇒ 环境未配置'
    alive = []
    for idx, ring in enumerate(rings):
        try:
            status, detail = probe._probe_text_ring(ring, idx)
        except Exception as e:
            status, detail = 'dead', '%s: %s' % (type(e).__name__, e)
        print('  [live] 环%d %-22s ⇒ %s (%s)'
              % (idx, ring.get('label'), status, detail[:80]))
        if status == 'alive':
            alive.append(ring)
    vendors = {r.get('vendor') for r in alive if r.get('vendor')}
    print('\n[live] 可用环 %d/%d ｜ 活着的独立服务商: %s'
          % (len(alive), len(rings), sorted(vendors)))
    assert vendors, '**没有任何活着的独立服务商** ⇒ 全链不可用（真故障，别 skip）'



# ─────────────────── E. 探针的「假冗余」判据 ────────────────────

def test_probe_counts_vendors_not_rings():
    """探针必须按 **vendor** 统计，不能按 ring 数。

    ⚠️ 反例：3 个环里 2 个是 MiniMax 同一家的不同协议 ⇒
      按 ring 数会报「3 家兜底，出事不慌」，按 vendor 数才是「1 家，冗余是假的」。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = [
        {'vendor': 'minimax', 'pool': 'cp-subscription'},
        {'vendor': 'minimax', 'pool': 'cp-subscription'},
        {'vendor': 'minimax', 'pool': 'code-subscription'},
    ]
    stat = probe._independence(rings, rings, [], [])
    assert stat['alive_vendors'] == ['minimax'], stat
    assert stat['vendor_redundant'] is False, \
        '同一家 3 个环被判成有冗余 —— 那是假冗余'


def test_probe_recognizes_second_vendor_as_real_redundancy():
    """加进第二家 ⇒ 必须判成「冗余是真的」。"""
    from daren_bot.tools import probe_llm_channels as probe
    rings = [
        {'vendor': 'minimax', 'pool': 'cp-subscription'},
        {'vendor': 'zhipu', 'pool': 'zhipu-resource-pack'},
    ]
    stat = probe._independence(rings, rings, [], [])
    assert stat['alive_vendors'] == ['minimax', 'zhipu'], stat
    assert stat['vendor_redundant'] is True


def test_probe_counts_empty_channel_as_alive():
    """「通道通但这次没吐字」≠ 通道死 —— 必须算进活的池。

    ⚠️ 反例：识图链探针拿 1×1 图去探，模型诚实返回空是**正确行为**，
      若判成死 → 报「识图链全灭」→ 让人去查一个根本没坏的通道。

    ⚠️ 判据要盯**函数内部**的合并逻辑：若把「alive + empty」写在调用方，
      调用方漏传 empty 时这条测试就抓不到（恒真闸门）。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = [{'vendor': 'zhipu', 'pool': 'zhipu-resource-pack'}]
    # alive 空、empty 有 —— 通道是活的，只是这次没吐字
    stat = probe._independence(rings, [], rings, [])
    assert stat['alive_vendors'] == ['zhipu'], stat
    assert stat['dead_pools'] == []
    assert stat['vendor_redundant'] is False, '只有一家时不该判成有冗余'



# ══════════════════════════════════════════════════════════════
# 探针工具自身的两个真 bug（2026-10-27 实锤并修复）
# ══════════════════════════════════════════════════════════════

def test_probe_independence_accepts_real_caller_tuple_shape():
    """★ `_independence` 必须认**真实调用方传的形状**：(ring, detail) 元组。

    ⚠️★ 这个 bug 让探针**每次跑到最后一行必崩**：
      真实调用方 `main()` 存的是 `(ring, detail)` 元组，
      而函数只认 dict ⇒ `tuple.get(...)` → AttributeError ⇒
        ① 退出码**恒为 1**（哪怕有活环）⇒ 谁拿它当闸门都会误判；
        ② 最关键的「活着的独立服务商/额度池」结论**从来没打印过**。
      ⇒ 单测之所以一直是绿的：单测传的形状和真实调用方**不一样**
        （典型的「测试形状 ≠ 生产形状」—— 探针必须能失败）。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = [{'vendor': 'minimax', 'pool': 'code-subscription', 'label': 'code-sub'}]
    # ★ 真实形状：元组
    alive = [(rings[0], '1117ms · 回复「可用」')]
    stat = probe._independence(rings, alive, [], [])
    assert stat['alive_vendors'] == ['minimax'], stat
    assert stat['alive_pools'] == ['code-subscription'], stat
    assert stat['vendor_redundant'] is False, '只有一家时不该判成有冗余'


def test_probe_independence_accepts_dict_shape_backward_compatible():
    """旧形状（直接传 ring dict）仍要认 —— 不许为了修真调用方把单测打死。"""
    from daren_bot.tools import probe_llm_channels as probe
    rings = [{'vendor': 'minimax', 'pool': 'code-subscription'}]
    stat = probe._independence(rings, rings, [], [])
    assert stat['alive_vendors'] == ['minimax'], stat


def test_probe_hit_ring_summary_reports_real_index_not_hardcoded_zero():
    """★「生产会命中：环N」里的 N 必须是**真实下标**。

    ⚠️ 原来硬编码「环0」，而命中的是 alive[0] 那一档 ⇒ 实测输出
      「生产会命中：环0 code-sub」，可 code-sub 其实是**环1**
      ⇒ 运维照着「环0」去查会查错对象（把真命中的当没命中的查）。
    """
    from daren_bot.tools import probe_llm_channels as probe
    rings = [
        {'label': 'minimax-cp-anthropic', 'model': 'MiniMax-M3',
         'vendor': 'minimax', 'pool': 'cp-subscription'},
        {'label': 'code-sub', 'model': 'MiniMax-M3',
         'vendor': 'minimax', 'pool': 'code-subscription'},
    ]
    alive = [(rings[1], '1117ms · 回复「可用」')]
    out = probe._hit_ring_summary(rings, alive, 2)
    assert '环1 code-sub' in out, out
    assert '环0 code-sub' not in out, out


def test_probe_hit_ring_summary_no_alive_ring():
    """一个活环都没有时必须给「全灭」结论，而不是崩或空话。"""
    from daren_bot.tools import probe_llm_channels as probe
    out = probe._hit_ring_summary([], [], 6)
    assert '没有任何环成功返回内容' in out, out
