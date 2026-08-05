# 任务管理前端规格

## 后端API（已实现）

| 方法 | 路径 | 说明 | 权限 |
|------|------|------|------|
| GET | /api/tasks | 任务列表，支持?status=&assignee=过滤 | 管理员看全部，员工看assignee=自己或creator=自己的 |
| GET | /api/tasks/:id | 单个任务详情 | 管理员或assignee/creator |
| POST | /api/tasks | 创建任务 | 仅管理员 |
| PUT | /api/tasks/:id | 更新任务 | 管理员可改全部，员工只能改status和progress |
| DELETE | /api/tasks/:id | 删除任务 | 仅管理员 |

### 任务数据模型
```
id: "task_xxx"
title: string (必填)
description: string
assignee: string (AI员工的agentId)
assignee_name: string
creator: string (userId)
creator_name: string
status: "pending" | "in_progress" | "completed" | "cancelled"
priority: "high" | "normal" | "low"
deadline: string (日期 YYYY-MM-DD)
project_id: string
progress: string (进度说明)
created_at: string
updated_at: string
completed_at: string
```

## 前端需求

### 1. 侧边栏导航
在达人库(navInfluencers)和设置(navSettings)之间添加"任务"导航项：
- id: `navTasks`
- data-module: `tasks`
- onclick: `switchModule('tasks')`
- icon: 用checkbox或clipboard图标

### 2. switchModule函数注册
在switchModule函数中添加tasks模块：
- allMids数组添加 `'tasksMidList'`
- allRights数组添加 `'tasksRight'`
- moduleMidMap添加 `tasks: ['tasksMidList']`
- moduleRightMap添加 `tasks: 'tasksRight'`
- appMain的classList操作添加 `'tasks-active'`
- 模块加载逻辑添加 `else if (module === 'tasks') loadTasks();`

### 3. 页面布局
参考products模块的三栏布局结构：
- 左侧任务列表 `tasksMidList`
- 右侧任务详情/编辑 `tasksRight`

### 4. 任务列表（tasksMidList）
- 顶部header：标题"任务管理" + 任务总数 + "新建任务"按钮
- 筛选tab：全部 / 待处理 / 进行中 / 已完成
- 任务卡片列表，每项显示：
  - 标题
  - 指派人名称
  - 优先级标签（高=红色, 中=蓝色, 低=灰色）
  - 截止日期
  - 状态badge（pending=灰色, in_progress=蓝色, completed=绿色, cancelled=红色）
- 点击卡片在右侧显示详情

### 5. 任务详情（tasksRight）
- 显示完整任务信息
- 管理员：可编辑所有字段 + 删除按钮
- 员工：只能更新状态和进度备注
- 状态切换按钮：待处理→进行中→已完成
- 返回按钮

### 6. 新建任务表单（弹窗或右侧面板）
- 标题（必填input）
- 描述（textarea）
- 指派给（select下拉，选项从/api/agents获取AI员工列表）
- 优先级（select：高/中/低）
- 截止日期（date input）
- 提交按钮

### 7. JavaScript函数
```javascript
loadTasks()           // GET /api/tasks，渲染列表
renderTaskList(tasks) // 渲染任务卡片
showTaskDetail(id)    // GET /api/tasks/:id，右侧显示详情
openTaskForm()        // 打开新建表单
submitTask()          // POST /api/tasks
updateTaskStatus(id, status) // PUT /api/tasks/:id {status}
updateTaskProgress(id, progress) // PUT /api/tasks/:id {progress}
deleteTask(id)        // DELETE /api/tasks/:id
filterTasks(status)   // 前端筛选
```

### 8. API调用方式
参考现有代码中fetchProducts的写法，使用fetch + Authorization Bearer token：
```javascript
const res = await fetch('/api/tasks', {
  headers: { 'Authorization': 'Bearer ' + token }
});
```

### 9. 设计风格
- Apple/飞书风格，卡片式，圆角12px
- 蓝色主色调 #007AFF
- 与现有products/influencers模块视觉风格一致
- 状态颜色：pending=#8E8E93, in_progress=#007AFF, completed=#34C759, cancelled=#FF3B30
- 优先级颜色：high=#FF3B30, normal=#007AFF, low=#8E8E93

### 10. 权限检查
如果现有模块通过hasModulePermission检查权限，tasks模块也需要添加对应配置。检查 `MODULE_PERMISSIONS` 或类似配置中是否需要添加tasks模块。
