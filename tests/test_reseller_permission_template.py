# -*- coding: utf-8 -*-
"""
合伙人（reseller）角色权限模板 + provisional 标记测试 (2026-11-07)

拍板来源：D3（2026-11-02）——权限管理页的角色模板加「合伙人」；
带 provisional='pack.reseller.console' 临时标记，阶段 1 packs 落地时
按 pack 重新推导，不许把临时白名单当终态硬编码。

覆盖:
  1) 默认模板含 reseller，modules 逐项钉死（settings/knowledge/groups/employees=False），
     provisional == 'pack.reseller.console'，knowledgeCategories == []
  2) 旧版 permissions.json（无 reseller）→ _load_permissions() 后自动补齐 reseller
  3) 用户 roleTemplateId='reseller' 时 PUT /api/permissions/users/<id> 覆盖
     → 返回 override 带 provisional；改绑 employee 再 PUT → 标记消失
  4) _get_effective_permissions 对 reseller 用户求值 = 模板 modules（provisional 不影响求值）
  5) 前端角色分段按钮：仅 r.provisional 存在时渲染「临时」徽标

策略: 仿 test_analyzed_talents_decouple —— importlib 加载 solobrave-server.py，
      临时 DATA_DIR + init_db()，ThreadingHTTPServer 高位随机端口。
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import os
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

_SPEC = importlib.util.spec_from_file_location('solobrave_server_reseller', ROOT / 'solobrave-server.py')
srv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(srv)


# ───────────────────────── helpers ─────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def _http(url, token=None, method='GET', body=None, timeout=10):
    headers = {}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if body is not None:
        data = json.dumps(body).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode('utf-8')
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode('utf-8', errors='ignore')
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


# 期望的合伙人能力矩阵（P2-B 口径：看下级 + 推品 + AI 办公室）
EXPECTED_RESELLER_MODULES = {
    'dashboard': True,
    'messages': True,
    'knowledge': False,
    'settings': False,
    'products': True,
    'groups': False,
    'influencers': True,
    'employees': False,
}
PROVISIONAL_MARKER = 'pack.reseller.console'


class _ResellerEnv:
    """造数 + 起服务，每个测试独立实例。"""

    def __init__(self):
        self.tmpdir = str(ROOT / 'tmp_verify' / ('reseller_' + str(int(time.time() * 1000))))
        os.makedirs(self.tmpdir, exist_ok=True)
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

        with open(srv.AGENTS_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        # users: admin + 挂 reseller 模板的合伙人账号 + 普通 employee
        with open(srv.USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump([
                {'id': 'user_admin', 'username': 'admin', 'role': 'admin',
                 'pwdVersion': 0},
                {'id': 'user_rs', 'username': 'hehuoren', 'role': 'employee',
                 'roleTemplateId': 'reseller', 'pwdVersion': 0},
                {'id': 'user_emp', 'username': 'employee', 'role': 'employee',
                 'pwdVersion': 0},
            ], f)
        # permissions: 走 _load_permissions 的补齐路径（等价于旧版文件）
        with open(srv.PERMISSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(srv._default_permission_templates(), f)

        self.port = _free_port()
        self.httpd = http.server.ThreadingHTTPServer(
            ('127.0.0.1', self.port), srv.SoloBraveHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

        self.admin_token = srv.generate_token('user_admin', 'admin', 0)

    def get(self, path, token):
        return _http(f'http://127.0.0.1:{self.port}{path}', token)

    def put(self, path, token, body):
        return _http(f'http://127.0.0.1:{self.port}{path}', token,
                     method='PUT', body=body)

    def close(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class ResellerTemplateSuite(unittest.TestCase):

    def setUp(self):
        self.env = _ResellerEnv()

    def tearDown(self):
        self.env.close()

    # ── 1) 默认模板内容钉死 ──────────────────────────────

    def test_default_templates_contain_reseller(self):
        perms = srv._default_permission_templates()
        tmpl = next((t for t in perms['roleTemplates'] if t.get('id') == 'reseller'), None)
        self.assertIsNotNone(tmpl, '默认模板缺 reseller')
        self.assertEqual(tmpl['name'], '合伙人')
        for mod, expected in EXPECTED_RESELLER_MODULES.items():
            self.assertEqual(tmpl['modules'].get(mod), expected,
                             f'合伙人模板 modules[{mod}] 应为 {expected}')
        self.assertEqual(tmpl.get('provisional'), PROVISIONAL_MARKER)
        self.assertEqual(tmpl.get('knowledgeCategories'), [])

    def test_get_api_returns_reseller_with_provisional(self):
        """生产验证同款：GET /api/permissions 返回里带 reseller + provisional。"""
        code, data = self.env.get('/api/permissions', self.env.admin_token)
        self.assertEqual(code, 200, f'expected 200, got {code}: {data}')
        tmpl = next((t for t in data.get('roleTemplates', [])
                     if t.get('id') == 'reseller'), None)
        self.assertIsNotNone(tmpl, 'GET /api/permissions 缺 reseller 模板')
        self.assertEqual(tmpl.get('provisional'), PROVISIONAL_MARKER)

    def test_get_api_rejects_non_admin(self):
        code, _ = self.env.get('/api/permissions', None)
        self.assertEqual(code, 403)

    # ── 2) 旧版 permissions.json 自动补齐 ────────────────

    def test_load_permissions_backfills_reseller(self):
        """旧版文件没有 reseller → _load_permissions 补齐且 provisional  intact。"""
        perms = srv._default_permission_templates()
        perms['roleTemplates'] = [t for t in perms['roleTemplates']
                                  if t.get('id') != 'reseller']
        assert not any(t.get('id') == 'reseller' for t in perms['roleTemplates'])
        with open(srv.PERMISSIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(perms, f)

        loaded = srv._load_permissions()
        tmpl = next((t for t in loaded['roleTemplates'] if t.get('id') == 'reseller'), None)
        self.assertIsNotNone(tmpl, '_load_permissions 未补齐 reseller')
        self.assertEqual(tmpl.get('provisional'), PROVISIONAL_MARKER)
        self.assertEqual(tmpl['knowledgeCategories'], [])

    # ── 3) PUT 覆盖时自动打/摘 provisional 标记 ──────────

    def test_put_override_on_reseller_user_gets_provisional(self):
        body = {'modules': dict(EXPECTED_RESELLER_MODULES),
                'knowledgeCategories': []}
        code, data = self.env.put('/api/permissions/users/user_rs',
                                  self.env.admin_token, body)
        self.assertEqual(code, 200, f'expected 200, got {code}: {data}')
        override = data.get('userOverride') or {}
        self.assertEqual(override.get('provisional'), PROVISIONAL_MARKER,
                         '挂了 reseller 模板的账号保存覆盖后应带 provisional 标记')

        # 落盘核对（重启后标记仍在）
        saved = json.load(open(srv.PERMISSIONS_FILE, encoding='utf-8'))
        self.assertEqual(
            saved['userOverrides']['user_rs'].get('provisional'),
            PROVISIONAL_MARKER)

    def test_put_override_on_plain_employee_has_no_provisional(self):
        body = {'modules': {'dashboard': True, 'messages': True,
                            'knowledge': True, 'settings': True,
                            'products': True, 'groups': True,
                            'influencers': True, 'employees': False},
                'knowledgeCategories': ['*']}
        code, data = self.env.put('/api/permissions/users/user_emp',
                                  self.env.admin_token, body)
        self.assertEqual(code, 200)
        override = data.get('userOverride') or {}
        self.assertNotIn('provisional', override,
                         '普通 employee 模板的覆盖不应带 provisional 标记')

    def test_rebind_to_employee_drops_provisional(self):
        """先给 reseller 账号存覆盖（带标记），再把账号改绑 employee，
        重存一次 → 标记消失。"""
        body = {'modules': dict(EXPECTED_RESELLER_MODULES),
                'knowledgeCategories': []}
        code, data = self.env.put('/api/permissions/users/user_rs',
                                  self.env.admin_token, body)
        self.assertEqual(code, 200)
        self.assertEqual((data.get('userOverride') or {}).get('provisional'),
                         PROVISIONAL_MARKER)

        # 改绑 employee 模板
        users = json.load(open(srv.USERS_FILE, encoding='utf-8'))
        for u in users:
            if u['id'] == 'user_rs':
                u['roleTemplateId'] = 'employee'
        with open(srv.USERS_FILE, 'w', encoding='utf-8') as f:
            json.dump(users, f)

        code, data = self.env.put('/api/permissions/users/user_rs',
                                  self.env.admin_token, body)
        self.assertEqual(code, 200)
        self.assertNotIn('provisional', data.get('userOverride') or {},
                         '换绑非 provisional 模板后重存，标记必须消失')

    def test_put_permissions_rejects_non_admin(self):
        code, _ = self.env.put('/api/permissions/users/user_rs', None, {})
        self.assertEqual(code, 403)

    # ── 4) effective 求值：provisional 不影响权限求值 ────

    def test_effective_permissions_for_reseller(self):
        user = {'id': 'user_rs', 'role': 'employee', 'roleTemplateId': 'reseller'}
        eff = srv._get_effective_permissions(user)
        for mod, expected in EXPECTED_RESELLER_MODULES.items():
            self.assertEqual(eff['modules'].get(mod), expected,
                             f'合伙人 effective modules[{mod}] 应为 {expected}')
        self.assertEqual(eff['knowledgeCategories'], [])

    def test_effective_permissions_override_still_applies(self):
        """覆盖优先于模板（既有语义不被 provisional 打乱）。"""
        perms = srv._load_permissions()
        perms.setdefault('userOverrides', {})['user_rs'] = {
            'modules': {'knowledge': True}, 'knowledgeCategories': ['*']}
        srv._save_permissions(perms)
        user = {'id': 'user_rs', 'role': 'employee', 'roleTemplateId': 'reseller'}
        eff = srv._get_effective_permissions(user)
        self.assertTrue(eff['modules']['knowledge'],
                        '用户覆盖应能打开模板里关掉的模块')


class ResellerFrontendBadgeSuite(unittest.TestCase):
    """前端契约：角色分段按钮仅对带 provisional 的模板渲染「临时」徽标。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / 'js' / 'inline-07.js').read_text(encoding='utf-8')

    def test_badge_rendered_only_when_provisional_present(self):
        self.assertIn('r.provisional', self.js,
                      '徽标渲染必须以 r.provisional 存在为条件')
        # 徽标拼接在三元表达式里（不存在时为空串）
        idx = self.js.find('r.provisional')
        seg = self.js[idx:idx + 400]
        self.assertIn('临时', seg, '徽标文案应为「临时」')
        self.assertIn("title=", seg, '徽标要带 title 说明阶段 1 重推导')

    def test_badge_mentions_pack_marker(self):
        idx = self.js.find('r.provisional')
        seg = self.js[idx:idx + 400]
        self.assertIn('pack', seg,
                      '徽标说明里应写明 pack 标记（阶段 1 按它重推导）')


if __name__ == '__main__':
    unittest.main()
