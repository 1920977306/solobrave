"""★ fix/talent-name-clean-blacklist: 通用词黑名单单测.

覆盖 _clean_talent_name 黑名单过滤 + 长度校验:
  1. 通用词黑名单 (数据/分析/档案 等)
  2. 黑名单子串匹配 (核心数据 / 直播带货数据)
  3. 长度 < 2 → 返空
  4. 正常达人名保留

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/talent_name_blacklist_test.py -v

★ 跟其他 test_*.py 一样用 importlib.util.spec_from_file_location 加载 server,
  Windows Mini 端静态分析用例逻辑, Mac 端跑 pytest 验证.
"""
import importlib.util
import sys
from pathlib import Path

# 跟其他 test_*.py 一致: 先把项目根加进 sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
_solobrave_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_solobrave_server)

sys.modules['solobrave_server'] = _solobrave_server

_clean_talent_name = _solobrave_server._clean_talent_name
_TALENT_NAME_NOUN_BLACKLIST = _solobrave_server._TALENT_NAME_NOUN_BLACKLIST


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


# ===== 黑名单子串匹配 (主要场景) =====

def test_clean_blacklist_data():
    """'数据' 黑名单 → 返空"""
    _assert_equal(_clean_talent_name('数据'), '', '单字 黑名单')
    _assert_equal(_clean_talent_name('  数据  '), '', '带空格 黑名单')


def test_clean_blacklist_substring_data():
    """'核心数据' / '直播带货数据' 黑名单子串命中 → 返空"""
    _assert_equal(_clean_talent_name('核心数据'), '', '核心数据 子串命中')
    _assert_equal(_clean_talent_name('直播带货数据'), '', '直播带货数据 子串命中')


def test_clean_blacklist_archive():
    """'档案' / '截图' 黑名单 → 返空"""
    _assert_equal(_clean_talent_name('档案'), '', '档案 单字')
    _assert_equal(_clean_talent_name('客户档案'), '', '客户档案 子串命中')
    _assert_equal(_clean_talent_name('截图'), '', '截图 单字')
    _assert_equal(_clean_talent_name('首页截图'), '', '首页截图 子串命中')


def test_clean_blacklist_analysis():
    """'分析' 黑名单 → 返空"""
    _assert_equal(_clean_talent_name('分析'), '', '分析 单字')
    _assert_equal(_clean_talent_name('数据分析'), '', '数据分析 子串命中')


def test_clean_blacklist_extension():
    """brief 扩展词 (直播/带货/合作 等) 也命中"""
    _assert_equal(_clean_talent_name('直播'), '', '直播')
    _assert_equal(_clean_talent_name('带货'), '', '带货')
    _assert_equal(_clean_talent_name('合作'), '', '合作')
    _assert_equal(_clean_talent_name('粉丝'), '', '粉丝')
    _assert_equal(_clean_talent_name('商品'), '', '商品')


# ===== 长度 < 2 =====

def test_clean_blacklist_short():
    """长度 < 2 → 返空"""
    _assert_equal(_clean_talent_name('A'), '', '单字符 长度 1')
    _assert_equal(_clean_talent_name('1'), '', '单数字 长度 1')
    _assert_equal(_clean_talent_name('x'), '', '单字母 长度 1')


def test_clean_blacklist_short_after_strip():
    """剥空白后长度 < 2 → 返空"""
    _assert_equal(_clean_talent_name('   A   '), '', '单字符 带空白')


# ===== 正常达人名保留 =====

def test_clean_keep_normal():
    """正常达人名 保留"""
    _assert_equal(_clean_talent_name('李婶儿'), '李婶儿', '李婶儿')
    _assert_equal(_clean_talent_name('发财周周'), '发财周周', '发财周周')
    _assert_equal(_clean_talent_name('小楚当妈'), '小楚当妈', '小楚当妈')


def test_clean_keep_with_prefix_number():
    """带前导序号 → 剥序号后保留"""
    _assert_equal(_clean_talent_name('1. 李婶儿'), '李婶儿', '1. 李婶儿')
    _assert_equal(_clean_talent_name('2、张三'), '张三', '2、张三')
    _assert_equal(_clean_talent_name('3）李四'), '李四', '3）李四')


def test_clean_keep_numeric_only():
    """纯数字开头无分隔符 保留 (例 '77爱吃' '11')"""
    _assert_equal(_clean_talent_name('77爱吃'), '77爱吃', '77爱吃 保留')
    _assert_equal(_clean_talent_name('11'), '11', '11 保留')


# ===== 空输入 =====

def test_clean_empty():
    """空输入 / None / 空白 → 返空"""
    _assert_equal(_clean_talent_name(''), '', '空字符串')
    _assert_equal(_clean_talent_name('   '), '', '空白字符串')
    _assert_equal(_clean_talent_name(None), '', 'None')
    _assert_equal(_clean_talent_name('\u3000\u3000'), '', '全角空白')


# ===== 边界: 黑名单刚好占满名字 =====

def test_clean_blacklist_full_match():
    """黑名单词刚好等于整个名字 → 返空"""
    _assert_equal(_clean_talent_name('信息'), '', '信息 全词')
    _assert_equal(_clean_talent_name('情况'), '', '情况 全词')
    _assert_equal(_clean_talent_name('结果'), '', '结果 全词')
    _assert_equal(_clean_talent_name('详情'), '', '详情 全词')
    _assert_equal(_clean_talent_name('概览'), '', '概览 全词')
    _assert_equal(_clean_talent_name('核心'), '', '核心 全词')


# ===== 黑名单集合完整性 =====

def test_blacklist_includes_brief_keywords():
    """brief 要求的 11 个关键词全在"""
    brief_keys = ['数据', '分析', '档案', '图片', '截图', '信息', '情况',
                  '结果', '详情', '概览', '核心']
    for k in brief_keys:
        _assert_equal(k in _TALENT_NAME_NOUN_BLACKLIST, True,
                      f'brief 关键词 {k!r} 在黑名单')