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
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    future = [s for s in slots if s.date >= today]
    if not future or not any(s.items for s in future):
        return ("你名下这批品都排完/推过了。\n"
                "在达人端选品车加新品（或发「接了 <商品ID>」），"
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


def requeue_overdue(talent_id: str, day_key: str = "") -> list[str]:
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
        if ids:
            conn.execute(
                "UPDATE deals SET scheduled_at=0, plan_slot=0 WHERE id IN (%s)"
                % ",".join("?" * len(ids)), ids)
            conn.commit()
        return ids
    finally:
        conn.close()


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


# ────────────────────────── 排品池自动同步 ──────────────────────────

# 选品车同步缓存：{"at": 时间戳, "ok": bool, "count": int}
_cart_sync: dict = {}


def sync_cart_pool(force: bool = False, max_age: int = 6 * 3600) -> dict:
    """把达人选品车同步进排品池（`deals`）。

    ★ 这才是「达人接了哪些品」的真源 —— 达人在选品广场点「加选品车」就是接了。
      靠「达人发魔法口令」登记在现实里行不通（实测 200+ 条消息触发数 = 0）。

    同步有成本（要走达人端浏览器，3 个接口 ≈ 30~60s）⇒ 带缓存：
      · 每天首次扫描时同步一次
      · 达人主动问「今天推啥」时，超过 `max_age` 才补同步
    """
    now = int(time.time())
    if not force and _cart_sync.get("at") and \
            (now - int(_cart_sync["at"])) < max_age:
        return dict(_cart_sync)
    try:
        sys.path.insert(0, str(_ROOT))
        from daren_cockpit.tools import cart_collector
        res = cart_collector.sync(apply=True)
    except Exception as e:
        log.exception("[plan] 选品车同步失败")
        res = {"ok": False, "error": str(e)[:160]}
    _cart_sync.clear()
    _cart_sync.update({
        "at": now,
        "ok": bool(res.get("ok")),
        "count": int(res.get("deal_count") or 0),
        "talent": (res.get("talent") or {}).get("name", ""),
        "error": res.get("error", ""),
    })
    log.info("[plan] 选品车同步: %s", {k: v for k, v in _cart_sync.items()
                                       if k != "at"})
    return dict(_cart_sync)


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
    """
    return (str(talent.get("platform_nickname") or "").strip()
            or str(talent.get("name") or "").strip())


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
    name = _display_name(talent)
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


def scan_and_push(ws=None, dry_run: bool = False, sync_first: bool = True) -> dict:
    """扫一遍灰度租户，把「今天推哪两个」推给有排期的达人。

    ★ 签名与 `coop_notify.scan_and_push` **保持一致**（收 `ws` 对象 + `dry_run`）——
      之前这里收的是「sender 工厂函数」，两边不一致 ⇒ 调用方直接传 ws 就炸
      （`'WsManager' object is not callable`，2026-10-24 踩过）。

    返回统计（和 coop_notify.scan_and_push 同样的形状，方便运维看）。
    ★ `sync_first`：扫描前先把**选品车**同步进排品池 ——
      否则达人新加的品不会进表（排品池只增不减要靠这个）。
    """
    stat = {"scanned": 0, "pushed": 0, "skipped": 0, "details": []}
    if sync_first:
        st = sync_cart_pool()
        stat["cart_sync"] = {k: v for k, v in st.items() if k != "at"}
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
            # ★ 先落库、再推送（2026-10-24 实测根因修复）：
            #   算出来的排期不落库 ⇒ `deals.scheduled_at` 永远是 0
            #   ⇒ 「已有排期保持原位」这条规则从来没生效过
            #   ⇒ 每天从头现算 ⇒ 明天会再推一遍一模一样的两个品。
            #   顺序也很重要：**先落库后推送** —— 推送失败时排期已经在，
            #   下一轮扫描还会扫到这个品（remind_state 没标）⇒ 能重试；
            #   反过来先推后落库，中间崩了就白推一次。
            #   `dry_run` 是探针，**一个字都不写**。
            if not dry_run:
                try:
                    talent_id = ""
                    trow = _read_talent(dyid)
                    talent_id = (trow or {}).get("id", "")
                    requeued = requeue_overdue(talent_id, day_key) if talent_id else []
                    if requeued:
                        log.info("[plan] %s 有 %d 个品排期已过且没推过，退回重排",
                                 tk, len(requeued))
                    slots, _t = plan_for_talent(dyid, talent_id)
                    if slots:
                        n = persist_plan(slots, talent_id=talent_id)
                        stat["persisted"] = stat.get("persisted", 0) + n
                except Exception:
                    # 落库失败**不阻断推送**：先让达人今天收到提醒，
                    # 排期明天再补（宁可少一次安排，也不能今天不推）。
                    log.exception("[plan] 排期落库失败（不阻断推送）")
            text, todo = build_reminder(dyid)
            if not text:
                stat["skipped"] += 1
                stat["details"].append({"tenant": tk, "why": "今天没有要推的品（没排期/已推过）"})
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
        # ★ 当天第一次推之前，强制把选品车同步一遍
        #   （达人昨天/今天新加的品要进今天的排品表）
        try:
            sync_cart_pool(force=True)
        except Exception:
            log.exception("[plan] 扫描前同步选品车失败（不阻断推送）")
        stat = scan_and_push(self.wm, sync_first=False)
        if stat.get("pushed"):
            self._last_day = today
