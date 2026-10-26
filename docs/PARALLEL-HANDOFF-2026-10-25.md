# 并行开发交接单（2026-10-25）

> **两个对话处理的是同一个系统（daren_cockpit + daren_bot）。**
> 本文件是两边唯一的交接面。**开工前先读，改完后来更新。**

---

## 0. 一句话现状

积分闸门刚收完并已推 `origin/dev`（`c8e849a`）。
**基点 = `c8e849a`**，两边都从这里起。

---

## 1. 两边的物理隔离（已配好，别改）

| | 对话 A（贾维斯 / 本对话） | 对话 B（老大新开） |
|---|---|---|
| 工作目录 | `/Users/qichen/solobrave-prod` | `/Users/qichen/sb-dev/parallel-handoff-1025` |
| 分支 | `main` | `feat/parallel-handoff-1025` |
| 基点 | `c8e849a` | `c8e849a` |

### ⚠️ 两个目录的分工铁律

1. **两边不要同时改同一个文件。** 同文件冲突是并行开发唯一的高频事故源。
2. **B 边的改动推自己的分支**，不合进 `main`；合入由 A 边统一做。
3. ⚠️ **绝对不要在 `/Users/qichen/Desktop/solobrave` 干活** ——
   那是同远端的**另一个老 clone**：停在 2026-10-05、落后 **1500 个 commit**、
   连 `daren_bot/` 目录都没有。在那儿开工 = 改一份没人部署的旧代码。

---

## 2. A 边刚做完的（别重复做）

**积分闸门**（老大：「每一个功能都需要积分控制 毕竟是消耗算力 不消耗的就算了」）

| 层 | 文件 | 职责 |
|---|---|---|
| 成本表 | `daren_bot/config.py:COST_TABLE` | **唯一真源**：动作名 → 价格 |
| 闸门 | `daren_bot/credits.py` | `charge_and_enqueue()` —— **唯一扣费入口** |
| 原子操作 | `daren_bot/db.py:enqueue_task_charged` | 幂等查→查余额→扣费+建任务（一个事务） |

补了三处白干：周报生成(30) / 周报自动采集(100) / 采集失败重采(100)。
修真顺序 bug：`_route_douyin` 原来「先回『已开始采集』→ 再扣费」。
修真漏洞：重复请求扣两次钱只建一个任务（实测扣 200 / 只退 100）。

**A 边动过的文件**（B 边改这些要先打招呼）：
`daren_bot/{config,credits,db,executors,handler,weekly}.py`、
`tests/test_credits_gate.py`、`tests/test_product_intent.py`、
`scripts/{mutate_new_guards,probe_credits_real_db}.py`、
`AGENTS.md`、`docs/PROGRESS-2026-10-22.md`

---

## 3. 已知待办（两边可分工，但**一个只能有一个**）

### 3.1 待老大拍板（**A 边在等回答，B 边别擅自定**）

| # | 事项 | 备注 |
|---|---|---|
| ① | **LLM 兜底链没有真正独立的第二家** | 6 环实测只有 minimax 家族真独立；kimi 429 月额度用尽；`api.minimaxi.com` 402；环里没 zhipu。⇒ 需老大拍板：智谱补资源包 or 换别家 |
| ② | 今天没排品要不要发占位消息 | 现在是**彻底静默** |
| ③ | 转化率 / 榜单排名 / 近7日短视频销量怎么采 | 转化率是**常量假值**（覆盖率 10.3%，只有 1 种取值 3.5） |
| ④ | 商品结论缓存扣 5 分 vs 问答缓存命中扣 0 分 | 口径不一致，既有定价决策 |
| ⑤ | `Ray`/`上官婉儿`/`孔明` 的 `createdBy=user_5b46ceaa` 在 `users.json` 里不存在 | ⇒ 这三个 AI 员工在所有 owner 过滤接口上是瞎子。**数据归属问题，A 边没擅自改，等老大定** |
| ⑥ | `origin/main` 是否合并 dev | 现在只推 dev，main 停在 `64f3ee2` |

### 3.2 已知缺陷 / 缺口（**A 边查出来的，状态如下**）

