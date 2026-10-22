# 达人看板模板（Daren Dashboard Template）

> 内测版样板完整固化文档。给新达人开通时按第 6 章步骤复制。
> 样板库本身（有真实数据）：https://my.feishu.cn/base/MZEdbVJMeah4x9sYYzncuhnbnnb
> 样板应用：https://my.feishu.cn/app/ZguOb2xCba10pVsRMlXcFmgenGd
> 最后更新：2026-10-19 v4（重建四页：纯文本横幅+最新快照筛选+饼升环；bitable_sync GMV 中值兜底）

## 1. 资产清单

| 资产 | Token | 说明 |
|---|---|---|
| 空间 | `IPJaslglSpi9GZcmPZfcqTrfn3d` | 「达人看板空间」，所有达人 base/app 放这里 |
| 样板 base | `MZEdbVJMeah4x9sYYzncuhnbnnb` | 4 张表 + 真实数据 |
| 样板 App | `ZguOb2xCba10pVsRMlXcFmgenGd` | 主题 cloudBlue，4 页面 |
| 招商表单 | form `vew7JRmRFo` @ 合作机会表 | 匿名可填，链接见第 5 章 |
| 通知工作流 | `wkfu5yZBA0EA8GSZ` | 新增合作意向 → 飞书通知 |

## 2. 表结构（4 张）

### 2.1 账号档案 `tblhJISXu8eGABEA`
| 字段 | 类型 | 来源 |
|---|---|---|
| 抖音号 | text（主字段） | bitable_sync 自动写入 |
| 昵称 | text | 自动 |
| 日期 | datetime | 自动（采集时间） |
| 粉丝 | number | 自动 |
| 累计GMV | number（元） | 自动；精确值缺失时按档位文本中值兜底（`_gmv_from_text`，如 ¥25万-50万→375000） |
| 评估结论 | select | 自动（潜力型/能带/慎带/别带） |
| 视频占比 | number（0-1） | 自动 |
| 报告摘要 | text | 自动 |
| **最新快照** | select（是/否） | bitable_sync 每次写入时应把本账号旧行置否、新行置是；指标卡按此筛选 |

### 2.2 商品推荐记录 `tbl22zFZvVtqXZnc`
商品名(text,主) / 结论(select) / 价格(text) / 佣金率(text) / 销量(text) / 匹配理由(text) / 来源(text) / 日期(datetime)
⚠️ 佣金率/销量用 text 是刻意的——百应给的是档位文本，不做数值聚合。

### 2.3 AI 互动记录 `tblbvWG03dC39cbw`
时间(datetime,主) / 类型(text) / 输入(text) / 回答或报告摘要(text)
⚠️ 表名带空格：`AI 互动记录`（坑#3）。

### 2.4 合作机会 `tblolFFeJTNfyInn`
品牌/商家(text,主) / 联系人 / 联系方式 / 合作类目 / 预算/佣金 / 合作说明(text) / 状态(select：待评估/有意向/已合作/不合适) / 提交时间(datetime)
人工/表单写入，daren_bot 不碰。

## 3. 应用结构（App `ZguOb2xCba10pVsRMlXcFmgenGd`，theme=cloudBlue）

> v4 重建后的页面与区块（旧页已删）。账号类图表统一筛选 `抖音号 = <该达人号>`，指标卡额外筛 `最新快照 = 是`。
> 样板库展示账号：洁洁爱户外（hlb725）。

