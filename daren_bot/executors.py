"""任务执行器 —— 对接 SoloBrave 现有管道，不重复造轮子。

抖音号采集: daren-bot → buyin_worker 控制口(:8095, Playwright 常驻浏览器)
  → 巨量百应团长端抓取 → GLM-4.7 结构化 → 写 SoloBrave 达人库
  → 本模块从同机 SQLite 读回字段 → 拼报告卡片文本。

商品/vision: 暂未接入，保持 NotImplementedError 走诚实失败返还路径（spec）。
"""
import base64
import json
import logging
import re as _re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("daren-bot.executors")

BUYIN_CONTROL = "http://127.0.0.1:8095"          # buyin_worker 控制口
SOLOBRAVE_DB = Path("/Users/qichen/solobrave-prod/data/solobrave.db")
JOB_POLL_INTERVAL = 5
JOB_WAIT_TIMEOUT = 20 * 60                        # 比 buyin_worker JOB_TIMEOUT 长，覆盖 429 降级耗时


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
    raise RuntimeError("采集超时(20分钟)，积分已返还，可稍后再试")


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
    _agency = pick("agency")
    if _agency:
        basics.append(f"签约 {_agency}")
    _rc = pick("related_company")
    if _rc:
        basics.append(f"关联 {_rc}")
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
    vst = pick("video_scene_total")
    if vst:
        gmv.append(f"视频结算(近30天) {vst}")
    vss = pick("video_scene_single")
    if vss:
        gmv.append(f"单视频结算(近30天) {vss}")
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
    vcr = t.get("video_completion_rate") or pick("video_completion_rate_scene")
    if vcr:
        content.append(f"完播 {_fmt_pct(vcr)}")
    vir = t.get("video_interaction_rate") or pick("video_interaction_rate_scene")
    if vir:
        content.append(f"互动 {_fmt_pct(vir)}")
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
    fcat = pick("fan_category")
    if fcat:
        fans.append(f"类目偏好 {fcat}")
    fct = pick("fan_city_tier")
    if fct:
        # ★ fix/json-str-unwrap: 该列存的是 JSON 编码字符串 '"三线城市(22%)"'，剥掉引号再展示
        if isinstance(fct, str):
            try:
                import json as _json
                _p = _json.loads(fct)
                if isinstance(_p, str):
                    fct = _p
            except Exception:
                pass
        fans.append(f"城市线级 {fct}")
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

    lines.append(f"—— 数据来源：AI 带货助手，采集于 "
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
    """商品链接分析：缓存命中直接用缓存；未命中调 buyin_worker /collect-product-url
    浏览器抓取 → 商品共享缓存 → 结合该达人画像出「能带/慎带/别带」结论。
    返回值为「商品名\n结论Markdown」，展示层由 worker 分支决定。"""
    from . import db as _db
    product_key = (task.get("target") or "").strip()
    if not product_key:
        return "没收到商品链接，重新发一下～"

    stats = None
    if task.get("cache_hit"):
        cached = _db.cache_get(product_key)
        if cached:
            try:
                stats = json.loads(cached["stats_json"] or "{}")
            except Exception:
                stats = None

    if stats is None:
        try:
            res = _http_json("POST", f"{BUYIN_CONTROL}/collect-product-url",
                             {"url": product_key})
        except Exception as e:
            raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL}): {e}")
        cid = res.get("collect_id") or ""
        if not cid:
            raise RuntimeError(f"采集器未返回任务号: {res}")
        deadline = time.time() + 240      # 开页+提取约1-2分钟，最多等4分钟
        job = {}
        while time.time() < deadline:
            time.sleep(JOB_POLL_INTERVAL)
            try:
                job = _http_json("GET", f"{BUYIN_CONTROL}/jobs/{cid}")
            except Exception:
                job = {}
            st = job.get("status") or ""
            if st in ("done", "failed", "need_login"):
                break
        st = (job or {}).get("status") or ""
        if st == "need_login":
            raise RuntimeError("百应登录态过期了，跟运营老师说一声扫码重登就好")
        if st != "done":
            raise RuntimeError((job or {}).get("error") or "商品采集超时，过会儿再试")
        try:
            stats = json.loads((job or {}).get("result") or "{}")
        except Exception:
            stats = {}
        if not stats.get("name"):
            raise RuntimeError("没能从页面里识别出商品信息，发个商品截图给我也行")
        # 写商品共享缓存（全平台达人共用，TTL 7 天）
        try:
            _db.cache_put(product_key,
                          name=stats.get("name", ""),
                          price=stats.get("price", ""),
                          commission=stats.get("commission", ""),
                          stats=stats,
                          source_url=stats.get("resolved_url") or product_key)
        except Exception:
            log.exception("product cache_put failed: key=%s", product_key)

    verdict = _product_verdict(task.get("tenant_key", ""), stats)
    title = stats.get("name") or "商品分析"
    # 结构化信封：worker 拆包后通知（卡片/文字版用 md+title，表格用 stats）
    return json.dumps({"title": title, "stats": stats, "md": verdict},
                      ensure_ascii=False)


