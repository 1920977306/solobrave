# 密钥轮换操作清单（老大手动执行，@贾维斯 自动复核）

> 生成时间：2026-10-26 17:15
> 事实来源：全部为**当场实测**（`openclaw secrets audit` / `sqlite3` / `git config`），
> 不是引用旧报告。执行前请确认现状仍与「清单里的现状」一致。

## ⚠️ 全局铁律（违反任何一条 ⇒ 这次轮换就是给自己制造新事故）

1. **顺序不可反**：**先生成新的 → 验证新的能用 → 再撤旧的**。
   反过来做 = 一旦新 key 有问题，所有服务立刻全灭，且**回不去了**（旧的已撤）。
2. **不同时轮换多个 key**。一次只动一个，验通了再动下一个。
   ⇒ 出问题时，你能立刻知道是哪一个。
3. **新 key 不进仓库、不进 `.env`、不进 git config、不进 shell history**。
   写入方式见每一步的「写入命令」。
4. **每一步都有断言**：命令输出必须出现指定内容，否则**停下来**，不要继续下一步。
   清单里每步都标了「✅ 断言」和「❌ 若不满足」。
5. **轮换完不要立刻删旧 key 的后台记录**，先跑完 §4 的全量复核，
   再在飞书/GitHub 后台撤销（后台撤销是最后一步，不是第一步）。

---

## 1. GitHub PAT 轮换

### 现状（2026-10-26 实测）

| 项 | 值 |
|---|---|
| 仓库 | `github.com/1920977306/solobrave.git` |
| 账号 | `1920977306` |
| 凭据来源 | `credential.helper = osxkeychain`（全局）|
| keychain 条目 | `security find-internet-password -s github.com` → `acct = 1920977306` |
| `~/.solobrave_github_pat` | **不存在**（一次性验证用的临时文件，用完即焚）|
| 已有的验证脚本 | `scripts/verify_github_pat.sh`（只读 PAT → 真 push → 删文件）|

### 步骤

**Step 1 — 生成新 PAT（GitHub 后台，老大手动）**

- 位置：GitHub → Settings → Developer settings → Personal access tokens →
  **Fine-grained tokens** → Generate new token
- 名称：`solobrave-dev-rotation-2026-10-26`
- 权限：
  - Repository access → **Only select repositories** → 选 `solobrave`
  - Permissions → Repository permissions → **Contents: Read and write**
    （如果还要动 PR/release，再勾 Pull requests / Metadata，Metadata 是必勾的）
- **不要勾** Workflows、Administration、Secrets —— 权限越小越好，这是轮换的意义

> ✅ 断言：生成成功页会显示一串 `github_pat_...` 开头的 token，**只显示一次**。
> ❌ 若页面显示的是 classic token（`ghp_` 开头）也能用，但 fine-grained 权限更小，优先 fine-grained。

**Step 2 — 把新 PAT 写入一次性文件（不进 history）**

```bash
umask 077 && printf '%s' '<粘贴刚才那串 github_pat_...>' > ~/.solobrave_github_pat
```

> ✅ 断言：
> ```bash
> ls -l ~/.solobrave_github_pat   # 必须是 -rw------- (600)
> echo -n "长度: "; tr -d '\r\n' < ~/.solobrave_github_pat | wc -c   # 必须是 40+ 位
> ```
> ❌ 若长度 < 20 ⇒ 说明把 `<粘贴...>` 占位符原样存进去了，回到 Step 2 重来。
> ❌ 若 `wc -c` 输出里带 `>` ⇒ 命令行里带了重定向符，token 会进 history，改用上面的 `printf` 写法。

**Step 3 — 验新 PAT 有写权限（真 push，不是只读）**

⚠️⚠️ **跑之前先做这个自检**（脚本内部是 `git push origin HEAD:dev`
⇒ **会把部署目录 HEAD 的所有未推 commit 一起推上 dev**）：

```bash
cd /Users/qichen/solobrave-prod
git status --porcelain                      # 应为空（工作区干净）
git log --oneline origin/dev..HEAD          # 应为空（无未推 commit）
```

> ★ **这两条任一不为空 ⇒ 先停下**，别跑 Step 3 ——
> 否则你的 PAT 验证会**顺手把别人的 commit 推进 dev**。
> （2026-10-26 实测当时是干净且同步的；但这是**时点状态**，换天就不一定。）

```bash
cd /Users/qichen/solobrave-prod && bash scripts/verify_github_pat.sh
```

