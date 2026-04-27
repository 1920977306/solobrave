/**
 * SoloBrave UI - 办公室页面（仪表盘版）
 */

(function() {
  const ns = window.soloBrave;
  const ui = ns.ui = ns.ui || {};

  ui.renderOffice = function() {
    const container = document.getElementById('main-content');
    const employees = ns.store.get('employees') || [];
    const tasks = ns.store.get('tasks') || [];
    const conversations = ns.store.get('conversations') || {};

    // 统计数据
    const stats = {
      employees: employees.length,
      totalTasks: tasks.length,
      inProgress: tasks.filter(t => t.status === 'in_progress').length,
      done: tasks.filter(t => t.status === 'done').length
    };

    // 最近动态
    const activities = generateActivities(tasks, conversations, employees);

    container.innerHTML = `
      <div class="office-view">
        <header class="page-header office-header">
          <div>
            <h1>🏢 SoloBrave Office</h1>
            <p class="subtitle">你的 AI 员工团队正在工作中</p>
          </div>
          <div class="header-actions">
            <button class="btn btn-secondary" onclick="window.soloBrave.navigate('group')">💬 项目群聊</button>
            <button class="btn btn-secondary" onclick="window.soloBrave.navigate('settings')">⚙️ 设置</button>
            <button class="btn btn-primary" onclick="window.soloBrave.navigate('recruit')">+ 招聘</button>
          </div>
        </header>

        <!-- 概览统计栏 -->
        <div class="stats-bar">
          <div class="stat-card">
            <div class="stat-icon">👥</div>
            <div class="stat-info">
              <span class="stat-number">${stats.employees}</span>
              <span class="stat-label">员工</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">📋</div>
            <div class="stat-info">
              <span class="stat-number">${stats.totalTasks}</span>
              <span class="stat-label">任务</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">🟡</div>
            <div class="stat-info">
              <span class="stat-number">${stats.inProgress}</span>
              <span class="stat-label">进行中</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon">✅</div>
            <div class="stat-info">
              <span class="stat-number">${stats.done}</span>
              <span class="stat-label">已完成</span>
            </div>
          </div>
        </div>

        <!-- 员工工位 -->
        <section class="office-section">
          <h2 class="section-title">员工工位</h2>
          <div class="employees-grid">
            ${employees.map(emp => renderEmployeeCard(emp, tasks, conversations)).join('')}
            <div class="employee-card recruit-card" onclick="window.soloBrave.navigate('recruit')">
              <div class="recruit-icon">+</div>
              <div class="recruit-text">招聘新员工</div>
            </div>
          </div>
        </section>

        <!-- 今日简报 -->
        <section class="office-section">
          <h2 class="section-title">📋 今日简报 · ${new Date().toISOString().slice(0, 10)}</h2>
          <div class="daily-report-list">
            ${employees.map(emp => {
              const empTasks = tasks.filter(t => t.assigneeId === emp.id);
              const today = new Date().toISOString().slice(0, 10);
              const todayDone = empTasks.filter(t => 
                t.status === 'done' && t.updatedAt && t.updatedAt.startsWith(today)
              );
              const active = empTasks.filter(t => 
                t.status === 'in_progress' || t.status === 'review'
              );
              const conv = conversations[emp.id];
              const msgCount = conv?.messages?.filter(m => 
                m.timestamp && m.timestamp.startsWith(today)
              ).length || 0;

              return `
                <div class="report-item">
                  <div class="report-emp">
                    <div class="report-avatar" style="background: ${emp.gradient}">
                      ${emp.initial}
                    </div>
                    <div>
                      <strong>${emp.name}</strong>
                      <span class="report-role">${emp.position}</span>
                    </div>
                  </div>
                  <div class="report-stats">
                    <span>✅ 完成 ${todayDone.length}</span>
                    <span>📋 进行中 ${active.length}</span>
                    <span>💬 对话 ${msgCount}</span>
                  </div>
                  ${active.length > 0 ? `
                    <div class="report-tasks">
                      当前：${active.map(t => t.title).join('、')}
                    </div>
                  ` : ''}
                </div>
              `;
            }).join('')}
          </div>
        </section>

        <!-- 最近动态 -->
        ${activities.length > 0 ? `
          <section class="office-section">
            <h2 class="section-title">最近动态</h2>
            <div class="activity-feed">
              ${activities.slice(0, 5).map(act => `
                <div class="activity-item">
                  <span class="activity-time">${act.time}</span>
                  <span class="activity-icon">${act.icon}</span>
                  <span class="activity-text">${act.text}</span>
                </div>
              `).join('')}
            </div>
          </section>
        ` : ''}
      </div>
    `;

    // 绑定快速派任务弹窗事件
    container.querySelectorAll('.btn-quick-task').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const empId = btn.dataset.id;
        ui.showQuickTaskModal(empId);
      });
    });
  };

  function renderEmployeeCard(emp, allTasks, conversations) {
    const activeTasks = allTasks.filter(t => 
      t.assigneeId === emp.id && 
      (t.status === 'in_progress' || t.status === 'review')
    );

    const conv = conversations[emp.id];
    const messages = conv?.messages || [];
    const lastMessage = messages.length > 0 ? messages[messages.length - 1] : null;
    const lastMessagePreview = lastMessage 
      ? (lastMessage.role === 'user' ? '你: ' : `${emp.name}: `) + lastMessage.content.slice(0, 25) + '...'
      : '暂无对话';
    const lastMessageTime = lastMessage 
      ? formatTimeAgo(lastMessage.timestamp)
      : '';

    const status = activeTasks.length > 0 
      ? { icon: '🟡', text: '任务中', color: '#FFA502' }
      : { icon: '🟢', text: '空闲', color: '#2ED573' };

    return `
      <div class="employee-card office-card" data-id="${emp.id}">
        <div class="card-main" onclick="window.soloBrave.store.setCurrentEmployee('${emp.id}'); window.soloBrave.navigate('chat')">
          <div class="card-avatar" style="background: ${emp.gradient}">
            <span>${emp.initial}</span>
          </div>
          <div class="card-info">
            <h3>${emp.name}</h3>
            <p class="position">${emp.position}</p>
            <span class="emp-status" style="color: ${status.color}">
              ${status.icon} ${status.text}
            </span>
          </div>
        </div>
        
        ${activeTasks.length > 0 ? `
          <div class="card-task-preview">
            <div class="task-preview-title">📋 ${activeTasks[0].title}</div>
            <span class="task-preview-priority ${activeTasks[0].priority}">${activeTasks[0].priority}</span>
            <span class="task-preview-status ${activeTasks[0].status}">
              ${activeTasks[0].status === 'in_progress' ? '进行中' : '待审核'}
            </span>
          </div>
        ` : ''}
        
        <div class="card-last-chat">
          <span class="chat-preview">💬 ${lastMessagePreview}</span>
          ${lastMessageTime ? `<span class="chat-time">${lastMessageTime}</span>` : ''}
        </div>
        
        <div class="card-actions">
          <button class="btn-action" onclick="event.stopPropagation(); window.soloBrave.store.setCurrentEmployee('${emp.id}'); window.soloBrave.navigate('chat')">
            💬 发消息
          </button>
          <button class="btn-action btn-quick-task" data-id="${emp.id}" onclick="event.stopPropagation()">
            📋 派任务
          </button>
        </div>
      </div>
    `;
  }

  function generateActivities(tasks, conversations, employees) {
    const activities = [];
    const empMap = new Map(employees.map(e => [e.id, e]));

    // 任务相关动态
    tasks.slice(0, 10).forEach(task => {
      const emp = empMap.get(task.assigneeId);
      if (!emp) return;

      if (task.status === 'done') {
        activities.push({
          time: formatTimeAgo(task.updatedAt),
          icon: '✅',
          text: `${emp.name} 完成了"${task.title}"`,
          timestamp: task.updatedAt
        });
      } else if (task.status === 'in_progress' && task.dispatchedAt) {
        activities.push({
          time: formatTimeAgo(task.dispatchedAt),
          icon: '📋',
          text: `新任务"${task.title}"派发给 ${emp.name}`,
          timestamp: task.dispatchedAt
        });
      }
    });

    // 对话相关动态
    Object.entries(conversations).forEach(([empId, conv]) => {
      const emp = empMap.get(empId);
      if (!emp || !conv.messages || conv.messages.length === 0) return;

      const lastMsg = conv.messages[conv.messages.length - 1];
      if (lastMsg.role === 'assistant') {
        activities.push({
          time: formatTimeAgo(lastMsg.timestamp),
          icon: '💬',
          text: `${emp.name}: "${lastMsg.content.slice(0, 30)}..."`,
          timestamp: lastMsg.timestamp
        });
      }
    });

    // 按时间排序
    return activities.sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp));
  }

  function formatTimeAgo(isoString) {
    if (!isoString) return '';
    const date = new Date(isoString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / (1000 * 60));
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffMins < 1) return '刚刚';
    if (diffMins < 60) return `${diffMins}分钟前`;
    if (diffHours < 24) return `${diffHours}小时前`;
    if (diffDays < 7) return `${diffDays}天前`;
    return date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
  }

  // ============ 快速派任务弹窗 ============
  ui.showQuickTaskModal = function(employeeId) {
    const employee = ns.employeeStore.getById(employeeId);
    if (!employee) return;

    // 创建弹窗
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal-content quick-task-modal">
        <div class="modal-header">
          <h3>给 ${employee.name} 派任务</h3>
          <button class="modal-close" onclick="this.closest('.modal-overlay').remove()">×</button>
        </div>
        <form id="quick-task-form">
          <div class="form-group">
            <label>任务标题 *</label>
            <input type="text" id="quick-task-title" placeholder="例如：设计登录页面" required>
          </div>
          <div class="form-group">
            <label>简单描述（可选）</label>
            <textarea id="quick-task-desc" rows="2" placeholder="一句话描述任务内容..."></textarea>
          </div>
          <div class="form-group">
            <label>优先级</label>
            <div class="priority-options">
              <label class="priority-option">
                <input type="radio" name="quick-priority" value="P1">
                <span class="priority-label" style="--color: #FF4757">P1 紧急</span>
              </label>
              <label class="priority-option">
                <input type="radio" name="quick-priority" value="P2" checked>
                <span class="priority-label" style="--color: #FFA502">P2 重要</span>
              </label>
              <label class="priority-option">
                <input type="radio" name="quick-priority" value="P3">
                <span class="priority-label" style="--color: #2ED573">P3 普通</span>
              </label>
            </div>
          </div>
          <div class="form-actions">
            <button type="button" class="btn btn-secondary" onclick="this.closest('.modal-overlay').remove()">取消</button>
            <button type="submit" class="btn btn-primary">派发</button>
          </div>
        </form>
      </div>
    `;

    document.body.appendChild(modal);

    // 表单提交
    modal.querySelector('#quick-task-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const title = document.getElementById('quick-task-title').value;
      const description = document.getElementById('quick-task-desc').value;
      const priority = document.querySelector('input[name="quick-priority"]:checked').value;

      if (!title) return;

      try {
        // 创建任务
        const task = ns.taskStore.add({
          title,
          description,
          priority,
          deadline: null,
          assigneeId: employeeId,
          status: 'pending'
        });
        ns.store.set('tasks', ns.taskStore.getAll());

        // 构建任务消息
        const priorityEmoji = { P1: '🔴 紧急', P2: '🟡 重要', P3: '🟢 普通' };
        const taskMessage = `【新任务指派】

标题：${task.title}
优先级：${priorityEmoji[task.priority] || '普通'}

${task.description || ''}

请确认收到，并简单说明你的执行计划。`;

        // 设置待发消息
        ns.pendingMessage = taskMessage;
        ns.pendingTaskId = task.id;

        // 关闭弹窗
        modal.remove();

        // 跳转到对话
        ns.store.setCurrentEmployee(employeeId);
        showToast(`✅ 任务已派发给 ${employee.name}`, 'success');
        ns.navigate('chat');
      } catch (error) {
        showToast('派发失败：' + error.message, 'error');
      }
    });
  };

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

})();