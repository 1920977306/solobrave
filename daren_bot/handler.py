"""消息路由 —— spec v1.1 第 4 章时序 + 3 秒硬约束。

handler 只做：免费路由判定 → 图片落盘 → 余额/限频检查 → 回确认卡片 → 预扣 → 入队。
抓取、vision、分析全在 worker（handler 内任何路径不得超过 3 秒）。

意图分类（glm-4-flash 走 OpenClaw 网关）未接入前，未命中免费路由的文本
一律走零成本软话术（fail-closed 兜底，spec 4.4），不产生任何模型/浏览器调用。
"""
import json
import logging
import re
import time
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from . import config, db, cockpit_link, product_intent
from . import credits   # 积分闸门（扣费入口唯一真源，见 credits.py）

log = logging.getLogger("daren-bot.handler")

DOUYIN_RE = re.compile(r"^\s*([A-Za-z0-9_]{5,24})\s*$")
DOUYIN_IN_TEXT_RE = re.compile(r"抖音号\s*[:：]?\s*([A-Za-z0-9_]{4,24})")
URL_RE = re.compile(r"https?://[^\s，。]+")
# 抖音视频链接（直链/短链/分享页），命中走视频拆解；其余链接仍走商品链路
VIDEO_LINK_RE = re.compile(
    r"(https?://v\.douyin\.com/[^\s，。]+"
    r"|https?://(?:www\.)?douyin\.com/video/\d+"
    r"|https?://(?:www\.)?iesdouyin\.com/share/video/\d+[^\s，。]*)")
TRACKING_PREFIXES = ("utm_", "spm", "fbclid", "gclid", "tt_")

UNRELATED_REPLY = ("我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，"
                   "其他问题帮不上忙哦～")
UNSUPPORTED_REPLY = ("这类消息我还处理不了，发我抖音号、商品截图、商品链接或抖音视频链接都可以～")
GREETING_RE = re.compile(r"^(你好|在吗|在么|您好|hi|hello|嗨|早|早上好|晚上好|下午好|哈喽|在不在)[呀啊呐哦哇~～!！\s]*$", re.I)
BALANCE_RE = re.compile(r"^(余额|积分|我的积分|积分余额|查积分|还有多少积分)[?？!！~～\s]*$")
WEEKLY_UNSUB_RE = re.compile(r"^(取消订阅|退订|关闭)(每周)?(经营)?周报[呀啊哦~～!！\s]*$")
WEEKLY_SUB_RE = re.compile(r"^(订阅|开通|恢复)(每周)?(经营)?周报[呀啊哦~～!！\s]*$")
WEEKLY_STATUS_RE = re.compile(r"^周报(状态|订阅)?[?？!！~～\s]*$")
# 管理员后门：主动推送类功能的验收/排查通道（灰度规矩：不等周日、不推达人只回管理员）
TRIAL_WEEKLY_RE = re.compile(
    r"^立即试跑周报(?:\s*[@＠]?(?P<tk>[A-Za-z0-9_]+))?[?？!！~～\s]*$")
# ── 排品表（2026-10-24 老大：达人接很多品，要 AI 帮忙排 + 提醒）──
# 「今天推啥 / 排品表 / 我的排品」→ 把排期表直接回给达人（零成本，不扣积分）
PLAN_QUERY_RE = re.compile(
    r"^(今天(推|发|上|播)(什么|啥|哪几个|哪些)?|排品表|排品|我的排品|"
    r"这周(推|发)什么|下周(推|发)什么)[呀啊哦哇~～!！\s]*$")
# 「接了 <商品ID>」→ 登记合作单（进排品池）
# ⚠️ 路由顺序：**必须在裸商品ID 之前** —— 否则「接了 3668...」里的 19 位数字
#    会被当成「查这个商品」抢走，达人想登记的意图就丢了（2026-10-24 踩过）。
ACCEPT_DEAL_RE = re.compile(
    r"^(?:我?接(了|下|了它|了这个品|了这单)|加进排品|排品加|要接|推这个)"
    r"[\s:：,，]*(?P<pid>[0-9]{15,22}|[A-Za-z0-9_-]{6,40})\s*$")

# ── 选品推荐（2026-10-25 老大目标 3：自算推荐指数接到飞书）──
# 「推荐点什么 / 推荐几个品 / 选品建议」→ 走**我方自算的推荐指数**
# （粉丝画像 → 类目策略 → 商品推荐 → ★ 星级），
# **零成本**：纯查库 + 纯函数算，不调 LLM、不扣积分。
#
# ⚠️ 与排品表（PLAN_QUERY_RE）严格区分：
#    「今天推什么」= 已排好的表（问排期）；「推荐什么」= 现算的候选（问推荐）。
# ⚠️ 判据刻意**保守**：宁可漏（回落到 LLM 问答），不可抢 ——
#    抢了会把「这个品能不能带」这种要**真值**的问题变成一份候选清单。
_RECO_KEYWORD_RE = re.compile(r"推荐|选品")
# 范围词：出现它才说明达人在要「一份清单」而不是「一个商品」
_RECO_RANGE_RE = re.compile(
    r"点|几个|些|一些|什么|啥|哪些|哪个|给我|来|看看|建议|指数|有什么|有啥")
# ★ 点名了某个**具体商品** ⇒ 那是商品判断意图，交给商品链路，别截
_RECO_EXCLUDE_RE = re.compile(
    r"这款|这个款|本款|这个商品|这款商品|这个产品|这款产品|"
    r"这个单品|这款单品|这个链接|这款链接|这瓶|这盒|这包|这双|"
    r"这个礼盒|这款礼盒|这个品|这款品")

