# -*- coding: utf-8 -*-
"""★ 代理链的「kimi 额度耗尽 ⇒ 自动切 MiniMax」断言（2026-10-27 A 边 §72）

★ 为什么必须有这条断言（老大 12:2x 原话）：
  「kimi 是恢复了，那如果再次耗尽就应该自动切换成 miniMax，而不是也跟着停止」
  ⇒ 这是一条**必须长期成立**的能力，不是今天手工测一下就完的事
  ⇒ ⇒ 把它写成**可执行断言** ⇒ 以后谁动了降级逻辑 ⇒ 这条测试立刻红。

★ 测的是**真链函数**（`solobrave-server.py` 的 `_proxy_chain_forward`），不是 mock 出来的假链
  ⇒ 该函数的 docstring 本就写着「自身不持有 HTTP handler，可独立单测」。

★ 四个场景（含 3 条负向对照 ⇒ 任何一条写错都会红）：
  ① 主 provider (kimi) 返 **429（额度耗尽）** ⇒ 必须**自动落到兜底 provider（minimax）**并写出 200
  ② **负向对照**：链上**没有**下一家 ⇒ 必须整体失败（证明「会切走」不是恒真）
  ③ **402（余额不足）** ⇒ 直接换 provider、**不换 key** ⇒ 行为必须与 429 可区分
  ④ **400（客户端错误）** ⇒ **不降级**、直接把 400 返回（证明不是「所有错误都降级」）
  ⑤ **敏感度守卫**：把 429 的分类改坏 ⇒ ① 立刻不成立 ⇒ 证明 ① 的绿灯来自真机制
"""
import importlib.util
import io
import json
import os
import sys
import urllib.error

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(REPO, "solobrave-server.py")


