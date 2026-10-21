# 达人经营驾驶舱（daren_cockpit）

网页版工作台，微信风白色简约。launchd 常驻（`com.solobrave.daren-cockpit`，日志 `/tmp/daren_cockpit.*.log`）。

- **入口**：`http://192.168.1.25:7188/`（内网）
- **达人**：专属链接 `?t=<tenant_key>&k=<k>`，k 在 `cockpit_keys.json`（不入库，gitignore）
- **运营**：admin 页口令 = `cockpit_keys.json` 的 `_ops` 值

## 发版前检查清单（每次改 server.py / index.html 必过）

### 本服务
- [ ] 达人链路：`/api/cockpit?t=daren_boss&k=<正确k>` → 200 + 数据；`k` 写错 → 403；**裸 `/api/cockpit?t=daren_boss`（不带 k）→ 403**
      > ⚠️ 路径必须写全 `/api/cockpit`。`/` 只回静态 HTML 壳（前端无硬编码密钥），**打 `/` 永远是 200，验不出 403**。曾因本清单旧版只写「裸 `?t=`」没写路径，冒烟脚本照抄打 `/?t=`，4 项永远失败。
- [ ] 运营链路：admin 口令门 → roster → 下钻 `?t=`（ops 头通道）→ 改状态写回飞书 → **测完还原状态**
- [ ] 异常态：坏 JSON 400 / 非法 tenant 字符 400 / 缺 record_id 400 / 无 ops 头 403
- [ ] 落地页三卡渲染正常（JS 改动后 `node --check` 抽出的 script）
- [ ] `launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-cockpit` 重启后冒烟
      → `daren_cockpit/tools/public_smoke.sh --local`（本地直连 7188，**不依赖公网证书**，13 项全绿才算过）
- [ ] `cockpit_keys.json` 未被 git 跟踪（`git status` 里不应出现）

### 关联系统回归（改动接口/鉴权后必查，2026-10-20 误报事故教训）
- [ ] **每周体检 automation**（`automation_30e3c477`，脚本在 blueprint assets）手动试跑一次 dryRun，确认 checks 全绿（含 form_anon_ok：招商表单匿名可填）
- [ ] **daren_bot 读取方**：改了接口格式/鉴权/k 的存放方式时，检查 `weekly.py` / `report_card.py` 拼链接的逻辑是否还兼容（格式以 `cockpit_keys.json` 为准）
- [ ] **周报 R1/看板渲染**：改了 `/api/cockpit` 返回字段时，确认 daren_bot 侧周报和看板长图的取数字段没断

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

## 已知边界

- 飞书 lark-cli 只能 `--as user`；启动预热只暖 daren_boss + roster
- echarts 走 unpkg + bootcdn 兜底（jsDelivr 被墙）
- CLT sqlite 3.54 打不开 WAL 只读 URI → 用普通连接 + `PRAGMA query_only=ON`
