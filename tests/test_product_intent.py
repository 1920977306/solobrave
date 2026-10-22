"""商品意图修复测试 (2026-10-22)。

老大截图实锤：
    达人问「Disney/迪士尼草莓熊磁吸二合一暖手生日礼物送女友闺蜜冬季保暖 这款可以带不」
    → bot 回「我是你的带货选品助手…其他问题帮不上忙哦～」
    → 老大：怎么可以这样回复，应该回复「请提供一下商品的链接或者商品ID」

**根因是设计层缺口，不是模型笨**：商品判断有硬前置（必须拿到商品标识才有真值），
但路由层从没表达过这个前置 —— 「这个请求需要什么输入」被压成了「这个请求答不了」。

锁死三层 + 一组反向断言：
    L1 路由层：商品判断意图 + 缺商品标识 → 回「要标识」，**零积分零 LLM**
    L2 商品ID 路由：达人按引导语只发一串数字 → 走**商品**链路（不是抖音号！）
    L3 拒答兜底：模型拒了且是商品判断 → 也要「要标识」，不是通用兜底
    反向：抖音号 / 视频链接 / 商品链接 / 泛类目策略问题 / 无关闲聊 **不能被劫持**

铁律：零 prod 接触 —— daren_bot 用 DAREN_DB env 指 tmp 库。
"""
import importlib
import os
import tempfile
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TK = "tk_intent_test"
# 老大截图里的原句，一字不改
REAL_QUESTION = ("Disney/迪士尼草莓熊磁吸二合一暖手生日礼物送女友闺蜜冬季保暖 "
                 "这款可以带不")
PRODUCT_ID = "3446348913060228570"
PRODUCT_URL = "https://www.jinritemai.com/product/" + PRODUCT_ID


@pytest.fixture()
def bot(monkeypatch):
    """daren_bot 模块都指向同一个 tmp 库。"""
    tmp = tempfile.mkdtemp(prefix='daren_intent_bot_')
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    import daren_bot.product_intent as pi
    importlib.reload(cfg)
    importlib.reload(ddb)
    importlib.reload(hd)
    importlib.reload(ex)
    importlib.reload(pi)
    ddb.init_db()
    ddb.grant(TK, 100)
    return {"db": ddb, "handler": hd, "executors": ex, "config": cfg,
            "intent": pi}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _router(handler):
    class _WM:
        def __init__(self, s):
            self.s = s

        def get_sender(self, tenant_key):
            return self.s

    s = _FakeSender()
    return handler.Router(_WM(s)), s


def _dispatch(handler, router, text, tenant=None):
    """走一次真实的文本路由（不是直接调 _route_xxx —— 要测的是**分流顺序**）。"""
    tenant = tenant or {"tenant_key": TK, "bind_douyin_id": ""}
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat_1", message_type="text",
                content='{"text": %s}' % _json_str(text)),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_test")),
        ))
    router._dispatch(tenant, data)
    return tenant


def _json_str(s):
    import json
    return json.dumps(s, ensure_ascii=False)


def _spy(bot, monkeypatch):
    """记录入队与扣分，用来断言「走了哪条路」「扣没扣钱」。"""
    db = bot["db"]
    enq, ded = [], []
    orig_enq, orig_ded = db.enqueue_task, db.deduct

    def spy_enq(*a, **kw):
        enq.append(a)
        return orig_enq(*a, **kw)

    def spy_ded(*a, **kw):
        ded.append(a)
        return orig_ded(*a, **kw)

    monkeypatch.setattr(db, "enqueue_task", spy_enq)
    monkeypatch.setattr(db, "deduct", spy_ded)
    return enq, ded


# ---------------- L1：缺商品标识 → 要标识 ----------------

class TestMissingProductRef:
    def test_real_screenshot_question_asks_for_link_or_id(self, bot, monkeypatch):
        """老大截图那句原话：必须回「请提供链接或商品ID」，不能是「其他问题帮不上忙」。"""
        hd = bot["handler"]
        pi = bot["intent"]
        router, s = _router(hd)
        enq, ded = _spy(bot, monkeypatch)

        _dispatch(hd, router, REAL_QUESTION)

        assert pi.NEED_PRODUCT_REF_REPLY in s.text
        assert "其他问题帮不上忙" not in s.text
        # 零成本：不扣积分、不入队（更不能去烧 LLM）
        assert ded == []
        assert enq == []

    def test_reply_contains_both_forms(self, bot):
        """话术必须同时给出「链接」和「商品ID」两个可接受输入。"""
        pi = bot["intent"]
        assert "链接" in pi.NEED_PRODUCT_REF_REPLY
        assert "商品ID" in pi.NEED_PRODUCT_REF_REPLY

    @pytest.mark.parametrize("q", [
        "这款暖手宝能带不",
        "磁吸暖手宝这款值得带吗",
        "这个商品可以带货吗",
        "这款杯子能推吗",
        "这个链接能带不",
        "这个礼物适合我吗",
    ])
    def test_variants_hit(self, bot, q):
        assert bot["intent"].missing_product_ref_question(q) is True

    @pytest.mark.parametrize("q", [
        "我的粉丝画像怎么样",          # 账号分析
        "帮我分析下账号",
        "我适合带食品吗",              # 泛类目策略问题 → 问链接是误伤
        "抖音带不带货",
        "天气怎么样",                  # 无关
        "这个报告的数据对不对",         # 质疑类 → 走 challenge 分支
        "余额",
        "这款",                        # 有指向无判断
        "能带吗",                      # 有判断无指向
    ])
    def test_non_product_questions_not_hijacked(self, bot, q):
        assert bot["intent"].missing_product_ref_question(q) is False

    def test_already_has_ref_does_not_ask(self, bot):
        pi = bot["intent"]
        assert pi.missing_product_ref_question(
            PRODUCT_URL + " 这款能带不") is False
        assert pi.missing_product_ref_question(
            "这个商品ID " + PRODUCT_ID + " 能带不") is False

    def test_phone_number_is_not_product_id(self, bot):
        assert bot["intent"].find_bare_product_id("19112345678") == ""


