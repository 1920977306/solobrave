# SoloBrave V2 架构设计文档

> 版本：V2.0  
> 日期：2024年5月  
> 状态：初稿

---

## 目录

1. [概述](#1-概述)
2. [角色模板系统](#2-角色模板系统)
3. [权限体系重构](#3-权限体系重构)
4. [账号管理面板](#4-账号管理面板)
5. [数据看板](#5-数据看板)
6. [数据模型设计](#6-数据模型设计)
7. [API设计](#7-api设计)
8. [前端改动点](#8-前端改动点)
9. [实施路线](#9-实施路线)

---

## 1. 概述

### 1.1 系统定位

SoloBrave V2 是 AI 版飞书系统的重大架构升级版本，核心目标是实现**角色预打包**和**团队权限管理**。管理员预先配置好角色模板（如"前端工程师"、"产品经理"），员工账号创建时自动继承完整配置，实现**开箱即用**的 AI 助理体验。

### 1.2 核心升级点

| 升级项 | V1（现状） | V2（目标） |
|--------|----------|-----------|
| 角色配置 | 员工自行配置 Agent | 管理员预打包模板 |
| 权限层级 | admin / employee 两级 | admin / leader / employee 三级 |
| 权限粒度 | 按个人创建者 | 按小组（team）划分 |
| 账号管理 | prompt() 弹窗 | 完整管理面板 |
| 数据统计 | 无 | 团队/个人数据看板 |

### 1.3 系统架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                         SoloBrave V2 前端                        │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │侧栏导航 │ │角色模板 │ │账号管理 │ │数据看板 │ │聊天界面 │   │
│  │         │ │管理页   │ │面板     │ │         │ │         │   │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │
└───────┼───────────┼───────────┼───────────┼───────────┼─────────┘
        │           │           │           │           │
        ▼           ▼           ▼           ▼           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      SoloBrave V2 后端 API                       │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    权限中间件层                          │    │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐                 │    │
│  │  │ Admin   │  │ Leader  │  │Employee │                 │    │
│  │  │ Middleware│ │ Middleware│ │Middleware│                │    │
│  │  └─────────┘  └─────────┘  └─────────┘                 │    │
│  └─────────────────────────────────────────────────────────┘    │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │用户API  │ │团队API  │ │模板API  │ │龙虾API  │ │看板API  │   │
│  └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘   │
└───────┼───────────┼───────────┼───────────┼───────────┼─────────┘
        │           │           │           │           │
        ▼           ▼           ▼           ▼           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        数据存储层                                │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐   │
│  │users.json│ │teams.json│ │role_   │ │agents.  │ │chats/   │   │
│  │(扩展)   │ │(新增)   │ │templates│ │json     │ │(消息)   │   │
│  │         │ │         │ │.json    │ │(改造)   │ │         │   │
│  └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 角色模板系统

### 2.1 设计目标

角色模板是 V2 版本的**核心创新**。管理员预先配置完整的角色定义，客户（员工账号）创建时从模板选择，系统自动完成：
- 技能安装
- 灵魂配置（SOUL.md / IDENTITY.md）
- 知识库初始化
- 记忆种子注入

员工**无需任何配置**，直接开始使用。

### 2.2 模板数据结构

**文件路径**：`~/.solobrave-data/role_templates.json`

```json
{
  "role_templates": [
    {
      "id": "tpl_frontend_engineer",
      "name": "前端工程师",
      "description": "擅长 React、Vue、TypeScript 等前端技术栈",
      "icon": "code",
      "color": "#007AFF",
      "category": "技术",
      "is_system": true,
      "is_active": true,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z",
      "config": {
        "skills": [
          {
            "skill_id": "skill_frontend_debug",
            "skill_name": "前端调试",
            "version": "1.0.0",
            "config": {
              "languages": ["JavaScript", "TypeScript", "CSS"],
              "frameworks": ["React", "Vue", "Angular"]
            }
          },
          {
            "skill_id": "skill_code_review",
            "skill_name": "代码审查",
            "version": "1.0.0",
            "config": {
              "check_list": ["安全性", "性能", "可维护性"]
            }
          }
        ],
        "soul": {
          "SOUL.md": "# 前端工程师灵魂\n\n## 核心价值观\n- 代码质量优先\n- 用户体验至上\n- 持续学习新技术\n\n## 沟通风格\n- 技术精准\n- 善于用代码示例说明\n- 注重可执行性",
          "IDENTITY.md": "# 前端工程师身份\n\n## 我是谁\n我是一名资深前端工程师，专注于现代前端技术栈。\n\n## 我的专长\n- React / Vue / Angular\n- TypeScript\n- 性能优化\n- 响应式设计\n\n## 我的工作方式\n收到问题时会先分析技术方案，再给出具体代码实现。"
        },
        "knowledge": [
          {
            "id": "kb_frontend_best_practices",
            "title": "前端最佳实践",
            "content": "# 前端最佳实践\n\n## 代码规范\n- 使用 ESLint 规范代码\n- 遵循组件设计原则\n...",
            "type": "markdown"
          }
        ],
        "memory_seed": {
          "initial_memory": [
            "我是一名专业的前端工程师",
            "我擅长使用 React 和 TypeScript",
            "我注重代码质量和用户体验"
          ],
          "context_templates": [
            "当遇到{技术问题}时，我会{分析思路}",
            "代码审查时，我会关注{检查点}"
          ]
        },
        "default_settings": {
          "temperature": 0.7,
          "max_tokens": 2000,
          "personality": "professional"
        }
      },
      "permissions": {
        "allow_chat": true,
        "allow_file_upload": true,
        "allow_code_execution": false,
        "max_agents": 3
      }
    }
  ]
}
```

### 2.3 模板管理 API

#### 2.3.1 获取模板列表

```
GET /api/v2/role-templates
```

**权限**：仅 admin

**Query 参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| category | string | 按分类筛选（可选） |
| is_active | boolean | 是否启用（可选） |
| page | int | 页码（默认1） |
| page_size | int | 每页数量（默认20） |

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 15,
    "page": 1,
    "page_size": 20,
    "list": [
      {
        "id": "tpl_frontend_engineer",
        "name": "前端工程师",
        "description": "擅长 React、Vue、TypeScript",
        "icon": "code",
        "color": "#007AFF",
        "category": "技术",
        "skills_count": 5,
        "agents_count": 23,
        "is_active": true
      }
    ]
  }
}
```

#### 2.3.2 获取模板详情

```
GET /api/v2/role-templates/{template_id}
```

**权限**：仅 admin

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "tpl_frontend_engineer",
    "name": "前端工程师",
    "description": "擅长 React、Vue、TypeScript 等前端技术栈",
    "icon": "code",
    "color": "#007AFF",
    "category": "技术",
    "is_system": true,
    "is_active": true,
    "config": { ... },
    "permissions": { ... },
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-05-01T00:00:00Z",
    "stats": {
      "total_agents": 23,
      "active_agents": 20
    }
  }
}
```

#### 2.3.3 创建模板

```
POST /api/v2/role-templates
```

**权限**：仅 admin

**请求体**：
```json
{
  "name": "产品经理",
  "description": "负责产品规划、需求管理和团队协作",
  "icon": "briefcase",
  "color": "#FF9500",
  "category": "产品",
  "config": {
    "skills": [...],
    "soul": {...},
    "knowledge": [...],
    "memory_seed": {...},
    "default_settings": {...}
  },
  "permissions": {
    "allow_chat": true,
    "allow_file_upload": true,
    "allow_code_execution": false,
    "max_agents": 5
  }
}
```

#### 2.3.4 更新模板

```
PUT /api/v2/role-templates/{template_id}
```

**权限**：仅 admin

**说明**：`is_system: true` 的模板不允许删除和修改核心配置

#### 2.3.5 删除模板

```
DELETE /api/v2/role-templates/{template_id}
```

**权限**：仅 admin

**约束**：
- 系统模板（`is_system: true`）不可删除
- 已有员工使用的模板不可删除（返回 403）

**响应**：
```json
{
  "code": 0,
  "message": "模板删除成功",
  "data": {
    "affected_users": 0
  }
}
```

#### 2.3.6 模板预览（员工视角）

```
GET /api/v2/role-templates/{template_id}/preview
```

**权限**：admin / 即将创建的员工

**响应**：展示模板的皮肤配置（技能名称、灵魂概要），不包含技术细节

### 2.4 员工创建时的模板应用

**流程**：

```
管理员选择模板 → 系统复制模板配置 → 创建员工账号 → 分配默认龙虾
     ↓
┌─────────────────────────────────────────┐
│  1. 创建 Agent 记录                      │
│     - 从模板复制 SOUL.md / IDENTITY.md   │
│     - 安装模板指定的技能                  │
│     - 导入知识库文档                      │
│     - 注入记忆种子                        │
│  2. 设置权限限制                          │
│     - 应用模板的 permissions             │
│  3. 初始化状态                            │
│     - status: "ready"                   │
│     - first_login: true                 │
└─────────────────────────────────────────┘
     ↓
  员工登录 → 开始使用
```

**API**：

```
POST /api/v2/users
```

```json
{
  "username": "zhangsan",
  "email": "zhangsan@company.com",
  "password": "********",
  "role_template_id": "tpl_frontend_engineer",
  "team_ids": ["team_tech"],
  "nickname": "张三",
  "create_default_agent": true
}
```

### 2.5 前端组件设计

#### 2.5.1 模板管理页（Admin）

**路径**：`/#/admin/role-templates`

**组件结构**：

```
┌─────────────────────────────────────────────────────────────┐
│  [Apple Style Navigation Bar]                               │
│  ← 返回   角色模板管理                    [+ 新建模板]       │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────┐   │
│  │ [Search...]  分类: [全部 ▼]  状态: [全部 ▼]          │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ [📦] 前端工程师                         [编辑] [删除] │   │
│  │     擅长 React、Vue、TypeScript                      │   │
│  │     技能: 5 个  ·  使用中: 23 个员工                  │   │
│  │     [系统模板]                                       │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ [💼] 产品经理                         [编辑] [删除] │   │
│  │     负责产品规划、需求管理和团队协作                  │   │
│  │     技能: 3 个  ·  使用中: 12 个员工                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ [+ 新建角色模板]                                     │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

**主要组件**：

| 组件名 | 文件 | 说明 |
|--------|------|------|
| `RoleTemplateList` | `components/RoleTemplateList.js` | 模板列表主组件 |
| `RoleTemplateCard` | `components/RoleTemplateCard.js` | 单个模板卡片 |
| `RoleTemplateEditor` | `components/RoleTemplateEditor.js` | 模板编辑表单 |
| `TemplateSkillSelector` | `components/TemplateSkillSelector.js` | 技能选择器 |
| `TemplateSoulEditor` | `components/TemplateSoulEditor.js` | 灵魂配置编辑器 |
| `TemplatePreview` | `components/TemplatePreview.js` | 模板预览弹窗 |

#### 2.5.2 模板编辑表单

**技能选择**：

```
┌─────────────────────────────────────────────────────────────┐
│  选择技能                                                   │
├─────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────┐  │
│  │ [🔍 搜索技能...]                                       │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  推荐技能                                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ [✓] 前端调试                                          │   │
│  │     JavaScript, TypeScript, CSS 调试                 │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │ [✓] 代码审查                                          │   │
│  │     代码质量和安全检查                                  │   │
│  ├─────────────────────────────────────────────────────┤   │
│  │ [ ] 架构设计                                          │   │
│  │     系统架构和方案设计                                  │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  已选择: 2 个                                                │
│                                                             │
│           [取消]                    [保存]                  │
└─────────────────────────────────────────────────────────────┘
```

**灵魂配置**：

```
┌─────────────────────────────────────────────────────────────┐
│  灵魂配置                                                   │
├─────────────────────────────────────────────────────────────┤
│  SOUL.md                                                   │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ # 前端工程师灵魂                                       │  │
│  │                                                       │  │
│  │ ## 核心价值观                                         │  │
│  │ - 代码质量优先                                        │  │
│  │ - 用户体验至上                                        │  │
│  │ ## 沟通风格                                           │  │
│  │ - 技术精准                                            │  │
│  │ - 善于用代码示例说明                                   │  │
│  └───────────────────────────────────────────────────────┘  │
│  字数: 128                                                  │
│                                                             │
│  IDENTITY.md                                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ # 前端工程师身份                                       │  │
│  │                                                       │  │
│  │ ## 我是谁                                             │  │
│  │ 我是一名资深前端工程师                                  │  │
│  └───────────────────────────────────────────────────────┘  │
│  字数: 32                                                   │
│                                                             │
│           [取消]                    [保存]                  │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 权限体系重构

### 3.1 设计目标

将权限体系从简单的 `admin/employee` 两级扩展为 **admin / leader / employee** 三级，并引入**小组（Team）** 作为权限划分的基本单位。

### 3.2 权限模型

#### 3.2.1 三级角色定义

| 角色 | 说明 | 权限范围 |
|------|------|----------|
| **admin** | 系统管理员 | 全部数据 + 系统配置 |
| **leader** | 组长 | 本组及子组成员的龙虾和数据 |
| **employee** | 普通员工 | 仅自己的龙虾和数据 |

#### 3.2.2 权限矩阵

| 功能 | admin | leader | employee |
|------|-------|--------|----------|
| 系统配置 | ✅ 完全控制 | ❌ | ❌ |
| 用户管理 | ✅ 完全控制 | ❌（只能查看） | ❌ |
| 小组管理 | ✅ 完全控制 | ❌（只能查看） | ❌ |
| 角色模板管理 | ✅ 完全控制 | ❌ | ❌ |
| 成员小龙虾 | ✅ 完全控制 | ✅ 本组及子组 | ❌ |
| 消息查看 | ✅ 全部 | ✅ 本组及子组 | ✅ 仅自己 |
| 数据看板 | ✅ 全局 | ✅ 本组及子组 | ✅ 仅自己 |
| 技能管理 | ✅ 完全控制 | ❌ | ❌ |
| 知识库管理 | ✅ 完全控制 | ❌ | ❌ |

#### 3.2.3 小组层级关系

```
公司 (根)
├── 技术部 (team_tech)
│   ├── 前端组 (team_frontend) — 父: team_tech
│   │   ├── 张三
│   │   └── 李四
│   ├── 后端组 (team_backend) — 父: team_tech
│   │   ├── 王五
│   │   └── 赵六
│   └── 技术组长 (王明) ← team_tech 的 leader
│
├── 产品部 (team_product)
│   ├── 产品经理组 (team_pm)
│   │   ├── 产品经理A
│   │   └── 产品经理B
│   └── 产品总监 (李华) ← team_product 的 leader
│
└── 设计部 (team_design)
    └── 设计师组 (team_ux)
```

**权限继承规则**：
- 组长可以查看**所有子组**的成员龙虾和数据
- 子组组长可以查看自己组的成员数据
- 例如：技术组长可以查看前端组、后端组所有人的龙虾

### 3.3 权限检查流程

```
请求进入
    │
    ▼
┌─────────────────┐
│  验证 JWT Token │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  获取用户角色    │
│  和所属小组      │
└────────┬────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│                    权限检查                              │
│                                                         │
│  IF role == 'admin':                                   │
│      放行                                                │
│  ELIF role == 'leader':                                │
│      target_user_id IN get_subordinate_ids(user_id):   │
│          放行                                            │
│      ELSE:                                              │
│          target_user_id == user_id:                    │
│              放行                                        │
│          ELSE:                                          │
│              拒绝                                        │
│  ELSE:  # employee                                      │
│      target_user_id == user_id:                        │
│          放行                                            │
│      ELSE:                                              │
│          拒绝                                            │
└─────────────────────────────────────────────────────────┘
```

### 3.4 小组数据结构

**文件路径**：`~/.solobrave-data/teams.json`

```json
{
  "teams": [
    {
      "id": "team_root",
      "name": "公司",
      "parent_id": null,
      "level": 0,
      "path": "/team_root",
      "leader_id": "admin",
      "members": ["admin"],
      "agents": [],
      "settings": {
        "allow_cross_team_chat": false,
        "max_agents_per_member": 5
      },
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z"
    },
    {
      "id": "team_tech",
      "name": "技术部",
      "parent_id": "team_root",
      "level": 1,
      "path": "/team_root/team_tech",
      "leader_id": "user_wangming",
      "members": ["user_wangming", "user_zhangsan", "user_lisi"],
      "agents": ["agent_1", "agent_2", "agent_3"],
      "settings": {
        "allow_cross_team_chat": true,
        "max_agents_per_member": 5
      },
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z"
    },
    {
      "id": "team_frontend",
      "name": "前端组",
      "parent_id": "team_tech",
      "level": 2,
      "path": "/team_root/team_tech/team_frontend",
      "leader_id": "user_zhangsan",
      "members": ["user_zhangsan", "user_lisi"],
      "agents": ["agent_1", "agent_2"],
      "settings": {
        "allow_cross_team_chat": false,
        "max_agents_per_member": 3
      },
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z"
    }
  ]
}
```

### 3.5 小组管理 API

#### 3.5.1 获取小组列表

```
GET /api/v2/teams
```

**权限**：admin（全部）/ leader（可见本组及子组）

**Query 参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| parent_id | string | 父小组ID（可选，用于树形展示） |
| include_members | boolean | 是否包含成员列表 |

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": [
    {
      "id": "team_tech",
      "name": "技术部",
      "parent_id": "team_root",
      "level": 1,
      "leader": {
        "id": "user_wangming",
        "name": "王明"
      },
      "members_count": 5,
      "children": [
        {
          "id": "team_frontend",
          "name": "前端组",
          "parent_id": "team_tech",
          "level": 2,
          "leader": {
            "id": "user_zhangsan",
            "name": "张三"
          },
          "members_count": 3
        }
      ]
    }
  ]
}
```

#### 3.5.2 获取小组详情

```
GET /api/v2/teams/{team_id}
```

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "team_frontend",
    "name": "前端组",
    "parent_id": "team_tech",
    "level": 2,
    "path": "/team_root/team_tech/team_frontend",
    "leader": {
      "id": "user_zhangsan",
      "name": "张三",
      "avatar": "..."
    },
    "members": [
      {
        "id": "user_zhangsan",
        "name": "张三",
        "role": "leader",
        "joined_at": "2024-01-01T00:00:00Z"
      },
      {
        "id": "user_lisi",
        "name": "李四",
        "role": "member",
        "joined_at": "2024-01-15T00:00:00Z"
      }
    ],
    "agents": [
      {
        "id": "agent_1",
        "name": "小前端",
        "owner": {
          "id": "user_zhangsan",
          "name": "张三"
        },
        "status": "online"
      }
    ],
    "stats": {
      "members_count": 2,
      "agents_count": 3,
      "messages_today": 156
    },
    "settings": {...},
    "created_at": "2024-01-01T00:00:00Z"
  }
}
```

#### 3.5.3 创建小组

```
POST /api/v2/teams
```

**权限**：仅 admin

**请求体**：
```json
{
  "name": "测试组",
  "parent_id": "team_tech",
  "leader_id": "user_tester",
  "members": ["user_tester", "user_tester2"],
  "settings": {
    "allow_cross_team_chat": true,
    "max_agents_per_member": 5
  }
}
```

#### 3.5.4 更新小组

```
PUT /api/v2/teams/{team_id}
```

**权限**：仅 admin

**可更新字段**：
- `name`: 小组名称
- `leader_id`: 组长
- `settings`: 小组设置

#### 3.5.5 删除小组

```
DELETE /api/v2/teams/{team_id}
```

**权限**：仅 admin

**约束**：
- 小组内存在成员时不可删除（需先转移成员）
- 存在子小组时不可删除

#### 3.5.6 添加/移除成员

```
POST /api/v2/teams/{team_id}/members
```

```json
{
  "user_ids": ["user_wangwu"],
  "role": "member"
}
```

```
DELETE /api/v2/teams/{team_id}/members/{user_id}
```

---

## 4. 账号管理面板

### 4.1 设计目标

将现有的 `prompt()` 简陋弹窗升级为完整的账号管理面板，支持：
- 完整的 CRUD 操作
- 角色模板选择
- 小组分配
- 层级关系展示
- 批量操作

### 4.2 用户数据结构扩展

**文件路径**：`~/.solobrave-data/users.json`（扩展）

```json
{
  "users": [
    {
      "id": "user_admin",
      "username": "admin",
      "email": "admin@company.com",
      "password_hash": "$2b$12$...",
      "nickname": "系统管理员",
      "avatar": null,
      "role": "admin",
      "status": "active",
      "teams": ["team_root"],
      "team_ids": ["team_root"],
      "primary_team_id": "team_root",
      "leader_id": null,
      "subordinates": [],
      "subordinate_ids": [],
      "role_template_id": null,
      "created_by": "system",
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z",
      "last_login_at": "2024-05-15T10:30:00Z",
      "settings": {
        "theme": "light",
        "language": "zh-CN",
        "notifications": true
      }
    },
    {
      "id": "user_zhangsan",
      "username": "zhangsan",
      "email": "zhangsan@company.com",
      "password_hash": "$2b$12$...",
      "nickname": "张三",
      "avatar": "...",
      "role": "leader",
      "status": "active",
      "teams": ["team_tech", "team_frontend"],
      "team_ids": ["team_tech", "team_frontend"],
      "primary_team_id": "team_frontend",
      "leader_id": "user_wangming",
      "subordinates": ["user_lisi"],
      "subordinate_ids": ["user_lisi"],
      "role_template_id": "tpl_frontend_engineer",
      "role_template": {
        "name": "前端工程师",
        "icon": "code"
      },
      "agents": ["agent_1", "agent_2"],
      "created_by": "user_admin",
      "created_at": "2024-01-10T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z",
      "last_login_at": "2024-05-15T09:00:00Z",
      "settings": {
        "theme": "light",
        "language": "zh-CN",
        "notifications": true
      }
    },
    {
      "id": "user_lisi",
      "username": "lisi",
      "email": "lisi@company.com",
      "password_hash": "$2b$12$...",
      "nickname": "李四",
      "avatar": "...",
      "role": "employee",
      "status": "active",
      "teams": ["team_frontend"],
      "team_ids": ["team_frontend"],
      "primary_team_id": "team_frontend",
      "leader_id": "user_zhangsan",
      "subordinates": [],
      "subordinate_ids": [],
      "role_template_id": "tpl_frontend_engineer",
      "role_template": {
        "name": "前端工程师",
        "icon": "code"
      },
      "agents": ["agent_3"],
      "created_by": "user_admin",
      "created_at": "2024-01-15T00:00:00Z",
      "updated_at": "2024-05-01T00:00:00Z",
      "last_login_at": "2024-05-14T18:00:00Z",
      "settings": {
        "theme": "dark",
        "language": "zh-CN",
        "notifications": false
      }
    }
  ]
}
```

### 4.3 账号管理 API

#### 4.3.1 获取用户列表

```
GET /api/v2/users
```

**权限**：admin（全部）/ leader（本组及子组）

**Query 参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| team_id | string | 按小组筛选（可选） |
| role | string | 按角色筛选（可选） |
| status | string | 按状态筛选（可选） |
| search | string | 搜索用户名/邮箱/昵称 |
| page | int | 页码（默认1） |
| page_size | int | 每页数量（默认20） |

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "total": 50,
    "page": 1,
    "page_size": 20,
    "list": [
      {
        "id": "user_zhangsan",
        "username": "zhangsan",
        "nickname": "张三",
        "email": "zhangsan@company.com",
        "avatar": "...",
        "role": "leader",
        "role_template": {
          "id": "tpl_frontend_engineer",
          "name": "前端工程师"
        },
        "primary_team": {
          "id": "team_frontend",
          "name": "前端组"
        },
        "status": "active",
        "last_login_at": "2024-05-15T09:00:00Z",
        "agents_count": 2
      }
    ]
  }
}
```

#### 4.3.2 获取用户详情

```
GET /api/v2/users/{user_id}
```

**权限**：本人 / leader（本组及子组成员）/ admin

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "id": "user_lisi",
    "username": "lisi",
    "email": "lisi@company.com",
    "nickname": "李四",
    "avatar": "...",
    "role": "employee",
    "status": "active",
    "role_template": {
      "id": "tpl_frontend_engineer",
      "name": "前端工程师",
      "icon": "code",
      "color": "#007AFF"
    },
    "teams": [
      {
        "id": "team_frontend",
        "name": "前端组",
        "path": "技术部 / 前端组"
      }
    ],
    "hierarchy": {
      "leader": {
        "id": "user_zhangsan",
        "name": "张三",
        "avatar": "..."
      },
      "subordinates": []
    },
    "agents": [
      {
        "id": "agent_3",
        "name": "小前端",
        "status": "online",
        "last_active": "2024-05-15T14:30:00Z"
      }
    ],
    "stats": {
      "total_messages": 1250,
      "messages_this_week": 45,
      "active_days": 30
    },
    "created_at": "2024-01-15T00:00:00Z",
    "last_login_at": "2024-05-14T18:00:00Z"
  }
}
```

#### 4.3.3 创建用户

```
POST /api/v2/users
```

**权限**：仅 admin

**请求体**：
```json
{
  "username": "newwangwu",
  "email": "wangwu@company.com",
  "password": "********",
  "nickname": "王五",
  "role": "employee",
  "role_template_id": "tpl_backend_engineer",
  "team_ids": ["team_backend"],
  "primary_team_id": "team_backend",
  "leader_id": "user_leader",
  "send_welcome_email": true
}
```

**响应**：
```json
{
  "code": 0,
  "message": "用户创建成功",
  "data": {
    "id": "user_newwangwu",
    "username": "newwangwu",
    "email": "wangwu@company.com",
    "role": "employee",
    "role_template": {
      "id": "tpl_backend_engineer",
      "name": "后端工程师"
    },
    "default_agent": {
      "id": "agent_new",
      "name": "小后端"
    }
  }
}
```

#### 4.3.4 更新用户

```
PUT /api/v2/users/{user_id}
```

**权限**：admin / 本人（部分字段）

**请求体**：
```json
{
  "nickname": "新昵称",
  "avatar": "base64...",
  "team_ids": ["team_frontend", "team_product"],
  "primary_team_id": "team_product",
  "leader_id": "user_newleader",
  "status": "active",
  "settings": {
    "theme": "dark"
  }
}
```

#### 4.3.5 删除用户

```
DELETE /api/v2/users/{user_id}
```

**权限**：仅 admin

**约束**：
- 用户存在龙虾时不可删除（需先转移或删除）
- admin 不可删除自己

#### 4.3.6 批量操作

```
POST /api/v2/users/batch
```

**权限**：仅 admin

**请求体**：
```json
{
  "action": "assign_teams",
  "user_ids": ["user_1", "user_2", "user_3"],
  "team_ids": ["team_frontend"]
}
```

**支持的 action**：
- `assign_teams`: 分配小组
- `change_role`: 变更角色
- `set_status`: 设置状态
- `send_notification`: 发送通知

### 4.4 前端组件设计

#### 4.4.1 账号管理主页面

**路径**：`/#/admin/users`

**布局**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Apple Style Navigation Bar]                                       │
│  ← 返回   账号管理                               [+ 新建用户] [批量操作] │
├─────────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │ [🔍 搜索用户...]  部门: [全部 ▼]  角色: [全部 ▼]  状态: [全部 ▼] │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  用户列表                                    已选择 0 项  [导出]       │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ☑ 头像  姓名/账号           部门          角色    状态   操作   │   │
│  ├─────────────────────────────────────────────────────────────┤   │
│  │ ☐  [头像]  张三 / zhangsan    前端组        组长   ●在线  [⋮]  │   │
│  │ ☑  [头像]  李四 / lisi        前端组        员工   ●在线  [⋮]  │   │
│  │ ☐  [头像]  王五 / wangwu      后端组        员工   ○离线  [⋮]  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  [◀ 1 2 3 4 5 ▶]                        显示 1-20 / 共 50 条        │
└─────────────────────────────────────────────────────────────────────┘
```

#### 4.4.2 新建用户向导

**步骤 1 - 基本信息**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  新建用户                                        [×]               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  基本信息                                                            │
│  ─────────────────────────────────────────────────────────────       │
│                                                                       │
│  用户名 *                                                            │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ zhangsan                                                      │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  邮箱 *                                                              │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ zhangsan@company.com                                          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  初始密码 *                                                          │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ ••••••••••••                                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│  [🔄 生成随机密码]                                                   │
│                                                                       │
│  昵称                                                                │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 张三                                                          │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  头像                                                                │
│  ┌──────────┐                                                       │
│  │  + 上传  │  建议尺寸 128x128，支持 JPG/PNG                       │
│  └──────────┘                                                       │
│                                                                       │
│                                       [取消]    [下一步 →]            │
│                                                                       │
│  ●━━━○━━━○━━━○                                                       │
│  基本信息  角色配置  团队分配  确认                                │
└─────────────────────────────────────────────────────────────────────┘
```

**步骤 2 - 角色配置**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  新建用户                                        [×]               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  角色配置                                                            │
│  ─────────────────────────────────────────────────────────────       │
│                                                                       │
│  选择角色模板 *                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ [🔍 搜索模板...]                                             │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  ○ 前端工程师                                                │    │
│  │    擅长 React、Vue、TypeScript 等前端技术栈                   │    │
│  │    技能: 5 个                                                │    │
│  ├─────────────────────────────────────────────────────────────┤    │
│  │  ● 后端工程师                                                │    │
│  │    擅长 Python、Go、数据库架构设计                            │    │
│  │    技能: 4 个                                                │    │
│  ├─────────────────────────────────────────────────────────────┤    │
│  │  ○ 产品经理                                                  │    │
│  │    负责产品规划、需求管理和团队协作                            │    │
│  │    技能: 3 个                                                │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  系统将为该用户创建「小后端」AI助理，开箱即用                          │
│                                                                       │
│                                       [← 上一步]   [下一步 →]        │
│                                                                       │
│  ●━━━●━━━○━━━○                                                       │
│  基本信息  角色配置  团队分配  确认                                │
└─────────────────────────────────────────────────────────────────────┘
```

**步骤 3 - 团队分配**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  新建用户                                        [×]               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  团队分配                                                            │
│  ─────────────────────────────────────────────────────────────       │
│                                                                       │
│  选择所属团队 *                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ 技术部                                                       │    │
│  │   └─ ☑ 前端组                                                │    │
│  │   └─ □ 后端组                                                │    │
│  │   └─ □ 测试组                                                │    │
│  │ 产品部                                                       │    │
│  │   └─ □ 产品组                                                │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  主团队                                                              │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ [前端组 ▼]                                                   │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  上级领导                                                            │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │ [张三 ▼]  (前端组组长)                                       │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│                                       [← 上一步]   [下一步 →]        │
│                                                                       │
│  ●━━━●━━━●━━━○                                                       │
│  基本信息  角色配置  团队分配  确认                                │
└─────────────────────────────────────────────────────────────────────┘
```

**步骤 4 - 确认**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  新建用户                                        [×]               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  确认信息                                                            │
│  ─────────────────────────────────────────────────────────────       │
│                                                                       │
│  ┌─────────────────────────────────────────────────────────────┐    │
│  │  基本信息                                                    │    │
│  │  用户名: zhangsan                                            │    │
│  │  邮箱: zhangsan@company.com                                   │    │
│  │  昵称: 张三                                                   │    │
│  ├─────────────────────────────────────────────────────────────┤    │
│  │  角色配置                                                    │    │
│  │  模板: 后端工程师                                             │    │
│  │  系统将自动创建: 「小后端」AI助理                              │    │
│  ├─────────────────────────────────────────────────────────────┤    │
│  │  团队分配                                                    │    │
│  │  所属团队: 技术部 / 后端组                                    │    │
│  │  上级领导: 王明                                               │    │
│  └─────────────────────────────────────────────────────────────┘    │
│                                                                       │
│  ☐ 发送欢迎邮件给用户                                                │
│  ☐ 创建后立即发送通知给上级领导                                       │
│                                                                       │
│                                       [← 上一步]   [创建]            │
│                                                                       │
│  ●━━━●━━━●━━━●                                                       │
│  基本信息  角色配置  团队分配  确认                                │
└─────────────────────────────────────────────────────────────────────┘
```

#### 4.4.3 用户详情页

**路径**：`/#/admin/users/{user_id}`

**布局**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  ← 返回   用户详情                                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌────────────────┐  ┌─────────────────────────────────────────────┐ │
│  │                │  │  张三                                      │ │
│  │   [大头像]     │  │  zhangsan · 前端组 · 组长                  │ │
│  │                │  │                                            │ │
│  │  [更换头像]    │  │  ● 在线  ·  最后活跃: 今天 14:30            │ │
│  │                │  │                                            │ │
│  └────────────────┘  │  角色模板: 前端工程师                        │ │
│                      └─────────────────────────────────────────────┘ │
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │ [基本信息] [团队关系] [AI助理] [使用统计]                      │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  基本信息                                    [编辑]                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  邮箱           zhangsan@company.com                         │   │
│  │  手机           138****8888                                   │   │
│  │  部门           技术部 / 前端组                                │   │
│  │  角色           组长                                          │   │
│  │  上级领导       王明                                          │   │
│  │  下属成员       李四、王五                                     │   │
│  │  加入时间       2024年1月10日                                  │   │
│  │  最后登录       2024年5月15日 09:00                            │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

#### 4.4.4 主要组件列表

| 组件名 | 文件 | 说明 |
|--------|------|------|
| `UserManagement` | `components/UserManagement.js` | 用户管理主组件 |
| `UserList` | `components/UserList.js` | 用户列表组件 |
| `UserCard` | `components/UserCard.js` | 用户卡片 |
| `UserFilters` | `components/UserFilters.js` | 筛选器组件 |
| `UserCreateWizard` | `components/UserCreateWizard.js` | 创建用户向导 |
| `UserDetail` | `components/UserDetail.js` | 用户详情页 |
| `UserEditForm` | `components/UserEditForm.js` | 用户编辑表单 |
| `TeamTreeSelector` | `components/TeamTreeSelector.js` | 团队树形选择器 |
| `RoleTemplateSelector` | `components/RoleTemplateSelector.js` | 角色模板选择器 |
| `BatchOperations` | `components/BatchOperations.js` | 批量操作弹窗 |
| `HierarchyTree` | `components/HierarchyTree.js` | 层级关系树状图 |

---

## 5. 数据看板

### 5.1 设计目标

提供多维度的数据统计和可视化，支持：
- 团队维度：整体活跃度、消息量、任务完成率
- 个人维度：员工使用情况
- 组长视角：下属团队数据汇总
- 管理员视角：全局数据

### 5.2 数据统计 API

#### 5.2.1 全局数据概览

```
GET /api/v2/dashboard/overview
```

**权限**：admin（全部）/ leader（本组及子组）/ employee（仅自己）

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "period": {
      "start": "2024-05-01",
      "end": "2024-05-15"
    },
    "summary": {
      "total_users": 50,
      "active_users_today": 38,
      "total_messages": 12580,
      "messages_today": 456,
      "total_agents": 120,
      "active_agents": 95
    },
    "trends": {
      "messages_growth": 12.5,
      "active_users_growth": 5.2
    },
    "top_agents": [
      {
        "id": "agent_1",
        "name": "小助手",
        "messages_count": 1234,
        "active_users": 45
      }
    ]
  }
}
```

#### 5.2.2 团队数据

```
GET /api/v2/dashboard/team/{team_id}
```

**权限**：team leader / admin

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "team": {
      "id": "team_frontend",
      "name": "前端组"
    },
    "period": {
      "start": "2024-05-01",
      "end": "2024-05-15"
    },
    "members": {
      "total": 5,
      "active": 4
    },
    "messages": {
      "total": 2580,
      "daily": [
        {"date": "2024-05-01", "count": 180},
        {"date": "2024-05-02", "count": 195}
      ]
    },
    "agents": {
      "total": 12,
      "active": 10,
      "by_template": [
        {"template": "前端工程师", "count": 10},
        {"template": "测试工程师", "count": 2}
      ]
    },
    "activity": {
      "hourly_distribution": [
        {"hour": 9, "count": 45},
        {"hour": 10, "count": 78}
      ],
      "top_users": [
        {"user_id": "user_1", "name": "张三", "messages": 580}
      ]
    }
  }
}
```

