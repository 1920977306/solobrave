"""档案名回填裁决测试 (2026-11-06)。

生产实锤：达人发「CO师傅分析一下这个账号呢 DJcoco888」⇒ 档案 name 变成整句
请求话「CO师傅分析一下这个账号呢」，飞书报告标题跟着没法看。
根因：buyin_worker 只在「name 为空或纯数字」时才用核验过的首页真名覆盖，
而 extract_fields 兜底把用户消息原样塞进了 name，回填被跳过。

锁死 `_resolve_talent_name` 的优先级：
    核验真名 >（DOM 真名 > 入参兜底）
"""
import importlib.util
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location(
        'buyin_worker_under_test', os.path.join(ROOT, 'buyin_worker.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bw = _load()
f = bw._resolve_talent_name

# hint 污染（生产原值）与核验真名
HINT_POLLUTED = 'CO师傅分析一下这个账号呢'
PROFILE_TRUE = 'CO师傅'
DY_ID_NUMERIC = '62224890070'


class TestBackfillWhenHintPollutes:
    def test_hint_passthrough_replaced_by_profile_name(self):
        """生产钉死用例：name 仍是入参原样 ⇒ 必须用核验真名覆盖。"""
        assert f(HINT_POLLUTED, HINT_POLLUTED, PROFILE_TRUE) == PROFILE_TRUE

    def test_empty_name_backfilled(self):
        assert f('', HINT_POLLUTED, PROFILE_TRUE) == PROFILE_TRUE

    def test_numeric_name_backfilled(self):
        """纯抖音号下单，名字就是号 ⇒ 回填真名。"""
        assert f(DY_ID_NUMERIC, DY_ID_NUMERIC, PROFILE_TRUE) == PROFILE_TRUE


class TestDomExtractedNameRespected:
    def test_real_dom_name_kept(self):
        """DOM 提出过真名（≠入参）⇒ 尊重 DOM，不被 profile_name 顶掉。"""
        assert f('洁洁爱户外', '洁洁爱户外 帮我分析', '别的名字') == '洁洁爱户外'

    def test_dom_name_different_from_hint_kept(self):
        assert f('萌萌超会买', '看一下这个号', '萌萌超会买') == '萌萌超会买'


class TestFallbackChain:
    def test_no_profile_no_dom_returns_hint(self):
        """没有核验真名时退回入参（旧行为不变）。"""
        assert f(HINT_POLLUTED, HINT_POLLUTED, '') == HINT_POLLUTED

    def test_nothing_at_all_empty(self):
        assert f('', '', '') == ''
