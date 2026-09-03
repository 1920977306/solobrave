/**
 * OpenClaw WebSocket Client
 * 连接地址: ws://192.168.1.25:18789
 * 协议版本: v3 (Challenge/Connect Auth)
 *
 * 消息格式:
 * - req: {type: "req", id: string, method: string, params: object}
 * - res: {type: "res", id: string, ok: boolean, payload|error: object}
 * - event: {type: "event", event: string, payload: object}
 */

// ===== Device Identity (Ed25519) =====
// OpenClaw 网关 v3 协议要求 connect params 中包含 device 字段（Ed25519 签名的设备身份证明），
// 否则会被拒绝（CONTROL_UI_DEVICE_IDENTITY_REQUIRED）。密钥在浏览器内生成、pkcs8 私钥只
// 存 localStorage（按 v1 命名空间），保证同一浏览器重连时 deviceId 稳定。
const DEVICE_IDENTITY_STORAGE_KEY = 'openclaw_device_identity_v1';

// 不需要 agentId 注入的方法（认证 / 控制类请求自己处理 owner 字段）
const SKIP_AGENTID_METHODS = new Set(['connect', 'auth', 'disconnect', 'agents.list', 'health']);
// 非安全上下文（http://，或本地 file://）下 crypto.subtle 不可用时用的 fallback 缓存。
// 单独 namespace 避免污染 Ed25519 缓存，也方便两种身份独立迁移。
const DEVICE_IDENTITY_FALLBACK_KEY = 'openclaw_device_identity';

// 检测 WebCrypto.subtle 是否真正可用（不仅是存在性，还要看 generateKey/sign 都在）
function _hasSubtleCrypto() {
  return !!(window.crypto && window.crypto.subtle
    && typeof window.crypto.subtle.generateKey === 'function'
    && typeof window.crypto.subtle.sign === 'function'
    && typeof window.crypto.subtle.digest === 'function');
}

