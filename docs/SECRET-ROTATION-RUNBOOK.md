# 密钥轮换操作清单（老大手动执行，@贾维斯 自动复核）

> 生成时间：2026-10-26 17:15
> 事实来源：全部为**当场实测**（`openclaw secrets audit` / `sqlite3` / `git config`），
> 不是引用旧报告。执行前请确认现状仍与「清单里的现状」一致。
> ★ **2026-10-28 修订（A 边，按 B 边 §89.7 + 交接单 §98）**：§2 映射表 / Step 0.5 表 / Step 1 的**归属判据**统一降级为「**命中 ⇒ 归属确定；搜不到 ⇒ 只说明「不在当前租户」**」⇒ **定位一律走「切到该账号 → 看应用列表 → 核对 App ID」**，不再把「搜不到」写成「属第 4 个账号」。

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
6. ★★★ **凭据明文绝不进对话**（2026-10-26 23:1x 实测 ⇒ 交接单 §37.6）：
   本机 runtime 会把**对话内容与后台任务输出**自动写进
   `~/.minimax/v2/sqlite/runtime-state.sqlite`（**1.8 GB，活的**）
   ⇒ ⇒ **任何在对话里出现过的凭据，明文都会自动沉淀在这个库里**
   （实测现役 PAT 就在 `local_runtime_message_rows.data_json` /
   `local_runtime_background_tasks.record_json` / `local_runtime_session_clio_state.state_json` 三张表里）
   ⇒ ⇒ ⇒ **在终端跑命令时，只把「指纹」贴回对话，绝不贴明文**
   ⇒ ⇒ ★ 连带一条：`security find-internet-password -g` 会把密码打到 **stderr**
   ⇒ **stderr 会被后台任务捕获并写进日志** ⇒ ⇒ **只用 `-w`，且不要把它的输出复制进对话**。
7. ★ **作废 > 擦除**：明文残留的风险 = 凭据**仍然有效**时的风险
   ⇒ 后台**撤销**那一刻，所有副本同时降级为无害历史残留
   ⇒ ⇒ **有些副本根本擦不掉**（云盘已同步 / Spotlight 索引 / runtime 的 sqlite / 快照）
   ⇒ ⇒ **先作废，再谈清理**（§16 X 第 6 条）。

---

## 1. GitHub PAT 轮换

### 现状（2026-10-26 实测）

| 项 | 值 |
|---|---|
| 仓库 | `github.com/1920977306/solobrave.git` |
| 账号 | `1920977306` |
| 凭据来源 | `credential.helper = osxkeychain`（⚠️ **在 git 自带的系统配置里**，见下方注）|
| keychain 条目 | `security find-internet-password -s github.com` → `acct = 1920977306` |
| `~/.solobrave_github_pat` | **不存在**（一次性验证用的临时文件，用完即焚）|
| 已有的验证脚本 | `scripts/verify_github_pat.sh`（只读 PAT → 真 push → 删文件）|

> ★★ **查 `credential.helper` 的坑**（2026-10-27 A 边实测；B 边先踩到）：
> `git config --global --get-all credential.helper` ⇒ **空**（退出码 1）
> `git config --system  --get-all credential.helper` ⇒ **也空**！
> ⇒ 真值在 **git 自带的系统配置**里：
> ```bash
> git config --show-origin --get-all credential.helper
> # ⇒ file:/Library/Developer/CommandLineTools/usr/share/git-core/gitconfig  osxkeychain
> ```
> ⇒ ⇒ **必须用 `--show-origin --get-all`**（一次列出**所有来源**）。
> ⇒ ⇒ ★ **为什么必须写进 runbook**：Step 4 是**老大手动**的 ⇒ 若按 `--global`/`--system` 查 ⇒ 两处都空
> ⇒ ⇒ **会误判成「git 不走 keychain ⇒ 覆写 keychain 白做」⇒ 直接放弃一个正确的步骤**。
> ⇒ ★ 通用纪律：**查「配置从哪来」⇒ 用 `--show-origin --get-all` ⇒ 别用单来源的 `--global`/`--system`**。

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
>
> ⚠️⚠️ **用完必须真删，不要「拖进废纸篓」（2026-10-26 22:0x 实测）** ——
> **废纸篓是「可恢复删除」⇒ 明文仍常驻磁盘** ⇒ 实测老大 20:1x 这三份
> （`~/.Trash/.solobrave_github_pat` ×3，mtime 20:12/20:12/20:15）
> **md5 与 keychain 现役那把完全同值** ⇒ ★ **现役 PAT 的明文还在废纸篓里**。
> ⇒ ⇒ **而且它们以 `.` 开头 = 隐藏文件** ⇒ **macOS Finder 默认不显示** ⇒
> **老大在废纸篓里根本看不见它们** ⇒ **清完必须按名字核对**：
> ```bash
> find ~/.Trash -maxdepth 1 -name '.solobrave_github_pat*' -print   # 必须无输出
> ```
> ⇒ ⇒ **首选：Step 2 一开始就用 `mktemp -d` 私有目录 + `trap` 兜底**
> （飞书那套脚本 `~/.solobrave-secrets/rotate_feishu_secret.sh` 已是这个做法）。

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

**Step 3.1 — ⚠️ 「no-op push」**不等于**「有写权限」（2026-10-26 20:1x 实测）**

`scripts/verify_github_pat.sh` 内部是 `git push origin HEAD:dev`；
若部署目录 `HEAD == origin/dev` ⇒ 实跑结果是 **`Everything up-to-date` 的 no-op**
⇒ ★ **那只证明了「能认证」，没证明「能写」**（no-op push 不与服务器协商写权限）
⇒ 而脚本照样打印「✅ 推送成功」⇒ **断言本身也会给假警报**。

⇒ **真正的写权限验证 = 创建一个 ref**（创建分支是**真写**操作）：

```bash
cd /Users/qichen/solobrave-prod
PROBE="_pat-write-probe-$(date +%s)"
git push origin HEAD:refs/heads/$PROBE     # 真写
git ls-remote --heads origin $PROBE        # 确认它真在远端
git push origin --delete $PROBE            # 用完即删
git ls-remote --heads origin $PROBE        # 必须为空（无残留）
```

> ✅ 断言：第 1 条输出含 **`* [new branch]`**（**不是** `Everything up-to-date`）⇒ **写权限确认**；
> 第 3 条输出含 **`* [deleted]`**；第 4 条**无输出** ⇒ 远端无残留。
> ⚠️ 探针名用 `_` 前缀 + 时间戳 ⇒ 不会撞上真业务分支；**测完立刻删**。
> ❌ 若第 1 条输出 `Everything up-to-date` ⇒ **没测到**（说明推的是已存在的 ref）⇒ 换探针名重来。
> ★ **2026-10-26 20:1x A 边实测**：输出 `* [new branch]` ⇒ 删除后远端残留 **0 条** ✅

**Step 4 — 把新 PAT 写进 keychain（替换旧条目）**

⚠️ `-w '<token>'` 会把 token 写进 shell history ⇒ **用交互式提示符**：

