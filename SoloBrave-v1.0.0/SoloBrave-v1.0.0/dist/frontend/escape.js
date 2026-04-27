/**
 * XSS 防护工具
 * 用于转义用户输入，防止 XSS 攻击
 */

(function(global) {
  'use strict';

  /**
   * HTML 转义 - 将文本转义为安全的 HTML
   * @param {string} text - 待转义文本
   * @returns {string} 转义后的安全文本
   */
  function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
  }

  /**
   * HTML 属性转义 - 用于元素属性值
   * @param {string} text - 待转义文本
   * @returns {string} 转义后的安全文本
   */
  function escapeAttr(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  /**
   * URL 转义 - 用于 URL 参数
   * @param {string} text - 待转义文本
   * @returns {string} 转义后的安全文本
   */
  function escapeUrl(text) {
    if (text === null || text === undefined) return '';
    try {
      return encodeURIComponent(String(text));
    } catch (e) {
      return '';
    }
  }

  /**
   * JavaScript 转义 - 用于脚本字符串
   * @param {string} text - 待转义文本
   * @returns {string} 转义后的安全文本
   */
  function escapeJs(text) {
    if (text === null || text === undefined) return '';
    return String(text)
      .replace(/\\/g, '\\\\')
      .replace(/"/g, '\\"')
      .replace(/'/g, "\\'")
      .replace(/</g, '\\<')
      .replace(/>/g, '\\>');
  }

  /**
   * 批量转义对象中的文本字段
   * @param {Object} obj - 待处理对象
   * @param {Array<string>} fields - 需要转义的字段名
   * @returns {Object} 转义后的对象副本
   */
  function escapeFields(obj, fields) {
    if (!obj || typeof obj !== 'object') return obj;
    const result = Array.isArray(obj) ? [] : {};
    for (const key in obj) {
      if (Object.prototype.hasOwnProperty.call(obj, key)) {
        if (fields.includes(key) && typeof obj[key] === 'string') {
          result[key] = escapeHtml(obj[key]);
        } else {
          result[key] = obj[key];
        }
      }
    }
    return result;
  }

  // 导出到全局
  global.escapeHtml = escapeHtml;
  global.escapeAttr = escapeAttr;
  global.escapeUrl = escapeUrl;
  global.escapeJs = escapeJs;
  global.escapeFields = escapeFields;

})(window);
