"""排品提醒（daren_bot/plan_notify.py）的测试。

用**临时库**（不碰生产 data/）跑完整链路：
登记接品 → 幂等 → 生成排期 → 生成今日提醒 → 提醒幂等。

★ 铁律锁点：
- 登记接品**幂等**（同达人同品不重复进池，否则坑位会被重复品占满）
- 排期/提醒**不写错数据**（只写达人明确登记过的品）
- 提醒**幂等**（同一天同一品只提醒一次）
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

plan_notify = importlib.import_module("daren_bot.plan_notify")


DDL = """
CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT,
    category_distribution TEXT, price_distribution TEXT,
    average_price REAL, video_avg_price TEXT,
    total_products INTEGER, total_gmv REAL, created_at INTEGER
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
    actual_gmv REAL, actual_roi REAL, actual_units INTEGER, result_note TEXT,
    predicted_conclusion TEXT, predicted_event_id TEXT, verification TEXT,
    created_by TEXT, created_at INTEGER, updated_at INTEGER,
    win_loss_category TEXT, key_moment TEXT, decision_maker_feedback TEXT,
    priority REAL, plan_slot INTEGER, plan_reason TEXT,
    deal_source TEXT, remind_state TEXT
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
        "total_gmv,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("tal_x", "七月姐姐", "dy_888",
         json.dumps({"个护家清": 63, "美妆": 20}),
         json.dumps({"0~25": 100, "50~100": 400, "100~200": 600}),
         65.0, "¥65", 190, 500000, 1))
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
         json.dumps({"投放期": 25, "常规活动期": 5, "其他": 5}),
         90, 91,
         json.dumps([{"date": f"202609{i:02d}", "qty": 10} for i in range(1, 15)]),
         "3668788850777522322", "active"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(plan_notify, "SOLOBRAVE_DB", path)
    return path


# ─────────────── 登记接品 ───────────────

def test_register_deal_by_product_id(dbfile):
    ok, msg = plan_notify.register_deal("dy_888", "3668788850777522321")
    assert ok is True
    assert "面霜A" in msg
    conn = sqlite3.connect(str(dbfile))
    row = conn.execute("SELECT * FROM deals").fetchone()
    cols = [c[0] for c in conn.execute("SELECT * FROM deals LIMIT 0").description]
    d = dict(zip(cols, row))
    conn.close()
    assert d["status"] == "approved"
    assert d["product_name"] == "面霜A"
    assert d["commission_rate"] == 20
    assert d["deal_source"] == "达人自选"


def test_register_deal_is_idempotent(dbfile):
    ok1, _ = plan_notify.register_deal("dy_888", "3668788850777522321")
    ok2, msg2 = plan_notify.register_deal("dy_888", "3668788850777522321")
    assert ok1 is True
    assert ok2 is False
    assert "已经登记" in msg2
    conn = sqlite3.connect(str(dbfile))
    n = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
    conn.close()
    assert n == 1, "同一个品不能重复进池（否则坑位被重复品占满）"


def test_register_deal_unknown_product(dbfile):
    ok, msg = plan_notify.register_deal("dy_888", "9999999999999999999")
    assert ok is False
    assert "不在我们库里" in msg


def test_register_deal_without_talent(dbfile):
    ok, msg = plan_notify.register_deal("dy_unknown", "3668788850777522321")
    assert ok is False
    assert "抖音号" in msg


# ─────────────── 排期 ───────────────

def test_plan_text_lists_deals(dbfile):
    plan_notify.register_deal("dy_888", "3668788850777522321")
    plan_notify.register_deal("dy_888", "prod_b")
    text = plan_notify.plan_text("dy_888")
    assert "七月姐姐" in text
    assert "排品表" in text
    assert ("面霜A" in text) or ("面霜B" in text)


def test_plan_text_when_no_deals(dbfile):
    text = plan_notify.plan_text("dy_888")
    assert "还没有登记" in text
    assert "接了" in text          # 告诉达人怎么用


def test_plan_text_when_no_talent(dbfile):
    text = plan_notify.plan_text("dy_nobody")
    assert "抖音号" in text


# ─────────────── 今日提醒 ───────────────

def test_build_reminder_includes_reasons(dbfile):
    plan_notify.register_deal("dy_888", "3668788850777522321")
    text, todo = plan_notify.build_reminder("dy_888")
    assert "今天推" in text
    assert "面霜A" in text
    assert "佣金" in text
    assert todo, "应该有待标记的 deal"


def test_reminder_is_idempotent(dbfile):
    """★ 幂等要测**真实机制**（推送层），不是 build 层。

    幂等落在 DB 的 `remind_state` 上：
    第一次 scan_and_push 推出去并标记 → 第二次不再推。
    （只测 `build_reminder` 两次调用没有意义 —— 它不写标记，
      标记发生在**推送成功之后**，先标记再推会吞掉提醒。）
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []

    class _Sender:
        def send_text(self, chat_id, text):
            sent.append((chat_id, text))

    monkey = plan_notify
    orig_gray = monkey._gray_map
    orig_chat = monkey.db.get_latest_chat_id
    monkey._gray_map = lambda: {"daren_boss": "dy_888"}
    monkey.db.get_latest_chat_id = lambda tk: "oc_test"
    try:
        st1 = monkey.scan_and_push(lambda tk: _Sender())
        assert st1["pushed"] == 1, st1
        assert len(sent) == 1
        st2 = monkey.scan_and_push(lambda tk: _Sender())
        assert st2["pushed"] == 0, "同一天不能重复推送"
        assert len(sent) == 1, "同一天同一品只能提醒一次"
    finally:
        monkey._gray_map = orig_gray
        monkey.db.get_latest_chat_id = orig_chat


def test_reminder_idempotency_survives_restart(dbfile):
    """★ 机器人当天重启也不能重推（幂等不能只靠进程内存）。

    `PlanScheduler._last_day` 挂在内存里，重启就清空；
    所以幂等必须落在 DB 的 `remind_state` 上。
    """
    plan_notify.register_deal("dy_888", "3668788850777522321")
    sent = []

    class _Sender:
        def send_text(self, chat_id, text):
            sent.append((chat_id, text))

    orig_gray = plan_notify._gray_map
    orig_chat = plan_notify.db.get_latest_chat_id
    plan_notify._gray_map = lambda: {"daren_boss": "dy_888"}
    plan_notify.db.get_latest_chat_id = lambda tk: "oc_test"
    try:
        plan_notify.scan_and_push(lambda tk: _Sender())
        assert len(sent) == 1
        # 「重启」= 新进程，内存状态全丢
        plan_notify.scan_and_push(lambda tk: _Sender())
        assert len(sent) == 1, "重启后不能把今天的提醒重推一遍"
    finally:
        plan_notify._gray_map = orig_gray
        plan_notify.db.get_latest_chat_id = orig_chat


def test_register_deal_reads_phase_commission(dbfile):
    """★ 分档佣金结构也要读出来（收益维度不能被静默丢掉）。

    平台佣金有标量（{"default": 20}）和分档（{"投放期": 25, ...}）两种形态，
    只认标量会让商品的收益维度悄悄消失 —— 评分口径就和真值对不上了。
    """
    ok, msg = plan_notify.register_deal("dy_888", "prod_b")
    assert ok is True
    assert "25" in msg, msg
    conn = sqlite3.connect(str(dbfile))
    rate = conn.execute("SELECT commission_rate FROM deals").fetchone()[0]
    conn.close()
    assert rate == 25


def test_build_reminder_empty_when_no_plan(dbfile):
    text, todo = plan_notify.build_reminder("dy_888")
    assert text == ""
    assert todo == []
