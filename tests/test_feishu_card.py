"""飞书卡片（daren_bot/feishu_card.py）的测试。

锁住三条**实测约束**（2026-10-24 逐个试错得来的）：
1. 只支持**卡片 1.0**（2.0 的 action/behaviour 都被飞书拒）
2. **不支持任何折叠组件**（collapsible / post.fold 都被拒）
   ⇒ 「把长文字收起来」只能靠「短卡片 + 长图」
3. 按钮回调参数在 `value` 里，回调拿到的是 **dict** 不是 JSON 字符串
"""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

fc = importlib.import_module("daren_bot.feishu_card")


def test_card_is_v1_schema():
    """★ 1.0：elements 在顶层，**不能**有 schema=2.0（2.0 会被飞书拒）。"""
    c = fc.product_card("品A", "🟡 慎带", fc.TPL_ORANGE, "价格偏高")
    assert "schema" not in c
    assert c["config"]["wide_screen_mode"] is True
    assert c["header"]["template"] == "orange"
    assert isinstance(c["elements"], list)


def test_buttons_use_action_element_with_value():
    """★ 按钮在 `{"tag":"action"}` 里，回调参数放 `value`。"""
    c = fc.product_card("品A", "🟢 能带", fc.TPL_GREEN, "能带",
                        product_id="3840201369122373702")
    acts = [e for e in c["elements"] if e.get("tag") == "action"]
    assert len(acts) == 1
    buttons = acts[0]["actions"]
    labels = [b["text"]["content"] for b in buttons]
    assert "接这个品" in labels and "看我的排品表" in labels
    accept = [b for b in buttons if b["text"]["content"] == "接这个品"][0]
    assert accept["type"] == "primary"
    # 回调参数：必须是 dict（飞书回传时 action.value 是 dict，不是 JSON 字符串）
    assert isinstance(accept["value"], dict)
    assert accept["value"]["action"] == "accept_deal"
    assert accept["value"]["product_id"] == "3840201369122373702"


def test_no_collapsible_element():
    """★ 这个飞书应用**不支持折叠组件** ⇒ 卡片里不许出现 collapsible。"""
    c = fc.product_card("品A", "🟢 能带", fc.TPL_GREEN, "能带")
    tags = {e.get("tag") for e in c["elements"]}
    assert "collapsible" not in tags


def test_to_content_is_json_string():
    """msg_type=interactive 的 content 字段是**字符串**。"""
    s = fc.to_content(fc.plan_card("名", "标题", ["**坑位 1** 品A"]))
    assert isinstance(s, str)
    d = json.loads(s)
    assert d["header"]["title"]["content"] == "标题"