| # | 事项 | 状态 |
|---|---|---|
| a | `coop` 调度 30 分钟扫一次，matched=0 / pushed=0 | **不是 bug** —— 唯一 1 条记录状态「待评估」不在触发集（有意向/已合作）⇒ 要运营点状态 |
| b | 七月姐姐（`tal_1787206498347_6ea19d`）飞书 + 自看排品表双断 | 5 单但**无抖音号、无租户**；H5 又要 ops 口令 |
| c | 飞书 `k` 等同访问密码 | 对外口径不出现「巨量百应」 |
| d | macOS TCC 录屏权限未授予终端/MiniMax Code | 截不到飞书客户端窗口，只能用 OpenAPI 回读取证 |
| e | Kimi key 池月额度用尽(429) / zhipu LLM 余额不足(1113) | 与 ① 相关 |

---

## 4. 环境与真库（**别踩**）

- 服务：server `:8080` / daren_bot `:8092` / daren_cockpit `:7188` /
  buyin_worker `:8095` / OpenClaw `:18789`
- **改了 `daren_bot/` 或 `daren_cockpit/` 的代码 ⇒ 必须重启对应服务**，
  且「重启生效」≠「验证通过」⇒ 要跑一遍它的所有入口。
- 主库 `data/solobrave.db`（products 137 / 在架 58 / deals 6 / talents 231 / kb_entries 636）
- bot 库 `data/daren-bot/daren_bot.db`（**独立库**，别拿主库的表去查）
- 排品池真源 = `deals` 表（状态 negotiating/sample_sent/approved）
- 租户：`data/tenants.json` 只有 `t_default` ⇒ **单租户模式**
- 飞书：daren_boss → `oc_13194fab6699f98c81492927277b849d`；
  daren_test2 → `oc_0dec9b2ae369e3c2e4217072609f599f`
- ⚠️ **不要在真库上做破坏性操作**；探针一律用**独立 target**（如 `probe_xxx`）
  并在验证后清掉自己造的数据。

---

## 5. 常用命令

```bash
cd /Users/qichen/solobrave-prod
python3 -m pytest tests/ -q                     # 915 passed / 4 skipped
python3 scripts/secret_scan.py                  # exit 0
python3 scripts/mutate_new_guards.py            # 变异验证（备份副本还原版）
launchctl kickstart -k gui/$(id -u)/com.solobrave.{daren-bot,daren-cockpit,server}
```

```bash
# 真实调度（会真推飞书，谨慎）
TOKEN=$(cat data/daren-bot/admin_token.txt | tr -d '\r\n')
curl -s -X POST "http://127.0.0.1:8092/admin/api/plan-scan?token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"dry_run":false}'
```

---

## 6. 协作规约（**每轮结束更新本文件**）

1. **开工前**：先 `git fetch origin` 确认基点，别在旧基点上写代码。
2. **改共享模块前**：在下面「占用登记」里写一行。
3. **每轮收工**：把「我改了什么 / 发现什么」写回本文件，**并 commit 推自己的分支**。
4. **要合进 main**：告诉 A 边，由 A 边合（避免两拨人同时动 `main`）。

### 占用登记

| 文件 | 持有方 | 开始时间 | 预计完成 | 备注 |
|---|---|---|---|---|
| `daren_bot/failure_text.py`（新）| A 边 | 2026-10-25 17:40 | ✅ 18:10 已交 | 失败话术翻译层 |
| `daren_bot/ops_alert.py`（新）| A 边 | 2026-10-25 18:00 | ✅ 18:10 已交 | 运营告警出口（多通道降级）|
| `daren_bot/login_watcher.py`（新）| A 边 | 2026-10-25 18:00 | ✅ 18:10 已交 | 登录态巡检线程 |
| `daren_bot/worker.py` | A 边 | 2026-10-25 17:40 | ✅ 18:10 已交 | 只改 `_fail` 展示层 |
| `daren_bot/executors.py` | A 边 | 2026-10-25 17:40 | ✅ 18:10 已交 | `NeedLogin` 异常 + 不回退 + 通道探活 |
| `daren_bot/handler.py` | A 边 | 2026-10-25 18:05 | ✅ 18:10 已交 | 只在 `_route_product` 加前置预检 |
| `daren_bot/config.py` / `main.py` / `admin.py` | A 边 | 2026-10-25 18:05 | ✅ 18:10 已交 | 告警配置 / 启动巡检 / 后台告警区 |
| `daren_bot/config.py` / `daren_bot/ops_alert.py` | A 边 | 2026-10-25 19:45 | ✅ 19:58 已交 | `.env` 加载 + 飞书告警走运营租户最近会话 |
| `solobrave-server.py` | A 边 | 2026-10-25 19:50 | ✅ 19:56 已交 | 只改 `_cleanup_agent_data` 一段（逐语句容错 + 补清漏表）|

