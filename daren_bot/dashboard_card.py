"""达人个人看板长图（docs/DASHBOARD-DESIGN.md，版式老大 18:02 过，P0 修正 18:09）。

三层定位：本图=第一层结论层（达人只看这个）；多维表格=后台账本；admin=管理。
铁律：
- 四项体检分走档位映射（§3），模型不拍分；字段缺失显示"暂不足"/"样本不足"
- 成交环比只做档位方向（同档=档位持平/跨档=升一档），不输出增量万数（伪精确）
- 账号结论四状态规则映射：预警>增长(高潜)>稳定(稳态)>数据不足(攒数据)
- "能带/慎带/别带"词只给商品；趋势 <4 周显示占位态，零假点
"""
import json
import logging
import re

from . import db
from .report_card import (_font, _round, _vgrad, _text_w, W, M,
                          BLUE, DARK, GRAY, LIGHT, GREEN, ORANGE, RED, LINE,
                          _draw_link)
from .weekly import gmv_mid_wan, _num, _ocr

log = logging.getLogger("daren-bot.dashboard")

VERDICT_RE = re.compile(r"(能带|慎带|别带)")

# ---------------- 四项体检分（档位映射，§3） ----------------

def gmv_bucket_score(mid_wan):
    if mid_wan is None:
        return None
    for threshold, score in ((500, 95), (250, 88), (100, 80), (50, 68),
                             (10, 50)):
        if mid_wan >= threshold:
            return score
    return 30


def followers_score(f):
    if f is None:
        return None
    for threshold, score in ((1000000, 95), (100000, 80), (10000, 65),
                             (1000, 50)):
        if f >= threshold:
            return score
    return 35


def gpm_score(gpm_mid, video_settlement_ratio=None):
    if gpm_mid is not None:
        for threshold, score in ((1000, 90), (500, 80), (100, 68)):
            if gpm_mid >= threshold:
                return score
        return 50
    # 兜底：视频为主型（成交几乎全在视频场景）
    if video_settlement_ratio is not None and video_settlement_ratio >= 80:
        return 65
    return None


def reputation_score(rating, good, total):
    """P1：评价数 <5 显示样本不足（2条全好评=100 是噪音）。"""
    if rating is None or not total:
        return None
    if total < 5:
        return None  # 样本不足（与暂不足区分：有数据但不够）
    return round(rating / 5 * 70 + good / total * 30)


def _parse_gpm_mid(text: str):
    if not text:
        return None
    nums = re.findall(r"(\d+(?:\.\d+)?)", str(text).replace(",", ""))
    if not nums:
        return None
    vals = [float(n) for n in nums[:2]]
    return sum(vals) / len(vals)


def compute_scores(t: dict, ocr: dict) -> list:
    """返回 [{label, score|None, kind, note, short}]，严格按 §3 档位表。"""
    out = []
    # 成交能力
    mid = gmv_mid_wan(t.get("total_gmv_text"))
    s = gmv_bucket_score(mid)
    out.append({"label": "成交能力", "score": s, "kind": "ability",
                "note": "近60天成交档位", "short": "暂不足"})
    # 粉丝规模（客观维度，中性蓝，不判好坏）
    f = _num(t, "followers")
    s = followers_score(f)
    rate = str(ocr.get("fan_growth_rate") or "")
    note = "客观体量，不判好坏；" + ("涨粉中" if rate.startswith("+")
                                    else "增长放缓" if f else "")
    out.append({"label": "粉丝规模", "score": s, "kind": "neutral",
                "note": note, "short": "暂不足"})
    # 内容效率（阈值待校准，P1）
    gpm_mid = _parse_gpm_mid(ocr.get("video_gpm_text") or t.get("video_gpm"))
    s = gpm_score(gpm_mid, _num(ocr, "video_settlement_ratio"))
    out.append({"label": "内容效率", "score": s, "kind": "ability",
                "note": "视频观看→成交转化·阈值待校准", "short": "暂不足"})
    # 履约口碑（P1：review_count<5 样本不足）
    rating = _num(t, "rating_score")
    rc, rg = _num(ocr, "review_count"), _num(ocr, "review_good")
    s = reputation_score(rating, rg or 0, rc or 0)
    short = "样本不足" if (rating is not None and (rc or 0) < 5) else "暂不足"
    out.append({"label": "履约口碑", "score": s, "kind": "ability",
                "note": f"商家评价{int(rc or 0)}条", "short": short})
    return out


