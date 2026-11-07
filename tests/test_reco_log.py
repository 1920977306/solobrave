"""推荐→出单反馈闭环（daren_bot/reco_log.py）的测试。

链路：推荐落档（每品一条、不去重留痕）→ 采纳标记（幂等）→
data_status 三态分类（纯函数钉死口径）→ 周采集后分组回填 → 聚合统计。

★ 铁律锁点（2026-11-07 老大拍板四个设计点，改口径前先读 reco_log.py docstring）：
- data_status 三态：命中率只算 ok，missing 单独汇总成采集健康度
  ⇒ 不允许把「采集失败」当「品不适合号」；
- accepted 与命中率分开：推荐命中率（准不准）vs 采纳命中率（说服力）；
- 一期只算正向、只丢不造：sold_gmv 预留可空，stats() 必须如实标 sold_pending_source；
- 同一品再次推荐**新增一行**（看判断变化），mark_accepted 幂等（重复点不重复计时）。
"""
import importlib
import sqlite3
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

reco_log = importlib.import_module("daren_bot.reco_log")

NOW = int(time.time())


def _mk_main_db(tmp_path, rows):
    """造最小主库（platform_talent_stats），rows = [(douyin_id, captured_at)]。"""
    path = tmp_path / "sb.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE platform_talent_stats ("
        " id INTEGER PRIMARY KEY, douyin_id TEXT, captured_at INTEGER)")
    conn.executemany(
        "INSERT INTO platform_talent_stats (douyin_id, captured_at) "
        "VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return path


@pytest.fixture()
def botdb(tmp_path, monkeypatch):
    """bot 库（bot_reco_log）+ 主库（platform_talent_stats）都指 tmp，隔离生产 data/。"""
    bot_db = tmp_path / "daren_bot.db"
    monkeypatch.setattr(reco_log.db.config, "DB_PATH", bot_db)
    reco_log.db.init_db()
    main_db = _mk_main_db(tmp_path, [])
    monkeypatch.setattr(reco_log, "SOLOBRAVE_DB", main_db)
    return bot_db


def _rows(bot_db):
    conn = sqlite3.connect(str(bot_db))
    cols = [c[0] for c in conn.execute(
        "SELECT * FROM bot_reco_log LIMIT 0").description]
    out = [dict(zip(cols, r)) for r in conn.execute("SELECT * FROM bot_reco_log")]
    conn.close()
    return out


_RECOS = [
    {"product_id": "prod_a", "name": "面霜A", "stars": 5,
     "reasons": ["店铺体验分 92", "好评率 95%"]},
    {"product_id": "prod_b", "name": "面霜B", "stars": 3,
     "reasons": []},
]


# ─────────────── 推荐落档 ───────────────

def test_log_recommendations_one_row_per_product(botdb):
    n = reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS, now=NOW)
    assert n == 2
    rows = _rows(botdb)
    assert len(rows) == 2
    a = next(r for r in rows if r["product_id"] == "prod_a")
    assert a["tenant_key"] == "tk1" and a["douyin_id"] == "dy_1"
    assert a["talent_id"] == "tal_1" and a["product_name"] == "面霜A"
    assert a["star_level"] == 5 and a["recommend_at"] == NOW
    assert a["accepted"] == 0 and a["data_status"] == ""
    assert a["sold_gmv"] is None            # 一期只丢不造：出单未知 = NULL 不是 0
    b = next(r for r in rows if r["product_id"] == "prod_b")
    assert b["verdict"] == ""               # 无理由列表不编造 verdict


def test_log_recommendations_same_product_again_adds_row(botdb):
    """同一品再次推荐 = 新增一行（判断随时间变化要留痕，不去重）。"""
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS[:1], now=NOW)
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS[:1],
                                 now=NOW + 86400)
    rows = _rows(botdb)
    assert len(rows) == 2
    assert {r["recommend_at"] for r in rows} == {NOW, NOW + 86400}


def test_log_recommendations_skips_empty_product_id(botdb):
    recos = [{"product_id": "", "name": "x"}, {"product_id": "  ", "name": "y"}]
    assert reco_log.log_recommendations("tk1", "dy_1", "", recos, now=NOW) == 0
    assert _rows(botdb) == []


# ─────────────── 采纳标记 ───────────────

def test_mark_accepted_marks_all_unaccepted_rows(botdb):
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS[:1], now=NOW)
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS[:1],
                                 now=NOW + 86400)
    n = reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 100)
    assert n == 2                            # 该 (达人,品) 所有未采纳行都标上
    rows = _rows(botdb)
    assert all(r["accepted"] == 1 for r in rows)
    assert all(r["accepted_at"] == NOW + 100 for r in rows)


def test_mark_accepted_is_idempotent(botdb):
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS[:1], now=NOW)
    assert reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 100) == 1
    # 幂等：重复点不重复计时、不再改行
    assert reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 999) == 0
    rows = _rows(botdb)
    assert rows[0]["accepted_at"] == NOW + 100