### 📊 经营总览 `pgePJ3r25SknFVsg`
| 区块 | 类型 | data_config 要点 |
|---|---|---|
| 总览标题 | text | `👤 洁洁爱户外 · 🟢 潜力型` / `数据来自我们系统 · 每周一更新`（**2 行** —— 第 3 行会被 App 裁掉，见坑 17）。⚠️ 内容 2026-10-22 已从「口号横幅」改成 **H5 同款身份卡**（名字 + 评级徽章 + 口径句）。⚠️ **块名改不了**（见坑 21），要叫「达人概览」得老大手动改 |
| 粉丝总量(万) | statistics | 账号档案 **公式字段「粉丝(万)」** SUM（`ROUND([粉丝]/10000,1)` → 1.3），filter 抖音号+最新快照。**单位化是为了数字不被裁，见坑 19** |
| 累计带货GMV(万) | statistics | 账号档案 **公式字段「累计GMV(万)」** SUM（`ROUND([累计GMV]/10000,1)` → 37.5），同上 filter（**命名对齐 H5**，原为「累计GMV（元）」） |
| 推荐商品 | statistics | 商品推荐记录 count_all（**命名对齐 H5**，原为「商品推荐数」） |
| 粉丝数(周度快照) | line | 账号档案 粉丝，group_by 日期 asc，filter 抖音号（**原名「粉丝趋势」已改** —— 它是周度快照不是趋势，见坑 18） |
| 评估结论分布 | ring | count_all group_by 评估结论，filter 抖音号 |
| AI 互动类型 | bar | AI 互动记录 count_all group_by 类型 |
| 本周一句话 | text | `本周一句话` / `涨粉健康、带货稳，优先接高佣标品`（**2 行**）。2026-10-22 新增，补 H5 有而飞书缺的一句话结论。⚠️ 内容是**写死**的 → 达人评级变了要手动改，守卫脚本会查（见 §9.3） |

### 🛒 选品推荐 `pgefbYAh1fZLAxXD`
- text 选品横幅：`🛒 本周选品推荐` / `🟢能带 🟡慎带 🔴别带 · 以最新为准`
  （结论 emoji 与 H5 一致，见 §9）
- ring 选品结论分布：商品推荐记录 count_all group_by 结论
- list/card 推荐商品卡片：fields 商品名/结论/价格/佣金率/销量/匹配理由/日期，sort 日期 desc

### 📋 账号与互动档案 `pgeQhS8dRjT0damH`
- text 档案横幅：`📋 账号档案与 AI 互动记录` / `每次采集和对话都留档，随时回查`
- list/detail 账号详情：fields 昵称/抖音号/日期/粉丝/累计GMV/评估结论/视频占比/报告摘要，sort 日期 desc
- list/standard AI互动记录：sort 时间 desc（table_name 必须 `AI 互动记录`）

### 🤝 合作机会 `pgemkj7Mk66OPhAN`
- text 合作横幅：`🤝 合作机会` / `品牌商家的合作意向会同步到这里`（**2 行**；原第 3 行的 61 字 share URL 已删 —— 既超 20 字被裁、又因第 3 行被裁，且 text 块不支持链接根本点不了，见坑 17）
- list/card 合作意向卡片：fields 品牌/状态/类目/预算佣金/联系人/联系方式/说明/提交时间，sort 提交时间 desc

图表 data_config 通用格式：
```json
{"base_token":"<base>","data_sources":[{"table_name":"<表名>","count_all":true,"group_by":[{"field_name":"<字段>"}],"filter":{"conjunction":"and","conditions":[{"field_name":"抖音号","operator":"is","value":"<达人号>"}]}}]}
```
列表：`{"base_token":"<base>","table_name":"<表名>","fields":[...],"sort_by":[{"field_name":"<字段>","order":"desc"}]}`

## 4. 新达人数据写入约定（daren_bot 侧）

- 账号档案/商品推荐记录/AI互动记录 由 `daren_bot/bitable_sync.py` 按租户配置写入
- **sync_profile 写入时自动维护「最新快照」**：新行置「是」，同抖音号其他「是」行自动置「否」（bitable_sync 已实现，2026-10-19）；指标卡按 `最新快照=是` 筛选即永远只取最新一行
- 写前用 `+field-list` 校验目标 base 字段一致；合作机会表 daren_bot 只读不写

## 5. 招商表单（复制时需重建）

- 创建：`+form-create`（表=合作机会），描述「品牌/商家提交合作意向，达人评估后联系你。带 * 为必填。」
- 问题自动带出全部字段；把 品牌/商家、联系人、联系方式、合作类目 设 required（`+form-questions-update`，**必须带 id+title**，全量覆盖语义）
- 分享：`+form-share-update --enabled` + `--allow-anonymous`
- 当前样板链接：https://my.feishu.cn/share/base/shrcntWrz2k7Vx4REmvGW0yLttf

