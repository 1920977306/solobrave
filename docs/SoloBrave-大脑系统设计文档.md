# SoloBrave 大脑系统设计文档

---

## 一、系统概述

### 1.1 目标

为 SoloBrave 构建三层大脑架构：

1. **记忆服务**：重建现有记忆系统，解决 20 个已知问题
   - 分池存储（核心记忆 + 日常记录 + 归档层）
   - 跨平台文件锁保证并发安全
   - 所有 API 强制认证
   - 过期归档而非删除，支持恢复

2. **知识库**：结构化存储知识文档，支持录入/搜索/关联员工
   - 完全后端化，前端 localStorage 遗留数据自动迁移
   - 关联员工的文档自动注入 AI 上下文

3. **商品库**：结构化存储商品信息，支持录入/搜索/匹配
   - 后端 JSON 索引 + 独立目录存储

4. **达人库**：结构化存储达人信息，支持录入/搜索/匹配
   - 后端 JSON 索引 + 独立目录存储

5. **匹配引擎**：商品↔达人双向智能匹配
   - 5 维度评分算法（分类/标签/价格/粉丝数/互动率）
   - 前端 🔗 按钮 + 结果弹窗展示

### 1.2 架构原则

#### 1. 数据持久化优先

| 模块 | V1 方案 | V2 方案 | 原则 |
|---|---|---|---|
| **记忆系统** | `localStorage` 碎片存储 | 后端 JSON 文件分池（core/daily/archive） | 浏览器不是数据库，所有业务数据必须落盘到服务器 |
| **知识库** | `localStorage.getItem('sb_docs')` | `GET/POST /api/knowledge` | 文档是团队资产，不能随浏览器清除而丢失 |
| **聊天记录** | `localStorage` 存储 | 后端 `/api/groups/{id}/history` + local 兜底 | 核心数据上云，本地仅作降级缓存 |
| **商品/达人库** | 无 | 后端 JSON 索引 + 独立目录 | 结构化业务数据必须持久化 |

> **底线**：任何用户无法随手 `localStorage.clear()` 删掉的数据，才配叫业务数据。

#### 2. 分层隔离原则

```
┌─────────────────────────────────────────┐
│  前端 (index.html)                       │  ← 只负责渲染和交互，不持有业务状态
│  · 调用 apiFetch()                      │
│  · 本地仅存：主题、当前选中项等 UI 状态   │
├─────────────────────────────────────────┤
│  接入层 (solobrave-server.py)            │  ← HTTP 路由 + 认证 + 参数校验
│  · do_GET/POST/PUT/DELETE               │
│  · _authenticate()                      │
├─────────────────────────────────────────┤
│  业务层 (Handler 方法)                   │  ← 编排逻辑，不涉及存储细节
│  · _handle_get_memory_v2()              │
│  · _calculate_match_score()             │
├─────────────────────────────────────────┤
│  存储层 (_load/_save 方法)               │  ← 文件 I/O + 并发锁
│  · _load_memory_v2() / _save_memory_v2()│
│  · 跨平台 threading.Lock()               │
├─────────────────────────────────────────┤
│  文件系统 (~/.solobrave-data/)           │  ← 唯一真相源
│  · JSON 文件 + 目录隔离                   │
└─────────────────────────────────────────┘
```

- **前端不直接操作文件**：所有数据变更必须通过 API
- **后端不直接操作 DOM**：不生成 HTML，只返回 JSON
- **存储层不处理业务逻辑**：只负责原子读写

#### 3. 认证即边界原则

所有新增 API 必须执行 `_authenticate()`：

```python
# 已覆盖的 API
/api/memory/*        # 记忆系统 v2
/api/knowledge/*     # 知识库
/api/products/*      # 商品库
/api/influencers/*   # 达人库
/api/match/*         # 匹配引擎
```

- 未认证请求 → 401
- 记忆系统 v1 曾无认证，v2 已补全
- **原则**：没有认证的 API 等于公共厕所

#### 4. 降级容错原则

| 场景 | 主方案 | 降级方案 | 最劣方案 |
|---|---|---|---|
| AI 消息发送 | OpenClaw WS | API 直连 | 本地模拟回复 |
| 群聊历史加载 | 后端 `/api/groups/{id}/history` | localStorage 旧数据 | 空列表 |
| 记忆注入 | 后端 core+daily 池 | 空提示词 | 硬编码默认人设 |
| 知识库读取 | 后端 `/api/knowledge` | 空上下文 | 无 |

- 每次降级必须打日志：`console.log('[降级] 原因...')`
- 不允许因为后端挂掉导致前端白屏

#### 5. 并发安全原则

记忆系统使用跨平台文件锁：

```python
_memory_file_locks = {}  # filepath -> threading.Lock()

def _get_memory_file_lock(filepath):
    with _memory_locks_mutex:
        if filepath not in _memory_file_locks:
            _memory_file_locks[filepath] = threading.Lock()
        return _memory_file_locks[filepath]
```

- Python `http.server` 是多线程的，文件读写必须加锁
- 锁粒度：按文件路径隔离，不同员工的记忆文件互不阻塞

#### 6. 容量管控原则

| 资源 | 上限 | 超限行为 |
|---|---|---|
| 核心记忆池 | 100 条 | 拒绝新增，提示归档 |
| 日常记忆池 | 100 条 | LRU 淘汰或触发归纳 |
| 日常记忆 TTL | 30 天 | 自动标记 archived=True（可恢复） |
| 单次注入记忆 | core 5 条 + daily 3 条 | 截断，优先 core |
| 聊天记录存储 | 后端 100 条 / 员工 50 条 | 截断保留最新 |
| 商品/达人列表 | 单次返回 50 条 | 分页 |

> **原则**：无限增长等于慢性死亡，每个池子必须有盖子和排水口。

#### 7. 最小侵入原则

新增功能不得破坏现有代码：

- **匹配引擎**：新增 `/api/match/*` 路由，不改动商品/达人 CRUD
- **知识库后端化**：保留 `syncLocalStorageDocsToBackend()` 做一次性迁移，不强制刷新页面
- **记忆 v2**：`_load_memory_v2()` 内部自动将 v1 扁平数组迁移到分池格式
- **CSS 复用**：达人库复用商品库全部 CSS 类名，不新增样式表

#### 8. 可观测原则

所有关键操作必须留痕：

```python
print(f'  [Memory] 保存 {empId} core={len(core)} daily={len(daily)}', flush=True)
print(f'  [Influencer] 录入达人: {name} ({id})', flush=True)
```

- 后端：`print` 到 stdout，systemd/docker 自动收集
- 前端：`console.log('[模块] 动作: 详情')`
- 异常：必须 `try/except + traceback.print_exc()`

#### 9. 单向依赖原则

模块依赖关系：

```
聊天系统 ──→ 记忆系统
    ↓           ↓
商品库 ←── 匹配引擎 ──→ 达人库
    ↑
知识库 ────────┘
```

- 记忆系统不依赖商品库
- 匹配引擎可以读取商品/达人，但商品/达人不知道匹配引擎存在
- 知识库可以被任何模块读取，但不主动调用其他模块

#### 10. 人类可读原则

所有持久化文件使用 JSON + 注释（Python 端打印注释）：

```json
{
  "_comment": "SoloBrave 记忆数据 v2",
  "version": "2.0",
  "config": { "core_max": 100, "daily_max": 100 },
  "core": [ ... ],
  "daily": [ ... ]
}
```

- 管理员可以 `cat` 文件直接看懂内容
- 不引入 SQLite/LevelDB 等需要工具才能查看的存储
- 备份 = `cp -r ~/.solopaw-data /backup`

### 1.3 环境

#### 1.3.1 运行时

| 层级 | 技术 | 版本 | 说明 |
|---|---|---|---|
| **操作系统** | Windows / macOS / Linux | — | 跨平台，当前主开发环境为 Windows |
| **后端运行时** | Python | 3.10+ | 纯标准库，零 pip 依赖 |
| **HTTP 服务器** | `http.server` | stdlib | 单线程+多线程混合，生产环境建议 systemd 托管 |
| **前端运行时** | 浏览器 | Chrome/Edge/Safari/Firefox | 纯原生 HTML/CSS/JS，零构建工具 |
| **AI 网关** | OpenClaw | v3+ | WebSocket 长连接，可选 |
| **文件系统** | 本地磁盘 | — | 所有数据以 JSON 文本存储 |

> **硬性约束**：不允许引入任何需要 `pip install` 的第三方包。标准库能解决的数据问题，绝不引入外部依赖。

#### 1.3.2 零依赖清单

```python
# 后端使用的全部导入（solobrave-server.py）
import os
import json
import time
import uuid
import re
import threading
import hashlib
import secrets
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
```

| 被禁止的依赖 | 替代方案 | 原因 |
|---|---|---|
| SQLite / MySQL / PostgreSQL / MongoDB | `json.dump()` + 文件系统 | 人类可读、零配置、备份即 `cp` |
| SQLAlchemy / Peewee | 无 | 不需要 ORM |
| Redis | `threading.Lock()` + 内存字典 | 单机部署不需要分布式缓存 |
| Flask / Django / FastAPI | `http.server.BaseHTTPRequestHandler` | 不需要路由框架 |
| WebSocket 库（后端） | 前端原生 `WebSocket` | 后端只暴露 HTTP API，WS 由 OpenClaw 网关处理 |

#### 1.3.3 部署方式

**开发模式（单命令启动）**

```bash
python solobrave-server.py --port 8080 --data ~/.solobrave-data
```

**生产模式（systemd）**

```ini
# /etc/systemd/system/solobrave.service
[Unit]
Description=SoloBrave AI Server
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/solobrave/solobrave-server.py --port 80 --data /var/lib/solobrave-data
Restart=always
User=solobrave

[Install]
WantedBy=multi-user.target
```

**数据备份**

```bash
# 备份 = 复制目录
cp -r ~/.solobrave-data /backup/solobrave-$(date +%Y%m%d)
# 恢复 = 复制回去
cp -r /backup/solobrave-20250101 ~/.solobrave-data
```

#### 1.3.4 前端环境

| 项 | 值 |
|---|---|
| 构建工具 | 无（零构建） |
| 框架 | 无（原生 DOM API） |
| CSS 方案 | 单文件内嵌 `<style>`，CSS 变量实现主题 |
| 状态管理 | 全局变量 + localStorage（仅 UI 状态） |
| 网络层 | 统一的 `apiFetch()` 函数（`fetch` 包装） |
| 文件体积 | `index.html` ~16,000 行，~580 KB |

> 前端不持有任何业务状态。`emps`、`groups`、`chatHistory` 等变量在内存中运行，刷新页面后从后端重新拉取。

#### 1.3.5 外部连接

```
┌─────────────┐     HTTP      ┌─────────────────┐
│  浏览器      │ ────────────→ │ solobrave-server │
│ (index.html) │ ←──────────── │   (Python)       │
└─────────────┘               └─────────────────┘
                                       │
                              ┌────────┴────────┐
                              │                 │
                              ▼                 ▼
                       ┌────────────┐    ┌─────────────┐
                       │ OpenClaw   │    │ AI API      │
                       │ WS Gateway │    │ (Kimi/      │
                       │ (可选)      │    │  OpenAI等)  │
                       └────────────┘    └─────────────┘
```

