"""★ fix/vision-json-repair: vision 坏 JSON 修复层 + 自由 key 归一层 单测.

覆盖 (老大 2026-09-29 拍板 A+B+C+D+E 一个 commit 全做):
  1) 4 类坏 JSON 可解析: markdown 围栏 / 裸 key 行 / 截断 / 尾逗号+单引号
  2) json_repair 缺库不报错 (降级到自研步骤)
  3) 自由 key 归一到标准字段: 城市等级分布 → fan_city_tier, 性别分布 → fan_gender
  4) minimax sections[].features[].label/percentage 展开成标准字段
  5) 白名单里没有 crowd_pref (库里没有 *_crowd_pref 列, 写错会 no such column)
  6) max_tokens 1024 → 4096
  7) _apply_schema_aliases 不改入参 / 已有值不覆盖 (先到先得)

跑法 (Mac / Linux):
    cd /path/to/solobrave
    python3 -m pytest tests/test_vision_json_repair.py -v

★ 跟其他 test_*.py 一样用 importlib.util.spec_from_file_location 加载 server.
  注意: 本文件刻意不写 unittest.main 块 (跟 test_vision_schema.py 一致), pytest 直接收集.
"""
import importlib.util
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

sys.modules['solobrave_server'] = _solobrave_server

_parse_vision_json = _solobrave_server._parse_vision_json
_repair_vision_json = _solobrave_server._repair_vision_json
_apply_schema_aliases = _solobrave_server._apply_schema_aliases
_sections_to_dist = _solobrave_server._sections_to_dist
_canonicalize_talent_row = _solobrave_server._canonicalize_talent_row
_SCHEMA_ALIAS_MAP = _solobrave_server._SCHEMA_ALIAS_MAP
_call_minimax_vision_fallback = _solobrave_server._call_minimax_vision_fallback


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def _assert_true(cond, msg):
    assert cond, f'{msg}\n  condition was False'


# ===== A+B: 4 类坏 JSON 都能救回来 =====

def test_repair_markdown_fence():
    """★ markdown ```json 围栏 + 围栏外说明文字 → 可解析"""
    text = '好的, 这是识别结果:\n```json\n{"粉丝特征": {"性别": {"男": 60, "女": 40}}}\n```\n希望有帮助。'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '围栏 JSON 解析成功')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '围栏内内容完整')


def test_repair_bare_key_line():
    """★ 裸 key 行 (只有 key 没有冒号和值) 被丢弃, 同行真数据保留"""
    text = '{\n  "粉丝特征": {\n    "性别": {"男": 60, "女": 40},\n    "城市等级分布\n  }\n}'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '裸 key 行不影响解析')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '同段真数据保留')
    _assert_true('城市等级分布' not in obj['粉丝特征'], '裸 key 行已被丢弃')


def test_repair_truncated_tail():
    """★ max_tokens 截断 (尾部缺 '}') → 补闭合后可解析"""
    text = '{"粉丝特征": {"性别": {"男": 60, "女": 40}, "年龄": {"18-23": 30'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '截断 JSON 修复后能解析')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '截断前的完整字段保留')


def test_repair_truncated_in_string():
    """★ 截断在字符串中间 (引号没闭合) → 补引号 + 补括号"""
    text = '{"粉丝特征": {"性别": {"男": 60, "女": 40}, "备注": "未完'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '字符串内截断能修复')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '完整字段保留')


def test_repair_truncated_after_complete_object():
    """★ 截断在第二个键的 value 中间 → 回退到最近结构边界, 不捏造假 key.
    老大 2026-09-29 回归: 老实现给半截 key 补 ': null', 造出 {"18-2": null} 这种假 key,
    假 key 会混进年龄分布被前端渲染出来. 现在原则是只丢不造."""
    text = '{"粉丝特征": {"性别": {"男": 60, "女": 40}}, "age": {"18-2'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '截断在第二个键时仍能解析')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '第一个完整对象保留')
    _assert_true('18-2' not in obj, '没有捏造 "18-2" 这个 key')
    _assert_true('18-2' not in json.dumps(obj, ensure_ascii=False), '结果里完全不含 "18-2"')
    _assert_equal(obj.get('age'), {}, 'age 回退为空对象而不是 {"18-2": null}')


