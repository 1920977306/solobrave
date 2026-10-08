/* ===== index.html 内联块 11 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: (function () { */

(function () {
  'use strict';

  // ===== State =====
  var _sb2ChatTasks = {
    active: false,        // popup 是否打开
    mode: null,           // 'slash' | 'mention' | 'model'
    selectedIdx: 0,
    slash: {
      filter: '',
      cmds: [
        { cmd: 'help',      desc: '列出所有可用命令' },
        { cmd: 'clear',     desc: '清空当前对话' },
        { cmd: 'stop',      desc: '停止生成 (中断当前回复)' },
        { cmd: 'model',     desc: '切换模型 (请求级)' },
        { cmd: 'summarize', desc: '压缩历史 (调用记忆池)' },
        { cmd: 'export',    desc: '导出当前对话为 JSON' },
        { cmd: 'forbidden', desc: '违禁词自检' }
      ]
    },
    mention: {
      filter: '',
      emps: [],           // 复制自 window.emps
      triggerPos: -1      // @ 在输入框中的位置
    },
    model: {
      current: 'kimi-for-coding',  // 默认值, 启动时从 localStorage 恢复
      list: [
        { name: 'kimi-for-coding', desc: '代码场景首选' },
        { name: 'glm-4-flash',     desc: '默认 fallback' },
        { name: 'deepseek-chat',   desc: '深度推理' },
        { name: 'gpt-4o-mini',     desc: '多模态' }
      ]
    }
  };

  // ===== Utility =====
  function _sb2EscapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s).replace(/[&<>"']/g, function (c) {
      return ({ '&': '&', '<': '<', '>': '>', '"': '"', "'": '&#39;' })[c];
    });
  }

  // ===== Popup 容器 (slash / mention / model 共用) =====
  function _sb2ShowPopup() {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (p) p.hidden = false;
  }
  function _sb2HidePopup() {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (p) { p.hidden = true; p.innerHTML = ''; }
    _sb2ChatTasks.active = false;
    _sb2ChatTasks.mode = null;
    _sb2ChatTasks.selectedIdx = 0;
  }
  function _sb2RenderPopup(html) {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (p) p.innerHTML = html;
  }

  // ===== §2.3 模型选择 =====
  function sb2_renderModelMenu() {
    _sb2ChatTasks.mode = 'model';
    _sb2ChatTasks.active = true;
    _sb2ChatTasks.selectedIdx = Math.max(0, _sb2ChatTasks.model.list.findIndex(function(m) {
      return m.name === _sb2ChatTasks.model.current;
    }));
    var html = '';
    for (var i = 0; i < _sb2ChatTasks.model.list.length; i++) {
      var m = _sb2ChatTasks.model.list[i];
      var sel = (i === _sb2ChatTasks.selectedIdx) ? ' active selected' : '';
      html += ''
        + '<div class="sb2-chat-tasks-row' + sel + '" data-mode="model" data-idx="' + i + '">'
        + '  <span class="sb2-chat-tasks-row-cmd">' + _sb2EscapeHtml(m.name) + '</span>'
        + '  <span class="sb2-chat-tasks-row-desc">' + _sb2EscapeHtml(m.desc) + '</span>'
        + '</div>';
    }
    _sb2RenderPopup(html);
    _sb2ShowPopup();
  }
  /* ★ r73 反馈 Comment 1 老大 17:44「点开改成弹窗」: 顶栏设置按钮改成居中浮窗
     — 跟 KB 详情浮层 (line 12712+) + 规律详情浮层 (line 10229+) 同款 precedent
     — 复用 _sb2ChatTasks.model.list 数据, 渲染到 .sb2-model-picker-list
     — 选完模型调 sb2_switchModel + 更新顶栏按钮文字 + 关闭浮窗
     — closeModelPicker 走 outside-click (overlay onclick) + close 按钮 + 选完自动关 */
  function sb2_openModelPicker(){
    var listEl = document.getElementById('sb2ModelPickerList');
    var overlay = document.getElementById('sb2ModelPickerOverlay');
    var panel = document.getElementById('sb2ModelPickerPanel');
    if (!listEl || !overlay || !panel) return;
    // 渲染模型列表
    var html = '';
    for (var i = 0; i < _sb2ChatTasks.model.list.length; i++) {
      var m = _sb2ChatTasks.model.list[i];
      var isActive = m.name === _sb2ChatTasks.model.current;
      var cls = 'sb2-model-picker-item' + (isActive ? ' active' : '');
      html += ''
        + '<div class="' + cls + '" data-mname="' + _sb2EscapeHtml(m.name) + '" onclick="sb2_pickModel(\'' + _sb2EscapeHtml(m.name).replace(/'/g, "\\'") + '\')">'
        + '  <div class="sb2-model-picker-item-body">'
        + '    <div class="sb2-model-picker-item-name">' + _sb2EscapeHtml(m.name) + '</div>'
        + (m.desc ? '    <div class="sb2-model-picker-item-desc">' + _sb2EscapeHtml(m.desc) + '</div>' : '')
        + '  </div>'
        + '  <span class="sb2-model-picker-item-check">✓</span>'
        + '</div>';
    }
    listEl.innerHTML = html;
    overlay.classList.add('open');
    panel.classList.add('open');
    _sb2HidePopup();  // 关闭 popup menu (避免双重弹窗)
  }
  function closeModelPicker(){
    var overlay = document.getElementById('sb2ModelPickerOverlay');
    var panel = document.getElementById('sb2ModelPickerPanel');
    if (overlay) overlay.classList.remove('open');
    if (panel) panel.classList.remove('open');
  }
  function sb2_pickModel(name){
    sb2_switchModel(name);
    // ★ 同步顶栏按钮文字: 显示当前模型名 (老大 17:44 反馈)
    var btn = document.getElementById('sb2ChatTopbarSettings');
    if (btn) btn.textContent = name;
    // 状态行 span (id 共用 sb2ChatPickModel, 删 button 后 fallback) 自动被 sb2_switchModel 更新
    closeModelPicker();
  }
  function sb2_switchModel(name) {
    var found = _sb2ChatTasks.model.list.find(function(m) { return m.name === name; });
    if (!found) return;
    _sb2ChatTasks.model.current = name;
    try { localStorage.setItem('sb2_current_model', name); } catch (e) { /* localStorage 可能不可用 */ }
    var badge = document.getElementById('sb2ChatPickModel');
    if (badge) badge.textContent = name;
    _sb2HidePopup();
  }
  function sb2_updateModelBadge() {
    var badge = document.getElementById('sb2ChatPickModel');
    if (badge) badge.textContent = _sb2ChatTasks.model.current;
  }
  function sb2_initModelFromStorage() {
    try {
      var saved = localStorage.getItem('sb2_current_model');
      if (saved && _sb2ChatTasks.model.list.find(function(m) { return m.name === saved; })) {
        _sb2ChatTasks.model.current = saved;
      }
    } catch (e) { /* noop */ }
    sb2_updateModelBadge();
  }

  // ===== §2.1 Slash 命令 =====
  function sb2_renderSlashMenu() {
    _sb2ChatTasks.mode = 'slash';
    _sb2ChatTasks.active = true;
    var filter = _sb2ChatTasks.slash.filter.toLowerCase();
    var matched = _sb2ChatTasks.slash.cmds.filter(function(c) {
      return filter.length === 0 || c.cmd.toLowerCase().indexOf(filter) >= 0;
    });
    _sb2ChatTasks.selectedIdx = 0;
    if (matched.length === 0) {
      _sb2RenderPopup('<div class="sb2-chat-tasks-empty">没有匹配命令 (输入 /help 查看全部)</div>');
    } else {
      var html = '';
      for (var i = 0; i < matched.length; i++) {
        var c = matched[i];
        var sel = (i === 0) ? ' active' : '';
        html += ''
          + '<div class="sb2-chat-tasks-row' + sel + '" data-mode="slash" data-idx="' + i + '" data-cmd="' + _sb2EscapeHtml(c.cmd) + '">'
          + '  <span class="sb2-chat-tasks-row-cmd">/' + _sb2EscapeHtml(c.cmd) + '</span>'
          + '  <span class="sb2-chat-tasks-row-desc">' + _sb2EscapeHtml(c.desc) + '</span>'
          + '</div>';
      }
      _sb2RenderPopup(html);
    }
    _sb2ShowPopup();
  }

  // slash 命令执行器
  function sb2_execSlashCommand(cmd) {
    var inp = document.getElementById('sb2ChatInput');
    var messagesEl = document.getElementById('sb2ChatMessages');

    function _showToast(msg, isErr) {
      var t = document.createElement('div');
      t.className = 'sb2-chat-tasks-toast' + (isErr ? ' sb2-chat-tasks-toast-err' : '');
      t.style.cssText = 'position:fixed;left:50%;top:24px;transform:translateX(-50%);background:var(--sb2-s1);color:var(--sb2-t1);border:1px solid var(--sb2-border);padding:8px 16px;border-radius:8px;box-shadow:var(--sb2-sh2);z-index:99;font-size:13px;';
      t.textContent = msg;
      document.body.appendChild(t);
      setTimeout(function() { if (t.parentNode) t.parentNode.removeChild(t); }, 2200);
    }

    switch (cmd) {
      case 'help': {
        var helpText = _sb2ChatTasks.slash.cmds.map(function(c) { return '/' + c.cmd + ' — ' + c.desc; }).join('\n');
        _showToast('命令: ' + _sb2ChatTasks.slash.cmds.length + ' 个\n' + helpText, false);
        break;
      }
      case 'clear': {
        // 清空当前对话 (前端 messages = [])
        if (typeof window._sb2Chat !== 'undefined' && window._sb2Chat && Array.isArray(window._sb2Chat.messages)) {
          window._sb2Chat.messages = [];
        }
        if (messagesEl) messagesEl.innerHTML = ''
          + '<div class="sb2-chat-empty">'
          + '  <div class="sb2-chat-empty-icon">💬</div>'
          + '  <div>对话已清空</div>'
          + '  <div class="sb2-chat-empty-tip">输入消息开始新对话</div>'
          + '</div>';
        _showToast('对话已清空', false);
        break;
      }
      case 'model': {
        // 跳到模型选择浮层
        sb2_renderModelMenu();
        return; // 不清输入框
      }
      case 'summarize': {
        // 调用后端记忆压缩
        _showToast('正在压缩记忆池...', false);
        if (typeof apiFetch === 'function') {
          apiFetch('/api/memory/consolidate', { method: 'POST', body: '{}' })
            .then(function(r) { _showToast(r ? '✓ 记忆压缩已触发' : '压缩失败', !!r); })
            .catch(function(e) { _showToast('压缩失败: ' + (e && e.message), true); });
        }
        break;
      }
      case 'export': {
        // 导出当前对话为 JSON 下载
        var msgs = (typeof window._sb2Chat !== 'undefined' && window._sb2Chat && Array.isArray(window._sb2Chat.messages)) ? window._sb2Chat.messages : [];
        var payload = {
          agentId: (typeof window._sb2Chat !== 'undefined' && window._sb2Chat) ? window._sb2Chat.agentId : null,
          exportedAt: new Date().toISOString(),
          messages: msgs
        };
        try {
          var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
          var url = URL.createObjectURL(blob);
          var a = document.createElement('a');
          a.href = url;
          a.download = 'solobrave-chat-' + payload.agentId + '-' + Date.now() + '.json';
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(function() { URL.revokeObjectURL(url); }, 1000);
          _showToast('对话已导出 (' + msgs.length + ' 条)', false);
        } catch (e) {
          _showToast('导出失败: ' + (e && e.message), true);
        }
        break;
      }
      case 'stop': {
        // 〔stop-cmd 2026-10-08〕真中止: AbortController 断掉进行中的私聊 POST (inline-10 window.sb2_stopGeneration)
        var stopped = (typeof window.sb2_stopGeneration === 'function') ? window.sb2_stopGeneration() : false;
        _showToast(stopped ? '⏹ 已停止生成' : '当前没有生成中的回复', !stopped);
        break;
      }
      case 'forbidden': {
        // 违禁词自检
        if (typeof apiFetch === 'function') {
          apiFetch('/api/forbidden-words/check', { method: 'POST', body: JSON.stringify({ text: inp ? inp.value : '' }) })
            .then(function(r) {
              if (!r) { _showToast('自检失败 (无响应)', true); return null; }
              return r.json();
            })
            .then(function(data) {
              if (!data) return;
              var hits = Array.isArray(data.hits) ? data.hits.length : (data.hit ? 1 : 0);
              _showToast(hits > 0 ? '⚠️ 发现 ' + hits + ' 个违禁词' : '✓ 未发现违禁词', hits > 0);
            })
            .catch(function(e) { _showToast('自检失败: ' + (e && e.message), true); });
        }
        break;
      }
      default:
        _showToast('未知命令: /' + cmd + ' (输入 /help 查看)', true);
        return;
    }

    // 清输入框
    if (inp) { inp.value = ''; inp.style.height = 'auto'; }
    // 触发 send-btn 状态更新
    var sendBtn = document.getElementById('sb2ChatSendBtn');
    if (sendBtn) sendBtn.disabled = true;
    _sb2HidePopup();
  }

  // ===== §2.2 @ 提及员工 =====
  function sb2_renderMentionMenu() {
    _sb2ChatTasks.mode = 'mention';
    _sb2ChatTasks.active = true;
    // 从 window.emps 复制 (兜底: 未填充则空数组)
    if (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) {
      _sb2ChatTasks.mention.emps = window.emps.slice();
    } else {
      _sb2ChatTasks.mention.emps = [];
    }
    var filter = _sb2ChatTasks.mention.filter.toLowerCase();
    var matched = _sb2ChatTasks.mention.emps.filter(function(e) {
      if (!e || !e.name) return false;
      return filter.length === 0 || (e.name || '').toLowerCase().indexOf(filter) >= 0
        || (e.role || '').toLowerCase().indexOf(filter) >= 0;
    });
    _sb2ChatTasks.selectedIdx = 0;

    if (_sb2ChatTasks.mention.emps.length === 0) {
      _sb2RenderPopup('<div class="sb2-chat-tasks-empty">员工加载中, 请稍后</div>');
    } else if (matched.length === 0) {
      _sb2RenderPopup('<div class="sb2-chat-tasks-empty">没有匹配员工</div>');
    } else {
      var html = '';
      for (var i = 0; i < matched.length; i++) {
        var e = matched[i];
        var name = e.name || '-';
        var role = e.role || '-';
        var online = e.online === true || e.status === 'online';
        var initials = name.substring(0, 1).toUpperCase();
        var sel = (i === 0) ? ' active' : '';
        var offlineCls = online ? '' : ' offline';
        var dotCls = online ? ' online' : '';
        html += ''
          + '<div class="sb2-chat-tasks-row' + sel + offlineCls + '" data-mode="mention" data-idx="' + i + '" data-empid="' + _sb2EscapeHtml(e.id || '') + '" data-name="' + _sb2EscapeHtml(name) + '">'
          + '  <span class="sb2-chat-tasks-avatar">' + _sb2EscapeHtml(initials) + '</span>'
          + '  <span class="sb2-chat-tasks-mention-name">' + _sb2EscapeHtml(name) + '</span>'
          + '  <span class="sb2-chat-tasks-mention-role">' + _sb2EscapeHtml(role) + '</span>'
          + '  <span class="sb2-chat-tasks-status">'
          +    '<span class="sb2-chat-tasks-status-dot' + dotCls + '"></span>'
          +    (online ? '' : '<span>(离线)</span>')
          + '  </span>'
          + '</div>';
      }
      _sb2RenderPopup(html);
    }
    _sb2ShowPopup();
  }

  function sb2_selectMention() {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (!p) return;
    var rows = p.querySelectorAll('.sb2-chat-tasks-row[data-mode="mention"]');
    var row = rows[_sb2ChatTasks.selectedIdx];
    if (!row) return;
    var name = row.getAttribute('data-name') || '';
    var empId = row.getAttribute('data-empid') || '';
    var inp = document.getElementById('sb2ChatInput');
    if (!inp || !name) return;

    // 在输入框 @ 位置插入 `@Name ` token
    var text = inp.value;
    var pos = _sb2ChatTasks.mention.triggerPos;
    if (pos < 0 || pos > text.length) pos = text.length;
    // 找到 @ 之后到当前位置的 filter 范围, 替换成 `@Name `
    var before = text.substring(0, pos);
    var after = text.substring(pos);
    var atIdx = before.lastIndexOf('@');
    if (atIdx >= 0) {
      before = before.substring(0, atIdx);
    }
    inp.value = before + '@' + name + ' ' + after;
    // 触发 send-btn enable 状态
    var sendBtn = document.getElementById('sb2ChatSendBtn');
    if (sendBtn) sendBtn.disabled = inp.value.trim().length === 0;
    // focus 回输入框
    inp.focus();
    _sb2HidePopup();
  }

  // ===== Trigger detection (输入框值变化) =====
  function sb2_checkInputTriggers() {
    var inp = document.getElementById('sb2ChatInput');
    if (!inp) return;
    var text = inp.value;
    var caret = inp.selectionStart || text.length;

    // Slash: 检测 `/` 在开头位置
    if (text.charAt(0) === '/' && caret >= 1) {
      var slashFilter = text.substring(1, caret);
      // 如果中间有空格, 关闭 slash 菜单
      if (slashFilter.indexOf(' ') >= 0 || slashFilter.indexOf('\n') >= 0) {
        if (_sb2ChatTasks.mode === 'slash') _sb2HidePopup();
        return;
      }
      _sb2ChatTasks.slash.filter = slashFilter;
      sb2_renderSlashMenu();
      return;
    }

    // Mention: 检测 `@` 在 caret 前
    var subText = text.substring(0, caret);
    var atIdx = subText.lastIndexOf('@');
    if (atIdx >= 0) {
      var between = subText.substring(atIdx + 1);
      // @ 后到 caret 之间不能有空格/换行 (否则不是 mention)
      if (between.indexOf(' ') < 0 && between.indexOf('\n') < 0) {
        _sb2ChatTasks.mention.filter = between;
        _sb2ChatTasks.mention.triggerPos = atIdx;
        sb2_renderMentionMenu();
        return;
      }
    }

    // 都不匹配, 关闭 popup
    if (_sb2ChatTasks.active) _sb2HidePopup();
  }

  // ===== Keyboard listener (popup 打开时) =====
  function sb2_handleTasksKeydown(e) {
    if (!_sb2ChatTasks.active) return;
    // popup 打开时拦截所有键盘事件, 防止 §1 的 Enter 监听误触发 send
    e.stopImmediatePropagation();

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      _sb2ChatTasks.selectedIdx++;
      sb2_updateActiveRow();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      _sb2ChatTasks.selectedIdx = Math.max(0, _sb2ChatTasks.selectedIdx - 1);
      sb2_updateActiveRow();
    } else if (e.key === 'Enter') {
      // popup 打开时拦截 Enter, 走选中逻辑 (不发送)
      e.preventDefault();
      sb2_selectActive();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      _sb2HidePopup();
    }
  }

  function sb2_updateActiveRow() {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (!p) return;
    var rows = p.querySelectorAll('.sb2-chat-tasks-row');
    if (rows.length === 0) return;
    _sb2ChatTasks.selectedIdx = Math.max(0, Math.min(_sb2ChatTasks.selectedIdx, rows.length - 1));
    for (var i = 0; i < rows.length; i++) {
      var cls = rows[i].classList;
      if (i === _sb2ChatTasks.selectedIdx) {
        cls.add('active');
        // scroll into view
        if (rows[i].scrollIntoView) rows[i].scrollIntoView({ block: 'nearest' });
      } else {
        cls.remove('active');
      }
    }
  }

  function sb2_selectActive() {
    var p = document.getElementById('sb2ChatTasksPopup');
    if (!p) return;
    var rows = p.querySelectorAll('.sb2-chat-tasks-row');
    var row = rows[_sb2ChatTasks.selectedIdx];
    if (!row) return;
    var mode = row.getAttribute('data-mode');
    if (mode === 'mention') {
      sb2_selectMention();
    } else if (mode === 'model') {
      var idx = parseInt(row.getAttribute('data-idx'), 10);
      var m = _sb2ChatTasks.model.list[idx];
      if (m) sb2_switchModel(m.name);
    } else if (mode === 'slash') {
      var cmd = row.getAttribute('data-cmd');
      if (cmd) sb2_execSlashCommand(cmd);
    }
  }

  // ===== Click outside to close =====
  function sb2_bindOutsideClick() {
    document.addEventListener('click', function(e) {
      if (!_sb2ChatTasks.active) return;
      var p = document.getElementById('sb2ChatTasksPopup');
      var inp = document.getElementById('sb2ChatInput');
      var pickModel = document.getElementById('sb2ChatPickModel');
      var settingsBtn = document.getElementById('sb2ChatTopbarSettings');
      if (!p) return;
      if (p.contains(e.target)) return;
      if (inp && inp.contains(e.target)) return;  // 输入框内点击由 keyup 触发
      if (pickModel && pickModel.contains(e.target)) return;  // 状态行模型名点击单独处理
      // ★ r68 批注②: 顶栏「设置」按钮同 pickModel — 点击先开弹层再冒泡到 document,
      //   不设豁免会被本监听器立刻关掉 (开即合, 表现「点击没反应」)
      // ★ r71 反向修法 (老大 17:08 走查截图): 撤回 r70 项① 删的顶栏「设置」按钮, 豁免也一并恢复
      if (settingsBtn && settingsBtn.contains(e.target)) return;
      _sb2HidePopup();
    });
  }

  // ===== Bind =====
  function sb2_bindTasks() {
    var inp = document.getElementById('sb2ChatInput');
    if (!inp) return;

    // 输入变化触发 slash / mention 检测
    inp.addEventListener('input', sb2_checkInputTriggers);
    // keyup 兼容 (粘贴/IME)
    inp.addEventListener('keyup', sb2_checkInputTriggers);
    // 键盘交互 (popup 打开时)
    inp.addEventListener('keydown', sb2_handleTasksKeydown);

    // 状态行内嵌模型名点击 → 打开模型选择
    var pickModel = document.getElementById('sb2ChatPickModel');
    if (pickModel) {
      pickModel.addEventListener('click', function(e) {
        e.stopPropagation();
        sb2_renderModelMenu();
      });
    }

    // popup 行点击
    var p = document.getElementById('sb2ChatTasksPopup');
    if (p) {
      p.addEventListener('click', function(e) {
        var row = e.target.closest('.sb2-chat-tasks-row');
        if (!row) return;
        _sb2ChatTasks.selectedIdx = parseInt(row.getAttribute('data-idx'), 10) || 0;
        sb2_selectActive();
      });
    }

    sb2_bindOutsideClick();
  }

  // ===== Init =====
  function sb2_initTasks() {
    sb2_initModelFromStorage();
    sb2_bindTasks();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', sb2_initTasks);
  } else {
    sb2_initTasks();
  }

  // ===== Expose for §1 sb2_doSend =====
  window.sb2_getCurrentModel = function() { return _sb2ChatTasks.model.current; };
  window.sb2_getMentions = function() {
    var inp = document.getElementById('sb2ChatInput');
    if (!inp) return [];
    var text = inp.value;
    if (!text) return [];
    // 从 text 中提取 @Name, 匹配 emps
    var emps = (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) ? window.emps : [];
    var seen = {};
    var mentions = [];
    var re = /@(\S+?)(?=\s|$|[,，。])/g;
    var m;
    while ((m = re.exec(text)) !== null) {
      var name = m[1];
      if (seen[name]) continue;
      var emp = emps.find(function(e) { return e && e.name === name; });
      if (emp) {
        seen[name] = true;
        mentions.push({ empId: emp.id, name: emp.name });
      }
    }
    return mentions;
  };
  window.sb2_renderModelMenu = sb2_renderModelMenu;
  window.sb2_switchModel = sb2_switchModel;
  window.sb2_openModelPicker = sb2_openModelPicker;
  window.closeModelPicker = closeModelPicker;
  window.sb2_pickModel = sb2_pickModel;
  window.sb2_execSlashCommand = sb2_execSlashCommand;
  window.sb2_renderMentionMenu = sb2_renderMentionMenu;
  window._sb2ChatTasks = _sb2ChatTasks;  // F12 调试

  /* 〔r65 批注① 2026-10-07〕输入框工具钮三助手 — 老大批注「缺以前的输入框功能」
     slash/mention/model 菜单本体本 IIFE 早已实现 (上方), 缺的只是触发按钮 (index.html 输入行)
     HTML onclick 够不到 IIFE 内部 → 按 IIFE+onclick 铁律 window 显式暴露 */
  window.sb2_insertEmoji = function() {
    var inp = document.getElementById('sb2ChatInput');
    if (!inp) return;
    var start = inp.selectionStart || 0;
    var end = inp.selectionEnd || 0;
    inp.value = inp.value.substring(0, start) + '😊' + inp.value.substring(end);
    inp.selectionStart = inp.selectionEnd = start + 2;
    inp.focus();
    var sendBtn = document.getElementById('sb2ChatSendBtn');
    if (sendBtn) sendBtn.disabled = inp.value.trim().length === 0;
  };
  window.sb2_openMentionBtn = function() {
    var inp = document.getElementById('sb2ChatInput');
    _sb2ChatTasks.mention.filter = '';
    if (inp) {
      var pos = inp.selectionStart || inp.value.length;
      inp.value = inp.value.substring(0, pos) + '@' + inp.value.substring(pos);
      inp.selectionStart = inp.selectionEnd = pos + 1;
      inp.focus();
      _sb2ChatTasks.mention.triggerPos = pos;
    }
    sb2_renderMentionMenu();
  };
  window.sb2_openSlashBtn = function() {
    _sb2ChatTasks.slash.filter = '';
    sb2_renderSlashMenu();
  };
})();
