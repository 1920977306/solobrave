"""商品建档（daren_cockpit/tools/filing_products.py）的纯函数测试。

重点锁四件事：
1. **口径不混**：三个「达人数」必须分列（平台带货达人 / 我方合作达人 / 我方机构出单达人）。
2. **不造数**：达人端取不到真值 ⇒ 价格/佣金留空，**不编**。
3. **merge 保留老值**：已建档商品只回填平台ID，**不覆盖** name/price。
4. **对拍用 n-gram 覆盖率**：平台名是全名、我们库里是简称，纯 `in` 恒 False。
"""
import importlib.util
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "filing_products.py")
spec = importlib.util.spec_from_file_location("filing_products", MOD_PATH)
fp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fp)


# ───────────── 金额/百分比/整数解析 ─────────────

@pytest.mark.parametrize("raw,expect", [
    ("¥549", 549.0),
    ("¥27.45", 27.45),
    ("¥1,299", 1299.0),
    ("", 0.0),
    (None, 0.0),
    ("不是数字", 0.0),
    (129, 129.0),
])
def test_yuan(raw, expect):
    assert fp._yuan(raw) == expect


@pytest.mark.parametrize("raw,expect", [
    ("5%", 5.0),
    ("92.5%", 92.5),
    ("15%", 15.0),
    ("", 0.0),
    (None, 0.0),
    (20, 20.0),
])
def test_pct(raw, expect):
    assert fp._pct(raw) == expect


@pytest.mark.parametrize("raw,expect", [
    ("4843", 4843),
    ("108", 108),
    ("", 0),
    (None, 0),
    ("不是数字", 0),
])
def test_int(raw, expect):
    assert fp._int(raw) == expect


# ───────────── 建行：口径与不造数 ─────────────

PR = {
    "platform_product_id": "3842076859001077832",
    "name": "Coolchap2026秋冬新品编织沙丘系列骑士靴时尚西部靴子女6201s",
    "avatar": "https://img/avatar.jpg",
    "order_daren_count": 6,        # 团长端：我方机构出单达人数
    "order_pay_num": 3230,
    "pay_gmv": 1589342.0,
    "settle_gmv": 0.0,
    "stat_date": "2026-10-23",
}

DAREN = {
    "product_id": "3842076859001077832",
    "name": "Coolchap2026秋冬新品编织沙丘系列骑士靴时尚西部靴子女6201s",
    "price": "¥549",
    "commission": "¥27.45",
    "commission_rate": "5%",
    "sales": "4843",
    "shop": "Coolchap官方旗舰店",
    "category_first": "鞋靴",
    "category_third": "靴子",
    "shop_score": "92",
    "good_ratio": "92.5%",
    "cooper_author_num": "108",
    "main_img": "https://img/main.jpg",
    "tags": ["流量扶持", "消费券"],
}


def test_build_row_reads_daren_fields():
    r = fp.build_row(PR, DAREN)
    assert r["price"] == 549.0
    assert r["price_range"] == "¥549"
    assert r["commission_amount"] == 27.45
    assert r["monthly_sales"] == 4843
    assert r["brand"] == "COOLCHAP"
    assert r["category"] == "鞋靴/靴子"
    assert r["main_image"] == "https://img/main.jpg"


def test_build_row_single_rate_uses_default_key():
    """单佣金率 → `{'default': rate}`（solobrave-server.py 的既定约定）。"""
    r = fp.build_row(PR, DAREN)
    assert r["commission_rates"] == '{"default": 5}'


def test_build_row_三个达人数必须分列():
    """★ 口径混用是这类数据最容易犯的错 —— 三个「达人数」语义完全不同。"""
    r = fp.build_row(PR, DAREN)
    assert r["influencer_count"] == 108, "平台带货达人数 = 达人端 cooper_author_num"
    assert r["talent_count"] == 0, "我方合作达人数由 product_talent_match 维护，不能手填"
    assert r["platform_order_daren_count"] == 6, "我方机构出单达人数 = 团长端 order_daren_count"


def test_build_row_platform_snapshot_comes_from_captain_side():
    r = fp.build_row(PR, DAREN)
    assert r["platform_product_id"] == PR["platform_product_id"]
    assert r["platform_pay_gmv"] == 1589342.0
    assert r["platform_order_pay_num"] == 3230
    assert r["platform_stat_date"] == "2026-10-23"