def test_repair_truncated_half_value_rollback():
    """★ 半截 value 所在维度回退为空, 同段完整字段不受影响"""
    text = '{"粉丝特征": {"性别": {"男": 60, "女": 40}, "年龄": {"18-2'
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '半截 value 回退后仍能解析')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '完整字段保留')
    _assert_equal(obj['粉丝特征'].get('年龄'), {}, '半截 value 的维度回退为空')


def test_repair_truncated_keeps_complete_tail_value():
    """★ 结尾是完整数字值时必须保留, 只补闭合符 (不能误丢)"""
    _assert_equal(_parse_vision_json('{"男": 60'), {'男': 60}, '结尾完整数字值保留, 只补 }')
    _assert_equal(_parse_vision_json('{"a": 1, "b": 2'), {'a': 1, 'b': 2}, '多键截断, 结尾完整值保留')
    _assert_equal(_parse_vision_json('{"a": true'), {'a': True}, '结尾 true 保留')


def test_repair_truncated_key_without_colon_rolls_back():
    """★ 已闭合但缺冒号的半截 key → 回退, 不留孤儿 key"""
    _assert_equal(_parse_vision_json('{"a": 1, "b"'), {'a': 1}, '缺冒号的 key 被回退')
    _assert_equal(_parse_vision_json('{"a": 1, "b": 2, "c"'), {'a': 1, 'b': 2}, '只回退最后那对')


def test_repair_truncated_colon_without_value_rolls_back():
    """★ 冒号后缺值 → 回退, 不补 null (老大定调: 只丢不造)"""
    _assert_equal(_parse_vision_json('{"a": 1, "b":'), {'a': 1}, '冒号后缺值 → 回退, 不补 null')
    _assert_equal(_parse_vision_json('{"a": {'), {'a': {}}, '刚开的容器 → 补成空容器')


def test_repair_trailing_comma_and_single_quote():
    """★ 尾逗号 + 单引号 → 可解析"""
    text = "{'粉丝特征': {'性别': {'男': 60, '女': 40},},}"
    obj = _parse_vision_json(text)
    _assert_true(obj is not None, '单引号 + 尾逗号能解析')
    _assert_equal(obj['粉丝特征']['性别'], {'男': 60, '女': 40}, '内容正确')


def test_repair_json_repair_optional():
    """★ json_repair 是可选增强: 缺库不报错, 仍走自研步骤"""
    _assert_true(callable(_repair_vision_json), '_repair_vision_json 可调用')
    # 没装 json_repair 也不能抛 (自研步骤兜住)
    obj = _parse_vision_json('```json\n{"a": 1,}\n```')
    _assert_equal(obj, {'a': 1}, '无 json_repair 时尾逗号仍能修')


def test_repair_empty_and_none():
    """★ None / 空串 / 无 '{' 的文本 → 不炸, 返回 None"""
    _assert_equal(_parse_vision_json(None), None, 'None 输入')
    _assert_equal(_parse_vision_json(''), None, '空串输入')
    _assert_equal(_parse_vision_json('完全没有 JSON 的一段话'), None, '无 { 文本')
    _assert_equal(_repair_vision_json(None), None, '_repair_vision_json(None) 不炸')


# ===== D+E: 自由 key 归一到标准字段 =====

def test_schema_alias_map_has_no_crowd_pref():
    """★ 库里没有 *_crowd_pref 列, 白名单不能出现 crowd_pref (会拼出不存在的列)"""
    for key, suffix in _SCHEMA_ALIAS_MAP.items():
        _assert_true(suffix != 'crowd_pref',
                     f'白名单 {key} 的后缀不能是 crowd_pref (真列是 fan_crowd 等)')
        _assert_true(suffix == 'crowd' or 'crowd_pref' not in suffix,
                     f'白名单 {key} 后缀 {suffix} 含 crowd_pref')


def test_schema_alias_map_expected_entries():
    """★ 白名单覆盖老大点名的自由 key"""
    for key in ('city_level_distribution', '城市等级分布', '城市等级分布柱状图',
                '客单价水平', '客单价分布', '性别分布', '年龄分布',
                '人群分布', '八大人群占比', '类目分布', '活跃度分布', '设备分布'):
        _assert_true(key in _SCHEMA_ALIAS_MAP, f'白名单含 {key}')
    _assert_equal(_SCHEMA_ALIAS_MAP['城市等级分布'], 'city_tier', '城市等级分布 → city_tier')
    _assert_equal(_SCHEMA_ALIAS_MAP['客单价水平'], 'price_range', '客单价水平 → price_range')
    _assert_equal(_SCHEMA_ALIAS_MAP['八大人群占比'], 'crowd', '八大人群占比 → crowd')


