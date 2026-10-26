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

# ──────────────── 今日提醒的**状态**（2026-10-26 新增）────────────────
#
# ★ 为什么必须把状态拆出来（老大 2026-10-26 拍板「今天没排品也要发占位消息」）：
#   原来 `_reminder_parts` 把「达人查不到」和「今天没品」**折叠成同一个** `("", [], [])`
#   ⇒ `scan_and_push` 只看 `if not text:` ⇒ 一条消息都不发（达人彻底静默）。
#   ⇒ 而这两种情况的**处置完全相反**：
#      - 「达人数据缺失」= **我们的问题**（数据没采到）⇒ 发「今天没排品」是**误报**；
#      - 「确实没品可推」= 达人的现状 ⇒ 该发占位让达人知道系统在跑。
#   ⇒ 状态必须是**显式枚举**，不许靠 `text == ''` 反推（把「缺一个输入」说成「答不了」）。
PLAN_STATE_OK = "ok"                        # 今天有品要推
PLAN_STATE_NO_TALENT = "no_talent"          # 达人数据缺失（我们的问题）⇒ 不发占位
PLAN_STATE_NO_POOL = "no_pool"              # 名下**一张单都没有** ⇒ 发占位
PLAN_STATE_NO_TODAY_ITEM = "no_today_item"  # 池里有单但今天没排到 ⇒ 发占位
PLAN_STATE_ALREADY_SENT = "already_sent"    # 今天该推的都推过了 ⇒ 不发（幂等）

# 空态占位消息的幂等 kind（落在 `bot_plan_notice_log`）
PLAN_NOTICE_EMPTY = "empty_plan"


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
            # 达人可能报过「抖音号：xxx」但库里 `douyin_id` 存的是另一个形态
            # （实测：库里的 `douyin_id` 有时是 `platform_talent_id`，有时是原始号）
            r = conn.execute(
                "SELECT * FROM talents WHERE platform_talent_id=? "
                "OR douyin_uid=? ORDER BY created_at DESC LIMIT 1",
                (douyin_id, douyin_id)).fetchone()
            if r:
                return dict(r)
        return None
    finally:
        conn.close()


def read_talent(douyin_id: str, talent_id: str = "") -> dict | None:
    """按抖音号 / 达人 id 找达人行（**公开入口**）。

    ★ 其它模块（`daren_bot/handler.py` 的选品推荐路由）也按达人身份取数
      ⇒ 别在别处再写一份查 `talents` 的 SQL（同一份事实只写一份）。
    ★ 实现只有这一份，`_read_talent` 是它的内部名。
    """
    return _read_talent(douyin_id, talent_id)


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
    # ★ 只展示**今天及以后**的排期 ——
    #   排期落库后（见 persist_plan），`build_schedule` 会把过去的排期
    #   原样带回来（那是历史，不是待办）。给达人看表时把过去的日子滤掉，
    #   否则「我的排品表」第一条永远是昨天，达人会以为系统没在排。
    future = plan.future_slots(slots)
    if not future or not any(s.items for s in future):
        return ("你名下这批品都排完/推过了。\n"
                "运营给你派新品、或者你发「接了 <商品ID>」，"
                "我马上帮你排进这周的品表。")
    return plan.plan_text(future, _display_name(talent))


# ────────────────────────── 排期落库 ──────────────────────────

