/**
 * SoloBrave App - 应用入口（普通脚本版本）
 */

(function() {
  const ns = window.soloBrave = window.soloBrave || {};

  // ============ 初始化 ============
  function init() {
    console.log('[SoloBrave] 初始化中...');

    // 1. 检查首次使用
    checkFirstUse();

    // 2. 绑定导航事件
    bindNavEvents();

    // 3. 渲染初始视图
    navigate('office');

    console.log('[SoloBrave] 初始化完成');
  }

  function checkFirstUse() {
    const employees = ns.store.get('employees') || [];
    if (employees.length === 0) {
      console.log('[SoloBrave] 首次使用，创建默认员工 Lucy');
      const lucy = ns.employeeStore.createFromTemplate('product_manager', {
        name: 'Lucy'
      });
      ns.store.set('employees', ns.employeeStore.getAll());
      ns.store.setCurrentEmployee(lucy.id);
    }

    // 首次使用引导：检查 API Key
    const aiConfig = ns.store.get('aiConfig');
    if (!aiConfig || !aiConfig.apiKey) {
      // 延迟显示引导，等页面渲染完成
      setTimeout(() => {
        showApiKeyGuide();
      }, 500);
    }
  }

  function showApiKeyGuide() {
    const container = document.getElementById('main-content');
    const guideHtml = `
      <div class="api-guide-overlay" id="api-guide" style="
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.6); z-index: 2000;
        display: flex; align-items: center; justify-content: center;
        padding: 20px;
      ">
        <div style="
          background: var(--bg-primary); border-radius: 16px;
          max-width: 480px; width: 100%; padding: 32px;
          box-shadow: 0 20px 60px rgba(0,0,0,0.3);
        ">
          <h2 style="margin: 0 0 8px 0; font-size: 22px;">🚀 欢迎使用 SoloBrave</h2>
          <p style="color: var(--text-secondary); margin-bottom: 24px;">
            你的 AI 员工团队已就绪，只需配置 API Key 即可开始对话。
          </p>

          <div style="background: var(--bg-secondary); border-radius: 12px; padding: 20px; margin-bottom: 24px;">
            <h4 style="margin: 0 0 12px 0; font-size: 14px; color: var(--text-secondary);">
              推荐：智谱 AI（免费额度充足）
            </h4>
            <ol style="margin: 0; padding-left: 20px; color: var(--text-secondary); font-size: 13px; line-height: 1.8;">
              <li>访问 <a href="https://open.bigmodel.cn" target="_blank" style="color: var(--sb-primary);">open.bigmodel.cn</a></li>
              <li>注册账号 → 创建 API Key</li>
              <li>复制 Key 粘贴到下方</li>
            </ol>
          </div>

          <div style="margin-bottom: 20px;">
            <label style="display: block; font-size: 13px; font-weight: 500; margin-bottom: 6px;">API Key</label>
            <input type="text" id="guide-api-key" placeholder="粘贴你的 API Key" style="
              width: 100%; padding: 12px; border-radius: 8px;
              border: 1px solid var(--border-color); background: var(--bg-primary);
              color: var(--text-primary); font-size: 14px;
            ">
          </div>

          <div style="display: flex; gap: 12px;">
            <button onclick="window.soloBrave.saveApiKeyFromGuide()" style="
              flex: 1; padding: 12px; border-radius: 8px;
              background: var(--sb-primary); color: white;
              border: none; font-size: 14px; font-weight: 500; cursor: pointer;
            ">保存并开始</button>
            <button onclick="document.getElementById('api-guide').remove()" style="
              padding: 12px 20px; border-radius: 8px;
              background: var(--bg-tertiary); color: var(--text-secondary);
              border: none; font-size: 14px; cursor: pointer;
            ">稍后再说</button>
          </div>
        </div>
      </div>
    `;
    document.body.insertAdjacentHTML('beforeend', guideHtml);
  }

  ns.saveApiKeyFromGuide = function() {
    const key = document.getElementById('guide-api-key').value.trim();
    if (!key) {
      alert('请输入 API Key');
      return;
    }

    ns.store.set('aiConfig', {
      provider: 'zhipu',
      baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
      model: 'glm-4-flash',
      apiKey: key
    });

    document.getElementById('api-guide').remove();

    // 更新侧边栏状态
    const statusEl = document.getElementById('api-status');
    if (statusEl) {
      statusEl.innerHTML = '<span class="status-dot" style="background: #2ED573;"></span><span class="status-text">已连接</span>';
    }

    // 显示成功提示
    if (ns.ui && ns.ui.showToast) {
      ns.ui.showToast('API Key 已保存，可以开始对话了！');
    }
  };

  function bindNavEvents() {
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.dataset.view;
        navigate(view);
      });
    });
  }

  // ============ 路由 ============
  function navigate(page) {
    ns.store.set('currentView', page);

    // 更新导航状态
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.view === page);
    });

    // 渲染对应页面
    switch(page) {
      case 'office':
        ns.ui.renderOffice();
        break;
      case 'chat':
        ns.ui.renderChat();
        break;
      case 'task':
        ns.ui.renderTaskBoard();
        break;
      case 'task-create':
        ns.ui.renderTaskCreate();
        break;
      case 'recruit':
        ns.ui.renderRecruit();
        break;
      case 'settings':
        ns.ui.renderSettings();
        break;
      case 'group':
        ns.ui.renderGroupChat();
        break;
      default:
        console.warn('Unknown page:', page);
    }
  }

  // ============ 公共 API ============
  ns.navigate = navigate;

  /**
   * 发送消息（流式真实 AI）
   */
  ns.sendMessage = async function(employeeId, content) {
    const employee = ns.employeeStore.getById(employeeId);
    if (!employee) throw new Error('员工不存在');

    const config = ns.store.get('aiConfig');
    if (!config || !config.apiKey) {
      throw new Error('未配置 AI，请先在设置中配置 API Key');
    }

    // 1. 添加用户消息
    const userMessage = {
      id: ns.generateId('msg'),
      role: 'user',
      content: content,
      timestamp: new Date().toISOString()
    };
    ns.store.addMessage(employeeId, userMessage);

    // 2. 获取对话上下文
    const conversations = ns.store.get('conversations') || {};
    const conversation = conversations[employeeId] || { messages: [] };
    const contextMessages = conversation.messages.slice(-20);

    // 3. 构建系统提示词
    let systemPrompt = ns.employeeStore.getSystemPrompt(employeeId);
    
    // 注入记忆
    const memoryText = ns.memoryStore.getPromptText(employeeId);
    if (memoryText) {
      systemPrompt += memoryText;
    }

    // 注入知识库
    try {
      const results = await ns.knowledgeStore.search(employeeId, content);
      if (results.length > 0) {
        systemPrompt += '\n\n【相关参考资料】\n';
        results.forEach(r => {
          systemPrompt += `\n来源：${r.doc_name}\n${r.content}\n`;
        });
        systemPrompt += '\n请参考以上资料回答，但不要说"根据资料"。';
      }
    } catch(e) {
      // 知识库未初始化时静默跳过
    }

    // 4. 构建消息数组
    const messages = [
      { role: 'system', content: systemPrompt },
      ...contextMessages.map(msg => ({
        role: msg.role === 'assistant' ? 'assistant' : 'user',
        content: msg.content
      }))
    ];

    // 5. 创建占位消息（思考中）并立即显示
    const assistantMessage = {
      id: ns.generateId('msg'),
      role: 'assistant',
      content: '思考中...',
      timestamp: new Date().toISOString()
    };
    
    // 立即触发 UI 渲染显示占位消息
    if (ns.ui && ns.ui.renderChat) {
      ns.ui.renderChat();
    }

    // 6. 流式调用 AI
    try {
      const response = await fetch(`${config.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${config.apiKey}`
        },
        body: JSON.stringify({
          model: config.model,
          messages: messages,
          stream: true
        })
      });

      if (!response.ok) {
        throw new Error(`API 错误: ${response.status}`);
      }

      // 7. 流式读取并更新消息
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let fullContent = '';
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || !trimmed.startsWith('data:')) continue;
          const data = trimmed.slice(5).trim();
          if (data === '[DONE]') continue;

          try {
            const parsed = JSON.parse(data);
            const delta = parsed.choices?.[0]?.delta?.content;
            if (delta) {
              fullContent += delta;
              // 更新消息内容
              assistantMessage.content = fullContent;
              // 触发 UI 更新
              if (ns.ui && ns.ui.updateChatMessage) {
                ns.ui.updateChatMessage(employeeId, assistantMessage.id, fullContent);
              }
            }
          } catch (e) {}
        }
      }

      // 8. 保存最终消息
      assistantMessage.content = fullContent || '（无回复内容）';

    } catch (error) {
      console.error('AI 回复失败:', error);
      assistantMessage.content = `回复失败：${error.message}。请检查设置中的 API 配置。`;
      throw error;
    } finally {
      // 保存消息到 store
      ns.store.addMessage(employeeId, assistantMessage);
    }

    // 9. 自动提取记忆（每 5 轮对话触发一次）
    if (conversation.messages.length % 5 === 0) {
      extractAndSaveMemory(employeeId, conversation.messages);
    }

    return assistantMessage;
  };

  /**
   * 自动提取并保存记忆
   */
  async function extractAndSaveMemory(employeeId, messages) {
    try {
      // 构建对话文本
      const conversationText = messages.slice(-10).map(m => `${m.role}: ${m.content}`).join('\n');
      
      // 调用 AI 提取
      const memories = await callAIForExtraction(conversationText);
      
      if (memories && memories.length > 0) {
        let newCount = 0;
        
        for (const memory of memories) {
          // 检查是否已存在相似记忆
          const existing = ns.memoryStore.getByEmployee(employeeId);
          const isDuplicate = existing.some(m => 
            m.content.toLowerCase().includes(memory.content.toLowerCase()) ||
            memory.content.toLowerCase().includes(m.content.toLowerCase())
          );
          
          if (!isDuplicate) {
            ns.memoryStore.add(employeeId, {
              type: memory.type,
              content: memory.content
            });
            newCount++;
          }
        }
        
        if (newCount > 0) {
          console.log(`[Memory] 提取到 ${newCount} 条新记忆`);
        }
      }
    } catch (error) {
      console.error('[Memory] 提取记忆失败:', error);
    }
  }

  /**
   * 调用 AI 提取记忆
   */
  async function callAIForExtraction(conversationText) {
    const config = ns.store.get('aiConfig');
    if (!config || !config.apiKey) {
      console.log('[Memory] 未配置 AI，跳过记忆提取');
      return [];
    }

    try {
      const response = await fetch(`${config.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${config.apiKey}`
        },
        body: JSON.stringify({
          model: config.model,
          messages: [
            {
              role: 'system',
              content: `你是一个信息提取助手。从对话中提取关于用户的关键信息。

返回格式（严格按此格式，每行一条）：
- [类型] 内容

类型只能是以下之一：
- project（项目信息）
- preference（用户偏好）
- decision（重要决策）
- fact（用户相关事实）
- todo（待办事项）

规则：
- 只提取用户侧的信息，忽略 AI 的回复内容
- 每条不超过 50 字
- 最多提取 5 条
- 如果没有值得记录的信息，返回：无`
            },
            {
              role: 'user',
              content: `请从以下对话中提取关键信息：\n\n${conversationText}`
            }
          ],
          temperature: 0.3,
          max_tokens: 500
        })
      });

      if (!response.ok) {
        console.error('[Memory] 提取失败:', response.status);
        return [];
      }

      const result = await response.json();
      const content = result.choices?.[0]?.message?.content || '';

      // 解析 AI 返回的记忆条目
      if (content.includes('无') && content.length < 10) return [];

      const memories = [];
      const lines = content.split('\n');

      for (const line of lines) {
        const match = line.match(/[-•]\s*\[(\w+)\]\s*(.+)/);
        if (match) {
          memories.push({
            type: match[1],
            content: match[2].trim()
          });
        }
      }

      return memories;
    } catch (error) {
      console.error('[Memory] API 调用失败:', error);
      return [];
    }
  }

  /**
   * 创建并派发任务
   */
  ns.createAndDispatchTask = async function(taskData) {
    const task = {
      title: taskData.title,
      description: taskData.description || '',
      priority: taskData.priority || 'P2',
      status: 'pending',
      assigneeId: taskData.assigneeId,
      deadline: taskData.deadline || null,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };

    // 保存任务
    ns.taskStore.add(task);
    ns.store.set('tasks', ns.taskStore.getAll());

    // 发送任务消息给员工
    const employee = ns.employeeStore.getById(taskData.assigneeId);
    if (employee) {
      const taskMessage = {
        id: ns.generateId('msg'),
        role: 'user',
        content: `【新任务】${task.title}\n\n${task.description || ''}\n\n优先级：${task.priority}${task.deadline ? ' | 截止：' + task.deadline : ''}`,
        timestamp: new Date().toISOString()
      };
      ns.store.addMessage(taskData.assigneeId, taskMessage);
    }

    return { task, message: '任务已创建并派发' };
  };

  /**
   * 更新任务状态
   */
  ns.updateTaskStatus = function(taskId, status) {
    const task = ns.taskStore.updateStatus(taskId, status);
    ns.store.set('tasks', ns.taskStore.getAll());
    return task;
  };

  /**
   * 获取任务统计
   */
  ns.getTaskStats = function() {
    return ns.taskStore.getStats();
  };

  /**
   * 招聘员工
   */
  ns.hireEmployee = function(templateKey, config) {
    const employee = ns.employeeStore.createFromTemplate(templateKey, config);
    ns.store.set('employees', ns.employeeStore.getAll());
    return employee;
  };

  /**
   * 解雇员工
   */
  ns.fireEmployee = function(employeeId) {
    ns.employeeStore.remove(employeeId);
    ns.store.set('employees', ns.employeeStore.getAll());
  };

  // ============ 启动 ============
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();