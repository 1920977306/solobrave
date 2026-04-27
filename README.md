# SoloBrave

**你的 AI 员工团队管理平台**

让普通人拥有自己的 AI 团队。不需要懂代码，不需要懂 AI 原理。
只需要知道自己要做什么事，会"招人"，会"管人"。

SoloBrave = 你的一人公司操作系统
AI 员工们 = 你的专属团队

---

## 功能

| 功能 | 说明 |
|---|---|
| 招聘 AI 员工 | 7 个岗位模板，自定义性格、能力、说话风格 |
| 办公室仪表盘 | 团队状态、任务进度、最近动态一目了然 |
| 1对1 对话 | 流式输出，每个员工有独立人格和记忆 |
| 记忆系统 | AI 自动记住你的项目、偏好、决策 |
| 知识库 | 上传文档，AI 在对话中自动引用 |
| 任务系统 | 创建、派发、追踪、完成，完整生命周期 |
| 项目群聊 | 一句话，整个团队同时回复，各从专业角度 |
| 员工日报 | 每日自动生成工作汇报 |
| 首次引导 | 新用户配置 API Key 即可开始 |

---

## 快速开始

### 1. 获取 API Key

推荐使用 [智谱 AI](https://open.bigmodel.cn)（免费额度充足）

注册 → 创建 API Key → 复制

### 2. 运行

本项目是纯前端应用，无需安装任何依赖。

**方式一：本地运行**

用任意 HTTP 服务器打开项目目录：

```bash
# Python
python -m http.server 8080

# Node.js
npx serve .

# 或者直接用 VS Code 的 Live Server 插件
```

打开 `http://localhost:8080`

**方式二：直接打开**

双击 `index.html` 即可运行（部分浏览器可能限制本地文件的网络请求）

### 3. 配置

首次打开会弹出引导，粘贴你的 API Key 即可。

也可以在右上角 设置 中随时修改。

---

## 项目结构

```
solobrave/
├── index.html              # 入口页面
├── css/
│   ├── variables.css       # CSS 变量
│   ├── layout.css          # 布局样式
│   ├── components.css      # 组件样式
│   └── animations.css      # 动画
├── js/
│   ├── store.js            # 数据层（员工、任务、对话、知识库、记忆）
│   ├── ui-office.js        # 办公室仪表盘
│   ├── ui-chat.js          # 对话页面 + 知识库管理 + 项目群聊
│   ├── ui-task.js          # 任务看板
│   ├── ui-settings.js      # 设置页面
│   └── app.js              # 路由 + AI 调用 + 初始化
├── data/
│   ├── defaults/           # 默认配置
│   └── templates/          # 员工模板
├── assets/
│   ├── fonts/              # 字体
│   └── icons/              # 图标
└── README.md
```

---

## 技术栈

- 纯 HTML + CSS + JavaScript（零依赖）
- localStorage + IndexedDB（本地存储）
- 智谱 AI / OpenAI / Claude API（AI 能力）
- 无需打包工具，无需 Node.js，无需数据库

---

## 支持的 AI 服务商

| 服务商 | 模型 | 说明 |
|---|---|---|
| 智谱 AI | GLM-4 Flash | 推荐，免费额度充足 |
| OpenAI | GPT-4o / GPT-4o-mini | 需要海外网络 |
| Claude | Claude 3.5 | 需要海外网络 |

---

## 数据说明

所有数据存储在浏览器本地（localStorage + IndexedDB）：

- 员工配置、任务、对话记录存在 localStorage
- 知识库文档存在 IndexedDB
- 不上传到任何服务器
- 清除浏览器数据会丢失所有内容
- 建议定期在 设置 中导出备份

---

## License

MIT
