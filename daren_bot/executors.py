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
        return f"{n/1e8:.1f}亿"
    if n >= 1e4:
        return f"{n/1e4:.1f}万"
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
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        try:
            job = _http_json("GET", f"{BUYIN_CONTROL}/jobs/{cid}")
        except Exception as e:
            log.warning("job poll failed: %s", e)
            continue
        status = job.get("status", "")
        if status == "done":
            return _build_report(job.get("talent_id", ""), douyin_id)
        if status in ("failed", "need_login"):
            raise RuntimeError(job.get("error") or f"采集失败({status})")
    raise RuntimeError("采集超时(15分钟)，积分已返还，可稍后再试")


def _build_report(talent_id: str, douyin_id: str) -> str:
    t = _read_talent(talent_id, douyin_id)
    if not t:
        return (f"采集完成，但达人档案读取失败(talent_id={talent_id})，"
                f"请联系运营核查")

    name = t.get("name") or "-"
    lines = [f"📊 达人报告：{name}（抖音号 {t.get('douyin_id') or douyin_id}）"]
    basics = []
    if t.get("followers"):
        basics.append(f"粉丝 {_fmt_wan(t['followers'])}")
    if t.get("talent_type"):
        basics.append(f"类型 {t['talent_type']}")
    if t.get("city"):
        basics.append(f"城市 {t['city']}")
    if t.get("level"):
        basics.append(f"等级 {t['level']}")
    if basics:
        lines.append("｜".join(basics))

    gmv = []
    if t.get("total_gmv"):
        gmv.append(f"总GMV {_fmt_wan(t['total_gmv'])}")
    if t.get("avg_live_gmv"):
        gmv.append(f"场均直播GMV {_fmt_wan(t['avg_live_gmv'])}")
    if t.get("live_gpm"):
        gmv.append(f"直播GPM {float(t['live_gpm']):.0f}")
    if t.get("video_gpm"):
        gmv.append(f"视频GPM {float(t['video_gpm']):.0f}")
    if gmv:
        lines.append("｜".join(gmv))

    ratios = []
    if t.get("live_ratio"):
        ratios.append(f"直播占比 {float(t['live_ratio'])*100:.0f}%")
    if t.get("video_ratio"):
        ratios.append(f"视频占比 {float(t['video_ratio'])*100:.0f}%")
    if t.get("average_price"):
        ratios.append(f"客单价 {_fmt_wan(t['average_price'])}")
    if ratios:
        lines.append("｜".join(ratios))

    scores = []
    if t.get("fulfillment_score"):
        scores.append(f"履约 {float(t['fulfillment_score']):.1f}")
    if t.get("rating_score"):
        scores.append(f"评分 {float(t['rating_score']):.1f}")
    if scores:
        lines.append("｜".join(scores))

    try:
        tags = json.loads(t.get("tags") or "[]")
        if tags:
            lines.append("标签：" + "、".join(str(x) for x in tags[:6]))
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        tops = json.loads(t.get("top_products") or "[]")
        if tops:
            lines.append("近期带货 top：")
            for i, p in enumerate(tops[:3], 1):
                lines.append(f"  {i}. {str(p)[:60]}")
    except (json.JSONDecodeError, TypeError):
        pass

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
