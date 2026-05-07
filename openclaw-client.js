/**
 * OpenClaw WebSocket Client
 * 连接地址: ws://192.168.1.25:18789
 * 协议版本: 2026.5.2 (Challenge/Connect Auth)
 * 
 * 消息格式:
 * - req: {type: "req", id: string, method: string, params: object}
 * - res: {type: "res", id: string, method: string, result: object}
 * - event: {type: "event", method: string, params: object}
 */

class OpenClawClient {
  constructor() {
    this.ws = null;
    this.url = 'ws://192.168.1.25:18789';
    this.connected = false;
    this.authenticated = false;
    this.mockMode = false;
    this._id = 0;
    this._pending = new Map();       // id -> {resolve, reject, timeout}
    this._listeners = new Map();     // event -> [callbacks]
    this._reconnectAttempts = 0;
    this._maxReconnectAttempts = 5;
    this._reconnectTimer = null;
    
    // Token 管理
    this._token = localStorage.getItem('openclaw_token') || '';
    
    if (!this._token) {
      console.warn('[OpenClaw] 未设置 token，请在控制台运行: openclaw.setToken("your-token")');
    }
  }

  // ========== 连接管理 ==========

  async connect() {
    return new Promise((resolve, reject) => {
      // 已有连接
      if (this.ws && this.connected) {
        resolve(true);
        return;
      }

      try {
        this._clearReconnectTimer();
        this.ws = new WebSocket(this.url);
        
        // 等待 challenge 的超时（也作为认证超时）
        const challengeTimeout = setTimeout(() => {
          console.warn('[OpenClaw] 10秒内未收到认证确认，启用 mock 模式');
          this._enableMockMode();
          this._cleanup();
          resolve(true); // mock 模式也算连接成功
        }, 10000);

        // 保存上下文供 onopen 回调使用
        this._connectContext = { challengeTimeout, resolve, reject };

        this.ws.onopen = () => {
          console.log('[OpenClaw] WebSocket 连接已建立');
          this.connected = true;
          this._reconnectAttempts = 0;
          this.emit('connected');
          
          // 立即发送认证（不再等 challenge）
          this._sendConnectImmediate(this._connectContext);
        };

        this.ws.onmessage = (event) => {
          this._handleMessage(event.data, { challengeTimeout, resolve, reject });
        };

        this.ws.onerror = (error) => {
          console.error('[OpenClaw] WebSocket 错误:', error);
          this.emit('error', error);
        };

        this.ws.onclose = () => {
          console.log('[OpenClaw] WebSocket 连接已关闭');
          this._cleanup();
          this.emit('disconnected');
          
          if (!this.mockMode) {
            this._attemptReconnect();
          }
        };

      } catch (error) {
        console.warn('[OpenClaw] 连接失败，启用 mock 模式:', error.message);
        this._enableMockMode();
        resolve(true);
      }
    });
  }

  _cleanup() {
    this.connected = false;
    this.authenticated = false;
    if (this.ws) {
      this.ws.onopen = null;
      this.ws.onmessage = null;
      this.ws.onerror = null;
      this.ws.onclose = null;
      this.ws = null;
    }
    this._clearPending();
  }

  _clearPending() {
    this._pending.forEach(({ timeout }) => clearTimeout(timeout));
    this._pending.clear();
  }

  _clearReconnectTimer() {
    if (this._reconnectTimer) {
      clearTimeout(this._reconnectTimer);
      this._reconnectTimer = null;
    }
  }

  // ========== 消息处理 ==========

