/**
 * OpenClawClient Core - Connection & Message Handling
 * Extracted from openclaw-client.js to keep files under 500 lines
 */

(function(global) {
  'use strict';

  function generateId() {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
      var r = Math.random() * 16 | 0;
      return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
    });
  }

  function now() { return Date.now(); }

  // ===== OpenClawClient Core Class =====
  function OpenClawClient(options) {
    options = options || {};
    this.wsUrl = options.wsUrl || 'ws://127.0.0.1:18789';
    this.token = options.token || localStorage.getItem('openclaw_token') || '';
    this.mockMode = options.mockMode || false;
    this.reconnectInterval = options.reconnectInterval || 3000;
    this.maxReconnectAttempts = options.maxReconnectAttempts || 5;

    this.ws = null;
    this.status = 'disconnected';
    this.reconnectAttempts = 0;
    this.reconnectTimer = null;
    this.pendingRequests = {};
    this.eventHandlers = {};
    this.seq = 0;
    this.features = [];
    this.snapshot = null;

    if (!this.mockMode) {
      this.connect();
    }
  }

  // ===== Connection Management =====
  OpenClawClient.prototype.connect = function() {
    var self = this;
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return Promise.resolve();
    }

    this.status = 'connecting';
    this._emit('statusChange', { status: 'connecting' });

    if (this.mockMode) {
      this.status = 'connected';
      this._emit('statusChange', { status: 'connected' });
      return Promise.resolve();
    }

    return new Promise(function(resolve, reject) {
      try {
        self.ws = new WebSocket(self.wsUrl);

        self.ws.onopen = function() {
          self.status = 'connected';
          self.reconnectAttempts = 0;
          self._emit('statusChange', { status: 'connected' });
          resolve();
        };

        self.ws.onmessage = function(event) {
          self._handleMessage(event.data);
        };

        self.ws.onerror = function(error) {
          self._emit('error', error);
          if (self.status === 'connecting') {
            reject(error);
          }
        };

        self.ws.onclose = function() {
          self.status = 'disconnected';
          self._emit('statusChange', { status: 'disconnected' });
          self._scheduleReconnect();
        };
      } catch (err) {
        reject(err);
      }
    });
  };

  OpenClawClient.prototype.disconnect = function() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this.status = 'disconnected';
    this._emit('statusChange', { status: 'disconnected' });
  };

  OpenClawClient.prototype._scheduleReconnect = function() {
    var self = this;
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this._emit('maxReconnectReached');
      return;
    }
    this.reconnectAttempts++;
    this.reconnectTimer = setTimeout(function() {
      self.connect();
    }, this.reconnectInterval * this.reconnectAttempts);
  };

  // ===== Message Handling =====
  OpenClawClient.prototype._handleMessage = function(data) {
    try {
      var msg = JSON.parse(data);

      if (msg.type === 'res') {
        var pending = this.pendingRequests[msg.id];
        if (pending) {
          delete this.pendingRequests[msg.id];
          if (msg.ok) {
            pending.resolve(msg.payload);
          } else {
            pending.reject(new Error(msg.error || 'Request failed'));
          }
        }
      } else if (msg.type === 'event') {
        this._emit(msg.event, msg.payload);
        this._emit('*', msg);
      }
    } catch (err) {
      console.error('Failed to parse message:', err);
    }
  };

  // ===== Request Sending =====
  OpenClawClient.prototype.request = function(method, params) {
    var self = this;
    var id = generateId();

    if (this.mockMode && this._mockRequest) {
      return this._mockRequest(method, params);
    }

    return new Promise(function(resolve, reject) {
      if (!self.ws || self.ws.readyState !== WebSocket.OPEN) {
        reject(new Error('WebSocket not connected'));
        return;
      }

      self.pendingRequests[id] = { resolve: resolve, reject: reject };

      var msg = {
        type: 'req',
        id: id,
        method: method,
        params: params || {}
      };

      self.ws.send(JSON.stringify(msg));

      setTimeout(function() {
        if (self.pendingRequests[id]) {
          delete self.pendingRequests[id];
          reject(new Error('Request timeout'));
        }
      }, 30000);
    });
  };

  // ===== Event System =====
  OpenClawClient.prototype.on = function(event, handler) {
    if (!this.eventHandlers[event]) {
      this.eventHandlers[event] = [];
    }
    this.eventHandlers[event].push(handler);
    return this;
  };

  OpenClawClient.prototype.off = function(event, handler) {
    if (!this.eventHandlers[event]) return this;
    var idx = this.eventHandlers[event].indexOf(handler);
    if (idx > -1) {
      this.eventHandlers[event].splice(idx, 1);
    }
    return this;
  };

  OpenClawClient.prototype._emit = function(event, data) {
    var handlers = this.eventHandlers[event];
    if (handlers) {
      handlers.forEach(function(fn) {
        try { fn(data); } catch (e) { console.error(e); }
      });
    }
  };

  // ===== Singleton Export =====
  var instance = null;
  OpenClawClient.getInstance = function(options) {
    if (!instance) {
      instance = new OpenClawClient(options);
    }
    return instance;
  };

  OpenClawClient.resetInstance = function() {
    if (instance) {
      instance.disconnect();
      instance = null;
    }
  };

  // Export
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = OpenClawClient;
  }
  global.OpenClawClient = OpenClawClient;

})(typeof window !== 'undefined' ? window : this);
