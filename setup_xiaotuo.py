#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小拓（达人拓展专员）一键上岗 —— A 简报 2026-11-10 老大拍板本会话代执行。

落点四处（全部幂等，patch 前自动备份）：
  1. openclaw secret store：MODEL_APIKEY_KIMI_PROXY_XIAOTUO = proxy_emp_xiaotuo
     （服务端 /api/proxy/kimi 按 proxy_<agent_id> 提取员工身份 → 走该员工 apiKey 转发 + 积分管控）
  2. openclaw.json：models.providers.kimi_proxy_xiaotuo（形状照抄 kimi_proxy_helen）
     + agents.entries.emp_xiaotuo（workspace=~/.openclaw/workspace-xiaotuo）
  3. workspace 目录 + 初始 SOUL.md（占位，职责链 apply 后会覆盖为正式人格）
  4. data/agents.json：员工记录（id=emp_xiaotuo / name=小拓 / openclawName=xiaotuo / role=商务）

用法：
  python3 setup_xiaotuo.py            # 部署
  python3 setup_xiaotuo.py --verify   # 只校验
"""
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OPENCLAW_CONFIG = Path.home() / '.openclaw' / 'openclaw.json'
BASE = Path.home() / '.openclaw'
WORKSPACE = BASE / 'workspace-xiaotuo'
AGENT_DIR = BASE / 'agents' / 'emp_xiaotuo' / 'agent'
AGENTS_JSON = ROOT / 'data' / 'agents.json'

AGENT_ID = 'emp_xiaotuo'
AGENT_NAME = '小拓'
PROVIDER = 'kimi_proxy_xiaotuo'
SECRET_ID = 'MODEL_APIKEY_KIMI_PROXY_XIAOTUO'
PROXY_TOKEN = f'proxy_{AGENT_ID}'

_SOUL_PLACEHOLDER = """# 小拓（达人拓展专员）—— 初始人格占位

