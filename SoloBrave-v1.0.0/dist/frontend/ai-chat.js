/**
 * Solo Brave - AI对话接口
 * 
 * 对接真实AI后端API
 * 位置: ./知识库/solobrave-modules/ai-chat.js
 * 
 * 安全修复:
 * - 移除生产环境 console.log
 * - 使用 Events 常量发送错误事件
 * - 添加错误类型常量
 * - 优化重试逻辑
 */

const AIChat = {
    // API配置
    config: {
        endpoint: '/api/v1/chat',
        timeout: 60000,
        retryCount: 3,
        retryDelay: 1000
    },
    
    // 当前会话状态
    _session: {
        conversationId: null,
        history: [],
        abortController: null
    },

    // 错误类型常量
    ERRORS: {
        NETWORK: 'network_error',
        TIMEOUT: 'timeout',
        ABORT: 'abort',
        SERVER: 'server_error',
        CLIENT: 'client_error',
        UNKNOWN: 'unknown'
    },

    /**
     * 初始化
     */
    init(options = {}) {
        Object.assign(this.config, options);
        
        // 尝试恢复会话
        const savedSession = Store.get('ai_session');
        if (savedSession) {
            this._session = savedSession;
        }
        
        // 通过 EventBus 通知初始化完成（而非 console.log）
        if (typeof EventBus !== 'undefined' && typeof Events !== 'undefined') {
            EventBus.emit(Events.AI_INITIALIZED || 'ai:initialized', { 
                hasSession: !!savedSession,
                historyLength: (savedSession?.history?.length || 0)
            });
        }
    },

    /**
     * 发送消息（非流式）
     */
    async send(message, options = {}) {
        const {
            employeeId = null,
            model = AI.currentModel,
            skill = null,
            temperature = 0.7,
            maxTokens = 2000
        } = options;

        // 构建请求
        const request = {
            message: message,
            model: model?.id || model,
            conversationId: this._session.conversationId,
            employeeId: employeeId,
            temperature: temperature,
            maxTokens: maxTokens,
            context: this._buildContext(skill),
            history: this._session.history.slice(-20) // 最近20条
        };

        try {
            State.set('isLoading', true);
            State.set('isSending', true);

            const response = await this._requestWithRetry(request);
            
            // 更新会话
            if (response.conversationId) {
                this._session.conversationId = response.conversationId;
            }
            
            // 添加到历史
            this._session.history.push(
                { role: 'user', content: message },
                { role: 'assistant', content: response.content }
            );
            
            // 保存会话
            this._saveSession();

            return {
                success: true,
                content: response.content,
                model: response.model,
                usage: response.usage,
                conversationId: response.conversationId
            };

        } catch (error) {
            this._emitError(Events.AI_ERROR || 'ai:error', error);
            return {
                success: false,
                error: error.message,
                type: error.type || this.ERRORS.UNKNOWN
            };
        } finally {
            State.set('isLoading', false);
            State.set('isSending', false);
        }
    },

    /**
     * 发送消息（流式响应）
     */
    async sendStream(message, options = {}, callbacks = {}) {
        const {
            employeeId = null,
            model = AI.currentModel,
            skill = null,
            temperature = 0.7
        } = options;

        const { onChunk, onComplete, onError } = callbacks;

        // 构建请求
        const request = {
            message: message,
            model: model?.id || model,
            conversationId: this._session.conversationId,
            employeeId: employeeId,
            temperature: temperature,
            stream: true,
            context: this._buildContext(skill)
        };

        try {
            State.set('isLoading', true);
            State.set('isSending', true);

            this._session.abortController = new AbortController();

            const response = await fetch(this.config.endpoint + '/stream', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request),
                signal: this._session.abortController.signal
            });

            if (!response.ok) {
                const error = new Error(`HTTP ${response.status}: ${response.statusText}`);
                error.type = this.ERRORS.SERVER;
                throw error;
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let fullContent = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                const chunk = decoder.decode(value);
                const lines = chunk.split('\n');

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = line.slice(6);
                        if (data === '[DONE]') continue;

                        try {
                            const parsed = JSON.parse(data);
                            if (parsed.content) {
                                fullContent += parsed.content;
                                if (onChunk) onChunk(parsed.content, fullContent);
                            }
                        } catch (e) {
                            // 忽略解析错误，继续处理下一行
                        }
                    }
                }
            }

            // 更新会话
            this._session.history.push(
                { role: 'user', content: message },
                { role: 'assistant', content: fullContent }
            );
            this._saveSession();

            if (onComplete) onComplete(fullContent);

            return {
                success: true,
                content: fullContent
            };

        } catch (error) {
            if (error.name === 'AbortError') {
                error.type = this.ERRORS.ABORT;
                this._emitError(Events.AI_ABORT || 'ai:abort', error);
            } else {
                this._emitError(Events.AI_ERROR || 'ai:error', error);
                if (onError) onError(error);
            }
            
            return {
                success: false,
                error: error.message,
                type: error.type || this.ERRORS.UNKNOWN
            };
        } finally {
            State.set('isLoading', false);
            State.set('isSending', false);
            this._session.abortController = null;
        }
    },

    /**
     * 取消请求
     */
    abort() {
        if (this._session.abortController) {
            this._session.abortController.abort();
        }
    },

    /**
     * 清空会话
     */
    clearSession() {
        this._session = {
            conversationId: null,
            history: [],
            abortController: null
        };
        Store.remove('ai_session');
        
        // 使用 Events 常量
        if (typeof EventBus !== 'undefined' && typeof Events !== 'undefined') {
            EventBus.emit(Events.AI_SESSION_CLEARED || 'ai:session:cleared');
        }
    },

    /**
     * 获取会话历史
     */
    getHistory() {
        return this._session.history;
    },

    /**
     * 构建上下文
     */
    _buildContext(skill) {
        const context = {
            memory: Memory?.getContextForAI?.(2000) || '',
            skill: skill ? Skills?.formatForPrompt?.(skill.id) || '' : '',
            knowledge: KnowledgeBase?.getContextForAI?.('查询') || ''
        };
        return context;
    },

    /**
     * 发送错误事件（统一方法）
     */
    _emitError(eventName, error) {
        if (typeof EventBus !== 'undefined') {
            EventBus.emit(eventName, {
                message: error.message,
                type: error.type || this.ERRORS.UNKNOWN,
                timestamp: Date.now()
            });
        }
    },

    /**
     * 带重试的请求（增强版）
     */
    async _requestWithRetry(request, retryCount = 0) {
        try {
            const response = await fetch(this.config.endpoint, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(request),
                signal: AbortSignal.timeout(this.config.timeout)
            });

            if (!response.ok) {
                const error = new Error(`HTTP ${response.status}: ${response.statusText}`);
                
                // 区分错误类型
                if (response.status >= 500) {
                    error.type = this.ERRORS.SERVER;
                } else if (response.status >= 400) {
                    error.type = this.ERRORS.CLIENT;
                } else {
                    error.type = this.ERRORS.UNKNOWN;
                }
                
                // 服务器错误可重试
                if (response.status >= 500 && retryCount < this.config.retryCount) {
                    await this._delay(this.config.retryDelay * (retryCount + 1));
                    return this._requestWithRetry(request, retryCount + 1);
                }
                throw error;
            }

            return await response.json();

        } catch (error) {
            // 区分错误类型
            if (error.name === 'AbortError') {
                error.type = this.ERRORS.ABORT;
            } else if (error.name === 'TimeoutError') {
                error.type = this.ERRORS.TIMEOUT;
            } else if (!error.type) {
                error.type = error.message.includes('fetch') ? this.ERRORS.NETWORK : this.ERRORS.UNKNOWN;
            }
            
            // 可重试的错误（网络错误或超时）
            if ((error.type === this.ERRORS.NETWORK || error.type === this.ERRORS.TIMEOUT) && 
                retryCount < this.config.retryCount) {
                await this._delay(this.config.retryDelay * (retryCount + 1));
                return this._requestWithRetry(request, retryCount + 1);
            }
            
            throw error;
        }
    },

    /**
     * 保存会话
     */
    _saveSession() {
        const toSave = {
            conversationId: this._session.conversationId,
            history: this._session.history.slice(-50) // 保留最近50条
        };
        Store.set('ai_session', toSave);
    },

    /**
     * 延迟
     */
    _delay(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
};

// 导出
if (typeof module !== 'undefined' && module.exports) {
    module.exports = AIChat;
}