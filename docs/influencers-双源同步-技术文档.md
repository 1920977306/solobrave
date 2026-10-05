# 达人库双源同步 — 技术调研文档

> 派单类型：顺位 1 文档化（**只调研不写代码**）
> 日期：2026-10-05
> 状态：初稿

---

## ⚠ 协调人复核批注（贾维斯 2026-10-05 23:05，commit 前增）

本文档由妍妍交付于 Desktop 仓 commit `51b7d8c`，落仓错误（该仓是老大个人工作区，非派单目标），已搬运至本 worktree。搬运时复核全部行号引用，**核心前提有事实错误，复核结论如下，正文保留原文供对照**：

### 批注 1：文档把「生产」认错了树（事实错误，正文第 2/4.6 章结论作废）

- 文档假设：主工作区 = `/Users/qichen/Desktop/solobrave`（main 分支）= 生产；worktree side-restore 未合并，"生产跑的依然是老的 JSON 体系"。
- **真相**：生产树 = `/Users/qichen/solobrave-prod`（HEAD `5437768`），side-restore 14+ commit 链**已全部 ff 合入 prod**。生产同时跑两套体系：
  - **JSON influencers 遗留体系仍在跑**：`data/influencers/` 实测 **424 个 JSON 文件**（不是 2 条——2 条只是 Desktop 老树的数据量）；`_load_influencers` prod `19118`、`INFLUENCER_DIR` prod `238`、启动自动迁移 `_migrate_influencers_json_to_sqlite()` prod `32212` 每次重启执行。
  - **sqlite talents + 飞书同步是现网活体系**：prod `_handle_get_talents` `18253`、`_handle_sync_feishu_talents` `17333`、路由 `/api/talents/sync-feishu` prod `8269-8270`。
  - **客户端 UI 已全部走 talents**：prod `index.html` 中 `/api/influencers` 调用 = **0 处**，达人库列表/搜索/详情/匹配全部 `/api/talents/*`（`34785` 列表、`35453` 搜索、`30620` 更新等）；飞书同步按钮 `45111` 也在。文档第 6.5 章"两套客户端入口互不知道对方存在"在 prod 不成立。
- 因此 prod 里真实的"双源"形态：**JSON 体系 = 遗留后端孤岛**（端点还在、424 条数据、启动迁移进 sqlite），**客户端已不读它**；sqlite+飞书 = 唯一活源。
- 正文 R7（"worktree → main 合入风险"）**作废**——已全部合入，不存在待合链。

### 批注 2：行号引用复核结果

| 引用组 | 结果 |
|---|---|
| 正文 11.2 节（worktree 引用， talents/sync 体系） | 对 prod 树**精确命中**：`_TALENT_COLUMNS` 4310、`_talent_row_to_dict` 4394、`_dict_to_talent_row` 4669、`_migrate_influencers_json_to_sqlite` 5110、`_feishu_record_to_talent` 5251、`_handle_sync_feishu_talents` 17333、启动迁移调用 32212、SQL 3988-3989/4001-4002/6841 全对；仅 `_handle_sync_feishu_products` 17417 vs prod 17418 差 1（可忽略） |
| 正文 11.1 节（Desktop main 引用， JSON 体系） | 对 prod 树**大幅漂移**：`INFLUENCER_DIR` 89→238、`_load_influencers` 6605→19118、`_handle_get_influencers` 6627→19132、`_handle_post_influencer` 6669→19183。**引用本身对 Desktop 树是对的**（行号属实），但 Desktop main 是落后的分叉分支，不是生产——按 prod 使用需整体平移 |

### 批注 3：仍然有效的部分

- 第 5 章字段映射表（20 字段 JSON vs 75 列 sqlite）：差异在 prod 真实存在，有效。
- 第 7 章 a-f 决策矩阵 + 老大"全部不批"拍板：有效，作为后续拍板材料。
- 第 9 章风险 R1-R6、R8、R9（R7 作废）：有效，均为真坑。
- 第 10 章实施前置 P1-P6：有效；P2 的 `.secret` 比对路径应改为 prod 树 vs worktree。

---
> 工作区对照：主工作区 `/Users/qichen/Desktop/solobrave`（main 分支，HEAD = e38fb21）vs Worktree `/Users/qichen/sb-dev/side-restore`（fix/sb2-side-restore 分支，HEAD = 4879d84）

---

## 目录

