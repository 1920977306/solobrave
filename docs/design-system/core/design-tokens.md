# SoloBrave 设计 Token 规范（V2）

> 适用版本：V2 架构 + SoloBrave 抖音团长项目
> 设计语言：Apple HIG + 飞书克制感
> 核心差异化：**AI 员工视觉协议** + **评级色视觉签名**
> 最后更新：2026-09-08

---

## 目录

1. [设计原则](#1-设计原则)
2. [架构](#2-架构)
3. [颜色系统](#3-颜色系统)
4. [字体系统](#4-字体系统)
5. [间距 & 圆角](#5-间距--圆角)
6. [阴影 & 光晕](#6-阴影--光晕)
7. [动效](#7-动效)
8. [组件规范](#8-组件规范)
9. [会员制 UI](#9-会员制-ui)
10. [数据通道 UI](#10-数据通道-ui)
11. [Onboarding 流程](#11-onboarding-流程)
12. [空状态](#12-空状态)
13. [角色与权限](#13-角色与权限)
14. [使用禁忌](#14-使用禁忌)
15. [多项目扩展](#15-多项目扩展)
16. [落地建议](#16-落地建议)
17. [版本历史](#17-版本历史)

---

## 1. 设计原则

| 原则 | 含义 |
|---|---|
| **AI 优先视觉** | AI 员工必须有视觉签名（光晕 / 徽章 / 底色），与真人员工一眼可分 |
| **评级即签名** | A/B/C/D 评级是产品的核心心智模型，必须在卡片任何位置都能识别 |
| **一信息一表达** | 状态徽章只保留最重要的一个；元信息用 chip 不堆字 |
| **克制胜于装饰** | 卡片默认无阴影，hover 才有；颜色只用于语义，不用于装饰 |
| **数据优于空** | "未采集"比"0"或"-"更尊重用户 |
| **新人团长视角** | 空状态、引导、CTA 都按"想成为团长的人"写，不要写成老司机语言 |
| **虚拟团队叙事** | AI 员工是产品的"虚拟团队"，是定价轴、onboarding 故事、第一句空状态 |

---

## 2. 架构

```
docs/design-system/
├── core/                # 跨项目通用
│   ├── tokens.css       # CSS 变量（无品牌色）
│   ├── components.css   # 通用组件类
│   ├── design-tokens.md # 本文档
│   └── preview.html     # 可视化预览
├── themes/              # 项目级品牌
│   └── solobrave.css    # SoloBrave（抖音团长）
├── domain/              # 领域术语
│   └── glossary.md      # 中英对照 + 后端字段映射
├── archive/             # 历史版本
│   └── V1/              # V1 文件备份
└── README.md            # 架构说明
```

**核心约束**：
- `core/` 内**不含任何品牌色**——所有品牌相关变量都从主题层覆盖
- 主题层只覆盖 3-5 个变量（主色 / 主色 hover / 主色软底 / 项目名 / 项目 tagline）
- 新增项目 = 复制 `solobrave.css` 改 5 个变量，**不动 core**

---

## 3. 颜色系统

### 3.1 主色（来自主题层）

| Token | 来源 | 用途 |
|---|---|---|
| `--color-primary` | `--theme-primary` | 主按钮、链接、激活态 |
| `--color-primary-hover` | `--theme-primary-hover` | 主按钮 hover |
| `--color-primary-soft` | `--theme-primary-soft` | 选中背景、提示性 chip |

> **关键规则**：所有产品级 UI 都用 `--color-primary`，**不**写死 `#007AFF`。
> 改主题时只改 theme 文件。

### 3.2 AI 视觉协议（产品级差异化，跨项目统一）

| Token | 值 | 用途 |
|---|---|---|
| `--color-ai-accent` | `#5E5CE6` | AI 标识主色（Apple Indigo） |
| `--color-ai-glow` | `rgba(94, 92, 230, 0.40)` | AI 头像光晕 |
| `--color-ai-ring` | `#5E5CE6` | AI 头像描边 |
| `--color-ai-bg-soft` | `rgba(94, 92, 230, 0.05)` | AI 卡片底色 |
| `--color-ai-bg-softer` | `rgba(94, 92, 230, 0.08)` | 示例 prompt chip 底色 |
| `--color-ai-bg-strong` | `rgba(94, 92, 230, 0.15)` | AI 强调背景 |
| `--color-ai-pulse` | `rgba(94, 92, 230, 0.60)` | 侧栏 AI 在线脉冲点 |

> **关键规则**：AI 视觉协议**全产品统一**——主区、侧栏、空状态、对话气泡、消息列表都必须用同一套。真人侧**禁用** indigo 色。

### 3.3 评级色（核心视觉签名）

| Token | 值 | 等级 | 含义 |
|---|---|---|---|
| `--color-rating-a` | `#34C759` | A 级 | 头部 / 高 ROI |
| `--color-rating-b` | `#007AFF` | B 级 | 优质 / 主推 |
| `--color-rating-c` | `#FFCC00` | C 级 | 一般 / 观察 |
| `--color-rating-d` | `#8E8E93` | D 级 | 边缘 / 待复评 |

> **视觉契约**：
> - 卡片**左侧 4px 色条** = 评级色
> - 卡片**头像底色** = 评级色（白字）
> - **两者必须一致**，不可冲突
> - 评级色只用于评级场景，不可挪用

### 3.4 合作状态色

| Token | 值 | 状态 |
|---|---|---|
| `--color-status-pending` | `#8E8E93` | 待跟进 |
| `--color-status-talking` | `#FF9500` | 沟通中 |
| `--color-status-active` | `#34C759` | 合作中 |
| `--color-status-lost` | `#FF3B30` | 已流失 |
| `--color-status-paused` | `#AF52DE` | 暂停 |

每个状态色都配套 `*-bg` 软底（12% 透明度），用于徽章背景。

### 3.5 数据置信度 & 新鲜度

| Token | 值 | 含义 |
|---|---|---|
| `--color-confidence-high` | `#34C759` | AI 识别高置信度（≥0.85） |
| `--color-confidence-medium` | `#FF9500` | 中置信度（0.6-0.85） |
| `--color-confidence-low` | `#FF3B30` | 低置信度（<0.6） |
| `--color-freshness-fresh` | `#34C759` | 数据 7 天内 |
| `--color-freshness-aging` | `#FF9500` | 数据 7-30 天 |
| `--color-freshness-stale` | `#8E8E93` | 数据 30 天+（卡片降级） |

### 3.6 中性色

| Token | 值 | 用途 |
|---|---|---|
| `--color-bg` | `#FFFFFF` | 页面主背景 |
| `--color-bg-soft` | `#F5F5F7` | 二级背景 |
| `--color-bg-softer` | `#F2F2F7` | 三级背景（hover、chip） |
| `--color-sidebar` | `#1C1C1E` | 左侧深色栏 |
| `--color-sidebar-hover` | `rgba(255, 255, 255, 0.08)` | 深色栏 hover |
| `--color-border` | `#E5E5EA` | 标准边框 |
| `--color-border-subtle` | `#F2F2F7` | 浅边框 |
| `--color-divider` | `#F2F2F7` | 内容分隔线 |
| `--color-text-primary` | `#1C1C1E` | 主文字 |
| `--color-text-secondary` | `#6E6E73` | 次要文字 |
| `--color-text-tertiary` | `#AEAEB2` | 三级文字 |
| `--color-text-disabled` | `#C7C7CC` | 禁用文字 |
| `--color-text-inverse` | `#FFFFFF` | 深色背景上的文字 |

### 3.7 平台色（**仅 chip 文字用**）

平台色不进 core 的固定变量。**chip 颜色由 HTML 的 `data-color` 属性驱动**：

```html
<span class="chip chip-platform" data-color="#FE2C55">抖音</span>
```

```css
.chip-platform[data-color] { color: var(--chip-color); }
/* 由组件层自动从 data-color 提取到 --chip-color */
```

> 项目级默认平台色（如 SoloBrave 默认抖音）写在主题层：
> ```css
> /* themes/solobrave.css */
> :root { --platform-douyin: #FE2C55; }
> ```

---

## 4. 字体系统

### 4.1 字体栈

```css
--font-sans: -apple-system, BlinkMacSystemFont, "PingFang SC",
             "Microsoft YaHei", "HarmonyOS Sans", sans-serif;
--font-num: "SF Pro", "Inter", -apple-system, "JetBrains Mono", monospace;
```

> 数字统一用 SF Pro / Inter，等宽数字用 `font-variant-numeric: tabular-nums`。

### 4.2 字号阶梯

| Token | size / line-height / weight | 用途 |
|---|---|---|
| `--fs-display` | 32 / 40 / 600 | 欢迎页主标题 |
| `--fs-h1` | 24 / 32 / 600 | 页面标题 |
| `--fs-h2` | 20 / 28 / 600 | 区块标题 |
| `--fs-h3` | 17 / 24 / 600 | 卡片标题、姓名 |
| `--fs-body-lg` | 16 / 24 / 400 | 大正文、卡片主指标 |
| `--fs-body` | 14 / 20 / 400 | 标准正文 |
| `--fs-caption` | 12 / 18 / 500 | chip 文字、辅助说明 |
| `--fs-micro` | 11 / 16 / 500 | 时间戳、单位 |

### 4.3 字重 & 字间距

```css
--fw-regular: 400;
--fw-medium:  500;
--fw-semibold: 600;
--fw-bold:    700;  /* 仅用于关键数字 */

--tracking-tight: -0.02em;  /* Display / H1 */
--tracking-wide: 0.04em;    /* Micro 标签 */
```

---

## 5. 间距 & 圆角

### 5.1 间距

| Token | 值 | 用途 |
|---|---|---|
| `--space-1` | 4px | 微调 |
| `--space-2` | 8px | 紧邻元素 |
| `--space-3` | 12px | chip 内边距 |
| `--space-4` | 16px | 卡片内边距 |
| `--space-5` | 20px | 卡片组间距 |
| `--space-6` | 24px | 区块间距 |
| `--space-8` | 32px | 页面边距 |
| `--space-10` | 40px | 大区块间距 |
| `--space-12` | 48px | hero 间距 |
| `--space-16` | 64px | 落地页 hero |

### 5.2 圆角

| Token | 值 | 用途 |
|---|---|---|
| `--radius-sm` | 6px | chip、tag |
| `--radius-md` | 8px | 按钮、输入框 |
| `--radius-lg` | 12px | 卡片、主容器 |
| `--radius-xl` | 16px | 弹层 |
| `--radius-2xl` | 20px | 大型 panel |
| `--radius-full` | 9999px | 头像、pill 徽章 |

---

## 6. 阴影 & 光晕

### 6.1 阴影

```css
--shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06),
             0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.08);
--shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.12);
--shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.16);
```

### 6.2 AI 光晕（产品签名）

```css
--shadow-ai:         /* 头像外光晕（主区） */
  0 0 0 2px var(--color-ai-ring),
  0 0 16px var(--color-ai-glow);

--shadow-ai-subtle:  /* 侧栏小头像（减弱） */
  0 0 0 1.5px var(--color-ai-ring);

--shadow-ai-strong:  /* 选中 / 强调态 */
  0 0 0 3px var(--color-ai-ring),
  0 0 24px var(--color-ai-glow);
```

---

## 7. 动效

```css
--ease-out: cubic-bezier(0.16, 1, 0.3, 1);
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);

--duration-fast: 120ms;
--duration-normal: 200ms;
--duration-slow: 320ms;
```

`prefers-reduced-motion: reduce` 时全部归零。

---

## 8. 组件规范

### 8.1 达人卡片

**结构**：
```
┌─────────────────────────────────────┐ ← 左侧 4px 评级色条
│ [头像48] 姓名  H3 / 600              │
│          平台chip(品牌色)  类目chip   │
│                                      │
│ 120万        3.2%         8.6万      │ ← 粉丝量为主指标
│ 粉丝量      互动率       带货GMV     │
│                                      │
│ [AI 匹配 87%]  3 天前    [状态徽章]   │
└─────────────────────────────────────┘
```

**规格**：
- 背景：white
- 边框：1px `--color-border-subtle`（hover 升级为 `--color-border`）
- 圆角：`--radius-lg` (12px)
- 阴影：默认无，hover 升 `--shadow-md`
- 左色条：4px × 100% 评级色
- 头像：48px，底色=评级色，文字白色 H3 weight
- 主指标（粉丝量）：`--fs-body-lg` / `--fw-semibold`
- 副指标：`--fs-body` / secondary
- 单位：紧贴数字（"120万"），单位字号同数字 80%
- 状态徽章：右下角
- 数据陈旧时（30 天+）：opacity 0.7 + "数据陈旧"标签

**数据属性驱动**：
```html
<div class="talent-card" data-rating="B" data-status="talking" data-stale="false">
```

### 8.2 AI 员工卡

复用达人卡片结构，叠加：
- 头像外 2px indigo 描边 + 16px 外发光
- 头像右下角"AI"小徽章
- 卡片底色：`--color-ai-bg-soft`（5% indigo）
- 姓名下方**必须**保留角色描述（2 行以内）
- 整卡 hover 时，AI 光晕**不消失**（保持身份）

```html
<div class="talent-card ai-employee" data-rating="B">
  ...
  <div class="name">Helen</div>
  <div class="role-desc">商务拓展，达人建联与合作洽谈</div>
  ...
</div>
```

### 8.3 真人卡片（侧栏 / 主区）

- 头像 32-48px
- **无任何描边和光晕**
- 在线点：绿色 `#34C759`
- 离线点：灰色 `#C7C7CC`

**AI 和真人视觉差异的硬规则**：
1. AI 有 indigo 光晕或描边，真人无
2. AI 状态点是 indigo 脉冲，真人是绿/灰
3. AI 卡片底色微带 indigo，真人纯白

### 8.4 状态徽章

```html
<span class="badge-status" data-status="active">合作中</span>
```

颜色自动从 `data-status` 派生。

### 8.5 平台 / 类目 chip

```html
<span class="chip chip-platform" data-color="#FE2C55">抖音</span>
<span class="chip">户外</span>  <!-- 类目用默认色 -->
```

**视觉区分**：
- 平台 chip：品牌色（由 data-color 驱动）
- 类目 chip：中性色（`--color-text-secondary`）

### 8.6 计划徽章（会员制）

```html
<span class="badge-plan" data-plan="free">Free</span>
<span class="badge-plan" data-plan="pro">Pro</span>
<span class="badge-plan" data-plan="enterprise">Enterprise</span>
```

### 8.7 按钮

| 角色 | 类 | 用途 |
|---|---|---|
| 主按钮 | `btn btn-primary` | 新建对话、确认 |
| 次按钮 | `btn btn-secondary` | 取消、返回 |
| AI 按钮 | `btn btn-ai` | 触发 AI 操作（特殊强调） |
| 危险 | `btn btn-danger` | 删除、流失 |
| 行内链接 | `btn btn-ghost` | "达人录入 →" |

尺寸：`btn-sm` (28px) / 默认 (36px) / `btn-lg` (44px)。

### 8.8 示例 prompt chip

```html
<span class="prompt-chip">帮我分析一位达人的带货数据</span>
```

AI 入口专用样式，浅蓝底 + hover 加深 + 上浮 1px。

### 8.9 FAB（悬浮操作）

```html
<button class="fab">📷</button>
```

右下角固定位置，AI 主色背景，**用于"上传截图"主入口**。

### 8.10 AI 配额

```html
<div class="quota-meter" data-usage="0.7">
  <div class="quota-bar"></div>
  <div class="quota-label">今日已用 7/10</div>
</div>
```

`data-usage` 0-1，达到 0.9 时变红。

### 8.11 截图录入

```html
<div class="screenshot-drop">
  <div class="screenshot-drop-icon">📷</div>
  <div class="screenshot-drop-title">上传抖音数据截图</div>
  <div class="screenshot-drop-hint">支持拖拽、粘贴、点击上传（可多张）</div>
</div>
```

处理中：
```html
<div class="screenshot-progress">
  <div class="screenshot-progress-spinner"></div>
  <div>
    <div class="screenshot-progress-text">AI 正在识别第 2 张截图…</div>
    <div class="screenshot-progress-meta">已识别 87%</div>
  </div>
</div>
```

### 8.12 字段置信度

```html
<span class="field-confidence" data-confidence="high">粉丝量 120万</span>
<span class="field-confidence" data-confidence="medium">互动率 3.2%</span>
<span class="field-confidence" data-confidence="low">带货 GMV</span>
```

AI 识别字段必带置信度标识。**低置信度必须提示用户复核**。

### 8.13 升级引导

```html
<div class="upgrade-cta">
  <div class="upgrade-cta-text">
    <div class="upgrade-cta-title">解锁第 2 位 AI 员工</div>
    <div class="upgrade-cta-desc">升级到 Pro · ¥99/月</div>
  </div>
  <button class="btn btn-primary">立即升级</button>
</div>
```

### 8.14 Onboarding 步骤

```html
<div class="onboarding-step">
  <div class="onboarding-step-num">1</div>
  <div class="onboarding-step-content">
    <div class="onboarding-step-title">认识你的 AI 团队</div>
    <div class="onboarding-step-desc">3 位 AI 员工已就位</div>
  </div>
</div>
```

---

## 9. 会员制 UI

### 9.1 计划标识

```html
<span class="badge-plan" data-plan="free">Free</span>
<span class="badge-plan" data-plan="pro">Pro</span>
<span class="badge-plan" data-plan="enterprise">Enterprise</span>
```

### 9.2 配额条

见 8.10。配额用完时变红 + 弹升级 CTA。

### 9.3 锁定态

```html
<div class="lock-feature">
  <!-- 功能内容 -->
</div>
```

**关键规则**：用锁定而不是禁用灰显。**保留视觉存在感**，教育用户"我可以升级到能用"。

### 9.4 升级引导

见 8.13。**触达点**：
- AI 员工数用完
- 达人数用完
- API 接入入口
- 高级 AI 员工（如自定义 AI）

---

## 10. 数据通道 UI

### 10.1 通用原则

所有数据从任何通道进入 → 同一个"AI 复核确认"流。

**V1 只支持"抖音截图"通道**。架构允许未来加 API。

### 10.2 截图录入

见 8.11。**主交互位置**：
- 达人详情页顶部（HERO）
- 全局 FAB（右下角悬浮）
- 快捷键 Cmd+Shift+V 粘贴

### 10.3 AI 复核

AI 识别后展示结果 + 置信度 + 让用户确认/修改。**必须**有这一步。

### 10.4 反馈循环

用户修改 AI 识别结果后，**回传**给 AI 学习。这是产品的"虚拟团队变得更聪明"叙事点。

---

## 11. Onboarding 流程

**目标用户**：想成为抖音团长的人（新人/小白）

### 11.1 三步引导

1. **认识 AI 团队** —— 介绍 3-4 位 AI 员工，**强调"虚拟团队"**
2. **录入第一个达人** —— 引导上传截图，AI 自动解析
3. **完成里程碑庆祝** —— "🎉 你的虚拟团队已就位"

### 11.2 引导组件

见 8.14。

### 11.3 跳过条件

老用户（已有数据）直接跳过，引导"补全你的 AI 团队"。

---

## 12. 空状态

### 12.1 三段式模板

1. 插画 / 图标（48-64px，indigo 描边线性）
2. 标题（一句话行动）
3. 副标题（解释 + 降低门槛）
4. 主 CTA + 次 CTA

### 12.2 新人团长视角的文案

**❌ 禁用**：
- "暂无达人"
- "快来添加你的第一个达人吧！"

**✅ 正确**：
- **空达人库** → "你的虚拟团队已就绪，先录入第一位达人"
- **空对话** → "挑一位 AI 员工开始协作"
- **空截图** → "上传你的第一张抖音数据截图"

### 12.3 通用模板

```html
<div class="empty-state">
  <div class="empty-icon">🎯</div>
  <h3>开始搭建你的达人矩阵</h3>
  <p>从抖音链接录入达人，AI 自动分析 6 维度匹配度</p>
  <div class="empty-ctas">
    <button class="btn btn-primary">批量导入达人</button>
    <button class="btn btn-secondary">搜索达人</button>
  </div>
</div>
```

---

## 13. 角色与权限

| 角色 | 看 | 写 | UI 表现 |
|---|---|---|---|
| 管理员 | 全部 | 全部 | 顶部显示"管理"入口 |
| 商务 | 全部 | 达人字段 | 按钮可见，hover 提示权限 |
| 运营 | 全部 | 商品 | 同上 |
| AI 员工 | 自己创建 | 自己的达人 | 头像带 AI 视觉协议 |
| 真人 | 自己创建 | 自己的达人 | 头像无 AI 协议 |

**UI 表现规则**：
- 权限不足时，**按钮可见但触发权限提示**
- **不**用禁用灰显（保留视觉一致性）

---

## 14. 使用禁忌

| 禁忌 | 原因 |
|---|---|
| ❌ 评级色用在非评级场景 | 颜色必须语义化 |
| ❌ 真人头像加 indigo 描边 | AI 协议被稀释 |
| ❌ 同一卡片挂 3+ 状态徽章 | 信息噪声 |
| ❌ 标题用 `text-overflow: ellipsis` 截断 | 截断是 bug 的伪装 |
| ❌ 时间和日期用 ISO 格式 | 用相对时间"今天 14:06" |
| ❌ 缺数据用 `-` 或 `0` | 用"未采集"+ 浅灰底 |
| ❌ UI 上留 "未知"/"undefined" | 数据脏 |
| ❌ 用 emoji 替代状态色 | 状态有专门颜色，emoji 是二次信号 |
| ❌ **在产品 UI 露内部 jargon**（OpenClaw 等） | 外部产品禁用 |
| ❌ 空状态写"暂无XX" | 用户视角改写 |
| ❌ **错误消息道歉**（"很抱歉..."） | 直接说发生了什么 + 下一步 |
| ❌ 在 core 里写品牌色 | 品牌色只进 themes/ |
| ❌ 在主按钮写死颜色 | 必须用 `--color-primary` |

---

## 15. 多项目扩展

### 15.1 加新项目（5 分钟）

1. 复制 `themes/solobrave.css` 为 `themes/xiaohongshu.css`
2. 改 3-5 个变量：
   ```css
   --theme-primary: #FF2442;        /* 小红书红 */
   --theme-primary-hover: #E62038;
   --theme-primary-soft: rgba(255, 36, 66, 0.10);
   --project-name: "SoloBrave · 小红书";
   --project-tagline: "...";
   ```
3. 在项目 HTML 加 `<html data-project="xiaohongshu">` 即可

**core 0 修改**。

### 15.2 加新平台

**不要**在 `core/components.css` 加新平台样式。  
**改用**：在项目 HTML 里 `data-color` 驱动。

未来如果平台很多（>5），考虑用后端返回 `data-color` 而非硬编码。

---

## 16. 落地建议

1. **CSS 引入顺序**：
   ```html
   <link rel="stylesheet" href="docs/design-system/core/tokens.css">
   <link rel="stylesheet" href="docs/design-system/core/components.css">
   <link rel="stylesheet" href="docs/design-system/themes/solobrave.css">
   ```

2. **Tailwind 用户**：把 `tokens.css` 映射到 `tailwind.config.js` 的 `theme.extend`，AI 颜色 / 评级色作为 colors 的命名子项

3. **新增组件前先 grep** token：确认 token 已存在再写新值

4. **改 token 先改这里**：不要在散落的 inline style 里改

5. **V1 → V2 迁移**：原有 `index.html` 里的 inline 颜色/字号分批替换成 token，**不要一次性重构**

---

## 17. 版本历史

| 版本 | 日期 | 关键变化 |
|---|---|---|
| V1 | 2026-09-08 | 初版：AI 视觉协议 + 评级色 + 卡片规范 |
| V2 | 2026-09-08 | 架构拆分 core/themes/domain；加会员制 UI；加截图录入 + 置信度/新鲜度；加 onboarding；术语词典；多项目扩展 |