```bash
# 4.1 交互输入（token 不进 history、不进 argv）
security add-internet-password -U -s github.com -a 1920977306 -w
#   ⇒ 会提示 "password data for new item:" ⇒ 粘贴 token 后回车（不回显）

# 4.2 验证条目在、且仓库能免密拉取
security find-internet-password -s github.com 2>&1 | grep -c "1920977306"   # 必须 ≥ 1
git -C /Users/qichen/solobrave-prod fetch origin   # 必须不要求输入用户名/密码

# 4.3 ★ 确认「git 到底走不走 keychain」—— 必须用 --show-origin --get-all
#        （--global / --system 都返回空 ⇒ 会误判成「不走 keychain ⇒ 白做」）
git config --show-origin --get-all credential.helper
# ⇒ 期望含 osxkeychain（来源 file:/Library/Developer/CommandLineTools/.../gitconfig）
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
# ⚠️ 2026-10-26 20:1x B 边实测修正：**两边的字节口径必须一致**，否则断言本身会给出假警报。
#    `security … -w` 输出的密码**带一个尾随换行**，而 `tr -d '\r\n' < file` 没有
#    ⇒ 原写法算出来两个指纹**永远不一样**（实测 15e30df754ea vs f9d0b20a13f2）
#    ⇒ **看起来像「装错了 PAT」，其实是口径不同** ⇒ **两边都去掉 \r\n 再算**。
security find-internet-password -s github.com -w | tr -d '\r\n' | shasum | cut -c1-12
tr -d '\r\n' < ~/.solobrave_github_pat | shasum | cut -c1-12

# 5.3 清掉临时文件
#    ★ 2026-10-27 A 边修正（原写法有两个坑，叠起来正好违反本文件自己的 §16 X）：
#      ① macOS 默认**没有 shred** ⇒ `shred -u` 直接失败 ⇒ 退到 `rm -f`
#      ② PATH 里的 `rm` 在 agent 环境下是**可恢复删除 shim**（→ 废纸篓）
#         ⇒ 而这个文件里装的是**现役的新 PAT** ⇒ 明文会**继续常驻磁盘**
#         ⇒ 与 AGENTS.md §16 X「可恢复删除 ≠ 删除 ⇒ 明文仍常驻磁盘」自相矛盾
#    ⇒ 故：**显式绝对路径真删**（该文件是一次性副本，新 PAT 此时已在 keychain 里）
/bin/rm -f ~/.solobrave_github_pat
#    ⇒ 若老大坚持留可恢复副本：/Users/qichen/.minimax/bin/mavis-trash -- "$HOME/.solobrave_github_pat"
#       ⚠️ 但要知道明文会因此常驻磁盘 ⇒ **现役 PAT 不建议走这条**
```

> ✅ 断言：**两行输出必须完全一样**。
> ⚠️ 这步是**唯一能证明「keychain 里装的是新 PAT 而不是旧的」**的手段 ——
> 只做 `git fetch` 成功是不够的（旧的还在 keychain 里时 fetch 也会成功）。
> ⚠️⚠️ **别用 `security find-internet-password … -g` 来「看属性」** ——
> 它会把**密码本体打到 stderr**（不是 stdout！）⇒ **明文直接进终端记录 / 日志 / 会话快照**。
> ⇒ 要看属性就只用 `-w` 并**立刻接管道**（`… -w | tr -d '\r\n' | shasum | cut -c1-12`），
> **别让裸密码单独出现在任何一行输出里**。

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

**4 个飞书 app**（`~/.openclaw/openclaw.json` → `channels.feishu.accounts`）：

| openclaw 账号标签 | app_id | secret store 条目名 | openclaw 侧 name（**不是飞书后台应用名**） |
|---|---|---|---|
| `default` | `cli_aafb5d7cd6389be4` | `FEISHU_APPSECRET_MAIN` | 貂蝉 |
| `helen` | `cli_aaf9b98563b99bd7` | `FEISHU_APPSECRET_HELEN` | Helen |
| `t_default` | `cli_aa420db06a78dcc5` | `FEISHU_APPSECRET_T_DEFAULT` | 租户Bot |
| `mumu` | `cli_aa51b1b62a7a9bf6` | `FEISHU_APPSECRET_MUMU` | 木木 |

⚠️⚠️ **最后一列不是飞书后台的应用名**（2026-10-26 21:0x 实测更正）
—— `name` 是 openclaw 配置里给该账号自定的**显示名**，与后台「应用名称」无关。
实测：`cli_aa420db06a78dcc5` 在后台叫「**貂蝉**」，
而 openclaw 里它的 `name` 写着「租户Bot」、账号标签是 `t_default`。
⇒ ★ **按 `name` 去后台找应用 = 找错应用 = 重置错 app secret = 机器人掉线**。
⇒ **定位应用唯一可靠的依据 = `app_id` 本身**
（后台「凭证与基础信息」页顶部「应用凭证 → App ID」）。

⚠️⚠️⚠️ **同名不同物（2026-10-27 01:2x 复核补上）** ——「貂蝉」这**两个字在两个命名空间里指两个不同的 app**：

| 命名空间 | 「貂蝉」指谁 | app_id |
|---|---|---|
| **飞书后台**（应用名，老大看到的）| `t_default` 这个 app | `cli_aa420db06a78dcc5` |
| **openclaw 配置**（`accounts.default.name`，本地别名）| `default` 这个账号 | `cli_aafb5d7cd6389be4` |

⇒ ⇒ **两个命名空间各有一个「貂蝉」** ⇒ ★ **绝不拿 `openclaw.json` 里的 `name` 去后台找应用**
（那只是本地别名）⇒ **只认 `app_id`**。
⇒ ⚠️ 本表**第一列是 openclaw 账号标签**、**第四列是 openclaw 侧 `name`**，两者**都不是**后台应用名。

**★ 实测（2026-10-26 21:0x → 21:4x，三轮迭代）：应用分属「不同飞书账号」，不是「同一账号的不同企业」**

⚠️⚠️ **术语更正（21:0x 我自己写错过，21:4x 坐实）**：本节最初写「另一个飞书**企业**」
⇒ **实测是「另一个飞书账号」** —— 个人版没有「企业 / 组织」概念，
开发者后台右上角是「个人中心 → **切换账号**」⇒ 菜单里列的是 **3 个已登录身份**：
**豆包账号**（团队/企业，面板提示「豆包账号提示」）/ `+86190…76`（飞书个人用户）/ `+86157…92`（飞书个人用户）。
⇒ ⇒ **「换企业」会把人带到错的地方找 ⇒ 正确说法是「换账号」。**

⇒ ⇒ ★★★ **「3 个已登录身份」对照表（2026-10-27 16:2x–16:3x B 边实测，★ 别把名字搞混）**：

| 子菜单里显示的名字 | 类型 | 切过去后面板的昵称 | 应用列表「所有者」列 | 该身份下的应用（实测全量）|
|---|---|---|---|---|
| **豆包账号** | 团队/企业 | **悦己** | **悦己** | 悦己的智能助手（1 个）|
| **+86190\*\*\*\*\*\*76** | 飞书个人用户 | **Ayn** | **Ayn** | ayn小助理 / Ayn的智能助手（2 个）|
| **+86157\*\*\*\*\*\*92** | 飞书个人用户 | **tuz** | **tuz** | 达人1 / 貂蝉 / 小路 / 贾维斯 / 全可传媒小翊的智能助手 / 妍妍 / 露露（7 个）|

⇒ ⇒ ★★ **两条坑（实测踩过）**：
- ⚠️ **「昵称 / 手机号 / 所有者」是三套不同标签** ⇒ **`tuz` 就是 `+86157…92`（同一个身份）**；**豆包账号是独立的一条，不是 `+86190…76`**。
- ⚠️ **子菜单顺序会变**（**当前账号永远排第 1**）⇒ **认人靠头像/文字，不靠「第 N 条」** ⇒ 切完必须看「所有者」列变了没。

**★ 已定位的映射（★ 2026-10-27 14:2x 升级为「API 实测真名」——B 边实测；原表这 2 行写的是「❓未定位」，原文见交接单 §80.14.1 第九项）**：

| 来源 | app_id | **飞书后台应用名（API 实测）** | 在哪个账号下 |
|---|---|---|---|
| `t_default`（`FEISHU_APPSECRET_T_DEFAULT`）| `cli_aa420db06a78dcc5` | **貂蝉** | **tuz 账号**（= `+86157…92`，所有者 tuz）|
| `helen`（`FEISHU_APPSECRET_HELEN`）| `cli_aaf9b98563b99bd7` | **Ayn的智能助手** | **Ayn 账号**（= `+86190…76`，所有者 Ayn）|
| `default`（`FEISHU_APPSECRET_MAIN`）| `cli_aafb5d7cd6389be4` | **貂蝉** ⚠️⚠️ | ⚠️ **归属未确证**（app_id / 应用名两路检索在 3 个已登录身份下均未召回 ⇒ **疑在第 4 个身份** ⇒ 须在它的**应用列表**里按 App ID 核对）|
| `mumu`（`FEISHU_APPSECRET_MUMU`）| `cli_aa51b1b62a7a9bf6` | **木木** | ⚠️ **同上（归属未确证；疑在第 4 个身份）**|
| `daren_bot.db` 租户 `daren_boss` | `cli_aa461b7cb7fa9cb7` | **达人1** | tuz 账号 |
| `daren_bot.db` 租户 `daren_test2` | `cli_aa5b67fd7b799d29` | **ayn小助理** | Ayn 账号 |

