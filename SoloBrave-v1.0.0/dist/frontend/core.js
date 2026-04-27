/**
 * Solo Brave Core - 基于 JiwuChat 模块化架构
 * 
 * 安全修复:
 * - EventBus 错误处理完善，单个监听器出错不影响其他监听器
 * - 错误恢复机制（错误过多时自动禁用）
 */

var EventBus = {
    _l: {},           // 事件监听器
    _errCount: {},    // 错误计数
    
    on: function(event, callback) {
        (this._l[event] || (this._l[event] = [])).push(callback);
        return function() { this.off(event, callback); }.bind(this);
    },
    
    once: function(event, callback) {
        var self = this;
        var wrapper = function() {
            try {
                callback.apply(null, arguments);
            } catch(err) {
                self._handleError(event, err);
            }
            self.off(event, wrapper);
        };
        this.on(event, wrapper);
    },
    
    off: function(event, callback) {
        if (!this._l[event]) return;
        this._l[event] = this._l[event].filter(function(x) { return x !== callback; });
    },
    
    emit: function(event) {
        var listeners = this._l[event] || [];
        var args = Array.prototype.slice.call(arguments, 1);
        for (var i = 0; i < listeners.length; i++) {
            try {
                listeners[i].apply(null, args);
            } catch(err) {
                this._handleError(event, err);
            }
        }
    },
    
    _handleError: function(event, err) {
        this._errCount[event] = (this._errCount[event] || 0) + 1;
        console.error('[EventBus] Error in "' + event + '":', err);
        
        // 如果某个事件错误过多，禁用它的监听器
        if (this._errCount[event] > 10) {
            console.warn('[EventBus] Too many errors in "' + event + '", disabling listeners');
            this._l[event] = [];
            this._errCount[event] = 0;
        }
    },
    
    clearErrors: function() {
        this._errCount = {};
    }
};

var Events = {
    MESSAGE_SEND: 'msg:send',
    MESSAGE_LIST_UPDATE: 'msg:list',
    UI_TOAST: 'ui:toast',
    AI_MODEL_CHANGE: 'ai:model',
    // Skills 系统事件
    SKILLS_LOADED: 'skills:loaded',
    SKILL_ACTIVATED: 'skill:activated',
    // AI Chat 系统事件
    AI_INITIALIZED: 'ai:initialized',
    AI_ERROR: 'ai:error',
    AI_ABORT: 'ai:abort',
    AI_SESSION_CLEARED: 'ai:session:cleared',
    // 文件上传事件
    FILE_UPLOADED: 'file:uploaded',
    FILE_ERROR: 'file:error',
    FILE_UPLOAD_PROGRESS: 'file:upload:progress',
    FILE_UPLOADER_INITIALIZED: 'file:uploader:initialized',
    FILE_INSERTED: 'file:inserted'
};

var AppStorage = {
    prefix: 'soloBrave_',
    
    save: function(key, value) {
        try {
            localStorage.setItem(this.prefix + key, JSON.stringify(value));
        } catch(e) {
            console.error('[AppStorage] Save error:', e);
        }
    },
    
    load: function(key, defaultValue) {
        try {
            var data = localStorage.getItem(this.prefix + key);
            return data ? JSON.parse(data) : defaultValue;
        } catch(e) {
            return defaultValue;
        }
    },
    
    remove: function(key) {
        localStorage.removeItem(this.prefix + key);
    }
};

var State = {
    _s: {},     // 状态数据
    _c: {},     // 变更回调
    
    init: function(initial) {
        Object.assign(this._s, initial);
        Object.keys(initial).forEach(function(key) {
            this._c[key] = [];
        }.bind(this));
    },
    
    get: function(key) {
        return this._s[key];
    },
    
    set: function(key, value, options) {
        options = options || {};
        
        if (typeof key === 'object') {
            Object.entries(key).forEach(function(entry) {
                this.set(entry[0], entry[1], options);
            }.bind(this));
            return;
        }
        
        var old = this._s[key];
        
        // 如果启用深度合并
        if (options.deep && typeof value === 'object' && typeof old === 'object') {
            this._s[key] = this._deepMerge(old, value);
        } else {
            this._s[key] = value;
        }
        
        // 通知所有订阅者
        (this._c[key] || []).forEach(function(callback) {
            try {
                callback(this._s[key], old);
            } catch(e) {
                console.error('[State] Subscriber error:', e);
            }
        }.bind(this));
        
        // 持久化
        AppStorage.save(key, this._s[key]);
    },
    
    // 简单的深度合并（仅一层深度）
    _deepMerge: function(target, source) {
        var result = Object.assign({}, target);
        Object.keys(source).forEach(function(key) {
            if (typeof source[key] === 'object' && !Array.isArray(source[key]) && 
                typeof result[key] === 'object' && !Array.isArray(result[key])) {
                result[key] = Object.assign(result[key] || {}, source[key]);
            } else {
                result[key] = source[key];
            }
        });
        return result;
    },
    
    sub: function(key, callback) {
        (this._c[key] || (this._c[key] = [])).push(callback);
        
        // 返回取消订阅函数
        return function() {
            this._c[key] = (this._c[key] || []).filter(function(x) { return x !== callback; });
        }.bind(this);
    }
};

