# -*- coding: utf-8 -*-
"""单元测试：阶段2.5 两个 bug 修复
Bug1 — OpenClaw 路径分析未入库：
1. reply="建档成功" + tool_results 含分析 -> knowledge_events 正确写入且实体正确
2. reply 短语 + tool_results 无分析 -> 不写入
3. reply 本身是分析结论（tool_results=None）-> 用 reply 入库（回归）
Bug2 — PUT /api/talents 浮点容错：
4. _parse_number_tolerant 区间/非法/正常值
5. PUT 更新：'100-500' -> 均值 300；'abc' -> 字段跳过保留原值；不再 500
运行: python test_openclaw_tool_analysis.py
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

tmpdir = tempfile.mkdtemp(prefix='sb_test_oc_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
srv.get_embedding_config = lambda emp_id=None: {}

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
now = 1700000000000
conn.execute("INSERT INTO talents (id, name, category, status, total_gmv, created_at, updated_at) "
             "VALUES ('t_zxg', '赵西瓜', '鞋靴', 'active', 99, ?, ?)", (now, now))
conn.commit()
conn.close()

analysis_in_tool = (
    '赵西瓜账号分析\n粉丝量128万，近30天涨粉4.2万，场均GMV约35万，转化率3.1%，客单价89元，'
    '视频完播率66%，互动率5.8%。\n合作建议：首选短视频种草加直播专场，预计首月ROI可达1:3。'
)

upsert_calls = []
orig_upsert = srv._upsert_knowledge_base
orig_find = srv._find_similar_kb_entry
srv._upsert_knowledge_base = lambda entry: upsert_calls.append(entry) or 'kb_test_oc_001'
srv._find_similar_kb_entry = lambda conn, content, threshold=0.92: None

try:
    # 场景1: OpenClaw 路径 —— reply 是操作短语，分析在 tool_results 里
    srv._maybe_auto_save_analysis('helen', '已完成建档。', tool_results=[analysis_in_tool])
    conn = sqlite3.connect(srv.DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM knowledge_events WHERE content_full LIKE '%赵西瓜账号分析%'").fetchall()
    check('1. 短reply+分析tool_results -> knowledge_events 写入',
          len(rows) == 1 and rows[0]['entity_type'] == 'talent' and rows[0]['entity_id'] == 't_zxg',
          f'rows={len(rows)}')
    check('1b. kb_entries 用拼接内容入库',
          len(upsert_calls) == 1 and '赵西瓜账号分析' in upsert_calls[0]['content'],
          f'upsert_calls={len(upsert_calls)}')
    conn.close()

    # 场景2: 短 reply + 无分析内容的 tool_results -> 不写入
    srv._maybe_auto_save_analysis('helen', '已完成。', tool_results=['工具调用成功', 'ok'])
    check('2. 无分析内容 -> 不写入', len(upsert_calls) == 1, f'upsert_calls={len(upsert_calls)}')

    # 场景3: reply 本身是结论（回归，tool_results=None）
    srv._maybe_auto_save_analysis('helen', analysis_in_tool)
    check('3. reply 本身是结论 -> 用 reply 入库（回归）',
          len(upsert_calls) == 2 and upsert_calls[1]['content'] == analysis_in_tool,
          f'upsert_calls={len(upsert_calls)}')
finally:
    srv._upsert_knowledge_base = orig_upsert
    srv._find_similar_kb_entry = orig_find

# 场景4: _parse_number_tolerant
ok4 = (
    srv._parse_number_tolerant('100-500') == (True, 300.0)
    and srv._parse_number_tolerant('100-500', is_int=True) == (True, 300)
    and srv._parse_number_tolerant('12.5') == (True, 12.5)
    and srv._parse_number_tolerant('1,000') == (True, 1000.0)
    and srv._parse_number_tolerant('abc')[0] is False
    and srv._parse_number_tolerant('50', is_int=True) == (True, 50)
    and srv._parse_number_tolerant(42) == (True, 42.0)
)
check('4. _parse_number_tolerant 区间/非法/正常值', ok4)

# 场景5: PUT /api/talents 容错 —— '100-500' 取均值，'abc' 跳过保留原值，不 500
fake_auth = types.SimpleNamespace(is_authenticated=True, user_id='admin', is_admin=True,
                                  user_info={'userId': 'admin'})
srv._authenticate = lambda *a, **k: fake_auth
srv._check_agent_role_write_scope = lambda auth, scope: None
srv._check_talent_write_permission = lambda auth, tid: None


class MockHandler:
    def __init__(self, body):
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


h = MockHandler({'average_price': '100-500', 'total_gmv': 'abc', 'name': '赵西瓜'})
srv.SoloBraveHandler._handle_put_talent(h, 't_zxg')
ok5 = (h.resp and h.resp[0] == 200
       and h.resp[1].get('average_price') == 300.0
       and h.resp[1].get('total_gmv') == 99
       and h.resp[1].get('name') == '赵西瓜')
check('5. PUT 区间取均值/非法跳过保留原值/不500', ok5, repr(h.resp)[:200] if h.resp else 'no resp')

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
