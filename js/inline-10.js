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
    // 群聊模式: 顶栏由 sb2RenderGroupShell 管, 私聊 topbar 逻辑全跳过
    if (window._sb2GroupMode) return;
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
    // 〔chat-opt-2 2026-10-07〕修活死人信号: 原 _sb2Chat.wsConnected 全仓无赋值点 (恒 false,
    //   顶栏永远「未连接」), 改读全局 openclaw 真实 WS 态; 连接中单独橙点态
    var statusEl = document.getElementById('sb2ChatTopbarStatus');
    var statusTextEl = document.getElementById('sb2ChatTopbarStatusText');
    var modelName = (_sb2ChatTasks && _sb2ChatTasks.model && _sb2ChatTasks.model.current) || '';
    var online = !!(emp && (emp.online === true || emp.status === 'online'));
    /* 〔chat-opt-2〕openclaw 是 openclaw-client.js 顶层 const (全局词法绑定, 不在 window 上),
       必须 typeof 裸名守卫, 不能 window.openclaw (恒 undefined) */
    var _oc = (typeof openclaw !== 'undefined') ? openclaw : null;
    var wsConnected = !!(_oc && _oc.connected && _oc.authenticated);
    var wsConnecting = !!(_oc && _oc.connected && !_oc.authenticated);
    if (statusEl) {
      statusEl.classList.toggle('offline', !(online && wsConnected) && !wsConnecting);
      statusEl.classList.toggle('connecting', wsConnecting);
    }
    if (statusTextEl) {
      if (online && wsConnected) {
        statusTextEl.innerHTML = '网关在线 · <span class="sb2-chat-topbar-pick-model" id="sb2ChatPickModel" title="点击切换模型">' + _sb2EscapeHtml(modelName || '-') + '</span>';
      } else if (wsConnected) {
        statusTextEl.textContent = '网关已连 · 员工离线';
      } else if (wsConnecting) {
        statusTextEl.textContent = '连接中…';
      } else {
        statusTextEl.textContent = '未连接 · 点击重连';
      }
    }

    // 输入框 placeholder 动态拼接员工名 (〔chat-opt-2〕精简: 快捷键提示已常驻空态提示 + title)
    var inp = document.getElementById('sb2ChatInput');
    if (inp) {
      inp.placeholder = '派活给 ' + (empName || '员工') + '…';
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

  // ★ r68 批注①: 「员工档案」接 legacy openEmpDetail (inline-03 既有员工详情抽屉, 零新逻辑)
  function sb2_openEmpProfile() {
    var id = _sb2Chat.agentId || _sb2GetCurrentEmpId();
    if (id && typeof openEmpDetail === 'function') {
      openEmpDetail(id);
      return;
    }
    sb2_toastComingSoon('员工档案');
  }

  // ===== Messages =====
  function sb2_renderMessages() {
    var container = document.getElementById('sb2ChatMessages');
    if (!container) return;
    if (!_sb2Chat.messages || _sb2Chat.messages.length === 0) {
      /* 〔chat-opt-4 2026-10-08 老大批注〕空对话给快捷派活建议 (能力卡片同款思路, 点击预填输入框):
         已选员工 → 问候 + 3 个快捷 prompt chips (按角色换一组); 未选员工 → 原提示 */
      if (_sb2Chat.agentId) {
        var _empQ = _sb2GetEmpById(_sb2Chat.agentId);
        var _empQName = (_empQ && _empQ.name) ? _empQ.name : '员工';
        var _roleQ = (_empQ && _empQ.role) ? String(_empQ.role) : '';
        var _promptsQ = ['帮我汇总一下当前任务进度', '最近有什么值得关注的新动态？', '给我列一份本周重点工作清单'];
        if (/达人|商务|选品|运营/.test(_roleQ)) {
          _promptsQ = ['分析一下最近达人的合作表现', '基于已合作达人推荐下一波选品', '给团队写一份本周工作简报'];
        }
        var _chipsQ = '';
        for (var _qi = 0; _qi < _promptsQ.length; _qi++) {
          _chipsQ += '<button type="button" class="sb2-chat-empty-chip" onclick="sb2QuickPrompt(' + JSON.stringify(_promptsQ[_qi]).replace(/"/g, '&quot;') + ')">' + _sb2EscapeHtml(_promptsQ[_qi]) + '</button>';
        }
        container.innerHTML = ''
          + '<div class="sb2-chat-empty">'
          + '  <div class="sb2-chat-empty-icon">💬</div>'
          + '  <div>和 ' + _sb2EscapeHtml(_empQName) + ' 说点什么吧</div>'
          + '  <div class="sb2-chat-empty-prompts">' + _chipsQ + '</div>'
          + '  <div class="sb2-chat-empty-tip">Enter 发送 / Shift+Enter 换行 / 📎 附件</div>'
          + '</div>';
      } else {
        container.innerHTML = ''
          + '<div class="sb2-chat-empty">'
          + '  <div class="sb2-chat-empty-icon">💬</div>'
          + '  <div>暂无消息,说点什么吧</div>'
          + '  <div class="sb2-chat-empty-tip">Enter 发送 / Shift+Enter 换行 / 📎 附件</div>'
          + '</div>';
      }
      return;
    }
    var html = '';
    for (var i = 0; i < _sb2Chat.messages.length; i++) {
      html += sb2_renderBubble(_sb2Chat.messages[i], _sb2GroupOpts(_sb2Chat.messages[i], i > 0 ? _sb2Chat.messages[i - 1] : null));
    }
    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;
    // ★ P1b: 异步步 — 扫占位 div 拉 GET /api/proposals/:id 填卡
    if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(container);
  }

  /* 〔chat-opt 2026-10-07 老大拍板〕消息分组渲染:
     同 role 且间隔 ≤5min 的连续消息 → 头像转 spacer 占位 (气泡列对齐不跳) + 时间戳省略;
     间隔 >5min / 换发送者 → 头像 + 时间戳回归; 跨天 → 额外插居中日期分割线
     依据: 气泡统一/头像合并/时间分组三件套, 会话 23:24 老大批注咨询 + 「执行」拍板 */
  var _SB2_GROUP_GAP = 5 * 60 * 1000;
  function _sb2MsgT(msg) { return msg && (msg.timestamp || msg.created_at); }
  function _sb2DayKey(t) { var d = new Date(t); return isNaN(d.getTime()) ? '' : d.getFullYear() + '-' + d.getMonth() + '-' + d.getDate(); }
  function _sb2DividerLabel(t) {
    var d = new Date(t);
    if (isNaN(d.getTime())) return '';
    var wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 星期' + wk;
  }
  function _sb2GroupOpts(msg, prev) {
    var t = _sb2MsgT(msg);
    if (!prev) return { showAvatar: true, showTime: true, divider: t ? _sb2DividerLabel(t) : null };
    var pt = _sb2MsgT(prev);
    var ptms = pt ? new Date(pt).getTime() : NaN;
    var tms = t ? new Date(t).getTime() : NaN;
    var gapBig = isNaN(ptms) || isNaN(tms) ? true : (tms - ptms > _SB2_GROUP_GAP);
    var dayChanged = _sb2DayKey(pt) !== _sb2DayKey(t);
    return {
      showAvatar: gapBig || prev.role !== msg.role,
      showTime: gapBig || dayChanged,
      divider: dayChanged && t ? _sb2DividerLabel(t) : null
    };
  }

  function sb2_renderBubble(msg, opts) {
    opts = opts || { showAvatar: true, showTime: true, divider: null };
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

    /* 〔chat-opt 2026-10-07〕头像合并: 组内非首条 → spacer 占位 (visibility:hidden 保布局对齐) */
    var avatarHtml = opts.showAvatar
      ? '<span class="sb2-chat-msg-avatar ' + avatarRoleCls + '">' + _sb2EscapeHtml(avatarChar) + '</span>'
      : '<span class="sb2-chat-msg-avatar ' + avatarRoleCls + ' spacer"></span>';
    var timeHtml = opts.showTime
      ? '<span class="sb2-chat-bubble-time">' + _sb2EscapeHtml(time) + '</span>'
      : '';
    /* 〔chat-opt-5 2026-10-08〕AI 气泡上方姓名+时间标签 (还原原型设计稿, 老大批注)
       每条 AI 消息上方显示员工名 (恒显), 时间随分组规则 (opts.showTime 才带) */
    var metaHtml = '';
    if (!isUser && !isSystem) {
      var _metaEmp = _sb2GetEmpById(_sb2Chat.agentId);
      var _metaName = (_metaEmp && _metaEmp.name) ? _metaEmp.name : '';
      if (_metaName) {
        metaHtml = '<div class="sb2-chat-msg-meta"><span class="sb2-chat-msg-meta-name">' + _sb2EscapeHtml(_metaName) + '</span>'
          + (opts.showTime ? '<span class="sb2-chat-msg-meta-time">' + _sb2EscapeHtml(time) + '</span>' : '')
          + '</div>';
      }
    }
    var dividerHtml = opts.divider
      ? '<div class="sb2-chat-date-divider"><span>' + _sb2EscapeHtml(opts.divider) + '</span></div>'
      : '';

    /* 〔chat-opt-2 2026-10-07〕失败消息重试入口 (历史 error 气泡 + 实时失败占位统一走 sb2ChatRetry) */
    var retryHtml = isErr
      ? '<button type="button" class="sb2-chat-retry-btn" onclick="sb2ChatRetry()">↻ 重试</button>'
      : '';

    return dividerHtml
      + '<div class="sb2-chat-msg-row" data-role="' + _sb2EscapeHtml(role) + '">'
      +    avatarHtml
      + '  <div class="sb2-chat-msg-col">'
      +    metaHtml
      +    chipsHtml
      + '    <div class="sb2-chat-bubble' + (isErr ? ' sb2-chat-bubble-error' : '') + '" data-role="' + _sb2EscapeHtml(role) + '">'
      + '      <div class="sb2-chat-bubble-body">' + bodyHtml + imgHtml + propCardHtml + '</div>'
      + '    </div>'
      +    timeHtml
      +    retryHtml
      + '  </div>'
      + '</div>';
  }

  function sb2_appendBubble(msg, scrollToEnd) {
    var container = document.getElementById('sb2ChatMessages');
    if (!container) return;
    var empty = container.querySelector('.sb2-chat-empty');
    if (empty) empty.parentNode.removeChild(empty);
    /* 〔chat-opt 2026-10-07〕增量消息同分组规则: 与 messages 末条比较
       (msg 可能未 push — 流式占位, 此时仍取末条做 prev) */
    var msgs = _sb2Chat.messages;
    var prev = null;
    if (msgs.length && msgs[msgs.length - 1] === msg) prev = msgs.length > 1 ? msgs[msgs.length - 2] : null;
    else if (msgs.length) prev = msgs[msgs.length - 1];
    var wrap = document.createElement('div');
    wrap.innerHTML = sb2_renderBubble(msg, _sb2GroupOpts(msg, prev));
    while (wrap.firstChild) container.appendChild(wrap.firstChild);
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
    // ★ 群聊模式 (2026-10-08 重新设计): 走 legacy sendGroupMessage 群聊管线 (OpenClaw 流式全保留)
    //   legacy 自己把用户消息 + AI 回复渲染进 #messagesArea → MutationObserver 镜像成 sb2 气泡
    //   所以这里不本地 append, 避免双份
    if (window._sb2GroupMode) {
      var __gMentions = (typeof window.sb2_getMentions === 'function') ? (window.sb2_getMentions() || []) : [];
      var __gImgs = _sb2Chat.attached.slice();
      inp.value = '';
      inp.style.height = 'auto';
      _sb2Chat.attached = [];
      sb2_renderAttachBar();
      sendBtn.disabled = true;
      setTimeout(function () { sendBtn.disabled = inp.value.trim().length === 0; }, 300);
      try {
        if (typeof sendGroupMessage === 'function') {
          sendGroupMessage(window._sb2GroupMode, text || '', __gMentions, __gImgs);
        } else if (typeof showToast === 'function') {
          showToast('群聊发送不可用');
        }
      } catch (e) {
        console.error('[sb2 group send]', e);
        if (typeof showToast === 'function') showToast('群聊发送失败');
      }
      return;
    }
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
        sb2_failBubble(placeholderEl);
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
      sb2_failBubble(placeholderEl);
    } finally {
      _sb2Chat.sending = false;
      if (progress) progress.hidden = true;
      sendBtn.disabled = inp.value.trim().length === 0;
    }
  }

  /* 〔chat-opt-4 2026-10-08〕空对话快捷 prompt: 点击 chip → 预填输入框 + 聚焦 (不直接发送, 可改) */
  function sb2QuickPrompt(text) {
    var inp = document.getElementById('sb2ChatInput');
    if (!inp) return;
    inp.value = text || '';
    inp.dispatchEvent(new Event('input'));
    inp.focus();
  }
  window.sb2QuickPrompt = sb2QuickPrompt;

  /* 〔chat-opt-2 2026-10-07〕发送失败占位 → 错误气泡 + 重试按钮 */
  function sb2_failBubble(placeholderEl) {
    if (!placeholderEl) return;
    var bubble = placeholderEl.closest('.sb2-chat-bubble');
    placeholderEl.textContent = '⚠️ 这条消息发送失败';
    if (!bubble) return;
    bubble.classList.add('sb2-chat-bubble-error');
    if (bubble.parentNode && !bubble.parentNode.querySelector('.sb2-chat-retry-btn')) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'sb2-chat-retry-btn';
      btn.textContent = '↻ 重试';
      btn.addEventListener('click', function () { sb2ChatRetry(); });
      bubble.parentNode.insertBefore(btn, bubble.nextSibling);
    }
  }

  /* 〔chat-opt-2 2026-10-07〕重试: 取最近一条用户消息 (含附件) 重新走 sb2_doSend */
  function sb2ChatRetry() {
    var lastUser = null;
    for (var i = _sb2Chat.messages.length - 1; i >= 0; i--) {
      if (_sb2Chat.messages[i] && _sb2Chat.messages[i].role === 'user') { lastUser = _sb2Chat.messages[i]; break; }
    }
    var inp = document.getElementById('sb2ChatInput');
    if (!lastUser || !inp) {
      if (typeof showToast === 'function') showToast('没有可重试的消息');
      return;
    }
    inp.value = (lastUser.content && lastUser.content !== '(附件)') ? lastUser.content : '';
    _sb2Chat.attached = (lastUser.images && lastUser.images.length) ? lastUser.images.slice() : [];
    if (typeof sb2_renderAttachBar === 'function') sb2_renderAttachBar();
    inp.dispatchEvent(new Event('input'));
    sb2_doSend();
  }

  /* 〔chat-opt-2 2026-10-07〕顶栏状态行点击重连 (走 openclaw 全局客户端) */
  function sb2ChatReconnect() {
    /* 〔chat-opt-2〕openclaw 是顶层 const 全局词法绑定, typeof 裸名取 */
    var _oc = (typeof openclaw !== 'undefined') ? openclaw : null;
    var _st = document.getElementById('sb2ChatTopbarStatusText');
    var _offline = _st && _st.textContent.indexOf('未连接') >= 0;
    if (!_offline) return;  // 健康态点击无副作用
    if (_oc && typeof _oc.connect === 'function') {
      try { _oc.connect(); } catch (e) { console.warn('[sb2ChatReconnect]', e); }
      if (typeof showToast === 'function') showToast('正在连接网关…');
    } else if (typeof showToast === 'function') {
      showToast('网关客户端未加载,请刷新页面');
    }
  }
  window.sb2ChatRetry = sb2ChatRetry;
  window.sb2ChatReconnect = sb2ChatReconnect;

  // ===== Dispatcher =====
  function renderChatMain() {
    var shell = document.getElementById('sb2ChatMain');
    if (!shell) return;
    // 1. 显隐切换
    if (typeof currentModule !== 'undefined' && currentModule === 'messages') {
      shell.hidden = false;
      // ★ 群聊模式 (2026-10-08 重新设计): sb2 原生群聊面板
      //   顶栏切群信息块, 消息经 MutationObserver 从 legacy #messagesArea 镜像成 sb2 气泡
      //   (legacy 群聊管线 OpenClaw 流式全保留, 只换视觉; #chatArea 保持 hidden 不显示旧设计)
      if (window._sb2GroupMode) {
        shell.classList.add('sb2-group-mode');
        try { sb2RenderGroupShell(); } catch (e) { console.error('[sb2 group shell]', e); }
        try { sb2GroupMirrorAll(); } catch (e) { console.error('[sb2 group mirror]', e); }
        return;
      }
      shell.classList.remove('sb2-group-mode');
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

  /* 〔chat-opt-2 2026-10-07〕顶栏状态跟随真实 WS 态:
     原状态只在进聊天时刷一次 → 断线/重连后文字假死; updateConnectionStatus 的周期在 inline-03,
     不跨块; 这里 5s 轻量重刷 sb2_updateTopbar (幂等纯 DOM, 无网络请求), 仅在 messages 模块 */
  setInterval(function () {
    try {
      if (typeof currentModule !== 'undefined' && currentModule === 'messages') sb2_updateTopbar();
      // ★ 群聊模式兜底退出 (2026-10-08): 用户从 legacy 侧栏员工列表直接点私聊 (不走 sb2 renderer)
      //   → sb_current_emp 变化但没人调 sb2ExitGroupChat → 5s 轮询比对, 变了就退出群模式
      //   注意: openGroupChat 自己会 removeItem('sb_current_emp') (inline-03:6010 防串混)
      //   → 变 '' 是群聊正常态, 只有变成非空真员工 id 才说明用户点了私聊
      var _empNow = localStorage.getItem('sb_current_emp') || '';
      if (window._sb2GroupMode) {
        if (window._sb2GroupLastEmp === undefined) window._sb2GroupLastEmp = _empNow;
        else if (_empNow && _empNow !== window._sb2GroupLastEmp) { window._sb2GroupLastEmp = _empNow; sb2ExitGroupChat(); }
      } else {
        window._sb2GroupLastEmp = _empNow;
      }
    } catch (e) { /* 守卫 */ }
  }, 5000);

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
  // ★ r68 批注①: 员工档案 onclick 跨块调用 — IIFE 内函数必须挂 window 才够得着
  window.sb2_openEmpProfile = sb2_openEmpProfile;

  // ===== 群聊模式 (2026-10-08 重新设计, 老大「群聊是以前的设计, 重新设计」) =====
  // 视觉: sb2 原生群聊面板 — 群信息顶栏 + 成员头像栈 + slim 公告栏 + sb2 气泡消息流
  // 数据: 发送/接收全走 legacy 群聊管线 (openGroupChat/sendGroupMessage/OpenClaw 流式/链式@)
  //   legacy 把每条消息渲染进 hidden 的 #messagesArea, MutationObserver 镜像成 sb2 气泡
  //   → AI 协作功能零重写, 旧设计 UI 完全不再显示 (#chatArea 保持 hidden)
  var _sb2GroupNodeMap = [];  // [{src: legacyNode, row: sb2RowEl}] (legacy 节点没有稳定 id, 用数组配对)

  function _sb2GroupFindRow(srcNode) {
    for (var i = 0; i < _sb2GroupNodeMap.length; i++) {
      if (_sb2GroupNodeMap[i].src === srcNode) return _sb2GroupNodeMap[i];
    }
    return null;
  }

  // 群顶栏 + 公告栏渲染 (幂等, 真数据: groups 全局/window.projects)
  function sb2RenderGroupShell() {
    var groupId = window._sb2GroupMode;
    if (!groupId || groupId === true) return;
    var list = [];
    try { list = (typeof groups !== 'undefined' && Array.isArray(groups) && groups.length) ? groups : (window.projects || []); } catch (e) {}
    var g = null;
    for (var i = 0; i < list.length; i++) { if (list[i] && list[i].id === groupId) { g = list[i]; break; } }
    if (!g) {
      // 缓存没有 → 拉一次真数据 (fire-and-forget, 拉到重渲)
      if (typeof apiFetch === 'function' && !window._sb2GroupShellLoading) {
        window._sb2GroupShellLoading = true;
        apiFetch('/api/groups').then(function (r) { return r && r.ok ? r.json() : null; }).then(function (data) {
          window._sb2GroupShellLoading = false;
          if (Array.isArray(data)) {
            try { window.projects = data.slice(); } catch (e) {}
            if (window._sb2GroupMode) sb2RenderGroupShell();
          }
        }).catch(function () { window._sb2GroupShellLoading = false; });
      }
      return;
    }
    var info = document.getElementById('sb2GroupTopbarInfo');
    if (info) info.hidden = false;
    var av = document.getElementById('sb2GroupTopbarAvatar');
    if (av) {
      av.textContent = g.emoji || '👥';
      av.style.background = 'linear-gradient(135deg,' + (g.bg || '#5856D6') + ',' + (g.bg || '#5856D6') + 'dd)';
    }
    var nm = document.getElementById('sb2GroupTopbarName');
    if (nm) nm.textContent = g.name || '-';
    var members = Array.isArray(g.members) ? g.members : [];
    var sub = document.getElementById('sb2GroupTopbarSub');
    if (sub) sub.textContent = members.length + ' 名成员 · 群聊';
    var stack = document.getElementById('sb2GroupTopbarMembers');
    if (stack) {
      var html = '';
      members.slice(0, 4).forEach(function (m) {
        var name = (m && (m.name || m.display_name)) || '?';
        var bg = (m && m.bg) || '#8E8E93';
        html += '<div class="mini-avatar" style="background:' + bg + ';">' + _sb2EscapeHtml(name.slice(0, 1)) + '</div>';
      });
      if (members.length > 4) html += '<div class="mini-avatar mini-more">+' + (members.length - 4) + '</div>';
      stack.innerHTML = html;
    }
    var detailBtn = document.getElementById('sb2GroupTopbarDetail');
    if (detailBtn) {
      detailBtn.style.display = '';  // 元素自带 inline display:none, CSS 规则盖不过, 这里显式放开
      detailBtn.onclick = function () {
        try { if (typeof openGroupDetail === 'function') openGroupDetail(groupId); } catch (e) {}
      };
    }
    // slim 公告栏: 真数据, 无公告整条隐藏
    var annBar = document.getElementById('sb2GroupAnnouncementBar');
    var annText = document.getElementById('sb2GroupAnnouncementText');
    if (annBar && annText) {
      var ann = (g.announcement || '').trim();
      if (ann) { annText.textContent = ann; annBar.hidden = false; }
      else { annBar.hidden = true; annText.textContent = ''; }
    }
    // 输入框 placeholder 切群语境
    var inp = document.getElementById('sb2ChatInput');
    if (inp) inp.placeholder = '在群里发言, @成员 可点名 AI 回复…';
  }

  // legacy 单节点 → sb2 行 (返回 HTML 串; 不认识的节点返回 null)
  function sb2GroupMirrorNode(node) {
    if (!node || node.nodeType !== 1) return null;
    if (node.id === 'typingIndicator') return null;
    // 日期分割线
    if (node.classList && node.classList.contains('time-separator')) {
      var label = node.textContent.trim() || '今天';
      return '<div class="sb2-chat-date-divider"><span>' + _sb2EscapeHtml(label) + '</span></div>';
    }
    // 系统提示
    if (node.classList && node.classList.contains('system-msg')) {
      return '<div class="sb2-chat-msg-row" data-role="system"><div class="sb2-chat-msg-col" style="align-items:center;">'
        + '<span class="sb2-chat-bubble-time">' + _sb2EscapeHtml(node.textContent.trim()) + '</span></div></div>';
    }
    if (!node.classList || !node.classList.contains('msg')) return null;
    var bubble = node.querySelector('.msg-bubble');
    var bodyHtml = bubble ? bubble.innerHTML : '';
    var senderNameEl = node.querySelector('.msg-sender-name');
    var senderRoleEl = node.querySelector('.msg-sender-role');
    var senderTimeEl = node.querySelector('.msg-sender-time');
    var isOwn = node.classList.contains('own');
    // 头像: 取 legacy avatar 底色 + 首字 (renderAvatar 可能是 img, 取文字首字兜底)
    var avEl = node.querySelector('.msg-avatar .avatar');
    var avBg = '';
    if (avEl && avEl.style && avEl.style.background) avBg = avEl.style.background;
    var senderName = senderNameEl ? senderNameEl.textContent.trim() : '';
    var avChar = senderName ? senderName.charAt(0) : '🤖';
    if (isOwn) {
      return '<div class="sb2-chat-msg-row" data-role="user"><span class="sb2-chat-msg-avatar user">我</span>'
        + '<div class="sb2-chat-msg-col"><div class="sb2-chat-bubble" data-role="user">'
        + '<div class="sb2-chat-bubble-body">' + bodyHtml + '</div></div></div></div>';
    }
    var roleTxt = senderRoleEl ? senderRoleEl.textContent.trim() : '';
    var timeTxt = senderTimeEl ? senderTimeEl.textContent.trim() : '';
    var meta = '<div class="sb2-chat-msg-meta"><span class="sb2-chat-msg-meta-name">' + _sb2EscapeHtml(senderName || 'AI') + '</span>'
      + (roleTxt ? '<span class="sb2-chat-msg-meta-role">' + _sb2EscapeHtml(roleTxt) + '</span>' : '')
      + (timeTxt ? '<span class="sb2-chat-msg-meta-time">' + _sb2EscapeHtml(timeTxt) + '</span>' : '')
      + '</div>';
    var avStyle = avBg ? ' style="' + _sb2EscapeHtml(avBg) + '"' : '';
    return '<div class="sb2-chat-msg-row" data-role="assistant"><span class="sb2-chat-msg-avatar assistant"' + avStyle + '>' + _sb2EscapeHtml(avChar) + '</span>'
      + '<div class="sb2-chat-msg-col">' + meta
      + '<div class="sb2-chat-bubble" data-role="assistant"><div class="sb2-chat-bubble-body">' + bodyHtml + '</div></div>'
      + '</div></div>';
  }

  // 全量镜像 (进入/切回时): 清空 sb2 消息区, 逐节点重建
  function sb2GroupMirrorAll() {
    var area = document.getElementById('messagesArea');
    var container = document.getElementById('sb2ChatMessages');
    if (!area || !container) return;
    container.innerHTML = '';
    _sb2GroupNodeMap = [];
    var hasMsg = false;
    Array.prototype.forEach.call(area.children, function (node) {
      var html = sb2GroupMirrorNode(node);
      if (html === null) return;
      var wrap = document.createElement('div');
      wrap.innerHTML = html;
      var row = wrap.children.length === 1 ? wrap.firstChild : wrap;
      container.appendChild(row);
      _sb2GroupNodeMap.push({ src: node, row: row });
      hasMsg = true;
    });
    if (!hasMsg) {
      container.innerHTML = '<div class="sb2-chat-empty"><div class="sb2-chat-empty-icon">👥</div>'
        + '<div>在群里说第一句吧</div>'
        + '<div class="sb2-chat-empty-tip">@成员 点名 AI 回复 / Enter 发送</div></div>';
    }
    container.scrollTop = container.scrollHeight;
    _sb2GroupObserverStart();
  }

  // 增量观察者: legacy #messagesArea 的增删/文本变化 → 同步 sb2 行
  var _sb2GroupObs = null;
  function _sb2GroupObserverStart() {
    var area = document.getElementById('messagesArea');
    if (!area || _sb2GroupObs) return;
    _sb2GroupObs = new MutationObserver(function (muts) {
      if (!window._sb2GroupMode) return;
      var container = document.getElementById('sb2ChatMessages');
      if (!container) return;
      muts.forEach(function (mu) {
        // 删除: 移除对应 sb2 行 (typing 占位移除等)
        Array.prototype.forEach.call(mu.removedNodes, function (n) {
          var hit = _sb2GroupFindRow(n);
          if (hit) {
            if (hit.row.parentNode) hit.row.parentNode.removeChild(hit.row);
            _sb2GroupNodeMap = _sb2GroupNodeMap.filter(function (x) { return x !== hit; });
          }
        });
        // 新增: 镜像成 sb2 行 (插到对应位置 — legacy 只往末尾插, 直接 append)
        Array.prototype.forEach.call(mu.addedNodes, function (n) {
          if (_sb2GroupFindRow(n)) return;
          var html = sb2GroupMirrorNode(n);
          if (html === null) return;
          var empty = container.querySelector('.sb2-chat-empty');
          if (empty) empty.parentNode.removeChild(empty);
          var wrap = document.createElement('div');
          wrap.innerHTML = html;
          var row = wrap.children.length === 1 ? wrap.firstChild : wrap;
          container.appendChild(row);
          _sb2GroupNodeMap.push({ src: n, row: row });
        });
        // 文本/子节点变化 (流式打字): 同步已映射行的气泡体
        if (mu.type === 'characterData' || mu.type === 'childList') {
          var t = mu.target;
          var srcNode = (t && t.nodeType === 1) ? t : (t && t.parentNode);
          while (srcNode && srcNode !== area) {
            var hit2 = _sb2GroupFindRow(srcNode);
            if (hit2) {
              var bubble = srcNode.querySelector ? srcNode.querySelector('.msg-bubble') : null;
              var body = hit2.row.querySelector ? hit2.row.querySelector('.sb2-chat-bubble-body') : null;
              if (bubble && body && body.innerHTML !== bubble.innerHTML) body.innerHTML = bubble.innerHTML;
              break;
            }
            srcNode = srcNode.parentNode;
          }
        }
      });
      container.scrollTop = container.scrollHeight;
    });
    _sb2GroupObs.observe(area, { childList: true, subtree: true, characterData: true });
  }
  function _sb2GroupObserverStop() {
    if (_sb2GroupObs) { try { _sb2GroupObs.disconnect(); } catch (e) {} _sb2GroupObs = null; }
    _sb2GroupNodeMap = [];
  }

  function sb2EnterGroupChat(groupId) {
    try { if (typeof switchModule === 'function') switchModule('messages'); } catch (e) {}
    window._sb2GroupMode = groupId || true;
    setTimeout(function () {
      try {
        // legacy 初始化群状态 (currentGroupId/历史/公告全在 hidden DOM 里跑, 不外显旧设计)
        if (typeof openGroupChat === 'function') openGroupChat(groupId);
        // openGroupChat 会 removeItem('sb_current_emp') (防串混) — 同步基准防看护误退
        window._sb2GroupLastEmp = localStorage.getItem('sb_current_emp') || '';
        renderChatMain();
      } catch (e) { console.error('[sb2EnterGroupChat]', e); }
    }, 300);
  }
  function sb2ExitGroupChat() {
    if (!window._sb2GroupMode) return;
    window._sb2GroupMode = null;
    try {
      _sb2GroupObserverStop();
      var shell = document.getElementById('sb2ChatMain');
      if (shell) shell.classList.remove('sb2-group-mode');
      var info = document.getElementById('sb2GroupTopbarInfo');
      if (info) info.hidden = true;
      var detailBtn = document.getElementById('sb2GroupTopbarDetail');
      if (detailBtn) detailBtn.style.display = 'none';
      var annBar = document.getElementById('sb2GroupAnnouncementBar');
      if (annBar) annBar.hidden = true;
      var inp = document.getElementById('sb2ChatInput');
      if (inp) inp.placeholder = '给员工派活…';
      if (typeof currentModule !== 'undefined' && currentModule === 'messages') renderChatMain();
    } catch (e) { /* 守卫 */ }
  }
  window._sb2GroupMode = null;
  window.sb2EnterGroupChat = sb2EnterGroupChat;
  window.sb2ExitGroupChat = sb2ExitGroupChat;
})();
