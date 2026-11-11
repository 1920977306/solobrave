#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""库管家一键上岗（组织第一批 2026-11-11）—— 泛化 setup_xiaotuo.py 到 maintain 型岗位。

四处落点（全部幂等，patch 前自动备份）：
  1. openclaw secret store：MODEL_APIKEY_KIMI_PROXY_<NAME_UPPER> = proxy_<agent_id>
  2. openclaw.json：provider kimi_proxy_<name>（照抄小拓形状）+ agents.entries.<agent_id>
  3. workspace 目录 + 初始 SOUL.md 占位（职责链 apply 后覆盖为正式人格）+ 体检工具软链
  4. data/agents.json：员工记录（group=资产线 / is_ai / role=资产）

用法：
  python3 setup_keeper.py --agent-id emp_talent_keeper --name 达人库管家 \
      --slug talent-keeper --group 资产线 --role 资产
  python3 setup_keeper.py --verify --agent-id emp_talent_keeper --name 达人库管家 \
      --slug talent-keeper
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPENCLAW_CONFIG = Path.home() / '.openclaw' / 'openclaw.json'
BASE = Path.home() / '.openclaw'
AGENTS_JSON = ROOT / 'data' / 'agents.json'

# 每岗个性化：工牌底色 + 头像序号（与小拓 #2E8B57/12 区分）
KEEPER_STYLE = {
    'emp_talent_keeper': {'bg': '#4A6FA5', 'avatar': 21},
    'emp_goods_keeper': {'bg': '#8B6B2E', 'avatar': 33},
}


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _oc_get(key):
    p = _run(['openclaw', 'config', 'get', key])
    return p.stdout.strip() if p.returncode == 0 else ''


def ensure_secret(slug, agent_id):
    secret_id = f'MODEL_APIKEY_KIMI_PROXY_{slug.upper().replace("-", "_")}'
    token = f'proxy_{agent_id}'
    tmp = BASE / f'.tmp-{secret_id}.txt'
    tmp.write_text(token, encoding='utf-8')
    try:
        p = _run(['openclaw', 'secrets', 'store', 'set', secret_id,
                  '--kind', 'secret', '--value-file', str(tmp)])
        print(f'  secret {secret_id}: rc={p.returncode} {p.stdout.strip() or p.stderr.strip()}')
    finally:
        tmp.unlink(missing_ok=True)
    _run(['openclaw', 'secrets', 'reload'])
    return secret_id


def ensure_openclaw_entry(slug, agent_id, name):
    workspace = BASE / f'workspace-{slug}'
    agent_dir = BASE / 'agents' / agent_id / 'agent'
    workspace.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (workspace / 'SOUL.md').write_text(
        f'# {name} —— 初始人格占位\n\n'
        '本文件由 setup_keeper.py 生成。**正式人格以职责链为准**：'
        'PUT duties（maintain 型）后 build_duty_system_prompt 会自动重组装并覆盖本文件。\n',
        encoding='utf-8')
    # 体检工具放进 workspace（agent turn 的工作目录在 workspace 下）
    tools_dir = workspace / 'tools'
    tools_dir.mkdir(exist_ok=True)
    src = ROOT / 'tools' / 'keeper_ops.py'
    link = tools_dir / 'keeper_ops.py'
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(src)

    bak = OPENCLAW_CONFIG.parent / f'openclaw.json.bak-keeper-{datetime.now():%Y%m%d_%H%M%S}'
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    print(f'  Backup → {bak.name}')

    provider = f'kimi_proxy_{slug.replace("-", "_")}'
    secret_id = f'MODEL_APIKEY_KIMI_PROXY_{slug.upper().replace("-", "_")}'
    provider_block = {
        'baseUrl': 'http://localhost:8080/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': {'source': 'store', 'provider': 'default', 'id': secret_id},
        'models': [{'id': 'k3', 'name': 'Kimi K3', 'reasoning': True,
                    'input': ['text', 'image', 'video'],
                    'cost': {'input': 0, 'output': 0}}],
    }
    entry = {
        'name': name,
        'workspace': str(workspace),
        'agentDir': str(agent_dir),
        'identity': {'name': name},
        'model': {'primary': f'{provider}/k3', 'fallbacks': ['minimax/MiniMax-M3']},
        'tools': {'deny': ['process', 'fs', 'apply_patch', 'automations',
                           'gateway', 'plugins'],
                  'alsoAllow': ['message']},
        'skills': ['session-logs'],
    }
    patch = {'models': {'providers': {provider: provider_block}},
             'agents': {'entries': {agent_id: entry}}}
    p = _run(['openclaw', 'config', 'patch', '--stdin'], input=json.dumps(patch))
    if p.returncode != 0:
        print('PATCH FAILED:', p.stderr.strip())
        sys.exit(1)
    print(f'  openclaw entry {agent_id} + provider {provider} 已写入')


