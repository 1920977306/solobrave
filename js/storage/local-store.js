/**
 * LocalStorage 封装 - 带版本迁移和数据校验
 * Day 1 核心模块
 */

const STORAGE_VERSION = '1.0.0';
const STORAGE_PREFIX = 'sb_';

class LocalStore {
  constructor() {
    this.version = STORAGE_VERSION;
    this.prefix = STORAGE_PREFIX;
    this._checkVersion();
  }

  // ============ 版本迁移 ============

  _checkVersion() {
    const storedVersion = localStorage.getItem(`${this.prefix}version`);
    if (storedVersion !== this.version) {
      console.log(`[LocalStore] Version migration: ${storedVersion} -> ${this.version}`);
      this._migrate(storedVersion);
      localStorage.setItem(`${this.prefix}version`, this.version);
    }
  }

  _migrate(oldVersion) {
    // v0 -> v1.0.0: 初始版本，无需迁移
    if (!oldVersion) return;

    // 未来版本迁移在这里添加
    // if (oldVersion === '1.0.0') { ... }
  }

  // ============ 基础操作 ============

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

  has(key) {
    return localStorage.getItem(this._key(key)) !== null;
  }

  // ============ 批量操作 ============

  getAll(pattern = null) {
    const result = {};
    for (let i = 0; i < localStorage.length; i++) {
      const fullKey = localStorage.key(i);
      if (fullKey.startsWith(this.prefix)) {
        const shortKey = fullKey.slice(this.prefix.length);
        if (!pattern || shortKey.includes(pattern)) {
          try {
            result[shortKey] = JSON.parse(localStorage.getItem(fullKey));
          } catch (e) {
            result[shortKey] = localStorage.getItem(fullKey);
          }
        }
      }
    }
    return result;
  }

  setBatch(items) {
    const results = [];
    for (const [key, value] of Object.entries(items)) {
      results.push(this.set(key, value));
    }
    return results.every(Boolean);
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

  // ============ 导出/导入 ============

  exportAll() {
    const data = this.getAll();
    return {
      version: this.version,
      exportedAt: new Date().toISOString(),
      data
    };
  }

  importAll(backup, mode = 'merge') {
    if (mode === 'overwrite') {
      this.clear();
    }

    const { data } = backup;
    for (const [key, value] of Object.entries(data)) {
      if (mode === 'merge' && this.has(key)) {
        // 合并数组（去重）
        if (Array.isArray(value)) {
          const existing = this.get(key, []);
          const merged = [...existing];
          for (const item of value) {
            if (!merged.some(e => e.id === item.id)) {
              merged.push(item);
            }
          }
          this.set(key, merged);
          continue;
        }
        // 合并对象
        if (typeof value === 'object' && value !== null) {
          const existing = this.get(key, {});
          this.set(key, { ...existing, ...value });
          continue;
        }
      }
      this.set(key, value);
    }
  }

  // ============ 备份管理 ============

  createSnapshot() {
    const snapshot = this.exportAll();
    const snapshots = this.get('snapshots', []);
    snapshots.push({
      ...snapshot,
      id: `snap_${Date.now()}`,
      name: `Auto ${new Date().toLocaleString('zh-CN')}`
    });
    // 只保留最近 7 个
    if (snapshots.length > 7) {
      snapshots.shift();
    }
    this.set('snapshots', snapshots);
    return snapshot;
  }

  getSnapshots() {
    return this.get('snapshots', []);
  }

  restoreSnapshot(snapshotId) {
    const snapshots = this.get('snapshots', []);
    const snapshot = snapshots.find(s => s.id === snapshotId);
    if (snapshot) {
      this.importAll(snapshot, 'overwrite');
      return true;
    }
    return false;
  }

  // ============ 空间管理 ============

  getSize() {
    let size = 0;
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key.startsWith(this.prefix)) {
        size += localStorage.getItem(key).length * 2; // UTF-16
      }
    }
    return {
      bytes: size,
      kb: (size / 1024).toFixed(2),
      mb: (size / 1024 / 1024).toFixed(2)
    };
  }

  cleanup() {
    // 清理过期的临时数据
    const tempKeys = ['temp_', 'cache_', 'draft_'];
    for (let i = localStorage.length - 1; i >= 0; i--) {
      const key = localStorage.key(i);
      if (key.startsWith(this.prefix)) {
        const shortKey = key.slice(this.prefix.length);
        if (tempKeys.some(prefix => shortKey.startsWith(prefix))) {
          localStorage.removeItem(key);
        }
      }
    }
  }
}

// 单例
const localStore = new LocalStore();
export default localStore;
