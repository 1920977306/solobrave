# -*- coding: utf-8 -*-
"""
Scoped API token + 请求审计 守卫测试（写类 API 开放前置三件套）

覆盖：
  1. mint → lookup → revoke 全流程（明文不入库，只存 sha256）
  2. _required_api_scope 分类（method → read/write/admin + 管理面路径覆盖）
  3. _authenticate 端到端（read-only token 做 POST → 403 含「缺少 write」；
     做 GET → 通过且 api_scopes 正确；坏 token → 401）
  4. _api_audit_emit 落盘 jsonl 且字段齐全
  5. 负向：非 scoped 凭证（X-Agent-Id 通道）api_scopes 保持 None，不受影响

数据全部落在 monkeypatch 出来的 tmp DATA_DIR，不碰 prod 的 data/api_tokens.json。
运行：python3 -m pytest tests/test_api_scoped_tokens.py -v
"""
import hashlib
import importlib.util
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


@pytest.fixture()
def tmp_data(monkeypatch, tmp_path):
    """把服务端 DATA_DIR 指到 tmp：token 库与审计文件都在临时目录。"""
    monkeypatch.setattr(srv, 'DATA_DIR', str(tmp_path))
    return str(tmp_path)


# ─── 1. mint / lookup / revoke ──────────────────────────────────────────────

def test_mint_lookup_revoke_roundtrip(tmp_data):
    raw, rec = srv._api_token_mint('t-one', 'user_x', ('read', 'write'))
    assert raw.startswith('sbk_')
    assert len(raw) == 4 + 48
    assert rec['scopes'] == ['read', 'write']

    # 库里只有 sha256，没有明文
    store = srv._api_token_load()
    h = hashlib.sha256(raw.encode()).hexdigest()
    assert h in store
    assert raw not in json.dumps(store)

    got = srv._api_token_lookup(raw)
    assert got is not None
    assert got['name'] == 't-one'
    assert got['user_id'] == 'user_x'
    assert got['_hash'] == h
    # last_used_at 节流刷新
    assert got['last_used_at'] > 0

    # 吊销后 lookup 失效
    assert srv._api_token_revoke('t-one') == 1
    assert srv._api_token_lookup(raw) is None
    # 重复吊销 0 条
    assert srv._api_token_revoke('t-one') == 0


def test_lookup_rejects_bad_format(tmp_data):
    assert srv._api_token_lookup('not-a-token') is None
    assert srv._api_token_lookup('sbk_' + 'z' * 48) is None
    assert srv._api_token_lookup('sbk_' + 'a' * 47) is None  # 长度不对


# ─── 2. scope 分类 ──────────────────────────────────────────────────────────

def test_required_api_scope_classification():
    r = srv._required_api_scope
    assert r('GET', '/api/talents') == 'read'
    assert r('HEAD', '/api/talents') == 'read'
    assert r('POST', '/api/deals') == 'write'
    assert r('PUT', '/api/talents/t1') == 'write'
    assert r('PATCH', '/api/talents/t1') == 'write'
    assert r('DELETE', '/api/talents/t1') == 'admin'
    # 管理面路径：即使 GET 也要 admin
    assert r('GET', '/api/users') == 'admin'
    assert r('GET', '/api/teams') == 'admin'
    assert r('GET', '/api/settings/x') == 'admin'
    assert r('GET', '/api/admin/anything') == 'admin'
    assert r('GET', '/api/openclaw/config') == 'admin'
    assert r('GET', '/api/agents/emp_1/permanent') == 'admin'
    # 去 query 串 + 尾部斜杠
    assert r('GET', '/api/talents?q=测') == 'read'
    assert r('GET', '/api/talents/') == 'read'
    # 非管理面的相似路径不受影响
    assert r('GET', '/api/agents') == 'read'
    assert r('GET', '/api/user-profile') == 'read'


# ─── 3. _authenticate 端到端 ─────────────────────────────────────────────────

def _fake_handler(method, path, auth_header):
    return SimpleNamespace(
        command=method,
        path=path,
        cached_body=None,
        client_address=('10.9.9.9', 5555),
        _headers={'Authorization': auth_header},
    )


def _auth_headers(token):
    return {'Authorization': f'Bearer {token}'}


def test_readonly_token_blocked_on_write(tmp_data):
    raw, _ = srv._api_token_mint('ro', 'user_x', ('read',))
    h = _fake_handler('POST', '/api/deals', f'Bearer {raw}')
    result = srv._authenticate(_auth_headers(raw), '10.9.9.9', h)
    assert not result.is_authenticated
    assert result.status == 403
    assert '缺少 write' in (result.error or '')
    # 拒绝也要保留 token 身份进审计，不能丢成 anon
    assert h._auth_audit['kind'] == 'scoped'
    assert h._auth_audit['identity'] == 'token:ro'
    assert h._auth_audit['ok'] is False


