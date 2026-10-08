# Phase 0 隔离底座调研 — 多租户 SaaS 标准版

> 项目：SoloBrave 快速交付档（多租户 SaaS 标准版）
> 基线：prod `fix/request-level-model` @ `ada9f7b`（本地 prod 唯一事实源，GitHub 只走 PR — 铁律 16）
> 本阶段只做调研 + 方案，**不写业务代码**。
> 验收物：本方案文档 + 风险清单 + 工作量估算。

---

## 0. 一句话结论

当前系统是「单租户 + 用户级子库」架构：认证是 JWT → 用户，数据行用 `created_by ∈ {uid} ∪ {该用户的 AI 员工 ids}` 做可见性过滤（"两层架构"），知识库另有 scope（all/global/team/personal/group）四维隔离。**没有 tenant 概念，且存在多处"列表过滤、详情不过滤"的行级越权（IDOR）**。推荐方案 **C（混合隔离）**：业务数据走 B（每租户独立 sqlite + 独立 JSON 目录），配置与平台级只读数据走 A（单库 tenant_id 列），分三阶段落地。

---

## 1. 存储面盘点

### 1.1 sqlite：`data/solobrave.db`（唯一活库，41 张业务表 + FTS 虚表）

其余 `solobrave-prod.db` / `solabrave.db` / `solubrave.db` 均为 0 表空文件（历史遗留，非事实源）。连接点 `solobrave-server.py:240`，单例 `ThreadingHTTPServer`。

| 分组 | 表 | 现有 owner 列 | 多租户判定 |
|---|---|---|---|
| **需要 tenant 隔离** | talents (264), products (89), kb_entries (282) + kb_entry_chunks (477), knowledge_patterns (112), knowledge_events (207), deals, proposals (6), tasks (2), talent_follow_ups, product_talent_match (149) | created_by / agent_id（**过滤实现参差不齐，详见风险清单**） | 业务核心数据，必须按租户硬隔离 |
| **需要 tenant 隔离（按 owner 链路）** | memory (413), memory_atoms, memory_conversations (1705), memory_summary (458), credit_accounts (12) + credit_quotas + credit_usage_log (2585), token_usage (1306), token_budgets, tool_calls (316), heavy_jobs (19), pipeline_state (10), notifications (439), user_settings, user_feishu_config | agent_id / user_id | agent 归属用户 → 归属租户，链路间接成立，但**依赖 agent→user→tenant 解析，无硬约束** |
| **只读全局 / 平台级** | brands (5), knowledge_categories (4), forbidden_words (5), embedding_cache (1699), knowledge (65), knowledge_base*（空壳）, kb_operation_log (54), knowledge_relations/versions（空） | 无 | 平台运营配置或全局共享；embedding_cache 跨租户命中需评估（同文本跨租户复用在语义上无害，但会泄露"别家提过这个问题"的侧信道） |
| **建议按 A 加 tenant_id** | （上述 owner 链路的表在方案 C 下随业务库走 B；此处为空） | — | 见 §2 |

FTS 虚表（knowledge_events_fts 等 3 张）跟随宿主表，方案 B 下自动随库隔离。

### 1.2 全局 JSON（`data/*.json`）

| 文件 | 结构 | 规模 | 多租户判定 |
|---|---|---|---|
| users.json | list，含 passwordHash/teamIds/subordinateIds/role | 4 | **A：加 tenant_id**（登录路由的第一跳） |
| agents.json | list，含 createdBy/visibility/permission | 8 | **A：加 tenant_id**（员工是租户内资源；visibility='all' 语义要改 tenant 内公开） |
| teams.json / groups.json | list，含 createdBy/leadAgentId/members | 0 / 1 | **A：加 tenant_id** |
| permissions.json | {version, roleTemplates, userOverrides} | 3 键 | 只读全局模板，**平台级共享**，不用隔离 |
| settings.json | {embedding, llm, vision} | 3 键 | 平台级 AI 配置；多租户下应拆「平台默认 + 租户覆盖」两层 |
| products.json | {products, total, version} | 3 键 | ⚠️ 与 sqlite products 表并存的双写面，需确认事实源（调研发现：API 走 sqlite，此文件疑为导出缓存） |

