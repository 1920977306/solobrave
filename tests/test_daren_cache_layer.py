"""复用层（存储/缓存）测试 (2026-10-21)。

老大要求原文：「第一次抓取数据必须存到我们的系统知识库，下次达人需要数据
直接调用存储的数据即可（省算力），一周之后再给达人重新抓取。」

三层缓存：
| 层 | 表 | TTL | 隔离维度 | 命中省什么 |
|---|---|---|---|---|
| 商品数据 | bot_product_cache（已有） | 7 天 | 全平台共用 | 开浏览器采集 |
| 选品结论 | bot_product_verdicts（新增） | 7 天 | **按租户** | 采集 + LLM 结论 |
| 问答回复 | bot_qa_cache（新增） | 1 天 | 按租户 | LLM |

铁律（对应本轮踩过的坑，写死成断言）：
① **复用判定必须在入队处**（handler._route_*）—— 放到执行时判定就晚了，
   LLM 已经烧掉了。→ 用「把 LLM/采集函数换成一调用就炸」证明**没被调**，
   而不是靠读代码说它短路了。
② **商品数据全局共享、结论按租户隔离** —— 结论依赖达人画像，
   同一个商品给不同达人结论必须不同。串了 = 把 A 的画像喂给 B。
③ **含时效词的问题不缓存** —— 缓存住等于给假的新鲜度。
④ **结论档案表不清** —— bot_product_verdicts 是审计依据，删了就没法回溯；
   过期判定在读的时候做。

铁律：零 prod 接触 —— daren_bot 用 DAREN_DB env 指 tmp 库。
"""
import importlib
import json
import os
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TK = "tk_cache_test"
TK2 = "tk_cache_test2"
Q = "我的粉丝画像和选品方向怎么定"
PRODUCT_URL = "https://www.jinritemai.com/product/3446348913060228570"


@pytest.fixture()
def bot(monkeypatch):
    """daren_bot 三个模块都指向同一个 tmp 库。"""
    tmp = tempfile.mkdtemp(prefix='daren_cache_bot_')
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    importlib.reload(cfg)
    importlib.reload(ddb)
    ddb.init_db()
    # handler/executors 在 reload 之后 import —— 拿到的是同一批模块对象
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    importlib.reload(hd)
    importlib.reload(ex)
    ddb.grant(TK, 100)
    ddb.grant(TK2, 100)
    return {"db": ddb, "handler": hd, "executors": ex, "config": cfg}


class _FakeSender:
    """替身 sender：记录发出去的消息，测试里断言「回了几句话、说了啥」。"""

    def __init__(self):
        self.sent = []

    def send_text(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    @property
    def text(self):
        return "\n".join(t for _, t in self.sent)


def _router(handler):
    """Router + 假 sender（handler 只用 wm.get_sender）。"""

    class _WM:
        def __init__(self, s):
            self.s = s

        def get_sender(self, tenant_key):
            return self.s

    s = _FakeSender()
    return handler.Router(_WM(s)), s


# ---------------- db 层：缓存语义 ----------------

class TestQaCache:
    def test_put_get_hit(self, bot):
        db = bot["db"]
        db.qa_cache_put(TK, Q, "答案正文")
        assert db.qa_cache_get(TK, Q) == "答案正文"

    def test_tenant_isolated(self, bot):
        """A 租户的答案不能被 B 租户读到（画像不一样，串了就是错答）。"""
        db = bot["db"]
        db.qa_cache_put(TK, Q, "A 的答案")
        assert db.qa_cache_get(TK2, Q) is None

    def test_volatile_question_not_cached(self, bot):
        """含时效词的问题不缓存 —— 缓存住等于给假的新鲜度。"""
        db = bot["db"]
        for q in ("最近的带货数据怎么样", "我今天该上什么品", "最新政策是什么"):
            assert db.qa_cacheable(q) is False
            db.qa_cache_put(TK, q, "不该被缓存的答案")
            assert db.qa_cache_get(TK, q) is None

    def test_expired_is_miss(self, bot):
        db = bot["db"]
        db.qa_cache_put(TK, Q, "答案")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_qa_cache SET created_at=? WHERE tenant_key=?",
                         (db.now_ts() - db.QA_CACHE_TTL - 10, TK))
        assert db.qa_cache_get(TK, Q) is None

    def test_purge_keeps_fresh_rows(self, bot):
        db = bot["db"]
        db.qa_cache_put(TK, Q, "还在期内")
        db.qa_cache_put(TK, "另一个问题啊", "旧的")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_qa_cache SET created_at=? WHERE question=?",
                         (db.now_ts() - db.QA_CACHE_TTL - 10, "另一个问题啊"))
        assert db.purge_expired_caches() == 1
        assert db.qa_cache_get(TK, Q) == "还在期内"
        assert db.qa_cache_get(TK, "另一个问题啊") is None


