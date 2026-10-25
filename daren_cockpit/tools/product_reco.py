#!/usr/bin/env python3
"""选品推理链 + 推荐指数评分器（纯函数，不碰网络、不碰 DB）。

对齐的原型 = 抖音达人端「选品AI助手」的推荐链路：

    粉丝画像  →  类目策略  →  商品推荐  →  推荐指数(★★★★★)

⚠️ 达人端那个 ★★★★★ **是 AI 助手自己算的，不是平台真值**
   （2026-10-24 实测：达人端没有任何「推荐指数」接口）⇒ 我们自己算一套。

──────────────────────── 设计铁律 ────────────────────────

1. **缺失维度不当 0 分**
   达人端推荐理由一共 9 个维度，我们只拿得到其中一部分
   （转化率没有平台通用真值 —— `product_diagnose/*` 只诊断我们自己账号的商品，
   实测 total=0；`/pc/diagnose/get_total` 返回风控码 11001）。
   把「没采到」当成 0 分 = **用数据的缺失惩罚商品** ⇒ 降级做法是：
   该维度**不参与加权、权重重新归一化**，并在输出里标注「缺了几项 / 完整度多少」。

2. **星级用绝对分，不用相对分位**
   分位制在候选池 < 10 时，会把「个个都很差」的池子也排出 5★（假绿灯）。
   绝对分跨批次可比、可解释、可复现 ⇒ 星级由绝对分决定；
   同分时**用池内分位做排序 tie-breaker**（有区分度，但不改星级）。

3. **池内覆盖率闸门**
   任一维度在候选池里的覆盖率 < 60% ⇒ 该维度**整体不参与**本次打分。
   理由：过半商品缺这一项时，逐个商品重新归一化会让口径不一致
   （A 用 8 个维度算、B 用 9 个算，分数不可比）。宁可少用一个维度。

4. **只丢不造**
   画像缺关键字段（客单价 / 偏好类目）就明说「画像不足」，**不编粉丝画像**；
   商品缺平台真值就标 missing，**不拿 0 冒充**。

──────────────────────── 数据来源（全部达人端） ────────────────────────

商品维度（见 `daren_cockpit/tools/daren_dim_collector.py`）：
    到手价 / 佣金率 / 月销 / 日销量轴 / 店铺体验分 / 好评率 / 带货达人数 /
    榜单排名（仅上榜商品有）/ 近7日短视频销量（仅榜内商品有）
达人画像（`talents` 表）：年龄段 / 性别 / 客单价 / 偏好类目 / 带货经验

用法
----
    from daren_cockpit.tools.product_reco import recommend, build_profile
    chain = recommend(talent_row, product_rows, top_n=6)
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

# ★ 商品事实解析（佣金率/到手价）的**单一真源** ——
#   和 `product_plan` 共用一份口径，避免两个评分器对同一商品读出不同佣金
#   （分档佣金结构曾导致 5/58 个商品的收益维度被静默丢弃）。
#   ⚠️ 用绝对导入 + sys.path 兜底：本模块既被当包内模块导入
#   （服务端 `from daren_cockpit.tools import product_reco`），
#   也被测试按**文件路径**直接加载（spec_from_file_location）—— 相对导入会炸。
import os as _os
import sys as _sys

_ROOT = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
if _ROOT not in _sys.path:
    _sys.path.insert(0, _ROOT)
from daren_cockpit.tools import product_facts as facts  # noqa: E402

__all__ = [
    "Profile", "Strategy", "Dim", "Recommendation", "DimSpec",
    "build_profile", "build_strategies", "score_products", "recommend",
    "stars_of", "explain_chain",
]


# ══════════════════════════════ 画像 ══════════════════════════════

@dataclass
class Profile:
    """达人粉丝画像（来自 `talents` 表）。"""

    talent_id: str = ""
    name: str = ""
    age_dist: dict[str, float] = field(default_factory=dict)      # {"31-35": 38}
    gender_dist: dict[str, float] = field(default_factory=dict)   # {"女": 92}
    price_range: str = ""            # "300-600"
    avg_price: float = 0.0           # 客单价（粉丝支付均价）
    categories: list[str] = field(default_factory=list)  # 偏好类目（按优先级）
    live_ratio: float = 0.0
    video_ratio: float = 0.0
    product_count: int = 0           # 带货商品数 ≈ 带货经验
    total_gmv: float = 0.0

    # ── 派生特征 ──
    @property
    def main_age(self) -> str:
        """占比最高的年龄段（画像里最常被引用的一条）。"""
        if not self.age_dist:
            return ""
        return max(self.age_dist.items(), key=lambda kv: kv[1])[0]

    @property
    def main_gender(self) -> str:
        if not self.gender_dist:
            return ""
        return max(self.gender_dist.items(), key=lambda kv: kv[1])[0]

    @property
    def price_center(self) -> float:
        """客单价中心值。

        优先级：`fan_price_range` 区间中点 → `average_price` → 0（=画像不足）。
        """
        lo, hi = parse_price_range(self.price_range)
        if lo and hi:
            return (lo + hi) / 2.0
        if self.avg_price > 0:
            return float(self.avg_price)
        return 0.0

    @property
    def is_inexperienced(self) -> bool:
        """带货经验少（产品数少且 GMV 低）⇒ 策略要更保守（别推高价）。"""
        return self.product_count <= 3 and self.total_gmv < 50_000

    @property
    def is_ready(self) -> bool:
        """画像够不够支撑策略生成。"""
        return self.price_center > 0 and bool(self.categories)

    def missing(self) -> list[str]:
        miss = []
        if not self.age_dist:
            miss.append("年龄段")
        if not self.gender_dist:
            miss.append("性别")
        if self.price_center <= 0:
            miss.append("客单价")
        if not self.categories:
            miss.append("偏好类目")
        return miss


def parse_price_range(text: Any) -> tuple[float, float]:
    """'300-600' / '¥300~600' / '0-100元' → (300.0, 600.0)；解析不出 → (0, 0)。

    ★ 实现见 `product_facts.parse_price_range`（和排品器共用同一份口径）。
    """
    return facts.parse_price_range(text)


def _as_json(value: Any, default: Any) -> Any:
    """talent 表里很多 JSON 列存的是 TEXT，解析不了就用默认值。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return out if out is not None else default


