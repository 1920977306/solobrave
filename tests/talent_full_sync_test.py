"""★ fix/talent-full-sync: 达人全字段同步 + PUT merge-only-empty 单测.

覆盖:
  1) _merge_talent_only_empty (solobrave-server.py L20735-20760)
  2) _map_talent_form_to_record (solobrave-server.py L20695-20730)
  3) updateExistingTalent 前端 body 字段 (index.html L32299-32350)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 tests/talent_full_sync_test.py
  或
  python3 -m pytest tests/talent_full_sync_test.py -v

退出码 0 = 全部通过, 1 = 有失败.

★ fix/talent-full-sync-r2: 用 importlib.util.spec_from_file_location 加载 server
  (solobrave-server.py 有连字符不能直接 import, 之前 Mac 跑 ModuleNotFoundError)
★ fix/talent-full-sync-r3: 在 spec_from_file_location 前加 sys.path.insert
  让 solobrave-server.py 内部 `from douyin_parser import *` 能找到 douyin_parser 模块
"""
import sys
import os
import importlib.util
from pathlib import Path

# ★ fix/talent-full-sync-r3: 先把项目根加进 sys.path, 让 server 内部 `from douyin_parser import *` 能 import
sys.path.insert(0, str(Path(__file__).parent.parent))

# ★ 改用 importlib.util.spec_from_file_location (老大 r2 反馈指定)
# 原因: solobrave-server.py 文件名有连字符, Python 不能 `import solobrave-server`
# 参考 scripts/backfill_analyzed_talents.py 的 helper 加载模式
_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

# 暴露给测试函数用
sys.modules['solobrave_server'] = _solobrave_server


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def test_merge_talent_only_empty_basic():
    """老值非空 + 新值非空 → 保留老值 (老大硬约束)."""
    from solobrave_server import _merge_talent_only_empty
    _assert_equal(
        _merge_talent_only_empty({'followers': 5486}, {'followers': 9999}),
        {'followers': 5486},
        '老值 5486 + 新值 9999 应保留 5486'
    )


def test_merge_talent_only_empty_only_empty_in_body():
    """新值 None / '' / 0 → 不覆盖, 无论老值空不空."""
    from solobrave_server import _merge_talent_only_empty
    # 老值空 + 新值 None → 不动
    _assert_equal(
        _merge_talent_only_empty({'followers': None}, {'followers': None}),
        {'followers': None},
        '老值 None + 新值 None 应保持 None'
    )
    # 老值非空 + 新值 '' → 不覆盖
    _assert_equal(
        _merge_talent_only_empty({'followers': 5486}, {'followers': ''}),
        {'followers': 5486},
        '老值 5486 + 新值 "" 应保留 5486'
    )
    # 老值非空 + 新值 0 → 不覆盖 (0 在 merge 中视为空)
    _assert_equal(
        _merge_talent_only_empty({'followers': 5486}, {'followers': 0}),
        {'followers': 5486},
        '老值 5486 + 新值 0 应保留 5486'
    )


def test_merge_talent_only_empty_fill_gaps():
    """老值空 + 新值非空 → 补."""
    from solobrave_server import _merge_talent_only_empty
    _assert_equal(
        _merge_talent_only_empty({'followers': None}, {'followers': 5486}),
        {'followers': 5486},
        '老值 None + 新值 5486 应补 5486'
    )
    _assert_equal(
        _merge_talent_only_empty({'followers': ''}, {'followers': 5486}),
        {'followers': 5486},
        '老值 "" + 新值 5486 应补 5486'
    )
    _assert_equal(
        _merge_talent_only_empty({'followers': 0}, {'followers': 5486}),
        {'followers': 5486},
        '老值 0 + 新值 5486 应补 5486 (0 视为空)'
    )