def persist_plan(slots, talent_id: str = "", dry_run: bool = False) -> int:
    """把算出来的排期**写回** `deals`（scheduled_at / plan_slot / priority / plan_reason）。

    ★ 为什么必须有这一步（2026-10-24 实测根因）：
      排品算法有**两个消费者** ——
        ① 服务端 `POST /api/deals/plan`：**落库**
        ② 机器人 `scan_and_push`：**只算不写**
      只有 ① 落库 ⇒ 达人侧的 `deals.scheduled_at` 永远是 0
      ⇒ `build_schedule` 里「已有排期的保持原位」这条规则**从来没生效过**
      ⇒ 每天从头现算 ⇒ **明天会再推一遍一模一样的两个品**
      （实测探针：`今天算出来的今天` == `明天算出来的明天` ⇒ True）

      排品表要成立，**算出来的安排必须落到 DB** —— 否则「安排」等于没做，
      达人每天收到的都是同一批品，AI 排期这件事在产品上就等于不存在。

    `dry_run=True` 时**一个字都不写**（探针必须无副作用）。
    """
    if dry_run:
        return 0
    now = int(time.time())
    rows = []
    for slot in slots:
        for item in slot.items:
            if not item.deal_id or not item.scheduled_at:
                continue        # 排不进本周的品保持 scheduled_at=0，等下一轮
            rows.append((int(item.scheduled_at), float(item.priority or 0),
                         int(item.plan_slot or 0),
                         " | ".join(item.reasons or [])[:500],
                         now, item.deal_id))
    if not rows:
        return 0
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    try:
        if talent_id:
            conn.executemany(
                "UPDATE deals SET scheduled_at=?, priority=?, plan_slot=?, "
                "plan_reason=?, updated_at=? WHERE id=? AND talent_id=?",
                [r + (talent_id,) for r in rows])
        else:
            conn.executemany(
                "UPDATE deals SET scheduled_at=?, priority=?, plan_slot=?, "
                "plan_reason=?, updated_at=? WHERE id=?", rows)
        conn.commit()
    finally:
        conn.close()
    return len(rows)


def requeue_overdue(talent_id: str, day_key: str = "",
                    dry_run: bool = False) -> list[str]:
    """把「排期已经过去、但**从来没推成功过**」的品退回排品池重新排。

    ★ 不能丢品 —— 达人接了品就该被推到：
      如果某天整批推送失败（网络/权限），那个品的排期就停在过去那天；
      而「今天该推哪两个」只看**当天** ⇒ 这个品**再也不会被推出去**
      ⇒ 达人白接了。所以这里把这类品的 `scheduled_at` 清零，
      让它重新进 pending，被排到今天或下一个空坑。

    ⚠️ 只认 `remind_state` **为空**才补 —— 已经推过的（哪怕推晚了）不重复推。
      这是「错挂比漏挂贵」的对偶：宁可晚推，不可重复骚扰达人。
    """
    if not SOLOBRAVE_DB.is_file() or not talent_id:
        return []
    day_key = day_key or datetime.datetime.now().strftime("%Y-%m-%d")
    today_ts = int(datetime.datetime.strptime(day_key, "%Y-%m-%d").timestamp())
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id FROM deals WHERE talent_id=? AND scheduled_at > 0 "
            "AND scheduled_at < ? "
            "AND (remind_state IS NULL OR remind_state='')",
            (talent_id, today_ts)).fetchall()
        ids = [r["id"] for r in rows]
        if ids and not dry_run:
            conn.execute(
                "UPDATE deals SET scheduled_at=0, plan_slot=0 WHERE id IN (%s)"
                % ",".join("?" * len(ids)), ids)
            conn.commit()
        return ids
    finally:
        conn.close()


# ────────────────────── 池级排期（排品池 = 我们的合作单） ──────────────────────

def pool_talent_ids() -> list[str]:
    """排品池里**所有**有单的达人 id —— **不论有没有飞书租户**。

    ★ 排品池的真源是 `deals`（我们的合作单），不是「有租户的达人」。
      池子里的人是运营建单 / 达人接品进来的，
      **他们不一定绑了飞书租户**（实测：七月姐姐 5 单，没租户）。
    """
    if not SOLOBRAVE_DB.is_file():
        return []
    conn = _ro_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT talent_id FROM deals "
            "WHERE talent_id IS NOT NULL AND talent_id<>'' "
            "AND status IN ('negotiating','sample_sent','approved') "
            "ORDER BY talent_id").fetchall()
        return [r["talent_id"] for r in rows]
    finally:
        conn.close()


