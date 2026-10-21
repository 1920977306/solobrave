# SoloBrave 项目 Agent 工作准则

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
