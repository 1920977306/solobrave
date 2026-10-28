# -*- coding: utf-8 -*-
"""图片识别链路 + 专属佣金闭环 守卫（2026-10-28）。

覆盖的线上实案：
  ① lark SDK 的 resp.file 是 BytesIO 不是 bytes ⇒ handler.write_bytes(BytesIO)
     TypeError ⇒ **所有图片消息 100% 下载失败**（15:28 实案，此路径从未被测过）。
  ② 「图片+一句话」在飞书是 post 消息，_dispatch 只认 image/text ⇒ 掉兜底话术。
  ③ 公开佣金 ≠ 专属佣金（商家单独给达人，平台接口查不到，老大 16:11 拍板）：
     结论因佣金低被压时必须问达人；达人报数后按专属佣金重算一版。

★ 每条守卫都按 §16 Y 第 4 条执行过「能不能红」的自检（注释里标了对照）。
"""

import base64
import importlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

TK = "tk_img_test"
PRODUCT_KEY = "prod_test_1"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    """隔离环境（照抄 tests/test_product_intent.py 的口径）：
    DB / 图片目录 / master key 全部落 tmp，不碰 data/。"""
    monkeypatch.setenv("DAREN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAREN_DB", str(tmp_path / "daren_bot.db"))
    monkeypatch.delenv("DAREN_USE_KIMI", raising=False)
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    import daren_bot.worker as wk
    for m in (cfg, ddb, hd, ex, wk):
        importlib.reload(m)
    ddb.init_db()
    ddb.grant(TK, 1000)
    return {"db": ddb, "handler": hd, "executors": ex, "worker": wk,
            "config": cfg}


class _FakeSender:
    def __init__(self, image_bytes: bytes = b""):
        self.sent = []
        self.image_bytes = image_bytes or b"\xff\xd8\xff" + b"fake-jpeg" * 100

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    def download_image(self, message_id, image_key):
        return self.image_bytes

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _router(handler, sender):
    class _WM:
        def __init__(self, s):
            self.s = s

        def get_sender(self, tenant_key):
            return self.s

    return handler.Router(_WM(sender)), sender


# ---------------- ① BytesIO 归一（下载图片 100% 失败的实案） ----------------

def _install_fake_lark(monkeypatch):
    """注入最小假 lark_oapi（本机 /usr/bin/python3 没装 SDK），
    让 ws_manager 可导入 —— 测的是 download_image 的归一逻辑，不是 SDK。"""
    import sys
    import types

    class _ReqBuilder:
        def message_id(self, x): return self
        def file_key(self, x): return self
        def type(self, x): return self
        def build(self): return self

    class _GetMessageResourceRequest:
        @staticmethod
        def builder():
            return _ReqBuilder()

    lark = types.ModuleType("lark_oapi")
    ws_mod = types.ModuleType("lark_oapi.ws")
    client_mod = types.ModuleType("lark_oapi.ws.client")
    api_mod = types.ModuleType("lark_oapi.api")
    im_mod = types.ModuleType("lark_oapi.api.im")
    v1_mod = types.ModuleType("lark_oapi.api.im.v1")
    v1_mod.GetMessageResourceRequest = _GetMessageResourceRequest
    ws_mod.client = client_mod
    lark.ws = ws_mod
    monkeypatch.setitem(sys.modules, "lark_oapi", lark)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws", ws_mod)
    monkeypatch.setitem(sys.modules, "lark_oapi.ws.client", client_mod)
    monkeypatch.setitem(sys.modules, "lark_oapi.api", api_mod)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.im", im_mod)
    monkeypatch.setitem(sys.modules, "lark_oapi.api.im.v1", v1_mod)


