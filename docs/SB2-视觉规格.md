# SoloBrave 2.0 视觉规格书 — 实现对稿硬指标

> 基准：原型 `/Users/qichen/Documents/kimi/tasks/2026-09-30/15-47-22-6b6fc1a8/solobrave-redesign/index.html`
> 原则：新 UI 一律 `--sb2-*` token（见 `index.html` `<style id="sb2-shell">` 的 `:root` 和 `[data-sb2-theme="dark"]`），**禁止自造色板**
> 验收：每批交付附真机截图，对照本规格 + 原型逐屏过

## 1. 色板铁律

| 用途 | 必须用 | 禁用（反例） |
|---|---|---|
| 背景/表面 | `--sb2-bg / --sb2-s1 / --sb2-s2 / --sb2-s3` | `#F8FAFC` 等 Tailwind slate 灰 |
| 正文/次要/弱 | `--sb2-t1 / --sb2-t2 / --sb2-t3` | `#1F2937 #6B7280 #94A3B8` |
| 边框 | `--sb2-border / --sb2-border-strong` | `#E5E7EB` |
| 成功/警告/危险 | `--sb2-success / --sb2-warning / --sb2-danger`（含 `-soft` 底） | `#10B981 #B45309 #B91C1C` 直写 |
| 品牌橙（主行动/用户气泡/品牌高亮） | `--sb2-brand`（hover `--sb2-brand-strong`，底 `--sb2-brand-soft`） | 自调蓝紫渐变 |
| 数据/链接 accent | `--sb2-accent` | `#4F6BFF` 直写 |

**角色分工**：用户自己发的消息/主 CTA = 品牌橙；AI/系统内容 = 白底卡片 + 中性色；链接与数据强调 = accent 蓝。橙是"人"，蓝是"数据"。

## 2. 组件态标准

- **卡片**：`1px --sb2-border` 边框 + `--sb2-sh1` 影；hover 浮起 `translateY(-2px)` + `--sb2-sh2` + 边框升 `--sb2-border-strong`，过渡 `.2s`
- **按钮**：三态齐全（默认/hover/active）；主行动 `--sb2-ink` 底（见壳层 `.sb2-btn`），品牌动作 `--sb2-brand`，次动作 ghost 描边
- **tag**：语义五类（brand/accent/success/warning/gold），圆角全圆，11px/500，配 `-soft` 底色——见壳层 `.sb2-tag*`
- **数字**：金额/粉丝/GMV 等一律 `font-variant-numeric: tabular-nums` + 等宽族

## 3. 布局与密度

- 气泡区：最大宽度 78%，人与 AI 头像 28px，时间戳 11px 弱色独立一行
- 发送框：ghost 边框容器 + 内嵌透明 input，附件按钮 36px ghost 圆钮
- 信息层级三层封顶：主标题（13-14px/600）→ 副信息（12px 弱色）→ meta（11px 弱色等宽）
- 模块容器统一 `--sb2-r-lg`(14px) 圆角，间距走 4pt 网格（4/8/12/16/24）

## 4. 交活 checklist（每批必过）

- [ ] `grep` 新 style 块：除品牌渐变/头像色外**零硬编码 hex**（验收线：中性色/语义色 0 处直写）
- [ ] 气泡/按钮/tag/数字四角色色与原型一致（截图对稿）
- [ ] hover/过渡动效与壳层一致
- [ ] console 0 error
