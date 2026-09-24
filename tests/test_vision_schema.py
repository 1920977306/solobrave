"""★ fix/vision-schema-missing-fields: 视野 OCR 字段映射补漏单测.

覆盖:
  1) _OCR_TO_TALENT_FIELDS 含 avg_live_gmv (场均结算额) 映射
  2) _OCR_TO_TALENT_FIELDS 含 live_gpm / video_gpm (概览页核心 GPM 字段)
  3) _parse_gmv_value parser 工作 (¥1万-3万 → 20000)
  4) prompt 字段名 avg_live_gmv 跟 DB 列一致 (修复 avg_session_gmv typo)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/test_vision_schema.py -v

★ 跟其他 test_*.py 一样用 importlib.util.spec_from_file_location 加载 server,
  Windows Mini 端静态分析用例逻辑, Mac 端跑 pytest 验证.
"""
import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

sys.modules['solobrave_server'] = _solobrave_server

_OCR_TO_TALENT_FIELDS = _solobrave_server._OCR_TO_TALENT_FIELDS
_parse_gmv_value = _solobrave_server._parse_gmv_value
_DEDUP_HINT_FULL_FIELDS = _solobrave_server._DEDUP_HINT_FULL_FIELDS


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def _assert_true(cond, msg):
    assert cond, f'{msg}\n  condition was False'


# ===== _OCR_TO_TALENT_FIELDS 字段映射完整性 =====

def _field_dict():
    """辅助: 提取 _OCR_TO_TALENT_FIELDS 的 db_col → ocr_key 映射"""
    return {db: ocr for (db, ocr, _parser) in _OCR_TO_TALENT_FIELDS}


def test_ocr_fields_includes_avg_live_gmv():
    """★ 场均结算额 (avg_live_gmv) 已在映射 (修复前缺失)"""
    d = _field_dict()
    _assert_true('avg_live_gmv' in d, 'avg_live_gmv 在 _OCR_TO_TALENT_FIELDS')
    _assert_equal(d['avg_live_gmv'], 'avg_live_gmv', 'avg_live_gmv 映射到同名 OCR key')


def test_ocr_fields_includes_live_gpm():
    """直播 GPM 已在映射"""
    d = _field_dict()
    _assert_true('live_gpm' in d, 'live_gpm 在 _OCR_TO_TALENT_FIELDS')


def test_ocr_fields_includes_video_gpm():
    """视频 GPM 已在映射"""
    d = _field_dict()
    _assert_true('video_gpm' in d, 'video_gpm 在 _OCR_TO_TALENT_FIELDS')


def test_ocr_fields_count_at_least():
    """★ 修复后字段数 ≥ 21 (原来 20, 加 avg_live_gmv 后 21)"""
    _assert_true(len(_OCR_TO_TALENT_FIELDS) >= 21,
                 f'字段数 {len(_OCR_TO_TALENT_FIELDS)} ≥ 21')


# ===== _parse_gmv_value parser 工作 =====

def test_parse_avg_live_gmv_range():
    """★ 区间值取均值 (¥1万-3万 → 20000)"""
    parsed = _parse_gmv_value('1万-3万')
    _assert_equal(parsed, 20000.0, '区间值取均值')


def test_parse_avg_live_gmv_single():
    """单值 (¥5万 → 50000)"""
    parsed = _parse_gmv_value('5万')
    _assert_equal(parsed, 50000.0, '单值 万')


def test_parse_avg_live_gmv_w_suffix():
    """'W' / 'w' 后缀"""
    parsed = _parse_gmv_value('1.5w')
    _assert_equal(parsed, 15000.0, '小写 w')


def test_parse_avg_live_gmv_empty():
    """空值 / 异常 → 0"""
    _assert_equal(_parse_gmv_value(''), 0.0, '空字符串')
    _assert_equal(_parse_gmv_value('   '), 0.0, '纯空白')
    _assert_equal(_parse_gmv_value('null'), 0.0, '"null" 字符串')


def test_parse_avg_live_gmv_full_format():
    """★ 真实截图格式 (¥1.2万-2.5万, ¥3000)"""
    _assert_equal(_parse_gmv_value('1.2万-2.5万'), 18500.0, '¥1.2万-2.5万 均值')
    _assert_equal(_parse_gmv_value('3000'), 3000.0, '3000 整数')


# ===== prompt 字段名一致性 (治本: avg_live_gmv 跟 DB 列一致) =====

def test_prompt_avg_live_gmv_consistent():
    """★ prompt 字段名 avg_live_gmv 跟 DB 列 + _OCR_TO_TALENT_FIELDS 一致
    (修复前 prompt 写 avg_session_gmv, 跟 DB 列 avg_live_gmv 不匹配 → _deep_get 永远找不到)"""
    _assert_true('avg_live_gmv' in _DEDUP_HINT_FULL_FIELDS,
                 'prompt 含 avg_live_gmv 字段名')
    _assert_true('avg_session_gmv' not in _DEDUP_HINT_FULL_FIELDS,
                 'prompt 已移除 typo avg_session_gmv')


def test_prompt_includes_core_metrics():
    """prompt 含核心字段 (场均结算额, 直播 GPM 等)"""
    _assert_true('场均结算额' in _DEDUP_HINT_FULL_FIELDS, 'prompt 含"场均结算额"')
    _assert_true('live_gpm' in _DEDUP_HINT_FULL_FIELDS, 'prompt 含 live_gpm')
    _assert_true('video_gpm' in _DEDUP_HINT_FULL_FIELDS, 'prompt 含 video_gpm')