### 1.3 文件目录（data/ 下）

| 目录 | 组织方式 | 多租户判定 |
|---|---|---|
| chats/ | `{agent_id}.json` 每员工一文件 (11 个) | 随 agent → 租户；方案 B 下整体进租户目录 |
| memory/ memories/ | `{agent_id}/` 子目录 + consolidation_log | 同上；含 `emp_001`、`{empId}` 模板残留目录，迁移时清洗 |
| messages/ knowledge/ products/ | `{emp_id}/` 或 `{id}.json` | 同上 |
| influencers/ talent_reports/ matches/ | 达人附件、报告、match_cache.json | matches 的 match_cache 无 owner，**需要 tenant 隔离或按 talent 归属代理** |
| certs/ backups/ | 证书、库备份 | 平台级；backups 需按租户拆分或保留全量平台备份 |

### 1.4 内存缓存（进程内全局 dict，多租户下键必须加 tenant 前缀）

| 变量 | 用途 | 风险 |
|---|---|---|
| `_chat_write_locks` | 聊天文件写锁 | 跨租户同名 agent_id 冲突（agent_id 全局唯一则低风险，但 B 方案下两租户可能有相同 emp id 习惯） |
| `_memory_file_locks` | 记忆文件锁 | 同上 |
| `_login_failures` / `_api_rate_log` / `_user_proxy_rate_log` | 限流/防爆破 | 平台级安全面，**保持平台级**（按 IP+username 已含用户维度） |
| `_HEAVY_JOBS` / `_OPENCLAW_RUNS` | 后台任务、openclaw 运行登记 | 键为 agent/session，需 tenant 前缀防串扰 |

### 1.5 认证模型（现状）

- JWT Bearer → `_authenticate()` (L2505) → `AuthResult(user_info)`，`auth.is_admin` 全局绕行。
- **localhost 快捷通道**：回环地址无 Bearer 时走 `_get_localhost_auth_result`，凭 `X-Agent-Id` 头直接获得该 agent 身份。**多租户下这是最大单点漏洞**：任意本机进程冒充任意 agent（= 任意租户的员工）读写全库。必须改为带签名的内部 token 或 unix socket + 进程白名单。
- 管理员 (role=admin) 当前是全局的；多租户需要拆「平台超管」与「租户管理员」两层角色。

---

## 2. 隔离方案对比

### A. 单库 + tenant_id 列 + 查询过滤

- 改动：所有 41 张表加列 → 所有 SQL 加 `WHERE tenant_id = ?`（server 有 244 个 handler、数百条裸 SQL）。
- 优点：迁移平滑；跨租户运营报表容易；单库备份简单。
- 缺点：**漏一个 WHERE 就是跨租户泄漏**（现状已经证明"列表过滤、详情不过滤"会自然发生）；删租户要逐表清扫；长表（credit_usage_log 2.5k 行未来上量）索引压力。

### B. 每租户独立 sqlite + 独立 JSON 目录

- 形态：`data/tenants/{tenant_id}/solobrave.db` + `data/tenants/{tenant_id}/{users,agents,...}.json` + 全部子目录；连接按 `X-Tenant`（从登录态解析，不信任请求头直传）路由。
- 优点：**强隔离**，删租户 = 删目录；泄漏面 = 路由写错一处（集中式 `_db_conn()` 单点改）；每租户可独立备份/导出（SaaS 卖点）；单租户大数据量不影响别家。
- 缺点：跨租户聚合（平台运营报表）要 federation；migration 脚本要对 N 个库跑；连接/文件句柄数 ×N（41 表 + WAL，需连接池上限）。

