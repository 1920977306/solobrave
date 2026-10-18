"""订阅式周报 + 异常预警（docs/WEEKLY-REPORT-DESIGN.md）。

两段式调度：
- 采集窗（周日 20:00 → 周一 08:00）：主动把订阅租户的绑定号错峰送入采集队列
  （payload.auto=1，系统账计费，不扣达人余额）；失败 2h 后重试 1 次。
- 推送窗（周一 09:37 + 租户 slot）：为本周有快照的租户生成并推送周报
  （含数据缺失版/首周基线版），OpenAPI 推送，不走 ws。

口径铁律：百应数据是「近60天滚动窗口」而非自然周，周报全程写
「滚动窗口对比」，不编造"本周"概念；异常规则带活跃上下文门槛，宁漏勿滥。
"""
import hashlib
import json
import logging
import re
import threading
import time
import datetime
from pathlib import Path

from . import db
from . import executors

log = logging.getLogger("daren-bot.weekly")

COLLECT_WINDOW_HOURS = 12          # 周日20:00 → 周一08:00
RETRY_GAP = 2 * 3600               # 自动采失败重试间隔
MAX_AUTO_FAILS = 2                 # 初始1次+重试1次，再败走数据缺失版
SNAPSHOT_FIELDS = (
    "name", "douyin_id", "followers", "total_products", "total_shops",
    "total_gmv_text", "rating_score", "fulfillment_score", "level",
    "category", "talent_type", "city", "ocr_raw_fields",
)


# ---------------- 采集完成钩子（worker.py 调用） ----------------

def _snapshot_from_talent(t: dict) -> dict:
    snap = {k: t.get(k) for k in SNAPSHOT_FIELDS if t.get(k) is not None}
    return snap


def post_collect(tenant_key: str, douyin_id: str, task: dict) -> None:
    """douyin 任务 done 后：存周报快照；自动采集记系统账（不扣达人余额）。"""
    try:
        t = executors._read_talent("", douyin_id)
        if t:
            db.save_snapshot(tenant_key, douyin_id, report_week_no(),
                             _snapshot_from_talent(t),
                             collected_at=time.strftime("%m-%d %H:%M"))
    except Exception:
        log.exception("weekly snapshot save failed: %s/%s", tenant_key, douyin_id)
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        payload = {}
    if payload.get("auto"):
        db.log_system_cost(tenant_key, "weekly_auto_collect",
                           100, task.get("id", ""))


# ---------------- 调度器 ----------------

def _md5_slot(s: str, mod: int) -> int:
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % mod


def _window(dt: datetime.datetime):
    """当前所处的采集窗 (start, end)：最近一个周日20:00 → +12h。"""
    days_since_sun = (dt.weekday() + 1) % 7
    sun = datetime.datetime.combine(
        (dt - datetime.timedelta(days=days_since_sun)).date(),
        datetime.time(20, 0))
    if dt < sun:
        sun -= datetime.timedelta(days=7)
    return sun, sun + datetime.timedelta(hours=COLLECT_WINDOW_HOURS)


def report_monday(dt: datetime.datetime = None) -> datetime.date:
    """采集窗（周日20:00→周一08:00）的数据属于周一开始的那个 ISO 周。
    解决快照周编号与周报周编号错位的坑：周日晚上采的数据是「下周一的周报」的。"""
    dt = dt or datetime.datetime.now()
    d = dt.date()
    days_since_sun = (dt.weekday() + 1) % 7
    sun = d - datetime.timedelta(days=days_since_sun)
    if dt >= datetime.datetime.combine(sun, datetime.time(20, 0)):
        return sun + datetime.timedelta(days=1)
    return d - datetime.timedelta(days=dt.weekday())


def report_week_no(dt: datetime.datetime = None) -> int:
    iso = report_monday(dt).isocalendar()
    return iso.year * 100 + iso.week


def _week_report_enqueued(conn_week_ts: int, tenant_key: str,
                          douyin_id: str) -> bool:
    with db.db_ctx() as conn:
        r = conn.execute(
            "SELECT 1 FROM bot_collect_tasks WHERE tenant_key=? AND target=? "
            "AND type='weekly_report' AND created_at>=? LIMIT 1",
            (tenant_key, douyin_id, conn_week_ts)).fetchone()
        return r is not None