PRODUCT_VERDICT_PROMPT = '''你是抖音电商达人的AI选品顾问。根据【商品信息】和【该达人的账号数据】判断这个商品适不适合这个达人带货，输出 Markdown（不要输出其他内容）：

## 结论
第一行只写「🟢 能带」「🟡 慎带」「🔴 别带」其中一个（不要写其他的），第二行用一句话说明理由（不超过30字）
## 匹配分析
- 价格匹配：商品价格 vs 该达人粉丝消费力
- 人群匹配：商品目标人群 vs 该达人粉丝画像（性别/年龄/地域）
- 佣金评估：佣金率是否有吸引力（没有佣金数据就明说没有，不要编）
## 行动建议
- 2-3条具体可执行的建议（怎么讲卖点、适合什么内容形式）

规则：
1. 有数据必须引用数据；没有的维度明说"没有数据"，绝对不要编造数字。
2. 如果商品信息里关键字段（价格/佣金）缺失，结论最多给"🟡 慎带"并说明缺什么。'''


def _product_verdict(tenant_key: str, stats: dict) -> str:
    ctx = _talent_context(tenant_key)
    lines = [f"- {k}：{v}" for k, v in stats.items()
             if v and k != "resolved_url"]
    prompt = (PRODUCT_VERDICT_PROMPT
              + "\n【商品信息】\n" + ("\n".join(lines) if lines else "(空)")
              + ("\n【该达人的账号数据】" + ctx if ctx else "\n【该达人的账号数据】暂无"))
    content = ""
    for attempt in (1, 2):
        try:
            content = _qa_llm(prompt, model='glm-4-flash')
        except Exception as e:
            log.warning("product verdict attempt %d failed: %s", attempt, e)
            content = ""
        if content.strip():
            break
    ans = content.strip()
    if not ans:
        return ("## 结论\n🟡 慎带（AI 分析暂时没出结果，可稍后重试）\n\n"
                "## 商品信息\n" + ("\n".join(lines) if lines else "- （无）"))
    return ans[:4000]


VISION_PRODUCT_PROMPT = '''你是电商商品信息提取器。从用户发的商品截图里提取以下字段，只输出 JSON（不要输出任何其他文字）：
{"name":"商品名","price":"到手价(字符串,带¥)","commission":"佣金(字符串)","commission_rate":"佣金率(字符串,百分数)","sales":"销量(字符串)","shop":"店铺名","category":"类目","shop_score":"店铺体验分(字符串,没有则空)","highlights":"核心卖点(一句话,60字内)","target_crowd":"目标人群(一句话)","coupon":"优惠信息(字符串,没有则空)"}
规则：
1. 只提取截图里明确可见的字段，没有的填空字符串，绝对不要编造数字（尤其价格/佣金/销量）。
2. 截图里可能有多个商品，取画面主体那个。
3. 金额保持截图原文格式（如 ¥29.9）。截图里没有的字段一律填空。'''


def _extract_json_obj(text: str) -> dict:
    """从模型输出里截取第一个配平的 JSON 对象。"""
    start = text.find('{')
    if start < 0:
        return {}
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    d = json.loads(text[start:i + 1])
                    return d if isinstance(d, dict) else {}
                except Exception:
                    return {}
    return {}


def _minimax_cfg():
    cfg = json.load(open(Path.home() / '.openclaw' / 'openclaw.json', encoding='utf-8'))
    mm = cfg.get('models', {}).get('providers', {}).get('minimax') or {}
    key = mm.get('apiKey') or ''
    if not key:
        raise RuntimeError('openclaw.json 缺少 minimax apiKey')
    return 'https://api.minimaxi.com/v1', key


