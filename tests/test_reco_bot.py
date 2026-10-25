"""飞书「选品推荐」链路（2026-10-25 老大目标 3：自算推荐指数接进飞书）。

锁住四件事：
1. **意图判据要准** —— 宁可漏（回落 LLM 问答），不可抢
   （抢了会把「这个品能不能带」这种要**真值**的问题变成一份候选清单）。
2. **零成本** —— 这条链路不调 LLM、不扣积分（纯查库 + 纯函数算）。
3. **只丢不造** —— 画像不足 / 算不出就如实说，不给一份编出来的推荐。
4. **闭环** —— 推荐卡上的「接这个品」点一下就进排品池。
"""
import importlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fc = importlib.import_module("daren_bot.feishu_card")
handler_mod = importlib.import_module("daren_bot.handler")
plan_notify = importlib.import_module("daren_bot.plan_notify")
db = importlib.import_module("daren_bot.db")
reco_source = importlib.import_module("daren_cockpit.tools.reco_source")


# ─────────────── 夹具 ───────────────

class FakeSender:
    def __init__(self):
        self.cards = []
        self.texts = []

    def send_card(self, chat_id, card):
        self.cards.append(card)
        return "om_test"

    def send_text(self, chat_id, text):
        self.texts.append(text)


def _rec(pid, name, stars, price=55.0, comm=20.0, dims=None):
    return {
        "product_id": pid, "name": name, "price": price, "stars": stars,
        "strategy": "高性价比服装/袜子爆品",
        "reasons": ["¥40 落在策略价带 ¥60~¥150"],
        "dims": dims if dims is not None else [
            {"key": "commission_rate", "label": "佣金率", "raw": comm,
             "score": 0.9, "note": f"佣金率 {comm:g}%"},
            {"key": "shop_score", "label": "店铺体验分", "raw": 92.0,
             "score": 0.9, "note": "店铺体验分 92"},
            {"key": "good_ratio", "label": "好评率", "raw": 93.4,
             "score": 0.9, "note": "好评率 93.4%"},
            {"key": "price_fit", "label": "到手价契合度", "raw": price,
             "score": 1.0, "note": "¥40 落在策略价带 ¥60~¥150"},
        ],
    }


# ─────────────── ① 意图判据 ───────────────

@pytest.mark.parametrize("text", [
    "推荐点什么", "推荐几个品", "推荐", "选品", "选品建议",
    "有什么推荐", "有啥推荐", "推荐指数", "推荐给我几个品", "推荐点商品",
    "帮我推荐", "推荐一下", "推荐几个商品",
])
def test_reco_intent_hits(text):
    assert handler_mod.is_reco_query(text), f"漏了「{text}」"


@pytest.mark.parametrize("text", [
    "今天推什么", "这周推什么", "排品表", "我的排品",      # 排品表，不是推荐
    "我适合带食品吗", "这个链接能带吗", "商品能带吗",       # 商品判断
    "推荐个平价好用的粉底液",                              # 让模型找货，不是要清单
    "推荐一下这个商品", "这个品值得带吗",                  # 点名了具体商品
    # ★ 既点名了具体商品、**又**带范围词 —— 专门覆盖「排除具体商品」那条守卫
    #   （没有范围词时，光靠范围词就能挡住，测不出这条守卫的作用）
    "这个商品有什么推荐", "这个品有什么推荐",
    "接了 3840201369122373702", "余额", "你好",
    "帮我分析下我的账号",
])
def test_reco_intent_misses(text):
    assert not handler_mod.is_reco_query(text), f"抢了「{text}」"


def test_reco_intent_ignores_too_long_text():
    """★ 超过 24 字的多半是在讲事情，不是在要清单。"""
    assert not handler_mod.is_reco_query("推荐" + "啊" * 40)


# ─────────────── ② 卡片 ───────────────

def test_reco_card_renders_stars_price_and_commission():
    c = fc.reco_card("**七月姐姐**", [_rec("p1", "哟酷酷美式AA杯垫", 5)])
    text = "\n".join(str(e.get("text", {}).get("content", ""))
                     for e in c["elements"] if isinstance(e.get("text"), dict))
    assert "★★★★★" in text, "没画星级"
    assert "¥55" in text, "没显示价格"
    assert "佣金20%" in text, "没显示佣金率"
    assert "**七月姐姐**" in text, "没显示达人画像行"


def test_reco_card_prefers_business_reason_over_internal_jargon():
    """★ 理由要达人看得懂的（店铺体验分/好评率），
    不要「¥40 落在策略价带 ¥60~¥150」这种内部话术。"""
    c = fc.reco_card("x", [_rec("p1", "品A", 4)])
    text = "\n".join(str(e.get("text", {}).get("content", ""))
                     for e in c["elements"] if isinstance(e.get("text"), dict))
    assert "店铺体验分" in text or "好评率" in text
    assert "策略价带" not in text, "把内部话术当理由给达人了"


