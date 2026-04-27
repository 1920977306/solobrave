/**
 * Solo Brave - 数据存储模块
 * 统一管理 localStorage 读写
 * 前缀 sb_，以后换后端只需修改这个文件
 */

const Store = {
  // 前缀
  PREFIX: 'sb_',
  
  // 获取数据
  get(key) {
    try {
      const data = localStorage.getItem(this.PREFIX + key);
      return data ? JSON.parse(data) : null;
    } catch (e) {
      console.error('[Store] Failed to parse data for key:', key, e);
      // 尝试返回 null 或清理损坏的数据
      this.remove(key);
      return null;
    }
  },
  
  // 设置数据
  set(key, value) {
    try {
      localStorage.setItem(this.PREFIX + key, JSON.stringify(value));
      return true;
    } catch (e) {
      console.error('Store.set error:', e);
      return false;
    }
  },
  
  // 删除数据
  remove(key) {
    localStorage.removeItem(this.PREFIX + key);
  },
  
  // ===== ID 生成 =====
  generateId(prefix) {
    return prefix + '_' + Date.now() + '_' + Math.random().toString(36).substring(2, 6);
  },
  
  // ===== 员工相关 =====
  getEmployees() {
    return this.get('employees') || [];
  },
  
  setEmployees(employees) {
    return this.set('employees', employees);
  },
  
  addEmployee(employee) {
    const employees = this.getEmployees();
    employee.id = this.generateId('emp');
    employees.push(employee);
    return this.setEmployees(employees);
  },
  
  removeEmployee(id) {
    const employees = this.getEmployees().filter(e => e.id !== id);
    return this.setEmployees(employees);
  },
  
  // ===== 项目组相关 =====
  getProjects() {
    return this.get('projects') || [];
  },
  
  setProjects(projects) {
    return this.set('projects', projects);
  },
  
  createProject(name, ownerId, memberIds) {
    const projects = this.getProjects();
    const newProject = {
      id: this.generateId('proj'),
      name,
      ownerId,
      memberIds,
      createdAt: Date.now()
    };
    projects.push(newProject);
    this.setProjects(projects);
    return newProject;
  },
  
  getProject(id) {
    return this.getProjects().find(p => p.id === id);
  },
  
  // ===== 消息相关 =====
  getMessages(projectId) {
    const all = this.get('messages') || {};
    return all[projectId] || [];
  },
  
  addMessage(projectId, message) {
    const all = this.get('messages') || {};
    if (!all[projectId]) all[projectId] = [];
    all[projectId].push({
      ...message,
      id: this.generateId('msg'),
      timestamp: Date.now()
    });
    return this.set('messages', all);
  },
  
  // ===== 任务相关 =====
  getTasks(projectId) {
    const all = this.get('tasks') || {};
    return all[projectId] || [];
  },
  
  setTasks(projectId, tasks) {
    const all = this.get('tasks') || {};
    all[projectId] = tasks;
    return this.set('tasks', all);
  },
  
  addTask(projectId, task) {
    const tasks = this.getTasks(projectId);
    tasks.push({
      ...task,
      id: this.generateId('task'),
      status: 'todo',
      createdAt: Date.now()
    });
    return this.setTasks(projectId, tasks);
  },
  
  updateTaskStatus(projectId, taskId, status) {
    const tasks = this.getTasks(projectId);
    const task = tasks.find(t => t.id === taskId);
    if (task) {
      task.status = status;
      return this.setTasks(projectId, tasks);
    }
    return false;
  },
  
  deleteTask(projectId, taskId) {
    const tasks = this.getTasks(projectId);
    const filtered = tasks.filter(t => t.id !== taskId);
    return this.setTasks(projectId, filtered);
  },
  
  // ===== 督促设置 =====
  getUrgeSettings(projectId) {
    const all = this.get('urge_settings') || {};
    return all[projectId] || {
      enabled: false,
      interval: 5,
      trigger: 'no_message',
      message: '大家继续推进，有进展同步一下'
    };
  },
  
  setUrgeSettings(projectId, settings) {
    const all = this.get('urge_settings') || {};
    all[projectId] = settings;
    return this.set('urge_settings', all);
  },
  
  // ===== 群公告 =====
  getAnnouncement(projectId) {
    const all = this.get('announcements') || {};
    return all[projectId] || null;
  },
  
  setAnnouncement(projectId, content) {
    const all = this.get('announcements') || {};
    if (content && content.trim()) {
      all[projectId] = {
        content: content.trim(),
        updatedAt: Date.now(),
        updatedBy: 'user'
      };
    } else {
      delete all[projectId];
    }
    return this.set('announcements', all);
  },
  
  // ===== 办公室配置 =====
  getOfficeConfig() {
    return this.get('office_config') || {};
  },
  
  setOfficeConfig(config) {
    return this.set('office_config', config);
  }
};

// 初始化默认员工数据
function initDefaultData() {
  if (!Store.get('employees') || Store.getEmployees().length === 0) {
    Store.setEmployees([
      { id: 'e1', name: '小龙虾', role: '产品经理', avatar: '🦞', status: 'online', msg: '好的，我来安排', time: '刚刚' },
      { id: 'e2', name: '大龙虾', role: '工程师', avatar: '🦞', status: 'busy', msg: '代码写一半...', time: '10分钟前' },
      { id: 'e3', name: '章鱼哥', role: '设计师', avatar: '🐙', status: 'online', msg: '设计稿好了', time: '30分钟前' },
      { id: 'e4', name: '海星', role: '营销专家', avatar: '⭐', status: 'offline', msg: '周一见', time: '昨天' }
    ]);
  }
}
