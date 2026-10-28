# -*- coding: utf-8 -*-
"""bot_interactions 留痕：零成本直达应答计入「AI 互动分布」的口径。

背景（2026-10-28）：排品查询/选品推荐/驾驶舱导航是**零成本直达回答**，
不建任务、不写积分流水 ⇒ 原来在 H5 驾驶舱「AI 互动分布」里完全不可见。
db.log_interaction 留轻量计数，daren_cockpit/server.py 的 load_cockpit 并进口径。
"""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

server = importlib.import_module("daren_cockpit.server")
from daren_bot import config, db  # noqa: E402


SB_DDL = """
CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT,
    category_distribution TEXT, price_distribution TEXT,
    average_price REAL, video_avg_price TEXT,
    total_products INTEGER, total_gmv REAL, created_at INTEGER,
    platform_talent_id TEXT, douyin_uid TEXT, platform_nickname TEXT,
    risk_rating TEXT, ai_summary TEXT, notes TEXT,
    followers INTEGER, video_ratio TEXT, video_gmv_ratio TEXT,
    total_gmv_text TEXT
);
CREATE TABLE products (
    id TEXT PRIMARY KEY, name TEXT, price REAL, price_range TEXT,
    category TEXT, commission_rates TEXT, tags TEXT,
    shop_score REAL, good_ratio REAL, video_sales_7d INTEGER,
    sales_7d INTEGER, board_rank INTEGER, sale_axis_json TEXT,
    platform_product_id TEXT, status TEXT
);
CREATE TABLE product_talent_match (
    talent_id TEXT, product_id TEXT, match_score REAL, match_reason TEXT
);
CREATE TABLE deals (
    id TEXT PRIMARY KEY, talent_id TEXT, product_id TEXT, product_name TEXT,
    deal_type TEXT, commission_rate REAL, status TEXT, scheduled_at INTEGER,
    priority REAL, plan_slot INTEGER, plan_reason TEXT,
    deal_source TEXT, remind_state TEXT, created_at INTEGER, updated_at INTEGER
);
"""

BOT_DDL = """
CREATE TABLE bot_collect_tasks (
    id TEXT PRIMARY KEY, tenant_key TEXT, type TEXT, target TEXT,
    chat_id TEXT, status TEXT, cache_hit INTEGER DEFAULT 0,
    credits_cost INTEGER DEFAULT 0, payload TEXT, created_at INTEGER
);
CREATE TABLE bot_weekly_snapshots (
    tenant_key TEXT, douyin_id TEXT, week_no INTEGER,
    snapshot TEXT, collected_at TEXT, created_at INTEGER
);
CREATE TABLE bot_product_verdicts (
    id TEXT PRIMARY KEY, tenant_key TEXT, name TEXT, verdict TEXT,
    why TEXT, analyzed_at TEXT, created_at INTEGER,
    product_key TEXT, md TEXT, stats_json TEXT
);
CREATE TABLE bot_interactions (
    tenant_key TEXT NOT NULL, kind TEXT NOT NULL, created_at INTEGER NOT NULL
);
"""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """load_cockpit 全链路依赖：solobrave 库 + daren_bot 库 + 租户 + 飞书样板库打桩。"""
    sb = tmp_path / "sb.db"
    conn = sqlite3.connect(str(sb))
    conn.executescript(SB_DDL)
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,followers,video_ratio,"
        "risk_rating,ai_summary,notes) VALUES (?,?,?,?,?,?,?,?)",
        ("tal_1", "测试达人", "dy_1", 1000, "90%",
         "潜力型", "测试摘要", ""))
    conn.commit()
    conn.close()

    bot = tmp_path / "bot.db"
    conn = sqlite3.connect(str(bot))
    conn.executescript(BOT_DDL)
    conn.execute(
        "INSERT INTO bot_collect_tasks (id,tenant_key,type,target,status,created_at)"
        " VALUES (?,?,?,?,?,?)", ("task_qa1", "t1", "qa", "q", "done", 1))
    conn.execute(
        "INSERT INTO bot_interactions (tenant_key,kind,created_at)"
        " VALUES ('t1','plan_query',1), ('t1','cockpit_link',2),"
        "        ('t1','reco_query',3), ('t1','reco_query',4)")
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "SOLOBRAVE_DB", sb)
    monkeypatch.setattr(config, "DB_PATH", bot)
    monkeypatch.setattr(server.botdb, "get_tenant",
                        lambda tk: {"tenant_key": tk, "bind_douyin_id": "dy_1"})
    monkeypatch.setattr(server, "lark_records", lambda *a, **k: [])
    return {"sb": sb, "bot": bot}


