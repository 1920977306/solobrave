"""★ fix/ocr-dist-key-normalize-backend: 分布 dict key 归一化单测.

覆盖:
  1) _normalize_dist_key (基础归一)
  2) _merge_dist_by_normalized_key (同义异名合并, 取较大值)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/test_ocr_dist_normalize.py -v

退出码 0 = 全部通过, 1 = 有失败.

★ fix/ocr-dist-key-normalize-backend: 同样用 importlib.util.spec_from_file_location
  加载 server (跟 talent_full_sync_test.py L21-38 模板一致), Windows Mini 端
  静态分析用例逻辑, Mac 端跑 pytest 验证.
"""
import importlib.util
import sys
from pathlib import Path

# 跟其他 test_*.py 一致: 先把项目根加进 sys.path, 让 server 内部 import 能解析
sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

sys.modules['solobrave_server'] = _solobrave_server

_normalize_dist_key = _solobrave_server._normalize_dist_key
_merge_dist_by_normalized_key = _solobrave_server._merge_dist_by_normalized_key
_DIST_FIELDS_NORMALIZE = _solobrave_server._DIST_FIELDS_NORMALIZE


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


# ===== _normalize_dist_key =====

def test_normalize_age_with_sui():
    """★ fix/ocr-canonical-sync-v2: '31-40岁' → '31-40岁' (保留岁字)

    v1 (去岁字) → v2 (保留岁字). 老大 2026-09-25 反馈:
      '31-40岁' / '31-40' 同义异名, merge 时 _merge_dist_by_normalized_key
      按归一 key 取较大值, 无需提前吞 '岁' 字. 保留原样让 OCR 数据完整可追溯.
    """
    _assert_equal(_normalize_dist_key('31-40岁'), '31-40岁', '带岁字保留 (v2)')
    _assert_equal(_normalize_dist_key('31-40'), '31-40', '不带岁字保持')


def test_normalize_age_fullwidth():
    """全角数字 → 半角 ('３１-４０' → '31-40')"""
    _assert_equal(_normalize_dist_key('３１-４０'), '31-40', '全角数字归一')
    _assert_equal(_normalize_dist_key('１８-２３'), '18-23', '全角数字归一 18-23')


def test_normalize_city():
    """★ fix/ocr-canonical-sync-v2: 城市保留中间空格 + trim 首尾

    v1 (去中间空格) → v2 (保留中间空格). 老大 2026-09-25 反馈:
      '三 线城市' 与 '三线城市' 是 OCR 视觉模型两种写法, 中间空格是有意义的分隔,
      v2 不应盲目去. _merge_dist_by_normalized_key 会按归一 key (trim 后) 合并取较大值.
    """
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', '城市保持')
    _assert_equal(_normalize_dist_key('三 线城市'), '三 线城市', '中间半角空格保留 (v2)')
    _assert_equal(_normalize_dist_key('  一线城市  '), '一线城市', '首尾空格 trim')


def test_normalize_dash_variants():
    """横线归一 (en-dash / em-dash / 减号 → '-')"""
    _assert_equal(_normalize_dist_key('31\u201340'), '31-40', 'en-dash 归一')
    _assert_equal(_normalize_dist_key('31\u201440'), '31-40', 'em-dash 归一')
    _assert_equal(_normalize_dist_key('31\u201540'), '31-40', '水平线归一')
    _assert_equal(_normalize_dist_key('31\u221240'), '31-40', '减号归一')


def test_normalize_non_string():
    """非字符串输入转 str"""
    _assert_equal(_normalize_dist_key(123), '123', '数字输入')
    _assert_equal(_normalize_dist_key(None), '', 'None 输入')


# ===== _merge_dist_by_normalized_key =====

