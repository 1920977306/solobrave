# SoloBrave AI 连接方案 - 双通道架构

## 架构概述

```
用户发送消息
    ↓
判断员工连接模式
    ↓
┌─────────────────────────────────┐
│ 1. OpenClaw WS（优先）          │
│    - 有 agentId → 走 WS         │
│    - 认证失败 → 降级到 API       │
├─────────────────────────────────┤
│ 2. API 直连（降级）             │
│    - 有 apiKey → 调 API         │
│    - 支持多供应商               │
├─────────────────────────────────┤
│ 3. Mock 模式（兜底）            │
│    - 都没有 → 模拟回复          │
└─────────────────────────────────┘
```

## 员工数据模型扩展

```javascript
// 新增字段
{
  id: 'emp_xxx',
  name: '龙虾',
  role: '后端架构师',
  avatar: 0,              // AVATAR_PRESETS index
  status: 'online',
  
  // === 新增连接配置 ===
  connectionMode: 'auto',  // 'auto' | 'openclaw' | 'api' | 'mock'
  agentId: 'lobster',      // OpenClaw agent ID（可选）
  
  // API 直连配置
  apiProvider: 'kimi',     // 'openai' | 'kimi' | 'deepseek' | 'custom'
  apiKey: '',              // 员工专属 API Key（可选，留空用全局）
  apiModel: 'kimi-code',  // 供应商对应的模型名
  apiEndpoint: '',         // 自定义端点（可选）
  
  // 系统提示词
  systemPrompt: '',        // 员工专属系统提示词
}
```

## API 供应商配置

```javascript
const API_PROVIDERS = {
  openai: {
    name: 'OpenAI',
    icon: '🔮',
    endpoint: 'https://api.openai.com/v1/chat/completions',
    models: ['gpt-4o', 'gpt-4o-mini', 'gpt-3.5-turbo'],
    defaultModel: 'gpt-4o'
  },
  kimi: {
    name: 'Kimi (Moonshot)',
    icon: '🌙',
    endpoint: 'https://api.moonshot.cn/v1/chat/completions',
    models: ['moonshot-v1-8k', 'moonshot-v1-32k', 'kimi-code'],
    defaultModel: 'kimi-code'
  },
  deepseek: {
    name: 'DeepSeek',
    icon: '🔍',
    endpoint: 'https://api.deepseek.com/v1/chat/completions',
    models: ['deepseek-chat', 'deepseek-coder', 'deepseek-reasoner'],
    defaultModel: 'deepseek-chat'
  },
  zhipu: {
    name: '智谱AI (GLM)',
    icon: '🔥',
    endpoint: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
    models: ['glm-4', 'glm-4-flash', 'glm-4-plus'],
    defaultModel: 'glm-4'
  },
  custom: {
    name: '自定义',
    icon: '⚙️',
    endpoint: '',
    models: [],
    defaultModel: ''
  }
};
```

## 消息发送流程（核心）

```javascript
async function sendMessage(text) {
  // 1. 添加用户消息到界面
  addUserMessage(text);
  
  // 2. 显示思考中
  showTypingIndicator();
  
  // 3. 获取当前员工
  const emp = getCurrentEmployee();
  
  // 4. 按优先级尝试连接
  let reply = null;
  
  // 尝试 OpenClaw WS
  if (emp.connectionMode !== 'api' && emp.connectionMode !== 'mock') {
    if (openclaw?.authenticated && emp.agentId) {
      try {
        reply = await sendViaOpenClaw(emp, text);
      } catch(e) {
        console.warn('[OpenClaw] 失败，降级到 API', e);
      }
    }
  }
  
  // 尝试 API 直连
  if (!reply && emp.connectionMode !== 'mock') {
    try {
      reply = await sendViaAPI(emp, text);
    } catch(e) {
      console.warn('[API] 失败', e);
    }
  }
  
  // Mock 兜底
  if (!reply) {
    reply = generateMockReply(emp, text);
  }
  
  // 5. 显示回复
  removeTypingIndicator();
  addAIMessage(emp, reply);
}
```

