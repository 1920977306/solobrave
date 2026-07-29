#!/usr/bin/env python3
"""
修复多AI协同配置：
1. 查询项目组API获取projectId
2. toolsDoc加fetch警告 + projectId参数
3. SOUL.md改为项目组内协同
"""
import json
import shutil
import urllib.request
from datetime import datetime

AGENTS_FILE = "data/agents.json"
BASE_URL = "http://localhost:8081"

TARGET_AGENTS = {
    "emp_1779955656118": "上官婉儿（HR）",
    "emp_1780132768182": "孔明（军师）",
    "emp_1780199176680": "Helen",
}

# Step 1: 查询每个员工的项目组
print("=== 查询项目组 ===")
agent_projects = {}

for agent_id, name in TARGET_AGENTS.items():
    try:
        req = urllib.request.Request(
            f"{BASE_URL}/api/groups",
            headers={"X-Agent-Id": agent_id}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print(f"\n{name} ({agent_id}) 的项目组:")
            print(json.dumps(data, ensure_ascii=False, indent=2))
            agent_projects[agent_id] = data
    except Exception as e:
        print(f"{name}: 查询失败 - {e}")
        agent_projects[agent_id] = None

print("\n\n=== 开始更新 agents.json ===")

# 备份
backup_path = f"data/agents.json.bak.{datetime.now().strftime('%Y%m%d%H%M')}"
shutil.copy2(AGENTS_FILE, backup_path)
print(f"✅ 备份: {backup_path}")

with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
    agents = json.load(f)

WARNING = """⚠️⚠️⚠️ 重要规则（必须遵守）⚠️⚠️⚠️
1. 所有API调用必须用 curl 命令（bash工具），禁止用 fetch！
2. 搜索知识库必须用 POST /api/knowledge/search，禁止用 GET /api/knowledge/entries！
3. 达人API端点是 /api/influencers，不是 /api/talents！
4. 每个curl必须带 -H "X-Agent-Id: {agent_id}"！
5. 搜索和存储知识库必须带 projectId 参数，只看同项目组的内容！
⚠️⚠️⚠️ 违反以上规则会导致数据搜不到 ⚠️⚠️⚠�

"""

# 从项目组响应中提取projectId
def extract_project_id(groups_data, agent_id):
    """从groups API响应中提取projectId"""
    if not groups_data:
        return None
    
    # 尝试不同的数据结构
    groups = groups_data if isinstance(groups_data, list) else groups_data.get("groups", groups_data.get("data", []))
    
    for group in groups:
        if isinstance(group, dict):
            # 检查agent是否在这个group里
            members = group.get("members", group.get("agents", group.get("agent_ids", [])))
            if isinstance(members, list):
                for m in members:
                    if isinstance(m, dict) and m.get("id") == agent_id:
                        return group.get("id") or group.get("_id") or group.get("project_id")
                    elif isinstance(m, str) and m == agent_id:
                        return group.get("id") or group.get("_id") or group.get("project_id")
            # 如果group有created_by或agent_id字段匹配
            if group.get("created_by") == agent_id or group.get("agent_id") == agent_id:
                return group.get("id") or group.get("_id")
    
    # 如果没找到匹配，返回第一个group的id（假设员工在同一项目组）
    for group in groups:
        if isinstance(group, dict):
            gid = group.get("id") or group.get("_id") or group.get("project_id")
            if gid:
                return gid
    return None

# 为每个员工生成新的toolsDoc
def make_tools_doc(agent_id, name, project_id):
    role = {
        "emp_1779955656118": "上官婉儿",
        "emp_1780132768182": "孔明",
        "emp_1780199176680": "Helen",
    }[agent_id]
    
    pid_str = f'"projectId":"{project_id}"' if project_id else '"projectId":"从groups接口获取的ID"'
    
    if agent_id == "emp_1780132768182":  # 孔明
        return WARNING.replace("{agent_id}", agent_id) + f"""# 工具能力

## 第一步：查你的项目组（每次对话开始时先执行）
curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {agent_id}"
→ 从返回结果找到你所在项目组的 id

## 知识库API（SoloBrave）
所有请求带两个Header：Content-Type: application/json 和 X-Agent-Id: {agent_id}

### 存储知识（最重要！每次分析后必须执行）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories -H "X-Agent-Id: {agent_id}" | python3 -m json.tool

再存储（必须带projectId，标题用[分析]前缀）：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"title":"[分析]文文-达人匹配建议","content":"分析内容...\\n来源：孔明 | 时间：2026-07-25 | 建议行动人：Helen | 建议行动：推荐美妆类商品","categoryId":1,{pid_str}}}'

### 搜索知识库（每次对话开始时搜索同项目组队友的更新）
搜索Helen的行动反馈：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"来源：Helen",{pid_str}}}'

搜索上官婉儿的进度汇总：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"来源：上官婉儿",{pid_str}}}'

## 业务API
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: {agent_id}"
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: {agent_id}"

## 使用规则
1. 先查项目组拿projectId，再搜索/存储知识库
2. 标题必须用前缀：[分析] [策略] [趋势] [预警] [紧急]
3. content末尾标注：来源：孔明 | 时间：YYYY-MM-DD | 建议行动人：XXX | 建议行动：XXX
4. 存储后告知用户"已存入知识库"
"""

    elif agent_id == "emp_1780199176680":  # Helen
        return WARNING.replace("{agent_id}", agent_id) + f"""# 工具能力

## 第一步：查你的项目组（每次对话开始时先执行）
curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {agent_id}"
→ 从返回结果找到你所在项目组的 id

## 知识库API（SoloBrave）
所有请求带两个Header：Content-Type: application/json 和 X-Agent-Id: {agent_id}

### 搜索知识库（每次对话开始时先搜索同项目组孔明的分析！）
搜索孔明的分析建议：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"来源：孔明",{pid_str}}}'

搜索所有分析类内容：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"[分析]",{pid_str}}}'

### 存储知识（收到重要信息或完成行动后必须执行）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories -H "X-Agent-Id: {agent_id}" | python3 -m json.tool

存储跟进结果（必须带projectId）：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"title":"[跟进结果]文文-已联系","content":"已联系文文，确认合作意向\\n来源：Helen | 时间：2026-07-25 | 对应分析：[分析]文文-达人匹配建议","categoryId":1,{pid_str}}}'

## 业务API
- 达人列表: curl -s http://localhost:8081/api/influencers -H "X-Agent-Id: {agent_id}"
- 商品列表: curl -s http://localhost:8081/api/products -H "X-Agent-Id: {agent_id}"
- 创建达人: curl -s -X POST http://localhost:8081/api/influencers -H "Content-Type: application/json" -H "X-Agent-Id: {agent_id}" -d '{{"name":"达人名","followers":500000,"category":"美妆"}}'

## 使用规则
1. 先查项目组拿projectId，再搜索/存储知识库
2. 每次对话开始，先搜索"来源：孔明"看有没有新任务
3. 标题必须用前缀：[达人档案] [跟进结果] [商品信息] [客户偏好]
4. content末尾标注：来源：Helen | 时间：YYYY-MM-DD
5. 存储后告知用户"已存入知识库"
"""

    else:  # 上官婉儿
        return WARNING.replace("{agent_id}", agent_id) + f"""# 工具能力

## 第一步：查你的项目组（每次对话开始时先执行）
curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {agent_id}"
→ 从返回结果找到你所在项目组的 id

## 知识库API（SoloBrave）
所有请求带两个Header：Content-Type: application/json 和 X-Agent-Id: {agent_id}

### 搜索知识库（每次对话开始时搜索同项目组队友的更新！）
搜索Helen的跟进结果：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"来源：Helen",{pid_str}}}'

搜索孔明的分析：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"来源：孔明",{pid_str}}}'

搜索所有跟进结果：
curl -s -X POST http://localhost:8081/api/knowledge/search \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"query":"[跟进结果]",{pid_str}}}'

### 存储知识（汇总后必须执行，带projectId）
先查分类：
curl -s http://localhost:8081/api/knowledge/categories -H "X-Agent-Id: {agent_id}" | python3 -m json.tool

存储进度汇总：
curl -s -X POST http://localhost:8081/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"title":"[进度汇总]2026-07-25-周报","content":"本周进度汇总...\\n来源：上官婉儿 | 时间：2026-07-25 | 涉及人员：孔明、Helen","categoryId":1,{pid_str}}}'

## 业务API
- 员工列表: curl -s http://localhost:8081/api/agents -H "X-Agent-Id: {agent_id}"
- 项目组列表: curl -s http://localhost:8081/api/groups -H "X-Agent-Id: {agent_id}"

## 使用规则
1. 先查项目组拿projectId，再搜索/存储知识库
2. 标题必须用前缀：[进度汇总] [人员变动] [制度]
3. content末尾标注：来源：上官婉儿 | 时间：YYYY-MM-DD | 涉及人员：XXX
4. 存储后告知用户"已存入知识库"
"""

# 更新SOUL.md的协同部分
def make_soul_doc(agent_id, original_soul):
    if agent_id == "emp_1780132768182":  # 孔明
        return original_soul.replace(
            "## 多智能体协同\n你的分析结果存入知识库后，Helen（商务）会搜索并读取。",
            "## 多智能体协同（项目组内）\n你与同项目组的Helen（商务）通过知识库协同工作。\n所有知识库搜索和存储必须带projectId，只看同项目组的内容。"
        ).replace(
            "5. 每次对话开始时，先搜索知识库\"来源：Helen\"，看是否有行动反馈",
            "5. 每次对话开始时，先查项目组（GET /api/groups），再用projectId搜索知识库\"来源：Helen\"，看是否有行动反馈"
        )
    elif agent_id == "emp_1780199176680":  # Helen
        return original_soul.replace(
            "## 多智能体协同（最高优先级）\n你与孔明（军师）通过知识库协同工作：",
            "## 多智能体协同（项目组内·最高优先级）\n你与同项目组的孔明（军师）通过知识库协同工作：\n所有知识库搜索和存储必须带projectId，只看同项目组的内容。"
        ).replace(
            "1. 每次对话开始时，必须先用curl搜索知识库中\"来源：孔明\"的内容",
            "1. 每次对话开始时，先查项目组（GET /api/groups）拿projectId，再用curl搜索知识库中\"来源：孔明\"的内容"
        )
    else:  # 上官婉儿
        return original_soul.replace(
            "## 多智能体协同（最高优先级）\n你通过知识库与孔明和Helen协同工作：",
            "## 多智能体协同（项目组内·最高优先级）\n你与同项目组的孔明和Helen通过知识库协同工作：\n所有知识库搜索和存储必须带projectId，只看同项目组的内容。"
        ).replace(
            "1. 每次对话开始时，先搜索知识库中\"[跟进结果]\"和\"来源：Helen\"",
            "1. 每次对话开始时，先查项目组（GET /api/groups）拿projectId，再搜索知识库中\"[跟进结果]\"和\"来源：Helen\""
        )

updated = 0
for agent in agents:
    agent_id = agent.get("id", "")
    if agent_id not in TARGET_AGENTS:
        continue
    
    name = TARGET_AGENTS[agent_id]
    project_id = extract_project_id(agent_projects.get(agent_id), agent_id)
    
    if project_id:
        print(f"✅ {name} projectId: {project_id}")
    else:
        print(f"⚠️ {name} 未找到projectId，toolsDoc将用动态查找")
    
    # 更新 toolsDoc
    agent["toolsDoc"] = make_tools_doc(agent_id, name, project_id)
    
    # 更新 soulDoc
    original_soul = agent.get("soulDoc", "")
    agent["soulDoc"] = make_soul_doc(agent_id, original_soul)
    
    updated += 1
    print(f"✅ 更新 SOUL+TOOLS: {name} ({agent_id})")

with open(AGENTS_FILE, 'w', encoding='utf-8') as f:
    json.dump(agents, f, ensure_ascii=False, indent=2)

print(f"\n✅ 共更新 {updated} 个员工")
print(f"✅ 文件已写回: {AGENTS_FILE}")
print(f"\n现在请重启8081服务：cd ~/Desktop/solobrave-test && lsof -ti:8081 | xargs kill -9; nohup python3 solobrave-server.py --data data 8081 > server.log 2>&1 &")