def test_merge_same_value():
    """★ fix/ocr-canonical-sync-v2: 同义异名 + 占比相同, v2 保留岁字 → 2 个 key 不合并.

    v1 (去岁字): '31-40岁' + '31-40' → _normalize_dist_key 归一后同 key → 合并 1 个
    v2 (保留岁字): 归一后 key 不同 → 不合并 2 个, 各保留原值.
    """
    dist = {'31-40岁': 30.1, '31-40': 30.1}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 2, 'v2 不合并 31-40岁 和 31-40 (保留岁字)')
    _assert_equal('31-40岁' in merged, True, '31-40岁 保留原值')
    _assert_equal('31-40' in merged, True, '31-40 保留原值')


def test_merge_take_max():
    """同义异名 key 不同值, 取较大值"""
    dist = {'31-40岁': 30.1, '31-40': 40.5}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(merged['31-40'], 40.5, '取较大值 40.5')


def test_normalize_dist_key_new_tier_alias_merged():
    """★ fix/mini-test-code-repair-20260925 11:21: '新一线' + '新一线城市' 合并.

    老大 brief 钉死: OCR 录入 city_tier dict 同时含短形式 '新一线' (17.5%) + 长形式 '新一线城市' (17.4%),
    旧 _normalize_dist_key 中文键 identity 不动 → 合并阶段因 key 不同不合并, 两行展示.
    现在: '新一线' → '新一线城市' 归一, 合并阶段同 key 合并 (max 取较大值).
    """
    # 基础归一
    _assert_equal(_normalize_dist_key('新一线'), '新一线城市', "'新一线' 短形式归一为 '新一线城市'")
    _assert_equal(_normalize_dist_key('新一线城市'), '新一线城市', "'新一线城市' 长形式 identity")
    _assert_equal(_normalize_dist_key('  新一线  '), '新一线城市', "trim + 短归一")
    _assert_equal(_normalize_dist_key('  新一线城市  '), '新一线城市', "trim + identity")

    # 反向不破坏 (其他档位未改, 短长形式都保留原值)
    _assert_equal(_normalize_dist_key('一线城市'), '一线城市', "'一线城市' identity (其他档未改)")
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', "'三线城市' identity")

    # 严格匹配, 扩展字符串不破坏
    _assert_equal(_normalize_dist_key('新一线城市abc'), '新一线城市abc', "'新一线城市abc' 严格 s== 不破坏扩展")

    # 合并 case (归一后同 key, _merge_dist_by_normalized_key 取 max)
    merged = _merge_dist_by_normalized_key({'新一线': 17.5, '新一线城市': 17.4})
    _assert_equal(len(merged), 1, "合并后 1 个 key ('新一线' 归一为 '新一线城市')")
    _assert_equal('新一线城市' in merged, True, "合并后 key = '新一线城市'")
    _assert_equal(merged['新一线城市'], 17.5, "合并后值取较大值 17.5 (max 逻辑)")


def test_merge_fullwidth_dash():
    """全角数字 + 横线归一后合并"""
    dist = {'３１-４０岁': 30.1, '31-40': 40.5}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(merged['31-40'], 40.5, '全角横线归一后取较大值')


def test_merge_keeps_unique():
    """不同 key 保持独立"""
    dist = {'18-23': 10, '24-30': 20, '31-40': 30}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 3, '3 个不同 key 保持')
    _assert_equal(merged, dist, '值不变')


def test_merge_non_numeric_keeps_first():
    """★ fix/ocr-canonical-sync-v2: 非数值 dict merge, v2 保留中间空格 → 2 个 key 不合并.

    v1 (去中间空格): '三线城市' + '三 线城市' → _normalize_dist_key 归一后同 key → 合并 1 个
    v2 (保留中间空格): 归一后 key 不同 → 不合并 2 个, 各保留原值.
    """
    dist = {'三线城市': '24%', '三 线城市': '25%'}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 2, 'v2 不合并 三线城市 和 三 线城市 (保留中间空格)')
    _assert_equal(merged['三线城市'], '24%', '三线城市 保留第一个值')
    _assert_equal(merged['三 线城市'], '25%', '三 线城市 保留原值')


