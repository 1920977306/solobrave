/**
 * Solo Brave - 头像选择器
 * 
 * 支持默认头像、自定义上传、颜色配置
 * 位置: ./知识库/solobrave-modules/avatar-picker.js
 * 
 * 安全修复:
 * - 移除生产环境 console.log/error
 * - 所有用户输入经过 HTML 转义
 * - 内联事件改用 addEventListener
 */

const AvatarPicker = {
    // 默认头像
    defaultAvatars: [
        // 动物类
        '🦞', '🐙', '⭐', '🦀', '🐠', '🦑', '🐬', '🦈', '🐳', '🦩',
        '🦋', '🐝', '🐢', '🦎', '🐍', '🦂', '🕷️', '🦑', '🐙', '🦀',
        // 人物类
        '👤', '👧', '👦', '👨', '👩', '🧑', '👴', '👵', '🧒', '👶',
        // 物品类
        '🤖', '🦸', '🧙', '🧚', '🧛', '🧜', '🧝', '🧞', '🥷', '🧛‍♀️',
        // 其他
        '🎭', '🎨', '🎪', '🎯', '🎲', '🎮', '🎸', '🎺', '🎻', '🏆'
    ],

    // 颜色选项
    colorOptions: [
        '#FF6B35', // 橙色
        '#FF3B30', // 红色
        '#FF9500', // 黄色
        '#FFCC00', // 金色
        '#34C759', // 绿色
        '#5AC8FA', // 蓝色
        '#007AFF', // 深蓝
        '#5856D6', // 紫色
        '#AF52DE', // 粉紫
        '#FF2D55', // 粉红
        '#A2845E', // 棕色
        '#8E8E93'  // 灰色
    ],

    // 自定义头像
    customAvatars: [],

    // 当前选中
    _current: {
        type: 'emoji',
        value: '🦞'
    },
    
    // 当前回调
    _currentCallback: null,
    
    // 事件处理器引用
    _eventHandlers: [],

    /**
     * HTML 转义
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
        this.loadCustomAvatars();
        // 通过 EventBus 通知（而非 console.log）
        if (typeof EventBus !== 'undefined') {
            EventBus.emit('avatar:initialized');
        }
    },

    /**
     * 销毁
     */
    destroy() {
        // 清理事件处理器
        this._eventHandlers.forEach(({ element, event, handler }) => {
            element.removeEventListener(event, handler);
        });
        this._eventHandlers = [];
    },

    /**
     * 加载自定义头像
     */
    loadCustomAvatars() {
        this.customAvatars = Store.get('custom_avatars') || [];
    },

    /**
     * 保存自定义头像
     */
    saveCustomAvatar(base64) {
        const avatar = {
            id: 'custom_' + Date.now() + '_' + Math.random().toString(36).substring(2, 6),
            data: base64,
            createdAt: Date.now()
        };
        this.customAvatars.push(avatar);
        Store.set('custom_avatars', this.customAvatars);
        return avatar;
    },

    /**
     * 删除自定义头像
     */
    deleteCustomAvatar(id) {
        this.customAvatars = this.customAvatars.filter(a => a.id !== id);
        Store.set('custom_avatars', this.customAvatars);
    },

    /**
     * 渲染选择器（安全版本）
     */
    render(options = {}) {
        const {
            onSelect = null,
            current = null,
            showColorPicker = false,
            columns = 6
        } = options;

        this._currentCallback = onSelect;

        // 创建容器
        const container = document.createElement('div');
        container.className = 'avatar-picker';

        // Emoji头像区
        const emojiSection = this._createEmojiSection(current, columns);
        container.appendChild(emojiSection);

        // 自定义头像区
        if (this.customAvatars.length > 0) {
            const customSection = this._createCustomSection(current, columns);
            container.appendChild(customSection);
        }

        // 颜色头像区
        if (showColorPicker) {
            const colorSection = this._createColorSection(current, columns);
            container.appendChild(colorSection);
        }

        // 上传区
        const uploadSection = this._createUploadSection();
        container.appendChild(uploadSection);

        return container;
    },

    /**
     * 创建 Emoji 头像区
     */
    _createEmojiSection(current, columns) {
        const section = document.createElement('div');
        section.className = 'avatar-section emoji-section';
        
        const title = document.createElement('div');
        title.className = 'avatar-section-title';
        title.textContent = '🤩 默认头像';
        section.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'avatar-grid';
        grid.style.gridTemplateColumns = 'repeat(' + columns + ', 1fr)';

        this.defaultAvatars.forEach(emoji => {
            const item = document.createElement('div');
            item.className = 'avatar-item' + (current === emoji ? ' selected' : '');
            item.dataset.type = 'emoji';
            item.dataset.value = emoji;
            item.textContent = emoji;
            
            const handler = () => this.select(item, emoji, 'emoji');
            item.addEventListener('click', handler);
            this._eventHandlers.push({ element: item, event: 'click', handler });
            
            grid.appendChild(item);
        });

        section.appendChild(grid);
        return section;
    },

    /**
     * 创建自定义头像区
     */
    _createCustomSection(current, columns) {
        const section = document.createElement('div');
        section.className = 'avatar-section custom-section';
        
        const title = document.createElement('div');
        title.className = 'avatar-section-title';
        title.textContent = '🖼️ 自定义头像';
        section.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'avatar-grid';
        grid.style.gridTemplateColumns = 'repeat(' + columns + ', 1fr)';

        this.customAvatars.forEach(avatar => {
            const item = document.createElement('div');
            item.className = 'avatar-item custom' + (current === avatar.id ? ' selected' : '');
            item.dataset.type = 'custom';
            item.dataset.value = avatar.id;

            const img = document.createElement('img');
            img.src = avatar.data;
            img.alt = '自定义头像';
            item.appendChild(img);

            const deleteBtn = document.createElement('button');
            deleteBtn.className = 'avatar-delete';
            deleteBtn.textContent = '×';
            const avatarId = avatar.id;
            const deleteHandler = (e) => {
                e.stopPropagation();
                this._deleteCustom(avatarId);
            };
            deleteBtn.addEventListener('click', deleteHandler);
            this._eventHandlers.push({ element: deleteBtn, event: 'click', handler: deleteHandler });
            item.appendChild(deleteBtn);

            const selectHandler = () => this.selectCustom(item, avatar.id);
            item.addEventListener('click', selectHandler);
            this._eventHandlers.push({ element: item, event: 'click', handler: selectHandler });

            grid.appendChild(item);
        });

        section.appendChild(grid);
        return section;
    },

    /**
     * 创建颜色头像区
     */
    _createColorSection(current, columns) {
        const section = document.createElement('div');
        section.className = 'avatar-section color-section';
        
        const title = document.createElement('div');
        title.className = 'avatar-section-title';
        title.textContent = '🎨 颜色头像';
        section.appendChild(title);

        const grid = document.createElement('div');
        grid.className = 'avatar-grid';
        grid.style.gridTemplateColumns = 'repeat(' + columns + ', 1fr)';

        this.colorOptions.forEach(color => {
            const item = document.createElement('div');
            item.className = 'avatar-item color' + (current === color ? ' selected' : '');
            item.dataset.type = 'color';
            item.dataset.value = color;
            item.style.backgroundColor = color;
            item.style.color = 'white';

            const initial = document.createElement('span');
            initial.className = 'avatar-initial';
            initial.textContent = 'A';
            item.appendChild(initial);

            const handler = () => this.selectColor(item, color);
            item.addEventListener('click', handler);
            this._eventHandlers.push({ element: item, event: 'click', handler });

            grid.appendChild(item);
        });

        section.appendChild(grid);
        return section;
    },

    /**
     * 创建上传区
     */
    _createUploadSection() {
        const section = document.createElement('div');
        section.className = 'avatar-upload-section';

        const input = document.createElement('input');
        input.type = 'file';
        input.id = 'avatarUploadInput';
        input.accept = 'image/*';
        input.hidden = true;
        
        const uploadHandler = () => {
            const file = input.files[0];
            if (file) this.handleUpload(file);
            input.value = ''; // 清空以便重复选择
        };
        input.addEventListener('change', uploadHandler);
        this._eventHandlers.push({ element: input, event: 'change', handler: uploadHandler });
        
        section.appendChild(input);

        const btn = document.createElement('button');
        btn.className = 'avatar-upload-btn';
        
        const icon = document.createElement('span');
        icon.className = 'upload-icon';
        icon.textContent = '📤';
        
        const text = document.createElement('span');
        text.className = 'upload-text';
        text.textContent = '上传自定义头像';
        
        const clickHandler = () => input.click();
        btn.addEventListener('click', clickHandler);
        this._eventHandlers.push({ element: btn, event: 'click', handler: clickHandler });
        
        btn.appendChild(icon);
        btn.appendChild(text);
        section.appendChild(btn);

        const hint = document.createElement('div');
        hint.className = 'upload-hint';
        hint.textContent = '支持 JPG、PNG、GIF，建议尺寸 200x200，不超过 2MB';
        section.appendChild(hint);

        return section;
    },

    /**
     * 渲染简洁版本（弹窗用）
     */
    renderModal(options = {}) {
        const { onSelect, current, title = '选择头像' } = options;
        this._currentCallback = onSelect;

        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay avatar-picker-modal show';

        const content = document.createElement('div');
        content.className = 'modal-content avatar-picker-content';

        const header = document.createElement('div');
        header.className = 'modal-header';

        const titleEl = document.createElement('div');
        titleEl.className = 'modal-title';
        titleEl.textContent = title;

        const closeBtn = document.createElement('button');
        closeBtn.className = 'modal-close';
        closeBtn.textContent = '×';
        closeBtn.addEventListener('click', () => this.closeModal());

        header.appendChild(titleEl);
        header.appendChild(closeBtn);

        const body = document.createElement('div');
        body.className = 'modal-body';
        const picker = this.render({ onSelect, current, columns: 5 });
        body.appendChild(picker);

        content.appendChild(header);
        content.appendChild(body);
        overlay.appendChild(content);

        return overlay;
    },

    /**
     * 选择emoji头像
     */
    select(element, value, type) {
        document.querySelectorAll('.avatar-item').forEach(el => {
            el.classList.remove('selected');
        });
        
        element.classList.add('selected');
        
        this._current.type = type;
        this._current.value = value;

        if (this._currentCallback) {
            this._currentCallback(value, type);
        }

        if (typeof EventBus !== 'undefined') {
            EventBus.emit(Events.AVATAR_SELECTED || 'avatar:selected', { value, type });
        }
    },

    /**
     * 选择自定义头像
     */
    selectCustom(element, avatarId) {
        const avatar = this.customAvatars.find(a => a.id === avatarId);
        if (!avatar) return;
        
        this.select(element, avatar.data, 'custom');
    },

    /**
     * 选择颜色头像
     */
    selectColor(element, color) {
        this.select(element, color, 'color');
    },

    /**
     * 删除自定义头像
     */
    _deleteCustom(avatarId) {
        if (confirm('确定要删除这个头像吗？')) {
            this.deleteCustomAvatar(avatarId);
            EventBus.emit(Events.AVATAR_DELETED || 'avatar:deleted', { id: avatarId });
        }
    },

    /**
     * 处理上传
     */
    async handleUpload(file) {
        if (!file) return;

        if (!file.type.startsWith('image/')) {
            UI?.toast?.('请选择图片文件', 'error');
            return;
        }

        if (file.size > 2 * 1024 * 1024) {
            UI?.toast?.('图片大小不能超过 2MB', 'error');
            return;
        }

        try {
            const base64 = await this._processImage(file);
            this.saveCustomAvatar(base64);
            UI?.toast?.('头像上传成功', 'success');
        } catch (error) {
            UI?.toast?.('上传失败: ' + error.message, 'error');
        }
    },

    /**
     * 处理图片
     */
    async _processImage(file) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            
            img.onload = () => {
                const maxSize = 200;
                let { width, height } = img;
                
                if (width > maxSize || height > maxSize) {
                    if (width > height) {
                        height = height * (maxSize / width);
                        width = maxSize;
                    } else {
                        width = width * (maxSize / height);
                        height = maxSize;
                    }
                }

                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, width, height);
                
                URL.revokeObjectURL(img.src);
                resolve(canvas.toDataURL('image/jpeg', 0.8));
            };
            
            img.onerror = () => {
                URL.revokeObjectURL(img.src);
                reject(new Error('图片加载失败'));
            };
            
            img.src = URL.createObjectURL(file);
        });
    },

    /**
     * 关闭弹窗
     */
    closeModal() {
        const modal = document.querySelector('.avatar-picker-modal');
        if (modal) {
            modal.classList.remove('show');
            setTimeout(() => modal.remove(), 300);
        }
    },

    /**
     * 显示模态框（安全版本）
     */
    showModal(options = {}) {
        const { onSelect, current, title } = options;
        
        // 检查是否已存在
        let existingModal = document.querySelector('.avatar-picker-modal');
        if (existingModal) {
            existingModal.remove();
        }

        // renderModal 返回的是 DOM 元素，直接使用
        const modal = this.renderModal({ onSelect, current, title });
        document.body.appendChild(modal);

        // 点击背景关闭
        modal.addEventListener('click', (e) => {
            if (e.target.classList.contains('avatar-picker-modal') || e.target === modal) {
                this.closeModal();
            }
        });
    },

    /**
     * 生成带颜色的字母头像
     */
    generateInitialAvatar(text, color, size = 100) {
        const initial = text.charAt(0).toUpperCase();
        
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        
        const ctx = canvas.getContext('2d');
        
        // 圆形背景
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
        ctx.fill();
        
        // 字母
        ctx.fillStyle = 'white';
        ctx.font = `bold ${size * 0.6}px -apple-system, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(initial, size / 2, size / 2);
        
        return canvas.toDataURL('image/png');
    }
};

// 样式
const avatarPickerStyles = `
    .avatar-picker {
        padding: 8px;
    }
    
    .avatar-section {
        margin-bottom: 16px;
    }
    
    .avatar-section-title {
        font-size: 12px;
        font-weight: 600;
        color: var(--text-secondary, #8e8e93);
        margin-bottom: 8px;
        padding: 0 4px;
    }
    
    .avatar-grid {
        display: grid;
        gap: 8px;
    }
    
    .avatar-item {
        aspect-ratio: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 24px;
        background: var(--bg-secondary, #f2f2f7);
        border-radius: 12px;
        cursor: pointer;
        transition: all 0.2s;
        position: relative;
        overflow: hidden;
    }
    
    .avatar-item:hover {
        transform: scale(1.08);
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
    }
    
    .avatar-item.selected {
        border: 2px solid var(--accent, #FF6B35);
        box-shadow: 0 0 0 3px rgba(255, 107, 53, 0.2);
    }
    
    .avatar-item.custom img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }
    
    .avatar-item.color .avatar-initial {
        font-size: 28px;
        font-weight: bold;
    }
    
    .avatar-delete {
        position: absolute;
        top: 2px;
        right: 2px;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        background: rgba(0,0,0,0.6);
        color: white;
        border: none;
        cursor: pointer;
        font-size: 12px;
        line-height: 1;
        opacity: 0;
        transition: opacity 0.2s;
    }
    
    .avatar-item:hover .avatar-delete {
        opacity: 1;
    }
    
    .avatar-upload-section {
        text-align: center;
        padding: 16px;
        border-top: 1px solid var(--separator, rgba(0,0,0,0.1));
        margin-top: 8px;
    }
    
    .avatar-upload-btn {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 20px;
        background: var(--accent-light, rgba(255, 107, 53, 0.1));
        border: 1px dashed var(--accent, #FF6B35);
        border-radius: 8px;
        color: var(--accent, #FF6B35);
        cursor: pointer;
        font-size: 14px;
        transition: all 0.2s;
    }
    
    .avatar-upload-btn:hover {
        background: var(--accent-light, rgba(255, 107, 53, 0.2));
    }
    
    .upload-icon {
        font-size: 18px;
    }
    
    .upload-hint {
        font-size: 11px;
        color: var(--text-tertiary, #aeaeb2);
        margin-top: 8px;
    }
    
    /* 模态框样式 */
    .avatar-picker-modal {
        position: fixed;
        inset: 0;
        background: rgba(0,0,0,0.5);
        display: flex;
        align-items: center;
        justify-content: center;
        z-index: 9999;
        opacity: 0;
        visibility: hidden;
        transition: all 0.2s;
    }
    
    .avatar-picker-modal.show {
        opacity: 1;
        visibility: visible;
    }
    
    .avatar-picker-content {
        background: var(--bg-primary, white);
        border-radius: 16px;
        width: 90%;
        max-width: 400px;
        max-height: 80vh;
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }
    
    .avatar-picker-content .modal-body {
        overflow-y: auto;
        padding: 0;
    }
    
    .modal-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 16px;
        border-bottom: 1px solid var(--separator, rgba(0,0,0,0.1));
    }
    
    .modal-title {
        font-size: 16px;
        font-weight: 600;
        color: var(--text-primary, #1c1c1e);
    }
    
    .modal-close {
        width: 28px;
        height: 28px;
        border-radius: 50%;
        background: var(--bg-secondary, #f2f2f7);
        border: none;
        cursor: pointer;
        font-size: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
    }
`;

// 注入样式（安全版本：只注入一次）
if (typeof document !== 'undefined' && !document.getElementById('avatar-picker-styles')) {
    const style = document.createElement('style');
    style.id = 'avatar-picker-styles';
    style.textContent = avatarPickerStyles;
    document.head.appendChild(style);
}

// 初始化
AvatarPicker.init();
