"""团长端出单数据采集器（daren_cockpit/tools/captain_stats.py）的纯函数测试。

重点锁三件事：
1. 金额单位换算：平台给「分」，落库给「元」（与达人端 material_list 同一口径）。
2. 字段映射：overview 的 business_info / order_info 都要摊平成一行，别丢字段。
3. 达人行必须有抖音号（对拍钥匙），没有就丢（不造数据）。
"""
import importlib.util
from pathlib import Path

import pytest

MOD_PATH = Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools" / "captain_stats.py"
spec = importlib.util.spec_from_file_location("captain_stats", MOD_PATH)
cs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cs)


# ───────────── 金额单位 ─────────────

@pytest.mark.parametrize("raw,expect", [
    ("180504000", 1805040.00),     # 180 万
    ("98516485", 985164.85),
    ("0", 0.0),
    ("", 0.0),
    (None, 0.0),
    ("681999122", 6819991.22),
])
def test_cents_to_yuan(raw, expect):
    assert cs.cents_to_yuan(raw) == expect


def test_cents_to_yuan_bad_value_is_zero():
    assert cs.cents_to_yuan("不是数字") == 0.0


@pytest.mark.parametrize("raw,expect", [(4325, 4325), ("4325", 4325), ("", 0), (None, 0)])
def test_to_int(raw, expect):
    assert cs.to_int(raw) == expect


# ───────────── 时间区间 ─────────────

def test_period_range_days():
    from datetime import datetime
    end = datetime(2026, 10, 21, 23, 59, 59)
    start, e = cs.period_range(30, end)
    assert start == "2026/09/21 23:59:59"
    assert e == "2026/10/21 23:59:59"


def test_period_range_format_matches_platform():
    start, end = cs.period_range(7)
    for s in (start, end):
        assert len(s) == 19
        assert s[4] == "/" and s[7] == "/" and s[10] == " " and s[13] == ":"


# ───────────── overview 摊平 ─────────────

OVERVIEW_FIXTURE = {
    "business_info": {"data_details": [
        {"title": "支付GMV", "field_key": "pay_gmv", "value": "681999122"},
        {"title": "结算GMV", "field_key": "settle_gmv", "value": "32519395"},
        {"title": "实际服务费收入", "field_key": "real_income_service_fee", "value": "1759404"},
        {"title": "支付订单量", "field_key": "order_pay_num", "value": "17603"},
        {"title": "平均服务费率", "field_key": "avg_service_rate", "value": "6.00"},
    ]},
    "order_info": {"data_details": [
        {"title": "成交达人", "field_key": "order_daren_count", "value": "78"},
        {"title": "出单商品", "field_key": "order_product_count", "value": "46"},
        {"title": "出单商家", "field_key": "order_shop_count", "value": "9"},
        {"title": "平均佣金率", "field_key": "avg_commission_rate", "value": "9.29"},
    ]},
    "data_cutoff_time": "2026-10-21 23:59:59",
}


def test_overview_row_maps_all_fields():
    row = cs.overview_row(OVERVIEW_FIXTURE)
    assert row["pay_gmv"] == 6819991.22
    assert row["settle_gmv"] == 325193.95
    assert row["real_income_service_fee"] == 17594.04
    assert row["order_pay_num"] == 17603
    assert row["order_daren_count"] == 78
    assert row["order_product_count"] == 46
    assert row["order_shop_count"] == 9
    assert row["data_cutoff_time"] == "2026-10-21 23:59:59"


def test_overview_row_ignores_unknown_field():
    data = {"business_info": {"data_details": [
        {"title": "未来字段", "field_key": "brand_new_metric", "value": "1"}]}}
    row = cs.overview_row(data)
    assert "brand_new_metric" not in row


def test_overview_row_empty_is_safe():
    assert cs.overview_row({}) == {"data_cutoff_time": ""}
    assert cs.overview_row(None) == {"data_cutoff_time": ""}