⇒ ⇒ ★★★ **「貂蝉」在飞书后台对应的是【两个不同的 app】**：`cli_aa420db06a78dcc5`（= `t_default`，tuz 账号）与 `cli_aafb5d7cd6389be4`（= `default`，另一个账号）
⇒ ⇒ ⇒ **按名字搜「貂蝉」会同时命中两个** ⇒ ⇒ **必须粘 app_id 二次确认**（后台「凭证与基础信息」页顶部「应用凭证 → App ID」）⇒ ⇒ **认错 = 重置错 app 的 secret**。
⇒ ⇒ ⇒ ★★ 这是**「同名不同物」在飞书后台里的实体版本** ⇒ ⇒ 连「按后台应用名找」都不再安全 ⇒ ⇒ **app_id 是唯一可靠依据**。

⇒ ⇒ ★★★ **「木木」= `cli_aa51b1b62a7a9bf6`（API 实测）** ⇒ ⇒ ★★ **Step 1 的定位方式已改（2026-10-28 A 边按 B 边 §89.7 更正）**：**不再靠「搜索」定位** ⇒ ⇒ **切到该账号 → 看它的「企业自建应用」列表 → 点进去核对 App ID** ⇒ ⇒ ⚠️⚠️ **它的归属仍未确证**（★ 16:2x–16:3x 实测：豆包账号 / `+86190…76`（Ayn）/ `+86157…92`（tuz）**三个身份搜该 app_id 全部「暂无搜索结果」** ⇒ 但「搜不到」**只说明「不在当前租户」**，**推不出「在第 4 个身份」** ⇒ 疑在第 4 个身份）⇒ ⇒ **须切到第 4 个身份（子菜单最后一行「登录更多账号」）**，进去后**在应用列表里按 App ID 核对** ⇒ ⇒ **别把「搜不到」当成「应用不存在」**。
⇒ ⇒ ⇒ ★ **（★ 14:2x 我自己写的「要切到豆包账号 / `+86190…76` 账号」已被 16:2x–16:3x 的 12 格实测推翻 —— 那时我只测了 Ayn 一个身份就外推了；且 `+86190…76` 是 **Ayn** 不是豆包账号）** ⇒ ⇒ ★ **教训：把「没在某一处找到」说成「在别处」＝ 用推测冒充实测**（`AGENTS.md` §「先核实前提」）。

⇒ ⇒ ★ **这 4 个真名是怎么测出来的（可复现，且不用碰浏览器）**：
```bash
# 与轮换脚本验证 secret 用的是同一条接口：先换 tenant_access_token，再问飞书「这个 app 叫什么」
#   POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal  {app_id, app_secret}
#   GET  https://open.feishu.cn/open-apis/bot/v3/info   Authorization: Bearer <tenant_access_token>
#   ⇒ 响应里 bot.app_name = 后台应用名
# ★ 方法可信度自证：t_default → 「貂蝉」、helen → 「Ayn的智能助手」＝ 与本节既有实测记录逐字一致
```
⇒ ⇒ ⇒ ⚠️ **不打印 secret / token**：只取 `app_name`；secret 从 store 复制到临时目录后只读（不碰生产库、不回显明文）。

⇒ ★ **4 个待轮换 app 里，2 个已定位（`t_default` → tuz 账号 / `helen` → Ayn 账号）**；
另 2 个（`default` / `mumu`）在 **3 个已登录身份（豆包账号 / `+86190…76`（Ayn）/ `+86157…92`（tuz））下均搜不到**
⇒ ⇒ **⇒ 归属未确证（疑在第 4 个身份，未登录）** ⇒ ⇒ ★ **老大要轮换木木，第 1 步就是登录第 4 个身份**（子菜单最后一行「登录更多账号」）⇒ 进去后**在它的「企业自建应用」列表里找到「木木」→ 点进去核对 App ID 是否 `cli_aa51b1b62a7a9bf6`**（★ **别只靠搜索命中** ⇒ 检索只覆盖当前租户 ⇒ 交接单 §98）。
⇒ ⇒ ★ **（★ 14:2x 的原文写「在第三个账号（豆包账号 / `+86190…76`）下」—— 已被 16:2x–16:3x 的 12 格实测推翻：豆包账号与 `+86190…76` 下都搜不到，且 `+86190…76` 是 Ayn 不是豆包账号）**

**★ 定位方法（★ 2026-10-28 A 边实测后降级：搜索只能当「本租户内的辅助」，不能当定位手段）**：
开发者后台应用列表上方有个输入框，placeholder = **「搜索应用名称或 App ID」**
⇒ ★★ **检索的作用域 = 当前租户**（A 边 2026-10-28 11:1x 实测；**身份用「无筛选的应用列表内容」锚定**：Ayn 租户 = 2 个 app / tuz = 7 个 / 豆包账号 = 1 个）⇒ **命中** ⇒ 该 app **就在当前租户** ⇒ 读结果行的「所有者」列 ⇒ 归属确定 ✔　｜　**「暂无搜索结果」** ⇒ **只说明「不在当前租户」** ⇒ **推不出「不存在」，更推不出「在第几个身份」** ⚠️
⇒ ⇒ ★★ **可靠定位法（唯一推荐）**：**切到该账号 → 看它的「企业自建应用」列表 → 点进去核对 App ID**（**列表 = 租户内权威视图**）⇒ ⇒ ★ **「按应用名搜」额外不可靠**：后台有**两个 app 同名「貂蝉」** ⇒ 名称命中 ≠ 就是它 ⇒ 必须核对 App ID。
⇒ **判定式：「搜不到」= 换账号，不是「应用不存在」**
（在 Ayn 账号下搜 `cli_aafb5d7cd6389be4` ⇒ 页面显示「暂无搜索结果」；★ **同一个 app_id `cli_aa420db06a78dcc5` 在 tuz 账号下则命中** ⇒ ⇒ **同一套操作，只换身份即换结果 ⇒ 搜索机制有效 ✔**）

**★ 3 个已登录身份的应用清单（实测）**：

- **tuz 账号（= `+86157…92`，7 个）**：达人1 / 貂蝉 / 小路 / 贾维斯 / 全可传媒小翊的智能助手 / 妍妍 / 露露
- **Ayn 账号（= `+86190…76`，2 个）**：ayn小助理 / Ayn的智能助手
- **豆包账号（= 悦己，1 个）**：悦己的智能助手

> ℹ️ **app_id 的来源（自己查配置时用）**：
> `~/.openclaw/openclaw.json` → `channels.feishu.accounts.<账号标签>.appId`
> ⇒ ⚠️ **字段名是驼峰 `appId`**，不是 `app_id` ⇒ 查 `app_id` 会拿到 `None`
> （另有 `channels.feishu.appId` = `default` 那把）。

**直达 URL 模板**（实测有效）：

```
https://open.feishu.cn/app/<app_id>/baseinfo
```

- 进得去 ⇒ 当前账号有权限，标题栏 `凭证与基础信息 - <应用名> - 开发者后台` 里那个 **就是真实应用名**；
- 跳 `/exception/403` ⇒ 当前账号看不到它（**不代表应用不存在**）。

**secret 存储位置（真源）**：
- `~/.openclaw/state/openclaw.sqlite` → 表 `secret_store_entries`
  （`scope_kind='team'`, `scope_id=''`, `kind='secret'`, 值长度均为 **32 字符**）
- ⚠️ **不要手改 sqlite** —— 有官方 CLI：`openclaw secrets store set`
- ⚠️ 这 4 条是 **team scope**，4 个 app 共用 team scope ⇒ 每个 app 的 secret 必须**分别**设置。

**另有一个独立副本**（容易漏！）：
- `data/solobrave.db` → 表 `tenant_feishu_config` → 1 行 `t_default`，
  `app_secret` 是 **明文 32 字符**（`status='routed'`, `bound_at=2026-10-09`）
  ⇒ **重置 `t_default` 的 secret 后，这张表也要同步更新**，否则两处不一致。
  ⚠️ `t_default` = 后台应用「**貂蝉**」，在 **tuz 账号**下
  ⇒ 它是 4 个里**当前账号就能打得开**的那一个。
