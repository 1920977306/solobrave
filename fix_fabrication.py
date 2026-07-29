#!/usr/bin/env python3
"""修复AI编造操作问题：在所有员工的systemPrompt中添加禁止编造规则"""
import json
import shutil
import os

AGENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'agents.json')

shutil.copy2(AGENTS_PATH, AGENTS_PATH + '.bak.fabrication')

with open(AGENTS_PATH, 'r', encoding='utf-8') as f:
    agents = json.load(f)

RULE = "\n\n【强制规则·禁止编造】必须如实报告API返回的真实结果。如果API返回错误、操作未执行或未找到数据，必须如实告知管理员。严禁声称已执行某操作但实际未执行，严禁编造API返回数据。每次调用API后，必须引用返回的原始JSON作为执行证据。"

count = 0
for agent in agents:
    sp = agent.get('systemPrompt', '')
    name = agent.get('name', agent.get('id', 'unknown'))
    if sp and '禁止编造' not in sp:
        agent['systemPrompt'] = sp + RULE
        count += 1
        print(f"  Updated: {name}")
    elif not sp:
        print(f"  Skipped (no systemPrompt): {name}")
    else:
        print(f"  Skipped (already has rule): {name}")

with open(AGENTS_PATH, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f"\nDone: {count} agents updated")
print(f"Backup: {AGENTS_PATH}.bak.fabrication")
