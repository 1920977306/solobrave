# -*- coding: utf-8 -*-
"""批B修复守卫（2026-11-09 周报 P1/P2 清单，方案 docs/DAREN-BOT-FIX-PLAN-20261109.md §批B）。

B1：口播脚本意图（product_intent.wants_script）——脚本诉求命中，普通问答不命中；
    execute_qa script_mode 强制注入上文商品块（无指代词也要有）。
B2/B3：_extract_name_hint 口语尾巴剥离——周报两个实例标题只留昵称本体。
B4：周额度已尽时求「再分析」给确定性同口径（不调 LLM 不答「能分析」）；
    「我最近带的品」注入档案明细块。
B5：上文有商品上下文时不再复读「没有附商品链接」尾巴。
B6：failure_text 409/认领冲突分类；worker auto 失败夜间/登录态/409 不推达人；
    weekly 登录态/409 失败不排重试。
B7：达人可见文案不含「缓存」内部词。
B8：纠错/质疑消息免费路由判据（is_correction 宁漏勿抢）。
B9：post 富文本多图逐张走视觉链路（dispatch 对每张图调 _route_image）。
"""
import json

from daren_bot import handler
from daren_bot import executors
from daren_bot import failure_text
from daren_bot import product_intent
from daren_bot import db as dbmod


# ---------- B1 口播脚本意图 ----------

class TestWantsScript:
    def test_hits(self):
        assert product_intent.wants_script("给我写个口播脚本")
        assert product_intent.wants_script("保温杯的带货话术来一个")
        assert product_intent.wants_script("前三秒钩子怎么写")

    def test_misses(self):
        # 普通问答不带脚本诉求
        assert not product_intent.wants_script("这个品佣金多少")
        assert not product_intent.wants_script("")
        # 超长不命中（防正文里 incidental 提到「脚本」）
        assert not product_intent.wants_script("帮我看看这个商品" + "数据" * 40 + "脚本")


class TestScriptModeQa:
    def test_forces_recent_product_block(self, monkeypatch):
        # script_mode 时无指代词也要注入上文商品块
        monkeypatch.setattr(dbmod, "get_commission_pending",
                            lambda tk: {"stats": {"name": "物生物保温杯",
                                                  "price": 79, "commission_rate": 20}})
        block = executors._recent_product_block("tk1", "给我写个口播脚本",
                                                force=True)
        assert "物生物保温杯" in block

    def test_script_mode_without_context_asks_ref(self, monkeypatch):
        # 竞态：路由把关后上下文消失 ⇒ 诚实要标识，绝不拿别的品写脚本
        monkeypatch.setattr(executors, "_recent_product_block",
                            lambda *a, **k: "")
        out = executors.execute_qa({"tenant_key": "tk1",
                                    "target": "给我写个口播脚本",
                                    "payload": json.dumps({"script_mode": True})})
        assert out == product_intent.NEED_PRODUCT_REF_REPLY

    def test_script_mode_prompt_locks_product(self, monkeypatch):
        monkeypatch.setattr(dbmod, "get_commission_pending",
                            lambda tk: {"stats": {"name": "物生物保温杯",
                                                  "price": 79}})
        prompts = []
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: prompts.append(p) or "好的脚本")
        executors.execute_qa({"tenant_key": "tk1",
                              "target": "给我写个口播脚本",
                              "payload": json.dumps({"script_mode": True})})
        assert prompts, "script_mode 必须调 LLM 生成脚本"
        p = prompts[0]
        assert "物生物保温杯" in p            # 绑上文商品
        assert "前三秒" in p                  # 脚本结构指令
        assert "禁止套用其他品类" in p

    def test_script_mode_never_asks_other_template(self, monkeypatch):
        """★ 回归（周报 P1 实锤）：保温杯脚本曾被「包」的模板顶替。"""
        monkeypatch.setattr(dbmod, "get_commission_pending",
                            lambda tk: {"stats": {"name": "物生物保温杯"}})
        prompts = []
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: prompts.append(p) or "脚本")
        executors.execute_qa({"tenant_key": "tk1",
                              "target": "口播脚本呢",
                              "payload": json.dumps({"script_mode": True})})
        assert prompts and "绝对禁止再索要商品链接或商品ID" in prompts[0]


# ---------- B2/B3 口语尾巴剥离 ----------