- `data/daren-bot/daren_bot.db` → 表 `bot_tenants` → 2 行
  （`daren_boss` / `daren_test2`），存的是 **AES-GCM 密文**（`app_secret_enc`，60 字符）
  ⇒ 这两个 app 的 app_id 与上面 4 个**都不同**（`cli_aa461b7cb7fa9cb7` / `cli_aa5b67fd7b799d29`），
  ⇒ **不在这次 4 个之内**。如果老大要一起轮换，需另走 `python3 -m daren_bot.main add-tenant` 流程。
  ⚠️ 2026-10-26 21:0x → 21:4x 实测：`cli_aa461b7cb7fa9cb7` = 后台应用「**达人1**」
  （在 **tuz 账号**下，直达 URL 打得开）；
  `cli_aa5b67fd7b799d29` = 后台应用「**ayn小助理**」（在 **Ayn 账号**下）。

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

**Step 0 — 建立「行为层基线」（轮换前必做；2026-10-27 A 边补，B 边 §53.4 指出的真缺口）**

★ **为什么必须做**：
- 我们轮换后能验的是**配置层**（账号 installed/configured/enabled、四服务 200、降级链、store/db 两处指纹一致）；
- 而「行为级验证」验的是「**机器人真的能收到并回复**」⇒ **这一层轮换前从未实测过**
  ⇒ ⇒ 轮换后若机器人不回，**分不清是「轮换坏了」还是「它本来就不通」** ⇒ ⇒ 必须先留基线。

✅ **怎么做（老大手动，1 分钟）**：
1. 在飞书里给**该 app 的机器人**发一句话（例如「密钥轮换自检」）；
2. 看它**是否正常回复**；
3. 把结果记下来（**回 / 不回 ＋ 回了什么**）⇒ 这就是该 app 的**行为层基线**。

⚠️⚠️ **必须老大自己在飞书里发，不能用 CLI 代发**：
- CLI `openclaw message send --channel feishu --account <acct> --target <ou_…>` 是**出站**路径（`via=direct`），
  而行为级验证验的是**入站**路径（飞书 → openclaw → agent → 回复）⇒ **两者不是同一条路** ⇒ CLI 发**不构成基线**；
- 且 `open_id` 是**按 app 隔离**的 ⇒ 别处账号下能用的 `ou_…` 在这个 app 下**可能无效**
  ⇒ 一旦失败，会造出一个**与轮换无关的假警报**。
⇒ ⇒ 顺带好处：老大亲自发，会在**该 app 下**真实产生老大的 `open_id` / 会话 ⇒ 后续用 CLI 做**出站**验证才有合法 target。

⇒ ★★ **建议的轮换顺序**（一次只做一个、验通再下一个 —— 见上面「全局铁律」第 2 条）：

| 顺序 | app | phase | 为什么放这个位置 |
|---|---|---|---|
| **1** | **木木**（`FEISHU_APPSECRET_MUMU`）| `--phase store` | ★ 老大平时就在跟它对话 ⇒ **它的行为层基线今天就已经存在**（不必额外做）；且 `--phase store` **不碰 db** ⇒ 路径最简单 ⇒ 先用它把「reset → 粘贴 → 写 store → reload → 老大发消息 → 机器人回」**整条链路跑通** |
| **2** | **「貂蝉」**（`FEISHU_APPSECRET_T_DEFAULT` = `cli_aa420db06a78dcc5`，⚠️ **后台叫「貂蝉」但共有 2 个 app 叫这名字** ⇒ 见 Step 0.5）| 默认 `--phase all` | 它要**连 db 一起写**（`tenant_feishu_config` 里就那一行）⇒ 是整轮里**唯一的新增变量** ⇒ 等第 1 条链路跑通再做，风险最低 |
| 3 / 4 | `default`（`FEISHU_APPSECRET_MAIN`）/ `helen`（`FEISHU_APPSECRET_HELEN`）| `--phase store` | db 里没有它们的行 ⇒ 与第 1 条同路径 ⇒ 最后做 |

⚠️⚠️ **每个 app 都是「后台重置 → 立刻粘贴旋转 → reload → 发消息验证」连着做**，
**不要先把 4 个全重置了再回头旋转** ——
飞书的「后台重置」**本身就是「撤旧」**（旧 secret 立即失效），与上面「全局铁律」第 1 条
「先生成新的 → 验证新的能用 → 再撤旧的」正好相反（飞书平台**只在重置后**才给新 secret），
⇒ ⇒ 所以**必须把「撤旧到装新」的窗口压到最短** ⇒ **一个 app 一个 app 来，别批量重置**。

**Step 0.5 — ★★ 机械自检：确认「本轮该找的是哪个 app」（老大手动，10 秒；2026-10-27 A 边落地）**

★★★ **为什么必须做这一小步**：
「貂蝉」这**两个字在飞书后台对应两个不同的 app**（实测，见上面「同名不同物」段）：
- `cli_aa420db06a78dcc5`（= `t_default`，tuz 账号）后台名「貂蝉」
- `cli_aafb5d7cd6389be4`（= `default`，另一个账号）后台名**也是**「貂蝉」

⇒ ⇒ **按应用名去后台搜，会同时命中两个** ⇒ ⇒ **认错 = 重置错 app 的 secret = 另一个机器人掉线**
⇒ ⇒ ⇒ **「app_id 是唯一可靠依据」**（后台「凭证与基础信息」页顶部「应用凭证 → App ID」）。

⇒ ⇒ ★★ **但「文档里写了警告」拦不住人**（本项目铁律：「纪律写进文档 ≠ 行为自动改变」）
⇒ ⇒ ⇒ **所以做成了机械闸门**：`scripts/feishu_app_name.py`。

⇒ ⇒ ★★★★ **「所属飞书账号」实测结论（A 边 2026-10-27 16:0x–16:2x 用浏览器逐账号实测；★ B 边 16:2x–16:3x 独立复现 12 格矩阵 ＋ 更正 2 处）**：
⇒ ⇒ ⇒ 测法：飞书开放平台后台按 **App ID** 搜索 ⇒ 命中 = 该账号下有此 app。
⇒ ⇒ ⇒ **已登录的 3 个身份（★「昵称 / 手机号 / 所有者」是三套不同标签 —— 完整对照表见本文 §2）**：① **tuz**（＝ `+86157…92`，飞书个人用户，所有者 tuz）② **Ayn**（＝ `+86190…76`，飞书个人用户，所有者 Ayn）③ **豆包账号**（＝悦己，团队/企业，所有者 悦己）
⇒ ⇒ ⇒ ⚠️ **（A 边 §85.2 原把 `+86190…76` 标成「豆包账号」、并把 `tuz` 与 `+86157…92` 列成两个账号 ⇒ 已按 B 边实测更正 ⇒ 交接单 §86.2）**

| 标签 | app_id | 后台真名 | **所属飞书账号（实测）** |
|---|---|---|---|
| `t_default` | `cli_aa420db06a78dcc5` | 貂蝉 | ✅ **tuz / +86157…92 命中**（同一租户，tuz 是所有者）|
| `mumu` | `cli_aa51b1b62a7a9bf6` | 木木 | ⚠️ **归属未确证**（三个已登录账号下 app_id / 应用名两路检索均未召回 ⇒ **疑在第 4 个账号（未登录）** ⇒ 须在它的**应用列表**里按 App ID 核对）|
| `default` | `cli_aafb5d7cd6389be4` | 貂蝉 | ⚠️ **归属未确证**（同上：三个已登录账号下 app_id / 应用名两路检索均未召回 ⇒ **疑在第 4 个账号（未登录）** ⇒ 须在它的**应用列表**里按 App ID 核对）|
| `helen` | `cli_aaf9b98563b99bd7` | Ayn的智能助手 | ✅ **Ayn（= +86190…76）命中**（所有者 Ayn）★ **（A 边 §85.2 原写「三个都搜不到」⇒ 已按 B 边 16:2x 实测更正 ⇒ 交接单 §86.4）** ★ **（2026-10-28 A 边补注：检索只覆盖当前租户 ⇒ HIT 可靠、MISS 只说明「不在本租户」⇒ 定位一律以「应用列表 + App ID」为准 ⇒ 交接单 §98）** |