def reschedule_pool(day_key: str = "", dry_run: bool = False) -> dict:
    """把**排品池里所有达人**的排期重算 + 落库（含过期重排）。

    ★ 遍历范围必须是**排品池**，不是「有飞书租户的达人」。

    【实测缺陷（2026-10-25）】
    之前「重排 + 落库」都长在 `scan_and_push()` 里，而 `scan_and_push`
    只遍历**灰度租户绑定的达人** ⇒ 没绑飞书租户的达人
    （实测：七月姐姐 5 单）**排期永远停在过去**：
      - `requeue_overdue` 够不着她 ⇒ 过期未推的品不会退回重排
      - `persist_plan` 也不碰她 ⇒ 新排期不会落库
    ⇒ 她的品在 H5 排品表里**直接消失**（表只显示今天及以后），
      运营看着像「少排了」，其实是**卡在昨天**。
    这跟「多消费者只一个落库」是同族问题：
      **同一份事实，多个消费者，遍历范围不一致**。

    ⇒ 分工：**推送给有租户的达人**（飞书侧的物理限制），
      **排期算给池子里所有人**（排期是池子的属性，不是租户的）。
    """
    day_key = day_key or datetime.datetime.now().strftime("%Y-%m-%d")
    out = {"talents": 0, "requeued": 0, "persisted": 0, "details": []}
    for tid in pool_talent_ids():
        try:
            requeued = requeue_overdue(tid, day_key, dry_run=dry_run)
            slots, talent = plan_for_talent("", tid)
            if not slots or not talent:
                continue
            n = persist_plan(slots, talent_id=tid, dry_run=dry_run)
            out["talents"] += 1
            out["requeued"] += len(requeued)
            out["persisted"] += n
            out["details"].append({"talent_id": tid, "requeued": requeued,
                                    "persisted": n})
        except Exception:
            log.exception("[plan] pool reschedule failed: talent=%s", tid)
    return out


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
        # ★ 当场把排期算出来落库 ——
        #   否则回达人那句「我现在帮你排进这周的品表」是**空头承诺**：
        #   排期要等下一次调度器扫描（最多 5 分钟）才落，
        #   达人和运营马上查排品表会看到空的。
        #   （声明端 ≠ 使用端：说了要做，就得当场做。）
        try:
            slots, _t = plan_for_talent(douyin_id, talent["id"])
            if slots:
                persist_plan(slots, talent_id=talent["id"])
        except Exception:
            # 落库失败**不阻断登记**（品已经接了，排期明天补上）
            log.exception("[plan] 登记后落排期失败（不阻断）")
        return True, (f"收到，已登记：{prow['name'][:24]}\n"
                      f"（佣金率 {comm:g}%｜我已经帮你排进这周的品表，"
                      f"每天早上会推「今天推哪个」给你）")
    finally:
        conn.close()


# ────────────────────────── 排品池的真源 ──────────────────────────
#
# ★ 排品池 = **我们自己系统里的合作单**（`deals` 表，状态 negotiating /
#   sample_sent / approved）。来源有两条：
#     ① 达人在飞书说「接了 <商品ID>」→ `register_deal()`
#     ② 运营/团长在后台建合作单
#
# ⚠️ **不要用达人选品车当真源**（2026-10-24 老大明确纠正）：
#   「达人端只是一个搜索商品的功能才会用到，其他的不用碰」。
#   选品车是达人在**平台侧**的私事，不是我们的业务数据；
#   拿它当排品真源 = 越界采集 + 归属错（只有一个登录账号时只会串到那一个人）。
#   ⇒ 采集器已下线，本模块不再有任何自动同步。
#
# 达人端浏览器（`buyin_worker`）的**唯一**用途：**按商品ID搜商品**，
#   顺带抓该商品的维度用于建档/推荐。别的都不碰。


# ────────────────────────── 每日提醒 ──────────────────────────

def _gray_map() -> dict:
    """灰度租户 → **最近活跃的**抖音号。

    ⚠️ 不用 `bind_douyin_id` —— 实测错位过（2026-10-24）：
       `daren_test2` 配的是 324733241（萌萌超会买），
       但老大实际在飞书聊的是 Ayn11930（一百）。
       查错人 ⇒ 排品表/提醒落到别人的数据上 = 看起来「这功能没做」。
    """
    out = {}
    for tk in PLAN_GRAY_TENANTS:
        try:
            t = db.get_tenant(tk)
        except Exception:
            t = None
        if not t:
            continue
        dyid = (db.get_latest_douyin_id(tk) or
                t.get("bind_douyin_id") or "").strip()
        if dyid:
            out[tk] = dyid
    return out


