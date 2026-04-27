/**
 * Solo Brave Framework - 配置中心
 * 
 * 统一管理所有配置
 */

var Config = (function() {
    'use strict';
    
    // ===== 默认配置 =====
    var defaults = {
        // 应用配置
        app: {
            name: 'Solo Brave',
            version: '1.0.0',
            env: 'development'
        },
        
        // API 配置
        api: {
            baseUrl: '/api',
            timeout: 30000,
            retryTimes: 3
        },
        
        // 存储配置
        storage: {
            prefix: 'sb_',
            expire: 7 * 24 * 60 * 60 * 1000  // 7天
        },
        
        // 记忆系统配置
        memory: {
            maxConversation: 100,
            maxDailyPerDay: 50,
            maxCoreMemories: 200,
            autoSummarize: true,
            summarizeInterval: 24 * 60 * 60 * 1000
        },
        
        // 工具系统配置
        tools: {
            maxHistory: 50,
            defaultTimeout: 30000
        },
        
        // 知识库配置
        knowledge: {
            vectorDim: 128,
            chunkSize: 500,
            similarityThreshold: 0.7
        },
        
        // UI 配置
        ui: {
            theme: 'dark',
            primaryColor: '#FF6B35',
            accentColor: '#4ecdc4',
            fontSize: 14,
            animation: true
        }
    };
    
    // ===== 当前配置 =====
    var config = {};
    
    // ===== 初始化 =====
    function init(options) {
        config = deepMerge({}, defaults, options || {});
        
        // 从 localStorage 恢复用户配置
        var saved = Store.get('config_user');
        if (saved) {
            config = deepMerge({}, config, saved);
        }
        
        console.log('[Config] Initialized:', config.app.name, config.app.version);
        return config;
    }
    
    // ===== 获取配置 =====
    function get(path) {
        var keys = path.split('.');
        var value = config;
        
        for (var i = 0; i < keys.length; i++) {
            if (value === undefined || value === null) return undefined;
            value = value[keys[i]];
        }
        
        return value;
    }
    
    // ===== 设置配置 =====
    function set(path, value) {
        var keys = path.split('.');
        var target = config;
        
        for (var i = 0; i < keys.length - 1; i++) {
            if (!target[keys[i]]) {
                target[keys[i]] = {};
            }
            target = target[keys[i]];
        }
        
        target[keys[keys.length - 1]] = value;
        saveUserConfig();
    }
    
    // ===== 保存用户配置 =====
    function saveUserConfig() {
        Store.set('config_user', config);
    }
    
    // ===== 重置配置 =====
    function reset() {
        config = deepMerge({}, defaults);
        Store.remove('config_user');
        console.log('[Config] Reset to defaults');
    }
    
    // ===== 深度合并 =====
    function deepMerge(target) {
        var sources = Array.prototype.slice.call(arguments, 1);
        
        sources.forEach(function(source) {
            if (source) {
                Object.keys(source).forEach(function(key) {
                    if (isPlainObject(source[key])) {
                        if (!target[key]) {
                            target[key] = {};
                        }
                        target[key] = deepMerge(target[key], source[key]);
                    } else {
                        target[key] = source[key];
                    }
                });
            }
        });
        
        return target;
    }
    
    // ===== 判断是否纯对象 =====
    function isPlainObject(obj) {
        return Object.prototype.toString.call(obj) === '[object Object]';
    }
    
    // ===== 导出 =====
    return {
        init: init,
        get: get,
        set: set,
        reset: reset,
        defaults: defaults
    };
})();
