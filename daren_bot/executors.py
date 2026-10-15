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
        if isinstance(v, str) and v.strip() in ("[]", "{}"):
            return True             # 列里存的 JSON 文本空容器
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

    # 内容总览（概览 tab 核心数据）
    overview = []
    ct = pick("content_total")
    if ct:
        overview.append(f"发布 {ct} 条")
    wt = pick("watch_total")
    if wt:
        overview.append(f"总观看 {wt}万" if isinstance(wt, (int, float)) and wt < 10000 else f"总观看 {wt}")
    va = pick("video_count_all")
    if va:
        overview.append(f"视频 {va}")
    lc = pick("live_count")
    if lc not in (None, ""):
        overview.append(f"直播 {lc} 场")
    tw = pick("tuwen_count")
    if tw:
        overview.append(f"图文 {tw}")
    if overview:
        lines.append("内容总览：" + "｜".join(str(x) for x in overview))

    # 带货能力
    gmv = []
    if t.get("total_gmv"):
        gmv.append(f"总GMV {_fmt_wan(t['total_gmv'])}{_bench_tag(t, 'total_gmv')}")
    if t.get("avg_live_gmv"):
        gmv.append(f"场均直播GMV {_fmt_wan(t['avg_live_gmv'])}")
    if t.get("live_gpm"):
        gmv.append(f"直播GPM {float(t['live_gpm']):.0f}")
    vg = pick("video_gpm_text")
    if vg:
        gmv.append(f"视频GPM {vg}")
    elif t.get("video_gpm"):
        gmv.append(f"视频GPM {float(t['video_gpm']):.0f}{_bench_tag(t, 'video_gpm')}")
    if t.get("average_price"):
        gmv.append(f"平均件单价 {float(t['average_price']):.0f}元{_bench_tag(t, 'average_price')}")
    vaprice = pick("video_avg_price")
    if vaprice:
        gmv.append(f"视频件单价 {vaprice}")
    if t.get("total_products"):
        gmv.append(f"带过 {t['total_products']} 品")
    if t.get("total_shops"):
        gmv.append(f"合作 {t['total_shops']} 店")
    vsr = pick("video_settlement_ratio")
    if vsr:
        gmv.append(f"视频结算占比 {vsr}")
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
        lines.append("带货：" + "｜".join(str(x) for x in gmv))

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
    fcl = _fmt_dist(_loads_dist(pick("fan_consume_level")))
    if fcl:
        fans.append(f"消费水平 {fcl}")
    fpp = pick("fan_purchase_pref")
    if fpp:
        fans.append(f"购买偏好 {fpp}")
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
        lines.append("粉丝/口碑：" + "｜".join(str(x) for x in fans))

    # 历史带货评价（总评+三维+好评率+明细+联系方式）
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
        if rg not in (None, ""):
            rev.append(f"好评 {rg}/中评 {rm or 0}/差评 {rb or 0}")
        lines.append("口碑：" + "｜".join(str(x) for x in rev))
        try:
            rtags = pick("review_tags")
            if isinstance(rtags, dict) and rtags:
                lines.append("评价标签：" + "、".join(
                    f"{k}({v})" for k, v in
                    sorted(rtags.items(), key=lambda kv: kv[1], reverse=True)[:8]))
        except (json.JSONDecodeError, TypeError):
            pass
        # 评价明细（商家/星级/内容/电话/日期）
        reviews = pick("reviews")
        if isinstance(reviews, str):
            try:
                reviews = json.loads(reviews)
            except (json.JSONDecodeError, TypeError):
                reviews = None
        if isinstance(reviews, list) and reviews:
            lines.append("评价明细：")
            for rv in reviews[:3]:
                if not isinstance(rv, dict):
                    continue
                shop = rv.get("商家") or rv.get("商家名") or "?"
                star = rv.get("星级") or ""
                text_ = rv.get("内容") or rv.get("评价原文") or rv.get("评价") or ""
                date_ = rv.get("日期") or ""
                phone = rv.get("联系电话") or rv.get("电话") or ""
                seg = f"  · {shop} {star}".rstrip()
                if date_:
                    seg += f"（{date_}）"
                lines.append(seg)
                if text_:
                    lines.append(f"    {str(text_).strip()[:60]}")
                if phone:
                    lines.append(f"    📞 {phone}")
        rcontact = pick("review_contact")
        if rcontact and not (isinstance(reviews, list) and any(
                str(rcontact) in json.dumps(r, ensure_ascii=False) for r in reviews)):
            lines.append(f"📞 合作联系方式（来自评价区）：{rcontact}")

    # 类目与品牌结构
    struct = []
    cats_raw = pick("category_distribution")
    if isinstance(cats_raw, list):      # 深度提取可能是 [{category,percentage}] 数组
        cats = {str(it.get('category')): it.get('percentage') for it in cats_raw
                if isinstance(it, dict) and it.get('category') is not None}
    else:
        cats = _loads_dist(cats_raw)
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
    brands_raw = pick("cooperating_brands") or pick("cooperating_brands_distribution")
    if isinstance(brands_raw, list):
        brands = {str(it.get('brand')): it.get('percentage') for it in brands_raw
                  if isinstance(it, dict) and it.get('brand') is not None}
    else:
        brands = _loads_dist(brands_raw)
    brand_items = [(k, _to_pct_num(v)) for k, v in brands.items()]
    brand_items = [(k, n) for k, n in brand_items if n is not None and k != '其他']
    if brand_items:
        struct.append("品牌 " + "、".join(f"{k} {n:.0f}%"
                      for k, n in sorted(brand_items, key=lambda kv: kv[1], reverse=True)[:5]))
    if struct:
        lines.append("结构：" + "｜".join(struct))

    # 近期带货视频明细（概览 tab 历史内容卡片）
    hvs = pick("history_videos")
    if isinstance(hvs, str):
        try:
            hvs = json.loads(hvs)
        except (json.JSONDecodeError, TypeError):
            hvs = None
    if isinstance(hvs, list) and hvs:
        shown = 0
        for v_ in hvs:
            if not isinstance(v_, dict):
                continue
            title = str(v_.get('title') or '').strip()
            if len(title) < 2:
                continue
            parts = []
            d_ = v_.get('publish_date') or v_.get('发布日期')
            if d_:
                parts.append(str(d_)[5:])          # 只留月-日
            views = v_.get('views') or v_.get('观看人数')
            if views not in (None, ''):
                parts.append(f"观看{views}")
            likes = v_.get('likes') or v_.get('点赞数')
            if likes not in (None, ''):
                parts.append(f"赞{likes}")
            settle = v_.get('settlement') or v_.get('结算额')
            if settle:
                parts.append(f"结算{settle}")
            price = v_.get('到手价') or v_.get('price')
            if price:
                parts.append(f"到手{price}")
            if shown == 0:
                lines.append("近期带货视频：")
            shown += 1
            lines.append(f"  {shown}. {title[:18]}" + ("｜" + "｜".join(parts) if parts else ""))
            if shown >= 4:
                break

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

    # AI 账号诊断（buyin_worker 落库的达人视角评估，读者是达人本人）
    if t.get('risk_rating'):
        lines.append(f"🎯 账号评估：{t['risk_rating']}")
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


