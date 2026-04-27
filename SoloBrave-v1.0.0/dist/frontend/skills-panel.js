/**
 * Solo Brave Skills Panel - 技能面板 UI
 */

var SkillsPanel = (function() {
    'use strict';
    
    var isOpen = false;
    var currentView = 'list'; // 'list' | 'detail' | 'search'
    var selectedSkill = null;
    
    // ===== 渲染面板 =====
    function render() {
        var panel = document.getElementById('skillsPanel');
        if (!panel) return;
        
        panel.innerHTML = `
            <div class="skills-panel-header">
                <div class="skills-title">⚡ 技能库</div>
                <div class="skills-actions">
                    <button class="skills-btn" onclick="SkillsPanel.search()" title="搜索技能">🔍</button>
                    <button class="skills-btn" onclick="SkillsPanel.close()" title="关闭">✕</button>
                </div>
            </div>
            
            <div class="skills-search-bar" id="skillsSearchBar" style="display:none;">
                <input type="text" id="skillsSearchInput" placeholder="搜索技能..."
                    oninput="SkillsPanel.onSearch(this.value)">
            </div>
            
            <div class="skills-tabs">
                <button class="skills-tab active" onclick="SkillsPanel.switchTab('all')">全部</button>
                <button class="skills-tab" onclick="SkillsPanel.switchTab('recent')">最近</button>
                <button class="skills-tab" onclick="SkillsPanel.switchTab('popular')">常用</button>
            </div>
            
            <div class="skills-content" id="skillsContent">
                ${renderList()}
            </div>
            
            <div class="skills-footer">
                <span class="skills-count">${Object.keys(Skills.getAll()).length} 个技能</span>
            </div>
        `;
    }
    
    // ===== 渲染列表 =====
    function renderList(filter) {
        var allSkills = Skills.getAll();
        
        if (filter === 'recent') {
            var recent = Skills.getRecentlyUsed(10);
            allSkills = recent.map(function(r) { return r.skill; });
        } else if (filter === 'popular') {
            allSkills = Skills.getMostUsed(10);
        }
        
        if (allSkills.length === 0) {
            return '<div class="skills-empty">暂无技能</div>';
        }
        
        return allSkills.map(function(skill) {
            return `
                <div class="skill-item" onclick="SkillsPanel.showDetail('${skill.id}')">
                    <div class="skill-icon">${skill.icon}</div>
                    <div class="skill-info">
                        <div class="skill-name">${skill.name}</div>
                        <div class="skill-desc">${skill.description}</div>
                    </div>
                    ${skill.usageCount > 0 ? '<div class="skill-badge">x' + skill.usageCount + '</div>' : ''}
                </div>
            `;
        }).join('');
    }
    
    // ===== 渲染详情 =====
    function renderDetail(skill) {
        return `
            <div class="skill-detail-header">
                <button class="skills-btn" onclick="SkillsPanel.back()">← 返回</button>
            </div>
            
            <div class="skill-detail">
                <div class="skill-detail-icon">${skill.icon}</div>
                <div class="skill-detail-name">${skill.name}</div>
                <div class="skill-detail-desc">${skill.description}</div>
                
                <div class="skill-meta">
                    <span class="skill-tag">${skill.category}</span>
                    ${skill.tags.map(function(t) { return '<span class="skill-tag">' + t + '</span>'; }).join('')}
                </div>
                
                ${skill.trigger ? '<div class="skill-trigger">触发: <code>' + skill.trigger + '</code></div>' : ''}
                
                <div class="skill-usage">
                    使用次数: ${skill.usageCount}
                    ${skill.lastUsed ? ' · 上次: ' + formatTime(skill.lastUsed) : ''}
                </div>
                
                <div class="skill-content-preview">
                    ${skill.content.substring(0, 200)}...
                </div>
                
                <button class="skill-use-btn" onclick="SkillsPanel.activateSkill('${skill.id}')">
                    ⚡ 使用此技能
                </button>
            </div>
        `;
    }
    
    // ===== 格式化时间 =====
    function formatTime(timestamp) {
        var date = new Date(timestamp);
        var now = new Date();
        var diff = now - date;
        
        if (diff < 60000) return '刚刚';
        if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
        if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
        if (diff < 604800000) return Math.floor(diff / 86400000) + '天前';
        
        return date.toLocaleDateString();
    }
    
    // ===== 打开面板 =====
    function open() {
        if (!document.getElementById('skillsPanel')) {
            createPanel();
        }
        
        var panel = document.getElementById('skillsPanel');
        panel.classList.add('open');
        isOpen = true;
        render();
    }
    
    // ===== 关闭面板 =====
    function close() {
        var panel = document.getElementById('skillsPanel');
        if (panel) {
            panel.classList.remove('open');
        }
        isOpen = false;
        currentView = 'list';
        selectedSkill = null;
    }
    
    // ===== 切换 Tab =====
    function switchTab(tab) {
        document.querySelectorAll('.skills-tab').forEach(function(el) {
            el.classList.toggle('active', el.textContent === 
                (tab === 'all' ? '全部' : tab === 'recent' ? '最近' : '常用'));
        });
        
        var content = document.getElementById('skillsContent');
        if (content) {
            content.innerHTML = renderList(tab);
        }
    }
    
    // ===== 显示详情 =====
    function showDetail(skillId) {
        var skill = Skills.get(skillId);
        if (!skill) return;
        
        selectedSkill = skill;
        currentView = 'detail';
        
        var content = document.getElementById('skillsContent');
        if (content) {
            content.innerHTML = renderDetail(skill);
        }
    }
    
    // ===== 返回列表 =====
    function back() {
        currentView = 'list';
        selectedSkill = null;
        render();
    }
    
    // ===== 搜索 =====
    function search() {
        var searchBar = document.getElementById('skillsSearchBar');
        if (searchBar) {
            searchBar.style.display = searchBar.style.display === 'none' ? 'block' : 'none';
            if (searchBar.style.display === 'block') {
                document.getElementById('skillsSearchInput').focus();
            }
        }
    }
    
    // ===== 搜索输入 =====
    function onSearch(query) {
        var content = document.getElementById('skillsContent');
        if (!content) return;
        
        if (!query.trim()) {
            content.innerHTML = renderList();
            return;
        }
        
        var results = Skills.search(query);
        
        if (results.length === 0) {
            content.innerHTML = '<div class="skills-empty">没有找到匹配的技能</div>';
            return;
        }
        
        content.innerHTML = results.map(function(skill) {
            return `
                <div class="skill-item" onclick="SkillsPanel.showDetail('${skill.id}')">
                    <div class="skill-icon">${skill.icon}</div>
                    <div class="skill-info">
                        <div class="skill-name">${skill.name}</div>
                        <div class="skill-desc">${skill.description}</div>
                    </div>
                </div>
            `;
        }).join('');
    }
    
    // ===== 激活技能 =====
    function activateSkill(skillId) {
        var skill = Skills.use(skillId);
        if (skill) {
            EventBus.emit('toast:show', '⚡ 已激活技能: ' + skill.name);
            
            // 将技能内容发送到输入框（安全方式）
            var input = document.getElementById('inputArea') || document.getElementById('msgInput');
            if (input) {
                // 使用 textContent 安全设置内容
                if (input.setSelectionRange) {
                    input.value = skill.content || '';
                    input.setSelectionRange(input.value.length, input.value.length);
                } else {
                    input.value = skill.content || '';
                }
                input.focus();
            }
            
            close();
        }
    }
    
    // ===== 创建面板 DOM =====
    function createPanel() {
        var panel = document.createElement('div');
        panel.id = 'skillsPanel';
        panel.className = 'skills-panel';
        
        // 添加样式
        var style = document.createElement('style');
        style.textContent = `
            .skills-panel {
                position: fixed;
                right: -400px;
                top: 0;
                width: 380px;
                height: 100vh;
                background: #1e1e2e;
                border-left: 1px solid #333;
                z-index: 1000;
                transition: right 0.3s ease;
                display: flex;
                flex-direction: column;
            }
            .skills-panel.open {
                right: 0;
            }
            .skills-panel-header {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 16px;
                border-bottom: 1px solid #333;
            }
            .skills-title {
                font-size: 18px;
                font-weight: bold;
                color: #fff;
            }
            .skills-actions {
                display: flex;
                gap: 8px;
            }
            .skills-btn {
                background: transparent;
                border: none;
                color: #888;
                font-size: 16px;
                cursor: pointer;
                padding: 4px 8px;
                border-radius: 4px;
            }
            .skills-btn:hover {
                background: #333;
                color: #fff;
            }
            .skills-search-bar {
                padding: 12px 16px;
                border-bottom: 1px solid #333;
            }
            .skills-search-bar input {
                width: 100%;
                padding: 10px 12px;
                background: #2a2a3e;
                border: 1px solid #444;
                border-radius: 8px;
                color: #fff;
                font-size: 14px;
            }
            .skills-search-bar input:focus {
                outline: none;
                border-color: #FF6B35;
            }
            .skills-tabs {
                display: flex;
                padding: 8px 16px;
                gap: 8px;
                border-bottom: 1px solid #333;
            }
            .skills-tab {
                padding: 6px 16px;
                background: transparent;
                border: none;
                color: #888;
                font-size: 14px;
                cursor: pointer;
                border-radius: 16px;
            }
            .skills-tab.active {
                background: #FF6B35;
                color: #fff;
            }
            .skills-content {
                flex: 1;
                overflow-y: auto;
                padding: 12px;
            }
            .skills-empty {
                text-align: center;
                color: #666;
                padding: 40px;
            }
            .skill-item {
                display: flex;
                align-items: center;
                padding: 12px;
                background: #2a2a3e;
                border-radius: 8px;
                margin-bottom: 8px;
                cursor: pointer;
                transition: background 0.2s;
            }
            .skill-item:hover {
                background: #3a3a4e;
            }
            .skill-icon {
                font-size: 24px;
                margin-right: 12px;
            }
            .skill-info {
                flex: 1;
            }
            .skill-name {
                font-size: 14px;
                font-weight: bold;
                color: #fff;
                margin-bottom: 4px;
            }
            .skill-desc {
                font-size: 12px;
                color: #888;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .skill-badge {
                background: #FF6B35;
                color: #fff;
                font-size: 11px;
                padding: 2px 6px;
                border-radius: 10px;
            }
            .skills-footer {
                padding: 12px 16px;
                border-top: 1px solid #333;
                text-align: center;
            }
            .skills-count {
                font-size: 12px;
                color: #666;
            }
            .skill-detail-header {
                margin-bottom: 16px;
            }
            .skill-detail {
                text-align: center;
            }
            .skill-detail-icon {
                font-size: 48px;
                margin-bottom: 12px;
            }
            .skill-detail-name {
                font-size: 20px;
                font-weight: bold;
                color: #fff;
                margin-bottom: 8px;
            }
            .skill-detail-desc {
                font-size: 14px;
                color: #aaa;
                margin-bottom: 16px;
            }
            .skill-meta {
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                justify-content: center;
                margin-bottom: 16px;
            }
            .skill-tag {
                background: #333;
                color: #888;
                font-size: 11px;
                padding: 4px 8px;
                border-radius: 4px;
            }
            .skill-trigger {
                font-size: 12px;
                color: #888;
                margin-bottom: 8px;
            }
            .skill-trigger code {
                background: #333;
                padding: 2px 6px;
                border-radius: 4px;
                color: #FF6B35;
            }
            .skill-usage {
                font-size: 12px;
                color: #666;
                margin-bottom: 16px;
            }
            .skill-content-preview {
                background: #2a2a3e;
                padding: 12px;
                border-radius: 8px;
                font-size: 12px;
                color: #aaa;
                text-align: left;
                margin-bottom: 16px;
                max-height: 200px;
                overflow-y: auto;
            }
            .skill-use-btn {
                width: 100%;
                padding: 12px;
                background: #FF6B35;
                border: none;
                border-radius: 8px;
                color: #fff;
                font-size: 14px;
                font-weight: bold;
                cursor: pointer;
                transition: background 0.2s;
            }
            .skill-use-btn:hover {
                background: #ff8555;
            }
        `;
        document.head.appendChild(style);
        
        document.body.appendChild(panel);
    }
    
    // ===== 导出 API =====
    return {
        open: open,
        close: close,
        render: render,
        switchTab: switchTab,
        showDetail: showDetail,
        back: back,
        search: search,
        onSearch: onSearch,
        activateSkill: activateSkill
    };
})();
