# daren-bot — 达人飞书机器人（独立进程）

技术方案见仓库根目录 `DAREN-BOT-SPEC.md`（v1.1，内测长连接模式）。
本目录是服务实现。独立 SQLite 库 + 一租户一条飞书长连接 + 异步 worker。

## 环境

```bash
python3 -m venv .venv-daren          # 仓库根目录执行（已建好）
.venv-daren/bin/pip install -r daren_bot/requirements.txt
```

## 运行

```bash
.venv-daren/bin/python -m daren_bot.main init-db
.venv-daren/bin/python -m daren_bot.main add-tenant <tenant_key> <app_id> <app_secret> [douyin_id]
.venv-daren/bin/python -m daren_bot.main serve
```

- 健康检查：`http://127.0.0.1:8092/healthz`
- 数据：`data/daren-bot/`（daren_bot.db、master.key、images/）
- 主密钥 `master.key` 0600，首启自动生成；app_secret 经 AES-GCM 加密落库。

## P0 验证（spec 第 1/8 章）

1. `add-tenant` 入库 + `serve` 起进程（日志出现 `ws client starting`）
2. 飞书开放平台该应用 → 事件订阅 → 选「使用长连接接收事件」→ 添加
   `im.message.receive_v1` → 保存（**保存时 client 必须已在线**，否则平台报错）
3. 给机器人发测试消息 → **10 秒内**日志出现 `im.message.receive_v1` 即通过
4. kill 进程重拉 → 补发消息仍能收到 = 断线重连不丢

## 问答降级链（2026-10-21 重排）

飞书问答的模型调用走 `executors._qa_llm()`，按环依次尝试，**环 0 命中即返回**：

| 环 | 协议 | 端点 | 凭据 | 状态 |
|---|---|---|---|---|
| 0 | Anthropic | `api.minimax.cn/anthropic/v1/messages` | `.env` `MINIMAX_CP_API_KEY`（套餐 key） | ✅ 长期凭据，主用 |
| 1 | Anthropic | `agent.minimax.cn/mavis/api/v1/llm/v1/messages` | Code 订阅 OAuth accessToken | ✅ 套餐 key 失效时备份 |
| 2 | OpenAI | `api.minimaxi.com/v1/chat/completions` | `.env` `MINIMAX_CP_API_KEY` | ✅ 协议冗余 |
| 3 | OpenAI | `api.kimi.com/coding/v1/chat/completions` | OpenClaw key store | ❌ 429 月度上限 |
| 4/5 | OpenAI | `api.minimaxi.com/v1/chat/completions` | `.env` `MINIMAX_API_KEY` | ❌ 402 余额 0 |

> **环序由老大拍板：套餐优先**（2026-10-21）。排序依据是**凭据稳定性**，不只是「有没有额度」：
> 套餐 key 是长期凭据（不会 30 分钟一轮换、不依赖 Code 桌面端开着）→ 排环 0；
> Code OAuth token 是 30 分钟量级的短期凭据 → 排环 1 当备份；
> 环 2 是同一把套餐 key 走另一套协议（协议冗余，防某个端点单点故障）。
> **别再改回去**：把 429/402 的老链提到前面，等于每次问答白等两轮必然失败的 HTTP，
> 飞书侧体感是「卡十几秒才回一句没反应」。

### 三把 MiniMax key 的三本账（最容易搞混的一处）

| key | 前缀 | 端点 | 额度从哪扣 | 现状 |
|---|---|---|---|---|
| 套餐 key | `sk-cp-…` | `api.minimax.cn/anthropic/v1`（M Plan 官方接入 base） | **订阅套餐** | ✅ 唯一有额度 |
| Code OAuth token | `mmoat_…` | `agent.minimax.cn/mavis/api/v1/llm/v1` | **Code 客户端自持凭据** | ✅ 有额度，但 30 分钟一轮换 |
| 开放平台 key | `sk-api-…` | `api.minimaxi.com/v1` | **开放平台余额**（与上面两本账独立） | ❌ 余额 0 → 402 |

⚠️ 三者是**三本独立的账**。看到其中一把报 402/401，不代表另两把也没额度 ——
「充值？我买的是 code 啊」正是踩这个坑：老大买的套餐根本不走开放平台余额。