def _kimi_cfg():
    cfg = json.load(open(Path.home() / '.openclaw' / 'openclaw.json', encoding='utf-8'))
    km = cfg.get('models', {}).get('providers', {}).get('kimi') or {}
    base = (km.get('baseUrl') or 'https://api.kimi.com/coding').rstrip('/')
    if not base.endswith('/v1'):
        base += '/v1'
    key = km.get('apiKey') or ''
    if not key:
        raise RuntimeError('openclaw.json 缺少 kimi apiKey')
    return base, key


def _vision_try(base: str, key: str, model: str, b64: str,
                temperature: float = 0.1) -> dict:
    """OpenAI 协议的 vision 调用，返回提取字段（无 name 返回 {}）。
    kimi coding 端点限 temperature=1。"""
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': VISION_PRODUCT_PROMPT},
                {'type': 'image_url',
                 'image_url': {'url': f'data:image/jpeg;base64,{b64}'}},
            ]}],
            'temperature': temperature,
            'max_tokens': 1500,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    return _extract_json_obj(content)


def _vision_extract_product(b64: str) -> dict:
    """商品截图 → vision 结构化字段。kimi 视觉优先，退 minimax M3 系列。
    ★ 2026-10-18 老大拍板摘除智谱（余额耗尽 429/1113）。"""
    last_err = ""
    # 第一梯队：kimi 视觉
    try:
        base, key = _kimi_cfg()
        d = _vision_try(base, key, 'moonshot-v1-8k-vision-preview', b64,
                        temperature=1.0)
        if d.get('name'):
            log.info("vision via kimi")
            return d
        last_err = "kimi no-name output"
    except Exception as e:
        last_err = str(e)
        log.warning("vision kimi failed: %s", e)
    # 第二梯队：minimax M3 系列（新套餐原生多模态）
    try:
        base, key = _minimax_cfg()
        for model in ('MiniMax-M3.1-Flash-Preview', 'MiniMax-M3'):
            try:
                d = _vision_try(base, key, model, b64, temperature=1.0)
            except Exception as e:
                last_err = str(e)
                log.warning("vision minimax(%s) failed: %s", model, e)
                continue
            if d.get('name'):
                log.info("vision via minimax(%s) fallback", model)
                return d
            last_err = f"{model} no-name output"
    except Exception as e:
        last_err = str(e)
        log.warning("minimax cfg failed: %s", e)
    raise RuntimeError('AI 视觉暂时不可用或没识别出商品（%s），可以换一张清晰截图，或直接发商品链接'
                       % last_err[:120])


def execute_image_vision(task: dict) -> str:
    """商品截图识别：vision 提取商品字段 → 结合该达人画像出「能带/慎带/别带」。
    废图（识别不出商品）抛异常 → worker 自动返还积分（spec：废图不扣分）。"""
    path = Path(task.get("target") or "")
    if not path.is_file():
        raise RuntimeError("截图文件没找到，重新发一下")
    b64 = base64.b64encode(path.read_bytes()).decode()
    stats = _vision_extract_product(b64)
    verdict = _product_verdict(task.get("tenant_key", ""), stats)
    # 结构化信封：worker 拆包后通知（截图来源标记为「截图」）
    return json.dumps({"title": stats.get("name") or "商品分析",
                       "stats": stats, "md": verdict,
                       "source": "截图"}, ensure_ascii=False)


def _send_text_quiet(sender, chat_id: str, text: str) -> None:
    """补发文字版消息；失败只记日志，绝不影响已发出的卡片。"""
    try:
        sender.send_text(chat_id, text)
    except Exception:
        log.exception("text version send failed: chat_id=%s", chat_id)


def notify_report(sender, chat_id: str, douyin_id: str, report_text: str) -> None:
    """报告推送：卡片长图 + 一句话摘要 + 完整文字版（便于复制）。"""
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
            _send_text_quiet(sender, chat_id, report_text)
            try:
                from . import bitable_sync, db as ddb
                tenant = ddb.get_tenant_by_douyin(douyin_id)
                if tenant:
                    summary = (t.get("ai_summary") or t.get("notes") or "")[:2000]
                    bitable_sync.sync_profile(
                        tenant["tenant_key"], t,
                        t.get("risk_rating") or "", summary,
                        img_path=str(tmp))
            except Exception:
                log.exception("bitable sync profile failed: douyin_id=%s", douyin_id)
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
        _send_text_quiet(sender, chat_id, f"💬 {answer}")
        try:
            from . import bitable_sync
            bitable_sync.sync_log(tenant_key, "问答", question, answer)
        except Exception:
            log.exception("bitable sync log failed: tenant=%s", tenant_key)
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


