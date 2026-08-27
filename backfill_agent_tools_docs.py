# -*- coding: utf-8 -*-
"""存量 AI 员工 TOOLS.md 回填脚本。

背景：早期创建的员工 TOOLS.md 是旧版（curl 不带 X-Agent-Id），
导致达人录入归属错乱（created_by='localhost'）或被 SubpoolGuard 拒绝。
本脚本为所有现存员工重新生成 TOOLS.md（X-Agent-Id 硬编码 + 防编造使用规则），
并同步更新 agents.json 的 toolsDoc 字段、为缺失的 systemPrompt 追加【工具使用铁律】。

生成逻辑直接复用 solobrave-server.py 的 _build_agent_tools_doc / _ANTI_FABRICATION_RULES，
与服务端创建新员工时的输出完全一致。

默认 dry-run：列出每个员工的 workspace 路径与现状，不改任何文件。
  python backfill_agent_tools_docs.py                 # 只列出，不修改
  python backfill_agent_tools_docs.py --write         # 执行回填
  python backfill_agent_tools_docs.py --write --port 8081   # 指定 API 端口（默认取服务端 PORT=8080）

写入前自动备份：TOOLS.md -> TOOLS.md.bak-<时间戳>；agents.json -> agents.json.bak-<时间戳>。
workspace 目录不存在的员工跳过 TOOLS.md 落盘（仍更新 agents.json），并列出提示。
"""
import importlib.util
import json
import os
import shutil
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_FILE = os.path.join(BASE_DIR, 'data', 'agents.json')

sys.stdout.reconfigure(encoding='utf-8')

# 复用服务端的 TOOLS.md 生成器与防编造铁律，保证与新建员工输出一致
spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(BASE_DIR, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)


def workspace_path_of(agent):
    """与服务端 write-agent-docs 的默认 workspace 规则一致：
    ~/.openclaw/workspace-<openclawName 或 agent id>"""
    name = (agent.get('openclawName') or '').strip() or agent.get('id', '')
    return os.path.expanduser('~/.openclaw/workspace-' + name)


def main():
    args = sys.argv[1:]
    do_write = '--write' in args
    if '--port' in args:
        idx = args.index('--port')
        if idx + 1 >= len(args):
            print('错误：--port 后需要端口号')
            sys.exit(1)
        srv.PORT = int(args[idx + 1])

    if not os.path.isfile(AGENTS_FILE):
        print(f'agents.json 不存在: {AGENTS_FILE}')
        sys.exit(1)
    with open(AGENTS_FILE, encoding='utf-8') as f:
        agents = json.load(f)
    if not isinstance(agents, list):
        print('agents.json 格式异常（应为列表）')
        sys.exit(1)

    ts = time.strftime('%Y%m%d-%H%M%S')
    active = [a for a in agents if isinstance(a, dict) and a.get('id') and not a.get('archived')]
    print(f'共 {len(active)} 个在职员工（API 端口: {srv.PORT}）：\n')

    plan = []  # (agent, tools_md, workspace, tools_path_or_None)
    for a in active:
        aid = a['id']
        name = a.get('name', aid)
        tools_md = srv._build_agent_tools_doc(aid)
        ws = workspace_path_of(a)
        tools_path = os.path.join(ws, 'TOOLS.md') if os.path.isdir(ws) else None
        has_old = bool(tools_path and os.path.exists(tools_path))
        has_rules = '【工具使用铁律】' in (a.get('systemPrompt') or '')
        print(f"  {name} ({aid})")
        print(f"    workspace: {ws}{'（不存在，跳过落盘）' if not tools_path else ''}")
        print(f"    旧 TOOLS.md: {'有，将备份' if has_old else '无'} | systemPrompt 铁律: {'已有' if has_rules else '缺失，将追加'}")
        plan.append((a, tools_md, tools_path))

    if not do_write:
        print('\ndry-run，未修改。确认后加 --write 执行回填。')
        return

    # 1. 写 TOOLS.md（有旧文件先备份）
    written = 0
    for a, tools_md, tools_path in plan:
        if not tools_path:
            continue
        if os.path.exists(tools_path):
            shutil.copy2(tools_path, tools_path + f'.bak-{ts}')
        with open(tools_path, 'w', encoding='utf-8') as f:
            f.write(tools_md)
        written += 1

    # 2. 备份并更新 agents.json：toolsDoc 重新生成 + systemPrompt 补铁律
    shutil.copy2(AGENTS_FILE, AGENTS_FILE + f'.bak-{ts}')
    rules_added = 0
    for a, tools_md, _tools_path in plan:
        a['toolsDoc'] = tools_md
        if '【工具使用铁律】' not in (a.get('systemPrompt') or ''):
            a['systemPrompt'] = (a.get('systemPrompt') or '').rstrip() + srv._ANTI_FABRICATION_RULES
            rules_added += 1
    with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(agents, f, ensure_ascii=False, indent=2)

    print(f'\n完成：')
    print(f'  TOOLS.md 落盘 {written} 个（旧文件已备份为 TOOLS.md.bak-{ts}）')
    skipped = len(plan) - written
    if skipped:
        print(f'  {skipped} 个员工 workspace 不存在，未落盘（agents.json 已更新，重新注册时会自动写入）')
    print(f'  agents.json 已更新 {len(plan)} 个 toolsDoc，{rules_added} 个 systemPrompt 追加铁律（备份 agents.json.bak-{ts}）')


if __name__ == '__main__':
    main()
