#!/usr/bin/env python3
"""在三个员工的toolsDoc顶部加醒目警告：必须用curl命令，不要用fetch；搜索必须用search接口不要用entries接口"""
import json
import shutil
from datetime import datetime

AGENTS_FILE = "data/agents.json"

# 备份
backup_path = f"data/agents.json.bak.{datetime.now().strftime('%Y%m%d%H%M')}"
shutil.copy2(AGENTS_FILE, backup_path)
print(f"✅ 备份: {backup_path}")

with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

WARNING = """⚠️⚠️⚠️ 重要警告（必须遵守）⚠️⚠️⚠️
1. 所有API调用必须用 curl 命令（通过bash工具执行），禁止用 fetch！
2. 搜索知识库必须用 POST /api/knowledge/search，禁止用 GET /api/knowledge/entries！
   entries接口只返回你自己创建的条目，搜不到别人的！
3. 达人API端点是 /api/influencers，不是 /api/talents！
4. 每个curl命令必须带 -H "X-Agent-Id: 你的agent_id" 头！
⚠️⚠️⚠️ 以上规则违反任何一个都会导致数据丢失 ⚠️⚠️⚠️

"""

TARGET_AGENTS = {
    "emp_1779955656118": "上官婉儿（HR）",
    "emp_1780132768182": "孔明（军师）",
    "emp_1780199176680": "Helen",
}

updated = 0
for agent in data:
    agent_id = agent.get("id", "")
    if agent_id not in TARGET_AGENTS:
        continue
    
    name = TARGET_AGENTS[agent_id]
    tools_doc = agent.get("toolsDoc", "")
    
    # 检查是否已有警告（避免重复添加）
    if "⚠️⚠️⚠️ 重要警告" in tools_doc:
        print(f"⏭️  跳过 {name} (已有警告)")
        continue
    
    # 在toolsDoc开头插入警告
    agent["toolsDoc"] = WARNING + tools_doc
    updated += 1
    print(f"✅ 更新 toolsDoc: {name} ({agent_id})")

with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n✅ 共更新 {updated} 个员工")
print(f"✅ 文件已写回: {AGENTS_FILE}")