- **OpenClaw 网关**：WebSocket 长连接，提供流式推送、Agent 注册、模型管理
- **AI API 直连**：当 OpenClaw 不可用时，前端直接调用 Kimi/OpenAI/Claude 等 API
- **主大脑 ↔ 子Agent**：通过 systemPrompt 注入 `[TOOL_CALL]` 协议，主大脑捕获并执行 API 调用

---

## 二、数据目录结构

### 2.1 根目录

```
~/.solobrave-data/          # 默认路径，可通过 --data 参数覆盖
├── .secret                 # JWT 签名密钥（64 字节随机 hex）
├── users.json              # 用户账号体系（登录/权限）
├── agents.json             # AI 员工（Agent）完整配置
├── groups.json             # 群组/项目组配置
├── teams.json              # 团队/组织架构
├── settings.json           # 系统级设置
├── chats/                  # 聊天记录（按 Agent 隔离）
├── memory/                 # 记忆系统 v2（按员工隔离）
│   └── archive/            # 聊天溢出归档
├── knowledge/              # 知识库文档
├── products/               # 商品库
└── influencers/            # 达人库
```

> **备份策略**：`cp -r ~/.solobrave-data /backup/solobrave-$(date +%Y%m%d)`  
> **恢复策略**：复制回去即可，无需数据库导入工具。

### 2.2 认证层

#### `.secret`
```json
"7a3f9e2d8c1b4a5e6f..."
```

- **生成时机**：首次启动时自动生成
- **用途**：JWT HMAC-SHA256 签名密钥
- **安全要求**：不可泄露，备份时必须包含此文件

### 2.3 用户层

#### `users.json`
```json
{
  "users": [
    {
      "id": "usr_abc123",
      "username": "admin",
      "displayName": "管理员",
      "role": "admin",
      "passwordHash": "sha256$...",
      "createdAt": 1700000000000
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `role` | `admin` / `leader` / `employee` |
| `passwordHash` | `sha256$盐$哈希`，无 bcrypt/pbkdf2（零依赖原则） |

### 2.4 员工层（AI Agent）

#### `agents.json`
```json
{
  "agents": [
    {
      "id": "emp_xxx",
      "name": "产品经理-小明",
      "role": "产品经理",
      "bg": "#FF6B35",
      "avatar": "🧑‍💼",
      "status": "online",
      "archived": false,
      "apiProvider": "kimi",
      "apiKey": "sk-...",
      "systemPrompt": "你是...",
      "soulDoc": "SOUL.md 内容",
      "skills": [],
      "openclawName": "",
      "tokenStats": {"input": 0, "output": 0, "total": 0}
    }
  ]
}
```

- **容量**：无上限（但建议 <100 人，否则侧栏体验差）
- **敏感字段**：`apiKey` 明文存储（后端文件权限保护）

### 2.5 群组层

#### `groups.json`
```json
{
  "groups": [
    {
      "id": "grp_xxx",
      "name": "龙虾办公室",
      "leadAgentId": "emp_xxx",
      "members": ["emp_yyy", "emp_zzz"],
      "announcement": "项目目标...",
      "createdAt": 1700000000000
    }
  ]
}
```

### 2.6 聊天记录

#### `chats/{agent_id}.json`
```json
[
  {"id": "msg_1", "role": "user", "content": "你好", "time": 1700000000000},
  {"id": "msg_2", "role": "assistant", "content": "老板好！", "time": 1700000001000}
]
```

| 规则 | 说明 |
|---|---|
| 容量上限 | 500 条/文件，超出时归档旧消息到 `memory/archive/` |
| 写入方式 | 追加，原子替换（`tmp` → `replace`） |
| 消息 ID | `msg_` + 时间戳 + 随机后缀 |

#### `chats/{agent_id}_summary.json`
```json
{"summary": "用户主要询问产品规划...", "createdAt": "2024-01-01T12:00:00"}
```

#### `chats/group_{group_id}.json`
与 personal 格式相同，但消息带有 `senderId` / `senderName`。

### 2.7 记忆系统 v2

#### `memory/{emp_id}.json`
```json
{
  "version": "2.0",
  "core": [
    {"id": "mem_xxx", "key": "core", "value": "用户喜欢极简风格", "time": 1700000000000, "source": "归纳"}
  ],
  "daily": [
    {"id": "mem_yyy", "key": "auto", "value": "用户提到下周出差", "time": 1700000000000, "source": "AI提取", "archived": false},
    {"id": "mem_zzz", "key": "auto", "value": "用户问天气", "time": 1699000000000, "source": "AI提取", "archived": true, "archivedTime": 1700000000000}
  ]
}
```

| 池 | 上限 | TTL | 归档行为 |
|---|---|---|---|
| **core** | 100 条 | 无 | 手动管理 |
| **daily** | 100 条 | 30 天 | 过期自动标记 `archived=True`（可恢复） |

> **关键变更**：旧版 `archive` 独立字段已废弃，归档数据以 `archived=True` 标记保存在 `daily` 池中。

#### `memory/archive/{agent_id}.json`
```json
{
  "memories": [],
  "summaries": [
    {"id": "sum_xxx", "type": "chat_overflow", "content": "user: 你好\nassistant: 老板好", "createdAt": 1700000000000}
  ]
}
```

- **用途**：仅用于聊天记录溢出归档（非记忆归档）
- **类型**：`chat_overflow`（超 500 条）、`ai_summary`（AI 归纳摘要）

### 2.8 知识库

#### `knowledge/index.json`
```json
{
  "docs": [
    {
      "id": "doc_xxx",
      "name": "产品需求文档",
      "content": "...",
      "icon": "📄",
      "linkedEmployees": ["emp_xxx"],
      "createdAt": 1700000000000,
      "updatedAt": 1700000000000
    }
  ]
}
```

### 2.9 商品库 & 达人库

#### `products/index.json`
```json
{
  "products": [
    {
      "id": "prod_xxx",
      "name": "丝绒哑光口红",
      "sku": "SKU-001",
      "category": "美妆护肤",
      "price": 129.00,
      "stock": 350,
      "tags": ["口红", "哑光"],
      "status": "active"
    }
  ]
}
```

#### `influencers/index.json`
```json
{
  "influencers": [
    {
      "id": "inf_xxx",
      "name": "美妆博主A",
      "platform": "抖音",
      "followerCount": 500000,
      "category": "美妆护肤",
      "cooperationPrice": 5000,
      "engagementRate": 5.2,
      "tags": ["种草", "测评"]
    }
  ]
}
```

### 2.10 文件写入原子性

所有 JSON 文件采用**原子写入**：

```python
def _write_json(filepath, data):
    tmp = filepath + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)  # 原子替换
```

- 写入过程中崩溃 → 原文件不受影响
- 多线程同时写入 → 由 `threading.Lock()` 串行化

### 2.11 容量警戒线

| 资源 | 文件 | 建议上限 | 超限后果 |
|---|---|---|---|
| 单员工记忆 | `memory/{emp}.json` | ~50 KB | 加载变慢，不影响功能 |
| 单员工聊天 | `chats/{emp}.json` | 500 条 | 自动归档到 `memory/archive/` |
| 总员工数 | `agents.json` | 100 人 | 侧栏渲染卡顿 |
| 总商品数 | `products/index.json` | 10,000 | 搜索响应变慢 |
| 总达人数 | `influencers/index.json` | 10,000 | 匹配计算变慢 |

### 2.12 跨设备同步

由于所有数据都是 JSON 文件：

```bash
# 方式 1：rsync
rsync -avz ~/.solobrave-data/ macmini:~/.solobrave-data/

# 方式 2：git
cd ~/.solobrave-data && git init && git add . && git commit -m "sync"

