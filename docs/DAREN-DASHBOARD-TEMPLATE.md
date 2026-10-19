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
| 总览标题 | text | 纯文本「🚀 达人经营驾驶舱」+一句口径说明（⚠️ 不要写 # / >，会字面显示；坑#4） |
| 粉丝总量 | statistics | 账号档案 粉丝 SUM，filter 抖音号+最新快照 |
| 累计GMV（元） | statistics | 账号档案 累计GMV SUM，同上 filter |
| 商品推荐数 | statistics | 商品推荐记录 count_all |
| 粉丝趋势 | line | 账号档案 粉丝，group_by 日期 asc，filter 抖音号 |
| 评估结论分布 | ring | count_all group_by 评估结论，filter 抖音号 |
| AI 互动类型 | bar | AI 互动记录 count_all group_by 类型 |

### 🛒 选品推荐 `pgefbYAh1fZLAxXD`
- text 选品横幅（一行标题+一句口径）
- ring 选品结论分布：商品推荐记录 count_all group_by 结论
- list/card 推荐商品卡片：fields 商品名/结论/价格/佣金率/销量/匹配理由/日期，sort 日期 desc

### 📋 账号与互动档案 `pgeQhS8dRjT0damH`
- text 档案横幅
- list/detail 账号详情：fields 昵称/抖音号/日期/粉丝/累计GMV/评估结论/视频占比/报告摘要，sort 日期 desc
- list/standard AI互动记录：sort 时间 desc（table_name 必须 `AI 互动记录`）

### 🤝 合作机会 `pgemkj7Mk66OPhAN`
- text 合作横幅（含招商表单链接）
- list/card 合作意向卡片：fields 品牌/状态/类目/预算佣金/联系人/联系方式/说明/提交时间，sort 提交时间 desc

图表 data_config 通用格式：
```json
{"base_token":"<base>","data_sources":[{"table_name":"<表名>","count_all":true,"group_by":[{"field_name":"<字段>"}],"filter":{"conjunction":"and","conditions":[{"field_name":"抖音号","operator":"is","value":"<达人号>"}]}}]}
```
列表：`{"base_token":"<base>","table_name":"<表名>","fields":[...],"sort_by":[{"field_name":"<字段>","order":"desc"}]}`

## 4. 新达人数据写入约定（daren_bot 侧）

- 账号档案/商品推荐记录/AI互动记录 由 `daren_bot/bitable_sync.py` 按租户配置写入
- **sync_profile 写入前必须把本账号旧行的「最新快照」置否**（当前代码未做，TODO；样板库是手工回填的）
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

## 8. 给新达人复制的步骤（运营手册）

1. 空间 → 复制样板 base（UI 创建副本，勾选复制记录）→ 改名「XX的达人看板」
2. 清空上一达人的数据（3 张自动表全删；合作机会保留示例或清空）
3. 重建招商表单（第 5 章），新链接贴进 App 合作页横幅（**UI 里改**，API 改不了 text）
4. 重建通知工作流（第 6 章），receiver 换该达人 open_id
5. App 按第 3 章结构重建（API 无 App 复制命令；blocks 的 data_config 照抄，base_token 换新的，抖音号筛选值换成新达人号）
6. daren_bot 侧：租户表配置指向新 base；bitable_sync 记得处理「最新快照」置否（见第 4 章 TODO）
7. 验收清单：① 采集后账号档案出现新行且只有该行「最新快照=是」 ② 看板粉丝总量=最新值 ③ 招商表单提交后 10 秒内收到通知 ④ 合作页卡片出现新记录

## 9. 设计规范（第三方玩家风，别再退回官方素风）

- 每页一主题，页名带 emoji；页首一行标题横幅（纯文本，不写 markdown 符号）
- 结论类（绿黄红 select）永远放卡片第一个字段
- 占比用 ring 不用 pie
- 达人视角只放人话，字段黑话（GPM 等）进 AI 报告不进看板
- 选品/合作结论必须带日期字段并在口径上声明"以最新日期为准"
- 账号类图表永远带 `抖音号` 筛选（多达人数据共存一张表时不会串）
