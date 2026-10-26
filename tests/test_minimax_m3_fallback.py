# -*- coding: utf-8 -*-
"""MiniMax 兜底档契约：**必须是套餐端点 + 真 M3**（2026-10-26）。

★ 背景（真实事故，14:24 木木回复变样）：
  代理链的兜底档来自 `settings.json` 的 `llm.providers`。
  那里的 minimax 档指向**开放平台老端点** `api.minimax.chat/v1/text`
  + 老模型 **`MiniMax-Text-01`** ⇒ 四条后果：
    ① 文风/指令遵循跟 MiniMax-M3 完全不同（老大看到的「跟以前不一样」）
    ② **老模型会把 prompt 里的 OpenClaw 元数据标记当正文复述** ——
       实测回复开头漏出 `[[response_to_current]]`
       （该标记由 OpenClaw 插在当前轮 user 消息里；MiniMax-M3 实测**不会**复述）
    ③ OpenAI 格式 ⇒ 兜底必须非流式 + SSE 包装
    ④ 拿套餐 key 打老端点只会拿到老模型

★ 本文件锁住：
  A. 适配器层：minimax = anthropic 格式 + MiniMax-M3 + /messages
  B. 兜底安全网：settings 里缺 minimax 档时，代码自动补一档**套餐端点 + M3**
     （key 只从 .env 实时读，**不写死在代码里**）
  C. 生产真相：`data/settings.json` 的 minimax 档必须指向套餐端点 + M3
"""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import provider_adapters as pa  # noqa: E402

# ─────────────────── A. 适配器层 ───────────────────

def test_minimax_adapter_is_anthropic_protocol():
    """minimax 必须走 Anthropic 协议（套餐端点）。"""
    a = pa.adapter_for('minimax')
    assert a.api_format == 'anthropic', a.api_format


def test_minimax_default_model_is_m3():
    """★ 默认模型必须是 MiniMax-M3（不是老模型 MiniMax-Text-01）。"""
    assert pa.adapter_for('minimax').default_model == 'MiniMax-M3'


def test_minimax_chat_path_is_messages():
    """套餐端点的路径是 /messages（不是老端点的 /chatcompletion_v2）。"""
    a = pa.adapter_for('minimax')
    assert a.build_url('https://api.minimax.cn/anthropic/v1') == \
        'https://api.minimax.cn/anthropic/v1/messages'


def test_minimax_headers_use_x_api_key():
    """套餐 key 用 x-api-key 鉴权（Anthropic 规范形态）。"""
    h = pa.adapter_for('minimax').build_headers('sk-cp-test')
    assert h['x-api-key'] == 'sk-cp-test'
    assert 'anthropic-version' in h


# ─────────────────── B. 兜底安全网 ───────────────────

def _load_server():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules['solobrave_server'] = mod
    return mod


def test_builtin_fallback_adds_minimax_m3_when_settings_missing(monkeypatch):
    """★ settings 里没有 minimax 档 ⇒ 代码自动补一档套餐端点 + M3。

    ⚠️ key 来自 .env（`_get_env_override`），**不写死在代码里**。
    """
    srv = _load_server()
    monkeypatch.setattr(srv, '_read_json', lambda *a, **k: {'llm': {'providers': []}})
    monkeypatch.setattr(srv, '_get_env_override', lambda name: 'sk-cp-fake' if name == 'MINIMAX_CP_API_KEY' else '')

    provs = srv._chain_fallback_providers()
    mm = [p for p in provs if p['name'] == 'minimax']
    assert mm, f'没有内置 minimax 兜底: {[p["name"] for p in provs]}'
    assert mm[0]['base_url'] == 'https://api.minimax.cn/anthropic/v1', mm[0]
    assert mm[0]['model'] == 'MiniMax-M3', mm[0]
    assert mm[0]['api_key'] == 'sk-cp-fake'


def test_builtin_fallback_skipped_without_env_key(monkeypatch):
    """没有 .env key ⇒ 不补这一档（不凭空造一个空 key 的 provider）。"""
    srv = _load_server()
    monkeypatch.setattr(srv, '_read_json', lambda *a, **k: {'llm': {'providers': []}})
    monkeypatch.setattr(srv, '_get_env_override', lambda name: '')

    provs = srv._chain_fallback_providers()
    assert not [p for p in provs if p['name'] == 'minimax'], \
        '没有 key 仍补了 minimax 档 ⇒ 会白发一轮请求'


def test_settings_minimax_is_not_overridden_by_builtin(monkeypatch):
    """settings 里**已配** minimax 档 ⇒ 不重复补（settings 是生产真相）。"""
    srv = _load_server()
    monkeypatch.setattr(
        srv, '_read_json',
        lambda *a, **k: {'llm': {'providers': [
            {'name': 'minimax', 'apiKey': 'k', 'baseUrl': 'http://x', 'model': 'm',
             'priority': 1}]}})
    monkeypatch.setattr(srv, '_get_env_override', lambda name: 'sk-cp-fake')

    provs = srv._chain_fallback_providers()
    mm = [p for p in provs if p['name'] == 'minimax']
    assert len(mm) == 1, mm
    assert mm[0]['base_url'] == 'http://x', mm[0]


# ─────────────────── C. 生产真相（settings.json）───────────────────

def test_prod_settings_minimax_points_at_cp_endpoint():
    """★ 生产 `data/settings.json` 的 minimax 档必须是套餐端点 + M3。

    这条是**生产真相**的守卫 —— 改回老端点/老模型时它会红。
    """
    p = os.path.join(ROOT, 'data', 'settings.json')
    if not os.path.exists(p):
        pytest.skip('本机没有 data/settings.json（运行时数据）')
    with open(p, encoding='utf-8') as f:
        s = json.load(f)
    provs = (s.get('llm') or {}).get('providers') or []
    mm = [x for x in provs if (x.get('name') or '').lower() == 'minimax']
    assert mm, 'settings.json 里没有 minimax 档（兜底会裸奔）'
    assert mm[0].get('baseUrl') == 'https://api.minimax.cn/anthropic/v1', mm[0]
    assert mm[0].get('model') == 'MiniMax-M3', mm[0]


def test_prod_settings_has_no_zhipu_fallback():
    """★ 智谱资源包是**知识库**的 API ⇒ 不该挂在对话兜底链上。

    老大 2026-10-26 口径；且 zhipu 档每次兜底都白等一轮 429/1113。
    """
    p = os.path.join(ROOT, 'data', 'settings.json')
    if not os.path.exists(p):
        pytest.skip('本机没有 data/settings.json（运行时数据）')
    with open(p, encoding='utf-8') as f:
        s = json.load(f)
    provs = (s.get('llm') or {}).get('providers') or []
    assert not [x for x in provs if (x.get('name') or '').lower() == 'zhipu'], \
        'zhipu 还在对话兜底链上 —— 它是知识库的 API，且每次白等一轮 429/1113'
