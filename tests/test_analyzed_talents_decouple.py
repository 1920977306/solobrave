# -*- coding: utf-8 -*-
"""
「已分析达人」语义接口端到端测试 (fix/analyzed-talents-decouple)

覆盖:
  1) employee (ayn, knowledge:false / influencers:true) 调
     GET /api/talents/analyzed 不再 403，且只能看到自己子库达人的分析
  2) admin 能看到全部已分析达人
  3) 没有 analysis 记录的达人不出现；vision_data 事件不单独触发入列
  4) 旧 entity_id 形态兼容: name:达人名 / tal_ / inf_
  5) 其他子账号子库的达人分析对 ayn 不可见（达人可见性不被绕过）
  6) 旧接口 /api/knowledge-events 对 ayn 仍 403（对照，证明解耦语义）
  7) apiFetch critical 语义：用 JS 源码断言 403 不再静默（critical 抛错，
     非 critical GET 仍返回空 Response，不影响既有批量静默行为）

策略: importlib 加载 solobrave-server.py（同 test_talent_full_sync），
      临时 DATA_DIR + init_db() 建库造数，ThreadingHTTPServer 跑在高位随机端口。
"""
import http.server
import importlib.util
import json
import os
import re
import socket
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location('solobrave_server_p02', ROOT / 'solobrave-server.py')
srv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(srv)


# ───────────────────────── helpers ─────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _http(url, token=None, timeout=10):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, body


def _insert_talent(conn, tid, name, created_by):
    conn.execute(
        "INSERT INTO talents (id, name, status, followers, created_by) "
        "VALUES (?, ?, 'active', ?, ?)",
        (tid, name, 10000, created_by))


def _insert_event(conn, eid, entity_id, event_type, created_at,
                  entity_type='talent', title='', content_full='',
                  agent_id='emp_1780199176680'):
    conn.execute(
        "INSERT INTO knowledge_events (id, entity_type, entity_id, agent_id, "
        "event_type, title, content_full, content_summary, conclusions, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '', '{}', ?)",
        (eid, entity_type, entity_id, agent_id, event_type, title,
         content_full, created_at))