def test_readonly_token_allowed_on_read(tmp_data):
    raw, _ = srv._api_token_mint('ro', 'user_x', ('read',))
    h = _fake_handler('GET', '/api/talents', f'Bearer {raw}')
    result = srv._authenticate(_auth_headers(raw), '10.9.9.9', h)
    assert result.is_authenticated
    assert result.api_scopes == {'read'}
    assert result.api_token_name == 'ro'
    # role 硬编码 user：即使 user 不存在也不许是 admin
    assert not result.is_admin
    # 审计摘要已挂到 handler 上
    assert h._auth_audit['kind'] == 'scoped'
    assert h._auth_audit['identity'] == 'token:ro'
    assert h._auth_audit['ok'] is True


def test_bad_token_returns_401(tmp_data):
    h = _fake_handler('GET', '/api/talents', 'Bearer sbk_' + 'f' * 48)
    result = srv._authenticate(_auth_headers('sbk_' + 'f' * 48), '10.9.9.9', h)
    assert not result.is_authenticated
    assert result.status == 401


def test_revoked_token_returns_401(tmp_data):
    raw, _ = srv._api_token_mint('rev', 'user_x', ('read', 'write', 'admin'))
    srv._api_token_revoke('rev')
    h = _fake_handler('GET', '/api/talents', f'Bearer {raw}')
    result = srv._authenticate(_auth_headers(raw), '10.9.9.9', h)
    assert not result.is_authenticated
    assert result.status == 401


def test_admin_scope_gated_by_path(tmp_data):
    raw, _ = srv._api_token_mint('rw', 'user_x', ('read', 'write'))
    # 有 write 但没 admin：DELETE 被拒
    h = _fake_handler('DELETE', '/api/talents/t1', f'Bearer {raw}')
    r1 = srv._authenticate(_auth_headers(raw), '10.9.9.9', h)
    assert r1.status == 403 and '缺少 admin' in (r1.error or '')
    # 管理面 GET 也要 admin
    h2 = _fake_handler('GET', '/api/users', f'Bearer {raw}')
    r2 = srv._authenticate(_auth_headers(raw), '10.9.9.9', h2)
    assert r2.status == 403 and '缺少 admin' in (r2.error or '')


# ─── 4. 审计落盘 ─────────────────────────────────────────────────────────────

def test_audit_emit_writes_jsonl(tmp_data):
    raw, _ = srv._api_token_mint('aud', 'user_x', ('read',))
    h = _fake_handler('GET', '/api/talents', f'Bearer {raw}')
    result = srv._authenticate(_auth_headers(raw), '10.9.9.9', h)
    assert result.is_authenticated
    h._resp_status = 200
    t0 = time.time()
    srv._api_audit_emit(h, 'GET', t0)

    path = os.path.join(tmp_data, 'api_audit.jsonl')
    with open(path, encoding='utf-8') as f:
        line = json.loads(f.readline())
    for key in ('ts', 'method', 'path', 'status', 'dur_ms', 'ok', 'kind', 'identity', 'scopes'):
        assert key in line, f'审计行缺字段 {key}'
    assert line['method'] == 'GET'
    assert line['path'] == '/api/talents'
    assert line['status'] == 200
    assert line['ok'] is True
    assert line['kind'] == 'scoped'
    assert line['identity'] == 'token:aud'
    assert line['scopes'] == ['read']
    assert line['ip'] == '10.9.9.9'


def test_audit_skips_non_api_paths(tmp_data):
    h = _fake_handler('GET', '/index.html', 'Bearer whatever')
    srv._authenticate({'Authorization': 'Bearer whatever'}, '10.9.9.9', h)
    srv._api_audit_emit(h, 'GET', time.time())
    assert not os.path.exists(os.path.join(tmp_data, 'api_audit.jsonl'))


def test_audit_survives_broken_handler(tmp_data):
    # 连 _auth_audit 都没有的 handler：静默跳过，不抛异常
    srv._api_audit_emit(SimpleNamespace(path='/api/x', client_address=('1.1.1.1', 1)), 'GET', time.time())
    assert not os.path.exists(os.path.join(tmp_data, 'api_audit.jsonl'))


# ─── 5. 负向：非 scoped 凭证不受影响 ─────────────────────────────────────────

def test_agent_id_channel_not_scoped():
    h = _fake_handler('GET', '/api/talents', '')
    h._headers = {'X-Agent-Id': 'definitely_not_a_real_agent'}
    result = srv._authenticate(h._headers, '127.0.0.1', h)
    # 未知 agent → 拒绝，但走的是老通道而非 scope 闸门
    assert not result.is_authenticated
    assert getattr(result, 'api_scopes', None) is None
    assert h._auth_audit['kind'] == 'anon'


def test_anon_request_audited_as_anon(tmp_data):
    h = _fake_handler('GET', '/api/talents', '')
    result = srv._authenticate({}, '10.9.9.9', h)
    assert not result.is_authenticated
    assert getattr(result, 'api_scopes', None) is None
    assert h._auth_audit['kind'] == 'anon'
    assert h._auth_audit['ok'] is False