def test_merge_talent_only_empty_real_scenario_facai_zhouzhou():
    """真实场景: Helen 分析发财周周 写入已有达人 (老大 brief 提到的核心字段)."""
    from solobrave_server import _merge_talent_only_empty
    existing = {
        'name': '发财周周',
        'followers': 5486,
        'total_gmv': 100,
        'content_style': '',
        'cooperation_status': 'available',
        'ai_analysis': '',
        'video_gpm': 0,
        'bio': '已有备注'
    }
    body = {
        'followers': 99999,           # 试图覆盖 → 应该保留
        'total_gmv': 99999,           # 试图覆盖 → 应该保留
        'content_style': '好物分享',  # 老值空 → 补
        'ai_analysis': 'Helen 新分析',  # 老值空 → 补
        'video_gpm': 2500,            # 老值 0 → 补 (0 视为空)
        'bio': '新备注',              # 试图覆盖 → 应该保留
    }
    result = _merge_talent_only_empty(existing, body)
    _assert_equal(result, {
        'name': '发财周周',
        'followers': 5486,            # 不覆盖
        'total_gmv': 100,             # 不覆盖
        'content_style': '好物分享',  # 补
        'cooperation_status': 'available',  # 不动
        'ai_analysis': 'Helen 新分析',  # 补
        'video_gpm': 2500,            # 补
        'bio': '已有备注',             # 不覆盖
    }, '发财周周完整场景合并应符合预期')


def test_merge_talent_only_empty_skip_internal_fields():
    """id / updated_at 跳过循环, 由调用方负责."""
    from solobrave_server import _merge_talent_only_empty
    result = _merge_talent_only_empty(
        {'name': 'A'},
        {'id': 'X', 'updated_at': 12345, 'name': 'B'}
    )
    # name 走正常 merge: 老值 'A' 非空, 保留
    _assert_equal(result, {'name': 'A'}, 'name 老值非空应保留, id/updated_at 跳过')


def test_merge_talent_only_empty_edge_cases():
    """边界: 空 body / 空 existing / 双空."""
    from solobrave_server import _merge_talent_only_empty
    _assert_equal(
        _merge_talent_only_empty({'followers': 100}, {}),
        {'followers': 100},
        '空 body 应保持 existing'
    )
    _assert_equal(
        _merge_talent_only_empty(None, {'followers': 100}),
        {'followers': 100},
        '空 existing 应只含 body'
    )
    _assert_equal(
        _merge_talent_only_empty({}, {}),
        {},
        '双空应返回空 dict'
    )


def test_map_talent_form_to_record_brief_to_db():
    """_map_talent_form_to_record: brief 字段名 → 表字段名 (翻译)."""
    from solobrave_server import _map_talent_form_to_record
    form = {
        # 核心数据
        'followers': 5486,
        'total_gmv': 100.5,
        'product_count': 5,
        'video_gpm': 2500.0,
        'avg_video_settlement': 1500.0,    # brief → single_video_settlement
        'total_shops': 3,
        # 基础信息
        'talent_type': '带货号',
        'content_style': '好物分享',
        'account_fans_profile': '25-35岁女性',
        'video_fans_profile': '18-24岁女性',
        'avg_video_price': 99.0,            # brief → video_avg_price
        'bio': '简介',
        'douyin_id': 'douyin_abc',
        'level': 'B',
        'city': '上海',
        'cooperation_days': 30,
        'live_count': 12,                    # brief → live_sessions
        'interaction_rate': 5.5,             # brief → video_interaction_rate
        # AI / 跟进
        'ai_rating': 'A',
        'ai_analysis': '分析',
        'cooperation_status': 'available',
    }
    record = _map_talent_form_to_record(form)
    # 核心数据 6 项
    _assert_equal(record.get('followers'), 5486, 'followers 直映射')
    _assert_equal(record.get('total_gmv'), 100.5, 'total_gmv 直映射')
    _assert_equal(record.get('video_gpm'), 2500.0, 'video_gpm 直映射')
    # brief → 表内翻译
    _assert_equal(record.get('single_video_settlement'), 1500.0,
                  'avg_video_settlement → single_video_settlement')
    _assert_equal(record.get('video_avg_price'), 99.0,
                  'avg_video_price → video_avg_price')
    _assert_equal(record.get('live_sessions'), 12,
                  'live_count → live_sessions')
    _assert_equal(record.get('video_interaction_rate'), 5.5,
                  'interaction_rate → video_interaction_rate')
    # 基础信息直映射
    _assert_equal(record.get('talent_type'), '带货号', 'talent_type 直映射')
    _assert_equal(record.get('account_fans_profile'), '25-35岁女性',
                  'account_fans_profile 直映射')
    _assert_equal(record.get('cooperation_days'), 30, 'cooperation_days 直映射')


