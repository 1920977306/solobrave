/**
 * TaskStore - 任务数据管理
 * M1 核心模块
 */

import localStore from '../storage/local-store.js';
import { TASK_STATUS } from '../models/task.js';

class TaskStore {
  constructor() {
    this.tasks = this.load();
  }

  load() {
    return localStore.get('tasks', []);
  }

  save() {
    localStore.set('tasks', this.tasks);
  }

  // ============ CRUD ============

  add(task) {
    if (!task.id) {
      task.id = this._generateId();
    }
    this.tasks.unshift(task);
    this.save();
    return task;
  }

  remove(id) {
    this.tasks = this.tasks.filter(t => t.id !== id);
    this.save();
  }

  update(id, updates) {
    const task = this.tasks.find(t => t.id === id);
    if (task) {
      Object.assign(task, updates);
      task.updatedAt = new Date().toISOString();
      this.save();
    }
    return task;
  }

  // ============ 状态流转 ============

  updateStatus(id, newStatus) {
    const task = this.tasks.find(t => t.id === id);
    if (!task) return null;

    const oldStatus = task.status;
    task.status = newStatus;
    task.updatedAt = new Date().toISOString();
    
    // 添加操作日志
    if (!task.logs) task.logs = [];
    task.logs.push({
      action: `status_${oldStatus}_to_${newStatus}`,
      time: new Date().toISOString(),
      by: 'user'
    });

    this.save();
    return task;
  }

  startTask(id) {
    return this.updateStatus(id, TASK_STATUS.IN_PROGRESS);
  }

  submitForReview(id) {
    return this.updateStatus(id, TASK_STATUS.REVIEW);
  }

  completeTask(id) {
    return this.updateStatus(id, TASK_STATUS.DONE);
  }

  cancelTask(id) {
    return this.updateStatus(id, TASK_STATUS.CANCELLED);
  }

  // ============ 查询 ============

  getAll() {
    return this.tasks;
  }

  getById(id) {
    return this.tasks.find(t => t.id === id);
  }

  getByAssignee(assigneeId) {
    return this.tasks.filter(t => t.assigneeId === assigneeId);
  }

  getByStatus(status) {
    return this.tasks.filter(t => t.status === status);
  }

  getActive() {
    return this.tasks.filter(t => 
      t.status !== TASK_STATUS.DONE && 
      t.status !== TASK_STATUS.CANCELLED
    );
  }

  getByPriority(priority) {
    return this.tasks.filter(t => t.priority === priority);
  }

  // ============ 统计 ============

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

  getEmployeeStats(assigneeId) {
    const employeeTasks = this.getByAssignee(assigneeId);
    return {
      total: employeeTasks.length,
      completed: employeeTasks.filter(t => t.status === TASK_STATUS.DONE).length,
      inProgress: employeeTasks.filter(t => t.status === TASK_STATUS.IN_PROGRESS).length,
      pending: employeeTasks.filter(t => t.status === TASK_STATUS.PENDING).length,
      review: employeeTasks.filter(t => t.status === TASK_STATUS.REVIEW).length
    };
  }

  // ============ 推荐匹配 ============

  /**
   * 根据任务内容推荐匹配的员工
   * 简单版：按技能标签匹配
   */
  recommendAssignees(taskDescription, employees) {
    const keywords = taskDescription.toLowerCase().split(/\s+/);
    
    return employees.map(emp => {
      let score = 0;
      const empSkills = [
        ...(emp.skills?.knowledgeDomains || []),
        ...(emp.skills?.tools || [])
      ].map(s => s.toLowerCase());

      keywords.forEach(keyword => {
        empSkills.forEach(skill => {
          if (skill.includes(keyword) || keyword.includes(skill)) {
            score += 10;
          }
        });
      });

      // 考虑当前负载
      const workload = emp.status?.workload || 0;
      score -= workload * 0.5;

      return {
        employee: emp,
        score: Math.max(0, Math.round(score))
      };
    }).sort((a, b) => b.score - a.score);
  }

  _generateId() {
    const timestamp = Date.now().toString(36);
    const random = Math.random().toString(36).substring(2, 6);
    return `task_${timestamp}_${random}`;
  }
}

// 单例
const taskStore = new TaskStore();
export default taskStore;