"""★ fix/ocr-canonical-sync: 规范化同步 + _text 列 + _normalize_dist_key 中文键修复 单测.

覆盖:
  1) _normalize_dist_key 中文键原样保留 ('三线城市' / '新一线城市' 不被归一为空)
  2) _canonicalize_talent_row 合成 OCR JSON 全路径
     - 顶层 snake_case 优先 (followers 等)
     - 顶层空壳回退 extra_fields 中文嵌套 (fan_gender 等)
     - 粉丝 4 段 (粉丝特征 / 粉丝团特征 / 直播间特征 / 短视频特征)
     - 直播带货数据 / 视频带货数据
     - 区间值双轨 (¥100万-500万 → avg_live_gmv=3000000 + avg_live_gmv_text 原文)
     - TOP3 中文键映射 (类目 → name, 均价 → avg_price, 结算额 → gmv)
  3) category_distribution > 105 按比例归一到 100
  4) price_distribution 保持不变
  5) 单视频结算额当文本存 (不解析均值)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/test_ocr_canonical_sync.py -v

★ 跟其他 test_*.py 一样用 importlib.util.spec_from_file_location 加载 server,
  Windows Mini 端静态分析用例逻辑 + Node 等价验证, Mac 端跑 pytest 验证.
"""
import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

sys.modules['solobrave_server'] = _solobrave_server

_normalize_dist_key = _solobrave_server._normalize_dist_key
_canonicalize_talent_row = _solobrave_server._canonicalize_talent_row
_deep_get = _solobrave_server._deep_get
_parse_gmv_value = _solobrave_server._parse_gmv_value
_merge_dist_by_normalized_key = _solobrave_server._merge_dist_by_normalized_key


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def _assert_true(cond, msg):
    assert cond, f'{msg}\n  condition was False'


def _build_synth_ocr_json():
    """合成 OCR JSON: 顶层 snake_case 部分空壳模拟 OCR 模型只填空壳,
    真值在 extra_fields 中文嵌套里."""
    return {
        # 顶层 snake_case 模板 (部分空壳)
        'followers': 50000,             # 顶层有值
        'total_gmv': '',                # 空壳, 真值在 extra_fields 直播带货数据
        'product_count': '',            # 空壳
        'total_shops': '',              # 空壳
        'video_gpm': '',                # 空壳, 真值在 extra_fields.视频带货数据.视频GPM
        'live_gpm': '',                 # 空壳
        'avg_live_gmv': '',             # 空壳
        'fan_gender': '',               # 空壳
        'fan_age': '',                  # 空壳
        'fan_city_tier': '',            # 空壳
        'fan_region': '',               # 空壳
        'fan_crowd': '',                # 空壳
        'fan_price_range': '',          # 空壳
        'fan_category': '',             # 空壳
        'main_category': '服饰',        # 顶层有值
        # 真实数据: extra_fields 中文嵌套
        'extra_fields': {
            '粉丝分析': {
                '粉丝特征': {
                    '性别': {'男': 65, '女': 35},
                    '年龄': {'31-40': 38, '26-30': 28, '36-40': 18, '18-25': 10, '41+': 6},
                    '城市等级': {'三线城市': 21, '新一线城市': 79},  # 中文键
                    '人群': '都市银发 23%',
                    '客单价': '50到100元 30%',
                    '品类偏好': '服装 23%',
                    '地域': '广东 25%',
                },
                '粉丝团特征': {
                    '性别': {'男': 60, '女': 40},
                    '年龄': {'26-30': 35, '18-25': 30, '31-35': 20},
                    '城市等级': {'新一线城市': 75, '二线城市': 25},
                },
                '直播间特征': {
                    '性别': {'女': 70, '男': 30},
                    '城市等级': {'一线城市': 60, '二线城市': 40},
                    '地域': '广东 25%',
                },
                '短视频特征': {
                    '性别': {'女': 65, '男': 35},
                    '城市等级': {'一线城市': 55, '二线城市': 45},
                },
            },
            '直播带货数据': {
                '场均结算额': '¥100万-500万',  # 区间值
                '直播GPM': '¥5,000-2万',
            },
            '视频带货数据': {
                '单视频结算额': '¥2万-10万',  # 区间值, 文本存
                '视频GPM': '300',
            },
            '热卖类目TOP3': [
                {'类目': '服装', '均价': '¥181.55', '结算额': '¥10万-25万'},
                {'类目': '个护', '均价': '¥99', '结算额': '¥5万-10万'},
            ],
            '热卖品牌TOP3': [
                {'品牌': '哈比熊', '均价': '¥181.55', '结算额': '¥10万-25万', '佣金': '未提供'},
            ],
            '类目分布': {'个护家清': 76, '医疗健康': 122},  # 总和 198 > 105
            '价格带分布': {'0-25': 15, '25-50': 20, '50-100': 40},
        },
    }