> ✅ 断言：脚本末尾必须打印 **`✅ 推送成功 —— PAT 有效且有写权限`**，
> 紧接着一行是 `git log --oneline origin/dev -1`（说明真的推上去了）。
> ❌ 若打印 `❌ 推送失败`：按脚本给的 4 条排查顺序走（占位符未替换 / 仓库没勾选 /
> Contents 权限不是 Read and write / token 过期）。**不要**跳到 Step 4。
> ⚠️ 该脚本**跑完会删掉 `~/.solobrave_github_pat`**（用完即焚）⇒
> 后面要再比指纹的话，得重新写一次文件（见 Step 5）。

**Step 4 — 把新 PAT 写进 keychain（替换旧条目）**

⚠️ `-w '<token>'` 会把 token 写进 shell history ⇒ **用交互式提示符**：

```bash
# 4.1 交互输入（token 不进 history、不进 argv）
security add-internet-password -U -s github.com -a 1920977306 -w
#   ⇒ 会提示 "password data for new item:" ⇒ 粘贴 token 后回车（不回显）

# 4.2 验证条目在、且仓库能免密拉取
security find-internet-password -s github.com 2>&1 | grep -c "1920977306"   # 必须 ≥ 1
git -C /Users/qichen/solobrave-prod fetch origin   # 必须不要求输入用户名/密码
```

> `-U` = 有则更新、无则添加（**幂等**，重复执行不会报「已存在」）。
> ✅ 断言：`fetch origin` 无任何用户名/密码提示，直接成功。
> ❌ 若 `git fetch` 仍要求输入密码 ⇒ 说明 keychain 没写进去，先别撤旧 key。
> ⚠️ `-T ""` 可去掉「只有创建者 app 能免提示读」的默认限制 ——
> **不要加** `-T ""`（那会让任何 app 都能读，属于降低安全性）。

**Step 5 — 确认 keychain 里确实是新 PAT（指纹比对）**

```bash
# 5.1 重新写一次临时文件（Step 3 的脚本已把它删了）
umask 077 && printf '%s' '<粘贴新PAT>' > ~/.solobrave_github_pat

# 5.2 比指纹 —— 两行必须完全一样
security find-internet-password -s github.com -w | shasum | cut -c1-12
tr -d '\r\n' < ~/.solobrave_github_pat | shasum | cut -c1-12

# 5.3 清掉临时文件
shred -u ~/.solobrave_github_pat 2>/dev/null || rm -f ~/.solobrave_github_pat
```

> ✅ 断言：**两行输出必须完全一样**。
> ⚠️ 这步是**唯一能证明「keychain 里装的是新 PAT 而不是旧的」**的手段 ——
> 只做 `git fetch` 成功是不够的（旧的还在 keychain 里时 fetch 也会成功）。

**Step 6 — 撤销旧 PAT（GitHub 后台，老大手动）**

- 位置：GitHub → Settings → Developer settings → Personal access tokens →
  找到**旧的那个**（不是刚建的新 PAT）→ Delete / Revoke

> ✅ 断言：撤销后，`git fetch origin` **仍然成功**（说明新 PAT 接管了）。
> ⚠️ 如果撤销旧 PAT 后 fetch 失败 ⇒ **新 PAT 没生效**，回 Step 4 重写 keychain。

**Step 7 — 确认本地没有 PAT 残留**

```bash
cd /Users/qichen/solobrave-prod
grep -rn "ghp_\|github_pat_" ~/.gitconfig .git/config 2>/dev/null   # 必须无输出
ls -la ~/.solobrave_github_pat 2>&1 | grep -q "No such file" && echo "✅ 临时文件已清"
```

> ✅ 断言：两条都成立。
> ❌ 若 `.git/config` 里有 PAT 明文 ⇒ 立刻 `git config --unset remote.origin.url` 改成干净 URL。

---

## 2. 飞书 app secret 轮换（4 个 app）+ 顺带发现的 13 处明文 key（在 `~/.openclaw/`，**不在本文件里**）

### 现状（2026-10-26 实测）

**4 个飞书 app**（`~/.openclaw/openclaw.json` → `channels.feishu`）：

| 账号标签 | app_id | secret store 条目名 | 用途 |
|---|---|---|---|
| `default` | `cli_aafb5d7cd6389be4` | `FEISHU_APPSECRET_MAIN` | 貂蝉 / 主机器人 |
| `helen` | `cli_aaf9b98563b99bd7` | `FEISHU_APPSECRET_HELEN` | Helen |
| `t_default` | `cli_aa420db06a78dcc5` | `FEISHU_APPSECRET_T_DEFAULT` | 租户 Bot |
| `mumu` | `cli_aa51b1b62a7a9bf6` | `FEISHU_APPSECRET_MUMU` | 木木 |