class TestVerdictCache:
    def test_put_get_hit(self, bot):
        db = bot["db"]
        db.verdict_cache_put(TK, "pk_1", name="折叠登山杖", verdict="能带",
                             why="类目一致", md="## 结论\n🟢 能带\n类目一致",
                             stats={"name": "折叠登山杖", "price": "¥8.9"})
        hit = db.verdict_cache_get(TK, "pk_1")
        assert hit is not None
        assert hit["verdict"] == "能带"
        assert hit["md"].startswith("## 结论")
        assert json.loads(hit["stats_json"])["price"] == "¥8.9"

    def test_tenant_isolated(self, bot):
        """结论依赖达人画像 —— 必须按租户隔离，绝不能跨租户复用。"""
        db = bot["db"]
        db.verdict_cache_put(TK, "pk_1", verdict="能带", md="## 结论\n🟢 能带\n理由")
        assert db.verdict_cache_get(TK2, "pk_1") is None

    def test_expired_is_miss(self, bot):
        db = bot["db"]
        db.verdict_cache_put(TK, "pk_1", verdict="能带", md="## 结论\n🟢 能带\n理由")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_product_verdicts SET created_at=? "
                         "WHERE tenant_key=? AND product_key=?",
                         (db.now_ts() - db.VERDICT_CACHE_TTL - 10, TK, "pk_1"))
        assert db.verdict_cache_get(TK, "pk_1") is None

    def test_purge_does_not_delete_verdict_archive(self, bot):
        """结论档案表不是缓存 —— 过期也要留着做审计/回溯。"""
        db = bot["db"]
        db.verdict_cache_put(TK, "pk_1", verdict="能带", md="## 结论\n🟢 能带\n理由")
        with db.db_ctx() as conn:
            conn.execute("UPDATE bot_product_verdicts SET created_at=0 "
                         "WHERE tenant_key=? AND product_key=?", (TK, "pk_1"))
        db.purge_expired_caches()
        with db.db_ctx() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM bot_product_verdicts "
                             "WHERE tenant_key=?", (TK,)).fetchone()["c"]
        assert n == 1


# ---------------- handler 层：判定必须在入队处 ----------------

class TestHandlerReuseGate:
    def test_qa_cache_hit_costs_zero(self, bot):
        """命中 QA 缓存 → 扣 0 分、ledger 记 cache_hit=1、任务带缓存答案。"""
        db, hd = bot["db"], bot["handler"]
        db.qa_cache_put(TK, Q, "缓存里的答案")
        router, sender = _router(hd)
        router._route_qa({"tenant_key": TK}, "chat_1", Q, sender)

        assert db.get_balance(TK) == 100          # 一分没扣
        with db.db_ctx() as conn:
            row = conn.execute(
                "SELECT * FROM bot_credits_ledger WHERE tenant_key=? "
                "ORDER BY created_at DESC LIMIT 1", (TK,)).fetchone()
            assert row["cost"] == 0
            assert row["cache_hit"] == 1
            task = conn.execute(
                "SELECT * FROM bot_collect_tasks WHERE tenant_key=? AND type='qa' "
                "ORDER BY created_at DESC LIMIT 1", (TK,)).fetchone()
        payload = json.loads(task["payload"])
        assert payload["cached_answer"] == "缓存里的答案"
        assert task["cache_hit"] == 1

    def test_qa_cache_miss_costs_normal(self, bot):
        """没命中 → 照常扣 COST_QA，不该被缓存逻辑误伤。"""
        db, hd = bot["db"], bot["handler"]
        router, sender = _router(hd)
        router._route_qa({"tenant_key": TK}, "chat_1", Q, sender)
        assert db.get_balance(TK) == 100 - bot["config"].COST_QA

    def test_verdict_cache_hit_costs_cached_price(self, bot):
        """命中结论缓存 → 扣 5 分（比现价便宜），任务带完整结论。"""
        db, hd = bot["db"], bot["handler"]
        pk = hd.normalize_product_url(PRODUCT_URL)
        db.verdict_cache_put(TK, pk, name="折叠登山杖", verdict="能带",
                             md="## 结论\n🟢 能带\n类目一致",
                             stats={"name": "折叠登山杖"})
        router, sender = _router(hd)
        router._route_link({"tenant_key": TK}, "chat_1", PRODUCT_URL, sender)

        assert db.get_balance(TK) == 100 - bot["config"].COST_PRODUCT_CACHED
        with db.db_ctx() as conn:
            task = conn.execute(
                "SELECT * FROM bot_collect_tasks WHERE tenant_key=? AND type='product' "
                "ORDER BY created_at DESC LIMIT 1", (TK,)).fetchone()
        payload = json.loads(task["payload"])
        assert payload["cached_verdict"]["verdict"] == "能带"
        assert task["cache_hit"] == 1

    def test_verdict_cache_miss_falls_through_to_product_cache(self, bot):
        """没有结论缓存 → 落回商品数据缓存分支（老路径不能被改坏）。"""
        db, hd = bot["db"], bot["handler"]
        pk = hd.normalize_product_url(PRODUCT_URL)
        db.cache_put(pk, name="折叠登山杖", price="¥8.9",
                     stats={"name": "折叠登山杖"})
        router, sender = _router(hd)
        router._route_link({"tenant_key": TK}, "chat_1", PRODUCT_URL, sender)
        assert db.get_balance(TK) == 100 - bot["config"].COST_PRODUCT_CACHED
        with db.db_ctx() as conn:
            task = conn.execute(
                "SELECT * FROM bot_collect_tasks WHERE tenant_key=? AND type='product' "
                "ORDER BY created_at DESC LIMIT 1", (TK,)).fetchone()
        assert "cached_verdict" not in json.loads(task["payload"] or "{}")


