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
- ⚠️⚠️ **`talents.douyin_id` 字段名骗人** —— 它实际存的是**平台达人 id**
  (`Wgg292`/`hlb725`),不是抖音号。拿平台 `user_source_id`(真抖音号)去对拍
  **恒为 0 命中**;能对上的是 `platform_talent_id`。**下「命中 0」结论前先确认键口径**。

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

### 环序(LIFO,最稳的排最前)

| 环 | 通道 | 协议 | 凭据 | 2026-10-22 实测 |
|---|---|---|---|---|
| 0 | MiniMax 套餐 API | Anthropic | `.env` `MINIMAX_CP_API_KEY` | ✅ 1.2s |
| 1 | **Code 订阅(=贾维斯自己)** | Anthropic | `~/.minimax/auth/**/mcode-public/auth.json` | ✅ 1.2s |
| 2 | MiniMax 套餐 API | OpenAI | 同环 0(协议冗余) | ✅ 3.9s |
| 3 | kimi moonshot-v1-32k | OpenAI | OpenClaw key store | ❌ 429 |
| 4/5 | minimax 开放平台 | OpenAI | `.env` `MINIMAX_API_KEY` | ❌ 402 |

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