#### 5.2.3 个人数据

```
GET /api/v2/dashboard/user/{user_id}
```

**权限**：本人 / leader / admin

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "user": {
      "id": "user_lisi",
      "name": "李四"
    },
    "period": {
      "start": "2024-05-01",
      "end": "2024-05-15"
    },
    "usage": {
      "total_messages": 456,
      "messages_this_week": 89,
      "active_days": 12,
      "avg_daily_messages": 38
    },
    "agents": {
      "owned": [
        {
          "id": "agent_3",
          "name": "小前端",
          "messages_count": 456,
          "last_active": "2024-05-15T14:30:00Z"
        }
      ]
    },
    "activity_timeline": [
      {"date": "2024-05-01", "messages": 42},
      {"date": "2024-05-02", "messages": 38}
    ]
  }
}
```

#### 5.2.4 趋势数据

```
GET /api/v2/dashboard/trends
```

**权限**：admin / team leader

**Query 参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| team_id | string | 小组ID（可选） |
| metric | string | 指标类型：messages/active_users/agents |
| period | string | 时间范围：week/month/quarter/year |
| granularity | string | 粒度：day/week/month |

**响应示例**：
```json
{
  "code": 0,
  "message": "success",
  "data": {
    "metric": "messages",
    "period": "month",
    "granularity": "day",
    "data": [
      {"date": "2024-04-15", "value": 1200},
      {"date": "2024-04-16", "value": 1350},
      {"date": "2024-04-17", "value": 1180}
    ],
    "summary": {
      "total": 38000,
      "avg_daily": 1267,
      "peak_day": "2024-04-20",
      "peak_value": 2100
    }
  }
}
```

### 5.3 前端组件设计

#### 5.3.1 数据看板主页面

**路径**：`/#/dashboard`

