# -*- coding: utf-8 -*-
"""
身份总闸守卫测试（P1-④，2026-11-15 老大拍板 + 看守点：is_admin/is_platform_admin 双强制
+ 第三条断言：agent 直连打 platform_admin 端点必 403）

核心不变量：AI 员工本地直连（localhost_agent_id 存在）时——
  is_admin           恒 False（无论 creator role 多高）
  is_platform_admin  恒 False（即使 kind 行读出 super_admin——userId 映射的是 creator，
                     kind 行恰是 super_admin，必须在这层挡住）
  is_tenant_manager  恒 False（由 platform_admin 传导）
真人身份不受影响：老大 JWT（kind=super_admin）三闸全开；employee 依旧非 admin。

钉死 2026-11-13 prod 实测洞：X-Agent-Id 直连映射 creator 身份，creator 是 admin 时
员工以老大身份自建 P1 单。

数据全走独立模块实例 + monkeypatch（_subject_kind 打桩），零 prod 接触；
末尾附活机探测（server 不在线则 skip，不阻塞 CI）。
"""
import importlib.util
import json
import os
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIVE = os.environ.get('SOLO_BRAVE_LIVE', '') == '1'

_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

BOSS_UID = 'user_7acb72ff'  # 老大（super_admin kind 行）

live_gate = pytest.mark.skipif(not LIVE,
                               reason='live 门禁：需 SOLO_BRAVE_LIVE=1（真打 localhost:8080）')


def _auth(user_id, role='admin', agent_id=None):
    r = srv.AuthResult(user_info={'userId': user_id, 'role': role})
    if agent_id:
        r.localhost_agent_id = agent_id
    return r


# ─── 1. 总闸：agent 直连三闸全关（即使 kind=super_admin 也抬不起来）──────────

def test_agent_direct_never_admin_even_with_super_admin_kind(monkeypatch):
    """老大看守点：is_admin 强制 False。kind 行打桩成 super_admin 也没用——
    员工 userId 映射的就是 creator，这是真实攻击形态。"""
    monkeypatch.setattr(srv, '_subject_kind', lambda uid: 'super_admin')
    a = _auth(BOSS_UID, 'admin', agent_id='emp_xiaotuo')
    assert a.is_admin is False, 'agent 直连 is_admin 必须为 False（总闸）'


def test_agent_direct_never_platform_admin_even_with_super_admin_kind(monkeypatch):
    """老大看守点第三条断言的身份层：is_platform_admin 强制 False。
    没这层，员工直连会携 creator 的 super_admin kind 行直通 /api/admin/* 平台面。"""
    monkeypatch.setattr(srv, '_subject_kind', lambda uid: 'super_admin')
    a = _auth(BOSS_UID, 'admin', agent_id='emp_xiaotuo')
    assert a.is_platform_admin is False, 'agent 直连 is_platform_admin 必须为 False（总闸）'
    assert a.is_tenant_manager is False, 'agent 直连 is_tenant_manager 必须为 False（传导）'


def test_agent_direct_employee_role_also_blocked():
    a = _auth(BOSS_UID, 'employee', agent_id='emp_keeper')
    assert a.is_admin is False
    assert a.is_platform_admin is False


# ─── 2. 真人不受影响 ─────────────────────────────────────────────────────────

def test_human_boss_keeps_full_access(monkeypatch):
    """双断言的「200 侧」：真人老大（无 localhost_agent_id，kind=super_admin）三闸全开。"""
    monkeypatch.setattr(srv, '_subject_kind', lambda uid: 'super_admin')
    b = _auth(BOSS_UID, 'admin')
    assert b.is_admin is True
    assert b.is_platform_admin is True
    assert b.is_tenant_manager is True


def test_human_employee_still_not_admin(monkeypatch):
    monkeypatch.setattr(srv, '_subject_kind', lambda uid: 'biz_staff')
    e = _auth('user_30f05078', 'employee')
    assert e.is_admin is False
    assert e.is_platform_admin is False


def test_subject_kind_fallback_when_no_row(monkeypatch):
    """无 kind 行时回退 role 语义不能被子开关意外改动。"""
    monkeypatch.setattr(srv, '_subject_kind', lambda uid: None)
    b = _auth(BOSS_UID, 'admin')
    assert b.is_platform_admin is True   # 回退 role=='admin'
    a = _auth(BOSS_UID, 'admin', agent_id='emp_x')
    assert a.is_platform_admin is False  # 总闸优先于回退


