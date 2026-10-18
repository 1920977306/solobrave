# daren_bot → SoloBrave 数据回流管道 · 设计稿（待老大审批）

> 2026-10-18 立项。背景：`daren_bot/executors.py` 对 solobrave.db 4 处连接全部 `mode=ro`，零写回；
> SoloBrave 侧无 `/internal/kb/ingest` 端点；`bot_collect_tasks` 30 行 payload 最大 47 字节、
> `bot_product_cache`/`bot_weekly_snapshots` 均 0 行。采集数据跑完即弃，是我们用自己的算力给别人攒库。

## 总原则

- 三条管道**全部走 localhost HTTP**，daren_bot 永远不直接写 solobrave.db（保留现有 ro 隔离）
- 原始数据进**达人库**，提炼后的规律才进**知识库**——两层分开，绝不混灌
- 所有改动走 worktree 分支 + merge 进 prod，pytest 全绿才上线

## 管道 1：达人库 upsert（原始数据）

**触发点**：采集任务成功回调处（`bot_collect_tasks` status → done 的同一事务后）。

**传输**：`POST http://127.0.0.1:8080/internal/talents/upsert`（新建 internal 端点，同 ingest 一套鉴权）。

**鉴权**：`X-Internal-Secret` 头，secret 存 `data/internal_secret.txt`（600 权限，不进 git），
两端读同一文件；端点内硬校验 `self.client_address[0] in ('127.0.0.1','::1')`，非本机直接 403。

**字段映射**（bitable「账号档案」已有字段 → talents 列，右列为已存在列直接复用）：

| 采集字段 | talents 列 | 说明 |
|---|---|---|
| douyin_id | douyin_id | 去重键 |
| 昵称 | name | |
| 粉丝 | followers | |
| 累计GMV | total_gmv | |
| 视频占比 | video_settlement_ratio | fix/talent-sync-missing-cols 已补该列 |
| 评估结论 | ai_rating | 潜力型/成长型/… |
| 报告摘要 | ai_summary | |
| 完整采集 JSON | ocr_raw_fields | 全量兜底，不丢字段 |

**upsert 口径**：
- 以 `douyin_id` 查重：存在 → UPDATE 上表列 + `updated_at`；不存在 → INSERT 新行，
  `source='daren_bot'`、`platform='抖音'`、`created_by='daren_bot'`、`status='active'`
- 只写白名单列（仿 `_TALENT_SYNC_COLUMNS` 模式，服务端常量 `_DAREN_UPSERT_COLUMNS`），
  绝不全行回写（防 status 复活事故重演）
- `embedding` 列绝不触碰

**验收**：采集一个测试达人 → solobrave 达人库出现该达人；重复触发同一 douyin_id 不产生重复行。

## 管道 2：知识库 ingest（提炼规律）

**端点**：`POST /internal/kb/ingest`（SoloBrave 侧新建，欠账补齐）。

**过价值闸**：入库前必须过已上线的价值判定闸（寒暄/任务流水/重复采集直接拒），
`domain='tuangzhang'`，`source='daren_bot'`，scope 归采集租户对应 scope。

**只收三类**（白名单，宁缺毋滥）：
1. 达人画像洞察（如"该达人粉丝价格带集中在 50-100，适合家清类目"）
2. 商品匹配规律（"A 类目商品在母婴达人上转化率显著高"类结论）
3. 达人显式反馈（文字表达的合作意向/拒绝原因）

**不收**：采集流水、问候寒暄、积分变动、系统状态汇报。

**验收**：推一条测试洞察 → 知识库可检索到；推一条寒暄 → 被闸拒并留日志。

## 管道 3：本地原料留存（保底）

- `bot_collect_tasks.payload` 存完整采集 JSON（gzip 与否先不压缩，量小）
- 保留策略：默认 30 天，滚动清理（cron 或写入时顺带删过期）
- 这是管道断链时的重放原料，数据安全底线

## 施工顺序与验收

1. 管道 3（半小时，零风险，先把原料留住）→ pytest 绿
2. 管道 1（达人库 upsert，含端点+鉴权+白名单）→ pytest 绿 + 真机采一个达人验证入库
3. 管道 2（kb ingest，含价值闸复用）→ pytest 绿 + 真机推一拒一收验证
4. 全量回归 377 基线保持全绿

## 明确不做

- daren_bot 直连写 solobrave.db（ro 隔离是特性，不破）
- 把采集原始 JSON 灌知识库（原始数据只进达人库）
- 本轮不动 bitable_sync 写飞书链路（达人那份照常给，是产品承诺）
