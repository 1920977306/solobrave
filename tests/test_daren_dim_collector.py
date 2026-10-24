"""达人端商品维度补齐器（daren_cockpit/tools/daren_dim_collector.py）的纯函数测试。

锁住三件事：
1. **不造数**：平台没给的维度写 **NULL**，绝不写 0 冒充
   （0 会被评分器当「该项很差」扣分，NULL 才是「未知」）。
2. **口径不混**：两个「近7日销量」分列
   （sales_7d = sale_axis 自算的总销量；video_sales_7d = 榜单短视频口径）。
3. **榜单排名双来源**：优先用平台自带的 recommend_reason（覆盖榜外商品），
   其次用类目榜命中值。
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "daren_dim_collector.py")
spec = importlib.util.spec_from_file_location("daren_dim_collector", MOD_PATH)
dc = importlib.util.module_from_spec(spec)
sys.modules["daren_dim_collector"] = dc
spec.loader.exec_module(dc)


# ─────────────── 近 N 天销量 ───────────────

def test_sum_last_days_takes_last_n_sorted_by_date():
    axis = [{"date": "20260930", "qty": 10}, {"date": "20260924", "qty": 1},
            {"date": "20261001", "qty": 100}]
    assert dc.sum_last_days(axis, 2) == 110     # 20260930 + 20261001
    assert dc.sum_last_days(axis, 3) == 111     # + 20260924


def test_sum_last_days_empty_is_zero():
    assert dc.sum_last_days([], 7) == 0
    assert dc.sum_last_days(None, 7) == 0
    assert dc.sum_last_days([{"date": "", "qty": 5}], 7) == 0


def test_sum_last_days_ignores_broken_points():
    axis = [{"date": "20261001", "qty": "abc"}, {"date": "20261002", "qty": 5}]
    assert dc.sum_last_days(axis, 7) == 5


# ─────────────── 榜单解析 ───────────────

def _board_payload():
    return {
        "code": 0, "data": {"summary_promotions": [
            {"product_id": "111", "custom_model": {"selection_square_model": {
                "board_data": {"rank": 3,
                               "board_sale": {"content": "近7日热销", "sale_num": 1234},
                               "pay_cnt_ratio": ""}}}},
            {"product_id": "222", "custom_model": {"selection_square_model": {
                "board_data": {"rank": 1,
                               "board_sale": {"content": "近7日热销", "sale_num": 9999}}}}},
        ]},
    }


def test_parse_board():
    out = dc.parse_board(_board_payload())
    assert out["111"]["rank"] == 3
    assert out["111"]["sale_num"] == 1234
    assert out["222"]["rank"] == 1
    assert out["111"]["content"] == "近7日热销"


def test_parse_board_empty_payload():
    assert dc.parse_board({}) == {}
    assert dc.parse_board({"data": {}}) == {}


# ─────────────── 组装维度 ───────────────

def _daren(**over):
    d = {
        "shop_score": "92", "good_ratio": "94.7%",
        "sale_axis": [{"date": f"2026100{i}", "qty": i} for i in range(1, 8)],
        "category_first_id": "16", "category_second_id": "1000000961",
        "category_third_id": "1000007608",
        "recommend_reason": "露趾凉鞋橱窗带货常销榜·第5名",
        "board": {"rank": "5", "rank_type": "sales", "date_type": "90d",
                  "genre_type": "window", "leaderboard_name": "露趾凉鞋榜单"},
    }
    d.update(over)
    return d


def test_build_dims_reads_truth():
    d = dc.build_dims(_daren(), None)
    assert d["shop_score"] == 92.0
    assert d["good_ratio"] == 94.7
    assert d["sales_7d"] == 28          # 1+2+…+7
    assert d["board_rank"] == 5
    assert d["board_rank_type"] == "sales"
    assert d["board_name"] == "露趾凉鞋榜单"
    assert d["board_reason"].startswith("露趾凉鞋")
    assert d["category_ids"] == "16,1000000961,1000007608"


def test_build_dims_missing_is_null_not_zero():
    """★ 关键：不造数。平台没给 ⇒ None（NULL），不是 0。"""
    d = dc.build_dims({"shop_score": "", "good_ratio": None, "sale_axis": []}, None)
    assert d["shop_score"] is None
    assert d["good_ratio"] is None
    assert d["sales_7d"] is None
    assert d["board_rank"] is None
    assert d["video_sales_7d"] is None
    assert d["sale_axis_json"] is None


def test_build_dims_board_hit_fills_rank_and_video_sales():
    d = dc.build_dims(_daren(board={}, recommend_reason=""),
                      {"rank": 7, "sale_num": 8888})
    assert d["board_rank"] == 7
    assert d["video_sales_7d"] == 8888
    # 口径要标清楚：榜单口径 = 近7日 × 短视频
    assert d["board_date_type"] == dc.BOARD_DATE_TYPE
    assert d["board_genre_type"] == dc.BOARD_GENRE_TYPE


def test_build_dims_prefers_platform_reason_rank_over_board_hit():
    """平台自带的 recommend_reason 优先（它覆盖榜外商品）。"""
    d = dc.build_dims(_daren(), {"rank": 999, "sale_num": 1})
    assert d["board_rank"] == 5
    assert d["video_sales_7d"] == 1      # 短视频销量仍取榜单命中值


def test_build_dims_empty_daren_is_all_null():
    d = dc.build_dims({}, None)
    assert all(v is None for k, v in d.items()
               if k in ("shop_score", "good_ratio", "sales_7d",
                        "video_sales_7d", "board_rank"))


# ─────────────── schema ───────────────

def test_ensure_schema_is_idempotent(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE products (id TEXT, name TEXT)")
    conn.commit()
    added = dc.ensure_schema(conn)
    assert "shop_score" in added
    assert "video_sales_7d" in added
    assert dc.ensure_schema(conn) == []          # 再跑一次不加列
    cols = {r[1] for r in conn.execute("PRAGMA table_info(products)")}
    assert {"shop_score", "good_ratio", "sales_7d", "video_sales_7d",
            "board_rank", "daren_synced_at"} <= cols
    conn.close()