> ⚠️ **B 边注意**：A 边与 B 边**都改过** `daren_bot/worker.py`、`config.py`、
> `executors.py`、`handler.py`、`main.py`、`admin.py` ⇒ 合 `origin/dev` 时**这几处会冲突**。
> ⇒ 合法：把 A 边 `origin/dev`（`8f50052`+）与 B 边 `origin/feat/parallel-handoff-1025`
>   （`58f864d`）两边都取，冲突处**人工过一遍**（A 边动的是展示层/告警层，
>   B 边动的是积分闸门的消耗侧守卫 —— 语义不重叠，但要确认 B 边的 spy
>   仍盯对入口：A 边把 `worker._fail` 改成收异常对象，B 边也改了 `worker.py`）。
> ⇒ `solobrave-server.py` **两边都没动**（B 边的 `58f864d` 只碰 daren_bot/scripts/docs）⇒ 无冲突。

### 最近更新

- **2026-10-25 19:58 A 边**：飞书告警切到运营租户最近会话（老大确认「我看得见」）+
  `daren_bot/config.py` 加 `.env` 加载；清理没用的 AI 员工（删「测试1」「子龙」，
  修「彻底删除」只清半截的老 bug）；Ray/上官婉儿/孔明 归属失效改挂 admin
  （Ray 的商品可见数 0 → 47）。见 `AGENTS.md §8.13`。
- **2026-10-25 18:10 A 边**：商品链接 bug + 登录态无感知 修完并推 `origin/dev`。
  新增 `failure_text.py` / `ops_alert.py` / `login_watcher.py`；
  改 `executors.py`（登录态类错误不许回退）、`worker.py`（达人看人话 + 运营收告警）、
  `handler.py`（前置预检）、`config.py` / `main.py` / `admin.py`。
  新增 `tests/test_failure_text.py`（38 条）+ `scripts/mutate_failure_guards.py`（15 条变异）。
  详细口径见 `AGENTS.md §8.12`。
- **2026-10-25 17:30 A 边**：积分闸门收完并推 `origin/dev`（`c8e849a`）；
  建好 B 边 worktree `sb-dev/parallel-handoff-1025`（基点 `c8e849a`）；
  本交接单首版。**B 边开工前请先读本文件。**

---

## 7. ★ 合并配方：A 边 × B 边（`origin/dev` × `origin/feat/parallel-handoff-1025`）

> 2026-10-25 19:58 A 边算清并已通过 `session send` 告知 B 边。
> **本节以文件为准**（会话消息可能来不及看 / 上下文丢了）。

### 7.1 冲突面精确到 3 个文件

```
git diff --name-status db1e9cc origin/dev                          # A 边
git diff --name-status db1e9cc origin/feat/parallel-handoff-1025   # B 边
comm -12 <(上两者 --name-only | sort)                              # 交集 = 冲突面
```

| 文件 | A 边改了什么 | B 边改了什么 | 怎么合 |
|---|---|---|---|
| `daren_bot/worker.py` | `_fail(task, reason, exc=None)`：达人看人话（`failure_text`）+ 运营收告警（`ops_alert`）；`_process` 传 `exc=e` | `_fail(task, reason)`：改走 `db.fail_task_and_refund()`（原子 + 幂等退款） | **⚠️ 真冲突，同一个函数 ⇒ 必须合起来**（配方见 7.2）|
| `daren_bot/config.py` | 顶部 `_load_env_file()`（读 `.env`）+ 底部运营告警配置 | `COST_TABLE` 上方的计费口径注释 | 三个区域互不重叠 ⇒ **一般能自动合**；若报冲突是行号位移造成的假冲突，**两边都留** |
| `docs/PARALLEL-HANDOFF-2026-10-25.md` | 占用登记 + §8.12/§8.13 更新 | 占用登记 + 对抗审查结论 | 文本冲突，**两段都留** |

**只有 B 边改、A 边没碰** ⇒ 无冲突：
`daren_bot/db.py`、`scripts/secret_scan.py`、`scripts/mutate_gate_adversarial.py`、
`tests/test_credits_gate_adversarial.py`、`docs/ADVERSARIAL-REVIEW-2026-10-25.md`。

### 7.2 `worker.py::_fail` 合并版（两边特性都保住）

