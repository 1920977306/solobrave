# -*- coding: utf-8 -*-
"""单元测试：Deal 实体（阶段4A）
1. 建表：deals 存在 + idx_deals_talent/idx_deals_status/idx_deals_product 三个索引
2. POST 创建：写入成功，id 前缀 deal_，created_by 正确
3. GET 列表：talent_id 过滤 + 子账号隔离（只能看到自己子库达人的 deal）
4. GET 详情：完整记录；不存在 404
5. PUT 状态转换全链路：pending→negotiating→sample_sent→approved→live→completed；
   completed→pending 400
6. PUT 切 completed 时 actual_gmv 和 result_note 都空 -> 400
7. DELETE 硬删除后详情 404
8. predicted_match 提取：_extract_predicted_match 返回 dict 且 product_name/product_id 正确；
   _save_knowledge_event 写入 conclusions 含 predicted_match；无推荐文本返回 None
运行: python test_deals.py
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

tmpdir = tempfile.mkdtemp(prefix='sb_test_deals_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
srv.get_embedding_config = lambda emp_id=None: {}
srv._load_agents = lambda: []

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
check('1. deals 建表成功', 'deals' in tables)
idx = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='deals'").fetchall()]
check('1b. 3个索引就位', all(i in idx for i in ('idx_deals_talent', 'idx_deals_status', 'idx_deals_product')), str(idx))

# 准备数据：admin 子库达人 + user_b 子库达人
now_s = 1700000000
conn.execute("INSERT INTO talents (id, name, category, status, created_by, created_at, updated_at) "
             "VALUES ('t_admin1', '达人甲', '鞋靴', 'active', 'admin', ?, ?)", (now_s, now_s))
conn.execute("INSERT INTO talents (id, name, category, status, created_by, created_at, updated_at) "
             "VALUES ('t_userb1', '达人乙', '美妆', 'active', 'user_b', ?, ?)", (now_s, now_s))
conn.execute("INSERT INTO products (id, name, status) VALUES ('p_jzb', '便携榨汁杯', 'active')")
conn.commit()

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


# 2. POST 创建
h = MockHandler(body={'talent_id': 't_admin1', 'product_id': 'p_jzb', 'product_name': '便携榨汁杯',
                      'deal_type': '直播带货', 'commission_rate': 0.2})
srv.SoloBraveHandler._handle_post_deal(h)
ok2 = h.resp and h.resp[0] == 200 and h.resp[1]['id'].startswith('deal_') \
    and h.resp[1]['created_by'] == 'admin' and h.resp[1]['status'] == 'pending'
check('2. POST 创建成功（id 前缀 deal_ / created_by=admin）', ok2, repr(h.resp)[:200])
deal_id = h.resp[1]['id']

# user_b 也建一条 deal（用于子账号隔离验证）
userb_auth = types.SimpleNamespace(is_authenticated=True, user_id='user_b', is_admin=False,
                                   user_info={'userId': 'user_b'})
srv._authenticate = lambda *a, **k: userb_auth
h = MockHandler(body={'talent_id': 't_userb1', 'product_name': '面膜'})
srv.SoloBraveHandler._handle_post_deal(h)
ok2b = h.resp and h.resp[0] == 200 and h.resp[1]['created_by'] == 'user_b'
check('2b. 子账号 POST 创建成功（created_by=user_b）', ok2b, repr(h.resp)[:200])
userb_deal_id = h.resp[1]['id']

# 3. GET 列表：talent_id 过滤 + 子账号隔离
h = MockHandler('/api/deals?talent_id=t_userb1')
srv.SoloBraveHandler._handle_get_deals(h)
ok3a = h.resp and h.resp[0] == 200 and h.resp[1]['total'] == 1 \
    and all(d['talent_id'] == 't_userb1' for d in h.resp[1]['deals'])
check('3a. GET 列表 talent_id 过滤', ok3a, repr(h.resp)[:200])
h = MockHandler('/api/deals')
srv.SoloBraveHandler._handle_get_deals(h)
ok3b = h.resp and h.resp[0] == 200 and h.resp[1]['total'] == 1 \
    and h.resp[1]['deals'][0]['id'] == userb_deal_id
check('3b. 子账号 GET 只能看到自己子库达人的 deal', ok3b, repr(h.resp)[:200])
# 管理员看全部
srv._authenticate = lambda *a, **k: fake_auth
h = MockHandler('/api/deals')
srv.SoloBraveHandler._handle_get_deals(h)
check('3c. 管理员 GET 看全部（total=2）', h.resp and h.resp[1]['total'] == 2, repr(h.resp)[:200])

# 4. GET 详情
h = MockHandler()
srv.SoloBraveHandler._handle_get_deal_detail(h, deal_id)
ok4a = h.resp and h.resp[0] == 200 and h.resp[1]['id'] == deal_id \
    and h.resp[1]['commission_rate'] == 0.2
check('4a. GET 详情返回完整记录', ok4a, repr(h.resp)[:200])
h = MockHandler()
srv.SoloBraveHandler._handle_get_deal_detail(h, 'deal_nonexist')
check('4b. GET 不存在返回 404', h.resp and h.resp[0] == 404, repr(h.resp)[:120])

# 5. PUT 状态转换全链路
flow_ok = True
for nxt in ('negotiating', 'sample_sent', 'approved', 'live'):
    h = MockHandler(body={'status': nxt})
    srv.SoloBraveHandler._handle_put_deal(h, deal_id)
    if not (h.resp and h.resp[0] == 200 and h.resp[1]['status'] == nxt):
        flow_ok = False
        break
check('5a. pending->negotiating->sample_sent->approved->live 逐步成功', flow_ok, repr(h.resp)[:200])
h = MockHandler(body={'status': 'completed', 'actual_gmv': 50000, 'actual_roi': 3.2, 'actual_units': 500, 'win_loss_category': 'other'})
srv.SoloBraveHandler._handle_put_deal(h, deal_id)
ok5b = h.resp and h.resp[0] == 200 and h.resp[1]['status'] == 'completed' \
    and h.resp[1]['actual_gmv'] == 50000
check('5b. live->completed（带 actual_gmv）成功', ok5b, repr(h.resp)[:200])
h = MockHandler(body={'status': 'pending'})
srv.SoloBraveHandler._handle_put_deal(h, deal_id)
check('5c. completed->pending 返回 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])

# 6. PUT 切 completed 时 actual_gmv 和 result_note 都空 -> 400
h = MockHandler(body={'talent_id': 't_admin1', 'product_name': '空气炸锅'})
srv.SoloBraveHandler._handle_post_deal(h)
deal2_id = h.resp[1]['id']
for nxt in ('negotiating', 'sample_sent', 'approved', 'live'):
    hh = MockHandler(body={'status': nxt})
    srv.SoloBraveHandler._handle_put_deal(hh, deal2_id)
h = MockHandler(body={'status': 'completed'})
srv.SoloBraveHandler._handle_put_deal(h, deal2_id)
check('6. 切 completed 无 actual_gmv/result_note 返回 400', h.resp and h.resp[0] == 400, repr(h.resp)[:150])

# 7. DELETE 硬删除后详情 404
h = MockHandler()
srv.SoloBraveHandler._handle_delete_deal(h, deal2_id)
h2 = MockHandler()
srv.SoloBraveHandler._handle_get_deal_detail(h2, deal2_id)
check('7. DELETE 后详情 404', h.resp and h.resp[1].get('deleted') is True and h2.resp and h2.resp[0] == 404,
      repr(h2.resp)[:120])

# 8. predicted_match 提取
analysis_text = '综合分析：该达人与商品高度契合，非常适合带便携榨汁杯，预计转化不错。'
pm = srv._extract_predicted_match(analysis_text)
ok8a = pm and pm['product_name'] == '便携榨汁杯' and pm['product_id'] == 'p_jzb' \
    and pm['confidence'] == 0.8 and '适合带便携榨汁杯' in pm['raw_quote']
check('8a. _extract_predicted_match 返回 dict 且 product_name/product_id 正确', ok8a, repr(pm)[:200])
event_id = srv._save_knowledge_event(analysis_text, 'agent_x', '达人分析标题')
row = conn.execute('SELECT conclusions FROM knowledge_events WHERE id = ?', (event_id,)).fetchone()
conclusions = json.loads(row['conclusions']) if row else {}
ok8b = event_id and 'predicted_match' in conclusions \
    and conclusions['predicted_match'].get('product_name') == '便携榨汁杯'
check('8b. _save_knowledge_event 的 conclusions 含 predicted_match', ok8b, repr(row['conclusions'])[:200] if row else 'no row')
check('8c. 无推荐文本返回 None', srv._extract_predicted_match('这是一段普通的达人数据分析，粉丝画像以女性为主。') is None)

conn.close()
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