**secret 存储位置（真源）**：
- `~/.openclaw/state/openclaw.sqlite` → 表 `secret_store_entries`
  （`scope_kind='team'`, `scope_id=''`, `kind='secret'`, 值长度均为 **32 字符**）
- ⚠️ **不要手改 sqlite** —— 有官方 CLI：`openclaw secrets store set`
- ⚠️ 这 4 条是 **team scope**，4 个 app 共用 team scope ⇒ 每个 app 的 secret 必须**分别**设置。

**另有一个独立副本**（容易漏！）：
- `data/solobrave.db` → 表 `tenant_feishu_config` → 1 行 `t_default`，
  `app_secret` 是 **明文 32 字符**（`status='routed'`, `bound_at=2026-10-09`）
  ⇒ **重置 `t_default` 的 secret 后，这张表也要同步更新**，否则两处不一致。
- `data/daren-bot/daren_bot.db` → 表 `bot_tenants` → 2 行
  （`daren_boss` / `daren_test2`），存的是 **AES-GCM 密文**（`app_secret_enc`，60 字符）
  ⇒ 这两个 app 的 app_id 与上面 4 个**都不同**（`cli_aa461b7cb7fa9cb7` / `cli_aa5b67fd7b799d29`），
  ⇒ **不在这次 4 个之内**。如果老大要一起轮换，需另走 `python3 -m daren_bot.main add-tenant` 流程。

**★ 轮换时顺带发现的 13 处明文 key（`openclaw secrets audit` 实测）**：

> ⚠️ **这 13 处明文在 `~/.openclaw/agents/*/agent/models.json` 里，**
> **本文件（runbook）本身不含任何明文密钥** ——
> 已用 5 类形态正则（`sk-` / `gh?_` / 32 位 hex / `Bearer` / 长 base64）独立复核：**全部 0 命中**。

```
plaintext=13, unresolved=0, shadowed=0, storeResidue=0, legacy=0
```

| 文件 | 明文字段 | 数量 |
|---|---|---|
| `~/.openclaw/agents/main/agent/models.json` | `providers.{openai-compatible,kimi-code,kimi_proxy_helen,kimi_proxy_shangguan,kimi_proxy_diaochan,kimi_proxy_kongming}.apiKey` | 6 |
| `~/.openclaw/agents/emp_1780199176680/agent/models.json` | 同上 6 个 + `providers.zhipu.apiKey` | 7 |

- 这 13 处是 **provider 的 apiKey 直接明文**，
  而对应的 `MODEL_APIKEY_*` 条目**已经存在在 secret store 里**（见下表）⇒
  **它们是 store 的残留副本**（store 里已有，可以改成引用）。
- ⚠️ `shadowed=0` ⇒ 现在 store 里的值和明文里的是**同一份**，不是冲突的两份。
  ⇒ 这也说明：**改了 store 里的 `MODEL_APIKEY_*`，这 13 处明文不会自动跟着变** ⇒
  **这才是「两份事实共用一个来源」的隐患**（对应 AGENTS.md §16 F）。
- ⚠️★ **2026-10-26 19:3x B 边补充**：两个 `models.json` 里实际有 **14 个 `apiKey` 字段**
  —— 上表的 **13 个真 API key** + **`main/codex`**（值形态 `codex…`，16 字符）
  ⇒ 那是 **Code 订阅的 OAuth token**，属 `AGENTS.md §7` 第 6 条说的
  **「第三种密钥形态：客户端自持的凭据」**（Code 桌面端自行续期并重写该文件）
  ⇒ **不要手动轮换它**，也不要把它算进「要去供应商后台准备的 key 数」。
  ⇒ 另：两个 agents 的 `kimi-code` 是**两把不同的 key**（指纹不同），不是同一把。

store 里已有的 `MODEL_APIKEY_*` 条目（轮换时按需改）：
`MODEL_APIKEY_{DEEPSEEK,KIMI,KIMI_PROXY_DIAOCHAN,KIMI_PROXY_HELEN,KIMI_PROXY_KONGMING,KIMI_PROXY_MUMU,KIMI_PROXY_SHANGGUAN,MINIMAX,ZHIPU}`
以及 `AUTHPROFILE_{KIMI_CODE,OLLAMA,OPENAI_COMPATIBLE}`、`GATEWAY_AUTH_TOKEN`。

### 步骤（一次只做一个 app）