# ───────────── 达人行 ─────────────

PROMOTER_FIXTURE = [{
    "type": 1, "name": "大丸子【囍事来】", "id": "Wgg292",
    "user_source_id": "1635706331203293", "appName": "抖音号", "appId": 1128,
    "avatar": "https://x/y.jpg",
    "order_product_count": 16, "order_pay_num": 4325,
    "pay_gmv": "180504000", "settle_gmv": "98516485",
    "data_visible_status": 1,
}]


def test_talent_rows_maps_and_converts():
    rows = cs.talent_rows(PROMOTER_FIXTURE, "2026-10-21", "s", "e")
    assert len(rows) == 1
    r = rows[0]
    assert r["douyin_id"] == "1635706331203293"
    assert r["platform_talent_id"] == "Wgg292"
    assert r["name"] == "大丸子【囍事来】"
    assert r["order_product_count"] == 16
    assert r["order_pay_num"] == 4325
    assert r["pay_gmv"] == 1805040.00
    assert r["settle_gmv"] == 985164.85
    assert r["stat_date"] == "2026-10-21"


def test_talent_rows_drops_without_douyin_id():
    """没有抖音号 ⇒ 对不了拍 ⇒ 丢掉，不造数据。"""
    bad = [dict(PROMOTER_FIXTURE[0], user_source_id="")]
    bad.append(dict(PROMOTER_FIXTURE[0]))
    rows = cs.talent_rows(bad, "2026-10-21", "s", "e")
    assert len(rows) == 1
    assert rows[0]["douyin_id"] == "1635706331203293"


def test_talent_rows_empty():
    assert cs.talent_rows([], "d", "s", "e") == []
    assert cs.talent_rows(None, "d", "s", "e") == []


# ───────────── 费率不能被截断 ─────────────

def test_rate_fields_keep_decimal():
    """9.29% 截成 9 就是数据失真 —— 费率必须保留小数。"""
    data = {"order_info": {"data_details": [
        {"title": "平均佣金率", "field_key": "avg_commission_rate", "value": "9.29"}]},
        "business_info": {"data_details": [
            {"title": "平均服务费率", "field_key": "avg_service_rate", "value": "6.00"}]}}
    row = cs.overview_row(data)
    assert row["avg_commission_rate"] == pytest.approx(9.29)
    assert row["avg_service_rate"] == pytest.approx(6.0)


def test_to_float():
    assert cs.to_float("9.29") == pytest.approx(9.29)
    assert cs.to_float("6.00") == pytest.approx(6.0)
    assert cs.to_float("") == 0.0
    assert cs.to_float("x") == 0.0


def test_inst_columns_cover_overview_fields():
    """每个被映射的 field_key 都得有落库列，不能悄悄丢。"""
    assert set(cs.OVERVIEW_FIELDS.values()) == set(cs.INST_COLUMNS) - {"data_cutoff_time"}


def test_save_inst_stats_binding_alignment(tmp_path):
    """绑定数必须和列数一致（上一版这里报过 "Incorrect number of bindings"）。"""
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cs.ensure_schema(conn)
    row = cs.overview_row(OVERVIEW_FIXTURE)
    cs.save_inst_stats(conn, row, "2026-10-21")
    conn.commit()
    got = conn.execute(
        "SELECT pay_gmv, order_daren_count, avg_commission_rate FROM platform_inst_stats"
    ).fetchone()
    assert got == (6819991.22, 78, pytest.approx(9.29))
    conn.close()


def test_save_talent_stats_upsert(tmp_path):
    import sqlite3
    conn = sqlite3.connect(str(tmp_path / "t.db"))
    cs.ensure_schema(conn)
    rows = cs.talent_rows(PROMOTER_FIXTURE, "2026-10-21", "s", "e")
    assert cs.save_talent_stats(conn, rows) == 1
    assert cs.save_talent_stats(conn, rows) == 1          # 幂等
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM platform_talent_stats").fetchone()[0]
    assert n == 1
    conn.close()


