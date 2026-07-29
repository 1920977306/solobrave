#!/usr/bin/env python3
"""更新三个员工的toolsDoc，加入具体的curl命令示例"""
import json
import shutil
from datetime import datetime

AGENTS_FILE = "data/agents.json"

# 备份
bak = f"{AGENTS_FILE}.bak.{datetime.now().strftime('%Y%m%d%H%M')}"
shutil.copy2(AGENTS_FILE, bak)
print(f"✅ 备份: {bak}")

with open(AGENTS_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

TARGET_IDS = {"emp_1780132768182", "emp_1779955656118", "emp_1780199176680"}

# 每个员工定制化的toolsDoc
TOOLS_DOCS = {
    "emp_1780132768182": """# 工具能力

## 知识库API（SoloBrave）
用 curl 命令调用API。所有请求带两个Header：
- Content-Type: application/json
- X-Agent-Id: emp_1780132768182

### 存储知识（最重要！收到重要信息必须执行）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories | python3 -m json.tool

再存储：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1780132768182" \\
  -d '{"title":"达人XX分析","content":"分析内容...","categoryId":1}'

### 搜索知识
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1780132768182" \\
  -d '{"query":"关键词"}'

## 业务API（同样用curl调用）
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: emp_1780132768182"
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: emp_1780132768182"
- 项目组列表: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: emp_1780132768182"

## 使用规则
1. 存储知识前先 GET /api/knowledge/categories 查看可用分类和ID
2. 存储时在content末尾标注："来源：孔明 | 时间：YYYY-MM-DD"
3. 分析结果存储后告知用户"已存入知识库"
""",

    "emp_1779955656118": """# 工具能力

## 知识库API（SoloBrave）
用 curl 命令调用API。所有请求带两个Header：
- Content-Type: application/json
- X-Agent-Id: emp_1779955656118

### 存储知识（最重要！收到重要信息必须执行）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories | python3 -m json.tool

再存储：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1779955656118" \\
  -d '{"title":"工作汇总XX","content":"汇总内容...","categoryId":1}'

### 搜索知识
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1779955656118" \\
  -d '{"query":"关键词"}'

## 业务API（同样用curl调用）
- 员工列表: curl -s http://localhost:8081/api/agents -H "X-Agent-Id: emp_1779955656118"
- 项目组列表: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: emp_1779955656118"

## 使用规则
1. 存储知识前先 GET /api/knowledge/categories 查看可用分类和ID
2. 存储时在content末尾标注："来源：上官婉儿 | 时间：YYYY-MM-DD"
3. 存储后告知用户"已存入知识库"
""",

    "emp_1780199176680": """# 工具能力

## 知识库API（SoloBrave）
用 curl 命令调用API。所有请求带两个Header：
- Content-Type: application/json
- X-Agent-Id: emp_1780199176680

### 存储知识（最重要！收到重要信息必须执行）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories | python3 -m json.tool

再存储：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1780199176680" \\
  -d '{"title":"达人XX档案","content":"达人信息...","categoryId":1}'

### 搜索知识
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: emp_1780199176680" \\
  -d '{"query":"关键词"}'

## 业务API（同样用curl调用）
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: emp_1780199176680"
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: emp_1780199176680"
- 项目组列表: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: emp_1780199176680"

## 使用规则
1. 存储知识前先 GET /api/knowledge/categories 查看可用分类和ID
2. 存储时在content末尾标注："来源：Helen | 时间：YYYY-MM-DD"
3. 存储后告知用户"已存入知识库"
""",
}

count = 0
for agent in data:
    if agent["id"] in TARGET_IDS:
        agent["toolsDoc"] = TOOLS_DOCS[agent["id"]]
        count += 1
        print(f"✅ 更新 toolsDoc: {agent['name']} ({agent['id']})")

with open(AGENTS_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n✅ 共更新 {count} 个员工")
print(f"✅ 文件已写回: {AGENTS_FILE}")