async function _ensureDeviceIdentity() {
  const useSubtle = _hasSubtleCrypto();

  if (useSubtle) {
    // ===== Ed25519 主路径 =====
    // 1) localStorage 缓存命中且字段齐全则直接复用
    try {
      const cached = localStorage.getItem(DEVICE_IDENTITY_STORAGE_KEY);
      if (cached) {
        const parsed = JSON.parse(cached);
        if (parsed && parsed.publicKeyRaw && parsed.privateKeyPkcs8 && parsed.deviceId) {
          return parsed;
        }
      }
    } catch (e) {
      console.warn('[OpenClaw] 读取设备身份缓存失败，将重新生成:', e);
    }

    // 2) 生成 Ed25519 密钥对
    const keyPair = await crypto.subtle.generateKey(
      { name: 'Ed25519' },
      true,
      ['sign', 'verify']
    );

    // 3) 导出 raw public key（32 字节，给网关校验签名用）
    const publicKeyRaw = new Uint8Array(
      await crypto.subtle.exportKey('raw', keyPair.publicKey)
    );
    // 导出 pkcs8 私钥（本地签名复用）
    const privateKeyPkcs8 = new Uint8Array(
      await crypto.subtle.exportKey('pkcs8', keyPair.privateKey)
    );

    // 4) deviceId = SHA256(raw publicKey) 的 hex 串
    const deviceIdHash = new Uint8Array(
      await crypto.subtle.digest('SHA-256', publicKeyRaw)
    );
    const deviceId = Array.from(deviceIdHash)
      .map(b => b.toString(16).padStart(2, '0'))
      .join('');

    // 5) base64url 编码（localStorage / JSON 传输更稳）
    const publicKeyRawB64 = _base64UrlEncode(publicKeyRaw);
    const privateKeyPkcs8B64 = _base64UrlEncode(privateKeyPkcs8);

    const identity = {
      deviceId,
      publicKeyRaw: publicKeyRawB64,
      privateKeyPkcs8: privateKeyPkcs8B64,
      algo: 'ed25519'
    };

    // 6) 写回 localStorage（写失败不影响本次连接，只是下次会重新生成）
    try {
      localStorage.setItem(DEVICE_IDENTITY_STORAGE_KEY, JSON.stringify(identity));
    } catch (e) {
      console.warn('[OpenClaw] 写入设备身份缓存失败:', e);
    }
    return identity;
  }

  // ===== Fallback 路径：WebCrypto.subtle 不可用 =====
  // 非安全上下文（http://，或本地 file://）下 chrome://flags 不生效时的兜底。
  // 关键：deviceId 必须是确定性的——同浏览器同 origin 每次产出同一 ID，让网关能"认住"；
  // 因此私钥不再用 crypto.getRandomValues（每次都不同），而是从浏览器指纹派生。
  // 公钥 = 私钥（仅用于标识 + 派生签名，不是真正的 Ed25519 验证）。
  // 安全模型：私钥派生自浏览器指纹，跨设备/跨浏览器自然不同；同设备同浏览器稳定。
  console.log('[OpenClaw] WebCrypto.subtle 不可用，使用 localStorage 降级设备身份');

  // 1) 优先复用已有 fallback 缓存（用户已经在该浏览器/设备上绑定过 ID）
  try {
    const cached = localStorage.getItem(DEVICE_IDENTITY_FALLBACK_KEY);
    if (cached) {
      const parsed = JSON.parse(cached);
      if (parsed && parsed.publicKeyRaw && parsed.privateKeyPkcs8 && parsed.deviceId) {
        return parsed;
      }
    }
  } catch (e) {
    console.warn('[OpenClaw] 读取 fallback 设备身份缓存失败，将重新生成:', e);
  }

  // 2) 浏览器指纹 → 32 字节确定性种子
  const fingerprint = [
    navigator.userAgent || '',
    String(screen && screen.width || 0),
    String(screen && screen.height || 0),
    String(screen && screen.colorDepth || 0),
    navigator.language || '',
    String(new Date().getFullYear()),
    String(window.location && window.location.origin || '')
  ].join('|');
  const privKeyBytes = _fingerprintToBytes(fingerprint, 32);
  // 公钥 = 私钥（仅用于标识 + 派生签名，不是真正的 Ed25519 验证对）
  const publicKeyRaw = new Uint8Array(privKeyBytes);

  // 3) deviceId = base64url(privateKey) 的前 43 字符（≈32 字节原始），稳定且较短
  const privateKeyB64 = _base64UrlEncode(privKeyBytes);
  const publicKeyB64 = _base64UrlEncode(publicKeyRaw);
  const deviceId = privateKeyB64.substring(0, 43);

  const identity = {
    deviceId: deviceId,
    publicKeyRaw: publicKeyB64,
    privateKeyPkcs8: privateKeyB64,
    algo: 'fallback'
  };

  // 4) 写回 localStorage（与 Ed25519 缓存不同 namespace，独立管理）
  try {
    localStorage.setItem(DEVICE_IDENTITY_FALLBACK_KEY, JSON.stringify(identity));
  } catch (e) {
    console.warn('[OpenClaw] 写入 fallback 设备身份缓存失败:', e);
  }
  return identity;
}

