/**
 * Solo Brave Memory System - 三层记忆系统
 * 
 * L1: 对话记忆（短期，内存）
 * L2: 日级记忆（中期，localStorage）
 * L3: 核心记忆（长期，localStorage + FTS）
 */

var Memory = (function() {
    'use strict';
    
    // ===== 存储键 =====
    var STORAGE_KEYS = {
        CONVERSATION: 'memory_conversation',
        DAILY: 'memory_daily',
        CORE: 'memory_core',
        STATS: 'memory_stats'
    };
    
    // ===== 三层记忆 =====
    var conversationMemory = [];  // L1: 当前对话
    var dailyMemories = {};       // L2: 日级记忆 { date: [entries] }
    var coreMemories = [];        // L3: 核心记忆
    
    // ===== 配置 =====
    var config = {
        maxConversationEntries: 100,
        maxDailyPerDay: 50,
        maxCoreMemories: 200,
        autoSummarize: true,
        summarizeInterval: 86400000, // 24小时
        lastSummarize: null
    };
    
    // ===== 初始化 =====
    function init() {
        loadFromStorage();
        
        // 检查是否需要自动汇总
        if (config.autoSummarize) {
            checkAutoSummarize();
        }
        
        console.log('[Memory] 三层记忆系统初始化完成');
        console.log('[Memory] 对话: ' + conversationMemory.length + ' | 日级: ' + countDaily() + ' | 核心: ' + coreMemories.length);
        
        return {
            conversation: conversationMemory.length,
            daily: countDaily(),
            core: coreMemories.length
        };
    }
    
    // ===== 从存储加载 =====
    function loadFromStorage() {
        // 加载日级记忆
        var dailyData = Store.get(STORAGE_KEYS.DAILY) || {};
        if (typeof dailyData === 'object') {
            dailyMemories = dailyData;
        }
        
        // 加载核心记忆
        var coreData = Store.get(STORAGE_KEYS.CORE) || [];
        if (Array.isArray(coreData)) {
            coreMemories = coreData;
        }
        
        // 加载统计
        var stats = Store.get(STORAGE_KEYS.STATS) || {};
        if (typeof stats === 'object') {
            config.lastSummarize = stats.lastSummarize;
        }
    }
    
    // ===== 保存到存储 =====
    function saveDailyToStorage() {
        Store.set(STORAGE_KEYS.DAILY, dailyMemories);
    }
    
    function saveCoreToStorage() {
        Store.set(STORAGE_KEYS.CORE, coreMemories);
    }
    
    function saveStats() {
        Store.set(STORAGE_KEYS.STATS, {
            lastSummarize: config.lastSummarize,
            totalMemories: conversationMemory.length + countDaily() + coreMemories.length
        });
    }
    
    // ===== L1: 对话记忆 =====
    
    /**
     * 记住新内容
     */
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
        
        // 自动提升重要内容到日级记忆
        if (entry.importance >= 0.7) {
            note(content, {
                source: 'conversation',
                tags: metadata.tags || []
            });
        }
        
        return entry;
    }
    
    /**
     * 获取对话历史
     */
    function recallHistory(limit) {
        limit = limit || 50;
        return conversationMemory.slice(-limit);
    }
    
    /**
     * 清空对话记忆
     */
    function clearConversation() {
        conversationMemory = [];
    }
    
    // ===== L2: 日级记忆 =====
    
    /**
     * 记录重要信息
     */
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
        
        // 限制每天数量
        if (dailyMemories[today].length > config.maxDailyPerDay) {
            dailyMemories[today] = dailyMemories[today].slice(-config.maxDailyPerDay);
        }
        
        saveDailyToStorage();
        
        return entry;
    }
    
    /**
     * 获取最近的笔记
     */
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
    
    /**
     * 获取今日记忆
     */
    function getTodayMemories() {
        var today = getDateKey(new Date());
        return dailyMemories[today] || [];
    }
    
    // ===== L3: 核心记忆 =====
    
    /**
     * 永久记忆
     */
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
        
        // 限制总数
        if (coreMemories.length > config.maxCoreMemories) {
            // 删除最旧或最不重要的
            coreMemories.sort(function(a, b) {
                return (b.importance - a.importance) || (b.timestamp - a.timestamp);
            });
            coreMemories = coreMemories.slice(0, config.maxCoreMemories);
        }
        
        saveCoreToStorage();
        
        return entry;
    }
    
    /**
     * 获取核心记忆
     */
    function getCoreMemories(limit) {
        limit = limit || 50;
        
        var sorted = coreMemories.slice().sort(function(a, b) {
            return (b.importance - a.importance) || (b.timestamp - a.timestamp);
        });
        
        return sorted.slice(0, limit);
    }
    
    /**
     * 获取身份信息
     */
    function getIdentity() {
        var identity = {
            name: '妍妍',
            description: '',
            traits: [],
            preferences: {}
        };
        
        coreMemories.forEach(function(memory) {
            var content = memory.content.toLowerCase();
            
            if (content.indexOf('名字') !== -1 || content.indexOf('name') !== -1) {
                identity.description += memory.content + '\n';
            }
            if (content.indexOf('喜欢') !== -1 || content.indexOf('prefer') !== -1) {
                identity.preferences[memory.id] = memory.content;
            }
            if (memory.tags.indexOf('性格') !== -1 || memory.tags.indexOf('trait') !== -1) {
                identity.traits.push(memory.content);
            }
        });
        
        return identity;
    }
    
    // ===== 搜索 =====
    
    /**
     * 搜索记忆
     */
    function search(query, options) {
        options = options || {};
        var memoryType = options.type; // 'conversation' | 'daily' | 'core' | undefined = all
        
        query = query.toLowerCase();
        var results = [];
        
        // 搜索对话记忆
        if (!memoryType || memoryType === 'conversation') {
            conversationMemory.forEach(function(entry) {
                if (entry.content.toLowerCase().indexOf(query) !== -1) {
                    results.push(entry);
                }
            });
        }
        
        // 搜索日级记忆
        if (!memoryType || memoryType === 'daily') {
            Object.keys(dailyMemories).forEach(function(dateKey) {
                dailyMemories[dateKey].forEach(function(entry) {
                    if (entry.content.toLowerCase().indexOf(query) !== -1) {
                        results.push(entry);
                    }
                });
            });
        }
        
        // 搜索核心记忆
        if (!memoryType || memoryType === 'core') {
            coreMemories.forEach(function(entry) {
                if (entry.content.toLowerCase().indexOf(query) !== -1) {
                    results.push(entry);
                }
            });
        }
        
        // 按重要性排序
        results.sort(function(a, b) { return b.importance - a.importance; });
        
        // 限制数量
        var limit = options.limit || 20;
        return results.slice(0, limit);
    }
    
    // ===== 记忆蒸馏 =====
    
    /**
     * 检查自动汇总
     */
    function checkAutoSummarize() {
        if (!config.lastSummarize) {
            config.lastSummarize = Date.now();
            return;
        }
        
        if (Date.now() - config.lastSummarize >= config.summarizeInterval) {
            summarizeDaily();
        }
    }
    
    /**
     * 每日汇总
     */
    function summarizeDaily(date) {
        date = date || new Date();
        var dateKey = getDateKey(date);
        
        var todayMemories = dailyMemories[dateKey] || [];
        
        if (todayMemories.length === 0) {
            return null;
        }
        
        // 提取关键点
        var keyPoints = todayMemories
            .filter(function(m) { return m.importance >= 0.7; })
            .map(function(m) { return m.content; });
        
        // 生成摘要
        var summary = {
            date: dateKey,
            summary: keyPoints.slice(0, 3).join('；'),
            keyPoints: keyPoints,
            memoryCount: todayMemories.length,
            timestamp: Date.now()
        };
        
        // 将摘要提升为核心记忆
        if (keyPoints.length > 0) {
            memorize(summary.summary, {
                tags: ['日总结', dateKey],
                source: 'auto_summarize',
                importance: 0.8
            });
        }
        
        config.lastSummarize = Date.now();
        saveStats();
        
        return summary;
    }
    
    /**
     * 蒸馏到核心记忆
     */
    function distill(days) {
        days = days || 30;
        var recentNotes = getRecentNotes(days);
        
        // 统计高频主题
        var themeCount = {};
        recentNotes.forEach(function(note) {
            var words = extractKeywords(note.content);
            words.forEach(function(word) {
                themeCount[word] = (themeCount[word] || 0) + 1;
            });
        });
        
        // 找出高频主题
        var highFreqThemes = Object.keys(themeCount)
            .filter(function(k) { return themeCount[k] >= 3; })
            .sort(function(a, b) { return themeCount[b] - themeCount[a]; });
        
        // 生成核心记忆
        var newCore = [];
        highFreqThemes.slice(0, 10).forEach(function(theme) {
            var coreContent = '长期主题：' + theme + ' (出现 ' + themeCount[theme] + ' 次)';
            
            // 检查是否已存在
            var exists = coreMemories.some(function(c) {
                return c.content.indexOf(theme) !== -1;
            });
            
            if (!exists) {
                memorize(coreContent, {
                    tags: ['蒸馏', '主题'],
                    importance: 0.7
                });
                newCore.push(coreContent);
            }
        });
        
        return newCore;
    }
    
    /**
     * 提取关键词
     */
    function extractKeywords(text) {
        // 简单分词
        var words = text.match(/[\u4e00-\u9fff]{2,4}/g) || [];
        
        // 停用词（使用 Set 提高查询性能）
        var stopWords = new Set(['这个', '那个', '什么', '怎么', '可以', '没有', '一个', '自己', '还有', '但是', '所以', '因为']);
        words = words.filter(function(w) { return !stopWords.has(w); });
        
        return words;
    }
    
    // ===== 上下文构建 =====
    
    /**
     * 为 AI 构建记忆上下文
     */
    function getContextForAI(maxLength) {
        maxLength = maxLength || 2000;
        
        var contextParts = [];
        
        // 1. 身份信息
        var identity = getIdentity();
        if (identity.description) {
            contextParts.push('## 身份\n' + identity.description);
        }
        
        // 2. 核心记忆
        var coreMemories = getCoreMemories(5);
        if (coreMemories.length > 0) {
            var coreText = '## 重要记忆\n';
            coreMemories.forEach(function(m) {
                coreText += '- ' + m.content + '\n';
            });
            contextParts.push(coreText);
        }
        
        // 3. 最近的笔记
        var recentNotes = getRecentNotes(3);
        if (recentNotes.length > 0) {
            var notesText = '## 最近记录\n';
            recentNotes.slice(-5).forEach(function(m) {
                var date = new Date(m.timestamp).toLocaleDateString();
                notesText += '- [' + date + '] ' + m.content.substring(0, 80) + '\n';
            });
            contextParts.push(notesText);
        }
        
        // 合并
        var fullContext = contextParts.join('\n\n');
        
        // 截断
        if (fullContext.length > maxLength) {
            fullContext = fullContext.substring(0, maxLength) + '\n\n...(记忆已截断)';
        }
        
        return fullContext;
    }
    
    // ===== 工具方法 =====
    
    function getDateKey(date) {
        var d = new Date(date);
        
        // 处理无效日期
        if (isNaN(d.getTime())) {
            d = new Date(); // 降级为当前时间
        }
        
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
        // L1: 对话记忆
        remember: remember,
        recallHistory: recallHistory,
        clearConversation: clearConversation,
        
        // L2: 日级记忆
        note: note,
        getRecentNotes: getRecentNotes,
        getTodayMemories: getTodayMemories,
        
        // L3: 核心记忆
        memorize: memorize,
        getCoreMemories: getCoreMemories,
        getIdentity: getIdentity,
        
        // 搜索
        search: search,
        
        // 蒸馏
        summarizeDaily: summarizeDaily,
        distill: distill,
        
        // 上下文
        getContextForAI: getContextForAI,
        
        // 工具
        getStats: getStats,
        init: init
    };
})();
