/**
 * Employee Model - AI 员工数据模型
 * Day 1 核心模块
 */

// 性格模板
export const PERSONALITY_TEMPLATES = {
  rigorous: {
    name: '严谨理性型',
    description: '逻辑清晰、注重细节、追求精确',
    suitable: ['技术', '分析', '财务'],
    basePrompt: '你是一个严谨理性的专业人士。你注重逻辑和事实，说话简洁准确，不喜欢模糊表达。'
  },
  creative: {
    name: '创意活泼型',
    description: '思维活跃、充满热情、善于创新',
    suitable: ['设计', '产品', '市场'],
    basePrompt: '你是一个创意活泼的专业人士。你思维活跃、充满热情，善于提出新颖的想法和解决方案。'
  },
  steady: {
    name: '稳重可靠型',
    description: '踏实稳重、值得信赖、注重执行',
    suitable: ['运营', '项目管理', '客服'],
    basePrompt: '你是一个稳重可靠的专业人士。你踏实务实、值得信赖，注重把事情做好做扎实。'
  },
  sharp: {
    name: '敏锐果断型',
    description: '洞察力强、决策迅速、目标导向',
    suitable: ['管理', '战略', '销售'],
    basePrompt: '你是一个敏锐果断的专业人士。你洞察力强、决策迅速，善于抓住关键问题和机会。'
  }
};

// 说话风格
export const SPEAKING_STYLES = {
  formal: {
    name: '正式专业',
    prompt: '请使用正式、专业的语言风格回复，用词准确，结构清晰。'
  },
  friendly: {
    name: '轻松友好',
    prompt: '请使用轻松、友好的语言风格回复，可以适当使用口语化表达，让人感到亲切。'
  },
  humorous: {
    name: '幽默风趣',
    prompt: '请使用幽默风趣的语言风格回复，可以适当开玩笑，让对话更加轻松愉快。'
  },
  concise: {
    name: '简洁直接',
    prompt: '请使用简洁、直接的语言风格回复，避免冗余，直击要点。'
  }
};

// 技能等级
export const SKILL_LEVELS = {
  beginner: { name: '入门级', level: 30 },
  competent: { name: '胜任级', level: 50 },
  proficient: { name: '熟练级', level: 70 },
  expert: { name: '专业级', level: 85 },
  master: { name: '大师级', level: 95 }
};

/**
 * 创建新员工
 */
export function createEmployee(config) {
  const now = new Date().toISOString();
  const template = PERSONALITY_TEMPLATES[config.personalityTemplate || 'creative'];
  const style = SPEAKING_STYLES[config.speakingStyle || 'friendly'];

  return {
    // 基础信息
    id: generateId('emp'),
    name: config.name || '未命名员工',
    position: config.position || '助理',
    avatar: config.avatar || null,
    initial: config.name ? config.name.charAt(0).toUpperCase() : '?',
    gradient: config.gradient || generateGradient(),

    // 灵魂配置
    soul: {
      template: config.personalityTemplate || 'creative',
      templateName: template.name,
      traits: {
        creativity: config.traits?.creativity ?? 70,
        logic: config.traits?.logic ?? 60,
        enthusiasm: config.traits?.enthusiasm ?? 70,
        patience: config.traits?.patience ?? 70,
        detail: config.traits?.detail ?? 50
      },
      speakingStyle: config.speakingStyle || 'friendly',
      speakingStyleName: style.name,
      values: {
        qualityFirst: config.values?.qualityFirst ?? true,
        userCentric: config.values?.userCentric ?? true,
        speedOverPerfection: config.values?.speedOverPerfection ?? false
      }
    },

    // 能力配置
    skills: {
      core: config.skills?.core || {},
      tools: config.skills?.tools || [],
      knowledgeDomains: config.skills?.knowledgeDomains || []
    },

    // 工作偏好
    workPreferences: {
      mode: config.workPreferences?.mode || 'proactive',
      responseSpeed: config.workPreferences?.responseSpeed || 'balanced',
      workingHours: config.workPreferences?.workingHours || ['weekday']
    },

    // 记忆系统
    memory: {
      shortTerm: [],
      longTerm: [],
      projectContext: {}
    },

    // 状态
    status: {
      online: true,
      currentTask: null,
      workload: 0
    },

    // 统计
    stats: {
      tasksCompleted: 0,
      messagesSent: 0,
      hoursWorked: 0,
      createdAt: now,
      lastActiveAt: now
    }
  };
}

/**
 * 生成系统提示词
 */
export function generateSystemPrompt(employee) {
  const { soul, skills, workPreferences } = employee;
  const template = PERSONALITY_TEMPLATES[soul.template];
  const style = SPEAKING_STYLES[soul.speakingStyle];

  const parts = [
    // 1. 身份定义
    `你是 ${employee.name}，${employee.position}。`,

    // 2. 性格模板
    template.basePrompt,

    // 3. 性格参数
    '你的性格特点：',
    `- 创造力：${soul.traits.creativity}/100 ${soul.traits.creativity > 70 ? '（擅长创新）' : ''}`,
    `- 逻辑性：${soul.traits.logic}/100 ${soul.traits.logic > 70 ? '（擅长分析）' : ''}`,
    `- 热情度：${soul.traits.enthusiasm}/100 ${soul.traits.enthusiasm > 70 ? '（表达积极）' : ''}`,
    `- 耐心度：${soul.traits.patience}/100 ${soul.traits.patience > 70 ? '（细心周到）' : ''}`,
    `- 细节关注：${soul.traits.detail}/100 ${soul.traits.detail > 70 ? '（注重细节）' : ''}`,

    // 4. 说话风格
    style.prompt,

    // 5. 价值观
    soul.values.qualityFirst ? '你注重工作质量，宁可多花时间也要把事情做好。' : '',
    soul.values.userCentric ? '你始终以用户价值为导向思考问题。' : '',
    soul.values.speedOverPerfection ? '你倾向于快速交付，在迭代中完善。' : '',

    // 6. 能力描述
    skills.knowledgeDomains.length > 0 ? `你的专业领域：${skills.knowledgeDomains.join('、')}。` : '',

    // 7. 工作模式
    workPreferences.mode === 'proactive' ? '你会主动思考、提出建议，而不是被动等待指示。' :
    workPreferences.mode === 'execution' ? '你专注于高效执行，把分配的任务做到最好。' :
    '你善于团队协作，乐于与他人配合完成工作。'
  ];

  return parts.filter(Boolean).join('\n');
}

/**
 * 生成唯一 ID
 */
function generateId(prefix) {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 6);
  return `${prefix}_${timestamp}_${random}`;
}

/**
 * 生成随机渐变
 */
function generateGradient() {
  const gradients = [
    'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
    'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
    'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)',
    'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
    'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)',
    'linear-gradient(135deg, #ff9a9e 0%, #fecfef 100%)',
    'linear-gradient(135deg, #a18cd1 0%, #fbc2eb 100%)',
    'linear-gradient(135deg, #fad0c4 0%, #ffd1ff 100%)'
  ];
  return gradients[Math.floor(Math.random() * gradients.length)];
}

/**
 * 更新员工统计
 */
export function updateEmployeeStats(employee, updates) {
  return {
    ...employee,
    stats: {
      ...employee.stats,
      ...updates,
      lastActiveAt: new Date().toISOString()
    }
  };
}

/**
 * 验证员工数据完整性
 */
export function validateEmployee(employee) {
  const required = ['id', 'name', 'position', 'soul'];
  const missing = required.filter(key => !(key in employee));
  return {
    valid: missing.length === 0,
    missing
  };
}