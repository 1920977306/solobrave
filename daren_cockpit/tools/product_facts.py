#!/usr/bin/env python3
"""商品事实提取（单一真源：佣金率 / 到手价）。

为什么单独一个模块
------------------
`product_reco`（推荐指数）和 `product_plan`（排品优先级）读的是**同一批商品字段**。
口径一分叉就会出现静默漂移 —— 实测踩过（2026-10-24）：

    平台的佣金率有两种形态：
      ① 标量：{"default": 20.0, "投放期": 8}
      ② **分档**：{"投放期": 10, "常规活动期": 5, "其他": 5}   ← 没有 default 键
    老代码只认 ① 的 `default` ⇒ ② 的 5/58 个商品被判成「佣金数据缺失」，
    收益维度（reco 权重 12% / plan 权重 35%）**静默退出加权**，
    评分跟平台真值对不上，而且**不报错**（最坏的一种错：看起来一切正常）。

所以规则只写**这一份**，两边都从这里取 —— 口径单一真源。

三条纪律
--------
1. **缺失 ≠ 0**：取不到返回 0.0，调用方据此走「退出加权」，不拿 0 冒充真值。
2. **不造数**：分档结构取的是平台自己给的档位值（最高档 = 可达上限），不外推。
3. **理由带数字**：理由行必须说清是哪个档位，不能只写「佣金率 20%」糊过去。
"""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "commission_rate_of", "commission_note", "price_of", "parse_price_range",
    "COMMISSION_SCALAR_KEYS",
]

# 标量佣金率在 `commission_rates` JSON 里的键名（按优先级）
COMMISSION_SCALAR_KEYS = ("default", "rate", "commission_rate")


def _as_json(value: Any, default: Any) -> Any:
    """TEXT 列里存的是 JSON 串；解析不了就用默认值。"""
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        out = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default
    return out if out is not None else default


def _to_float(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_price_range(text: Any) -> tuple[float, float]:
    """'300-600' / '¥300~600' / '0-100元' → (300.0, 600.0)；解析不出 → (0, 0)。"""
    if not text:
        return 0.0, 0.0
    s = str(text).replace("，", ",").replace("~", "-").replace("—", "-")
    s = re.sub(r"[¥￥元\s]", "", s)
    nums = re.findall(r"\d+(?:\.\d+)?", s)
    if len(nums) >= 2:
        lo, hi = float(nums[0]), float(nums[1])
        return (lo, hi) if hi >= lo else (hi, lo)
    if len(nums) == 1:
        v = float(nums[0])
        return v, v
    return 0.0, 0.0


def _commission_parts(product: dict) -> tuple[float, str, str]:
    """→ (佣金率%, 来源标签, 档位名)。

    来源标签：``column``（独立列）/ ``scalar``（JSON 标量键）/ ``phase``（分档结构）
    """
    if not product:
        return 0.0, "", ""
    v = product.get("commission_rate")
    if v is not None and not isinstance(v, bool):
        f = _to_float(v)
        if f > 0:
            return f, "column", ""
    rates = _as_json(product.get("commission_rates"), {})
    if not isinstance(rates, dict):
        return 0.0, "", ""
    # ① 标量键
    for key in COMMISSION_SCALAR_KEYS:
        if key in rates:
            f = _to_float(rates[key])
            if f > 0:
                return f, "scalar", ""
    # ② 分档结构 → 取**最高档**
    #    平台的档位（投放期/常规活动期/自然流/其他）各给一个佣金率，
    #    最高档就是这套品「可达上限」，不是我们外推的数字。
    best, best_key = 0.0, ""
    for k, val in rates.items():
        f = _to_float(val)
        if f > best:
            best, best_key = f, str(k)
    if best > 0:
        return best, "phase", best_key
    return 0.0, "", ""


def commission_rate_of(product: dict) -> float:
    """佣金率（%）。取不到 → 0.0（**缺失**，不是 0 分）。"""
    return _commission_parts(product)[0]


def commission_note(product: dict) -> str:
    """佣金理由行（带具体数字 + 档位）。取不到 → ''。"""
    value, source, phase = _commission_parts(product)
    if value <= 0:
        return ""
    if source == "phase" and phase:
        return f"佣金率最高 {value:g}%（{phase}）"
    return f"佣金率 {value:g}%"


def price_of(product: dict) -> float:
    """到手价：优先 `price` 列 → 缺则 `price_range` 中点 → 0（缺失）。"""
    if not product:
        return 0.0
    v = product.get("price")
    if v is not None and not isinstance(v, bool):
        f = _to_float(v)
        if f > 0:
            return f
    rng = product.get("price_range")
    if isinstance(rng, str) and rng.strip():
        lo, hi = parse_price_range(rng)
        if lo or hi:
            return (lo + hi) / 2.0
    return 0.0
