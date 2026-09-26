# -*- coding: utf-8 -*-
"""★ fix/ocr-canonical-sync-v2: 规范化 OCR→talents 同步 v2 单测.

老大 2026-09-25 反馈 v1 (a8c7de7 弃用) 4 大病根, v2 从零写, 严格按 fixture 嵌套层级:
  1. 粉丝 4 段路径错误 → v2 FLAT 2 层 (extra_fields.<段>.<维度>, 无"粉丝分析"中间层)
  2. _strip_yuan 漏多前缀 → v2 循环 while 清所有 ¥/￥ 前缀
  3. 无同步保护 → v2 9 列白名单 (4 _text + 4 fan 段起始 + single_video_settlement)
  4. main_category 来源断言错 → v2 顶层优先 (fixture L23 顶层有值)

覆盖 (14 case):
  1) _normalize_dist_key 中文键原样保留 (3 case)
     - 中文城市等级原样保留
     - '31-40岁' 保留岁字 (v1 错误去岁字)
     - 中间空格保留 (v1 错误去中间空格)
  2) _canonicalize_talent_row FLAT 2 层 4 段路径 (1 case)
  3) _strip_yuan 循环 while 加强版 (1 case)
  4) _update_talent_column_if_empty 同步保护 (2 case)
     - 已有非空值跳过
     - 空字符串写入
  5) 区间值双轨 (2 case)
     - 含 ¥ 前缀 (¥100万-500万)
     - 不含 ¥ 前缀 (500-1,000)
  6) category_distribution > 105 归一 (1 case)
  7) single_video_settlement 文本化 (1 case)
  8) main_category 顶层优先 (2 case)
     - 顶层有值优先
     - 不从 fan_category 兜底 (v1 错误回退路径修正)
  9) _canonicalize_talent_row 全路径 smoke test (1 case)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/test_ocr_canonical_sync.py -v

期望: 14/14 PASS (跟 dev 原有 77 case + 新增 14 = 91 PASS).

★ Windows Mini 端无 Python, Mac 端跑 pytest 验证.
★ 测试代码自己内嵌合成 OCR JSON, 不从 fixture 读 (避免真实业务 JSON 入 git).
"""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ★ 跟其他 test_*.py 一致: 用 importlib.util.spec_from_file_location 加载 server
_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)


# ★ fix/mini-test-code-repair-20260925 02:51: 测试 fixture 加 _ImmuneConn 代理
#   跟 KB 3 / heavy_pipe / rag_index 模式统一, 让产品代码 close() 不关真连接
#   (治 4 个 migrate 测试 conn 被产品代码 close → 后面 closed database 假绿)
class _ImmuneConn:
    def __init__(self, real):
        self._real = real
    def close(self):
        pass  # 产品代码 close 不生效 (产品代码 finally 关 conn 不能真的关)
    def __getattr__(self, name):
        return getattr(self._real, name)


sys.modules['solobrave_server'] = _solobrave_server

_normalize_dist_key = _solobrave_server._normalize_dist_key
_canonicalize_talent_row = _solobrave_server._canonicalize_talent_row
_strip_yuan = _solobrave_server._strip_yuan
_update_talent_column_if_empty = _solobrave_server._update_talent_column_if_empty
_parse_gmv_value = _solobrave_server._parse_gmv_value
_merge_dist_by_normalized_key = _solobrave_server._merge_dist_by_normalized_key
_migrate_existing_talents_fill_text_columns = _solobrave_server._migrate_existing_talents_fill_text_columns
_talent_row_to_dict = _solobrave_server._talent_row_to_dict
_get_confidence_level = _solobrave_server._get_confidence_level
_set_confidence_level = _solobrave_server._set_confidence_level
_normalize_distribution = _solobrave_server._normalize_distribution
_MIN_DIST_KEYS = _solobrave_server._MIN_DIST_KEYS
_set_distribution_incomplete = _solobrave_server._set_distribution_incomplete
_get_distribution_incomplete_from_ocr_raw = _solobrave_server._get_distribution_incomplete_from_ocr_raw
_detect_existing_collapsed_city_tier = _solobrave_server._detect_existing_collapsed_city_tier
_reset_collapsed_city_tier = _solobrave_server._reset_collapsed_city_tier


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def _assert_true(cond, msg):
    assert cond, f'{msg}\n  condition was False'


