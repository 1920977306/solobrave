/* ===== index.html 内联块 10 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: (function () { */

(function () {
  'use strict';

  // ===== State =====
  var _sb2Chat = {
    agentId: null,            // 当前员工 ID (从 localStorage sb_current_emp 或兜底 Ray)
    messages: [],             // 历史消息列表 (从后端 /api/chat/:agentId 读)
    attached: [],             // 附件 (dataURL 数组, base64)
    sending: false,           // 发送中 flag
    wsConnected: false,       // OpenClaw 网关连接状态 (顶部状态)
    watchedUntil: 0,          // sb2_watchModule 最多轮询 5 秒
  };
  var _sb2ChatInited = false;
  var _sb2OrigSwitch = (typeof switchModule === 'function') ? switchModule : null;
  var _SB2_FALLBACK_EMP = 'emp_1779430403964';  // Ray 兜底 (prod 实际员工)

  // ===== Utility =====
  function _sb2EscapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c];
    });
  }
  function _sb2FmtTime(t) {
    if (!t) return '-';
    var d = new Date(t);
    if (isNaN(d.getTime())) return '-';
    var pad = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate())
      + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function _sb2GetCurrentEmpId() {
    try {
      var id = localStorage.getItem('sb_current_emp');
      if (id) return id;
    } catch (e) { /* localStorage may be unavailable */ }
    return _SB2_FALLBACK_EMP;
  }
  function _sb2GetEmpById(id) {
    if (!id) return null;
    if (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) {
      for (var i = 0; i < window.emps.length; i++) {
        if (window.emps[i] && window.emps[i].id === id) return window.emps[i];
      }
    }
    return null;
  }

  // ===== Topbar =====
  function sb2_updateTopbar() {
    var emp = _sb2GetEmpById(_sb2Chat.agentId);
    var empRole = (emp && emp.role) ? emp.role : '';
    var empName = (emp && emp.name) ? emp.name : '-';
    var empFirst = empName ? empName.charAt(0) : '-';

    // 头像首字 (Ray/Helen/...)
    var avatarEl = document.getElementById('sb2ChatTopbarAvatar');
    if (avatarEl) avatarEl.textContent = empFirst;

    // 姓名·角色 (缺值显 -, 与原 query 行为对齐)
    var nameEl = document.getElementById('sb2ChatTopbarName');
    if (nameEl) nameEl.textContent = empName + (empRole ? ' · ' + empRole : '');

    // 状态行: 绿点 + 网关在线 · 模型名 / 未连接 (原型规格: 模型名内联, 可点击换模型)
    var statusEl = document.getElementById('sb2ChatTopbarStatus');
    var statusTextEl = document.getElementById('sb2ChatTopbarStatusText');
    var modelName = (_sb2ChatTasks && _sb2ChatTasks.model && _sb2ChatTasks.model.current) || '';
    var online = !!(emp && (emp.online === true || emp.status === 'online'));
    var wsConnected = !!_sb2Chat.wsConnected;
    if (statusEl) statusEl.classList.toggle('offline', !(online && wsConnected));
    if (statusTextEl) {
      if (online && wsConnected) {
        statusTextEl.innerHTML = '网关在线 · <span class="sb2-chat-topbar-pick-model" id="sb2ChatPickModel" title="点击切换模型">' + _sb2EscapeHtml(modelName || '-') + '</span>';
      } else if (wsConnected) {
        statusTextEl.textContent = '网关已连 · 员工离线';
      } else {
        statusTextEl.textContent = '未连接';
      }
    }

    // 输入框 placeholder 动态拼接员工名 (原型: 给 {员工名} 派活…)
    var inp = document.getElementById('sb2ChatInput');
    if (inp) {
      inp.placeholder = '给 ' + (empName || '员工') + ' 派活… (Enter 发送, Shift+Enter 换行)';
    }
  }
  // 顶栏 ghost 按钮占位 (原型对应"员工档案/设置"待展开, 跟前端规范一致走 toast)
  function sb2_toastComingSoon(label) {
    if (typeof showToast === 'function') {
      showToast(label + ' — 原型未展开');
    } else {
      console.log('[sb2]', label, 'coming soon');
    }
  }

  // ===== Messages =====
  function sb2_renderMessages() {
    var container = document.getElementById('sb2ChatMessages');
    if (!container) return;
    if (!_sb2Chat.messages || _sb2Chat.messages.length === 0) {
      container.innerHTML = ''
        + '<div class="sb2-chat-empty">'
        + '  <div class="sb2-chat-empty-icon">💬</div>'
        + '  <div>暂无消息,说点什么吧</div>'
        + '  <div class="sb2-chat-empty-tip">Enter 发送 / Shift+Enter 换行 / 📎 附件</div>'
        + '</div>';
      return;
    }
    var html = '';
    for (var i = 0; i < _sb2Chat.messages.length; i++) {
      html += sb2_renderBubble(_sb2Chat.messages[i]);
    }
    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;
    // ★ P1b: 异步步 — 扫占位 div 拉 GET /api/proposals/:id 填卡
    if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(container);
  }

  function sb2_renderBubble(msg) {
    var role = (msg && msg.role) ? msg.role : 'assistant';
    var content = (msg && msg.content !== undefined && msg.content !== null) ? msg.content : '-';
    var time = _sb2FmtTime(msg && (msg.timestamp || msg.created_at));
    var images = (msg && Array.isArray(msg.images)) ? msg.images : [];
    var isErr = msg && msg.error === true;
    var isUser = role === 'user';
    var isSystem = role === 'system';

    var safeContent = _sb2EscapeHtml(content);
    // §2 mention token 高亮: @Name → <span class="sb2-chat-mention">@Name</span>
    var bodyHtml = safeContent.replace(/@(\S+?)([,，。\s]|$)/g, '<span class="sb2-chat-mention">@$1</span>$2');

    // ★ P1b: 提议卡片 token 检测 (同步步)
    //   检测 [[proposal:prp_xxx]] → 剥掉原文 → bubble body 末尾拼占位 div
    //   渲染后由 sb2_renderMessages / sb2_appendBubble 末尾调 window.sb2PropMountCards 异步填卡
    //   apply when: 任何「消息 token → 卡片子组件」渲染管线 (spec §1)
    var propCardHtml = '';
    var _propTokenMatch = bodyHtml.match(/\[\[proposal:(prp_[a-f0-9]{8,32})\]\]/);
    if (_propTokenMatch) {
      bodyHtml = bodyHtml.replace(/\[\[proposal:prp_[a-f0-9]{8,32}\]\]/g, '').trim();
      propCardHtml = (typeof window.sb2PropRenderPlaceholder === 'function')
        ? window.sb2PropRenderPlaceholder(_propTokenMatch[1]) : '';
    }

    /* 〔fix/sb2-side-restore 21 轮批注③-2〕附件渲染 bug 修复
       修前 bug: var src = _sb2EscapeHtml(images[j]) → 对象 .toString() = '[object Object]'
         → 历史消息附件 <img src="[object Object]"> ×4 (无图片显示)
       修法: 兼容字符串 + 对象两种形态
         - 字符串: 原 base64 data URL, 直接用
         - 对象 {base64|url|dataUrl|src}: 取字段 (前端上传返回 {base64, filename}, 见 compressImageToBase64 line 17284)
       范围: 仅 .sb2-chat-bubble-images 渲染逻辑, 不动上传链路 (pendingImages 结构不变) */
    var imgHtml = '';
    if (images.length > 0) {
      imgHtml = '<div class="sb2-chat-bubble-images">';
      for (var j = 0; j < images.length; j++) {
        var _img = images[j];
        var _src = '';
        if (typeof _img === 'string') {
          _src = _img;
        } else if (_img && typeof _img === 'object') {
          _src = _img.base64 || _img.url || _img.dataUrl || _img.src || '';
        }
        if (_src) {
          imgHtml += '<img class="sb2-chat-bubble-image" src="' + _sb2EscapeHtml(_src) + '" alt="attachment-' + j + '" />';
        }
      }
      imgHtml += '</div>';
    }

    // 注入标签 chips (数据驱动, 禁止写死数字)
    // 〔r35 后端透传立项落地〕三色 pill 真数据源: msg.injectionTags (后端 commit 透传 + 落库,
    //   历史消息刷新仍可见). 记忆 = core+daily+archive 合计 / 知识 = kb_entries 召回 / 规律 = patterns 命中
    //   老消息无 injectionTags → 不渲染 (只丢不造); 0 条不渲染 (避免满屏零 pill)
    var chipsHtml = '';
    if (!isUser && msg && msg.injectionTags && typeof msg.injectionTags === 'object') {
      var _it = msg.injectionTags;
      var _mem = (_it.memory && typeof _it.memory === 'object') ? _it.memory : {};
      var _memTotal = (_mem.core || 0) + (_mem.daily || 0) + (_mem.archive || 0);
      var _itHtml = '';
      if (_memTotal > 0) _itHtml += '<span class="sb2-chat-injection-chip accent">🧠 记忆 ' + _memTotal + ' 条</span>';
      if ((_it.knowledge || 0) > 0) _itHtml += '<span class="sb2-chat-injection-chip brand">📄 知识 ' + _it.knowledge + ' 条</span>';
      if ((_it.pattern || 0) > 0) _itHtml += '<span class="sb2-chat-injection-chip gold">🧩 规律 ' + _it.pattern + ' 条</span>';
      if (_itHtml) chipsHtml = '<div class="sb2-chat-injection-chips">' + _itHtml + '</div>';
    }
    // 老链路兼容: citations (RAG 召回数组) — 仅在无 injectionTags 时兜底, 不双渲染
    if (!chipsHtml && !isUser && msg && Array.isArray(msg.citations) && msg.citations.length > 0) {
      chipsHtml = '<div class="sb2-chat-injection-chips">'
        + '<span class="sb2-chat-injection-chip brand">📄 召回 ' + msg.citations.length + ' 条知识</span>'
        + '</div>';
    }

    // 头像首字: 助手取当前 agent 员工姓名, 用户取"我" (前端规范: 缺值显 -)
    var avatarChar = '-';
    var avatarRoleCls = 'assistant';
    if (isUser) {
      avatarChar = '我';
      avatarRoleCls = 'user';
    } else if (isSystem) {
      avatarChar = '·';
      avatarRoleCls = 'system';
    } else {
      var emp = _sb2GetEmpById(_sb2Chat.agentId);
      var empName = (emp && emp.name) ? emp.name : '';
      avatarChar = empName ? empName.charAt(0) : '-';
    }

    return ''
      + '<div class="sb2-chat-msg-row" data-role="' + _sb2EscapeHtml(role) + '">'
      + '  <span class="sb2-chat-msg-avatar ' + avatarRoleCls + '">' + _sb2EscapeHtml(avatarChar) + '</span>'
      + '  <div class="sb2-chat-msg-col">'
      +    chipsHtml
      + '    <div class="sb2-chat-bubble' + (isErr ? ' sb2-chat-bubble-error' : '') + '" data-role="' + _sb2EscapeHtml(role) + '">'
      + '      <div class="sb2-chat-bubble-body">' + bodyHtml + imgHtml + propCardHtml + '</div>'
      + '    </div>'
      + '    <span class="sb2-chat-bubble-time">' + _sb2EscapeHtml(time) + '</span>'
      + '  </div>'
      + '</div>';
  }

  function sb2_appendBubble(msg, scrollToEnd) {
    var container = document.getElementById('sb2ChatMessages');
    if (!container) return;
    var empty = container.querySelector('.sb2-chat-empty');
    if (empty) empty.parentNode.removeChild(empty);
    var wrap = document.createElement('div');
    wrap.innerHTML = sb2_renderBubble(msg);
    var bubble = wrap.firstChild;
    if (bubble) container.appendChild(bubble);
    if (scrollToEnd !== false) container.scrollTop = container.scrollHeight;
    // ★ P1b: 异步步 — 扫占位 div 拉 GET /api/proposals/:id 填卡
    if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(container);
  }

  // ===== History =====
  async function sb2_loadHistory() {
    if (!_sb2Chat.agentId) {
      _sb2Chat.messages = [];
      sb2_renderMessages();
      return;
    }
    try {
      var resp = await apiFetch('/api/chat/' + encodeURIComponent(_sb2Chat.agentId) + '?type=personal&limit=100');
      if (!resp) { _sb2Chat.messages = []; sb2_renderMessages(); return; }
      var data = await resp.json();
      if (Array.isArray(data)) _sb2Chat.messages = data;
      else if (data && Array.isArray(data.messages)) _sb2Chat.messages = data.messages;
      else _sb2Chat.messages = [];
    } catch (e) {
      console.error('[sb2_loadHistory]', e);
      _sb2Chat.messages = [];
    }
    sb2_renderMessages();
    // 二次刷新 topbar (loadEmployees 可能异步完成)
    sb2_updateTopbar();
  }

  // ===== Send =====
  function sb2_renderAttachBar() {
    var bar = document.getElementById('sb2ChatAttachBar');
    if (!bar) return;
    bar.innerHTML = '';
    for (var i = 0; i < _sb2Chat.attached.length; i++) {
      var thumb = document.createElement('div');
      thumb.className = 'sb2-chat-attach-thumb';
      thumb.innerHTML = '<img src="' + _sb2EscapeHtml(_sb2Chat.attached[i]) + '" alt="thumb-' + i + '" />'
        + '<button type="button" class="sb2-chat-attach-thumb-x" data-idx="' + i + '" aria-label="移除附件">×</button>';
      bar.appendChild(thumb);
    }
  }
  function sb2_removeAttach(idx) {
    if (idx < 0 || idx >= _sb2Chat.attached.length) return;
    _sb2Chat.attached.splice(idx, 1);
    sb2_renderAttachBar();
  }
  function sb2_bindInput() {
    var inp = document.getElementById('sb2ChatInput');
    var sendBtn = document.getElementById('sb2ChatSendBtn');
    if (!inp || !sendBtn) return;
    inp.addEventListener('input', function () {
      sendBtn.disabled = inp.value.trim().length === 0;
      // Auto-resize
      inp.style.height = 'auto';
      var maxH = 160;
      var next = Math.min(inp.scrollHeight, maxH);
      inp.style.height = next + 'px';
    });
    inp.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (!_sb2Chat.sending) sb2_doSend();
      }
      // Shift+Enter: 默认行为(换行)
    });
    sendBtn.addEventListener('click', function () {
      if (!_sb2Chat.sending) sb2_doSend();
    });
    // 粘贴图片
    inp.addEventListener('paste', function (e) {
      var items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      var added = 0;
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file' && items[i].type.indexOf('image/') === 0) {
          var file = items[i].getAsFile();
          if (!file) continue;
          (function (f) {
            var reader = new FileReader();
            reader.onload = function (ev) {
              _sb2Chat.attached.push(ev.target.result);
              sb2_renderAttachBar();
            };
            reader.readAsDataURL(f);
          })(file);
          added++;
        }
      }
      if (added > 0) e.preventDefault();
    });
    // 附件按钮
    var attachBtn = document.getElementById('sb2ChatAttachBtn');
    var fileInput = document.getElementById('sb2ChatFileInput');
    if (attachBtn && fileInput) {
      attachBtn.addEventListener('click', function () { fileInput.click(); });
      fileInput.addEventListener('change', function () {
        var files = fileInput.files;
        if (!files || files.length === 0) return;
        for (var i = 0; i < files.length; i++) {
          (function (f) {
            var reader = new FileReader();
            reader.onload = function (ev) {
              _sb2Chat.attached.push(ev.target.result);
              sb2_renderAttachBar();
            };
            reader.readAsDataURL(f);
          })(files[i]);
        }
        fileInput.value = '';
      });
    }
    // 附件移除 (事件委托)
    var bar = document.getElementById('sb2ChatAttachBar');
    if (bar) {
      bar.addEventListener('click', function (e) {
        var btn = e.target.closest('.sb2-chat-attach-thumb-x');
        if (!btn) return;
        var idx = parseInt(btn.getAttribute('data-idx'), 10);
        sb2_removeAttach(idx);
      });
    }
  }
  async function sb2_doSend() {
    if (_sb2Chat.sending) return;
    var inp = document.getElementById('sb2ChatInput');
    var sendBtn = document.getElementById('sb2ChatSendBtn');
    if (!inp) return;
    var text = inp.value.trim();
    if (!text && _sb2Chat.attached.length === 0) return;
    if (!_sb2Chat.agentId) {
      console.warn('[sb2_doSend] no agentId, skip');
      return;
    }
    _sb2Chat.sending = true;
    sendBtn.disabled = true;

    // 1. 先 append user 气泡
    var userMsg = {
      role: 'user',
      content: text || '(附件)',
      images: _sb2Chat.attached.slice(),
      timestamp: new Date().toISOString()
    };
    _sb2Chat.messages.push(userMsg);
    sb2_appendBubble(userMsg, true);
    // ★ P0 fix: 清空 inp.value / _sb2Chat.attached 之前先抓 mentions + imgs
    // 之前: POST body 在清空后才调 sb2_getMentions() → 读已清空输入框 → mentions 恒 []
    //       同样 _sb2Chat.attached.slice() 也在清空后 → images 恒 []
    // 现在: 先存局部变量, body 里直接用
    var __mentions = (typeof window.sb2_getMentions === 'function') ? window.sb2_getMentions() : undefined;
    var __imgs = _sb2Chat.attached.slice();
    inp.value = '';
    inp.style.height = 'auto';
    _sb2Chat.attached = [];
    sb2_renderAttachBar();

    // 2. 占位 assistant 气泡 + 进度条
    var progress = document.getElementById('sb2ChatProgress');
    if (progress) progress.hidden = false;
    var placeholderMsg = {
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString()
    };
    sb2_appendBubble(placeholderMsg, true);
    var placeholderEl = document.querySelector('#sb2ChatMessages .sb2-chat-bubble[data-role="assistant"]:last-child .sb2-chat-bubble-body');

    // 3. POST
    try {
      var resp = await apiFetch('/api/chat/' + encodeURIComponent(_sb2Chat.agentId) + '?type=personal', {
        method: 'POST',
        body: JSON.stringify({
          content: text || '',
          role: 'user',
          images: __imgs,
          // §2 透传: model + mentions (后端 chatpost 已支持, 缺值 undefined)
          model: (typeof window.sb2_getCurrentModel === 'function') ? window.sb2_getCurrentModel() : undefined,
          mentions: __mentions
        })
      });
      if (!resp) {
        if (placeholderEl) placeholderEl.textContent = '-';
        return;
      }
      var reply = await resp.json();
      // server 返 {userMessage, aiMessage:{id, role, content, ...}, archived, ...}
      // 缺值: content/message/aiMessage.content 全空 → '-'
      var replyText = '-';
      if (reply) {
        if (reply.aiMessage && (reply.aiMessage.content !== undefined && reply.aiMessage.content !== null)) {
          replyText = reply.aiMessage.content;
        } else if (reply.content !== undefined && reply.content !== null) {
          replyText = reply.content;
        } else if (reply.message !== undefined && reply.message !== null) {
          replyText = reply.message;
        }
      }
      if (placeholderEl) {
        placeholderEl.textContent = replyText;
        placeholderEl.classList.remove('sb2-chat-bubble-cursor');
      }
      // 〔r35〕透传 injectionTags 进内存消息: 本轮 pill 立即渲染; 历史刷新走服务端落库字段
      var _pushMsg = {
        role: 'assistant',
        content: replyText,
        timestamp: new Date().toISOString()
      };
      if (reply && reply.aiMessage && reply.aiMessage.injectionTags) {
        _pushMsg.injectionTags = reply.aiMessage.injectionTags;
      } else if (reply && reply.injectionTags) {
        _pushMsg.injectionTags = reply.injectionTags;
      }
      _sb2Chat.messages.push(_pushMsg);
      /* 〔r35.1b〕占位气泡渲染时 injectionTags 还没回来 → pill 渲染不出。
         用带 tags 的完整 bubble 替换占位 row (含 chips), 保持滚动 + 提议卡挂载 */
      var _mc = document.getElementById('sb2ChatMessages');
      var _rows = _mc ? _mc.querySelectorAll('.sb2-chat-msg-row') : [];
      var _lastRow = _rows[_rows.length - 1];
      if (_lastRow) {
        var _tmpWrap = document.createElement('div');
        _tmpWrap.innerHTML = sb2_renderBubble(_pushMsg);
        if (_tmpWrap.firstChild) _lastRow.parentNode.replaceChild(_tmpWrap.firstChild, _lastRow);
        if (_mc) _mc.scrollTop = _mc.scrollHeight;
        if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(_mc);
      }
    } catch (e) {
      console.error('[sb2_doSend]', e);
      if (placeholderEl) placeholderEl.textContent = '-';
    } finally {
      _sb2Chat.sending = false;
      if (progress) progress.hidden = true;
      sendBtn.disabled = inp.value.trim().length === 0;
    }
  }

  // ===== Dispatcher =====
  function renderChatMain() {
    var shell = document.getElementById('sb2ChatMain');
    if (!shell) return;
    // 1. 显隐切换
    if (typeof currentModule !== 'undefined' && currentModule === 'messages') {
      shell.hidden = false;
    } else {
      shell.hidden = true;
      return;
    }
    // 2. 同步当前员工
    _sb2Chat.agentId = _sb2GetCurrentEmpId();
    sb2_updateTopbar();
    // 3. 加载历史
    sb2_loadHistory();
    // 4. 绑定输入 (一次性, 二调不会重复)
    if (!_sb2ChatInited) {
      _sb2ChatInited = true;
      sb2_bindInput();
    }
  }

  // ===== Watchdog: 5 秒内兜底 module 激活 =====
  function sb2_watchModule() {
    var startTime = Date.now();
    var timer = setInterval(function () {
      if (Date.now() - startTime > 5000) {
        clearInterval(timer);
        return;
      }
      if (typeof currentModule !== 'undefined' && currentModule === 'messages') {
        clearInterval(timer);
        renderChatMain();
      }
    }, 200);
  }

  // ===== Init =====
  function tryInit() {
    // wrap 老的 switchModule, 任意切换都触发 renderChatMain
    // (P0 fix: 离开 messages 时也要触发, 让 renderChatMain 内部根据 currentModule 隐藏 sb2ChatMain)
    if (_sb2OrigSwitch && typeof window.switchModule === 'function') {
      // 已在外部加载完成, 重新包装
    }
    // 重新定义全局 switchModule (一次性包装, 老函数先调, 再触发 sb2 显隐判断)
    window.switchModule = function (mod) {
      // ★ P1 fix 第一步: hide 所有 sb2 main 兄弟容器 (双保险 — wrapper2 是真正被 nav 调用的入口,
      //  跳过 wrapper1, 所以 hideAll 必须也在 wrapper2 里调一次)
      sb2_hideAllModuleMains();
      if (_sb2OrigSwitch) {
        try { _sb2OrigSwitch(mod); } catch (e) { console.error('[sb2 wrap switchModule]', e); }
      }
      // 无条件触发 renderChatMain, 内部判断 currentModule==='messages' 决定显隐
      // (P0 fix: 离开 messages 时也要触发, 否则 sb2ChatMain.hidden=false 没人改回来,
      //  导致切到达人/知识/规律/任务时聊天界面仍显示)
      setTimeout(renderChatMain, 0);
    };
    // 启动兜底监听 (防 wrap 之前用户已点过 nav 触发 currentModule=messages)
    sb2_watchModule();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', tryInit);
  } else {
    tryInit();
  }

  // 暴露关键函数到 window (供 F12 调试 + 老 openMessages 兜底调用)
  window.renderChatMain = renderChatMain;
  window.sb2_loadHistory = sb2_loadHistory;
  window.sb2_doSend = sb2_doSend;
  // ★ P1b B2 fix: bubble 渲染函数也挂 window (跨脚本块调用, SEV1 红线)
  //   之前: sb2PropAppendSystemBubble (在另一 script 块 line 46925) typeof sb2_appendBubble 永远 'undefined', 系统气泡静默不渲染
  //   现在: 三函数全挂 window, 跨块调用稳妥
  window.sb2_renderBubble = sb2_renderBubble;
  window.sb2_renderMessages = sb2_renderMessages;
  window.sb2_appendBubble = sb2_appendBubble;
  // 同步 _sb2Chat 引用挂 window (同根因: P1b prop 块需 push 消息)
  window._sb2Chat = _sb2Chat;
})();