class _AnalyzedTalentsEnv:
    """造数 + 起服务，每个测试独立实例。"""

    def __init__(self):
        self.tmpdir = str(ROOT / 'tmp_verify' / ('e2e_' + str(int(time.time() * 1000))))
        os.makedirs(self.tmpdir, exist_ok=True)
        # 重定向服务端全局路径（对齐 main() --data 的做法）
        srv.DATA_DIR = self.tmpdir
        srv.AGENTS_FILE = os.path.join(self.tmpdir, 'agents.json')
        srv.USERS_FILE = os.path.join(self.tmpdir, 'users.json')
        srv.PERMISSIONS_FILE = os.path.join(self.tmpdir, 'permissions.json')
        srv.GROUPS_FILE = os.path.join(self.tmpdir, 'groups.json')
        srv.TEAMS_FILE = os.path.join(self.tmpdir, 'teams.json')
        srv.SECRET_FILE = os.path.join(self.tmpdir, '.secret')
        srv.SETTINGS_FILE = os.path.join(self.tmpdir, 'settings.json')
        srv.CHATS_DIR = os.path.join(self.tmpdir, 'chats')
        srv.MEMORY_DIR = os.path.join(self.tmpdir, 'memory')
        srv.INFLUENCER_DIR = os.path.join(self.tmpdir, 'influencers')
        srv.DB_PATH = os.path.join(self.tmpdir, 'solobrave.db')
        srv._ensure_data_dir()
        srv.init_db()

        # agents: ayn 的 AI 员工 Helen；另一个子账号的 AI 员工
        # _load_agents 要求纯数组（不是 {'agents': [...]}）
        with open(srv.AGENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump([
                {'id': 'emp_helen', 'name': 'Helen', 'createdBy': 'user_ayn',
                 'status': 'online'},
                {'id': 'emp_other', 'name': 'OtherAI', 'createdBy': 'user_bob',
                 'status': 'online'},
            ], f)
        # users: admin / ayn(employee) / bob(employee)
        with open(srv.USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump([
                {'id': 'user_admin', 'username': 'admin', 'role': 'admin',
                 'pwdVersion': 0},
                {'id': 'user_ayn', 'username': 'ayn', 'role': 'employee',
                 'pwdVersion': 0},
                {'id': 'user_bob', 'username': 'bob', 'role': 'employee',
                 'pwdVersion': 0},
            ], f)
        # permissions: ayn 复刻贺主管 override — knowledge:false, influencers:true
        perms = srv._default_permission_templates()
        perms['userOverrides'] = {
            'user_ayn': {'modules': {
                'dashboard': True, 'messages': True, 'knowledge': False,
                'settings': True, 'products': False, 'groups': True,
                'influencers': True, 'employees': True,
            }, 'knowledgeCategories': ['*']},
        }
        with open(srv.PERMISSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(perms, f)

        conn = srv._db_conn()
        try:
            # ayn 子库：3 个达人（tal_ 新版 / inf_ 旧版 / name: 游离名匹配）
            _insert_talent(conn, 'tal_ayn1', '阿恩达人A', 'user_ayn')
            _insert_talent(conn, 'inf_ayn2', '阿恩达人B', 'emp_helen')
            _insert_talent(conn, 'tal_ayn3', '阿恩达人C', 'user_ayn')
            # 第 4 个：只有 vision_data，没有 analysis → 不应出现
            _insert_talent(conn, 'tal_ayn4', '阿恩达人D', 'user_ayn')
            # bob 子库达人：ayn 不可见
            _insert_talent(conn, 'tal_bob1', '鲍勃达人A', 'user_bob')
            _insert_talent(conn, 'tal_bob2', '鲍勃达人B', 'emp_other')
            # 主库达人（created_by 空）：仅 admin 可见
            _insert_talent(conn, 'tal_main1', '主库达人A', '')

            base = int(time.time() * 1000)
            # ayn1: 两条 analysis，取最新（entity_id=t.id）
            _insert_event(conn, 'ev_a1_old', 'tal_ayn1', 'analysis', base - 2000,
                          title='A旧分析', content_full='A旧 评级：B级')
            _insert_event(conn, 'ev_a1_new', 'tal_ayn1', 'analysis', base - 1000,
                          title='A新分析', content_full='A新 评级：A级 粉丝量12.3万')
            # ayn2: inf_ id 匹配
            _insert_event(conn, 'ev_a2', 'inf_ayn2', 'analysis', base - 3000,
                          content_full='评级：C级')
            # ayn3: 旧版 name:达人名 匹配
            _insert_event(conn, 'ev_a3', 'name:阿恩达人C', 'analysis', base - 4000,
                          content_full='评级：D级')
            # ayn4: 仅 vision_data
            _insert_event(conn, 'ev_a4v', 'tal_ayn4', 'vision_data', base - 500,
                          content_full='截图')
            # bob 子库分析
            _insert_event(conn, 'ev_b1', 'tal_bob1', 'analysis', base - 1500,
                          content_full='评级：A级')
            _insert_event(conn, 'ev_b2', 'name:鲍勃达人B', 'analysis', base - 1600,
                          content_full='评级：B级')
            # 主库达人分析
            _insert_event(conn, 'ev_m1', 'tal_main1', 'analysis', base - 1700,
                          content_full='评级：A级')
            conn.commit()
        finally:
            conn.close()

        self.port = _free_port()
        self.httpd = http.server.ThreadingHTTPServer(
            ('127.0.0.1', self.port), srv.SoloBraveHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        self.admin_token = srv.generate_token('user_admin', 'admin', 0)
        self.ayn_token = srv.generate_token('user_ayn', 'employee', 0)
        self.bob_token = srv.generate_token('user_bob', 'employee', 0)

    def get(self, path, token):
        return _http(f'http://127.0.0.1:{self.port}{path}', token)

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class AnalyzedTalentsEndpointSuite(unittest.TestCase):

    def setUp(self):
        self.env = _AnalyzedTalentsEnv()

    def tearDown(self):
        self.env.close()

    def test_ayn_sees_only_her_subpool(self):
        """ayn(employee, knowledge:false) 调新接口 200 且只有自己子库 3 个达人。"""
        code, data = self.env.get('/api/talents/analyzed?limit=200', self.env.ayn_token)
        self.assertEqual(code, 200, f'expected 200, got {code}: {data}')
        events = data['events']
        self.assertEqual(data['total'], 3)
        talent_ids = {e['talent_id'] for e in events}
        self.assertEqual(talent_ids, {'tal_ayn1', 'inf_ayn2', 'tal_ayn3'})
        # bob/主库的达人绝不可见
        self.assertTrue(all(e['talent_id'] not in
                            ('tal_bob1', 'tal_bob2', 'tal_main1') for e in events))

    def test_ayn_latest_event_chosen(self):
        """ayn1 取最近一条 analysis（ev_a1_new），并带 talent_name/content_full。"""
        _, data = self.env.get('/api/talents/analyzed', self.env.ayn_token)
        a1 = [e for e in data['events'] if e['talent_id'] == 'tal_ayn1'][0]
        self.assertEqual(a1['id'], 'ev_a1_new')
        self.assertEqual(a1['talent_name'], '阿恩达人A')
        self.assertIn('A级', a1['content_full'])

    def test_admin_sees_all(self):
        """admin 拿到全部 6 个有 analysis 的达人（含 bob 子库与主库）。"""
        code, data = self.env.get('/api/talents/analyzed?limit=200',
                                  self.env.admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(data['total'], 6)
        self.assertEqual(
            {e['talent_id'] for e in data['events']},
            {'tal_ayn1', 'inf_ayn2', 'tal_ayn3', 'tal_bob1', 'tal_bob2',
             'tal_main1'})

    def test_bob_sees_only_own(self):
        """对照：bob 只能看到自己子库 2 个，看不到 ayn 的。"""
        _, data = self.env.get('/api/talents/analyzed', self.env.bob_token)
        self.assertEqual({e['talent_id'] for e in data['events']},
                         {'tal_bob1', 'tal_bob2'})

    def test_talents_without_analysis_excluded(self):
        """只有 vision_data（无 analysis）的 tal_ayn4 不出现。"""
        for token in (self.env.ayn_token, self.env.admin_token):
            _, data = self.env.get('/api/talents/analyzed', token)
            self.assertNotIn('tal_ayn4', {e['talent_id'] for e in data['events']})

    def test_legacy_knowledge_events_still_403_for_ayn(self):
        """对照：旧接口对 ayn 仍 403 —— 证明解耦没有放宽 knowledge gate。"""
        code, data = self.env.get(
            '/api/knowledge-events?entity_type=talent&limit=200',
            self.env.ayn_token)
        self.assertEqual(code, 403, f'expected 403, got {code}: {data}')

    def test_unauthenticated_rejected(self):
        code, _ = self.env.get('/api/talents/analyzed', None)
        self.assertEqual(code, 401)

    def test_limit_clamped(self):
        """limit 上限 500 不报错（造数 6 条，全返回）。"""
        code, data = self.env.get('/api/talents/analyzed?limit=999999',
                                  self.env.admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(data['total'], 6)


class ApiFetchCriticalContractSuite(unittest.TestCase):
    """前端 apiFetch 403 处理：critical 主数据源不再静默。

    直接对 index.html 内联源码做契约断言（无浏览器环境）：
      - critical 分支在 GET 静默之前抛错
      - 非 critical GET 行为保留（返回 '[]' 200 Response）
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / 'index.html').read_text(encoding='utf-8')

    def test_critical_throws_before_silent_path(self):
        m = re.search(r'async function apiFetch\(.*?\n\}', self.html, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(0)
        crit_idx = body.find('if (options.critical)')
        silent_idx = body.find("403 Forbidden, skipping")
        self.assertGreater(crit_idx, -1, '缺少 critical 分支')
        self.assertLess(crit_idx, silent_idx,
                        'critical 必须在静默返回之前抛错')
        # critical 分支抛出错误
        seg = body[crit_idx:silent_idx]
        self.assertIn("throw new Error('权限不足 (403)')", seg)

    def test_non_critical_get_silent_kept(self):
        m = re.search(r'async function apiFetch\(.*?\n\}', self.html, re.DOTALL)
        self.assertIsNotNone(m)
        body = m.group(0)
        self.assertIn("403 Forbidden, skipping", body)
        self.assertIn("new Response('[]'", body)

    def test_analyzed_tab_uses_critical_and_new_endpoint(self):
        # loadAnalyzedTalents 改到新接口且带 critical
        m = re.search(r'function loadAnalyzedTalents\(\).*?\n\}', self.html, re.DOTALL)
        self.assertIsNotNone(m)
        block = m.group(0)
        self.assertIn('/api/talents/analyzed', block)
        self.assertIn('critical: true', block)
        # 实际请求行不再出现旧接口（注释里的历史说明除外）
        self.assertNotIn("apiFetch('/api/knowledge-events?entity_type=talent'", block)


if __name__ == '__main__':
    unittest.main()
