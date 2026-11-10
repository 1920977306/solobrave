# -*- coding: utf-8 -*-
"""③AI 互 @ 通道守卫（2026-11-10 派单）：身份闸 / 深度闸 / 出口义务 / 三归属。

测试矩阵：
1. replyToken 签发/校验：正确通过；错群 / 错人 / 过期 / 篡改一律 False
2. _msg_ownership 三态 + 边界（mentions 空列表 → direct；None → direct）
3. _group_append_agent_reply 落盘：mentionDepth=parent+1、mentions 只留群成员且排除自己、
   provenance=server
4. 深度闸：reply depth < 3 链式触发下一跳；= 3 只渲染不触发（monkeypatch 记录调用）
5. 出口义务：群创建者收到 group_mention 通知
6. 身份闸 AST 守卫：_handle_post_group_history 源码里 senderType==agent 分支必须
   调 _verify_group_reply_token（防将来 refactor 把闸拆没）

数据全落 tmp（同 test_emp_duties 脚手架），绝不写真 prod 路径。
运行：/usr/bin/python3 -m pytest tests/test_group_mention.py -v
"""
import ast
import hashlib
import hmac as hmac_mod
import importlib.util
import json
import os
import time

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


GROUP_ID = 'grp_test_mention'
AGENT_A = 'emp_agent_a'
AGENT_B = 'emp_agent_b'
OWNER = 'user_owner_1'


def _mk_group(group_id=GROUP_ID, lead=AGENT_A, members=None, created_by=OWNER):
    return {
        'id': group_id,
        'name': '测试项目组',
        'leadAgentId': lead,
        'members': members if members is not None else [
            {'id': AGENT_A, 'name': 'AI甲'}, {'id': AGENT_B, 'name': 'AI乙'}],
        'createdBy': created_by,
    }


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
    monkeypatch.setattr(srv, 'INTERNAL_SECRET_FILE', str(tmp_path / 'certs' / 'internal_secret'))
    srv._ensure_data_dir()
    srv.init_db()
    return srv


def _save_group(mod, group):
    groups = mod._load_groups()
    groups = [g for g in groups if g.get('id') != group['id']]
    groups.append(group)
    with open(mod.GROUPS_FILE, 'w', encoding='utf-8') as f:
        json.dump(groups, f, ensure_ascii=False, indent=2)


# ── 1. replyToken 签发/校验 ──────────────────────────────

def test_reply_token_roundtrip(mod):
    tok = mod._issue_group_reply_token(GROUP_ID, AGENT_A)
    assert mod._verify_group_reply_token(tok, GROUP_ID, AGENT_A) is True


def test_reply_token_wrong_group(mod):
    tok = mod._issue_group_reply_token(GROUP_ID, AGENT_A)
    assert mod._verify_group_reply_token(tok, 'grp_other', AGENT_A) is False


def test_reply_token_wrong_agent(mod):
    tok = mod._issue_group_reply_token(GROUP_ID, AGENT_A)
    assert mod._verify_group_reply_token(tok, GROUP_ID, AGENT_B) is False


def test_reply_token_expired(mod):
    past = int(time.time()) - 10
    payload = f'{GROUP_ID}|{AGENT_A}|{past}'
    sig = hmac_mod.new(mod._get_internal_secret(), payload.encode(),
                       hashlib.sha256).hexdigest()[:32]
    tok = f'{payload}|{sig}'
    assert mod._verify_group_reply_token(tok, GROUP_ID, AGENT_A) is False


def test_reply_token_tampered(mod):
    tok = mod._issue_group_reply_token(GROUP_ID, AGENT_A)
    g, a, exp, sig = tok.split('|')
    bad = f'{g}|{AGENT_B}|{exp}|{sig}'  # 换人不换签名
    assert mod._verify_group_reply_token(bad, GROUP_ID, AGENT_B) is False


def test_reply_token_garbage(mod):
    assert mod._verify_group_reply_token('', GROUP_ID, AGENT_A) is False
    assert mod._verify_group_reply_token(None, GROUP_ID, AGENT_A) is False
    assert mod._verify_group_reply_token('a|b|c', GROUP_ID, AGENT_A) is False


# ── 2. 三归属纯函数 ─────────────────────────────────────

def test_ownership_three_states():
    assert srv._msg_ownership({'type': 'system', 'content': 'x 加入群组'}) == 'system'
    assert srv._msg_ownership(
        {'senderType': 'agent', 'mentions': [AGENT_B]}) == 'agent-to-agent'
    assert srv._msg_ownership({'senderType': 'agent'}) == 'direct'
    assert srv._msg_ownership({'senderType': 'user', 'mentions': [AGENT_B]}) == 'direct'