## 6. 通知工作流（复制时需重建）

模板：`docs/templates/coop-notify-workflow.json`
- Trigger: AddRecordTrigger, table_name=合作机会, watched_field_name=品牌/商家
- Action: LarkMessageAction → receiver=user(达人 open_id)，content 引用 trigger 字段，btn 打开看板
- 创建后默认 disabled，必须 `+workflow-enable`；client_token 每次换新

## 7. API 铁律与坑（全部实测，写代码前重读）

1. **身份**：写操作只能 `--as user`（老大号「全可传媒小翊」）；bot 被 scope 卡死。
2. **主题只能在建 App 时定**（default|cloudBlue|fresh|softLight|future|technology），之后只能 UI 改。
3. **table_name 必须与 `+table-list` 逐字符一致**——`AI 互动记录` 带空格，写错报 "API error: [1]"。
4. **text 区块：API 不能 update，且内容按纯文本字面显示**——写 `#`/`>` markdown 会露出源码。文案必须在 create 时定稿（一行短句最稳，卡片只有 1/4 宽，长句会截断）；后续改文案只能 UI 编辑或重建页面。
5. **区块类型创建后不可改、不可删**——create 前 --dry-run，建错只能 UI 删。
6. **页面名应用内唯一**——重建页面要先把旧页改名（+app-page-update）再建新同名页，建完删旧页。
7. **布局/位置/尺寸 API 不可控**，平台自动排；不顺眼 UI 里拖。
8. **列表 fields 用字符串数组**（字段名）；columns 是对象数组格式未公开，不传用默认列。
9. **表单 questions-update 全量覆盖**：只发改动字段会把其他写空，带 id+title。
10. **workflow client_token 唯一**；创建后默认 disabled。
11. 给达人分享：base 邀请协作者；App 链接需达人是空间成员。
12. **样式是平台锁死的，API 改不了（2026-10-21 实测）**：
    - SSOT 明文：「布局、位置、尺寸、`show_title` 等展示配置**不属于本期公开
      Create/Update 请求字段**」。实测传 `{"show_title":false}` → 服务端
      `API error: [1]`，**改不动**（好消息：失败时原 `data_config` 完好，不会清空数据源）。
      ⚠️ **2026-10-22 更正证据强度**：`API error:[1]` 是**不透明错误码，含义不唯一**
      （见坑 15），所以「`show_title` 传了报错 = 平台不支持」只是**其中一个解释**，
      不是铁证。真正的铁证是 SSOT 明文那句「不属于公开请求字段」。
    - 后果：飞书 `statistics` 卡片会把区块 `name` **渲染两次**（黑色标题 + 灰色副标题），
      固定高度装不下 → **标题重复 + 数字被裁**。这是平台渲染行为，**API 无解**，
      只能去飞书 UI 手动把卡片拖高（另见坑 16 的取证方法）。
    - 主题色（`theme_style`）建 App 时定死，之后只能 UI 改 → 蓝色底改不成 H5 的绿。
13. **text 区块支持 Markdown，但有硬限制**（2026-10-21 实测 SSOT）：
    - 支持：`#`/`##`/`###` 标题、`**粗体**`、`*斜体*`、`~~删除线~~`、有序/无序列表。
    - **不支持**：链接、图片、代码块、表格（写了当纯文本显示）。
    - **每行 ≤ 20 字**：卡片宽度窄，**长句横向裁切不换行**
      （实测「每周一自动更新 · 数据来自巨量百应 · 结论仅供参考，选品以最新周报为准」
      被裁成「说明GI/最新周报为准」）。所以文案要**分行写短句**。
    - text 区块**可以 update** 了（`+app-block-update --data-config '{"text":"..."}'`），
      旧文档「API 不能 update」已过时。
14. **口径文案要与 H5 同源**：H5 是 `daren_cockpit/index.html`，
      飞书 App 是本模板，两边的**指标名 / 口径句 / 结论 emoji** 必须一致，
      否则用户会以为是两个产品。改动任一边都要同步另一边（2026-10-21 老大要求：
      「两个风格得一样，就是设计感」）。
    **数值单位也同源** —— 见坑 20（粉丝量统一「万」）。

