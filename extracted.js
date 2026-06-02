


// ===== 数据打通：认证与 API =====
var API_BASE = '';  // 与主页共用后端

// 获取认证头
function getAuthHeaders() {
  var token = localStorage.getItem('sb_auth_token');
  return token ? { 'Authorization': 'Bearer ' + token } : {};
}

// 带认证的 Fetch，自动处理 401
async function authFetch(url, options = {}) {
  options.headers = { ...options.headers || {}, ...getAuthHeaders() };
  var r = await fetch(url, options);
  if (r.status === 401) {
    localStorage.removeItem('sb_auth_token');
    localStorage.removeItem('sb_current_user');
    // 跳转回主页登录
    showToast('请先登录，正在跳转...', 'error');
    setTimeout(function(){ location.href = './index.html'; }, 1500);
    throw new Error('Unauthorized');
  }
  return r;
}

// 检查登录状态
function checkOfficeAuth() {
  var token = localStorage.getItem('sb_auth_token');
  if (!token || token === 'null') {
    showToast('请先在主页登录', 'error');
    setTimeout(function(){ location.href = './index.html'; }, 1500);
    return false;
  }
  return true;
}

// 页面加载时检查登录
document.addEventListener('DOMContentLoaded', function(){
  checkOfficeAuth();
});

// OpenClaw client loaded via openclaw-client.js

// Global OpenClaw instance
// openclaw defined by openclaw-client.js
var isOnline = false;
var docCache = {};
var currentDocName = 'IDENTITY.md';

// Initialize OpenClaw connection (using openclaw-client.js same as main page)
var OPENCLAW_CONFIG = {
  gatewayUrl: 'ws://192.168.1.25:18789',
  defaultToken: '8606e4d80b1accfaa4e22729466c40003cd217ce2bda93f3'
};

async function initOpenClaw() {
  // 先加载员工数据（从 localStorage 优先），不管 OpenClaw 是否连接
  await loadRealAgents();

  if(typeof openclaw === 'undefined'){
    console.warn('[Office] openclaw-client.js 未加载，使用离线模式');
    updateOpenClawStatus(false);
    return;
  }

  var ocToken = localStorage.getItem('openclaw_token') || OPENCLAW_CONFIG.defaultToken;
  if(!ocToken){
    console.warn('[Office] 无 OpenClaw token，使用离线模式');
    updateOpenClawStatus(false);
    return;
  }

  openclaw.setToken(ocToken);

  openclaw.on('authenticated', function(){
    console.log('[Office] OpenClaw 已认证');
    updateOpenClawStatus(true);
    // 认证成功后重新加载员工数据（可能之前401）
    loadEmployeesFromStorage().then(function(){
      renderChatZone();
      renderWorkZone();
      renderLoungeZone();
    });
  });

  openclaw.on('disconnected', function(){
    console.log('[Office] OpenClaw 断开');
    updateOpenClawStatus(false);
  });

  openclaw.on('error', function(err){
    console.error('[Office] OpenClaw 错误:', err);
    updateOpenClawStatus(false);
  });

  try {
    await openclaw.connect();
  } catch (err) {
    console.log('[Office] OpenClaw 连接失败，使用离线模式:', err.message);
    updateOpenClawStatus(false);
  }
}

function updateOpenClawStatus(connected){
  isOnline = connected;
  var statusDot = document.getElementById('wsStatusDot');
  var statusText = document.getElementById('wsStatusText');
  var configStatus = document.getElementById('aiConfigStatus');

  if (connected) {
    if(statusDot) statusDot.style.background = 'var(--green)';
    if(statusText){ statusText.textContent = '🟢在线'; statusText.style.color = 'var(--green)'; }
    if (configStatus) {
      configStatus.innerHTML = '<span style="width:8px;height:8px;background:var(--green);border-radius:50%"></span>在线模式';
      configStatus.style.color = 'var(--green)';
    }
  } else {
    if(statusDot) statusDot.style.background = 'var(--gray-4)';
    if(statusText){ statusText.textContent = '❌离线'; statusText.style.color = 'var(--gray-4)'; }
    if (configStatus) {
      configStatus.innerHTML = '<span style="width:8px;height:8px;background:var(--gray-4);border-radius:50%"></span>离线模式';
      configStatus.style.color = 'var(--gray-4)';
    }
  }
}


// ===== 数据定义 (支持 Mock 和 Real) =====
var employees = [];
var activities = [];

var activitiesFallback = [
  { id:1, name:'Lucy', initial:'L', gradient:['#FF6B9D','#C44FE2'], status:'thinking', text:'正在分析用户需求...', time:'刚刚' },
  { id:2, name:'Cute', initial:'C', gradient:['#FF9500','#FF2D55'], status:'thinking', text:'设计评审进行中...', time:'刚刚' },
  { id:3, name:'Eric', initial:'E', gradient:['#5AC8FA','#34C759'], status:'done', text:'✅ 前端组件库更新完成', time:'2分钟前' },
  { id:4, name:'Gates', initial:'G', gradient:['#5856D6','#007AFF'], status:'done', text:'✅ API 接口文档已同步', time:'5分钟前' },
  { id:5, name:'Nova', initial:'N', gradient:['#AF52DE','#FF375F'], status:'idle', text:'💤 等待测试任务分配', time:'10分钟前' },
  { id:6, name:'Mike', initial:'M', gradient:['#00C7BE','#30D158'], status:'p1', text:'⚠️ 服务器负载告警', time:'15分钟前' },
];

var employeesFallback = [
  { id:'lucy', name:'Lucy', role:'产品经理', initial:'L', gradient:['#FF6B9D','#C44FE2'], status:'online', avatar:0 },
  { id:'gates', name:'Gates', role:'架构师', initial:'G', gradient:['#5856D6','#007AFF'], status:'busy', avatar:1 },
  { id:'eric', name:'Eric', role:'前端开发', initial:'E', gradient:['#5AC8FA','#34C759'], status:'online', avatar:2 },
  { id:'cute', name:'Cute', role:'UI设计', initial:'C', gradient:['#FF9500','#FF2D55'], status:'idle', avatar:3 },
  { id:'alex', name:'Alex', role:'后端开发', initial:'A', gradient:['#30B0C7','#5856D6'], status:'offline', avatar:4 },
  { id:'nova', name:'Nova', role:'测试工程师', initial:'N', gradient:['#AF52DE','#FF375F'], status:'online', avatar:5 },
  { id:'mike', name:'Mike', role:'DevOps', initial:'M', gradient:['#00C7BE','#30D158'], status:'busy', avatar:6 },
  { id:'lisa', name:'Lisa', role:'运营', initial:'I', gradient:['#FF6B35','#FFD60A'], status:'idle', avatar:7 },
];

function initMockData() {
  employees = [...employeesFallback];
  activities = [...activitiesFallback];
}

// ===== 数据打通：加载员工数据 =====
function loadEmployeesFromStorage() {
  return new Promise(function(resolve) {
    var token = localStorage.getItem('sb_auth_token');
    if (!token) {
      console.warn('[Office] No auth token, using mock data');
      initMockData();
      resolve();
      return;
    }
    
    // V2: 直接从后端获取，后端已按当前用户角色过滤
    fetch('/api/agents', {
      headers: { 'Authorization': 'Bearer ' + token }
    })
    .then(function(res) {
      if (res.ok) return res.json();
      throw new Error('API error: ' + res.status);
    })
    .then(function(data) {
      if (data && Array.isArray(data) && data.length > 0) {
        employees = data;
        console.log('[Office] Loaded ' + employees.length + ' employees from API (role-filtered)');
      } else {
        console.warn('[Office] API returned empty, using mock data');
        initMockData();
      }
      resolve();
    })
    .catch(function(err) {
      console.warn('[Office] API failed, using mock data:', err);
      initMockData();
      resolve();
    });
  });
}

// Load real agents from OpenClaw (带 localStorage 降级)
function loadRealAgents() {
  // V2: 直接调用 loadEmployeesFromStorage（已从后端API加载）
  return loadEmployeesFromStorage();
}

// Get agent identity details
async function getAgentIdentity(agentId) {
  if (!openclaw || !openclaw._connected) {
    // Return mock data
    return {
      model: 'glm-5 (BigModel)',
      agentId: agentId,
      createdAt: '2026/2/28',
      status: '在线',
      sessions: 0,
      lastActive: '10分钟前'
    };
  }

  try {
    var identity = await openclaw.agent_identity_get(agentId);
    var sessions = await openclaw.listSessions();
    
    return {
      model: (identity.config && identity.config.model) || identity.model || 'unknown',
      agentId: agentId,
      createdAt: identity.created_at || new Date().toLocaleDateString(),
      status: identity.status || '在线',
      sessions: Array.isArray(sessions) ? sessions.length : 0,
      lastActive: '刚刚'
    };
  } catch (err) {
    console.error('[OpenClaw] Failed to get identity:', err);
    return null;
  }
}

// Load available models
async function loadModels() {
  var select = document.getElementById('modelSelect');
  if (!select) return;

  // Show loading
  select.innerHTML = '<option>加载中...</option>';
  select.disabled = true;

  if (!openclaw || !openclaw._connected) {
    // Use fallback models
    select.innerHTML = '<option value="glm-5">glm-5 (BigModel)</option>' +
      '<option value="gpt-4">gpt-4 (OpenAI)</option>' +
      '<option value="claude-3">claude-3 (Anthropic)</option>';
    select.disabled = false;
    return;
  }

  try {
    var models = await openclaw.listModels();
    if (Array.isArray(models) && models.length > 0) {
      var html = '';
      models.forEach(function(m){
        html += '<option value="' + (m.id || m.name) + '">' + (m.name || m.id) + '</option>';
      });
      select.innerHTML = html;
    } else {
      // Fallback
      select.innerHTML = '<option value="glm-5">glm-5 (BigModel)</option>' +
        '<option value="gpt-4">gpt-4 (OpenAI)</option>' +
        '<option value="claude-3">claude-3 (Anthropic)</option>';
    }
    select.disabled = false;
  } catch (err) {
    console.error('[OpenClaw] Failed to load models:', err);
    select.innerHTML = '<option value="glm-5">glm-5 (BigModel)</option>' +
      '<option value="gpt-4">gpt-4 (OpenAI)</option>' +
      '<option value="claude-3">claude-3 (Anthropic)</option>';
    select.disabled = false;
  }
}

// Load channel status
async function loadChannels() {
  var container = document.getElementById('channelTabs');
  if (!container) return;

  if (!openclaw || !openclaw._connected) {
    // Use fallback channels
    container.innerHTML = '<div class="channel-tab active" data-channel="dingtalk" onclick="switchChannelTab(this)">钉钉 <span class="channel-check">✓</span></div>' +
      '<div class="channel-tab" data-channel="feishu" onclick="switchChannelTab(this)">飞书</div>' +
      '<div class="channel-tab" data-channel="agentlink" onclick="switchChannelTab(this)">AgentLink <span class="channel-check">✓</span></div>' +
      '<div class="channel-tab" data-channel="telegram" onclick="switchChannelTab(this)">Telegram <span class="channel-check">✓</span></div>';
    return;
  }

  try {
    var status = await openclaw.send('channels.status', {});
    if (status && typeof status === 'object') {
      var channels = ['dingtalk', 'feishu', 'agentlink', 'telegram'];
      var channelNames = { dingtalk: '钉钉', feishu: '飞书', agentlink: 'AgentLink', telegram: 'Telegram' };
      
      var html = '';
      channels.forEach(function(ch){
        var enabled = (status[ch] && status[ch].enabled) || false;
        html += '<div class="channel-tab' + (enabled ? ' active' : '') + '" data-channel="' + ch + '" onclick="switchChannelTab(this)">' +
          channelNames[ch] + (enabled ? ' <span class="channel-check">✓</span>' : '') +
          '</div>';
      });
      container.innerHTML = html;
    }
  } catch (err) {
    console.error('[OpenClaw] Failed to load channels:', err);
    // Fallback
    container.innerHTML = '<div class="channel-tab active" data-channel="dingtalk" onclick="switchChannelTab(this)">钉钉 <span class="channel-check">✓</span></div>' +
      '<div class="channel-tab" data-channel="feishu" onclick="switchChannelTab(this)">飞书</div>' +
      '<div class="channel-tab" data-channel="agentlink" onclick="switchChannelTab(this)">AgentLink</div>' +
      '<div class="channel-tab" data-channel="telegram" onclick="switchChannelTab(this)">Telegram</div>';
  }
}

// Toggle channel
async function toggleChannel(channelId, enable) {
  if (!openclaw || !openclaw._connected) {
    showToast('🔴 离线模式：无法切换渠道', 'error');
    return;
  }

  showToast((enable ? '启动' : '停止') + ' ' + channelId + '...', 'loading');

  try {
    if (enable) {
      await openclaw.send('channels.start', {channel_id: channelId});
    } else {
      await openclaw.send('channels.stop', {channel_id: channelId});
    }
    showToast('✅ ' + channelId + ' ' + (enable ? '已启动' : '已停止'), 'success');
    await loadChannels();
  } catch (err) {
    console.error('[OpenClaw] Failed to toggle channel:', err);
    showToast('❌ 操作失败: ' + (err.message || ''), 'error');
  }
}

