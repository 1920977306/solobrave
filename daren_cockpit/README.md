# 达人经营驾驶舱（daren_cockpit）

网页版工作台，微信风白色简约。launchd 常驻（`com.solobrave.daren-cockpit`，日志 `/tmp/daren_cockpit.*.log`）。

- **入口**：`http://192.168.1.25:7188/`（内网）
- **达人**：专属链接 `?t=<tenant_key>&k=<k>`，k 在 `cockpit_keys.json`（不入库，gitignore）
- **运营**：打开 H5 首页会弹「运营口令」输入框，口令 = `cockpit_keys.json` 的 `_ops` 值。⚠️ **没有独立的 `/admin` 页面**（实测 404）—— 运营台就是 H5 首页里的一道口令门，口令存 sessionStorage`['opskey']`，之后请求带 `x-ops-key` 头。运营台**不用** `?t=_ops&k=`：`_ops` 不是 `bot_tenants` 里的达人租户，`tenant_link_key('_ops')` 返回空 → 该 URL 形式**永远 403**。

## 访问鉴权：两种身份，URL 形态完全不同（2026-10-22 老大踩坑后补）

驾驶舱有**两种**访问身份，鉴权机制也不同，**别混用**：

| 身份 | URL 形态 | 后端校验 | 看得到 |
|---|---|---|---|
| 达人 | `https://daren.sczlgyl.com/?t=<tenant_key>&k=<key>` | `tenant_link_key(t)` 非空 **且** `k` 相等 | 只有自己 |
| 运营 | 打开 `https://daren.sczlgyl.com/` → 弹「运营口令」 | 请求头 `x-ops-key` == `ops_key()` | 全部租户 |

### ⚠️ 三个坑

1. **没有 `/admin` 页面**（实测 404）。运营台不是独立页面，是 H5 首页里的一道口令门：口令存 `sessionStorage['opskey']`，之后每个请求带 `x-ops-key` 头。

2. **`?t=_ops&k=<运营口令>` 永远 403。** `server.py:tenant_link_key()` 首行就是 `if not botdb.get_tenant(t): return ""` —— `_ops` 不是 `bot_tenants` 里的达人租户，取不到 key → 校验必挂。**运营台只能用口令弹窗进。**

3. **key 轮换 → 老链接集体 403。** 2026-10-22 为防泄漏轮换了 `cockpit_keys.json` 里的 key，老大手机收藏的旧链接当场 403（新 key 200 / 旧 key 403，实测）。
   已把 403 文案从「无权访问 + 让你从飞书机器人拿链接」改成说人话的**「链接无效或已过期。专属链接会定期更换(安全原因),请向运营老师要一下最新的链接。」**
   ⚠️ **幽灵租户（不存在的 `t`）和错 key 走同一条分支、同一句文案**（`server.py:613` `if not _is_ops(self) and (not lk or k != lk)`）→ **不泄露租户是否存在**。

### 403 排障（先拿响应码，别猜）

```bash
# 1) 本地直查 keys 文件有没有被轮换过（只看键名和值长度，不打印明文）
python3 -c "import json;d=json.load(open('daren_cockpit/cockpit_keys.json'));print({k:len(str(v)) for k,v in d.items()})"

# 2) 公网实际响应码（这是唯一判据）
curl -s -o /dev/null -w "%{http_code}\n" "https://daren.sczlgyl.com/api/cockpit?t=<t>&k=<k>"
```

| 响应码 | 含义 | 下一步 |
|---|---|---|
| 200 | key 与租户匹配 | 正常 |
| 403 | key 错了 / 已轮换 / 租户不存在 | 查 `cockpit_keys.json` 是否被轮换过 → 让运营重推新链接 |
| 000 / 超时 | 隧道或 TLS 层问题 | 走本文档「排障决策表」 |

> 通用教训：**403 只说明「鉴权没过」，不说明「谁错了」。** 别把「我手机打不开」直接写成「服务挂了」—— 先 curl 拿真实响应码，再区分是鉴权 / 隧道 / 浏览器缓存。

## 发版前检查清单（每次改 server.py / index.html 必过）

### 本服务
- [ ] **改完 `index.html` / `apply.html` 后，线上不用重启**：模板按 mtime 热加载
      （`server.py: _load_tpl()`），刷新页面就生效。
      > ⚠️ 2026-10-21 踩过：模板原本在 import 时读进内存 → 磁盘上把「巨量百应」改掉了，
      > 进程却还是改之前启动的旧副本 → **公网仍然显示旧文案**，老大「页面去百应」的要求
      > 看着做完了其实没落地。「我改了文件」≠「线上生效了」（声明端 ≠ 使用端）。
      > 验证口径：改完直接 `curl https://daren.sczlgyl.com/ | grep -c 巨量百应` → 必须是 0。
- [ ] 达人链路：`/api/cockpit?t=daren_boss&k=<正确k>` → 200 + 数据；`k` 写错 → 403；**裸 `/api/cockpit?t=daren_boss`（不带 k）→ 403**
      > ⚠️ 路径必须写全 `/api/cockpit`。`/` 只回静态 HTML 壳（前端无硬编码密钥），**打 `/` 永远是 200，验不出 403**。曾因本清单旧版只写「裸 `?t=`」没写路径，冒烟脚本照抄打 `/?t=`，4 项永远失败。
