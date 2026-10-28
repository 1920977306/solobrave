# -*- coding: utf-8 -*-
"""飞书助理平台化 · 商务角色分流 守卫（2026-10-28，老大拍板）

背景：达人助理扩成「飞书助理」项目——一个达人一个助理、一个商务一个助理。
本文件守住商务角色（role=commerce）的五条新链路：
  ① 租户表 role 列：迁移幂等、add_tenant 落库、缺省 daren（存量零感知）
  ② persona 按角色分文件：商务问答注入商务人格，不串达人话术
  ③ handler 分流：商务发抖音号=分析该达人（不绑定）；达人专属路由
     （周报订阅/排品/佣金/推荐/驾驶舱）对商务关闭，落回 QA
  ④ executors：商务问答注入问题里点到达人的真实档案（查达人数据）
  ⑤ weekly 调度：非达人租户永远不推经营周报（防烧算力）

★ 每条守卫都按 §16 Y 第 4 条执行「能不能红」自检（注释里标对照）。
"""

import importlib
import json
import os
import sys
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TK_DAREN = "tk_role_daren"
TK_COMM = "tk_role_commerce"
DYID = "Ayn11930"


@pytest.fixture()
def bot(tmp_path, monkeypatch):
    """隔离环境（照抄 tests/test_image_intake.py 的口径）。"""
    monkeypatch.setenv("DAREN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DAREN_DB", str(tmp_path / "daren_bot.db"))
    monkeypatch.delenv("DAREN_USE_KIMI", raising=False)
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    import daren_bot.handler as hd
    import daren_bot.executors as ex
    import daren_bot.worker as wk
    import daren_bot.weekly as wly
    for m in (cfg, ddb, hd, ex, wk, wly):
        importlib.reload(m)
    ddb.init_db()
    ddb.grant(TK_DAREN, 1000)
    ddb.grant(TK_COMM, 1000)
    return {"db": ddb, "handler": hd, "executors": ex, "worker": wk,
            "weekly": wly, "config": cfg}


def _add(bot, tk, role):
    bot["db"].add_tenant(f"app_{tk}", tk, "enc", role=role)


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


# ---------------- ① 租户 role 列 ----------------

class TestTenantRole:
    def test_default_role_is_daren(self, bot):
        _add(bot, TK_DAREN, "daren")
        assert bot["db"].get_tenant(TK_DAREN)["role"] == "daren"

    def test_commerce_role_persisted(self, bot):
        _add(bot, TK_COMM, "commerce")
        assert bot["db"].get_tenant(TK_COMM)["role"] == "commerce"
        # get_active_tenants 也要带 role（weekly/handler 都读这份）
        roles = {t["tenant_key"]: t.get("role") for t in bot["db"].get_active_tenants()}
        assert roles[TK_COMM] == "commerce"

    def test_upsert_keeps_role(self, bot):
        """同 app_id 重复开通：role 以最新一次为准（幂等可重跑）。"""
        _add(bot, TK_COMM, "commerce")
        bot["db"].add_tenant(f"app_{TK_COMM}", TK_COMM, "enc2", role="commerce")
        assert bot["db"].get_tenant(TK_COMM)["role"] == "commerce"


# ---------------- ② persona 按角色 ----------------

class TestPersonaByRole:
    def test_commerce_persona_content(self, bot):
        t = bot["executors"].persona.qa_persona("commerce")
        assert "商务" in t and "建联" in t
        assert "选品顾问" not in t  # 不串达人人格

    def test_daren_persona_unchanged(self, bot):
        t = bot["executors"].persona.qa_persona("")
        assert "选品顾问" in t
        assert "建联" not in t

    def test_unknown_role_falls_back_daren(self, bot, monkeypatch):
        """角色文件不存在 ⇒ 回退 daren 模板（不炸、不裸奔）。"""
        t = bot["executors"].persona.qa_persona("merchant")
        assert "选品顾问" in t

    def test_role_path_sanitized(self, bot):
        """角色名白化：路径穿越字符被剥掉（../../x ⇒ 安全名）。"""
        p = bot["executors"].persona._role_path("../../etc")
        assert ".." not in str(p)


# ---------------- ③ handler 分流 ----------------