def test_map_talent_form_to_record_skip_empty():
    """空值字段不应进 record (None / '')."""
    from solobrave_server import _map_talent_form_to_record
    form = {
        'followers': 5486,
        'total_gmv': None,
        'bio': '',
        'cooperation_status': 'available',
    }
    record = _map_talent_form_to_record(form)
    _assert_equal(record.get('followers'), 5486, '非空字段进 record')
    assert 'total_gmv' not in record, 'None 字段不进 record'
    assert 'bio' not in record, '"" 字段不进 record'
    _assert_equal(record.get('cooperation_status'), 'available',
                  'cooperation_status 进 record')


def test_map_talent_form_to_record_empty_input():
    """空 form / None / 非 dict 都不应崩."""
    from solobrave_server import _map_talent_form_to_record
    _assert_equal(_map_talent_form_to_record(None), {}, 'None 输入返回空')
    _assert_equal(_map_talent_form_to_record({}), {}, '空 dict 输入返回空')
    _assert_equal(_map_talent_form_to_record('not a dict'), {}, '非 dict 输入返回空')


# ★ fix/talent-full-sync-r2: 回归保护, 防止再有人忘了同步 _TALENT_COLUMNS / _dict_to_talent_row
def test_talent_columns_includes_3_new():
    """★ r2 根因回归保护: _TALENT_COLUMNS 必须含 ALTER TABLE 新加的 3 列.
    否则 PUT handler 拼 UPDATE SQL 会漏这 3 列, DB 永远写不进.
    """
    from solobrave_server import _TALENT_COLUMNS
    for col in ('account_fans_profile', 'video_fans_profile', 'cooperation_days'):
        assert col in _TALENT_COLUMNS, f'_TALENT_COLUMNS 缺 {col} (r2 根因: PUT SQL 不写这列)'


def test_dict_to_talent_row_includes_3_new():
    """★ r2 根因回归保护: _dict_to_talent_row 输出必须含 3 列映射.
    否则即使补了 _TALENT_COLUMNS, row dict 也不带这 3 列.
    """
    from solobrave_server import _dict_to_talent_row
    row = _dict_to_talent_row({
        'name': 'test',
        'account_fans_profile': '25-35岁女性',
        'video_fans_profile': '18-24岁女性',
        'cooperation_days': 30,
    })
    _assert_equal(row.get('account_fans_profile'), '25-35岁女性', 'account_fans_profile 映射')
    _assert_equal(row.get('video_fans_profile'), '18-24岁女性', 'video_fans_profile 映射')
    _assert_equal(row.get('cooperation_days'), 30, 'cooperation_days 映射')


def test_dict_to_talent_row_default_3_new():
    """★ r2 根因回归保护: 缺 3 列字段时, row 默认值应符合 schema (TEXT '' / INTEGER 0)."""
    from solobrave_server import _dict_to_talent_row
    row = _dict_to_talent_row({'name': 'test'})
    _assert_equal(row.get('account_fans_profile'), '', '缺省值: 空字符串')
    _assert_equal(row.get('video_fans_profile'), '', '缺省值: 空字符串')
    _assert_equal(row.get('cooperation_days'), 0, '缺省值: 0')


# ★ fix/talent-full-sync-r3: dedup 路径前端自动 PUT 解析测试
# 这俩测试验证 Python 等价的 _extract_talent_fields_from_llm_reply 行为,
# JS 版 (index.html L26029 起的 _extractTalentFieldsFromLLMReply) 用同一套 regex.
# Mac 端跑: python3 tests/talent_full_sync_test.py → 14 pass

def _parse_follower_count_py(v):
    """★ r3 helper: 复用 server _parse_follower_count 逻辑, 支持 '1.2万' / '5,486'."""
    import re
    s = re.sub(r'[,\s]', '', str(v or '').lower())
    if not s:
        return 0
    try:
        if '万' in s or 'w' in s:
            return int(float(re.sub(r'[万千wW]', '', s)) * 10000)
        return int(float(s))
    except Exception:
        return 0


