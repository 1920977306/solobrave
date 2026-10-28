# -*- coding: utf-8 -*-
"""POST /api/talents 数值列容错守卫（2026-10-27）。

实锤：worker 视觉提取会回传区间文本 '10-50'（平均件单价等），
_dict_to_talent_row 里的裸 float() 直接 ValueError → 500 ——
两次采集（20:47 / 22:06）在最后写入一步白干 25 分钟。
修法：行构建全部数值列走 _parse_number_tolerant（区间取均值、脏串置 0 + 警告）。
"""
import sys
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)
sys.modules['solobrave_server'] = srv


def _row(body):
    return srv._dict_to_talent_row(body)


def test_range_string_averaged_not_500():
    row = _row({'id': 't1', 'name': '豆鲨包', 'average_price': '10-50',
                'total_gmv': '5-10'})
    assert row['average_price'] == 30.0
    assert row['total_gmv'] == 7.5


def test_percent_string_parsed():
    row = _row({'id': 't2', 'name': 'x', 'fulfillment_score': '98.33%'})
    assert row['fulfillment_score'] == 98.33


def test_dirty_string_zeroed_not_500():
    row = _row({'id': 't3', 'name': 'x', 'followers': '1.3万'})
    assert row['followers'] == 0


def test_normal_numbers_untouched():
    row = _row({'id': 't4', 'name': 'x', 'commission_requirement': 20,
                'total_products': 58, 'followers': 12804})
    assert row['commission_requirement'] == 20.0
    assert row['total_products'] == 58
    assert row['followers'] == 12804


def test_no_bare_float_left_in_row_builder():
    """守卫要能失败：行构建里不许再出现「裸 float/int 直接转换 t.get 输入」。

    判据收窄为「参数树里含 t.get 调用」——int(time.time())（now）和
    bool 清理的 int(_v) 是安全的，不算违规（§16 教训：守门人收窄成摆设
    或恒红都等于没有守门人）。"""
    import ast
    src = Path(_SERVER_PATH).read_text(encoding='utf-8')
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == '_dict_to_talent_row':
            for sub in ast.walk(node):
                if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                        and sub.func.id in ('float', 'int') and sub.args):
                    has_tget = any(
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == 'get' and isinstance(n.func.value, ast.Name)
                        and n.func.value.id == 't'
                        for n in ast.walk(sub.args[0]))
                    assert not has_tget, \
                        f'行构建 {sub.lineno} 行还有裸 {sub.func.id}(t.get(...))（会重新引入 500 坑）'
            return
    raise AssertionError('_dict_to_talent_row 不存在？')