本文件由 setup_xiaotuo.py 生成。**正式人格以职责链为准**：
`PUT /api/agents/emp_xiaotuo/duties` 后，emp_duties → build_duty_system_prompt
会自动重组装并覆盖本文件（验收①：改职责 ⇒ SOUL.md 跟着变）。
"""


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _oc_get(key):
    p = _run(['openclaw', 'config', 'get', key])
    return p.stdout.strip() if p.returncode == 0 else ''


def ensure_secret():
    """proxy token 写 secret store（--value-file 不走命令行，避免进 shell 历史）。"""
    tmp = BASE / f'.tmp-{SECRET_ID}.txt'
    tmp.write_text(PROXY_TOKEN, encoding='utf-8')
    try:
        p = _run(['openclaw', 'secrets', 'store', 'set', SECRET_ID,
                  '--kind', 'secret', '--value-file', str(tmp)])
        print(f'  secret {SECRET_ID}: rc={p.returncode} {p.stdout.strip() or p.stderr.strip()}')
    finally:
        tmp.unlink(missing_ok=True)
    _run(['openclaw', 'secrets', 'reload'])


def ensure_openclaw_entry():
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    AGENT_DIR.mkdir(parents=True, exist_ok=True)
    (WORKSPACE / 'SOUL.md').write_text(_SOUL_PLACEHOLDER, encoding='utf-8')

    bak = OPENCLAW_CONFIG.parent / f'openclaw.json.bak-xiaotuo-{datetime.now():%Y%m%d_%H%M%S}'
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    print(f'  Backup → {bak.name}')

    provider_block = {
        'baseUrl': 'http://localhost:8080/api/proxy/kimi',
        'api': 'anthropic-messages',
        'apiKey': {'source': 'store', 'provider': 'default', 'id': SECRET_ID},
        'models': [{'id': 'k3', 'name': 'Kimi K3', 'reasoning': True,
                    'input': ['text', 'image', 'video'],
                    'cost': {'input': 0, 'output': 0}}],
    }
    entry = {
        'name': AGENT_NAME,
        'workspace': str(WORKSPACE),
        'agentDir': str(AGENT_DIR),
        'identity': {'name': AGENT_NAME},
        'model': {'primary': f'{PROVIDER}/k3', 'fallbacks': ['minimax/MiniMax-M3']},
        'tools': {'deny': ['exec', 'process', 'fs', 'apply_patch', 'automations',
                           'gateway', 'plugins'],
                  'alsoAllow': ['message']},
        'skills': ['session-logs'],
    }
    patch = {'models': {'providers': {PROVIDER: provider_block}},
             'agents': {'entries': {AGENT_ID: entry}}}
    p = _run(['openclaw', 'config', 'patch', '--stdin'], input=json.dumps(patch))
    if p.returncode != 0:
        print('PATCH FAILED:', p.stderr.strip())
        sys.exit(1)
    print(f'  openclaw entry {AGENT_ID} + provider {PROVIDER} 已写入')


def ensure_agents_json():
    agents = json.loads(AGENTS_JSON.read_text(encoding='utf-8'))
    if not isinstance(agents, list):
        agents = []
    bak = AGENTS_JSON.with_name(f'agents.json.bak-xiaotuo-{datetime.now():%Y%m%d_%H%M%S}')
    bak.write_text(json.dumps(agents, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'  Backup → {bak.name}')

    now = datetime.now().isoformat()
    existing = next((a for a in agents if isinstance(a, dict)
                     and a.get('id') == AGENT_ID), None)
    record = {
        'id': AGENT_ID,
        'name': AGENT_NAME,
        'role': '商务',
        'bg': '#2E8B57',
        'avatar': 12,
        'status': 'online',
        'msg': '',
        'archived': False,
        'permission': 'dev',
        'visibility': 'creator',
        'createdBy': 'user_7acb72ff',
        'createdAt': now,
        'connectionType': '',
        'apiProvider': 'kimi',
        'apiModel': '',
        'openclawAgent': AGENT_ID,
        'openclawModel': '',
        'openclawName': 'xiaotuo',
        'aiProvider': 'kimi',
        'department': '',
        'customEndpoint': '',
        'group': '业务团队',
        'idDoc': '',
        'soulDoc': _SOUL_PLACEHOLDER,
        'systemPrompt': '',
        'createdByName': '管理员',
        'pinned': False,
        'toolsDoc': '',
        'userDoc': '',
        'badge': '',
        'is_ai': True,
        'tenant_id': 't_default',
    }
    if existing:
        # 幂等：保留运行期字段（积分/记忆等），只补缺失的静态字段
        changed = False
        for k, v in record.items():
            if k not in existing:
                existing[k] = v
                changed = True
        print(f'  agents.json 已有 {AGENT_ID}，补缺失字段 {"(有更新)" if changed else "(无变化)"}')
    else:
        agents.append(record)
        print(f'  agents.json 新增 {AGENT_ID}（小拓）')
    AGENTS_JSON.write_text(json.dumps(agents, ensure_ascii=False, indent=1),
                           encoding='utf-8')


def verify():
    print('\n=== verify ===')
    print(f'  provider primary = {_oc_get(f"agents.entries.{AGENT_ID}.model.primary") or "(缺)"}')
    print(f'  workspace        = {_oc_get(f"agents.entries.{AGENT_ID}.workspace") or "(缺)"}')
    print(f'  secret ref       = {_oc_get(f"models.providers.{PROVIDER}.apiKey.id") or "(缺)"}')
    agents = json.loads(AGENTS_JSON.read_text(encoding='utf-8'))
    rec = next((a for a in agents if isinstance(a, dict) and a.get('id') == AGENT_ID), None)
    print(f'  agents.json      = {"✅" if rec else "❌ 缺记录"}'
          + (f' (name={rec.get("name")}, role={rec.get("role")})' if rec else ''))
    p = _run(['openclaw', 'agents', 'list'])
    hit = [l for l in p.stdout.splitlines() if AGENT_ID in l or 'xiaotuo' in l.lower()]
    print(f'  openclaw agents list 命中 {len(hit)} 行')
    for l in hit:
        print('   ', l.strip())


if __name__ == '__main__':
    if '--verify' in sys.argv:
        verify()
    else:
        print('① secret store …')
        ensure_secret()
        print('② openclaw entry + provider …')
        ensure_openclaw_entry()
        print('③ data/agents.json …')
        ensure_agents_json()
        print('\n★ 收尾（需要生效时执行）：')
        print('  openclaw secrets reload   # 已执行' )
        print('  openclaw daemon restart   # 让新 entry/provider 进网关路由')
        print('  launchctl kickstart -k gui/$(id -u)/com.solobrave.server')
        print('    # server 重启后：PUT /api/agents/emp_xiaotuo/duties 落职责链')
        verify()
