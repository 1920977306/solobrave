# SoloBrave 移动端 / 响应式适配 Brief

> 给 Mini 的执行 brief · 老大 2026-09-19 定方向 ·
> worktree 隔离 + 每分支 1 commit + push 等 review

## 0. 为什么现在做

- SoloBrave 团长多为移动端场景(抖音生态团长在外/路上操作),桌面版 OK 但移动版体验差距大
- 上线前必补 — 用户首次接触产品的入口很可能是移动端
- 别一上来全改 — 分阶段,先 audit + 核心 3 页 + 侧栏抽屉

## 1. 范围(scope 严格)

### 1.1 第一阶段(本 brief 范围)

| 项 | 说明 |
|----|------|
| **响应式 audit** | 跨全站扫一遍响应式现状,产出 audit 报告 |
| **核心 3 页适配** | 聊天 / 达人库 / 商品库 |
| **侧栏抽屉适配** | 移动端 sidebar 抽屉式切换 |
| 桌面版、平板、已 polish 过的 V2.1 样式 | **不动** |
| 数据 / 后端 / 业务逻辑 | **不动** |

### 1.2 第二阶段(本 brief 不做)

- 其他页面(知识库 / 任务 / 设置 / 规律库 / 商品详情等)适配
- 表格横向滚动 / 移动端特殊交互手势
- PWA / 离线缓存 / 推送通知

## 2. 设备 / 断点矩阵

| 设备 | 宽度 | 策略 |
|------|------|------|
| 手机竖屏 | ≤ 480px | 单列,抽屉式 sidebar,顶部状态条压缩 |
| 手机横屏 / 小平板 | 481-768px | 单列 + 浮动元素,sidebar 抽屉 |
| 平板 / 小桌面 | 769-1024px | 2 列布局,sidebar 默认收起(可点开) |
| 桌面 | > 1024px | 维持当前桌面版,**不动** |

**断点 CSS 变量** (在 `core/tokens.css` 已有,沿用):

```css
/* 写 @media query 时统一用这三个断点 */
--bp-sm: 480px;   /* 手机横屏边界 */
--bp-md: 768px;   /* 平板边界 */
--bp-lg: 1024px;  /* 小桌面边界 */
```

如果 `tokens.css` 还没定义,先提 PR 加进去(3 行)。

## 3. 响应式 audit 框架

### 3.1 输出文档

新增 `docs/responsive-audit-2026-09.md`,包含:

1. **现状扫描表** — 全站页面 × 断点矩阵(8 列内)
2. **每页关键问题清单** — 文字溢出 / 元素重叠 / 触摸目标 < 44px / 横向滚动
3. **优先级分级** — P0(不可用)/ P1(体验差)/ P2(可优化)
4. **修复建议** — 每个 P0/P1 问题给出 1-3 行修复方向(不写代码)

### 3.2 audit 范围

- 全站 8+ 主页面:聊天 / 达人库 / 达人详情 / 商品库 / 商品详情 / 知识库 / 任务 / 设置 / 规律库
- 关键组件:侧栏 / 顶部条 / 卡片 / 列表 / 弹窗 / 表单输入

### 3.3 audit 不准做的

- 实际修改 CSS / HTML(只看不改)
- 改 design system tokens
- 跨 worktree 大改

## 4. 核心 3 页适配要求

### 4.1 聊天页(优先级 P0)

**目标**: 移动端能用,核心对话不破

- 顶部状态条(`.chat-topbar-*`)— 移动端隐藏 model name,只保留 6 个 AI 圆点
- 左侧栏(`.chat-sidebar`)— 默认隐藏,顶部加汉堡按钮 → 抽屉式打开
- 消息流(`.chat-messages`)— 全宽,padding 缩到 12px
- 输入区 — 高度自适应,工具栏 4 个 icon 折叠为"+"菜单
- AI 团队列表(`.chat-agent-list`)— 抽屉内显示

### 4.2 达人库(优先级 P1)

**目标**: 移动端可浏览 + 单达人详情可看

- 列表卡片(`.talent-card`)— 单列,padding 缩,AI 匹配度 chip 移到第二行
- KPI 区(`.talent-metric-*`)— 6 KPI 卡 → 3+3 两行 grid(≤480)或 3 列 grid(481-768)
- 筛选栏 — 顶部"筛选"按钮 → 抽屉式
- 详情面板 — 移动端用全屏 sheet(类似 iOS modal),Tab 切换

### 4.3 商品库(优先级 P1)

