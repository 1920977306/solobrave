/**
 * Solo Brave - 员工模型管理
 * 
 * 安全修复:
 * - 统一使用 Store API（移除 AppStorage/Storage 混用）
 * - 添加 HTML 转义防止 XSS
 * - 重构为可维护的标准模块
 */

var EmployeeModels = {
    // ===== 可用模型列表 =====
    chinaModels: [
        { id: 'qwen-plus', name: '通义千问 Plus' },
        { id: 'deepseek-chat', name: 'DeepSeek Chat' },
        { id: 'minimax-chat', name: 'MiniMax Chat' }
    ],
    
    globalModels: [
        { id: 'gpt-4o', name: 'GPT-4o' },
        { id: 'claude-3.5', name: 'Claude 3.5' }
    ],
    
    // ===== 角色模板 =====
    templates: [
        { id: 'frontend', name: '前端工程师', emoji: '🧑‍💻', desc: 'Vue/React' },
        { id: 'backend', name: '后端工程师', emoji: '🧑‍💻', desc: 'Node/Java' },
        { id: 'product', name: '产品经理', emoji: '📋', desc: '需求分析' },
        { id: 'designer', name: '设计师', emoji: '🎨', desc: 'UI/UX' },
        { id: 'tester', name: '测试工程师', emoji: '🧪', desc: '测试' },
        { id: 'devops', name: '运维工程师', emoji: '🔧', desc: 'DevOps' }
    ],
    
    // ===== 角色映射 =====
    roleMap: {
        'frontend': '前端',
        'backend': '后端',
        'product': '产品',
        'designer': '设计',
        'tester': '测试',
        'devops': '运维'
    },
    
    // ===== 状态 =====
    employees: [],
    step: 1,
    data: {},
    
    // ===== 初始化 =====
    init: function() {
        // 统一使用 Store API
        var saved = Store.get('employees');
        this.employees = saved || [
            { id: 'e1', name: '小龙虾', emoji: '🦞', role: 'frontend', status: '在线', model: 'qwen-plus' },
            { id: 'e2', name: '大龙虾', emoji: '🦞', role: 'backend', status: '在线', model: 'deepseek-chat' },
            { id: 'e3', name: '章鱼哥', emoji: '🐙', role: 'product', status: '忙碌', model: 'qwen-turbo' },
            { id: 'e4', name: '海星', emoji: '⭐', role: 'tester', status: '在线', model: 'qwen-plus' },
            { id: 'e5', name: '鲨鱼', emoji: '🦈', role: 'devops', status: '离线', model: 'moonshot' }
        ];
        this.render();
    },
    
    // ===== 渲染员工列表 =====
    render: function() {
        var container = document.getElementById('employeeList');
        if (!container) return;
        
        var html = this.employees.map(function(e) {
            var role = this.roleMap[e.role] || e.role;
            var safeName = this._escapeHtml(e.name);
            var safeId = this._escapeAttr(e.id);
            
            return '<div class="emp" onclick="openChat(\'' + safeId + '\',\'employee\')">' +
                '<span class="emp-st">' + e.emoji + '</span>' +
                '<div class="emp-info">' +
                    '<span class="emp-name">' + safeName + '</span>' +
                    '<span class="emp-role">' + role + '</span>' +
                '</div>' +
                '<span class="emp-status">' + e.status + '</span>' +
            '</div>';
        }.bind(this)).join('');
        
        container.innerHTML = html;
    },
    
    // ===== 打开添加员工弹窗 =====
    openModal: function() {
        var existing = document.getElementById('addEmpModal');
        if (existing) existing.remove();
        
        this.step = 1;
        this.data = {
            name: '',
            emoji: '👤',
            role: 'frontend',
            model: 'qwen-plus'
        };
        
        var modal = document.createElement('div');
        modal.className = 'modal-overlay';
        modal.id = 'addEmpModal';
        document.body.appendChild(modal);
        
        this.renderStep();
        
        requestAnimationFrame(function() {
            modal.classList.add('show');
        });
    },
    
    // ===== 渲染步骤内容 =====
    renderStep: function() {
        var modal = document.getElementById('addEmpModal');
        if (!modal) return;
        
        var s = this.step;
        var d = this.data;
        var t = this.templates;
        
        // 步骤指示器
        var indicator = s === 1 ? 
            '<b style="color:#FF6B35">1</b> - 2 - 3' :
            s === 2 ? 
                '✓ - <b style="color:#FF6B35">2</b> - 3' :
                '✓ - ✓ - <b style="color:#FF6B35">3</b>';
        
        // 标题
        var title = s === 1 ? '添加新员工' :
            s === 2 ? '选择角色' : '配置模型';
        
        // 内容
        var body = '';
        
        if (s === 1) {
            // 步骤1: 基本信息
            var emojis = ['🦞', '👨‍💻', '🐙', '⭐', '🦈', '🤖', '👤', '👩‍💻'];
            var emojiHtml = emojis.map(function(e) {
                return '<span class="eo' + (d.emoji === e ? ' sel' : '') + '" onclick="EM.selectEmoji(\'' + this._escapeAttr(e) + '\')">' + e + '</span>';
            }.bind(this)).join('');
            
            body = '<div class="fg">' +
                '<label>姓名</label>' +
                '<input id="wName" value="' + this._escapeAttr(d.name) + '" placeholder="输入姓名">' +
            '</div>' +
            '<div class="fg">' +
                '<label>头像</label>' +
                '<div class="ep">' + emojiHtml + '</div>' +
            '</div>';
            
        } else if (s === 2) {
            // 步骤2: 选择角色
            var roleHtml = t.map(function(x) {
                return '<div class="rc' + (d.role === x.id ? ' sel' : '') + '" onclick="EM.selectRole(\'' + this._escapeAttr(x.id) + '\')">' +
                    '<b>' + x.emoji + '</b>' +
                    '<span>' + x.name + '</span>' +
                '</div>';
            }.bind(this)).join('');
            
            body = '<div class="rg">' + roleHtml + '</div>';
            
        } else {
            // 步骤3: 选择模型
            var modelHtml = this.chinaModels.map(function(m) {
                return '<option' + (d.model === m.id ? ' selected' : '') + '>' + m.name + '</option>';
            }).join('');
            
            body = '<div class="fg">' +
                '<label>AI模型</label>' +
                '<select id="wModel">' + modelHtml + '</select>' +
            '</div>';
        }
        
        // 按钮
        var footer = '';
        if (s === 1) {
            footer = '<button onclick="EM.close()">取消</button>' +
                '<button class="primary" onclick="EM.next()">下一步</button>';
        } else if (s === 2) {
            footer = '<button onclick="EM.prev()">上一步</button>' +
                '<button class="primary" onclick="EM.next()">下一步</button>';
        } else {
            footer = '<button onclick="EM.prev()">上一步</button>' +
                '<button class="primary" onclick="EM.finish()">完成</button>';
        }
        
        modal.innerHTML = 
            '<div class="mc">' +
                '<div class="mh">' +
                    '<h3>' + title + '</h3>' +
                    '<div class="si">' + indicator + '</div>' +
                '</div>' +
                '<div class="mb">' + body + '</div>' +
                '<div class="mf">' + footer + '</div>' +
            '</div>';
    },
    
    // ===== 选择头像 =====
    selectEmoji: function(emoji) {
        this.data.emoji = emoji;
        this.renderStep();
    },
    
    // ===== 选择角色 =====
    selectRole: function(roleId) {
        this.data.role = roleId;
        this.renderStep();
    },
    
    // ===== 下一步 =====
    next: function() {
        if (this.step === 1) {
            var nameInput = document.getElementById('wName');
            if (nameInput) {
                this.data.name = nameInput.value.trim();
            }
            if (!this.data.name) {
                alert('请输入姓名');
                return;
            }
        } else if (this.step === 2) {
            var modelInput = document.getElementById('wModel');
            if (modelInput) {
                this.data.model = modelInput.value;
            }
        }
        
        this.step++;
        this.renderStep();
    },
    
    // ===== 上一步 =====
    prev: function() {
        if (this.step === 2) {
            var nameInput = document.getElementById('wName');
            if (nameInput) {
                this.data.name = nameInput.value.trim();
            }
        } else if (this.step === 3) {
            var modelInput = document.getElementById('wModel');
            if (modelInput) {
                this.data.model = modelInput.value;
            }
        }
        
        this.step--;
        this.renderStep();
    },
    
    // ===== 完成添加 =====
    finish: function() {
        var modelInput = document.getElementById('wModel');
        if (modelInput) {
            this.data.model = modelInput.value;
        }
        
        var newEmployee = {
            id: Store.generateId('emp'),
            name: this.data.name,
            emoji: this.data.emoji,
            role: this.data.role,
            model: this.data.model,
            status: '在线'
        };
        
        this.employees.push(newEmployee);
        Store.set('employees', this.employees);
        
        this.render();
        this.close();
    },
    
    // ===== 关闭弹窗 =====
    close: function() {
        var modal = document.getElementById('addEmpModal');
        if (modal) {
            modal.classList.remove('show');
            setTimeout(function() { modal.remove(); }, 300);
        }
    },
    
    // ===== HTML 转义 =====
    _escapeHtml: function(text) {
        if (!text) return '';
        var div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },
    
    // ===== 属性转义 =====
    _escapeAttr: function(value) {
        if (!value) return '';
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#x27;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    }
};