def _num(value: Any) -> float:
    try:
        if isinstance(value, bool):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


_GENDER_ALIAS = {"male": "男", "female": "女", "m": "男", "f": "女",
                 "men": "男", "women": "女"}


def clean_category(name: Any) -> str:
    """OCR 味的偏好类目写法 → 干净类目名。

    实测数据长这样：`偏好服装(57%)` —— 前缀「偏好」+ 尾部占比。
    不洗的话：① 策略名变成「高性价比偏好服装(57%)爆品」（读着别扭）；
    ② 类目匹配对不上商品库写法（商品是「服装/…」）⇒ 候选数归零。
    """
    s = str(name or "").strip()
    if not s:
        return ""
    s = re.sub(r"[（(]\s*\d+(?:\.\d+)?\s*%\s*[)）]\s*$", "", s)
    s = re.sub(r"^偏好\s*", "", s)
    s = s.strip()
    return s or str(name).strip()


def _normalize_gender(key: str) -> str:
    """性别键归一（我们库里有「女/男」，也有 OCR 来的「female/male」）。"""
    k = str(key).strip()
    return _GENDER_ALIAS.get(k.lower(), k)


def build_profile(row: dict | Any) -> Profile:
    """`talents` 表一行 → Profile。

    ★ 不编：拿不到就是空，不猜、不用「默认受众」填。
    """
    r = dict(row) if isinstance(row, dict) else {
        c[0]: row[c[0]] for c in row.keys()
    }
    age = _as_json(r.get("fan_age"), {})
    gender = _as_json(r.get("fan_gender"), {})

    cats: list[str] = []
    for key in ("fan_category", "category", "main_category"):
        v = (r.get(key) or "").strip() if isinstance(r.get(key), str) else ""
        v = clean_category(v)
        if v and v not in cats:
            cats.append(v)
    dist = _as_json(r.get("category_distribution"), {})
    if isinstance(dist, dict):
        for k, v in sorted(dist.items(), key=lambda kv: -_num(kv[1])):
            k = clean_category(k)
            if k and k not in cats:
                cats.append(str(k))

    return Profile(
        talent_id=str(r.get("id") or ""),
        name=str(r.get("name") or ""),
        age_dist={str(k): _num(v) for k, v in age.items()} if isinstance(age, dict) else {},
        gender_dist={_normalize_gender(k): _num(v) for k, v in gender.items()}
        if isinstance(gender, dict) else {},
        price_range=str(r.get("fan_price_range") or ""),
        avg_price=_num(r.get("average_price")),
        categories=cats,
        live_ratio=_num(r.get("live_ratio")),
        video_ratio=_num(r.get("video_ratio")),
        product_count=int(_num(r.get("product_count") or r.get("total_products"))),
        total_gmv=_num(r.get("total_gmv")),
    )


# ══════════════════════════════ 策略 ══════════════════════════════

ROLE_HIT = "爆品"
ROLE_PUSH = "热推款"
ROLE_TRAFFIC = "引流款"

