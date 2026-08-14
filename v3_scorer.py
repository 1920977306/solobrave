# -*- coding: utf-8 -*-
"""
V3 商品评分器
=============

实现《V3 达人匹配模型 - 算法文档 v1.0》(V3_model.md) 的两层漏斗评分：

第一层 选品意愿模型（达人商务会不会接）：
    意愿分 = 佣金意愿*0.35 + 好评率意愿*0.20 + 店铺信誉*0.15
             + 品牌势能*0.15 + 市场热度*0.15

第二层 带货效果模型（发出去能不能卖）：
    效果分 = 品类转化基准*0.30 + 内容形式匹配*0.25 + 价格带匹配*0.20
             + 季节/场景匹配*0.15 + 达人匹配度*0.10

第五层 综合打分：
    综合分 = 选品意愿分*0.45 + 带货效果分*0.45 + 人群匹配度*0.10
    评级：>=80 S / 70-79 A / 60-69 B / 50-59 C / <50 D

说明：
- 输入中只有品类和价格能直接命中第二层规则；内容形式匹配、季节/场景匹配、
  达人匹配度、人群匹配度作为可选输入，缺省时取中性分 NEUTRAL_SCORE=60，
  不人为拔高或压低。
- 品类转化基准表给的是转化率区间，这里取区间中位数再映射成分数
  （转化率越高，效果分越高）。

飞书写入（save_to_feishu）：
- 通过飞书开放 API 把评分结果写入多维表格"商品数据表"。
- 需要环境变量：FEISHU_APP_ID、FEISHU_APP_SECRET（自建应用凭证），
  以及 FEISHU_APP_TOKEN（多维表格 Base 的 app_token）。
- 写入前按"商品名称"查重：已存在则更新原记录，不存在则新建。
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

NEUTRAL_SCORE = 60.0  # 缺少数据时的中性分

# 选品意愿模型权重（V3_model.md 1.1~1.5）
WILL_WEIGHTS = {
    "commission": 0.35,   # 佣金意愿
    "positive": 0.20,     # 好评率意愿
    "shop": 0.15,         # 店铺信誉
    "brand": 0.15,        # 品牌势能
    "heat": 0.15,         # 市场热度
}

# 带货效果模型权重（V3_model.md 2.4）
EFFECT_WEIGHTS = {
    "category": 0.30,     # 品类转化基准
    "content": 0.25,      # 内容形式匹配
    "price": 0.20,        # 价格带匹配
    "season": 0.15,       # 季节/场景匹配
    "influencer": 0.10,   # 达人匹配度
}

# 品牌势能评分表（1.4）
BRAND_SCORES = {
    "国际大牌": 95.0,
    "国内知名品牌": 80.0,
    "IP/明星联名": 75.0,
    "新锐品牌": 55.0,
    "白牌/无品牌": 30.0,
}

# 品类转化率基准表（2.1），keyword -> (转化下限%, 转化上限%)
CATEGORY_CONVERSION = [
    (("医美", "医疗器械"), (10.0, 15.0)),          # 医疗器械/医美
    (("洗护", "浴花", "个护", "芦荟"), (15.0, 40.0)),  # 功能痛点品
    (("开学", "防晒"), (10.0, 15.0)),               # 季节刚需品
    (("美瞳", "护肤", "面霜"), (10.0, 15.0)),       # 品牌认知品
    (("内衣", "内裤", "文胸", "Bra"), (5.0, 15.0)),  # 内衣/内裤
    (("服装", "服饰", "鞋", "睡衣", "家居服", "T恤", "风衣", "卫衣"), (5.0, 10.0)),  # 日常服装
]
DEFAULT_CONVERSION = (5.0, 10.0)   # 未识别品类按日常服装档处理
HIGH_END_CONVERSION = (2.5, 5.0)   # 高端品（¥200+）
HIGH_END_PRICE = 200.0
# 高端品价格覆盖的豁免规则：(品类关键词, 豁免价格下限, 豁免价格上限)
# 仅当品类命中关键词且价格落在 [下限, 上限) 区间才豁免；
# 如鞋类仅 ¥200-300 豁免，¥300 及以上的鞋仍按高端品档（45 分）
PRICE_OVERRIDE_EXEMPT = (("鞋", 200.0, 300.0),)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _to_ratio(value):
    """接受 20 / 0.20 两种写法，统一成小数比率。"""
    v = float(value)
    return v / 100.0 if v > 1 else v


def _to_percent(value):
    """好评率等百分数：接受 98 / 0.98，统一成 0~100 的数值。"""
    v = float(value)
    return v * 100.0 if v <= 1 else v


# ---------------------------------------------------------------------------
# 第一层：选品意愿模型
# ---------------------------------------------------------------------------

def commission_score(page_rate):
    """佣金意愿评分（1.1）。page_rate 为页面佣金率（20 或 0.20 均可）。

    分档：>=20% -> 95；15-19% -> 85；10-14% -> 70；5-9% -> 55；<5% -> 35。
    """
    r = _to_ratio(page_rate)
    if r >= 0.20:
        return 95.0
    if r >= 0.15:
        return 85.0
    if r >= 0.10:
        return 70.0
    if r >= 0.05:
        return 55.0
    return 35.0


def positive_rate_score(positive_rate):
    """好评率意愿评分（1.2）。接受 98 或 0.98。"""
    p = _to_percent(positive_rate)
    if p >= 96:
        return 95.0
    if p >= 93:
        return 80.0
    if p >= 90:
        return 65.0
    if p >= 85:
        return 40.0
    return 15.0


def shop_score(shop_rating):
    """店铺信誉评分（1.3）。店铺评分为 0~100 分制。"""
    s = float(shop_rating)
    if s >= 99:
        return 95.0
    if s >= 95:
        return 80.0
    if s >= 90:
        return 65.0
    return 40.0


def brand_score(brand_type):
    """品牌势能评分（1.4）。按关键词匹配品牌类型。"""
    t = str(brand_type)
    if "国际" in t or "进口" in t or "海外" in t:
        return BRAND_SCORES["国际大牌"]
    if "IP" in t or "明星" in t or "联名" in t:
        return BRAND_SCORES["IP/明星联名"]
    if "知名" in t or "大牌" in t:
        return BRAND_SCORES["国内知名品牌"]
    if "新锐" in t or "新兴" in t:
        return BRAND_SCORES["新锐品牌"]
    if "白牌" in t or "无品牌" in t:
        return BRAND_SCORES["白牌/无品牌"]
    return BRAND_SCORES["白牌/无品牌"]  # 未知品牌保守按白牌处理


def heat_score(monthly_sales):
    """市场热度评分（1.5），按月销量。"""
    n = float(monthly_sales)
    if n > 10000:
        return 90.0
    if n >= 3000:
        return 70.0
    if n >= 1000:
        return 55.0
    return 35.0


def willingness_score(product):
    """选品意愿总分 = 佣金*0.35 + 好评*0.20 + 店铺*0.15 + 品牌*0.15 + 热度*0.15"""
    brand_type = product.get("品牌类型", "白牌/无品牌")
    parts = {
        "commission": commission_score(product["页面佣金率"]),
        "positive": positive_rate_score(product["好评率"]),
        "shop": shop_score(product["店铺评分"]),
        "brand": brand_score(brand_type),
        "heat": heat_score(product["月销量"]),
    }
    total = sum(parts[k] * WILL_WEIGHTS[k] for k in WILL_WEIGHTS)
    return total, parts


# ---------------------------------------------------------------------------
# 第二层：带货效果模型
# ---------------------------------------------------------------------------

def category_conversion_range(category, price):
    """品类转化率基准（2.1）。¥200+ 按高端品档，但命中 PRICE_OVERRIDE_EXEMPT
    价格区间豁免规则的品类除外（如 ¥200-300 的鞋类按日常服装档）。"""
    c = str(category)
    p = float(price)
    if p >= HIGH_END_PRICE:
        exempt = any(k in c and low <= p < high
                     for k, low, high in PRICE_OVERRIDE_EXEMPT)
        if not exempt:
            return HIGH_END_CONVERSION
    for keywords, conv in CATEGORY_CONVERSION:
        if any(k in c for k in keywords):
            return conv
    return DEFAULT_CONVERSION


def category_score(category, price):
    """品类转化基准分：取转化率区间中位数，映射为分数。"""
    low, high = category_conversion_range(category, price)
    mid = (low + high) / 2.0
    if mid >= 30:
        return 95.0
    if mid >= 20:
        return 85.0
    if mid >= 12:
        return 75.0
    if mid >= 7:
        return 60.0
    if mid >= 3.5:
        return 45.0
    return 35.0


def price_band_score(price):
    """价格带效果评分（2.3）：价格越高，视频转化越低。"""
    p = float(price)
    if p < 40:
        return 90.0   # ¥15-40 低价冲动品，视频转化高（15-40%）
    if p < 100:
        return 75.0   # ¥40-100 日常消费，转化中（5-15%）
    if p < 200:
        return 60.0   # ¥100-200 中端品，转化中低（5-10%）
    return 45.0       # ¥200+ 高端品，转化低（2.5-5%），需直播/图文补偿


def effect_score(product):
    """带货效果总分（2.4）。

    可选输入：内容形式匹配分 / 季节场景匹配分 / 达人匹配度（0-100），
    缺省取中性分 NEUTRAL_SCORE。
    """
    parts = {
        "category": category_score(product["品类"], product["价格"]),
        "content": float(product.get("内容形式匹配分", NEUTRAL_SCORE)),
        "price": price_band_score(product["价格"]),
        "season": float(product.get("季节场景匹配分", NEUTRAL_SCORE)),
        "influencer": float(product.get("达人匹配度", NEUTRAL_SCORE)),
    }
    total = sum(parts[k] * EFFECT_WEIGHTS[k] for k in EFFECT_WEIGHTS)
    return total, parts


# ---------------------------------------------------------------------------
# 第五层：综合打分
# ---------------------------------------------------------------------------

def grade(overall):
    """综合评级（5.2）：>=80 S / 70-79 A / 60-69 B / 50-59 C / <50 D"""
    if overall >= 80:
        return "S"
    if overall >= 70:
        return "A"
    if overall >= 60:
        return "B"
    if overall >= 50:
        return "C"
    return "D"


def score_product(product, weights_dir="."):
    """V3 商品评分主函数。

    输入 dict：
        商品名称、品类、价格、页面佣金率、好评率、店铺评分、品牌类型、月销量
        （可选：内容形式匹配分、季节场景匹配分、达人匹配度、人群匹配度）

    输出 dict：
        选品意愿分、带货效果分、综合分、评级（S/A/B/C/D），
        以及各子项明细（意愿明细 / 效果明细）。

    综合分的维度权重自动加载 weights_dir 下最新版本权重文件
    （model_weights_v*.json，取版本号最大者）；不存在则用内置默认值
    DEFAULT_DIMENSION_WEIGHTS（意愿 0.45 / 效果 0.45 / 综合 0.10）。
    """
    will, will_parts = willingness_score(product)
    effect, effect_parts = effect_score(product)
    crowd = float(product.get("人群匹配度", NEUTRAL_SCORE))
    w = load_latest_weights(weights_dir)
    overall = (will * w["选品意愿"]
               + effect * w["带货效果"]
               + crowd * w["综合得分"])
    return {
        "商品名称": product.get("商品名称", ""),
        "选品意愿分": round(will, 2),
        "带货效果分": round(effect, 2),
        "综合分": round(overall, 2),
        "评级": grade(overall),
        "意愿明细": will_parts,
        "效果明细": effect_parts,
    }


# ---------------------------------------------------------------------------
# 模型验证（预测 vs 达人真实带货反馈）
# ---------------------------------------------------------------------------

# validate_model 输入的英文字段 -> score_product 中文字段
EN_TO_CN_KEYS = {
    "product_name": "商品名称",
    "category": "品类",
    "price": "价格",
    "commission_rate": "页面佣金率",
    "review_rate": "好评率",
    "store_score": "店铺评分",
    "brand_type": "品牌类型",
    "monthly_sales": "月销量",
}

# 验证维度：(维度名, 预测值键, 实际值键)
VALIDATE_DIMENSIONS = (
    ("选品意愿", "选品意愿分", "actual_willingness"),
    ("带货效果", "带货效果分", "actual_effect"),
    ("综合得分", "综合分", "actual_overall"),
)

# 偏差等级阈值（按 MAPE %）：<=10 正常 / <=20 需关注 / <=30 较大偏差 / >30 严重偏差
DEVIATION_THRESHOLDS = ((10.0, "正常"), (20.0, "需关注"), (30.0, "较大偏差"))


def deviation_level(mape):
    """按 MAPE 判定偏差等级。"""
    for threshold, level in DEVIATION_THRESHOLDS:
        if mape <= threshold:
            return level
    return "严重偏差"


def validate_model(products, actuals, report_dir="iteration_logs"):
    """用达人真实带货反馈验证 V3 模型预测偏差。

    参数：
        products  商品实际数据列表，每个 dict 含 product_name/category/price/
                  commission_rate/review_rate/store_score/brand_type/monthly_sales
        actuals   实际结果列表，每个 dict 含 product_name/actual_willingness/
                  actual_effect/actual_overall
        report_dir 报告输出目录，默认 iteration_logs

    返回 dict：
        验证时间、样本量、dimensions（每个维度的 MAE/MAPE/偏差等级）、
        details（逐商品偏差明细）、severe_products（严重偏差商品清单）、
        report_path（markdown 报告路径）
    同时把报告写入 iteration_logs/iteration_YYYYMMDD.md。
    """
    actual_by_name = {a["product_name"]: a for a in actuals}
    details = []
    errors = {dim: [] for dim, _, _ in VALIDATE_DIMENSIONS}   # (绝对误差, 百分比误差)

    for p in products:
        name = p.get("product_name")
        actual = actual_by_name.get(name)
        if name is None or actual is None:
            continue  # 无匹配实际反馈的样本不参与验证
        cn_input = {EN_TO_CN_KEYS[k]: p[k] for k in EN_TO_CN_KEYS if k in p}
        pred = score_product(cn_input)

        row = {"product_name": name, "dimensions": {}, "max_pct_error": 0.0}
        for dim, pred_key, act_key in VALIDATE_DIMENSIONS:
            p_val = pred[pred_key]
            a_val = float(actual[act_key])
            abs_err = abs(p_val - a_val)
            pct_err = abs_err / abs(a_val) * 100.0 if a_val != 0 else 0.0
            row["dimensions"][dim] = {
                "predicted": p_val,
                "actual": a_val,
                "abs_error": round(abs_err, 2),
                "pct_error": round(pct_err, 2),
            }
            row["max_pct_error"] = max(row["max_pct_error"], pct_err)
            errors[dim].append((abs_err, pct_err))
        row["max_pct_error"] = round(row["max_pct_error"], 2)
        row["level"] = deviation_level(row["max_pct_error"])
        details.append(row)

    dimensions = {}
    for dim, _, _ in VALIDATE_DIMENSIONS:
        errs = errors[dim]
        if not errs:
            continue
        mae = sum(e[0] for e in errs) / len(errs)
        mape = round(sum(e[1] for e in errs) / len(errs), 2)
        dimensions[dim] = {
            "MAE": round(mae, 2),
            "MAPE": mape,
            "偏差等级": deviation_level(mape),
        }

    severe = [d for d in details if d["max_pct_error"] > 30.0]
    report_path = _write_iteration_report(dimensions, details, severe, report_dir)

    return {
        "验证时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "样本量": len(details),
        "dimensions": dimensions,
        "details": details,
        "severe_products": severe,
        "report_path": report_path,
    }


def _write_iteration_report(dimensions, details, severe, report_dir):
    """把验证结果写成 markdown 报告，返回文件路径。"""
    os.makedirs(report_dir, exist_ok=True)
    path = os.path.join(report_dir,
                        "iteration_%s.md" % datetime.now().strftime("%Y%m%d"))
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "# V3 模型迭代验证报告",
        "",
        "- 验证时间：%s" % now,
        "- 样本量：%d" % len(details),
        "",
        "## 各维度偏差汇总",
        "",
        "| 维度 | MAE | MAPE | 偏差等级 |",
        "| --- | --- | --- | --- |",
    ]
    for dim, _, _ in VALIDATE_DIMENSIONS:
        d = dimensions.get(dim)
        if d:
            lines.append("| %s | %.2f | %.2f%% | %s |"
                         % (dim, d["MAE"], d["MAPE"], d["偏差等级"]))
    lines += [
        "",
        "偏差等级阈值：MAPE <=10% 正常 / <=20% 需关注 / <=30% 较大偏差 / >30% 严重偏差",
        "",
        "## 严重偏差商品清单",
        "",
    ]
    if severe:
        lines.append("| 商品 | 维度 | 预测值 | 实际值 | 偏差% |")
        lines.append("| --- | --- | --- | --- | --- |")
        for row in severe:
            for dim, _, _ in VALIDATE_DIMENSIONS:
                d = row["dimensions"][dim]
                if d["pct_error"] > 30.0:
                    lines.append("| %s | %s | %.2f | %.2f | %.2f%% |"
                                 % (row["product_name"], dim, d["predicted"],
                                    d["actual"], d["pct_error"]))
    else:
        lines.append("无")

    lines += [
        "",
        "## 逐商品偏差明细",
        "",
        "| 商品 | 维度 | 预测值 | 实际值 | 绝对误差 | 偏差% |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in details:
        for dim, _, _ in VALIDATE_DIMENSIONS:
            d = row["dimensions"][dim]
            lines.append("| %s | %s | %.2f | %.2f | %.2f | %.2f%% |"
                         % (row["product_name"], dim, d["predicted"],
                            d["actual"], d["abs_error"], d["pct_error"]))
    lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


# ---------------------------------------------------------------------------
# 权重自动调整（基于 validate_model 验证结果）
# ---------------------------------------------------------------------------

# 维度级权重（原始值，总和为 1）：综合得分维度对应人群匹配度位
DEFAULT_DIMENSION_WEIGHTS = {
    "选品意愿": 0.45,
    "带货效果": 0.45,
    "综合得分": 0.10,
}

MAPE_TRIGGER = 20.0        # MAPE 超过 20% 触发权重下调
DOWN_RATIO = 0.05          # 超阈值维度权重下调 5%
MAX_DRIFT = 0.15           # 权重调整范围限制在原始值 ±15% 以内


def _latest_weights_path(weights_dir):
    """找最新版本权重文件，返回 (路径, 版本号)；没有则 (None, 0)。"""
    best_path, best_n = None, 0
    if os.path.isdir(weights_dir):
        for fn in os.listdir(weights_dir):
            if fn.startswith("model_weights_v") and fn.endswith(".json"):
                try:
                    n = int(fn[len("model_weights_v"):-len(".json")])
                except ValueError:
                    continue
                if n > best_n:
                    best_path, best_n = os.path.join(weights_dir, fn), n
    return best_path, best_n


def load_latest_weights(weights_dir="."):
    """加载最新维度权重：扫描 model_weights_v*.json 取版本号最大的文件，
    用其中的 dimension_weights；不存在则返回内置默认值 DEFAULT_DIMENSION_WEIGHTS。
    """
    path, _ = _latest_weights_path(weights_dir)
    if path:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return dict(data["dimension_weights"])
    return dict(DEFAULT_DIMENSION_WEIGHTS)


def auto_update_model(validation_result, weights_dir=".", report_path=None):
    """根据验证结果自动调整维度权重。

    规则：
    - 遍历 dimensions 每个维度，MAPE > 20% 的维度权重下调 5%；
    - 释放出的权重按当前权重比例分配给未超阈值的维度；
    - 所有维度都超阈值则不调整，保持原权重；
    - 每个权重限制在原始值（DEFAULT_DIMENSION_WEIGHTS）±15% 以内；
    - 调整后归一化，保证总和为 1；
    - 权重写入 model_weights_v{N}.json（N 从 1 递增），并在迭代报告中
      追加权重变更记录（调整前/调整后权重 + 调整原因）。

    返回 dict：updated / adjusted_dimensions / old_weights / new_weights /
    new_weights_path / reason。
    """
    dimensions = validation_result.get("dimensions", {})
    old_weights = load_latest_weights(weights_dir)
    base = {
        "updated": False,
        "adjusted_dimensions": [],
        "old_weights": old_weights,
        "new_weights": dict(old_weights),
        "new_weights_path": None,
    }

    over = [d for d in dimensions
            if dimensions[d].get("MAPE", 0) > MAPE_TRIGGER and d in old_weights]
    if not over:
        base["reason"] = "无维度 MAPE 超过 %g%%，不调整" % MAPE_TRIGGER
        return base
    if len(over) == len(old_weights):
        base["reason"] = "所有维度 MAPE 均超过 %g%%，保持原权重" % MAPE_TRIGGER
        return base

    # 1) 超阈值维度下调 5%（不低于原始值的 85%）
    new_weights = dict(old_weights)
    freed = 0.0
    for d in over:
        target = old_weights[d] * (1 - DOWN_RATIO)
        floor = DEFAULT_DIMENSION_WEIGHTS[d] * (1 - MAX_DRIFT)
        new_weights[d] = max(target, floor)
        freed += old_weights[d] - new_weights[d]

    # 2) 释放的权重按当前权重比例分配给未超阈值维度（不超过原始值的 115%）
    receivers = [d for d in old_weights if d not in over]
    recv_total = sum(old_weights[d] for d in receivers)
    for d in receivers:
        share = freed * old_weights[d] / recv_total if recv_total else 0.0
        cap = DEFAULT_DIMENSION_WEIGHTS[d] * (1 + MAX_DRIFT)
        new_weights[d] = min(old_weights[d] + share, cap)

    # 3) 归一化，总和为 1
    total = sum(new_weights.values())
    new_weights = {d: round(w / total, 6) for d, w in new_weights.items()}

    # 4) 版本化存储
    os.makedirs(weights_dir, exist_ok=True)
    _, last_n = _latest_weights_path(weights_dir)
    new_path = os.path.join(weights_dir, "model_weights_v%d.json" % (last_n + 1))
    reason = "维度 %s MAPE 超过 %g%%，权重下调 %g%% 并按比例重新分配" % (
        "、".join(over), MAPE_TRIGGER, DOWN_RATIO * 100)
    payload = {
        "version": last_n + 1,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "reason": reason,
        "dimension_weights": new_weights,
    }
    with open(new_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 5) 迭代报告追加权重变更记录
    rpt = report_path or validation_result.get("report_path")
    if rpt and os.path.isfile(rpt):
        lines = [
            "",
            "## 权重变更记录",
            "",
            "- 时间：%s" % payload["updated_at"],
            "- 调整原因：%s" % reason,
            "- 权重文件：%s" % os.path.basename(new_path),
            "",
            "| 维度 | 调整前权重 | 调整后权重 |",
            "| --- | --- | --- |",
        ]
        for d in old_weights:
            lines.append("| %s | %.4f | %.4f |" % (d, old_weights[d], new_weights[d]))
        lines.append("")
        with open(rpt, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    base.update({
        "updated": True,
        "adjusted_dimensions": over,
        "new_weights": new_weights,
        "new_weights_path": new_path,
        "reason": reason,
    })
    return base


# ---------------------------------------------------------------------------
# 飞书多维表格写入
# ---------------------------------------------------------------------------

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
FEISHU_TABLE_ID = "tblNSiw5782plUlY"  # 商品数据表
FEISHU_APP_TOKEN_DEFAULT = "NmYLbFbnJa3y1TsEujCcBoNEnzb"  # 多维表格 Base

# 商品原始字段 -> 多维表格字段名映射
FEISHU_FIELD_MAPPING = {
    "商品名称": "product_name",
    "品类": "category",
    "价格": "price",
    "页面佣金率": "commission_rate",
    "总销量": "total_sales",
    "总结算": "total_settlement",
    "达人带货占比": "talent_pct",
    "好评率": "review_rate",
    "出单达人数": "talent_count",
    "主力人群": "main_audience",
    "人群画像": "audience_profile",
    "驱动类型": "drive_type",
}

# 评分结果字段 -> 多维表格字段名映射
FEISHU_SCORE_FIELD_MAPPING = {
    "选品意愿分": "willingness_score",
    "带货效果分": "effect_score",
    "综合分": "total_score",
    "评级": "grade",
}


class FeishuError(RuntimeError):
    """飞书 API 调用失败。"""


def _feishu_request(method, path, token=None, body=None, params=None):
    """飞书开放 API 请求，返回响应 JSON 的 data 部分。"""
    url = FEISHU_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise FeishuError("飞书 API HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")))
    except urllib.error.URLError as e:
        raise FeishuError("飞书 API 网络错误: %s" % e.reason)
    if payload.get("code") != 0:
        raise FeishuError("飞书 API 错误 code=%s msg=%s" % (payload.get("code"), payload.get("msg")))
    return payload.get("data") or {}


def _get_tenant_access_token(app_id=None, app_secret=None):
    """用 app_id/app_secret 换取 tenant_access_token。"""
    app_id = app_id or os.environ.get("FEISHU_APP_ID")
    app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise FeishuError("缺少飞书凭证：请配置环境变量 FEISHU_APP_ID 和 FEISHU_APP_SECRET")
    data = _feishu_request("POST", "/auth/v3/tenant_access_token/internal",
                           body={"app_id": app_id, "app_secret": app_secret})
    token = data.get("tenant_access_token")
    if not token:
        raise FeishuError("获取 tenant_access_token 失败")
    return token


def _find_record_by_name(token, app_token, table_id, name):
    """按商品名称查找已有记录，返回 record_id；不存在返回 None。"""
    body = {
        "filter": {
            "conjunction": "and",
            "conditions": [{
                "field_name": FEISHU_FIELD_MAPPING["商品名称"],
                "operator": "is",
                "value": [name],
            }],
        },
        "page_size": 1,
    }
    data = _feishu_request("POST",
                           "/bitable/v1/apps/%s/tables/%s/records/search" % (app_token, table_id),
                           token=token, body=body)
    items = data.get("items") or []
    return items[0]["record_id"] if items else None


def save_to_feishu(product, score_result=None, app_token=None, table_id=FEISHU_TABLE_ID,
                   app_id=None, app_secret=None):
    """把商品评分结果写入飞书多维表格商品数据表。

    参数：
        product      商品输入 dict（商品名称必填；品类/价格/页面佣金率/好评率/
                     总销量/总结算/达人带货占比/出单达人数/主力人群/人群画像/驱动类型
                     等可选，缺省的字段不写入）
        score_result 可选，score_product(product) 的结果；不传则内部计算
        app_token    多维表格 Base 的 app_token，缺省取环境变量 FEISHU_APP_TOKEN，
                     再缺省用内置默认值 FEISHU_APP_TOKEN_DEFAULT
        table_id     数据表 ID，默认商品数据表 tblNSiw5782plUlY

    返回：{"action": "created"|"updated", "record_id": ...}
    写入前按商品名称查重，已存在则更新原记录，避免重复。
    """
    name = product.get("商品名称")
    if not name:
        raise ValueError("product 缺少商品名称")
    app_token = (app_token or os.environ.get("FEISHU_APP_TOKEN")
                 or FEISHU_APP_TOKEN_DEFAULT)

    result = score_result or score_product(product)

    # 组装字段：product 中存在的原始字段 + 评分结果字段
    fields = {FEISHU_FIELD_MAPPING[k]: product[k]
              for k in FEISHU_FIELD_MAPPING if product.get(k) is not None}
    fields.update({FEISHU_SCORE_FIELD_MAPPING[k]: result[k]
                   for k in FEISHU_SCORE_FIELD_MAPPING})

    token = _get_tenant_access_token(app_id, app_secret)
    record_id = _find_record_by_name(token, app_token, table_id, name)
    if record_id:
        _feishu_request("PUT",
                        "/bitable/v1/apps/%s/tables/%s/records/%s" % (app_token, table_id, record_id),
                        token=token, body={"fields": fields})
        return {"action": "updated", "record_id": record_id}
    data = _feishu_request("POST",
                           "/bitable/v1/apps/%s/tables/%s/records" % (app_token, table_id),
                           token=token, body={"fields": fields})
    return {"action": "created", "record_id": data["record"]["record_id"]}


if __name__ == "__main__":
    demo = {
        "商品名称": "Kismet风衣",
        "品类": "服装/风衣",
        "价格": 139.99,
        "页面佣金率": 20,
        "好评率": 98,
        "店铺评分": 95,
        "品牌类型": "新锐品牌",
        "月销量": 29500,
    }
    print(json.dumps(score_product(demo), ensure_ascii=False, indent=2))
