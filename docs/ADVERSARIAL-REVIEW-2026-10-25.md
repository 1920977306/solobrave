# 积分闸门 · 独立对抗审查报告（B 边）

> **日期**：2026-10-25
> **审查人**：B 边对话（`/Users/qichen/sb-dev/parallel-handoff-1025`，分支 `feat/parallel-handoff-1025`）
> **审查对象**：A 边 2026-10-25 交付的积分闸门（基点 `c8e849a`）
> **方法**：独立第二双眼睛 —— **不读 A 边的口径当结论**，从「算力消耗侧」反查

---

## 0. 一句话结论

**闸门本身是对的，但它的守卫是「单侧」的** —— 全部从「入队侧」看，
所以**一个「直接调 LLM 而不入队」的新功能能绕过整道闸门，而 19 条守卫全绿**。
⇒ 本轮补了 **19 条从「消耗侧 / 退款侧」反查的守卫**，并把**三个真缺口全部修完**
（退款不幂等 ⇒ 白送积分 / 僵死回收无门槛 ⇒ 算力双跑 / 退款与标记不同事务 ⇒ 白扣）。

**变异验证：9/9 全抓；其中 8/9 变异下 A 边的守卫完全全绿**（盲区坐实）。

---

## 1. 审查基准：先把「消耗算力」这件事摊开

### 1.1 算力消耗点全量（AST 枚举，16 处）

| 文件 | 所在函数 | 消耗的东西 | 归属 |
|---|---|---|---|
| `executors.py` | `execute_product_collect` | 商品结论 LLM | 收费 `collect_product` |
| `executors.py` | `execute_image_vision` | 视觉 LLM + 结论 LLM（2 处） | 收费 `vision` |
| `executors.py` | `execute_qa` | 问答 LLM | 收费 `qa` |
| `executors.py` | `_llm_product_verdict` | 文字 LLM 漏斗 | 由调用方计费 |
| `executors.py` | `_vision_extract_product` | 视觉漏斗 | 由调用方计费 |
| `executors.py` | `_qa_llm` | 降级链传输（2 处） | 传输层 |
| `llm_channels.py` | `call_ring` / `call_ring_messages` | 环传输（各 2 处） | 传输层 |
| `weekly.py` | `_ai_tips` | 周报 AI 建议 | 收费 `weekly_report` |
| `tools/audit_verdict.py` | `audit` | 运维审计 | 平台自己的算力 |
| `tools/probe_llm_channels.py` | `_probe_text_ring`（2 处） | 运维探针 | 平台自己的算力 |

### 1.2 收费入口全量（9 处）

`handler._route_douyin`(collect_douyin) / `_route_image`(vision) / `_route_qa`(qa) /
`_route_product`(collect_product ×1、collect_product_cached ×2) / `_route_video`(video) /
`weekly._enqueue_report`(weekly_report) / `weekly._maybe_enqueue_collect`(weekly_collect) /
`executors`(recollect ×2)

### 1.3 执行体可达性（查「有没有队列之外的直调」）

`execute_*` 全部只被 `worker.EXECUTORS` 消费（= 只跑已付费任务），
另有两个 `daren_bot/tools/` 下的探针直调（平台自己的算力）。
**没有发现「不入队就干活」的现行漏洞** —— 但这条**没有守卫锁住**（见 §3）。

---

## 2. A 边守卫的结构性盲区（核心发现）

A 边的 `tests/test_credits_gate.py`（19 条）全部是这种形状：

```
扫「所有 db.enqueue_task* / credits.charge* 的调用点」→ 问「每个入队都收费吗」
```

⇒ 它的**模型前提**是：**消耗算力 = 走队列 = 先扣费后干活**。

⇒ **而这个前提本身没有任何守卫锁住**。
一个新增功能只要「直接调 LLM、不入队」，就完全落在所有扫描器的视野之外。

**实测证据**（`scripts/mutate_gate_adversarial.py`，M1 变异）：

| | A 边守卫 | B 边守卫 |
|---|---|---|
| 在 `execute_qa` 里多加一处 `_qa_llm()` | **19 条全绿** ❌ | 🔴 `test_compute_site_call_counts_match` 红 |

⇒ 这不是「A 边写错了」，是**覆盖面不同**：A 边管「入队侧收费对不对」，
B 边管「消耗侧有没有被漏记」。**两边合起来才是完整的闸门。**

---

## 3. 本轮新增的 15 条守卫（`tests/test_credits_gate_adversarial.py`）