# ---------------- 账号结论四状态（规则映射，§4+P0 修正） ----------------

VERDICT_META = {
    "预警": (RED, "预警型"),
    "高潜": (GREEN, "增长型"),
    "稳态": (BLUE, "稳定型"),
    "攒数据": (GRAY, "数据不足型"),
}


def account_verdict(cur: dict, prev: dict, anomalies: list):
    """优先级：预警 > 攒数据(无基线) > 高潜(跨一档或粉丝周增≥20%) > 稳态。
    P0-2：高潜需跨一个成交档（同档抖动不算）；无基线差分=null 落攒数据。"""
    if anomalies:
        return "预警"
    if not cur or not prev:
        return "攒数据"
    cm = gmv_mid_wan(cur.get("total_gmv_text"))
    pm = gmv_mid_wan(prev.get("total_gmv_text"))
    cf, pf = _num(cur, "followers"), _num(prev, "followers")
    bucket_up = cm is not None and pm is not None and cm > pm
    fan_up = cf is not None and pf and (cf - pf) / pf >= 0.20
    if bucket_up or fan_up:
        return "高潜"
    return "稳态"


def verdict_headline(word: str, t: dict, ocr: dict) -> str:
    """一句话模板（真实数字拼装；数据不够不硬凑爽句）。"""
    name = t.get("name") or "你"
    if word == "高潜":
        f = _num(t, "followers")
        g = t.get("total_gmv_text") or ""
        return (f"{int(f) if f else '—'} 个粉丝带出 {g} 成交，抓紧接同类高佣新品"
                if f and g else f"{name} 近期数据向上，值得加大投入")
    if word == "预警":
        return f"{name} 的数据有下滑信号，本周先看异常提醒再行动"
    if word == "稳态":
        return f"{name} 经营平稳，保持节奏并试 1-2 个新品类"
    return f"{name} 数据还在积累，连采几周后出完整结论"


# ---------------- 选品记录（TOP3 数据源） ----------------

def save_product_verdict(tenant_key: str, title: str, md: str) -> None:
    """商品/截图分析完成时落一条选品记录（供看板 TOP3 取数）。"""
    m = VERDICT_RE.search(md or "")
    if not m:
        return
    try:
        with db.db_ctx() as conn:
            import time as _t
            conn.execute(
                "INSERT INTO bot_product_verdicts "
                "(tenant_key, name, verdict, why, analyzed_at, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (tenant_key, (title or "商品")[:60], m.group(1),
                 "", _t.strftime("%m-%d"), int(_t.time())))
    except Exception:
        log.exception("save product verdict failed: tenant=%s", tenant_key)


def recent_picks(tenant_key: str, limit: int = 3) -> list:
    with db.db_ctx() as conn:
        rows = conn.execute(
            "SELECT name, verdict, analyzed_at FROM bot_product_verdicts "
            "WHERE tenant_key=? ORDER BY created_at DESC LIMIT ?",
            (tenant_key, limit)).fetchall()
        return [dict(r) for r in rows]


# ---------------- 渲染 ----------------