def _enqueue_report(tenant_key: str, douyin_id: str, missing: int) -> None:
    chat_id = db.get_latest_chat_id(tenant_key)
    if not chat_id:
        log.warning("weekly report skipped, no chat_id: tenant=%s", tenant_key)
        return
    db.enqueue_task(tenant_key, "weekly_report", douyin_id, 0,
                    chat_id=chat_id,
                    payload=json.dumps({"missing": missing}))


class WeeklyScheduler(threading.Thread):
    """每分钟扫一遍：采集窗做主动采集编排，推送窗做周报入队。"""

    def __init__(self):
        super().__init__(daemon=True, name="weekly-scheduler")
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def run(self):
        log.info("weekly scheduler started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("weekly scheduler tick failed")
            self._stop.wait(60)

    def _tick(self):
        now = datetime.datetime.now()
        w_start, w_end = _window(now)
        week_ts = int(w_start.timestamp())
        tenants = db.get_active_tenants()
        for t in tenants:
            tk = t["tenant_key"]
            if not int(t.get("weekly_sub") or 1):
                continue
            dyid = (t.get("bind_douyin_id") or "").strip()
            if not dyid:
                continue
            in_collect_phase = w_start <= now < w_end
            if in_collect_phase and not self._collected(tk, dyid, week_ts):
                self._maybe_enqueue_collect(tk, dyid, week_ts, now, w_start, w_end)
            self._maybe_enqueue_report(t, dyid, week_ts, now, w_start)

    # ---- 采集编排 ----
    def _collected(self, tk, dyid, week_ts) -> bool:
        with db.db_ctx() as conn:
            r = conn.execute(
                "SELECT 1 FROM bot_collect_tasks WHERE tenant_key=? AND target=? "
                "AND type='douyin' AND status IN ('pending','collecting','done') "
                "AND created_at>=? LIMIT 1",
                (tk, dyid, week_ts)).fetchone()
            return r is not None

    def _maybe_enqueue_collect(self, tk, dyid, week_ts, now, w_start, w_end):
        fails = db.count_auto_fails_this_week(tk, dyid, week_ts)
        chat_id = db.get_latest_chat_id(tk)
        if not chat_id:
            return
        slot = datetime.timedelta(minutes=_md5_slot(tk, 720))  # 打散在12h窗内
        if fails == 0:
            if now >= w_start + slot:
                retry = 0
            else:
                return
        elif fails == 1:
            # 重试 1 次：距上次失败 2h 后、采集窗关闭前
            if now + datetime.timedelta(hours=1) >= w_end:
                return
            with db.db_ctx() as conn:
                r = conn.execute(
                    "SELECT MAX(created_at) m FROM bot_collect_tasks "
                    "WHERE tenant_key=? AND target=? AND type='douyin' "
                    "AND status='failed' AND created_at>=?",
                    (tk, dyid, week_ts)).fetchone()
            last_fail = int(r["m"]) if r and r["m"] else 0
            if now.timestamp() < last_fail + RETRY_GAP:
                return
            retry = 1
        else:
            return  # 两次都败，等数据缺失版周报
        db.enqueue_task(tk, "douyin", dyid, 0, chat_id=chat_id,
                        payload=json.dumps({"auto": 1, "retry": retry},
                                           ensure_ascii=False))
        log.info("weekly auto collect enqueued: tenant=%s dyid=%s retry=%d",
                 tk, dyid, retry)

    # ---- 推送编排：窗口结束(周一08:00) + 租户slot → 09:37 前后入队周报 ----
    def _maybe_enqueue_report(self, tenant: dict, dyid, week_ts, now, w_start):
        tk = tenant["tenant_key"]
        slot_min = int(tenant.get("weekly_push_slot") or 577)  # 周一00:00起的分钟数
        push_at = w_start + datetime.timedelta(
            hours=COLLECT_WINDOW_HOURS - 8 + slot_min // 60,
            minutes=slot_min % 60)  # 窗口结束是周一08:00，折算到周一 slot 时刻
        if now < push_at:
            return
        if _week_report_enqueued(week_ts, tk, dyid):
            return
        # 有本周快照 → 正常版；没有（采集失败）→ 数据缺失版
        snap = db.get_snapshot(dyid, report_week_no())
        _enqueue_report(tk, dyid, missing=0 if snap else 1)
        log.info("weekly report enqueued: tenant=%s dyid=%s missing=%s",
                 tk, dyid, 0 if snap else 1)


# ---------------- 规则引擎（宁漏勿滥，带活跃上下文） ----------------

_GMV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿|万)?")