class TestDownloadImageBytesIO:
    def test_bytesio_returned_as_bytes(self, bot, monkeypatch):
        """SDK 返回 BytesIO 时 download_image 必须归一成 bytes（对照：删掉
        hasattr(data,'read') 分支后本用例以 TypeError 失败 = 能红）。"""
        _install_fake_lark(monkeypatch)
        from daren_bot.ws_manager import Sender
        payload = b"\xff\xd8\xff" + os.urandom(64)
        fake_resp = SimpleNamespace(
            success=lambda: True,
            file=io.BytesIO(payload),   # ← 线上实案：SDK 给的是 BytesIO
        )

        class _Resource:
            @staticmethod
            def get(req):
                return fake_resp

        class _V1:
            message_resource = _Resource

        class _Im:
            v1 = _V1

        class _Client:
            im = _Im()

        s = object.__new__(Sender)   # 绕过 __init__（不需要真 ws 连接）
        s.client = _Client()
        out = s.download_image("om_x", "img_x")
        assert isinstance(out, bytes)
        assert out == payload

    def test_plain_bytes_passthrough(self, bot, monkeypatch):
        """SDK 若某版本真返回 bytes，也不能被归一逻辑弄坏。"""
        _install_fake_lark(monkeypatch)
        from daren_bot.ws_manager import Sender
        payload = b"\xff\xd8\xff" + os.urandom(32)
        fake_resp = SimpleNamespace(success=lambda: True, file=payload)

        class _Resource:
            @staticmethod
            def get(req):
                return fake_resp

        class _V1:
            message_resource = _Resource

        class _Im:
            v1 = _V1

        class _Client:
            im = _Im()

        s = object.__new__(Sender)
        s.client = _Client()
        assert s.download_image("om_x", "img_x") == payload


# ---------------- ② post 消息解析（图+一句话此前掉兜底） ----------------

class TestParsePost:
    def test_text_plus_image(self, bot):
        hd = bot["handler"]
        content = {
            "title": "",
            "content": [
                [{"tag": "text", "text": "帮我看看这个品"}],
                [{"tag": "img", "image_key": "img_abc"}],
            ],
        }
        caption, keys = hd._parse_post(content)
        assert caption == "帮我看看这个品"
        assert keys == ["img_abc"]

    def test_title_and_link(self, bot):
        hd = bot["handler"]
        content = {
            "title": "这个能带吗",
            "content": [[{"tag": "a", "text": "商品链接",
                          "href": "https://example.com/p?id=1"}]],
        }
        caption, keys = hd._parse_post(content)
        assert "这个能带吗" in caption
        assert "商品链接" in caption
        assert keys == []

    def test_empty_content(self, bot):
        hd = bot["handler"]
        assert hd._parse_post({}) == ("", [])

    def test_multi_image_takes_first(self, bot):
        hd = bot["handler"]
        content = {"content": [[
            {"tag": "img", "image_key": "img_1"},
            {"tag": "img", "image_key": "img_2"},
        ]]}
        _, keys = hd._parse_post(content)
        assert keys == ["img_1", "img_2"]


# ---------------- ②b _route_image 带 caption 入队 ----------------