# 角色 → (价格带下界系数, 价格带上界系数)  —— 相对粉丝客单价
ROLE_BAND = {
    ROLE_HIT:    (0.40, 1.00),   # 高性价比：不超过客单价
    ROLE_PUSH:   (0.80, 1.30),   # 主力成交带
    ROLE_TRAFFIC: (0.00, 0.35),  # 低价拉新
}
ROLE_LABEL = {
    ROLE_HIT: "高性价比",
    ROLE_PUSH: "主力成交带",
    ROLE_TRAFFIC: "低价高转化",
}


@dataclass
class Strategy:
    """类目策略（对齐达人端 AI 助手的「[价格特征]+[类目]+[角色]」命名）。"""

    name: str
    category: str
    role: str
    price_lo: float
    price_hi: float
    rationale: str = ""          # 为什么这么推（引用画像里的具体数字）
    dim_boost: dict[str, float] = field(default_factory=dict)  # 维度权重微调

    def to_dict(self) -> dict:
        return {
            "name": self.name, "category": self.category, "role": self.role,
            "price_lo": self.price_lo, "price_hi": self.price_hi,
            "rationale": self.rationale, "dim_boost": self.dim_boost,
        }


def build_strategies(profile: Profile) -> list[Strategy]:
    """粉丝画像 → 类目策略列表。

    ★ 画像不够就返回空列表（不硬造）—— 调用方要据此明说「画像不足」。
    """
    p = profile.price_center
    if p <= 0 or not profile.categories:
        return []

    age_txt = profile.main_age or "年龄未标注"
    gender_txt = profile.main_gender or ""
    who = " ".join(x for x in (gender_txt, age_txt) if x) or "该达人"

    strategies: list[Strategy] = []
    roles = [ROLE_HIT, ROLE_PUSH, ROLE_TRAFFIC]
    cats = list(profile.categories[:3])
    # 类目不够 3 个 ⇒ 用已有类目补齐剩余角色（换价格带，不换类目）
    i = 0
    while len(strategies) < len(roles):
        cat = cats[i % len(cats)] if cats else ""
        role = roles[len(strategies)]
        lo_f, hi_f = ROLE_BAND[role]
        lo, hi = round(p * lo_f, 2), round(p * hi_f, 2)
        if role == ROLE_TRAFFIC and lo <= 0:
            lo = 1.0
        name = f"{ROLE_LABEL[role]}{cat}{role}"
        rationale = (
            f"{who}，粉丝客单价约 ¥{p:g}；该类目按「{ROLE_LABEL[role]}」定位，"
            f"建议到手价 ¥{lo:g}~¥{hi:g}"
        )
        if profile.is_inexperienced:
            rationale += "；该达人带货经验较少，优先低门槛价位"
        strategies.append(Strategy(name=name, category=cat, role=role,
                                   price_lo=lo, price_hi=hi, rationale=rationale))
        i += 1
    return strategies


# ══════════════════════════════ 评分器 ══════════════════════════════

@dataclass(frozen=True)
class DimSpec:
    key: str
    label: str
    weight: float
    kind: str                 # bounded / log / band / rank / tag
    lo: float = 0.0
    hi: float = 1.0


# 基准维度（权重和 = 1.00）。顺序即展示顺序。
BASE_DIMS: tuple[DimSpec, ...] = (
    DimSpec("price_fit",      "到手价契合度",   0.22, "band"),
    DimSpec("video_sales_7d", "近7日短视频销量", 0.20, "log"),
    DimSpec("shop_score",     "店铺体验分",     0.15, "bounded", 0, 100),
    DimSpec("commission_rate", "佣金率",        0.12, "bounded", 0, 30),
    DimSpec("good_ratio",     "好评率",         0.10, "bounded", 0, 100),
    # ★ 「带货达人数」= **达人端** `cooper_author_num` = 平台全商品带货达人数。
    #   ⚠️ 绝不能用 `platform_order_daren_count` —— 那是**团长端**的
    #      「我方机构出单达人数」（口径见 filing_products.py 的注释），
    #      属**我方出单数据** ⇒ 进达人可见的推荐 = 违反 AGENTS.md §8.1。
    #      字段名带 `platform_` 前缀、看着像平台数据，其实是我方的 ⇒ 极易误用。
    DimSpec("talents_sold",   "带货达人数",     0.10, "log"),
    DimSpec("board_rank",     "榜单排名",       0.06, "rank"),
    DimSpec("benefit_tags",   "优惠/运营险标签", 0.05, "tag"),
)

# 可选维度：池内覆盖率达标才启用（见铁律 3）
OPTIONAL_DIMS: tuple[DimSpec, ...] = (
    DimSpec("conversion_rate", "转化率", 0.08, "bounded", 0, 10),
)