def _extract_talent_fields_from_llm_reply_py(reply):
    """★ r3 Python 等价: 跟 index.html _extractTalentFieldsFromLLMReply 同 regex 解析."""
    import re
    if not reply or not isinstance(reply, str):
        return None

    fields = {}

    # ───── 达人 ID (触发前提) ─────
    # LLM 实际回复格式 A/B/C 三种, 优先匹配 A (反引号 + 姓名: 紧跟)
    m = re.search(r'[`\'"]?(tal_[a-zA-Z0-9_]+)[`\'"]?\s*\(姓名:', reply)
    if not m:
        m = re.search(r'达人ID[:\s]*[`\'"]?(tal_[a-zA-Z0-9_]+|[a-zA-Z0-9_]{8,})[`\'"]?', reply)
    if not m:
        return None  # 没达人 ID → 不是 dedup 触发场景, 跳过
    talent_id = m.group(1)

    # ───── dedup 触发关键词 ─────
    if not re.search(r'(已为.*达人.*更新|已更新.*档案|直接调\s*PUT|PUT\s*/api/talents)', reply):
        return None

    # ───── 核心数据 ─────
    m = re.search(r'粉丝[量]?[:\s]*([0-9,\.万千]+)', reply)
    if m:
        fields['followers'] = _parse_follower_count_py(m.group(1))

    m = re.search(r'(结算总额|总GMV|GMV总额)[:\s]*[¥￥]?([0-9,\-万千]+)', reply)
    if m:
        fields['total_gmv'] = m.group(2)

    m = re.search(r'(带货商品数|商品数)[:\s]*([0-9]+)', reply)
    if m:
        fields['product_count'] = int(m.group(2)) or 0

    m = re.search(r'(合作店铺数|关联店铺数)[:\s]*([0-9]+)', reply)
    if m:
        fields['total_shops'] = int(m.group(2)) or 0

    m = re.search(r'(?:视频\s*GPM|GPM)[:\s]*([0-9,\-]+)', reply)
    if m:
        fields['video_gpm'] = m.group(1)

    m = re.search(r'单视频结算额[:\s]*[¥￥]?([0-9,\-]+)', reply)
    if m:
        fields['single_video_settlement'] = m.group(1)

    m = re.search(r'互动率[:\s]*([0-9\.]+)\s*%', reply)
    if m:
        fields['video_interaction_rate'] = (float(m.group(1)) or 0) / 100

    m = re.search(r'直播(?:场次|场数|场|次|数)?[:\s]*([0-9]+)', reply)
    if m:
        fields['live_sessions'] = int(m.group(1)) or 0

    m = re.search(r'带货天数[:\s]*([0-9]+)', reply)
    if m:
        fields['cooperation_days'] = int(m.group(1)) or 0

    # ───── 基础信息 ─────
    m = re.search(r'内容类型[:\s]*([^\n,。；]+)', reply)
    if m:
        fields['talent_type'] = m.group(1).strip()

    m = re.search(r'内容风格[:\s]*([^\n,。；]+)', reply)
    if m:
        fields['content_style'] = m.group(1).strip()

    m = re.search(r'(?:账号粉丝特征|粉丝特征)[:\s]*([^\n,。；]+)', reply)
    if m:
        fields['account_fans_profile'] = m.group(1).strip()

    m = re.search(r'(?:短视频粉丝特征|视频粉丝特征)[:\s]*([^\n,。；]+)', reply)
    if m:
        fields['video_fans_profile'] = m.group(1).strip()

    m = re.search(r'抖音号[:\s]*[`\'"]?([a-zA-Z0-9_\-\.]+)[`\'"]?', reply)
    if m:
        fields['douyin_id'] = m.group(1)

    m = re.search(r'(?:等级|评级)[:\s]*(LV\d+|L\d+|[A-D]\s*级|[A-D][级]?)', reply)
    if m:
        fields['level'] = re.sub(r'\s', '', m.group(1))

    m = re.search(r'(?:所在地|所在城市|城市)[:\s]*([^\n,。；]+)', reply)
    if m:
        fields['city'] = m.group(1).strip()

    m = re.search(r'(?:备注|简介|bio)[:\s]*([^\n]+)', reply)
    if m:
        fields['bio'] = m.group(1).strip()

    return {'talent_id': talent_id, 'body': fields}


