# -*- coding: utf-8 -*-
"""上文商品语境承接守卫（2026-10-28 老大实锤）。

症状：达人上面已经发过商品链接并拿到分析结论，下面追问「这个品适合我吗」，
bot 却回「请提供一下商品的链接或者商品ID」—— 完全没记住上文。

修复四层：
  ① handler：missing_product_ref 话术前挡一下 —— 24h 内分析过商品就不拦，
    让追问落到 QA 链；
  ② executors._recent_product_block：指代词命中 + 有最近商品上下文 ⇒
    把商品块（名称/价格/佣金/月销/结论）注入 QA prompt，禁令「绝对禁止再要商品ID」；
  ③ db.qa_cacheable：指代词问题不缓存 —— 答案绑死「上文是哪个商品」的语境，
    缓存住 = 语境漂移型缓存污染（几天后换个商品问同一句，旧答案直接发出）；
  ④ db.get_latest_product_verdict / has_recent_product_context：数据源。

★ 每条守卫都做过「能不能红」的变异对照（注释里标了）。
"""

import importlib
import json
from types import SimpleNamespace

import pytest

TK = "tk_ctx_test"
PRODUCT_KEY = "prod_ctx_1"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    monkeypatch.setenv("DAREN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAREN_DB", str(tmp_path / "daren_bot.db"))
    monkeypatch.delenv("DAREN_USE_KIMI", raising=False)
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    for m in (cfg, ddb, hd, ex):
        importlib.reload(m)
    ddb.init_db()
    ddb.grant(TK, 1000)
    return {"db": ddb, "handler": hd, "executors": ex, "config": cfg}


class _FakeSender:
    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

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


def _dispatch_text(handler, router, text):
    tenant = {"tenant_key": TK, "bind_douyin_id": ""}
    data = SimpleNamespace(
        event=SimpleNamespace(
            message=SimpleNamespace(
                chat_id="chat_1", message_type="text",
                content=json.dumps({"text": text}, ensure_ascii=False)),
            sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="ou_t"))))
    router._dispatch(tenant, data)
    return tenant


# ---------------- ① 路由层：有上文时不再要标识 ----------------

class TestRouteContextCarry:
    def test_with_pending_followup_goes_to_qa(self, bot, monkeypatch):
        """24h 内分析过商品 ⇒ 「这个品可以带不」必须走 QA（入队 qa 任务），
        不许回「请提供链接或商品ID」。
        （对照：删掉 handler 里 has_recent_product_context 挡板 ⇒ 本用例红）"""
        from daren_bot import product_intent
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, {"name": "测试品A"})
        hd = bot["handler"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        # ★ 用「指针词(这款)+判断词(可以带)」的真命中句式 —— 无上下文时这句
        #   走的正是 missing_product_ref 要标识分支（2026-10-22 老大口径场景）。
        _dispatch_text(hd, router, "迪士尼草莓熊暖手宝这款可以带不")
        assert product_intent.NEED_PRODUCT_REF_REPLY not in s.text, \
            f"有上文还回要标识话术：{s.text!r}"
        assert any(a[1] == "qa" for a, _ in seen), \
            f"追问没有走 QA 链：{seen!r}"

    def test_without_pending_still_asks_for_ref(self, bot):
        """没有上文 ⇒ 保持 2026-10-22 的老大口径：要标识（不能回通用兜底）。"""
        from daren_bot import product_intent
        hd = bot["handler"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        _dispatch_text(hd, router, "迪士尼草莓熊暖手宝这款可以带不")
        assert product_intent.NEED_PRODUCT_REF_REPLY in s.text

    def test_verdict_also_counts_as_context(self, bot, monkeypatch):
        """pending 过期/不存在时，7 天内的结论档案也能挡要标识话术。"""
        from daren_bot import product_intent
        ddb = bot["db"]
        ddb.verdict_cache_put(TK, PRODUCT_KEY, name="测试品B",
                              verdict="慎带", why="佣金偏低")
        hd = bot["handler"]
        sender = _FakeSender()
        router, s = _router(hd, sender)
        seen = []
        orig = ddb.enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(ddb, "enqueue_task_charged", spy)
        _dispatch_text(hd, router, "这个品适合我吗")
        assert product_intent.NEED_PRODUCT_REF_REPLY not in s.text
        assert any(a[1] == "qa" for a, _ in seen)


# ---------------- ② 语境块构建 ----------------

class TestRecentProductBlock:
    STATS = {"name": "夏日凉鞋X", "price": "99", "commission_rate": "20%",
             "monthly_sales": 3000, "shop_score": 92}

    def test_pending_hit(self, bot):
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self.STATS)
        ex = bot["executors"]
        block = ex._recent_product_block(TK, "这个品佣金多少")
        assert "夏日凉鞋X" in block and "20%" in block

    def test_non_referential_question_no_block(self, bot):
        """非指代问题不注入（别污染无关问答）。
        （对照：把 regex 判据删了 ⇒ 本用例红）"""
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self.STATS)
        ex = bot["executors"]
        assert ex._recent_product_block(TK, "怎么提升粉丝互动率") == ""

    def test_verdict_fallback(self, bot):
        """没有 pending 时回退到结论档案（名称/结论/理由）。"""
        ddb = bot["db"]
        ddb.verdict_cache_put(TK, PRODUCT_KEY, name="测试品B",
                              verdict="慎带", why="佣金率低于类目均值")
        ex = bot["executors"]
        block = ex._recent_product_block(TK, "这款能带吗")
        assert "测试品B" in block and "慎带" in block

    def test_no_context_no_block(self, bot):
        ex = bot["executors"]
        assert ex._recent_product_block(TK, "这个品怎么样") == ""

    def test_qa_prompt_includes_block(self, bot, monkeypatch):
        """execute_qa 的 prompt 必须含【上文语境】块 + 禁止再要ID的指令。
        （对照：把 prompt 里的 recent_line 删了 ⇒ 本用例红）"""
        ddb = bot["db"]
        ddb.set_commission_pending(TK, PRODUCT_KEY, self.STATS)
        ex = bot["executors"]
        captured = {}
        monkeypatch.setattr(ex, "_qa_llm",
                            lambda prompt: captured.setdefault("p", prompt)
                            or "答：能带")
        task = {"tenant_key": TK, "target": "这个品适合我吗",
                "chat_id": "", "payload": ""}
        ex.execute_qa(task)
        p = captured.get("p", "")
        assert "上文语境" in p and "夏日凉鞋X" in p
        assert "绝对禁止再要商品ID" in p


# ---------------- ③ 指代问题不缓存 ----------------

class TestReferentialNotCached:
    @pytest.mark.parametrize("q", ["这个品怎么样", "这款能带吗", "它的佣金多少",
                                   "那个商品适合我吗", "刚才那个品呢"])
    def test_referential_questions_not_cached(self, bot, q):
        assert bot["db"].qa_cacheable(q) is False

    @pytest.mark.parametrize("q", ["怎么提升GPM", "粉丝画像不准怎么办",
                                   "什么类目适合我"])
    def test_normal_questions_still_cached(self, bot, q):
        assert bot["db"].qa_cacheable(q) is True

    def test_referential_not_stored_in_cache(self, bot):
        """写缓存的口子也要堵住：qa_cache_put 对指代问题直接不存。"""
        ddb = bot["db"]
        ddb.qa_cache_put(TK, "这个品怎么样", "答：能带")
        assert ddb.qa_cache_get(TK, "这个品怎么样") is None