# ---------------- executor 层：消费缓存，真的不调 LLM ----------------

class TestExecutorConsumesCache:
    def test_qa_uses_cached_answer_without_llm(self, bot, monkeypatch):
        """payload 带 cached_answer → 直接返回，LLM 一次都不调。"""
        ex = bot["executors"]

        def _boom(*a, **kw):
            raise AssertionError("缓存命中时不应该调 LLM")

        monkeypatch.setattr(ex, "_qa_llm", _boom)
        monkeypatch.setattr(ex, "_talent_context", _boom)
        out = ex.execute_qa({
            "tenant_key": TK, "target": Q,
            "payload": json.dumps({"cached_answer": "缓存里的答案"}),
        })
        assert out == "缓存里的答案"

    def test_product_uses_cached_verdict_without_collect(self, bot, monkeypatch):
        """payload 带 cached_verdict → 直接返回，采集和 LLM 都不调。"""
        ex = bot["executors"]

        def _boom(*a, **kw):
            raise AssertionError("结论缓存命中时不应该再采集/调 LLM")

        monkeypatch.setattr(ex, "collect_product_by_url", _boom)
        monkeypatch.setattr(ex, "_product_verdict", _boom)
        out = ex.execute_product_collect({
            "tenant_key": TK, "target": "pk_1",
            "payload": json.dumps({"cached_verdict": {
                "name": "折叠登山杖", "verdict": "能带",
                "md": "## 结论\n🟢 能带\n类目一致",
                "stats_json": json.dumps({"name": "折叠登山杖"}),
            }}),
        })
        data = json.loads(out)
        assert data["title"] == "折叠登山杖"
        assert data["md"].startswith("## 结论")
        assert data["stats"]["name"] == "折叠登山杖"

    def test_qa_writes_cache_after_real_answer(self, bot, monkeypatch):
        """正常问答跑完后把答案写进缓存（下次才能命中）。"""
        ex, db = bot["executors"], bot["db"]
        monkeypatch.setattr(ex, "_qa_llm", lambda *a, **kw: "你的粉丝偏女性，优先家居类目")
        monkeypatch.setattr(ex, "_talent_context", lambda *a, **kw: "【数据】粉丝 10 万")
        out = ex.execute_qa({"tenant_key": TK, "target": Q})
        assert "优先家居类目" in out
        assert db.qa_cache_get(TK, Q) == out

    def test_qa_refusal_not_cached(self, bot, monkeypatch):
        """拒答不是答案，不入缓存 —— 否则下一次会把「拒」发出去。"""
        ex, db = bot["executors"], bot["db"]
        monkeypatch.setattr(ex, "_qa_llm", lambda *a, **kw: "拒")
        monkeypatch.setattr(ex, "_talent_context", lambda *a, **kw: "")
        ex.execute_qa({"tenant_key": TK, "target": "今天天气怎么样啊"})
        assert db.qa_cache_get(TK, "今天天气怎么样啊") is None

    def test_product_writes_verdict_cache(self, bot, monkeypatch):
        """采集+判完 → 写结论缓存（同商品同达人下次直接复用）。"""
        ex, db = bot["executors"], bot["db"]
        monkeypatch.setattr(
            ex, "collect_product_by_url",
            lambda *a, **kw: {"name": "折叠登山杖", "price": "¥8.9",
                              "commission": "50%", "category": "居家日用"})
        monkeypatch.setattr(ex, "_talent_context", lambda *a, **kw: "【数据】服饰内衣 92.56%")
        monkeypatch.setattr(
            ex, "_product_verdict",
            lambda *a, **kw: "## 结论\n🟢 能带\n类目一致且价格带匹配")
        ex.execute_product_collect({"tenant_key": TK, "target": "pk_1"})
        hit = db.verdict_cache_get(TK, "pk_1")
        assert hit is not None
        assert hit["verdict"] == "能带"
        assert hit["why"] == "类目一致且价格带匹配"
        assert json.loads(hit["stats_json"])["price"] == "¥8.9"


