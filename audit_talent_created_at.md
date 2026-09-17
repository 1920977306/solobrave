# talents 表 created_at 异常 audit (dev/feat: talents 修复 #5)

执行时间: 2026-09-18 00:40
执行命令: `sqlite3 data/solobrave.db "..."` (见下方 SQL)
结论: **健康, 无需修复**

## 检查项 + 结果

| 检查 | SQL | 结果 |
|---|---|---|
| NULL/0 created_at | `WHERE created_at IS NULL OR created_at = 0` | 0 |
| NULL/0 updated_at | `WHERE updated_at IS NULL OR updated_at = 0` | 0 |
| created_at > updated_at | `WHERE created_at > updated_at` | 0 |
| 未来 created_at | `WHERE created_at > now*1000` | 0 |
| 2025-01 前 (远古) | `WHERE created_at < 2026-01-01*1000` | 0 |
| ms/s 单位错乱 (1e12 以下) | `WHERE created_at > 0 AND created_at < 1e12` | 0 |

## created_at 月份分布 (84 active talents)

| 月份 | 数量 |
|---|---|
| 2025-12 | 1 |
| 2026-02 | 1 |
| 2026-03 | 3 |
| 2026-04 | 3 |
| 2026-05 | 3 |
| 2026-06 | 2 |
| 2026-08 | 44 (大批种子导入) |
| 2026-09 | 27 |

## 范围

- 最早: `2025-12-31 16:00:00`
- 最晚: `2026-09-15 03:45:49`
- 跨度: 约 9 个月, 正常 SaaS 数据积累曲线
- 2026-07 缺失, 不影响 — 间断而非异常 (可能当月没导入)

## 结论

数据健康, 无任何 created_at 异常需要修复. talents 表 created_at 字段
schema (INTEGER ms) 一致, 没混用秒/毫秒, 单元错乱风险 0.

后续若出现新 talents 导入, 建议在 INSERT 路径加防御:
- 非整数 / None / 未来 / 1970 之前 → fallback 到 `int(time.time()*1000)`

(未实现, 留待明早按需.)