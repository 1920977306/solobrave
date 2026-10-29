"""llm_usage — LLM 调用消耗统计（旁路埋点，绝不影响主链路）。

优化菜单③「MiniMax 消耗统计进 admin」：
- 埋点位置：`daren_bot/llm_channels.py` 四个底层调用函数（包装层）。
- site 传递：ContextVar（任务执行器跑在独立线程，线程内 set/reset 安全），
  任务入口用 ``with llm_usage.span('qa'):`` 包住环循环。
- 存储：独立 sqlite（默认 ``data/daren-bot/llm_usage.db``，可用 env ``LLM_USAGE_DB``
  覆盖），**不共用 daren_bot.db**，避开写锁争抢；WAL 模式。
- ★ 旁路铁律（同 ops_alert 纪律）：``record_call`` 全程 try/except 吞掉异常，
  统计挂了绝不能让达人问答/采集主链路失败。
- token 数拿不到（底层调用只返回文本），用 prompt_chars / 调用次数当消耗代理。

表结构 calls：
    ts          INTEGER  毫秒时间戳
    site        TEXT     任务类型（qa / product_verdict / vision_product_extract /
                          vision_dashboard_qa / worker_extract / worker_eval /
                          worker_report / worker / probe / unknown）
    mode        TEXT     'prompt' | 'messages'
    vendor      TEXT     minimax / kimi / ...
    pool        TEXT     cp-subscription / code-subscription / minimax-open-balance / ...
    model       TEXT
    ok          INTEGER  1/0
    latency_ms  INTEGER
    err         TEXT     失败原因（≤200 字）
    prompt_chars INTEGER 提示长度（消耗代理）
    max_tokens  INTEGER   本次请求的 max_tokens 上限

注意：绝不在此模块落 ring 的 key/base 等敏感字段。
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("daren-bot.llm_usage")

# ---------- site 常量（任务类型标签） ----------
SITE_QA = "qa"                                # 达人问答环（executors._qa_llm）
SITE_PRODUCT_VERDICT = "product_verdict"      # 商品选品结论（_llm_product_verdict）
SITE_VISION_PRODUCT = "vision_product_extract"  # 商品截图识别（_vision_extract_product）
SITE_VISION_DASHBOARD = "vision_dashboard_qa"   # 通用截图问答（_vision_dashboard_qa）
SITE_WORKER = "worker"                      # worker 未细分入口（llm_chat_fb 默认）
SITE_WORKER_EXTRACT = "worker_extract"      # 视觉提取批次（buyin_worker）
SITE_WORKER_EVAL = "worker_eval"            # 达人评估
SITE_WORKER_REPORT = "worker_report"        # 拆解报告
SITE_WORKER_NOTES = "worker_notes"          # 达人档案 notes 摘要
SITE_WORKER_PRODUCT_URL = "worker_product_url"  # 商品页正文结构化提取
SITE_PROBE = "probe"                        # 通道探针
SITE_UNKNOWN = "unknown"

_current_site: contextvars.ContextVar[str] = contextvars.ContextVar(
    "llm_usage_site", default=SITE_UNKNOWN
)


@contextlib.contextmanager
def span(site: str):
    """给一段调用区间打上任务类型标签（线程内有效，嵌套用内层）。"""
    token = _current_site.set(site)
    try:
        yield
    finally:
        _current_site.reset(token)


def current_site() -> str:
    return _current_site.get()


# ---------- 存储 ----------

_DDL = """
CREATE TABLE IF NOT EXISTS calls (
    ts INTEGER NOT NULL,
    site TEXT NOT NULL,
    mode TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT '',
    pool TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    ok INTEGER NOT NULL,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    err TEXT NOT NULL DEFAULT '',
    prompt_chars INTEGER NOT NULL DEFAULT 0,
    max_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_calls_ts ON calls (ts);
CREATE INDEX IF NOT EXISTS idx_calls_site ON calls (site, ts);
"""


def _db_path() -> Path:
    override = os.environ.get("LLM_USAGE_DB")
    if override:
        return Path(override)
    from . import config

    return Path(config.DATA_DIR) / "llm_usage.db"


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """建表（幂等）。首次 record/query 前调用。"""
    conn = _connect()
    try:
        conn.executescript(_DDL)
        conn.commit()
    finally:
        conn.close()


# ---------- 记录（旁路铁律：任何异常都吞掉） ----------

def record_call(
    *,
    site: str,
    mode: str,
    ring: dict | None = None,
    ok: bool,
    latency_ms: int,
    err: str = "",
    prompt_chars: int = 0,
    max_tokens: int = 0,
) -> None:
    """记录一次 LLM 调用。★ 全程 try/except，统计挂了不影响主链路。"""
    try:
        ring = ring or {}
        row = (
            int(time.time() * 1000),
            str(site)[:64],
            str(mode)[:16],
            str(ring.get("vendor", ""))[:32],
            str(ring.get("pool", ""))[:64],
            str(ring.get("model", ""))[:64],
            1 if ok else 0,
            int(latency_ms),
            str(err)[:200],
            int(prompt_chars),
            int(max_tokens),
        )
        conn = _connect()
        try:
            conn.executescript(_DDL)  # 幂等，省去 init 顺序问题
            conn.execute(
                "INSERT INTO calls (ts, site, mode, vendor, pool, model, ok,"
                " latency_ms, err, prompt_chars, max_tokens) VALUES"
                " (?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 —— 旁路纪律
        try:
            log.warning("llm_usage.record_call 旁路失败（不影响主链路）: %s", exc)
        except Exception:  # noqa: BLE001
            pass


# ---------- 查询（admin API 用） ----------

def query(days: int = 7) -> dict:
    """按天聚合消耗。返回 totals / by_day_site / by_model / recent_failures。"""
    days = max(1, min(30, int(days)))
    since_ms = int((time.time() - days * 86400) * 1000)
    conn = _connect()
    try:
        conn.executescript(_DDL)
        cur = conn.cursor()

        cur.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0), COALESCE(AVG(latency_ms),0),"
            " COALESCE(SUM(prompt_chars),0)"
            " FROM calls WHERE ts >= ?",
            (since_ms,),
        )
        total, ok_sum, avg_lat, sum_chars = cur.fetchone()
        totals = {
            "days": days,
            "calls": int(total or 0),
            "ok": int(ok_sum or 0),
            "fail": int((total or 0) - (ok_sum or 0)),
            "avg_latency_ms": round(float(avg_lat or 0), 1),
            "prompt_chars": int(sum_chars or 0),
        }

        cur.execute(
            "SELECT date(ts/1000, 'unixepoch', 'localtime') AS day, site,"
            " COUNT(*), SUM(ok), AVG(latency_ms), SUM(prompt_chars)"
            " FROM calls WHERE ts >= ?"
            " GROUP BY day, site ORDER BY day DESC, COUNT(*) DESC",
            (since_ms,),
        )
        by_day_site = [
            {
                "day": r[0], "site": r[1], "calls": int(r[2]),
                "ok": int(r[3] or 0), "fail": int(r[2] - (r[3] or 0)),
                "avg_latency_ms": round(float(r[4] or 0), 1),
                "prompt_chars": int(r[5] or 0),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT vendor, pool, model, COUNT(*), SUM(ok), AVG(latency_ms)"
            " FROM calls WHERE ts >= ?"
            " GROUP BY vendor, pool, model ORDER BY COUNT(*) DESC",
            (since_ms,),
        )
        by_model = [
            {
                "vendor": r[0], "pool": r[1], "model": r[2],
                "calls": int(r[3]), "ok": int(r[4] or 0),
                "fail": int(r[3] - (r[4] or 0)),
                "avg_latency_ms": round(float(r[5] or 0), 1),
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            "SELECT ts, site, model, err, latency_ms FROM calls"
            " WHERE ok = 0 AND ts >= ? ORDER BY ts DESC LIMIT 50",
            (since_ms,),
        )
        recent_failures = [
            {
                "ts": int(r[0]), "site": r[1], "model": r[2], "err": r[3],
                "latency_ms": int(r[4] or 0),
            }
            for r in cur.fetchall()
        ]

        return {
            "totals": totals,
            "by_day_site": by_day_site,
            "by_model": by_model,
            "recent_failures": recent_failures,
        }
    finally:
        conn.close()