**布局**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  数据看板                                          [📅 5月1日-15日 ▼] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐   │
│  │  活跃用户   │ │  消息总量   │ │  AI助理数   │ │  今日消息   │   │
│  │             │ │             │ │             │ │             │   │
│  │    38       │ │   12,580    │ │     120     │ │     456     │   │
│  │             │ │             │ │             │ │             │   │
│  │  ↑ 5.2%     │ │  ↑ 12.5%    │ │  ↑ 3.2%     │ │  ↑ 8.1%     │   │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘   │
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  [折线图] 消息趋势                                             │   │
│  │                                                            ▲   │   │
│  │                                                       ▲        │   │
│  │                          ▲                    ▲                 │   │
│  │              ▲     ▲           ▲     ▲                            │   │
│  │    ────────────────────────────────────────────────────────→    │   │
│  │    5/1   5/3   5/5   5/7   5/9   5/11  5/13  5/15              │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌───────────────────────────┐  ┌───────────────────────────────────┐│
│  │  [柱状图] 部门活跃度       │  │  [饼图] AI助理分布                ││
│  │                           │  │                                   ││
│  │  技术部     ████████ 45%  │  │      ┌─────────────────┐          ││
│  │  产品部     █████    28%  │  │      │   前端工程师    │          ││
│  │  设计部     ████     18%  │  │      │     35%         │          ││
│  │  其他       ███       9%  │  │      │                 │          ││
│  │                           │  │      └─────────────────┘          ││
│  └───────────────────────────┘  └───────────────────────────────────┘│
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  TOP 10 活跃用户                                              │   │
│  │  ┌─────────────────────────────────────────────────────────┐  │   │
│  │  │ 1. 张三      前端组      ████████████████████  1,234 条   │  │   │
│  │  │ 2. 李四      前端组      ████████████████     980 条     │  │   │
│  │  │ 3. 王五      后端组      ████████████         756 条     │  │   │
│  │  └─────────────────────────────────────────────────────────┘  │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