def test_alias_top_level_key_lands_in_fan_column():
    """★ 顶层自由 key 归一 → _canonicalize_talent_row 落成 fan_gender (端到端)"""
    merged = _apply_schema_aliases({'性别分布': {'男': 60, '女': 40}})
    canonical = _canonicalize_talent_row(merged)
    _assert_true('fan_gender' in canonical, 'fan_gender 落库键存在')
    _assert_equal(json.loads(canonical['fan_gender']), {'男': 60, '女': 40}, 'fan_gender 值正确')


def test_alias_nested_in_extra_fields():
    """★ extra_fields.段内 的自由 key 也归一"""
    merged = _apply_schema_aliases({
        'extra_fields': {'粉丝特征': {'性别分布': {'男': 60, '女': 40}}},
    })
    block = merged['extra_fields']['粉丝特征']
    _assert_equal(block.get('性别'), {'男': 60, '女': 40}, '归一到标准中文键 性别')


def test_alias_city_tier_lands_in_fan_city_tier():
    """★ 城市等级分布 → 归一到 城市等级 → _FAN_SOURCE_MAP 拼出 fan_city_tier"""
    dist = {'一线城市': 8.1, '新一线城市': 17.6, '二线城市': 19.9, '三线城市': 21.0,
            '四线城市': 18.7, '五线城市': 14.1, '六线及以下城市': 0.7}
    merged = _apply_schema_aliases({'城市等级分布': dist})
    block = merged['extra_fields']['粉丝特征']
    _assert_equal(block.get('城市等级'), dist, '归一到标准中文键 城市等级')
    canonical = _canonicalize_talent_row(merged)
    _assert_true('fan_city_tier' in canonical, 'fan_city_tier 落库键存在')


def test_alias_english_key():
    """★ 英文自由 key city_level_distribution 也能归一"""
    dist = {'一线城市': 30, '新一线城市': 20, '二线城市': 20, '三线城市': 10,
            '四线城市': 8, '五线城市': 7, '六线及以下城市': 5}
    merged = _apply_schema_aliases({'city_level_distribution': dist})
    _assert_equal(merged['extra_fields']['粉丝特征'].get('城市等级'), dist, '英文 key 归一')


def test_sections_features_expand():
    """★ minimax sections[].features[].label/percentage 列表 → 标准 key 的分布 dict"""
    sections = [{
        'title': '城市等级分布',
        'features': [
            {'label': '一线城市', 'percentage': '8.1%'},
            {'label': '新一线城市', 'percentage': '17.6%'},
        ],
    }]
    out = _sections_to_dist(sections)
    _assert_equal(out.get('city_tier'),
                  {'一线城市': 8.1, '新一线城市': 17.6}, 'sections 展开成 city_tier 分布')
    merged = _apply_schema_aliases({'sections': sections})
    _assert_equal(merged['extra_fields']['粉丝特征'].get('城市等级'),
                  {'一线城市': 8.1, '新一线城市': 17.6}, 'sections 注入到 粉丝特征.城市等级')


def test_sections_unknown_title_ignored():
    """★ 白名单外的 section 标题不误伤"""
    sections = [{'title': 'GMV 走势', 'features': [{'label': '1月', 'percentage': 10}]}]
    _assert_equal(_sections_to_dist(sections), {}, '白名单外 section 忽略')


def test_alias_does_not_mutate_input():
    """★ 不改入参 (调用方 merged 还要继续用)"""
    original = {'性别分布': {'男': 60, '女': 40}}
    snapshot = json.dumps(original, ensure_ascii=False, sort_keys=True)
    _apply_schema_aliases(original)
    _assert_equal(json.dumps(original, ensure_ascii=False, sort_keys=True), snapshot, '入参未被改')


def test_alias_first_wins_no_overwrite():
    """★ 已有标准值时不覆盖 (先到先得, 跟跨图合并逻辑一致)"""
    merged = _apply_schema_aliases({
        'extra_fields': {'粉丝特征': {'性别': {'男': 61, '女': 39}}},
        '性别分布': {'男': 60, '女': 40},
    })
    block = merged['extra_fields']['粉丝特征']
    _assert_equal(block.get('性别'), {'男': 61, '女': 39}, '已有值不被自由 key 覆盖')


