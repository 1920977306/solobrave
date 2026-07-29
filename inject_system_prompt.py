#!/usr/bin/env python3
"""
把知识库搜索/存储指令注入systemPrompt（不只是toolsDoc）
systemPrompt是OpenClaw注入给LLM的系统提示词，AI必须遵守
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

INJECTS = {
    "emp_1780132768182": {  # 孔明
        "agent_id": "emp_1780132768182",
        "search_query": "来源：Helen",
        "search_desc": "Helen的行动反馈",
        "store_prefix": "[分析]",
        "store_desc": "分析结果",
        "sign": "来源：孔明",
    },
    "emp_1780199176680": {  # Helen
        "agent_id": "emp_1780199176680",
        "search_query": "来源：孔明",
        "search_desc": "孔明的分析建议",
        "store_prefix": "[跟进结果]",
        "store_desc": "跟进结果",
        "sign": "来源：Helen",
    },
    "emp_1779955656118": {  # 上官婉儿
        "agent_id": "emp_1779955656118",
        "search_query": "来源：孔明",
        "search_desc": "孔明的分析和Helen的跟进",
        "store_prefix": "[进度汇总]",
        "store_desc": "进度汇总",
        "sign": "来源：上官婉儿",
    },
}

for agent in agents:
    agent_id = agent.get("id", "")
    if agent_id not in INJECTS:
        continue
    
    cfg = INJECTS[agent_id]
    aid = cfg["agent_id"]
    
    # 构建注入到systemPrompt的指令块
    injection = f"""

【强制规则 - 你必须执行以下操作，不可跳过】

## 规则1：每次对话开始，你必须先用bash执行以下curl命令搜索知识库
在回复用户任何内容之前，先执行：
curl -s -X POST http://localhost:8081/api/knowledge/search -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"query":"{cfg["search_query"]}","projectId":"{PROJECT_ID}"}}'
读取返回结果后，再回复用户。如果搜到了队友的分析或跟进结果，要在回复中提及。

## 规则2：完成分析/跟进/汇总后，你必须用bash执行以下curl命令存入知识库
curl -s -X POST http://localhost:8081/api/knowledge/entries -H "Content-Type: application/json" -H "X-Agent-Id: {aid}" -d '{{"title":"{cfg["store_prefix"]}主题","content":"内容...\\n{cfg["sign"]} | 时间：2026-07-25","categoryId":1,"projectId":"{PROJECT_ID}"}}'

## 规则3：禁止用fetch，必须用curl（通过bash工具执行）
## 规则4：搜索知识库用POST /api/knowledge/search，禁止用GET /api/knowledge/entries
## 规则5：达人API是 /api/influencers，不是 /api/talents

## 查询业务数据也用curl：
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: {aid}"
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: {aid}"
- 项目组: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {aid}"
"""
    
    old_prompt = agent.get("systemPrompt", "")
    
    # 检查是否已注入过（避免重复）
    if "【强制规则" in old_prompt:
        print(f"⚠️ {agent['name']} systemPrompt已有注入，跳过")
        continue
    
    agent["systemPrompt"] = old_prompt + injection
    print(f"✅ {agent['name']} ({agent_id}) systemPrompt已注入API指令")

with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f"\n✅ 文件已写回: {AGENTS_FILE}")
print(f"现在重启8081: cd ~/Desktop/solobrave-test && lsof -ti:8081 | xargs kill -9; nohup python3 solobrave-server.py --data data 8081 > server.log 2>&1 &")
