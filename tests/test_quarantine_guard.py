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


# ── 隔离闸门（2026-11-15 根因单 dq_0f6069d56a0e）────────────────────────────
# _handle_proxy_kimi 对名单内 agent 直接 429 拒转上游。
# 实机依据：Helen 泄漏是网关内孤儿 agentic 循环，错误/超时杀不死，
# 只有掐断出口能止烧（16:22:54 网关重试撞上 429 后循环终止，16:16:38 后
# 零转发）。该测试守的是「闸门存在且位于上游转发之前」这一不变量——
# 真实往返已由 prod 探针验证（HTTP 429 + anthropic 错误格式，零计费）。

def test_proxy_quarantine_gate_exists_and_precedes_upstream():
    import re
    src = _extract_func(_server_tree, '_handle_proxy_kimi')
    # 名单常量与处理函数必须真实存在且引用同一常量
    m = re.search(r'QUARANTINE_LEAK_AGENTS\s*=\s*\{([^}]*)\}', _server_src)
    assert m and LEAK_AGENT in m.group(1), '模块级隔离名单必须含泄漏员工'
    # 闸门分支存在（静态 + 动态共用）
    assert 'rate_limit_error' in src and '隔离拒转' in src
    assert '_is_proxy_quarantined' in src and '_proxy_rate_record_and_check' in src
    # 闸门必须位于上游深管道（记忆召回）之前——429 先行，不许任何上游动作
    assert src.index('QUARANTINE_LEAK_AGENTS') < src.index('recall_with_budget')


# ── 动态隔离（§162 防复发①：形态学熔断，不限于写死名单）─────────────────────
# Helen 僵尸的通用形态 = 无会话登记的高频代理调用。动态限频闸门必须在
# 滑动窗口超阈值时自动隔离且只触发一次；静态名单行为保持不变。

_NS2 = {}
_src2 = '\n\n'.join([
    'import threading',
    'import time',
    '_PROXY_RATE_WINDOW_SEC = 600',
    '_PROXY_RATE_LIMIT = 120',
    '_proxy_rate_lock = threading.Lock()',
    '_proxy_rate_buckets = {}',
    '_dynamic_quarantined = {}',
    "QUARANTINE_LEAK_AGENTS = {'emp_1780199176680'}",
    _extract_func(_server_tree, '_proxy_rate_record_and_check'),
    _extract_func(_server_tree, '_is_proxy_quarantined'),
])
exec(compile(_src2, '<dyn-quarantine-ns>', 'exec'), _NS2)


def test_dynamic_quarantine_triggers_once_over_limit():
    import time as _t
    base = 2000000.0
    agent = 'emp_zombie_test'
    triggered = []
    for i in range(125):                      # > 120 阈值
        now = base + i * 5.0                  # 全部落在 10 分钟窗口内
        real = _t.time
        _t.time = lambda: now
        try:
            if _NS2['_proxy_rate_record_and_check'](agent):
                triggered.append(i)
        finally:
            _t.time = real
    assert len(triggered) == 1, f'自动隔离应只触发一次，实际 {len(triggered)} 次'
    assert agent in _NS2['_dynamic_quarantined']
    assert _NS2['_is_proxy_quarantined'](agent)


def test_dynamic_quarantine_window_pruning_no_false_positive():
    import time as _t
    base = 3000000.0
    agent = 'emp_busy_shift_test'
    # 121 次调用但摊在 30 分钟里（间隔 15s > 窗口内不可能超阈值）：
    # 任何 10 分钟窗口内只有 ~40 次 → 不触发
    for i in range(121):
        now = base + i * 15.0
        real = _t.time
        _t.time = lambda: now
        try:
            hit = _NS2['_proxy_rate_record_and_check'](agent)
        finally:
            _t.time = real
        assert not hit, '分散的正常调用不得触发自动隔离'
    assert agent not in _NS2['_dynamic_quarantined']
    assert not _NS2['_is_proxy_quarantined'](agent)


def test_static_list_still_quarantined_without_rate():
    # 静态名单：一次调用（远低于阈值）也要拒转（数据止血语义不变）
    import time as _t
    real = _t.time
    _t.time = lambda: 4000000.0
    try:
        assert not _NS2['_proxy_rate_record_and_check'](LEAK_AGENT)
    finally:
        _t.time = real
    assert _NS2['_is_proxy_quarantined'](LEAK_AGENT)


def test_empty_agent_id_never_quarantined():
    assert not _NS2['_is_proxy_quarantined']('')
    assert not _NS2['_is_proxy_quarantined'](None)
