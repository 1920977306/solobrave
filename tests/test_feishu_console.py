# -*- coding: utf-8 -*-
"""§119 P1 飞书助理控制台 单测（2026-11-01，老大「可以」批准）

背景（架构文档 docs/FEISHU-PLATFORM-ARCHITECTURE.md P1）：飞书功能独立成项目，
server 侧要有控制台三区块 —— 租户总览 / 角色管理 / 聊天审计。

守卫点（行为断言，变异对照：砍 role 透传 / 砍「无文件⇒未启用」必红）：
1. _feishu_console_merge：state（角色/在线）与 credits（标签/余额/30天消耗）按
   tenant_key 合并；单边缺数据降级不炸；role 从 state 原样透传（不许被改写/丢弃）
2. _feishu_role_info：persona 模板元数据 —— 文件存在=启用（mtime=版本）、
   「# 训练沉淀」后的 '-' 条目=纠正条数（排除（…）说明行）、无文件=未启用
   （商家助理无 merchant.md 必须未启用，这是老大 10-28「商家先不做」的口径锁）
3. _handle_get_feishu_console / _handle_get_feishu_chat：admin 鉴权、双源都挂⇒502、
   chat 透传带 tenant_key 编码 + limit、缺 tenant_key⇒400 不碰代理

铁律：零 prod 接触 —— server 模块走 tmp DATA_DIR；_daren_bot_admin_api 一律
monkeypatch，绝不真连 8092；_feishu_role_info 一律传 tmp personas_dir。
"""
import importlib.util
import json
import os
import tempfile
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_server(tmp):
    spec = importlib.util.spec_from_file_location(
        'solobrave_server_feishu_console', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    os.environ.setdefault('SOLOBRAVE_TEST_NO_SERVE', '1')
    spec.loader.exec_module(mod)
    mod.DATA_DIR = tmp
    mod.DB_PATH = os.path.join(tmp, 'solobrave.db')
    mod.AGENTS_FILE = os.path.join(tmp, 'agents.json')
    mod.USERS_FILE = os.path.join(tmp, 'users.json')
    mod.SECRET_FILE = os.path.join(tmp, '.secret')
    mod._ensure_data_dir()
    mod.init_db()
    return mod


@pytest.fixture(scope='module')
def srv():
    tmp = tempfile.mkdtemp(prefix='feishu_console_srv_')
    yield _load_server(tmp)


# ---------- 守卫 1：合并（纯函数） ----------

class TestMerge:
    def _state(self):
        return {'tenants': [
            {'tenant_key': 'mumu', 'app_id': 'cli_a', 'role': 'commerce',
             'douyin_id': '62224890070', 'balance': 42, 'ws_online': True},
            {'tenant_key': 'daren_test2', 'role': 'daren', 'ws_online': False},
        ], 'alerts': [{'kind': 'login_lost'}]}

    def _credits(self):
        return {'accounts': [
            {'tenant_key': 'mumu', 'tenant_label': '木木', 'balance': 42,
             'douyin_id': '62224890070', 'updated_at': 1},
        ], 'usage30d': {'mumu': 120}}

    def test_merge_by_tenant_key(self, srv):
        out = srv._feishu_console_merge(self._state(), self._credits())
        by = {t['tenant_key']: t for t in out['tenants']}
        mumu = by['mumu']
        # state 侧字段透传
        assert mumu['role'] == 'commerce', 'role 必须原样透传（变异：改写 role 必红）'
        assert mumu['ws_online'] is True
        assert mumu['app_id'] == 'cli_a'
        # credits 侧字段合并
        assert mumu['tenant_label'] == '木木'
        assert mumu['balance'] == 42
        assert mumu['usage30d'] == 120
        # 只在 state 出现的租户也在（credits 缺它 ⇒ 降级兜底，不丢）
        t2 = by['daren_test2']
        assert t2['role'] == 'daren'
        assert t2['tenant_label'] == 'daren_test2', '无标签时回退 tenant_key'
        assert t2['balance'] == 0
        assert t2['usage30d'] == 0
        assert out['alerts'] == [{'kind': 'login_lost'}]

    def test_only_credits_side_still_works(self, srv):
        out = srv._feishu_console_merge({}, self._credits())
        (card,) = out['tenants']
        assert card['tenant_key'] == 'mumu'
        assert card['tenant_label'] == '木木'
        assert card['role'] == '', 'credits 单边 ⇒ role 空串兜底'
        assert card['ws_online'] is False

    def test_both_empty_gives_empty_tenants(self, srv):
        out = srv._feishu_console_merge(None, None)
        assert out['tenants'] == []
        assert out['alerts'] == []


# ---------- 守卫 2：角色模板解析 ----------

_DAREN_MD = """# 达人助理 · 角色模板

你是抖音电商达人的AI选品顾问。

# 训练沉淀
（达人的纠正和建议会自动积累到这里，让全体达人共享教训——聊天即训练。）
- 纠正：佣金口径以专属佣金为准
- 建议：回答要带数据依据
"""


class TestRoleInfo:
    def test_parse_enabled_and_corrections(self, srv, tmp_path):
        (tmp_path / 'daren.md').write_text(_DAREN_MD, encoding='utf-8')
        (tmp_path / 'commerce.md').write_text('# 商务\n\n无训练沉淀节\n', encoding='utf-8')
        roles = {r['role']: r for r in srv._feishu_role_info(str(tmp_path))}
        d = roles['daren']
        assert d['enabled'] is True
        assert d['updated_at'], 'mtime 必须透出（模板版本）'
        assert d['corrections'] == 2, '只数「- 」条目，（…）说明行排除'
        c = roles['commerce']
        assert c['enabled'] is True
        assert c['corrections'] == 0, '无训练沉淀节 ⇒ 0，不许炸'
        m = roles['merchant']
        assert m['enabled'] is False, '变异：无文件也标启用 ⇒ 必红'
        assert m['updated_at'] is None
        assert '未启用' in m['note']

    def test_bad_file_does_not_raise(self, srv, tmp_path):
        (tmp_path / 'daren.md').write_bytes(b'\xff\xfe broken')
        roles = {r['role']: r for r in srv._feishu_role_info(str(tmp_path))}
        assert roles['daren']['enabled'] is False
        assert '不可用' in roles['daren']['note']

    def test_default_dir_matches_repo_contract(self, srv):
        """真实仓库：达人/商务启用，商家未启用（老大 10-28 口径锁）。"""
        roles = {r['role']: r for r in srv._feishu_role_info()}
        assert roles['daren']['enabled'] is True
        assert roles['commerce']['enabled'] is True
        assert roles['merchant']['enabled'] is False


# ---------- 守卫 3：handler 行为 ----------

def _admin_auth():
    return type('A', (), {'is_authenticated': True, 'is_admin': True,
                          'is_tenant_admin': False, 'user_id': 'boss',
                          'error': None, 'status': 401})


def _make_handler(srv, method_name, path='/'):
    sent = {}

    class H:
        headers = {}
        client_address = ('127.0.0.1', 51234)

        def _send_json(self, code, obj):
            sent['code'] = code
            sent['obj'] = obj

        def _send_json_error(self, code, msg):
            sent['code'] = code
            sent['obj'] = {'error': msg}

        def _send_auth_error(self, error, status):
            sent['code'] = status
            sent['obj'] = {'error': error}

    H.path = path  # 类体外绑定：类体内 `path = path` 会命中类名空间自身（NameError）
    h = H()
    bound = types.MethodType(getattr(srv.SoloBraveHandler, method_name), h)
    assert bound is not None, 'Handler 类名/方法名变了，探针要盯真名'
    return bound, sent


class TestConsoleHandler:
    def _call(self, srv, monkeypatch, auth=None, state=(200, None), credits=(200, None)):
        calls = []

        def fake_api(path, payload=None):
            calls.append(path)
            if path == '/admin/api/state':
                return state
            return credits
        monkeypatch.setattr(srv, '_daren_bot_admin_api', fake_api)
        monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: auth or _admin_auth())
        bound, sent = _make_handler(srv, '_handle_get_feishu_console')
        bound()
        return sent, calls

    def test_non_admin_forbidden(self, srv, monkeypatch):
        auth = type('A', (), {'is_authenticated': True, 'is_admin': False,
                              'is_tenant_admin': False, 'error': None,
                              'status': 401, 'user_id': 'u1'})
        sent, calls = self._call(srv, monkeypatch, auth=auth)
        assert sent['code'] == 403
        assert not calls, '无权时不许碰代理'

    def test_both_sources_down_returns_502(self, srv, monkeypatch):
        sent, _ = self._call(
            srv, monkeypatch,
            state=(502, {'ok': False, 'error': 'daren_bot 不可达'}),
            credits=(502, {'ok': False, 'error': 'daren_bot 不可达'}))
        assert sent['code'] == 502
        assert 'error' in sent['obj']

    def test_merges_and_attaches_roles(self, srv, monkeypatch):
        sent, calls = self._call(
            srv, monkeypatch,
            state=(200, {'tenants': [{'tenant_key': 'mumu', 'role': 'commerce'}], 'alerts': []}),
            credits=(200, {'accounts': [{'tenant_key': 'mumu', 'tenant_label': '木木',
                                         'balance': 42}], 'usage30d': {'mumu': 7}}))
        assert sent['code'] == 200
        assert calls == ['/admin/api/state', '/admin/api/credits']
        body = sent['obj']
        (card,) = body['tenants']
        assert card['role'] == 'commerce' and card['tenant_label'] == '木木'
        assert card['usage30d'] == 7
        roles = {r['role']: r for r in body['roles']}
        assert roles['merchant']['enabled'] is False