MIN_COVERAGE = 0.6     # 池内覆盖率闸门

# 候选池小于这个数 ⇒ 池内归一化没有区分度 ⇒ 星级封顶（防假绿灯）
MIN_POOL_FOR_CONFIDENCE = 3
MAX_STARS_LOW_CONFIDENCE = 3

# 星级阈值（绝对分）。★ **按真实数据校准过**，不是拍脑袋：
#   2026-10-24 实测本库 92 个「策略×商品」样本的分数分布：
#     min 12.0 / p25 68.9 / median 74.3 / p75 82.2 / p90 85.4 / max 91.3
#   ⇒ 阈值对齐分位：5★≈前 10%，4★≈前 30%，3★≈前 75%，2★≈后 25%。
#   改这里 = 整体校准，改完请重跑 `tests/test_product_reco.py` 的校准用例。
STAR_THRESHOLDS = ((85.0, 5), (78.0, 4), (68.0, 3), (50.0, 2))

# 池内分位 → 星级（提供**区分度**）
PERCENTILE_STARS = ((0.90, 5), (0.75, 4), (0.50, 3), (0.25, 2))

BENEFIT_TAGS = ("消费券", "运费险", "流量扶持", "百亿补贴", "分期免息",
                "高结算率", "新品", "补贴", "券后", "可开票")

# 维度取值来源：products 表的列 → 归一化用的原始值
_DIM_SOURCES = {
    "price_fit":      lambda p: p.get("price"),
    "video_sales_7d": lambda p: p.get("video_sales_7d"),
    "shop_score":     lambda p: p.get("shop_score"),
    "commission_rate": lambda p: p.get("commission_rate"),
    "good_ratio":     lambda p: p.get("good_ratio"),
    "talents_sold":   lambda p: p.get("talents_sold"),
    "board_rank":     lambda p: p.get("board_rank"),
    "benefit_tags":   lambda p: p.get("tags"),
    "conversion_rate": lambda p: p.get("conversion_rate"),
}


@dataclass
class Dim:
    key: str
    label: str
    raw: Any
    score: float            # 0..1
    weight: float           # 参与加权的实际权重
    missing: bool = False
    note: str = ""          # 可解释文案

    def to_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "raw": self.raw,
                "score": round(self.score, 4), "weight": round(self.weight, 4),
                "missing": self.missing, "note": self.note}


@dataclass
class Recommendation:
    product_id: str
    name: str
    price: float
    score: float                 # 0..100（绝对分）
    stars: int                   # 1..5（min(绝对分星级, 池内分位星级)）
    strategy: str
    reasons: list[str] = field(default_factory=list)
    dims: list[Dim] = field(default_factory=list)
    missing_dims: list[str] = field(default_factory=list)      # 本商品缺这一项
    dropped_dims: list[str] = field(default_factory=list)      # 池内覆盖率不足，整体未启用
    abs_stars: int = 0           # 只看绝对分的星级（地板）
    pool_percentile: float = 0.0  # 池内分位 0..1
    completeness: float = 0.0    # 参与打分的权重占比（数据完整度）
    rank_in_pool: int = 0
    low_confidence: bool = False  # 候选池太小 ⇒ 星级不可信

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id, "name": self.name,
            "price": self.price, "score": round(self.score, 1), "stars": self.stars,
            "abs_stars": self.abs_stars,
            "pool_percentile": round(self.pool_percentile, 3),
            "strategy": self.strategy, "reasons": self.reasons,
            "dims": [d.to_dict() for d in self.dims],
            "missing_dims": self.missing_dims,
            "dropped_dims": self.dropped_dims,
            "completeness": round(self.completeness, 3),
            "rank_in_pool": self.rank_in_pool,
            "low_confidence": self.low_confidence,
        }


def stars_of(score: float) -> int:
    """绝对分 → 1..5 星。"""
    for threshold, star in STAR_THRESHOLDS:
        if score >= threshold:
            return star
    return 1


def stars_by_percentile(pct: float) -> int:
    """池内分位 → 1..5 星（提供区分度）。"""
    for threshold, star in PERCENTILE_STARS:
        if pct >= threshold:
            return star
    return 1


def _missing(value: Any) -> bool:
    """空值判定：None / 空串 / 空集合 / 0 都算「没有真值」。

    ⚠️ 0 算缺失是刻意的：平台给的 0（好评率 0%、体验分 0）几乎总是
    「该字段没有数据」，而不是「真的是 0」。分数相同时优先取有值的那个。
    """
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return float(value) <= 0
    return False