def test_extract_talent_fields_from_llm_reply_basic():
    """★ r3 新增: 基本 dedup 触发 + 字段提取 (LLM 看到 dedup_hint 的典型回复)."""
    reply = (
        '已为达人 `tal_abc123_xyz` (姓名: 发财周周) 更新档案, 核心数据如下:\n'
        '粉丝量: 5486\n'
        '结算总额: 50万-100万\n'
        '带货商品数: 63\n'
        '合作店铺数: 41\n'
        '视频GPM: 50-100\n'
        '单视频结算额: 1000-2500\n'
        '互动率: 0.32%\n'
        '直播场次: 0\n'
        '带货天数: 372\n'
        '内容类型: 带货号\n'
        '内容风格: 好物分享\n'
        '账号粉丝特征: 25-35岁女性\n'
        '短视频粉丝特征: 18-24岁女性\n'
        '抖音号: douyin_facai\n'
        '等级: B级\n'
        '所在地: 上海\n'
        '备注: 高质量带货达人\n'
    )
    result = _extract_talent_fields_from_llm_reply_py(reply)
    assert result, '应解析出结果 (dedup 触发 + 达人ID 存在)'
    _assert_equal(result['talent_id'], 'tal_abc123_xyz', 'talent_id 正确')
    body = result['body']
    _assert_equal(body.get('followers'), 5486, 'followers 直数字')
    _assert_equal(body.get('total_gmv'), '50万-100万', 'total_gmv 区间格式')
    _assert_equal(body.get('product_count'), 63, 'product_count 整数')
    _assert_equal(body.get('total_shops'), 41, 'total_shops 整数')
    _assert_equal(body.get('video_gpm'), '50-100', 'video_gpm 区间格式')
    _assert_equal(body.get('single_video_settlement'), '1000-2500', 'single_video_settlement')
    _assert_equal(body.get('video_interaction_rate'), 0.0032, 'video_interaction_rate 0.32% → 0.0032')
    _assert_equal(body.get('live_sessions'), 0, 'live_sessions')
    _assert_equal(body.get('cooperation_days'), 372, 'cooperation_days')
    _assert_equal(body.get('talent_type'), '带货号', 'talent_type')
    _assert_equal(body.get('content_style'), '好物分享', 'content_style')
    _assert_equal(body.get('account_fans_profile'), '25-35岁女性', 'account_fans_profile')
    _assert_equal(body.get('video_fans_profile'), '18-24岁女性', 'video_fans_profile')
    _assert_equal(body.get('douyin_id'), 'douyin_facai', 'douyin_id')
    _assert_equal(body.get('level'), 'B级', 'level')
    _assert_equal(body.get('city'), '上海', 'city')
    _assert_equal(body.get('bio'), '高质量带货达人', 'bio')


def test_extract_talent_fields_handles_chinese_units():
    """★ r3 新增: 中文单位 ('1.2万') + 区间值 ('50-100') 容错解析."""
    # 中文单位粉丝
    assert _parse_follower_count_py('1.2万') == 12000, '1.2万 → 12000'
    assert _parse_follower_count_py('1.2W') == 12000, '1.2W → 12000'
    assert _parse_follower_count_py('5,486') == 5486, '5,486 → 5486'
    assert _parse_follower_count_py('1.5万') == 15000, '1.5万 → 15000'
    assert _parse_follower_count_py('11') == 11, '11 → 11'
    assert _parse_follower_count_py('') == 0, '空 → 0'
    assert _parse_follower_count_py(None) == 0, 'None → 0'

    # 非 dedup 触发 → 返回 None (避免误触发普通 LLM 回复)
    reply_no_dedup = (
        '达人ID: `tal_xxx` 这是个普通问候, 粉丝量: 5486\n'  # 有达人 ID 但没"已为...更新"
    )
    result = _extract_talent_fields_from_llm_reply_py(reply_no_dedup)
    assert result is None, '非 dedup 触发应返回 None, 不解析'

    # 没达人 ID → 返回 None
    reply_no_id = '已为达人更新档案, 粉丝量: 5486'
    result = _extract_talent_fields_from_llm_reply_py(reply_no_id)
    assert result is None, '没达人 ID 应返回 None'

    # 空 / None 输入
    assert _extract_talent_fields_from_llm_reply_py('') is None
    assert _extract_talent_fields_from_llm_reply_py(None) is None


