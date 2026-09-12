#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
provider_adapters.py — 统一 Provider 适配层
============================================

把 Anthropic Messages 格式与 OpenAI chat/completions 格式的请求/响应
（含流式 SSE）双向转换收敛到独立、可扩展的适配器；新增 Provider 只需
实现 ProviderAdapter 接口并注册到 ADAPTERS，主转发逻辑不变。

仅依赖标准库，不 import solobrave-server，可独立单测。

包含：
  1. ProviderAdapter 基类 + AnthropicMessagesAdapter / OpenAIChatAdapter
  2. 请求/响应格式转换（openai ↔ anthropic，含图片块）
  3. Anthropic 必填字段规范化（max_tokens 补齐 + system 顶层抽取，
     原 fix/kimi-proxy-400 逻辑收编于此，默认值按调用场景可配置）
  4. SSE：事件构造（非流式结果包装成流式）+ 流式解析（usage 提取）
  5. 错误规范化：统一错误结构，按前端请求格式包 anthropic/openai 外壳
  6. CooldownTracker：provider 失败冷却 + 半开探测（替代永久降级）
"""

import json
import threading
import time

ANTHROPIC_VERSION = '2023-06-01'

# max_tokens 默认值按调用场景区分：
# 对外代理链路（/api/proxy、/api/proxy/kimi）用 4096（fix/kimi-proxy-400 的生产值），
# 服务端内部调用（_call_kimicode_messages 等）维持原有 2000 语义。
DEFAULT_MAX_TOKENS_PROXY = 4096
DEFAULT_MAX_TOKENS_INTERNAL = 2000


# ═══════════════════════════════════════════════════
# Provider 冷却 + 半开探测
# ═══════════════════════════════════════════════════

class CooldownTracker:
    """provider 连续失败达到阈值后进入冷却（默认 10 分钟）；
    冷却到期进入半开态：只放一个试探请求，成功才恢复，失败重新冷却。
    避免一次抖动永久跳过，也避免冷却刚结束就被流量打爆（雪崩）。"""

    def __init__(self, cooldown_seconds=600, threshold=3):
        self._cooldown_seconds = cooldown_seconds
        self._threshold = threshold
        self._fail_counts = {}
        self._cooldown_until = {}
        self._probe_inflight = set()
        self._lock = threading.Lock()

    def allow_request(self, name):
        """是否允许向该 provider 发请求。冷却到期只允许一个半开探测。"""
        with self._lock:
            until = self._cooldown_until.get(name, 0)
            if time.time() < until:
                return False
            if name in self._cooldown_until:
                # 冷却刚到期：半开态，只允许一个探测请求
                if name in self._probe_inflight:
                    return False
                self._probe_inflight.add(name)
            return True

    def mark_ok(self, name):
        with self._lock:
            self._fail_counts.pop(name, None)
            self._cooldown_until.pop(name, None)
            self._probe_inflight.discard(name)

    def mark_failed(self, name):
        with self._lock:
            self._probe_inflight.discard(name)
            count = self._fail_counts.get(name, 0) + 1
            self._fail_counts[name] = count
            if count >= self._threshold:
                self._cooldown_until[name] = time.time() + self._cooldown_seconds
                return True  # 本次失败触发了（或刷新了）冷却
            return False

    def is_cooling(self, name):
        with self._lock:
            return time.time() < self._cooldown_until.get(name, 0)


# ═══════════════════════════════════════════════════
# 请求/响应格式转换（openai ↔ anthropic）
# ═══════════════════════════════════════════════════

def openai_content_to_anthropic(content):
    """将单条 OpenAI message.content 转成 Anthropic Messages API 格式。
    已含 Anthropic 原生格式（type='image' + source）的块直接透传。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    result = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type == 'text':
            result.append({'type': 'text', 'text': item.get('text', '')})
        elif item_type == 'image_url':
            url = item.get('image_url', {}).get('url', '')
            if url.startswith('data:'):
                try:
                    header, b64 = url.split(',', 1)
                    media_type = header.split(';')[0].split(':')[1]
                    result.append({
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': b64
                        }
                    })
                except Exception:
                    pass
        elif item_type == 'image' and isinstance(item.get('source'), dict):
            result.append(item)
    return result


