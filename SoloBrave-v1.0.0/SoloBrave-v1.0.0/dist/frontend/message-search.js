/**
 * Solo Brave - 消息搜索模块
 * 
 * 支持全文搜索、记忆搜索、上下文高亮
 * 位置: ./知识库/solobrave-modules/message-search.js
 * 
 * 安全修复:
 * - 所有用户输入经过 HTML 转义
 * - 添加 destroy() 方法防止内存泄漏
 * - 事件监听器正确清理
 */

const MessageSearch = {
    // 搜索索引
    _index: {
        messages: [],
        employees: [],
        lastUpdate: null
    },

    // 搜索配置
    config: {
        maxResults: 50,
        snippetLength: 60,
        highlightClass: 'search-highlight',
        debounceMs: 300
    },

    // 防抖定时器
    _debounceTimer: null,
    
    // 保存的事件处理函数引用
    _keydownHandler: null,
    _messageUpdateOff: null,
    _modalInputHandler: null,
    _modalKeydownHandler: null,
    _modalClickHandler: null,

    /**
     * HTML 转义（防止 XSS）
     */
    _escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    },

    /**
     * 初始化
     */
    init() {
        // 构建索引
        this._buildIndex();
        
        // 监听事件
        if (typeof EventBus !== 'undefined' && typeof Events !== 'undefined') {
            this._messageUpdateOff = EventBus.on(Events.MESSAGE_LIST_UPDATE, () => {
                this._buildIndex();
            });
        }
        
        // 监听快捷键
        this._keydownHandler = (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
                e.preventDefault();
                this._showSearchModal();
            }
        };
        document.addEventListener('keydown', this._keydownHandler);
    },

    /**
     * 销毁（防止内存泄漏）
     */
    destroy() {
        // 移除键盘监听器
        if (this._keydownHandler) {
            document.removeEventListener('keydown', this._keydownHandler);
            this._keydownHandler = null;
        }
        
        // 移除事件订阅
        if (this._messageUpdateOff) {
            this._messageUpdateOff();
            this._messageUpdateOff = null;
        }
        
        // 清除防抖定时器
        clearTimeout(this._debounceTimer);
        this._debounceTimer = null;
        
        // 关闭并清理搜索模态框
        this._closeSearchModal();
    },

    /**
     * 构建搜索索引
     */
    _buildIndex() {
        // 索引消息
        const messages = State.get('messages') || [];
        this._index.messages = messages.map(msg => ({
            id: msg.id,
            content: msg.content,
            type: msg.type,
            sender: msg.sender || 'user',
            timestamp: msg.timestamp,
            employeeId: msg.employeeId
        }));

        // 索引员工消息
        const employees = Store.getEmployees() || [];
        this._index.employees = [];
        
        employees.forEach(emp => {
            const empMessages = Store.getMessages(emp.id) || [];
            empMessages.forEach(msg => {
                this._index.employees.push({
                    id: msg.id,
                    content: msg.content,
                    type: msg.type,
                    sender: msg.sender,
                    timestamp: msg.timestamp,
                    employeeId: emp.id,
                    employeeName: emp.name
                });
            });
        });

        this._index.lastUpdate = Date.now();
    },

    /**
     * 执行搜索
     */
    search(query, options = {}) {
        const {
            types = ['message', 'memory', 'employee'],
            limit = this.config.maxResults
        } = options;

        if (!query || query.trim().length < 1) {
            return [];
        }

        const results = [];
        const lowerQuery = query.toLowerCase().trim();

        // 1. 搜索消息
        if (types.includes('message')) {
            this._index.messages.forEach(msg => {
                if (msg.content.toLowerCase().includes(lowerQuery)) {
                    results.push({
                        type: 'message',
                        id: msg.id,
                        content: msg.content,
                        sender: msg.sender,
                        timestamp: msg.timestamp,
                        employeeId: msg.employeeId,
                        snippet: this._getSnippet(msg.content, query),
                        score: this._calculateScore(msg.content, query)
                    });
                }
            });
        }

        // 2. 搜索员工消息
        if (types.includes('employee')) {
            this._index.employees.forEach(msg => {
                if (msg.content.toLowerCase().includes(lowerQuery)) {
                    results.push({
                        type: 'employee',
                        id: msg.id,
                        content: msg.content,
                        sender: msg.sender,
                        timestamp: msg.timestamp,
                        employeeId: msg.employeeId,
                        employeeName: msg.employeeName,
                        snippet: this._getSnippet(msg.content, query),
                        score: this._calculateScore(msg.content, query, 1.2)
                    });
                }
            });
        }

        // 3. 搜索记忆
        if (types.includes('memory') && typeof Memory !== 'undefined') {
            const memoryResults = Memory.search(query, { limit: 20 });
            memoryResults.forEach(m => {
                results.push({
                    type: 'memory',
                    id: m.id,
                    content: m.content,
                    timestamp: m.timestamp,
                    importance: m.importance,
                    tags: m.tags,
                    snippet: this._getSnippet(m.content, query),
                    score: this._calculateScore(m.content, query, m.importance || 1)
                });
            });
        }

        // 4. 搜索知识库
        if (types.includes('knowledge') && typeof KnowledgeBase !== 'undefined') {
            const kbResults = KnowledgeBase.search(query, { limit: 10 });
            kbResults.forEach(r => {
                results.push({
                    type: 'knowledge',
                    id: r.documentId,
                    content: r.text,
                    title: r.title,
                    similarity: r.similarity,
                    snippet: this._getSnippet(r.text, query),
                    score: r.similarity
                });
            });
        }

        // 去重并排序
        const seen = new Set();
        const uniqueResults = results.filter(r => {
            const key = r.type + '-' + r.id;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });

        uniqueResults.sort((a, b) => b.score - a.score);
        
        return uniqueResults.slice(0, limit);
    },

    /**
     * 实时搜索（防抖）
     */
    searchDebounced(query, options = {}) {
        return new Promise((resolve) => {
            clearTimeout(this._debounceTimer);
            this._debounceTimer = setTimeout(() => {
                resolve(this.search(query, options));
            }, this.config.debounceMs);
        });
    },

    /**
     * 获取文本片段
     */
    _getSnippet(content, query, contextLen = 60) {
        const lowerContent = content.toLowerCase();
        const lowerQuery = query.toLowerCase();
        const index = lowerContent.indexOf(lowerQuery);
        
        if (index === -1) {
            return content.substring(0, contextLen * 2);
        }

        const start = Math.max(0, index - contextLen);
        const end = Math.min(content.length, index + query.length + contextLen);

        let snippet = content.substring(start, end);
        if (start > 0) snippet = '\u2026' + snippet;
        if (end < content.length) snippet = snippet + '\u2026';

        return snippet;
    },

    /**
     * 计算相关性分数
     */
    _calculateScore(content, query, weight = 1) {
        const lowerContent = content.toLowerCase();
        const lowerQuery = query.toLowerCase();
        
        let score = 0;
        
        // 精确匹配
        if (lowerContent === lowerQuery) {
            score += 100;
        }
        
        // 开头匹配
        if (lowerContent.startsWith(lowerQuery)) {
            score += 50;
        }
        
        // 包含匹配
        const matches = lowerContent.split(lowerQuery).length - 1;
        score += matches * 10;
        
        // 长度惩罚
        const lengthRatio = content.length / query.length;
        score = score * Math.min(lengthRatio / 10, 1);
        
        return score * weight;
    },

    /**
     * 高亮搜索词（安全版本 - 先转义再高亮）
     */
    highlight(text, query) {
        if (!query || !text) return this._escapeHtml(text);
        
        // 先转义特殊字符，防止 RegExp 注入
        const escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const escapedText = this._escapeHtml(text);
        const regex = new RegExp('(' + escapedQuery + ')', 'gi');
        
        return escapedText.replace(regex, '<mark class="' + this.config.highlightClass + '">$1</mark>');
    },

    /**
     * 显示搜索模态框
     */
    _showSearchModal() {
        let modal = document.getElementById('searchModal');
        if (modal) {
            modal.classList.add('show');
            const input = modal.querySelector('.search-input');
            if (input) input.focus();
            return;
        }

        // 创建模态框
        modal = document.createElement('div');
        modal.id = 'searchModal';
        modal.className = 'search-modal-overlay';
        
        // 安全创建内容
        modal.innerHTML = this._renderSearchModal();
        document.body.appendChild(modal);

        // 绑定事件
        this._bindSearchModalEvents(modal);

        // 显示动画
        requestAnimationFrame(() => {
            modal.classList.add('show');
            const input = modal.querySelector('.search-input');
            if (input) input.focus();
        });
    },

    /**
     * 渲染搜索模态框
     */
    _renderSearchModal() {
        // SVG 需要特殊处理，只允许基本属性
        const safeSvgIcon = '<svg class="search-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>';
        
        return '<div class="search-modal-content">' +
            '<div class="search-modal-header">' +
                safeSvgIcon +
                '<input type="text" class="search-input" placeholder="搜索消息、记忆..." autofocus>' +
                '<kbd class="search-shortcut">ESC</kbd>' +
            '</div>' +
            '<div class="search-results"></div>' +
        '</div>';
    },

    /**
     * 绑定搜索模态框事件（保存引用以便移除）
     */
    _bindSearchModalEvents(modal) {
        const input = modal.querySelector('.search-input');
        const results = modal.querySelector('.search-results');

        // 保存事件处理函数引用
        this._modalInputHandler = async (e) => {
            const query = e.target.value.trim();
            
            if (!query) {
                results.innerHTML = this._renderEmptyState();
                return;
            }

            results.innerHTML = '<div class="search-loading">搜索中...</div>';
            
            const searchResults = await this.searchDebounced(query);
            
            if (searchResults.length === 0) {
                results.innerHTML = this._renderNoResults(query);
                return;
            }

            results.innerHTML = this._renderResults(searchResults, query);
        };

        this._modalKeydownHandler = (e) => {
            if (e.key === 'Escape') {
                this._closeSearchModal();
            }
        };

        this._modalClickHandler = (e) => {
            if (e.target === modal) {
                this._closeSearchModal();
            }
        };

        input.addEventListener('input', this._modalInputHandler);
        input.addEventListener('keydown', this._modalKeydownHandler);
        modal.addEventListener('click', this._modalClickHandler);
    },

    /**
     * 关闭搜索模态框
     */
    _closeSearchModal() {
        const modal = document.getElementById('searchModal');
        if (!modal) return;
        
        modal.classList.remove('show');
        
        // 移除事件监听器
        const input = modal.querySelector('.search-input');
        if (input && this._modalInputHandler) {
            input.removeEventListener('input', this._modalInputHandler);
        }
        if (input && this._modalKeydownHandler) {
            input.removeEventListener('keydown', this._modalKeydownHandler);
        }
        if (this._modalClickHandler) {
            modal.removeEventListener('click', this._modalClickHandler);
        }
        
        setTimeout(() => modal.remove(), 300);
    },

    /**
     * 渲染搜索结果（安全版本 - 所有输入转义）
     */
    _renderResults(results, query) {
        const typeLabels = {
            message: '💬 当前对话',
            employee: '👤 员工对话',
            memory: '🧠 记忆',
            knowledge: '📚 知识库'
        };

        let html = '<div class="search-results-list">';
        
        results.forEach(result => {
            // 安全转义所有用户输入
            const safeId = this._escapeHtml(result.id);
            const safeType = this._escapeHtml(result.type);
            const safeSnippet = this.highlight(result.snippet || '', query);
            const safeEmployeeName = result.employeeName ? this._escapeHtml(result.employeeName) : '';
            const timeStr = this._formatTime(result.timestamp);
            
            html += '<div class="search-result-item" data-id="' + safeId + '" data-type="' + safeType + '">' +
                '<div class="result-header">' +
                    '<span class="result-type">' + (typeLabels[result.type] || result.type) + '</span>' +
                    '<span class="result-time">' + timeStr + '</span>' +
                '</div>' +
                '<div class="result-content">' + safeSnippet + '</div>' +
                (safeEmployeeName ? '<div class="result-meta">来自 ' + safeEmployeeName + '</div>' : '') +
            '</div>';
        });
        
        html += '</div>';
        return html;
    },

    /**
     * 渲染空状态
     */
    _renderEmptyState() {
        return '<div class="search-empty">' +
            '<div class="search-empty-icon">🔍</div>' +
            '<div class="search-empty-text">输入关键词开始搜索</div>' +
            '<div class="search-empty-hint">' +
                '<span>支持:</span>' +
                '<span class="hint-tag">💬 消息</span>' +
                '<span class="hint-tag">🧠 记忆</span>' +
                '<span class="hint-tag">📚 知识</span>' +
            '</div>' +
        '</div>';
    },

    /**
     * 渲染无结果（query 转义）
     */
    _renderNoResults(query) {
        const safeQuery = this._escapeHtml(query);
        return '<div class="search-no-results">' +
            '<div class="no-results-icon">😕</div>' +
            '<div class="no-results-text">没有找到与 "' + safeQuery + '" 相关的内容</div>' +
        '</div>';
    },

    /**
     * 格式化时间
     */
    _formatTime(timestamp) {
        if (!timestamp) return '';
        
        const date = new Date(timestamp);
        const now = new Date();
        const diff = now - date;
        
        if (diff < 60000) return '刚刚';
        if (diff < 3600000) return Math.floor(diff / 60000) + '分钟前';
        if (diff < 86400000) return Math.floor(diff / 3600000) + '小时前';
        if (diff < 604800000) return Math.floor(diff / 86400000) + '天前';
        
        return date.toLocaleDateString();
    }
};

