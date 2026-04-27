/**
 * Solo Brave Skills System - 技能系统
 * 
 * 管理 AI 技能，支持从 skills/ 目录加载
 */

var Skills = (function() {
    'use strict';
    
    // ===== 技能数据结构 =====
    var skills = {};
    var skillCategories = {};
    var currentSkill = null;
    var usageHistory = [];
    
    // ===== 内置技能目录 =====
    var builtInSkills = {
        'brainstorming': {
            name: '头脑风暴',
            category: 'design',
            tags: ['设计', '规划', '需求'],
            description: '将想法转化为完整设计，通过自然协作对话',
            icon: '💡',
            trigger: '/brainstorm'
        },
        'systematic-debugging': {
            name: '系统调试',
            category: 'debug',
            tags: ['调试', 'bug', '修复'],
            description: '4步根因分析，找到问题根源再修复',
            icon: '🔍',
            trigger: '/debug'
        },
        'test-driven-development': {
            name: '测试驱动开发',
            category: 'development',
            tags: ['TDD', '测试', '重构'],
            description: '红绿重构循环，先写测试再实现',
            icon: '🧪',
            trigger: '/tdd'
        },
        'using-git-worktrees': {
            name: 'Git Worktree',
            category: 'git',
            tags: ['git', '分支', '并行'],
            description: '使用 git worktree 并行开发分支',
            icon: '🌳',
            trigger: '/worktree'
        },
        'writing-plans': {
            name: '编写计划',
            category: 'planning',
            tags: ['计划', '任务', '实施'],
            description: '制定详细的实施计划',
            icon: '📋',
            trigger: '/plan'
        },
        'writing-skills': {
            name: '编写技能',
            category: 'meta',
            tags: ['技能', '创建', '定义'],
            description: '创建新的 AI 技能定义',
            icon: '🛠️',
            trigger: '/newskill'
        },
        'safe-editing': {
            name: '安全编辑',
            category: 'safety',
            tags: ['编辑', '防损坏', '备份'],
            description: '防止文件越修越坏的编辑规范',
            icon: '🛡️',
            trigger: '/safe'
        },
        'subagent-driven-development': {
            name: '子代理开发',
            category: 'development',
            tags: ['代理', '协作', '审查'],
            description: '双阶段审查的子代理开发模式',
            icon: '🤖',
            trigger: '/subagent'
        }
    };
    
    // ===== 初始化 =====
    function init() {
        // 从 localStorage 恢复
        var saved = Store.get('skills_usage') || [];
        if (saved && Array.isArray(saved)) {
            usageHistory = saved;
        }
        
        // 加载内置技能
        Object.keys(builtInSkills).forEach(function(key) {
            var skill = builtInSkills[key];
            registerSkill(key, skill);
        });
        
        // 通过 EventBus 通知（使用 Events 常量）
        if (typeof EventBus !== 'undefined' && typeof Events !== 'undefined') {
            EventBus.emit(Events.SKILLS_LOADED, { count: Object.keys(skills).length });
        }
        // console.log('[Skills] 已加载 ' + Object.keys(skills).length + ' 个技能');
        return skills;
    }
    
    // ===== 注册技能 =====
    function registerSkill(id, config) {
        skills[id] = {
            id: id,
            name: config.name || id,
            description: config.description || '',
            category: config.category || 'general',
            tags: config.tags || [],
            icon: config.icon || '⚡',
            trigger: config.trigger || '/' + id,
            content: config.content || getDefaultContent(id, config),
            usageCount: getUsageCount(id),
            lastUsed: null
        };
        
        // 分类
        if (!skillCategories[skills[id].category]) {
            skillCategories[skills[id].category] = [];
        }
        if (!skillCategories[skills[id].category].includes(id)) {
            skillCategories[skills[id].category].push(id);
        }
    }
    
    // ===== 获取默认技能内容 =====
    function getDefaultContent(id, config) {
        var templates = {
            'brainstorming': `## 头脑风暴技能

在开始任何创意工作前使用此技能：
- 创建功能/组件
- 添加功能/修改行为

### 流程
1. 探索项目上下文
2. 提供视觉伴侣（如需要）
3. 逐一提问澄清需求
4. 提出 2-3 个方案
5. 展示设计
6. 获得用户批准
7. 编写设计文档
8. 过渡到实施计划

### 关键原则
- **一次一问** - 不要用多个问题淹没用户
- **多选优先** - 多选题更容易回答
- **YAGNI 严格** - 从所有设计中移除不必要的功能
- **增量验证** - 展示设计，获得批准后再继续`,
            
            'systematic-debugging': `## 系统调试技能

遇到任何 bug、测试失败或意外行为时使用。

### 核心铁律
**先找根因，再尝试修复**

### 四阶段流程
1. **根因调查** - 读错误信息、重现、检查变更
2. **模式分析** - 找相似代码、对比差异
3. **假设测试** - 形成单一假设、最小测试
4. **实现修复** - 创建失败测试 → 修复 → 验证

### 红旗
- "快速修复"
- "先试试改 X"
- "跳过测试"
- **连续尝试 3+ 次修复失败** → 质疑架构`,
            
            'test-driven-development': `## 测试驱动开发

### 红绿重构循环
1. **红** - 写一个失败的测试
2. **绿** - 写最少代码让测试通过
3. **重构** - 改进代码，保持测试通过

### 原则
- 测试先行
- 保持测试快速
- 测试隔离
- 命名清晰`,
            
            'safe-editing': `## 安全编辑规范

### 修改前必做
- [ ] 备份当前文件
- [ ] 明确修改范围
- [ ] 检查相关代码

### 修改后必做
- [ ] 立即验证
- [ ] 不要连续改多次
- [ ] 有问题立刻回退

### 紧急回滚
\`\`\`bash
git checkout HEAD~1 -- <file>
\`\`\``,
            
            'writing-plans': `## 编写计划技能

### 计划结构
1. **概述** - 目标、范围
2. **任务列表** - 详细步骤
3. **验收标准** - 怎么算完成
4. **风险评估** - 潜在问题

### 格式
- 使用清单格式
- 每个任务明确负责人
- 估算时间
- 标记依赖关系`
        };
        
        return templates[id] || `## ${config.name}\n\n${config.description}`;
    }
    
    // ===== 获取技能 =====
    function get(id) {
        return skills[id] || null;
    }
    
    function getAll() {
        return Object.values(skills);
    }
    
    function getByCategory(category) {
        var categorySkills = skillCategories[category];
        if (!categorySkills) return [];
        return categorySkills.map(function(id) { return skills[id]; }).filter(Boolean);
    }
    
    function getCategories() {
        return Object.keys(skillCategories);
    }
    
    // ===== 搜索技能 =====
    function search(query) {
        query = query.toLowerCase();
        var results = [];
        
        Object.values(skills).forEach(function(skill) {
            var score = 0;
            
            // 名称匹配
            if (skill.name.toLowerCase().indexOf(query) !== -1) {
                score += 10;
            }
            // 描述匹配
            if (skill.description.toLowerCase().indexOf(query) !== -1) {
                score += 5;
            }
            // 标签匹配
            if (skill.tags.some(function(t) { return t.toLowerCase().indexOf(query) !== -1; })) {
                score += 3;
            }
            // 触发词匹配
            if (skill.trigger && skill.trigger.toLowerCase().indexOf(query) !== -1) {
                score += 8;
            }
            
            if (score > 0) {
                results.push({ skill: skill, score: score });
            }
        });
        
        // 排序
        results.sort(function(a, b) { return b.score - a.score; });
        
        return results.map(function(r) { return r.skill; });
    }
    
    // ===== 使用技能 =====
    function use(id) {
        var skill = skills[id];
        if (!skill) return null;
        
        // 更新使用统计
        skill.usageCount++;
        skill.lastUsed = Date.now();
        currentSkill = skill;
        
        // 记录历史（保留最近 50 条）
        usageHistory.unshift({
            skillId: id,
            timestamp: Date.now()
        });
        if (usageHistory.length > 50) {
            usageHistory = usageHistory.slice(0, 50);
        }
        
        // 保存到 storage
        saveUsageStats();
        
        // 发送事件（使用 Events 常量）
        if (typeof Events !== 'undefined') {
            EventBus.emit(Events.SKILL_ACTIVATED, skill);
        }
        
        return skill;
    }
    
    // ===== 停止使用技能 =====
    function deactivate() {
        if (currentSkill) {
            if (typeof Events !== 'undefined') {
                EventBus.emit('skill:deactivated', currentSkill);
            }
            currentSkill = null;
        }
    }
    
    // ===== 获取当前技能 =====
    function getCurrent() {
        return currentSkill;
    }
    
    // ===== 获取使用统计 =====
    function getUsageStats() {
        var stats = {};
        Object.values(skills).forEach(function(skill) {
            stats[skill.id] = {
                count: skill.usageCount,
                lastUsed: skill.lastUsed
            };
        });
        return stats;
    }
    
    function getUsageCount(id) {
        var history = Store.get('skills_stats') || {};
        return history[id] || 0;
    }
    
    function saveUsageStats() {
        var stats = {};
        Object.values(skills).forEach(function(skill) {
            stats[skill.id] = skill.usageCount;
        });
        Store.set('skills_stats', stats);
    }
    
    // ===== 获取常用技能 =====
    function getMostUsed(limit) {
        limit = limit || 5;
        var all = Object.values(skills);
        all.sort(function(a, b) { return b.usageCount - a.usageCount; });
        return all.slice(0, limit);
    }
    
    // ===== 获取最近使用 =====
    function getRecentlyUsed(limit) {
        limit = limit || 5;
        var recent = [];
        var seen = {};
        
        for (var i = 0; i < usageHistory.length && recent.length < limit; i++) {
            var item = usageHistory[i];
            if (!seen[item.skillId]) {
                seen[item.skillId] = true;
                var skill = skills[item.skillId];
                if (skill) {
                    recent.push({
                        skill: skill,
                        timestamp: item.timestamp
                    });
                }
            }
        }
        
        return recent;
    }
    
    // ===== 检测触发词 =====
    function detectTrigger(text) {
        if (!text || typeof text !== 'string') return null;
        
        // 检查斜杠命令
        var slashMatch = text.match(/^\/(\w+)/);
        if (slashMatch) {
            var cmd = slashMatch[1].toLowerCase();
            // 精确匹配
            for (var id in skills) {
                var trigger = skills[id].trigger;
                if (trigger && trigger.replace('/', '') === cmd) {
                    return skills[id];
                }
            }
        }
        
        return null;
    }
    
    // ===== 格式化技能为提示词 =====
    function formatForPrompt(skillId) {
        var skill = skills[skillId];
        if (!skill) return '';
        
        return skill.content;
    }
    
    // ===== 添加自定义技能 =====
    function addCustomSkill(id, config) {
        if (skills[id]) {
            console.warn('[Skills] 技能 ' + id + ' 已存在');
            return false;
        }
        
        registerSkill(id, config);
        EventBus.emit('skill:added', skills[id]);
        return true;
    }
    
    // ===== 导出公共 API =====
    return {
        init: init,
        get: get,
        getAll: getAll,
        getByCategory: getByCategory,
        getCategories: getCategories,
        search: search,
        use: use,
        deactivate: deactivate,
        getCurrent: getCurrent,
        getUsageStats: getUsageStats,
        getMostUsed: getMostUsed,
        getRecentlyUsed: getRecentlyUsed,
        detectTrigger: detectTrigger,
        formatForPrompt: formatForPrompt,
        addCustomSkill: addCustomSkill
    };
})();

// ===== 初始化 =====
if (typeof window !== 'undefined') {
    window.addEventListener('DOMContentLoaded', function() {
        Skills.init();
    });
}
