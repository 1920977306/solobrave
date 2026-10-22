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
| 达人概览 | text | `👤 洁洁爱户外 · 🟢 潜力型` / `数据来自我们系统 · 每周一更新`（**2 行** —— 第 3 行会被 App 裁掉，见坑 17）。⚠️ 内容 2026-10-22 已从「口号横幅」改成 **H5 同款身份卡**（名字 + 评级徽章 + 口径句）。⚠️ **块名 API 改不了**（坑 21）→ 2026-10-22 已用「**新建同名块 + UI 删旧块**」把「总览标题」换成「达人概览」（坑 27） |
| 粉丝总量(万) | statistics | 账号档案 **number 字段「粉丝·卡」** SUM（`fmt.wan_value()` 写入 1.3），filter 抖音号+最新快照。⚠️ **指标卡一律取整且不认字段 style**（坑 26）⇒ 数值放独立 number 字段，卡的显示精度另在 UI「自定义配置 → 小数精度 = 保留 1 位小数」设 |
| 累计带货GMV(万) | statistics | 账号档案 **number 字段「累计GMV·卡」** SUM（37.5），同上 filter。⚠️ 同上：公式字段「累计GMV(万)」**留在表里给人选**，但**不喂给指标卡** |
| 推荐商品 | statistics | 商品推荐记录 count_all（**命名对齐 H5**，原为「商品推荐数」） |
| 粉丝数(周度快照) | line | 账号档案 粉丝，group_by 日期 asc，filter 抖音号（**原名「粉丝趋势」已改** —— 它是周度快照不是趋势，见坑 18） |
| 评估结论分布 | ring | count_all group_by 评估结论，filter 抖音号 |
| AI 互动类型 | bar | AI 互动记录 count_all group_by 类型 |
| 本周一句话 | text | `本周一句话` / `涨粉健康、带货稳，优先接高佣标品`（**2 行**）。2026-10-22 新增，补 H5 有而飞书缺的一句话结论。⚠️ 内容是**写死**的 → 达人评级变了要手动改，守卫脚本会查（见 §9.3） |
| 账号档案明细 | list | 账号档案，filter 最新快照=是，columns 昵称/粉丝·卡/累计GMV·卡/视频占比/评估结论。⚠️ **列表列不能引用公式字段**（用公式列会报「数据无效」）⇒ 用 number 副本字段 |

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
    - 主题色（`theme_style`）**可以 UI 改**（编辑模式顶栏「外观」→ 6 套预设主题，含绿系「清新」）
      —— 建 App 时的 `--theme-style` 只是**初始值**，不是锁死。见坑 33。
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

22. **主题色可以在 UI 改（2026-10-23 更正）** ——
    ⚠️ **本条原写「主题色只能建 App 时定，改不了已有 App」，已被实测推翻**。
    `lark-base` reference 原文「`--theme-style` 只在 `+app-create` 时生效」只说明
    **建 App 时不能传自定义 hex**（可选值 `default|cloudBlue|fresh|softLight|future|technology`），
    **不等于「已有 App 改不了」** —— 编辑模式顶栏有「**外观**」面板，可随时切这 6 套预设主题。
    ⇒ **不需要新建 App**，见坑 33 的入口与代码。
    （教训同 §「做不到结论前必须穷举状态」：只查了 API 文档，没找 UI 入口就下全局结论。）

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

25. **⚠️⚠️ 飞书 App 有「编辑模式」，之前「UI 做不到」的结论全部作废（2026-10-22 亲测推翻）** ——
    **URL 加 `&mode=edit` 进入编辑模式**，页面类名变
    `base-app-container-wrapper--global-right-edit-mode`，此时：
    - 每个块出现 **拖拽手柄** `.widget-container-header-drag-handle`（18×16 小图标）
      和 **4 个缩放手柄** `.resize-handle-{nw,ne,sw,se}`（12×12）→ **能拖、能拉高**。
    - 块头出现 **块菜单按钮** `.widget-container-block-menu-config-button`（24×24）。
    - 块菜单内容**按块型分两套**：
      - 图表块：配置 / 复制 / 背景描边 / 移入 / 删除
      - text 块：复制 / 更换背景色 / 背景描边 / **尺寸** / 移入 / 删除（**没有「配置」**）
    - 「配置」打开**右侧 350px 设置抽屉**，三个 Tab：
      **基础配置**（块名 input / 数据源 / 数据范围 / 统计方式 / 同环比）、
      **自定义配置**（背景色 / 文字色 / 字体字号 / **小数精度** / 数字格式 / 宽度 / 高度）、
      **数据说明**。
    - 顶部工具条出现「**完成编辑**」→ **改完必须点它才保存**。
    - 侧栏出现「+ 新建」「+ 添加组件」。
    - **根因复盘**：之前只截了浏览态的图（`base-app-container-readonly`），
      就下了「改名/拖拽/删块 UI 也做不到」的结论 —— **只看一个态就下全局结论**，
      和「cookie 在 ≠ session 有效」同型。**下「平台不支持」结论前必须先穷举可用态。**

