# AI 员工详情页后端接口规格（stats + 工作记录时间线）

> 对应设计稿：`docs/design-system/redesigns/ai-employee-profile.html`（V3 已锁版）
> 本文档所有行号基于当前 dev 分支 solobrave-server.py，实现前请重新 grep 定位（行号会漂移）。
> 铁律：**只统计真实存在的数据，算不出的字段返回 null（前端显示 `--`），禁止编造。**

## 0. 结论：3 件事 = 后端 2 个新 GET 接口 + 前端 1 个已有函数

| 需求 | 做法 | 新代码 |
|---|---|---|
| 4 个 stats 数字 | 新接口 `GET /api/agents/:id/stats` | 后端 |
| 工作记录时间线 | 新接口 `GET /api/agents/:id/activity` | 后端 |
| "和 Helen 聊聊"按钮 | **不用新接口**。前端已有 `openChat(id)`（index.html L12897），按钮 onclick 调 `switchModule('messages'); openChat('emp_xxx')` | 前端（页面迁移时） |

后端只做 2 个接口。页面从 redesigns/ 迁入 index.html 是第二阶段前端任务，等接口 curl 验证通过后再做。

---

## 1. 通用要求（两个接口都适用）

### 1.1 鉴权（照抄现有模式）
```python
auth = _authenticate(self.headers, self.client_address[0], self)
if not auth.is_authenticated:
    self._send_auth_error(auth.error, auth.status); return
if not self._require_module_permission(auth, 'employees'): return
```

### 1.2 Agent 可见性校验（照抄 `_handle_get_agent`，L9456-9481）
先 `_load_agents()` 找到 `:id`：
- 找不到 → 404 `{'error': '员工不存在'}`
- 非 admin 且 `agent.get('createdBy') != uid` 且 `agent.get('visibility') != 'all'` → 403
- leader 的项目组可见性（`_get_accessible_agent_ids`）也要兼容。
- **建议**：抽一个公共函数 `_can_view_agent(auth, agent) -> bool`，两个新接口和 `_handle_get_agent` 共用，避免逻辑分叉。

### 1.3 数据权限范围（非 admin 两层架构，照抄达人列表 L15404-15409）
```python
uid = auth.user_info['userId']
owner_set = {uid} | set(_get_user_emp_ids(uid))   # 自己 + 自己创建的全部 AI 员工
visible_talent_ids = SELECT id FROM talents WHERE created_by IN owner_set
```
- admin：不加任何 scope 过滤。
- 非 admin 但 `auth.localhost_agent_id` 存在（AI 员工本地调用）：同样按 owner_set 过滤。
- 参考：`_resolve_talent_owner_id(auth)`（L1892）。

### 1.4 路由注册位置（重要！）
`_do_GET`（L6074）里现有通配 `if path.startswith('/api/agents/')`（L6115）会把 `/api/agents/X/stats` 整体当成 agent_id 传进 `_handle_get_agent('X/stats')` → 404。
**两个新路由的 if 判断必须插在 L6115 那个通配分支之前**：
```python
if path.startswith('/api/agents/') and path.endswith('/stats'):
    agent_id = path[len('/api/agents/'):-len('/stats')]
    self._handle_get_agent_stats(agent_id); return
if path.startswith('/api/agents/') and path.endswith('/activity'):
    agent_id = path[len('/api/agents/'):-len('/activity')]
    self._handle_get_agent_activity(agent_id); return
```

### 1.5 时间格式坑（五种格式，不许在 SQL 里混着过滤）
| 表 | 时间字段 | 类型 |
|---|---|---|
| knowledge_events | created_at | INTEGER（epoch 秒） |
| talent_follow_ups | follow_up_at / created_at | INTEGER（epoch 秒） |
| deals | created_at | INTEGER（epoch 秒） |
| tasks | created_at / completed_at | TEXT，`datetime('now','localtime')` 本地时间字符串 |
| tool_calls | created_at | TEXT，`CURRENT_TIMESTAMP` **UTC** 字符串 |

**统一做法**：SQL 只做 agent/scope 过滤；取数后在 Python 侧把每条记录时间归一化成 epoch 秒，再做窗口过滤、合并、排序、截断。

---

## 2. 接口一：GET /api/agents/:id/stats?window_days=30

返回 4 个指标 + 分母。窗口默认 30 天（按归一化后的 epoch 秒过滤）。

```json
{
  "agent_id": "emp_1780199176680",
  "window_days": 30,
  "stats": {
    "talents_served": 12,
    "analyses_done": 8,
    "tasks_done": 5,
    "tool_calls_total": 47,
    "tool_success_rate": 0.96
  },
  "generated_at": 1789000000
}
```
- `tool_calls_total = 0` 时 `tool_success_rate` 返回 `null`（前端显示 `--`），不许返回 0 或 1。

### 指标口径（逐条对应设计稿 stat-source 注释）

**① talents_served（已服务达人数，去重）**
两个来源取并集去重：
- `talents` 表：`created_by = :agent_id`（该员工自己录入的达人）
- `knowledge_events` 表：`agent_id = :agent_id AND entity_type='talent'` 的 `DISTINCT entity_id`（该员工分析过的达人）
- 非 admin：并集结果再与 `visible_talent_ids` 取交集。
- 窗口：talents 按 created_at、events 按 created_at 过滤。

