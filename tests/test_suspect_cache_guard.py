"""疑似坏采集护栏（fix/suspect-cache-guard，2026-11-03）。

背景（hlb725 洁洁爱户外实锤）：buyin_worker 采集时平台返回**陈旧缓存渲染**
（选行粉丝≈0，followers 靠性别分布回填成 99），整组数据是旧的（99粉/168款/GMV 50-100万）。
写库时结构化字段被 merge「只补空」保护住（行内真值 12804/151/63 没被冲掉），
但 __refresh_fields 里的 ai_analysis **无条件覆盖** ⇒ 报告文本基于坏数据重写，
与行内真值自相矛盾（报告写「粉丝仅99人」，同页显示 12,804）。

修法两层（都只做一件事：把派生文本字段从本次 __refresh_fields 剔除）：
  ① 显式标记：采集端 followers 走了回填兜底 → PUT body 带 __suspect_cache=true；
  ② 数值兜底：没打标但新 followers 与行内存值差 ≥10 倍（存量 ≥1000，0 除外）
    也按疑似坏采集处理 —— 防别的写入方没打标。

红线：
  - 护栏只动「派生文本刷新」，核心事实字段的「只补空」保护不受影响；
  - 没打标、数值也正常时，刷新行为与之前**逐位一致**；
  - 控制字段 __suspect_cache 不许混进数据。
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(ROOT, "solobrave-server.py")

_spec = importlib.util.spec_from_file_location("solobrave_server_suspect", SERVER)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

guard = srv._guard_suspect_refresh
merge = srv._merge_talent_only_empty
DERIVED = srv._TALENT_REFRESHABLE_DERIVED
REFRESH = ['notes', 'ai_summary', 'ai_analysis', 'ai_reason', 'ai_rating', 'ai_tags']


class TestExplicitSuspectFlag:
    def test_suspect_strips_all_derived_from_refresh(self):
        body = {'notes': '坏摘要', 'ai_analysis': '坏报告',
                '__suspect_cache': True, '__refresh_fields': list(REFRESH)}
        existing = {'followers': 12804, 'notes': '好摘要', 'ai_analysis': '好报告'}
        out, suspect = guard(body, existing)
        assert suspect is True
        assert out['__refresh_fields'] == [], '疑似坏采集时不该刷新任何派生文本'
        merged = merge(existing, out)
        assert merged['notes'] == '好摘要'
        assert merged['ai_analysis'] == '好报告', '坏采集的报告绝不能覆盖好报告'

    def test_suspect_still_allows_filling_empty_fields(self):
        """护栏只拦「刷新」，老值为空的字段照常补（只补空语义不变）。"""
        body = {'ai_summary': '新摘要', '__suspect_cache': True, '__refresh_fields': list(REFRESH)}
        existing = {'ai_summary': ''}
        out, suspect = guard(body, existing)
        assert suspect is True
        merged = merge(existing, out)
        assert merged['ai_summary'] == '新摘要', '老值为空仍应补上'

    def test_control_flag_is_popped_not_stored(self):
        body = {'__suspect_cache': True, 'notes': 'x', '__refresh_fields': ['notes']}
        out, _ = guard(body, {})
        assert '__suspect_cache' not in out, '控制字段不能混进数据'
        merged = merge({}, out)
        assert '__suspect_cache' not in merged

    def test_no_refresh_list_noop(self):
        body = {'__suspect_cache': True, 'followers': 99}
        out, suspect = guard(body, {'followers': 12804})
        assert suspect is True
        assert out == {'followers': 99}, '没有刷新声明时护栏无事可做'


class TestNumericFallbackGuard:
    """没打标，但 followers 量级突变（≥10x，存量 ≥1000）——别的写入方的坏数据也要拦。"""

    def test_ten_x_drop_triggers_guard(self):
        body = {'followers': 99, 'ai_analysis': '坏报告', '__refresh_fields': list(REFRESH)}
        existing = {'followers': 12804, 'ai_analysis': '好报告'}
        out, suspect = guard(body, existing)
        assert suspect is True
        merged = merge(existing, out)
        assert merged['ai_analysis'] == '好报告'
        assert merged['followers'] == 12804, '核心事实只补空保护不变'

    def test_ten_x_rise_triggers_guard(self):
        body = {'followers': 200000, '__refresh_fields': ['notes']}
        out, suspect = guard(body, {'followers': 12804})
        assert suspect is True
        assert out['__refresh_fields'] == []

    def test_normal_delta_does_not_trigger(self):
        body = {'followers': 13000, '__refresh_fields': list(REFRESH)}
        out, suspect = guard(body, {'followers': 12804})
        assert suspect is False
        assert out['__refresh_fields'] == REFRESH, '正常波动不许误伤刷新'

    def test_small_existing_baseline_exempt(self):
        """存量 <1000 的账号波动天然大（新号涨粉快），不启用数值兜底。"""
        body = {'followers': 99, '__refresh_fields': list(REFRESH)}
        out, suspect = guard(body, {'followers': 500})
        assert suspect is False

    def test_zero_new_value_exempt(self):
        """新值为 0/缺失 = 没采到，由「空值不覆盖」保护，不算量级突变。"""
        body = {'followers': 0, '__refresh_fields': list(REFRESH)}
        out, suspect = guard(body, {'followers': 12804})
        assert suspect is False


class TestNormalPathUnchanged:
    """没打标 + 数值正常 ⇒ 行为与修之前逐位一致。"""

    def test_refresh_still_works(self):
        existing = {'followers': 12804, 'notes': '旧摘要', 'ai_analysis': '旧报告'}
        body = {'notes': '新摘要', 'ai_analysis': '新报告', '__refresh_fields': list(REFRESH)}
        out, suspect = guard(body, existing)
        assert suspect is False
        merged = merge(existing, out)
        assert merged['notes'] == '新摘要'
        assert merged['ai_analysis'] == '新报告'

    def test_suspect_flag_false_means_normal(self):
        body = {'__suspect_cache': False, 'notes': '新', '__refresh_fields': ['notes']}
        _, suspect = guard(body, {'notes': '旧'})
        assert suspect is False


class TestWhitelistInvariants:
    def test_guard_only_strips_whitelisted_derived(self):
        """护栏剔除范围 == _TALENT_REFRESHABLE_DERIVED，不多不少。"""
        extra = ['followers', 'custom_xxx']
        body = {'__suspect_cache': True, '__refresh_fields': list(REFRESH) + extra}
        out, _ = guard(body, {})
        # 白名单内的全被剔除；白名单外（含核心事实字段）原样保留由 merge 双保险去拦
        for k in DERIVED:
            assert k not in out['__refresh_fields']
        for k in extra:
            assert k in out['__refresh_fields']