# ---------------- L2：裸商品ID 能被收下（不是抖音号） ----------------

class TestBareProductIdRouting:
    def test_bare_id_goes_to_product_pipeline(self, bot, monkeypatch):
        """达人按引导语只发一串数字 → 必须走商品链路，不能被 DOUYIN_RE 当成抖音号。"""
        hd = bot["handler"]
        router, s = _router(hd)
        enq, ded = _spy(bot, monkeypatch)

        _dispatch(hd, router, PRODUCT_ID)

        kinds = [a[1] for a in enq]
        targets = [a[2] for a in enq]
        assert "product" in kinds, "裸商品ID 应当走商品链路"
        assert "douyin" not in kinds, "裸商品ID 不能被当成抖音号"
        assert PRODUCT_ID in targets
        assert "商品ID" in s.text

    def test_id_with_words_goes_to_product(self, bot, monkeypatch):
        hd = bot["handler"]
        router, s = _router(hd)
        enq, _ = _spy(bot, monkeypatch)

        _dispatch(hd, router, "这个商品ID " + PRODUCT_ID + " 能带不")

        kinds = [a[1] for a in enq]
        assert "product" in kinds
        assert "douyin" not in kinds

    def test_id_inside_link_is_not_bare_id(self, bot):
        """链接里的数字归链接路由管，不能被抽成裸 ID（否则会走两条路）。"""
        assert bot["intent"].find_bare_product_id(
            "https://x.com/detail?id=" + PRODUCT_ID) == ""


# ---------------- 回归保护：老路由不能被破坏 ----------------

class TestRoutingRegressions:
    def test_douyin_id_still_douyin(self, bot, monkeypatch):
        hd = bot["handler"]
        router, s = _router(hd)
        enq, _ = _spy(bot, monkeypatch)

        _dispatch(hd, router, "hlb725")

        kinds = [a[1] for a in enq]
        assert "douyin" in kinds

    def test_labeled_douyin_id_still_douyin(self, bot, monkeypatch):
        """明确写「抖音号：xxx」→ 一定按抖音号走（哪怕那串是 19 位数字）。"""
        hd = bot["handler"]
        router, s = _router(hd)
        enq, _ = _spy(bot, monkeypatch)

        _dispatch(hd, router, "抖音号：" + PRODUCT_ID)

        kinds = [a[1] for a in enq]
        assert "douyin" in kinds
        assert "product" not in kinds

    def test_product_link_still_product(self, bot, monkeypatch):
        hd = bot["handler"]
        router, s = _router(hd)
        enq, _ = _spy(bot, monkeypatch)

        _dispatch(hd, router, PRODUCT_URL)

        kinds = [a[1] for a in enq]
        assert "product" in kinds
        assert "douyin" not in kinds

    def test_video_link_still_video(self, bot, monkeypatch):
        hd = bot["handler"]
        router, s = _router(hd)
        enq, _ = _spy(bot, monkeypatch)

        _dispatch(hd, router, "https://v.douyin.com/iRNBho6G/ 帮我看看")

        kinds = [a[1] for a in enq]
        assert "video" in kinds


# ---------------- L3：模型拒答时的兜底 ----------------

class TestRefusalFallback:
    def test_refusal_on_product_question_asks_for_ref(self, bot, monkeypatch):
        """模型拒答 + 是商品判断意图 → 要标识，而不是通用兜底。"""
        ex = bot["executors"]
        pi = bot["intent"]
        monkeypatch.setattr(ex, "_qa_llm", lambda prompt: "拒")
        monkeypatch.setattr(ex, "_talent_context", lambda tk: "")

        ans = ex.execute_qa({"tenant_key": TK, "target": "这个暖手宝能带不"})

        assert ans == pi.NEED_PRODUCT_REF_REPLY
        assert "其他问题帮不上忙" not in ans

    def test_refusal_on_unrelated_still_generic(self, bot, monkeypatch):
        """真的无关的问题 → 仍然是通用兜底（别把闲聊也变成要链接）。"""
        ex = bot["executors"]
        monkeypatch.setattr(ex, "_qa_llm", lambda prompt: "拒")
        monkeypatch.setattr(ex, "_talent_context", lambda tk: "")

        ans = ex.execute_qa({"tenant_key": TK, "target": "今天天气怎么样"})

        assert "其他问题帮不上忙" in ans
        assert "商品ID" not in ans