- [ ] 运营链路：admin 口令门 → roster → 下钻 `?t=`（ops 头通道）→ 改状态写回飞书 → **测完还原状态**
- [ ] 异常态：坏 JSON 400 / 非法 tenant 字符 400 / 缺 record_id 400 / 无 ops 头 403
- [ ] 落地页三卡渲染正常（JS 改动后 `node --check` 抽出的 script）
- [ ] **视觉同源守卫**：`python3 daren_bot/tools/verify_visual_sync.py` → 全绿（exit 0）
      > 校验 H5 驾驶舱与飞书 BaseApp 的**结论颜色 / emoji / 一句话结论**是否还是一套。
      > 唯一事实来源 = `daren_bot/verdict_ui.py`；改任何一边都要跑，漏改会 exit 1。
      > ⚠️ 改守卫本身也要**反向验证一次**（故意改错 → 确认它真的报红），别写恒真断言。
      > `--offline` 只查本地 H5（不调飞书），改 H5 时用这个更快。
- [ ] `launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-cockpit` 重启后冒烟
      → `daren_cockpit/tools/public_smoke.sh --local`（本地直连 7188，**不依赖公网证书**，13 项全绿才算过）
- [ ] `cockpit_keys.json` 未被 git 跟踪（`git status` 里不应出现）

### 关联系统回归（改动接口/鉴权后必查，2026-10-20 误报事故教训）
- [ ] **本机体检探针**（真值版，2026-10-22 落地）：`/Users/qichen/Developer/solobrave-ops/healthcheck.py`
      → 由 launchd `com.solobrave.healthcheck` 每 120s 跑，日志尾部应出现 `驾驶舱=OK`
      ```bash
      tail -1 /Users/qichen/Developer/solobrave-ops/health.log   # 期望：... 驾驶舱=OK
      ```
      检查项四件套（名字与历史云端体检保持一致）：`keys_ok` / `reach` / `http_200` / `form_anon_ok`。
      - `http_200` 必须**带 k** 打 `/api/cockpit`（不带 k 必然 403 → 那是鉴权问题，不是服务故障）
      - `form_anon_ok` = 招商表单 `/apply` 匿名可填
      - ⚠️ 探针**不告警即通过**；真挂了会走 healthcheck 既有的飞书/Bark 通道（30 分钟节流）
      > ⚠️ **原清单里的「每周体检 automation（automation_30e3c477，脚本在 blueprint assets）」已作废**。
      > 2026-10-22 穷举核查：全仓 + `~/.minimax` + `~/.openclaw` + `~/Developer/solobrave-ops`
      > + LaunchAgents + Code 定时任务（`cron_read` 空）+ 洛链运行时配置（`api.lolia.link`
      > 拉下来只有 `localPort 7188`，无任何 monitor/probe 字段）⇒ **该 automation 在本机与洛链均不存在**，
      > 且洛链文档全站没有「云端定时体检」这个功能。保留此行只会让人每周白跑一次。
- [ ] **daren_bot 读取方**：改了接口格式/鉴权/k 的存放方式时，检查 `weekly.py` / `report_card.py` 拼链接的逻辑是否还兼容（格式以 `cockpit_keys.json` 为准）
- [ ] **周报 R1/看板渲染**：改了 `/api/cockpit` 返回字段时，确认 daren_bot 侧周报和看板长图的取数字段没断

### ⚠️ 巡检端口：7188 是唯一真值（2026-10-22 查清）

历史上有个探针把端口写成 **`79999`**，往老大飞书刷「驾驶舱巡检异常」。**这不是驾驶舱坏了，是端口号本身非法**：

| 端口 | urllib 行为 | 含义 |
|---|---|---|
| `79999` / `65536`（**越界**） | `gaierror [Errno 8] nodename nor servname provided` | **端口非法**（超 TCP 上限 65535）→ 报文长得像「DNS 解析失败」，极易误判成「服务连不上」 |
| `65535` / `9999`（合法但无人监听） | `[Errno 61] Connection refused` | 端口对、服务没起 |
| `7188` | `200` | 真端口（launchd `com.solobrave.daren-cockpit`） |

⇒ **教训**：`Errno 8` 在 URL 场景里不等于「DNS 坏了」，先查**端口是否越界**（`>65535`）。
本机探针已加端口闸门：越界直接报「端口非法」并拦下，不再伪装成网络故障。

## 公网暴露（洛链 FRP 隧道）

内网 7188 通过洛链 FRP 暴露到公网。链路分两段，排障先分段定位。

### 部署事实

| 项 | 值 |
|---|---|
| launchd label | `com.solobrave.daren-tunnel` |
| plist | `~/Library/LaunchAgents/com.solobrave.daren-tunnel.plist` |
| 客户端 | `/Users/qichen/bin/lolia-frpc`（LoliaFRP-CLI 0.71.2） |
| 启动参数 | `-t <隧道ID>:<token>`（token 见 frpc.toml，勿外传） |
| 隧道 | ID `31718` · 名称「达人工作台」· 节点「西安电信-1」（中国大陆节点） |
| 访问域名 | `daren.sczlgyl.com`（域名白名单 + DNS TXT 验证通过 + **工信部已备案**：老大确认 + 洛链服务端准入反证，双向印证） |
| Auto TLS | ✅ 已开启（面板 → 高级配置）· 证书由 Let's Encrypt 签发，有效期 90 天 |
| 日志 | `/tmp/daren_tunnel.out.log` / `/tmp/daren_tunnel.err.log` |
| 本地上游 | `127.0.0.1:7188`（本目录 server.py，label `com.solobrave.daren-cockpit`） |

```bash
launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-tunnel   # 重启隧道
launchctl list | grep daren                                     # 看隧道 + 本地服务
tail -20 /tmp/daren_tunnel.out.log                              # 应见「成功启动隧道」
```

