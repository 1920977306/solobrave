# 21 轮批注③-1 探查报告 — 后端 MS3 注入条数透传情况

> 探查时间：2026-10-06 09:17
> 执行：小路（side-restore-2）
> 规格：docs/design-spec/v4-21轮AI办公室.md 批注③-1
> 工作区：/Users/qichen/sb-dev/side-restore-2
> 基点：9121ecf（含 20A/20B/21 轮规格表）

## 探查目的

确认后端 AI 回复是否把 **MS3 记忆注入条数 / 知识召回条数 / 规律命中条数** 透传到消息 API（GET /api/chat 或 chat history DB）。

**前置结论：规格 v4 line 59「无透传 → 只丢不造：报数据缺口回来，不编数字」**

## 探查证据链

### 证据 1 — `_call_ai_api` 返回签名（solobrave-server.py:27141）

```python
def _call_ai_api(agent, user_message, user_info=None, include_history=True, group_id=None,
                 allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,
                 group_ids=None):
    """通过代理调用 AI API（带记忆和上下文注入）"""
```

**只返回字符串**（api_reply = system_prompt 注入后的字符串），**不返回**任何条数统计 dict。

### 证据 2 — `ms3.inject_memories` 返回签名（memory_service_v3.py:1969-2108）

```python
def inject_memories(emp_id, system_prompt='', user_message='', api_key=None, provider='openai',
                    agent_config=None, allowed_knowledge_categories=None,
                    model=None, base_url=None):
    """
    为 AI 对话注入记忆，返回更新后的 system_prompt
    注入优先级：core → daily → archive → knowledge
    """
    ...
    return system_prompt  # ← 只返回字符串，无 stats dict
```

L1/L2/L3/L4 4 级注入都在函数内部，条数仅在 `core_lines` / `daily_lines` / `archive_lines` / `kb_lines` 局部变量，不传出函数。

### 证据 3 — `_retrieve_knowledge_context` 4 级注入（solobrave-server.py:25486-25630）

```
1) 同实体历史  (knowledge_events ORDER BY created_at DESC LIMIT N)
2) 同类目相似分析 (knowledge_events JOIN talents ORDER BY created_at DESC)
3) embedding 语义兜底 (_hybrid_retrieve_events 混合 RRF)
4) proven/verified/candidate 规律 (knowledge_patterns ORDER BY confidence_score DESC LIMIT 2)
```

**所有 4 级结果只格式化为 `parts.append(...)` 字符串，**不返回条数 dict。

### 证据 4 — 注入日志只写 server log（line 27208/27250/27258/25589）

```python
logger.error(f'  [MemoryInject] {agent_id} 注入失败: {e}')      # line 27208
logger.error(f'  [RAG] {agent_id} 注入失败: {e}')                # line 27250
logger.info(f'  [KnowledgeInject] {agent_id} 注入知识事件上下文 {len(ke_context)} 字')  # line 27258
logger.warning(f'  [KnowledgeInject] 规律查询失败，跳过: {e}')   # line 25589
```

注入条数 / 字符数**只到 server log**，不到 API 响应。

### 证据 5 — 接口实测（POST /api/chat/:agentId，skipAI=false）

```bash
curl -s -X POST "http://localhost:18211/api/chat/emp_1779430403964" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"role":"user","content":"hi","skipAI":false}'
```

**实际响应字段**：
```json
{
    "userMessage": {"id": "...", "role": "user", "content": "hi", "timestamp": "...", "userId": "..."},
    "aiMessage": {"id": "...", "role": "assistant", "content": "...", "timestamp": "..."},
    "archived": 0,
    "credit": {"balance": 4590, "has_credits": true}
}
```

**缺字段**：
- ❌ `memory_injection_count` (核心/日常/归档 3 级)
- ❌ `knowledge_recall_count` (kb_entries 语义检索)
- ❌ `pattern_hit_count` (knowledge_patterns 命中)
- ❌ `injection_tags` (统一 tags 结构)
- ❌ `system_prompt_size` (注入总字符)

### 证据 6 — chat.json 落库结构（data/chats/emp_xxx.json）

```json
[
    {"id": "...", "role": "user", "content": "...", "timestamp": "...", "userId": "..."},
    {"id": "...", "role": "assistant", "content": "...", "timestamp": "..."}
]
```

**assistant msg 缺字段**：
- ❌ `tags` (3 色 pill 数据源)
- ❌ `injection_meta` (注入元数据)
- ❌ `memory/knowledge/pattern count`

## 探查结论

| 维度 | 后端是否透传 | 证据 |
|---|---|---|
| 记忆注入条数 (core/daily/archive) | ❌ 否 | 证据 1+2+5 |
| 知识召回条数 (kb_entries semantic) | ❌ 否 | 证据 2+5 |
| 规律命中条数 (knowledge_patterns) | ❌ 否 | 证据 3+5 |
| 注入内容摘要 | ✅ 是（拼到 system_prompt 字符串） | 证据 3 |
| 注入字符数（部分） | ✅ 是（log） | 证据 4 |
| 注入 tags 结构化数据 | ❌ 否 | 证据 5+6 |

**规格 v4 line 59 触发：「无透传 → 只丢不造」**

## 派单建议（转协调人立项）

### 后端透传待立项（必须做才能渲染 tag pill）

服务端 `ms3.inject_memories` 和 `_retrieve_knowledge_context` 函数需改造为返回 `(system_prompt, stats)` tuple，stats 包含：

```python
stats = {
    'memory': {'core': N, 'daily': N, 'archive': N},  # 记忆 3 级
    'knowledge': N,  # kb_entries 召回条数
    'pattern': N,  # knowledge_patterns 命中条数
}
```

`/api/chat/:agentId` 响应需增加字段：

```json
{
    "aiMessage": {...},
    "injectionTags": {
        "memory": {"core": 2, "daily": 1, "archive": 0},
        "knowledge": 3,
        "pattern": 1
    }
}
```

**chat.json 落库** 也需在 assistant msg 加 `injectionTags` 字段，保证历史消息刷新后 tag 仍可见（前端 .sb2-chat-bubble 的 tags 渲染从 msg.injectionTags 读）。

### 设计哲学依据（与「只丢不造」一致）

> 老大 21 轮规格 line 59：「有透传 → 前端在 assistant 气泡上方渲染三色 pill（样式照原型 tag：3px 8px / r999 / 11px，三色用现有 token）；无透传 → 只丢不造：报数据缺口回来，不编数字，本项转「后端透传」待立项。」

数据缺口不编造 3 色 pill 数字，跟 20 轮 r27「stats.total + stats.totalChunks 真字段口径」教训一致（编 demo 数字 = 假数据 = 假阳性验收）。

## 本 commit 状态

- ✅ 探查报告完成（无数据缺口编造）
- ❌ 不动后端代码（不在本轮范围）
- ❌ 不动前端 tag 渲染代码（等后端透传立项）
- ✅ 仅 docs/probe-21A-MS3-injection-tags.md 探查报告 commit

## 执行器提交

- commit hash: 见 git log
- 改动清单: docs/probe-21A-MS3-injection-tags.md 新增（探查报告）
- 自检三件套: 不适用（本轮是探查非改动，0 hex + node --check 自动过）
- server 零改动（探查无代码修改）
- 范围守住声明: 仅 docs/probe-21A-MS3-injection-tags.md 一个文件，solobrave-server.py / index.html / memory_service_v3.py / knowledge_service.py 均 0 行改动
