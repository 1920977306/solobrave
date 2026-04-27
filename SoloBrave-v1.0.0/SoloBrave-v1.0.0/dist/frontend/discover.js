/**
 * Solo Brave Discover - 发现系统
 * 
 * 借鉴 HelloGitHub：推荐技能/工具、社区广场、标签筛选
 */

var Discover = (function() {
    'use strict';
    
    // ===== 发现项类型 =====
    var ItemType = {
        SKILL: 'skill',
        TOOL: 'tool',
        TEMPLATE: 'template',
        KNOWLEDGE: 'knowledge'
    };
    
    // ===== 分类标签 =====
    var Categories = {
        'ai': { name: '🤖 AI', color: '#FF6B35', icon: '🤖' },
        'dev': { name: '🛠️ 开发', color: '#4ecdc4', icon: '🛠️' },
        'productivity': { name: '⚡ 效率', color: '#ffd93d', icon: '⚡' },
        'creative': { name: '🎨 创意', color: '#ff6b9d', icon: '🎨' },
        'learning': { name: '📚 学习', color: '#9b59b6', icon: '📚' },
        'system': { name: '⚙️ 系统', color: '#3498db', icon: '⚙️' }
    };
    
    // ===== 推荐项目库 =====
    var featuredItems = [
        {
            id: 'skill_brainstorm',
            type: ItemType.SKILL,
            name: '💡 头脑风暴',
            oneLine: '将模糊想法转化为完整方案',
            description: '通过引导式提问，把用户的零散想法组织成结构化的设计方案。适合产品规划、功能讨论、需求澄清。',
            tags: ['ai', 'productivity', 'dev'],
            stars: 286,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: '/brainstorm'
        },
        {
            id: 'skill_debug',
            type: ItemType.SKILL,
            name: '🔍 系统调试',
            oneLine: '4步根因分析，治标又治本',
            description: '采用「问题→假设→验证→修复」的闭环流程，找到 bug 根本原因而不是表面症状。',
            tags: ['dev', 'ai'],
            stars: 198,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: '/debug'
        },
        {
            id: 'skill_tdd',
            type: ItemType.SKILL,
            name: '🧪 测试驱动开发',
            oneLine: '先写测试再实现，代码质量有保障',
            description: '遵循「红→绿→重构」循环，确保每个功能都有测试覆盖，减少回归 bug。',
            tags: ['dev', 'ai'],
            stars: 156,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: '/tdd'
        },
        {
            id: 'skill_plan',
            type: ItemType.SKILL,
            name: '📋 任务规划',
            oneLine: '把大目标拆成可执行的小任务',
            description: '帮助用户将复杂的项目拆解成具体的每日任务，配合番茄工作法提升执行力。',
            tags: ['productivity', 'ai'],
            stars: 234,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: '/plan'
        },
        {
            id: 'tool_memory',
            type: ItemType.TOOL,
            name: '🧠 记忆系统',
            oneLine: '三层记忆，重要信息不忘',
            description: '对话级→日级→核心级，自动蒸馏长期记忆。让 AI 越来越懂你。',
            tags: ['ai', 'system'],
            stars: 312,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: 'Memory.remember()'
        },
        {
            id: 'tool_knowledge',
            type: ItemType.TOOL,
            name: '📚 知识库',
            oneLine: '向量搜索 + RAG，让 AI 更专业',
            description: '上传文档构建专属知识库，AI 回答时自动检索相关知识。',
            tags: ['ai', 'learning'],
            stars: 267,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: 'KnowledgeBase.search()'
        },
        {
            id: 'tool_channels',
            type: ItemType.TOOL,
            name: '📡 多渠道',
            oneLine: '一个 AI，多个入口',
            description: '支持网页、钉钉、API 等多渠道接入，统一管理消息收发。',
            tags: ['system', 'ai'],
            stars: 189,
            author: 'SoloBrave',
            url: null,
            featured: true,
            featuredDate: '2026-04',
            usage: 'Channels.send()'
        },
        {
            id: 'template_code_review',
            type: ItemType.TEMPLATE,
            name: '🔍 代码审查',
            oneLine: '自动审查代码，发现潜在问题',
            description: '从性能、安全、可读性等维度全面审查代码，给出改进建议。',
            tags: ['dev', 'ai'],
            stars: 145,
            author: 'SoloBrave',
            url: null,
            featured: false,
            featuredDate: '2026-04',
            usage: 'PromptBuilder.gen("code_review")'
        },
        {
            id: 'template_writing',
            type: ItemType.TEMPLATE,
            name: '✍️ 专业写作',
            oneLine: '邮件、报告、文档一键生成',
            description: '根据场景自动调整语气和格式，生成专业规范的商务文档。',
            tags: ['creative', 'productivity'],
            stars: 178,
            author: 'SoloBrave',
            url: null,
            featured: false,
            featuredDate: '2026-04',
            usage: 'PromptBuilder.gen("writing")'
        }
    ];
    
    // ===== 用户贡献 =====
    var userContributions = [];
    
    // ===== 统计数据（预计算 + 缓存）=====
    var stats = {
        totalItems: featuredItems.length,
        totalStars: 0,
        totalViews: 0,
        byCategory: null,
        byType: null,
        _cacheTime: 0
    };
    
    // 预计算统计
    featuredItems.forEach(function(item) {
        stats.totalStars += item.stars;
    });
    
    // 缓存计算结果（每分钟刷新一次）
    function _recalcStats() {
        stats.byCategory = Object.keys(Categories).reduce(function(acc, cat) {
            acc[cat] = featuredItems.filter(function(i) { return i.tags.indexOf(cat) !== -1; }).length;
            return acc;
        }, {});
        
        stats.byType = Object.keys(ItemType).reduce(function(acc, type) {
            acc[type] = featuredItems.filter(function(i) { return i.type === ItemType[type]; }).length;
            return acc;
        }, {});
        
        stats._cacheTime = Date.now();
    }
    
    // 初始化缓存
    _recalcStats();
    
    // ===== 获取所有项目 =====
    function getAll(options) {
        options = options || {};
        var items = featuredItems.slice();
        
        if (options.type) {
            items = items.filter(function(i) { return i.type === options.type; });
        }
        
        if (options.tag) {
            items = items.filter(function(i) { return i.tags.indexOf(options.tag) !== -1; });
        }
        
        if (options.query) {
            var q = options.query.toLowerCase();
            items = items.filter(function(i) {
                return i.name.toLowerCase().indexOf(q) !== -1 ||
                       i.oneLine.toLowerCase().indexOf(q) !== -1 ||
                       i.description.toLowerCase().indexOf(q) !== -1;
            });
        }
        
        if (options.sort === 'stars') {
            items.sort(function(a, b) { return b.stars - a.stars; });
        } else if (options.sort === 'featured') {
            items.sort(function(a, b) { return (b.featured ? 1 : 0) - (a.featured ? 1 : 0); });
        }
        
        if (options.limit) {
            items = items.slice(0, options.limit);
        }
        
        return items;
    }
    
    // ===== 获取精选项目 =====
    function getFeatured(limit) {
        return featuredItems.filter(function(i) { return i.featured; }).slice(0, limit || 6);
    }
    
    // ===== 获取 Hot 项目 =====
    function getHot(limit) {
        return featuredItems.slice().sort(function(a, b) { return b.stars - a.stars; }).slice(0, limit || 5);
    }
    
    // ===== 获取分类 =====
    function getCategories() {
        return Categories;
    }
    
    // ===== 获取某分类的项目 =====
    function getByCategory(category, limit) {
        return getAll({ tag: category, sort: 'stars', limit: limit });
    }
    
    // ===== 获取统计（使用缓存）=====
    function getStats() {
        // 每分钟刷新缓存
        if (Date.now() - stats._cacheTime > 60000) {
            _recalcStats();
        }
        
        return {
            totalItems: featuredItems.length,
            totalStars: stats.totalStars,
            totalViews: stats.totalViews,
            byCategory: stats.byCategory,
            byType: stats.byType
        };
    }
    
    function submitContribution(contribution) {
        var item = {
            id: 'user_' + Date.now(),
            type: contribution.type || ItemType.TOOL,
            name: contribution.name,
            oneLine: contribution.oneLine || contribution.name + ' 的简介',
            description: contribution.description || '',
            tags: contribution.tags || [],
            stars: 0,
            author: '社区用户',
            url: contribution.url || null,
            featured: false,
            featuredDate: null,
            usage: contribution.usage || '',
            userId: contribution.userId || 'anonymous',
            userName: contribution.userName || '匿名用户',
            submittedAt: Date.now(),
            status: 'pending'
        };
        userContributions.push(item);
        Store.set('discover_contributions', userContributions);
        EventBus.emit('discover:contribution:submitted', item);
        console.log('[Discover] 新提交:', item.name);
        return item;
    }
    
    function getContributions(status) {
        if (status) {
            return userContributions.filter(function(c) { return c.status === status; });
        }
        return userContributions;
    }
    
    function loadContributions() {
        var saved = Store.get('discover_contributions') || [];
        userContributions = saved;
        return saved;
    }
    
    function star(itemId) {
        var item = featuredItems.find(function(i) { return i.id === itemId; });
        if (item) {
            item.stars++;
            return item;
        }
        return null;
    }
    
    function search(query, options) {
        return getAll(Object.assign({ query: query }, options || {}));
    }
    
    function toMarkdown(item) {
        var typeName = {
            'skill': '🛠️ 技能',
            'tool': '🔧 工具',
            'template': '📝 模板',
            'knowledge': '📚 知识'
        };
        var md = '### ' + item.name + '\n\n';
        md += '> ' + item.oneLine + '\n\n';
        md += '**类型**: ' + (typeName[item.type] || item.type) + '\n\n';
        md += item.description + '\n\n';
        md += '**标签**: ' + item.tags.map(function(t) {
            return Categories[t] ? Categories[t].name : t;
        }).join(' | ') + '\n\n';
        md += '**使用**: `' + item.usage + '`\n\n';
        md += '⭐ ' + item.stars + ' | by @' + item.author + '\n';
        return md;
    }
    
    return {
        Type: ItemType,
        getAll: getAll,
        getFeatured: getFeatured,
        getHot: getHot,
        getByCategory: getByCategory,
        search: search,
        getCategories: getCategories,
        submitContribution: submitContribution,
        getContributions: getContributions,
        loadContributions: loadContributions,
        star: star,
        toMarkdown: toMarkdown,
        getStats: getStats
    };
})();