var Messages = {
    PAGE_SIZE: 20,
    
    init: function() {
        State.init({
            messages: AppStorage.load('messages', []),
            isLoading: false,
            isSending: false
        });
    },
    
    send: function(content, type, meta) {
        type = type || 'user';
        meta = meta || {};
        
        var msg = {
            id: 'msg_' + Date.now() + '_' + Math.random().toString(36).substr(2, 9),
            type: type,
            content: content,
            timestamp: Date.now(),
            status: type === 'user' ? 'sending' : 'received'
        };
        
        Object.assign(msg, meta);
        
        var msgs = [].concat(State.get('messages') || []).concat([msg]);
        State.set('messages', msgs);
        
        EventBus.emit(Events.MESSAGE_SEND, msg);
        EventBus.emit(Events.MESSAGE_LIST_UPDATE, msgs);
        
        return msg;
    },
    
    reply: function(content, meta) {
        return this.send(content, 'ai', Object.assign({ status: 'received' }, meta || {}));
    },
    
    edit: function(id, content) {
        var msgs = State.get('messages') || [];
        var i = msgs.findIndex(function(m) { return m.id === id; });
        
        if (i !== -1) {
            msgs[i] = Object.assign({}, msgs[i], {
                content: content,
                edited: true,
                editTime: Date.now()
            });
            State.set('messages', msgs);
            EventBus.emit(Events.MESSAGE_LIST_UPDATE, msgs);
        }
    },
    
    delete: function(id) {
        var msgs = (State.get('messages') || []).filter(function(m) { return m.id !== id; });
        State.set('messages', msgs);
        EventBus.emit(Events.MESSAGE_LIST_UPDATE, msgs);
    }
};

var AI = {
    models: [
        { id: 'gpt-4o', name: 'GPT-4o', provider: 'OpenAI' },
        { id: 'claude-3.5', name: 'Claude 3.5', provider: 'Anthropic' },
        { id: 'gemini-pro', name: 'Gemini Pro', provider: 'Google' },
        { id: 'qwen-plus', name: '通义千问 Plus', provider: 'Alibaba' },
        { id: 'deepseek-chat', name: 'DeepSeek Chat', provider: 'DeepSeek' }
    ],
    
    currentModel: null,
    
    init: function() {
        var saved = AppStorage.load('currentModel', null);
        this.currentModel = saved || this.models[0].id;
    },
    
    setModel: function(id) {
        this.currentModel = id;
        AppStorage.save('currentModel', id);
        EventBus.emit(Events.AI_MODEL_CHANGE, id);
    }
};

// UI 工具模块
var UI = {
    /**
     * 显示 Toast 消息
     */
    toast: function(message, type) {
        type = type || 'info';
        
        // 移除已存在的 toast
        var existing = document.querySelector('.toast-notification');
        if (existing) existing.remove();
        
        var toast = document.createElement('div');
        toast.className = 'toast-notification toast-' + type;
        toast.textContent = message;
        
        // 设置样式
        toast.style.cssText = [
            'position: fixed',
            'bottom: 24px',
            'left: 50%',
            'transform: translateX(-50%)',
            'padding: 12px 24px',
            'border-radius: 8px',
            'background: ' + (type === 'error' ? '#FF3B30' : type === 'success' ? '#34C759' : '#333'),
            'color: white',
            'font-size: 14px',
            'z-index: 99999',
            'animation: toast-fade-in 0.3s ease-out'
        ].join(';');
        
        document.body.appendChild(toast);
        
        // 自动消失
        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s';
            setTimeout(function() { toast.remove(); }, 300);
        }, 3000);
        
        return toast;
    },
    
    /**
     * 显示确认对话框
     */
    confirm: function(message, onOk, onCancel) {
        if (confirm(message)) {
            if (onOk) onOk();
        } else {
            if (onCancel) onCancel();
        }
    },
    
    /**
     * 显示模态框
     */
    modal: function(content, options) {
        options = options || {};
        
        var overlay = document.createElement('div');
        overlay.className = 'modal-overlay show';
        overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.5);display:flex;align-items:center;justify-content:center;z-index:9999';
        
        var modal = document.createElement('div');
        modal.className = 'modal-content';
        modal.style.cssText = 'background:white;border-radius:16px;padding:24px;max-width:' + (options.maxWidth || 400) + 'px;width:90%';
        
        modal.innerHTML = content;
        
        overlay.appendChild(modal);
        document.body.appendChild(overlay);
        
        // 点击背景关闭
        overlay.addEventListener('click', function(e) {
            if (e.target === overlay) {
                UI.closeModal(overlay);
                if (options.onClose) options.onClose();
            }
        });
        
        return overlay;
    },
    
    /**
     * 关闭模态框
     */
    closeModal: function(overlay) {
        if (overlay) {
            overlay.classList.remove('show');
            setTimeout(function() { overlay.remove(); }, 300);
        } else {
            document.querySelectorAll('.modal-overlay').forEach(function(el) {
                el.classList.remove('show');
                setTimeout(function() { el.remove(); }, 300);
            });
        }
    }
};
