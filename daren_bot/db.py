"""SQLite 独立库 daren_bot.db —— DDL 与 DAREN-BOT-SPEC.md v1.1 第 3 章逐字对齐。

服务启动自动建表。连接对象线程安全（模块级锁 + WAL）。
"""
import datetime
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from . import config

_lock = threading.RLock()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_ctx():
    with _lock:
        conn = _connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()


DDL = """
-- 3.1 租户表：一租户 = 一达人 = 一抖音号（MVP 收紧，换号找运营）
-- v1.1 改动: 主键改 app_id（长连接模式无回调路径，encrypt_key 不再使用，已删除）
CREATE TABLE IF NOT EXISTS bot_tenants (
  app_id          TEXT PRIMARY KEY,             -- 飞书自建应用 App ID, 全局唯一
  tenant_key      TEXT NOT NULL UNIQUE,         -- 自建命名: daren_xxx，业务标识
  app_secret_enc  TEXT NOT NULL,                -- AES 加密，系统主密钥
  bind_douyin_id  TEXT NOT NULL DEFAULT '',     -- MVP 一租户一号
  bitable_app_token TEXT DEFAULT '',            -- 1 个 Bitable App
  bitable_table_ids TEXT DEFAULT '',            -- JSON: {"profile":"tbl_x","products":"tbl_y","advice":"tbl_z"}
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- 3.2 任务队列（异步执行，幂等: 同租户同目标 pending/collecting 不重复入队）
-- chat_id: 完成/失败卡片推送目标（worker 回消息用，v1.1 实现时补入 DDL）
CREATE TABLE IF NOT EXISTS bot_collect_tasks (
  id           TEXT PRIMARY KEY,
  tenant_key   TEXT NOT NULL,
  type         TEXT NOT NULL,                   -- douyin | product | image
  target       TEXT NOT NULL,                   -- 抖音号 或 归一化商品 URL 或图片落盘路径
  chat_id      TEXT NOT NULL DEFAULT '',        -- 卡片推送目标会话
  status       TEXT NOT NULL DEFAULT 'pending', -- pending/collecting/done/failed
  cache_hit    INTEGER NOT NULL DEFAULT 0,
  credits_cost INTEGER NOT NULL DEFAULT 0,
  error        TEXT DEFAULT '',
  created_at   INTEGER NOT NULL,
  done_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON bot_collect_tasks(tenant_key, status);

-- 3.3 商品共享缓存（全平台达人共用，TTL 7-14 天）
CREATE TABLE IF NOT EXISTS bot_product_cache (
  product_key TEXT PRIMARY KEY,                 -- URL 归一化(去追踪参数/短链解析后)
  name        TEXT DEFAULT '',
  price       TEXT DEFAULT '',
  commission  TEXT DEFAULT '',
  stats_json  TEXT DEFAULT '{}',
  source_url  TEXT DEFAULT '',
  fetched_at  INTEGER NOT NULL
);

-- 3.4 周抓计数：按 ISO 周数存，查时自然分周，无定时重置任务
CREATE TABLE IF NOT EXISTS bot_week_counters (
  tenant_key TEXT NOT NULL,
  douyin_id  TEXT NOT NULL,
  week_no    INTEGER NOT NULL,                  -- ISO 8601 周数, e.g. 202641
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_key, douyin_id, week_no)
);

-- 3.5 积分账户
CREATE TABLE IF NOT EXISTS bot_credits_accounts (
  tenant_key TEXT PRIMARY KEY,
  balance    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

-- 3.6 积分流水：预留 recharge_order_id，转付费不动表结构
CREATE TABLE IF NOT EXISTS bot_credits_ledger (
  id                TEXT PRIMARY KEY,
  tenant_key        TEXT NOT NULL,
  action            TEXT NOT NULL,              -- collect_douyin/collect_product/collect_product_cached/vision/qa/grant/refund
  cost              INTEGER NOT NULL,           -- 正扣负返
  balance_after     INTEGER NOT NULL,
  cache_hit         INTEGER NOT NULL DEFAULT 0,
  ref_task_id       TEXT DEFAULT '',
  recharge_order_id TEXT DEFAULT '',            -- 预留
  created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_tenant ON bot_credits_ledger(tenant_key, created_at);
"""


