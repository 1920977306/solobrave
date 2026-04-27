/**
 * Solo Brave Framework - 框架入口
 * 
 * 整合所有模块，统一初始化
 * 借鉴 RuoYi-Vue-Pro 模块化架构
 */

var SoloBrave = (function() {
    'use strict';
    
    // ===== 版本信息 =====
    var VERSION = '1.0.0';
    
    // ===== 模块注册表 =====
    var modules = {};
    var initialized = false;
    var initPromise = null;
    
    // ===== 注册模块 =====
    function registerModule(name, module) {
        modules[name] = module;
        console.log('[SoloBrave] Module registered:', name);
    }
    
    // ===== 获取模块 =====
    function getModule(name) {
        return modules[name] || null;
    }
    
    // ===== 获取所有模块 =====
    function getAllModules() {
        return Object.assign({}, modules);
    }
    
    // ===== 初始化 =====
    function init(options) {
        if (initialized) {
            console.log('[SoloBrave] Already initialized');
            return Promise.resolve(modules);
        }
        
        if (initPromise) {
            return initPromise;
        }
        
        initPromise = new Promise(function(resolve) {
            console.log('[SoloBrave] Initializing framework v' + VERSION + '...');
            
            // 初始化配置
            Config.init(options);
            
            // 注册模块
            registerModule('Config', Config);
            registerModule('Memory', MemoryModule);
            registerModule('Tools', ToolsModule);
            registerModule('Knowledge', KnowledgeModule);
            registerModule('Result', Result);
            registerModule('Exception', Exception);
            
            // 初始化各模块
            var initPromises = [];
            
            if (modules.Memory && modules.Memory.init) {
                initPromises.push(
                    Promise.resolve(modules.Memory.init())
                        .then(function(stats) {
                            console.log('[SoloBrave] Memory module:', stats);
                        })
                );
            }
            
            if (modules.Tools && modules.Tools.init) {
                initPromises.push(
                    Promise.resolve(modules.Tools.init())
                        .then(function(tools) {
                            console.log('[SoloBrave] Tools module:', Object.keys(tools).length, 'tools');
                        })
                );
            }
            
            if (modules.Knowledge && modules.Knowledge.init) {
                initPromises.push(
                    Promise.resolve(modules.Knowledge.init())
                        .then(function(stats) {
                            console.log('[SoloBrave] Knowledge module:', stats);
                        })
                );
            }
            
            // 等待所有模块初始化
            Promise.all(initPromises).then(function() {
                initialized = true;
                console.log('[SoloBrave] Framework initialized successfully!');
                EventBus.emit('framework:ready', modules);
                resolve(modules);
            }).catch(function(err) {
                console.error('[SoloBrave] Init error:', err);
                resolve(modules);
            });
        });
        
        return initPromise;
    }
    
    // ===== 检查初始化 =====
    function isReady() {
        return initialized;
    }
    
    // ===== 获取状态 =====
    function getStatus() {
        var status = {
            version: VERSION,
            initialized: initialized,
            modules: {}
        };
        
        Object.keys(modules).forEach(function(name) {
            var mod = modules[name];
            status.modules[name] = {
                loaded: !!mod,
                methods: mod ? Object.keys(mod).filter(function(k) { return typeof mod[k] === 'function'; }).length : 0
            };
        });
        
        return status;
    }
    
    // ===== 执行工具 =====
    function executeTool(toolId, params) {
        if (!modules.Tools) {
            return Promise.reject(new Exception.SystemException('Tools module not loaded'));
        }
        return modules.Tools.execute(toolId, params);
    }
    
    // ===== 记忆 =====
    function remember(content, metadata) {
        if (!modules.Memory) return null;
        return modules.Memory.remember(content, metadata);
    }
    
    function recall(query) {
        if (!modules.Memory) return [];
        return modules.Memory.search(query);
    }
    
    // ===== 知识库 =====
    function searchKnowledge(query, options) {
        if (!modules.Knowledge) return [];
        return modules.Knowledge.search(query, options);
    }
    
    function enhanceWithRAG(query, prompt) {
        if (!modules.Knowledge) return prompt;
        return modules.Knowledge.enhanceWithRAG(query, prompt);
    }
    
    // ===== 统一响应 =====
    function success(data, msg) {
        return Result.success(data, msg);
    }
    
    function error(code, msg) {
        return Result.error(code, msg);
    }
    
    // ===== 导出 =====
    return {
        // 版本
        VERSION: VERSION,
        
        // 初始化
        init: init,
        isReady: isReady,
        getStatus: getStatus,
        
        // 模块管理
        register: registerModule,
        getModule: getModule,
        getAllModules: getAllModules,
        
        // 快捷方法
        remember: remember,
        recall: recall,
        executeTool: executeTool,
        searchKnowledge: searchKnowledge,
        enhanceWithRAG: enhanceWithRAG,
        
        // 响应
        success: success,
        error: error
    };
})();