# 专属佣金回复：文本里出现「百分比数字」才可能是达人报佣金（2026-10-28）。
# ★ 仅作「必要条件」用 —— 真正的拦截判据在 _route_commission 里
#   （还必须有待重算的 pending 商品，否则普通问答里的百分号不会被误吃）。
COMMISSION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def _parse_post(content: dict):
    """解析飞书富文本（post 消息）⇒ (caption文字, [image_key,...])。

    结构：{"title": str, "content": [[{tag:"text",text:...},
    {tag:"a",text:...,href:...},{tag:"img",image_key:...}], ...]}
    —— 外层 content 是「段落列表」，每段是「行内元素列表」。

    达人手机端「图片加一句话」发出来就是 post（不是 image）——
    此前 _dispatch 只认 image/text，post 直接掉兜底话术（2026-10-28 实案）。
    """
    caption_parts = []
    image_keys = []
    for para in content.get("content") or []:
        if not isinstance(para, list):
            continue
        for el in para:
            if not isinstance(el, dict):
                continue
            tag = el.get("tag")
            if tag == "img":
                key = el.get("image_key")
                if key:
                    image_keys.append(key)
            elif tag == "text":
                caption_parts.append(el.get("text", ""))
            elif tag == "a":
                caption_parts.append(el.get("text", "") or el.get("href", ""))
    title = content.get("title") or ""
    caption = " ".join(p.strip() for p in ([title] + caption_parts)
                       if p and p.strip()).strip()
    return caption, image_keys

# 一次给几个推荐（= 卡片上的「接这个品」按钮数，别太多）
RECO_TOP_N = 3
RECO_CARD_NOTE = "★ 是我方按你的粉丝画像算的（平台没这个数）· 想看更多发「推荐」"


def is_reco_query(text: str) -> bool:
    """达人是不是在要「一份选品推荐清单」。"""
    t = (text or "").strip()
    if not t or len(t) > 24:
        return False
    if _RECO_EXCLUDE_RE.search(t):
        return False
    if not _RECO_KEYWORD_RE.search(t):
        return False
    # 光秃秃一个信号（「推荐」「选品」）也算
    return len(t) <= 4 or bool(_RECO_RANGE_RE.search(t))


GREETING_REPLY = ("你好呀，我是你的带货选品助手。发我你的抖音号可以做账号分析，"
                  "直接问选品、带货、粉丝画像的问题我也都能答～")


def normalize_product_url(url: str) -> str:
    """URL 归一化：去追踪参数（短链解析在 worker 里做，内测先用直连链接）。"""
    try:
        parts = urlsplit(url)
        query = [(k, v) for k, v in parse_qsl(parts.query)
                 if not k.lower().startswith(TRACKING_PREFIXES)]
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                           parts.path.rstrip("/"), urlencode(query), ""))
    except Exception:
        return url


def _extract_name_hint(text: str, dy_id: str) -> str:
    """客户常带名字发号：「洁洁爱户外\n抖音号：hlb725」→ 「洁洁爱户外」。
    去掉抖音号标注和 id 后剩余的中文/字母数字视作名字提示，仅作搜索核验用。"""
    t = text.replace(dy_id, ' ')
    t = re.sub(r'抖音[号IDid]*\s*[:：]?', ' ', t)
    t = re.sub(r'[^\w\u4e00-\u9fff]', '', t)
    # 客户常写「昵称：萌萌超会买」，剥掉标注词本身
    t = re.sub(r'^(昵称|名字|姓名|达人名|达人昵称)', '', t)
    return t[:20] if 1 < len(t) <= 20 else ''