// Load document content
async function loadDocContent(docName) {
  if(!currentAgentId){
    return '请先选择一个员工';
  }
  try {
    var resp = await authFetch('/api/openclaw/agent-docs/' + encodeURIComponent(currentAgentId) + '?doc=' + encodeURIComponent(docName));
    if(resp && resp.ok){
      var data = await resp.json();
      var content = data.content || '';
      var emp = employees.find(function(e){ return e.id === currentAgentId; });

      // 如果后端返回的是默认模板，优先用本地保存的个性化内容
      if(emp){
        if(docName === 'TOOLS.md' && emp.toolsDoc && content.indexOf('Skills define _how_ tools work') > -1){
          return emp.toolsDoc;
        }
        if(docName === 'USER.md' && emp.userDoc && content.indexOf('USER.md 学员适配档案') === -1 && content.indexOf('This file teaches the model') > -1){
          return emp.userDoc;
        }
      }
      return content;
    } else {
      // 回退：从 employees 数组读
      var emp = employees.find(function(e){ return e.id === currentAgentId; });
      if(emp){
        if(docName === 'SOUL.md') return emp.soulDoc || emp.systemPrompt || '';
        if(docName === 'IDENTITY.md') return emp.idDoc || (emp.name + ' - ' + emp.role);
        if(docName === 'USER.md') return emp.userDoc || emp.user || '';
        if(docName === 'TOOLS.md') return emp.toolsDoc || emp.tools || '';
      }
      return '';
    }
  } catch(err){
    console.error('[Office] 加载文档失败:', err);
    // 回退到本地数据
    var emp = employees.find(function(e){ return e.id === currentAgentId; });
    if(emp){
      if(docName === 'SOUL.md') return emp.soulDoc || emp.systemPrompt || '';
      if(docName === 'IDENTITY.md') return emp.idDoc || (emp.name + ' - ' + emp.role);
      if(docName === 'USER.md') return emp.userDoc || emp.user || '';
      if(docName === 'TOOLS.md') return emp.toolsDoc || emp.tools || '';
    }
    return '';
  }
}

// Set document content
async function saveDocContent(docName, content) {
  if(!currentAgentId){
    showToast('请先选择一个员工');
    return false;
  }
  showToast('正在保存...');

  try {
    var emp = employees.find(function(e){ return e.id === currentAgentId; });
    var workspacePath = emp && emp.openclawName ? '~/.openclaw/workspace-' + emp.openclawName : '';
    
    var body = {
      agentId: currentAgentId,
      workspacePath: workspacePath
    };
    if(docName === 'SOUL.md') body.soulDoc = content;
    else if(docName === 'IDENTITY.md') body.identityDoc = content;
    else if(docName === 'USER.md') body.userDoc = content;
    else if(docName === 'TOOLS.md') body.toolsDoc = content;

    var resp = await authFetch('/api/openclaw/write-agent-docs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    
    if(resp && resp.ok){
      // 同时更新本地 emp 对象
      if(emp){
        if(docName === 'SOUL.md') emp.soulDoc = content;
        else if(docName === 'IDENTITY.md') emp.idDoc = content;
        else if(docName === 'USER.md') emp.userDoc = content;
        else if(docName === 'TOOLS.md') emp.toolsDoc = content;
      }
      showToast('✅ 文档已保存');
      // 通知主页刷新
      if(typeof lobsterChannel !== 'undefined' && lobsterChannel){
        lobsterChannel.postMessage({type: 'employees_updated'});
      }
      return true;
    } else {
      var errData = resp ? await resp.json() : {};
      showToast('❌ 保存失败: ' + (errData.error || '服务器错误'));
      return false;
    }
  } catch(err){
    console.error('[Office] 保存文档失败:', err);
    showToast('❌ 保存失败: ' + err.message);
    return false;
  }
}

// Save model config
async function saveModelConfig() {
  var select = document.getElementById('modelSelect');
  var textarea = document.getElementById('modelJson');
      var modelId = (select && select.value);
      var jsonConfig = (textarea && textarea.value);

  if (!modelId) {
    showToast('请选择模型', 'error');
    return;
  }

  if (!openclaw || !openclaw._connected) {
    showToast('🔴 离线模式：配置仅本地保存', 'error');
    localStorage.setItem('mock_model_config', JSON.stringify({ model: modelId, config: jsonConfig }));
    return;
  }

  showToast('正在保存模型配置...', 'loading');

  try {
    await openclaw.send('config.set', {key: 'model', value: modelId});
    if (jsonConfig) {
      await openclaw.send('config.set', {key: 'model.config', value: jsonConfig});
    }
    showToast('✅ 模型配置已保存', 'success');
    closeModelConfig();
  } catch (err) {
    console.error('[OpenClaw] Failed to save config:', err);
    showToast('❌ 保存失败: ' + (err.message || ''), 'error');
  }
}

// Test chat
async function testChat() {
  var textarea = document.getElementById('modelJson');
      var testMessage = (textarea && textarea.value) || 'Hello, this is a test message.';

  if (!openclaw || !openclaw._connected) {
    showToast('🔴 离线模式：无法发送测试消息', 'error');
    return;
  }

  var btn = document.querySelector('.config-test-btn');
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 发送中...';
  }

  showToast('正在发送测试消息...', 'loading');

  try {
    await openclaw.sendChat('test_session', testMessage);
    showToast('✅ 测试消息已发送', 'success');
  } catch (err) {
    console.error('[OpenClaw] Failed to send test:', err);
    showToast('❌ 测试失败: ' + (err.message || ''), 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '测试对话';
    }
  }
}

// Create new model
function createModel() {
  showToast('🏗️ 打开模型创建表单...', 'loading');
  var textarea = document.getElementById('modelJson');
  if (textarea) {
    textarea.value = '{\n  "name": "my-model",\n  "provider": "custom",\n  "apiUrl": "https://api.example.com/v1",\n  "apiKey": "your-api-key",\n  "model": "gpt-4",\n  "maxTokens": 2000,\n  "temperature": 0.7\n}';
  }
}

// ===== BroadcastChannel 状态同步 (扩展版) =====
var lobsterChannel = null;

function initBroadcastChannel() {
  try {
    lobsterChannel = new BroadcastChannel('lobster-office');
    
    lobsterChannel.onmessage = function(event) {
      var data = event.data;
      var type = data.type;
      var agent_id = data.agent_id;
      var status = data.status;
      var empData = data.employees;
      
      switch (type) {
        case 'lobster_status':
          // 更新员工状态
          if (agent_id && status) {
            var emp = null;
            for(var i=0;i<employees.length;i++){
              if(employees[i].id === agent_id){ emp=employees[i]; break; }
            }
            if (emp) {
              var oldStatus = emp.status;
              emp.status = mapStatus(status);
              
              if (emp.status !== oldStatus) {
                if (status === 'thinking') {
                  addTimelineEvent(emp.name, '开始思考...');
                } else if (status === 'idle') {
                  addTimelineEvent(emp.name, '进入空闲状态');
                } else if (status === 'online') {
                  addTimelineEvent(emp.name, '上线');
                }
              }
              
              renderChatZone();
              renderWorkZone();
              renderLoungeZone();
              updateStats();
            }
          }
          break;
          
        case 'status_change':
          // 某个员工状态变了，只更新该员工的显示（不全量刷新）
          if (agent_id && status) {
            var emp = null;
            for(var i=0;i<employees.length;i++){
              if(employees[i].id === agent_id){ emp=employees[i]; break; }
            }
            if (emp) {
              emp.status = mapStatus(status);
              // 添加淡入动画效果
              var workZone = document.getElementById('workZone');
              var loungeZone = document.getElementById('loungeZone');
              if(workZone) workZone.style.opacity = '0.7';
              if(loungeZone) loungeZone.style.opacity = '0.7';
              setTimeout(function(){
                renderWorkZone();
                renderLoungeZone();
                if(workZone) workZone.style.opacity = '1';
                if(loungeZone) loungeZone.style.opacity = '1';
              }, 150);
              updateStats();
            }
          }
          break;
          
        case 'new_message':
          // 有新聊天消息，办公室对话区显示最新一条
          if (agent_id) {
            var emp = null;
            for(var i=0;i<employees.length;i++){
              if(employees[i].id === agent_id){ emp=employees[i]; break; }
            }
            if(emp){
              var msgText = data.msg || '';
              var direction = data.direction || 'incoming';
              if(direction === 'outgoing'){
                addTimelineEvent(emp.name, '发送: ' + (msgText.length > 20 ? msgText.substring(0,20) + '...' : msgText));
              } else {
                addTimelineEvent(emp.name, '回复: ' + (msgText.length > 20 ? msgText.substring(0,20) + '...' : msgText));
              }
              // 刷新对话区并添加淡入效果
              var chatZone = document.getElementById('chatZone');
              if(chatZone) chatZone.style.opacity = '0.7';
              setTimeout(function(){
                renderChatZone();
                if(chatZone) chatZone.style.opacity = '1';
              }, 150);
            }
          }
          break;
          
        case 'employees_updated':
          // V2: 收到更新通知，从后端重新加载（后端已按角色过滤）
          loadEmployeesFromStorage().then(function() {
            // 添加淡入动画
            var workZone = document.getElementById('workZone');
            var loungeZone = document.getElementById('loungeZone');
            var chatZone = document.getElementById('chatZone');
            if(workZone) workZone.style.opacity = '0.5';
            if(loungeZone) loungeZone.style.opacity = '0.5';
            if(chatZone) chatZone.style.opacity = '0.5';
            setTimeout(function(){
              renderChatZone();
              renderWorkZone();
              renderLoungeZone();
              renderEmployees();
              updateStats();
              if(workZone) workZone.style.opacity = '1';
              if(loungeZone) loungeZone.style.opacity = '1';
              if(chatZone) chatZone.style.opacity = '1';
            }, 200);
          });
          break;
          
        case 'chat_message':
          // 主页收到新消息 - 刷新对话区显示
          if (agent_id) {
            var emp = null;
            for(var i=0;i<employees.length;i++){
              if(employees[i].id === agent_id){ emp=employees[i]; break; }
            }
            if(emp){
              var direction = data.direction || 'incoming';
              if(direction === 'outgoing'){
                addTimelineEvent(emp.name, '用户发送消息');
              } else {
                addTimelineEvent(emp.name, '回复了消息');
              }
            }
            // 刷新对话区并高亮新消息
            renderChatZone();
            scrollChatZoneToTop(agent_id);
          }
          break;
          
        case 'employee_created':
          // 新员工创建
          if (empData) {
            loadEmployeesFromStorage().then(function() {
              renderChatZone();
              renderWorkZone();
              renderLoungeZone();
              renderEmployees();
              updateStats();
              addTimelineEvent(empData.name || '新员工', '加入团队');
            });
          }
          break;
          
        case 'employee_deleted':
          // 员工被删除
          if (agent_id) {
            loadEmployeesFromStorage().then(function() {
              renderChatZone();
              renderWorkZone();
              renderLoungeZone();
              renderEmployees();
              updateStats();
            });
          }
          break;
          
        default:
          console.log('[BroadcastChannel] Unknown message type:', type);
      }
    };
    
    console.log('[BroadcastChannel] lobster-office initialized');
  // 初始化动态面板 - 显示员工上线状态
  for(var i=0;i<employees.length;i++){
    if(employees[i].status === 'online' || employees[i].status === 'busy'){
      addTimelineEvent(employees[i].name, '🟢 已上线');
    }
  }
  } catch (err) {
    console.log('[BroadcastChannel] Not supported or failed:', err);
  }
}

function mapStatus(status) {
  var map = {
    'thinking': 'thinking',
    'using_tool': 'using_tool',
    'writing': 'writing',
    'reading': 'reading',
    'coding': 'coding',
    'idle': 'idle',
    'offline': 'offline',
    'online': 'online'
  };
  return map[status] || 'online';
}

// Broadcast status to other tabs
function broadcastStatus(agentId, status) {
  if (lobsterChannel) {
    lobsterChannel.postMessage({
      type: 'lobster_status',
      agent_id: agentId,
      status: status
    });
  }
}

var skillPresets = [
  {name:'brainstorm', emoji:'💡', desc:'头脑风暴'},
  {name:'debug', emoji:'🔧', desc:'系统调试'},
  {name:'tdd', emoji:'🧪', desc:'测试驱动'},
  {name:'plan', emoji:'📋', desc:'任务规划'},
  {name:'review', emoji:'👀', desc:'代码审查'},
  {name:'refactor', emoji:'♻️', desc:'代码重构'},
];

var docsData = [
  {id:1, name:'产品需求文档 PRD', icon:'📋', type:'project', tags:['产品','需求'], date:'今天更新'},
  {id:2, name:'技术架构设计', icon:'🏗️', type:'tech', tags:['架构','后端'], date:'昨天'},
  {id:3, name:'UI 设计规范 v2', icon:'🎨', type:'design', tags:['设计','规范'], date:'3天前'},
  {id:4, name:'API 接口文档', icon:'🔌', type:'tech', tags:['API','前端'], date:'本周'},
  {id:5, name:'部署流程手册', icon:'🚀', type:'ops', tags:['运维','部署'], date:'上周'},
  {id:6, name:'Sprint 迭代计划', icon:'📅', type:'project', tags:['敏捷','计划'], date:'今天'},
];

// ===== Tab 切换 =====
function switchTab(tab){
  // Tab 切换已简化：办公室只显示三大区域+动态面板
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c=>c.classList.remove('active'));
  if (event && event.target) event.target.classList.add('active');
  var tabEl = document.getElementById('tab-'+tab); if (tabEl) tabEl.classList.add('active');
}

// ===== 统计数字动画 =====
function animateNumber(el,target,duration){
  if(!el) return;
  duration = duration || 800;
  var start = parseInt(el.textContent)||0;
  var startTime = performance.now();
  function update(ct){
    var progress = Math.min((ct-startTime)/duration,1);
    var ease = 1-Math.pow(1-progress,3);
    el.textContent = Math.floor(start+(target-start)*ease);
    if(progress<1) requestAnimationFrame(update);
  }
  requestAnimationFrame(update);
}

