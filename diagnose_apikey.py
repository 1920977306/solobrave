#!/usr/bin/env python3
"""
诊断 agents.json 中 apiKey 字段的污染情况
用法: python diagnose_apikey.py [--data-dir /path/to/data] [--fix]
"""
import argparse
import json
import os
import re
import sys


def find_agents_json(data_dir=None):
    candidates = []
    if data_dir:
        candidates.append(os.path.join(data_dir, 'agents.json'))
    env_dir = os.environ.get('SOLOBRAVE_DATA_DIR')
    if env_dir:
        candidates.append(os.path.join(env_dir, 'agents.json'))
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'agents.json'))
    candidates.append(os.path.join(os.path.expanduser('~'), '.solobrave-data', 'agents.json'))
    candidates.append(os.path.join(os.getcwd(), 'data', 'agents.json'))
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


_LOG_PATTERNS = [
    re.compile(r'\[\d{2}:\d{2}:\d{2}\]\s+"(GET|POST|PUT|DELETE|OPTIONS)\s+[^"]*\s+HTTP/1\.1"\s+\d+'),
    re.compile(r'\[\d{2}:\d{2}:\d{2}\]\s+\['),
    re.compile(r'\[PUT agent\]|\[GET agents\]|\[POST agent\]|\[OpenClawSync\]'),
]


def is_log_polluted(value):
    if not isinstance(value, str) or len(value) < 30:
        return False
    for pat in _LOG_PATTERNS:
        if pat.search(value):
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description='诊断 agents.json 中 apiKey 污染情况')
    parser.add_argument('--data-dir', help='指定数据目录')
    parser.add_argument('--fix', action='store_true', help='清理污染的 apiKey（设为空字符串）')
    parser.add_argument('--output', help='输出修复后的 agents.json 到新路径（默认覆盖原文件）')
    args = parser.parse_args()

    agents_file = find_agents_json(args.data_dir)
    if not agents_file:
        print('[Error] 找不到 agents.json')
        sys.exit(1)

    print(f'[Info] 使用: {agents_file}')
    with open(agents_file, 'r', encoding='utf-8') as f:
        agents = json.load(f)

    if not isinstance(agents, list):
        print('[Error] agents.json 格式错误')
        sys.exit(1)

    polluted = []
    clean = []
    for a in agents:
        api_key = a.get('apiKey', '') or ''
        if is_log_polluted(api_key):
            polluted.append({
                'id': a.get('id'),
                'name': a.get('name'),
                'apiKey_len': len(api_key),
                'apiKey_preview': api_key[:200] + ('...' if len(api_key) > 200 else ''),
                'provider': a.get('aiProvider') or a.get('apiProvider'),
            })
        else:
            clean.append({
                'id': a.get('id'),
                'name': a.get('name'),
                'apiKey_len': len(api_key),
                'has_key': bool(api_key),
            })

    print(f'\n[结果] 总共 {len(agents)} 个员工')
    print(f'  - 污染: {len(polluted)} 个')
    print(f'  - 正常: {len(clean)} 个')

    if polluted:
        print('\n--- 污染详情 ---')
        for p in polluted:
            print(f"\n  [{p['id']}] {p['name']}")
            print(f"    apiKey 长度: {p['apiKey_len']}")
            print(f"    供应商: {p['provider']}")
            print(f"    预览: {p['apiKey_preview']}")

    print('\n--- 正常员工 ---')
    for c in clean:
        status = '有Key' if c['has_key'] else '无Key'
        print(f"  [{c['id']}] {c['name']} — {status} (长度 {c['apiKey_len']})")

    if args.fix and polluted:
        fixed = 0
        for a in agents:
            api_key = a.get('apiKey', '') or ''
            if is_log_polluted(api_key):
                a['apiKey'] = ''
                fixed += 1
        out_path = args.output or agents_file
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
        print(f'\n[Fix] 已清理 {fixed} 个员工的污染 apiKey → {out_path}')


if __name__ == '__main__':
    main()