1. [调研目的](#1-调研目的)
2. ["双源"是什么 — 真相](#2-双源是什么--真相)
3. [现状：主工作区 influencer JSON 主源](#3-现状主工作区-influencer-json-主源)
4. [现状：Worktree side-restore talents sqlite + 飞书副源](#4-现状worktree-side-restore-talents-sqlite--飞书副源)
5. [字段级映射差异表](#5-字段级映射差异表)
6. [双源差异点（ID / 写入 / 查询 / 并发）](#6-双源差异点id--写入--查询--并发)
7. [改造路径选项 a-f（决策矩阵）](#7-改造路径选项-a-f决策矩阵)
8. [推荐方案 + 理由](#8-推荐方案--理由)
9. [风险点](#9-风险点)
10. [实施前置](#10-实施前置)
11. [附录：trace 行号对照表](#11-附录trace-行号对照表)

---

## 1. 调研目的

老大 22:17 拍板：**顺位 1 是文档化不是改代码**，a-f 修法全部不批。本文不评判、不挑选 a-f 修法，只把当前"双源"的真相、字段差异、写入/查询路径、风险前置列清楚，作为老大后续拍板的决策依据。

**本文不承诺**：
- 不写迁移代码
- 不动 `_TALENT_COLUMNS` / `_feishu_record_to_talent` / `_handle_post_influencer`
- 不选 a-f 中任何一个方案
- 不下"哪个方案更好"的强结论

**本文承诺**：
- 字段名 / 函数名 / 行号 100% 对齐源码（先 grep server + index.html，再 cite 行号）
- 双源差异点列全（任何遗漏老大后续打回的成本都极高）
- 风险点 = 真坑（不写"理论上没问题"那种没营养的话）

---

## 2. "双源"是什么 — 真相

调研发现一个**关键认知纠正**：老大此前讨论的"双源同步"，**不是单一系统的两个数据源，而是两套不同实现之间的差异**：

| 维度 | 主工作区（main / Desktop 树） | Worktree side-restore（fix/sb2-side-restore） |
|------|-------------------------------|-----------------------------------------------|
| 数据存储 | `data/influencers/index.json` + `{id}.json`（JSON 文件） | `data/solobrave.db` 内 `talents` 表（SQLite） |
| 数据来源 | 客户端表单 POST / 手动录入 | 飞书多维表格（bitable）同步 |
| 概念命名 | `influencer` / `inf_*` ID | `talent` / `tal_*` ID |
| API 路径 | `/api/influencers/*` | `/api/talents/*` |
| 同步机制 | 无（写入即落盘 JSON） | 定时/手动触发 `_handle_sync_feishu_talents` → 拉飞书 bitable → 写 SQLite |
| 部署位置 | 主工作区（生产） | Worktree，未合 main |

**因此所谓"双源同步"实际上是**：
- **主源**：主工作区 JSON 体系（influencer 文件，2 条样本：inf_limantou + inf_huahuac）
- **副源**：Worktree sqlite + 飞书同步体系（worktree 新实现，main 还没合进来）

两套体系**字段、ID、API、客户端入口完全不同**，目前没有任何代码路径把它们互相通信。

---

## 3. 现状：主工作区 influencer JSON 主源

### 3.1 存储路径

- 目录定义：`solobrave-server.py:89` — `INFLUENCER_DIR = os.path.join(DATA_DIR, 'influencers')`
- 数据目录：`data/influencers/`
- 文件：
  - `index.json` — 索引（含全部达人的简化记录）
  - `{influencerId}.json` — 单个达人详情（如 `inf_limantou.json`、`inf_huahuac.json`）
- 实际数据：`ls data/influencers/*.json | wc -l = 2`（2026-10-05 实测，仅 2 条）

### 3.2 读写函数

| 函数 | 行号 | 职责 |
|------|------|------|
| `_write_json(filepath, data)` | `solobrave-server.py:279` | 通用 JSON 落盘（原子写） |
| `_read_json(filepath, default)` | （同模块通用） | 读 JSON，缺则返默认 |
| `_load_influencers()` | `solobrave-server.py:6605` | 读 `index.json`，缺则返 `{'influencers': [], 'version': '1.0'}` |
| `_save_influencers(data)` | `solobrave-server.py:6610` | 写 `index.json`，强制 `version='1.0'` |
| `_sync_influencer_file(influencer)` | `solobrave-server.py:6616` | 同步单条到 `{id}.json` |
| `_remove_influencer_file(inf_id)` | `solobrave-server.py:6621` | 删除 `{id}.json` |

### 3.3 路由分发

| 方法 | 路由 | 处理函数 | 行号 |
|------|------|----------|------|
| GET | `/api/influencers` | `_handle_get_influencers` | `solobrave-server.py:1810` → 函数 `6627` |
| GET | `/api/influencers/{id}` | `_handle_get_influencer` | `solobrave-server.py:1816-1826` → 函数 `6655` |
| GET | `/api/influencers/{id}/matches` | `_handle_get_influencer_matches` | `solobrave-server.py:1820-1824` → 函数 `6355` |
| GET | `/api/influencers/search` | `_handle_search_influencers` | `solobrave-server.py:1813` → 函数 `6758` |
| POST | `/api/influencers` | `_handle_post_influencer` | `solobrave-server.py:2114` → 函数 `6669` |
| PUT | `/api/influencers/{id}` | `_handle_put_influencer` | `solobrave-server.py:2238-2241` → 函数 `6710` |
| DELETE | `/api/influencers/{id}` | `_handle_delete_influencer` | `solobrave-server.py:2336-2339` → 函数 `6742` |

### 3.4 字段定义（POST handler 显式白名单 20 字段）

`solobrave-server.py:6681-6703`（`_handle_post_influencer` 内显式组装 influencer 字典）：

```
id, name, avatar, platform, accountId, followerCount,
category, tags, bio, contentStyle,
cooperationPrice, priceUnit, contact, status,
engagementRate, avgViews, lastCooperation, notes,
createdBy, createdAt, updatedAt
```

### 3.5 客户端调用入口（index.html）

| 行号 | 调用 | 用途 |
|------|------|------|
| `index.html:14686-14690` | `apiFetch('/api/influencers')` | AI 工具 `list_influencers` |
| `index.html:14702-14708` | `apiFetch('/api/influencers?q=...')` | AI 工具 `search_influencers` |
| `index.html:14725-14728` | `apiFetch('/api/influencers/{id}')` | AI 工具 `get_influencer_detail` |
| `index.html:14773-14776` | `apiFetch('/api/influencers/{id}/matches?limit=5')` | AI 工具 `get_influencer_matches` |
| `index.html:17442-17443` | `apiFetch('/api/influencers')` | 达人库侧栏加载 |
| `index.html:17839` | `/api/influencers?...` | 搜索筛选 URL 拼接 |
| `index.html:17949-17951` | POST/PUT `/api/influencers[/{id}]` | 表单保存 |
| `index.html:17963-17971` | `apiFetch('/api/influencers/{id}')` | 详情回填 |
| `index.html:18128` | `DELETE /api/influencers/{id}` | 删除达人 |

### 3.6 飞书相关（主工作区）

主工作区的"飞书"**仅用于 OpenClaw channel 消息收发**，与达人库无关：

| 行号 | 路由 | 用途 |
|------|------|------|
| `solobrave-server.py:1693` | GET `/api/openclaw/channels/feishu/status` | channel 状态 |
| `solobrave-server.py:1981` | POST `/api/openclaw/channels/feishu` | channel 配置 |
| `solobrave-server.py:2378-2398` | 同上的 PUT 分发 | — |
| `solobrave-server.py:8129-8167` | `_handle_feishu_status` | 状态返回 |
| `solobrave-server.py:8167-8263` | `_handle_feishu_config` | 配置读写 |

**结论**：主工作区**完全没有** `_feishu_record_to_talent` / `_feishu_list_all_records` / `_feishu_get_tenant_access_token` / `migrate_influencers_json_to_sqlite` 任何与飞书 bitable 同步相关的代码。

---

## 4. 现状：Worktree side-restore talents sqlite + 飞书副源

### 4.1 存储路径

- 表：`talents`（SQLite，位于 `data/solobrave.db`）
- Schema：`worktree solobrave-server.py:3308`（`CREATE TABLE IF NOT EXISTS talents`）
- 列定义：`_TALENT_COLUMNS` `worktree solobrave-server.py:4310` — **75 列**

### 4.2 列定义（75 列）

`worktree solobrave-server.py:4310-4342`（节选关键分组）：

```
基础身份：id, name, avatar, douyin_id, real_name, wechat, phone, email
地理属性：city, level, followers, talent_type, location, agency, tags
描述：bio, contact, contact_name, contact_phone, contact_wechat, contact_email
商务：cooperation_status, follow_up_by, next_follow_up_at, follow_up_note,
     commission_requirement, fulfillment_score, rating_score
业绩：total_gmv, total_products, product_count, total_shops, average_price
     live_ratio, video_ratio, avg_live_gmv, live_gpm, video_gpm
粉丝画像：fan_gender, fan_age, fan_region, fan_crowd, fan_price_range,
         fan_category, fan_activity, fan_device
        （★ fix/mini-test-code-repair-20260925 补 fan_ 前缀 — 见 `line 4319` 注释）
AI 标注：category, content_style, fans_profile, ai_tags, ai_rating, ai_summary,
        ai_analysis
视频数据：total_history_days, live_sessions, live_views, video_plays,
         single_video_settlement, video_completion_rate, video_likes,
         video_comments, video_shares, video_interaction_rate, video_avg_price
（还有后续若干列，详见源码）
```

### 4.3 关键函数

| 函数 | 行号 | 职责 |
|------|------|------|
| `_talent_row_to_dict` | `worktree solobrave-server.py:4394` | sqlite row → dict |
| `_dict_to_talent_row` | `worktree solobrave-server.py:4669` | dict → sqlite row（兜底 `_TALENT_COLUMNS` 所有列） |
| `_migrate_influencers_json_to_sqlite` | `worktree solobrave-server.py:5110` | **关键迁移函数**：把 JSON 主源迁到 sqlite |
| `_talent_insert_or_update_by_id` | `worktree solobrave-server.py:5104` | 插入或按 id 更新 |
| `_feishu_get_tenant_access_token` | `worktree solobrave-server.py:5168` | 飞书鉴权 token |
| `_feishu_record_to_talent` | `worktree solobrave-server.py:5251` | 飞书 record → talent dict（38 字段映射） |
| `_feishu_list_all_records` | `worktree solobrave-server.py:5297` | 分页拉飞书 bitable |
| `_handle_sync_feishu_talents` | `worktree solobrave-server.py:17333` | `POST /api/talents/sync-feishu` handler |
| `_handle_sync_feishu_products` | `worktree solobrave-server.py:17417` | `POST /api/talents/sync-feishu`（产品） |
| 启动时自动迁移 | `worktree solobrave-server.py:32212` | 调 `_migrate_influencers_json_to_sqlite()` |

### 4.4 SQL 操作位置

| 行号 | 操作 |
|------|------|
| `worktree solobrave-server.py:3988-3989` | `UPDATE talents SET {_TALENT_COLUMNS} WHERE id = ?` |
| `worktree solobrave-server.py:4001-4002` | `INSERT INTO talents ({_TALENT_COLUMNS}) VALUES (...)` |
| `worktree solobrave-server.py:5106-5107` | 同 INSERT（`_talent_insert_or_update_by_id`） |
| `worktree solobrave-server.py:6841` | 又一处 INSERT |

### 4.5 客户端调用入口（worktree index.html）

| 行号 | 调用 | 用途 |
|------|------|------|
| `worktree index.html:45122` | `<button onclick="syncFeishuTalentsFromSettings()">🔄 同步达人</button>` | 设置页触发按钮 |
| `worktree index.html:45182` | `function syncFeishuTalentsFromSettings()` | 函数定义 |
| `worktree index.html:45184` | `fetch('/api/talents/sync-feishu', { method: 'POST', ... })` | 调后端 sync |

### 4.6 Worktree 待合并链（14 commit）

```
4879d84 ← B 补修（全库聚合）
5437768 ← A/B/C 一锅出（B 错版）
1b6a946 ← Revert commit 21
9b8d6c0 ← commit 23 顶栏搜索缩图标
bad6928 ← commit 22 批注②补修 达人库 active 白卡
b29c1d7 ← commit 21 批注③ 一页一搜索（被 1b6a946 revert）
7317486 ← commit 20 批注① chat shell bg
8ace23d ← commit 19 批注② top:48px → top:0
f2331bb ← commit 18 16 轮 hero 嵌套
3fffcee ← commit 17 SB2 loader
8999e2b ← commit 16 server totalChunks
f745a1b ← commit 15 _knowledgeData（被 16+17 覆盖）
13110a6 ← commit 14 批注⑥
6de9027 ← commit 13 批注①②③④⑤
f9df87b ← prod HEAD（baseline）
```

**注意**：talents + 飞书同步的完整体系**已经在 worktree 里**，只是**还没 ff merge 到 main**，所以主工作区 / 生产环境跑的依然是老的 JSON 体系。

---

## 5. 字段级映射差异表

主工作区 20 字段 vs Worktree 75 字段，**没有一个字段是完全对齐的**：

| 主工作区字段 | Worktree 等价字段 | 差异点 |
|-------------|------------------|--------|
| `id` (inf_*) | `id` (tal_*) | ID 前缀不同 |
| `name` | `name` | 同名同义 |
| `avatar` | `avatar` | 同名同义 |
| `platform` | 无 | Worktree 用 `talent_type` + 隐含渠道字段 |
| `accountId` | `douyin_id` (示例) | Worktree 按平台拆：douyin_id / wechat 等 |
| `followerCount` | `followers` | 命名风格不同 |
| `category` | `category` | 同名（业务值可能有差异，需 trace 数据） |
| `tags` | `tags` | 同名同义 |
| `bio` | `bio` | 同名同义 |
| `contentStyle` | `content_style` | 命名风格不同（snake_case） |
| `cooperationPrice` | 无单字段 | Worktree 拆 `single_video_settlement` 等 |
| `priceUnit` | 无 | 隐含在数字字段里 |
| `contact` | `contact` + `contact_name/phone/wechat/email` | 主工作区是字符串，Worktree 拆分 4 个 |
| `status` (available) | `cooperation_status` | 枚举值可能不同 |
| `engagementRate` | 无直接字段 | Worktree 用 `video_interaction_rate` |
| `avgViews` | 无直接字段 | Worktree 用 `live_views` / `video_plays` 等 |
| `lastCooperation` | 无直接字段 | Worktree 用 `next_follow_up_at` |
| `notes` | `follow_up_note` + `ai_summary` | 主工作区一锅，Worktree 拆分 |
| `createdBy/At` | 无 | Worktree 无创建人字段 |
| — | `ai_tags/ai_rating/ai_analysis` | **Worktree 独有**：AI 自动标注 |
| — | `fan_*` 8 列 | **Worktree 独有**：粉丝画像 |
| — | `total_gmv/product_count/...` 14 列 | **Worktree 独有**：业绩指标 |
| — | `live_sessions/video_plays/...` 10 列 | **Worktree 独有**：直播/视频数据 |

**字段覆盖率估算**：主工作区 20 字段 ≈ Worktree 30 字段（其中有 5 处命名不同 + 6 处结构拆分），剩下 45 字段是 Worktree 独有（AI 标注 + 粉丝画像 + 业绩指标 + 视频数据）。

---

## 6. 双源差异点（ID / 写入 / 查询 / 并发）

### 6.1 ID 体系不兼容

- 主工作区：`inf_limantou` / `inf_huahuac`（实测 2 条样本）
- Worktree：飞书 record_id 或 `tal_*` 前缀（具体生成逻辑在 `_feishu_record_to_talent` line 5251，需进一步 trace）

**意味着**：任何迁移脚本都得维护一份 `inf_* → tal_*` 的映射表，否则 FK 关联全断。

### 6.2 写入路径完全不同

| 写入触发 | 主工作区 | Worktree |
|---------|---------|----------|
| 用户手动录入 | POST/PUT `/api/influencers` → `_save_influencers` + `_sync_influencer_file` | **无对应入口**（worktree 改用 sync-feishu） |
| 飞书同步 | **无** | `_handle_sync_feishu_talents` → 拉 bitable → `_talent_insert_or_update_by_id` |
| 删除 | DELETE `/api/influencers/{id}` → `_remove_influencer_file` | **无对应入口** |
| AI 工具写入 | （待 trace，但 `list_influencers` 等是只读） | **无** |

**意味着**：双源下用户改主工作区 JSON，worktree 看不到；worktree 飞书同步下来，主工作区 JSON 也没动。

### 6.3 查询路径完全不同

| 查询 | 主工作区 | Worktree |
|------|---------|----------|
| 列表 | GET `/api/influencers` 走 `_load_influencers` | **需 trace**（推测走 sqlite SELECT） |
| 搜索 | GET `/api/influencers?q=` | **需 trace** |
| 详情 | GET `/api/influencers/{id}` | **需 trace** |
| 匹配商品 | GET `/api/influencers/{id}/matches` | — |

**Worktree 侧的 talents 查询路由还没在本文档 trace 完**（顺位 1 文档化派单只要求 sync 流程，不是查询流程，老大后续要 trace 再说）。

### 6.4 并发写入冲突

- 主工作区：`_save_influencers` 走 `_write_json` 原子写（`line 279`），无文件锁，并发写入可能丢更新
- Worktree：SQLite 走默认 journal 模式，单写者安全
- 双源下：两套并发策略不一致，任何一方的 sync 都会冲突

### 6.5 客户端入口分裂

主工作区客户端调 `/api/influencers/*`，worktree 客户端调 `/api/talents/sync-feishu`。**两套客户端入口互不知道对方存在**：
- 主工作区侧栏渲染达人列表 → 调 `/api/influencers` → 拿 JSON 主源数据
- Worktree 设置页点"🔄 同步达人" → 调 `/api/talents/sync-feishu` → 触发飞书同步
- 两个客户端入口**不在同一个文件**（worktree 改了 index.html，主工作区没改）

---

## 7. 改造路径选项 a-f（决策矩阵）

**声明**：本节**不评判** a-f 哪个更好，只把"老大此前讨论过的方向"列成决策矩阵，每个选项的"做什么 / 不做什么 / 谁受影响"全部列清。

### 选项 A：主源 JSON 不动，副源 sqlite 影子同步

- 做什么：保留 `data/influencers/index.json` 主源不动；worktree 飞书同步只写 sqlite；查询时按需合并
- 不做什么：不迁移 JSON → sqlite；不动主工作区 index.html
- 谁受影响：主工作区无感；worktree 飞书同步结果只对 sqlite 可见

### 选项 B：主源 JSON 迁到 sqlite，飞书同步进同一 sqlite

- 做什么：跑 `_migrate_influencers_json_to_sqlite` 把 2 条 JSON 数据迁到 sqlite；飞书同步写同一 sqlite；客户端从 sqlite 读
- 不做什么：不动 JSON 目录结构（保留作 fallback？）
- 谁受影响：所有达人读写入口全部从 JSON 切到 sqlite；老的 `/api/influencers/*` 路径要么改要么加 alias

### 选项 C：主源 JSON 写 sqlite 影子备份

- 做什么：每次 `_save_influencers` 同时写 JSON + 影子 sqlite；查询从 JSON 读；sync 从 sqlite 读
- 不做什么：不动 JSON 主源；不动飞书同步流程
- 谁受影响：写入路径增加 sqlite 影子；读路径不变；存量 JSON 数据先一次性预热到 sqlite

### 选项 D：JSON 主源废弃，全部走 sqlite + 飞书

- 做什么：删 `data/influencers/`；删 `_load_influencers` / `_save_influencers` / `_sync_influencer_file` / `_remove_influencer_file`；删 `_handle_get/post/put/delete/search_influencers`；改 `/api/influencers/*` 全部重定向到 `/api/talents/*`；客户端调入口改
- 不做什么：保留 `_TALENT_COLUMNS` / 飞书同步体系
- 谁受影响：几乎是破坏性改动，索引/侧栏/详情/AI 工具全部要改

### 选项 E：sqlite 主源，JSON 影子同步

- 做什么：与 C 反过来：sqlite 为主源，JSON 写影子备份（兼容老查询路径）
- 不做什么：飞书同步流程不变
- 谁受影响：查询路径要决定从哪读（JSON 还是 sqlite）；若 sqlite 为主，需要把 `_TALENT_COLUMNS` 75 列映射回 20 字段 JSON

### 选项 F：双源独立，UI 层选择

- 做什么：保留两套体系；UI 层加 tab "JSON 数据 / 飞书数据" 让用户选看哪个
- 不做什么：不动两边的写入路径
- 谁受影响：UI 加 tab；后端不动；维护成本高（两套都要修）

### 决策矩阵速览

| 选项 | 破坏性 | 实现成本 | 数据一致性 | 老查询兼容 | 飞书支持 |
|------|--------|----------|-----------|-----------|----------|
| A | 无 | 低 | 弱（双源分裂） | 完全 | 是 |
| B | 中 | 中 | 强（sqlite 唯一源） | 需 alias | 是 |
| C | 低 | 中 | 中（影子备份） | 完全 | 是 |
| D | **极高** | 高 | 强（sqlite 唯一源） | **全断** | 是 |
| E | 中 | 中 | 中（影子备份） | 部分 | 是 |
| F | 无 | 低 | 弱（双源分裂） | 完全 | 是 |

**老大 22:17 已拍板**：a-f **全部不批**，本文不推荐任何一个。

---

## 8. 推荐方案 + 理由

**本节不写推荐**（老大已批 a-f 全部不批，本节留空）。

老大后续拍板后再补。

---

## 9. 风险点

无论最终走哪个方案，下列风险点是工程实测到的真坑：

### R1. 字段名假设风险（已踩坑 — 字段名铁律第 3 条）

**坑**：任何"主工作区 JSON 字段名 vs Worktree sqlite 列名"映射都得**先 trace server 源码 return dict + 接口 curl 实测双验**，不能凭印象命名。

**前车之鉴**：16 轮批注③打回 3 次，根因都是字段名假设没验证。

**应对**：所有映射字段必须 grep 双方源码（主工作区 + worktree）拿到真实 return dict，再写文档。

### R2. ALTER TABLE 与 _TALENT_COLUMNS 同步风险

**坑**：`_TALENT_COLUMNS` (`worktree solobrave-server.py:4310`) 注释明文写：**"fix/talent-full-sync-r2: ALTER TABLE 加了 3 列，必须同步到这里否则 UPDATE 永远漏写"**（`worktree solobrave-server.py:4334` 附近）。

**应对**：任何迁移脚本都得先 dump 当前 `_TALENT_COLUMNS` 跟 sqlite schema 比对，差异列必须先 ALTER TABLE。

### R3. JWT / secret 一致性风险（已踩坑 — backlog-1）

**坑**：主工作区 `.secret` 跟 worktree `.secret` 可能不同，测试进程跑 worktree server 时 JWT secret 必须同源，否则 auth 失败。

**应对**：迁移前先确认双树 `.secret` 一致；测试场景用 conftest 拷同源 secret 到 tmp。

### R4. 并发写入风险

**坑**：`_save_influencers` 走 `_write_json` 原子写（`line 279`），**无文件锁**，并发写入可能丢更新。SQLite 默认 journal 模式可以避免。

**应对**：双源同步必须串行（一方写完锁住再写另一方），否则会出现 JSON 有但 sqlite 没有 / sqlite 有但 JSON 没有的脏数据。

### R5. 飞书鉴权 / rate limit 风险

**坑**：`_feishu_get_tenant_access_token` (`worktree solobrave-server.py:5168`) 走飞书 OAuth，**tenant_access_token 2 小时过期**；`_feishu_list_all_records` (`worktree solobrave-server.py:5297`) 分页拉取，bitable 单次 500 行上限。

**应对**：sync 流程必须实现 token 缓存 + 自动刷新 + 分页循环；切忌"一次拉 5000 条然后崩"。

### R6. 启动时自动迁移的风险

**坑**：`_migrate_influencers_json_to_sqlite` 在 `worktree solobrave-server.py:32212` 启动时**自动调用**，意味着每次重启 server 都会跑一次迁移。

**应对**：迁移函数必须有幂等性（已存在则跳过）；否则重启一次数据翻倍。

### R7. worktree → main 合入的 14 commit 链风险

**坑**：worktree 14 commit 里有 6 commit 是 UI 改动（侧栏 / 顶栏），不是数据库 / sync 改动。**全链合入会带 6 commit 不必要的 UI 改动**到 main。

**应对**：ff merge 前必须**先 cherry-pick** 只跟 talents / sync-feishu 相关的 commit，UI 改动留在 worktree 慢慢合。

### R8. mcp_browser safety 锁 / 视觉验收受限

**坑**：本派单老大要求视觉自检附 computed + 截图，但 mcp_browser 触发 authentication_user_takeover safety 锁，worktree dev server 内网访问受限。

**应对**：视觉验收若必走浏览器，得另起临时方案（puppeteer e2e 脚本或老大手动真机）。**本文档不涉及 UI，不触发此风险**。

### R9. server 改动自提醒重启风险

**坑**：worktree 自验时任何 server 端改动都得重启 server（kill pid + 重起 18210），否则跑测试看到的是旧 server。

**应对**：本文档调研不写代码，不触发此风险；但后续拍板方案落地时**任何 server 端改动 commit message 必带「server 改动 → 自提醒重启」字样**。

---

## 10. 实施前置

任何方案落地前，必须先做下列前置动作（无论 a-f 哪个）：

### P1. 字段映射表固化

- 主工作区 20 字段 + Worktree 75 字段的全量映射表写到 `docs/influencers-字段映射表.md`
- 每个映射字段都得 trace 双源 return dict（不是猜）
- 老大签字后才进开发

### P2. 双源 `.secret` 同源校验

- 比对 `/Users/qichen/Desktop/solobrave/.secret` vs `/Users/qichen/sb-dev/side-restore/.secret`
- 不一致先解决（要么拷一份，要么新生成）

### P3. 跑一次 baseline 测试

- 主工作区跑 `pytest tests/`（拷 prod DB 模式，参考 backlog-1 改造方案）
- worktree side-restore 跑 `pytest tests/`
- 记下两边基线（pass/fail 数量 + 各自报错）

### P4. 飞书 bitable 配置确认

- 飞书 app_id / app_secret 配在哪（推测在 `data/settings.json` 里，但没 trace 到）
- bitable app_token / table_id 在哪定义
- 是否有现网可达的飞书测试环境

### P5. 客户端入口双源排查

- 主工作区 index.html 全部 10 处 `/api/influencers` 调用 vs worktree index.html 全部 `/api/talents/*` 调用
- 列清楚哪几处 UI 在 main 跑、哪几处在 worktree 跑
- UI 改动要不要跟 server 同步合

### P6. 决策矩阵拍板

- 老大拍 a-f 中一个（或拍新方案）
- 写决策记录到 `docs/influencers-双源同步-决策记录.md`
- 不抢跑：拍板前不写代码

---

## 11. 附录：trace 行号对照表

### 11.1 主工作区 `/Users/qichen/Desktop/solobrave`

| 元素 | 行号 |
|------|------|
| `INFLUENCER_DIR` 定义 | `solobrave-server.py:89` |
| `_write_json` 函数 | `solobrave-server.py:279` |
| GET `/api/influencers` 路由分发 | `solobrave-server.py:1810` |
| GET `/api/influencers/search` 路由分发 | `solobrave-server.py:1813` |
| GET `/api/influencers/{id}` 路由分发 | `solobrave-server.py:1816-1826` |
| GET `/api/influencers/{id}/matches` 路由分发 | `solobrave-server.py:1820-1824` |
| POST `/api/influencers` 路由分发 | `solobrave-server.py:2114` |
| POST `/api/influencers/search` 路由分发 | `solobrave-server.py:2117` |
| PUT `/api/influencers/{id}` 路由分发 | `solobrave-server.py:2238-2241` |
| DELETE `/api/influencers/{id}` 路由分发 | `solobrave-server.py:2336-2339` |
| 飞书 channel GET status | `solobrave-server.py:1693` |
| 飞书 channel POST config | `solobrave-server.py:1981` |
| 飞书 channel PUT 分发 | `solobrave-server.py:2378-2398` |
| `_handle_feishu_status` | `solobrave-server.py:8129` |
| `_handle_feishu_config` | `solobrave-server.py:8167` |
| `_handle_get_influencer_matches` | `solobrave-server.py:6355` |
| `_load_influencers` | `solobrave-server.py:6605` |
| `_save_influencers` | `solobrave-server.py:6610` |
| `_sync_influencer_file` | `solobrave-server.py:6616` |
| `_remove_influencer_file` | `solobrave-server.py:6621` |
| `_handle_get_influencers` | `solobrave-server.py:6627` |
| `_handle_get_influencer` | `solobrave-server.py:6655` |
| `_handle_post_influencer` (20 字段白名单) | `solobrave-server.py:6669-6708` |
| `_handle_put_influencer` | `solobrave-server.py:6710` |
| `_handle_delete_influencer` | `solobrave-server.py:6742` |
| `_handle_search_influencers` | `solobrave-server.py:6758` |
| 客户端 AI 工具 `list_influencers` | `index.html:14686-14690` |
| 客户端 AI 工具 `search_influencers` | `index.html:14702-14708` |
| 客户端 AI 工具 `get_influencer_detail` | `index.html:14725-14728` |
| 客户端 AI 工具 `get_influencer_matches` | `index.html:14773-14776` |
| 客户端侧栏加载 | `index.html:17442-17443` |
| 客户端搜索筛选 URL 拼接 | `index.html:17839` |
| 客户端表单保存 | `index.html:17949-17951` |
| 客户端详情回填 | `index.html:17963-17971` |
| 客户端删除达人 | `index.html:18128` |

### 11.2 Worktree `/Users/qichen/sb-dev/side-restore`

| 元素 | 行号 |
|------|------|
| `CREATE TABLE talents` schema | `solobrave-server.py:3308` |
| UPDATE talents SET ... | `solobrave-server.py:3988-3989` |
| INSERT INTO talents | `solobrave-server.py:4001-4002` |
| `_TALENT_COLUMNS` 75 列定义 | `solobrave-server.py:4310-4342` |
| `_talent_row_to_dict` | `solobrave-server.py:4394` |
| `_dict_to_talent_row` | `solobrave-server.py:4669` |
| `_talent_insert_or_update_by_id` | `solobrave-server.py:5104` |
| `_migrate_influencers_json_to_sqlite` | `solobrave-server.py:5110` |
| INSERT INTO talents (同函数) | `solobrave-server.py:5106-5107` |
| `_feishu_get_tenant_access_token` | `solobrave-server.py:5168` |
| `_feishu_record_to_talent` (38 字段映射) | `solobrave-server.py:5251` |
| `_feishu_list_all_records` | `solobrave-server.py:5297` |
| INSERT INTO talents (另一处) | `solobrave-server.py:6841` |
| POST `/api/talents/sync-feishu` 路由分发 | `solobrave-server.py:8269-8270` |
| POST `/api/talents/sync-feishu/products` 路由分发 | `solobrave-server.py:8305` |
| 启动时调用 `_migrate_influencers_json_to_sqlite` | `solobrave-server.py:32212` |
| `_handle_sync_feishu_talents` | `solobrave-server.py:17333` |
| `_handle_sync_feishu_products` | `solobrave-server.py:17417` |
| 客户端设置页触发按钮 | `index.html:45122` |
| 客户端 `syncFeishuTalentsFromSettings` 函数 | `index.html:45182` |
| 客户端 fetch sync 接口 | `index.html:45184` |

### 11.3 待补充 trace（顺位 1 文档化未涵盖）

- Worktree 侧 talents 查询路由（推测是 `/api/talents` GET 等，需后续 trace）
- 飞书 app_id / app_secret 配置实际存储位置（推测 `data/settings.json` 但没 trace）
- Worktree 索引/侧栏对 talents 的渲染逻辑（推测改过但没 trace）

---

**文档版本**：v0.1 初稿
**下次更新触发**：老大拍板 a-f 中一个方案后补"第 8 章 推荐方案 + 理由"
**维护者**：贾维斯（@Mavis）