// ===== 更新统计 =====
function updateStats(){
  // 统计各状态员工数
  var online = employees.filter(function(e){return e.status==='online'}).length;
  var working = employees.filter(function(e){
    return e.status==='busy' || e.status==='working' || e.status==='coding' || e.status==='writing' || e.status==='reading' || e.status==='thinking';
  }).length;
  var idle = employees.filter(function(e){return e.status==='idle'}).length;
  var pending = activities.filter(function(a){return a.status==='p1'}).length;

  animateNumber(document.getElementById('statOnline'),online);
  animateNumber(document.getElementById('statWorking'),working);
  animateNumber(document.getElementById('statIdle'),idle);
  animateNumber(document.getElementById('statPending'),pending);

  // 更新百分比
  var elPct;
  elPct = document.getElementById('statOnlinePct'); if(elPct) elPct.textContent = Math.round(online/employees.length*100)+'%';
  elPct = document.getElementById('statWorkingPct'); if(elPct) elPct.textContent = Math.round(working/employees.length*100)+'%';
  elPct = document.getElementById('statIdlePct'); if(elPct) elPct.textContent = Math.round(idle/employees.length*100)+'%';
  elPct = document.getElementById('statPendingPct'); if(elPct) elPct.textContent = Math.round(pending/activities.length*100)+'%';

  // 更新底部状态栏 - 彩色统计
  var elOnline = document.getElementById('statusOnline');
  var elWorking = document.getElementById('statusWorking');
  var elIdle = document.getElementById('statusIdle');
  var elPending = document.getElementById('statusPending');
  if(elOnline) elOnline.textContent = online;
  if(elWorking) elWorking.textContent = working;
  if(elIdle) elIdle.textContent = idle;
  if(elPending) elPending.textContent = pending;
}

// ===== 渲染活动列表 =====
function renderActivities(){
  var container = document.getElementById('liveActivities');
  if(!container) return;
  // 动态面板现在用 timeline-list 渲染，不再用 activities 数组
  // timeline-list 由 addTimelineEvent 动态添加条目
}

// 跳转到主页打开对应员工聊天
function goToMainChat(empId) {
  window.open('./index.html?emp=' + empId, '_blank');
}

// 渲染员工网格 - 点击跳转到主页
function renderEmployees(){
  var grid = document.getElementById('employeeGrid');
  if(!grid)return;
  grid.innerHTML = employees.map(function(emp, idx){
    var avatarHtml = renderAvatar(emp, 64);
    var statusText = emp.status==='online'?'● 在线':emp.status==='busy'?'● 忙碌':emp.status==='offline'?'● 离线':'● 空闲';
    return '<div class="emp-card" onclick="openDetailPanel(\''+emp.id+'\')" style="cursor:pointer" title="点击查看 '+emp.name+' 的详情">' +
      '<div class="emp-avatar" style="background:linear-gradient(135deg,'+(emp.gradient && emp.gradient[0] || '#FFD60A')+' 0%,'+(emp.gradient && emp.gradient[1] || '#FFB800')+' 100%);overflow:hidden;">'+avatarHtml+'</div>' +
      '<div class="emp-name">'+emp.name+'</div>' +
      '<div class="emp-role">'+emp.role+'</div>' +
      '<div class="emp-status '+emp.status+'">'+statusText+'</div>' +
    '</div>';
  }).join('');
}

// 辅助函数：截断消息文本
function truncateMessage(text, maxLen) {
  if (!text) return '';
  if (text.length <= maxLen) return text;
  return text.substring(0, maxLen) + '...';
}

// 辅助函数：获取员工聊天记录的最后一条消息
function getLastMessage(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      if (msgs && msgs.length > 0) {
        return msgs[msgs.length - 1];
      }
    } catch (e) {}
  }
  return null;
}

// 辅助函数：计算未读消息数
function getUnreadCount(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      return msgs.length;
    } catch (e) {}
  }
  return 0;
}

// ===== 辅助函数：获取相对时间 =====
function formatTimeAgo(timestamp) {
  if (!timestamp) return '刚刚';
  var now = new Date();
  var then = new Date(timestamp);
  var diffMs = now.getTime() - then.getTime();
  var diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return diffMin + '分钟前';
  var diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return diffHour + '小时前';
  var diffDay = Math.floor(diffHour / 24);
  return diffDay + '天前';
}

// ===== 辅助函数：获取最新消息预览 =====
function getLatestMessage(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      if (msgs && msgs.length > 0) {
        var lastMsg = msgs[msgs.length - 1];
        // 返回消息内容
        if (lastMsg.content) return lastMsg.content;
        if (lastMsg.text) return lastMsg.text;
        if (lastMsg.message) return lastMsg.message;
        // 如果是对象，尝试获取其他字段
        for (var prop in lastMsg) {
          if (typeof lastMsg[prop] === 'string' && lastMsg[prop].length > 0 && lastMsg[prop].length < 500) {
            return lastMsg[prop];
          }
        }
      }
    } catch (e) {}
  }
  return null;
}

// ===== 辅助函数：获取消息时间戳 =====
function getMessageTimestamp(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      if (msgs && msgs.length > 0) {
        var lastMsg = msgs[msgs.length - 1];
        if (lastMsg.timestamp) return lastMsg.timestamp;
        if (lastMsg.time) return lastMsg.time;
        if (lastMsg.date) return lastMsg.date;
      }
    } catch (e) {}
  }
  return null;
}

// ===== 辅助函数：获取未读消息数 =====
function getUnreadMessages(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      return msgs && msgs.length > 0 ? msgs.length : 0;
    } catch (e) {}
  }
  return 0;
}

// ===== 辅助函数：获取最后一条AI消息作为当前任务描述 =====
function getLastAIMessage(empId) {
  var key = 'sb_chat_history_' + empId;
  var hist = localStorage.getItem(key);
  if (hist) {
    try {
      var msgs = JSON.parse(hist);
      if (msgs && msgs.length > 0) {
        // 从后往前找AI回复
        for (var i = msgs.length - 1; i >= 0; i--) {
          var msg = msgs[i];
          // 判断是否是AI消息（role为assistant或ai）
          if (msg.role === 'assistant' || msg.role === 'ai' || msg.isAI === true) {
            var text = msg.content || msg.text || msg.message || '';
            if (text.length > 0) {
              // 截断30字
              if (text.length > 30) {
                text = text.substring(0, 30) + '...';
              }
              return text;
            }
          }
        }
      }
    } catch (e) {}
  }
  // 根据状态返回默认文本
  var emp = employees.find(function(e) { return e.id === empId; });
  if (emp) {
    if (emp.status === 'offline') return '待命中';
    if (emp.status === 'thinking') return '处理中...';
    if (emp.status === 'busy' || emp.status === 'working') return '等待任务';
    if (emp.status === 'online') return '空闲中';
  }
  return '空闲';
}