def _commission_rate(product: dict) -> float:
    """佣金率（%）。

    ★ 解析规则见 `product_facts.commission_rate_of`（单一真源，和排品器共用）：
      · 独立列 `commission_rate`
      · `commission_rates` JSON 的标量键（default/rate/commission_rate）
      · **分档结构**（{"投放期": 10, "常规活动期": 5, "其他": 5}）→ 取最高档
    都取不到 → 0.0（**缺失**，调用方据此退出加权，不拿 0 当真值）。
    """
    return facts.commission_rate_of(product)


def _tags_of(product: dict) -> list[str]:
    t = product.get("tags")
    if isinstance(t, list):
        return [str(x) for x in t]
    if isinstance(t, str) and t.strip():
        parsed = _as_json(t, None)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [x.strip() for x in t.replace("、", ",").split(",") if x.strip()]
    return []


def _price_of(product: dict) -> float:
    """到手价（★ 口径见 `product_facts.price_of`，和排品器共用一份）。"""
    return facts.price_of(product)


def _num_or_zero(value: Any) -> float:
    """取数值；缺失/非法 → 0.0。

    ⚠️ 别写 `_missing(x) and 0 or float(x)` —— 那是 Python 短路陷阱：
    `_missing` 为 True 时 `True and 0` 得 0，`0 or float(x)` 又去算 float(x)
    ⇒ x 是 None 时直接 TypeError（踩过）。
    """
    if _missing(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _normalized_row(product: dict) -> dict:
    """把 products 一行抽成评分器要的原始值（口径集中在这里，别处别再各写一套）。"""
    row = {
        "price": _price_of(product),
        "commission_rate": _commission_rate(product),
        "good_ratio": _num_or_zero(product.get("good_ratio")),
        "shop_score": _num_or_zero(product.get("shop_score")),
        "video_sales_7d": _num_or_zero(product.get("video_sales_7d")),
        "board_rank": _num_or_zero(product.get("board_rank")),
        "conversion_rate": _num_or_zero(product.get("conversion_rate")),
        "tags": _tags_of(product),
    }
    # ★ 「带货达人数」的唯一合规来源 = **达人端** `cooper_author_num`
    #   （= 平台全商品带货达人数，存 `products.influencer_count`）。
    #   ⛔ 绝不能 fallback 到 `platform_order_daren_count`（团长端「我方机构出单达人数」）
    #      —— 那是**我方出单数据**，进了推荐就是泄露我方经营数据给达人（AGENTS.md §8.1）。
    #      这颗雷的隐蔽处：列名带 `platform_` 前缀，看着像平台数据。
    sold = product.get("influencer_count")
    if _missing(sold):
        # 兜底只认「调用方显式传入的同义 key」，生产链路没人传（已 grep 确认），
        # 保留是为了让纯函数单测能用自造样本喂值。
        sold = product.get("talents_sold")
    row["talents_sold"] = _num_or_zero(sold)
    return row


def _band_membership(value: float, lo: float, hi: float) -> float:
    """价格带隶属度：带内 1.0，边缘线性衰减到 0。

    - 带内（含边界）→ 1.0
    - 价格 < lo：按「比 lo 低多少 / lo」衰减（lo=0 时直接 0）
    - 价格 > hi：按「比 hi 高多少 / hi」衰减
    """
    if value <= 0:
        return 0.0
    if lo <= value <= hi:
        return 1.0
    if value < lo:
        if lo <= 0:
            return 0.0
        return max(0.0, 1.0 - (lo - value) / lo)
    if hi <= 0:
        return 0.0
    return max(0.0, 1.0 - (value - hi) / hi)


def _log_scale(values: list[float]) -> tuple[float, float]:
    """log1p 后的 min/max（池内）。空池 → (0, 1)。"""
    logs = [math.log1p(max(0.0, v)) for v in values if v > 0]
    if not logs:
        return 0.0, 1.0
    return min(logs), max(logs)


def _norm_log(value: float, lo: float, hi: float) -> float:
    if value <= 0:
        return 0.0
    if hi <= lo:
        return 1.0
    return (math.log1p(value) - lo) / (hi - lo)


def _norm_bounded(value: float, lo: float, hi: float) -> float:
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _norm_rank(rank: float, max_rank: float) -> float:
    """排名越靠前分越高：1 → 1.0，max_rank → 0.0（对数刻度，避免长尾全挤 0）。"""
    if rank <= 0:
        return 0.0
    if max_rank <= 1:
        return 1.0
    return max(0.0, 1.0 - math.log(rank) / math.log(max_rank))


def _tag_score(tags: list[str]) -> float:
    if not tags:
        return 0.0
    hits = [t for t in tags if any(b in t for b in BENEFIT_TAGS)]
    if not hits:
        return 0.2
    return min(1.0, 0.6 + 0.2 * len(hits))


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f)):,}"
    return f"{f:,.1f}"


