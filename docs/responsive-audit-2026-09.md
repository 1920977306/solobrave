# SoloBrave 响应式 Audit 报告 · W1

> Jarvis (mavis) · 2026-09-19 · 老大 review 用
> 仅 audit,不改代码 · 后续改造分支:`feat/responsive-chat` / `feat/responsive-talent` / `feat/responsive-product` / `feat/sidebar-drawer-mobile`

---

## 0. 摘要

| 维度 | 现状 | 备注 |
|------|------|------|
| **总 @media 块** | 13 个,分散在 7 个不同断点 | **断点未统一**,核心问题 |
| **4 断点全覆盖的页** | 0 个 | 没有页覆盖 480/768/1024 全部 |
| **聊天页 sidebar drawer** | ✅ 已实现 (767px 触发) | `feat/responsive-chat` 收尾 |
| **聊天页 topbar 压缩** | ⚠️ 部分实现 (767 隐 role/label) | 移动端字号/高度还要压 |
| **侧栏平板模式 (769-1024)** | ❌ 完全缺 | 抽屉骨架在 768 临界跳变 |
| **达人库列表 移动适配** | ❌ 完全缺 | 列表卡片没动 |
| **商品库列表 移动适配** | ❌ 完全缺 | 中卡没动 |
| **触摸目标 ≥ 44px** | ❌ 多数 button 12-32px | 需全局 padding + 字号放大 |
| **断点用 token 变量** | ❌ 全写死 | 需统一成 `--bp-sm/md/lg` |

**P0 (上线前必补)**: 3 项
**P1 (应补)**: 7 项
**P2 (可优化)**: 4 项

工作量统计:总 S=6 / M=7 / L=1 项

---

## 1. 现状扫描 · 页 × 断点

✅ = 有适配 / ⚠️ = 部分适配 / ❌ = 完全无适配

| 页 | 默认(>1024) | 平板(769-1024) | 手机横(481-768) | 手机竖(≤480) |
|----|---|---|---|---|
| **聊天 Chat** | ✅ 全功能 | ❌ 未测 (临界 768 跳变) | ⚠️ sidebar 抽屉 OK,topbar/消息/输入区仅 padding 缩 | ⚠️ 继承手机横,字号未压 |
| **达人库 Talent** | ✅ 6 KPI + 列表 | ⚠️ 1100→3 KPI,无其它 | ❌ 列表无适配,卡片字号/间距不动 | ❌ 列表无适配 |
| **商品库 Products** | ✅ 4 KPI + 中卡列表 | ⚠️ 900→2 KPI,无其它 | ❌ 列表/筛选无适配 | ❌ 列表无适配 |
| **侧栏 Sidebar** | ✅ 280px 固定 | ❌ 769-1024 区间无规则 | ✅ 767 drawer 滑入 | ⚠️ 继承 drawer,宽度 80%max 300px |
| **登录 Login** | ✅ 双列 1.05fr/1fr | ⚠️ 临界 768 切换 | ⚠️ 单列 padding 缩 | ⚠️ 继承 |
| **AI 员工详情 modal** | ✅ 默认 95% wide | ⚠️ 600→95% (无 768 中间态) | ⚠️ 600→95% | ⚠️ 600→95% |

---

## 2. 现有 @media 块清单(13 个)

按出现顺序:

| L | 断点 | 选择器 | 说明 |
|---|------|--------|------|
| 1184 | 900px | `.products-kpi-grid` | 4→2 列 ✓ |
| 2265 | 1100px | `.talents-metric-grid` | 6→3 列 ✓ |
| 2266 | 600px | `.talents-metric-grid` | 3→2 列 ✓ |
| 2369 | 700px | `.ai-dimension-grid` | (AI 员工详情 6 维度)→2 列 |
| 4443 | 768px | `.app-sidebar*` `.mid-toggle-btn` `.sidebar-resizer` | sidebar drawer 骨架 ✓ |
| 4474 | 480px | `.left-nav` | 左侧 nav 适配 ✓ |
| 4746 | 767px | `.chat-topbar` | 压缩 padding/高度/字号,隐藏 role/label ✓ |
| 5435 | 600px | `.emp-detail-panel` | AI 员工 modal 95% wide ✓ |
| 6067 | 767px | `.app-sidebar` `.chat-area` `.top-banner` `.messages-area` `.input-wrap` | Chat 全套 ✓ |
| 6103 | 480px | `.status-grid` `.quick-grid` | 状态/快捷 grid 适配 ✓ |
| 8245 | ≥768 | `.login-shell` | 登录双列 ✓ |
| 8731 | 767px | `.login-hero` `.login-form-side` | 登录 padding 缩 ✓ |
| 9151 | 767px | `.selection-shell` `.selection-kpi-row` `.selection-board` `.selection-col` | 选品看板单列 ✓ |