⇒ ⇒ ⇒ ★★ **这条对老大最有用**：**木木是轮换第 1 步** ⇒ **切到这 3 个账号里任何一个都找不到它**
⇒ ⇒ ⇒ ⇒ **疑在第四个账号（老大自己的那个）** ⇒ **否则会误判成「应用不存在」** ⇒ ★ **（★ 原写「必须切到第四个账号」⇒ 已降级为「疑在」：「三个身份全搜不到」是排除法推断、不是实测归属 ⇒ B 边 §89.7 ⇒ 交接单 §98）**
⇒ ⇒ ⇒ ⇒ ⇒ ★ **「搜不到 ≠ 不存在」**：先换账号，**别急着下结论**。
⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ★ 而 **「搜到了」也不等于「就是它」** ⇒ 仍然要核对 App ID 逐字一致（Step 0.5 的闸门）。
⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ★ **正向对照（证明搜索机制有效，不是「搜不到 = 搜索坏了」）**：
`cli_aa420db06a78dcc5` 在 **tuz** 身份下**命中**、在 **Ayn** 与 **豆包账号** 身份下**不命中** ⇒ ⇒ ⇒ **同一个 app_id、同一套 URL 搜索法，只换身份即换结果** ✔
⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ★★ **（★ 2026-10-28 A 边实测补注：这条对照证明的是「检索按租户过滤」，不是「跨租户检索」⇒ 「命中」⇒ 归属确定；「不命中」⇒ 只说明「不在当前租户」⇒ **最终定位一律走「应用列表 + App ID」** ⇒ 交接单 §98）**
⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ⇒ ★ **（A 边 §85.2 原写「在 tuz 与 +86157…92 两个账号下都搜到了」—— 那两个是同一个身份 ⇒ 那是「同一租户搜两遍」，不是「换账号即换结果」⇒ 现换成真正跨 3 个身份的对照 ⇒ 交接单 §86.5）**

✅ **Step 0.5 怎么做**：
```bash
# ★ 每次轮换前先跑这一条 ⇒ 屏幕上会打出「本轮该找的 App ID」，照着这串去后台核对
# ★ 用**绝对路径**（老大不一定在仓库目录里；脚本本身与 cwd 无关）
python3 /Users/qichen/solobrave-prod/scripts/feishu_app_name.py --app mumu        # 第 1 步（木木）
python3 /Users/qichen/solobrave-prod/scripts/feishu_app_name.py --app t_default   # 第 2 步（貂蝉/t_default）
python3 /Users/qichen/solobrave-prod/scripts/feishu_app_name.py --all              # 想看全表 + 同名冲突时
```
输出里那行 `>>> cli_… <<<` 就是**本轮唯一正确的 App ID** ⇒ 老大在后台页面上核对它**逐字一致**，再点重置。

⇒ ★★ **更稳的做法（推荐老大用这个）**：在后台看到 App ID 后，**把它粘回来核对**：
```bash
# 把后台页面上看到的 App ID 粘进来（把 cli_xxx 换成你看到的那个）
python3 /Users/qichen/solobrave-prod/scripts/feishu_app_name.py --check cli_你看到的那个 --app t_default
```
- **退出码 0** ⇒ 「就是这个，继续」；
- **退出码 1** ⇒ **⚠️ 你复制的是别的 app ⇒ 不要重置它**（输出会直接告诉你本轮该找的 App ID）。

⇒ ⇒ ★★★ **好消息：「粘错 secret」已被机械拦住**（A 边 14:2x 实测坐实）：
轮换脚本在**写入之前**就用 `app_id` 去问飞书「这个 secret 属不属于这个 app」
⇒ **粘了别的 app 的 secret ⇒ `code=10014 app secret invalid` ⇒ exit 5 ⇒ store 与 db 零写入**。
⇒ ⇒ **所以最坏情况是「老大白费一轮 + 看到一条看不懂的报错」，不是「机器人掉线」**。

⇒ ★ **取数纪律（这个脚本内置，不可放宽）**：app_secret 从 openclaw store **只读**、
**只在内存里过**、**绝不落盘**、**绝不回显**；只输出 app_id / 后台应用名 / 指纹 md5[:12]。

**Step 1 — 在飞书后台重置 secret（老大手动）**

- 位置：飞书开放平台 → **先确认账号对得上**（右上角「个人中心 → 切换账号」，共 3 个已登录账号）
  → ★★ **切到该账号 → 看它的「企业自建应用」列表 → 点进去核对 App ID**（★ **搜索框只覆盖当前租户、且应用名可重名 ⇒ 别拿搜索当定位手段** ⇒ 交接单 §98）
  → 左侧「凭证与基础信息」→ 顶部「应用凭证 → App ID」**核对与上表一致** → **App Secret** → 重置
- ⚠️ **不要靠应用名找**（见上面的实测更正：openclaw 的 `name` ≠ 后台应用名）。
- ⚠️⚠️ **「貂蝉」在后台有两个** ⇒ **必须粘 app_id 搜 / 核对**（见 Step 0.5）⇒ **别用应用名搜**。
- ⚠️⚠️⚠️ **账号要对上（2026-10-27 16:2x–16:3x 浏览器实测，全表见 Step 0.5）**：
  - `t_default` ⇒ 在 **`tuz`（= `+86157…92`）** 身份下**能搜到** ✔
  - `helen` ⇒ 在 **`Ayn`（= `+86190…76`）** 身份下**能搜到** ✔ ★ **（A 边 §85.2 原写「也要切第 4 个账号」⇒ 已按 B 边 16:2x 实测更正 ⇒ 交接单 §86.4）**
  - **`mumu` / `default`** ⇒ **已登录的 3 个身份下都搜不到** ⇒ **归属未确证，疑在第 4 个身份** ⇒ **切到第 4 个身份，在它的应用列表里按 App ID 核对**
    ⇒ ⇒ **搜不到 = 换账号，不是「应用不存在」** ⚠️
  - ⚠️ **切账号时认人靠头像/文字，不靠「第 N 条」**（子菜单顺序随当前账号变）⇒ 切完看应用列表「所有者」列变了没。
- 逐个 app 做，**不要一次全重置**。

> ✅ 断言：后台显示新的 secret（32 字符），且**你自己已经复制下来**（只显示一次）。
> ❌ 若还没复制就去做下一个 app ⇒ 这个 secret 就永远拿不回来了。

**Step 2 — 写入 secret store（★ `t_default` 连 db 一起写）**

★★ **优先走「一条龙脚本」**（老大在后台只需复制**一次** ⇒ store 与 db **结构上不可能不一致**）：

```bash
# 2.0 老大在飞书后台把新 secret 复制到剪贴板（★ 复制一次就够，后面不要重复制）
# 2.1 一条命令：
pbpaste | bash ~/.solobrave-secrets/rotate_feishu_secret_and_db.sh FEISHU_APPSECRET_T_DEFAULT
# 2.2 ★★ 跑完顺手清剪贴板（★ 见下方「剪贴板残留」—— 新 secret 还在剪贴板里）
printf '' | pbcopy
```

⇒ ✅ **三个断言都要看**：
1. **脚本最后一行是 `★ 哨兵：全部步骤完成`** ⇒ 看到这行 = 脚本跑到底了；
2. 脚本打印的**新 secret 指纹（md5[:12]）**与它自己写的值一致
   （输出里还会有「写前验证：飞书已接受（code=0）」「断言(db): length=32 / status=routed / 指纹匹配」）。
3. **清剪贴板那行也执行了**（见下）。

> ⚠️⚠️ **不要只看 exit code** —— macOS 自带 **bash 3.2** 的 `trap cleanup EXIT`
> 会把「意外退出」的退出码洗成 **0** ⇒ **脚本死了也报成功** ⇒ **成功判据 = 末行哨兵**。

