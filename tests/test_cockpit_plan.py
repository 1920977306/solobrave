"""排品表 H5（daren_cockpit/server.py 的 load_plan / load_plan_roster）测试。

锁住三件事：
1. **排品池 = 我们自己系统里的合作单**（`deals`），不是达人端的数据。
2. **按达人列，不按租户列** —— 运营后台建的单，达人不一定绑了飞书租户。
3. **达人端只做「按商品ID搜商品」** —— 产品代码里不许再出现选品车采集路径
   （2026-10-24 老大纠正：「达人端只是一个搜索商品的功能才会用到，其他的不用碰」）。
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


DDL = """
CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT,
    category_distribution TEXT, price_distribution TEXT,
    average_price REAL, video_avg_price TEXT,
    total_products INTEGER, total_gmv REAL, created_at INTEGER,
    platform_talent_id TEXT, douyin_uid TEXT, platform_nickname TEXT
);
CREATE TABLE products (
    id TEXT PRIMARY KEY, name TEXT, price REAL, price_range TEXT,
    category TEXT, commission_rates TEXT, tags TEXT,
    shop_score REAL, good_ratio REAL, video_sales_7d INTEGER,
    sales_7d INTEGER, board_rank INTEGER, sale_axis_json TEXT,
    platform_product_id TEXT, status TEXT
);
CREATE TABLE deals (
    id TEXT PRIMARY KEY, talent_id TEXT, product_id TEXT, product_name TEXT,
    deal_type TEXT, commission_rate REAL, status TEXT, scheduled_at INTEGER,
    priority REAL, plan_slot INTEGER, plan_reason TEXT,
    deal_source TEXT, remind_state TEXT, created_at INTEGER, updated_at INTEGER
);
"""


@pytest.fixture()
def dbfile(tmp_path, monkeypatch):
    path = tmp_path / "sb.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,category_distribution,"
        "price_distribution,average_price,video_avg_price,total_products,"
        "total_gmv,created_at,platform_nickname) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        ("tal_x", "七月姐姐", "dy_888",
         json.dumps({"个护家清": 63, "美妆": 20}),
         json.dumps({"0~25": 100, "50~100": 400, "100~200": 600}),
         65.0, "¥65", 190, 500000, 1, "七月"))
    conn.execute(
        "INSERT INTO products (id,name,price,category,commission_rates,"
        "shop_score,good_ratio,sale_axis_json,platform_product_id,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("prod_a", "面霜A", 99, "个护家清/面霜",
         json.dumps({"default": 20}), 92, 95,
         json.dumps([{"date": f"202609{i:02d}", "qty": 30} for i in range(1, 15)]),
         "3668788850777522321", "active"))
    conn.execute(
        "INSERT INTO products (id,name,price,category,commission_rates,"
        "shop_score,good_ratio,sale_axis_json,platform_product_id,status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("prod_b", "面霜B", 199, "个护家清/面霜",
         json.dumps({"default": 18}), 90, 91,
         json.dumps([{"date": f"202609{i:02d}", "qty": 10} for i in range(1, 15)]),
         "3668788850777522322", "active"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(server, "SOLOBRAVE_DB", path)
    return path


def _add_deal(path, deal_id, talent_id, product_id, name, source="运营建单",
              status="approved", scheduled_at=0):
    conn = sqlite3.connect(str(path))
    conn.execute(
        "INSERT INTO deals (id,talent_id,product_id,product_name,deal_type,"
        "commission_rate,status,scheduled_at,deal_source,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (deal_id, talent_id, product_id, name, "video", 20, status,
         scheduled_at, source, 1, 1))
    conn.commit()
    conn.close()


# ─────────────── 排品池 = 我们的合作单 ───────────────

def test_load_plan_uses_our_own_deals(dbfile):
    """合作单进池 ⇒ 排品表排得出来。"""
    _add_deal(dbfile, "d1", "tal_x", "prod_a", "面霜A")
    _add_deal(dbfile, "d2", "tal_x", "prod_b", "面霜B")
    p = server.load_plan_for_talent("tal_x")
    assert p["pool"] == 2, p
    names = [i["product_name"] for s in p["slots"] for i in s["items"]]
    assert "面霜A" in names and "面霜B" in names, names
    assert p["talent_name"] == "七月", p["talent_name"]


def test_load_plan_empty_when_no_deal(dbfile):
    p = server.load_plan_for_talent("tal_x")
    assert p["pool"] == 0
    assert not any(s["items"] for s in p["slots"])


def test_load_plan_ignores_non_pool_status(dbfile):
    """`pending` 不算「接了的品」⇒ 不进排品池。"""
    _add_deal(dbfile, "d1", "tal_x", "prod_a", "面霜A", status="pending")
    p = server.load_plan_for_talent("tal_x")
    assert p["pool"] == 0, p


def test_load_plan_hides_past_days(dbfile):
    """排期在过去 ⇒ 排品表不展示（历史是历史，不是待办）。"""
    import datetime
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    ts = int(datetime.datetime.combine(yesterday, datetime.time.min).timestamp())
    _add_deal(dbfile, "d1", "tal_x", "prod_a", "面霜A", scheduled_at=ts)
    p = server.load_plan_for_talent("tal_x")
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    assert all(s["date"] >= today for s in p["slots"]), p["slots"]


# ─────────────── 按达人列，不按租户列 ───────────────

def test_roster_lists_by_talent_not_tenant(dbfile):
    """★ 运营后台建单，达人**不一定**绑了飞书租户 ⇒ 必须按达人列。

    实测：七月姐姐有 5 单但没租户；按租户列就看不到她 ⇒ 排品表 H5 里选不到。
    """
    conn = sqlite3.connect(str(dbfile))
    conn.execute(
        "INSERT INTO talents (id,name,douyin_id,created_at) VALUES (?,?,?,?)",
        ("tal_y", "另一个达人", "", 1))
    conn.commit()
    conn.close()
    _add_deal(dbfile, "d1", "tal_x", "prod_a", "面霜A")
    _add_deal(dbfile, "d2", "tal_y", "prod_b", "面霜B")
    roster = server.load_plan_roster()
    ids = {r["id"] for r in roster}
    assert ids == {"tal_x", "tal_y"}, roster
    assert all(r["pool"] == 1 for r in roster), roster


# ─────────────── 达人端只做「按商品ID搜商品」 ───────────────

def test_product_code_does_not_collect_darenhou_cart():
    """★ 边界锁点：产品代码里**不许**再有选品车采集路径。

    2026-10-24 老大纠正：「达人端只是一个搜索商品的功能才会用到，其他的不要碰」。
    采集器已下线，这里防它被重新接回去。
    """
    banned = ("cart_collector", "sync_cart_pool", "selection_cart_pc_api",
              "m_get_card", "m_get_cart_sum")
    scanned = []
    for p in (ROOT / "daren_bot").rglob("*.py"):
        scanned.append(p)
    for p in (ROOT / "daren_cockpit").rglob("*.py"):
        scanned.append(p)
    hits = []
    for p in scanned:
        text = p.read_text(encoding="utf-8", errors="ignore")
        # 注释里提到「已下线/不要碰」不算命中
        for line in text.splitlines():
            code = line.split("#")[0]
            for b in banned:
                if b in code:
                    hits.append(f"{p.relative_to(ROOT)}: {line.strip()[:90]}")
    assert not hits, "达人端采集路径复活了：\n" + "\n".join(hits)


def test_buyin_worker_only_keeps_search_capability():
    """达人端浏览器保留「搜商品 / 抓该商品接口」，但没有选品车入口。"""
    text = (ROOT / "buyin_worker.py").read_text(encoding="utf-8")
    assert "/daren-search" in text
    assert "selection_cart_pc_api" not in text, \
        "buyin_worker 不该再碰选品车接口"
