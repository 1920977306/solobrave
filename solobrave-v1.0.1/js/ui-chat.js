/**
 * SoloBrave UI - 对话页面
 */

(function() {
  const ns = window.soloBrave;
  const ui = ns.ui = ns.ui || {};

  ui.renderChat = function() {
    const container = document.getElementById('main-content');
    const currentEmp = ns.store.get('currentEmployee');

    if (!currentEmp) {
      container.innerHTML = `
        <div class="placeholder">
          <h2>💬 对话广场</h2>
          <p>请先选择一个员工开始对话</p>
          <button class="btn btn-primary" onclick="window.soloBrave.navigate('office')">去办公室选择</button>
        </div>
      `;
      return;
    }

    const conversations = ns.store.get('conversations') || {};
    const conversation = conversations[currentEmp.id] || { messages: [] };

    // 获取该员工的活跃任务
    const activeTasks = ns.taskStore.getByAssignee(currentEmp.id)
      .filter(t => t.status === 'in_progress' || t.status === 'review');

    container.innerHTML = `
      <div class="chat-view">
        <header class="chat-header">
          <div class="chat-employee">
            <div class="mini-avatar" style="background: ${currentEmp.gradient}">${currentEmp.initial}</div>
            <div class="chat-employee-info">
              <h3>${currentEmp.name}</h3>
              <span class="position">${currentEmp.position}</span>
            </div>
          </div>
          <button class="kb-entry-btn" onclick="window.soloBrave.ui.renderKnowledgePanel('${currentEmp.id}')" title="知识库">📚</button>
        </header>
        ${activeTasks.length > 0 ? `
          <div class="task-bar-container">
            ${activeTasks.map(task => `
              <div class="task-bar" data-task-id="${task.id}">
                <div class="task-bar-info">
                  <span class="task-bar-status ${task.status}">
                    ${task.status === 'in_progress' ? '进行中' : '待审核'}
                  </span>
                  <span class="task-bar-title">${task.title}</span>
                  <span class="task-bar-priority ${task.priority}">${task.priority}</span>
                </div>
                <div class="task-bar-actions">
                  ${task.status === 'in_progress' 
                    ? `<button class="btn-task-action" onclick="window.soloBrave.ui.updateTaskStatus('${task.id}', 'review')">标为待审核</button>
                       <button class="btn-task-action primary" onclick="window.soloBrave.ui.updateTaskStatus('${task.id}', 'done')">直接完成</button>`
                    : `<button class="btn-task-action primary" onclick="window.soloBrave.ui.updateTaskStatus('${task.id}', 'done')">确认完成</button>
                       <button class="btn-task-action" onclick="window.soloBrave.ui.updateTaskStatus('${task.id}', 'in_progress')">打回修改</button>`
                  }
                </div>
              </div>
            `).join('')}
          </div>
        ` : ''}
        <div class="chat-messages" id="chat-messages"></div>
        <div class="chat-input-area">
          <button class="upload-doc-btn" id="upload-doc-btn" title="上传文档到知识库">📎</button>
          <textarea id="chat-input" placeholder="和 ${currentEmp.name} 说点什么..." rows="1"></textarea>
          <button class="send-btn" id="send-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="22" y1="2" x2="11" y2="13"></line>
              <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
            </svg>
          </button>
        </div>
      </div>
    `;

    // 渲染消息
    renderMessages(currentEmp, conversation);

    // 绑定事件
    const input = container.querySelector('#chat-input');
    const sendBtn = container.querySelector('#send-btn');

    const doSend = async () => {
      const content = input.value.trim();
      if (!content) return;
      input.value = '';
      input.disabled = true;
      sendBtn.disabled = true;

      try {
        await ns.sendMessage(currentEmp.id, content);
        // 重新渲染消息
        ui.renderChat();
      } catch (error) {
        showToast(error.message, 'error');
      } finally {
        input.disabled = false;
        sendBtn.disabled = false;
        input.focus();
      }
    };

    sendBtn.addEventListener('click', doSend);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        doSend();
      }
    });

    // 绑定上传按钮事件
    const uploadBtn = container.querySelector('#upload-doc-btn');
    if (uploadBtn) {
      uploadBtn.addEventListener('click', () => {
        ui.uploadDocument(currentEmp.id);
      });
    }

    // 滚动到底部
    const messagesContainer = container.querySelector('#chat-messages');
    if (messagesContainer) {
      messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    // 检查是否有待发消息（来自任务派发）
    if (ns.pendingMessage && ns.pendingTaskId) {
      const message = ns.pendingMessage;
      const taskId = ns.pendingTaskId;

      // 清除待发状态
      ns.pendingMessage = null;
      ns.pendingTaskId = null;

      // 自动发送任务消息
      setTimeout(() => {
        sendTaskMessage(currentEmp.id, message, taskId);
      }, 500);
    }
  };

  async function sendTaskMessage(employeeId, text, taskId) {
    const employee = ns.employeeStore.getById(employeeId);
    if (!employee) return;

    // 1. 添加用户消息（任务指派）
    const userMessage = {
      id: ns.generateId('msg'),
      role: 'user',
      content: text,
      timestamp: new Date().toISOString()
    };
    ns.store.addMessage(employeeId, userMessage);

    // 2. 重新渲染对话显示任务消息
    ui.renderChat();

    // 3. 显示"正在输入"
    showTypingIndicator();

    // 4. 调用 AI 获取回复
    try {
      const response = await callAI(employee, text);

      // 5. 移除"正在输入"，显示 AI 回复
      hideTypingIndicator();

      const assistantMessage = {
        id: ns.generateId('msg'),
        role: 'assistant',
        content: response,
        timestamp: new Date().toISOString()
      };
      ns.store.addMessage(employeeId, assistantMessage);

      // 6. 更新任务状态为"进行中"
      if (taskId) {
        ns.taskStore.dispatch(taskId);
        ns.store.set('tasks', ns.taskStore.getAll());
      }

      // 7. 更新对话摘要
      updateConversationSummary(employeeId);

      // 8. 重新渲染
      ui.renderChat();
    } catch (error) {
      hideTypingIndicator();
      showToast('AI 回复失败：' + error.message, 'error');
    }
  }

  function showTypingIndicator() {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const typingDiv = document.createElement('div');
    typingDiv.className = 'message assistant typing-indicator';
    typingDiv.id = 'typing-indicator';
    typingDiv.innerHTML = `
      <div class="message-avatar" style="background: #667eea"><span>...</span></div>
      <div class="message-content">
        <div class="message-text">正在思考...</div>
      </div>
    `;
    container.appendChild(typingDiv);
    container.scrollTop = container.scrollHeight;
  }

  function hideTypingIndicator() {
    const indicator = document.getElementById('typing-indicator');
    if (indicator) indicator.remove();
  }

  // ============ 流式更新消息 ============
  ui.updateChatMessage = function(employeeId, messageId, content) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    // 查找消息元素
    let msgEl = container.querySelector(`[data-msg-id="${messageId}"]`);
    if (msgEl) {
      // 更新现有消息
      const textEl = msgEl.querySelector('.message-text');
      if (textEl) {
        textEl.textContent = content;
      }
    }

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
  };

  async function callAI(employee, text) {
    // 模拟 AI 调用（后续接入真实 AI Gateway）
    // 根据员工角色生成不同的回复风格
    const responses = {
      '产品经理': [
        '收到！这个需求我已经理解了，我先梳理一下用户场景和核心流程，稍后给你一份详细的PRD。',
        '好的，我来负责这个需求。我会先进行竞品分析，然后输出产品方案。',
        '明白！我会从用户痛点出发，设计最简洁的解决方案。'
      ],
      '技术架构师': [
        '已收到任务。我先评估一下技术可行性，然后给出架构设计方案。',
        '收到。我会先梳理技术难点，然后输出详细的技术方案。',
        '明白。我会从系统稳定性、扩展性、性能三个维度来设计。'
      ],
      '设计师': [
        '收到！我先构思一下视觉方向，然后出几版方案给你选。',
        '好的，我会先研究一下目标用户的审美偏好，然后设计界面。',
        '明白！我会确保设计既美观又易用。'
      ],
      '前端开发': [
        '收到！我先看一下需求，然后评估工时。',
        '好的，我会按照设计稿实现，确保交互流畅。',
        '明白。我会注意代码质量和性能优化。'
      ]
    };

    const position = employee.position || '助理';
    const positionResponses = responses[position] || [
      '收到任务！我会尽快处理。',
      '好的，我来负责这个任务。',
      '明白！马上开始工作。'
    ];

    // 模拟延迟
    await new Promise(resolve => setTimeout(resolve, 1000 + Math.random() * 2000));

    return positionResponses[Math.floor(Math.random() * positionResponses.length)];
  }

  function renderMessages(employee, conversation) {
    const container = document.getElementById('chat-messages');
    if (!container) return;

    const messages = conversation.messages || [];

    if (messages.length === 0) {
      container.innerHTML = '';
      const welcome = document.createElement('div');
      welcome.className = 'welcome-message';
      welcome.innerHTML = `
        <div class="welcome-avatar" style="background: ${employee.gradient}">
          <span>${employee.initial}</span>
        </div>
        <h3>你好，我是 ${employee.name}</h3>
        <p>你的 ${employee.position}，有什么我可以帮你的吗？</p>
      `;
      container.appendChild(welcome);
      return;
    }

    // 清空并重新渲染所有消息
    container.innerHTML = '';
    messages.forEach(msg => {
      const msgDiv = document.createElement('div');
      msgDiv.className = 'message ' + msg.role;
      msgDiv.setAttribute('data-msg-id', msg.id);

      const isAssistant = msg.role === 'assistant';
      const avatarBg = isAssistant ? employee.gradient : '#667eea';
      const avatarText = isAssistant ? employee.initial : '你';
      
      msgDiv.innerHTML = `
        <div class="message-avatar" style="background: ${avatarBg}">
          <span>${avatarText}</span>
        </div>
        <div class="message-content">
          <div class="message-text"></div>
          <span class="message-time">${formatTime(msg.timestamp)}</span>
        </div>
      `;

      // 使用 textContent 避免 HTML 注入
      const textEl = msgDiv.querySelector('.message-text');
      if (textEl) {
        textEl.textContent = msg.content;
      }
      container.appendChild(msgDiv);
    });

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
  }

  function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  function formatTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }

  function updateConversationSummary(employeeId) {
    const conversations = ns.store.get('conversations') || {};
    const conv = conversations[employeeId];
    if (!conv || !conv.messages || conv.messages.length === 0) return;

    // 取最后一条 AI 消息作为摘要
    const lastAiMessage = [...conv.messages]
      .reverse()
      .find(m => m.role === 'assistant');

    if (lastAiMessage) {
      conv.summary = lastAiMessage.content.slice(0, 80);
      conv.lastActive = new Date().toISOString();
      ns.store.set('conversations', conversations);
    }
  }

  function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => toast.remove(), 3000);
  }

  // ============ 任务状态操作 ============
  ui.updateTaskStatus = function(taskId, newStatus) {
    const task = ns.taskStore.getById(taskId);
    if (!task) return;

    // 更新任务状态
    ns.taskStore.updateStatus(taskId, newStatus);
    ns.store.set('tasks', ns.taskStore.getAll());

    // 状态变化时发消息给 AI
    const employee = ns.employeeStore.getById(task.assigneeId);
    if (employee) {
      let message = '';
      if (newStatus === 'done') {
        message = `✅ 任务"${task.title}"已确认完成，辛苦了！`;
      } else if (newStatus === 'review') {
        message = `📋 任务"${task.title}"已提交审核，请等待确认。`;
      } else if (newStatus === 'in_progress') {
        message = `🔄 任务"${task.title}"需要修改，请调整后重新提交。`;
      }

      if (message) {
        const systemMessage = {
          id: ns.generateId('msg'),
          role: 'user',
          content: message,
          timestamp: new Date().toISOString()
        };
        ns.store.addMessage(employee.id, systemMessage);
      }
    }

    // 刷新对话页面
    ui.renderChat();

    // 显示提示
    const statusLabels = {
      'done': '已完成',
      'review': '待审核',
      'in_progress': '进行中'
    };
    showToast(`任务已标为${statusLabels[newStatus]}`, 'success');
  };

  // ============ 文档上传 ============
  ui.uploadDocument = function(employeeId) {
    if (!employeeId) {
      showToast('请先选择一个员工', 'error');
      return;
    }

    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.txt,.md,.csv,.json';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      try {
        const doc = await ns.knowledgeStore.addDocument(employeeId, file);
        showToast(`文档"${doc.name}"已上传，共 ${doc.chunks.length} 个片段`, 'success');
      } catch(err) {
        showToast('上传失败: ' + err.message, 'error');
      }
    };
    input.click();
  };

  // ============ 知识库管理页面 ============
  ui.renderKnowledgePanel = async function(employeeId) {
    const docs = await ns.knowledgeStore.getDocuments(employeeId);
    const container = document.getElementById('main-content');
    const employee = ns.employeeStore.getById(employeeId);

    const docListHtml = docs.length === 0
      ? '<div class="kb-empty">暂无文档，点击下方按钮上传</div>'
      : docs.map(doc => `
        <div class="kb-doc-item">
          <div class="kb-doc-icon">📄</div>
          <div class="kb-doc-info">
            <div class="kb-doc-name">${escapeHtml(doc.name)}</div>
            <div class="kb-doc-meta">
              ${formatSize(doc.size)} · ${doc.chunkCount} 个片段 · ${timeAgo(doc.createdAt)}
            </div>
          </div>
          <button class="kb-doc-delete" onclick="window.soloBrave.ui.deleteDocument('${doc.id}', '${employeeId}')">删除</button>
        </div>
      `).join('');

    container.innerHTML = `
      <div class="kb-page">
        <div class="kb-header">
          <button class="back-btn" onclick="window.soloBrave.ui.renderChat()">← 返回对话</button>
          <h2>知识库 · ${employee ? employee.name : ''}</h2>
        </div>

        <div class="kb-doc-list">
          ${docListHtml}
        </div>

        <button class="kb-upload-btn" onclick="window.soloBrave.ui.uploadFromKB('${employeeId}')">
          + 上传新文档
        </button>

        <div class="kb-tips">
          支持 .txt .md .csv .json 格式，单文件建议不超过 100KB
        </div>
      </div>
    `;
  };

  ui.deleteDocument = async function(docId, employeeId) {
    if (!confirm('确定删除这份文档？删除后 Lucy 将无法引用它。')) return;
    await ns.knowledgeStore.removeDocument(docId);
    ui.renderKnowledgePanel(employeeId);
  };

  ui.uploadFromKB = function(employeeId) {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.txt,.md,.csv,.json';
    input.onchange = async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        await ns.knowledgeStore.addDocument(employeeId, file);
        ui.renderKnowledgePanel(employeeId);
      } catch(err) {
        showToast('上传失败: ' + err.message, 'error');
      }
    };
    input.click();
  };

  function formatSize(bytes) {
    if (bytes < 1024) return bytes + 'B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + 'KB';
    return (bytes / 1024 / 1024).toFixed(1) + 'MB';
  }

  function timeAgo(dateStr) {
    const diff = Date.now() - new Date(dateStr).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return '刚刚';
    if (mins < 60) return mins + '分钟前';
    const hours = Math.floor(mins / 60);
    if (hours < 24) return hours + '小时前';
    return Math.floor(hours / 24) + '天前';
  }

  // ============ 项目群聊 ============
  ui.renderGroupChat = function() {
    const employees = ns.employeeStore.getAll();
    const container = document.getElementById('main-content');

    // 群聊消息从 localStorage 读取
    const groupMessages = JSON.parse(
      localStorage.getItem('solobrave_group_chat') || '[]'
    );

    const messagesHtml = groupMessages.map(msg => {
      if (msg.role === 'user') {
        return `
          <div class="group-msg group-msg-user">
            <div class="group-msg-sender">你</div>
            <div class="group-msg-content">${escapeHtml(msg.content)}</div>
          </div>`;
      }
      const emp = employees.find(e => e.id === msg.employee_id);
      return `
        <div class="group-msg group-msg-ai">
          <div class="group-msg-avatar" style="background: ${emp ? emp.gradient : '#667eea'}">
            ${emp ? emp.initial : '?'}
          </div>
          <div class="group-msg-body">
            <div class="group-msg-sender">${emp ? emp.name : '未知'} · ${emp ? emp.position : ''}</div>
            <div class="group-msg-content">${escapeHtml(msg.content)}</div>
          </div>
        </div>`;
    }).join('');

    const memberTags = employees.map(emp =>
      `<span class="group-member-tag" style="border-color: ${emp.gradient}">${emp.initial} ${emp.name}</span>`
    ).join('');

    container.innerHTML = `
      <div class="group-chat-page">
        <div class="group-chat-header">
          <button class="back-btn" onclick="window.soloBrave.navigate('office')">← 返回</button>
          <h2>💬 项目群聊</h2>
          <div class="group-members">${memberTags}</div>
        </div>

        <div class="group-chat-messages" id="group-messages">
          ${messagesHtml || '<div class="group-empty">发送一条消息，所有员工都会回复你</div>'}
        </div>

        <div class="group-chat-input">
          <input type="text" id="group-input" placeholder="对所有人说..."
                 onkeydown="if(event.key==='Enter')window.soloBrave.ui.sendGroupMessage()">
          <button class="btn btn-primary" onclick="window.soloBrave.ui.sendGroupMessage()">发送</button>
        </div>
      </div>
    `;

    // 滚动到底部
    const msgContainer = document.getElementById('group-messages');
    if (msgContainer) msgContainer.scrollTop = msgContainer.scrollHeight;
  };

  ui.sendGroupMessage = async function() {
    const input = document.getElementById('group-input');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';

    const employees = ns.employeeStore.getAll();
    if (employees.length === 0) return;

    // 保存用户消息
    const groupMessages = JSON.parse(
      localStorage.getItem('solobrave_group_chat') || '[]'
    );
    groupMessages.push({
      role: 'user',
      content: text,
      time: new Date().toISOString()
    });
    localStorage.setItem('solobrave_group_chat', JSON.stringify(groupMessages));

    // 先刷新显示用户消息
    ui.renderGroupChat();

    const config = ns.store.get('aiConfig');
    if (!config || !config.apiKey) {
      showToast('未配置 AI，请先在设置中配置 API Key', 'error');
      return;
    }

    // 依次让每个 AI 回复
    for (const emp of employees) {
      try {
        // 加入"正在输入"提示
        ui.addGroupTyping(emp);

        // 获取该员工的个人对话历史作为上下文
        const conversations = ns.store.get('conversations') || {};
        const conversation = conversations[emp.id] || { messages: [] };
        const history = conversation.messages.slice(-10);

        // 构建系统提示词
        let systemPrompt = ns.employeeStore.getSystemPrompt(emp.id);
        
        // 注入记忆
        const memoryText = ns.memoryStore.getPromptText(emp.id);
        if (memoryText) {
          systemPrompt += memoryText;
        }

        // 追加群聊指令
        systemPrompt += `\n\n【当前场景】这是项目群聊，团队成员包括：${employees.map(e => `${e.name}(${e.position})`).join('、')}。请从你的专业角度简短回复，100字以内。不要重复其他人说过的内容。`;

        // 构建消息数组
        const messages = [
          { role: 'system', content: systemPrompt },
          ...history.map(msg => ({
            role: msg.role === 'assistant' ? 'assistant' : 'user',
            content: msg.content
          })),
          { role: 'user', content: text }
        ];

        // 调用 AI（非流式）
        const response = await fetch(`${config.baseUrl}/chat/completions`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${config.apiKey}`
          },
          body: JSON.stringify({
            model: config.model,
            messages: messages,
            stream: false,
            max_tokens: 300
          })
        });

        const result = await response.json();
        const content = result.choices?.[0]?.message?.content || '（无回复）';

        // 移除"正在输入"，保存回复
        ui.removeGroupTyping(emp.id);
        groupMessages.push({
          role: 'ai',
          employee_id: emp.id,
          content: content,
          time: new Date().toISOString()
        });
        localStorage.setItem('solobrave_group_chat', JSON.stringify(groupMessages));

        // 逐个显示
        ui.renderGroupChat();

      } catch(err) {
        ui.removeGroupTyping(emp.id);
        groupMessages.push({
          role: 'ai',
          employee_id: emp.id,
          content: `回复失败：${err.message}`,
          time: new Date().toISOString()
        });
        localStorage.setItem('solobrave_group_chat', JSON.stringify(groupMessages));
        ui.renderGroupChat();
      }
    }
  };

  ui.addGroupTyping = function(emp) {
    const container = document.getElementById('group-messages');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'group-msg group-msg-ai group-typing';
    el.id = `typing-${emp.id}`;
    el.innerHTML = `
      <div class="group-msg-avatar" style="background: ${emp.gradient}">
        ${emp.initial}
      </div>
      <div class="group-msg-body">
        <div class="group-msg-sender">${emp.name} · ${emp.position}</div>
        <div class="group-msg-content">正在输入...</div>
      </div>`;
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  };

  ui.removeGroupTyping = function(empId) {
    const el = document.getElementById(`typing-${empId}`);
    if (el) el.remove();
  };

})();