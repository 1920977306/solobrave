# -*- coding: utf-8 -*-
"""单元测试：分析结论识别放宽 + 标题提取 + 已保存分析碎片去重护栏
1. Helen 式分析（含"合作建议""首选"，无旧7个标记词）-> _is_analysis_conclusion True
2. 纯闲聊 -> False
3. 短回复 <80 字 -> False
4. 300字、含指标词+判断词但无强标记词 -> True（组合信号）
5. 300字、只有指标词没有判断词 -> False
6. 原7个标记词回归 -> True
7. _extract_analysis_title 对含"合作建议"的回复能提取合理标题
8. 集成：先 _maybe_auto_save_analysis 保存，再用相同内容调 _auto_check_knowledge -> None 且不再新增条目
运行: python test_analysis_autosave.py
"""
import importlib.util
import os
import sqlite3
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


# Helen 式分析原文：用"合作建议""首选"，不含旧7个标记词
helen_text = (
    '赵西瓜账号分析\n'
    '粉丝量128万，近30天涨粉4.2万，场均GMV约35万，转化率3.1%，客单价89元，'
    '视频完播率66%，互动率5.8%，粉丝画像以25-35岁女性为主，复购率表现稳定。\n'
    '合作建议：该达人与我们价格带匹配度高，首选短视频种草加直播专场的组合打法，'
    '预计首月ROI可达1:3以上。'
)
assert not any(m in helen_text for m in (
    '建议合作', '建议测试', '建议观望', '不建议', '分析结论', '综合建议', '分析结果')), '样例不应含旧标记词'

# 场景1
check('1. Helen式分析(合作建议/首选) -> True', srv._is_analysis_conclusion(helen_text) is True)

# 场景2: 纯闲聊（>80字，无任何标记词）
chat_text = (
    '好的老板，我这边收到啦，今天直播间氛围不错，大家积极性都很高，'
    '晚点我把链接整理好发给你，中午记得吃饭，下午开会别迟到，'
    '明天上午我让运营把排班表再过一遍，有什么要补充的随时跟我说就行。'
)
assert len(chat_text) >= srv._ANALYSIS_MIN_LENGTH
check('2. 纯闲聊 -> False', srv._is_analysis_conclusion(chat_text) is False)

# 场景3: 短回复 <80 字，即使含标记词也 False
check('3. 短回复<80字 -> False', srv._is_analysis_conclusion('建议合作') is False)

# 场景4: 300字、含指标词(完播率/转化率/GMV等)+判断词(建议)但无任何强标记词 -> True
combo_true = (
    '该达人粉丝量86万，近30天涨粉2.1万，场均GMV约22万，转化率2.4%，客单价129元。'
    '视频完播率58%，互动率4.3%，粉丝画像集中在三线以下城市30-40岁女性，复购率中等。'
    '带货类目以家居日用为主，价格带集中在50-150元区间，爆款视频占比12%，坑产稳定。'
    '近10场直播GPM均值1800，ROI均值1.9，佣金比例20%，坑位费3万。'
    '粉丝活跃时段集中在晚间20-22点，场均观看人次8.5万，播放量均值120万，点赞率3.2%。'
    '涨粉动力主要来自每周两条的固定栏目更新，坑产在同类达人中处于中上水平。'
    '从数据表现看，该达人的转化效率高于类目均值，内容调性与品牌契合，'
    '建议以短视频挂车加月度直播专场的方式推进，首月预估GMV 30万左右。'
)
assert len(combo_true) >= srv._ANALYSIS_COMBO_MIN_LENGTH, f'样例长度不足300: {len(combo_true)}'
assert not any(m in combo_true for m in srv._ANALYSIS_CONCLUSION_MARKERS), '样例不应含强标记词'
check('4. 300字组合信号(指标+判断) -> True', srv._is_analysis_conclusion(combo_true) is True)

# 场景5: 300字、只有指标词没有判断词 -> False
combo_false = (
    '达人A粉丝量52万，场均GMV 8万，转化率1.8%，客单价69元，完播率41%，互动率2.9%。'
    '达人B粉丝量73万，场均GMV 15万，转化率2.2%，客单价99元，完播率47%，互动率3.5%。'
    '达人C粉丝量41万，场均GMV 6万，转化率1.5%，客单价59元，完播率38%，互动率2.4%。'
    '三位达人带货类目均为食品生鲜，价格带30-80元，佣金比例15%-18%，坑位费1-2万。'
    '近30天涨粉分别为0.8万、1.5万、0.6万，复购率均低于类目均值，ROI均值1.2-1.6。'
    '播放量均值分别为45万、78万、32万，点赞率2.1%、2.8%、1.9%，GPM均值900-1400。'
    '三位达人的粉丝画像均以18-24岁用户为主，坑产环比持平，爆款产出频率约为每两周一条。'
)
assert len(combo_false) >= srv._ANALYSIS_COMBO_MIN_LENGTH, f'样例长度不足300: {len(combo_false)}'
assert not any(w in combo_false for w in srv._ANALYSIS_JUDGMENT_WORDS), '样例不应含判断词'
check('5. 300字仅指标词无判断词 -> False', srv._is_analysis_conclusion(combo_false) is False)

# 场景6: 原7个标记词回归
old_markers = ('建议合作', '建议测试', '建议观望', '不建议', '分析结论', '综合建议', '分析结果')
ok6 = True
for m in old_markers:
    text = '前面铺垫一些分析内容。' * 6 + f'最终{m}这个方向。'
    if len(text) < srv._ANALYSIS_MIN_LENGTH:
        text += '补充说明。' * 10
    if srv._is_analysis_conclusion(text) is not True:
        ok6 = False
        check(f'6. 旧标记词[{m}]回归', False, '未命中')
if ok6:
    check('6. 原7个标记词回归 -> 全部True', True)

# 场景7: _extract_analysis_title 对含"合作建议"的回复提取标题
title = srv._extract_analysis_title(helen_text, '分析一下赵西瓜')
check('7. 标题提取含"合作建议"', '合作建议' in title, f'title={title!r}')

# 场景8: 集成 —— 保存后用相同内容调 _auto_check_knowledge -> None 且不再新增条目
upsert_calls = {'count': 0}
orig_upsert = srv._upsert_knowledge_base
orig_find = srv._find_similar_kb_entry
orig_db = srv._db_conn


def fake_upsert(entry):
    upsert_calls['count'] += 1
    return 'kb_test_autosave_001'


srv._upsert_knowledge_base = fake_upsert
srv._find_similar_kb_entry = lambda conn, content, threshold=0.92: None
srv._db_conn = lambda: sqlite3.connect(':memory:')  # 不触碰真实库；缺表异常由内部兜底
try:
    srv._maybe_auto_save_analysis('helen', helen_text, '分析一下赵西瓜')
    check('8a. _maybe_auto_save_analysis 完成保存', upsert_calls['count'] == 1,
          f'upsert调用次数={upsert_calls["count"]}')
    r = srv._auto_check_knowledge('helen', 'mem_test_001', helen_text)
    check('8b. 相同内容调 _auto_check_knowledge -> None', r is None, f'return={r!r}')
    check('8c. 未新增碎片条目', upsert_calls['count'] == 1,
          f'upsert调用次数={upsert_calls["count"]}')
finally:
    srv._upsert_knowledge_base = orig_upsert
    srv._find_similar_kb_entry = orig_find
    srv._db_conn = orig_db

print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
