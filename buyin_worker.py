#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
buyin_worker.py — 巨量百应自动采集 worker（系统 AI 的"手"）

解决：系统 AI 员工无法控制浏览器的问题。
架构：员工/客户只往 talent_collects 队列丢「达人名称 + 抖音号」，本 worker
轮询队列 → 驱动常驻 Playwright 浏览器（团长端 buyin.jinritemai.com，登录态
持久化在本机 user-data-dir）→ 像人一样搜索→点进主页→逐 tab 点击翻页 →
抓取 XHR JSON + DOM 快照 → GLM-4.7 结构化提取 → 写回达人库（走 solobrave
HTTP API，复用去重/归属逻辑）→ 回写队列 done/failed。

运行：/Users/qichen/Library/Application\ Support/kimi-desktop/daimon-share/daimon/runtime/python/bin/python3 buyin_worker.py
控制口：http://127.0.0.1:8095  (/health /open /collect /jobs/<id> /shots/)
"""

import json
import os
import random
import re
import secrets
import sqlite3
import subprocess
import threading
import time
import traceback
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─────────────────────────────── 配置 ───────────────────────────────
PROD_DIR = Path('/Users/qichen/solobrave-prod')
DB_PATH = PROD_DIR / 'data' / 'solobrave.db'
SERVER_BASE = 'http://127.0.0.1:8080'          # solobrave 内部 HTTP API（8081 是 WS，别用）
WORKER_AGENT_ID = 'emp_1780199176680'          # Helen（商务），数据落她子库
BROWSER_DIR = PROD_DIR / 'data' / 'buyin_browser'
PROFILE_DIR = BROWSER_DIR / 'profile'          # 团长端登录态持久化目录
DAREN_PROFILE_DIR = BROWSER_DIR / 'profile_daren'  # 达人端独立 profile（同域不同身份，必须分开）
SHOTS_DIR = BROWSER_DIR / 'shots'
ARCHIVE_DIR = BROWSER_DIR / 'archives'
VIDEOS_DIR = BROWSER_DIR / 'videos'             # 拆解视频落地目录
LARK_CLI = '/opt/homebrew/bin/lark-cli'         # 妙记转写（launchd PATH 无 /opt/homebrew/bin，用绝对路径）
LOG_FILE = BROWSER_DIR / 'worker.log'
CONTROL_HOST, CONTROL_PORT = '127.0.0.1', 8095
BUYIN_SQUARE = 'https://buyin.jinritemai.com/dashboard/servicehall/daren-square'
BUYIN_HOME = 'https://buyin.jinritemai.com/'
# 达人端（商品库采集走这里：选品广场/商品详情在达人工作台）
# 老大实测：正确入口是抖音电商官网 www.douyinec.com → 右上角「登录」→ 达人工作台
DAREN_HOME = 'https://www.douyinec.com/'

# GLM-4.7（openai-completions 协议）— key 从 openclaw.json 读，不写死
OPENCLAW_CFG = Path.home() / '.openclaw' / 'openclaw.json'

TABS = ['概览', '直播详情', '视频详情', '粉丝分析', '带货分析', '历史带货评价']
JOB_TIMEOUT = 15 * 60           # 单任务硬超时
POLL_INTERVAL = 5               # 队列轮询秒数
XHR_MAX_PER_TAB = 60            # 每 tab 最多抓取的 XHR 数
DOM_SNAP_LIMIT = 9000           # 每 tab DOM 快照字数上限

for _d in (BROWSER_DIR, SHOTS_DIR, ARCHIVE_DIR, VIDEOS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_log_lock = threading.Lock()


def log(msg):
    line = f'[{time.strftime("%m-%d %H:%M:%S")}] {msg}'
    with _log_lock:
        print(line, flush=True)
        try:
            with open(LOG_FILE, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except Exception:
            pass


# ─────────────────────────────── LLM (GLM-4.7) ───────────────────────────────
class LLMError(Exception):
    pass


def _zhipu_cfg():
    cfg = json.load(open(OPENCLAW_CFG, encoding='utf-8'))
    zp = cfg.get('models', {}).get('providers', {}).get('zhipu') or {}
    base = (zp.get('baseUrl') or 'https://open.bigmodel.cn/api/paas/v4/').rstrip('/')
    key = zp.get('apiKey') or ''
    if not key:
        raise LLMError('openclaw.json 缺少 zhipu apiKey')
    return base, key


def llm_chat(messages, max_tokens=8000, temperature=0.0, timeout=180, model='glm-4.7'):
    """调 GLM（OpenAI chat completions 协议），返回 content 字符串。"""
    base, key = _zhipu_cfg()
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
        method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise LLMError(f'glm http {e.code}: {e.read()[:300]}')
    except Exception as e:
        raise LLMError(f'glm request failed: {e}')
    content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    if not content.strip():
        raise LLMError('glm empty content')
    return content


def _balanced_json(text):
    """从文本中截取第一个 { 起到配平 } 的完整 JSON 段，忽略前后杂物。"""
    start = text.find('{')
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
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
                return text[start:i + 1]
    return None


def extract_fields(collect_name, collect_douyin_id, tabs_dom, xhr_dump):
    """把原始采集材料交给 GLM-4.7 做结构化提取，返回 (fields_dict, usage_note)。"""
    material = {
        '目标达人名称': collect_name,
        '目标抖音号': collect_douyin_id or '（未提供，以名称匹配为准）',
        '各tab页面文本快照': tabs_dom,
        '抓取到的接口JSON（节选）': xhr_dump,
    }
    system = (
        '你是电商数据提取引擎。从巨量百应达人详情页采集材料中，提取指定达人的全部结构化字段。\n'
        '只输出一个 JSON 对象，不要输出任何其他文字、不要用 markdown 代码块。\n'
        '铁律：\n'
        '1. 只提取「目标达人」的数据，页面中出现的其他达人数据一律忽略\n'
        '2. 数值必须来自材料原文，严禁编造或估算；材料中没有的字段填 null，绝不猜测\n'
        '3. 金额区间（如 "¥100万-250万"）保留原文字符串，不要换算\n'
        '4. 百分比一律输出百分数数字（如 66 表示66%），严禁把图表计数当成百分比；'
        '括号里带%的才是百分比，纯数字是计数，不要混填\n'
        '5. 达人昵称/抖音号以页面顶部主页信息为准，原样照抄，不要增删字符\n'
        '输出字段（严格用这些 key）：\n'
        'name(昵称) douyin_id(抖音号,字符串) followers(粉丝数,整数) talent_type(达人类型) '
        'city(地区) level(等级) tags(平台标签,数组) fulfillment_score(履约分) rating_score(评价分) '
        'category(主推类目) content_style(内容标签) agency(所属机构) bio(简介) '
        'main_category(主推类目原文) data_period(统计周期) '
        'total_products(带货商品数,整数) total_history_days(历史带货天数) total_shops(合作店铺数) '
        'total_gmv_text(结算总额区间原文) live_ratio(直播销售占比数字) live_sessions(带货直播场次) '
        'live_views(直播观看人数,字符串) avg_live_gmv_text(场均结算额区间原文) live_gpm_text(直播GPM区间原文) '
        'video_ratio(视频销售占比数字) video_plays(视频播放量,字符串) single_video_settlement(单视频结算额区间原文) '
        'video_gpm_text(视频GPM区间原文) monthly_settlement(月结算额) '
        'price_distribution(价格带分布,对象) category_distribution(带货类目分布,对象) '
        'fan_gender(粉丝性别分布,对象) fan_age(粉丝年龄分布,对象) fan_region(粉丝地域分布,对象) '
        'fan_crowd(粉丝八大人群,对象) fan_price_range(粉丝价格偏好) '
        'video_completion_rate(视频完播率) video_interaction_rate(视频互动率) '
        'live_avg_watch(人均观看时长) live_peak_online(在线峰值) '
        'top_products(近期带货TOP商品,数组,每项含name/结算额区间/类目) '
        'recent_videos(近期带货视频,数组,每项含文案/发布时间/播放量/互动率/单视频结算额) '
        'cooperation_mode(合作模式) commission_note(佣金报价说明) rating_note(带货评价分说明)'
    )
    last_err = ''
    for attempt in (1, 2):
        try:
            content = llm_chat([
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(material, ensure_ascii=False)[:120000]},
            ], max_tokens=8000)
            candidate = _balanced_json(content)
            if candidate is None:
                raise LLMError(f'输出中找不到完整 JSON（第{attempt}次）: {content[:200]}')
            try:
                fields = json.loads(candidate)
            except Exception as e:
                raise LLMError(f'JSON 解析失败（第{attempt}次）: {e}; 末尾200字: ...{candidate[-200:]}')
            if not isinstance(fields, dict) or not fields.get('name'):
                raise LLMError(f'JSON 结构异常（第{attempt}次）: {str(fields)[:200]}')
            return fields
        except LLMError as e:
            last_err = str(e)
            log(f'  [LLM] 提取第{attempt}次失败: {last_err[:300]}')
    raise LLMError(last_err)


def _repair_json_text(s):
    """glm-4v 常把中文单位裸写进数字字段（"followers": 450万），补引号让它可解析。"""
    s = re.sub(r':\s*(-?[0-9][0-9.,]*[\u4e00-\u9fff%][^,\}\]]*)', r': "\1"', s)
    s = re.sub(r':\s*([\u4e00-\u9fff][^,\}\]"]*)', r': "\1"', s)
    return s


_VISION_NUMERIC_KEYS = {
    'followers', 'total_products', 'total_shops', 'average_price',
    'live_ratio', 'video_ratio', 'live_sessions', 'live_views',
    'video_completion_rate', 'video_interaction_rate', 'fan_growth',
    'fulfillment_score', 'rating_score',
}

# 区间类文本字段的常识校验：单视频结算额不可能到"万"级，模型把结算总额抄过来时能拦住
_VISION_TEXT_GUARDS = {
    'single_video_settlement': lambda s: '万' not in str(s) and '亿' not in str(s),
}


def _vision_text_ok(key, value) -> bool:
    guard = _VISION_TEXT_GUARDS.get(key)
    return guard(value) if guard else True


def _vision_junk_value(v) -> bool:
    """视觉提取占位垃圾判定：None/空容器/纯零值。
    glm-4v 识别失败时常返回 {"男性": 0.0, "女性": 0.0}、0、'0.0'、'0%' 这类占位，
    绝不能写进 fields（会覆盖掉 DOM 提取的好数据）。"""
    if v is None or v == '' or v == [] or v == {}:
        return True
    if isinstance(v, (int, float)) and v == 0:
        return True
    if isinstance(v, str):
        s = v.strip().rstrip('%').strip()
        if s in ('0', '0.0', '0.00'):
            return True
    if isinstance(v, dict):
        nums = []
        for x in v.values():
            if isinstance(x, (int, float)):
                nums.append(float(x))
            elif isinstance(x, str):
                try:
                    nums.append(float(x.strip().rstrip('%')))
                except ValueError:
                    return False        # 有非数字值，当正常数据
        return bool(nums) and all(n == 0 for n in nums)
    if isinstance(v, list) and all(_vision_junk_value(x) for x in v):
        return True
    return False


# 视觉提取按 tab 聚焦的字段清单：一次索要 ~50 个字段会严重稀释读图精度，
# 每个 tab 用各自的字段清单单独读（实测覆盖率从 46 字段/多垃圾 → 52 字段/高可用）
_VISION_IRON = (
    '铁律：数值必须来自截图原文，严禁编造或估算；截图里确实没有的字段填 null。'
    '金额区间（如"¥250万-500万"）保留原文。百分数输出数字（54.74 表示54.74%）。'
    '只输出一个 JSON 对象，不要输出任何其他文字。'
)
_VISION_TAB_PROMPTS = {
    '概览': (
        '读巨量百应达人详情页【概览】tab截图。提取：'
        'content_total(发布内容总数,整数) watch_total(观看总次数,万字数字) '
        'live_count(直播场次) video_count_all(视频个数) tuwen_count(图文个数) '
        'history_videos(历史带货视频明细,数组,每项含title/publish_date/duration/views/likes/'
        'comments/settlement/到手价/原价,能读多少读多少) '
        'fan_gender/fan_age/fan_region/fan_crowd(粉丝画像分布,对象,百分比数字) '
        'fan_purchase_pref(粉丝购买偏好,如"服装22.68%") fan_consume_level(粉丝消费水平,对象) '
        'category_distribution(带货最多品类TOP5,对象,百分比数字) '
        'cooperating_brands_distribution(品牌集中度TOP5,对象,百分比数字)。' + _VISION_IRON
    ),
    '带货分析': (
        '读巨量百应达人详情页【带货分析】tab截图。提取：'
        'total_gmv_text(结算总额) total_products(带货商品数) total_shops(合作店铺数) '
        'average_price(平均件单价) '
        'live_settlement_ratio/live_avg_gmv_text/live_gpm_text/live_product_count/'
        'live_shop_count/live_avg_price(直播带货数据块,无数据显示null) '
        'video_settlement_ratio/single_video_settlement/video_gpm_text/video_product_count/'
        'video_shop_count/video_avg_price(视频带货数据块)。' + _VISION_IRON
    ),
    '粉丝分析': (
        '读巨量百应达人详情页【粉丝分析】tab截图。提取：'
        'fan_gender/fan_age/fan_region/fan_crowd(分布对象,百分比数字。'
        '性别以页面文字摘要为准如"男性居多 54.74%"，不要凭饼图颜色猜) '
        'fan_feature_gender/fan_feature_age/fan_feature_region/fan_feature_crowd(粉丝特征pill,原样字符串) '
        'consume_pref_price/consume_pref_category(消费偏好pill,原样字符串) '
        'fan_growth(近2月粉丝变化数,带符号整数) fan_growth_rate(粉丝变化率,字符串) '
        'fan_consume_level(消费水平分布,对象)。' + _VISION_IRON
    ),
    '评价详情': (
        '读巨量百应达人详情页【评价详情】tab截图。提取：'
        'review_count(全部) review_good(好评) review_mid(中评) review_bad(差评) '
        'rating_effect/rating_fulfill/rating_communication(三维分,无数据显示null) '
        'reviews(评价明细数组,每项: 商家名/星级/评价原文/联系电话/日期,联系电话原样照抄数字) '
        'review_contact(评价里出现的联系电话)。' + _VISION_IRON
    ),
    '场景分析': (
        '读巨量百应达人详情页【场景分析】tab截图。提取：'
        'live_scene/video_scene/tuwen_scene/chuchuang_scene(各场景数据对象,有则提取;'
        '页面显示"暂无数据"就填 null)。' + _VISION_IRON
    ),
}
_VISION_TAB_KEYS = tuple(_VISION_TAB_PROMPTS.keys()) + ('视频详情', '直播详情')


def _vision_extract_fields(shots):
    """glm-4v-plus 直接看 tab 截图做视觉提取，补齐 DOM 提取缺失/不准的字段。
    截图压缩后每 3 张一批（单次 6 张会 400），多批结果合并。
    返回 fields dict（key 与 extract_fields 对齐）；失败返回 {}（非致命）。"""
    import base64
    import io

    def _b64_img(path):
        from PIL import Image
        im = Image.open(path).convert('RGB')
        if im.width > 960:
            im = im.resize((960, int(im.height * 960 / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, 'JPEG', quality=82)
        return base64.b64encode(buf.getvalue()).decode()

    shots = [p for p in (shots or []) if not str(p).endswith('_profile.png')]
    if not shots:
        return {}
    prompt = (
        '你是电商数据提取引擎。给你某抖音达人在巨量百应详情页各 tab 的真实页面截图，'
        '逐张仔细读图，提取下列字段。只输出一个 JSON 对象，不要输出任何其他文字。\n'
        '铁律：\n'
        '1. 数值必须来自截图原文，严禁编造或估算；截图里确实没有的字段填 null\n'
        '2. 括号里带 % 的才是百分比，输出百分数数字（75 表示75%，0.6 表示0.6%）；纯数字是计数\n'
        '3. 金额区间（如 "¥250万-500万"）保留原文字符串，不要换算\n'
        '4. 五大标签（粉丝特征/消费偏好这类 pill 标签）原样照抄，包括百分比\n'
        '5. 评价明细里的商家名、评价内容、联系电话、日期都要；联系电话脱敏规则：'
        '原样照抄数字即可\n'
        '输出字段（严格用这些 key）：\n'
        'name(昵称) douyin_id(抖音号,字符串) followers(粉丝数,整数) talent_type(达人类型) '
        'city(地区) level(等级) tags(平台标签,数组) fulfillment_score(履约分) rating_score(评价总分) '
        'category(主推类目) content_style(内容标签) agency(所属机构) bio(简介) '
        'data_period(统计周期) '
        'total_products(带货商品数,整数) total_shops(合作店铺数) '
        'total_history_days(历史带货天数,整数) '
        'total_gmv_text(结算总额区间原文) '
        'live_ratio(直播销售占比数字) live_sessions(带货直播场次) '
        'avg_live_gmv_text(场均结算额区间原文) live_gpm_text(直播GPM区间原文) '
        'video_ratio(视频销售占比数字) video_count(带货视频数量,整数) '
        'video_plays(带货视频播放量,字符串) video_avg_plays(视频平均播放量,字符串) '
        'video_plays_range(近30天播放量区间,字符串,如"最低53,最高66.2万,均值2.78万") '
        'single_video_settlement(单视频结算额区间原文) video_gpm_text(视频GPM区间原文) '
        'monthly_settlement(月结算额) average_price(平均件单价) video_avg_price(视频平均件单价) '
        'video_completion_rate(视频完播率百分数) video_interaction_rate(视频互动率百分数) '
        'video_likes(点赞数) video_comments(评论数) video_shares(转发数) '
        'category_distribution(带货主推类目分布,对象,含全部类目) '
        'cooperating_brands(合作品牌分布,对象,含全部品牌) '
        'fan_gender(粉丝性别分布,对象) fan_age(粉丝年龄分布,对象) '
        'fan_region(粉丝地域分布,对象) fan_crowd(粉丝人群标签,对象) '
        'fan_price_range(粉丝价格偏好) content_preference(粉丝内容偏好,如"偏好服装37") '
        'fan_growth(粉丝变化数,带符号整数) fan_growth_rate(粉丝变化率,字符串) '
        'rating_effect(带货效果分) rating_fulfill(合作履约分) rating_communication(沟通态度分) '
        'review_count(评价总数) review_good(好评数) review_mid(中评数) review_bad(差评数) '
        'review_tags(评价标签及次数,对象) '
        'reviews(评价明细,数组,每项含商家/内容/电话/日期) '
        'review_contact(评价中出现的合作联系电话,字符串)'
    )
    # ★ 按 tab 分组聚焦提取：每 tab 用自己的字段清单，组内每 3 张一批；
    # 没匹配上已知 tab 的截图（旧版布局的视频详情/直播详情等）走全量通用清单
    groups: dict = {}
    for p in shots:
        base = Path(p).stem
        for kw in _VISION_TAB_KEYS:
            if kw in base:
                groups.setdefault(kw, []).append(p)
                break
        else:
            groups.setdefault('*', []).append(p)

    batches = []          # (prompt, [paths])
    for tab, paths in groups.items():
        tprompt = _VISION_TAB_PROMPTS.get(tab) or prompt
        for start in range(0, len(paths), 3):
            batches.append((tprompt, paths[start:start + 3]))

    merged = {}

    def _try_vision(base, key, model, prompt, batch, temperature=0.0):
        """单次 vision 调用并把通过校验的字段并入 merged，成功返回 True。"""
        imgs = []
        for p in batch:
            imgs.append({'type': 'image_url',
                         'image_url': {'url': f'data:image/jpeg;base64,{_b64_img(p)}'}})
        req = urllib.request.Request(
            base + '/chat/completions',
            data=json.dumps({
                'model': model,
                'messages': [{'role': 'user', 'content':
                              [{'type': 'text', 'text': prompt}] + imgs}],
                'temperature': temperature,
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': f'Bearer {key}'},
            method='POST')
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode())
        content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
        candidate = _balanced_json(content)
        if not candidate:
            raise LLMError(f'视觉提取输出中无 JSON: {content[:200]}')
        try:
            vf = json.loads(candidate)
        except json.JSONDecodeError:
            vf = json.loads(_repair_json_text(candidate))
        if not isinstance(vf, dict):
            return False
        for k, v in vf.items():
            if k in ('name', 'douyin_id'):
                continue  # 身份字段以 DOM 提取+回填为准，视觉不覆盖
            if k in _VISION_NUMERIC_KEYS and not isinstance(v, (int, float)):
                continue  # 数字字段收到脏字符串直接丢弃，保留 DOM 值
            if k in ('total_products', 'total_shops', 'followers',
                     'average_price', 'total_history_days', 'video_count',
                     'rating_effect', 'rating_fulfill', 'rating_communication') and v == 0:
                continue  # 这些字段为 0 必是读图失败，不能覆盖 DOM 值
            if isinstance(v, dict) and any('全部' in str(kk) for kk in v):
                continue  # {"全部品牌":"全部品牌"} 这类占位垃圾
            if k == 'video_avg_price' and str(v) in ('¥0', '0', '0.0', ''):
                continue
            if k == 'video_plays_range' and ('最低0' in str(v) or '最高0' in str(v)):
                continue
            if k == 'fan_growth_rate' and str(v) in ('0%', '0.0%', ''):
                continue
            if not _vision_text_ok(k, v):
                continue  # 常识校验不过，保留 DOM 值
            if v not in (None, '', [], {}):
                merged[k] = v
        return True

    def _fallback_cfgs():
        """智谱 429/挂之后的降级链：kimi 视觉 → minimax M3 系列。
        配置读 openclaw.json（与 daren_bot 视觉兜底同源）。"""
        out = []
        try:
            cfg = json.load(open(OPENCLAW_CFG, encoding='utf-8'))
            km = cfg.get('models', {}).get('providers', {}).get('kimi') or {}
            kbase = (km.get('baseUrl') or 'https://api.kimi.com/coding').rstrip('/')
            if not kbase.endswith('/v1'):
                kbase += '/v1'
            if km.get('apiKey'):
                out.append((kbase, km['apiKey'], 'moonshot-v1-8k-vision-preview', 1.0))
            mm = cfg.get('models', {}).get('providers', {}).get('minimax') or {}
            if mm.get('apiKey'):
                for m in ('MiniMax-M3.1-Flash-Preview', 'MiniMax-M3'):
                    out.append(('https://api.minimaxi.com/v1', mm['apiKey'], m, 1.0))
        except Exception as e:
            log(f'  [LLM] 视觉降级配置读取失败: {e}')
        return out

    for bidx, (prompt, batch) in enumerate(batches, 1):
        if bidx > 1:
            time.sleep(6)               # 批间限速，防 glm-4v RPM 触发 429
        ok = False
        try:
            zbase, zkey = _zhipu_cfg()
            for attempt in (1, 2, 3):
                try:
                    ok = _try_vision(zbase, zkey, 'glm-4v-plus', prompt, batch)
                    if ok:
                        break
                except Exception as e:
                    log(f'  [LLM] 视觉提取批次{bidx}第{attempt}次失败: {e}')
                    if '429' in str(e):
                        time.sleep(20 * attempt)    # 429 限速：退避 20/40/60s 再试
            if not ok:
                log(f'  [LLM] 视觉提取批次{bidx}智谱放弃，尝试降级模型')
        except LLMError as e:
            log(f'  [LLM] 智谱配置不可用: {e}')
        if not ok:
            for fbase, fkey, fmodel, ftemp in _fallback_cfgs():
                try:
                    if _try_vision(fbase, fkey, fmodel, prompt, batch, ftemp):
                        log(f'  [LLM] 视觉提取批次{bidx}经 {fmodel} 降级成功')
                        ok = True
                        break
                except Exception as e:
                    log(f'  [LLM] 视觉降级 {fmodel} 失败: {str(e)[:120]}')
            if not ok:
                log(f'  [LLM] 视觉提取批次{bidx}放弃（非致命）')
    return merged


def analyze_talent(fields):
    """达人视角的账号深度诊断。报告读者是达人本人（客户），不是团长。
    返回 (verdict, analysis_text, reason)，失败返回 ('','','')。
    analysis_text 为分区文本：亮点/风险/建议/粉丝解读/选品方向/内容策略。"""
    prompt = (
        '你是抖音电商达人的 AI 运营顾问。达人本人发来自己的账号数据，想知道自己账号做得怎么样、'
        '下一步该怎么提升带货。基于以下结构化数据产出深度账号诊断，用第二人称"你"对达人说话。\n'
        '只输出一个 JSON 对象：{"verdict":"潜力型|稳健型|待提升型",'
        '"highlights":["..."], "risks":["..."], "suggestions":["..."],'
        '"fan_insight":["..."], "product_directions":["..."], "content_strategy":["..."]}\n'
        '要求：每条 60 字以内，具体、有数据支撑、不喊空话；\n'
        'highlights 2-4 条（账号优势）；risks 1-3 条（不改进会影响带货的问题）；'
        'suggestions 2-4 条（达人自己的下一步动作：拍什么内容、要不要开播、选什么品、怎么涨粉）；\n'
        'fan_insight 2-3 条（解读粉丝画像对带货的含义：谁在看、买得起什么、爱买什么，'
        '结合性别/年龄/地域/消费水平/购买偏好给出判断）；\n'
        'product_directions 3-5 条（适合带的选品方向：类目、价格带、品牌/店铺类型，'
        '必须结合粉丝消费水平、已有类目分布、带货商品数等数据，给出"为什么是这类"的一句话理由）；\n'
        'content_strategy 2-4 条（内容策略：视频怎么拍、多久发一条、要不要开播、'
        '结合视频/直播占比和近期视频表现说）；\n'
        '所有数字必须来自输入数据，没有的维度不要硬编。'
        '输入里已有的带货数据（带过多少品、月结算多少）必须引用具体数字，'
        '禁止说"带货数据未体现"这类无视输入的话。\n'
        '特别注意：average_price 是平均件单价（每单金额），不是月结算额；'
        'monthly_settlement 为空表示没有月结算数据，禁止拿件单价充当月结算，'
        '也不要编造"月结算仅X元"这类句子。'
    )
    safe = {k: v for k, v in fields.items() if not isinstance(v, (bytes, bytearray))}
    content = ''
    for model in ('glm-4.7', 'glm-4-flash'):   # 主模型挂了就降级便宜模型，评估不能空
        try:
            content = llm_chat([
                {'role': 'user',
                 'content': prompt + '\n【达人数据】' + json.dumps(safe, ensure_ascii=False)[:20000]},
            ], max_tokens=2200, temperature=0.0,
                **({'model': model} if model != 'glm-4.7' else {}))
            if content.strip():
                break
        except Exception as e:
            log(f'  [LLM] 达人评估({model})失败，降级: {e}')
            content = ''
    if not content.strip():
        log('  [LLM] 达人评估全部失败（非致命）')
        return '', '', ''
    try:
        candidate = _balanced_json(content)
        if not candidate:
            raise LLMError('评估输出无 JSON')
        obj = json.loads(candidate)
        verdict = str(obj.get('verdict') or '').strip()
        # 数据守卫：输入里没有月结算数据时，丢弃一切引用"月结算"的条目（弱模型爱拿件单价冒充）
        _no_ms = not fields.get('monthly_settlement')
        lines = []
        for title, key in (('亮点', 'highlights'), ('风险', 'risks'), ('建议', 'suggestions'),
                           ('粉丝解读', 'fan_insight'), ('选品方向', 'product_directions'),
                           ('内容策略', 'content_strategy')):
            items = obj.get(key) or []
            if _no_ms:
                items = [x for x in items if '月结算' not in str(x)]
            if items:
                lines.append(f'【{title}】')
                lines.extend(f'· {x}' for x in items[:6])
        return verdict, '\n'.join(lines), ''
    except Exception as e:
        log(f'  [LLM] 达人评估输出解析失败（非致命）: {e}')
        return '', '', ''


def summarize_for_notes(fields):
    """把提取结果压成 notes 摘要（达人档案速览）。"""
    try:
        content = llm_chat([
            {'role': 'system', 'content':
             '把达人结构化数据写成 200 字以内的中文档案速览，纯文本无 markdown，'
             '格式：昵称/粉丝/地区/主营类目 | 核心数据（结算额、直播视频占比）| 粉丝画像要点 | 合作要点。'
             '所有数字必须来自输入，没有的写"未提供"。'},
            {'role': 'user', 'content': json.dumps(fields, ensure_ascii=False)[:20000]},
        ], max_tokens=600, temperature=0.0)
        if not content.strip():
            raise LLMError('empty content')
        return content.strip()
    except Exception as e:
        log(f'  [LLM] notes 摘要主模型失败，降级 glm-4-flash: {e}')
    try:
        content = llm_chat([
            {'role': 'system', 'content':
             '把达人结构化数据写成 200 字以内的中文档案速览，纯文本，数字必须来自输入。'},
            {'role': 'user', 'content': json.dumps(fields, ensure_ascii=False)[:20000]},
        ], max_tokens=600, temperature=0.0, model='glm-4-flash')
        return (content or '').strip()
    except Exception as e:
        log(f'  [LLM] notes 摘要失败（不影响主流程）: {e}')
        return ''


# ─────────────────────────────── 浏览器 ───────────────────────────────
class NeedLogin(Exception):
    pass


class Browser:
    """常驻 Playwright 浏览器（有头模式，登录态持久化）。"""

    def __init__(self, profile_dir=PROFILE_DIR, label='团长端'):
        self.profile_dir = Path(profile_dir)
        self.label = label
        self._pw = None
        self.ctx = None
        self.page = None
        self.lock = threading.Lock()

    def up(self):
        return self.ctx is not None

    def ensure(self):
        """确保浏览器已启动；返回主 page。"""
        if self.ctx is not None:
            try:
                if self.page and not self.page.is_closed():
                    return self.page
            except Exception:
                pass
        from playwright.sync_api import sync_playwright
        log(f'[Browser:{self.label}] 启动（有头模式，持久化目录 {self.profile_dir.name}）…')
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._pw = sync_playwright().start()
        except Exception as e:
            if 'asyncio loop' in str(e):
                # 线程事件循环被污染（Chromium 崩溃后遗症），交由 _browser_loop 换线程自愈
                raise BrowserLoopPoisoned(str(e))
            raise
        self.ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            headless=False,
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
            viewport={'width': 1440, 'height': 900},
            args=['--disable-blink-features=AutomationControlled',
                  '--start-maximized'],
        )
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        log(f'[Browser:{self.label}] 已启动')
        return self.page

    def close(self):
        try:
            if self.ctx:
                self.ctx.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self.ctx = None
        self.page = None
        self._pw = None

    def health(self):
        """返回 (browser_up, logged_in)。"""
        if not self.up():
            return False, False
        try:
            url = self.page.url
            if 'login' in url or 'passport' in url:
                return True, False
            # 以登录 cookie 为准；达人端用 daren 专属标记
            mark = DAREN_LOGIN_MARK if self.label == '达人端' else None
            cookies = self.ctx.cookies('https://buyin.jinritemai.com')
            names = {c.get('name') for c in cookies}
            if mark:
                return True, mark in names
            logged = 'ecom_us_lt_buyin' in names and 'sessionid' in names
            return True, logged
        except Exception:
            return True, False


BROWSER = Browser(PROFILE_DIR, '团长端')
DAREN_BROWSER = Browser(DAREN_PROFILE_DIR, '达人端')
DAREN_LOGIN_MARK = 'sid_ucp_sso_v1_daren'   # 达人端登录态核心 cookie（SSO daren 域）


# ─────────────────────────────── 采集管线 ───────────────────────────────
def _human_sleep(a=1.0, b=2.2):
    time.sleep(random.uniform(a, b))


def _robust_click(locator, tag=''):
    """常规点击 → force → JS dispatch 三级降级，应对拦截层。"""
    try:
        locator.click(timeout=8000)
        return True
    except Exception as e:
        log(f'  [Collect] 常规点击失败({tag})，降级 force: {type(e).__name__}')
    try:
        locator.click(timeout=5000, force=True)
        return True
    except Exception:
        pass
    try:
        locator.evaluate("el => el.click()")
        return True
    except Exception as e2:
        log(f'  [Collect] {tag} 点击彻底失败: {e2}')
        return False


def _attach_sniffer(page, bucket):
    """挂在 page 上，把所有 XHR/fetch 的 JSON 响应收进 bucket（list）。"""

    def _on_response(resp):
        try:
            if resp.request.resource_type not in ('xhr', 'fetch'):
                return
            ct = (resp.headers.get('content-type') or '')
            if 'json' not in ct:
                return
            body = resp.json()
            bucket.append({'url': resp.url[:300], 'body': body})
        except Exception:
            return

    page.on('response', _on_response)


def _check_login_state(page):
    url = page.url
    if 'login' in url or 'passport' in url:
        raise NeedLogin('页面跳转到了登录页')
    # 以登录 cookie 为准（e.g. ecom_us_lt_buyin），不做 QR 元素猜测——
    # 页面里的客服/联系二维码会造成误报
    try:
        cookies = page.context.cookies('https://buyin.jinritemai.com')
        names = {c.get('name') for c in cookies}
        if 'ecom_us_lt_buyin' not in names:
            raise NeedLogin('登录 cookie 缺失')
    except NeedLogin:
        raise
    except Exception:
        pass


def _dump_page(page, tag, cid):
    """失败现场留存：截图 + HTML，便于离线分析 DOM。"""
    try:
        safe_tag = re.sub(r'[^\w]+', '_', tag)[:40]
        page.screenshot(path=str(SHOTS_DIR / f'dbg_{cid}_{safe_tag}.png'))
        html = page.content()
        (SHOTS_DIR / f'dbg_{cid}_{safe_tag}.html').write_text(html, encoding='utf-8')
        log(f'  [Debug] 现场已留存: dbg_{cid}_{safe_tag}.png/.html (url={page.url})')
    except Exception as e:
        log(f'  [Debug] 现场留存失败: {e}')


def _search_and_open(page, name, douyin_id, cid):
    """在达人广场搜索并点开目标达人主页，返回 (profile_page, picked_name)。
    ★ 老大实测口径：抖音号搜索的首行永远是正确达人，名字搜索首行可能不对——
    所以有抖音号时一律用抖音号做搜索词、直选平台首行（身份核验兜底）。"""
    id_driven = bool(douyin_id)
    search_term = douyin_id if id_driven else name
    page.goto(BUYIN_SQUARE, timeout=60000)
    page.wait_for_load_state('domcontentloaded')
    _human_sleep(2.5, 4)
    _check_login_state(page)
    log(f'  [Collect] 广场页已打开 url={page.url} 搜索词={"抖音号" if id_driven else "昵称"}:{search_term}')

    # 找搜索框（多候选）
    box = None
    used_sel = ''
    for sel in [
        'input[placeholder*="搜索"]',
        'input[placeholder*="达人"]',
        'div[class*="search"] input:visible',
        'input:visible >> nth=0',
    ]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                box = loc.first
                used_sel = sel
                break
        except Exception:
            continue
    if box is None:
        _dump_page(page, 'no_search_box', cid)
        raise RuntimeError('找不到达人搜索框')
    log(f'  [Collect] 搜索框命中: {used_sel}')

    try:
        box.click(timeout=6000)
    except Exception:
        try:
            box.evaluate("el => el.focus()")
        except Exception:
            pass
    _human_sleep(0.3, 0.7)
    try:
        box.fill('')
        box.press_sequentially(search_term, delay=random.randint(60, 140))
    except Exception:
        box.fill(search_term)
    _human_sleep(0.8, 1.5)
    try:
        page.keyboard.press('Enter')
        box.press('Enter')
    except Exception:
        pass
    _human_sleep(2.5, 4)

    picked, picked_name = _pick_result_row(page, name, douyin_id, cid, id_driven=id_driven)

    # 点击前先记录已打开页面，用于识别新开的 profile 标签页
    with page.context.expect_page(timeout=15000) as pop:
        clicked = _robust_click(picked, '达人搜索结果')
    if not clicked:
        _dump_page(page, 'result_click_failed', cid)
        raise RuntimeError('点击达人搜索结果失败')
    # 情况一：新开标签页打开主页
    try:
        profile = pop.value
        profile.wait_for_load_state('domcontentloaded')
        _human_sleep(2, 3)
        if 'daren-profile' in profile.url:
            _verify_profile_identity(profile, name, picked_name, douyin_id, cid)
            return profile, picked_name
    except RuntimeError:
        raise
    except Exception:
        pass
    # 情况二：当前页直接跳转
    _human_sleep(2, 3)
    if 'daren-profile' in page.url:
        _verify_profile_identity(page, name, picked_name, douyin_id, cid)
        return page, picked_name
    # 兜底：从现有页面里找 daren-profile
    for p in page.context.pages:
        if 'daren-profile' in p.url:
            _verify_profile_identity(p, name, picked_name, douyin_id, cid)
            return p, picked_name
    raise RuntimeError('点击后未进入达人主页')


def _name_close(nick, name):
    """昵称与目标名是否「同一人级别」的近似：
    前两字相同且长度差≤2（梦慈麻麻≈梦慈妈妈）。
    注意「璐妈妈」这类只共享后两字的不算。"""
    if nick == name:
        return True
    if len(name) < 2 or len(nick) < 2:
        return False
    return nick[:2] == name[:2] and abs(len(nick) - len(name)) <= 2


def _parse_fans(text):
    """从行文本里解析粉丝数（8.95万 / 203 / 1,718）。"""
    m = re.search(r'([\d.]+)\s*万', text)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except ValueError:
            return 0
    m = re.search(r'粉丝[^\d]{0,6}([\d,]+)', text)
    if m:
        try:
            return int(m.group(1).replace(',', ''))
        except ValueError:
            return 0
    return 0


def _pick_result_row(page, name, douyin_id, cid, id_driven=False):
    """从搜索结果列表里选行。

    id_driven=True（搜索词是抖音号）：直选平台首行——老大实测抖音号搜索首行
    永远是正确达人。
    id_driven=False（昵称搜索）：严格按平台排序（DOM 顺序）从上到下，取第一个
    可接受的行——昵称精确等于目标名，或「前两字相同的近似名 + 粉丝≥1000 真账号
    信号」（梦慈麻麻 8.95万 会排在精确等名的梦慈妈妈 203粉空号之前；
    老于波子_ 251粉仿名小号会被粉丝门槛拦住）。兜底：第一个精确等名行。
    返回 (row_locator, picked_name)。"""
    # 平台 2026-10 改版：list-table-info-right-name 下线，
    # 新 DOM 为 auxo-dorami-info-card-title-content > span.index-module__title___<hash>。
    # 多选择器兼容新旧版，命中即用。
    nick_sels = ['[class*="list-table-info-right-name"]',
                 '[class*="info-card-title-content"] > span',
                 'span[class^="index-module__title"]']
    rows = []
    for nick_sel in nick_sels:
        try:
            nicks = page.locator(nick_sel)
            for i in range(min(nicks.count(), 30)):
                try:
                    nick = nicks.nth(i)
                    txt = (nick.inner_text() or '').strip()
                    if not txt:
                        continue
                    # 行容器：向上逐层找「含粉丝数据的整张卡片」。注意昵称自身 class 就含
                    # list-table/info-card，会先把名字块当行；要一直爬到含「粉丝」文本的祖先。
                    row, row_text = None, ''
                    for level in range(1, 7):
                        cand = nick.locator(
                            'xpath=ancestor::*[contains(@class,"list-table") '
                            'or contains(@class,"info-card")][' + str(level) + ']')
                        if cand.count() == 0:
                            break
                        t = (cand.inner_text() or '')
                        if '粉丝' in t and len(t) < 2000:
                            row, row_text = cand, t
                            break
                    if row is None:
                        row = nick.locator('xpath=ancestor::*[self::div or self::a or self::li][position()<=4][1]')
                        row_text = (row.inner_text() if row.count() else '') or ''
                    rows.append({'row': row if row.count() else nick,
                                 'nick': txt, 'row_text': row_text,
                                 'fans': _parse_fans(row_text)})
                except Exception:
                    continue
            if rows:
                break  # 新版选择器命中即停，避免旧版空转
        except Exception:
            continue

    if not rows:
        # 兜底：老的 text= 定位方式
        cands = page.locator(f'text={name}')
        for i in range(min(cands.count(), 30)):
            try:
                el = cands.nth(i)
                if not el.is_visible():
                    continue
                row = el.locator('xpath=ancestor::*[self::div or self::a or self::li][position()<=4][1]')
                row_text = (row.inner_text() if row.count() else el.inner_text()) or ''
                rows.append({'row': row if row.count() else el,
                             'nick': (el.inner_text() or '').strip(),
                             'row_text': row_text,
                             'fans': _parse_fans(row_text)})
            except Exception:
                continue

    if not rows:
        _dump_page(page, 'search_no_result', cid)
        raise RuntimeError(f'搜索结果中未找到达人「{name}」' + (f'（抖音号 {douyin_id}）' if douyin_id else ''))

    # ★ 老大实测口径：抖音号搜索的首行永远是正确达人（名字搜索才可能不准）。
    # 抖音号驱动时直选平台首行，不再走近似匹配。
    if id_driven:
        r0 = rows[0]
        log(f'  [Collect] 选行(抖音号搜索·平台首行直选): {r0["nick"]} 粉丝≈{r0["fans"]}')
        return r0['row'], r0['nick']

    # 有抖音号时优先行内含抖音号的
    if douyin_id:
        for r in rows:
            if douyin_id in r['row_text']:
                return r['row'], r['nick']

    # 主策略：严格按平台排序（DOM 顺序）从上到下，取第一个可接受的行——
    # 精确等名，或「近似名+真账号信号(粉丝≥1000)」。梦慈麻麻(8.95万)会因此排在
    # 精确等名的梦慈妈妈(203粉LV0空号)之前，符合老大实测口径。
    for r in rows:
        if r['nick'] == name or (_name_close(r['nick'], name) and r['fans'] >= 1000):
            log(f'  [Collect] 选行(按平台排序): {r["nick"]} 粉丝≈{r["fans"]}')
            return r['row'], r['nick']
    # 回退：有精确等名行就选第一个（哪怕粉丝低）
    for r in rows:
        if r['nick'] == name:
            log(f'  [Collect] 选行(回退精确等名): {r["nick"]} 粉丝≈{r["fans"]}')
            return r['row'], r['nick']

    _dump_page(page, 'search_no_result', cid)
    raise RuntimeError(
        f'搜索结果中没有可信的「{name}」：' +
        '；'.join(f'{r["nick"]}({r["fans"]}粉)' for r in rows[:6]) +
        (f'（抖音号 {douyin_id}）' if douyin_id else '建议提供抖音号精准匹配。'))


def _verify_profile_identity(profile, name, picked_name, douyin_id, cid):
    """打开的主页必须是目标达人本人：左侧昵称区前 80 行须出现目标名或选行时命中的昵称。
    防止仿名账号（如「老于波子_」251粉）被当成目标达人入库。
    主页内容异步渲染：先轮询等昵称真正出现（最多 20s），再核验。"""
    # ok_names 只收「真昵称」：纯数字（只给抖音号时 name 就是数字串）不会出现在
    # 主页文本里，放进去必误判
    ok_names = {n for n in (name, picked_name) if n and not n.isdigit()}
    head = ''
    for _ in range(20):
        try:
            if profile.is_closed():
                raise RuntimeError('浏览器页面已被关闭')
            head = profile.evaluate(
                "() => (document.body.innerText || '').slice(0, 4000)") or ''
        except RuntimeError:
            raise
        except Exception:
            return
        if any(l.strip() in ok_names for l in head.split('\n')) or len(head) > 1500:
            break
        time.sleep(1)
    lines = [l.strip() for l in head.split('\n') if l.strip()]
    if any(l in ok_names for l in lines[:80]):
        log(f'  [Collect] 身份核验通过: {picked_name or name}')
        return
    shown = ' / '.join(lines[:6])
    _dump_page(profile, 'identity_mismatch', cid)
    raise RuntimeError(
        f'搜索到的是「{shown}」，不是目标达人「{name}」（可能是仿名/小号）。'
        + ('' if douyin_id else '建议提供抖音号精准匹配。'))


def self_pages(page):
    try:
        return [p.url for p in page.context.pages]
    except Exception:
        return []


def _list_tabs(page):
    """枚举达人主页实际存在的 tab。百应有两版布局（旧版6tab/新版场景分析版），
    固定清单会点空，必须以页面实际 tab 为准。"""
    try:
        container = page.locator('#daren-profile-main-tabs')
        if container.count() == 0:
            return []
        texts = container.first.inner_text(timeout=5000)
        tabs = []
        for t in (texts or '').split('\n'):
            t = t.strip()
            if 2 <= len(t) <= 8 and t not in tabs:
                tabs.append(t)
        return tabs
    except Exception:
        return []


def _click_tab(page, tab_name):
    """在 #daren-profile-main-tabs 容器内点击指定 tab。"""
    try:
        container = page.locator('#daren-profile-main-tabs')
        if container.count() == 0:
            container = page
        tab = container.get_by_text(tab_name, exact=True).first
        tab.wait_for(timeout=8000)
        return _robust_click(tab, f'tab:{tab_name}')
    except Exception as e:
        log(f'  [Collect] tab「{tab_name}」点击失败: {e}')
        return False