# 方式 3：scp
scp -r ~/.solobrave-data/* user@server:~/.solobrave-data/
```

**注意**：`.secret` 必须同步，否则 JWT 令牌验证会失败。

---

## 五、匹配引擎算法

匹配引擎基于"商品-达人双向匹配"模型设计，核心目标是根据多维度特征计算匹配度，为运营决策提供排序建议。算法运行在 `solobrave-server.py` 中，零外部依赖，纯 Python 实现。

### 5.1 价格区间解析器

`_parse_price_range(price_range)` 负责将商品的价格描述字符串解析为 (min, max, avg) 三元组，供后续粉丝数档位判断使用。

**支持的格式：**

| 输入示例 | 解析结果 `(min, max, avg)` | 说明 |
|---|---|---|
| `100-200` | `(100, 200, 150)` | 标准区间 |
| `99` | `(99, 99, 99)` | 固定价格 |
| `200以下` | `(0, 200, 100)` | 上限区间，`avg = v/2` |
| `500以上` | `(500, 999999, 750)` | 下限区间，`avg = v*1.5` |
| `空/无效` | `(0, 999999, 100)` | 兜底默认值 |

**实现要点：**
- 正则优先匹配 `A-B`、`A`、`低于A`、`高于A` 四种模式
- 空格会被移除，支持中文关键词（低于/小于/以下/高于/大于/以上）
- 平均值用于粉丝数档位判断（见 5.2.4）

### 5.2 五维度评分算法

`_calculate_match_score(product, influencer) → (score, reasons)` 是匹配引擎的核心，总分范围 **0~100+**，由 5 个独立维度累加。

#### 维度 1：分类匹配（权重 25 分）

```python
if product['category'] == influencer['category']:
    score += 25
    reasons.append('分类一致')
else:
    reasons.append('分类不同')
```

- 分类字段为字符串精确匹配（如 `美妆` == `美妆`）
- 缺少分类信息时记为 `缺少分类信息`，不加分

#### 维度 2：标签匹配（权重 24 分）

```python
p_tags = set(t.lower() for t in (product.get('tags') or []))
i_tags = set(t.lower() for t in (influencer.get('tags') or []))
tag_common = p_tags & i_tags
tag_score = min(len(tag_common) * 8, 24)
```

- 标签大小写不敏感比较
- 每匹配一个标签 +8 分，**上限 24 分**（即最多 3 个标签贡献满分）
- 无匹配标签时记为 `无匹配标签`

#### 维度 3：价格匹配（权重 20 分）

```python
price_min, price_max, price_avg = self._parse_price_range(product.get('priceRange'))
inf_price = influencer.get('cooperationPrice', 0) or 0

if price_min <= inf_price <= price_max:
    score += 20          # 报价在区间内
elif price_min * 0.5 <= inf_price <= price_max * 1.5:
    score += 10          # 报价接近区间（±50%）
else:
    # 偏差较大，不加分
```

- 商品端存储价格区间字符串，达人端存储合作报价数字
- 判断逻辑以商品价格为基准，看达人报价是否落在合理范围
- 接近区间（0.5x ~ 1.5x）仍有部分加分，体现弹性匹配

#### 维度 4：粉丝数匹配（权重 5~25 分）

粉丝数评分与商品均价挂钩，分为三档：

| 商品均价 | 粉丝数门槛 | 加分 | 原因文本 |
|---|---|---|---|
| `< 100`（低价品） | ≥ 50,000 | +15 | 粉丝量充足 |
| | ≥ 10,000 | +10 | 粉丝量良好 |
| | ≥ 1,000 | +5 | 粉丝量一般 |
| `100 ~ 500`（中价品） | ≥ 200,000 | +20 | 粉丝量非常充足 |
| | ≥ 50,000 | +15 | 粉丝量充足 |
| | ≥ 10,000 | +10 | 粉丝量良好 |
| `≥ 500`（高价品） | ≥ 500,000 | +25 | 头部达人，粉丝量极佳 |
| | ≥ 200,000 | +20 | 粉丝量非常充足 |
| | ≥ 50,000 | +15 | 粉丝量充足 |

- 高价品允许满分 25 分，低价品最高 15 分，体现"价格越高对流量需求越大"的业务逻辑
- 未达最低门槛时记为对应档位的负面提示（如 `粉丝量较少`）

#### 维度 5：互动率加分（权重 15 分）

```python
engagement = influencer.get('engagementRate', 0) or 0
# 字符串 "%" 处理
if isinstance(engagement, str):
    engagement = engagement.replace('%', '').strip()
    engagement = float(engagement)
# 数值归一化（大于1视为百分比数值，如 5.2 → 0.052）
if engagement > 1:
    engagement = engagement / 100
```

| 互动率 | 加分 | 原因文本 |
|---|---|---|
| `≥ 10%` | +15 | 互动率极佳 (>10%) |
| `≥ 5%` | +10 | 互动率优秀 (>5%) |
| `≥ 2%` | +5 | 互动率良好 (>2%) |
| `< 2%` | 0 | 互动率一般 |

- 前端录入时可能存 `5.2%` 字符串，也可能存 `0.052` 数字，后端统一兼容
- 互动率是达人质量的关键信号，独立作为加分维度（不与粉丝数耦合）

### 5.3 分数汇总与上限

| 维度 | 最高分 |
|---|---|
| 分类匹配 | 25 |
| 标签匹配 | 24 |
| 价格匹配 | 20 |
| 粉丝数匹配 | 25 |
| 互动率加分 | 15 |
| **理论总分** | **109** |

实际业务中，同时满足所有满分条件的组合极少。前端展示时使用 `min(100, int(score))` 将百分比上限截断至 100%。

### 5.4 双向匹配接口

匹配引擎提供两个对称的 HTTP 端点：

#### POST /api/match/product-to-influencer

**功能：** 为指定商品寻找匹配的达人

**请求体：**
```json
{
  "productId": "prod_xxx",
  "minScore": 30,
  "limit": 10
}
```

**响应体：**
```json
{
  "product": { ... },
  "results": [
    {
      "influencer": { ... },
      "score": 78.0,
      "reasons": ["分类一致", "标签匹配 2 个", "报价在商品价格区间内", ...],
      "matchPercent": 78
    }
  ],
  "total": 5
}
```

#### POST /api/match/influencer-to-product

**功能：** 为指定达人寻找匹配的商品

**请求体：**
```json
{
  "influencerId": "inf_xxx",
  "minScore": 30,
  "limit": 10
}
```

**响应结构与上面对称，只是 `results[].product` 替代 `results[].influencer`。**

#### 共同行为

- `minScore` 过滤：只返回分数 ≥ minScore 的结果（默认 0，不过滤）
- `limit` 截断：默认返回前 10 条，可调整
- `status == 'inactive'` 的达人/商品会被自动排除
- 结果按 `score` 降序排列

### 5.5 达人搜索接口（独立）

`POST /api/influencers/search` 是另一套面向人工筛选的搜索逻辑，与匹配引擎评分体系不同：

| 匹配项 | 加分 |
|---|---|
| 名称包含关键词 | +10 |
| 账号精确匹配 | +12 |
| 平台一致 | +7 |
| 分类一致 | +8 |
| 标签交集 | +5/个 |

- 支持 `minFollowers`、`maxFollowers`、`minPrice`、`maxPrice`、`minEngagement`、`status` 等硬过滤条件
- 结果同样按 score 降序排列，默认 limit=20

### 5.6 算法特性与局限

**当前特性：**
- 规则驱动，无机器学习，可解释性强
- 五维度独立累加，调整某维度权重不影响其他
- 双向对称：`match(A,B) == match(B,A)`

**已知局限：**
- 分类匹配是精确字符串比较，不支持层级（如 `美妆` 与 `护肤` 不算匹配）
- 标签匹配无语义扩展，仅字面比较
- 价格匹配单向以商品为基准，达人视角未考虑"达人想接什么价位的单"
- 粉丝数档位阈值硬编码，未根据实际业务数据校准
- 无历史合作效果反馈闭环（如"该达人带货转化率"未纳入评分）

**未来可优化方向：**
- 引入分类层级映射（如 `美妆 > 护肤 > 面膜`）
- 标签向量化 / 同义词扩展
- 根据历史成交数据动态调整各维度权重
- 达人历史带货品类偏好建模

---

## 六、Agent 工具调用协议

SoloBrave 采用"主大脑管控资源、子 Agent 申请调用"的协作模型。商品库、达人库、匹配引擎由主大脑（后端 API）统一管理，各 AI 员工（子 Agent）不直接操作数据，而是通过标准化的 `[TOOL_CALL]` 协议向主大脑发起请求，由主大脑执行并返回结果。

### 6.1 协议格式

工具调用采用**标记包裹的 JSON** 格式，嵌入在 AI 回复文本末尾：

```
[TOOL_CALL]{"action":"list_products"}[/TOOL_CALL]
[TOOL_CALL]{"action":"match_product","params":{"productId":"prod_abc123"}}[/TOOL_CALL]
```

**格式规范：**
- 标记：`[TOOL_CALL]` 开始，`[/TOOL_CALL]` 结束
- 内容：合法的 JSON 对象，必须包含 `action` 字段
- `params`：可选， action 所需的参数对象
- 位置：通常放在回复末尾，用户不可见（前端渲染时会移除标记）
- 多条：单条回复可包含多个 `[TOOL_CALL]` 块，按顺序执行

### 6.2 支持的 Action 清单

| Action | 参数 | 调用后端 API | 功能说明 |
|---|---|---|---|
| `list_products` | 无 | `GET /api/products` | 查询商品库列表（取前10条展示） |
| `list_influencers` | 无 | `GET /api/influencers` | 查询达人库列表（取前10条展示） |
| `match_product` | `productId` (string) | `POST /api/match/product-to-influencer` | 为指定商品智能匹配达人（top 5） |
| `match_influencer` | `influencerId` (string) | `POST /api/match/influencer-to-product` | 为指定达人智能匹配商品（top 5） |

**前端执行器伪代码：**

```javascript
async function executeToolCall(call) {
  switch (call.action) {
    case 'list_products':
      var data = await apiFetch('/api/products').then(r => r.json());
      return '📦 商品库当前共有 N 个商品：\n- 商品名 (¥价格, 分类, 库存)';
    case 'list_influencers':
      var data = await apiFetch('/api/influencers').then(r => r.json());
      return '🎙️ 达人库当前共有 N 个达人：\n- 达人名 (平台, 粉丝数, 报价)';
    case 'match_product':
      var data = await apiFetch('/api/match/product-to-influencer', {
        method: 'POST', body: JSON.stringify({productId: call.params.productId, limit: 5})
      }).then(r => r.json());
      return '🔗 为商品「X」匹配到 N 个达人：\n- 达人名 (匹配度X分, 原因)';
    case 'match_influencer':
      var data = await apiFetch('/api/match/influencer-to-product', {
        method: 'POST', body: JSON.stringify({influencerId: call.params.influencerId, limit: 5})
      }).then(r => r.json());
      return '🔗 为达人「X」匹配到 N 个商品：\n- 商品名 (匹配度X分)';
  }
}
```

### 6.3 Prompt 注入策略

工具调用能力通过 **systemPrompt 追加** 的方式告知 AI 模型，不修改模型本身。

**注入内容模板：**

```
【系统资源（由主大脑管理，你可请求调用）】
你作为AI员工，可以通过主大脑访问以下资源。当用户有相关需求时，你可以主动提出帮忙操作。

1. 商品库 (/api/products)
   - 查询商品列表、按分类/标签搜索
   - 录入新商品、编辑商品信息
   - 每个商品有：名称、SKU、分类、价格、库存、标签、状态

2. 达人库 (/api/influencers)
   - 查询达人列表、按平台/分类/粉丝数筛选
   - 录入新达人、编辑达人信息
   - 每个达人有：名称、平台、账号、粉丝数、分类、报价、互动率、标签

3. 匹配引擎 (/api/match)
   - 为指定商品智能匹配适合的推广达人
   - 为指定达人智能匹配适合推广的商品
   - 匹配维度：分类、标签、价格区间、粉丝数、互动率

当你需要执行以上操作时，请在回复末尾使用以下格式（用户看不到这段标记）：
[TOOL_CALL]{"action":"list_products"}[/TOOL_CALL]
或 [TOOL_CALL]{"action":"match_product","params":{"productId":"prod_xxx"}}[/TOOL_CALL]
主大脑会自动执行并把结果展示给用户。
【注意】禁止编造不存在的商品或达人信息，所有数据以主大脑查询结果为准。
```

**注入时机：**
- 个人聊天模式：构造 systemPrompt 时追加（约 line 3896）
- 群聊模式：同样追加（约 line 12104）
- 所有 AI 员工共享同一份工具上下文，无差异化权限

### 6.4 执行流程（时序）

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  用户   │────→│ AI模型  │────→│ 前端UI  │────→│ 后端API │
│ (提问)  │     │(生成回复)│     │(解析执行)│     │(数据查询)│
└─────────┘     └─────────┘     └─────────┘     └─────────┘
                                          ↑        │
                                          └────────┘
                                          结果回调
```

**详细步骤：**

1. **用户提问**（如："帮我看看口红商品适合找哪些达人推广"）
2. **AI 推理** — 模型识别意图，决定调用 `match_product`，在回复末尾生成 `[TOOL_CALL]...[/TOOL_CALL]`
3. **前端解析** — `parseToolCalls()` 用正则 `/\[TOOL_CALL\]([\s\S]*?)\[\/TOOL_CALL\]/g` 提取所有工具调用块
4. **渲染 AI 回复** — 移除标记后的纯文本展示给用户
5. **异步执行** — `executeToolCall()` 调用对应后端 API（携带认证头）
6. **结果展示** — 以"🧠 主大脑 / 系统"身份，用特殊样式气泡追加到聊天记录
7. **群聊同步** — 如为群聊，AI 原始回复（含工具标记）同步到后端群聊历史；工具执行结果仅前端展示，不入群聊记录

### 6.5 安全与约束

| 层面 | 机制 |
|---|---|
| **认证** | 所有后端 API 调用通过 `apiFetch()`，自动注入 `X-User-ID`、`X-Admin-Token` 等认证头；后端 handler 统一调用 `_authenticate()` |
| **防伪造** | systemPrompt 明确提示"禁止编造不存在的商品或达人信息"，执行结果以主大脑查询为准 |
| **参数校验** | `executeToolCall` 内对必要参数做空值检查（如 `productId`、`influencerId`），缺失时返回失败提示 |
| **作用域隔离** | 工具调用只涉及读取和匹配操作，无写入/删除能力；录入/编辑需用户手动在 UI 操作 |
| **错误处理** | API 异常被 try-catch 捕获，返回 `【工具执行失败】错误信息`，不阻断聊天流程 |

### 6.6 UI 表现

**AI 员工回复：**
- 正常文本气泡，发送者显示为 AI 员工名称和角色

**主大脑执行结果：**
- 发送者：🧠 主大脑 / 系统
- 样式：`background: var(--accent-light); color: var(--accent); font-size: 13px; white-space: pre-wrap;`
- 内容：纯文本结果（如商品列表、匹配结果），保留换行格式
- 位置：紧跟在触发工具调用的 AI 回复下方

### 6.7 扩展设计

当前仅实现 4 个读取/匹配类 action，未来可按相同协议扩展：

| 潜在 Action | 说明 |
|---|---|
| `search_products` | 按关键词搜索商品（需 `query` 参数） |
| `search_influencers` | 按条件筛选达人（需 `filters` 参数） |
| `get_product_detail` | 查询单个商品详情（需 `productId`） |
| `get_influencer_detail` | 查询单个达人详情（需 `influencerId`） |
| `create_product` | 录入新商品（需完整商品参数）—— 建议保留人工确认 |
| `update_product` | 更新商品信息 —— 建议保留人工确认 |

**扩展原则：** 保持 `[TOOL_CALL]{action, params}[/TOOL_CALL]` 格式不变，只需在 `executeToolCall()` 中新增 case 分支，并在 `buildAgentToolsContext()` 中补充说明。

---

## 七、前端架构

SoloBrave 前端采用**单文件纯原生架构**，所有代码集中在 `index.html` 一个文件中，无框架、无构建工具、无 npm 依赖。这种设计服务于"零配置开箱即用"的部署目标。

### 7.1 技术栈

| 层级 | 技术 | 说明 |
|---|---|---|
| 结构 | HTML5 | 单文件，~16178 行 |
| 样式 | CSS3 + CSS 变量 | 主题系统基于 CSS 自定义属性 |
| 逻辑 | Vanilla JavaScript (ES6+) | async/await、箭头函数、解构等现代语法 |
| 网络 | Fetch API | 原生 fetch，无 axios 等封装库 |
| 存储 | localStorage（极少）+ 后端 JSON | 仅存认证 token、主题偏好 |

### 7.2 单文件组织方式

`index.html` 按功能区域自上而下排列：

```html
<!DOCTYPE html>
<html>
<head>
  <!-- Meta、CSS 变量、全局样式（~3000 行） -->
</head>
<body>
  <!-- 登录页 -->
  <!-- 主应用容器 -->
  <!--   - 侧边栏（员工列表、群组列表） -->
  <!--   - 聊天区域（个人/群聊） -->
  <!--   - 任务看板 -->
  <!--   - 抽屉面板（员工详情、设置） -->
  <!--   - 模态框（商品库、达人库、匹配弹窗、向导） -->
  <!-- 全局脚本（~11000 行） -->
</body>
</html>
```

**为什么没有拆分？**
- 部署只需复制一个文件，无构建步骤
- 后端 `http.server` 直接伺服静态文件
- 开发阶段用编辑器书签/折叠即可导航

### 7.3 状态管理

前端没有 Redux/Vuex 等状态管理库，采用**全局变量 + API 同步**的轻量模式。

#### 运行时全局状态

```javascript
var emps = [];              // 员工列表（内存主副本）
var groups = [];            // 群组列表
var currentUser = null;     // 当前登录用户
var currentEmpId = null;    // 当前选中的员工
var currentGroupId = null;  // 当前选中的群组
var authToken = null;       // 认证令牌
var themeScheme = 'auto';   // 主题模式：light/dark/auto
```

#### 数据持久化策略

| 数据类型 | 存储位置 | 同步机制 |
|---|---|---|
| 员工元数据（name/role/model等） | 后端 `/api/agents` | 修改后 3 秒 debounce 同步 |
| 员工运行时状态（status/msg/lastActive） | 仅内存 | **不同步到后端** |
| 群组信息 | 后端 `/api/groups` | 实时 CRUD |
| 聊天历史 | 后端 `/api/chat/:id` / `/api/groups/:id/history` | 实时读写 |
| 记忆数据 | 后端 `/api/memory/:empId` | 实时读写 |
| 商品/达人/知识库 | 后端对应 API | 实时读写 |
| 认证 token | localStorage `sb_auth_token` | 登录时写入，过期清除 |
| 主题偏好 | localStorage `sb_theme` | 切换时即时写入 |

#### 员工同步细节

```javascript
function saveEmployees() {
  // 1. 通知其他模块数据已更新
  notifyOfficeEmployeesUpdated();
  // 2. 延迟同步到服务器（debounce 3秒）
  if (_saveEmpTimer) clearTimeout(_saveEmpTimer);
  _saveEmpTimer = setTimeout(function () {
    emps.forEach(function (emp) {
      syncEmpToServer(emp);
    });
  }, 3000);
}

async function syncEmpToServer(emp) {
  var syncData = Object.assign({}, emp);
  // 运行时状态不应持久化
  delete syncData.status;
  delete syncData.msg;
  delete syncData.lastActive;
  await apiFetch('/api/agents/' + emp.id, {
    method: 'PUT', body: JSON.stringify(syncData)
  });
}
```

**关键设计：**
- 运行时状态（如 `thinking`、`working` 等临时状态）被显式剔除，避免后端存储无意义的瞬时值
- Debounce 防止快速连续修改（如批量编辑）导致频繁 API 调用
- 新建员工时走 `syncNewEmpToServer()` 直接 POST，不走 debounce

### 7.4 API 封装层

`apiFetch()` 是所有后端通信的统一入口：

```javascript
async function apiFetch(url, options = {}) {
  const token = localStorage.getItem('sb_auth_token');
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  };
  if (token && token !== 'null' && token !== 'local_mode') {
    headers['Authorization'] = 'Bearer ' + token;
  }
  const resp = await fetch(url, { ...options, headers });
  if (resp.status === 401) {
    doLogout();        // Token 过期，自动跳转登录
    return null;
  }
  if (resp.status === 403) {
    // 权限不足静默返回空数组（如子账号访问他人聊天记录）
    console.warn('[apiFetch] 403 Forbidden:', url);
    return new Response('[]', { status: 200 });
  }
  if (!resp.ok) throw new Error('HTTP ' + resp.status);
  return resp;
}
```

**特性：**
- 自动注入 `Authorization: Bearer <token>` 头
- 401 自动登出，无需手动处理
- 403 静默降级（返回空数组而非抛错），避免子账号权限边界导致 UI 崩溃
- 所有业务代码统一使用，无分散的 `fetch()` 调用

### 7.5 主题系统

基于 CSS 变量实现，支持 light/dark/auto 三模式：

```javascript
function initTheme() {
  const saved = localStorage.getItem('sb_theme');
  if (saved) themeScheme = saved;
  applyTheme();
  window.matchMedia('(prefers-color-scheme: dark)')
    .addEventListener('change', function () {
      if (themeScheme === 'auto') applyTheme();
    });
}
```

**CSS 变量示例：**

```css
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f5f5f5;
  --text-primary: #1a1a1a;
  --accent: #FF6B35;
  --accent-light: #fff3ed;
}
[data-theme="dark"] {
  --bg-primary: #1a1a1a;
  --bg-secondary: #2a2a2a;
  --text-primary: #e0e0e0;
}
```

- 主题类名挂载在 `<html data-theme="...">` 上
- `auto` 模式监听系统 `prefers-color-scheme`
- 用户选择持久化到 localStorage

### 7.6 视图与路由

无前端路由库，采用**条件渲染 + DOM 显示控制**：

```javascript
function showMainApp() {
  document.getElementById('loginPage').style.display = 'none';
  document.getElementById('mainApp').style.display = 'flex';
  loadEmployees();   // 拉取员工列表
  loadGroups();      // 拉取群组列表
}

function switchToChat(empId) {
  currentGroupId = null;
  currentEmpId = empId;
  // 加载该员工的聊天历史
  loadChatFromServer(empId, 'personal', renderMessages);
}

function switchToGroup(groupId) {
  currentEmpId = null;
  currentGroupId = groupId;
  // 加载群组历史
}
```

**面板管理：**
- 任务看板：`toggleTaskBoard()` 切换 `chatArea` 与 `taskBoard` 的 `display`
- 抽屉面板：`openDrawer() / closeDrawer()` 控制 CSS class `open`
- 模态框：动态插入/移除 DOM，或用 `display` 控制

### 7.7 主要功能模块

| 模块 | 核心函数 | 说明 |
|---|---|---|
| **认证** | `checkAuth()`, `doLogout()`, `doLogin()` | Token 验证、登录态维持 |
| **员工管理** | `loadEmployees()`, `saveEmployees()`, `syncEmpToServer()`, `renderEmployeeList()` | CRUD、状态徽章、3D 头像 |
| **群组管理** | `loadGroups()`, `saveGroups()`, `createGroup()`, `openGroupChat()` | 成员管理、群聊历史 |
| **个人聊天** | `sendMessage()`, `loadChatFromServer()`, `displayAIReply()` | 与单员工对话、AI 流式回复 |
| **群聊** | `sendGroupMessage()`, `displayGroupAIReply()`, `parseAndTriggerMentions()`, `pollAgentsForReply()` | 多 AI 讨论、@链式触发 |
| **记忆系统** | `loadMemoryFromBackend()`, `saveMemoryToBackend()` | v2 分池读写、归档标签页 |
| **商品库** | `openProductLibrary()`, `renderProducts()`, `saveProduct()` | 录入/搜索/匹配入口 |
| **达人库** | `openInfluencerLibrary()`, `renderInfluencers()`, `saveInfluencer()` | 录入/搜索/匹配入口 |
| **匹配引擎** | `showMatchResultModal()` | 弹窗展示匹配结果 |
| **知识库** | `loadKnowledgeDocs()`, `saveKnowledgeDoc()` | 后端文档管理 |
| **全局搜索** | `initGlobalSearch()` | 跨员工/群组/消息搜索 |
| **Agent 工具** | `buildAgentToolsContext()`, `parseToolCalls()`, `executeToolCall()` | [TOOL_CALL] 协议执行 |

### 7.8 组件化模式

虽然没有 React/Vue 组件系统，但采用**函数级组件**模式复用 UI：

```javascript
// 头像渲染（多处复用）
function renderAvatar(emp, size) { ... }

// 消息内容格式化（链接、代码块、换行）
function formatMessageContent(text) { ... }

// 状态文本/图标转换
function getStatusText(status) { ... }
function getStatusIcon(status) { ... }

// 员工角色显示
function getEmpRoleDisplay(emp) { ... }

// 头像选择器
function renderAvatarPicker() { ... }
```

**渲染模式：**
- 模板字符串拼接 HTML，用 `insertAdjacentHTML('beforeend', html)` 插入
- 更新时通常重新渲染整个列表（如 `renderEmployeeList()`），而非细粒度 diff
- 数据量小（员工 < 50 人，消息列表有分页），全量渲染性能可接受

### 7.9 消息渲染与去重

群聊场景存在竞态风险（多个 AI 同时回复），前端实现了 DOM 级去重：

```javascript
// 检查最近 3 条消息是否已有相同 sender + content
var recentMsgs = area.querySelectorAll('.msg');
for (var i = recentMsgs.length - 1, count = 0; i >= 0 && count < 3; i--, count--) {
  var m = recentMsgs[i];
  var nameEl = m.querySelector('.msg-sender-name');
  var bubbleEl = m.querySelector('.msg-bubble');
  if (nameEl && bubbleEl && nameEl.textContent === senderName 
      && bubbleEl.textContent === cleanText) {
    isDuplicateDom = true;
    break;
  }
}
if (isDuplicateDom) return;  // 跳过插入
```

### 7.10 安全与防御

| 措施 | 实现 |
|---|---|
| XSS 防护 | `escapeHtml()` / `escapeAttr()` 在插入 DOM 前转义 |
| CSRF 防护 | 依赖 `Authorization: Bearer` Token，无 cookie 会话 |
| 认证拦截 | `apiFetch()` 统一处理 401，自动登出 |
| 权限降级 | 403 静默返回空数组，避免子账号越界崩溃 |
| 输入校验 | 后端二次校验，前端仅做基础非空检查 |

### 7.11 性能特征

| 指标 | 现状 | 说明 |
|---|---|---|
| 首屏加载 | ~200KB（单 HTML） | 无 JS bundle 拆分，一次请求 |
| 运行时内存 | 低 | 无框架运行时开销 |
| 列表渲染 | 全量重新渲染 | 数据量小，无需虚拟滚动 |
| API 调用 | Debounce 合并 | 员工编辑 3 秒防抖 |
| 图片资源 | 内联 SVG + 本地 PNG | 无 CDN 依赖 |

---

## 八、部署与运维

SoloBrave 采用极简部署模型：单 Python 进程 + 单 HTML 文件 + JSON 数据目录，无需容器、无需反向代理、无需数据库服务。

### 8.1 部署架构

```
┌─────────────────┐
│   用户浏览器    │◄── 访问 http://host:8080
└────────┬────────┘
         │
┌────────▼────────┐
│ solobrave-server │  Python 3.10+ (单进程)
│  ├─ http.server  │  静态文件伺服 (index.html)
│  ├─ API handlers │  业务逻辑处理
│  └─ JSON I/O     │  文件读写
└────────┬────────┘
         │
┌────────▼────────┐
│ ~/.solobrave-data/│  本地数据目录
│  ├─ agents/      │  员工配置
│  ├─ chat/        │  聊天记录
│  ├─ memory/      │  记忆数据
│  ├─ knowledge/   │  知识库
│  ├─ products/    │  商品库
│  ├─ influencers/ │  达人库
│  └─ users.json   │  用户账户
└─────────────────┘
```

### 8.2 环境要求

| 组件 | 最低版本 | 说明 |
|---|---|---|
| Python | 3.10+ | 仅使用标准库，零 pip 依赖 |
| 操作系统 | macOS / Linux / Windows | 跨平台，开发用 Windows，生产用 macOS |
| 磁盘空间 | 100MB+ | 程序极小，数据随使用增长 |
| 内存 | 512MB+ | 单进程低内存占用 |

### 8.3 启动方式

#### 开发环境（Windows）

```bash
cd D:/CoPaw/workspace/workspaces/default/solobrave
git checkout dev
python solobrave-server.py
```

默认监听 `0.0.0.0:8080`，浏览器访问 `http://localhost:8080`。

#### 生产环境（Mac mini）

```bash
cd ~/Desktop/solobrave
git checkout main
python3 solobrave-server.py
```

建议后台运行：

```bash
nohup python3 solobrave-server.py > server.log 2>&1 &
```

### 8.4 systemd 服务配置（推荐用于长期运行）

创建 `/etc/systemd/system/solobrave.service`：

```ini
[Unit]
Description=SoloBrave Server
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/Desktop/solobrave
ExecStart=/usr/bin/python3 solobrave-server.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
sudo systemctl enable solobrave
sudo systemctl start solobrave
sudo systemctl status solobrave
```

### 8.5 数据目录管理

**数据路径：** `~/.solobrave-data/`（用户主目录下）

**环境隔离：**

| 环境 | 数据目录 | 隔离方式 |
|---|---|---|
| 开发 | `C:\Users\<User>\.solobrave-data\` | Windows 本地 |
| 生产 | `/Users/<User>/.solobrave-data/` | macOS 本地 |
| 多用户 | 同一目录 | 靠 `users.json` 和 `createdBy` 字段区分 |

**注意：** 不同机器的数据目录互不共享，生产环境数据需单独备份。

### 8.6 备份策略

由于所有数据为 JSON 文本文件，备份极为简单：

```bash
# 全量备份
tar czvf solobrave-backup-$(date +%Y%m%d).tar.gz ~/.solobrave-data/

# 增量备份（按时间戳）
rsync -av ~/.solobrave-data/ /backup/solobrave/$(date +%Y%m%d)/
```

**建议：**
- 每日自动备份一次数据目录
- 代码通过 git 管理，`main` 分支即生产版本
- 备份时无需停服，文件写入为原子操作（`.tmp` + `os.replace()`），不会读到半写状态

### 8.7 日志与监控

**当前日志输出：**
- 全部通过 `print()` 输出到控制台，包含 `flush=True` 确保实时
- 关键日志前缀：
  - `[Auth]` — 认证相关
  - `[AgentSync]` — 员工同步
  - `[ChatGET]` / `[ChatFilter]` — 聊天查询
  - `[Memory]` — 记忆操作
  - `[Knowledge]` — 知识库操作
  - `[Product]` / `[Influencer]` — 商品/达人库操作

**日志重定向：**

```bash
python3 solobrave-server.py 2>&1 | tee -a solobrave.log
```

**当前无以下能力（未来可扩展）：**
- 结构化日志（JSON 格式）
- 日志轮转（logrotate）
- 性能指标采集（Prometheus / Grafana）
- 健康检查端点（`/health`）
- 告警通知

### 8.8 版本管理与发布流程

**分支策略：**

| 分支 | 用途 | 保护 |
|---|---|---|
| `main` | 生产环境 | 禁止直接推送，需 PR 合并 |
| `dev` | 开发/测试 | 日常开发分支 |

**发布步骤：**

```bash
# 1. 在 dev 分支完成开发和自测
git add .
git commit -m "feat: xxx"
git push origin dev

# 2. 合并到 main（通过 PR 或手动 merge）
git checkout main
git merge dev --no-ff
git push origin main

# 3. 在生产环境拉取更新
cd ~/Desktop/solobrave
git pull origin main
# 重启服务（如使用 systemd：sudo systemctl restart solobrave）
```

### 8.9 故障排查

| 现象 | 可能原因 | 排查方法 |
|---|---|---|
| 无法启动，端口占用 | 8080 被其他进程占用 | `lsof -i :8080` 或修改启动端口 |
| 401 频繁跳转登录 | Token 过期或 `users.json` 损坏 | 检查 `localStorage` 中的 `sb_auth_token`，或重置 `users.json` |
| 数据丢失 | 文件写入中断或磁盘满 | 检查 `~/.solobrave-data/` 目录权限和磁盘空间 |
| AI 回复慢 | 外部 API 延迟 | 查看控制台 `[Chat]` 日志中的耗时 |
| 群聊消息重复 | 竞态条件 / 轮询冲突 | 检查 `displayGroupAIReply` 的 DOM 去重日志 |
| 匹配引擎无结果 | 商品/达人库为空 | 确认 `products/products.json` 和 `influencers/influencers.json` 有数据 |

### 8.10 扩容与限制

**当前架构限制：**
- 单进程单线程（`http.server` 基于 `socketserver.ThreadingMixIn` 实际为多线程，但 Python GIL 限制 CPU 并行）
- 无负载均衡，单实例承载
- 数据文件无分片，大文件（如聊天记录）全量加载

**适用规模：**
- 并发用户：~10 人（个人/小团队使用）
- 员工数量：~50 个 AI 员工
- 聊天记录：单员工 ~1000 条（历史消息按时间倒序，新消息追加）
- 商品/达人库：~1000 条（内存全量加载，搜索遍历）

**如需扩容：**
- 将 `http.server` 替换为 `uvicorn` + `FastAPI`（需引入外部依赖）
- 数据分片：按用户/时间拆分 JSON 文件
- 引入 SQLite 替代纯 JSON 文件（仍为零外部服务，但查询性能更好）

---

## 三、API 规范

SoloBrave 后端目前提供约 **60 个 HTTP 端点**，全部基于 `http.server.BaseHTTPRequestHandler` 手写路由，无框架依赖。所有业务 API 统一执行 `_authenticate()` 认证。

### 3.1 接口总览（按模块）

#### 认证模块 (Auth)

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| POST | `/api/auth/login` | 用户登录，返回 JWT Token | 否 |
| POST | `/api/auth/register` | 用户注册 | 否 |
| POST | `/api/auth/change-password` | 修改密码 | 是 |
| GET | `/api/auth/me` | 获取当前登录用户信息 | 是 |

#### OpenClaw 网关模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/openclaw/status` | 网关连接状态 | 是 |
| GET | `/api/openclaw/agents` | 列出已注册 Agent | 是 |
| GET | `/api/openclaw/models` | 列出可用模型 | 是 |
| GET | `/api/openclaw/skills/list` | 列出已安装技能 | 是 |
| GET | `/api/openclaw/skills/search` | 搜索技能市场 | 是 |
| GET | `/api/openclaw/channels/feishu/status` | 飞书通道状态 | 是 |
| GET | `/api/openclaw/dreaming` | 获取梦境状态 | 是 |
| POST | `/api/openclaw/agents/create` | 创建 OpenClaw Agent | 是 |
| POST | `/api/openclaw/agents/update` | 更新 OpenClaw Agent | 是 |
| POST | `/api/openclaw/skills/install` | 安装技能 | 是 |
| POST | `/api/openclaw/skills/remove` | 移除技能 | 是 |
| POST | `/api/openclaw/channels/feishu` | 配置飞书通道 | 是 |
| POST | `/api/openclaw/pairing/approve` | 审批设备配对 | 是 |
| POST | `/api/openclaw/gateway/restart` | 重启网关 | 是 |
| POST | `/api/openclaw/dreaming` | 触发梦境 | 是 |
| POST | `/api/openclaw/write-agent-docs` | 写入 Agent 文档 | 是 |
| POST | `/api/openclaw/write-soul` | 写入 Soul 配置 | 是 |
| DELETE | `/api/openclaw/agents/:name` | 删除 OpenClaw Agent | 是 |

#### 用户管理模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/users` | 列出所有用户 | 是 |
| GET | `/api/users/:id` | 获取单个用户 | 是 |
| PUT | `/api/users/:id` | 更新用户信息 | 是 |
| PUT | `/api/users/:id/role` | 更新用户角色 | 是 |
| DELETE | `/api/users/:id` | 删除用户 | 是 |
| GET | `/api/users/:id/subordinates` | 获取下属列表 | 是 |

#### 员工/Agent 模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/agents` | 列出所有员工 | 是 |
| GET | `/api/agents/:id` | 获取员工详情 | 是 |
| POST | `/api/agents` | 创建员工 | 是 |
| PUT | `/api/agents/:id` | 更新员工配置 | 是 |
| DELETE | `/api/agents/:id` | 删除员工 | 是 |

#### 群组模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/groups` | 列出所有群组 | 是 |
| GET | `/api/groups/:id` | 获取群组详情 | 是 |
| GET | `/api/groups/:id/history` | 获取群聊历史 | 是 |
| POST | `/api/groups` | 创建群组 | 是 |
| POST | `/api/groups/:id/history` | 发送群聊消息 | 是 |
| POST | `/api/groups/:id/chat` | 群组 AI 对话 | 是 |
| POST | `/api/groups/:id/members` | 添加群成员 | 是 |
| PUT | `/api/groups` | 批量保存群组 | 是 |
| PUT | `/api/groups/:id` | 更新群组信息 | 是 |
| DELETE | `/api/groups/:id/members/:memberId` | 移除群成员 | 是 |
| DELETE | `/api/groups/:id` | 删除群组 | 是 |

#### 团队模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/teams` | 列出所有团队 | 是 |
| GET | `/api/teams/:id` | 获取团队详情 | 是 |
| GET | `/api/teams/:id/members/:memberId` | 获取团队成员 | 是 |
| POST | `/api/teams` | 创建团队 | 是 |
| POST | `/api/teams/:id/members` | 添加团队成员 | 是 |
| PUT | `/api/teams/:id` | 更新团队信息 | 是 |
| DELETE | `/api/teams/:id/members/:memberId` | 移除团队成员 | 是 |
| DELETE | `/api/teams/:id` | 删除团队 | 是 |

#### 聊天模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/chat/:agentId` | 获取个人聊天记录 | 是 |
| GET | `/api/chat/:agentId/summarize` | 获取聊天摘要 | 是 |
| POST | `/api/chat/:agentId` | 发送消息 | 是 |
| POST | `/api/chat/:agentId/summarize` | 触发聊天归纳 | 是 |
| DELETE | `/api/chat/:agentId/:msgId` | 删除单条消息 | 是 |
| DELETE | `/api/chat/:agentId` | 清空聊天记录 | 是 |

#### 记忆系统 v2 模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/memory/:empId` | 获取分池记忆（含归档） | 是 |
| POST | `/api/memory/:empId` | 添加记忆 | 是 |
| POST | `/api/memory/:empId/archive` | 手动触发过期归档 | 是 |
| POST | `/api/memory/:empId/:memId/promote` | 升级为核心记忆 | 是 |
| POST | `/api/memory/:empId/:memId/restore` | 从归档恢复 | 是 |
| PUT | `/api/memory/:empId/:memId` | 修改/跨池移动记忆 | 是 |
| DELETE | `/api/memory/:empId/:memId` | 删除记忆 | 是 |

#### 知识库模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/knowledge` | 列出所有文档 | 是 |
| POST | `/api/knowledge` | 创建文档 | 是 |
| PUT | `/api/knowledge/:id` | 更新文档 | 是 |
| DELETE | `/api/knowledge/:id` | 删除文档 | 是 |

#### 商品库模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/products` | 列出商品 | 是 |
| GET | `/api/products/search` | 搜索商品 | 是 |
| POST | `/api/products` | 录入商品 | 是 |
| POST | `/api/products/search` | 高级搜索 | 是 |
| PUT | `/api/products/:id` | 更新商品 | 是 |
| DELETE | `/api/products/:id` | 删除商品 | 是 |

#### 达人库模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/influencers` | 列出达人 | 是 |
| GET | `/api/influencers/search` | 搜索达人 | 是 |
| POST | `/api/influencers` | 录入达人 | 是 |
| POST | `/api/influencers/search` | 高级搜索/匹配 | 是 |
| PUT | `/api/influencers/:id` | 更新达人 | 是 |
| DELETE | `/api/influencers/:id` | 删除达人 | 是 |

#### 匹配引擎模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| POST | `/api/match/product-to-influencer` | 为商品匹配达人 | 是 |
| POST | `/api/match/influencer-to-product` | 为达人匹配商品 | 是 |

#### 抖音解析模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| POST | `/api/douyin/parse` | 解析抖音分享链接 | 是 |
| POST | `/api/douyin/transcribe` | 视频语音转文字 | 是 |

#### 代理模块

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| POST | `/api/proxy` | HTTP 代理转发 | 是 |

#### 健康检查

| 方法 | 路径 | 功能 | 认证 |
|---|---|---|---|
| GET | `/api/health` | 服务健康状态 | 否 |

### 3.2 通用响应格式

**成功响应：**

```json
{
  "data": { ... },
  "total": 42,
  "page": 1
}
```

或简写为直接返回资源对象：

```json
{
  "id": "emp_xxx",
  "name": "..."
}
```

**错误响应：**

```json
{
  "error": "错误描述",
  "pool": "daily",
  "max": 100,
  "suggestion": "Archive or delete old memories first"
}
```

**HTTP 状态码约定：**

| 状态码 | 含义 | 典型场景 |
|---|---|---|
| 200 | 成功 | 正常返回 |
| 201 | 已创建 | 资源创建成功（部分接口） |
| 400 | 请求参数错误 | 缺少必填字段、格式不对 |
| 401 | 未认证 | Token 缺失或过期 |
| 403 | 权限不足 | 子账号访问他人数据 |
| 404 | 资源不存在 | Agent/记忆/商品未找到 |
| 409 | 资源冲突 | 记忆池已满、容量超限 |
| 500 | 服务器内部错误 | 文件读写异常 |

### 3.3 关键逻辑改动

#### v2 → v3 核心变更

| 变更项 | v2 行为 | v3 行为 | 影响 |
|---|---|---|---|
| **数据存储** | 单文件扁平数组 | 分池物理隔离（core/daily/archive） | 容量可控，查询更快 |
| **过期归档** | v1: 30天到期**直接删除**<br>v2: 标记 `archived=true` + `archiveReason='expired'` 保留在 daily 池 | 物理移入 `archived.json`<br>（`archiveReason='expired'`，可恢复） | 数据不丢失，<br>活跃池更干净 |
| **字段映射** | 直接返回原始字段 | `createdAt`→`time`，隐藏内部字段 | 前端兼容，减少噪音 |
| **响应结构** | 直接返回分池数组 | 统一 `memories` 数组 + 分池双格式 | 支持表格/分类两种视图 |
| **查询参数** | 无过滤能力 | 支持 type/tag/keyword/limit/offset | 灵活筛选 |
| **value 限制** | >5000 字符返回 400 | >2000 字符自动截断 + warning | 友好降级 |
| **容量控制** | 仅写入时检查 | 写入检查 + 读取时自动归档（>200条） | 自动维护 |
| **归纳合并** | 无 | `POST /api/memory/consolidate` | AI 摘要合并，减少碎片 |
| **记忆注入** | 前端 `buildMemoryPrompt()` + 后端 `inject_memories()` **双重注入** | 仅后端 `ms3.inject_memories()` 统一注入 | 避免重复，<br>减少 token 浪费 |
| **全局搜索** | 仅支持单员工 | `GET /api/memory/search` 跨员工搜索 | 统一管理 |

#### 自动归档触发条件

**清理策略：每天运行一次，只归档、不删除任何数据。**

```
daily 过期（30天未访问）    → archiveReason: expired
活跃记忆总数 > 200 条       → archiveReason: capacity
手动触发归档                 → archiveReason: manual
归纳合并原始记忆              → archiveReason: consolidated
```

> **核心原则**：归档 = 移动位置，不是删除。所有数据永久保留，随时可恢复。

#### 跨池移动字段转换

| 方向 | 新增字段 | 清除字段 |
|------|---------|---------|
| daily → core | priority, tags, updatedAt, accessCount | expiresAt, context |
| core → daily | expiresAt=now+30天, context | priority, tags, updatedAt, accessCount |

### 3.4 认证机制

**请求头：**

```http
Authorization: Bearer <jwt_token>
Content-Type: application/json
```

**Token 获取：**

```bash
POST /api/auth/login
Body: {"username": "admin", "password": "xxx"}
Response: {"token": "eyJhbG...", "user": {"id": "...", "role": "admin"}}
```

**Token 存储：**
- 前端：`localStorage.setItem('sb_auth_token', token)`
- 后端：无 Session，每次请求独立验证 JWT

**Token 刷新：**
- 当前实现无自动刷新机制
- Token 过期（7 天）后用户需重新登录
- 前端 `apiFetch()` 捕获 401 后自动调用 `doLogout()`

### 3.5 关键接口详细说明

#### POST /api/chat/:agentId

**功能：** 向指定员工发送消息，触发 AI 回复

**请求体：**

```json
{
  "role": "user",
  "content": "你好",
  "skipAI": false,
  "empId": "emp_xxx"
}
```

**响应体：**

```json
{
  "userMessage": {"id": "msg_1", "role": "user", "content": "你好"},
  "aiMessage": {"id": "msg_2", "role": "assistant", "content": "老板好！"},
  "archived": 0
}
```

**特殊行为：**
- `skipAI=true` 时只保存消息，不调用 AI API（用于 OpenClaw 模式）
- 消息数超过 500 条时自动归档旧消息到 `memory/archive/`
- 自动注入记忆、摘要、层级关系到 systemPrompt

#### GET /api/memory/:empId

**功能：** 查询员工记忆列表（活跃记忆 + 归档），支持分池过滤与搜索

**路径参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `empId` | string | 是 | 员工唯一标识，如 `emp_001` |

**查询参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `type` | string | 否 | 数据类型过滤。`core`/`daily`/`knowledge`/`active`/`archive`。不传返回全部 |
| `key` | string | 否 | 按 key 精确过滤，如 `preference`、`auto`、`auto_extract` |
| `tag` | string | 否 | 按标签过滤，支持逗号分隔多标签 OR 匹配，如 `凉鞋,达人反馈` |
| `keyword` | string | 否 | 关键词搜索，`value` 字段模糊匹配（大小写不敏感） |
| `include_archived` | bool | 否 | 是否包含归档记忆。默认 `false`，传 `true` 时返回 archive 数据 |
| `limit` | integer | 否 | 单池返回条数上限。默认 50，最大 200 |
| `offset` | integer | 否 | 分页偏移量。默认 0 |

**请求示例：**

```http
GET /api/memory/emp_001?pool=active&tag=达人反馈&limit=10
Authorization: Bearer eyJhbG...
```

**响应示例：**

```json
{
  "success": true,
  "data": {
    "memories": [
      {
        "id": "mem_20260608_abc124",
        "pool": "core",
        "key": "core",
        "value": "李馒头对凉鞋感兴趣但觉得佣金低",
        "source": "chat",
        "time": 1777312800000
      },
      {
        "id": "mem_20260608_def456",
        "pool": "daily",
        "key": "auto",
        "value": "用户提到下周三下午有产品评审会",
        "source": "ai_extract",
        "time": 1700000000000
      }
    ],
    "total": 2,
    "limit": 50,
    "offset": 0,
    "core": [...],
    "daily": [...],
    "archive": [],
    "knowledge": [],
    "version": "3.0",
    "config": {
      "core_max": 50,
      "daily_max": 100,
      "daily_ttl_days": 30
    },
    "shouldConsolidate": false,
    "suggestedSourceIds": []
  }
}
```

#### POST /api/memory/:empId

**功能：** 添加记忆到指定分池（自动分池 + 容量检查）

**路径参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `empId` | string | 是 | 员工唯一标识 |

**请求体：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `value` | string | 是 | 记忆内容，1-2000 字符。超过自动截断并返回 warning |
| `type` | string | 否 | 记忆类型。`auto`/`auto_extract` → daily 池；其他 → core 池。默认 `auto`（兼容字段 `key`） |
| `source` | string | 否 | 来源标识。如 `user_input`、`chat`、`ai_extract`。默认 `user_input` |
| `priority` | int | 否 | 优先级 1-10，仅 core 记忆有效。默认 5 |
| `tags` | array | 否 | 标签数组，最多 10 个。如 `["凉鞋", "达人反馈"]` |
| `context` | string | 否 | 上下文原文，仅 daily 记忆有效 |

**分池规则：**
- `key` 为 `auto` 或 `auto_extract` → `daily` 池
- 其他值 → `core` 池

**请求示例：**

```json
POST /api/memory/emp_001
Content-Type: application/json

{
  "key": "core",
  "value": "李馒头对凉鞋感兴趣但觉得佣金低",
  "source": "chat",
  "priority": 5,
  "tags": ["凉鞋", "达人反馈"]
}
```

**成功响应（200）：**

```json
{
  "success": true,
  "data": {
    "id": "mem_20260608_xxx",
    "key": "core",
    "value": "李馒头对凉鞋感兴趣但觉得佣金低",
    "source": "chat",
    "priority": 5,
    "tags": ["凉鞋", "达人反馈"],
    "time": 1777312800000
  }
}
```

**截断 Warning 响应（200，value 超过 2000 字符时）：**

```json
{
  "success": true,
  "data": {
    "id": "mem_20260608_xxx",
    "key": "core",
    "value": "李馒头对凉鞋感兴趣但觉得佣金低...（截断后）",
    "time": 1777312800000
  },
  "warning": "Value truncated to 2000 chars (original: 3500)"
}
```

**容量超限响应（409）：**

```json
{
  "success": false,
  "error": "daily pool full (100/100)",
  "pool": "daily",
  "max": 100,
  "suggestion": "Archive or delete old memories first"
}
```

#### PUT /api/memory/:empId/:memId

**功能：** 修改记忆内容，支持跨池移动（type 变更时自动迁移）

**路径参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `empId` | string | 是 | 员工唯一标识 |
| `memId` | string | 是 | 记忆唯一标识 |

**请求体：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `value` | string | 否 | 新内容，1-2000 字符。超过自动截断并返回 warning |
| `type` | string | 否 | 新类型（兼容字段 `key`）。变更时自动跨池迁移 |
| `source` | string | 否 | 新来源 |
| `priority` | int | 否 | 新优先级 1-10 |
| `tags` | array | 否 | 新标签，最多 10 个 |
| `context` | string | 否 | 新上下文（仅 daily） |

**请求示例：**

```json
PUT /api/memory/emp_001/mem_20260608_abc124
Content-Type: application/json

{
  "value": "李馒头对凉鞋感兴趣，佣金可谈到20%",
  "type": "core",
  "priority": 7,
  "tags": ["凉鞋", "达人反馈", "佣金谈判"]
}
```

**成功响应（200）：**

```json
{
  "success": true,
  "data": {
    "id": "mem_20260608_abc124",
    "key": "core",
    "value": "李馒头对凉鞋感兴趣，佣金可谈到20%",
    "source": "chat",
    "priority": 7,
    "tags": ["凉鞋", "达人反馈", "佣金谈判"],
    "time": 1777312800000
  }
}
```

**容量超限响应（409）：**

```json
{
  "success": false,
  "error": "core pool full (50/50)",
  "pool": "core",
  "max": 50,
  "suggestion": "Archive or delete old memories first"
}
```

#### DELETE /api/memory/:empId/:memId

**功能：** 删除指定记忆（从活跃池或归档中永久删除）

**路径参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `empId` | string | 是 | 员工唯一标识 |
| `memId` | string | 是 | 记忆唯一标识 |

**成功响应（200）：**

```json
{
  "success": true,
  "data": {
    "deleted": true,
    "id": "mem_20260608_abc124"
  }
}
```

#### POST /api/memory/consolidate

**功能：** 归纳合并多条 daily 记忆为一条 core 记忆，原记忆移入归档（`archiveReason='consolidated'`）

**请求体：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `empId` | string | 是 | 员工唯一标识 |
| `sourceIds` | array | 是 | 要合并的 daily 记忆 ID 列表（2-10 条） |
| `consolidatedValue` | string | 是 | 合并后的新内容，1-2000 字符 |
| `key` | string | 否 | 合并后的类型，默认 `core` |
| `priority` | int | 否 | 优先级 1-10，默认 8 |
| `tags` | array | 否 | 标签数组 |

**请求示例：**

```json
POST /api/memory/consolidate
{
  "empId": "emp_001",
  "sourceIds": ["mem_20260608_def456", "mem_20260608_def457"],
  "consolidatedValue": "李馒头是美妆穿搭达人，对凉鞋感兴趣，佣金可谈到20%",
  "key": "core",
  "priority": 8,
  "tags": ["达人", "李馒头", "凉鞋"]
}
```

**成功响应（200）：**

```json
{
  "success": true,
  "data": {
    "newMemory": {
      "id": "mem_20260608_xxx",
      "key": "core",
      "value": "李馒头是美妆穿搭达人...",
      "source": "induction",
      "priority": 8,
      "tags": ["达人", "李馒头", "凉鞋"],
      "time": 1777312800000
    },
    "archivedIds": ["mem_20260608_def456", "mem_20260608_def457"]
  }
}
```

#### GET /api/memory/search

**功能：** 全局搜索记忆（跨员工、跨池：core + daily + archive）

**查询参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `keyword` | string | 否 | 关键词搜索，`value` 字段模糊匹配（大小写不敏感） |
| `tag` | string | 否 | 逗号分隔多标签，OR 匹配 |
| `type` | string | 否 | `core`/`daily`/`active`/`archive`，不传返回全部 |
| `key` | string | 否 | 按 key 精确过滤，如 `preference`、`auto` |
| `empId` | string | 否 | 按员工 ID 精确过滤 |
| `limit` | int | 否 | 返回条数上限，默认 50，最大 200 |
| `offset` | int | 否 | 分页偏移，默认 0 |

**响应示例：**

```json
{
  "success": true,
  "data": {
    "memories": [
      {
        "id": "mem_20260608_abc124",
        "empId": "emp_001",
        "pool": "core",
        "key": "core",
        "value": "李馒头对凉鞋感兴趣但觉得佣金低",
        "source": "chat",
        "time": 1777312800000
      }
    ],
    "total": 15,
    "limit": 50,
    "offset": 0
  }
}
```

#### GET /api/memory/archived

**功能：** 查看全局归档记忆（所有员工的归档数据）

**查询参数：**

| 参数 | 类型 | 必选 | 说明 |
|---|---|---|---|
| `keyword` | string | 否 | 关键词搜索，匹配 `value` 字段 |
| `archived_reason` | string | 否 | 归档原因过滤：`expired`/`capacity`/`manual`/`consolidated` |
| `limit` | int | 否 | 返回条数上限，默认 50，最大 200 |
| `offset` | int | 否 | 分页偏移，默认 0 |

**响应示例：**

```json
{
  "success": true,
  "data": {
    "memories": [
      {
        "id": "mem_20260608_xxx",
        "empId": "emp_001",
        "pool": "archive",
        "key": "auto",
        "value": "用户提到下周三下午有产品评审会",
        "source": "ai_extract",
        "time": 1700000000000,
        "archivedTime": 1777312800000,
        "archiveReason": "expired"
      }
    ],
    "total": 15,
    "limit": 50,
    "offset": 0
  }
}
```

#### POST /api/match/product-to-influencer

**功能：** 为商品智能匹配达人

**请求体：**

```json
{
  "productId": "prod_xxx",
  "minScore": 30,
  "limit": 10
}
```

**响应体：**

```json
{
  "product": {"name": "口红", ...},
  "results": [
    {
      "influencer": {"name": "美妆博主A", ...},
      "score": 78.0,
      "reasons": ["分类一致", "标签匹配 2 个"],
      "matchPercent": 78
    }
  ],
  "total": 5
}
```

---

## 四、记忆系统详细设计

记忆系统是 SoloBrave 最核心的差异化能力之一。v2 版本解决了 v1 的 20 余个已知问题，引入分池架构、容量管控、过期归档、跨池移动等机制。

### 4.1 设计目标

| 问题 | v1 现状 | v2 解决方案 |
|---|---|---|
| 数据丢失 | `localStorage` 碎片存储 | 后端 JSON 文件持久化 |
| 无容量管控 | 无限增长 | core/daily 双池各 100 条上限 |
| 过期处理 | 物理删除 | 逻辑标记 `archived=True`，可恢复 |
| 并发安全 | 无锁 | 跨平台 `threading.Lock()` |
| 认证缺失 | 直接读取 | 所有 API 强制 `_authenticate()` |
| 版本兼容 | 扁平数组 | 自动迁移到分池格式 |
| 注入失控 | 全量注入 | 分层截断（core 5 + daily 3 + archive 2） |

### 4.2 三层存储架构

```
┌─────────────────────────────────────────┐
│  L1 核心记忆池 (core)                    │  ← 永久保留，手动管理
│  · 员工人设、用户偏好、关键事实           │
│  · 上限 100 条，超出拒绝写入              │
│  · 无 TTL，永不自动过期                  │
├─────────────────────────────────────────┤
│  L2 日常记录池 (daily)                   │  ← 自动提取，30 天 TTL
│  · 聊天中 AI 自动提取的短期记忆           │
│  · 上限 100 条，超出拒绝写入              │
│  · 30 天未访问 → 标记 archived=True      │
├─────────────────────────────────────────┤
│  L3 归档层 (archive)                     │  ← 逻辑归档，非物理删除
│  · 过期日常记录保留在 daily 池中          │
│  · 聊天记录溢出摘要                       │
│  · 可恢复、可删除、不参与默认注入         │
└─────────────────────────────────────────┘
```

### 4.3 数据格式

**单条记忆结构：**

```json
{
  "id": "a1b2c3d4",
  "key": "auto",
  "value": "用户提到下周要出差北京",
  "source": "AI提取",
  "time": 1700000000000,
  "archived": false,
  "archivedTime": null
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 8 位随机 hex，唯一标识 |
| `key` | string | `auto`/`auto_extract` → daily 池；其他 → core 池 |
| `value` | string | 记忆内容，存储上限 2000 字符 |
| `source` | string | 来源标记，如 `AI提取`、`用户录入`、`归纳` |
| `time` | int | 毫秒时间戳，用于排序和 TTL 判断 |
| `archived` | bool | 是否已归档，默认 false |
| `archivedTime` | int | 归档时间戳，恢复时清除 |

**员工记忆文件 (`memory/{emp_id}.json`)：**

```json
{
  "version": "2.0",
  "core": [ /* 核心记忆数组 */ ],
  "daily": [ /* 日常记录数组（含 archived=True） */ ]
}
```

> **注意**：v2 不再使用独立的 `archive` 字段，归档数据以 `archived=True` 标记保存在 `daily` 池中。前端 `GET /api/memory/:empId` 返回时，后端会将 `daily` 中 `archived=True` 的数据分离到 `archive` 字段返回，便于 UI 展示。

### 4.4 容量管控算法

**配置常量 (`MEMORY_CONFIG`)：**

```python
MEMORY_CONFIG = {
    'core_max': 100,           # 核心记忆池上限
    'daily_max': 100,          # 日常记录池上限
    'daily_ttl_days': 30,      # 日常记录过期天数
    'inject_core_max': 5,      # 注入时核心记忆条数
    'inject_daily_max': 3,     # 注入时日常记忆条数
    'inject_value_max': 500,   # 单条记忆注入字符上限
    'store_value_max': 2000,   # 单条记忆存储字符上限
    'history_inject_max': 10,  # 聊天历史注入条数
    'summarize_threshold': 20, # 归纳触发阈值
    'chat_store_max': 500,     # 聊天记录存储上限
}
```

**写入时的容量检查：**

```python
def _handle_post_memory(self, emp_id):
    pool = 'core' if key not in ('auto', 'auto_extract') else 'daily'
    target_pool = data.get(pool, [])
    pool_max = cfg['core_max'] if pool == 'core' else cfg['daily_max']
    
    if len(target_pool) >= pool_max:
        return 409, {
            'error': f'{pool} pool full ({pool_max}/{pool_max})',
            'pool': pool,
            'max': pool_max,
            'suggestion': 'Archive or delete old memories first'
        }