// ===== 渲染 Chat Zone - 实时消息流格式 =====
function escapeAttr(s){if(!s)return '';return s.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function escapeHtml(s){if(!s)return '';return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

// 格式化时长：99分钟 -> 1小时39分钟
function formatDuration(minutes){
  if(minutes < 60) return minutes + ' 分钟';
  var hours = Math.floor(minutes / 60);
  var mins = minutes % 60;
  if(mins === 0) return hours + ' 小时';
  return hours + '小时' + mins + '分钟';
}

function renderAvatar(emp, size, radius, wrapperClass){
  size = size || 48;
  radius = radius || '50%';
  var style = 'width:' + size + 'px;height:' + size + 'px;border-radius:' + radius + ';object-fit:cover;display:block;';
  var inner;
  // 1. Number index -> use AVATAR_PRESETS
  if(typeof emp.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[emp.avatar]){
    inner = '<img src="' + escapeAttr(AVATAR_PRESETS[emp.avatar]) + '" style="' + style + '">';
  } else if(emp.avatar && typeof emp.avatar === 'string' && (emp.avatar.indexOf('data:image') === 0 || emp.avatar.indexOf('.png') > 0 || emp.avatar.indexOf('.jpg') > 0 || emp.avatar.indexOf('.jpeg') > 0 || emp.avatar.indexOf('avatars/') === 0)){
    // 2. Image path or data URL
    inner = '<img src="' + escapeAttr(emp.avatar) + '" style="' + style + '">';
  } else if(typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS.length > 0){
    // 3. Letter/emoji string or any unknown -> use name hash to pick a preset avatar
    var hash = 0;
    var name = emp.name || emp.id || '';
    for(var c = 0; c < name.length; c++){
      hash = ((hash << 5) - hash) + name.charCodeAt(c);
      hash = hash & hash;
    }
    var idx = Math.abs(hash) % AVATAR_PRESETS.length;
    inner = '<img src="' + escapeAttr(AVATAR_PRESETS[idx]) + '" style="' + style + '">';
  } else {
    // 4. Ultimate fallback: letter
    inner = '<span style="font-size:' + Math.floor(size * 0.5) + 'px;font-weight:700;color:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;width:' + size + 'px;height:' + size + 'px;border-radius:' + radius + ';">' + (emp.name ? emp.name.charAt(0).toUpperCase() : '?') + '</span>';
  }
  if(wrapperClass){
    return '<div class="' + wrapperClass + '" style="width:' + size + 'px;height:' + size + 'px;border-radius:' + radius + ';overflow:hidden;">' + inner + '</div>';
  }
  return inner;
}

// ===== 渲染对话区 - 新版设计 =====
function renderChatZone() {
  var chatZone = document.getElementById('chatZone');
  if (!chatZone) return;
  
  // 对话区：只显示 status === 'thinking' 的员工（正在思考/对话中）
  var chatters = [];
  for (var i = 0; i < employees.length; i++) {
    var e = employees[i];
    if (e.status === 'thinking') {
      chatters.push(e);
    }
  }
  
  chatters.sort(function(a, b) {
    var tsA = getMessageTimestamp(a.id);
    var tsB = getMessageTimestamp(b.id);
    if (!tsA && !tsB) return 0;
    if (!tsA) return 1;
    if (!tsB) return -1;
    return tsB - tsA;
  });
  
  if (chatters.length === 0) {
    chatZone.innerHTML = '<div style="text-align:center;padding:32px 16px;color:var(--text-secondary, #86868B);font-size:13px;">暂无对话中的成员</div>';
    return;
  }
  
  var totalUnread = 0;
  for (var j = 0; j < chatters.length; j++) {
    totalUnread += getUnreadMessages(chatters[j].id);
  }
  var unreadBadge = document.getElementById('chatZoneUnread');
  if (unreadBadge) {
    if (totalUnread > 0) {
      unreadBadge.textContent = totalUnread + '条新';
      unreadBadge.style.display = 'flex';
    } else {
      unreadBadge.style.display = 'none';
    }
  }
  
  var html = '';
  for (var idx = 0; idx < chatters.length; idx++) {
    var e = chatters[idx];
    var isThinking = e.status === 'thinking';
    
    var latestMsg = getLatestMessage(e.id);
    var msgTime = getMessageTimestamp(e.id);
    var timeText = formatTimeAgo(msgTime);
    if (!msgTime && isThinking) timeText = '刚刚';
    
    var unreadCount = getUnreadMessages(e.id);
    
    // 头像（48px圆角矩形）
    var avatarHtml = renderAvatar(e, 48, '12px');
    
    // 清理角色名称
    var roleName = e.role || '成员';
    roleName = roleName.replace('后庭', '后端');
    roleName = roleName.replace('科福', '后端');
    
    // Apple 风格 conv-card
    html += '<div class="conv-card" onclick="openChatDetail(\'' + escapeAttr(e.id) + '\')">';
    
    // 左侧头像
    html += '<div class="conv-avatar">';
    html += avatarHtml;
    html += '</div>';
    
    // 右侧信息
    html += '<div style="flex:1;min-width:0;">';
    
    // 名字 + 时间
    html += '<div class="conv-name-row">';
    html += '<span class="conv-name">' + escapeHtml(e.name || '?') + '</span>';
    html += '<span class="conv-time">' + escapeHtml(timeText) + '</span>';
    html += '</div>';
    
    // 角色标签
    html += '<span class="conv-role">' + escapeHtml(roleName) + '</span>';
    
    // 状态行（思考中 + 跳动圆点）
    html += '<div class="work-status-line">';
    html += '<span class="work-status-label">思考中</span>';
    html += '<span class="work-thinking-dots"><span></span><span></span><span></span></span>';
    html += '</div>';
    
    // 消息预览 + 未读badge
    if (latestMsg) {
      var preview = latestMsg.length > 24 ? latestMsg.substring(0, 24) + '...' : latestMsg;
      html += '<div class="conv-preview-row">';
      html += '<span class="conv-preview">' + escapeHtml(preview) + '</span>';
      if (unreadCount > 0) {
        html += '<span class="conv-unread">' + unreadCount + '</span>';
      }
      html += '</div>';
    }
    
    html += '</div>'; // right side
    
    html += '</div>'; // conv-card
  }
  
  chatZone.innerHTML = html;
}

// ===== 点击对话条目打开详情 =====
function openChatDetail(empId) {
  // 尝试在龙虾办公室内展开消息详情
  if (typeof openDetailPanel === 'function') {
    openDetailPanel(empId);
    return;
  }
  // 备用：跳转到主页打开该对话
  var mainUrl = './index.html';
  if (typeof getEmployeeChatUrl === 'function') {
    mainUrl = getEmployeeChatUrl(empId);
  }
  window.location.href = mainUrl + '?empId=' + encodeURIComponent(empId) + '&chat=1';
}

// ===== 滚动到对话条目并高亮 =====
function scrollChatZoneToTop(empId) {
  var chatZone = document.getElementById('chatZone');
  if (!chatZone) return;
  var items = chatZone.querySelectorAll('.chat-msg-item');
  for (var i = 0; i < items.length; i++) {
    var item = items[i];
    if (item && item.getAttribute('onclick') && item.getAttribute('onclick').indexOf(empId) > -1) {
      item.classList.add('highlight');
      item.scrollIntoView({ behavior: 'smooth', block: 'center' });
      (function(el) {
        setTimeout(function() {
          if (el) el.classList.remove('highlight');
        }, 2000);
      })(item);
      break;
    }
  }
}















// 渲染办公区 - 新版设计
function renderWorkZone() {
  var workZone = document.getElementById('workZone');
  var countEl = document.getElementById('workZoneCount');
  if (!workZone) return;
  
  // 办公区：显示正在干活的员工（不含thinking，thinking在对话区）
  // busy/working/coding/writing/reading
  var workingEmployees = [];
  for (var i = 0; i < employees.length; i++) {
    var e = employees[i];
    if (e.status === 'busy' || e.status === 'working' || e.status === 'coding' || e.status === 'writing' || e.status === 'reading') {
      workingEmployees.push(e);
    }
  }
  
  // 更新计数
  if (countEl) {
    countEl.textContent = workingEmployees.length + '人在工作';
  }
  
  if (workingEmployees.length === 0) {
    workZone.innerHTML = '<div style="text-align:center;padding:32px 16px;color:var(--text-secondary, #86868B);font-size:13px;">暂无工作中的成员</div>';
    return;
  }
  
  var html = '';
  for (var idx = 0; idx < workingEmployees.length; idx++) {
    var e = workingEmployees[idx];
    var avatarHtml = renderAvatar(e, 44, '12px');
    
    // 状态细分配置（办公区不含thinking）
    var statusConfig = {
      'using_tool': { icon: '🔧', text: '调工具', color: '#FF9500', badge: 'working' },
      'writing': { icon: '📝', text: '写内容', color: '#34C759', badge: 'working' },
      'reading': { icon: '📖', text: '读文档', color: '#AF52DE', badge: 'working' },
      'coding': { icon: '💻', text: '写代码', color: '#30D158', badge: 'working' },
      'online': { icon: '🟢', text: '在线', color: '#34C759', badge: 'online' },
      'busy': { icon: '⚡', text: '工作中', color: '#FF9500', badge: 'working' },
      'working': { icon: '⚡', text: '工作中', color: '#FF9500', badge: 'working' }
    };
    var cfg = statusConfig[e.status] || statusConfig['busy'];
    var statusData = 'working';
    var badgeClass = cfg.badge;
    var statusText = cfg.icon + ' ' + cfg.text;
    
    // 生成随机时长（用于演示）
    var minutes = Math.floor(Math.random() * 120) + 5;
    var durationText = formatDuration(minutes);
    
    // 从localStorage读取最后一条AI消息作为当前任务描述
    var taskDesc = getLastAIMessage(e.id);
    
    // 新 work-card 设计
    html += '<div class="work-card" data-status="' + statusData + '" onclick="openDetailPanel(\'' + e.id + '\')">';
    
    // 头像
    html += '<div class="work-avatar">';
    html += avatarHtml;
    html += '<div class="work-pulse"></div>';
    html += '</div>';
    
    // 中间信息
    html += '<div class="work-body">';
    // 清理角色名称
    var roleName = e.role || '成员';
    roleName = roleName.replace('后庭', '后端');
    roleName = roleName.replace('科福', '后端');
    
    html += '<div class="work-name">' + escapeHtml(e.name || '?') + '</div>';
    html += '<div class="work-role">' + escapeHtml(roleName) + '</div>';
    html += '<div class="work-task">' + escapeHtml(taskDesc) + '</div>';
    
    // 工作状态指示器（显示具体状态）
    if(e.status === 'reading' || e.status === 'coding' || e.status === 'writing' || e.status === 'using_tool'){
      var thinkDots = '<span class="work-thinking-dots"><span></span><span></span><span></span></span>';
      html += '<div class="work-status-line">';
      html += '<span class="work-status-label">' + cfg.text + '</span>';
      html += '<span class="work-status-text">处理中' + thinkDots + '</span>';
      html += '</div>';
    }
    
    html += '</div>';
    
    // 右侧状态
    html += '<div class="work-meta">';
    html += '<div class="work-badge ' + badgeClass + '">' + statusText + '</div>';
    html += '<div class="work-duration">' + durationText + '</div>';
    html += '</div>';
    
    html += '</div>';
  }
  
  workZone.innerHTML = html;
}


function viewProfile(empId) {
  var emp = employees.find(function(e) { return e.id === empId; });
  if (emp) {
    showToast('查看 ' + emp.name + ' 的资料');
    openDetailPanel(empId);
  }
}

// 开始聊天
function startChat(empId) {
  var emp = employees.find(function(e) { return e.id === empId; });
  if (emp) {
    showToast('开始与 ' + emp.name + ' 聊天');
    openDetailPanel(empId);
    // 切换到聊天标签
    var chatTab = document.querySelector('[data-tab="chat"]');
    if (chatTab) chatTab.click();
  }
}

// 检查进度
function checkProgress(empId) {
  var emp = employees.find(function(e) { return e.id === empId; });
  if (emp) {
    showToast(emp.name + ' 的工作进度：' + Math.floor(Math.random() * 40 + 50) + '%');
  }
}

// 发送任务
function sendTask(empId) {
  var emp = employees.find(function(e) { return e.id === empId; });
  if (emp) {
    showToast('为 ' + emp.name + ' 分配新任务');
  }
}


// ===== 技能系统 =====
function renderSkills(){
  var list = document.getElementById('skillsList');
  var empty = document.getElementById('skillsEmpty');
  var presets = document.getElementById('skillsPresets');
  if(!list)return;
  var skills = JSON.parse(localStorage.getItem('sb_skills')||'[]');
  if(skills.length===0){list.innerHTML='';empty.style.display='block';list.style.display='none';}
  else{empty.style.display='none';list.style.display='flex';
    var html = '';
    skills.forEach(function(s){
      html += '<div class="skill-item">';
      html += '<div class="skill-emoji">' + s.emoji + '</div>';
      html += '<div class="skill-info"><div class="skill-name">' + s.name + '</div><div>';
      var stars = '';
      for(var si = 0; si < (s.level || 3); si++){
        stars += '<span class="skill-star filled">★</span>';
      }
      html += stars + '</div></div>';
      html += '<button class="skill-delete" onclick="deleteSkill(\'' + s.id + '\')">✕</button></div>';
    });
    list.innerHTML = html;
  }
  if(presets){
    var phtml = '';
    skillPresets.forEach(function(p){
      var isUsed = skills.some(function(s){return s.name === p.name;});
      phtml += '<div class="skill-preset' + (isUsed ? ' used' : '') + '" onclick="addSkillPreset(\'' + p.name + '\',\'' + p.emoji + '\')">' + p.emoji + ' ' + p.desc + '</div>';
    });
    presets.innerHTML = phtml;
  }
}

function addSkill(){
  var name =prompt('输入技能名称:');
  if(!name)return;
  var emoji =prompt('输入表情符号:')||'⚡';
  var skills =JSON.parse(localStorage.getItem('sb_skills')||'[]');
  skills.push({id:Date.now(),name,emoji,level:3});
  localStorage.setItem('sb_skills',JSON.stringify(skills));
  renderSkills();showToast('✅ 技能已添加');
}

function addSkillPreset(name,emoji){
  var skills =JSON.parse(localStorage.getItem('sb_skills')||'[]');
  if(skills.some(function(s){return s.name===name})){showToast('该技能已添加');return;}
  skills.push({id:Date.now(),name,emoji,level:3});
  localStorage.setItem('sb_skills',JSON.stringify(skills));
  renderSkills();showToast('✅ '+name+' 已添加');
}

function deleteSkill(id){
  if(!confirm('确定删除?'))return;
  var skills =JSON.parse(localStorage.getItem('sb_skills')||'[]');
  skills=skills.filter(function(s){return s.id!=id;});
  localStorage.setItem('sb_skills',JSON.stringify(skills));
  renderSkills();showToast('🗑️ 已删除');
}

// ===== 记忆系统 =====
var currentMemoryTab = 'core';

function switchMemoryTab(tab){
  currentMemoryTab = tab;
  document.querySelectorAll('.memory-tab').forEach(function(t){t.classList.remove('active')});
  var target = document.querySelector('.memory-tab[data-mtab="' + tab + '"]');
  if(target) target.classList.add('active');
  renderMemory();
}

function renderMemoryTab(){
  return renderMemory.apply(this, arguments);
}

function renderMemory(){
  var content = document.getElementById('memoryContent');
  var empty = document.getElementById('memoryEmpty');
  if(!content) return;
  
  var empId = localStorage.getItem('sb_current_emp');
  if(!empId){
    // 备用方案：从当前选中的员工元素读 ID
    var active = document.querySelector('.emp-card.active, .emp-item.active, [data-emp].active, [data-id].active');
    if(active) empId = active.getAttribute('data-emp') || active.getAttribute('data-id');
  }
  if(!empId){
    content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">请先选择一个员工</div>';
    return;
  }
  
  content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  if(empty) empty.style.display = 'none';
  
  if(typeof apiFetch !== 'function') return;
  apiFetch('/api/memory/' + encodeURIComponent(empId))
    .then(function(r){return r.json()})
    .then(function(memories){
      // 按分类过滤
      var filtered = memories.filter(function(m){
        if(currentMemoryTab === 'core'){
          return m.key !== 'auto_extract' && m.key !== 'auto';
        } else {
          return m.key === 'auto_extract' || m.key === 'auto';
        }
      });
      
      // 更新 Tab 计数
      var coreCount = memories.filter(function(m){return m.key !== 'auto_extract' && m.key !== 'auto'}).length;
      var dailyCount = memories.filter(function(m){return m.key === 'auto_extract' || m.key === 'auto'}).length;
      var coreTab = document.querySelector('.memory-tab[data-mtab="core"]');
      var dailyTab = document.querySelector('.memory-tab[data-mtab="daily"]');
      if(coreTab) coreTab.textContent = '核心记忆 (' + coreCount + ')';
      if(dailyTab) dailyTab.textContent = '日常记录 (' + dailyCount + ')';
      
      if(filtered.length === 0){
        content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">' + (currentMemoryTab === 'core' ? '暂无核心记忆' : '暂无日常记录') + '</div>';
        return;
      }
      
      var html = '';
      filtered.forEach(function(m){
        var timeStr = m.time ? new Date(m.time).toLocaleString() : '';
        var isAuto = m.key === 'auto_extract' || m.key === 'auto';
        html += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;">';
        html += '<div class="memory-item-content" style="flex:1;">';
        html += '<div class="memory-item-text" style="font-size:14px;line-height:1.5;color:var(--text-primary);">' + (m.value||'').replace(/</g,'&lt;') + '</div>';
        html += '<div class="memory-item-date" style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">' + timeStr + '</div>';
        html += '</div>';
        html += '<div style="display:flex;gap:4px;">';
        if(isAuto){
          html += '<button onclick="promoteToCore(\'' + empId + '\',\'' + m.id + '\')" title="升级为核心记忆" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--brand-primary);display:flex;align-items:center;justify-content:center;">⭐</button>';
        }
        html += '<button onclick="deleteMemory(\'' + empId + '\',\'' + m.id + '\')" title="删除" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-tertiary);display:flex;align-items:center;justify-content:center;">🗑️</button>';
        html += '</div></div>';
      });
      content.innerHTML = html;
    })
    .catch(function(e){
      content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--red);">加载失败</div>';
      console.warn('[Memory] 加载失败:', e);
    });
}

function addMemory(){
  var empId = localStorage.getItem('sb_current_emp');
  if(!empId){showToast('请先选择员工');return;}
  
  // 在面板内显示输入框，不用 prompt
  var container = document.getElementById('addMemoryContainer');
  if(!container){
    container = document.createElement('div');
    container.id = 'addMemoryContainer';
    container.style.cssText = 'padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:12px;';
    container.innerHTML = '<input type="text" id="newMemoryInput" placeholder="输入记忆内容..." style="width:100%;padding:8px 12px;border:1px solid var(--border-color);border-radius:8px;font-size:14px;background:var(--bg-primary);color:var(--text-primary);box-sizing:border-box;margin-bottom:8px;" onkeypress="if(event.key===\'Enter\')confirmAddMemory()">';
    container.innerHTML += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
    container.innerHTML += '<button onclick="document.getElementById(\'addMemoryContainer\').remove()" style="padding:6px 14px;border-radius:8px;border:1px solid var(--border-color);background:var(--bg-secondary);color:var(--text-primary);font-size:13px;cursor:pointer;">取消</button>';
    container.innerHTML += '<button onclick="confirmAddMemory()" style="padding:6px 14px;border-radius:8px;border:none;background:var(--brand-gradient);color:white;font-size:13px;cursor:pointer;">确认</button>';
    container.innerHTML += '</div>';
    var content = document.getElementById('memoryContent');
    if(content) content.parentNode.insertBefore(container, content);
    document.getElementById('newMemoryInput').focus();
  }
}

function confirmAddMemory(){
  var input = document.getElementById('newMemoryInput');
  if(!input) return;
  var text = input.value.trim();
  if(!text) return;
  var empId = localStorage.getItem('sb_current_emp');
  if(!empId) return;
  
  apiFetch('/api/memory/' + encodeURIComponent(empId), {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: 'manual', value: text, source: '手动添加'})
  }).then(function(r){return r.json()})
    .then(function(){
      showToast('✅ 记忆已添加');
      var container = document.getElementById('addMemoryContainer');
      if(container) container.remove();
      renderMemory();
    })
    .catch(function(e){
      showToast('❌ 添加失败');
      console.warn('[Memory] 添加失败:', e);
    });
}

function deleteMemory(empId, memoryId){
  if(!confirm('确定删除这条记忆？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {
    method: 'DELETE'
  }).then(function(r){return r.json()})
    .then(function(){
      showToast('🗑️ 已删除');
      renderMemory();
    })
    .catch(function(e){
      showToast('❌ 删除失败');
      console.warn('[Memory] 删除失败:', e);
    });
}

function promoteToCore(empId, memoryId){
  if(!confirm('确定升级为核心记忆？')) return;
  // 先获取记忆内容，再以 manual key 重新保存
  apiFetch('/api/memory/' + encodeURIComponent(empId))
    .then(function(r){return r.json()})
    .then(function(memories){
      var mem = memories.find(function(m){return m.id === memoryId});
      if(!mem){showToast('❌ 记忆不存在');return;}
      // 保存为核心记忆
      apiFetch('/api/memory/' + encodeURIComponent(empId), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key: 'manual', value: mem.value, source: '从日常记录升级'})
      }).then(function(r2){return r2.json()})
        .then(function(){
          // 删除原日常记录
          return apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {method: 'DELETE'});
        }).then(function(){
          showToast('⭐ 已升级为核心记忆');
          renderMemory();
        });
    })
    .catch(function(e){
      showToast('❌ 升级失败');
      console.warn('[Memory] 升级失败:', e);
    });
}

