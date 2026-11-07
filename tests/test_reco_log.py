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
import json
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


# ═══════════════ 二期：达人端商品详情 top 带货达人 ═══════════════

import importlib as _il
pts = _il.import_module("daren_cockpit.tools.product_talent_stats")
captain = _il.import_module("daren_cockpit.tools.captain_stats")


# ─────────────── parse_top_talents（纯函数，只丢不造）───────────────
# 契约 2026-11-07 扫码实测钉死：达人端商品决策页「带货内容」模块
# POST /pc/selection/decision/pack_detail → data.model.sale_content_data.content_list
# 真实样本留档 data/buyin_browser/manhua_content.json

def _item(nickname="芝士百宝箱", author_id="4013663505615021", fans=33992,
          sales_fmt="2.5千+", gmv_fmt="1千+"):
    return {"content_id": "7698379366615254518", "content_type": "video",
            "content_title": "万万没想到我们擦嘴用的纸巾居然是这样加工出来的",
            "author_name": nickname, "author_fans_count": fans,
            "author_sec_id": "v2_0a2c", "format_sales": sales_fmt,
            "format_sales_amount": gmv_fmt, "like_count": 10281,
            "play_count": 2058836, "order_conversion_rate": 0.0752,
            "share_url": f"https://www.douyin.com/share/user/{author_id}",
            "is_self_sale": False}


def _wrap(items, count=None):
    return {"code": 0, "st": 0, "msg": "",
            "data": {"model": {"sale_content_data":
                               {"content_list": items,
                                "count": count if count is not None else len(items),
                                "code": 0}}}}


def test_parse_real_contract_shape():
    """实测结构：share_url 尾部数字 → author_id；nickname/粉丝解析正确。"""
    rows = pts.parse_top_talents(_wrap([_item()]), "3644272059510584408",
                                 now=NOW)
    assert len(rows) == 1
    r = rows[0]
    assert r["rank"] == 1
    assert r["author_id"] == "4013663505615021"   # share_url 解析，非格式猜
    assert r["nickname"] == "芝士百宝箱"
    assert r["fans_count"] == 33992
    assert r["platform_product_id"] == "3644272059510584408"
    assert r["stat_date"] == time.strftime("%Y-%m-%d", time.localtime(NOW))


def test_parse_platform_only_gives_ranges_so_gmv_sales_are_none():
    """平台只给区间文案（format_sales_amount「1千+」）不给真值 ⇒ 只丢不造。"""
    rows = pts.parse_top_talents(_wrap([_item()]), "p1", now=NOW)
    assert rows[0]["gmv"] is None and rows[0]["sales"] is None
    assert rows[0]["douyin_id"] == ""             # 真抖音号接口没给
    # 区间文案留在 extra_json 里，报表层自取
    extra = json.loads(rows[0]["extra_json"])
    assert extra["format_sales_amount"] == "1千+"
    assert extra["format_sales"] == "2.5千+"


def test_parse_aggregates_same_author_best_rank():
    """同一达人多条视频（列表已按销售额降序）⇒ 只留第一条，rank 取最高名次。"""
    items = [_item("芝士百宝箱", "4013663505615021", 33992, "2.5千+", "1千+"),
             _item("团团不吃香菜", "4013663505615999", 1469391, "2.5千+", "500+"),
             _item("团团不吃香菜", "4013663505615999", 1469391, "250+", "200+")]
    rows = pts.parse_top_talents(_wrap(items, count=78), "p1", now=NOW)
    assert len(rows) == 2
    tt = next(r for r in rows if r["nickname"] == "团团不吃香菜")
    assert tt["rank"] == 2                        # 聚合后名次，不是原始第 3 条
    assert tt["fans_count"] == 1469391
    extra = json.loads(tt["extra_json"])
    assert extra["format_sales"] == "2.5千+"      # 留的是卖得最好的那条


def test_parse_unknown_shape_returns_empty_not_fabricated():
    assert pts.parse_top_talents({"code": 0, "msg": "ok"}, "p1", now=NOW) == []
    assert pts.parse_top_talents(None, "p1", now=NOW) == []
    assert pts.parse_top_talents("garbage", "p1", now=NOW) == []
    # 空 content_list ≠ 失败：结构对但没数据 ⇒ 空列表（调用方自己判）
    assert pts.parse_top_talents(_wrap([]), "p1", now=NOW) == []