### 套餐 key 的能力矩阵（实测，别靠猜）

| 协议 | 端点 | 鉴权头 |
|---|---|---|
| Anthropic | `api.minimax.cn/anthropic/v1/messages` | `x-api-key` ✅ / `Authorization: Bearer` ✅ |
| Anthropic | `api.minimaxi.com/anthropic/v1/messages` | 同上 ✅ |
| Anthropic | `api.minimax.chat/anthropic/v1/messages` | 同上 ✅ |
| OpenAI | `api.minimaxi.com/v1/chat/completions` | `Authorization: Bearer` ✅ |

- **协议不能混用**：`agent.minimax.cn` 的 `/chat/completions` 返 503，只有 `/messages` 通
- 代码用 `ring['auth']` 显式声明鉴权头形态（`x-api-key` / `bearer`），不靠猜

### 实现要点

- **套餐 key 只进 `.env`**（600、gitignored），代码只读不算（AGENTS.md §7 规则 1）
- **Code OAuth token 每次调用实时读盘、绝不缓存**：token 是 30 分钟量级短期凭据，
  Code 桌面端自行续期并**重写 auth.json**，缓存下来的 token 下一次必 401
  （表现是「刚开始还能用、跑一会儿全灭」，极易误判成额度又没了）
- **剩余 < 60s 视为过期** → 跳过该环而不是让用户等一次 401
- **401 特判**：读盘到发请求之间刚好过期 → 重读凭据重试一次
- **推理标签剥离**：M3 走 OpenAI 协议时 content 开头是 `<think>We need answer…`，
  透传给飞书用户会看到一堆英文思考过程 → `_strip_think()` 必须剥
  （Anthropic 协议是 thinking/text 分块返回，按 `type=='text'` 过滤）
- **可观测**：命中哪一环会打 `[fallback] 命中环N protocol=… model=… base=…`。
  没有这行就看不出「套餐环失灵、静默降级到 kimi」这类故障
  （跳过警告只在该环被跳过时才打）

### vision 降级环（2026-10-21 重排）

vision 链曾**全灭**（kimi 429 月度上限 + 开放平台 402 余额 0），而「商家发商品图」
是招商减负方案的地基（**不依赖团长端浏览器登录态**）—— 链断了整条路就废了。

| 环 | 通道 | 凭据 | 状态（2026-10-21 实测） |
|---|---|---|---|
| 0 | `api.minimaxi.com/v1`（OpenAI 协议）`MiniMax-M3` | `.env` `MINIMAX_CP_API_KEY`（套餐 key） | ✅ **唯一有额度，3.8s 出结果** |
| 1 | kimi `moonshot-v1-8k-vision-preview` | OpenClaw key store | ❌ 401 |
| 2 | `api.minimaxi.com/v1` 开放平台 | `sk-api-…` | ❌ 401 |

实现要点：

- **环序按凭据稳定性排**（与问答降级链一致）：套餐 key 是长期凭据 → 排环 0
- `_vision_try()` 补了 `base_resp.status_code` 检查：**HTTP 200 不代表成功**，
  MiniMax 会在 body 里塞 1008（余额不足）——只看状态码会把它伪装成「没识别出商品」，
  把余额问题伪装成识别问题，排查方向直接跑偏
- **MIME 按 magic bytes 判**（`_img_mime`）：原来硬写 `data:image/jpeg`，商家传 PNG 就标错格式
- **全链失败时错误信息必须可读**：逐环列出失败原因（原来只留最后一个）
- 抽出的公共函数 `collect_product_by_url(url, timeout)`：链接采集链路（达人和商家共用），
  **前置检查登录态**——没登录时秒判报错，不再白等 60s `page.goto` 超时

实测（合成商品图）：

```
name='轻便折叠登山杖 铝合金伸缩款' price='¥59.9' commission_rate='20%'
sales='8600件' category='户外装备 / 登山杖'
```

喂一张**抖音首页**截图（不是商品图）时，模型返回全空字段并拒绝编造 → 判定为 no-name，
继续下一环。**这是正确行为**（诚实失败），不是 bug。

### 排障

```bash
# 看当前环序（不打印 key 明文）
.venv-daren/bin/python -c "
from daren_bot import executors as E
for i,r in enumerate(E._fallback_rings_daren('')):
    print(i, r['protocol'], r.get('auth'), r['model'], r['base'])
"
# 看命中了哪一环
grep '\[fallback\]' logs/daren-bot.launchd.log | tail
```

