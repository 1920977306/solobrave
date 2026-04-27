# SoloBrave - AI 原生 IM 协作系统

> 一个让 AI 真正成为「一等公民」的智能协作平台

![Version](https://img.shields.io/badge/version-1.0.0-blue)
![Status](https://img.shields.io/badge/status-production-green)
![Platform](https://img.shields.io/badge/platform-Web-FF6B35)

---

## 🎯 核心理念

**不再是寄生在人类 IM 里的机器人！**

SoloBrave 让 AI 拥有自己的身份、头像、岗位、技能，可以：
- 互相 `@` 协作
- 自主推进任务
- 智能管理上下文
- 实时监控状态

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      SoloBrave 系统                         │
├─────────────────┬─────────────────┬─────────────────────────┤
│    IM 聊天系统   │    办公室后台    │       文档库          │
│   (brave-office)│ (lobster-office)│   (lobster-office)    │
├─────────────────┴─────────────────┴─────────────────────────┤
│                      核心能力                              │
│  • 上下文智能管理  • 文档自动关联  • AI 上下文感知         │
│  • 任务督促机制    • 实时状态监控  • 知识沉淀闭环          │
└─────────────────────────────────────────────────────────────┘
```

### 三大核心模块

| 模块 | 文件 | 功能 |
|------|------|------|
| **IM 聊天系统** | `brave-office.html` | 消息、@提及、斜杠命令、文档上下文感知 |
| **办公室后台** | `lobster_office.html` | 员工管理、技能配置、统计卡片、任务看板 |
| **文档库** | `lobster_office.html` | CRUD、Markdown编辑、版本管理、项目关联 |

---

## ✨ 功能清单

### IM 聊天系统

- [x] **消息系统** - 支持文本、@提及、斜杠命令
- [x] **上下文管理** - 压缩/摘要/归档/重置四档控制
- [x] **文档上下文感知** - AI 自动读取相关文档
- [x] **群公告** - 文档链接列表，项目信息聚合
- [x] **项目组协作** - 多成员群聊，任务分配
- [x] **督促机制** - 智能检查、催促升级、浏览器通知

### 办公室后台

- [x] **员工管理** - 添加/编辑/删除，状态监控
- [x] **快速配置模板** - 前端/后端/产品/设计/测试/运维
- [x] **技能系统** - 技能标签、熟练度星级
- [x] **工作状态细化** - thinking/reading/coding/waiting 等
- [x] **休闲区** - 离线员工管理、一键唤醒
- [x] **统计卡片** - 项目/任务/成员/消息数量

### 文档库

- [x] **文档 CRUD** - 创建、编辑、删除、归档
- [x] **Markdown 编辑器** - 实时预览、工具栏快捷操作
- [x] **版本管理** - 历史版本查看、恢复
- [x] **文档模板** - PRD、设计规范、API 文档、会议纪要
- [x] **项目关联** - 文档与项目组绑定
- [x] **员工关联** - 文档与员工绑定
- [x] **导出功能** - 一键导出 .md 文件

---

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| 前端框架 | 原生 HTML/CSS/JavaScript |
| 数据存储 | localStorage |
| UI 风格 | Apple Human Interface Guidelines |
| 图标 | Emoji + SVG |
| 动画 | CSS Transitions + Keyframes |

---

## 📁 文件结构

```
mission-control/
├── brave-office.html       # 主应用（IM聊天 + 办公室）
├── lobster_office.html     # 龙虾办公室（管理后台 + 文档库）
├── frontend/              # JS 模块目录
│   ├── core.js           # 框架核心
│   ├── store.js          # 状态管理
│   ├── escape.js         # XSS 防护
│   ├── memory.js         # 记忆系统
│   ├── knowledge-base.js # 知识库
│   ├── skills.js         # 技能系统
│   ├── channels.js       # 渠道抽象
│   ├── prompt-builder.js # 提示生成
│   ├── discover.js       # 发现模块
│   ├── features.js       # 功能系统
│   ├── skills-panel.js   # 技能面板
│   ├── employee_models.js # 员工模型
│   ├── ai-chat.js        # AI 聊天
│   ├── message-search.js # 消息搜索
│   ├── file-upload.js    # 文件上传
│   └── avatar-picker.js  # 头像选择
├── 代码文件列表/          # 代码快照备份
└── halo-research/        # 参考项目截图
```

---

## 🚀 快速开始

### 方式一：直接打开

```bash
# 在浏览器中打开
brave-office.html      # 主应用
lobster_office.html   # 管理后台
```

### 方式二：本地服务器

```bash
# 使用 Python 启动
python -m http.server 8080

# 或使用 Node.js
npx serve .

# 访问
http://localhost:8080/brave-office.html
http://localhost:8080/lobster_office.html
```

---

## 📖 使用指南

### 创建第一个员工

1. 打开 `lobster_office.html`
2. 进入「👥 员工」Tab
3. 点击「➕ 添加员工」
4. 或使用「⚡ 快速配置模板」一键创建

### 创建项目组

1. 在 `brave-office.html` 中
2. 点击侧边栏「➕ 新建」
3. 输入项目名称和成员
4. 项目组创建成功

### 发布群公告

1. 选择一个项目组
2. 点击顶部「📢 发布公告」
3. 填写标题和内容
4. 添加项目文档链接
5. 选择公告类型（普通/重要/紧急）

### 文档协作流程

```
创建文档 → 关联项目 → 发布群公告 → AI 自动感知
    ↓
分享文档给 AI → AI 读取内容 → 智能协作
```

### 上下文管理

| 命令 | 功能 |
|------|------|
| `/压缩` | 保留关键信息，减少 Token |
| `/总结` | 生成对话摘要 |
| `/归档` | 保存当前对话到归档 |
| `/重置` | 清空上下文，重新开始 |

---

## 🎨 设计参考

- **Apple Human Interface Guidelines** - 整体风格
- **Halo 后台** - 统计卡片、快捷入口、通知面板
- **SoybeanAdmin** - 主题系统、过渡动画
- **RuoYi-Vue-Pro** - 模块化架构

---

## 🔧 核心函数参考

### 文档上下文

```javascript
getDocContextForChat()      // 获取当前对话的完整文档上下文
getAIContextMessage(projId) // 获取项目关联文档内容摘要
getEmployeeDocContext(empId) // 获取员工关联文档摘要
shareDocToChat(docId)       // 手动分享文档给 AI
```

### 上下文管理

```javascript
compressContext()    // 压缩上下文
generateSummary()    // 生成摘要
archiveCurrent()     // 归档当前对话
resetContext()       // 重置对话
```

### 督促机制

```javascript
checkReminders()           // 检查是否需要督促
generateReminderMsg()      // 生成催促消息
showReminder()            // 显示催促提醒
startReminderCheck()      // 启动督促检查
```

---

## 📊 数据结构

### 员工

```javascript
{
  id: 'emp_xxx',
  name: '小龙虾',
  role: '前端工程师',
  avatar: '🦞',
  bg: '#FF6B35',
  status: 'online',      // online/busy/idle/offline/thinking/reading/coding/waiting
  model: 'GPT-4o',
  skills: [{ name: 'React', emoji: '⚛️', level: 5 }],
  linkedDocs: ['doc1', 'doc2']
}
```

### 文档

```javascript
{
  id: 'doc_xxx',
  name: 'PRD - 前端重构',
  type: 'project',       // project/skill/template/archive
  content: '# Markdown 内容',
  tags: ['前端', '重构'],
  projectId: 'proj_xxx',
  employeeId: 'emp_xxx',
  versions: [{ version: '1.0', content: '...', date: '2024-01-15' }]
}
```

---

## 🗺️ 数据流转图

### AI 协作

- [x] **真实 AI 集成** - 支持 CoPaw Agent / OpenAI API / 自定义 API
- [x] **AI 配置面板** - 一键切换 AI 模式流程

```
用户消息 → sendMsg()
    ↓
┌─────────────────┐
│ 自动检测文档上下文 │
│ • 项目关联文档    │
│ • 员工关联文档    │
└─────────────────┘
    ↓
┌─────────────────┐
│ 构建 Prompt     │
│ • 文档内容摘要   │
│ • 历史对话     │
│ • 员工信息     │
└─────────────────┘
    ↓
┌─────────────────┐
│ AI 生成响应     │
│ • 返回消息     │
│ • 更新状态     │
│ • 记录记忆     │
└─────────────────┘
    ↓
显示消息 + 📚 已读取文档提示
```

### 上下文管理流程

```
上下文阈值触发
    ↓
压缩(Compress) → 保留关键信息
    ↓
继续增长
    ↓
摘要(Summarize) → 生成摘要
    ↓
继续增长
    ↓
归档(Archive) → 保存到归档
    ↓
重置(Reset) → 清空重新开始
```

---

## 📝 版本历史

### v1.0.0 (2024-01)
- ✅ IM 聊天系统完整功能
- ✅ 办公室后台管理
- ✅ 文档库 CRUD + Markdown 编辑
- ✅ 上下文四档管理
- ✅ 文档上下文 AI 感知
- ✅ 群公告文档链接
- ✅ 督促机制
- ✅ 快速配置模板
- ✅ 工作状态细化

---

## 🙏 致谢

- **Apple** - Human Interface Guidelines 设计参考
- **Halo** - 后台管理设计参考
- **SoybeanAdmin** - 主题系统参考
- **RuoYi** - 模块化架构参考

---

<p align="center">
  <strong>SoloBrave - 让 AI 真正成为一等公民</strong>
  <br>
  Made with ❤️ by SoloBrave Team
</p>