class TestNameHint:
    def test_weekly_case_co(self):
        # 周报实例 1：「CO师傅分析一下这个账号呢」→「CO师傅」
        assert handler._extract_name_hint(
            "CO师傅分析一下这个账号呢", "DJcoco888") == "CO师傅"

    def test_weekly_case_yibai(self):
        # 周报实例 2：「我的小号发给你跟我分析一下名称一百」→「一百」
        assert handler._extract_name_hint(
            "我的小号发给你跟我分析一下名称一百", "Ayn11930") == "一百"

    def test_plain_nickname_kept(self):
        # 常规形态不回归：「洁洁爱户外\n抖音号：hlb725」
        assert handler._extract_name_hint("洁洁爱户外 抖音号：hlb725",
                                          "hlb725") == "洁洁爱户外"

    def test_b2_case_routes_to_collect(self):
        # B2 回放：报抖音名+号求分析 ⇒ name_hint 是昵称本体（不再整句）
        assert handler._extract_name_hint(
            "抖音名：一百 抖音账号：Ayn11930 这是我的账号，帮我分析一下",
            "Ayn11930") == "一百"


# ---------- B4 周限口径 / 近期带的品 ----------

class TestWeeklyLimitConsistency:
    def test_reanalysis_within_week_denied_deterministically(self, monkeypatch):
        monkeypatch.setattr(dbmod, "get_tenant",
                            lambda tk: {"bind_douyin_id": "dy1"})
        monkeypatch.setattr(dbmod, "get_week_count", lambda tk, d: 1)
        called = []
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: called.append(p) or "能分析")
        out = executors.execute_qa({"tenant_key": "tk1",
                                    "target": "现在不能分析嘛"})
        assert "下周更新" in out and "能分析" != out.strip()
        assert not called, "周额度已尽时不许调 LLM 即兴答「能分析」"

    def test_normal_question_not_blocked(self, monkeypatch):
        # 不含「分析」诉求的普通问题不受 B4 分支影响
        monkeypatch.setattr(dbmod, "get_tenant",
                            lambda tk: {"bind_douyin_id": "dy1"})
        monkeypatch.setattr(dbmod, "get_week_count", lambda tk, d: 1)
        called = []
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: called.append(p) or "答")
        executors.execute_qa({"tenant_key": "tk1", "target": "最近天气如何"})
        assert called, "普通问题照常走 LLM"


class TestRecentProductsBlock:
    def test_block_injected_when_asked(self, monkeypatch):
        t = {"updated_at": 1762000000000,
             "product_details": [{"cells": ["Coolchap 人字拖", "¥179"]}]}
        monkeypatch.setattr(executors, "_read_talent", lambda *a, **k: t)
        block = executors._recent_products_answer_block("tk1", "dy1",
                                                        "我最近带的品有啥")
        assert "人字拖" in block

    def test_misses_when_not_asked(self):
        assert executors._recent_products_answer_block("tk1", "dy1",
                                                       "今天推什么") == ""


# ---------- B5 复读尾巴 ----------

class TestNoRepeatedMissingRefHint:
    def test_hint_suppressed_with_recent_context(self, monkeypatch):
        # 上文刚分析过商品，再问带「链接」字样的问题 ⇒ 尾巴不复读
        monkeypatch.setattr(dbmod, "get_tenant",
                            lambda tk: {"bind_douyin_id": ""})
        monkeypatch.setattr(dbmod, "has_recent_product_context",
                            lambda tk: True)
        monkeypatch.setattr(executors, "_recent_product_block",
                            lambda *a, **k: "")
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: "这个是商品链接的情况说明")
        out = executors.execute_qa({"tenant_key": "tk1",
                                    "target": "这个链接的商品怎么样"})
        assert "没有附商品链接" not in out

    def test_hint_kept_without_context(self, monkeypatch):
        # 没有上文商品上下文 ⇒ 提醒保留（别误伤原设计意图）
        monkeypatch.setattr(dbmod, "get_tenant",
                            lambda tk: {"bind_douyin_id": ""})
        monkeypatch.setattr(dbmod, "has_recent_product_context",
                            lambda tk: False)
        monkeypatch.setattr(executors, "_recent_product_block",
                            lambda *a, **k: "")
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: "这个品类整体不错")
        out = executors.execute_qa({"tenant_key": "tk1",
                                    "target": "这个商品能带吗"})
        assert "没有附商品链接" in out


# ---------- B6 auto 失败分流 ----------

