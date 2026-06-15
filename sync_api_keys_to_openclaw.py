#!/usr/bin/env python3
"""
批量同步员工 API Key 到 OpenClaw

遍历 agents.json 中所有配置了 apiKey + provider 的员工，
调用 openclaw models auth --agent <id> paste-api-key --provider <provider>
将 API Key 同步到 OpenClaw。

用法:
    # 自动搜索 agents.json
    python sync_api_keys_to_openclaw.py

    # 指定数据目录
    python sync_api_keys_to_openclaw.py --data-dir /path/to/data

    # 调试模式（打印所有员工的字段详情）
    python sync_api_keys_to_openclaw.py --debug
"""

import argparse
import json
import os
import subprocess
import sys

OPENCLAW_CLI = os.environ.get('OPENCLAW_CLI', '/opt/homebrew/bin/openclaw')
OPENCLAW_TIMEOUT = 30

# 应用内 provider -> OpenClaw CLI provider id 映射
_OPENCLAW_PROVIDER_MAP = {
    'kimi': 'moonshot',
    'moonshot': 'moonshot',
    'kimicode': 'kimi',
    'deepseek': 'deepseek',
    'zhipu': 'zhipu',
    'anthropic': 'anthropic',
    'siliconflow': 'siliconflow',
    'openai': 'openai',
}


def _openclaw_provider_for(app_provider):
    """将应用内员工配置的 provider 名称映射为 OpenClaw CLI 识别的 provider id"""
    if not app_provider:
        return ''
    return _OPENCLAW_PROVIDER_MAP.get(app_provider.lower(), app_provider)


def find_agents_json(data_dir=None):
    """搜索 agents.json 文件，返回找到的路径或 None"""
    candidates = []

    # 1. 命令行/环境变量指定
    if data_dir:
        candidates.append(os.path.join(data_dir, 'agents.json'))
    env_dir = os.environ.get('SOLOBRAVE_DATA_DIR')
    if env_dir:
        candidates.append(os.path.join(env_dir, 'agents.json'))

    # 2. 项目目录下的 data/
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(script_dir, 'data', 'agents.json'))

    # 3. 用户主目录下的 .solobrave-data/
    candidates.append(os.path.join(os.path.expanduser('~'), '.solobrave-data', 'agents.json'))

    # 4. 当前工作目录下的 data/
    candidates.append(os.path.join(os.getcwd(), 'data', 'agents.json'))

    print('[Search] 搜索 agents.json 路径:')
    for p in candidates:
        exists = os.path.isfile(p)
        print(f'  {"✓" if exists else "✗"} {p}')
        if exists:
            return p
    return None


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


def get_provider(agent):
    """从员工数据中提取 provider，支持多种字段名"""
    # 优先 aiProvider（前端用的字段），其次 apiProvider
    return agent.get('aiProvider', '') or agent.get('apiProvider', '')


def has_api_config(agent):
    """判断员工是否配置了 API Key + Provider"""
    api_key = (agent.get('apiKey', '') or '').strip()
    provider = get_provider(agent)
    return bool(api_key and provider)


def sync_agent_api_key(agent):
    """同步单个员工的 API Key 到 OpenClaw"""
    agent_id = agent.get('id')
    api_key = (agent.get('apiKey', '') or '').strip()
    app_provider = get_provider(agent)
    provider = _openclaw_provider_for(app_provider)

    if not api_key or not provider:
        return False, '缺少 apiKey 或 provider'
    if not os.path.isfile(OPENCLAW_CLI):
        return False, f'OpenClaw CLI 未找到: {OPENCLAW_CLI}'

    # OpenClaw 默认使用 <provider>:manual profile；按 provider 存key才能让 infer 命中
    profile_id = f'{provider}:manual'
    args = ['models', 'auth', 'paste-api-key', '--provider', provider, '--profile-id', profile_id]
    success, stdout, stderr, rc = _run_openclaw(args, input_data=api_key)
    if success and rc == 0:
        return True, stdout.strip()
    else:
        err = stderr or stdout or f'returncode={rc}'
        return False, err


def main():
    parser = argparse.ArgumentParser(description='批量同步员工 API Key 到 OpenClaw')
    parser.add_argument('--data-dir', help='指定数据目录（包含 agents.json）')
    parser.add_argument('--debug', action='store_true', help='调试模式：打印所有员工字段')
    args = parser.parse_args()

    agents_file = find_agents_json(args.data_dir)
    if not agents_file:
        print('[Error] 找不到 agents.json，请用 --data-dir 指定数据目录')
        sys.exit(1)

    print(f'[Info] 使用: {agents_file}')
    print()

    with open(agents_file, 'r', encoding='utf-8') as f:
        agents = json.load(f)

    if not isinstance(agents, list):
        print('[Error] agents.json 格式错误，应为数组')
        sys.exit(1)

    print(f'[Info] 共 {len(agents)} 个员工')
    print()

    # 调试模式：打印每个员工的字段
    if args.debug:
        print('[Debug] 所有员工字段详情:')
        for a in agents:
            print(f'  id={a.get("id")} name={a.get("name")}')
            relevant_keys = ['apiKey', 'aiProvider', 'apiProvider', 'apiModel',
                             'connectionType', 'openclawName', 'openclawAgent']
            for k in relevant_keys:
                v = a.get(k)
                if v:
                    display = v[:4] + '****' if 'Key' in k and len(v) > 4 else v
                    print(f'    {k}={display}')
        print()

    # 筛选出有 apiKey 的员工
    candidates = [a for a in agents if has_api_config(a)]
    no_key = [a for a in agents if not has_api_config(a)]

    print(f'[Info] 配置了 API Key 的员工: {len(candidates)} 个')
    print(f'[Info] 未配置 API Key 的员工: {len(no_key)} 个')
    print()

    if not candidates:
        print('[Info] 没有需要同步的员工')
        return

    # 列出要同步的员工
    print('将要同步的员工:')
    for a in candidates:
        agent_id = a.get('id', '?')
        name = a.get('name', '?')
        provider = get_provider(a)
        api_key_masked = (a.get('apiKey', '') or '')[:4] + '****'
        print(f'  [{agent_id}] {name}  provider={provider}  apiKey={api_key_masked}')
    print()

    success_count = 0
    fail_count = 0
    for agent in candidates:
        agent_id = agent.get('id', '?')
        name = agent.get('name', '?')
        provider = get_provider(agent)

        print(f'[{agent_id}] {name} provider={provider} ... ', end='', flush=True)
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
