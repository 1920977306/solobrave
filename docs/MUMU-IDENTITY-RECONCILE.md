# 「木木」身份对账（两个同名物，勿混）

> **为什么有这份文档**
> 2026-10-27，老大同时在两条会话里看到两个都叫「木木」的东西 ⇒ 差点当成同一个。
> 本文把**两个「木木」逐项实测对账**，并记录**已修复的真故障**。
> ⇒ **目的：让第三个对话不必重新踩这个坑。**
>
> **口径**：全部结论来自本机实测（openclaw 配置 / workspace 文件 / 会话日志 / HTTP 探测 / git 历史），
> 不含推测。测法与时间点见每节标注。

---

## 一、一句话结论

**「木木」有两个，指的不是同一件事：**

| # | 名字 | 本质 | 载体 | 有没有达人业务能力 |
|---|---|---|---|---|
| 1 | **木木（OpenClaw 助手）** | **通用聊天 agent**（人设「商务」）| `~/.openclaw` 的 agent `emp_1787550091124_1048` + 独立飞书应用 `cli_aa51b1b62a7a9bf6` | **有**（见 §3，实测调用过 137 次业务 API）|
| 2 | **daren_bot 达人助理** | **业务机器人服务**（Python 常驻）| `solobrave-prod/daren_bot`，launchd 常驻（label `com.solobrave.daren-bot`）| **有**（本体）|

⇒ **不是「一个是壳、一个是本体」的简单关系** —— 木木通过 HTTP 调本项目 API 也能做达人业务；
真正区别是**谁在跑**：一个是 OpenClaw 里的 agent（靠 `TOOLS.md` 指挥它 curl），一个是本地服务（自带业务逻辑 + WS 长连接）。

---

## 二、两个「木木」逐项对账

### 2.1 木木（OpenClaw 助手）

| 项 | 实测值 | 取证方式 |
|---|---|---|
| agent id | `emp_1787550091124_1048` | `openclaw.json → agents.entries` |
| 名称 | `emp_1787550091124_1048`（entries 里**没有** friendly name）| 同上 |
| 模型 | `kimi_proxy_mumu/k3`，fallback `minimax/MiniMax-M3` | 同上 |
| 飞书应用 | `cli_aa51b1b62a7a9bf6`（账号 id `mumu`）| `channels.feishu.accounts.mumu` |
| 人设 | `IDENTITY.md` = **「木木 - 商务」**（15 字节，全文就这一行）| `cat -A` 逐字读 |
| 人设正文 | `SOUL.md`：**商务拓展 / 客户合作 / 商机跟进**；且**明写**「数据源强制约束：达人数据只能从 `/api/talents` 读」| 读 workspace |
| 工作区 | `~/.openclaw/workspace-emp_1787550091124_1048/`（`ID/AGENTS/USER/SOUL/TOOLS/HEARTBEAT/DREAMS.md` + `memory/`）| `ls` |
| 绑定脚本 | **`solobrave-prod/bind_feishu_mumu.py`**（本项目仓库内）| `git log` ⇒ commit `e8e01da`（**2026-10-23 15:52**）|

### 2.2 daren_bot 达人助理

| 项 | 实测值 | 取证方式 |
|---|---|---|
| 代码 | `solobrave-prod/daren_bot/`（`ws_manager.py` / `handler.py` / `worker.py` / `weekly.py` / `feishu_card.py` / `crypto_util.py`）| `ls` |
| 运行 | launchd 常驻 `com.solobrave.daren-bot`，健康端口 **8092** | `launchctl` + `/api/health` |
| 飞书应用 | **两个租户**：`daren_boss`=`cli_aa461b7cb7fa9cb7`、`daren_test2`=`cli_aa5b67fd7b799d29` | `bot_tenants` 表 |
| secret 存储 | `data/daren-bot/daren_bot.db` 的 `bot_tenants.app_secret_enc`（**AES-GCM 密文**，主钥 `master.key` 0600）| 读库 + 读 `crypto_util.py` |
| 发送形态 | `lark.ws.Client` + `message.create`（**新建**语义 ⇒ 无「已读」）| `ws_manager.py` |
| 能力 | 达人抖音号 → 百应采集 → 账号分析 / 选品评分 → 周报长图 → 积分 → 7188 驾驶舱 | 日志 + 代码 |

### 2.3 形态差异的根因（别归错因）

| | 木木（openclaw）| daren_bot |
|---|---|---|
| 发送 API | `client.im.message.reply(...)`（**回复**语义 ⇒ 飞书标「已读」）| `.im.v1.message.create(...)`（**新建**语义 ⇒ 无「已读」）|
| 回复外观 | **流式卡片**（`streaming.mode=partial` 默认）| 纯文本 / `interactive` 卡片 |
| 模型 | `kimi_proxy_mumu/k3` | 不走 openclaw，机器人侧另接 |