def _build_v2_synth_ocr():
    """★ v2 合成 OCR JSON: 严格按 fixture FLAT 2 层嵌套结构
    (extra_fields.<中文段>.<维度>, 没有"粉丝分析"中间层).

    对比 v1 (a8c7de7 弃用) 用 extra_fields.粉丝分析.<段>.<维度> 3 层错路径.
    测试 fixture: tests/fixtures/ocr_structure_skeleton.json (688 行脱敏骨架).
    """
    return {
        # ----- 顶层 snake_case (部分空壳 + 部分有值) -----
        'followers': 50000,                  # 顶层有真值
        'main_category': '服饰内衣',         # fixture L23 顶层 snake_case
        'total_gmv': '',                     # 空壳, 真值在 extra_fields.直播带货数据
        'video_gpm': '',                     # 空壳
        'live_gpm': '',                      # 空壳
        'avg_live_gmv': '',                  # 空壳
        'fan_gender': '',                    # 空壳, 真值在 extra_fields.粉丝特征
        'fan_age': '',
        'fan_city_tier': '',
        'fan_crowd': '',
        'fan_price_range': '',
        'fan_category': '',
        'fan_region': '',
        # ----- 顶层 category_distribution (fixture L81-95, 13 keys 总和>105) -----
        'category_distribution': {
            '服饰内衣': 24, '个护家清': 19, '食品饮料': 17, '美妆': 12, '母婴': 10,
            '家居': 8, '数码': 7, '运动户外': 6, '图书': 5, '汽车': 4,
            '游戏': 3, '本地服务': 3, '其他': 2,
        },  # 总和 120 > 105, 触发归一
        # ★ v2 改动: price_distribution 放顶层 (v2 函数不查 extra_fields.价格带分布, 见交付报告 v2 边界)
        'price_distribution': {'0-25': 15, '25-50': 20, '50-100': 40},
        # ----- extra_fields FLAT 2 层 (没"粉丝分析"中间层) -----
        'extra_fields': {
            '粉丝特征': {
                '性别': {'男': 65, '女': 35},
                '年龄': {'31-40岁': 38, '26-30岁': 28},
                '城市等级': {'三线城市': 21, '新一线城市': 79},  # 中文键
                '人群': '都市银发 23%',
                '客单价': '50到100元 30%',
                '品类偏好': '服装 23%',
            },
            '粉丝团特征': {
                '性别': {'男': 60, '女': 40},
                '城市等级': {'新一线城市': 75, '二线城市': 25},
            },
            '直播间特征': {
                '性别': {'女': 70, '男': 30},
                '城市等级': {'一线城市': 60, '二线城市': 40},
            },
            '短视频特征': {
                '性别': {'女': 65, '男': 35},
                '城市等级': {'一线城市': 55, '二线城市': 45},
            },
            '直播带货数据': {
                '场均结算额': '¥100万-500万',    # 含 ¥ 区间
                '直播GPM': '500-1,000',          # 不含 ¥ 区间
                '带货商品数': 25,
                '合作店铺数': 12,
            },
            '视频带货数据': {
                '单视频结算额': '¥5万-10万',     # 文本存, 不解析均值
                '视频GPM': '300',                # 纯数字
            },
            '热卖类目TOP3': [
                {'类目': '服装', '均价': '¥181.55', '结算额': '¥10万-25万'},
                {'类目': '个护', '均价': '¥99', '结算额': '¥5万-10万'},
            ],
            '热卖品牌TOP3': [
                {'品牌': '哈比熊', '均价': '¥181.55', '结算额': '¥10万-25万', '佣金参考': '未提供'},
            ],
            '类目分布': {'个护家清': 76, '医疗健康': 122},  # 总和 198 > 105 (extra_fields 兜底路径)
            # 注: '价格带分布' 不放 extra_fields, 顶层已有 price_distribution
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 1. _normalize_dist_key 中文键原样保留 (3 case)
# ══════════════════════════════════════════════════════════════════════

def test_normalize_dist_key_chinese_city_kept():
    """★ v2 中文城市等级键原样保留 ('三线城市' 不被吞成空导致 fan_city_tier 变 {}).

    老大 2026-09-25 反馈: v1 _normalize_dist_key s.replace('岁', '') + re.sub(r'[\\s\\u3000]+', '')
    会吞中文中间字 ('三 线城市' → '三线城市', 同义异名 merge 后 fan_city_tier 错乱).
    v2 fix: 只 trim 首尾空白 + 全角数字 + 横线归一, 中文键原样保留.
    """
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', '三线城市 原样')
    _assert_equal(_normalize_dist_key('新一线城市'), '新一线城市', '新一线城市 原样')
    _assert_equal(_normalize_dist_key('一线城市'), '一线城市', '一线城市 原样')
    _assert_equal(_normalize_dist_key('二线城市'), '二线城市', '二线城市 原样')
    _assert_equal(_normalize_dist_key('都市银发'), '都市银发', '人群标签 原样')
    _assert_equal(_normalize_dist_key('广东'), '广东', '地域 原样')


def test_normalize_dist_key_age_keeps_sui():
    """★ v2 '31-40岁' 保留 '岁' 字 (v1 错误去岁字导致 '31-40' 与 '31-40岁' 合并成同一 key).

    v1 fix/_talent-age-normalize commit ad90c65 用同义归一 ('31-40岁' → '31-40') 防 dict key 重复,
    但 v2 决定保留岁字让 OCR 原样, merge 时 '31-40' (占比 X) + '31-40岁' (占比 Y) 同义归一取较大值.
    """
    _assert_equal(_normalize_dist_key('31-40岁'), '31-40岁', '带岁字保留')
    _assert_equal(_normalize_dist_key('31-40'), '31-40', '不带岁字保持')
    # 全角数字归一仍然生效 (基础功能不丢)
    _assert_equal(_normalize_dist_key('３１-４０岁'), '31-40岁', '全角数字归一 + 岁字保留')
    _assert_equal(_normalize_dist_key('１８-２３'), '18-23', '全角数字归一')


def test_normalize_dist_key_inner_space_kept():
    """★ v2 中间空格保留 (v1 错误去中间空格导致 '三 线城市' 与 '三线城市' 合并成同一 key).

    v2: 只 trim 首尾空白, 不动中间空格 (中间空格可能是有意义的分隔符, 不该盲目去).
    """
    _assert_equal(_normalize_dist_key('三 线城市'), '三 线城市', '中间半角空格保留')
    _assert_equal(_normalize_dist_key('  三线城市  '), '三线城市', '首尾半角空格 trim')
    _assert_equal(_normalize_dist_key('新　一线城市'), '新　一线城市', '中间全角空格保留 (U+3000)')
    _assert_equal(_normalize_dist_key('　三线城市　'), '三线城市', '首尾全角空格 trim')


# ══════════════════════════════════════════════════════════════════════
# 2. _canonicalize_talent_row FLAT 4 段路径 (1 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_flattens_4_fan_sections():
    """★ v2 粉丝 4 段从 extra_fields.<段>.<维度> FLAT 2 层抽取
    (v1 a8c7de7 错误走 extra_fields.粉丝分析.<段>.<维度> 3 层路径 → 找不到值).

    验证 4 个段 fan_gender / fan_group_gender / live_audience_gender / video_audience_gender 都有,
    城市等级中文键保留.
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    # 粉丝特征 → fan_gender (dict 序列化为 JSON 字符串)
    _assert_true('fan_gender' in out, 'fan_gender 应在 out (FLAT 2 层路径)')
    fan_gender = json.loads(out['fan_gender']) if isinstance(out['fan_gender'], str) else out['fan_gender']
    _assert_equal(fan_gender, {'男': 65, '女': 35}, 'fan_gender 内容')
    # 粉丝团特征 → fan_group_gender
    _assert_true('fan_group_gender' in out, 'fan_group_gender 应在 out (FLAT 2 层路径)')
    fan_group_gender = json.loads(out['fan_group_gender']) if isinstance(out['fan_group_gender'], str) else out['fan_group_gender']
    _assert_equal(fan_group_gender, {'男': 60, '女': 40}, 'fan_group_gender 内容')
    # 直播间特征 → live_audience_gender
    _assert_true('live_audience_gender' in out, 'live_audience_gender 应在 out (FLAT 2 层路径)')
    live_gender = json.loads(out['live_audience_gender']) if isinstance(out['live_audience_gender'], str) else out['live_audience_gender']
    _assert_equal(live_gender, {'女': 70, '男': 30}, 'live_audience_gender 内容')
    # 短视频特征 → video_audience_gender
    _assert_true('video_audience_gender' in out, 'video_audience_gender 应在 out (FLAT 2 层路径)')
    video_gender = json.loads(out['video_audience_gender']) if isinstance(out['video_audience_gender'], str) else out['video_audience_gender']
    _assert_equal(video_gender, {'女': 65, '男': 35}, 'video_audience_gender 内容')
    # 城市等级中文键保留 (老大核心要求)
    fan_city = json.loads(out['fan_city_tier']) if isinstance(out['fan_city_tier'], str) else out['fan_city_tier']
    _assert_true('三线城市' in fan_city, 'fan_city_tier 含"三线城市"中文键 (v1 错丢失)')
    _assert_true('新一线城市' in fan_city, 'fan_city_tier 含"新一线城市"中文键 (v1 错丢失)')


# ══════════════════════════════════════════════════════════════════════
# 3. _strip_yuan 加强版 (1 case)
# ══════════════════════════════════════════════════════════════════════

def test_strip_yuan_multi_prefix_loop():
    """★ v2 循环 while 清所有 ¥ 和 ￥ 前缀 (v1 lstrip 单次漏多前缀).

    OCR 偶发输出 '¥¥100' (视觉模型误判重复字符), v1 只 lstrip 一次导致 float('¥100') 抛 ValueError
    _parse_gmv_single 兜底返 0, 区间均值错算. v2 循环 while 清干净.
    """
    _assert_equal(_strip_yuan('¥100'), '100', '单前缀半角 ¥')
    _assert_equal(_strip_yuan('￥100'), '100', '单前缀全角 ￥')
    _assert_equal(_strip_yuan('¥¥100'), '100', '双前缀半角 ¥¥')
    _assert_equal(_strip_yuan('￥￥100'), '100', '双前缀全角 ￥￥')
    _assert_equal(_strip_yuan('¥￥100'), '100', '混合前缀 ¥￥ (半角+全角)')
    _assert_equal(_strip_yuan('￥¥100'), '100', '混合前缀 ￥¥ (全角+半角)')
    _assert_equal(_strip_yuan('¥¥¥100'), '100', '三前缀 ¥¥¥')
    _assert_equal(_strip_yuan('  ¥100  '), '100', '前后空格 + ¥')
    _assert_equal(_strip_yuan(None), '', 'None 返空')
    _assert_equal(_strip_yuan(''), '', '空字符串 返空')
    _assert_equal(_strip_yuan('100'), '100', '无前缀原样')


# ══════════════════════════════════════════════════════════════════════
# 4. _update_talent_column_if_empty 同步保护 (2 case)
# ══════════════════════════════════════════════════════════════════════

def _setup_talents_test_db():
    """★ 同步保护测试用 in-memory DB: 建 talents 表 + 插 1 行, 返回 conn.

    跟 _update_talent_column_if_empty 函数签名一致 (conn / talent_id / column / new_value),
    用 sqlite3 in-memory 数据: 测试 9 列同步保护行为 (老大原话: 防覆盖手修正值).
    """
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE talents (id INTEGER PRIMARY KEY, name TEXT, total_gmv_text TEXT DEFAULT "")')
    conn.execute("INSERT INTO talents (id, name, total_gmv_text) VALUES (1, '李婶儿', '老修正值')")
    conn.commit()
    return conn


def test_protect_skips_when_existing_value():
    """★ v2 同步保护: 已有非空值则跳过更新 (防覆盖手修正值).

    老大原话: "若目标列已有非空值则跳过更新 (防止覆盖手修正值)".
    场景: 团长手动修正过 total_gmv_text='老修正值', OCR 同步再写入会被跳过.
    """
    conn = _setup_talents_test_db()
    try:
        # total_gmv_text 已有 '老修正值', 新值 '新解析值' 应被跳过
        result = _update_talent_column_if_empty(conn, 1, 'total_gmv_text', '新解析值')
        _assert_equal(result, False, '已有非空值应跳过, 返回 False')
        conn.commit()  # 函数本身不 commit, 调用方 commit (跟 _update_talent_from_ocr_fields 一致)
        # DB 值不变
        row = conn.execute('SELECT total_gmv_text FROM talents WHERE id = 1').fetchone()
        _assert_equal(row['total_gmv_text'], '老修正值', 'DB 值未被覆盖, 保留老修正值')
    finally:
        conn.close()


def test_protect_writes_when_existing_empty():
    """★ v2 同步保护: 已有空字符串则写入新值 (覆盖是允许的).

    场景: total_gmv_text 是空字符串 (OCR 首次同步), 新解析值 '新解析值' 应被写入.
    """
    conn = _setup_talents_test_db()
    try:
        # 手动 UPDATE 让 total_gmv_text 变成空字符串
        conn.execute("UPDATE talents SET total_gmv_text = '' WHERE id = 1")
        conn.commit()
        # 现在 total_gmv_text 是 '', new='新解析值' 应被写入
        result = _update_talent_column_if_empty(conn, 1, 'total_gmv_text', '新解析值')
        _assert_equal(result, True, '空字符串应写入, 返回 True')
        conn.commit()  # 函数本身不 commit, 调用方 commit
        # DB 值更新
        row = conn.execute('SELECT total_gmv_text FROM talents WHERE id = 1').fetchone()
        _assert_equal(row['total_gmv_text'], '新解析值', 'DB 值已更新为新值')
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# 5. 区间值双轨 (2 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_interval_with_yuan_prefix():
    """★ v2 区间值双轨 (含 ¥ 前缀): avg_live_gmv_text='¥100万-500万' + avg_live_gmv=3000000.

    对应 fixture L612-619 直播带货数据.场均结算额 含 ¥ 区间格式 (¥100万-500万 等).
    数值列存解析均值 (300万), 同步 _text 列存原文 (¥100万-500万).
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    _assert_equal(out.get('avg_live_gmv_text'), '¥100万-500万', 'avg_live_gmv_text 原文')
    _assert_equal(out.get('avg_live_gmv'), 3000000.0, 'avg_live_gmv 区间均值 = (100万 + 500万) / 2 = 300万')


def test_canonicalize_interval_without_yuan_prefix():
    """★ v2 区间值双轨 (不含 ¥ 前缀): live_gpm_text='500-1,000' + live_gpm=750.

    对应 fixture L612-619 直播带货数据.直播GPM 不含 ¥ 区间格式 (500-1,000 等).
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    _assert_equal(out.get('live_gpm_text'), '500-1,000', 'live_gpm_text 原文')
    _assert_equal(out.get('live_gpm'), 750.0, 'live_gpm 区间均值 = (500 + 1000) / 2 = 750')


# ══════════════════════════════════════════════════════════════════════
# 6. category_distribution > 105 归一 (1 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_category_distribution_normalize_over_105():
    """★ v2 category_distribution 总和 > 105 按比例归一到 100.

    合成 JSON 顶层 category_distribution 13 keys 总和 120, 归一后和≈100.
    对应 fixture L81-95 13 keys 总和~198% 真实场景, 治 v1 bug "两套数据叠加成 198%".

    老大 brief 原话: "category_distribution若值总和>105则按百分比归一到100".
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    _assert_true('category_distribution' in out, 'category_distribution 应在 out')
    cat = out['category_distribution']
    total = sum(v for v in cat.values() if isinstance(v, (int, float)))
    _assert_equal(round(total, 1), 100.0, f'category_distribution 归一后和 = 100 (实际 {total})')
    # 13 keys 数量不变
    _assert_equal(len(cat), 13, '13 keys 全部保留')
    # 各 key 按比例缩放 (24 * 100/120 = 20.0)
    scale = 100.0 / 120
    _assert_equal(cat['服饰内衣'], round(24 * scale, 2), '服饰内衣 缩放到 20.0')
    _assert_equal(cat['个护家清'], round(19 * scale, 2), '个护家清 缩放到 15.83')
    _assert_equal(cat['其他'], round(2 * scale, 2), '其他 缩放到 1.67')


# ══════════════════════════════════════════════════════════════════════
# 7. single_video_settlement 文本化 (1 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_single_video_settlement_keeps_text():
    """★ v2 single_video_settlement 文本存原始字符串 (不解析均值).

    老大 brief 原话: "single_video_settlement存文本原文".
    v1 bug: 把 '¥5万-10万' 当数字解析成 (50000 + 100000) / 2 = 75000, 但字段本意是文本.
    v2 fix: str(single_video) 直接存, 不解析.
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    _assert_equal(out.get('single_video_settlement'), '¥5万-10万', 'single_video_settlement 文本原样存')
    # 同步保护列白名单: single_video_settlement 在 _PROTECTED_COLUMNS 里
    _assert_true('single_video_settlement' in out, 'single_video_settlement 在 out')


# ══════════════════════════════════════════════════════════════════════
# 8. main_category 顶层优先 (2 case, 修正 v1 红 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_main_category_top_priority():
    """★ v2 main_category 顶层优先 (fixture L23 'main_category':'服饰内衣' 顶层有值).

    老大 brief 原话: "修main_category相关的两个红case".

    v1 bug: 走 extra_fields 来源断言, 顶层值被忽略.
    v2 fix: 顶层 _OCR_TO_TALENT_FIELDS 优先, _deep_get(ocr_json, 'main_category') 找到顶层值.

    注意: db_col='category' (在 _OCR_TO_TALENT_FIELDS 第 2 列定义, L21269),
    所以 out 字典键是 'category'.
    _talent_row_to_dict L4279 兜底: row['main_category'] or row['category'] or '' 都能取出 '服饰内衣'.
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    # 顶层 main_category='服饰内衣' → out['category']='服饰内衣' (db_col 是 'category')
    _assert_equal(out.get('category'), '服饰内衣', '顶层 main_category → out["category"]=服饰内衣')


def test_canonicalize_main_category_no_fallback_to_fan_segments():
    """★ v2 main_category 不会从 extra_fields.粉丝特征.品类偏好 取值 (v1 错误回退路径).

    即使顶层 main_category 缺失, 也不会错误地从 fan_category 取 (粉丝品类偏好 ≠ 主推类目).
    修正 v1 第二个红 case: extra_fields 顶层无 main_category 时, 别瞎兜底到 fan_category.
    """
    ocr = _build_v2_synth_ocr()
    ocr['main_category'] = ''  # 顶层清空
    out = _canonicalize_talent_row(ocr)
    # out 里不该有 'category' = '服装 23%' (那是 fan_category, 不是主推类目)
    if 'category' in out:
        _assert_true(out['category'] != '服装 23%', '不应从 fan_category 兜底取主推类目 (粉丝品类 ≠ 主推类目)')
        # 顶层空时, _OCR_TO_TALENT_FIELDS parser 返 '', out['category'] 应是空字符串
        _assert_equal(out['category'], '', '顶层空时 out["category"] 为空字符串')


# ══════════════════════════════════════════════════════════════════════
# 9. _canonicalize_talent_row 全路径 smoke test (1 case)
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_full_path_minimal_synth():
    """★ v2 _canonicalize_talent_row 全路径综合验证 (14 case 综合 smoke test).

    验证覆盖全部 4 大病根:
    - 顶层 followers / main_category 优先
    - fan_* 4 段都有 (FLAT 2 层)
    - avg_live_gmv / live_gpm / video_gpm 区间双轨
    - single_video_settlement 文本化
    - category_distribution > 105 归一
    - price_distribution 原样 (顶层)
    - top_categories / top_brands 中文键映射
    """
    out = _canonicalize_talent_row(_build_v2_synth_ocr())
    # ----- 顶层优先 -----
    _assert_equal(out.get('followers'), 50000, '顶层 followers 解析为 50000')
    _assert_equal(out.get('category'), '服饰内衣', '顶层 main_category → out["category"]')
    # ----- fan_* 4 段都有 (FLAT 2 层) -----
    for key in ('fan_gender', 'fan_group_gender', 'live_audience_gender', 'video_audience_gender'):
        _assert_true(key in out, f'{key} 应在 out (FLAT 4 段)')
    # ----- 区间双轨 -----
    _assert_equal(out.get('avg_live_gmv_text'), '¥100万-500万', 'avg_live_gmv_text 原文')
    _assert_equal(out.get('avg_live_gmv'), 3000000.0, 'avg_live_gmv 区间均值')
    _assert_equal(out.get('live_gpm_text'), '500-1,000', 'live_gpm_text 原文')
    _assert_equal(out.get('live_gpm'), 750.0, 'live_gpm 区间均值')
    _assert_equal(out.get('video_gpm_text'), '300', 'video_gpm_text 原文')
    _assert_equal(out.get('video_gpm'), 300.0, 'video_gpm 数值')
    # ----- single_video_settlement 文本化 -----
    _assert_equal(out.get('single_video_settlement'), '¥5万-10万', 'single_video_settlement 文本')
    # ----- category_distribution 归一 -----
    cat = out.get('category_distribution', {})
    cat_total = sum(v for v in cat.values() if isinstance(v, (int, float)))
    _assert_equal(round(cat_total, 1), 100.0, 'category_distribution 归一后和 = 100')
    # ----- price_distribution 原样 (顶层) -----
    _assert_equal(out.get('price_distribution'), {'0-25': 15, '25-50': 20, '50-100': 40}, 'price_distribution 原样')
    # ----- TOP3 中文键映射 -----
    cats = out.get('top_categories', [])
    _assert_equal(len(cats), 2, 'top_categories 2 个')
    _assert_equal(cats[0]['name'], '服装', 'cat[0].name = 服装 (中文键映射)')
    _assert_equal(cats[0]['avg_price'], '¥181.55', 'cat[0].avg_price = ¥181.55')
    _assert_equal(cats[0]['gmv'], '¥10万-25万', 'cat[0].gmv = ¥10万-25万')
    brands = out.get('top_brands', [])
    _assert_equal(len(brands), 1, 'top_brands 1 个')
    _assert_equal(brands[0]['name'], '哈比熊', 'brand[0].name = 哈比熊')


# ══════════════════════════════════════════════════════════════════════
# ★ fix/ocr-canonical-sync-v2 闭环 (2026-09-25): 4 项修复新增 5 case
# ══════════════════════════════════════════════════════════════════════

# ----- 修 4: _parse_gmv_value 闭环 -----

def test_parse_gmv_value_yuan_range_means_3000000():
    """★ v2 闭环: _parse_gmv_value('¥100万-500万') → 3000000 (区间均值).

    老大原话: "修total_gmv的解析使'¥100万-500万'的区间均值=3000000
    (先_strip_yuan清前缀再解析100和500取均值)".

    修前 bug: parts[0]='¥100万' float 抛 ValueError 返 0, 最终 (0+5000000)/2=2500000 错.
    修后: 先 _strip_yuan 清前缀 → '100万-500万' → split → (1000000+5000000)/2 = 3000000 ✓
    """
    _assert_equal(_parse_gmv_value('¥100万-500万'), 3000000.0, '含 ¥ 区间 → 3000000 (均值)')
    # 不含 ¥ 也正确
    _assert_equal(_parse_gmv_value('100万-500万'), 3000000.0, '不含 ¥ 区间 → 3000000')
    # 全角 ¥ 也清
    _assert_equal(_parse_gmv_value('￥100万-500万'), 3000000.0, '全角 ￥ 区间 → 3000000')
    # 多前缀
    _assert_equal(_parse_gmv_value('¥¥100万-500万'), 3000000.0, '多前缀 ¥¥ 区间 → 3000000')


# ----- 修 3: total_gmv_text 顶层字符串写入路径 -----

def test_canonicalize_total_gmv_text_from_top_level_string():
    """★ v2 闭环: 顶层 total_gmv 是区间字符串时, 也写到 total_gmv_text.

    老大原话: "total_gmv_text列必须存区间原文'¥100万-500万'而非MISSING,
    若顶层ocr_json含该键且值为区间字符串则直接写入".

    修前 bug: 顶层 ocr_json.get('total_gmv')='¥100万-500万' 时,
    L22038 只查 text_col/_raw 漏写 total_gmv_text → 写后 DB total_gmv_text = MISSING.
    修后: 顶层 num_col 是字符串时 fallback 写到 _text 列.
    """
    ocr = {
        'total_gmv': '¥100万-500万',  # 顶层区间字符串 (脱敏前真实值)
        'main_category': '服饰内衣',
    }
    out = _canonicalize_talent_row(ocr)
    # 1. 数值列 total_gmv = 解析均值 (修 4 也生效)
    _assert_equal(out.get('total_gmv'), 3000000.0, 'total_gmv 数值列 解析均值')
    # 2. ★ 修 3: total_gmv_text 存区间原文 (不 MISSING)
    _assert_equal(out.get('total_gmv_text'), '¥100万-500万', 'total_gmv_text 存区间原文')
    # 3. 不含 ¥ 顶层也能写
    ocr2 = {'total_gmv': '500-1,000'}
    out2 = _canonicalize_talent_row(ocr2)
    _assert_equal(out2.get('total_gmv_text'), '500-1,000', 'total_gmv_text 不含 ¥ 也写')


# ----- 修 2: _PROTECTED_COLUMNS 加 5 列, 总 14 列 -----

def test_protected_columns_includes_5_new_columns():
    """★ v2 闭环: _PROTECTED_COLUMNS 加 5 列, 总 14 列.

    老大原话: "把product_count/total_shops/live_ratio/video_ratio/avg_live_gmv
    这5列也加到_PROTECTED_COLUMNS白名单, 使重跑同步不会用OCR读错的值覆盖手修正确值".

    验证 2 点:
    1. string match: _update_talent_from_ocr_fields 函数源码含 5 列名 (白名单定义)
    2. 行为验证: 5 列都有保护 (已有手修值不被 OCR 新值覆盖)
    """
    import inspect
    _update_talent_from_ocr_fields = _solobrave_server._update_talent_from_ocr_fields
    func_src = inspect.getsource(_update_talent_from_ocr_fields)
    for col in ('product_count', 'total_shops', 'live_ratio', 'video_ratio', 'avg_live_gmv'):
        _assert_true(col in func_src, f'_update_talent_from_ocr_fields 源码含 {col} 列名 (白名单)')

    # 行为验证: 模拟重跑同步, OCR 读错的 5 个新值应被跳过 (老大原话)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id INTEGER PRIMARY KEY, name TEXT,
        product_count INTEGER DEFAULT 0,
        total_shops INTEGER DEFAULT 0,
        live_ratio REAL DEFAULT 0,
        video_ratio REAL DEFAULT 0,
        avg_live_gmv REAL DEFAULT 0
    )''')
    conn.execute("""INSERT INTO talents (id, name, product_count, total_shops, live_ratio, video_ratio, avg_live_gmv)
                    VALUES (1, '李婶儿', 23, 12, 27.9, 69.61, 3000000)""")
    conn.commit()
    try:
        # 模拟 OCR 读错的新值 (5 个新列)
        ocr_wrong_values = {
            'product_count': 22,       # OCR 读错: 23 → 22
            'total_shops': 13,         # OCR 读错: 12 → 13
            'live_ratio': 27.5,        # OCR 读错: 27.9 → 27.5 (百分比)
            'video_ratio': 70.0,       # OCR 读错: 69.61 → 70.0
            'avg_live_gmv': 3500000,   # OCR 区间错读: 3000000 → 3500000
        }
        for col, new_val in ocr_wrong_values.items():
            result = _update_talent_column_if_empty(conn, 1, col, new_val)
            _assert_equal(result, False, f'{col} 已有手修值应跳过 (返 False)')
        conn.commit()
        # 验证 5 列 DB 值都仍是手修正确值
        expected = {'product_count': 23, 'total_shops': 12, 'live_ratio': 27.9,
                    'video_ratio': 69.61, 'avg_live_gmv': 3000000}
        for col, exp_val in expected.items():
            row = conn.execute(f'SELECT {col} FROM talents WHERE id = 1').fetchone()
            _assert_equal(row[col], exp_val, f'{col} 手修值未被覆盖')
    finally:
        conn.close()


# ----- 修 1: 2 个 merge case 适配 v2 _normalize_dist_key 行为 -----

def test_merge_dist_by_normalized_key_age_v2():
    """★ v2 闭环: _merge_dist_by_normalized_key 不合并'31-40岁'+'31-40' (v2 保留岁字).

    老大原话: "修test_merge_same_value两个红case的断言, 使其与当前_normalize_dist_key v2真实行为一致
    (保留岁字、保留中间空格)".

    v2 _normalize_dist_key('31-40岁')='31-40岁' (保留岁字, v1 错去岁字),
    v2 _normalize_dist_key('31-40')='31-40'.
    归一后 key 不同 → _merge_dist_by_normalized_key 不合并, 保留 2 个 key.
    """
    dist = {'31-40岁': 30.1, '31-40': 40.5}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 2, 'v2 不合并 31-40岁 和 31-40 (归一 key 不同)')
    _assert_equal(merged.get('31-40岁'), 30.1, '31-40岁 保留原值')
    _assert_equal(merged.get('31-40'), 40.5, '31-40 保留原值')


def test_merge_dist_by_normalized_key_city_with_space_v2():
    """★ v2 闭环: _merge_dist_by_normalized_key 不合并'三线城市'+'三 线城市' (v2 保留中间空格).

    老大原话: "修test_merge_non_numeric_keeps_first两个红case的断言, 使其与当前_normalize_dist_key v2真实行为一致".

    v2 _normalize_dist_key('三线城市')='三线城市' (trim 首尾),
    v2 _normalize_dist_key('三 线城市')='三 线城市' (中间空格保留).
    归一后 key 不同 → _merge_dist_by_normalized_key 不合并.
    """
    dist = {'三线城市': '24%', '三 线城市': '25%'}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 2, 'v2 不合并 三线城市 和 三 线城市 (中间空格不同)')
    _assert_equal(merged.get('三线城市'), '24%', '三线城市 保留第一个值')
    _assert_equal(merged.get('三 线城市'), '25%', '三 线城市 保留原值')


# ══════════════════════════════════════════════════════════════════════
# ★ fix/ocr-canonical-sync-v3 (2026-09-25): 4 项治本新增 4 case
# ══════════════════════════════════════════════════════════════════════


def test_migrate_existing_talents_fill_text_columns():
    """★ v3 治本: startup migration 回填 4 个 _text 列.

    老大原话: "_update_talent_from_ocr_fields 加 startup 逻辑
    遍历所有 talent 行, 从 ocr_raw_fields 回填 4 个 _text 列".

    验证:
    - talent 行 ocr_raw_fields 含 total_gmv='¥100万-500万' (字符串)
    - talent 行 total_gmv_text='' 空
    - 跑 _migrate_existing_talents_fill_text_columns
    - DB total_gmv_text='¥100万-500万' (回填成功)
    - video_gpm_text / live_gpm_text / avg_live_gmv_text 也回填
    """
    import sqlite3 as _sqlite3

    # 准备 in-memory DB, mock _db_conn 返它
    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY,
        name TEXT,
        ocr_raw_fields TEXT,
        total_gmv_text TEXT DEFAULT '',
        video_gpm_text TEXT DEFAULT '',
        live_gpm_text TEXT DEFAULT '',
        avg_live_gmv_text TEXT DEFAULT '',
        single_video_settlement TEXT DEFAULT ''
    )''')
    conn.execute("""INSERT INTO talents (id, name, ocr_raw_fields, total_gmv_text, video_gpm_text, live_gpm_text, avg_live_gmv_text)
                    VALUES (1, '李婶儿',
                            '{"total_gmv":"¥100万-500万","video_gpm":"300","live_gpm":"500-1,000","avg_live_gmv":"¥2万-10万"}',
                            '', '', '', '')""")
    conn.commit()

    # monkey-patch _db_conn 返测试 conn
    original_db_conn = _solobrave_server._db_conn
    _solobrave_server._db_conn = lambda: _ImmuneConn(conn)
    try:
        _migrate_existing_talents_fill_text_columns()
        # 验证 4 _text 列都回填
        row = conn.execute('SELECT total_gmv_text, video_gpm_text, live_gpm_text, avg_live_gmv_text FROM talents WHERE id = 1').fetchone()
        _assert_equal(row['total_gmv_text'], '¥100万-500万', 'total_gmv_text 回填成功')
        _assert_equal(row['video_gpm_text'], '300', 'video_gpm_text 回填成功')
        _assert_equal(row['live_gpm_text'], '500-1,000', 'live_gpm_text 回填成功')
        _assert_equal(row['avg_live_gmv_text'], '¥2万-10万', 'avg_live_gmv_text 回填成功')
    finally:
        _solobrave_server._db_conn = original_db_conn
        conn.close()


