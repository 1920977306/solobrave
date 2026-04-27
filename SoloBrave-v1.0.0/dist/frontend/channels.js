/**
 * Solo Brave Channels - 渠道抽象层
 * 
 * 统一的渠道接口，支持多种消息渠道
 * 工厂模式 + 适配器模式
 */

var Channels = (function() {
    'use strict';
    
    // ===== 渠道类型 =====
    var CHANNEL_TYPES = {
        WEB: 'web',           // 网页聊天
        TERMINAL: 'terminal', // 命令行
        DINGTALK: 'dingtalk', // 钉钉
        WECHAT: 'wechat',     // 微信
        SLACK: 'slack',       // Slack
        DISCORD: 'discord',   // Discord
        API: 'api'            // REST API
    };
    
    // ===== 渠道注册表 =====
    var channels = {};
    var channelFactories = {
        [CHANNEL_TYPES.WEB]: function() { return Object.assign({}, WebChannel); },
        [CHANNEL_TYPES.TERMINAL]: function() { return Object.assign({}, TerminalChannel); },
        [CHANNEL_TYPES.DINGTALK]: function() { return Object.assign({}, DingTalkChannel); },
        [CHANNEL_TYPES.API]: function() { return Object.assign({}, ApiChannel); }
    };
    var defaultChannel = null;
    
    // ===== 消息接口 =====
    var MessageInterface = {
        id: null,
        channel: null,
        type: 'text',         // text, image, file, audio, video
        content: '',
        sender: null,          // { id, name, avatar }
        receiver: null,       // { id, name, avatar }
        timestamp: null,
        metadata: {}          // 扩展数据
    };
    
    // ===== 基础渠道类 =====
    var BaseChannel = {
        type: null,
        config: {},
        
        // 连接
        connect: function() {
            return Promise.resolve({ success: true });
        },
        
        // 断开
        disconnect: function() {
            return Promise.resolve({ success: true });
        },
        
        // 发送消息
        send: function(message) {
            return Promise.reject(new Error('send not implemented'));
        },
        
        // 接收消息
        onMessage: function(callback) {
            // override in subclass
        },
        
        // 状态变化
        onStatusChange: function(callback) {
            // override in subclass
        },
        
        // 获取状态
        getStatus: function() {
            return 'disconnected';
        }
    };
    
    // ===== Web 渠道 =====
    var WebChannel = Object.assign({}, BaseChannel, {
        type: CHANNEL_TYPES.WEB,
        status: 'disconnected',
        
        connect: function() {
            var self = this;
            return new Promise(function(resolve) {
                self.status = 'connected';
                console.log('[Channel:Web] Connected');
                resolve({ success: true });
            });
        },
        
        disconnect: function() {
            this.status = 'disconnected';
            return Promise.resolve({ success: true });
        },
        
        send: function(message) {
            // Web 渠道通过 EventBus 发送
            EventBus.emit('channel:message:send', {
                channel: this.type,
                message: message
            });
            return Promise.resolve({ success: true, messageId: message.id });
        },
        
        getStatus: function() {
            return this.status;
        },
        
        // 模拟接收消息
        receive: function(message) {
            EventBus.emit('channel:message:receive', {
                channel: this.type,
                message: message
            });
        }
    });
    
    // ===== Terminal 渠道 =====
    var TerminalChannel = Object.assign({}, BaseChannel, {
        type: CHANNEL_TYPES.TERMINAL,
        status: 'disconnected',
        
        connect: function() {
            this.status = 'connected';
            console.log('[Channel:Terminal] Connected');
            return Promise.resolve({ success: true });
        },
        
        disconnect: function() {
            this.status = 'disconnected';
            return Promise.resolve({ success: true });
        },
        
        send: function(message) {
            // Terminal 直接输出
            console.log('[Terminal]', message.content);
            return Promise.resolve({ success: true });
        },
        
        getStatus: function() {
            return this.status;
        }
    });
    
    // ===== DingTalk 渠道 =====
    var DingTalkChannel = Object.assign({}, BaseChannel, {
        type: CHANNEL_TYPES.DINGTALK,
        status: 'disconnected',
        _accessToken: null,
        _tokenExpiry: null,
        config: {
            clientId: null,
            clientSecret: null,
            agentId: null,
            webhook: null,
            // API 地址（可配置）
            apiBase: 'https://oapi.dingtalk.com'
        },
        
        connect: function(config) {
            var self = this;
            Object.assign(this.config, config);
            
            // 配置检查
            if (!this.config.clientId || !this.config.clientSecret) {
                console.warn('[Channel:DingTalk] Warning: clientId/clientSecret not configured. Using demo mode.');
                this.status = 'demo';
                return Promise.resolve({ success: true, demo: true });
            }
            
            // 获取 access_token
            return this._getAccessToken().then(function(token) {
                self._accessToken = token;
                self._tokenExpiry = Date.now() + 7200000; // 2小时
                self.status = 'connected';
                console.log('[Channel:DingTalk] Connected successfully');
                return { success: true, token: token };
            }).catch(function(err) {
                console.error('[Channel:DingTalk] Connection failed:', err);
                self.status = 'error';
                return { success: false, error: err.message };
            });
        },
        
        // 获取 access_token（真实 OAuth 流程）
        _getAccessToken: function() {
            var self = this;
            
            return new Promise(function(resolve, reject) {
                // 如果是演示模式，返回模拟 token
                if (!self.config.clientId || !self.config.clientSecret) {
                    resolve('demo_token_' + Date.now());
                    return;
                }
                
                // 真实 OAuth 请求
                var url = self.config.apiBase + '/gettoken?' + 
                    'appkey=' + encodeURIComponent(self.config.clientId) + 
                    '&appsecret=' + encodeURIComponent(self.config.clientSecret);
                
                fetch(url).then(function(response) {
                    return response.json();
                }).then(function(data) {
                    if (data.errcode === 0 && data.access_token) {
                        resolve(data.access_token);
                    } else {
                        reject(new Error(data.errmsg || 'Failed to get token'));
                    }
                }).catch(function(err) {
                    // 网络错误时降级到演示模式
                    console.warn('[Channel:DingTalk] Network error, using demo mode');
                    resolve('demo_token_' + Date.now());
                });
            });
        },
        
        disconnect: function() {
            this.status = 'disconnected';
            this._accessToken = null;
            this._tokenExpiry = null;
            return Promise.resolve({ success: true });
        },
        
        send: function(message) {
            var self = this;
            
            // 检查连接状态
            if (this.status === 'demo') {
                console.log('[Channel:DingTalk:Demo] Would send:', message.content.substring(0, 50));
                return Promise.resolve({ success: true, demo: true, msgId: 'demo_' + Date.now() });
            }
            
            if (this.status !== 'connected') {
                return Promise.reject(new Error('Channel not connected'));
            }
            
            return new Promise(function(resolve, reject) {
                // 真实发送逻辑
                var payload = {
                    agent_id: self.config.agentId,
                    msgtype: 'text',
                    text: { content: message.content }
                };
                
                // 发送消息 API
                var url = self.config.apiBase + '/message/send_to_conversation?' + 
                    'access_token=' + encodeURIComponent(self._accessToken);
                
                fetch(url, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                }).then(function(response) {
                    return response.json();
                }).then(function(data) {
                    if (data.errcode === 0) {
                        console.log('[Channel:DingTalk] Sent:', message.content.substring(0, 50));
                        resolve({ success: true, msgId: data.msg_id });
                    } else {
                        reject(new Error(data.errmsg || 'Send failed'));
                    }
                }).catch(function(err) {
                    // 网络错误时降级
                    console.warn('[Channel:DingTalk] Network error during send');
                    resolve({ success: false, demo: true, error: err.message });
                });
            });
        },
        
        sendMarkdown: function(title, content) {
            var message = {
                msgtype: 'markdown',
                markdown: {
                    title: title,
                    text: content
                }
            };
            return this.send(message);
        },
        
        sendActionCard: function(title, content, actions) {
            var message = {
                msgtype: 'actionCard',
                actionCard: {
                    title: title,
                    text: content,
                    btnOrientation: '0',
                    singleTitle: actions[0] ? actions[0].title : '查看',
                    singleURL: actions[0] ? actions[0].url : '#'
                }
            };
            return this.send(message);
        },
        
        getStatus: function() {
            return this.status;
        }
    });
    
    // ===== API 渠道 =====
    var ApiChannel = Object.assign({}, BaseChannel, {
        type: CHANNEL_TYPES.API,
        status: 'disconnected',
        config: {
            baseUrl: '/api',
            token: null,
            timeout: 30000
        },
        
        connect: function(config) {
            Object.assign(this.config, config);
            this.status = 'connected';
            console.log('[Channel:API] Connected to', this.config.baseUrl);
            return Promise.resolve({ success: true });
        },
        
        disconnect: function() {
            this.status = 'disconnected';
            return Promise.resolve({ success: true });
        },
        
        send: function(message) {
            var self = this;
            return new Promise(function(resolve, reject) {
                // 模拟 API 调用
                setTimeout(function() {
                    resolve({
                        success: true,
                        response: { message: 'ok', id: message.id }
                    });
                }, 200);
            });
        },
        
        request: function(method, path, data) {
            var self = this;
            return new Promise(function(resolve, reject) {
                console.log('[Channel:API]', method, path, data);
                setTimeout(function() {
                    resolve({ success: true, data: {} });
                }, 100);
            });
        },
        
        getStatus: function() {
            return this.status;
        }
    });
    
    // ===== 工厂函数 =====
    function createChannel(type, config) {
        var factory = channelFactories[type];
        if (!factory) {
            throw new Error('Unknown channel type: ' + type);
        }
        
        return factory();
    }
    
    // ===== 消息工厂 =====
    function createMessage(content, options) {
        options = options || {};
        
        return Object.assign({}, MessageInterface, {
            id: 'msg_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6),
            channel: options.channel || defaultChannel || CHANNEL_TYPES.WEB,
            type: options.type || 'text',
            content: content,
            sender: options.sender || { id: 'system', name: 'System' },
            receiver: options.receiver || null,
            timestamp: Date.now(),
            metadata: options.metadata || {}
        });
    }
    
    // ===== 注册渠道 =====
    function register(id, channel) {
        channels[id] = channel;
        
        // 如果是第一个渠道，设为默认
        if (!defaultChannel) {
            defaultChannel = id;
        }
        
        EventBus.emit('channel:registered', { id: id, channel: channel });
        console.log('[Channels] Registered:', id, '->', channel.type);
    }
    
    // ===== 初始化 =====
    function init() {
        // 注册默认渠道
        register('web', WebChannel);
        register('terminal', TerminalChannel);
        
        // 连接默认渠道
        connect('web').then(function() {
            console.log('[Channels] Default channel ready');
        });
        
        return channels;
    }
    
    // ===== 连接渠道 =====
    function connect(id, config) {
        var channel = channels[id];
        if (!channel) {
            return Promise.reject(new Error('Channel not found: ' + id));
        }
        
        var connectPromise = config ? channel.connect(config) : channel.connect();
        
        return connectPromise.then(function(result) {
            EventBus.emit('channel:status', { id: id, status: 'connected' });
            return result;
        }).catch(function(err) {
            EventBus.emit('channel:error', { id: id, error: err });
            throw err;
        });
    }
    
    // ===== 断开渠道 =====
    function disconnect(id) {
        var channel = channels[id];
        if (!channel) {
            return Promise.reject(new Error('Channel not found: ' + id));
        }
        
        return channel.disconnect().then(function(result) {
            EventBus.emit('channel:status', { id: id, status: 'disconnected' });
            return result;
        });
    }
    
    // ===== 发送消息 =====
    function send(channelId, content, options) {
        var channel = channels[channelId];
        if (!channel) {
            return Promise.reject(new Error('Channel not found: ' + channelId));
        }
        
        var message = createMessage(content, Object.assign({ channel: channelId }, options));
        
        return channel.send(message).then(function(result) {
            EventBus.emit('channel:message:sent', { channel: channelId, message: message });
            return Object.assign({ message: message }, result);
        });
    }
    
    // ===== 广播消息 =====
    function broadcast(content, options) {
        var promises = [];
        
        Object.keys(channels).forEach(function(id) {
            var channel = channels[id];
            if (channel.getStatus() === 'connected') {
                promises.push(send(id, content, options));
            }
        });
        
        return Promise.all(promises);
    }
    
    // ===== 获取渠道 =====
    function get(id) {
        return channels[id] || null;
    }
    
    function getAll() {
        return Object.assign({}, channels);
    }
    
    function getDefault() {
        return channels[defaultChannel] || null;
    }
    
    // ===== 设置默认渠道 =====
    function setDefault(id) {
        if (!channels[id]) {
            throw new Error('Channel not found: ' + id);
        }
        defaultChannel = id;
        EventBus.emit('channel:default', { id: id });
    }
    
    // ===== 获取状态 =====
    function getStatus(id) {
        var channel = channels[id];
        return channel ? channel.getStatus() : null;
    }
    
    function getAllStatuses() {
        var statuses = {};
        Object.keys(channels).forEach(function(id) {
            statuses[id] = channels[id].getStatus();
        });
        return statuses;
    }
    
    // ===== 获取支持的类型 =====
    function getSupportedTypes() {
        return Object.values(CHANNEL_TYPES);
    }
    
    // ===== 消息格式化 =====
    function formatMessage(message, format) {
        format = format || 'plain';
        
        switch (format) {
            case 'plain':
                return message.content;
                
            case 'markdown':
                return '**' + (message.sender ? message.sender.name : 'Unknown') + '**: ' + message.content;
                
            case 'json':
                return JSON.stringify(message, null, 2);
                
            case 'dingtalk':
                return {
                    msgtype: 'text',
                    text: { content: message.content }
                };
                
            case 'slack':
                return {
                    text: message.content,
                    username: message.sender ? message.sender.name : 'Bot'
                };
                
            default:
                return message.content;
        }
    }
    
    // ===== 导出 API =====
    return {
        // 常量
        TYPES: CHANNEL_TYPES,
        
        // 初始化
        init: init,
        
        // 渠道管理
        register: register,
        create: createChannel,
        
        // 连接管理
        connect: connect,
        disconnect: disconnect,
        
        // 消息
        send: send,
        broadcast: broadcast,
        createMessage: createMessage,
        formatMessage: formatMessage,
        
        // 获取
        get: get,
        getAll: getAll,
        getDefault: getDefault,
        setDefault: setDefault,
        
        // 状态
        getStatus: getStatus,
        getAllStatuses: getAllStatuses,
        
        // 类型
        getSupportedTypes: getSupportedTypes
    };
})();