def _load_server():
    """按文件路径加载 solobrave-server.py（文件名带连字符 ⇒ 不能直接 import）。"""
    if "sb_server_under_test" in sys.modules:
        return sys.modules["sb_server_under_test"]
    spec = importlib.util.spec_from_file_location("sb_server_under_test", SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sb_server_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def srv():
    return _load_server()


class _FakeResp:
    """最小 200 响应替身（链的非流式路径只用 resp.read()）。"""

    def __init__(self, payload: bytes):
        self._buf = io.BytesIO(payload)
        self.status = 200
        self.headers = {}

    def read(self, *a):
        return self._buf.read(*a)

    def close(self):
        pass


def _anthropic_ok(text="收到", model="MiniMax-M3"):
    return json.dumps({
        "id": "msg_fake", "type": "message", "role": "assistant",
        "model": model, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }, ensure_ascii=False).encode("utf-8")


def _http_error(url, code, msg="err"):
    return urllib.error.HTTPError(
        url, code, msg, {}, io.BytesIO(b'{"error":{"code":"x","message":"y"}}'))


def _patch_urlopen(monkeypatch, srv, kimi_status, minimax_status=200):
    """按 URL 片段分流：kimi → 指定状态码；minimax → 200。"""
    calls = {"kimi": 0, "minimax": 0}

    def fake(req, timeout=None, context=None):
        url = req.full_url
        if "kimi.test" in url:
            calls["kimi"] += 1
            if kimi_status == 200:
                return _FakeResp(_anthropic_ok(model="moonshot-v1-32k"))
            raise _http_error(url, kimi_status)
        if "minimax.test" in url:
            calls["minimax"] += 1
            if minimax_status == 200:
                return _FakeResp(_anthropic_ok())
            raise _http_error(url, minimax_status)
        raise AssertionError("意外的上游 URL: %s" % url)

    monkeypatch.setattr(srv.urllib.request, "urlopen", fake)
    return calls


def _run_chain(srv, monkeypatch, kimi_status, fallback=True, request_format="anthropic"):
    """跑一次真链：主 = kimi，兜底 = minimax（可选）。"""
    calls = _patch_urlopen(monkeypatch, srv, kimi_status)
    written = {"head": None, "chunks": []}

    def write_head(status, content_type, extra):
        written["head"] = (status, content_type, extra or {})

    def write_chunk(data):
        written["chunks"].append(data)

    fb = ([{'name': 'minimax', 'api_key': 'fake_minimax_key',
            'base_url': 'https://minimax.test/anthropic/v1', 'model': 'MiniMax-M3'}]
          if fallback else [])

    result = srv._proxy_chain_forward(
        {"model": "moonshot-v1-32k", "max_tokens": 16,
         "messages": [{"role": "user", "content": "ping"}]},
        request_format=request_format, is_streaming=False,
        write_head=write_head, write_chunk=write_chunk,
        target_path="/v1/messages",
        primary={"name": "kimi", "url": "https://kimi.test/v1/messages"},
        primary_key="fake_kimi_key_1", key_pool=None,
        fallback_providers=fb, log_prefix="TestChain",
    )
    return result, written, calls


# ── ① 主体断言：kimi 429（额度耗尽）⇒ 自动切 minimax ──────────────────────
def test_429_on_kimi_falls_back_to_minimax(srv, monkeypatch):
    """★ 老大的要求：kimi 再耗尽时，必须自动切 MiniMax，而不是跟着停。"""
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=429)

    assert result["ok"] is True, "kimi 429 后应自动降级成功，实得 %r" % (result,)
    assert result["provider"] == "minimax", \
        "应降级到 minimax，实得 %r（attempts=%r）" % (result["provider"], result["attempts"])
    assert result["fallback_from"] == "kimi", \
        "fallback_from 应记为 kimi，实得 %r" % (result["fallback_from"],)
    assert written["head"][0] == 200, "对调用方应写出 200，实得 %r" % (written["head"],)
    assert calls["minimax"] == 1, "minimax 应被调用 1 次，实得 %d" % calls["minimax"]
    # 429 的分类 = 换 key（同 provider 内重试）⇒ 至少留下 429 的 attempt 痕迹
    assert any(a.get("status") == 429 for a in result["attempts"]), \
        "attempts 里应有 429 痕迹，实得 %r" % (result["attempts"],)


# ── ② 负向对照：链上没有下一家 ⇒ 必须整体失败（证明①不是恒真）──────────
def test_429_without_fallback_provider_fails_overall(srv, monkeypatch):
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=429, fallback=False)

    assert result["ok"] is False, "没有兜底 provider 时必须整体失败，实得 %r" % (result,)
    assert written["head"][0] == 429, \
        "应对调用方写出 429，实得 %r" % (written["head"],)
    assert calls["minimax"] == 0, "没有兜底档时不该调 minimax"
    assert result.get("error") == "all providers failed"


# ── ③ 402：直接换 provider（不换 key）⇒ 与 429 可区分 ────────────────────
def test_402_switches_provider_without_rotating_key(srv, monkeypatch):
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=402)

    assert result["ok"] is True
    assert result["provider"] == "minimax"
    # 402 = 直接降级（不换 key）⇒ kimi 只被调用 1 次；429 = 换 key ⇒ 会轮换
    assert calls["kimi"] == 1, "402 不应换 key 重试，kimi 被调 %d 次" % calls["kimi"]
    assert calls["minimax"] == 1


# ── ④ 400：客户端错误 ⇒ 不降级 ─────────────────────────────────────────
def test_400_does_not_fall_back(srv, monkeypatch):
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=400)

    assert result["ok"] is False, "400 不应被判成功"
    assert written["head"][0] == 400, "应把 400 原样返回，实得 %r" % (written["head"],)
    assert calls["minimax"] == 0, "400 不该触发降级，实调 %d 次" % calls["minimax"]


