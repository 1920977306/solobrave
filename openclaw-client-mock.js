/**
 * OpenClawClient Mock Data & Handler
 * Separated from core to keep files under 500 lines
 */

(function(global) {
  'use strict';

  function now() { return Date.now(); }

  // ===== Mock Data =====
  // Mock agents - SoloBrave employees
  var MOCK_AGENTS = [
    { id: 'emily', name: 'Emily', role: 'CEO助理', avatar: 'E', status: 'working', model: 'gpt-4', description: '负责CEO日常事务、会议安排、邮件处理', skills: ['日程管理', '邮件处理', '会议记录'] },
    { id: 'xiaoke', name: '小可', role: '全可AI客服', avatar: '小', status: 'online', model: 'claude-3', description: '7x24小时智能客服，处理用户咨询和售后', skills: ['客户沟通', '问题解答', '售后处理'] }
  ];

  var MOCK_SESSIONS = [
    { key: 'agent:emily:main', agentId: 'emily', title: '与 Emily 的对话', lastMessage: '会议安排已确认', timestamp: now() - 300000, unread: 0 },
    { key: 'agent:xiaoke:main', agentId: 'xiaoke', title: '与小可的对话', lastMessage: '用户问题已解决', timestamp: now() - 600000, unread: 2 }
  ];

  var MOCK_MESSAGES = {
    'agent:emily:main': [
      { id: 'm1', role: 'user', content: [{ type: 'text', text: '帮我安排明天的会议' }], timestamp: now() - 3600000 },
      { id: 'm2', role: 'assistant', content: [{ type: 'thinking', thinking: '正在查看CEO明天的日程...' }, { type: 'text', text: '已为您安排明天上午10点的团队会议，地点：会议室A' }], timestamp: now() - 3500000 }
    ],
    'agent:xiaoke:main': [
      { id: 'm3', role: 'user', content: [{ type: 'text', text: '用户问怎么退款' }], timestamp: now() - 7200000 },
      { id: 'm4', role: 'assistant', content: [{ type: 'text', text: '退款流程：1. 进入订单页面 2. 点击申请退款 3. 选择退款原因 4. 提交审核' }], timestamp: now() - 7100000 }
    ]
  };

  // ===== Mock Request Handler =====
  OpenClawClient.prototype._mockRequest = function(method, params) {
    params = params || {};
    return new Promise(function(resolve) {
      setTimeout(function() {
        switch (method) {
          case 'agents.list':
            resolve({ agents: MOCK_AGENTS });
            break;
          case 'agent.identity.get':
            var agent = MOCK_AGENTS.find(function(a) { return a.id === params.agentId; });
            resolve({ identity: agent || null });
            break;
          case 'sessions.list':
            resolve({ sessions: MOCK_SESSIONS });
            break;
          case 'chat.history':
            var msgs = MOCK_MESSAGES[params.sessionKey] || [];
            resolve({ messages: msgs, hasMore: false });
            break;
          case 'chat.send':
            var newMsg = {
              id: 'mock_' + Date.now(),
              role: 'assistant',
              content: [{ type: 'text', text: 'This is a mock response from ' + (params.sessionKey || 'agent') + '.' }],
              timestamp: now()
            };
            var list = MOCK_MESSAGES[params.sessionKey] || [];
            list.push(newMsg);
            resolve({ message: newMsg });
            break;
          case 'models.list':
            resolve({ models: [{ id: 'gpt-4', name: 'GPT-4' }, { id: 'claude-3', name: 'Claude 3' }, { id: 'dall-e', name: 'DALL-E' }] });
            break;
          case 'health':
            resolve({ status: 'ok', version: '1.0.0-mock', mock: true });
            break;
          case 'tools.catalog':
            resolve({ tools: [{ name: 'search', description: 'Web search' }, { name: 'code', description: 'Code execution' }] });
            break;
          case 'skills.status':
            resolve({ skills: [{ name: 'chat', status: 'active' }, { name: 'image', status: 'active' }] });
            break;
          default:
            resolve({ mock: true, method: method });
        }
      }, 150);
    });
  };

  // ===== Convenience Methods =====
  OpenClawClient.prototype.getAgents = function() {
    return this.request('agents.list');
  };

  OpenClawClient.prototype.getAgentIdentity = function(agentId) {
    return this.request('agent.identity.get', { agentId: agentId });
  };

  OpenClawClient.prototype.getSessions = function() {
    return this.request('sessions.list');
  };

  OpenClawClient.prototype.getHistory = function(sessionKey) {
    return this.request('chat.history', { sessionKey: sessionKey });
  };

  OpenClawClient.prototype.sendMessage = function(sessionKey, text) {
    return this.request('chat.send', {
      sessionKey: sessionKey,
      content: [{ type: 'text', text: text }]
    });
  };

  OpenClawClient.prototype.getModels = function() {
    return this.request('models.list');
  };

  OpenClawClient.prototype.getHealth = function() {
    return this.request('health');
  };

  OpenClawClient.prototype.getTools = function() {
    return this.request('tools.catalog');
  };

  OpenClawClient.prototype.getSkills = function() {
    return this.request('skills.status');
  };

})(typeof window !== 'undefined' ? window : this);
