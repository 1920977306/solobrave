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
