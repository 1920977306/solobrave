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

from . import config, db, cockpit_link

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
        elif msg.message_type == "text":
            text = content.get("text", "")
            m = DOUYIN_IN_TEXT_RE.search(text) or DOUYIN_RE.match(text)
            if cockpit_link.is_cockpit_url(text):
                # 达人把自己收到的驾驶舱链接发回来 → 回入口，别当成商品链接扣 20 分
                self._route_cockpit_link(tenant, chat_id, text, sender)
            elif m:
                self._route_douyin(tenant, chat_id, m.group(1), sender,
                                   name_hint=_extract_name_hint(text, m.group(1)))
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
            elif TRIAL_WEEKLY_RE.match(text.strip()):
                self._route_trial_weekly(tenant, chat_id, text, sender)
            elif len(text.strip()) <= 8 and GREETING_RE.search(text):
                sender.send_text(chat_id, GREETING_REPLY)
            elif cockpit_link.is_cockpit_link_question(text):
                # 驾驶舱入口导航（2026-10-20 交接项①）：零成本，不扣积分
                self._route_cockpit_link(tenant, chat_id, text, sender)
            else:
                # 业务问答：入队异步走 LLM（扣 1 积分），超出带货范围由模型拒答
                self._route_qa(tenant, chat_id, text, sender)
        else:
            sender.send_text(chat_id, UNSUPPORTED_REPLY)

        elapsed = time.time() - t0
        if elapsed > 3:
            log.warning("HANDLER OVER 3s: tenant=%s elapsed=%.2fs",
                        tenant["tenant_key"], elapsed)

    # ---------- 管理员后门：立即试跑周报（只回管理员，不推达人） ----------

    def _route_cockpit_link(self, tenant, chat_id, text, sender):
        """驾驶舱入口导航（2026-10-20 交接项①，2026-10-21 补做）。

        **零成本** —— 纯导航不是分析，不该扣积分。
        链接是达人专属 k（一人一链），只发给他本人。
        ⚠️ 日志只记 tenant_key，**不打 k**（k 等同于访问密码）。
        """
        tk = tenant["tenant_key"]
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

        # v1.1 收紧：先回确认卡片，再扣分
        sender.send_text(
            chat_id,
            "已开始采集，正常5-8分钟出报告" if is_self
            else f"已开始采集对标账号 {dy_id}，正常5-8分钟出报告；"
                 "你的号和对标号数据可以对比着看")
        ok, _ = db.deduct(tk, config.COST_COLLECT_DOUYIN, "collect_douyin")
        if not ok:  # 并发下余额竞争失败
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        db.enqueue_task(tk, "douyin", dy_id, config.COST_COLLECT_DOUYIN,
                        chat_id=chat_id,
                        payload=json.dumps({"name_hint": name_hint},
                                           ensure_ascii=False) if name_hint else '')
        if first_bind:
            log.info("tenant=%s first-bind douyin_id=%s", tk, bound)
        elif not is_self:
            log.info("tenant=%s peer-collect dy_id=%s (peers=%s)", tk, dy_id,
                     db.peer_douyin_count(tk, bound))

    # ---------- ② 商品截图（spec 4.2） ----------

    def _route_image(self, tenant, msg, content, sender) -> None:
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

        ok, bal = db.deduct(tk, config.COST_VISION, "vision")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        sender.send_text(chat_id, f"收到截图，识别中～（积分余额 {bal}）")
        # vision 走 worker（3 秒硬约束）；废图（识别不出）在 worker 里返还
        db.enqueue_task(tk, "image", str(path), config.COST_VISION,
                        chat_id=chat_id)

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
        if db.get_balance(tk) < config.COST_QA:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_QA} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return
        ok, bal = db.deduct(tk, config.COST_QA, "qa")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        sender.send_text(chat_id, f"收到，我想一下～（积分余额 {bal}）")
        db.enqueue_task(tk, "qa", q[:200], config.COST_QA, chat_id=chat_id)

    # ---------- ③ 商品链接（spec 4.3） ----------

    def _route_link(self, tenant, chat_id, text, sender) -> None:
        tk = tenant["tenant_key"]
        url = URL_RE.search(text).group(0)
        product_key = normalize_product_url(url)

        cached = db.cache_get(product_key)
        if cached:
            ok, bal = db.deduct(tk, config.COST_PRODUCT_CACHED,
                                "collect_product_cached", cache_hit=1)
            if not ok:
                sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
                return
            sender.send_text(
                chat_id,
                f"收到链接（缓存命中，秒出结果）～（积分余额 {bal}）")
            db.enqueue_task(tk, "product", product_key,
                            config.COST_PRODUCT_CACHED, cache_hit=1,
                            chat_id=chat_id)
            return

        if db.get_balance(tk) < config.COST_COLLECT_PRODUCT:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_COLLECT_PRODUCT} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return
        ok, bal = db.deduct(tk, config.COST_COLLECT_PRODUCT, "collect_product")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        sender.send_text(
            chat_id,
            f"收到链接，打开商品页分析中，大约 1-2 分钟～（积分余额 {bal}）")
        db.enqueue_task(tk, "product", product_key, config.COST_COLLECT_PRODUCT,
                        chat_id=chat_id)

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
        ok, bal = db.deduct(tk, config.COST_VIDEO, "teardown_video")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        sender.send_text(chat_id,
                         "收到视频链接，拆解中：下载→转写口播→看画面→出拆解报告，"
                         f"大约需要 3-8 分钟～（积分余额 {bal}）")
        db.enqueue_task(tk, "video", url, config.COST_VIDEO, chat_id=chat_id)
