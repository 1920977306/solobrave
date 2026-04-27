/**
 * Solo Brave Modules - Tools System
 * 
 * 工具系统模块化实现
 */

var ToolsModule = (function() {
    'use strict';
    
    // ===== 模块信息 =====
    var moduleInfo = {
        name: 'ToolsModule',
        version: '1.0.0',
        description: '工具管理系统'
    };
    
    // ===== 工具注册表 =====
    var tools = {};
    var toolCategories = {};
    var toolHistory = [];
    var maxHistory = 50;
    
    // ===== 内置工具 =====
    var builtInTools = {
        'file_read': {
            name: '读取文件',
            category: 'file',
            description: '读取文件内容',
            icon: '📄',
            params: [
                { name: 'path', type: 'string', required: true, description: '文件路径' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, content: '[模拟] 文件内容...' });
            }
        },
        
        'file_write': {
            name: '写入文件',
            category: 'file',
            description: '写入内容到文件',
            icon: '💾',
            params: [
                { name: 'path', type: 'string', required: true, description: '文件路径' },
                { name: 'content', type: 'string', required: true, description: '文件内容' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, path: params.path });
            }
        },
        
        'file_search': {
            name: '搜索文件',
            category: 'file',
            description: '在目录中搜索文件',
            icon: '🔍',
            params: [
                { name: 'pattern', type: 'string', required: true, description: '搜索模式' },
                { name: 'path', type: 'string', required: false, description: '搜索目录' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, files: [] });
            }
        },
        
        'bash': {
            name: '执行命令',
            category: 'system',
            description: '执行 Bash/Shell 命令',
            icon: '⌨️',
            params: [
                { name: 'command', type: 'string', required: true, description: '要执行的命令' },
                { name: 'timeout', type: 'number', required: false, description: '超时时间(秒)' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, output: '[模拟] 命令执行结果' });
            }
        },
        
        'web_search': {
            name: '网页搜索',
            category: 'search',
            description: '搜索互联网信息',
            icon: '🌐',
            params: [
                { name: 'query', type: 'string', required: true, description: '搜索关键词' },
                { name: 'limit', type: 'number', required: false, description: '结果数量' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, results: [] });
            }
        },
        
        'browser_open': {
            name: '打开网页',
            category: 'browser',
            description: '在浏览器中打开URL',
            icon: '🌍',
            params: [
                { name: 'url', type: 'string', required: true, description: '网址' }
            ],
            execute: function(params) {
                window.open(params.url, '_blank');
                return Promise.resolve({ success: true });
            }
        },
        
        'browser_screenshot': {
            name: '截图',
            category: 'browser',
            description: '截取当前页面',
            icon: '📸',
            params: [
                { name: 'fullPage', type: 'boolean', required: false, description: '截取整页' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, screenshot: '[base64...]' });
            }
        },
        
        'code_run': {
            name: '运行代码',
            category: 'code',
            description: '执行代码片段',
            icon: '▶️',
            params: [
                { name: 'language', type: 'string', required: true, description: '语言 (js/python)' },
                { name: 'code', type: 'string', required: true, description: '代码内容' }
            ],
            execute: function(params) {
                return Promise.resolve({ success: true, output: '[执行结果]' });
            }
        },
        
        'time_now': {
            name: '当前时间',
            category: 'system',
            description: '获取当前时间',
            icon: '⏰',
            params: [],
            execute: function(params) {
                return Promise.resolve({
                    success: true,
                    timestamp: Date.now(),
                    datetime: new Date().toISOString()
                });
            }
        },
        
        'memory_remember': {
            name: '记忆',
            category: 'memory',
            description: '记住重要信息',
            icon: '🧠',
            params: [
                { name: 'content', type: 'string', required: true, description: '要记忆的内容' },
                { name: 'tags', type: 'string', required: false, description: '标签(逗号分隔)' }
            ],
            execute: function(params) {
                if (typeof MemoryModule !== 'undefined') {
                    var tags = params.tags ? params.tags.split(',') : [];
                    MemoryModule.remember(params.content, { tags: tags });
                }
                return Promise.resolve({ success: true });
            }
        },
        
        'memory_recall': {
            name: '回忆',
            category: 'memory',
            description: '搜索相关记忆',
            icon: '💭',
            params: [
                { name: 'query', type: 'string', required: true, description: '搜索查询' }
            ],
            execute: function(params) {
                var results = [];
                if (typeof MemoryModule !== 'undefined') {
                    results = MemoryModule.search(params.query);
                }
                return Promise.resolve({ success: true, results: results });
            }
        }
    };
    
    // ===== 初始化 =====
    function init() {
        Object.keys(builtInTools).forEach(function(id) {
            registerTool(id, builtInTools[id]);
        });
        
        console.log('[ToolsModule] Initialized with', Object.keys(tools).length, 'tools');
        return tools;
    }
    
    // ===== 注册工具 =====
    function registerTool(id, config) {
        tools[id] = {
            id: id,
            name: config.name || id,
            description: config.description || '',
            category: config.category || 'general',
            icon: config.icon || '🔧',
            params: config.params || [],
            execute: config.execute || function() { return Promise.resolve({}); },
            usageCount: 0,
            lastUsed: null
        };
        
        if (!toolCategories[tools[id].category]) {
            toolCategories[tools[id].category] = [];
        }
        if (!toolCategories[tools[id].category].includes(id)) {
            toolCategories[tools[id].category].push(id);
        }
    }
    
    // ===== 获取工具 =====
    function get(id) {
        return tools[id] || null;
    }
    
    function getAll() {
        return Object.values(tools);
    }
    
    function getByCategory(category) {
        var categoryTools = toolCategories[category];
        if (!categoryTools) return [];
        return categoryTools.map(function(id) { return tools[id]; }).filter(Boolean);
    }
    
    function getCategories() {
        return Object.keys(toolCategories);
    }
    
    // ===== 搜索工具 =====
    function search(query) {
        query = query.toLowerCase();
        var results = [];
        
        Object.values(tools).forEach(function(tool) {
            var score = 0;
            if (tool.name.toLowerCase().indexOf(query) !== -1) score += 10;
            if (tool.description.toLowerCase().indexOf(query) !== -1) score += 5;
            if (tool.category.toLowerCase().indexOf(query) !== -1) score += 3;
            
            if (score > 0) {
                results.push({ tool: tool, score: score });
            }
        });
        
        results.sort(function(a, b) { return b.score - a.score; });
        return results.map(function(r) { return r.tool; });
    }
    
    // ===== 执行工具 =====
    function execute(id, params) {
        var tool = tools[id];
        if (!tool) {
            return Promise.reject(new Exception.NotFoundException('工具 ' + id));
        }
        
        var validation = validateParams(tool, params);
        if (!validation.valid) {
            return Promise.reject(new Exception.ParamException(validation.error));
        }
        
        recordExecution(id, params);
        
        return tool.execute(params).then(function(result) {
            tool.usageCount++;
            tool.lastUsed = Date.now();
            return result;
        });
    }
    
    // ===== 验证参数 =====
    function validateParams(tool, params) {
        for (var i = 0; i < tool.params.length; i++) {
            var param = tool.params[i];
            
            if (param.required && (params[param.name] === undefined || params[param.name] === null)) {
                return { valid: false, error: '缺少必填参数: ' + param.name };
            }
        }
        
        return { valid: true };
    }
    
    // ===== 记录历史 =====
    function recordExecution(toolId, params) {
        toolHistory.unshift({
            toolId: toolId,
            params: params,
            timestamp: Date.now()
        });
        
        if (toolHistory.length > maxHistory) {
            toolHistory = toolHistory.slice(0, maxHistory);
        }
        
        Store.set('tools_history', toolHistory.map(function(h) {
            return { toolId: h.toolId, timestamp: h.timestamp };
        }));
    }
    
    // ===== 获取历史 =====
    function getHistory(limit) {
        limit = limit || 10;
        return toolHistory.slice(0, limit);
    }
    
    // ===== 获取统计 =====
    function getStats() {
        var stats = {};
        Object.values(tools).forEach(function(tool) {
            stats[tool.id] = {
                count: tool.usageCount,
                lastUsed: tool.lastUsed
            };
        });
        return stats;
    }
    
    // ===== 获取常用工具 =====
    function getMostUsed(limit) {
        limit = limit || 5;
        var all = Object.values(tools);
        all.sort(function(a, b) { return b.usageCount - a.usageCount; });
        return all.slice(0, limit);
    }
    
    // ===== 获取 Schema =====
    function getSchema() {
        return Object.values(tools).map(function(tool) {
            return {
                name: tool.name,
                description: tool.description,
                input_schema: {
                    type: 'object',
                    properties: {},
                    required: tool.params.filter(function(p) { return p.required; }).map(function(p) { return p.name; })
                }
            };
        });
    }
    
    // ===== 添加自定义工具 =====
    function addCustomTool(id, config) {
        if (tools[id]) {
            console.warn('[ToolsModule] Tool', id, 'already exists');
            return false;
        }
        registerTool(id, config);
        EventBus.emit('tool:added', tools[id]);
        return true;
    }
    
    // ===== 导出 API =====
    return {
        // 模块信息
        info: moduleInfo,
        
        // 初始化
        init: init,
        
        // 注册/获取
        registerTool: registerTool,
        get: get,
        getAll: getAll,
        getByCategory: getByCategory,
        getCategories: getCategories,
        
        // 搜索/执行
        search: search,
        execute: execute,
        
        // 历史/统计
        getHistory: getHistory,
        getStats: getStats,
        getMostUsed: getMostUsed,
        
        // Schema
        getSchema: getSchema,
        
        // 自定义
        addCustomTool: addCustomTool
    };
})();
