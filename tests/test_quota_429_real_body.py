# -*- coding: utf-8 -*-
"""★ **额度耗尽型 429 ⇒ 直接降级 provider** —— 用**真实现场的 body**（B 边 §80）

★ 这条为什么独立于 A 边 §78 的第 ⑧ 组（不是重复，是补上他缺的那一半）：
  A 边 §78 的样本 `KIMI_QUOTA_BODY` 是**构造**的：
      {"error":{"code":"exceeded_current_quota_error","message":"月计费周期额度已用尽"}}
  ⇒ 用 OpenAI 风格的 **`code`** 字段 ＋ 中文文案；
  ⇒ 而**生产日志里真实发生的** Kimi 月度额度耗尽（2026-10-27 03:00，`server.log`）是：
      {"error":{"type":"rate_limit_error","message":"You've reached your monthly usage limit
       for this billing cycle. Your quota will be refreshed in the next cycle. To continue
       now, purchase extra usage or upgrade your plan: ..."}}
  ⇒ **字段名都不同**（`type` vs `code`）、文案是英文、
     且 **`type` 字段的值恰好是 `rate_limit_error`**（额度耗尽与普通限流**共用**它）
  ⇒ ⇒ 用真实 body 实测：A 边 §78 的实现**判 False**（当成限流）⇒ **逐把试 key 照旧**
  ⇒ ⇒ ★ **样本不真实 ⇒ 绿灯不代表真实现场**（假绿家族的新变种）

★ 本文件的作用 = 把**真实 body** 钉成回归基线：
  ① 真实额度 body ⇒ 必须判 fallback
  ② ★ type 字段免疫：`"type":"rate_limit_error"` **不得**把额度型误判成限流
  ③ 对照：普通限流 body ⇒ 仍判 rotate_key（行为不变 ⇒ 零回归）
  ④ 对照：A 边 §78 的构造样本 ⇒ 仍判 fallback（**他的判据不被破坏** ⇒ 零破坏证明）
  ⑤ 契约：不传 body ⇒ 与历史完全一致
  ⑥ 链级（★ 决定性）：真实额度 body ⇒ **kimi 只被调 1 次**（不逐把试 key）⇒ 直落 minimax 200
  ⑦ 链级对照：普通限流 ⇒ **仍走完 3 把 key** ⇒ 与 ⑥ 只差 body ⇒ 判别精确
  ⑧ 敏感度守卫：把分类器改坏 ⇒ ⑥ 立刻不成立（证明 ⑥ 的绿灯来自真机制）
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

# ★★★ 生产日志里的**真实** Kimi 额度耗尽 body（逐字，2026-10-27 03:00 实测）
#   ⚠️ 只含错误文案，不含任何密钥
REAL_QUOTA_429_BODY = (
    '{"error":{"type":"rate_limit_error","message":"You\'ve reached your monthly usage '
    'limit for this billing cycle. Your quota will be refreshed in the next cycle. To '
    'continue now, purchase extra usage or upgrade your plan: '
    'https://www.kimi.com/membership/subscription?tab=quota"},"type":"error"}'
)

# 普通限流 429（**不含**任何额度语义）⇒ 必须仍判 rotate_key
PLAIN_429_BODY = (
    '{"error":{"type":"rate_limit_error","message":"Too many requests. '
    'Please slow down and retry later."}}'
)

# A 边 §78 第 ⑧ 组用的**构造样本**（逐字抄来）⇒ 用它证明「我的改动不破坏他的判据」
A_SIDE_CONSTRUCTED_QUOTA_BODY = (
    '{"error":{"code":"exceeded_current_quota_error","message":"月计费周期额度已用尽"}}'
)


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


class _FakeKeyPool:
    """最小 key 池替身：记录 get_key / mark_failed（用来证明「有没有逐把试」）。"""

    def __init__(self, keys):
        self.keys = list(keys)
        self.failed = []
        self.get_calls = 0

    def get_key(self):
        self.get_calls += 1
        return self.keys.pop(0) if self.keys else None

    def mark_failed(self, key):
        self.failed.append(key)


def _anthropic_ok(text="收到", model="MiniMax-M3"):
    return json.dumps({
        "id": "msg_fake", "type": "message", "role": "assistant",
        "model": model, "content": [{"type": "text", "text": text}],
        "usage": {"input_tokens": 5, "output_tokens": 3},
    }, ensure_ascii=False).encode("utf-8")


def _http_error(url, code, body: bytes):
    return urllib.error.HTTPError(url, code, "err", {}, io.BytesIO(body))


def _run_chain(srv, monkeypatch, kimi_body: bytes):
    """跑一次真链：主 = kimi（**3 把 key**：2 主 + 1 池），兜底 = minimax。

    ★ 3 把 key 是刻意的：只有 key 池 >1 时，「逐把试」与「不逐把试」才**分得开**
      （A 边 §78 的第 ⑧ 组用的是单把 key ⇒ 测不出这一点）。
    """
    calls = {"kimi": 0, "minimax": 0}
    pool = _FakeKeyPool(["fake_pool_key_1"])

    def fake(req, timeout=None, context=None):
        url = req.full_url
        if "kimi.test" in url:
            calls["kimi"] += 1
            raise _http_error(url, 429, kimi_body)
        if "minimax.test" in url:
            calls["minimax"] += 1
            return _FakeResp(_anthropic_ok())
        raise AssertionError("意外的上游 URL: %s" % url)

    monkeypatch.setattr(srv.urllib.request, "urlopen", fake)

    written = {"head": None, "chunks": []}

    def write_head(status, content_type, extra):
        written["head"] = (status, content_type, extra or {})

    def write_chunk(data):
        written["chunks"].append(data)

    fb = [{'name': 'minimax', 'api_key': 'fake_minimax_key',
           'base_url': 'https://minimax.test/anthropic/v1', 'model': 'MiniMax-M3'}]

    result = srv._proxy_chain_forward(
        {"model": "moonshot-v1-32k", "max_tokens": 16,
         "messages": [{"role": "user", "content": "ping"}]},
        request_format="anthropic", is_streaming=False,
        write_head=write_head, write_chunk=write_chunk,
        target_path="/v1/messages",
        primary={"name": "kimi", "url": "https://kimi.test/v1/messages"},
        primary_key=["fake_kimi_key_1", "fake_kimi_key_2"],
        key_pool=pool,
        fallback_providers=fb, log_prefix="TestQuota429",
    )
    return result, written, calls, pool


# ── ① 真实额度 body ⇒ 必须判 fallback ────────────────────────────────────
def test_real_quota_429_body_is_fallback(srv):
    """★ 生产实测的额度型 body 必须被识别成额度耗尽 ⇒ 直接降级 provider。"""
    pa = srv.padapters
    assert pa.is_quota_exhausted(REAL_QUOTA_429_BODY) is True, \
        "真实 Kimi 额度型 body 必须被识别成额度耗尽（生产日志 2026-10-27 03:00 实测）"
    assert pa.classify_upstream_status(429, REAL_QUOTA_429_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "额度耗尽型 429 应判为直接降级 provider（换 key 对共享额度无效）"


# ── ② ★ type 字段免疫：额度型不得被 "type":"rate_limit_error" 误杀 ──────
def test_quota_429_is_not_masked_by_rate_limit_error_type(srv):
    """★★ Kimi 把「额度耗尽」也标成 `"type":"rate_limit_error"`
    ⇒ **type 字段不能当语义证据** ⇒ 额度语义必须在 message 里被认出来。

    ⇒ 这条正是 A 边 §78 的实现漏掉的那一半（它的速率特征表含 'rate_limit'
    ⇒ 命中 type 字段 ⇒ 真实额度 body 被当成限流 ⇒ 逐把试 key 照旧）。
    """
    pa = srv.padapters
    body = REAL_QUOTA_429_BODY
    assert '"type":"rate_limit_error"' in body, "样本前提变了：本条测的就是 type 字段污染"
    assert pa.classify_upstream_status(429, body) == pa.ERROR_ACTION_FALLBACK, \
        "额度语义在 message 里 ⇒ 不能被 type 字段的 rate_limit_error 挡掉"


# ── ③ 对照：普通限流 ⇒ 仍判 rotate_key（行为不变，零回归）──────────────
def test_plain_rate_limit_429_still_rotates_key(srv):
    pa = srv.padapters
    assert pa.is_quota_exhausted(PLAIN_429_BODY) is False, \
        "普通限流不能被误判成额度耗尽"
    assert pa.classify_upstream_status(429, PLAIN_429_BODY) == pa.ERROR_ACTION_ROTATE_KEY, \
        "普通限流必须仍走「换 key」路径（保守取向不变）"


# ── ④ 对照：A 边 §78 的构造样本 ⇒ 仍判 fallback（零破坏证明）────────────
def test_a_side_constructed_sample_still_fallback(srv):
    """★ 我的特征表改动**不得**破坏 A 边 §78 已有的判据。"""
    pa = srv.padapters
    assert pa.classify_upstream_status(429, A_SIDE_CONSTRUCTED_QUOTA_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "A 边第 ⑧ 组的构造样本必须仍判 fallback（我的改动不能破坏他的判据）"


# ── ⑤ 契约：不传 body ⇒ 与历史完全一致（其它 5 条管线零影响）───────────
def test_without_body_backward_compatible(srv):
    pa = srv.padapters
    assert pa.classify_upstream_status(429) == pa.ERROR_ACTION_ROTATE_KEY
    assert pa.classify_upstream_status(401) == pa.ERROR_ACTION_ROTATE_KEY
    assert pa.classify_upstream_status(402) == pa.ERROR_ACTION_FALLBACK
    assert pa.classify_upstream_status(400) == pa.ERROR_ACTION_CLIENT
    assert pa.classify_upstream_status(404) == pa.ERROR_ACTION_CLIENT


# ── ⑥ 链级（★ 决定性）：真实额度 body ⇒ 不逐把试 key ───────────────────
def test_real_quota_429_does_not_walk_the_key_pool(srv, monkeypatch):
    """真实额度 body ⇒ **kimi 只被调 1 次** ⇒ 直接落 minimax 200。"""
    result, written, calls, pool = _run_chain(srv, monkeypatch, REAL_QUOTA_429_BODY.encode("utf-8"))

    assert calls["kimi"] == 1, \
        "额度耗尽不该逐把试 key（kimi 实调 %d 次）" % calls["kimi"]
    assert result["ok"] is True, "应降级成功，实得 %r" % (result,)
    assert result["provider"] == "minimax", "应降级到 minimax，实得 %r" % (result["provider"],)
    assert result["fallback_from"] == "kimi", \
        "fallback_from 应记为 kimi，实得 %r" % (result["fallback_from"],)
    assert written["head"][0] == 200, "对调用方应写出 200，实得 %r" % (written["head"],)
    assert calls["minimax"] == 1, "minimax 应被调用 1 次，实得 %d" % calls["minimax"]


# ── ⑦ 链级对照：普通限流 ⇒ 仍走完 3 把 key（与 ⑥ 只差 body）────────────
def test_plain_429_still_walks_the_key_pool(srv, monkeypatch):
    """★ 判别精确：同一套 mock、只差 body ⇒ 普通限流仍逐把试（kimi 3 次）。"""
    result, written, calls, pool = _run_chain(srv, monkeypatch, PLAIN_429_BODY.encode("utf-8"))

    assert calls["kimi"] == 3, \
        "普通限流应逐把试完 3 把 key（2 主 + 1 池），kimi 实调 %d 次" % calls["kimi"]
    assert result["ok"] is True, "最终仍应降级成功，实得 %r" % (result,)
    assert result["provider"] == "minimax"


# ── ⑧ 敏感度守卫：把分类器改坏 ⇒ ⑥ 立刻不成立 ─────────────────────────
def test_quota_429_assertion_is_sensitive_to_classifier(srv, monkeypatch):
    """证明 ⑥ 的绿灯来自**分类机制**，而不是碰巧。"""
    padapters = srv.padapters
    orig = padapters.classify_upstream_status
    monkeypatch.setattr(
        padapters, "classify_upstream_status",
        lambda status, body_text='': padapters.ERROR_ACTION_ROTATE_KEY,  # 一律当限流
    )
    try:
        result, written, calls, pool = _run_chain(srv, monkeypatch, REAL_QUOTA_429_BODY.encode("utf-8"))
        assert calls["kimi"] == 3, \
            "分类改坏后额度型 429 应退回逐把试 key（kimi 3 次），实调 %d 次" % calls["kimi"]
    finally:
        monkeypatch.setattr(padapters, "classify_upstream_status", orig)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