def render_dashboard(t, cur: dict, prev: dict, anomalies: list,
                     picks: list, out_path: str,
                     collected_at: str = "", week_label: str = "",
                     link: str = "") -> str:
    from PIL import Image, ImageDraw
    ocr = _ocr(cur)
    scores = compute_scores(cur, ocr)
    word = account_verdict(cur, prev, anomalies)
    vcolor, state_tag = VERDICT_META[word]

    im = Image.new("RGB", (W, 2600), "white")
    dr = ImageDraw.Draw(im)
    y = 0

    def sec(title):
        nonlocal y
        y += 30
        dr.text((M, y), title, font=_font(32, True), fill=DARK)
        y += 50

    # 头部
    hdr_h = 250
    _vgrad(dr, (0, 0, W, hdr_h), (43, 90, 237), (18, 40, 120))
    dr.text((M, 40), "达人个人看板", font=_font(26), fill=(190, 208, 255))
    dr.text((M, 82), t.get("name") or "-", font=_font(54, True), fill="white")
    sub = f"抖音号 {t.get('douyin_id') or '-'}"
    for k in ("city", "level", "category"):
        if t.get(k):
            sub += f"　{t[k]}"
    dr.text((M, 164), sub, font=_font(24), fill=(205, 218, 252))
    stamp = collected_at or (week_label and f"{week_label} 周报") or ""
    if stamp:
        dr.text((W - M - _text_w(dr, stamp, _font(22)), 48),
                stamp, font=_font(22), fill=(190, 208, 255))
    y = hdr_h + 8

    # ① 结论区
    sec("结论")
    _round(dr, (M, y, W - M, y + 190), 24, LIGHT)
    cx, cy, r = M + 95, y + 95, 52
    dr.ellipse((cx - r, cy - r, cx + r, cy + r), fill=vcolor)
    dr.text((cx - _text_w(dr, word, _font(34, True)) // 2, cy - 24),
            word, font=_font(34, True), fill="white")
    tag = state_tag
    tw = _text_w(dr, tag, _font(20)) + 28
    _round(dr, (M + 185, y + 22, M + 185 + tw, y + 54), 16, LIGHT)
    dr.text((M + 199, y + 27), tag, font=_font(20), fill=vcolor)
    hl = verdict_headline(word, cur, ocr)
    while _text_w(dr, hl, _font(30, True)) > W - M - (M + 185) - 20 and len(hl) > 6:
        hl = hl[:-2]
    dr.text((M + 185, y + 66), hl, font=_font(30, True), fill=DARK)
    g = cur.get("total_gmv_text") or ""
    svs = ocr.get("single_video_settlement") or ""
    line2 = f"近60天带货 {g}，一条视频平均结算 {svs}" if g and svs else \
        (f"近60天带货 {g}" if g else "成交数据积累中")
    dr.text((M + 185, y + 118), line2, font=_font(22), fill=GRAY)
    dr.text((M + 185, y + 152), "数据来自我们系统", font=_font(18), fill=GRAY)
    y += 190 + 10

    # ② 四项体检
    sec("账号体检")
    _round(dr, (M, y, W - M, y + 210), 24, LIGHT)
    dr.text((M + 50, y + 60), "四项体检", font=_font(40, True), fill=BLUE)
    dr.text((M + 50, y + 130), "单项分含义明确可解释；", font=_font(20), fill=GRAY)
    dr.text((M + 50, y + 160), "总分与同量级对比攒数后上线", font=_font(20), fill=GRAY)
    bx = M + 400
    bw_total = W - M - bx - 200
    for i, it in enumerate(scores):
        iy = y + 22 + i * 46
        dr.text((bx, iy), it["label"], font=_font(22), fill=DARK)
        bar_x = bx + 110
        _round(dr, (bar_x, iy + 6, bar_x + bw_total, iy + 22), 8, (228, 232, 240))
        if it["score"] is None:
            dr.text((bar_x, iy - 2), it["short"], font=_font(22), fill=GRAY)
        else:
            c = BLUE if it["kind"] == "neutral" else \
                (GREEN if it["score"] >= 80 else ORANGE if it["score"] >= 55 else RED)
            _round(dr, (bar_x, iy + 6, bar_x + int(bw_total * it["score"] / 100),
                        iy + 22), 8, c)
            dr.text((bar_x + bw_total + 12, iy - 2), str(it["score"]),
                    font=_font(22, True), fill=c)
        dr.text((bx, iy + 24), it["note"], font=_font(16), fill=GRAY)
    y += 210 + 10

    # 折叠线
    dr.line((M, y + 18, W - M, y + 18), fill=LINE, width=2)
    dr.text((W // 2 - _text_w(dr, "﹀ 下滑看更多", _font(22)) // 2, y + 28),
            "﹀ 下滑看更多", font=_font(22), fill=GRAY)
    y += 70

    # ③ 核心数据（成交只出档位方向；粉丝=时点环比）
    sec("核心数据")
    cm = gmv_mid_wan(cur.get("total_gmv_text"))
    pm = gmv_mid_wan(prev.get("total_gmv_text")) if prev else None
    cf = _num(cur, "followers")
    pf = _num(prev, "followers") if prev else None
    if pm is None:
        gmv_chg, gmv_sub, gmv_c = "—", "攒基线中", GRAY
    elif cm == pm:
        gmv_chg, gmv_sub, gmv_c = "持平", "近7天成交档位", GRAY
    elif cm > pm:
        gmv_chg, gmv_sub, gmv_c = "▲ 较上周升一档", f"上周档 {prev.get('total_gmv_text')}", GREEN
    else:
        gmv_chg, gmv_sub, gmv_c = "▼ 较上周降一档", f"上周档 {prev.get('total_gmv_text')}", RED
    if pf:
        d = (cf - pf) / pf if cf is not None else 0
        f_chg = f"{'+' if d >= 0 else ''}{d*100:.1f}%"
        f_c = GREEN if d >= 0 else RED
    else:
        f_chg, f_c = "—", GRAY
    gpm_mid = _parse_gpm_mid(ocr.get("video_gpm_text"))
    gpm_prev = _parse_gpm_mid(_ocr(prev).get("video_gpm_text")) if prev else None
    if gpm_mid is not None and gpm_prev:
        d = (gpm_mid - gpm_prev) / gpm_prev
        if abs(d) < 0.05:
            g_chg, g_c, g_sub = "持平", GRAY, "vs 上周（阈值±5%）"
        else:
            g_chg = f"{'▲' if d >= 0 else '▼'} {abs(d)*100:.0f}%"
            g_c, g_sub = (GREEN if d >= 0 else RED), "vs 上周"
    else:
        g_chg, g_c, g_sub = "—", GRAY, "暂不足"
    ap = cur.get("average_price")
    stats = [("粉丝", f"{int(cf)}" if cf is not None else "—", f_chg, f_c, "vs 上周（时点）"),
             ("近60天带货成交", cur.get("total_gmv_text") or "—", gmv_chg, gmv_c, gmv_sub),
             ("带货效率（千次观看成交）",
              f"{int(gpm_mid)}元" if gpm_mid else "—", g_chg, g_c, g_sub),
             ("人均下单金额", f"{float(ap):.0f}元" if ap else "—", "—", GRAY,
              "vs 上周" if ap else "暂不足")]
    cw = (W - 2 * M - 3 * 24) // 4
    for i, (label, val, chg, cc, sub) in enumerate(stats):
        x0 = M + i * (cw + 24)
        _round(dr, (x0, y, x0 + cw, y + 190), 20, LIGHT)
        dr.text((x0 + 26, y + 22), label, font=_font(19), fill=GRAY)
        fs = _font(36, True) if len(str(val)) > 6 else _font(44, True)
        dr.text((x0 + 26, y + 60), str(val), font=fs, fill=DARK)
        f = _font(20, True) if len(str(chg)) > 8 else _font(24, True)
        dr.text((x0 + 26, y + 130), str(chg), font=f, fill=cc)
        dr.text((x0 + 26, y + 162), sub, font=_font(16), fill=GRAY)
    y += 190 + 10

    # ④ 涨粉趋势（<4 周占位态，零假点）
    sec("涨粉趋势")
    _round(dr, (M, y, W - M, y + 240), 20, LIGHT)
    n = 0
    try:
        with db.db_ctx() as conn:
            n = conn.execute(
                "SELECT COUNT(DISTINCT week_no) n FROM bot_weekly_snapshots "
                "WHERE douyin_id=?", (t.get("douyin_id") or "",)).fetchone()["n"]
    except Exception:
        pass
    if n >= 4:
        _draw_trend(dr, t.get("douyin_id") or "", M, y)
    else:
        dr.text((M + 40, y + 80), "趋势生成中…", font=_font(34, True), fill=GRAY)
        dr.text((M + 40, y + 140),
                f"已连续采集 {n} / 4 周，攒满 4 周后自动出涨粉折线图",
                font=_font(24), fill=GRAY)
        dr.text((M + 40, y + 184), "成交趋势待平台分天粒度接入后上线",
                font=_font(20), fill=GRAY)
    y += 240 + 10

    # ⑤ 选品 TOP3（带分析日期）
    sec("本周精选 TOP3")
    if picks:
        _round(dr, (M, y, W - M, y + 44), 12, LIGHT)
        dr.text((M + 20, y + 10),
                f"来自 {picks[-1]['analyzed_at']} 起的选品分析，会随时间变化",
                font=_font(18), fill=GRAY)
        y += 44 + 10
        pick_color = {"能带": GREEN, "慎带": ORANGE, "别带": RED}
        for p in picks:
            _round(dr, (M, y, W - M, y + 108), 18, LIGHT)
            c = pick_color.get(p["verdict"], GRAY)
            dr.ellipse((M + 28, y + 26, M + 84, y + 82), fill=c)
            dr.text((M + 40, y + 38), p["verdict"], font=_font(22, True),
                    fill="white")
            dr.text((M + 112, y + 22), p["name"], font=_font(28, True), fill=DARK)
            dr.text((M + 112, y + 64), f"分析于 {p['analyzed_at']}",
                    font=_font(20), fill=GRAY)
            y += 108 + 16
    else:
        _round(dr, (M, y, W - M, y + 120), 18, LIGHT)
        dr.text((M + 40, y + 40), "还没有选品分析", font=_font(28, True), fill=DARK)
        dr.text((M + 40, y + 80), "发我商品链接或截图，我来出能带/慎带/别带结论",
                font=_font(22), fill=GRAY)
        y += 120 + 10
    y += 6

    # 异常关注（有异常才画，接在选品后、页脚前）：emoji 字体缺字形，改画色点
    if anomalies:
        sec("异常关注")
        for emoji, text in anomalies:
            c = {"🔴": RED, "🟡": ORANGE, "🟢": GREEN}.get(emoji, GRAY)
            _round(dr, (M, y, W - M, y + 88), 16, LIGHT)
            dr.ellipse((M + 30, y + 34, M + 54, y + 58), fill=c)
            dr.text((M + 76, y + 16), text,
                    font=_font(24, True), fill=c)
            y += 88 + 14

    # 页脚
    y = _draw_link(dr, y, link)
    dr.text((M, y), "数据来自我们系统 · 每周一自动更新", font=_font(20), fill=GRAY)
    dr.text((M, y + 32), "选品结论会随时间变化，带货前把商品链接重新发我确认一次",
            font=_font(20), fill=GRAY)
    y += 90

    im.crop((0, 0, W, min(y + 20, 2600))).save(out_path)
    return out_path


def _draw_trend(dr, douyin_id: str, M: int, y: int) -> None:
    """周快照粉丝折线（≥4 点才调用）。"""
    with db.db_ctx() as conn:
        rows = conn.execute(
            "SELECT week_no, snapshot FROM bot_weekly_snapshots "
            "WHERE douyin_id=? ORDER BY week_no DESC LIMIT 8",
            (douyin_id,)).fetchall()
    pts = []
    for r in reversed(rows):
        try:
            d = json.loads(r["snapshot"])
            f = float(d.get("followers") or 0)
            if f:
                pts.append((str(r["week_no"])[-2:], f))
        except Exception:
            continue
    if len(pts) < 4:
        return
    gx0, gy0 = M + 50, y + 190
    gw, gh = W - 2 * M - 100, 130
    vals = [p[1] for p in pts]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    step = gw / (len(pts) - 1)
    xy = [(gx0 + i * step, gy0 - (v - lo) / span * gh)
          for i, (_, v) in enumerate(pts)]
    for i in range(len(xy) - 1):
        dr.line((xy[i], xy[i + 1]), fill=BLUE, width=5)
    for px, py in xy:
        dr.ellipse((px - 7, py - 7, px + 7, py + 7), fill=BLUE)
    for i, (lbl, _) in enumerate(pts):
        dr.text((gx0 + i * step - 20, gy0 + 12), lbl, font=_font(18), fill=GRAY)