```

**行为：**
- 超出容量 → **拒绝写入**（返回 409），不静默丢弃
- 用户必须主动归档旧记忆或删除后才能新增
- 核心池和日常池独立计数，互不影响

### 4.5 过期归档算法

**触发时机：**
1. `GET /api/memory/:empId` 时自动检查
2. `POST /api/memory/:empId/archive` 手动触发
3. AI 对话注入记忆前自动清理

**算法逻辑：**

```python
def _cleanup_and_archive_expired(emp_id, memory_data):
    now = int(time.time() * 1000)
    ttl_ms = cfg['daily_ttl_days'] * 24 * 3600 * 1000
    archived_count = 0
    
    for m in memory_data.get('daily', []):
        if m.get('archived'):
            continue
        mem_time = m.get('time', 0)
        if mem_time and (now - mem_time) > ttl_ms:
            m['archived'] = True
            m['archivedTime'] = now
            archived_count += 1
    
    return memory_data, archived_count
```

**关键设计：**
- **逻辑归档**：只改标记，不移动文件，不物理删除
- **保留在 daily 池**：`archived=True` 的数据仍存于 `memory/{emp}.json` 的 `daily` 数组中
- **可恢复**：通过 `POST /api/memory/:empId/:memId/restore` 清除 `archived` 标记
- **核心记忆无 TTL**：`core` 池不参与过期检查

**容量归档（活跃记忆总数 > 200）：**

除过期归档外，系统还监控活跃记忆（core + daily）总数：

```python
active_total = len(core) + len(daily)
if active_total > 200 and daily:
    # 按 createdAt 升序，归档最旧的一条 daily
    oldest = min(daily, key=lambda m: m['createdAt'])
    oldest['archiveReason'] = 'capacity'
