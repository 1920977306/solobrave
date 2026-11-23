/* [FrontErr] 前端错误回采钩子（dq_b8cd14fde7d8，2026-11-23 晚）。
 * window.onerror + unhandledrejection → POST /api/frontend-error → server.log [FrontErr]。
 * 目的：拆「等老大 F12 红字」依赖——商品库/达人库「加载中」故障疑点在浏览器运行时，
 * 老大正常打开页面，异常自动飞回服务器。
 * 纪律：
 *   - 钩子自身全程 try 兜底，绝不影响页面（零侵入）；
 *   - 正常页面零噪音（只在 error/rejection 事件时发请求）；
 *   - 限频 2s：错误风暴只回采头几条，防日志炸弹；
 *   - 幂等：__frontErrHooked 闩锁防重复注入。 */
(function () {
  if (window.__frontErrHooked) return;
  window.__frontErrHooked = 1;
  var lastSent = 0;
  function send(payload) {
    try {
      var now = Date.now();
      if (now - lastSent < 2000) return;
      lastSent = now;
      var body = JSON.stringify({
        page: location.pathname + location.search,
        msg: String(payload.msg || '').slice(0, 300),
        src: String(payload.src || '').slice(0, 200),
        line: payload.line || 0,
        col: payload.col || 0,
        stack: String(payload.stack || '').slice(0, 1500)
      });
      if (navigator.sendBeacon) {
        navigator.sendBeacon('/api/frontend-error',
          new Blob([body], { type: 'application/json' }));
      } else {
        fetch('/api/frontend-error', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: body
        }).catch(function () {});
      }
    } catch (e) { /* 钩子永不再抛 */ }
  }
  window.addEventListener('error', function (ev) {
    try {
      if (ev && ev.target && ev.target !== window && !ev.message) {
        // 资源加载失败（script/img 等）：target 是元素，message 空
        send({ msg: 'resource error: ' + (ev.target.tagName || '?') + ' ' +
                     (ev.target.src || ev.target.href || '') });
        return;
      }
      send({
        msg: ev.message || 'unknown error',
        src: ev.filename || '',
        line: ev.lineno || 0,
        col: ev.colno || 0,
        stack: (ev.error && ev.error.stack) || ''
      });
    } catch (e) {}
  }, true);
  window.addEventListener('unhandledrejection', function (ev) {
    try {
      var r = ev.reason;
      send({
        msg: 'unhandledrejection: ' + String((r && r.message) || r),
        stack: (r && r.stack) || String(r || '')
      });
    } catch (e) {}
  });
})();