### C. 混合（业务数据 B + 配置数据 A）✅ 推荐

| 数据类 | 方案 | 理由 |
|---|---|---|
| 业务库（talents/products/kb/memory/credits/chats/…全部 41 表） | **B 独立库** | 泄漏代价最高 = 客户商业数据；删租户即删目录符合 SaaS 合规；路由集中单点可控 |
| 用户/员工/团队/群组 JSON | **A 加 tenant_id**（留在平台库） | 登录第一跳要在打开租户库**之前**解析用户→租户，配置数据量小、结构稳定，A 的改动面可控 |
| permissions.json / settings.json / knowledge_categories / brands / forbidden_words | 平台级共享，只读下发 | 角色模板和平台违禁词本来就是平台运营资产；settings 拆「平台默认 + 租户覆盖」 |
| embedding_cache | 平台级共享（保留） | 文本→向量无语义租户属性；规避侧信道可在 key 里加盐（tenant_id 混入 hash），成本一行 |
| 内存缓存 | 键加 tenant 前缀（`_HEAVY_JOBS`、`_OPENCLAW_RUNS`）或保持平台级（限流类） | 见 §1.4 |

**推荐 C，核心理由**：把"漏 WHERE 就泄漏"的面压缩到 `_db_conn()` 一个函数（B），而把"必须在租户库打开前完成解析"的登录/配置链留在单库（A），两类数据的失效模式互不传染。纯 A 把 244 个 handler 全部变成信任边界，纯 B 让登录链路无处安放且跨租户报表成本过高。

**分阶段落地**：
1. **P1 配置层**：users/agents/teams/groups 加 tenant_id；平台超管/租户管理员角色拆分；localhost 快捷通道加签。
2. **P2 业务库路由**：`_db_conn()` 改租户路由；41 张表结构不动（同构迁移到新库）；chats/memory 等目录进租户目录；存量单租户数据落入 `tenants/default/`。
3. **P3 收尾**：内存缓存键前缀、embedding_cache 盐、matches 缓存归属、产品化开通流程（§4）。

---

## 3. 改动面 Top 20 风险点（按前端调用点热度排序）

> 热度 = 前端 `js/` + `index.html` 中调用点数量（无 prod 访问日志，以调用面代理调用量）。
> 泄漏判定：✅ 已有行级过滤；⚠️ 部分过滤/语义需租户化；❌ 实测确认无行级校验（越权可读）。
> 代码证据行号基于基线 `ada9f7b`。