def ensure_agents_json(agent_id, name, group, role):
    agents = json.loads(AGENTS_JSON.read_text(encoding='utf-8'))
    if not isinstance(agents, list):
        agents = []
    bak = AGENTS_JSON.with_name(f'agents.json.bak-keeper-{datetime.now():%Y%m%d_%H%M%S}')
    bak.write_text(json.dumps(agents, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  Backup → {bak.name}')

    style = KEEPER_STYLE.get(agent_id, {'bg': '#6B5B95', 'avatar': 44})
    existing = next((a for a in agents if isinstance(a, dict)
                     and a.get('id') == agent_id), None)
    if existing:
        print(f'  agents.json 已有 {agent_id}，保留运行期字段不动')
        return
    agents.append({
        'id': agent_id,
        'name': name,
        'role': role,
        'bg': style['bg'],
        'avatar': style['avatar'],
        'status': 'online',
        'msg': '',
        'archived': False,
        'permission': 'dev',
        'visibility': 'creator',
        'createdBy': 'user_7acb72ff',
        'createdAt': datetime.now().isoformat(),
        'connectionType': '',
        'apiProvider': 'kimi',
        'apiModel': '',
        'openclawAgent': agent_id,
        'openclawModel': '',
        'openclawName': agent_id.replace('emp_', ''),
        'aiProvider': 'kimi',
        'department': '',
        'customEndpoint': '',
        'group': group,
        'idDoc': '',
        'soulDoc': '',
        'systemPrompt': '',
        'createdByName': '管理员',
        'pinned': False,
        'toolsDoc': '',
        'userDoc': '',
        'badge': '',
        'is_ai': True,
        'tenant_id': 't_default',
    })
    AGENTS_JSON.write_text(json.dumps(agents, ensure_ascii=False, indent=1),
                           encoding='utf-8')
    print(f'  agents.json 新增 {agent_id}（{name}）')


def verify(agent_id, slug, name):
    print('\n=== verify ===')
    print(f'  provider primary = {_oc_get(f"agents.entries.{agent_id}.model.primary") or "(缺)"}')
    print(f'  workspace        = {_oc_get(f"agents.entries.{agent_id}.workspace") or "(缺)"}')
    agents = json.loads(AGENTS_JSON.read_text(encoding='utf-8'))
    rec = next((a for a in agents if isinstance(a, dict) and a.get('id') == agent_id), None)
    print(f'  agents.json      = {"✅" if rec else "❌ 缺记录"}'
          + (f' (name={rec.get("name")}, group={rec.get("group")})' if rec else ''))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent-id', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--slug', required=True, help='workspace 短名，如 talent-keeper')
    ap.add_argument('--group', default='资产线')
    ap.add_argument('--role', default='资产')
    ap.add_argument('--verify', action='store_true')
    args = ap.parse_args()
    if args.verify:
        verify(args.agent_id, args.slug, args.name)
        return
    print(f'① secret store … {args.agent_id}')
    ensure_secret(args.slug, args.agent_id)
    print('② openclaw entry + provider …')
    ensure_openclaw_entry(args.slug, args.agent_id, args.name)
    print('③ data/agents.json …')
    ensure_agents_json(args.agent_id, args.name, args.group, args.role)
    print('\n★ 收尾：')
    print('  openclaw daemon restart   # 让新 entry/provider 进网关路由')
    print('  PUT duties（maintain 型）→ _apply_duty_to_agent 落职责链')
    print('  launchd 排班 keeper_daily.py --agent ' + args.agent_id)
    verify(args.agent_id, args.slug, args.name)


if __name__ == '__main__':
    main()