15. **`API error:[1]` 是不透明错误码，含义不唯一（2026-10-22 实测）** ——
    同一个码在不同场景给出完全不同的原因，**不能拿它当「某字段不支持」的证据**：
    - 同名幂等 `--name` update：块 A 报「同一 Page 内组件名称必须唯一」，
      块 B 报 `API error:[1]`（同名、同样没变更）
    - 幂等 `--data-config` text update：**成功**（所以它也不是「无变更」码）
    - 某块 `block-get` **稳定 5/5 失败**，同页其它块正常
    - 某块 `block-get` 与 `update` **都稳定失败**，但同页其它块读写都正常
    → 结论：**遇到 `API error:[1]` 先重试 + 换读法（`+app-block-list` 看名字、
      `+app-block-update` 的返回值看配置），别急着下「字段不支持」的结论**。
    已知案例：App `ZguOb2xCba10pVsRMlXcFmgenGd` / 选品推荐页 / 「选品横幅」
    `wgtfRsGRrubM2pLU` —— **该块通过 API 不可读不可写**（2026-10-22）。
    要改它的内容只能去飞书 UI。
16. **statistics 指标卡「标题渲染两次」的取证方法（2026-10-22）** ——
    灰字副标题**不是** series 的 `field_name`，而是服务端拿**区块 name** 兜底的数据 label。
    证据：`+app-block-get-data` 返回 `dimensions: null`、`measures[]` 里**只有
    `field_name` / `rollup` / `alias`（内部 base64 id），没有 name 字段**；
    而卡片灰字显示的恰是区块名（`field_name` 是「粉丝」，灰字是「粉丝总量」）。
    → 后果：**只要用 statistics 就必然重复一次区块名**；而 `series` 规范里
    **没有显示名字段**（只有 `{field_name, rollup}`），所以**重复消不掉**
    （只能把区块名改短让它不那么刺眼）。
    另：CLI 侧**没有 `+app-block-delete`**，且 **type 创建后不可改** →
    **换不了组件类型、删不掉旧块**。
    ⚠️ **但「数字被裁」不是无解** —— 见坑 19：数值长度是主因，
    用**公式字段**把值单位化缩短就能解决（2026-10-22 已修）。
17. **text 区块在 App 里只显示 2 行**（2026-10-22 手机端实测）——
    第 3 行开始被**纵向裁掉**（不是横向裁切，是卡片高度固定、装不下）。
    配合坑 13 的「每行 ≤20 字」，横幅铁律改成：**≤2 行、每行 ≤20 字**。
    - 已修：经营总览「总览标题」3 行 → 2 行（删掉第 3 行「结论仅供参考，选品以最新周报为准」，
      改成「数据由我们系统 · 结论仅供参考」；「选品以最新周报为准」在**选品推荐页**
      横幅第 2 行「… · 以最新为准」已经承担）
    - 已修：合作机会「合作横幅」3 行 → 2 行（原第 3 行是 61 字 share URL，
      **既超 20 字被横向裁、又因第 3 行被纵向裁，而且 text 块不支持链接 → 根本点不了**；
      换成「品牌商家的合作意向会同步到这里」）
18. **图表看起来「平」先查数据再查渲染**（2026-10-22）——
    「粉丝趋势」画成一条直线，**不是渲染 bug**：`账号档案` 表里 hlb725 只有
    **2 个快照点**（10-15、10-19），两次粉丝数都是 12804。
    采集是**周度**的 → 短期内就是 2 个点 → 折线必然是平的。
    已把区块名从「粉丝趋势」改成「**粉丝数(周度快照)**」——
    **名字要诚实**：它不是趋势，是周度快照。想让它有信息量得提高采集频率
    （与老大「一周抓一次」的要求冲突，需老大拍板）。
    顺带发现：`账号档案` 里 324733241 的**最早一条记录「日期」为空**（早期写入的遗留），
    按日期分组的图表会出空 x 轴 —— 新写入的都带日期，属历史遗留，低优先级。