def test_reco_card_buttons_carry_accept_deal():
    """★ 「接这个品」按钮必须带商品 id ⇒ 点一下就进排品池（闭环）。"""
    c = fc.reco_card("x", [_rec("prod_abc", "哟酷酷", 5)])
    acts = [e for e in c["elements"] if e.get("tag") == "action"]
    assert len(acts) == 1, "按钮区应该只有一个 action 元素"
    buttons = acts[0]["actions"]
    assert len(buttons) == 1
    b = buttons[0]
    assert b["value"]["action"] == "accept_deal"
    assert b["value"]["product_id"] == "prod_abc"


def test_reco_card_button_labels_are_distinguishable():
    """★ 三个按钮不能都叫「接这个品」—— 达人分不清哪个对哪个品。"""
    c = fc.reco_card("x", [_rec("p1", "马卡龙运动款", 5),
                           _rec("p2", "温途云绒暖感", 4),
                           _rec("p3", "哟酷酷内搭", 3)])
    buttons = [e for e in c["elements"] if e.get("tag") == "action"][0]["actions"]
    labels = [b["text"]["content"] for b in buttons]
    assert len(set(labels)) == len(labels), f"按钮标签重复了：{labels}"


def test_reco_card_label_has_no_broken_bracket():
    """★ 截断别留半个括号（「接 马卡龙 运动款【」很难看）。"""
    c = fc.reco_card("x", [_rec("p1", "马卡龙 运动款【16双盒装】10A抗菌", 5)])
    label = [e for e in c["elements"] if e.get("tag") == "action"][0]["actions"][0]
    assert "【" not in label["text"]["content"]
    assert "】" not in label["text"]["content"]


# ─────────────── ③ 路由三个分支 ───────────────

def test_route_reco_without_douyin_id_asks_for_it(monkeypatch):
    monkeypatch.setattr(db, "get_latest_douyin_id", lambda tk: "")
    s = FakeSender()
    handler_mod.Router(None)._route_reco_query(
        {"tenant_key": "tk_x", "bind_douyin_id": ""}, "oc_x", s)
    assert s.texts and "抖音号" in s.texts[0]
    assert not s.cards, "没抖音号就不该发卡"


def test_route_reco_without_profile_is_honest(monkeypatch):
    """★ 只丢不造：画像不足就如实说「算不出」，不给编出来的推荐。"""
    monkeypatch.setattr(db, "get_latest_douyin_id", lambda tk: "dy_x")
    monkeypatch.setattr(plan_notify, "read_talent", lambda dyid, talent_id="":
                        {"id": "tal_x", "name": "某达人"})
    monkeypatch.setattr(reco_source, "build_chain",
                        lambda talent, top_n=6, db_path=None: {
                            "profile": {"name": "某达人"},
                            "recommendations": [],
                            "notes": ["画像不足以生成类目策略（缺：粉丝年龄）—— 不硬编策略"]})
    s = FakeSender()
    handler_mod.Router(None)._route_reco_query(
        {"tenant_key": "tk_x", "bind_douyin_id": "dy_x"}, "oc_x", s)
    assert s.texts, "算不出时该回一句话"
    assert not s.cards, "算不出时不该发一张空卡"
    assert "算不出" in s.texts[0]


def test_route_reco_sends_card_with_recommendations(monkeypatch):
    monkeypatch.setattr(db, "get_latest_douyin_id", lambda tk: "dy_x")
    monkeypatch.setattr(plan_notify, "read_talent", lambda dyid, talent_id="":
                        {"id": "tal_x", "name": "七月姐姐"})
    monkeypatch.setattr(reco_source, "build_chain",
                        lambda talent, top_n=6, db_path=None: {
                            "profile": {"name": "七月姐姐", "main_gender": "女",
                                        "main_age": "24-30", "price_center": 75.0,
                                        "categories": ["服装", "个护家清"]},
                            "recommendations": [_rec("prod_1", "哟酷酷内搭", 5)],
                            "notes": []})
    s = FakeSender()
    handler_mod.Router(None)._route_reco_query(
        {"tenant_key": "tk_x", "bind_douyin_id": "dy_x"}, "oc_x", s)
    assert s.cards, "有推荐就该发卡"
    c = s.cards[0]
    text = "\n".join(str(e.get("text", {}).get("content", ""))
                     for e in c["elements"] if isinstance(e.get("text"), dict))
    assert "七月姐姐" in text and "女" in text and "24-30" in text
    assert c["header"]["title"]["content"] == "⭐ 给你的选品推荐"


def test_route_reco_is_zero_cost(monkeypatch):
    """★ 零成本：这条链路不许调 LLM、不许扣积分。

    验法：`plan_notify.read_talent` 之外的**任何**出网/入队调用都会让测试挂掉
    —— 这里断言它只读库、只算函数。
    """
    src = inspect.getsource(handler_mod.Router._route_reco_query)
    for forbidden in ("enqueue_task", "charge", "deduct", "llm_channels",
                      "call_openai", "call_anthropic"):
        assert forbidden not in src, f"推荐链路里不该出现「{forbidden}」（要零成本）"
