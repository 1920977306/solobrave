# 守护线程多租户化审计（phase0 遗留项 · 只调研不写码）

> 调研时间：2026-10-08，基线 commit `ac6c5c9`（prod HEAD）
> 背景：phase0 调研文档 §遗留「其他后台线程逐点审计」。本文逐个审计平台级守护线程在**多租户模式启用后**的租户上下文行为。
> 前置事实（M2/M5 已确立）：
> - `_db_conn()` 按 thread-local tid 路由租户库；**无 tid 的线程恒落默认租户**
> - `_td()` 按当前线程 tid 路由租户目录；同理
> - `_run_as_tenant(tid, fn)` 是官方包裹器；请求线程派生子线程的标准姿势 = 捕获 `_req_tid` + 子线程入口包裹（M5 先例：HeavyPipe L21644 / Reanalysis L22008）
> - 租户枚举 helper `_active_tenant_ids()`（L261）已存在

## 结论速览

| # | 守护/线程 | 运行状态 | 泄漏类型 | 风险 | 修复工作量 |
|---|-----------|----------|----------|------|-----------|
| 1 | **L13886 记忆自动归纳子线程** | ✅ 运行中 | 租户 B 知识写入默认租户库 | 🔴 高 | 0.5 人日（一行级包裹，M5 漏网） |
| 2 | **CreditSyncLoop**（L33560→29899） | ✅ 运行中 | 租户 B 积分扣账落默认租户库 | 🔴 高 | 1–1.5 人日 |
| 3 | **BrainScheduler**（L751） | ⏸ 停用（start 被注释 L33586） | 启用即：记忆清洗/主题沉淀全落默认租户 | 🟡 高（潜在） | 2–3 人日 |
| 4 | **PatternInduce-Cron**（L33905） | ✅ 运行中 | 只跑默认租户数据，租户 B 规律库空转 | 🟡 中（功能缺失，不泄漏） | 0.5–1 人日 |
| 5 | OpenClawTaskQueue（L577） | ✅ 运行中 | 无 DB/目录写，租户中立 | 🟢 低 | 不建议改 |
| 6 | OpenClawWatchdog（L33584） | ✅ 运行中 | 只读探测 + 进程自愈 | 🟢 极低 | 不用改 |

> 注意：当前 prod 单租户模式运行，**以上泄漏在多租户模式（SOLOBRAVE_MT=1 或活跃租户 >1）启用后才实际发生**。但 M1 验签硬闸已就绪、开通端点已上线，泄漏窗口随时会被打开，应在开闸前修掉 1、2。

## 逐点审计

### 1. L13886 记忆自动归纳子线程 — 🔴 高（运行中，M5 漏网）

**链路**：`POST /api/memory/{empId}`（请求线程，tid=B 正常）→ 记忆 key ∈ (auto, auto_extract) → L13886 派生裸线程 `_induct_knowledge_for_agent(agent, auth.user_id)` → `ks.knowledge_create()` → `knowledge_service._db_conn()` fallback 到 `solobrave_server._db_conn()`（已验证 L136）→ **线程无 tid → 写默认租户 knowledge_base_new**。

**后果**：租户 B 员工的个人记忆被归纳成「全局共享知识文档」后，落进默认租户（老大）的全局知识库 —— 跨租户知识泄漏，且是内容级（记忆文本）。

**对照**：M5 已用 `_req_tid` 捕获 + `_run_as_tenant` 包裹了 HeavyPipe（L21644）和 Reanalysis（L22008），**这一处是同模式的漏网**。

**建议修法**（照 M5 先例，一行级）：
```python
_req_tid = _current_tenant_id()  # 请求线程捕获
threading.Thread(
    target=lambda: _run_as_tenant(_req_tid, _induct_knowledge_for_agent, agent, auth.user_id),
    daemon=True
).start()
```

### 2. CreditSyncLoop — 🔴 高（运行中）

**链路**：L33560 守护循环 → `_sync_token_usage_from_trajectories()`（L29899）→ `_glob_trajectory_files()` 扫 `~/.openclaw/agents/*/sessions/*.trajectory.jsonl`（**全局 OpenClaw 目录，天然跨租户**，L29747）→ `token_usage` INSERT 走 `_db_conn()` → 线程无 tid → **全部落默认租户库**。

**后果**：多租户后，租户 B 员工的聊天 token 消耗回放扣账记到老大的积分账上 —— 积分记错账，直接经济损失面。

**关键细节**：轨迹文件按 agent_id 归属，agent_id 全局唯一（M1 租户 agent 互不可见），所以**归因正确、落库错误**。修法不是按文件分租户，而是 INSERT 时按 `agent_id → tenant_id` 映射（agents.json 已有 tenant_id 章）路由到对应租户库；`_repair_misattributed_credits` 同理。

