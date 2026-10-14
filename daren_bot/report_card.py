"""达人报告可视化长图卡片：数字+图表一图流（替代纯文字报告的展示层）。

数据口径与文字报告完全一致（读同一 talent 行 + ocr_raw_fields 兜底），
渲染失败由调用方回退纯文字，不阻塞推送。
"""
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .executors import _read_talent, _loads_dist, _to_pct_num, _fmt_wan

W = 1080
M = 48                      # 页边距
BLUE = (43, 90, 237)
DARK = (24, 32, 51)
GRAY = (110, 122, 148)
LIGHT = (245, 247, 251)
GREEN = (24, 160, 88)
ORANGE = (240, 140, 22)
RED = (224, 82, 82)
LINE = (228, 232, 240)

_FONT_TTC = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(_FONT_TTC, size, index=1 if bold else 0)
    except Exception:
        return ImageFont.truetype(_FONT_TTC, size)


def _text_w(draw, s, font):
    return draw.textbbox((0, 0), s, font=font)[2]


def _round(draw, box, r, fill):
    draw.rounded_rectangle(box, radius=r, fill=fill)


def _is_empty(v):
    if v in (None, "", [], {}):
        return True
    if isinstance(v, str) and v.strip() in ("[]", "{}"):
        return True                 # 列里存的 JSON 文本空容器
    if isinstance(v, (int, float)) and v == 0:
        return True
    if isinstance(v, dict):
        nums = [_to_pct_num(x) for x in v.values()]
        nums = [n for n in nums if n is not None]
        return bool(nums) and all(n == 0 for n in nums)
    return False


def _pick(t, ocr, key, default=""):
    v = t.get(key)
    if _is_empty(v):
        v = ocr.get(key)
    return default if _is_empty(v) else v


def _dist_items(v, name_key="category", pct_key="percentage"):
    """dict / [{name,percentage}] 数组 / 双重编码字符串 → [(name, pct)]，按值降序。"""
    if isinstance(v, list):
        items = [(str(it.get(name_key)), _to_pct_num(it.get(pct_key)))
                 for it in v if isinstance(it, dict) and it.get(name_key) is not None]
    else:
        d = _loads_dist(v)
        items = [(k, _to_pct_num(x)) for k, x in d.items()]
    items = [(k, n) for k, n in items if n is not None and n > 0 and k != '其他']
    items.sort(key=lambda kv: kv[1], reverse=True)
    return items


def _wrap(text, font, max_w, draw):
    """中文按字换行。"""
    lines, cur = [], ""
    for ch in str(text):
        if _text_w(draw, cur + ch, font) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------- 绘制