def _qa_llm(prompt: str, model: str = '') -> str:
    """问答直连文字模型链：kimi moonshot-v1-32k → minimax ×2。
    ★ 2026-10-18 老大拍板摘除智谱（余额耗尽 429/1113）。kimi coding 端点
    temperature 必须 1.0（reasoning 模型），max_tokens 给足防推理 token 吞空。"""
    errors = []
    for base, key, model, temp in _fallback_cfgs_daren():
        try:
            req = urllib.request.Request(
                base + '/chat/completions',
                data=json.dumps({
                    'model': model,
                    'messages': [{'role': 'user', 'content': prompt[:15000]}],
                    'temperature': temp,
                    'max_tokens': 1200,
                }).encode('utf-8'),
                headers={'Content-Type': 'application/json',
                         'Authorization': f'Bearer {key}'},
                method='POST')
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode())
            out = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
            if out.strip():
                return out
            errors.append(f'{model} empty content')
        except Exception as e:
            errors.append(f'{model}: {str(e)[:100]}')
    raise RuntimeError('问答降级链全灭: ' + ' | '.join(errors))


def _fallback_cfgs_daren():
    """kimi/moonshot → minimax 降级配置（与 buyin_worker._fallback_cfgs 同源）。"""
    out = []
    try:
        cfg = json.load(open(Path.home() / '.openclaw' / 'openclaw.json', encoding='utf-8'))
        km = cfg.get('models', {}).get('providers', {}).get('kimi') or {}
        kbase = (km.get('baseUrl') or 'https://api.kimi.com/coding').rstrip('/')
        if not kbase.endswith('/v1'):
            kbase += '/v1'
        if km.get('apiKey'):
            out.append((kbase, km['apiKey'], 'moonshot-v1-32k', 1.0))
        mm = cfg.get('models', {}).get('providers', {}).get('minimax') or {}
        if mm.get('apiKey'):
            for m in ('MiniMax-M3.1-Flash-Preview', 'MiniMax-M3'):
                out.append(('https://api.minimaxi.com/v1', mm['apiKey'], m, 1.0))
    except Exception as e:
        log.warning("fallback cfgs read failed: %s", e)
    return out


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


def _fmt_compare_brief(t: dict) -> str:
    """单个达人的对比摘要（全部来自库里真实字段，没有就跳过，绝不编）。"""
    parts = []
    name = t.get('name') or '?'
    dyid = t.get('douyin_id') or ''
    parts.append(f"昵称{name}" + (f"（抖音号{dyid}）" if dyid else ""))
    if t.get('followers'):
        parts.append(f"粉丝{int(t['followers'])}")
    if t.get('category'):
        parts.append(f"主营类目{t['category']}")
    if t.get('level'):
        parts.append(f"等级{t['level']}")
    if t.get('city'):
        parts.append(f"城市{t['city']}")
    if t.get('total_gmv'):
        parts.append(f"累计GMV{_fmt_wan(t['total_gmv'])}")
    if t.get('video_gpm'):
        parts.append(f"视频GPM{float(t['video_gpm']):.0f}")
    raw = t.get('ocr_raw_fields') or ''
    try:
        raw = json.loads(raw)
        if isinstance(raw, str):
            raw = json.loads(raw)
    except Exception:
        raw = {}
    if isinstance(raw, dict):
        for label, key in (('性别', 'fan_gender'), ('年龄', 'fan_age'),
                           ('人群', 'fan_crowd'), ('消费水平', 'fan_consume_level')):
            v = raw.get(key)
            if v:
                parts.append(f"{label}{json.dumps(v, ensure_ascii=False)}")
        vg = raw.get('video_gpm') or raw.get('video_gpm_text')
        if vg and not t.get('video_gpm'):
            parts.append(f"视频GPM{vg}")
        cd = raw.get('category_distribution')
        if isinstance(cd, dict) and cd:
            top = sorted(cd.items(), key=lambda kv: -float(kv[1] or 0))[:2]
            parts.append("类目分布" + "、".join(f"{k}{v}%" for k, v in top))
        cb = raw.get('cooperating_brands') or raw.get('cooperating_brands_distribution')
        if isinstance(cb, dict) and cb:
            top = sorted(cb.items(), key=lambda kv: -float(kv[1] or 0))[:2]
            parts.append("主要品牌" + "、".join(f"{k}{round(float(v))}%" for k, v in top))
        fg = raw.get('fan_growth')
        if fg:
            parts.append(f"近2月涨粉{fg}")
    return "｜".join(parts)