```

- **触发时机**：`GET /api/memory/:empId` 加载记忆时自动检查
- **归档原因**：`capacity`（容量超限），区别于 `expired`（自然过期）
- **优先归档 daily**：core 记忆更重要，不触发自动容量归档
- **仅归档 1 条/次**：渐进式清理，避免一次性删除过多

### 4.6 三层注入策略

记忆注入发生在 AI 对话时，由 `_call_ai_api()` 自动执行。

**注入优先级（从高到低）：**

```
1. 核心记忆 (L1)
   · 按时间倒序取最新 inject_core_max (5) 条
   · 字符截断至 inject_value_max (500)
   
2. 日常记录 (L2)
   · 按时间倒序取最新 inject_daily_max (3) 条
   · 仅取 archived=False 的数据
   · 字符截断至 inject_value_max (500)
   
3. 归档补充 (L3)
   · 当 L1+L2 不足 (5+3=8) 条时
   · 从 memory/archive/ 取最近归档摘要
   · 最多补充 2 条，前缀标记 [归档]
```

**注入格式：**

```
【关于用户的记忆】
- 用户喜欢极简风格
- 用户是某电商公司运营总监
- 用户对价格敏感
- [归档] 用户曾询问过口红推广方案
```

**容量控制：**
- 单条记忆注入最多 500 字符，超长自动截断
- 总注入记忆最多 8 条（5 core + 3 daily）
- 避免 token 超限，同时保留最关键的信息

### 4.7 跨池移动

**升级（daily → core）：**

```
POST /api/memory/:empId/:memId/promote
```

- 从 daily 池移除，加入 core 池
- `key` 自动改为 `core`
- 检查 core 池容量，满则 409

**修改时跨池移动：**

```
PUT /api/memory/:empId/:memId
Body: {"type": "core"}  # 原 type 为 auto
```

- 检查目标池容量
- 从旧池移除，加入新池
- 保留原 `id` 和 `time`

**恢复（archive → daily）：**

```
POST /api/memory/:empId/:memId/restore
```

- 从 `archive` 列表移除
- 清除 `archived` 和 `archivedTime` 标记
- 加入 daily 池，更新 `time` 为当前时间
- 检查 daily 池容量，满则 409

### 4.8 版本兼容与迁移

**v1 → v2 自动迁移：**

```python
# v1 格式：扁平数组
if isinstance(raw, list):
    migrated = {'core': [], 'daily': [], 'version': '2.0'}
    for m in raw:
        key = m.get('key', 'auto')
        if key in ('auto', 'auto_extract'):
            migrated['daily'].append(m)
        else:
            migrated['core'].append(m)
    _write_json(filepath, migrated)