// ===== 文档库 =====
function renderDocs(filter=''){
  var grid =document.getElementById('docsGrid');
  var empty =document.getElementById('docsEmpty');
  if(!grid)return;
  var docs = docsData.filter(function(d){
    return !filter || d.name.toLowerCase().indexOf(filter.toLowerCase()) > -1 || d.tags.some(function(t){return t.indexOf(filter) > -1});
  });
  if(docs.length===0){grid.innerHTML='';empty.style.display='block';}
  else{empty.style.display='none';
    var html = '';
    docs.forEach(function(d){
      var tagsHtml = '';
      d.tags.forEach(function(t){
        tagsHtml += '<span class="doc-tag">' + t + '</span>';
      });
      html += '<div class="doc-card" onclick="showToast(\'打开: ' + d.name + '\')">' +
        '<div class="doc-card-icon">' + d.icon + '</div>' +
        '<div class="doc-name">' + d.name + '</div>' +
        '<div class="doc-meta"><span>📁 ' + d.type + '</span><span>🕐 ' + d.date + '</span></div>' +
        '<div class="doc-tags">' + tagsHtml + '</div></div>';
    });
    grid.innerHTML = html;
  }
}

function filterDocs(q){renderDocs(q);}

function addDoc(){showToast('📝 新建文档功能开发中...');}

// ===== AI 模式选择 =====
function selectAIMode(el){
  document.querySelectorAll('.ai-mode-option').forEach(function(o){o.classList.remove('active')});
  el.classList.add('active');
  var mode = el.dataset.mode;
  document.getElementById('openaiConfigPanel').style.display = mode==='openai'?'block':'none';
  if(mode!=='openai') showToast('已切换到 '+el.querySelector('.mode-name').textContent);
}

// ===== 网关控制 =====
function restartGateway(){
  showToast('🔄 正在重启网关...', 'loading');
  setTimeout(function(){ showToast('✅ 网关重启成功', 'success'); },1500);
}

// ===== 员工详情 =====
function showEmpDetail(id){
  var emp = employees.find(function(e){ return e.id===id; });
  if(emp) showToast('👤 ' + emp.name + ' · ' + emp.role);
}

// ===== Toast =====
function showToast(msg, type){
  type = type || 'default';
  var existing = document.querySelector('.toast');
  if(existing){ existing.remove(); }
  var toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = msg;
  document.body.appendChild(toast);
  requestAnimationFrame(function(){ toast.classList.add('show'); });
  setTimeout(function(){ toast.classList.remove('show'); setTimeout(function(){ toast.remove(); }, 350); }, 2500);
}

// ===== 全局搜索 =====
document.addEventListener('keydown', function(e){
  if((e.metaKey || e.ctrlKey) && e.key === 'k'){
    e.preventDefault();
    document.getElementById('searchModal').classList.add('show');
    document.getElementById('searchInput').focus();
  }
  if(e.key === 'Escape'){
    document.getElementById('searchModal').classList.remove('show');
    document.getElementById('searchInput').value = '';
  }
});

function performSearch(q){
  var results = document.getElementById('searchResults');
  if(!q.trim()){ results.innerHTML = '<div style="padding:32px;text-align:center;color:var(--text-secondary)"><div style="font-size:36px;margin-bottom:12px">🔎</div><p>输入关键词开始搜索</p></div>'; return; }

  // 搜索员工
  var empMatches = employees.filter(function(e){ return e.name.toLowerCase().indexOf(q.toLowerCase()) !== -1; });
  // 搜索技能
  var skills = JSON.parse(localStorage.getItem('sb_skills') || '[]');
  var skillMatches = skills.filter(function(s){ return s.name.toLowerCase().indexOf(q.toLowerCase()) !== -1; });
  // 搜索文档
  var docMatches = docsData.filter(function(d){ return d.name.toLowerCase().indexOf(q.toLowerCase()) !== -1; });

  var html = '';
  if(empMatches.length) html += '<div style="padding:12px 20px;font-size:12px;color:var(--text-secondary);font-weight:600">团队成员</div>';
  empMatches.forEach(function(e){
    html += '<div class="search-result-item" onclick="switchTab(\'employees\');closeSearch()">' +
      '<div class="activity-avatar" style="background:linear-gradient(135deg,' + e.gradient[0] + ' 0%,' + e.gradient[1] + ' 100%);width:32px;height:32px;font-size:13px">' + e.initial + '</div>' +
      '<div><div style="font-size:14px;font-weight:600">' + e.name + '</div><div style="font-size:12px;color:var(--text-secondary)">' + e.role + '</div></div></div>';
  });
  if(skillMatches.length) html += '<div style="padding:12px 20px;font-size:12px;color:var(--text-secondary);font-weight:600">技能</div>';
  skillMatches.forEach(function(s){
    html += '<div class="search-result-item" onclick="switchTab(\'skills\');closeSearch()">' +
      '<span style="font-size:20px">' + s.emoji + '</span><div><div style="font-size:14px;font-weight:600">' + s.name + '</div></div></div>';
  });
  if(docMatches.length) html += '<div style="padding:12px 20px;font-size:12px;color:var(--text-secondary);font-weight:600">文档</div>';
  docMatches.forEach(function(d){
    html += '<div class="search-result-item" onclick="switchTab(\'docs\');closeSearch()">' +
      '<span style="font-size:20px">' + d.icon + '</span><div><div style="font-size:14px;font-weight:600">' + d.name + '</div><div style="font-size:12px;color:var(--text-secondary)">' + d.date + '</div></div></div>';
  });
  if(!html) html = '<div style="padding:32px;text-align:center;color:var(--text-secondary)"><div style="font-size:36px;margin-bottom:12px">😕</div><p>没有找到 "' + q + '" 相关结果</p></div>';

  results.innerHTML = html;
}

function closeSearch(){
  document.getElementById('searchModal').classList.remove('show');
  document.getElementById('searchInput').value='';
}

// ===== 员工详情面板 =====
var currentDetailEmp = null;
var currentAgentId = null;

async function openDetailPanel(empId){
  var emp = null;
  for(var i=0;i<employees.length;i++){
    if(employees[i].id===empId){ emp=employees[i]; break; }
  }
  if(!emp) return;
  currentDetailEmp = emp;
  currentAgentId = empId;
  localStorage.setItem('sb_current_emp', empId);
  
  document.getElementById('detailAvatar').textContent = emp.name[0];
  document.getElementById('detailName').textContent = emp.name;
  document.getElementById('detailRole').textContent = emp.role;
  
  // Show loading state
  var gridHtml = '<div style="padding:20px;text-align:center;color:var(--gray-4)">加载中...</div>';
  document.getElementById('detailInfoGrid').innerHTML = gridHtml;
  
  // Load real data from backend API first
  try {
    var resp = await authFetch('/api/agents/' + encodeURIComponent(empId));
    if (resp && resp.ok) {
      var serverEmp = await resp.json();
      // Merge server data with local
      if (serverEmp) {
        emp.name = serverEmp.name || emp.name;
        emp.role = serverEmp.role || emp.role;
        emp.bg = serverEmp.bg || emp.bg;
        emp.avatar = serverEmp.avatar !== undefined ? serverEmp.avatar : emp.avatar;
        emp.status = serverEmp.status || emp.status;
        emp.model = serverEmp.model || emp.model;
        emp.subCategory = serverEmp.subCategory || emp.subCategory;
        // Update display
        document.getElementById('detailAvatar').textContent = emp.name[0];
        document.getElementById('detailName').textContent = emp.name;
        document.getElementById('detailRole').textContent = emp.role;
      }
    }
  } catch(e) {
    console.log('[Office] 从后端加载员工详情失败:', e.message);
  }
  
  // Load identity data
  var identity = await getAgentIdentity(empId);
  if (identity) {
    var statusMap = {online:'在线',busy:'工作中',offline:'离线',idle:'空闲',thinking:'思考中',working:'工作中'};
    var statusColor = {online:'var(--green)',busy:'var(--orange)',offline:'var(--gray-4)',idle:'var(--gray-4)',thinking:'var(--blue)',working:'var(--orange)'};
    
    gridHtml = '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">模型</div>';
    gridHtml += '<div class="info-value">'+identity.model+'</div>';
    gridHtml += '</div>';
    
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">Agent ID</div>';
    gridHtml += '<div class="info-value">#'+identity.agentId+'</div>';
    gridHtml += '</div>';
    
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">创建时间</div>';
    gridHtml += '<div class="info-value">'+identity.createdAt+'</div>';
    gridHtml += '</div>';
    
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">状态</div>';
    gridHtml += '<div class="info-value" style="color:'+(statusColor[emp.status]||'var(--gray-4)')+'">'+statusMap[emp.status]+'</div>';
    gridHtml += '<div class="info-sub">'+identity.lastActive+'</div>';
    gridHtml += '</div>';
    
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">上下文</div>';
    gridHtml += '<div class="info-value">'+identity.sessions+'条</div>';
    gridHtml += '<div class="info-sub">会话</div>';
    gridHtml += '</div>';
    
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">标签</div>';
    gridHtml += '<div class="info-value">'+(emp.role?'1':'0')+'个</div>';
    gridHtml += '</div>';
    
    // 分组选择
    gridHtml += '<div class="detail-info-item">';
    gridHtml += '<div class="info-label">分组</div>';
    gridHtml += '<select id="detailGroupSelect" onchange="moveEmployeeToGroup(this.value)" style="padding:6px 10px;border-radius:6px;border:1px solid var(--border-color);font-size:13px;background:var(--bg-secondary);color:var(--text-primary);cursor:pointer">';
    gridHtml += '<option value="">未分组</option>';
    // 从 employees 中提取所有分组
    var groups = {};
    for(var gi=0; gi<employees.length; gi++){
      var g = employees[gi].group;
      if(g && !groups[g]){
        groups[g] = true;
        gridHtml += '<option value="'+g+'"'+(emp.group===g?' selected':'')+'>'+g+'</option>';
      }
    }
    gridHtml += '</select>';
    gridHtml += '</div>';
    
    document.getElementById('detailInfoGrid').innerHTML = gridHtml;
  }
  
  // Docs section
  var docsHtml = '<div class="docs-layout">';
  docsHtml += '<div class="docs-nav">';
  docsHtml += '<div class="docs-nav-item active" data-doc="IDENTITY.md" onclick="switchDocTab(this)">IDENTITY.md</div>';
  docsHtml += '<div class="docs-nav-item" data-doc="USER.md" onclick="switchDocTab(this)">USER.md</div>';
  docsHtml += '<div class="docs-nav-item" data-doc="SOUL.md" onclick="switchDocTab(this)">SOUL.md</div>';
  docsHtml += '<div class="docs-nav-item" data-doc="TOOLS.md" onclick="switchDocTab(this)">TOOLS.md</div>';
  docsHtml += '</div>';
  docsHtml += '<div class="docs-content">';
  docsHtml += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><h4 style="margin:0">IDENTITY.md</h4><button onclick="saveCurrentDoc()" style="padding:4px 12px;border-radius:6px;border:none;background:var(--blue);color:white;font-size:12px;cursor:pointer">💾 保存</button></div>';
  docsHtml += '<textarea id="docEditor" style="width:100%;height:calc(100% - 40px);border:1px solid var(--border-color);border-radius:8px;padding:12px;font-size:12px;font-family:monospace;resize:none;background:var(--gray-1);color:var(--gray-6);line-height:1.6;white-space:pre-wrap" placeholder="编辑文档内容...">加载中...</textarea>';
  docsHtml += '</div>';
  docsHtml += '</div>';
  
  document.getElementById('detailDocs').innerHTML = docsHtml;
  document.getElementById('detailOverlay').classList.add('active');
  document.getElementById('detailPanel').classList.add('active');
  
  // 初始化 Dreaming 状态
  initDreamingStatus();
  
  // 异步加载第一个文档内容
  var firstDoc = await loadDocContent('IDENTITY.md');
  var editor = document.getElementById('docEditor');
  if(editor) {
    editor.value = firstDoc;
    docCache[currentAgentId + '_IDENTITY.md'] = firstDoc;
  }
  currentDocName = 'IDENTITY.md';
}

async function switchDocTab(el){
  var docName = el.getAttribute('data-doc');

  // 缓存当前编辑器内容
  var editor = document.getElementById('docEditor');
  if(editor && currentDocName && currentAgentId){
    docCache[currentAgentId + '_' + currentDocName] = editor.value;
  }

  // 更新 tab 高亮
  document.querySelectorAll('.docs-nav-item').forEach(function(item){item.classList.remove('active');});
  el.classList.add('active');
  currentDocName = docName;

  // 重建编辑器
  var contentEl = document.querySelector('.docs-content');
  if(contentEl){
    contentEl.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><h4 style="margin:0">' + docName + '</h4><button onclick="saveCurrentDoc()" style="padding:4px 12px;border-radius:6px;border:none;background:var(--blue);color:white;font-size:12px;cursor:pointer">💾 保存</button></div><textarea id="docEditor" style="width:100%;height:calc(100% - 40px);border:1px solid var(--border-color);border-radius:8px;padding:12px;font-size:12px;font-family:monospace;resize:none;background:var(--gray-1);color:var(--gray-6);line-height:1.6;white-space:pre-wrap" placeholder="编辑文档内容..."></textarea>';

    var ed = document.getElementById('docEditor');

    // 先显示缓存内容，避免闪烁
    var cacheKey = currentAgentId + '_' + docName;
    if(docCache[cacheKey] !== undefined){
      ed.value = docCache[cacheKey];
    } else {
      ed.value = '加载中...';
      // 后台异步刷新
      var docContent = await loadDocContent(docName);
      if(docContent){
        ed.value = docContent;
        docCache[cacheKey] = docContent;
      } else {
        ed.value = '';
      }
    }
  }
}

