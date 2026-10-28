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

from .llm_text import strip_think as _strip_think_impl
from . import persona
from .llm_channels import (
    CP_ANTHROPIC_BASE,
    CP_DEFAULT_MODEL,
    CODE_SUB_BASE,
    call_anthropic as _call_anthropic_impl,
    call_openai as _call_openai_impl,
    code_sub_token as _code_sub_token_impl,
    env_api_key as _env_api_key_impl,
    openclaw_api_key as _openclaw_api_key_impl,
    text_rings as _text_rings_impl,
    vision_rings as _vision_rings_impl,
)
# ★ `_anthropic_messages` 薄壳要转发给 llm_channels 里的实现 ——
#   之前**漏了这个 import**，薄壳一被调用就 NameError
#   （静态扫描 tests/test_no_undefined_names.py 抓到的，2026-10-24）。
from .llm_channels import _anthropic_messages as _anthropic_messages_impl
from .product_link import extract_product_id
from . import product_intent
# ★ 计费成本常量（统一用 `config.COST_*`，别用局部别名 ——
#   防漏测试 `tests/test_credits_gate.py` 只认 `config.COST_*`，
#   用别名会解析不出成本 ⇒ 直接判红）。
from . import config

log = logging.getLogger("daren-bot.executors")

BUYIN_CONTROL = "http://127.0.0.1:8095"          # buyin_worker 控制口
SOLOBRAVE_DB = Path("/Users/qichen/solobrave-prod/data/solobrave.db")
JOB_POLL_INTERVAL = 5
JOB_WAIT_TIMEOUT = 35 * 60                        # 盖过 buyin_worker JOB_TIMEOUT(25min) + MiniMax-M3 视觉提取 8 批串行余量


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
    seen_once = False
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        try:
            job = _http_json("GET", f"{BUYIN_CONTROL}/jobs/{cid}")
        except Exception as e:
            log.warning("job poll failed: %s", e)
            continue
        if job.get("error") == "not found":
            # 采集器重启会导致内存任务表丢失, 快速失败让用户重发, 不傻等超时。
            # 但「从未见过任务」≠ 重启丢失：采集器忙时任务在排队，/jobs 尚不可见，
            # 只有见过后又消失才判定丢失（2026-10-18 排队 10 分钟被误判的教训）。
            if seen_once:
                not_found_streak += 1
                if not_found_streak >= 3:
                    raise RuntimeError("采集器重启导致任务丢失，积分已返还，请重新发送抖音号")
            continue
        not_found_streak = 0
        seen_once = True
        status = job.get("status", "")
        if status == "done":
            return _build_report(job.get("talent_id", ""), douyin_id)
        if status in ("failed", "need_login"):
            raise RuntimeError(job.get("error") or f"采集失败({status})")
    raise RuntimeError("采集超时(35分钟)，积分已返还，可稍后再试")


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
        conn = _ro_conn()
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


def _ro_conn():
    """只读连接 solobrave.db。

    ⚠️ 2026-10-21 修复（本文件 + reflow.py 共 5 处同款 bug）：
      **不要用 `sqlite3.connect(f"file:{DB}?mode=ro", uri=True)`** ——
      CLT sqlite 3.54 打不开 WAL 库的只读 URI，会抛
      `OperationalError: unable to open database file`。
      致命之处不在于报错，而在于**上层 `except Exception` 把异常静默吞掉**：
      `_talent_context` 因此返回空串 → AI 选品判断和 AI 问答**拿不到任何
      达人画像，等于盲判**；报告卡/周报同理。全程无日志，所以一直没人发现。
      正确姿势：**普通连接 + `PRAGMA query_only=ON`**（引擎级禁写，比 mode=ro 更强，
      且不受 sqlite 版本 × WAL 只读打开限制）。
    """
    conn = sqlite3.connect(str(SOLOBRAVE_DB), timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _read_talent(talent_id: str, douyin_id: str):
    if not SOLOBRAVE_DB.is_file():
        return None
    conn = _ro_conn()
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


# ---------------- 商品链接采集（达人和商家共用一条链路） ----------------

class ProductNotFound(RuntimeError):
    """平台确实没有这个商品的数据（已下架 / 不在当前推广池）。

    ★ 单独一类：不是技术故障 —— 重试、换链路（打开商品页让 AI 读正文）
      都没用（实测商品页正文是「打开抖音APP」壳，product_id 出现 0 次），
      调用方应据此直接给用户指路：「发个商品截图我照样能看」。
    """


class NeedLogin(RuntimeError):
    """采集账号登录态失效（达人端或团长端）。

    ★ 单独一类，理由和 `ProductNotFound` 同源但更硬：
      **换链路也救不了** —— 登录态掉了，走哪条链路都会撞同一堵墙。
      2026-10-25 实测踩过：达人端掉登录 → ID 通路抛错 → **被 `except RuntimeError`
      吞掉并回退到「页面解析链路」** → 而那条链路查的是**团长端**（违反 §8.1
      「商品数据只走达人端」）→ 团长端也没登录 ⇒ 达人看到的却是
      「采集账号登录态过期了，跟运营老师说一声扫码重登就好」——
      **真实原因（达人端掉登录）被掩盖了**，而且话术是写给运营的，达人看不懂。

    ⇒ 语义：**登录态类错误一律不���退**，直接冒到最上层，
      由展示层翻译成「达人看得懂 + 运营知道该干嘛」的话术。
    """

    def __init__(self, message: str, side: str = "采集账号"):
        super().__init__(message)
        self.side = side   # "达人端" / "团长端" —— 用于定位是谁掉登录


def _raise_need_login(side: str) -> None:
    """统一抛登录态失效（话术统一在这里，不各处乱写）。"""
    raise NeedLogin(
        f"{side}采集账号登录态过期了，需要重新扫码登录", side=side)


def _buyin_logged_in() -> tuple:
    """采集器健康检查 → (可达, 团长端浏览器已启动, 团长端已登录)。

    ★ 为什么多返回「已启动」这一维：Browser.health() 在 ctx is None
      （懒启动未触发）时返回 (False, False) —— 只看 logged_in 会把
      「浏览器没开」误判成「登录态过期」，商家收到的是误导性报错。
      只有「已启动 且 未登录」才是真的登录态失效。
    """
    try:
        d = _http_json("GET", f"{BUYIN_CONTROL}/health", None, timeout=8)
        return True, bool(d.get("browser_up")), bool(d.get("logged_in"))
    except Exception:
        return False, False, False


def _daren_collect_state() -> tuple:
    """采集器健康检查 → (可达, 达人端浏览器已启动, 达人端已登录)。

    ★ 注意「浏览器没启动」≠「登录态过期」：Browser.health() 在 ctx is None
      （懒启动未触发）时返回 (False, False)，只看 logged_in 会误判成掉登录。
      只有「已启动 且 未登录」才是真的登录态失效。
    """
    try:
        d = _http_json("GET", f"{BUYIN_CONTROL}/health", None, timeout=8)
        return True, bool(d.get("daren_browser_up")), bool(d.get("daren_logged_in"))
    except Exception:
        return False, False, False


def daren_channel_state() -> str:
    """达人端采集通道状态 → 'ready' / 'logged_out' / 'unknown'。

    ★ 只看**达人端**（商品数据只走达人端，AGENTS §8.1；团长端登录态与达人链路无关）。
    ★ 'logged_out' 的判定**必须同时看 `daren_browser_up`** ——
      两个浏览器互斥，切到团长端时达人端浏览器会被关掉（§8.2），
      「浏览器没开」≠「登录过期」，否则会把设计当故障、天天误报。
    ★ 超短超时（2s）：这个函数在**达人对话的 3 秒硬约束**里调，
      采集器不在线时不能把达人的回复卡住 ⇒ 拿不到就返回 'unknown'（照常走任务）。
    """
    try:
        d = _http_json("GET", f"{BUYIN_CONTROL}/health", None, timeout=2)
    except Exception:
        return "unknown"
    if not bool(d.get("daren_browser_up")):
        return "unknown"          # 浏览器没开（可能是切到团长端了）
    return "ready" if bool(d.get("daren_logged_in")) else "logged_out"


def collect_product_by_id(product_id: str, timeout: int = 150) -> dict:
    """商品ID → 结构化商品数据。失败抛 RuntimeError（错误信息可读）。

    ★ 2026-10-21 新增：走达人端「选品广场」商品接口（buyin_worker
      /collect-product-id → 浏览器页面内 fetch material_list）。
      接口直接给结构化字段：商品名/主图/三级类目/月销/好评率/近30天销量曲线/
      佣金费率/佣金费/到手价/店铺名/店铺体验分/带货达人数/优惠标签。

    ★ 为什么不用「打开商品页让 AI 读正文」：实测商品页正文里**没有商品数据**
      （留存 HTML 389KB，product_id 出现 0 次，只有「打开抖音APP」壳），
      走这条路 AI 只能瞎编。ID → 接口才是真数据。
    """
    pid = str(product_id or "").strip()
    if not pid:
        raise RuntimeError("商品ID为空")
    reachable, up, logged = _daren_collect_state()
    if not reachable:
        raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL})，请稍后再试")
    if up and not logged:
        _raise_need_login("达人端")
    try:
        res = _http_json("POST", f"{BUYIN_CONTROL}/collect-product-id",
                         {"product_id": pid, "size": 5})
    except Exception as e:
        raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL}): {e}")
    cid = res.get("collect_id") or ""
    if not cid:
        raise RuntimeError(f"采集器未返回任务号: {res}")
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        time.sleep(JOB_POLL_INTERVAL)
        try:
            job = _http_json("GET", f"{BUYIN_CONTROL}/jobs/{cid}")
        except Exception:
            job = {}
        st = job.get("status") or ""
        if st in ("done", "failed", "need_login", "not_found"):
            break
    st = (job or {}).get("status") or ""
    if st == "need_login":
        _raise_need_login("达人端")
    if st == "not_found":
        raise ProductNotFound((job or {}).get("error")
                              or "平台查不到这个商品（可能已下架，或不在当前推广池）")
    if st != "done":
        raise RuntimeError((job or {}).get("error") or "商品查询超时，过会儿再试")
    try:
        stats = json.loads((job or {}).get("result") or "{}")
    except Exception:
        stats = {}
    if not stats.get("name"):
        raise RuntimeError("平台没返回这个商品的名称，发个商品截图给我也行")
    return stats