def test_merge_empty():
    """空 dict / 非 dict 返回"""
    _assert_equal(_merge_dist_by_normalized_key({}), {}, '空 dict')
    _assert_equal(_merge_dist_by_normalized_key(None), None, 'None 返回 None')


# ===== _DIST_FIELDS_NORMALIZE 列表完整性 =====

def test_dist_fields_list_count():
    """18 个字段全覆盖 (14 个 fan_* + 4 个直播/视频观众画像)"""
    expected_count = 18
    _assert_equal(
        len(_DIST_FIELDS_NORMALIZE),
        expected_count,
        f'分布字段应有 {expected_count} 个',
    )


def test_dist_fields_includes_fan_age():
    """fan_age 必须包含 (老大 brief 重点)"""
    _assert_equal('fan_age' in _DIST_FIELDS_NORMALIZE, True, 'fan_age 在列表')


def test_dist_fields_includes_audience():
    """直播/视频观众画像字段必须包含"""
    for k in ('live_audience_region', 'live_audience_city_tier',
              'video_audience_region', 'video_audience_city_tier'):
        _assert_equal(k in _DIST_FIELDS_NORMALIZE, True, f'{k} 在列表')


# ===== fix/mini-test-code-repair-20260925 16:06: 7 档归一全覆盖 =====

def test_normalize_dist_key_city_tier_all_7_short_forms():
    """★ fix/mini-test-code-repair-20260925 16:06: 7 档短形式全归一到长形式 (老大拍板方案 B).

    老大 brief 钉死: "归一全部 6 档 (7 档全长形式, 数据一致)" + "六线及以下 → 六线及以下城市".
    根因: 1029d29 只归一 '新一线', 老大截图 7 档全是短形式 (新一线/三线/二线/四线/五线/一线/六线及以下).
    """
    # 7 个短形式 → 长形式
    _assert_equal(_normalize_dist_key('新一线'), '新一线城市', "'新一线' → '新一线城市'")
    _assert_equal(_normalize_dist_key('一线'), '一线城市', "'一线' → '一线城市'")
    _assert_equal(_normalize_dist_key('二线'), '二线城市', "'二线' → '二线城市'")
    _assert_equal(_normalize_dist_key('三线'), '三线城市', "'三线' → '三线城市'")
    _assert_equal(_normalize_dist_key('四线'), '四线城市', "'四线' → '四线城市'")
    _assert_equal(_normalize_dist_key('五线'), '五线城市', "'五线' → '五线城市'")
    _assert_equal(_normalize_dist_key('六线及以下'), '六线及以下城市', "'六线及以下' → '六线及以下城市'")

    # 7 个长形式 identity
    _assert_equal(_normalize_dist_key('新一线城市'), '新一线城市', "'新一线城市' identity")
    _assert_equal(_normalize_dist_key('一线城市'), '一线城市', "'一线城市' identity")
    _assert_equal(_normalize_dist_key('二线城市'), '二线城市', "'二线城市' identity")
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', "'三线城市' identity")
    _assert_equal(_normalize_dist_key('四线城市'), '四线城市', "'四线城市' identity")
    _assert_equal(_normalize_dist_key('五线城市'), '五线城市', "'五线城市' identity")
    _assert_equal(_normalize_dist_key('六线及以下城市'), '六线及以下城市', "'六线及以下城市' identity")

    # 严格等值匹配 (不用 startswith / in)
    _assert_equal(_normalize_dist_key('新一线城市abc'), '新一线城市abc', "'新一线城市abc' 不破坏扩展")
    _assert_equal(_normalize_dist_key('一线城市新区'), '一线城市新区', "'一线城市新区' 不破坏扩展")

    # 其他字段不受影响 (人群 / 地域 / 年龄)
    _assert_equal(_normalize_dist_key('都市银发'), '都市银发', "人群标签 identity")
    _assert_equal(_normalize_dist_key('广东'), '广东', "地域 identity")
    _assert_equal(_normalize_dist_key('31-40岁'), '31-40岁', "年龄 identity")