19. **statistics 数字被裁的解法：公式字段单位化（2026-10-22 实测已修）** ——
    现象：手机端「粉丝总量 12 804」「累计带货GMV 375 000」的**数字下半截被裁**，
    而同一张卡里的「推荐商品 1」完整 → **主因是数值长度/字号，不是布局**。
    修法（零代码，不用动表结构以外的任何东西）：
    - 在源表加**公式字段**做单位化值：
      `粉丝(万)` = `ROUND([粉丝]/10000, 1)` → 1.3
      `累计GMV(万)` = `ROUND([累计GMV]/10000, 1)` → 37.5
    - statistics 的 `series[].field_name` 指到公式字段，区块名带单位
      （`粉丝总量(万)` / `累计带货GMV(万)`）→ 字符数 6→3、7→4，不再被裁
    - 同时这也是**业务表达**：达人圈讲「1.3 万粉」「37.5 万 GMV」，
      比「12804」更贴近认知。H5 侧仍显示精确值（网页自由排版，没有裁切问题）——
      两边单位不同但都正确，区块名带「(万)」消除歧义。
    三个子坑（都会把人带沟里）：
    - **公式字段不支持 `style`**：传 `style` 直接报
      `invalid_request: Unrecognized key(s) in object: 'style'` →
      **精度只能靠 expression 里的 `ROUND(x, n)` 控制**。
    - **statistics 不遵守字段的显示格式**：`粉丝` 字段的
      `thousands_separator` 实测就是 `false`，卡片却显示「12 804」（空格千分位）
      → 卡片**自己格式化**，改字段 style 对卡片无效（别在改 style 上浪费时间）。
    - **新建公式字段后聚合有延迟**：第一次 `+app-block-get-data` 返回
      `main_data: null`（像失败），约 10 秒后才正常 → **别把延迟当失败**，
      也不要因此去重建块。
    - 公式字段是**只读**的：`daren_bot` 的 `bitable_sync` 按字段名写入
      （`TABLES` spec 白名单），`_convert_fields_to_spec` 也只处理 spec 里的字段名
      → **新增公式字段不会被同步流程改坏**（已核对代码）。
20. **粉丝量统一用「万」单位（2026-10-22 老大定调「1.3万这样好看点」）** ——
    同一个粉丝数在**四个地方**展示，必须给同一个字符串，否则达人以为是两个数：
    | 位置 | 实现 | 出口 |
    |---|---|---|
    | 飞书 App 指标卡 | 公式字段 `粉丝(万)` = `ROUND([粉丝]/10000, 1)` | 区块名「粉丝总量(万)」 |
    | H5 驾驶舱 | `index.html` 顶层 `fmtFans()` | KPI「粉丝总量」 |
    | 飞书长图看板 | `dashboard_card.py` | `fmt_wan()` |
    | 周报文字 | `weekly.py` | `fmt_wan()` |
    - **唯一事实来源** = `daren_bot/fmt.py` 的 `fmt_wan()`；H5 的 `fmtFans()`
      是它的 JS 孪生，**两边是手写两份**，靠人记着同步早晚会忘
      → `tests/test_fmt_consistency.py` 用 node 实跑 JS 跟 Python 对拍锁住
      （已反向验证：故意改错 JS 阈值 → 测试立刻红并指出是哪个数不一致）。
    - 规则：**< 1 万 → 千分位原值**（5,251）；**≥ 1 万 → X.X 万**（12,804 → 1.3万）；
      整数不带小数（20,000 → 2万）。< 1 万不转「0.5万」—— 没人看得懂。
    - ⚠️ **舍入必须用 `floor(x/10000*10+0.5)/10`（远离零），不能用 Python 内置
      `round()`**（银行家舍入：`round(2.675, 2)` = 2.67，和 JS 对不上）。
    - **不跟着转的**（改了反而丢信息）：图表 Y 轴与数据点标签（用真值才能看出变化）、
      粉丝**环比增量**（「1.3万 → 1.3万」看不出涨没涨）、周报里给 AI 看的 diff 文本、
      「X 个粉丝带出 Y 成交」这种语义是「人数」的句子。
    - GMV 侧本来就是平台**档位文本**（「25万-50万(档位中值 ¥37.5万)」），
      天然是万单位 —— 所以飞书指标卡的 37.5 和 H5 的「档位中值 ¥37.5万」天然对得上。
    - H5 改完**不用重启**（模板按 mtime 热加载），但**要真渲染验证**：
      `node --check` 抽 script 过语法 + Playwright 开手机视口截图看真实渲染。