> ★★ **退出码契约（2026-10-27 A 边实测补齐；exit 6 这一档此前从未被实测过）**
>
> | 退出码 | 含义 | 写入情况 |
> |---|---|---|
> | 0 | 成功 | 两处都写好（`--phase` 决定写哪几处）|
> | 2 | 参数错（条目名/phase/变量未设置）| **零写入** |
> | 3 | 读到 EOF（没粘东西）| **零写入** |
> | 4 | 长度 ≠ 32 | **零写入** |
> | 5 | 飞书不认这个 secret（写前验证 code≠0）| **零写入** |
> | **6** | **db 侧 / store 侧的前置检查或断言失败** | **零写入**（2026-10-27 起）|
>
> ★★ **「外部可观测契约」视角：exit 6 背后是 8 条内部分支，但外部只需认 3 件事**
> —— **退出码是 6 ＋ store/db 都没被改 ＋ 没有打成功哨兵** ⇒ **零写入**。
>
> | 内部分支（代码层）| 外部可观测 | 能否与其他 **exit 6** 分支区分 |
> |---|---|---|
> | 备份 store 失败 | exit 6 ＋ 零写入 ＋ 无哨兵 | ❌ 与其他 exit 6 分支**不可区分** |
> | 备份 db 失败 | 同上 | ❌ |
> | 预检读库失败 | 同上 | ❌ |
> | db 文件不存在 | 同上 | ❌ |
> | db 里没有该 `app_id` 的行 | 同上 | ❌ |
> | 同一 `app_id` 命中多行 | 同上 | ❌ |
> | `tenant_id` 与预期不符 | 同上 | ❌ |
> | 写后断言失败（db / store）| 同上 | ❌ |
>
> ⇒ ★ **给老大的一句话**：**你不需要知道是哪一条分支炸的** ——
> 看到 **exit 6** ＋ **没有成功哨兵** ⇒ 就是「**什么都没写**」⇒ 直接改用 `--phase store` 重来即可。
> ⇒ ⇒ **「区分内部分支」是代码层的事**（用分支级覆盖矩阵 + 逐条单改的变异去保证），
> **不是操作层的事** ⇒ 契约文档只保证**外部可观测**的那 3 件事。
> ⇒ ★ 「备份 db 失败」与「预检读库失败」**结构上**不可区分（有实验证据，见交接单 §50.3）：
> 要让 db 备份失败就得让 app db 内容坏 ⇒ 内容坏也必然让预检读库失败 ⇒ 同一现象 ⇒ 同一契约。
> ⇒ **同理**：备份目的地 I/O 失败也够不到 db 备份分支（store 备份用同一个 `--bak-dir` 且排在前面 ⇒ 先炸它）。
> ⇒ ⚠️ 因此**不为「备份 db 失败」单独立测试用例**（立了也分不出来）——
> 但**也不许把它记成「已覆盖」**：它是**不可判别**，不是**零覆盖**（两者分开记账，见交接单 §48.6 / §50.3）。

> ⚠️ **exit 6 以前是「半写」** —— 脚本原顺序是「写 store → 同步 db」，
> 而 db 侧的三条拒写条件（**没有该 `app_id` 的行** / **命中多行** / **`tenant_id` 不符**）
> 是**在 store 已经写完之后**才检查的 ⇒ **store 有新值、db 还是旧值**
> ⇒ 「粘贴一次 ⇒ 两处结构上一致」被打破（其中 `tenant_id` 不符最严重：
> **secret 已经落进 store，却没通过交叉校验**）。
> ⇒ **修法（2026-10-27 A 边已落地）**：把这三条条件**前移成「步骤 2.5 db 侧预检」**
> ⇒ db 侧任何确定性失败 ⇒ **store 与 db 双双零写入** ⇒ 干净失败。
> ⇒ 预检覆盖 5 类：① db 文件不存在 ② db 读不了/不是 sqlite 库/没有该表
>   ③ 没有该 `app_id` 的行 ④ 该 `app_id` 命中多行 ⑤ `tenant_id` 与预期不符。
>
> ⚠️ **已知残留窗口（诚实声明，别写成「绝不会半写」）**：预检通过之后、
> step 4 的 `UPDATE` 真正落库之前，若 db 被锁 / 磁盘满 / 进程被杀
> ⇒ 仍可能「store 已写新值、db 还是旧值」。概率低（预检已排除全部确定性失败）。
> ⇒ **恢复方式 = 重跑同一条命令**（脚本是幂等的：同一个新 secret 再粘一次即可对齐两处）。
>
> ✅ **验过的证据**（2026-10-27 A 边实测，不是我推断的）：
> - 5 类 db 侧失败 ⇒ **退出码真为 6**（`set -euo pipefail` + `trap cleanup EXIT` **不会**把它洗成 0）
>   + **store 与 db 双双零写入** + **不打成功哨兵**；
> - **变异对照**：把预检整体禁用 ⇒ 上述 5 例立刻变红（症状正是「store 被改」= 半写复现）；
>   把预检的 exit 6 改成 0 ⇒ 脚本继续往下走、真把 store 写了 ⇒ **exit 6 是承重的**；
> - B 边守卫 `scripts/e2e_feishu_rotate_exit_codes.sh`（8/8 全绿）**未受影响**。

> ⚠️⚠️ **「长度校验」只是辅助闸，真正的主闸是「写前向飞书验证」**（B 边 §40 实测发现，A 边复核确认）：
> - 脚本第 3 步查长度是不是 32 ⇒ **但 bash 的 `read` 会按 IFS 剥掉首尾空白**
>   ⇒ **复制时多带一个尾随空格，33 字符会被读成 32 ⇒ 静默通过**（实测）。
> - ⇒ 好处：老大手抖多带空格会被自动修好；
>   坏处：**长度对了不等于复制对了**（长度校验并没有在验证「你复制的是不是这个字段」）。
> - ⇒ **真正把关的是第 2 步「写前验证：飞书是否接受这个 secret」**（只有 `code=0` 才写）
>   ⇒ 所以**必须看那行输出**，不能只看「长度 32 OK」就放心。

> ⚠️⚠️ **剪贴板残留**（`pbpaste` **只读不清**）：
> - 老大复制的新 secret **跑完仍留在系统剪贴板** ⇒ 会被 **Spotlight 索引 / 剪贴板历史**
>   捕获 ⇒ **与 §37.6 那 44 处明文副本同源**（脚本这层是干净的，剪贴板脚本管不了）。
> - ⇒ **跑完立刻执行 `printf '' | pbcopy`**（上面 2.2）⇒ 一步清空。
> - ⇒ 四个 app 都轮换完后再清一次即可（中途清也行，只是要重新复制）。

⇒ ★★ **四个 app 的 phase 不同**（**其余三个一律 `--phase store`**）：

| app（openclaw 标签）| 条目名 | 怎么跑 | 为什么 |
|---|---|---|---|
| **`t_default`（后台名「貂蝉」）** | `FEISHU_APPSECRET_T_DEFAULT` | **默认 `--phase all`** | ★ **部署目录 db 里有它那一行** ⇒ 必须连 db 一起写，否则该租户 Bot 401 |
| `default` | `FEISHU_APPSECRET_MAIN` | `--phase store` | db 里**没有**它的行 ⇒ 用 `all` 会 exit 6（**且零写入** —— 2026-10-27 起，失败不再留半写）|
| `helen` | `FEISHU_APPSECRET_HELEN` | `--phase store` | 同上 |
| `mumu` | `FEISHU_APPSECRET_MUMU` | `--phase store` | 同上 |

```bash
# 例：helen（只写 store）
pbpaste | bash ~/.solobrave-secrets/rotate_feishu_secret_and_db.sh FEISHU_APPSECRET_HELEN --phase store
```

> ⚠️ **若你改走下面的「官方 CLI」备选路径**：它**只写 store、不写 db**
> ⇒ **`t_default` 走它之后必须补跑 Step 5**，且 **两次粘贴必须一致**
> ⇒ 这正是「一条龙脚本」要消灭的那个坑。

**Step 2 备选路径 — 直接用官方 CLI（⚠️ 只写 store，不写 db）**

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

