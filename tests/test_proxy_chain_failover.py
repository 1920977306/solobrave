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


def _http_error(url, code, msg="err", body=None):
    payload = body if body is not None else b'{"error":{"code":"x","message":"y"}}'
    return urllib.error.HTTPError(url, code, msg, {}, io.BytesIO(payload))


def _patch_urlopen(monkeypatch, srv, kimi_status, minimax_status=200, kimi_body=None):
    """按 URL 片段分流：kimi → 指定状态码（可带真实 body）；minimax → 200。"""
    calls = {"kimi": 0, "minimax": 0}

    def fake(req, timeout=None, context=None):
        url = req.full_url
        if "kimi.test" in url:
            calls["kimi"] += 1
            if kimi_status == 200:
                return _FakeResp(_anthropic_ok(model="moonshot-v1-32k"))
            raise _http_error(url, kimi_status, body=kimi_body)
        if "minimax.test" in url:
            calls["minimax"] += 1
            if minimax_status == 200:
                return _FakeResp(_anthropic_ok())
            raise _http_error(url, minimax_status)
        raise AssertionError("意外的上游 URL: %s" % url)

    monkeypatch.setattr(srv.urllib.request, "urlopen", fake)
    return calls


def _run_chain(srv, monkeypatch, kimi_status, fallback=True, request_format="anthropic",
               kimi_body=None, primary_key="fake_kimi_key_1"):
    """跑一次真链：主 = kimi，兜底 = minimax（可选）。"""
    calls = _patch_urlopen(monkeypatch, srv, kimi_status, kimi_body=kimi_body)
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
        primary_key=primary_key, key_pool=None,
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
        # ★ 注意签名：分类器现在收 (status, body_text) ⇒ 补丁也要收两个参数
        #   （收一个会让链里 TypeError ⇒ 被当成网络错误 ⇒ 反而触发降级 ⇒ 断言会假红）
        lambda status, body_text='': padapters.ERROR_ACTION_CLIENT,
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


# ── ⑧ 429 的两种语义（2026-10-27 A 边 §78：B 边 §72 提的「真缺口」落地）────
#   429 在不同上游含义不同：速率限制（换 key 可能有效）vs 额度耗尽（换 key 无效）
#
# ⚠️⚠️ **A 边 §81 更正**：下面这个 `KIMI_QUOTA_BODY` 是**构造样本**（`code` 字段 + 中文文案）
#   ⇒ 与**真实现场**（`type` 字段 + 英文文案）**不同构** ⇒ 它当年绿了，但真实现场是红的
#   ⇒ 真实现场的回归基线在 **`tests/test_quota_429_real_body.py`**（B 边 §80 建）
#   ⇒ 这里**保留**构造样本：它是「两条判据同时成立」的回归基线
#     （B 边的 `test_a_side_constructed_sample_still_fallback` 也钉了它 ⇒ 两边都不许删）
#
# ⚠️⚠️⚠️ **别被 `"\u6708"` 骗了**（A 边 §81 实测，pytest 的 DeprecationWarning 引出来的）：
#   `b'...\u6708...'` 在 **bytes 字面量**里是**无效转义** ⇒ Python 原样保留
#   ⇒ 里面存的是**字面 6 个字符** `\u6708`，**不是汉字「月」**！
#   ⇒ ⇒ 所以这条样本命中的是 **`code` 字段**的 `exceeded_current_quota` 特征，
#      **并没有**命中「额度已用尽」等**中文文案**特征。
#   ⇒ ⇒ **故意保持原样**（实测：把它换成真中文 ⇒ 两条路径同时命中 ⇒
#      中文特征坏掉时它仍然绿 ⇒ **敏感度反而下降**）⇒ 只加注释说明，不改字节。
KIMI_QUOTA_BODY = (b'{"error":{"code":"exceeded_current_quota_error",'
                   b'"message":"\u6708\u8ba1\u8d39\u5468\u671f\u989d\u5ea6\u5df2\u7528\u5c3d"}}')
KIMI_RATE_BODY = (b'{"error":{"code":"rate_limit_exceeded",'
                  b'"message":"too many requests, please retry later"}}')

# ★★★ 真实现场的**第二变体**（A 边 §81：从生产 `server.log` 逐字提取，2026-10-27）
#   ⚠️ 只含上游错误文案，不含任何密钥。
#   ⇒ 同一条「monthly usage limit」文案，在现场**既以 429 出现、也以 403 出现**
#   ⇒ 本条是 **403 + `type: permission_error`** 变体。
REAL_KIMI_403_BODY = (
    '{"error":{"type":"permission_error","message":"You\'ve reached your monthly usage '
    'limit for this billing cycle. Your quota will be refreshed in the next cycle. To '
    'continue now, purchase extra usage or upgrade your plan: '
    'https://www.kimi.com/membership/subscription?tab=quota"},"type":"error"}'
)

# ★★★ 真实现场的 minimax 套餐 **Token Plan 到顶** body（A 边 §81：server.log 2026-10-26 19:22）
#   ⚠️ `request_id` 已脱敏（其余逐字）—— 它是上游追踪号，**不是凭据**，但不该永久进 git。
REAL_MINIMAX_TOKEN_PLAN_429_BODY = (
    '{"type":"error","error":{"type":"rate_limit_error","message":"\u5df2\u8fbe\u5230 Token '
    'Plan \u7528\u91cf\u4e0a\u9650\uff1a\u8bf7\u5347\u7ea7 Token Plan \u5957\u9910\u6216'
    '\u8d2d\u4e70\u79ef\u5206\u8865\u5145\u7528\u91cf\u3002 (2056)"},"request_id":"<redacted>"}'
)


