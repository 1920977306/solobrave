# SoloBrave 项目 Agent 工作准则

## 0. ★★ 并行开发铁律（2026-10-25 新增，**开工前必读**）

**本项目同时有多个对话/Agent 在改同一套系统** ⇒ 撞车是唯一的高频事故源。

### 0.1 开工前必做三件事

1. **先读交接单**：`docs/PARALLEL-HANDOFF-2026-10-25.md`
   —— 里面有两边的目录/分支分工、已完成的活、待办清单、**文件占用登记**。
2. **确认自己的基点**：`git fetch origin && git log --oneline -1 origin/dev`
   ⇒ **别在旧基点上写代码**（基点漂移 = 改了也白改）。
3. **确认自己在哪个目录**（见下表）—— **搞错目录 = 改了一份没人部署的旧代码**。

★ **0.1.1 收到跨边消息时，多做一步「基点自查」**（2026-10-26 补）：
   跨边消息**会在队列里排队**，到达时基点可能已前进好几轮
   ⇒ **消息里的「当前状态」是快照，不是现状**。
   ```bash
   git fetch origin && git log --oneline -1 origin/dev \
     && git merge-base --is-ancestor <你查的 commit> origin/dev && echo "✅ 你的版本已在 dev"
   ```
   - 「已在 dev」⇒ **别再报了**（不是没修，是你查的是旧版）；
   - 非 0 ⇒ 你的版本**真的没进 dev** ⇒ 报告有效。
   ⇒ ⚠️ 今晚真实案例：B 边连续 4 次报同一个「死因盲区未修」并附 patch，
     而它**早在 `0fe3bb1` 就修好并合进 dev 了**（他查的是旧基点）。
   ⇒ **收到跨边报告 → 先 re-verify 再动手**，否则会重复修已修的东西。

★ **0.1.2 并发期跑全量测试，结论不可信**（2026-10-26 补）：
   今晚出现过 3 次「红」，HEAD 稳定后 1064/6/0 全绿 ⇒ 那些红是**并发中间态**
   （别人正在改文件 / 变异脚本正在跑）。
   ⇒ 拿到「全量有红」先确认 HEAD 稳定 + 工作区干净，再下结论。

### 0.2 目录分工（**2026-10-25 快照，以交接单为准**）

| 对话 | 工作目录 | 分支 |
|---|---|---|
| A（协调/收口）| `/Users/qichen/solobrave-prod` | `main` |
| B（并行开发）| `/Users/qichen/sb-dev/parallel-handoff-1025` | `feat/parallel-handoff-1025` |

### 0.3 ⚠️ 绝对不要在 `/Users/qichen/Desktop/solobrave` 干活

那是同远端的**另一个老 clone**：停在 2026-10-05、落后 **1500 个 commit**、
连 `daren_bot/` 目录都没有。⚠️ 它是**陷阱**，不是备胎。

### 0.4 撞车防护

- **同一时刻，一个文件只能有一个对话在改** ⇒ 改前在交接单的「占用登记」里写一行。
- ★★ **协调类请求（「你改了吗」「先别动 X」「这段归谁」）不能只靠消息 —— 必须落成可查的登记**：
  - **原因（今晚实证）**：**跨边消息有送达延迟** —— A 边 18:05 写「未授权」时，
    B 边的更正**还在队列里没送到**；等它送到（19:12）时 A 边**早已改完同一处**
    ⇒ 结果是**双方各自改了同一处**（好在方向一致、无冲突无回退，但纯属运气）。
  - ⇒ **消息是「意图」，登记才是「事实」** ⇒ 对方收到的消息**可能已过期**。
  - ⇒ **纪律**：
    1. **要别人别动某处 ⇒ 立刻在交接单「占用登记」写一行**（文件 + 持有方 + 状态），
       **不依赖对方是否收到消息**；
    2. **收到「先别动」类请求 ⇒ 先查「占用登记」有没有这一行** ——
       没有登记 = **对方可能没收到** ⇒ **先自己核实当前状态**（§0.1.1），
       **不要凭消息内容直接停手或直接动手**；
    3. ⇒ **两侧都改同一处时，事后以「合并冲突」化解**（两边内容都留、按章节号排序），
       **不要任何一方回退另一方**；
    4. ⇒ 引用别人的结论时，**标明「引自哪条会话 / 哪个 commit」**（§16 U 同款：范围是结论的一部分）。
- ★★ ★ **合并态（`MERGE_HEAD` 存在）下**禁止跑 `git stash` / 任何会重置 git 状态的命令 ——
  ⇒ ** stash 会把 `MERGE_HEAD` 一起存进 stash commit** ⇒ `git stash pop` 恢复后
  **合并态丢失** ⇒ 随后的 `git commit` **退化成普通提交（只有 1 个父）**
  ⇒ **对方的 commit 身份没进 dev** ⇒ `git log --oneline origin/dev..origin/<对方分支>`
  **永远显示 1（未合）**，而**内容其实一个字都没差**。
  - ★ **实测（2026-10-26 20:15，A 边）**：A 边合 B 边 `f7eb0bd` 时，为了做「合并前后围栏对比」
    跑了 `git stash` ⇒ 提交 `8b21c21` 的 message 写着「合入 B 边 f7eb0bd」，
    **但它只有 1 个父** ⇒ `f7eb0bd` 的身份没进 dev ⇒ 而 `git diff` 出来是**零差异**
    （内容早在 dev 里）⇒ **表面全对，拓扑错了**。
  - ⇒ ★ **危害**：对方 rebase 后，它的 commit 会「看起来没合」⇒ 双方都会误判 ⇒ 又一轮返工。
  - ⇒ **纪律**：
    1. **要对比文件内容 ⇒ 把文件拷到 repo 外比对**（`cp x /tmp/x` + `diff`），
       **不要动 git 状态**；
    2. ⇒ **提交前必查 `git cat-file -p HEAD | head -3`**：
       **父提交行应有 2 个 hash**（真 merge）⇒ 只有 1 个 ⇒ **合并态丢了**，
       用 `git merge --abort` + 重做补回身份（**内容零差异，不会改文件**）；
    3. ⇒ **推后必查 `git log --oneline origin/dev..origin/<对方分支>` ⇒ 应为空**；
       **不为空不等于「内容没合」** ⇒ 要用
       `git merge-base --is-ancestor <commit> origin/dev` 判**身份**，
       用 `git diff` 判**内容** ⇒ **两件事分开判**。
- ★★ **跨边发消息（`mavis session send`）时，`session_id` 必须同时放在 `args` 里面和顶层** ——
  ⚠️ **实测（2026-10-26 20:4x，A 边）**：只放在 `args` 里 ⇒ 工具报
  `session_id: Required`（**参数被静默丢弃**，不是内容问题 —— 同一段内容改放顶层就成功）
  ⇒ 连续失败 4 次才定位到 ⇒ **报错说「缺参数」时，先怀疑传参方式，不是内容**
  ⇒ ⇒ **「工具报参数缺失」与「内容被拒」是两回事** ⇒ 换传参位置再试，别改内容。
- ★★ **重置类操作（`reset --hard` / `checkout -B <分支> <ref>` / `branch -f`）之前，先留 backup 分支**：
  ```bash
  git branch backup/<name>-<YYYYMMDD-HHMM> <ref>   # 一行，成本几乎为零
  ```
  - ★ **本项目已两次靠 reflog / 残留对象抢救**（2026-10-26 目录边界事故 + 今晚 `checkout -B` 误摘两笔 commit），
    **两次都是「对象还在但不在分支上」** ⇒ **能救，但要先知道 reflog 能救**。
  - ⇒ **reflog 是唯一可靠的重置后恢复来源**，但**前提是对象没被 gc** ⇒ 所以**要提前留 backup 分支**，
    **不能指望事后救**。
  - ⇒ ★ **推后自检**：`git log --oneline origin/dev..origin/<自己分支>` ⇒ **应为空**；
    **不为空 = 有 commit 没被合进去**（或反过来，**分支被重置过**）。
    ⚠️ 今晚就栽在这：rebase 改写哈希 ⇒ 「只剩 1 个 commit」一度被误读成「对面已合入」，
    实际是**自己把两笔从分支上摘掉了**。
- **不要两拨人同时动 `main`** ⇒ 合入由协调边统一做。
- ★★ **任何边都不许推 `origin/main`**（2026-10-26 18:05 新立，§16 S）：
  `main` 只由**协调边（A 边）在 `dev` 验收后统一推**；其它边一律
  `git push origin HEAD:<自己分支>`（B 边 = `HEAD:feat/parallel-handoff-1025`）。
  ⚠️ **禁止裸 `git push`** —— 部署目录 local `main` 领先 `origin/main` 时，
  裸 push 会把那几个未推 commit **顺手带进 main** = **等于替老大执行了 ⑥**。
  ⚠️ 今晚真发生过：`origin/main` 被推了**两次**（17:46:20 / 17:57:57）——
  老大**当时已拍板**「main 合 dev」（17:45:56，§16 T 有原始记录），
  但**推完只跟老大汇报、没同步给另一条边** ⇒ 对面只能从 reflog 反推，白花一轮取证。
  ⇒ 纪律重点：**推完立刻通知所有边**（§16 S）。
- **改共享模块后必须跑它的所有入口**；「重启生效」≠「验证通过」。
- **收工时把「我改了什么 / 发现什么」写回交接单并 commit 推自己的分支**。

### 0.5 ★★ 每个 Agent 必须有自己的目录 —— **不许借用别人的工作区**

★ 2026-10-26 目录边界事故（B 边 §12.11 实锤，A 边认账并落地）：

A 边为了做「无 `.env` 环境」的验证（部署目录**有** `.env`），
在 **`/Users/qichen/sb-dev/parallel-handoff-1025`（= B 边的工作目录）**里改了文件
（`tests/test_llm_channels.py` / `scripts/mutate_llm_ring_guards.py`）。

⚠️ **这不是「礼貌问题」，是真实竞态**：
**变异脚本的机制是「备份 → 变异 → 还原」，还原是逐字覆盖**
⇒ **它会直接抹掉别人在这期间的改动**，
而且抹完还会报「已还原」⇒ **静默丢失**。
⇒ A 边改的那两个文件，正好是 `mutate_llm_ring_guards.py` **会碰的那两个**
⇒ 再晚一步，B 边跑变异就会把 A 边的改动冲掉，B 边还一无所知。

⇒ ★ **2026-10-26 20:2x 同型第二例（B 边自己认账）**：
B 边为了给 runbook Step 3 加前置自检，**直接在部署目录 `/Users/qichen/solobrave-prod`
（A 边的目录）改了 `docs/SECRET-ROTATION-RUNBOOK.md`**，事后用
`git checkout -- <file>` 还原 ⇒ **同样是 §0.5 违规，只是方向反了**（A 边当年借用 B 边的目录，
这次 B 边借用 A 边的目录）⇒ **「自己的目录」这条对两边一视同仁，没有主次之分**。
⇒ ⚠️ **`git checkout -- <file>` 还原的隐患**：它**按索引覆盖工作区** ⇒
**若那个文件同时被对方改过，会连带抹掉对方的改动**，而且**不报错**。
⇒ ★ A 边实测复核：部署目录与 `origin/dev` **完全一致**、runbook 内容与 `f7eb0bd` **逐字一致**
⇒ **这次没造成损失**（纯属时序侥幸）⇒ 但**机制上仍要堵**。

⇒ **铁律**：
1. **缺环境时另建 worktree，不许借用别人的工作区**。
   ⇒ ★ **对称条款**：**也不许在部署目录（`/Users/qichen/solobrave-prod`）里改文件** ——
   那是**协调边的专用目录**，其它边的一切改动**一律在自己 worktree 里做**，
   改完 commit + 推自己分支，由协调边合入。
   ⇒ ⇒ **还原一律用「备份副本」逐字拷回**，**不用 `git checkout --`** ——
   后者会连带抹掉同文件上别人的改动，且**不报错**。
   （无凭据验证用 `/Users/qichen/sb-dev/alice-noenv` —— B 边已为 A 边建好，
   detached HEAD、无 `.env`、干净。）
2. **在别人目录里跑变异脚本前，先确认没人正在改同一批文件**。
3. **跑完变异后核对「还原」是否真的还原了** ——
   `git status` 看有没有残留的 modified（mtime 变但内容一致 ⇒ 无害；
   内容真变了 ⇒ 还原失败）。

⚠️ 顺带一个自检（B 边 §12.12 的查库坑，同源思路）：
**「工具报告成功」≠「工作区真的干净」** ⇒ 每次跑完变异都要自己 `git status` 复核，
不能只看脚本最后那行「✅ 已还原」。

---

## 1. 数据隔离铁律(最高优先级)

`data/` 整目录在 `.gitignore`。**绝对不要**:
- `git add -f` 任何 `data/` 文件
- 删/覆盖 `data/agents.json`、`data/solobrave.db` 等运行时数据
- 假设 `data/` 里的状态是 git 可恢复的

测试时造 demo 数据要写到 `data/` 外的临时位置(`.tmp/` / 内存常量),截图后清掉。

### ⚠️ 已跟踪文件陷阱(2026-09-10 教训)

`.gitignore` 的 `data/` **对已跟踪文件不生效**!

**症状**:`git ls-files data/agents.json` 还有输出,但 `.gitignore` 里写明 `data/`——它仍是"已跟踪"状态,git 操作会读写它(stash、pull、merge 都能污染生产数据)。

**为什么会发生**:
- 早期项目里 `data/agents.json` 等被 `git add` 进了索引(可能无意或为早期 demo)
- 后来加 `.gitignore` 的 `data/` 只对**新增/未跟踪**文件生效,已跟踪文件不受保护

**修复**:
```bash
# 1. 看哪些 data/ 文件还在跟踪
git ls-files data/

# 2. 从索引移除(工作树文件保留,只 untrack),--cached 必加,不能漏
git rm --cached data/agents.json
git rm --cached data/influencers/index.json
# ... 13 个文件

# 3. commit
git commit -m "fix: untrack data/ runtime files (.gitignore alone insufficient)"

# 4. 验证
git check-ignore -v data/agents.json  # 应输出 .gitignore:N:data/   data/agents.json
git ls-files data/                     # 应为空
```

**历史**:老 commit 里仍然有这些文件的快照(`git log -p` 仍能看到),但 HEAD 不再跟踪,**新 clone 下来的 working tree 干净**。彻底从历史抹掉需要 `git filter-repo`(风险大,会改所有 commit hash),不建议做。

**事故复盘**(2026-09-08,bcf5607 之前那次):为了截图 AI 光晕,给 `data/agents.json` 加 demo 员工并用 `git add -f` 强行 push,覆盖了远端生产数据,用户被迫从备份恢复。
**事故复盘 2**(2026-09-10):用户部署时 `git stash` 把生产 agents.json 冲掉,根因是 .gitignore 对已跟踪文件不生效;13 个 data/ 文件 untrack 后修复。

## 2. v2 样式收尾:边界与教训

v2 设计系统达人卡视觉(中性头像 / 评级 chip / 待评级虚线态)已 commit `36df27e`,但**还有收尾工作**没做。

**收尾时的硬边界**:
- ✅ **只动 CSS / HTML 视觉层**:design system 组件、preview.html 演示
- ❌ **不要碰评级 JS 逻辑**:`renderTalentList` 评级拼接、`_build_talent_injection` 注入、talent DB schema
- ❌ **不要顺手优化别的**(Helen prompt、OCR 流程、平台 key 归一 等)

**为什么**:评级逻辑和视觉高度耦合(JS 渲染什么 data-rating → CSS 才有什么样式),任何逻辑改动都要重测 5 态视觉,容易引发"删错了 / 加多了"型 bug。**样式收尾独立 commit**,出了问题 git revert 范围小,不影响其他功能。

## 3. 开工前先验前提(2026-09-09 教训)

**血泪**:用户描述"删 9 条图片分析/反幻觉规则",但实际 `data/agents.json` 里 Helen 的 `systemPrompt` 字段是空字符串(0 字符),仓库 grep 全部关键词 0 命中,git log 也没找到历史 commit。**根本没有 9 条可删**。

**规则**:
- 接到"删 N 条规则"型任务,**先 grep 文件 + 查 git log**,确认规则真的存在
- 不要"用户说有就有"——验证完再说
- 验证不符时,主动反馈并**给 2-4 个调整方案**,让用户选,而不是"忽略问题继续干"

## 4. 后端注入功能模板(2026-09-09 已落地,可复用)

`solobrave-server.py` 已有达人数据注入架构,新场景优先复用而非重写:

| 已存在的函数 | 用途 |
|---|---|
| `_build_talent_injection(text, auth)` | 关键词命中时查 talents 表 top 50 followers |
| `_is_analysis_conclusion(text)` | 判断 AI 回复是否是"分析结论" |
| `_maybe_auto_save_analysis(agent_id, reply, ...)` | 自动存知识库 + kb_entries + knowledge_events |

**新增的场景**(per-talent 精确注入 / 历史报告 recall)已在 `200f25f` commit 实现:
- `_extract_talent_from_text` — OCR 文本识别具体达人
- `_build_single_talent_injection(talent_id, auth)` — 单达人精确数据
- `_save_talent_report_to_json(talent_id, content, user_id)` — per-talent JSON 存储
- `_VISION_FALLBACK_NOTICE` — OCR 失败的显式提示

未来同类需求,先看这 4 个函数能不能组合用,别从零写。

## 5. 任务交付节奏

用户工作流:**任务描述 → 出方案 → 用户审 → 改代码 → 跑测 → commit + push**。

- **方案阶段**用 4 段结构(现状 / Gap / 改动 / 风险),不要堆砌代码片段
- **明确"待你确认"的 blocking 问题**,用 `ask_user` 而不是堆问题列表
- **改完跑完,主动给 5 张验收图 + 1 段 commit summary**,不等用户问
- **commit 前必跑 `python3 scripts/secret_scan.py --staged`**,退出码非 0 不许提交(见 §7)

## 6. commit message 模板

- 设计系统改动:`refactor(design-system): <要点>`
- 后端功能:`feat(server): <要点>`
- 修复:`fix: <症状>`

每个 commit 前 `git log -1 --stat origin/dev` 确认远端 HEAD,避免本地 commit 错位。

## 7. 密钥管理铁律(2026-10-21 新增,优先级仅次于 §1)

**背景**:2026-10-21 查出 4 把生产 Kimi API key 明文硬编码在 `solobrave-server.py`,
被提交进 git 历史**并推送到 GitHub**。代码改掉了、**历史撤不回**——只能去 Kimi 后台轮换作废。
根因不是笔误,是**流程性漏洞**:没有任何机制在提交前拦住它。
本节 + `scripts/secret_scan.py` 就是那道闸门。

### 规则

1. **密钥只进 `.env`,代码里只读不算**
   - `.env` 已在 `.gitignore`(`*.env`),权限必须 `chmod 600`
   - 代码里一律 `os.environ.get('XXX_KEY')` 读,**不许出现 key 字面量**
   - ⚠️ 读 `.env` 用**绝对路径**(`os.path.dirname(os.path.abspath(__file__))`),
     不要只依赖 `os.environ`——本项目 `_load_dotenv()` 在 `solobrave-server.py`
     **模块末尾**才调用,晚于 key 池创建,纯环境变量方案会**静默失效**
     (2026-10-21 实测踩过:注释写"环境变量优先",实际 100% 走内置明文池)

2. **提交前必跑扫描器,退出码非 0 不许提交**
   ```bash
   python3 scripts/secret_scan.py --staged       # 提交前(只扫暂存区,最快)
   python3 scripts/secret_scan.py                # 工作区全量(跟踪文件 + 未跟踪新文件)★默认
   python3 scripts/secret_scan.py --all          # 同上,显式全量(发版前)
   python3 scripts/secret_scan.py --history      # 扫 git 历史所有版本(轮换后确认残留,约 5 秒)
   ```
   ⚠️ **未跟踪的新文件也算**(2026-10-21 修过一个假安全感):
   旧实现的 `--all` 反而**跳过**未跟踪文件,而新泄漏恰恰都在还没 `git add` 的新文件里
   → "用了 --all 就万事大吉"实际是自欺。现在 `--all` = 真全量,
   另有 `--tracked-only` 给确实只想扫跟踪文件的场景。

   ⚠️ **历史里的密钥改代码不算修**: `--history` 扫出历史里还剩几把、分别在哪些文件,
   那份清单才是要去供应商后台**轮换作废**的名单。

3. **带密钥的备份文件一律放 repo 外**
   ```bash
   cp x.py ~/.solobrave-backups/x.py.bak-<ts>   # 不要留在仓库里
   ```
   原因同 §1:`.gitignore` 对已跟踪文件不生效,而 `git add .` 会把备份一起带走。
   2026-10-21 那个 `solobrave-server.py.bak-kimi-keys-*` 就是这么差点**二次泄漏**的。

4. **已提交的密钥必须轮换,改代码不算修**
   ```bash
   git log --all --oneline -S "<key前缀>"    # 有输出 = 进过历史 = 必须去后台 revoke
   ```

5. **误报登记走 allowlist,不要直接忽略整类**
   `scripts/secret_scan_allowlist.txt` 每行 = `<指纹>|<原因>|<负责人>|<日期>`,
   指纹用 `python3 scripts/secret_scan.py --fingerprint "<值>"` 生成(**不写明文**,
   否则这个文件本身就成了新的泄漏点)。登记项**不阻塞提交但会打印提醒**,处理完删行。

6. **第三种密钥形态:由客户端自持的凭据 —— 代码只读不算,且必须实时读盘**
   有些凭据根本不该进 `.env`,因为**客户端已经在替我们持有并自动续期**
   (2026-10-21 实例:MiniMax Code 订阅的 OAuth accessToken 在
   `~/.minimax/auth/<env>/<region>/mcode-public/auth.json`,Code 桌面端会自行
   续期并重写该文件)。这类凭据:
   - **绝不复制进 `.env` 或代码** —— 复制就等于制造第二份需要轮换的副本
   - **每次调用实时读盘,绝不缓存** —— 缓存下来的短期 token 必然在下次调用时 401,
     表现是「刚开始还能用、跑一会儿全灭」,极易误判成「额度又没了」
   - **过期就跳过,不要让用户等一次必然失败的请求**;401 时可重读一次凭据重试
   - 这类凭据天然不进仓库 → 扫描器扫不到也正常,**不代表它可以进仓库**

### 扫描器基线里的 3 条已知待处理(2026-10-21 登记)

| 项 | 风险 | 处置 | 优先级 |
|---|---|---|---|
| OpenClaw gateway token 硬编码在前端 JS(4 个文件) | gateway 是内网 `ws://192.168.1.25:18789`,公网拿到也连不上 | 前端不直连,改走后端代理(服务端持 token) | P1 |
| 旧 Kimi key 遗留在 `KIMI_API_PROXY_SPEC.md` | 已实测 401 失效 | 文档改成「从 .env 读」的说明 | P2 |
| 飞书多维表格 Base token 硬编码 `v3_scorer.py` | 拿到可读该 Base 的数据 | 迁 `.env`(`FEISHU_APP_TOKEN`) | P1 |

> 扫描器上线时用**负向测试**验过闸门真的会失败(造 4 类真 token → 全部拦下 + exit 1),
> 不是恒真检查。改扫描逻辑后请重跑一次负向测试。

## 8. 商品数据查询:商品ID → 平台接口(2026-10-21 新增)

> ⚠️ **先分清是哪一端**（老大 2026-10-23 铁律）：
> 「**达人数据用团长端看,商品才会用达人端看**」——
> **商品**（商品名/价/佣金/月销/类目/店铺）走**达人端** `material_list`（本节）；
> **达人**（出单/成交/粉丝/GMV/经营概览）走**团长端** `captain/institution/**`（见 §8.1）。

**背景**:老大要求「商品链接直接打开没有用 没有数据 必须拿到商品链接上的商品ID
然后去巨量找这个商品」。实测坐实:商品页正文里**没有商品数据**
(留存 HTML 389KB,`product_id` 出现 0 次,只有「打开抖音APP」壳),
所以「打开链接 → AI 读正文」必然瞎编。

**唯一真数据源** = 达人端选品广场接口(契约与 6 个坑见 `daren_cockpit/README.md`
「商品数据查询」章节,改这块前**先读那一节**):

```
POST https://buyin.jinritemai.com/pc/selection/common/material_list
{"scene":"PCSquareSearch","size":N,"search_text":"<商品ID>",...}
```

### 本项目专属铁律

1. **商品链接采集一律走「解析商品ID → 接口」**(`daren_bot/product_link.py:extract_product_id`
   + `executors.collect_product_by_id`),不许再写「打开页面让 AI 读正文」当主链路。