```python
    def _fail(self, task: dict, reason: str, exc: BaseException | None = None) -> None:
        """任务失败：B 边「原子+幂等退款」+ A 边「达人看人话 / 运营收告警」。"""
        cost = int(task.get("credits_cost") or 0)
        refunded, _balance = db.fail_task_and_refund(task["id"], reason)   # ← B 边的原子退款
        product_scene = task.get("type") in ("product", "image")
        if exc is not None:
            user_text = failure_text.user_text_for(exc, product=product_scene)
            ops_text = failure_text.ops_text_for(exc)
        else:
            user_text = failure_text.user_text_for(RuntimeError(reason), product=product_scene)
            ops_text = reason
        log.warning("[task-failed] id=%s type=%s refunded=%s cost=%s ops=%s",
                    task["id"], task.get("type"), refunded, cost, ops_text)
        if exc is not None and failure_text.is_login_failure(exc):
            ops_alert.alert(
                "⚠️ 达人端采集通道掉登录了",
                failure_text.ops_alert_body(
                    exc, context=f"任务 {task['id']}（{task.get('type')}）失败"),
                kind="login_lost", dedup_key="daren_collector_need_login")
        suffix = f"（已返还 {cost} 积分）" if cost > 0 else ""
        self._notify(task["tenant_key"], f"{user_text}{suffix}", task.get("chat_id", ""))
```

`_process` 里两行同步恢复 `exc=e`：
```python
        except NotImplementedError as e:
            self._fail(task, str(e), exc=e)
        except Exception as e:
            log.exception("task failed: id=%s", task["id"])
            self._fail(task, f"{type(e).__name__}: {e}", exc=e)
```

### 7.3 ⚠️ `worker.py` 里两行**必须保住**（解冲突时最容易丢）

1. **`from __future__ import annotations`** ——
   生产跑的是 **python 3.9**（`/Library/Developer/CommandLineTools/…/python3.9`），
   没有它，`exc: BaseException | None = None` 在**类定义时**就 `TypeError` 起不来。
2. **`from . import failure_text` / `from . import ops_alert`** —— 少了直接 NameError。

### 7.4 合并后必跑（三样缺一不可）

```bash
cd /Users/qichen/sb-dev/parallel-handoff-1025
python3 -m pytest tests/ -q                   # 两边测试都要绿（A 38 条 + B adversarial）
python3 scripts/mutate_failure_guards.py      # A 边 15 条变异
python3 scripts/mutate_gate_adversarial.py    # B 边变异
launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-bot   # B 的 claimed_at 列要走迁移
```

⚠️ **间接耦合提醒**：`worker._fail` 现在带 `exc` 参数。
B 边若在 `_fail` 里加新逻辑，**必须保留 `exc`** ——
否则 A 边的告警链路会静默失效（`failure_text.is_login_failure(exc)` 拿不到异常对象
⇒ 采集通道掉登录不再告警运营）。

⚠️ **合并口径**：两边特性**都保住**，不做单边取舍。

---

## 8. A 边第 4 轮：LLM 降级链补「第二家独立服务商」（2026-10-25 20:50）

### 一句话

老大问「**补什么啊，要我再买API吗？**」⇒ 当场逐环实测的结论是
**不用买** —— 老大买的**智谱资源包本来就够用**（文字 + 识图都实测通），
但它**压根没接进降级链** ⇒ 这轮把它接进去，并给降级链加上
「**有几环 ≠ 有几家**」的判据。

### A 边这轮改了什么（已推 `origin/dev`）

| 文件 | 改动 | 与 B 边是否冲突 |
|---|---|---|
| `daren_bot/llm_channels.py` | 加智谱文字环（`glm-4-flash`/`glm-4.5-flash`）+ 识图环（`glm-4v-flash`）；所有环补 `vendor` + `pool` | ✅ **B 边没碰** |
| `daren_bot/tools/probe_llm_channels.py` | 加独立服务商/额度池统计 + 死因分类 | ✅ **B 边没碰** |
| `tests/test_llm_channels.py` | +9 条契约测试 | ✅ **B 边没碰** |
| `scripts/mutate_llm_ring_guards.py`（新）| 11 条变异验证 | ✅ **B 边没碰** |
| `AGENTS.md` §11 | 环序表更新 + 「有几环≠有几家」口径 + 智谱口径 | ✅ **B 边没碰**（B 边 3 个 commit 都没动 AGENTS.md）|
| `docs/PROGRESS-2026-10-22.md` §32 | 本轮全过程与沉淀 | ✅ **B 边没碰** |
| `docs/PARALLEL-HANDOFF-2026-10-25.md` | **只在文件末尾追加本节**（不碰 §6「最近更新」，那是 B 边改的）| ⚠️ 文本面：末尾追加，行级不重叠 |