**Step 1 — 在飞书后台重置 secret（老大手动）**

- 位置：飞书开放平台 → 对应应用的「凭证与基础信息」→ **App Secret** → 重置
- 逐个 app 做，**不要一次全重置**。

> ✅ 断言：后台显示新的 secret（32 字符），且**你自己已经复制下来**（只显示一次）。
> ❌ 若还没复制就去做下一个 app ⇒ 这个 secret 就永远拿不回来了。

**Step 2 — 写入 secret store（官方 CLI）**

⚠️ **secret 类条目没有 `--secret` 参数** ⇒ 必须走 `--value-file`（已用 `--dry-run` 实测确认）：

```bash
# 2.1 先写临时文件（权限 600，不进 history）
umask 077 && printf '%s' '<粘贴新secret>' > /tmp/_feishu_secret.txt

# 2.2 先 dry-run 验参数对不对（不写库）
openclaw secrets store set FEISHU_APPSECRET_MAIN --scope team --kind secret \
  --value-file /tmp/_feishu_secret.txt --dry-run
# ✅ 断言：输出 "Would write FEISHU_APPSECRET_MAIN (secret)." 且退出码 0

# 2.3 真写
openclaw secrets store set FEISHU_APPSECRET_MAIN --scope team --kind secret \
  --value-file /tmp/_feishu_secret.txt

# 2.4 用完即焚
shred -u /tmp/_feishu_secret.txt 2>/dev/null || rm -f /tmp/_feishu_secret.txt
```

> ✅ 断言：
> ```bash
> openclaw secrets store list | grep FEISHU_APPSECRET_MAIN   # 必须有这一行
> ```
> ⚠️ `secrets store get` 对 `kind='secret'` 是 **write-only**（读不出来）——
> 这是**设计如此**，不是 bug。⇒ **验证只能靠行为验证**（Step 4），不能靠读回。
> ⚠️ 4 个 app 要用**各自的名字**重复 Step 2：
> `FEISHU_APPSECRET_MAIN` / `FEISHU_APPSECRET_HELEN` / `FEISHU_APPSECRET_T_DEFAULT` / `FEISHU_APPSECRET_MUMU`。
> ⚠️ 这 4 条的 `scope_kind='team'` / `scope_id=''` ⇒ **CLI 必须带 `--scope team`**（默认值就是 team，但显式写更安全）。

**Step 3 — 让运行时重新解析 secret**

```bash
openclaw secrets reload --timeout 60000
```

> ✅ 断言：输出 **`Secrets reloaded with N warning(s).`**，退出码 0。
> ⚠️ **必须带 `--timeout 60000`** —— 实测 reload 要 **19~22 秒**，
> 用默认 30 秒偶尔够、但用短 timeout（如 8 秒）会报
> 「OpenClaw took too long to respond. Your request may have completed.」
> ⇒ **那不是失败**，是超时。**看到这句要去日志确认结果，不要盲目重跑**：
> ```bash
> grep "secrets.reload" /tmp/openclaw/openclaw-*.log | tail -3
> # 真成功会看到 "⇄ res ✓ secrets.reload 21650ms"
> ```
> ⚠️ **预期会有 1 条 warning**（既有的，与本次操作无关）：
> ```
> [SECRETS_REF_IGNORED_INACTIVE_SURFACE] channels.feishu.appSecret:
>   no enabled account inherits this top-level Feishu appSecret.
> ```
> ⇒ 含义：顶层 `channels.feishu.appSecret` **没有账号继承它**（每个 account 各自指定了
> `FEISHU_APPSECRET_*`）⇒ **顶层那条是死引用，不是你的 secret 没生效**。
> ⇒ 真要生效的是 `channels.feishu.accounts.{default,helen,t_default,mumu}.appSecret`。

**Step 4 — 行为验证（唯一可靠的验证）**

```bash
# 4.1 机器人还活着吗（⚠️ 端点各不同，别用 /health 一把梭）
cd /Users/qichen/solobrave-prod
curl -s -o /dev/null -w "server:%{http_code}\n" http://127.0.0.1:8080/api/health
curl -s -o /dev/null -w "bot:%{http_code}\n"   http://127.0.0.1:8092/healthz
curl -s -o /dev/null -w "worker:%{http_code}\n" http://127.0.0.1:8095/health
```

> ✅ 断言：三行都是 `200`。
> ❌ 若 5xx / 超时 ⇒ **立刻回滚**：把 Step 2 的 secret 改回旧值 → `openclaw secrets reload`。

