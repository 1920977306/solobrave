#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""商品类目分层树构建器（dq_9c4dee912a5b，老大 11-20 提，P3）。

数据源 = products.category 分段路径（"鞋靴/靴子"、"智能家居/搁板/置物架"），
一级→二级→三级折叠树，节点带计数；空类目归「未分类」桶（如实，不造类目）。
平台类目树快照（data/board_cate_tree_*.json）是类目「真名/层级」参考，
本树是「我们池子里实际有什么」的统计——两口径不混：树按池内真实分布长。

纯函数无 DB：server 处理器与测试共用这一份（对拍锁死）。
"""
from collections import OrderedDict

UNCATEGORIZED = '未分类'


def build_category_tree(categories):
    """categories: iterable[str|None] → {tree, total, uncategorized}。

    tree = [{'name': 一级, 'count': n, 'children': [{'name': 二级, ...}]}]，
    按 count 降序、同 count 按名序（输出确定性，守卫对拍）。"""
    root = OrderedDict()

    def _node(d, name):
        if name not in d:
            d[name] = {'name': name, 'count': 0, '_c': OrderedDict()}
        return d[name]

    n_uncat = 0
    n_total = 0
    for raw in categories:
        n_total += 1
        path = [p.strip() for p in str(raw or '').split('/') if p.strip()]
        if not path:
            path = [UNCATEGORIZED]
            n_uncat += 1
        level, node = root, None
        for seg in path[:3]:  # 平台类目最深三级；更深层并入第三级名
            node = _node(level, seg)
            node['count'] += 1
            level = node['_c']

    def _finalize(d):
        out = sorted(d.values(), key=lambda n: (-n['count'], n['name']))
        for n in out:
            n['children'] = _finalize(n['_c'])
            del n['_c']
        return out

    return {'tree': _finalize(root), 'total': n_total,
            'uncategorized': n_uncat}
