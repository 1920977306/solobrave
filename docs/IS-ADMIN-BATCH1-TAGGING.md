# 第一批 is_admin 打标清单（P2-A 三个面，16 点位）

> 执行：A 边 · 2026-11-07 · 纯对账不改代码（行号对当日 `origin/dev` = `df5ef35`）
> 依据：`docs/AGENT-TIER-DESIGN.md` §5.1（分两批，第一批 20–30 处随 P2-A）+
> `docs/SUBJECTS-PACKS-RECONCILE.md`（三簇归属判据）
> 方法（拍板口径）：不从零猜——以存量 M5 样本（`is_admin or is_tenant_admin`
> 带「★ M5: 租户管理员可管内务」注释，全仓 12 处）反推分簇规律再套。

## 0. 分簇判据（从 M5 样本归纳，阶段 1 直接引用）

| 簇 | 判据 | 阶段 1 归属 |
|---|---|---|
| 平台运营面 | 跨租户：租户总览/聊天审计/开租户/升降合伙人/**积分充值口子** | `super_admin` only |
| 业务面 | 租户内：余额/流水/事实——主体只能看自己（+自己员工） | `tenant_owner`（数据按租户裁剪） |
| 展示/通知 | UI 标签、推送对象 | 不改语义只改文案 |

**★ 本次对账新增一条判据（11-02 拍板的推论）**：「积分只归平台，平台充值是唯一
积分口子」⇒ **一切充值/发放类端点 = 平台运营面 = super_admin only**——
即使它今天写成 M5 形态（`or is_tenant_admin`），也是错配，阶段 1 必须收回。

## 1. 面 A：飞书控制台租户面（8 点位）

| # | 行号 | 端点 | 现守卫 | 归属簇 | 阶段 1 动作 |
|---|---|---|---|---|---|
| A1 | :18782 | GET `/api/feishu/console`（租户总览聚合）| M5 | 平台运营面 | super_admin；tenant_owner 按本租户**裁剪**（同 P2-B `as_agent` 逻辑，server 侧先不做） |
| A2 | :18827 | GET `/api/feishu/console/role*`（角色模板内容）| M5 | 平台运营面 | super_admin only（角色模板全平台共享，tenant 无权改） |
| A3 | :18858 | GET `/api/feishu/console/chat`（聊天审计）| M5 | 平台运营面 | super_admin；tenant_owner 裁剪本租户 |
| A4 | :18881 | GET `/api/feishu/console/facts`（达人事实）| M5 | 平台运营面 | super_admin；tenant_owner 裁剪本租户 |
| A5 | :18900 | POST `/api/feishu/console/fact-delete` | M5 | 平台运营面 | super_admin；tenant_owner 仅允许删本租户事实 |
| A6 | :18918 | POST `/api/feishu/console/tenant-create`（填表开助理）| M5 | 平台运营面 | **super_admin only**——开租户是平台动作，M5 放行 tenant_admin 属错配 |
| A7 | :18811 | POST `/api/feishu/console/agent-enable` / `agent-parent`（升降合伙人/绑上级）| M5 | 平台运营面 | **super_admin only**——升降合伙人是平台特权（对账表 §2 #1 同判） |
| A8 | :20469 | GET `/api/user/feishu-config`（用户自读多维表格配置）| 仅登录 | 不进簇 | 个人自助配置，无权限语义，**零动作** |

## 2. 面 B：积分中心（8 点位）

| # | 行号 | 端点 | 现守卫 | 归属簇 | 阶段 1 动作 |
|---|---|---|---|---|---|
| B1 | :18391 | 余额列表 owner 过滤（`GET /api/credits/balance` 无 agent_id 时）| 裸 `is_admin` 分支 | 业务面 | tenant_owner 语义：非 admin 看 `visible_ids`（自己+自己员工）；阶段 1 换读 kind |
| B2 | :18547 | `admin_grant`（平台发放积分）| M5 | 平台运营面 | **super_admin only**（充值口子铁律；M5 错配收回） |
| B3 | :18663 | `GET /api/credits/quotas`（配额管理）| M5 | 平台运营面 | super_admin；tenant_owner 若要管自己员工配额，二期加「本租户配额」子视图 |
| B4 | :18700 | `credit_recharge`（给员工充值）| M5 | 平台运营面 | **super_admin only**（充值口子铁律；M5 错配收回） |
| B5 | :18735 | `GET /api/credits/feishu`（飞书 AI 积分聚合）| M5 | 平台运营面 | super_admin；tenant_owner 裁剪本租户 |
| B6 | :18752 | `POST /api/credits/feishu/recharge` | M5 | 平台运营面 | **super_admin only**（充值口子铁律；M5 错配收回） |
| B7 | :18931 | `GET /api/credits/usage`（使用记录分页）| **仅登录闸，无 owner 过滤** | 业务面 | ⚠️ **真缺口**：任何登录用户传任意 `agent_id` 看全平台流水。阶段 1 必须补 `visible_ids` 过滤（照抄 :18391 的写法） |
| B8 | :18987 | `GET /api/credits/usage/summary`（使用汇总）| **仅登录闸，无 owner 过滤** | 业务面 | ⚠️ **真缺口**：同 B7，补 `visible_ids` 过滤 |

> 备注：`GET /api/credits/check`(:19045) 也无过滤，但语义是「发送前余额检查」
> （内部调用点），归入业务面二期处理，不在第一批动。

## 3. 面 C：升降合伙人

已合并为面 A #A7（server 代理 bot `/admin/api/agent-*`，守卫在 :18811）。
bot 侧 `daren_bot/admin.py` 对应端点守卫同判：super_admin only。

## 4. 对账结论（三份清单，阶段 1 的直接输入）

**① M5 错配清单（今天放行 tenant_admin、阶段 1 必须收回 super_admin 的）——5 处：**
`:18547` admin_grant · `:18700` recharge · `:18752` feishu recharge ·
`:18811` agent-enable/parent · `:18918` tenant-create。
判据统一：**充值口子 + 升降合伙人 + 开租户 = 平台特权**（11-02「积分只归平台」拍板的推论）。

**② 真缺口清单（今天无闸/无过滤、阶段 1 必须补的）——2 处：**
`:18931` usage · `:18987` usage_summary（积分流水/汇总对非管理员裸奔）。

**③ 需按租户裁剪清单（tenant_owner 可见但只能看本租户的）——5 处：**
`:18782` console · `:18858` chat · `:18881` facts · `:18900` fact-delete · `:18735` feishu credits。
（与 P2-B `as_agent` 数据裁剪是同一件事，建议阶段 1 与 P2-B 合流一次做掉。）

## 5. 明确不做的（边界）

- 第二批全量 133 处：留到阶段 1 动 ②③ 之前（拍板）。
- 本清单不改任何代码——含 ② 缺口也只登记。修缺口是阶段 1 的活，
  避免「对账顺手改」把两周工程拆成不可审计的碎片。