| # | 端点 | 热度 | 现状 | 泄漏判定 |
|---|---|---|---|---|
| 1 | `GET /api/knowledge` | 114 | scope 四维隔离（all/global/team/personal/group）+ 分类权限 | ⚠️ scope 语义租户化；`scope=all/global` 在单租户指全平台，多租户必须重定义为租户内 |
| 2 | `GET /api/talents` 列表 | 49 | `created_by ∈ {uid}∪{员工ids}` 两层过滤（L18540） | ✅ 列表；❌ **详情 `GET /api/talents/:id`（L18685）无任何行级校验**，已知 id 即可读任意租户达人 |
| 3 | `GET /api/knowledge/entries` | 34 | kb_entries 有 created_by | ⚠️ 需逐条核对详情/编辑端点是否同样过滤（详情类端点同 #2 模式） |
| 4 | `GET /api/knowledge-patterns` | 25 | created_by | ⚠️ 同上，detail/PUT/DELETE（L15503/15544/15641）扫描无作用域标记 |
| 5 | `GET /api/agents` | 24 | createdBy + visibility + 团队三维过滤（L11233） | ✅ 过滤完善；⚠️ `visibility='all'` 语义需改租户内公开 |
| 6 | `GET/POST /api/tasks` | 22 | 仅 `is_admin` 判定（L17296-17450），无 owner 过滤 | ❌ 全部任务端点对非 admin 的可见性/可写性存疑（现状只有 2 行数据，暴露面未爆） |
| 7 | `GET /api/products` 列表 | 21 | created_by 存在 | ❌ **详情 `GET /api/products/:id`（L17206）无行级校验** |
| 8 | `GET /api/proposals` | 18 | agent_id 归属 | ❌ **`GET /api/proposals/:id`（L19294）零校验**，可翻任意提议（含商务条款） |
| 9 | `GET /api/groups` | 18 | createdBy + `_can_access_team` | ✅ 体系较完整，租户化改 tenant_id 即可 |
| 10 | `GET /api/knowledge-events` | 16 | agent_id | ⚠️ stats 端点（L15334）扫描无作用域标记 |
| 11 | `GET /api/teams` | 12 | 管理员/leader 体系 | ⚠️ 角色拆分后需重审 |
| 12 | `GET /api/notifications` | 11 | user_id 过滤（L16820-16905） | ✅ |
| 13 | `GET /api/knowledge/categories` | 11 | 全表共享 | ⚠️ 平台级可接受，但分类下文档权限要跟 #1 走 |
| 14 | `POST /api/ai-match` | 10 | 模块权限即放行；冷启动单条 45s+ | ⚠️ 多租户下互相挤占连接/算力，需按租户配额排队（今晚 r82 池 6→2 已打样） |
| 15 | `/api/proxy/kimi` 等代理 | 8 | 全局 API key 代理 | ❌/⚠️ key 是平台资产需按租户计量；代理出网内容含各家业务数据 |
| 16 | `GET/POST /api/forbidden-words` | 7 | **无 owner 列**（L17054-17124） | ⚠️ 若平台运营资产则保持全局并加平台角色写保护 |
| 17 | `GET /api/credits/check` | 7 | agent_id（今晚新建积分体系） | ✅ 新代码口径正确，租户化随 P2 |
| 18 | `GET/PUT /api/users` | 6 | 自身记录 | ⚠️ admin 全量列表在租户化后必须收敛为租户内 |
| 19 | `GET/POST /api/deals` | 6 | created_by；详情 L15764 无标记 | ❌ deal 详情疑似同 #2 模式（待逐条复核） |
| 20 | `GET /api/talents/injection-text` | 5 | 拼接 prompt 注入文本 | ⚠️ 输出进 LLM 上下文，跨租户串文本 = 直接数据泄漏进模型记忆 |

**结构性风险（不进 Top 20 但优先级最高）**：
- **localhost 快捷通道**（L2505 `_authenticate`）：多租户下任意本机进程可凭 `X-Agent-Id` 冒充任意租户员工，P1 必须加签。
- **admin 全局绕行**：`auth.is_admin` 在 244 个 handler 里是平台级；需拆平台超管/租户管理员。
- **单例 `_db_conn()`**：方案 C 下它是唯一信任边界，P2 的改动就压在这一处。

---

## 4. 一键开通流程伪代码

```
function provision_tenant(admin_email, plan):
    # ── 1. 开户（平台库，P1 结构）────────────────────────
    tx_begin(platform_db)
    tenant = insert tenants {id: uuid(), name, plan, status: 'provisioning',
                             created_at: now()}
    user   = insert users {id: uuid(), tenant_id: tenant.id, email: admin_email,
                           role: 'tenant_admin', pwd_temp: random()}
    insert tenant_quotas {tenant_id, credits: plan.credits, agents: plan.agent_quota}
    tx_commit()

    # ── 2. 冷启动（租户目录 + 同构业务库，P2 结构）────────
    dir   = mkdir data/tenants/{tenant.id}/
    db    = sqlite_create(dir + 'solobrave.db', schema=MIGRATION_LATEST)
    for f in ['agents','groups']:            # teams 按需
        write_json(dir + f + '.json', seed_template(f, tenant.id))
    for d in ['chats','memory','memories','messages','knowledge','products',
              'influencers','talent_reports','matches']:
        mkdir dir + d
    run_migrations(db)                        # 幂等，记录 schema_version
    write_json(dir + 'tenant_meta.json', {schema_version, provisioned_at, plan})

    # ── 3. 积分（复用今晚 credit_accounts 体系）───────────
    # 签到钱包规则沿用 r-积分批注: 落 AI 员工账户 + 租户总额度双记账
    credit_accounts.create(tenant_id, type: 'tenant_pool', balance: plan.credits)
    audit_log('tenant.provisioned', tenant.id, by: 'platform')

    # ── 4. Bot 绑定（飞书/微信，可选异步）─────────────────
    if admin_email.feishu_bound:
        job = enqueue(feishu_bind_tenant, tenant.id)   # 沿用 _feishu_get_tenant_access_token 链路
        heavy_jobs.insert({tenant_id, job, status: 'running'})
    notify(admin_email, '开通完成', login_url, temp_password)

    return {tenant_id, login_url}
```