#### 5.3.2 团队数据视图（组长视角）

**路径**：`/#/dashboard/team/{team_id}`

**布局**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  ← 返回   前端组数据看板                         [📅 本月 ▼] [导出]   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  团队概览                                                            │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  成员: 5 人  ·  活跃: 4 人  ·  AI助理: 12 个  ·  消息: 2,580  │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  [折线图] 团队消息趋势（可切换：按日/周/月）                    │   │
│  │                                                            ▲   │   │
│  │                          ▲                    ▲                 │   │
│  │              ▲     ▲           ▲     ▲                            │   │
│  │    ────────────────────────────────────────────────────────→    │   │
│  │    5/1   5/3   5/5   5/7   5/9   5/11  5/13  5/15              │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  成员使用情况                                                        │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │ 姓名       部门      消息数    AI助理    状态    操作          │   │
│  ├───────────────────────────────────────────────────────────────┤   │
│  │ 张三       前端组     1,234     3 个     ●在线   [查看详情]    │   │
│  │ 李四       前端组       980     2 个     ●在线   [查看详情]    │   │
│  │ 王五       前端组       756     2 个     ○离线   [查看详情]    │   │
│  │ 赵六       前端组       580     2 个     ●在线   [查看详情]    │   │
│  │ 钱七       前端组       180     1 个     ○离线   [查看详情]    │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌───────────────────────────┐  ┌───────────────────────────────────┐│
│  │  [柱状图] 成员消息排行      │  │  [环形图] AI助理使用情况           ││
│  │                           │  │                                   ││
│  │  ████████████ 张三         │  │       ┌───────────┐              ││
│  │  ██████████   李四         │  │       │  活跃     │              ││
│  │  ████████     王五         │  │       │   78%     │              ││
│  │  ██████       赵六         │  │       │           │              ││
│  │  ███         钱七          │  │       └───────────┘              ││
│  └───────────────────────────┘  └───────────────────────────────────┘│
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

#### 5.3.3 个人数据视图

**路径**：`/#/dashboard/user/{user_id}` 或 `/#/dashboard/my`

**布局**：