def test_parse_drops_identifierless_rows():
    """share_url 无达人 ID 且 author_name 空 ⇒ 丢弃——不凑数。"""
    bad = _item()
    bad["author_name"] = ""
    bad["share_url"] = ""
    rows = pts.parse_top_talents(_wrap([bad, _item()]), "p1", now=NOW)
    assert len(rows) == 1 and rows[0]["nickname"] == "芝士百宝箱"


def test_parse_no_share_url_falls_back_to_nickname():
    """接口没给 share_url ⇒ author_id 空串但行保留（nickname 在）。"""
    it = _item()
    it["share_url"] = ""
    rows = pts.parse_top_talents(_wrap([it]), "p1", now=NOW)
    assert rows[0]["author_id"] == "" and rows[0]["nickname"] == "芝士百宝箱"


# ─────────────── save_rows 幂等落库 ───────────────

def test_save_rows_idempotent(tmp_path):
    dbf = tmp_path / "main.db"
    conn = sqlite3.connect(str(dbf))
    captain.ensure_schema(conn)
    rows = pts.parse_top_talents(_wrap([_item()]), "p1", now=NOW)
    assert len(rows) == 1
    assert pts.save_rows(conn, rows) == 1
    assert pts.save_rows(conn, rows) == 1          # 重复采集覆盖不堆行
    n = conn.execute("SELECT COUNT(*) FROM platform_product_talent").fetchone()[0]
    assert n == 1
    gmv, fans = conn.execute(
        "SELECT gmv, fans_count FROM platform_product_talent").fetchone()
    assert gmv is None                             # 只丢不造：平台没给真值
    assert fans == 33992
    conn.close()


# ─────────────── collect_for_matched_products 周采集编排 ───────────────

def _mk_loop2_env(tmp_path, monkeypatch, cache, fetch_results):
    """造 cache + 空主库，mock fetch_top_talents 按 promotion_id 顺序返回。"""
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(cache, ensure_ascii=False))
    dbf = tmp_path / "loop2.db"
    conn = sqlite3.connect(str(dbf))
    captain.ensure_schema(conn)
    conn.close()
    it = iter(fetch_results)

    def _fake_fetch(promotion_id, *a, **kw):
        return next(it)
    monkeypatch.setattr(pts, "fetch_top_talents", _fake_fetch)
    # 减速 sleep 砍掉，测试跑快
    monkeypatch.setattr(pts.time, "sleep", lambda *a, **kw: None)
    attempts_path = tmp_path / "attempts.json"
    return dbf, cache_path, attempts_path


def test_loop2_week_gate_skips_when_done_this_week(tmp_path, monkeypatch):
    """周闸门：本周一（含）以来已采过的商品不再采；采够配额整体跳过。"""
    dbf, cache_path, att = _mk_loop2_env(
        tmp_path, monkeypatch,
        {"pid_a": {"promotion_id": "promo_a"}}, [])
    conn = sqlite3.connect(str(dbf))
    rows = pts.parse_top_talents(_wrap([_item()]), "pid_a", now=NOW)
    pts.save_rows(conn, rows)                       # stat_date = 今天 ≥ 本周一
    conn.close()
    called = []

    def _boom(*a, **kw):
        called.append(1)
        return [], "不该被调"
    monkeypatch.setattr(pts, "fetch_top_talents", _boom)
    out = pts.collect_for_matched_products(db_path=str(dbf),
                                           cache_path=str(cache_path))
    assert out["skipped"] is True and called == []


def test_loop2_collects_only_not_yet_done(tmp_path, monkeypatch):
    """本周未采的进 todo；mock 两行返回 ⇒ done=2 且落库。"""
    dbf, cache_path, att = _mk_loop2_env(
        tmp_path, monkeypatch,
        {"pid_a": {"promotion_id": "promo_a"},
         "pid_b": {"promotion_id": "promo_b"}},
        [(pts.parse_top_talents(_wrap([_item()]), "pid_a", now=NOW), ""),
         (pts.parse_top_talents(_wrap([_item("福宝", "3859746845373659",
                                             54905)]), "pid_b", now=NOW), "")])
    out = pts.collect_for_matched_products(db_path=str(dbf),
                                           cache_path=str(cache_path),
                                           attempts_path=str(att))
    assert out["done"] == 2 and out["errors"] == 0
    conn = sqlite3.connect(str(dbf))
    n = conn.execute("SELECT COUNT(*) FROM platform_product_talent").fetchone()[0]
    assert n == 2
    conn.close()