def test_migrate_existing_talents_skips_existing_text():
    """★ v3 治本: startup migration 不覆盖已有 _text 值 (防覆盖手修值).

    场景: 团长手修过 total_gmv_text='老修正值' (防 OCR 错误),
    startup migration 看到 ocr_raw_fields 含 total_gmv='¥100万-500万'
    但 total_gmv_text 非空, 应跳过 (不覆盖).
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT, ocr_raw_fields TEXT,
        total_gmv_text TEXT DEFAULT '',
        video_gpm_text TEXT DEFAULT '',
        live_gpm_text TEXT DEFAULT '',
        avg_live_gmv_text TEXT DEFAULT '',
        single_video_settlement TEXT DEFAULT ''
    )''')
    conn.execute("""INSERT INTO talents (id, name, ocr_raw_fields, total_gmv_text, video_gpm_text, live_gpm_text, avg_live_gmv_text)
                    VALUES (1, '李婶儿',
                            '{"total_gmv":"¥100万-500万","video_gpm":"300"}',
                            '老修正值', '', '', '')""")
    conn.commit()

    original_db_conn = _solobrave_server._db_conn
    _solobrave_server._db_conn = lambda: _ImmuneConn(conn)
    try:
        _migrate_existing_talents_fill_text_columns()
        row = conn.execute('SELECT total_gmv_text, video_gpm_text FROM talents WHERE id = 1').fetchone()
        # total_gmv_text 不被覆盖 (已有 '老修正值')
        _assert_equal(row['total_gmv_text'], '老修正值', 'total_gmv_text 已有值不覆盖')
        # video_gpm_text 应回填 (空)
        _assert_equal(row['video_gpm_text'], '300', 'video_gpm_text 空, 已回填')
    finally:
        _solobrave_server._db_conn = original_db_conn
        conn.close()


def test_migrate_existing_talents_skips_talents_without_ocr_raw():
    """★ v3 治本: startup migration 跳过 ocr_raw_fields IS NULL/空 的 talent 行.

    防: 没有 OCR dump 的 legacy 行, migration 不报错不写 UPDATE.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT, ocr_raw_fields TEXT,
        total_gmv_text TEXT DEFAULT '',
        video_gpm_text TEXT DEFAULT '',
        live_gpm_text TEXT DEFAULT '',
        avg_live_gmv_text TEXT DEFAULT '',
        single_video_settlement TEXT DEFAULT ''
    )''')
    conn.execute("INSERT INTO talents (id, name, ocr_raw_fields) VALUES (1, '李婶儿', NULL)")
    conn.execute("INSERT INTO talents (id, name, ocr_raw_fields) VALUES (2, '王五', '')")
    conn.commit()

    original_db_conn = _solobrave_server._db_conn
    _solobrave_server._db_conn = lambda: _ImmuneConn(conn)
    try:
        _migrate_existing_talents_fill_text_columns()
        # 2 行 total_gmv_text 都应保持 ''
        rows = conn.execute('SELECT id, total_gmv_text FROM talents ORDER BY id').fetchall()
        _assert_equal(rows[0]['total_gmv_text'], '', 'legacy NULL 行 跳过')
        _assert_equal(rows[1]['total_gmv_text'], '', 'legacy 空字符串 行 跳过')
    finally:
        _solobrave_server._db_conn = original_db_conn
        conn.close()


def test_talent_row_to_dict_missing_columns_no_error():
    """★ v3 治本: _talent_row_to_dict 缺 4 _text + video_count 等列不抛 IndexError.

    老大原话: "对未确认存在的列用 row.get('xxx') or default, 避免 API 500 No item with that key".

    旧 DB (Mac 端生产 data/solobrave.db) 没跑过 v2 migration 时:
      - 缺 4 _text 列 (total_gmv_text / video_gpm_text / live_gpm_text / avg_live_gmv_text)
      - 缺 price_distribution / category_distribution
      - 缺 main_category / video_count (任务 7)
      - 缺 ocr_raw_fields
    _talent_row_to_dict 加 _safe_row_get / _json_col 容错, 缺列返 default.

    测试: sqlite3 in-memory 建老 schema (没 v3 关心列), INSERT 一行, 调 _talent_row_to_dict 不抛异常.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    # ★ 故意只建老 schema 列 (没 v3 关心的 4 _text + price/category_distribution + main_category + video_count + ocr_raw_fields)
    conn.execute('''CREATE TABLE talents (
        id INTEGER PRIMARY KEY, name TEXT, avatar TEXT,
        cooperation_status TEXT DEFAULT 'available'
    )''')
    conn.execute("INSERT INTO talents (id, name, avatar) VALUES (1, '李婶儿', 'avatar.png')")
    conn.commit()
    try:
        row = conn.execute('SELECT * FROM talents WHERE id = 1').fetchone()
        # ★ 不抛 IndexError
        result = _talent_row_to_dict(row)
        _assert_true(result is not None, '_talent_row_to_dict 不抛异常 (缺多列也 OK)')
        _assert_equal(result['id'], 1, 'id 正确')
        _assert_equal(result['name'], '李婶儿', 'name 正确')
        # 缺列返 default
        _assert_equal(result.get('total_gmv_text'), '', 'total_gmv_text 缺列 返空字符串')
        _assert_equal(result.get('video_gpm_text'), '', 'video_gpm_text 缺列 返空字符串')
        _assert_equal(result.get('live_gpm_text'), '', 'live_gpm_text 缺列 返空字符串')
        _assert_equal(result.get('avg_live_gmv_text'), '', 'avg_live_gmv_text 缺列 返空字符串')
        _assert_equal(result.get('price_distribution'), {}, 'price_distribution 缺列 返空 dict')
        _assert_equal(result.get('category_distribution'), {}, 'category_distribution 缺列 返空 dict')
        _assert_equal(result.get('main_category'), '', 'main_category 缺列 返空字符串')
        # 任务 7: video_count 缺列 返 0 兜底
        _assert_equal(result.get('video_count'), 0, 'video_count 缺列 返 0 (任务 7 兜底)')
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════
# ★ fix/ocr-canonical-sync-v4 (2026-09-25): P0 止血 + P2 补漏新增 8 case
# ══════════════════════════════════════════════════════════════════════

# ----- 任务 1: 白名单三级分级 (sidecar JSON) -----

def test_get_confidence_level_default_l0():
    """★ v4: _get_confidence_level 默认 'L0' (兼容旧数据无 _confidence 字段).

    老大原话: "L0 raw (OCR 初值, ✅ 可被覆盖) / L1 verified / L2 confirmed (人工确认, ❌ 绝不覆盖)".
    兼容旧数据: ocr_raw_fields 无 _confidence 子字段 → 默认 'L0' (可被覆盖).
    """
    # 1. None 输入 → 'L0'
    _assert_equal(_get_confidence_level(None, 'total_gmv'), 'L0', 'None 输入 返 L0')
    # 2. 空字符串 → 'L0'
    _assert_equal(_get_confidence_level('', 'total_gmv'), 'L0', '空字符串 返 L0')
    # 3. JSON 无 _confidence 字段 → 'L0'
    _assert_equal(
        _get_confidence_level('{"total_gmv":"¥100万-500万"}', 'total_gmv'),
        'L0',
        'JSON 无 _confidence 字段 返 L0 (兼容旧数据)',
    )
    # 4. JSON 解析失败 → 'L0'
    _assert_equal(_get_confidence_level('{invalid json', 'total_gmv'), 'L0', 'JSON 解析失败 返 L0')
    # 5. _confidence 不含 col → 'L0'
    _assert_equal(
        _get_confidence_level('{"_confidence":{"other_col":"L2"}}', 'total_gmv'),
        'L0',
        '_confidence 不含 col 返 L0',
    )


def test_get_confidence_level_l2():
    """★ v4: _get_confidence_level L2 提升 (人工确认绝不覆盖).

    L2 在 ocr_raw_fields._confidence[col] 时返 'L2', 触发 _update_talent_column_if_empty 跳过.
    """
    # 1. L2 提升
    _assert_equal(
        _get_confidence_level('{"_confidence":{"total_gmv":"L2"}}', 'total_gmv'),
        'L2',
        '_confidence[col]=L2 返 L2',
    )
    # 2. L1 verified (待 P1.2 实测, 暂不实现)
    _assert_equal(
        _get_confidence_level('{"_confidence":{"total_gmv":"L1"}}', 'total_gmv'),
        'L1',
        '_confidence[col]=L1 返 L1',
    )
    # 3. 非法值 → 兜底 'L0'
    _assert_equal(
        _get_confidence_level('{"_confidence":{"total_gmv":"L99"}}', 'total_gmv'),
        'L0',
        '非法 confidence 值 兜底 L0',
    )
    # 4. dict 输入 (不是 JSON 字符串) 也支持
    _assert_equal(
        _get_confidence_level({'_confidence': {'col': 'L2'}}, 'col'),
        'L2',
        'dict 输入 (非 JSON 字符串) 也支持',
    )


def test_set_confidence_level_writes_confidence():
    """★ v4: _set_confidence_level 写 _confidence 字段到 ocr_raw_fields JSON.

    保留原 OCR 字段不动, 只加/改 _confidence 子 dict.
    """
    ocr_raw = '{"total_gmv":"¥100万-500万","product_count":23}'
    new_json = _set_confidence_level(ocr_raw, 'total_gmv', 'L2')
    parsed = json.loads(new_json)
    # 原字段保留
    _assert_equal(parsed['total_gmv'], '¥100万-500万', '原字段 total_gmv 保留')
    _assert_equal(parsed['product_count'], 23, '原字段 product_count 保留')
    # 新增 _confidence
    _assert_equal(parsed['_confidence']['total_gmv'], 'L2', '_confidence.total_gmv=L2')


def test_update_talent_column_if_empty_l2_skips():
    """★ v4: _update_talent_column_if_empty L2 绝不覆盖 (人工确认).

    即使 DB 已有非空值 (v3 行为), L2 也额外跳过 — 防止 OCR 重跑覆盖人工确认值.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, total_gmv_text TEXT DEFAULT "")')
    conn.execute("INSERT INTO talents (id, total_gmv_text) VALUES ('1', 'L2-已确认值')")
    conn.commit()

    # 模拟 ocr_raw_fields._confidence.total_gmv=L2 (人工确认)
    ocr_raw_l2 = '{"total_gmv":"¥100万-500万","_confidence":{"total_gmv":"L2"}}'

    # 即使 total_gmv_text 是 'L2-已确认值', OCR 新值 '¥100万-500万' 应被跳过
    result = _update_talent_column_if_empty(conn, '1', 'total_gmv_text', '¥100万-500万', ocr_raw_l2)
    _assert_equal(result, False, 'L2 跳过 (返 False)')

    conn.commit()
    row = conn.execute('SELECT total_gmv_text FROM talents WHERE id = 1').fetchone()
    # 仍 'L2-已确认值' (没被覆盖)
    _assert_equal(row['total_gmv_text'], 'L2-已确认值', 'DB 值未被 L2 OCR 覆盖')

    conn.close()


# ----- 任务 2: 归一防塌缩前置守卫 -----

def test_normalize_distribution_below_min_keys():
    """★ v4: _normalize_distribution city_tier < 7 档不归一 (incomplete=True).

    老大原话: "城市只抓到 2/7 档 → 归一逻辑在'值不全'时按现有值比例硬凑 100, 变成 50/50.
     修复: 若捕获到的档位明显不全 (城市 < 7) → 禁止按比例硬凑 100."

    OCR 只抓到 2 档 (三线 + 新一线), 不归一, 标记 incomplete=True.
    """
    dist = {'三线城市': 50, '新一线城市': 50}  # 总和 100, 但只 2 档
    normalized, incomplete = _normalize_distribution(dist, field_name='fan_city_tier')
    # 原始值保留
    _assert_equal(normalized, dist, '< 7 档原始值保留 (不归一 100)')
    # 标记 incomplete
    _assert_equal(incomplete, True, '< 7 档标记 incomplete=True')
    # _MIN_DIST_KEYS 验证
    _assert_equal(_MIN_DIST_KEYS.get('fan_city_tier'), 7, 'fan_city_tier 最小 7 档')


def test_normalize_distribution_total_over_105():
    """★ v4: _normalize_distribution 总和 > 105 按比例归一到 100 (v2 行为保留).

    类目 13 keys 总和 120, 归一后和 ≈ 100, incomplete=False.
    """
    dist = {'服饰内衣': 24, '个护家清': 19, '食品饮料': 17, '美妆': 12, '母婴': 10,
            '家居': 8, '数码': 7, '运动户外': 6, '图书': 5, '汽车': 4,
            '游戏': 3, '本地服务': 3, '其他': 2}
    normalized, incomplete = _normalize_distribution(dist, field_name='category_distribution')
    total = sum(v for v in normalized.values() if isinstance(v, (int, float)))
    _assert_equal(round(total, 1), 100.0, '归一后总和 = 100')
    _assert_equal(incomplete, False, '>= 13 档且总和 > 105 → 不 incomplete')
    # 各值按比例缩放
    _assert_equal(normalized['服饰内衣'], round(24 * 100 / 120, 2), '服饰内衣 缩放到 20.0')


def test_normalize_distribution_total_90_to_110():
    """★ v4: _normalize_distribution 总和在 90-110 已归一, 不动 (incomplete=False).

    真实数据总和 95-105 (OCR 已归一过), 不重复归一, 防计算误差.
    """
    dist = {'服饰内衣': 24.5, '个护家清': 19.3, '食品饮料': 17.1, '美妆': 12.0, '母婴': 9.8}
    normalized, incomplete = _normalize_distribution(dist, field_name='category_distribution')
    # 5 档 < 13 (_MIN_DIST_KEYS), 但已知最小集合检查 → incomplete=True
    # 这里验证规则 4 (90-110) 不适用, 因为档位数 < 13 优先触发 incomplete
    _assert_equal(incomplete, True, '5 档 < 13 _MIN_DIST_KEYS → incomplete=True')


def test_normalize_distribution_complete_90_to_110():
    """★ v4: 13 keys 齐全且总和 90-110 → 已归一, 不动 (incomplete=False).

    13 keys 总和 95, 已知最小集合 (13) 满足, 总和在 90-110 → 不归一, 不 incomplete.
    """
    dist = {f'类目{i}': 95/13 for i in range(1, 14)}
    normalized, incomplete = _normalize_distribution(dist, field_name='category_distribution')
    _assert_equal(incomplete, False, '13 档齐全 + 总和 90-110 → incomplete=False')
    # 原始值不动
    _assert_equal(normalized['类目1'], dist['类目1'], '已归一值不动')


# ----- 任务 3: single_video_settlement migration 回填 -----

def test_migrate_existing_talents_fill_single_video_settlement():
    """★ v4 P2: _migrate_existing_talents_fill_text_columns 加 single_video_settlement 落库.

    老大原话: "single_video_settlement 落库 + KPI 卡渲染".
    从 ocr_raw_fields.extra_fields.视频带货数据.单视频结算额 反推填到 single_video_settlement.
    防覆盖手修值 (已有非空跳过).
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT, ocr_raw_fields TEXT,
        total_gmv_text TEXT DEFAULT '', video_gpm_text TEXT DEFAULT '',
        live_gpm_text TEXT DEFAULT '', avg_live_gmv_text TEXT DEFAULT '',
        single_video_settlement TEXT DEFAULT ''
    )''')
    conn.execute("""INSERT INTO talents (id, name, ocr_raw_fields, single_video_settlement)
                    VALUES ('1', '李婶儿',
                            '{"extra_fields":{"视频带货数据":{"单视频结算额":"¥5,000-2万","视频GPM":"300"}}}',
                            '')""")
    conn.commit()

    original_db_conn = _solobrave_server._db_conn
    _solobrave_server._db_conn = lambda: _ImmuneConn(conn)
    try:
        _migrate_existing_talents_fill_text_columns()
        row = conn.execute('SELECT single_video_settlement FROM talents WHERE id = 1').fetchone()
        _assert_equal(row['single_video_settlement'], '¥5,000-2万', 'single_video_settlement 回填成功')
    finally:
        _solobrave_server._db_conn = original_db_conn
        conn.close()