# ─── 3. 权限计算不被总闸误伤（员工按创建者角色模板拿模块权限，非 admin 通道）──

def test_agent_module_permission_via_creator_template(tmp_path, monkeypatch):
    """回归保护：总闸封死 is_admin 后，_get_effective_permissions 走角色模板路径
    （创建者 role=admin → admin 模板全模块），员工业务链路（采集回写/日报）不 403。
    完整模拟生产 auth 路径：load_user_record 把创建者记录挂上（server.py:3456 同款）。"""
    perms_file = tmp_path / 'permissions.json'
    perms_file.write_text(json.dumps({
        'roleTemplates': [
            {'id': 'admin', 'modules': {m: True for m in srv.AVAILABLE_MODULES},
             'knowledgeCategories': ['*']},
        ],
        'userOverrides': {},
    }, ensure_ascii=False), encoding='utf-8')
    users_file = tmp_path / 'users.json'
    users_file.write_text(json.dumps(
        [{'id': BOSS_UID, 'role': 'admin', 'username': 'boss'}],
        ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(srv, 'PERMISSIONS_FILE', str(perms_file))
    monkeypatch.setattr(srv, 'USERS_FILE', str(users_file))
    a = _auth(BOSS_UID, 'admin', agent_id='emp_xiaotuo')
    a.load_user_record()
    assert a.user_record, 'load_user_record 必须挂上创建者记录（生产路径同款）'
    perms = srv._get_effective_permissions(a)
    assert perms['modules'].get('influencers') is True, '员工采集回写模块权限不可被总闸误伤'
    assert '*' in perms['knowledgeCategories']


# ─── 4. 活机探测（server 不在线则 skip）：两条 403 + 真人 200 ─────────────────

def _probe(url, method, body, headers):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Content-Type': 'application/json', **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode('utf-8') or '{}')
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode('utf-8') or '{}')
        except Exception:
            return e.code, {}
    except Exception:
        return -1, {}


@pytest.mark.live
@live_gate
def test_live_agent_direct_admin_endpoint_403():
    """断言一（活机）：agent 直连打 admin 业务端点（派单优先级口）必 403。"""
    code, _ = _probe('http://localhost:8080/api/dispatch-queue', 'POST',
                     {'summary': 'identity-gate-live-probe', 'priority': 1},
                     {'X-Agent-Id': 'emp_xiaotuo'})
    if code == -1:
        pytest.skip('server 不在线')
    assert code == 403, f'agent 直连打 admin 端点应 403，实测 {code}'


@pytest.mark.live
@live_gate
def test_live_agent_direct_platform_admin_endpoint_403():
    """老大看守点第三条断言（活机）：agent 直连打 platform_admin 端点必 403。
    选 GET /api/tenants（租户管理面，is_platform_admin 闸、**历史上无 localhost 焊点**）——
    旧代码里员工直连曾携 creator 的 super_admin kind 行直通此口，总闸必须在根上封死它。
    若此探测在旧代码上跑会 200（暴露洞），在新代码上必须 403。"""
    code, _ = _probe('http://localhost:8080/api/tenants', 'GET', None,
                     {'X-Agent-Id': 'emp_xiaotuo'})
    if code == -1:
        pytest.skip('server 不在线')
    assert code == 403, f'agent 直连打 platform_admin 端点应 403，实测 {code}'


@pytest.mark.live
@live_gate
def test_live_dispatch_queue_create_without_priority_allowed():
    """语义回归（活机）：不带 priority 的创建仍是开放面（§17 拉单模型），不得被总闸误伤。
    探针行创建后立刻置「已作废」清场，不留在队列里。"""
    code, data = _probe('http://localhost:8080/api/dispatch-queue', 'POST',
                        {'summary': 'identity-gate-live-probe（总闸回归探针）'},
                        {'X-Agent-Id': 'emp_xiaotuo'})
    if code == -1:
        pytest.skip('server 不在线')
    assert code == 200, f'员工开放创建（无 priority）应 200，实测 {code}'
    did = (data or {}).get('id')
    assert did, f'创建成功必须回 id，实测 {data}'
    code2, _ = _probe(f'http://localhost:8080/api/dispatch-queue/{did}', 'PUT',
                      {'status': '已作废'}, {'X-Agent-Id': 'emp_xiaotuo'})
    assert code2 == 200, f'探针行作废清场失败（{did}）HTTP {code2}，请人工作废'