def transform_openai_to_anthropic(body_json, default_max_tokens=DEFAULT_MAX_TOKENS_INTERNAL):
    """将 OpenAI chat/completions 请求体转为 Anthropic Messages API 格式。
    default_max_tokens 按调用场景传入（对外代理 4096 / 内部调用 2000）。"""
    system_parts = []
    messages = []
    for msg in body_json.get('messages', []):
        role = msg.get('role')
        content = msg.get('content', '')
        if role == 'system':
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                texts = [item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text']
                system_parts.extend(texts)
        elif role in ('user', 'assistant'):
            anthropic_content = openai_content_to_anthropic(content)
            messages.append({'role': role, 'content': anthropic_content})

    anthropic_body = {
        'model': body_json.get('model', ''),
        'max_tokens': body_json.get('max_tokens', default_max_tokens),
        'messages': messages
    }
    if system_parts:
        anthropic_body['system'] = '\n\n'.join(system_parts)

    temp = body_json.get('temperature')
    if temp is not None and 0 <= temp <= 1:
        anthropic_body['temperature'] = temp

    return anthropic_body


def anthropic_normalize_request(body, default_max_tokens=DEFAULT_MAX_TOKENS_PROXY):
    """Anthropic /v1/messages 必填字段规范化（原 fix/kimi-proxy-400 逻辑）：
    1. max_tokens 缺失/非 int/<1 时补默认值（按调用场景可配置）；
    2. messages 里的 system role 消息合并提到顶层 body.system（已有顶层不覆盖），
       并从 messages 数组过滤掉（anthropic 规范 messages[*].role 只能是 user/assistant）。
    就地修改并返回 body。"""
    if not isinstance(body, dict):
        return body
    if not isinstance(body.get('max_tokens'), int) or body.get('max_tokens', 0) < 1:
        body['max_tokens'] = default_max_tokens
    if isinstance(body.get('messages'), list):
        sys_msgs = [m for m in body['messages'] if isinstance(m, dict) and m.get('role') == 'system']
        if sys_msgs and 'system' not in body:
            buf = []
            for m in sys_msgs:
                c = m.get('content', '')
                if isinstance(c, str):
                    buf.append(c)
                elif isinstance(c, list):
                    for blk in c:
                        if isinstance(blk, dict) and blk.get('type') == 'text':
                            buf.append(blk.get('text', ''))
            if buf:
                body['system'] = '\n\n'.join(buf).strip()
        body['messages'] = [m for m in body['messages'] if not (isinstance(m, dict) and m.get('role') == 'system')]
    return body


def transform_anthropic_to_openai(resp_json):
    """将 Anthropic Messages API 响应转回 OpenAI chat/completions 格式。"""
    content_items = resp_json.get('content', []) if isinstance(resp_json.get('content'), list) else []
    texts = []
    for item in content_items:
        if isinstance(item, dict) and item.get('type') == 'text':
            texts.append(item.get('text', ''))
    content = ''.join(texts)

    usage = resp_json.get('usage', {})
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)

    stop_reason = resp_json.get('stop_reason', '')
    finish_reason_map = {'end_turn': 'stop', 'max_tokens': 'length', 'stop_sequence': 'stop'}
    finish_reason = finish_reason_map.get(stop_reason, stop_reason or 'stop')

    return {
        'id': resp_json.get('id', ''),
        'object': 'chat.completion',
        'model': resp_json.get('model', ''),
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': content},
            'finish_reason': finish_reason
        }],
        'usage': {
            'prompt_tokens': input_tokens,
            'completion_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens
        }
    }


def anthropic_extract_text_and_usage(resp_json):
    """从 Anthropic Messages 响应提取 (text, usage)，usage 统一为
    {'input_tokens': int, 'output_tokens': int}。"""
    content_items = resp_json.get('content', []) if isinstance(resp_json.get('content'), list) else []
    text = ''.join(item.get('text', '') for item in content_items
                   if isinstance(item, dict) and item.get('type') == 'text')
    usage_raw = resp_json.get('usage') or {}
    usage = {
        'input_tokens': int(usage_raw.get('input_tokens') or 0),
        'output_tokens': int(usage_raw.get('output_tokens') or 0),
    }
    return text, usage


