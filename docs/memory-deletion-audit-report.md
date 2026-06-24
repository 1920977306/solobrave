# AI 员工记忆删除彻底性排查报告

> 排查时间：2026-06-23  
> 排查范围：单条记忆删除、彻底删除 AI 员工、群聊记忆、工具创建数据  
> 相关提交：
> - `c89d4c8` 记忆删除彻底化（物理删除 + 二级/三级沉淀清理）
> - `fd17b70` / `d16d873` 沉淀清理增强与 embedding_cache 清理
> - `c789941` 删除员工时清理数据库沉淀、向量缓存、RAG 缓存与群聊消息
> - `f751453` 删除员工时确保 AI 绝对失忆（群聊与其他 AI 记忆上下文清理）
> - `163503d` 工具创建的达人/商品级联硬删除

---

## 1. 单条记忆删除（`delete_memory`）

### 原问题
- 原实现为软删除（`status='archived'`），数据仍留在 `memory` 表和 JSON 文件中。
- 关联的二级归纳、三级知识库、主题索引等沉淀数据仍保留对该记忆的引用。

### 修复结果
- **物理删除**：从 `memory.json` / `archived.json` 中移除，并从 SQLite `memory` 表物理删除。
- **级联清理**：
  - `memory_summary`：移除引用，无关联时物理删除归纳记录。
  - `knowledge_base`：移除引用，证据数为 0 时物理删除知识记录。
  - `knowledge_base_new`：移除 `evidence_mem_ids` 引用，无依据时物理删除。
  - `memory_topics`：重算 `mem_count` / `emp_ids`，主题下无记忆时物理删除。
  - `embedding_cache`：按被删记忆 `value` 的 MD5 hash 物理删除向量缓存。
- **容错**：各沉淀清理步骤独立 `try/except` + 独立事务，任意表缺失不影响核心删除。

---

## 2. 彻底删除 AI 员工（`permanent=true`）

### 原问题
`_cleanup_agent_data` 仅清理文件系统（聊天记录、记忆目录、归档文件），未清理 SQLite 数据库中的沉淀数据，存在严重残留。

### 修复结果
清理范围扩展为：

| 数据层 | 清理动作 |
|---|---|
| 个人聊天记录 / 聊天摘要 | 删除文件 |
| 个人记忆 JSON / 归档 | 删除目录 / 文件 |
| 群聊消息 | 遍历 `data/chats/group_*.json`，移除该 AI 发送的消息 |
| 群聊 L3 overflow 归档 | 遍历 `data/memory/archive/group_*.json`，移除该 AI 的原始消息 |
| 项目组公共记忆（活跃+归档） | 移除 `senderId == agent_id` 的记录 |
| 其他 AI 个人记忆 | 移除 `senderId == agent_id` 的群聊上下文记录 |
| SQLite `memory` | 删除 `emp_id=?` 的记录 |
| SQLite `memory_summary` | 删除或移除引用 |
| SQLite `knowledge_base` | 删除或移除引用 |
| SQLite `knowledge_base_new` | 按 `evidence_mem_ids` 删除或移除引用 |
| SQLite `knowledge` / `knowledge_chunks` / `knowledge_versions` | 删除 `emp_id=?` 的个人文档 |
| SQLite `memory_topics` | 从 `emp_ids` 中移除该员工，为空则删除主题 |
| SQLite `embedding_cache` | 按该员工所有记忆 value 的 hash 删除 |
| 内存 RAG 缓存 `ks._rag_cache` | 删除 `rag:<agent_id>:*` 缓存 |
| 工具创建的业务实体 | 硬删除 `created_by=?` 的达人/商品及关联数据 |

---

## 3. AI 注入入口确认（确保"失忆"）

| 注入/检索入口 | 数据来源 | 删除后是否仍会调出 |
|---|---|---|
| `inject_memories` → core/daily/archive | `load_memory` / `load_archive` JSON 文件 | 不会，已物理删除 |
| `inject_memories` → knowledge | `knowledge_search_semantic` 查 `knowledge` 表 | 不会，员工个人文档已删除 |
| `inject_group_memories` | `load_group_memory` JSON 文件 | 不会，已按 `senderId` 清理 |
| `_load_memory_summaries` | SQLite `memory_summary` | 不会，已删除或移除引用 |
| `_load_knowledge_base` | SQLite `knowledge_base` | 不会，已删除或移除引用 |
| `_run_daily_memory_jobs` | 遍历现存 agent | 不会，已删除员工不在 agent 列表 |
| 工具查询达人/商品 | SQLite `talents` / `products` | 不会，员工创建的已级联硬删除 |

---

## 4. 工具创建数据（`add_talent` / 商品）

### 原问题
- `add_talent` 调用 `POST /api/talents` 创建达人，与记忆系统无关联。
- `talents` / `products` 表无 `created_by` 字段，删除员工时无法识别其创建的业务实体。
- 达人/商品残留会通过各种工具查询重新回到 AI 上下文，造成"污染"。

### 修复结果
- `talents` / `products` 表新增 `created_by` 字段（兼容旧表自动升级）。
- 前端 `add_talent` 工具调用时传入 `created_by = getCurrentEmpId()`。
- 彻底删除员工时硬删除：
  - `created_by=?` 的达人
  - `created_by=?` 的商品
  - 关联的 `talent_follow_ups` 跟进记录
  - 关联的 `product_talent_match` 匹配记录
  - 更新受影响品牌的统计指标

### 注意
单条记忆删除不会级联删除达人/商品，因为业务实体是独立的用户操作结果，不等于记忆本身。

---

## 5. 验证

- `D:/Python312/python.exe -m py_compile solobrave-server.py memory_service_v3.py` ✅
- 临时单元测试：
  - 单条记忆删除的沉淀清理 ✅
  - 删除员工的数据库级联清理 ✅
  - 群聊/其他 AI 记忆的 `senderId` 标记与清理 ✅
  - 工具数据 `created_by` 与级联硬删除 ✅
- `run_regression.py`：**33 PASS / 0 FAIL** ✅

---

## 6. 结论

当前实现已覆盖以下残留风险点：

1. ✅ 记忆物理删除（JSON + SQLite）
2. ✅ 向量缓存 `embedding_cache` 清理
3. ✅ 二级归纳 `memory_summary` 清理
4. ✅ 三级知识库 `knowledge_base` / `knowledge_base_new` 清理
5. ✅ 主题索引 `memory_topics` 清理
6. ✅ 知识库文档/分块/版本 `knowledge` / `knowledge_chunks` / `knowledge_versions` 清理
7. ✅ RAG 内存缓存 `_rag_cache` 清理
8. ✅ 个人/群聊聊天记录清理
9. ✅ 项目组公共记忆（活跃+归档）清理
10. ✅ 其他 AI 个人记忆中的群聊上下文清理
11. ✅ 工具创建的达人/商品级联硬删除

未发现 Redis/Memcached/Kafka/RabbitMQ 等外部缓存层；仅存在进程内 `_rag_cache`，已处理。