def test_loop2_single_failure_does_not_abort_others(tmp_path, monkeypatch):
    """单商品失败记 errors 继续下一个；无带货内容单独计数。"""
    dbf, cache_path, att = _mk_loop2_env(
        tmp_path, monkeypatch,
        {"pid_a": {"promotion_id": "promo_a"},
         "pid_b": {"promotion_id": "promo_b"},
         "pid_c": {"promotion_id": "promo_c"}},
        [([], "worker 采集失败：异常"),            # pid_a 失败（通用 err）
         ([], "采集超时：页面 XHR 未返回带货内容（该商品可能无带货内容，"
              "或页面加载失败）"),               # pid_b 无内容
         (pts.parse_top_talents(_wrap([_item()]), "pid_c", now=NOW), "")])
    out = pts.collect_for_matched_products(db_path=str(dbf),
                                           cache_path=str(cache_path),
                                           attempts_path=str(att))
    assert out["errors"] == 1 and out["no_content"] == 1 and out["done"] == 1
    conn = sqlite3.connect(str(dbf))
    n = conn.execute("SELECT COUNT(*) FROM platform_product_talent").fetchone()[0]
    assert n == 1                                    # 只有 pid_c 落库
    conn.close()


def test_loop2_attempted_today_skipped_until_tomorrow(tmp_path, monkeypatch):
    """无内容商品今天尝试过就进 attempts ⇒ 当天不再重复采（配额不被吃掉）；
    明天（日期变）会重新进 todo。"""
    dbf, cache_path, att = _mk_loop2_env(
        tmp_path, monkeypatch,
        {"pid_a": {"promotion_id": "promo_a"}},
        [([], "采集超时：页面 XHR 未返回带货内容（该商品可能无带货内容，"
              "或页面加载失败）")])
    out = pts.collect_for_matched_products(db_path=str(dbf),
                                           cache_path=str(cache_path),
                                           attempts_path=str(att))
    assert out["no_content"] == 1
    assert json.loads(att.read_text())              # attempts 已写
    called = []

    def _boom(*a, **kw):
        called.append(1)
        return [], "不该被调"
    monkeypatch.setattr(pts, "fetch_top_talents", _boom)
    out2 = pts.collect_for_matched_products(db_path=str(dbf),
                                            cache_path=str(cache_path),
                                            attempts_path=str(att))
    assert out2["skipped"] is True and called == []   # 今天不再尝试


# ─────────────── backfill_sold 配对回填 ───────────────