| # | 守卫 | 锁住什么 |
|---|---|---|
| 1 | `test_compute_call_sites_are_registered` | **消耗点必须登记**（双向：漏登记红 / 登记失效红） |
| 2 | `test_compute_site_call_counts_match` | 同一函数里的**调用点数**必须与登记一致（新增一处要留痕） |
| 3 | `test_registered_compute_actions_are_priced` | 登记为「要收费」的 action 必须在成本表里且价格为正 |
| 4 | `test_unpriced_compute_sites_explain_themselves` | 「不收费」的消耗点必须写清理由（写不出上层 = 白干） |
| 5 | `test_multi_call_sites_declare_their_multiplier` | 一次收费打多次调用必须显式登记倍数 |
| 6 | `test_charge_action_matches_task_type` | **收费动作 ↔ 任务类型必须对得上**（收错价） |
| 7 | `test_every_cost_table_action_has_a_valid_task_type` | 成本表每个 action 都要登记配对 |
| 8 | `test_direct_charged_enqueue_cost_must_equal_price` | 直接调底层入队时 **cost 必须 == 表价** |
| 9 | `test_gate_forward_layer_reads_price_from_table` | 转发层必须**查表**拿价，不能写死数字 |
| 10 | `test_deduct_is_only_used_as_zero_cost_tracing` | **`db.deduct` 裸扣费入口**只许零成本留痕 |
| 11 | `test_failed_task_refunds_exactly_once` | **退款幂等**（真缺口，已修） |
| 12 | `test_refund_amount_equals_charged_amount` | 退款额 == 实扣额 |
| 13 | `test_zero_cost_task_is_not_refunded` | 零成本任务失败不许退款（否则白送） |
| 14 | `test_out_of_scope_compute_is_declared` | 范围外的消耗面必须显式登记 |
| 15 | `test_out_of_scope_declarations_are_real` | 范围声明不许挂空 |
| 16 | `test_fail_task_and_refund_is_idempotent` | **原子退款函数幂等**（同一个任务最多退一次） |
| 17 | `test_fail_task_and_refund_marks_task_failed` | 原子退款**同时**标记任务失败（不能只退钱不标记） |
| 18 | `test_reclaim_stale_collecting_respects_time_threshold` | **僵死回收要有时间门槛**（刚领走的任务不许被抢回） |
| 19 | `test_refund_entry_is_not_used_in_business_code` | **业务代码里零裸退款**（退款只许走原子函数） |

### 3.1 每条守卫都做过变异验证（`scripts/mutate_gate_adversarial.py`）

| 变异 | 破坏什么 | B 边 | A 边 |
|---|---|---|---|
| M1 | QA 入口多打一次模型（消耗点长出来了没登记） | 🔴 红 | **19 全绿** |
| M2 | 转发层把价格写死成 100 | 🔴 红 | 🔴 红（余额断言连带挂） |
| M3 | worker 退回「先 mark 再 refund」老写法 | 🔴 红（2 条） | **19 全绿** |
| M4 | qa 任务配 `collect_douyin` 的价 | 🔴 红 | **19 全绿** |
| M5 | 零成本留痕点改成真扣 100 | 🔴 红 | **19 全绿** |
| M6 | 新增未登记的裸扣费调用点 | 🔴 红 | **19 全绿** |
| M7 | 直接调底层入队、用错价（少收 99 分） | 🔴 红 | **19 全绿** |
| M8 | 原子退款函数去掉幂等判重 | 🔴 红（2 条） | **19 全绿** |
| M9 | 僵死回收去掉时间门槛 | 🔴 红 | **19 全绿** |

⇒ **9/9 全抓**；**8/9 变异下 A 边完全全绿**（M2 因连带余额断言被抓住 —— 属侥幸，不是设计）。

---

## 4. 抓到的真缺口（可自证）—— **三个已全部修完**

> 老大 2026-10-25 拍板「按你的建议来」⇒ 三个缺口由 B 边**直接实施**
> （改了 `db.py` / `config.py` / `worker.py`，已在交接单登记占用）。

### ★ 缺口 1：退款没有幂等保护 ⇒ 白送积分（**已修**）

**位置**：`daren_bot/worker.py: _fail()` + `daren_bot/db.py`

**原来的写法**（两个独立写）：

```python
db.mark_task(task["id"], "failed", reason)      # ①
if cost > 0:
    db.refund(task["tenant_key"], cost, "refund", ref_task_id=task["id"])   # ②
```

**三个问题**：

1. **没有幂等保护** —— 同一个任务被 `_fail` 两次就**退两次钱**。
   - 可达路径：worker 重启时 `db.reclaim_stale_collecting()` 把 `collecting`
     打回 `pending`（**无时间门槛** ⇒ 正在执行的任务也会被回收）→ 重跑又失败 → 双退。
   - `bot_credits_ledger` **没有** `ref_task_id` 唯一约束 ⇒ DB 层拦不住。
   - ⇒ **白送积分**（比多扣更糟：那是直接造钱）。