function _base64UrlEncode(uint8Array) {
  let binary = '';
  for (let i = 0; i < uint8Array.length; i++) {
    binary += String.fromCharCode(uint8Array[i]);
  }
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function _base64UrlDecode(str) {
  str = str.replace(/-/g, '+').replace(/_/g, '/');
  while (str.length % 4) str += '=';
  const binary = atob(str);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

// 纯 JS 派生签名：把 privateKey 字节与 payload 字节拼接后跑 16 轮 FNV-1a（不同 salt），
// 拼出 64 字节。**不是密码学安全的签名**——只是给服务端一个稳定、可复现的 device
// 身份校验值（用户在已知 fallback 模式的网关侧做对应校验时能通过）。
function _fallbackSignDevicePayload(privateKeyB64, payload) {
  const privKeyBytes = _base64UrlDecode(privateKeyB64);
  const payloadBytes = new TextEncoder().encode(payload);
  const combined = new Uint8Array(privKeyBytes.length + payloadBytes.length);
  combined.set(privKeyBytes, 0);
  combined.set(payloadBytes, privKeyBytes.length);

  const out = new Uint8Array(64);
  for (let i = 0; i < 16; i++) {
    // 16 个 salt 让 16 轮 FNV-1a 各自得到不同的 4 字节，凑齐 64 字节
    let h = (0x811c9dc5 ^ (i * 0x9e3779b9)) >>> 0;
    for (let j = 0; j < combined.length; j++) {
      h ^= combined[j];
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    out[i * 4] = h & 0xff;
    out[i * 4 + 1] = (h >>> 8) & 0xff;
    out[i * 4 + 2] = (h >>> 16) & 0xff;
    out[i * 4 + 3] = (h >>> 24) & 0xff;
  }
  return _base64UrlEncode(out);
}

// 把任意字符串确定性映射成 N 字节——纯 JS、不依赖 WebCrypto。
// 与 _fallbackSignDevicePayload 用同样的 8 轮（产生 32 字节）或 N/4 轮带不同 salt 的 FNV-1a，
// 让相同输入每次产出完全一致的字节序列。同浏览器同 origin 拿到的就是同一组字节。
function _fingerprintToBytes(input, byteLen) {
  const enc = new TextEncoder().encode(String(input));
  const out = new Uint8Array(byteLen);
  const rounds = Math.ceil(byteLen / 4);
  for (let i = 0; i < rounds; i++) {
    let h = (0x811c9dc5 ^ (i * 0x9e3779b9)) >>> 0;
    for (let j = 0; j < enc.length; j++) {
      h ^= enc[j];
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    const base = i * 4;
    out[base] = h & 0xff;
    if (base + 1 < byteLen) out[base + 1] = (h >>> 8) & 0xff;
    if (base + 2 < byteLen) out[base + 2] = (h >>> 16) & 0xff;
    if (base + 3 < byteLen) out[base + 3] = (h >>> 24) & 0xff;
  }
  return out;
}

async function _signDevicePayload(privateKeyPkcs8B64, payload) {
  // crypto.subtle 不可用时（fallback 身份）走纯 JS 派生签名
  if (!_hasSubtleCrypto()) {
    return _fallbackSignDevicePayload(privateKeyPkcs8B64, payload);
  }
  const privateKeyData = _base64UrlDecode(privateKeyPkcs8B64);
  const privateKey = await crypto.subtle.importKey(
    'pkcs8',
    privateKeyData,
    { name: 'Ed25519' },
    false,
    ['sign']
  );
  const encoder = new TextEncoder();
  const signature = await crypto.subtle.sign(
    'Ed25519',
    privateKey,
    encoder.encode(payload)
  );
  return _base64UrlEncode(new Uint8Array(signature));
}

class OpenClawClient {
  constructor() {
    this.ws = null;
    // 协议自适应：https 页面下用 wss，http 页面下用 ws。
    // 远程访问时 OpenClaw Gateway（18789）只支持 ws://，所以 https 页面下不走直连 18789，
    // 而是连到 SoloBrave HTTPS 8443 同源下的 wss 代理端口 8444，由后端透传到 18789。
    // 同机 localhost / 127.0.0.1 访问（http 页面）仍直连 Gateway：浏览器已经把 localhost
    // 视为安全上下文，crypto.subtle 可用，wss 不必要。
    var _pageIsSecure = (typeof window !== 'undefined' && window.location && window.location.protocol === 'https:');
    var _host = (typeof window !== 'undefined' && window.location && window.location.hostname) || '192.168.1.25';
    var _port = (typeof window !== 'undefined' && window.location && window.location.port) || '';
    if (_pageIsSecure) {
      // 走 SoloBrave WSS 代理：同主机 + 8444 端口（与 HTTPS 8443 同源，证书一致）
      this.url = 'wss://' + _host + ':8444';
    } else {
      // 走 OpenClaw Gateway 直连：18789
      this.url = 'ws://' + _host + ':18789';
    }
    this.connected = false;
    this.authenticated = false;
    this.mockMode = false;
    this._id = 0;
    this._pending = new Map();       // id -> {resolve, reject, timeout}
    this._listeners = new Map();     // event -> [callbacks]
    this._reconnectAttempts = 0;
    this._maxReconnectAttempts = 5;
    this._reconnectTimer = null;

    // 稳定的设备 ID（存 localStorage 保持不变）
    this._deviceId = localStorage.getItem('openclaw_device_id');
    if (!this._deviceId) {
      this._deviceId = 'solobrave-' + Math.random().toString(36).substring(2, 10);
      localStorage.setItem('openclaw_device_id', this._deviceId);
    }

    // Token 管理
    this._token = localStorage.getItem('openclaw_token') || '';

    // 默认 agentId：网关配置了多个 agent 时，每个请求必须显式带 agentId（"Multiple agents
    // are configured, but this Gateway request has no explicit owner"）。先从 localStorage
    // 读上次选中的；没有就让 listAgents() 用网关返回的 defaultId 填充；都没有就空着，
    // send() 就不注入，让网关按"未指定"处理（避免硬编码 'main' 命中错误的 session）。
    this._defaultAgentId = localStorage.getItem('openclaw_default_agent_id') || '';
    this._agentsCache = null;        // 缓存 list_agents 结果（供 index.html 选默认用）
    this._gatewayDefaultId = null;   // 网关在 agents.list 响应里告诉我们的默认 agent

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

      // 预生成 device identity，让首次密钥生成不阻塞 connect 流程
      _ensureDeviceIdentity().catch(e => console.warn('[OpenClaw] device identity pre-gen failed:', e));

      try {
        this._clearReconnectTimer();
        this.ws = new WebSocket(this.url);
        
        // 等待认证的超时
        const authTimeout = setTimeout(() => {
          console.warn('[OpenClaw] 10秒内未完成认证，启用 mock 模式');
          this._enableMockMode();
          this._cleanup();
          resolve(true); // mock 模式也算连接成功
        }, 10000);

        // 保存上下文供回调使用
        this._connectContext = { authTimeout, resolve, reject };

        this.ws.onopen = () => {
          console.log('[OpenClaw] WebSocket 连接已建立');
          this.connected = true;
          this._reconnectAttempts = 0;
          this.emit('connected');
          
          // v3 协议：等待 Gateway 发 challenge，不要主动发 connect
          // Gateway 会在连接建立后推送 connect.challenge 事件
          console.log('[OpenClaw] 等待 Gateway challenge...');
        };

        this.ws.onmessage = (event) => {
          this._handleMessage(event.data);
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

  _handleMessage(data) {
    let msg;
    try {
      msg = JSON.parse(data);
    } catch (error) {
      console.error('[OpenClaw] 解析消息失败:', error);
      return;
    }

    const { type, method, event, id, payload, params } = msg;

    // 1. 服务端推送: connect.challenge (v3 格式用 event 字段)
    if (type === 'event' && (event === 'connect.challenge' || method === 'connect.challenge')) {
      const nonce = payload?.nonce || params?.nonce || params?.challenge;
      console.log('[OpenClaw] 收到 challenge, nonce:', nonce);
      if (this._connectContext?.authTimeout) {
        clearTimeout(this._connectContext.authTimeout);
      }
      this._sendConnect(nonce, this._connectContext);
      return;
    }

    // 2. 普通响应: 匹配 pending 请求
    if (type === 'res' && id) {
      console.log('[OpenClaw] 收到响应 id=' + id + ' ok=' + msg.ok +
        (msg.error ? ' error=' + JSON.stringify(msg.error) : '') +
        (payload ? ' payload_type=' + (payload.type || 'unknown') : ''));

      const pending = this._pending.get(id);
      if (pending) {
        clearTimeout(pending.timeout);
        this._pending.delete(id);
        if (msg.error || msg.ok === false) {
          var errObj = msg.error || { code: 'UNKNOWN', message: 'request failed' };
          var errMsg = typeof errObj === 'string' ? errObj : (errObj.message || JSON.stringify(errObj));
          var errCode = (errObj && errObj.code) || '';

          // 自动纠错：session 与 agentId 不匹配时，从错误消息里抠出网关真正期望的
          // agentId（例如 "does not match session key agent 'emp_xxx'"），切过去并重试。
          // 每个 id 只重试一次，避免死循环。
          if (errCode === 'INVALID_REQUEST' && /does not match session key agent\s+"([^"]+)"/i.test(errMsg || '')) {
            var m = errMsg.match(/does not match session key agent\s+"([^"]+)"/i);
            var correctId = m && m[1];
            if (correctId && correctId !== this._defaultAgentId && !pending._retried) {
              console.warn('[OpenClaw] agentId 不匹配，自动切换: ' + this._defaultAgentId + ' → ' + correctId);
              this.setDefaultAgentId(correctId);
              try { localStorage.setItem('openclaw_default_agent_id', correctId); } catch (e) {}
              // 用新 agentId 重新发原请求（用新 id，旧的 _pending 已经被删了）
              try {
                var newMsg = {
                  type: 'req',
                  id: this._generateId(),
                  method: pending.method,
                  params: Object.assign({}, pending.params || {}, { agentId: correctId })
                };
                if (this.ws && this.ws.readyState === 1 /* OPEN */) {
                  this.ws.send(JSON.stringify(newMsg));
                  // 给重发的请求也建一个 pending，超时单独挂
                  var newTimeout = setTimeout(function(self, mid) {
                    return function() {
                      self._pending.delete(mid);
                      pending.reject(new Error('请求超时: ' + pending.method + ' (retry)'));
                    };
                  }(this, newMsg.id), 30000);
                  this._pending.set(newMsg.id, {
                    resolve: pending.resolve,
                    reject: pending.reject,
                    timeout: newTimeout,
                    method: pending.method,
                    params: newMsg.params,
                    _retried: true
                  });
                  return; // 不 reject，等重试结果
                }
              } catch (e) {
                console.error('[OpenClaw] 重试发送失败:', e);
              }
            }
          }
          pending.reject(new Error(errMsg));
        } else {
          // v3 format: payload contains the actual result
          pending.resolve(payload || msg);
        }
      }
      return;
    }

    // 3. 服务端事件推送
    if (type === 'event') {
      const eventName = event || method;
      this.emit(eventName, payload || params);
      this.emit('event', { event: eventName, payload: payload || params });
      return;
    }

    // 4. 其他消息
    this.emit('message', msg);
  }

  // 构建标准的 v3 connect 参数
  async _buildConnectParams(nonce) {
    const role = 'operator';
    const scopes = ['operator.read', 'operator.write', 'operator.admin'];
    const clientId = 'openclaw-control-ui';
    const clientMode = 'webchat';
    const platform = (navigator.platform || 'web').trim().toLowerCase();
    const params = {
      minProtocol: 4,
      maxProtocol: 4,
      client: {
        id: clientId,
        version: '1.0.0',
        platform: navigator.platform || 'web',
        mode: clientMode
      },
      role: role,
      scopes: scopes,
      caps: [],
      commands: [],
      permissions: {},
      auth: { token: this._token },
      locale: 'zh-CN',
      userAgent: 'SoloBrave/1.0.0 ' + navigator.userAgent
    };

    // 非安全上下文（crypto.subtle 不可用）→ 不带 device 字段，让网关走 token-only 流程
    // 或进入 pending 审批队列。FNV-1a / 指纹派生签名的 fallback 方案网关不认，
    // 反而会因为伪造签名 / 错配 ID 直接拒接，比"少带字段"更糟。
    if (!_hasSubtleCrypto()) {
      console.warn('[OpenClaw] 非安全上下文，跳过设备身份认证（仅凭 token 连接，网关可能进入待审批）');
      return params;
    }

    // 安全上下文 → 走 Ed25519 设备身份
    const identity = await _ensureDeviceIdentity();
    const signedAtMs = Date.now();
    // v3 auth payload（与网关侧校验逻辑一致：v3|deviceId|clientId|clientMode|role|scopes|signedAt|token|nonce|platform|deviceFamily）
    const deviceFamily = '';
    const payload = [
      'v3',
      identity.deviceId,
      clientId,
      clientMode,
      role,
      scopes.join(','),
      String(signedAtMs),
      this._token || '',
      nonce || '',
      platform,
      deviceFamily
    ].join('|');
    const signature = await _signDevicePayload(identity.privateKeyPkcs8, payload);

    params.device = {
      id: identity.deviceId,
      publicKey: identity.publicKeyRaw,
      signature: signature,
      signedAt: signedAtMs,
      nonce: nonce || ''
    };

    return params;
  }

  async _sendConnectImmediate(context) {
    console.log('[OpenClaw] 发送认证请求...');
    try {
      const id = this._generateId();
      const msg = JSON.stringify({
        type: 'req',
        id: id,
        method: 'connect',
        params: await this._buildConnectParams()
      });
      
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
      
      // v3 响应: payload.type === 'hello-ok'
      if (res?.type === 'hello-ok') {
        console.log('[OpenClaw] ✅ 认证成功！');
        this.authenticated = true;
        this.emit('authenticated', res);
        if (this._connectContext?.authTimeout) clearTimeout(this._connectContext.authTimeout);
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      } else {
        console.warn('[OpenClaw] 认证失败，启用 mock 模式, response:', JSON.stringify(res));
        this._enableMockMode();
        if (this._connectContext?.resolve) this._connectContext.resolve(true);
      }
    } catch (error) {
      console.warn('[OpenClaw] 认证请求失败，启用 mock 模式:', error.message || JSON.stringify(error));
      this._enableMockMode();
      if (this._connectContext?.resolve) this._connectContext.resolve(true);
    }
  }

  async _sendConnect(nonce, context) {
    console.log('[OpenClaw] 收到 challenge，回复认证 (nonce: ' + nonce + ')...');
    try {
      const id = this._generateId();
      const msg = JSON.stringify({
        type: 'req',
        id: id,
        method: 'connect',
        params: await this._buildConnectParams(nonce)
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
      
      if (res?.type === 'hello-ok') {
        console.log('[OpenClaw] ✅ challenge 认证成功！');
        this.authenticated = true;
        this.emit('authenticated', res);
        if (this._connectContext?.authTimeout) clearTimeout(this._connectContext.authTimeout);
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

      // 自动注入 agentId：网关配了多个 agent 时，每条请求必须显式带 agentId。
      // 调用方已显式传了就尊重；否则用 _defaultAgentId；如果默认是空（还没拉到
      // agents.list 或没 defaultId），就跳过注入，让网关按"未指定"处理。
      // connect 握手 / 自身认证类请求跳过注入。
      if (!SKIP_AGENTID_METHODS.has(method) && params && !params.agentId && this._defaultAgentId) {
        params = Object.assign({}, params, { agentId: this._defaultAgentId });
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

      // 把 method/params 一起存到 pending，方便错误重试时知道原请求是什么
      this._pending.set(id, { resolve, reject, timeout, method, params });
      this.ws.send(JSON.stringify(msg));
    });
  }

  // 切换默认 agentId 并持久化（用户手动选了某个 agent 后调）
  setDefaultAgentId(agentId) {
    if (!agentId || typeof agentId !== 'string') return;
    this._defaultAgentId = agentId;
    try { localStorage.setItem('openclaw_default_agent_id', agentId); } catch (e) {}
    console.log('[OpenClaw] 默认 agentId 已切换为:', agentId);
  }

  getDefaultAgentId() {
    return this._defaultAgentId;
  }

  // 拉取网关上的 agent 列表（多 agent 部署时让前端选默认）。
  // 网关 v3 实际返回结构是 { defaultId, agents: [...] }，但版本/配置差异可能输出别的形状；
  // 这里把 normalize 写厚一点：接受数组、{agents|items|data|list|results: []}、{agentId: ...} 单对象。
  _normalizeAgentsList(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    if (typeof raw !== 'object') return [];
    // 常见容器 key
    const containerKeys = ['agents', 'items', 'data', 'list', 'results', 'records'];
    for (const k of containerKeys) {
      if (Array.isArray(raw[k])) return raw[k];
    }
    // 单个 agent 对象（看起来像 agent）→ 包成数组
    if (raw.agentId || raw.id || raw.name) {
      return [raw];
    }
    // 最后兜底：把所有数组值合并
    const collected = [];
    for (const v of Object.values(raw)) {
      if (Array.isArray(v)) collected.push(...v);
    }
    return collected;
  }

  _extractAgentId(agentObj) {
    if (!agentObj || typeof agentObj !== 'object') return null;
    return agentObj.agentId || agentObj.id || agentObj.name || null;
  }

  // 从 agents.list 原始响应里抠 defaultId（可能挂在顶层 / 容器对象内 / 单 agent 上）
  _extractDefaultId(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const candidateKeys = ['defaultId', 'default_id', 'defaultAgentId', 'default_agent', 'activeId', 'currentId'];
    for (const k of candidateKeys) {
      if (typeof raw[k] === 'string' && raw[k]) return raw[k];
    }
    return null;
  }

  async listAgents(force = false) {
    if (!force && this._agentsCache && this._agentsCache.length) {
      return this._agentsCache;
    }
    try {
      const resp = await this.send('agents.list', {});
      // 调试：完整打印原始 payload（不截断），方便后续格式变化时快速对照
      let pretty = '(unserializable)';
      try { pretty = JSON.stringify(resp, null, 2); } catch (e) {}
      console.log('[OpenClaw] agents.list 原始响应（完整）:\n' + pretty);

      // 1) 抽 defaultId（网关告诉我们的默认 agent）
      const gatewayDefaultId = this._extractDefaultId(resp);
      if (gatewayDefaultId) {
        this._gatewayDefaultId = gatewayDefaultId;
        console.log('[OpenClaw] agents.list 返回 defaultId =', gatewayDefaultId);
      }

      // 2) 抽 agents 列表
      const list = this._normalizeAgentsList(resp);
      const normalized = list
        .filter(a => a && typeof a === 'object')
        .map(a => ({
          agentId: this._extractAgentId(a),
          name: a.name || a.label || a.displayName || a.agentId || a.id || '(unnamed)',
          raw: a
        }))
        .filter(a => !!a.agentId);
      this._agentsCache = normalized;
      console.log('[OpenClaw] agents.list 解析到', normalized.length, '个 agent:', normalized.map(a => a.agentId).join(', '));

      // 3) 自动选默认：用户没显式存过 → 优先网关 defaultId，没有再选列表第一个
      if (!force && !localStorage.getItem('openclaw_default_agent_id')) {
        let chosen = null;
        if (gatewayDefaultId && normalized.some(a => a.agentId === gatewayDefaultId)) {
          chosen = gatewayDefaultId;
          console.log('[OpenClaw] 自动选用网关 defaultId:', chosen);
        } else if (normalized.length) {
          chosen = normalized[0].agentId;
          console.log('[OpenClaw] 网关未指定 defaultId，自动选用列表第一个:', chosen);
        } else {
          console.warn('[OpenClaw] 没有任何 agent 可选，agentId 注入会跳过');
        }
        if (chosen) this.setDefaultAgentId(chosen);
      } else {
        console.log('[OpenClaw] 用户已选过 defaultAgentId:', this._defaultAgentId, '（尊重，不覆盖）');
      }
      return normalized;
    } catch (e) {
      console.warn('[OpenClaw] listAgents 失败:', e && e.message || e);
      return this._agentsCache || [];
    }
  }

  // 暴露给 index.html / office-v3.html 调试或 UI 切换用
  getGatewayDefaultId() {
    return this._gatewayDefaultId;
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
  async sendChat(sessionKey, message, options = {}) {
    const params = {
      sessionKey,
      message: message,
      idempotencyKey: Date.now().toString(36) + '-' + Math.random().toString(36).substring(2, 8)
    };
    // 合并额外参数（如 images、files 等）
    Object.assign(params, options);
    return this.send('chat.send', params);
  }


  // 中断生成
  async abortChat(sessionKey) {
    return this.send('chat.abort', { sessionKey });
  }

  // 重置指定 session（清空 OpenClaw 网关侧上下文）
  async resetSession(sessionKey) {
    return this.send('sessions.reset', { key: sessionKey });
  }

  // 获取模型列表
  async listModels() {
    return this.send('models.list', {});
  }

  // 健康检查
  async health() {
    return this.send('health', {});
  }

  // ========== Dreaming 功能 ==========

  // 获取 Dreaming 状态
  async getDreamingStatus(agentId) {
    return this.send('dreaming.status', { agentId: agentId || 'main' });
  }

  // 切换 Dreaming 状态
  async toggleDreaming(agentId, enabled) {
    return this.send('dreaming.toggle', { 
      agentId: agentId || 'main',
      enabled: enabled 
    });
  }

  // 获取支持的 Dreaming 阶段列表
  async listDreamingPhases() {
    return this.send('dreaming.phases', {});
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