26. **指标卡的小数精度是 UI 专属（2026-10-22 实测）** ——
    - 指标卡对数值**一律取整**（显示层行为）：公式 `粉丝(万)`=1.3 → **1**；
      number+precision=1 的 1.3 → **1**；number+precision=2 的 12.5 → **13**（不是 12.50）。
      **只有千分位生效**（12804 → "12,804"）。
      ⇒ 「卡上精度跟随字段 style」是**错判**（`GET fields` 返 null 也只是读法问题，
      `+field-list` 是能读到 style 的）。
    - **文本字段喂给指标卡 = 0**（`rollup: MAX` 对 text 无效），别再试这条路。
    - 真解法：**块菜单 → 配置 → 自定义配置 → 小数精度 = 「保留 1 位小数」**
      （选项：整数 / 保留 1~8 位小数），**API 改不了**（`data_config` 不收 format/precision）。
    - ⇒ **口径纪律**：飞书侧凡是「万单位 + 1 位小数」的数字，
      **字段用 number + `style.precision=1`**（列表/表格按字段格式显示 1.3 / 37.5），
      **指标卡靠 UI 的「小数精度」** —— 两者都要做，缺一个就打架。

27. **块改名：新建同名块 + UI 删旧块（2026-10-22 走通）** ——
    - API `update --name` 恒 `API error:[1]`（坑 21 仍然成立）。
    - 但 `+app-block-create --name "达人概览" --type text --data-config '{"text":"..."}'`
      **一次把名字写对**；再用编辑模式把旧块**删掉**（菜单 → 删除 → 确认）。
    - ⚠️ **新建的块会落在页面末尾**（视口外）→ 渲染时**看不到**，
      但 `GET .../blocks` 里有 → 别误判成「块丢了」。

28. **飞书 App 只渲染视口内的块（2026-10-22 踩到）** ——
    屏幕外的块**不渲染 DOM**（`data-ssr-widget-info` 都没有），
    导致「块找不到 / 拖不动 / 位置读不到」。
    - 拖拽前**把 Playwright 视口加高**（`viewport={'width':1600,'height':2400}`），
      整页进 DOM，拖拽/缩放都能做。
    - 定位块用 `data-ssr-widget-info` 里的 **widget id**（`position.i`），
      **不要用块标题**（text 块在 DOM 里没有标题元素，用标题定位必然失败）。
    - 网格坐标 → 页面坐标映射（本样板 4 列实测）：
      `page_x = 304 + grid_x*0.859`，`page_y = 108 + grid_y`（视口 1600 宽时）。

29. **⚠️ 用 API 改图表块的 `data_config`，会把 UI 设的「显示设置」冲回默认（2026-10-22 实测踩到）** ——
    - 现象：UI 里把指标卡的「小数精度」设成**保留 1 位小数**（卡显示 1.3 / 37.5 ✅），
      之后我用 `+app-block-update` 只改了 `series.field_name`（字段改名），
      **再打开配置面板，「小数精度」已经变回「整数」**，卡上又显示 1 / 38。
    - 根因：`data_config` 只是**数据源**；显示设置（精度/数字格式/字体/背景/宽高）
      存在另一处，**API 改 data_config 会触发重算并重置那处**。
    - ⇒ **纪律**：
      ① **改完图表块的 `data_config`（API），必须回 UI「自定义配置」重设显示设置**；
      ② 或者**先用 UI 改完显示设置，最后再改 data_config**，并把顺序写进流程；
      ③ 改完**必须重新渲染读一次卡面文字**（`粉丝总量(万)|1.3`）才算数 ——
      不要以「API 返回 ok」当验收。
    - 同型：块名（`name`）、布局/位置/尺寸**都只存在于 UI 侧**，API 碰不到（坑 21/25）。
    ⇒ 一句话：**图表块的「数据」和「长相」分属两套通道，改一边要验另一边。**

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
> 平台锁死的那部分（顶栏形状/卡片圆角）**接受**：同一品牌两种终端形态本就该有不同气质。
> **主题色 2026-10-23 已统一**：App 主题切「清新」（白底+青绿点缀，见坑 33），
> 内容层再叠 H5 同款绿（图表 `singleColorSeries-G` + 横幅底 `#e8f8ef` + 边框跟随主题），
> 整屏观感与 H5 同调。

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