def test_build_row_no_daren_data_does_not_fabricate():
    """达人端没数据 ⇒ 价格/佣金/类目留空，**不能编**。"""
    r = fp.build_row(PR, None)
    assert r["price"] == 0.0
    assert r["commission_amount"] == 0.0
    assert r["commission_rates"] == "{}"
    assert r["monthly_sales"] == 0
    assert r["influencer_count"] == 0
    # 团长端有的照留
    assert r["platform_product_id"] == PR["platform_product_id"]
    assert r["platform_pay_gmv"] == 1589342.0
    # 主图退回团长端 avatar
    assert r["main_image"] == "https://img/avatar.jpg"


def test_build_row_name_falls_back_to_captain_side():
    d = dict(DAREN)
    d["name"] = ""
    r = fp.build_row(PR, d)
    assert r["name"] == PR["name"]


# ───────────── id 生成 ─────────────

def test_new_product_id_uses_brand_and_platform_tail():
    taken = set()
    pid = fp.new_product_id("COOLCHAP", "3842076859001077832", taken)
    assert pid == "prod_coolchap_077832"
    assert pid in taken


def test_new_product_id_avoids_collision():
    taken = {"prod_coolchap_077832"}
    pid = fp.new_product_id("COOLCHAP", "3842076859001077832", taken)
    assert pid != "prod_coolchap_077832"
    assert pid not in {"prod_coolchap_077832"}
    # 函数会把新 id 登记进 taken，防止同一次运行里再撞
    assert pid in taken


# ───────────── 分类：n-gram 只出候选，��挂载 ─────────────

def test_name_matched_candidates_finds_by_coverage():
    """平台全名 vs 我方简称：不是连续子串，纯 `in` 会恒 False ⇒ 必须靠 n-gram 覆盖率。"""
    plat = [{"platform_product_id": "P1",
             "name": "Coolchap嘭嘭爱心系列2026沙滩度假女士平底拖鞋人字拖CCF1001"}]
    ours = [{"id": "o1", "name": "嘭嘭爱心系列人字拖", "platform_product_id": ""}]
    c = fp.name_matched_candidates(plat, ours)["candidates"]
    assert len(c) == 1
    assert c[0]["coverage"] >= fp.MATCH_THRESHOLD
    assert c[0]["our"]["id"] == "o1"


def test_name_matched_candidates_ignores_unrelated():
    plat = [{"platform_product_id": "P1", "name": "温途 7A云绒暖感竹叶青抗菌男士内裤2001D"}]
    ours = [{"id": "o1", "name": "嘭嘭爱心系列人字拖", "platform_product_id": ""}]
    assert fp.name_matched_candidates(plat, ours)["candidates"] == []


def test_name_matched_candidates_one_our_row_not_consumed_twice():
    """同一条我方记录被两个平台商品命中 ⇒ 两个候选都产出，由 `resolve_conflicts` 裁决。

    （旧行为是「贪心排除」—— A 抢走候选后 B 匹配不上自己的记录 ⇒ B 被重复建档，
    已废弃，见 `test_name_matched_candidates_does_not_greedily_consume`。）
    """
    plat = [{"platform_product_id": "P1", "name": "Coolchap嘭嘭爱心系列2026春人字拖"},
            {"platform_product_id": "P2", "name": "Coolchap新款嘭嘭爱心系列春海边人字拖"}]
    ours = [{"id": "o1", "name": "嘭嘭爱心系列人字拖", "platform_product_id": ""}]
    c = fp.name_matched_candidates(plat, ours)["candidates"]
    assert len(c) == 2
    win, lose = fp.resolve_conflicts(c)
    assert len(win) == 1 and len(lose) == 1


# ───────────── 强证据裁决：宁可漏挂，不可错挂 ─────────────

def _our(price, rates):
    return {"id": "o1", "name": "嘭嘭爱心系列人字拖", "price": price,
            "commission_rates": rates}


def test_strong_evidence_accepts_same_price_and_commission():
    """实测真同款：我方 ¥129 / 自然流佣金 20% == 平台 ¥129 / 20%。"""
    daren = {"price": "¥129", "commission_rate": "20%"}
    ok, why = fp.strong_evidence({}, _our(129.0, '{"自然流": 20, "投放期": 8}'), daren)
    assert ok is True
    assert "一致" in why