> ⚠️ **顺序**：`t_default` 的行为验证**必须在 db 写完之后**。
> ⇒ 走「一条龙脚本 `--phase all`」时 db 已随 Step 2 一起写好 ⇒ 直接做本步；
> ⇒ 若走的是「官方 CLI」备选路径 ⇒ **先补 Step 5，再做本步**，否则会验出一个**假的 401**。

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

★ **正常路径不需要这一步** —— Step 2 的 `--phase all` 已经把 store 与 db **一起写了**。
⚠️⚠️ **别再重复跑**：若 Step 2 已成功，再用**第二次粘贴**的值覆盖 db
⇒ **可能与 store 不一致**（这正是「粘贴两次」的原始风险）。

⇒ **仅在 Step 2 中途失败、需要单独补写 db 时**才跑（脚本会自己再向飞书验证一次）：

```bash
pbpaste | bash ~/.solobrave-secrets/rotate_feishu_secret_and_db.sh FEISHU_APPSECRET_T_DEFAULT --phase db
```

⇒ 脚本内部已经做对这几件事（**不要手写 SQL 替代**）：
① **用 sqlite `backup()` API 取一致性快照** —— ⚠️ **`cp` 一个 WAL 库不是一致性备份**
（生产库 `data/solobrave.db` 是 WAL 模式，`cp` 可能拷到撕裂快照）；
② **按不可变 `app_id` 定位**（不是 `tenant_id` 那种可读名）+ `tenant_id` 交叉校验（不一致 ⇒ 拒写）；
③ **参数绑定**（secret 不进 SQL 文本、不进 shell history）；
④ **写前向飞书验证**（复制错了 ⇒ 对 db **零破坏**）；
⑤ 断言 `length=32` + `status` 未变 + 指纹匹配；**只打印指纹，绝不打印明文**。

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

**★ 另一类漏项：飞书 app secret 在老大本机「其它项目目录」里的明文（2026-10-26 21:3x，B 边全盘实测）**

⚠️ §2 的副本地图只覆盖了「部署目录 + `.env` + `settings.json` + `models.json`」
⇒ **漏了老大本机上其它项目目录里的 `solobrave.db`**（表 `tenant_feishu_config` / `user_feishu_config`
的 `app_secret` 列 = **明文 32 字符**）：

| 位置 | 表.列 | 明文指纹 |
|---|---|---|
| `/Users/qichen/solobrave-prod/data/solobrave.db` | `tenant_feishu_config.app_secret` | `21e42f04ea54` |
| `/Users/qichen/solobrave-prod/data/backups/20261026_*/solobrave.db`（4 个时间点）| 同上 | `21e42f04ea54` |
| `/Users/qichen/solobrave-prod/data/solobrave-backup-20261012-192929.db` | 同上 | `21e42f04ea54` |
| `/Users/qichen/Developer/solobrave-code/data/`（含 8 个时间点 backups）| 同上 | `21e42f04ea54` |
| `/Users/qichen/Developer/solubrave-prod/data/solobrave.db` | `user_feishu_config.app_secret` | `5c3727ab3a85` |
| `/Users/qichen/Developer/solubrave-test/` + `/Users/qichen/Projects/solobrave-test/` + `/Users/qichen/Desktop/solobrave-test/` | `user_feishu_config.app_secret` | `e36d40e5c0e1` |

⇒ ★ **`21e42f04ea54` 就是 §2 Step 5 要同步的 `t_default` 那把**
⇒ 轮换 `t_default` 后它**立即作废**，但**旧值仍以明文躺在 14+ 个 db 文件里** ⇒ 至少要清掉。
⇒ ⚠️ `5c3727ab3a85` / `e36d40e5c0e1` 是 `user_feishu_config`（**不同的表 ⇒ 不同的飞书应用**）
⇒ **它们各自属于哪个 app 待查**（不在本轮 4 个的确定范围里）⇒ 老大若要一并轮换，需先查清对应 app。
⇒ ⚠️ 另：`~/.Trash/gate_verify_*/copy.db` 里也有 `bot_tenants` 的 AES-GCM 密文副本。

⇒ ★ **纪律（这是「副本地图」第三次漏项 ⇒ 升级判据）**：
**副本地图不能只扫「当前部署目录」** ——
**凡是把凭据写进文件/数据库的工具，都会把副本散到本机各处**
⇒ **必须按【内容指纹】全盘 join**（`/Users/qichen` 全域 + `~/.Trash`），
**只扫部署目录得到的「总数」永远是下界，不是总数**。

> ⚠️⚠️ **A 边 21:4x 定点实测更正（上面那张表的路径归因有两处出入 —— 清理时以本块为准）**：
>
> | 位置 | A 边实测（逐库打开、报行数与指纹）| 结论 |
> |---|---|---|
> | `/Users/qichen/solobrave-prod/data/` —— **7 个**时间点 backups 目录 × 2 个 db + 根目录 `solobrave-backup-20261012-192929.db` | `tenant_feishu_config.app_secret` = `21e42f04ea54` | ★ **15 处命中全在这里 —— 这就是 t_default 要清的那 15 个** |
> | `/Users/qichen/Developer/solobrave-code/data/`（8 个时间点 + 根目录）| `user_feishu_config` 表存在但 **行数 = 0（空表）** | ⚠️ **不持有明文，不用清** |
> | `/Users/qichen/Developer/solubrave-prod/data/solobrave.db` | 同上，**行数 = 0** | ⚠️ **不持有明文** |
> | `/Users/qichen/Developer/solubrave-test/data/solobrave.db` | `user_feishu_config.app_secret` = `5c3727ab3a85` | 1 处（**另一个 app**）|
> | `/Users/qichen/Projects/solobrave-test/data/solobrave.db` + `/Users/qichen/Desktop/solobrave-test/data/solobrave.db` | `user_feishu_config.app_secret` = `e36d40e5c0e1` | 2 处（**另一个 app**）|
>
> ⚠️ **`~/.Trash` 里有 8 个 db 打不开**（`OperationalError`：6 个 `gate_verify_*/copy.db` + 1 个 `data/solubrave.db`）
> ⇒ **无法排除那里还有副本** ⇒ 按 §16 U，只能写「**在可打开的 27 个（定点）/ 2184 个（全盘）库中命中**」，
> **不能断言「只有这些」**。
> ⚠️⚠️ **注意目录名有拼写变体**：`solobrave-*`（正确）与 **`solubrave-*`（少一个 `o`）** 并存
> ⇒ ★ **别只扫 `solobrave-*`**（A 边自己就漏了这一片，见交接单 §27.2）⇒
> **用 `find /Users/qichen -name '*.db'` 真全盘，或把变体一起枚举**。

⚠️⚠️⚠️ **★ 第四次漏项：`~/.Trash`（废纸篓）= 凭据的明文沉积层（2026-10-26 22:0x，B 边实测）**

⇒ 前三次副本地图漏项（`settings.json` / 其它项目目录 / 路径归因）**都没扫 `~/.Trash`**
⇒ 这轮深扫后坐实：★ **废纸篓里有现役凭据的明文副本**。

| 废纸篓里的东西 | 字节 | md5[:12] | 是不是现役 |
|---|---|---|---|
| `.solobrave_github_pat` | 93 | `4c7a0e6b974b` | ★ **是 —— 与 keychain 里那把完全同值** |
| `.solobrave_github_pat 20.14.10` | 93 | `4c7a0e6b974b` | 同值（Finder 拖废纸篓时自动改名）|
| `.solobrave_github_pat 20.15.25` | 93 | `4c7a0e6b974b` | 同值 |
| `_srv_backup.py` 等 4 份旧源码备份 | — | 含 `api_key` 明文 | **否** —— 与现役 8 个指纹 **0 匹配**，是 2026-10-21 已作废的旧 Kimi key |

⇒ ★ **三份 PAT 同 md5 ⇒ 1 把凭据、3 个副本**（报数量前先按内容指纹去重）。
⇒ ⚠️ **「删除」「拖进废纸篓」都不等于凭据消失** —— macOS 上拖废纸篓是**可恢复删除**，明文仍在磁盘。
⇒ ⇒ **老大侧待办（§1 轮换 PAT 之后一并做）**：
**清空废纸篓**（Finder 右键废纸篓 → 清倒废纸篓），或至少删掉上面那 3 个 `.solobrave_github_pat*`。
⚠️ **清废纸篓会一并删掉老大自己的其它文件** ⇒ **这一步由老大自己按需执行**，我不代劳。