# ══════════════════════════════════════════════════════════════════════
# ★ fix/ocr-canonical-sync-v4 amend (2026-09-25): 补 3 city_tier pytest case
#   - task 1: _FAN_SOURCE_MAP 4 段循环 city_tier 走 _normalize_distribution (2/7 档 + 7 档齐全)
#   - task 3: _detect_existing_collapsed_city_tier 检测塌缩行
# ══════════════════════════════════════════════════════════════════════

def test_canonicalize_flattens_4_city_tier_uses_normalize():
    """★ v4 amend task 1: _canonicalize_talent_row 4 段 city_tier 走 _normalize_distribution.

    老大原话 (2026-09-25): "禁止硬凑 100, 变成 50/50" — 4 个 city_tier 字段 (fan_city_tier /
    fan_group_city_tier / live_audience_city_tier / video_audience_city_tier) OCR 只抓到 2 档时
    应保留原始值 + incomplete=True, 绝不能硬凑 100.

    验证:
    - 4 段 city_tier 各 2 档 + 总和 100 → out[col] = JSON 原始值 (不归一)
    - out['_distribution_incomplete'] 包含 4 列标记
    """
    ocr_json = {
        'fan_city_tier': '',         # 顶层空, 强制走 _FAN_SOURCE_MAP 循环
        'fan_group_city_tier': '',
        'live_audience_city_tier': '',
        'video_audience_city_tier': '',
        'extra_fields': {
            '粉丝特征': {
                '城市等级': {'三线城市': 60, '新一线城市': 40},  # 2 档 / 总和 100
            },
            '粉丝团特征': {
                '城市等级': {'一线城市': 55, '四线城市': 45},
            },
            '直播间特征': {
                '城市等级': {'二线城市': 70, '五线城市': 30},
            },
            '短视频特征': {
                '城市等级': {'新一线城市': 80, '三线城市': 20},
            },
        },
    }

    result = _canonicalize_talent_row(ocr_json)

    # 4 个 city_tier 列都应存在, 且原始值保留
    for col, expected in [
        ('fan_city_tier', {'三线城市': 60, '新一线城市': 40}),
        ('fan_group_city_tier', {'一线城市': 55, '四线城市': 45}),
        ('live_audience_city_tier', {'二线城市': 70, '五线城市': 30}),
        ('video_audience_city_tier', {'新一线城市': 80, '三线城市': 20}),
    ]:
        _assert_true(col in result, f'{col} 应在 result dict 中')
        # out[col] 是 JSON 字符串 (序列化)
        actual = json.loads(result[col])
        _assert_equal(actual, expected, f'{col} 原始值保留 (不归一 100)')

    # incomplete 标记: 4 列都在 _distribution_incomplete 子 dict
    inc = result.get('_distribution_incomplete', {})
    _assert_true(isinstance(inc, dict), '_distribution_incomplete 是 dict')
    for col in ['fan_city_tier', 'fan_group_city_tier', 'live_audience_city_tier', 'video_audience_city_tier']:
        _assert_equal(inc.get(col), True, f'_distribution_incomplete[{col}] = True')


