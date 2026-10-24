"""商品事实解析（daren_cockpit/tools/product_facts.py）的口径测试。

锁住三条设计铁律（都是这轮踩坑换来的）：

1. **单一真源** —— 推荐指数和排品优先级读的是同一份佣金/价格口径，
   不能各写一套（分叉 ⇒ 同一商品在两个评分器里读出不同佣金 ⇒ 静默漂移）。
2. **缺失 ≠ 0** —— 取不到就是取不到，返回 0.0 让调用方退出加权，
   不拿 0 冒充真值（否则「没佣金」会被当成「佣金很差」扣分）。
3. **平台有两种佣金形态** —— 标量（`{"default": 20}`）和
   **分档**（`{"投放期": 10, "常规活动期": 5, "其他": 5}`）。
   只认标量会让 5/58 个商品的收益维度被静默丢掉（2026-10-24 实测）。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "product_facts.py")
spec = importlib.util.spec_from_file_location("product_facts", MOD_PATH)
pf = importlib.util.module_from_spec(spec)
sys.modules["product_facts"] = pf
spec.loader.exec_module(pf)


# ─────────────── 佣金率 ───────────────

@pytest.mark.parametrize("product,expect", [
    # ① 独立列
    ({"commission_rate": 12.5}, 12.5),
    # ② JSON 标量键
    ({"commission_rates": '{"default": 20.0}'}, 20.0),
    ({"commission_rates": '{"rate": 8}'}, 8.0),
    # ③ ★ 分档结构（没有 default！）→ 取最高档
    ({"commission_rates": '{"投放期": 10, "常规活动期": 5, "其他": 5}'}, 10.0),
    ({"commission_rates": '{"自然流": 20, "投放期": 8}'}, 20.0),
    # 标量键优先于分档
    ({"commission_rates": '{"default": 30, "投放期": 10}'}, 30.0),
    # ④ 都没有 ⇒ 缺失（0.0，**不是** 0 分真值）
    ({"commission_rates": '{}'}, 0.0),
    ({"commission_rates": None}, 0.0),
    ({}, 0.0),
    # 非法值不能炸
    ({"commission_rates": '{"default": "abc"}'}, 0.0),
    ({"commission_rate": "xyz"}, 0.0),
])
def test_commission_rate_of(product, expect):
    assert pf.commission_rate_of(product) == pytest.approx(expect)


def test_commission_note_marks_the_phase():
    """★ 分档佣金的理由行必须说清是哪个档位（否则「佣金率 10%」是误导）。"""
    note = pf.commission_note({"commission_rates": '{"投放期": 10, "常规活动期": 5}'})
    assert "10" in note and "投放期" in note
    assert pf.commission_note({"commission_rates": '{"default": 20}'}) == "佣金率 20%"


def test_commission_note_empty_when_missing():
    assert pf.commission_note({}) == ""
    assert pf.commission_note({"commission_rates": "{}"}) == ""


def test_commission_note_is_not_misleading_for_zero_rates():
    """平台真给 0 佣金时，仍按「缺失」处理（不显示「佣金率 0%」骗人）。"""
    assert pf.commission_rate_of({"commission_rates": '{"default": 0}'}) == 0.0
    assert pf.commission_note({"commission_rates": '{"default": 0}'}) == ""


# ─────────────── 到手价 ───────────────

@pytest.mark.parametrize("product,expect", [
    ({"price": 99}, 99.0),
    ({"price_range": "300-600"}, 450.0),
    ({"price_range": "¥300~600"}, 450.0),
    ({"price_range": "0-100元"}, 50.0),
    ({"price_range": "129"}, 129.0),
    ({"price": 0, "price_range": "300-600"}, 450.0),
    ({}, 0.0),
    ({"price_range": "abc"}, 0.0),
])
def test_price_of(product, expect):
    assert pf.price_of(product) == pytest.approx(expect)


def test_parse_price_range_orders_lo_hi():
    assert pf.parse_price_range("600-300") == (300.0, 600.0)
    assert pf.parse_price_range("") == (0.0, 0.0)
    assert pf.parse_price_range(None) == (0.0, 0.0)


# ─────────────── 两个评分器口径一致（防漂移）───

def test_reco_and_plan_read_the_same_commission():
    """★ 单一真源的真正目的：两个评分器对同一商品读出**同一个**佣金率。"""
    reco_path = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
                 / "product_reco.py")
    plan_path = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
                 / "product_plan.py")
    product = {"commission_rates": '{"投放期": 10, "常规活动期": 5}'}

    for path, name in ((reco_path, "product_reco"), (plan_path, "product_plan")):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        assert mod._commission_rate(product) == pytest.approx(10.0), \
            f"{name} 的佣金口径和单一真源不一致"
