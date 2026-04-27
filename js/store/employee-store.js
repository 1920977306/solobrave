/**
 * EmployeeStore - 员工数据管理
 * M1 Step 1: 支持多员工的增删改查和模板系统
 */

import localStore from '../storage/local-store.js';

// 员工岗位模板
export const EMPLOYEE_TEMPLATES = {
  product_manager: {
    name: '产品经理',
    position: '产品经理',
    personalityTemplate: 'creative',
    speakingStyle: 'friendly',
    defaultTraits: {
      creativity: 80,
      logic: 60,
      enthusiasm: 75,
      patience: 70,
      detail: 55,
      communication: 85
    },
    defaultSkills: {
      core: {
        product_planning: { level: 80, name: '产品策划' },
        user_research: { level: 70, name: '用户研究' },
        documentation: { level: 60, name: '文档撰写' }
      },
      tools: ['search', 'document', 'analysis'],
      knowledgeDomains: ['互联网产品', '用户体验', '敏捷开发']
    }
  },

  tech_architect: {
    name: '技术架构师',
    position: '技术架构师',
    personalityTemplate: 'rational',
    speakingStyle: 'professional',
    defaultTraits: {
      creativity: 60,
      logic: 95,
      enthusiasm: 50,
      patience: 80,
      detail: 90,
      communication: 70
    },
    defaultSkills: {
      core: {
        system_design: { level: 90, name: '系统设计' },
        code_review: { level: 85, name: '代码审查' },
        tech_research: { level: 80, name: '技术调研' }
      },
      tools: ['search', 'code', 'document'],
      knowledgeDomains: ['系统架构', '微服务', '云原生', '性能优化']
    }
  },

  designer: {
    name: '设计师',
    position: 'UI/UX 设计师',
    personalityTemplate: 'creative',
    speakingStyle: 'friendly',
    defaultTraits: {
      creativity: 95,
      logic: 50,
      enthusiasm: 85,
      patience: 75,
      detail: 80,
      communication: 75
    },
    defaultSkills: {
      core: {
        ui_design: { level: 90, name: 'UI 设计' },
        ux_research: { level: 80, name: 'UX 研究' },
        prototyping: { level: 85, name: '原型制作' }
      },
      tools: ['search', 'document', 'image'],
      knowledgeDomains: ['界面设计', '交互设计', '设计系统', '用户心理']
    }
  },

  frontend_dev: {
    name: '前端开发',
    position: '前端工程师',
    personalityTemplate: 'steady',
    speakingStyle: 'concise',
    defaultTraits: {
      creativity: 65,
      logic: 85,
      enthusiasm: 70,
      patience: 75,
      detail: 85,
      communication: 60
    },
    defaultSkills: {
      core: {
        frontend_dev: { level: 90, name: '前端开发' },
        ui_implementation: { level: 85, name: 'UI 实现' },
        performance_opt: { level: 75, name: '性能优化' }
      },
      tools: ['search', 'code', 'document'],
      knowledgeDomains: ['React/Vue', 'CSS/动画', '前端工程化', '浏览器原理']
    }
  },

  operator: {
    name: '运营专员',
    position: '运营专员',
    personalityTemplate: 'creative',
    speakingStyle: 'friendly',
    defaultTraits: {
      creativity: 75,
      logic: 55,
      enthusiasm: 90,
      patience: 65,
      detail: 60,
      communication: 85
    },
    defaultSkills: {
      core: {
        content_planning: { level: 80, name: '内容策划' },
        data_analysis: { level: 70, name: '数据分析' },
        user_operation: { level: 75, name: '用户运营' }
      },
      tools: ['search', 'document', 'analysis'],
      knowledgeDomains: ['内容运营', '用户增长', '活动策划', '社交媒体']
    }
  },

  copywriter: {
    name: '文案写手',
    position: '文案策划',
    personalityTemplate: 'creative',
    speakingStyle: 'humorous',
    defaultTraits: {
      creativity: 90,
      logic: 50,
      enthusiasm: 80,
      patience: 70,
      detail: 75,
      communication: 85
    },
    defaultSkills: {
      core: {
        copywriting: { level: 90, name: '文案撰写' },
        brand_strategy: { level: 75, name: '品牌策略' },
        content_creation: { level: 85, name: '内容创作' }
      },
      tools: ['search', 'document'],
      knowledgeDomains: ['广告文案', '品牌故事', '内容营销', '用户心理']
    }
  },

  data_analyst: {
    name: '数据分析师',
    position: '数据分析师',
    personalityTemplate: 'rational',
    speakingStyle: 'professional',
    defaultTraits: {
      creativity: 55,
      logic: 95,
      enthusiasm: 60,
      patience: 85,
      detail: 90,
      communication: 70
    },
    defaultSkills: {
      core: {
        data_analysis: { level: 90, name: '数据分析' },
        visualization: { level: 80, name: '数据可视化' },
        reporting: { level: 85, name: '报告撰写' }
      },
      tools: ['search', 'document', 'analysis'],
      knowledgeDomains: ['统计分析', 'SQL', 'Python', '商业智能']
    }
  }
};

