# -*- coding: utf-8 -*-
"""单元测试：达人录入 category 取值修复
1. main_category 优先
2. 仅 category -> 用 category
3. category 空 + top_categories(list of dict) -> 取第一条 name
4. top_categories 为 JSON 字符串 -> 兼容解析
5. main_category/category/top_categories 全空 -> ''（不再回退 fan_category）
运行: python test_talent_category.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


check('1. main_category 优先',
      srv._dict_to_talent_row({'name': 'x', 'main_category': '服饰内衣', 'category': '时尚'})['category'] == '服饰内衣')
check('2. 仅 category',
      srv._dict_to_talent_row({'name': 'x', 'category': '鞋靴'})['category'] == '鞋靴')
check('3. top_categories list 兜底',
      srv._dict_to_talent_row({'name': 'x', 'top_categories': [{'name': '服饰内衣', 'ratio': 90}]})['category'] == '服饰内衣')
check('4. top_categories JSON 字符串兜底',
      srv._dict_to_talent_row({'name': 'x', 'top_categories': '[{"name": "美妆", "ratio": 80}]'})['category'] == '美妆')
r = srv._dict_to_talent_row({'name': 'x', 'fan_category': '服装23%'})
check('5. 全空不回退 fan_category', r['category'] == '', repr(r['category']))

print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
