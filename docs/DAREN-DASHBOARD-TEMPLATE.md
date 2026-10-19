# 达人看板模板（Daren Dashboard Template）

> 内测版样板完整固化文档。给新达人开通时按第 6 章步骤复制。
> 样板库本身（有真实数据）：https://my.feishu.cn/base/MZEdbVJMeah4x9sYYzncuhnbnnb
> 样板应用：https://my.feishu.cn/app/ZguOb2xCba10pVsRMlXcFmgenGd
> 最后更新：2026-10-19（v3.1：+合作机会表/表单/工作流）

## 1. 资产清单

| 资产 | Token | 说明 |
|---|---|---|
| 空间 | `IPJaslglSpi9GZcmPZfcqTrfn3d` | 「达人看板空间」，所有达人 base/app 放这里 |
| 样板 base | `MZEdbVJMeah4x9sYYzncuhnbnnb` | 含 4 张表 + 真实数据 17+ 条 |
| 样板 App | `ZguOb2xCba10pVsRMlXcFmgenGd` | 主题 cloudBlue，4 页面 |
| 招商表单 | form `vew7JRmRFo` @ 合作机会表 | 匿名可填，分享链接见第 5 章 |
| 通知工作流 | `wkfu5yZBA0EA8GSZ` | 新增合作意向 → 飞书通知 |

## 2. 表结构（4 张）

### 2.1 账号档案 `tblhJISXu8eGABEA`
| 字段 | 类型 | 来源 |
|---|---|---|
| 抖音号 | text（主字段） | bitable_sync 自动写入 |
| 昵称 | text | 自动 |
| 日期 | datetime | 自动（采集日） |
| 粉丝 | number | 自动 |
| 累计GMV | number | 自动 |
| 评估结论 | select | 自动（能带/慎带/别带） |
| 视频占比 | number | 自动 |
| 报告摘要 | text | 自动 |

### 2.2 商品推荐记录 `tbl22zFZvVtqXZnc`
商品名(text,主) / 结论(select：能带/慎带/别带) / 价格(text) / 佣金率(text) / 销量(text) / 匹配理由(text) / 来源(text) / 日期(datetime)
⚠️ 佣金率/销量用 text 是刻意的——百应给的是档位文本（"20%+"），不做数值聚合。

### 2.3 AI 互动记录 `tblbvWG03dC39cbw`
时间(datetime,主) / 类型(text) / 输入(text) / 回答或报告摘要(text)
⚠️ 表名带空格：`AI 互动记录`（见第 7 章坑#3）。

### 2.4 合作机会 `tblolFFeJTNfyInn`
品牌/商家(text,主) / 联系人(text) / 联系方式(text) / 合作类目(text) / 预算/佣金(text) / 合作说明(text) / 状态(select：待评估/有意向/已合作/不合适) / 提交时间(datetime)
人工/表单写入，daren_bot 不碰。

## 3. 应用结构（App `ZguOb2xCba10pVsRMlXcFmgenGd`，theme=cloudBlue）

### 📊 经营总览 `pge8HwdVdEq3oQ72`
| 区块 | 类型 | 名称 | data_config 要点 |
|---|---|---|---|
| chtcn…xc | text | 总览标题 | 建应用时的默认标题（API 改不了，见坑#4） |
| …dATi… | statistics | 粉丝总量 | table=账号档案, series=粉丝 SUM（最新快照逻辑靠日期排序，统计值取最新见 bitable_sync） |
| …G825… | statistics | 累计GMV（万元） | series=累计GMV SUM |
| …gFSx… | statistics | 商品推荐数 | count_all, table=商品推荐记录 |
| …g7rF… | line | 粉丝趋势 | table=账号档案, series=粉丝, group_by=日期 asc |
| …hNWg… | pie | 评估结论分布 | count_all, group_by=评估结论 |
| …r1DM… | bar | AI 互动类型 | count_all, group_by=类型 |

### 🛒 选品推荐 `pge3OM9UJXDsKN4J`
- text 选品横幅（大标题+更新时间口径声明）
- ring 选品结论分布：count_all, group_by=结论
- list/card 推荐商品卡片：fields=[商品名,结论,价格,佣金率,销量,匹配理由,日期], sort_by 日期 desc

### 📋 账号与互动档案 `pgew45EV1GwOcG8z`
- text 档案横幅
- list/detail 账号详情：fields=[昵称,抖音号,日期,粉丝,累计GMV,评估结论,视频占比,报告摘要], sort_by 日期 desc
- list/standard AI互动记录：sort_by 时间 desc（表名必须写 `AI 互动记录`）

### 🤝 合作机会 `pgeOCtdOPee933Td`
- text 合作横幅（内含招商表单链接）
- list/card 合作意向卡片：fields=[品牌/商家,状态,合作类目,预算/佣金,联系人,联系方式,合作说明,提交时间], sort_by 提交时间 desc