def collect_product_by_url(url: str, timeout: int = 240) -> dict:
    """商品链接 → 商品字段。失败抛 RuntimeError（错误信息可读）。

    ★ 2026-10-21 链路升级：**先解析商品ID → 走达人端商品接口**。
      实测商品页（haohuo.jinritemai.com）正文里根本没有商品数据
      （留存 HTML 389KB，product_id 出现 0 次，只有「打开抖音APP」壳），
      靠 AI 读正文必然瞎编；ID → 接口才有价格/佣金/月销/类目真值。
      ID 解析不出来（非抖音商品链接）才回退旧的页面解析链路。
      ID 解析出来但平台查无此商品（已下架/不在推广池）→ 抛 ProductNotFound，
      不做无意义回退。

    ★ 2026-10-21 抽出为公共函数：原来这段逻辑内嵌在 execute_product_collect 里，
      招商减负页（商家给链接）也要用同一条链路 —— 采集器(浏览器)是**全局单例**，
      两边共用才能避免各自起浏览器抢资源。

    链路：商品ID → buyin_worker /collect-product-id（达人端接口）→ 结构化真数据；
      ID 解析不出来才走 /collect-product-url（团长端浏览器打开页面 → LLM 结构化提取）
      → GET /jobs/<cid> 轮询取 result。

    ⚠️ 依赖浏览器登录态（团长端 cookie ecom_us_lt_buyin + sessionid；
      达人端 cookie sid_ucp_sso_v1_daren，挂在 buyin-daren-sso 域）。
      未登录会返回 need_login → 这里翻译成可读错误，不静默失败。
    """
    url = (url or "").strip()
    if not url:
        raise RuntimeError("商品链接为空")

    # ★★ 2026-10-21 主链路：**商品ID → 达人端商品接口**（结构化真数据）。
    #   实测商品页（haohuo.jinritemai.com）正文里根本没有商品数据
    #   （留存 HTML 389KB，product_id 出现 0 次，只有「打开抖音APP」壳），
    #   走「AI 读正文」必然瞎编；ID → 接口才有价格/佣金/月销/类目真值。
    #   ID 解析不出来（非抖音商品链接）才回退旧的页面解析链路。
    pid = extract_product_id(url)
    if pid:
        try:
            stats = collect_product_by_id(pid)
            stats.setdefault('resolved_url', url)
            return stats
        except (ProductNotFound, NeedLogin):
            # ★ 这两类**都不许回退**：
            #   - ProductNotFound：平台确实没这个商品 ⇒ 回退也没用。
            #   - NeedLogin：登录态掉了 ⇒ **换链路也救不了**（走哪条都撞同一堵墙），
            #     而回退链路查的是**团长端**（违反 §8.1「商品数据只走达人端」），
            #     更会把「达人端掉登录」掩盖成「团长端掉登录」（2026-10-25 实测踩过）。
            raise
        except RuntimeError as e:
            logging.getLogger(__name__).warning(
                f"[product] ID 通路失败({pid}) → 回退页面解析链路: {e}")
    # ★ 预检登录态：没登录时 page.goto 会白等 60s 超时、或落到 need_login，
    #   商家端表现为"提交后卡住一分钟才失败"。先查健康接口，秒判。
    reachable, up, logged_in = _buyin_logged_in()
    if not reachable:
        raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL})，请稍后再试或改用商品图片")
    if up and not logged_in:
        _raise_need_login("团长端")
    try:
        res = _http_json("POST", f"{BUYIN_CONTROL}/collect-product-url", {"url": url})
    except Exception as e:
        raise RuntimeError(f"采集器未启动或不可达({BUYIN_CONTROL}): {e}")
    cid = res.get("collect_id") or ""
    if not cid:
        raise RuntimeError(f"采集器未返回任务号: {res}")
    deadline = time.time() + timeout     # 开页+提取约1-2分钟，最多等 timeout 秒
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
        _raise_need_login("团长端")
    if st != "done":
        raise RuntimeError((job or {}).get("error") or "商品采集超时，过会儿再试")
    try:
        stats = json.loads((job or {}).get("result") or "{}")
    except Exception:
        stats = {}
    if not stats.get("name"):
        raise RuntimeError("没能从页面里识别出商品信息，发个商品截图给我也行")
    return stats


def execute_product_collect(task: dict) -> str:
    """商品链接分析：缓存命中直接用缓存；未命中调 buyin_worker /collect-product-url
    浏览器抓取 → 商品共享缓存 → 结合该达人画像出「能带/慎带/别带」结论。
    返回值为「商品名\n结论Markdown」，展示层由 worker 分支决定。"""
    from . import db as _db
    product_key = (task.get("target") or "").strip()
    if not product_key:
        return "没收到商品链接，重新发一下～"

    # ★ 2026-10-21 复用层：**结论缓存命中 → 连 LLM 都不调**。
    #   商品数据缓存只省了「开浏览器采集」，结论照样要 LLM 重算（最贵的一步）。
    #   结论缓存省的是整条链路。
    try:
        _pl = json.loads(task.get("payload") or "{}")
    except Exception:
        _pl = {}
    _vh = _pl.get("cached_verdict")
    if isinstance(_vh, dict) and (_vh.get("md") or _vh.get("verdict")):
        _st = {}
        try:
            _st = json.loads(_vh.get("stats_json") or "{}")
        except Exception:
            _st = {}
        return json.dumps({"title": _vh.get("name") or "商品分析",
                           "stats": _st, "md": _vh.get("md") or ""},
                          ensure_ascii=False)

    stats = None
    if task.get("cache_hit"):
        cached = _db.cache_get(product_key)
        if cached:
            try:
                stats = json.loads(cached["stats_json"] or "{}")
            except Exception:
                stats = None

    if stats is None:
        stats = collect_product_by_url(product_key, timeout=240)
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

    verdict = _llm_product_verdict(task.get("tenant_key", ""), stats)
    title = stats.get("name") or "商品分析"
    # ★ 写结论缓存（7 天）：同租户同商品直接复用，不重复调 LLM
    try:
        _db.verdict_cache_put(task.get("tenant_key", ""), product_key,
                              name=title,
                              verdict=_verdict_label(verdict),
                              why=_verdict_reason(verdict),
                              md=verdict, stats=stats)
    except Exception:
        log.warning("verdict cache put failed: key=%s", product_key, exc_info=True)
    # ★ 聊天即知识（2026-10-27）：新算出的选品结论 → 知识库 match_pattern
    #   （只有新结论才到这；缓存命中分支在前面早退，不会重复推。尽力而为。）
    try:
        from . import reflow as _reflow
        _reflow.push_product_verdict(task.get("tenant_key", ""), title, verdict)
    except Exception:
        log.warning("verdict kb push failed (non-fatal)", exc_info=True)
    # 结构化信封：worker 拆包后通知（卡片/文字版用 md+title，表格用 stats）
    return json.dumps({"title": title, "stats": stats, "md": verdict},
                      ensure_ascii=False)