⇒ **A 边这轮的 4 个代码文件与 B 边零交集** ⇒ 合并时这几个文件不会冲突。

### ★★ 给 B 边：上线阻塞**已解除**

B 边 `924ea8a` 记的「上线阻塞」是：
> 部署目录 `solobrave-prod` 有另一会话未提交的 `config.py` / `ops_alert.py` 改动，
> 与 B 边改的 `config.py` 同文件 ⇒ `git merge` 会被拒。

⇒ **那两个改动 A 边已经在 `c585dd7` 之前提交并推 `origin/dev` 了**，
部署目录工作区现在**干净**（A 边本轮只动 LLM 链相关文件）。
⇒ **B 边现在可以干净 merge 了** —— 不需要再等。

⚠️ 合并时 B 边要记得：`origin/dev` 这期间又多了
`c585dd7`（登录态单一真源，改 `buyin_worker.py` + 新增 `buyin_auth.py` + `tests/test_buyin_login_judge.py`）
与本轮的 LLM 链改动 ⇒ **冲突面仍是原来那 3 个文件**：
`daren_bot/worker.py`（真冲突）/ `daren_bot/config.py`（区域不重叠，疑似假冲突）/ 交接单（文本面）。

⚠️ **本轮新增的合并注意**：B 边如果改了 `daren_bot/llm_channels.py` 或
`daren_bot/tools/probe_llm_channels.py`，**要保留 `vendor` / `pool` 两个字段**
—— 它们是「独立服务商」判据的**唯一来源**，
丢了字段探针就会退化成「按 host 反推」⇒ 把一家数成三家。

### 本轮的实测结论（别再重新测，直接用）

| 环 | 通道 | 服务商 | 额度池 | 实测 |
|---|---|---|---|---|
| 0 | MiniMax 套餐（Anthropic）| minimax | cp-subscription | ✅ 1.1s |
| 1 | Code 订阅（=贾维斯自己）| minimax | code-subscription | ✅ 0.9s |
| 2 | MiniMax 套餐（OpenAI 协议冗余）| minimax | cp-subscription | ✅ 1.2s |
| 3/4 | **智谱 glm-4-flash / glm-4.5-flash** | **zhipu** | zhipu-resource-pack | ✅ 1.1s / 17s |
| 5 | kimi moonshot-v1-32k | kimi | kimi-monthly-quota | ❌ **429 月度额度耗尽**（下周期刷新）|
| 6/7 | minimax 开放平台 | minimax | minimax-open-balance | ❌ **402 余额不足** |

识图链：环0 MiniMax ✅ → 环1 **智谱 glm-4v-flash** ✅ 728ms → kimi ❌ → 开放平台 ❌。

⇒ **活的独立服务商 = 2 家**（minimax + zhipu）⇒ 冗余**是真的**了。

### 占用登记（本轮）

| 文件 | 持有方 | 开始时间 | 预计完成 | 备注 |
|---|---|---|---|---|
| `daren_bot/llm_channels.py` | A 边 | 2026-10-25 20:36 | ✅ 20:50 已交 | 加智谱环 + vendor/pool |
| `daren_bot/tools/probe_llm_channels.py` | A 边 | 2026-10-25 20:36 | ✅ 20:50 已交 | 加双维度统计 |
| `tests/test_llm_channels.py` | A 边 | 2026-10-25 20:36 | ✅ 20:50 已交 | +9 条契约测试 |
| `scripts/mutate_llm_ring_guards.py`（新）| A 边 | 2026-10-25 20:36 | ✅ 20:50 已交 | 11 条变异 |

### 本轮沉淀（跨边通用）

1. **「有几环」≠「有几家」** —— 冗余判据是**独立服务商/额度池**，不是环数；
   同一家的多条协议只是**协议冗余**（一个端点挂了另一个还能走），不是额度冗余。
2. **别从 host 反推服务商**：`api.minimax.cn` / `agent.minimax.cn` / `api.minimaxi.com`
   是同一家；而套餐 key 与开放平台 key 同属一家却是**两套独立的账**。
   ⇒ 字段写死在环构造处，下游只读不算。