21. **块的 `name` 改不了，但 `create` 时能定（2026-10-22 实测）** ——
    - `+app-block-update --name ...` → **恒定 `API error:[1]`**（与内容无关）。
      raw API `PATCH /open-apis/base/v3/base_apps/{app}/pages/{page}/blocks/{block}`
      带 `name` 同样报 `[1]` → **是服务端不接受该字段，不是 CLI 的锅**。
    - **但 `+app-block-update --data-config` 正常** → **内容能改、名字不能改**。
      分解探针法（一次改两个字段失败 → 拆成只改 content / 只改 name 各自试）能立刻定位。
    - `+app-block-create --name ...` **是好的**（新块名一次就写对）。
    - ⇒ 想改已有块的显示名，**只能去飞书 UI 改名**。
    - ⚠️ 连带影响：`show_title` / 布局 / 尺寸同理**不是公开字段**，所以
      「标题不显示」「卡片太矮」这类问题 API 一律无解，只能 UI 拖。

22. **主题色只能建 App 时定，改不了已有 App（SSOT 明文）** ——
    `lark-base` reference 原文：「`--theme-style` 只在 `+app-create` 时生效」，
    可选值 `default|cloudBlue|fresh|softLight|future|technology`。
    ⇒ 想让飞书 App 变成 H5 的微信绿，**只能新建一个 App**（代价：
    重建全部区块 + 坏块 `wgtfRsGRrubM2pLU` 读不出内容会丢 + 排序要 UI 重拖）。
    **默认不这么做** —— 平台壳层差异接受，靠 §9 的三层语义对齐（品牌名 / 颜色语义 / 内容结构）。

23. **结论颜色必须与 H5 同源（2026-10-22 老大「两个设计风格没一样」）** ——
    修之前的割裂：同一个「潜力型」H5 绿底 / 飞书**紫**标签；「🟢 能带」飞书标签是**红色**
    （**emoji 与颜色反了**）；「待评估」H5 琥珀 / 飞书**蓝**。
    这些颜色直接显示在飞书 App 的商品列表 / 合作卡片 / 结论分布图上 → 一眼看出两个系统。
    - **唯一事实来源 = `daren_bot/verdict_ui.py`**（映射表见 §9.3）。
    - 飞书侧改色用 `+field-update`（**PUT 全量语义**：先 `+field-get` 读回完整定义，
      原样带上 `name/type/multiple/default_value/options`，只改 `options[].hue`）。
    - **守卫**：`python3 daren_bot/tools/verify_visual_sync.py`（漏改立刻 exit 1，
      已反向验证：故意改错 H5 emoji / 飞书 hue 各报一次红）。

24. **飞书选项名可能带 emoji 前缀，H5 拼 CSS 类名前必须剥掉（2026-10-22 实测踩到）** ——
    - 「商品推荐记录·结论」的选项名是 **`🟢 能带`**（带 emoji），
      「账号档案·评估结论」是裸词 `潜力型`。
    - H5 原来直接 `v-${c.verdict}` 拼类名 → 拼出 `v-🟢 能带` 这个**不存在的类**
      → 结论标签**掉底色**（裸文字），和飞书带色的标签完全不像。
    - 修法：`verdictKey()` 归一化（Python 侧 `verdict_ui.verdict_key()` 是孪生），
      守卫脚本第 4 项专门对拍这个归一化。
    - ⇒ **推论**：飞书字段值直接喂前端拼类名/键名之前，一律先归一化，别假设它长得干净。

## 8. 给新达人复制的步骤（运营手册）