def _name_candidates(q: str) -> list:
    """从问题里剥出名字候选：去掉常见功能词后按分隔符切，供对比/找人复用。"""
    t = _re.sub(r'[【】\[\]（）()~～!！?？\s]+', ' ', q)
    t = _re.sub(r'我的(账号|的号|号)?', ' ', t)
    t = _re.sub(r'(账号|对比|比较|分析一下?|发一下|看看|怎么样|帮我|一下|和|与|跟|还有|以及)', ' ', t)
    return [c for c in _re.split(r'[、,，;；\s]+', t) if len(c) >= 2]


def _find_mentioned_peers(tk: str, q: str) -> list:
    """问题里点名的第三方达人。匹配方式: 库里名字(≥3字)是否出现在问题原文里
    （比名字模糊查询健壮——'小晴姑姑的粉丝数量...都没有' 这种带修饰的长句也能命中）。
    重名取数据最全的最新一行。返回 [{'name','dyid','row'}]，不含本人绑定号。"""
    try:
        from . import db
        tenant = db.get_tenant(tk)
        own_dyid = (tenant or {}).get("bind_douyin_id") or ""
        flat = _re.sub(r'\s+', '', q)          # 去空白后做子串匹配
        conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            names = conn.execute(
                "SELECT id, name FROM talents WHERE status != 'archived' "
                "AND length(name) >= 3").fetchall()
            # 命中: 名字在问题原文里; ≥4 字的名字再去掉首字试一次
            # (采集名常带前缀, 如 '名小晴姑姑' —— 问题里写的是 '小晴姑姑')
            matched_ids = []
            for r in names:
                n = r['name']
                if not n:
                    continue
                if n in flat or (len(n) >= 4 and n[1:] in flat):
                    matched_ids.append(r['id'])
            # 包含关系去重: '大丸子【囍事来】' 和 '大丸子' 同时命中时, 保留更完整的全称
            matched_names = [r['name'] for r in names if r['id'] in set(matched_ids)]
            matched_ids = [r['id'] for r in names
                           if r['id'] in set(matched_ids)
                           and not any(r['name'] != m and r['name'] in m
                                       for m in matched_names)]
            if not matched_ids:
                return []
            # 同名字多行时按 数据完整度+新鲜度 打分取最优
            peers, seen_names = [], set()
            for mid in matched_ids[:10]:
                rows = conn.execute(
                    "SELECT * FROM talents WHERE (id = ? OR name = "
                    "(SELECT name FROM talents WHERE id = ?)) AND status != 'archived' "
                    "ORDER BY updated_at DESC LIMIT 5", (mid, mid)).fetchall()
                best, best_score = None, -1
                for r in rows:
                    r = dict(r)
                    ocr_len = len(str(r.get('ocr_raw_fields') or ''))
                    score = (2 if r.get('douyin_id') else 0) \
                          + (2 if r.get('followers') else 0) \
                          + (1 if r.get('category') else 0) \
                          + (3 if ocr_len > 100 else 0) \
                          + int((r.get('updated_at') or 0) / 1e10)
                    if score > best_score:
                        best, best_score = r, score
                if not best or best.get('name') in seen_names:
                    continue
                if not best.get('douyin_id') or best.get('douyin_id') == own_dyid:
                    continue
                seen_names.add(best.get('name'))
                peers.append({'name': best.get('name'), 'dyid': best.get('douyin_id'),
                              'row': best})
            return peers
        finally:
            conn.close()
    except Exception:
        log.exception("find mentioned peers failed")
        return []


def _peer_data_empty(row: dict) -> bool:
    """判断该达人行是否是'空采集'（防编造守卫同款判定）。"""
    return (not row.get('followers') and not row.get('category')
            and not row.get('total_products') and not row.get('total_shops')
            and len(str(row.get('ocr_raw_fields') or '')) < 100)