```

**旧版独立 archive 字段合并：**

```python
old_archive = raw.get('archive', [])
if old_archive:
    for oa in old_archive:
        oa['archived'] = True
        all_daily.append(oa)
```

**旧版物理归档文件合并：**

```python
old_file = _load_archive(emp_id)
old_mems = old_file.get('memories', [])
for om in old_mems:
    migrated = {
        'id': om.get('id'),
        'key': om.get('type', 'auto'),
        'value': om.get('originalValue') or om.get('summary', ''),
        'time': om.get('originalTime') or om.get('archivedTime'),
        'archived': True,
        'archivedTime': om.get('archivedTime'),
        'source': om.get('source', '')
    }
    all_daily.append(migrated)
# 合并后删除旧物理归档文件
os.remove(archive_fp)
```

**迁移触发：**
- 首次 `GET /api/memory/:empId` 时自动检测并执行
- 对用户无感知，不丢失数据
- 打日志记录迁移过程

### 4.9 并发安全

**文件锁机制：**

```python
_memory_file_locks = {}  # filepath -> threading.Lock()
_memory_locks_mutex = threading.Lock()

def _get_memory_file_lock(filepath):
    with _memory_locks_mutex:
        if filepath not in _memory_file_locks:
            _memory_file_locks[filepath] = threading.Lock()
        return _memory_file_locks[filepath]
