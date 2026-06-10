#!/usr/bin/env python3
"""
批量同步员工 API Key 到 OpenClaw

遍历 agents.json 中所有配置了 apiKey + apiProvider/aiProvider 的员工，
调用 openclaw models auth --agent <id> paste-api-key --provider <provider>
将 API Key 同步到 OpenClaw。

用法:
    python sync_api_keys_to_openclaw.py
"""

import json
import os
import subprocess
import sys

OPENCLAW_CLI = '/opt/homebrew/bin/openclaw'
DATA_DIR = os.path.join(os.path.expanduser('~'), '.solobrave-data')
AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')
OPENCLAW_TIMEOUT = 30


def _run_openclaw(args, input_data=None):
    """执行 openclaw CLI 命令"""
    cmd = [OPENCLAW_CLI] + args
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=OPENCLAW_TIMEOUT, env=env, input=input_data
        )
        return True, result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return False, '', f'OpenClaw CLI not found at {OPENCLAW_CLI}', -1
    except subprocess.TimeoutExpired:
        return False, '', f'Command timed out after {OPENCLAW_TIMEOUT}s', -1
    except PermissionError:
        return False, '', f'Permission denied executing {OPENCLAW_CLI}', -1
    except Exception as e:
        return False, '', str(e), -1


def sync_agent_api_key(agent):
    """同步单个员工的 API Key 到 OpenClaw"""
    agent_id = agent.get('id')
    api_key = agent.get('apiKey', '').strip()
    provider = agent.get('apiProvider', '') or agent.get('aiProvider', '')

    if not api_key or not provider:
        return False, '缺少 apiKey 或 provider'
    if not os.path.isfile(OPENCLAW_CLI):
        return False, f'OpenClaw CLI 未找到: {OPENCLAW_CLI}'

    args = ['models', 'auth', '--agent', agent_id, 'paste-api-key', '--provider', provider]
    success, stdout, stderr, rc = _run_openclaw(args, input_data=api_key)
    if success and rc == 0:
        return True, stdout.strip()
    else:
        err = stderr or stdout or f'returncode={rc}'
        return False, err


def main():
    if not os.path.isfile(AGENTS_FILE):
        print(f'[Error] 找不到 agents.json: {AGENTS_FILE}')
        sys.exit(1)

    with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
        agents = json.load(f)

    if not isinstance(agents, list):
        print('[Error] agents.json 格式错误，应为数组')
        sys.exit(1)

    # 筛选出有 apiKey 的员工
    candidates = []
    for a in agents:
        if a.get('apiKey', '').strip() and (a.get('apiProvider') or a.get('aiProvider')):
            candidates.append(a)

    print(f'[Info] 共 {len(agents)} 个员工，其中 {len(candidates)} 个配置了 API Key')
    print()

    if not candidates:
        print('[Info] 没有需要同步的员工')
        return

    success_count = 0
    fail_count = 0
    for agent in candidates:
        agent_id = agent.get('id', '?')
        name = agent.get('name', '?')
        provider = agent.get('apiProvider', '') or agent.get('aiProvider', '')
        api_key_masked = agent.get('apiKey', '')[:4] + '****' if agent.get('apiKey') else '无'

        print(f'[{agent_id}] {name} provider={provider} apiKey={api_key_masked} ... ', end='', flush=True)
        ok, msg = sync_agent_api_key(agent)
        if ok:
            print('OK')
            success_count += 1
        else:
            print(f'FAIL: {msg}')
            fail_count += 1

    print()
    print(f'[Done] 成功: {success_count}  失败: {fail_count}  总计: {len(candidates)}')


if __name__ == '__main__':
    main()
