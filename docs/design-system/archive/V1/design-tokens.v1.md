# SoloBrave 设计 Token 规范

> 适用版本：V3 改版及之后
> 设计语言：Apple HIG + 飞书克制感
> 核心差异化：AI 员工视觉协议 + 评级色条视觉签名
> 最后更新：2026-09-08

---

## 1. 设计原则（design principles）

| 原则 | 含义 |
|---|---|
| **AI 优先视觉** | AI 员工必须有视觉签名（光晕 / 徽章 / 底色），与真人员工一眼可分 |
| **评级即签名** | A/B/C/D 评级是产品的核心心智模型，必须在卡片任何位置都能识别 |
| **一信息一表达** | 状态徽章只保留最重要的一个；元信息用 chip 不堆字 |
| **克制胜于装饰** | 卡片默认无阴影，hover 才有；颜色只用于语义，不用于装饰 |
| **数据优于空** | "未采集"比"0"或"-"更尊重用户 |

---

## 2. 颜色系统

### 2.1 品牌主色

| Token | 值 | 用途 |
|---|---|---|
| `--color-primary` | `#007AFF` | 主按钮、链接、激活态 |
| `--color-primary-hover` | `#0066D6` | 主按钮 hover |
| `--color-primary-soft` | `rgba(0, 122, 255, 0.10)` | 选中背景、提示性 chip |

### 2.2 AI 视觉协议（产品级差异化）

| Token | 值 | 用途 |
|---|---|---|
| `--color-ai-accent` | `#5E5CE6` | AI 标识主色（Apple Indigo） |
| `--color-ai-glow` | `rgba(94, 92, 230, 0.40)` | AI 头像光晕 |
| `--color-ai-ring` | `#5E5CE6` | AI 头像描边 |
| `--color-ai-bg-soft` | `rgba(94, 92, 230, 0.05)` | AI 卡片底色 |
| `--color-ai-bg-softer` | `rgba(94, 92, 230, 0.08)` | 示例 prompt chip 底色 |
| `--color-ai-pulse` | `rgba(94, 92, 230, 0.60)` | 侧栏 AI 在线脉冲点 |

> **关键规则**：AI 视觉协议**全产品统一**——主区、侧栏、空状态、对话气泡、消息列表都必须用同一套。真人侧**禁用** indigo 色。

### 2.3 评级色（核心视觉签名）

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
> - "AI 匹配度 87%"等元信息也用评级色作为进度条

### 2.4 合作状态色

| Token | 值 | 状态 | 含义 |
|---|---|---|---|
| `--color-status-pending` | `#8E8E93` | 待跟进 | 未联系 |
| `--color-status-talking` | `#FF9500` | 沟通中 | 谈判期 |
| `--color-status-active` | `#34C759` | 合作中 | 已在合作 |
| `--color-status-lost` | `#FF3B30` | 已流失 | 终止合作 |
| `--color-status-paused` | `#AF52DE` | 暂停 | 暂时搁置 |

### 2.5 中性色（surface / text）

| Token | 值 | 用途 |
|---|---|---|
| `--color-bg` | `#FFFFFF` | 页面主背景 |
| `--color-bg-soft` | `#F5F5F7` | 二级背景（卡片分组） |
| `--color-bg-softer` | `#F2F2F7` | 三级背景（hover、chip） |
| `--color-sidebar` | `#1C1C1E` | 左侧深色栏 |
| `--color-sidebar-hover` | `rgba(255, 255, 255, 0.08)` | 深色栏 hover |
| `--color-border` | `#E5E5EA` | 标准边框 |
| `--color-border-subtle` | `#F2F2F7` | 浅边框（卡片内分隔） |
| `--color-divider` | `#F2F2F7` | 内容分隔线 |
| `--color-text-primary` | `#1C1C1E` | 主文字 |
| `--color-text-secondary` | `#6E6E73` | 次要文字（标签、辅助） |
| `--color-text-tertiary` | `#AEAEB2` | 三级文字（时间戳、placeholder） |
| `--color-text-disabled` | `#C7C7CC` | 禁用文字 |
| `--color-text-inverse` | `#FFFFFF` | 深色背景上的文字 |

---

## 3. 字体系统

### 3.1 字体栈

```css
--font-sans: -apple-system, BlinkMacSystemFont, "PingFang SC",
             "Microsoft YaHei", "HarmonyOS Sans", sans-serif;
--font-num: "SF Pro", "Inter", -apple-system, "JetBrains Mono", monospace;
```

> 数字统一用 SF Pro / Inter，等宽数字用 `font-variant-numeric: tabular-nums`。

### 3.2 字号阶梯

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

### 3.3 字重

```css
--fw-regular: 400;
--fw-medium:  500;
--fw-semibold: 600;
--fw-bold:    700;  /* 仅用于关键数字 */
```

### 3.4 字间距

```css
--tracking-tight: -0.02em;   /* Display / H1 */
--tracking-normal: 0;        /* 默认 */
--tracking-wide: 0.04em;     /* Micro 标签 */
```