  _handleMessage(data, context = {}) {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch (error) {
      console.error('[OpenClaw] 解析消息失败:', error);
      return;
    }

    const { type, method, id, params, result } = msg;

    // 1. 服务端推送: connect.challenge
    if (type === 'event' && method === 'connect.challenge') {
      console.log('[OpenClaw] 收到 challenge:', params?.challenge);
      if (context.challengeTimeout) {
        clearTimeout(context.challengeTimeout);
      }
      this._sendConnect(params?.challenge, context);
      return;
    }

    // 2. 普通响应: 匹配 pending 请求（connect 响应也走这里）
    if (type === 'res' && id) {
      console.log('[OpenClaw] 收到响应 method=' + method + ' id=' + id + ' result=' + JSON.stringify(result) + ' error=' + JSON.stringify(msg.error));
      const pending = this._pending.get(id);
      if (pending) {
        clearTimeout(pending.timeout);
        this._pending.delete(id);
        if (msg.error) {
          var errMsg = typeof msg.error === 'string' ? msg.error : JSON.stringify(msg.error);
          pending.reject(new Error(errMsg));
        } else {
          pending.resolve(result || msg);
        }
      }
      return;
    }

    // 4. 服务端事件推送
    if (type === 'event') {
      this.emit(method, params);
      this.emit('event', { method, params });
      return;
    }

    // 5. 其他消息
    this.emit('message', msg);
  }