### ⚠️ 七个已踩过的坑

1. **`frpc.toml` 不是运行时的配置源。** lolia-frpc 以 `-t <id>:<token>` 模式启动，配置是**运行时从洛链 API 拉的**（日志首行「已获取到配置文件 隧道名称=…」）。改 `frpc.toml` **不会生效**，它只是本地留档（已 gitignore + 600）。域名/端口一律在面板改。

2. **frpc 日志说「您可通过 https://xxx 访问」≠ 域名真的绑定。** 这行只代表服务端下发了配置。**域名是否可用只能在面板或公网侧验证。**

3. **公网不通 ≠ 隧道挂了。** 2026-10-21 实测 `daren.sczlgyl.com`：frpc 日志一切正常（登录成功 + 隧道启动成功）、本地 7188 返回 200，但 443 回落成明文 HTTP、80 端口返回洛链的 `404 - 未绑定域名`。**这类问题 frpc 侧完全无感知。**

4. **洛链没有「系统提供的免费二级域名」。** 面板「访问域名」是**自由文本输入框**（placeholder「例如：114514.com」），不是下拉框；全站文档（`llms-full.txt`）+ 面板 98 个 JS chunk 里搜「免费域名 / 系统域名 / 赠送域名 / 二级域名」**零命中**。想用自定义域名必须走下面的「域名白名单」流程。

5. **80 端口的「未绑定域名」≠ 域名没配置。** 对照实验（同一节点 IP，只改 Host 头）：

   | Host | 响应 |
   |---|---|
   | `daren.sczlgyl.com` | 1847 字节「404 - 未绑定域名」（洛链 vhost 层） |
   | `speedtest.cn-xa-1.qwq.fan` | 516 字节宝塔默认页（节点自身站点） |
   | 任意随机域名 | 1847 字节（**同样**是「未绑定域名」） |

   结论：**「未绑定域名」是洛链 vhost 的默认兜底页**，任何没绑定的域名都长这样 —— 它**不能证明**你的域名有问题。本项目的隧道 `type = 'https'`，frp 只会把它注册到 **HTTPS vhost（443）**，80 上本来就没有对应的 http 代理 → **80 返回 404 是 type=https 的正常表现**。

   真正该看的判据是 **443 的 TLS 握手**。

6. **Auto TLS 证书缓存目录跟 CWD 走，根卷只读会写失败。** 快捷启动的证书默认落在 `程序运行目录/.autotls-cache/`。本项目 plist 早期**没设 `WorkingDirectory`**，frpc 以 `cwd=/` 启动 → 缓存目录变成 `/.autotls-cache` → macOS SIP 下根卷只读 → **证书申请必失败**。已修：plist 加 `WorkingDirectory = /Users/qichen/solobrave-prod/daren_cockpit`，缓存落在 `daren_cockpit/.autotls-cache/`（已 gitignore，私钥不入库）。

   > **改 plist 的正确姿势**：`launchctl bootout` 后 `bootstrap` 在本机会报 `Input/output error`；用 `launchctl load -w <plist>` 即可（本机实测有效）。改完 `plutil -lint` 校验语法。

7. **冒烟预检自己会误报 —— 验证层也需要被验证。** 预检原来拿 openssl 输出里的 `SSL handshake has read` 当「443 没证书」的特征，但**成功握手也打印这行**（实测 `SSL handshake has read 4858 bytes`）→ 证书明明签好了，预检却报「443 无证书」并 exit 2。

   修法：**判据改用「探针能否拿到 HTTP 码」** —— 直接 curl 真实鉴权端点，拿到 `403`/`200` 就说明 TLS 层通了（裸访问本就该 403），拿到 `000` 才是真的没连上；openssl 只用来**打印**证书信息（subject/issuer/verify），不再用来判定成败。

   > 通用教训：`openssl s_client` 的**成功**输出里也会含 `handshake has read`、`Certificate chain` 等看似失败/成功的片段，别拿模糊子串做失败判据。**断言要落在最终行为上**（能不能拿到响应码），不是中间日志的字符串上。

### 域名备案：洛链把它当**创建门槛**，所以「隧道能建起来」本身就是判据

洛链官方文档原文（docs.lolia.link）：

> 只有添加到白名单并通过 DNS 验证的域名，才能在创建 HTTP 隧道时被选择；**在中国大陆节点上创建 HTTP 隧道，还要求域名已完成备案。**
>
> - **已备案**：域名已在工信部登记备案，可以在**中国大陆节点**上创建 HTTP 隧道，面板会同时展示对应的 ICP 备案号
> - **未备案**：只能在**境外节点**上创建 HTTP 隧道
>
> 未备案域名不能创建国内隧道。

**推论（本项目已成立）**：隧道建在**中国大陆节点**「西安电信-1」上、类型 `https`、且**已成功创建并持续运行** → 服务端放行时该域名必然是「已备案」。**备案不是本次 443 不通的原因，也不需要为它换境外节点。**

> 拿不到具体 ICP 备案号的原因（2026-10-21 实测，别再重试）：工信部官方 API `hlwicpfwc.miit.gov.cn/icpproject_query/api/auth` 对本机 WAF 拦截（连 `qq.com` 对照也返回 `{"code":500,"msg":"服务器异常"}` → 是被拦不是查不到）；`beian.miit.gov.cn` 浏览器渲染空白；第三方源只暴露各自站点 footer 的 ICP 号。**要具体备案号只能看面板域名白名单页域名后方的备案标记。**

