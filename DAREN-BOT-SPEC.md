# 达人飞书机器人 · 技术方案全文 v1.1

> 2026-10-11 派单最终版 9 条 + 老大批注 + 19:42 批准改长连接模式，全部已吸收。
> 产品：达人零操作的 BYO 飞书机器人（内测，长连接模式）→ ISV 商店应用（正式，webhook）。
>
> **v1.1 变更**：①第 1 章 P0 从 FRP 隧道验证改为长连接连通验证（隧道实测已断：Mac 无 frpc、faipod 网关无映射，且 faipod 控制台管理人待确认）②DDL 去 encrypt_key/Verification Token，bot_tenants 主键改 app_id ③SOP 改「凭证入库→起 client 在线→后台保存长连接订阅」④handler 3 秒硬约束写进时序 ⑤三个收紧：纯数字先回确认卡片再扣分、废图不扣分、week_no 用 ISO 周年。
> **P0 排查结论**：OpenClaw 现有飞书渠道（貂蝉/Helen）走 WebSocket 长连接（已核实 ESTABLISHED 连接 101.132.183.92:443），不依赖 faipod 隧道，隧道断对现有业务零影响。

---

## 第 1 章 长连接连通验证（P0，不过则全停）

**结论前置**：frp/webhook 方案实测不可用（faipod 网关无映射、本机无客户端、控制台管理人待确认），**内测期改用飞书官方长连接模式**：自建应用用 lark-oapi SDK 的 ws.Client 主动外连飞书收事件，不需要公网回调 URL、不需要域名证书、NAT 后直接跑。正式版 ISV 阶段再做 webhook（届时 faipod/Lolia 隧道需修复，见第 9 章）。

**P0 验证步骤（10 分钟内）**：

1. 准备测试租户：任一自建应用（可复用运营自己的测试应用）的 app_id / app_secret 入库
2. 起 `lark-oapi` Python SDK 的 `ws.Client`，用该凭证连接
3. 飞书开放平台后台 → 事件订阅 → 切「使用长连接接收事件」→ 添加事件 `im.message.receive_v1` → **保存**（保存时 client 必须已在线，否则报错）
4. 给该测试应用机器人发一条消息 → 断言：10 秒内收到事件推送，消息内容/发送人 open_id 正确
5. 断开 client 重连一次 → 断言：重连后事件不丢（飞书侧短时有重推窗口）

通过 = 门禁过，进入开发；任一步失败 = 停在第 1 章排查（凭证/网络/SDK 版本三选一）。

**排查单结论（老大派）**：OpenClaw 现有飞书渠道已核实为长连接模式，隧道断对 SoloBrave 现有业务**无影响**，无需应急。

---

## 第 2 章 总体架构