**建议修法**：
1. `_parse_trajectory_event` 后查 agent→tenant 映射表（agents.json 一次加载缓存）
2. 按租户分组后，每组 `_run_as_tenant(tid, _write_group)` 写入
3. repair 步骤同样按映射分流

### 3. BrainScheduler — 🟡 高（当前停用，启用即漏）

**运行状态**：`_brain_scheduler.start()` 在 L33586 **被注释**，迁移任务 L33589 也被注释。守护线程当前不在跑。但 `request_clean`（L13867）仍在请求线程被调用，**队列只入不消**，长期是内存隐患（顺手事项）。

**若启用，泄漏面**（全部在无 tid 的守护线程执行）：
- `_enqueue_uncleaned_memories`（L727，启动 + 每 60s）：扫 `_db_conn()` memory 表 → 只看默认租户，租户 B 记忆永不入队
- `_do_clean`（L843）：`ms3._clean_and_deduplicate` + `classify_memory_to_topic` 写回 → 落默认租户库
- `_do_induct`（L854）/ `_do_classify`（L871）/ `_daily_inspect`（L961）/ `get_stats`（L981）/ `enqueue_all_pending`（L1003）：同
- 大脑 API 端点（L14299-14371）在请求线程直调 `_topic_svc`/`_know_svc`，**这部分没问题**（tid 正确）

**建议修法**（启用前必须做，工作量最大的一项）：
1. 任务队列条目带 `tid`：`request_clean`/`request_induct`/`request_classify` 调用方在请求线程捕获 `_current_tenant_id()` 入队
2. `_execute_task` 统一 `_run_as_tenant(task['tid'], self._execute_task_real, task)`
3. `_enqueue_uncleaned_memories` 改为 `for tid in _active_tenant_ids(): _run_as_tenant(tid, _scan)`
4. 守护线程本身不持有 tid，只做分发

### 4. PatternInduce-Cron — 🟡 中（功能缺失型）

**链路**：L33905 线程（6h 周期）→ `_run_pattern_induce_all_categories`（L33909）→ category 列表 UNION 查询 `knowledge_patterns/talents/products/brands`（全走 `_db_conn()`）→ `_induce_knowledge_patterns` 写回 knowledge_patterns。

**后果**：多租户后只归纳默认租户数据；租户 B 的规律库永远空跑。`_resolve_induce_llm_config('')` 用的是全局 LLM 配置（空串 = 全局），**这部分恰好是跨租户共享的正确语义**，不用动。

**建议修法**（最简）：
```python
def _run():
    try:
        for tid in _active_tenant_ids():
            _run_as_tenant(tid, _run_pattern_induce_all_categories)
    ...
```

### 5. OpenClawTaskQueue — 🟢 低

纯 AI 调用队列：`_call_ai_for_json` 调 OpenClaw CLI，**无 DB 写、无 DATA_DIR 目录写**（轨迹写 `~/.openclaw/agents` 是 OpenClaw 进程自己写的，不经本队列）。租户中立。
唯一注意点：全局单队列，多租户后所有租户 AI 调用共享优先级队列与 120s 超时 —— 资源层面的互相影响（排队延迟），非数据泄漏。不建议动。

### 6. OpenClawWatchdog — 🟢 极低

health 探测 + dispatch 停滞自愈，无租户数据读写。不用改。

## 修复优先级与工作量

| 顺序 | 项 | 理由 | 工作量 |
|------|-----|------|--------|
| P0 | #1 L13886 包裹 | 运行中 + 内容级泄漏 + 一行级修复 | 0.5 人日 |
| P0 | #2 CreditSyncLoop 分流 | 运行中 + 积分错账 | 1–1.5 人日 |
| P1 | #4 PatternInduce-Cron 循环租户 | 运行中 + 租户 B 功能缺失 | 0.5–1 人日 |
| P2 | #3 BrainScheduler 队列带 tid | 当前停用，启用前必须完成；另需处理 request_clean 只入不消 | 2–3 人日 |
| — | 顺手：request_clean 队列只入不消 | 当前队列无限增长（每条记忆 POST 都入队） | 随 #3 |

**合计：约 4–6 人日。** 建议 P0 两项合成一轮派单（都小），P1 一轮，BrainScheduler 等老大决定是否重启大脑调度器后再议。

## 验证方法（修复后）

沙箱 `SOLOBRAVE_MT=1` 双租户实测：
1. 租户 B 员工 POST 一条 auto 记忆 → 断言知识落在租户 B 库（`data/tenants/<tidB>/solobrave.db` 的 knowledge_base_new），默认库 0 新增
2. 租户 B 员工聊一句产生 trajectory → 触发 `/api/token-usage/sync` → 断言 token_usage 落在租户 B 库
3. 等一个 PatternInduce 周期（或手动触发）→ 断言租户 B 库 knowledge_patterns 有产出
4. 默认租户回归：talents/knowledge/memory 计数不变