def _display_name(talent: dict) -> str:
    """给达人看的名字。

    ★ 优先用**平台昵称**（`/index/getUser` 的 `user_name`）——
      库里的 `name` 可能是运营随手写的（实测「我的小号发给你跟我分析一下名称一百」），
      直接用在提醒标题里很难看。
    ★ 口径与服务端 `/api/deals/plan` **共用一份**（`product_plan.display_name`）——
      同一个算法两个消费者，显示口径也分叉 ⇒ 运营看到的和达人看到的不一样。
    """
    return plan.display_name(talent)


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
    text, todo, _items, _state = _reminder_parts(douyin_id, talent_id)
    return text, todo


def build_reminder_state(douyin_id: str, talent_id: str = "") -> tuple[str, list[str], str]:
    """今日提醒 + 待标记 deal + **状态**（新增出口）。

    ★ 为什么要单独一个出口（2026-10-26）：`build_reminder` 只给「有品可推」的文案，
      空态的处置（发不发占位、发的什么）需要**状态**才能判断，
      而状态不能靠 `text == ''` 反推（那把「达人查不到」和「今天没品」混成一团）。
    """
    text, todo, _items, state = _reminder_parts(douyin_id, talent_id)
    return text, todo, state


def _reminder_parts(douyin_id: str, talent_id: str = ""):
    """提醒的产物：文字版 / 待标记 deal / 今天的品 / **状态**。

    ★ 2026-10-26：返回值从 3 元组变 4 元组（多了 `state`）。
      原来「达人查不到」和「今天没品」都返回 `("", [], [])` —— 两者处置完全相反，
      折叠在一起 ⇒ 只能「一律不发」，达人彻底静默。
      改 4 元组而不是加个新函数：`_reminder_parts` 是**唯一**知道
      「达人查不到 vs 今天没排到 vs 已推过」的地方，状态必须从这里出来（口径单一真源）。
    """
    items, talent = _today_slots(douyin_id, talent_id)
    if not talent:
        # 达人数据缺失 = **我们的问题**（没采到账号数据），不是「没品可推」
        return "", [], [], PLAN_STATE_NO_TALENT
    if not items:
        # 区分「池里一张单都没有」 vs 「池里有单但今天没排到」
        #   —— 判据用**整周 slots 有没有品**：排不进本周的品也会带 reason 落在 slots 里，
        #   所以「整周都没品」才等于「池是空的」。
        slots, _t = plan_for_talent(douyin_id, talent_id)
        has_pool = any(s.items for s in (slots or []))
        return "", [], [], (PLAN_STATE_NO_TODAY_ITEM if has_pool else PLAN_STATE_NO_POOL)
    day_key = datetime.datetime.now().strftime("%Y-%m-%d")
    markable = [it for it in items if it.deal_id]
    todo = [it.deal_id for it in markable
            if not _already_reminded(it.deal_id, day_key)]
    if markable and not todo:
        # 今天该推的都推过了 ⇒ 不重复推（重启也守住）
        return "", [], items, PLAN_STATE_ALREADY_SENT
    name = _display_name(talent)
    # ⚠️ 数量必须**跟着实际坑位数走** —— 原来硬编码「今天推这两件」，
    #   排品池里只有 1 个品也照样说「两件」，达人一数就发现不对（2026-10-24 实测）。
    n = len(items)
    cnt = {1: "这件", 2: "这两件"}.get(n, f"这{n}件")
    lines = [f"【{name} 今天推{cnt}】"] if name else [f"【今天推{cnt}】"]
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
    return "\n".join(lines), todo, items, PLAN_STATE_OK


def _notice_log_exists(tenant_key: str, day_key: str, kind: str) -> bool:
    """今天是否已经发过这类通知（DB 级幂等，重启也守住）。

    ★ 走 `db` 模块（**bot 自己的库** `daren_bot.db`）——
      `bot_plan_notice_log` 是**推送去重表**，跟 `bot_coop_push_log` 同类
      ⇒ 推送类去重日志统一放 bot 库。
    ⚠️ 反面教材（2026-10-26 实测踩到）：这张表**建在 bot 库**，
      但早先的实现去读 `SOLOBRAVE_DB`（排品库）⇒ **两个库不是一个库**
      ⇒ 表查不到 ⇒ 幂等**静默失效**（每次都发占位）⇒ 本函数改由 db 模块代劳。
    """
    try:
        return db.notice_log_exists(tenant_key, day_key, kind)
    except Exception as e:
        # ⚠️ 读不到 ⇒ 幂等**失效**（会重复发）。必须**喊出来** ——
        #   静默降级成「每次都发」= 把故障写成正常。
        log.warning("[plan] 读 bot_plan_notice_log 失败（幂等可能失效）: %s", e)
        return False