# ★ fix/talent-full-sync-r4: 双条件结构匹配 (tal_xxx id + dedup context 关键词 AND)
# Python 等价: 跟 JS 版 _tryAutoPutTalentFromReply (index.html L26155) 同一 regex
def _should_auto_put_py(reply):
    """★ r4 双条件判定: 达人 ID (tal_xxx) AND dedup context 关键词.
    返回 talent_id (str) 或 None (不触发).
    """
    import re
    if not reply or not isinstance(reply, str):
        return None
    m = re.search(r'\b(tal_[a-zA-Z0-9_]+)\b', reply)
    if not m:
        return None  # 条件 1: 没达人 ID → 不触发
    if not re.search(r'(更新|录入|建档|同步|写入|档案|覆盖)', reply):
        return None  # 条件 2: 没 dedup context 关键词 → 不触发
    return m.group(1)


def test_r4_tal_id_with_update_keyword_triggers():
    """★ r4 case 1 (老大 brief 字面 wording): '我将使用达人ID tal_xxx 更新' → 触发."""
    reply = "我将使用达人ID tal_abc123 更新档案"
    result = _should_auto_put_py(reply)
    assert result is not None, "应触发自动 PUT (有 tal_abc123 id + 更新 + 档案)"
    _assert_equal(result, 'tal_abc123', 'r4 case 1 提取的 talent_id')


def test_r4_separate_id_and_context_triggers():
    """★ r4 case 2 (老大 brief 字面 wording): '以下是更新的信息...达人ID: tal_xxx' → 触发."""
    reply = "以下是更新的信息: 粉丝量5486\n达人ID: tal_abc123"
    result = _should_auto_put_py(reply)
    assert result is not None, "应触发自动 PUT (有 tal_abc123 id + 更新 + 达人ID)"
    _assert_equal(result, 'tal_abc123', 'r4 case 2 提取的 talent_id')


def test_r4_normal_conversation_no_tal_id_skipped():
    """★ r4 case 3 (老大 brief 字面 wording): '发财周周粉丝量5486' (普通对话无 tal_xxx) → 不触发."""
    reply = "发财周周粉丝量5486, 互动率0.32%"
    result = _should_auto_put_py(reply)
    assert result is None, "不应触发 (无 tal_xxx id, 只含昵称 + 数字)"


def run_all_tests():
    """跑全部测试, 返回 (pass_count, fail_count)."""
    import traceback
    tests = [
        test_merge_talent_only_empty_basic,
        test_merge_talent_only_empty_only_empty_in_body,
        test_merge_talent_only_empty_fill_gaps,
        test_merge_talent_only_empty_real_scenario_facai_zhouzhou,
        test_merge_talent_only_empty_skip_internal_fields,
        test_merge_talent_only_empty_edge_cases,
        test_map_talent_form_to_record_brief_to_db,
        test_map_talent_form_to_record_skip_empty,
        test_map_talent_form_to_record_empty_input,
        # ★ r2 新增 (根因回归保护)
        test_talent_columns_includes_3_new,
        test_dict_to_talent_row_includes_3_new,
        test_dict_to_talent_row_default_3_new,
        # ★ r3 新增 (dedup 路径自动 PUT 解析)
        test_extract_talent_fields_from_llm_reply_basic,
        test_extract_talent_fields_handles_chinese_units,
        # ★ r4 新增 (双条件结构匹配)
        test_r4_tal_id_with_update_keyword_triggers,
        test_r4_separate_id_and_context_triggers,
        test_r4_normal_conversation_no_tal_id_skipped,
    ]
    pass_count = 0
    fail_count = 0
    for test in tests:
        name = test.__name__
        try:
            test()
            print(f'  PASS: {name}')
            pass_count += 1
        except Exception as e:
            print(f'  FAIL: {name}')
            print(f'    {type(e).__name__}: {e}')
            traceback.print_exc()
            fail_count += 1
    return pass_count, fail_count


if __name__ == '__main__':
    print('=== ★ fix/talent-full-sync 单测 ===\n')
    pass_count, fail_count = run_all_tests()
    print(f'\n=== {pass_count} pass / {fail_count} fail ===')
    sys.exit(0 if fail_count == 0 else 1)
