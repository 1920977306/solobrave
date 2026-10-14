"""任务执行器 —— 对接 SoloBrave 现有管道，不重复造轮子。

抖音号采集: daren-bot → buyin_worker 控制口(:8095, Playwright 常驻浏览器)
  → 巨量百应团长端抓取 → GLM-4.7 结构化 → 写 SoloBrave 达人库
  → 本模块从同机 SQLite 读回字段 → 拼报告卡片文本。

商品/vision: 暂未接入，保持 NotImplementedError 走诚实失败返还路径（spec）。
"""
import json
import logging
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("daren-bot.executors")

BUYIN_CONTROL = "http://127.0.0.1:8095"          # buyin_worker 控制口
SOLOBRAVE_DB = Path("/Users/qichen/solobrave-prod/data/solobrave.db")
JOB_POLL_INTERVAL = 5
JOB_WAIT_TIMEOUT = 15 * 60                        # 与 buyin_worker JOB_TIMEOUT 对齐


def _http_json(method: str, url: str, payload: dict = None, timeout: int = 15) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _fmt_wan(v) -> str:
    """数字转 万/亿 可读格式。"""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n >= 1e8:
        s = f"{n/1e8:.1f}"
        return f"{s.rstrip('0').rstrip('.')}亿"
    if n >= 1e4:
        s = f"{n/1e4:.1f}"
        return f"{s.rstrip('0').rstrip('.')}万"
    return f"{n:.0f}"


# ---------------- 抖音号采集（spec 4.1 主链路） ----------------

def execute_douyin_collect(task: dict) -> str:
    douyin_id = task["target"].strip()
    log.info("submit buyin collect: douyin_id=%s", douyin_id)
    try:
        res = _http_json("POST", f"{BUYIN_CONTROL}/collect",
                         {"name": douyin_id, "douyin_id": douyin_id})
    except (urllib.error.URLError, ConnectionError, OSError) as e:
        raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL}): {e}")
    cid = res.get("collect_id", "")
    if not cid:
        raise RuntimeError(f"采集器拒绝任务: {res}")

    deadline = time.time() + JOB_WAIT_TIMEOUT
    not_found_streak = 0
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        try:
            job = _http_json("GET", f"{BUYIN_CONTROL}/jobs/{cid}")
        except Exception as e:
            log.warning("job poll failed: %s", e)
            continue
        if job.get("error") == "not found":
            # 采集器重启会导致内存任务表丢失, 快速失败让用户重发, 不傻等超时
            not_found_streak += 1
            if not_found_streak >= 3:
                raise RuntimeError("采集器重启导致任务丢失，积分已返还，请重新发送抖音号")
            continue
        not_found_streak = 0
        status = job.get("status", "")
        if status == "done":
            return _build_report(job.get("talent_id", ""), douyin_id)
        if status in ("failed", "need_login"):
            raise RuntimeError(job.get("error") or f"采集失败({status})")
    raise RuntimeError("采集超时(15分钟)，积分已返还，可稍后再试")


def _fmt_pct(v) -> str:
    """百分比格式化。库里有的字段存 0-1 小数、有的存 0-100 百分数，两者都兼容。"""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n <= 1.5:            # 0-1 小数
        n *= 100
    return f"{n:.0f}%"


def _fmt_crowd(d: dict) -> str:
    """粉丝消费层级分布取 top2。"""
    if not d:
        return ""
    items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:2]
    total = sum(d.values()) or 1
    return "、".join(f"{k} {v/total*100:.0f}%" for k, v in items)


def _fmt_dist(d: dict, top: int = 3) -> str:
    """分布对象取 topN，值是百分比数字就原样带%，是计数就归一化成占比。"""
    if not isinstance(d, dict) or not d:
        return ""
    items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:top]
    total = sum(v for v in d.values() if isinstance(v, (int, float))) or 0
    out = []
    for k, v in items:
        if not isinstance(v, (int, float)):
            continue
        if total > 100:            # 计数 → 占比
            out.append(f"{k} {v/total*100:.0f}%")
        else:                       # 已是百分数
            out.append(f"{k} {v:.0f}%")
    return "、".join(out)


