# 达人飞书机器人 · 技术方案全文 v1.0

> 2026-10-11 派单最终版 9 条 + 老大批注 3 硬伤修正 + 4 拍板项 + 2 收紧点，全部已吸收。
> 产品：达人零操作的 BYO 飞书机器人（内测）→ ISV 商店应用（正式售卖）。
> 执行顺序（老大定）：**隧道验证（P0 门禁）→ 表结构 DDL → 5 条消息时序 → 运营代配置 SOP → 15 条验收用例**。

---

## 第 1 章 隧道验证（P0，不过则全停）

**问题**：回调域名 `sczlgyl.com/daren-bot/callback/` 走 Lolia FRP 隧道，但现有隧道映射的是 SoloBrave 8080。新产品是独立进程 + 独立库，必须落到自己的端口（建议 **8092**）。

**执行方第一步（先核实再动工）**：

1. 登录 Lolia 管理端 / 看 frpc 配置，确认支持哪种：
   - **方案 A（首选）**：路径分流——隧道入口按 `/daren-bot/*` 前缀转发 8092，其余继续 8080。frp 本身不支持路径分流，需 Lolia 侧 nginx/Caddy 反代，或 frpc 起第二条 http 隧道共用一个 vhost。
   - **方案 B**：加一条独立隧道（如 `daren.sczlgyl.com` 或新端口），frpc 加一条 proxy 规则 → 127.0.0.1:8092。
2. 落一条验证用规则后，本机起 `python3 -m http.server 8092`，外网执行：
   ```
   curl -X POST https://sczlgyl.com/daren-bot/callback/test -d '{}'
   ```
   通 = 门禁过，继续往下写；不通 = 停在第 1 章，别写后面的（写了也白写）。
3. 同时确认：sczlgyl.com 证书 11-12 到期，续费提醒已挂；若证书续的是裸域，新子域方案 B 要一并续 SAN。

---

## 第 2 章 总体架构

```
达人飞书消息 → 事件回调(im.message.receive_v1, AES 解密) → FRP → daren-bot 服务(独立进程:8092)
                                    │
                    ┌───────────────┴──────────────── 免费路由（先判，零模型成本）────────┐
                    │ 图片消息 → 立刻下载落盘 → vision 路由                              │
                    │ 文本含 URL → 链接路由                                              │
                    │ 纯文本匹配抖音号格式(^\d{5,25}$) → 周抓路由                        │
                    │ 其余纯文本 → glm-4-flash 分类(走 OpenClaw 网关便宜 profile)      │
                    │              ├ 业务问答 → 主链路                                   │
                    │              ├ 无关 → 固定软话术拒答（零成本）                     │
                    │              └ 分类器故障 → fail-open 放行业务问答,                │
                    │                 但禁触发浏览器/vision（系统提示词兜底）            │
                    ▼
              ① 抖音号路由: 周限频检查(ISO 周计数) → 超限话术 / 预扣 100 分
                 → 异步任务 → Kimi 浏览器 → 巨量团长系统抓取 → 建档
              ② 截图路由: vision 识别 → 缺字段明说 → 预扣 10 分
              ③ 链接路由: URL 归一化 → 缓存查询
                 ├ 命中 → 预扣 5 分 → 直接用缓存分析
                 └ 未命中 → 预扣 20 分 → 浏览器抓取 → 写共享缓存(TTL 7-14d)
                    ▼
              AI 分析（V3 匹配：理性/冲动品类差异化权重）
              产出：能带/慎带/别带 + 理由 + 建议拍法
                    ▼
        ┌───────────┴───────────┐
        ▼                       ▼
  飞书卡片推送              多维表格写入（1 个 Bitable App 内 3 张表）
  （用该租户自己的凭证）    （建到达人云空间，数据归达人）
        │                       │
  积分流水落账              结果经 /internal/kb/ingest 单向回流 SoloBrave 知识库
  （失败自动返还）          （共享 secret，内测走 localhost，只写不联动）
```

**架构硬约束**：
- 浏览器、vision 两个最贵动作**只由路由/分类命中触发**，模型无权自行调用（能力白名单）。
- 意图分类走 OpenClaw 网关配便宜 profile（glm-4-flash 类），**不直连 provider**（守架构铁律）。
- 每个达人一套飞书 app 凭证，**加密存储、用各自凭证回消息，严禁混用**。

---

## 第 3 章 表结构 DDL（独立库 `daren_bot.db`）

拍板：**独立 SQLite 库**（产品独立、未来迁云、边界清晰）。服务启动自动建表。

