# v6-23 轮 messages 侧栏 — 老大 2026-10-06 10:29 prod `?v=r30` 批注①②③

> 编号铁律：批注①②③ = 本轮位置编号。行级修法照抄；0 hex 全 token；font 简写第二值禁裸数字；
> 只丢不造；不碰 knowledge/22 轮成果；不动聊天发送/流式/附件上传链路。

## 诊断结论（协调人已实锤，写进规格防走偏）

### 批注③「点不动」— 真 bug，根因实锤
- 链路：侧栏员工项 `onClick="startNewChat && startNewChat('emp_xxx')"`（SB2_SIDE_RENDERERS.messages renderer，line ~46406）→ `startNewChat()`（line 32613，shim 函数）**丢弃全部参数**，`startNewConversation` 不存在 → 落进"避免递归"空分支 → **完整 no-op**。点击任何员工 = 什么都没发生。
- sb2 聊天选中态读 `localStorage.getItem('sb_current_emp')`（`_sb2GetCurrentEmpId`，line ~47154），真正会写这个值的是 `openChat(id)`（line ~21856）。
- `renderChatMain` 在 chat IIFE 内**未挂 window**（对照：`window.renderSideFor` 已挂，line ~47047 有先例）。

### 批注①「输入框呢」— 协调人无法复现（证据齐）
- 输入框 `.sb2-chat-input-wrap` 是 **#sb2ChatMain 内静态 HTML**（line ~15067），全仓 JS 无任何 display 操作，CSS 无隐藏规则。
- 探针 1618×765 / 1618×560 两档视口实测：topbar 61 + messages flex:1 收缩 + input-wrap 75 始终在卡底（765 时 y672-747）。
- 结论：代码路径上输入框不可能消失。**处置 = 老大硬刷 `?v=r30` 复看；若再现立刻喊我抓 DOM 快照**（本项不盲改，行级修法铁律）。

### 批注②「没有原版好看」— 与原版 diff 实锤（原型 18203 探针取数）
| 项 | 原版（原型 chat 侧栏） | prod 现值 | diff |
|---|---|---|---|
| role 文本 | `>Helen<span> · 商务</span>` — **`·` 前有空格** | `'· '+role` — **无空格** | "Ray· AI拍板助手" vs "Helen · 商务" |
| role 间距 | inline 自然空格，无 margin | `.sb2-side-item-role{ margin-left:6px }`（line ~10300） | 多重间距/排版不齐 |
| role 字级 | `font:var(--fs-micro)` = 11px，色 var(--text-3) | 11px/400 var(--sb2-t3) | 基本一致，保留 |
| 选中态 | active 项：**白底 r10 + 墨色 rgb(24,24,27)**；未选中 rgb(90,90,102) | 模板支持 `it.active`（line ~47028），**但全仓无 `.sb2-side-item.active` CSS**，renderer 也从不设 active | 选中员工无任何高亮 |

## 批注③ 修法（行级，执行：小路）

1. **暴露 refresh 入口**：chat IIFE 内加 `window.renderChatMain = renderChatMain;`（挂 window 先例：line ~47047 `window.renderSideFor`，同款注释风格）。
2. **renderer onClick 改真链路**（line ~46406，messages 分支 map 内）：
   ```
   onClick: "(function(id){try{localStorage.setItem('sb_current_emp',id);}catch(e){}"
          + "if(typeof openChat==='function'){try{openChat(id);}catch(e){}}"
          + "if(typeof window.renderChatMain==='function'){window.renderChatMain();}"
          + "if(typeof window.renderSideFor==='function'){window.renderSideFor('messages');}"
          + "})('"+e.id+"')"
   ```
   - `openChat(id)` 是选中单一真源（写 sb_current_emp + 清群聊态 + legacy DOM 同步，legacy 全 display:none 无副作用）
   - `renderChatMain()` 重读 sb_current_emp → 刷 topbar + 重载历史
   - `renderSideFor('messages')` 重渲侧栏 → active 高亮落到新选中项
3. **renderer 设 active**（同 map 内）：`active: (localStorage.getItem('sb_current_emp') === e.id)` — 读真值不造。
4. 验收：探针点第 2 个员工 → topbar 换人 + 历史切换 + 该项 active 高亮 + 原项高亮移除；点回第 1 个亦复位。

## 批注② 修法（行级，同 commit 或拆 commit，执行：小路）

1. **role 文本补前导空格**（line ~47021）：`'· '+escHtml(it.role)` → `' · '+escHtml(it.role)`。
2. **role CSS 去 margin**（line ~10300）：`.sb2-side-item .sb2-side-item-role` 删 `margin-left:6px`（保留 color/font-size/font-weight/white-space/font-family）。
3. **新增 active 态 CSS**（加在 line ~10300 块旁，0 hex token 化）：
   ```css
   .sb2-side-item.active{ background:var(--sb2-s1); color:var(--sb2-t1); }
   ```
   - 原版语义：active = 白底（--sb2-s1 白）+ 墨色（--sb2-t1 = rgb(24,24,27)）；未选中维持 base 色（即 rgb(90,90,102) 档）。
   - 作用域：通用类，但全仓只有 messages renderer 会设 active → knowledge/patterns/talents 侧栏无 active 输入，零影响（探针回归确认）。
4. dot 保持 7×7 三色不动（19/21 轮成果）。
5. 验收：探针对比 prod vs 原版——role 文本含「 · 」（前导空格）、无 6px margin、点选后白底墨色高亮；knowledge 侧栏（r26.3 四项）/patterns 侧栏（20B）回归无损。

## 批注① 处置（本规格不改码）

- 老大硬刷 `?v=r30` messages 复看输入框。
- 若再现：老大喊一声，我当场抓 `#sb2ChatMain` children computed 快照 + 页面 console 错误定位。
- 预防性观察项：小路交付时探针必须截 `.sb2-chat-input-wrap` rect（1650×900 / 1280×700 双视口）存证。

## 红线

- 不动 22 轮 knowledge 任何文件段（hero/分页/弹窗，6425f81 成果）。
- 不动聊天发送/接收/流式/附件上传功能逻辑（③-2 修复段 line ~47057 不碰）。
- server 零改动（本规格纯前端）。
- node --check 13/13 + 0 hex。

## 交付

- 执行：**小路**（worktree `side-restore-2`；先同步基点：`git fetch` 无 remote 直接用 `git reset --hard 5eb9e7f`——你的 c173175 已被协调人 cherry-pick 为 5eb9e7f 进 prod，reset 不丢内容）。
- commit：`fix(side-restore-r30.x): 23 轮批注③ 侧栏员工选择链路修复 + 批注② role/active 对照原版 — 小路执行`
- 流程：交付报 hash → 协调人探针复测（含双视口输入框存证 + knowledge/patterns 回归）→ ff prod → 老大走查 `?v=r31`。