```

**写入流程：**

```python
def _write_json(filepath, data):
    tmp_path = filepath + '.tmp.' + uuid.uuid4().hex[:8]
    file_lock = _get_memory_file_lock(filepath)
    with file_lock:
        with open(tmp_path, 'w') as f:
            json.dump(data, f)
        os.replace(tmp_path, filepath)
```

**特性：**
- 锁粒度：按文件路径隔离，不同员工的记忆文件互不阻塞
- 原子写入：`.tmp.{random}` → `os.replace()`，崩溃不损坏原文件
- 兼容 Windows：`threading.Lock()` 替代 `fcntl`（Windows 无 fcntl）
- 读操作无锁：JSON 读取不加锁，依赖原子写入保证一致性

### 4.10 聊天记录归档

记忆系统与聊天系统共享 `MEMORY_CONFIG` 中的容量参数。

**聊天溢出处理：**

```python
if len(messages) > cfg['chat_store_max']:  # 500 条
    old_messages = messages[:-300]  # 保留最近 300 条
    archive_data = _load_archive(agent_id)
    archive_data['summaries'].append({
        'id': 'sum_xxx',
        'type': 'chat_overflow',
        'summary': '\n'.join(f"{'用户' if m['role']=='user' else 'AI'}: {m['content'][:100]}"),
        'compressedCount': len(old_messages),
        'createdAt': now
    })
    _save_archive(agent_id, archive_data)
    messages = messages[-300:]
```

**与记忆归档的区别：**

| | 记忆归档 | 聊天记录归档 |
|---|---|---|
| 触发条件 | 30 天 TTL | 超过 500 条 |
| 存储位置 | `memory/{emp}.json` daily 池 | `memory/archive/{emp}.json` summaries |
| 数据形式 | 单条记忆对象 | 对话摘要文本 |
| 可恢复性 | 可恢复为 daily | 仅作参考，不恢复为聊天 |
| 注入策略 | L3 补充（最多 2 条） | 不直接注入 |

### 4.11 记忆提取流程

AI 对话时，系统通过 `[记忆提取任务]` 触发自动归纳：

```python
is_extract = '【记忆提取任务】' in content
api_reply = self._call_ai_api(agent, content, user_info, include_history=not is_extract)
```

- 提取任务不加载历史记录，避免 token 浪费
- AI 返回的记忆通过 `POST /api/memory/:empId` 保存到 daily 池
- 受 daily_max 容量限制，满则拒绝