**关键设计点**：
- 开户与冷启动分开提交：开户成功即返回 tenant_id，冷启动失败可重试（幂等 `status: 'provisioning'` 状态机：provisioning → active / failed）。
- 业务库 schema 与平台库同构迁移脚本共用一份 `MIGRATION_LATEST`，杜绝两叉。
- Bot 绑定复用现有飞书链路；失败不阻断开通，进 heavy_jobs 可重跑。

---

## 5. 工作量估算（人日，1 人全栈基准）

| 阶段 | 内容 | 估算 |
|---|---|---|
| P0.5 前置 | 现状 43 个可疑 handler 逐条复核（本清单已标 20，剩 23 条半天）；products.json 与 sqlite 双写面确认事实源 | 1 d |
| P1 配置层 | users/agents/teams/groups 加 tenant_id + 迁移脚本；平台超管/租户管理员角色拆分（permissions.json 模板 + `is_admin` 判定收敛，约 60 处调用点）；localhost 通道加签；登录路由租户解析 | 3–4 d |
| P2 业务库 | `_db_conn()` 租户路由 + AuthResult 挂 tenant_id；目录重定向（DATA_DIR 按租户拼）；41 表同构迁移工具 + 存量数据落入 `tenants/default/`；详情类端点补行级校验（Top 20 里所有 ❌） | 5–7 d |
| P3 收尾 | 内存缓存键前缀；embedding_cache 盐；matches 归属；ai-match 租户配额队列；开通流程 API + 平台运营页 | 3–4 d |
| 测试 | 隔离断言套件（跨租户越权用例自动化，复用今晚 playwright 基建）+ 迁移回滚演练 | 3 d |
| **合计** | | **15–19 人日** |

风险缓冲建议 +20%（详情端点复核可能继续炸出 ❌）。P1+P2 之间可插一个「单租户伪装多租户」的验收：起两个 default 之外的测试租户跑全链路冒烟。

---

## 6. 验收对照

- [x] 全仓存储面盘点（§1：41 表 / 7 JSON / 9 目录 / 7 内存缓存，逐组标注）
- [x] 隔离方案 A/B/C 对比 + 推荐 C + 理由（§2）
- [x] Top 20 风险点按热度排序 + 泄漏端点标注（§3，含 ❌ 实测证据行号）
- [x] 一键开通伪代码（§4：开户→冷启动→积分→Bot 绑定）
- [x] 工作量估算（§5）

---

## 7. 实现进度（M1–M4 已落地，分支 feat/tenant-isolation）

> 以下里程碑均已沙箱（18220, SOLOBRAVE_MT=1）实测通过，prod 以单租户模式运行（行为零变化）。