def test_quota_exhausted_429_is_fallback_not_rotate_key(srv):
    """★ 额度耗尽型 429 ⇒ **直接降级**（换 key 对共享额度无效）。"""
    pa = srv.padapters
    assert pa.classify_upstream_status(429, KIMI_QUOTA_BODY.decode()) == pa.ERROR_ACTION_FALLBACK, \
        "额度耗尽型 429 必须判为 fallback（直接降级 provider）"
    # 智谱那类（余额不足也报 429）也要认
    assert pa.classify_upstream_status(429, '{"code":"1113","message":"\u4f59\u989d\u4e0d\u8db3\u6216\u65e0\u53ef\u7528\u8d44\u6e90\u5305"}') \
        == pa.ERROR_ACTION_FALLBACK


# ★★★ A 边 §81：补两条**真实现场**的断言（B 边 §80 只把它们写进了 docstring，**没写断言**）
#   ⇒ 它们的价值不是「证明现在是对的」（现在确实对），而是**钉住语义，防止有人改回去**。

def test_real_kimi_403_quota_body_is_fallback(srv):
    """★★ 真实现场的**第二变体**：**403 + `permission_error`**（A 边 §81 实测：server.log 100 条）。

    ⇒ 403 本来就在 `classify_upstream_status` 的 FALLBACK 状态码表里 ⇒ **不受 body 判据影响** ✔
    ⇒ ⇒ 那为什么还要断言？
       因为「额度耗尽只会以 429 出现」是个**会过期的假绿**（同款坑第 N 次）：
       现场明明还有 403 变体，若没有这条断言，下一个人查证时会只查 429
       ⇒ 就又会得出「额度耗尽 ⇒ 一定 429」的结论 ⇒ 下一个变体照样漏。
    ⇒ 本条 = **把「现场有几种形态」钉成可执行事实**，而不是靠人记得。
    """
    pa = srv.padapters
    assert pa.classify_upstream_status(403, REAL_KIMI_403_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "真实现场的 403 额度变体必须判为 fallback"
    # 顺带钉住语义：它同时**是**额度耗尽（不是速率限制）
    assert pa.is_quota_exhausted(REAL_KIMI_403_BODY) is True, \
        "403 变体的文案是额度耗尽语义（与 429 变体同一段文案）"


def test_real_minimax_token_plan_429_is_fallback(srv):
    """★★ 真实现场的 minimax 套餐 **Token Plan 到顶**（A 边 §81：server.log 2026-10-26 19:22，15 条）。

    ⇒ 旧判据把它当成「速率限制 ⇒ 维持 rotate_key」（docstring 里还这么写着）
    ⇒ 现已更正为 **FALLBACK**（单 key ⇒ rotate/fallback 等价 ⇒ 不丢能力）
    ⇒ ⇒ 本条断言的作用是**防止那段 docstring 被改回去**：
       「文档说 FALLBACK、代码判 ROTATE_KEY」= 又一次**同名不同物**（本项目复发 N 次的坑）。
    """
    pa = srv.padapters
    assert pa.classify_upstream_status(429, REAL_MINIMAX_TOKEN_PLAN_429_BODY) == pa.ERROR_ACTION_FALLBACK, \
        "minimax 套餐 Token Plan 到顶必须判为 fallback（换 key 对套餐额度无效）"
    assert pa.is_quota_exhausted(REAL_MINIMAX_TOKEN_PLAN_429_BODY) is True


def test_rate_limited_429_still_rotates_key(srv):
    """★ 速率限制型 429 ⇒ **维持换 key**（换一把可能就通了）⇒ 不改变历史行为。"""
    pa = srv.padapters
    assert pa.classify_upstream_status(429, KIMI_RATE_BODY.decode()) == pa.ERROR_ACTION_ROTATE_KEY, \
        "速率限制型 429 必须维持 rotate_key（保守：宁可慢，不要把限流误判成额度耗尽）"


def test_classification_without_body_is_backward_compatible(srv):
    """★ **不传 body** ⇒ 行为与历史完全一致 ⇒ 共用本模块的其它 5 条管线零影响。"""
    pa = srv.padapters
    assert pa.classify_upstream_status(429) == pa.ERROR_ACTION_ROTATE_KEY
    assert pa.classify_upstream_status(401) == pa.ERROR_ACTION_ROTATE_KEY
    assert pa.classify_upstream_status(402) == pa.ERROR_ACTION_FALLBACK
    assert pa.classify_upstream_status(400) == pa.ERROR_ACTION_CLIENT


def test_quota_429_does_not_walk_the_whole_key_pool(srv, monkeypatch):
    """★★ 端到端：额度耗尽型 429 ⇒ **不逐把试 key**，直接落下一 provider。

    ⇒ 这是「省掉 N 次无谓 429 往返」的收益点：**kimi 只被调用 1 次**。
    ⇒ 对照：速率限制型 429 ⇒ 仍走完 key 池（维持历史行为）。
    """
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=429,
                                         kimi_body=KIMI_QUOTA_BODY)
    assert result["ok"] is True
    assert result["provider"] == "minimax", "应直接降级到 minimax，实得 %r" % (result["provider"],)
    assert calls["kimi"] == 1, "额度耗尽不该逐把试 key（kimi 被调 %d 次）" % calls["kimi"]
    assert written["head"][0] == 200


def test_rate_429_still_walks_key_pool(srv, monkeypatch):
    """★ 对照组：速率限制型 429 ⇒ 仍会换 key（行为不变 ⇒ 不误伤）。"""
    result, written, calls = _run_chain(srv, monkeypatch, kimi_status=429,
                                         kimi_body=KIMI_RATE_BODY)
    # 单把 key ⇒ 换 key 一次就走完 ⇒ 仍会降级到 minimax（历史行为）
    assert result["ok"] is True
    assert result["provider"] == "minimax"
