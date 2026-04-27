/**
 * Solo Brave - 增强功能
 * 消息编辑 + 虚拟滚动 + Markdown 渲染
 * 
 * 安全修复:
 * - XSS 防护: 所有用户输入经过 HTML 转义
 * - 内存管理: 事件监听器正确清理
 */

// ========================================
// 0. HTML 转义工具
// ========================================
const HtmlEscaper = {
  escape(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },
  
  escapeAttr(value) {
    if (!value) return '';
    return String(value)
      .replace(/&/g, '&amp;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#x27;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }
};

// ========================================
// 1. 消息编辑交互
// ========================================
const MessageEdit = {
  currentEditId: null,
  _eventOff: null,
  _keydownHandler: null,  // 保存绑定后的函数引用
  
  init() {
    if (typeof EventBus !== 'undefined' && typeof Events !== 'undefined') {
      if (this._eventOff) this._eventOff();
      this._eventOff = EventBus.on(Events.MESSAGE_LIST_UPDATE, (messages) => {
        this.renderMessageActions(messages);
      });
    }
    
    // 预绑定函数，避免每次创建新引用
    if (!this._keydownHandler) {
      this._keydownHandler = this._handleKeydown.bind(this);
    }
  },
  
  destroy() {
    if (this._eventOff) {
      this._eventOff();
      this._eventOff = null;
    }
    if (this._keydownHandler) {
      document.removeEventListener('keydown', this._keydownHandler);
    }
  },
  
  startEdit(messageId) {
    if (this.currentEditId) this.cancelEdit();
    this.currentEditId = messageId;
    
    const msgEl = document.querySelector('[data-msg-id="' + HtmlEscaper.escapeAttr(messageId) + '"]');
    if (msgEl) {
      msgEl.classList.add('editing');
      const input = msgEl.querySelector('.edit-input');
      if (input) { input.value = msgEl.querySelector('.msg-content')?.textContent || ''; input.focus(); }
    }
    if (this._keydownHandler) {
      document.addEventListener('keydown', this._keydownHandler);
    }
  },
  
  saveEdit(messageId) {
    const msgEl = document.querySelector('[data-msg-id="' + HtmlEscaper.escapeAttr(messageId) + '"]');
    if (!msgEl) return;
    const input = msgEl.querySelector('.edit-input');
    if (!input) return;
    const newContent = input.value.trim();
    if (newContent) {
      Messages.edit(HtmlEscaper.escapeAttr(messageId), newContent);
      UI.toast('Updated', 'success');
    }
    this._endEdit();
  },
  
  cancelEdit() { this._endEdit(); },
  
  _endEdit() {
    if (this.currentEditId) {
      const msgEl = document.querySelector('[data-msg-id="' + HtmlEscaper.escapeAttr(this.currentEditId) + '"]');
      if (msgEl) msgEl.classList.remove('editing');
    }
    this.currentEditId = null;
    if (this._keydownHandler) {
      document.removeEventListener('keydown', this._keydownHandler);
    }
  },
  
  _handleKeydown(e) {
    if (e.key === 'Escape') this.cancelEdit();
    else if (e.key === 'Enter' && e.ctrlKey) this.saveEdit(this.currentEditId);
  },
  
  renderMessageActions(messages) {
    messages.forEach(msg => {
      let msgEl = document.querySelector('[data-msg-id="' + HtmlEscaper.escapeAttr(msg.id) + '"]');
      if (!msgEl && msg.type === 'user') {
        msgEl = this._createMessageElement(msg);
        document.querySelector('.messages')?.appendChild(msgEl);
      }
    });
  },
  
  _createMessageElement(msg) {
    const div = document.createElement('div');
    div.className = 'message-item message-' + msg.type;
    div.setAttribute('data-msg-id', HtmlEscaper.escapeAttr(msg.id));
    
    const safeId = HtmlEscaper.escapeAttr(msg.id);
    
    div.innerHTML = 
      '<div class="msg-time">' + this._formatTime(msg.timestamp) + '</div>' +
      '<div class="msg-content">' + Markdown.render(msg.content) + '</div>' +
      '<textarea class="edit-input"></textarea>' +
      '<div class="edit-actions">' +
        '<button class="edit-btn save" onclick="MessageEdit.saveEdit(\'' + safeId + '\')">Save</button>' +
        '<button class="edit-btn cancel" onclick="MessageEdit.cancelEdit()">Cancel</button>' +
      '</div>' +
      '<div class="message-actions">' +
        (msg.type === 'user' ? '<button class="msg-action-btn" onclick="MessageEdit.startEdit(\'' + safeId + '\')" title="Edit">E</button>' : '') +
        '<button class="msg-action-btn" onclick="Messages.delete(\'' + safeId + '\')" title="Delete">X</button>' +
      '</div>';
    
    const textarea = div.querySelector('.edit-input');
    if (textarea) textarea.textContent = msg.content || '';
    
    return div;
  },
  
  _formatTime(ts) { return new Date(ts).toLocaleTimeString('zh-CN', {hour:'2-digit',minute:'2-digit'}); }
};

// ========================================
// 2. 虚拟滚动
// ========================================
const VirtualScroll = {
  container: null, items: [], itemHeight: 100, visibleCount: 10, scrollTop: 0, buffer: 3, renderItem: null,
  _scrollHandler: null,  // 预绑定的滚动处理函数
  _virtualScroll: null,
  
  init(options) {
    options = options || {};
    this.container = options.container || document.querySelector('.messages');
    this.itemHeight = options.itemHeight || 100;
    this.buffer = options.buffer || 3;
    this.renderItem = options.renderItem || this._defaultRender;
    this.destroy();
    
    // 预绑定函数，避免每次创建新引用
    if (this.container && !this._scrollHandler) {
      this._scrollHandler = this._handleScroll.bind(this);
      this.container.addEventListener('scroll', this._scrollHandler);
    }
  },
  
  destroy() {
    if (this.container && this._scrollHandler) {
      this.container.removeEventListener('scroll', this._scrollHandler);
      this._scrollHandler = null;
    }
  },
  
  setItems(items) { this.items = items; this._render(); },
  
  _handleScroll() { this.scrollTop = this.container?.scrollTop || 0; this._render(); },
  
  _render() {
    if (!this.container || !this.items.length) return;
    const containerHeight = this.container.clientHeight;
    const startIndex = Math.max(0, Math.floor(this.scrollTop / this.itemHeight) - this.buffer);
    const endIndex = Math.min(this.items.length, Math.ceil((this.scrollTop + containerHeight) / this.itemHeight) + this.buffer);
    this.container.innerHTML = '';
    const spacerTop = document.createElement('div'); spacerTop.style.height = (startIndex * this.itemHeight) + 'px'; this.container.appendChild(spacerTop);
    for (let i = startIndex; i < endIndex; i++) {
      const itemEl = this.renderItem(this.items[i], i);
      itemEl.style.cssText = 'position:absolute;top:' + (i * this.itemHeight) + 'px;width:100%;box-sizing:border-box;';
      this.container.appendChild(itemEl);
    }
    const spacerBottom = document.createElement('div'); spacerBottom.style.height = ((this.items.length - endIndex) * this.itemHeight) + 'px'; this.container.appendChild(spacerBottom);
  },
  
  _defaultRender(item) { 
    const div = document.createElement('div'); 
    div.className = 'virtual-item';
    div.textContent = item.content || JSON.stringify(item); 
    return div; 
  },
  
  scrollToBottom() { if (this.container) this.container.scrollTop = this.container.scrollHeight; }
};

// ========================================
// 3. Markdown 渲染（安全版）
// ========================================
const Markdown = {
  cache: new Map(),
  
  render(text) {
    if (!text) return '';
    if (this.cache.has(text)) return this.cache.get(text);
    
    let html = this._escapeHtml(text);
    
    // 代码块
    html = html.replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="code-block"><code>$2</code></pre>');
    html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');
    
    // 格式
    html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/__([^_]+)__/g, '<strong>$1</strong>');
    html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
    html = html.replace(/_([^_]+)_/g, '<em>$1</em>');
    html = html.replace(/~~([^~]+)~~/g, '<del>$1</del>');
    
    // 链接 - 安全处理，防止 javascript: 伪协议
    html = html.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (match, text, url) => {
      return '<a href="' + this._safeUrl(url) + '" target="_blank" rel="noopener noreferrer">' + text + '</a>';
    });
    
    // 标题
    html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
    html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
    html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
    
    // 引用和列表
    html = html.replace(/^> (.+)$/gm, '<blockquote>$1</blockquote>');
    html = html.replace(/^- (.+)$/gm, '<li>$1</li>');
    
    // 换行
    html = html.replace(/\n/g, '<br>');
    
    this.cache.set(text, html);
    return html;
  },
  
  _escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  },
  
  _safeUrl(url) {
    if (!url) return '#';
    url = url.trim();
    if (/^javascript:/i.test(url)) return '#';
    if (/^(https?|ftp):\/\//i.test(url)) return url;
    if (/^[a-z]+:/i.test(url)) return '#';
    return url;
  },
  
  clearCache() { this.cache.clear(); }
};

// ========================================
// 4. 深色模式
// ========================================
const Theme = {
  current: 'light',
  _mediaQuery: null,
  _listener: null,
  
  init() {
    const saved = AppStorage.load('theme', 'light');
    this.setTheme(saved);
    
    if (window.matchMedia) {
      this._mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
      this._listener = (e) => {
        if (!AppStorage.load('theme')) {
          this.setTheme(e.matches ? 'dark' : 'light');
        }
      };
      this._mediaQuery.addEventListener('change', this._listener);
    }
  },
  
  destroy() {
    if (this._mediaQuery && this._listener) {
      this._mediaQuery.removeEventListener('change', this._listener);
    }
  },
  
  setTheme(theme) {
    this.current = theme;
    document.documentElement.setAttribute('data-theme', theme);
    AppStorage.save('theme', theme);
    const btn = document.getElementById('themeToggle');
    if (btn) btn.textContent = theme === 'dark' ? 'Light' : 'Dark';
    if (typeof EventBus !== 'undefined') EventBus.emit('theme:change', theme);
  },
  
  toggle() { this.setTheme(this.current === 'dark' ? 'light' : 'dark'); }
};