```sql
-- 3.1 租户表：一租户 = 一达人 = 一抖音号（MVP 收紧，换号找运营）
CREATE TABLE IF NOT EXISTS bot_tenants (
  tenant_key      TEXT PRIMARY KEY,             -- 自建命名: daren_xxx，也用于回调路径
  app_id          TEXT NOT NULL,
  app_secret_enc  TEXT NOT NULL,                -- AES 加密，系统主密钥
  encrypt_key     TEXT NOT NULL,                -- 飞书事件订阅 Encrypt Key
  bind_douyin_id  TEXT NOT NULL DEFAULT '',     -- MVP 一租户一号
  bitable_app_token TEXT DEFAULT '',            -- 1 个 Bitable App
  bitable_table_ids TEXT DEFAULT '',            -- JSON: {"profile":"tbl_x","products":"tbl_y","advice":"tbl_z"}
  status          TEXT NOT NULL DEFAULT 'active',
  created_at      INTEGER NOT NULL,
  updated_at      INTEGER NOT NULL
);

-- 3.2 任务队列（异步执行，幂等: 同租户同目标 pending/collecting 不重复入队）
CREATE TABLE IF NOT EXISTS bot_collect_tasks (
  id           TEXT PRIMARY KEY,
  tenant_key   TEXT NOT NULL,
  type         TEXT NOT NULL,                   -- douyin | product
  target       TEXT NOT NULL,                   -- 抖音号 或 归一化商品 URL
  status       TEXT NOT NULL DEFAULT 'pending', -- pending/collecting/done/failed
  cache_hit    INTEGER NOT NULL DEFAULT 0,
  credits_cost INTEGER NOT NULL DEFAULT 0,
  error        TEXT DEFAULT '',
  created_at   INTEGER NOT NULL,
  done_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tasks_tenant ON bot_collect_tasks(tenant_key, status);

-- 3.3 商品共享缓存（全平台达人共用，TTL 7-14 天）
CREATE TABLE IF NOT EXISTS bot_product_cache (
  product_key TEXT PRIMARY KEY,                 -- URL 归一化(去追踪参数/短链解析后)
  name        TEXT DEFAULT '',
  price       TEXT DEFAULT '',
  commission  TEXT DEFAULT '',
  stats_json  TEXT DEFAULT '{}',
  source_url  TEXT DEFAULT '',
  fetched_at  INTEGER NOT NULL
);

-- 3.4 周抓计数：按 ISO 周数存，查时自然分周，无定时重置任务
CREATE TABLE IF NOT EXISTS bot_week_counters (
  tenant_key TEXT NOT NULL,
  douyin_id  TEXT NOT NULL,
  week_no    INTEGER NOT NULL,                  -- ISO 8601 周数, e.g. 202641
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_key, douyin_id, week_no)
);

-- 3.5 积分账户
CREATE TABLE IF NOT EXISTS bot_credits_accounts (
  tenant_key TEXT PRIMARY KEY,
  balance    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

-- 3.6 积分流水：预留 recharge_order_id，转付费不动表结构
CREATE TABLE IF NOT EXISTS bot_credits_ledger (
  id                TEXT PRIMARY KEY,
  tenant_key        TEXT NOT NULL,
  action            TEXT NOT NULL,              -- collect_douyin/collect_product/collect_product_cached/vision/qa/grant/refund
  cost              INTEGER NOT NULL,           -- 正扣负返
  balance_after     INTEGER NOT NULL,
  cache_hit         INTEGER NOT NULL DEFAULT 0,
  ref_task_id       TEXT DEFAULT '',
  recharge_order_id TEXT DEFAULT '',            -- 预留
  created_at        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_tenant ON bot_credits_ledger(tenant_key, created_at);
```

**图片落盘**（不入库，文件系统）：`data/daren-bot/images/<tenant_key>/<msg_id>_<file_key>.jpg`，收到消息**立刻下载**（im:resource 资源 key 约 3 天过期，异步队列可能几十分钟后才处理，必须收消息即落盘）。

---

## 第 4 章 消息时序（5 条）

### 4.1 抖音号采集（type=douyin）

```
达人: "31693707031"
  │
  ▼
daren-bot: 免费路由命中(纯文本+^\d{5,25}$)
  ├─ 未绑定? → bind_douyin_id=该号(首次发号=绑定+首抓) 
  ├─ 已绑别的号? → "本助手已绑定你的抖音号 xxx，查别人的号内测期请联系运营"
  ├─ SELECT count FROM bot_week_counters WHERE week_no=今年第几周 → ≥1?
  │     └─ 是 → 回复"本周已分析过你的账号，下周更新；加急联系运营"（END）
  ├─ 余额 ≥100? 否 → 余额不足话术（END）
  ├─ 预扣 100 分（流水 action=collect_douyin, 同目标幂等检查）
  ├─ 入队 bot_collect_tasks → 立即回卡片"已开始采集，正常5-8分钟"
  ▼
worker 线程: Kimi 浏览器 → 巨量团长 → 抓取 → 建档 → talent 画像写缓存
  ├─ 成功 → 更新任务 done → V3 分析 → 卡片推送完整报告 + 写多维表格「账号档案」(按日期追加一行) 
  │        → /internal/kb/ingest 回流 SoloBrave KB
  └─ 失败 → 任务 failed → 自动返还 100 分(流水 action=refund) → 卡片告知原因+"说重新采集可重试"
```

