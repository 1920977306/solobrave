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


def _run_chain(srv, monkeypatch, kimi_body: bytes, kimi_status: int = 429):
    """跑一次真链：主 = kimi（**3 把 key**：2 主 + 1 池），兜底 = minimax。

    ★ 3 把 key 是刻意的：只有 key 池 >1 时，「逐把试」与「不逐把试」才**分得开**
      （A 边 §78 的第 ⑧ 组用的是单把 key ⇒ 测不出这一点）。
    ★ `kimi_status` 参数化（A 边 16:1x 补）：生产主形态是 **403**（1013 次）
      ⇒ 需要能在同一台架上复现 **403 + permission_error** 这条路径。
    """
    calls = {"kimi": 0, "minimax": 0}
    pool = _FakeKeyPool(["fake_pool_key_1"])

    def fake(req, timeout=None, context=None):
        url = req.full_url
        if "kimi.test" in url:
            calls["kimi"] += 1
            raise _http_error(url, kimi_status, kimi_body)
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


# ══════════════════════════════════════════════════════════════════════════
# ⑨ ★★★ 生产**主路径**覆盖（2026-10-27 16:1x，A 边独立复核时发现）
#
# ★ 为什么必须补：上面 8 条用的真实 body 是 **`type:"rate_limit_error"` 变体**
#   ⇒ 而**生产日志统计**（`server.log` 逐字 grep，A 边 16:1x 实测）显示：
#       provider=kimi HTTP 403  → **1013 次**
#       provider=kimi HTTP 401  →   785 次
#       provider=kimi HTTP 429  →   119 次
#       "permission_error"      → **1075 次**
#   ⇒ ⇒ **真实现场的主形态是 `type:"permission_error"` + HTTP 403**（不是 429）
#   ⇒ ⇒ 而 403 本来就在 `classify_upstream_status` 的 FALLBACK 状态码表里
#   ⇒ ⇒ ⇒ **上一组测试覆盖不到生产主路径** ⇒ 「全绿」不代表主路径被测过。
#
# ★ 这条与上一组的关系（不是重复）：
#   上一组测「**429 + rate_limit_error**」⇒ 走 **body 判据**分支
#   本条测「**403 + permission_error**」⇒ 走 **状态码表**分支（body 判据是双保险）
#   ⇒ **两条分支都得绿**，否则只堵住了变体、漏了主形态。
#
# ★ 附带的**冗余守卫**（本组第 ⑩ 条）：
#   真实文案在特征表里有 **3 条冗余标记**（'monthly usage limit' /
#   'quota will be refreshed' / 'purchase extra usage'，**同批加入于 335e921**）
#   ⇒ **只打坏其中一条，测试仍然全绿**（实测：A 边 16:1x 单条变异 ⇒ 8 passed）
#   ⇒ ⇒ 「8 条全绿」**无法证明这组标记整体是活的** ⇒ 补第 ⑩ 条守它
#   ⇒ ⇒ **这是本项目第 5 次「假绿」形态的变种：冗余特征表 ⇒ 掩盖单点失效**。
#   ⇒ ⇒ ★ 注意：单条冗余是**刻意设计**（同一句文案拆三段 ⇒ 任一段被上游改写都有兜底）
#     ⇒ ⇒ 第 ⑩ 条断言的是「**整体必需**」，**不是**「每条单独必需」。
# ══════════════════════════════════════════════════════════════════════════

# ★ 生产日志逐字提取的 **403 + permission_error** 形态（2026-10-27 03:00，`server.log`）
REAL_QUOTA_403_BODY = (
    '{"error":{"type":"permission_error","message":"You\'ve reached your monthly usage '
    'limit for this billing cycle. Your quota will be refreshed in the next cycle. To '
    'continue now, purchase extra usage or upgrade your plan: '
    'https://www.kimi.com/membership/subscription?tab=quota"},"type":"error"}'
)