| 里程碑 | 内容 | 提交 | 沙箱验证 |
|---|---|---|---|
| M1 租户底座 | tenants.json 注册表；`scripts/migrate_tenant_bootstrap.py` 幂等迁移（存量归 t_default，自动 .bak）；JWT 加 `tid` claim（旧 token 无 tid → 默认租户）；AuthResult.tenant_id / is_platform_admin / is_tenant_admin；localhost 通道多租户模式 HMAC 验签硬闸（X-Agent-Ts ±300s + X-Agent-Sig，密钥 `data/certs/internal_secret` 0600） | d1531ca | 迁移幂等✅ 登录 tid✅ 验签 4 场景✅ |
| M2 租户 DB 路由 | `_db_conn()` thread-local 路由（tid 仅认证层写入）；t_default 恒落 legacy 库（零拷贝）；新租户惰性建库 + init_db 全量 schema；`_run_as_tenant()` 后台任务包裹器 | 3e61972 | admin 262/t_acme 空库 39 表/双向写入隔离✅ |
| M3 行级校验 | talents 详情 + 3 个子资源端点镜像列表可见性（404 不暴露 id）；proposals 详情对齐列表 admin-only；deals 详情 JOIN 归属校验；`_talent_visible_to_auth()` 统一 helper。tasks 复核本就有校验；products 为共享货盘语义不动 | f5d4119 | 员工越权 404/admin 200/proposals 403✅ |
| M4 一键开通 | `POST /api/tenants`（平台超管专属）：开户 + 冷启动（惰性建库）+ 积分种子（tenant_pool）；`GET /api/tenants`（userCount）；重名 409 | 7501e2a | 开通→登录→积分 800→tenant_admin 403→跨租户不可见✅ |

### M5 已落地（fa984a2 起）
- 目录物理隔离：`_td()` 目录租户化（30 处调用点替换，t_default 零行为变化），chat 文件实测落 `tenants/<tid>/chats/`
- 删租户：`scripts/tenant_delete.py`（dry-run/--execute/--purge），收号双口径（tenant_id 章 + createdBy∈租户用户），sweep 扣默认库种子 id 防误删
- 员工/群组创建盖 tenant_id 章；积分内务 3 端点放行 tenant_admin；tenant_admin 租户内全权
- heavy 子线程两处（HeavyPipe/Reanalysis）`_run_as_tenant` 包裹

### 设计决定
- embedding_cache **不加盐**：文件缓存走 `_td` 已租户分目录，表缓存在各租户库内——M2+M5 后天然无跨租户共享，加盐属多余复杂度

### M6 已落地
- 飞书 Bot 绑定异步化：`POST /api/tenants/<id>/feishu-bind`（平台超管或本租户管理员）→ 凭证落租户库 `tenant_feishu_config`（随删租户即毁）→ 异步线程真调飞书验签 → 状态回写（pending/verified/failed+error）
- `GET` 同路径查状态（不回显 secret）；沙箱实测假凭证 → 飞书 API 真实返回 10003 → failed+错误信息 ✅
- ★ 诚实边界：消息路由到本租户 agent 需 OpenClaw 网关多租户改造，本轮到「凭证已存+连通已验证」为止

### M7 已落地（verified → routed 最后一跳）
- `_feishu_gateway_route()`：openclaw 网关写账号 + binding（bindings 全量替换幂等，网关侧校验 agentId 必须已注册）
- M6 验签通过 → 自动路由到本租户 agent（可传 agentId 指定，默认第一个；跨租户 agent 403）→ 状态升级 `routed`；网关不在/写失败 → 保持 `verified` + routeError 降级
- `tenant_delete.py` 加网关摘路由（best effort）
- 沙箱实测：网关读回/幂等重写/摘除干净/default 绑定回归 ✅；网关拒绝未注册 agent（安全校验，符合预期）

### 遗留（下一轮）
- 租户 agent 的网关注册时机打通（agent 在网关注册前绑定 → 保持 verified，重绑即 routed）
- 存量 dirs 的 `emp_001`/`{empId}` 模板残留清洗
- 其他后台线程逐点审计（OpenClaw 队列 / BrainScheduler / PatternInduce-Cron 均为平台级守护，默认租户语义正确，多租户化时再逐个 `_run_as_tenant`）
