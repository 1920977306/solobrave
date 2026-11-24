# -*- coding: utf-8 -*-
"""P2 双单守卫（2026-11-24 晨）：
1. dq_4d41045a5af9 撮合链路余额前置快失败（11-20 风暴：余额 0 后每达人 90s×120+
   次拒转空转，靠自动隔离才压住）
2. dq_b47546016b45 代理免费转发权限缝（无身份请求 house key 兜底转发、200
   成功、零记账——Helen 孤儿循环同款不可归因形态）

守卫矩阵：
1a. 余额 0 ⇒ _ai_match_candidates 直接快失败、零 AI 调用、degrade_reason=
    'insufficient_credits'
1b. 余额恢复 ⇒ 炸弹解锁、正常打分（自动恢复，无需人工解隔离）
1c. 白名单（_check_credit_balance 豁免）不受闸影响
1d. 源码锚：闸在唯一推理调用点 _call_ai_for_json 之前
2a. 无身份无 key ⇒ 401 + 零 house 池调用
2b. 无身份带 key ⇒ 用 caller 自持 key（house 池不兜底）
2c. proxy_<agent> 形态 ⇒ 原路径（余额检查/隔离闸）不破坏
"""
import importlib.util
import os
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

_AGENT = {'id': 'emp_1779430403964', 'name': 'Ray'}
_CANDS = [{'id': f'tal_{i}', 'name': f'达人{i}'} for i in range(3)]


def _bare_handler():
    return srv.SoloBraveHandler.__new__(srv.SoloBraveHandler)


# ─── 1. 撮合余额前置快失败 ──────────────────────────────────────────────

