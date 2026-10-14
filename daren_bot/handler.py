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

from . import config, db

log = logging.getLogger("daren-bot.handler")

DOUYIN_RE = re.compile(r"^\s*([A-Za-z0-9_]{5,24})\s*$")
DOUYIN_IN_TEXT_RE = re.compile(r"抖音号\s*[:：]?\s*([A-Za-z0-9_]{4,24})")
URL_RE = re.compile(r"https?://[^\s，。]+")
TRACKING_PREFIXES = ("utm_", "spm", "fbclid", "gclid", "tt_")

UNRELATED_REPLY = ("我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，"
                   "其他问题帮不上忙哦～")
UNSUPPORTED_REPLY = "这类消息我还处理不了，发我抖音号、商品截图或商品链接都可以～"


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

        if msg.message_type == "image":
            self._route_image(tenant, msg, content, sender)
        elif msg.message_type == "text":
            text = content.get("text", "")
            m = DOUYIN_IN_TEXT_RE.search(text) or DOUYIN_RE.match(text)
            if m:
                self._route_douyin(tenant, chat_id, m.group(1), sender,
                                   name_hint=_extract_name_hint(text, m.group(1)))
            elif URL_RE.search(text):
                self._route_link(tenant, chat_id, text, sender)
            else:
                # 意图分类未接入前的零成本兜底（spec 4.4：不进主链路、零积分变动）
                sender.send_text(chat_id, UNRELATED_REPLY)
        else:
            sender.send_text(chat_id, UNSUPPORTED_REPLY)

        elapsed = time.time() - t0
        if elapsed > 3:
            log.warning("HANDLER OVER 3s: tenant=%s elapsed=%.2fs",
                        tenant["tenant_key"], elapsed)

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
        elif bound != dy_id:
            sender.send_text(
                chat_id,
                f"本助手已绑定你的抖音号 {bound}，查别人的号内测期请联系运营")
            return

        if db.get_week_count(tk, bound) >= 1:
            sender.send_text(
                chat_id, "本周已分析过你的账号，下周更新；加急联系运营")
            return

        if db.get_balance(tk) < config.COST_COLLECT_DOUYIN:
            sender.send_text(
                chat_id,
                f"这个动作需要 {config.COST_COLLECT_DOUYIN} 积分，你的余额不够啦。"
                "联系运营老师补充额度（内测期免费发放）")
            return

        # v1.1 收紧：先回确认卡片，再扣分
        sender.send_text(chat_id, "已开始采集，正常5-8分钟出报告")
        ok, _ = db.deduct(tk, config.COST_COLLECT_DOUYIN, "collect_douyin")
        if not ok:  # 并发下余额竞争失败
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        db.enqueue_task(tk, "douyin", bound, config.COST_COLLECT_DOUYIN,
                        chat_id=chat_id,
                        payload=json.dumps({"name_hint": name_hint},
                                           ensure_ascii=False) if name_hint else '')
        if first_bind:
            log.info("tenant=%s first-bind douyin_id=%s", tk, bound)

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

        ok, _ = db.deduct(tk, config.COST_VISION, "vision")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        sender.send_text(chat_id, "收到截图，识别中")
        # vision 走 worker（3 秒硬约束）；废图（识别不出）在 worker 里返还
        db.enqueue_task(tk, "image", str(path), config.COST_VISION,
                        chat_id=chat_id)

    # ---------- ③ 商品链接（spec 4.3） ----------

    def _route_link(self, tenant, chat_id, text, sender) -> None:
        tk = tenant["tenant_key"]
        url = URL_RE.search(text).group(0)
        product_key = normalize_product_url(url)

        sender.send_text(chat_id, "收到链接，分析中")

        cached = db.cache_get(product_key)
        if cached:
            ok, _ = db.deduct(tk, config.COST_PRODUCT_CACHED,
                              "collect_product_cached", cache_hit=1)
            if not ok:
                sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
                return
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
        ok, _ = db.deduct(tk, config.COST_COLLECT_PRODUCT, "collect_product")
        if not ok:
            sender.send_text(chat_id, "积分余额不足，联系运营老师补充额度")
            return
        db.enqueue_task(tk, "product", product_key, config.COST_COLLECT_PRODUCT,
                        chat_id=chat_id)
