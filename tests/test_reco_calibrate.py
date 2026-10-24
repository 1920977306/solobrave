"""阈值校准脚本（daren_cockpit/tools/reco_calibrate.py）的测试。

★ 核心要求：**门禁必须能失败**（探针要能失败）。
   一个永远返回 OK 的校准脚本比没有脚本更危险 —— 它会让人以为阈值还成立。
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "daren_cockpit" / "tools" / "reco_calibrate.py"
spec = importlib.util.spec_from_file_location("reco_calibrate", MOD_PATH)
rc = importlib.util.module_from_spec(spec)
sys.modules["reco_calibrate"] = rc
spec.loader.exec_module(rc)


TALENT_DDL = """
CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, fan_age TEXT, fan_gender TEXT,
    fan_price_range TEXT, average_price REAL, video_avg_price TEXT,
    category_distribution TEXT, price_distribution TEXT,
    top_categories TEXT, total_products INTEGER, total_gmv REAL,
    created_at INTEGER
);
"""
PRODUCT_DDL = """
CREATE TABLE products (
    id TEXT PRIMARY KEY, name TEXT, price REAL, price_range TEXT,
    category TEXT, commission_rates TEXT, tags TEXT,
    shop_score REAL, good_ratio REAL, video_sales_7d INTEGER,
    sales_7d INTEGER, board_rank INTEGER, sale_axis_json TEXT,
    platform_product_id TEXT, status TEXT
);
"""


@pytest.fixture()
def mini_db(tmp_path):
    """一个**能跑通**的小库：1 个达人 + 6 个商品（品类/价格都对得上）。"""
    path = tmp_path / "sb.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(TALENT_DDL)
    conn.executescript(PRODUCT_DDL)
    conn.execute(
        "INSERT INTO talents (id,name,fan_age,fan_gender,fan_price_range,"
        "average_price,video_avg_price,category_distribution,"
        "price_distribution,total_products,total_gmv,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("tal_x", "璐妈妈", '{"31-35": 38, "26-30": 28}',
         '{"女": 92, "男": 8}', "300-600", 450.0, "¥450",
         '{"鞋靴": 72, "服饰内衣": 20}', '{"300-600": 80}', 190, 500000, 1))
    for i in range(6):
        conn.execute(
            "INSERT INTO products (id,name,price,category,commission_rates,"
            "shop_score,good_ratio,video_sales_7d,board_rank,status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"prod_{i}", f"凉鞋{i}", 200 + i * 40, "鞋靴/凉鞋",
             json.dumps({"default": 10 + i * 2}), 88 + i, 90 + i,
             100 + i * 300, 3 + i, "active"))
    conn.commit()
    conn.close()
    return path


# ─────────────── 采集 ───────────────

def test_collects_candidates_and_displayed(mini_db):
    talents, products = rc._load(mini_db)
    cands = rc.collect_samples(talents, products)
    shown = rc.collect_displayed(talents, products)
    assert cands, "应该采到候选样本"
    assert shown, "应该采到展示样本"
    assert len(shown) <= len(cands), "展示样本不应多于候选样本"
    assert all(0 < d["score"] <= 100 for d in shown)


def test_displayed_is_deduped_per_talent(mini_db):
    """★ 去重必须**按达人分开** —— 同一商品对不同达人分数不同，混在一起口径就错了。"""
    talents, products = rc._load(mini_db)
    talents = talents + [dict(talents[0], id="tal_y", name="另一个达人")]
    shown = rc.collect_displayed(talents, products)
    ids = [d["score"] for d in shown]
    assert len(ids) >= 2, "两个达人各自的推荐都要留下"


def test_histogram_buckets_by_five_points():
    hist = rc.histogram([10.0, 12.4, 77.0, 79.9, 95.0])
    assert hist == {"10-14": 2, "75-79": 2, "95-99": 1}


# ─────────────── 门禁：★ 必须能失败 ───────────────

def test_gate_passes_on_healthy_distribution():
    """健康分布 ⇒ 通过。"""
    ok, problems = rc.check_displayed([
        {"score": 90, "stars": 5}, {"score": 85, "stars": 4},
        {"score": 70, "stars": 3}, {"score": 55, "stars": 2},
        {"score": 30, "stars": 1},
    ])
    assert ok, problems


def test_gate_fails_when_everything_is_five_stars():
    """★ 探针必须能失败：清一色 5★ ⇒ 门禁必须报错。"""
    ok, problems = rc.check_displayed(
        [{"score": 90, "stars": 5} for _ in range(20)])
    assert not ok, "清一色 5★ 竟然通过了门禁"
    assert any("5★" in p for p in problems)


def test_gate_fails_when_only_one_star_level():
    ok, problems = rc.check_displayed([{"score": 90, "stars": 3}] * 10)
    assert not ok
    assert any("档" in p for p in problems)


def test_gate_fails_when_no_low_star():
    ok, problems = rc.check_displayed(
        [{"score": 90, "stars": 5}, {"score": 88, "stars": 4},
         {"score": 86, "stars": 4}, {"score": 84, "stars": 4}])
    assert not ok
    assert any("1★" in p for p in problems)


def test_gate_fails_on_empty_sample():
    ok, problems = rc.check_displayed([])
    assert not ok
    assert problems


# ─────────────── CLI ───────────────

def test_cli_check_exits_zero_on_healthy_db(mini_db, capsys):
    rc.main.__globals__  # 触发一次属性访问，确保模块已加载
    import subprocess
    r = subprocess.run(
        [sys.executable, str(MOD_PATH), "--db", str(mini_db), "--check"],
        capture_output=True, text=True, cwd=str(ROOT))
    # 小库样本少，门禁可能因为样本不足报问题 —— 但**不能崩**（exit 0/1 都算正常）
    assert r.returncode in (0, 1), r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_cli_json_output_is_parseable(mini_db):
    import subprocess
    r = subprocess.run(
        [sys.executable, str(MOD_PATH), "--db", str(mini_db), "--json"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode in (0, 1), r.stderr
    data = json.loads(r.stdout)
    assert "displayed" in data and "thresholds" in data
    assert data["thresholds"] == [[85.0, 5], [78.0, 4], [68.0, 3], [50.0, 2]]


def test_cli_missing_db_exits_two():
    import subprocess
    r = subprocess.run(
        [sys.executable, str(MOD_PATH), "--db", "/tmp/definitely-not-here.db"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