1. 空间 → 复制样板 base（UI 创建副本，勾选复制记录）→ 改名「XX的达人看板」
2. 清空上一达人的数据（3 张自动表全删；合作机会保留示例或清空）
3. 重建招商表单（第 5 章），新链接贴进 App 合作页横幅（**UI 里改**，API 改不了 text）
4. 重建通知工作流（第 6 章），receiver 换该达人 open_id
5. App 按第 3 章结构重建（API 无 App 复制命令；blocks 的 data_config 照抄，base_token 换新的，抖音号筛选值换成新达人号）
6. daren_bot 侧：租户表配置指向新 base；bitable_sync 记得处理「最新快照」置否（见第 4 章 TODO）
7. 验收清单：① 采集后账号档案出现新行且只有该行「最新快照=是」 ② 看板粉丝总量=最新值 ③ 招商表单提交后 10 秒内收到通知 ④ 合作页卡片出现新记录

## 9. 设计规范（第三方玩家风，别再退回官方素风）

> **⏸️ 飞书「网页应用」：老大 2026-10-22 拍板暂停**（不是废弃，是先不做）。
> **这不是「没做完」，是评估后判定现有方案够用** —— 竞品对比结论（`DAREN-WORKBENCH-COMPARISON.md` §49）：
> 「网页应用那种『产品感』，我们在飞书内用应用模式 + 长图卡片逼近，够用且零运维」。
> 暂停原因待老大补充；**恢复成本极低**，下面这段已备好，随时可捡：
> - H5 侧**零代码**已支持 `https://daren.sczlgyl.com/?t=<tenant>&k=<key>` 直达（已验 200）
> - 鉴权、专属 key、IP 白名单、洛链 Auto TLS 全部就绪
> - 恢复只需老大手动三步：开发者后台建企业自建应用 → 添加「网页应用」能力 →
>   桌面端/移动端主页填上面那个 URL → 版本管理发布（可能要管理员审批）
> - ⚠️ `lark-cli application` domain **只能管 slash command，建不了网页应用**（API 层面做不到）
> - ⚠️ 一个网页应用固定 URL = 一个达人（URL 里带租户 key）。要多达人自动识别，
>   得做飞书 OAuth 免登（官方链接格式
>   `https://open.feishu.cn/open-apis/authen/v1/index?redirect_uri=<编码>&app_id=cli_xxx&state=pc`），
>   H5 侧要加 callback + open_id→租户映射（工作量中等，**等老大拍板再做**）
>
> **桌面端当前走飞书 BaseApp**（§3 应用结构），手机端走 H5 —— 暂停网页应用不影响这两个入口。

> **定位（2026-10-21 老大定调）**：H5 驾驶舱（`daren.sczlgyl.com`）是**手机端主入口**，
> 飞书 BaseApp 是**电脑端**。
>
> **2026-10-22 升级**：老大反馈「两个设计风格没有一样，不然会以为是两个系统」。
> 查证后发现**两端展示的是同一个达人（hlb725 洁洁爱户外）的同一套数据**
> （统计卡的 filter 就是 `抖音号 = hlb725`），所以「像两个系统」是**真的**。
> 修的思路不是追求像素一致（飞书的顶栏/卡片/主题色被平台锁死，见 §7 坑 12），
> 而是让三层**语义**对齐 —— 用户一眼能认出「这是同一个东西」：
>
> | 层 | 规则 | 落地 |
> |---|---|---|
> | **① 品牌识别层** | 两边**同名**：H5 顶栏「达人经营驾驶舱」= 飞书 App 顶栏 App 名 | H5 加品牌头（绿点 + 产品名，2026-10-22） |
> | **② 色彩语义层** | **同一句话必须同色** | 唯一事实来源 `daren_bot/verdict_ui.py`；飞书 4 个单选字段 hue 已按它改色；H5 按它取徽章底色 |
> | **③ 内容结构层** | 同一套指标命名、同一套口径句、同一套结论 emoji、同一句「本周一句话」 | 见下方规则清单 |
>
> 平台锁死的那部分（顶栏形状/卡片圆角/主题色）**接受**：同一品牌两种终端形态本就该有不同气质。
> 若老大坚持主色也统一，唯一路径是**新建 App 指定 `--theme-style`**（见 §7 坑 22），代价见该条。

