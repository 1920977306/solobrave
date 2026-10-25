"""卡片「看我的排品表」的口径（2026-10-25 修）。

修的两个问题（都是体检时实测出来的）：

1. **只显示今天** ⇒ 按钮叫「排品表」却只给 1 天；而且和
   H5 `/api/plan`（7 天）、文字版 `plan_notify.plan_text`（今天及以后）
   **三处口径不一致** ⇒ 运营看到的、达人看到的、机器人算出来的不是同一张表。
2. **卡片替换把「接这个品」按钮弄没了** ⇒ 点完「看我的排品表」，
   原卡被替换，达人就接不了那个品了（闭环断在最后一步）。
   修法：`view_plan` 按钮把 `product_id` 带过去，排品表卡上再放一个「接这个品」。

★ 「同一份事实只能有一个真源」：过滤必须走共用的 `plan.future_slots`，
  不许在 handler 里另写一套（另写 = 迟早跟 H5 漂移）。
"""
import datetime
import importlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fc = importlib.import_module("daren_bot.feishu_card")
plan_notify = importlib.import_module("daren_bot.plan_notify")
db = importlib.import_module("daren_bot.db")
handler_mod = importlib.import_module("daren_bot.handler")
plan = importlib.import_module("daren_cockpit.tools.product_plan")


# ─────────────── 夹具 ───────────────

def _d(offset_days: int) -> str:
    return (datetime.datetime.now()
            + datetime.timedelta(days=offset_days)).strftime("%Y-%m-%d")


def _item(name: str, slot: int = 1, price: float = 55.0, comm: float = 10.0):
    return plan.PlanItem(product_id="p_" + name, product_name=name, plan_slot=slot,
                          price=price, commission_rate=comm,
                          lifecycle=plan.Lifecycle(stage="ramp"))


def _slots() -> list:
    """过去 1 天 + 今天 + 明天（2 个坑）+ 后天。"""
    return [
        plan.PlanSlot(date=_d(-2), items=[_item("过期品")]),
        plan.PlanSlot(date=_d(0), items=[_item("今天的品", 1)]),
        plan.PlanSlot(date=_d(1), items=[_item("明天的品", 1), _item("明天第二个", 2)]),
        plan.PlanSlot(date=_d(2), items=[_item("后天品", 1)]),
    ]


def _card_text(card: dict) -> str:
    """把卡片里所有 lark_md / plain_text 文本拼起来。"""
    out = []
    for el in card.get("elements", []):
        txt = el.get("text")
        if isinstance(txt, dict) and txt.get("content"):
            out.append(str(txt["content"]))
        for sub in el.get("elements", []) or []:
            if isinstance(sub, dict) and sub.get("content"):
                out.append(str(sub["content"]))
    return "\n".join(out)


def _buttons(card: dict) -> list:
    for el in card.get("elements", []):
        if el.get("tag") == "action":
            return el.get("actions", [])
    return []


@pytest.fixture
def plan_env(monkeypatch):
    """把达人身份和排品表都固定住（不碰生产库）。"""
    monkeypatch.setattr(db, "get_latest_douyin_id", lambda tk: "dy_test")
    monkeypatch.setattr(db, "get_tenant", lambda tk: {})
    monkeypatch.setattr(
        plan_notify, "plan_for_talent",
        lambda dyid, talent_id="": (_slots(), {"id": "tal_x", "name": "七月姐姐"}))
    return handler_mod.Router(None)


# ─────────────── 显示口径 ───────────────

def test_view_plan_shows_today_and_future(plan_env):
    """★ 不许只显示今天 —— 今天及以后都要给。"""
    r = plan_env._card_view_plan("tk_test", "oc_x")
    assert "card" in r, r
    text = _card_text(r["card"]["data"])
    assert _d(0) in text, "今天的排品没显示"
    assert _d(1) in text, "明天的排品没显示（按钮叫「排品表」却只给今天）"
    assert _d(2) in text, "后天的排品没显示"
    assert "过期品" not in text, "历史排期不该出现在给达人看的表里"


def test_view_plan_uses_shared_future_filter():
    """★ 过滤必须走共用的 `plan.future_slots`（别自己写一套）。"""
    src = inspect.getsource(handler_mod.Router._card_view_plan)
    assert "future_slots" in src, \
        "排品表卡必须用共用的 future_slots 过滤（另写一套 = 迟早跟 H5 漂移）"


def test_handler_has_no_today_only_filter():
    """★ 静态：`_card_view_plan` 里不许再出现「只筛今天」的写法。"""
    src = inspect.getsource(handler_mod.Router._card_view_plan)
    assert "s.date != today" not in src, "还在只筛 today（按钮名不副实的老毛病）"


# ─────────────── 接品闭环 ───────────────

def test_view_plan_keeps_accept_button(plan_env):
    """★ 卡片被替换后，原卡上的「接这个品」不能丢。"""
    r = plan_env._card_view_plan("tk_test", "oc_x", "3840201369122373702")
    buttons = _buttons(r["card"]["data"])
    labels = [b["text"]["content"] for b in buttons]
    assert "接这个品" in labels, f"排品表卡上没保留「接这个品」按钮：{labels}"
    acc = [b for b in buttons if b["text"]["content"] == "接这个品"][0]
    assert acc["value"]["action"] == "accept_deal"
    assert acc["value"]["product_id"] == "3840201369122373702"


def test_view_plan_has_no_dead_button(plan_env):
    """★ 没有 product_id 时不挂空按钮；也不留「点了还是同一张卡」的死按钮。"""
    r = plan_env._card_view_plan("tk_test", "oc_x")
    labels = [b["text"]["content"] for b in _buttons(r["card"]["data"])]
    assert "接这个品" not in labels
    assert "看我的排品表" not in labels, "留了个点了还是同一张卡的死按钮"
    assert "看明天推什么" not in labels, "留了个点了还是同一张卡的死按钮"


def test_product_card_view_plan_button_carries_product_id():
    """★ 商品分析卡的「看我的排品表」必须把 product_id 带过去。"""
    c = fc.product_card("品A", "🟢 能带", fc.TPL_GREEN, "能带", product_id="PID123")
    vp = [b for b in _buttons(c) if b["text"]["content"] == "看我的排品表"][0]
    assert vp["value"]["action"] == "view_plan"
    assert vp["value"]["product_id"] == "PID123", \
        "view_plan 按钮没带 product_id ⇒ 排品表卡上接不回那个品"


def test_product_card_without_product_id_still_has_view_plan():
    """★ 没有商品 id 时「看我的排品表」照旧要在（只是不带 id）。"""
    c = fc.product_card("品A", "🟢 能带", fc.TPL_GREEN, "能带")
    vp = [b for b in _buttons(c) if b["text"]["content"] == "看我的排品表"][0]
    assert vp["value"] == {"action": "view_plan"}