# ── ⑤ 敏感度守卫：把 429 的分类改坏 ⇒ ① 的绿灯必须消失 ─────────────────
def test_assertion_is_sensitive_to_status_classification(srv, monkeypatch):
    """证明 ① 的绿灯来自**分类机制**，而不是碰巧。"""
    padapters = srv.padapters
    orig = padapters.classify_upstream_status
    monkeypatch.setattr(
        padapters, "classify_upstream_status",
        lambda status: padapters.ERROR_ACTION_CLIENT,   # 把所有错误都当客户端错误
    )
    try:
        result, written, calls = _run_chain(srv, monkeypatch, kimi_status=429)
        assert result["ok"] is False, "分类改坏后不应再降级成功（说明①的断言不敏感）"
        assert calls["minimax"] == 0, "分类改坏后不该调 minimax"
    finally:
        monkeypatch.setattr(padapters, "classify_upstream_status", orig)


# ── ⑥ 分类表本身的契约（把「哪些状态码会降级」钉成显式契约）────────────
def test_status_classification_contract(srv):
    pa = srv.padapters
    assert pa.classify_upstream_status(429) == pa.ERROR_ACTION_ROTATE_KEY, \
        "429（额度耗尽）必须判为换 key ⇒ 换完才降级 provider"
    assert pa.classify_upstream_status(401) == pa.ERROR_ACTION_ROTATE_KEY
    for st in (402, 403, 408, 500, 502, 503, 504):
        assert pa.classify_upstream_status(st) == pa.ERROR_ACTION_FALLBACK, \
            "%d 应判为直接降级 provider" % st
    assert pa.classify_upstream_status(400) == pa.ERROR_ACTION_CLIENT
    assert pa.classify_upstream_status(404) == pa.ERROR_ACTION_CLIENT


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))


# ── ⑦ 配置契约：代理链兜底档 ≥ 2（防止「唯一兜底」回归）────────────────
def test_proxy_chain_has_at_least_two_fallback_tiers(srv):
    """★ 8080 代理链（飞书机器人走的那条）的兜底**必须 ≥ 2 档**。

    ★★ 为什么要有这条（2026-10-27 A 边 §73 实锤）：
      `data/settings.json` 的 `llm.proxyChainProviders`（代理链专属档位表）
      **一直是空的** ⇒ 兜底只有 `llm.providers` 里那**一档 minimax**
      ⇒ 木木那条链实际是 `kimi(主) → minimax(唯一兜底)` ⇒ **单点**
      ⇒ 而 **2026-10-27 12:3x minimax 套餐 Token Plan 真的 429 到顶**（真实报错
        「已达到 Token Plan 用量上限」）⇒ 若 kimi 同时耗尽 ⇒ **机器人会停**
      ⇒ 补了 `code_sub`（Code 订阅，标记式动态凭据）作为第二档兜底
      ⇒ 端到端实测：kimi 429 → minimax 429 → **code_sub 200，回复「收到」** ⇒ 没停 ✔

    ⇒ 这条断言的作用：**以后谁把兜底档清空/删掉，立即红**。
    """
    settings = os.path.join(REPO, "data", "settings.json")
    if not os.path.isfile(settings):
        pytest.skip("本机无 data/settings.json（运行时配置不在本机）⇒ 不是代码问题")
    fb = srv._chain_fallback_providers()
    names = [p.get("name") for p in fb]
    assert len(fb) >= 2, (
        "代理链兜底档只有 %d 档（%s）⇒ **单点故障** ⇒ "
        "kimi 一挂、minimax 套餐一 429，机器人就停"
        "（2026-10-27 12:3x 实测过 minimax 套餐真的 429）"
        "⇒ 请在 settings.json 的 llm.proxyChainProviders 补档"
        % (len(fb), names))
    assert "code_sub" in names, (
        "兜底档里没有 code_sub（Code 订阅）⇒ 只剩 minimax 一族 ⇒ "
        "换 minimax 的 key / 套餐 / 开放平台，全都会一起死（%s）" % (names,))