2. **两个写不在一个事务** —— 崩溃窗口里出现「标记了失败但没退款」
   ⇒ 达人的分**永久被扣**（`failed` 任务不会被 reclaim 捡回来重试）。
3. **判重靠任务状态不靠流水** —— 状态和退款是两个独立写：
   「状态=failed 但没退」⇒ 按状态判**永远不补退**；「钱退了但状态没改」⇒ 按状态判**重复退**。

**修法**（与扣费侧 `enqueue_task_charged` **同型**）：

- 新增 `db.fail_task_and_refund(task_id, error, tenant_key, cost)` —— **一个事务**里做
  「查幂等（流水里退过没有）→ 标记 failed → 退款」；
- 幂等判据是**流水**（`action='refund' AND ref_task_id=?`），不是任务状态；
- `worker._fail` 改成只调这一个原子函数（不再自己写两笔）；
- ★ **金额与租户的口径**：任务行存在 ⇒ 用**行里**的（账本真源：实扣多少退多少）；
  任务行**不存在** ⇒ 用**调用方传入**的值兜底。
  **理由：钱的事上「拿不准就不退」是错的** —— 静默跳过退款 = 达人的分被吞
  （客诉 + 信任损失）。宁可多退（有流水可查、幂等判重仍生效），也不能一分钱不退。

**与 A 边的合并**（`worker._fail` 两边都改过 ⇒ 真冲突）：
按 A 边交接单 §7.2 的配方合 —— **两边特性都保住**：
A 边的话术层（`failure_text` 达人看人话）+ 运营告警（`ops_alert`、登录态掉线告警）
全部保留，只把退款那两行换成原子函数；`exc` 参数、两个 import 一行没丢（§7.3）。

**证据**：`test_failed_task_refunds_exactly_once` + `test_fail_task_and_refund_is_idempotent`
+ `test_refund_happens_even_if_task_row_missing`
（M3 / M8 变异把它们打红 ⇒ 守卫会红，不是摆设）。

### ★ 缺口 2：`reclaim_stale_collecting()` 没有时间门槛（**已修**）

```python
# 原来：回收**所有** collecting —— 不区分是不是真的卡死了
"UPDATE bot_collect_tasks SET status='pending' WHERE status='collecting'"

# 现在：只回收「领走超过 30 分钟」的
"UPDATE bot_collect_tasks SET status='pending', error='' "
"WHERE status='collecting' AND (claimed_at IS NULL OR claimed_at < ?)"
```

配套：

- 新增 `bot_collect_tasks.claimed_at` 列（记录**被 worker 领走**的时刻）
  + `init_db()` 里的轻量迁移；
- `claim_next_task()` 领走时写入 `claimed_at`；
- 门槛 `STALE_COLLECTING_SECONDS = 1800`（30 分钟）
  —— 取值依据：单次采集最长 `timeout=240s`，视频拆解（下载+转写+vision+报告）更久，
  30 分钟远高于任何正常任务；
- ⚠️ 判据用 `claimed_at`（**领走**的时刻）而不是 `created_at`
  —— 否则「排队很久、刚被领走」的任务会被立刻判成僵死 ⇒ 白重跑一遍。

**效果**：worker 重启的重叠窗口里，**正在执行的任务不会被抢回重跑**
⇒ 算力不再跑两遍，也不再有双退的触发面。

### ★ 缺口 3：退款与状态更新没合成一个事务（**已修**）

随缺口 1 一并闭合 —— `fail_task_and_refund()` 把两笔写放进**同一个事务**
（`db_ctx()` 上下文管理器里）⇒ 要么都成，要么都不成。

### ★★ 附带堵掉的后门：裸退款入口

退款下沉到原子函数后，`db.refund(...)` 在**业务代码**里就成了后门
（绕开幂等与状态标记 ⇒ 「退两次钱」「标记没改」两个老坑全部复活）。
⇒ 新增守卫 `test_refund_entry_is_not_used_in_business_code`：
业务代码里**零调用**，只允许登记过的运维脚本用
（当前只有 A 边的真库探针 `scripts/probe_credits_real_db.py:main`，
探针自己扣的分测完手工退回来，人工操作、不在业务链路上）。

---

## 5. 已排查、确认**不是**缺口（省得以后重复怀疑）