class TestRouteImageCaption:
    def test_caption_goes_into_payload(self, bot, monkeypatch):
        """post（图+话）进来的 caption 必须随 payload 交给 vision 链
        （对照：payload 回退成空 ⇒ 断言红）。"""
        hd = bot["handler"]
        ddb = bot["db"]
        sender = _FakeSender()
        router, s = _router(hd, sender)

        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        msg = SimpleNamespace(chat_id="chat_1", message_id="om_1")
        router._route_image(tenant, msg, {"image_key": "img_k"}, s,
                            caption="帮我看看这个品佣金多少")
        assert seen, "图片任务没有入队"
        a, kw = seen[0]
        assert a[1] == "image" and a[4] == "vision", f"action 口径错了：{a!r}"
        assert json.loads(kw["payload"])["caption"] == "帮我看看这个品佣金多少"

    def test_no_caption_keeps_legacy_empty_payload(self, bot, monkeypatch):
        hd = bot["handler"]
        ddb = bot["db"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        msg = SimpleNamespace(chat_id="chat_1", message_id="om_2")
        router._route_image(tenant, msg, {"image_key": "img_k2"}, s)
        a, kw = seen[0]
        assert a[4] == "vision"
        assert kw.get("payload", "") == ""

    def test_post_dispatch_carries_caption(self, bot, monkeypatch):
        """端到端：post 消息（图+一句话）走 _dispatch 分流，
        caption 必须最终落进 vision 任务 payload
        （对照：_dispatch 里不传 caption ⇒ 本用例红）。"""
        hd = bot["handler"]
        ddb = bot["db"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        content = json.dumps({
            "title": "",
            "content": [
                [{"tag": "text", "text": "帮我看看这个品"}],
                [{"tag": "img", "image_key": "img_post1"}],
            ],
        }, ensure_ascii=False)
        data = SimpleNamespace(
            event=SimpleNamespace(
                message=SimpleNamespace(
                    chat_id="chat_1", message_type="post", content=content,
                    message_id="om_post1"),
                sender=SimpleNamespace(
                    sender_id=SimpleNamespace(open_id="ou_test")),
            ))
        router._dispatch(tenant, data)
        assert seen, "post 消息没有入队任何任务（分流掉了？）"
        a, kw = seen[0]
        assert a[1] == "image"
        assert json.loads(kw["payload"])["caption"] == "帮我看看这个品"


# ---------------- ③ 专属佣金闭环 ----------------

class TestCommissionRecalc:
    def _pending_stats(self):
        return {"name": "测试商品", "commission_rate": "5%",
                "price": "99", "monthly_sales": 1000}

    def test_commission_executor_registered(self, bot):
        assert "commission" in bot["worker"].EXECUTORS

    def test_with_pending_enqueues_and_clears(self, bot, monkeypatch):
        """有 pending + 达人报数 ⇒ 入队 commission 类型并清掉 pending
        （对照：_route_commission 返回 False ⇒ 走到 QA ⇒ 断言红）。"""
        hd = bot["handler"]
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self._pending_stats())
        sender = _FakeSender()
        router, s = _router(hd, sender)
        enq, _ded = _spy_charge(bot, monkeypatch)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        assert router._route_commission(tenant, "chat_1", "专属佣金20%", sender) is True
        assert any(a[1] == "commission" for a in enq), \
            f"没有入队 commission 任务：{enq!r}"
        assert ddb.get_commission_pending(TK) is None
        assert "20%" in s.text

    def test_rate_passed_in_payload(self, bot, monkeypatch):
        hd = bot["handler"]
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self._pending_stats())
        sender = _FakeSender()
        router, s = _router(hd, sender)
        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        assert router._route_commission(tenant, "chat_1", "20%", sender) is True
        a, kw = seen[0]
        assert a[1] == "commission" and a[4] == "commission"
        payload = json.loads(kw["payload"])
        assert payload["rate"] == 20.0
        assert payload["stats"]["name"] == "测试商品"

    def test_no_pending_not_intercepted(self, bot, monkeypatch):
        """没有 pending 时，带百分号的普通问答**不许**被佣金路由抢走
        （对照：删掉 pending 判据 ⇒ 本用例红）。"""
        hd = bot["handler"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        # 「佣金是怎么算的」—— 含佣字但无数字；「毛利率有20%吗」—— 有数字但无 pending
        assert router._route_commission(tenant, "chat_1", "毛利率有20%吗", sender) is False
        assert router._route_commission(tenant, "chat_1", "佣金是怎么算的", sender) is False

    def test_long_text_without_keyword_not_intercepted(self, bot, monkeypatch):
        """有 pending 也要防误吃：长句、不含「佣」字的百分号文本 ⇒ 不拦截
        （判据收紧：含佣字 或 ≤12 字）。"""
        hd = bot["handler"]
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self._pending_stats())
        sender = _FakeSender()
        router, s = _router(hd, sender)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        long_text = ("我大概看了下这个品最近三十天的动销数据转化率应该在20%上下浮动"
                     "你觉得这个水平算高还是算低")
        assert router._route_commission(tenant, "chat_1", long_text, sender) is False
        # pending 没被清掉（没被误吃）
        assert ddb.get_commission_pending(TK) is not None

    def test_out_of_range_rate_rejected(self, bot):
        hd = bot["handler"]
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self._pending_stats())
        sender = _FakeSender()
        router, s = _router(hd, sender)
        tenant = {"tenant_key": TK, "bind_douyin_id": ""}
        assert router._route_commission(tenant, "chat_1", "佣金200%", sender) is False
        assert router._route_commission(tenant, "chat_1", "佣金0%", sender) is False

    def test_commission_prompt_mentions_exclusive(self, bot):
        """结论 prompt 必须含「专属佣金」引导话术（老大 16:11 口径：
        公开佣金低 ≠ 不能带，专属佣金只有达人后台可见）。"""
        ex = bot["executors"]
        assert "专属佣金" in ex.PRODUCT_VERDICT_PROMPT
        assert "重新算一版" in ex.PRODUCT_VERDICT_PROMPT

    def test_pending_ttl_and_overwrite(self, bot):
        """pending 24h TTL + 同租户覆盖（第二次分析的商品顶掉第一次）。"""
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, {"name": "A"})
        ddb.set_commission_pending(TK, "prod_second", {"name": "B"})
        got = ddb.get_commission_pending(TK)
        assert got["product_key"] == "prod_second"
        assert got["stats"]["name"] == "B"
        ddb.clear_commission_pending(TK)
        assert ddb.get_commission_pending(TK) is None