  async _sendConnectImmediate() {
    if (!this._token) {
      console.warn('[OpenClaw] 无 token，使用空 token 连接');
    }
    console.log('[OpenClaw] 发送认证请求 (token: ' + this._token.substring(0,8) + '...)');
    try {
      // 直接通过 WebSocket 发送，绕过 this.send() 的 connected 检查
      const id = this._generateId();
      const msg = JSON.stringify({
        type: 'req',
        id: id,
        method: 'connect',
        params: { token: this._token }
      });
      
      // 设置 pending 来接收响应
      const authPromise = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          this._pending.delete(id);
          reject(new Error('认证超时'));
        }, 15000);
        this._pending.set(id, { resolve, reject, timeout });
      });
      
      this.ws.send(msg);
      console.log('[OpenClaw] 认证消息已发送');
      
      const res = await authPromise;
      console.log('[OpenClaw] 收到认证响应:', JSON.stringify(res));
      
      if (res?.status === 'ok') {
        console.log('[OpenClaw] ✅ 认证成功！');
        this.authenticated = true;
        this.emit('authenticated', res);
        if (this._connectContext?.challengeTimeout) clearTimeout(this._connectContext.challengeTimeout);
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      } else {
        console.warn('[OpenClaw] 认证失败，启用 mock 模式');
        this._enableMockMode();
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      }
    } catch (error) {
      console.warn('[OpenClaw] 认证请求失败，启用 mock 模式:', error.message || JSON.stringify(error));
      this._enableMockMode();
      if (this._connectContext?.resolve) this._connectContext.resolve(true);
    }
  }

  async _sendConnect(challenge, context = {}) {
    // This handles challenge-based auth (if Gateway sends challenge first)
    if (!this._token) {
      console.warn('[OpenClaw] 无 token，使用空 token 连接');
    }
    console.log('[OpenClaw] 回复 challenge 认证...');
    try {
      const id = this._generateId();
      const msg = JSON.stringify({
        type: 'req',
        id: id,
        method: 'connect',
        params: { token: this._token, challenge }
      });
      const authPromise = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
          this._pending.delete(id);
          reject(new Error('challenge 认证超时'));
        }, 15000);
        this._pending.set(id, { resolve, reject, timeout });
      });
      this.ws.send(msg);
      const res = await authPromise;
      if (res?.status === 'ok') {
        console.log('[OpenClaw] ✅ challenge 认证成功！');
        this.authenticated = true;
        this.emit('authenticated', res);
        if (this._connectContext?.challengeTimeout) clearTimeout(this._connectContext.challengeTimeout);
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      } else {
        console.warn('[OpenClaw] 认证失败，启用 mock 模式');
        this._enableMockMode();
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      }
    } catch (error) {
      console.warn('[OpenClaw] 认证请求失败，启用 mock 模式:', error.message || JSON.stringify(error));
      this._enableMockMode();
      if (this._connectContext?.resolve) this._connectContext.resolve(true);
    }
  }

  // ========== 发送请求 ==========

  send(method, params = {}) {
    // Mock 模式
    if (this.mockMode) {
      return this._mockSend(method, params);
    }

    return new Promise((resolve, reject) => {
      if (!this.connected) {
        reject(new Error('WebSocket 未连接'));
        return;
      }

      const id = this._generateId();
      const msg = {
        type: 'req',
        id,
        method,
        params
      };

      // 30秒超时
      const timeout = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`请求超时: ${method}`));
      }, 30000);

      this._pending.set(id, { resolve, reject, timeout });
      this.ws.send(JSON.stringify(msg));
    });
  }

  _generateId() {
    return Date.now().toString(36) + '-' + (++this._id).toString(36);
  }

  // ========== 重连机制 ==========

  _attemptReconnect() {
    if (this._reconnectAttempts >= this._maxReconnectAttempts) {
      console.error('[OpenClaw] 达到最大重连次数 (5次)');
      this._enableMockMode();
      return;
    }

    this._reconnectAttempts++;
    // 递增延迟: 3s, 6s, 9s, 12s, 15s
    const delay = this._reconnectAttempts * 3000;
    
    console.log(`[OpenClaw] ${delay/1000}秒后尝试重连 (${this._reconnectAttempts}/${this._maxReconnectAttempts})`);
    
    this._reconnectTimer = setTimeout(() => {
      this.connect().catch(err => {
        console.error('[OpenClaw] 重连失败:', err);
      });
    }, delay);
  }

  // ========== Mock 模式 ==========

  _enableMockMode() {
    this.mockMode = true;
    this._cleanup();
    console.log('%c[OpenClaw] ⚠️ Mock 模式已启用 - 使用模拟数据', 'color: orange; font-weight: bold');
    this.emit('mockMode', true);
  }

  _mockSend(method, params) {
    return new Promise((resolve) => {
      // 模拟网络延迟
      setTimeout(() => {
        switch (method) {
          case 'agents.list':
            resolve({ agents: MOCK_AGENTS });
            break;
          case 'sessions.list':
            resolve({ sessions: MOCK_SESSIONS });
            break;
          case 'chat.history':
            resolve({ messages: MOCK_MESSAGES });
            break;
          case 'chat.send':
            // 模拟回复
            const replyText = this._getMockReply(params?.content?.[0]?.text || '');
            this.emit('chat.message', { 
              content: [{ type: 'text', text: replyText }],
              sessionKey: params?.sessionKey 
            });
            resolve({ status: 'ok' });
            break;
          case 'models.list':
            resolve({ models: MOCK_MODELS });
            break;
          case 'health':
            resolve({ status: 'ok', mock: true });
            break;
          default:
            resolve({ status: 'ok', mock: true });
        }
      }, 200 + Math.random() * 300);
    });
  }

  _getMockReply(message) {
    const lowerMsg = message.toLowerCase();
    
    if (lowerMsg.includes('你好') || lowerMsg.includes('hi') || lowerMsg.includes('hello')) {
      return '你好！我是模拟助手。在 mock 模式下，所有功能使用模拟数据。';
    }
    if (lowerMsg.includes('员工') || lowerMsg.includes('列表')) {
      return '当前员工列表: 张三(前端)、李四(后端)、王五(产品)、赵六(设计)';
    }
    if (lowerMsg.includes('帮助') || lowerMsg.includes('help')) {
      return '可用命令: /agents - 查看代理, /sessions - 查看会话, /help - 帮助';
    }
    
    const replies = [
      '收到！这是一条 mock 回复。',
      '好的，消息已收到。（mock 模式）',
      '我理解了，这是模拟回复。',
      '好的，请问还有什么需要帮助的？'
    ];
    return replies[Math.floor(Math.random() * replies.length)];
  }

  // ========== Event 系统 ==========

  on(event, callback) {
    if (!this._listeners.has(event)) {
      this._listeners.set(event, new Set());
    }
    this._listeners.get(event).add(callback);
    
    // 返回取消函数
    return () => this.off(event, callback);
  }

  off(event, callback) {
    const callbacks = this._listeners.get(event);
    if (callbacks) {
      callbacks.delete(callback);
    }
  }

  emit(event, data) {
    const callbacks = this._listeners.get(event);
    if (callbacks) {
      callbacks.forEach(cb => {
        try {
          cb(data);
        } catch (error) {
          console.error(`[OpenClaw] 事件 ${event} 回调错误:`, error);
        }
      });
    }
  }

  // ========== Token 管理 ==========

  setToken(token) {
    this._token = token;
    localStorage.setItem('openclaw_token', token);
    console.log('[OpenClaw] Token 已保存，重新连接后将使用新 token');
  }

  getToken() {
    return this._token;
  }

  clearToken() {
    this._token = '';
    localStorage.removeItem('openclaw_token');
    console.log('[OpenClaw] Token 已清除');
  }

  // ========== 断开连接 ==========

  disconnect() {
    this._clearReconnectTimer();
    this._reconnectAttempts = this._maxReconnectAttempts; // 防止自动重连
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._cleanup();
    console.log('[OpenClaw] 已断开连接');
  }

  // ========== API 方法 ==========

  // 获取 agent 列表
  async listAgents() {
    return this.send('agents.list', {});
  }

  // 获取 session 列表
  async listSessions() {
    return this.send('sessions.list', {});
  }

  // 获取聊天历史
  async getChatHistory(sessionKey) {
    return this.send('chat.history', { sessionKey });
  }

  // 发送聊天消息
  async sendChat(sessionKey, message) {
    return this.send('chat.send', {
      sessionKey,
      content: [{ type: 'text', text: message }]
    });
  }

  // 中断生成
  async abortChat(sessionKey) {
    return this.send('chat.abort', { sessionKey });
  }

  // 获取模型列表
  async listModels() {
    return this.send('models.list', {});
  }

  // 健康检查
  async health() {
    return this.send('health', {});
  }
}