# ===== _normalize_dist_key 中文键原样保留 =====

def test_normalize_dist_key_chinese_city_tier():
    """★ 中文城市等级键原样保留 (老大反馈 fan_city_tier 变 {} 的根因)"""
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', '三线城市')
    _assert_equal(_normalize_dist_key('新一线城市'), '新一线城市', '新一线城市')
    _assert_equal(_normalize_dist_key('一线城市'), '一线城市', '一线城市')
    _assert_equal(_normalize_dist_key('二线城市'), '二线城市', '二线城市')
    _assert_equal(_normalize_dist_key('都市银发'), '都市银发', '人群标签')
    _assert_equal(_normalize_dist_key('广东'), '广东', '地域')


def test_normalize_dist_key_age_basic():
    """★ age '31-40' 仍正常归一 (中文键修复不影响)"""
    _assert_equal(_normalize_dist_key('31-40'), '31-40', 'age 31-40')
    _assert_equal(_normalize_dist_key('２６-３０'), '26-30', 'age 全角')


def test_normalize_dist_key_empty():
    """None / 空 → 返空"""
    _assert_equal(_normalize_dist_key(None), '', 'None')
    _assert_equal(_normalize_dist_key(''), '', '空字符串')
    _assert_equal(_normalize_dist_key(123), '123', '数字')


# ===== _canonicalize_talent_row 顶层 snake_case 优先 =====

def test_canonicalize_top_level_priority():
    """★ 顶层 followers / main_category 优先, 数值 parse 正确"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_equal(out.get('followers'), 50000, 'followers 顶层优先')
    _assert_equal(out.get('main_category'), '服饰', 'main_category')


# ===== fan_* 4 段从 extra_fields 中文键回退 =====

def test_canonicalize_fan_segments_four_sources():
    """★ 粉丝 4 段 (粉丝特征/粉丝团特征/直播间特征/短视频特征) 从 extra_fields 抽取,
    中文键 (性别/年龄/城市等级...) 映射到 fan_* 列族"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    # 粉丝特征 → fan_*
    _assert_true('fan_gender' in out, 'fan_gender 在 out')
    fan_gender = json.loads(out['fan_gender']) if isinstance(out['fan_gender'], str) else out['fan_gender']
    _assert_equal(fan_gender, {'男': 65, '女': 35}, 'fan_gender')
    _assert_true('fan_age' in out, 'fan_age')
    _assert_true('fan_city_tier' in out, 'fan_city_tier')
    fan_city_tier = json.loads(out['fan_city_tier']) if isinstance(out['fan_city_tier'], str) else out['fan_city_tier']
    # ★ 中文键原样保留: '三线城市' / '新一线城市' 必须保留
    _assert_true('三线城市' in fan_city_tier, 'fan_city_tier 含"三线城市"中文键')
    _assert_true('新一线城市' in fan_city_tier, 'fan_city_tier 含"新一线城市"中文键')
    # 粉丝团特征 → fan_group_*
    _assert_true('fan_group_gender' in out, 'fan_group_gender')
    _assert_true('fan_group_age' in out, 'fan_group_age')
    fan_group_city = json.loads(out['fan_group_city_tier']) if isinstance(out['fan_group_city_tier'], str) else out['fan_group_city_tier']
    _assert_true('新一线城市' in fan_group_city, 'fan_group_city_tier 中文键')
    # 直播间特征 → live_audience_*
    _assert_true('live_audience_gender' in out, 'live_audience_gender')
    _assert_true('live_audience_city_tier' in out, 'live_audience_city_tier')
    # 短视频特征 → video_audience_*
    _assert_true('video_audience_gender' in out, 'video_audience_gender')
    _assert_true('video_audience_city_tier' in out, 'video_audience_city_tier')


def test_canonicalize_fan_string_dim():
    """★ fan_crowd / fan_region / fan_price_range / fan_category 是字符串 (含 % 或纯文本), 原样存"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_equal(out.get('fan_crowd'), '都市银发 23%', 'fan_crowd 字符串')
    _assert_equal(out.get('fan_price_range'), '50到100元 30%', 'fan_price_range')
    _assert_equal(out.get('fan_category'), '服装 23%', 'fan_category')
    _assert_equal(out.get('fan_region'), '广东 25%', 'fan_region')


# ===== 直播/视频带货数据区间值双轨 =====

def test_canonicalize_live_video_range_value():
    """★ 区间值双轨: avg_live_gmv 存解析均值 (¥100万-500万 → 3000000),
    avg_live_gmv_text 存原文 '¥100万-500万'"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_equal(out.get('avg_live_gmv'), 3000000.0, 'avg_live_gmv 区间均值 100万+500万 / 2 = 3000000')
    _assert_equal(out.get('avg_live_gmv_text'), '¥100万-500万', 'avg_live_gmv_text 原文')
    _assert_equal(out.get('live_gpm_text'), '¥5,000-2万', 'live_gpm_text 原文')