def test_talent_rows_dedupes_platform_duplicate_rows():
    """平台会返回一字不差的重复行 ⇒ 必须显式去重，不能靠 DB UNIQUE 静默合并。"""
    dup = [PROMOTER_FIXTURE[0], dict(PROMOTER_FIXTURE[0])]
    rows = cs.talent_rows(dup, "2026-10-21", "s", "e")
    assert len(rows) == 1
    assert rows[0]["douyin_id"] == "1635706331203293"


# ───────────── 商品维度 ─────────────

PRODUCT_FIXTURE = [{
    "name": "Coolchap嘭嘭爱心系列2026沙滩度假女士平底拖鞋人字拖CCF1001",
    "id": "3729610176878215476",
    "avatar": "https://x/y.jpg",
    "order_daren_count": 11, "order_second_colonel_count": 0,
    "order_pay_num": 15, "pay_gmv": "193500", "settle_gmv": "0",
}]


def test_product_rows_maps_and_converts():
    rows = cs.product_rows(PRODUCT_FIXTURE, "2026-10-23", "s", "e")
    assert len(rows) == 1
    r = rows[0]
    assert r["platform_product_id"] == "3729610176878215476"
    assert r["order_daren_count"] == 11
    assert r["order_pay_num"] == 15
    assert r["pay_gmv"] == 1935.00
    assert r["settle_gmv"] == 0.0


def test_product_rows_dedupes():
    rows = cs.product_rows([PRODUCT_FIXTURE[0], dict(PRODUCT_FIXTURE[0])], "d", "s", "e")
    assert len(rows) == 1


def test_product_rows_drops_without_id():
    assert cs.product_rows([{"name": "x", "id": ""}], "d", "s", "e") == []


# ───────────── 名字覆盖率匹配 ─────────────

def test_name_coverage_exact_is_one():
    assert cs.name_coverage("Coolchap凉鞋", "Coolchap凉鞋") == 1.0


def test_name_coverage_abbr_vs_fullname():
    """简称不是全名的连续子串 —— 纯 `in` 会判 False，覆盖率要能认出来。"""
    ours = "嘭嘭爱心系列人字拖"
    theirs = "Coolchap嘭嘭爱心系列2026沙滩度假女士平底拖鞋人字拖CCF1001"
    cov = cs.name_coverage(ours, theirs)
    assert cov >= 0.6
    assert ours not in theirs      # 证明纯包含判断会漏


def test_name_coverage_unrelated_is_low():
    assert cs.name_coverage("嘭嘭爱心系列人字拖", "一双雪地靴男款加绒防滑") < 0.3


def test_name_coverage_empty():
    assert cs.name_coverage("", "x") == 0.0
    assert cs.name_coverage("x", "") == 0.0


def test_match_products_splits_hit_missing_cold():
    our = [("prod_1", "嘭嘭爱心系列人字拖", "COOLCHAP"),
           ("prod_2", "厚底松糕拖鞋", "COOLCHAP")]
    plat = [dict(cs.product_rows(PRODUCT_FIXTURE, "d", "s", "e")[0]),
            {"platform_product_id": "999", "name": "厚底松糕拖鞋新款", "pay_gmv": 100.0}]
    m = cs.match_products(plat, our, threshold=0.6)
    assert len(m["pairs"]) == 2
    assert {p[2][0] for p in m["pairs"]} == {"prod_1", "prod_2"}
    assert m["missing"] == []
    assert m["ours_cold"] == []


def test_match_products_reports_missing_and_cold():
    our = [("prod_1", "嘭嘭爱心系列人字拖", "COOLCHAP")]
    plat = [{"platform_product_id": "888", "name": "完全不相干的商品", "pay_gmv": 1.0}]
    m = cs.match_products(plat, our, threshold=0.6)
    assert m["pairs"] == []
    assert len(m["missing"]) == 1
    assert len(m["ours_cold"]) == 1