2. **`scene` 只能是 `PCSquareSearch`**。用 `PCSquareFeed` 时 `search_text` 被静默忽略,
   返回全量流(`total: 2998`)且**不报错** —— 改这个字段等于改坏链路。
3. **接口调用必须用绝对 URL + 页面在 buyin 域**。相对路径按当前页 origin 解析,
   页面停在 haohuo/douyinec 域就会打到错域 → 404。
4. **`code=-2` = 会话失效,不是「商品不存在」**。直接 goto `dashboard` 不触发 SSO 换 session。
   ⚠️⚠️ **禁止用带 `log_out=1` 的 URL 自动重登**(那是**登出**语义,实测把已有会话注销了)。
   登录是人工动作 → 引导扫码 → 轮询等待 → 自动续跑;入口 URL 去掉 `log_out`。
   混淆这两者会把正常商品误报成「已下架」—— 属于把故障写成结论。
5. **登录态检查要查全部域**(`ctx.cookies()`)。达人端 SSO cookie 挂在
   `.buyin-daren-sso.jinritemai.com`,只查 `buyin.jinritemai.com` 永远查不到 → 假阴性。
6. **`/health` 的 `logged_in:false` 可能是「浏览器没启动」**(`Browser.health()` 在
   `ctx is None` 时返回 `(False,False)`)。判定「登录态过期」必须同时看 `*_browser_up`。
7. **查不到 ≠ 报错**。平台查无此商品(已下架/不在推广池)走 `ProductNotFound`,
   **不重试不回退**,直接指路「发个商品截图我照样能看」(vision 链路与浏览器无关,始终可用)。
8. **stats 是完整结构,prompt 不是**。`_product_verdict()` 禁止把 `stats` 整包塞进 prompt ——
   30 天日销曲线会烧 token 并带偏结论(已排除并压成一行摘要)。
9. **等待人工操作(扫码)期间不许动页面**。定时导航会打断老大正在扫的码。
   登录检测必须**状态驱动**:出现「达人工作台」入口 = 扫码完成 → 才跳;没出现就只 sleep+重试。
   系统自动化边界: 引导/检测/等待/续跑 全自动,**扫码永远人工**。
10. **「要标识」≠「答不了」**。达人问某个具体商品能不能带、但没给链接/商品ID/截图时,
    要回「请提供一下商品的链接或者商品ID」(`daren_bot/product_intent.py` 是**唯一事实来源**,
    handler 与 executors 都从那儿取话术),**不许**甩「其他问题帮不上忙」的通用兜底 ——
    那是把「缺一个输入」说成了「答不了」(2026-10-22 老大截图实锤)。
    ⚠️ **说了能收什么就得真能收**:引导语里写了「或商品ID」,路由层就必须收得下裸商品ID
    (17-20 位数字)。而 `DOUYIN_RE`(`^[A-Za-z0-9_]{5,24}$`)会把一串 19 位数字当成抖音号抢走
    ⇒ 裸商品ID 判定**必须排在抖音号之前**,且要**对着库判**(是否本租户已绑定的抖音号 /
    是否已知达人),不能靠猜。改 `handler._dispatch` 的分流顺序前先读这条。

### 8.1 达人查团长端，商品查达人端（2026-10-23 老大纠正 + 补充口径,新增）

老大原话（两句都要记）：

1. 「**达人数据用团长端看,商品才会用达人端看**」
2. 「商品数据在**达人端显示会更全面** —— 因为会展示**这个商品前几位带得好的达人卖的数据**
   还有**那个视频**；团长端也可以找商品,但**数据不会显示前几位达人的数据还有视频**。
   所以我**团长端只查找达人,达人端搜索商品**。」

⇒ **不是「哪端都行」,是按「要什么数据」分工**：