# ---------------- 结论标签解析（别把「慎带」误标成「能带」） ----------------

class TestVerdictLabel:
    def test_label_from_conclusion_line_only(self, bot):
        ex = bot["executors"]
        md = "## 结论\n🟡 慎带\n价格合适但类目不对，暂时不能带"
        assert ex._verdict_label(md) == "慎带"
        assert ex._verdict_reason(md) == "价格合适但类目不对，暂时不能带"

    def test_label_without_header(self, bot):
        ex = bot["executors"]
        assert ex._verdict_label("🟢 能带\n理由") == "能带"

    def test_label_empty(self, bot):
        ex = bot["executors"]
        assert ex._verdict_label("") == ""
        assert ex._verdict_label("## 结论\n（AI 分析未出结果，暂不给结论）") == ""


# ---------------- worker 韧性：推送失败不能杀死线程 ----------------

class TestWorkerSurvivesPushFailure:
    """★ 2026-10-21 线上体检发现：worker.run() 里 `self._process(task)` 没包 try，
    一次飞书推送抛异常 → **worker 线程猝死** → 整个机器人静默停摆
    （日志里只有一条 traceback，之后所有任务都不再处理，没人发现）。

    这两个用例把「线程还活着」钉成断言，不靠嘴说。
    """

    def _worker(self, bot, monkeypatch):
        import daren_bot.worker as wkm
        db = bot["db"]
        # 商品 executor 换成假实现，别真去开浏览器
        monkeypatch.setitem(
            wkm.EXECUTORS, "product",
            lambda task: json.dumps({"title": "T", "stats": {}, "md": "## 结论\n🟢 能带\n理由"}))
        db.enqueue_task(TK, "product", "pk_push_fail", 20, chat_id="chat_x")

        class _Sender:
            def send_text(self, *a, **kw):
                pass

        class _WM:
            def get_sender(self, tenant_key):
                return _Sender()

        w = wkm.Worker(_WM(), poll_interval=0.1)
        w.start()
        return w

    def test_push_failure_does_not_kill_worker_thread(self, bot, monkeypatch):
        import time
        ex = bot["executors"]

        def _boom(*a, **kw):
            raise RuntimeError("模拟飞书推送失败")

        monkeypatch.setattr(ex, "notify_product_card", _boom)
        w = self._worker(bot, monkeypatch)
        try:
            time.sleep(1.5)
            alive = w.is_alive()
        finally:
            w.stop()
            w.join(timeout=3)
        assert alive, "推送失败把 worker 线程带死了（整个机器人会静默停摆）"

    def test_verdict_save_failure_does_not_kill_worker_thread(self, bot, monkeypatch):
        import sys
        import time
        import types

        # dashboard_card 依赖 PIL（测试环境没装）→ 用假模块注入，
        # 顺便验证 worker 里的 `from . import dashboard_card` 失败也不会带累线程
        fake = types.ModuleType("daren_bot.dashboard_card")

        def _boom(*a, **kw):
            raise RuntimeError("模拟写看板表失败")

        fake.save_product_verdict = _boom
        monkeypatch.setitem(sys.modules, "daren_bot.dashboard_card", fake)

        w = self._worker(bot, monkeypatch)
        try:
            time.sleep(1.5)
            alive = w.is_alive()
        finally:
            w.stop()
            w.join(timeout=3)
        assert alive, "写看板表失败把 worker 线程带死了"

    def test_push_success_is_logged(self, bot, monkeypatch, caplog):
        """推送成功必须留痕 —— 否则「飞书到底收到没有」只能靠肉眼。"""
        import logging
        import time
        w = self._worker(bot, monkeypatch)
        try:
            with caplog.at_level(logging.INFO, logger="daren-bot.worker"):
                time.sleep(1.5)
        finally:
            w.stop()
            w.join(timeout=3)
        assert any("push ok: type=product" in r.getMessage()
                   for r in caplog.records), "推送成功没有日志"