def notify_report(sender, chat_id: str, douyin_id: str, report_text: str) -> None:
    """报告推送：优先渲染可视化长图卡片发图 + 一句话摘要；渲染失败回退纯文字。"""
    if sender is None:
        log.info("[notify-no-sender] douyin_id=%s", douyin_id)
        return
    try:
        from .report_card import render_card
        t = _read_talent("", douyin_id)
        if t:
            import tempfile
            tmp = Path(tempfile.gettempdir()) / f"daren_card_{t['id']}.png"
            render_card(t["id"], douyin_id, str(tmp))
            sender.send_image_path(chat_id, str(tmp))
            name = t.get("name") or douyin_id
            verdict = t.get("risk_rating") or ""
            sender.send_text(
                chat_id,
                f"📊 {name} 的账号数据报告如上　🎯 账号评估：{verdict or '见报告'}")
            return
    except Exception:
        log.exception("render card failed, fallback to text: douyin_id=%s", douyin_id)
    sender.send_text(chat_id, report_text)


def _talent_name(tk: str) -> str:
    """租户绑定账号的昵称，取不到返回空串。"""
    try:
        from . import db
        tenant = db.get_tenant(tk)
        dyid = (tenant or {}).get("bind_douyin_id") or ""
        if not dyid:
            return ""
        t = _read_talent("", dyid)
        return (t or {}).get("name") or ""
    except Exception:
        return ""