# ── ⑨ 生产主路径：403 + permission_error ⇒ 必须降级（不停）────────────────
def test_real_quota_403_permission_error_is_fallback(srv):
    """★★ 生产**主形态**（403 + permission_error，1075/1013 次）⇒ 必须判降级。

    ★ 为什么要单列：403 不在 body 判据的触发条件里（`classify_upstream_status`
      只在 **status == 429** 时才问 body）⇒ 403 靠**状态码表**兜底
      ⇒ ⇒ **body 判据对 403 是「双保险」而不是「唯一路径」**
      ⇒ ⇒ 那就必须两条都测，否则只测 429 变体 = **主形态没被测过**。
    """
    pa = srv.padapters
    assert '"type":"permission_error"' in REAL_QUOTA_403_BODY, "样本前提变了：应带 permission_error"
    # ① 状态码路径：403 ⇒ FALLBACK（无论 body 是什么）
    assert pa.classify_upstream_status(403, REAL_QUOTA_403_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "生产主形态 403 必须判降级 provider（换 key 对共享额度无效）"
    assert pa.classify_upstream_status(403) == pa.ERROR_ACTION_FALLBACK, \
        "403 即便不带 body 也必须降级（状态码表兜底）"
    # ② body 判据路径：额度语义仍应被认出来（双保险，不能因 403 就失效）
    assert pa.is_quota_exhausted(REAL_QUOTA_403_BODY) is True, \
        "403 形态的额度文案也必须被识别成额度耗尽（body 判据是双保险）"
    # ③ 同一条 body 若状态码是 429 ⇒ 仍必须降级（保证「429 + permission_error」也不停）
    assert pa.classify_upstream_status(429, REAL_QUOTA_403_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "同一条真实 body 配 429 也必须降级"


# ── ⑨-b ★ 链级（生产主路径）：403 + permission_error ⇒ 不逐把试 key，直落 minimax ──
def test_real_quota_403_chain_does_not_walk_key_pool(srv, monkeypatch):
    """★★ 生产**主形态**（403 + permission_error，1013 次）在**链级**也必须降级。

    ★ 为什么链级也要单独测（不是重复 ⑨ 的判据级）：
      判据级只验「分类器说 fallback」⇒ 但链里还有**别的分支**可能绕开它
      ⇒ 必须真跑一次链 ⇒ 断言 **kimi 只被调 1 次** + **落到 minimax 200**
      ⇒ 这才是老大 15:3x 问的「**会不会跟着停**」的直接答案。
    """
    result, written, calls, pool = _run_chain(
        srv, monkeypatch, REAL_QUOTA_403_BODY.encode("utf-8"), kimi_status=403)

    assert calls["kimi"] == 1, \
        "403 额度耗尽不该逐把试 key（kimi 实调 %d 次）" % calls["kimi"]
    assert result["ok"] is True, "应降级成功（不停服），实得 %r" % (result,)
    assert result["provider"] == "minimax", "应降级到 minimax，实得 %r" % (result["provider"],)
    assert result["fallback_from"] == "kimi", \
        "fallback_from 应记为 kimi，实得 %r" % (result["fallback_from"],)
    assert written["head"][0] == 200, "对调用方应写出 200，实得 %r" % (written["head"],)
    assert calls["minimax"] == 1, "minimax 应被调用 1 次，实得 %d" % calls["minimax"]


# ── ⑩ ★ 冗余特征表守卫：**整体必需** + **单条冗余是刻意设计** ───────────
def test_real_body_markers_are_required_as_a_group(srv, monkeypatch):
    """★★ 证明真实文案的 **3 条标记合起来是必需的**，且**单条冗余是刻意设计**。

    ★ 实测背景（A 边 16:1x）：把 'monthly usage limit' 单独改坏 ⇒
      `tests/test_quota_429_real_body.py` **8 passed**（全绿！）
      ⇒ 因为 'quota will be refreshed' / 'purchase extra usage' 兜住了
      ⇒ ⇒ 「8 条全绿」**证明不了**这组标记整体是活的（⇒ 假绿）

    ★ 三条标记的来源：`335e921`（B 边 §80）**同批加入** ⇒ 是**刻意冗余**
      （同一句真实文案拆成三段 ⇒ 任何一段被上游改写都还有兜底）
      ⇒ ⇒ **单条打坏必须仍绿**（这正是冗余的价值）
      ⇒ ⇒ **全部打坏必须变红**（证明这组标记整体是活的，不是死代码）

    ★ 纪律：断言必须写清「打坏什么 ⇒ 期望什么」，不能只写「rc≠0」
    """
    pa = srv.padapters
    markers = ("monthly usage limit", "quota will be refreshed", "purchase extra usage")
    orig_markers = pa._QUOTA_BODY_MARKERS

    # ① 单条打坏 ⇒ **仍应判额度耗尽**（冗余生效 ⇒ 这是刻意设计，不是漏洞）
    for m in markers:
        mutated = tuple(("__MUTATED__" + x) if x == m else x for x in orig_markers)
        monkeypatch.setattr(pa, "_QUOTA_BODY_MARKERS", mutated)
        try:
            assert pa.is_quota_exhausted(REAL_QUOTA_429_BODY) is True, \
                ("特征 %r 单独打坏后应仍判额度耗尽（3 条标记是**刻意冗余**，同批加入于 335e921）"
                 "⇒ 若这里红了，说明冗余被破坏" % m)
        finally:
            monkeypatch.setattr(pa, "_QUOTA_BODY_MARKERS", orig_markers)

    # ② **全部打坏** ⇒ 必须变红（证明这组标记整体是活的 ⇒ 堵住「全绿但无覆盖」）
    all_mutated = tuple(("__MUTATED__" + x) if x in markers else x for x in orig_markers)
    monkeypatch.setattr(pa, "_QUOTA_BODY_MARKERS", all_mutated)
    try:
        assert pa.is_quota_exhausted(REAL_QUOTA_429_BODY) is False, \
            "3 条真实文案标记全被打坏后，真实额度 body 仍被判成额度耗尽 ⇒ 这组标记**整体是死的**"
        assert pa.classify_upstream_status(429, REAL_QUOTA_429_BODY) == pa.ERROR_ACTION_ROTATE_KEY, \
            "3 条标记全打坏后应退回 rotate_key（逐把试 key），实得 %r" \
            % (pa.classify_upstream_status(429, REAL_QUOTA_429_BODY),)
    finally:
        monkeypatch.setattr(pa, "_QUOTA_BODY_MARKERS", orig_markers)

    # ★ 守卫自身自检：全部还原后 ⇒ 必须仍判 fallback（证明上面的「变红」不是恒真）
    assert pa.is_quota_exhausted(REAL_QUOTA_429_BODY) is True, "守卫自检失败：还原后应判额度耗尽"
    assert pa.classify_upstream_status(429, REAL_QUOTA_429_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "守卫自检失败：还原后应判 fallback"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v", "--no-header", "-p", "no:cacheprovider"]))
