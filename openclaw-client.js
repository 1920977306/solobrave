/**
 * OpenClaw WebSocket Client
 * 连接地址: ws://192.168.1.25:18789
 * 使用 Ed25519 JWK 格式认证
 */

class OpenClawClient {
  constructor() {
    this.ws = null;
    this.url = 'ws://192.168.1.25:18789';
    this.connected = false;
    this.authenticated = false;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 3000;
    this.pendingRequests = new Map();
    this.subscriptions = new Map();
    this.listeners = new Map();
    
    // Ed25519 JWK 密钥（需要从 SECRET.md 或配置获取）
    this.jwk = null;
    this.initJWK();
  }

  async initJWK() {
    // 尝试从 localStorage 或全局配置获取密钥
    // 如果没有配置，使用默认的开发密钥
    const savedKey = localStorage.getItem('openclaw_jwk');
    if (savedKey) {
      this.jwk = JSON.parse(savedKey);
    } else {
      // 默认开发密钥（实际使用时应该从服务器获取）
      this.jwk = {
        kty: "OKP",
        crv: "Ed25519",
        x: "9VHfO8L_LnnCQCDPVD3LrYHLMQ5_2cR-3uQYp9v6T2o",
        y: ""
      };
    }
  }

  // 连接 WebSocket
  async connect() {
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(this.url);
        
        this.ws.onopen = async () => {
          console.log('[OpenClaw] WebSocket 连接已建立');
          this.connected = true;
          this.reconnectAttempts = 0;
          
          // 发送认证消息
          await this.authenticate();
          resolve(true);
        };
        
        this.ws.onmessage = (event) => {
          this.handleMessage(event.data);
        };
        
        this.ws.onerror = (error) => {
          console.error('[OpenClaw] WebSocket 错误:', error);
          this.emit('error', error);
        };
        
        this.ws.onclose = () => {
          console.log('[OpenClaw] WebSocket 连接已关闭');
          this.connected = false;
          this.authenticated = false;
          this.emit('disconnected');
          this.attemptReconnect();
        };
        
      } catch (error) {
        reject(error);
      }
    });
  }

  // 认证 - 使用 challenge-response 机制
  async authenticate() {
    if (!this.jwk) {
      await this.initJWK();
    }
    
    // 生成或获取私钥
    if (!this.jwk.d) {
      // 生成一个随机私钥（用于测试）
      const randomBytes = new Uint8Array(32);
      if (window.crypto) {
        crypto.getRandomValues(randomBytes);
      } else {
        for (let i = 0; i < 32; i++) {
          randomBytes[i] = Math.floor(Math.random() * 256);
        }
      }
      this.jwk.d = btoa(String.fromCharCode(...randomBytes));
    }
    
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => {
        reject(new Error('认证超时'));
      }, 10000);
      
      // 等待 challenge 消息
      const handleChallenge = async (msg) => {
        // 检查是否是 challenge 消息
        if (msg.type === 'challenge' || msg.nonce || (msg.params && msg.params.nonce)) {
          clearTimeout(timeout);
          this.off('message', handleChallenge);
          
          try {
            const nonce = msg.nonce || msg.params?.nonce;
            const signedAt = Date.now();
            
            // 构造签名数据（简化版，实际应该使用 Ed25519 签名）
            const signatureData = `${nonce}:${signedAt}`;
            const signature = btoa(signatureData); // 简化签名，实际应使用私钥签名
            
            // 发送认证响应
            const authMsg = {
              type: 'auth',
              method: 'jwk',
              params: {
                jwk: {
                  kty: this.jwk.kty,
                  crv: this.jwk.crv,
                  x: this.jwk.x
                },
                device: {
                  publicKey: {
                    kty: this.jwk.kty,
                    crv: this.jwk.crv,
                    x: this.jwk.x
                  },
                  signature: signature,
                  signedAt: signedAt,
                  nonce: nonce
                }
              }
            };
            
            this.send(authMsg).then(response => {
              if (response.success || response.authenticated) {
                this.authenticated = true;
                console.log('[OpenClaw] 认证成功');
                this.emit('authenticated');
                resolve(true);
              } else {
                reject(new Error('认证失败: ' + (response.error || '未知错误')));
              }
            }).catch(err => {
              reject(err);
            });
          } catch (err) {
            reject(err);
          }
        }
      };
      
      // 监听 challenge
      this.on('message', handleChallenge);
      
      // 发送初始认证请求（触发服务器发送 challenge）
      this.send({
        type: 'auth',
        method: 'jwk',
        params: {
          jwk: {
            kty: this.jwk.kty,
            crv: this.jwk.crv,
            x: this.jwk.x
          }
        }
      }).catch(() => {
        // 忽略错误，等待 challenge
      });
    });
  }

  // 发送请求
  async send(message) {
    return new Promise((resolve, reject) => {
      if (!this.connected) {
        reject(new Error('WebSocket 未连接'));
        return;
      }
      
      const id = this.generateId();
      const msg = {
        ...message,
        id: id
      };
      
      this.pendingRequests.set(id, { resolve, reject });
      
      // 设置超时
      const timeout = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error(`请求超时: ${id}`));
      }, 30000);
      
      // 存储超时引用以便清除
      const stored = this.pendingRequests.get(id);
      if (stored) {
        stored.timeout = timeout;
      }
      
      this.ws.send(JSON.stringify(msg));
    });
  }

  // 处理接收到的消息
  handleMessage(data) {
    try {
      const msg = JSON.parse(data);
      
      // 处理响应
      if (msg.id && this.pendingRequests.has(msg.id)) {
        const pending = this.pendingRequests.get(msg.id);
        clearTimeout(pending.timeout);
        this.pendingRequests.delete(msg.id);
        
        if (msg.error) {
          pending.reject(new Error(msg.error));
        } else {
          pending.resolve(msg.result || msg);
        }
        return;
      }
      
      // 处理订阅事件
      if (msg.type === 'event' || msg.type === 'subscribe') {
        this.handleSubscriptionEvent(msg);
      }
      
      // 广播消息给所有监听器
      this.emit('message', msg);
      
    } catch (error) {
      console.error('[OpenClaw] 解析消息失败:', error);
    }
  }

  // 处理订阅事件
  handleSubscriptionEvent(msg) {
    const channel = msg.channel || msg.subscription;
    if (channel && this.subscriptions.has(channel)) {
      const callbacks = this.subscriptions.get(channel);
      callbacks.forEach(cb => {
        try {
          cb(msg.data || msg);
        } catch (error) {
          console.error('[OpenClaw] 订阅回调错误:', error);
        }
      });
    }
  }

  // 订阅频道
  subscribe(channel, callback) {
    if (!this.subscriptions.has(channel)) {
      this.subscriptions.set(channel, new Set());
      
      // 发送订阅请求
      this.send({
        type: 'subscribe',
        channel: channel
      }).catch(err => {
        console.error(`[OpenClaw] 订阅 ${channel} 失败:`, err);
      });
    }
    
    this.subscriptions.get(channel).add(callback);
    
    // 返回取消订阅函数
    return () => {
      const callbacks = this.subscriptions.get(channel);
      if (callbacks) {
        callbacks.delete(callback);
        if (callbacks.size === 0) {
          this.subscriptions.delete(channel);
          this.send({
            type: 'unsubscribe',
            channel: channel
          }).catch(err => {
            console.error(`[OpenClaw] 取消订阅 ${channel} 失败:`, err);
          });
        }
      }
    };
  }

  // 事件监听
  on(event, callback) {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event).add(callback);
    return () => this.listeners.get(event)?.delete(callback);
  }

  off(event, callback) {
    const callbacks = this.listeners.get(event);
    if (callbacks) {
      callbacks.delete(callback);
    }
  }

  emit(event, data) {
    const callbacks = this.listeners.get(event);
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

  // 重连
  attemptReconnect() {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error('[OpenClaw] 达到最大重连次数');
      return;
    }
    
    this.reconnectAttempts++;
    console.log(`[OpenClaw] ${this.reconnectDelay/1000}秒后尝试重连 (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    
    setTimeout(() => {
      this.connect().catch(err => {
        console.error('[OpenClaw] 重连失败:', err);
      });
    }, this.reconnectDelay);
  }

  // 断开连接
  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.connected = false;
    this.authenticated = false;
    this.pendingRequests.clear();
    this.subscriptions.clear();
  }

  // 生成唯一 ID
  generateId() {
    return Date.now().toString(36) + Math.random().toString(36).substr(2, 9);
  }

  // ========== API 方法 ==========

  // 获取代理列表
  async agents_list() {
    return this.send({
      type: 'call',
      method: 'agents.list',
      params: {}
    });
  }

  // 获取代理身份信息
  async agent_identity_get(agentId) {
    return this.send({
      type: 'call',
      method: 'agent.identity.get',
      params: { agent_id: agentId }
    });
  }

  // 获取会话列表
  async sessions_list(agentId = null) {
    return this.send({
      type: 'call',
      method: 'sessions.list',
      params: agentId ? { agent_id: agentId } : {}
    });
  }

  // 订阅会话
  sessions_subscribe(callback) {
    return this.subscribe('sessions', callback);
  }

  // 发送聊天消息
  async chat_send(sessionId, content) {
    return this.send({
      type: 'call',
      method: 'chat.send',
      params: {
        session_id: sessionId,
        content: content
      }
    });
  }

  // 获取聊天历史
  async chat_history(sessionId, limit = 50) {
    return this.send({
      type: 'call',
      method: 'chat.history',
      params: {
        session_id: sessionId,
        limit: limit
      }
    });
  }

  // 健康检查
  async health() {
    return this.send({
      type: 'call',
      method: 'health',
      params: {}
    });
  }

  // 获取节点列表
  async node_list() {
    return this.send({
      type: 'call',
      method: 'node.list',
      params: {}
    });
  }

  // 更新代理配置
  async agent_update(agentId, config) {
    return this.send({
      type: 'call',
      method: 'agent.update',
      params: {
        agent_id: agentId,
        config: config
      }
    });
  }

  // 应用配置
  async config_apply(config) {
    return this.send({
      type: 'call',
      method: 'config.apply',
      params: { config: config }
    });
  }

  // 重启网关
  async gateway_restart() {
    return this.send({
      type: 'call',
      method: 'gateway.restart',
      params: {}
    });
  }
}

// 创建全局实例
const openclaw = new OpenClawClient();