3. **能力层测试：重试优先于 skip**；真要 skip 要按**错误语义**分类，
   **配置错误必须让它红**（本轮变异 M11 逼出来的 —— 只看状态码会把
   `503 + direct_route_not_configured` 当成瞬时 ⇒ 恒真绿）。
4. **降级链是顺序遍历** ⇒ 活环必须排在死环前面。
5. **不许拿旧数据回答「现在能不能用」** ⇒ 逐环真调一次。

---

## 9. A 边第 5 轮：木木回复变样 / 空回复的 6 处根因（2026-10-26 15:40）

> 触发：老大「木木回复消息，怎么回复跟以前弄的不一样啊」
> 提交：`24a7fb9`（已推 `origin/dev`；`origin/main` 未动）
> ⚠️ **B 边：本轮改的是 `solobrave-server.py` / `provider_adapters.py` /
> `switch_chat_model.py` —— 如果你也在改这几个文件，先说一声再动。**

### 现场取证（当场实测，没引用旧数据）

| 时间 | 现象 | 真相 |
|---|---|---|
| 14:24 | 木木回复开头漏出 `[[response_to_current]]` | 降级到老模型 `MiniMax-Text-01`（它会把 prompt 里的元数据标记当正文复述；`MiniMax-M3` 实测不会）|
| 15:05 / 15:08 | 木木只会说「我试试用图像识别工具重新读一遍」 | 它的 SOUL.md 被截断（304/556 字，缺【数据源强制约束】【工具使用铁律】），且**没有 TOOLS.md** |
| 15:12 | 木木**空回复** | 我重启服务端时把在途的那一轮打死了（我自己的操作，不是 bug）|

### 6 处根因（全是「声明端 ≠ 使用端」形态）

| # | 根因 | 后果 | 修法 |
|---|---|---|---|
| ① | 服务端按 `agents.json` 的 `openclawName` 拼 workspace（**声明端**），OpenClaw 运行时用 `openclaw.json` 的 `entry.workspace`（**使用端**）| 木木（`openclawName='main'`，6 人里唯一错位）读写**贾维斯**的 workspace；写文档会覆盖贾维斯 SOUL.md | `_resolve_agent_workspace()` 为唯一真源 + 写前归属校验（409 拒写）|
| ② | minimax 兜底档指向开放平台老端点 + 老模型 `MiniMax-Text-01` | 文风突变 + 标记泄漏 | 适配层改**套餐 Anthropic 端点 + MiniMax-M3** |
| ③ | `_resolve_ai_base_url` 里 `custom_endpoint` 被内置 mapping **覆盖** | settings.json 配的 baseUrl **静默失效** ⇒ minimax 打老端点 → 404 | 改成**配置优先**（默认只作兜底）|
| ④ | `_call_minimax_messages` / `_try_minimax_proxy_fallback` 写死 OpenAI 协议 | minimax 档换端点后协议/端点/模型全错位 | 统一走 `provider_adapters`（单一协议真源）|
| ⑤ | kimi key 池耗尽时在**链之前**就 503 | 「第一环死」被写成「全员死」⇒ 兜底档永远没机会接管 | chain 模式带空 primary 进链，由链引擎继续降级 |
| ⑥ | legacy 路径响应解析只认 OpenAI 形状 | 转发的明明是 Anthropic 端点，正常响应被误判成「格式异常」丢进垃圾兜底 | 补格式感知解析 |

### 实测效果（生产链路，非单测）

```
15:30:41  POST /api/proxy/kimi/v1/messages → 503   ← 修复前（key 池空就 503）
15:32:03  trying provider=kimi → 429
          trying provider=minimax format=anthropic url=https://api.minimax.cn/anthropic/v1/messages
          ✅ provider=minimax 成功（降级自 kimi）
15:32:03  POST /api/proxy/kimi/v1/messages → 200   ← 修复后（兜底档接管）
```

木木端到端（`openclaw agent --agent emp_1787550091124_1048`）：
回复「我是木木，你的商务助理，负责客户拓展、合作洽谈和商机跟进这一摊事儿。」
—— 人格正确（不再是被配上的贾维斯灵魂）、**无 `[[response_to_current]]` 泄漏**、带老板口吻。

### 本轮顺带复活的两份「一直死着的覆盖」★

