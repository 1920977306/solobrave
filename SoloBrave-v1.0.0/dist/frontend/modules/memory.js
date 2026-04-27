/**
 * Solo Brave Modules - Memory System
 * 
 * 三层记忆系统模块化实现
 */

var MemoryModule = (function() {
    'use strict';
    
    // ===== 模块信息 =====
    var moduleInfo = {
        name: 'MemoryModule',
        version: '1.0.0',
        description: '三层记忆系统'
    };
    
    // ===== 存储键 =====
    var STORAGE_KEYS = {
        CONVERSATION: 'memory_conversation',
        DAILY: 'memory_daily',
        CORE: 'memory_core',
        STATS: 'memory_stats'
    };
    
    // ===== 三层记忆存储 =====
    var conversationMemory = [];  // L1: 当前对话
    var dailyMemories = {};       // L2: 日级记忆
    var coreMemories = [];        // L3: 核心记忆
    
    // ===== 配置 =====
    var config = {
        maxConversationEntries: 100,
        maxDailyPerDay: 50,
        maxCoreMemories: 200,
        autoSummarize: true,
        summarizeInterval: 86400000
    };
    
    // ===== 初始化 =====
    function init(options) {
        // 合并配置
        if (options) {
            Object.assign(config, options);
        }
        
        // 从存储加载
        loadFromStorage();
        
        // 检查自动汇总
        if (config.autoSummarize) {
            checkAutoSummarize();
        }
        
        console.log('[MemoryModule] Initialized');
        return getStats();
    }
    
    // ===== 加载存储 =====
    function loadFromStorage() {
        var dailyData = Store.get(STORAGE_KEYS.DAILY) || {};
        var coreData = Store.get(STORAGE_KEYS.CORE) || [];
        var stats = Store.get(STORAGE_KEYS.STATS) || {};
        
        dailyMemories = dailyData;
        coreMemories = coreData;
        config.lastSummarize = stats.lastSummarize;
    }
    
    // ===== 保存存储 =====
    function saveDailyToStorage() {
        Store.set(STORAGE_KEYS.DAILY, dailyMemories);
    }
    
    function saveCoreToStorage() {
        Store.set(STORAGE_KEYS.CORE, coreMemories);
    }
    
    function saveStats() {
        Store.set(STORAGE_KEYS.STATS, {
            lastSummarize: config.lastSummarize,
            total: conversationMemory.length + countDaily() + coreMemories.length
        });
    }
    
    // ===== L1: 对话记忆 =====
    function remember(content, metadata) {
        metadata = metadata || {};
        
        var entry = {
            id: 'mem_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
            content: content,
            timestamp: Date.now(),
            type: 'conversation',
            metadata: metadata,
            importance: metadata.importance || 0.5
        };
        
        conversationMemory.push(entry);
        
        // 限制大小
        if (conversationMemory.length > config.maxConversationEntries) {
            conversationMemory = conversationMemory.slice(-config.maxConversationEntries);
        }
        
        // 自动提升重要内容
        if (entry.importance >= 0.7) {
            note(content, { source: 'conversation', tags: metadata.tags || [] });
        }
        
        return entry;
    }
    
    function recallHistory(limit) {
        limit = limit || 50;
        return conversationMemory.slice(-limit);
    }
    
    function clearConversation() {
        conversationMemory = [];
    }
    
    // ===== L2: 日级记忆 =====
    function note(content, options) {
        options = options || {};
        var today = getDateKey(new Date());
        
        if (!dailyMemories[today]) {
            dailyMemories[today] = [];
        }
        
        var entry = {
            id: 'note_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
            content: content,
            timestamp: Date.now(),
            type: 'daily',
            tags: options.tags || [],
            source: options.source || 'manual',
            importance: options.importance || 0.7
        };
        
        dailyMemories[today].push(entry);
        
        if (dailyMemories[today].length > config.maxDailyPerDay) {
            dailyMemories[today] = dailyMemories[today].slice(-config.maxDailyPerDay);
        }
        
        saveDailyToStorage();
        return entry;
    }
    
    function getRecentNotes(days) {
        days = days || 7;
        var results = [];
        var now = new Date();
        
        for (var i = 0; i < days; i++) {
            var date = new Date(now - i * 86400000);
            var dateKey = getDateKey(date);
            if (dailyMemories[dateKey]) {
                results = results.concat(dailyMemories[dateKey]);
            }
        }
        
        return results;
    }
    
    function getTodayMemories() {
        var today = getDateKey(new Date());
        return dailyMemories[today] || [];
    }
    
    // ===== L3: 核心记忆 =====
    function memorize(content, options) {
        options = options || {};
        
        var entry = {
            id: 'core_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
            content: content,
            timestamp: Date.now(),
            type: 'core',
            tags: options.tags || [],
            source: options.source || 'manual',
            importance: Math.min(1, options.importance || 1.0)
        };
        
        coreMemories.push(entry);
        
        if (coreMemories.length > config.maxCoreMemories) {
            coreMemories.sort(function(a, b) {
                return (b.importance - a.importance) || (b.timestamp - a.timestamp);
            });
            coreMemories = coreMemories.slice(0, config.maxCoreMemories);
        }
        
        saveCoreToStorage();
        return entry;
    }
    
    function getCoreMemories(limit) {
        limit = limit || 50;
        var sorted = coreMemories.slice().sort(function(a, b) {
            return (b.importance - a.importance) || (b.timestamp - a.timestamp);
        });
        return sorted.slice(0, limit);
    }
    
    // ===== 搜索 =====
    function search(query, options) {
        options = options || {};
        var memoryType = options.type;
        query = query.toLowerCase();
        var results = [];
        
        // 搜索各层
        if (!memoryType || memoryType === 'conversation') {
            conversationMemory.forEach(function(entry) {
                if (entry.content.toLowerCase().indexOf(query) !== -1) {
                    results.push(entry);
                }
            });
        }
        
        if (!memoryType || memoryType === 'daily') {
            Object.keys(dailyMemories).forEach(function(dateKey) {
                dailyMemories[dateKey].forEach(function(entry) {
                    if (entry.content.toLowerCase().indexOf(query) !== -1) {
                        results.push(entry);
                    }
                });
            });
        }
        
        if (!memoryType || memoryType === 'core') {
            coreMemories.forEach(function(entry) {
                if (entry.content.toLowerCase().indexOf(query) !== -1) {
                    results.push(entry);
                }
            });
        }
        
        results.sort(function(a, b) { return b.importance - a.importance; });
        return results.slice(0, options.limit || 20);
    }
    
    // ===== 蒸馏 =====
    function summarizeDaily(date) {
        date = date || new Date();
        var dateKey = getDateKey(date);
        var todayMemories = dailyMemories[dateKey] || [];
        
        if (todayMemories.length === 0) return null;
        
        var keyPoints = todayMemories
            .filter(function(m) { return m.importance >= 0.7; })
            .map(function(m) { return m.content; });
        
        if (keyPoints.length > 0) {
            memorize(keyPoints.slice(0, 3).join('；'), {
                tags: ['日总结', dateKey],
                source: 'auto_summarize',
                importance: 0.8
            });
        }
        
        config.lastSummarize = Date.now();
        saveStats();
        
        return { date: dateKey, keyPoints: keyPoints };
    }
    
    function checkAutoSummarize() {
        if (!config.lastSummarize && Object.keys(dailyMemories).length > 0) {
            config.lastSummarize = Date.now();
            return;
        }
        
        if (Date.now() - config.lastSummarize >= config.summarizeInterval) {
            summarizeDaily();
        }
    }
    
    // ===== 上下文构建 =====
    function getContextForAI(maxLength) {
        maxLength = maxLength || 2000;
        var contextParts = [];
        
        // 核心记忆
        var coreMems = getCoreMemories(5);
        if (coreMems.length > 0) {
            var coreText = '## 重要记忆\n';
            coreMems.forEach(function(m) {
                coreText += '- ' + m.content + '\n';
            });
            contextParts.push(coreText);
        }
        
        // 最近笔记
        var recentNotes = getRecentNotes(3);
        if (recentNotes.length > 0) {
            var notesText = '## 最近记录\n';
            recentNotes.slice(-5).forEach(function(m) {
                var date = new Date(m.timestamp).toLocaleDateString();
                notesText += '- [' + date + '] ' + m.content.substring(0, 80) + '\n';
            });
            contextParts.push(notesText);
        }
        
        var fullContext = contextParts.join('\n\n');
        
        if (fullContext.length > maxLength) {
            fullContext = fullContext.substring(0, maxLength) + '\n\n...(已截断)';
        }
        
        return fullContext;
    }
    
    // ===== 工具方法 =====
    function getDateKey(date) {
        var d = new Date(date);
        return d.getFullYear() + '-' + 
            String(d.getMonth() + 1).padStart(2, '0') + '-' + 
            String(d.getDate()).padStart(2, '0');
    }
    
    function countDaily() {
        var count = 0;
        Object.keys(dailyMemories).forEach(function(key) {
            count += dailyMemories[key].length;
        });
        return count;
    }
    
    function getStats() {
        return {
            conversation: conversationMemory.length,
            daily: countDaily(),
            core: coreMemories.length,
            total: conversationMemory.length + countDaily() + coreMemories.length,
            lastSummarize: config.lastSummarize ? new Date(config.lastSummarize).toISOString() : null
        };
    }
    
    // ===== 导出 API =====
    return {
        // 模块信息
        info: moduleInfo,
        
        // 初始化
        init: init,
        
        // L1 对话记忆
        remember: remember,
        recallHistory: recallHistory,
        clearConversation: clearConversation,
        
        // L2 日级记忆
        note: note,
        getRecentNotes: getRecentNotes,
        getTodayMemories: getTodayMemories,
        
        // L3 核心记忆
        memorize: memorize,
        getCoreMemories: getCoreMemories,
        
        // 搜索
        search: search,
        
        // 蒸馏
        summarizeDaily: summarizeDaily,
        
        // 上下文
        getContextForAI: getContextForAI,
        
        // 统计
        getStats: getStats
    };
})();