def gmv_mid_wan(text: str):
    """'¥100万-250万' → 175.0（万）；解析不出返回 None（不编造）。"""
    if not text:
        return None
    nums = []
    for m in _GMV_RE.finditer(str(text).replace(",", "").replace("¥", "")):
        v = float(m.group(1))
        if m.group(2) == "亿":
            v *= 10000
        nums.append(v)
    if not nums:
        return None
    return sum(nums) / len(nums)


def _num(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        try:
            if v is not None and v != "":
                return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _ocr(d: dict) -> dict:
    raw = d.get("ocr_raw_fields") or ""
    if isinstance(raw, dict):
        return raw
    try:
        o = json.loads(raw)
        return o if isinstance(o, dict) else {}
    except Exception:
        return {}


def detect_anomalies(cur: dict, prev: dict) -> list:
    """返回异常/关注条目列表（空=本周无异常）。cur/prev 为快照 data。"""
    out = []
    co, po = _ocr(cur), _ocr(prev)

    # R2 粉丝异动（粉丝数是时点值，可信）
    cf, pf = _num(cur, "followers"), _num(prev, "followers")
    if cf is not None and pf:
        chg = (cf - pf) / pf
        if chg <= -0.10:
            out.append(("🔴", f"粉丝下降 {abs(chg)*100:.0f}%（{pf:.0f} → {cf:.0f}），"
                             f"建议检查近期内容和舆情"))
        elif chg >= 0.50:
            out.append(("🟢", f"粉丝暴涨 {chg*100:.0f}%（{pf:.0f} → {cf:.0f}），"
                             f"疑似起量，抓紧更新选品接住流量"))

    # R1 GMV 异常下滑：必须「本周开播≥2场」才报；未开播是正常说明不是异常
    cg, pg = gmv_mid_wan(cur.get("total_gmv_text")), gmv_mid_wan(prev.get("total_gmv_text"))
    live = _num(co, "live_count") or 0
    if live == 0:
        if cg is not None and pg is not None and cg != pg:
            out.append(("ℹ️", "本周未开播，GMV 区间变化属正常波动"))
    elif cg is not None and pg and (cg - pg) / pg <= -0.30:
        out.append(("🔴", f"开播 {live:.0f} 场但 GMV 区间下滑 "
                         f"{abs(cg-pg)/pg*100:.0f}%（{cur.get('total_gmv_text')} ← "
                         f"{prev.get('total_gmv_text')}），建议复盘流量承接"))

    # R3 口碑分下滑（门槛：本周有成交）
    cr, pr = _num(cur, "rating_score"), _num(prev, "rating_score")
    has_deal = (_num(co, "video_count_scene") or 0) > 0 or \
               (_num(cur, "total_products") or 0) > 0
    if cr is not None and pr and has_deal and pr - cr > 0.3:
        out.append(("🟡", f"口碑分下滑 {pr-cr:.1f} 分（{pr:.1f} → {cr:.1f}），"
                         f"注意履约和售后响应"))

    # R4 商品结构突变（门槛：本周有成交）
    cp, pp = _num(cur, "total_products"), _num(prev, "total_products")
    if cp is not None and pp and has_deal and abs(cp - pp) / pp > 0.4:
        out.append(("🟡", f"带过商品数变化 {abs(cp-pp)/pp*100:.0f}%"
                         f"（{pp:.0f} → {cp:.0f}），确认是否有换品/清库动作"))
    return out


# ---------------- 周报组装 ----------------

def _fmt_chg(cur_v, prev_v, unit=""):
    """环比格式化：'+12.3%（100 → 112）'；缺数据返回 '-'。"""
    if cur_v is None or prev_v in (None, 0):
        return "-"
    chg = (cur_v - prev_v) / prev_v
    arrow = "↑" if chg >= 0 else "↓"
    return f"{arrow}{abs(chg)*100:.0f}%（{_fmt_num(prev_v)} → {_fmt_num(cur_v)}{unit}）"


def _fmt_num(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{n:.0f}" if n == int(n) else f"{n:.1f}"


def _core_lines(d: dict) -> list:
    o = _ocr(d)
    lines = []
    f = _num(d, "followers")
    if f is not None:
        lines.append(("粉丝", f"{_fmt_num(f)}"))
    lines.append(("带过商品", f"{_fmt_num(_num(d,'total_products') or 0)} 品 / "
                            f"{_fmt_num(_num(d,'total_shops') or 0)} 店"))
    if d.get("total_gmv_text"):
        lines.append(("GMV区间", str(d["total_gmv_text"])))
    live = _num(o, "live_count")
    if live is not None:
        lines.append(("开播场次", f"{_fmt_num(live)} 场"))
    r = _num(d, "rating_score")
    if r is not None:
        lines.append(("口碑分", f"{r:.1f}"))
    if d.get("level"):
        lines.append(("等级", str(d["level"])))
    return lines


def build_weekly_md(tenant_key: str, douyin_id: str,
                    cur_snap, prev_snap, missing: bool) -> str:
    """文字版周报（卡片数据口径一致）。"""
    week_no = report_week_no()
    cur = (cur_snap or {}).get("data") or {}
    prev = (prev_snap or {}).get("data") or {}
    name = cur.get("name") or prev.get("name") or douyin_id
    iso_y, iso_w = week_no // 100, week_no % 100
    L = [f"📈 达人经营周报（{iso_y} 第 {iso_w} 周）",
         f"👤 {name}（抖音号 {douyin_id}）",
         "数据口径：巨量百应近60天滚动窗口"]

    if missing:
        last = (prev_snap or {}).get("collected_at", "")
        L += ["", "⚠️ 本周自动采集未成功，先附上最近一期数据。",
              f"（最近采集：{last or '暂无'}）"]
        if prev:
            L.append("")
            L += [f"· {k}：{v}" for k, v in _core_lines(prev)]
        return "\n".join(L)

    L[2] += f"，本次采集于 {cur_snap.get('collected_at', '')}"

    if not prev:
        # 首周：基线建立版，环比与异常不生效
        L += ["", "🌱 这是你的第一份周报，先建立数据基线，下周开始出环比变化。"]
        L.append("")
        L += [f"· {k}：{v}" for k, v in _core_lines(cur)]
        return "\n".join(L)

    L += ["", "【本周核心数据】"]
    L += [f"· {k}：{v}" for k, v in _core_lines(cur)]
    L += ["", "【环比变化】（滚动窗口对比）"]
    cf, pf = _num(cur, "followers"), _num(prev, "followers")
    L.append(f"· 粉丝 {_fmt_chg(cf, pf)}")
    cg, pg = gmv_mid_wan(cur.get("total_gmv_text")), gmv_mid_wan(prev.get("total_gmv_text"))
    if cg is not None and pg is not None and cur.get("total_gmv_text") != prev.get("total_gmv_text"):
        L.append(f"· GMV区间 {cur.get('total_gmv_text')}（上周 {prev.get('total_gmv_text')}）")
    cp, pp = _num(cur, "total_products"), _num(prev, "total_products")
    L.append(f"· 带过商品 {_fmt_chg(cp, pp, ' 品')}")

    anomalies = detect_anomalies(cur, prev)
    L += ["", "【异常关注】"]
    if anomalies:
        L += [f"{icon} {text}" for icon, text in anomalies]
    else:
        L.append("· 本周无异常 🎉")

    tips = _ai_tips(tenant_key, name, cur, prev, anomalies)
    if tips:
        L += ["", "【下周建议】", tips]
    return "\n".join(L)


def _ai_tips(tenant_key: str, name: str, cur: dict, prev: dict,
             anomalies: list) -> str:
    try:
        diff = []
        cf, pf = _num(cur, "followers"), _num(prev, "followers")
        if cf is not None and pf:
            diff.append(f"粉丝 {pf:.0f}→{cf:.0f}")
        if cur.get("total_gmv_text") != prev.get("total_gmv_text"):
            diff.append(f"GMV区间 {prev.get('total_gmv_text')}→{cur.get('total_gmv_text')}")
        co = _ocr(cur)
        diff.append(f"开播{ _num(co,'live_count') or 0:.0f}场")
        alert = "；".join(t for _, t in anomalies) if anomalies else "无异常"
        prompt = (f"你是抖音带货达人的经营顾问。达人「{name}」本周数据变化："
                  f"{'；'.join(diff)}。异常关注：{alert}。"
                  f"请给 3 条以内具体可执行的下周经营建议（每条一行，带序号，"
                  f"50字内，口吻直接，不要寒暄和重复数据）。")
        out = executors._qa_llm(prompt)
        db.log_system_cost(tenant_key, "weekly_report", 1)
        return out.strip()[:600]
    except Exception as e:
        log.warning("weekly ai tips failed: %s", e)
        return "（AI 建议暂时不可用，下周恢复）"


# ---------------- 卡片渲染（复用 report_card 视觉体系） ----------------

def render_weekly_card(name: str, douyin_id: str, md: str,
                       out_path: str) -> str:
    from .report_card import (_font, _round, _vgrad, _text_w, W, M,
                              BLUE, DARK, GRAY, LIGHT, GREEN, ORANGE, RED)
    from PIL import Image, ImageDraw

    im = Image.new("RGB", (W, 3000), "white")
    dr = ImageDraw.Draw(im)
    y = 0

    hdr_h = 240
    _vgrad(dr, (0, 0, W, hdr_h), (24, 160, 88), (14, 90, 50))
    dr.text((M, 40), "达人经营周报", font=_font(26), fill=(205, 240, 220))
    dr.text((M, 82), name, font=_font(50, True), fill="white")
    dr.text((M, 158), f"抖音号 {douyin_id}", font=_font(24), fill=(210, 235, 220))
    y = hdr_h + 10

    # md 按节渲染：简单稳健——标题行加粗放大，条目行常规，emoji 颜色前缀着色
    for line in md.splitlines():
        if not line.strip():
            y += 12
            continue
        if y > 2850:
            break
        s = line.rstrip()
        if s.startswith("【") and s.endswith("】"):
            y += 26
            dr.text((M, y), s, font=_font(32, True), fill=DARK)
            y += 48
            continue
        if s.startswith(("📈", "👤", "数据口径")):
            continue  # 头部已展示
        color = DARK
        body = s
        for prefix, c in (("🔴", RED), ("🟡", ORANGE), ("🟢", GREEN),
                          ("⚠️", ORANGE), ("ℹ️", BLUE), ("🌱", GREEN),
                          ("🎉", GREEN)):
            if s.startswith(prefix):
                color = c
                body = s[len(prefix):].strip()
                break
        # 长行硬截断（卡片内不折行，文字版有完整内容）
        max_w = W - 2 * M
        while _text_w(dr, body, _font(26)) > max_w and len(body) > 4:
            body = body[:-2]
        body = body + ("…" if not s.endswith(body) else "")
        dr.text((M, y), body if body else s, font=_font(26), fill=color)
        y += 42
    y += 20
    dr.text((M, y), "数据来自我们系统 · 每周一自动推送", font=_font(20), fill=GRAY)
    im.crop((0, 0, W, min(y + 60, 3000))).save(out_path)
    return out_path


# ---------------- worker 执行器 + 推送 ----------------

def execute_weekly_report(task: dict) -> str:
    """weekly_report 任务：组装文字版 + 渲染卡片，返回 JSON 信封给 worker 推送。"""
    tk, dyid = task["tenant_key"], task["target"].strip()
    week_no = report_week_no()
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        payload = {}
    cur = db.get_snapshot(dyid, week_no)
    missing = bool(payload.get("missing")) or not cur
    if missing:
        # 数据缺失版：附上最近一份历史快照（不限周数）
        prev = db.get_prev_snapshot(dyid, 99999999)
    else:
        prev = db.get_prev_snapshot(dyid, week_no)
    md = build_weekly_md(tk, dyid, cur, prev, missing)
    name = ((cur or prev or {}).get("data") or {}).get("name") or dyid
    img = ""
    try:
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"daren_weekly_{week_no}_{dyid}.png"
        render_weekly_card(name, dyid, md, str(tmp))
        img = str(tmp)
    except Exception:
        log.exception("weekly card render failed, text only: %s", dyid)
    return json.dumps({"md": md, "img": img, "name": name},
                      ensure_ascii=False)


def notify_weekly(sender, chat_id: str, result_summary: str) -> None:
    """OpenAPI 推送（im/v1/messages + chat_id，不走 ws）：长图 + 摘要 + 文字版。"""
    if sender is None:
        log.info("[notify-no-sender] weekly_report")
        return
    try:
        env = json.loads(result_summary)
    except Exception:
        env = {"md": result_summary, "img": "", "name": ""}
    md, img, name = env.get("md", ""), env.get("img", ""), env.get("name", "")
    try:
        if img and Path(img).is_file():
            sender.send_image_path(chat_id, img)
        first = (md.splitlines() or [""])[0]
        sender.send_text(chat_id, f"{first}　👤 {name} 的周报已生成")
        if md:
            sender.send_text(chat_id, md)
    except Exception:
        log.exception("weekly notify failed chat=%s", chat_id)
