/**
 * SoloBrave Store - 状态管理（普通脚本版本）
 * 整合: Store + EmployeeStore + TaskStore + LocalStore
 */

(function() {
  const ns = window.soloBrave = window.soloBrave || {};

  // ============ LocalStore ============
  const STORAGE_VERSION = '1.0.0';
  const STORAGE_PREFIX = 'sb_';

  class LocalStore {
    constructor() {
      this.version = STORAGE_VERSION;
      this.prefix = STORAGE_PREFIX;
      this._checkVersion();
    }

    _checkVersion() {
      const storedVersion = localStorage.getItem(`${this.prefix}version`);
      if (storedVersion !== this.version) {
        console.log(`[LocalStore] Version migration: ${storedVersion} -> ${this.version}`);
        this._migrate(storedVersion);
        localStorage.setItem(`${this.prefix}version`, this.version);
      }
    }

    _migrate(oldVersion) {
      if (!oldVersion) return;
    }

    _key(key) {
      return `${this.prefix}${key}`;
    }

    get(key, defaultValue = null) {
      try {
        const raw = localStorage.getItem(this._key(key));
        if (raw === null) return defaultValue;
        return JSON.parse(raw);
      } catch (e) {
        console.warn(`[LocalStore] get(${key}) failed:`, e);
        return defaultValue;
      }
    }

    set(key, value) {
      try {
        localStorage.setItem(this._key(key), JSON.stringify(value));
        return true;
      } catch (e) {
        console.warn(`[LocalStore] set(${key}) failed:`, e);
        return false;
      }
    }

    remove(key) {
      localStorage.removeItem(this._key(key));
    }

    clear() {
      const keysToRemove = [];
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith(this.prefix)) {
          keysToRemove.push(key);
        }
      }
      keysToRemove.forEach(key => localStorage.removeItem(key));
    }
  }

  const localStore = new LocalStore();

  // ============ Store (状态管理) ============
  class Store {
    constructor() {
      this.state = {
        employees: [],
        currentEmployee: null,
        currentConversation: null,
        conversations: {},
        tasks: [],
        currentView: 'office',
        config: {
          api: {
            provider: 'openai',
            key: '',
            model: 'gpt-4o-mini',
            baseUrl: '',
            temperature: 0.7
          },
          ui: {
            theme: 'light',
            sidebarCollapsed: false,
            language: 'zh-CN'
          }
        },
        ui: {
          isLoading: false,
          toast: null,
          modal: null
        }
      };

      this.listeners = new Map();
      this.listenerId = 0;
      this._loadFromStorage();
    }

    subscribe(path, callback) {
      const id = ++this.listenerId;
      if (!this.listeners.has(path)) {
        this.listeners.set(path, new Map());
      }
      this.listeners.get(path).set(id, callback);
      return id;
    }

    unsubscribe(id) {
      for (const [, callbacks] of this.listeners) {
        if (callbacks.has(id)) {
          callbacks.delete(id);
          return true;
        }
      }
      return false;
    }

    _notify(path, value) {
      if (this.listeners.has(path)) {
        for (const [, callback] of this.listeners.get(path)) {
          try {
            callback(value, path);
          } catch (e) {
            console.error('Store listener error:', e);
          }
        }
      }
      if (this.listeners.has('*')) {
        for (const [, callback] of this.listeners.get('*')) {
          try {
            callback(value, path);
          } catch (e) {
            console.error('Store wildcard listener error:', e);
          }
        }
      }
    }

    get(path) {
      return this._getPath(this.state, path);
    }

    _getPath(obj, path) {
      const keys = path.split('.');
      let current = obj;
      for (const key of keys) {
        if (current === null || current === undefined) return undefined;
        current = current[key];
      }
      return current;
    }

    set(path, value) {
      const keys = path.split('.');
      let current = this.state;
      for (let i = 0; i < keys.length - 1; i++) {
        const key = keys[i];
        if (!(key in current)) {
          current[key] = {};
        }
        current = current[key];
      }
      current[keys[keys.length - 1]] = value;
      this._notify(path, value);
      this._persistIfNeeded(path, value);
      return value;
    }

    _persistIfNeeded(path, value) {
      const persistPaths = ['employees', 'currentEmployee', 'conversations', 'tasks', 'config'];
      if (persistPaths.includes(path)) {
        localStore.set(path, value);
      }
    }

    _loadFromStorage() {
      const persistPaths = ['employees', 'currentEmployee', 'conversations', 'tasks', 'config'];
      for (const path of persistPaths) {
        const value = localStore.get(path);
        if (value !== null) {
          this._setPath(this.state, path, value);
        }
      }
    }

    _setPath(obj, path, value) {
      const keys = path.split('.');
      let current = obj;
      for (let i = 0; i < keys.length - 1; i++) {
        const key = keys[i];
        if (!(key in current)) {
          current[key] = {};
        }
        current = current[key];
      }
      current[keys[keys.length - 1]] = value;
    }

    setView(view) {
      this.set('currentView', view);
      this._notify('sb:viewchange', { view });
    }

    setCurrentEmployee(employeeId) {
      const employees = this.get('employees') || [];
      const employee = employees.find(e => e.id === employeeId);
      if (employee) {
        this.set('currentEmployee', employee);
      }
      return employee;
    }

    addMessage(employeeId, message) {
      const conversations = this.get('conversations') || {};
      if (!conversations[employeeId]) {
        conversations[employeeId] = {
          messages: [],
          updatedAt: new Date().toISOString()
        };
      }
      conversations[employeeId].messages.push(message);
      conversations[employeeId].updatedAt = new Date().toISOString();
      this.set('conversations', conversations);
      return conversations[employeeId];
    }
  }

  // ============ EmployeeStore ============
  const PERSONALITY_TEMPLATES = {
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

  const SPEAKING_STYLES = {
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

  const EMPLOYEE_TEMPLATES = {
    product_manager: {
      name: '产品经理',
      position: '产品经理',
      personalityTemplate: 'creative',
      speakingStyle: 'friendly',
      defaultTraits: {
        creativity: 80, logic: 60, enthusiasm: 75, patience: 70, detail: 55, communication: 85
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
      personalityTemplate: 'rigorous',
      speakingStyle: 'formal',
      defaultTraits: {
        creativity: 60, logic: 95, enthusiasm: 50, patience: 80, detail: 90, communication: 70
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
        creativity: 95, logic: 50, enthusiasm: 85, patience: 75, detail: 80, communication: 75
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
        creativity: 65, logic: 85, enthusiasm: 70, patience: 75, detail: 85, communication: 60
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
        creativity: 75, logic: 55, enthusiasm: 85, patience: 70, detail: 60, communication: 80
      },
      defaultSkills: {
        core: {
          content_planning: { level: 80, name: '内容策划' },
          data_analysis: { level: 65, name: '数据分析' },
          user_operation: { level: 75, name: '用户运营' }
        },
        tools: ['search', 'document', 'analysis'],
        knowledgeDomains: ['内容运营', '用户增长', '活动策划', '社群运营']
      }
    },
    copywriter: {
      name: '文案写手',
      position: '文案策划',
      personalityTemplate: 'creative',
      speakingStyle: 'humorous',
      defaultTraits: {
        creativity: 90, logic: 50, enthusiasm: 80, patience: 65, detail: 70, communication: 85
      },
      defaultSkills: {
        core: {
          copywriting: { level: 90, name: '文案撰写' },
          brand_strategy: { level: 70, name: '品牌策略' },
          content_creation: { level: 85, name: '内容创作' }
        },
        tools: ['search', 'document'],
        knowledgeDomains: ['品牌文案', '新媒体', '广告创意', '故事叙述']
      }
    },
    data_analyst: {
      name: '数据分析师',
      position: '数据分析师',
      personalityTemplate: 'rigorous',
      speakingStyle: 'formal',
      defaultTraits: {
        creativity: 50, logic: 95, enthusiasm: 55, patience: 85, detail: 90, communication: 65
      },
      defaultSkills: {
        core: {
          data_analysis: { level: 90, name: '数据分析' },
          visualization: { level: 80, name: '数据可视化' },
          reporting: { level: 75, name: '报告撰写' }
        },
        tools: ['search', 'document', 'analysis'],
        knowledgeDomains: ['统计学', 'SQL', 'Python', '商业分析']
      }
    }
  };

  function generateId(prefix) {
    const timestamp = Date.now().toString(36);
    const random = Math.random().toString(36).substring(2, 6);
    return `${prefix}_${timestamp}_${random}`;
  }

  function generateGradient() {
    const colors = [
      ['#667eea', '#764ba2'],
      ['#f093fb', '#f5576c'],
      ['#4facfe', '#00f2fe'],
      ['#43e97b', '#38f9d7'],
      ['#fa709a', '#fee140'],
      ['#30cfd0', '#330867'],
      ['#a8edea', '#fed6e3'],
      ['#ff9a9e', '#fecfef'],
      ['#fbc2eb', '#a6c1ee'],
      ['#fdcbf1', '#e6dee9']
    ];
    const [c1, c2] = colors[Math.floor(Math.random() * colors.length)];
    return `linear-gradient(135deg, ${c1} 0%, ${c2} 100%)`;
  }

  function generateSystemPrompt(employee) {
    const template = PERSONALITY_TEMPLATES[employee.soul?.template || 'creative'];
    const style = SPEAKING_STYLES[employee.soul?.speakingStyle || 'friendly'];
    const skills = Object.values(employee.skills?.core || {})
      .map(s => `${s.name}(${s.level}分)`)
      .join('、');

    return `你是${employee.name}，${employee.position}。

${template.basePrompt}
${style.prompt}

你的技能：${skills || '暂无'}

请始终保持这个角色设定，用第一人称回复。`;
  }

  class EmployeeStore {
    constructor() {
      this.employees = this._load();
    }

    _load() {
      return localStore.get('employees', []);
    }

    _save() {
      localStore.set('employees', this.employees);
    }

    getAll() {
      return this.employees;
    }

    getById(id) {
      return this.employees.find(e => e.id === id);
    }

    create(config) {
      const template = PERSONALITY_TEMPLATES[config.personalityTemplate || 'creative'];
      const style = SPEAKING_STYLES[config.speakingStyle || 'friendly'];
      const now = new Date().toISOString();

      const employee = {
        id: generateId('emp'),
        name: config.name || '未命名员工',
        position: config.position || '助理',
        avatar: config.avatar || null,
        initial: (config.name || '?').charAt(0).toUpperCase(),
        gradient: config.gradient || generateGradient(),
        soul: {
          template: config.personalityTemplate || 'creative',
          templateName: template.name,
          speakingStyle: config.speakingStyle || 'friendly',
          speakingStyleName: style.name,
          traits: config.traits || {}
        },
        skills: config.skills || {},
        createdAt: now,
        updatedAt: now
      };

      this.employees.push(employee);
      this._save();
      return employee;
    }

    createFromTemplate(templateKey, overrides = {}) {
      const template = EMPLOYEE_TEMPLATES[templateKey];
      if (!template) {
        throw new Error(`Unknown template: ${templateKey}`);
      }

      const config = {
        name: overrides.name || template.name,
        position: overrides.position || template.position,
        personalityTemplate: overrides.personalityTemplate || template.personalityTemplate,
        speakingStyle: overrides.speakingStyle || template.speakingStyle,
        traits: { ...template.defaultTraits, ...overrides.traits },
        skills: { ...template.defaultSkills, ...overrides.skills }
      };

      return this.create(config);
    }

    remove(id) {
      this.employees = this.employees.filter(e => e.id !== id);
      this._save();
    }

    update(id, updates) {
      const employee = this.employees.find(e => e.id === id);
      if (employee) {
        Object.assign(employee, updates, { updatedAt: new Date().toISOString() });
        this._save();
      }
      return employee;
    }

    getSystemPrompt(employeeId) {
      const employee = this.getById(employeeId);
      if (!employee) return null;
      return generateSystemPrompt(employee);
    }
  }

  // ============ TaskStore ============
  const TASK_STATUS = {
    PENDING: 'pending',
    IN_PROGRESS: 'in_progress',
    REVIEW: 'review',
    DONE: 'done',
    CANCELLED: 'cancelled'
  };

  const TASK_PRIORITY = {
    P1: 'P1',
    P2: 'P2',
    P3: 'P3'
  };

  class TaskStore {
    constructor() {
      this.tasks = this._load();
    }

    _load() {
      return localStore.get('tasks', []);
    }

    _save() {
      localStore.set('tasks', this.tasks);
    }

    add(task) {
      if (!task.id) {
        task.id = generateId('task');
      }
      task.createdAt = task.createdAt || new Date().toISOString();
      task.updatedAt = new Date().toISOString();
      this.tasks.unshift(task);
      this._save();
      return task;
    }

    remove(id) {
      this.tasks = this.tasks.filter(t => t.id !== id);
      this._save();
    }

    update(id, updates) {
      const task = this.tasks.find(t => t.id === id);
      if (task) {
        Object.assign(task, updates, { updatedAt: new Date().toISOString() });
        this._save();
      }
      return task;
    }

    getById(id) {
      return this.tasks.find(t => t.id === id);
    }

    getAll() {
      return this.tasks;
    }

    getByAssignee(assigneeId) {
      return this.tasks.filter(t => t.assigneeId === assigneeId);
    }

    getByStatus(status) {
      return this.tasks.filter(t => t.status === status);
    }

    updateStatus(id, newStatus) {
      const task = this.tasks.find(t => t.id === id);
      if (!task) return null;

      const oldStatus = task.status;
      task.status = newStatus;
      task.updatedAt = new Date().toISOString();

      if (!task.logs) task.logs = [];
      task.logs.push({
        action: `status_${oldStatus}_to_${newStatus}`,
        time: new Date().toISOString(),
        by: 'user'
      });

      this._save();
      return task;
    }

    getStats() {
      return {
        total: this.tasks.length,
        pending: this.getByStatus(TASK_STATUS.PENDING).length,
        inProgress: this.getByStatus(TASK_STATUS.IN_PROGRESS).length,
        review: this.getByStatus(TASK_STATUS.REVIEW).length,
        done: this.getByStatus(TASK_STATUS.DONE).length,
        cancelled: this.getByStatus(TASK_STATUS.CANCELLED).length
      };
    }

    dispatch(id) {
      const task = this.tasks.find(t => t.id === id);
      if (!task) return null;

      task.status = TASK_STATUS.IN_PROGRESS;
      task.dispatchedAt = new Date().toISOString();
      task.updatedAt = new Date().toISOString();

      if (!task.logs) task.logs = [];
      task.logs.push({
        action: 'dispatched',
        time: new Date().toISOString(),
        by: 'user'
      });

      this._save();
      return task;
    }
  }

  // ============ 记忆管理 ============
  class MemoryStore {
    constructor() {
      this.memories = this._load();
    }

    _load() {
      return localStore.get('memories', {});
    }

    _save() {
      localStore.set('memories', this.memories);
    }

    getByEmployee(employeeId) {
      return this.memories[employeeId] || [];
    }

    add(employeeId, memory) {
      if (!this.memories[employeeId]) {
        this.memories[employeeId] = [];
      }

      const newMemory = {
        id: generateId('mem'),
        type: memory.type || 'general',
        content: memory.content,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };

      this.memories[employeeId].push(newMemory);
      this._save();
      return newMemory;
    }

    update(employeeId, memoryId, updates) {
      const memories = this.memories[employeeId] || [];
      const memory = memories.find(m => m.id === memoryId);
      if (memory) {
        Object.assign(memory, updates, { updatedAt: new Date().toISOString() });
        this._save();
      }
      return memory;
    }

    remove(employeeId, memoryId) {
      if (this.memories[employeeId]) {
        this.memories[employeeId] = this.memories[employeeId].filter(m => m.id !== memoryId);
        this._save();
      }
    }

    clear(employeeId) {
      delete this.memories[employeeId];
      this._save();
    }

    // 获取格式化的记忆文本（用于注入 Prompt）
    getPromptText(employeeId) {
      const memories = this.getByEmployee(employeeId);
      if (memories.length === 0) return '';

      const lines = memories.map(m => `  - ${m.content}`);
      return `\n\n【你了解的关于用户的信息】\n${lines.join('\n')}`;
    }
  }

  // ============ KnowledgeStore ============
  class KnowledgeStore {
    constructor() {
      this.dbName = 'SoloBraveKnowledge';
      this.dbVersion = 1;
      this.db = null;
      this._initDB();
    }

    async _initDB() {
      return new Promise((resolve, reject) => {
        const request = indexedDB.open(this.dbName, this.dbVersion);
        
        request.onerror = () => reject(request.error);
        request.onsuccess = () => {
          this.db = request.result;
          resolve(this.db);
        };
        
        request.onupgradeneeded = (event) => {
          const db = event.target.result;
          
          // 文档存储
          if (!db.objectStoreNames.contains('documents')) {
            const docStore = db.createObjectStore('documents', { keyPath: 'id' });
            docStore.createIndex('employeeId', 'employeeId', { unique: false });
            docStore.createIndex('name', 'name', { unique: false });
          }
          
          // 片段存储
          if (!db.objectStoreNames.contains('chunks')) {
            const chunkStore = db.createObjectStore('chunks', { keyPath: 'id' });
            chunkStore.createIndex('docId', 'docId', { unique: false });
            chunkStore.createIndex('employeeId', 'employeeId', { unique: false });
          }
        };
      });
    }

    async _ensureDB() {
      if (!this.db) {
        await this._initDB();
      }
      return this.db;
    }

    // 添加文档（自动分片）
    async addDocument(employeeId, file) {
      const db = await this._ensureDB();
      
      // 读取文件内容
      const content = await this._readFile(file);
      
      // 创建文档记录
      const docId = ns.generateId('doc');
      const doc = {
        id: docId,
        employeeId: employeeId,
        name: file.name,
        type: file.type || 'text/plain',
        size: file.size,
        createdAt: new Date().toISOString(),
        chunkCount: 0
      };

      // 分片（简单按段落分割，每片最多 500 字）
      const chunks = this._splitIntoChunks(content, 500);
      doc.chunkCount = chunks.length;

      // 保存文档
      await new Promise((resolve, reject) => {
        const tx = db.transaction('documents', 'readwrite');
        const store = tx.objectStore('documents');
        const request = store.put(doc);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
      });

      // 保存片段
      const chunkRecords = chunks.map((chunk, index) => ({
        id: ns.generateId('chunk'),
        docId: docId,
        employeeId: employeeId,
        content: chunk,
        index: index,
        createdAt: new Date().toISOString()
      }));

      await new Promise((resolve, reject) => {
        const tx = db.transaction('chunks', 'readwrite');
        const store = tx.objectStore('chunks');
        let count = 0;
        chunkRecords.forEach(chunk => {
          const request = store.put(chunk);
          request.onsuccess = () => {
            count++;
            if (count === chunkRecords.length) resolve();
          };
          request.onerror = () => reject(request.error);
        });
      });

      return { ...doc, chunks: chunkRecords };
    }

    // 读取文件内容
    _readFile(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = (e) => resolve(e.target.result);
        reader.onerror = (e) => reject(e);
        reader.readAsText(file);
      });
    }

    // 分片
    _splitIntoChunks(text, maxLength) {
      const chunks = [];
      const paragraphs = text.split(/\n\s*\n/);
      let currentChunk = '';

      for (const para of paragraphs) {
        if (currentChunk.length + para.length > maxLength) {
          if (currentChunk) chunks.push(currentChunk.trim());
          currentChunk = para;
        } else {
          currentChunk += '\n\n' + para;
        }
      }
      if (currentChunk) chunks.push(currentChunk.trim());
      
      return chunks.length > 0 ? chunks : [text];
    }

    // 关键词检索
    async search(employeeId, query, limit = 3) {
      const db = await this._ensureDB();
      
      // 获取该员工的所有片段
      const chunks = await new Promise((resolve, reject) => {
        const tx = db.transaction('chunks', 'readonly');
        const store = tx.objectStore('chunks');
        const index = store.index('employeeId');
        const request = index.getAll(employeeId);
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error);
      });

      if (chunks.length === 0) return [];

      // 简单关键词匹配（后续可升级为向量检索）
      const queryWords = query.toLowerCase().split(/\s+/).filter(w => w.length > 1);
      
      const scored = chunks.map(chunk => {
        const content = chunk.content.toLowerCase();
        let score = 0;
        
        queryWords.forEach(word => {
          // 整词匹配加分更多
          if (content.includes(word)) {
            score += 1;
            // 如果出现在开头，额外加分
            if (content.indexOf(word) < 50) score += 0.5;
          }
        });

        return { ...chunk, score };
      });

      // 按分数排序，取前 N 个
      const results = scored
        .filter(c => c.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, limit);

      // 获取文档名称
      for (const result of results) {
        const doc = await new Promise((resolve) => {
          const tx = db.transaction('documents', 'readonly');
          const store = tx.objectStore('documents');
          const request = store.get(result.docId);
          request.onsuccess = () => resolve(request.result);
        });
        result.doc_name = doc ? doc.name : '未知文档';
      }

      return results;
    }

    // 获取员工的所有文档
    async getDocuments(employeeId) {
      const db = await this._ensureDB();
      
      return new Promise((resolve, reject) => {
        const tx = db.transaction('documents', 'readonly');
        const store = tx.objectStore('documents');
        const index = store.index('employeeId');
        const request = index.getAll(employeeId);
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error);
      });
    }

    // 删除文档（同时删除片段）
    async removeDocument(docId) {
      const db = await this._ensureDB();
      
      // 删除文档
      await new Promise((resolve, reject) => {
        const tx = db.transaction('documents', 'readwrite');
        const store = tx.objectStore('documents');
        const request = store.delete(docId);
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
      });

      // 删除相关片段
      const chunks = await new Promise((resolve, reject) => {
        const tx = db.transaction('chunks', 'readonly');
        const store = tx.objectStore('chunks');
        const index = store.index('docId');
        const request = index.getAll(docId);
        request.onsuccess = () => resolve(request.result || []);
        request.onerror = () => reject(request.error);
      });

      await new Promise((resolve, reject) => {
        const tx = db.transaction('chunks', 'readwrite');
        const store = tx.objectStore('chunks');
        let count = 0;
        chunks.forEach(chunk => {
          const request = store.delete(chunk.id);
          request.onsuccess = () => {
            count++;
            if (count === chunks.length) resolve();
          };
          request.onerror = () => reject(request.error);
        });
        if (chunks.length === 0) resolve();
      });
    }

    // 获取知识库统计
    async getStats(employeeId) {
      const docs = await this.getDocuments(employeeId);
      const db = await this._ensureDB();
      
      let totalChunks = 0;
      for (const doc of docs) {
        totalChunks += doc.chunkCount || 0;
      }

      return {
        documentCount: docs.length,
        chunkCount: totalChunks,
        totalSize: docs.reduce((sum, d) => sum + (d.size || 0), 0)
      };
    }
  }

  // ============ 暴露到全局 ============
  ns.store = new Store();
  ns.employeeStore = new EmployeeStore();
  ns.taskStore = new TaskStore();
  ns.memoryStore = new MemoryStore();
  ns.knowledgeStore = new KnowledgeStore();
  ns.localStore = localStore;
  ns.PERSONALITY_TEMPLATES = PERSONALITY_TEMPLATES;
  ns.SPEAKING_STYLES = SPEAKING_STYLES;
  ns.EMPLOYEE_TEMPLATES = EMPLOYEE_TEMPLATES;
  ns.TASK_STATUS = TASK_STATUS;
  ns.TASK_PRIORITY = TASK_PRIORITY;
  ns.generateId = generateId;
  ns.generateGradient = generateGradient;

})();
