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
    name_hint = ""
    try:
        name_hint = json.loads(task.get("payload") or "{}").get("name_hint", "")
    except (json.JSONDecodeError, TypeError, AttributeError):
        pass
    submit_name = name_hint or douyin_id   # 客户带名字发号时，名字用于搜索核验
    log.info("submit buyin collect: douyin_id=%s name_hint=%s", douyin_id, name_hint)
    try:
        res = _http_json("POST", f"{BUYIN_CONTROL}/collect",
                         {"name": submit_name, "douyin_id": douyin_id})
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
    """百分比格式化。库里统一存 0-100 的百分数数字（0.6 表示0.6%），原样展示不再换算。"""
    try:
        n = float(v or 0)
    except (TypeError, ValueError):
        return "-"
    if n <= 0:
        return "-"
    if n < 10:
        return f"{n:g}%"
    return f"{n:.0f}%"


def _fmt_crowd(d: dict) -> str:
    """粉丝消费层级分布取 top2。"""
    if not d:
        return ""
    items = sorted(d.items(), key=lambda kv: kv[1], reverse=True)[:2]
    total = sum(d.values()) or 1
    return "、".join(f"{k} {v/total*100:.0f}%" for k, v in items)


def _to_pct_num(v):
    """百分比值容错：数字原样，'45.26%'/'0.6%' 字符串剥 %，其他返回 None。"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().rstrip('%').strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _fmt_dist(d: dict, top: int = 3) -> str:
    """分布对象取 topN，值是百分比数字就原样带%，是计数就归一化成占比。
    兼容字符串百分比（'45.26%'）与全零占位垃圾（视觉提取失败返回全是 0 的字典）。"""
    if not isinstance(d, dict) or not d:
        return ""
    num_items = [(k, n) for k, v in d.items() if (n := _to_pct_num(v)) is not None]
    if not num_items or all(n == 0 for _, n in num_items):
        return ""                       # 全零 = 视觉占位垃圾，不当真数据展示
    items = sorted(num_items, key=lambda kv: kv[1], reverse=True)[:top]
    total = sum(n for _, n in num_items)
    out = []
    for k, n in items:
        if total > 100:                 # 计数 → 占比
            out.append(f"{k} {n/total*100:.0f}%")
        else:                           # 已是百分数
            out.append(f"{k} {n:.0f}%")
    return "、".join(out)


def _loads_dist(v) -> dict:
    """读分布列：容忍双重 JSON 编码（列里存的是 JSON 字符串的 JSON 字符串）。"""
    if isinstance(v, dict):
        return v
    if not v or not isinstance(v, str):
        return {}
    try:
        parsed = json.loads(v)
    except (json.JSONDecodeError, TypeError):
        return {}
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, str):         # 双重编码，再解一次
        try:
            parsed2 = json.loads(parsed)
            return parsed2 if isinstance(parsed2, dict) else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


_PEER_MIN = 8          # 同类样本少于这个数就退化为全库对标
_bench_cache: dict = {}


def _bench(t: dict, key: str):
    """库内对标：返回 (高于百分比, 样本数, 口径)，数据不足返回 None。
    优先同 category 对比，样本太少则全库对比。"""
    try:
        v = float(t.get(key) or 0)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    cat = (t.get("category") or "").strip()
    tid = t.get("id") or ""
    cache_key = (cat, key)
    if cache_key not in _bench_cache:
        conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True, timeout=10)
        try:
            if cat:
                rows = conn.execute(
                    f"SELECT {key} FROM talents WHERE status='active' "
                    f"AND category=? AND {key} > 0", (cat,)).fetchall()
                scope = "同类"
            else:
                rows, scope = [], "全库"
            if len(rows) < _PEER_MIN:
                rows = conn.execute(
                    f"SELECT {key} FROM talents WHERE status='active' "
                    f"AND {key} > 0").fetchall()
                scope = "全库"
            _bench_cache[cache_key] = ([float(r[0]) for r in rows], scope)
        finally:
            conn.close()
    vals, scope = _bench_cache[cache_key]
    vals = [x for x in vals if True]  # 保留全体，self 已在分子分母中各计一次，影响可忽略
    if len(vals) < _PEER_MIN:
        return None
    higher = sum(1 for x in vals if x < v) / len(vals) * 100
    return higher, len(vals), scope


def _bench_tag(t: dict, key: str) -> str:
    r = _bench(t, key)
    if not r:
        return ""
    higher, n, scope = r
    if higher >= 95:
        return f"（{scope}顶尖，{n}人中第1档）"
    if higher < 5:
        return f"（{scope}偏低，{n}人中靠后）"
    return f"（高于{higher:.0f}% {scope}{n}人）"


def _build_report(talent_id: str, douyin_id: str) -> str:
    t = _read_talent(talent_id, douyin_id)
    if not t:
        return (f"采集完成，但达人档案读取失败(talent_id={talent_id})，"
                f"请联系运营核查")

    # 无专属列的丰富字段（评价明细/视频趋势/标签等）从视觉原始提取里取
    # 兼容双重 JSON 编码（服务端写库口径差异，读端兜底）
    ocr = {}
    raw = t.get("ocr_raw_fields") or ""
    for candidate in (raw, ):
        try:
            parsed = json.loads(candidate) if candidate else {}
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(parsed, dict):
            ocr = parsed
            break
        if isinstance(parsed, str):      # 双重编码，再解一次
            try:
                parsed2 = json.loads(parsed)
                if isinstance(parsed2, dict):
                    ocr = parsed2
                    break
            except (json.JSONDecodeError, TypeError):
                continue

    def _is_empty_val(v) -> bool:
        """空值判定：None/空串/空容器/全零字典都算空（全零是视觉占位垃圾）。"""
        if v in (None, "", [], {}):
            return True
        if isinstance(v, dict):
            nums = [_to_pct_num(x) for x in v.values()]
            nums = [n for n in nums if n is not None]
            return bool(nums) and all(n == 0 for n in nums)
        if isinstance(v, (int, float)) and v == 0:
            return True
        return False

    def pick(key, default=""):
        v = t.get(key)
        if _is_empty_val(v):
            v = ocr.get(key)
        return default if _is_empty_val(v) else v

    name = t.get("name") or "-"
    lines = [f"📊 达人报告：{name}（抖音号 {t.get('douyin_id') or douyin_id}）"]

    # 基础信息
    basics = []
    if t.get("followers"):
        basics.append(f"粉丝 {_fmt_wan(t['followers'])}{_bench_tag(t, 'followers')}")
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
        gmv.append(f"总GMV {_fmt_wan(t['total_gmv'])}{_bench_tag(t, 'total_gmv')}")
    if t.get("avg_live_gmv"):
        gmv.append(f"场均直播GMV {_fmt_wan(t['avg_live_gmv'])}")
    if t.get("live_gpm"):
        gmv.append(f"直播GPM {float(t['live_gpm']):.0f}")
    if t.get("video_gpm"):
        gmv.append(f"视频GPM {float(t['video_gpm']):.0f}{_bench_tag(t, 'video_gpm')}")
    if t.get("average_price"):
        gmv.append(f"客单价 {float(t['average_price']):.0f}元{_bench_tag(t, 'average_price')}")
    if t.get("total_products"):
        gmv.append(f"带过 {t['total_products']} 品")
    if t.get("total_shops"):
        gmv.append(f"合作 {t['total_shops']} 店")
    if t.get("single_video_settlement"):
        gmv.append(f"单视频结算 {t['single_video_settlement']}")
    ms = pick("monthly_settlement")
    if ms:
        gmv.append(f"月结算 {ms}")
    hd = pick("total_history_days")
    if hd:
        gmv.append(f"历史带货 {hd} 天")
    gt = pick("total_gmv_text")
    if gt and not t.get("total_gmv"):
        gmv.append(f"累计GMV {gt}")
    alg = pick("avg_live_gmv_text")
    if alg:
        gmv.append(f"场均直播GMV {alg}")
    dp = pick("data_period")
    if dp:
        import re as _re
        _years = [int(y) for y in _re.findall(r'(20\d\d)', str(dp))]
        # 视觉把年份识别错的概率高（如 2026→2020），年份离谱就不展示
        if _years and all(2023 <= y <= 2035 for y in _years):
            gmv.append(f"数据周期 {dp}")
    if gmv:
        lines.append("带货：" + "｜".join(gmv))

    # 内容表现
    content = []
    vc = pick("video_count")
    if vc:
        content.append(f"带货视频 {vc} 条")
    vap = pick("video_avg_plays")
    if vap:
        content.append(f"视频均播 {vap}")
    if t.get("video_plays") and str(t.get("video_plays", "0")).isdigit() and int(t["video_plays"]) > 0:
        content.append(f"视频均播 {_fmt_wan(t['video_plays'])}")
    vpr = pick("video_plays_range")
    if vpr:
        content.append(f"30天播放 {vpr}")
    if t.get("video_completion_rate"):
        content.append(f"完播 {_fmt_pct(t['video_completion_rate'])}")
    if t.get("video_interaction_rate"):
        content.append(f"互动 {_fmt_pct(t['video_interaction_rate'])}")
    vl = pick("video_likes")
    if vl:
        content.append(f"赞{vl}/评{pick('video_comments','0')}/转{pick('video_shares','0')}")
    if t.get("live_ratio"):
        content.append(f"直播占比 {_fmt_pct(t['live_ratio'])}")
    vr = pick("video_ratio")
    if vr:
        content.append(f"视频占比 {_fmt_pct(vr)}")
    _tags = pick("tags")
    if _tags:
        if isinstance(_tags, str):
            _s = _tags.strip()
            for _ in range(3):          # 列里可能是 JSON 字符串的 JSON 字符串（多重编码）
                if not isinstance(_s, str):
                    break
                try:
                    _s2 = json.loads(_s)
                except (json.JSONDecodeError, TypeError):
                    break
                if _s2 == _s:
                    break
                _s = _s2
            _tags = _s
        if isinstance(_tags, list):
            _tags = "、".join(str(x) for x in _tags if str(x).strip())
        if isinstance(_tags, str) and _tags.strip() and _tags.strip() not in ("[]",):
            content.append(f"标签 {_tags.strip()}")
    _bio = pick("bio")
    if _bio and str(_bio).strip():
        content.append(f"简介 {str(_bio).strip()[:40]}")
    if content:
        lines.append("内容：" + "｜".join(content))

    # 粉丝画像
    fans = []
    g = _fmt_dist(_loads_dist(t.get("fan_gender")))
    if g:
        g = g.replace("female", "女性").replace("male", "男性")
        fans.append(f"性别 {g}")
    a = _fmt_dist(_loads_dist(t.get("fan_age")))
    if a:
        fans.append(f"年龄 {a}")
    r = _fmt_dist(_loads_dist(t.get("fan_region")), top=2)
    if r:
        fans.append(f"地域 {r}")
    c = _fmt_dist(_loads_dist(t.get("fan_crowd")), top=2)
    if c:
        fans.append(f"人群 {c}")
    if t.get("fan_price_range"):
        fpr = t["fan_price_range"]
        try:
            fpr = _fmt_dist(json.loads(fpr)) or fpr
        except (json.JSONDecodeError, TypeError):
            pass
        fans.append(f"价格偏好 {fpr}")
    cp = pick("content_preference")
    if cp and not ('偏好' in str(cp) and not any(ch.isdigit() for ch in str(cp))):
        fans.append(f"内容偏好 {cp}")
    fg = pick("fan_growth")
    fgr = pick("fan_growth_rate")
    if fg or fgr:
        fans.append(f"近2月涨粉 {fg if fg else '?'}{'（' + fgr + '）' if fgr else ''}")
    if t.get("rating_score"):
        fans.append(f"带货评分 {float(t['rating_score']):.1f}")
    if t.get("fulfillment_score"):
        fans.append(f"履约 {float(t['fulfillment_score']):.1f}")
    if fans:
        lines.append("粉丝/口碑：" + "｜".join(fans))

    # 历史带货评价（总评+三维+好评率+标签+联系方式）
    rc = pick("review_count")
    if rc:
        rev = [f"评价 {rc} 条"]
        dims = []
        for label, key in (("带货效果", "rating_effect"), ("合作履约", "rating_fulfill"),
                           ("沟通态度", "rating_communication")):
            dv = pick(key)
            if dv:
                dims.append(f"{label} {dv}")
        if dims:
            rev.append("｜".join(dims))
        rg, rm, rb = pick("review_good"), pick("review_mid"), pick("review_bad")
        if rg:
            rev.append(f"好评 {rg}" + (f"/中评 {rm}/差评 {rb}" if rm or rb else ""))
        lines.append("口碑：" + "｜".join(rev))
        try:
            rtags = pick("review_tags")
            if isinstance(rtags, dict) and rtags:
                lines.append("评价标签：" + "、".join(
                    f"{k}({v})" for k, v in
                    sorted(rtags.items(), key=lambda kv: kv[1], reverse=True)[:8]))
        except (json.JSONDecodeError, TypeError):
            pass
        rcontact = pick("review_contact")
        if rcontact:
            lines.append(f"📞 合作联系方式（来自评价区）：{rcontact}")

    # 类目与品牌结构
    struct = []
    cats = _loads_dist(pick("category_distribution"))
    cats_items = [(k, _to_pct_num(v)) for k, v in cats.items()]
    cats_items = [(k, n) for k, n in cats_items if n is not None]
    if cats_items:
        # 值总计 >100 视为计数（场次），归一成占比；否则已是百分比
        total = sum(n for _, n in cats_items)
        if total > 100:
            struct.append("类目 " + "、".join(f"{k} {n/total*100:.0f}%"
                          for k, n in sorted(cats_items, key=lambda kv: kv[1], reverse=True)[:4]))
        else:
            struct.append("类目 " + "、".join(f"{k} {n:.0f}%"
                          for k, n in sorted(cats_items, key=lambda kv: kv[1], reverse=True)[:4]))
    brands = _loads_dist(pick("cooperating_brands")
                         or pick("cooperating_brands_distribution"))
    brand_items = [(k, _to_pct_num(v)) for k, v in brands.items()]
    brand_items = [(k, n) for k, n in brand_items if n is not None and k != '其他']
    if brand_items:
        struct.append("品牌 " + "、".join(f"{k} {n:.0f}%"
                      for k, n in sorted(brand_items, key=lambda kv: kv[1], reverse=True)[:5]))
    if struct:
        lines.append("结构：" + "｜".join(struct))

    # 近期带货
    try:
        tops = json.loads(t.get("top_products") or "[]")
        if isinstance(tops, str):          # 双重 JSON 编码兜底
            tops = json.loads(tops)
        if isinstance(tops, list) and tops:
            shown = 0
            for p in tops:
                s = str(p).strip().replace('\n', ' ')
                # 单字符碎片是解析残渣；tab 栏文本是新版布局点击未弹窗的残渣，都不展示
                if len(s) < 4 or '概览' in s:
                    continue
                if shown == 0:
                    lines.append("近期带货 top：")
                shown += 1
                lines.append(f"  {shown}. {s[:60]}")
                if shown >= 3:
                    break
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