def score_products(products: list[dict], strategy: Strategy) -> list[Recommendation]:
    """一批商品 × 一条策略 → 打分排序后的推荐列表。

    ★ 池内归一化（log / rank）需要先看全池 ⇒ 先抽原始值、再算池统计、最后逐个打分。
    """
    if not products:
        return []
    rows = [_normalized_row(p) for p in products]

    # ── 池统计（归一化基准）──
    log_lo, log_hi = _log_scale([r["video_sales_7d"] for r in rows])
    sold_lo, sold_hi = _log_scale([r["talents_sold"] for r in rows])
    ranks = [r["board_rank"] for r in rows if r["board_rank"] > 0]
    max_rank = max(ranks) if ranks else 0.0

    # ── 维度覆盖率闸门（铁律 3）──
    #   覆盖率不够 ⇒ 该维度**整个池**都不参与 ⇒ 口径一致、分数可比。
    #   ⚠️ 被剔除的维度要**如实报出来**（dropped_dims），
    #      否则调用方会以为「所有维度都参与了」⇒ 误读完整度。
    active: list[DimSpec] = []
    dropped: list[str] = []
    for spec in list(BASE_DIMS) + list(OPTIONAL_DIMS):
        src = _DIM_SOURCES[spec.key]
        cover = sum(0 if _missing(src(r)) else 1 for r in rows) / len(rows)
        if cover >= MIN_COVERAGE:
            active.append(spec)
        else:
            dropped.append(spec.label)

    base_weight = sum(s.weight for s in active)
    boost = strategy.dim_boost or {}
    # 候选池太小 ⇒ 池内归一化没有区分度 ⇒ 星级不可信（防假绿灯）
    low_confidence = len(products) < MIN_POOL_FOR_CONFIDENCE

    recs: list[Recommendation] = []
    for product, row in zip(products, rows):
        dims: list[Dim] = []
        used_weight = 0.0
        weighted = 0.0
        missing_labels: list[str] = []
        for spec in active:
            raw = _DIM_SOURCES[spec.key](row)
            if _missing(raw):
                missing_labels.append(spec.label)
                continue
            if spec.kind == "band":
                sc = _band_membership(float(raw), strategy.price_lo, strategy.price_hi)
                note = (f"¥{_fmt_num(raw)} 落在策略价带 "
                        f"¥{strategy.price_lo:g}~¥{strategy.price_hi:g}")
            elif spec.kind == "log":
                if spec.key == "video_sales_7d":
                    sc = _norm_log(float(raw), log_lo, log_hi)
                    note = f"近7日短视频销量 {_fmt_num(raw)}"
                else:
                    sc = _norm_log(float(raw), sold_lo, sold_hi)
                    note = f"达人已出单 {_fmt_num(raw)} 人"
            elif spec.kind == "bounded":
                sc = _norm_bounded(float(raw), spec.lo, spec.hi)
                unit = "%" if spec.key in ("commission_rate", "good_ratio") else ""
                note = f"{spec.label} {_fmt_num(raw)}{unit}"
            elif spec.kind == "rank":
                sc = _norm_rank(float(raw), max_rank)
                note = f"榜单第 {_fmt_num(raw)} 名"
            elif spec.kind == "tag":
                sc = _tag_score(list(raw))
                note = "、".join(list(raw)[:3]) or "无优惠标签"
            else:
                sc, note = 0.0, ""
            w = spec.weight * (1.0 + boost.get(spec.key, 0.0))
            dims.append(Dim(key=spec.key, label=spec.label, raw=raw,
                            score=sc, weight=w, missing=False, note=note))
            used_weight += w
            weighted += w * sc

        score = 100.0 * (weighted / used_weight) if used_weight > 0 else 0.0
        completeness = used_weight / base_weight if base_weight else 0.0
        reasons = [d.note for d in dims if d.score >= 0.6][:3]
        if missing_labels:
            reasons.append("数据缺：" + "、".join(missing_labels))

        stars = stars_of(score)
        if low_confidence and stars > MAX_STARS_LOW_CONFIDENCE:
            # 候选池太小 ⇒ 分数没有区分度 ⇒ 星级封顶，别给假绿灯
            stars = MAX_STARS_LOW_CONFIDENCE

        recs.append(Recommendation(
            product_id=str(product.get("id") or product.get("platform_product_id") or ""),
            name=str(product.get("name") or ""),
            price=_price_of(product),
            score=score,
            stars=stars,
            abs_stars=stars,
            strategy=strategy.name,
            reasons=reasons,
            dims=dims,
            missing_dims=missing_labels,
            dropped_dims=list(dropped),
            completeness=completeness,
            low_confidence=low_confidence,
        ))

    # ── 排序：绝对分 desc；同分用池内分位 tie-break（不改变星级）──
    order = sorted(range(len(recs)),
                   key=lambda i: (-recs[i].score, -_pool_percentile(recs, i)))
    out: list[Recommendation] = []
    for pos, i in enumerate(order, 1):
        recs[i].rank_in_pool = pos
        out.append(recs[i])

    # ── 星级 = min(绝对分星级, 池内分位星级) ──
    #   绝对分做**地板**：防止「一池子全是很差的货」也被分位制排成 5★（假绿灯）；
    #   池内分位做**区分度**：绝对分挤在一起时（我们库里头部商品只差 3 分）
    #   纯绝对分会「清一色 5★」，用户看不到差别。
    if len(recs) >= MIN_POOL_FOR_CONFIDENCE:
        for idx, r in enumerate(recs):
            pct = _pool_percentile(recs, idx)
            r.pool_percentile = pct
            r.stars = min(r.abs_stars, stars_by_percentile(pct))
    return out