def notify_qa_card(sender, chat_id: str, question: str, answer: str,
                   tenant_key: str) -> None:
    """问答推送：优先渲染问答卡片发图；渲染失败回退纯文字答案。"""
    if sender is None:
        log.info("[notify-no-sender] qa tenant=%s", tenant_key)
        return
    try:
        from .report_card import render_qa_card
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"daren_qa_{abs(hash(question)) % 10**8}.png"
        render_qa_card(question, answer, _talent_name(tenant_key), str(tmp))
        sender.send_image_path(chat_id, str(tmp))
        return
    except Exception:
        log.exception("render qa card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, answer)



# ---------------- 业务问答（spec ① 意图分类的问答类，1 积分/次） ----------------

def _zhipu_cfg_daren():
    cfg = json.load(open(Path.home() / '.openclaw' / 'openclaw.json', encoding='utf-8'))
    zp = cfg.get('models', {}).get('providers', {}).get('zhipu') or {}
    base = (zp.get('baseUrl') or 'https://open.bigmodel.cn/api/paas/v4/').rstrip('/')
    key = zp.get('apiKey') or ''
    if not key:
        raise RuntimeError('openclaw.json 缺少 zhipu apiKey')
    return base, key


def _qa_llm(prompt: str, model: str = 'glm-4-flash') -> str:
    """问答用便宜模型（glm-4-flash），OpenAI chat 协议直连。"""
    base, key = _zhipu_cfg_daren()
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': prompt}],
            'temperature': 0.3,
            'max_tokens': 1200,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    return (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''


def _talent_context(tk: str) -> str:
    """该达人绑定的账号画像摘要（问答上下文）：
    基础档案 + 粉丝画像分布 + 类目/品牌结构 + 近期视频表现。"""
    try:
        from . import db
        tenant = db.get_tenant(tk)
        dyid = (tenant or {}).get("bind_douyin_id") or ""
        if not dyid:
            return ""
        t = _read_talent("", dyid)
        if not t:
            return ""
        parts = [f"昵称{t.get('name') or '?'}（抖音号{dyid}）"]
        notes = (t.get('notes') or '').split('｜')[0].strip()
        if notes:
            parts.append(notes)
        raw = t.get('ocr_raw_fields') or ''
        try:
            raw = json.loads(raw)
            if isinstance(raw, str):
                raw = json.loads(raw)
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            for label, key in (('粉丝性别分布', 'fan_gender'), ('年龄分布', 'fan_age'),
                               ('地域分布', 'fan_region'), ('人群分布', 'fan_crowd'),
                               ('消费水平', 'fan_consume_level'),
                               ('带货类目分布', 'category_distribution'),
                               ('品牌集中度', 'cooperating_brands')):
                v = raw.get(key)
                if v:
                    parts.append(f'{label}: {json.dumps(v, ensure_ascii=False)}')
            vp = raw.get('video_details')
            if isinstance(vp, list) and vp:
                tops = []
                for it in vp[:3]:
                    cells = [str(c).strip() for c in (it.get('cells') or []) if str(c).strip()]
                    if cells:
                        first = cells[0].splitlines()[0][:20]
                        stat = ' '.join(c.replace('\n', ' ')[:16] for c in cells[1:3])
                        tops.append(f"{first}（{stat}）")
                if tops:
                    parts.append('近期视频: ' + '；'.join(tops))
        return '。'.join(parts)
    except Exception:
        return ""


def execute_qa(task: dict) -> str:
    """业务问答：结合该达人自己的画像回答；超出带货范围由模型拒答。"""
    from . import db as _db
    q = (task.get("target") or "").strip()
    if not q:
        return "没收到问题内容，重新发一下～"
    ctx = _talent_context(task.get("tenant_key", ""))
    prompt = (
        '你是抖音电商达人的AI选品顾问，用第二人称"你"回答问题。\n'
        '只回答抖音带货、选品、商品判断、账号运营、粉丝画像、直播相关的问题。\n'
        '如果问题与这些无关（闲聊、天气、代码、数学、新闻等），只输出一个字：拒\n'
        '回答要求：优先结合该达人自己的数据给具体可执行的建议（80-250字），'
        '结构为「结论 + 数据依据 + 行动建议」；有数据必须引用数据，'
        '没有的维度不要编造数字，不要喊口号。\n'
        + (f'【该达人的账号数据】{ctx}\n' if ctx else '')
        + f'【达人问】{q}'
    )
    content = ""
    for attempt in (1, 2):
        try:
            content = _qa_llm(prompt)
        except Exception as e:
            log.warning("qa llm attempt %d failed: %s", attempt, e)
            content = ""
        if content.strip():
            break
    ans = content.strip()
    if not ans:
        return "AI 刚才没反应过来，你稍等几秒再问一次～"
    if ans.startswith("拒") and len(ans) <= 3:
        return ("我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，"
                "其他问题帮不上忙哦～")
    return ans[:900]


# ---------------- 视频链接拆解（teardown_url：下载→转写→画面→报告） ----------------

TEARDOWN_URL_API = "http://127.0.0.1:8095/teardown_url"
VIDEO_POLL_TIMEOUT = 15 * 60       # 妙记转写可能要几分钟，最多等 15 分钟
VIDEO_POLL_INTERVAL = 10


def _video_job_result(job_id: str):
    """读 video_jobs/talent_videos → (status, 推送文本, error)。"""
    conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True, timeout=10)
    try:
        r = conn.execute("SELECT status, error FROM video_jobs WHERE id=?",
                         (job_id,)).fetchone()
        if not r:
            return "running", "", ""
        if r[0] != "done":
            return r[0], "", r[1] or ""
        v = conn.execute(
            "SELECT title, report FROM talent_videos WHERE job_id=? AND status='done' "
            "ORDER BY rank LIMIT 1", (job_id,)).fetchone()
        if not v or not v[1]:
            return "done", "视频处理完成，但报告为空", ""
        report = v[1]
        # Markdown 标题转成可读的分节符，飞书纯文本更顺眼
        lines = [ln.replace("## ", "▎", 1) if ln.startswith("## ") else ln
                 for ln in report.splitlines()]
        return "done", f"📹 视频拆解：{v[0]}\n\n" + "\n".join(lines), ""
    finally:
        conn.close()


def execute_video(task: dict) -> str:
    """视频链接拆解：调 buyin_worker /teardown_url，轮询到完成后返回报告。"""
    url = (task.get("target") or "").strip()
    if not url:
        return "没收到视频链接，重新发一下～"
    req = urllib.request.Request(
        TEARDOWN_URL_API,
        data=json.dumps({"url": url}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"拆解服务未响应: {e}")
    job_id = data.get("job_id") or ""
    if not job_id:
        raise RuntimeError(f"拆解服务未返回任务号: {data}")

    deadline = time.time() + VIDEO_POLL_TIMEOUT
    while time.time() < deadline:
        time.sleep(VIDEO_POLL_INTERVAL)
        status, text, err = _video_job_result(job_id)
        if status == "done":
            return text
        if status == "failed":
            raise RuntimeError(err or "视频拆解失败（链接可能要求登录或已失效）")
    raise RuntimeError("拆解超时（>15分钟），任务还在后台跑，过会儿问运营老师要结果")