> ✅ **2026-10-21 老大确认：`sczlgyl.com` 是工信部备案的。** 与上面的服务端准入反证**相互印证**（两条独立证据同向）。
>
> 因此洛链文档里「部分节点只认特定接入商的备案（例如阿里云节点要阿里云接入备案）」这条**对本项目不构成风险** —— 已经在境内节点「西安电信-1」跑通并签下证书。以后再遇到「要不要换境外节点 / 换节点」的问题，答案是**不用换**。

### Auto TLS（HTTPS 的开关，容易漏）

隧道「高级配置」里的 **Auto TLS** 打开后，客户端会在**本地**用 `TLS-ALPN-01` 向 Let's Encrypt 申请证书并起 `https2http` 插件（本地是 HTTP 服务时适用——本项目 7188 就是 HTTP，正好匹配）。

- 生效前提：隧道类型为 https + 快捷启动 + LoliaFRP-CLI ≥ 0.67.1（本机 0.71.2 ✅）
- **改完必须重启隧道**，frpc 才会拉到新配置：`launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-tunnel`
- **不开 Auto TLS → 443 没有证书 → 表现为「443 回落成明文 HTTP」**
- 验证证书是否真签下来：看 `daren_cockpit/.autotls-cache/` 有没有生成文件 + frpc 日志有无证书相关报错

**客户端侧前置条件自检**（开完 Auto TLS 后若证书仍签不下来，逐条查）：

| 检查项 | 命令 | 合格线 |
|---|---|---|
| 本地 443 空闲 | `lsof -nP -iTCP:443 -sTCP:LISTEN` | 无输出（frpc 要 listen 443 做 ALPN 验证） |
| 无代理软件劫持 | `ps aux \| grep -Ei 'clash\|surge\|v2ray\|xray\|shadow'` | 无代理进程 |
| Let's Encrypt 可达 | `curl -s -o /dev/null -w '%{http_code}' https://acme-v02.api.letsencrypt.org/directory` | `200` |
| 证书缓存目录可写 | `ls -d daren_cockpit/.autotls-cache` | 路径可写（靠 plist 的 `WorkingDirectory` 保证） |

> ⚠️ **Let's Encrypt 对证书申请频率有限制，过量申请可能临时封禁。** 改完配置**只重启一次**隧道，别反复 `kickstart` 去试。

> **兜底（未实施，P1）**：面板开关无效时可改用手动 frpc 配置 + `[proxies.plugin.autoTLS] cacheDir = './.autotls-cache'`。风险是与面板那条隧道抢同一个 vhost，需要先停掉旧隧道，切换有掉线风险，暂不作为首选。

### ✅ 当前状态（2026-10-21 15:19 实测）

| 项 | 实测值 |
|---|---|
| Auto TLS | 面板已开 → 服务端下发 `[proxies.plugin.autoTLS] enable = true` + `https2http` |
| 签发方式 | `TLS-ALPN-01`，Let's Encrypt 生产 CA（`issuer=CN=YE2`） |
| 证书有效期 | **90 天**，notBefore `2026-10-21 06:20:51 GMT` → notAfter **`2027-01-19 06:20:50 GMT`** |
| 缓存目录 | `daren_cockpit/.autotls-cache/`（`daren.sczlgyl.com` + `…+rsa` + `acme_account+key`，全 600） |
| 443 握手 | `subject=CN=daren.sczlgyl.com`，`Verify return code: 0 (ok)` |
| 公网冒烟 | **13/13 全绿**（`tools/public_smoke.sh` 退出码 0） |
| 隧道重启行为 | ✅ **安全**：15:30 实测 `kickstart` 后 → 证书**复用缓存**（notBefore/notAfter 与文件 mtime 都没变、日志无「开始预申请」）→ **不消耗 Let's Encrypt 申请额度**、不掉线（重启后公网冒烟仍 13/13） |

> ⚠️ **首次签发那一刻，frpc 日志的「过期时间」不可信。** 15:19 首次签发时日志写「过期时间 2026-10-22T07:19:09Z」（= 签发 +24h，是**预申请阶段**的值），但实际证书到 **2027-01-19**（本地缓存与公网返回的 notAfter 双向核对一致）。
>
> **缓存命中后**（即隧道重启时）日志报的过期时间 = 真实到期时间（实测差 4 秒）。**任何时候都以 openssl 读到的 notAfter 为准**：
>
> ```bash
> openssl x509 -in daren_cockpit/.autotls-cache/daren.sczlgyl.com -noout -dates
> echo | openssl s_client -connect daren.sczlgyl.com:443 -servername daren.sczlgyl.com 2>/dev/null \
>   | openssl x509 -noout -dates
> ```

### 排障决策表

| 现象 | 含义 | 处理 |
|---|---|---|
| 443 `wrong version number` / `tlsv1 alert` | **主判据**：443 无 TLS 证书 | 开 **Auto TLS** + 重启隧道；查 `.autotls-cache/` 是否生成 |
| 冒烟预检报「443 无证书」但 `openssl s_client` 其实成功 | ⚠️ **预检误报**，不是真故障 | 见下方第 7 坑；预检已改用「探针能否拿到 HTTP 码」判定 |
| 80 端口 `404 - 未绑定域名` | ⚠️ **正常现象**，type=https 不注册 http vhost | 不用管（除非新建的 type=http 隧道也这样） |
| 域名新建后完全不生效 | 白名单没加 / 没过 DNS TXT 验证 | 去**域名白名单**页加域名 + 过验证 + 查备案标记 |
| 面板里选不到「境内节点」 | 备案标记为**未备案** | 去备案；已备案则**换节点**（部分节点只认特定接入商备案，如阿里云节点要阿里云接入备案） |
| 隧道在境内节点上跑着但拿不到备案号 | 备案**已生效**（服务端放行即判据） | 看面板白名单页域名后的备案标记取号，**不用再纠结是不是没备案** |
| 日志无「成功启动隧道」 | 隧道没起来 | `launchctl kickstart -k` |
| 冒烟全 000 且本地 7188 也不通 | 本地服务挂了 | 查 `com.solobrave.daren-cockpit` |

