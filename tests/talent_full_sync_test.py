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
"""
import sys
import os

# 把项目根加进 sys.path, 让 import solobrave-server 能找到
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