def _pool_percentile(recs: list[Recommendation], idx: int) -> float:
    """该推荐在池内的分位（0..1），同分时用来区分先后。"""
    s = recs[idx].score
    below = sum(1 for r in recs if r.score < s)
    return below / max(1, len(recs) - 1)


# ══════════════════════════════ 编排 ══════════════════════════════

def _category_of(product: dict) -> str:
    cat = (product.get("category") or "").strip()
    return cat


def _first_seg(category: str) -> str:
    """一级类目（路径第一段）。"""
    return (category or "").replace("，", "/").split("/")[0].strip()


def _categories_overlap(a: str, b: str) -> bool:
    """两个类目是不是同一个（层级不同也算）。

    ⚠️ 坑：我们库里的一级类目写「鞋靴」，平台口径叫「鞋靴箱包」；
       达人画像写「鞋靴/凉鞋」，商品写「鞋靴/靴子」——
       **只比全等会一个都匹配不上**（踩过：候选数 0，推荐空列表）。
    ⇒ 规则：一级类目相同 ⇒ 同一类目；否则只要有任一层段重合 ⇒ 也算同一类目。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    fa, fb = _first_seg(a), _first_seg(b)
    if fa == fb:
        return True
    # 平台一级类目叫「鞋靴箱包」、我们库里叫「鞋靴」⇒ 前缀同义
    # （长度 ≥2 才算，避免「鞋」这种单字误命中）
    if len(fa) >= 2 and len(fb) >= 2 and (fa.startswith(fb) or fb.startswith(fa)):
        return True
    sa = {x.strip() for x in a.replace("，", "/").split("/") if x.strip()}
    sb = {x.strip() for x in b.replace("，", "/").split("/") if x.strip()}
    return bool(sa & sb)


def _in_category(product: dict, category: str) -> bool:
    """类目匹配：层级不同也能命中（我们存「鞋靴/凉鞋」，策略可能只写「鞋靴」）。"""
    if not category:
        return True
    cat = _category_of(product)
    if not cat:
        return False
    return _categories_overlap(cat, category)


def _resolve_pool_category(target: str, pool_cats: list[str]) -> str:
    """把策略里的类目**落到商品池真实存在的类目**上。

    为什么需要：画像里的偏好类目是达人侧口径（「鞋靴/凉鞋」），
    商品池里是另一套写法（「鞋靴/靴子」）—— 不落一次，候选数直接归零。
    返回空串 ⇒ 池里根本没有这个类目 ⇒ 这条策略无货。
    """
    if not target:
        return ""
    if target in pool_cats:
        return target
    hits = [c for c in pool_cats if _categories_overlap(c, target)]
    if not hits:
        return ""
    # 取池内出现最多的那个（口径以商品池为准）
    return max(hits, key=lambda c: sum(1 for p in pool_cats if p == c))


def _in_price_band(product: dict, strategy: Strategy) -> bool:
    price = _price_of(product)
    if price <= 0:
        return False
    lo = strategy.price_lo if strategy.price_lo > 0 else 0.0
    # 价带外放宽到 2 倍（策略价带是「建议带」，不是硬门槛）
    return lo * 0.5 <= price <= strategy.price_hi * 2.0


def recommend(talent_row: dict, product_rows: list[dict], top_n: int = 6) -> dict:
    """完整推理链：画像 → 策略 → 商品推荐 → 推荐指数。

    返回 dict（可直接给 API / 前端）。
    """
    profile = build_profile(talent_row)
    strategies = build_strategies(profile)

    result: dict[str, Any] = {
        "profile": {
            "talent_id": profile.talent_id,
            "name": profile.name,
            "main_age": profile.main_age,
            "main_gender": profile.main_gender,
            "age_dist": profile.age_dist,
            "gender_dist": profile.gender_dist,
            "price_range": profile.price_range,
            "price_center": profile.price_center,
            "categories": profile.categories,
            "is_inexperienced": profile.is_inexperienced,
            "missing": profile.missing(),
        },
        "strategies": [s.to_dict() for s in strategies],
        "recommendations": [],
        "notes": [],
    }

    if not strategies:
        miss = profile.missing()
        result["notes"].append(
            "画像不足以生成类目策略（缺：" + "、".join(miss) + "）—— 不硬编策略")
        return result

    products = [p for p in product_rows if p]
    picked: list[Recommendation] = []
    per_strategy: list[dict] = []

    # ── 策略类目 → 商品池真实类目（口径对齐，否则候选数归零）──
    pool_cats = [c for c in (_category_of(p) for p in products) if c]
    for st in strategies:
        resolved = _resolve_pool_category(st.category, pool_cats)
        if resolved and resolved != st.category:
            st.category = resolved
            st.name = f"{ROLE_LABEL[st.role]}{resolved}{st.role}"
            st.rationale += f"；类目按商品库口径记为「{resolved}」"
        if not resolved:
            per_strategy.append({
                "strategy": st.to_dict(), "candidate_count": 0,
                "scored_count": 0, "top_product_ids": [],
                "note": "商品库里没有这个类目的货",
            })
            continue
        cands = [p for p in products
                 if _in_category(p, st.category) and _in_price_band(p, st)]
        scored = score_products(cands, st)
        per_strategy.append({
            "strategy": st.to_dict(),
            "candidate_count": len(cands),
            "scored_count": len(scored),
            "top_product_ids": [r.product_id for r in scored[:top_n]],
        })
        picked.extend(scored[:top_n])

    # ── 同一商品可能命中多条策略 ⇒ 取最高分那条 ──
    best: dict[str, Recommendation] = {}
    for r in picked:
        old = best.get(r.product_id)
        if old is None or r.score > old.score:
            best[r.product_id] = r
    final = sorted(best.values(), key=lambda r: (-r.score, r.rank_in_pool))[:top_n]
    for i, r in enumerate(final, 1):
        r.rank_in_pool = i

    result["recommendations"] = [r.to_dict() for r in final]
    result["strategy_match"] = per_strategy
    # ★ strategies 要在**类目落池之后**再导出，否则前端拿到的是没对齐口径的类目
    result["strategies"] = [s.to_dict() for s in strategies]
    result["notes"].append(
        f"推荐指数由我方评分器自算（达人端无此真值）；缺失维度不按 0 分计，"
        f"已按剩余权重归一化")
    dropped = sorted({d for r in final for d in r.dropped_dims})
    if dropped:
        result["notes"].append(
            "以下维度因池内数据覆盖率不足未参与打分：" + "、".join(dropped))
    if any(r.low_confidence for r in final):
        result["notes"].append(
            f"候选商品少于 {MIN_POOL_FOR_CONFIDENCE} 个，星级已封顶"
            f"{MAX_STARS_LOW_CONFIDENCE}★（池内无区分度，避免假绿灯）")
    return result


def explain_chain(chain: dict) -> str:
    """把推理链渲染成一段人话（飞书 / H5 直接展示）。"""
    lines: list[str] = []
    p = chain.get("profile") or {}
    if p.get("name"):
        head = f"【粉丝画像】{p['name']}"
        bits = []
        if p.get("main_gender"):
            bits.append(p["main_gender"])
        if p.get("main_age"):
            bits.append(p["main_age"] + "为主")
        if p.get("price_center"):
            bits.append(f"客单价约 ¥{p['price_center']:g}")
        if bits:
            head += "：" + "、".join(bits)
        lines.append(head)
    for st in chain.get("strategies") or []:
        lines.append(f"【类目策略】{st['name']} —— {st.get('rationale', '')}")
    for r in chain.get("recommendations") or []:
        lines.append(
            f"【推荐】{r['name'][:28]} ¥{r['price']:g} "
            f"{'★' * r['stars']}{'☆' * (5 - r['stars'])} {r['score']:.0f}分"
            + (f"（{'；'.join(r['reasons'][:2])}）" if r.get("reasons") else "")
        )
    for n in chain.get("notes") or []:
        lines.append(f"[说明] {n}")
    return "\n".join(lines)
