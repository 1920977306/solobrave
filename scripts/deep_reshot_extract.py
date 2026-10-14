#!/usr/bin/env python3
"""一次性深度补采：对已有 tab 截图按 tab 分组做聚焦视觉提取，合并进 ocr_raw_fields 并修正错列。"""
import sys, json, sqlite3, time, base64, io, urllib.request
from pathlib import Path

sys.path.insert(0, '/Users/qichen/solobrave-prod')
import buyin_worker as bw

PROD = Path('/Users/qichen/solobrave-prod')
SHOTS = PROD / 'data/buyin_browser/shots'
RUN = 'tcr_1791969322541_dfea79'
TALENT_ID = 'tal_1791968933211_279ddb'


def b64_img(path):
    from PIL import Image
    im = Image.open(path).convert('RGB')
    if im.width > 960:
        im = im.resize((960, int(im.height * 960 / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, 'JPEG', quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def vision(prompt, paths):
    base, key = bw._zhipu_cfg()
    imgs = [{'type': 'image_url', 'image_url': {'url': f'data:image/jpeg;base64,{b64_img(p)}'}}
            for p in paths]
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps({
            'model': 'glm-4v-plus',
            'messages': [{'role': 'user', 'content': [{'type': 'text', 'text': prompt}] + imgs}],
            'temperature': 0.0,
        }).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {key}'},
        method='POST')
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode())
    content = (data.get('choices') or [{}])[0].get('message', {}).get('content') or ''
    cand = bw._balanced_json(content)
    if not cand:
        raise RuntimeError(f'无JSON: {content[:200]}')
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        return json.loads(bw._repair_json_text(cand))


IRON = ('铁律：数值必须来自截图原文，严禁编造；没有的填 null。'
        '金额区间保留原文。百分数输出数字（54.74 表示54.74%）。只输出一个 JSON 对象。')

GROUPS = [
    ('概览', [
        (f'{SHOTS}/{RUN}_洁洁爱户外_概览.png',),
        (f'{SHOTS}/{RUN}_洁洁爱户外_概览.png', f'{SHOTS}/{RUN}_洁洁爱户外_概览_下.png'),
    ], [
        '第一轮（核心数据+视频明细）：提取 content_total(发布内容总数,整数) watch_total(观看总次数原文) '
        'live_count(直播场次) video_count_all(视频个数) tuwen_count(图文个数) '
        'history_videos(历史带货视频明细,数组,每项: title/发布日期/时长/观看人数/点赞/评论/结算额/到手价/原价,'
        '能读多少读多少)',
        '第二轮（粉丝画像+类目品牌）：提取 fan_gender/fan_age/fan_region/fan_crowd(对象,百分比数字) '
        'fan_purchase_pref(粉丝购买偏好,如"服装22.68%") fan_consume_level(粉丝消费水平,如"客单价50到100元23.81%") '
        'category_distribution(带货最多品类TOP5,对象,百分比数字) '
        'cooperating_brands_distribution(品牌集中度TOP5,对象,百分比数字)',
    ]),
    ('带货分析', [
        (f'{SHOTS}/{RUN}_洁洁爱户外_带货分析.png',),
        (f'{SHOTS}/{RUN}_洁洁爱户外_带货分析.png', f'{SHOTS}/{RUN}_洁洁爱户外_带货分析_下.png'),
    ], [
        '第一轮（核心数据）：提取 total_gmv_text(结算总额) total_products(带货商品数) total_shops(合作店铺数) '
        'average_price(平均件单价)',
        '第二轮（直播+视频两块）：提取 live_settlement_ratio(直播带货结算占比) live_avg_gmv_text(场均结算额) '
        'live_gpm_text(直播GPM) live_product_count(直播带货商品数) live_shop_count(直播合作店铺数) '
        'live_avg_price(直播平均件单价) video_settlement_ratio(视频结算占比) '
        'single_video_settlement(单视频结算额区间) video_gpm_text(视频GPM区间) '
        'video_product_count(视频带货商品数) video_shop_count(视频合作店铺数) video_avg_price(视频平均件单价)',
    ]),
    ('粉丝分析', [
        (f'{SHOTS}/{RUN}_洁洁爱户外_粉丝分析.png',),
        (f'{SHOTS}/{RUN}_洁洁爱户外_粉丝分析.png', f'{SHOTS}/{RUN}_洁洁爱户外_粉丝分析_下.png'),
    ], [
        '第一轮（粉丝特征+趋势）：提取 fan_feature_gender/fan_feature_age/fan_feature_region/fan_feature_crowd '
        '(粉丝特征pill标签,原样字符串) consume_pref_price/consume_pref_category(消费偏好pill标签,原样字符串) '
        'fan_growth(近2月粉丝变化数,带符号整数) fan_growth_rate(粉丝变化率,字符串) '
        'fan_gender/fan_age/fan_region/fan_crowd(分布对象,百分比数字)。'
        '注意：性别分布以页面文字摘要为准（如"男性居多 54.74%"），不要凭饼图颜色猜。',
    ]),
    ('评价详情', [
        (f'{SHOTS}/{RUN}_洁洁爱户外_评价详情.png',),
        (f'{SHOTS}/{RUN}_洁洁爱户外_评价详情.png', f'{SHOTS}/{RUN}_洁洁爱户外_评价详情_下.png'),
    ], [
        '第一轮（评价统计+明细）：提取 review_count(全部评价数) review_good(好评数) review_mid(中评数) '
        'review_bad(差评数) review_score(评价总分) rating_effect(带货效果) rating_fulfill(合作履约) '
        'rating_communication(沟通态度) reviews(评价明细数组,每项: 商家名/星级/评价原文/联系电话/日期,'
        '联系电话原样照抄数字) review_contact(评价里出现的联系电话)',
    ]),
    ('场景分析', [
        (f'{SHOTS}/{RUN}_洁洁爱户外_场景分析.png', f'{SHOTS}/{RUN}_洁洁爱户外_场景分析_下.png'),
    ], [
        '第一轮：提取 live_scene(直播场景数据,有则对象:场次/场均/GPM) video_scene(视频场景数据) '
        'tuwen_scene(图文场景数据) chuchuang_scene(橱窗场景数据),页面显示"暂无数据"就填 null',
    ]),
]

deep = {}
for gname, shot_groups, prompts in GROUPS:
    for shots, ptext in zip(shot_groups, prompts):
        paths = [Path(s) for s in shots if Path(s).exists()]
        if not paths:
            continue
        for attempt in (1, 2):
            try:
                r = vision(f'{ptext}\n{IRON}', paths)
                if isinstance(r, dict):
                    for k, v in r.items():
                        if v not in (None, '', [], {}):
                            deep[k] = v
                print(f'[OK] {gname} ({len(paths)}张): +{len(r or {})} keys')
                break
            except Exception as e:
                print(f'[ERR] {gname} 第{attempt}次: {e}')
                if attempt == 2:
                    print(f'[GIVEUP] {gname}')

print(f'\n deep keys({len(deep)}):', sorted(deep.keys()))

# 合并进库：ocr_raw_fields = 旧 ∪ 新；修正 fan_gender 错列
conn = sqlite3.connect(PROD / 'data/solobrave.db')
conn.row_factory = sqlite3.Row
row = conn.execute('SELECT ocr_raw_fields, fan_gender FROM talents WHERE id=?', (TALENT_ID,)).fetchone()
raw = row['ocr_raw_fields'] or ''
try:
    old = json.loads(raw)
    if isinstance(old, str):
        old = json.loads(old)
except Exception:
    old = {}
if not isinstance(old, dict):
    old = {}
old.update(deep)
now = int(time.time() * 1000)
conn.execute('UPDATE talents SET ocr_raw_fields=?, updated_at=? WHERE id=?',
             (json.dumps(old, ensure_ascii=False), now, TALENT_ID))
if 'fan_gender' in deep and isinstance(deep['fan_gender'], dict):
    conn.execute('UPDATE talents SET fan_gender=? WHERE id=?',
                 (json.dumps(deep['fan_gender'], ensure_ascii=False), TALENT_ID))
    print('fan_gender 列已修正:', deep['fan_gender'])
if 'average_price' in deep:
    try:
        conn.execute('UPDATE talents SET average_price=? WHERE id=?',
                     (float(str(deep['average_price']).replace('¥', '').replace(',', '')), TALENT_ID))
        print('average_price 列已填:', deep['average_price'])
    except ValueError:
        pass
conn.commit()
conn.close()
print('已写回库')