def _build_compare_block(tk: str, q: str) -> str:
    """对比问题取数：本人绑定账号 + 问题里点名的对标号。"""
    try:
        from . import db
        tenant = db.get_tenant(tk)
        own_dyid = (tenant or {}).get("bind_douyin_id") or ""
        if not own_dyid:
            return ""
        peers = _find_mentioned_peers(tk, q)
        own = _read_talent("", own_dyid)
        if not own:
            return ""
        lines = ["【你的账号】" + _fmt_compare_brief(own)]
        if peers:
            for p in peers[:3]:
                lines.append("【对标账号】" + _fmt_compare_brief(p['row']))
        else:
            lines.append("【对标账号】问题里点名的达人未在库里找到数据，"
                         "回答时明说'对方数据还没采集，发我对方抖音号，我采完再给你完整对比'")
        return "\n".join(lines)
    except Exception:
        log.exception("build compare block failed")
        return ""


def execute_qa(task: dict) -> str:
    """业务问答：结合该达人绑定的账号画像回答；超出带货范围由模型拒答。
    对报告/分析的质疑类问题强制走回答路径（弱模型爱把质疑当无关问题拒答）。"""
    import re as _re
    from . import db as _db
    q = (task.get("target") or "").strip()
    if not q:
        return "没收到问题内容，重新发一下～"
    ctx = _talent_context(task.get("tenant_key", ""))
    # ★ fix/third-party-recollect (2026-10-18): "小晴姑姑的...都没有"/"她的账号没有数据,发给我"
    #   → 不绕模型, 直接免费重采并推送. 判定: 抱怨词 + 点名的对标号是空采集行.
    if _re.search(r"没有|暂无|缺|重新采|再采|采集|发给我|补充|都没有", q):
        _empties = [p for p in _find_mentioned_peers(task.get("tenant_key", ""), q)
                    if _peer_data_empty(p['row'])]
        if _empties and task.get("chat_id"):
            _p = _empties[0]
            try:
                from . import db as _db
                _db.enqueue_task(task["tenant_key"], "douyin", _p['dyid'], 0,
                                 chat_id=task["chat_id"])
                return (f"你说得对，{_p['name']}（{_p['dyid']}）这次没有采集到有效数据，"
                        "是我这边采集失误。已免费安排重新采集，报告出来直接发你～")
            except Exception:
                log.exception("enqueue recollect failed")
    # ★ fix/compare-qa (2026-10-18 实锤): "我的账号和XX、YY对比发一下" 被弱模型判无关拒答.
    #   对比/比较是核心业务问题: 强制回答 + 注入库里的真实对比数据, 杜绝编造.
    compare_block = ""
    if _re.search(r"对比|比较|差距|哪家强|谁厉害", q):
        compare_block = _build_compare_block(task.get("tenant_key", ""), q)
    challenge = bool(_re.search(r"有误|不对|错|分析|报告|数据|画像|建议|对比|比较|差距", q))
    force_line = (
        '【系统指令】达人这条消息是在质疑或询问已生成的报告/分析内容，'
        '属于账号分析业务，必须正常回答，绝对禁止输出"拒"。\n' if challenge else '')
    compare_line = (
        '【系统指令】达人在要求做账号对比，这是核心业务问题，必须基于下面的对比数据回答，'
        '绝对禁止输出"拒"。回答结构：先并排列出各方核心数据（粉丝/类目/GMV/GPM/等级/城市/粉丝画像），'
        '再指出达人自己账号的优势和差距，最后给可执行建议。'
        '对比数据里没有的维度，明说"对方该维度暂无数据"，严禁编造数字。\n'
        + compare_block + '\n' if compare_block else '')
    prompt = (
        '你是抖音电商达人的AI选品顾问，用第二人称"你"回答问题。\n'
        '只回答抖音带货、选品、商品判断、账号运营、粉丝画像、直播相关的问题，'
        '以及达人对报告/分析内容的疑问和纠错（这类属于业务范围，必须回答，不许拒）。\n'
        '如果问题与这些无关（闲聊、天气、代码、数学、新闻等），只输出一个字：拒\n'
        '如果达人质疑报告或分析有误（如"分析是不是有误""数据不对"）：先核对下面的账号数据，'
        '数据支持就确认并解释依据；确实和分析矛盾就大方承认并给出纠正后的结论，'
        '禁止把这类质疑当无关问题拒答。\n'
        '回答要求：优先结合该达人自己的数据给具体可执行的建议（80-250字），'
        '结构为「结论 + 数据依据 + 行动建议」；有数据必须引用数据，'
        '没有的维度不要编造数字，不要喊口号。\n'
        + force_line
        + compare_line
        + (f'【该达人的账号数据】{ctx}\n' if ctx else '')
        + f'【达人问】{q}'
    )
    content = ""
    for attempt in (1, 2, 3):
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
        # ★ fix/third-party-recollect (2026-10-18 实锤): "小晴姑姑的...都没有"/"她的账号没有数据,
        #   发给我" 被当成质疑本人报告, 乱发道歉话术. 先识别第三方数据诉求: 点名了对标号 +
        #   抱怨没数据 → 免费重采并推送, 而不是答非所问.
        if _re.search(r"没有|暂无|缺|重新采|再采|采集|发给我|补充|都没有", q):
            empty_peers = [p for p in _find_mentioned_peers(task.get("tenant_key", ""), q)
                           if _peer_data_empty(p['row'])]
            if empty_peers:
                p = empty_peers[0]
                chat_id = task.get("chat_id") or ""
                if chat_id:
                    try:
                        from . import db as _db
                        _db.enqueue_task(task["tenant_key"], "douyin", p['dyid'], 0,
                                         chat_id=chat_id)
                        return (f"你说得对，{p['name']}（{p['dyid']}）这次没有采集到有效数据，"
                                "是我这边采集失误。已免费安排重新采集，报告出来直接发你～")
                    except Exception:
                        log.exception("enqueue recollect failed")
                return (f"{p['name']}（{p['dyid']}）这次确实没采集到有效数据，"
                        "重新发一次 TA 的抖音号，我免费重采～")
        if compare_block:
            # 对比类问题仍被误拒：直接输出库里真实数据的确定性对比，绝不让达人吃闭门羹
            back = "给你整理了三方账号的真实数据对比：\n\n" + compare_block
            back += ("\n\n以上为库里已采集的真实数据，缺失维度就是还没采集到。"
                     "想看哪一项的深挖对比，或者要我把某个对标号的最新数据重新采一遍，直接说～")
            return back
        if challenge:
            # 质疑类问题被误拒：先确认问的是本人账号（第三方诉求上面已拦截），
            # 再用账号数据给兜底回应，绝不让达人吃闭门羹
            if _find_mentioned_peers(task.get("tenant_key", ""), q):
                # 问题里点名了第三方账号，不属于"质疑本人报告"，不能发道歉话术
                return ("这个问题涉及到你提到的另一位达人。我目前只有TA的已采集数据，"
                        "需要我深挖 TA 的账号，把 TA 的抖音号发我即可～")
            cat = ""
            m = (_re.search(r"主营类目[\s：:]*([\u4e00-\u9fff/]+)", ctx)
                 or _re.search(r"专注([\u4e00-\u9fff/]{2,10})领域", ctx))
            if m:
                cat = m.group(1).strip()
            back = "收到你的质疑。我核对了你的账号数据"
            if cat:
                back += f"，你的主营类目是{cat}"
            back += ("。如果报告里的推荐和你的实际主营不匹配，以你的实际主营为准——"
                     "那是我分析失误，抱歉。你可以把想带的具体商品发我，我重新给你针对性分析。")
            return back
        return ("我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，"
                "其他问题帮不上忙哦～")
    # 问题提到了具体商品/视频但这条消息没带链接或截图（带了会走别的路由），
    # 说明 AI 没看到实物，提醒一句避免达人误以为 AI 已经分析过那个商品
    if any(k in q for k in ("视频", "商品", "产品", "链接", "这条", "这个品", "这款", "那个品")):
        ans += ("\n\n⚠️ 这条消息里没有附视频链接或商品截图，"
                "以上是按你的账号画像给的通用建议；把链接或截图发我，"
                "我可以针对具体商品分析～")
    return ans[:900]


