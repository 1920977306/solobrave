/**
 * Task Model - 任务数据模型
 * M1 核心模块
 */

// 任务状态
export const TASK_STATUS = {
  PENDING: 'pending',        // 待处理
  IN_PROGRESS: 'in_progress', // 进行中
  REVIEW: 'review',          // 待审核
  DONE: 'done',              // 已完成
  CANCELLED: 'cancelled'     // 已取消
};

// 任务优先级
export const TASK_PRIORITY = {
  P1: { code: 'P1', label: '紧急', color: '#FF4757', weight: 3 },
  P2: { code: 'P2', label: '重要', color: '#FFA502', weight: 2 },
  P3: { code: 'P3', label: '普通', color: '#2ED573', weight: 1 }
};

// 状态标签映射
export const STATUS_LABELS = {
  [TASK_STATUS.PENDING]: '待处理',
  [TASK_STATUS.IN_PROGRESS]: '进行中',
  [TASK_STATUS.REVIEW]: '待审核',
  [TASK_STATUS.DONE]: '已完成',
  [TASK_STATUS.CANCELLED]: '已取消'
};

/**
 * 创建任务
 */
export function createTask(config) {
  const now = new Date().toISOString();

  return {
    id: generateId('task'),
    title: config.title || '新任务',
    description: config.description || '',
    assigneeId: config.assigneeId || null,
    creatorId: config.creatorId || 'user',
    status: config.status || TASK_STATUS.PENDING,
    priority: config.priority || 'P2',
    createdAt: now,
    updatedAt: now,
    deadline: config.deadline || null,
    deliverables: config.deliverables || [],
    conversationId: config.conversationId || null,
    tags: config.tags || [],
    logs: [{
      action: 'created',
      time: now,
      by: 'user'
    }],
    metadata: {
      estimatedHours: config.estimatedHours || null,
      actualHours: null,
      reviewNotes: null
    }
  };
}

/**
 * 生成任务消息（用于发送给 AI 员工）
 */
export function buildTaskMessage(task, employee) {
  const priorityInfo = TASK_PRIORITY[task.priority] || TASK_PRIORITY.P3;
  const deadlineText = task.deadline ? `\n截止时间：${formatDeadline(task.deadline)}` : '';

  return `【新任务指派】

标题：${task.title}
优先级：${priorityInfo.label}（${task.priority}）${deadlineText}

描述：
${task.description}

请确认收到，并告知你的执行计划。`;
}

/**
 * 格式化截止时间
 */
function formatDeadline(deadline) {
  const date = new Date(deadline);
  const now = new Date();
  const diffMs = date - now;
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));

  if (diffHours < 24) {
    return `今天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
  } else if (diffHours < 48) {
    return `明天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
  } else {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }
}

function generateId(prefix) {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 6);
  return `${prefix}_${timestamp}_${random}`;
}