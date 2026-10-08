# 员工详情页 / 列表设计语言规范（r82–r83b）

> 2026-10-08 由 r82（详情页重设计）→ r83（灵感三落地）→ r83b（列表推广）三个迭代沉淀。
> 适用范围：员工详情页六个 tab、员工列表行、以及以后所有同类管理界面。
> 新增页面/组件必须遵守本规范，防止旧样式回潮。

## 四条硬规则

### 1. 状态一律「圆点语义色 + 细边胶囊」，禁止 emoji、禁止彩色填充 badge

- 头像角标：`.status-dot`（online 绿 / busy 橙 / offline 灰 / thinking 蓝，圆点 6–11px）。
- 文字状态：`.emp-status-pill`（1px `var(--sb2-border)` 边 + 999 圆角 + `var(--sb2-s1)` 浅底 + 圆点 + 文字）。
  紧凑版 `.emp-status-pill-sm`（列表/小空间，11px）。
- 文案剥离 emoji：填充 JS 用 `_stText.replace(/^\S+\s/, '')` 剥掉 `getStatusText` 返回的 🟢🟠 前缀，
  胶囊自带圆点，二者不得并存。
- 浅底上的状态字必须够深：tint 底 + 深色字（如沟通中 = 深琥珀 #B8860B 字 + 黄 tint 底 + 发丝边），
  禁止黄字压黄底这类不可读组合。

### 2. 分割线用发丝线，层次靠留白和字重，不靠阴影

- 行/卡分隔：`0.5px–1px var(--sb2-border)`。
- 禁止多层 `box-shadow` 堆叠造层次；阴影只用于悬浮交互态（hover、弹层）。

### 3. 数据/时间用等宽字体，标签小字灰、内容常规

- 日期/ID/数值：`ui-monospace / 'SF Mono'`（`.ai-emp-work-time`、`.emp-info-value` 已就位）。
- 信息栅格：标签列定宽（如 88px）+ 12px 小字灰（`var(--sb2-t3)`），内容列等宽字体左对齐（3+9 grid 手法）。
- 日期前缀格式：`MM/DD HH:mm`。

### 4. 卡片语言：灰底标题行 + 白卡体，圆角阶梯统一

- 卡壳：`.emp-sk-card`（12 圆角 + 1px 边）→ `.emp-sk-head`（`var(--sb2-s2)` 灰底标题行，13px/600 + 右侧 action/hint）→ `.emp-sk-body`（14/16 padding）。
- 圆角阶梯：小件 8 / 卡片 12 / 胶囊 999，全局不得混用第四种。
- hint 口径必须真实（如工作记录「近 30 天 · 最近 20 条」对应 API `days=30&limit=20`），禁止装饰性文案。
- 按钮语言：紧凑 `.emp-action-btn`（< 200px 宽），禁止 `width:100%` 长按钮（r82 已全站清零，禁止回潮）。

## 已落地位置清单

| 位置 | 规则 | commit |
|---|---|---|
| 详情页 tabs 栏铺满 + stats 条仅基础 tab 显示 | 1/2 | 3e51a20 |
| 记忆 tab 分段控件 + 横幅色边类 | 2 | 3e51a20 |
| 连接/技能/头像/工作记录 tab 卡片化（emp-sk-card） | 4 | 3e51a20 / fdb2b63 / d5da9ee |
| 身份卡右端状态点胶囊（empStatusPill） | 1 | 13d248f |
| 基础信息 3+9 栅格 + 0.5px 发丝线 | 2/3 | 13d248f |
| 工作记录行：等宽日期前缀 + `.ai-emp-work-badge` tint 发丝胶囊 | 1/3 | 13d248f |
| 员工列表行：角标圆点 + 创建者 `.emp-creator-tag` 胶囊，消息行无 emoji | 1 | 4b772d3 |

## 关键实现备忘（踩过的坑）

1. **员工列表真实渲染器是 `js/inline-07.js` 的 monkey patch**（覆盖 inline-03 的 `renderEmployeeItem`）。
   改列表行样式必须改 inline-07；inline-03 原函数保持同语言只作兜底。
2. **改 `index.html` 里的 JS 引用必须 bump `?v=` 钉扎**，否则浏览器吃旧缓存。
3. 头像 tab 底色选中环走 class（`.color-option.selected`），不是内联 `boxShadow`——
   内联样式会盖掉 class，切 tab 回显（inline-03 `switchEmpDetailTab`）与点击回调必须同一机制。
4. 详情页头图区已删除（r82 批注②），名字/角色/状态由基础 tab 身份卡承担；JS 对
   `empDetailName/empDetailRole/empDetailStatus` 的填充是空值守卫，不要删守卫。