### 冒烟脚本

```bash
daren_cockpit/tools/public_smoke.sh          # 公网冒烟：自动从洛链 API 拉当前域名
daren_cockpit/tools/public_smoke.sh <域名>    # 或手动指定
daren_cockpit/tools/public_smoke.sh --local   # 本地直连 7188：只验鉴权层，跳过 TLS 预检
```

**13 项**：鉴权 9 项（裸 `/api/cockpit` 403、错 k 403、两个租户正确 k 200、幽灵租户无 k 403/404、幽灵租户假 k 403、坏 tenant 400、roster 无 ops 头 403、带 ops 头 200）+ PWA 资源 4 项（manifest + 三个 icon）。

> **鉴权端点是 `/api/cockpit`，不是 `/`。** `/` 只回静态 HTML 壳（前端无硬编码密钥，ops key 走 sessionStorage 手输），数据由前端 `fetch('/api/cockpit?t=..&k=..')` 取 —— 打 `/` 永远是 200，**结构上就验不出 403**。曾踩：脚本打 `/?t=..`，4 项永远失败（根因是上方检查清单旧版只写「裸 `?t=`」没写路径）。
>
> **不查 `/sw.js`**：service worker 本项目从未实现（index.html 无 `navigator.serviceWorker.register`、manifest 无 `service_worker` 字段、仓库无 sw.js），浏览器根本不会去请求它 —— 查它是**凭空造需求**。PWA 走「manifest + icons」路线（iOS apple-touch-icon / Android manifest）。将来真做离线缓存再加回来。

退出码：`0` 全过 · `1` 有失败 · `2` 443 无证书（**未执行**，不是鉴权挂）。预检专门用来把「证书没签下来」和「鉴权全挂」区分开——否则 443 回落明文会让 13 项全报 000，看着像鉴权崩了实则根本没连上。

> **`--local` 是分段定位用的**：链路 = `公网域名 → frps 节点 → frpc → 127.0.0.1:7188`，`--local` 直连最后一环，**不依赖证书**。所以改完 `server.py` 不用等公网证书就能验鉴权层。
>
> 2026-10-21 本地 **13/13 全绿** → 证明服务端鉴权层本身没问题，公网不通的缺口**只在 TLS 证书这一段**（唯一待办：面板开 Auto TLS）。

**当前基线（2026-10-21 15:19）**：公网 `https://daren.sczlgyl.com` **13/13 全绿，退出码 0**。

> token 不内嵌在脚本里（从环境变量 `LOLIA_TOKEN` 或 `frpc.toml` 读），因此脚本可安全入库。改脚本时勿写死 token。

## 招商合作「减负」提交（2026-10-21 新增）

### 为什么改

商家嫌飞书多维表格分享表单字段多不愿填（8 个字段，其中连「商品名」都没有）。
商家真实意愿：**只给商品链接 / 商品图片 / 其他达人带过的商品 / 一段话描述**，其余由 AI 补。

### 新入口

`https://daren.sczlgyl.com/apply`（本地 `:7188/apply`）—— 独立 HTML，**不依赖 Tailwind CDN**（移动端首屏更稳）。

| 字段 | 必填 | 说明 |
|---|---|---|
| 品牌 / 商家 | ✅ | |
| 联系方式 | ✅ | 微信号或手机号都行（一格，运营表里自动拆「联系人/联系方式」两列） |
| 商品链接 | 任选一 | 淘宝/抖音/拼多多都行 |
| 商品图片 | 任选一 | 手机拍照或选图，**客户端压到长边 ≤1400px / JPEG q0.85** 再上传 |
| 商品描述 | 任选一 | 一句话即可 |

「三项全空」直接拒（不知道推什么给达人）。

### 链路

```
POST /api/apply  →  落本地表 bot_coop_apply（status=pending）→ 立即返回提交编号 AP-XXXXXX
                    ↓ 后台线程（异步，不让商家干等）
   链接 → executors.collect_product_by_url()  团长端浏览器采集（1-2 分钟）
   图片 → executors._vision_extract_product()  套餐 key 多模态（~4 秒）
   描述 → executors._qa_llm() + JSON 抽取      类目/商品名/佣金
                    ↓ 回填
   本地表回填 + 同步飞书合作机会表（新品名/商品链接两个字段）
```

- **异步是硬要求**：链接采集要走浏览器（全局单例、1-2 分钟），同步等会让商家在网页上干等。
- **只丢不造**：解析不出来的字段留空 + `status='partial'`，绝不编商品信息。
- 商家侧提示区分「商家的问题」和「我们的问题」：链接识别失败九成是**团长端登录态过期**，
  提示写「我们这边的采集账号需要重新登录」，不让商家背锅（`user_hint` 字段）。

### 端点

| 端点 | 方法 | 鉴权 | 说明 |
|---|---|---|---|
| `/apply` | GET | 公开 | 提交页 |
| `/api/apply` | POST | 公开（限流 8 次/小时/IP） | 提交；返回 `{ok, code}` |
| `/api/apply/status?code=AP-XXXXXX` | GET | 公开 | 查进度 + 已识别到的商品字段 |