// 头像渐变色池
export const AVATAR_GRADIENTS = [
  ['#FF6B9D', '#C44FE2'],  // 粉紫
  ['#4FACFE', '#00F2FE'],  // 蓝青
  ['#FA709A', '#FEE140'],  // 粉黄
  ['#A18CD1', '#FBC2EB'],  // 淡紫
  ['#667EEA', '#764BA2'],  // 靛紫
  ['#F093FB', '#F5576C'],  // 紫红
  ['#43E97B', '#38F9D7'],  // 绿青
  ['#A9C9FF', '#FFBBEC'],  // 淡蓝粉
  ['#FCCB90', '#D57EEB'],  // 橙紫
  ['#E0C3FC', '#8EC5FC'],  // 淡紫蓝
  ['#FF9A9E', '#FECFEF'],  // 粉红
  ['#667EEA', '#764BA2'],  // 蓝紫
];

/**
 * 员工数据存储类
 */
class EmployeeStore {
  constructor() {
    this.employees = this.load();
    this.gradientIndex = this.loadGradientIndex();
  }

  // ============ 持久化 ============

  load() {
    return localStore.get('employees', []);
  }

  save() {
    localStore.set('employees', this.employees);
  }

  loadGradientIndex() {
    return localStore.get('gradient_index', 0);
  }

  saveGradientIndex() {
    localStore.set('gradient_index', this.gradientIndex);
  }

  // ============ CRUD ============

  /**
   * 添加员工
   */
  add(employee) {
    // 自动分配 ID
    if (!employee.id) {
      employee.id = this._generateId();
    }

    // 自动分配头像渐变色
    if (!employee.gradient) {
      employee.gradient = this._getNextGradient();
    }

    // 自动分配首字母
    if (!employee.initial) {
      employee.initial = employee.name.charAt(0).toUpperCase();
    }

    // 初始化统计数据
    if (!employee.stats) {
      employee.stats = {
        tasksCompleted: 0,
        messagesSent: 0,
        hoursWorked: 0,
        createdAt: new Date().toISOString(),
        lastActiveAt: new Date().toISOString()
      };
    }

    // 初始化状态
    if (!employee.status) {
      employee.status = {
        online: true,
        currentTask: null,
        workload: 0
      };
    }

    this.employees.push(employee);
    this.save();
    return employee;
  }

  /**
   * 删除员工
   */
  remove(id) {
    this.employees = this.employees.filter(e => e.id !== id);
    this.save();
  }

  /**
   * 更新员工
   */
  update(id, updates) {
    const emp = this.employees.find(e => e.id === id);
    if (emp) {
      Object.assign(emp, updates);
      emp.stats.lastActiveAt = new Date().toISOString();
      this.save();
    }
    return emp;
  }

  /**
   * 获取所有员工
   */
  getAll() {
    return this.employees;
  }

  /**
   * 根据 ID 获取员工
   */
  getById(id) {
    return this.employees.find(e => e.id === id);
  }

  /**
   * 根据模板创建员工
   */
  createFromTemplate(templateId, overrides = {}) {
    const template = EMPLOYEE_TEMPLATES[templateId];
    if (!template) {
      throw new Error(`未知模板: ${templateId}`);
    }

    const employee = {
      name: overrides.name || template.name,
      position: overrides.position || template.position,
      personalityTemplate: template.personalityTemplate,
      speakingStyle: template.speakingStyle,
      traits: { ...template.defaultTraits, ...overrides.traits },
      skills: {
        core: { ...template.defaultSkills.core, ...overrides.skills?.core },
        tools: overrides.skills?.tools || template.defaultSkills.tools,
        knowledgeDomains: overrides.skills?.knowledgeDomains || template.defaultSkills.knowledgeDomains
      },
      workPreferences: {
        mode: 'proactive',
        responseSpeed: 'balanced',
        workingHours: ['weekday']
      },
      ...overrides
    };

    return this.add(employee);
  }

  // ============ 工具方法 ============

  _generateId() {
    const timestamp = Date.now().toString(36);
    const random = Math.random().toString(36).substring(2, 6);
    return `emp_${timestamp}_${random}`;
  }

  _getNextGradient() {
    const gradient = AVATAR_GRADIENTS[this.gradientIndex % AVATAR_GRADIENTS.length];
    this.gradientIndex++;
    this.saveGradientIndex();
    return `linear-gradient(135deg, ${gradient[0]} 0%, ${gradient[1]} 100%)`;
  }

  /**
   * 导出所有员工数据
   */
  exportAll() {
    return {
      version: '1.0',
      exportedAt: new Date().toISOString(),
      employees: this.employees
    };
  }

  /**
   * 导入员工数据
   */
  importAll(data, mode = 'merge') {
    if (mode === 'overwrite') {
      this.employees = data.employees || [];
    } else {
      // 合并：跳过已存在的 ID
      const existingIds = new Set(this.employees.map(e => e.id));
      const newEmployees = (data.employees || []).filter(e => !existingIds.has(e.id));
      this.employees.push(...newEmployees);
    }
    this.save();
  }

  /**
   * 获取模板列表
   */
  getTemplates() {
    return Object.entries(EMPLOYEE_TEMPLATES).map(([id, template]) => ({
      id,
      name: template.name,
      position: template.position,
      personality: template.personalityTemplate,
      style: template.speakingStyle
    }));
  }
}

// 单例
const employeeStore = new EmployeeStore();
export default employeeStore;