| 文件 | 之前 | 现在 |
|---|---|---|
| `tests/test_proxy_engine.py` | 在 C1 就 `KeyError: 'content'` ⇒ 链引擎核心覆盖**从没跑过** | **57/57 全绿** |
| `test_multi_provider.py`（根目录）| 引用已废弃的 `_PROVIDER_FAIL_COUNTS` ⇒ 长期报错 | **22/22 全绿** |

⚠️ **教训（跨边通用）**：`tests/` 下有 **4 个脚本式文件**（`test_proxy_engine.py` /
`test_proxy_e2e.py` / `test_provider_adapters.py` / `test_kimi_response.py`）
**pytest 收集 0 条**（它们是 `main()` + `check()` 风格）⇒ 看着有覆盖，实际 CI 跑不到。
本轮修好 2 个；另 2 个（`test_proxy_e2e.py` 需真服务器、`test_kimi_response.py` 有 4 条历史失败）
**仍是脚本式、仍不被收集** —— 建议后续统一改成 pytest 风格。

### 新增/变更文件

| 文件 | 持有方 | 状态 |
|---|---|---|
| `solobrave-server.py` | A 边 | ✅ 已交 |
| `provider_adapters.py` | A 边 | ✅ 已交 |
| `switch_chat_model.py` | A 边 | ✅ 已交（minimax 默认端点）|
| `tests/test_openclaw_workspace.py`（新）| A 边 | ✅ 已交（9 条）|
| `tests/test_minimax_m3_fallback.py`（新）| A 边 | ✅ 已交（9 条）|
| `tests/test_minimax_protocol_consistency.py`（新）| A 边 | ✅ 已交（6 条）|
| `scripts/mutate_openclaw_workspace_guards.py`（新）| A 边 | ✅ 已交（W1-W5 全抓住）|
| `scripts/mutate_minimax_protocol_guards.py`（新）| A 边 | ✅ 已交（M1-M6 全抓住）|
| `tests/test_proxy_engine.py` / `test_multi_provider.py` / `tests/test_provider_adapters.py` | A 边 | ✅ 已交（复活 + 协议对齐）|

### 运行时配置改动（**不进 git**，data/ 被 ignore）

| 文件 | 改动 |
|---|---|
| `data/settings.json` | `llm.providers.minimax` → `https://api.minimax.cn/anthropic/v1` + `MiniMax-M3`；**删掉 zhipu 档**（老大口径：智谱资源包是知识库的 API）|
| `~/.openclaw/openclaw.json` | `models.providers.minimax.baseUrl` → `https://api.minimax.cn/anthropic/v1`（原值 `api.minimaxi.com/anthropic` 实测 **404**）；**6 个员工的模型链全部去掉 zhipu**，Ray/孔明 primary 从**死的** `zhipu/glm-4.7`（实测 429 code 1113）换成各自的 `kimi_proxy_*/k3` |
| `~/.openclaw/workspace-emp_1787550091124_1048/` | 补齐 `SOUL.md`（556 字完整）+ 新建 `TOOLS.md`（1238 字）|

### 本轮沉淀（跨边通用）

1. **「声明端 ≠ 使用端」有三种形态**，本轮全踩了一遍：
   ① 路径拼接用声明字段（`openclawName` vs `entry.workspace`）；
   ② 配置被代码默认值覆盖（`custom_endpoint` vs 内置 mapping）；
   ③ 协议写死在调用处（OpenAI vs Anthropic），换端点就错位。
   ⇒ **凡是要「配置生效」的地方，都要问一句：使用端到底读的是谁？**
2. **「第一环死」≠「全员死」**：任何在**降级链之前**的早退闸门，
   都会把「主档位不可用」写成「整条链不可用」。
   ⇒ 早退闸门必须问：**链后面还有档位吗？**
3. **恒真绿的死因文案比没有文案更坏**：提前 503 报「Key 池已空」，
   把真因（请求本身是空的）整整掩盖了 5 天 ⇒ **死因文案要按错误语义写**。
4. **看着有覆盖 ≠ CI 跑得到**：脚本式测试文件（`main()` + `check()`）
   在 pytest 下收集 0 条 ⇒ 改动前先确认「这条测试真的会被执行」。
5. **变异验证是发现「恒真闸门」的唯一可靠手段**：
   本轮 M5（把「池空继续走链」改回「池空就 503」）立刻变红 ⇒ 闸栏有效。