// 搜索模态框样式
var searchModalStyles = [
    '.search-modal-overlay {',
    '    position: fixed;',
    '    inset: 0;',
    '    background: rgba(0, 0, 0, 0.5);',
    '    display: flex;',
    '    align-items: flex-start;',
    '    justify-content: center;',
    '    padding-top: 10vh;',
    '    z-index: 10000;',
    '    opacity: 0;',
    '    visibility: hidden;',
    '    transition: all 0.2s;',
    '}',
    '.search-modal-overlay.show {',
    '    opacity: 1;',
    '    visibility: visible;',
    '}',
    '.search-modal-content {',
    '    width: 90%;',
    '    max-width: 600px;',
    '    background: var(--bg-primary, #fff);',
    '    border-radius: 16px;',
    '    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.2);',
    '    overflow: hidden;',
    '    transform: scale(0.95);',
    '    transition: transform 0.2s;',
    '}',
    '.search-modal-overlay.show .search-modal-content {',
    '    transform: scale(1);',
    '}',
    '.search-modal-header {',
    '    display: flex;',
    '    align-items: center;',
    '    gap: 12px;',
    '    padding: 16px 20px;',
    '    border-bottom: 1px solid var(--separator, rgba(0,0,0,0.1));',
    '}',
    '.search-icon { color: var(--text-secondary, #8e8e93); }',
    '.search-input {',
    '    flex: 1;',
    '    border: none;',
    '    background: transparent;',
    '    font-size: 16px;',
    '    outline: none;',
    '}',
    '.search-shortcut {',
    '    padding: 4px 8px;',
    '    background: var(--bg-secondary, #f2f2f7);',
    '    border-radius: 4px;',
    '    font-size: 12px;',
    '    color: var(--text-secondary, #8e8e93);',
    '}',
    '.search-results {',
    '    max-height: 400px;',
    '    overflow-y: auto;',
    '}',
    '.search-results-list { padding: 8px; }',
    '.search-result-item {',
    '    padding: 12px;',
    '    border-radius: 8px;',
    '    cursor: pointer;',
    '    transition: background 0.15s;',
    '}',
    '.search-result-item:hover {',
    '    background: var(--accent-light, rgba(255, 107, 53, 0.1));',
    '}',
    '.result-header {',
    '    display: flex;',
    '    justify-content: space-between;',
    '    margin-bottom: 4px;',
    '}',
    '.result-type { font-size: 12px; color: var(--text-secondary, #8e8e93); }',
    '.result-time { font-size: 11px; color: var(--text-tertiary, #aeaeb2); }',
    '.result-content {',
    '    font-size: 14px;',
    '    line-height: 1.4;',
    '    word-break: break-word;',
    '}',
    '.result-meta { font-size: 12px; color: var(--text-secondary, #8e8e93); margin-top: 4px; }',
    '.search-highlight {',
    '    background: rgba(255, 107, 53, 0.3);',
    '    padding: 0 2px;',
    '    border-radius: 2px;',
    '}',
    '.search-empty, .search-no-results { text-align: center; padding: 40px; }',
    '.search-empty-icon, .no-results-icon { font-size: 48px; margin-bottom: 12px; }',
    '.search-empty-text, .no-results-text { font-size: 14px; color: var(--text-secondary, #8e8e93); }',
    '.search-loading { text-align: center; padding: 20px; color: var(--text-secondary, #8e8e93); }'
].join('');

// 注入样式
if (typeof document !== 'undefined') {
    var style = document.createElement('style');
    style.textContent = searchModalStyles;
    document.head.appendChild(style);
}

// 初始化
MessageSearch.init();
