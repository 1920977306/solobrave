# 阶段 0 #1：现有身份/权限检查点 → subjects × packs 对账表

> 执行：A 边（Mac 生产侧 `/Users/qichen/solobrave-prod`）· 2026-11-02
> 依据：`SoloBrave架构重设计_主体与能力包_v2` §3.2 五层流水线（②③新增，④⑤保留）
> 性质：**纯对账，不改代码**。所有行号对的是当日生产代码，改代码前先重核行号。

---

## 1. 现有权限的真实结构（核实结果，比 v2 文档多一点）

v2 §3.1 说权限是三层（is_admin / 模块白名单 / owner 过滤）。核实后**补一个 v2 没写的事实**：
**M1 租户层的骨架已经在了**，不是全新增：

| 已有件 | 位置 | 说明 |
|---|---|---|
| `_tenant_db_path(tid)` | `solobrave-server.py:3414` | 非默认租户重定向独立 DB；`t_default` 落 legacy DB（零拷贝） |
| `auth.tenant_id` | `:2617-2621` | token 里的 `tid` 优先，回填默认 t_default |
| `is_platform_admin` | `:2609-2611` | **M1 已显式命名** = 跨租户运营（现实现同 `role=='admin'`） |
| `is_tenant_admin` | `:2613-2616` | M1 已引入，存量数据无此角色 ⇒ **恒 False，是现成的 tenant_owner 挂载点** |
| users 带 `tenant_id` | `data/users.json` | 4 条记录全带（恒 `t_default`） |
| agents 带 `tenant_id` | `data/agents.json` | 6 个 AI 员工全带 |

**结论：v2 五层里的 ②（租户边界）= M1 骨架已存在，阶段 1 是"启用 + 补闸门"，不是从零造。**

## 2. 角色硬编码逐处判归属（核心对账成果）

`role == 'admin'` 全仓共 6 处出现，**每一处的归属不同，不能一刀切**：

| # | 位置 | 语义 | 归属判定 | 迁移动作 | 风险 |
|---|---|---|---|---|---|
| 1 | `:2605` `is_admin` property | **主开关，全仓 133 处消费** | ⚠️ **不能整体归一类**——133 处按消费场景分两簇：平台运营面（租户管理/权限管理/积分中心/飞书配置/违禁词）→ `kind=super_admin`；业务面（知识库/达人库/商品库/AI 员工管理）→ 保留或归 `tenant_owner` | 阶段 1 前必须把这 133 处**逐处打标**（清单见 §4），否则拆了主开关就是全面爆炸 | **高** |
| 2 | `:2610` `is_platform_admin` | 跨租户运营（M1 命名） | ✅ 直接就是 `super_admin` 的语义 | 实现换读 subjects.kind，**命名不用改** | 低 |
| 3 | `:2615` `is_tenant_admin` | 租户管理员（M1 占位） | ✅ 现成的 `tenant_owner` 挂载点 | 存量数据从恒 False 变为可分配 | 低 |
| 4 | `:2780` `_check_agent_role_write_scope` | ⚠️ **这是 AI 员工的人格 role，不是用户 role** | **不进 subjects 迁移**——归「人格 ≠ 身份」桶，和 bot_tenants.role 同类 | 零动作；守卫测试锁「不许给这里挂业务身份」 | 零（动它才危险） |
| 5 | `:32186` `role_display` | UI 标签（老板/组长/员工） | 仅展示 | 阶段 2 加 kind 显示时顺手改 | 零 |
| 6 | `:37985` watchdog 找 admin 推通知 | 系统通知对象 | `platform_ops`（或保留 super_admin） | 一行改读 | 低 |

**v2 §3.3 原表写的是"role=='admin' #1/#2/#3 三处"，精确数字应为上面 6 处——其中只有 #1–#3 是用户角色，#4 是人格 role（最易混），#5/#6 无权限语义。**

## 3. 三层权限机制的准确名称（v2 文档可直接引用）

| v2 层 | 代码真名 | 核实位置 |
|---|---|---|
| ④ 模块白名单（§117 权限管理页） | `/api/permissions` + `/api/permissions/roles/:id` + `/api/permissions/users/:id`（角色级 + 用户级两级） | `solobrave-server.py:9247, 10304-10312`；导航 `perm:` 字段 + `adm:true`（inline-09.js:40, 1725） |
| ⑤ owner 数据过滤 | `_resolve_talent_owner_id(auth)` → `visible_ids = {uid} ∪ AI员工ids` | `:2740`，消费 5+ 处（达人/商品/知识库） |
| ③ 能力包（新增） | 尚无承载点 | 新增 `subject.packs` + `pack.modules → ④ 白名单` 的推导关系 |

**关键连接关系**：④ 现在是独立配置的；落地 ③ 时建议「白名单 = 该主体 packs 的 modules 并集」为默认推导、页面保留手工微调——这样 ④ 自动成为 ③ 的下游投影（v2 §3.2 的原话），不用迁移存量配置。

## 4. 三套身份映射现状（v2 §4 的代码落点）

```
现状（无外键，靠名字对）                目标（subject_id 外键）
users.json  4 人                        subjects ←┬ bot_tenants 3 条
agents.json 6 AI 员工                            └ agents.json 6 个
bot_tenants 3 条（另一个 DB）
```

| 对象 | 现在 | 挂 subject_id 的成本 |
|---|---|---|
| users.json | 有 `tenant_id`（t_default）、`role`（admin/employee） | 低：4 条手工映射一次 |
| agents.json | 有 `tenant_id`、`role`（人格）、`permission`、`createdBy` | 低：6 个映射；`createdBy=user_5b46ceaa` 的 3 个孤儿（Ray/上官婉儿/孔明）映射前先解决归属 |
| bot_tenants | 有 `role`（人格 daren/commerce/merchant）、无 tenant_id 概念 | 中：在 daren_bot.db，跨库外键用 `subject_id` 字符串 |

**映射种子数据（拍板后可以直接用）**：
- 贺主管（user_30f05078, employee）→ kind=biz_staff
- admin（user_7acb72ff）→ kind=super_admin（同时是 tenant_owner of t_default）
- daren_boss / daren_test2 / mumu 三个 bot 租户 → kind=creator（达人主体）
- Ray / Helen → 挂贺主管；木木 → 挂其真实所有人（10-27 对账有结论）

## 5. 阶段 1 前置清单（从对账表直接推出来的）

1. **133 处 is_admin 逐处打标**（§2 #1）——这是阶段 1 最大的活，也是唯一高风险的。产出：每处标注 [platform] 或 [tenant]。先做清单再动刀。
2. 复用 `is_tenant_admin` 挂 tenant_owner，**不要新造属性名**。
3. `is_platform_admin` 语义不变，实现换读 kind——**改名零成本**（M1 命名已准）。
4. `_check_agent_role_write_scope`（:2780）加一行守卫注释：此处 role 是 AI 人格，与 subjects.kind 永不互通。
5. 三套身份映射种子数据先落成 migration 脚本（dry-run 默认），拍板后 --apply。

## 6. 与 P2 的衔接

P2 的 `agent_of`/`agent_enabled` 两列（AGENT-TIER-DESIGN.md）= 本表 §4 里 `subjects.agent_of` 的 bot 侧投影。两份文档不冲突：**P2 先落 bot 侧两列（一周），subjects 大表后合流（阶段 1）**。
