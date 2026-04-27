/**
 * Solo Brave Framework - 统一响应 & 异常处理
 * 
 * 借鉴 RuoYi-Vue-Pro 的统一响应结构
 */

var Result = (function() {
    'use strict';
    
    // ===== 错误码定义 =====
    var ErrorCode = {
        // 成功
        SUCCESS: { code: 0, msg: '操作成功' },
        
        // 系统错误 500
        SYSTEM_ERROR: { code: 500, msg: '系统异常' },
        SYSTEM_BUSY: { code: 501, msg: '系统繁忙' },
        
        // 参数错误 400
        PARAM_ERROR: { code: 400, msg: '参数错误' },
        PARAM_MISSING: { code: 401, msg: '缺少必填参数' },
        PARAM_INVALID: { code: 402, msg: '参数格式错误' },
        
        // 认证错误 401
        UNAUTHORIZED: { code: 401, msg: '未登录或登录已过期' },
        TOKEN_INVALID: { code: 402, msg: 'Token 无效' },
        
        // 权限错误 403
        FORBIDDEN: { code: 403, msg: '没有权限' },
        
        // 资源错误 404
        NOT_FOUND: { code: 404, msg: '资源不存在' },
        
        // 业务错误 600+
        USER_NOT_FOUND: { code: 601, msg: '用户不存在' },
        SKILL_NOT_FOUND: { code: 602, msg: '技能不存在' },
        MEMORY_ERROR: { code: 603, msg: '记忆操作失败' },
        TOOL_NOT_FOUND: { code: 604, msg: '工具不存在' },
        TOOL_EXECUTE_ERROR: { code: 605, msg: '工具执行失败' },
        CHANNEL_NOT_FOUND: { code: 606, msg: '渠道不存在' },
        CHANNEL_DISCONNECTED: { code: 607, msg: '渠道未连接' },
        KNOWLEDGE_NOT_FOUND: { code: 608, msg: '知识不存在' }
    };
    
    // ===== 创建成功响应 =====
    function success(data, msg) {
        return {
            code: ErrorCode.SUCCESS.code,
            msg: msg || ErrorCode.SUCCESS.msg,
            data: data !== undefined ? data : null,
            timestamp: Date.now()
        };
    }
    
    // ===== 创建错误响应 =====
    function error(codeOrError, msg) {
        var code, errorMsg;
        
        if (typeof codeOrError === 'number') {
            code = codeOrError;
            errorMsg = msg || '操作失败';
        } else if (typeof codeOrError === 'object') {
            code = codeOrError.code || ErrorCode.SYSTEM_ERROR.code;
            errorMsg = codeOrError.msg || msg || '操作失败';
        } else {
            code = ErrorCode.SYSTEM_ERROR.code;
            errorMsg = codeOrError || msg || '操作失败';
        }
        
        return {
            code: code,
            msg: errorMsg,
            data: null,
            timestamp: Date.now()
        };
    }
    
    // ===== 创建自定义响应 =====
    function create(options) {
        return {
            code: options.code || 0,
            msg: options.msg || 'success',
            data: options.data !== undefined ? options.data : null,
            timestamp: options.timestamp || Date.now()
        };
    }
    
    // ===== 判断是否成功 =====
    function isSuccess(result) {
        return result && result.code === ErrorCode.SUCCESS.code;
    }
    
    // ===== 判断是否错误 =====
    function isError(result) {
        return result && result.code !== ErrorCode.SUCCESS.code;
    }
    
    // ===== 导出 =====
    return {
        // 错误码
        Code: ErrorCode,
        
        // 方法
        success: success,
        error: error,
        create: create,
        isSuccess: isSuccess,
        isError: isError
    };
})();