def _mark_notice_sent(tenant_key: str, day_key: str, kind: str) -> None:
    """记下「今天已发过这类通知」。"""
    try:
        db.mark_notice_sent(tenant_key, day_key, kind)
    except Exception as e:
        # ⚠️ 记不进去 = 幂等失效（同上）⇒ 必须喊出来，别让它静默变成「每天重复发」
        log.warning("[plan] 写 bot_plan_notice_log 失败（幂等失效，可能重复发）: %s", e)


def build_empty_plan_notice(douyin_id: str, talent_id: str = "") -> tuple[dict | None, str]:
    """「今天没排品」占位消息 → (卡片, 文字)。状态不是空态时返回 (None, "")。

    ★ 为什么要有这条（老大 2026-10-26 拍板）：
      原来「今天没排品」⇒ 达人**彻底静默**（一条消息都不发），
      达人会以为系统挂了/没人管他 —— 而实际上系统只是**没有品可推**。
      ⇒ 空态也要有回音，让达人知道「今天确实没排，不是坏了」。

    ★ 但**只对「真的没品」回音**：
      - `no_talent`（达人数据没采到）= **我们的问题** ⇒ 不发（否则是把故障说成「没排品」）；
      - `no_pool` / `no_today_item` = 达人的现状 ⇒ 发。

    ⚠️ 文案不许说「你的品被清了」这类我们**不知道**的事（只丢不造）：
      说「你名下还没有登记待推的品」是可核验的；说「排不下是因为坑位满了」是**推断**，
      除非我们真能从 `plan_reason` 里读出来 —— 所以这里只说能核验的那部分。
    """
    _text, _todo, _items, state = _reminder_parts(douyin_id, talent_id)
    if state not in (PLAN_STATE_NO_POOL, PLAN_STATE_NO_TODAY_ITEM):
        return None, ""
    slots, talent = plan_for_talent(douyin_id, talent_id)
    name = _display_name(talent) if talent else ""
    if state == PLAN_STATE_NO_POOL:
        title = "📦 今天没排品"
        body = ("你名下还没有登记待推的品。\n"
                "接了品之后发我「接了 <商品ID或商品链接>」，"
                "我就能帮你排哪天推哪个。")
    else:
        title = "📦 今天没排品"
        body = ("今天没排到要推的品。\n"
                "你名下这批品这周的安排看「我的排品表」，"
                "有新单我马上帮你排进去。")
    from . import feishu_card as fc
    card = fc.plan_card(
        name, title, [body],
        actions=[fc._button("看我的排品表", {"action": "view_plan"}, "default")],
        note="每天 09:37 我会推「今天推哪个」给你",
        template=fc.TPL_GREY)          # ★ 灰色：别用绿色（绿色=「今天有好货」的误读）
    return card, f"{title}\n{body}"


def build_reminder_card(douyin_id: str, talent_id: str = ""):
    """今日提醒的**卡片版**（文字版太长，聊天里铺开很难看）。

    ★ 飞书这个应用**不支持任何折叠组件**（collapsible / post.fold 都被拒），
      所以「把内容收起来」只能靠**卡片本身短**：标题 + 坑位 + 按钮。
      详细理由不进卡片（要看就在 H5 排品表页看）。
    """
    from . import feishu_card as fc
    _text, todo, items, _state = _reminder_parts(douyin_id, talent_id)
    if not todo or not items:
        return None, []
    slots, talent = plan_for_talent(douyin_id, talent_id)
    name = _display_name(talent) if talent else ""
    n = len(items)
    cnt = {1: "这件", 2: "这两件"}.get(n, f"这{n}件")
    lines = []
    for it in items:
        meta = " · ".join(x for x in (
            (f"¥{it.price:g}" if it.price else ""),
            (f"佣金{it.commission_rate:g}%" if it.commission_rate else ""),
            (it.lifecycle.label if it.lifecycle else "")) if x)
        lines.append(f"**坑位 {it.plan_slot}** {it.product_name[:26]}\n"
                     f"{meta}")
    tail_left = _tail_pending(douyin_id, talent_id, items)
    note = f"每天 09:37 我会推「今天推哪个」给你"
    if tail_left:
        note = f"⏰ 还有 {len(tail_left)} 个品是尾货期，再不推就过期了"
    card = fc.plan_card(
        name, f"📦 今天推{cnt}", lines,
        actions=[fc._button("看我的排品表", {"action": "view_plan"}, "default")],
        note=note)
    return card, todo


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