def render_card(talent_id: str, douyin_id: str, out_path: str) -> str:
    t = _read_talent(talent_id, douyin_id)
    if not t:
        raise RuntimeError("talent not found")
    raw = t.get("ocr_raw_fields") or ""
    try:
        ocr = json.loads(raw)
        if isinstance(ocr, str):
            ocr = json.loads(ocr)
    except Exception:
        ocr = {}
    if not isinstance(ocr, dict):
        ocr = {}

    # 先整体排版收集内容，再按内容量定高度
    im = Image.new("RGB", (W, 4000), "white")
    dr = ImageDraw.Draw(im)
    y = 0

    def sec_title(title):
        nonlocal y
        y += 34
        dr.text((M, y), title, font=_font(34, True), fill=DARK)
        y += 50

    # ── 头部色块 ──
    hdr_h = 300
    _round(dr, (0, 0, W, hdr_h), 0, BLUE)
    dr.text((M, 44), "达人数据报告", font=_font(26), fill=(190, 208, 255))
    name = t.get("name") or "-"
    dr.text((M, 88), name, font=_font(52, True), fill="white")
    dy = t.get("douyin_id") or douyin_id
    sub = f"抖音号 {dy}"
    city = t.get("city") or ""
    if city:
        sub += f"　{city}"
    if t.get("talent_type"):
        sub += f"　{t['talent_type']}"
    if t.get("level"):
        sub += f"　等级{t['level']}"
    if t.get("category"):
        sub += f"　{t['category']}"
    dr.text((M, 168), sub, font=_font(24), fill=(205, 218, 252))
    # 粉丝大数字
    foll = t.get("followers")
    if foll:
        fs = _fmt_wan(foll)
        dr.text((M, 214), fs, font=_font(56, True), fill="white")
        dr.text((M + _text_w(dr, fs, _font(56, True)) + 14, 240), "粉丝", font=_font(24), fill=(205, 218, 252))
        fg = _pick(t, ocr, "fan_growth")
        fgr = _pick(t, ocr, "fan_growth_rate")
        if fg or fgr:
            chip = f"近2月 {'+' if str(fg).startswith('+') or (isinstance(fg, int) and fg > 0) else ''}{fg or ''}（{fgr or '-'}）"
            _round(dr, (M + 260, 226, M + 260 + _text_w(dr, chip, _font(22)) + 36, 272), 23, (255, 255, 255, 40))
            dr.text((M + 278, 236), chip, font=_font(22), fill=BLUE)
    y = hdr_h + 6

    # ── 带货核心数字（6 宫格）──
    stats = []
    gt = t.get("total_gmv") or _pick(t, ocr, "total_gmv_text")
    if gt:
        stats.append(("累计GMV", _fmt_wan(gt) if isinstance(gt, (int, float)) else str(gt)))
    vg = _pick(t, ocr, "video_gpm_text") or t.get("video_gpm")
    if vg:
        stats.append(("视频GPM", str(vg)))
    ap = t.get("average_price") or _pick(t, ocr, "average_price")
    if ap:
        try:
            stats.append(("平均件单价", f"{float(ap):.0f}元"))
        except (TypeError, ValueError):
            stats.append(("平均件单价", str(ap)))
    ms = _pick(t, ocr, "monthly_settlement")
    if ms:
        stats.append(("月结算", str(ms)))
    if t.get("total_products"):
        stats.append(("带过商品", f"{t['total_products']}品"))
    if t.get("total_shops"):
        stats.append(("合作店铺", f"{t['total_shops']}店"))
    svs = _pick(t, ocr, "single_video_settlement")
    if svs and len(stats) < 6:
        stats.append(("单视频结算", str(svs)))
    if stats:
        sec_title("带货能力")
        cell_w = (W - 2 * M - 40) // 3
        for i, (label, val) in enumerate(stats[:6]):
            cx = M + (i % 3) * (cell_w + 20)
            cy = y + (i // 3) * 132
            _round(dr, (cx, cy, cx + cell_w, cy + 112), 16, LIGHT)
            dr.text((cx + 24, cy + 18), label, font=_font(22), fill=GRAY)
            dr.text((cx + 24, cy + 52), str(val), font=_font(34, True), fill=DARK)
        y += ((len(stats[:6]) + 2) // 3) * 132 + 10

    # ── 内容总览 ──
    ov = []
    ct = _pick(t, ocr, "content_total")
    if ct:
        ov.append(f"发布 {ct} 条")
    wt_ = _pick(t, ocr, "watch_total")
    if wt_:
        ov.append(f"总观看 {wt_}万" if isinstance(wt_, (int, float)) else f"总观看 {wt_}")
    vr = _pick(t, ocr, "video_ratio")
    if vr:
        try:
            ov.append(f"视频占比 {float(vr):.0f}%")
        except (TypeError, ValueError):
            pass
    vap = _pick(t, ocr, "video_avg_plays")
    if vap:
        ov.append(f"视频均播 {vap}")
    if ov:
        sec_title("内容表现")
        dr.text((M, y), "　｜　".join(str(x) for x in ov), font=_font(26), fill=DARK)
        y += 46

    # ── 粉丝画像 ──
    gender = _dist_items(_pick(t, ocr, "fan_gender"), "男性", "女性") or \
        _dist_items(t.get("fan_gender"))
    ages = _dist_items(t.get("fan_age") or _pick(t, ocr, "fan_age"))
    region = _dist_items(t.get("fan_region") or _pick(t, ocr, "fan_region"))
    crowd = _dist_items(t.get("fan_crowd") or _pick(t, ocr, "fan_crowd"))
    consume = _dist_items(_pick(t, ocr, "fan_consume_level"))
    purchase = _pick(t, ocr, "fan_purchase_pref")
    if gender or ages:
        sec_title("粉丝画像")
        # 左：性别 donut
        gx, gy = M + 130, y + 110
        if gender:
            total = sum(n for _, n in gender) or 1
            start = -90
            colors = [(64, 128, 255), (255, 128, 160)]
            for i, (k, n) in enumerate(gender[:2]):
                extent = n / total * 360
                dr.pieslice((gx - 90, gy - 90, gx + 90, gy + 90), start, start + extent,
                            fill=colors[i % 2])
                start += extent
            dr.ellipse((gx - 52, gy - 52, gx + 52, gy + 52), fill="white")
            for i, (k, n) in enumerate(gender[:2]):
                ly = gy - 40 + i * 44
                dr.rectangle((M - 8, ly, M + 8, ly + 16), fill=colors[i % 2])
                dr.text((M + 20, ly - 4), f"{k} {n:.0f}%", font=_font(24), fill=DARK)
        # 右：年龄横条
        bx = M + 420
        if ages:
            amax = ages[0][1] or 1
            dr.text((bx, y), "年龄分布", font=_font(24), fill=GRAY)
            bar_max = W - M - bx - 150          # 给百分比标签留位
            for i, (k, n) in enumerate(ages[:4]):
                ry = y + 44 + i * 46
                dr.text((bx, ry), str(k), font=_font(22), fill=DARK)
                bw = int(bar_max * n / amax)
                _round(dr, (bx + 120, ry + 4, bx + 120 + max(bw, 8), ry + 26), 11, BLUE)
                dr.text((bx + 128 + bw + 8, ry), f"{n:.0f}%", font=_font(20), fill=GRAY)
        y += 236
        chips = []
        if region:
            chips.append(f"地域 {region[0][0]} {region[0][1]:.0f}%")
        if crowd:
            chips.append(f"人群 {crowd[0][0]} {crowd[0][1]:.0f}%")
        if consume:
            chips.append(f"消费 {consume[0][0]} {consume[0][1]:.0f}%")
        if purchase:
            chips.append(f"购买偏好 {purchase}")
        if chips:
            cy = y
            cx = M
            for c in chips:
                cw = _text_w(dr, c, _font(22)) + 36
                if cx + cw > W - M:
                    cx = M
                    cy += 52
                _round(dr, (cx, cy, cx + cw, cy + 42), 21, LIGHT)
                dr.text((cx + 18, cy + 8), c, font=_font(22), fill=DARK)
                cx += cw + 16
            y = cy + 52

    # ── 带货结构 ──
    cats = _dist_items(_pick(t, ocr, "category_distribution"))
    brands = _dist_items(_pick(t, ocr, "cooperating_brands")
                         or _pick(t, ocr, "cooperating_brands_distribution"), "brand", "percentage")
    if cats or brands:
        sec_title("带货结构")
        col_w = (W - 2 * M - 40) // 2

        def _bars(items, x0, title):
            dr.text((x0, y), title, font=_font(24), fill=GRAY)
            if not items:
                dr.text((x0, y + 44), "暂无数据", font=_font(22), fill=GRAY)
                return
            m = items[0][1] or 1
            bar_max = col_w - 150               # 给百分比标签留位，不出血
            for i, (k, n) in enumerate(items[:5]):
                ry = y + 46 + i * 48
                dr.text((x0, ry), str(k)[:8], font=_font(20), fill=DARK)
                bw = int(bar_max * n / m)
                _round(dr, (x0 + 130, ry + 3, x0 + 130 + max(bw, 8), ry + 25), 11,
                       BLUE if title.startswith("类目") else (90, 170, 255))
                pct = f"{n:.1f}%"
                pw = _text_w(dr, pct, _font(18))
                if bw > pw + 22:            # 标签放条内（白字右对齐），避免出血到右列
                    dr.text((x0 + 130 + bw - 8 - pw, ry + 1), pct, font=_font(18), fill="white")
                else:
                    dr.text((x0 + 138 + bw + 6, ry + 1), pct, font=_font(18), fill=GRAY)
        _bars(cats, M, "类目占比")
        _bars(brands, M + col_w + 40, "品牌集中度")
        y += 46 + 5 * 48 + 6

    # ── 口碑评价 ──
    rc_ = _pick(t, ocr, "review_count")
    reviews = _pick(t, ocr, "reviews")
    if isinstance(reviews, str):
        try:
            reviews = json.loads(reviews)
        except Exception:
            reviews = None
    if rc_:
        sec_title("口碑评价")
        rg = _pick(t, ocr, "review_good", 0)
        dr.text((M, y), f"全部 {rc_} 条　好评 {rg or 0} 条　好评率 "
                        f"{(float(rg or 0) / float(rc_) * 100):.0f}%",
                font=_font(26), fill=DARK)
        y += 52
        if isinstance(reviews, list):
            for rv in reviews[:2]:
                if not isinstance(rv, dict):
                    continue
                shop = rv.get("商家") or rv.get("商家名") or "?"
                star = rv.get("星级") or ""
                body = (rv.get("评价原文") or rv.get("内容") or "")[:50]
                date_ = rv.get("日期") or ""
                phone = rv.get("联系电话") or ""
                _round(dr, (M, y, W - M, y + 96), 14, LIGHT)
                dr.text((M + 24, y + 14), f"{shop} {star}".strip(), font=_font(24, True), fill=DARK)
                dr.text((W - M - 24 - _text_w(dr, str(date_), _font(20)), y + 18),
                        str(date_), font=_font(20), fill=GRAY)
                if body:
                    dr.text((M + 24, y + 52), body, font=_font(22), fill=DARK)
                if phone:
                    dr.text((W - M - 24 - _text_w(dr, f"📞 {phone}", _font(22)), y + 52),
                            f"📞 {phone}", font=_font(22), fill=GREEN)
                y += 112

    # ── 账号评估 ──
    verdict = t.get("risk_rating") or ""
    analysis = (t.get("ai_analysis") or "").strip()
    if verdict or analysis:
        sec_title("账号评估")
        vcolor = {"潜力型": GREEN, "稳健型": BLUE, "待提升型": ORANGE}.get(verdict, BLUE)
        if verdict:
            _round(dr, (M, y, M + _text_w(dr, verdict, _font(28, True)) + 48, y + 52), 26, vcolor)
            dr.text((M + 24, y + 10), verdict, font=_font(28, True), fill="white")
            y += 70
        # 解析亮点/风险/建议
        blocks = {"亮点": [], "风险": [], "建议": []}
        cur = None
        for ln in analysis.splitlines():
            ln = ln.strip()
            for tag in blocks:
                if ln.startswith(f"【{tag}】"):
                    cur = tag
                    break
            else:
                if cur and ln:
                    blocks[cur].append(ln.lstrip("· ").strip())
        colors = {"亮点": GREEN, "风险": RED, "建议": BLUE}
        for tag, items in blocks.items():
            if not items:
                continue
            dr.text((M, y), tag, font=_font(24, True), fill=colors[tag])
            y += 38
            for it in items[:3]:
                for j, wl in enumerate(_wrap(f"· {it}", _font(23), W - 2 * M - 30, dr)[:2]):
                    dr.text((M + 14, y), wl, font=_font(23), fill=DARK)
                    y += 34
            y += 8
        y += 6

    # ── 页脚 ──
    y += 20
    dr.line((M, y, W - M, y), fill=LINE, width=2)
    y += 18
    dr.text((M, y), f"数据来自巨量百应　采集于 {time.strftime('%Y-%m-%d %H:%M')}",
            font=_font(20), fill=GRAY)
    y += 44

    card = im.crop((0, 0, W, y + 10))
    card.save(out_path, "PNG")
    return out_path
