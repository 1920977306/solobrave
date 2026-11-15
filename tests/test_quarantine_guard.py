# -*- coding: utf-8 -*-
"""
止血守卫测试（P1-⑤ dq_2e3dcf47d76c，老大 2026-11-15 14:12 拍「止血」）。

守卫语义（solobrave-server.py `_record_credit_usage`）：
  QUARANTINE_LEAK_AGENTS 名单内的 agent，且 session_id 为空 的 consumption 行：
    - 行照写，quarantined=1（指纹保留，协调边硬条件①）
    - 余额不扣减
    - credit_accounts.quarantined_credits 累计
  其他情况（名单内但有 session / 非名单 agent / bypass kind）一律原行为。

同交付：scripts/unquarantine_credits.py（dry-run 默认，--apply 冲回）。

策略同 test_credits_checkin_grant.py：AST 抽取 + 内存 sqlite，避开模块导入副作用。
"""
import ast
import os
import sqlite3

import pytest

_SERVER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'solobrave-server.py'
)
with open(_SERVER_PATH, 'r', encoding='utf-8') as f:
    _server_src = f.read()
_server_tree = ast.parse(_server_src)


def _extract_func(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(_server_src, node)
    raise KeyError(f'function {name} not found')


_NS = {}
_src = '\n\n'.join([
    'import math\nimport logging\nlogger = logging.getLogger(__name__)',
    'CREDITS_PER_TOKENS = 1000',
    "QUARANTINE_LEAK_AGENTS = {'emp_1780199176680'}",
    _extract_func(_server_tree, '_ensure_credit_account'),
    _extract_func(_server_tree, '_record_credit_usage'),
])
exec(compile(_src, '<quarantine-ns>', 'exec'), _NS)

LEAK_AGENT = 'emp_1780199176680'
OTHER_AGENT = 'emp_someone_else'


@pytest.fixture()
def conn():
    c = sqlite3.connect(':memory:')
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE credit_accounts (
        agent_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0,
        total_consumed INTEGER DEFAULT 0, total_granted INTEGER DEFAULT 0,
        quota_limit INTEGER DEFAULT 0, updated_at TEXT DEFAULT '',
        quarantined_credits INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE credit_usage_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
        input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
        cache_read_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
        credits_used INTEGER DEFAULT 0, session_id TEXT,
        created_at TEXT DEFAULT (datetime('now','localtime')),
        reason TEXT DEFAULT 'consumption', delta INTEGER DEFAULT 0,
        note TEXT DEFAULT '', quarantined INTEGER DEFAULT 0)''')
    c.execute('''CREATE TABLE subjects (
        subject_id TEXT PRIMARY KEY, kind TEXT DEFAULT 'agent_persona')''')
    for aid in (LEAK_AGENT, OTHER_AGENT, 'emp_bypass'):
        c.execute('INSERT INTO credit_accounts (agent_id, balance) VALUES (?, 1000)', (aid,))
        c.execute('INSERT INTO subjects (subject_id, kind) VALUES (?, ?)',
                  (aid, 'super_admin' if aid == 'emp_bypass' else 'agent_persona'))
    c.commit()
    yield c
    c.close()


def _record(c, agent, tokens=2000, session=''):
    return _NS['_record_credit_usage'](c, agent, tokens, 0, 0, session_id=session)


def test_leak_agent_empty_session_quarantined(conn):
    used = _record(conn, LEAK_AGENT, session='')
    row = conn.execute('SELECT * FROM credit_usage_log').fetchone()
    acc = conn.execute('SELECT * FROM credit_accounts WHERE agent_id=?', (LEAK_AGENT,)).fetchone()
    assert used == 2                                  # 2000 tokens → 2 credits
    assert row['quarantined'] == 1                    # 硬条件①：行照写 + 标记
    assert acc['balance'] == 1000                   # 余额冻结
    assert acc['quarantined_credits'] == 2          # 冻结累计


def test_leak_agent_with_session_normal(conn):
    _record(conn, LEAK_AGENT, session='sess-1')
    row = conn.execute('SELECT * FROM credit_usage_log').fetchone()
    acc = conn.execute('SELECT * FROM credit_accounts WHERE agent_id=?', (LEAK_AGENT,)).fetchone()
    assert row['quarantined'] == 0
    assert acc['balance'] == 998                    # 带 session 正常扣减
    assert acc['quarantined_credits'] == 0


def test_other_agent_empty_session_normal(conn):
    _record(conn, OTHER_AGENT, session='')
    row = conn.execute('SELECT * FROM credit_usage_log').fetchone()
    acc = conn.execute('SELECT * FROM credit_accounts WHERE agent_id=?', (OTHER_AGENT,)).fetchone()
    assert row['quarantined'] == 0                    # 范围限定：名单外不受守卫影响
    assert acc['balance'] == 998
    assert acc['quarantined_credits'] == 0


def test_bypass_kind_untouched(conn):
    _record(conn, 'emp_bypass', session='')
    row = conn.execute('SELECT * FROM credit_usage_log').fetchone()
    acc = conn.execute('SELECT * FROM credit_accounts WHERE agent_id=?', ('emp_bypass',)).fetchone()
    assert row['quarantined'] == 0                    # bypass 路径不沾染守卫
    assert acc['balance'] == 1000
    assert acc['quarantined_credits'] == 0


def test_quarantine_accumulates_across_rows(conn):
    for _ in range(5):
        _record(conn, LEAK_AGENT, tokens=1000, session='')
    acc = conn.execute('SELECT * FROM credit_accounts WHERE agent_id=?', (LEAK_AGENT,)).fetchone()
    n = conn.execute('SELECT COUNT(*) FROM credit_usage_log WHERE quarantined=1').fetchone()[0]
    assert n == 5 and acc['quarantined_credits'] == 5 and acc['balance'] == 1000
