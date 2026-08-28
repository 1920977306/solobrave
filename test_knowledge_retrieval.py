# -*- coding: utf-8 -*-
"""单元测试：知识事件检索注入（阶段2）
1. 提到达人A名字 -> 返回达人A的最近历史分析（多条）+ 含【相关分析参考】标记
2. 提到类目关键词但无具体达人名 -> 返回同类目其他达人的分析
3. 无匹配 -> 返回空字符串
4. 注入格式正确（头部/尾部标记）
5. content_full 超 200 字正确截断；总长度 <= 1500
6. search API：embedding 不可用时 LIKE 降级匹配
运行: python test_knowledge_retrieval.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import types

sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

tmpdir = tempfile.mkdtemp(prefix='sb_test_kr_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
# embedding 不可用 -> 走实体+类目匹配，语义检索跳过（优雅降级）
srv.get_embedding_config = lambda emp_id=None: {}

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
now = int(time.time() * 1000)
DAY = 86400 * 1000

# 达人：赵西瓜(鞋靴) / 小晴姑姑(鞋靴) / 老王(美食)
conn.execute("INSERT INTO talents (id, name, category, status, created_at, updated_at) VALUES ('t_zxg', '赵西瓜', '鞋靴', 'active', ?, ?)", (now, now))
conn.execute("INSERT INTO talents (id, name, category, status, created_at, updated_at) VALUES ('t_xqg', '小晴姑姑', '鞋靴', 'active', ?, ?)", (now, now))
conn.execute("INSERT INTO talents (id, name, category, status, created_at, updated_at) VALUES ('t_lw', '老王', '美食', 'active', ?, ?)", (now, now))

# 事件：赵西瓜 2 条（3天前/10天前）、小晴姑姑 1 条（5天前）、无实体 1 条
events = [
    ('ke_a1', 'talent', 't_zxg', '达人分析：合作建议首选人字拖', '赵西瓜分析：定位纯视频种草。合作建议：首选COOLCHAP人字拖129元。', now - 3 * DAY),
    ('ke_a2', 'talent', 't_zxg', '达人分析：赵西瓜复投评估', '赵西瓜复投：GPM 高于类目均值，建议加大投放。', now - 10 * DAY),
    ('ke_b1', 'talent', 't_xqg', '达人分析：小晴姑姑低客单', '小晴姑姑：客单价30-80元，合作建议：低客单母婴用品纯佣测试。', now - 5 * DAY),
    ('ke_c1', '', '', '分析结论：闲聊', '这是一条无实体的普通内容。', now - 1 * DAY),
]
for eid, etype, eent, title, content, ts in events:
    conn.execute(
        "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, content_full, created_at) "
        "VALUES (?, ?, ?, 'helen', 'analysis', ?, ?, ?)", (eid, etype, eent, title, content, ts))
conn.commit()
conn.close()

# 场景1: 提到达人名 -> 同实体历史（2条都在）
ctx = srv._retrieve_knowledge_context('分析一下赵西瓜最近的带货表现', 'helen', None)
check('1. 提到达人A -> 含其2条历史分析',
      bool(ctx) and '赵西瓜' in ctx and '首选COOLCHAP人字拖' in ctx and '复投' in ctx,
      repr(ctx[:150]) if ctx else 'empty')

# 场景2: 类目关键词无达人名 -> 同类目分析
ctx = srv._retrieve_knowledge_context('帮我看看鞋靴类目达人合作', 'helen', None)
check('2. 类目关键词 -> 含同类目达人分析',
      bool(ctx) and '小晴姑姑' in ctx and '同类目' in ctx,
      repr(ctx[:150]) if ctx else 'empty')

# 场景3: 无匹配 -> 空字符串
ctx = srv._retrieve_knowledge_context('今天天气怎么样', 'helen', None)
check('3. 无关键词 -> 空字符串', ctx == '', repr(ctx[:80]) if ctx else '')
ctx = srv._retrieve_knowledge_context('分析一下市场行情', 'helen', None)  # 有关键词但无实体/类目
check('3b. 有关键词无实体 -> 空字符串', ctx == '', repr(ctx[:80]) if ctx else '')

# 场景4: 注入格式
ctx = srv._retrieve_knowledge_context('分析赵西瓜', 'helen', None)
check('4. 格式含头尾标记',
      ctx.startswith('【相关分析参考 - 仅供分析参考，不要直接复述】')
      and ctx.endswith('以上为历史分析记录，请结合当前达人实际情况判断。'),
      repr(ctx[:60]) if ctx else 'empty')

# 场景5: 截断 —— 超长 content_full 截取 200 字
long_content = '赵西瓜数据明细：' + '数' * 500
conn = sqlite3.connect(srv.DB_PATH)
conn.execute(
    "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, content_full, created_at) "
    "VALUES ('ke_long', 'talent', 't_zxg', 'helen', 'analysis', '达人分析：长文', ?, ?)",
    (long_content, now - 1 * DAY))
conn.commit()
conn.close()
ctx = srv._retrieve_knowledge_context('分析赵西瓜', 'helen', None)
check('5. content_full 截断到200字', ('数' * 192) in ctx and ('数' * 193) not in ctx)
check('5b. 总长度 <= 1500', len(ctx) <= srv._KE_CONTEXT_MAX_LEN, f'len={len(ctx)}')

# 场景6: search API 降级 LIKE（embedding 不可用）
res = srv._search_knowledge_events('人字拖', entity_type='talent', limit=5)
check('6. search LIKE 降级匹配', len(res) == 1 and res[0]['id'] == 'ke_a1'
      and 'content_full' not in res[0], repr(res)[:150])
res = srv._search_knowledge_events('不存在的词xyz', limit=5)
check('6b. search 无匹配返回空', res == [], repr(res)[:100])

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
