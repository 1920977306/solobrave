#!/usr/bin/env python3
"""排品表 + 提醒（达人视角：今天推什么、为什么、哪个该清了）。

问题（老大 2026-10-24）
----------------------
「AI 助手一直是围绕着自己的达人账号做分析的」——
账号分析、选品建议、商品判断都有，但**达人接了品之后怎么排、什么时候推、什么时候提醒**，
这一条完全空着。达人每天接十几个品，不排就会：
① 爆款期错过（品热了才推得动）；
② 尾货堆积（不赶紧推就废了）；
③ 一天推太多（视频位有限，观众疲劳）。

做什么
------
    达人接的品（deals, status=approved）
      → 商品生命周期（sale_axis 日销量轴的斜率）
      → 排品优先级（佣金 × 粉丝匹配度 × 时机 × 商品质量）
      → 时间安排表（每天 N 个、同品类不连推、尾货优先清）
      → 每日提醒（今天推哪两个、为什么、哪个该清了）

三条设计铁律
------------
1. **只丢不造** —— 排品依据全部来自真值（达人历史带货结构 + 达人端商品维度）。
   拿不到依据的维度**退出加权并重新归一化**，不拿 0 冒充（和 `product_reco` 同款纪律）。
2. **可解释** —— 每个推荐都带「为什么推它」的理由行，理由里带**具体数字**
   （佣金率 20%、该类目占你 GMV 72%、近7日销量 +38%）。达人/运营看了才敢信。
3. **提醒幂等** —— 同一天同一个品只提醒一次；推送失败不阻塞下一次扫描。

用法
----
    from daren_cockpit.tools.product_plan import build_schedule, plan_text
    slots = build_schedule(talent_row, deal_rows, product_rows, days=7, per_day=2)
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# ★ 商品事实解析（佣金率/到手价）的**单一真源** ——
#   和 `product_reco` 共用一份口径，避免两个评分器对同一商品读出不同佣金
#   （分档佣金结构曾导致 5/58 个商品的收益维度被静默丢弃）。
#   ⚠️ 用绝对导入 + sys.path 兜底：本模块既被当包内模块导入
#   （服务端 / daren_bot），也被测试按**文件路径**直接加载 —— 相对导入会炸。
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from daren_cockpit.tools import product_facts as facts  # noqa: E402

__all__ = [
    "Lifecycle", "PlanItem", "PlanSlot",
    "product_lifecycle", "score_item", "build_schedule", "plan_text",
    "LIFECYCLE_RAMP", "LIFECYCLE_PEAK", "LIFECYCLE_DECLINE",
    "LIFECYCLE_TAIL", "LIFECYCLE_UNKNOWN",
]


# ══════════════════════════════ 生命周期 ══════════════════════════════

LIFECYCLE_RAMP = "ramp"          # 爬坡期：销量在涨，现在推正是时候
LIFECYCLE_PEAK = "peak"          # 爆款期：销量高位，能推得动
LIFECYCLE_DECLINE = "decline"    # 衰退期：还在跌，能推但要快
LIFECYCLE_TAIL = "tail"          # 尾货期：卖不动了，再不推就废了
LIFECYCLE_UNKNOWN = "unknown"    # 没有销量轴，判不了

LIFECYCLE_LABEL = {
    LIFECYCLE_RAMP: "爬坡期",
    LIFECYCLE_PEAK: "爆款期",
    LIFECYCLE_DECLINE: "衰退期",
    LIFECYCLE_TAIL: "尾货期",
    LIFECYCLE_UNKNOWN: "销量未知",
}

# 生命周期的时机分（timing score 0..1）
TIMING_SCORE = {
    LIFECYCLE_RAMP: 1.0,      # 正在涨 ⇒ 推的最佳时机
    LIFECYCLE_PEAK: 0.85,     # 高位稳定
    LIFECYCLE_DECLINE: 0.5,   # 在跌 ⇒ 还能推，但要快
    LIFECYCLE_TAIL: 0.15,     # 基本卖不动 ⇒ 只能当清库存
    LIFECYCLE_UNKNOWN: 0.5,   # 判不了 ⇒ 中性
}

# 绝对量闸门：日均不到这个数（件）⇒ 不管涨跌都算「基本没动销」
MIN_DAILY_MOVING = 1.0

# 尾货每天最多占几个坑位 —— 清库存，但不无限期挤占主推位
TAIL_SLOTS_PER_DAY = 1


@dataclass
class Lifecycle:
    """商品的热度生命周期（由达人端 30 天日销量轴推出来的）。"""

    stage: str = LIFECYCLE_UNKNOWN
    recent7: int = 0        # 近 7 天销量
    prev7: int = 0          # 前 7 天销量
    change_pct: float = 0.0  # 近7 vs 前7 的变化率（%）
    daily_avg: float = 0.0
    peak_daily: float = 0.0
    note: str = ""

    @property
    def label(self) -> str:
        return LIFECYCLE_LABEL.get(self.stage, self.stage)

    def to_dict(self) -> dict:
        return {"stage": self.stage, "label": self.label, "recent7": self.recent7,
                "prev7": self.prev7, "change_pct": round(self.change_pct, 1),
                "daily_avg": round(self.daily_avg, 1),
                "peak_daily": round(self.peak_daily, 1), "note": self.note}


def _axis_points(sale_axis: Any) -> list[tuple[str, float]]:
    """日销量轴 → [(日期, 销量)]（按日期升序，脏点丢掉）。"""
    pts: list[tuple[str, float]] = []
    for p in (sale_axis or []):
        if not isinstance(p, dict):
            continue
        d = str(p.get("date") or p.get("x") or "")
        if not d:
            continue
        q = p.get("qty", p.get("y"))
        try:
            q = float(q or 0)
        except (TypeError, ValueError):
            continue
        pts.append((d, q))
    pts.sort(key=lambda kv: kv[0])
    return pts


def product_lifecycle(sale_axis: Any) -> Lifecycle:
    """日销量轴 → 生命周期。

    判定口径（近 7 天 vs 前 7 天）：
      · 后 7 天均值 ≥ 前 7 天的 1.2 倍          → **爬坡期**（正在涨）
      · 后 7 天均值 < 前 7 天的 0.8 倍
          且后 7 天均值 < 峰值日销的 20%         → **尾货期**（该清）
          否则                                     → **衰退期**
      · 其余                                     → **爆款期**
    ⚠️ 阈值是按真实数据校准的（见 `tests/test_product_plan.py` 的校准用例），
      改这里要重跑校准回归。
    """
    pts = _axis_points(sale_axis)
    if len(pts) < 4:
        return Lifecycle(stage=LIFECYCLE_UNKNOWN,
                         note="销量数据不足，判不了热度")
    recent = pts[-7:]
    prev = pts[-14:-7] if len(pts) >= 14 else pts[:-7] or pts
    r_sum = sum(v for _, v in recent)
    p_sum = sum(v for _, v in prev)
    r_avg = r_sum / len(recent) if recent else 0.0
    p_avg = p_sum / len(prev) if prev else 0.0
    peak = max(v for _, v in pts)

    change = ((r_avg - p_avg) / p_avg * 100.0) if p_avg > 0 else 0.0
    lc = Lifecycle(stage=LIFECYCLE_UNKNOWN, recent7=int(round(r_sum)),
                   prev7=int(round(p_sum)), change_pct=change,
                   daily_avg=r_avg, peak_daily=peak)

    if r_avg <= 0:
        lc.stage = LIFECYCLE_TAIL
        lc.note = f"近7天零销量（峰值日销 {peak:.0f}）—— 该清了"
        return lc
    # ⚠️ **绝对量闸门**：日均不到 1 件的品，不管涨跌都算「基本没动销」，
    #   不能因为「从 0 涨到 1」就叫爬坡期（实测有品近7天只卖 1 件）。
    if r_avg < MIN_DAILY_MOVING:
        lc.stage = LIFECYCLE_TAIL
        lc.note = (f"近7天日均 {r_avg:.1f} 件，基本没动销"
                   f"（峰值日销 {peak:.0f}）—— 该清了")
        return lc
    if p_avg <= 0:
        # 前 7 天没销量、后 7 天有 ⇒ 新品起量
        lc.stage = LIFECYCLE_RAMP
        lc.note = f"新起量：近7天日均 {r_avg:.0f}（前7天无销量）—— 推正是时候"
        return lc
    if r_avg >= p_avg * 1.2:
        lc.stage = LIFECYCLE_RAMP
        lc.note = (f"近7天日均 {r_avg:.0f}，比前7天涨 {change:.0f}%"
                   f"—— 正在爬坡，现在推最好")
    elif r_avg < p_avg * 0.8:
        if peak > 0 and r_avg < peak * 0.2:
            lc.stage = LIFECYCLE_TAIL
            lc.note = (f"近7天日均 {r_avg:.0f}，只剩峰值的 "
                       f"{r_avg / peak * 100:.0f}% —— 尾货，尽快清")
        else:
            lc.stage = LIFECYCLE_DECLINE
            lc.note = (f"近7天日均 {r_avg:.0f}，比前7天降 {abs(change):.0f}%"
                       f" —— 还在跌，能推要快")
    else:
        lc.stage = LIFECYCLE_PEAK
        lc.note = (f"近7天日均 {r_avg:.0f}，和前7天基本持平"
                   f"（{change:+.0f}%）—— 稳定出货期")
    return lc


# ══════════════════════════════ 达人画像 ══════════════════════════════

@dataclass
class TalentFit:
    """达人侧「接得住什么」的画像（来自历史带货结构）。"""

    category_share: dict[str, float] = field(default_factory=dict)  # 类目 GMV 占比 %
    price_buckets: dict[str, float] = field(default_factory=dict)   # 价格带 GMV
    avg_price: float = 0.0
    video_avg_price: float = 0.0
    top_category: str = ""
    categories_with_price: dict[str, float] = field(default_factory=dict)  # 类目 → 该类目均价
    sample_size: int = 0

    def note_missing(self) -> list[str]:
        miss = []
        if not self.category_share:
            miss.append("品类结构")
        if not self.price_buckets and not self.avg_price:
            miss.append("价格带")
        return miss


def _as_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return out if out is not None else default


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if isinstance(value, bool):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _yuan(text: Any) -> float:
    """'¥38.71' / '38.71元' / 38.71 → 数值。"""
    if isinstance(text, (int, float)):
        return float(text)
    if not text:
        return 0.0
    m = re.search(r"[\d.]+", str(text).replace(",", ""))
    return float(m.group()) if m else 0.0


def build_talent_fit(row: dict | Any) -> TalentFit:
    """`talents` 表一行 → 达人接货画像（只从真值里取，不猜）。"""
    r = dict(row) if isinstance(row, dict) else {c[0]: row[c[0]] for c in row.keys()}
    fit = TalentFit()

    dist = _as_json(r.get("category_distribution"), {})
    if isinstance(dist, dict):
        fit.category_share = {str(k): _num(v) for k, v in dist.items() if _num(v) > 0}
    if not fit.category_share:
        tops = _as_json(r.get("top_categories"), [])
        if isinstance(tops, list):
            for t in tops:
                if not isinstance(t, dict):
                    continue
                name = str(t.get("name") or "")
                if not name:
                    continue
                ratio = _num(t.get("ratio"))
                if ratio <= 0:
                    # 没有 ratio 就用 GMV 档位折算个相对占比（只用于排序，不当绝对值）
                    gmv = str(t.get("gmv") or "")
                    ratio = _gmv_bucket_rank(gmv)
                if ratio > 0:
                    fit.category_share[name] = ratio
                fit.categories_with_price[name] = _yuan(t.get("avg_price"))

    pd = _as_json(r.get("price_distribution"), {})
    if isinstance(pd, dict):
        fit.price_buckets = {str(k): _num(v) for k, v in pd.items() if _num(v) > 0}
    fit.avg_price = _num(r.get("average_price"))
    fit.video_avg_price = _yuan(r.get("video_avg_price"))
    if fit.category_share:
        fit.top_category = max(fit.category_share.items(), key=lambda kv: kv[1])[0]
    fit.sample_size = int(_num(r.get("total_products")))
    return fit


def _gmv_bucket_rank(gmv_text: str) -> float:
    """GMV 档位字符串 → 相对权重（用于同类目排序，不是绝对占比）。"""
    s = str(gmv_text or "")
    m = re.search(r"([\d.]+)\s*万", s)
    if m:
        return float(m.group(1)) * 10000
    m = re.search(r"([\d.]+)\s*亿", s)
    if m:
        return float(m.group(1)) * 100000000
    m = re.search(r"([\d.]+)", s)
    return float(m.group(1)) if m else 0.0


def _bucket_of(price: float) -> str:
    """价格 → 价格带标签（与 `price_distribution` 的键同口径）。"""
    if price <= 0:
        return ""
    for hi, name in ((25, "0~25"), (50, "25~50"), (100, "50~100"),
                     (200, "100~200"), (500, "200~500")):
        if price <= hi:
            return name
    return "500+"


# ══════════════════════════════ 排品优先级 ══════════════════════════════

@dataclass
class PlanItem:
    """一个待推品 + 它的排品依据。"""

    deal_id: str = ""
    product_id: str = ""
    product_name: str = ""
    price: float = 0.0
    commission_rate: float = 0.0
    priority: float = 0.0
    reasons: list[str] = field(default_factory=list)
    lifecycle: Optional[Lifecycle] = None
    category: str = ""
    scheduled_at: int = 0
    plan_slot: int = 0
    date: str = ""
    completeness: float = 0.0

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id, "product_id": self.product_id,
            "product_name": self.product_name, "price": self.price,
            "commission_rate": self.commission_rate,
            "priority": round(self.priority, 1), "reasons": self.reasons,
            "lifecycle": self.lifecycle.to_dict() if self.lifecycle else None,
            "category": self.category, "scheduled_at": self.scheduled_at,
            "plan_slot": self.plan_slot, "date": self.date,
            "completeness": round(self.completeness, 3),
        }


def _commission_rate(product: dict) -> float:
    """佣金率（%）。

    ★ 解析规则见 `product_facts.commission_rate_of`（单一真源，和推荐器共用）：
      · 独立列 `commission_rate`
      · `commission_rates` JSON 的标量键（default/rate/commission_rate）
      · **分档结构**（{"投放期": 10, "常规活动期": 5, "其他": 5}）→ 取最高档
    都取不到 → 0.0（**缺失**，该维度退出加权，不拿 0 当真值）。
    """
    return facts.commission_rate_of(product)


def _price_of(product: dict) -> float:
    """到手价（★ 口径见 `product_facts.price_of`，和推荐器共用一份）。"""
    return facts.price_of(product)


def _product_category(product: dict) -> str:
    return str(product.get("category") or "").strip()


def _cat_first(category: str) -> str:
    return (category or "").replace("，", "/").split("/")[0].strip()


def _cat_overlap(a: str, b: str) -> bool:
    """类目是否同一（层级/前缀同义都算，见 §8.3 口径纪律）。"""
    if not a or not b:
        return False
    if a == b:
        return True
    fa, fb = _cat_first(a), _cat_first(b)
    if fa == fb:
        return True
    if len(fa) >= 2 and len(fb) >= 2 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    sa = {x.strip() for x in a.replace("，", "/").split("/") if x.strip()}
    sb = {x.strip() for x in b.replace("，", "/").split("/") if x.strip()}
    return bool(sa & sb)


def _category_fit(product_cat: str, fit: TalentFit) -> tuple[float, str, bool]:
    """商品类目 vs 达人品类结构 → (契合度 0..1, 理由, **是否有依据**)。

    ★ 三态（铁律：缺失 ≠ 0 分）：
      · 无类目数据            → (0, '', False)  **不知道**，该维度退出加权
      · 有类目数据但没这个类目 → (0, '', True)   **真的不匹配**，0 分是实情
      · 匹配上了              → (ratio, 理由, True)
    """
    if not product_cat:
        return 0.0, "", False
    if not fit.category_share:
        return 0.0, "", False
    best, best_name = 0.0, ""
    for name, share in fit.category_share.items():
        if _cat_overlap(product_cat, name):
            if share > best:
                best, best_name = share, name
    if not best_name:
        return 0.0, "", True          # 有历史数据，确实不做这个类目
    total = sum(fit.category_share.values()) or 1.0
    ratio = min(1.0, best / total) if total > 0 else 0.0
    return ratio, f"{best_name}占你历史GMV {best:.0f}%", True


def _price_fit(price: float, fit: TalentFit) -> tuple[float, str, bool]:
    """商品到手价 vs 达人价格带结构 → (契合度 0..1, 理由, 是否有依据)。"""
    if price <= 0:
        return 0.0, "", False
    if fit.price_buckets:
        bucket = _bucket_of(price)
        total = sum(fit.price_buckets.values()) or 1.0
        share = fit.price_buckets.get(bucket, 0.0) / total
        return min(1.0, share * 3), f"{bucket}元带占你历史GMV {share * 100:.0f}%", True
    center = fit.video_avg_price or fit.avg_price
    if center > 0:
        # 离均价越远越不契合（2 倍以外基本接不住）
        ratio = price / center
        score = 1.0 if 0.6 <= ratio <= 1.6 else max(0.0, 1.0 - abs(ratio - 1.0) / 1.5)
        return score, f"你的视频均价 ¥{center:.0f}，这个品 ¥{price:.0f}", True
    return 0.0, "", False


def _profit_score(product: dict, commission_rate: float) -> tuple[float, str]:
    """佣金率 → (收益分 0..1, 理由)。30% 封顶。

    ★ 理由用 `facts.commission_note` ⇒ 分档佣金会写明「佣金率最高 10%（投放期）」，
      不糊成一句「佣金率 10%」（分档场景下那是误导）。
    """
    if commission_rate <= 0:
        return 0.0, ""
    note = facts.commission_note(product) or f"佣金率 {commission_rate:g}%"
    return min(1.0, commission_rate / 30.0), note


def _quality_score(product: dict) -> tuple[float, str]:
    """店铺体验分 + 好评率 → (质量分 0..1, 理由)。差品该拦就别推。"""
    parts, notes = [], []
    score = 0.0
    ss = _num(product.get("shop_score"))
    if ss > 0:
        score += min(1.0, ss / 100.0) * 0.5
        notes.append(f"店铺体验分 {ss:g}")
    gr = _num(product.get("good_ratio"))
    if gr > 0:
        score += min(1.0, gr / 100.0) * 0.5
        notes.append(f"好评率 {gr:g}%")
    if not notes:
        return 0.0, ""
    return score, "、".join(notes)


def score_item(talent_fit: TalentFit, product: dict,
               lifecycle: Optional[Lifecycle] = None) -> tuple[float, list[str], float]:
    """一个品 → (优先级 0..100, 理由行, 数据完整度)。

    权重：匹配度 40% / 收益 35% / 时机 25%
    ⚠️ 缺失的维度**退出加权并重新归一化**，不拿 0 分冒充。
    """
    price = _price_of(product)
    commission = _commission_rate(product)
    cat = _product_category(product)
    lc = lifecycle or product_lifecycle(product.get("sale_axis") or
                                         _as_json(product.get("sale_axis_json"), []))

    fit_score, fit_note, fit_known = _category_fit(cat, talent_fit)
    price_score, price_note, price_known = _price_fit(price, talent_fit)
    # 匹配度 = **有依据的那几项**的平均（缺的那半不按 0 算）
    known_parts = []
    if fit_known:
        known_parts.append(fit_score)
    if price_known:
        known_parts.append(price_score)
    match_score = (sum(known_parts) / len(known_parts)) if known_parts else 0.0
    match_note = "、".join(x for x in (fit_note, price_note) if x)

    profit_score, profit_note = _profit_score(product, commission)
    timing_score = TIMING_SCORE.get(lc.stage, 0.5)
    quality_score, quality_note = _quality_score(product)

    weighted, used = 0.0, 0.0
    if known_parts:
        # 匹配度权重按「有几项有依据」折算（只有价格依据 ⇒ 0.40 → 0.20）
        weighted += 0.40 * (len(known_parts) / 2.0) * match_score
        used += 0.40 * (len(known_parts) / 2.0)
    if profit_score > 0:
        weighted += 0.35 * profit_score
        used += 0.35
    if lc.stage != LIFECYCLE_UNKNOWN:
        weighted += 0.25 * timing_score
        used += 0.25
    # 质量只做**扣分项**（差品压后），不占正权重 —— 没数据时不惩罚
    if quality_score > 0:
        weighted *= (0.7 + 0.3 * quality_score)
    priority = 100.0 * (weighted / used) if used > 0 else 0.0

    reasons = [x for x in (profit_note, match_note, lc.note, quality_note) if x]
    completeness = used / 1.0
    return priority, reasons, completeness


# ══════════════════════════════ 排期 ══════════════════════════════

@dataclass
class PlanSlot:
    """一天。"""

    date: str = ""
    items: list[PlanItem] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"date": self.date,
                "items": [i.to_dict() for i in self.items]}


def _fmt_date(ts: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _day_start(ts: int) -> int:
    import datetime
    d = datetime.datetime.fromtimestamp(ts)
    return int(datetime.datetime(d.year, d.month, d.day).timestamp())


def _prev_day(date_str: str) -> str:
    """'2026-10-24' → '2026-10-23'（日历前一天）。"""
    import datetime
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    return (d - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def build_schedule(talent_row: dict, deal_rows: list[dict], product_rows: list[dict],
                   days: int = 7, per_day: int = 2,
                   start_at: int = 0) -> list[PlanSlot]:
    """达人 + 待推品 → 时间安排表。

    约束（按老大的场景「达人每天接很多品」定）：
      ① 每天最多 `per_day` 个（默认 2）—— 视频位有限，观众会疲劳；
      ② 同品类**不连推**（跨天也不连着推同一个类目）；
      ③ **尾货优先清**（占前面的坑位）；
      ④ 已经排过期的（`scheduled_at` 在今天之前）保留在原位，不改写。

    ★ 两条兜底（都是「只丢不造」的对偶面 —— **不能因为约束就把品丢掉**）：
      ⑤ **同品去重**：同一个达人 + 同一个品的重复合作单只占**一个**坑位
         （重复单会把坑位占满，等于别的品没得排）；
      ⑥ **破例补位**：坑位空着、但还有没排的品 ⇒ 补上（优先挑同品类
         「上一次出现得最早」的品，把重复拉得最开），并在理由里标注。
         没有这条，池子里品类一单一（实测：3 个品 2 个同类目），
         7 天表会只排得出 1 天，剩下的品永远「排不进本周」。
    """
    import datetime
    fit = build_talent_fit(talent_row)
    products = {str(p.get("id") or ""): p for p in product_rows if p and p.get("id")}

    now = int(start_at or (datetime.datetime.now().timestamp() * 1000))
    now_s = now / 1000.0

    # ── ⑤ 同品去重：同一个品的多张合作单只留一张（重复单会把坑位占满）──
    def _deal_rank(d: dict) -> tuple:
        """同品多单时挑哪张留下：已排期的 > 信息全的 > 先出现的。"""
        return (
            1 if _num(d.get("scheduled_at")) else 0,
            1 if (d.get("product_name") or "").strip() else 0,
            1 if _num(d.get("commission_rate")) else 0,
        )

    deduped: list[dict] = []
    by_product: dict[str, dict] = {}
    for d in (deal_rows or []):
        pid = str(d.get("product_id") or "")
        key = pid or f"__deal__{d.get('id')}"
        old = by_product.get(key)
        if old is None:
            by_product[key] = d
            deduped.append(d)
            continue
        if _deal_rank(d) > _deal_rank(old):
            by_product[key] = d
            for i, x in enumerate(deduped):
                if x is old:
                    deduped[i] = d
                    break
        # 其余情况：保留先出现的那张（deduped 顺序即稳定顺序）

    scored: list[PlanItem] = []
    for d in deduped:
        pid = str(d.get("product_id") or "")
        product = products.get(pid) or {}
        if not product and d.get("product_name"):
            # deal 里带了商品名但商品库里没有 ⇒ 只能按名字/佣金做粗排
            product = {"id": pid, "name": d.get("product_name"),
                       "price": 0, "commission_rates": {},
                       "category": d.get("category") or ""}
        life = product_lifecycle(product.get("sale_axis") or
                                 _as_json(product.get("sale_axis_json"), []))
        priority, reasons, completeness = score_item(fit, product, life)
        scored.append(PlanItem(
            deal_id=str(d.get("id") or ""),
            product_id=pid,
            product_name=str(product.get("name") or d.get("product_name") or ""),
            price=_price_of(product),
            commission_rate=_commission_rate(product),
            priority=priority, reasons=reasons, lifecycle=life,
            category=_product_category(product),
            completeness=completeness,
        ))

    # ── 已有排期的保持原位（不重排）──
    # ⚠️ 必须按天存**列表** —— 早期版本用 dict[date] → 单个 item，
    #    导致同一天排了 2 个品时**第二个直接丢失**（2026-10-24 踩过：
    #    内搭背心从排期表里凭空消失）
    scheduled: dict[str, list[PlanItem]] = {}
    for d in deduped:
        ts = _num(d.get("scheduled_at"))
        if ts <= 0:
            continue
        item = next((x for x in scored if x.deal_id == str(d.get("id") or "")), None)
        if item is None:
            continue
        item.scheduled_at = int(ts)
        item.date = _fmt_date(int(ts))
        scheduled.setdefault(item.date, []).append(item)
        if item not in scored:
            scored.append(item)

    # ── 未排期的排进未来 N 天 ──
    pending = [x for x in scored if x.scheduled_at <= 0]
    pending.sort(key=lambda x: (-x.priority, x.product_name))

    base = _day_start(int(now_s))
    slots: dict[str, PlanSlot] = {}
    for i in range(max(1, days)):
        dstr = _fmt_date(base + i * 86400)
        slots.setdefault(dstr, PlanSlot(date=dstr))
    for dstr, items in scheduled.items():
        slots.setdefault(dstr, PlanSlot(date=dstr))
        for item in items:
            if item not in slots[dstr].items:
                slots[dstr].items.append(item)

    def _is_tail(it: PlanItem) -> bool:
        return bool(it.lifecycle and it.lifecycle.stage == LIFECYCLE_TAIL)

    # 每天用过的类目（判断「昨天推没推过同品类」用）
    cat_used_days: dict[str, list[str]] = {}
    # 每个类目上一次出现的日期（补位时用来把重复拉得最开）
    cat_last_day: dict[str, str] = {}

    def _place(slot: PlanSlot, dstr: str, item: PlanItem) -> None:
        slot.items.append(item)
        item.date = dstr
        item.plan_slot = len(slot.items)
        # ⚠️ 单位是**秒**（和前端 `scheduled_at * 1000`、既有 deals 行一致）——
        #   这里曾写成毫秒，导致落库后前端日期差 50000 年（2026-10-24 踩过）
        item.scheduled_at = int(datetime.datetime.strptime(
            dstr, "%Y-%m-%d").timestamp())

    for dstr in sorted(slots):
        slot = slots[dstr]
        capacity = max(1, per_day)
        # ★ 同品类不连推 —— 口径是「**相邻两个日历日**」
        #   （不是「上一个有排品的日」：那个口径跨过空档也算连推，
        #    会让品类集中的池子死锁 —— 实测 4 个同类目的品、7 天、每天 2 坑，
        #    旧口径只排得出第 1 天，剩下的品永远「排不进本周」，
        #    而达人的诉求是「我接的这些品都要排上」）
        prev_cats = cat_used_days.get(_prev_day(dstr), [])
        # ① 先放非尾货（好品优先推，尾货不挤主推位）
        for want_tail in (False, True):
            tail_used = sum(1 for i in slot.items if _is_tail(i))
            for item in pending:
                if item.scheduled_at > 0:
                    continue
                if len(slot.items) >= capacity:
                    break
                if _is_tail(item) != want_tail:
                    continue
                # 尾货每天最多占 1 个坑位（清库存但不无限期占坑）
                if want_tail and tail_used >= TAIL_SLOTS_PER_DAY:
                    continue
                # 同类目不连推：和**昨天**用过的类目比
                if item.category and any(
                        _cat_overlap(item.category, c) for c in prev_cats):
                    continue
                _place(slot, dstr, item)
                if want_tail:
                    tail_used += 1
            if len(slot.items) >= capacity:
                break

        # ⑥ 破例补位：**先让严格规则把所有能排的排完**，还剩的才在这里补
        #   （窗口太紧时宁可同品类连排，也不能让达人的品「排不进本周」——
        #    典型场景：只剩 1 天但还剩 3 个品）
        #   ⚠️ 补位必须放在「所有天都排过一遍之后」——
        #    否则第 2 天就会把第 3 天的品抢过来（同类目挤成连续两天）。
        #   ★ 补位仍守：尾货每天最多 1 坑（不让尾货挤占主推位）
        if slot.items:
            for i in slot.items:
                if i.category:
                    cat_last_day[i.category] = dstr
                    cat_used_days.setdefault(dstr, []).append(i.category)

    # ── 补位阶段：严格规则排完还有剩 ⇒ 往空着的坑位里补 ──
    for dstr in sorted(slots):
        slot = slots[dstr]
        capacity = max(1, per_day)
        if len(slot.items) >= capacity:
            continue
        prev_cats = cat_used_days.get(_prev_day(dstr), [])
        while len(slot.items) < capacity:
            rest = [x for x in pending if x.scheduled_at <= 0]
            if not rest:
                break
            ok = [x for x in rest
                  if not (_is_tail(x) and sum(1 for i in slot.items
                                             if _is_tail(i)) >= TAIL_SLOTS_PER_DAY)]
            if not ok:
                break

            def _gap_key(x: PlanItem):
                last = cat_last_day.get(x.category or "", "")
                return (last or "", -x.priority, x.product_name)

            item = sorted(ok, key=_gap_key)[0]
            conflict = bool(item.category and any(
                _cat_overlap(item.category, c) for c in prev_cats))
            _place(slot, dstr, item)
            if conflict:
                item.reasons.append(
                    "同品类连排（窗口太紧，这批货品类集中，破例补位）")
            for i in slot.items:
                if i.category:
                    cat_last_day[i.category] = dstr
                    cat_used_days.setdefault(dstr, []).append(i.category)

    for item in pending:
        if item.scheduled_at <= 0:
            item.reasons.append("排不进本周（坑位满了）")

    out = [slots[k] for k in sorted(slots)]
    for slot in out:
        # 排序：已排期的按原槽位在前，其余按优先级；然后**重新编号** 1..N
        #   （已排期的品 plan_slot 是 0，不重编号的话前端「第几个坑」会显示成 0）
        slot.items.sort(key=lambda x: (x.plan_slot or 99, -x.priority,
                                       x.product_name))
        for i, it in enumerate(slot.items, 1):
            it.plan_slot = i
    return out


def plan_text(slots: list[PlanSlot], talent_name: str = "") -> str:
    """排期表 → 达人看得懂的一段话（飞书推送 / H5 都用这个）。"""
    lines: list[str] = []
    head = f"【{talent_name} 的排品表】" if talent_name else "【排品表】"
    lines.append(head)
    if not slots:
        lines.append("暂时没有可排的品。")
        return "\n".join(lines)
    for slot in slots:
        if not slot.items:
            continue
        lines.append(f"\n{slot.date}")
        for i, it in enumerate(slot.items, 1):
            stars = "".join("★" * int(round(it.priority / 20)) or "☆")
            price = f"¥{it.price:g}" if it.price else ""
            comm = f"佣金{it.commission_rate:g}%" if it.commission_rate else ""
            meta = " · ".join(x for x in (price, comm, it.lifecycle.label if it.lifecycle else "") if x)
            lines.append(f"  {i}. {it.product_name}（{meta}）")
            for r in (it.reasons or [])[:2]:
                lines.append(f"     · {r}")
    return "\n".join(lines)