def test_alias_no_free_key_returns_input_unchanged():
    """★ 没有自由 key 时原样返回 (不产生多余的 extra_fields)"""
    src = {'粉丝特征': {'性别': {'男': 60}}}
    _assert_true(_apply_schema_aliases(src) is src, '无自由 key 时直接返回入参对象')
    _assert_equal(_apply_schema_aliases('not a dict'), 'not a dict', '非 dict 原样返回')


# ===== C: max_tokens 1024 → 4096 =====

def test_max_tokens_raised_to_4096():
    """★ minimax vision max_tokens 必须是 4096 (1024 会截断 OCR 输出)"""
    src = inspect.getsource(_call_minimax_vision_fallback)
    _assert_true("'max_tokens': 4096" in src, 'max_tokens = 4096')
    _assert_true("'max_tokens': 1024" not in src, '旧的 max_tokens = 1024 已移除')


# ===== 保护逻辑没被动 =====

def test_protected_columns_untouched():
    """★ 同步保护列集合没被这次改动影响 (fan_city_tier 仍受保护)"""
    protected = _solobrave_server._PROTECTED_COLUMNS
    for col in ('fan_city_tier', 'fan_group_city_tier', 'live_audience_city_tier',
                'video_audience_city_tier', 'fan_activity', 'fan_device'):
        _assert_true(col in protected, f'{col} 仍在 _PROTECTED_COLUMNS')


def test_business_vision_prompt_untouched():
    """★ BUSINESS_VISION_PROMPT 没被这次改动碰过 (只改解析层不改提示词)"""
    prompt = _solobrave_server.BUSINESS_VISION_PROMPT
    _assert_true(isinstance(prompt, str) and len(prompt) > 0, 'BUSINESS_VISION_PROMPT 仍是非空字符串')


# ===== 0929 闸门③: 图表描述对象不得污染顶层 canonical fan_* =====
# 复现 (g3_raw.txt): 顶层 fan_city_tier 是干净 7 档 dict,
# extra_fields.城市等级分布 是 {标题,说明文字,图表类型,数据:[{城市等级,占比}]} 图表对象,
# 老实现把图表对象整体注入 → fan_city_tier 落库成图表对象 (污染).

_FAN_CITY_TIER_TOP = {'新一线': 17.58, '一线': 8.05, '二线': 19.93, '三线': 20.97,
                      '四线': 18.68, '五线': 14.08, '六线及以下': 0.69}

# ★ 一致性补丁 0929: 落库期望的长名 7 档 (历史数据格式, 见李婶儿记录)
_FAN_CITY_TIER_TOP_LONG = {'新一线城市': 17.58, '一线城市': 8.05, '二线城市': 19.93,
                           '三线城市': 20.97, '四线城市': 18.68, '五线城市': 14.08,
                           '六线及以下城市': 0.69}

_CITY_CHART_OBJ = {
    '标题': '城市等级分布',
    '说明文字': '三线城市居多，占比20.97%',
    '图表类型': '柱状图',
    'Y轴范围': '0% - 25%',
    '数据': [
        {'城市等级': '新一线', '占比': '17.58%'},
        {'城市等级': '一线', '占比': '8.05%'},
        {'城市等级': '二线', '占比': '19.93%'},
        {'城市等级': '三线', '占比': '20.97%'},
        {'城市等级': '四线', '占比': '18.68%'},
        {'城市等级': '五线', '占比': '14.08%'},
        {'城市等级': '六线及以下', '占比': '0.69%'},
    ],
}