---

## 4. 间距 & 圆角

### 4.1 间距（4 / 8 基准）

| Token | 值 | 用途 |
|---|---|---|
| `--space-1` | 4px | 元素内微调 |
| `--space-2` | 8px | 紧邻元素 |
| `--space-3` | 12px | chip 内边距 |
| `--space-4` | 16px | 卡片内边距 |
| `--space-5` | 20px | 卡片组间距 |
| `--space-6` | 24px | 区块间距 |
| `--space-8` | 32px | 页面边距 |
| `--space-10` | 40px | 大区块间距 |
| `--space-12` | 48px | hero 间距 |

### 4.2 圆角

| Token | 值 | 用途 |
|---|---|---|
| `--radius-sm` | 6px | chip、tag |
| `--radius-md` | 8px | 按钮、输入框 |
| `--radius-lg` | 12px | 卡片、主容器 |
| `--radius-xl` | 16px | 弹层、模态 |
| `--radius-2xl` | 20px | 大型 panel |
| `--radius-full` | 9999px | 头像、pill 徽章 |

---

## 5. 阴影 & 光晕

### 5.1 阴影阶梯

```css
--shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06),
             0 1px 2px rgba(0, 0, 0, 0.04);
--shadow-md: 0 4px 12px rgba(0, 0, 0, 0.08);
--shadow-lg: 0 8px 24px rgba(0, 0, 0, 0.12);
--shadow-xl: 0 16px 48px rgba(0, 0, 0, 0.16);
```

### 5.2 AI 光晕（产品签名）

```css
/* 头像外光晕（主区卡片） */
--shadow-ai: 0 0 0 2px var(--color-ai-ring),
             0 0 16px var(--color-ai-glow);

/* 头像外光晕（侧栏小头像，减弱版） */
--shadow-ai-subtle: 0 0 0 1.5px var(--color-ai-ring);

/* AI 在线脉冲点 */
--shadow-ai-pulse: 0 0 0 0 var(--color-ai-pulse);
```

---

## 6. 动效

```css
--ease-out: cubic-bezier(0.16, 1, 0.3, 1);
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);

--duration-fast: 120ms;
--duration-normal: 200ms;
--duration-slow: 320ms;
```

> 偏好设置 `prefers-reduced-motion` 时，全部归零。

---

## 7. 组件规范

### 7.1 达人卡片（Talent Card）

**结构**：
```
┌─────────────────────────────────────┐ ← 左侧 4px 评级色条
│ [头像48] 姓名  H3 / 600              │
│          平台chip  类目chip           │
│                                      │
│ 120万        3.2%         8.6万      │ ← 粉丝量为主指标（fs-body-lg）
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
- 头像：48px，底色=评级色，文字白色 H3 weight，显示姓首字
- 主指标（粉丝量）：`--fs-body-lg` / `--fw-semibold`
- 副指标（互动率/GMV）：`--fs-body` / 文字 secondary
- 单位：紧贴数字（"120万"），单位字号同数字 80%
- 状态徽章：右下角
- 元信息行：底部，字号 caption，颜色 tertiary

### 7.2 AI 员工卡（AI Employee Card）

**复用达人卡片结构**，叠加：
- 头像外 2px indigo 描边 + 16px 外发光
- 头像右下角"AI"小徽章（10px 蓝底白字）
- 卡片底色：`--color-ai-bg-soft`（5% indigo）
- 姓名下方**必须**保留角色描述（2 行以内，14px / secondary）
- 整卡 hover 时，AI 光晕**不消失**（保持身份）

**侧栏 AI 头像**（缩略版）：
- 头像 32px
- 描边 1.5px indigo（不发强光）
- 右下角"AI"小圆点（4px 蓝实心）

### 7.3 真人头像（侧栏 / 主区）

- 头像 32-48px
- **无任何描边和光晕**
- 在线点：绿色 `#34C759`
- 离线点：灰色 `#C7C7CC`

> **AI 和真人视觉差异的硬规则**：
> 1. AI 有 indigo 光晕或描边，真人无
> 2. AI 状态点是 indigo 脉冲，真人是绿/灰
> 3. AI 卡片底色微带 indigo，真人纯白

### 7.4 评级色条 + 头像协议

```css
.talent-card {
  border-left: 4px solid var(--rating-color);
}
.talent-card .avatar {
  background: var(--rating-color);
  color: white;
}
.talent-card[data-rating="A"] { --rating-color: var(--color-rating-a); }
.talent-card[data-rating="B"] { --rating-color: var(--color-rating-b); }
.talent-card[data-rating="C"] { --rating-color: var(--color-rating-c); }
.talent-card[data-rating="D"] { --rating-color: var(--color-rating-d); }
```

> ⚠️ **不要**在数据属性以外的地方硬编码评级色。所有评级色都走 CSS 变量。

### 7.5 状态徽章