```
┌─────────────────────────────────────────────────────────────────────┐
│  我的数据看板                                    [📅 本月 ▼]         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  我的概览                                                            │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  总消息: 456  ·  本周: 89  ·  活跃天数: 12  ·  日均: 38 条      │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  [折线图] 我的消息趋势                                         │   │
│  │                                                            ▲   │   │
│  │                          ▲                    ▲                 │   │
│  │              ▲     ▲           ▲     ▲                            │   │
│  │    ────────────────────────────────────────────────────────→    │   │
│  │    5/1   5/3   5/5   5/7   5/9   5/11  5/13  5/15              │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  我的AI助理                                                           │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐                 │   │
│  │  │  [头像]    │  │  [头像]    │  │  [+]       │                 │   │
│  │  │  小前端    │  │  小助手    │  │  添加新AI  │                 │   │
│  │  │           │  │           │  │            │                 │   │
│  │  │  ●在线    │  │  ○离线    │  │            │                 │   │
│  │  │  消息 456 │  │  消息 123 │  │            │                 │   │
│  │  └───────────┘  └───────────┘  └───────────┘                 │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
│  使用时段                                                            │
│  ┌───────────────────────────────────────────────────────────────┐   │
│  │  [柱状图] 24小时使用分布                                       │   │
│  │                                                            ▲   │   │
│  │            ████                                               │   │
│  │      ████████████                   ████                     │   │
│  │  ███████████████████████████████████████████████████████████  │   │
│  │  0  3  6  9  12 15 18 21 24                                    │   │
│  │                     ↑                                          │   │
│  │               最佳时段: 14:00-16:00                            │   │
│  └───────────────────────────────────────────────────────────────┘   │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

#### 5.3.4 主要组件列表

| 组件名 | 文件 | 说明 |
|--------|------|------|
| `DashboardOverview` | `components/DashboardOverview.js` | 数据看板主组件 |
| `StatCard` | `components/StatCard.js` | 统计卡片组件 |
| `TrendChart` | `components/TrendChart.js` | 趋势图表（折线图） |
| `ActivityChart` | `components/ActivityChart.js` | 活跃度图表 |
| `DistributionChart` | `components/DistributionChart.js` | 分布图表（饼图/环形图） |
| `BarChart` | `components/BarChart.js` | 柱状图组件 |
| `UserActivityTable` | `components/UserActivityTable.js` | 用户活跃度表格 |
| `TeamDashboard` | `components/TeamDashboard.js` | 团队数据视图 |
| `PersonalDashboard` | `components/PersonalDashboard.js` | 个人数据视图 |
| `PeriodSelector` | `components/PeriodSelector.js` | 时间范围选择器 |

---

## 6. 数据模型设计

### 6.1 数据文件概览

```
~/.solobrave-data/
├── users.json           # 用户数据（扩展）
├── teams.json           # 小组数据（新增）
├── role_templates.json  # 角色模板（新增）
├── agents.json          # AI助理数据（改造）
├── skills.json          # 技能定义（已有）
├── groups.json          # 群组数据（已有）
├── chats/               # 聊天记录目录
│   ├── {user_id}/
│   │   ├── {agent_id}/
│   │   │   ├── metadata.json
│   │   │   └── messages.jsonl
│   │   └── ...
│   └── ...
└── knowledge/           # 知识库目录
    ├── documents/
    └── embeddings/
```

### 6.2 数据结构详解

#### 6.2.1 users.json（扩展）

```json
{
  "version": "2.0",
  "last_updated": "2024-05-15T00:00:00Z",
  "users": [
    {
      "id": "string",           // 用户唯一ID，格式: user_{uuid}
      "username": "string",     // 用户名，登录凭证
      "email": "string",        // 邮箱
      "password_hash": "string",// 密码哈希（bcrypt）
      "nickname": "string",     // 昵称
      "avatar": "string|null",  // 头像URL或base64
      "role": "string",         // admin | leader | employee
      "status": "string",       // active | inactive | suspended
      "teams": ["array"],       // 所属小组详情列表
      "team_ids": ["array"],    // 小组ID列表
      "primary_team_id": "string|null", // 主小组ID
      "leader_id": "string|null",      // 上级领导ID
      "subordinates": ["array"],       // 下属详情列表
      "subordinate_ids": ["array"],    // 下属ID列表
      "role_template_id": "string|null",// 角色模板ID
      "role_template": "object|null",   // 角色模板信息（冗余存储）
      "agents": ["array"],      // 拥有的AI助理ID列表
      "settings": {
        "theme": "light|dark|auto",
        "language": "zh-CN|en-US",
        "notifications": "boolean",
        "timezone": "string"
      },
      "created_by": "string",   // 创建者ID
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "last_login_at": "ISO8601|null"
    }
  ]
}
```

#### 6.2.2 teams.json（新增）

```json
{
  "version": "2.0",
  "last_updated": "2024-05-15T00:00:00Z",
  "teams": [
    {
      "id": "string",           // 小组唯一ID，格式: team_{uuid}
      "name": "string",         // 小组名称
      "parent_id": "string|null",// 父小组ID，null表示根小组
      "level": "integer",       // 层级深度，根为0
      "path": "string",         // 路径，如: /team_root/team_tech
      "leader_id": "string|null",// 组长用户ID
      "members": ["array"],     // 成员详情列表
      "member_ids": ["array"],  // 成员ID列表
      "agents": ["array"],      // 小组AI助理ID列表
      "settings": {
        "allow_cross_team_chat": "boolean",
        "max_agents_per_member": "integer",
        "require_approval": "boolean"
      },
      "stats": {
        "total_messages": "integer",
        "messages_this_month": "integer"
      },
      "created_at": "ISO8601",
      "updated_at": "ISO8601"
    }
  ]
}
```

#### 6.2.3 role_templates.json（新增）

```json
{
  "version": "2.0",
  "last_updated": "2024-05-15T00:00:00Z",
  "templates": [
    {
      "id": "string",           // 模板唯一ID，格式: tpl_{uuid}
      "name": "string",         // 模板名称
      "description": "string",  // 模板描述
      "icon": "string",         // 图标名称
      "color": "string",        // 主题色（hex）
      "category": "string",     // 分类：技术|产品|设计|运营|通用
      "is_system": "boolean",  // 是否系统模板
      "is_active": "boolean",   // 是否启用
      "order": "integer",       // 排序权重
      "config": {
        "skills": [
          {
            "skill_id": "string",
            "skill_name": "string",
            "version": "string",
            "config": "object"
          }
        ],
        "soul": {
          "SOUL.md": "string",
          "IDENTITY.md": "string"
        },
        "knowledge": [
          {
            "id": "string",
            "title": "string",
            "content": "string",
            "type": "markdown|html|text"
          }
        ],
        "memory_seed": {
          "initial_memory": ["string"],
          "context_templates": ["string"]
        },
        "default_settings": {
          "temperature": "float",
          "max_tokens": "integer",
          "personality": "string"
        }
      },
      "permissions": {
        "allow_chat": "boolean",
        "allow_file_upload": "boolean",
        "allow_code_execution": "boolean",
        "max_agents": "integer"
      },
      "stats": {
        "total_assigned": "integer",
        "active_count": "integer"
      },
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "created_by": "string"
    }
  ]
}
```

#### 6.2.4 agents.json（改造）

```json
{
  "version": "2.0",
  "last_updated": "2024-05-15T00:00:00Z",
  "agents": [
    {
      "id": "string",           // AI助理唯一ID，格式: agent_{uuid}
      "name": "string",         // 助理名称
      "description": "string",  // 助理描述
      "owner_id": "string",      // 所有者用户ID
      "owner_team_id": "string",// 所有者主小组ID（新增）
      "template_id": "string|null", // 来源模板ID（新增）
      "template_snapshot": {    // 模板配置快照（新增）
        "name": "string",
        "icon": "string",
        "color": "string"
      },
      "avatar": "string|null",
      "status": "online|offline|busy",
      "role": "string",         // 角色定位
      "personality": "string",   // 人格设定
      "skills": ["array"],      // 已安装技能列表
      "knowledge_ids": ["array"],// 关联知识库ID
      "memory": {
        "initial": ["array"],    // 初始记忆
        "context": ["array"],    // 上下文记忆
        "long_term": "object"    // 长期记忆
      },
      "stats": {
        "total_messages": "integer",
        "total_conversations": "integer",
        "last_active": "ISO8601"
      },
      "settings": {
        "temperature": "float",
        "max_tokens": "integer",
        "system_prompt": "string"
      },
      "created_at": "ISO8601",
      "updated_at": "ISO8601",
      "created_by": "string"
    }
  ]
}
```

### 6.3 数据关系图

```
┌──────────────┐         ┌──────────────┐         ┌──────────────────┐
│   users     │         │   teams      │         │  role_templates  │
├──────────────┤         ├──────────────┤         ├──────────────────┤
│ id           │         │ id           │         │ id               │
│ username     │         │ name         │         │ name             │
│ role         │         │ parent_id    │────────▶│ config           │
│ leader_id    │────────▶│ leader_id    │         │ permissions      │
│ team_ids     │         │ member_ids   │         │ stats            │
│ template_id  │────────▶│ id           │         └──────────────────┘
│ subordinates │         └──────────────┘                  │
└──────┬───────┘              ▲                           │
       │                      │                           │
       │                      │                           │
       ▼                      │                           │