def test_ownership_edge_cases():
    # mentions 空列表 ≠ 有 mentions → direct（恒真闸门防线）
    assert srv._msg_ownership({'senderType': 'agent', 'mentions': []}) == 'direct'
    assert srv._msg_ownership(None) == 'direct'
    assert srv._msg_ownership('not-a-dict') == 'direct'
    assert srv._msg_ownership({}) == 'direct'


# ── 3+4. 落盘 + 深度闸 ──────────────────────────────────

def test_append_reply_persists_and_filters_mentions(mod, monkeypatch):
    _save_group(mod, _mk_group())
    recorded = []
    monkeypatch.setattr(mod, '_trigger_group_agent_turn',
                        lambda gid, aid, src, depth: recorded.append((aid, depth)))
    mod._group_append_agent_reply(
        GROUP_ID, AGENT_A,
        f'好的 @not_a_member @emp_agent_b 看一下', depth=0)
    msgs = mod._load_chat(f'group_{GROUP_ID}')
    assert len(msgs) == 1
    m = msgs[0]
    assert m['mentionDepth'] == 1          # parent 0 + 1
    assert m['provenance'] == 'server'
    assert m['senderType'] == 'agent'
    # 非成员被过滤、自己 @ 自己不触发；群成员 emp_agent_b 保留
    assert m['mentions'] == [AGENT_B]
    # depth 0 → reply depth 1 < 3 → 链式触发被 @ 的 B，depth 传 1
    assert recorded == [(AGENT_B, 1)]


def test_self_mention_not_triggered(mod, monkeypatch):
    _save_group(mod, _mk_group())
    recorded = []
    monkeypatch.setattr(mod, '_trigger_group_agent_turn',
                        lambda gid, aid, src, depth: recorded.append(aid))
    mod._group_append_agent_reply(GROUP_ID, AGENT_A, '@emp_agent_a 自言自语', depth=0)
    msgs = mod._load_chat(f'group_{GROUP_ID}')
    assert msgs[0]['mentions'] == []
    assert recorded == []


def test_depth_gate_blocks_fourth_hop(mod, monkeypatch):
    _save_group(mod, _mk_group())
    recorded = []
    monkeypatch.setattr(mod, '_trigger_group_agent_turn',
                        lambda gid, aid, src, depth: recorded.append((aid, depth)))
    # parent depth 2 → reply depth 3 = _MENTION_MAX_DEPTH → 只渲染不触发
    mod._group_append_agent_reply(GROUP_ID, AGENT_A, '@emp_agent_b 接力', depth=2)
    msgs = mod._load_chat(f'group_{GROUP_ID}')
    assert msgs[0]['mentionDepth'] == 3
    assert msgs[0]['mentions'] == [AGENT_B]   # 仍渲染（mentions 保留在消息里）
    assert recorded == []                      # 但不触发


def test_depth_gate_allows_third_hop(mod, monkeypatch):
    _save_group(mod, _mk_group())
    recorded = []
    monkeypatch.setattr(mod, '_trigger_group_agent_turn',
                        lambda gid, aid, src, depth: recorded.append((aid, depth)))
    # parent depth 1 → reply depth 2 < 3 → 触发第三跳
    mod._group_append_agent_reply(GROUP_ID, AGENT_A, '@emp_agent_b 接力', depth=1)
    assert recorded == [(AGENT_B, 2)]


# ── 5. 出口义务 ─────────────────────────────────────────

def test_exit_summary_notifies_group_owner(mod, monkeypatch):
    _save_group(mod, _mk_group(created_by=OWNER))
    monkeypatch.setattr(mod, '_trigger_group_agent_turn', lambda *a, **kw: None)
    mod._group_append_agent_reply(GROUP_ID, AGENT_A, '@emp_agent_b 请看', depth=0)
    conn = mod._db_conn()
    try:
        rows = conn.execute(
            "SELECT user_id, type, title, content FROM notifications "
            "WHERE user_id = ?", (OWNER,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0][1] == 'group_mention'
    assert 'AI 协同' in rows[0][2]
    assert '@emp_agent_b' in rows[0][3]
    assert 'emp_agent_a' in rows[0][3] or 'AI甲' in rows[0][3]


# ── 6. 身份闸 AST 守卫 ──────────────────────────────────

def test_identity_gate_ast_guard():
    """_handle_post_group_history 里 senderType==agent 分支必须调
    _verify_group_reply_token —— 防 refactor 把身份闸拆没（恒真闸门防线）。"""
    src = open(os.path.join(ROOT, 'solobrave-server.py'),
               encoding='utf-8').read()
    tree = ast.parse(src)
    fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and \
                node.name == '_handle_post_group_history':
            fn = node
            break
    assert fn is not None, '_handle_post_group_history 不存在'
    calls = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call):
            if isinstance(n.func, ast.Name):
                calls.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                calls.add(n.func.attr)
    assert '_verify_group_reply_token' in calls, \
        '身份闸被拆除：_handle_post_group_history 不再校验 replyToken'
    assert '_send_json_error' in calls