async function saveCurrentDoc(){
  var editor = document.getElementById('docEditor');
  if(!editor) return;
  // Find current active doc tab
  var activeTab = document.querySelector('.docs-nav-item.active');
  var docName = activeTab ? activeTab.getAttribute('data-doc') : 'IDENTITY.md';
  await saveDocContent(docName, editor.value);
}

function openChannelConfig(){
  loadChannels();
  document.getElementById('channelOverlay').classList.add('active');
  document.getElementById('channelPanel').classList.add('active');
}
function closeChannelConfig(){
  document.getElementById('channelOverlay').classList.remove('active');
  document.getElementById('channelPanel').classList.remove('active');
}
function openModelConfig(){
  loadModels();
  document.getElementById('modelOverlay').classList.add('active');
  document.getElementById('modelPanel').classList.add('active');
}
function closeModelConfig(){
  document.getElementById('modelOverlay').classList.remove('active');
  document.getElementById('modelPanel').classList.remove('active');
}
function switchChannelTab(el){
  var channel = el.getAttribute('data-channel');
  document.querySelectorAll('.channel-tab').forEach(function(t){t.classList.remove('active');});
  el.classList.add('active');
  
  // 隐藏所有表单
  document.getElementById('dingtalkForm').style.display = 'none';
  document.getElementById('feishuForm').style.display = 'none';
  document.getElementById('agentlinkForm').style.display = 'none';
  document.getElementById('telegramForm').style.display = 'none';
  
  // 显示当前渠道表单
  var formId = channel + 'Form';
  var formEl = document.getElementById(formId);
  if(formEl) formEl.style.display = 'block';
  
  // 如果是飞书，加载配置
  if(channel === 'feishu'){
    loadFeishuConfig();
  }
}

// ===== 飞书渠道配置 =====

// 加载飞书配置
async function loadFeishuConfig(){
  try{
    var resp = await authFetch('/api/openclaw/channels/feishu/status');
    if(resp && resp.ok){
      var data = await resp.json();
      var appIdEl = document.getElementById('feishuAppId');
      var appSecretEl = document.getElementById('feishuAppSecret');
      var botNameEl = document.getElementById('feishuBotName');
      var dmPolicyEl = document.getElementById('feishuDmPolicy');
      var statusEl = document.getElementById('feishuStatus');
      var enableEl = document.getElementById('enableFeishu');
      
      if(appIdEl) appIdEl.value = data.appId || '';
      if(appSecretEl && data.appSecret){
        // 遮盖值不覆盖输入框，保留空值让用户知道需要重新输入
        if(data.appSecret.includes('*')){
          appSecretEl.value = '';
          appSecretEl.placeholder = '已保存（遮盖），如需修改请重新输入';
        } else {
          appSecretEl.value = data.appSecret;
        }
      }
      if(botNameEl) botNameEl.value = data.botName || '全可AI助手';
      if(dmPolicyEl) dmPolicyEl.value = data.dmPolicy || 'pairing';
      if(enableEl) enableEl.checked = data.enabled || false;
      
      // 更新状态显示
      if(statusEl){
        if(data.connected){
          statusEl.innerHTML = '🟢 已连接';
          statusEl.style.color = 'var(--green)';
        } else if(data.appId){
          statusEl.innerHTML = '🔴 未连接';
          statusEl.style.color = 'var(--red)';
        } else {
          statusEl.innerHTML = '⚪ 未配置';
          statusEl.style.color = 'var(--gray-4)';
        }
      }
      
      // 显示配对码区域（如果已启用但未配对）
      var pairingArea = document.getElementById('feishuPairingArea');
      if(pairingArea && data.enabled && !data.paired){
        pairingArea.style.display = 'block';
      } else if(pairingArea){
        pairingArea.style.display = 'none';
      }
    }
  } catch(err){
    console.log('[Feishu] 加载配置失败:', err.message);
  }
}

// 保存飞书配置
async function saveChannelConfig(){
  var activeTab = document.querySelector('.channel-tab.active');
  var channel = activeTab ? activeTab.getAttribute('data-channel') : 'dingtalk';
  
  if(channel === 'feishu'){
    var appId = document.getElementById('feishuAppId').value.trim();
    var appSecret = document.getElementById('feishuAppSecret').value.trim();
    var botName = document.getElementById('feishuBotName').value.trim() || '全可AI助手';
    var dmPolicy = document.getElementById('feishuDmPolicy').value;
    var enabled = document.getElementById('enableFeishu').checked;
    
    if(!appId){
      showToast('请填写 App ID');
      return;
    }
    
    showToast('正在保存飞书配置...');
    
    // 构建 body，appSecret 为空时不发送（后端保留原值）
    var body = {
      appId: appId,
      botName: botName,
      dmPolicy: dmPolicy,
      enabled: enabled
    };
    if(appSecret) body.appSecret = appSecret;
    
    try{
      var resp = await authFetch('/api/openclaw/channels/feishu', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(body)
      });
      
      if(resp && resp.ok){
        showToast('✅ 飞书配置已保存');
        loadFeishuConfig();
        closeChannelConfig();
      } else {
        var errData = await resp.json();
        showToast('❌ 保存失败: ' + (errData.error || '服务器错误'));
      }
    } catch(err){
      console.error('[Feishu] 保存失败:', err);
      showToast('❌ 保存失败: ' + err.message);
    }
  } else {
    showToast('✅ ' + channel + ' 配置已保存');
  }
}

// 切换飞书 App Secret 可见性
function toggleFeishuSecretVisibility(){
  var el = document.getElementById('feishuAppSecret');
  if(el){
    el.type = el.type === 'password' ? 'text' : 'password';
  }
}

// 批准飞书配对码
async function approveFeishuPairing(){
  var code = document.getElementById('feishuPairingCode').value.trim();
  if(!code){
    showToast('请输入配对码');
    return;
  }
  
  showToast('正在批准配对...');
  
  try{
    var resp = await authFetch('/api/openclaw/pairing/approve', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        channel: 'feishu',
        code: code
      })
    });
    
    if(resp && resp.ok){
      showToast('✅ 配对码已批准');
      document.getElementById('feishuPairingCode').value = '';
      loadFeishuConfig();
    } else {
      var errData = await resp.json();
      showToast('❌ 批准失败: ' + (errData.error || '服务器错误'));
    }
  } catch(err){
    console.error('[Feishu] 配对批准失败:', err);
    showToast('❌ 批准失败: ' + err.message);
  }
}

// 重启 Gateway
async function restartGateway(){
  showToast('🔄 正在重启 Gateway...');
  
  try{
    var resp = await authFetch('/api/openclaw/gateway/restart', {method: 'POST'});
    if(resp && resp.ok){
      showToast('✅ Gateway 重启成功');
    } else {
      showToast('❌ Gateway 重启失败');
    }
  } catch(err){
    console.error('[Gateway] 重启失败:', err);
    showToast('❌ Gateway 重启失败: ' + err.message);
  }
}

function closeDetailPanel(){
  document.getElementById('detailPanel').classList.remove('active');
  document.getElementById('detailOverlay').classList.remove('active');
  document.body.style.overflow = '';
  currentDetailEmp = null;
  currentAgentId = null;
}

// ===== 员工改名功能 =====
var editingName = false;
var originalName = '';

function startEditName(){
  if(editingName || !currentDetailEmp) return;
  editingName = true;
  originalName = currentDetailEmp.name || '';
  
  var nameEl = document.getElementById('detailName');
  var btnEl = document.getElementById('editNameBtn');
  if(!nameEl) return;
  
  // 替换为输入框
  nameEl.innerHTML = '<input type="text" id="nameEditInput" value="' + originalName + '" style="font-size:inherit;font-weight:inherit;font-family:inherit;border:2px solid var(--blue);border-radius:8px;padding:4px 12px;background:var(--bg-secondary);color:var(--text-primary);outline:none;text-align:center;max-width:200px" onblur="finishEditName()" onkeydown="handleNameEditKey(event)">';
  
  var input = document.getElementById('nameEditInput');
  if(input){
    input.focus();
    input.select();
  }
  if(btnEl) btnEl.style.display = 'none';
}

function handleNameEditKey(e){
  if(e.key === 'Enter'){
    finishEditName();
  } else if(e.key === 'Escape'){
    cancelEditName();
  }
}

function finishEditName(){
  if(!editingName) return;
  editingName = false;
  
  var input = document.getElementById('nameEditInput');
  var newName = input ? input.value.trim() : originalName;
  
  if(!newName){
    newName = originalName;
  }
  
  // 更新显示
  var nameEl = document.getElementById('detailName');
  var btnEl = document.getElementById('editNameBtn');
  if(nameEl) nameEl.textContent = newName;
  if(btnEl) btnEl.style.display = '';
  
  // 如果名字变了，保存
  if(newName !== originalName && currentDetailEmp){
    var empId = currentDetailEmp.id;
    var emp = employees.find(function(e){ return e.id === empId; });
    if(emp){
      emp.name = newName;
      // 更新头像文字
      var avatarEl = document.getElementById('detailAvatar');
      if(avatarEl) avatarEl.textContent = newName[0];
      // 同步到服务器
      saveEmployeeName(empId, newName);
    }
  }
}

function cancelEditName(){
  if(!editingName) return;
  editingName = false;
  
  var nameEl = document.getElementById('detailName');
  var btnEl = document.getElementById('editNameBtn');
  if(nameEl) nameEl.textContent = originalName;
  if(btnEl) btnEl.style.display = '';
}

async function saveEmployeeName(empId, newName){
  try{
    // 更新服务器
    var resp = await authFetch('/api/agents/' + encodeURIComponent(empId), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: newName})
    });
    
    if(resp && resp.ok){
      showToast('✅ 名字已保存');
      // 刷新列表
      renderEmployees();
      renderChatZone();
      renderWorkZone();
      renderLoungeZone();
    } else {
      showToast('❌ 保存失败');
    }
  } catch(err){
    console.error('[Office] 保存名字失败:', err);
    showToast('❌ 保存失败: ' + err.message);
  }
}

// ===== 移动员工到分组 =====
async function moveEmployeeToGroup(groupName){
  if(!currentDetailEmp) return;
  
  var empId = currentDetailEmp.id;
  var emp = employees.find(function(e){ return e.id === empId; });
  if(!emp) return;
  
  var oldGroup = emp.group || '';
  if(oldGroup === groupName) return;
  
  emp.group = groupName;
  
  try{
    var resp = await authFetch('/api/agents/' + encodeURIComponent(empId), {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({group: groupName})
    });
    
    if(resp && resp.ok){
      showToast(groupName ? '✅ 已移动到分组: ' + groupName : '✅ 已移出分组');
      renderEmployees();
    } else {
      showToast('❌ 移动失败');
      emp.group = oldGroup;
    }
  } catch(err){
    console.error('[Office] 移动分组失败:', err);
    showToast('❌ 移动失败: ' + err.message);
    emp.group = oldGroup;
  }
}

// ===== Dreaming 模式功能 =====

// 当前 Dreaming 状态
var currentDreamingState = false;
var currentDreamingPhase = 'idle';

// 初始化 Dreaming 状态
async function initDreamingStatus() {
  var toggle = document.getElementById('dreamingToggle');
  var stateEl = document.getElementById('dreamingState');
  var phaseEl = document.getElementById('dreamingPhase');
  var controlEl = document.getElementById('dreamingControl');
  
  if (!toggle || !currentAgentId) return;
  
  try {
    // 尝试从后端获取状态
    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      var status = await openclaw.getDreamingStatus(currentAgentId);
      currentDreamingState = status.enabled || false;
      currentDreamingPhase = status.phase || 'idle';
    } else {
      // Mock 模式：读取 localStorage
      var stored = localStorage.getItem('dreaming_' + currentAgentId);
      if (stored) {
        var data = JSON.parse(stored);
        currentDreamingState = data.enabled || false;
        currentDreamingPhase = data.phase || 'idle';
      }
    }
    
    // 更新 UI
    toggle.checked = currentDreamingState;
    updateDreamingUI();
    
  } catch (err) {
    console.log('[Dreaming] 获取状态失败，使用默认:', err.message);
    currentDreamingState = false;
    currentDreamingPhase = 'idle';
    updateDreamingUI();
  }
}

// 更新 Dreaming UI 状态
function updateDreamingUI() {
  var stateEl = document.getElementById('dreamingState');
  var phaseEl = document.getElementById('dreamingPhase');
  var controlEl = document.getElementById('dreamingControl');
  var phaseTags = document.querySelectorAll('.phase-tag');
  
  if (currentDreamingState) {
    stateEl.textContent = '已激活';
    controlEl.classList.add('active');
    
    // 根据阶段显示不同的标签
    var phaseTexts = {
      'idle': '🌙 待机',
      'dreaming': '💭 做梦中',
      'awake': '☀️ 已唤醒'
    };
    phaseEl.textContent = phaseTexts[currentDreamingPhase] || '💭 做梦中';
    phaseEl.style.display = 'inline-block';
  } else {
    stateEl.textContent = '未激活';
    controlEl.classList.remove('active');
    phaseEl.style.display = 'none';
  }
  
  // 更新阶段标签激活状态
  phaseTags.forEach(function(tag) {
    if (tag.getAttribute('data-phase') === currentDreamingPhase && currentDreamingState) {
      tag.classList.add('active');
    } else {
      tag.classList.remove('active');
    }
  });
}