**② analyses_done（完成分析数）**
`COUNT(*) FROM knowledge_events WHERE agent_id=:agent_id AND event_type='analysis'`，窗口内。
非 admin：仅统计 entity_id 在 visible_talent_ids 内的记录（entity_type='talent' 时）。

**③ tasks_done（完成任务数）**
`COUNT(*) FROM tasks WHERE assignee=:agent_id AND status='completed'`。
- **注意枚举值是 `'completed'` 不是 `'done'`**（前端任务筛选 L7241 实证）。
- 窗口用 `completed_at`（为空回退 created_at）。
- 非 admin 追加 `AND creator IN owner_set`（只看派给自己员工的任务）。

**④ tool_success_rate（工具调用成功率）**
- 分子：`tool_calls WHERE agent_id=:agent_id AND exit_code=0`
- 分母：`tool_calls WHERE agent_id=:agent_id`（exit_code 含 NULL/非0 都算总数）
- 窗口按 created_at（UTC 字符串，归一化）。
- tool_calls 无 created_by 字段：agent 本身已通过 1.2 可见性校验，可见该 agent 即可见其工具日志。

---

## 3. 接口二：GET /api/agents/:id/activity?days=30&limit=20

聚合 5 类事件，按时间倒序合并返回。前端映射 V3 设计稿时间线徽章。

```json
{
  "agent_id": "emp_...",
  "items": [
    {
      "type": "analysis",
      "status": "active",
      "title": "完成达人分析：小菜菜",
      "detail": "B级 · 低客单价走量型账号……",
      "entity_id": "t_xxx",
      "entity_name": "小菜菜",
      "ts": 1789000000
    }
  ]
}
```
- `ts`：epoch 秒，**前端负责格式化**（"今天 15:43" 等，复用现有时间格式化函数）。
- `detail`：后端截断 60 字，超长加省略号。
- 无数据时 `items: []`（前端显示空状态，不放假数据）。

### 3.1 五类事件取数与映射

| type | 数据源 | 归属条件（admin） | 非 admin 追加 scope | title 规则 | status 映射 |
|---|---|---|---|---|---|
| `analysis` | knowledge_events | `agent_id=:id AND event_type='analysis'` | entity_id ∈ visible_talent_ids（talent 类） | `完成达人分析：{talents.name}`，无 name 用 event.title | 恒 `active`（已完成） |
| `ingest` | knowledge_events | `agent_id=:id AND event_type='ingest'`（vision/截图录入事件，event_type 实际值先 grep 确认） | 同上 | `录入截图数据：{talents.name}` | 恒 `active` |
| `follow_up` | talent_follow_ups | `follow_up_by=:id`（字段名先 grep 确认实际写入值是 agent id 还是 name） | talent_id ∈ visible_talent_ids | `跟进 {talents.name}`，detail=content 截断 | status 列：`completed`→`active`（已跟进）；`pending`→`talking`（沟通中） |
| `task` | tasks | `assignee=:id` | creator ∈ owner_set | title=任务标题 | `completed`→`active`（已完成）；`pending`→`pending`（待处理）；进行中类→`talking` |
| `deal` | deals | **仅 `created_by=:id` 直接归属** | 追加 talent_id ∈ visible_talent_ids | `合作单：{product_name}（{talents.name}）` | status：`failed` 或 win_loss_category=`lost` → **`lost`（需介入，红色徽章）**；`completed/live` → `active`；`pending/negotiating/sample_sent/approved` → `talking` |

### 3.2 两条诚实性约束
- **deals 表没有 agent_id 列**，只有 created_by。只按 `created_by=:id` 直接归属算这个员工的合作单，**禁止**用"该员工分析过的达人"间接推断合作单归属。查不到就少一行，不编。
- `follow_up_by`、knowledge_events 的 `ingest` event_type：实现前先 grep 后端实际写入值（是 emp id 还是员工名），按真实值匹配，不要凭字段名想当然。

### 3.3 排序与截断
五类各自取数（Python 归一化时间 + 窗口过滤）→ 合并 → 按 ts 倒序 → 取前 `limit`（默认 20）。

---

## 4. 验收（改 solobrave-server.py 的强制流程，MEMORY 规则）

1. 先跑 `tests/` 现有单元测试，全绿。
2. 本地起服务后 curl 验证：
   - `GET /api/agents/:id/stats` 未带登录态 → 401；带普通员工 token 访问别人的私有 agent → 403；
   - admin 调 stats/activity → 200，JSON 结构与上面一致；
   - 非 admin 调 activity，返回的记录里不出现别人子库的达人（重点验证 deals/follow_ups 不越权）。
3. 回归核心接口：`/api/products`、`/api/talents`、`/api/tasks`、`/api/agents` 全部 200。
4. 全部通过才 push origin/dev。

## 5. 不做的事
- 不动 index.html、不动前端（页面迁移 + openChat 接线是第二阶段，等这 2 个接口验证后单开）。
- 不新增表、不加字段、不改现有接口返回结构。
- 不做"响应速度/准确度"类无法从真实数据算出的指标（设计稿已删，别加回来）。
