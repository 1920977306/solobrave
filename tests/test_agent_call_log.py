# -*- coding: utf-8 -*-
"""
AI 调 AI 审计日志（agent_call_log）守卫测试（2026-11-08 老大拍板）

覆盖：
  1. init_db 建 agent_call_log 表（字段 + 三个索引齐全）
  2. _log_agent_call 落库：字段透传、payload/result 各截断 2000 字
  3. _log_agent_call 落库失败不阻断主链路（_db_conn 抛异常也吞）
  4. GET /api/agent_calls 鉴权口径：人类 admin 200 / localhost AI 403 / 未登录 401
  5. 过滤参数 caller/callee/action/status + limit 上限 200

数据全落 tmp（importlib 独立模块实例 + DATA_DIR/DB_PATH 覆盖），零 prod 接触。
运行：.venv-test/bin/python -m pytest tests/test_agent_call_log.py -v
"""
import importlib.util
import json
import os
import sqlite3
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    """独立模块实例 + 全部数据路径指 tmp，init_db 建全量 schema。"""
    monkeypatch.setattr(srv, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(srv, 'DB_PATH', str(tmp_path / 'solobrave.db'))
    monkeypatch.setattr(srv, 'AGENTS_FILE', str(tmp_path / 'agents.json'))
    monkeypatch.setattr(srv, 'USERS_FILE', str(tmp_path / 'users.json'))
    monkeypatch.setattr(srv, 'PERMISSIONS_FILE', str(tmp_path / 'permissions.json'))
    monkeypatch.setattr(srv, 'GROUPS_FILE', str(tmp_path / 'groups.json'))
    monkeypatch.setattr(srv, 'TEAMS_FILE', str(tmp_path / 'teams.json'))
    monkeypatch.setattr(srv, 'SECRET_FILE', str(tmp_path / '.secret'))
    monkeypatch.setattr(srv, 'SETTINGS_FILE', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(srv, 'CHATS_DIR', str(tmp_path / 'chats'))
    monkeypatch.setattr(srv, 'MEMORY_DIR', str(tmp_path / 'memory'))
    monkeypatch.setattr(srv, 'INFLUENCER_DIR', str(tmp_path / 'influencers'))
    srv._ensure_data_dir()
    srv.init_db()
    return srv


def _conn(mod):
    return sqlite3.connect(mod.DB_PATH)


# ─── 1. DDL ─────────────────────────────────────────────────────────────────

def test_ddl_creates_agent_call_log(mod):
    conn = _conn(mod)
    try:
        cols = {r[1] for r in conn.execute('PRAGMA table_info(agent_call_log)')}
        assert {'id', 'caller_agent_id', 'caller_name', 'callee_agent_id',
                'action', 'payload', 'result', 'status',
                'proposal_id', 'decided_by', 'created_at'} <= cols
        idx = {r[1] for r in conn.execute(
            "SELECT * FROM sqlite_master WHERE type='index'"
            " AND tbl_name='agent_call_log'")}
        assert 'idx_agent_call_log_created_at' in idx
        assert 'idx_agent_call_log_caller' in idx
        assert 'idx_agent_call_log_callee' in idx
    finally:
        conn.close()


# ─── 2. 写入 + 截断 ─────────────────────────────────────────────────────────

def test_log_agent_call_roundtrip_and_truncation(mod):
    big = {'text': '长' * 5000}
    srv._log_agent_call(
        action='message.send', caller_agent_id='emp_A', caller_name='木木',
        callee_agent_id='emp_B', payload=big, result={'ok': True},
        status='ok', proposal_id='prop_1', decided_by='user_boss')
    conn = _conn(mod)
    try:
        row = conn.execute('SELECT * FROM agent_call_log').fetchone()
        d = dict(zip([c[1] for c in conn.execute('PRAGMA table_info(agent_call_log)')], row))
        assert d['caller_agent_id'] == 'emp_A'
        assert d['caller_name'] == '木木'
        assert d['callee_agent_id'] == 'emp_B'
        assert d['action'] == 'message.send'
        assert d['status'] == 'ok'
        assert d['proposal_id'] == 'prop_1'
        assert d['decided_by'] == 'user_boss'
        assert len(d['payload']) == 2000          # 截断到 2000 字
        assert d['id'].startswith('acall_')
        assert isinstance(d['created_at'], int) and d['created_at'] > 0
    finally:
        conn.close()


def test_log_agent_call_never_raises_on_db_failure(mod, monkeypatch):
    def _boom():
        raise RuntimeError('db down')
    monkeypatch.setattr(srv, '_db_conn', _boom)
    # 不抛即过 —— 审计是观察器不是闸门
    srv._log_agent_call(action='task.create', caller_agent_id='emp_A')


# ─── 3-5. GET /api/agent_calls 鉴权 + 过滤 ─────────────────────────────────

def _make_handler(method_name, path='/'):
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

    H.path = path
    h = H()
    bound = types.MethodType(getattr(srv.SoloBraveHandler, method_name), h)
    assert bound is not None
    return bound, sent


def _admin_auth():
    return types.SimpleNamespace(is_authenticated=True, is_admin=True,
                                 localhost_agent_id=None,
                                 user_info={'userId': 'user_boss'})


def _agent_auth():
    return types.SimpleNamespace(is_authenticated=True, is_admin=True,
                                 localhost_agent_id='emp_A',
                                 user_info={'userId': 'user_creator'})


def _anon_auth():
    return types.SimpleNamespace(is_authenticated=False, is_admin=False,
                                 localhost_agent_id=None, error='未登录',
                                 status=401)


def test_get_agent_calls_authz_matrix(mod, monkeypatch):
    cases = [(_admin_auth(), 200), (_agent_auth(), 403), (_anon_auth(), 401)]
    for auth, want in cases:
        monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: auth)
        bound, sent = _make_handler('_handle_get_agent_calls')
        bound()
        assert sent['code'] == want, f'auth={auth!r} 期望 {want} 实得 {sent["code"]}'


def test_get_agent_calls_filters_and_limit(mod, monkeypatch):
    monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: _admin_auth())
    for i in range(5):
        srv._log_agent_call(action='message.send', caller_agent_id='emp_A',
                            callee_agent_id='emp_B', status='ok')
    srv._log_agent_call(action='message.send', caller_agent_id='emp_X',
                        callee_agent_id='emp_Y', status='failed')
    srv._log_agent_call(action='task.create', caller_agent_id='emp_A',
                        status='ok')

    def _get(path):
        bound, sent = _make_handler('_handle_get_agent_calls', path=path)
        bound()
        assert sent['code'] == 200
        return sent['obj']

    d = _get('/api/agent_calls')
    assert d['total'] == 7 and len(d['calls']) == 7

    d = _get('/api/agent_calls?caller=emp_A')
    assert d['total'] == 6 and all(c['caller_agent_id'] == 'emp_A' for c in d['calls'])

    d = _get('/api/agent_calls?callee=emp_Y')
    assert d['total'] == 1 and d['calls'][0]['callee_agent_id'] == 'emp_Y'

    d = _get('/api/agent_calls?action=task.create')
    assert d['total'] == 1

    d = _get('/api/agent_calls?status=failed')
    assert d['total'] == 1

    d = _get('/api/agent_calls?limit=9999')
    assert d['limit'] == 200                    # limit 封顶 200

    # 新插入的排最前（created_at DESC）
    d = _get('/api/agent_calls?limit=1')
    assert d['calls'][0]['action'] == 'task.create'


def test_get_agent_calls_payload_is_json(mod, monkeypatch):
    monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: _admin_auth())
    srv._log_agent_call(action='message.send', caller_agent_id='emp_A',
                        callee_agent_id='emp_B',
                        payload={'content': '你好'}, result={'ok': True})
    bound, sent = _make_handler('_handle_get_agent_calls')
    bound()
    call = sent['obj']['calls'][0]
    assert json.loads(call['payload']) == {'content': '你好'}
    assert json.loads(call['result']) == {'ok': True}