def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with db_ctx() as conn:
        conn.executescript(DDL)


def now_ts() -> int:
    return int(datetime.datetime.now().timestamp())


def current_week_no() -> int:
    """ISO 8601 周数编码：year*100 + week，如 202641（spec 3.4，无定时重置任务）。"""
    iso = datetime.date.today().isocalendar()
    return iso.year * 100 + iso.week


# ---------- 租户 ----------

def add_tenant(app_id: str, tenant_key: str, app_secret_enc: str,
               bind_douyin_id: str = "") -> None:
    ts = now_ts()
    with db_ctx() as conn:
        conn.execute(
            """INSERT INTO bot_tenants
               (app_id, tenant_key, app_secret_enc, bind_douyin_id, status, created_at, updated_at)
               VALUES (?,?,?,?,'active',?,?)
               ON CONFLICT(app_id) DO UPDATE SET
                 tenant_key=excluded.tenant_key,
                 app_secret_enc=excluded.app_secret_enc,
                 bind_douyin_id=excluded.bind_douyin_id,
                 status='active', updated_at=excluded.updated_at""",
            (app_id, tenant_key, app_secret_enc, bind_douyin_id, ts, ts))
        conn.execute(
            """INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at)
               VALUES (?,?,?)
               ON CONFLICT(tenant_key) DO NOTHING""",
            (tenant_key, config.INITIAL_CREDITS, ts))


def get_active_tenants() -> list:
    with db_ctx() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM bot_tenants WHERE status='active' ORDER BY created_at")]


