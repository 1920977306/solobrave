// ===== OpenClaw WebSocket Client (with Ed25519 auth) =====

// 硬编码设备身份（从 OpenClaw 导出）
var OC_DEVICE = {
  deviceId: '0a3b672b45043edfe152d38c1d219b89720f2948e721f0e2c8333527e86f10a6',
  publicKey: '4RJ7hQaJcjzJOmiL2Z7NjbSxLvLsXPXGOuBGi2DKbG4',
  privateKey: 'm8Hd_TfNTmTR1Y6KpHyh51Fhozy6KR2klE-T9fxWgA0',
  token: '8606e4d80b1accfaa4e22729466c40003cd217ce2bda93f3'
};

function base64urlToBuffer(b64) {
  var s = b64.replace(/-/g, '+').replace(/_/g, '/');
  while (s.length % 4) s += '=';
  var bin = atob(s);
  var buf = new Uint8Array(bin.length);
  for (var i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function bufferToBase64url(buf) {
  var bytes = new Uint8Array(buf);
  var binary = '';
  for (var i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function signConnectParams(nonce) {
  var signedAt = Date.now();
  var signStr = [
    'v2',
    OC_DEVICE.deviceId,
    'openclaw-control-ui',
    'webchat',
    'operator',
    'operator.admin,operator.read,operator.write,operator.approvals,operator.pairing',
    String(signedAt),
    OC_DEVICE.token,
    nonce
  ].join('|');

  // 使用 JWK 格式导入私钥
  var jwk = {
    alg: 'Ed25519',
    crv: 'Ed25519',
    d: OC_DEVICE.privateKey,
    ext: false,
    key_ops: ['sign'],
    kty: 'OKP',
    x: OC_DEVICE.publicKey
  };

  var key = await crypto.subtle.importKey(
    'jwk',
    jwk,
    'Ed25519',
    false,
    ['sign']
  );

  var encoded = new TextEncoder().encode(signStr);
  var sigBuf = await crypto.subtle.sign('Ed25519', key, encoded);
  var signature = bufferToBase64url(sigBuf);

  return {
    id: OC_DEVICE.deviceId,
    publicKey: OC_DEVICE.publicKey,
    signature: signature,
    signedAt: signedAt,
    nonce: nonce
  };
}

function OpenClawClient(url, token) {
  this.url = url || 'ws://localhost:18789';
  this.token = token || OC_DEVICE.token;
  this.ws = null;
  this.connected = false;
  this.authenticated = false;
  this.reqId = 0;
  this.pending = {};
  this.eventHandlers = {};
}

OpenClawClient.prototype.connect = function() {
  var self = this;
  return new Promise(function(resolve, reject) {
    try {
      self.ws = new WebSocket(self.url);
    } catch (e) {
      reject(new Error('WebSocket创建失败: ' + e.message));
      return;
    }

    self.ws.onopen = function() {
      self.connected = true;
      console.log('[OpenClaw] WebSocket已连接');
    };

    self.ws.onmessage = function(event) {
      self._onMessage(event.data);
    };

    self.ws.onclose = function() {
      self.connected = false;
      self.authenticated = false;
      console.log('[OpenClaw] WebSocket已关闭');
      self._emit('close');
    };

    self.ws.onerror = function(err) {
      console.error('[OpenClaw] WebSocket错误:', err);
      self._emit('error', err);
      reject(err);
    };

    self.once('hello-ok', function() {
      self.authenticated = true;
      resolve(self);
    });

    self.once('hello-fail', function(err) {
      reject(new Error('认证失败: ' + (err ? err.message : '未知错误')));
    });

    setTimeout(function() {
      if (!self.authenticated) {
        reject(new Error('连接超时，请检查Gateway是否运行'));
      }
    }, 15000);
  });
};

OpenClawClient.prototype._onMessage = function(data) {
  try {
    var msg = JSON.parse(data);
    console.log('[OpenClaw] 收到:', msg.type, msg.id || msg.event);

    if (msg.type === 'event') {
      this._handleEvent(msg);
    } else if (msg.type === 'res') {
      this._handleResponse(msg);
    }
  } catch (e) {
    console.error('[OpenClaw] 消息解析失败:', e);
  }
};

OpenClawClient.prototype._handleEvent = function(msg) {
  var eventName = msg.event;

  if (eventName === 'connect.challenge') {
    this._sendAuth(msg.payload);
    return;
  }

  if (eventName === 'hello-ok') {
    this._emit('hello-ok', msg.payload);
    return;
  }

  if (eventName === 'hello-fail') {
    this._emit('hello-fail', msg.payload);
    return;
  }

  this._emit(eventName, msg.payload);
  this._emit('*', msg);
};

OpenClawClient.prototype._sendAuth = function(challenge) {
  var self = this;
  var nonce = challenge ? challenge.nonce : '';

  signConnectParams(nonce).then(function(device) {
    var id = self._nextId();
    var req = {
      type: 'req',
      id: id,
      method: 'connect',
      params: {
        minProtocol: 3,
        maxProtocol: 3,
        client: {
          id: 'openclaw-control-ui',
          version: 'control-ui',
          platform: navigator.platform || 'web',
          mode: 'webchat',
          instanceId: crypto.randomUUID ? crypto.randomUUID() : 'inst-' + Date.now()
        },
        role: 'operator',
        scopes: ['operator.admin', 'operator.read', 'operator.write', 'operator.approvals', 'operator.pairing'],
        device: device,
        caps: ['tool-events'],
        auth: {
          token: self.token,
          deviceToken: self.token
        },
        userAgent: navigator.userAgent,
        locale: 'zh-CN'
      }
    };

    console.log('[OpenClaw] 发送认证请求');

    self.pending[id] = {
      resolve: function(payload) {
        console.log('[OpenClaw] 认证成功');
        self.authenticated = true;
        self._emit('hello-ok', payload);
      },
      reject: function(err) {
        console.log('[OpenClaw] 认证失败:', JSON.stringify(err));
        self._emit('hello-fail', err);
      }
    };

    self._send(req);
  }).catch(function(err) {
    console.error('[OpenClaw] 签名失败:', err);
    self._emit('hello-fail', { message: '签名失败: ' + err.message });
  });
};

OpenClawClient.prototype._handleResponse = function(msg) {
  var id = msg.id;
  var pending = this.pending[id];
  if (!pending) return;

  delete this.pending[id];

  if (msg.ok) {
    pending.resolve(msg.payload);
  } else {
    var errMsg = typeof msg.error === 'string' ? msg.error : (msg.error && msg.error.message ? msg.error.message : JSON.stringify(msg.error || '请求失败'));
    pending.reject(new Error(errMsg));
  }
};

OpenClawClient.prototype.request = function(method, params) {
  var self = this;
  return new Promise(function(resolve, reject) {
    if (!self.connected) {
      reject(new Error('WebSocket未连接'));
      return;
    }

    var id = self._nextId();
    var req = {
      type: 'req',
      id: id,
      method: method,
      params: params || {}
    };

    self.pending[id] = { resolve: resolve, reject: reject };
    self._send(req);

    setTimeout(function() {
      if (self.pending[id]) {
        delete self.pending[id];
        reject(new Error('请求超时: ' + method));
      }
    }, 30000);
  });
};

OpenClawClient.prototype._send = function(data) {
  if (this.ws && this.ws.readyState === WebSocket.OPEN) {
    this.ws.send(JSON.stringify(data));
  }
};

OpenClawClient.prototype._nextId = function() {
  this.reqId++;
  return 'req-' + this.reqId;
};

OpenClawClient.prototype.on = function(event, handler) {
  if (!this.eventHandlers[event]) {
    this.eventHandlers[event] = [];
  }
  this.eventHandlers[event].push(handler);
};

OpenClawClient.prototype.once = function(event, handler) {
  var self = this;
  var wrapper = function(data) {
    self.off(event, wrapper);
    handler(data);
  };
  this.on(event, wrapper);
};

OpenClawClient.prototype.off = function(event, handler) {
  var handlers = this.eventHandlers[event];
  if (!handlers) return;
  var idx = handlers.indexOf(handler);
  if (idx !== -1) {
    handlers.splice(idx, 1);
  }
};

OpenClawClient.prototype._emit = function(event, data) {
  var handlers = this.eventHandlers[event];
  if (handlers) {
    for (var i = 0; i < handlers.length; i++) {
      try {
        handlers[i](data);
      } catch (e) {
        console.error('[OpenClaw] 事件处理错误:', e);
      }
    }
  }
};

OpenClawClient.prototype.disconnect = function() {
  if (this.ws) {
    this.ws.close();
    this.ws = null;
  }
  this.connected = false;
  this.authenticated = false;
};

// ===== 便捷方法 =====
OpenClawClient.prototype.agentsList = function() {
  return this.request('agents.list', {});
};

OpenClawClient.prototype.chatHistory = function(sessionKey, limit) {
  return this.request('chat.history', {
    sessionKey: sessionKey,
    limit: limit || 200
  });
};

OpenClawClient.prototype.chatSend = function(sessionKey, text) {
  return this.request('chat.send', {
    sessionKey: sessionKey,
    text: text
  });
};

OpenClawClient.prototype.chatAbort = function(sessionKey) {
  return this.request('chat.abort', {
    sessionKey: sessionKey
  });
};

OpenClawClient.prototype.sessionsList = function() {
  return this.request('sessions.list', {
    includeGlobal: true,
    includeUnknown: true
  });
};

OpenClawClient.prototype.sessionsSubscribe = function() {
  return this.request('sessions.subscribe', {});
};

OpenClawClient.prototype.health = function() {
  return this.request('health', {});
};

// ===== 全局实例 =====
var openclawClient = null;

function initOpenClaw(url, token) {
  openclawClient = new OpenClawClient(url, token);
  return openclawClient.connect();
}