class TestConflictClassify:
    def test_409(self):
        assert failure_text.classify(RuntimeError("认领失败 409 Conflict")) \
            == "conflict"

    def test_login_still_login(self):
        assert failure_text.classify(
            RuntimeError("达人端采集账号登录态过期了，需要重新扫码登录")) == "need_login"

    def test_timeout_not_conflict(self):
        assert failure_text.classify(RuntimeError("查询超时")) == "timeout"


def _make_worker(monkeypatch):
    from daren_bot import worker
    sent = []

    class _Sender:
        def send_text(self, chat_id, text):
            sent.append((chat_id, text))

    class _Wm:
        def get_sender(self, tk):
            return _Sender()

    monkeypatch.setattr(worker.db, "fail_task_and_refund",
                        lambda *a, **k: (True, 100))
    monkeypatch.setattr(worker.ops_alert, "alert",
                        lambda *a, **k: None)
    # __new__ 绕开 Thread.__init__（只测 _fail 不需要线程）
    w = worker.Worker.__new__(worker.Worker)
    w.wm = _Wm()
    return w, sent


class _FakeDateTimeNS:
    """替换 worker.datetime 模块引用（datetime.datetime 是 C 类型，不能直接 patch）。"""

    def __init__(self, fake_now):
        class _DT:
            @staticmethod
            def now():
                return fake_now
        self.datetime = _DT


class TestAutoFailSilence:
    def test_login_auto_fail_not_pushed(self, monkeypatch):
        w, sent = _make_worker(monkeypatch)
        task = {"id": 1, "tenant_key": "tk1", "type": "douyin",
                "chat_id": "oc1", "credits_cost": 0,
                "payload": json.dumps({"auto": 1})}
        w._fail(task, "达人端采集账号登录态过期了，需要重新扫码登录",
                exc=RuntimeError("达人端采集账号登录态过期了，需要重新扫码登录"))
        assert not sent, "登录态类 auto 失败不许推达人"

    def test_conflict_auto_fail_not_pushed(self, monkeypatch):
        w, sent = _make_worker(monkeypatch)
        task = {"id": 2, "tenant_key": "tk1", "type": "douyin",
                "chat_id": "oc1", "credits_cost": 0,
                "payload": json.dumps({"auto": 1})}
        w._fail(task, "认领失败 409 Conflict",
                exc=RuntimeError("认领失败 409 Conflict"))
        assert not sent, "409 类 auto 失败不许推达人"

    def test_night_auto_fail_not_pushed(self, monkeypatch):
        from daren_bot import worker
        w, sent = _make_worker(monkeypatch)
        fake_now = type("T", (), {"hour": 1})()    # 周报实锤的凌晨 01:47
        monkeypatch.setattr(worker, "datetime",
                            _FakeDateTimeNS(fake_now))
        task = {"id": 3, "tenant_key": "tk1", "type": "douyin",
                "chat_id": "oc1", "credits_cost": 0,
                "payload": json.dumps({"auto": 1})}
        w._fail(task, "网络抖动", exc=RuntimeError("网络抖动"))
        assert not sent, "夜间 auto 失败不许推达人"

    def test_daytime_other_fail_gets_delay_text(self, monkeypatch):
        from daren_bot import worker
        w, sent = _make_worker(monkeypatch)
        fake_now = type("T", (), {"hour": 10})()
        monkeypatch.setattr(worker, "datetime",
                            _FakeDateTimeNS(fake_now))
        task = {"id": 4, "tenant_key": "tk1", "type": "douyin",
                "chat_id": "oc1", "credits_cost": 0,
                "payload": json.dumps({"auto": 1})}
        w._fail(task, "网络抖动", exc=RuntimeError("网络抖动"))
        assert sent and "我再试一次" not in sent[0][1], \
            "auto 失败白天也不许说「我再试一次」"
        assert "运营在处理中" in sent[0][1]

    def test_manual_fail_unaffected(self, monkeypatch):
        # 非 auto（达人主动发起）失败话术不受 B6 影响
        w, sent = _make_worker(monkeypatch)
        task = {"id": 5, "tenant_key": "tk1", "type": "douyin",
                "chat_id": "oc1", "credits_cost": 100, "payload": ""}
        w._fail(task, "查询超时", exc=RuntimeError("查询超时"))
        assert sent, "达人主动发起的任务失败照常告知"