class Router:
    def __init__(self, ws_manager):
        self.wm = ws_manager

    def make_p2_handler(self, tenant: dict):
        """每个租户一个闭包（事件体不带 app_id，租户归属靠连接）。"""
        def do_p2_im_message_receive_v1(data) -> None:
            try:
                self._dispatch(tenant, data)
            except Exception:
                log.exception("dispatch failed tenant=%s", tenant["tenant_key"])
        return do_p2_im_message_receive_v1

    # ---------- 卡片动作回调（card.action.trigger） ----------

    def make_card_action_handler(self, tenant: dict):
        """卡片按钮点击 → 干活（接品 / 看排品表）。

        ★ 走**长连接**（`register_p2_card_action_trigger`）⇒
          不需要公网地址，也不用在飞书后台填「卡片请求网址」。
        ★ 卡片只支持 1.0，按钮回传的是 `value` 里的自定义字段。
        ⚠️ `action.value` 是 **dict**（不是 JSON 字符串）——
           按字符串解会直接炸（2026-10-24 查 SDK 源码确认）。
        """
        tk = tenant["tenant_key"]

        def on_card_action(data) -> dict:
            try:
                ev = data.event
                action = getattr(ev, "action", None)
                ctx = getattr(ev, "context", None)
                value = {}
                if action is not None and isinstance(action.value, dict):
                    value = action.value
                act = str(value.get("action") or "")
                chat_id = str(getattr(ctx, "open_chat_id", "") or "")
                log.info("card action: tenant=%s act=%s chat=%s value=%s",
                         tk, act, chat_id, value)
                if act == "accept_deal":
                    return self._card_accept_deal(tk, value)
                if act == "view_plan":
                    # ★ product_id 一起带过去：原卡被替换后「接这个品」会消失，
                    #   排品表卡上要能再接一次同一个品。
                    return self._card_view_plan(tk, chat_id,
                                                 str(value.get("product_id") or ""))
                return {"toast": {"type": "info", "content": "这个按钮还没接上"}}
            except Exception:
                log.exception("card action failed tenant=%s", tk)
                return {"toast": {"type": "error", "content": "处理失败，稍后再试"}}
        return on_card_action

    def _card_accept_deal(self, tk: str, value: dict) -> dict:
        """卡片按钮「接这个品」→ 登记进排品池（和飞书说「接了 xxx」同一条路径）。"""
        from . import plan_notify
        pid = str(value.get("product_id") or "").strip()
        if not pid:
            return {"toast": {"type": "error", "content": "没拿到商品ID"}}
        try:
            dyid = (db.get_latest_douyin_id(tk)
                    or (db.get_tenant(tk) or {}).get("bind_douyin_id") or "").strip()
            ok, msg = plan_notify.register_deal(dyid, pid)
            tip = "已接进排品表 ✅" if ok else msg
            return {"toast": {"type": "success" if ok else "info", "content": tip[:60]}}
        except Exception:
            log.exception("card accept deal failed tenant=%s", tk)
            return {"toast": {"type": "error", "content": "接品失败，稍后再试"}}

    def _card_view_plan(self, tk: str, chat_id: str, product_id: str = "") -> dict:
        """卡片按钮「看我的排品表」→ 直接回一张排品卡。

        ★ 用回调响应里的 `card` 字段**替换原卡片**（飞书原生「点开替换」），
          不用再发一条新消息 ⇒ 聊天里不会越堆越多。
        ★ 显示**今天及以后**（口径与 H5 `/api/plan`、文字版 `plan_notify.plan_text`
          三处一致）—— 原来只筛 today 这 1 天，按钮叫「排品表」却只给 1 天，
          名不副实（2026-10-25 修）。
        ★ 原卡上的「接这个品」按钮会因为「替换」而消失 ⇒ 把 product_id 带过来，
          排品表卡上再放一个，接品的闭环不丢。
        """
        from . import plan_notify
        from . import feishu_card as fc
        from daren_cockpit.tools import product_plan as plan
        try:
            dyid = (db.get_latest_douyin_id(tk)
                    or (db.get_tenant(tk) or {}).get("bind_douyin_id") or "").strip()
            slots, talent = plan_notify.plan_for_talent(dyid)
            if not talent:
                return {"toast": {"type": "info", "content": "还没有排品，先接几个品吧"}}
            # ★ 只看今天及以后 —— 和 H5 / 文字版同一个过滤函数（`plan.future_slots`），
            #   别在这儿另写一套（历史排期不是待办）。
            future = plan.future_slots(slots or [])
            if not future or not any(s.items for s in future):
                return {"toast": {"type": "info",
                                  "content": "今天及以后没有排品，接几个品我就给你排"}}
            lines = []
            for s in future:
                if not s.items:
                    continue
                lines.append(f"**{s.date}**")
                for it in s.items:
                    life = it.lifecycle.label if it.lifecycle else ""
                    lines.append(f"坑位 {it.plan_slot} · {it.product_name[:24]}\n"
                                 f"¥{it.price:g} · 佣金{it.commission_rate:g}%"
                                 + (f" · {life}" if life else ""))
            actions = []
            if product_id:
                actions.append(fc._button("接这个品",
                                          {"action": "accept_deal", "product_id": product_id},
                                          "primary"))
            new_card = fc.plan_card(
                plan.display_name(talent), "📋 我的排品表", lines,
                actions=actions or None,
                note="每天 09:37 我会推「今天推哪个」给你 · 想加品发我「接了 <商品ID>」")
            return {"card": {"type": "raw", "data": new_card}}
        except Exception:
            log.exception("card view plan failed tenant=%s", tk)
            return {"toast": {"type": "error", "content": "排品表算不出来"}}

    # ---------- 主路由 ----------

    def _dispatch(self, tenant: dict, data) -> None:
        t0 = time.time()
        msg = data.event.message
        chat_id = msg.chat_id
        sender = self.wm.get_sender(tenant["tenant_key"])
        if sender is None:
            log.error("no sender for tenant=%s", tenant["tenant_key"])
            return
        try:
            content = json.loads(msg.content or "{}")
        except json.JSONDecodeError:
            content = {}

        log.info("message received: tenant=%s type=%s chat=%s from=%s",
                 tenant["tenant_key"], msg.message_type, chat_id,
                 getattr(data.event.sender.sender_id, "open_id", "?"))
        # 记住达人本人 open_id：建多维表格后授权其可见（幂等，只写一次）
        try:
            db.set_owner_open_id(
                tenant["tenant_key"],
                getattr(data.event.sender.sender_id, "open_id", "") or "")
        except Exception:
            pass

        if msg.message_type == "image":
            self._route_image(tenant, msg, content, sender)
        elif msg.message_type == "post":
            # ★ 2026-10-28：富文本（图+文组合）此前直接掉兜底话术——
            #   达人手机端「图片加一句话」就是 post。拆出文字与图，图走视觉（文字作上下文）。
            caption, image_keys = _parse_post(content)
            if image_keys:
                self._route_image(tenant, msg,
                                  {"image_key": image_keys[0]}, sender,
                                  caption=caption)
            elif caption:
                self._route_text(tenant, chat_id, caption, sender)
            else:
                sender.send_text(chat_id, UNSUPPORTED_REPLY)
        elif msg.message_type == "text":
            self._route_text(tenant, chat_id, content.get("text", ""), sender)
        else:
            sender.send_text(chat_id, UNSUPPORTED_REPLY)

        elapsed = time.time() - t0
        if elapsed > 3:
            log.warning("HANDLER OVER 3s: tenant=%s elapsed=%.2fs",
                        tenant["tenant_key"], elapsed)

    def _route_text(self, tenant: dict, chat_id: str, text: str, sender) -> None:
        """文字消息路由（text 与 post 的文字部分共用本入口）。

        路由顺序有讲究（2026-10-22 商品意图修复）：
          ① 明确标注「抖音号：xxx」→ 一定是抖音号
          ② 裸商品ID（17-20 位数字）→ 必须在 ③ 之前！否则 DOUYIN_RE 会把它当抖音号抢走
          ③ 整条是一个 alnum 串 → 抖音号
        """
        dy_in_text = DOUYIN_IN_TEXT_RE.search(text)
        dy_only = DOUYIN_RE.match(text)
        bare_pid = self._bare_product_id(tenant, text, dy_only)
        accept = ACCEPT_DEAL_RE.match(text.strip())
        if cockpit_link.is_cockpit_url(text):
            # 达人把自己收到的驾驶舱链接发回来 → 回入口，别当成商品链接扣 20 分
            self._route_cockpit_link(tenant, chat_id, text, sender)
        elif accept:
            # ★「接了 <商品ID>」→ 登记进排品池（必须在裸商品ID 分支之前！）
            self._route_accept_deal(tenant, chat_id, accept.group('pid'), sender)
        elif dy_in_text:
            self._route_douyin(tenant, chat_id, dy_in_text.group(1), sender,
                               name_hint=_extract_name_hint(text, dy_in_text.group(1)))
        elif bare_pid:
            # 达人按引导语只发了一串商品ID → 和商品链接同一条链路
            self._route_product_id(tenant, chat_id, bare_pid, sender)
        elif dy_only:
            self._route_douyin(tenant, chat_id, dy_only.group(1), sender,
                               name_hint=_extract_name_hint(text, dy_only.group(1)))
        elif VIDEO_LINK_RE.search(text):
            self._route_video(tenant, chat_id, text, sender)
        elif URL_RE.search(text):
            self._route_link(tenant, chat_id, text, sender)
        elif BALANCE_RE.match(text.strip()):
            sender.send_text(
                chat_id,
                f"你的积分余额：{db.get_balance(tenant['tenant_key'])} 分"
                "（发抖音号查账号 100 分/周，商品链接 20 分，截图 10 分，"
                "问答 1 分；内测期找运营免费充值）")
        elif WEEKLY_UNSUB_RE.match(text.strip()):
            db.set_weekly_sub(tenant["tenant_key"], 0)
            sender.send_text(
                chat_id,
                "好的，已取消每周经营周报订阅，不会再主动推送。"
                "想恢复随时发「订阅周报」～")
        elif WEEKLY_SUB_RE.match(text.strip()):
            db.set_weekly_sub(tenant["tenant_key"], 1)
            sender.send_text(
                chat_id,
                "已订阅每周经营周报 ✅ 每周一上午我会自动推送你的账号数据变化、"
                "异常提醒和下周建议，不用你手动查。想取消随时发「取消订阅周报」")
        elif WEEKLY_STATUS_RE.match(text.strip()):
            sub = db.get_weekly_sub(tenant["tenant_key"])
            sender.send_text(
                chat_id,
                ("周报订阅：已开启 ✅ 每周一上午自动推送"
                 if sub else "周报订阅：已关闭。发「订阅周报」可恢复"))
        elif PLAN_QUERY_RE.match(text.strip()):
            # 排品表查询（零成本）
            self._route_plan_query(tenant, chat_id, sender)
        elif TRIAL_WEEKLY_RE.match(text.strip()):
            self._route_trial_weekly(tenant, chat_id, text, sender)
        elif len(text.strip()) <= 8 and GREETING_RE.search(text):
            sender.send_text(chat_id, GREETING_REPLY)
        elif cockpit_link.is_cockpit_link_question(text):
            # 驾驶舱入口导航（2026-10-20 交接项①）：零成本，不扣积分
            self._route_cockpit_link(tenant, chat_id, text, sender)
        elif product_intent.missing_product_ref_question(text):
            # 商品判断意图 + 没给商品标识（2026-10-22 老大截图实锤）：
            # 以前这种会掉进「其他问题帮不上忙」的通用兜底，达人以为机器人不懂货。
            # 零成本（只是要一个输入），不扣积分、不调 LLM。
            sender.send_text(chat_id, product_intent.NEED_PRODUCT_REF_REPLY)
        elif is_reco_query(text):
            # 选品推荐（零成本：我方自算的推荐指数，不调 LLM、不扣积分）
            self._route_reco_query(tenant, chat_id, sender)
        elif self._route_commission(tenant, chat_id, text, sender):
            # 专属佣金回复（2026-10-28）：达人报了自己后台的专属佣金 ⇒ 重算商品结论
            pass
        else:
            # 业务问答：入队异步走 LLM（扣 1 积分），超出带货范围由模型拒答
            self._route_qa(tenant, chat_id, text, sender)

    # ---------- 专属佣金回复（达人报数 → 重算商品结论） ----------

    def _route_commission(self, tenant: dict, chat_id: str, text: str,
                          sender) -> bool:
        """判断这条文字是不是「达人报专属佣金」；是则入队重算并返回 True。

        收紧判据（防误吃普通问答）：
          - 文本里要有百分比数字，且含「佣」字**或**全句很短（≤12 字，如「20%」「佣金20」）；
          - 且该租户**确有**待重算的商品（24h 内分析过的）——没有 pending 一律不算佣金回复。
        """
        tk = tenant["tenant_key"]
        m = COMMISSION_RE.search(text)
        if not m:
            return False
        short_or_keyword = ("佣" in text) or (len(text.strip()) <= 12)
        if not short_or_keyword:
            return False
        pending = db.get_commission_pending(tk)
        if not pending:
            return False
        try:
            rate = float(m.group(1))
        except ValueError:
            return False
        if rate <= 0 or rate > 95:
            return False
        # ⚠️ 第 4 位是 action（计费口径 "commission"），达人报的数与原商品
        #   字段走 payload 关键字参数。
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "commission", pending["product_key"], "commission",
            chat_id=chat_id,
            payload=json.dumps({"rate": rate, "stats": pending["stats"]},
                               ensure_ascii=False))
        if status == "insufficient":
            sender.send_text(chat_id, credits.insufficient_text("commission", bal))
            return True
        if status == "duplicate":
            sender.send_text(chat_id,
                             f"已经在按专属佣金重算啦，出来直接发你～（积分余额 {bal}）")
            return True
        db.clear_commission_pending(tk)
        sender.send_text(
            chat_id,
            f"收到，按专属佣金 {rate:g}% 重新评估刚才那个商品～（积分余额 {bal}）")
        log.info("tenant=%s commission recalc queued: rate=%g product=%s",
                 tk, rate, pending["product_key"])
        return True

    # ---------- 裸商品ID：抖音号 还是 商品ID？ ----------

    def _bare_product_id(self, tenant, text: str, dy_only) -> str:
        """整条消息是一串 17-20 位数字时，判定它是**商品ID**还是**抖音号**。

        为什么要判（2026-10-22）：`DOUYIN_RE` 是 `^[A-Za-z0-9_]{5,24}$`，
        一串 19 位纯数字**会被当成抖音号**抢走 —— 而我们正在教达人「发商品ID」，
        收了却走错路 = 空头支票。

        判据（对着库判，不靠猜）：
          ① 消息里明确写「抖音号：xxx」→ 抖音号（调用方已先处理）
          ② 消息里还有别的字（不是整条一个数字）→ 只可能是商品ID
          ③ 整条一个数字：等于本租户已绑定的抖音号 → 抖音号；
             库里能查到这号是已采集达人 → 抖音号；其余 → 商品ID
             （抖音号几乎不会是 17-20 位纯数字，商品ID 一定是）
        """
        pid = product_intent.find_bare_product_id(text)
        if not pid:
            return ""
        if DOUYIN_IN_TEXT_RE.search(text):
            return ""
        if not dy_only:
            # 消息里还有别的字 → 不可能是抖音号
            return pid
        bound = (tenant.get("bind_douyin_id") or "").strip()
        if bound and bound == pid:
            return ""
        try:
            if db.get_tenant_by_douyin(pid):
                return ""
        except Exception:
            log.warning("bare id dyid lookup failed", exc_info=True)
        return pid

    # ---------- 管理员后门：立即试跑周报（只回管理员，不推达人） ----------

    def _route_cockpit_link(self, tenant, chat_id, text, sender):
        """驾驶舱入口导航（2026-10-20 交接项①，2026-10-21 补做）。

        **零成本** —— 纯导航不是分析，不该扣积分。
        链接是达人专属 k（一人一链），只发给他本人。
        ⚠️ 日志只记 tenant_key，**不打 k**（k 等同于访问密码）。
        """
        tk = tenant["tenant_key"]
        db.log_interaction(tk, "cockpit_link")
        link = cockpit_link.tenant_cockpit_link(tk)
        if not link:
            sender.send_text(chat_id,
                             "你的专属驾驶舱还没开通～联系运营老师开一下就能用啦。")
            return
        log.info("cockpit link sent: tenant=%s", tk)
        sender.send_text(
            chat_id,
            f"这是你的专属经营驾驶舱 👇\n{link}\n"
            "（一人一链接，只给你自己；里面是账号数据、选品建议和合作机会）")

    # ---------- 排品表（2026-10-24 老大：达人接很多品，要 AI 帮忙排 + 提醒）----------
    def _route_accept_deal(self, tenant, chat_id, product_id, sender):
        """「接了 <商品ID>」→ 登记成合作单（进排品池）。

        **零成本**：不调 LLM、不扣积分 —— 只是一次登记。
        ⚠️ 幂等：同一个达人同一个品不重复登记（避免刷一排重复品把坑位占满）。
        """
        from . import plan_notify
        tk = tenant["tenant_key"]
        # ★ 用「最近活跃的抖音号」找达人，别只信 bind_douyin_id（实测错位过）
        dyid = (db.get_latest_douyin_id(tk) or
                tenant.get("bind_douyin_id") or "").strip()
        try:
            ok, msg = plan_notify.register_deal(dyid, str(product_id or "").strip())
        except Exception as e:
            log.exception("register deal failed: %s", e)
            sender.send_text(chat_id, "登记失败，我这边查一下再回你～")
            return
        log.info("accept deal: tenant=%s product=%s ok=%s", tk, product_id, ok)
        sender.send_text(chat_id, msg)

    def _route_plan_query(self, tenant, chat_id, sender):
        """「今天推啥 / 排品表」→ 把排期表直接回给达人。

        **零成本**：纯查库 + 纯函数算排期，不调 LLM、不扣积分。
        ⚠️ 这里**不做任何自动采集** —— 排品池就是我们系统里的合作单；
          达人端只用于「按商品ID搜商品」，不碰达人的其他数据
          （2026-10-24 老大纠正：达人端只做搜商品这一件事）。
        """
        from . import plan_notify
        tk = tenant["tenant_key"]
        db.log_interaction(tk, "plan_query")
        dyid = (db.get_latest_douyin_id(tk) or
                tenant.get("bind_douyin_id") or "").strip()
        try:
            text = plan_notify.plan_text(dyid)
        except Exception as e:
            log.exception("plan query failed: %s", e)
            text = "排品表我这边算不出来，稍后再问我一次～"
        log.info("plan query: tenant=%s", tk)
        sender.send_text(chat_id, text)

    def _route_reco_query(self, tenant, chat_id, sender):
        """「推荐点什么 / 选品建议」→ 回一张**自算推荐指数**卡。

        ★ 零成本：纯查库 + 纯函数算，不调 LLM、不扣积分（跟排品表查询一个待遇）。
        ★ 只丢不造：画像不足就如实说「算不出」，**不给一份编出来的推荐**。
        ★ 取数走 `reco_source`（**唯一真源**）—— 和驾驶舱那份是同一份取数、同一套算法。
        ★ 卡片上的「接这个品」点一下就进排品池 → 当场排期 → 第二天早上推「今天推哪个」
          （闭环）。
        """
        from . import plan_notify
        from . import feishu_card as fc
        from daren_cockpit.tools import reco_source
        tk = tenant["tenant_key"]
        db.log_interaction(tk, "reco_query")
        dyid = (db.get_latest_douyin_id(tk)
                or tenant.get("bind_douyin_id") or "").strip()
        if not dyid:
            sender.send_text(chat_id, "先把你的抖音号发我，我才能按你的粉丝画像算推荐～")
            return
        try:
            talent = plan_notify.read_talent(dyid)
        except Exception:
            log.exception("reco query talent lookup failed tenant=%s", tk)
            talent = None
        if not talent:
            sender.send_text(chat_id, "还没采到你的账号数据，发我抖音号我先帮你拉一份～")
            return
        try:
            chain = reco_source.build_chain(talent, top_n=RECO_TOP_N)
        except Exception:
            log.exception("reco query failed tenant=%s", tk)
            chain = {}
        recos = chain.get("recommendations") or []
        if not recos:
            # ★ 只丢不造：算不出就如实说，不给一份编出来的推荐
            notes = [n for n in (chain.get("notes") or []) if n]
            why = notes[0] if notes else ""
            sender.send_text(chat_id, "我这边暂时算不出你的选品推荐"
                             + (f"（{why}）" if why else "") + "～")
            return
        prof = chain.get("profile") or {}
        bits = []
        if prof.get("main_gender"):
            bits.append(str(prof["main_gender"]))
        if prof.get("main_age"):
            bits.append(str(prof["main_age"]))
        if prof.get("price_center"):
            bits.append(f"客单价约 ¥{float(prof['price_center']):g}")
        cats = prof.get("categories") or []
        if cats:
            bits.append("、".join(str(c) for c in cats[:2]))
        head = "**" + str(prof.get("name") or "") + "**"
        if bits:
            head += " · " + " · ".join(bits)
        card = fc.reco_card(head, recos, note=RECO_CARD_NOTE)
        try:
            sender.send_card(chat_id, card)
        except Exception:
            # ★ 发卡失败回退成文字（卡片不是唯一出口）
            log.exception("reco card send failed tenant=%s", tk)
            lines = [head]
            for r in recos:
                lines.append(f"{'★' * int(r.get('stars') or 0)} {r.get('name') or ''}"
                             f" ¥{r.get('price') or 0:g}")
            sender.send_text(chat_id, "\n".join(lines))
            return
        log.info("reco query: tenant=%s recos=%d", tk, len(recos))

    def _route_trial_weekly(self, tenant, chat_id, text, sender):
        tk = tenant["tenant_key"]
        if tk != "daren_boss":
            sender.send_text(chat_id, "这个指令只有管理员可用～")
            return
        m = TRIAL_WEEKLY_RE.match(text.strip())
        target_tk = (m.group("tk") if m else None) or tk
        t = db.get_tenant(target_tk)
        if not t:
            sender.send_text(chat_id, f"租户 {target_tk} 不存在")
            return
        dyid = (t.get("bind_douyin_id") or "").strip()
        if not dyid:
            sender.send_text(chat_id, f"租户 {target_tk} 未绑定抖音号")
            return
        task_id = db.enqueue_task(target_tk, "weekly_report", dyid, 0,
                                  chat_id=chat_id, payload="{}")
        sender.send_text(chat_id,
                         f"已为 {target_tk}（{dyid}）触发周报试跑，"
                         f"结果直接回本会话（任务 {task_id[:8]}）")

    # ---------- ① 抖音号（spec 4.1） ----------

    def _route_douyin(self, tenant, chat_id, dy_id, sender, name_hint: str = "") -> None:
        tk = tenant["tenant_key"]
        bound = tenant.get("bind_douyin_id") or ""
        first_bind = False
        if not bound:
            db.bind_douyin_id(tk, dy_id)
            tenant["bind_douyin_id"] = dy_id
            bound = dy_id
            first_bind = True
        is_self = (dy_id == bound)

        # 对标号（查同行/竞争对手）：不占用自己号的周更新额度，
        # 但每个号每周同样限 1 次，且总数有上限（内测期防刷）
        if not is_self:
            peers = db.peer_douyin_count(tk, bound)
            if peers >= config.MAX_PEER_IDS:
                sender.send_text(
                    chat_id,
                    f"内测期最多同时跟踪 {config.MAX_PEER_IDS} 个对标号，"
                    "需要调整联系运营")
                return

        if db.get_week_count(tk, dy_id) >= 1:
            sender.send_text(
                chat_id,
                "本周已分析过你的账号，下周更新；加急联系运营" if is_self
                else "这个号本周已分析过，每个号每周更新 1 次；下周再查")
            return

        if db.get_balance(tk) < config.COST_COLLECT_DOUYIN:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_COLLECT_DOUYIN} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return

        # ★ 2026-10-25 修：**先扣费、再回确认、最后入队**（同一原子操作）。
        #   原来顺序是「先回『已开始采集』→ 再扣费」⇒ 并发下余额被别人扣光时，
        #   达人收到「已开始采集」但任务**根本没入队** ⇒ 永远等不到报告，
        #   还多收一句「余额不足」自相矛盾。
        #   另修：「扣费」与「入队」原本是**两个事务**，而入队是幂等的
        #   ⇒ 重复发同一个号会**扣两次钱、只建一个任务**（净多扣）。
        #   现在 `charge_and_enqueue` 一个事务搞定，且**只有真建了任务才扣分**。
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "douyin", dy_id, "collect_douyin",
            chat_id=chat_id,
            payload=json.dumps({"name_hint": name_hint},
                               ensure_ascii=False) if name_hint else '')
        if status == "insufficient":  # 并发下余额竞争失败
            sender.send_text(chat_id, credits.insufficient_text("collect_douyin", bal))
            return
        if status == "duplicate":
            # 同一个号已经在采集中 ⇒ 没多消耗算力 ⇒ 不扣分
            sender.send_text(chat_id,
                             f"这个号正在采集中啦，报告出来直接发你～（积分余额 {bal}）")
            return
        sender.send_text(
            chat_id,
            ("已开始采集，正常5-8分钟出报告" if is_self
             else f"已开始采集对标账号 {dy_id}，正常5-8分钟出报告；"
                  "你的号和对标号数据可以对比着看")
            + f"（积分余额 {bal}）")
        if first_bind:
            log.info("tenant=%s first-bind douyin_id=%s", tk, bound)
        elif not is_self:
            log.info("tenant=%s peer-collect dy_id=%s (peers=%s)", tk, dy_id,
                     db.peer_douyin_count(tk, bound))

    # ---------- ② 商品截图（spec 4.2） ----------

    def _route_image(self, tenant, msg, content, sender,
                     caption: str = "") -> None:
        tk = tenant["tenant_key"]
        chat_id = msg.chat_id
        image_key = content.get("image_key", "")
        if not image_key:
            sender.send_text(chat_id, "没读到图片内容，重新发一下试试")
            return

        # 3 秒硬约束内必须落盘（im:resource 资源 key 约 3 天过期，spec 第 3 章）
        try:
            raw = sender.download_image(msg.message_id, image_key)
            image_dir = config.IMAGE_DIR / tk
            image_dir.mkdir(parents=True, exist_ok=True)
            path = image_dir / f"{msg.message_id}_{image_key}.jpg"
            path.write_bytes(raw)
        except Exception:
            log.exception("image download failed tenant=%s", tk)
            sender.send_text(chat_id, "图片下载失败，重新发一下试试")
            return

        if db.get_balance(tk) < config.COST_VISION:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_VISION} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return

        # ★ 2026-10-28：post 消息（图+一句话）带进来的文字作上下文，
        #   放进 payload 交给 vision 链（prompt 里问「截图想表达什么」）。
        # ⚠️ charge_and_enqueue 的第 4 位是 action（计费口径），payload 是关键字参数。
        _payload = ""
        if caption:
            _payload = json.dumps({"caption": caption}, ensure_ascii=False)
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "image", str(path), "vision", chat_id=chat_id, payload=_payload)
        if status == "insufficient":
            sender.send_text(chat_id, credits.insufficient_text("vision", bal))
            return
        if status == "duplicate":
            sender.send_text(chat_id,
                             f"这张图正在识别中，识别完直接发你～（积分余额 {bal}）")
            return
        sender.send_text(chat_id, f"收到截图，识别中～（积分余额 {bal}）")
        # vision 走 worker（3 秒硬约束）；废图（识别不出）在 worker 里返还

    # ---------- ③ 业务问答（spec ① 意图分类的问答类） ----------

    def _route_qa(self, tenant, chat_id, text, sender) -> None:
        tk = tenant["tenant_key"]
        q = text.strip()
        if not q:
            sender.send_text(chat_id, UNSUPPORTED_REPLY)
            return
        if len(q) > 300:
            sender.send_text(chat_id, "问题太长了，简短一点问我～")
            return
        # ★ 2026-10-21 复用层（老大：抓一次存下来，下次直接读，省算力）：
        #   同一问题在缓存期内 → 不调 LLM、不扣积分（cache_hit=1）
        #   含时效词的问题（最近/今天/最新…）不缓存 —— 答案会过期
        cached_qa = db.qa_cache_get(tk, q)
        if cached_qa:
            # ★ 命中缓存 ⇒ 这次**没消耗算力**（根本没调 LLM）⇒ 不扣分，
            #   但记一条 cache_hit=1 的流水留痕（走闸门的零成本入口，不裸调 db.deduct）。
            credits.enqueue_free(
                tk, "qa", q[:200], "qa_cached", cache_hit=1, chat_id=chat_id,
                payload=json.dumps({"cached_answer": cached_qa}, ensure_ascii=False))
            sender.send_text(chat_id, "收到（缓存命中，秒回）～")
            return
        if db.get_balance(tk) < config.COST_QA:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_QA} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "qa", q[:200], "qa", chat_id=chat_id)
        if status == "insufficient":
            sender.send_text(chat_id, credits.insufficient_text("qa", bal))
            return
        if status == "duplicate":
            sender.send_text(chat_id,
                             f"这个问题正在回答中，回答完直接发你～（积分余额 {bal}）")
            return
        sender.send_text(chat_id, f"收到，我想一下～（积分余额 {bal}）")

    # ---------- ③ 商品链接 / 商品ID（spec 4.3） ----------

    def _route_link(self, tenant, chat_id, text, sender) -> None:
        url = URL_RE.search(text).group(0)
        self._route_product(tenant, chat_id, normalize_product_url(url), sender, "链接")

    def _route_product_id(self, tenant, chat_id, pid, sender) -> None:
        """裸商品ID —— 和商品链接**同一条链路**。

        2026-10-22：既然引导语说了「发链接或商品ID」，就必须真能收下 ID。
        链路复用不变：`collect_product_by_url(target)` → `extract_product_id` → 查真值。
        """
        self._route_product(tenant, chat_id, pid, sender, "商品ID")

    def _route_product_verdict_cached(self, tenant, chat_id, product_key, sender,
                                       label="链接", verdict=None) -> None:
        """★★ **结论缓存命中** ⇒ **零消耗** ⇒ **不扣分**（老大 2026-10-26 拍板）。

        ★ 为什么是 0 分（不是 5 分）：
          executor 拿到 payload 里的 `cached_verdict` 就**直接 return**
          （`executors.py`：`if isinstance(_vh, dict) and (_vh.get("md") or ...): return ...`）
          ⇒ **不调浏览器、不调 LLM、不写缓存** ⇒ **一点算力都没消耗**
          ⇒ 按「**不消耗的就算了**」的口径 ⇒ 扣 0 分。

        ★ 为什么**单独一个函数**（而不是在 `_route_product` 里直接改）：
          零成本入口 `credits.enqueue_free()` 的调用点要登记在
          `tests/test_credits_gate.py` 的 `ZERO_COST_ALLOWED` 里，而那个登记是
          **函数级**粒度（`_free_entry_calls()` 返回 `(文件名, 函数名)`）
          ⇒ 挂在 `_route_product` 上会**一并放过该函数里未来新增的**
          `enqueue_free` 调用点（哪怕它消耗算力）⇒ 闸门被削弱。
          ⇒ 独立成函数 ⇒ 登记粒度精确，且**语义自解释**。

        ★ 为什么**不走** `charge_and_enqueue`：
          那个入口的语义是「扣费 + 入队」原子操作；0 分路径不扣费
          ⇒ 走 `enqueue_free()`（写一条 0 分流水留痕，对账可查 cache_hit=1）。

        ⚠️ 与「**商品数据缓存命中**」严格区分（那条**仍收 5 分**）：
          商品数据缓存只省了「开浏览器采集」，**LLM 照跑**（`executors.py` 里
          `verdict = _llm_product_verdict(...)` 照常执行）⇒ **有真实消耗**。
        """
        tk = tenant["tenant_key"]
        # 幂等命中（同一商品正在分析中）⇒ 保留既有提示，不退化成「秒出」
        if db.has_pending_task(tk, "product", product_key):
            bal = db.get_balance(tk)
            sender.send_text(
                chat_id,
                f"这个商品正在分析中，结论出来直接发你～（积分余额 {bal}）")
            return
        credits.enqueue_free(
            tk, "product", product_key, "collect_product_verdict_cached",
            cache_hit=1, chat_id=chat_id,
            payload=json.dumps({"cached_verdict": verdict}, ensure_ascii=False))
        bal = db.get_balance(tk)
        sender.send_text(
            chat_id,
            f"收到{label}（结论缓存命中，秒出结果）～（积分余额 {bal}）")

    def _route_product(self, tenant, chat_id, product_key, sender, label="链接") -> None:
        tk = tenant["tenant_key"]

        # ★ 2026-10-21 复用层：**结论缓存**优先于商品数据缓存。
        #   结论缓存命中 = 连 LLM 都不用调（最贵的一步）⇒ **零消耗** ⇒ 不扣分
        #   （老大 2026-10-26 拍板：「没有消耗肯定是 0 分」）。
        #   ⚠️ 下面的**商品数据缓存**只省了「开浏览器采集」，**LLM 照跑**
        #   ⇒ 那是**有真实消耗**的 ⇒ 仍收 `COST_PRODUCT_CACHED`（5 分）。
        #   ⚠️ 这两条以前**共用同一个 action** ⇒ 消耗量不同却同价、流水里还分不出来
        #   ⇒ 2026-10-26 拆开：结论缓存命中走**零成本入口**（独立 action + 独立函数）。
        v_hit = db.verdict_cache_get(tk, product_key)
        if v_hit:
            self._route_product_verdict_cached(
                tenant, chat_id, product_key, sender, label=label, verdict=v_hit)
            return

        cached = db.cache_get(product_key)
        if cached:
            status, _task_id, bal = credits.charge_and_enqueue(
                tk, "product", product_key, "collect_product_cached",
                cache_hit=1, chat_id=chat_id)
            if status == "insufficient":
                sender.send_text(
                    chat_id, credits.insufficient_text("collect_product_cached", bal))
                return
            if status == "duplicate":
                sender.send_text(
                    chat_id,
                    f"这个商品正在分析中，结论出来直接发你～（积分余额 {bal}）")
                return
            sender.send_text(
                chat_id,
                f"收到{label}（缓存命中，秒出结果）～（积分余额 {bal}）")
            return

        # ★ 2026-10-25 前置预检：达人端**确实**掉登录时，直接回人话 ——
        #   不入队、不扣分（省得达人等一轮、再收到一条失败通知）。
        #   ⚠️ 只放在**需要开浏览器采集**的路径上（上面两个缓存命中不用浏览器）。
        #   ⚠️ 判定口径：只有「浏览器开着 + 没登录」才是掉登录
        #     （浏览器没开 = 切到团长端了，是设计不是故障）。
        #   ⚠️ 3 秒硬约束：探活超时 2s，拿不到就照常走任务（不阻塞达人）。
        from . import executors as _exec
        if _exec.daren_channel_state() == "logged_out":
            from . import failure_text as _ft
            from . import ops_alert as _oa
            exc = _exec.NeedLogin(
                "达人端采集账号登录态过期了，需要重新扫码登录", side="达人端")
            sender.send_text(
                chat_id,
                _ft.user_text_for(exc, product=True) + "（这次没有扣积分）")
            _oa.alert(
                "⚠️ 达人端采集通道掉登录了",
                _ft.ops_alert_body(
                    exc, context="达人发商品链接时前置预检命中（未入队、未扣分）"),
                kind="login_lost",
                dedup_key="daren_collector_need_login")
            return

        if db.get_balance(tk) < config.COST_COLLECT_PRODUCT:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_COLLECT_PRODUCT} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "product", product_key, "collect_product", chat_id=chat_id)
        if status == "insufficient":
            sender.send_text(chat_id, credits.insufficient_text("collect_product", bal))
            return
        if status == "duplicate":
            sender.send_text(
                chat_id, f"这个商品正在分析中，结论出来直接发你～（积分余额 {bal}）")
            return
        tip = ("收到商品ID，正在查这个商品的数据，分析中" if label == "商品ID"
               else "收到链接，打开商品页分析中")
        sender.send_text(
            chat_id,
            f"{tip}，大约 1-2 分钟～（积分余额 {bal}）")

    # ---------- 视频链接拆解（直链下载+转写+画面+报告） ----------

    def _route_video(self, tenant, chat_id, text, sender) -> None:
        tk = tenant["tenant_key"]
        url = VIDEO_LINK_RE.search(text).group(0)
        if db.get_balance(tk) < config.COST_VIDEO:
            sender.send_text(
                chat_id,
                f"视频拆解需要 {config.COST_VIDEO} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return
        status, _task_id, bal = credits.charge_and_enqueue(
            tk, "video", url, "video", chat_id=chat_id)
        if status == "insufficient":
            sender.send_text(chat_id, credits.insufficient_text("video", bal))
            return
        if status == "duplicate":
            sender.send_text(
                chat_id, f"这个视频正在拆解中，拆完直接发你～（积分余额 {bal}）")
            return
        sender.send_text(chat_id,
                         "收到视频链接，拆解中：下载→转写口播→看画面→出拆解报告，"
                         f"大约需要 3-8 分钟～（积分余额 {bal}）")
