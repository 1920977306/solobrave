/**
 * SoloBrave Store - 状态管理（发布订阅模式）
 * Day 1 核心模块
 */

class Store {
  constructor() {
    // 核心状态
    this.state = {
      // 员工列表
      employees: [],
      // 当前选中的员工
      currentEmployee: null,
      // 当前对话
      currentConversation: null,
      // 对话列表（按员工隔离）
      conversations: {},
      // 任务列表
      tasks: [],
      // 当前视图
      currentView: 'office', // office | recruit | task | chat | knowledge | settings
      // 系统配置
      config: {
        // API 配置
        api: {
          provider: 'openai', // openai | claude | local
          key: '',
          model: 'gpt-4o-mini',
          baseUrl: '',
          temperature: 0.7
        },
        // UI 配置
        ui: {
          theme: 'light',
          sidebarCollapsed: false,
          language: 'zh-CN'
        }
      },
      // UI 状态
      ui: {
        isLoading: false,
        toast: null,
        modal: null
      }
    };

    // 监听器
    this.listeners = new Map();
    this.listenerId = 0;

    // 初始化：从 storage 加载
    this._loadFromStorage();
  }

  // ============ 订阅/发布 ============

  /**
   * 订阅状态变化
   * @param {string} path - 状态路径，如 'employees', 'config.api'
   * @param {Function} callback - 回调函数
   * @returns {number} 订阅 ID，用于取消订阅
   */
  subscribe(path, callback) {
    const id = ++this.listenerId;
    if (!this.listeners.has(path)) {
      this.listeners.set(path, new Map());
    }
    this.listeners.get(path).set(id, callback);
    return id;
  }

  /**
   * 取消订阅
   */
  unsubscribe(id) {
    for (const [, callbacks] of this.listeners) {
      if (callbacks.has(id)) {
        callbacks.delete(id);
        return true;
      }
    }
    return false;
  }

  /**
   * 通知监听器
   */
  _notify(path, value) {
    // 通知精确匹配的监听器
    if (this.listeners.has(path)) {
      for (const [, callback] of this.listeners.get(path)) {
        try {
          callback(value, path);
        } catch (e) {
          console.error('Store listener error:', e);
        }
      }
    }

    // 通知通配符监听器（监听所有变化）
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

  // ============ Getter ============

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

  // ============ Setter ============

  /**
   * 设置状态（自动通知 + 持久化）
   */
  set(path, value) {
    const oldValue = this.get(path);
    this._setPath(this.state, path, value);
    this._notify(path, value);

    // 自动持久化关键路径
    if (this._shouldPersist(path)) {
      this._persist(path, value);
    }

    return value;
  }

  _setPath(obj, path, value) {
    const keys = path.split('.');
    let current = obj;
    for (let i = 0; i < keys.length - 1; i++) {
      const key = keys[i];
      if (!(key in current) || typeof current[key] !== 'object') {
        current[key] = {};
      }
      current = current[key];
    }
    current[keys[keys.length - 1]] = value;
  }

  // ============ 批量更新 ============

  /**
   * 批量更新多个状态
   */
  batch(updates) {
    const persisted = [];
    for (const [path, value] of Object.entries(updates)) {
      this._setPath(this.state, path, value);
      this._notify(path, value);
      if (this._shouldPersist(path)) {
        persisted.push([path, value]);
      }
    }
    // 批量持久化
    if (persisted.length > 0) {
      this._persistBatch(persisted);
    }
  }

  // ============ 持久化 ============

  _shouldPersist(path) {
    const persistPaths = ['employees', 'conversations', 'tasks', 'config'];
    return persistPaths.some(p => path === p || path.startsWith(p + '.'));
  }

  _persist(path, value) {
    try {
      const key = `sb_${path.replace(/\./g, '_')}`;
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {
      console.warn('Store persist failed:', e);
    }
  }

  _persistBatch(items) {
    try {
      for (const [path, value] of items) {
        const key = `sb_${path.replace(/\./g, '_')}`;
        localStorage.setItem(key, JSON.stringify(value));
      }
    } catch (e) {
      console.warn('Store batch persist failed:', e);
    }
  }

  _loadFromStorage() {
    const mappings = {
      'sb_employees': 'employees',
      'sb_conversations': 'conversations',
      'sb_tasks': 'tasks',
      'sb_config': 'config'
    };

    for (const [storageKey, statePath] of Object.entries(mappings)) {
      try {
        const raw = localStorage.getItem(storageKey);
        if (raw) {
          const value = JSON.parse(raw);
          this._setPath(this.state, statePath, value);
        }
      } catch (e) {
        console.warn(`Store load failed for ${statePath}:`, e);
      }
    }
  }

  // ============ 便捷方法 ============

  /**
   * 添加员工
   */
  addEmployee(employee) {
    const employees = [...this.state.employees, employee];
    this.set('employees', employees);
    return employee;
  }

  /**
   * 更新员工
   */
  updateEmployee(id, updates) {
    const employees = this.state.employees.map(emp =>
      emp.id === id ? { ...emp, ...updates } : emp
    );
    this.set('employees', employees);
  }

  /**
   * 删除员工
   */
  removeEmployee(id) {
    const employees = this.state.employees.filter(emp => emp.id !== id);
    this.set('employees', employees);
  }

  /**
   * 设置当前员工
   */
  setCurrentEmployee(id) {
    const emp = this.state.employees.find(e => e.id === id) || null;
    this.set('currentEmployee', emp);
    return emp;
  }

  /**
   * 获取或创建员工的对话
   */
  getOrCreateConversation(employeeId) {
    const conversations = this.state.conversations;
    if (!conversations[employeeId]) {
      conversations[employeeId] = {
        employeeId,
        messages: [],
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };
      this.set('conversations', conversations);
    }
    return conversations[employeeId];
  }

  /**
   * 添加消息到员工对话
   */
  addMessage(employeeId, message) {
    const conversations = { ...this.state.conversations };
    if (!conversations[employeeId]) {
      conversations[employeeId] = {
        employeeId,
        messages: [],
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      };
    }
    conversations[employeeId] = {
      ...conversations[employeeId],
      messages: [...conversations[employeeId].messages, message],
      updatedAt: new Date().toISOString()
    };
    this.set('conversations', conversations);
  }

  /**
   * 获取员工对话历史
   */
  getConversation(employeeId) {
    return this.state.conversations[employeeId] || null;
  }

  /**
   * 切换视图
   */
  setView(view) {
    this.set('currentView', view);
  }

  /**
   * 显示 Toast
   */
  showToast(message, type = 'info', duration = 3000) {
    this.set('ui.toast', { message, type, duration });
    setTimeout(() => {
      this.set('ui.toast', null);
    }, duration);
  }

  /**
   * 设置加载状态
   */
  setLoading(isLoading) {
    this.set('ui.isLoading', isLoading);
  }
}

// 单例导出
const store = new Store();
export default store;