**核心问题**: 13 个块用了 **7 种不同断点值**(480/600/700/767/768/900/1100),应该统一成 3 个 token:

```css
--bp-sm: 480px;   /* 手机横屏边界 */
--bp-md: 768px;   /* 平板边界 */
--bp-lg: 1024px;  /* 小桌面边界 */
```

> ⚠️ 767 vs 768 是临界跳变(767 隐,768 显)。两者在中间设备上"闪"。
> 建议用 `768px` 单值,临界设备(767.x)按桌面处理。

---

## 3. 关键问题清单(按优先级)

### P0 · 上线前必补(3 项)

#### P0-1. 断点未统一成 token 变量
- **影响**: 全站
- **现状**: 13 个 @media 块写死 7 种断点值
- **改造**: tokens.css 加 `--bp-sm/md/lg`,把所有写死断点替换成 `var(--bp-*)`
- **工作量**: **S** (~30 行 CSS 改写 + 1 个 token commit)

#### P0-2. 侧栏 769-1024 区间无适配(平板临界跳变)
- **影响**: iPad / Android 平板 / 768-1024 笔记本
- **现状**: 767 → drawer,768 → 固定侧栏。767.x 设备闪屏;769-1024 无专属样式
- **改造**: drawer 触发点扩到 ≤1024 (`max-width: 1024px`),平板默认收起(可点开)
- **工作量**: **M** (~40 行 CSS + 默认收起逻辑)

#### P0-3. 触摸目标 < 44px
- **影响**: 全站可点击元素
- **现状**: 大部分 button / chip 字号 11-12px,padding 4-8px,触摸目标 < 32px
- **改造**: 全局 `@media (max-width: 768px)` 下 button / chip 字号 min 14px,padding 8-12px (iOS HIG ≥44pt)
- **工作量**: **L** (跨多个组件,1 个全局 + 局部微调)

### P1 · 应补(7 项)

#### P1-1. 聊天页 topbar 压缩不彻底(≤768)
- **现状**: 768 时仍显示 model name + 6 个 AI 圆点全名
- **改造**: ≤768 隐藏 model name,圆点缩小到 6px 仅 1 字 (R/R/R/H/M),高度 28px → 24px
- **工作量**: **S** (~15 行 CSS)

#### P1-2. 聊天页消息流 / 输入区 ≤480 字号缩
- **现状**: 767 已缩 padding,但 ≤480 字号 / 行高未压
- **改造**: ≤480 `.message-text` 字号 14→13px,行高 1.5 → 1.45
- **工作量**: **S** (~10 行 CSS)

#### P1-3. 聊天页 AI 团队列表抽屉化(≤768)
- **现状**: sidebar 抽屉已存在,但 AI 团队列表(`.chat-agent-list`)在抽屉内布局未优化
- **改造**: ≤768 抽屉内 AI 团队列表分组折叠(置顶/全部/项目组/归档),状态点保留
- **工作量**: **M** (~50 行 CSS + 一些 HTML 结构)

#### P1-4. 达人库列表移动适配(≤768)
- **现状**: `.talent-card` 默认布局,padding 16px,字号 14px,移动端字号/间距未压
- **改造**: ≤768 单列,padding 12px,字号 14→13px,AI 匹配度 chip 移到第二行
- **工作量**: **M** (~40 行 CSS)

#### P1-5. 达人详情 KPI grid ≤480 → 3+3 两行
- **现状**: 600→2 列,480 字号不变
- **改造**: ≤480 KPI grid 强制 `repeat(3, 1fr)`,字号 12→11px,padding 8px
- **工作量**: **S** (~10 行 CSS,改 600→480 规则)

#### P1-6. 商品库中卡移动适配(≤768)
- **现状**: `.products-mid-card` 默认布局,`grid-template-columns` 在 `<768` 未变
- **改造**: ≤768 单列,padding 12px,letter avatar 60→48px,price 字号缩
- **工作量**: **M** (~30 行 CSS)

#### P1-7. 商品详情 KPI 4 列 ≤480 → 2x2
- **现状**: 900→2 列 (中间态 OK),≤480 字号 / padding 不动
- **改造**: ≤480 KPI card padding 12→10px,sparkline w/h 略缩
- **工作量**: **S** (~10 行 CSS)

### P2 · 可优化(4 项)

#### P2-1. 登录页 ≤480 字号微调
- **现状**: 767 padding 缩,字号没压
- **改造**: ≤480 `.login-hero-title` 24→20px,`.login-hero-sub` 14→13px
- **工作量**: **S**

#### P2-2. AI 员工详情 modal ≤768 中间态
- **现状**: 600→95%,无 768 中间态
- **改造**: ≤768 modal 宽度 90% max 480px,内部 padding 缩
- **工作量**: **S**