// ========== Mock 数据 ==========

const MOCK_AGENTS = [
  { agentId: 'lobster', name: 'Lobster', description: '主助手' },
  { agentId: 'coder', name: 'Coder', description: '代码助手' },
  { agentId: 'writer', name: 'Writer', description: '写作助手' }
];

const MOCK_SESSIONS = [
  { sessionKey: 'agent:lobster:main', agentId: 'lobster', name: '主会话', createdAt: new Date().toISOString() },
  { sessionKey: 'agent:coder:main', agentId: 'coder', name: '代码会话', createdAt: new Date().toISOString() }
];

const MOCK_MESSAGES = [
  { role: 'user', content: [{ type: 'text', text: '你好' }], createdAt: new Date(Date.now() - 60000).toISOString() },
  { role: 'assistant', content: [{ type: 'text', text: '你好！有什么可以帮你的吗？' }], createdAt: new Date(Date.now() - 59000).toISOString() },
  { role: 'user', content: [{ type: 'text', text: '你是谁' }], createdAt: new Date(Date.now() - 30000).toISOString() },
  { role: 'assistant', content: [{ type: 'text', text: '我是你的 AI 助手' }], createdAt: new Date(Date.now() - 29000).toISOString() }
];

const MOCK_MODELS = [
  { id: 'gpt-4', name: 'GPT-4' },
  { id: 'gpt-3.5-turbo', name: 'GPT-3.5 Turbo' },
  { id: 'claude-3', name: 'Claude 3' }
];

// ========== 全局实例 ==========

const openclaw = new OpenClawClient();

console.log('%c[OpenClaw] 客户端已创建', 'color: green; font-weight: bold');
console.log('  • 运行 openclaw.connect() 尝试连接');
console.log('  • 运行 openclaw.setToken("your-token") 设置 token');
console.log('  • 连接失败时自动启用 mock 模式');
