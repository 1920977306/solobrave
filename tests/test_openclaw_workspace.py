# -*- coding: utf-8 -*-
"""OpenClaw workspace 路径解析的**唯一真源**守卫（2026-10-26）。

★ 背景（真实事故，不是设想）：
  服务端三处都按 `agents.json` 的 `openclawName` 拼 workspace 目录 ⇒ **声明端**；
  而 OpenClaw 运行时真正用的是 `openclaw.json` 的
  `agents.entries[agent_id].workspace` ⇒ **使用端**。
  木木的 `openclawName='main'`，实际跑在 `workspace-emp_1787550091124_1048`
  ⇒ 读写全错位，三条后果：
    ① 木木在飞书里拿不到自己的完整设定（缺「数据源强制约束」「工具使用铁律」，
       且那个 workspace 里**没有 TOOLS.md**）⇒ 只会客套，不会查数据/调工具
    ② 服务端把木木的 soul 解析成了**贾维斯自己的** SOUL.md ⇒ 木木被配上别人的灵魂
    ③ 一旦写木木的文档 ⇒ **直接覆盖贾维斯的 SOUL.md**

★ 本文件锁住：
  A. 路径必须按 **entry.workspace**（使用端）解析，**不是** openclawName（声明端）
  B. 写 workspace 前要做**归属校验** —— 目录属于别的 agent 就拒绝写
  C. 真事故本身作为回归用例（木木解析到自己的目录、soul 不是别人的）
"""
import json
import os
import sys
import importlib.util
from pathlib import Path

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ★ solobrave-server.py 文件名带连字符 ⇒ 不能直接 import
#   （沿用 tests/test_talent_full_sync.py 的加载模式）
_SERVER_PATH = Path(__file__).parent.parent / 'solobrave-server.py'
_spec = importlib.util.spec_from_file_location('solobrave_server', _SERVER_PATH)
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)
sys.modules['solobrave_server'] = srv


def _write_fake_openclaw(tmp_path, entries):
    """造一份假 openclaw.json（entry.workspace = 使用端真值）。"""
    cfg = {'agents': {'entries': entries}}
    p = tmp_path / 'openclaw.json'
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return str(p)


def _use_fake_openclaw(monkeypatch, tmp_path, entries):
    """把 server 指到假 openclaw.json，并清掉 mtime 缓存。"""
    path = _write_fake_openclaw(tmp_path, entries)
    monkeypatch.setattr(srv, 'OPENCLAW_CONFIG_PATH', path)
    srv._OC_WS_CACHE['mtime'] = 0.0
    srv._OC_WS_CACHE['entries'] = {}
    return path


# ─────────────────── A. 路径按「使用端」解析 ───────────────────

def test_workspace_uses_entry_not_openclaw_name(monkeypatch, tmp_path):
    """★ 核心：entry.workspace 与 openclawName 不一致时，必须听 entry 的。

    这是木木事故的根因：`openclawName='main'`（声明），
    实际跑在 `workspace-emp_...`（使用端）。
    """
    _use_fake_openclaw(monkeypatch, tmp_path, {
        'emp_mumu': {'workspace': '/tmp/ws-emp-mumu', 'name': 'emp_mumu'},
    })
    agent = {'id': 'emp_mumu', 'openclawName': 'main'}   # 声明端是 main

    ws = srv._resolve_agent_workspace('emp_mumu', agent)
    assert ws == os.path.abspath('/tmp/ws-emp-mumu'), ws
    assert not ws.endswith('workspace-main'), \
        '又退回声明端拼法了 —— 木木会读写到别人的 workspace'


def test_workspace_falls_back_to_openclaw_name_when_no_entry(monkeypatch, tmp_path):
    """没有 entry（还没被 OpenClaw 接管的新员工）⇒ 兜底用 openclawName。"""
    _use_fake_openclaw(monkeypatch, tmp_path, {})
    agent = {'id': 'emp_new', 'openclawName': 'emp_new'}
    ws = srv._resolve_agent_workspace('emp_new', agent)
    assert ws.endswith('workspace-emp_new'), ws


def test_workspace_owner_lookup(monkeypatch, tmp_path):
    """`_workspace_owner_agent` 能按 entry 反查目录归属。"""
    _use_fake_openclaw(monkeypatch, tmp_path, {
        'emp_a': {'workspace': '/tmp/ws-a'},
        'emp_b': {'workspace': '/tmp/ws-b'},
    })
    assert srv._workspace_owner_agent('/tmp/ws-a') == 'emp_a'
    assert srv._workspace_owner_agent('/tmp/ws-b') == 'emp_b'
    assert srv._workspace_owner_agent('/tmp/ws-nobody') == ''


# ─────────────────── B. 写前归属校验 ───────────────────

