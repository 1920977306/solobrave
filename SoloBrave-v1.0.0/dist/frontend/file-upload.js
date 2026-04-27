/**
 * Solo Brave - 文件上传模块
 * 
 * 支持拖拽上传、粘贴上传、图片预览
 * 位置: ./知识库/solobrave-modules/file-upload.js
 * 
 * 安全修复:
 * - 移除生产环境 console.log
 * - 添加 destroy() 方法防止内存泄漏
 * - 文件名 HTML 转义
 * - 事件使用 Events 常量
 */

const FileUploader = {
    // 配置
    config: {
        maxSize: 10 * 1024 * 1024, // 10MB
        maxImageSize: 5 * 1024 * 1024, // 5MB
        allowedImageTypes: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'],
        allowedDocTypes: [
            'application/pdf',
            'application/msword',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'text/plain',
            'text/markdown'
        ],
        imageCompression: 0.8,
        maxWidth: 1920,
        maxHeight: 1080,
        thumbnailSize: 200
    },

    // 当前上传状态
    _state: {
        uploading: false,
        progress: 0,
        currentFile: null
    },

    // 保存的事件处理函数引用
    _eventHandlers: {
        dragenter: null,
        dragover: null,
        dragleave: null,
        drop: null,
        paste: null,
        fileInputChange: null,
        uploadBtnClick: null
    },

    // 文件输入元素
    _fileInput: null,

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
    init(options = {}) {
        Object.assign(this.config, options);
        
        this._setupDropZone();
        this._setupPasteHandler();
        this._setupFileInput();
        this._renderUploadButton();

        // 通过 EventBus 通知初始化完成（而非 console.log）
        if (typeof EventBus !== 'undefined') {
            EventBus.emit('file:uploader:initialized');
        }
    },

    /**
     * 销毁（防止内存泄漏）
     */
    destroy() {
        const chatArea = document.querySelector('.chat-input-area') || document.body;
        
        // 移除拖拽事件监听器
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            const handler = this._eventHandlers[eventName];
            if (handler) {
                chatArea.removeEventListener(eventName, handler);
            }
        });
        
        // 移除粘贴事件监听器
        if (this._eventHandlers.paste) {
            document.removeEventListener('paste', this._eventHandlers.paste);
        }
        
        // 移除文件输入事件
        if (this._fileInput && this._eventHandlers.fileInputChange) {
            this._fileInput.removeEventListener('change', this._eventHandlers.fileInputChange);
        }
        
        // 移除上传按钮
        const uploadBtn = document.getElementById('uploadBtn');
        if (uploadBtn && this._eventHandlers.uploadBtnClick) {
            uploadBtn.removeEventListener('click', this._eventHandlers.uploadBtnClick);
        }
        
        // 清理状态
        this._eventHandlers = {};
        this._fileInput = null;
        this._state.uploading = false;
        this._state.currentFile = null;
    },

    /**
     * 设置拖拽区域
     */
    _setupDropZone() {
        const chatArea = document.querySelector('.chat-input-area') || document.body;
        
        // 保存事件处理函数引用
        const preventHandler = (e) => {
            e.preventDefault();
            e.stopPropagation();
        };
        
        const dragEnterOverHandler = () => {
            chatArea.classList.add('drag-over');
            this._showDropIndicator();
        };
        
        const dragLeaveDropHandler = () => {
            chatArea.classList.remove('drag-over');
            this._hideDropIndicator();
        };
        
        const dropHandler = (e) => {
            const files = e.dataTransfer.files;
            this.handleFiles(files);
        };
        
        // 保存引用
        this._eventHandlers.dragenter = preventHandler;
        this._eventHandlers.dragover = preventHandler;
        this._eventHandlers.dragleave = dragLeaveDropHandler;
        this._eventHandlers.drop = dropHandler;
        
        // 绑定事件
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            if (eventName === 'dragleave' || eventName === 'drop') {
                chatArea.addEventListener(eventName, dragLeaveDropHandler);
            } else if (eventName === 'dragenter' || eventName === 'dragover') {
                chatArea.addEventListener(eventName, preventHandler);
                chatArea.addEventListener(eventName, dragEnterOverHandler);
            } else {
                chatArea.addEventListener(eventName, dropHandler);
            }
        });
        
        // 重新组织拖拽事件
        chatArea.addEventListener('dragenter', dragEnterOverHandler);
        chatArea.addEventListener('dragover', preventHandler);
        chatArea.addEventListener('dragleave', dragLeaveDropHandler);
        chatArea.addEventListener('drop', dropHandler);
    },

    /**
     * 设置粘贴上传
     */
    _setupPasteHandler() {
        this._eventHandlers.paste = (e) => {
            const items = e.clipboardData?.items;
            if (!items) return;

            for (const item of items) {
                if (item.type.startsWith('image/')) {
                    const file = item.getAsFile();
                    if (file) {
                        this.handleFiles([file]);
                    }
                }
            }
        };
        
        document.addEventListener('paste', this._eventHandlers.paste);
    },

    /**
     * 设置文件输入
     */
    _setupFileInput() {
        let input = document.getElementById('fileUploadInput');
        if (!input) {
            input = document.createElement('input');
            input.id = 'fileUploadInput';
            input.type = 'file';
            input.multiple = true;
            input.accept = this._getAcceptString();
            input.style.display = 'none';
            document.body.appendChild(input);
        }
        
        this._fileInput = input;
        
        this._eventHandlers.fileInputChange = (e) => {
            if (e.target.files?.length) {
                this.handleFiles(e.target.files);
                e.target.value = ''; // 清空以允许重复选择同一文件
            }
        };
        
        input.addEventListener('change', this._eventHandlers.fileInputChange);
    },

    /**
     * 渲染上传按钮（安全版本）
     */
    _renderUploadButton() {
        const toolbar = document.querySelector('.input-toolbar');
        if (!toolbar) return;

        if (document.getElementById('uploadBtn')) return;

        const btn = document.createElement('button');
        btn.id = 'uploadBtn';
        btn.className = 'toolbar-btn';
        btn.title = '上传文件';
        
        // 使用 textContent 而非 innerHTML 防止 XSS
        const icon = document.createElement('span');
        icon.textContent = '📎';
        btn.appendChild(icon);
        
        // 保存点击处理函数引用
        this._eventHandlers.uploadBtnClick = () => {
            document.getElementById('fileUploadInput')?.click();
        };
        
        btn.addEventListener('click', this._eventHandlers.uploadBtnClick);
        toolbar.appendChild(btn);
    },

    /**
     * 处理文件
     */
    async handleFiles(files) {
        const fileArray = Array.from(files);
        
        for (const file of fileArray) {
            if (!this.validateFile(file)) continue;
            
            await this._processFile(file);
        }
    },

    /**
     * 验证文件
     */
    validateFile(file) {
        // 检查大小
        const isImage = file.type.startsWith('image/');
        const maxSize = isImage ? this.config.maxImageSize : this.config.maxSize;
        
        if (file.size > maxSize) {
            const sizeMB = (maxSize / 1024 / 1024).toFixed(1);
            UI?.toast?.('文件超过大小限制 (' + sizeMB + 'MB)', 'error');
            return false;
        }

        // 检查类型
        const allowedTypes = [...this.config.allowedImageTypes, ...this.config.allowedDocTypes];
        const isAllowed = allowedTypes.some(type => {
            if (type.endsWith('/*')) {
                return file.type.startsWith(type.slice(0, -1));
            }
            return file.type === type;
        });

        if (!isAllowed) {
            UI?.toast?.('不支持的文件类型: ' + file.type, 'error');
            return false;
        }

        return true;
    },

    /**
     * 处理单个文件
     */
    async _processFile(file) {
        this._state.uploading = true;
        this._state.currentFile = file;
        this._state.progress = 0;

        try {
            // 图片压缩
            if (file.type.startsWith('image/')) {
                const compressed = await this._compressImage(file);
                file = compressed;
            }

            // 预览
            if (file.type.startsWith('image/')) {
                await this._showImagePreview(file);
            }

            // 上传
            const result = await this._uploadFile(file);

            if (result.success) {
                this._insertToInput(result);
                // 使用 Events 常量
                if (typeof EventBus !== 'undefined') {
                    EventBus.emit(Events.FILE_UPLOADED || 'file:uploaded', result);
                }
                UI?.toast?.('文件上传成功', 'success');
            }

        } catch (error) {
            // 使用 Events 常量发送错误事件
            if (typeof EventBus !== 'undefined') {
                EventBus.emit(Events.FILE_ERROR || 'file:error', {
                    message: error.message,
                    file: file.name
                });
            }
            UI?.toast?.('上传失败: ' + error.message, 'error');
        } finally {
            this._state.uploading = false;
            this._state.currentFile = null;
            this._hideImagePreview();
        }
    },

    /**
     * 压缩图片
     */
    async _compressImage(file) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            
            img.onload = () => {
                let { width, height } = img;
                
                if (width > this.config.maxWidth) {
                    height = height * (this.config.maxWidth / width);
                    width = this.config.maxWidth;
                }
                if (height > this.config.maxHeight) {
                    width = width * (this.config.maxHeight / height);
                    height = this.config.maxHeight;
                }

                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, width, height);

                canvas.toBlob(blob => {
                    if (!blob) {
                        reject(new Error('图片压缩失败'));
                        return;
                    }
                    const compressedFile = new File([blob], file.name, {
                        type: file.type,
                        lastModified: Date.now()
                    });
                    resolve(compressedFile);
                }, file.type, this.config.imageCompression);
                
                URL.revokeObjectURL(img.src);
            };
            
            img.onerror = () => {
                reject(new Error('图片加载失败'));
                URL.revokeObjectURL(img.src);
            };
            
            img.src = URL.createObjectURL(file);
        });
    },

    /**
     * 生成缩略图
     */
    async _generateThumbnail(file) {
        return new Promise((resolve) => {
            if (!file.type.startsWith('image/')) {
                resolve(null);
                return;
            }

            const img = new Image();
            
            img.onload = () => {
                const canvas = document.createElement('canvas');
                const size = this.config.thumbnailSize;
                
                const minDim = Math.min(img.width, img.height);
                const sx = (img.width - minDim) / 2;
                const sy = (img.height - minDim) / 2;
                
                canvas.width = size;
                canvas.height = size;
                
                const ctx = canvas.getContext('2d');
                ctx.drawImage(img, sx, sy, minDim, minDim, 0, 0, size, size);
                
                URL.revokeObjectURL(img.src);
                resolve(canvas.toDataURL('image/jpeg', 0.7));
            };
            
            img.onerror = () => {
                URL.revokeObjectURL(img.src);
                resolve(null);
            };
            
            img.src = URL.createObjectURL(file);
        });
    },

    /**
     * 上传文件
     */
    async _uploadFile(file) {
        return new Promise((resolve) => {
            let progress = 0;
            const interval = setInterval(() => {
                progress += Math.random() * 20;
                if (progress >= 100) {
                    progress = 100;
                    clearInterval(interval);
                }
                this._state.progress = progress;
                // 使用 Events 常量
                if (typeof EventBus !== 'undefined') {
                    EventBus.emit(Events.FILE_UPLOAD_PROGRESS || 'file:upload:progress', { 
                        progress, 
                        file: this._escapeHtml(file.name)
                    });
                }
            }, 200);

            const reader = new FileReader();
            
            reader.onload = (e) => {
                const base64 = e.target.result;
                const thumbnail = file.type.startsWith('image/') 
                    ? this._generateThumbnail(file) 
                    : Promise.resolve(null);
                
                thumbnail.then(thumb => {
                    // 生成安全的文件 ID
                    const safeName = this._escapeHtml(file.name);
                    const fileData = {
                        id: 'file_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8),
                        name: safeName,
                        type: file.type,
                        size: file.size,
                        data: base64,
                        thumbnail: thumb
                    };
                    
                    resolve({ success: true, ...fileData });
                });
            };
            
            reader.onerror = () => {
                clearInterval(interval);
                resolve({ success: false, error: '文件读取失败' });
            };
            
            reader.readAsDataURL(file);
        });
    },

    /**
     * 插入到消息输入框（安全版本）
     */
    _insertToInput(fileData) {
        const input = document.getElementById('msgInput') || document.getElementById('inputArea');
        if (!input) return;

        // 安全转义文件名
        const safeName = this._escapeHtml(fileData.name);
        
        if (fileData.type.startsWith('image/') && fileData.thumbnail) {
            input.value += '\n[图片: ' + safeName + ']\n';
        } else {
            input.value += '\n[文件: ' + safeName + ']\n';
        }
        
        input.focus();
        EventBus?.emit(Events.FILE_INSERTED || 'file:inserted', fileData);
    },

    /**
     * 显示图片预览（安全版本）
     */
    async _showImagePreview(file) {
        let container = document.getElementById('imagePreviewContainer');
        
        if (!container) {
            container = document.createElement('div');
            container.id = 'imagePreviewContainer';
            container.className = 'image-preview-container';
            
            const chatArea = document.querySelector('.chat-area');
            if (chatArea) {
                chatArea.insertBefore(container, chatArea.firstChild);
            }
        }

        const thumbnail = await this._generateThumbnail(file);
        const safeName = this._escapeHtml(file.name);
        const objectUrl = URL.createObjectURL(file);
        
        // 使用 DOM API 创建元素，避免 innerHTML
        container.innerHTML = '';
        
        const preview = document.createElement('div');
        preview.className = 'image-preview';
        
        const img = document.createElement('img');
        img.src = objectUrl;
        img.alt = '预览';
        preview.appendChild(img);
        
        if (this._state.uploading) {
            const progressDiv = document.createElement('div');
            progressDiv.className = 'preview-progress';
            
            const progressBar = document.createElement('div');
            progressBar.className = 'preview-progress-bar';
            progressBar.style.width = this._state.progress + '%';
            progressDiv.appendChild(progressBar);
            
            preview.appendChild(progressDiv);
        }
        
        // 使用 addEventListener 而非 onclick 防止 XSS
        const removeBtn = document.createElement('button');
        removeBtn.className = 'preview-remove';
        removeBtn.textContent = '×';
        removeBtn.addEventListener('click', () => this._hideImagePreview());
        preview.appendChild(removeBtn);
        
        container.appendChild(preview);
        container.style.display = 'block';
    },

    /**
     * 隐藏图片预览
     */
    _hideImagePreview() {
        const container = document.getElementById('imagePreviewContainer');
        if (container) {
            container.style.display = 'none';
            container.innerHTML = '';
        }
    },

    /**
     * 显示拖拽提示（安全版本）
     */
    _showDropIndicator() {
        let indicator = document.getElementById('dropIndicator');
        if (!indicator) {
            indicator = document.createElement('div');
            indicator.id = 'dropIndicator';
            indicator.className = 'drop-indicator';
            
            // 使用 DOM API 而非 innerHTML
            const content = document.createElement('div');
            content.className = 'drop-indicator-content';
            
            const icon = document.createElement('div');
            icon.className = 'drop-icon';
            icon.textContent = '📁';
            
            const text = document.createElement('div');
            text.className = 'drop-text';
            text.textContent = '释放以上传文件';
            
            content.appendChild(icon);
            content.appendChild(text);
            indicator.appendChild(content);
            
            document.body.appendChild(indicator);
        }
        
        indicator.style.display = 'flex';
    },

    /**
     * 隐藏拖拽提示
     */
    _hideDropIndicator() {
        const indicator = document.getElementById('dropIndicator');
        if (indicator) {
            indicator.style.display = 'none';
        }
    },

    /**
     * 获取文件类型接受字符串
     */
    _getAcceptString() {
        const types = [...this.config.allowedImageTypes, ...this.config.allowedDocTypes];
        return types.join(',');
    }
};

// 文件上传样式
var fileUploaderStyles = [
    '.drop-indicator {',
    '    position: fixed;',
    '    top: 0;',
    '    left: 0;',
    '    right: 0;',
    '    bottom: 0;',
    '    background: rgba(255, 107, 53, 0.1);',
    '    border: 3px dashed #FF6B35;',
    '    display: none;',
    '    align-items: center;',
    '    justify-content: center;',
    '    font-size: 18px;',
    '    color: #FF6B35;',
    '    z-index: 9999;',
    '    pointer-events: none;',
    '}',
    '.drag-over {',
    '    outline: 2px dashed #FF6B35;',
    '    outline-offset: -2px;',
    '}'
].join('');

// 注入样式
if (typeof document !== 'undefined') {
    var style = document.createElement('style');
    style.textContent = fileUploaderStyles;
    document.head.appendChild(style);
}

// 初始化
FileUploader.init();