⇒ ★ **顺带结案 A 边 §27.4 的「8 个 db 打不开、无法排除」**：
`~/.Trash` 下 `*.db*` 候选 **11 个** ⇒ **10 个能打开**，唯一失败的 `copy.db-shm` **是 shm 文件、本就不是 db**。
⇒ 打不开的真因 = **WAL 模式 + 需写权限重建 shm** ⇒ 用 `file:<path>?immutable=1`
或连 `-wal` / `-shm` 一起拷到临时目录再开，**都能打开**。
⇒ **`user_feishu_config.app_secret` / `tenant_feishu_config.app_secret` 在 `~/.Trash` 里 0 处明文**
⇒ 命中的只有 `bot_tenants.app_secret_enc`（**AES-GCM 密文**）⇒
⇒ **`e36d40e5c0e1` 的副本数现在可以断言为 2 处**（不再是「无法排除」）。
⚠️ 扫描时**匹配要放宽到 `*.db*`**（不是 `*.db`）⇒ 才能命中 `xxx.db.backup_*` 之类变体。

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

> ★★ **关于「磁盘上还剩几份明文」**（2026-10-27 A 边实测，**口径闭合**）：
> 飞书 4 条 app secret 的明文副本 = **264 个文件 / 14 类**
> （含部署目录 `data/` 的 **117** 份、`data/backups/<时间戳>/`、openclaw 本体、MiniMax runtime 的会话快照、
> **shell 历史**、**iCloud**、**Coze 云盘**、kimi 桌面端、另一个项目目录……）
> ⇒ ★★ **其中云盘/iCloud/runtime/快照那几类擦不掉** ⇒ ⇒ **「逐个擦干净」这条路走不通**
> ⇒ ⇒ **唯一成本可控的收敛动作 = 轮换（作废）**：旧 secret 一旦在后台重置，
> ⇒ ⇒ **这 264 个副本同时降级为无害历史残留** ⇒ ⇒ **一轮轮换 ≈ 一次全盘作废**。
> ⇒ 完整地图与取证范围见 **交接单 §57.4**。

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
print('环数:', len(lc.text_rings()))"

# ★★ 2026-10-27 A 边修正：**别用「硬编码 dead 集合」算「能顶几家」** ——
#   上面那段原本带 `dead={POOL_KIMI_MONTHLY, POOL_MINIMAX_OPEN}`，
#   那是 **2026-10-26 kimi 月度额度耗尽当时的快照** ⇒ 现在 kimi 已恢复（12:2x 实测 4/4 key 活）
#   ⇒ 那个集合会把**活着的 kimi 少算成一家** ⇒ **假阴性**（kimi 是唯一活家时会误报 0 ⇒ 误触发回滚）。
#   ⇒ 改用**活探针**（真打一次上游）作为判据：
.venv-daren/bin/python daren_bot/tools/probe_llm_channels.py | tail -12

# 全量测试
# ★ 2026-10-27 A 边修正：`.venv-daren` **没装 pytest** ⇒ 原来这条命令当前**跑不通**
#   （实测 "No module named pytest"）⇒ 改用 .venv-test
.venv-test/bin/python -m pytest -q --no-header -p no:cacheprovider 2>&1 | tail -3
```

> ✅ 断言：
> - 4 行健康检查全是 `200`（**注意 8080 是 `/api/health`、8092 是 `/healthz`**，
>   用 `/health` 会得 404 —— 那是路径错，不是服务挂了）
> - **活探针**：可用环 ≥ 3、**活着的独立服务商 ≥ 1**
>   （**2026-10-27 12:2x 实测基线：可用环 4/6、活着的独立服务商 2 家（kimi + minimax）、
>   活着的独立额度池 3 个（cp-subscription / code-subscription / kimi-monthly-quota）、
>   死的只剩 `minimax-open-balance`（402 需充钱）**。
>   ⇒ 10-26 当时的基线是「环数 6、能顶 = {minimax}」⇒ **kimi 月度额度恢复后多回一家**。
>   ⇒ 轮换后若「能顶」变成 0 ⇒ **那家 key 填错了，立刻回滚**，别继续做下一个）
> - pytest **`1096 passed / 7 skipped / 0 failed`**（**2026-10-27 12:4x 当场实测**，用
>   `.venv-test/bin/python`；前一个基线 1071/6/0 @ `4a98b4f` ⇒ 差 25 个 = 新增测试，非回归）
>   ⇒ ★ **skip 从 6 变 7**：新增的是「live 门禁（LLM 链活探针）」⇒ 默认 skip 是**设计**（要真打上游）
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
> | 1 | `tests/test_llm_channels.py::test_live_chain_has_alive_independent_vendor` | live 门禁：**真探针**测「此刻活着的独立服务商」（`SOLO_BRAVE_LIVE=1` 才跑）⇒ ★ 这是**唯一**能回答「现在还剩几家能顶」的测试 |
>
> ⇒ **看到 7 条 skip 不用慌**，那是「需要真环境」的显式登记（不是隐形死代码）。
> ⇒ ★ **想知道「此刻还剩几家能顶」⇒ 别看任何写死的集合，跑探针**：
> ```bash
> cd /Users/qichen/solobrave-prod
> .venv-test/bin/python daren_bot/tools/probe_llm_channels.py | tail -8
> SOLO_BRAVE_LIVE=1 .venv-test/bin/python -m pytest tests/test_llm_channels.py -m live -q -s
> ```
> ⇒ 若 skip 数**变多** ⇒ 有测试被意外 skip 了，要查；**变少** ⇒ 有 live 门禁失效了，也要查。
>
> ⚠️ 「HTTP 200 要看响应体」—— 光看状态码不够：
> ```bash
> curl -s http://127.0.0.1:8095/health | head -c 300
> ```
> 要能看到 `browser_up` / `daren_browser_up` / `logged_in` 等字段。

### 4.1 ★★ 轮换后「明文残留 + workdir 残留」补偿控制（@贾维斯 执行）

> ⚠️ **为什么要单列这一节**（2026-10-27 A 边 §70.2 实锤）：
> 守卫 `scripts/e2e_feishu_rotate_exit_codes.sh` 的「本次运行零残留」断言，
> glob 写的是 `/private/tmp/.sbfb.?????????`（**9 个 ?**），
> 而被测脚本 `mktemp -d /private/tmp/.sbfb.XXXXXXXX` 产生的是 **8 位**随机名
> ⇒ **命中恒 0** ⇒ 该断言**恒真 = 判不出任何东西**（假阴性 ⇒ 假绿）。
> ⇒ ⇒ **轮换后不能只信守卫的「零残留」** ⇒ 必须有一道**独立**复核。

```bash
cd /Users/qichen/solobrave-prod

# ① 四种命名形状的 workdir 残留（宽度都要覆盖 —— 只查当前那一个形状 = 漏算）
for p in '/private/tmp/.sbfb.*' '/private/tmp/.sbfb-guard.*' '/private/tmp/sbfb-e2e.*' '/private/tmp/.probebak.*'; do
  echo "$p => $(ls -d $p 2>/dev/null | wc -l | tr -d ' ')"
done

# ② 真值扫描：现役明文**只进内存** ⇒ 逐字节找 ⇒ **不回显明文、不回显指纹**
python3 scripts/secret_residue_scan.py

# ③ 先证明这个扫描器能抓（否则「0 命中」可能只是恒绿）
python3 scripts/secret_residue_scan.py --selftest
```

> ✅ 断言：
> - ① 四行**全是 0**；非 0 ⇒ 先确认是不是并发方在用 ⇒ 再清理
>   （**批量临时目录用绝对路径删除** —— 本机 PATH 里的 `rm` 是可恢复删除 shim，会静默失败或挂死）
> - ② 输出 **「✅ 未发现现役明文残留」**；
>   命中 ⇒ **立刻按 §6 处理，不要继续做下一个 app**（窗口里的明文比顺序更重要）
> - ③ 自检输出 **「canary 被命中」**；报不出 ⇒ 扫描器坏了 ⇒ ② 的「0 命中」**不作数**

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
