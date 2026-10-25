"""SQLite 独立库 daren_bot.db —— DDL 与 DAREN-BOT-SPEC.md v1.1 第 3 章逐字对齐。

服务启动自动建表。连接对象线程安全（模块级锁 + WAL）。
"""
import datetime
import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager

from . import config

log = logging.getLogger("daren-bot.db")

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
  owner_open_id   TEXT DEFAULT '',              -- 达人本人 open_id：多维表格创建后授权用
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

-- 3.4b 周报快照：每次采集成功存一份选定字段，周报环比 = 本周快照 vs 上周快照
-- 注意：百应数据是「近60天滚动窗」而非自然周，周报口径写「滚动窗口对比」，不编造"本周"
CREATE TABLE IF NOT EXISTS bot_weekly_snapshots (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_key   TEXT NOT NULL,
  douyin_id    TEXT NOT NULL,
  week_no      INTEGER NOT NULL,                -- ISO 8601 周数
  snapshot     TEXT NOT NULL,                   -- JSON: 选定字段+ocr_raw_fields
  collected_at TEXT DEFAULT '',                 -- 采集完成时间（百应口径）
  created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ws_douyin ON bot_weekly_snapshots(douyin_id, week_no);

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

-- 3.7 选品结论记录：每次商品分析把 AI 给的绿黄红结论落库，供达人看板「本周精选」区展示
CREATE TABLE IF NOT EXISTS bot_product_verdicts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_key  TEXT NOT NULL,
  name        TEXT DEFAULT '',
  verdict     TEXT DEFAULT '',                  -- 能带/慎带/别带
  why         TEXT DEFAULT '',
  analyzed_at TEXT DEFAULT '',                  -- 分析日期（YYYY-MM-DD），看板上标注
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pverdict_tenant ON bot_product_verdicts(tenant_key, created_at);

-- 3.9 问答缓存：同一问题短期内不重复调 LLM（老大要求：抓一次存下来，下次直接读）
--     key = (租户, 问题哈希)；TTL 1 天（画像类结论一天内不会变）
CREATE TABLE IF NOT EXISTS bot_qa_cache (
  tenant_key TEXT NOT NULL,
  q_hash     TEXT NOT NULL,
  question   TEXT DEFAULT '',
  answer     TEXT DEFAULT '',
  created_at INTEGER NOT NULL,
  PRIMARY KEY (tenant_key, q_hash)
);
CREATE INDEX IF NOT EXISTS idx_qacache_created ON bot_qa_cache(created_at);

-- 3.8 合作机会推送日志：去重（record_id+tenant_key 唯一）+ 频控（每周≤N条）
-- 工单 docs/BACKLOG-coop-automation-工单.md 硬要求①
CREATE TABLE IF NOT EXISTS bot_coop_push_log (
  id          TEXT PRIMARY KEY,
  record_id   TEXT NOT NULL,                 -- 飞书合作机会表 record_id
  tenant_key  TEXT NOT NULL,                 -- 推给了谁
  brand       TEXT DEFAULT '',               -- 品牌/商家（排查用）
  category    TEXT DEFAULT '',
  week_no     INTEGER NOT NULL DEFAULT 0,    -- ISO 周号，频控按周算
  pushed_at   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_coop_push_uniq
  ON bot_coop_push_log(record_id, tenant_key);
CREATE INDEX IF NOT EXISTS idx_coop_push_week
  ON bot_coop_push_log(tenant_key, week_no);

-- 3.9 商家招商减负提交（极简表单落地）
-- 商家只给：品牌 + 联系方式 + （商品链接 | 商品图片 | 一段话描述）任一
-- 解析结果（商品名/价格/佣金/类目）由后台异步回填；再同步到飞书合作机会表
-- 走运营既有的「待评估 → 有意向/已合作」管道。
-- ⚠️ 只丢不造：解析不出来就留空 + status='partial'，绝不编商品字段。
CREATE TABLE IF NOT EXISTS bot_coop_apply (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  code           TEXT NOT NULL UNIQUE,            -- 提交编号（AP-XXXXXX），商家凭它查进度
  brand          TEXT NOT NULL,                   -- 品牌/商家
  contact        TEXT NOT NULL,                   -- 联系方式（微信/电话，二合一）
  link           TEXT DEFAULT '',                 -- 商品链接
  image_path     TEXT DEFAULT '',                 -- 商品图本地路径
  desc_text      TEXT DEFAULT '',                 -- 一段话描述
  status         TEXT NOT NULL DEFAULT 'pending', -- pending/parsing/done/partial/failed
  source         TEXT DEFAULT '',                 -- 解析来源：link/image/desc（多选逗号分隔）
  product_name   TEXT DEFAULT '',
  price          TEXT DEFAULT '',
  commission     TEXT DEFAULT '',
  sales          TEXT DEFAULT '',
  category       TEXT DEFAULT '',                 -- 合作类目（用于匹配达人）
  shop           TEXT DEFAULT '',
  product_url    TEXT DEFAULT '',                 -- 解析出的真实商品页 URL
  error          TEXT DEFAULT '',                 -- 解析失败原因（可读，排查用）
  coop_record_id TEXT DEFAULT '',                 -- 同步到飞书合作机会表后的 record_id
  created_at     INTEGER NOT NULL,
  updated_at     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coop_apply_created
  ON bot_coop_apply(created_at DESC);
"""


def init_db():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with db_ctx() as conn:
        conn.executescript(DDL)
        # 轻量迁移：tasks 表补 payload 列（存名字提示等任务负载，JSON）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_collect_tasks)")}
        if "payload" not in cols:
            conn.execute("ALTER TABLE bot_collect_tasks "
                         "ADD COLUMN payload TEXT DEFAULT ''")
        # 轻量迁移：tenants 表补 owner_open_id（多维表格创建后授权给达人本人）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_tenants)")}
        if "owner_open_id" not in cols:
            conn.execute("ALTER TABLE bot_tenants "
                         "ADD COLUMN owner_open_id TEXT DEFAULT ''")
        # 轻量迁移：周报订阅（默认订阅；weekly_push_slot=周一09:37，错峰整点半点）
        if "weekly_sub" not in cols:
            conn.execute("ALTER TABLE bot_tenants "
                         "ADD COLUMN weekly_sub INTEGER NOT NULL DEFAULT 1")
        if "weekly_push_slot" not in cols:
            conn.execute("ALTER TABLE bot_tenants "
                         "ADD COLUMN weekly_push_slot INTEGER NOT NULL DEFAULT 577")
        # 轻量迁移：选品结论表补「商品键 + 完整结论 + 商品数据快照」三列
        #   （2026-10-21 加缓存：同商品同达人的结论直接复用，不重复烧 LLM）
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bot_product_verdicts)")}
        if "product_key" not in cols:
            conn.execute("ALTER TABLE bot_product_verdicts "
                         "ADD COLUMN product_key TEXT DEFAULT ''")
        if "md" not in cols:
            conn.execute("ALTER TABLE bot_product_verdicts "
                         "ADD COLUMN md TEXT DEFAULT ''")
        if "stats_json" not in cols:
            conn.execute("ALTER TABLE bot_product_verdicts "
                         "ADD COLUMN stats_json TEXT DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pverdict_key "
                     "ON bot_product_verdicts(tenant_key, product_key, created_at)")
    # 启动时清一次过期的问答缓存（结论档案表不清，见 purge_expired_caches 注释）
    try:
        n = purge_expired_caches()
        if n:
            log.info("purged %d expired qa cache rows", n)
    except Exception:
        log.warning("purge qa cache failed", exc_info=True)


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


def get_tenant_by_douyin(douyin_id: str):
    """按绑定抖音号反查租户（notify_report 场景：只有 douyin_id 没有 tenant_key）。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_tenants WHERE bind_douyin_id=? AND status='active'",
            (douyin_id,)).fetchone()
        return dict(r) if r else None


def get_latest_douyin_id(tenant_key: str) -> str:
    """这个租户**最近活跃**的达人抖音号（达人自己报过的那个）。

    ★ 为什么不能只信 `bind_douyin_id`（2026-10-24 踩过）：
      实测 `daren_test2` 配的 `bind_douyin_id=324733241`（萌萌超会买），
      但老大实际在飞书聊的是 `Ayn11930`（一百）——
      查错人 ⇒ 排品表/提醒全落到别人的数据上，看起来就是「这功能没做」。
      ⇒ 优先取该租户**最近一次抖音号分析**请求里的号（那是达人自己报的，最真），
        没有才回退到 `bind_douyin_id`。
    """
    with db_ctx() as conn:
        try:
            r = conn.execute(
                "SELECT target FROM bot_collect_tasks "
                "WHERE tenant_key=? AND type='douyin' AND target!='' "
                "ORDER BY created_at DESC LIMIT 1", (tenant_key,)).fetchone()
            if r and (r[0] or '').strip():
                return r[0].strip()
        except Exception:
            pass
    try:
        t = get_tenant(tenant_key) or {}
    except Exception:
        t = {}
    return (t.get('bind_douyin_id') or '').strip()


def set_owner_open_id(tenant_key: str, open_id: str) -> None:
    """记住达人本人的 open_id（首次消息时写入；建多维表格后授权可见）。"""
    if not open_id:
        return
    with db_ctx() as conn:
        conn.execute(
            "UPDATE bot_tenants SET owner_open_id=? WHERE tenant_key=? AND owner_open_id=''",
            (open_id, tenant_key))


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


def grant(tenant_key: str, amount: int, action: str = 'grant') -> int:
    """运营发放积分（内测期手动充值）。返回发放后余额。"""
    if amount <= 0:
        raise ValueError("发放积分必须为正数")
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        balance = int(r["balance"]) if r else 0
        new_balance = balance + amount
        conn.execute(
            "INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(tenant_key) DO UPDATE SET balance=excluded.balance, "
            "updated_at=excluded.updated_at",
            (tenant_key, new_balance, now_ts()))
        _ledger(conn, tenant_key, action, -amount, new_balance, 0, '')
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


def peer_douyin_count(tenant_key: str, bound_id: str) -> int:
    """该租户已跟踪过的对标号数量（非绑定号的去重计数，含进行中）。"""
    with db_ctx() as conn:
        r = conn.execute(
            """SELECT COUNT(DISTINCT target) FROM bot_collect_tasks
               WHERE tenant_key=? AND type='douyin' AND target<>?
                 AND status IN ('done','pending','collecting')""",
            (tenant_key, bound_id)).fetchone()
        return int(r[0]) if r else 0


# ---------- 任务队列 ----------

def _find_pending(conn, tenant_key: str, task_type: str, target: str):
    """同租户同类型同目标是否已有在跑的任务（幂等键）。"""
    return conn.execute(
        """SELECT id FROM bot_collect_tasks
           WHERE tenant_key=? AND type=? AND target=? AND status IN ('pending','collecting')
           ORDER BY created_at LIMIT 1""",
        (tenant_key, task_type, target)).fetchone()


def enqueue_task(tenant_key: str, task_type: str, target: str,
                 credits_cost: int, cache_hit: int = 0,
                 chat_id: str = '', payload: str = '') -> str:
    """入队，幂等：同租户同目标存在 pending/collecting 时返回原任务 id。

    ⚠️ **只给零成本 / 管理员后门用**（不扣分的路径）。
       凡是**消耗算力**的路径 ⇒ 必须走 `enqueue_task_charged()`
       （它才做「扣费 + 入队」的原子操作），
       否则会出现「扣了钱、却没建任务」或「重复扣费、只建一个任务」。
    """
    with db_ctx() as conn:
        r = _find_pending(conn, tenant_key, task_type, target)
        if r:
            return r["id"]
        task_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO bot_collect_tasks
               (id, tenant_key, type, target, chat_id, status, cache_hit, credits_cost, payload, created_at)
               VALUES (?,?,?,?,?,'pending',?,?,?,?)""",
            (task_id, tenant_key, task_type, target, chat_id,
             cache_hit, credits_cost, payload, now_ts()))
        return task_id


def enqueue_task_charged(tenant_key: str, task_type: str, target: str,
                         cost: int, action: str, cache_hit: int = 0,
                         chat_id: str = '', payload: str = '') -> tuple:
    """★ 原子「扣费 + 入队」—— 消耗算力的路径**只能**走这里。

    ★ 为什么必须是一个操作（2026-10-25 实测过的坑）：
      原来「先 `deduct` 扣费、再 `enqueue_task` 入队」是**两个独立事务**，
      而入队本身是**幂等**的（同租户+同类型+同目标已有 pending/collecting
      ⇒ 返回旧任务、**不新建**）⇒ 达人重复发一次（同一个商品 / 同一个问题 /
      同一个对标号）就**扣两次钱、只建一个任务**；任务失败时 worker 只按任务上
      记的金额退一份 ⇒ **净多扣**（实测：扣 200、只有 1 个任务、只退 100）。
      ⇒ 现在：**先查幂等键 → 再查余额 → 扣费 + 写流水 + 建任务**，全在一个事务里。

    ★ 扣费规则：**只有真建了新任务才扣**——
      命中幂等（不额外消耗算力）⇒ 不扣分（老大：「不消耗的就算了」）。

    返回 `(status, task_id, balance)`：
      status = "ok"           扣费成功 + 已入队
      status = "duplicate"    命中幂等 ⇒ **未扣费**、未新建（task_id = 旧任务 id）
      status = "insufficient" 余额不足 ⇒ **未扣费**、未入队
    """
    with db_ctx() as conn:
        # 1) 幂等命中？⇒ 不额外消耗算力 ⇒ 不扣费
        r = _find_pending(conn, tenant_key, task_type, target)
        if r:
            br = conn.execute("SELECT balance FROM bot_credits_accounts "
                              "WHERE tenant_key=?", (tenant_key,)).fetchone()
            return "duplicate", r["id"], int(br["balance"]) if br else 0
        # 2) 余额够吗？
        br = conn.execute("SELECT balance FROM bot_credits_accounts "
                          "WHERE tenant_key=?", (tenant_key,)).fetchone()
        balance = int(br["balance"]) if br else 0
        if balance < cost:
            return "insufficient", "", balance
        # 3) 扣费 + 建任务（同一事务 ⇒ 要么都成，要么都不成）
        new_balance = balance - cost
        conn.execute(
            "UPDATE bot_credits_accounts SET balance=?, updated_at=? WHERE tenant_key=?",
            (new_balance, now_ts(), tenant_key))
        task_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO bot_collect_tasks
               (id, tenant_key, type, target, chat_id, status, cache_hit, credits_cost, payload, created_at)
               VALUES (?,?,?,?,?,'pending',?,?,?,?)""",
            (task_id, tenant_key, task_type, target, chat_id,
             cache_hit, cost, payload, now_ts()))
        # 流水带上 task_id ⇒ 退款/查账能双向对得上
        _ledger(conn, tenant_key, action, cost, new_balance, cache_hit, task_id)
        return "ok", task_id, new_balance


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


def reclaim_stale_collecting() -> int:
    """worker 启动时把残留的 collecting 打回 pending 重跑。
    进程重启后内存里的 buyin collect_id 已丢、轮询线程已死，这些任务永远不会
    自己完结；积分在入队时已扣，重跑不重复收费。返回回收条数。"""
    with db_ctx() as conn:
        cur = conn.execute(
            "UPDATE bot_collect_tasks SET status='pending', error='' "
            "WHERE status='collecting'")
        return cur.rowcount


def mark_task(task_id: str, status: str, error: str = ''):
    with db_ctx() as conn:
        conn.execute(
            "UPDATE bot_collect_tasks SET status=?, error=?, done_at=? WHERE id=?",
            (status, error[:500], now_ts() if status in ('done', 'failed') else None, task_id))


def save_task_payload(task_id: str, payload: str):
    """feat/daren-reflow 管道 3: 采集成功后回写完整快照 JSON (断链重放原料)。"""
    with db_ctx() as conn:
        conn.execute("UPDATE bot_collect_tasks SET payload=? WHERE id=?",
                     (payload[:200000], task_id))


def purge_old_payloads(days: int = 30) -> int:
    """feat/daren-reflow 管道 3: 30 天滚动清理 — 行保留 (审计/周计数要查), 只清 payload。
    now_ts 是秒级, 故 cutoff 用 days*86400 秒。"""
    cutoff = now_ts() - days * 86400
    with db_ctx() as conn:
        cur = conn.execute(
            "UPDATE bot_collect_tasks SET payload='' "
            "WHERE status IN ('done','failed') AND done_at IS NOT NULL AND done_at > 0 "
            "AND done_at < ? AND payload NOT IN ('', '{}')", (cutoff,))
        return cur.rowcount


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


# ---------- 多维表格联动（spec 第8条：卡片+表格双写） ----------

def save_bitable_refs(tenant_key: str, app_token: str, table_ids: dict) -> None:
    """回写租户的多维表格引用（app_token + 各表 table_id JSON）。"""
    with db_ctx() as conn:
        conn.execute(
            "UPDATE bot_tenants SET bitable_app_token=?, bitable_table_ids=?, updated_at=? WHERE tenant_key=?",
            (app_token, json.dumps(table_ids, ensure_ascii=False), now_ts(), tenant_key))


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


# ---------- 复用层（2026-10-21 老大要求：抓一次存下来，下次直接读） ----------
# 设计原则：
#   ① 数据分三层存 —— 结构化指标走结构化表，文本结论走结论表 + 知识库
#   ② 复用判定放在**入队处**（唯一入口），命中就 cache_hit=1 且不扣积分
#      —— 放到执行时判定就晚了，LLM 已经烧掉了
#   ③ TTL 分层：问答 1 天（画像一天不变）；商品数据/选品结论 7 天
#      （= 老大的「一周之后再给达人重新抓取」：TTL 到期 = 自动回源重抓）
QA_CACHE_TTL = 86400              # 秒
VERDICT_CACHE_TTL = 7 * 86400     # 与商品缓存 / 周度重抓同周期

# 含时效词的问题**一律不缓存** —— 答案会过期，缓存住等于给假的新鲜度
_QA_VOLATILE_WORDS = ("最近", "今天", "今日", "最新", "现在", "刚才", "实时",
                       "目前", "本周", "这周", "这几天", "近期", "几点", "今天到")


def _qhash(question: str) -> str:
    return hashlib.md5((question or "").strip().encode("utf-8")).hexdigest()


def qa_cacheable(question: str) -> bool:
    """这个问题能不能缓存（含时效词 → 不能）。"""
    q = (question or "").strip()
    if len(q) < 4:          # 太短的问题缓存下来没意义，还容易被拒答占位
        return False
    return not any(w in q for w in _QA_VOLATILE_WORDS)


def qa_cache_get(tenant_key: str, question: str):
    """命中返回答案文本；未命中/过期/不可缓存 → None。"""
    if not qa_cacheable(question):
        return None
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT answer, created_at FROM bot_qa_cache "
            "WHERE tenant_key=? AND q_hash=?", (tenant_key, _qhash(question))).fetchone()
        if not r:
            return None
        d = dict(r)
        if now_ts() - int(d["created_at"]) > QA_CACHE_TTL:
            return None      # 过期视同未命中
        return d["answer"] or None