| 怀疑对象 | 结论 | 依据 |
|---|---|---|
| 主服务端 `solobrave-server.py` 的 LLM 链路 | **有意不在达人闸门范围** | `_call_ai_api`（AI 员工聊天）、`_call_ai_analysis`（`product_analyze` / `talent_analyze`）都是**运营侧**接口，烧平台算力、不扣达人分 ⇒ 已写进 `OUT_OF_SCOPE` 登记表 |
| `executors.py` 里的 `recollect` 二次收费 | **正确** | 那是 worker 执行期发现「这达人没数据」→ 新起一个采集任务（100 分）⇒ 新任务新收费，不是重复收费 |
| `execute_qa` 的 3 次重试只收 1 分 | **口径如此，已显式登记** | 口径是「按功能收费」不是「按调用收费」；倍数写进了 `COMPUTE_SITES` 的 `max_calls` 字段 |
| `_qa_llm` 降级链打 4 条环只收 1 分 | 同上，已登记 | 同上 |
| `credits.enqueue_free` 记 0 分流水 | **合规** | 命中缓存 ⇒ 根本没调 LLM ⇒ 不收费但留痕（`cost=0`，在 `ALLOWED_DEDUCT` 登记） |

---

## 6. 本轮改了什么（文件级）

| 文件 | 性质 | 说明 |
|---|---|---|
| `daren_bot/db.py` | **改**（已登记占用） | 新增 `fail_task_and_refund()` 原子退款 + `reclaim_stale_collecting` 时间门槛 + `claimed_at` 列与迁移 |
| `daren_bot/worker.py` | **改**（已登记占用） | `_fail` 改走原子退款函数（不再自己写两笔） |
| `daren_bot/config.py` | **改**（已登记占用） | 成本表旁写死计费口径（按功能收费） |
| `scripts/secret_scan.py` | **改**（已登记占用） | 修「worktree 里跑不起来」（`.git` 在 worktree 是文件） |
| `tests/test_credits_gate_adversarial.py` | **新增** | 19 条对抗守卫 |
| `scripts/mutate_gate_adversarial.py` | **新增** | 变异验证（9 个变异） |
| `docs/ADVERSARIAL-REVIEW-2026-10-25.md` | **新增** | 本报告 |

**A 边其余文件没碰**：
`daren_bot/{credits,executors,handler,weekly}.py`、
`tests/test_credits_gate.py`、`tests/test_product_intent.py`、
`scripts/{mutate_new_guards,probe_credits_real_db}.py`、`AGENTS.md`、
`docs/PROGRESS-2026-10-22.md`。

---

## 7. 三件待办的处置结果（老大 2026-10-25 拍板「按你的建议来」）

| # | 事项 | 处置 |
|---|---|---|
| ① | `db.py` 加 `fail_task_and_refund()` 原子函数 + `reclaim_stale_collecting` 加时间门槛 | ✅ **已实施**（缺口 1/2/3 全部闭合，见 §4）。新增 `claimed_at` 列 + 迁移 |
| ② | 两份守卫纳入常规跑 | ✅ **天然满足**：`pytest.ini` 的 `testpaths = tests` ⇒ `tests/test_credits_gate_adversarial.py` 会被全量自动收集（实测全量 934 passed）。⚠️ 本项目**没有远端 CI**（无 `.github/workflows`）⇒ **本地 `pytest tests/` 就是常规跑** ⇒ 谁忘了跑，谁就看不见自己打破了闸门 |
| ③ | 「一次收费对应多次调用」的口径 | ✅ **已定死**：**按「功能」收费，不按「调用次数」收费**（理由写在 `config.py` 的成本表注释里）。倍数在 `COMPUTE_SITES.max_calls` 登记，成本核算不靠猜 |

### 还需要人决定的（只有这一件）

**A 边的 19 条守卫要不要也按「消耗侧」补一补？**
—— 不补的后果已经实测出来了：6/9 个变异下 A 边全绿（盲区）。
—— 本轮 B 边的 19 条守卫已覆盖「入队侧 + 消耗侧 + 退款侧」，
**两份合起来才是完整的闸门**；单独任何一份都有盲区。

---

## 8. 怎么用这份产出

```bash
cd /Users/qichen/sb-dev/parallel-handoff-1025

# 只跑 B 边的对抗守卫（15 条，~3 秒）
python3 -m pytest tests/test_credits_gate_adversarial.py -q

# 变异验证：证明这 15 条真的会红（~2 分钟）
python3 scripts/mutate_gate_adversarial.py

# 只跑一个变异
python3 scripts/mutate_gate_adversarial.py --only M3
```

> ⚠️ 变异脚本会**临时改代码**再还原 —— 只备份它自己动过的文件，
> 还原后逐字校验。**别在别人正在改同一批文件时跑它。**
