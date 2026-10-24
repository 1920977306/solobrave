"""飞书交互卡片（**卡片 1.0**）。

★ 实测约束（2026-10-24 逐个试错得来的，别再乱试）：
  ① 这个飞书应用**只支持卡片 1.0** ——
     2.0 的 `action` 元素报 "cards of schema V2 no longer support this capability"，
     2.0 的 `button.behaviour` 报 "unknown property: behaviour"。
     ⇒ 一律用 1.0：`elements` 在**顶层**、按钮在 `{"tag":"action"}` 里、
     按钮回调参数用 `value`。
  ② **不支持任何折叠组件** ——
     `collapsible`（1.0/2.0 都试过）报 "unsupported type of block"，
     post 富文本的 `fold` 报 "message_content has wrong tag:{fold}"。
     ⇒ 「把长文字收起来」只能靠：
       · **短卡片 + 长图**（图里放全文，卡片只放结论 + 按钮）—— 本模块的做法
       · 或「点按钮 → 回调 PATCH 同一张卡展开」（见 `daren_bot/card_action.py`）

③ 卡片按钮要**点了有反应**，必须注册 `card.action.trigger` 回调。
   走**长连接**（`register_p2_card_action_trigger`）⇒ 不需要公网地址，
   也不用在飞书后台填「卡片请求网址」。
"""
from __future__ import annotations

import json

# 卡片配色（header.template）
TPL_GREEN = "green"
TPL_ORANGE = "orange"
TPL_RED = "red"
TPL_BLUE = "blue"
TPL_GREY = "grey"


def _md(text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": text or ""}}


def _note(text: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": text or ""}]}


def _hr() -> dict:
    return {"tag": "hr"}


def _button(text: str, value: dict, type_: str = "default") -> dict:
    """一个按钮。`value` 会原样回传给卡片回调（card.action.trigger）。"""
    return {"tag": "button", "text": {"tag": "plain_text", "content": text},
            "type": type_, "value": value}


def _action(buttons: list) -> dict:
    return {"tag": "action", "actions": buttons}


def card(title: str, elements: list, subtitle: str = "",
         template: str = TPL_GREEN, wide: bool = True) -> dict:
    """卡片 1.0 骨架。

    ⚠️ 1.0 的 header **只有 title**（没有 subtitle 字段），
       且 title 是单行 ⇒ 副信息不能拼进 title（会折行、很难看），
       要放就放 body 或 note。
    """
    header = {"template": template,
              "title": {"tag": "plain_text", "content": title or ""}}
    return {"config": {"wide_screen_mode": wide},
            "header": header,
            "elements": [e for e in elements if e]}


def product_card(product_name: str, verdict: str, verdict_tpl: str,
                 summary: str, detail_lines: list = None,
                 product_id: str = "", plan_hint: str = "") -> dict:
    """商品分析卡：结论 + 关键数字 + 按钮。

    ★ 详细匹配分析**不放卡里**（卡片不支持折叠，长文铺开会很难看）——
      详细分析走长图，卡片只留「结论 + 数字 + 动作」。
    """
    els = []
    body_md = ""
    if verdict:
        body_md += f"**{verdict}**\n"
    if summary:
        body_md += summary
    els.append(_md(body_md.strip()))
    if detail_lines:
        els.append(_hr())
        els.append(_md("\n".join(detail_lines)))
    buttons = []
    if product_id:
        buttons.append(_button("接这个品",
                               {"action": "accept_deal", "product_id": product_id},
                               "primary"))
    buttons.append(_button("看我的排品表", {"action": "view_plan"}))
    els.append(_hr())
    els.append(_action(buttons))
    if plan_hint:
        els.append(_note(plan_hint))
    return card(f"🛒 {product_name}", els, template=verdict_tpl)


def plan_card(talent_name: str, title: str, lines: list,
              actions: list = None, note: str = "") -> dict:
    """排品提醒卡：今天推哪几个 + 坑位 + 按钮。

    ★ 达人名放**正文第一行**（header.title 是单行，塞不下两行）。
    """
    els = []
    if talent_name:
        els.append(_md(f"**{talent_name}**"))
    els.append(_md("\n".join(lines)))
    if actions:
        els.append(_hr())
        els.append(_action(actions))
    if note:
        els.append(_note(note))
    return card(title, els, template=TPL_GREEN)


def to_content(card_obj: dict) -> str:
    """飞书 msg_type=interactive 的 content 字段（**字符串**）。"""
    return json.dumps(card_obj, ensure_ascii=False)
