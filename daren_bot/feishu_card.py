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
    # ★ 把当前商品的 product_id 一起带过去 ——
    #   点「看我的排品表」会用回调响应**替换**原卡 ⇒ 原卡上的「接这个品」按钮会消失。
    #   带上 id ⇒ 排品表卡上能再放一个「接这个品」，接品闭环不丢。
    vp_value = {"action": "view_plan"}
    if product_id:
        vp_value["product_id"] = product_id
    buttons.append(_button("看我的排品表", vp_value))
    els.append(_hr())
    els.append(_action(buttons))
    if plan_hint:
        els.append(_note(plan_hint))
    return card(f"🛒 {product_name}", els, template=verdict_tpl)


def _dim_raw(rec: dict, key: str):
    """从推荐结果的 `dims` 里取某个维度的**原始值**（没有就返回 None）。

    ★ 为什么不直接读商品表的列：卡片只拿到 `Recommendation.to_dict()`，
      维度原始值就在 `dims` 里（每项带 key/label/raw/score/note）。
    """
    for d in rec.get("dims") or []:
        if d.get("key") == key:
            return d.get("raw")
    return None


# ★ 达人能看懂的「业务理由」维度。
#   ⛔ 排除「到手价契合度」—— 它的 note 是「¥40 落在策略价带 ¥60~¥150」，
#      那是**我们内部**的话术，达人看了不知道这品好不好。
_RECO_REASON_DIMS = ("shop_score", "good_ratio", "video_sales_7d",
                      "talents_sold", "board_rank")


def _reco_reasons(rec: dict, limit: int = 2) -> list:
    """挑达人能看懂的推荐理由（业务维度优先，挑不满再退回 `reasons`）。"""
    out = []
    for d in rec.get("dims") or []:
        if d.get("key") in _RECO_REASON_DIMS and (d.get("score") or 0) >= 0.5:
            note = str(d.get("note") or "").strip()
            if note:
                out.append(note)
        if len(out) >= limit:
            break
    if not out:
        out = [str(x).strip() for x in (rec.get("reasons") or []) if str(x).strip()]
    return out[:limit]


def _btn_label(name: str) -> str:
    """按钮标签：去掉括号后缀，截断得干净些（别留半个「【」）。"""
    s = str(name or "")
    for ch in "[【（(":
        if ch in s:
            s = s.split(ch)[0]
    s = s.strip() or "这个品"
    return ("接 " + s)[:12]


def reco_card(profile_line: str, recos: list, note: str = "") -> dict:
    """选品推荐卡：画像一句话 + 每个推荐一行（★/名称/价格/佣金/理由）+ 「接这个品」按钮。

    ★ 每个推荐自带一个「接这个品」按钮（`accept_deal` + 商品 id）
      ⇒ 达人点一下就进排品池 → **当场排期** → 第二天早上推「今天推哪个」（闭环）。
    ★ 按钮标签带商品名首字 —— 不能三个一模一样的「接这个品」，
      达人分不清哪个按钮对哪个品。
    ★ 星级用推荐结果里的 `stars`（= min(绝对分星级, 池内分位星级)），
      不自己另算一套。
    """
    els = []
    if profile_line:
        els.append(_md(profile_line))
    buttons = []
    for r in recos:
        name = str(r.get("name") or "")
        stars = int(r.get("stars") or 0)
        price = r.get("price") or 0
        comm = _dim_raw(r, "commission_rate")
        bits = []
        if price:
            bits.append(f"¥{float(price):g}")
        if comm:
            bits.append(f"佣金{float(comm):g}%")
        strat = str(r.get("strategy") or "")
        if strat:
            bits.append(strat)
        reasons = _reco_reasons(r)
        lines = [f"**{'★' * stars}{'☆' * (5 - stars)}** {name[:26]}"]
        if bits:
            lines.append(" · ".join(bits))
        if reasons:
            lines.append(" · ".join(reasons))
        els.append(_md("\n".join(lines)))
        pid = str(r.get("product_id") or "")
        if pid:
            buttons.append(_button(
                _btn_label(name),
                {"action": "accept_deal", "product_id": pid},
                "primary" if not buttons else "default"))
    if buttons:
        els.append(_hr())
        els.append(_action(buttons))
    if note:
        els.append(_note(note))
    return card("⭐ 给你的选品推荐", els, template=TPL_BLUE)


def plan_card(talent_name: str, title: str, lines: list,
              actions: list = None, note: str = "",
              template: str = TPL_GREEN) -> dict:
    """排品提醒卡：今天推哪几个 + 坑位 + 按钮。

    ★ 达人名放**正文第一行**（header.title 是单行，塞不下两行）。
    ★ `template` 可覆盖（2026-10-26）：空态占位卡用**灰色** ——
      绿色模板的视觉语义是「今天有好货」，空态用绿色 = 误读。
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
    return card(title, els, template=template)


def to_content(card_obj: dict) -> str:
    """飞书 msg_type=interactive 的 content 字段（**字符串**）。"""
    return json.dumps(card_obj, ensure_ascii=False)