### 4.2 商品截图（type=image）

```
达人: [图片消息]
  ▼
daren-bot: 免费路由命中(图片类型)
  ├─ 立即用该租户 tenant_access_token 下载图片落盘（不等队列）
  ├─ 余额 ≥10? → 预扣 → 回卡片"收到截图，识别中"
  ▼
vision 识别 → 结构化(商品名/价格/佣金/卖点/类目)
  ├─ 关键字段缺失 → 卡片明说"图里没看到佣金/销量，补一张更完整的"（已扣 10 分不返还，属识别成功）
  ▼
V3 分析（用该达人上周画像缓存）→ 卡片"能带/慎带/别带+理由+建议拍法"
  → 写多维表格「商品推荐记录」→ KB 回流
```

### 4.3 商品链接（type=product）

```
达人: "https://v.douyin.com/xxxx 帮我看看"
  ▼
daren-bot: 免费路由命中(含 URL) → 短链/口令解析 → URL 归一化(去 utm 等参数)
  ├─ bot_product_cache 命中且未过期?
  │   ├─ 是 → 预扣 5 分(命中价) → 直接用缓存 stats 分析（全链路秒回）
  │   └─ 否 → 余额 ≥20? → 预扣 20 → 入队浏览器抓取 → 写缓存(TTL 7-14d) → 分析
  ▼
卡片推送结论 → 写「商品推荐记录」(cache_hit 标记) → KB 回流
```

### 4.4 无关问题（零成本拒答）

```
达人: "帮我写个周报"
  ▼
免费路由未命中 → glm-4-flash 分类 → "无关"
  ▼
固定软话术（不扣积分、不进主链路、无 LLM 调用）:
"我是你的带货选品助手，账号分析、选品建议、商品判断都可以找我，其他问题帮不上忙哦～"
```

### 4.5 余额不足

```
任一扣费动作前预检余额 < 所需分值
  ▼
"这个动作需要 N 积分，你的余额不够啦。联系运营老师补充额度（内测期免费发放）"
（不产生任务、不入队）
```

---

## 第 5 章 运营代配置 SOP（标准工时 10 分钟/达人）

> 权限清单近年有调整，**执行时以飞书开放平台实时权限列表为准**，SOP 截图环节逐一核对。

**前置**：拿到达人飞书团队管理员权限（远程屏幕共享代操作，或达人按授权书临时授权）。

1. **创建企业自建应用**：open.feishu.cn → 创建企业自建应用。命名规范：`<达人昵称>的选品助手`，头像用达人抖音头像。
2. **开启机器人能力**：应用能力 → 添加「机器人」。
3. **配置事件订阅**：
   - 订阅方式：使用长连接（内测期可选）或 webhook；webhook 回调 URL：
     `https://sczlgyl.com/daren-bot/callback/<tenant_key>`（tenant_key 预先在系统里注册好再填）
   - 记录 **Encrypt Key** 和 **Verification Token**
   - 添加事件：`im.message.receive_v1`（接收消息）
4. **勾权限**（以平台实时列表为准）：
   - `im:message` / `im:message.receive_v1`：收发消息
   - `im:resource`：读取消息资源（**收图必须**，图片立刻下载落盘）
   - `bitable:app`：查看/评论/编辑多维表格（建 3 张表）
   - 通讯录只读（按需）
5. **创建版本 → 发布**：达人自己是管理员，秒过审。
6. **凭证入库**：app_id / app_secret / encrypt_key 写入 `bot_tenants`，secret AES 加密落盘；**严禁明文出现在日志**。
7. **自动初始化**：系统检测到新租户 → 自动创建 Bitable App（命名「我的带货数据」）→ 建 3 张数据表 → app_token + table_id 回写租户表 → 发验证卡片。
8. **回环验证**：代达人发一条测试消息（如自家抖音号）→ 确认卡片回复 + 表格落行。
9. **交付**：把机器人名片推给达人，话术："这是你的选品助手，直接发消息给它就行——发你的抖音号查账号，发商品截图或链接问能不能带。"

---

## 第 6 章 多维表格设计（1 个 Bitable App，3 张数据表）

**1 个 app_token + 3 个 table_id**，存 bot_tenants。结论字段用单选带色。每表 ≤10 字段，不做重看板。