⇒ ★★ **「已读 + 卡片」的差异来自发送端 API 语义，与 LLM provider 无关**
（openclaw 4 个账号 provider 各不相同，**却全是流式卡片** ⇒ 形态不由模型决定）。
⇒ 可复跑探针：`python3 scripts/probe_feishu_reply_mode.py`（rc=0 确认 / rc=1 结论不成立）。

---

## 三、★ 「木木没有达人业务能力」——**该说法不成立**（实测反证）

`workspace-emp_1787550091124_1048/TOOLS.md` **明确写着**本项目 API 的用法：

```
## 达人录入（收到达人资料必须实际调用，严禁口头回复"已录入"）
curl -s -X POST http://localhost:8081/api/talents ...
## 数据查询
- 达人列表: curl -s http://localhost:8081/api/talents -H "X-Agent-Id: emp_1787550091124_1048"
- 商品列表: curl -s http://localhost:8081/api/products ...
- 项目组列表: curl -s http://localhost:8081/api/groups ...
## 知识库 … POST http://localhost:8081/api/knowledge/entries
```

⇒ ★★ **木木确实调用过业务 API**，木木历史会话里逐字命中：

| 端点 | 命中次数 |
|---|---|
| `localhost:8081/api/products` | **98** |
| `localhost:8081/api/influencers` | **36** |
| `localhost:8081/api/talents` | **2** |
| `localhost:8081/api/daren` | **1** |

⇒ ⇒ ★ **结论：木木「有业务能力」**；它不是纯聊天壳。

---

## 四、★★★ 已修复的真故障：木木的 `TOOLS.md` 端口过时 ⇒ 业务能力实际不可用

### 4.1 症状

`TOOLS.md` 里 **7 处**都写 `http://localhost:8081` ⇒ 实测：

| 端点 | 8080 | 8081 |
|---|---|---|
| `/api/talents` | **200** | **426** |
| `/api/products` | **200** | **426** |
| `/api/groups` | **200** | **426** |
| `/api/knowledge/categories` | **200** | **426** |

⇒ 8081 返回：`Failed to open a WebSocket connection: missing Connection header.`

### 4.2 根因

⇒ ★ **8081 从来不是 HTTP API**：`git log -S "WS_PROXY_PORT"` 显示 **2026-09-24** 就把 8081 定为 **plain WS 代理**
（给浏览器连 openclaw 的 `ws://127.0.0.1:18789` 用，见 `solobrave-server.py:179-192`）。
⇒ ★ **HTTP API 的正确端口是 8080**（`/api/health` 200）。

### 4.3 修复（2026-10-27 17:38，A 边）

- **备份**：`~/.solobrave-secrets/bak-20261027-1738/TOOLS.md.bak-173810`（0600，repo 外）
- **改动**：`TOOLS.md` **7 处 `localhost:8081` → `localhost:8080`**（逐字替换，非通配）
  + 头部加 3 行端口说明（**写明 8081 是 WS 代理、打 8081 得 426、历史上从来不是 HTTP API**）
- **验证（端到端，按修好的 `TOOLS.md` 原样执行）**：
  - `/api/talents` ⇒ **200 + 真实数据**（`tal_1787213095215_841db3` 大丸子 …）
  - `/api/products` ⇒ **200**（`{"products": [], "total": 0}`）
  - `/api/knowledge/categories` ⇒ **200**（3 号「公共知识」…）
  - **负向对照**：不带 `X-Agent-Id` ⇒ **401**（`本地调用需要 X-Agent-Id 头或 Bearer token`）⇒ **鉴权真的生效**

⇒ ★ **木木的业务能力至此才真正可用**。

---

## 五、⚠️ 「木木 ≠ 达人业务本体」这个直觉仍然成立（但理由不同）

⇒ 木木是**调用方**（靠 `TOOLS.md` 指挥它 curl），daren_bot 是**实现方**（自带采集/评分/周报/积分 + WS 长连接）。
⇒ ⇒ ★ **所以「木木能不能干达人活」取决于两件事**：
  1. ✅ `TOOLS.md` 的命令是否**正确可用**（本轮已修端口）
  2. ❓ `TOOLS.md` 里**有没有**daren_bot 那套更重的业务（**采集 / 选品评分 / 周报长图 / 积分**）⇒ **实测：没有** ⇒ 木木只覆盖**录入 + 查询 + 知识库**，**不覆盖** daren_bot 的采集与评分。
⇒ ⇒ ★★ **准确说法**：
  - **木木能做**：达人录入、达人/商品/项目组查询、知识库读写（经本项目 HTTP API）
  - **木木不能做**（当前）：百应采集、选品评分（绿黄红）、周报长图、积分、飞书长连接消息流
  - **daren_bot 能做**：以上全部（但它自己的两个飞书应用与木木无关）