class TestWeeklyNoRetryOnLoginConflict:
    def test_retry_skipped_on_login(self, monkeypatch):
        from daren_bot import weekly
        sched = weekly.WeeklyScheduler.__new__(weekly.WeeklyScheduler)
        monkeypatch.setattr(weekly.db, "count_auto_fails_this_week",
                            lambda *a, **k: 1)
        monkeypatch.setattr(weekly.db, "get_latest_chat_id", lambda tk: "oc1")
        monkeypatch.setattr(sched, "_last_fail_kind",
                            lambda *a, **k: "need_login")
        enqueued = []
        monkeypatch.setattr(weekly.credits, "charge_and_enqueue",
                            lambda *a, **k: enqueued.append(a) or ("ok", 1, 100))
        tenant = {"tenant_key": "tk1"}
        now = weekly.datetime.datetime(2026, 11, 3, 21, 0)
        w_start = now - weekly.datetime.timedelta(hours=3)
        sched._maybe_enqueue_collect(tenant, "dy1",
                                     int(w_start.timestamp()), now, w_start)
        assert not enqueued, "登录态失败不许排自动重试"

    def test_compensate_skipped_on_conflict(self, monkeypatch):
        from daren_bot import weekly
        sched = weekly.WeeklyScheduler.__new__(weekly.WeeklyScheduler)
        monkeypatch.setattr(weekly.db, "get_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(weekly.db, "count_auto_fails_this_week",
                            lambda *a, **k: 1)
        monkeypatch.setattr(weekly.db, "get_latest_chat_id", lambda tk: "oc1")
        monkeypatch.setattr(sched, "_last_fail_kind",
                            lambda *a, **k: "conflict")
        enqueued = []
        monkeypatch.setattr(weekly.credits, "charge_and_enqueue",
                            lambda *a, **k: enqueued.append(a) or ("ok", 1, 100))
        sched._maybe_compensate_collect({"tenant_key": "tk1"}, "dy1", 0)
        assert not enqueued, "409 失败不许排窗后补偿采集"


# ---------- B7 内部词外泄 ----------

class TestNoInternalWording:
    def test_handler_texts_no_cache_word(self):
        import inspect
        src = inspect.getsource(handler)
        assert "缓存命中，秒" not in src, "达人可见文案不许出现「缓存命中」"
        assert "（缓存命中" not in src


# ---------- B8 纠错免费 ----------

class TestIsCorrection:
    def test_hits(self):
        assert product_intent.is_correction("分析错了")
        assert product_intent.is_correction("不对啊")
        assert product_intent.is_correction("为啥呢 什么原因 还是说截图不清")

    def test_misses(self):
        # 正常提问不许被免费兜底吞掉
        assert not product_intent.is_correction("这个商品能带吗")
        assert not product_intent.is_correction("有什么建议")
        assert not product_intent.is_correction("为什么适合带食品")
        assert not product_intent.is_correction("")
        assert not product_intent.is_correction("帮我分析一下账号")

    def test_with_product_ref_not_correction(self):
        t = "这个不对 https://v.douyin.com/abc 你看看"
        assert not product_intent.is_correction(t)

    def test_qa_correction_deterministic_no_llm(self, monkeypatch):
        monkeypatch.setattr(dbmod, "get_tenant",
                            lambda tk: {"bind_douyin_id": ""})
        called = []
        monkeypatch.setattr(executors, "_qa_llm",
                            lambda p: called.append(p) or "x")
        out = executors.execute_qa({"tenant_key": "tk1", "target": "分析错了",
                                    "payload": json.dumps({"qa_correction": True})})
        assert "质疑" in out
        assert not called, "纠错兜底不调 LLM（零算力）"


# ---------- B9 多图 ----------

class TestPostMultiImage:
    def test_each_image_routed(self, monkeypatch):
        routed = []
        monkeypatch.setattr(handler.Router, "_route_image",
                            lambda self, tenant, msg, content, sender,
                            caption="": routed.append(content.get("image_key")))
        monkeypatch.setattr(handler, "_parse_post",
                            lambda c: ("手套和保温杯",
                                       ["img_k1", "img_k2"]))

        class _Sender:
            def send_text(self, chat_id, text):
                pass

        class _Wm:
            def get_sender(self, tk):
                return _Sender()

        router = handler.Router(ws_manager=_Wm())

        class _Msg:
            message_type = "post"
            chat_id = "oc1"
            content = json.dumps({"title": "", "content": []})

        class _Ev:
            class sender:
                class sender_id:
                    open_id = "ou1"
            message = _Msg()

        class _Data:
            event = _Ev()

        router._dispatch({"tenant_key": "tk1"}, _Data())
        assert routed == ["img_k1", "img_k2"], f"双图必须逐张走视觉链路：{routed}"
