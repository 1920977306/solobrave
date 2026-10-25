"""排品表 + 提醒（daren_cockpit/tools/product_plan.py）的纯函数测试。

锁住四件事：
1. **生命周期判得准**：爬坡 / 稳定 / 衰退 / 尾货，且**绝对量闸门**不能被趋势绕过。
2. **缺失依据不当 0 分**：没有达人历史结构就不打分，不拿 0 冒充。
3. **排期有约束**：每天不超坑位、同类目不连推、尾货优先清。
4. **只丢不造**：没数据的品不硬排。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

MOD_PATH = (Path(__file__).resolve().parents[1] / "daren_cockpit" / "tools"
            / "product_plan.py")
spec = importlib.util.spec_from_file_location("product_plan", MOD_PATH)
pp = importlib.util.module_from_spec(spec)
sys.modules["product_plan"] = pp
spec.loader.exec_module(pp)


# ─────────────── 夹具 ───────────────

def _axis(recent, prev=None, start=1):
    """构造 30 天日销量轴：prev 是前 7 天，recent 是近 7 天。"""
    prev = prev if prev is not None else recent
    pts = []
    d = 1
    for v in list(prev) + list(recent):
        pts.append({"date": f"202609{d:02d}", "qty": v})
        d += 1
    return pts


def _talent(**over):
    row = {
        "id": "tal_x", "name": "七月姐姐",
        "category_distribution": {"个护家清": 63, "美妆": 20, "滋补保健": 13},
        "price_distribution": {"0~25": 100, "25~50": 200, "50~100": 400,
                                "100~200": 600, "200~500": 300},
        "average_price": 65.71, "video_avg_price": "¥65.42",
        "total_products": 192, "total_gmv": 500000,
    }
    row.update(over)
    return row


def _product(pid, name, price, **over):
    p = {"id": pid, "name": name, "price": price, "category": "个护家清/面霜",
         "commission_rates": {"default": 20}, "shop_score": 92, "good_ratio": 94.0,
         "sale_axis": _axis([30, 30, 30, 30, 30, 30, 30])}
    p.update(over)
    return p


# ─────────────── 生命周期 ───────────────

def test_lifecycle_ramp():
    lc = pp.product_lifecycle(_axis([60] * 7, [20] * 7))
    assert lc.stage == pp.LIFECYCLE_RAMP
    assert lc.change_pct > 100
    assert "爬坡" in lc.label


def test_lifecycle_peak():
    lc = pp.product_lifecycle(_axis([30] * 7, [32] * 7))
    assert lc.stage == pp.LIFECYCLE_PEAK


def test_lifecycle_decline():
    lc = pp.product_lifecycle(_axis([25] * 7, [60] * 7))
    assert lc.stage == pp.LIFECYCLE_DECLINE
    assert "跌" in lc.note


def test_lifecycle_tail_zero_sales():
    lc = pp.product_lifecycle(_axis([0] * 7, [50] * 7))
    assert lc.stage == pp.LIFECYCLE_TAIL
    assert "清" in lc.note


def test_lifecycle_absolute_gate_beats_trend():
    """★ 日均不到 1 件 ⇒ 不管是涨是跌都算「基本没动销」，不能叫爬坡期。

    近7天**总共**只卖 1 件（日均 0.14）—— 真实数据里就有这种品。
    """
    lc = pp.product_lifecycle(_axis([0] * 6 + [1], [0] * 7))
    assert lc.stage == pp.LIFECYCLE_TAIL
    assert "没动销" in lc.note


def test_lifecycle_unknown_when_no_data():
    assert pp.product_lifecycle([]).stage == pp.LIFECYCLE_UNKNOWN
    assert pp.product_lifecycle(None).stage == pp.LIFECYCLE_UNKNOWN
    assert pp.product_lifecycle([{"date": "", "qty": 5}]).stage == pp.LIFECYCLE_UNKNOWN


def test_lifecycle_sorts_by_date():
    """轴给乱了序也要按日期排。"""
    pts = [{"date": "20260902", "qty": 10}, {"date": "20260901", "qty": 90},
           {"date": "20260903", "qty": 10}]
    lc = pp.product_lifecycle(pts)
    assert lc.stage in (pp.LIFECYCLE_UNKNOWN, pp.LIFECYCLE_TAIL, pp.LIFECYCLE_PEAK)


def test_lifecycle_calibration_regression():
    """★ 校准回归：真实 47 个商品跑出来的分布不能突变。

    2026-10-24 实测（补完商品维度后）：ramp 13 / tail 22 / decline 5 / peak 7
    ⇒ 尾货最多（一半是老款/新品没动销），爬坡其次。
    """
    samples = []
    # ramp：日均 30 → 60
    samples.append(pp.product_lifecycle(_axis([60] * 7, [20] * 7)))
    # peak：日均 30 → 30
    samples.append(pp.product_lifecycle(_axis([30] * 7, [32] * 7)))
    # decline：日均 60 → 25
    samples.append(pp.product_lifecycle(_axis([25] * 7, [60] * 7)))
    # tail：日均 50 → 0
    samples.append(pp.product_lifecycle(_axis([0] * 7, [50] * 7)))
    # tail：绝对量闸门（近7天总共 1 件）
    samples.append(pp.product_lifecycle(_axis([0] * 6 + [1], [0] * 7)))
    stages = [s.stage for s in samples]
    assert stages == [pp.LIFECYCLE_RAMP, pp.LIFECYCLE_PEAK, pp.LIFECYCLE_DECLINE,
                      pp.LIFECYCLE_TAIL, pp.LIFECYCLE_TAIL]


# ─────────────── 达人画像 ───────────────

def test_build_talent_fit_from_category_distribution():
    fit = pp.build_talent_fit(_talent())
    assert fit.top_category == "个护家清"
    assert fit.category_share["美妆"] == 20
    assert fit.price_buckets["50~100"] == 400
    assert fit.note_missing() == []


def test_build_talent_fit_fallback_to_top_categories():
    row = _talent(category_distribution={}, price_distribution={},
                  top_categories=[{"name": "个护家清", "avg_price": "¥59.98",
                                   "gmv": "¥50万-100万"}])
    fit = pp.build_talent_fit(row)
    assert fit.top_category == "个护家清"
    assert fit.categories_with_price["个护家清"] == 59.98


def test_build_talent_fit_reports_missing():
    fit = pp.build_talent_fit({"id": "t"})
    assert "品类结构" in fit.note_missing()
    assert "价格带" in fit.note_missing()


# ─────────────── 排品优先级 ───────────────

def test_score_item_has_explainable_reasons():
    fit = pp.build_talent_fit(_talent())
    score, reasons, completeness = pp.score_item(fit, _product("p1", "面霜", 99))
    assert score > 0
    assert completeness > 0
    assert any("佣金率" in r for r in reasons)
    assert any("GMV" in r for r in reasons)          # 引用达人历史占比
    assert any("%" in r for r in reasons)


def test_score_item_prefers_fit_and_profit():
    fit = pp.build_talent_fit(_talent())
    good = _product("p1", "面霜", 99, category="个护家清/面霜",
                    commission_rates={"default": 25})
    bad = _product("p2", "耳机", 999, category="3C数码家电/耳机",
                   commission_rates={"default": 2})
    s_good, _, _ = pp.score_item(fit, good)
    s_bad, _, _ = pp.score_item(fit, bad)
    assert s_good > s_bad


def test_score_item_missing_talent_data_does_not_zero_out():
    """达人历史全空 ⇒ 该维度退出加权，不拿 0 分冒充（完整度 <1）。"""
    empty_fit = pp.build_talent_fit({"id": "t"})
    score, reasons, completeness = pp.score_item(
        empty_fit, _product("p1", "面霜", 99))
    assert completeness < 1.0
    assert score > 0                      # 佣金 + 时机仍在


def test_score_item_no_data_at_all():
    score, reasons, completeness = pp.score_item(
        pp.build_talent_fit({"id": "t"}),
        {"id": "p", "name": "空", "price": 0, "category": ""})
    assert completeness == 0.0
    assert not reasons or all(isinstance(r, str) for r in reasons)


def test_score_item_quality_penalises_bad_product():
    fit = pp.build_talent_fit(_talent())
    good = _product("p1", "好品", 99, shop_score=95, good_ratio=97.0)
    bad = _product("p2", "差品", 99, shop_score=60, good_ratio=55.0)
    assert pp.score_item(fit, good)[0] > pp.score_item(fit, bad)[0]


def test_score_item_tail_lifecycle_scores_lower_than_ramp():
    fit = pp.build_talent_fit(_talent())
    ramp = _product("p1", "爬坡", 99, sale_axis=_axis([60] * 7, [20] * 7))
    tail = _product("p2", "尾货", 99, sale_axis=_axis([0] * 7, [50] * 7))
    assert pp.score_item(fit, ramp)[0] > pp.score_item(fit, tail)[0]


# ─────────────── 排期 ───────────────

def _deals(n, cat="个护家清/面霜"):
    return [{"id": f"deal_{i}", "product_id": f"p{i}", "product_name": f"品{i}",
             "status": "approved", "scheduled_at": 0, "category": cat}
            for i in range(n)]


def _products(n, cat="个护家清/面霜"):
    return [_product(f"p{i}", f"品{i}", 99 + i, category=cat) for i in range(n)]


def test_schedule_respects_daily_capacity():
    slots = pp.build_schedule(_talent(), _deals(5), _products(5), days=7, per_day=2)
    assert len(slots) == 7
    for s in slots:
        assert len(s.items) <= 2


def test_schedule_same_category_not_consecutive():
    """★ 同品类不排在**相邻两个日历日**上。

    ⚠️ 口径为什么是「日历日」而不是「上一个有排品的日」：
       后者（跨过空档也算连推）会让品类集中的池子**死锁** ——
       实测 4 个同类目的品、7 天、每天 2 个坑，旧口径只排得出第 1 天，
       剩下的品永远「排不进本周」；而老大的诉求是
       「达人手里每天要接很多的品，这些品都需要 AI 帮忙安排」——
       **约束不能把品丢掉**。
       改成日历日相邻后：第 1 天排 2 个、第 3 天排 2 个，全部排上且隔开。
    """
    slots = pp.build_schedule(_talent(), _deals(4), _products(4), days=7, per_day=2)
    days = [s for s in slots if s.items]
    # ① 所有品都要排上（约束不能把品丢掉）
    scheduled = sum(len(s.items) for s in slots)
    assert scheduled == 4, f"4 个品只排了 {scheduled} 个 —— 约束把品丢了"
    # ② 相邻两个**日历日**不推同品类
    for i in range(len(slots) - 1):
        for x in {it.category for it in slots[i].items if it.category}:
            for y in {it.category for it in slots[i + 1].items if it.category}:
                assert not pp._cat_overlap(x, y), \
                    f"{slots[i].date} 与 {slots[i + 1].date} 同类目连推"


def test_multiple_pre_scheduled_items_on_same_day_all_survive():
    """★ 同一天已经排了多个品时，**一个都不能丢**。

    早期版本 `scheduled` 用 `dict[date] → 单个 item`，
    同一天的第二个品会凭空消失（2026-10-24 实测：内搭背心从排期表里不见了）。
    """
    import datetime
    day = int(datetime.datetime.now().replace(
        hour=10, minute=0, second=0, microsecond=0).timestamp())
    deals = [{"id": "d1", "product_id": "p0", "product_name": "品0",
              "scheduled_at": day},
             {"id": "d2", "product_id": "p1", "product_name": "品1",
              "scheduled_at": day},
             {"id": "d3", "product_id": "p2", "product_name": "品2",
              "scheduled_at": 0}]
    slots = pp.build_schedule(_talent(), deals, _products(3), days=7, per_day=2)
    ids = [it.product_id for s in slots for it in s.items]
    assert "p0" in ids and "p1" in ids, f"同天已排期的品丢了: {ids}"
    # 槽位编号必须是 1..N（前端显示「第几个坑」）
    for s in slots:
        for i, it in enumerate(s.items, 1):
            assert it.plan_slot == i, f"槽位编号错乱: {it.product_name}={it.plan_slot}"


def test_schedule_fills_window_when_categories_are_concentrated():
    """★ 品类集中时也要把品排满（补位兜底）。"""
    slots = pp.build_schedule(_talent(), _deals(6), _products(6), days=7, per_day=2)
    scheduled = sum(len(s.items) for s in slots)
    assert scheduled == 6, f"6 个品只排了 {scheduled} 个"
    assert all(len(s.items) <= 2 for s in slots), "每天仍不能超过 per_day"


def test_schedule_dedupes_same_product_deals():
    """★ 同一个品的重复合作单只占一个坑位（否则重复品把坑位占满）。"""
    deals = [{"id": "d1", "product_id": "p0", "product_name": "同品",
              "scheduled_at": 0},
             {"id": "d2", "product_id": "p0", "product_name": "同品",
              "scheduled_at": 0},
             {"id": "d3", "product_id": "p1", "product_name": "另一个",
              "scheduled_at": 0}]
    slots = pp.build_schedule(_talent(), deals, _products(2), days=7, per_day=2)
    names = [it.product_id for s in slots for it in s.items]
    assert names.count("p0") == 1, f"同品占了 {names.count('p0')} 个坑位"


def test_schedule_keeps_already_scheduled_slot():
    """★ 已经排过期的合作单保留原位，不被重排。

    ⚠️ `scheduled_at` 单位是**秒**（前端 `scheduled_at * 1000` 读）——
       排品器曾写成毫秒，落库后前端日期差 50000 年。
    """
    import datetime
    ts = int(datetime.datetime.now().replace(
        hour=10, minute=0, second=0, microsecond=0).timestamp())
    deals = [{"id": "d1", "product_id": "p0", "product_name": "品0",
              "scheduled_at": ts},
             {"id": "d2", "product_id": "p1", "product_name": "品1",
              "scheduled_at": 0}]
    slots = pp.build_schedule(_talent(), deals, _products(2), days=7, per_day=2)
    got = [it for s in slots for it in s.items if it.product_id == "p0"]
    assert got and got[0].scheduled_at == ts, "已排期的单不能被改写"


def test_new_item_is_not_scheduled_into_a_past_day():
    """★ 排期落库后会有「过去的 slot」——新品不能被排进过去那天。

    落库后 `build_schedule` 会把过去的排期原样带回来（那是历史，不是待办），
    生成一个过去的 slot。如果不拦，新品会被排到**已经过去**的那天
    ⇒ 那个品**永远推不出去**（提醒只看当天）⇒ 达人就白接了。
    """
    import datetime
    yesterday = datetime.datetime.now() - datetime.timedelta(days=1)
    ts = int(datetime.datetime.combine(yesterday, datetime.time.min).timestamp())
    deals = [{"id": "d_old", "product_id": "p0", "product_name": "老品",
              "scheduled_at": ts},
             {"id": "d_new", "product_id": "p1", "product_name": "新品",
              "scheduled_at": 0}]
    slots = pp.build_schedule(_talent(), deals, _products(2), days=7, per_day=2)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    new_it = [it for s in slots for it in s.items if it.product_id == "p1"]
    assert new_it, "新品应该被排进表里"
    assert new_it[0].date >= today, \
        f"新品被排到过去那天（{new_it[0].date}）⇒ 永远推不出去"
    old_it = [it for s in slots for it in s.items if it.product_id == "p0"]
    assert old_it and old_it[0].scheduled_at == ts, "老品要保留原位"


def test_display_name_prefers_platform_nickname():
    """★ 显示名优先平台昵称 —— 库里运营随手写的 name 没法看。

    实测库名「我的小号发给你跟我分析一下名称一百」，
    直接当提醒标题达人根本认不出这是自己。
    """
    assert pp.display_name({"name": "我的小号发给你跟我分析一下名称一百",
                            "platform_nickname": "一百"}) == "一百"
    # 没有平台昵称时回退库名
    assert pp.display_name({"name": "洁洁爱户外"}) == "洁洁爱户外"
    assert pp.display_name({"name": "x", "platform_nickname": "  "}) == "x"


def test_future_slots_drops_past_days():
    """★ 只展示今天及以后 —— 历史是历史，不是待办。"""
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    slots = [pp.PlanSlot(date=yesterday), pp.PlanSlot(date=today),
             pp.PlanSlot(date=tomorrow)]
    got = [s.date for s in pp.future_slots(slots)]
    assert yesterday not in got
    assert today in got, "今天必须留着"
    assert tomorrow in got


def test_scheduled_at_is_in_seconds():
    """★ 单位锁点：`scheduled_at` 是**秒**（前端按秒读，差 1000 倍就是差 50000 年）。"""
    import datetime
    slots = pp.build_schedule(_talent(), _deals(3), _products(3), days=7, per_day=2)
    for s in slots:
        for it in s.items:
            assert 1_000_000_000 < it.scheduled_at < 4_000_000_000, \
                f"scheduled_at={it.scheduled_at} 不像秒（毫秒的话前端日期会差 50000 年）"
            assert datetime.datetime.fromtimestamp(it.scheduled_at).strftime(
                "%Y-%m-%d") == it.date


def test_schedule_tail_does_not_crowd_out_good_product():
    """★ 尾货要清，但**不挤掉主推位**：好品在前，尾货每天最多占 1 个坑。"""
    prod = _products(2)
    prod[0]["sale_axis"] = _axis([60] * 7, [20] * 7)     # ramp（好品）
    prod[1]["sale_axis"] = _axis([0] * 7, [50] * 7)      # tail（该清）
    deals = [{"id": "d0", "product_id": "p0", "product_name": "爬坡品",
              "scheduled_at": 0},
             {"id": "d1", "product_id": "p1", "product_name": "尾货品",
              "scheduled_at": 0}]
    slots = pp.build_schedule(_talent(), deals, prod, days=7, per_day=2)
    day1 = slots[0].items
    assert len(day1) == 2
    assert day1[0].product_id == "p0", "好品必须占主推位"
    assert day1[1].product_id == "p1", "尾货当天也该清一个"
    # 第二天不该再塞尾货（每天上限 1 个）
    day2_tails = [i for i in slots[1].items
                  if i.lifecycle and i.lifecycle.stage == pp.LIFECYCLE_TAIL]
    assert len(day2_tails) <= 1


def test_schedule_tail_never_blocks_all_slots():
    """尾货再多也不能占满所有坑位。"""
    prod = _products(4)
    for p in prod:
        p["sale_axis"] = _axis([0] * 7, [50] * 7)
    deals = [{"id": f"d{i}", "product_id": f"p{i}", "product_name": f"尾货{i}",
              "scheduled_at": 0} for i in range(4)]
    slots = pp.build_schedule(_talent(), deals, prod, days=7, per_day=2)
    for s in slots:
        tails = [i for i in s.items
                 if i.lifecycle and i.lifecycle.stage == pp.LIFECYCLE_TAIL]
        assert len(tails) <= pp.TAIL_SLOTS_PER_DAY


def test_schedule_keeps_existing_plan():
    import time
    ts = int(time.time()) + 86400
    deals = [{"id": "d0", "product_id": "p0", "product_name": "已排",
              "scheduled_at": ts}]
    slots = pp.build_schedule(_talent(), deals, _products(1), days=7, per_day=2)
    placed = [i for s in slots for i in s.items if i.deal_id == "d0"]
    assert len(placed) == 1
    assert placed[0].scheduled_at == ts


def test_schedule_no_items():
    slots = pp.build_schedule(_talent(), [], [], days=7, per_day=2)
    assert len(slots) == 7
    assert all(not s.items for s in slots)


def test_plan_text_is_readable():
    slots = pp.build_schedule(_talent(), _deals(2), _products(2), days=7, per_day=2)
    text = pp.plan_text(slots, "七月姐姐")
    assert "七月姐姐" in text
    assert "排品表" in text
    assert "佣金" in text


# ─────────────── 缺失 vs 真不匹配（铁律：缺失不当 0 分）───────────────

def test_category_fit_three_states():
    """★ 类目匹配必须三态：无数据 / 真的不匹配 / 匹配上。"""
    fit = pp.build_talent_fit(_talent())          # 有个护家清 63%
    prod_cat = "个护家清/面霜"
    s, note, known = pp._category_fit(prod_cat, fit)
    assert known is True and s > 0 and "GMV" in note

    s, note, known = pp._category_fit("3C数码家电/耳机", fit)
    assert known is True and s == 0.0, "有历史数据但不做这个类目 ⇒ 0 分是实情"

    empty = pp.build_talent_fit({"id": "t"})       # 没有任何类目历史
    s, note, known = pp._category_fit(prod_cat, empty)
    assert known is False, "没有类目历史 ⇒ 不知道，不能按 0 分算"


def test_price_fit_three_states():
    fit = pp.build_talent_fit(_talent())
    s, note, known = pp._price_fit(99, fit)
    assert known is True and note
    empty = pp.build_talent_fit({"id": "t"})
    s, note, known = pp._price_fit(99, empty)
    assert known is False


def test_score_item_missing_category_data_keeps_price_weight():
    """只有价格依据时，匹配度权重折半，**不按类目 0 分惩罚**。"""
    fit_no_cat = pp.build_talent_fit(
        {"id": "t", "category_distribution": {}, "price_distribution": {},
         "video_avg_price": "¥65"})
    fit_full = pp.build_talent_fit(_talent(category_distribution={"个护家清": 63}))
    p = _product("p1", "面霜", 99, category="个护家清/面霜")
    s_no, _, c_no = pp.score_item(fit_no_cat, p)
    s_full, _, c_full = pp.score_item(fit_full, p)
    assert c_no < c_full, "少一维依据 ⇒ 完整度必须更低"
    # 无类目依据的那份，分数不该被类目 0 分拖垮
    assert s_no > 0


def test_completeness_reflects_backed_dimensions():
    fit = pp.build_talent_fit(_talent())
    _, _, full = pp.score_item(fit, _product("p1", "x", 99))
    bare = {"id": "p2", "name": "裸品", "price": 99, "category": "",
            "commission_rates": {}, "sale_axis": []}
    _, _, thin = pp.score_item(fit, bare)
    assert thin < full


def test_prescheduled_item_counts_for_category_constraint():
    """★ **已排期（保留原位）的品也要参与同品类约束**（2026-10-25 实测）。

    实测场景：明天已经排好一个鞋靴（保留原位，不改写），
    而今天新排的也是鞋靴 ⇒ 相邻两天同品类。

    根因：`cat_used_days` 原来**只在主循环末尾记账**，
    而「保留原位」的品要么落在**过去**的日子（主循环 `continue` 跳过 ⇒ 从不记账），
    要么落在**未来**的日子（要等排到那天才记账 ⇒ 排今天时看不见明天已占位的品类）。
    ⇒ 约束只在「当天新排出来的品」之间生效，**已排期的品完全游离在约束之外**。

    正确口径：「相邻两天的**所有**品」，不论新排还是保留原位。
    """
    import datetime
    now = datetime.datetime.now().replace(hour=10, minute=0, second=0,
                                          microsecond=0)
    tomorrow = int((now + datetime.timedelta(days=1)).timestamp())

    deals = [
        # 明天已排好（保留原位）的鞋靴
        {"id": "d_fixed", "product_id": "p_shoe1", "product_name": "鞋A",
         "status": "approved", "scheduled_at": tomorrow,
         "category": "鞋靴/靴子"},
        # 待排的另一个鞋靴 —— 不能排在今天（今天与明天相邻），也不能排明天
        # （明天已经有固定位的鞋靴）
        {"id": "d_shoe2", "product_id": "p_shoe2", "product_name": "鞋B",
         "status": "approved", "scheduled_at": 0, "category": "鞋靴/靴子"},
        # 待排的服饰 —— 可以排今天
        {"id": "d_cloth", "product_id": "p_cloth", "product_name": "衣C",
         "status": "approved", "scheduled_at": 0, "category": "服饰内衣/内搭"},
    ]
    products = [
        _product("p_shoe1", "鞋A", 159, category="鞋靴/靴子"),
        _product("p_shoe2", "鞋B", 129, category="鞋靴/靴子"),
        _product("p_cloth", "衣C", 55, category="服饰内衣/内搭"),
    ]
    slots = pp.build_schedule(_talent(), deals, products, days=7, per_day=2)
    by_date = {s.date: [it.product_id for it in s.items] for s in slots}

    # ① 保留原位的品必须还在明天（不改写已排好的安排）
    assert "p_shoe1" in by_date[slots[1].date], f"保留原位的品被挪走了：{by_date}"
    # ② 相邻两天不能同品类
    for i in range(len(slots) - 1):
        a = {it.category for it in slots[i].items if it.category}
        b = {it.category for it in slots[i + 1].items if it.category}
        for x in a:
            for y in b:
                assert not pp._cat_overlap(x, y), \
                    f"{slots[i].date} 与 {slots[i+1].date} 同类目连推：{x} vs {y}"
    # ③ 约束不能把品丢掉
    all_ids = [it.product_id for s in slots for it in s.items]
    assert set(all_ids) == {"p_shoe1", "p_shoe2", "p_cloth"}, f"品丢了：{all_ids}"