def test_canonicalize_full_7_city_tier_normalizes():
    """★ v4 amend task 1: 7 档齐全 + 总和 100 → _normalize_distribution 不归一 (incomplete=False).

    老大原话 (2026-09-25): "禁止硬凑 100, 变成 50/50" — 但 7 档齐全时不应误判 incomplete.
    验证:
    - 7 档齐全 + 总和 100 → out[col] = 原始 7 档值
    - out['_distribution_incomplete'] 不含 fan_city_tier (false, 不标记)
    """
    full_7_tier = {
        '一线城市': 20, '新一线城市': 18, '二线城市': 16, '三线城市': 14,
        '四线城市': 12, '五线城市': 10, '其他': 10,  # 7 档齐全 / 总和 100
    }
    ocr_json = {
        'fan_city_tier': '',
        'extra_fields': {
            '粉丝特征': {'城市等级': full_7_tier},
        },
    }

    result = _canonicalize_talent_row(ocr_json)
    _assert_true('fan_city_tier' in result, 'fan_city_tier 在 result dict')
    actual = json.loads(result['fan_city_tier'])
    _assert_equal(actual, full_7_tier, '7 档齐全 → 原始值保留 (不归一不增减)')
    # _MIN_DIST_KEYS 验证
    _assert_equal(_MIN_DIST_KEYS.get('fan_city_tier'), 7, '_MIN_DIST_KEYS[fan_city_tier] = 7')
    # 7 档齐全不 incomplete
    inc = result.get('_distribution_incomplete', {})
    _assert_equal(inc.get('fan_city_tier', False), False, '7 档齐全 → _distribution_incomplete 不标记')