## OpenClaw WS 协议（重写）

### 认证流程
```
1. 客户端连接 ws://192.168.1.25:18789
2. 服务端推送: {type:"event", method:"connect.challenge", params:{challenge:"xxx"}}
3. 客户端发送: {type:"req", id:"uuid-1", method:"connect", params:{token:"xxx"}}
4. 服务端回复: {type:"res", id:"uuid-1", result:{status:"ok"}}
```

### 聊天流程
```
客户端发送: {type:"req", id:"uuid-2", method:"chat.send", params:{sessionKey:"agent:lobster:main", content:[{type:"text",text:"你好"}]}}
服务端回复: {type:"res", id:"uuid-2", result:{status:"ok"}}
服务端推送: {type:"event", method:"chat.message", params:{content:[{type:"text",text:"你好！"}]}}
```

## API 直连实现

```javascript
async function sendViaAPI(emp, userMessage) {
  const provider = API_PROVIDERS[emp.apiProvider || 'kimi'];
  if (!provider) throw new Error('未知供应商');
  
  // 获取 API Key：员工专属 > 全局配置
  const apiKey = emp.apiKey || AI_CONFIG.apiKey;
  if (!apiKey) throw new Error('未配置 API Key');
  
  // 构建消息
  const messages = [];
  if (emp.systemPrompt) {
    messages.push({role: 'system', content: emp.systemPrompt});
  } else {
    messages.push({role: 'system', content: `你是${emp.name}，一个${emp.role}。请用第一人称回复，保持角色一致性。`});
  }
  
  // 加载历史消息
  const history = getChatHistory(emp.id);
  if (history) {
    const recent = history.slice(-10); // 最近10条
    recent.forEach(m => messages.push(m));
  }
  
  messages.push({role: 'user', content: userMessage});
  
  // 调用 API
  const endpoint = emp.apiEndpoint || provider.endpoint;
  const model = emp.apiModel || provider.defaultModel;
  
  const res = await fetch(endpoint, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`
    },
    body: JSON.stringify({
      model: model,
      messages: messages,
      temperature: 0.8,
      stream: false
    })
  });
  
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(`API 错误 (${res.status}): ${err.error?.message || '未知'}`);
  }
  
  const data = await res.json();
  return data.choices?.[0]?.message?.content || '⚠️ 返回格式异常';
}
```

## CORS 问题处理

浏览器直接调 API 有 CORS 限制。解决方案（按优先级）：

1. **CoPaw Agent 代理**（推荐）
   - 在 Mac mini 上跑一个简单代理服务
   - `http://192.168.1.25:8080/api/proxy` → 转发到各 API
   - 最稳定，无 CORS 问题

2. **CORS 浏览器扩展**
   - Chrome 安装 "Allow CORS" 扩展
   - 开发环境够用

3. **Cloudflare Worker 代理**
   - 免费额度足够
   - 部署一次，永久可用

## 实施步骤

### Phase 1：API 直连（QwenPaw）
1. 添加 `API_PROVIDERS` 配置对象
2. 实现 `sendViaAPI()` 函数
3. 修改新增员工向导第三步，加 API 供应商选择
4. 修改员工详情面板，加连接配置 Tab
5. 修改 `sendMessage` 流程，API 直连优先于 mock

### Phase 2：OpenClaw WS（Jarvis）
1. 重写 `openclaw-client.js` 认证协议
2. 实现 `sendViaOpenClaw()` 函数
3. 添加 `chat.message` 事件监听
4. 连接状态 UI 指示

### Phase 3：整合（协作）
1. 实现三通道降级逻辑
2. 员工连接模式切换 UI
3. 错误处理 + 重试
4. 连接状态指示器

## 文件分工

| 文件 | 负责人 | 内容 |
|------|--------|------|
| `index.html` | QwenPaw | API_PROVIDERS、sendViaAPI、UI 配置 |
| `openclaw-client.js` | Jarvis | WS 认证协议、chat.send/receive |
| `index.html` | 整合 | sendMessage 降级逻辑、状态 UI |
