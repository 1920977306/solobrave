# SoloBrave 项目 Agent 工作准则

## 1. 数据隔离铁律(最高优先级)

`data/` 整目录在 `.gitignore` 第 12 行。**绝对不要**:
- `git add -f` 任何 `data/` 文件
- 删/覆盖 `data/agents.json`、`data/solobrave.db` 等运行时数据
- 假设 `data/` 里的状态是 git 可恢复的(它不在版本控制)

测试时造 demo 数据要写到 `data/` 外的临时位置(`.tmp/` / 内存常量),截图后清掉。

**事故复盘**(2026-09-08,bcf5607 之前那次):为了截图 AI 光晕,给 `data/agents.json` 加 demo 员工并用 `git add -f` 强行 push,覆盖了远端生产数据,用户被迫从备份恢复。

## 2. v2 样式收尾:边界与教训

v2 设计系统达人卡视觉(中性头像 / 评级 chip / 待评级虚线态)已 commit `36df27e`,但**还有收尾工作**没做。

**收尾时的硬边界**:
- ✅ **只动 CSS / HTML 视觉层**:design system 组件、preview.html 演示
- ❌ **不要碰评级 JS 逻辑**:`renderTalentList` 评级拼接、`_build_talent_injection` 注入、talent DB schema
- ❌ **不要顺手优化别的**(Helen prompt、OCR 流程、平台 key 归一 等)

**为什么**:评级逻辑和视觉高度耦合(JS 渲染什么 data-rating → CSS 才有什么样式),任何逻辑改动都要重测 5 态视觉,容易引发"删错了 / 加多了"型 bug。**样式收尾独立 commit**,出了问题 git revert 范围小,不影响其他功能。

## 3. 开工前先验前提(2026-09-09 教训)

**血泪**:用户描述"删 9 条图片分析/反幻觉规则",但实际 `data/agents.json` 里 Helen 的 `systemPrompt` 字段是空字符串(0 字符),仓库 grep 全部关键词 0 命中,git log 也没找到历史 commit。**根本没有 9 条可删**。

**规则**:
- 接到"删 N 条规则"型任务,**先 grep 文件 + 查 git log**,确认规则真的存在
- 不要"用户说有就有"——验证完再说
- 验证不符时,主动反馈并**给 2-4 个调整方案**,让用户选,而不是"忽略问题继续干"

## 4. 后端注入功能模板(2026-09-09 已落地,可复用)

`solobrave-server.py` 已有达人数据注入架构,新场景优先复用而非重写:

| 已存在的函数 | 用途 |
|---|---|
| `_build_talent_injection(text, auth)` | 关键词命中时查 talents 表 top 50 followers |
| `_is_analysis_conclusion(text)` | 判断 AI 回复是否是"分析结论" |
| `_maybe_auto_save_analysis(agent_id, reply, ...)` | 自动存知识库 + kb_entries + knowledge_events |

**新增的场景**(per-talent 精确注入 / 历史报告 recall)已在 `200f25f` commit 实现:
- `_extract_talent_from_text` — OCR 文本识别具体达人
- `_build_single_talent_injection(talent_id, auth)` — 单达人精确数据
- `_save_talent_report_to_json(talent_id, content, user_id)` — per-talent JSON 存储
- `_VISION_FALLBACK_NOTICE` — OCR 失败的显式提示

未来同类需求,先看这 4 个函数能不能组合用,别从零写。

## 5. 任务交付节奏

用户工作流:**任务描述 → 出方案 → 用户审 → 改代码 → 跑测 → commit + push**。

- **方案阶段**用 4 段结构(现状 / Gap / 改动 / 风险),不要堆砌代码片段
- **明确"待你确认"的 blocking 问题**,用 `ask_user` 而不是堆问题列表
- **改完跑完,主动给 5 张验收图 + 1 段 commit summary**,不等用户问

## 6. commit message 模板

- 设计系统改动:`refactor(design-system): <要点>`
- 后端功能:`feat(server): <要点>`
- 修复:`fix: <症状>`

每个 commit 前 `git log -1 --stat origin/dev` 确认远端 HEAD,避免本地 commit 错位。
