#!/usr/bin/env python3
"""触达卡 v2（2026-11-18，dq_556ca64c241c）：达人 + 匹配品 + 佣金 三联卡。

背景
----
协调边 19:29 派单：商品建档完成后，把「达人+匹配品+佣金」三联卡组装进日报推荐段，
商务拿到手就能直接开口（④ 效率翻倍前置）。建档战役当日核验为**已 47/47 全挂**
（dq_8aa5f9433888 待验），本模块随即施工。

设计要点
--------
1. **确定性组装，不靠模型自觉** —— 卡片由本模块直读 DB 生成，拼进日报正文，
   模型写不写它的判断不影响商务拿到带品信息（对比：让模型自己提品 = 有概率漏）。
2. **匹配优先级**：`product_talent_match`（有 match_score/match_reason 的既有匹配表）
   优先；无匹配记录回退到「已挂平台 ID 且有真实出单 GMV」的商品按 GMV 排序。
   两条路都取不到 ⇒ 明示「暂无可配品」，**不编造**（§143 铁律）。
3. **佣金口径单一真源** —— 一律走 `product_facts.commission_rate_of` /
   `commission_note`（分档结构取最高档），不在此处另写一套解析。
4. **池内真值优先** —— 排序加分项是 `platform_pay_gmv`（我方机构真实出单），
   不是月销/浏览量；商务开口讲的是「这品我们自己在出单」。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from daren_cockpit.tools import product_facts

CARD_HEADING = '【带品建议】'

_MATCH_SQL = '''
SELECT p.id, p.name, p.price, p.commission_rates, p.platform_pay_gmv,
       m.match_score, m.match_reason
FROM product_talent_match m JOIN products p ON p.id = m.product_id
WHERE m.talent_id = ?
ORDER BY m.match_score DESC, COALESCE(p.platform_pay_gmv, 0) DESC
'''

_FALLBACK_SQL = '''
SELECT id, name, price, commission_rates, platform_pay_gmv
FROM products
WHERE platform_product_id IS NOT NULL AND platform_product_id != ''
  AND COALESCE(platform_pay_gmv, 0) > 0
ORDER BY platform_pay_gmv DESC
'''


def _fmt_gmv(gmv: Any) -> str:
    """GMV 人读格式化：≥1万 用万，其余原样。"""
    try:
        g = float(gmv or 0)
    except (TypeError, ValueError):
        return ''
    if g <= 0:
        return ''
    if g >= 10000:
        return f'我方30日GMV ¥{g / 10000:.1f}万'
    return f'我方30日GMV ¥{g:.0f}'


def match_products_for_talent(conn, talent_id: str, top_n: int = 2) -> list[dict]:
    """单个达人的带品候选（确定性，top_n 条）。

    返回 [{'name','price','rate','note','gmv_text','source'}]；空表 = 真无匹配。
    source: 'match'（匹配表）/ 'fallback'（全店热销回退）。
    """
    tid = str(talent_id or '').strip()
    if not tid:
        return []
    out: list[dict] = []
    try:
        rows = conn.execute(_MATCH_SQL, (tid,)).fetchall()
    except sqlite3.Error:  # 表缺失时走回退
        rows = []
    for r in rows:
        prod = {'name': r[1], 'price': r[2], 'commission_rates': r[3],
                'platform_pay_gmv': r[4]}
        rate = product_facts.commission_rate_of(prod)
        if not prod.get('name') or rate <= 0:
            continue                                # 无佣金真值的不推（商务开口必报佣金）
        out.append({
            'name': str(prod['name']),
            'price': product_facts.price_of(prod),
            'rate': rate,
            'note': product_facts.commission_note(prod),
            'gmv_text': _fmt_gmv(prod.get('platform_pay_gmv')),
            'source': 'match',
        })
        if len(out) >= top_n:
            return out
    if out:                                          # 匹配表有货 ⇒ 直接用，不混回退
        return out
    try:
        frows = conn.execute(_FALLBACK_SQL).fetchall()
    except sqlite3.Error:
        frows = []
    for r in frows:
        prod = {'name': r[1], 'price': r[2], 'commission_rates': r[3],
                'platform_pay_gmv': r[4]}
        rate = product_facts.commission_rate_of(prod)
        if not prod.get('name') or rate <= 0:
            continue
        out.append({
            'name': str(prod['name']),
            'price': product_facts.price_of(prod),
            'rate': rate,
            'note': product_facts.commission_note(prod),
            'gmv_text': _fmt_gmv(prod.get('platform_pay_gmv')),
            'source': 'fallback',
        })
        if len(out) >= top_n:
            break
    return out


def _prod_bit(p: dict, idx: int) -> str:
    price = f'¥{p["price"]:g}' if p.get('price') else '价待核'
    bit = f'{idx}.{p["name"][:20]} {price} 佣金{p["rate"]:g}%'
    if p.get('gmv_text'):
        bit += f'（{p["gmv_text"]}）'
    return bit


def build_outreach_cards(conn, recs, top_n: int = 2) -> str:
    """把推荐名单组装成三联卡文本段（无推荐 ⇒ 空串）。

    recs: [{'talent_id','talent_name'}]（_square_normalize_recs 的输入形态）。
    每个达人一行：· 昵称 → ①品 ¥价 佣金X% ②品 …；无匹配 ⇒ 明示暂无可配品。
    """
    lines = []
    for r in (recs or []):
        if not isinstance(r, dict):
            continue
        tid = str(r.get('talent_id') or '').strip()
        name = str(r.get('talent_name') or tid).strip()[:30]
        if not tid:
            continue
        prods = match_products_for_talent(conn, tid, top_n=top_n)
        if prods:
            bits = ' '.join(_prod_bit(p, i + 1) for i, p in enumerate(prods))
            lines.append(f'· {name} → {bits}')
        else:
            lines.append(f'· {name} → 暂无可配品（库内无匹配记录，商务自行选品）')
    if not lines:
        return ''
    return CARD_HEADING + '\n' + '\n'.join(lines)
