# -*- coding: utf-8 -*-
"""单元测试：规律归纳 L3 + 检索注入扩展（阶段3）
1. 建表：knowledge_patterns 存在 + 3 个索引
2. 归纳数据不足：<5 条事件返回 ok=false + current_count
3. 归纳成功：mock LLM 返回 JSON 数组 -> 写入 draft
4. LLM 非法 JSON -> ok=false + error
5. GET 列表按 status 过滤
6. PUT 状态转换：draft->confirmed 成功；confirmed->draft 400
7. DELETE 硬删除后详情 404
8. 检索注入：confirmed 规律注入（含【历史规律参考】），draft 不注入
运行: python test_knowledge_patterns.py
"""
import importlib.util
import json
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

tmpdir = tempfile.mkdtemp(prefix='sb_test_kp_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
srv.get_embedding_config = lambda emp_id=None: {}

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


# 1. 建表
srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
conn.row_factory = sqlite3.Row
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
check('1. knowledge_patterns 建表成功', 'knowledge_patterns' in tables)
idx = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='knowledge_patterns'").fetchall()]
check('1b. 3个索引就位', all(i in idx for i in ('idx_kp_category', 'idx_kp_status', 'idx_kp_entity_type')), str(idx))

# 准备数据：鞋靴类目 1 个达人 + 6 条事件
now_ms = 1700000000000
conn.execute("INSERT INTO talents (id, name, category, status, created_at, updated_at) VALUES ('t_zxg', '赵西瓜', '鞋靴', 'active', ?, ?)", (now_ms, now_ms))
for i in range(6):
    conn.execute(
        "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, event_type, title, content_full, created_at) "
        "VALUES (?, 'talent', 't_zxg', 'helen', 'analysis', ?, ?, ?)",
        (f'ke_t{i}', f'达人分析：第{i}次', f'赵西瓜第{i}次分析内容，合作建议：首选短视频种草。' * 3, now_ms + i))
conn.commit()

# 2. 归纳数据不足
ok, res = srv._induce_knowledge_patterns('美妆', {'apiKey': 'x'}, created_by='tester')
check('2. 数据不足返回 ok=false + current_count',
      ok is False and res.get('current_count') == 0 and '数据不足' in res.get('error', ''), repr(res))

# 3. 归纳成功（mock LLM）
llm_json = json.dumps([
    {'pattern_text': '鞋靴类目高GPM达人共性：纯视频种草+低坑位费打包', 'evidence_event_ids': ['ke_t0', 'ke_t1'], 'confidence': 0.85},
    {'pattern_text': '低客单鞋靴适合纯佣测试', 'evidence_event_ids': ['ke_t2'], 'confidence': 0.7},
], ensure_ascii=False)
srv._call_chat_completion = lambda *a, **k: llm_json
ok, res = srv._induce_knowledge_patterns('鞋靴', {'apiKey': 'x'}, created_by='tester')
rows = conn.execute("SELECT * FROM knowledge_patterns WHERE category = '鞋靴'").fetchall()
check('3. 归纳成功写入 draft',
      ok is True and res.get('induced') == 2 and len(rows) == 2
      and all(r['status'] == 'draft' for r in rows)
      and all(r['id'].startswith('kp_') for r in rows),
      f"ok={ok} induced={res.get('induced')} rows={len(rows)}")

# 4. LLM 非法 JSON
srv._call_chat_completion = lambda *a, **k: '我觉得这些达人都不错，没有规律可言'
ok, res = srv._induce_knowledge_patterns('鞋靴', {'apiKey': 'x'}, created_by='tester')
check('4. LLM 非法 JSON -> ok=false + error', ok is False and res.get('error') == 'LLM解析失败', repr(res))

# 准备 mock handler
fake_auth = types.SimpleNamespace(is_authenticated=True, user_id='admin', is_admin=True,
                                  user_info={'userId': 'admin'})
srv._authenticate = lambda *a, **k: fake_auth


class MockHandler:
    def __init__(self, path='', body=None):
        self.path = path
        self.headers = {}
        self.client_address = ('127.0.0.1', 0)
        self._body = body
        self.resp = None

    def _require_module_permission(self, auth, module):
        return True

    def _read_body(self):
        return self._body

    def _send_json(self, status, data):
        self.resp = (status, data)

    def _send_json_error(self, status, msg):
        self.resp = (status, {'error': msg})

    def _send_auth_error(self, error, status):
        self.resp = (status, {'error': error})


# 5. GET 列表按 status 过滤
h = MockHandler('/api/knowledge-patterns?status=draft')
srv.SoloBraveHandler._handle_get_knowledge_patterns(h)
ok5 = h.resp and h.resp[0] == 200 and h.resp[1]['total'] == 2 \
    and all(p['status'] == 'draft' for p in h.resp[1]['patterns']) \
    and 'evidence' not in h.resp[1]['patterns'][0]
check('5. GET 列表 status 过滤 + 不含 evidence', ok5, repr(h.resp)[:200])

# 6. PUT 状态转换
pid = rows[0]['id']
h = MockHandler(body={'status': 'confirmed'})
srv.SoloBraveHandler._handle_put_knowledge_pattern(h, pid)
ok6a = h.resp and h.resp[0] == 200 and h.resp[1]['status'] == 'confirmed' and 'evidence' in h.resp[1]
check('6a. draft -> confirmed 成功', ok6a, repr(h.resp)[:150])
h = MockHandler(body={'status': 'draft'})
srv.SoloBraveHandler._handle_put_knowledge_pattern(h, pid)
ok6b = h.resp and h.resp[0] == 400
check('6b. confirmed -> draft 返回 400', ok6b, repr(h.resp)[:150])

# 7. DELETE 硬删除
pid2 = rows[1]['id']
h = MockHandler()
srv.SoloBraveHandler._handle_delete_knowledge_pattern(h, pid2)
h2 = MockHandler()
srv.SoloBraveHandler._handle_get_knowledge_pattern_detail(h2, pid2)
check('7. DELETE 后详情 404', h.resp and h.resp[1].get('deleted') is True and h2.resp and h2.resp[0] == 404,
      repr(h2.resp)[:120])

# 8. 检索注入：confirmed 注入 / draft 不注入
# rows[0] 已 confirmed（鞋靴），rows[1] 已删除；补一条 draft 验证不注入
conn.execute(
    "INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, confidence, status, created_at, updated_at) "
    "VALUES ('kp_draft_x', '鞋靴', 'talent', 'DRAFT规律不应注入', 0.9, 'draft', 1, 1)")
conn.commit()
conn.close()
ctx = srv._retrieve_knowledge_context('分析一下赵西瓜的合作价值', 'helen', None)
check('8. confirmed 规律注入含【历史规律参考】',
      bool(ctx) and '【历史规律参考】' in ctx and '鞋靴类目高GPM达人共性' in ctx,
      repr(ctx[-200:]) if ctx else 'empty')
check('8b. draft 规律不注入', 'DRAFT规律不应注入' not in (ctx or ''))

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