def qa_cache_put(tenant_key: str, question: str, answer: str) -> None:
    if not qa_cacheable(question) or not (answer or "").strip():
        return
    with db_ctx() as conn:
        conn.execute(
            """INSERT INTO bot_qa_cache (tenant_key, q_hash, question, answer, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(tenant_key, q_hash) DO UPDATE SET
                 answer=excluded.answer, created_at=excluded.created_at""",
            (tenant_key, _qhash(question), (question or "")[:200], answer, now_ts()))


def verdict_cache_get(tenant_key: str, product_key: str):
    """同租户同商品的结论缓存（7 天内直接复用，不重复调 LLM）。"""
    if not product_key:
        return None
    with db_ctx() as conn:
        r = conn.execute(
            """SELECT * FROM bot_product_verdicts
               WHERE tenant_key=? AND product_key=?
               ORDER BY created_at DESC LIMIT 1""",
            (tenant_key, product_key)).fetchone()
        if not r:
            return None
        d = dict(r)
        if now_ts() - int(d["created_at"]) > VERDICT_CACHE_TTL:
            return None
        return d


def verdict_cache_put(tenant_key: str, product_key: str, name: str = "",
                      verdict: str = "", why: str = "", md: str = "",
                      stats: dict = None) -> None:
    if not product_key:
        return
    with db_ctx() as conn:
        conn.execute(
            """INSERT INTO bot_product_verdicts
               (tenant_key, product_key, name, verdict, why, md, stats_json,
                analyzed_at, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (tenant_key, product_key, name or "", verdict or "", why or "",
             md or "", json.dumps(stats or {}, ensure_ascii=False),
             datetime.date.today().isoformat(), now_ts()))


def purge_expired_caches() -> int:
    """清掉过期的问答缓存（启动时跑一次）。

    ⚠️ 只清 QA 缓存。`bot_product_verdicts` 是**结论档案**不是缓存 ——
       它记录「哪天、给谁、判了什么结论」，是审计和回溯的依据，不能删。
       （过期判定在读的时候做：verdict_cache_get 过期视同未命中，
         同时保留历史行。两者分工：读时判过期、启动时清纯缓存。）
    """
    with db_ctx() as conn:
        cur = conn.execute("DELETE FROM bot_qa_cache WHERE created_at < ?",
                            (now_ts() - QA_CACHE_TTL,))
        return cur.rowcount or 0


# ---------- 周报（weekly.py 配套：快照/订阅/系统账） ----------

def log_system_cost(tenant_key: str, action: str, cost: int,
                    ref_task_id: str = '') -> None:
    """系统账：真实成本入账但不扣达人余额（balance_after 取当前值原样记录）。
    weekly_auto_collect / weekly_report 走此路径——余额 0 的达人照常收周报。"""
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        balance = int(r["balance"]) if r else 0
        _ledger(conn, tenant_key, action, cost, balance, 0, ref_task_id)


def set_weekly_sub(tenant_key: str, sub: int) -> None:
    with db_ctx() as conn:
        conn.execute("UPDATE bot_tenants SET weekly_sub=?, updated_at=? "
                     "WHERE tenant_key=?", (sub, now_ts(), tenant_key))


def get_weekly_sub(tenant_key: str) -> int:
    with db_ctx() as conn:
        r = conn.execute("SELECT weekly_sub FROM bot_tenants WHERE tenant_key=?",
                         (tenant_key,)).fetchone()
        return int(r["weekly_sub"]) if r else 1


def save_snapshot(tenant_key: str, douyin_id: str, week_no: int,
                  snapshot: dict, collected_at: str = '') -> None:
    with db_ctx() as conn:
        conn.execute(
            "INSERT INTO bot_weekly_snapshots "
            "(tenant_key, douyin_id, week_no, snapshot, collected_at, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (tenant_key, douyin_id, week_no,
             json.dumps(snapshot, ensure_ascii=False), collected_at, now_ts()))


def get_snapshot(douyin_id: str, week_no: int):
    """取指定周快照（该周最后一份）。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_weekly_snapshots WHERE douyin_id=? AND week_no=? "
            "ORDER BY created_at DESC LIMIT 1", (douyin_id, week_no)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["data"] = json.loads(d["snapshot"])
        except Exception:
            d["data"] = {}
        return d


def get_prev_snapshot(douyin_id: str, before_week_no: int):
    """取 before_week_no 之前最近一份快照（环比基线）。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_weekly_snapshots WHERE douyin_id=? AND week_no<? "
            "ORDER BY week_no DESC, created_at DESC LIMIT 1",
            (douyin_id, before_week_no)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["data"] = json.loads(d["snapshot"])
        except Exception:
            d["data"] = {}
        return d


def has_done_collect_this_week(tenant_key: str, douyin_id: str,
                               week_start_ts: int) -> bool:
    """本周窗口内是否已有成功采集（任何途径：手动/自动）——有就不重复采。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT 1 FROM bot_collect_tasks WHERE tenant_key=? AND target=? "
            "AND type='douyin' AND status='done' AND done_at>=? LIMIT 1",
            (tenant_key, douyin_id, week_start_ts)).fetchone()
        return r is not None


