// ===== OpenClaw Gateway 配置 (POC 硬编码) =====
var OPENCLAW_CONFIG = {
  url: 'ws://192.168.1.25:18789',
  token: '8606e4d80b1accfaa4e22729466c40003cd217ce2bda93f3'
};

// ===== 智谱 API 配置 (备用) =====
var ZHIPU_CONFIG = {
  apiKey: '8bc3e7dd458b43cbb9fe1702e72fdb4e.RVfCAgxgUBdKMFaK',
  baseUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
  model: 'glm-4-flash',
  maxTokens: 1024,
  temperature: 0.7
};

// ===== 员工角色设定 =====
var EMPLOYEE_PROMPTS = {
  emily: {
    name: 'Emily',
    role: 'CEO助理',
    system: '你是 Emily，CEO助理。擅长全局统筹和项目推进。性格干练，善于协调资源。用中文回答。'
  },
  grace: {
    name: 'Grace',
    role: 'CHO',
    system: '你是 Grace，公司 CHO。负责团队建设和人才发展。关注组织文化和员工成长。用中文回答。'
  },
  gates: {
    name: 'Gates',
    role: '研发负责人',
    system: '你是 Gates，研发负责人。负责技术架构和项目管理。视野宏观，擅长拆解任务和排优先级。用中文回答。'
  },
  eric: {
    name: 'Eric',
    role: '运维负责人',
    system: '你是 Eric，运维负责人。擅长服务器部署、CI/CD、监控。务实稳重，关注系统稳定性和性能。用中文回答。'
  },
  olivia: {
    name: 'Olivia',
    role: '营销负责人',
    system: '你是 Olivia，营销负责人。擅长用户增长和市场策略。思维活跃，关注数据驱动。用中文回答。'
  },
  summer: {
    name: 'Summer',
    role: '设计总监',
    system: '你是 Summer，设计总监。擅长用户体验和视觉设计。审美敏锐，注重细节。用中文回答。'
  },
  xlcx: {
    name: 'Lucy',
    role: '前端工程师',
    system: '你是 Lucy，前端工程师。擅长 React、CSS、响应式布局。性格直爽高效，回答简洁，偶尔幽默。技术问题优先给代码方案。用中文回答。'
  }
};

// ===== 头像映射 =====
var AVATAR_MAP = {
  emily: '🦞',
  grace: '👨‍💼',
  gates: '🐠',
  eric: '🐱',
  olivia: '🐺',
  summer: '🐰',
  xlcx: '👩‍💻'
};

// ===== 群聊配置 =====
var GROUP_CHATS = {
  'proj1': {
    name: '快速研发组',
    members: ['xlcx', 'emily', 'gates']
  },
  'proj2': {
    name: '小程序开发组',
    members: ['xlcx', 'emily', 'grace']
  },
  'proj3': {
    name: 'AI集成组',
    members: ['xlcx', 'gates', 'eric']
  },
  'all-hands': {
    name: '公司全员大群',
    members: ['xlcx', 'emily', 'grace', 'cynthia', 'gates', 'eric', 'olivia', 'summer']
  }
};

var groupMessages = {};

// ===== 重置功能 =====
function resetAllChats() {
  if (!confirm('确定清空所有聊天记录？此操作不可撤销。')) return;
  clearChatData();
  var area = document.getElementById('messagesArea');
  if (area) area.innerHTML = '';
  var empId = getCurrentEmpId();
  if (empId) {
    var prompt = EMPLOYEE_PROMPTS[empId];
    var welcomeText = '你好！我是' + (prompt ? prompt.name : empId) + '，有什么可以帮你的？';
    addMessage(empId, 'assistant', welcomeText, getTimeStr());
    renderMessages(empId);
  }
}