# ─────────────── db 层：留痕与计数 ───────────────

def test_log_and_count_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "daren_bot.db"
    monkeypatch.setattr(config, "DB_PATH", path)
    db.init_db()
    db.log_interaction("t1", "plan_query")
    db.log_interaction("t1", "plan_query")
    db.log_interaction("t1", "reco_query")
    db.log_interaction("t2", "plan_query")
    assert db.interaction_counts("t1") == {"plan_query": 2, "reco_query": 1}
    assert db.interaction_counts("t2") == {"plan_query": 1}
    assert db.interaction_counts("nobody") == {}


def test_log_failure_does_not_raise(tmp_path, monkeypatch):
    """写库失败不许冒到主链路（这只是看板口径）。"""
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "daren_bot.db")
    db.init_db()

    def _boom(*a, **kw):
        raise RuntimeError("db locked")
    monkeypatch.setattr(db, "db_ctx", _boom)
    db.log_interaction("t1", "plan_query")  # 不抛即过


# ─────────────── 7188 侧：load_cockpit 并进口径 ───────────────

def test_load_cockpit_merges_interactions(env):
    """任务计数 + 零成本直达计数并成同一张 interact。"""
    d = server.load_cockpit("t1")
    assert d["interact"]["智能问答"] == 1       # 来自 bot_collect_tasks
    assert d["interact"]["排品查询"] == 1       # 来自 bot_interactions
    assert d["interact"]["驾驶舱导航"] == 1
    assert d["interact"]["选品推荐"] == 2


def test_load_cockpit_survives_missing_interactions_table(tmp_path, monkeypatch):
    """7188 先于 daren_bot 迁移重启 ⇒ bot_interactions 缺表，
    零成本段静默跳过，任务计数不受影响。"""
    sb = tmp_path / "sb.db"
    conn = sqlite3.connect(str(sb))
    conn.executescript(SB_DDL)
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,followers,video_ratio,"
        "risk_rating,ai_summary,notes) VALUES (?,?,?,?,?,?,?,?)",
        ("tal_1", "测试达人", "dy_1", 1000, "90%", "潜力型", "测试摘要", ""))
    conn.commit()
    conn.close()

    bot = tmp_path / "bot.db"
    conn = sqlite3.connect(str(bot))
    # ★ 故意不建 bot_interactions 表（DDL 里剔除该段）
    ddl_no_interactions = BOT_DDL.split("CREATE TABLE bot_interactions")[0]
    conn.executescript(ddl_no_interactions)
    conn.execute(
        "INSERT INTO bot_collect_tasks (id,tenant_key,type,target,status,created_at)"
        " VALUES (?,?,?,?,?,?)", ("task_qa1", "t1", "qa", "q", "done", 1))
    # 选品卡行：证明缺表被兜住后，后续的选品卡查询仍执行到了
    conn.execute(
        "INSERT INTO bot_product_verdicts (id,tenant_key,name,verdict,why,"
        "analyzed_at,created_at,stats_json) VALUES (?,?,?,?,?,?,?,?)",
        ("v1", "t1", "测试商品", "慎带", "理由", "2026-10-28", 1, "{}"))
    conn.commit()
    conn.close()

    monkeypatch.setattr(server, "SOLOBRAVE_DB", sb)
    monkeypatch.setattr(config, "DB_PATH", bot)
    monkeypatch.setattr(server.botdb, "get_tenant",
                        lambda tk: {"tenant_key": tk, "bind_douyin_id": "dy_1"})
    monkeypatch.setattr(server, "lark_records", lambda *a, **k: [])

    d = server.load_cockpit("t1")
    assert d["interact"]["智能问答"] == 1
    assert "排品查询" not in d["interact"]
    # 关键：缺表没吃掉后面的选品卡查询
    assert len(d["cards"]) == 1 and d["cards"][0]["name"] == "测试商品"