def _dom_snapshot(page):
    try:
        txt = page.evaluate("""() => {
            const m = document.querySelector('#daren-profile-main-tabs')
              || document.querySelector('main') || document.body;
            return (m.innerText || '').slice(0, %d);
        }""" % DOM_SNAP_LIMIT)
        return txt or ''
    except Exception:
        return ''


def _ensure_alive(page):
    """浏览器/页面中途被关（人手动关窗口等）时立即中断，避免带着残缺材料出报告。"""
    try:
        if page.is_closed():
            raise RuntimeError('浏览器页面已被关闭')
        if page.context.browser is None or not page.context.browser.is_connected():
            raise RuntimeError('浏览器已被关闭')
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f'浏览器连接异常: {e}')


# ─────────────────────────────── 视频拆解管线 ───────────────────────────────
# 客户/员工给达人 → 自动抓其播放量 TOP N 带货视频 → 下载 → 双路分析：
#   ① lark-cli 妙记转写口播文案（音频路）
#   ② glm-4v-plus 直接「看」视频画面（视觉路）
#   ③ glm-4.7 汇总两路材料 + 平台数据 → 出可复制拆解报告
VIDEO_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS talent_videos (
    id TEXT PRIMARY KEY,
    job_id TEXT DEFAULT '',
    talent_id TEXT DEFAULT '',
    talent_name TEXT DEFAULT '',
    rank INTEGER DEFAULT 0,
    title TEXT DEFAULT '',
    cover_url TEXT DEFAULT '',
    metrics TEXT DEFAULT '{}',
    source_url TEXT DEFAULT '',
    local_path TEXT DEFAULT '',
    transcript TEXT DEFAULT '',
    transcript_status TEXT DEFAULT '',
    visual_desc TEXT DEFAULT '',
    report TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    error TEXT DEFAULT '',
    created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0
)'''
VIDEO_JOB_TABLE_SQL = '''
CREATE TABLE IF NOT EXISTS video_jobs (
    id TEXT PRIMARY KEY,
    talent_id TEXT DEFAULT '',
    talent_name TEXT DEFAULT '',
    top_n INTEGER DEFAULT 3,
    status TEXT DEFAULT 'running',
    stage TEXT DEFAULT '',
    error TEXT DEFAULT '',
    video_ids TEXT DEFAULT '[]',
    created_at INTEGER DEFAULT 0,
    updated_at INTEGER DEFAULT 0
)'''

_MEDIA_URLS = []
_media_lock = threading.Lock()
_media_sniff_on = False


def _ensure_video_tables():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conn.execute(VIDEO_TABLE_SQL)
        conn.execute(VIDEO_JOB_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


def _vdb_insert(table, fields):
    _ensure_video_tables()
    fields = dict(fields)
    fields.setdefault('created_at', int(time.time() * 1000))
    fields.setdefault('updated_at', fields['created_at'])
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conn.execute(
            f'INSERT INTO {table} ({",".join(fields)}) VALUES ({",".join("?" * len(fields))})',
            tuple(fields.values()))
        conn.commit()
    finally:
        conn.close()


def _vdb_update(table, rid, **fields):
    if not fields:
        return
    _ensure_video_tables()
    fields['updated_at'] = int(time.time() * 1000)
    cols = ', '.join(f'{k} = ?' for k in fields)
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        conn.execute(f'UPDATE {table} SET {cols} WHERE id = ?', (*fields.values(), rid))
        conn.commit()
    finally:
        conn.close()


def _media_sniffer_on(ctx):
    """context 级媒体请求嗅探（只需挂一次）。"""
    global _media_sniff_on
    if _media_sniff_on:
        return

    def _on(req):
        u = req.url
        if any(k in u for k in ('.mp4', '.m3u8', '/video/', 'media-')):
            with _media_lock:
                _MEDIA_URLS.append(u)

    ctx.on('request', _on)
    _media_sniff_on = True


def _parse_cn_num(s):
    """'3,411' / '1.2万' / '8900' → int"""
    s = str(s or '').replace(',', '').strip()
    m = re.match(r'^(\d+(?:\.\d+)?)(万|[wW])?', s)
    if not m:
        return 0
    n = float(m.group(1))
    if m.group(2):
        n *= 10000
    return int(n)


def _extract_video_rows(page):
    """视频详情 tab（auxo-table 布局，列可配：用表头映射取列）→ [视频项]。
    视频行特征：首列含发布日期 \\d{4}/\\d{2}/\\d{2}。"""
    raw = page.evaluate("""() => {
      const anchor = document.querySelector('#daren-profile-main-tabs');
      // main-tabs 只含 tab 标签，内容渲染器是其兄弟容器 → 以父级为 root
      const root = (anchor && anchor.parentElement) || document;
      const dbg = {layout: '', heads: []};
      // 定位视频列表所在的「最小」表格容器（含 视频信息+播放量 文本）
      let wrap = null;
      root.querySelectorAll('div[class*="auxo-table"], div[class*="table-wrapper"]').forEach(w => {
        const t = w.innerText || '';
        if (!t.includes('视频信息') || !t.includes('播放量')) return;
        if (!wrap || wrap.contains(w)) wrap = w;   // 取最小的匹配容器
      });
      if (!wrap) {
        // 兜底：直接拿全部行（靠日期过滤）
        wrap = root;
      }
      let heads = Array.from(wrap.querySelectorAll('.auxo-table-thead .auxo-table-cell, .auxo-table-head .auxo-table-cell'))
        .map(h => (h.innerText || '').trim()).filter(Boolean);
      if (!heads.length) {
        const first = wrap.querySelector('.auxo-table-row');
        if (first && (first.innerText || '').includes('视频信息')) {
          heads = Array.from(first.querySelectorAll('.auxo-table-cell')).map(h => (h.innerText || '').trim());
        }
      }
      dbg.heads = heads.slice(0, 12);
      const idxOf = k => heads.findIndex(h => h.includes(k));
      const iTitle = Math.max(0, idxOf('视频信息'));
      const map = {plays: idxOf('播放量'), interact: idxOf('互动率'), gmv: idxOf('结算额'),
                   gpm: idxOf('GPM'), likes: idxOf('点赞'), comments: idxOf('评论'),
                   shares: idxOf('转发'), goods: idxOf('带货商品')};
      const out = [];
      wrap.querySelectorAll('.auxo-table-row').forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll('.auxo-table-cell')).map(td => (td.innerText || '').trim());
        if (cells.length < 3) return;
        const t0 = cells[iTitle] || '';
        if (!/\\d{4}\\/\\d{2}\\/\\d{2}/.test(t0)) return;   // 只留视频行（含发布日期）
        const img = tr.querySelector('img');
        const cover = img ? (img.currentSrc || img.src || img.dataset.src || '') : '';
        const pick = i => (i >= 0 && i < cells.length ? cells[i] : '');
        const k = out.length;
        tr.setAttribute('data-tearank', String(k));   // 给行打标，点击阶段直接用
        out.push({index: k, title: t0.slice(0, 200), cover: cover, cells: cells,
          plays: pick(map.plays), interact: pick(map.interact), gmv: pick(map.gmv),
          gpm: pick(map.gpm), likes: pick(map.likes), comments: pick(map.comments),
          shares: pick(map.shares)});
      });
      dbg.layout = 'auxo-table:' + out.length;
      return {rows: out.slice(0, 30), dbg: dbg};
    }""")
    dbg = (raw or {}).get('dbg') or {}
    log(f'  [Teardown] 视频列表布局={dbg.get("layout")} 表头={dbg.get("heads")}')
    items = []
    for r in (raw or {}).get('rows') or []:
        cells = r.get('cells') or []
        items.append({
            'index': r.get('index', 0),
            'title': r.get('title') or '',
            'cover': r.get('cover') or '',
            'plays': r.get('plays') or '', 'interact': r.get('interact') or '',
            'gmv': r.get('gmv') or '', 'gpm': r.get('gpm') or '',
            'likes': r.get('likes') or '', 'comments': r.get('comments') or '',
            'shares': r.get('shares') or '',
            'cells': cells, 'layout': 'auxo-table',
        })
    return items


def _extract_tab_table_rows(page, marker: str):
    """通用 auxo-table 抓行：视频详情/直播详情等明细 tab → [{cells:[...], title}]。
    marker 用于定位包含该文本的最小表格容器；行首列含日期才保留（滤掉表头/汇总行）。"""
    raw = page.evaluate("""(marker) => {
      const anchor = document.querySelector('#daren-profile-main-tabs');
      const root = (anchor && anchor.parentElement) || document;
      let wrap = null;
      root.querySelectorAll('div[class*="auxo-table"], div[class*="table-wrapper"]').forEach(w => {
        const t = w.innerText || '';
        if (!t.includes(marker)) return;
        if (!wrap || wrap.contains(w)) wrap = w;
      });
      if (!wrap) return {rows: [], layout: 'no-table'};
      const out = [];
      wrap.querySelectorAll('.auxo-table-row').forEach((tr) => {
        const cells = Array.from(tr.querySelectorAll('.auxo-table-cell'))
          .map(td => (td.innerText || '').trim()).filter(c => c !== '');
        if (cells.length < 3) return;
        if (!/\\d{4}\\/\\d{2}\\/\\d{2}/.test(cells[0])) return;   // 只留含日期的明细行
        out.push({title: cells[0].slice(0, 150), cells: cells.slice(0, 10)});
      });
      return {rows: out.slice(0, 30), layout: 'auxo-table:' + out.length};
    }""", marker)
    rows = []
    for r in (raw or {}).get('rows') or []:
        rows.append({'title': r.get('title') or '', 'cells': r.get('cells') or []})
    return rows


def _lark_json(args, timeout=180):
    """调 lark-cli，解析其 stdout 里的第一个完整 JSON 对象。"""
    env = dict(os.environ)
    env['PATH'] = env.get('PATH', '') + ':/opt/homebrew/bin'
    out = subprocess.run([LARK_CLI] + args, capture_output=True, text=True,
                         timeout=timeout, env=env, cwd=str(PROD_DIR))
    txt = (out.stdout or '') + '\n' + (out.stderr or '')
    i = txt.find('{')
    if i < 0:
        raise RuntimeError(f'lark-cli 无 JSON 输出: {txt[:200]}')
    seg = _balanced_json(txt[i:])
    if not seg:
        raise RuntimeError(f'lark-cli JSON 解析失败: {txt[:200]}')
    return json.loads(seg)


def _clean_transcript(raw):
    lines = []
    for ln in raw.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('Keywords:') or re.match(r'^\d{4}-\d{2}-\d{2} ', ln):
            continue
        ln = re.sub(r'^Speaker \d+\s+\d{2}:\d{2}:\d{2}(\.\d+)?\s*', '', ln)
        if ln:
            lines.append(ln)
    return '\n'.join(lines)[:4000]


def _transcribe(video_path, title):
    """飞书妙记转写口播文案。返回 (text, status, err)。"""
    name = (re.sub(r'[^\w\u4e00-\u9fff]+', '_', title)[:40] or 'teardown') + '.mp4'
    try:
        up = _lark_json(['drive', '+upload', '--file', str(video_path), '--name', name], timeout=300)
        ft = (up.get('data') or {}).get('file_token')
        if not ft:
            return '', 'failed', 'drive upload 无 file_token'
        mu = _lark_json(['minutes', '+upload', '--file-token', ft], timeout=120)
        token = (mu.get('data') or {}).get('minute_token') or ''
        if not token:
            url = (mu.get('data') or {}).get('minute_url') or ''
            token = url.rstrip('/').split('/')[-1]
        if not token:
            return '', 'failed', 'minutes +upload 无 minute_token'
        tfile = PROD_DIR / 'minutes' / token / 'transcript.txt'
        text = ''
        for _ in range(6):
            time.sleep(15)
            _lark_json(['vc', '+notes', '--minute-tokens', token], timeout=180)
            if tfile.is_file():
                raw = tfile.read_text(encoding='utf-8', errors='ignore')
                text = _clean_transcript(raw)
                if text:
                    break
        if not text:
            return '', 'failed', '转写超时或为空（可能纯音乐无语音）'
        return text, 'ok', ''
    except Exception as e:
        return '', 'failed', f'{type(e).__name__}: {e}'[:300]


def _vision_watch(video_path, talent_name, item):
    """glm-4v-plus 直接看整条视频，返回画面描述。返回 (desc, err)。"""
    try:
        size = os.path.getsize(str(video_path))
        if size > 50 * 1024 * 1024:
            return '', f'视频 {size // 1048576}MB 超 50MB 上限，跳过画面分析'
        import base64
        b64 = base64.b64encode(Path(video_path).read_bytes()).decode()
        prompt = (
            f'你是短视频带货拆解专家。这是抖音达人「{talent_name}」的一条带货视频'
            f'（播放量 {item.get("plays") or "-"}，点赞 {item.get("likes") or "-"}）。'
            '请仔细看完整条视频后，用中文 300 字以内客观描述：\n'
            '1) 前 3 秒画面是什么（钩子形式）；2) 全程拍了什么（场景/人物/产品/动作）；\n'
            '3) 卖点用什么方式呈现的（口播/字幕/演示/顾客反应）；4) 有没有字幕/花字/价格贴片，什么样式；\n'
            '5) 结尾怎么收的、有没有引导动作。只描述你真实看到的，不要编造。')
        base, key = _zhipu_cfg()
        req = urllib.request.Request(
            base + '/chat/completions',
            data=json.dumps({
                'model': 'glm-4v-plus',
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'video_url', 'video_url': {'url': f'data:video/mp4;base64,{b64}'}},
                ]}],
                'temperature': 0.0,
            }).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
            method='POST')
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        desc = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
        if not desc.strip():
            return '', 'glm-4v-plus 空返回'
        return desc.strip()[:3000], ''
    except urllib.error.HTTPError as e:
        return '', f'glm-4v-plus http {e.code}: {e.read()[:200]}'
    except Exception as e:
        return '', f'{type(e).__name__}: {e}'[:300]


def _teardown_report(talent_name, item, transcript, visual_desc):
    """glm-4.7 汇总两路材料 → 可复制拆解报告。"""
    prompt = f"""你是短视频带货拆解专家。基于以下三路材料，产出一份可直接给商务团队参考的「带货视频拆解报告」(Markdown)。

【达人】{talent_name}
【平台数据】播放量 {item.get('plays') or '-'} | 互动率 {item.get('interact') or '-'} | 单视频结算额 {item.get('gmv') or '-'} | 视频GPM {item.get('gpm') or '-'} | 赞 {item.get('likes') or '-'} / 评 {item.get('comments') or '-'} / 转 {item.get('shares') or '-'}
【口播转写】{(transcript or '(无语音，纯音乐/无人声)')[:1500]}
【画面描述(glm-4v-plus 实看)】{(visual_desc or '(画面分析失败)')[:1500]}

报告必须包含:
## 钩子(前3秒)
## 口播文案骨架
## 卖点呈现方式
## 节奏与剪辑
## 字幕与包装
## 结尾引导
## 可复制清单(表格: 元素 | 怎么抄)
## 数据诊断(播放量/互动率/GPM 哪里强哪里弱，直说)

要求: 只基于给定材料推理，标注不确定处；数据弱就直说弱；总长度 600 字内，清单具体可执行。"""
    try:
        return llm_chat([{'role': 'user', 'content': prompt}], max_tokens=2500, timeout=180)
    except Exception as e:
        return f'(拆解报告生成失败: {e})'


def _capture_one_video(profile, vid, dom_index, item, talent_name):
    """点开一个视频行 → 嗅探源地址 → 下载 → 转写 → 看画面 → 报告。"""
    _vdb_update('talent_videos', vid, status='downloading')
    with _media_lock:
        before = len(_MEDIA_URLS)

    clicked = False
    sels = (f'[data-tearank="{dom_index}"] img',
            f'[data-tearank="{dom_index}"] .auxo-table-cell >> nth=0')
    for sel in sels:
        try:
            el = profile.locator(sel).first
            if el.count() > 0 and el.is_visible():
                if _robust_click(el, f'video-row-{dom_index}'):
                    clicked = True
                    break
        except Exception:
            continue
    if not clicked:
        raise RuntimeError('视频行点击失败')

    src = ''
    for _ in range(2):
        _human_sleep(6, 9)
        try:
            srcs = profile.evaluate("""() => Array.from(document.querySelectorAll('video'))
                .map(v => v.src || (v.querySelector('source')||{}).src || '').filter(Boolean)""")
        except Exception:
            srcs = []
        with _media_lock:
            fresh = [u for u in _MEDIA_URLS[before:] if 'mime_type=video_mp4' in u or '.mp4' in u]
        src = (srcs[0] if srcs else (fresh[-1] if fresh else ''))
        if src:
            break
    if not src:
        raise RuntimeError('未嗅探到视频源地址')

    resp = profile.context.request.get(src, headers={'Referer': 'https://buyin.jinritemai.com/'},
                                        timeout=120000)
    body = resp.body()
    if not body or len(body) < 10000:
        raise RuntimeError(f'下载内容异常 {len(body)}B')
    safe_name = re.sub(r'[^\w\u4e00-\u9fff]+', '_', talent_name)[:20] or 'talent'
    path = VIDEOS_DIR / f'{vid}_{safe_name}.mp4'
    path.write_bytes(body)
    try:
        profile.keyboard.press('Escape')
    except Exception:
        pass
    _human_sleep(0.8, 1.5)
    _vdb_update('talent_videos', vid, status='transcribing',
                source_url=src[:500], local_path=str(path))

    transcript, tstatus, terr = _transcribe(path, item.get('title') or vid)
    _vdb_update('talent_videos', vid, transcript=transcript, transcript_status=tstatus,
                error=terr)

    _vdb_update('talent_videos', vid, status='watching')
    visual_desc, verr = _vision_watch(path, talent_name, item)
    if verr:
        log(f'  [Teardown] {vid} 画面分析跳过: {verr}')
    _vdb_update('talent_videos', vid, visual_desc=visual_desc)

    _vdb_update('talent_videos', vid, status='analyzing')
    report = _teardown_report(talent_name, item, transcript, visual_desc)
    _vdb_update('talent_videos', vid, status='done', report=report)


def teardown_pipeline(job_id, talent_id, name, douyin_id, top_n=3):
    """视频拆解主流程：搜达人 → 视频详情 tab → 播放量 TOP N → 逐条下载+双路分析。"""
    with BROWSER.lock:
        page = BROWSER.ensure()
        _media_sniffer_on(page.context)
        _vdb_update('video_jobs', job_id, stage='打开达人主页')
        profile, profile_name = _search_and_open(page, name, douyin_id, job_id)
        _human_sleep(3, 5)
        _check_login_state(profile)
        talent_name = profile_name or name

        _vdb_update('video_jobs', job_id, stage='读取视频详情 tab')
        if not _click_tab(profile, '视频详情'):
            raise RuntimeError('视频详情 tab 点击失败')
        _human_sleep(2.5, 3.5)
        for _ in range(3):
            _ensure_alive(profile)
            profile.mouse.wheel(0, 1500)
            _human_sleep(1.2, 2.0)

        rows = _extract_video_rows(profile)
        if not rows:
            _dump_page(profile, 'no_video_rows', job_id)
            raise RuntimeError('视频详情 tab 未解析到视频列表（页面结构可能变化，现场已留存）')
        for r in rows:
            r['plays_n'] = _parse_cn_num(r.get('plays'))
        rows.sort(key=lambda r: -r['plays_n'])
        picked = rows[:max(1, min(top_n, len(rows)))]
        log(f'[Teardown] {job_id} 解析到 {len(rows)} 条视频，取 TOP{len(picked)}')

        _vdb_update('video_jobs', job_id, stage=f'逐条处理 TOP{len(picked)} 视频')
        video_ids = []
        for rank, item in enumerate(picked, 1):
            vid = f"vid_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
            video_ids.append(vid)
            _vdb_insert('talent_videos', {
                'id': vid, 'job_id': job_id, 'talent_id': talent_id,
                'talent_name': talent_name, 'rank': rank,
                'title': item.get('title', ''), 'cover_url': item.get('cover', ''),
                'metrics': json.dumps({
                    'plays': item.get('plays'), 'interact': item.get('interact'),
                    'gmv': item.get('gmv'), 'gpm': item.get('gpm'),
                    'likes': item.get('likes'), 'comments': item.get('comments'),
                    'shares': item.get('shares'),
                }, ensure_ascii=False),
                'status': 'pending',
            })
            _ensure_alive(profile)
            try:
                log(f'[Teardown] {job_id} 第{rank}条 (DOM行{item["index"]}) 播放{item.get("plays")}')
                _capture_one_video(profile, vid, item['index'], item, talent_name)
            except Exception as e:
                err = f'{type(e).__name__}: {e}'[:300]
                log(f'[Teardown] {vid} 失败: {err}')
                _vdb_update('talent_videos', vid, status='failed', error=err)
                try:
                    profile.keyboard.press('Escape')
                except Exception:
                    pass
                _human_sleep(1.0, 2.0)

        done_n = 0
        conn = sqlite3.connect(str(DB_PATH), timeout=10)
        try:
            done_n = conn.execute(
                "SELECT COUNT(*) FROM talent_videos WHERE job_id = ? AND status = 'done'",
                (job_id,)).fetchone()[0]
        finally:
            conn.close()
        _vdb_update('video_jobs', job_id,
                    video_ids=json.dumps(video_ids, ensure_ascii=False),
                    stage='完成' if done_n else '全部失败')
        if not done_n:
            raise RuntimeError(f'TOP{len(picked)} 条视频全部处理失败')


def _run_teardown_job(row):
    job_id = row['job_id']
    log(f'[Teardown] 任务 {job_id} 开始: {row.get("name")} TOP{row.get("top_n", 3)}')
    try:
        teardown_pipeline(job_id, row.get('talent_id', ''), row['name'],
                          row.get('douyin_id', ''), row.get('top_n', 3))
        _vdb_update('video_jobs', job_id, status='done', stage='完成')
        log(f'[Teardown] 任务 {job_id} 完成')
    except NeedLogin as e:
        _vdb_update('video_jobs', job_id, status='failed',
                    error=f'百应登录态失效，需在浏览器窗口重新扫码登录: {e}')
    except Exception as e:
        err = f'{type(e).__name__}: {e}'[:400]
        log(f'[Teardown] 任务 {job_id} 失败: {err}')
        _vdb_update('video_jobs', job_id, status='failed', error=err)


def teardown_url_pipeline(job_id, url):
    """直链视频拆解：打开链接 → 嗅探/提取视频源 → 下载 → 转写 → 看画面 → 拆解报告。
    给达人机器人用：客户发任意抖音视频链接，无需百应页面有该达人数据。"""
    with BROWSER.lock:
        page = BROWSER.ensure()
        _media_sniffer_on(page.context)
        _vdb_update('video_jobs', job_id, stage='打开视频链接')
        page.goto(url, timeout=60000, wait_until='domcontentloaded')
        _human_sleep(5, 8)
        final_url = page.url
        try:
            title = (page.title() or '').strip()
        except Exception:
            title = ''
        title = re.sub(r'[【\[].*?[\]】]', '', title).split('-')[0].strip()[:60] or '视频'

        vid = f"vid_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
        item = {'plays': '', 'interact': '', 'gmv': '', 'gpm': '',
                'likes': '', 'comments': '', 'shares': ''}
        _vdb_insert('talent_videos', {
            'id': vid, 'job_id': job_id, 'talent_id': '', 'talent_name': title,
            'rank': 1, 'title': title, 'cover_url': '',
            'metrics': json.dumps(item, ensure_ascii=False),
            'status': 'pending', 'source_url': final_url[:500],
        })
        try:
            with _media_lock:
                before = len(_MEDIA_URLS)
            src = ''
            for _ in range(4):
                _ensure_alive(page)
                _human_sleep(4, 6)
                try:
                    srcs = page.evaluate("""() => Array.from(document.querySelectorAll('video'))
                        .map(v => v.src || (v.querySelector('source')||{}).src || '').filter(Boolean)""")
                except Exception:
                    srcs = []
                srcs = [s for s in (srcs or []) if not str(s).startswith('blob:')]
                with _media_lock:
                    fresh = [u for u in _MEDIA_URLS[before:]
                             if 'mime_type=video_mp4' in u or '.mp4' in u]
                # 抖音 web 播放器是 blob:MSE，真实 mp4 要走网络嗅探；嗅探优先，DOM src 兜底
                src = (fresh[-1] if fresh else (srcs[0] if srcs else ''))
                if src:
                    break
            if not src:
                raise RuntimeError('未找到视频源地址（页面可能要求登录或链接已失效）')
            resp = page.context.request.get(
                src, headers={'Referer': 'https://www.douyin.com/'}, timeout=120000)
            body = resp.body()
            if not body or len(body) < 10000:
                raise RuntimeError(f'下载内容异常 {len(body)}B')
            path = VIDEOS_DIR / f'{vid}_direct.mp4'
            path.write_bytes(body)
            _vdb_update('talent_videos', vid, status='downloading', local_path=str(path))

            transcript, tstatus, terr = _transcribe(path, title)
            _vdb_update('talent_videos', vid, transcript=transcript,
                        transcript_status=tstatus, error=terr)

            _vdb_update('talent_videos', vid, status='watching')
            visual_desc, verr = _vision_watch(path, title, item)
            if verr:
                log(f'  [TeardownURL] {vid} 画面分析跳过: {verr}')
            _vdb_update('talent_videos', vid, visual_desc=visual_desc)

            _vdb_update('talent_videos', vid, status='analyzing')
            report = _teardown_report(title, item, transcript, visual_desc)
            _vdb_update('talent_videos', vid, status='done', report=report)
            _vdb_update('video_jobs', job_id, video_ids=json.dumps([vid]), stage='完成')
        except Exception as e:
            err = f'{type(e).__name__}: {e}'[:300]
            _vdb_update('talent_videos', vid, status='failed', error=err)
            raise


def _run_teardown_url_job(row):
    job_id = row['job_id']
    log(f'[TeardownURL] 任务 {job_id} 开始: {row.get("url")}')
    try:
        teardown_url_pipeline(job_id, row['url'])
        _vdb_update('video_jobs', job_id, status='done', stage='完成')
        log(f'[TeardownURL] 任务 {job_id} 完成')
    except Exception as e:
        err = f'{type(e).__name__}: {e}'[:400]
        log(f'[TeardownURL] 任务 {job_id} 失败: {err}')
        _vdb_update('video_jobs', job_id, status='failed', error=err)


def collect_pipeline(collect_id, name, douyin_id):
    """执行一次完整采集，返回 dict(原始材料+截图清单)。"""
    with BROWSER.lock:
        page = BROWSER.ensure()
        job_shots = []
        tabs_dom = {}
        all_xhr = []

        profile, profile_name = _search_and_open(page, name, douyin_id, collect_id)
        _human_sleep(3, 5)
        _check_login_state(profile)

        safe = re.sub(r'[^\w\u4e00-\u9fff]+', '_', name)[:40]
        shot = SHOTS_DIR / f'{collect_id}_{safe}_profile.png'
        try:
            profile.screenshot(path=str(shot), full_page=False)
            job_shots.append(str(shot))
        except Exception:
            pass

        bucket = []
        _attach_sniffer(profile, bucket)

        # 动态枚举实际存在的 tab（新旧两版布局都兼容），枚举失败退回固定清单
        live_tabs = _list_tabs(profile)
        tabs_to_visit = [t for t in live_tabs if t in TABS] + \
                        [t for t in live_tabs if t not in TABS]
        if not tabs_to_visit:
            tabs_to_visit = TABS
        log(f'  [Collect] 本轮 tab 清单: {tabs_to_visit}')

        for tab in tabs_to_visit:
            _ensure_alive(profile)
            _human_sleep(1.2, 2.0)
            if not _click_tab(profile, tab):
                continue
            _human_sleep(2.2, 3.5)   # 等 tab 内容/接口加载
            tabs_dom[tab] = _dom_snapshot(profile)
            shot = SHOTS_DIR / f'{collect_id}_{safe}_{tab}.png'
            try:
                profile.screenshot(path=str(shot))
                job_shots.append(str(shot))
            except Exception:
                pass
            # 下滚补拍一张：性别/年龄分布图、视频列表、商品表格、评价明细都在首屏之下
            try:
                profile.mouse.wheel(0, 850)
                _human_sleep(1.0, 1.8)
                shot2 = SHOTS_DIR / f'{collect_id}_{safe}_{tab}_下.png'
                profile.screenshot(path=str(shot2))
                job_shots.append(str(shot2))
                profile.mouse.wheel(0, -850)
                _human_sleep(0.5, 1.0)
            except Exception:
                pass
            all_xhr.append({'tab': tab, 'xhr': bucket[-XHR_MAX_PER_TAB:]})

        # ── 带货分析：点开 TOP 商品明细（可点的行）──
        _ensure_alive(profile)
        top_products = []
        try:
            if _click_tab(profile, '带货分析'):
                _human_sleep(2.5, 3.5)
                rows = profile.locator('table tbody tr')
                rn = min(rows.count(), 5)
                for i in range(rn):
                    try:
                        cell = rows.nth(i).locator('td').nth(1)
                        if cell.count() == 0:
                            continue
                        cell.click()
                        _human_sleep(1.8, 2.8)
                        detail_txt = _dom_snapshot(profile)
                        top_products.append({'row': i + 1, 'detail_text': detail_txt[:3000]})
                        try:
                            profile.keyboard.press('Escape')
                        except Exception:
                            pass
                        _human_sleep(0.8, 1.5)
                    except Exception:
                        continue
        except Exception as e:
            log(f'  [Collect] TOP商品明细点击失败（非致命）: {e}')

        # ── 视频/直播明细：滚动加载更多 + 抓表格行（报告端「近期明细」展示）──
        _ensure_alive(profile)
        details = {}
        for tab_key, out_key, marker in (('视频详情', 'video_details', '视频信息'),
                                         ('直播详情', 'live_details', '直播')):
            try:
                if not _click_tab(profile, tab_key):
                    continue
                _human_sleep(2.0, 3.0)
                rows = _extract_tab_table_rows(profile, marker)
                for _ in range(2):
                    profile.mouse.wheel(0, 1200)
                    _human_sleep(1.0, 1.8)
                if tab_key == '视频详情':
                    tabs_dom['视频详情_滚动后'] = _dom_snapshot(profile)
                rows2 = _extract_tab_table_rows(profile, marker)
                seen, merged_rows = set(), []
                for r in rows + rows2:
                    key = ((r.get('cells') or [''])[0]) or r.get('title') or ''
                    if key and key not in seen:
                        seen.add(key)
                        merged_rows.append(r)
                details[out_key] = merged_rows[:12]
                log(f'  [Collect] {tab_key} 明细抓取 {len(merged_rows)} 行')
            except Exception as e:
                log(f'  [Collect] {tab_key} 明细抓取失败（非致命）: {e}')

        xhr_dump = []
        for group in all_xhr:
            keep = []
            for item in group['xhr']:
                s = json.dumps(item['body'], ensure_ascii=False)
                if len(s) > 4000:
                    s = s[:4000] + '…(截断)'
                keep.append({'url': item['url'], 'body': s})
            xhr_dump.append({'tab': group['tab'], 'xhr': keep})

        return {
            'tabs_dom': tabs_dom,
            'xhr_dump': xhr_dump,
            'top_products': top_products,
            'details': details,
            'shots': job_shots,
            'profile_url': profile.url,
            'profile_name': profile_name,
        }


def _talent_columns():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    try:
        rows = conn.execute('PRAGMA table_info(talents)').fetchall()
        return {r[1] for r in rows}
    finally:
        conn.close()


_TALENT_COLS = None


def _normalize_fields(fields):
    """写库前规整：dict/list→JSON字符串，bool→int，None 丢弃（保留库中已有值），
    并过滤到 talents 表真实存在的列。"""
    global _TALENT_COLS
    if _TALENT_COLS is None:
        _TALENT_COLS = _talent_columns()
    out = {}
    for k, v in fields.items():
        if v is None or k not in _TALENT_COLS:
            continue
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        elif isinstance(v, bool):
            out[k] = int(v)
        else:
            out[k] = v
    return out


def _fill_missing_fields(fields):
    """LLM 提取漏字段时兜底：
    - followers 缺失/为0 且 fan_gender 是数值字典 → 用性别分布求和回填
      （实测王悦悦呀：followers=None 但 fan_gender 合计 43110 ≈ 4.3万）"""
    try:
        f = fields.get('followers')
        if (f is None or (isinstance(f, (int, float)) and f <= 0)
                or (isinstance(f, str) and not f.strip().isdigit())):
            fg = fields.get('fan_gender')
            if isinstance(fg, str):
                fg = json.loads(fg)
            if isinstance(fg, dict):
                total = 0
                for v in fg.values():
                    try:
                        total += int(float(v))
                    except (TypeError, ValueError):
                        pass
                if total > 0:
                    fields['followers'] = total
                    log(f'  [Collect] followers 由粉丝性别分布回填: {total}')
    except Exception as e:
        log(f'  [Collect] followers 兜底失败（非致命）: {e}')


# ─────────────────────────────── 服务器 API ───────────────────────────────
_admin_token_cache = {'token': None, 'ts': 0}
CRED_FILE = BROWSER_DIR / 'credentials.json'


def admin_token():
    """获取（并缓存）管理员 Bearer token：自动采集数据属系统数据，写主库需要管理员身份。"""
    if _admin_token_cache['token'] and time.time() - _admin_token_cache['ts'] < 3600 * 20:
        return _admin_token_cache['token']
    creds = json.load(open(CRED_FILE, encoding='utf-8'))
    req = urllib.request.Request(
        SERVER_BASE + '/api/auth/login',
        data=json.dumps({'username': creds['username'], 'password': creds['password']}).encode('utf-8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    tok = data.get('token')
    if not tok:
        raise RuntimeError('管理员登录失败: ' + str(data)[:200])
    _admin_token_cache['token'] = tok
    _admin_token_cache['ts'] = time.time()
    return tok


def api(method, path, body=None, timeout=30, admin=False):
    headers = {'Content-Type': 'application/json'}
    if admin:
        headers['Authorization'] = 'Bearer ' + admin_token()
    else:
        headers['X-Agent-Id'] = WORKER_AGENT_ID
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    req = urllib.request.Request(SERVER_BASE + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def db_pending():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT * FROM talent_collects WHERE status = 'pending' ORDER BY created_at ASC LIMIT 5"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─────────────────────────────── 任务执行 ───────────────────────────────
JOBS = {}          # collect_id -> {status, stage, started, error, shots}
_job_lock = threading.Lock()
_running = threading.Event()
JOB_Q = __import__('queue').Queue()
STATE = {'browser_up': False, 'logged_in': False,
         'daren_browser_up': False, 'daren_logged_in': False}   # 浏览器线程维护，HTTP线程只读


def _job_set(cid, **kw):
    with _job_lock:
        JOBS.setdefault(cid, {}).update(kw)


def _state_set(**kw):
    with _job_lock:
        STATE.update(kw)


def submit_collect(row):
    """任意线程调用：把任务放入队列，由唯一浏览器线程执行。"""
    JOB_Q.put(row)


class BrowserLoopPoisoned(Exception):
    """浏览器线程的 asyncio 事件循环被污染（Chromium 崩溃后遗症），
    本线程已无法再用 sync_playwright，必须换新线程。"""


def _reset_browser_instances():
    """丢弃被污染的 Browser 实例状态（旧线程里的残留 loop 随线程消亡）。"""
    for b in (BROWSER, DAREN_BROWSER):
        try:
            b.ctx = None
            b.page = None
            b._pw = None
        except Exception:
            pass


_browser_thread = None


def _ensure_browser_thread():
    """浏览器工作线程托管：挂了或中毒退出后由下一个任务触发重建。"""
    global _browser_thread
    if _browser_thread is None or not _browser_thread.is_alive():
        _browser_thread = threading.Thread(target=_browser_loop, daemon=True)
        _browser_thread.start()
        log('[BrowserLoop] 工作线程已启动')


def _browser_loop():
    """Playwright  sync API 要求对象只在创建它的线程使用——
    所有浏览器操作集中在这一条线程。"""
    log('[BrowserLoop] 浏览器工作线程启动')
    while True:
        row = JOB_Q.get()
        if row is None:
            break
        if row.get('_open_browser'):
            try:
                page = BROWSER.ensure()
                page.goto(BUYIN_HOME, timeout=60000)
                log('[Control] 已打开百应首页（如需登录请扫码）')
            except Exception as e:
                log(f'[Control] 打开浏览器失败: {e}')
            continue
        if row.get('_open_daren_browser'):
            try:
                page = DAREN_BROWSER.ensure()
                page.goto(DAREN_HOME, timeout=60000)
                page.wait_for_load_state('domcontentloaded')
                _human_sleep(3, 5)
                # 未登录时：官网右上角「登录」→ 选「达人工作台」
                if 'login' in page.url or DAREN_BROWSER.health()[1] is False:
                    try:
                        page.locator('text=登录').first.click(timeout=8000)
                        _human_sleep(1.5, 2.5)
                        page.locator('text=达人工作台').first.click(timeout=8000)
                        _human_sleep(3, 5)
                    except Exception as e:
                        log(f'[Control] 官网登录入口点击失败（可人工操作）: {e}')
                log(f'[Control] 达人端入口已打开 url={page.url}')
            except Exception as e:
                log(f'[Control] 打开达人端浏览器失败: {e}')
            continue
        if row.get('_graceful_daren'):
            try:
                DAREN_BROWSER.close()
                log('[Control] 达人端浏览器已优雅关闭（cookie 已落盘）')
            except Exception as e:
                log(f'[Control] 优雅关闭失败: {e}')
            continue
        if row.get('_probe_daren'):
            try:
                info = {'up': DAREN_BROWSER.up(), 'url': None, 'cookies': []}
                if DAREN_BROWSER.up() and DAREN_BROWSER.page and not DAREN_BROWSER.page.is_closed():
                    info['url'] = DAREN_BROWSER.page.url
                    cs = DAREN_BROWSER.ctx.cookies()
                    info['cookies'] = sorted({f"{c.get('domain','')}:{c.get('name')}" for c in cs})
                with _job_lock:
                    STATE['daren_probe'] = info
                log(f"[Control] probe: url={info['url']} cookies={len(info['cookies'])}")
            except Exception as e:
                log(f'[Control] probe 失败: {e}')
            continue
        try:
            # 顺手刷新登录态标记（供 /health 读取）
            try:
                up, logged = BROWSER.health()
                dup, dlogged = DAREN_BROWSER.health()
                _state_set(browser_up=up, logged_in=logged,
                           daren_browser_up=dup, daren_logged_in=dlogged)
            except Exception:
                pass
            if row.get('_teardown_url'):
                _run_teardown_url_job(row)
            elif row.get('_teardown'):
                _run_teardown_job(row)
            elif row.get('_product'):
                _run_product_impl(row)
            elif row.get('_product_url'):
                _run_product_url_impl(row)
            else:
                _run_collect_impl(row)
        except BrowserLoopPoisoned:
            # 事件循环中毒：本线程已废，重置实例、任务重新排队、新开线程接手
            log('[BrowserLoop] 检测到 asyncio 循环污染，重启工作线程自愈')
            _reset_browser_instances()
            JOB_Q.put(row)
            _ensure_browser_thread()
            break
        except Exception as e:
            log(f'[BrowserLoop] 未捕获异常: {e}\n{traceback.format_exc()[:800]}')
        try:
            up, logged = BROWSER.health()
            dup, dlogged = DAREN_BROWSER.health()
            _state_set(browser_up=up, logged_in=logged,
                       daren_browser_up=dup, daren_logged_in=dlogged)
        except Exception:
            pass


PRODUCT_URL_PROMPT = '''你是电商商品信息提取器。从抖音电商商品页的标题和正文里提取以下字段，只输出 JSON（不要输出任何其他文字）：
{"name":"商品名","price":"到手价(字符串,带¥)","commission":"佣金(字符串)","commission_rate":"佣金率(字符串,百分数)","sales":"销量(字符串)","shop":"店铺名","category":"类目","shop_score":"店铺体验分(字符串,没有则空)","highlights":"核心卖点(一句话,60字内)","target_crowd":"目标人群(一句话)","coupon":"优惠信息(字符串,没有则空)"}
规则：
1. 只提取页面里明确出现的字段，没有的填空字符串，绝对不要编造数字（尤其价格/佣金/销量）。
2. 页面可能有多个商品，取主商品（标题对应的那个）。
3. 金额保持页面原文格式（如 ¥29.9、29.9元）。'''


def _llm_extract_product(title: str, text: str) -> dict:
    """商品页正文 → 结构化字段。glm-4-flash 优先，失败退 glm-4.7。"""
    messages = [{'role': 'user', 'content':
                 PRODUCT_URL_PROMPT + f'\n【页面标题】{title}\n【页面正文】\n{text}'}]
    for model in ('glm-4-flash', 'glm-4.7'):
        try:
            content = llm_chat(messages, max_tokens=1500, model=model)
        except Exception as e:
            log(f'[ProductURL] llm({model}) 失败: {e}')
            continue
        raw = _balanced_json(content)
        if raw:
            try:
                d = json.loads(raw)
                if isinstance(d, dict) and d.get('name'):
                    return d
            except Exception:
                pass
    raise RuntimeError('AI 提取商品字段失败')


def _run_product_url_impl(row):
    """商品链接采集（团长端浏览器）：打开商品页 → 取正文 → LLM 结构化 → result 落 job。
    result = JSON 字符串：{name,price,commission,commission_rate,sales,shop,category,
    shop_score,highlights,target_crowd,coupon,resolved_url}"""
    cid, url = row['id'], (row.get('url') or '').strip()
    _job_set(cid, status='collecting', stage='启动团长端浏览器', started=time.time())
    log(f'[ProductURL] {cid} 开始商品链接采集: {url}')
    try:
        page = BROWSER.ensure()
        _job_set(cid, stage='打开商品页')
        page.goto(url, timeout=60000)
        page.wait_for_load_state('domcontentloaded')
        _human_sleep(3, 5)
        resolved = page.url
        try:
            _check_login_state(page)
        except NeedLogin:
            # 短链可能跳到非百应域名（精选联盟详情页也在百应域），仅百应域校验 cookie
            if 'jinritemai.com' in resolved:
                raise
            log(f'[ProductURL] {cid} 非百应域名({resolved})，跳过 cookie 校验')
        _job_set(cid, stage='提取页面内容')
        title = (page.title() or '')[:200]
        text = page.evaluate(
            "() => (document.body ? (document.body.innerText || '') : '')")[:9000]
        if len(text.strip()) < 50:
            raise RuntimeError('页面正文过短，可能是登录墙或页面未加载完成')
        _job_set(cid, stage='AI 提取商品字段')
        fields = _llm_extract_product(title, text)
        fields['resolved_url'] = resolved
        _job_set(cid, status='done', stage='完成',
                 result=json.dumps(fields, ensure_ascii=False))
        log(f'[ProductURL] {cid} 完成: {list(fields.keys())[:12]}')
    except NeedLogin as e:
        err = f'团长端登录态缺失: {e}'
        log(f'[ProductURL] {cid} {err}')
        _job_set(cid, status='need_login', error=err)
    except BrowserLoopPoisoned:
        raise
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        log(f'[ProductURL] {cid} 失败: {err}\n{traceback.format_exc()[:1200]}')
        try:
            if BROWSER.page:
                _dump_page(BROWSER.page, 'fatal', cid)
        except Exception:
            pass
        _job_set(cid, status='failed', error=err)


def _run_product_impl(row):
    """商品库采集（达人端浏览器）。v1=DOM 侦察：打开目标页并留存现场，
    人工确认达人端商品页结构后再升级为完整解析管线。"""
    cid = row['id']
    url, name, shop = row.get('url') or '', row.get('name') or '', row.get('shop') or ''
    _job_set(cid, status='collecting', stage='启动达人端浏览器', started=time.time())
    log(f'[ProductJob] {cid} 开始商品采集: url={url} name={name} shop={shop}')
    try:
        page = DAREN_BROWSER.ensure()
        _job_set(cid, stage='打开商品页')
        if url:
            page.goto(url, timeout=60000)
            page.wait_for_load_state('domcontentloaded')
            _human_sleep(3, 5)
        # 无 url 时不导航：直接看当前页（不打断人工登录流程）
        # 侦察阶段：登录检查软处理（cookie 名待 probe 确认，失败也留存现场）
        try:
            _check_login_state(page)
        except NeedLogin as e:
            log(f'[ProductJob] {cid} 登录态未确认（继续侦察）: {e}')
        # 有 url 且停在角色选择页时，自动进入达人工作台（无 url 的纯侦察不点，避免打断人工操作）
        if url and 'roles-select' in page.url:
            _job_set(cid, stage='进入达人工作台')
            page.get_by_text('登录达人工作台', exact=True).first.click(timeout=8000)
            page.wait_for_load_state('domcontentloaded')
            _human_sleep(4, 6)
            log(f'[ProductJob] {cid} 点击达人工作台后 url={page.url}')
        _job_set(cid, stage='留存现场（DOM 侦察）')
        _dump_page(page, f'product_{name or "url"}', cid)
        _job_set(cid, status='recon_done', stage='现场已留存，等待解析规则')
        log(f'[ProductJob] {cid} 侦察完成 url={page.url}')
    except NeedLogin as e:
        err = f'达人端登录态缺失，请先扫码登录: {e}'
        log(f'[ProductJob] {cid} {err}')
        _job_set(cid, status='need_login', error=err)
    except BrowserLoopPoisoned:
        raise
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        log(f'[ProductJob] {cid} 失败: {err}\n{traceback.format_exc()[:1200]}')
        try:
            if DAREN_BROWSER.page:
                _dump_page(DAREN_BROWSER.page, 'fatal', cid)
        except Exception:
            pass
        _job_set(cid, status='failed', error=err)


def _run_collect_impl(row):
    cid = row['id']
    name, douyin_id = row['name'], row.get('douyin_id') or ''
    _job_set(cid, status='collecting', stage='启动浏览器', started=time.time())
    log(f'[Job] {cid} 开始采集: {name} {douyin_id}')

    try:
        api('POST', f'/api/talent-collects/{cid}', {'status': 'collecting'})
    except Exception as e:
        log(f'[Job] {cid} 认领失败: {e}')
        return

    try:
        _job_set(cid, stage='浏览器采集（搜索→主页→逐tab）')
        material = collect_pipeline(cid, name, douyin_id)

        _job_set(cid, stage='AI 结构化提取')
        fields = extract_fields(name, douyin_id, material['tabs_dom'], material['xhr_dump'])
        fields['name'] = fields.get('name') or name
        # 纯抖音号下单时 name 是数字串——用主页核验过的真实昵称覆盖
        _pn = material.get('profile_name') or ''
        if _pn and (not fields.get('name') or str(fields['name']).isdigit()):
            fields['name'] = _pn
            log(f'[Job] {cid} 名字回填为首页昵称: {_pn}')
        if douyin_id and not fields.get('douyin_id'):
            fields['douyin_id'] = douyin_id
        fields['platform'] = '抖音'

        # ── 视觉补齐：glm-4v-plus 直接读 tab 截图，覆盖 DOM 提取缺失/不准的字段 ──
        _job_set(cid, stage='AI 视觉提取（读截图补齐字段）')
        vf = _vision_extract_fields(material.get('shots') or [])
        merged = 0
        for k, v in vf.items():
            if _vision_junk_value(v):
                continue                    # 零值/占位垃圾：不写入，更不覆盖已有好数据
            # followers 单位事故防护：视觉把 "1.2万" 读成浮点 1.2，比 DOM 值小一个量级时不采纳
            if k == 'followers':
                try:
                    _dom_f = float(fields[k])
                    _vis_f = float(v)
                    if _dom_f >= 1000 and _vis_f < _dom_f / 10:
                        log(f'[Job] {cid} 视觉followers单位疑似丢失({_vis_f})，保留DOM({_dom_f})')
                        continue
                except (TypeError, ValueError):
                    pass
            fields[k] = v
            merged += 1
        if merged:
            log(f'[Job] {cid} 视觉提取补齐/覆盖 {merged} 个字段')
        # 视觉原始提取全量留档（含无专属列的字段：评价明细/视频趋势/标签等），报告端从这里取
        det = material.get('details') or {}
        if vf or det:
            raw_all = dict(vf)
            for _k in ('video_details', 'live_details'):
                if det.get(_k):
                    raw_all[_k] = det[_k]
                    fields[_k] = det[_k]          # 分析输入用（list），normalize 时会滤掉非表列
            fields['ocr_raw_fields'] = json.dumps(raw_all, ensure_ascii=False)

        provenance = f'buyin_worker 自动采集 {time.strftime("%Y-%m-%d %H:%M")}'
        notes = summarize_for_notes(fields)
        notes = (notes + '｜' if notes else '') + provenance
        fields['notes'] = notes
        if material.get('top_products'):
            fields['top_products'] = json.dumps(
                [p.get('detail_text', '')[:500] for p in material['top_products']],
                ensure_ascii=False)
        log(f'[Job] {cid} 提取完成: {list(fields.keys())[:12]}…')
        _fill_missing_fields(fields)
        fields = _normalize_fields(fields)
        # 已知视觉脏值清理：月结算与件单价数值相同 = 量级读错（带百品月结¥129.72 不可能）
        try:
            import re as _re
            _ms = _re.sub(r'[^\d.]', '', str(fields.get('monthly_settlement') or ''))
            if _ms and fields.get('average_price') and int(fields.get('total_products') or 0) > 20 \
                    and abs(float(_ms) - float(fields['average_price'])) < 0.01:
                fields.pop('monthly_settlement', None)
                log(f'[Job] {cid} 清理脏值 monthly_settlement（与件单价同值）')
        except Exception:
            pass

        # ── 团长视角合作评估（ai_analysis/risk_rating 落库，报告端展示）──
        _job_set(cid, stage='AI 合作评估')
        verdict, analysis_text, _reason = analyze_talent(fields)
        if verdict:
            fields['risk_rating'] = verdict
            fields['ai_analysis'] = analysis_text
            log(f'[Job] {cid} 评估结论: {verdict}')

        # ── 写库：POST 优先（服务端去重），重名/已存在则 PUT 更新（管理员身份，可更新主库）──
        _job_set(cid, stage='写入达人库')
        try:
            res = api('POST', '/api/talents', fields, timeout=60, admin=True)
        except urllib.error.HTTPError as e:
            res = {'error': f'http {e.code}'}
        talent_id = res.get('id', '')
        if res.get('duplicate') and res.get('can_update') and talent_id:
            log(f'[Job] {cid} 达人已存在 {talent_id}，以管理员身份执行更新')
            try:
                api('PUT', f'/api/talents/{talent_id}', fields, timeout=60, admin=True)
                log(f'[Job] {cid} 更新完成')
            except Exception as e:
                log(f'[Job] {cid} PUT 更新失败（保留原数据）: {e}')
        elif not talent_id:
            raise RuntimeError(f'达人写入失败: {res.get("error") or str(res)[:200]}')

        # ── 归档 ──
        archive = ARCHIVE_DIR / f'daren_{name}.json'
        try:
            archive.write_text(json.dumps({
                'collect_id': cid, 'name': name, 'douyin_id': douyin_id,
                'talent_id': talent_id, 'collected_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'fields': fields, 'material_summary': {
                    'tabs': list(material['tabs_dom'].keys()),
                    'shots': material['shots'],
                },
            }, ensure_ascii=False, indent=1), encoding='utf-8')
        except Exception as e:
            log(f'[Job] {cid} 归档写盘失败（非致命）: {e}')

        api('POST', f'/api/talent-collects/{cid}',
            {'status': 'done', 'talent_id': talent_id})
        _job_set(cid, status='done', talent_id=talent_id,
                 stage='完成', shots=material['shots'])
        log(f'[Job] {cid} 完成 → {talent_id}')

    except NeedLogin as e:
        err = f'百应登录态失效，需要人工在浏览器窗口重新登录: {e}'
        log(f'[Job] {cid} {err}')
        _safe_fail(cid, err)
        _job_set(cid, status='need_login', error=err)
    except BrowserLoopPoisoned:
        raise
    except Exception as e:
        err = f'{type(e).__name__}: {e}'
        log(f'[Job] {cid} 失败: {err}\n{traceback.format_exc()[:1500]}')
        try:
            if BROWSER.page:
                _dump_page(BROWSER.page, 'fatal', cid)
        except Exception:
            pass
        _safe_fail(cid, err)
        _job_set(cid, status='failed', error=err)


def _safe_fail(cid, err):
    try:
        api('POST', f'/api/talent-collects/{cid}', {'status': 'failed', 'error': err[:400]})
    except Exception as e:
        log(f'[Job] {cid} 回写 failed 也失败了: {e}')


def queue_loop():
    log('[Queue] 轮询启动')
    while not _running.is_set():
        try:
            pending = db_pending()
            for row in pending:
                if _running.is_set():
                    break
                submit_collect(row)
        except Exception as e:
            log(f'[Queue] 轮询异常: {e}')
        _running.wait(POLL_INTERVAL)
    log('[Queue] 轮询退出')


# ─────────────────────────────── 控制口 ───────────────────────────────
class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/health':
            with _job_lock:
                up, logged_in = STATE.get('browser_up'), STATE.get('logged_in')
                dup, dlogged = STATE.get('daren_browser_up'), STATE.get('daren_logged_in')
                jobs = {k: {kk: vv for kk, vv in v.items() if kk != 'shots'}
                        for k, v in JOBS.items()}
            self._json(200, {
                'ok': True, 'browser_up': up, 'logged_in': logged_in,
                'daren_browser_up': dup, 'daren_logged_in': dlogged,
                'daren_probe': STATE.get('daren_probe'),
                'queue_pending': len(db_pending()),
                'jobs': jobs,
            })
            return
        if self.path.startswith('/jobs/'):
            cid = self.path[len('/jobs/'):]
            with _job_lock:
                job = dict(JOBS.get(cid) or {})
            self._json(200, job or {'error': 'not found'})
            return
        if self.path.startswith('/shots/'):
            fname = os.path.basename(self.path[len('/shots/'):])
            fpath = SHOTS_DIR / fname
            if fpath.is_file():
                data = fpath.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._json(404, {'error': 'not found'})
            return
        self._json(404, {'error': 'unknown'})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                pass
        if self.path == '/open':
            # 浏览器操作统一进工作线程，避免跨线程 greenlet 错误
            JOB_Q.put({'_open_browser': True})
            self._json(200, {'ok': True, 'msg': '浏览器正在打开'})
            return
        if self.path == '/open-daren':
            # 打开达人端浏览器（独立 profile），供人工扫码登录达人工作台
            JOB_Q.put({'_open_daren_browser': True})
            self._json(200, {'ok': True, 'msg': '达人端浏览器正在打开，请扫码登录'})
            return
        if self.path == '/graceful-daren':
            # 优雅关闭达人端浏览器：触发 cookie 落盘，重启 worker 不丢登录态
            JOB_Q.put({'_graceful_daren': True})
            self._json(200, {'ok': True, 'msg': '达人端浏览器正在优雅关闭（cookie 落盘）'})
            return
        if self.path == '/probe-daren':
            # 只读探测：当前 URL + cookie 名，不导航不点击（登录诊断用）
            JOB_Q.put({'_probe_daren': True})
            self._json(200, {'ok': True, 'msg': 'probe 已入队，随后 GET /probe-result'})
            return
        if self.path == '/collect-product-url':
            # 商品链接采集：{url} → 团长端浏览器打开 → LLM 提取 → GET /jobs/<id> 取 result
            url = str(body.get('url') or '').strip()
            if not url:
                self._json(400, {'error': 'missing url'})
                return
            cid = f"pur_{int(time.time()*1000)}_{secrets.token_hex(3)}"
            submit_collect({'_product_url': True, 'id': cid, 'url': url})
            self._json(200, {'ok': True, 'collect_id': cid})
            return
        if self.path == '/collect-product':
            # 商品采集：{url? 或 name+shop?} → 入队由达人端浏览器执行
            url = str(body.get('url') or '').strip()
            name = str(body.get('name') or '').strip()
            shop = str(body.get('shop') or '').strip()
            if not url and not name:
                self._json(400, {'error': 'missing url or name'})
                return
            cid = f"pcr_{int(time.time()*1000)}_{secrets.token_hex(3)}"
            submit_collect({'_product': True, 'id': cid, 'url': url,
                            'name': name, 'shop': shop})
            self._json(200, {'ok': True, 'collect_id': cid})
            return
        if self.path == '/collect':
            name = str(body.get('name', '')).strip()
            if not name:
                self._json(400, {'error': 'missing name'})
                return
            cid = f"tcr_{int(time.time()*1000)}_{secrets.token_hex(3)}"
            douyin_id = str(body.get('douyin_id') or '').strip()
            now = int(time.time() * 1000)
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            try:
                conn.execute(
                    'INSERT INTO talent_collects (id, name, douyin_id, status, requester, created_at, updated_at) '
                    'VALUES (?,?,?,?,?,?,?)',
                    (cid, name, douyin_id, 'pending', 'buyin_worker_manual', now, now))
                conn.commit()
            finally:
                conn.close()
            submit_collect({'id': cid, 'name': name, 'douyin_id': douyin_id})
            self._json(200, {'ok': True, 'collect_id': cid})
            return
        if self.path == '/teardown':
            # 视频拆解：{talent_id?|name?, douyin_id?, top_n?} → 建 job 入队，worker 逐条抓+分析
            talent_id = str(body.get('talent_id') or '').strip()
            name = str(body.get('name') or '').strip()
            douyin_id = str(body.get('douyin_id') or '').strip()
            try:
                top_n = max(1, min(int(body.get('top_n') or 3), 10))
            except (TypeError, ValueError):
                top_n = 3
            if not talent_id and not name:
                self._json(400, {'error': 'missing talent_id or name'})
                return
            if not name:
                conn = sqlite3.connect(str(DB_PATH), timeout=10)
                try:
                    r = conn.execute('SELECT name, douyin_id FROM talents WHERE id = ?',
                                     (talent_id,)).fetchone()
                finally:
                    conn.close()
                if not r:
                    self._json(404, {'error': 'talent not found'})
                    return
                name = r[0] or ''
                douyin_id = douyin_id or (r[1] or '')
            if not name:
                self._json(400, {'error': '达人名称为空且库里查不到'})
                return
            _ensure_video_tables()
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            try:
                dup = conn.execute(
                    "SELECT id FROM video_jobs WHERE talent_id = ? AND status = 'running' "
                    "ORDER BY created_at DESC LIMIT 1", (talent_id,)).fetchone()
                if dup:
                    self._json(200, {'ok': True, 'job_id': dup[0], 'duplicate': True})
                    return
                jid = f"vjob_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
                now = int(time.time() * 1000)
                conn.execute(
                    'INSERT INTO video_jobs (id, talent_id, talent_name, top_n, status, stage, created_at, updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (jid, talent_id, name, top_n, 'running', '排队中', now, now))
                conn.commit()
            finally:
                conn.close()
            submit_collect({'_teardown': True, 'job_id': jid, 'talent_id': talent_id,
                            'name': name, 'douyin_id': douyin_id, 'top_n': top_n})
            self._json(200, {'ok': True, 'job_id': jid})
            return
        if self.path == '/teardown_url':
            # 直链视频拆解：{url} → 下载+转写+画面+报告，结果落 talent_videos/video_jobs
            url = str(body.get('url', '')).strip()
            if 'douyin.com' not in url:
                self._json(400, {'error': '只支持抖音视频链接'})
                return
            _ensure_video_tables()
            jid = f"vjob_{int(time.time() * 1000)}_{secrets.token_hex(3)}"
            now = int(time.time() * 1000)
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            try:
                conn.execute(
                    'INSERT INTO video_jobs (id, talent_id, talent_name, top_n, status, stage, created_at, updated_at) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (jid, '', '(直链)', 1, 'running', '排队中', now, now))
                conn.commit()
            finally:
                conn.close()
            submit_collect({'_teardown_url': True, 'job_id': jid, 'url': url})
            self._json(200, {'ok': True, 'job_id': jid})
            return
        self._json(404, {'error': 'unknown'})


def main():
    log('=' * 50)
    log('buyin_worker 启动')
    # 探测 LLM 配置
    try:
        _zhipu_cfg()
        log('LLM(glm-4.7) 配置 OK')
    except Exception as e:
        log(f'!! LLM 配置异常: {e}')
    server = ThreadingHTTPServer((CONTROL_HOST, CONTROL_PORT), ControlHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log(f'控制口 http://{CONTROL_HOST}:{CONTROL_PORT}')
    _ensure_browser_thread()
    try:
        queue_loop()
    except KeyboardInterrupt:
        pass
    finally:
        _running.set()
        JOB_Q.put(None)
        try:
            if _browser_thread:
                _browser_thread.join(timeout=10)
        except Exception:
            pass
        BROWSER.close()
        DAREN_BROWSER.close()
        log('buyin_worker 退出')


if __name__ == '__main__':
    main()