落地页「招商合作」角色卡、达人页「提交合作意向」、运营页「提交合作意向（减负版）」均已指向 `/apply`；
飞书分享表单降级为「填完整表 →」备选。

### 飞书表新增字段（2026-10-21）

合作机会表新增 `商品名`、`商品链接`（text）。字段不存在时自动降级写进「合作说明」，不硬失败。

### ⚠️ 三个坑

1. **别让日志成哑弹**：`daren_cockpit/apply.py` 和 `daren_bot/executors.py` 都用 `logging.getLogger`，
   但 `server.py` 原来**没调 `logging.basicConfig()`** → 提交解析失败时日志里一行都没有，
   表现成「功能静默不工作」，排查时完全瞎。已在 `main()` 里显式 `basicConfig(level=INFO)`。
2. **record_id 的路径不固定**：`record-upsert` 的**创建**响应里 id 在 `data.record.record_id_list[0]`，
   **更新**响应只有 `{"record":{"update":{...}},"updated":true}`（压根没 id）。
   → 多种路径都试，取不到就**回查**（按品牌+联系方式找最新一条）。否则「同步成功但拿不到 id」= 静默失败。
3. **`grid-template-columns:1fr` 会撑破**：`1fr` 的最小尺寸是 min-content，商品名一长就把列撑破
   （手机 420px 视口横向溢出 22px）。必须写 `repeat(2,minmax(0,1fr))`；长文本容器加 `overflow-wrap:anywhere`。

## 商品推荐展示改版（2026-10-21）

老大反馈「商品一多就一直往下划」。原实现：服务端硬截 `[:3]` 后**全量平铺**。

现在：
- 服务端 `load_cockpit` 返回**全量**商品（上限 60 条控 payload），每条带 `is_today`（服务端按本地日期算）。
- 前端分层：
  - **今日推荐** —— 今天的商品，网格展示（手机 2 列 / 宽屏 3 列），超过 4 件出现「换一批 / 上一批」。
  - **最近推荐** —— 今天没有新推荐时的降级态，显示最近一次日期。
  - **更多推荐** —— 历史商品收进 `<details>`，**默认折叠**。
- **合作机会区同样收敛**：默认最近 3 条，其余进「更多合作机会」折叠。
- KPI「本周推荐商品」改名「推荐商品」（现在显示总数）。

> 改布局时注意：**inline `onclick` 只能查全局作用域** —— `switchProductBatch()` 写在 `render()` 内部
> 会变成「点了没反应」。这类函数必须留在顶层。

## 商品数据查询：商品ID → 平台接口（2026-10-21 新增）

### 为什么走这条路

老大原话：「商品链接直接打开没有用 没有数据 必须拿到商品链接上的商品ID 然后去巨量找这个商品」。

**实测坐实了这个判断**：商品页 `haohuo.jinritemai.com/.../detail/index.html?id=<ID>`
留存 HTML 389KB，`product_id` 出现 **0 次**，正文只有「请使用抖音APP扫码 / 点此进入抖音查看商品信息 /
检测到当前环境存在安全风险」的壳 —— **页面正文里没有商品数据**。
所以「打开链接 → AI 读正文」这条路必然瞎编（AI 只能按商品名编价格）。

有数据的是**达人端选品广场的商品接口**，输入商品ID 直接返回结构化真值。

### 接口契约（浏览器上下文实测，非文档推测）

```
POST https://buyin.jinritemai.com/pc/selection/common/material_list
Content-Type: application/json

{"scene":"PCSquareSearch","size":5,"search_text":"<商品ID>","cursor":0,
 "extra":{"new_session_strategy":"1","search_id":"","session_id":"",
          "use_kol_product":"1","trigger_ids":"","feed_source":"","share_return_scene":""},
 "filters":{}}
```

响应 `data.summary_promotions[0].base_model` 里就是商品数据：

| 字段 | 路径 | 实测值 |
|---|---|---|
| 商品名 | `product_info.name` | 亲肤不冰被子固定器家用无痕床单… |
| 到手价 | `marketing_info.price_desc.price.origin`（**单位是分**） | 890 → ¥8.9 |
| 佣金费 | `promotion_info.cos_info.cos.cos_fee.origin`（分） | 445 → ¥4.45 |
| 佣金率 | `promotion_info.cos_info.cos.cos_ratio.origin` | 50 → 50% |
| 月销 | `product_info.month_sale.origin` | 589 |
| 好评率 | `product_info.good_ratio.origin` | 91.3% |
| 三级类目 | `product_info.category.first/second/third_category.category_name` | 居家日用 / 收纳整理工具 / 夹子 |
| 店铺 | `shop_info.shop_name` | 萌乐用品店1号 |
| 店铺体验分 | `shop_info.shop_score_info.shop_score.score` | 81 |
| 带货达人数 | `promotion_info.cooper_author_num.origin` | 333 |
| 近30天日销 | `product_info.sale_axis[]` | `[{x:'20260921',y:7},…]`（30 点） |
| 优惠标签 | `tag_info.second_tags[].text.text` | 消费券 / 现货 / 包邮 |

> `category` 对外给**一级类目** —— 达人侧的「带货类目分布」用的就是一级类目
> （服饰内衣/美妆/居家日用…），两边不同层就没法比。二三级另存 `category_second/third`。

### ⚠️ 六个坑（全是实测踩出来的，不是猜的）

