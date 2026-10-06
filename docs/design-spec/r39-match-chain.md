# 39 轮规格单：P0 人货撮合链最小闭环

> 阶段切换：外观收口 → 功能填充。老大 00:00 拍板「开始填功能」，协调人体检后定 P0 = 撮合链。
> prod 基点：`4ec565c`。协调人设计定稿，双执行器并行。

## 一、现状摸底（协调人已实测，后端全在，前端断链）

| 层 | 现状 |
| --- | --- |
| 后端撮合引擎 | ✅ 完整：规则初筛 `_calculate_match_score_v2` + AI 语义二阶段打分 + 降级信号 |
| 统一入口 | `POST /api/ai-match`，body `{direction:'talent-to-product'\|'product-to-talent', talentId/productId, limit, agentId?}` |
| 响应契约 | `{talent_id, matches:[…], total, ai_scored, degraded, degrade_reason}`；match 项含 `product`/`talent` + `rule_score` + `rule_reasons` + AI 分（**真字段名以实调为准**，字段名铁律） |
| proposals/deals | ✅ CRUD 全在：`POST /api/proposals`、`POST/GET/PUT/DELETE /api/deals[/]` |
| 前端断链点 | sb2 达人库「匹配商品」列恒 0；没有任何入口调 `/api/ai-match`；legacy 选品看板是旧壳不接 sb2 |

## 二、39 轮范围（4 项，最小撮合闭环）

### 项 1：达人库「匹配商品」列接真数据（小路）
- 先探：该列当前渲染数据源是什么；全表 262 行逐行调 ai-match 不可行（AI 二阶段太贵）
- 方案候选（按探查结果选一，写进汇报）：a) 批量规则分接口（若后端有 product-to-influencer 批量版）b) 复用现有列表接口里已带的匹配字段 c) 折中：只对当前页/筛选结果跑规则分
- **只丢不造**：列里放真数，没有匹配就显示 0，不造假数据

### 项 2：达人侧「AI 匹配」弹窗（小路）
- 达人行操作（行 hover 操作区或详情面板加「AI 匹配」按钮）→ 居中弹窗（720px 居中 precedent，批注③ 同款）
- 弹窗内容：POST /api/ai-match talent-to-product → TOP N（limit 10）商品卡：商品名/类目/规则分/AI 分/推荐理由（rule_reasons 逐条）
- `degraded=true` 时展示降级提示条（`degrade_reason`），不静默
- 加载态/空态/错误态三态齐全

### 项 3：商品侧「找达人」弹窗（妍妍）
- 商品行操作「找达人」→ 同款居中弹窗，direction 反转为 product-to-talent
- 达人卡：昵称/类目/等级/粉丝/双分数/推荐理由
- 这是妍妍负责区域（老大 15:56：商品库重设计随达人匹配链一并出），商品库行操作区样式跟你的重设计稿走，但以本规格的功能契约为准

### 项 4：匹配结果 → 合作方案接线（小路）
- 两个弹窗内的 TOP 1（或用户勾选项）加「生成合作方案」按钮 → POST /api/proposals（**创建契约先实调/读后端真代码**，字段名铁律）
- 成功 toast + 在达人详情 deals 区可见（现有 sb2PtnDealsWrap 联动）
- proposals resolve/删除等管理动作不在本轮范围

## 三、铁律清单（派单前必扫，六维互验）

1. **字段名铁律**：所有接口字段以实调响应为准，禁止凭印象（26 轮② `cats.length` 教训）
2. **pageSize 动态**：弹窗列表如分页，用真实几何公式，不写固定数
3. **28 终态**：不碰侧栏/hero 现有成果；弹窗不改主区布局
4. **IIFE 暴露**：新函数给 HTML onclick 用的必须 `window.*` 暴露（r38 踩坑）
5. **接入点 6 步 + 数据初始化链**：行操作按钮渲染函数在哪个接线点被调，全链路覆盖（26 轮② loadProductCategories 漏调用教训）
6. **pytest 三要素**：改完 node --check + dev 实测双向真数据 + 截图/DOM 几何双重证据
7. **工作区隔离**：各自 worktree/分支改，prod 只 ff；禁止 checkout/reset 主工作树

## 四、验收标准

- node --check 13/13
- dev 实测：达人→商品 TOP10 真数据渲染（含推荐理由）、商品→达人同款、degraded 提示可触发可见
- 「生成合作方案」成功后 proposals 落库（DB 实查）+ deals 区联动
- prod 主仓 0 行直接改动（协调人 ff）

## 五、汇报格式

各自报：改动文件/行数、commit hash、自检三件套结果、实调真字段清单（响应 JSON 关键字段名摘录）、与规格偏差项。
