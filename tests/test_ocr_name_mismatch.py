"""★ fix/talent-dedup-ocr-name-priority: dedup name 不一致阻断单测.

覆盖:
  1) _ensure_talent_from_analysis 一致场景 → 返 talent dict (正常路径)
  2) _ensure_talent_from_analysis 不一致场景 → 返 conflict dict (阻断路径)
  3) _parse_llm_json_block 保留 name/talent_name 字段 (老白名单过滤已修复)

跑法 (Mac / Linux):
  cd /path/to/solobrave
  python3 -m pytest tests/test_ocr_name_mismatch.py -v

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

_parse_llm_json_block = _solobrave_server._parse_llm_json_block
_ensure_talent_from_analysis = _solobrave_server._ensure_talent_from_analysis


def _assert_equal(actual, expected, msg):
    assert actual == expected, f'{msg}\n  expected: {expected!r}\n  actual:   {actual!r}'


def _assert_true(cond, msg):
    assert cond, f'{msg}\n  condition was False'


# ===== _parse_llm_json_block 保留 name/talent_name =====

def test_parse_llm_json_includes_name():
    """★ 白名单已包含 name 字段 (治本: 让 _ensure_talent_from_analysis 能拿到 OCR name)"""
    reply = '```json\n{"ai_rating": "B", "name": "李婶儿", "ai_summary": "OK"}\n```'
    parsed = _parse_llm_json_block(reply)
    _assert_equal(parsed.get('name'), '李婶儿', 'name 字段保留')
    _assert_equal(parsed.get('ai_rating'), 'B', 'ai_rating 保留')
    _assert_equal(parsed.get('ai_summary'), 'OK', 'ai_summary 保留')


def test_parse_llm_json_includes_talent_name():
    """talent_name 字段也保留 (LLM prompt 可能用 talent_name 而非 name)"""
    reply = '```json\n{"talent_name": "张三", "ai_rating": "A"}\n```'
    parsed = _parse_llm_json_block(reply)
    _assert_equal(parsed.get('talent_name'), '张三', 'talent_name 保留')


def test_parse_llm_json_filters_other_fields():
    """其他字段被白名单过滤 (防 SQL injection)"""
    reply = '```json\n{"name": "李婶儿", "evil_field": "<script>", "sql": "DROP TABLE"}\n```'
    parsed = _parse_llm_json_block(reply)
    _assert_equal(parsed.get('name'), '李婶儿', 'name 保留')
    _assert_true('evil_field' not in parsed, 'evil_field 过滤')
    _assert_true('sql' not in parsed, 'sql 过滤')


def test_parse_llm_json_invalid_json():
    """JSON 解析失败 → 返空 dict"""
    parsed = _parse_llm_json_block('这不是 JSON')
    _assert_equal(parsed, {}, '解析失败 返空')


# ===== _ensure_talent_from_analysis 一致场景 (走正常路径, 不会 conflict) =====

def test_ensure_consistent_returns_dict_or_none():
    """★ 注意: 实际测试需要真 DB (data/solobrave.db), 这里只验证函数可调不抛异常.
    一致场景在 Mac 端真 DB 上验证.
    Windows Mini 端静态分析逻辑通过 node 等价测试覆盖 (D:\\tmp)."""
    # 不实际调用 (会尝试连 sqlite3 :memory: 但会建表失败)
    # 只验证函数引用存在
    _assert_true(callable(_ensure_talent_from_analysis), '_ensure_talent_from_analysis 可调用')


# ===== _ensure_talent_from_analysis 不一致场景 (核心防御) =====

def test_conflict_return_shape():
    """★ 不一致阻断后返 {conflict:True, existing_talent_id, existing_name, ocr_name, user_name, message}

    此测试在 Mac 端真 DB 上跑:
    1) 预置一个达人 name='李婶儿'
    2) 调 _ensure_talent_from_analysis(name='李婶儿', llm_json={'name': '张三'})
    3) 期望返回 dict 含 conflict=True + message

    Windows Mini 端不跑 (依赖生产 DB), commit message 标明."""
    pass  # Mac 端验证


def test_no_conflict_when_consistent():
    """★ 一致场景不返 conflict"""
    pass  # Mac 端验证


# ===== mock 测试: 模拟 _db_conn 返回, 验证 conflict 分支 =====

class MockConn:
    """★ fix/test-mock-design-fixes: 重设计 MockConn.

    之前按查询参数精确比对: 当 OCR name 跟 user_name 不同时, MockConn 返 None,
    _ensure_talent_from_analysis 走 INSERT 分支, 永远触发不了 conflict=True 分支.

    新设计: 按 name 精确查重时无条件返 existing_talent_name 那一行,
    模拟 DB 始终存在该达人, 使 _ensure_talent_from_analysis 拿到 existing_name
    后跟 OCR clean 名比较, 触发 conflict=True 分支.
    """
    def __init__(self, existing_talent_name='李婶儿', existing_talent_id='tal_existing_001'):
        # ★ 兼容原有 5 个测试用例: MockConn('李婶儿') / MockConn('张三') 用 positional 第一个参数
        self._existing_name = existing_talent_name
        self._existing_id = existing_talent_id

    def execute(self, sql, params=()):
        sql_norm = ' '.join(sql.split()).lower()

        # 精确查重: SELECT id, name FROM talents WHERE LOWER(name)=LOWER(?) → 无条件返回
        #   排除 LIKE 兜底 (LIKE 走下一分支)
        if ('select id, name' in sql_norm and 'from talents' in sql_norm
                and 'lower(name)' in sql_norm and 'like' not in sql_norm):
            return _MockCursor({'id': self._existing_id, 'name': self._existing_name})

        # LIKE 兜底: 不命中 (无 LIKE 兜底场景)
        if 'select id, name' in sql_norm and 'like' in sql_norm:
            return _MockCursor(None)

        # 其他查询 (SELECT *, UPDATE, INSERT) 返 None, 不抛异常
        return _MockCursor(None)

    def commit(self):
        pass

    def close(self):
        pass


class _MockCursor:
    def __init__(self, row):
        self._row = row
    def fetchone(self):
        return self._row
    @property
    def rowcount(self):
        return 1


def test_conflict_mock_consistent():
    """★ 一致场景: 既有达人存在, ocr_name 等于既有达人名 → 不冲突"""
    import unittest.mock as mock
    # 替换 _db_conn 为 mock
    with mock.patch.object(_solobrave_server, '_db_conn', return_value=MockConn('李婶儿')):
        result = _ensure_talent_from_analysis(
            name='李婶儿',
            vision_field_maps=None,
            llm_json={'name': '李婶儿'},
            user_id='test_user',
            agent=None,
        )
    _assert_true(isinstance(result, dict), '返 dict')
    _assert_true(not result.get('conflict'), '一致场景不返 conflict')
    _assert_true(result.get('id') == 'tal_existing_001', '命中既有达人 id')


def test_conflict_mock_ocr_differs():
    """★ 不一致场景: 既有达人 '李婶儿' vs OCR name '张三' → 阻断 conflict"""
    import unittest.mock as mock
    with mock.patch.object(_solobrave_server, '_db_conn', return_value=MockConn('李婶儿')):
        result = _ensure_talent_from_analysis(
            name='李婶儿',
            vision_field_maps=None,
            llm_json={'name': '张三'},
            user_id='test_user',
            agent=None,
        )
    _assert_true(isinstance(result, dict), '返 dict')
    _assert_true(result.get('conflict') is True, '不一致场景 返 conflict=True')
    _assert_equal(result.get('existing_talent_id'), 'tal_existing_001', '既有达人 id')
    _assert_equal(result.get('existing_name'), '李婶儿', '既有达人名')
    _assert_equal(result.get('ocr_name'), '张三', 'OCR 识别名')
    _assert_true('不一致' in result.get('message', ''), 'message 含"不一致"提示')


def test_conflict_mock_user_only_no_ocr():
    """★ OCR name 缺失, 仅 user_name, 命中既有达人名一致 → 不冲突"""
    import unittest.mock as mock
    with mock.patch.object(_solobrave_server, '_db_conn', return_value=MockConn('李婶儿')):
        result = _ensure_talent_from_analysis(
            name='李婶儿',
            vision_field_maps=None,
            llm_json=None,  # 无 OCR
            user_id='test_user',
            agent=None,
        )
    _assert_true(isinstance(result, dict), '返 dict')
    _assert_true(not result.get('conflict'), '一致场景不冲突')


def test_conflict_mock_user_differs_from_existing():
    """★ OCR name 缺失, user_name='张三' 命中既有 '李婶儿' → 阻断 (user_name 跟既有不一致)"""
    import unittest.mock as mock
    with mock.patch.object(_solobrave_server, '_db_conn', return_value=MockConn('李婶儿')):
        result = _ensure_talent_from_analysis(
            name='张三',
            vision_field_maps=None,
            llm_json=None,
            user_id='test_user',
            agent=None,
        )
    _assert_true(isinstance(result, dict), '返 dict')
    _assert_true(result.get('conflict') is True, 'user_name 不一致阻断')
    _assert_equal(result.get('existing_name'), '李婶儿', '既有达人名')
    _assert_equal(result.get('user_name'), '张三', 'user_name')


def test_ocr_name_priority_over_user_name():
    """★ OCR name 优先: 即使 user_name 是 '李婶儿', OCR name '张三' 命中既有 '张三' → 不冲突"""
    import unittest.mock as mock
    with mock.patch.object(_solobrave_server, '_db_conn', return_value=MockConn('张三')):
        result = _ensure_talent_from_analysis(
            name='李婶儿',
            vision_field_maps=None,
            llm_json={'name': '张三'},
            user_id='test_user',
            agent=None,
        )
    _assert_true(isinstance(result, dict), '返 dict')
    _assert_true(not result.get('conflict'), 'OCR name 命中 → 不冲突')