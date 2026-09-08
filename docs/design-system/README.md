# SoloBrave 设计系统

> 跨项目基础库 · 适配多项目矩阵（V1 = SoloBrave 抖音团长工具）

## 目录

- [架构总览](#架构总览)
- [核心原则](#核心原则)
- [文件结构](#文件结构)
- [快速开始](#快速开始)
- [新增项目](#新增项目)
- [新增组件](#新增组件)
- [版本历史](#版本历史)

---

## 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  Project HTML                                            │
│  <html data-project="solobrave">                        │
└─────────────────┬───────────────────────────────────────┘
                  │
       ┌──────────┼──────────┬──────────────┐
       ▼          ▼          ▼              ▼
  core/        core/      themes/         domain/
  tokens.css   components  solobrave.css   glossary.md
                          ↑                ↑
                  品牌色覆盖            领域术语
                  (项目级)              (跨项目)
```

**核心约束**：
- `core/` 内**不含任何品牌色**——所有品牌变量都从主题层覆盖
- `core/` 不写"项目专属组件"——比如"团长徽章"放 themes/，不放 core/
- 主题层只覆盖 3-5 个变量——新项目 = 复制主题文件 + 改 3 个颜色

---

## 核心原则

| 原则 | 含义 |
|---|---|
| **AI 优先视觉** | AI 员工必须有视觉签名（indigo 光晕 / 徽章），与真人员工一眼可分 |
| **评级即签名** | A/B/C/D 评级必须能在卡片任何位置被识别（左色条 = 头像底色） |
| **数据通道抽象** | 截图 / API / 手动表单走同一个"AI 复核确认"流 |
| **会员即定价** | AI 员工数 = 定价轴，UI 必须有 plan / quota / upgrade / lock |
| **新人团长视角** | 所有空状态 / onboarding / CTA 按"想成为团长的人"写 |
| **不露内部 jargon** | OpenClaw、延迟 ms 之类内部技术词不进外部产品 UI |

---

## 文件结构

```
docs/design-system/
├── README.md                  ← 本文件
├── core/
│   ├── tokens.css             ← CSS 变量（无品牌色）
│   ├── components.css         ← 通用组件类
│   ├── design-tokens.md       ← 完整规范文档（17 节）
│   └── preview.html           ← 可视化预览（14 个区块）
├── themes/
│   └── solobrave.css          ← SoloBrave 品牌覆盖
├── domain/
│   └── glossary.md            ← 术语词典（中英 + 后端字段）
└── archive/
    └── V1/                    ← V1 文件备份（仅作参考）
        ├── design-tokens.v1.md
        ├── tokens.v1.css
        └── preview.v1.html
```

---

## 快速开始

### 在产品 HTML 引入

```html
<!DOCTYPE html>
<html lang="zh-CN" data-project="solobrave">
<head>
  <link rel="stylesheet" href="docs/design-system/core/tokens.css">
  <link rel="stylesheet" href="docs/design-system/core/components.css">
  <link rel="stylesheet" href="docs/design-system/themes/solobrave.css">
</head>
<body>
  <!-- 使用组件 -->
  <div class="talent-card" data-rating="A" data-status="active">
    <div class="avatar">头</div>
    <div class="meta">
      <div class="name">达人姓名</div>
      <div class="chips">
        <span class="chip chip-platform" data-color="#FE2C55">抖音</span>
        <span class="chip">美妆</span>
      </div>
    </div>
    ...
  </div>
</body>
</html>
```

### 组件清单（core/components.css）

| 组件类 | 用途 |
|---|---|
| `.talent-card` | 达人/创作者卡片 |
| `.talent-card.ai-employee` | AI 员工卡（叠加类） |
| `.badge-status` | 合作状态徽章 |
| `.badge-plan` | 会员计划徽章 |
| `.chip` / `.chip-platform` | 平台/类目 chip |
| `.btn` + 变体 | 按钮（primary / secondary / ai / ghost / danger） |
| `.prompt-chip` | AI 入口示例 |
| `.quota-meter` | AI 用量配额条 |
| `.lock-feature` | 锁定态（升级提示） |
| `.upgrade-cta` | 升级引导卡 |
| `.screenshot-drop` | 截图上传 drop zone |
| `.screenshot-progress` | 截图处理进度 |
| `.fab` | 悬浮操作按钮 |
| `.onboarding-step` | 引导步骤 |
| `.field-confidence` | AI 识别置信度标识 |
| `.field-ai-suggested` | AI 推荐值（虚线框） |
| `.data-missing` | 缺数据占位 |
| `.ai-pulse-dot` | AI 在线脉冲点 |

---

## 新增项目

### 步骤（5 分钟）

1. **复制主题文件**：
   ```bash
   cp themes/solobrave.css themes/xiaohongshu.css
   ```

2. **改 3-5 个变量**：
   ```css
   :root,
   :root[data-project="xiaohongshu"] {
     --theme-primary: #FF2442;        /* 小红书红 */
     --theme-primary-hover: #E62038;
     --theme-primary-soft: rgba(255, 36, 66, 0.10);
     --project-name: "SoloBrave · 小红书";
     --project-tagline: "...";
   }
   ```

3. **项目 HTML 加 data-project**：
   ```html
   <html data-project="xiaohongshu">
   ```

4. **（可选）添加项目专属组件**：
   - 在 themes/xiaohongshu.css 末尾追加
   - 命名加项目前缀（`.xiaohongshu-*`）避免冲突

**core 0 修改**。

---

## 新增组件

**铁律**：先看 core 里有没有，**没有再写**。

### 通用组件（应进 core）

判断标准：跨项目都用得到。

- 示例：`data-source` 多通道数据展示（如果未来要加 API 通道）

### 项目专属组件（应进 themes）

判断标准：只有一个项目用。

- 示例：`.badge-role-affiliate`（仅 SoloBrave 用）

### 业务组件（应进项目仓库，不进设计系统）

判断标准：和具体业务逻辑强耦合。

- 示例：达人详情 6 Tab 面板（绑 6 个 tab 的具体内容）

---

## 待办 / 已知问题

- [ ] 移动端响应式（当前仅桌面，移动端 V2 后做）
- [ ] 深色模式（V3 规划）
- [ ] Tailwind preset（让 core 变量可注入 Tailwind）
- [ ] Storybook 集成（V3 规划）
- [ ] 截图识别结果复核 UI（V2.5 细化）
- [ ] AI 员工详细页（V2.5 细化）
- [ ] 团队组织架构可视化（V3，需要时做）

---

## 版本历史

| 版本 | 日期 | 关键变化 |
|---|---|---|
| V1 | 2026-09-08 | 初版：AI 视觉协议 + 评级色 + 卡片规范 |
| V2 | 2026-09-08 | 架构拆分（core/themes/domain）；加会员制 UI；加截图录入 + 置信度/新鲜度；加 onboarding；术语词典；多项目换皮架构 |

---

## 维护

- **Owner**：设计组
- **变更流程**：改 token 先改这里，**不**在散落的 inline style 里改
- **同步**：新组件进 core 必须配 `design-tokens.md` 文档
- **审阅**：任何改动前先看本 README 和 design-tokens.md
