#!/usr/bin/env python3
"""
inject_system_prompt_v2.py
给所有活跃员工注入API调用指令到systemPrompt
"""
import json
import shutil
from datetime import datetime

AGENTS_FILE = "data/agents.json"

# 备份
backup_path = f"data/agents.json.bak.{datetime.now().strftime('%Y%m%d%H%M')}"
shutil.copy2(AGENTS_FILE, backup_path)
print(f"✅ 备份: {backup_path}")

with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
    agents = json.load(f)

PROJECT_ID = "grp_1779608571712"

# COOLCHAP项目组成员 - 有定制化的协同规则
TEAM_INJECTS = {
    "emp_1780132768182": {  # 孔明
        "agent_id": "emp_1780132768182",
        "search_query": "来源：Helen",
        "store_prefix": "[分析]",
        "sign": "来源：孔明",
    },
    "emp_1780199176680": {  # Helen
        "agent_id": "emp_1780199176680",
        "search_query": "来源：孔明",
        "store_prefix": "[跟进结果]",
        "sign": "来源：Helen",
    },
    "emp_1779955656118": {  # 上官婉儿
        "agent_id": "emp_1779955656118",
        "search_query": "来源：孔明",
        "store_prefix": "[进度汇总]",
        "sign": "来源：上官婉儿",
    },
}


def build_team_injection(cfg):
    """项目组成员的定制化注入"""
    aid = cfg["agent_id"]
    return f"""

【强制规则 - 你必须执行以下操作，不可跳过】

## 规则1：每次对话开始，你必须先用bash执行以下curl命令搜索知识库
在回复用户任何内容之前，先执行：
curl -s -X POST http://localhost:8081/api/knowledge/search -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"query":"{cfg["search_query"]}","projectId":"{PROJECT_ID}"}}'
读取返回结果后，再回复用户。如果搜到了队友的分析或跟进结果，要在回复中提及。

## 规则2：完成分析/跟进/汇总后，你必须用bash执行以下curl命令存入知识库
curl -s -X POST http://localhost:8081/api/knowledge/entries -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"title":"{cfg["store_prefix"]}主题","content":"内容...\\n{cfg["sign"]} | 时间：2026-07-26","categoryId":1,"projectId":"{PROJECT_ID}"}}'

## 规则3：禁止用fetch，必须用curl（通过bash工具执行）
## 规则4：搜索知识库用POST /api/knowledge/search，禁止用GET /api/knowledge/entries
## 规则5：达人API是 /api/influencers，不是 /api/talents

## 查询业务数据也用curl：
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: {aid}"
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: {aid}"
- 项目组: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {aid}"
"""


def build_generic_injection(aid, name):
    """通用员工的注入"""
    return f"""

【强制规则 - 你必须执行以下操作，不可跳过】

## 规则1：每次对话开始，如果用户的问题涉及业务数据或已有知识，你必须先用bash执行以下curl命令搜索知识库
curl -s -X POST http://localhost:8081/api/knowledge/search -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"query":"用户问题的关键词"}}'
读取返回结果后，再回复用户。不要凭记忆编造数据，必须先查询。

## 规则2：完成工作后，如果产生了有价值的信息，你必须用bash执行以下curl命令存入知识库
curl -s -X POST http://localhost:8081/api/knowledge/entries -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"title":"主题","content":"内容...\\n来源：{name} | 时间：2026-07-26","categoryId":1}}'

## 规则3：禁止用fetch，必须用curl（通过bash工具执行）
## 规则4：搜索知识库用POST /api/knowledge/search，禁止用GET /api/knowledge/entries
## 规则5：达人API是 /api/influencers，不是 /api/talents

## 查询业务数据也用curl：
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: {aid}"
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: {aid}"
- 项目组: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {aid}"
"""


injected_count = 0
skipped_count = 0

for agent in agents:
    agent_id = agent.get("id", "")
    name = agent.get("name", "未知")
    archived = agent.get("archived", False)
    api_provider = agent.get("apiProvider", "")

    # 跳过已归档的
    if archived:
        print(f"⏭️ {name} ({agent_id}) 已归档，跳过")
        skipped_count += 1
        continue

    # 跳过没有API provider的（如knowledge_admin）
    if not api_provider:
        print(f"⏭️ {name} ({agent_id}) 无apiProvider，跳过")
        skipped_count += 1
        continue

    # 跳过测试1（openclawName=main）
    if agent.get("openclawName") == "main" and "测试" in name:
        print(f"⏭️ {name} ({agent_id}) 测试员工，跳过")
        skipped_count += 1
        continue

    old_prompt = agent.get("systemPrompt", "")

    # 检查是否已注入过
    if "【强制规则" in old_prompt:
        print(f"⚠️ {name} ({agent_id}) 已有注入，跳过")
        skipped_count += 1
        continue

    # 项目组成员用定制化注入，其他用通用注入
    if agent_id in TEAM_INJECTS:
        injection = build_team_injection(TEAM_INJECTS[agent_id])
    else:
        injection = build_generic_injection(agent_id, name)

    agent["systemPrompt"] = old_prompt + injection
    print(f"✅ {name} ({agent_id}) systemPrompt已注入API指令")
    injected_count += 1

with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f"\n📊 注入: {injected_count}个 | 跳过: {skipped_count}个")
print(f"✅ 文件已写回: {AGENTS_FILE}")
print(f"现在重启8081: cd ~/Desktop/solobrave-test && lsof -ti:8081 | xargs kill -9; nohup python3 solobrave-server.py --data data 8081 > server.log 2>&1 &")