def get_tenant(tenant_key: str):
    with db_ctx() as conn:
        r = conn.execute("SELECT * FROM bot_tenants WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        return dict(r) if r else None


# ---------- 积分 ----------

def _ledger(conn, tenant_key, action, cost, balance_after,
            cache_hit=0, ref_task_id=''):
    conn.execute(
        """INSERT INTO bot_credits_ledger
           (id, tenant_key, action, cost, balance_after, cache_hit, ref_task_id, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (uuid.uuid4().hex, tenant_key, action, cost, balance_after,
         cache_hit, ref_task_id, now_ts()))


def get_balance(tenant_key: str) -> int:
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        return int(r["balance"]) if r else 0


def deduct(tenant_key: str, cost: int, action: str,
           ref_task_id: str = '', cache_hit: int = 0) -> tuple:
    """预扣。余额不足返回 (False, 当前余额)，成功返回 (True, 扣后余额)。事务内完成。"""
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        balance = int(r["balance"]) if r else 0
        if balance < cost:
            return False, balance
        new_balance = balance - cost
        conn.execute(
            "UPDATE bot_credits_accounts SET balance=?, updated_at=? WHERE tenant_key=?",
            (new_balance, now_ts(), tenant_key))
        _ledger(conn, tenant_key, action, cost, new_balance, cache_hit, ref_task_id)
        return True, new_balance


def refund(tenant_key: str, cost: int, action: str = 'refund',
           ref_task_id: str = '') -> int:
    """返还（任务失败/废图路径）。返回返还后余额。"""
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        balance = int(r["balance"]) if r else 0
        new_balance = balance + cost
        conn.execute(
            "INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(tenant_key) DO UPDATE SET balance=excluded.balance, "
            "updated_at=excluded.updated_at",
            (tenant_key, new_balance, now_ts()))
        _ledger(conn, tenant_key, action, -cost, new_balance, 0, ref_task_id)
        return new_balance


# ---------- 周抓计数 ----------

def get_week_count(tenant_key: str, douyin_id: str, week_no: int = None) -> int:
    week_no = week_no or current_week_no()
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT count FROM bot_week_counters WHERE tenant_key=? AND douyin_id=? AND week_no=?",
            (tenant_key, douyin_id, week_no)).fetchone()
        return int(r["count"]) if r else 0


def incr_week_count(tenant_key: str, douyin_id: str, week_no: int = None) -> int:
    week_no = week_no or current_week_no()
    with db_ctx() as conn:
        conn.execute(
            """INSERT INTO bot_week_counters (tenant_key, douyin_id, week_no, count)
               VALUES (?,?,?,1)
               ON CONFLICT(tenant_key, douyin_id, week_no)
               DO UPDATE SET count = count + 1""",
            (tenant_key, douyin_id, week_no))
        r = conn.execute(
            "SELECT count FROM bot_week_counters WHERE tenant_key=? AND douyin_id=? AND week_no=?",
            (tenant_key, douyin_id, week_no)).fetchone()
        return int(r["count"])


# ---------- 任务队列 ----------

def enqueue_task(tenant_key: str, task_type: str, target: str,
                 credits_cost: int, cache_hit: int = 0,
                 chat_id: str = '') -> str:
    """入队，幂等：同租户同目标存在 pending/collecting 时返回原任务 id。"""
    with db_ctx() as conn:
        r = conn.execute(
            """SELECT id FROM bot_collect_tasks
               WHERE tenant_key=? AND type=? AND target=? AND status IN ('pending','collecting')
               ORDER BY created_at LIMIT 1""",
            (tenant_key, task_type, target)).fetchone()
        if r:
            return r["id"]
        task_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO bot_collect_tasks
               (id, tenant_key, type, target, chat_id, status, cache_hit, credits_cost, created_at)
               VALUES (?,?,?,?,?,'pending',?,?,?)""",
            (task_id, tenant_key, task_type, target, chat_id,
             cache_hit, credits_cost, now_ts()))
        return task_id


def claim_next_task() -> dict:
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_collect_tasks WHERE status='pending' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not r:
            return {}
        cur = conn.execute(
            "UPDATE bot_collect_tasks SET status='collecting' WHERE id=? AND status='pending'",
            (r["id"],))
        if cur.rowcount != 1:
            return {}  # 被别的 worker 抢走
        d = dict(r)
        d["status"] = "collecting"
        return d


def mark_task(task_id: str, status: str, error: str = ''):
    with db_ctx() as conn:
        conn.execute(
            "UPDATE bot_collect_tasks SET status=?, error=?, done_at=? WHERE id=?",
            (status, error[:500], now_ts() if status in ('done', 'failed') else None, task_id))


def get_task(task_id: str):
    with db_ctx() as conn:
        r = conn.execute("SELECT * FROM bot_collect_tasks WHERE id=?",
                         (task_id,)).fetchone()
        return dict(r) if r else None


# ---------- 抖音号绑定（MVP 一租户一号） ----------

def bind_douyin_id(tenant_key: str, douyin_id: str) -> None:
    with db_ctx() as conn:
        conn.execute(
            "UPDATE bot_tenants SET bind_douyin_id=?, updated_at=? WHERE tenant_key=?",
            (douyin_id, now_ts(), tenant_key))


# ---------- 商品共享缓存（TTL 7-14 天，spec 3.3） ----------

PRODUCT_CACHE_TTL = 7 * 86400  # 秒；spec 允许 7-14 天，内测取 7


def cache_get(product_key: str):
    with db_ctx() as conn:
        r = conn.execute("SELECT * FROM bot_product_cache WHERE product_key=?",
                         (product_key,)).fetchone()
        if not r:
            return None
        d = dict(r)
        if now_ts() - int(d["fetched_at"]) > PRODUCT_CACHE_TTL:
            return None  # 过期视同未命中
        return d


def cache_put(product_key: str, name: str = '', price: str = '',
              commission: str = '', stats: dict = None,
              source_url: str = '') -> None:
    with db_ctx() as conn:
        conn.execute(
            """INSERT INTO bot_product_cache
               (product_key, name, price, commission, stats_json, source_url, fetched_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(product_key) DO UPDATE SET
                 name=excluded.name, price=excluded.price, commission=excluded.commission,
                 stats_json=excluded.stats_json, source_url=excluded.source_url,
                 fetched_at=excluded.fetched_at""",
            (product_key, name, price, commission,
             json.dumps(stats or {}, ensure_ascii=False), source_url, now_ts()))
