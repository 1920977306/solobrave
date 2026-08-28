# -*- coding: utf-8 -*-
"""单元测试：knowledge_events 实体档案（阶段1）
1. init_db 建表成功（knowledge_events 存在且索引就位）
2. _save_knowledge_event 写入并可查询返回正确
3. _extract_entity_from_analysis 含达人名 -> (talent, talent_id)，取最长匹配
4. _extract_entity_from_analysis 无实体名 -> ('', '')
5. API 按 entity 过滤返回正确结果（模拟 handler）
6. 完整流程：_maybe_auto_save_analysis -> knowledge_events 有记录且 entity_id 正确
运行: python test_knowledge_events.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

tmpdir = tempfile.mkdtemp(prefix='sb_test_ke_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
# 不向外部 embedding API 发请求
srv.get_embedding_config = lambda emp_id=None: {}

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


# 场景1: 建表
srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check('1. knowledge_events 建表成功', 'knowledge_events' in tables)
idx = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='knowledge_events'").fetchall()]
check('1b. 索引就位', all(i in idx for i in ('idx_ke_entity', 'idx_ke_agent', 'idx_ke_type', 'idx_ke_created')), str(idx))

# 准备实体数据
now_ms = 1700000000000
conn.execute("INSERT INTO talents (id, name, status, created_at, updated_at) VALUES ('t_xg', '赵西', 'active', ?, ?)", (now_ms, now_ms))
conn.execute("INSERT INTO talents (id, name, status, created_at, updated_at) VALUES ('t_zxg', '赵西瓜', 'active', ?, ?)", (now_ms, now_ms))
conn.execute("INSERT INTO products (id, name, status, created_at, updated_at) VALUES ('p_1', '小龙虾调料', 'active', ?, ?)", (now_ms, now_ms))
conn.commit()

# 场景3: 实体提取 —— 达人名最长匹配
et, eid = srv._extract_entity_from_analysis('赵西瓜的粉丝画像很优质，建议合作。', '分析一下赵西瓜')
check('3. 达人名最长匹配 -> (talent, t_zxg)', (et, eid) == ('talent', 't_zxg'), f'{(et, eid)}')

# 场景3b: 商品名匹配
et, eid = srv._extract_entity_from_analysis('小龙虾调料这个品转化率很高。', '')
check('3b. 商品名匹配 -> (product, p_1)', (et, eid) == ('product', 'p_1'), f'{(et, eid)}')

# 场景4: 无实体名
et, eid = srv._extract_entity_from_analysis('今天天气不错，随便聊聊。', '你好')
check('4. 无实体名 -> (\'\', \'\')', (et, eid) == ('', ''), f'{(et, eid)}')

# 场景2: _save_knowledge_event 写入并查询
reply_text = '赵西瓜账号分析：粉丝量128万，合作建议：首选短视频种草。' * 3
event_id = srv._save_knowledge_event(reply_text, 'helen', '达人分析：合作建议', '分析赵西瓜')
row = conn.execute('SELECT * FROM knowledge_events WHERE id = ?', (event_id,)).fetchone()
check('2. 写入并可查询', row is not None and row['content_full'] == reply_text
      and row['entity_type'] == 'talent' and row['entity_id'] == 't_zxg'
      and row['agent_id'] == 'helen' and row['user_query'] == '分析赵西瓜',
      '' if row else 'row is None')
check('2b. id 前缀 ke_', bool(event_id) and event_id.startswith('ke_'), str(event_id))

# 再插一条无实体 + 一条其他实体的，用于过滤测试
srv._save_knowledge_event('普通闲聊内容，无归属。' * 10, 'helen', '分析结论：普通', '')
conn.execute(
    "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, content_full, created_at) "
    "VALUES ('ke_other', 'talent', 't_other', 'mumu', 'analysis', '其他达人分析', '内容', ?)", (now_ms,))
conn.commit()
conn.close()

# 场景5: API 按 entity 过滤（模拟 handler）
fake_auth = types.SimpleNamespace(is_authenticated=True, user_id='admin', is_admin=True)
srv._authenticate = lambda *a, **k: fake_auth


class MockHandler:
    def __init__(self, path):
        self.path = path
        self.headers = {}
        self.client_address = ('127.0.0.1', 0)
        self.resp = None

    def _require_module_permission(self, auth, module):
        return True

    def _send_json(self, status, data):
        self.resp = (status, data)

    def _send_json_error(self, status, msg):
        self.resp = (status, {'error': msg})

    def _send_auth_error(self, error, status):
        self.resp = (status, {'error': error})


h = MockHandler('/api/knowledge-events?entity_type=talent&entity_id=t_zxg')
srv.SoloBraveHandler._handle_get_knowledge_events(h)
ok = h.resp and h.resp[0] == 200 and h.resp[1]['total'] == 1 \
    and h.resp[1]['events'][0]['entity_id'] == 't_zxg' \
    and 'content_full' not in h.resp[1]['events'][0]
check('5. API entity 过滤返回1条且不含 content_full', ok, repr(h.resp)[:200])

h = MockHandler('/api/knowledge-events/ke_other')
srv.SoloBraveHandler._handle_get_knowledge_event_detail(h, 'ke_other')
ok = h.resp and h.resp[0] == 200 and h.resp[1].get('content_full') == '内容'
check('5b. API 详情含 content_full', ok, repr(h.resp)[:200])

h = MockHandler('/api/knowledge-events/stats')
srv.SoloBraveHandler._handle_get_knowledge_events_stats(h)
ok = h.resp and h.resp[0] == 200 and h.resp[1]['total'] == 3 and h.resp[1]['byEntityType'].get('talent') == 2
check('5c. API stats 总数/分类计数', ok, repr(h.resp)[:200])

# 场景6: 完整流程 _maybe_auto_save_analysis -> knowledge_events 有记录且 entity_id 正确
before = sqlite3.connect(srv.DB_PATH).execute('SELECT COUNT(*) FROM knowledge_events').fetchone()[0]
orig_upsert = srv._upsert_knowledge_base
orig_find = srv._find_similar_kb_entry
srv._upsert_knowledge_base = lambda entry: 'kb_test_ke_001'
srv._find_similar_kb_entry = lambda conn, content, threshold=0.92: None
try:
    full_reply = '赵西瓜深度分析：粉丝量128万，完播率66%，转化率3.1%。合作建议：首选短视频种草加直播专场。' * 4
    srv._maybe_auto_save_analysis('helen', full_reply, '分析一下赵西瓜')
finally:
    srv._upsert_knowledge_base = orig_upsert
    srv._find_similar_kb_entry = orig_find
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute('SELECT * FROM knowledge_events ORDER BY created_at DESC').fetchall()
new_rows = [r for r in rows if r['content_full'] == full_reply]
check('6. 完整流程 knowledge_events 新增且 entity 正确',
      len(new_rows) == 1 and new_rows[0]['entity_type'] == 'talent' and new_rows[0]['entity_id'] == 't_zxg',
      f'before={before} matched={len(new_rows)}')
conn.close()

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