# ---------------- 视频链接拆解（teardown_url：下载→转写→画面→报告） ----------------

TEARDOWN_URL_API = "http://127.0.0.1:8095/teardown_url"
VIDEO_POLL_TIMEOUT = 15 * 60       # 妙记转写可能要几分钟，最多等 15 分钟
VIDEO_POLL_INTERVAL = 10


def _video_job_result(job_id: str):
    """读 video_jobs/talent_videos → (status, title, 报告md原文, error)。"""
    conn = sqlite3.connect(f"file:{SOLOBRAVE_DB}?mode=ro", uri=True, timeout=10)
    try:
        r = conn.execute("SELECT status, error FROM video_jobs WHERE id=?",
                         (job_id,)).fetchone()
        if not r:
            return "running", "", "", ""
        if r[0] != "done":
            return r[0], "", "", r[1] or ""
        v = conn.execute(
            "SELECT title, report FROM talent_videos WHERE job_id=? AND status='done' "
            "ORDER BY rank LIMIT 1", (job_id,)).fetchone()
        if not v or not v[1]:
            return "done", v[0] if v else "", "", ""
        return "done", v[0], v[1], ""
    finally:
        conn.close()


def _md_to_text(md: str) -> str:
    """Markdown 报告转飞书纯文本（## → ▎），卡片渲染失败时兜底用。"""
    return "\n".join(ln.replace("## ", "▎", 1) if ln.startswith("## ") else ln
                     for ln in (md or "").splitlines())