1. **`scene` 必须是 `PCSquareSearch`（搜索场景）**。用 `PCSquareFeed`（信息流场景）时
   `search_text` 被**静默忽略**，返回全量商品流（`total: 2998`）—— 不是你要的商品，**且不报错**。最坑的一个。
2. **必须用绝对 URL**。相对路径 `/pc/...` 按当前页面 origin 解析 —— 页面若停在 `haohuo`/`douyinec` 域
   就打到错误的域 → 404。
3. **必须先落到 buyin 域**（跨域被 CORS 拦；`about:blank` 没有 base URL）。
   跳转是**连跳**（`mpa/account/login` → sso → `dashboard`），
   跳转期间执行 evaluate 会抛 `Execution context was destroyed` → 要等 URL **连续两次不变**再动手。
4. **直接 goto `dashboard` 不会建立会话** → 接口返回 `code=-2 用户未登录`。
   ⚠️⚠️ **绝对不要用带 `log_out=1` 的 URL 自动「重登」** —— 那是**登出**语义。
   反复走它会让服务端把已有会话标为失效（实测踩过：cookie 还在本地，
   但 SSO `check_login` 返回 `subject_has_login:false`，接口全线 `code=-2`）。
   **登录是人工动作（扫码）**，系统的正确做法是「引导扫码 → 轮询等待 → 自动续跑」；
   进工作台的入口 URL 要**去掉 `log_out` 参数**（`_enter_daren_workspace()` 干这件事）。
   ⚠️ `code=-2` 是**会话问题，不是商品查不到** —— 混淆了就会把正常商品误报成「已下架」。
5. **接口 URL 带 `msToken` + `a_bogus`（抖音请求签名）**，我们自拼的 fetch 拼不出 `a_bogus`
   → **新会话冷启动时服务端不报错、直接返回空列表**。所以「空」要当「可能没就绪」重试
   （已做：3 次重试 + 刷新会话 + 必要时 SSO 重建），不能当「商品不存在」直接报错。
6. **达人端 cookie 在兄弟域**：`sid_ucp_sso_v1_daren` 挂在 `.buyin-daren-sso.jinritemai.com`，
   只查 `buyin.jinritemai.com` 域**永远查不到** → 假阴性「登录态过期」。（已修：`ctx.cookies()` 查全部域。）
7. **等待扫码期间绝对不要导航页面**：老大手动扫码需要页面停在原地，
   定时把页面跳走 = 打断他正在扫的码（白扫）。
   所以登录检测是**状态驱动**的：官网首页**出现**「达人工作台」入口 = 扫码已完成，才跳进去；
   没出现就**什么都别动**，只 sleep + 重试接口。（`_enter_daren_workspace()` 干这件事。）

### 会话失效时的完整流程（登录是人工动作）

```
接口返回 code=-2
  → _prompt_daren_login()：官网首页 → 「登录」→「达人工作台」→ 停在扫码页
  → 每 15 秒重试一次接口，最长等 300 秒（DAREN_LOGIN_WAIT）
  → 老大扫码完成 → 官网出现「达人工作台」入口 → _enter_daren_workspace() 进工作台
  → 接口返回 code=0 → 任务自动续跑完成
  → 超时未登录 → need_login + 可读提示「请在弹出的浏览器里扫码重登」
```

**系统自动化的边界**：引导 / 检测 / 等待 / 续跑 —— 全部自动；
**扫码这一步永远是人工**。不做「自动重登」（任何形式的自动登出都会注销自己的会话）。

### 推广池边界（业务上要知道）

接口只覆盖**当前在售 / 在推广池**的商品。2022 年的老商品能查到评论
（`/pc/decision/comment/list?product_id=<ID>` 返 200 有数据）但**选品广场搜不到**。

这种情况抛 `ProductNotFound`（**不是**技术故障）→ 不重试、不回退，直接指路：

> 平台查不到这个商品（可能已下架，或不在当前推广池）—— 发个商品截图我照样能看

（截图走 vision 链路，与浏览器无关，**始终可用**。）

### 端点

| 端点 | 用途 |
|---|---|
| `POST /collect-product-id` `{product_id, size}` | 商品ID → 结构化商品数据；排队取 `GET /jobs/<cid>` |
| `GET /daren-net?limit=N&q=关键字` | **常驻**流量日志（人工在浏览器里手动操作也能抓到接口） |
| `POST /daren-fetch` `{path, method, body}` | 在达人端页面上下文里 fetch 任意接口（自带登录 cookie） |
| `POST /probe-product-api` | 探路：进达人工作台 → 抓接口清单 / DOM / 菜单 |

### 代码位置

- `daren_bot/product_link.py` — `extract_product_id(url)`（抖音商品链接里的 19 位数字就是商品ID）
- `daren_bot/executors.py` — `collect_product_by_id()` / `collect_product_by_url()`（优先 ID 通路）
- `buyin_worker.py` — `_run_daren_product_impl()` / `_parse_daren_product()` / `_ensure_buyin_page()` / `_daren_sso_relogin()`

### 数据形态契约 ↔ 呈现对齐（别踩）

`stats` 是给前端/缓存用的**完整结构**（含 30 天销量曲线），但 `_product_verdict()`
**不能把 stats 整包塞进 prompt** —— 30 个日销点会烧 token 还会把结论带偏（AI 会去数明细）。
已排除 `sale_axis/main_img/promotion_id/resolved_url/total`，并把日销曲线压成一行摘要
「近30天日销：最高X、最低Y、合计Z」。

## 已知边界