class TestCommerceRouting:
    def _spy_enqueue(self, bot, monkeypatch):
        seen = []
        orig = bot["db"].enqueue_task_charged

        def spy(*a, **kw):
            seen.append((a, kw))
            return orig(*a, **kw)

        monkeypatch.setattr(bot["db"], "enqueue_task_charged", spy)
        return seen

    def test_commerce_douyin_no_bind(self, bot, monkeypatch):
        """商务发抖音号 = 分析这个达人：入队采集、**永不绑定**、
        话术不喊「你的账号」。
        （对照：commerce 早退分支删了 ⇒ 走达人逻辑把号绑上 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        router, s = _router(bot["handler"], _FakeSender())
        seen = self._spy_enqueue(bot, monkeypatch)
        tenant = bot["db"].get_tenant(TK_COMM)
        router._route_text(tenant, "chat_1", DYID, s)
        assert seen, "商务发抖音号没有入队采集任务"
        assert seen[0][0][1] == "douyin"
        assert bot["db"].get_tenant(TK_COMM)["bind_douyin_id"] == ""
        assert "你的账号" not in s.text and DYID in s.text

    def test_daren_douyin_still_binds(self, bot, monkeypatch):
        """回归：达人发抖音号仍走自动绑定（commerce 改动不能碰达人链路）。"""
        _add(bot, TK_DAREN, "daren")
        router, s = _router(bot["handler"], _FakeSender())
        self._spy_enqueue(bot, monkeypatch)
        tenant = bot["db"].get_tenant(TK_DAREN)
        router._route_text(tenant, "chat_1", DYID, s)
        assert bot["db"].get_tenant(TK_DAREN)["bind_douyin_id"] == DYID

    def test_commerce_weekly_sub_falls_to_qa(self, bot, monkeypatch):
        """「订阅周报」是达人专属：商务发 ⇒ 落 QA，绝不写 weekly_sub。
        （对照：is_daren 护栏删了 ⇒ 订阅成功 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        router, s = _router(bot["handler"], _FakeSender())
        seen = self._spy_enqueue(bot, monkeypatch)
        tenant = bot["db"].get_tenant(TK_COMM)
        router._route_text(tenant, "chat_1", "订阅周报", s)
        assert seen and seen[0][0][4] == "qa", f"商务的订阅周报应落 QA：{seen!r}"
        assert bot["db"].get_weekly_sub(TK_COMM) == 1  # 未被改写（默认订阅位原样）

    def test_daren_weekly_sub_still_works(self, bot, monkeypatch):
        """回归：达人订阅周报照常生效。"""
        _add(bot, TK_DAREN, "daren")
        bot["db"].set_weekly_sub(TK_DAREN, 0)
        router, s = _router(bot["handler"], _FakeSender())
        self._spy_enqueue(bot, monkeypatch)
        tenant = bot["db"].get_tenant(TK_DAREN)
        router._route_text(tenant, "chat_1", "订阅周报", s)
        assert bot["db"].get_weekly_sub(TK_DAREN) == 1

    def test_commerce_greeting(self, bot, monkeypatch):
        _add(bot, TK_COMM, "commerce")
        router, s = _router(bot["handler"], _FakeSender())
        tenant = bot["db"].get_tenant(TK_COMM)
        router._route_text(tenant, "chat_1", "你好", s)
        assert "商务助理" in s.text

    def test_commerce_balance_mentions_commerce_pricing(self, bot, monkeypatch):
        _add(bot, TK_COMM, "commerce")
        router, s = _router(bot["handler"], _FakeSender())
        tenant = bot["db"].get_tenant(TK_COMM)
        router._route_text(tenant, "chat_1", "余额", s)
        assert "查达人账号分析 100 分" in s.text

    def test_commerce_commission_not_intercepted(self, bot, monkeypatch):
        """专属佣金重算是达人链路：商务报百分号 ⇒ 落 QA。
        （对照：is_daren 护栏删了 ⇒ _route_commission 可能拦 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        router, s = _router(bot["handler"], _FakeSender())
        seen = self._spy_enqueue(bot, monkeypatch)
        tenant = bot["db"].get_tenant(TK_COMM)
        router._route_text(tenant, "chat_1", "这个达人佣金有20%吗", s)
        assert seen and seen[0][0][4] == "qa"


# ---------------- ④ executors：商务查达人档案注入 ----------------

class TestCommerceTalentBlock:
    def _fake_talent(self, dyid):
        return {"name": "测试达人", "douyin_id": dyid, "fans": 12804,
                "notes": "潜力型｜女粉83%"}

    def test_mentioned_dyid_injected(self, bot, monkeypatch):
        ex = bot["executors"]
        monkeypatch.setattr(ex, "_read_talent",
                            lambda _uid, dyid: self._fake_talent(dyid))
        block = ex._commerce_talent_block(f"帮我看看 {DYID} 这个达人能带家居吗")
        assert DYID in block and "测试达人" in block

    def test_no_dyid_no_block(self, bot):
        assert bot["executors"]._commerce_talent_block("怎么跟达人开口谈佣金") == ""

    def test_lookup_capped(self, bot, monkeypatch):
        """最多注入 3 个档案：问题里塞 6 个号也只出 3 个。"""
        ex = bot["executors"]
        monkeypatch.setattr(ex, "_read_talent",
                            lambda _uid, dyid: self._fake_talent(dyid))
        ids = [f"TestId{i:03d}" for i in range(6)]
        block = ex._commerce_talent_block("对比 " + " ".join(ids))
        assert block.count("【达人 ") == 3

    def test_qa_prompt_commerce_persona_and_block(self, bot, monkeypatch):
        """execute_qa：商务租户 ⇒ prompt 用商务人格 + 达人档案块 + 【商务问】。
        （对照：prompt 里 commerce_line 删了 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        ex = bot["executors"]
        monkeypatch.setattr(ex, "_read_talent",
                            lambda _uid, dyid: self._fake_talent(dyid))
        captured = {}
        monkeypatch.setattr(ex, "_qa_llm",
                            lambda prompt: captured.setdefault("p", prompt)
                            or "答：可以建联")
        task = {"tenant_key": TK_COMM,
                "target": f"{DYID} 这个达人什么水平", "chat_id": "", "payload": ""}
        ex.execute_qa(task)
        p = captured.get("p", "")
        assert "商务助理" in p            # 商务人格
        assert "【系统指令】" in p and DYID in p and "测试达人" in p
        assert "【商务问】" in p
        assert "【达人问】" not in p

    def test_qa_prompt_daren_untouched(self, bot, monkeypatch):
        """回归：达人租户 QA prompt 不含商务指令块。"""
        _add(bot, TK_DAREN, "daren")
        ex = bot["executors"]
        captured = {}
        monkeypatch.setattr(ex, "_qa_llm",
                            lambda prompt: captured.setdefault("p", prompt)
                            or "答：能带")
        task = {"tenant_key": TK_DAREN, "target": "这个品适合我吗",
                "chat_id": "", "payload": ""}
        ex.execute_qa(task)
        p = captured.get("p", "")
        assert "【达人问】" in p
        assert "提问的是商务人员" not in p
        assert "【商务问】" not in p

    def test_commerce_bound_talent_not_injected(self, bot, monkeypatch):
        """商务租户即便误绑了抖音号，QA 也不注入「本人账号画像」（那是达人语境）。
        （对照：executors 里 commerce ctx 清零删了 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        bot["db"].bind_douyin_id(TK_COMM, DYID)
        ex = bot["executors"]
        monkeypatch.setattr(ex, "_read_talent",
                            lambda _uid, dyid: {"name": "误绑达人", "douyin_id": dyid})
        captured = {}
        monkeypatch.setattr(ex, "_qa_llm",
                            lambda prompt: captured.setdefault("p", prompt)
                            or "答：可以建联")
        task = {"tenant_key": TK_COMM, "target": "这个月建联话术怎么写",
                "chat_id": "", "payload": ""}
        ex.execute_qa(task)
        assert "【该达人的账号数据】" not in captured.get("p", "")


# ---------------- ⑤ weekly 调度跳过非达人租户 ----------------

class TestWeeklySkipsCommerce:
    def test_commerce_never_weekly(self, bot, monkeypatch):
        """_tick 扫到商务租户（即便有绑定号/默认订阅）⇒ 三个编排函数一个都不调。
        （对照：weekly.py 里 role 护栏删了 ⇒ 本用例红）"""
        _add(bot, TK_COMM, "commerce")
        bot["db"].bind_douyin_id(TK_COMM, DYID)   # 故意制造会触发的条件
        wly = bot["weekly"]
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_enqueue_collect",
                            lambda *a, **kw: pytest.fail("商务不该触发周采集"))
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_compensate_collect",
                            lambda *a, **kw: pytest.fail("商务不该触发补采"))
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_enqueue_report",
                            lambda *a, **kw: pytest.fail("商务不该触发周报"))
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_ops_alert",
                            lambda *a, **kw: None)
        sched = wly.WeeklyScheduler(wm=None)
        sched._tick()   # 直接跑一轮编排扫描，不应 fail

    def test_daren_still_gets_weekly(self, bot, monkeypatch):
        """回归：达人租户的周报编排照常会被评估（调用链不被护栏挡住）。"""
        _add(bot, TK_DAREN, "daren")
        bot["db"].bind_douyin_id(TK_DAREN, DYID)
        wly = bot["weekly"]
        called = {"n": 0}
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_enqueue_report",
                            lambda *a, **kw: called.__setitem__("n", called["n"] + 1))
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_enqueue_collect",
                            lambda *a, **kw: None)
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_compensate_collect",
                            lambda *a, **kw: None)
        monkeypatch.setattr(wly.WeeklyScheduler, "_maybe_ops_alert",
                            lambda *a, **kw: None)
        sched = wly.WeeklyScheduler(wm=None)
        sched._tick()
        assert called["n"] >= 1