def test_top_level_canonical_fan_city_tier_not_polluted_by_chart_obj():
    """★ 闸门③回归 (要求 A): 顶层已有干净 fan_city_tier 时,
    extra_fields.城市等级分布 图表描述对象不得污染 —
    (1) 图表对象不被注入 粉丝特征.城市等级;
    (2) 落库值 (_canonicalize_talent_row 的 fan_city_tier) == 顶层 7 档 dict (原值原样)."""
    merged = {'fan_city_tier': dict(_FAN_CITY_TIER_TOP),
              'extra_fields': {'城市等级分布': _CITY_CHART_OBJ}}
    out = _apply_schema_aliases(merged)
    block = out.get('extra_fields', {}).get('粉丝特征', {})
    _assert_true('城市等级' not in block,
                 '顶层 canonical 已有时, 图表对象不被注入 粉丝特征.城市等级')
    canonical = _canonicalize_talent_row(out)
    _assert_true('fan_city_tier' in canonical, 'fan_city_tier 落库键存在')
    _assert_equal(json.loads(canonical['fan_city_tier']), _FAN_CITY_TIER_TOP_LONG,
                  '落库 fan_city_tier == 顶层 7 档 dict (不被图表对象污染, key 归一长名/数值原样)')


def test_chart_obj_rows_extracted_to_standard_dist():
    """★ 要求 B: 图表对象 数据[].城市等级/占比 能被抽成标准 7 档分布 dict
    (值是数字不是 '17.58%' 字符串, 和≈99.94), 并落到 fan_city_tier"""
    merged = _apply_schema_aliases({'extra_fields': {'城市等级分布': _CITY_CHART_OBJ}})
    block = merged['extra_fields']['粉丝特征']
    dist = block.get('城市等级')
    _assert_true(isinstance(dist, dict), '注入的是抽取后的分布 dict, 不是图表对象')
    _assert_equal(set(dist), set(_FAN_CITY_TIER_TOP), '7 档 key 齐全 (元数据键 标题/说明文字 不得混入)')
    _assert_true(all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in dist.values()),
                 "值全是数字 (不是 '17.58%' 这种字符串)")
    total = sum(dist.values())
    _assert_true(abs(total - 99.94) < 0.1, f'和≈99.94 (实测 7 档相加 {total}, 截图四舍五入误差)', )
    _assert_equal(dist, _FAN_CITY_TIER_TOP, '抽取结果与顶层 7 档数值一致')
    canonical = _canonicalize_talent_row(merged)
    _assert_true('fan_city_tier' in canonical, '抽取后能落 fan_city_tier')


def test_pure_meta_chart_obj_not_injected():
    """★ 要求 C: 纯元数据图表对象不注入、不产生假键 —
    (a) 无 数据/items/rows 的 {标题,说明文字,图表类型} 对象 → 原样返回 (无注入);
    (b) 有 数据 但行里没有占比键 → 同样不注入."""
    src = {'extra_fields': {'城市等级分布': {'标题': '城市等级分布', '说明文字': '三线城市居多',
                                           '图表类型': '柱状图', 'Y轴范围': '0% - 25%'}}}
    _assert_true(_apply_schema_aliases(src) is src, '纯元数据图表对象 → 无注入, 原样返回入参')
    src2 = {'extra_fields': {'城市等级分布': {'标题': '城市等级分布',
                                            '数据': [{'城市等级': '新一线'}, {'城市等级': '一线'}]}}}
    _assert_true(_apply_schema_aliases(src2) is src2, '数据行无占比键 → 抽不出数值, 不注入')
    canonical = _canonicalize_talent_row(src)
    _assert_true('fan_city_tier' not in canonical, '不注入 → canonical 不产生假的 fan_city_tier')


def test_top_level_fan_city_tier_keys_normalized_to_long_names():
    """★ 一致性补丁 0929: 顶层 fan_city_tier 经落库后 key 全部为长名且值不变 —
    历史长短名两种输入统一成长名 (新一线城市=17.58 等), 与历史数据 (李婶儿记录) 一致."""
    # 输入 1: 短名 (OCR 原始短形式)
    canonical = _canonicalize_talent_row({'fan_city_tier': dict(_FAN_CITY_TIER_TOP)})
    _assert_true('fan_city_tier' in canonical, '短名输入: fan_city_tier 落库键存在')
    _assert_equal(json.loads(canonical['fan_city_tier']), _FAN_CITY_TIER_TOP_LONG,
                  '短名输入: 落库 key 全部归一长名, 数值原样 (新一线城市=17.58 等)')
    # 输入 2: 长名 (已是历史格式) — 归一后保持不变
    canonical2 = _canonicalize_talent_row({'fan_city_tier': dict(_FAN_CITY_TIER_TOP_LONG)})
    _assert_equal(json.loads(canonical2['fan_city_tier']), _FAN_CITY_TIER_TOP_LONG,
                  '长名输入: 归一后仍是长名, 数值原样')
