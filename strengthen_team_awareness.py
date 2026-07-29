#!/usr/bin/env python3
"""强化团队动态感知规则 - 让员工必须主动提及团队动态"""
import json
import shutil
from datetime import datetime

AGENTS_FILE = 'data/agents.json'
BACKUP_FILE = f'data/agents.json.bak.team_strong_{datetime.now().strftime("%Y%m%d%H%M")}'

shutil.copy2(AGENTS_FILE, BACKUP_FILE)
print(f'备份: {BACKUP_FILE}')

with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
    agents = json.load(f)

OLD_RULE = '【团队动态感知】你在项目组中，系统会自动为你注入同组其他AI成员的最近对话动态（格式：【团队动态】[成员名 时间]对话摘要）。利用这些信息与队友协调工作，避免重复劳动，主动配合队友的进展。'

NEW_RULE = '【团队动态感知·强制执行】系统会自动在你的上下文中注入同组其他AI成员的最近对话动态（格式：【团队动态】[成员名 时间]对话摘要）。你必须在回复中体现对团队动态的认知：\n1. 如果团队动态中有队友的近期工作，必须在回复中用1-2句话主动提及你看到的队友动态（例如"我注意到孔明刚做了COOLCHAP的达人匹配分析..."）\n2. 根据队友的进展调整你的工作方向，避免重复劳动\n3. 如果队友的分析与当前任务相关，主动引用并结合\n4. 即使是简单问候，也要先提及团队动态再回复正题\n5. 如果团队动态为空（没有队友近期对话），则正常回复无需提及'

count = 0
for agent in agents:
    sp = agent.get('systemPrompt', '')
    if OLD_RULE in sp:
        agent['systemPrompt'] = sp.replace(OLD_RULE, NEW_RULE)
        count += 1
        print(f'  更新: {agent.get("name", agent.get("id", "?"))}')

with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f'\n完成: {count} 个员工已更新团队动态感知规则')