---

## 坑 30：图表块的主题色**只有预设色板，没有自定义 hex**（2026-10-22 实测）

老大问「色调为什么还是蓝色，我不是绿色的吗，我说了跟 H5 一样的来」—— 查清了：

- **图表块（line/column/ring/bar）**：块菜单「配置」→ 右侧抽屉 → **「主题色」** 下拉，
  共 **14 个预设色板**（每个 6 色），选择器类名 `color-series-dropdown-menu`，
  每项 `sheet-test-attr="menuItem-<名字>ColorSeries@v2"`：
  `baseAppCloudBlue(默认)` / `rainbow` / `complementary` / `converse` / `primary` /
  `brandBlue` / `brandPurple` / `singleColorSeries-B/W/G/Y/O/R/D`。
  **绿色系 = `singleColorSeries-G-@v2`**（首色 `rgb(52,199,35)` = #34C723，后 5 档深浅绿）。
  ⚠️ **没有 hex 输入框** ⇒ 图表颜色**只能取预设值**。
- **API 侧无颜色字段**：`+app-block-get` 读图表块，`data_config` 里只有
  `base_token / data_sources / table_name`，**没有任何 color/theme/palette 字段**
  ⇒ 颜色**只能 UI 改**（与「小数精度」同属 UI-only 的显示设置）。
- 落地（2026-10-22）：总览页 4 个图表（粉丝数周度快照 / 评估结论分布 / 视频占比趋势 /
  AI 互动类型）+ 选品页 1 个 ring 全部切到 `singleColorSeries-G`，
  验收 = **重开抽屉看色板预览已是 `rgb(52,199,35)`** + 渲染截图全绿。
- ⚠️ **验收不能用 SVG 取色**：飞书图表是 **canvas 渲染**，DOM 里没有 `fill/stroke`。
  取色只有两条路：**截图肉眼验** / **读抽屉里的 `.color-series-item` computed color**。

## 坑 31：text 块能设**精确 hex 底色**；但**描边没有颜色面板**

- text 块（页头横幅）块菜单有「**更换背景色**」「**背景描边**」。
- **「更换背景色」有完整颜色面板**：HEX/R/G/B 输入框（`.ud__color-picker-panel input`，
  第一个是 HEX）、不透明度、背景模糊 ⇒ **底色能填精确色值**（与图表块不同）。
  ⚠️ **HEX 输入框默认不可见**：必须先点面板里的「**更多颜色**」展开完整面板，
  否则 `fill()` 报 "element is not visible"（2026-10-22 踩过）。
- ⚠️ **「背景描边」没有颜色面板**（2026-10-23 实测更正）——
  它是二级菜单「**粗细**」，第三层只有 `跟随主题 / 0px / 0.5px / 1px / 2px / 3px / 4px`
  **粗细档位**，**没有 HEX、没有色板** ⇒ 描边色**只能跟随 App 主题色**，
  填不出 `#07c160` 这种自定义值。（原写「描边颜色也在颜色面板里填 hex」是错的。）
- 落地（2026-10-23）：三个页头横幅（选品横幅 / 档案横幅 / 合作横幅）
  底色 `#e8f8ef`（H5 `--green-bg`）+ 描边 `1px 跟随主题`（主题已切「清新」青绿）。

## 坑 32：**text 块和图表块的 DOM 标识属性不同**（2026-10-22 踩坑）

- 图表/列表/指标卡：`[data-ssr-widget-info*="<widget_id>"]`
- **text 块：`[data-id="<widget_id>"]`**（没有 `data-ssr-widget-info`）
  ⇒ 用错属性时元素明明在页面里，`querySelector` 却**一个都匹配不上**，
  而且**不报错**，只是静默返回 null（本轮白排查半小时）。
- ⚠️ **widget id 不要手抄**：本轮把 `wgtfRsGRurbM2pLU` 手抄成 `wgtfRsGRrubM2pLU`
  （u/r 位置颠倒）⇒ 所有选择器静默失配，误以为「块没渲染」。
  **对着页面真值核**（读 `getAttribute('data-id')` 的字符码），或**按标题文字定位**：
  `.react-grid-item` 里 `innerText.includes(横幅标题)`。
  ⚠️ 同款：**块渲染有延迟**（实测 2.5s~9s 不等），定位前要**轮询等待**而不是赌固定 sleep。

## 坑 33：App 外壳主题色**可以在 UI 改**（6 套预设，含绿系「清新」）