def openai_extract_text_and_usage(resp_json):
    """从 OpenAI chat.completion 响应提取 (text, usage)，usage 统一为
    {'input_tokens': int, 'output_tokens': int}。"""
    text = ''
    choices = resp_json.get('choices') or []
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get('message') or {}
        text = msg.get('content') or ''
    usage_raw = resp_json.get('usage') or {}
    in_tok = usage_raw.get('prompt_tokens') or usage_raw.get('input_tokens') or 0
    out_tok = usage_raw.get('completion_tokens') or usage_raw.get('output_tokens') or 0
    return text, {'input_tokens': int(in_tok), 'output_tokens': int(out_tok)}


def to_openai_messages(body_json):
    """把 Anthropic 或 OpenAI 格式的请求体归一为 OpenAI messages 列表
    （供 OpenAI 兼容的兜底 provider 使用，如 minimax）。
    返回 (messages, max_tokens)；没有可转换的文本消息时 messages 为空列表。"""
    oai_messages = []
    if not isinstance(body_json, dict):
        return oai_messages, DEFAULT_MAX_TOKENS_PROXY
    # 判定输入格式：顶层有 'system' 或 'max_tokens' 视为 Anthropic
    input_is_anthropic = 'system' in body_json or 'max_tokens' in body_json
    if input_is_anthropic:
        system_text = body_json.get('system', '')
        if isinstance(system_text, str) and system_text:
            oai_messages.append({'role': 'system', 'content': system_text})
        elif isinstance(system_text, list):
            sys_texts = [b.get('text', '') for b in system_text if isinstance(b, dict) and b.get('type') == 'text']
            if sys_texts:
                oai_messages.append({'role': 'system', 'content': '\n'.join(sys_texts)})
        for msg in body_json.get('messages', []) or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get('role')
            if role == 'system':
                continue
            content = msg.get('content')
            if isinstance(content, str):
                oai_messages.append({'role': role, 'content': content})
            elif isinstance(content, list):
                texts = [item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text']
                if texts:
                    oai_messages.append({'role': role, 'content': '\n'.join(texts)})
    else:
        for msg in body_json.get('messages', []) or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get('role')
            content = msg.get('content')
            if isinstance(content, str):
                oai_messages.append({'role': role, 'content': content})
            elif isinstance(content, list):
                texts = [item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text']
                if texts:
                    oai_messages.append({'role': role, 'content': '\n'.join(texts)})
    max_tokens = body_json.get('max_tokens', DEFAULT_MAX_TOKENS_PROXY)
    return oai_messages, max_tokens


# ═══════════════════════════════════════════════════
# SSE：事件构造 + 流式解析
# ═══════════════════════════════════════════════════

def sse_event(event_name, data_obj):
    return f'event: {event_name}\ndata: {json.dumps(data_obj, ensure_ascii=False)}\n\n'


def anthropic_sse_from_text(text, model, msg_id=None, input_tokens=0, output_tokens=0):
    """把一段完整文本（非流式调用结果）包装成 Anthropic messages 流式 SSE 序列。
    OpenClaw 的 anthropic-messages provider 只认这套事件序列。"""
    msg_id = msg_id or f'msg-fallback-{int(time.time() * 1000)}'
    parts = [
        sse_event('message_start', {
            'type': 'message_start',
            'message': {
                'id': msg_id,
                'type': 'message',
                'role': 'assistant',
                'content': [],
                'model': model,
                'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': input_tokens, 'output_tokens': 0}
            }
        }),
        sse_event('content_block_start', {
            'type': 'content_block_start',
            'index': 0,
            'content_block': {'type': 'text', 'text': ''}
        }),
    ]
    if text:
        parts.append(sse_event('content_block_delta', {
            'type': 'content_block_delta',
            'index': 0,
            'delta': {'type': 'text_delta', 'text': text}
        }))
    parts.append(sse_event('content_block_stop', {
        'type': 'content_block_stop',
        'index': 0
    }))
    parts.append(sse_event('message_delta', {
        'type': 'message_delta',
        'delta': {'stop_reason': 'end_turn', 'stop_sequence': None},
        'usage': {'output_tokens': output_tokens}
    }))
    parts.append(sse_event('message_stop', {'type': 'message_stop'}))
    return ''.join(parts).encode('utf-8')


def openai_sse_from_text(text, model, input_tokens=0, output_tokens=0):
    """把一段完整文本包装成 OpenAI chat.completion.chunk 流式 SSE 序列。"""
    chunk_id = f'chatcmpl-fallback-{int(time.time() * 1000)}'
    created = int(time.time())

    def _chunk(delta, finish_reason=None, usage=None):
        obj = {
            'id': chunk_id,
            'object': 'chat.completion.chunk',
            'created': created,
            'model': model,
            'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish_reason}],
        }
        if usage is not None:
            obj['usage'] = usage
        return f'data: {json.dumps(obj, ensure_ascii=False)}\n\n'

    parts = [_chunk({'role': 'assistant', 'content': ''})]
    if text:
        parts.append(_chunk({'content': text}))
    parts.append(_chunk({}, finish_reason='stop', usage={
        'prompt_tokens': input_tokens,
        'completion_tokens': output_tokens,
        'total_tokens': input_tokens + output_tokens,
    }))
    parts.append('data: [DONE]\n\n')
    return ''.join(parts).encode('utf-8')


def looks_like_sse(sample_bytes):
    """首包校验：上游声称流式时，首段字节应包含 SSE 行头，
    否则视为上游故障（错误页/代理拦截页），不能让前端拿到垃圾。"""
    if not sample_bytes:
        return False
    head = sample_bytes[:4096].decode('utf-8', errors='replace').lstrip()
    return head.startswith('event:') or head.startswith('data:') or '\nevent:' in head or '\ndata:' in head


class AnthropicSSEParser:
    """增量解析 Anthropic messages 流式 SSE，提取 usage。
    feed(chunk) 可在转发的同时调用；解析失败不抛异常（透传不受影响）。"""

    def __init__(self):
        self._buffer = b''
        self.input_tokens = 0
        self.output_tokens = 0
        self.events_seen = 0
        self.parse_errors = 0

    def feed(self, chunk):
        self._buffer += chunk
        while b'\n\n' in self._buffer:
            event_str, self._buffer = self._buffer.split(b'\n\n', 1)
            self._parse_event(event_str)

    def _parse_event(self, event_bytes):
        try:
            event_text = event_bytes.decode('utf-8')
            data_str = None
            for line in event_text.split('\n'):
                if line.startswith('data:'):
                    data_str = line[5:].strip()
                    break
            if not data_str or data_str == '[DONE]':
                return
            data_json = json.loads(data_str)
            self.events_seen += 1
            evt_type = data_json.get('type', '')
            if evt_type == 'message_start':
                usage = data_json.get('message', {}).get('usage', {})
                self.input_tokens = usage.get('input_tokens', 0)
            elif evt_type == 'message_delta':
                usage = data_json.get('usage', {})
                self.output_tokens = usage.get('output_tokens', self.output_tokens)
        except Exception:
            self.parse_errors += 1

    @property
    def usage(self):
        return {'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens}


class OpenAISSEParser:
    """增量解析 OpenAI chat.completion.chunk 流式 SSE，提取 usage（若上游提供）。"""

    def __init__(self):
        self._buffer = b''
        self.input_tokens = 0
        self.output_tokens = 0
        self.events_seen = 0
        self.parse_errors = 0

    def feed(self, chunk):
        self._buffer += chunk
        while b'\n\n' in self._buffer:
            event_str, self._buffer = self._buffer.split(b'\n\n', 1)
            self._parse_event(event_str)

    def _parse_event(self, event_bytes):
        try:
            event_text = event_bytes.decode('utf-8')
            for line in event_text.split('\n'):
                if not line.startswith('data:'):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == '[DONE]':
                    continue
                data_json = json.loads(data_str)
                self.events_seen += 1
                usage = data_json.get('usage') or {}
                if usage:
                    self.input_tokens = usage.get('prompt_tokens', self.input_tokens)
                    self.output_tokens = usage.get('completion_tokens', self.output_tokens)
        except Exception:
            self.parse_errors += 1

    @property
    def usage(self):
        return {'input_tokens': self.input_tokens, 'output_tokens': self.output_tokens}


# ═══════════════════════════════════════════════════
# 错误规范化
# ═══════════════════════════════════════════════════

# 上游状态码 → 处置动作：
#   rotate_key — key 失效/限流，换 key 重试（同 provider 内）
#   fallback   — 配额/权限/服务端故障/超时，降级到下一 provider
#   client     — 请求本身有问题（400 等），规范错误直接返回前端，不重试不降级
ERROR_ACTION_ROTATE_KEY = 'rotate_key'
ERROR_ACTION_FALLBACK = 'fallback'
ERROR_ACTION_CLIENT = 'client'


def classify_upstream_status(status):
    if status in (401, 429):
        return ERROR_ACTION_ROTATE_KEY
    if status in (402, 403, 408, 425, 500, 502, 503, 504, 529):
        return ERROR_ACTION_FALLBACK
    return ERROR_ACTION_CLIENT


_STATUS_MESSAGES = {
    400: '请求参数无效',
    401: 'API Key 无效或认证失败',
    402: '上游账户余额不足',
    403: 'API 访问被拒绝（配额或权限）',
    404: '上游接口不存在',
    408: '上游请求超时',
    429: '请求过于频繁，请稍后再试',
    500: 'AI 服务端内部错误',
    502: 'AI 服务网关错误',
    503: 'AI 服务暂不可用',
    504: 'AI 服务响应超时',
}

_ERROR_TYPE_MAP = {
    400: 'invalid_request',
    401: 'authentication_error',
    402: 'quota_exceeded',
    403: 'permission_denied',
    404: 'not_found',
    408: 'timeout',
    429: 'rate_limited',
    500: 'upstream_error',
    502: 'upstream_error',
    503: 'upstream_unavailable',
    504: 'timeout',
}


def normalize_error_payload(request_format, status, provider='', detail=''):
    """把上游错误规范化为统一结构，按前端请求格式包外壳。
    上游原始响应体不进返回值（只应出现在服务端日志）。
    返回 (http_status, content_type, body_bytes)。
    结构：{error: {type, message, provider, upstream_status, retryable}}"""
    retryable = classify_upstream_status(status) in (ERROR_ACTION_ROTATE_KEY, ERROR_ACTION_FALLBACK)
    message = _STATUS_MESSAGES.get(status, f'上游服务错误（HTTP {status}）')
    if detail:
        message = f'{message}：{detail}'[:300]
    err = {
        'type': _ERROR_TYPE_MAP.get(status, 'upstream_error'),
        'message': message,
        'provider': provider or '',
        'upstream_status': status,
        'retryable': retryable,
    }
    if request_format == 'openai':
        payload = {'error': err}
    else:
        payload = {'type': 'error', 'error': err}
    return status, 'application/json', json.dumps(payload, ensure_ascii=False).encode('utf-8')


# ═══════════════════════════════════════════════════
# Provider 适配器
# ═══════════════════════════════════════════════════

class ProviderAdapter:
    """Provider 适配器基类。新增 Provider 子类化并注册到 ADAPTERS 即可，
    主转发/降级/流式逻辑不用改。

    属性：
      name           — provider 名（日志/响应头标识用）
      api_format     — 'anthropic' 或 'openai'（上游接口格式）
      default_model  — 未指定模型时的默认模型
      chat_path      — 聊天端点 path（拼在 base_url 后）
    """
    name = 'base'
    api_format = 'openai'
    default_model = ''
    chat_path = '/chat/completions'

    def build_url(self, base_url):
        return base_url.rstrip('/') + self.chat_path

    def build_headers(self, api_key):
        return {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }

    def prepare_body(self, body, default_max_tokens=DEFAULT_MAX_TOKENS_PROXY):
        """把（可能为任一格式的）请求体准备成本 provider 上游格式。
        返回新的 body dict（不修改入参）。"""
        raise NotImplementedError

    def parse_response(self, resp_json):
        """解析非流式响应 → (text, usage{'input_tokens','output_tokens'})。"""
        if self.api_format == 'anthropic':
            return anthropic_extract_text_and_usage(resp_json)
        return openai_extract_text_and_usage(resp_json)

    def convert_response_to(self, resp_json, target_format):
        """把上游响应转成前端期望的格式（'anthropic'/'openai'）。"""
        if target_format == self.api_format:
            return resp_json
        if target_format == 'openai' and self.api_format == 'anthropic':
            return transform_anthropic_to_openai(resp_json)
        raise ValueError(f'unsupported conversion: {self.api_format} -> {target_format}')

    def make_sse_parser(self):
        if self.api_format == 'anthropic':
            return AnthropicSSEParser()
        return OpenAISSEParser()

    def wrap_sse(self, text, model, request_format, usage=None):
        """把非流式完整结果包装成 request_format 的流式 SSE 字节串
        （降级 provider 只走非流式调用，流式请求靠这里包装）。"""
        usage = usage or {}
        if request_format == 'anthropic':
            return anthropic_sse_from_text(
                text, model,
                input_tokens=usage.get('input_tokens', 0),
                output_tokens=usage.get('output_tokens', 0))
        return openai_sse_from_text(
            text, model,
            input_tokens=usage.get('input_tokens', 0),
            output_tokens=usage.get('output_tokens', 0))


class AnthropicMessagesAdapter(ProviderAdapter):
    """Anthropic Messages API（api.anthropic.com、Kimi coding 兼容端点）。
    鉴权 x-api-key + anthropic-version；prepare_body 内置
    max_tokens 补齐 + system 顶层抽取（默认值按调用场景传入）。"""
    name = 'anthropic'
    api_format = 'anthropic'
    default_model = 'claude-3-5-sonnet-20241022'
    chat_path = '/messages'

    def build_headers(self, api_key):
        return {
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': ANTHROPIC_VERSION,
        }

    def prepare_body(self, body, default_max_tokens=DEFAULT_MAX_TOKENS_PROXY):
        # 格式转换（openai→anthropic）由调用方按 request_format 显式先做；
        # 这里只做 Anthropic 必填字段规范化（max_tokens 补齐 + system 顶层抽取）
        if not isinstance(body, dict):
            return body
        return anthropic_normalize_request(dict(body), default_max_tokens=default_max_tokens)


class KimiCodingAdapter(AnthropicMessagesAdapter):
    """Kimi coding（api.kimi.com/coding，Anthropic 兼容）。"""
    name = 'kimi'
    default_model = 'kimi-for-coding'


class OpenAIChatAdapter(ProviderAdapter):
    """OpenAI chat/completions 及兼容端点（deepseek/moonshot/zhipu/siliconflow 等）。"""
    name = 'openai'
    api_format = 'openai'
    default_model = 'gpt-4o-mini'
    chat_path = '/chat/completions'

    def prepare_body(self, body, default_max_tokens=DEFAULT_MAX_TOKENS_PROXY):
        if not isinstance(body, dict):
            return body
        # 仅当请求体是明确的 Anthropic 格式（顶层 system 字段）才转 OpenAI；
        # 其他情况原样透传（不补 max_tokens、不动任何字段），保持透传行为不变
        if 'system' in body:
            messages, max_tokens = to_openai_messages(body)
            out = {'model': body.get('model', ''), 'messages': messages,
                   'max_tokens': max_tokens, 'stream': bool(body.get('stream', False))}
            return out
        return dict(body)


class MinimaxAdapter(OpenAIChatAdapter):
    """MiniMax（OpenAI 兼容格式，path 特殊）。"""
    name = 'minimax'
    default_model = 'MiniMax-Text-01'
    chat_path = '/chatcompletion_v2'


def _named(cls, name, **attrs):
    inst = cls()
    inst.name = name
    for k, v in attrs.items():
        setattr(inst, k, v)
    return inst


ADAPTERS = {
    'anthropic': _named(AnthropicMessagesAdapter, 'anthropic'),
    'kimi': _named(KimiCodingAdapter, 'kimi'),
    'kimicode': _named(KimiCodingAdapter, 'kimicode'),
    'moonshot': _named(OpenAIChatAdapter, 'moonshot'),
    'openai': _named(OpenAIChatAdapter, 'openai'),
    'deepseek': _named(OpenAIChatAdapter, 'deepseek'),
    'zhipu': _named(OpenAIChatAdapter, 'zhipu'),
    'siliconflow': _named(OpenAIChatAdapter, 'siliconflow'),
    'minimax': _named(MinimaxAdapter, 'minimax'),
}


def adapter_for(provider_name):
    """按 provider 名取适配器；未注册的默认按 OpenAI 兼容处理。"""
    key = (provider_name or '').lower().strip()
    if key in ADAPTERS:
        return ADAPTERS[key]
    adapter = OpenAIChatAdapter()
    adapter.name = key or 'unknown'
    return adapter