def test_canonicalize_video_data():
    """★ 视频带货数据: video_gpm = 300 (数字), single_video_settlement 文本"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_equal(out.get('video_gpm'), 300.0, 'video_gpm 300')
    _assert_equal(out.get('video_gpm_text'), '300', 'video_gpm_text 原文')
    # 单视频结算额 文本存 (不解析均值, single_video_settlement 本来就是文本字段)
    _assert_equal(out.get('single_video_settlement'), '¥2万-10万', 'single_video_settlement 文本')


# ===== TOP3 中文键映射 =====

def test_canonicalize_top3_chinese_keys():
    """★ TOP3 中文键映射: 类目/品牌 → name, 均价 → avg_price, 结算额 → gmv"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_true('top_categories' in out, 'top_categories')
    _assert_true('top_brands' in out, 'top_brands')
    cats = out['top_categories']
    _assert_equal(len(cats), 2, 'top_categories 2 个')
    _assert_equal(cats[0]['name'], '服装', 'cat[0].name')
    _assert_equal(cats[0]['avg_price'], '¥181.55', 'cat[0].avg_price')
    _assert_equal(cats[0]['gmv'], '¥10万-25万', 'cat[0].gmv')
    brands = out['top_brands']
    _assert_equal(len(brands), 1, 'top_brands 1 个')
    _assert_equal(brands[0]['name'], '哈比熊', 'brand[0].name')


# ===== category_distribution > 105 归一到 100 =====

def test_canonicalize_category_distribution_normalize():
    """★ category_distribution 总和 > 105 按比例归一到 100 (76+122=198 → 100 比例)"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_true('category_distribution' in out, 'category_distribution')
    cat = out['category_distribution']
    total = sum(v for v in cat.values() if isinstance(v, (int, float)))
    _assert_equal(round(total, 1), 100.0, f'category_distribution 和为 100 (实际 {total})')


def test_canonicalize_price_distribution_keep():
    """★ price_distribution 保持不变 (总和 < 105)"""
    out = _canonicalize_talent_row(_build_synth_ocr_json())
    _assert_true('price_distribution' in out, 'price_distribution')
    price = out['price_distribution']
    _assert_equal(price, {'0-25': 15, '25-50': 20, '50-100': 40}, 'price_distribution 原值')


# ===== _OCR_TO_TALENT_FIELDS 21 字段顶层优先 =====

def test_canonicalize_full_field_list_top_priority():
    """★ _OCR_TO_TALENT_FIELDS 21 字段顶层优先 (followers, total_gmv 等)"""
    # 改合成 JSON: 给 total_gmv 一个真值 (顶层优先), 看 _canonicalize_talent_row 是否走它
    ocr = {
        'followers': 100000,
        'total_gmv': 1000000,  # 顶层有值
        'product_count': 50,
        'total_shops': 20,
        'main_category': '美妆',
        'extra_fields': {
            '粉丝分析': {
                '粉丝特征': {'性别': {'女': 100}},  # 顶层空
            },
        },
    }
    out = _canonicalize_talent_row(ocr)
    _assert_equal(out.get('followers'), 100000, 'followers 顶层优先')
    _assert_equal(out.get('total_gmv'), 1000000, 'total_gmv 顶层优先')
    _assert_equal(out.get('product_count'), 50, 'product_count 顶层优先')
    _assert_equal(out.get('total_shops'), 20, 'total_shops 顶层优先')
    _assert_equal(out.get('main_category'), '美妆', 'main_category 顶层优先')


# ===== 边界 case =====

def test_canonicalize_empty_input():
    """空输入 / 非 dict 返空"""
    _assert_equal(_canonicalize_talent_row({}), {}, '空 dict')
    _assert_equal(_canonicalize_talent_row(None), {}, 'None')
    _assert_equal(_canonicalize_talent_row('not dict'), {}, 'string 输入')


def test_canonicalize_no_extra_fields():
    """无 extra_fields 时只走顶层 snake_case"""
    ocr = {'followers': 1000}
    out = _canonicalize_talent_row(ocr)
    _assert_equal(out.get('followers'), 1000, 'followers 顶层')
    _assert_true('fan_gender' not in out, '无 extra_fields 不走 fan_*')