┌──────────────┐         ┌──────────────┐                │
│   agents     │         │   teams      │                │
├──────────────┤         │  (members)   │                │
│ id           │         └──────────────┘                │
│ owner_id     │◀────────┘                                 │
│ team_id      │──────────────────────────────────────────┘
│ template_id  │
│ skills       │
│ memory       │
└──────────────┘
```

### 6.4 数据库迁移策略

#### 6.4.1 迁移脚本设计

**文件名**：`migrate_v1_to_v2.py`

```python
#!/usr/bin/env python3
"""
SoloBrave V1 -> V2 数据迁移脚本
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

DATA_DIR = Path.home() / ".solobrave-data"

def load_json(filename: str) -> Dict:
    """加载 JSON 文件"""
    path = DATA_DIR / filename
    if path.exists():
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json(filename: str, data: Dict):
    """保存 JSON 文件"""
    path = DATA_DIR / filename
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def migrate_users():
    """迁移用户数据"""
    users_data = load_json("users.json")
    
    for user in users_data.get("users", []):
        # 1. 添加新字段
        user.setdefault("teams", [])
        user.setdefault("team_ids", [])
        user.setdefault("primary_team_id", None)
        user.setdefault("leader_id", None)
        user.setdefault("subordinates", [])
        user.setdefault("subordinate_ids", [])
        user.setdefault("role_template", None)
        user.setdefault("agents", [])
        
        # 2. 处理 role 字段
        if user.get("role") == "admin":
            user["role"] = "admin"
            user["teams"] = [{"id": "team_root", "name": "公司"}]
            user["team_ids"] = ["team_root"]
            user["primary_team_id"] = "team_root"
        elif user.get("role") == "employee":
            user["role"] = "employee"
            # employee 默认加入 default 组
        
        # 3. 设置默认 settings
        user.setdefault("settings", {
            "theme": "light",
            "language": "zh-CN",
            "notifications": True
        })
        
        # 4. 添加新时间戳
        user.setdefault("last_login_at", None)
        
        # 5. 更新统计数据
        user["stats"] = {
            "total_messages": user.get("message_count", 0),
            "messages_this_month": 0
        }
    
    users_data["version"] = "2.0"
    users_data["last_updated"] = datetime.now().isoformat()
    save_json("users.json", users_data)
    print(f"迁移了 {len(users_data.get('users', []))} 个用户")

def migrate_teams():
    """创建默认团队结构"""
    teams_data = {
        "version": "2.0",
        "last_updated": datetime.now().isoformat(),
        "teams": [
            {
                "id": "team_root",
                "name": "公司",
                "parent_id": None,
                "level": 0,
                "path": "/team_root",
                "leader_id": None,
                "members": [],
                "member_ids": [],
                "agents": [],
                "settings": {
                    "allow_cross_team_chat": True,
                    "max_agents_per_member": 10
                },
                "stats": {
                    "total_messages": 0,
                    "messages_this_month": 0
                },
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
        ]
    }
    
    # 从用户数据中恢复团队信息
    users_data = load_json("users.json")
    for user in users_data.get("users", []):
        if user.get("role") == "admin":
            teams_data["teams"][0]["leader_id"] = user["id"]
            teams_data["teams"][0]["members"].append({
                "id": user["id"],
                "name": user.get("nickname", user["username"]),
                "role": "leader",
                "joined_at": user["created_at"]
            })
            teams_data["teams"][0]["member_ids"].append(user["id"])
    
    save_json("teams.json", teams_data)
    print(f"创建了 {len(teams_data['teams'])} 个团队")

def migrate_agents():
    """迁移 AI 助理数据"""
    agents_data = load_json("agents.json")
    
    for agent in agents_data.get("agents", []):
        # 添加新字段
        agent.setdefault("owner_team_id", None)
        agent.setdefault("template_id", None)
        agent.setdefault("template_snapshot", None)
        agent.setdefault("stats", {
            "total_messages": agent.get("message_count", 0),
            "total_conversations": 0,
            "last_active": None
        })
    
    agents_data["version"] = "2.0"
    agents_data["last_updated"] = datetime.now().isoformat()
    save_json("agents.json", agents_data)
    print(f"迁移了 {len(agents_data.get('agents', []))} 个AI助理")

def create_role_templates():
    """创建默认角色模板"""
    templates_data = {
        "version": "2.0",
        "last_updated": datetime.now().isoformat(),
        "templates": [
            {
                "id": "tpl_general",
                "name": "通用助手",
                "description": "通用型AI助手，适用于各种日常办公场景",
                "icon": "bot",
                "color": "#007AFF",
                "category": "通用",
                "is_system": True,
                "is_active": True,
                "order": 1,
                "config": {
                    "skills": [],
                    "soul": {
                        "SOUL.md": "# 通用助手灵魂\n\n## 核心价值观\n- 乐于助人\n- 专业高效\n- 持续学习\n\n## 沟通风格\n- 友好亲切\n- 简洁明了\n- 注重实用性",
                        "IDENTITY.md": "# 通用助手身份\n\n## 我是谁\n我是一名通用型AI助手，随时准备帮助你解决各种问题。\n\n## 我的专长\n- 信息查询\n- 文本处理\n- 任务辅助\n\n## 我的工作方式\n我会根据你的需求，提供最合适的解决方案。"
                    },
                    "knowledge": [],
                    "memory_seed": {
                        "initial_memory": [
                            "我是一名通用型AI助手",
                            "我愿意帮助用户解决各种问题"
                        ],
                        "context_templates": []
                    },
                    "default_settings": {
                        "temperature": 0.7,
                        "max_tokens": 2000,
                        "personality": "helpful"
                    }
                },
                "permissions": {
                    "allow_chat": True,
                    "allow_file_upload": True,
                    "allow_code_execution": False,
                    "max_agents": 3
                },
                "stats": {
                    "total_assigned": 0,
                    "active_count": 0
                },
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
                "created_by": "system"
            }
        ]
    }
    
    save_json("role_templates.json", templates_data)
    print(f"创建了 {len(templates_data['templates'])} 个默认模板")

def main():
    print("=" * 50)
    print("SoloBrave V1 -> V2 数据迁移")
    print("=" * 50)
    
    # 备份原文件
    backup_dir = DATA_DIR / "backup_v1"
    backup_dir.mkdir(exist_ok=True)
    
    for filename in ["users.json", "agents.json", "groups.json"]:
        src = DATA_DIR / filename
        if src.exists():
            dst = backup_dir / filename
            import shutil
            shutil.copy2(src, dst)
            print(f"已备份: {filename}")
    
    # 执行迁移
    print("\n开始迁移...")
    migrate_users()
    migrate_teams()
    migrate_agents()
    create_role_templates()
    
    print("\n迁移完成！")
    print(f"备份文件位置: {backup_dir}")

if __name__ == "__main__":
    main()
```

---

## 7. API 设计

### 7.1 API 规范

#### 7.1.1 基础规范

- **Base URL**: `/api/v2`
- **认证**: Bearer Token (JWT)
- **Content-Type**: `application/json`
- **字符编码**: UTF-8

#### 7.1.2 通用响应格式

```json
{
  "code": 0,
  "message": "success",
  "data": {}
}
```

**错误码定义**：

| code | 说明 |
|------|------|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 认证失败 |
| 1003 | 权限不足 |
| 1004 | 资源不存在 |
| 1005 | 资源已存在 |
| 1006 | 操作被禁止 |
| 2001 | 服务器内部错误 |

### 7.2 API 完整列表

#### 7.2.1 认证相关

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| POST | `/api/v2/auth/login` | 用户登录 | 公开 |
| POST | `/api/v2/auth/logout` | 用户登出 | 登录用户 |
| POST | `/api/v2/auth/refresh` | 刷新Token | 登录用户 |
| GET | `/api/v2/auth/me` | 获取当前用户信息 | 登录用户 |

#### 7.2.2 用户管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/users` | 获取用户列表 | admin/leader |
| GET | `/api/v2/users/{user_id}` | 获取用户详情 | admin/leader/本人 |
| POST | `/api/v2/users` | 创建用户 | admin |
| PUT | `/api/v2/users/{user_id}` | 更新用户 | admin/本人 |
| DELETE | `/api/v2/users/{user_id}` | 删除用户 | admin |
| POST | `/api/v2/users/batch` | 批量操作 | admin |
| PUT | `/api/v2/users/{user_id}/password` | 修改密码 | admin/本人 |
| GET | `/api/v2/users/{user_id}/hierarchy` | 获取用户层级关系 | admin/leader/本人 |
| GET | `/api/v2/users/{user_id}/subordinates` | 获取下属列表 | admin/leader/本人 |

#### 7.2.3 团队管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/teams` | 获取团队列表 | admin/leader |
| GET | `/api/v2/teams/tree` | 获取团队树形结构 | admin/leader |
| GET | `/api/v2/teams/{team_id}` | 获取团队详情 | admin/leader |
| POST | `/api/v2/teams` | 创建团队 | admin |
| PUT | `/api/v2/teams/{team_id}` | 更新团队 | admin |
| DELETE | `/api/v2/teams/{team_id}` | 删除团队 | admin |
| POST | `/api/v2/teams/{team_id}/members` | 添加成员 | admin |
| DELETE | `/api/v2/teams/{team_id}/members/{user_id}` | 移除成员 | admin |
| PUT | `/api/v2/teams/{team_id}/leader` | 设置组长 | admin |
| GET | `/api/v2/teams/{team_id}/stats` | 获取团队统计 | admin/leader |

#### 7.2.4 角色模板管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/role-templates` | 获取模板列表 | admin |
| GET | `/api/v2/role-templates/{template_id}` | 获取模板详情 | admin |
| GET | `/api/v2/role-templates/{template_id}/preview` | 模板预览 | admin/员工 |
| POST | `/api/v2/role-templates` | 创建模板 | admin |
| PUT | `/api/v2/role-templates/{template_id}` | 更新模板 | admin |
| DELETE | `/api/v2/role-templates/{template_id}` | 删除模板 | admin |
| GET | `/api/v2/role-templates/categories` | 获取模板分类 | admin |

#### 7.2.5 AI助理管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/agents` | 获取AI助理列表 | 按权限过滤 |
| GET | `/api/v2/agents/{agent_id}` | 获取AI助理详情 | owner/admin |
| POST | `/api/v2/agents` | 创建AI助理 | admin/员工 |
| PUT | `/api/v2/agents/{agent_id}` | 更新AI助理 | owner/admin |
| DELETE | `/api/v2/agents/{agent_id}` | 删除AI助理 | owner/admin |
| GET | `/api/v2/agents/{agent_id}/stats` | 获取AI助理统计 | owner/admin |
| POST | `/api/v2/agents/{agent_id}/skills` | 安装技能 | owner/admin |
| DELETE | `/api/v2/agents/{agent_id}/skills/{skill_id}` | 卸载技能 | owner/admin |

#### 7.2.6 聊天相关

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/chats` | 获取聊天列表 | owner/admin |
| GET | `/api/v2/chats/{chat_id}/messages` | 获取聊天消息 | owner/admin |
| POST | `/api/v2/chats/{chat_id}/messages` | 发送消息 | owner/admin |
| DELETE | `/api/v2/chats/{chat_id}` | 删除聊天 | owner/admin |

#### 7.2.7 数据看板

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/dashboard/overview` | 全局数据概览 | admin |
| GET | `/api/v2/dashboard/team/{team_id}` | 团队数据 | admin/team leader |
| GET | `/api/v2/dashboard/user/{user_id}` | 用户数据 | admin/leader/本人 |
| GET | `/api/v2/dashboard/trends` | 趋势数据 | admin/leader |
| GET | `/api/v2/dashboard/activity` | 活跃度数据 | admin/leader |

#### 7.2.8 技能管理

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | `/api/v2/skills` | 获取技能列表 | admin |
| GET | `/api/v2/skills/{skill_id}` | 获取技能详情 | admin |
| POST | `/api/v2/skills` | 安装技能 | admin |
| DELETE | `/api/v2/skills/{skill_id}` | 卸载技能 | admin |

### 7.3 权限中间件

```python
# 后端权限检查伪代码
def check_permission(user, resource, action):
    """
    权限检查
    - user: 当前用户
    - resource: 资源类型 (user/team/agent/dashboard)
    - action: 操作类型 (read/write/delete)
    """
    
    # admin 可以操作所有资源
    if user.role == "admin":
        return True
    
    # 读取操作
    if action == "read":
        if resource.type == "user":
            # leader 可以查看本组及子组成员
            if user.role == "leader":
                return resource.id in get_all_subordinate_user_ids(user)
            # 员工只能查看自己
            return resource.id == user.id
        
        if resource.type == "team":
            if user.role == "leader":
                return resource.id in get_manageable_team_ids(user)
            return False
        
        if resource.type == "agent":
            # owner 或 leader/admin
            if resource.owner_id == user.id:
                return True
            if user.role in ("leader", "admin"):
                owner = get_user(resource.owner_id)
                return owner.team_id in get_manageable_team_ids(user)
            return False
        
        if resource.type == "dashboard":
            if user.role == "leader":
                return resource.team_id in get_manageable_team_ids(user)
            return resource.user_id == user.id
    
    # 写操作
    if action in ("write", "delete"):
        if resource.type == "user":
            # admin 可以管理所有用户
            # leader 不能管理用户
            return user.role == "admin"
        
        if resource.type == "team":
            return user.role == "admin"
        
        if resource.type == "agent":
            return resource.owner_id == user.id or user.role == "admin"
        
        if resource.type == "role_template":
            return user.role == "admin"
    
    return False
```

---

## 8. 前端改动点

### 8.1 整体架构改动

#### 8.1.1 路由结构

```javascript
// 新增路由配置
const routes = {
  // 管理后台
  '/admin/users': {
    component: 'UserManagement',
    title: '账号管理',
    roles: ['admin']
  },
  '/admin/users/:id': {
    component: 'UserDetail',
    title: '用户详情',
    roles: ['admin', 'leader']
  },
  '/admin/users/create': {
    component: 'UserCreateWizard',
    title: '新建用户',
    roles: ['admin']
  },
  '/admin/teams': {
    component: 'TeamManagement',
    title: '团队管理',
    roles: ['admin']
  },
  '/admin/role-templates': {
    component: 'RoleTemplateList',
    title: '角色模板',
    roles: ['admin']
  },
  '/admin/role-templates/:id': {
    component: 'RoleTemplateEditor',
    title: '编辑模板',
    roles: ['admin']
  },
  
  // 数据看板
  '/dashboard': {
    component: 'DashboardOverview',
    title: '数据看板',
    roles: ['admin', 'leader', 'employee']
  },
  '/dashboard/team/:teamId': {
    component: 'TeamDashboard',
    title: '团队数据',
    roles: ['admin', 'leader']
  },
  '/dashboard/user/:userId': {
    component: 'UserDashboard',
    title: '个人数据',
    roles: ['admin', 'leader']
  },
  '/dashboard/my': {
    component: 'PersonalDashboard',
    title: '我的数据',
    roles: ['admin', 'leader', 'employee']
  },
  
  // 原有路由保持不变
  '/': 'MainPage',
  '/chat/:agentId': 'ChatPage',
  // ...
};
```

#### 8.1.2 权限控制层

```javascript
// 全局权限控制
class PermissionManager {
  constructor() {
    this.currentUser = null;
    this.userTeams = [];
    this.userRole = null;
  }
  
  // 检查用户权限
  can(action, resource, resourceId = null) {
    if (!this.currentUser) return false;
    
    const { role } = this.currentUser;
    
    // admin 拥有所有权限
    if (role === 'admin') return true;
    
    // 具体权限判断逻辑...
    switch (action) {
      case 'view_user':
        if (role === 'leader') {
          return this.isInManagedTeam(resourceId);
        }
        return resourceId === this.currentUser.id;
      
      case 'manage_user':
        return role === 'admin';
      
      case 'view_team':
        return role === 'admin' || this.isInManagedTeam(resourceId);
      
      case 'view_dashboard':
        if (role === 'leader') {
          return this.isInManagedTeam(resourceId);
        }
        return !resourceId || resourceId === this.currentUser.id;
      
      // ... 其他权限
    }
    
    return false;
  }
  
  // 获取用户可管理的团队ID列表
  getManagedTeamIds() {
    if (this.currentUser.role === 'admin') {
      return this.getAllTeamIds(); // admin 可以管理所有团队
    }
    
    // 递归获取所有子团队
    return this.getTeamAndDescendants(this.currentUser.primary_team_id);
  }
  
  // 检查资源是否属于可管理的团队
  isInManagedTeam(resourceTeamId) {
    const managedTeams = this.getManagedTeamIds();
    return managedTeams.includes(resourceTeamId);
  }
}

// 全局实例
window.permissionManager = new PermissionManager();
```

### 8.2 侧栏改造

#### 8.2.1 权限过滤逻辑

```javascript
// 侧栏组件改造
class Sidebar {
  constructor() {
    this.filteredEmployees = [];
    this.filteredAgents = [];
  }
  
  // 根据权限过滤显示内容
  refreshByPermission() {
    const user = authManager.getCurrentUser();
    const role = user?.role;
    
    if (role === 'admin') {
      // admin: 显示所有
      this.showAllEmployees();
      this.showAllAgents();
    } else if (role === 'leader') {
      // leader: 显示本组及子组成员和AI助理
      this.showTeamMembers(user.primary_team_id);
      this.showTeamAgents(user.primary_team_id);
    } else {
      // employee: 只显示自己的AI助理
      this.showOnlyOwnAgents(user.id);
    }
    
    this.render();
  }
  
  // 显示团队成员（包括子组）
  showTeamMembers(teamId) {
    const teamIds = permissionManager.getTeamAndDescendants(teamId);
    const allUsers = dataStore.getUsers();
    
    this.filteredEmployees = allUsers.filter(user => 
      user.team_ids.some(tid => teamIds.includes(tid))
    );
  }
  
  // 显示团队AI助理（包括子组）
  showTeamAgents(teamId) {
    const teamIds = permissionManager.getTeamAndDescendants(teamId);
    const allAgents = dataStore.getAgents();
    
    this.filteredAgents = allAgents.filter(agent => 
      teamIds.includes(agent.owner_team_id)
    );
  }
}
```

#### 8.2.2 侧栏UI更新

```
更新前:
┌────────────────────┐
│ [龙] 龙虾办公室      │
├────────────────────┤
│ ☰ 功能             │
│ ├─ 首页            │
│ ├─ 聊天            │
│ ├─ 群组            │
│ ├─ 知识库          │
│ └─ 技能            │
├────────────────────┤
│ 👥 员工列表        │
│ ├─ 张三            │
│ ├─ 李四            │
│ └─ 王五            │
├────────────────────┤
│ 🦞 龙虾列表        │
│ └─ 小助手          │
└────────────────────┘

更新后:
┌────────────────────┐
│ [龙] 龙虾办公室      │
├────────────────────┤
│ ☰ 功能             │
│ ├─ 首页            │
│ ├─ 聊天            │
│ ├─ 群组            │
│ ├─ 知识库          │
│ └─ 技能            │
├────────────────────┤
│ [admin/组长可见]   │
│ 👥 团队管理        │
│ ├─ 技术部          │
│ │  ├─ 前端组       │
│ │  └─ 后端组       │
│ └─ 产品部          │
├────────────────────┤
│ 👥 成员列表        │
│ ├─ 张三 [组长]     │
│ ├─ 李四            │
│ └─ 王五            │
├────────────────────┤
│ 🦞 我的龙虾        │
│ ├─ 小前端          │
│ └─ 小助手          │
├────────────────────┤
│ 📊 数据看板        │
│ [admin/组长可见]   │
└────────────────────┘
```

### 8.3 新增页面详细设计

#### 8.3.1 角色模板管理页

**文件**: `components/RoleTemplateList.js`

**功能**:
1. 模板列表展示（卡片式）
2. 分类筛选
3. 状态筛选
4. 搜索功能
5. 新建/编辑/删除模板
6. 模板使用统计

**状态管理**:
```javascript
const templateState = {
  templates: [],
  categories: [],
  filters: {
    category: null,
    status: 'all',
    search: ''
  },
  pagination: {
    page: 1,
    pageSize: 20,
    total: 0
  },
  selectedTemplate: null,
  isLoading: false,
  error: null
};
```

#### 8.3.2 用户创建向导

**文件**: `components/UserCreateWizard.js`

**步骤流程**:
1. 基本信息（用户名、邮箱、密码）
2. 角色配置（选择模板）
3. 团队分配（选择团队、设置上级）
4. 确认信息

**状态管理**:
```javascript
const wizardState = {
  currentStep: 1,
  totalSteps: 4,
  formData: {
    // Step 1
    username: '',
    email: '',
    password: '',
    nickname: '',
    avatar: null,
    
    // Step 2
    role_template_id: null,
    
    // Step 3
    team_ids: [],
    primary_team_id: null,
    leader_id: null,
    
    // Step 4 (computed)
    confirm_data: {}
  },
  validation: {
    step1: { valid: false, errors: {} },
    step2: { valid: false, errors: {} },
    step3: { valid: false, errors: {} }
  },
  isSubmitting: false
};
```

#### 8.3.3 数据看板页

**文件**: `components/DashboardOverview.js`

**图表组件**:
- `TrendChart`: 趋势折线图
- `DistributionChart`: 分布饼图/环形图
- `BarChart`: 柱状图
- `ActivityHeatmap`: 活跃度热力图
- `StatCard`: 统计卡片

**数据获取**:
```javascript
async function loadDashboardData(period) {
  const [overview, trends, distribution] = await Promise.all([
    api.getDashboardOverview(period),
    api.getDashboardTrends({ metric: 'messages', period }),
    api.getDashboardDistribution()
  ]);
  
  return { overview, trends, distribution };
}
```

### 8.4 现有组件改造

#### 8.4.1 导航栏更新

**文件**: `components/Navbar.js`

```javascript
// 新增导航项
const navItems = [
  // 原有...
  {
    id: 'users',
    label: '账号管理',
    icon: 'users',
    path: '/admin/users',
    roles: ['admin']
  },
  {
    id: 'teams',
    label: '团队管理',
    icon: 'sitemap',
    path: '/admin/teams',
    roles: ['admin']
  },
  {
    id: 'role-templates',
    label: '角色模板',
    icon: 'box',
    path: '/admin/role-templates',
    roles: ['admin']
  },
  {
    id: 'dashboard',
    label: '数据看板',
    icon: 'chart',
    path: '/dashboard',
    roles: ['admin', 'leader', 'employee']
  }
];

// 根据角色过滤显示
function filterNavItems(userRole) {
  return navItems.filter(item => 
    item.roles.includes(userRole)
  );
}
```

#### 8.4.2 用户选择器改造

**文件**: `components/UserSelector.js`

```javascript
class UserSelector {
  // 支持按团队筛选
  async searchUsers(query, options = {}) {
    const { teamId, role, includeSubordinates } = options;
    
    let users = await api.getUsers({ search: query });
    
    // 按团队过滤
    if (teamId) {
      const teamIds = includeSubordinates 
        ? permissionManager.getTeamAndDescendants(teamId)
        : [teamId];
      
      users = users.filter(u => 
        u.team_ids.some(tid => teamIds.includes(tid))
      );
    }
    
    // 按角色过滤
    if (role) {
      users = users.filter(u => u.role === role);
    }
    
    return users;
  }
}
```

#### 8.4.3 聊天页面权限控制

**文件**: `pages/ChatPage.js`

```javascript
class ChatPage {
  async loadChat(agentId) {
    const agent = await api.getAgent(agentId);
    
    // 权限检查
    if (!permissionManager.can('view', { type: 'agent', owner_id: agent.owner_id })) {
      this.showPermissionDenied();
      return;
    }
    
    // 加载聊天记录...
  }
}
```

### 8.5 UI 组件清单

| 组件名 | 文件 | 说明 |
|--------|------|------|
| `AppShell` | `components/AppShell.js` | 应用外壳，包含侧栏和主内容区 |
| `Navbar` | `components/Navbar.js` | 顶部导航栏 |
| `Sidebar` | `components/Sidebar.js` | 侧边栏 |
| `SidebarSection` | `components/SidebarSection.js` | 侧栏分区 |
| `UserManagement` | `components/UserManagement.js` | 用户管理主组件 |
| `UserList` | `components/UserList.js` | 用户列表 |
| `UserCard` | `components/UserCard.js` | 用户卡片 |
| `UserFilters` | `components/UserFilters.js` | 用户筛选器 |
| `UserCreateWizard` | `components/UserCreateWizard.js` | 用户创建向导 |
| `UserDetail` | `components/UserDetail.js` | 用户详情页 |
| `UserEditModal` | `components/UserEditModal.js` | 用户编辑弹窗 |
| `TeamManagement` | `components/TeamManagement.js` | 团队管理组件 |
| `TeamTree` | `components/TeamTree.js` | 团队树形结构 |
| `TeamMemberList` | `components/TeamMemberList.js` | 团队成员列表 |
| `RoleTemplateList` | `components/RoleTemplateList.js` | 角色模板列表 |
| `RoleTemplateCard` | `components/RoleTemplateCard.js` | 角色模板卡片 |
| `RoleTemplateEditor` | `components/RoleTemplateEditor.js` | 角色模板编辑器 |
| `TemplateSkillSelector` | `components/TemplateSkillSelector.js` | 模板技能选择器 |
| `TemplateSoulEditor` | `components/TemplateSoulEditor.js` | 模板灵魂编辑器 |
| `DashboardOverview` | `components/DashboardOverview.js` | 数据看板主组件 |
| `StatCard` | `components/StatCard.js` | 统计卡片 |
| `TrendChart` | `components/TrendChart.js` | 趋势图表 |
| `DistributionChart` | `components/DistributionChart.js` | 分布图表 |
| `BarChart` | `components/BarChart.js` | 柱状图 |
| `TeamDashboard` | `components/TeamDashboard.js` | 团队数据看板 |
| `PersonalDashboard` | `components/PersonalDashboard.js` | 个人数据看板 |
| `PeriodSelector` | `components/PeriodSelector.js` | 时间范围选择器 |
| `PermissionGuard` | `components/PermissionGuard.js` | 权限守卫组件 |
| `RoleBadge` | `components/RoleBadge.js` | 角色徽章 |
| `TeamSelector` | `components/TeamSelector.js` | 团队选择器 |
| `BatchOperationsModal` | `components/BatchOperationsModal.js` | 批量操作弹窗 |

---

## 9. 实施路线

### 9.1 Phase 1: 权限体系 + 小组管理 + 账号升级

**预计工时**: 2 周

**目标**: 完成基础的权限体系和账号管理功能

#### 9.1.1 Week 1: 数据模型和后端基础

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T1.1 | 创建 teams.json 数据结构 | 无 | 0.5d |
| T1.2 | 扩展 users.json 新增字段 | 无 | 0.5d |
| T1.3 | 编写数据迁移脚本 migrate_v1_to_v2.py | T1.1, T1.2 | 1d |
| T1.4 | 实现小组 CRUD API | T1.1 | 2d |
| T1.5 | 实现用户管理 API 扩展 | T1.2 | 2d |
| T1.6 | 实现权限中间件 | T1.4, T1.5 | 1d |
| T1.7 | 后端接口测试 | T1.3-T1.6 | 1d |

**交付物**:
- 数据迁移脚本
- 小组管理 API (5个接口)
- 用户管理 API 扩展 (3个新接口)
- 权限中间件

#### 9.1.2 Week 2: 前端账号管理

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T1.8 | 账号管理页面 UserManagement | T1.5, T1.6 | 1d |
| T1.9 | 用户创建向导 UserCreateWizard | T1.5, T1.6 | 1.5d |
| T1.10 | 用户详情页 UserDetail | T1.5, T1.6 | 1d |
| T1.11 | 团队管理页面 TeamManagement | T1.4, T1.6 | 1d |
| T1.12 | 侧栏权限过滤 | T1.6 | 0.5d |
| T1.13 | 导航栏权限控制 | T1.6 | 0.5d |
| T1.14 | 前后端联调 | T1.8-T1.13 | 1.5d |

**交付物**:
- 账号管理完整 CRUD 页面
- 用户创建向导（4步）
- 团队管理页面
- 侧栏和导航栏权限控制

#### 9.1.3 Phase 1 验收标准

- [ ] 用户可以按小组筛选
- [ ] 组长只能看到本组及子组成员
- [ ] 员工只能看到自己
- [ ] 可以创建/编辑/删除用户
- [ ] 可以创建/编辑/删除小组
- [ ] 可以分配用户到小组
- [ ] 层级关系正确展示

---

### 9.2 Phase 2: 角色模板系统

**预计工时**: 2 周

**目标**: 实现角色模板的完整功能

#### 9.2.1 Week 1: 后端模板功能

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T2.1 | 设计 role_templates.json 结构 | 无 | 0.5d |
| T2.2 | 实现模板 CRUD API | T2.1 | 2d |
| T2.3 | 实现模板预览 API | T2.2 | 0.5d |
| T2.4 | 实现模板应用到用户逻辑 | T2.2 | 1d |
| T2.5 | 创建默认模板数据 | T2.1 | 0.5d |
| T2.6 | 后端接口测试 | T2.2-T2.5 | 1d |

**交付物**:
- role_templates.json 数据结构
- 模板 CRUD API (6个接口)
- 默认模板数据（通用助手）

#### 9.2.2 Week 2: 前端模板功能

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T2.7 | 角色模板列表页 | T2.2 | 1d |
| T2.8 | 角色模板编辑页 | T2.2 | 1.5d |
| T2.9 | 模板技能选择器 | T2.2 | 0.5d |
| T2.10 | 模板灵魂编辑器 | T2.2 | 0.5d |
| T2.11 | 用户创建向导集成模板选择 | T2.3 | 1d |
| T2.12 | 前后端联调 | T2.7-T2.11 | 1d |

**交付物**:
- 角色模板管理页面
- 模板编辑功能（技能、灵魂）
- 用户创建向导模板选择

#### 9.2.3 Phase 2 验收标准

- [ ] 管理员可以创建/编辑/删除模板
- [ ] 模板包含技能、灵魂、知识、记忆配置
- [ ] 用户创建时可以选择模板
- [ ] 从模板创建的用户自动获得配置
- [ ] 员工不能修改核心配置

---

### 9.3 Phase 3: 数据看板

**预计工时**: 1.5 周

**目标**: 实现数据统计和可视化

#### 9.3.1 Week 1: 数据统计后端

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T3.1 | 设计数据统计指标 | 无 | 0.5d |
| T3.2 | 实现全局概览 API | T3.1 | 1d |
| T3.3 | 实现团队数据 API | T3.1 | 1d |
| T3.4 | 实现个人数据 API | T3.1 | 1d |
| T3.5 | 实现趋势数据 API | T3.1 | 1d |
| T3.6 | 消息统计采集逻辑 | T1.7 | 1d |

**交付物**:
- 数据看板 API (5个接口)
- 消息统计采集机制

#### 9.3.2 Week 2: 前端可视化

| 任务 | 描述 | 依赖 | 工时 |
|------|------|------|------|
| T3.7 | 数据看板概览页 | T3.2 | 1d |
| T3.8 | 趋势图表组件 TrendChart | T3.5 | 0.5d |
| T3.9 | 分布图表组件 DistributionChart | T3.5 | 0.5d |
| T3.10 | 团队数据视图 | T3.3 | 1d |
| T3.11 | 个人数据视图 | T3.4 | 0.5d |
| T3.12 | 时间范围选择器 | T3.7 | 0.5d |
| T3.13 | 前后端联调 | T3.7-T3.12 | 1d |

**交付物**:
- 全局数据看板页面
- 团队数据视图
- 个人数据视图
- 图表组件

#### 9.3.3 Phase 3 验收标准

- [ ] 全局数据概览展示
- [ ] 消息趋势图表
- [ ] 部门活跃度分布
- [ ] 组长可以看到团队数据
- [ ] 员工可以看到个人数据
- [ ] 支持时间范围筛选

---

### 9.4 整体里程碑

| 里程碑 | 时间 | 交付内容 |
|--------|------|----------|
| M1 | Week 2 (Phase 1 结束) | 权限体系、账号管理、团队管理 |
| M2 | Week 4 (Phase 2 结束) | 角色模板系统 |
| M3 | Week 5.5 (Phase 3 结束) | 数据看板 |
| M4 | Week 6 | 系统测试、Bug修复、文档 |
| GA | Week 7 | 正式发布 |

### 9.5 技术债务和后续优化

| 优先级 | 任务 | 说明 |
|--------|------|------|
| P0 | 移除 prompt() 代码 | 将所有 prompt() 替换为正式 UI |
| P1 | 简化 index.html | 考虑拆分组件到独立文件 |
| P1 | 消息分页加载 | 当前可能一次性加载全部消息 |
| P2 | 离线消息同步 | 支持离线后消息同步 |
| P2 | 移动端适配 | 响应式布局优化 |
| P3 | 国际化 | 支持多语言 |

---

## 附录

### A. 术语表

| 术语 | 说明 |
|------|------|
| 龙虾/Agent | AI 助理，同义 |
| 小组/Team | 权限划分的基本单位 |
| 角色模板 | 预打包的 Agent 配置 |
| 灵魂 | Agent 的人格配置（SOUL.md/IDENTITY.md） |
| leader | 组长，有团队管理权限 |

### B. 文件清单

**后端文件**:
- `solobrave-server.py` - 主服务器（需改造）

**前端文件**:
- `index.html` - 主页面（需改造）

**新增文件**:
- `migrate_v1_to_v2.py` - 数据迁移脚本
- `components/UserManagement.js`
- `components/UserCreateWizard.js`
- `components/UserDetail.js`
- `components/TeamManagement.js`
- `components/RoleTemplateList.js`
- `components/RoleTemplateEditor.js`
- `components/DashboardOverview.js`
- `components/TrendChart.js`
- `components/DistributionChart.js`
- ... (详见第8章组件清单)

### C. 配置示例

**OpenClaw 配置** (如需更新):
```json
{
  "openclaw": {
    "ws_url": "ws://192.168.1.25:18789",
    "timeout": 30
  }
}
```

**JWT 配置**:
```json
{
  "jwt": {
    "secret": "your-secret-key",
    "algorithm": "HS256",
    "expires_in": "7d"
  }
}
```

---

**文档结束**

*本文档为 SoloBrave V2 架构设计初稿，具体实现时可能根据实际情况调整。*