def _build_report(talent_id: str, douyin_id: str) -> str:
    t = _read_talent(talent_id, douyin_id)
    if not t:
        return (f"采集完成，但达人档案读取失败(talent_id={talent_id})，"
                f"请联系运营核查")

    name = t.get("name") or "-"
    lines = [f"📊 达人报告：{name}（抖音号 {t.get('douyin_id') or douyin_id}）"]

    # 基础信息
    basics = []
    if t.get("followers"):
        basics.append(f"粉丝 {_fmt_wan(t['followers'])}")
    if t.get("category"):
        basics.append(f"主营类目 {t['category']}")
    if t.get("talent_type"):
        basics.append(f"类型 {t['talent_type']}")
    if t.get("city"):
        basics.append(f"城市 {t['city']}")
    if t.get("level"):
        basics.append(f"等级 {t['level']}")
    if basics:
        lines.append("｜".join(basics))

    # 带货能力
    gmv = []
    if t.get("total_gmv"):
        gmv.append(f"总GMV {_fmt_wan(t['total_gmv'])}")
    if t.get("avg_live_gmv"):
        gmv.append(f"场均直播GMV {_fmt_wan(t['avg_live_gmv'])}")
    if t.get("live_gpm"):
        gmv.append(f"直播GPM {float(t['live_gpm']):.0f}")
    if t.get("video_gpm"):
        gmv.append(f"视频GPM {float(t['video_gpm']):.0f}")
    if t.get("average_price"):
        gmv.append(f"客单价 {float(t['average_price']):.0f}元")
    if t.get("total_products"):
        gmv.append(f"带过 {t['total_products']} 品")
    if t.get("total_shops"):
        gmv.append(f"合作 {t['total_shops']} 店")
    if t.get("single_video_settlement"):
        gmv.append(f"单视频结算 {t['single_video_settlement']}")
    if t.get("monthly_settlement"):
        gmv.append(f"月结算 {t['monthly_settlement']}")
    if gmv:
        lines.append("带货：" + "｜".join(gmv))

    # 内容表现
    content = []
    if t.get("video_plays") and str(t.get("video_plays", "0")).isdigit() and int(t["video_plays"]) > 0:
        content.append(f"视频均播 {_fmt_wan(t['video_plays'])}")
    if t.get("video_completion_rate"):
        content.append(f"完播 {_fmt_pct(t['video_completion_rate'])}")
    if t.get("video_interaction_rate"):
        content.append(f"互动 {_fmt_pct(t['video_interaction_rate'])}")
    if t.get("live_ratio"):
        content.append(f"直播占比 {_fmt_pct(t['live_ratio'])}")
    if t.get("video_ratio"):
        content.append(f"视频占比 {_fmt_pct(t['video_ratio'])}")
    if content:
        lines.append("内容：" + "｜".join(content))

    # 粉丝画像
    fans = []
    try:
        g = _fmt_dist(json.loads(t.get("fan_gender") or "{}"))
        if g:
            g = g.replace("female", "女性").replace("male", "男性")
            fans.append(f"性别 {g}")
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        a = _fmt_dist(json.loads(t.get("fan_age") or "{}"))
        if a:
            fans.append(f"年龄 {a}")
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        r = _fmt_dist(json.loads(t.get("fan_region") or "{}"), top=2)
        if r:
            fans.append(f"地域 {r}")
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        c = _fmt_dist(json.loads(t.get("fan_crowd") or "{}"), top=2)
        if c:
            fans.append(f"人群 {c}")
    except (json.JSONDecodeError, TypeError):
        pass
    if t.get("fan_price_range"):
        fpr = t["fan_price_range"]
        try:
            fpr = _fmt_dist(json.loads(fpr)) or fpr
        except (json.JSONDecodeError, TypeError):
            pass
        fans.append(f"价格偏好 {fpr}")
    if t.get("rating_score"):
        fans.append(f"带货评分 {float(t['rating_score']):.1f}")
    if t.get("fulfillment_score"):
        fans.append(f"履约 {float(t['fulfillment_score']):.1f}")
    if fans:
        lines.append("粉丝/口碑：" + "｜".join(fans))

    # 类目与品牌结构
    struct = []
    try:
        cats = json.loads(t.get("category_distribution") or "{}")
        if cats:
            struct.append("类目 " + "、".join(f"{k}({v})" for k, v in
                          sorted(cats.items(), key=lambda kv: kv[1], reverse=True)[:4]))
    except (json.JSONDecodeError, TypeError):
        pass
    try:
        brands = json.loads(t.get("cooperating_brands") or "{}")
        if brands:
            struct.append("品牌 " + "、".join(f"{k}({v})" for k, v in
                          sorted(brands.items(), key=lambda kv: kv[1], reverse=True)[:5]))
    except (json.JSONDecodeError, TypeError):
        pass
    if struct:
        lines.append("结构：" + "｜".join(struct))

    # 近期带货
    try:
        tops = json.loads(t.get("top_products") or "[]")
        if tops:
            lines.append("近期带货 top：")
            for i, p in enumerate(tops[:3], 1):
                lines.append(f"  {i}. {str(p)[:60]}")
    except (json.JSONDecodeError, TypeError):
        pass

    # AI 合作评估（buyin_worker 落库的团长视角评估）
    if t.get('risk_rating'):
        lines.append(f"🎯 评估结论：{t['risk_rating']}")
    if t.get('ai_analysis'):
        lines.append(t['ai_analysis'])

    # AI 摘要（buyin_worker 的 LLM 画像，去掉内部来源后缀）
    notes = (t.get("notes") or "").split("｜")[0].strip()
    if notes:
        lines.append(f"画像：{notes}")

    lines.append(f"—— 数据来自巨量百应，采集于 "
                 f"{time.strftime('%m-%d %H:%M')} ——")
    return "\n".join(lines)


def _read_talent(talent_id: str, douyin_id: str):
    if not SOLOBRAVE_DB.is_file():
        return None
    conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        if talent_id:
            r = conn.execute("SELECT * FROM talents WHERE id=?",
                             (talent_id,)).fetchone()
            if r:
                return dict(r)
        if douyin_id:
            r = conn.execute("SELECT * FROM talents WHERE douyin_id=? "
                             "ORDER BY created_at DESC LIMIT 1",
                             (douyin_id,)).fetchone()
            if r:
                return dict(r)
        return None
    finally:
        conn.close()


# ---------------- 未接入（诚实失败路径，spec 4.2/4.3） ----------------

def execute_product_collect(task: dict) -> str:
    raise NotImplementedError("商品采集链路开发中（团长端商品页）")


def execute_image_vision(task: dict) -> str:
    raise NotImplementedError("vision 识别链路开发中")