| 现象 | 根因 | 处置 |
|---|---|---|
| `套餐 API: .env 无 MINIMAX_CP_API_KEY` | `.env` 缺 key / 权限不对 | 检查 `.env` 有该键且 `chmod 600` |
| `Code 订阅通道: 无有效 accessToken` | Code 桌面端未登录 / 凭据已过期 | 打开 Code 客户端登录一次 |
| 命中环 1~2 | 套餐环失效已静默降级 | 看上一行的跳过警告，套餐环为何被跳过 |
| `问答降级链无可用通道` | 全部环的 key 都缺 | 看日志 `[fallback]` 警告行 |
| 回答「没有数据 / 无法判断」 | 该达人的数据确实没采集 | 正常行为（诚实失败，不编造） |

## 合作机会主动通知（工单 coop-automation，2026-10-21 认领完成）

**触达层**的核心增量：不等达人来问，主动把匹配的合作机会推给合适的达人。
（工单原文 `docs/BACKLOG-coop-automation-工单.md`；三层分工见 `DAREN-ARCHITECTURE.md`）

### 流程

```
读飞书「合作机会」表（lark-cli --as user + 120s 缓存，复用 7188 侧 lark_records）
  → 状态在「有意向/已合作」才处理（待评估不推）
  → 按合作类目匹配达人（两条路径取并集）
  → 过滤：只推灰度名单内、已绑定抖音号的达人
  → 去重（record_id+tenant_key）+ 频控（同一达人每周 ≤ N 条）
  → 推文本卡片（品牌/类目/佣金/为什么适合你 + 专属驾驶舱链接）
```

### 匹配的两条路径

| 路径 | 链路 | 匹配理由来源 |
|---|---|---|
| A 商品桥接 | 类目 → `products.category` → `product_talent_match` → 达人 | **现成的** `match_reason`（如"类目一致；价格带匹配"） |
| B 类目直配 | 类目 → `talents.main_category/category` → 达人 | 本模块基于真数据生成（"主营类目「X」与本次合作类目一致"） |

⚠️ **只丢不造**：匹配不到达人 → **不推**。宁可漏推，也不推没有依据的机会。
（真实案例：合作类目「休闲零食」在现有达人库里匹配不到任何人 → 诚实地不推）

### 硬要求（工单原文 → 落实位置）

| 要求 | 落实 |
|---|---|
| ① 防打扰：每周 ≤2 条、同记录同人只推一次 | `bot_coop_push_log` 表（唯一索引 + 周计数），`db.coop_push_exists` / `coop_push_count_week` |
| ② 灰度：默认只推 daren_boss + daren_test2 | `COOP_GRAY_TENANTS`（可用 `DAREN_COOP_GRAY_TENANTS` 覆盖） |
| ③ 推送走 daren_bot Sender（按租户凭证） | `ws.get_sender(tk)` + `db.get_latest_chat_id(tk)` |
| ④ 读表复用 lark_records 模式 | 直接复用 `daren_cockpit.server.lark_records` |

### 触发与运维

- **定时**：`CoopScheduler` 每 30 分钟扫一次（`DAREN_COOP_SCAN_INTERVAL`）
- **按需**：`POST /admin/api/coop-scan?token=…`，body `{"dry_run": true|false}`
  ⚠️ **默认 `dry_run=true`**（只决策不推送）——「一按就推」太危险，真推必须显式传 `false`

```bash
TOKEN=$(cat data/daren-bot/admin_token.txt)
# 先看决策（不推）
curl -s -X POST "http://127.0.0.1:8092/admin/api/coop-scan?token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"dry_run":true}'
# 真推
curl -s -X POST "http://127.0.0.1:8092/admin/api/coop-scan?token=$TOKEN" \
  -H "Content-Type: application/json" -d '{"dry_run":false}'
```

### 排障

