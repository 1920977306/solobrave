/**
 * Solo Brave Framework - 异常处理
 * 
 * 统一的异常类型和处理器
 */

var Exception = (function() {
    'use strict';
    
    // ===== 异常基类 =====
    var BaseException = function(code, message, details) {
        this.name = 'BaseException';
        this.code = code || 500;
        this.message = message || 'Unknown error';
        this.details = details || null;
        this.timestamp = Date.now();
    };
    BaseException.prototype.toString = function() {
        return '[' + this.code + '] ' + this.message;
    };
    BaseException.prototype.toResult = function() {
        return Result.error(this.code, this.message);
    };
    
    // ===== 业务异常 =====
    var BusinessException = function(code, message, details) {
        this.name = 'BusinessException';
        BaseException.call(this, code, message, details);
    };
    BusinessException.prototype = Object.create(BaseException.prototype);
    BusinessException.prototype.constructor = BusinessException;
    
    // ===== 参数异常 =====
    var ParamException = function(message, details) {
        this.name = 'ParamException';
        BaseException.call(this, 400, message, details);
    };
    ParamException.prototype = Object.create(BaseException.prototype);
    ParamException.prototype.constructor = ParamException;
    
    // ===== 认证异常 =====
    var AuthException = function(message, details) {
        this.name = 'AuthException';
        BaseException.call(this, 401, message, details);
    };
    AuthException.prototype = Object.create(BaseException.prototype);
    AuthException.prototype.constructor = AuthException;
    
    // ===== 权限异常 =====
    var ForbiddenException = function(message, details) {
        this.name = 'ForbiddenException';
        BaseException.call(this, 403, message, details);
    };
    ForbiddenException.prototype = Object.create(BaseException.prototype);
    ForbiddenException.prototype.constructor = ForbiddenException;
    
    // ===== 未找到异常 =====
    var NotFoundException = function(resource, details) {
        this.name = 'NotFoundException';
        var message = resource ? resource + ' 不存在' : '资源不存在';
        BaseException.call(this, 404, message, details);
    };
    NotFoundException.prototype = Object.create(BaseException.prototype);
    NotFoundException.prototype.constructor = NotFoundException;
    
    // ===== 系统异常 =====
    var SystemException = function(message, details) {
        this.name = 'SystemException';
        BaseException.call(this, 500, message, details);
    };
    SystemException.prototype = Object.create(BaseException.prototype);
    SystemException.prototype.constructor = SystemException;
    
    // ===== 工具执行异常 =====
    var ToolException = function(toolId, message, details) {
        this.name = 'ToolException';
        this.toolId = toolId;
        BaseException.call(this, 605, message, details);
    };
    ToolException.prototype = Object.create(BaseException.prototype);
    ToolException.prototype.constructor = ToolException;
    
    // ===== 全局异常处理器 =====
    var handlers = [];
    
    function handle(exception) {
        // 调用注册的处理器
        for (var i = 0; i < handlers.length; i++) {
            try {
                var result = handlers[i](exception);
                if (result !== undefined) {
                    return result;
                }
            } catch (e) {
                console.error('[Exception] Handler error:', e);
            }
        }
        
        // 默认处理
        console.error('[Exception]', exception.name, exception.toString());
        return exception.toResult();
    }
    
    function addHandler(handler) {
        handlers.push(handler);
    }
    
    // ===== try-catch 包装 =====
    function wrap(fn, defaultMsg) {
        return function() {
            var args = Array.prototype.slice.call(arguments);
            try {
                var result = fn.apply(this, args);
                
                // 如果返回 Promise
                if (result && typeof result.then === 'function') {
                    return result.catch(function(e) {
                        console.error('[Exception]', e);
                        return Result.error(e.code || 500, e.message || defaultMsg || '操作失败');
                    });
                }
                
                return result;
            } catch (e) {
                console.error('[Exception]', e);
                return Result.error(e.code || 500, e.message || defaultMsg || '操作失败');
            }
        };
    }
    
    // ===== 导出 =====
    return {
        // 异常类型
        BaseException: BaseException,
        BusinessException: BusinessException,
        ParamException: ParamException,
        AuthException: AuthException,
        ForbiddenException: ForbiddenException,
        NotFoundException: NotFoundException,
        SystemException: SystemException,
        ToolException: ToolException,
        
        // 处理器
        handle: handle,
        addHandler: addHandler,
        
        // 工具方法
        wrap: wrap
    };
})();