class TestChatHandler:
    def _call(self, srv, monkeypatch, path, auth=None):
        calls = []

        def fake_api(p, payload=None):
            calls.append(p)
            return 200, {'ok': True, 'messages': []}
        monkeypatch.setattr(srv, '_daren_bot_admin_api', fake_api)
        monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: auth or _admin_auth())
        bound, sent = _make_handler(srv, '_handle_get_feishu_chat', path=path)
        bound()
        return sent, calls

    def test_missing_tenant_rejected(self, srv, monkeypatch):
        sent, calls = self._call(srv, monkeypatch, '/api/feishu/chat')
        assert sent['code'] == 400
        assert not calls, '参数不齐不许碰代理'

    def test_forwards_encoded_tenant_and_limit(self, srv, monkeypatch):
        sent, calls = self._call(
            srv, monkeypatch,
            '/api/feishu/chat?tenant_key=mu%2Fmu&limit=50')
        assert sent['code'] == 200
        assert calls == ['/admin/api/chat?tenant_key=mu%2Fmu&limit=50'], \
            '透传不许丢参数/改变编码'

    def test_non_admin_forbidden(self, srv, monkeypatch):
        auth = type('A', (), {'is_authenticated': True, 'is_admin': False,
                              'is_tenant_admin': False, 'error': None,
                              'status': 401, 'user_id': 'u1'})
        sent, calls = self._call(srv, monkeypatch,
                                 '/api/feishu/chat?tenant_key=mumu', auth=auth)
        assert sent['code'] == 403
        assert not calls