| 现象 | 根因 | 处置 |
|---|---|---|
| `scanned=0` | 读表失败或表里没记录 | 看日志 `[coop] lark_records 失败`；确认 lark-cli 登录态 |
| `why=状态=待评估 不在触发集` | 运营还没评估 | 正常（防打扰）；运营改成「有意向/已合作」才推 |
| `why=类目「X」匹配不到灰度达人` | 类目匹配不到人 | 正常（只丢不造）；换类目或先补达人类目数据 |
| `why=已推过` | 去重命中 | 正常（同一记录不重复推） |
| `why=本周已达频控上限` | 频控命中 | 正常（每周 ≤2 条） |
| `灰度名单内没有已绑定抖音号的租户` | 租户没绑号 | `bind_douyin_id` 为空，先绑定 |

## 驾驶舱入口（飞书是门，网页是工作台）

**2026-10-20 交接项①，2026-10-21 补做。** 架构铁律见 `DAREN-ARCHITECTURE.md` 三层分工：
触达=daren_bot / 工作台=7188 网页 / 档案=bitable_sync。

### 为什么必须做

老大 10-20 问「我怎么打开驾驶舱」→ 机器人答不上来（当时 Kimi 额度耗尽）；
恢复后实测又答成了「**抖音官方达人驾驶舱**」——问的其实是**我们自己的 7188 网页**。
`grep 7188|驾驶舱|sczlgyl daren_bot/*.py` → 改之前 **0 处**，对话侧跟工作台完全没连上。

### 现在的行为

| 触发 | 行为 | 计费 |
|---|---|---|
| 消息含「驾驶舱/工作台/后台/看板」 | 直接回该达人的专属链接 | **零成本**（纯导航不是分析） |
| 消息含驾驶舱域名（达人把链接发回来） | 回入口 | 零成本 |
| 报告卡 / 问答卡 / 周报看板长图 | 页脚带同一条专属链接 | — |
| 主语是抖音/字节/官方 | **不劫持**（那是问抖音官方产品） | 走正常问答 |

### 链接形态与安全

```
https://daren.sczlgyl.com/?t=<tenant_key>&k=<该达人的专属 k>     # 一人一链
```

- 唯一来源：`daren_bot/cockpit_link.py`（`tenant_cockpit_link(tenant_key)`），
  **不在各处散落拼串**；k 从 `daren_cockpit/cockpit_keys.json` 读，与 `server.py` 同一份
- ⚠️ **k 等同于访问密码**：日志只记 `tenant_key`、**不打 k**；只出现在发给达人本人的消息和卡片里
- 落地页 `/` 只返回静态外壳（无任何租户数据），**真正的数据在 `/api/cockpit`，那里校验 k**：
  正确 k → 200 / 错误 k → 403 / 裸 `?t=` → 403
- 达人专属 k 没开通时返回空串 → 调用方**跳过**，不因缺链接阻断主流程

### 排障

```bash
# 意图识别自测（正例 True / 反例 False）
.venv-daren/bin/python -c "
from daren_bot import cockpit_link as C
for t in ['我怎么打开驾驶舱','驾驶舱在哪','抖音驾驶舱怎么用','这个号怎么样']:
    print(t, '→', C.is_cockpit_link_question(t))
"
# 日志里应出现：cockpit link sent: tenant=xxx（没有 task claimed = 走的是零成本路由）
grep 'cockpit link sent' logs/daren-bot.launchd.log | tail
```

| 现象 | 根因 | 处置 |
|---|---|---|
| 机器人答成「抖音达人驾驶舱」 | 命中了 LLM 问答而非入口路由 | 看日志有没有 `cockpit link sent`；没有就是意图没命中 |
| 回「专属驾驶舱还没开通」 | `cockpit_keys.json` 里没有该 tenant | 跑 `daren_cockpit/server.py` 的 key 生成逻辑补一个 |
| 卡片上没有链接 | 调用方没传 `link=` | 检查 `notify_*` / `render_*` 是否带上 `tenant_cockpit_link()` |

## 当前边界（内测骨架）

- 免费路由 + 余额/限频 + 预扣/入队/确认卡片：**已接**
- worker 失败 → 自动返还积分 → 推送告知：**已接**
- 浏览器采集 / vision / 意图分类 / 卡片消息格式 / Bitable 建表 / KB 回流：**未接**
  （对应 executor 抛 NotImplementedError，走失败返还路径，不产生静默扣费）
- 无关问题零成本软话术拒答：**已接**（分类器接入前一律软话术，fail-closed）
