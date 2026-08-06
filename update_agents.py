#!/usr/bin/env python3
"""更新 agents.json：名称去括号 + 追加API规则"""
import json, os

AGENTS_PATH = os.path.join(os.path.dirname(__file__), 'data', 'agents.json')

with open(AGENTS_PATH, 'r', encoding='utf-8') as f:
    agents = json.load(f)

API_RULES = (
    "\n\n## API使用规则（必须遵守）\n"
    "1. 禁止在回复中输出原始JSON数据、代码块或技术调试信息，只用人话总结结果\n"
    "2. 通过exec工具执行命令行操作（如curl调用API），不要使用bash工具\n"
    "3. 达人数据API是 /api/talents（不是/api/influencers），商品数据API是 /api/products\n"
    "4. 每次调用API后，用人话总结返回结果，不要把原始JSON粘贴到回复中"
)

NAME_MAP = {
    '貂蝉（行政）': '貂蝉',
    '上官婉儿（HR）': '上官婉儿',
    '孔明（军师）': '孔明',
}

TARGET_IDS = {
    'emp_1779430403964',  # 貂蝉
    'emp_1779955656118',  # 上官婉儿
    'emp_1780132768182',  # 孔明
    'emp_1780199176680',  # Helen
}

updated = []
for agent in agents:
    aid = agent.get('id', '')
    old_name = agent.get('name', '')

    # 1. 更新名称
    if old_name in NAME_MAP:
        agent['name'] = NAME_MAP[old_name]
        updated.append(f"  名称: {old_name} -> {agent['name']}")

    # 2. 追加API规则到 systemPrompt
    if aid in TARGET_IDS:
        sp = agent.get('systemPrompt', '') or ''
        if 'API使用规则' not in sp:
            agent['systemPrompt'] = sp + API_RULES
            updated.append(f"  {agent['name']}: 已追加API使用规则")

with open(AGENTS_PATH, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print("=== agents.json 更新完成 ===")
for u in updated:
    print(u)
print(f"共更新 {len(updated)} 处")