def _verdict_lines(md: str):
    """拆出「结论行」和「理由行」。

    ⚠️ 不能拿「前 60 字里搜第一个『能带』」当结论标签 ——
       理由句里也可能出现「能带」二字（比如「不能带」/「建议能带」），
       会把「🟡 慎带」误标成「能带」，写进档案后审计就废了。
       这里只认「## 结论」标题后的第一行（没有标题就用第一行）。
    """
    lines = [ln.strip() for ln in (md or "").splitlines() if ln.strip()]
    if not lines:
        return "", ""
    idx = 0
    for i, ln in enumerate(lines):
        if ln.startswith("#") and "结论" in ln:
            idx = i + 1
            break
    concl = lines[idx] if idx < len(lines) else ""
    reason = lines[idx + 1] if idx + 1 < len(lines) else ""
    return concl, reason


# 结论 emoji ↔ 标签的唯一映射（prompt 锁死了这三个写法，见 PRODUCT_VERDICT_PROMPT）
_VERDICT_EMOJI = {"🟢": "能带", "🟡": "慎带", "🔴": "别带"}


def _verdict_label(md: str) -> str:
    """从结论行里取「能带/慎带/别带」标签。

    ★ **先认 emoji，再认整词** ——
      emoji 是唯一无歧义的锚点（prompt 要求结论行只写「🟢 能带」三选一）；
      旧实现是「按固定顺序在结论行里 in 匹配」，会被
      「不能带」「未达到8%的能带标准」这类词带偏
      （实测踩过：真结论「🟡 慎带」被判成「能带」）。
      整词判定还能挡住「不能带」被当成「能带」。
    """
    concl, _ = _verdict_lines(md)
    if not concl:
        return ""
    for e, v in _VERDICT_EMOJI.items():
        if e in concl:
            return v
    tokens = _re.split(r"[\s,，。;；:：、/|()（）\[\]【】\-—·•]+", concl)
    for v in ("别带", "慎带", "能带"):
        if v in tokens:
            return v
    return ""


def _verdict_reason(md: str) -> str:
    """取结论的理由句（结论行的下一行）。"""
    _, reason = _verdict_lines(md)
    return reason[:120]


PRODUCT_VERDICT_PROMPT = '''你是抖音电商达人的AI选品顾问。根据【商品信息】和【该达人的账号数据】判断这个商品适不适合这个达人带货，输出 Markdown（不要输出其他内容）：

## 结论
第一行只写「🟢 能带」「🟡 慎带」「🔴 别带」其中一个（不要写其他的），第二行用一句话说明理由（不超过30字）

## 判定标准（按顺序判，命中即用）
- 🟢 能带：①商品类目与该达人主营带货类目一致或高度相关 ②价格落在该达人粉丝消费力主力区间内
  ③佣金率 ≥ 8%。**三条同时成立就是能带** —— 不要因为"数据还不够多"就降级。
- 🟡 慎带：只满足其中 1-2 条，或有明确次要冲突（类目弱相关 / 价格带偏低 / 佣金一般）。
- 🔴 别带：类目明显不相关，或价格明显超出粉丝消费力区间（≥2 倍），或佣金率 < 5%。

## 匹配分析
- 价格匹配：商品价格 vs 该达人粉丝消费力（引用具体数字，如"¥100-200 段占 26%"）
- 人群匹配：商品目标人群 vs 该达人粉丝画像（性别/年龄/地域），引用具体数字
- 佣金评估：佣金率是否有吸引力（没有佣金数据就明说没有，不要编）
- 类目匹配：商品类目 vs 该达人带货类目分布

## 行动建议
- 2-3条具体可执行的建议（怎么讲卖点、适合什么内容形式）

规则：
1. 有数据必须引用数据；没有的维度明说"没有数据"，绝对不要编造数字。
2. **不要因为"某个维度没数据"就把结论降级成慎带** —— 缺哪个维度就在分析里写明"该维度无数据"，
   结论照常按其余维度判。只有当**多个关键维度同时缺失**（商品信息里价格、佣金、类目几乎全空）时才用「🟡 慎带」。
3. 结论必须**明确且可复现**：同样的商品和同样的达人数据，要给出同样的结论。
   不要用"可以先试试""建议小量测试"这类模糊表述代替明确的能带/慎带/别带。
4. **信号权重**：达人的「带货类目分布」是最强信号（占比压倒性的类目就是ta的带货主场），
   优先于性别/年龄这类单点分布。类目主场命中时，不要仅凭"男性粉丝偏多"就否决。
5. **佣金口径（2026-10-28 老大拍板）**：【商品信息】里的佣金率是**平台公开佣金**，
   不是达人后台的「专属佣金」（商家单独给这个达人的，平台接口**看不到**，只有达人自己知道）。
   所以：当「佣金率低」是你判 🟡 慎带 / 🔴 别带 的**重要理由**时，
   「## 行动建议」里**必须**加一条（原话）：
   「这个品你后台如果有商家给的专属佣金，把百分比数字发我，我按你的专属佣金重新算一版」。
   达人报了专属佣金后系统会自动重算，你不用在本次输出里做任何预留。'''