# ---------------- ⑤ 截图问答：Markdown 原文通道（JSON 解析 bug 回归） ----------------

class TestDashboardQA:
    def test_markdown_answer_returned_verbatim(self, bot, monkeypatch):
        """截图问答的输出是 Markdown 自由文本，必须原文返回 —— 走 JSON 字段
        解析的老实现会把 markdown 解析成 {} 误判「empty output」
        （2026-10-28 自测实案：minimax-cp 明明答了却报没看懂）。
        对照：把 _vision_dashboard_qa 改回调 _vision_try ⇒ 本用例红。"""
        ex = bot["executors"]
        md = "## 截图里有什么\n- GMV 12,345（照抄）\n## 解读\n- 环比上涨"
        monkeypatch.setattr(ex, "_vision_rings",
                            lambda: [{"base": "http://x", "key": "k",
                                      "model": "m", "temp": 0.1,
                                      "label": "fake"}])
        monkeypatch.setattr(ex, "_vision_call", lambda *a, **kw: md)
        out = ex._vision_dashboard_qa("aGVsbG8=", "帮我看看这张图")
        assert out == md.strip()

    def test_caption_becomes_question(self, bot, monkeypatch):
        """达人随图发的那句话必须拼进 prompt 当问题。"""
        ex = bot["executors"]
        captured = {}
        monkeypatch.setattr(ex, "_vision_rings",
                            lambda: [{"base": "http://x", "key": "k",
                                      "model": "m", "temp": 0.1,
                                      "label": "fake"}])

        def fake_call(base, key, model, b64, temp, prompt):
            captured["prompt"] = prompt
            return "ok"

        monkeypatch.setattr(ex, "_vision_call", fake_call)
        ex._vision_dashboard_qa("aGVsbG8=", "这个品佣金多少")
        assert "这个品佣金多少" in captured["prompt"]


def _spy_charge(bot, monkeypatch):
    """照抄 test_product_intent._spy 口径：盯原子入口 enqueue_task_charged。"""
    db = bot["db"]
    enq = []
    orig = db.enqueue_task_charged

    def spy(*a, **kw):
        enq.append(a)
        return orig(*a, **kw)

    monkeypatch.setattr(db, "enqueue_task_charged", spy)
    return enq, None


# ---------------- ④ 大图降采样（算力成本控制） ----------------

class TestImgB64:
    def test_big_image_shrinks(self, bot, tmp_path):
        """2000×2000 的图压到长边 1280 内，像素与体积都必须显著变小。

        （用随机噪声图：纯色 PNG 对大片同色压缩极好，体积断言会失真）。"""
        pytest.importorskip("PIL")
        import random
        from PIL import Image
        ex = bot["executors"]
        rng = random.Random(7)
        p = tmp_path / "big.png"
        im_src = Image.new("RGB", (2000, 2000))
        im_src.putdata([(rng.randrange(256), rng.randrange(256),
                         rng.randrange(256)) for _ in range(2000 * 2000)])
        im_src.save(p)
        raw = p.read_bytes()
        b64 = ex._img_b64(p)
        assert len(base64.b64decode(b64)) < len(raw) / 2

        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert max(im.size) <= ex._IMG_MAX_EDGE

    def test_small_image_passes_through_shape(self, bot, tmp_path):
        pytest.importorskip("PIL")
        from PIL import Image
        ex = bot["executors"]
        p = tmp_path / "small.png"
        Image.new("RGB", (80, 60), (10, 200, 30)).save(p)
        b64 = ex._img_b64(p)
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        assert im.size == (80, 60)

    def test_non_image_bytes_fallback(self, bot, tmp_path):
        """PIL 打不开的内容回退原始字节（链路不因此断）。"""
        ex = bot["executors"]
        p = tmp_path / "junk.jpg"
        junk = b"\xff\xd8\xff" + os.urandom(256)
        p.write_bytes(junk)
        assert base64.b64decode(ex._img_b64(p)) == junk
