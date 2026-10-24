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
