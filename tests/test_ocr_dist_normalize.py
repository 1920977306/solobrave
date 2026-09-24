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
    """'31-40岁' → '31-40' (去岁字)"""
    _assert_equal(_normalize_dist_key('31-40岁'), '31-40', '带岁字年龄归一')
    _assert_equal(_normalize_dist_key('31-40'), '31-40', '不带岁字保持')


def test_normalize_age_fullwidth():
    """全角数字 → 半角 ('３１-４０' → '31-40')"""
    _assert_equal(_normalize_dist_key('３１-４０'), '31-40', '全角数字归一')
    _assert_equal(_normalize_dist_key('１８-２３'), '18-23', '全角数字归一 18-23')


def test_normalize_city():
    """城市去全角空格 + trim"""
    _assert_equal(_normalize_dist_key('三线城市'), '三线城市', '城市保持')
    _assert_equal(_normalize_dist_key('三 线城市'), '三线城市', '城市去半角空格')
    _assert_equal(_normalize_dist_key('  一线城市  '), '一线城市', '城市 trim')


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
    """同义异名 key 同值合并"""
    dist = {'31-40岁': 30.1, '31-40': 30.1}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 1, '合并后只有 1 个 key')
    _assert_equal('31-40' in merged, True, '归一后 key 是 31-40')


def test_merge_take_max():
    """同义异名 key 不同值, 取较大值"""
    dist = {'31-40岁': 30.1, '31-40': 40.5}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(merged['31-40'], 40.5, '取较大值 40.5')


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
    """非数字 value 保留第一个 (不覆盖)"""
    dist = {'三线城市': '24%', '三 线城市': '25%'}
    merged = _merge_dist_by_normalized_key(dist)
    _assert_equal(len(merged), 1, '合并后只有 1 个 key')
    _assert_equal(merged['三线城市'], '24%', '保留第一个值')


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