def scan_and_push(ws=None, dry_run: bool = False) -> dict:
    """扫一遍灰度租户，把「今天推哪两个」推给有排期的达人。

    ★ 签名与 `coop_notify.scan_and_push` **保持一致**（收 `ws` 对象 + `dry_run`）——
      之前这里收的是「sender 工厂函数」，两边不一致 ⇒ 调用方直接传 ws 就炸
      （`'WsManager' object is not callable`，2026-10-24 踩过）。

    返回统计（和 coop_notify.scan_and_push 同样的形状，方便运维看）。
    """
    stat = {"scanned": 0, "pushed": 0, "notified": 0, "skipped": 0, "details": []}
    day_key = datetime.datetime.now().strftime("%Y-%m-%d")

    # ★★ 池级排期：**在推租户之前**先把**整个排品池**的排期重算并落库。
    #   推送给有租户的达人（飞书侧物理限制），
    #   但**排期是池子的属性** —— 之前只在租户循环里落库，
    #   没绑租户的达人（实测：七月姐姐 5 单）排期永远停在过去，
    #   过期未推的品也永远等不到 `requeue_overdue` 退回重排 ⇒ 品卡死在昨天。
    #   `dry_run` 是探针，**一个字都不写**。
    if not dry_run:
        try:
            pool = reschedule_pool(day_key)
            stat["pool_talents"] = pool["talents"]
            stat["pool_requeued"] = pool["requeued"]
            stat["persisted"] = pool["persisted"]
            if pool["requeued"]:
                log.info("[plan] 池内有 %d 个过期未推的品退回重排（涉及 %d 个达人）",
                         pool["requeued"], pool["talents"])
        except Exception:
            # 落库失败**不阻断推送**：先让达人今天收到提醒，
            # 排期明天再补（宁可少一次安排，也不能今天不推）。
            log.exception("[plan] 池级排期落库失败（不阻断推送）")

    gray = _gray_map()
    if not gray:
        log.info("[plan] 灰度名单内没有已绑定抖音号的租户 → 本轮不推")
        return stat
    for tk, dyid in gray.items():
        stat["scanned"] += 1
        try:
            chat_id = db.get_latest_chat_id(tk)
            if not chat_id:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "没有 chat_id"})
                continue
            # ★ 先落库、再推送（2026-10-24 实测根因修复）：
            #   算出来的排期不落库 ⇒ `deals.scheduled_at` 永远是 0
            #   ⇒ 「已有排期保持原位」这条规则从来没生效过
            #   ⇒ 每天从头现算 ⇒ 明天会再推一遍一模一样的两个品。
            #   顺序也很重要：**先落库后推送** —— 推送失败时排期已经在，
            #   下一轮扫描还会扫到这个品（remind_state 没标）⇒ 能重试；
            #   反过来先推后落库，中间崩了就白推一次。
            #   （落库已上移到**池级** `reschedule_pool()` —— 那里按排品池遍历，
            #    覆盖所有有单的达人，不只是有租户的。）
            text, todo, state = build_reminder_state(dyid)
            card, card_todo = build_reminder_card(dyid)

            # ── 状态分派（2026-10-26）────────────────────────────────────
            # 原来只有一句 `if not text: skip` —— 「达人查不到」和「今天没品」
            # 被折叠成同一个空字符串 ⇒ 一律静默 ⇒ 达人彻底收不到回音。
            # 现在按**显式状态**分派，处置完全相反的两类分开走。
            if state == PLAN_STATE_NO_TALENT:
                # ★ 达人数据没采到 = **我们的问题** ⇒ 不发「今天没排品」
                #   （那是把故障说成结论）。只记日志 + 运营侧可见。
                stat["skipped"] += 1
                stat["details"].append({
                    "tenant": tk, "why": "达人数据缺失（不发给达人：避免把故障说成「没排品」）"})
                log.warning("[plan] 租户 %s 达人数据缺失（dyid=%s），本轮不推", tk, dyid)
                continue

            if state in (PLAN_STATE_NO_POOL, PLAN_STATE_NO_TODAY_ITEM):
                # 「今天确实没排品」⇒ 发占位消息（老大 2026-10-26 拍板）
                #   幂等挂 `bot_plan_notice_log`（空态没有 deal_id，挂不上 remind_state）
                if _notice_log_exists(tk, day_key, PLAN_NOTICE_EMPTY):
                    stat["skipped"] += 1
                    stat["details"].append({
                        "tenant": tk, "why": "今天没排品（占位消息今天已发过，不重发）"})
                    continue
                empty_card, empty_text = build_empty_plan_notice(dyid)
                if not empty_text:
                    stat["skipped"] += 1
                    stat["details"].append({"tenant": tk, "why": "今天没排品（状态已变）"})
                    continue
                if dry_run:
                    stat["details"].append({"tenant": tk, "text": empty_text,
                                            "state": state, "dry_run": True})
                    continue
                sender = None
                if ws is not None:
                    try:
                        sender = ws.get_sender(tk)
                    except Exception:
                        sender = None
                if sender is None:
                    stat["skipped"] += 1
                    stat["details"].append({"tenant": tk, "why": "拿不到 sender"})
                    continue
                sent_as = "card"
                try:
                    sender.send_card(chat_id, empty_card)
                except Exception:
                    log.exception("[plan] 空态占位卡发送失败，回退文字版")
                    sender.send_text(chat_id, empty_text)
                    sent_as = "text"
                # ★ 先记幂等再计数：记成功才不发第二遍（发送成功却没记 ⇒ 会重发）
                _mark_notice_sent(tk, day_key, PLAN_NOTICE_EMPTY)
                stat["notified"] = stat.get("notified", 0) + 1
                stat["details"].append({"tenant": tk, "state": state, "sent_as": sent_as})
                continue

            if state == PLAN_STATE_ALREADY_SENT:
                # 今天该推的都推过了 ⇒ 不重复推（幂等）
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "今天该推的都推过了（幂等）"})
                continue

            if not text:
                # 兜底：状态说 ok 却没文案 ⇒ 记日志，别静默
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": f"状态={state} 但没文案（异常）"})
                log.warning("[plan] 租户 %s 状态=%s 但文案为空（异常）", tk, state)
                continue
            if dry_run:
                stat["details"].append({"tenant": tk, "deals": todo,
                                        "text": text, "dry_run": True})
                continue
            sender = None
            if ws is not None:
                try:
                    sender = ws.get_sender(tk)
                except Exception:
                    sender = None
            if sender is None:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "拿不到 sender"})
                continue
            # ★ 优先发**卡片**（标题+坑位+按钮，聊天里不铺长文）；
            #   发卡片失败就回退文字版（宁可丑一点，也不能不推）。
            sent_as = "card"
            if card:
                try:
                    sender.send_card(chat_id, card)
                except Exception:
                    log.exception("[plan] 卡片推送失败，回退文字版")
                    sender.send_text(chat_id, text)
                    sent_as = "text"
            else:
                sender.send_text(chat_id, text)
                sent_as = "text"
            stat["pushed"] += 1
            stat["sent_as"] = sent_as
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
        # ⚠️ 这里**不**做任何自动采集 ——
        #   排品池 = 我们自己系统里的合作单（达人说「接了 xxx」/ 运营建单）；
        #   达人端只用于「按商品ID搜商品」，不碰达人的其他数据（2026-10-24 老大纠正）。
        stat = scan_and_push(self.wm)
        # ⚠️ 空态占位**也要**置位 `_last_day`（2026-10-26）：
        #   调度器每 SCAN_INTERVAL(300s) 醒一次，`_last_day` 是「今天推过了」的闸门。
        #   只发占位没发品时若不置位 ⇒ 09:37 之后会**一整天**每 5 分钟重扫一次
        #   ⇒ 同一条占位消息发 300+ 次（DB 幂等能挡住重复发，但白扫 300 次）。
        if stat.get("pushed") or stat.get("notified"):
            self._last_day = today