```bash
# 4.2 真发一条消息（这一步才是真验证）
# 在飞书里给对应机器人发一句「密钥轮换自检」，看它是否正常回复
```

> ✅ 断言：**机器人正常回复**（不是「app not found / 401 / ticket 无效」）。
> ⚠️ 若机器人报 **凭据无效 / invalid app secret** ⇒ 立刻回滚，别继续做下一个 app。

**Step 5 — 同步 `data/solobrave.db` 的 `tenant_feishu_config`（只对 `t_default`）**

⚠️ **不要用 `sqlite3 "... UPDATE ... '<secret>'"`** —— secret 会进 shell history。

```bash
cd /Users/qichen/solobrave-prod
mkdir -p ~/.solobrave-backups
cp data/solobrave.db ~/.solobrave-backups/solobrave.db.bak-$(date +%Y%m%d-%H%M%S)-before-feishu-secret

umask 077 && printf '%s' '<粘贴新secret>' > /tmp/_feishu_secret.txt

# 用参数绑定，不拼进 SQL 文本
.venv-daren/bin/python - <<'PY'
import sqlite3, pathlib
secret = pathlib.Path('/tmp/_feishu_secret.txt').read_text().strip()
conn = sqlite3.connect('data/solobrave.db')
conn.execute("UPDATE tenant_feishu_config SET app_secret=?, updated_at=strftime('%s','now') WHERE tenant_id='t_default'",
             (secret,))
conn.commit()
conn.close()
print("updated")
PY

shred -u /tmp/_feishu_secret.txt 2>/dev/null || rm -f /tmp/_feishu_secret.txt
```

> ✅ 断言：
> ```bash
> sqlite3 data/solobrave.db "SELECT tenant_id, length(app_secret), status FROM tenant_feishu_config;"
> # 必须还是 t_default | 32 | routed
> ```
> ⚠️ 这张表是**明文存 secret**（本身就是个隐患，见 §5），但**改 schema 是另一个工程**，
> 现在先保证**两处一致**，别在轮换窗口里动 schema。
> ❌ 若 `length(app_secret)` 不是 32 ⇒ 粘贴时带了空格/换行，重贴。

**Step 6 — 四个 app 全部做完后，跑 §4 的全量复核**

---

## 3. ★ LLM / embedding / vision 侧的明文 key（`data/settings.json` 5 处 + `.env` + 与 §2 的副本关系）

> ★ **本节是 2026-10-26 19:3x（B 边）实测补上的** —— 原清单只覆盖了
> **GitHub PAT + 飞书 4 个 app secret + `~/.openclaw/` 那 13 处**，
> **漏了 `data/settings.json` 里 5 处明文 key** ⇒ **照原清单做完，磁盘上仍有明文**。
> ⚠️ **本节与 §2 的「13 处」是同一个病根：同一份凭据在多处留副本，改一处不会自动同步。**

### 3.1 现状（2026-10-26 19:3x 实测；**只出指纹 md5[:12]，不回显明文**）

**判据：一律按【字段名】判定**（`apiKey` / `*_API_KEY` / `token` / `secret`），
**不按值的形态猜** ⇒ ⚠️ 本轮实测两次栽在「按形态过滤」上
（先滤掉非 `sk-` 前缀的 kimi 代理 token、再滤掉非 hex 的 zhipu key）
⇒ **同 `AGENTS.md §16 T` 第 8 条：过滤器会把答案自己滤掉。**

**总账（实测）**：落盘明文凭据 **27 处条目 / 14 把不同凭据**
= **10 把上游 API key** + **4 把本地代理 token**（不去供应商后台轮换）

**★ 4 把「多副本」的上游 key —— 漏改一处 ⇒ 旧 key 仍在被调用 ⇒ 撤不掉**：

| 指纹 | 副本数 | 副本位置 | 实际生效吗 |
|---|---|---|---|
| `67e51cc86d32` | **3** | `.env SOLOBRAVE_EMBEDDING_API_KEY` + `settings.json .visionQA.apiKey` + `models.json emp_…/zhipu.apiKey` | ✅ **= 老大说的「语义用智谱」那把**（`.env` 的 override 是 `provider=zhipu` / `baseUrl=open.bigmodel.cn` / `model=embedding-3`）|
| `aade0ff2e504` | **3** | `.env MINIMAX_CP_API_KEY` + `.env OPENCLAW_MINIMAX_API_KEY`（**两个变量名同值**）+ `settings.json .llm.providers[0].apiKey` | ✅ Minimax CP |
| `65b936186621` | **3** | `.env KIMI_API_KEY` **第 4 段** + `settings.json .llm.providers[1].apiKey` + `settings.json .vision.apiKey` | ✅ **同时服务 LLM 与 vision 两条链路** ⇒ **两条都要验** |
| `dfd27cfdd97f` | **3** | `models.json emp_…/openai-compatible.apiKey` + `models.json main/openai-compatible.apiKey` + `settings.json .embedding.apiKey` | ⚠️ **硅基流动**那把（`settings.json` 的 `embedding` 段 provider=`siliconflow`）⇒ **当前被 env override 覆盖、不生效**，**但仍是明文落盘 ⇒ 轮换要一起处理** |