def _mk_main_with_talents(tmp_path, monkeypatch, bot_db, ptt_rows):
    """主库：products/talents 最小表 + platform_product_talent（ensure_schema）。"""
    main = tmp_path / "sb.db"
    conn = sqlite3.connect(str(main))
    captain.ensure_schema(conn)
    conn.execute("CREATE TABLE products (id TEXT PRIMARY KEY, name TEXT,"
                 " platform_product_id TEXT)")
    conn.execute("CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT,"
                 " douyin_id TEXT, platform_talent_id TEXT DEFAULT '',"
                 " douyin_uid TEXT DEFAULT '')")
    conn.execute("INSERT INTO products VALUES ('prod_a','面霜A','plat_1')")
    conn.execute("INSERT INTO products VALUES ('prod_c','面霜C','')")
    conn.execute("INSERT INTO talents VALUES ('tal_1','洁洁','dy_1','Wgg292','')")
    conn.executemany(
        "INSERT INTO platform_product_talent (id, stat_date,"
        " platform_product_id, rank, author_id, douyin_id, nickname,"
        " gmv, sales, extra_json, captured_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ptt_rows)
    conn.commit()
    conn.close()
    monkeypatch.setattr(reco_log, "SOLOBRAVE_DB", main)
    monkeypatch.setattr(reco_log.db.config, "DB_PATH", bot_db)
    reco_log.db.init_db()
    return main


def test_backfill_sold_matches_via_author_id(tmp_path, monkeypatch):
    bot_db = tmp_path / "daren_bot.db"
    _mk_main_with_talents(tmp_path, monkeypatch, bot_db, [
        ("ppt_1", "2026-11-07", "plat_1", 1, "Wgg292", "", "洁洁",
         12500.0, 300, "{}", NOW)])
    reco_log.log_recommendations("tk1", "dy_1", "",
                                 _RECOS[:1], now=NOW)   # product_id=prod_a
    out = reco_log.backfill_sold(now=NOW)
    assert out == {"matched": 1, "gmv_filled": 1}
    r = _rows(bot_db)[0]
    assert r["sold_gmv"] == 12500 and r["sold_at"] == NOW
    assert r["sold_source"] == "daren_product_detail"
    # 幂等：重跑不再改（sold_source 已非空）
    assert reco_log.backfill_sold(now=NOW) == {"matched": 0, "gmv_filled": 0}


def test_backfill_sold_matches_via_douyin_id(tmp_path, monkeypatch):
    bot_db = tmp_path / "daren_bot.db"
    _mk_main_with_talents(tmp_path, monkeypatch, bot_db, [
        ("ppt_2", "2026-11-07", "plat_1", 3, "", "dy_1", "洁洁小号",
         None, 5, "{}", NOW)])
    reco_log.log_recommendations("tk1", "dy_1", "", _RECOS[:1], now=NOW)
    out = reco_log.backfill_sold(now=NOW)
    assert out == {"matched": 1, "gmv_filled": 0}   # 命中但平台没给金额
    r = _rows(bot_db)[0]
    assert r["sold_gmv"] is None and r["sold_source"] == "daren_product_detail"
    assert r["sold_at"] == NOW


def test_backfill_sold_no_false_match_on_other_talent(tmp_path, monkeypatch):
    """榜单上是别人的达人 ⇒ 不配对（不在 top N ≠ 没出单，保持 NULL）。"""
    bot_db = tmp_path / "daren_bot.db"
    _mk_main_with_talents(tmp_path, monkeypatch, bot_db, [
        ("ppt_3", "2026-11-07", "plat_1", 1, "OTHER99", "", "别人",
         99999.0, 9, "{}", NOW)])
    reco_log.log_recommendations("tk1", "dy_1", "", _RECOS[:1], now=NOW)
    assert reco_log.backfill_sold(now=NOW) == {"matched": 0, "gmv_filled": 0}
    r = _rows(bot_db)[0]
    assert r["sold_gmv"] is None and r["sold_source"] == ""


def test_backfill_sold_unresolvable_product_skipped(tmp_path, monkeypatch):
    """product_id 解析不出平台商品ID（prod_c 未建档平台ID）⇒ 跳过不比猜。"""
    bot_db = tmp_path / "daren_bot.db"
    _mk_main_with_talents(tmp_path, monkeypatch, bot_db, [
        ("ppt_4", "2026-11-07", "plat_x", 1, "Wgg292", "", "洁洁",
         1.0, 1, "{}", NOW)])
    reco = [{"product_id": "prod_c", "name": "面霜C", "stars": 3,
             "reasons": ["x"]}]
    reco_log.log_recommendations("tk1", "dy_1", "", reco, now=NOW)
    assert reco_log.backfill_sold(now=NOW) == {"matched": 0, "gmv_filled": 0}


def test_backfill_sold_prefers_latest_capture(tmp_path, monkeypatch):
    bot_db = tmp_path / "daren_bot.db"
    _mk_main_with_talents(tmp_path, monkeypatch, bot_db, [
        ("ppt_5", "2026-11-06", "plat_1", 1, "Wgg292", "", "洁洁",
         100.0, 1, "{}", NOW - 86400),
        ("ppt_6", "2026-11-07", "plat_1", 1, "Wgg292", "", "洁洁",
         200.0, 2, "{}", NOW)])
    reco_log.log_recommendations("tk1", "dy_1", "", _RECOS[:1], now=NOW)
    reco_log.backfill_sold(now=NOW)
    assert _rows(bot_db)[0]["sold_gmv"] == 200
