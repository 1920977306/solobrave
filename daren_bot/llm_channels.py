"""LLM 通道（降级环）的**唯一事实来源**。

为什么要单独成文件（2026-10-22 老大拍板「kimi 现在没额度，只能你来」）：

仓库里一度有**两份**独立的降级环实现：

1. `daren_bot/executors.py:_fallback_rings_daren` —— 问答 / 选品结论链
   环 0 = MiniMax 套餐 API（Anthropic 协议）
   环 1 = **Code 订阅 OAuth（= 贾维斯自己）**
   环 2 = 套餐 key 走 OpenAI 协议（协议冗余）
   环 3/4 = kimi + minimax 开放平台（当前全灭，保留）

2. `buyin_worker.py:_text_fallback_cfgs` —— **商品采集 / 报告摘要链**
   环 0 = 套餐 key（OpenAI 协议）
   环 1 = kimi（429）
   环 2/3 = minimax 开放平台（402）

**两份环不一致的代价（实测）**：

- 问答链有「贾维斯自己」（Code 订阅 Anthropic）这条**长期可用**的环；
  采集 / 摘要链**没有** —— 一旦套餐 key 出问题（余额/额度/套餐到期），
  采集链只剩 kimi(429) + 开放平台(402) → **全灭**，
  而「报告摘要」正是这条链生成的（`summarize_for_notes` → `llm_chat_fb`）。
- 更糟的是：`buyin_worker` 的环**只支持 OpenAI 协议**
  （`_openai_chat` 打 `/chat/completions`），
  而 Code 订阅那条环**是 Anthropic 协议**（打 `/messages`）——
  就算把 key 塞进去也调不通。**协议差异才是根因。**

本模块把「环的构造 + 凭据读取 + 两种协议调用」收敛成一份，
`executors.py` 与 `buyin_worker.py` 都从这里取 —— 改一处必改两处。

──────────────────── 设计约束（改动前必读）────────────────────

1. **凭据只进 .env / 本地未跟踪文件**，代码里只读不算（AGENTS.md §7）。
   本模块**不落任何密钥**，只提供「去哪儿读」的路径。

2. **Code 订阅 token 绝不缓存**：token 是 30 分钟量级的短期凭据，
   Code 桌面端会自行续期并**重写 auth.json**（旧 token 随之作废）。
   缓存下来会在下一次调用变成 401 —— 表现为「刚开始还能用、跑一会儿全灭」，
   极易误判成「额度又没了」。→ `_code_sub_token()` **每次调用实时读盘**。

3. **401 要重读凭据再试一次**，而不是直接判定该环失败
   （读盘到发请求之间可能刚好过期）。

4. **HTTP 200 ≠ 成功**：MiniMax 会在 body 里塞 `base_resp.status_code`
   （1008 insufficient balance 等），状态码仍是 200。只看 HTTP 码会把它
   当成功 → 拿到空 content → 报「空内容」，把**余额问题伪装成模型问题**。

5. **两种鉴权头都要能发**：Anthropic 规范形态 `x-api-key`（套餐 API key）
   与 `Authorization: Bearer`（Code OAuth token）—— 环里用 `auth` 显式声明，
   **不靠猜**。

──────────────────── 环序（LIFO 降级，最稳的排最前）────────────────────

  环 0  套餐 API（Anthropic）—— 长期凭据，官方 M Plan 接入路径，最快
  环 1  Code 订阅 OAuth（= 贾维斯自己）—— 套餐 key 失效时的备份
  环 2  套餐 API（OpenAI）—— 协议冗余：Anthropic 端点挂了还能活
  环 3+ kimi / minimax 开放平台 —— 当前 429/402，保留但不指望

`vision` 链单独一套（识图要能读图的模型）。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .llm_text import strip_think

# ───────────────────────── 通道常量 ─────────────────────────

#: MiniMax 套餐 API（sk-cp-…）—— Anthropic 协议（官方 M Plan Claude Code base）
CP_ANTHROPIC_BASE = 'https://api.minimax.cn/anthropic/v1'
#: 同一把 key 走 OpenAI 协议（协议冗余环，实测同 key 也通）
CP_OPENAI_BASE = 'https://api.minimaxi.com/v1'
CP_DEFAULT_MODEL = 'MiniMax-M3'
CP_MODELS = ('MiniMax-M3', 'MiniMax-M3.1-Flash')

#: Code 订阅通道（= 贾维斯自己）—— Anthropic 协议，Bearer 鉴权
CODE_SUB_BASE = 'https://agent.minimax.cn/mavis/api/v1/llm/v1'
CODE_SUB_AUTH_DIR = Path.home() / '.minimax' / 'auth'
CODE_SUB_MODELS = ('MiniMax-M3', 'MiniMax-M3.1-Flash', 'MiniMax-M2.7')
CODE_SUB_DEFAULT_MODEL = 'MiniMax-M3'

#: 识图走 OpenAI 协议（同一把套餐 key 实测能读图）
VISION_MODELS = ('MiniMax-M3', 'MiniMax-M3.1-Flash')

#: 文本模型默认输出上限（下游会覆盖）
DEFAULT_MAX_TOKENS = 2000


# ───────────────────────── 凭据读取 ─────────────────────────

def env_api_key(name: str) -> str:
    """从项目根 `.env` 读 key（绝对路径，避开 CWD 依赖）。

    ⚠️ 2026-10-21：密钥一律只进 `.env`（已 gitignore，600 权限），
    代码里只读不算 —— 与 AGENTS.md §7 密钥管理铁律配套。
    """
    env_path = Path(__file__).resolve().parent.parent / '.env'
    if not env_path.exists():
        env_path = Path.cwd() / '.env'
    try:
        with open(env_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith(name + '='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return ''


def openclaw_api_key(provider: str) -> str:
    """从 OpenClaw 拿 provider 的**真** API key。

    ⚠️ 2026-10-21 修复：`openclaw.json` 里 `models.providers.<name>.apiKey`
    是**引用形式** `{'source': 'store', 'provider': 'default', 'id': 'MODEL_APIKEY_XXX'}`
    —— 不是密钥本身。真值在 `~/.openclaw/agents/<agent>/agent/models.json` 里。
    老代码直接把引用 dict 当 key 用（`if km.get('apiKey')` 对非空 dict 恒真），
    实际发出去的是 `Bearer {'source': 'store', ...}` → 必然 401，
    **降级链的 kimi / minimax 环从来没真正工作过**。

    查找顺序：
      1. 各 agent 的 `models.json`（真 key 落盘处）—— 逐个 agent 找
      2. `openclaw.json` 里如果直接就是字符串（旧版本 / 手工配置）也认
      3. 都是引用形式 → 返回空串（跳过该 provider），由调用方记日志
    """
    home = Path.home() / '.openclaw'
    # openclaw.json 与 models.json 的 provider 命名不一致，需别名映射：
    #   openclaw.json: 'kimi' → models.json: 'kimi-code'
    aliases = {'kimi': ('kimi', 'kimi-code')}
    names = aliases.get(provider, (provider,))
    for models_file in sorted(home.glob('agents/*/agent/models.json')):
        try:
            with open(models_file, encoding='utf-8') as f:
                mj = json.load(f)
        except Exception:
            continue
        mprovs = mj.get('providers') or {}
        for pname in names:
            pcfg = mprovs.get(pname)
            if not isinstance(pcfg, dict):
                continue
            key = pcfg.get('apiKey')
            if isinstance(key, str) and key and not key.startswith('{'):
                return key
    try:
        with open(home / 'openclaw.json', encoding='utf-8') as f:
            cfg = json.load(f)
        pcfg = (cfg.get('models', {}).get('providers', {}) or {}).get(provider) or {}
        key = pcfg.get('apiKey')
        if isinstance(key, str) and key and not key.startswith('{'):
            return key
    except Exception:
        pass
    return ''


def code_sub_token() -> str:
    """取 MiniMax Code 订阅的 OAuth accessToken（**每次调用实时读盘，绝不缓存**）。

    ⚠️ 为什么不能缓存：token 是 30 分钟量级的短期凭据，Code 桌面端会自行
    续期并**重写 auth.json**（旧 token 随之作废）。缓存下来的 token 会在
    下一次调用时变成 401 —— 表现是「刚开始还能用、跑一会儿就全灭」，
    排查时极容易误判成「额度又没了」。
    """
    now_ms = time.time() * 1000
    best = ''
    try:
        auth_files = sorted(CODE_SUB_AUTH_DIR.glob('**/mcode-public/auth.json'))
    except Exception:
        return ''
    for auth_file in auth_files:
        try:
            with open(auth_file, encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            continue
        for rec in (data.get('records') or {}).values():
            if not isinstance(rec, dict):
                continue
            tok = rec.get('accessToken') or ''
            if not tok.startswith('mmoat_'):
                continue
            exp = rec.get('expiresAtMs') or 0
            # 剩余不足 60s 视为过期 —— 宁可跳过这一环，也别让用户等一次 401
            if exp and exp - now_ms < 60_000:
                continue
            if len(tok) > len(best):
                best = tok
    return best


# ───────────────────────── 环构造 ─────────────────────────

def text_rings(requested_model: str = '') -> list:
    """文字链路降级环（问答 / 选品结论 / 报告摘要**共用**）。

    每环字段：
      protocol : 'openai'    → POST {base}/chat/completions
                 'anthropic' → POST {base}/messages
      base/key/model/temp    : 通道参数
      auth     : 'x-api-key'（套餐 API key）/ 'bearer'（Code OAuth token）
      refresh  : 是否允许 401 后重读凭据重试（仅 Code 通道为 True）
      max_tokens : 该环的输出上限（下游可覆盖）
      label    : 人类可读名（探针 / 日志用）
    """
    rings = []
    requested = (requested_model or '').strip()

    # ---- 环 0：套餐 API（Anthropic 协议）—— 长期凭据，最快 ----
    cp_key = env_api_key('MINIMAX_CP_API_KEY')
    if cp_key:
        model = requested if requested in CP_MODELS else CP_DEFAULT_MODEL
        rings.append({'protocol': 'anthropic', 'base': CP_ANTHROPIC_BASE, 'key': cp_key,
                      'model': model, 'temp': 1.0, 'models': CP_MODELS,
                      'auth': 'x-api-key', 'refresh': False,
                      'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'minimax-cp-anthropic'})
    else:
        _warn('文字链 套餐 API: .env 无 MINIMAX_CP_API_KEY → 该环跳过')

    # ---- 环 1：Code 订阅 OAuth（= 贾维斯自己）—— 套餐 key 失效时的备份 ----
    token = code_sub_token()
    if token:
        model = requested if requested in CODE_SUB_MODELS else CODE_SUB_DEFAULT_MODEL
        rings.append({'protocol': 'anthropic', 'base': CODE_SUB_BASE, 'key': token,
                      'model': model, 'temp': 1.0, 'models': CODE_SUB_MODELS,
                      'auth': 'bearer', 'refresh': True,
                      'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'code-sub'})
    else:
        _warn('文字链 Code 订阅通道: 无有效 accessToken'
              '（Code 桌面端未登录 / 凭据已过期）→ 该环跳过')

    # ---- 环 2：套餐 key 走 OpenAI 协议（协议冗余）----
    if cp_key:
        rings.append({'protocol': 'openai', 'base': CP_OPENAI_BASE, 'key': cp_key,
                      'model': CP_DEFAULT_MODEL, 'temp': 1.0, 'models': CP_MODELS,
                      'auth': 'bearer', 'refresh': False,
                      'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'minimax-cp-openai'})
    else:
        _warn('文字链 套餐 API(OpenAI 协议冗余环): 无 key → 该环跳过')

    # ---- 环 3+：kimi + minimax 开放平台（当前 429/402，保留但不指望）----
    try:
        kbase, kkey = _kimi_channel()
        if kkey:
            rings.append({'protocol': 'openai', 'base': kbase, 'key': kkey,
                          'model': 'moonshot-v1-32k', 'temp': 1.0,
                          'models': ('moonshot-v1-32k',), 'auth': 'bearer', 'refresh': False,
                          'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'kimi'})
        else:
            _warn('文字链 kimi 无可用 key → 该环跳过')
        mmkey = env_api_key('MINIMAX_API_KEY') or openclaw_api_key('minimax')
        if mmkey:
            for m in ('MiniMax-M3.1-Flash-Preview', 'MiniMax-M3'):
                rings.append({'protocol': 'openai', 'base': CP_OPENAI_BASE, 'key': mmkey,
                              'model': m, 'temp': 1.0, 'models': VISION_MODELS,
                              'auth': 'bearer', 'refresh': False,
                              'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'minimax-open'})
        else:
            _warn('文字链 minimax 开放平台无可用 key → 该环跳过')
    except Exception as e:                                    # noqa: BLE001
        _warn(f'文字链 openclaw 配置读取失败: {e}')
    return rings


def vision_rings() -> list:
    """识图链路降级环（要能读图的模型）。

    ⚠️ 识图的**探针判据**与文字链不同：一张 1×1 像素图里没有商品，
    模型**诚实返回空字段**是**正确行为**（「不编造」原则），
    不是通道死亡 —— 探针必须能区分二者，否则会误报「识图链全灭」。
    """
    rings = []
    cp_key = env_api_key('MINIMAX_CP_API_KEY')
    if cp_key:
        rings.append({'protocol': 'openai', 'base': CP_OPENAI_BASE, 'key': cp_key,
                      'model': CP_DEFAULT_MODEL, 'temp': 1.0,
                      'max_tokens': 1500, 'label': 'minimax-cp'})
    else:
        _warn('识图链 套餐 API: .env 无 MINIMAX_CP_API_KEY → 该环跳过')
    try:
        kbase, kkey = _kimi_channel()
        if kkey:
            rings.append({'protocol': 'openai', 'base': kbase, 'key': kkey,
                          'model': 'moonshot-v1-8k-vision-preview', 'temp': 1.0,
                          'max_tokens': 1500, 'label': 'kimi'})
        else:
            _warn('识图链 kimi 无可用 key → 该环跳过')
        mmkey = env_api_key('MINIMAX_API_KEY') or openclaw_api_key('minimax')
        if mmkey:
            for m in ('MiniMax-M3.1-Flash-Preview', 'MiniMax-M3'):
                rings.append({'protocol': 'openai', 'base': CP_OPENAI_BASE, 'key': mmkey,
                              'model': m, 'temp': 1.0, 'max_tokens': 1500,
                              'label': 'minimax-open'})
        else:
            _warn('识图链 minimax 开放平台无可用 key → 该环跳过')
    except Exception as e:                                    # noqa: BLE001
        _warn(f'识图链 openclaw 配置读取失败: {e}')
    return rings


def _kimi_channel():
    """kimi 的 (base, key)。base 从 openclaw.json 读，key 从 key store 读真值。"""
    try:
        with open(Path.home() / '.openclaw' / 'openclaw.json', encoding='utf-8') as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    km = (cfg.get('models', {}).get('providers', {}) or {}).get('kimi') or {}
    base = (km.get('baseUrl') or 'https://api.kimi.com/coding').rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    return base, openclaw_api_key('kimi')


def _warn(msg: str) -> None:
    """环构造期的跳过告警（走 stderr，不依赖调用方的 logger 配置）。"""
    try:
        import sys
        print(f'[llm_channels][warn] {msg}', file=sys.stderr)
    except Exception:                                         # noqa: BLE001
        pass


# ───────────────────────── 调用 ─────────────────────────

def _check_body(data: dict, model: str) -> None:
    """**HTTP 200 ≠ 成功**：MiniMax 会在 body 里塞 `base_resp.status_code`。"""
    bresp = data.get('base_resp') or {}
    if bresp.get('status_code'):
        raise RuntimeError(f'{model} {bresp.get("status_msg") or bresp.get("status_code")}')


def call_anthropic(ring: dict, prompt: str, max_tokens: int = 0) -> str:
    """Anthropic 协议环：POST {base}/messages。

    ⚠️ 401 特判：token 是 30 分钟量级的短期凭据，读盘到发请求之间可能刚好过期
    （Code 桌面端续期后重写 auth.json）→ 401 时**重读一次凭据再重试**，
    而不是直接判定该环失败。
    """
    key = ring['key']
    limit = max_tokens or ring.get('max_tokens') or DEFAULT_MAX_TOKENS
    for attempt in (1, 2):
        try:
            return _anthropic_messages(
                ring['base'], key, ring['model'], ring.get('temp', 1.0), prompt,
                auth=ring.get('auth', 'bearer'), max_tokens=limit)
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1 and ring.get('refresh'):
                fresh = code_sub_token()
                if fresh and fresh != key:
                    key = fresh
                    continue
            raise


def _anthropic_messages(base: str, key: str, model: str, temp: float,
                        prompt: str, auth: str = 'bearer',
                        max_tokens: int = DEFAULT_MAX_TOKENS) -> str:
    """Anthropic /messages 单次调用。

    `auth` 两种形态（**实测两种都通，但不能靠猜**）：
      'x-api-key' → 头 `x-api-key: <key>`（Anthropic 规范形态，套餐 API key 用这个）
      'bearer'    → 头 `Authorization: Bearer <key>`（Code OAuth token 用这个）
    """
    headers = {'Content-Type': 'application/json', 'anthropic-version': '2023-06-01'}
    if auth == 'x-api-key':
        headers['x-api-key'] = key
    else:
        headers['Authorization'] = f'Bearer {key}'
    req = urllib.request.Request(
        base.rstrip('/') + '/messages',
        data=json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': prompt[:15000]}],
            'temperature': temp,
            'max_tokens': max_tokens,
        }).encode('utf-8'),
        headers=headers,
        method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    # 同款「HTTP 200 ≠ 成功」：Code 通道成功时 base_resp.status_code=0（falsy）,
    # 非 0 才是真错误 → 用 truthy 判断，不能写成 `!= 0`。
    _check_body(data, model)
    parts = []
    for blk in (data.get('content') or []):
        # 只取 type=='text' —— 推理模型可能回 thinking 块，不该混进正文
        if isinstance(blk, dict) and blk.get('type') == 'text':
            parts.append(blk.get('text') or '')
    return strip_think(''.join(parts).strip())


def call_openai(ring: dict, prompt: str, max_tokens: int = 0) -> str:
    """OpenAI 协议环：POST {base}/chat/completions。"""
    req = urllib.request.Request(
        ring['base'].rstrip('/') + '/chat/completions',
        data=json.dumps({
            'model': ring['model'],
            'messages': [{'role': 'user', 'content': prompt[:15000]}],
            'temperature': ring.get('temp', 1.0),
            'max_tokens': max_tokens or ring.get('max_tokens') or DEFAULT_MAX_TOKENS,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f"Bearer {ring['key']}"},
        method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    # ⚠️ 2026-10-21：部分厂商（如 MiniMax）会用「HTTP 200 + body 里塞错误」的方式
    #   返回失败（base_resp.status_code=1008 insufficient balance），
    #   只看 HTTP 状态码会把它当成成功 → 必须查响应体里的真实错误。
    _check_body(data, ring['model'])
    raw = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    return strip_think(raw)


def call_ring(ring: dict, prompt: str, max_tokens: int = 0) -> str:
    """按环的 protocol 分派到对应协议的调用器。"""
    if ring.get('protocol') == 'anthropic':
        return call_anthropic(ring, prompt, max_tokens)
    return call_openai(ring, prompt, max_tokens)


# ───────────────── 多消息（system + user）版本 ─────────────────
#
# `buyin_worker` 的采集 / 摘要链用**多消息**形态（system 提示词 + user 载荷），
# 而上面的 `call_*` 是**单 prompt** 形态（问答链用）。
# 两边共用同一份环 → 必须在这里把多消息形态也统一掉，
# 否则 buyin_worker 就得自己再写一份环（= 同源同 bug 复发）。

def call_openai_messages(ring: dict, messages: list, max_tokens: int = 0,
                          temperature: float = None, timeout: int = 120) -> str:
    """OpenAI 协议 + 多消息（system/user）。"""
    mt = max_tokens or ring.get('max_tokens') or DEFAULT_MAX_TOKENS
    tmp = _resolve_temp(ring, temperature)
    req = urllib.request.Request(
        ring['base'].rstrip('/') + '/chat/completions',
        data=json.dumps({
            'model': ring['model'],
            'messages': messages,
            'temperature': tmp,
            'max_tokens': mt,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f"Bearer {ring['key']}"},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'{ring["model"]} http {e.code}: {e.read()[:300]}')
    except Exception as e:
        raise RuntimeError(f'{ring["model"]} request failed: {e}')
    _check_body(data, ring['model'])
    raw = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    return strip_think(raw)


def call_anthropic_messages(ring: dict, messages: list, max_tokens: int = 0,
                            temperature: float = None, timeout: int = 120) -> str:
    """Anthropic 协议 + 多消息（system/user）。

    ⚠️ Anthropic 协议与 OpenAI 协议的**结构差异**：system 不是 messages 里的一
    条，而是**顶层独立字段** —— 直接把 OpenAI 形态的 messages 塞进 /messages
    会丢掉 system 提示词（表现为「模型不知道要干什么」）。

    ⚠️ 401 特判：Code 订阅 token 是 30 分钟量级短期凭据，读盘到发请求之间可能
    刚好过期 → 401 时**重读一次凭据再重试**，而不是直接判定该环失败。
    """
    system, convo = _split_system(messages)
    mt = max_tokens or ring.get('max_tokens') or DEFAULT_MAX_TOKENS
    tmp = _resolve_temp(ring, temperature)
    key = ring['key']
    for attempt in (1, 2):
        headers = {'Content-Type': 'application/json',
                   'anthropic-version': '2023-06-01'}
        if ring.get('auth') == 'x-api-key':
            headers['x-api-key'] = key
        else:
            headers['Authorization'] = f'Bearer {key}'
        body = {
            'model': ring['model'],
            'messages': convo,
            'temperature': tmp,
            'max_tokens': mt,
        }
        if system:
            body['system'] = system
        req = urllib.request.Request(
            ring['base'].rstrip('/') + '/messages',
            data=json.dumps(body).encode('utf-8'),
            headers=headers,
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 401 and attempt == 1 and ring.get('refresh'):
                fresh = code_sub_token()
                if fresh and fresh != key:
                    key = fresh
                    continue
            raise RuntimeError(f'{ring["model"]} http {e.code}: {e.read()[:300]}')
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f'{ring["model"]} request failed: {e}')
        break
    _check_body(data, ring['model'])
    parts = []
    for blk in (data.get('content') or []):
        # 只取 type=='text' —— 推理模型可能回 thinking 块，不该混进正文
        if isinstance(blk, dict) and blk.get('type') == 'text':
            parts.append(blk.get('text') or '')
    return strip_think(''.join(parts).strip())


def call_ring_messages(ring: dict, messages: list, max_tokens: int = 0,
                       temperature: float = None, timeout: int = 120) -> str:
    """按环的 protocol 分派**多消息**调用（采集 / 摘要链用）。"""
    if ring.get('protocol') == 'anthropic':
        return call_anthropic_messages(ring, messages, max_tokens, temperature, timeout)
    return call_openai_messages(ring, messages, max_tokens, temperature, timeout)


def _split_system(messages: list):
    """把 OpenAI 形态的 messages 拆成 (system 文本, Anthropic 形态 messages)。"""
    system_parts = []
    convo = []
    for m in (messages or []):
        role = m.get('role')
        content = m.get('content') or ''
        if role == 'system':
            system_parts.append(content)
        elif role in ('user', 'assistant'):
            convo.append({'role': role, 'content': content})
    if not convo:
        # 没有 user 消息 → Anthropic 会直接拒；补一条空的避免整环白跑
        convo = [{'role': 'user', 'content': ''}]
    return '\n\n'.join(p for p in system_parts if p), convo


def _resolve_temp(ring: dict, temperature):
    """温度决策：kimi/moonshot coding 端点强制 temp=1.0，其余尊重传入值。

    ⚠️ 出结论类任务必须低温求可复现（temp=1.0 时同义输入会在
    「能带 / 慎带」之间飘 → 同一个商品给用户两个答案）。
    """
    if temperature is None:
        return ring.get('temp', 1.0)
    if 'moonshot' in (ring.get('model') or ''):
        return 1.0
    return temperature

