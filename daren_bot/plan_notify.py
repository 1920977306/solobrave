"""排品表 + 每日提醒（飞书侧）。

起因（老大 2026-10-24）
----------------------
「AI 助手一直是围绕着自己的达人账号做分析的」——
账号分析、选品建议、商品判断都有，但**达人接了品之后怎么排、什么时候推、什么时候提醒**，
这一条空着。达人每天接十几个品，不排就会：
① 爆款期错过；② 尾货堆积；③ 一天推太多（视频位有限）。

本模块负责三件事
----------------
1. `register_deal()`   —— 达人说「我接了这个品」⇒ 落成合作单（排品池的入口）
2. `plan_for_talent()` —— 给某个达人算排品表（画像 × 生命周期 × 佣金 ⇒ 谁先推）
3. `PlanScheduler`     —— 每天扫一遍，把「今天该推哪两个」推给达人（幂等，不重复推）

⚠️ 只读达人数据（`PRAGMA query_only=ON`）；写 `deals` 走**普通连接**，
   并且只在「达人明确说接了」时写（不造合作单）。
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from . import db
from .executors import SOLOBRAVE_DB

log = logging.getLogger("daren-bot.plan")

# 排品表用的算法在 daren_cockpit.tools.product_plan（纯函数，和服务端共用一份）
import sys
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from daren_cockpit.tools import product_plan as plan  # noqa: E402
# 商品事实解析（佣金率口径）和推荐器/排品器共用一份 —— 别在这儿另写一套
from daren_cockpit.tools import product_facts as facts  # noqa: E402


# ────────────────────────── 常量 ──────────────────────────

# 每天推几个（达人视频位有限；观众会疲劳）
PLAN_PER_DAY = 2
# 排期看几天
PLAN_DAYS = 7
# 提醒时刻（小时，24 小时制）—— 上午 9 点，达人开播前
REMIND_HOUR = 9
REMIND_MINUTE = 37
# 扫描间隔（秒）
SCAN_INTERVAL = 300
# 进排品池的合作单状态
PLAN_DEAL_STATUSES = ("negotiating", "sample_sent", "approved")
# 灰度：先只推已绑抖音号的租户（和 coop 一致）
PLAN_GRAY_TENANTS = ("daren_boss", "daren_test2")


# ────────────────────────── 数据读取 ──────────────────────────

def _ro_conn():
    """只读连接（引擎级禁写，见 executors._ro_conn 的踩坑注释）。"""
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def _read_talent(douyin_id: str, talent_id: str = "") -> dict | None:
    if not SOLOBRAVE_DB.is_file():
        return None
    conn = _ro_conn()
    try:
        if talent_id:
            r = conn.execute("SELECT * FROM talents WHERE id=?",
                             (talent_id,)).fetchone()
            if r:
                return dict(r)
        if douyin_id:
            r = conn.execute(
                "SELECT * FROM talents WHERE douyin_id=? "
                "ORDER BY created_at DESC LIMIT 1", (douyin_id,)).fetchone()
            if r:
                return dict(r)
        return None
    finally:
        conn.close()


def _read_deals(talent_id: str) -> list[dict]:
    conn = _ro_conn()
    try:
        rows = conn.execute(
            f"SELECT * FROM deals WHERE talent_id=? AND status IN "
            f"({','.join('?' * len(PLAN_DEAL_STATUSES))})",
            (talent_id,) + tuple(PLAN_DEAL_STATUSES)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _read_products() -> list[dict]:
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT id,name,price,price_range,category,commission_rates,tags,"
            "shop_score,good_ratio,video_sales_7d,sales_7d,board_rank,"
            "sale_axis_json FROM products WHERE status!='archived'").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ────────────────────────── 对外能力 ──────────────────────────

def plan_for_talent(douyin_id: str, talent_id: str = "",
                     days: int = PLAN_DAYS, per_day: int = PLAN_PER_DAY,
                     start_at: int = 0):
    """某个达人的排品表 → (slots, talent)。查不到达人返回 (None, None)。"""
    talent = _read_talent(douyin_id, talent_id)
    if not talent:
        return None, None
    deals = _read_deals(talent["id"])
    products = _read_products()
    slots = plan.build_schedule(talent, deals, products,
                                days=days, per_day=per_day, start_at=start_at)
    return slots, talent


def plan_text(douyin_id: str, talent_id: str = "") -> str:
    """排品表 → 给达人看的文字（达人主动问时用）。"""
    slots, talent = plan_for_talent(douyin_id, talent_id)
    if not talent:
        return ("还没采到你的账号数据，排品表要等账号数据齐了才能生成。"
                "发你的抖音号我可以先帮你拉一份。")
    if not slots or not any(s.items for s in slots):
        return ("你名下还没有登记待推的品。\n"
                "接了品之后发我「接了 <商品ID或商品链接>」，"
                "我就能帮你排哪天推哪个、哪个该清了。")
    return plan.plan_text(slots, talent.get("name") or "")


def register_deal(douyin_id: str, product_id: str, talent_id: str = "",
                  deal_source: str = "达人自选") -> tuple[bool, str]:
    """登记「达人接了这个品」→ 落一条合作单（status=approved，进排品池）。

    返回 (ok, 说明)。**幂等**：同一个达人 + 同一个品只登记一次。
    """
    talent = _read_talent(douyin_id, talent_id)
    if not talent:
        return False, "还没采到你的账号数据，先发你的抖音号我才能登记。"
    if not product_id:
        return False, "没看到商品ID，发「接了 <商品ID>」就行。"
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    conn.row_factory = sqlite3.Row     # ⚠️ 写连接也要设！否则 fetchone() 返回 tuple，
                                       #    下面 prow["id"] 直接 TypeError（2026-10-24 踩过）
    try:
        # 商品定位：**先按我方 id 精确匹配，再按平台商品 id** ——
        #   一条 OR 查询 + LIMIT 1 在两列都命中时命中哪行是不确定的。
        prow = conn.execute(
            "SELECT id, name, commission_rates FROM products "
            "WHERE id=? LIMIT 1", (product_id,)).fetchone()
        if not prow:
            # ⚠️ 排序只按 `id`：`daren_synced_at` 是采集器才加的列，
            #    不是所有部署都有，拿它排序会在缺列的库上直接报错。
            prow = conn.execute(
                "SELECT id, name, commission_rates FROM products "
                "WHERE platform_product_id=? ORDER BY id LIMIT 1",
                (product_id,)).fetchone()
        if not prow:
            return False, (f"商品 {product_id} 不在我们库里，"
                           f"先把商品链接发我，我采完再登记。")
        exist = conn.execute(
            "SELECT id FROM deals WHERE talent_id=? AND product_id=? "
            "AND status IN ('negotiating','sample_sent','approved') LIMIT 1",
            (talent["id"], prow["id"])).fetchone()
        if exist:
            return False, f"这个品已经登记过了（{prow['name'][:18]}），不用重复接。"
        comm = facts.commission_rate_of(dict(prow))
        now = int(time.time())
        deal_id = "deal_" + uuid.uuid4().hex[:12]
        conn.execute(
            "INSERT INTO deals (id, talent_id, product_id, product_name, "
            "deal_type, commission_rate, status, scheduled_at, deal_source, "
            "created_by, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (deal_id, talent["id"], prow["id"], prow["name"] or "",
             "video", comm, "approved", 0, deal_source, "daren_bot",
             now, now))
        conn.commit()
        return True, (f"收到，已登记：{prow['name'][:24]}\n"
                      f"（佣金率 {comm:g}%｜我现在帮你排进这周的品表，"
                      f"明天早上会推「今天推哪两个」给你）")
    finally:
        conn.close()


# ────────────────────────── 每日提醒 ──────────────────────────

def _gray_map() -> dict:
    """灰度租户 → 抖音号。"""
    out = {}
    for tk in PLAN_GRAY_TENANTS:
        try:
            t = db.get_tenant(tk)
        except Exception:
            t = None
        if not t:
            continue
        dyid = (t.get("bind_douyin_id") or "").strip()
        if dyid:
            out[tk] = dyid
    return out


def _already_reminded(deal_id: str, day_key: str) -> bool:
    """今天这个品是否已经提醒过（幂等）。"""
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT remind_state FROM deals WHERE id=?",
                         (deal_id,)).fetchone()
        return bool(r and r["remind_state"] == day_key)
    finally:
        conn.close()


def _mark_reminded(deal_id: str, day_key: str) -> None:
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    try:
        conn.execute("UPDATE deals SET remind_state=? WHERE id=?",
                     (day_key, deal_id))
        conn.commit()
    finally:
        conn.close()


def _today_slots(douyin_id: str, talent_id: str = ""):
    """今天的排期（当天 00:00 起的 slot）。"""
    slots, talent = plan_for_talent(douyin_id, talent_id)
    if not talent or not slots:
        return [], None
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    for s in slots:
        if s.date == today:
            return s.items, talent
    return [], talent


def build_reminder(douyin_id: str, talent_id: str = "") -> tuple[str, list[str]]:
    """今日提醒文案 + 要标记的 deal_id 列表。

    ★ 幂等落在 **DB 的 `remind_state`** 上（不是进程内存）——
      机器人当天重启后 `PlanScheduler._last_day` 会清空，
      如果幂等只靠内存，重启一次就把今天的提醒重推一遍。
      所以这里先看「今天该推的是不是都已经推过了」：
      全部推过 ⇒ 直接返回空（不重复推）。
    """
    items, talent = _today_slots(douyin_id, talent_id)
    if not talent:
        return "", []
    if not items:
        return "", []
    day_key = datetime.datetime.now().strftime("%Y-%m-%d")
    markable = [it for it in items if it.deal_id]
    todo = [it.deal_id for it in markable
            if not _already_reminded(it.deal_id, day_key)]
    if markable and not todo:
        # 今天该推的都推过了 ⇒ 不重复推（重启也守住）
        return "", []
    name = talent.get("name") or ""
    lines = [f"【{name} 今天推这两件】"] if name else ["【今天推这两件】"]
    for i, it in enumerate(items, 1):
        lc = it.lifecycle.label if it.lifecycle else ""
        meta = " · ".join(x for x in (
            (f"¥{it.price:g}" if it.price else ""),
            (f"佣金{it.commission_rate:g}%" if it.commission_rate else ""),
            lc) if x)
        lines.append(f"{i}. {it.product_name[:30]}（{meta}）")
        for r in (it.reasons or [])[:2]:
            lines.append(f"   · {r}")
    # 尾货提醒：提醒还没排进今天的尾货（该清了）
    tail_left = _tail_pending(douyin_id, talent_id, items)
    if tail_left:
        lines.append("")
        lines.append(f"⏰ 还有 {len(tail_left)} 个品是尾货期（基本没动销），"
                     f"再不推就过期了：")
        for it in tail_left[:3]:
            lines.append(f"   · {it.product_name[:26]}"
                         f"（{it.lifecycle.note if it.lifecycle else ''}）")
    return "\n".join(lines), todo


def _tail_pending(douyin_id: str, talent_id: str, today_items) -> list:
    """还没排进今天的尾货品。"""
    slots, talent = plan_for_talent(douyin_id, talent_id)
    if not talent:
        return []
    today_ids = {i.deal_id for i in today_items}
    out = []
    for s in slots:
        for it in s.items:
            if it.deal_id in today_ids:
                continue
            if it.lifecycle and it.lifecycle.stage == plan.LIFECYCLE_TAIL:
                out.append(it)
    return out


def scan_and_push(sender_factory) -> dict:
    """扫一遍灰度租户，把「今天推哪两个」推给有排期的达人。

    返回统计（和 coop_notify.scan_and_push 同样的形状，方便运维看）。
    """
    stat = {"scanned": 0, "pushed": 0, "skipped": 0, "details": []}
    gray = _gray_map()
    if not gray:
        log.info("[plan] 灰度名单内没有已绑定抖音号的租户 → 本轮不推")
        return stat
    day_key = datetime.datetime.now().strftime("%Y-%m-%d")
    for tk, dyid in gray.items():
        stat["scanned"] += 1
        try:
            chat_id = db.get_latest_chat_id(tk)
            if not chat_id:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "没有 chat_id"})
                continue
            text, todo = build_reminder(dyid)
            if not text:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "今天没有要推的品（没排期/已推过）"})
                continue
            sender = sender_factory(tk)
            if sender is None:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "拿不到 sender"})
                continue
            sender.send_text(chat_id, text)
            stat["pushed"] += 1
            stat["details"].append({"tenant": tk, "deals": todo})
            for did in todo:
                try:
                    _mark_reminded(did, day_key)
                except Exception:
                    log.exception("mark reminded failed: %s", did)
        except Exception:
            log.exception("[plan] push failed: %s", tk)
            stat["skipped"] += 1
    log.info("[plan] 扫描统计: %s", {k: v for k, v in stat.items() if k != "details"})
    return stat


class PlanScheduler(threading.Thread):
    """排品提醒调度器（每 SCAN_INTERVAL 秒扫一次，到点就推今日排品）。"""

    def __init__(self, wm=None):
        super().__init__(daemon=True, name="plan-scheduler")
        self.wm = wm
        self._stop = threading.Event()
        self._last_day = ""

    def stop(self):
        self._stop.set()

    def _sender_factory(self, tk):
        if self.wm is None:
            return None
        try:
            return self.wm.get_sender(tk)
        except Exception:
            return None

    def run(self):
        log.info("plan scheduler started (interval=%ds, gray=%s)",
                 SCAN_INTERVAL, ",".join(PLAN_GRAY_TENANTS))
        while not self._stop.wait(SCAN_INTERVAL):
            try:
                self._tick()
            except Exception:
                log.exception("plan scheduler tick failed")

    def _tick(self):
        now = datetime.datetime.now()
        if now.hour < REMIND_HOUR or (now.hour == REMIND_HOUR
                                      and now.minute < REMIND_MINUTE):
            return
        today = now.strftime("%Y-%m-%d")
        if self._last_day == today:
            return          # 一天只主动推一次
        stat = scan_and_push(self._sender_factory)
        if stat.get("pushed"):
            self._last_day = today