**单副本的上游 key（6 把，各 1 处）**：`.env` 的 `KIMI_API_KEY` 第 1/2/3 段
（`4e02915848a8` / `e46e3708d354` / `cfd73ae529eb`）、`.env MINIMAX_API_KEY`（`d3b46fa4d835`）、
`models.json emp_…/kimi-code`（`7e5d28ed4f89`）、`models.json main/kimi-code`（`721be7956009`）。

**4 把本地代理 token（不去供应商后台轮换）**：`kimi_proxy_{diaochan,helen,kongming,shangguan}`，
各 2 副本（两个 agents 同名同值）。

⚠️ **两处数量更正（相对 §2 与交接单）**：
- `data/settings.json` 里是 **5 处**（不是 4）：`.embedding.apiKey` / `.llm.providers[0].apiKey` /
  `.llm.providers[1].apiKey` / `.vision.apiKey` / **`.visionQA.apiKey`**。
- `~/.openclaw/agents/*/agent/models.json` 里是 **14 个 `apiKey` 字段** = **13 个真 API key**
  + **1 个 `main/codex`（Code 订阅的 OAuth token，客户端自持会自行续期 ⇒ 不要手动轮换它）**
  ⇒ **§2 的「13 处」是对的**（审计工具数的是真 API key），**不是漏算**。

⚠️⚠️ **`.env` 的 `SOLOBRAVE_EMBEDDING_*` 四个 override 变量优先级最高**
（`get_embedding_config()`：**环境变量 > settings.json**）⇒
**只改 `settings.json` 的 embedding 段可能不生效** ⇒ **真源在 `.env`**。
⇒ ⇒ 顺带解释了一个历史现象：`settings.json` 的 `embedding` 段还停在
`siliconflow` + `BAAI/bge-large-zh-v1.5`，而 `.env` 已经是 `zhipu` + `embedding-3`
⇒ **老大换过供应商，但旧的那份没清 ⇒ 它就是「旧值仍在盘上」的实例。**

### 3.2 ★ 要不要重启 8080（**两处规则不同**）

| 改哪里 | 要重启吗 | 原因（实测自代码）|
|---|---|---|
| `.env` | **必须重启** | ① `_kimi_keys_from_config()` 在**模块导入时**执行一次（模块级 `_env_kimi_keys = …`）⇒ **key 池是启动时构建的**；② `_get_env_override()` 读的是 `os.environ` ⇒ **改 `.env` 不重启就不生效**（它 docstring 里的「实时」是相对模块常量而言，不是相对文件）|
| `data/settings.json` | **不用重启** | `_read_json(SETTINGS_FILE, …)` **每次调用实时读盘**（embedding / llm providers / vision / visionQA 都走它）|
| `~/.openclaw/…/models.json` | 按 **§2 Step 3**（优先 `secrets reload`，别急着重启 gateway）| — |

⇒ ⇒ **`.env` 改了要重启、`settings.json` 改了不用 ⇒ 两处都改 ⇒ 重启一次。**

### 3.3 步骤（**一次只做一把**，顺序遵循开头的全局铁律）

**Step 1 — 先只读地确认副本关系没变（只出指纹，不出明文）**

```bash
cd /Users/qichen/solobrave-prod && python3 - <<'PY'
import hashlib, json, os, glob
fp = lambda s: hashlib.md5(s.strip().encode()).hexdigest()[:12]
d = json.load(open('data/settings.json', encoding='utf-8'))
print('embedding      ', fp(d['embedding']['apiKey']), '(provider=%s)' % d['embedding'].get('provider'))
print('llm.providers0 ', fp(d['llm']['providers'][0]['apiKey']))
print('llm.providers1 ', fp(d['llm']['providers'][1]['apiKey']),
      '(name=%s)' % d['llm']['providers'][1].get('name'))
print('vision         ', fp(d['vision']['apiKey']))
print('visionQA       ', fp(d['visionQA']['apiKey']))
for f in sorted(glob.glob(os.path.expanduser('~/.openclaw/agents/*/agent/models.json'))):
    tag = os.path.basename(os.path.dirname(os.path.dirname(f)))
    for n, v in sorted(((json.load(open(f, encoding='utf-8')).get('providers') or {})).items()):
        if isinstance(v, dict) and isinstance(v.get('apiKey'), str) and v['apiKey'].strip():
            print(f'  {tag}:{n:20s}', fp(v['apiKey']))
PY
```