**表 A · 我的账号档案**（周抓按日期追加，看趋势）

| 字段 | 类型 |
|---|---|
| 日期 | 日期 |
| 粉丝数 | 数字 |
| 近30天GMV | 数字 |
| 主推类目 | 文本 |
| 价格带分布 | 文本 |
| 视频占比% | 数字 |
| 单视频结算 | 数字 |
| 综合评级 | 单选（S/A/B/C 带色） |
| 备注 | 文本 |

**表 B · 商品推荐记录**

| 字段 | 类型 |
|---|---|
| 分析日期 | 日期 |
| 商品名 | 文本 |
| 商品链接 | 超链接 |
| 佣金率 | 文本 |
| 匹配分 | 数字 |
| **结论** | **单选（绿=能带/黄=慎带/红=别带）** |
| 理由 | 文本 |
| 建议拍法 | 文本 |
| 类目 | 文本 |

**表 C · AI 建议日志**

| 字段 | 类型 |
|---|---|
| 日期 | 日期 |
| 场景 | 单选（选品/账号/内容/其他） |
| 建议内容 | 文本 |
| 反馈 | 单选（待反馈/已采纳/出单了，达人手改） |

「出单了」反馈数据定期导出，喂 V3 算法优化（金矿字段）。

---

## 第 7 章 限频、缓存、积分规则汇总

| 规则 | 实现 |
|---|---|
| 抖音号周抓 | bot_week_counters 按 (tenant_key, douyin_id, ISO week_no) 计数；≥1 拒；无定时任务 |
| MVP 一租户一号 | bind_douyin_id；换号/查他人号 → 找运营 |
| 商品缓存 | URL 归一化 key；TTL 7-14 天；全平台共享 |
| 任务幂等 | 同租户同目标存在 pending/collecting → 返回原任务进度 |
| 失败返还 | 任务 failed → 按 credits_cost 反向流水 refund |
| 积分扣费 | 入队前预扣：周抓 100 / 链接 20 / 命中 5 / 截图 10 / 问答 1-2 / 无关 0 |
| 初始额度 | 500 分（≈5 次周抓 + 日常问答余量），用完运营手动补（同 SoloBrave 签到发放范式） |
| 余额不足 | 动作前预检，给运营联系话术，不产生任务 |

**KB 回流**：新服务调 SoloBrave 内部 HTTP 接口 `POST /internal/kb/ingest`（共享 secret，内测走 localhost），只写不联动。SoloBrave 侧需新增该中继端点（守铁律 13：AI 专属端点不加宽鉴权，新建中继）。

---

## 第 8 章 验收用例（15 条）

1. 发抖音号 → 绑定+首抓 → 5-8 分钟内收到完整报告卡片
2. 同号本周再发 → 周限频话术，无任务产生，不扣分
3. 跨自然周再发 → ISO 周计数自动放行
4. 已绑定后发别人抖音号 → "已绑定你的号 xxx"话术
5. 发商品链接（未命中缓存）→ 扣 20 → 出结论 → 缓存落库
6. 另一达人发同链接 → 命中缓存 → 扣 5 → 秒回
7. 发商品截图（字段齐全）→ 扣 10 → 结论卡片 + 表格落行
8. 发残缺截图 → 明说缺哪些字段，不编造
9. 发无关问题（如"写周报"）→ 软话术拒答；**断言**：无 OpenClaw 主链路调用、无浏览器、无 vision、无积分变动
10. 余额 5 分时发抖音号 → 不足话术，不产生任务
11. 抓取任务失败 → 积分自动返还，流水 action=refund 可查
12. 同目标重复提交（pending 中）→ 幂等返回原任务，不重复扣费
13. 凭证混用测试：A 租户消息用 B 租户凭证回 → 系统拦截/告警（验收时模拟配置错误）
14. 多维表格：3 张表自动创建、结论单选带色、周抓数据按日期追加
15. KB 回流：完成一次分析后 SoloBrave 知识库可检索到该条目

**P0 前置用例（第 1 章门禁）**：`curl -X POST https://sczlgyl.com/daren-bot/callback/test` 到达 8092 服务。

---

## 第 9 章 两阶段路线与风险

| 阶段 | 形态 | 要点 |
|---|---|---|
| 内测 | BYO + FRP + 独立库 | 3-5 种子达人，代配置 SOP，积分手动发放 |
| 正式 | ISV 商店应用 | 企业资质 + 飞书审核 1-2 周；达人 30 秒安装；tenant_key 分租户 |

**风险**：① 巨量风控（周抓已限频）② FRP 单点（内测可接受，正式迁云）③ 截图质量参差（缺字段明说）④ sczlgyl.com 证书 11-12 到期（续费提醒已挂）。