**目标**: 移动端可浏览商品 + 月销可读

- 中卡(`.products-mid-card`)— 单列,padding 缩
- KPI 卡(`.products-kpi-*`)— 4 列 → 2 列(≤480)或 2 列(481-768)
- sparkline / trend(`.products-mid-card-sales-trend`)— 保留,但月销行高度自适应
- 8 色 letter avatar — 维持,字号 / 尺寸不变(品牌识别优先)
- 品牌色块 — 维持
- 筛选栏 — 顶部"筛选"按钮 → 抽屉式

## 5. 侧栏抽屉规范

### 5.1 触发

- 移动端(≤768px):汉堡按钮(顶左 24×24 SVG)→ 抽屉滑入
- 平板(769-1024px):侧栏默认收起,顶左汉堡 → 抽屉
- 桌面(>1024px):维持当前固定侧栏

### 5.2 抽屉样式

- 全屏高度,宽度 280px(手机) / 320px(平板)
- 背景 `var(--color-bg, #FFFFFF)` + 右侧 box-shadow
- 滑入动画 240ms ease-out
- 点击抽屉外区域 / Esc / 关闭按钮 → 滑出
- 抽屉内模块顺序与桌面侧栏一致

### 5.3 不动

- 桌面版固定侧栏代码 — 维持
- 侧栏数据 / 权限逻辑
- 头像 / 状态点 / 在线状态视觉

## 6. V2.1 token 对齐原则

所有新增响应式 CSS:

- **断点用变量**:`@media (max-width: 480px)` 别写死 `480px`,用 `@media (max-width: var(--bp-sm))`
- **颜色全 token**:`var(--color-bg, #FFFFFF)` / `var(--color-text-primary, #1C1C1E)` 等
- **间距全 token**:`var(--space-2)` / `var(--space-4)` 等
- **老 token 名禁用**:`var(--text-primary)` / `var(--separator)` 等是 V2.0 命名,统一替换

## 7. 协作约定

### 7.1 worktree

| 阶段 | branch | base | commit 频率 |
|------|--------|------|-------------|
| 1. audit | `feat/responsive-audit` | origin/dev | 1 commit(只产出 audit 文档) |
| 2. 核心 3 页 | `feat/responsive-chat` / `feat/responsive-talent` / `feat/responsive-product` | origin/dev | 每页 1 commit,独立 branch |
| 3. 侧栏抽屉 | `feat/sidebar-drawer-mobile` | origin/dev | 1 commit |

每分支独立 push,老大 review 一项合一项。**不 squash**(老大明确要求)。

### 7.2 commit message 模板

```
ui(<scope>): <mobile/responsive> 简述

- 改动 1
- 改动 2

Scope: <具体页/组件>
Breakpoints: <影响的断点>
```

### 7.3 不动的东西(铁律)

- ❌ 数据 / 后端 / API
- ❌ V2.1 已 polish 过的设计(只新增响应式,不重做样式)
- ❌ `data/` 任何文件
- ❌ `solobrave-server.py` 业务逻辑

## 8. 验收标准

每完成 1 项, Mini 给老大 5 张截图:

1. 手机竖屏 375×667(iPhone SE 尺寸)
2. 手机横屏 667×375
3. 平板竖屏 768×1024
4. 平板横屏 1024×768
5. 桌面 1440×900(确认桌面版无 regression)

每张截图标注:
- 操作路径(哪个按钮 → 哪个页面)
- 视觉一致性(V2.1 token 是否生效)
- 触摸目标(关键按钮 ≥ 44×44px)
- 横向滚动(应无)

## 9. 老大的可调参数

如果你(Mini)发现:
- 断点不合理(用户群有特定机型集中)
- 某页"不动"会影响核心体验
- audit 发现 P0 问题需要立即处理

随时在 commit message 或单独通知里标出,我(Jarvis)review。

## 10. 排期建议(老大可调整)

| 周 | 内容 | 验收 |
|----|------|------|
| W1 上半 | audit 文档 | 报告完成,老大 review |
| W1 下半 | 侧栏抽屉骨架 | 3 平台截图 OK |
| W2 上半 | 聊天页适配 | 3 平台截图 OK |
| W2 下半 | 达人库适配 | 3 平台截图 OK |
| W3 上半 | 商品库适配 | 3 平台截图 OK |
| W3 下半 | 集成验收 | 全站无 regression |

— 写于 2026-09-19 · Jarvis (mavis) · 给 Mini 响应式适配执行 brief