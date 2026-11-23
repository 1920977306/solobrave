# -*- coding: utf-8 -*-
"""商品类目分层树守卫（dq_9c4dee912a5b，老大 11-20 提）。

矩阵：
1. 三级折叠树构建：层级/计数/排序确定性
2. 空类目归「未分类」如实桶，不造类目
3. 超三级路径并入第三级；脏输入（None/空串/斜杠）不炸
4. server 源码锚：路由在 startswith('/api/products/') 之前 + 处理器接单一真源
"""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'product_category_tree',
    os.path.join(ROOT, 'daren_cockpit/tools/product_category_tree.py'))
pct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pct)


def _flatten(tree):
    return {n['name']: n for n in tree}


def test_three_level_tree_with_counts():
    r = pct.build_category_tree(
        ['鞋靴/靴子', '鞋靴/靴子', '鞋靴/凉鞋', '智能家居/小夜灯',
         '智能家居/搁板/置物架', None, ''])
    assert r['total'] == 7
    assert r['uncategorized'] == 2
    top = _flatten(r['tree'])
    assert top['鞋靴']['count'] == 3
    assert top['智能家居']['count'] == 2
    lvl2 = _flatten(top['鞋靴']['children'])
    assert lvl2['靴子']['count'] == 2
    assert lvl2['凉鞋']['count'] == 1


def test_sorting_deterministic():
    """count 降序 + 同名按名序——输出确定性（守卫对拍前提）。"""
    r = pct.build_category_tree(['b/1', 'a/1', 'b/2', 'a/2', 'c'])
    names = [n['name'] for n in r['tree']]
    assert names[0] in ('a', 'b') and names[:2] != names[2:]  # a/b 并列前
    again = pct.build_category_tree(['c', 'a/2', 'b/1', 'a/1', 'b/2'])
    assert [n['name'] for n in again['tree']] == names


def test_uncategorized_bucket_honest():
    r = pct.build_category_tree([None, '', '   ', '/ /'])
    assert r['tree'][0]['name'] == pct.UNCATEGORIZED
    assert r['uncategorized'] == 4
    # 不造类目：未分类桶不带子层
    assert r['tree'][0]['children'] == []


def test_deep_path_folds_into_third_level():
    r = pct.build_category_tree(['a/b/c/d/e'])
    node = _flatten(r['tree'])['a']
    node = _flatten(node['children'])['b']
    node = _flatten(node['children'])['c']
    assert node['count'] == 1
    assert node['children'] == []


def test_dirty_input_safe():
    r = pct.build_category_tree(['//', 'a//b', 123, 'a/b'])
    assert r['tree']  # 不炸
    top = _flatten(r['tree'])
    assert top['a']['count'] == 2


def test_server_route_wired():
    src = open(os.path.join(ROOT, 'solobrave-server.py'), encoding='utf-8').read()
    assert "'/api/products/category-tree'" in src
    # 必须排在 startswith('/api/products/') 通用前缀之前（recommend 同款教训）
    seg = src.split("path == '/api/products/category-tree'", 1)[0]
    assert "path.startswith('/api/products/')" not in seg.split('def do_GET', 1)[-1], \
        'category-tree 路由被 startswith 通用前缀遮蔽'
    assert 'product_category_tree.build_category_tree' in src, \
        '处理器必须接纯函数单一真源'