- 飞书 lark-cli 只能 `--as user`；启动预热只暖 daren_boss + roster
- echarts 走 unpkg + bootcdn 兜底（jsDelivr 被墙）
- CLT sqlite 3.54 打不开 WAL 只读 URI → 用普通连接 + `PRAGMA query_only=ON`
- **商品链接采集现在走「商品ID → 平台接口」**（见上一节），依赖**达人端**浏览器登录态
  （cookie `sid_ucp_sso_v1_daren`，挂在 `.buyin-daren-sso.jinritemai.com` 域）。
  ⚠️ `GET /health` 的 `daren_logged_in: false` 可能是「浏览器没启动」而不是「掉登录」——
  **只有 `daren_browser_up: true` 且 `daren_logged_in: false` 才是真的登录态失效**。
  旧链路的团长端登录态（`ecom_us_lt_buyin` + `sessionid`）只用于「ID 解析不出来」时的页面解析兜底。
  → **商家的图片路径始终不受影响**（vision 走套餐 key，与浏览器无关），所以「发商品图」永远可用。
- 商品图是飞书 attachment 字段，**尚未接入下载通道** → 卡片里 `image` 恒为空（接口已留好）。

## 视觉：与飞书 App 同源（2026-10-22）

老大反馈「两个设计风格没有一样，不然会以为是两个系统」。查证后确认**两端展示的是同一个达人（hlb725 洁洁爱户外）的同一套数据**——飞书统计卡的 filter 就是 `抖音号 = hlb725`——所以这个观感是真的。

修法不是追求像素一致（飞书的顶栏/卡片/主题色被平台锁死），而是让**三层语义**对齐：

| 层 | 规则 | 本次落地 |
|---|---|---|
| ① 品牌识别 | 两边同名 | H5 顶栏加**品牌头**（绿点 + 「达人经营驾驶舱」），与飞书 App 顶栏 App 名一致；landing / 运营视图同步 |
| ② 色彩语义 | 同一句话同色 | 飞书 4 个单选字段 `options.hue` 按 `daren_bot/verdict_ui.py` 改色；H5 徽章按同一张表取色 |
| ③ 内容结构 | 同一套指标名/口径句/结论 emoji | 飞书新增「本周一句话」块；口径句统一「数据来自我们系统」 |

**修掉的两个真割裂**：
- 飞书「评估结论」潜力型是**紫**标签、「🟢 能带」是**红色**标签（emoji 与颜色反了），H5 是绿的 → 同一句话两个颜色。
- 飞书商品结论选项名带 emoji（`🟢 能带`），H5 直接拿它拼 `.v-*` 类名 → 拼出不存在的类 → **结论标签掉底色**（裸文字）。修法：`verdictKey()` 归一化（Python 侧 `verdict_ui.verdict_key()` 是孪生）。

**守卫**：`python3 daren_bot/tools/verify_visual_sync.py`（见「发版前检查清单」）。映射表全量见 `docs/DAREN-DASHBOARD-TEMPLATE.md` §9.3，API 边界见 `AGENTS.md` §10。

## 数字展示：粉丝量统一「万」单位（2026-10-22）

老大定调「1.3万这样好看点」。同一个粉丝数在**四个地方**展示，必须给同一个字符串：

| 位置 | 实现 |
|---|---|
| 飞书 App 指标卡 | 公式字段 `粉丝(万)` = `ROUND([粉丝]/10000, 1)`（区块名「粉丝总量(万)」） |
| H5 驾驶舱 | `index.html` 顶层 `fmtFans()` |
| 飞书长图看板 | `daren_bot/dashboard_card.py` → `fmt_wan()` |
| 周报文字 | `daren_bot/weekly.py` → `fmt_wan()` |

- **唯一事实来源** = `daren_bot/fmt.py` 的 `fmt_wan()`；`fmtFans()` 是它的 JS 孪生。
  两份手写实现靠人同步早晚会忘 → `tests/test_fmt_consistency.py` 用 node 实跑 JS
  跟 Python **对拍**（反向验证过：故意改错 JS 阈值 → 测试立刻红并指出是哪个数不一致）。
- 规则：< 1 万 → 千分位原值（5,251）；≥ 1 万 → X.X 万（12,804 → 1.3万）；整数不带小数。
- ⚠️ 舍入用 `floor(x/10000*10+0.5)/10`（远离零），**不要用 Python 内置 `round()`**
  （银行家舍入，和 JS 对不上）。
- **不跟着转**：图表 Y 轴与数据点标签（真值才看得出变化）、粉丝环比增量、周报给 AI 的 diff、
  「X 个粉丝带出 Y 成交」这种语义是「人数」的句子。

### 改完 H5 怎么验（不用重启，但一定要真渲染）

模板按 mtime 热加载（见发版清单）→ 改完刷新页面就生效。验证两件套：

```bash
# 1) 语法
python3 - <<'PY'
import re
s = open('daren_cockpit/index.html', encoding='utf-8').read()
open('/tmp/h5_all.js','w',encoding='utf-8').write(
    '\n'.join(re.findall(r'<script>(.*?)</script>', s, re.S)))
PY
node --check /tmp/h5_all.js && echo "JS OK"

# 2) 真渲染（手机视口截图，别只看代码）
python3 - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width":390,"height":844},
                        device_scale_factor=2, is_mobile=True)
    pg = ctx.new_page()
    pg.goto("https://daren.sczlgyl.com/?t=daren_boss&k=<key>",
            wait_until="networkidle", timeout=45000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path="/tmp/h5.png", full_page=True)
    b.close()
PY
```