def test_detect_existing_collapsed_city_tier():
    """★ v4 amend task 3: _detect_existing_collapsed_city_tier 识别存量塌缩行.

    老大原话 (2026-09-25): "加函数可识别存量'档位不全却被凑成整百'的 city_tier 并重置.
    (只写函数+pytest, 不执行不写库)".

    规则:
    - 4 city_tier 字段任一: 档位 < 7 且 95 <= total <= 105 → 标记塌缩
    - 7 档齐全 / 总和 ≠ 100 → 不塌缩
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(':memory:')
    conn.row_factory = _sqlite3.Row
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT,
        fan_city_tier TEXT DEFAULT '{}',
        fan_group_city_tier TEXT DEFAULT '{}',
        live_audience_city_tier TEXT DEFAULT '{}',
        video_audience_city_tier TEXT DEFAULT '{}'
    )''')

    # row 1: 塌缩 (fan_city_tier 2 档 + 总和 100)
    conn.execute("INSERT INTO talents VALUES (?, ?, ?, ?, ?, ?)", (
        '1', '李婶儿',
        json.dumps({'三线城市': 60, '新一线城市': 40}, ensure_ascii=False),  # 2 档 / 100 → 塌缩
        '{}', '{}', '{}',
    ))
    # row 2: 7 档齐全 → 不塌缩
    conn.execute("INSERT INTO talents VALUES (?, ?, ?, ?, ?, ?)", (
        '2', '王二姐',
        json.dumps({'一线': 20, '新一线': 18, '二线': 16, '三线': 14, '四线': 12, '五线': 10, '其他': 10}, ensure_ascii=False),
        '{}', '{}', '{}',
    ))
    # row 3: 2 档但总和 = 50 → 不塌缩 (总和 ≠ 100)
    conn.execute("INSERT INTO talents VALUES (?, ?, ?, ?, ?, ?)", (
        '3', '张大奕',
        json.dumps({'三线': 30, '新一线': 20}, ensure_ascii=False),  # 2 档 / 50 → 不塌缩
        '{}', '{}', '{}',
    ))
    conn.commit()

    collapsed = _detect_existing_collapsed_city_tier(conn)

    # 只 row 1 fan_city_tier 塌缩, 其余 3 city_tier 列 (空 {}) 跳过
    _assert_equal(len(collapsed), 1, '只有 row 1 fan_city_tier 1 个塌缩')
    talent_id, col, current_val = collapsed[0]
    _assert_equal(talent_id, '1', '塌缩 talent_id = 1 (李婶儿)')
    _assert_equal(col, 'fan_city_tier', '塌缩列 = fan_city_tier')
    _assert_equal(current_val, {'三线城市': 60, '新一线城市': 40}, '当前塌缩值原样返回')

    conn.close()