---

## 六、openclaw 里的同族 agent（避免「第三个木木」）

`openclaw.json → agents.entries` 全量（2026-10-27 17:3x 实测）：

| agent id | 模型 | 备注 |
|---|---|---|
| `emp_1779430403964` | `kimi_proxy_diaochan/k3` | 貂蝉（同时绑 `feishu/default` + `feishu/t_default`）|
| `emp_1779955656118` | `kimi_proxy_shangguan/k3` | 上官婉儿 |
| `emp_1780132768182` | `kimi_proxy_kongming/k3` | 孔明 |
| `emp_1780199176680` | `kimi_proxy_helen/k3` | Helen |
| `emp_1787202695740_4386` | **`kimi/k3`（直连，非代理）** | ⚠️ **2026-10-27 03:00 新建**（agents 目录时间戳）⇒ 另一个实验体；workspace 目录是 **10-08** ⇒ **不是全新 agent，是老 workspace 被新 agent 复用** |
| `emp_1787550091124_1048` | `kimi_proxy_mumu/k3` | **木木** |

⇒ ★★ **除木木外还有 5 个 Kimi 系 agent** ⇒ 「木木」不是唯一 ⇒ **认人只能用 agent id + app_id，不能按名字找**
（与 §83「后台真名有两个貂蝉」同款坑）。

---

## 七、待老大拍板

1. **要不要把 daren_bot 的重业务（采集 / 评分 / 周报 / 积分）也开放给木木？**
   ⇒ 做法：把这套能力封成本项目 HTTP API，在木木 `TOOLS.md` 里补命令
   ⇒ 不做：木木保持「录入 + 查询 + 知识库」，重业务仍走 daren_bot 的两个飞书应用
2. **要不要给 daren_bot 也保留「独立飞书应用」这条路？**（现状：已是，与木木完全隔离）

---

## 八、复现命令（本文所有结论都能重跑）

```bash
# 木木身份 + 人设
cat ~/.openclaw/workspace-emp_1787550091124_1048/IDENTITY.md      # ⇒ 木木 - 商务
cat ~/.openclaw/workspace-emp_1787550091124_1048/TOOLS.md         # ⇒ 端口说明 + 全部 curl 命令
cat ~/.openclaw/workspace-emp_1787550091124_1048/SOUL.md          # ⇒ 商务人设 + 数据源强制约束

# agent 清单与模型
python3 -c "import json,os;c=json.load(open(os.path.expanduser('~/.openclaw/openclaw.json')));
[print(' ',k,v.get('model'),v.get('name')) for k,v in c['agents']['entries'].items()]"

# 木木历史调用过业务 API（次数）
grep -rho "localhost:808[01]/api/[a-z]*" ~/.openclaw/agents/emp_1787550091124_1048/sessions/ | sort | uniq -c | sort -rn

# 端口对照（8080 通 / 8081 426）
for ep in api/talents api/products api/groups api/knowledge/categories; do
  printf "%-28s 8080=" "$ep"; curl -s -o /dev/null -w "%{http_code}" -H "X-Agent-Id: emp_1787550091124_1048" "http://localhost:8080/$ep"
  printf " 8081="; curl -s -o /dev/null -w "%{http_code}\n" -H "X-Agent-Id: emp_1787550091124_1048" "http://localhost:8081/$ep"
done

# 8081 的真实身份（WS 代理，不是 HTTP API）
grep -n "WS_PROXY_PORT\|plain WS" /Users/qichen/solobrave-prod/solobrave-server.py | head
git -C /Users/qichen/solobrave-prod log --format="%h %ad %s" --date=format:'%m-%d %H:%M' -S "WS_PROXY_PORT" -- solobrave-server.py | tail -3

# daren_bot 租户与密文形态
python3 -c "
import sqlite3
con=sqlite3.connect('file:/Users/qichen/solobrave-prod/data/daren-bot/daren_bot.db?immutable=1',uri=True)
for r in con.execute('SELECT app_id,tenant_key,length(app_secret_enc),status FROM bot_tenants'): print(' ',r)"

# 形态差异探针（reply vs create）
python3 /Users/qichen/solobrave-prod/scripts/probe_feishu_reply_mode.py
```

---

## 九、相关文档

| 文档 | 内容 |
|---|---|
| `docs/PARALLEL-HANDOFF-2026-10-25.md` §83 / §85 / §86 / §87 / §88 | app 归属实测、账号标签串位、形态根因、密钥轮换 |
| `docs/SECRET-ROTATION-RUNBOOK.md` | 密钥轮换 runbook（4 把 app 的账号归属与切换路径）|
| `scripts/probe_feishu_reply_mode.py` | 形态差异探针（能失败）|
| `scripts/rotate_daren_tenant_secret.py` | daren_bot 密文库 secret 轮换（只改 secret，不伤运营数据）|