```
达人飞书消息 → 长连接(ws.Client 主动外连飞书收 im.message.receive_v1, 一租户一条)
                                    → daren-bot 服务(独立进程:8092)   【内测不用 FRP/公网回调】
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
              ① 抖音号路由: 余额检查 → 回确认卡片("已发起采集, 5-8分钟出报告")
                 → 预扣 100 分 → 异步任务 → Kimi 浏览器 → 巨量团长系统抓取 → 建档
              ② 截图路由: vision 识别 → 缺字段明说 → 预扣 10 分（废图识别不出 → 不扣分, 返还）
              ③ 链接路由: 回确认卡片 → URL 归一化 → 缓存查询
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
- **handler 3 秒硬约束**：事件 handler 只做图片落盘 + 入队 + 快速返回，抓取/vision/分析全在 worker 异步执行（飞书事件重推机制要求快速 ACK）。
- **长连接按租户各自维持**：一租户一条 ws.Client，由 daren-bot 服务主动外连，断线 SDK 自动重连；不依赖任何公网回调地址。

---

## 第 3 章 表结构 DDL（独立库 `daren_bot.db`）

拍板：**独立 SQLite 库**（产品独立、未来迁云、边界清晰）。服务启动自动建表。

```sql
-- 3.1 租户表：一租户 = 一达人 = 一抖音号（MVP 收紧，换号找运营）
-- v1.1 改动: 主键改 app_id（长连接模式无回调路径，encrypt_key 不再使用，已删除）
CREATE TABLE IF NOT EXISTS bot_tenants (
  app_id          TEXT PRIMARY KEY,             -- 飞书自建应用 App ID, 全局唯一
  tenant_key      TEXT NOT NULL UNIQUE,         -- 自建命名: daren_xxx，业务标识
  app_secret_enc  TEXT NOT NULL,                -- AES 加密，系统主密钥
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
  ├─ 回确认卡片"已开始采集，正常5-8分钟出报告"（先回卡片再扣费）
  ├─ 预扣 100 分（流水 action=collect_douyin, 同目标幂等检查）
  ├─ 入队 bot_collect_tasks → handler 立即返回（3 秒硬约束，重活在 worker）
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
  ├─ 废图（识别不出任何有效商品内容）→ 返还 10 分(流水 action=refund) → 卡片"这张图我没认出商品，
  │     发商品链接或更清晰的截图我再看"（不扣费，END）
  ├─ 关键字段缺失 → 卡片明说"图里没看到佣金/销量，补一张更完整的"（已扣 10 分不返还，属识别成功）
  ▼
V3 分析（用该达人上周画像缓存）→ 卡片"能带/慎带/别带+理由+建议拍法"
  → 写多维表格「商品推荐记录」→ KB 回流
```

### 4.3 商品链接（type=product）

```
达人: "https://v.douyin.com/xxxx 帮我看看"
  ▼
daren-bot: 免费路由命中(含 URL) → 回确认卡片"收到链接，分析中" → 短链/口令解析 → URL 归一化(去 utm 等参数)
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
3. **勾权限**（以平台实时列表为准）：
   - `im:message` / `im:message.receive_v1`：收发消息
   - `im:resource`：读取消息资源（**收图必须**，图片立刻下载落盘）
   - `bitable:app`：查看/评论/编辑多维表格（建 3 张表）
   - 通讯录只读（按需）
4. **创建版本 → 发布**：达人自己是管理员，秒过审。
5. **凭证入库**：app_id / app_secret 写入 `bot_tenants`（主键 app_id），secret AES 加密落盘；**严禁明文出现在日志**。
6. **起长连接 client**：daren-bot 检测到新租户 → 用 app_id + app_secret 自动拉起 ws.Client（lark-oapi）→ 日志确认 `client online`。
7. **后台保存事件订阅**：开发者后台 → 事件与回调 → 事件订阅 → 订阅方式选**「使用长连接接收事件」**→ 添加 `im.message.receive_v1` → 保存。**保存时第 6 步的 client 必须已在线**，否则平台报错保存失败；保存成功即表示长连接链路接通。
8. **自动初始化**：系统检测到新租户订阅接通 → 自动创建 Bitable App（命名「我的带货数据」）→ 建 3 张数据表 → app_token + table_id 回写租户表 → 发验证卡片。
9. **回环验证**：代达人发一条测试消息（如自家抖音号）→ 确认卡片回复 + 表格落行。
10. **交付**：把机器人名片推给达人，话术："这是你的选品助手，直接发消息给它就行——发你的抖音号查账号，发商品截图或链接问能不能带。"

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

**P0 前置用例（第 1 章门禁）**：长连接连通——代达人发测试消息 **10 秒内** daren-bot 日志收到 `im.message.receive_v1` 事件；手动 kill client 进程后 **自动重连、重连后新消息不丢**。

---

## 第 9 章 两阶段路线与风险

| 阶段 | 形态 | 要点 |
|---|---|---|
| 内测 | BYO + 长连接 + 独立库 | 3-5 种子达人，代配置 SOP，积分手动发放；零公网依赖 |
| 正式 | ISV 商店应用 | 企业资质 + 飞书审核 1-2 周；达人 30 秒安装；tenant_key 分租户；届时再评估 webhook 回调 |

**风险**：① 巨量风控（周抓已限频）② 正式版需修复 faipod 隧道（控制台管理人待确认；sczlgyl.com 证书 11-12 到期，续费提醒已挂）③ 截图质量参差（缺字段明说，废图不扣分）④ 长连接依赖 daren-bot 进程在线（进程守护 + 断线重连，内测可接受）。
