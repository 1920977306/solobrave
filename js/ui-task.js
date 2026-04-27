/**
 * SoloBrave UI - 任务页面
 */

(function() {
  const ns = window.soloBrave;
  const ui = ns.ui = ns.ui || {};

  ui.renderTaskBoard = function() {
    const container = document.getElementById('main-content');
    const tasks = ns.store.get('tasks') || [];
    const employees = ns.store.get('employees') || [];
    const empMap = new Map(employees.map(e => [e.id, e]));

    const statusConfig = {
      pending: { label: '待处理', color: '#FF4757' },
      in_progress: { label: '进行中', color: '#FFA502' },
      review: { label: '待审核', color: '#1E90FF' },
      done: { label: '已完成', color: '#2ED573' },
      cancelled: { label: '已取消', color: '#95A5A6' }
    };

    const stats = {
      total: tasks.length,
      pending: tasks.filter(t => t.status === 'pending').length,
      in_progress: tasks.filter(t => t.status === 'in_progress').length,
      review: tasks.filter(t => t.status === 'review').length,
      done: tasks.filter(t => t.status === 'done').length
    };

    container.innerHTML = `
      <div class="task-board-view">
        <header class="page-header">
          <h1>📋 任务大厅</h1>
          <p class="subtitle">${stats.total} 个任务 · ${stats.pending} 待处理 · ${stats.in_progress} 进行中 · ${stats.done} 已完成</p>
        </header>
        <div class="task-board-actions">
          <button class="btn btn-primary" onclick="window.soloBrave.navigate('task-create')">
            <span>+</span> 创建任务
          </button>
        </div>
        <div class="task-columns">
          ${Object.entries(statusConfig).map(([status, config]) => {
            const statusTasks = tasks.filter(t => t.status === status);
            return `
              <div class="task-column">
                <div class="column-header">
                  <span class="column-dot" style="background: ${config.color}"></span>
                  <span class="column-title">${config.label}</span>
                  <span class="column-count">${statusTasks.length}</span>
                </div>
                <div class="column-tasks">
                  ${statusTasks.map(task => {
                    const emp = empMap.get(task.assigneeId);
                    const priorityColors = { P1: '#FF4757', P2: '#FFA502', P3: '#2ED573' };
                    return `
                      <div class="task-card" data-task-id="${task.id}">
                        <div class="task-priority" style="background: ${priorityColors[task.priority] || '#95A5A6'}">
                          ${task.priority}
                        </div>
                        <h4 class="task-title">${task.title}</h4>
                        <p class="task-desc">${task.description?.substring(0, 50) || ''}...</p>
                        <div class="task-meta">
                          ${emp ? `
                            <div class="task-assignee">
                              <div class="mini-avatar" style="background: ${emp.gradient}">${emp.initial}</div>
                              <span>${emp.name}</span>
                            </div>
                          ` : '<span class="unassigned">未指派</span>'}
                          ${task.deadline ? `<span class="task-deadline">${formatDeadline(task.deadline)}</span>` : ''}
                        </div>
                      </div>
                    `;
                  }).join('')}
                  ${statusTasks.length === 0 ? `<div class="empty-column">暂无任务</div>` : ''}
                </div>
              </div>
            `;
          }).join('')}
        </div>
      </div>
    `;

    // 点击任务卡片跳转到对话
    container.querySelectorAll('.task-card').forEach(card => {
      card.addEventListener('click', () => {
        const taskId = card.dataset.taskId;
        const task = tasks.find(t => t.id === taskId);
        if (task && task.assigneeId) {
          const emp = empMap.get(task.assigneeId);
          if (emp) {
            ns.store.setCurrentEmployee(emp.id);
            ns.navigate('chat');
          }
        }
      });
    });
  };

  ui.renderTaskCreate = function() {
    const container = document.getElementById('main-content');
    const employees = ns.store.get('employees') || [];

    container.innerHTML = `
      <div class="task-create-view">
        <header class="page-header">
          <h1>📝 创建任务</h1>
          <p class="subtitle">填写任务信息，派发给 AI 员工</p>
        </header>
        <form class="task-form" id="task-form">
          <div class="form-group">
            <label>任务标题 *</label>
            <input type="text" id="task-title" placeholder="例如：产品需求文档 - 用户登录模块" required>
          </div>
          <div class="form-group">
            <label>任务描述</label>
            <textarea id="task-desc" rows="4" placeholder="详细描述任务内容、要求和预期产出..."></textarea>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>优先级</label>
              <div class="priority-options">
                <label class="priority-option">
                  <input type="radio" name="priority" value="P1">
                  <span class="priority-label" style="--color: #FF4757">P1 紧急</span>
                </label>
                <label class="priority-option">
                  <input type="radio" name="priority" value="P2" checked>
                  <span class="priority-label" style="--color: #FFA502">P2 重要</span>
                </label>
                <label class="priority-option">
                  <input type="radio" name="priority" value="P3">
                  <span class="priority-label" style="--color: #2ED573">P3 普通</span>
                </label>
              </div>
            </div>
            <div class="form-group">
              <label>截止时间</label>
              <input type="datetime-local" id="task-deadline">
            </div>
          </div>
          <div class="form-group">
            <label>指派给 *</label>
            <div class="assignee-options">
              ${employees.map(emp => `
                <label class="assignee-option">
                  <input type="radio" name="assignee" value="${emp.id}" required>
                  <div class="assignee-card">
                    <div class="mini-avatar" style="background: ${emp.gradient}">${emp.initial}</div>
                    <div class="assignee-info">
                      <span class="assignee-name">${emp.name}</span>
                      <span class="assignee-position">${emp.position}</span>
                    </div>
                  </div>
                </label>
              `).join('')}
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-secondary" onclick="window.soloBrave.navigate('task')">取消</button>
            <button type="submit" class="btn btn-primary">派发任务</button>
          </div>
        </form>
      </div>
    `;

    // 表单提交
    container.querySelector('#task-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const title = document.getElementById('task-title').value;
      const description = document.getElementById('task-desc').value;
      const priority = document.querySelector('input[name="priority"]:checked').value;
      const deadline = document.getElementById('task-deadline').value;
      const assigneeId = document.querySelector('input[name="assignee"]:checked')?.value;

      if (!title || !assigneeId) {
        showToast('请填写必填项', 'error');
        return;
      }

      try {
        showToast('正在派发任务...', 'info');

        // 1. 创建任务
        const task = ns.taskStore.add({
          title,
          description,
          priority,
          deadline: deadline || null,
          assigneeId,
          status: 'pending'
        });

        // 2. 更新 store 中的任务列表
        ns.store.set('tasks', ns.taskStore.getAll());

        // 3. 构建任务消息
        const priorityEmoji = { P1: '🔴 紧急', P2: '🟡 重要', P3: '🟢 普通' };
        const taskMessage = `【新任务指派】

标题：${task.title}
优先级：${priorityEmoji[task.priority] || '普通'}
${task.deadline ? '截止：' + task.deadline : ''}

${task.description || ''}

请确认收到，并简单说明你的执行计划。`;

        // 4. 设置待发消息
        ns.pendingMessage = taskMessage;
        ns.pendingTaskId = task.id;

        // 5. 跳转到对话页面
        ns.store.setCurrentEmployee(assigneeId);
        showToast(`✅ 任务已派发给 ${employees.find(e => e.id === assigneeId)?.name || '员工'}`, 'success');
        ns.navigate('chat');
      } catch (error) {
        showToast('派发失败：' + error.message, 'error');
      }
    });
  };

  function formatDeadline(deadline) {
    const date = new Date(deadline);
    const now = new Date();
    const diffMs = date - now;
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));

    if (diffHours < 0) return '已过期';
    if (diffHours < 24) return `今天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
    if (diffHours < 48) return `明天 ${date.getHours()}:${String(date.getMinutes()).padStart(2, '0')}`;
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

})();