> ✅ 断言（两条都对才继续）：
> ① `llm.providers1` 与 `vision` **指纹相同**；
> ② `visionQA` 与 `.env` 的 `SOLOBRAVE_EMBEDDING_API_KEY` **指纹相同**。
> ❌ **指纹对不上 ⇒ 停下来，先查清楚，别写。**

**Step 2 — 生成新的**（铁律同 §2：不进仓库 / 不进 history / 不进 git config）

**Step 3 — 写入（副本表里几处就写几处，一处都不能漏）**

- `data/settings.json` ⇒ **用「临时文件 + `os.replace`」原子替换**（与服务端 `_write_json` 同款，
  **别原地改**，读者可能读到半截）；**只改对应的 `apiKey` 字段，其它字段一个都不动**。
- `.env` ⇒ 按副本表改对应变量（`KIMI_API_KEY` 只改第 4 段，**其它 3 段一个字节都不动**）。
- `~/.openclaw/agents/*/agent/models.json` ⇒ 按 **§2** 的 13 处一起处理。

**Step 4 — 重启 8080**（因为动了 `.env`）

```bash
launchctl kickstart -k gui/$(id -u)/com.solobrave.daren-bot
```

> ⚠️ 若这一轮**只**改了 `data/settings.json`、**没碰** `.env` ⇒ **不用重启**。

**Step 5 — 验（这把同时服务 LLM 与 vision ⇒ 两条链路都要验）**

- LLM 链路：跑 **§4** 的降级链复核（能顶的独立服务商 ≥ 1）。
- vision 链路：跑一次图片分析。
- ⚠️ **验的是「新 key 生效」，不是「上游有额度」** ⇒ 这两件事**分开判**：
  ⚠️⚠️ **这 4 把 kimi 的月度额度已用尽**（2026-10-26 19:2x **裸调**实测：4 把**全部** 429
  `monthly usage limit` ⇒ **上游真死，不是本地熔断假象** ⇒ **等 1800s 熔断窗口没用**）
  ⇒ ⇒ **换新 key 后若仍 429，说明新 key 也没额度，不是操作失败。**

---

## 4. 全量复核（@贾维斯 可自动执行，也可老大手动跑）

```bash
cd /Users/qichen/solobrave-prod
# 服务全绿 —— ⚠️ 各服务健康端点不同（2026-10-26 实测），别用 /health 一把梭
curl -s -o /dev/null -w "server(8080):%{http_code}\n" http://127.0.0.1:8080/api/health
curl -s -o /dev/null -w "bot(8092):%{http_code}\n"   http://127.0.0.1:8092/healthz
curl -s -o /dev/null -w "worker(8095):%{http_code}\n" http://127.0.0.1:8095/health
curl -s -o /dev/null -w "cockpit(7188):%{http_code}\n" http://127.0.0.1:7188/

# 降级链还活着（文字链应 ≥ 3 环、能顶的独立服务商 ≥ 1）
.venv-daren/bin/python -c "
import sys; sys.path.insert(0,'.')
from daren_bot import llm_channels as lc
dead={lc.POOL_KIMI_MONTHLY, lc.POOL_MINIMAX_OPEN}
print('环数:', len(lc.text_rings()))
print('能顶的独立服务商:', {r['vendor'] for r in lc.text_rings() if r['pool'] not in dead})"

# 全量测试
.venv-daren/bin/python -m pytest -q --no-header -p no:cacheprovider 2>&1 | tail -3
```