def test_strong_evidence_rejects_same_series_different_model():
    """★ 实测踩坑：`裸穿微光 7800-2327` 与 `繁花编织 7800-2315` n-gram 覆盖率 0.79，
    但**价格对不上** ⇒ 不是同一商品 ⇒ 必须拒绝挂载。"""
    daren = {"price": "¥459", "commission_rate": "5%"}
    ok, why = fp.strong_evidence({}, _our(299.0, '{"default": 5}'), daren)
    assert ok is False
    assert "价格不一致" in why


def test_strong_evidence_rejects_commission_mismatch():
    daren = {"price": "¥129", "commission_rate": "15%"}
    ok, why = fp.strong_evidence({}, _our(129.0, '{"default": 20}'), daren)
    assert ok is False
    assert "佣金率不一致" in why


def test_strong_evidence_refuses_without_truth():
    """没有达人端真值 ⇒ 判不了 ⇒ 拒绝（不猜）。"""
    ok, why = fp.strong_evidence({}, _our(129.0, '{"default": 20}'), None)
    assert ok is False
    assert "无法判定" in why


def test_strong_evidence_refuses_when_price_missing():
    daren = {"price": "", "commission_rate": "20%"}
    ok, why = fp.strong_evidence({}, _our(0.0, '{"default": 20}'), daren)
    assert ok is False


# ───────────── 我方佣金率解析 ─────────────

@pytest.mark.parametrize("raw,expect", [
    ('{"自然流": 20, "投放期": 8}', 20.0),
    ('{"default": 5}', 5.0),
    ('{}', 0.0),
    ('', 0.0),
    (None, 0.0),
    ("不是 JSON", 0.0),
])
def test_our_commission_rate(raw, expect):
    assert fp._our_commission_rate(raw) == expect


# ───────────── 冲突消解：同一条我方记录只能挂一个平台商品 ─────────────

def test_resolve_conflicts_keeps_highest_coverage():
    """两个平台商品都指向同一条我方记录 ⇒ 覆盖率高的胜，低的改建新记录。"""
    our = {"id": "o1", "name": "嘭嘭爱心系列人字拖"}
    a = {"platform": {"platform_product_id": "P1", "pay_gmv": 10}, "our": our, "coverage": 0.9}
    b = {"platform": {"platform_product_id": "P2", "pay_gmv": 99}, "our": our, "coverage": 0.7}
    win, lose = fp.resolve_conflicts([a, b])
    assert [c["platform"]["platform_product_id"] for c in win] == ["P1"]
    assert [c["platform"]["platform_product_id"] for c in lose] == ["P2"]


def test_resolve_conflicts_tie_breaks_by_gmv():
    our = {"id": "o1", "name": "X"}
    a = {"platform": {"platform_product_id": "P1", "pay_gmv": 10}, "our": our, "coverage": 0.8}
    b = {"platform": {"platform_product_id": "P2", "pay_gmv": 99}, "our": our, "coverage": 0.8}
    win, lose = fp.resolve_conflicts([a, b])
    assert win[0]["platform"]["platform_product_id"] == "P2"
    assert lose[0]["platform"]["platform_product_id"] == "P1"


def test_resolve_conflicts_no_conflict():
    a = {"platform": {"platform_product_id": "P1", "pay_gmv": 1},
         "our": {"id": "o1"}, "coverage": 0.9}
    b = {"platform": {"platform_product_id": "P2", "pay_gmv": 1},
         "our": {"id": "o2"}, "coverage": 0.9}
    win, lose = fp.resolve_conflicts([a, b])
    assert len(win) == 2 and lose == []


def test_name_matched_candidates_does_not_greedily_consume():
    """★ 每个平台商品**独立**找候选 —— 不能「A 抢走 B 的候选」。

    回归锁：贪心会导致 B 匹配不上自己的记录 → B 被重复建档。
    """
    plat = [{"platform_product_id": "P1", "name": "Coolchap2026秋季新品裸穿微光系列玛丽珍单鞋7800-2327"},
            {"platform_product_id": "P2", "name": "Coolchap2026秋季新品繁花编织系列玛丽珍单鞋7800-2315"}]
    ours = [{"id": "o1", "name": "Coolchap2026秋季新品繁花编织系列玛丽珍单鞋7800-2315",
             "platform_product_id": ""}]
    c = fp.name_matched_candidates(plat, ours)["candidates"]
    assert len(c) == 2            # 两个平台商品各自都拿到候选（不是 1 个）
