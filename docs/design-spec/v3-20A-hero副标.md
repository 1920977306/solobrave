# 20A 规格 — 知识库 hero 副标对照原版（v3）

- 派单时间：2026-10-06 03:40
- 执行：**小路**（首个任务）
- worktree：`/Users/qichen/sb-dev/side-restore-2`，分支 `fix/sb2-side-restore-b2`（基点 8f39d74）
- 验收基准：1618×940 登录态（`sb_auth_token` + `sb_current_user` 两 key，currentUser 非空 + rail 10 按钮）

## 任务（1 项，小）

知识库主区 hero（`.sb2-kb-hero`）对照原型 line 533-538 改样式：

| 维度 | 原版实测 | prod 现值 | 目标 |
|---|---|---|---|
| 标题「知识库」 | 600 24px/1.25（原型 --fs-display） | 13px（hero 统一 13px） | 24px / 600 / lh1.25 |
| 副标 | 400 13px/1.5，color text-3，margin-top 2px（--fs-small） | 无独立副标样式 | 13px / 400 / lh1.5 / var(--sb2-t3) / margin-top 2px |
| 副标文案 | 原型是「287 条 · 新表 kb_entries · 语义检索已切换」 | prod 现「123 条 · …」 | **只借结构不借文案**（演示文案照搬 = 造假信息，字段名铁律）；用 prod 现有真字段口径 |

## 红线

- 作用域限 `.sb2-kb-hero` 内，不改主区 14px 字体、不碰 chips/卡片/侧栏（妍妍并行在改规律库）
- 0 hex，注释全角〔〕，1 commit 报 hash
- dev server 自验：`python3 solobrave-server.py 18211 --data /Users/qichen/sb-dev/side-restore-2/data`（18210 是妍妍的，别 kill）
- node --check 13 块全过

## 走查路径

rail 知识库 → hero 标题 24px 粗体 + 副标 13px 灰（margin-top 2px）→ 右侧按钮组不动 → 切规律库确认无影响。
