/**
 * Solo Brave Tools System - 工具系统
 * 
 * 动态加载和执行工具
 */

var Tools = (function() {
    'use strict';
    
    // ===== 工具注册表 =====
    var tools = {};
    var toolCategories = {};
    var toolHistory = [];
    var maxHistory = 50;
    
    // ===== 内置工具 =====
    var builtInTools = {
        // 文件操作
        'file_read': {
            name: '读取文件',
            category: 'file',
            description: '读取文件内容',
            icon: '📄',
            params: [
                { name: 'path', type: 'string', required: true, description: '文件路径' }
            ],
            execute: function(params) {
                return new Promise(function(resolve, reject) {
                    // 在 Node.js 环境或通过 API 调用
                    resolve({ success: true, content: '[模拟] 文件内容...' });
                });
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
                return new Promise(function(resolve) {
                    resolve({ success: true, path: params.path });
                });
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
                return new Promise(function(resolve) {
                    resolve({ success: true, files: [] });
                });
            }
        },
        
        // Bash 命令
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
                return new Promise(function(resolve) {
                    resolve({ success: true, output: '[模拟] 命令执行结果' });
                });
            }
        },
        
        // 搜索
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
                return new Promise(function(resolve) {
                    resolve({ success: true, results: [] });
                });
            }
        },
        
        // 浏览器
        'browser_open': {
            name: '打开网页',
            category: 'browser',
            description: '在浏览器中打开URL',
            icon: '🌍',
            params: [
                { name: 'url', type: 'string', required: true, description: '网址' }
            ],
            execute: function(params) {
                return new Promise(function(resolve) {
                    window.open(params.url, '_blank');
                    resolve({ success: true });
                });
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
                return new Promise(function(resolve) {
                    resolve({ success: true, screenshot: '[base64...]' });
                });
            }
        },
        
        // 代码执行
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
                return new Promise(function(resolve) {
                    resolve({ success: true, output: '[执行结果]' });
                });
            }
        },
        
        // 时间
        'time_now': {
            name: '当前时间',
            category: 'system',
            description: '获取当前时间',
            icon: '⏰',
            params: [],
            execute: function(params) {
                return new Promise(function(resolve) {
                    resolve({ 
                        success: true, 
                        timestamp: Date.now(),
                        datetime: new Date().toISOString()
                    });
                });
            }
        },
        
        // 记忆
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
                return new Promise(function(resolve) {
                    // 调用 Memory 模块
                    if (typeof Memory !== 'undefined') {
                        var tags = params.tags ? params.tags.split(',') : [];
                        Memory.remember(params.content, tags);
                    }
                    resolve({ success: true });
                });
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
                return new Promise(function(resolve) {
                    var results = [];
                    if (typeof Memory !== 'undefined') {
                        results = Memory.search(params.query);
                    }
                    resolve({ success: true, results: results });
                });
            }
        }
    };
    
    // ===== 初始化 =====
    function init() {
        // 注册内置工具
        Object.keys(builtInTools).forEach(function(id) {
            registerTool(id, builtInTools[id]);
        });
        
        console.log('[Tools] 已加载 ' + Object.keys(tools).length + ' 个工具');
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
        
        // 分类
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
            return Promise.reject(new Error('Tool not found: ' + id));
        }
        
        // 验证参数
        var validation = validateParams(tool, params);
        if (!validation.valid) {
            return Promise.reject(new Error(validation.error));
        }
        
        // 记录历史
        recordExecution(id, params);
        
        // 执行
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
                return { valid: false, error: 'Missing required parameter: ' + param.name };
            }
            
            if (params[param.name] !== undefined && params[param.name] !== null) {
                var expectedType = param.type;
                var actualType = typeof params[param.name];
                
                if (expectedType === 'number' && isNaN(params[param.name])) {
                    return { valid: false, error: 'Parameter ' + param.name + ' must be a number' };
                }
            }
        }
        
        return { valid: true };
    }
    
    // ===== 记录执行历史 =====
    function recordExecution(toolId, params) {
        toolHistory.unshift({
            toolId: toolId,
            params: params,
            timestamp: Date.now()
        });
        
        if (toolHistory.length > maxHistory) {
            toolHistory = toolHistory.slice(0, maxHistory);
        }
        
        // 保存
        var history = toolHistory.map(function(h) {
            return { toolId: h.toolId, timestamp: h.timestamp };
        });
        Store.set('tools_history', history);
    }
    
    // ===== 获取执行历史 =====
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
    
    // ===== 格式化工具为 Schema =====
    function getSchema() {
        var schema = Object.values(tools).map(function(tool) {
            return {
                name: tool.name,
                description: tool.description,
                input_schema: {
                    type: 'object',
                    properties: {},
                    required: []
                }
            };
        });
        
        Object.values(tools).forEach(function(tool) {
            tool.params.forEach(function(param) {
                var prop = schema.find(function(s) { return s.name === tool.name; });
                if (prop) {
                    prop.input_schema.properties[param.name] = {
                        type: param.type,
                        description: param.description
                    };
                    if (param.required) {
                        prop.input_schema.required.push(param.name);
                    }
                }
            });
        });
        
        return schema;
    }
    
    // ===== 添加自定义工具 =====
    function addCustomTool(id, config) {
        if (tools[id]) {
            console.warn('[Tools] Tool ' + id + ' already exists');
            return false;
        }
        
        registerTool(id, config);
        EventBus.emit('tool:added', tools[id]);
        return true;
    }
    
    // ===== 导出 API =====
    return {
        init: init,
        get: get,
        getAll: getAll,
        getByCategory: getByCategory,
        getCategories: getCategories,
        search: search,
        execute: execute,
        validateParams: validateParams,
        getHistory: getHistory,
        getStats: getStats,
        getMostUsed: getMostUsed,
        getSchema: getSchema,
        addCustomTool: addCustomTool
    };
})();
