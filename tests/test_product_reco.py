"""选品推理链 + 推荐指数评分器（daren_cockpit/tools/product_reco.py）的纯函数测试。

锁住四条设计铁律（都是这轮踩坑换来的）：
1. **缺失维度不当 0 分** —— 缺一项不能比「那一项很差」得分还低。
2. **星级用绝对分** —— 一池子全是很差的商品，不能被排出 5★（假绿灯）。
3. **池内覆盖率闸门** —— 覆盖率不够的维度整体不参与，口径才可比。
4. **只丢不造** —— 画像不足就不生成策略，不编粉丝画像。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "product_reco.py")
spec = importlib.util.spec_from_file_location("product_reco", MOD_PATH)
pr = importlib.util.module_from_spec(spec)
sys.modules["product_reco"] = pr          # ★ dataclass + future annotations 需要
spec.loader.exec_module(pr)


# ─────────────── 夹具 ───────────────

def _talent(**over):
    row = {
        "id": "tal_x", "name": "璐妈妈",
        "fan_age": '{"31-35": 38, "26-30": 28, "36-40": 18}',
        "fan_gender": '{"女": 92, "男": 8}',
        "fan_price_range": "300-600",
        "fan_category": "鞋靴/凉鞋",
        "product_count": 12, "total_gmv": 300000,
        "average_price": 0,
    }
    row.update(over)
    return row


def _prod(pid, name, price, **over):
    p = {
        "id": pid, "name": name, "price": price,
        "category": "鞋靴/凉鞋",
        "shop_score": 92, "good_ratio": 94.0,
        "commission_rate": 20.0,
        "video_sales_7d": 5000,
        "talents_sold": 30,
        "board_rank": 5,
        "tags": ["消费券", "流量扶持"],
    }
    p.update(over)
    return p


# ─────────────── 价格区间解析 ───────────────

@pytest.mark.parametrize("raw,expect", [
    ("300-600", (300.0, 600.0)),
    ("¥300~600", (300.0, 600.0)),
    ("0-100元", (0.0, 100.0)),
    ("300", (300.0, 300.0)),
    ("600-300", (300.0, 600.0)),   # 反了也要归一
    ("abc", (0.0, 0.0)),
    ("", (0.0, 0.0)),
    (None, (0.0, 0.0)),
])
def test_parse_price_range(raw, expect):
    assert pr.parse_price_range(raw) == expect


# ─────────────── 画像：不编 ───────────────

def test_build_profile_reads_json_columns():
    p = pr.build_profile(_talent())
    assert p.main_age == "31-35"
    assert p.main_gender == "女"
    assert p.price_center == 450.0
    assert "鞋靴/凉鞋" in p.categories
    assert p.is_ready is True


def test_build_profile_no_invent():
    """画像字段缺失 ⇒ 空着，不拿默认值糊。"""
    p = pr.build_profile({"id": "t", "name": "空"})
    assert p.age_dist == {}
    assert p.gender_dist == {}
    assert p.price_center == 0.0
    assert p.categories == []
    assert p.is_ready is False
    assert "客单价" in p.missing()
    assert "偏好类目" in p.missing()


def test_build_profile_avg_price_fallback():
    p = pr.build_profile({"id": "t", "average_price": 88, "fan_category": "鞋靴"})
    assert p.price_center == 88.0


def test_build_profile_cleans_ocr_category():
    """OCR 味的偏好类目要洗干净（'偏好服装(57%)' → '服装'）。

    不洗的话：策略名读着别扭 + 类目匹配对不上商品库写法（候选数归零）。
    """
    p = pr.build_profile(_talent(fan_category="偏好服装(57%)"))
    assert p.categories == ["服装"]


@pytest.mark.parametrize("raw,expect", [
    ("偏好服装(57%)", "服装"),
    ("鞋靴/凉鞋", "鞋靴/凉鞋"),
    ("服装（46%）", "服装"),
    ("", ""),
])
def test_clean_category(raw, expect):
    assert pr.clean_category(raw) == expect


def test_build_profile_tolerates_broken_json():
    p = pr.build_profile({"id": "t", "fan_age": "{不是json", "fan_gender": None})
    assert p.age_dist == {}
    assert p.gender_dist == {}


# ─────────────── 策略：画像不足不硬造 ───────────────

def test_no_strategy_without_profile():
    p = pr.build_profile({"id": "t", "name": "无画像"})
    assert pr.build_strategies(p) == []


def test_no_strategy_without_price():
    p = pr.build_profile({"id": "t", "fan_category": "鞋靴"})
    assert pr.build_strategies(p) == []


def test_strategies_cover_three_roles():
    p = pr.build_profile(_talent())
    sts = pr.build_strategies(p)
    assert len(sts) == 3
    assert {s.role for s in sts} == {pr.ROLE_HIT, pr.ROLE_PUSH, pr.ROLE_TRAFFIC}
    # 价带必须围绕客单价（450）展开，且引流款最便宜、爆品上限=客单价
    by_role = {s.role: s for s in sts}
    assert by_role[pr.ROLE_HIT].price_hi == 450.0
    assert by_role[pr.ROLE_TRAFFIC].price_hi < by_role[pr.ROLE_PUSH].price_lo
    assert by_role[pr.ROLE_HIT].price_lo < by_role[pr.ROLE_PUSH].price_lo


def test_strategy_rationale_cites_profile_numbers():
    p = pr.build_profile(_talent())
    st = pr.build_strategies(p)[0]
    assert "450" in st.rationale           # 引用客单价
    assert "31-35" in st.rationale or "女" in st.rationale


def test_inexperienced_talent_gets_caveat():
    p = pr.build_profile(_talent(product_count=1, total_gmv=100))
    assert p.is_inexperienced is True
    assert "经验较少" in pr.build_strategies(p)[0].rationale


def test_strategy_fills_missing_categories_with_same_category():
    """偏好类目只有 1 个 ⇒ 三个角色都用这个类目，不编别的类目。"""
    p = pr.build_profile(_talent(fan_category="鞋靴", category="", main_category=""))
    sts = pr.build_strategies(p)
    assert len(sts) == 3
    assert {s.category for s in sts} == {"鞋靴"}


# ─────────────── 星级：绝对分 ───────────────

@pytest.mark.parametrize("score,expect", [
    (95, 5), (85, 5), (84.9, 4), (78, 4), (68, 3), (50, 2), (49.9, 1), (0, 1),
])
def test_stars_of(score, expect):
    assert pr.stars_of(score) == expect


def test_star_thresholds_are_calibrated_to_real_distribution():
    """★ 校准回归：阈值是按真实数据分布定的，别随手改。

    2026-10-24 用 `daren_cockpit/tools/reco_calibrate.py` 实测本库
    （★ 主口径 = **用户实际看到的推荐列表**，不是策略池全体候选）：
        展示 323 条 ｜ 1★:70(22%) 2★:63(20%) 3★:84(26%) 4★:38(12%) 5★:68(21%)
        候选 764 个 ｜ min 17.0 / p25 65.6 / median 74.3 / p90 91.4 / max 97.5
    ⇒ 5★ 约占 1/5（不是清一色），同时 1★ 留得住（低分不假绿灯）。

    ⚠️ 分布变了要**重跑** `python3 daren_cockpit/tools/reco_calibrate.py`
       并更新这里的数字（脚本不可复现 = 阈值迟早失准）。
    """
    hist = {"15-19": 2, "20-24": 3, "25-29": 18, "30-34": 6, "35-39": 3,
            "40-44": 8, "45-49": 34, "50-54": 24, "55-59": 22, "60-64": 68,
            "65-69": 96, "70-74": 117, "75-79": 85, "80-84": 58,
            "85-89": 114, "90-94": 78, "95-99": 28}
    samples = []
    for bucket, count in hist.items():
        lo = float(bucket.split("-")[0])
        samples += [lo + 0.5] * count
    assert len(samples) == 764
    stars = [pr.stars_of(s) for s in samples]
    n5 = stars.count(5)
    assert 0 < n5 <= len(samples) * 0.35, f"5★ 占比 {n5}/{len(samples)} 太高（阈值失去区分度）"
    assert stars.count(1) >= 1, "必须有 1★（否则低分商品也被抬成假绿灯）"
    assert len(set(stars)) >= 4, f"星级只有 {set(stars)} 档，区分度不足"


def test_stars_combined_floor_and_percentile():
    """星级 = min(绝对分星级, 池内分位星级)。"""
    assert pr.stars_by_percentile(0.95) == 5
    assert pr.stars_by_percentile(0.80) == 4
    assert pr.stars_by_percentile(0.60) == 3
    assert pr.stars_by_percentile(0.30) == 2
    assert pr.stars_by_percentile(0.05) == 1


def test_stars_are_not_all_five_in_a_realistic_pool():
    """池子里不该「清一色 5★」—— 用户看不到差别就没意义。"""
    pool = [_prod(f"p{i}", f"商品{i}", 200 + i * 30,
                  shop_score=88 + i, good_ratio=80 + i * 2,
                  commission_rate=10 + i * 2, video_sales_7d=100 + i * 500,
                  talents_sold=i, board_rank=10 + i)
            for i in range(10)]
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    recs = pr.score_products(pool, st)
    assert len({r.stars for r in recs}) >= 3, [r.stars for r in recs]


def test_bad_pool_does_not_get_five_stars():
    """铁律 2：一池子全是很差的商品，不能被分位制排成 5★。"""
    bad = [_prod(f"p{i}", f"差商品{i}", 999, shop_score=10, good_ratio=5.0,
                 commission_rate=1.0, video_sales_7d=1, talents_sold=0,
                 board_rank=9999, tags=[])
           for i in range(4)]
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    recs = pr.score_products(bad, st)
    assert recs
    assert all(r.stars <= 2 for r in recs), [r.stars for r in recs]


def test_small_pool_caps_stars():
    """候选池太小 ⇒ 池内无区分度 ⇒ 星级封顶（防假绿灯）。"""
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    one = pr.score_products([_prod("p1", "唯一商品", 200)], st)[0]
    assert one.low_confidence is True
    assert one.stars <= pr.MAX_STARS_LOW_CONFIDENCE


# ─────────────── 评分器：缺失维度不当 0 分 ───────────────

def test_missing_dim_beats_bad_dim():
    """铁律 1：缺一项好维度，绝不能比「那一项很差」得分还低。

    池子要够大（覆盖率 ≥ 60%）才有资格比较，否则该维度整体不参与。
    """
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod("p_missing", "缺好评率", 200, good_ratio=None)]
    pool += [_prod(f"p{i}", f"商品{i}", 200, good_ratio=90.0) for i in range(4)]
    recs = {r.product_id: r for r in pr.score_products(pool, st)}

    pool2 = [_prod(f"q{i}", f"q{i}", 200, good_ratio=90.0) for i in range(4)]
    pool2.append(_prod("p_bad", "好评率极差", 200, good_ratio=5.0))
    recs.update({r.product_id: r for r in pr.score_products(pool2, st)})

    assert recs["p_missing"].score > recs["p_bad"].score
    assert "好评率" in recs["p_missing"].missing_dims
    assert "好评率" not in recs["p_bad"].missing_dims
    assert recs["p_missing"].completeness < 1.0


def test_missing_dims_are_listed_not_scored_zero():
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200) for i in range(4)]
    p = _prod("p_missing", "缺多项", 200)
    p["shop_score"] = None
    p["good_ratio"] = None
    pool.append(p)
    r = {x.product_id: x for x in pr.score_products(pool, st)}["p_missing"]
    labels = {d.label for d in r.dims}
    assert "店铺体验分" not in labels
    assert {"店铺体验分", "好评率"} <= set(r.missing_dims)
    assert r.completeness < 1.0


def test_dropped_dims_are_reported():
    """覆盖率不足的维度要**如实报出来**，不能让人误以为全都参与了。"""
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200) for i in range(4)]
    pool.append(_prod("p_none", "缺转化率", 200, conversion_rate=None))
    recs = pr.score_products(pool, st)
    assert "转化率" in recs[0].dropped_dims


def test_dims_carry_explanation():
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    r = pr.score_products([_prod("p1", "商品", 200)], st)[0]
    assert r.dims
    for d in r.dims:
        assert d.note, f"{d.key} 缺解释"
    notes = " ".join(d.note for d in r.dims)
    assert "佣金率" in notes
    assert "店铺体验分" in notes


# ─────────────── 覆盖率闸门 ───────────────

def test_low_coverage_dim_is_dropped_entirely():
    """铁律 3：池内覆盖率不够 ⇒ 该维度整体不参与（口径才可比）。"""
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200,
                  conversion_rate=(5.0 if i < 2 else None))
            for i in range(10)]
    recs = pr.score_products(pool, st)
    keys = {d.key for r in recs for d in r.dims}
    assert "conversion_rate" not in keys


def test_high_coverage_dim_is_used():
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200,
                  conversion_rate=(5.0 if i < 9 else None))
            for i in range(10)]
    recs = pr.score_products(pool, st)
    keys = {d.key for r in recs for d in r.dims}
    assert "conversion_rate" in keys


# ─────────────── 编排 ───────────────

def test_recommend_reports_insufficient_profile():
    chain = pr.recommend({"id": "t", "name": "画像不足"}, [_prod("p1", "x", 100)])
    assert chain["strategies"] == []
    assert chain["recommendations"] == []
    assert any("画像不足" in n for n in chain["notes"])


def test_recommend_end_to_end():
    chain = pr.recommend(_talent(), [
        _prod("p1", "凉鞋A", 300),
        _prod("p2", "凉鞋B", 120),
        _prod("p3", "凉鞋C", 700),
    ], top_n=3)
    assert chain["strategies"]
    assert chain["recommendations"]
    for r in chain["recommendations"]:
        assert 1 <= r["stars"] <= 5
        assert 0 <= r["score"] <= 100
        assert r["strategy"]
        assert r["completeness"] > 0


def test_recommend_respects_category():
    """类目不匹配的商品不该被这条策略捞进来。"""
    chain = pr.recommend(_talent(), [
        _prod("p1", "鞋类", 300),
        _prod("p2", "食品", 300, category="食品饮料/休闲食品"),
    ])
    matched = [r["product_id"] for r in chain["recommendations"]]
    assert "p1" in matched
    assert "p2" not in matched


# ─────────────── 类目口径对齐 ───────────────

@pytest.mark.parametrize("a,b,expect", [
    ("鞋靴/凉鞋", "鞋靴/靴子", True),      # 同级不同款
    ("鞋靴/凉鞋", "鞋靴", True),            # 画像写细、库写粗
    ("鞋靴", "鞋靴箱包", True),             # 平台口径叫「鞋靴箱包」
    ("鞋靴/凉鞋", "食品饮料/休闲食品", False),
    ("", "鞋靴", False),
])
def test_categories_overlap(a, b, expect):
    assert pr._categories_overlap(a, b) is expect


def test_strategy_category_resolves_to_pool_vocabulary():
    """策略类目要落到商品池真实写法上（否则候选数归零）。"""
    pool = [_prod("p1", "靴子", 300, category="鞋靴/靴子")]
    chain = pr.recommend(_talent(fan_category="鞋靴/凉鞋"), pool)
    assert chain["strategy_match"][0]["candidate_count"] == 1
    assert chain["recommendations"]


def test_strategy_without_stock_is_reported():
    """池里没这个类目的货 ⇒ 明确说「无货」，不是静默 0。"""
    chain = pr.recommend(_talent(fan_category="母婴玩具"),
                         [_prod("p1", "鞋", 300, category="鞋靴/靴子")])
    hit = [s for s in chain["strategy_match"]
           if "没有这个类目的货" in (s.get("note") or "")]
    assert hit


def test_recommend_dedups_same_product():
    """同一商品命中多条策略 ⇒ 只保留最高分那条。"""
    chain = pr.recommend(_talent(), [_prod("p1", "鞋", 300)], top_n=6)
    ids = [r["product_id"] for r in chain["recommendations"]]
    assert len(ids) == len(set(ids))


def test_explain_chain_is_readable():
    chain = pr.recommend(_talent(), [_prod("p1", "凉鞋A", 300)])
    text = pr.explain_chain(chain)
    assert "粉丝画像" in text
    assert "类目策略" in text
    assert "推荐" in text