def count_auto_fails_this_week(tenant_key: str, douyin_id: str,
                               week_start_ts: int) -> int:
    """本周自动采集(payload 含 \"auto\":1)失败次数——重试上限用。"""
    with db_ctx() as conn:
        n = conn.execute(
            "SELECT COUNT(*) c FROM bot_collect_tasks WHERE tenant_key=? AND target=? "
            "AND type='douyin' AND status='failed' AND created_at>=? "
            "AND payload LIKE '%\"auto\":1%'",
            (tenant_key, douyin_id, week_start_ts)).fetchone()
        return int(n["c"])


def get_latest_chat_id(tenant_key: str) -> str:
    """该租户最近一次任务的 chat_id——主动推送目标（达人跟 bot 有过会话）。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT chat_id FROM bot_collect_tasks WHERE tenant_key=? AND chat_id!='' "
            "ORDER BY created_at DESC LIMIT 1", (tenant_key,)).fetchone()
        return r["chat_id"] if r else ""


# ---------------- 合作机会推送：去重 + 频控（工单 coop-automation） ----------------

def coop_push_exists(record_id: str, tenant_key: str) -> bool:
    """这条机会是否已经推过给这个达人（防重复打扰）。"""
    if not record_id or not tenant_key:
        return False
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT 1 FROM bot_coop_push_log WHERE record_id=? AND tenant_key=?",
            (record_id, tenant_key)).fetchone()
        return r is not None


def coop_push_count_week(tenant_key: str, week_no: int) -> int:
    """该达人本周已收到多少条合作机会（频控：每周至多 N 条）。"""
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT COUNT(*) c FROM bot_coop_push_log "
            "WHERE tenant_key=? AND week_no=?", (tenant_key, week_no)).fetchone()
        return int(r["c"])


def log_coop_push(record_id: str, tenant_key: str, brand: str = "",
                  category: str = "", week_no: int = 0) -> None:
    """记一条推送（去重 + 频控的依据）。"""
    import uuid
    with db_ctx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO bot_coop_push_log "
            "(id, record_id, tenant_key, brand, category, week_no, pushed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), record_id, tenant_key, brand, category,
             week_no, now_ts()))


# ---------------- 商家招商减负提交（极简表单落地） ----------------

def create_coop_apply(code: str, brand: str, contact: str, link: str = "",
                      image_path: str = "", desc_text: str = "") -> int:
    """落一条招商提交（status=pending，等后台解析回填）。返回自增 id。"""
    with db_ctx() as conn:
        cur = conn.execute(
            "INSERT INTO bot_coop_apply "
            "(code, brand, contact, link, image_path, desc_text, status, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (code, brand, contact, link, image_path, desc_text, "pending",
             now_ts(), now_ts()))
        return int(cur.lastrowid)


def update_coop_apply(code: str, **fields) -> None:
    """回填解析结果 / 状态。只允许更新白名单列（防 SQL 注入拼错列名）。"""
    allowed = ("status", "source", "product_name", "price", "commission",
               "sales", "category", "shop", "product_url", "error",
               "coop_record_id")
    sets, vals = [], []
    for k, v in fields.items():
        if k not in allowed:
            continue
        sets.append(f"{k}=?")
        vals.append(v)
    if not sets:
        return
    sets.append("updated_at=?")
    vals.extend([now_ts(), code])
    with db_ctx() as conn:
        conn.execute(f"UPDATE bot_coop_apply SET {','.join(sets)} WHERE code=?",
                     vals)


def get_coop_apply(code: str) -> dict:
    """按提交编号查一条（商家查进度用）。"""
    if not code:
        return {}
    with db_ctx() as conn:
        r = conn.execute(
            "SELECT * FROM bot_coop_apply WHERE code=?", (code,)).fetchone()
        return dict(r) if r else {}


def recent_coop_apply(limit: int = 50) -> list:
    """最近的招商提交（运营工作台看）。"""
    with db_ctx() as conn:
        rows = conn.execute(
            "SELECT * FROM bot_coop_apply ORDER BY created_at DESC LIMIT ?",
            (int(limit),)).fetchall()
        return [dict(r) for r in rows]