```css
.badge-status {
  display: inline-flex;
  align-items: center;
  height: 22px;
  padding: 0 8px;
  border-radius: var(--radius-full);
  font-size: var(--fs-caption);
  font-weight: var(--fw-medium);
}
.badge-status[data-status="pending"] {
  background: rgba(142, 142, 147, 0.12);
  color: var(--color-status-pending);
}
.badge-status[data-status="talking"] {
  background: rgba(255, 149, 0, 0.12);
  color: var(--color-status-talking);
}
/* ... 其他状态类似 */
```

### 7.6 平台 / 类目 chip

```css
.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 20px;
  padding: 0 8px;
  border-radius: var(--radius-sm);
  font-size: var(--fs-caption);
  background: var(--color-bg-softer);
  color: var(--color-text-secondary);
}
.chip-platform-douyin { color: #FE2C55; }
.chip-platform-xiaohongshu { color: #FF2442; }
.chip-platform-bilibili { color: #FB7299; }
.chip-platform-wechat { color: #07C160; }
```

> 平台 chip 用品牌色，类目 chip 用中性色，**视觉上必须能区分**。

### 7.7 主按钮 / 次按钮 / 文字链接

| 角色 | 背景 | 文字 | 边框 | 用途 |
|---|---|---|---|---|
| Primary | `--color-primary` | white | 无 | 主行动（新建对话、确认） |
| Secondary | `--color-bg-soft` | `--color-text-primary` | 无 | 次行动（取消、返回） |
| Ghost | transparent | `--color-primary` | 无 | 行内链接（达人录入、达人搜索） |
| Danger | `--color-status-lost` | white | 无 | 危险操作（删除、流失） |

按钮统一：`--radius-md` (8px)，高度 36px（主）/ 28px（次）。

### 7.8 示例 prompt chip（AI 入口）

```css
.prompt-chip {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  border-radius: var(--radius-full);
  background: var(--color-ai-bg-softer);
  color: var(--color-text-primary);
  font-size: var(--fs-body);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}
.prompt-chip:hover {
  background: rgba(94, 92, 230, 0.15);
}
```

---

## 8. 空状态

**三段式**：
1. 插画 / 图标（48-64px，indigo 描边线性）
2. 标题（一句话行动）
3. 副标题（解释 + 降低门槛）
4. 主 CTA + 次 CTA

**模板**：
> 🎯 [开始搭建你的达人矩阵]
>
> 从抖音链接录入达人，AI 自动分析 6 维度匹配度
>
> [批量导入达人]  [搜索达人]

**禁忌**：
- ❌ "暂无达人"（无行动指引）
- ❌ "快来添加你的第一个达人吧！"（卖萌式口吻）
- ❌ 只有文字没有 CTA

---

## 9. 角色与权限的视觉映射

| 角色 | 看 | 写 | UI 表现 |
|---|---|---|---|
| 管理员 | 全部 | 全部 | 顶部显示"管理"入口 |
| 商务 | 全部 | 达人字段 | 写操作按钮**不禁用**但 hover 提示"你的权限可写" |
| 运营 | 全部 | 商品 | 同上 |
| AI 员工 | 自己创建 | 自己的达人 | 头像带 AI 视觉协议 |
| 真人 | 自己创建 | 自己的达人 | 头像无 AI 协议 |

> **禁示**：在按钮上无脑禁用 + 灰显。**更友好**的做法是"按钮可见但触发权限提示"。

---

## 10. 使用禁忌（Don'ts）

| 禁忌 | 原因 |
|---|---|
| ❌ 评级色用在非评级场景 | 颜色必须语义化，A/B/C/D 颜色只能用于评级 |
| ❌ 真人头像加 indigo 描边 | AI 协议被稀释 |
| ❌ 同一卡片挂 3+ 状态徽章 | 信息噪声，状态合并为一个 |
| ❌ 标题用 `text-overflow: ellipsis` 截断 | 截断是 bug 的伪装 |
| ❌ 时间和日期用 ISO 格式 `2026/09/07 14:06` | 应该用相对时间"今天 14:06" |
| ❌ 缺数据用 `-` 或 `0` | 应该是"未采集"+ 浅灰底 |
| ❌ 在 UI 上留 "未知"、"undefined"、"N/A" | 数据脏，要么补上要么删 |
| ❌ 用 emoji 替代状态色 | 状态有专门颜色，emoji 是二次信号 |

---

## 11. 落地建议

1. **CSS 变量文件 `tokens.css` 直接引入** `<link>` 到 `index.html` 顶部
2. **Tailwind 用户**：把 tokens 映射到 `tailwind.config.js` 的 theme.extend
3. **新增组件前先 grep**：确认 token 已存在再写新值
4. **改 token 先改这里**：不要在散落的 inline style 里改

---

## 附：版本历史

| 版本 | 日期 | 关键变化 |
|---|---|---|
| V3 | 2026-09-08 | 初版：AI 视觉协议 + 评级色 + 状态徽章 + 卡片规范 |