// 切换 Dreaming 模式
async function toggleDreamingMode(enabled) {
  if (!currentAgentId) {
    showToast('请先选择一个 Agent');
    var toggle = document.getElementById('dreamingToggle');
    if (toggle) toggle.checked = false;
    return;
  }
  
  currentDreamingState = enabled;
  var stateEl = document.getElementById('dreamingState');
  
  if (enabled) {
    stateEl.textContent = '正在启动...';
    
    try {
      if (typeof openclaw !== 'undefined' && openclaw.connected) {
        // 调用后端 API
        var result = await openclaw.toggleDreaming(currentAgentId, true);
        currentDreamingPhase = result.phase || 'dreaming';
        showToast('🎭 Dreaming 模式已启动 - ' + currentDreamingPhase);
      } else {
        // Mock 模式
        currentDreamingPhase = 'dreaming';
        localStorage.setItem('dreaming_' + currentAgentId, JSON.stringify({
          enabled: true,
          phase: 'dreaming',
          updatedAt: new Date().toISOString()
        }));
        showToast('🎭 Dreaming 模式已启动（Mock）');
      }
    } catch (err) {
      console.error('[Dreaming] 启动失败:', err);
      currentDreamingState = false;
      var toggle = document.getElementById('dreamingToggle');
      if (toggle) toggle.checked = false;
      showToast('Dreaming 启动失败: ' + err.message);
    }
  } else {
    stateEl.textContent = '正在关闭...';
    
    try {
      if (typeof openclaw !== 'undefined' && openclaw.connected) {
        var result = await openclaw.toggleDreaming(currentAgentId, false);
        currentDreamingPhase = 'idle';
        showToast('🌙 Dreaming 模式已关闭');
      } else {
        // Mock 模式
        localStorage.setItem('dreaming_' + currentAgentId, JSON.stringify({
          enabled: false,
          phase: 'idle',
          updatedAt: new Date().toISOString()
        }));
        showToast('🌙 Dreaming 模式已关闭（Mock）');
      }
    } catch (err) {
      console.error('[Dreaming] 关闭失败:', err);
      showToast('Dreaming 关闭失败: ' + err.message);
    }
  }
  
  updateDreamingUI();
}

// 选择 Dreaming 阶段
function selectDreamingPhase(phase) {
  if (!currentDreamingState) {
    showToast('请先开启 Dreaming 模式');
    return;
  }
  
  currentDreamingPhase = phase;
  
  try {
    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      // 调用后端 API 切换阶段
      openclaw.send('dreaming.setPhase', {
        agentId: currentAgentId,
        phase: phase
      }).then(function(result) {
        showToast('阶段已切换: ' + phase);
      }).catch(function(err) {
        console.error('[Dreaming] 切换阶段失败:', err);
      });
    } else {
      // Mock 模式
      localStorage.setItem('dreaming_' + currentAgentId, JSON.stringify({
        enabled: true,
        phase: phase,
        updatedAt: new Date().toISOString()
      }));
      showToast('阶段已切换（Mock）: ' + phase);
    }
  } catch (err) {
    console.error('[Dreaming] 切换阶段失败:', err);
  }
  
  updateDreamingUI();
}

function detailStartChat(){
  if(currentDetailEmp){
    closeDetailPanel();
    goToMainChat(currentDetailEmp.id);
  }
}

function detailViewProjects(){
  if(currentDetailEmp){
    closeDetailPanel();
    switchTab('projects');
  }
}

// ===== Lounge Drawer 休闲区抽屉 =====
// 切换休闲区抽屉开关
function toggleLounge(){
  var drawer = document.getElementById('loungeDrawer');
  var btn = document.getElementById('loungeNavBtn');
  if(!drawer) return;
  drawer.classList.toggle('open');
  if(btn){
    btn.classList.toggle('active');
  }
  // 打开时渲染内容
  if(drawer.classList.contains('open')){
    renderLounge();
  }
}

// 渲染休闲区抽屉内容
function renderLounge(){
  var content = document.getElementById('loungeDrawerContent');
  if(!content) return;
  
  // 统计各状态员工数
  var total = employees.length;
  var online = 0;
  var busy = 0;
  var thinking = 0;
  var idle = 0;
  var offline = 0;
  
  for(var i=0; i<employees.length; i++){
    var emp = employees[i];
    var status = emp.status || 'offline';
    if(status === 'online') online++;
    else if(status === 'busy' || status === 'working') busy++;
    else if(status === 'thinking') thinking++;
    else if(status === 'idle') idle++;
    else offline++;
  }
  
  var html = '';
  
  // 统计栏
  html += '<div class="lounge-stat-bar">';
  html += '<span>🟢 在线 <strong style="color:var(--green)">' + online + '</strong></span>';
  html += '<span>🟠 工作中 <strong style="color:var(--orange)">' + busy + '</strong></span>';
  html += '<span>🟡 思考中 <strong style="color:var(--yellow)">' + thinking + '</strong></span>';
  html += '<span>⚪ 离线 <strong style="color:var(--gray-4)">' + offline + '</strong></span>';
  html += '</div>';
  
  // 员工列表 - 显示所有员工
  if(employees.length === 0){
    html += '<div class="lounge-drawer-empty">';
    html += '<div class="lounge-drawer-empty-icon">🦞</div>';
    html += '<div class="lounge-drawer-empty-text">暂无员工</div>';
    html += '</div>';
  } else {
    html += '<div class="lounge-employee-list">';
    
    for(var j=0; j<employees.length; j++){
      var e = employees[j];
      var bg = (e.gradient && e.gradient[0]) || e.bg || '#FFD60A';
      var statusClass = e.status || 'offline';
      var statusText = statusClass === 'online' ? '在线' : statusClass === 'busy' || statusClass === 'working' ? '工作中' : statusClass === 'thinking' ? '思考中' : statusClass === 'idle' ? '空闲' : '离线';
      
      // 获取当前任务（如果有）
      var currentTask = '';
      if(e.currentTask){
        currentTask = e.currentTask;
      } else if(e.task){
        currentTask = e.task;
      }
      
      // 获取今日对话数
      var dialogueCount = e.dialogueCount || e.sessions || Math.floor(Math.random() * 15);
      
      html += '<div class="lounge-employee-item" onclick="openDetailPanel(\'' + e.id + '\')">';
      html += '<div class="lounge-avatar-wrap">';
      html += '<div class="lounge-avatar-badge ' + statusClass + '"></div>';
      html += '</div>';
      html += '<div class="lounge-employee-info">';
      html += '<div class="lounge-employee-name">' + escapeHtml(e.name || '') + ' <span class="lounge-role-tag">' + escapeHtml(e.role || '员工') + '</span></div>';
      if(currentTask){
        html += '<div class="lounge-employee-task">📋 ' + escapeHtml(currentTask) + '</div>';
      }
      html += '<div class="lounge-employee-meta">最后活跃: ' + escapeHtml(e.lastActive || '刚刚') + ' · 今日对话: ' + dialogueCount + '条</div>';
      html += '</div>';
      
      // Hover 预览卡
      html += '<div class="lounge-preview-card">';
      html += '<div class="lounge-preview-header">';
      html += '<div class="lounge-preview-avatar" style="background:' + bg + '">' + (e.name ? e.name[0] : '?') + '</div>';
      html += '<div>';
      html += '<div class="lounge-preview-name">' + escapeHtml(e.name || '') + '</div>';
      html += '<div class="lounge-preview-role">' + escapeHtml(e.role || '员工') + '</div>';
      html += '</div>';
      html += '</div>';
      if(currentTask){
        html += '<div class="lounge-preview-task">📋 当前任务: <strong>' + escapeHtml(currentTask) + '</strong></div>';
      }
      html += '<div class="lounge-preview-dialogue">💬 今日对话: ' + dialogueCount + '条</div>';
      html += '</div>';
      
      html += '</div>';
    }
    
    html += '</div>';
  }
  
  content.innerHTML = html;
}