| 端 | profile | 入口 | 拿什么 | 缺什么 |
|---|---|---|---|---|
| **团长端** | `data/buyin_browser/profile` | `buyin.jinritemai.com`(右上角身份=**团长**) | **达人数据**:达人出单/成交/粉丝/GMV/经营概览、合作达人管理、数据参谋 | **查商品只能拿机构出单汇总**(GMV/单量/带单达人数),**没有「前几位带货达人」也没有视频** |
| **达人端** | `data/buyin_browser/profile_daren` | `www.douyinec.com`→达人工作台(选品库) | **商品数据**:选品广场 `material_list`、商品详情的**前几位带货达人 + 视频**、带货情况筛选(月销/浏览量/**带货达人数**) | 无机构级出单汇总 |

⚠️⚠️ **同一个 host、同一路径,`用哪个 profile 就是哪个身份`**：
拿达人端 profile 打 `buyin.jinritemai.com/dashboard/...` 会进「**达人自己的罗盘**」
(`compass_api/author/**`,author=自己),**看不到我们机构的成交数据**。
2026-10-22 我就是这么查错的(结论已作废,见 `docs/PROGRESS-2026-10-22.md` 第七/十三节)。

**团长端真接口**(页面上下文 fetch,**不需要 `a_bogus`**):

```
GET /ecom/captain/institution/statistics/overview?start_time=&end_time=&date_type=
GET /ecom/captain/institution/statistics/promoter/list?...&sort_type=0
    &sort_field=order_product_count&page=&page_size=20&promoter_type=0
    &author_query=&colonel_query=&date_type=1
GET /ecom/captain/institution/statistics/product/list?product_id=&date_type=1
    ← **商品维度**出单明细，`id` = **平台商品ID**（可 `product_id=` 精确查）
GET /ecom/captain/operate_analysis/coop_diagnosis/overview?period_type=&ewid=<机构id>
```

- `captain`=团长 / `institution`=机构 ⇒ 路径名自证是团长视角;我们 `ewid=4e29de4e35955d8ebdfbfffde0354882`。
- **金额单位是「分」**(同 `material_list`),落库换算成元。
- 采集器:`daren_cockpit/tools/captain_stats.py`;
  表:`platform_inst_stats` / `platform_talent_stats` / `platform_product_stats`。
- ⚠️ **平台商品名是全名、我们库里是简称**(`嘭嘭爱心系列人字拖` ⊄
  `Coolchap嘭嘭爱心系列2026沙滩度假女士平底拖鞋人字拖CCF1001`) ⇒ 纯 `in` 判断恒 False,
  必须用 **n-gram 覆盖率**(`name_coverage()`),否则会把「明明同一个商品」判成「没这批人」。
- ⚠️ **`products.id` 是内部 id**(`prod_coolchap_1_4452c0`),**库里没有平台商品ID** ⇒
  现在只能靠名字对拍(实测 46 个出单商品只对上 2 个,44 个在出单的商品没建档)。
- ⚠️ `promoter/list` 少传 `promoter_type/author_query/colonel_query/date_type`
  **不报错、直接返回空数组**;⚠️ 平台会返回**完全重复的行** ⇒ 必须显式去重。
- **达人端商品详情接口**（JS bundle 挖出 + 真实交互验证,⚠️ **裸 fetch 会被风控**:
  `code=10001010A 当前环境存在风险` / `code=11001 当前网络不稳定`
  —— **必须带页面签名 a_bogus/verifyFp,只能在真实页面交互里抓**）：
  - `/pc/diagnose/get_total?ewid=&product_ids=<逗号分隔平台商品ID>`
    （**商品诊断**:风险提示 / **30日动销达人数** / 诊断指标 / 佣金率）
    页面入口 `https://buyin.jinritemai.com/dashboard/product-diagnosis/home`（商品质量诊断）
  - `/pc/selection_tool/promotion/list` + `/pc/selection_tool/promotion/pools`
    （**我的橱窗 / 最近带货 / 选品车** —— 达人端的商品来源池）
    ⚠️ **只作接口参考，我们不用它扒达人数据**（2026-10-24 老大纠正：
    达人端只做「按商品ID搜商品」）。
  - `/api/authorStatData/productRelatedLives`（商品相关直播）、
    `/api/authorStatData/salesAnalyseV2`（带货分析）、
    `/api/authorStatData/authorRepresentWorksList`（达人代表作品/视频）、
    `/api/authorStatData/similarAuthor`（相似达人）、
    `/api/benchmark/product/deal`（商品成交对比）

#### 两端能力矩阵（2026-10-23 实测,别再猜哪端能干什么）

| 要什么 | 团长端 `profile` | 达人端 `profile_daren` |
|---|---|---|
| 达人档案 / 出单 / 成交 / GMV | ✅ 直连 fetch | — |
| 机构经营概览（支付GMV/订单量/成交达人） | ✅ 直连 fetch | — |
| 机构商品出单汇总（GMV/单量/带单达人数） | ✅ 直连 fetch | — |
| 商品基础信息（价/佣金/月销/类目/店铺/体验分） | ❌ | ✅ `material_list`（**需签名**） |
| 商品的带货达人数/浏览量/榜单/投千川标记 | ❌ | ✅ 选品库列表 |
| 商品诊断（风险提示/**30日动销达人数**） | ❌ | ✅ `/pc/diagnose/get_total`（**需签名**） |
| 商品相关直播 / 视频 / 相似达人 | ❌ | ✅ `/api/authorStatData/*`（**需签名**） |

⇒ **达人端商品数据更全面但必须走页面交互;团长端能直连但只有汇总。**
⇒ 所以 **查达人 → 团长端;查商品 → 达人端**（老大手工分工,不是随意选的）。
- ⚠️⚠️ **`talents.douyin_id` 混装了三类值**（2026-10-23 实证，**推翻了上一轮「它只是字段名骗人」的说法**）：
  ① **平台达人 id**（`Wgg292`/`hlb725`/`372168571`/`69097875157`）
  ② **真抖音号**（`31693707031`/`372443191`）
  ③ **脏值**（`@limantou200w`/`原来是安娜`/`鲸鱼姐姐`/`__speedtest__`）
  ⚠️⚠️ **靠格式无法自动判别** —— 平台达人 id **也可能是纯数字**（`372168571`、
  `69097875157`），所以「纯数字 ⇒ 真抖音号」是**错的**（我踩过）。
  ⇒ 唯一可靠判据是**对着平台真值 join**。
  ⇒ **已整改**（`daren_cockpit/tools/fix_talent_ids.py`）：加 `platform_talent_id` +
  `douyin_uid` 两列（**`douyin_id` 一根手指不动** —— 它是对外契约：
  创建查重 / 前端 `accountId` / 飞书统计卡筛选 / 租户 `bind_douyin_id` 都靠它），
  三路 join（平台id > 真抖音号 > 昵称精确）回填 7 行，删 1 条确认空壳重复档案。
  ⇒ 商品侧同理：`products.platform_product_id` 已加，**商品对拍主键从 n-gram 换成平台ID**。

### 8.2 商品建档：两端合流 + 强证据裁决（2026-10-23 新增）

**为什么建档**：团长端 46 个出单商品，我们库只对得上 2 个，**44 个没建档且占 GMV 99.97%**；
我们库 91 个商品里 90 个零出单 ⇒ 推荐的跟赚钱的几乎零重叠。

**两端字段互补，缺一不可**：
- 团长端 `product/list` **只有 8 个字段**（无价格/佣金/类目/月销）
- 达人端 `material_list` 有全套，但**必须带页面签名**
  ⚠️ **`headless=True` 会被风控**（`code=11001`），生产链路是 `headless=False`
  ⇒ **一律走 `buyin_worker` 控制口** `POST http://127.0.0.1:8095/collect-product-id`，
  **不要自己克隆 profile 起无头浏览器**（既占 500M+ 磁盘，又拿不到数据）。

**工具**：`daren_cockpit/tools/filing_products.py`（`--dry-run` 默认 / `--apply` 才写库，
自动备份 products 表 + 整库副本；达人端补数据落 `data/filing-enrich-cache.json`）。

#### ⚠️ `buyin_worker` 运维：两个浏览器**互斥** + 重启会掉登录态（2026-10-24 实测）

- **团长端**（`profile`）和**达人端**（`profile_daren`）**同一时刻只能开一个**：
  切换时日志会打 `[BrowserLoop] 切换浏览器：先关闭 达人端（profile_daren）`。
  ⇒ `/health` 里 `browser_up` 和 `daren_browser_up` **不会同时为 true，这是设计不是故障**；
  发对应端的任务会自动切换过去。
- **重启 worker 会掉浏览器**（`launchctl kickstart -k` ⇒ 浏览器进程被杀）。
  ⚠️ **正确姿势**：重启前先 `POST http://127.0.0.1:8095/graceful-daren`
  （优雅关闭达人端浏览器，触发 cookie 落盘），再 kickstart；
  重启后按需 `POST /open`（团长端）或 `POST /open-daren`（达人端）拉起。
  ⇒ 实测：直接 kickstart 后 `daren_logged_in=false`，
  但**盘上 cookie 还在**，重新 `open-daren` 后自动恢复 `daren_logged_in=true`（无需扫码）。

**三条铁律**：
1. **三个「达人数」必须分列** ——
   `influencer_count`(平台全商品带货达人数, 达人端) /
   `talent_count`(我方合作达人数, `product_talent_match` 自动维护) /
   `platform_order_daren_count`(我方机构出单达人数, 团长端)。
   口径不同，混用必错。
2. **n-gram 只出候选，不自动挂** —— 覆盖率**分不清「同系列不同款」**
   （实测 `裸穿微光 7800-2327` vs `繁花编织 7800-2315` = **0.79**，却是两个型号）
   ⇒ 挂载要求**到手价一致 + 佣金率一致**的**强证据**；不满足 ⇒ 按新商品建档。
   ⇒ 配套**冲突消解**（同一条我方记录只能挂一个平台商品），否则贪心会让 A 抢走 B 的候选
   ⇒ **宁可漏挂，不可错挂**（错挂会锁死后续精确匹配）。
3. **`created_by` 不能留空** —— `/api/products` 对非管理员按 owner 过滤
   （`visible_ids = {uid} | 该用户的 AI 员工 id`）⇒ 空归属的行**租户端完全看不见**，
   建档等于白做 ⇒ 取存量里最常见的非空 `created_by` 作归属，**不写死 ID**。

### 8.3 商品维度补齐 + 选品推理链 + 推荐指数（2026-10-24 新增）

**起因**:老大给了抖音达人「选品AI助手」的推荐截图,拍板「**两边都要**」——
补商品维度 + 搬推理链 + 自己算推荐指数。

#### 达人端真值接口（本轮实测定位,别再猜）

| 用途 | 接口 | 关键字段 |
|---|---|---|
| 商品基础维度 | `POST /pc/selection/common/material_list` | 到手价/佣金/月销/**日销量轴**/**店铺体验分**/**好评率**/**带货达人数**/类目三级(+id)/平台标签 |
| **榜单排名** | 同上 → `custom_model.selection_square_model.recommend_reason` | `extra.rank` + `rank_type`/`date_type`/`genre_type`/`leaderboard_name` + 展示原文（如「露趾凉鞋橱窗带货常销榜·第5名」） |
| **近7日短视频销量** | `POST /pc/leaderboard_26/board_pmt` | `custom_model...board_data.rank` + `board_sale.sale_num`（`date_type=7d, genre_type=video`） |
| 榜单筛选字典 | `GET /pc/selection_square/get_filter` + `POST /pc/leaderboard_26/get_board_default_info` | 榜单类型/售价/佣金/体验分/周期/体裁 + **类目树(含 cate_id)** |

⚠️ **四条坑**（都是这轮踩的）:
1. **页面路由不能直接 goto** —— `/merch-picking-hall/rank` 直接跳会显示
   「链接失效啦」；**必须进 SPA 后点导航**（左导航无 `<a href>`，是 JS 路由）。
2. **响应体大小会腰斩 JSON** —— `board_pmt` `count=100` 单页 ≈ 440KB；
   worker 里的切片上限必须 ≥ 2MB，否则 `code=None`、解析失败（先踩 20KB、再踩 400KB）。
3. **`board_pmt` 不认 `search_text`** —— 传了也返回默认总榜；只能按类目拉榜。
4. **`get_board_default_info` 的类目树是嵌套的** —— 一级类目在
   `industry_list[].child_cate_list[]` 里（`cate_level=0`），别只看顶层。

#### ★★ 页面实证：达人端**能**用商品ID搜到商品（2026-10-24 老大要截图核对）

> 老大原话：「商品ID是否可以搜索到商品，你把搜索之后的页面截图给我，我需要知道是否准确」。

**结论：能，而且精确命中唯一商品。** 3 个真实平台ID 实测（页面实拍，非接口推断）：

| 商品ID | 页面搜到的商品 | 到手价 | 佣金 | 店铺分 |
|---|---|---|---|---|
| `3668788850777522321` | Coolchap【迪丽热巴同款】夏季加密印章系列小香风沙滩凉鞋CCF2006 | ¥179 | 20%（投放期8%） | 92 |
| `3679179617945649473` | 夏季新款百搭欧式女款海边软底编织麻花度假沙滩人字拖CAF1021 | ¥159 | 5% | 92 |
| `3684758647369498887` | 哟酷酷（夏日度假）美式AA杯垫刺绣露脐百搭运动内搭美背吊带背心 | ¥55 | 10% | 89 |

**平台是真拿 ID 去查的**（不是关键词模糊匹配撞出来的）—— 搜索时浏览器实际发出：
- `GET /pc/selection/search/sug?query=<商品ID>&ewid=...`（联想建议）
- `POST /pc/selection/common/material_list` body 含
  `{"scene":"PCSquareSearch","size":30,"search_text":"<商品ID>","cursor":0,...}`
  ⇒ `search_text` **就是商品ID本身**。

**页面数据 vs 我们库：100% 一致**（到手价 / 佣金率 / 店铺分逐项对得上）
⇒ 「达人把商品ID发给我们 → 我们查到对应商品」这条链路**前提成立**。

**入口**（踩坑记录）：
- 达人工作台根路由 `https://buyin.jinritemai.com/` 会**重定向回官网** `www.douyinec.com`；
  官网落地页**没有任何输入框**（`inputs: []`），别在那儿找搜索框。
- 真正带搜索框的是 **`https://buyin.jinritemai.com/dashboard/merch-picking-library`**
  （落地后就是「选品广场」页，顶部有搜索框）。
  ⚠️ 直达 `selection-square` / `daren-square` / `exclusive-selection-square/rank`
  三个路由都**没有可见输入框**。
- 复现工具：`POST http://127.0.0.1:8095/daren-search` body `{pid, tag, url?}`
  ⇒ 真·页面输入 + 回车 + 截图（`darensearch_<tag>_输入.png` / `_结果.png`），
  结果看 `GET /daren-search`（含 body 正文 + 本次触发的接口列表）。

#### 没有平台真值的维度（如实留空，别造）

- **转化率**：❌ 平台**没有通用商品转化率接口**。
  `/api/anchor/product_diagnose/search|detail` 只诊断**我们自己达人账号**的商品
  （实测 `total=0`）；`/pc/diagnose/get_total` 返回风控码 `11001`。
  ⇒ `products.conversion_rate` 保持 NULL，评分器把它当「缺失维度」。
  ★ **2026-10-26 已清库**：137 行假值（131 行 `0.0` + 6 行 `3.5`）全部清成 NULL
  （`product_talent_match` 的 342 行同清）⇒ 实测推荐输出**一位不差**
  （321 条展示 / 764 样本）—— 转化率维度本就因覆盖率 4% < 60% 被整体剔除。
  ⚠️ 但**假值必须清**：覆盖率闸门挡不住「假值凑够 60% 覆盖」⇒ 会翻身参与打分。
  ⇒ 代码侧也堵死了（示例种子不写死数字 / 导入不落 0 / prompt 不写「转化率：0%」），
  守门人 `tests/test_conversion_rate_truth.py`（含 AST 扫描）。详见 **§16 H**。
- **推荐指数★★★★★**：❌ 平台**没有这个接口** —— 达人端那个是 **AI 助手自己算的**。
  ⇒ **我们自己算**（见下）。

#### 工具

- `daren_cockpit/tools/daren_dim_collector.py` —— 达人端商品维度采集 → `products` 新列。
  `--dry-run`（默认）/ `--apply`（自动备份 CSV）。缓存 `data/daren-dim-cache.json`。
- `daren_cockpit/tools/product_reco.py` —— **选品推理链 + 推荐指数评分器**（纯函数，无网络无 DB）。
- API：`GET /api/products/recommend?talent_id=xxx&top_n=6`
  → `{profile, strategies, recommendations, strategy_match, notes}`。

#### products 新增列（全部 **NULL = 没有真值**，0 = 平台真给 0）

| 列 | 含义 | 来源 |
|---|---|---|
| `shop_score` | 店铺体验分 0-100 | material_list |
| `good_ratio` | 好评率 % | material_list |
| `sales_7d` | 近7日**总**销量（sale_axis 自算） | material_list |
| `video_sales_7d` | 近7日**短视频**销量（榜单口径） | board_pmt |
| `board_rank` / `board_rank_type` / `board_date_type` / `board_genre_type` / `board_name` / `board_reason` | 榜单排名 + 口径 + 平台原文 | recommend_reason |
| `sale_axis_json` / `category_ids` / `daren_synced_at` | 原始轴/类目id/采集时间 | — |

⚠️ **两个「近7日销量」口径不同，必须分列**：`sales_7d`（总销量，覆盖全部商品）
vs `video_sales_7d`（短视频口径，**只覆盖榜内商品**）。混用必错。

#### 评分器铁律（`product_reco.py`，改之前先读）

1. **缺失维度不当 0 分** —— 没采到的维度**退出加权 + 重新归一化**，
   输出带 `missing_dims` / `dropped_dims` / `completeness`。
   （把「没采到」当 0 分 = 用数据的缺失惩罚商品。）
2. **池内覆盖率闸门** —— 任一维度在池内覆盖率 < 60% ⇒ **整个池**都不用它
   （口径才可比；且要如实报 `dropped_dims`）。
3. **星级 = min(绝对分星级, 池内分位星级)** —— 绝对分做**地板**（防假绿灯），
   分位做**区分度**（绝对分挤在一起时「清一色 5★」没意义）。
   ⚠️ 绝对阈值**按真实分布校准**（当前 `(85,5),(78,4),(68,3),(50,2)`），
   **校准必须用脚本重跑**，别手算：
   ```bash
   python3 daren_cockpit/tools/reco_calibrate.py            # 看分布 + 是否还成立
   python3 daren_cockpit/tools/reco_calibrate.py --json     # 导出直方图（贴进测试）
   python3 daren_cockpit/tools/reco_calibrate.py --check    # CI 门禁（不成立 exit 1）
   ```
   ★ **校准的主口径 = 用户实际看到的推荐列表**（`collect_displayed`），
   **不是**策略池全体候选（`collect_samples`）——
   「清一色 5★」「假绿灯」都是**用户看得见的列表**上的说法；
   拿几百个候选当校准对象会得出误导结论（实测候选池 5★ 占 29%，
   但展示口径只有 21%，阈值其实没问题）。
   当前展示口径：323 条 ｜ 1★:70(22%) 2★:63(20%) 3★:84(26%) 4★:38(12%) 5★:68(21%)。
4. **候选池 < 3 ⇒ 星级封顶 3★** —— 池内没区分度时别给假绿灯。
5. **策略类目要落到商品池真实写法** —— 画像写「鞋靴/凉鞋」、商品写「鞋靴/靴子」、
   平台一级类目叫「鞋靴箱包」⇒ 只比全等会**候选数归零**（踩过）。
6. **只丢不造** —— 画像缺客单价/偏好类目 ⇒ 明说「画像不足」，不编粉丝画像。
7. ★ **商品事实解析（佣金率/到手价）只有一份** —— `daren_cockpit/tools/product_facts.py`。
   推荐器和排品器**都从它取**，别各写一套（口径分叉 = 同一商品在两个评分器里
   读出不同佣金 = 静默漂移）。详见 §8.4。

#### 铁律延续（§8.1）

- 商品维度**只走达人端**；**绝不把我方出单数据带进给达人看的推荐里**。
- `/api/products/recommend` 的商品池**按 owner 过滤**，口径与 `/api/products` 一致
  （数据隔离优先级最高）；H5 驾驶舱 `daren_cockpit/server.py` 是**直读 DB**，不受此限。


### 8.4 排品表 + 每日提醒（2026-10-24 新增）

**起因**：老大原话——「AI 助手一直是围绕着自己的达人账号做分析的」，
「达人手里每天要接很多的品，那这些品就需要 AI 帮忙安排，提醒」。
账号分析/选品建议都有了，缺的是**接了品之后怎么排、什么时候推、什么时候提醒**。

#### 载体：复用 `deals` 表，不另起表

`deals` 早就设计好状态机（`pending→negotiating→sample_sent→approved→live→completed/failed`）
+ `scheduled_at` + 实际结果回填字段，但**零使用** ⇒ 直接当排品池用。

`deals` 新增 5 列：

| 列 | 含义 |
|---|---|
| `priority` | 排品优先级分 0-100（匹配度40% + 收益35% + 时机25%） |
| `plan_slot` | 当天第几个坑位（1..N） |
| `plan_reason` | 排品理由（留痕，便于复盘「为什么那天推它」） |
| `deal_source` | 品从哪来（达人自选/商家推品/AI推荐/链接咨询） |
| `remind_state` | 提醒幂等标记（当天日期） |

进排品池的状态 = `negotiating` / `sample_sent` / `approved`（**不含** `pending`，
刚提还没答应的不算「接了的品」）。

#### 工具与入口

- `daren_cockpit/tools/product_plan.py` —— **排品算法**（纯函数，无网络无 DB）：
  `product_lifecycle()` / `build_talent_fit()` / `score_item()` / `build_schedule()` / `plan_text()`。
- `daren_cockpit/tools/product_facts.py` —— **商品事实单一真源**（佣金率/到手价解析）。
- `daren_bot/plan_notify.py` —— 飞书侧：`register_deal()` / `plan_for_talent()` /
  `plan_text()` / `build_reminder()` / `scan_and_push()` / `PlanScheduler`。
- API：`GET /api/deals/plan?talent_id=&days=&per_day=`（只算不写）、
  `POST /api/deals/plan`（写回 `scheduled_at`/`priority`/`plan_slot`/`plan_reason`）。
  ⚠️ 路由必须排在 `startswith('/api/deals/')` **之前**，否则被当成 `deal_id='plan'`。
- 飞书（**零成本**，不调 LLM、不扣积分）：
  - 「接了 `<商品ID>`」→ 登记进排品池（`ACCEPT_DEAL_RE`，
    ⚠️ **必须排在裸商品ID分支之前** —— 否则 19 位平台 ID 会被「查商品」抢走）。
  - 「今天推啥 / 排品表 / 我的排品」→ 直接回表（`PLAN_QUERY_RE`）。
  - 每日 09:37 自动推「今天推哪两个 + 哪个该清了」（灰度 `daren_boss`/`daren_test2`）。

#### 排品池的真源 = **我们自己系统里的合作单**（`deals` 表）

> 老大原话（2026-10-24 20:26）：**「选品车？我说了达人端只是一个搜索商品的功能才会用到，
> 其他的不要碰」**。

排品池 = `deals` 表里状态为 `negotiating`/`sample_sent`/`approved` 的合作单，
来源三条，全部在我们自己的系统里：

| 来源 | 入口 |
|---|---|
| 运营/团长建单 | `POST /api/deals`（后台） |
| 达人在飞书说「接了 `<商品ID>`」 | `plan_notify.register_deal()` |
| 达人提合作意向 → 运营通过 | 合作申请管道 |

⚠️ **不要用达人选品车当真源**（曾走错，已下线）：
- 选品车是达人在**平台侧**的私事，不是我们的业务数据；
- 达人端只有一个登录账号 ⇒ 采到的只会是**那一个人**的选品车，归属直接串掉；
- 属于「拿达人端当数据源乱扒」，越界。

⇒ 已下线：`daren_cockpit/tools/cart_collector.py`（可恢复删除）、
`plan_notify.sync_cart_pool()`、调度器的自动同步、`scan_and_push(sync_first=…)` 参数。
⇒ 库里的采集残留（2 商品 + 2 合作单）已清理，回退到 products 137 / deals 5。

**达人端浏览器（`buyin_worker`）的边界**：**只做「按商品ID搜商品」**
（`/daren-search` + `/daren-fetch` 抓该商品维度用于建档/推荐）。
**别的都不碰** —— 不爬选品车、不扒达人业务数据。

<details>
<summary>（已废弃，仅留档）当年实测的平台选品车契约 —— 不要照着做</summary>

| 用途 | 接口 | 关键字段 |
|---|---|---|
| 当前登录达人身份 | `GET /index/getUser` | `user_name` / `origin_uid` / `shop_id` / `user_id` |
| 选品车商品 | `POST /selection_cart_pc_api/card/m_get_card` | `data.cart_view_card[].cards[].product_card` |
| 选品车角标 | `POST /selection_cart_pc_api/comm/m_get_cart_sum` | `data.cart_sums[]` |

商品卡口径（★ **单位坑**，搜商品建档时仍要用）：
- `base_product_info.price` = **分**（6800 ⇒ ¥68.00）
- `business_operation_info.cos_ratio` = 佣金率**万分比**（1000 ⇒ 10%）
- `ad_cos_ratio` = 投放期佣金万分比（600 ⇒ 6%）
- `base_shop_info.exp_score`（店铺体验分）/ `shop_name`
- `base_product_info.new_category_id` = 一级/二级/三级类目 + 类目id

</details>

#### 排品表 H5 页面（2026-10-24 老大要「拍品表做成 h5」）

- 独立页 `daren_cockpit/plan.html` → `http://127.0.0.1:7188/plan`（运营口令保护）
- 数据源 `GET /api/plan?talent_id=`（运营口令和 `x-ops-key` 头）
- 达人视图（`/?t=<tenant>`）里也内嵌「排品表」区块（`load_cockpit` 返回的 `plan` 字段）
- 运营工作台右上角有「排品表」入口
- ★ **按达人列，不按租户列**：运营后台建的合作单，达人不一定绑了飞书租户
  （实测：七月姐姐有 5 单但没租户 ⇒ 按租户列就看不到她）

#### ⚠️ 租户↔达人身份会错位（同一个坑，第二次踩）

实测：`daren_test2` 配的 `bind_douyin_id=324733241`（萌萌超会买），
但老大实际在飞书聊的是 `Ayn11930`（一百）——
**查错人 ⇒ 排品表/提醒全落到别人的数据上，看起来就是「这功能没做」**。

⇒ **判据顺序**（`db.get_latest_douyin_id()`）：
1. 该租户**最近一次抖音号分析**请求里的号（达人自己报的，最真）
2. 回退 `bot_tenants.bind_douyin_id`

⇒ 另加 `talents.platform_nickname`（达人端昵称），
提醒标题用昵称而不是库里的 `name`（实测库名是「我的小号发给你跟我分析一下名称一百」，
直接用在标题里没法看）。

#### 排品表 + 提醒铁律（`product_plan.py`，改之前先读）

1. **优先级 = 匹配度40% + 收益35% + 时机25%**，质量只做**扣分项**（差品压后）。
2. **匹配度三态** —— 无类目数据 =「不知道」（该半权重退出，重新归一化）；
   有数据但没这个类目 =「真的不匹配」= 0 分（实情）；匹配上 = 占比。
   ⚠️ 「有数据但没这个类目」≠「没类目数据」。
3. **生命周期**（`sale_axis` 近7 vs 前7 + 绝对量闸门 `MIN_DAILY_MOVING=1.0`）：
   爬坡/爆款/衰退/尾货/未知。⚠️ 绝对量闸门不能省 ——
   实测有品近7天只卖 1 件，「从 0 涨到 1」不叫爬坡期。
4. **同品类不排在相邻两个日历日**。
   ⚠️ **口径是「日历日」不是「上一个有排品的日」** —— 后者（跨过空档也算连推）
   会让品类集中的池子**死锁**：实测 4 个同类目的品、7 天、每天 2 坑，
   旧口径只排得出第 1 天，剩下的品永远「排不进本周」；
   而老大的诉求是「这些品都需要 AI 帮忙安排」⇒ **约束不能把品丢掉**。
5. **补位兜底** —— 严格规则排完还剩的品，往空坑位里补（窗口太紧时允许同品类连排，
   并在理由里标注「同品类连排（窗口太紧…）」）。
   ⚠️ 补位必须放在**所有天都排过一遍之后**，否则第 2 天会把第 3 天的品抢过来。
6. **同品去重** —— 同一个达人 + 同一个品的重复合作单只占**一个**坑位
   （挑「已排期 > 有商品名 > 有佣金率 > 先出现」的那张留）。
7. **尾货每天最多占 1 坑**（`TAIL_SLOTS_PER_DAY=1`），且**不挤主推位**（好品在前）。
8. ★ **`scheduled_at` 单位是「秒」**（前端 `scheduled_at * 1000` 读）——
   写成毫秒 ⇒ 前端日期差 50000 年。`tests/test_product_plan.py` 有单位锁点用例。
9. ★ **同一天排了多个已排期的品，一个都不能丢** ——
   `scheduled` 要按天存**列表**（早期用 `dict[date] → 单个 item`，
   同天第二个品会**凭空消失**）。

#### 提醒幂等：落在 DB，不靠进程内存

`remind_state` 存当天日期。`build_reminder()` 在「今天该推的都推过了」时返回空
⇒ **机器人当天重启也不会重推**（`PlanScheduler._last_day` 是内存态，重启即清空）。
⚠️ 标记必须发生在**推送成功之后** —— 先标记再推会吞掉提醒。

#### 写入侧幂等（`POST /api/deals`）

同一个达人 + 同一个品，只在排品池里存在一条时，**直接返回那条**（`duplicate: true`），
不再新建 —— 否则重复品把排品坑位占满（实测踩过：同一品被登记两次，排品表两个坑推同一个品）。

#### 佣金率口径：平台有两种形态（`product_facts.py`）

| 形态 | 例子 | 读法 |
|---|---|---|
| 标量 | `{"default": 20.0, "投放期": 8}` | 取 `default` |
| **分档** | `{"投放期": 10, "常规活动期": 5, "其他": 5}` | ⚠️ **取最高档**，并标注档位名 |

⚠️ **只认 `default` ⇒ 分档结构的商品的收益维度被静默丢掉**
（实测 5/58 个商品；reco 权重 12%、plan 权重 35%）——
而且**不报错**，只是评分口径和平台真值对不上。
理由行要写清「佣金率最高 10%（投放期）」，不能糊成「佣金率 10%」。

### 8.5 排期必须落库（2026-10-24 实测根因）

> 症状：老大说「排品提醒没做」；修完入口后当天推对了，
> 但**第二天会把一模一样的两个品再推一遍**。

#### 根因：同一个算法有**两个消费者**，只有一个落库

| 消费者 | 入口 | 落库？ |
|---|---|---|
| 服务端 | `POST /api/deals/plan` | ✅ 写回 `scheduled_at`/`priority`/`plan_slot`/`plan_reason` |
| 机器人 | `daren_bot.plan_notify.scan_and_push()` | ❌ **只算不写** |

⇒ 达人侧的 `deals.scheduled_at` **永远是 0**
⇒ `build_schedule()` 里「已有排期的保持原位」这条规则**从来没生效过**
⇒ 每天从头现算，而 `base` 随日期平移
⇒ **今天的坑位 = 明天的坑位 = 一模一样的两个品**。

实测探针（`今天算出来的今天` == `明天算出来的明天` ⇒ `True`）。

⚠️ 这类 bug **不会报错、不掉日志、HTTP 200** ——
它是「安排」这件事在产品上**根本不存在**，而不是「安排算错了」。

#### 修法

1. `plan_notify.persist_plan(slots, talent_id)` —— 排期写回 `deals`。
2. `scan_and_push()` 里 **先落库、后推送**（`dry_run` 一个字都不写）：
   - 先落库 ⇒ 推送失败时排期已在，下一轮还能扫到这个品 ⇒ **可重试**；
   - 反过来先推后落库，中间崩了就白推一次。
   - 落库失败**不阻断推送**（宁可少一次安排，也不能今天不推）。
3. `build_schedule()`：**过去日期的 slot 不接收新品** ——
   落库后 `scheduled` 会带出过去的排期并生成一个过去的 slot，
   不拦的话新品会被排到**已经过去**的那天 ⇒ 那个品**永远推不出去**。

#### 配套铁律

- **过期未推的品要退回重排**（`requeue_overdue()`）：
  排期停在过去那天 + **从没推过**（`remind_state` 为空）⇒ 清零 `scheduled_at` 重排。
  ⚠️ 只认「从没推过」才补 —— 已经推过的（哪怕推晚了）**不重复推**
  （「错挂比漏挂贵」）。否则某天整批推送失败 ⇒ 那个品**再也不会被推**。
- **`plan_text` 不展示过去的日子**（那是历史，不是待办）。
  落库后 `build_schedule` 会把过去的排期原样带回来。

#### ⚠️ 变异测试必须绕开 `.pyc` 陷阱（本次真踩）

改文件跑测试时，若「变异」与「还原」发生在**同一秒**且**文件长度相同**
（本例 `if slots:` → `if False:` 等长），Python 的 `.pyc` 校验
（**mtime + size**）会判定缓存有效 ⇒ **复用变异版的字节码** ⇒
「已还原」的代码跑出来仍是变异结果。

⇒ 变异测试纪律：**改完 `sleep 1.2` 让 mtime 跨秒**，
并清掉仓库自己的 `__pycache__`（别去清 venv 的，慢）。
⇒ 否则会得出「修复无效」的错误结论（本例真的被骗了一轮）。

### 8.6 卡片化带出的 3 个真 bug：同名遮蔽 / 漏 import / 结论口径漂移（2026-10-24 新增）

卡片化（`56bdf96`）之后一次跑通老大会话，抓到 **3 个真 bug**，
其中 2 个是**上线后才生效**的（改共享模块 ⇒ 重启进程 ⇒ 冷路径被激活）。

#### ① 同名函数定义 = 静默遮蔽（最贵的一个）

`executors.py` 里同时有两个 `_product_verdict`：

```python
def _product_verdict(tenant_key, stats) -> str:   # LLM 出结论（933/1252 行调用）
def _product_verdict(report) -> (label, tpl):     # 卡片取结论（后定义）
```

Python「后 def 覆盖先 def」⇒ 卡片那个把 LLM 那个**整个遮蔽**了 ⇒
`execute_product_collect`（商品链接/商品ID 分析）和 `execute_image_vision`（截图识别）
里的两参调用直接 `TypeError`：

```
TypeError: _product_verdict() takes 1 positional argument but 2 were given
```

⇒ **达人发任何没缓存过的商品 ID、或发一张截图，商品分析 100% 失败。**

**为什么一直没被发现（两条，都要记住）**：
- 缓存命中的路径走早返回，压根碰不到被遮蔽的函数；
- 现有单测用 `monkeypatch.setattr(ex, "_product_verdict", lambda *a, **kw: ...)`
  —— **把自己的被测对象整个替掉了**，替身接受任意参数 ⇒ 遮蔽被完美掩盖。
  ⇒ **探针必须能失败的前提是「探针不能被替身顶掉」**。

**修法**：改名消歧（`_llm_product_verdict` / `_card_verdict`）+ 加自动闸门：
- `tests/test_no_duplicate_defs.py` —— AST 扫 `daren_bot/` + `daren_cockpit/`
  所有模块，**模块级作用域不得有同名函数/类定义两次**（含 `if/try` 里的 def）。
- 同文件还锁「卡片结论 == 档案结论」（见下面 ③）。

#### ② 漏 import：NameError 藏在从没被测过的冷路径里

`handler.py::_card_view_plan()` 用了 `datetime.datetime.now()`，
但该模块**没 import datetime** ⇒ 点「看我的排品表」按钮必然 `NameError`
⇒ 回调返回「排品表算不出来」。

⚠️ IDE 不报、`py_compile` 不报、`pytest` 也不报 —— **因为那条路径从来没被测过**。

**修法**：`tests/test_no_undefined_names.py` —— 用 `symtable` 静态扫
「函数里引用了、但模块作用域没定义、也不是内置/dunder」的全局名 ⇒ 直接判红。
（顺带扫出 `executors._anthropic_messages` 薄壳引用了未导入的
`_anthropic_messages_impl` —— 那是个没人调的**死壳**，谁碰谁炸，已补 import。）

#### ③ 结论口径漂移：卡片说「能带」，档案说「慎带」

同一份 LLM 报告，卡片和结论档案用了**两套解析**：

| | 旧实现 | 结果 |
|---|---|---|
| 卡片 `_card_verdict` | 全文档扫词，固定顺序先判「能带」 | 理由句里「未达到8%的**能带**标准」命中 ⇒ 真结论「🟡 慎带」被标成「🟢 能带」+ 绿色 |
| 卡片 `_card_summary` | 按 `"▎结论"` 切分 | 真实报告标题是 `## 结论`，`▎结论` 全仓只出现在它自己的 docstring 里 ⇒ **摘要恒为空** |

⇒ **达人看到的结论是反的**（该慎带的品标成能带）。

**修法（单一真源）**：卡片一律复用档案口径
（`_verdict_label` / `_verdict_lines` / `_verdict_reason`）。
`_verdict_label` 本身也加固成「**先认 emoji，再认整词**」——
prompt 锁死了「🟢 能带」「🟡 慎带」「🔴 别带」三选一，emoji 是唯一无歧义锚点；
整词判定还能挡住「**不能带**」被当成「能带」。

#### 配套铁律

- **改共享模块后必须跑一遍它的所有入口**，不能只跑自己这次改的那条。
  本次就是「下线选品车采集线」时重启了 daren-bot ⇒ 把一条**从未被执行过的路径**激活了。
- **卡片/档案/多维表格读同一个字段 ⇒ 解析规则只写一份**，
  多处复用同一个函数（别再各写一套）。
- **写调用方和被调方同名 ⇒ 等于给自己埋雷**，命名必须能区分职责
  （`_llm_xxx` / `_card_xxx` / `_archive_xxx`）。



### 8.7 排品池的遍历范围 + 同品类约束必须算上「已排期」的品（2026-10-25 新增）

老大确认卡片没问题后，我查调度时发现两个更深的缺陷。
它们同族：**同一份事实，多个消费者，但遍历范围/口径不一致。**

#### ① 排期与重排只遍历「有飞书租户的达人」⇒ 没租户的达人品会卡死在过去

`requeue_overdue`（过期未推 ⇒ 退回重排）和 `persist_plan`（排期落库）
原本都长在 `scan_and_push()` 里，而 `scan_and_push` 只遍历**灰度租户绑定的达人**。

⇒ 没绑飞书租户的达人（实测：**七月姐姐 5 单，没租户**）：
- `requeue_overdue` 够不着她 ⇒ 过期未推的品**不会退回重排**
- `persist_plan` 也不碰她 ⇒ 新排期**不会落库**
⇒ 她的品排期永远停在过去那天；在 H5 排品表里（只显示今天及以后）**直接消失**，
  运营看着像「少排了」，其实是**卡在昨天**。

**修法**（新增 `plan_notify.reschedule_pool()`）：
- 遍历范围 = **排品池**（`deals` 里所有有单的达人），不是「有租户的达人」
- 排品池真源是**我们的合作单** ⇒ 池子里的人都得被排
- 分工：**推送给有租户的达人**（飞书侧的物理限制），
  **排期算给池子里所有人**（排期是池子的属性，不是租户的）
- 服务端**不用改**：运营建单只负责入池，排期由每日调度统一算

⚠️ 推论：**任何「按租户/按绑定关系遍历」的逻辑，都要问一句
「排品池里没绑定关系的那部分怎么办」**。
（与「面向按人的视图要按人列」同源。）

#### ② 同品类约束把「已排期（保留原位）的品」漏在视野外

`cat_used_days`（每天用过的类目）原来**只在主循环末尾记账**，而主循环开头有
`if dstr < today_str: continue` ⇒ **过去的日子整段被跳过，从不记账**；
未来的保留位也要等排到那天才记账 ⇒ 排 10-25 时**看不见 10-26 已占位的品类**。

⇒ 约束只在「当天新排出来的品」之间生效，**已排期的品完全游离在约束之外**。
实测：10-25 新排了鞋靴、10-26 保留原位的也是鞋靴 ⇒ 相邻两天同品类。

**修法**：
- 排期前先 **seeding**：把所有「已排期（保留原位）」的品的品类记进 `cat_used_days`
- 约束比较**前后各一天**（`_prev_day` + `_next_day`）：排 10-25 既要避开 10-24 已推的，
  也要避开 10-26 已占位的

⚠️ **收窄口径的边界（这次踩到）**：一开始还把「当天」也加进约束，
结果「同一天推两个同品类」被禁掉了 —— 但那是**既有语义**
（达人一天接 2 个同品类品就一起推，锁在
`tests/test_plan_notify.py::test_reminder_title_two_items`）⇒ 越界，得撤回。
⇒ **改约束前先查清楚「哪些是既有语义（有测试锁着）」**。

#### 配套铁律

- **约束口径 = 「相邻两个日历日」**，不是「上一个有排品的日」
  （后者跨空档也算连推 ⇒ 品类集中的池子死锁 ⇒ 品排不进本周）
- **约束用于排序，不用于淘汰** ⇒ 排不下时破例补位并在理由里标注
  「同品类连排（窗口太紧，破例补位）」
- 排期落库**必须先落库后推送**（推送失败可重试）；落库失败不阻断推送
- `requeue_overdue` 只认 `remind_state` **为空**才补（已推过的不重复推，
  「错挂比漏挂贵」）⇒ 它带 `dry_run` 参数，探针不写库


## 9. 复用层:抓一次存下来,下次直接读(2026-10-21 新增)

**背景**:老大要求「第一次抓取数据必须存到我们的系统知识库,下次达人需要数据直接调用
存储的数据即可(省算力),一周之后再给达人重新抓取」。

三层缓存(实现见 `daren_bot/db.py`,判定入口见 `daren_bot/handler.py`):

| 层 | 表 | TTL | 隔离维度 | 命中省什么 |
|---|---|---|---|---|
| 商品数据 | `bot_product_cache`(已有) | 7 天 | 全平台共用 | 开浏览器采集 |
| 选品结论 | `bot_product_verdicts`(新增 `product_key`/`md`/`stats_json`) | 7 天 | **按租户** | 采集 + LLM 结论 |
| 问答回复 | `bot_qa_cache`(新增表) | 1 天 | 按租户 | LLM |

### 本项目专属铁律

1. **复用判定必须放在入队处**(`handler._route_qa` / `_route_link`),不能放执行处。
   放到执行处判定就晚了 —— LLM 已经烧掉了,省不下算力。
   命中则 `cache_hit=1` + 扣缓存价(问答 0 分 / 商品 5 分)。
2. **商品数据全局共享、选品结论按租户隔离**。结论依赖**该达人画像**,
   同一个商品给不同达人结论可以不同 —— 跨租户复用结论 = 把 A 的画像喂给 B。
3. **含时效词的问题一律不缓存**(最近/今天/最新/现在/本周/近期/几点…) ——
   缓存住等于给假的新鲜度。判定在 `db.qa_cacheable()`。
4. **拒答/兜底话术不入缓存**。那是失败态不是答案,缓存住等于下次把「拒」发出去。
   (免费重采那条分支是提前 `return`,天然不会落缓存。)
5. **`bot_product_verdicts` 是结论档案,不是缓存** —— 过期判定在**读的时候**做
   (`verdict_cache_get`),历史行一律保留做审计/回溯。启动只清 QA 缓存
   (`purge_expired_caches()`)。别拿「清缓存」的名义删档案。
6. **结论标签只认「结论行」**(`executors._verdict_lines()`)。
   禁止「在前 60 字里搜第一个『能带』」—— 理由句里也可能出现「能带」,
   会把「🟡 慎带」误标成「🟢 能带」,写进档案后审计就废了。
7. **验证必须能失败**:复用是否真短路,用「把 LLM/采集函数换成一调用就炸」证明,
   不要靠读代码说它短路了。回归见 `tests/test_daren_cache_layer.py`(21 条),
   线上探针见 `python3 daren_bot/tools/cache_probe.py --mode probe`(问答路径)
   / `--mode probe-product`(商品路径,需达人端登录态有效)(两者自清理、不发飞书)。

### 「一周之后重新抓取」落在哪

- **商品/结论**:`PRODUCT_CACHE_TTL` / `VERDICT_CACHE_TTL` = 7 天,到期自动回源重抓。
- **达人账号数据**:`weekly.py` 的采集窗(周日 20:00 → 周一 08:00)主动重采 +
  `bot_weekly_snapshots` 周快照。**注意**:采集窗是「每周必采」,
  不受 7 天新鲜度约束 —— 跳过会缺周快照 → 周报走「数据缺失版」→ 达人体验变差。

## 10. 视觉同源铁律:H5 驾驶舱 ↔ 飞书 BaseApp(2026-10-22 新增)

> 触发事件:老大「两个设计风格没有一样,不然会以为是两个系统」。
> 查证发现**两端展示的是同一个达人(hlb725 洁洁爱户外)的同一套数据**
> (飞书统计卡的 filter 就是 `抖音号 = hlb725`),所以「像两个系统」是真的,不是错觉。

### 铁律

1. **同一句话必须同色。** 结论/状态的「词 → 颜色」映射有**唯一事实来源**:
   `daren_bot/verdict_ui.py`。飞书 Base 单选字段的 `options.hue`、H5 的 `.v-*` 徽章底色、
   H5 的 `VERDICT_EMOJI` JS 映射全部由它约束。
   - 修之前的割裂(留个教训):H5「潜力型」绿底 / 飞书**紫**标签;
     「🟢 能带」飞书标签是**红色**(emoji 与颜色反了);「待评估」H5 琥珀 / 飞书**蓝**。
   - **改色 = 改四处**,漏一处就漂回去。
2. **守卫必须能失败。** 改任何一边之前先跑
   `python3 daren_bot/tools/verify_visual_sync.py`(全查)/ `--offline`(只查本地 H5),
   漏改立刻 exit 1。**已反向验证**:故意改错 H5 emoji / 飞书 hue 各报一次红。
   新增检查项时同样要反向验证一次,别写恒真断言。
3. **飞书字段值直接喂前端拼类名之前,先归一化。**
   「商品推荐记录·结论」的选项名是 `🟢 能带`(**带 emoji**),「账号档案·评估结论」是裸词
   `潜力型`。H5 原来直接 `v-${verdict}` 拼类名 → 拼出 `v-🟢 能带` 这个**不存在的类**
   → 结论标签**掉底色**(裸文字),和飞书带色标签完全不像。
   归一化用 `verdictKey()`(H5 JS) / `verdict_ui.verdict_key()`(Python,孪生,守卫对拍)。
4. **产品名两边必须同名。** H5 顶栏品牌头 = 飞书 App 顶栏 App 名 = 「达人经营驾驶舱」。
   H5 早期**连产品名都没有**(达人视图直接从账号卡开始) → 用户不知道自己在哪。
5. **口径句统一用「数据来自我们系统」**(不是「由」),两边逐字一致。

### 飞书 BaseApp 的 API 边界(实测,别再试)

| 想做的事 | 能不能 | 怎么办 |
|---|---|---|
| 改块的**内容** | ✅ 能 | `+app-block-update --data-config '{"text":"..."}'` |
| 改块的**名字** | ❌ 不能 | 服务端不接受 `name` 字段(恒 `API error:[1]`,raw PATCH 同样挂)→ **只能 UI 改名** |
| 新建块时定名 | ✅ 能 | `+app-block-create --name ...` |
| 调整块**顺序 / 尺寸 / 标题显示** | ❌ 不能 | 不是公开字段 → **只能 UI 拖** |
| 改**主题色** | ⚠️ 不能自定义 hex，**但能切预设** | API 侧 `--theme-style` 只在 `+app-create` 生效（6 套，无 hex）；**已有 App 可在编辑模式顶栏「外观」面板随时切**（`.base-app-style-comp-icon`，含绿系「清新」）→ 见坑 33 |
| 改块**底色 / 描边 / 高度 / 图表色板** | ❌ 不能 | 版式与配色**不在公开 Create/Update 协议内**（CLI 明文）→ **只能 UI**，见坑 31/34/35 |
| 改**单选选项配色** | ✅ 能 | `+field-update`(**PUT 全量语义**:先 `+field-get` 读回完整定义再整体提交) |
| 读坏块 `wgtfRsGRrubM2pLU`(选品横幅) | ❌ 不能 | CLI 和 raw API 都是 `API error:[1]` → **不能写**(写了=瞎编内容) |

> ⚠️ `API error:[1]` 是不透明错误码,**含义不唯一**。排查手法:**分解探针** ——
> 一次改两个字段失败时,拆成「只改 A」「只改 B」各试一次,立刻定位是哪个字段的锅。

## 11. LLM 通道铁律:降级环只有一份(2026-10-22 新增)

老大原话:「**kimi 现在没额度,只能你来**」——「你」= **Code 订阅 OAuth 通道**
(`agent.minimax.cn/mavis/api/v1/llm/v1`,Anthropic 协议)。

### 唯一事实来源

**`daren_bot/llm_channels.py`** —— 环构造 + 凭据读取 + 两种协议调用,全在这里。

| 东西 | 在哪 | 谁在用 |
|---|---|---|
| 文字降级环 | `llm_channels.text_rings()` | 问答链(`executors`)+ 采集/摘要链(`buyin_worker`) |
| 识图降级环 | `llm_channels.vision_rings()` | 同上 |
| 凭据读取 | `env_api_key()` / `openclaw_api_key()` / `code_sub_token()` | 同上 |
| 协议调用 | `call_ring()`(单 prompt)/ `call_ring_messages()`(多消息) | 同上 |

`executors.py` 与 `buyin_worker.py` 里的 `_fallback_rings_daren` /
`_text_fallback_cfgs` / `_fallback_cfgs` **全是薄壳**,只做转发。
**别再在任何一个文件里手写环。**

### 环序(LIFO,最稳的排最前) —— **2026-10-26 当场实测**(老大拍板摘除智谱后)

| 环 | 通道 | 协议 | 凭据 | vendor | pool | 实测 |
|---|---|---|---|---|---|---|
| 0 | MiniMax 套餐 API | Anthropic | `.env` `MINIMAX_CP_API_KEY` | minimax | cp-subscription | ✅ 1.1s |
| 1 | **Code 订阅(=贾维斯自己)** | Anthropic | `~/.minimax/auth/**/mcode-public/auth.json` | minimax | code-subscription | ✅ 0.9s |
| 2 | MiniMax 套餐 API | OpenAI | 同环 0(协议冗余) | minimax | cp-subscription | ✅ 1.2s |
| 3 | kimi moonshot-v1-32k | OpenAI | OpenClaw key store | kimi | kimi-monthly-quota | ❌ 429 月度额度耗尽 |
| 4/5 | minimax 开放平台 | OpenAI | `.env` `MINIMAX_API_KEY` | minimax | minimax-open-balance | ❌ 402 余额不足 |

识图链:环0 MiniMax(✅) → kimi(❌429) → 开放平台(❌402)。
（原来的环1「智谱 `glm-4v-flash`」**已摘除** ⇒ 识图链也只剩 MiniMax 一家。）

### ⚠️⚠️ 独立服务商 = **1 家**（MiniMax）⇒ **冗余归零，该买第二家 API**（2026-10-26）

老大 2026-10-26 终审口径：**智谱资源包只做知识库 embedding，不拿来做 LLM 对话**
⇒ `daren_bot/llm_channels.py` 的智谱文字环 + 智谱识图环**全部摘除**（含常量/符号一并删，
防复活后门）。

⇒ **回答老大「要再买 API 吗」：要买。**
  链上有 5 个环，但 **能顶的独立服务商只有 MiniMax 一家**
  （环0/1/2 全是 MiniMax 的三条路；环3 kimi 429；环4/5 开放平台 402）
  ⇒ MiniMax 一家出问题时全线崩 ⇒ **冗余是假的**。

★ 这条「缺口」被**钉在测试里**（`tests/test_llm_channels.py::test_current_chain_alive_independent_vendors_is_one`）：
  有人接回第二家服务商时该测试会红 ⇒ 逼着同步更新环序表与结论，不让它悄悄变。

### ★★「有几环」≠「有几家」(2026-10-25 实测教训)

改造前链上 6 环、3 个「可用」—— **实则全是 MiniMax 一家**
(`api.minimax.cn` 套餐 / `agent.minimax.cn` Code 订阅 / `api.minimaxi.com` 同一把 key 的另一条协议)。
**一家挂了,三个环同时死** ⇒ 冗余是**假的**。

⇒ 每个环现在都带两个**显式字段**(写死在 `llm_channels` 构造环时,不许下游反推 host):

| 字段 | 答什么 | 取值 |
|---|---|---|
| `vendor` | **一家挂了是不是全家死** = 判「要不要买第二家 API」 | `minimax` / `kimi` |
| `pool` | **还剩几种能顶的额度**(同家的不同账/不同协议也算不同池) | `cp-subscription` / `code-subscription` / `kimi-monthly-quota` / `minimax-open-balance` |

⚠️ **别从 host 反推 vendor**:`api.minimax.cn` / `agent.minimax.cn` / `api.minimaxi.com`
三个不同 host 是**同一家**;而套餐 key 与开放平台 key 同属 MiniMax 却是**两套独立的账**
(订阅额度 vs 账户余额)⇒ 开放平台 402 **不影响**套餐继续用。两个维度都不能互相替代。

### 智谱摘除的口径(2026-10-26 老大终审)

- **老大原话**:「智谱资源包是知识库（embedding）的 API」⇒ **不拿来做 LLM 对话**。
- ⇒ 对话链 + 识图链的智谱环**全部摘除**;`llm_channels` 里**连常量都不留**
  （`VENDOR_ZHIPU` / `POOL_ZHIPU_PACK` / `ZHIPU_*` / `_zhipu_channel` 全部删除）
  ⇒ **口径变更必须改代码才能生效**，不是一个常量就能绕过去的。
- ⚠️ **对话流量在偷知识库额度**：对话链与知识库 embedding **共用同一把 key / 同一个资源包**
  ⇒ 智谱对话环每跑一次 = 知识库少一分可用额度。
- **智谱只在服务端视觉 QA 里保留**（witness OCR 逐字校验）—— 那是**校验**不是对话，
  且实测 glm-4v-flash 的转录精度明显优于 MiniMax-M3
  （MiniMax 会把「验收」转成「验饭」、「接的品」转成「接的作品」——
  witness 是**权威依据**，错字会把正确字段「修」错）。
  ⇒ 但它必须用**独立配置源**，见下一节。

### ★★ §11.1 视觉 QA 的配置源必须**独立**于对话链（2026-10-26 实测踩坑）

★ **病根 = 两份语义不同的事实共用一份配置源**：
  视觉 QA 的 witness/repair 原来读 `settings.llm.providers`（**对话链的档位表**）
  里 name 含 zhipu 的那一档。对话链按老大口径摘掉智谱档后,
  **视觉 QA 被一起带断** —— 实测 `_vision_qa_cfg()` 返回 None
  ⇒ 字段提取精度从 95%+ 退回「只跑常识规则」（~67%）。

⇒ **修法 = 拆成两个配置源**（不是「再摘一个档」）：

| 消费者 | 配置源 | 用途 |
|---|---|---|
| **对话链 / 识图链** | `settings.llm.providers` + `.env` | LLM 推理（**不含智谱**）|
| **服务端视觉 QA** | `settings.visionQA` 段（+ env `SOLOBRAVE_VISION_QA_*` 兜底）| witness OCR + 修复 pass |

⚠️ **配套三条**：
  1. 视觉 QA 走 **OpenAI 兼容**协议（`image_url` 形态）⇒
     provider 配成 `minimax`/`anthropic`/`kimicode`（Anthropic 协议）会**静默走错分支**
     ⇒ `_vision_qa_cfg()` 显式拦下并降级，不硬撑。
  2. `base` 必须剥掉 `/embeddings` 后缀（实测踩过 → 404）。
  3. 模型名也归 cfg（`witnessModel` / `repairModel`），不许每个 pass 各自读 env。

⇒ 守门人：`tests/test_vision_qa.py::test_vision_qa_cfg_ignores_conversation_chain_provider_list`
  + 变异 M10（配置源跑回 `llm.providers` ⇒ 必须红）。

### 降级链顺序也有讲究(2026-10-25)

降级链是**顺序遍历**的 ⇒ **活环必须排在死环前面**。
kimi(429)/开放平台(402) 排在 MiniMax 三环后面 = 每次降级都先白等两轮 HTTP 失败
(飞书侧体感就是「卡十几秒才回一句没反应」)。


### 为什么必须收敛(这轮踩到的真 bug)

改动前有**两份独立拷贝**的环(第三例「同源同 bug」),且:
- 采集/摘要链那份**只支持 OpenAI 协议**(`_openai_chat` 打 `/chat/completions`),
  而 Code 订阅环是 **Anthropic 协议**(打 `/messages`)⇒ **就算把 token 塞进去也调不通**;
- 结果:采集链只剩套餐 key 一个活环 —— 套餐一挂就全灭,
  而**「报告摘要」正是这条链生成的**(`summarize_for_notes` → `llm_chat_fb`)。

**已验收**:掐掉套餐 key 后,采集/摘要链靠环 1 跑通(实测 1893ms 生成 139 字摘要,
万单位/GMV 档位与飞书卡片一致)。测试见 `tests/test_llm_channels.py`。

### 能力边界(实测,别再试)

- **Code 订阅环不能读图**:Anthropic 协议传 base64 图片 → `529 overloaded_error`。
  ⇒ 识图环**不许**放它(放进去每次识图都白等一次失败)。
- 识图/视频链只能用 OpenAI 协议的环。

### 协议坑

- Anthropic 的 **system 是顶层字段**,不是 `messages` 里的一条
  ⇒ OpenAI 形态的 messages 直接塞进 `/messages` 会**丢掉 system 提示词**
  (表现为「模型不知道要干什么」)。`llm_channels._split_system()` 负责拆。
- 两种鉴权头:套餐 key 用 `x-api-key`,Code OAuth token 用 `Authorization: Bearer`
  ⇒ 环里用 `auth` 字段**显式声明**,不靠猜。
- `kimi/moonshot` 是 reasoning 模型,**强制 temp=1.0**;
  其余通道尊重传入值(出结论类任务要低温求可复现)。

### 探针

`python3 daren_bot/tools/probe_llm_channels.py [--vision]` —— 逐环真调一次。

⚠️ **识图链的判据**:1×1 像素图里没有商品,模型**诚实返回空字段是正确行为**
(「不编造」原则),**不等于通道死**。旧判据把它当死亡 ⇒ 报过「识图链全灭」的
**假警报**。现在分三态:`alive`(有回复)/ `empty`(通但空)/ `dead`(真错误)。
文字链同款三态。

---

## 12. 飞书 BaseApp 运维铁律(2026-10-22 新增)

> 详细实测记录见 `docs/DAREN-DASHBOARD-TEMPLATE.md` 坑 25–28。

### ⚠️ 先说最贵的一条:飞书 App 有「编辑模式」

**URL 加 `&mode=edit`** 进入编辑模式,此时才出现:

| 能力 | 选择器 | 说明 |
|---|---|---|
| 拖拽块 | `.widget-container-header-drag-handle` | 18×16 小图标(**不是整个块头** —— 拖块头会变成文本选择,卡住不动) |
| 改尺寸 | `.resize-handle-{nw,ne,sw,se}` | 四角 12×12,拖 `se` 角可同时改宽高 |
| 块菜单 | `.widget-container-block-menu-config-button` | 24×24,hover 块头才出现 |
| 配置抽屉 | `.setting-panel-container` | 右侧 350px,Tab:基础配置/自定义配置/数据说明 |
| 保存 | 顶部「**完成编辑**」 | **改完必须点它**,否则不落盘 |

- 块菜单**按块型分两套**:
  图表块 = 配置/复制/背景描边/移入/删除;
  **text 块 = 复制/更换背景色/背景描边/尺寸/移入/删除(没有「配置」)**
  ⇒ text 块**改名只能「新建同名块 + UI 删旧块」**(`+app-block-create --name` 一次写对)。
- **「自定义配置」里有「小数精度」(整数/保留 1~8 位小数)** ——
  指标卡的小数位**只能在这里改,API 改不了**。
- 改完**要点「完成编辑」**。

### 指标卡取整(实测,别再当谜案查)

指标卡对数值**一律取整**,且**不看字段 style**:
公式 1.3→`1`、number+precision=1 的 1.3→`1`、number+precision=2 的 12.5→`13`;
**只有千分位生效**(12804→"12,804")。
文本字段喂给指标卡 = `0`(`rollup: MAX` 对 text 无效)。
⇒ **万单位小数要两头一起做**:字段用 number + `style.precision=1`
(列表/表格按字段格式显示 1.3),指标卡另在 UI 设「小数精度=保留 1 位小数」。

⚠️ **API 改图表块的 `data_config` 会把 UI 设的「显示设置」冲回默认** ——
实测:UI 设好「保留 1 位小数」(卡显示 1.3),再用 API 改一次 `series.field_name`,
打开配置面板精度**已变回「整数」**,卡上又显示 1。
⇒ **改完 `data_config` 必须回 UI 重设显示设置,并且重新渲染读一次卡面文字才算数**
(别拿「API 返回 ok」当验收)。详见 `docs/DAREN-DASHBOARD-TEMPLATE.md` 坑 29。

### 定位块的正确姿势

- **图表/列表/指标卡的块用 `data-ssr-widget-info` 里的 widget id**(`position.i`),
  **text 块用 `data-id`** —— ⚠️ 两种块**标识属性不同**,2026-10-22 用错属性导致
  「元素明明在页面里,选择器却一个都匹配不上」,白排查半小时。
- **不要用块标题定位图表块** —— text 块在 DOM 里**没有标题元素**(按标题定位必然失败);
  反过来 **text 块按标题文字定位最稳**(`.react-grid-item` 里 `innerText.includes(标题)`),
  前提是块已进 DOM。
- ⚠️ **widget id 不能手抄**:本轮把 `wgtfRsGRurbM2pLU` 手抄成 `wgtfRsGRrubM2pLU`
  (u/r 位置颠倒)⇒ 所有选择器静默失配。**对着页面真值核**(读 `getAttribute` 的字符码),
  或干脆**按标题文字定位**。
- 飞书**只渲染视口内的块**,屏幕外的块连 DOM 都没有 ⇒
  拖拽前把 Playwright 视口加高(`{'width':1600,'height':2400}`),整页进 DOM 才拖得动。
- 视口 1600 宽时网格→页面映射(本样板 4 列实测):
  `page_x = 304 + grid_x*0.859`,`page_y = 108 + grid_y`。
- **图表是 canvas 渲染**,DOM 里读不到 `fill/stroke` ⇒ 取色验收只能看
  **截图**或**配置抽屉里的色板预览**(`.color-series-item` 的 computed backgroundColor)。
- ⚠️ **渲染有延迟**:同一页不同块渲染完成时间不同(实测 2.5s~9s),
  截图太早会拍到**空块**(本轮误以为「档案页文字丢了」,实际是截图早了)。
  ⇒ 验收前**轮询等块 innerText 有内容**再截图,别赌固定 sleep。

### 块菜单飞层 / 主题面板的交互姿势(2026-10-23 实测)

- **二级飞层要「带轨迹的鼠标移动」才展开**:`mouse.move(x-60,y)` → `mouse.move(x,y,steps=12)`。
  单点瞬移、`locator.hover()`、JS `.click()`、`locator.click(force=True)` **全部不触发**
  (本轮连废 8 个探针脚本)。症状极具误导性:菜单能开但面板永远
  `offsetParent===null`,报错像「面板没渲染」。
- ⚠️ **DOM 里有多份同名副本**(颜色面板:块菜单里一份 `display:none`、
  图表抽屉里一份)⇒ **一律按「可见」筛,别用 `.first`**;
  `.first` 会命中隐藏副本,报错「element is not visible」且值是默认 `#000000`。
- **text 块改高度**:块菜单「尺寸 → 高度 → **适应内容**」,治两行标题被裁切
  (本轮 62px → 90px)。`宽度`/`高度` 各有「默认 / 适应内容 / 填充容器」档位,
  ⚠️ **别误点宽度那组**。
- **text 块底色可填精确 hex**(「更换背景色」→ 更多颜色 → HEX),
  **描边不行** ——「背景描边」只有粗细档位(`跟随主题/0/0.5/1/2/3/4px`),
  描边色**只能跟随 App 主题色**。
- **App 主题可切**:编辑模式顶栏「**外观**」按钮(`.base-app-style-comp-icon`)
  → 「外观设置」→ 主题 6 套(默认/云蓝/**清新**/柔光/未来/科技),
  **清新 = 白底 + 青绿点缀**,是唯一绿系。
  ⚠️ 必须点 `.theme-box-img` 缩略图(点外层不生效);⚠️ **别按坐标点**
  (面板位置随右侧 AI 面板开合漂移,本轮点进过「应用模式 AI」面板)。
- ⚠️ **「API 没有这个字段」≠「平台不支持」**:本轮曾据 CLI 文档
  「`--theme-style` 只在 create 生效」就断言「主题色改不了、只能新建 App」,
  实测**编辑模式外观面板一键可切** —— 写「做不到」结论前必须找过 UI 入口。

### 下「平台不支持」结论之前

**穷举可用态**。本轮教训:只看浏览态(只读容器)就下了
「改名/拖拽/删块 UI 也做不到」,进编辑态后**全部推翻**。
跟「cookie 在 ≠ session 有效」同型 —— **一个态的证据不能推全局**。

## 13. 飞书机器人多账号绑定(2026-10-23 新增)

**架构**:飞书应用 → OpenClaw `channels.feishu.accounts.<id>` →
`bindings[{agentId, match:{channel:"feishu", accountId}}]` → SoloBrave AI 员工 →
会话落 `memory_conversations`(知识库 L0)。

### ⚠️ 加新账号**不能**走 `POST /api/openclaw/channels/feishu`

那个接口以 **`tenant_id` 当 account_id** 写入。本项目**只有一个租户 `t_default`**,
且 `t_default` 已经被「租户Bot → Ray」占用 ⇒ 走那个接口会
**覆盖现有账号凭据 + binding**,把线上租户Bot搞坏。
⇒ 加新账号走 `openclaw config patch`（脚本:`bind_feishu_mumu.py`）:
**新增** account_id,`bindings` 全量读-改-写(先删同 accountId 旧 binding 再加新的)⇒ 幂等。

### 密钥一律进 secret store,配置里只放引用

既有 3 个账号的 `appSecret` 都是引用式:
```json
{"source":"store","provider":"default","id":"FEISHU_APPSECRET_<ACCOUNT_ID大写>"}
```
⇒ 新账号也要 `openclaw secrets store set FEISHU_APPSECRET_XXX --kind secret --value-file <文件>`,
配置里写引用。**明文写进 `openclaw.json` 会被 `openclaw secrets audit` 报
`PLAINTEXT_FOUND`**,与本项目存储模型不一致。
- `--value-file` 走文件,**不要 `--value` 走命令行**(会进 shell 历史 / ps)。
- 存完要 `openclaw secrets reload` + `openclaw daemon restart` 才生效。

### 生效判据(别只看配置写成功)

```bash
tail -n +$((BEFORE+1)) /tmp/openclaw/openclaw-$(date +%Y-%m-%d).log \
  | grep -oE "feishu\[<id>\]: (bot open_id resolved: ou_[a-z0-9]+|WebSocket client started)"
```
- `bot open_id resolved` ⇒ 应用凭据有效、机器人身份解析成功
- `WebSocket client started` ⇒ **长连接已建立**（事件订阅走长连接，不是 webhook）
- 出现这两行才算**真绑好**;`config patch` 成功**不等于**连上了。
- ⚠️ 日志里那条 `SECRETS_REF_IGNORED_INACTIVE_SURFACE` 告警是**既有的**
  (顶层 `channels.feishu.appSecret` 没有账号继承),与新增账号无关。

### 可见性:新增账号在系统里原本**看不见**

`GET /api/openclaw/channels/feishu/status` 原本只返回 `default` 一个账号 ⇒
多账号场景没法在系统里确认绑定。已加 `accounts[]` 字段(纯增量,不改原有键),
列出所有账号的 `id/name/appId/boundAgentId`(密钥脱敏)。

### 群策略

新增测试账号:`allowFrom:["*"]`(私聊放开)但 `groupPolicy:"allowlist"` +
`groupAllowFrom:[]` ⇒ **不会被拉进任何群**。

## 14. 知识库分层与 L0 回填(2026-10-23 新增)

### ★★ 语义(embedding)用智谱 —— 老大买的资源包(2026-10-23)

**老大原话:「语义是智谱, 我买的资源包」—— 这条被强调过很多遍, 别再查错。**

| 项 | 生产真实值 |
|---|---|
| provider | **zhipu** |
| model | **`embedding-3`**(2048 维) |
| baseUrl | `https://open.bigmodel.cn/api/paas/v4/embeddings` |
| 配置位置 | **`.env`** 的 `SOLOBRAVE_EMBEDDING_PROVIDER/API_KEY/BASE_URL/MODEL` |

- **`get_embedding_config()` 优先级 = 环境变量 > settings.json > 员工自身 AI 配置**
  ⇒ 生产走 `.env` 里的智谱; `data/settings.json` 里那个 `embedding.provider=siliconflow`
  是**备用配置**, `.env` 一旦加载就轮不到它。
- ⚠️ **在 server 进程外跑脚本时必须自己加载 `.env`**, 否则会读到 settings.json 的
  siliconflow ⇒ 报 `402 Payment Required` ⇒ **误判成「生产的语义服务挂了」**。
  实测: 我据此报过一次「siliconflow 频繁报错」, 生产日志里 `HTTP 402 from` 实际 **0 次**。
  ⇒ **apply when**:任何「调 embedding 失败」的排查, 先确认进程有没有加载 `.env`。
- ⚠️ **改完 `.env` 必须重启 server**(launchd 只设 PATH, 进程启动时 load 一次 .env)
  ⇒ 不重启的话服务端**还在用旧模型**, 症状是「重算完了但检索仍报 429」。
- **智谱的错误码习惯**: 余额/资源包不可用返回 **HTTP 429** 且 body 是
  `{"error":{"code":"1113","message":"余额不足或无可用资源包,请充值。"}}`
  ⇒ **别按状态码猜原因**(429 通常被当成限流), 必须读 body。
- ⚠️⚠️ **`1113` 文案有误导性**: 老大资源包明明有余额、也没动过, 但 `embedding-2`
  就是报「余额不足或无可用资源包」—— 实测该资源包**覆盖 `embedding-3`, 不覆盖
  `embedding-2`**。⇒ **同一 key 同���时刻, `embedding-3`/`embedding-3-pro`/`glm-4-flash`
  全通, 只有 `embedding-2`/`text_embedding` 报 1113** ⇒ **按「逐模型探测」定位, 别信文案**。
- 存量健康度(2026-10-23): **790/790 chunk 全部 `embedding-3` / 2048 维**。

**★ 换语义模型必须连存量一起重算**:
- RAG 召回 SQL 硬条件 = `c.embedding_model = <query模型>`
  ⇒ 存量是旧模型时, **静默不召回**(不报错, 只是搜不到 —— 最隐蔽的失效)。
- 切换流程: ①备份 db ②改 `.env` 的 `SOLOBRAVE_EMBEDDING_MODEL` ③跑
  `scripts/reembed_kb_chunks.py --apply`(直调 `get_embedding` **绕开 cache**) ④**重启 server** ⑤验召回。
  实测 790 chunk / 279s / 0 失败。
- `embedding_cache` 主键 = `(content_hash, model)` ⇒ 按模型隔离, 不会串味(但重算仍显式绕开 cache 更保险)。

**★ 缺向量的坑(已修)**:
- RAG 召回条件 = `e.status='ok' AND c.embedding IS NOT NULL AND c.embedding_model=<模型>`
  ⇒ 无向量的条目**完全不被召回**(不是「召回差」, 是「召不回」)。
- 向量化失败必须留痕: `_vectorize_kb_entry` 失败时写 `kb_operation_log` 'embedding_failed'
  ⇒ **不要改 `status`** —— status 是审核闸语义(仅 ok/pending), 管理员审核会覆盖,
  覆盖后成「假绿灯」条目(看着正常但永远补不回向量), 比不改更坏。
- 补齐入口: `scripts/backfill_missing_embeddings.py`
  按「**有 chunk 但 embedding IS NULL**」扫(与 `kb_entry_stats.pendingChunks` 同口径),
  **不靠 entry.status**。默认 dry-run, `--apply` 才写; 带连通性探测, 探测失败主动早退。

**两条独立的整理链**,别混:

### 链 A:记忆分层(`memory_pipeline.py`,agent 级,**自动**)

| 层 | 表 | 2026-10-23 实测行数 | 触发 |
|---|---|---|---|
| L0 结构化对话 | `memory_conversations` | **2780** | 每次对话结束 |
| L1 事实原子 | `memory_atoms` | 254 | **每 5 轮** + 价值闸 |
| L2 场景聚合 | `memory_scenes` | 19 | L1 后延迟 10s,间隔 ≥15min |
| L3 长期画像 | `memory_personas` | 1 | 每 50 条新原子 |

- **价值闸**(`value_classify_batch`):规则预过滤(<1ms,无 LLM) + LLM 批分类
  ⇒ 闲聊/废话**不进** L1(整批无价值时连提取 LLM 都不调)。
- 召回时 `recall_with_budget` 按 token 预算把 L3/L2/L1 注入 system prompt。

### 链 B:主题知识沉淀(`brain_knowledge_service.py`,按主题触发)

`induct_topic_to_knowledge(topic_id)` → LLM 把同一主题的记忆归纳成 1-3 条结构化知识
→ `knowledge_base_new`;另有 `detect_conflicts` 做知识冲突检测。
⚠️ **实测 `knowledge_base` / `knowledge_base_new` 都是 0 行** —— 这条链目前没跑起来,
不是「数据都在」。

### ★★ 订正:知识库「真表」是 `kb_entries`,不是 `knowledge_base`(2026-10-23)

**上一版 §14 写「知识库 0 行」是查错表了 —— 老大产品上的「知识库」界面一直有 636 条。**

| 表 | 行数 | 谁在用 | 结论 |
|---|---|---|---|
| **`kb_entries`** | **636** | 前端 `GET /api/knowledge/entries` → `ks.kb_entry_list` | ★ **真表**,老大界面读它 |
| `knowledge_base` | 0 | 废弃(`_upsert_knowledge_base` 注释明写「旧表已废弃,不再写入」) | 死表 |
| `knowledge_base_new` | 0 | 链 B `brain_knowledge_service` | 链 B 未跑 |

- 前端:`js/inline-07.js` 的 `sb2KbInit()` → `_sb2KbEntries` → `GET /api/knowledge/entries`
  (SB2 路径**不读** legacy 的 `_knowledgeData` —— 那条路径在 SB2 下永远空)。
- 旁证:`kb_entries.domain` 默认 `'tuangzhang'`;`ks.KB_DEFAULT_DOMAIN` 写死在 INSERT 里。
- **教训**:「知识库空着」这类结论必须先确认**前端到底读哪张表**,
  否则会把「我没找对表」当成「系统没数据」(同「探针结论必先对库再下判断」)。

### ★★ 知识库入库分两档(老大 2026-10-23 口径,已落地)

老大原话:「**分析之后类的就需要完全提取然后放入知识库,
但有一些很平常的就整理出来再放入知识库**」

| 档 | tier | source | 判定 | 处理 |
|---|---|---|---|---|
| **A 分析之后类** | `analysis` | `auto_analysis` | 成型结论(评级/核心结论/表格/结论句式) | **完全提取** —— 原文一字不改整段入库 |
| **B 平常类** | `daily` | `auto_daily` | 碎片素材(一行一字段/一句建议) | **整理后** —— 归并同主题 → 抽要素 → 结构化 |
| 人工录入 | `manual` | `manual` | 界面手工建 | 原样入库 |
| 历史数据 | `legacy` | `legacy` | 迁移前的老行 | 如实标 legacy,**不假装是某一档** |

**实现**:
- `knowledge_intake.py`(新):`classify_tier()` 判档 / `organize_daily()` 整理 /
  `merge_daily_fragments()` 同主题归并。**纯函数**,不碰 DB、不发网络请求。
- `solobrave-server.py`:
  - `_auto_check_knowledge()` —— 两档分流(记忆保存时触发)
  - `_maybe_auto_save_analysis()` —— 档 A 主入口,标 `tier=analysis`
  - `_daren_kb_ingest()` —— 画像洞察/匹配规律⇒analysis;显式反馈⇒daily
  - `_upsert_knowledge_base()` —— **真落库** tier/source(见下)
- `kb_entries` 加了 `tier` / `source` 两列(`init_kb_entries_db` 幂等迁移),API 已透出。

**三条铁律**:
1. **只丢不造** —— 整理只做「归并/剪裁/重排/抽要素」,不改写语义、不补原文没有的信息。
   单元测 `test_no_fabrication` 断言每个值都必须在原文出现过。
2. **宁可漏不可造** —— 整理不出结构 ⇒ **不入库**(纯碎片如「达人ID：tal_xxx」直接拒),
   不硬凑空壳条目。改前库里有 234 条「知识点：」碎片大半是这类垃圾。
3. **不确定就退回原文** —— 档 A 完全提取路径**不经过**任何剪裁逻辑。

**★ 又一处「声明端 ≠ 使用端」**:
`source='auto_analysis'` 这些值**以前只写在调用方字典里、从不落库**
(`kb_entries` 当时根本没有这一列)⇒ 老大两档口径**不可查、不可验**。
本次补齐落库 + API 透出。**apply when**:任何「按来源/档位分流」的字段,
都要确认它**真的进了 DB**,而不是只在代码里出现过。

**★ 闸门校准(同一轮)**:
`_is_analysis_conclusion` 原来有 `len < 80` 的**长度硬门槛**,
⇒ 56 字的「评级+表格+核心结论」短报告被判 False ⇒ 被 `_passes_auto_save_gate` 拦掉
⇒ **明明是成型分析却没进知识库**(生产日志 25 次「命中闸门」)。
已改:①标记词命中即 True(不限长度)②结构完整即 True(并扩充「核心结论/合作建议」信号)
③`_passes_auto_save_gate` 只拦**否认类元对话**,不再要求特定格式。
回归已验:寒暄/否认/工具转录**仍**被拦,库里真实分析**仍**放行。

**回滚点**:`settings.json` `daily_kb_intake=false` ⇒ 档 B 退回旧行为(照抄原文 +「知识点：」标题);
档 A(分析类完全提取)不受此开关影响 —— 那是老大明确要的行为。

### ★ L0 必须有「答」—— 2026-10-23 修的真问题

- **症状**:Ray 的 **316 条飞书对话 `assistant_content` 100% 为空**
  ⇒ 知识库 L0 只有「问」没有「答」,L1 事实提取拿不到回复,提炼不出结论。
- **根因**:代理链在**转发请求之前**就落了 L0(`save_conversation`),
  那时还没拿到 LLM 回复 ⇒ `assistant_content` 用了默认值 `''`。
- **修法**:拿到上游响应后按 `(agent_id, session_id, turn_id)` 回填
  (`_backfill_l0_assistant`)。
- ⚠️⚠️ **两个坑,都是「声明端 ≠ 使用端」**:
  1. **真正跑的是 chain 路径**(`PROXY_ENGINE=chain` → `_handle_proxy_kimi_chain`),
     legacy 路径只是备选 ⇒ 回填**只挂 legacy = 改在旁支上,永远不生效**。
     **挂之前先看 `server.log` 确认走的是哪条**(日志有 `chain结束:` / `provider=xxx`)。
  2. **上游响应格式会变**:Kimi 是 OpenAI 形态(`choices[0].message.content`),
     但降级到 MiniMax 后是 **Anthropic 形态**(`content:[{type:text,text}]`)
     ⇒ `_extract_kimi_message_content` 认不出来(返回空)⇒ 必须用
     `_extract_assistant_text_any` 兼容两种形态(**别改前者**,它在降级健康检查里另有用途)。
  3. 流式路径的 SSE parser 原来**只抽 usage 不抽文本** ⇒ 已加 `.text` 累积
     (`AnthropicSSEParser` / `OpenAISSEParser`)。
- **历史 316 条补不回来** —— 回复从未落库,源头就没了(除非从 openclaw 会话日志反挖)。

### 8.8 达人可见面的两条边界：数据源不许带我方出单 / 卡片替换会吃掉原卡按钮（2026-10-25 新增）

体检「还有什么可以优化」时挖出来的两件事，**都不是优化项，是缺陷**。

#### ① 推荐指数里掺了我方出单数据（接进飞书就会泄露）

`products` 表里三个「达人数」**口径完全不同**，注释写在
`daren_cockpit/tools/filing_products.py`：

| 列 | 来源 | 性质 |
|---|---|---|
| `influencer_count` | 达人端 `cooper_author_num` | 平台全商品带货达人数（**达人端**）|
| `talent_count` | 我方 `product_talent_match` | 我方合作达人数 |
| `platform_order_daren_count` | 团长端 `order_daren_count` | **我方机构出单达人数** |

而 `product_reco` 把**第三个**当 `talents_sold` 维度参与打分（覆盖 81%、12 种取值）
⇒ 违反 §8.1「不向达人暴露我方出单数据」。

**这颗雷的隐蔽处**：列名带 `platform_` 前缀、字段名像平台数据，**实际是我方团长端数据**
⇒ 几乎不可能靠看名字发现。2026-10-25 体检才抓到。

**修法**：`talents_sold` 维度改读达人端 `influencer_count`
（覆盖 75.9%、**24 种取值**，区分度反而更好）；维度文案「达人已出单数」→「带货达人数」。

⚠️ **闸门的作用范围刻意只盯推荐链路**：`talent_count` / `platform_pay_gmv`
在 `solobrave-server.py`、`captain_stats.py` 里是**合法的内部经营用法**
（商品档案、团长端看板）⇒ 全仓一刀切会误伤。
⇒ 闸门落在「推荐器 + 推荐取数的两个消费端」，且用 **AST 扫字符串字面量**
（不是正则）—— 列名写在**注释**里是允许的（那是在说明别用它）。

★ 顺带一条通用纪律：**达人可见的输出，每个字段都要能回答「这个数是哪一端的」**。
分不清「达人端 / 团长端 / 我方」的字段，一律不许出现在达人可见的面上。

#### ② 卡片「替换」会把原卡的按钮一起吃掉

「看我的排品表」按钮是用回调响应里的 `card` 字段**替换**原卡
（飞书原生「点开替换」，好处是聊天里不会越堆越多）。
但商品分析卡上有两个按钮，替换后**只剩排品表卡自己的按钮**
⇒ 原来的「接这个品」**消失** ⇒ 达人看完排品表就接不了那个品了（闭环断在最后一步）。

**修法**：`view_plan` 按钮把 `product_id` 一起带过去 ⇒ 排品表卡上再放一个「接这个品」。

⚠️ 推论：**卡片替换 = 替换了整张卡，不是一个区块**
⇒ 替换出去的卡必须**自带完整的下一步动作**，
不能假设「原卡上还有按钮」（那是另一个卡片的）。

#### ③ 排品表三处口径必须一致

同一个「排品表」有三个面：飞书卡片 / H5 `/api/plan` / 文字版 `plan_notify.plan_text`。
原来飞书卡片**只筛今天**（`if s.date != today: continue`），
而 H5 给 7 天、文字版给「今天及以后」⇒ **按钮叫「排品表」却只给 1 天**。

**修法**：三个面统一走 `plan.future_slots()`（今天及以后），
**不许在 handler 里另写一套过滤**（另写 = 迟早跟 H5 漂移）。

★ 通用纪律：**同一个概念出现在 N 个面上，就必须有 N 个消费者共用同一个解析函数**。
第 ②③ 条是同一条纪律的两个面：数据源一个、解析函数一个。

**回归**：`tests/test_reco_data_source.py`（7 条）+ `tests/test_card_plan_view.py`（7 条），
变异验证 7/7 全被抓住（`scripts/mutate_new_guards.py`）。
⚠️ 变异脚本的**还原必须用备份副本**，不能用 `git checkout --`
—— 未提交的真改动会被一起冲掉（2026-10-25 实测踩过：一轮变异冲掉 5 个文件的改动）。

### 8.9 自算推荐指数接进飞书（老大目标 3 收口，2026-10-25）

「达人端那个 ★★★★★ 是 AI 助手自己算的（平台无此真值）⇒ 我们用 `product_reco`
算自己的一套」—— 这套引擎 2026-10-24 就写好了，但**一直只在运营面**
（驾驶舱 `reco` 字段 + 服务端 `/api/products/recommend`），
**飞书里完全接不到**（`grep product_reco|recommend|推荐指数|stars` 在 `daren_bot/`
零命中）⇒ 达人问「推荐点什么」只会掉进通用 LLM 问答（扣 1 积分，还可能瞎编）。

本轮把这条链路接上，同时把**取数口收拢**。

#### ① 取数层收拢成一个门：`daren_cockpit/tools/reco_source.py`

之前「驾驶舱」和「服务端」各写一份取数 SQL，两个面各算各的。
现在收成 `reco_source`（唯一真源），两个消费端共用：

- `reco_source.PRODUCT_COLS` = **列白名单**（不是黑名单 ——
  黑名单会被新加的我方列悄悄绕过，白名单不会）
- `reco_source.load_product_pool()` / `build_chain()`
- ⚠️ **为什么不直接 import `daren_cockpit.server`**：
  那个模块**顶层就起 `ThreadPoolExecutor`** ⇒ bot 侧 import 会带起线程池
  ⇒ 取数下沉到 `tools/` 这个纯工具层

★ 由此「达人可见面」只有**一个门** ⇒ 护栏也只需卡一处。

#### ② 意图判据刻意**保守**：宁可漏，不可抢

`is_reco_query()`（`daren_bot/handler.py`）只认「推荐/选品」+ **范围词**
（点/几个/什么/哪些/建议/指数/给我…），并且**点名了具体商品一律不拦**。

⚠️ 为什么保守：抢了会把「这个品能不能带」这种需要**商品真值**的问题
变成一份候选清单 —— 达人拿到的是「候选」而不是「这个品的结论」，
是**降级**而不是升级。漏掉的会回落到 LLM 问答（本来就能答）。

#### ③ 零成本 + 只丢不造

- **零成本**：纯查库 + 纯函数算，**不调 LLM、不扣积分**（跟排品表查询一个待遇）
- **只丢不造**：画像不足 / 算不出 ⇒ 如实说「算不出」，**不给一份编出来的推荐**

#### ④ 卡片体验：理由必须达人看得懂

`Recommendation.reasons` 里的 note 是**维度话术**，第一条常常是
「¥40 落在策略价带 ¥60~¥150」⇒ **这是我们内部话术，达人看不懂**。
⇒ 卡片层自己从 `dims` 里挑**业务维度**（店铺体验分 / 好评率 / 近7日短视频销量 /
带货达人数），挑不满才退回 `reasons`。

★ 顺带修了一个漏网：**`product_reco` 里 `talents_sold` 维度的 note 文案**
还写着「达人已出单 N 人」—— 数据源已换成达人端「平台带货达人数」，
**文案没跟着换** ⇒ 口径漂移。label 和 note **要一起改**。

#### ⑤ 闭环

推荐卡上每个品自带「接这个品」按钮（`accept_deal` + 商品 id）
⇒ 点一下进排品池 → **当场排期** → 第二天早上推「今天推哪个」。

★ 按钮标签**必须带商品名首字**（不能三个都叫「接这个品」）
⇒ 达人分不清哪个按钮对哪个品。

**回归**：`tests/test_reco_bot.py`（39 条）+ `tests/test_reco_data_source.py`（10 条），
变异验证 **13/13** 全被抓住（`scripts/mutate_new_guards.py`）。

★ 变异验证里又抓到一条**恒真闸门**：「排除具体商品」那条守卫
在原有用例集里**根本没生效过**（用例都被范围词挡住了）
⇒ 补了「这个商品有什么推荐」这种**既点名又带范围词**的反例才真正覆盖它。
⇒ **闸门写完要问「这条守卫到底在挡什么」**，否则它可能一直是摆设。

### 8.10 隔离的边界：商品「管理」按 owner，商品「候选池」不按 owner（2026-10-25 新增）

体检时发现 `/api/products/recommend`（选品推荐推理链）**真实调用下返回 0 条推荐**。

#### 根因

这个接口照抄了 `/api/products`（商品**管理**）的 owner 过滤：

```python
uid = _resolve_talent_owner_id(auth)
visible_ids = {uid} | set(_get_user_emp_ids(uid))
pool = [p for p in pool if p.get('created_by') in visible_ids]
```

而商品库的**主体挂在管理员（`user_7acb72ff`）名下**（在架 47 条），
AI 员工却分属贺主管 / 荔枝 / 一个孤儿 user ⇒ 可见集里没有这些商品
⇒ **候选池被裁空** ⇒ 返回 0 条。

⚠️ 但真正在用的两个面都是**公共库**语义（不过滤 owner）：
- 运营驾驶舱 `daren_cockpit`（`/api/cockpit` 的 `reco` 字段）
- 飞书达人侧（`reco_source`）

⇒ 所以真正的病根是**口径分叉**：同一个「推荐」，三个面三种算出来。

#### 修法

服务端也走 `reco_source`（唯一真源）⇒ 三个面**同一份取数、同一套算法**。

#### ★ 隔离的边界（这条要记住）

| 面 | 语义 | 按 owner 隔离？ |
|---|---|---|
| `/api/products`（商品**管理**）| 谁能看/改这些商品行（含 `created_by` 等我方字段）| **是** |
| `/api/products/recommend`（**候选池**）| 给某个达人选品 ⇒ 输出只有达人端商品维度 + 星级 | **否** |

理由：商品库本质是**我方推给达人的公共商品库**（达人侧、驾驶舱侧都按公共库取）；
推荐接口的输出经 `reco_source.PRODUCT_COLS` **白名单**过滤，
**不含任何我方经营数据** ⇒ 按录入人裁剪只会让各面算出不一样的推荐。
多租户之间的隔离由 **DB 路由**（`_tenant_db_path`）保证，不靠这里。

⚠️ 放宽**只限候选池**，不许顺手扩到管理面 ⇒ 闸门
`test_product_management_api_still_isolates_by_owner` 专门锁这条。

#### 顺带查出：三个 AI 员工的归属是孤儿

`Ray` / `上官婉儿` / `孔明` 的 `createdBy` 都是 `user_5b46ceaa`，
而这个 user **在 `data/users.json` 里不存在** ⇒ 它们在任何按 owner 过滤的
接口上都是瞎子（可见集恒空）。**这是数据问题、涉及归属，没擅自改**，
交老大定。

#### ★ 闸门探针的教训（同一个坑，第二次踩）

「商品管理接口仍按 owner 隔离」这条守卫，最初写成
「查 `_resolve_talent_owner_id` / `_get_user_emp_ids` 这两个**名字还在不在**」。

⇒ 变异把 `if not auth.is_admin or ...:` 改成 `if False:` 时，
**这两个名字照旧在源码里**（只是永远不执行）⇒ **恒真探针**，抓不住任何东西。
⇒ 改成查 **`if` 的条件本身**（AST 里找 test 引用了 `is_admin` 的 `ast.If`）。

★ 规律：**探针要盯「行为」，不是盯「符号」**。
把条件改成死值时，符号一个都不会少。

### 8.11 积分闸门：消耗算力必收费，且「扣费 + 入队」必须是一个原子操作（2026-10-25 新增）

老大原话：「每一个功能都需要积分控制 毕竟是消耗算力 不消耗的就算了」。

#### 闸门分层（各管各的，别越层）

| 层 | 文件 | 职责 |
|---|---|---|
| 成本表 | `daren_bot/config.py:COST_TABLE` | **唯一真源**：动作名 → 价格 |
| 闸门 | `daren_bot/credits.py` | `charge_and_enqueue()` —— **唯一的扣费入口** |
| 原子操作 | `daren_bot/db.py:enqueue_task_charged` | 幂等查 → 查余额 → 扣费+建任务（**一个事务**） |

⚠️ **不提供「只扣费不建任务」的裸入口**（`credits.charge()` 已删）。
理由见下「两事务坑」⇒ 留着它就等于留着坑的入口。

#### ★★ 「两事务坑」：扣费与入队分两次调用 ⇒ 重复请求净多扣

**根因**：入队是**幂等**的（同租户+同类型+同目标已有 pending/collecting ⇒ 返回旧任务、
**不新建**）。若「先 `deduct` 扣费、再 `enqueue_task` 入队」是**两个独立事务**：

```
第 1 次：扣 100 + 建任务（task.credits_cost=100）
第 2 次：又扣 100 + 命中幂等（**没建新任务**）
⇒ 流水扣了 200，任务只有一个
⇒ 任务失败时 worker 只按 task.credits_cost 退 100 ⇒ 净多扣 100
```

**实测**（2026-10-25）：余额 1000 → 800，任务表 1 条，流水 2 条。

⇒ **修法**：合并成 `db.enqueue_task_charged()` 一个事务，
且**只有真建了新任务才扣分**（命中幂等 = 没多消耗算力 = 不收费，老大口径
「不消耗的就算了」）。返回 `(status, task_id, balance)`，
`status ∈ {ok, duplicate, insufficient}`。

⇒ 闸门 `test_repeat_request_does_not_double_charge` 锁死这条。

#### 三个层次的「白干」漏网（体检查出的，已补）

| 漏网 | 原因 | 修法 |
|---|---|---|
| 周报生成 | `enqueue_task(..., 0)` | 扣 `weekly_report`(30) |
| 周报自动采集 | `enqueue_task(..., 0)` | 扣 `weekly_collect`(100) |
| 采集失败后的重采 | `enqueue_task(..., 0)` + 话术写「已免费安排」 | 扣 `recollect`(100) |

#### 防漏闸门（`tests/test_credits_gate.py`，19 条）

- **静态扫描整个 `daren_bot/`**：所有入队的 `credits_cost` 必须 > 0
  （例外只有：缓存命中、管理员后门、零成本入口白名单）
- **成本表是唯一真源**：表里不许有死价；代码里扣费动作名必须在表里
- **原子性**：余额不足不扣不入队；扣了必须写流水且流水带 `ref_task_id`
- **不许有裸扣费入口**：`credits.charge()` 不许存在（两事务坑的源头）

⚠️ **扫描器必须盯「真正在用的入口名」**：
本轮把扣费挪进原子入口后，原先只扫 `db.enqueue_task` 的扫描器
**扫不到任何消耗算力的路径了** ⇒ 闸门恒真。
⇒ 现在同时扫 `enqueue_task_charged` + `enqueue_task`，
且**每个扫描器都带 `scanned > 0` 自检**（扫不到就红），
防止「改名之后闸门静默失效」。

★ 这正是「**声明端 ≠ 使用端**」：测试盯的入口名，必须跟**实际调用链**一致。
同理，`tests/test_product_intent.py` 的 spy 也必须盯新入口
（`db.enqueue_task_charged`）—— 2026-10-25 实测：改了实现没改 spy ⇒ 6 个路由测试假红。

#### ★ 变异验证抓出的一条**真缺口**：零成本入口能绕过整道闸门

把「周报自动采集」「采集失败重采」两处**收费**改调 `credits.enqueue_free()`
（cost 恒为 0）⇒ **19 条测试全绿** ⇒ 护栏形同虚设。

**根因**：`enqueue_free` 是闸门自己的零成本入口，实参里**没有 cost**
⇒ 通用扫描器看不到它。

⇒ 补 `test_free_entry_calls_are_all_whitelisted`：**单独扫这个入口的调用点**，
逐条登记「为什么这次不收费」（`ZERO_COST_ALLOWED`）。

★ 规律：**每加一个入口，扫描器就得跟着扩**，否则新入口就是绕过闸门的后门。

### 8.12 商品链接查找链路 + 登录态失效的话术分层与运营告警（2026-10-25 新增）

> 起因：老大报 bug「我发送商品链接，ai 无法从中识别出商品 id 进行查找商品数据，
> 然后就是浏览器的掉了，我也不知道，就是需要我重新扫码登陆或者验证码登陆，我都不知道」。

#### 一、达人发商品链接后，AI 到底怎么找的（**唯一链路，别再改形状**）

```
达人发链接/商品ID
  → handler._route_link → normalize_product_url（去追踪参数）
  → handler._route_product（label="链接"）
  → executors.collect_product_by_url
      → extract_product_id(url)         # ?id= / /product/ / 全文 17-20 位数字
      → executors.collect_product_by_id(pid)
          → buyin_worker POST /collect-product-id
              → **达人端**浏览器页面内 fetch 选品广场 material_list
      → 结构化字段（价格/佣金/月销/类目/店铺分/好评率/带货达人数/30天销量曲线）
  → 结合达人画像 → 「能带 / 慎带 / 别带」结论（LLM）
```

⚠️ **不许再写「打开商品页让 AI 读正文」当主链路**：
实测商品页正文里**没有商品数据**（留存 HTML 389KB，`product_id` 出现 0 次，
只有「打开抖音APP」壳）⇒ AI 读正文必然瞎编。
**ID → 接口**才有价格/佣金/月销真值。

⚠️ **ID 通路失败不许回退到页面解析链路**（2026-10-25 修）：

| 失败类型 | 处理 | 理由 |
|---|---|---|
| `ProductNotFound`（平台查无此商品）| **不回退**，直接抛 | 回退也没用（平台确实没这个品）|
| `NeedLogin`（采集通道登录态失效）| **不回退**，直接抛 | **换链路也救不了**（走哪条都撞同一堵墙），而回退链路查的是**团长端**（违反 §8.1），更会把「达人端掉登录」掩盖成「团长端掉登录」|

⇒ 2026-10-25 实测事故：达人端掉登录 → 抛错 → 被 `except RuntimeError` 吞掉回退 →
团长端也没登录 ⇒ 达人看到的却是「采集账号登录态过期了」，**真实原因被掩盖**。
⇒ 现在登录态类错误用**独立异常类** `executors.NeedLogin(side=...)`，
`collect_product_by_url` 只捕获 `ProductNotFound` / `NeedLogin` 并**直接 raise**。

#### 二、失败话术必须分层（达人看人话 / 运营看细节）

> 达人收到的原文曾是：`任务失败：RuntimeError: 达人端采集账号登录态过期了，
> 跟运营老师说一声扫码重登就好` —— 三个问题：异常类名对达人无意义、
> 话术是写给运营的、达人不知道该干嘛。

⇒ 新增 `daren_bot/failure_text.py`（话术翻译层）：

| 出口 | 给谁 | 内容 |
|---|---|---|
| `user_text_for(exc, product=…)` | **达人** | 只说「现在什么情况 + 你能做什么 + 替代路径」|
| `ops_text_for(exc)` | **运营**（日志）| 异常类型 + 原始消息 + `side`（达人端/团长端）|
| `ops_alert_body(exc, context=…)` | **运营**（告警）| 发生了什么 + **该做的一个动作** |

★★ **达人可见文案的静态闸门**（`tests/test_failure_text.py`）不许出现：
异常类名、端口号、内部路径、**我们的内部状态**（登录态/扫码/采集器/巨量/百应）、
给运营看的指示（「跟运营老师说一声」「联系运营」）。

⇒ **「我们的内部状态」也是内部细节**：
「采集账号登录过期、需要扫码」是**我们的问题**，不是达人的问题 ——
达人只需要知道「现在拿不到 + 我记下了 + 你可以怎么做」。
⇒ 「这是我们的错」不要甩给达人，更不要让达人去联系谁。

⚠️ **话术要按「失败类型 × 任务场景」两维分**：
「发商品截图我照样能分析」只对**商品/图片**任务成立；
塞给「采集对标号」就是驴唇不对马嘴 ⇒ `_USER_TEXT_PRODUCT` / `_USER_TEXT_GENERIC` 两套。

#### 三、运营告警通道（**实测结论，不是推测**）

| 通道 | 实测 | 说明 |
|---|---|---|
| **macOS 本地通知** | ✅ 可用 | `osascript display notification`，老大本机直接弹窗 |
| **日志 `[OPS-ALERT]`** | ✅ 可用 | launchd 日志，`grep OPS-ALERT` |
| **落盘 jsonl** | ✅ 可用 | `data/daren-bot/ops_alerts.jsonl` → admin 后台展示 |
| **飞书（运营租户最近会话）** | ✅ **启用** | 老大确认「我看得见」⇒ 走 `daren_boss` 那条（`oc_13194fab6699f98c81492927277b849d`），跟 weekly 运营告警**同一条通道** |
| 飞书（老大私聊 `oc_c086…`）| ❌ **不可用** | `code=230002 Bot/User can NOT be out of the chat` —— 老大私聊是**另一个 app** 的会话，达人 bot 进不去 ⇒ **别配成 `DAREN_OPS_CHAT_ID`** |

⇒ 飞书告警**默认启用**，会话来源两级：
  ① `DAREN_OPS_CHAT_ID` 显式配置 → 用它（要固定到别的会话时才需要）；
  ② 没配置 → **自动取** `db.get_latest_chat_id(OPS_TENANT_KEY)`
     ⇒ **老大不用手填 chat id**，换了会话也不会失效。
⇒ **告警是旁路**：任何通道挂了都不许冒到主链路（`ops_alert.alert` 逐通道 try/except）。

⚠️ **daren_bot 也要读 `.env`**（launchd 起的服务没有 shell 环境）：
  `daren_bot/config.py` 顶部有 `_load_env_file()`（绝对路径读 `ROOT/.env`，
  **只在 `os.environ` 里没有该键时才补** ⇒ 真实环境变量优先）。

#### 四、登录态巡检（主动叫醒运营，不等达人投诉）

`daren_bot/login_watcher.py` 定期拉采集器 `/health`：

| 采样 | 判定 | 动作 |
|---|---|---|
| 采集器不可达 | unknown | 不告警（不是掉登录，是采集器没起）|
| `daren_browser_up=false` | unknown | **不告警** —— 两浏览器**互斥**，切到团长端时达人端会被关掉（§8.2），**是设计不是故障** |
| `daren_browser_up=true` + `daren_logged_in=false` | **bad** | 告警：真掉登录 |
| `daren_browser_up=true` + `daren_logged_in=true` | ok | 恢复时告警一次 |

★ 只盯**达人端**（商品数据只走达人端，§8.1）。
★ **边沿触发 + 防抖**：连续 2 次采样都异常才认定；同一轮故障不重复报。
★ 判定口径的**唯一真源是 `classify_health()`**（纯函数，可单测）。
★ 巡检**必须有心跳日志**（`state=ok (cycle N)`）——
  巡检是哑的就没有可观测性，线程死了没人知道。

#### 五、变异验证抓出的两条**真缺口**（本轮）

1. **基线不绿 ⇒ 变异验证全是假象**：
   `test_alert_survives_channel_failure` 在基线就是红的（告警通道异常会冒到主链路），
   于是「变异后变红」证明不了任何事。
   ⇒ `scripts/mutate_failure_guards.py` 现在**先跑基线自检**，基线红就直接退出。
2. **只断言词表内容 = 恒真闸门**：
   变异「`table = _USER_TEXT_PRODUCT if product else _USER_TEXT_GENERIC`
   → `table = _USER_TEXT_PRODUCT`」后测试**照样全绿** ——
   因为用例只查了词表里有没有「商品」，**没查选择逻辑**。
   ⇒ 补：① 走公开 API 按场景验输出；② 用例必须用**真的命中词表**的异常
   （用归类成 `other` 的异常 → `table.get('other', 兜底)` 根本走不到表里）。

⇒ 规律：**闸门要盯「行为」，不只盯「内容」**；
     **用例用的输入必须真的走到被保护的那条分支**。

### 8.13 「没用的 AI 员工」的判定口径（2026-10-25 老大：「没什么用的ai就删除掉」新增）

> ⚠️ **「owner 失效」≠「没用」** —— 这是本轮差点删错的地方。

#### 判定必须看**真实用量**，不是看归属字段

2026-10-25 实测（`data/agents.json` 8 个 AI 员工 × 主库引用统计）：

| AI 员工 | 最后活跃 | 关联记录 | 结论 |
|---|---|---|---|
| **Ray** | **2026-10-25 03:00（当天）** | 3890（1815 会话 / 1910 积分流水）| **在用 ❌ 绝不能删** |
| **孔明** | **2026-10-23 16:04（2 天前）** | 325 | **在用 ❌ 绝不能删** |
| 上官婉儿 / Helen / 木木 | 10-14 / 10-20 / 10-14 | 195 / 2751 / 424 | 在用 ❌ |
| **知识库管理员** | 从未 | 1 | ⚠️ **系统角色**：知识库写入通道的 `X-Agent-Id`，`visibility=all` ⇒ 删了知识沉淀就断 |
| 测试1 | 10-05 | 65（全是 `ping,请回 pong` 测试流量）| ✅ 删 |
| 子龙 | 10-05 | 65（已归档、置顶）| 归档 + 零使用 ⇒ 候选 |

⇒ **`createdBy` 指向不存在的 user（Ray / 上官婉儿 / 孔明 都是 `user_5b46ceaa`）**
  只说明「它们在 owner 过滤接口上是瞎子」，**不代表没用** ——
  Ray 是当天还在跑的主力。**先查最后活跃时间和关联记录数，再决定删不删。**

#### 删除必须走正规通道 + 备份

- 入口：`DELETE /api/agents/:id?permanent=true`（**仅限已归档员工**），
  服务端会清聊天文件 / 记忆目录 / 知识表 / 商品关联。
- ⚠️ **归档员工在「localhost X-Agent-Id 免签通道」里查不到**
  （`_get_agent_by_id` 走 `_load_agents()`，默认**不含归档**）⇒ 彻底删除必须用
  **Bearer token**（admin 本地签一个短时 token 即可）。
- 删前必备份：`data/agents.json.bak-<ts>-before-ai-cleanup` +
  `data/solobrave-backup-<ts>-before-ai-cleanup.db`。

#### ★ 服务端「彻底删除」原本**只清 4 张表**（本轮已补全）

`memory_summary / knowledge_base / knowledge_base_new / memory_topics`
⇒ **MS3 时代的会话与工作痕迹表一张都没清**：
`memory_conversations`（会话原文，**会被记忆抽取/知识检索重新消费** ——
员工都删了，它说过的话还在往知识库沉淀）、`pipeline_state`、
`memory_atoms / memory_scenes / memory_personas`、
`group_messages / tool_calls / notifications / heavy_jobs`。
实测删「测试1」后 `memory_conversations` 还剩 64 条。

⇒ 已补进 `_cleanup_agent_data`（`solobrave-server.py`）。
★ **边界：删「会话与工作痕迹」，留「知识资产与账本」** ——
  `credit_accounts / credit_usage_log / token_usage` 是**积分账本**（抹了毁账目留痕）；
  `knowledge_events / knowledge_patterns / proposals` 是**知识沉淀与业务提案**（出处要留）。

### 8.14 登录态判定：**单一真源 + 分层优先级**（2026-10-25 新增）

> 起因：老大扫码登录后 `/health` 仍报 `logged_in=false` ⇒ **被反复叫去扫码**，
> 而登录态其实一直是好的。

#### 事故现场（实测，不是推测）

| 证据 | 值 |
|---|---|
| 盘上 cookie（团长端）| `ecom_us_lt_buyin`（过期 10-28）+ `sessionid`（过期 12-24）—— **有效** |
| cookie 末次访问 | **12:16**（当天下午），19:42 之后没再更新 |
| 浏览器 History 最后访问 | 11:47 `mpa/account/institution-role-select`（**登录后的角色选择页**）|
| `/health` 当时报 | `browser_up=true, logged_in=false` |

⇒ 根因：**两处判定口径分叉**，且 health 把「URL 含 login/passport」放在 cookie **之前**：
```python
# 旧 health()
url = self.page.url
if 'login' in url or 'passport' in url:
    return True, False          # ← 先否决，cookie 根本没机会说话
cookies = self.ctx.cookies()
logged = 'ecom_us_lt_buyin' in names and 'sessionid' in names
```
⇒ 页面刚打开时 URL 还停在带 login/passport 的地址（标签页没跟上 / 跳转没走完）
⇒ **登录态明明有效，却报「掉登录」** ⇒ 老大白扫码。

⇒ 而且另一处 `_check_login_state()` 的口径**还不一样**（cookie 要**任一**即可）
⇒ 同一个系统两个「登录态」定义 ⇒ 谁也说不清当前到底登录了没。

#### 修法：`buyin_auth.py` = 唯一真源

| 导出 | 作用 |
|---|---|
| `CAPTAIN_COOKIE` / `CAPTAIN_COOKIE_SESSION` / `DAREN_COOKIE` | 登录 cookie 名（唯一真源）|
| `is_login_url(url)` | 登录页判据 —— **路径段匹配**，不做裸子串（`/dashboard/passport-center` 是业务页不是登录页）|
| `cookies_logged_in(names, label)` | cookie 判据（团长端要两个都在；达人端认自己的 SSO cookie）|
| `judge_login_state(url, names, label, prefer_page=…)` | **唯一判定入口** |

#### ★ 两种用途、两种优先级 —— 是**分层**，不是分叉

| 用途 | 场景 | 优先级 | 理由 |
|---|---|---|---|
| `prefer_page=False` | `/health`、登录态巡检、前置预检 | **cookie 为准** | 只回答「登录态还在不在」⇒ 宁可宽松 —— 误报掉登录 = 老大白扫码 + 达人白等 |
| `prefer_page=True` | 真正发起采集前（`_check_login_state`）| **页面为准** | 只回答「这次访问通不通」⇒ 服务端会话已死时页面会跳登录页，**必须**拦住（否则把登录页正文当数据抽）|

⇒ 改判据只改 `buyin_auth.py` 一处；`buyin_worker.py` 两处都调它。
⇒ 回归用例：`tests/test_buyin_login_judge.py`（18 条），
★ 含**今天这次假阴性本身**作为钉死用例。

#### 实测效果

- 重启采集器后，**两端登录态自动恢复，零扫码**：
  团长端 `browser_up=true / logged_in=true`；达人端 `daren_browser_up=true / daren_logged_in=true`。
- 真实商品查询照常通：`3847842537083633879` → ¥499 / 佣金 5% / 月销 21 / 店铺分 92 / 带货达人 19。

⚠️ **排查登录态问题的正确姿势**（省得再白扫一次码）：
1. 先看**盘上 cookie**（`data/buyin_browser/profile*/Default/Cookies` 的
   `expires_utc` / `last_access_utc`）—— cookie 有效就说明登录态在；
2. 再看浏览器 History 的最后访问（`Default/History`）—— 停在哪一页一目了然；
3. 最后才考虑「让老大重扫」。

---

## 15. 服务端代理链铁律:声明端 ≠ 使用端 + 「第一环死 ≠ 全员死」(2026-10-26 新增)

> 触发:老大「**木木回复消息,怎么回复跟以前弄的不一样啊**」
> ⇒ 当场挖出 **6 处根因**,**全是「声明端 ≠ 使用端」形态**。
> 详见 `docs/PARALLEL-HANDOFF-2026-10-25.md` §9(提交 `24a7fb9` / `82d10c7`)。

### A. 「声明端 ≠ 使用端」的三种形态(本轮全踩了一遍)

| 形态 | 声明端(人配的) | 使用端(真跑的) | 后果 |
|---|---|---|---|
| **路径** | `agents.json` 的 `openclawName` | `openclaw.json` 的 `agents.entries[].workspace` | 木木(`openclawName='main'`,6 人里唯一错位)读写**贾维斯**的 workspace;写文档会**覆盖别人的 SOUL.md** |
| **配置** | `settings.json` 的 `llm.providers[].baseUrl` | `_resolve_ai_base_url()` 的**内置 mapping** | 配置**静默失效**(minimax 实际打老端点 → 404) |
| **协议** | provider 名 / 端点 | **写死在调用处**的 OpenAI 形状 | 换端点后协议 / 端点 / 模型三者全错位 |

⇒ **凡是要「配置生效」的地方,都必须问一句:使用端到底读的是谁?**
⇒ **协议不许写死在调用处** —— 统一走 `provider_adapters`(唯一协议真源)。

### B. 「第一环死」≠「全员死」—— 降级链**之前**的早退闸门会杀死整条链

★ 实测:kimi 月度额度耗尽 ⇒ 4 把 key 全被拉黑
⇒ 旧实现在**进链之前**就 503「Key 池已空」
⇒ 兜底档(minimax 套餐 M3,实测 200 可用)**根本没机会接管**。

⇒ **早退闸门必须问:链后面还有档位吗?**
- chain 引擎:带**空 primary** 进链,`_chain_kimi_key_iter`(池空即止)自己往下走;
- legacy 引擎:没有链可退 ⇒ **仍 503**(这条要保留,别拆)。

### C. 闸门的返回码会被下游当成**健康信号**

★ 运维健康检查(`~/Developer/solobrave-ops/healthcheck.py`)用**空 body** 探测代理端口,
它**只把 400/401/403/404/405/422 当作「端口活着」**
⇒ 我们返回 503 ⇒ 它判「主服务故障」⇒ **连续误报 13 次以上**。

⇒ **闸门选错状态码 ⇒ 上游监控整套失真。**
⇒ 死因文案要按**错误语义**写(空 body 就报「空 body」,别报「Key 池已空」)。

### D. 看着有覆盖 ≠ CI 跑得到

★ `tests/` 下有 4 个**脚本式**测试文件(`main()` + `check()` 风格),
pytest **收集 0 条** ⇒ 看着有覆盖,CI 其实跑不到
(本轮 `tests/test_proxy_engine.py` 在第一条就崩 ⇒ **链引擎核心覆盖一直是死的**)。

⇒ **改共享模块前先确认「相关测试真的会被执行」**;新测试一律 pytest 风格。

### E. 员工设定以 **OpenClaw 运行时**为准,不是 agents.json

★ 员工的完整人格 / 工具清单存在**它真正跑的那个 workspace**
(`openclaw.json` 的 `entry.workspace`),不是 `agents.json` 的字段
(木木的 `SOUL.md` 曾被截断成 304/556 字、且**没有 TOOLS.md**)。

⇒ 改设定走服务端 `POST /api/openclaw/write-agent-docs`(按 `entry.workspace` 解析 + 归属校验),
**别手改 workspace 目录**(会绕过归属校验)。

---

## 16. 缺失值 / 空态 / 配置源三条铁律（2026-10-26 新增）

> 触发:老大 2026-10-26 问卷拍板三件事 —— ① 撤 daren_bot 降级链里的智谱环
> ②「今天没排品」发占位消息 ③ 转化率保持 NULL 当缺失维度。
> ⇒ 执行时**当场挖出 4 条更深的病根**,都写在这里。

### F. 两份**语义不同**的事实共用一份配置源 ⇒ 一改就互相带断

★ 病根（本轮实测踩到）：视觉 QA 的 witness/repair 读的是
  `settings.llm.providers`（**对话链的档位表**）。
  对话链按老大口径摘掉智谱档 ⇒ 视觉 QA **跟着一起断**
  ⇒ 字段提取精度从 95%+ 退回「只跑常识规则」（~67%），而两边**都不报错**。

⇒ **判据**：这份配置是给「谁」用的？消费者语义不同 ⇒ **配置源必须分开**。
  （对话链 = `settings.llm.providers`；视觉 QA = `settings.visionQA` 段 + env 兜底）
⇒ **共享模块改了配置，另一条链会静默失效** —— 这类故障没有任何报错，
  只能靠「配置源独立」在结构上杜绝。

### G. 空态与故障必须分得开：**不许把故障说成结论**

★ 原来 `plan_notify._reminder_parts` 把「达人查不到」和「今天没品」
  **折叠成同一个 `("", [], [])`** ⇒ `scan_and_push` 只看 `if not text:`
  ⇒ 一律静默。老大拍板「今天没排品也要发占位」后，暴露出一个新问题：
  **空态有回音了，但「我们没采到数据」也会被说成「今天没排品」**。

⇒ **状态必须是显式枚举**（`ok` / `no_talent` / `no_pool` / `no_today_item` / `already_sent`），
  **不许靠 `text == ''` 反推**（那把「缺一个输入」说成了「答不了」）。
⇒ 处置相反的两类必须分开：
  「达人数据缺失」= **我们的问题** ⇒ 不发给达人（只记日志/告警）；
  「确实没品可推」= 达人的现状 ⇒ 发占位。
⇒ ⚠️ **空态消息没有 `deal_id`** ⇒ 挂不上 `deals.remind_state` ⇒ 幂等要**单独一个标记位**
  （`bot_plan_notice_log`，按 `(tenant_key, day_key, kind)` 唯一，**DB 级**，重启也守住）。
  且空态推送**要计入调度器的「今天推过了」闸门**，否则 09:37 之后会每 300s 重扫一整天。

### H. 「没有真值」不许变成 **0** —— 编造的 0 比 NULL 更毒

★ 转化率：平台**没有**通用商品转化率接口 ⇒ 我们的库里从来没有过它的真值。
  但代码在到处**编造**：
  - 示例种子写死 `3.5` / `[3.2, 2.8, 4.1, 3.5]`
  - 导入时缺字段落库成 `0`
  - 商品分析 prompt 写死「转化率：0%」⇒ **编造的数进了 LLM 的输入**
    ⇒ 模型会拿它当事实推理（「转化率 0% 说明没人买」）⇒ 结论被带偏。

⇒ **NULL ≠ 0**：0 会被读成「真的是 0%」；NULL 才是「没采到」。
  评分器侧已有配套（`_missing()` 把 0 也当缺失 + 池内覆盖率闸门 ⇒ 退出加权 + 重新归一化），
  但**闸门挡不住「假值凑够 60% 覆盖」** ⇒ 必须从**源头**不写假值。
⇒ 往 LLM prompt 里塞数字前先问：**这个数的来源是哪一端？平台真值还是我们自己填的？**
  没有 ⇒ **整行不写**（让模型知道「这项不知道」），别写 0。

### I. 守门人**不能为了让代码通过而放宽标准**

★ 本轮用 AST 扫描「代码里有没有写死的转化率数字」，第一次跑就抓到
  一个 `grep 'conversion_rate'` **漏掉**的假值 —— 它用的是 camelCase 的
  `conversionRate`（藏在示例达人卡的 JSON blob 里）。

⇒ **守门人一旦为了「让代码过」而收窄覆盖面，它就从守门人变成了摆设** ——
  那还不如不写。正确做法是**把代码改对**（把那个假值也清掉）。
⇒ 静态扫描抓事实类问题（代码里有没有硬编码）用 **AST**，不用正则：
  正则会漏（多行 / 变量间接赋值），而且会把「注释里提到这个数字」也当命中（假红）。

### J. 假值清理的**影响面必须实测**，不能靠推理

★ 清掉 137 行假转化率前，先取基线快照（`reco_calibrate.collect_displayed`
  = **用户实际看到的**推荐列表，321 条 / 764 样本），清完再跑一次对比。

⇒ 实测结果：**321 条展示、764 样本，分数一位不差** ——
  因为转化率维度此前就因覆盖率 4% < 60% 被整体剔除 ⇒ 清假值对打分**零影响**。
  （但假值仍必须清：它随时可能凑够覆盖率翻身。）
⇒ **「推理它应该没影响」不算验证**，**当场跑一遍对比**才算。

### K. 测试的**死因**也要说对 —— 「环境没配」≠「代码漂移」

★ 2026-10-26 B 边实测反馈（跨边对抗审查抓到的）：
  A 边写的 `test_current_chain_alive_independent_vendors_is_one` 在**没有 `.env` 的
  worktree** 里红了。B 边判「环境差异，不是代码问题，不用查」—— 方向对，
  但**死因文案是误导的**：环是**按凭据构造**的，没 key ⇒ `text_rings()` 返回**空列表**
  ⇒ 断言读成「独立服务商集合变了」⇒ **让人去查代码，而代码其实没错**。

⇒ 修法（**不弱化闸门**，只把死因分开）：
  ① 链**一个环都没构造出来** ⇒ 报「**环境未配置**」（缺哪个 key / 哪个登录态）
  ② 链构造出来了但**集合不对** ⇒ 报「集合变了」—— 这才是真的代码漂移

⇒ ★ 与 §15 C 同一个道理，只是落在**测试**上：
  **闸门/断言的返回与死因要按「错误语义」写** ——
  「配置错误必须让它红」（§5 能力层测试纪律，不许 skip），
  但**红的原因得让人知道该去查环境还是该去查代码**。
  一个报错文案指错方向的测试，比没有测试更费时间。

⇒ ★★ 2026-10-26（B 边第 2 轮再反馈后补）：**「没配」和「配了一半」要分开**。
  K 的判据是「链**一个环都没构造出来** ⇒ 环境未配置」——
  但**半配置态**（环构造出来了、却全是已死额度池）会**穿过**这条判据
  ⇒ 落到第二个断言 ⇒ 仍报「集合变了」⇒ **死因照样误导**。

  ⇒ 判据要改成「**没有任何可用的独立服务商**（`alive` 空）」而不是「环空」。
  ⇒ 通用版：**「缺失」有两种 —— 一个都没有 / 有但全不可用**。
    只判前一种 ⇒ 后一种会伪装成「配置变了」。判据要问的是
    **「有没有一个能用的」**，不是「有没有一个」。

### L. 静态扫描要分清「代码」和「解释代码的文档」

★ 2026-10-26 修 `_extract_text_from_garbage()` 时踩的坑（形态守卫自己先坏了两次）：

守卫要检查「关键词正则的分隔符不是可选的」，但那函数的 **docstring 里正写着**
老写法 `\s*[:：\-]?\s*` 用来解释历史坑 ⇒ **扫全文就必然误报**。

两种极端都会坏：
- ❌ **不剥离文档** ⇒ **假命中**（注释/docstring 里的示例被当真代码）
- ❌ **连字符串一起清**（AST 把所有 str 常量置空）⇒ **真命中也扫不到**
  （判据本身就写在正则字符串里）⇒ 守卫退化成「找不到就报错」的**假闸门**

⇒ ✅ 正确做法：**只剥注释行与 docstring 区间，保留字符串字面量**。

⇒ ★ 推论：**「静态扫描抓硬编码」类守卫（§16 H 的 AST 扫描）同理** ——
  「扫字符串字面量」时，**注释里可以合法提及**（那是在说明别用它），
  但**代码里写死**才是违规 ⇒ 两端要分清，别一刀切。

### M. 「降档 / 放宽测试前置条件」之前必须**真跑一遍**

★ 2026-10-26 踩的坑：修完 C4 后，我一度想把 `tests/test_kimi_response.py`
从 `live` 降到 `env`（理由：C4 不再是 bug）。

**实测降档 ⇒ 真跑 ⇒ 暴露 3 条 E2E 失败**（那 3 条要**真打 Kimi 上游**拿 `choices`）
⇒ 降不了 ⇒ 保持 `live`。

⇒ ★ 纪律：**「以为能降」会把真上游依赖偷偷带进 CI**，
  而 CI 里那条会**真打生产上游**（花钱、慢、还会因上游抖动随机红）。
  ⇒ 任何「这条测试不需要真环境了」的判断，**必须真跑一遍**再改登记。
  ⇒ 同族：「这条 skip 能不能去掉」「这条守卫能不能放宽」⇒ 一律先真跑。

### N. 「沉默的假验证」—— **退出码非 0 但一个变异都没跑**，比红更危险

★ 2026-10-26（B 边 §12.9 抓到的真问题，A 边修）：

`scripts/mutate_llm_ring_guards.py` 在**没有 `.env` 的环境**（干净 clone / CI / 并行 worktree）
里，基线自检必然红（那条测试要求「必须有可用的独立服务商」）
⇒ 旧逻辑 `if rc != 0: sys.exit(1)` ⇒ 脚本 **exit 1 且 0 个变异被执行**
⇒ **守卫线根本没被验证，却看不出任何异常**（只看到「基线不绿」，
会误以为是代码坏了 ⇒ 去查代码 ⇒ 查半天发现代码没坏）。

⇒ ★ **比「红」更危险**：红至少会说「有问题」；
  **静默地没跑**会让你以为「验过了」⇒ 后续所有基于它的结论都是空的。

⇒ 修法（**两层，缺一层都不够**）：
| 层 | 改哪 | 怎么改 |
|---|---|---|
| ① 测试层 | 那条测试的**死因文案** | 环境性红要**明确说「环境未配置」**（否则脚本认不出来）|
| ② 脚本层 | 变异脚本的**基线自检** | 分两类红：<br>「环境性红」⇒ **跳过变异 + 明确说「这不是守住了，是压根没跑」+ 退出码非 0**；<br>「真漂移」⇒ 照旧报错。|

⇒ ★ **判据要按错误语义**（认「失败输出里有没有那个死因标记」），
  **不是按状态码** —— 两种红的退出码是一样的。

⚠️ ★★ **同款坑的第二层：判据的观察窗口必须覆盖判据要看的东西。**
  本次第一版修完**没生效**：`run_tests()` 只保留 stdout **最后 800 字符**，
  而 pytest 的失败详情在**中段**（末尾只有「1 failed, 23 passed」摘要）
  ⇒ **判据看不见要判的东西** ⇒ 永远认不出环境性红。
  ⇒ 把窗口扩到 **8000** 才通。
  ⇒ **规律：窗口太小 = 判据恒假**（这里表现为「永远认不出环境性红」，
  而「认不出」会被误当成「不是环境问题」⇒ **把环境问题说成代码问题**）。

⚠️ ★ **判据的设计方向要选对**：本条判据的两条变异（标记改错 / 窗口改小）
  都只让判据**变钝**（认不出环境性红 ⇒ 退回「真漂移」分支），
  **不会让判据误判**（把真漂移说成环境问题）⇒ 这是对的。
  ⇒ **宁可漏判环境性红，也绝不把真代码漂移说成环境问题** ——
  后者会让真正的代码 bug 逍遥法外。

### O. 文档漂移也算缺陷 —— **已拍板的事还在表里写「待拍板」**

★ 2026-10-26（B 边 §12.8 核出，A 边改）：

交接单 §3.1「待老大拍板」表里，①②③ 老大**早已拍板并实现**，
表却还写「待拍板」⇒ **代码侧一直干净，错的只是文档**。

⇒ ★ 危害不在代码，在**人**：接手人（人 or 新对话）读表 ⇒ 以为这三条还在等
  ⇒ **重复讨论已定的事**，还会基于过时认知做决策（比缺文档更糟）。

⇒ ⇒ **「待拍板」这类状态表是活文档，必须有人负责同步**。
  本项目约定：**A 边（协调边）维护 §3.1**；别的边核出漂移 ⇒ **只记录不擅改**
  （§12.8 就是这么做的，做得对）。

⇒ **推论：每次「拍板已实现」之后，要回头看一眼状态表** ——
  否则文档会慢慢变成「与现实脱节的第二份事实」
  （这正是 §16 F「两份事实共用一份来源」的变体：这里的两份是「代码」与「文档」）。

### P. 「某侧全绿」要归因到**哪一侧** —— 先看那个文件**在不在** dev 上

★ 2026-10-26（B 边 ④ 澄清，A 边自己复核后更正）：

A 边在交接单里写过一句「合并闭合了一个已知盲区：A 边守卫全绿 8/9 ⇒ 那 8 条 0 绿 0 红（根本没跑）」。
**这是错的** —— A 边把两边搞混了：

- 合并前那次 0 绿 0 红的是 **B 边守卫**（`tests/test_credits_gate_adversarial.py` 当时**还没合进 dev**
  ⇒ 文件不存在 ⇒ 跑不起来）；
- **A 边守卫是另一个文件**（`tests/test_credits_gate.py`），它**一直在 dev 上**，19 条一条不少。

⇒ 实测复核（脚本输出里 `a_green` 有计数）：
| 守卫 | 文件 | 条数 | 守哪一面 |
|---|---|---|---|
| A 边 | `tests/test_credits_gate.py` | 19 | **入队/计费侧**（charge、ledger、cost table、幂等、零成本白名单）|
| B 边 | `tests/test_credits_gate_adversarial.py` | 20 | **消耗/对抗侧**（`COMPUTE_SITES` 登记表）|

⇒ 那 8 条变异改的是**消耗侧** ⇒ A 边守卫**本来就抓不到** ⇒ **这是设计（职责互补），不是缺口**。
⇒ 而且实测里 A 边守卫在**第 2 条**变异时**抓到了 2 条** ⇒ **证明它在正常工作**。

⇒ ★ **纪律：把「某侧 0 绿 / 全绿」归因到「哪一侧」之前，
  先确认那个测试文件在不在 dev 上**：
  - **不在** ⇒ 那侧**根本没跑**（= 结构性裸奔，合并顺序问题）；
  - **在 + 跑了** ⇒ 那是**真的「跑了但没抓住」**（= 覆盖面问题）。
  ⇒ **两者含义完全相反**，混了就会把「设计上的职责分工」误报成「缺口」，
  或者把「真缺口」误当成「本来就该抓不到」。

⇒ ★ **元教训：这句话是我凭「合并前那次输出」推断的，没实测** ——
  脚本输出里**本来就有 `a_green` 计数**能区分两种情况，我该看没看。
  ⇒ **推论：结论要区分「跑了没抓住」和「压根没跑」时，
  先找一个能区分这两者的观测量（这里是 `a_green` 计数），再看结论。**
  ⇒ 同族：「工具报告成功」≠「工作区真的干净」（§0.5 第③条）——
  **先找到能区分的那个观测量**。

### Q. 「命中缓存」**不是一个东西** —— 判断能不能免费，要看**命中之后还调不调上游**

★ 2026-10-26 老大拍板「没有消耗肯定是 0 分」时踩出来的（差点改错）。

**产品里两条路径都叫「缓存命中」，消耗量却完全不同**：

| 路径 | 命中后 | 消耗 | 该收 |
|---|---|---|---|
| **结论缓存命中** | executor 拿到结论**直接 return**（不调浏览器、不调 LLM、不写缓存）| **零** | **0 分** |
| **商品数据缓存命中** | 只省了「开浏览器采集」⇒ **LLM 照跑** | **有** | 照收 |

⇒ ⚠️ **直接把价格改成 0 会误伤第二条** —— 那条 LLM 照跑，
  收 0 分 = **真·白干不收钱**（比「收多了」更糟）。

⇒ ★ **判据不是「命中了没有」，是「命中之后到底还调不调上游」。**

⇒ ★ 附带的通用坑：**同一个 action 名承载了两种消耗量**
  ⇒ 价格只能有一个 ⇒ 必然有一边收错 ⇒ 而且**流水里分不出来**（对账时看不出是哪条）。
  ⇒ 修法：**按消耗量拆 action**（零消耗的走**零成本入口** `enqueue_free`，
    有消耗的走**原子入口** `charge_and_enqueue`）⇒ 流水 action 不同 ⇒ 可对账。

⇒ ★★ **报表层的同一条纪律（2026-10-26 18:50 补）**：
  「一个 action 只承载一种消耗量」**不只管流水层，报表层同样成立** ——
  **计数可以合并，算账不能合并**。
  ⚠️ 实例：复用看板把「结论缓存命中」与「商品数据缓存命中」**两个 action 求和**算「复用命中数」
  ⇒ **计数对**（少算 = 复用率偏低），但拿同一个和去算「省了多少分」⇒ **两处都错**：
  ① 结论命中是零消耗（省整笔 20 分）却按 15 分算 ⇒ **每次少算 5 分**；
  ② 商品数据命中 **LLM 照跑、根本没省 LLM** 却算进了「省了 N 次 LLM」⇒ **多算**。
  ⇒ ⇒ **合并的前提是「两者同质」** —— 命中次数同质，**消耗量不同质** ⇒ 分子可合、分值不可合。
  ⇒ 修法：抽出**纯函数**按各自消耗量算 + 看板**分行说明** + 守门人锁住两条口径。

⇒ ★ 拆开时的粒度坑：零成本入口的调用点要登记在 `ZERO_COST_ALLOWED` 里，
  而那个登记是**函数级**（返回 `(文件名, 函数名)`）⇒
  **把零成本路径独立成函数**，别挂在主路由函数上 ——
  否则主函数里未来新增的违规 `enqueue_free` 调用会**被一并放过**。
  ⇒ 变异实测证明了：独立成函数后，「误改有消耗的那条」会被白名单守门人抓住；
  挂在主函数上就不会。

### R. 「按功能收费，不按调用次数收费」的**真正判据是「有没有消耗算力」**

★ 2026-10-26 老大拍板后落定的口径（`config.py` 已写死同一句）：

- 收费的判据 = **这次请求有没有真的消耗算力**（调了 LLM / 开了浏览器 / 打了上游）；
- 「命中缓存」**不是**判据 —— 命中了也可能**照跑 LLM**（那是**部分**省）；
- 「没消耗」= **不扣分**，但**流水要留痕**（`cache_hit=1`）⇒ 对账不受影响。

⇒ ⇒ 所以「问答缓存命中 0 分 / 结论缓存命中 0 分 / 商品数据缓存命中 5 分」
  三条**并不矛盾** —— 它们各按**自己真实有没有消耗**收费。

### S. ★★ 推 `origin/main` 需要「拍板凭据」—— 没凭据就是替老大拍板

★ 2026-10-26 18:05 实锤：`origin/main` 被推了**两次**（17:46:20 / 17:57:57）。
⚠️⚠️ **2026-10-26 18:25 更正**：老大**当时已拍板**「main 合 dev」
（问卷 `ask_0122d2a436085c06a82ea037`，17:45:56 提交 ⇒ 第一次推送在授权后 **24 秒**）
⇒ **不是「未拍板就执行」，是「拍板后执行」**；
我 18:05 那句「老大从未就 ⑥ 拍板」是**回查范围不够导致的假阴性**（见 §16 T / §16 U）。
⇒ ★ **但下面这些纪律依然全部成立**（与「是否授权过」无关）：

**取证链**（remote-tracking reflog 带时间戳；`.git` 共享 ⇒ 两边都看得到）：
```
64f3ee2 → 30b19b8   17:46:20   update by push
30b19b8 → 2c680b4   17:57:57   update by push
```

执行方 = **B 边**（证据：它自己会话日志里出现 `git push origin origin/dev:main`；
它 17:59 的汇报把「main 合并」列为自己完成的工作；时间线与它 17:55 rebase 完成吻合）。

⚠️ ★ **根因不是「裸 push」** —— 它用的是**显式 refspec**（`origin/dev:main`），**不是**裸 `git push`。
⇒ ⇒ **真正的三个根因**：
1. **缺「拍板凭据」校验** —— 铁律写了「main 不动」，却**没规定「谁凭什么能推 main」**；
   双方对「老大说了什么」认知不一致（B 边认为被授权、A 边查记录查不到）⇒ **靠记忆把关**。
2. **推完漏报** —— 推 `origin/main` 只写进了给老大的汇报，**没同步给 A 边**
   ⇒ A 边从 reflog 看到「有人推了 main」却查不到出处 ⇒ 白花一轮取证。
3. **授权认知靠转述** —— 见下方姊妹条。

⇒ ★ **显式 refspec 的真正价值 =「可归因」**（不只是「防误推」）：
  本次**正是因为**命令是显式的（`origin/dev:main` 而非裸 `git push`），
  才能在 B 边会话日志里 grep 到执行方、坐实归属。
  ⇒ ⇒ **破坏力可见 + 事后可归因** —— 这才是显式 refspec 的两个真实收益。
  ⇒ 裸 `git push` 的额外风险（local `main` 领先时顺手把未推 commit 带进 main）仍然要禁。

⇒ ⇒ ✅ **⑥ 已结案**（2026-10-26 19:00 执行 + **19:07:29 老大亲口追认**「认可现状：main 跟 dev 走」+「main 只由 A 边推」）：
  老大 17:45:56 已拍板「main 快进到 dev」
  （凭据：问卷 `ask_0122d2a436085c06a82ea037` 第 2 问），
  协调边据此执行 `git push origin HEAD:main`（**FF，非 force**）
  ⇒ `origin/main` == `origin/dev` == `8feadfe`，**零分叉**。
  ⇒ **往后 main 跟 dev 走**，且**只有协调边推**（防撞规则，不是新决策）。

⇒ ★ **纪律**：
1. **推 `origin/main` 必须有「拍板凭据」** —— 授权只认**原始记录**（问卷原文 / 老大原话 / 明确指令），
   **转述不算**；拿不到 ⇒ **不许推**；
2. **任何边都不许推 `origin/main`** —— main 只由**协调边（A 边）在 dev 验收后统一推**；
3. 其余边一律写全 refspec：`git push origin HEAD:dev` / `HEAD:feat/<branch>`；
4. **禁止裸 `git push`**（连 `git push origin` 都不行 —— 它推的是「当前分支」，
   而部署目录的当前分支默认就是 `main` ⇒ 那是**默认状态**，不是选择）；
5. **推完立刻通知所有边**（推 main 尤其）—— 漏报会让对面从 reflog 反推，白花一轮取证；
6. 推之前自查：`git rev-list --count origin/dev..origin/main`（>0 = 有人推了 main）。

⇒ ★ 附带一条：**「未拍板就执行」比「拍错了」更贵**。
  本次 main 多推了 92 个 commit，**看似要回退**，
  但因 dev 与 main 已**零分叉** ⇒ 回退反而白丢 ⇒ **回退本身变贵了**。
  ⇒ 「顺手执行」剥夺了老大的选择权，并让回退变贵。

⇒ ★ **姊妹条：「谁说老大同意了」≠「老大同意了」**。
  跨边转述**不是授权凭证** —— B 边曾声称「老大授权过推 main」，
  回查会话原始记录后：老大唯一答过的问卷**只含 ④ 那一问**（原文「没有消耗肯定是0分啊」），
  ⑥ 问卷 `answered: false` ⇒ ~~**推 main 属未授权动作**~~ ⚠️⚠️ **这句已作废**（见上方更正 + §16 T）。
  ⇒ ⇒ **授权只认原始记录**（问卷原文 / 老大原话 / 明确的手动指令），
  转述只能当线索；拿不到原始记录 ⇒ **按「未授权」处理**。

⇒ ⚠️⚠️ **2026-10-26 18:20 更正：上面那条「推 main 属未授权动作」的判定本身是错的 —— 老大授权过。**
  错因 = **回查范围不够**：那两条问卷 ID（`ask_7f2c1bd…` / `ask_7c18f684…`）**都在 A 边自己那条会话的快照里**，
  而老大答 ⑥ 的原始记录在 **B 边会话**的快照里（`ask_0122d2a436085c06a82ea037`，`step_count=2`，`answered=true`，
  提交于 `1793007956641` = 17:45:56，第 2 问原文「合（推荐）：main 快进到 dev，部署目录 main 同步」）。
  ⇒ **不是「记录里没有」，是「记录在另一条会话里」**。
  ⇒ ★ **姊妹条「谁说老大同意了 ≠ 老大同意了」本身仍然成立、仍然要守**；
  但它**推不出**「未授权」—— 那是**另一步**，那一步需要**跨全部相关会话**取证。
  ⇒ 详见下面 **T**。

### T. ★★ 回查「授权凭据」必须**跨全部相关会话** —— 只查自己那条 = 系统性假阴性

★ 2026-10-26 18:05–18:20 实锤（本轮差点在铁律里写进一条**假事实**）。

**坑**：A 边为核实「老大有没有授权过推 `origin/main`」，回查了会话原始记录，
结论写进 `AGENTS.md §16 S` + 交接单 §17.1.1：
「老大唯一答过的问卷只含 ④ 那一问」⇒「⑥ `answered:false`」⇒「**推 main 属未授权动作**」。

**真相**：老大**答过 ⑥**，原文「合（推荐）：main 快进到 dev，部署目录 main 同步」，
与 ④ 在**同一份问卷、同一秒提交**（`submittedAt = 1793007956641` = 17:45:56）。

**为什么查不到**：两份问卷落在**不同的会话快照文件**里 ——
| 记录 | 所在会话快照 |
|---|---|
| A 边引用的 `ask_7f2c1bd…`（只含 ④）| A 边自己的会话 |
| A 边引用的 `ask_7c18f684…`（⑥，未答）| A 边自己的会话 |
| ★ **老大答 ⑥ 的 `ask_0122d2a436085c06a82ea037`（④+⑥ 两问）** | **B 边的会话** |
⇒ A 边只 grep **自己那条会话** ⇒ **命中 0 条 ⑥ 的授权记录** ⇒ 得出「未授权」。

**元教训（比结论更重要）**：
1. **「记录里没有」≠「记录里没有」** —— 可能是**记录在另一条会话的快照里**。
   ⇒ 查授权前，**先把所有相关会话的快照全扫一遍**（本轮是 `~/.minimax/v2/sessions` 下所有
   `*/snapshots/*.jsonl`）—— ⚠️ **别用 `**` 通配**（bash 默认 `globstar` 关闭 ⇒ 匹配 0 个文件 ⇒ 假阴性）：
   ```bash
   find ~/.minimax/v2/sessions -name '*.jsonl' -path '*snapshots*' -exec grep -l "<关键 ID>" {} \;
   ```
   ⇒ `find` 覆盖面**比 python `glob` 更广**（后者跳过 `.` 开头的隐藏目录）⇒ 取证**用 `find`**。
   ⇒ ★ **命中之后还要验结构**：ID 命中 ≠ 那行是原始答卷 ⇒ 见 **§16 T 第 10 点**。
2. ⇒ **假阴性会直接写进铁律文件** —— 本轮若 A 边的 `§16 S` 落地成「未授权动作」，
   **每个新会话都会自动加载这条假事实** ⇒ 一次查证疏漏 = 污染全项目。
   ⇒ ⇒ **「负向结论」比正向结论更需要多源交叉**：查不到 ≠ 不存在。
3. ⇒ **授权只认原始记录**（这条**不变**）：问卷原文 / 老大原话 / 明确指令；
   **转述永远只是线索**。
   ⇒ 但「拿不到原始记录 ⇒ 按未授权处理」这条**要补一个前置条件**：
   **「拿不到」必须是在「已跨全部相关会话查过」之后成立的**，
   否则那是**伪否定**（没找到 ≠ 不存在），会误伤已经合规的行为。
4. ⇒ **反向自证也用同一把尺**：本轮 B 边不靠记忆、靠**同一条会话快照的原始 `details.answers`** 自证授权存在
   ⇒ 双方用**同一份原始记录**、**同一套判据**对账 ⇒ 结论才闭合。
5. ⇒ ★ **授权的「完整证据链」= 发出端 + 接收端 + 执行端，三段都要有**
   （本轮补齐后闭环，全文见 `docs/PARALLEL-HANDOFF-2026-10-25.md §18.2` / `§18.2.1`）：
   - **发出端**：问卷**原文**（问的是什么、选项是什么）+ 发起时的意图
     ⇒ 快照里 `toolCall.arguments.steps[]` 拿得到；
   - **接收端**：老大**实际选了什么** ⇒ 快照里 `toolResult.details.answers[]` 拿得到；
   - **执行端**：实际执行的命令与结果 ⇒ reflog / 命令历史。
   ⇒ ⚠️ **只看接收端不够**：选项文案相近、问题不同，答案的**指向**就不同。
   ⇒ ⚠️ **尤其**：一份 `answered: false` 的问卷，只能证「**那一份**没人答」，
   **不能证「老大没答过这个问题」** —— 他可能在**另一份问卷**里答过（同一件事被问两遍）。
   ⇒ ⇒ 判「未授权」前，必须先确认：**全盘没有第二份答过它的问卷**。

6. ⇒ ★★ **取证还要查「实时会话历史」，不能只查快照文件** ——
   快照是**定期刷盘**的（实测本机最新落盘时间落后约 **1 小时**）
   ⇒ **最新的答卷可能还没进快照** ⇒
   **一次「grep 全部快照 = 0 命中」既不能证「不存在」，也不能证「没答过」** ——
   还要查**发起那条问卷的会话的实时历史**（`mavis session messages`）。
   ⇒ 本轮第三次差点踩同一个坑：查老大 19:07:29 那份答卷时快照命中 **0**，
   真实原因是**还没落盘**，不是**不存在** ⇒ 已按 §16 U 写成「待快照落盘后复核」，
   **没有**写成「老大没答」。

7. ⇒ ★★ **授权只对它自己问的那个问题有效** ——
   拿**一份问卷**的 `answered:false` 去否定**另一件事** = **答非所问**。
   ⇒ ★ **本轮我的第二个错因**（第一个是「只查自己那条会话」，B 边 19:05 指出）：
   老大 **17:45:56** 答的是「**要不要现在把 main 合到 dev**」（本次动作，B 边会话）
   与 **19:07** 答的是「**往后 main 由谁推**」（治理归属，我的会话）—— **是两个不同的问题**；
   我却用**后者的答卷状态**去否定**前者** ⇒ 推不出「未授权」。
   ⇒ ⇒ **三段证据链里，「问的是什么」必须与「要证明什么」逐字对齐** ——
   **问题文本不同 ⇒ 答案不能互相代替**。
   ⇒ ⇒ 落成一句可复用的问法：**「我要证明的这件事，对应的是哪一问？」** 答不上来 ⇒ 就没有证据。

8. ⇒ ★★ **跨边回执到达时，先 re-verify 再动手 —— 而且「回执说的」和「现状」要分开判** ——
   ★ 2026-10-26 20:0x–20:5x 实证：B 边连续发来 **5 条回执**，全部是**旧基点**快照
   （引用的 `ad3ac46`/`110c3c0`/`b20bbfe`/`de48dab` 都已被后续进展超越）
   ⇒ ★ **每一条的内容都已由后续动作闭合** ⇒ **一条都不能照着做**
   ⇒ ⇒ **三种状态要分开判**：
   | 判据 | 回答什么 | 怎么查 |
   |---|---|---|
   | **内容是否已在 dev** | 它说的那件事做完没 | `git merge-base --is-ancestor <c> origin/dev` |
   | **是否还有未合内容** | 它有没有新东西要给我 | `git log --oneline origin/dev..origin/<对方分支>` |
   | **回执的基点是否过期** | 它说的现状还可信吗 | 比对它引用的 hash 与当前 `origin/dev` |
   ⇒ ⇒ **「它说已完成」≠「已完成」**（本轮第 1 条的「行号差 1」就是照着它的数字写进去的假事实）
   ⇒ ⇒ **「它说有东西要合」≠「真要合」**（本轮 `de48dab` 已合，它仍说「领先 1 个」）
   ⇒ ⇒ **纪律**：收到跨边回执 ⇒ **先跑上面三条查询** ⇒ 再决定动不动手
   ⇒ ⇒ ★ **「回执是快照，现状是事实」** —— 与 §16 T 第 6 条（快照落盘滞后）同源，
   **但方向相反**：那次是「快照旧、现状新」，这次是「**回执旧、现状新**」⇒ **两边都要以现场查询为准**。

9. ⇒ ★★ **取证脚本的「过滤条件」本身要先验证 —— 过滤可能把答案**自己**滤掉** ——
   ★ A 边 19:05 自查发现：它第二次扫 B 边会话时，脚本带**两个过滤条件**
   （`role == "user"` + 正文长度 ≤ 1500），**结果把老大那份答卷直接滤掉了**
   ⇒ **第二次假阴性**（第一次是「只查自己会话」）。
   ⚠️⚠️ **2026-10-26 19:50 A 边复核更正：真凶不是「长度过滤」，是「role 过滤」** ——
   B 边 `7e7241a` 写的「长度阈值把答案滤掉」**归因错了**（详见下方「实测数字」的判别实验）。
   ⚠️ ⇒ **这与第 1 条是两种不同的错因**：
   - 第 1 条 = **查的范围**不够（没查别的会话）；
   - 本条 = **查到了但没看见**（过滤条件把命中项滤掉）⇒ **范围对、结果仍为假阴性**。
   ⇒ ⇒ **实测数字（本机，2026-10-26，A 边逐行实测）**：
   ```
   line 340（B 边那条 bash 的输出）: role=toolResult  整行 1844 / 正文 339
   line 341（问卷发出 ask_user）  : role=assistant   整行 2535 / 正文 147
   line 342（老大答卷）           : role=toolResult  整行 1573 / 正文 768   ← ★ RID 在这里
   ```
   ⚠️⚠️ **B 边 `7e7241a` 的行号整体差 1**（它写「答卷=341 / 问卷=340」，
   真值是「**答卷=342 / 问卷=341**」）；**字符数全对**（768/1573、147/2535）
   ⇒ **数字对、行号错** ⇒ 引用前必须自己逐行量（§16 U 同款）。
   ⇒ ⇒ ★★ **真凶判别实验（A 边 19:50 复刻那次扫描，四组对照）**：
   | 扫描条件 | 命中 |
   |---|---|
   | `role=="user"` + 正文 ≤1500（原脚本） | **0 条** ❌ |
   | **只去掉长度过滤**（保留 `role=="user"`） | **0 条** ❌ ⇒ 长度不是原因 |
   | **只去掉 role 过滤**（保留正文 ≤1500） | **命中 line 342** ✅ |
   | 两个都去掉 | 命中 line 342 ✅ |
   ⇒ ⇒ ★ **结论：把答案滤掉的是 `role=="user"`** ——
   **答卷在快照里是 `role=toolResult`**（问卷工具的返回值），**不是 `role=user`**；
   而正文 768 < 1500 **本来就能通过长度阈值** ⇒
   **「长度过滤滤掉了答案」这个说法是错的**（B 边 `7e7241a` 已更正）。
   ⇒ ⇒ ★ **由此得到一条比原版更狠的纪律**：
   **过滤器不止有「长度」一种 —— `role` / `type` / 字段名这类「结构假设」同样会滤掉答案**
   ⇒ **答卷不一定长得像「用户消息」**（它可能是工具返回值、可能落在别的 role 下）。
   ⇒ ⇒ **「命中 0 条」之前必须先问：我的过滤器会不会把命中项本身滤掉？**
   ⇒ ★ **纪律**：
   1. **过滤器只能用来「缩小噪声」，不能用来「判定不存在」** ——
      要下否定结论的那一次扫描，**必须有一条不过滤的原始扫描**做对照；
   2. **阈值要自己校准** —— 先在**已知存在**的样本上跑一遍过滤器（正样本自检），
      确认它**不会**滤掉该样本，再拿它去查未知；
   3. ⇒ **与 §16 U 合起来读**：
      **U 管「结论怎么写」（范围 + 措辞），本条管「扫描怎么跑」（过滤器 + 阈值）**
      ⇒ 两者都要过，**否则照样出假阴性**。
   4. ⇒ ★★ **「引用的行号」也必须自己量 —— 0-based 索引 ≠ 文件行号**：
      ⚠️ **本条是我自己踩的**（B 边 `7e7241a` 写的「答卷=line 341 / 问卷=line 340」
      **整体差 1**；A 边 19:50 逐行量出真值是「**答卷=342 / 问卷=341**」——
      字符数全对、**只有行号错**）。
      - **根因**：脚本里 `lines[341]` 是 **0-based 索引**，我却当成了**文件第 341 行**
        （真实行号 = 索引 + 1）⇒ **写进文档的「line NNN」全错 1**。
      - ⇒ **任何写进文档的「第 N 行 / line N」都必须用 `grep -n`（1-based）复核**，
        **不能直接抄脚本里的索引**。
      - ⇒ **「字符数对、行号错」是「数值型断言里最容易混过去的一类错」** ⇒
        引用前**逐个量一遍**（§16 U 同款：**范围是结论的一部分，行号也是**）。
   5. ⇒ ★★ **同一件事写两遍，就会有两遍不一样 —— 校对不能只校一处**：
      ⚠️ **实测**（2026-10-26 20:0x）：我在**同一笔 commit** 里把行号写成了两个版本 ——
      | 位置 | 内容 | 对错 |
      |---|---|---|
      | **commit message** | `答卷 = line 342 / 问卷 = line 341` | ✔ **对** |
      | **AGENTS.md 正文** | `真值是 341 / 342` | ✘ **写反了** |
      ⚠️⚠️ **上表第二行是「故意举的反例」（带 ✘ 标记）** —— 那一行**就是错的**，
      正确写法是「**答卷=342 / 问卷=341**」⇒ **它不是残留错误，是被引用的原始错例**。
      ⇒ ★ **读这类表格别按「看到旧数字就是残留」判**，**先看它有没有 ✔/✘ 标记**
      （A 边 20:1x 复核时就差点误判成「旧错没清干净」）。
      ⇒ 讽刺且必须记：**这条纪律本身就是防「行号错」的，而它自己的正文里就把行号写反了**；
      ⇒ 且**只有正文错**（message 对）⇒ 说明**不是理解错，是誊写时错**。
      ⇒ ⇒ **纪律**：
      1. **事实一旦要写两遍（正文 + commit message / 正文 + 报告 / 文档 + 代码注释）⇒ 两遍都要校**，
         **不能因为「另一处我刚写对了」就跳过**；
      2. ⇒ **合并 = 最后一道校对闸门**：合入前**逐处读一遍 diff**，
         **不是形式主义** —— 本轮 A 边就是靠这一步抓到我这两处笔误（行号写反 + 引号未闭合）；
      3. ⇒ 配 §0.4：**合并前先 `git branch backup/<name>-<ts> <ref>`**
         （A 边这轮留了 `backup/pre-merge-9c8e4be-20261026-1955`）⇒ 校对改错了能回退。

10. ⇒ ★★ **「命中 ID」≠「那行就是原始记录」—— 命中之后还要验结构**：
   ⚠️ **本轮 B 边 19:0x 实测**：用 `find … -exec grep -l "<问卷ID>"` 扫全盘，
   **同一个问卷 ID 命中 3 个快照文件**，但**只有一份是原始答卷**：
   | 命中文件 | 那行是什么 | 是不是原始记录 |
   |---|---|---|
   | B 边 `g000000000001--ctx_77ba217a-*.jsonl` | **line 342 = `<questionnaire-response>`**（`role=toolResult`）| ✅ **是** |
   | B 边 `g000000000002--ctx_e0bd9c00-*.jsonl` | line 20 = **bash 输出**「=== 找 ask_user 问卷 ID ===」| ✘ 只是**检索输出** |
   | A 边 `g000000000046--ctx_15e290f3-*.jsonl` | 多行 = A 边后来**引用**这段原文 | ✘ 只是**引用** |
   ⇒ ★ **两件必须做的事**：
   1. **命中后验结构**：原始答卷 = `role=toolResult` 且正文含 `<questionnaire-response>` + `<requestId>`；
      **bash 检索输出里也会出现同一个 ID** ⇒ 只看「命中」会把**引用**当成**原始记录**。
   2. ★ **行号必须连快照文件名一起钉** —— 同一会话的**不同快照文件行号不同**
      （同一份问卷：`g000000000001` 里是 line 342；同会话的 `g000000000002` 里是 line 20，
      **而且那行是另一条消息**）⇒ 只写「line 342」不写文件名 = **半条引用** ⇒ 换个文件就指错地方。
   ⇒ ★ **推论：命中集合会随时间增长**（别的会话一引用就多一份）
   ⇒ **不要在文档里写死「命中 N 个」** ⇒ 写「**命中 ≥1，权威那一份是 <文件名> 的 <行号>**」+ 给自查命令。

11. ⇒ ★★ **取证命令本身也要先验证「它到底能不能匹配到文件」** ——
    ⚠️ **本轮 B 边 19:0x 实测**：bash **默认 `globstar` 关闭** ⇒ `sessions/**/snapshots/*.jsonl`
    **匹配 0 个文件** ⇒ 整段路径原样传给 `grep` ⇒ 它报 `No such file or directory`
    ⇒ **照着这条命令做取证的人，永远拿不到证据**（而且**不报错** ⇒ 又一个假阴性）。
    ⇒ ★ **⇒ 「假可复现」是假阴性的第 4 种形态**：
    ① 过滤器把答案滤掉（第 9 点）② 查符号≠查行为（§16 V）③ 命中≠原始记录（第 10 点）
    ④ **命令跑不出结果**（本条）⇒ 前三条查「查什么」，**这一条查「怎么查」**。
    ⇒ ⇒ **纪律**：**写进文档的复现命令，必须自己真跑一遍并把命中数贴进文档**
    （**看起来能跑 ≠ 跑得出结果**）；⇒ 命令**跑不出结果**时，第一反应是
    **「查了 0 个文件」而不是「查了没有」**。
    ⇒ ⇒ **本项目取证命令的权威写法**（实测可跑、覆盖面最广）：
    ```bash
    find ~/.minimax/v2/sessions -name '*.jsonl' -path '*snapshots*' -exec grep -l "<关键 ID>" {} \;
    ```
    ⇒ ⚠️ **不要用 python `glob`** 作替代：它**跳过 `.` 开头的隐藏目录** ⇒ 覆盖面小于 `find`
    （本项目 `.history-mutation-agent-host-*/snapshots/` 就在隐藏目录里）。

⇒ ★ **落地口径**（新增，任何边都适用）：
> 声称「某操作**已获授权**」或「某操作**未获授权**」之前，
> 必须完成**跨全部相关会话快照**的取证，并在结论里**写明查过哪些会话快照文件**。
> 查不到 ⇒ 写「**在已查的 N 个会话快照中未找到**」，**不许**写「**不存在**」。

### U. ★★ 「负向结论」比「正向结论」贵 —— 「没找到」只能写「在已查的 N 处未找到」

★ 2026-10-26 18:25，本项目**第一次**把一条**假事实**写进了这份**每个新会话自动加载**的
  `AGENTS.md`（§16 S 姊妹条：「推 main 属未授权动作」）⇒ 18:20 被 B 边用**可复现的快照 ID** 推翻。

**错因链**（每一步单看都「合理」）：
1. 要判定一件**否定事实**（「老大没授权过」）⇒ 查了**自己那条会话**的快照；
2. 命中 0 条 ⇒ 写下「**记录里没有**」⇒ 推出「**不存在**」；
3. 把它写进**规则文件** ⇒ **每个新会话都会自动加载这条假事实**。

⇒ ★ **纪律（比 §16 T 更前一步 —— T 管「怎么查」，U 管「结论怎么写」）**：
1. ★ **否定结论必须写明取证范围** ——
   写「**在已查的 N 个会话快照 / M 个文件中未找到**」，**不许**写「**不存在**」；
   ⇒ **范围是结论的一部分，不是脚注**。
2. ★ **取证范围要覆盖「同一件事可能在别处被问过」** ——
   同一件事被问两遍是常态（两边各发一份问卷）⇒
   **一份 `answered:false` 只能证「那一份没人答」，不能证「老大没答过这个问题」。**
3. ★ **写进自动加载的规则文件前，多花 30 秒全盘扫一遍** ——
   ```bash
   find ~/.minimax/v2/sessions -name '*.jsonl' -path '*snapshots*' -exec grep -l "<关键 ID>" {} \;
   ```
   ⚠️ **不要用 `**` 通配**：bash 默认 `globstar` 关闭 ⇒ `sessions/**/snapshots/*.jsonl`
   **匹配 0 个文件** ⇒ grep 直接报 `No such file or directory` ⇒ **又是假阴性**。
   ⚠️ 也**不要只写 `grep -rl` + python `glob`**：python 的 `glob` **跳过 `.` 开头的隐藏目录**
   ⇒ 覆盖面小于 `find`（本项目 `.history-mutation-*` 子树就在隐藏目录里）⇒ 取证**用 `find`**。
   ⇒ ★ **命中之后还要验结构**：ID 命中 ≠ 那行是原始答卷（**§16 T 第 10 点**）。
   ⇒ **一条假事实的传播成本 = 全项目所有会话的认知污染**。
4. ⇒ **被推翻时不要护着自己写的原文** —— 改它 / 加更正块 / 标注作废都行，
   **但不能让它继续以「事实」的姿态留在规则里**。
5. ⇒ ★ **协作的正确姿势不是「谁先下结论」，是「谁能把结论钉到原始记录上」** ——
   本次是 B 边用**同一条会话快照的原始 `details.answers`** 自证授权存在，
   双方用**同一份原始记录 + 同一套判据**对账 ⇒ 结论才闭合。
   ⇒ ★ **而「负向结论」几乎无法自证** ⇒ 所以它更容易错 ⇒ **所以它要先怀疑自己**。

### V. ★★ 按「展示名」去外部系统找对象 = **找错对象** —— 定位一律用不可变 ID

★ 2026-10-26 21:0x，老大按 runbook §2 的表去飞书后台找「机器人」**找不到** ⇒ 才暴露出
**那张表的最后一列是 openclaw 配置里的自定 `name`，不是飞书后台的应用名**。

**错因链**（每一步单看都「合理」）：
1. runbook §2 的表把 openclaw 侧的自定 `name`（`貂蝉` / `租户Bot` / `Helen` / `木木`）
   直接写在「用途」列 ⇒ **读起来像「后台应用名」**；
2. 实测：openclaw 账号标签 `t_default`、name 写「租户Bot」的 app_id
   `cli_aa420db06a78dcc5`，在飞书后台叫「**貂蝉**」⇒ **同一个「貂蝉」字样指两个不同的 app**；
3. ⇒ ★ **照旧表去后台按名字找，会找到 `t_default` 那个 app 并重置它的 secret**
   ⇒ `default` 的 secret 根本没换、`t_default` 的被换掉 ⇒ **机器人掉线**。

⇒ ★ **纪律**：
1. **外部资源一律有两个名字**：**展示名**（可重名 / 可改 / 中文 / 人写的）
   vs **不可变 ID**（`app_id` / `repo_id` / ARN / resource id）
   ⇒ **定位一律用不可变 ID；展示名只能当交叉验证的线索**。
2. **不要把「名称对上了」当作定位成功的证据** ——
   ★ **唯一证据 = 打开后读回来的不可变 ID 与目标一致**。
3. ⇒ **同名 ≠ 同对象**（本例是「同标签 ≠ 同应用名」）；
   ⇒ 与 §16 U「命中 ≠ 原始记录」同族：**「找到了一个叫这个名字的东西」≠「找到了那个东西」**。
4. ⇒ **写表格时列名必须自证语义** —— 「用途」这种模糊列头会让读者自动脑补成
   「这个东西在外部系统里的名字」⇒ **列头要写清「哪一侧的什么」**
   （本次改成「openclaw 侧 name（**不是飞书后台应用名**）」）。
5. ⇒ ★ **「后台找不到」≠「对象不存在」** —— 先排除「**登错企业 / 登错账号 / 权限范围不同**」
   （本例 4 个 app 里 3 个在**另一个飞书企业**，当前企业下当然找不到）
   ⇒ **直达 URL 能开 = 有权限；跳 403 = 当前企业看不到，不等于不存在**。

### W. ★★ 盘点「全量对象」的三件套 —— **「没找到」必须区分「没看成」**

★ 2026-10-26 21:3x–21:4x，密钥副本地图「第三次漏项」在**两侧各暴露一个同款病根**：
B 边在 `for row in cur.execute(...)` 循环体内又调 `cur.execute(...)` ⇒ **同一 cursor 被复用**
⇒ **外层遍历被静默截断**（只跑了第一行，**输出看起来正常、不报错**）；
A 边在全盘扫描里写了 `except Exception: pass` ⇒ **把「db 打不开」和「db 打开了但没命中」
混为一谈** ⇒ **系统性假阴性**。
⇒ 两者合起来是同一条：**静默失败 = 「没看成」被当成「没有」**。

⇒ ★ **纪律：任何「枚举全量对象」的循环，三件套缺一不可**：
1. ★ **不吞异常** —— 打不开/读失败的条目**必须单独计数并报出来**
   （`打开成功 N / 打不开 M`）⇒ **绝不能让它们混进「没找到」**；
2. ★ **先 `fetchall()` 再遍历** —— 复用同一个 cursor 会静默截断结果集
   （或每层用独立 cursor）⇒ 「输出正常」不等于「结果完整」；
3. ★ **枚举范围不能手写假设** —— 目录名/文件名可能有**拼写变体**
   （本机同时有 `solobrave-*` 与 `solubrave-*`）
   ⇒ ⇒ ★ **「全盘 join」若 roots 是手写枚举的，它就只是「手写清单的 join」，不是全盘**
   ⇒ 用 `find <根> -name '*.db'` 真全盘，或把变体一起枚举。

⇒ ⇒ ★ **量「某一列/某个字段」的长度或内容，必须让数据源自己算**
（`length()` / `SELECT 该列` / `fetchall()` 按列取）
⇒ **绝不用「正则掩码 + 手工配对」去量** ⇒ 那会把同一行里**别的**串误认成目标列
（本项目真实踩过：`app_secret_enc` 被误读成 44，实际两行都是 60）。

⇒ ⇒ 与 §16 U（负向结论要写取证范围）、§16 V（同名 ≠ 同对象）同族：
★ **「没找到」的结论里，必须同时写出「看了多少、其中多少没看成」。**

### X. ★★ 「可恢复删除」**不等于**「删除」—— 明文凭据的最后一公里

★ 2026-10-26 22:0x，实测坐实：**老大轮换 GitHub PAT 时，临时文件
`~/.solobrave_github_pat` 写完删掉后被拖进了废纸篓**
⇒ 3 份**现役** PAT 明文（md5 与 keychain 现役那把**完全同值**）**仍躺在磁盘上**。

⇒ ★ **纪律**：
1. ★ **「删文件」「拖进废纸篓」都不等于凭据消失** ——
   废纸篓是**可恢复删除**，明文**仍常驻磁盘**
   ⇒ ⇒ **盘点副本时必须把 `~/.Trash` 算进去**（本项目副本地图的**第四次漏项**）。
2. ★★ **隐藏文件会让清理动作「看不见自己清了什么」** ——
   临时凭据文件常以 `.` 开头 ⇒ **macOS Finder 默认不显示隐藏文件**
   ⇒ ⇒ 老大打开废纸篓**根本看不到那几份明文** ⇒
   ⇒ **清完必须按名字核对**（`find ~/.Trash -name '.*pat*'`），
   **不能只点一下「清倒废纸篓」就当完事**。
3. ⇒ **临时文件一开始就该放进 `mktemp -d` 私有目录（700）+ `trap` 兜底清理** ——
   不用可预测的固定路径（`/tmp/xxx_secret_<NAME>.txt`）⇒ 报错 / Ctrl-C 就留明文。
4. ⇒ ★ **「可恢复删除」的适用范围远不止废纸篓** ——
   `git checkout --`（恢复旧版本）、数据库备份目录、`data/backups/`、时间机器快照
   ⇒ **凡是「以前有过一份」的载体，都可能躺着旧明文**。
5. ⇒ **盘点副本时「当前目录」永远是下界** ⇒ 必须按**内容指纹**全盘 join
   （配合 §16 W：全盘 + 不吞异常 + 目录名不能手写假设）。
6. ⇒ ★★ **明文残留的风险 = 凭据仍有效时的风险** ⇒ ★★ **作废 > 擦除** ——
   **残留的明文不是风险，残留明文对应的凭据「仍然有效」才是风险**；
   ⇒ 一旦在供应商后台**作废**该凭据，所有副本**同时降级为无害的历史残留**
   ⇒ ⇒ **行动优先级 = 先作废，再考虑擦除**（且 APFS + SSD 上覆写本就不可靠 ⇒ 擦了也不彻底）
   ⇒ ⇒ ★ **但「盘点」≠「擦除」**：作废前仍要盘，因为**可能有别的活副本**。
   ⇒ ⇒ ★ **推论：副本地图的「要清多少」≠「要改多少」** ——
   **备份/快照里的旧值本来就是废值，改它们是自找麻烦**（破坏历史真实性 + 多份写风险）。