def execute_video(task: dict) -> str:
    """视频链接拆解：调 buyin_worker /teardown_url，轮询到完成后返回报告。
    返回值为「标题\n报告Markdown原文」，展示层（卡片/纯文本）由 worker 分支决定。"""
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
        status, title, report, err = _video_job_result(job_id)
        if status == "done":
            if not report:
                return f"{title or '视频'}\n(拆解完成，报告为空)"
            return f"{title or '视频拆解报告'}\n{report}"
        if status == "failed":
            raise RuntimeError(err or "视频拆解失败（链接可能要求登录或已失效）")
    raise RuntimeError("拆解超时（>15分钟），任务还在后台跑，过会儿问运营老师要结果")


def notify_product_card(sender, chat_id: str, title: str, report: str,
                        tenant_key: str, stats: dict = None,
                        source: str = "") -> None:
    """商品分析推送：分节卡片长图（复用拆解渲染器）+ 标题 + 文字版
    + 多维表格「商品推荐记录」（结论列单选带色）。"""
    if sender is None:
        log.info("[notify-no-sender] product tenant=%s", tenant_key)
        return
    title = title or "商品分析"
    try:
        from .report_card import render_teardown_card
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"daren_product_{abs(hash(title)) % 10**8}.png"
        render_teardown_card(title, report, str(tmp),
                             kicker="商品分析",
                             chip="能带 · 慎带 · 别带")
        sender.send_image_path(chat_id, str(tmp))
        sender.send_text(chat_id, f"🛒 商品分析完成：{title}")
        _send_text_quiet(sender, chat_id,
                         f"🛒 {title}\n{_md_to_text(report)}")
        try:
            from . import bitable_sync
            bitable_sync.sync_product(
                tenant_key, stats or {"name": title}, report, source,
                img_path=str(tmp))
        except Exception:
            log.exception("bitable sync product failed: tenant=%s", tenant_key)
        return
    except Exception:
        log.exception("render product card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, f"🛒 {title}\n\n{_md_to_text(report)}")


def notify_video_card(sender, chat_id: str, result_text: str, tenant_key: str) -> None:
    """视频拆解推送：优先渲染分节卡片长图发图+标题；渲染失败回退纯文本。"""
    if sender is None:
        log.info("[notify-no-sender] video tenant=%s", tenant_key)
        return
    title, _, report = (result_text or "").partition("\n")
    try:
        from .report_card import render_teardown_card
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"daren_teardown_{abs(hash(title)) % 10**8}.png"
        render_teardown_card(title, report, str(tmp))
        sender.send_image_path(chat_id, str(tmp))
        sender.send_text(chat_id, f"📹 视频拆解完成：{title}")
        _send_text_quiet(sender, chat_id,
                         f"📹 {title}\n{_md_to_text(report)}")
        try:
            from . import bitable_sync
            bitable_sync.sync_log(
                tenant_key, "视频拆解",
                (result_text or "").split("\n", 1)[0][:500],
                _md_to_text(report))
        except Exception:
            log.exception("bitable sync log failed: tenant=%s", tenant_key)
        return
    except Exception:
        log.exception("render teardown card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, f"📹 视频拆解：{title}\n\n{_md_to_text(report)}")
