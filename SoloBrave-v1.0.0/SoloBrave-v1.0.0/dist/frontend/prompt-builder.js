/**
 * Solo Brave Prompt Builder - 模块化提示词构建器
 * 
 * 组合各种模块生成高质量提示词
 */

var PromptBuilder = (function() {
    'use strict';
    
    // ===== 提示词模板库 =====
    var templates = {};
    
    // ===== 内置模板 =====
    var builtInTemplates = {
        
        // ===== 基础模板 =====
        'system_prompt': {
            name: '系统提示词',
            description: '生成基础系统提示词',
            icon: '⚙️',
            fields: [
                { name: 'name', label: '名字', type: 'text', default: '小一' },
                { name: 'personality', label: '性格', type: 'textarea', default: '专业、友善、乐于助人' },
                { name: 'abilities', label: '能力', type: 'textarea', default: '搜索信息、回答问题、编写代码' },
                { name: 'constraints', label: '约束', type: 'textarea', default: '不知道就说不知道，不确定要确认' }
            ],
            generate: function(fields) {
                return '你叫 ' + fields.name + '，一个' + fields.personality + '的 AI 助手。\n\n' +
                       '## 你的能力\n' + fields.abilities + '\n\n' +
                       '## 约束\n' + fields.constraints;
            }
        },
        
        // ===== 角色扮演 =====
        'roleplay': {
            name: '角色扮演',
            description: '生成角色扮演提示词',
            icon: '🎭',
            fields: [
                { name: 'role', label: '角色', type: 'text', required: true, placeholder: '如：资深产品经理' },
                { name: 'style', label: '表达风格', type: 'textarea', default: '专业、简洁、有条理' },
                { name: 'context', label: '背景', type: 'textarea', placeholder: '角色所处的环境和背景' },
                { name: 'goal', label: '目标', type: 'textarea', placeholder: '角色要达成的目标' }
            ],
            generate: function(fields) {
                return '你扮演一个' + fields.role + '。\n\n' +
                       '## 表达风格\n' + fields.style + '\n\n' +
                       '## 背景\n' + (fields.context || '普通的工作场景') + '\n\n' +
                       '## 目标\n' + (fields.goal || '帮助用户解决问题');
            }
        },
        
        // ===== 头脑风暴 =====
        'brainstorm': {
            name: '头脑风暴',
            description: '生成引导头脑风暴的提示词',
            icon: '💡',
            fields: [
                { name: 'topic', label: '主题', type: 'text', required: true, placeholder: '要讨论的主题' },
                { name: 'count', label: '想法数量', type: 'number', default: 5 },
                { name: 'diversity', label: '多样性', type: 'select', options: ['高', '中', '低'], default: '高' }
            ],
            generate: function(fields) {
                var diversity = fields.diversity === '高' ? '打破常规' : (fields.diversity === '中' ? '适度创新' : '稳健方案');
                return '## 头脑风暴：' + fields.topic + '\n\n' +
                       '请生成至少 ' + fields.count + ' 个想法，要求：\n' +
                       '- ' + diversity + '\n' +
                       '- 每个想法包含：标题、简述、优缺点\n' +
                       '- 最后的 TOP 3 推荐\n\n' +
                       '## 格式\n```\n1. [标题]\n   简述：xxx\n   优点：xxx\n   缺点：xxx\n```';
            }
        },
        
        // ===== 代码审查 =====
        'code_review': {
            name: '代码审查',
            description: '生成代码审查提示词',
            icon: '🔍',
            fields: [
                { name: 'language', label: '语言', type: 'text', default: 'JavaScript' },
                { name: 'focus', label: '关注点', type: 'multiselect', options: ['性能', '安全', '可读性', '最佳实践'], default: ['性能', '安全'] },
                { name: 'strict', label: '严格程度', type: 'select', options: ['宽松', '适中', '严格'], default: '适中' }
            ],
            generate: function(fields) {
                var strict = {
                    '宽松': '指出明显问题，鼓励为主',
                    '适中': '发现常见问题，给出建议',
                    '严格': '全面审查，包括潜在风险'
                };
                
                return '## 代码审查 (' + fields.language + ')\n\n' +
                       '严格程度：' + fields.strict + ' (' + strict[fields.strict] + ')\n\n' +
                       '关注点：' + fields.focus.join('、') + '\n\n' +
                       '## 审查清单\n' +
                       fields.focus.map(function(f) {
                           return '- [ ] ' + f;
                       }).join('\n') + '\n\n' +
                       '## 输出格式\n```\n## 问题\n| 级别 | 位置 | 问题 | 建议 |\n|------|------|------|------|\n| 🔴 高 | xxx | xxx | xxx |\n\n## 总结\n- 总计：X 个问题\n- 需要修改：X\n```';
            }
        },
        
        // ===== 需求分析 =====
        'requirements': {
            name: '需求分析',
            description: '生成需求分析提示词',
            icon: '📋',
            fields: [
                { name: 'product', label: '产品名称', type: 'text', placeholder: '产品名称' },
                { name: 'type', label: '需求类型', type: 'select', options: ['功能需求', '非功能需求', '业务需求'], default: '功能需求' },
                { name: 'priority', label: '优先级', type: 'select', options: ['P0-紧急', 'P1-重要', 'P2-普通', 'P3-可延后'], default: 'P1-重要' }
            ],
            generate: function(fields) {
                return '## 需求分析：' + (fields.product || '产品') + '\n\n' +
                       '类型：' + fields.type + '\n' +
                       '优先级：' + fields.priority + '\n\n' +
                       '## 分析维度\n' +
                       '1. **用户故事** - 谁、想要什么、为什么\n' +
                       '2. **功能分解** - 主要流程、子功能\n' +
                       '3. **验收标准** - 怎样算完成\n' +
                       '4. **依赖关系** - 上下游依赖\n\n' +
                       '## 格式\n```markdown\n### 用户故事\nAS A [角色]\nI WANT [功能]\nSO THAT [价值]\n\n### 功能清单\n- [ ] 功能1\n- [ ] 功能2\n```';
            }
        },
        
        // ===== 调试分析 =====
        'debug': {
            name: '调试分析',
            description: '生成系统调试提示词',
            icon: '🔧',
            fields: [
                { name: 'error', label: '错误信息', type: 'textarea', required: true, placeholder: '粘贴错误信息' },
                { name: 'context', label: '上下文', type: 'textarea', placeholder: '相关代码或配置' },
                { name: 'steps', label: '已尝试的步骤', type: 'textarea', placeholder: '已经试过的解决方法' }
            ],
            generate: function(fields) {
                return '## 调试请求\n\n' +
                       '### 错误信息\n```\n' + (fields.error || '[错误信息]') + '\n```\n\n' +
                       '### 上下文\n```\n' + (fields.context || '[相关代码/配置]') + '\n```\n\n' +
                       '### 已尝试\n' + (fields.steps || '- 无') + '\n\n' +
                       '## 请分析\n' +
                       '1. 根因是什么？\n' +
                       '2. 解决方案是什么？\n' +
                       '3. 如何避免类似问题？';
            }
        },
        
        // ===== 总结摘要 =====
        'summary': {
            name: '总结摘要',
            description: '生成内容总结提示词',
            icon: '📝',
            fields: [
                { name: 'length', label: '总结长度', type: 'select', options: ['简短', '中等', '详细'], default: '中等' },
                { name: 'style', label: '风格', type: 'select', options: ['客观', '友好', '专业'], default: '客观' },
                { name: 'format', label: '格式', type: 'select', options: ['段落', '要点', '表格'], default: '要点' }
            ],
            generate: function(fields) {
                var lengthMap = {
                    '简短': '3-5句话',
                    '中等': '1段话 + 3-5个要点',
                    '详细': '完整总结 + 所有要点 + 结论'
                };
                
                var formatMap = {
                    '段落': '使用自然段落',
                    '要点': '使用 bullet points',
                    '表格': '使用 Markdown 表格'
                };
                
                return '## 总结任务\n\n' +
                       '长度：' + lengthMap[fields.length] + '\n' +
                       '风格：' + fields.style + '、' + formatMap[fields.format] + '\n\n' +
                       '## 输出格式\n' +
                       '### 一句话总结\n[核心内容]\n\n' +
                       '### 详细总结\n[正文]\n\n' +
                       '### 关键要点\n- 要点1\n- 要点2\n- 要点3';
            }
        },
        
        // ===== 写作助手 =====
        'writing': {
            name: '写作助手',
            description: '生成写作辅助提示词',
            icon: '✍️',
            fields: [
                { name: 'type', label: '文体', type: 'select', options: ['邮件', '报告', '文档', '社交媒体'], default: '文档' },
                { name: 'tone', label: '语气', type: 'select', options: ['正式', '中性', '轻松'], default: '中性' },
                { name: 'length', label: '长度', type: 'select', options: ['短', '中', '长'], default: '中' }
            ],
            generate: function(fields) {
                // 提取风格描述为独立函数
                var styleDesc = this._getToneDescription(fields.tone);
                
                return '## 写作任务：' + fields.type + '\n\n' +
                       '语气：' + fields.tone + '\n' +
                       '目标长度：' + fields.length + '\n\n' +
                       '## 要求\n' +
                       '1. 语言简洁明了\n' +
                       '2. 逻辑清晰\n' +
                       '3. 突出重点\n\n' +
                       '## 风格示例\n' +
                       styleDesc;
            },
            
            _getToneDescription: function(tone) {
                var descriptions = {
                    '正式': '使用正式用语，避免口语化表达',
                    '轻松': '使用友好、轻松的语言风格'
                };
                return descriptions[tone] || '平衡专业性和可读性';
            }
        },
        
        // ===== 学习辅导 =====
        'learning': {
            name: '学习辅导',
            description: '生成学习辅导提示词',
            icon: '📚',
            fields: [
                { name: 'subject', label: '学科', type: 'text', required: true, placeholder: '如：Python编程' },
                { name: 'level', label: '学习者水平', type: 'select', options: ['零基础', '入门', '进阶', '高级'], default: '入门' },
                { name: 'goal', label: '学习目标', type: 'textarea', placeholder: '想达成的目标' }
            ],
            generate: function(fields) {
                return '## 学习辅导：' + fields.subject + '\n\n' +
                       '学习者水平：' + fields.level + '\n' +
                       '目标：' + (fields.goal || '掌握基础知识') + '\n\n' +
                       '## 教学风格\n' +
                       '- 循序渐进，由浅入深\n' +
                       '- 结合实例讲解\n' +
                       '- 鼓励提问和实践\n\n' +
                       '## 格式\n' +
                       '1. 概念解释（简单易懂）\n' +
                       '2. 示例代码/案例\n' +
                       '3. 练习题\n' +
                       '4. 常见错误提醒';
            }
        }
    };
    
    // ===== 模块库 =====
    var modules = {
        'context': {
            name: '上下文',
            icon: '📎',
            content: '## 上下文\n以下是当前对话的上下文信息：\n{context}'
        },
        'memory': {
            name: '记忆',
            icon: '🧠',
            content: '## 相关记忆\n{memory}'
        },
        'constraints': {
            name: '约束',
            icon: '🚫',
            content: '## 约束条件\n{constraints}'
        },
        'output_format': {
            name: '输出格式',
            icon: '📐',
            content: '## 输出格式\n{format}'
        },
        'examples': {
            name: '示例',
            icon: '📖',
            content: '## 示例\n{examples}'
        }
    };
    
    // ===== 初始化 =====
    function init() {
        // 注册内置模板
        Object.keys(builtInTemplates).forEach(function(id) {
            registerTemplate(id, builtInTemplates[id]);
        });
        
        console.log('[PromptBuilder] 已加载 ' + Object.keys(templates).length + ' 个模板');
        return templates;
    }
    
    // ===== 注册模板 =====
    function registerTemplate(id, config) {
        templates[id] = config;
    }
    
    // ===== 生成提示词 =====
    function generate(templateId, fields) {
        var template = templates[templateId];
        if (!template) {
            return { error: 'Template not found: ' + templateId };
        }
        
        try {
            var prompt = template.generate(fields || {});
            return {
                success: true,
                prompt: prompt,
                template: templateId
            };
        } catch (e) {
            return {
                success: false,
                error: e.message
            };
        }
    }
    
    // ===== 组合提示词 =====
    function compose(options) {
        options = options || {};
        var parts = [];
        
        // 1. 系统提示
        if (options.system) {
            parts.push(options.system);
        }
        
        // 2. 角色定义
        if (options.role) {
            parts.push('## 角色\n' + options.role);
        }
        
        // 3. 上下文
        if (options.context) {
            parts.push('## 上下文\n' + options.context);
        }
        
        // 4. 任务描述
        if (options.task) {
            parts.push('## 任务\n' + options.task);
        }
        
        // 5. 约束条件
        if (options.constraints) {
            parts.push('## 约束\n' + options.constraints);
        }
        
        // 6. 示例
        if (options.examples) {
            parts.push('## 示例\n' + options.examples);
        }
        
        // 7. 输出格式
        if (options.format) {
            parts.push('## 输出格式\n' + options.format);
        }
        
        return parts.join('\n\n');
    }
    
    // ===== 增强提示词 =====
    function enhance(prompt, options) {
        options = options || {};
        var enhanced = prompt;
        
        // 添加思维链
        if (options.chainOfThought) {
            enhanced += '\n\n## 思考过程\n请一步步分析问题，展示你的思考推理过程。';
        }
        
        // 添加自我检查
        if (options.selfCheck) {
            enhanced += '\n\n## 自我检查\n在回答前，请检查：\n1. 我的回答是否准确？\n2. 是否有遗漏重要信息？\n3. 表达是否清晰？';
        }
        
        // 添加置信度
        if (options.confidence) {
            enhanced += '\n\n## 置信度\n如果不确定，请说明置信度：\n- 高置信度 (>90%)\n- 中置信度 (60-90%)\n- 低置信度 (<60%)';
        }
        
        return enhanced;
    }
    
    // ===== 获取模板 =====
    function getTemplate(id) {
        return templates[id] || null;
    }
    
    function getAllTemplates() {
        return Object.values(templates);
    }
    
    function getTemplateCategories() {
        var categories = {};
        Object.values(templates).forEach(function(t) {
            var cat = t.category || 'general';
            if (!categories[cat]) categories[cat] = [];
            categories[cat].push(t);
        });
        return categories;
    }
    
    // ===== 导出 API =====
    return {
        init: init,
        templates: templates,
        generate: generate,
        compose: compose,
        enhance: enhance,
        getTemplate: getTemplate,
        getAllTemplates: getAllTemplates,
        getTemplateCategories: getTemplateCategories,
        modules: modules
    };
})();