def _llm_product_verdict(tenant_key: str, stats: dict) -> str:
    """商品 + 达人画像 → 「能带/慎带/别带」结论。

    ★ 函数名带 `_llm_` 前缀是有原因的（2026-10-24 实测事故）：
      这个模块里**曾经**还有一个同名 `_product_verdict(report)`（卡片取结论用的），
      Python「后定义覆盖先定义」⇒ 后者把本函数**整个遮蔽**了，
      于是 933/1252 两行 `_llm_product_verdict(tenant_key, stats)` 调用
      直接 TypeError ⇒ **达人发任何没缓存过的商品 ID 或截图，商品分析 100% 失败**
      （而缓存命中的路径走早返回，所以一直没被发现）。
      ⇒ 同名函数必须改名；并有 `tests/test_no_duplicate_defs.py` 兜底。

    ⚠️ 2026-10-21 三处修正（老大质疑「AI 判断能力到底是不是对的」的产物）：
      ① **模型名**：原来写死 `model='glm-4-flash'` —— 智谱 2026-10-18 就被摘除了，
         这个模型名根本不在任何降级环里（靠软偏好落到 MiniMax-M3）。
         请求一个已下线的模型 = 语义僵尸，改成不指定（用环默认模型）。
      ② **温度**：原来吃环默认 temp=1.0（kimi coding 端点的硬要求），
         而「出结论」需要可复现 → 显式传 temp=0.2。
         实测 temp=1.0 时同一商品（同义输入）结论在「能带/慎带」之间飘。
      ③ **失败不伪装成结论**：LLM 两次都失败时原来返回「🟡 慎带（AI 分析暂时没出结果）」
         —— 这会把「分析没跑」记成「慎带」，污染结论数据、让审计失真。
         现在明确写成「未出结果」，不占结论位。
    """
    ctx = _talent_context(tenant_key)
    # ★ 数据形态契约 ↔ prompt 呈现对齐：stats 是给前端/缓存用的**完整结构**，
    #   但直接整包塞给 AI 会出事 —— sale_axis 是 30 天日销数组，
    #   整包进去既烧 token 又把结论带偏（AI 会去数明细）。
    #   这里只给 AI 看该看的：字段值 + 销量曲线压缩成一行摘要。
    _skip = {"resolved_url", "main_img", "promotion_id", "sale_axis", "total"}
    lines = [f"- {k}：{v}" for k, v in stats.items()
             if v and k not in _skip]
    axis = stats.get("sale_axis") or []
    if isinstance(axis, list) and axis:
        try:
            qty = [float(x.get("qty") or 0) for x in axis if isinstance(x, dict)]
            if qty:
                lines.append(f"- 近{len(qty)}天日销：最高{max(qty):g}、"
                             f"最低{min(qty):g}、合计{sum(qty):g}")
        except Exception:
            pass
    prompt = (PRODUCT_VERDICT_PROMPT
              + "\n【商品信息】\n" + ("\n".join(lines) if lines else "(空)")
              + ("\n【该达人的账号数据】" + ctx if ctx else "\n【该达人的账号数据】暂无"))
    content = ""
    for attempt in (1, 2):
        try:
            content = _qa_llm(prompt, temp=0.2)
        except Exception as e:
            log.warning("product verdict attempt %d failed: %s", attempt, e)
            content = ""
        if content.strip():
            break
    ans = content.strip()
    if not ans:
        # ⚠️ 不要在这里编一个「慎带」出来 —— 那会把「没分析成」记成「慎带」。
        return ("## 结论\n（AI 分析未出结果，暂不给结论）\n\n"
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


def _img_mime(b64: str) -> str:
    """按 base64 载荷的 magic bytes 判真实 MIME。

    ⚠️ 原实现硬写 'data:image/jpeg;base64,' —— 但商家上传的截图可能是 PNG/WebP。
    MiniMax 容忍（实测 PNG 标 jpeg 也能读出来），但严格的多模态后端会拒收
    或错判格式 → 别靠「对方宽容」当正确性。
    """
    try:
        raw = base64.b64decode(b64[:64] + '==', validate=False)
    except Exception:
        return 'image/jpeg'
    if raw.startswith(b'\x89PNG'):
        return 'image/png'
    if raw.startswith(b'RIFF') and raw[8:12] == b'WEBP':
        return 'image/webp'
    if raw.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if raw.startswith(b'GIF8'):
        return 'image/gif'
    return 'image/jpeg'


def _vision_call(base: str, key: str, model: str, b64: str,
                 temperature: float, prompt_text: str):
    """vision 环的 HTTP 调用：请求 + base_resp 检查 + 取 content 原文。
    返回 content 文本（未做 JSON 解析 —— 解析是调用方的事）。"""
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model,
            'messages': [{'role': 'user', 'content': [
                {'type': 'text', 'text': prompt_text},
                {'type': 'image_url',
                 'image_url': {'url': f'data:{_img_mime(b64)};base64,{b64}'}},
            ]}],
            'temperature': temperature,
            'max_tokens': 1500,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json',
                 'Authorization': f'Bearer {key}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    # ⚠️ 同款「HTTP 200 ≠ 成功」：MiniMax 会在 body 里塞 base_resp.status_code
    #   （1008 insufficient balance 等）而状态码仍是 200 —— 只看 HTTP 会把它
    #   当成成功，然后解析出一个空 content → 报"没识别出商品"，把余额问题
    #   伪装成识别问题，排查方向直接跑偏。
    bresp = data.get('base_resp') or {}
    if bresp.get('status_code'):
        raise RuntimeError(str(bresp.get('status_msg') or bresp.get('status_code')))
    return (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''


def _vision_try(base: str, key: str, model: str, b64: str,
                temperature: float = 0.1,
                prompt_text=None) -> dict:
    """OpenAI 协议的 vision 调用，返回提取字段（无 name 返回 {}）。
    kimi coding 端点限 temperature=1。
    prompt_text: 默认取 VISION_PRODUCT_PROMPT（商品字段提取）；传别的即通用截图问答。"""
    content = _vision_call(base, key, model, b64, temperature,
                           prompt_text or VISION_PRODUCT_PROMPT)
    return _extract_json_obj(_strip_think(content))


# 套餐 key 走 OpenAI 协议的 vision base（2026-10-21 实测 MiniMax-M3 能读图）
CP_VISION_BASE = 'https://api.minimaxi.com/v1'
CP_VISION_MODELS = ('MiniMax-M3', 'MiniMax-M3.1-Flash')


def _vision_rings() -> list:
    """vision 降级环（按凭据稳定性排）。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（**唯一事实来源**）。
    保留本函数名做薄壳。

    ⚠️ 2026-10-21：vision 链曾**全灭**（kimi 429 月度上限 + 开放平台 402 余额 0），
    而「商家发商品图」是招商减负方案的地基（不依赖团长端浏览器登录态）——
    链断了整条路就废了。所以套餐 key（唯一有额度的长期凭据）必须排在最前。
    """
    return _vision_rings_impl()


def _vision_extract_product(b64: str) -> dict:
    """商品截图 → vision 结构化字段。降级环：套餐 key → kimi → minimax 开放平台。
    ★ 2026-10-18 老大拍板摘除智谱（余额耗尽 429/1113）。
    ★ 2026-10-21 套餐 key 提到环 0（唯一有额度的长期凭据；kimi 429 / 开放平台 402）。"""
    rings = _vision_rings()
    if not rings:
        raise RuntimeError('AI 视觉暂时不可用（没有可用通道），可以换一张清晰截图，'
                           '或直接发商品链接')
    errors = []
    for idx, ring in enumerate(rings):
        try:
            d = _vision_try(ring['base'], ring['key'], ring['model'], b64,
                            temperature=ring['temp'])
            if d.get('name'):
                log.info("[vision] 命中环%d channel=%s model=%s base=%s",
                         idx, ring['label'], ring['model'], ring['base'])
                return d
            errors.append(f"{ring['label']} no-name output")
            log.warning("[vision] 环%d %s 没识别出商品名", idx, ring['label'])
        except Exception as e:
            errors.append(f"{ring['label']}: {str(e)[:80]}")
            log.warning("[vision] 环%d %s 失败: %s", idx, ring['label'], e)
    # 全链失败：错误信息必须可读 —— 原来只留最后一个错误，看不出是额度问题
    # 还是识别问题（kimi 429 / 402 会被伪装成"没识别出商品"）。
    raise RuntimeError('AI 视觉暂时不可用或没识别出商品（%s），可以换一张清晰截图，'
                       '或直接发商品链接' % ' | '.join(errors)[:200])


_IMG_MAX_EDGE = 1280   # ★ 2026-10-28：视觉调用前统一降采样（长边 1280px / JPEG q80）
                         #   ——达人常发手机原图（3-4MB），图像 token 比必要值高数倍；
                         #   压到 1280 成本降 5-10 倍，识别精度基本无损。


def _img_b64(path) -> str:
    """图片文件 → 降采样后的 JPEG base64（给视觉环用）。

    PIL 不可用或处理失败时回退原始字节（链路不因此断）。
    """
    raw = Path(path).read_bytes()
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(raw))
        im = im.convert("RGB")
        if max(im.size) > _IMG_MAX_EDGE:
            im.thumbnail((_IMG_MAX_EDGE, _IMG_MAX_EDGE))
        buf = BytesIO()
        im.save(buf, format="JPEG", quality=80)
        out = buf.getvalue()
        if out:
            raw = out
    except Exception:
        log.warning("image downscale failed, use original bytes: %s", path,
                    exc_info=True)
    return base64.b64encode(raw).decode()


VISION_DASHBOARD_PROMPT = '''你是抖音电商数据分析师。用户发来一张截图（可能是抖音电商罗盘/精选联盟/商品详情等后台页面）。
请结合截图里**真实可见**的数据回答用户的问题，输出 Markdown（不要输出其他内容）：
## 截图里有什么
- 用 3-5 条列出截图中可见的关键数据（数值照抄，看不清的写"看不清"，绝对不要编造）
## 解读
- 结合这些数据给出 2-4 条对带货有用的解读（趋势/结构/异常）
## 建议
- 1-3 条可执行的下一步动作
规则：只引用截图里明确可见的数字；截图与问题无关就直说"这张图里看不到能回答这个问题的数据"。'''


def _vision_dashboard_qa(b64: str, caption: str) -> str:
    """非商品截图（罗盘/数据页等）→ 通用截图问答（降级环与商品识别共用）。

    ★ 输出是 **Markdown 自由文本**（VISION_DASHBOARD_PROMPT 要求 md），
    不走 _vision_try 的 JSON 字段解析 —— 那是商品提取的协议，套在这里会把
    正常的 markdown 回答解析成 {} 误判「empty output」（2026-10-28 自测实案）。
    """
    rings = _vision_rings()
    if not rings:
        raise RuntimeError('AI 视觉暂时不可用（没有可用通道），可以换一张清晰截图再发')
    q = (caption.strip() or "帮我看看这张图里的数据")
    prompt = ("用户的问题：" + q + "\n\n" + VISION_DASHBOARD_PROMPT)
    errors = []
    for idx, ring in enumerate(rings):
        try:
            text = _strip_think(_vision_call(ring['base'], ring['key'],
                                             ring['model'], b64,
                                             ring['temp'], prompt)).strip()
            if text:
                log.info("[vision] 截图问答命中环%d channel=%s model=%s",
                         idx, ring['label'], ring['model'])
                return text
            errors.append(f"{ring['label']} empty output")
        except Exception as e:
            errors.append(f"{ring['label']}: {str(e)[:80]}")
            log.warning("[vision] 截图问答环%d %s 失败: %s", idx, ring['label'], e)
    raise RuntimeError('AI 视觉暂时不可用或没看懂这张图（%s），'
                       '可以换一张更清晰的截图，或直接用文字告诉我你想看什么'
                       % ' | '.join(errors)[:200])


def execute_image_vision(task: dict) -> str:
    """截图识别：商品截图 → vision 提取商品字段 → 「能带/慎带/别带」；
    非商品截图（罗盘/数据页）→ 通用截图问答（结合达人随图发的那句话）。

    废图（识别不出商品且问答也没结果）抛异常 → worker 自动返还积分。"""
    path = Path(task.get("target") or "")
    if not path.is_file():
        raise RuntimeError("截图文件没找到，重新发一下")
    caption = ""
    try:
        payload = json.loads(task.get("payload") or "{}")
        caption = str(payload.get("caption") or "")
    except Exception:
        pass
    b64 = _img_b64(path)
    try:
        stats = _vision_extract_product(b64)
    except Exception:
        log.info("[vision] 商品提取失败，转截图问答: task=%s", task.get("id"))
        answer = _vision_dashboard_qa(b64, caption)
        return json.dumps({"title": "截图分析",
                           "stats": {}, "md": answer,
                           "source": "截图问答"}, ensure_ascii=False)
    verdict = _llm_product_verdict(task.get("tenant_key", ""), stats)
    # 结构化信封：worker 拆包后通知（截图来源标记为「截图」）
    return json.dumps({"title": stats.get("name") or "商品分析",
                       "stats": stats, "md": verdict,
                       "source": "截图"}, ensure_ascii=False)


def execute_commission_recalc(task: dict) -> str:
    """专属佣金重算（2026-10-28 老大拍板）：平台接口只能查到公开佣金，
    商家给达人的专属佣金只有达人自己后台可见 ⇒ 结论若因佣金低被压，
    必须问达人专属佣金；达人报数后走到这里，用新佣金重出一版结论。

    payload = {"rate": 20.0, "stats": {...原商品字段...}}
    """
    try:
        payload = json.loads(task.get("payload") or "{}")
    except Exception:
        payload = {}
    rate = float(payload.get("rate") or 0)
    stats = payload.get("stats") or {}
    if not rate or not stats:
        raise RuntimeError("重算参数没带上，联系运营处理")
    rate_str = f"{rate:g}%"
    stats = dict(stats)
    stats["commission_rate"] = rate_str
    stats["commission"] = f"专属佣金 {rate_str}（达人后台口径）"
    verdict = _llm_product_verdict(task.get("tenant_key", ""), stats)
    return json.dumps({"title": stats.get("name") or "商品分析（专属佣金重算）",
                       "stats": stats, "md": verdict,
                       "source": "专属佣金重算"}, ensure_ascii=False)


def _send_text_quiet(sender, chat_id: str, text: str) -> None:
    """补发文字版消息；失败只记日志，绝不影响已发出的卡片。"""
    try:
        sender.send_text(chat_id, text)
    except Exception:
        log.exception("text version send failed: chat_id=%s", chat_id)


def _tenant_key_of_douyin(douyin_id: str) -> str:
    """抖音号 → 租户 key（取不到返回空串）。用于给卡片挂达人专属驾驶舱链接。"""
    try:
        from . import db as ddb
        tenant = ddb.get_tenant_by_douyin(douyin_id) or {}
        return tenant.get("tenant_key") or ""
    except Exception:
        return ""


def notify_report(sender, chat_id: str, douyin_id: str, report_text: str) -> None:
    """报告推送：卡片长图 + 一句话摘要 + 完整文字版（便于复制）。"""
    if sender is None:
        log.info("[notify-no-sender] douyin_id=%s", douyin_id)
        return
    try:
        from .report_card import render_card
        from . import cockpit_link
        t = _read_talent("", douyin_id)
        if t:
            import tempfile
            tmp = Path(tempfile.gettempdir()) / f"daren_card_{t['id']}.png"
            render_card(t["id"], douyin_id, str(tmp),
                        link=cockpit_link.tenant_cockpit_link(
                            _tenant_key_of_douyin(douyin_id)))
            sender.send_image_path(chat_id, str(tmp))
            name = t.get("name") or douyin_id
            verdict = t.get("risk_rating") or ""
            sender.send_text(
                chat_id,
                f"📊 {name} 的账号数据报告如上　🎯 账号评估：{verdict or '见报告'}")
            _send_text_quiet(sender, chat_id, report_text)
            # ★ 2026-10-27 老大拍板：档案层已换成 mini 做的新功能（达人经营驾驶舱），
            #   每租户「我的带货助手」多维表格（bitable_sync）链路废弃——
            #   新达人应用不再有此权限，sync 只会刷 400 噪音。整段停用，模块保留备查。
            # try:
            #     from . import bitable_sync, db as ddb
            #     tenant = ddb.get_tenant_by_douyin(douyin_id)
            #     if tenant:
            #         summary = (t.get("ai_summary") or t.get("notes") or "")[:2000]
            #         bitable_sync.sync_profile(
            #             tenant["tenant_key"], t,
            #             t.get("risk_rating") or "", summary,
            #             img_path=str(tmp))
            # except Exception:
            #     log.exception("bitable sync profile failed: douyin_id=%s", douyin_id)
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
        from . import cockpit_link
        import tempfile
        tmp = Path(tempfile.gettempdir()) / f"daren_qa_{abs(hash(question)) % 10**8}.png"
        render_qa_card(question, answer, _talent_name(tenant_key), str(tmp),
                       link=cockpit_link.tenant_cockpit_link(tenant_key))
        sender.send_image_path(chat_id, str(tmp))
        _send_text_quiet(sender, chat_id, f"💬 {answer}")
        # ★ 2026-10-27 档案层已换代（见上方 sync_profile 同因），问答日志同步一并停用。
        # try:
        #     from . import bitable_sync
        #     bitable_sync.sync_log(tenant_key, "问答", question, answer)
        # except Exception:
        #     log.exception("bitable sync log failed: tenant=%s", tenant_key)
        return
    except Exception:
        log.exception("render qa card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, answer)



# ---------------- 业务问答（spec ① 意图分类的问答类，1 积分/次） ----------------

# ⚠️ 2026-10-26：原 `_zhipu_cfg_daren()`（直连智谱的裸配置读取）**已删除** ——
#   它**从来没有被调用过**（全仓仅此定义处命中），却是「对话链悄悄依赖智谱」的
#   残留入口：留着它 = 给后人留一条绕过「智谱只做 embedding」口径的后门。
#   智谱的对话/识图环已在 `daren_bot/llm_channels.py` 摘除（老大 2026-10-26 拍板）。

def _qa_llm(prompt: str, model: str = '', temp: float = None) -> str:
    """问答直连文字模型链：Code 订阅 → kimi moonshot-v1-32k → minimax ×2。

    ★ 2026-10-18 老大拍板摘除智谱（余额耗尽 429/1113）。kimi coding 端点
    temperature 必须 1.0（reasoning 模型），max_tokens 给足防推理 token 吞空。

    ★ 2026-10-21 新增环 0「Code 订阅通道」：老大买的是 **Code 订阅**（M Plan），
    额度不走开放平台余额 —— 老大给的 sk-api-… 在 api.minimaxi.com 报 402 insufficient
    balance，但 Code 订阅的 OAuth accessToken 在 agent.minimax.cn 走 **Anthropic 协议**
    `/messages` 是通的（同一 base 的 `/chat/completions` 返 503 → 协议不能混用）。
    放环 0 的理由：kimi 429 / minimax 402 两条老链目前全灭，先打它们每次问答
    都要白等两轮 HTTP 失败，飞书侧体感是"卡十几秒才回一句没反应"。

    `model` 参数是**软偏好**（旧代码里被 for 循环变量同名遮蔽，等于一直没生效）：
    该通道支持就用它，不支持就用通道默认模型并记一行日志，不再静默丢弃。

    ⚠️ 2026-10-21 新增 `temp` 参数：**出结论类任务必须低温**。
    原来所有环固定 temp=1.0（kimi coding 端点的硬要求），
    而选品结论（能带/慎带/别带）也吃 temp=1.0 →
    同义输入实测结论会在「能带/慎带」之间飘（"商品偏女性" vs "佣金可观"，
    结论一个能带一个慎带）→ **同一个商品给用户两个答案**。
    temp=0.0 时用通道默认值（kimi 仍 1.0），其余通道按传入值。
    """
    rings = _fallback_rings_daren(model)
    if not rings:
        # ⚠️ 全链被跳过时，遍历循环根本不会执行 → 错误信息会退化成
        #   「问答降级链全灭: 」(后面什么都没有)，运维看着毫无线索。
        #   这里显式写出每种可能性（细节看 logs 的 [fallback] 警告行）。
        raise RuntimeError('问答降级链无可用通道：套餐 API key（.env MINIMAX_CP_API_KEY）'
                           '缺失、Code 订阅凭据缺失/已过期、kimi 与 minimax 无可用 key'
                           '（详见日志 [fallback] 警告）')
    errors = []
    for idx, ring in enumerate(rings):
        try:
            # 出结论类任务用低温求可复现；kimi coding 端点强制 temp=1.0，不动它
            if temp is not None and 'moonshot' not in (ring.get('model') or ''):
                ring = dict(ring, temp=temp)
            if ring['protocol'] == 'anthropic':
                out = _call_anthropic(ring, prompt)
            else:
                out = _call_openai(ring, prompt)
            if out.strip():
                # ⚠️ 可观测：命中哪一环必须写进日志 —— 否则「Code 环失灵、静默降级到
                #   kimi」这类故障在日志里完全看不出来（跳过警告只在该环被跳过时才打）。
                log.info("[fallback] 命中环%d protocol=%s model=%s base=%s",
                         idx, ring['protocol'], ring['model'], ring['base'])
                return out
            errors.append(f"{ring['model']} empty content")
        except Exception as e:
            errors.append(f"{ring['model']}: {str(e)[:100]}")
    raise RuntimeError('问答降级链全灭: ' + ' | '.join(errors))


def _call_openai(ring: dict, prompt: str) -> str:
    """OpenAI 协议环：POST {base}/chat/completions。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    保留本函数名做薄壳 —— `probe_llm_channels.py` 等外部调用方按名引用。
    """
    return _call_openai_impl(ring, prompt)


def _call_anthropic(ring: dict, prompt: str) -> str:
    """Anthropic 协议环：POST {base}/messages（Code 订阅通道用这个）。

    ⚠️ 401 特判：token 是 30 分钟量级的短期凭据，读盘到发请求之间可能刚好过期
    （Code 桌面端续期后重写 auth.json）→ 401 时重读一次凭据再重试，
    而不是直接判定该环失败。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    保留本函数名做薄壳 —— 外部调用方按名引用。
    """
    return _call_anthropic_impl(ring, prompt)


def _anthropic_messages(base: str, key: str, model: str, temp: float,
                        prompt: str, auth: str = 'bearer',
                        max_tokens: int = 2000) -> str:
    """Anthropic /messages 单次调用。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    本函数保留为薄壳。

    `auth` 两种形态（**实测两种都通，但不能靠猜**）：
      'x-api-key' → 头 `x-api-key: <key>`（Anthropic 规范形态，套餐 API key 用这个）
      'bearer'    → 头 `Authorization: Bearer <key>`（Code OAuth token 用这个）
    """
    return _anthropic_messages_impl(base, key, model, temp, prompt,
                                    auth=auth, max_tokens=max_tokens)


def _strip_think(text: str) -> str:
    """剥掉 OpenAI 协议返回里混入的 <think>…</think> 推理块。

    ⚠️ 2026-10-22 修 bug：旧实现是 `out.strip() or text.strip()`，
    在「整个 content 都是 think 块」（M3 有时把最终草稿也写进 think 里）时
    out 为空 → 兜底把**原文**还回去 = 根本没剥 → 英文思维链直接进 notes/飞书摘要。
    唯一实现搬到 `daren_bot/llm_text.py`（buyin_worker 那边也用同一份）。
    """
    return _strip_think_impl(text)


def _openclaw_api_key(provider: str) -> str:
    """从 OpenClaw 拿 provider 的**真** API key。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    保留本函数名做薄壳 —— 外部调用方按名引用。

    ⚠️ 历史坑（详见 llm_channels 同名函数注释）：openclaw.json 里的 apiKey
    是**引用形式** dict，不是密钥本身 —— 真值在 models.json 里。
    老代码把 dict 当字符串用 → 必然 401 → 该环从来没真正工作过。
    """
    return _openclaw_api_key_impl(provider)

def _env_api_key(name: str) -> str:
    """从项目根 .env 读 key（绝对路径，避开 CWD 依赖）。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    保留本函数名做薄壳。

    ⚠️ 密钥一律只进 .env（已 gitignore, 600），代码里只读不算
    —— 与 AGENTS.md §7 密钥管理铁律配套。
    """
    return _env_api_key_impl(name)

def _code_sub_token() -> str:
    """取 MiniMax Code 订阅的 OAuth accessToken（**每次调用实时读盘，绝不缓存**）。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（唯一事实来源）。
    保留本函数名做薄壳。

    ⚠️ 为什么不能缓存：token 是 30 分钟量级的短期凭据，Code 桌面端会自行
    续期并**重写 auth.json**（旧 token 随之作废）。缓存下来的 token 会在
    下一次问答时变成 401 —— 表现是"刚开始还能用、跑一会儿就全灭"，
    排查时极容易误判成"额度又没了"。
    """
    return _code_sub_token_impl()

def _fallback_rings_daren(requested_model: str = '') -> list:
    """问答降级环（按优先级从高到低）。

    ★ 2026-10-22：实现搬到 `daren_bot/llm_channels.py`（**唯一事实来源**）。
    保留本函数名做薄壳 —— `probe_llm_channels.py` 等外部调用方按名引用。

    每环字段：
      protocol : 'openai' → POST {base}/chat/completions
                 'anthropic' → POST {base}/messages
      base/key/model/temp/models : 通道参数
      auth     : 'x-api-key'（套餐 API key）/ 'bearer'（Code OAuth token）
      refresh  : 是否允许 401 后重读凭据重试（Code 通道为 True）

    ⚠️ 历史（2026-10-21 → 2026-10-22）：
      同名函数 `_fallback_cfgs_daren` 已下线 —— 它只能表达 OpenAI 协议的 4 元组，
      装不下 Code 订阅这条 Anthropic 协议环。
      `buyin_worker.py` 里的 `_text_fallback_cfgs` 曾是**另一份独立拷贝**，
      且**只支持 OpenAI 协议** → 采集 / 报告摘要链拿不到「贾维斯自己」那条环。
      现已两份共用 `daren_bot/llm_channels.py`，同源同 bug 从根上消除。
    """
    return _text_rings_impl(requested_model)

def _recent_product_ref_re():
    """指代词判据（惰性编译）：达人在追问上文那个商品。"""
    import re as _re
    return _re.compile(
        r"这个品|这款|那款|该商品|这个商品|那个商品|这个产品|这款产品|"
        r"这个宝贝|这个货|那个品|本品|它|刚才那个|上面那个|之前那个")


def _recent_product_block(tk: str, q: str) -> str:
    """上文商品语境块：达人发过商品链接/ID/截图分析过后，追问「这个品/这款/它」
    时注入 QA prompt —— 让 AI 承接上文，而不是再要一遍商品ID
    （2026-10-28 老大实锤：「上面已经给到了商品链接，下面问道其它问题又要给商品ID」）。

    数据来源（先到先用）：
      ① bot_commission_pending（24h 内分析过的商品 stats，字段最全）
      ② bot_product_verdicts 最近一条（7 天内结论档案：名称/结论/理由）
      ③ bot_collect_tasks 24h 内 done 的截图任务（2026-10-29 质检实锤：
        达人发罗盘/截图问答后追问碎片词，前两条都可能没有 —— 截图分析
        不落 pending 也不写结论档案，但达人说的「这个品」就是截图那个）
    返回空串 = 没有可承接的上下文。
    """
    if not _recent_product_ref_re().search(q or ""):
        return ""
    from . import db
    try:
        pending = db.get_commission_pending(tk)
    except Exception:
        pending = None
    if pending and pending.get("stats"):
        s = pending["stats"]
        parts = [f"商品：{s.get('name') or '（名称未知）'}"]
        for label, key in (("到手价", "price"), ("佣金", "commission"),
                           ("佣金率", "commission_rate"),
                           ("月销", "monthly_sales"), ("店铺分", "shop_score"),
                           ("类目", "category")):
            v = s.get(key)
            if v not in (None, "", {}):
                parts.append(f"{label} {v}")
        return "｜".join(parts)[:300]
    try:
        v = db.get_latest_product_verdict(tk)
    except Exception:
        v = None
    if v:
        parts = [f"商品：{v.get('name') or '（名称未知）'}"]
        if v.get("verdict"):
            parts.append(f"结论 {v['verdict']}")
        why = (v.get("why") or "").strip()
        if why:
            parts.append(why[:150])
        return "｜".join(parts)[:300]
    try:
        img = db.get_recent_done_image_task(tk)
    except Exception:
        img = None
    if img:
        caption = ""
        try:
            payload = json.loads(img.get("payload") or "{}")
            caption = (payload.get("caption") or "").strip()
        except Exception:
            pass
        parts = ["上文：达人 24 小时内发过商品截图，已完成识别分析"]
        if caption:
            parts.append(f"截图附言「{caption}」")
        parts.append("追问里的「这个品/它」指截图中的商品，承接上文回答，"
                     "不要再索要商品链接或商品ID")
        return "｜".join(parts)[:300]
    return ""


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
    except Exception as e:
        # ⚠️ 2026-10-21：这里原来**静默 return ""** —— 上游 sqlite mode=ro 打不开
        #   WAL 库的异常被吞掉 → AI 选品判断和 AI 问答拿不到任何达人画像，
        #   **等于盲判**，且全程无日志 → 没人发现。
        #   上下文为空是**异常状态**，不是正常情况，必须让人看得见。
        log.warning("talent_context 读取失败 tenant=%s: %s", tk, e)
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
        conn = _ro_conn()
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


# ★ 2026-10-28 飞书助理平台化：商务问答的「查达人」档案注入。
# 问题里出现的 5-24 位 alnum 串（抖音号形态）逐个查库，命中档案就注入真实摘要。
_DYID_TOKEN_RE = _re.compile(r"[A-Za-z0-9_]{5,24}")
_COMMERCE_TALENT_MAX = 3   # 一次问答最多注入几个达人档案（防 prompt 爆炸）
_COMMERCE_LOOKUP_MAX = 5   # 最多尝试查库几次（防长句里英文词浪费查询）


def _commerce_talent_block(q: str) -> str:
    """商务问答：问题里点到的抖音号 → 该达人的真实档案摘要（≤3 个）。

    查达人数据是商务的本职工作（老大 2026-10-28：商务需要达人分析、查达人数据）。
    查不到档案的号不注入（prompt 指令会要求 AI 明说没有数据，严禁编造）。"""
    seen: set = set()
    out = []
    lookups = 0
    for m in _DYID_TOKEN_RE.finditer(q or ""):
        dyid = m.group(0)
        if dyid in seen:
            continue
        seen.add(dyid)
        lookups += 1
        if lookups > _COMMERCE_LOOKUP_MAX:
            break
        try:
            t = _read_talent("", dyid)
        except Exception:
            log.warning("commerce talent lookup failed dyid=%s", dyid,
                        exc_info=True)
            t = None
        if t:
            out.append(f"【达人 {dyid}】" + _fmt_compare_brief(t))
        if len(out) >= _COMMERCE_TALENT_MAX:
            break
    return "\n".join(out)[:900]


def execute_qa(task: dict) -> str:
    """业务问答：结合该达人绑定的账号画像回答；超出带货范围由模型拒答。
    对报告/分析的质疑类问题强制走回答路径（弱模型爱把质疑当无关问题拒答）。"""
    import re as _re
    from . import db as _db
    q = (task.get("target") or "").strip()
    if not q:
        return "没收到问题内容，重新发一下～"
    # ★ 2026-10-21 复用层：入队时已判定缓存命中 → 直接回缓存答案，**不调 LLM**。
    #   （判定必须在入队处做 —— 到这里才判就晚了，LLM 已经烧掉了）
    try:
        _pl = json.loads(task.get("payload") or "{}")
    except Exception:
        _pl = {}
    if _pl.get("cached_answer"):
        return _pl["cached_answer"]
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
                from . import credits as _credits
                # ★ 2026-10-25 老大：「每一个功能都需要积分控制 毕竟是消耗算力」——
                #   重采 = 又开一次浏览器采集 ⇒ 消耗算力 ⇒ 必须过积分闸门。
                #   （原来这里 cost=0 且话术写「已免费安排」⇒ 一直白干）
                # ★ 扣费+入队一个原子操作 ⇒ 幂等命中不重复扣分。
                _st, _tid, _bal = _credits.charge_and_enqueue(
                    task["tenant_key"], "douyin", _p['dyid'], "recollect",
                    chat_id=task["chat_id"])
                if _st == "insufficient":
                    return (f"你说得对，{_p['name']}（{_p['dyid']}）这次确实没采集到有效数据。"
                            f"重新采集需要 {config.COST_RECOLLECT} 积分，"
                            "你的余额不够了，联系运营老师补充额度我再帮你重采～")
                if _st == "duplicate":
                    return (f"你说得对，{_p['name']}（{_p['dyid']}）这次确实没采集到有效数据。"
                            f"已经安排了重新采集（扣 {config.COST_RECOLLECT} 积分，余额 {_bal}），"
                            "报告出来直接发你～")
                return (f"你说得对，{_p['name']}（{_p['dyid']}）这次没有采集到有效数据，"
                        "是我这边采集失误。已安排重新采集"
                        f"（扣 {config.COST_RECOLLECT} 积分，余额 {_bal}），"
                        "报告出来直接发你～")
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
    # ★ 2026-10-28 上文承接：达人前面发过商品链接/ID/截图并分析过，
    #   这句「这个品/这款/它…」指的就是那个商品 —— 把商品块注入语境，
    #   绝对禁止再要商品ID（老大实锤：再给一遍ID = 没记住上文，体验崩）。
    recent_product = _recent_product_block(task.get("tenant_key", ""), q)
    recent_line = (
        '【上文语境】达人刚发过这个商品的分析，TA 这句问的「这个品/这款/它」'
        '就是指下面这个商品。基于它回答，**绝对禁止再要商品ID或链接**；'
        '商品块里没有的维度，明说「这个维度我这边没有数据」，严禁编造。\n'
        + recent_product + '\n' if recent_product else '')
    # ★ 2026-10-28 飞书助理平台化：商务租户问答用商务人格 + 查达人档案注入
    _role = ""
    try:
        _tenant_row = _db.get_tenant(task.get("tenant_key", ""))
        _role = (_tenant_row or {}).get("role") or ""
    except Exception:
        _role = ""
    # 「本人账号画像」是达人语境：商务没有自己的号，注入反而误导
    if _role == "commerce":
        ctx = ""
    commerce_block = ""
    if _role == "commerce":
        commerce_block = _commerce_talent_block(q)
    commerce_line = (
        '【系统指令】提问的是商务人员，这是TA问题里点到的达人的真实档案数据。'
        '必须基于这些数据回答，绝对禁止编造粉丝数/GMV/佣金/类目等数字；'
        '档案里没有的维度明说「这个维度我这边没有数据」。'
        '如果问题点到的达人没有档案数据，直接说「这个达人还没有档案，'
        '发我TA的抖音号，我采集分析完再给你完整数据」。\n'
        + commerce_block + '\n' if commerce_block else '')
    compare_line = (
        '【系统指令】达人在要求做账号对比，这是核心业务问题，必须基于下面的对比数据回答，'
        '绝对禁止输出"拒"。回答结构：先并排列出各方核心数据（粉丝/类目/GMV/GPM/等级/城市/粉丝画像），'
        '再指出达人自己账号的优势和差距，最后给可执行建议。'
        '对比数据里没有的维度，明说"对方该维度暂无数据"，严禁编造数字。\n'
        + compare_block + '\n' if compare_block else '')
    prompt = (
        # ★ 人格唯一来源 personas/<role>.md（角色模板：新租户自动继承，纠正沉淀共享）
        persona.qa_persona(_role) + '\n'
        + force_line
        + recent_line
        + commerce_line
        + compare_line
        + (f'【该达人的账号数据】{ctx}\n' if ctx else '')
        + (f'【商务问】{q}' if _role == "commerce" else f'【达人问】{q}')
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
    # ★ 聊天即训练：达人这句话是在纠正/教 AI ⇒ 原文进知识库（talent_feedback 白名单，
    #   服务端价值闸把关），定期沉淀回 persona.md。尽力而为，绝不阻塞回复。
    try:
        persona.capture_feedback(task.get("tenant_key", ""), q, ans)
        # ★ 聊天即知识：有实质内容的问答整段进知识库（talent_insight，pending 人工审）
        from . import reflow as _reflow
        _reflow.push_qa_exchange(task.get("tenant_key", ""), q, ans)
    except Exception:
        log.warning("kb push failed (non-fatal)", exc_info=True)
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
                        from . import credits as _credits
                        # ★ 同上：重采消耗算力 ⇒ 过积分闸门（扣费+入队原子）
                        _st, _tid, _bal = _credits.charge_and_enqueue(
                            task["tenant_key"], "douyin", p['dyid'], "recollect",
                            chat_id=chat_id)
                        if _st == "insufficient":
                            return (f"你说得对，{p['name']}（{p['dyid']}）这次确实没采集到有效数据。"
                                    f"重新采集需要 {config.COST_RECOLLECT} 积分，"
                                    "你的余额不够了，联系运营老师补充额度我再帮你重采～")
                        if _st == "duplicate":
                            return (f"你说得对，{p['name']}（{p['dyid']}）这次确实没采集到有效数据。"
                                    f"已经安排了重新采集（扣 {config.COST_RECOLLECT} 积分，"
                                    f"余额 {_bal}），报告出来直接发你～")
                        return (f"你说得对，{p['name']}（{p['dyid']}）这次没有采集到有效数据，"
                                "是我这边采集失误。已安排重新采集"
                                f"（扣 {config.COST_RECOLLECT} 积分，余额 {_bal}），"
                                "报告出来直接发你～")
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
        # ★ 2026-10-22（老大截图实锤）：模型拒答时，先分辨是「答不了」还是「缺一个输入」。
        #   「这款商品能不能带」而达人没给链接/ID ⇒ 不是无关问题，是**缺商品标识**，
        #   要的是「请提供链接或商品ID」，不能甩「其他问题帮不上忙」的通用兜底。
        if product_intent.refusal_needs_product_ref(q):
            return product_intent.NEED_PRODUCT_REF_REPLY
        return ("我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，"
                "其他问题帮不上忙哦～")
    # 问题提到了具体商品/视频但这条消息没带链接或截图（带了会走别的路由），
    # 说明 AI 没看到实物，提醒一句避免达人误以为 AI 已经分析过那个商品
    if any(k in q for k in ("视频", "商品", "产品", "链接", "这条", "这个品", "这款", "那个品")):
        ans += ("\n\n⚠️ 这条消息里没有附商品链接、商品ID 或商品截图，"
                "以上是按你的账号画像给的通用建议；把链接、商品ID 或截图发我，"
                "我可以针对具体商品分析～")
    ans = ans[:900]
    # ★ 写回复缓存（1 天内同一问题直接复用，不重复调 LLM）
    #   不缓存拒答/兜底话术 —— 那是失败态，不是答案
    try:
        if ans and not ans.startswith(("拒", "我是你的带货选品助手", "AI 刚才没反应过来",
                                       "这个动作需要", "积分余额不足")):
            _db.qa_cache_put(task.get("tenant_key", ""), q, ans)
    except Exception:
        log.warning("qa cache put failed", exc_info=True)
    return ans


# ---------------- 视频链接拆解（teardown_url：下载→转写→画面→报告） ----------------

TEARDOWN_URL_API = "http://127.0.0.1:8095/teardown_url"
VIDEO_POLL_TIMEOUT = 15 * 60       # 妙记转写可能要几分钟，最多等 15 分钟
VIDEO_POLL_INTERVAL = 10


def _video_job_result(job_id: str):
    """读 video_jobs/talent_videos → (status, title, 报告md原文, error)。"""
    conn = _ro_conn()
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


def _send_accept_hint(sender, chat_id: str, product_id: str = "") -> None:
    """商品分析后补一句**接品引导**（2026-10-24）。

    达人发**裸商品ID** 的语义是「这商品怎么样」（查），
    但他很可能也想要「把它接进排品表」（接）——
    两条链路长得一模一样，不给引导，达人永远不知道「接了 xxx」这个入口存在。
    """
    if sender is None or not product_id:
        return
    try:
        sender.send_text(
            chat_id,
            f"想把它接进排品表（AI 帮你排哪天推、每天早上提醒你），"
            f"回我一句「接了 {product_id}」就行～")
    except Exception:
        log.exception("send accept hint failed: chat=%s", chat_id)


def _card_verdict(report: str):
    """从长报告里抽「结论」（能带/慎带/别带）→ (文案, 卡片配色)。

    ★ **必须复用档案口径 `_verdict_label`**（同一份事实解析只有一个真源）：
      旧实现自己扫一遍「能带/慎带/别带」三个词，且固定顺序先判「能带」，
      于是报告理由句里的「未达到8%的能带标准」会把真结论「🟡 慎带」
      误标成「🟢 能带」+ 绿色 ⇒ **达人看到的结论是反的**（该慎带的品标成能带）。
    """
    v = _verdict_label(report)
    tpl = {"能带": "green", "慎带": "orange", "别带": "red"}.get(v, "grey")
    emoji = {"能带": "🟢", "慎带": "🟡", "别带": "🔴"}.get(v, "⚪")
    return (f"{emoji} {v}" if v else ""), tpl


def _card_summary(report: str) -> str:
    """结论下面那一句话（价格偏高且佣金率仅5% 之类）。

    ★ 取「## 结论」标题下的**理由行**（`_verdict_lines` 已是唯一真源）。
      旧实现按 `"▎结论"` 切分 —— 但真实报告的标题是 `## 结论`，
      `▎结论` 这个串全仓只出现在它自己的 docstring 里
      ⇒ 摘要**恒为空**，卡片只能显示「（点上方长图看完整分析）」。
    """
    return _verdict_reason(report)[:60]


def _send_product_card(sender, chat_id: str, title: str, report: str,
                        product_id: str = "") -> bool:
    """发商品分析卡（结论 + 关键数字 + 按钮）。成功返回 True。"""
    try:
        from . import feishu_card as fc
        verdict, tpl = _card_verdict(report)
        summary = _card_summary(report)
        # 详细匹配分析在**长图**里；卡片只留「结论 + 一句话 + 按钮」，
        # 不要再把摘要重复一遍（重复 = 啰嗦 = 不好看）。
        card = fc.product_card(
            title[:40], verdict, tpl, summary or "（点上方长图看完整分析）",
            detail_lines=[],
            product_id=product_id,
            plan_hint="接了之后 AI 帮你排哪天推、每天早上提醒你")
        sender.send_card(chat_id, card)
        return True
    except Exception:
        log.exception("send product card failed: chat=%s", chat_id)
        return False


def notify_product_card(sender, chat_id: str, title: str, report: str,
                        tenant_key: str, stats: dict = None,
                        source: str = "", product_id: str = "") -> None:
    """商品分析推送：分节卡片长图（复用拆解渲染器）+ 标题 + 文字版
    + 多维表格「商品推荐记录」（结论列单选带色）。

    ★ 末尾带**接品引导**（2026-10-24）：
      达人发**裸商品ID** 的语义是「这商品怎么样」（查），
      但他很可能也想要「把它接进排品表」（接）——
      两条链路长得一样，不给引导，达人永远不知道「接了 xxx」这个入口存在。
    """
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
        # ★ 详细分析在**长图**里 ⇒ 聊天里**不再铺一整段文字**
        #   （老大 2026-10-24：「文字版可以收起来就好了，有点不好看」）。
        #   换成一张**短卡片**：结论 + 关键数字 + 按钮。
        if not _send_product_card(sender, chat_id, title, report, product_id):
            # 发卡片失败 → 回退文字版（宁可丑一点，也不能不推）
            sender.send_text(chat_id, f"🛒 商品分析完成：{title}")
            _send_text_quiet(sender, chat_id,
                             f"🛒 {title}\n{_md_to_text(report)}")
            _send_accept_hint(sender, chat_id, product_id)
        # ★ 2026-10-27 档案层已换代（bitable_sync 链路废弃，见 notify_douyin_report 同因）。
        # try:
        #     from . import bitable_sync
        #     bitable_sync.sync_product(
        #         tenant_key, stats or {"name": title}, report, source,
        #         img_path=str(tmp))
        # except Exception:
        #     log.exception("bitable sync product failed: tenant=%s", tenant_key)
        return
    except Exception:
        log.exception("render product card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, f"🛒 {title}\n\n{_md_to_text(report)}")
    _send_accept_hint(sender, chat_id, product_id)


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
        # ★ 2026-10-27 档案层已换代（bitable_sync 链路废弃，见 notify_douyin_report 同因）。
        # try:
        #     from . import bitable_sync
        #     bitable_sync.sync_log(
        #         tenant_key, "视频拆解",
        #         (result_text or "").split("\n", 1)[0][:500],
        #         _md_to_text(report))
        # except Exception:
        #     log.exception("bitable sync log failed: tenant=%s", tenant_key)
        return
    except Exception:
        log.exception("render teardown card failed, fallback to text: tenant=%s", tenant_key)
    sender.send_text(chat_id, f"📹 视频拆解：{title}\n\n{_md_to_text(report)}")