- ⚠️ **本条原写「主题色改不了，除非新建 App」，已被实测推翻**（2026-10-23）。
  原结论只查了 `lark-base` 文档里「`--theme-style` 只在 `+app-create` 生效」，
  就外推成「已有 App 改不了」—— **API 层的限制 ≠ 产品层的限制**。
- **入口**：编辑模式顶栏右上角「**外观**」按钮，类名 `.base-app-style-comp-icon`
  （tooltip 文案就叫「外观」；同排还有 智能体 / 应用权限 / 应用模式 AI 等图标）。
- **面板**「外观设置」三段：
  - 导航布局：`侧边导航` / `顶部导航`
  - **主题（6 套预设）**：`默认` / `云蓝` / `清新` / `柔光` / `未来` / `科技`
  - 背景：`颜色` / `图片` + 下拉（`跟随主题` 等）
- **6 套里只有「清新」是绿系**（白底 + 青绿点缀），其余：云蓝=蓝底、默认=浅灰、
  柔光=米色、未来=深紫、科技=深蓝灰。⚠️ **仍然没有自定义 hex**（只有这 6 套）。
- **切换代码**（2026-10-23 实测，一键生效 + 刷新后持久）：
  ```python
  pg.locator(".base-app-style-comp-icon").first.click()          # 打开外观面板
  box = pg.locator(".theme-box", has_text="清新").first
  box.scroll_into_view_if_needed()
  box.locator(".theme-box-img").first.click()                     # 必须点缩略图
  ```
  ⚠️ **必须点 `.theme-box-img`**：点外层 `.theme-box` 的中心不生效（会点空）。
  ⚠️ **别按坐标点**：面板位置会随右侧 AI 面板开合漂移（本轮按坐标点过一次，
  结果点进了「应用模式 AI」面板，主题没切但多开了个 AI 面板）。
- 落地：App `ZguOb2xCba10pVsRMlXcFmgenGd` 主题 `云蓝` → **`清新`**，
  左侧导航栏从「蓝底白字」变「白底深字 + 绿色 App 图标」。
- 想先看某套配色再点：预览图是静态 PNG，可直连
  `https://lf-package-cn.feishucdn.com/obj/feishu-static/ccm/static_resource/scm_upload/ccm_bitable_baseapptheme_lm_v_<name>_v2.png`
  （name ∈ default/yunlan/qingxin/rouguang/weilai/keji）。

## 坑 34：块菜单的**二级飞层必须用「带轨迹的鼠标移动」才展开**（2026-10-23）

- 块菜单（`更换背景色`/`背景描边`/`尺寸`/`移入`）是 **hover 展开飞层**，
  而**单点瞬移** `page.mouse.move(x, y)` **不触发**，`locator.hover()` 也不触发。
- ✅ **有效写法**（先从旁边靠近，再分步移动过去）：
  ```python
  pg.mouse.move(x - 60, y); pg.wait_for_timeout(150)
  pg.mouse.move(x, y, steps=12); pg.wait_for_timeout(900)
  ```
- 本轮在这上面浪费了 8 个探针脚本（`mouse.click` / `locator.click(force=True)` /
  JS `.click()` / `mouse.move` 全部无效），直到用 `steps=12` 轨迹移动才开面板。
  **症状**：菜单能开、但颜色面板永远 `offsetParent === null`，
  `get_by_text("更多颜色").first` 只会命中 DOM 里**隐藏的旧副本**（还带 `#000000` 默认值）
  ⇒ 报错信息具有误导性（「element is not visible」看着像面板没渲染）。
- ⚠️ **DOM 里同时存在多个同名可见/隐藏副本**：颜色面板一份在块菜单的
  `display:none` 包装层里、一份在图表块配置抽屉里 ⇒ **一律按「可见」筛，别用 `.first`**。

## 坑 35：版式 / 尺寸 / 配色**全部不在公开 Create/Update 协议内**

- `+app-block-update` 的 CLI 明文：「**Widget layout, position, size and display settings
  are not part of the public create/update protocol**」；
  `+app-block-get` 实测 text 块 `data_config` 只有 `{"text": "..."}`，
  图表块只有 `base_token / data_sources / table_name` —— **没有任何颜色/尺寸字段**。
- ⇒ 版式类（块高/块宽/底色/描边/图表色板/小数精度）**一律只能走 UI**，
  **不要在 API 层找解**（也别把「API 没有」写成「平台不支持」）。
- text 块的**高度**在块菜单「尺寸 → 高度 → **适应内容**」里改（治两行标题被裁切）；
  `高度` 另有 `默认高度` / `填充容器`，`宽度` 有 `默认宽度` / `填充容器`。
  落地：三个横幅 `height: 62px → 90px`，两行文字完整显示（2026-10-23）。