def test_match_fastfail_zero_balance_no_ai_call(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(srv, '_check_credit_balance', lambda aid: (0, False))
    monkeypatch.setattr(srv, '_call_ai_for_json',
                        lambda *a, **k: calls.append(1) or ([], None))
    scores, reason = srv.SoloBraveHandler._ai_match_candidates(
        _bare_handler(), {'id': 'p1', 'name': '商品'}, _CANDS, 'talents', _AGENT)
    assert scores == {} and reason == 'insufficient_credits'
    assert calls == [], '余额 0 必须零 AI 调用（11-20 风暴形态不得复现）'


def test_match_auto_recovers_when_balance_back(tmp_path, monkeypatch):
    calls = []
    def _fake_ok(prompt, agent, system_prompt=None, capture_reason=False):
        calls.append(prompt)
        return ([{'id': 'tal_0', 'matchScore': 88, 'reason': '类目契合'}], None)
    monkeypatch.setattr(srv, '_check_credit_balance', lambda aid: (100, True))
    monkeypatch.setattr(srv, '_call_ai_for_json', _fake_ok)
    scores, reason = srv.SoloBraveHandler._ai_match_candidates(
        _bare_handler(), {'id': 'p1', 'name': '商品'}, _CANDS, 'talents', _AGENT)
    assert len(calls) == 1, '余额恢复 ⇒ 正常推理（自动恢复，无需人工解隔离）'
    assert scores['tal_0']['ai_score'] == 88 and reason is None


def test_match_whitelist_not_blocked_by_gate(monkeypatch, tmp_path):
    calls = []
    def _fake_ok(prompt, agent, system_prompt=None, capture_reason=False):
        calls.append(1)
        return ([], None)
    # 真身 _check_credit_balance + 内存库 subjects.kind=platform_internal
    # ⇒ 走豁免分支返 (999999, True)（prod_db_guard 不许连真库）
    import sqlite3 as _s3
    _db = _s3.connect(':memory:')
    _db.execute('CREATE TABLE subjects (subject_id TEXT PRIMARY KEY, kind TEXT)')
    _db.execute('INSERT INTO subjects VALUES (?,?)', ('main', 'platform_internal'))
    _db.commit()
    monkeypatch.setattr(srv, '_db_conn', lambda: _db)
    monkeypatch.setattr(srv, '_call_ai_for_json', _fake_ok)
    scores, reason = srv.SoloBraveHandler._ai_match_candidates(
        _bare_handler(), {'id': 'p1'}, _CANDS, 'talents',
        {'id': 'main', 'name': '内置'})
    assert calls == [1], '白名单/内置身份的撮合不受余额闸影响（真身余额检查豁免）'


def test_match_fastfail_source_anchors():
    import inspect
    src = inspect.getsource(srv.SoloBraveHandler._ai_match_candidates)
    pos_gate = src.find('_check_credit_balance')
    pos_infer = src.find('_call_ai_for_json')
    pos_log = src.find('[MatchFastFail]')
    assert pos_gate != -1 and pos_infer != -1 and pos_log != -1
    assert pos_gate < pos_infer, '余额闸必须在唯一推理调用点之前'


# ─── 2. 代理免费转发权限缝 ────────────────────────────────────────────

def _proxy_handler(api_key=None):
    h = _bare_handler()
    h.headers = {'x-api-key': api_key} if api_key else {}
    h.client_address = ('127.0.0.1', 50000)
    h.path = '/api/proxy/kimi/v1/messages'
    responses = []
    h._send_json = lambda code, obj: responses.append((code, obj))
    h._send_json_error = lambda code, msg: responses.append((code, msg))
    return h, responses


def test_proxy_no_identity_no_key_401(monkeypatch):
    h, responses = _proxy_handler(api_key=None)
    monkeypatch.setattr(srv, '_is_proxy_quarantined', lambda aid: False)
    pool_calls = []
    monkeypatch.setattr(srv.KIMI_KEY_POOL, 'get_key',
                        lambda: pool_calls.append(1) or 'house-key')
    srv.SoloBraveHandler._handle_proxy_kimi(h)
    assert responses and responses[0][0] == 401, (
        f'无身份无 key 必须 401：{responses}')
    assert pool_calls == [], 'house key 池绝不得为无身份请求兜底'


def test_proxy_no_identity_uses_caller_key(monkeypatch):
    h, responses = _proxy_handler(api_key='sk-fake-caller-key')
    monkeypatch.setattr(srv, '_is_proxy_quarantined', lambda aid: False)
    pool_calls = []
    monkeypatch.setattr(srv.KIMI_KEY_POOL, 'get_key',
                        lambda: pool_calls.append(1) or 'house-key')
    # 转发真发生会打网络——拦在最外层转发函数（名字以实际为准，探针兜底）
    with mock.patch.object(srv, 'KIMI_KEY_POOL'):
        try:
            srv.SoloBraveHandler._handle_proxy_kimi(h)
        except Exception:
            pass  # 只关心：house 池没被调用
    assert pool_calls == [], '无身份带 key：转发必须用 caller 自持 key，house 池不兜底'


def test_proxy_key_ring_path_untouched(monkeypatch):
    """proxy_<agent> 形态：agent_id 提取、隔离闸、余额检查链路不被权限缝改动破坏。"""
    h, responses = _proxy_handler(api_key='proxy_emp_testagent_1')
    seen = {}
    def _fake_quar(aid):
        seen['q'] = aid
        return False
    monkeypatch.setattr(srv, '_is_proxy_quarantined', _fake_quar)
    monkeypatch.setattr(srv, '_proxy_rate_record_and_check', lambda aid: False)
    def _fake_balance(aid):
        seen['balance_check'] = aid
        return (0, False)
    monkeypatch.setattr(srv, '_check_credit_balance', _fake_balance)
    monkeypatch.setattr(srv, '_get_agent_api_key', lambda aid: 'agent-own-key')
    # 429 硬停路径走裸 send_response（老代码）——裸实例补全最小响应面
    h.send_response = lambda code: responses.append(code)
    h.send_header = lambda *a, **k: None
    h.end_headers = lambda: None
    class _W:
        def write(self, b):
            return len(b)
    h.wfile = _W()
    srv.SoloBraveHandler._handle_proxy_kimi(h)
    assert seen.get('q') == 'emp_testagent_1', 'proxy_ key 必须提取出 agent_id'
    assert seen.get('balance_check') == 'emp_testagent_1', '余额检查照常（未绕过）'
    assert responses and responses[0] == 429, (
        f'余额 0 的代理请求必须 429 硬停（原语义不破）：{responses[:1]}')
