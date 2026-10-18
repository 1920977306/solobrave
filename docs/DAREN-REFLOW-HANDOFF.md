# daren_bot 数据回流三管道 · 跨对话交接

> 交接给后续在 daren_bot 里新增「采集类」任务类型的对话/会话。
> 事实核对（2026-10-18）：commit `31624c9` 存在于 main，模块 `daren_bot/reflow.py` 在库，
> 设计稿 `docs/daren-reflow-design.md` 已随本文件一并入库。

## 交接话（老大原文，可直接转发）

```
daren_bot 数据回流三管道已上线（feat/daren-reflow, commit 31624c9，已 merge main 并重启生效）。

以后你在 daren_bot 里新写任何「采集类」任务类型时：
1. 完成回调（worker._process 里 mark done 之后）必须走 reflow.after_douyin_collect(task)，
   或至少调 reflow 里对应的 push 函数 —— 别让新管道漏接，采集数据又跑丢了
2. 新增采集类型如有结构化结果，优先落 bot_collect_tasks.payload（管道 3 的原料，
   30 天滚动清理已托管，不用你管）
3. 有提炼结论要进知识库，用 reflow.push_kb_insight(kind, text)，kind 只许三个白名单：
   talent_insight / match_pattern / talent_feedback，服务端会自动过价值闸，
   被拒不报错只记日志，别自己再写一套入库逻辑
4. 不要绕过 reflow 直接连 solobrave.db 写（现有 mode=ro 隔离是铁律），
   达人库回写走 POST /internal/talents/upsert，鉴权由 reflow 模块托管

管道断了不会报错（设计就是尽力而为不阻塞主流程），所以新接任务类型时记得自查一遍。
```

## 关键路径速查

| 内容 | 位置 |
| --- | --- |
| 实现模块 | `daren_bot/reflow.py` |
| 接线点 | `daren_bot/worker.py`（`_process` mark done 后调 `reflow.after_douyin_collect(task)`） |
| 服务端端点 | `solobrave-server.py`：`POST /internal/talents/upsert`、`POST /internal/kb/ingest` |
| 设计稿 | `docs/daren-reflow-design.md` |
| 测试 | `tests/test_daren_reflow.py`（15 例；commit 时全量 392 passed） |
| 上线 commit | `31624c9`（feat/daren-reflow, 2026-10-18） |

## 三管道一句话版

1. **达人库 upsert**：采集成功 → 原始字段按 douyin_id 去重回写达人库（11 列白名单，绝不全行回写）
2. **知识库 ingest**：提炼结论（kind 白名单：画像洞察/匹配规律/达人显式反馈）→ 价值闸 → 强制 pending 人工审
3. **payload 留原料**：结构化结果落 `bot_collect_tasks.payload`，30 天滚动清理（行保留只清内容），供断链重放

铁律：daren_bot 永远不直接写 solobrave.db（mode=ro 隔离），全部走 localhost HTTP + 共享密钥。