> ✅ 断言：
> - 4 行健康检查全是 `200`（**注意 8080 是 `/api/health`、8092 是 `/healthz`**，
>   用 `/health` 会得 404 —— 那是路径错，不是服务挂了）
> - 环数 ≥ 3、能顶的独立服务商 ≥ 1
>   （**2026-10-26 基线：环数 6、能顶 = {minimax}**。
>   ⇒ 轮换后若「能顶」变成 0 ⇒ **那家 key 填错了，立刻回滚**，别继续做下一个）
> - pytest **`1064 passed / 6 skipped / 0 failed`**（**2026-10-26 18:00 当场实测**）
>
> ⚠️ **基线会随开发前进，别拿旧数字当判据**：
>   这个数字改一次就过期一次 ⇒ 轮换时若 passed 数不同，**先看是不是「新增了测试」**
>   （更常见）而不是「代码坏了」。★ 判断方法：`git log --oneline <上次基线>..HEAD -- tests/`
>   —— 有新测试 commit ⇒ 数字涨了是正常的。
>
> ⚠️ **6 条 skip 是正常的（不是「没跑」也不是「坏了」）**，实测明细：
> | 数量 | 来源 | 原因 |
> |---|---|---|
> | 2 | `tests/test_mt_p0_tenant_leak.py` | live 双租户沙箱（需 `MT_P0_LIVE=1` + 沙箱在跑）|
> | 1 | `tests/test_mt_p1_ms3_routing.py` | live 沙箱（需 `MT_P0_LIVE=1`）|
> | 1 | `tests/test_reliability_full_suite.py` | live 派单验收（要打 prod 8080 + 真实计费调用，**属上线前人工验收，不属回归**）|
> | 2 | `tests/test_script_suites.py` | live 门禁：`test_kimi_response.py`（真打 Kimi 上游）+ `test_proxy_e2e.py`（要真 server + 登录态）|
>
> ⇒ **看到 6 条 skip 不用慌**，那是「需要真环境」的显式登记（不是隐形死代码）。
> ⇒ 若 skip 数**变多** ⇒ 有测试被意外 skip 了，要查；**变少** ⇒ 有 live 门禁失效了，也要查。
>
> ⚠️ 「HTTP 200 要看响应体」—— 光看状态码不够：
> ```bash
> curl -s http://127.0.0.1:8095/health | head -c 300
> ```
> 要能看到 `browser_up` / `daren_browser_up` / `logged_in` 等字段。

---

## 5. 轮换完之后（可选后续，不在本次窗口做）

1. **`tenant_feishu_config.app_secret` 改加密存储** —— 现在是明文，
   对齐 `daren_bot` 的 `app_secret_enc` 做法（AES-GCM + 主密钥）。
2. **13 处明文 `models.json` apiKey 改成 SecretRef 引用** ——
   `openclaw secrets store` 里已有对应的 `MODEL_APIKEY_*` ⇒ 改成引用即可，
   之后**轮换 store 一处就够**，不再有第二份事实（AGENTS.md §16 F）。
3. **`GATEWAY_AUTH_TOKEN` 也轮换** —— 它同时被前端 JS 硬编码（AGENTS.md §7 已知项 P1），
   正确修法是改走后端代理、服务端持 token。

---

## 6. 回滚手册（任何一步失败，按这个来）

| 症状 | 立即动作 |
|---|---|
| 飞书机器人报凭据无效 | 把 store 里那条 secret 改回**旧值**（Step 2 的 `--value-file` 流程）→ `openclaw secrets reload --timeout 60000` → 验证恢复 |
| `git push` 失败 | **不要**撤销旧 PAT，先查 Step 3 的 4 条排查项 |
| 撤销旧 PAT 后 fetch 失败 | 重写 keychain（Step 4）→ `security delete-internet-password -s github.com` 再 `add` |
| 服务 5xx | `launchctl kickstart -k gui/$(id -u)/com.solobrave.<服务名>` 重启对应服务 |
| `reload` 报「took too long」 | **不是失败**。查日志 `grep "secrets.reload" /tmp/openclaw/openclaw-*.log \| tail -3`，看到 `res ✓` 就是成功 |
| `reload` 真的失败 / gateway 挂了 | 兜底重启 gateway：`launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway`，等 10 秒后 `openclaw gateway status` 必须显示 `Runtime: running` + `Connectivity check: ok` |

**回滚永远是「换回旧值」，不是「继续往前修」** —— 先恢复可用，再查原因。

### ⚠️ 关于重启 gateway 的代价（2026-10-26 实测）

- gateway = LaunchAgent `ai.openclaw.gateway`（pid 11665，端口 18789，loopback-only）
- 重启它会**短暂中断所有 openclaw agent 的飞书通道**（日志实测：
  `Plugin feishu forced retirement after 5000ms`）
- ⇒ **优先用 `secrets reload`（原子换快照，不断连接）**；
  只在 reload 真的失败时才重启 gateway。
- ⇒ 重启后必须确认：`openclaw gateway status` 显示 `Runtime: running` +
  `Connectivity check: ok`，且 `Capability` 不是 `read-only`（读不到就说明 CLI 连不上写通道）。