def test_mark_accepted_scoped_to_product(botdb):
    reco_log.log_recommendations("tk1", "dy_1", "tal_1", _RECOS, now=NOW)
    reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 100)
    rows = {r["product_id"]: r for r in _rows(botdb)}
    assert rows["prod_a"]["accepted"] == 1
    assert rows["prod_b"]["accepted"] == 0   # 不波及别的品


# ─────────────── data_status 三态（纯函数钉死口径）───────────────

def test_classify_data_status_three_states():
    c = reco_log.classify_data_status
    assert c(None, now=NOW) == "missing"                       # 从没采到
    assert c(NOW - 6 * 86400, now=NOW) == "ok"                 # ≤7 天
    assert c(NOW - 7 * 86400, now=NOW) == "ok"                 # 边界 = 7 天整仍 ok
    assert c(NOW - 7 * 86400 - 1, now=NOW) == "stale"          # 边界外 = stale


# ─────────────── 周采集后回填 ───────────────

def test_backfill_groups_by_douyin_id(tmp_path, monkeypatch):
    bot_db = tmp_path / "daren_bot.db"
    monkeypatch.setattr(reco_log.db.config, "DB_PATH", bot_db)
    reco_log.db.init_db()
    # dy_fresh 有近期采集、dy_old 只有旧采集、dy_none 主库无行
    main_db = _mk_main_db(tmp_path, [
        ("dy_fresh", NOW - 86400),
        ("dy_old", NOW - 30 * 86400),
    ])
    monkeypatch.setattr(reco_log, "SOLOBRAVE_DB", main_db)

    recos = [{"product_id": "prod_a", "name": "A", "stars": 4, "reasons": ["x"]}]
    for dy in ("dy_fresh", "dy_old", "dy_none"):
        reco_log.log_recommendations("tk1", dy, "", recos, now=NOW)

    counts = reco_log.backfill_data_status(now=NOW)
    assert counts == {"ok": 1, "stale": 1, "missing": 1}
    status = {r["douyin_id"]: r["data_status"] for r in _rows(bot_db)}
    assert status == {"dy_fresh": "ok", "dy_old": "stale",
                      "dy_none": "missing"}


def test_backfill_does_not_touch_sold_fields(tmp_path, monkeypatch):
    """只丢不造：回填绝不碰 sold_*（出单数据源未接入，sold_gmv 保持 NULL）。"""
    bot_db = tmp_path / "daren_bot.db"
    monkeypatch.setattr(reco_log.db.config, "DB_PATH", bot_db)
    reco_log.db.init_db()
    monkeypatch.setattr(reco_log, "SOLOBRAVE_DB",
                        _mk_main_db(tmp_path, [("dy_1", NOW)]))
    reco_log.log_recommendations(
        "tk1", "dy_1", "",
        [{"product_id": "prod_a", "name": "A", "stars": 4, "reasons": ["x"]}],
        now=NOW)
    reco_log.backfill_data_status(now=NOW)
    rows = _rows(bot_db)
    assert rows[0]["sold_gmv"] is None and rows[0]["sold_source"] == ""


# ─────────────── 聚合统计 ───────────────

def test_stats_aggregation(botdb):
    reco_log.log_recommendations("tk1", "dy_1", "", _RECOS, now=NOW)
    reco_log.log_recommendations("tk2", "dy_2", "", _RECOS[:1], now=NOW)
    reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 10)

    s = reco_log.stats()
    assert s["total"] == 3 and s["accepted"] == 1
    assert s["accept_rate"] == round(1 / 3, 4)
    # data_status 只统计已采纳行（进入出单观察的），未采纳行不算；
    # 空串在 API 侧归一成 "unset"（JSON 里不留空键）
    assert s["data_status"] == {"unset": 1}
    # 一期只丢不造：出单如实标待接入，不显示假数字
    assert s["sold"] == 0 and s["sold_pending_source"] is True
    tenants = {t["tenant_key"]: t for t in s["tenants"]}
    assert tenants["tk1"] == {"tenant_key": "tk1", "total": 2, "accepted": 1}
    assert tenants["tk2"]["accepted"] == 0


def test_stats_per_tenant_filter(botdb):
    reco_log.log_recommendations("tk1", "dy_1", "", _RECOS, now=NOW)
    reco_log.log_recommendations("tk2", "dy_2", "", _RECOS, now=NOW)
    reco_log.mark_accepted("tk1", "dy_1", "prod_a", now=NOW + 10)

    s = reco_log.stats(tenant_key="tk1")
    assert s["total"] == 2 and s["accepted"] == 1
    assert s["accept_rate"] == 0.5
    assert s["tenants"] == []               # 单租户视图不重复列明细