#### P2-3. 选品看板 kanban 列宽 ≤480
- **现状**: 767 单列,内容样式未压
- **改造**: ≤480 kanban 列内 card padding 12→10px,sparkline 缩
- **工作量**: **S**

#### P2-4. tokens.css 文档更新
- **现状**: brief 提到的 `--bp-sm/md/lg` 没定义
- **改造**: 加 3 行 token + design-tokens.md 补一段"断点系统"
- **工作量**: **S**

---

## 4. 改造点 + 工作量汇总

| # | 项 | 类型 | 工作量 | 估时 |
|---|----|------|--------|------|
| P0-1 | 断点统一 token | 全站 | **S** | 1h |
| P0-2 | 侧栏平板 drawer | 侧栏 | **M** | 3h |
| P0-3 | 触摸目标 ≥44px | 全站 | **L** | 6h |
| P1-1 | 聊天 topbar ≤768 | 聊天 | **S** | 1h |
| P1-2 | 聊天字号 ≤480 | 聊天 | **S** | 1h |
| P1-3 | 聊天 AI 列表抽屉化 | 聊天 | **M** | 3h |
| P1-4 | 达人库列表 ≤768 | 达人 | **M** | 3h |
| P1-5 | 达人 KPI ≤480 | 达人 | **S** | 1h |
| P1-6 | 商品中卡 ≤768 | 商品 | **M** | 3h |
| P1-7 | 商品详情 KPI ≤480 | 商品 | **S** | 1h |
| P2-1 | 登录字号 ≤480 | 登录 | **S** | 0.5h |
| P2-2 | AI 员工 modal ≤768 | AI | **S** | 1h |
| P2-3 | 选品 kanban ≤480 | 选品 | **S** | 0.5h |
| P2-4 | tokens.css 文档 | 设计系统 | **S** | 0.5h |
| **总** | 14 项 | | **S×6 / M×4 / L×1 / +3 mini-S** | **~24h** |

---

## 5. 建议执行顺序(W2-W3)

| 阶段 | branch | 内容 | 验收 |
|------|--------|------|------|
| W2 Day 1 | `feat/responsive-tokens` | P0-1 + P2-4 一起提(断点 token) | tokens.css + 全站 @media 替换 |
| W2 Day 2 | `feat/sidebar-drawer-mobile` | P0-2 侧栏平板 drawer | 平板临界设备无跳变 |
| W2 Day 3-4 | `feat/responsive-chat` | P1-1 + P1-2 + P1-3 + P0-3 (chat 部分) | 聊天 3 平台截图 OK |
| W2 Day 5 | `feat/responsive-talent` | P1-4 + P1-5 + P0-3 (talent 部分) | 达人 3 平台截图 OK |
| W3 Day 1-2 | `feat/responsive-product` | P1-6 + P1-7 + P0-3 (product 部分) | 商品 3 平台截图 OK |
| W3 Day 3 | `feat/responsive-p2` | P2-1 + P2-2 + P2-3 | 剩余页 OK |
| W3 Day 4 | 集成验收 | 全站无 regression + 触摸目标 ≥44px | 5 张截图 / 页 |

**优化路径**(老大可选):
- P0-3 触摸目标 ≥44px 可拆到各分支里并行做,不必单独 commit
- P0-1 断点 token 必须**第一个做**,所有后续分支依赖

---

## 6. 风险 / 注意点

1. **临界跳变**: 767 / 768 临界设备在未统一断点前会有视觉抖动。建议**第一个 commit 修**
2. **侧栏收起状态持久化**: 用户上次是否收起过侧栏,需要 localStorage 记住。否则每次刷新都展开,体验差
3. **iOS Safari 100vh bug**: `height: calc(100vh - 48px)` 在 Safari 上不正确,需要 `100dvh` fallback。**聊天 modal drawer 要测**
4. **横向滚动**: talent / product 列表的 sparkline 在 ≤480 不会溢出,但 detail KPI 长数字可能溢出(已用 `minmax(0, 1fr)` 防,但需实测)
5. **设计稿 vs 真实**: brief 里说"顶部加汉堡按钮",实际很多页已经有 topbar / mid-toggle,需要确认汉堡按钮位置不重复

---

## 7. 待老大确认

1. **断点临界值**: 480 / 768 / 1024 是否合适?用户群有没有特定机型集中(如 iPhone 16 Pro Max 430 宽度)?
2. **P0-3 触摸目标 ≥44px**: 全局加 padding / 字号放大,接受视觉密度降低吗?
3. **侧栏默认收起 vs 展开**: 769-1024 默认收起(节省内容区)还是展开(更多功能可见)?
4. **是否需要 PWA**: brief 不做,但如果上线后用户有需求,要不要预留 hook?

— 写于 2026-09-19 · 老大 review 后开 W2 改造