- 每页一主题，页名带 emoji；页首一行标题横幅（纯文本，不写 markdown 符号）
- 结论类（绿黄红 select）永远放卡片第一个字段
- 占比用 ring 不用 pie
- 达人视角只放人话，字段黑话（GPM 等）进 AI 报告不进看板
- 选品/合作结论必须带日期字段并在口径上声明"以最新日期为准"
- 账号类图表永远带 `抖音号` 筛选（多达人数据共存一张表时不会串）
- **结论 emoji 与 H5 统一**：`🟢能带` / `🟡慎带` / `🔴别带`（H5 用的就是这套）
- **对外口径统一**：「数据来自我们系统」「平台档位口径」，**不出现「巨量百应」**
  （内部代码注释可以保留真实数据源，对外文案不行）

### 9.3 色彩语义映射表（唯一事实来源 = `daren_bot/verdict_ui.py`）

> ⚠️ **2026-10-22 修复前的割裂**：同一个词两边不同色 —— H5「潜力型」绿底，飞书是**紫**标签；
> H5 商品结论「🟢 能带」绿底，飞书标签是**红色**（emoji 与颜色反了）；H5「待评估」琥珀，飞书是**蓝**。
> 这些颜色直接显示在飞书 App 的商品列表 / 合作卡片 / 结论分布图上 —— 用户一眼看出两个系统。

| 结论 / 状态 | H5 徽章 | 飞书 `options.hue` | 语义 |
|---|---|---|---|
| 潜力型 | 🟢 绿底 | `Green` | 正向 |
| 成长型 | 🟢 绿底 | `Lime` | 正向 |
| 稳健型 / 成熟型 | 🟡 琥珀底 | `Yellow` | 中性 |
| 风险型 | 🔴 红底 | `Red` | 负向 |
| 🟢 能带 | 绿底 | `Green` | 正向 |
| 🟡 慎带 | 琥珀底 | `Orange` | 谨慎 |
| 🔴 别带 | 红底 | `Red` | 负向 |
| 待评估 | 琥珀底 | `Orange` | 合作状态 |
| 有意向 | 蓝底 | `Wathet` | 合作状态 |
| 已合作 | 绿底 | `Green` | 合作状态 |
| 不合适 | 灰底 | `Gray` | 合作状态 |
| 最新快照 是 / 否 | — | `Green` / `Gray` | 当前 / 历史 |

**改色的正确姿势**：单选字段的 `hue` 用 `+field-update` 改（**PUT 全量语义**，必须先
`+field-get` 读回完整定义，再原样带上 `name/type/multiple/default_value/options` 提交，
只改 `options[].hue`）：

```bash
lark-cli base +field-update --base-token <base> --table-id <tbl> --field-id "结论" \
  --json '{"name":"结论","type":"select","multiple":false,"default_value":null,"options":[
    {"name":"🟢 能带","hue":"Green","lightness":"Lighter"},
    {"name":"🟡 慎带","hue":"Orange","lightness":"Lighter"},
    {"name":"🔴 别带","hue":"Red","lightness":"Lighter"}]}' --yes --as user
```

⚠️ **两张表的选项名不一样**：账号档案·评估结论 = 裸词「潜力型」；商品推荐记录·结论 =
**带 emoji** 的「🟢 能带」。H5 拼 `.v-*` CSS 类名前**必须剥掉 emoji 前缀**
（`verdictKey()`），否则会拼出不存在的类 → 结论标签掉底色（2026-10-22 实测发现并修掉）。

**守卫脚本**（改任何一边之前先跑，漏改立刻 exit 1）：

```bash
python3 daren_bot/tools/verify_visual_sync.py            # 全查（含飞书）
python3 daren_bot/tools/verify_visual_sync.py --offline  # 只查本地 H5
```

它校验 4 件事：飞书 4 个单选字段的实际 hue、H5 的 `VERDICT_EMOJI` JS 映射（跨语言对拍）、
H5 的 `.v-*` 色类齐全、飞书「本周一句话」块的文本 == `verdict_ui.verdict_line(最新评估结论)`。