def test_write_conflict_detects_other_owner(monkeypatch, tmp_path):
    """★ 目录属于**别的** agent ⇒ 必须报冲突（防覆盖别人的 SOUL.md）。"""
    _use_fake_openclaw(monkeypatch, tmp_path, {
        'emp_other': {'workspace': '/tmp/ws-other'},
    })
    assert srv._workspace_write_conflict('emp_mumu', '/tmp/ws-other') == 'emp_other'


def test_write_conflict_allows_own_workspace(monkeypatch, tmp_path):
    """自己的目录不报冲突（否则正常写入被拦死）。"""
    _use_fake_openclaw(monkeypatch, tmp_path, {
        'emp_mumu': {'workspace': '/tmp/ws-mumu'},
    })
    assert srv._workspace_write_conflict('emp_mumu', '/tmp/ws-mumu') == ''


def test_write_conflict_ignores_unowned_dir(monkeypatch, tmp_path):
    """没人认领的目录（新建员工）⇒ 不冲突。"""
    _use_fake_openclaw(monkeypatch, tmp_path, {})
    assert srv._workspace_write_conflict('emp_new', '/tmp/ws-brand-new') == ''


# ─────────────────── C. 真事故回归 ───────────────────

def test_mumu_soul_is_not_another_agents_soul(monkeypatch, tmp_path):
    """★ 复现木木事故：它读到的 soul 必须是**自己**那份，不是别人的。

    修好前：服务端按 openclawName='main' 拼 ⇒ 读到 workspace-main/SOUL.md
    （别人的灵魂）⇒ 木木被配上别人的心智。
    """
    ws_mumu = tmp_path / 'workspace-emp-mumu'
    ws_other = tmp_path / 'workspace-main'
    ws_mumu.mkdir()
    ws_other.mkdir()
    (ws_mumu / 'SOUL.md').write_text('# 你是谁\n你是一名商务，负责商务拓展。', encoding='utf-8')
    (ws_other / 'SOUL.md').write_text(
        '# SOUL.md 抖音全域数据分析师核心心智与底层逻辑\n你是全域数据分析师。', encoding='utf-8')

    _use_fake_openclaw(monkeypatch, tmp_path, {
        'emp_mumu': {'workspace': str(ws_mumu)},
        'emp_main': {'workspace': str(ws_other)},
    })
    agent = {'id': 'emp_mumu', 'openclawName': 'main',
             'systemPrompt': 'agents.json 里的兜底'}

    soul = srv._resolve_agent_soul(agent)
    assert '商务' in soul, soul
    assert '全域数据分析师' not in soul, \
        '又读到别人的 soul 了 —— 木木被配上别人的心智'


def test_real_openclaw_config_mumu_not_pointing_at_main():
    """★ 真环境回归：木木必须解析到**自己的** workspace，不能是 workspace-main。

    读真实 `~/.openclaw/openclaw.json`（生产上就存在）⇒
    断言服务端解析结果 == entry.workspace，且 ≠ `workspace-main`。
    """
    real = os.path.expanduser('~/.openclaw/openclaw.json')
    if not os.path.exists(real):
        pytest.skip('本机没有 openclaw.json（契约层已由假配置覆盖）')
    with open(real, encoding='utf-8') as f:
        cfg = json.load(f)
    entries = ((cfg.get('agents') or {}).get('entries') or {})
    target = None
    for aid, entry in entries.items():
        ws = (entry.get('workspace') or '')
        if '1787550091124_1048' in str(ws) or '1787550091124_1048' in str(aid):
            target = (aid, os.path.abspath(os.path.expanduser(ws)))
            break
    if not target:
        pytest.skip('openclaw.json 里没有木木的 entry（环境已变）')
    aid, expected = target
    got = srv._resolve_agent_workspace(aid, {'id': aid, 'openclawName': 'main'})
    assert got == expected, f'{aid}: 解析成 {got}，实际应为 {expected}'
    assert not got.endswith('workspace-main'), \
        '木木又被指到 workspace-main（= 贾维斯自己的目录）了'


def test_no_legacy_openclaw_name_path_building():
    """静态守卫：代码里不许再出现按 `openclawName` 拼路径的老写法。

    ⚠️ 这条是**补充**闸门（防回退）；真正的行为断言在上面的用例里
       —— 变异掉下面的实现时，行为用例会红。
    """
    src = open(os.path.join(ROOT, 'solobrave-server.py'), encoding='utf-8').read()
    assert "'~/.openclaw/workspace-' + (openclaw_name or agent_id)" not in src, \
        '又出现按 openclawName 拼 workspace 的老写法'
    assert "expanduser('~/.openclaw/workspace-' + openclaw_name)" not in src, \
        '又出现按 openclawName 拼 workspace 的老写法'
