"""推荐→出单反馈闭环（2026-11-07 老大拍板，评估方四条设计点定稿）。

链路：达人问推荐 → 每个推荐品落一条 bot_reco_log → 达人点「接这个品」标 accepted=1
→ 每周采集后回填 data_status（达人维度结算数据新鲜度）→ 命中率视图。

三个设计铁律（改之前先读）：
  ① data_status 三态（ok/stale/missing）：命中率**只算 ok**；missing 单独汇总成
    「采集健康度」——把「采集失败」当「品不适合号」会产出貌似有数据支撑的错误结论。
  ② accepted 与命中率分开：推荐命中率（AI 准不准）vs 采纳命中率（有没有说服力），
    混在一起就诊断不出来。
  ③ 一期只算正向，且只丢不造：达人×商品出单明细目前没有现成数据源
    （talent_videos 无商品关联、material_list 只有达人数、history_videos 无商品），
    sold_gmv 预留可空、 sold_source 待接「达人端商品详情 top 带货达人」采集口，
    没接之前视图显示「待接入」，绝不拿机构级商品出单冒充达人出单。
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid

from . import db
from .executors import SOLOBRAVE_DB

log = logging.getLogger("daren-bot.reco_log")

DATA_STATUS_OK = "ok"
DATA_STATUS_STALE = "stale"
DATA_STATUS_MISSING = "missing"

# settle 数据新鲜度门槛：>7 天算 stale（与商品缓存 TTL 同口径）
STALE_SECONDS = 7 * 86400


def log_recommendations(tenant_key: str, douyin_id: str, talent_id: str,
                        recos: list, now: int | None = None) -> int:
    """一次推荐 → 每个推荐品落一条留档（同一品再次推荐会新增一行——
    判断随时间/画像变化，留痕才能看「判断变化」，不去重）。"""
    if not recos:
        return 0
    now = int(now or time.time())
    rows = []
    for r in recos:
        pid = str(r.get("product_id") or "").strip()
        if not pid:
            continue
        reasons = r.get("reasons") or []
        verdict = ""
        for note in reasons:
            if note:
                verdict = str(note)[:100]
                break
        rows.append((
            uuid.uuid4().hex, tenant_key, douyin_id, talent_id or "", pid,
            str(r.get("name") or "")[:80], now,
            int(r.get("stars") or 0), verdict, 0, 0, "", None, 0, "", now,
        ))
    if not rows:
        return 0
    with db.db_ctx() as conn:
        conn.executemany(
            "INSERT INTO bot_reco_log (id, tenant_key, douyin_id, talent_id,"
            " product_id, product_name, recommend_at, star_level, verdict,"
            " accepted, accepted_at, data_status, sold_gmv, sold_at,"
            " sold_source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows)
    log.info("reco log: tenant=%s dy=%s recos=%d", tenant_key, douyin_id,
             len(rows))
    return len(rows)


def mark_accepted(tenant_key: str, douyin_id: str, product_id: str,
                  now: int | None = None) -> int:
    """达人接了这个品（点按钮/说「接了 xxx」）⇒ 该 (达人,品) 所有未采纳行标 1。

    幂等：accepted=0 才更新，重复点不重复计时。
    """
    now = int(now or time.time())
    with db.db_ctx() as conn:
        cur = conn.execute(
            "UPDATE bot_reco_log SET accepted=1, accepted_at=? "
            "WHERE tenant_key=? AND douyin_id=? AND product_id=? AND accepted=0",
            (now, tenant_key, douyin_id, product_id))
        return cur.rowcount


def _talent_capture_time(douyin_id: str) -> int | None:
    """主库 platform_talent_stats 里该达人最近一次结算采集时间（秒）。
    无行 → None（= 从没采到过达人维度结算数据）。"""
    if not SOLOBRAVE_DB.is_file():
        return None
    conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True)
    try:
        r = conn.execute(
            "SELECT MAX(captured_at) FROM platform_talent_stats WHERE douyin_id=?",
            (douyin_id,)).fetchone()
        return int(r[0]) if r and r[0] else None
    finally:
        conn.close()


def classify_data_status(captured_at: int | None, now: int | None = None) -> str:
    """纯函数：采集时间 → 三态。测试直接钉这个口径。"""
    if captured_at is None:
        return DATA_STATUS_MISSING
    now = int(now or time.time())
    if now - int(captured_at) <= STALE_SECONDS:
        return DATA_STATUS_OK
    return DATA_STATUS_STALE


def backfill_data_status(now: int | None = None) -> dict:
    """周采集后回填所有行的 data_status（按 douyin_id 分组，幂等可重跑）。

    只动 data_status，绝不碰 sold_*（出单数据源未接入，只丢不造）。
    返回三态计数。
    """
    now = int(now or time.time())
    with db.db_ctx() as conn:
        dyids = [r[0] for r in conn.execute(
            "SELECT DISTINCT douyin_id FROM bot_reco_log")]
        counts = {DATA_STATUS_OK: 0, DATA_STATUS_STALE: 0,
                  DATA_STATUS_MISSING: 0}
        for dy in dyids:
            status = classify_data_status(_talent_capture_time(dy), now)
            cur = conn.execute(
                "UPDATE bot_reco_log SET data_status=? WHERE douyin_id=?",
                (status, dy))
            counts[status] += cur.rowcount
    log.info("reco data_status backfill: %s", counts)
    return counts


def stats(tenant_key: str | None = None) -> dict:
    """闭环总览（命中率口径：一期只有「采纳率」是真数，出单待接入如实标注）。

    返回 {total, accepted, accept_rate, data_status{...}, sold, tenants[...]}。
    """
    where = "WHERE tenant_key=?" if tenant_key else ""
    args = (tenant_key,) if tenant_key else ()
    with db.db_ctx() as conn:
        total, accepted = conn.execute(
            f"SELECT COUNT(*), COALESCE(SUM(accepted),0) FROM bot_reco_log {where}",
            args).fetchone()
        ds = {row[0] or "unset": row[1] for row in conn.execute(
            f"SELECT data_status, COUNT(*) FROM bot_reco_log WHERE accepted=1"
            + (" AND tenant_key=?" if tenant_key else "")
            + " GROUP BY data_status",
            args)}
        sold = conn.execute(
            f"SELECT COUNT(*) FROM bot_reco_log WHERE accepted=1"
            f" AND sold_gmv IS NOT NULL"
            + (" AND tenant_key=?" if tenant_key else ""),
            args).fetchone()[0]
        tenants = []
        if not tenant_key:
            for row in conn.execute(
                    "SELECT tenant_key, COUNT(*), COALESCE(SUM(accepted),0)"
                    " FROM bot_reco_log GROUP BY tenant_key"
                    " ORDER BY COUNT(*) DESC"):
                tenants.append({"tenant_key": row[0], "total": row[1],
                                "accepted": row[2]})
    return {
        "total": total,
        "accepted": accepted,
        "accept_rate": round(accepted / total, 4) if total else 0,
        "data_status": ds,          # 只统计 accepted=1 的行（进入出单观察的）
        "sold": sold,               # 一期恒 0：出单数据源未接入
        "sold_pending_source": True,
        "tenants": tenants,
    }


# ─────────── 出单回填（二期：达人端商品详情 top 带货达人数据源）───────────

# sold_source 取值：''=未回填；'daren_product_detail'=达人端商品详情配对命中。
#   ⚠️ 命中的行 sold_gmv 也可能为 NULL（平台榜单没给金额）——
#   sold_at/sold_source 非空 = 「确认这个达人带过这个品」，
#   sold_gmv 非空 = 「且知道金额」；stats() 的 sold 按金额口径（分列二期再做）。

def _resolve_platform_product_id(conn, product_id: str) -> str:
    """bot_reco_log.product_id（product_reco 取 products.id 优先、
    platform_product_id 兜底）→ 平台商品ID。两步都查不到 ⇒ ''（不配对比猜）。"""
    r = conn.execute(
        "SELECT platform_product_id FROM products WHERE id=?",
        (product_id,)).fetchone()
    if r and r[0]:
        return str(r[0])
    r = conn.execute(
        "SELECT platform_product_id FROM products WHERE platform_product_id=?",
        (product_id,)).fetchone()
    return str(r[0]) if r and r[0] else ""


def backfill_sold(now: int | None = None) -> dict:
    """「推荐→出单」配对回填：bot_reco_log × platform_product_talent。

    配对键（不靠格式猜，AGENTS.md §8.1 三类混装铁律）：
      商品侧  bot_reco_log.product_id → products.platform_product_id
      达人侧  ① platform_product_talent.author_id == talents.platform_talent_id
              ② platform_product_talent.douyin_id == bot_reco_log.douyin_id
              （nickname 不做配对键——宁可漏不可错挂）
    「不在 top N」≠「没出单」：配对不上保持 NULL（那是采不到，不是卖了 0）。
    只更新 sold_gmv IS NULL 的行（已有值不覆盖）；返回 {matched, gmv_filled}。
    """
    now = int(now or time.time())
    if not SOLOBRAVE_DB.is_file():
        return {"matched": 0, "gmv_filled": 0}
    main = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True)
    matched = gmv_filled = 0
    try:
        with db.db_ctx() as bconn:
            pairs = [(r[0], r[1]) for r in bconn.execute(
                "SELECT DISTINCT product_id, douyin_id FROM bot_reco_log"
                # sold_source='' ⇒ 从没配对过；gmv NULL 但已配对的行（平台没给
                # 金额）不重跑——那是终态不是「还没回填」
                " WHERE sold_gmv IS NULL AND sold_source=''")]
        for product_id, dyid in pairs:
            ppid = _resolve_platform_product_id(main, product_id)
            if not ppid:
                continue
            # 我方达人的平台 id 集合（author_id 配对用）
            tids = {str(r[0]) for r in main.execute(
                "SELECT platform_talent_id FROM talents"
                " WHERE douyin_id=? AND platform_talent_id!=''", (dyid,))}
            clauses, args = ["douyin_id=?"], [ppid, ppid, dyid]
            if tids:
                clauses.insert(0, "author_id IN (%s)"
                               % ",".join("?" * len(tids)))
                args[2:2] = sorted(tids)
            row = main.execute(
                "SELECT author_id, douyin_id, gmv, captured_at"
                " FROM platform_product_talent"
                " WHERE platform_product_id=?"
                " AND captured_at=(SELECT MAX(captured_at) FROM"
                "  platform_product_talent WHERE platform_product_id=?)"
                " AND (" + " OR ".join(clauses) + ")",
                args).fetchone()
            if not row:
                continue
            gmv, cap_at = row[2], int(row[3] or now)
            with db.db_ctx() as bconn:
                cur = bconn.execute(
                    "UPDATE bot_reco_log SET sold_gmv=?, sold_at=?,"
                    " sold_source='daren_product_detail'"
                    " WHERE product_id=? AND douyin_id=?"
                    " AND sold_gmv IS NULL AND sold_source=''",
                    (gmv, cap_at, product_id, dyid))
            matched += cur.rowcount
            if gmv is not None:
                gmv_filled += cur.rowcount
        log.info("reco sold backfill: matched=%d gmv_filled=%d",
                 matched, gmv_filled)
        return {"matched": matched, "gmv_filled": gmv_filled}
    finally:
        main.close()


# ─────────── 对账视图（2026-11-07 老大拍板四个设计点）───────────

import re as _re

_CN_UNIT = {"百": 100, "千": 1000, "万": 10000}


def amount_floor(fmt: str) -> int | None:
    """平台区间文案 → 下限金额（纯函数，只丢不造）。

    「1千+」→1000、「2.5千+」→2500、「40+」→40、「2万+」→20000、
    「0-10」→0、「500+」→500。解析不出 ⇒ None（绝不用 0 冒充）。
    """
    if not fmt:
        return None
    m = _re.match(r"\s*([\d.]+)\s*(百|千|万)?", str(fmt))
    if not m:
        return None
    try:
        base = float(m.group(1))
    except (TypeError, ValueError):
        return None
    unit = _CN_UNIT.get(m.group(2) or "", 1)
    return int(base * unit)


def gmv_band(floor: int | None) -> str:
    """下限金额 → 档位（老大口径：一期只算正向，按 GMV 分三档）。"""
    if floor is None:
        return "unknown"
    if floor < 1000:
        return "<1k"
    if floor < 10000:
        return "1k-1w"
    return ">1w"


def _fmt_of_extra(extra_json: str) -> str:
    try:
        d = json.loads(extra_json or "{}")
    except (TypeError, ValueError):
        return ""
    return str(d.get("format_sales_amount") or "")


def hit_stats(tenant_key: str | None = None, now: int | None = None) -> dict:
    """对账视图（四个设计点口径）：

      recommend  推荐命中率（AI 准不准）：data_status='ok' 的行里
                 sold_source 命中的比例 —— 命中率只算 ok，missing 进健康度
      accept     采纳命中率（说服力）：accepted=1 且 ok 的行里命中比例
      health     采集健康度：全部行三态分布（missing 单独看，不混进命中率）
      gmv_bands  命中行的 GMV 档位分布（<1k / 1k-1w / >1w / unknown），
                 金额从主库命中行 extra_json.format_sales_amount 区间文案解析
      hits       命中明细（达人昵称/品/采纳与否/档位/采集日），供视图列表

    一期只算正向（推荐了→出单）；负样本（建议别带→没带）二期。
    """
    where = "WHERE tenant_key=?" if tenant_key else ""
    args = (tenant_key,) if tenant_key else ()
    out = {
        "recommend": {"total": 0, "hit": 0, "rate": 0.0},
        "accept": {"total": 0, "hit": 0, "rate": 0.0},
        "health": {DATA_STATUS_OK: 0, DATA_STATUS_STALE: 0,
                   DATA_STATUS_MISSING: 0, "unset": 0},
        "gmv_bands": {"<1k": 0, "1k-1w": 0, ">1w": 0, "unknown": 0},
        "hits": [],
        "generated_at": int(now or time.time()),
    }
    with db.db_ctx() as conn:
        # 健康度：全部行（不管采纳与否）
        for st, n in conn.execute(
                f"SELECT COALESCE(NULLIF(data_status,''),'unset'), COUNT(*)"
                f" FROM bot_reco_log {where} GROUP BY 1", args):
            out["health"][st] = n
        # 推荐命中率：ok 的行
        total_ok, hit_ok = conn.execute(
            f"SELECT COUNT(*),"
            f" COALESCE(SUM(sold_source!=''),0) FROM bot_reco_log"
            f" {where + ' AND' if where else 'WHERE'} data_status=?",
            args + (DATA_STATUS_OK,)).fetchone()
        out["recommend"]["total"], out["recommend"]["hit"] = total_ok, hit_ok
        out["recommend"]["rate"] = round(hit_ok / total_ok, 4) if total_ok else 0
        # 采纳命中率：accepted 且 ok
        at, ah = conn.execute(
            f"SELECT COUNT(*),"
            f" COALESCE(SUM(sold_source!=''),0) FROM bot_reco_log"
            f" {where + ' AND' if where else 'WHERE'} accepted=1"
            f" AND data_status=?", args + (DATA_STATUS_OK,)).fetchone()
        out["accept"]["total"], out["accept"]["hit"] = at, ah
        out["accept"]["rate"] = round(ah / at, 4) if at else 0
        # 命中明细 + GMV 档位（回主库取区间文案）
        # 口径与 recommend 一致：只列 data_status='ok' 的命中——
        # missing 行的 sold_source 混进明细会跟命中率分母对不上账。
        hit_rows = conn.execute(
            f"SELECT douyin_id, product_id, product_name, star_level,"
            f" accepted, sold_at FROM bot_reco_log"
            f" {where + ' AND' if where else 'WHERE'} sold_source!=''"
            f" AND data_status=?",
            args + (DATA_STATUS_OK,)).fetchall()

    if hit_rows and SOLOBRAVE_DB.is_file():
        # 不用 mode=ro：活 WAL 库在 -shm 不在场时 ro 句柄打不开（AGENTS.md §16 Y.7）
        main = sqlite3.connect(str(SOLOBRAVE_DB))
        try:
            for dyid, pid, pname, stars, acc, sold_at in hit_rows:
                ppid = _resolve_platform_product_id(main, str(pid))
                fmt = ""
                if ppid:
                    r = main.execute(
                        "SELECT extra_json FROM platform_product_talent"
                        " WHERE platform_product_id=?"
                        " AND captured_at=(SELECT MAX(captured_at) FROM"
                        "  platform_product_talent WHERE platform_product_id=?)"
                        " AND author_id IN (SELECT platform_talent_id FROM"
                        "  talents WHERE douyin_id=? AND platform_talent_id!='')",
                        (ppid, ppid, dyid)).fetchone()
                    if r:
                        fmt = _fmt_of_extra(r[0])
                band = gmv_band(amount_floor(fmt))
                out["gmv_bands"][band] += 1
                out["hits"].append({
                    "douyin_id": dyid, "product_name": pname,
                    "star_level": stars, "accepted": bool(acc),
                    "gmv_text": fmt or "未知", "gmv_band": band,
                    "sold_at": int(sold_at or 0),
                })
        finally:
            main.close()
    return out
