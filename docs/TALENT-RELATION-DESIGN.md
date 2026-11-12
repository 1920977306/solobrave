# 达人撞名关系标注 · 数据包 + 字段设计（v1.0，待老大审）

> 第一行先把口径钉死（老大 11-13 原话）：**抖音号是身份键，撞名 ≠ 重复，
> 合并才是制造错误数据。** 本表的存在意义是「标注关系」，不是「找出谁该删」。
> 删谁、合谁，永远是人做的决策（管家边界：不删任何记录）。

## 第一部分 · 撞名清单数据包（老大 11-13 06:14 备齐，库内最新数据重组）

### A 类 · 有身份的撞名（标关系——进 talent_relations）

**【明赫箱包】3 号组**（今天新形成，信息最全的样本）

| 号 | 抖音号 | 粉丝 | 30天结算 | 小拓判定 | 标注 |
|---|---|---|---|---|---|
| 5号（11-12 入库） | dyrsxywdtwyc | 26,578 | 2500-5000 | 值得联系 | same_team（已回填） |
| 6号 | 74292052415 | 4,135 | 1000-2500 | 值得联系 | same_team（11-11 裁） |
| 7号 | 50215117403 | 2,094 | 250-500 | 存疑 | same_team（11-11 裁） |

**【大丸子】2 号组**（待人工标，老大未裁）：Wgg292 · 3.9万粉 · 结算25-50万 ｜ AXR095 · 22.2万粉 · 无结算数据

**【七月姐姐】2 条**（已标 unknown）：17,000 粉 vs 12,000 粉，均无抖音号

### B 类 · 空壳撞实（不进关系表——处理口径是补号或删除候选，删除归人）

- **【一一好胖丫】2 条**：均无抖音号、0 粉、无结算——空壳记录
- **【鸡腿堡】2 条**：鸡腿堡（空壳）vs 劲脆鸡腿堡（4,620 粉 · 鞋靴箱包）——一壳一实

> ⚠️ 两类必须分开：商务看到 0 粉空壳去标「同一团队」是设计要防的错误。
> B 类走「空壳嫌疑清单」（keeper 日报），由人决定补号还是删除。

### 已有裁决的迁移实况（11-11 自由文本 remark → 已结构化回填）

| 组 | 原 remark 载体 | 已回填 relation_label | labeled_by |
|---|---|---|---|
| 明赫 6/7 | 7 号 remark | same_team | executor-20261111 |
| 明赫 5/6、5/7 | 老大 11-13 数据包 | same_team | boss-20261113 |
| 小美穿搭/搭配 | inf_xiaomei remark | different | executor-20261111 |
| 七月姐姐 ×2 | 带括号那条 remark | unknown | executor-20261111 |
| 优奢/新奢 | 新奢 remark（裸文本混在 JSON 采集数据后） | 待人工确认后回填 | — |

最后一个案例特别说明问题：优奢的 remark 字段里是 JSON 采集数据，新奢的 remark 里是裁决文本——**同一字段两种语义混用**，自由文本 remark 留给别的，裁决必须结构化（这是本设计存在的理由之二）。

## 第二部分 · 字段设计（表已建并回填，本节是设计依据）

### talent_relations（关系是 pair，不塞单条列）

```sql
CREATE TABLE talent_relations (
    id TEXT PRIMARY KEY,
    a_talent_id TEXT NOT NULL,
    b_talent_id TEXT NOT NULL,
    relation_label TEXT NOT NULL CHECK (relation_label IN
        ('same_person','same_team','different','unknown')),
      -- same_person 同一人多号 / same_team 同店矩阵（明赫案）
      -- different 撞名不同人（小美案）/ unknown 待补证据（七月案）
    labeled_by TEXT NOT NULL,        -- 标注人；AI 没有写这表的权限（见下）
    labeled_at INTEGER NOT NULL,     -- epoch ms
    evidence TEXT DEFAULT '',        -- 判定证据：抖音号对比/来源，必填
    note TEXT DEFAULT '',
    UNIQUE (a_talent_id, b_talent_id),
    CHECK (a_talent_id < b_talent_id)  -- 无序对：a-b/b-a 双行在约束层就不存在
);
```

五条设计决策，逐条带理由：

1. **关系独立成表，不给 talents 加列**。撞名是两两关系，加列表达不了；
   一个达人可以同时在多组关系里（明赫 5 号同时在 5/6、5/7 两行）。
2. **无序对 + 唯一约束**。`CHECK(a<b)` 让 a-b / b-a 双行在数据库层就不可能发生，
   不靠代码自觉（AGENTS.md §8.6 同名遮蔽的教训：靠约束不靠约定）。
3. **AI 建议与人工标注分通道**。`labeled_by` 只收人（或老大代裁 `boss-*`）；
   AI（管家/小拓）发现疑似只进日报「建议人工过目」，**人确认后才写这表**。
   「人标一次永久生效」= 已标 different 的 pair 从此在 dup_loose 里降噪（已实现，
   见 keeper_ops.talent_healthcheck 的 relation_join）；改判不删行，插新行、
   取最新（审计链完整，谁什么时候改了口径留得住）。
4. **evidence 必填语义**。自由文本 remark 的失败模式是「只有结论没有依据，三个月后
   没人记得为什么裁的」；evidence 强制写判定证据（抖音号对比/粉丝差/来源）。
5. **B 类空壳不走这表**。空壳嫌疑 = 无抖音号 + 0 粉 + 无结算三条件，
   进 keeper 日报「空壳嫌疑清单」，删除决策归人（管家 boundary 已有）。

### 配套改动（本轮已落）

- `tools/keeper_ops.py`：`talent_healthcheck` 的 dup_loose/dup_maybe 输出前
  LEFT JOIN talent_relations，**已标 different 的 pair 不再报**（降噪——标过
  的噪音每天重报就是逼人对同一问题标两次）；已标 same_team/same_person 的
  仍列出但带 `labeled:` 前缀和标签，供商务参考。
- 回填 5 行（11-11 三组迁移 + 老大 11-13 明赫 5 号两组），见第一部分表格。

### 待派单（执行方）

- 详情页/管家日报 UI：撞名组渲染时查 talent_relations 显示关系标签
  （「同店矩阵」「已裁决不同」角标），标注入口（下拉四选一+证据框）给人用。
- 大丸子组 + 优奢/新奢组：等老大或商务标完回填。

### 验收

- 守卫测试：dup_loose 降噪（标 different 后不再出现）/ 空壳清单三条件 /
  无序对约束拒绝 b-a 反向插入。
- 真机：明日管家日报的「重复疑似」段必须并列双口径 + 已标组带标签前缀。
