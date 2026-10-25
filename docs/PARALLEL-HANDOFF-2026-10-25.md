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

> ⚠️ **B 边注意**：以上 7 个文件本轮被 A 边占用过。
> 若 B 边也要改 `executors.py` / `handler.py` / `worker.py`，**先看本表 + `git log`**，
> 合并时注意这几处都有改动。

### 最近更新

- **2026-10-25 18:10 A 边**：商品链接 bug + 登录态无感知 修完并推 `origin/dev`。
  新增 `failure_text.py` / `ops_alert.py` / `login_watcher.py`；
  改 `executors.py`（登录态类错误不许回退）、`worker.py`（达人看人话 + 运营收告警）、
  `handler.py`（前置预检）、`config.py` / `main.py` / `admin.py`。
  新增 `tests/test_failure_text.py`（38 条）+ `scripts/mutate_failure_guards.py`（15 条变异）。
  详细口径见 `AGENTS.md §8.12`。
- **2026-10-25 17:30 A 边**：积分闸门收完并推 `origin/dev`（`c8e849a`）；
  建好 B 边 worktree `sb-dev/parallel-handoff-1025`（基点 `c8e849a`）；
  本交接单首版。**B 边开工前请先读本文件。**