图表 data_config 通用格式：
```json
{"base_token":"<base>","data_sources":[{"table_name":"<表名>","count_all":true,"group_by":[{"field_name":"<字段>"}]}]}
```
列表：`{"base_token":"<base>","table_name":"<表名>","fields":[...],"sort_by":[{"field_name":"<字段>","order":"desc"}]}`

## 4. 新达人数据写入约定（daren_bot 侧）

- 账号档案/商品推荐记录/AI互动记录 由 `daren_bot/bitable_sync.py` 按租户配置写入（表名以上述为准）
- 写前用 `+field-list` 校验目标 base 字段一致；不一致走字段创建补丁，禁止改样板结构
- 合作机会表 daren_bot 只读不写

## 5. 招商表单（复制时需重建）

- 创建：`+form-create`（表=合作机会），描述「品牌/商家提交合作意向，达人评估后联系你。带 * 为必填。」
- 问题：自动带出全部字段；把 品牌/商家、联系人、联系方式、合作类目 设 required（`+form-questions-update`，必须带 id+title，全量覆盖语义）
- 分享：`+form-share-update --enabled` + `--allow-anonymous`（招商免登录）
- 当前样板链接：https://my.feishu.cn/share/base/shrcntWrz2k7Vx4REmvGW0yLttf

## 6. 通知工作流（复制时需重建）

`+workflow-create` body 模板见 /tmp/wf_coop.json 模式（已不在机器上则以本章为准）：
- Trigger: AddRecordTrigger, table_name=合作机会, watched_field_name=品牌/商家
- Action: LarkMessageAction → receiver=user(达人 open_id)，title「🤝 收到新的合作意向」，content 引用 trigger 字段（品牌/类目/预算/说明），btn 打开看板链接
- 创建后默认 disabled，必须 `+workflow-enable`
- ⚠️ client_token 每次复制要换新

## 7. API 铁律与坑（全部实测，写代码前重读）

1. **身份**：仪表盘/App/表单/工作流的写操作只能 `--as user`（老大号「全可传媒小翊」）；bot 被 scope 卡死。跑批量前先确认 user token 没过期。
2. **主题只能在建 App 时定**（`+app-create --theme-style`：default|cloudBlue|fresh|softLight|future|technology），之后无 API 可改，只能 UI 手改。
3. **table_name 必须与 `+table-list` 返回逐字符一致**——`AI 互动记录` 带空格，写错报模糊的 "API error: [1]"。
4. **text 区块不能 update**（+app-block-update 返回 API error [1]）；图表/列表可以。
5. **区块类型创建后不可改、不可删**（无 delete 命令）——create 前用 --dry-run 想清楚，建错只能 UI 删。
6. **布局/位置/尺寸 API 不可控**，平台自动排；不顺眼 UI 里拖。
7. **列表 columns/fields**：fields 用字符串数组（字段名）；columns 是对象数组，格式未公开——不传就用默认列。
8. **表单 questions-update 是全量覆盖语义**：只发要改的字段会把其他字段写空，必须带 id+title。
9. **workflow-create 的 client_token 唯一**；workflow 创建后默认 disabled。
10. 给达人分享：base 用「邀请协作者（可编辑）」或只读链接；App 链接达人需是空间成员。

## 8. 给新达人复制的步骤（运营手册）

1. 空间 `达人看板空间` → 复制样板 base（UI：base 右上角「创建副本」，勾选复制记录）→ 改名「XX的达人看板」→ 移到空间根目录
2. 删除副本里上一达人的数据：AI互动记录/账号档案/商品推荐记录全选删除（合作机会表保留示例或清空）
3. 重建招商表单（第 5 章），把新链接贴进 App 合作页横幅（UI 改 text 区块，API 改不了）
4. 重建通知工作流（第 6 章），receiver 换成该达人 open_id（`lark-cli contact +get-user --as user` 本人查自己；查别人用 +search-user）
5. App：UI 里打开样板 App → 复制？（App 复制能力以 UI 实际为准；API 无复制命令。若 UI 不支持复制 App，则按第 3 章结构重建，data_config 照抄换 base_token）
6. daren_bot 侧：租户表配置指向新 base 的 3 张表（bitable_sync 按表名匹配），积分/绑定流程不变
7. 验收清单：① 采集后账号档案出现新行 ② 看板粉丝趋势+1 点 ③ 招商表单提交后 10 秒内收到通知 ④ 合作页卡片出现新记录

## 9. 设计规范（第三方玩家风，别再退回官方素风）

- 每页一主题，页名带 emoji；页首必有大标题横幅（# 标题 + > 口径/日期声明）
- 结论类（绿黄红 select）永远放卡片第一个字段
- 占比用 ring 不用 pie（老页饼图是历史遗留）
- 达人视角只放人话，字段黑话（GPM 等）进 AI 报告不进看板
- 选品/合作结论必须带日期字段并在横幅声明"以最新日期为准"