// HTML 转义函数
function escapeHtml(str){
  if(!str) return '';
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

// ===== Lounge Zone 渲染 =====
function renderLoungeZone(){
  var loungeZone = document.getElementById('loungeZone');
  if(!loungeZone) return;
  if(employees.length === 0) {
    loungeZone.innerHTML = '<div style="padding:20px;text-align:center;color:var(--gray-4)">暂无员工</div>';
    return;
  }

  // 横向滚动头像列表 — 只显示不在对话区也不在办公区的员工
  var html = '';
  var onlineCount = 0;

  for (var i = 0; i < employees.length; i++) {
    var e = employees[i];
    // 休闲区：只显示不在对话区也不在办公区的员工
    // 排除对话区（thinking）和办公区（busy/working/coding/writing/reading）
    if (e.status === 'thinking') continue;
    if (e.status === 'busy' || e.status === 'working' || e.status === 'coding' || e.status === 'writing' || e.status === 'reading') continue;

    var isOnline = e.status === 'online';
    if (isOnline) onlineCount++;

    // 使用renderAvatar渲染头像（圆角矩形）
    var avatarHtml = renderAvatar(e, 48, '14px');
    var dotClass = isOnline ? '' : 'offline';
    var chipClass = isOnline ? 'lounge-chip active' : 'lounge-chip';

    html += '<div class="' + chipClass + '" onclick="openDetailPanel(\'' + e.id + '\')">';
    html += '<div class="lounge-avatar">';
    html += avatarHtml;
    html += '<div class="lounge-dot ' + dotClass + '"></div>';
    html += '</div>';
    html += '<span class="lounge-name">' + escapeHtml(e.name) + '</span>';
    html += '</div>';
  }

  loungeZone.innerHTML = html;

  // 更新在线人数badge
  var loungeNum = document.getElementById('loungeNum');
  var loungeBadge = document.getElementById('loungeBadge');
  if (loungeNum) loungeNum.textContent = onlineCount;
  if (loungeBadge) {
    if (onlineCount > 0) {
      loungeBadge.style.display = 'inline-flex';
    } else {
      loungeBadge.style.display = 'none';
    }
  }
}

function updateStatusTime(){
  var el = document.getElementById('statusTime');
  if(el){
    var now = new Date();
    el.textContent = now.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
  }
}

// ===== Live 更新模拟 =====
function startLiveUpdates(){
  setInterval(function(){
    if (employees.length === 0) return;
    var idx = Math.floor(Math.random()*activities.length);
    var statuses = ['thinking','done','idle','p1'];
    var texts = ['正在处理任务...','✅ 任务完成','💤 等待中','⚠️ 发现问题'];
    if (activities[idx]) {
      activities[idx].status = statuses[Math.floor(Math.random()*statuses.length)];
      activities[idx].time = '刚刚';
    }
    renderActivities();
  },8000);
}

// ===== 实时插入机制 =====
function moveToChatFront(empId){
  var emp = employees.find(function(e){return e.id === empId});
  if(!emp) return;
  emp.status = 'thinking';
  emp.lastActive = new Date().toISOString();
  employees.sort(function(a,b){
    var ta = a.lastActive ? new Date(a.lastActive) : new Date(0);
    var tb = b.lastActive ? new Date(b.lastActive) : new Date(0);
    return tb - ta;
  });
  renderChatZone();
  renderWorkZone();
  renderLoungeZone();
  addTimelineEvent(emp.name, '开始思考...');
  broadcastStatus(empId, 'thinking');
}

function addTimelineEvent(name, action){
  var list = document.querySelector('.timeline-list');
  if(!list) return;
  var time = new Date().toLocaleTimeString('zh-CN', {hour:'2-digit', minute:'2-digit'});
  var item = document.createElement('div');
  item.className = 'timeline-item';
  // 查找员工获取头像，找不到就用名字hash分配一个预设头像
  var emp = employees.find(function(e) { return e.name === name; });
  var avatarHtml;
  if (emp) {
    avatarHtml = renderAvatar(emp, 28, '8px', 'timeline-avatar');
  } else if (typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS.length > 0) {
    var hash = 0;
    var n = name || '';
    for (var c = 0; c < n.length; c++) {
      hash = ((hash << 5) - hash) + n.charCodeAt(c);
      hash = hash & hash;
    }
    var idx = Math.abs(hash) % AVATAR_PRESETS.length;
    avatarHtml = '<div class="timeline-avatar" style="width:28px;height:28px;border-radius:8px;overflow:hidden;"><img src="' + escapeAttr(AVATAR_PRESETS[idx]) + '" style="width:28px;height:28px;border-radius:8px;object-fit:cover;display:block;"></div>';
  } else {
    avatarHtml = '<span style="font-size:14px;font-weight:700;color:rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:8px;background:#F2F2F7;">' + (name ? name.charAt(0).toUpperCase() : '?') + '</span>';
  }
  item.innerHTML = avatarHtml+'<div class="timeline-content"><div class="timeline-text"><b>'+name+'</b> '+action+'</div><div class="timeline-time">'+time+'</div></div>';
  list.insertBefore(item, list.firstChild);
  while(list.children.length > 10){
    list.removeChild(list.lastChild);
  }
}

// ===== 初始化 =====

function renderTaskList(){
  var container = document.getElementById('taskList');
  if(!container) return;
  
  // 从 localStorage 读取日历事件
  var events = [];
  try{
    events = JSON.parse(localStorage.getItem('sb_calendar_events') || '[]');
  }catch(e){}
  
  if(events.length === 0){
    container.innerHTML = '<div style="padding:10px;text-align:center;color:var(--gray-4);font-size:12px">暂无定时任务</div>';
    return;
  }
  
  var html = '';
  for(var i=0; i<Math.min(events.length, 5); i++){
    var ev = events[i];
    var priority = (i % 3 === 0) ? 'p1' : (i % 3 === 1) ? 'p2' : 'p3';
    var name = ev.summary || ev.title || '未命名任务';
    if(name.length > 15) name = name.substring(0, 15) + '...';
    html += '<div class="task-item"><span class="task-dot ' + priority + '"></span>' + name + '</div>';
  }
  container.innerHTML = html;
}

document.addEventListener('DOMContentLoaded', async function(){
  // Initialize OpenClaw connection
  await initOpenClaw();
  
  // Initialize BroadcastChannel
  initBroadcastChannel();
  
  // Render UI
  updateStats();
  renderActivities();
  renderTaskList();
  renderEmployees();
  renderSkills();
  renderDocs();
  startLiveUpdates();
  renderChatZone();
  renderWorkZone();
  renderLoungeZone();
  updateStatusTime();
  setInterval(updateStatusTime, 60000);
  setInterval(renderActivities, 5000);
});

// Backup renderActivities for fallback
function renderActivitiesFallback(){
  var container = document.getElementById('liveActivities');
  if(!container) return;
  container.innerHTML = activitiesFallback.map(function(a){
    return '<div class="activity-item">' +
      '<div class="activity-avatar" style="background:' + a.gradient[0] + '">' + a.initial + '</div>' +
      '<div class="activity-content">' +
      '<div class="activity-name">' + a.name + '</div>' +
      '<div class="activity-status">' +
      '<span class="status-badge ' + a.status + '">' + a.text + '</span>' +
      '</div></div>' +
      '<div class="activity-time">' + a.time + '</div></div>';
  }).join('');
}

// ====== 创建龙虾向导 ======
var soulTemplates = {
  assistant: {
    name: '助理型',
    emoji: '📋',
    desc: '温柔、专业、高效',
    soul: '# SOUL.md\n\n你是{name}，一个专业高效的助理。\n\n## 核心特质\n- 温柔且专业\n- 高效执行任务\n- 主动思考问题\n\n## 沟通风格\n- 简洁明了\n- 先给结论再展开\n- 适当使用emoji增加温度',
    identity: '# IDENTITY.md\n\n## 基本信息\n- 名称: {name}\n- 角色: {role}\n- 类型: 助理型\n\n## 能力\n- 日程管理\n- 信息整理\n- 文档撰写',
    user: '# USER.md\n\n## 用户画像\n- 目标用户: 需要高效助理支持的专业人士\n- 使用场景: 日常办公、会议安排、文档处理\n- 痛点: 时间碎片化、信息过载\n\n## 用户偏好\n- 沟通风格: 简洁专业\n- 响应速度: 快速\n- 工作时间: 9:00-18:00',
    tools: '# TOOLS.md\n\n## 可用工具\n- 日历管理\n- 邮件发送\n- 文档编辑\n- 会议预约\n\n## 使用规则\n- 先确认再执行\n- 重要操作需二次确认\n- 记录所有操作日志'
  },
  sales: {
    name: '销售型',
    emoji: '🎯',
    desc: '热情、自信、说服力',
    soul: '# SOUL.md\n\n你是{name}，一个充满热情的销售专家。\n\n## 核心特质\n- 热情自信\n- 洞察客户需求\n- 强大的说服力\n\n## 沟通风格\n- 先共情再推荐\n- 用数据说话\n- 制造紧迫感',
    identity: '# IDENTITY.md\n\n## 基本信息\n- 名称: {name}\n- 角色: {role}\n- 类型: 销售型\n\n## 能力\n- 客户需求分析\n- 产品推荐\n- 商务谈判'
  },
  tech: {
    name: '技术型',
    emoji: '💻',
    desc: '严谨、逻辑、代码',
    soul: '# SOUL.md\n\n你是{name}，一个严谨的技术专家。\n\n## 核心特质\n- 逻辑严密\n- 注重代码质量\n- 系统性思维\n\n## 沟通风格\n- 技术术语精准\n- 给出完整方案而非片段\n- 先分析再动手',
    identity: '# IDENTITY.md\n\n## 基本信息\n- 名称: {name}\n- 角色: {role}\n- 类型: 技术型\n\n## 能力\n- 代码开发\n- 架构设计\n- Bug调试'
  },
  service: {
    name: '客服型',
    emoji: '🤝',
    desc: '耐心、同理心、解决问题',
    soul: '# SOUL.md\n\n你是{name}，一个耐心体贴的客服专员。\n\n## 核心特质\n- 极度耐心\n- 同理心强\n- 解决问题为导向\n\n## 沟通风格\n- 先安抚情绪\n- 确认理解客户问题\n- 给出明确解决步骤',
    identity: '# IDENTITY.md\n\n## 基本信息\n- 名称: {name}\n- 角色: {role}\n- 类型: 客服型\n\n## 能力\n- 问题诊断\n- 工单处理\n- 客户安抚'
  },
  creative: {
    name: '策划型',
    emoji: '💡',
    desc: '创意、洞察、结构化',
    soul: '# SOUL.md\n\n你是{name}，一个富有创意的策划师。\n\n## 核心特质\n- 创意无限\n- 洞察用户心理\n- 结构化输出方案\n\n## 沟通风格\n- 用类比和故事表达\n- 方案分层呈现\n- 鼓励头脑风暴',
    identity: '# IDENTITY.md\n\n## 基本信息\n- 名称: {name}\n- 角色: {role}\n- 类型: 策划型\n\n## 能力\n- 创意策划\n- 用户研究\n- 方案设计'
  }
};

var avatarPresets = [];
if(typeof AVATAR_PRESETS !== 'undefined'){
  for(var i=0;i<AVATAR_PRESETS.length;i++){
    avatarPresets.push({index:i, src:AVATAR_PRESETS[i]});
  }
}

var wizardState = {
  step: 1,
  avatar: null,
  avatarBg: null,
  name: '',
  role: '',
  bio: '',
  model: 'glm-5',
  soulTemplate: 'assistant'
};

function openCreateWizard() {
  wizardState = { step: 1, avatar: null, avatarBg: null, name: '', role: '', bio: '', model: 'glm-5', soulTemplate: 'assistant' };
  document.getElementById('wizardOverlay').classList.add('active');
  renderWizardStep();
}

function closeWizard() {
  document.getElementById('wizardOverlay').classList.remove('active');
}

function renderWizardStep() {
  var stepIndicators = document.querySelectorAll('.wizard-step');
  stepIndicators.forEach(function(el, i) {
    var num = i + 1;
    el.classList.remove('active', 'done');
    if (num === wizardState.step) el.classList.add('active');
    else if (num < wizardState.step) el.classList.add('done');
  });

  var steps = document.querySelectorAll('.wizard-step-content');
  steps.forEach(function(el, i) {
    el.style.display = i + 1 === wizardState.step ? 'block' : 'none';
  });
  
  var prevBtn = document.getElementById('wizardPrevBtn');
  var nextBtn = document.getElementById('wizardNextBtn');
  var createBtn = document.getElementById('wizardCreateBtn');
  
  prevBtn.style.display = wizardState.step > 1 ? 'inline-flex' : 'none';
  nextBtn.style.display = wizardState.step < 3 ? 'inline-flex' : 'none';
  createBtn.style.display = wizardState.step === 3 ? 'inline-flex' : 'none';
  
  if (wizardState.step === 3) {
    updateWizardPreview();
  }
}

function selectAvatar(avatarIndex) {
  wizardState.avatar = avatarIndex;
  wizardState.avatarBg = '';
  document.querySelectorAll('.avatar-option').forEach(function(el){ el.classList.remove('selected'); });
  event.currentTarget.classList.add('selected');
  updateWizardPreview();
}

function updateAvatarFromInput() {
  var input = document.getElementById('customAvatarInput');
  var text = input.value.trim();
  if(!text) return;
  var num = parseInt(text);
  if(!isNaN(num) && num >= 1 && num <= 24 && typeof AVATAR_PRESETS !== 'undefined'){
    wizardState.avatar = num - 1;
  } else {
    wizardState.avatar = text.charAt(0);
  }
  wizardState.avatarBg = '';
  document.querySelectorAll('.avatar-option').forEach(function(el){ el.classList.remove('selected'); });
  updateWizardPreview();
}

function nextStep() {
  if (wizardState.step === 1 && !wizardState.avatar) {
    showToast('请选择或输入头像字母', 'error');
    return;
  }
  if (wizardState.step === 2) {
    wizardState.name = document.getElementById('wizardName').value.trim();
    wizardState.role = document.getElementById('wizardRole').value.trim();
    wizardState.bio = document.getElementById('wizardBio').value.trim();
    if (!wizardState.name) { showToast('请输入名字', 'error'); return; }
    if (!wizardState.role) { showToast('请输入职位', 'error'); return; }
  }
  if (wizardState.step < 3) {
    wizardState.step++;
    renderWizardStep();
  }
}

function prevStep() {
  if (wizardState.step > 1) {
    wizardState.step--;
    renderWizardStep();
  }
}

function selectSoulTemplate(key) {
  wizardState.soulTemplate = key;
  document.querySelectorAll('.soul-template-card').forEach(function(el){ el.classList.remove('selected'); });
  event.currentTarget.classList.add('selected');
  updateWizardPreview();
}

function updateWizardPreview() {
  var preview = document.getElementById('wizardPreview');
  if (!preview) return;
  var tmpl = soulTemplates[wizardState.soulTemplate];
  var avatarHtml = '?';
  if(typeof wizardState.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[wizardState.avatar]){
    avatarHtml = '<img src="' + AVATAR_PRESETS[wizardState.avatar] + '" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">';
  } else if(wizardState.avatar && typeof wizardState.avatar === 'string' && (wizardState.avatar.indexOf('data:image') === 0 || wizardState.avatar.indexOf('.png') > 0)){
    avatarHtml = '<img src="' + wizardState.avatar + '" style="width:100%;height:100%;border-radius:50%;object-fit:cover;">';
  } else {
    avatarHtml = (wizardState.avatar || '?');
  }
  preview.innerHTML = '<div class="wizard-preview-title">✨ 预览</div>' +
    '<div class="wizard-preview-content">' +
    '<div class="wizard-preview-avatar" style="background:linear-gradient(135deg, #FFED4A, #FFD60A);overflow:hidden;">' + avatarHtml + '</div>' +
    '<div class="wizard-preview-info">' +
    '<div class="wizard-preview-name">' + (wizardState.name || '名字') + '</div>' +
    '<div class="wizard-preview-role">' + (wizardState.role || '职位') + '</div>' +
    '<div class="wizard-preview-soul">' + tmpl.emoji + ' ' + tmpl.name + ' · ' + tmpl.desc + '</div>' +
    '</div></div>';
}

async function confirmCreateAgent() {
  wizardState.model = document.getElementById('wizardModelSelect').value;
  
  var tmpl = soulTemplates[wizardState.soulTemplate];
  var soul = tmpl.soul.replace(/{name}/g, wizardState.name).replace(/{role}/g, wizardState.role);
  var identity = tmpl.identity.replace(/{name}/g, wizardState.name).replace(/{role}/g, wizardState.role);
  var user = tmpl.user.replace(/{name}/g, wizardState.name).replace(/{role}/g, wizardState.role);
  var tools = tmpl.tools.replace(/{name}/g, wizardState.name).replace(/{role}/g, wizardState.role);
  
  showToast('正在创建 Agent...', 'loading');
  
  try {
    if (window.openclawConnected && typeof openclaw !== 'undefined' && openclaw.agents) {
      var result = await openclaw.send('agents.create', {
        name: wizardState.name,
        role: wizardState.role,
        model: wizardState.model,
        soul: soul,
        identity: identity,
        status: 'idle'
      });
      
      showToast('🎉 Agent 创建成功！', 'success');
      
      // Add to local array
      var newAgent = {
        id: result.id || 'agent_' + Date.now(),
        name: wizardState.name,
        role: wizardState.role,
        bio: wizardState.bio,
        avatar: wizardState.avatar,
        avatarBg: wizardState.avatarBg,
        model: wizardState.model,
        soulType: wizardState.soulTemplate,
        status: 'idle',
        skills: [],
        channels: [],
        user: user,
        tools: tools
      };
      employees.push(newAgent);
    } else {
      // Offline mode simulation
      var newAgent = {
        id: 'emp_' + Date.now(),
        name: wizardState.name,
        role: wizardState.role,
        bio: wizardState.bio,
        avatar: wizardState.avatar,
        avatarBg: wizardState.avatarBg,
        model: wizardState.model,
        soulType: wizardState.soulTemplate,
        status: 'idle',
        skills: [],
        channels: [],
        lastActive: new Date().toISOString(),
        user: user,
        tools: tools
      };
      employees.push(newAgent);
      showToast('🌟 Agent 创建成功（离线模式）', 'success');
    }
    
    // 同步到 localStorage 和通知主页
    
    // 尝试同步到后端
    try {
      var newEmp = employees[employees.length - 1];
      await authFetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: newEmp.id,
          name: newEmp.name,
          role: newEmp.role,
      bg: newEmp.bg || (newEmp.gradient && newEmp.gradient[0]) || '#FF6B35',
      avatar: newEmp.avatar || (newEmp.name && newEmp.name[0]) || '👤',
          status: 'idle',
          permission: 'dev',
          category: '职能',
          subCategory: '技术团队'
        })
      });
    } catch (e) {
      console.log('[Office] 同步到后端失败:', e.message);
    }
    
    closeWizard();
    renderEmployees();
    renderChatZone();
    renderWorkZone();
    renderLoungeZone();
    updateStats();
    
  } catch (err) {
    console.error('Create agent error:', err);
    showToast('❌ 创建失败: ' + err.message, 'error');
  }
}

// 初始化头像网格
(function initAvatarGrid() {
  var grid = document.getElementById('avatarGrid');
  if (!grid) return;
  if(avatarPresets.length > 0){
    grid.innerHTML = avatarPresets.map(function(a) {
      return '<div class="avatar-option" onclick="selectAvatar(' + a.index + ')"><img src="' + a.src + '" style="width:48px;height:48px;border-radius:50%;object-fit:cover;"></div>';
    }).join('');
  } else {
    grid.innerHTML = '<div style="color:#8E8E93;font-size:13px;padding:20px;text-align:center;">头像资源加载中...</div>';
  }
})();

// ===== 锁定 deleteMemory/editMemory，防止被空壳函数覆盖 =====
// 使用 Object.defineProperty 锁定，任何后续赋值都会静默失败
(function(){
  var realDeleteMemory = function(empId, memoryId){
    if(!confirm('确定删除这条记忆？')) return;
    apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {
      method: 'DELETE'
    }).then(function(){
      showToast('✅ 记忆已删除');
      renderMemoryTab(empId);
      updateMemoryTabTitle(empId);
    }).catch(function(e){
      showToast('❌ 删除失败');
      console.warn('[Memory] 删除失败:', e);
    });
  };

  var realEditMemory = function(empId, memoryId){
    // 编辑功能待实现，先给提示
    showToast('编辑功能开发中');
  };

  Object.defineProperty(window, 'deleteMemory', {
    value: realDeleteMemory,
    writable: false,
    configurable: false
  });

  Object.defineProperty(window, 'editMemory', {
    value: realEditMemory,
    writable: false,
    configurable: false
  });
})();
