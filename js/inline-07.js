/* ===== index.html 内联块 7 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: // ======================================== */
// ========================================
// Badge 紧急标记功能
// ========================================

// 设置员工紧急标记

// 渲染员工头像的 Badge
function renderAvatarBadge(emp, avatarClass) {
  if (!emp || !emp.badge) return '';
  var badgeClass = 'badge ';
  var pulseClass = '';
  var numberHtml = '';
  if (emp.badge.level === 'urgent') {
    badgeClass += 'urgent has-number pop-in';
    pulseClass = 'pulse-urgent';
    if (emp.badge.number) {
      numberHtml = '<span class="badge-number">' + emp.badge.number + '</span>';
    }
  } else if (emp.badge.level === 'important') {
    badgeClass += 'important no-number pop-in';
    pulseClass = 'pulse-important';
  } else if (emp.badge.level === 'normal') {
    badgeClass += 'normal no-number pop-in';
  } else {
    return '';
  }
  return '<div class="' + badgeClass + '">' + numberHtml + '</div>';
}

// 更新 renderEmployeeItem 函数中的 badge 渲染
// ★ fix/render-employee-monkey-patch-v2: 改用公共降级器 _applyMonkeyPatchWithRetry
//   上一轮 c1f9adb 只 1 级 DOMContentLoaded 防御, 生产仍报 skip warn (L13053 函数定义晚于 DOMContentLoaded)
//   升级到 3 级: DOMContentLoaded → window.load → 500ms setTimeout
function _applyRenderEmployeePatch() {
  if (typeof renderEmployeeItem !== 'function') return false;
  var _originalRenderEmployeeItem = renderEmployeeItem;
  renderEmployeeItem = function (e, showCreator) {
    var size = 40;
    var avatarHtml = renderAvatar(e, size);
    var creatorTag = '';

    // admin/leader视图：显示创建者标签（回退链 createdByName → _createdByName → role → "创建者"，
    // 全空时不再渲染空 span 假装没事，直接显"创建者"占位，避免 admin 列表看到空白行像 bug）
    if (showCreator && (isAdmin() || isLeader()) && e.createdBy && e.createdBy !== (currentUser && currentUser.id)) {
      var creatorName = e.createdByName || e._createdByName || e.role || '创建者';
      creatorTag = '<span style="display:inline-flex;align-items:center;margin-left:6px;padding:1px 6px;background:#E8E8E8;border-radius:4px;font-size:11px;color:#999999;vertical-align:middle;font-weight:500;">👤 ' + escapeHtml(creatorName) + '</span>';
    }

    // 新版 Badge
    var badgeHtml = renderAvatarBadge(e, 'avatar small');

    // 组装头像容器（含 Badge）
    var avatarContainer = '<div class="item-avatar" style="position:relative;width:' + size + 'px;height:' + size + 'px;flex-shrink:0;"><div class="avatar small ' + (e.badge && e.badge.level === 'urgent' ? 'pulse-urgent' : e.badge && e.badge.level === 'important' ? 'pulse-important' : '') + '" style="background:' + escapeAttr(e.bg || '') + ';width:' + size + 'px;height:' + size + 'px;overflow:hidden;">' + avatarHtml + badgeHtml + '</div><div class="status-dot ' + escapeAttr(e.status || 'offline') + '" style="position:absolute;bottom:0;right:0;width:11px;height:11px;border:2px solid var(--bg-primary);border-radius:50%;box-shadow:0 0 0 0.5px rgba(0,0,0,0.06);transition:transform 0.2s;"></div></div>';
    return avatarContainer + '<div class="item-info" style="flex:1;min-width:0;"><div class="item-name" style="font-weight:500;font-size:14px;letter-spacing:-0.01em;">' + escapeHtml(e.name || '') + creatorTag + '</div><div class="item-msg" style="font-size:12px;color:var(--text-secondary);margin-top:2px;">' + getStatusIcon(e.status) + ' ' + escapeHtml(e.msg || '') + '</div></div>';
  };
  return true;
}
_applyMonkeyPatchWithRetry('renderEmployeeItem', _applyRenderEmployeePatch);

// ========================================
// 督促通知功能
// ========================================

var notificationId = 0;
var activeNotifications = {};
var urgingSnoozeTimers = {};

// 显示督促通知
function showUrgingNotify(empName, taskName, deadline, empId) {
  var id = ++notificationId;
  var container = document.getElementById('notificationContainer');
  if (!container) return;

  // 创建卡片
  var card = document.createElement('div');
  card.className = 'notification-card';
  card.id = 'notification-' + id;
  card.innerHTML = '<div class="notification-content">' + '<div class="notification-header">' + '<div class="notification-avatar" style="background:linear-gradient(135deg,#ff6b6b,#ff3b30);display:flex;align-items:center;justify-content:center;color:white;font-weight:600;">' + (empName ? empName.charAt(0) : '?') + '</div>' + '<span class="notification-title"><span class="highlight">' + escapeHtml(empName || '未知') + '</span> 催你处理任务</span>' + '</div>' + '<div class="notification-body">' + '<div class="notification-task">' + escapeHtml(taskName || '未知任务') + '</div>' + '<div class="notification-time">截止时间：' + escapeHtml(deadline || '未设置') + '</div>' + '</div>' + '<div class="notification-actions">' + '<button class="notification-btn primary" onclick="handleUrgingView(' + id + ', \'' + escapeAttr(empId || '') + '\')">查看详情</button>' + '<button class="notification-btn secondary" onclick="handleUrgingSnooze(' + id + ', \'' + escapeAttr(empId || '') + '\', \'' + escapeAttr(taskName || '') + '\', \'' + escapeAttr(deadline || '') + '\')">稍后提醒</button>' + '</div>' + '</div>' + '<div class="notification-progress">' + '<div class="notification-progress-bar" id="progress-' + id + '"></div>' + '</div>';
  container.appendChild(card);

  // 触发动画
  requestAnimationFrame(function () {
    card.classList.add('show');
  });

  // 启动8秒倒计时进度条
  var duration = 8000;
  var progressBar = document.getElementById('progress-' + id);
  if (progressBar) {
    progressBar.style.transition = 'transform ' + duration + 'ms linear';
    requestAnimationFrame(function () {
      progressBar.style.transform = 'scaleX(0)';
    });
  }

  // 8秒后自动消失
  var hideTimer = setTimeout(function () {
    hideNotification(id);
  }, duration);
  activeNotifications[id] = {
    timer: hideTimer,
    empId: empId
  };
}

// 隐藏通知
function hideNotification(id) {
  var card = document.getElementById('notification-' + id);
  if (!card) return;
  card.classList.remove('show');
  card.classList.add('hide');
  setTimeout(function () {
    if (card.parentNode) {
      card.parentNode.removeChild(card);
    }
    delete activeNotifications[id];
  }, 400);
}

// 查看详情 - 打开聊天
function handleUrgingView(id, empId) {
  if (empId && typeof openChat === 'function') {
    openChat(empId);
  }
  hideNotification(id);
}

// 稍后提醒 - 5分钟后再次弹出
function handleUrgingSnooze(id, empId, taskName, deadline) {
  hideNotification(id);

  // 清除之前的 snooze 定时器（如果有）
  if (urgingSnoozeTimers[empId]) {
    clearTimeout(urgingSnoozeTimers[empId]);
  }

  // 5分钟后再次显示
  urgingSnoozeTimers[empId] = setTimeout(function () {
    showUrgingNotify(empId, taskName, deadline, empId);
    delete urgingSnoozeTimers[empId];
  }, 5 * 60 * 1000);
}

// 渲染督促聊天气泡
function renderUrgeBubble(taskName, deadline) {
  return '<div class="urge-header"><span class="urge-icon">⚡</span> 催促处理</div>' + '<div class="urge-body">' + '<div class="urge-task">' + escapeHtml(taskName || '任务') + '</div>' + '<div class="urge-time">截止：' + escapeHtml(deadline || '未设置') + '</div>' + '</div>';
}

// ★ fix/render-employee-monkey-patch-v2: Monkey Patch 公共降级器
//   3 级降级: DOMContentLoaded → window.load → 500ms setTimeout
//   应对 L13053/L16274 函数定义晚于 DOMContentLoaded 的 race (可能在 defer/module 块)
//   1 级防御不够 (c1f9adb 后生产仍报 renderEmployeeItem skip + renderMsgs ReferenceError)
function _applyMonkeyPatchWithRetry(name, applyFn) {
  function attempt(label) {
    if (applyFn()) {
      console.log('[monkey-patch] ' + name + ' patch applied at ' + label);
      return true;
    }
    console.warn('[monkey-patch] ' + name + ' not defined at ' + label);
    return false;
  }
  // 第 1 级: DOMContentLoaded
  document.addEventListener('DOMContentLoaded', function () {
    if (attempt('DOMContentLoaded')) return;
    // 第 2 级: window.load (等所有资源加载完)
    window.addEventListener('load', function () {
      if (attempt('window.load')) return;
      // 第 3 级: 500ms setTimeout 最后一次重试 (module 加载可能 > 500ms)
      setTimeout(function () { attempt('setTimeout-500ms'); }, 500);
    });
  });
}

// 更新 renderMsgs 函数以支持督促气泡 (3 级降级应用)
function _applyRenderMsgsPatch() {
  if (typeof renderMsgs !== 'function') return false;
  var _originalRenderMsgs = renderMsgs;
  renderMsgs = function (type) {
    // 先调用原函数
    _originalRenderMsgs(type);

    // 如果有活跃的催促消息，在消息区域顶部显示
    var area = document.getElementById('messagesArea');
    if (!area) return;

    // 检查是否有催促消息需要显示
    var empId = localStorage.getItem('sb_current_emp');
    var urgeData = localStorage.getItem('sb_urge_' + empId);
    if (urgeData) {
      try {
        var urge = JSON.parse(urgeData);
        if (urge && urge.show) {
          // 在消息区域顶部插入催促气泡
          var urgeBubble = document.createElement('div');
          urgeBubble.className = 'msg urge';
          urgeBubble.id = 'urge-bubble';
          urgeBubble.innerHTML = '<div class="msg-bubble">' + renderUrgeBubble(urge.taskName, urge.deadline) + '</div>';
          var firstMsg = area.querySelector('.msg, .chat-empty-state');
          if (firstMsg) {
            area.insertBefore(urgeBubble, firstMsg);
          } else {
            area.appendChild(urgeBubble);
          }

          // 3秒后淡出
          setTimeout(function () {
            var bubble = document.getElementById('urge-bubble');
            if (bubble) {
              bubble.style.opacity = '0';
              bubble.style.transition = 'opacity 0.5s';
              setTimeout(function () {
                if (bubble.parentNode) bubble.parentNode.removeChild(bubble);
              }, 500);
            }
          }, 3000);

          // 清除催促状态
          urge.show = false;
          localStorage.setItem('sb_urge_' + empId, JSON.stringify(urge));
        }
      } catch (e) {}
    }
  };
  return true;
}
_applyMonkeyPatchWithRetry('renderMsgs', _applyRenderMsgsPatch);

// 触发催促通知（供外部调用）
function triggerUrgeNotification(empId, taskName, deadline) {
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (emp) {
    showUrgingNotify(emp.name, taskName, deadline, empId);

    // 同时设置催促状态，在下次打开聊天时显示气泡
    localStorage.setItem('sb_urge_' + empId, JSON.stringify({
      taskName: taskName,
      deadline: deadline,
      show: true
    }));

    // 推送群主督促通知到通知中心（保留现有提醒 UI 行为）
    if (typeof apiFetch === 'function') {
      apiFetch('/api/notifications', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          type: 'group_urge',
          title: '⚡ ' + emp.name + ' 催你处理任务',
          content: '任务「' + (taskName || '') + '」截止时间：' + (deadline || '未设置'),
          agent_id: empId
        })
      }).then(function () {
        refreshUnreadBadge();
      }).catch(function (e) {
        console.warn('[Urge] 推送通知失败:', e);
      });
    }
  }
}

// ========== Token 统计面板 ==========
function showTokenStats() {
  var panel = document.getElementById('tokenStatsPanel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'tokenStatsPanel';
    panel.className = 'modal-overlay';
    panel.innerHTML = '<div class="modal" style="width:90%;max-width:600px;max-height:80vh;overflow:auto;">' + '<div class="modal-header">' + '<h3>📊 Token 统计</h3>' + '<button class="modal-close" onclick="closeTokenStats()">×</button>' + '</div>' + '<div id="tokenStatsContent" style="padding:16px;"></div>' + '</div>';
    document.body.appendChild(panel);
  }
  renderTokenStats();
  panel.classList.add('open');
}
function closeTokenStats() {
  var panel = document.getElementById('tokenStatsPanel');
  if (panel) panel.classList.remove('open');
}
async function renderTokenStats() {
  var content = document.getElementById('tokenStatsContent');
  if (!content) return;
  content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  try {
    var resp = await apiFetch('/api/token-usage?group_by=agent');
    var data = await resp.json();
    var employeeStats = (data.items || []).map(normalizeTokenUsageItem);
    var summary = data.summary || {};
    var totalInput = summary.inputTokens || 0;
    var totalOutput = summary.outputTokens || 0;
    var totalCache = summary.cacheReadTokens || 0;
    var totalTokens = summary.totalTokens || 0;
    var rows = '';
    employeeStats.forEach(function (e) {
      rows += '<tr style="border-bottom:1px solid var(--separator);">' + '<td style="padding:8px;display:flex;align-items:center;gap:8px;">' + '<span>' + escapeHtml(e.name || '未知') + '</span>' + '</td>' + '<td style="padding:8px;text-align:right;font-size:12px;">' + formatNumber(e.inputTokens) + '</td>' + '<td style="padding:8px;text-align:right;font-size:12px;">' + formatNumber(e.outputTokens) + '</td>' + '<td style="padding:8px;text-align:right;font-size:12px;">' + formatNumber(e.cacheReadTokens) + '</td>' + '<td style="padding:8px;text-align:right;font-size:12px;font-weight:600;">' + formatNumber(e.totalTokens) + '</td>' + '</tr>';
    });
    content.innerHTML = '<div style="margin-bottom:16px;">' + '<div style="display:flex;gap:12px;margin-bottom:12px;flex-wrap:wrap;">' + '<div style="flex:1;min-width:110px;padding:12px;background:var(--bg-secondary);border-radius:8px;text-align:center;">' + '<div style="font-size:11px;color:var(--text-tertiary);">总输入</div>' + '<div style="font-size:18px;font-weight:600;color:var(--accent);">' + formatNumber(totalInput) + '</div>' + '</div>' + '<div style="flex:1;min-width:110px;padding:12px;background:var(--bg-secondary);border-radius:8px;text-align:center;">' + '<div style="font-size:11px;color:var(--text-tertiary);">总输出</div>' + '<div style="font-size:18px;font-weight:600;color:var(--accent);">' + formatNumber(totalOutput) + '</div>' + '</div>' + '<div style="flex:1;min-width:110px;padding:12px;background:var(--bg-secondary);border-radius:8px;text-align:center;">' + '<div style="font-size:11px;color:var(--text-tertiary);">缓存命中</div>' + '<div style="font-size:18px;font-weight:600;color:var(--accent);">' + formatNumber(totalCache) + '</div>' + '</div>' + '<div style="flex:1;min-width:110px;padding:12px;background:var(--bg-secondary);border-radius:8px;text-align:center;">' + '<div style="font-size:11px;color:var(--text-tertiary);">总计</div>' + '<div style="font-size:18px;font-weight:600;color:var(--accent);">' + formatNumber(totalTokens) + '</div>' + '</div>' + '</div>' + '</div>' + '<table style="width:100%;border-collapse:collapse;font-size:13px;">' + '<thead>' + '<tr style="border-bottom:2px solid var(--separator);">' + '<th style="padding:8px;text-align:left;font-size:12px;color:var(--text-tertiary);">员工</th>' + '<th style="padding:8px;text-align:right;font-size:12px;color:var(--text-tertiary);">输入</th>' + '<th style="padding:8px;text-align:right;font-size:12px;color:var(--text-tertiary);">输出</th>' + '<th style="padding:8px;text-align:right;font-size:12px;color:var(--text-tertiary);">缓存</th>' + '<th style="padding:8px;text-align:right;font-size:12px;color:var(--text-tertiary);">总计</th>' + '</tr>' + '</thead>' + '<tbody>' + rows + '</tbody>' + '</table>';
  } catch (e) {
    console.warn('[renderTokenStats] failed:', e);
    content.innerHTML = '<div style="padding:20px;text-align:center;color:var(--danger);">加载失败</div>';
  }
}

// ========== 渠道配置面板 ==========
function showChannelConfig() {
  var panel = document.getElementById('channelConfigPanel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'channelConfigPanel';
    panel.className = 'modal-overlay';
    panel.innerHTML = '<div class="modal" style="width:90%;max-width:500px;">' + '<div class="modal-header">' + '<h3>🔌 渠道配置</h3>' + '<button class="modal-close" onclick="closeChannelConfig()">×</button>' + '</div>' + '<div style="padding:16px;">' + '<div style="margin-bottom:16px;">' + '<label style="display:block;font-size:12px;color:var(--text-tertiary);margin-bottom:4px;">AI 提供商</label>' + '<select id="globalAIProvider" style="width:100%;padding:8px;border:1px solid var(--separator);border-radius:8px;background:var(--bg-tertiary);color:var(--text);">' + '<option value="kimi">Kimi</option>' + '<option value="openai">OpenAI</option>' + '<option value="anthropic">Anthropic</option>' + '<option value="deepseek">DeepSeek</option>' + '<option value="qwen">通义千问</option>' + '</select>' + '</div>' + '<div style="margin-bottom:16px;">' + '<label style="display:block;font-size:12px;color:var(--text-tertiary);margin-bottom:4px;">默认模型</label>' + '<select id="globalModel" style="width:100%;padding:8px;border:1px solid var(--separator);border-radius:8px;background:var(--bg-tertiary);color:var(--text);">' + '<option value="gpt-4o">GPT-4o</option>' + '<option value="claude-3.5-sonnet">Claude 3.5 Sonnet</option>' + '<option value="deepseek-v3">DeepSeek V3</option>' + '<option value="kimi-k2">Kimi K2</option>' + '<option value="qwen-max">Qwen Max</option>' + '</select>' + '</div>' + '<div style="margin-bottom:16px;">' + '<label style="display:block;font-size:12px;color:var(--text-tertiary);margin-bottom:4px;">API Key</label>' + '<input type="password" id="globalApiKey" placeholder="输入全局 API Key" style="width:100%;padding:8px;border:1px solid var(--separator);border-radius:8px;background:var(--bg-tertiary);color:var(--text);font-size:13px;">' + '</div>' + '<div style="margin-bottom:16px;">' + '<label style="display:block;font-size:12px;color:var(--text-tertiary);margin-bottom:4px;">Gateway URL</label>' + '<input type="text" id="globalGatewayUrl" placeholder="ws://localhost:8080" style="width:100%;padding:8px;border:1px solid var(--separator);border-radius:8px;background:var(--bg-tertiary);color:var(--text);font-size:13px;">' + '</div>' + '<div style="display:flex;gap:8px;">' + '<button class="btn btn-primary" onclick="saveChannelConfig()" style="flex:1;">保存配置</button>' + '<button class="btn btn-secondary" onclick="closeChannelConfig()">取消</button>' + '</div>' + '</div>' + '</div>';
    document.body.appendChild(panel);
  }

  // 加载已有配置
  var config = JSON.parse(localStorage.getItem('sb_channel_config') || '{}');
  var providerEl = document.getElementById('globalAIProvider');
  var modelEl = document.getElementById('globalModel');
  var keyEl = document.getElementById('globalApiKey');
  var urlEl = document.getElementById('globalGatewayUrl');
  if (providerEl) providerEl.value = config.provider || 'kimi';
  if (modelEl) modelEl.value = config.model || 'gpt-4o';
  if (keyEl) keyEl.value = config.apiKey || '';
  if (urlEl) urlEl.value = config.gatewayUrl || '';
  panel.classList.add('open');
}
function closeChannelConfig() {
  var panel = document.getElementById('channelConfigPanel');
  if (panel) panel.classList.remove('open');
}
function closeChannelConfigModal() {
  var modal = document.getElementById('channelConfigModal');
  if (modal) modal.style.display = 'none';
}
function saveChannelConfig() {
  var provider = document.getElementById('globalAIProvider').value;
  var model = document.getElementById('globalModel').value;
  var apiKey = document.getElementById('globalApiKey').value.trim();
  var gatewayUrl = document.getElementById('globalGatewayUrl').value.trim();
  var config = {
    provider: provider,
    model: model,
    apiKey: apiKey,
    gatewayUrl: gatewayUrl,
    updatedAt: Date.now()
  };
  localStorage.setItem('sb_channel_config', JSON.stringify(config));
  showToast('✅ 渠道配置已保存');
  closeChannelConfig();
}

// 获取全局渠道配置（员工创建时使用）
function getChannelConfig() {
  return JSON.parse(localStorage.getItem('sb_channel_config') || '{}');
}

// ========== 侧边栏宽度拖拽调整 ==========
var _SIDEBAR_WIDTH_KEY = 'sidebarWidth';
var _MIN_SIDEBAR_WIDTH = 240;
var _MAX_SIDEBAR_WIDTH = 600;
var _DEFAULT_SIDEBAR_WIDTH = 280;

function getSidebarWidth() {
  var saved = localStorage.getItem(_SIDEBAR_WIDTH_KEY);
  var w = saved ? parseInt(saved, 10) : _DEFAULT_SIDEBAR_WIDTH;
  if (isNaN(w) || w < _MIN_SIDEBAR_WIDTH) w = _MIN_SIDEBAR_WIDTH;
  if (w > _MAX_SIDEBAR_WIDTH) w = _MAX_SIDEBAR_WIDTH;
  return w;
}

function getAllSidebars() {
  return [
    document.querySelector('.app-sidebar'),
    document.getElementById('productsMidList'),
    document.getElementById('influencersMid'),
    document.getElementById('tasksMidList')
  ].filter(Boolean);
}

function applySidebarWidth(width) {
  width = Math.max(_MIN_SIDEBAR_WIDTH, Math.min(_MAX_SIDEBAR_WIDTH, width));
  getAllSidebars().forEach(function(sb) {
    sb.style.width = width + 'px';
    sb.style.flex = '0 0 auto';
  });
}

// fix/responsive-shell-collapse-r3: 兜底 JS-driven 响应式 shell
// 背景: 老大复验发现 r2 (≤768 .left-nav 改底部 tab bar) 在达人库页生效,
// 对话页 .left-nav 仍是 56px 左侧栏, CSS 静态分析(r2 已带 !important)
// 仍无法定位. 用 JS 直接 inline 强写 .left-nav 样式, 跳过 CSS @media,
// 任何微信 WebView / 浏览器 CSS 解析异常都拦截.
// 触发: load(resize 兼容旋转), resize, switchModule 后, 保证模块切换也重排.
function forceResponsiveShell() {
  var leftNav = document.querySelector('.left-nav');
  if (!leftNav) return;
  var appContainer = document.querySelector('.app-container');
  var appMain = document.querySelector('.app-main');
  var navTop = leftNav.querySelector('.left-nav-top');
  var navBottom = leftNav.querySelector('.left-nav-bottom');
  var navItems = leftNav.querySelectorAll('.nav-item');
  var navItemSvgs = leftNav.querySelectorAll('.nav-item svg');
  var isMobile = window.innerWidth <= 768;

  // 保留的属性白名单(switchModule/applySidebarWidth 可能改 .left-nav 个别属性,
  // 但本函数仅在 ≤768/≥769 边界改这些字段,其他不动)
  if (isMobile) {
    leftNav.style.position = 'fixed';
    leftNav.style.left = '0';
    leftNav.style.right = '0';
    leftNav.style.bottom = '0';
    leftNav.style.width = '100%';
    leftNav.style.height = '56px';
    leftNav.style.flexDirection = 'row';
    leftNav.style.justifyContent = 'space-around';
    leftNav.style.padding = '0 4px';
    leftNav.style.borderRight = 'none';
    leftNav.style.borderTop = '0.5px solid var(--separator)';
    leftNav.style.zIndex = '300';
    if (navTop) {
      navTop.style.flexDirection = 'row';
      navTop.style.gap = '0';
      navTop.style.flex = '1';
      navTop.style.justifyContent = 'space-around';
      navTop.style.padding = '0 4px';
    }
    if (navBottom) navBottom.style.display = 'none';
    for (var i = 0; i < navItems.length; i++) {
      var item = navItems[i];
      var w = window.innerWidth <= 480 ? '36px' : '32px';
      item.style.width = w;
      item.style.height = w;
      item.style.borderRadius = '8px';
      item.style.flexShrink = '0';
      // ::before indicator 由 CSS 控制(不在这里覆盖, 避免内联 !important 污染)
    }
    for (var k = 0; k < navItemSvgs.length; k++) {
      navItemSvgs[k].style.width = '18px';
      navItemSvgs[k].style.height = '18px';
    }
    // 主区让出底部 56px
    if (appContainer) appContainer.style.paddingBottom = '56px';
    if (appMain) appMain.style.paddingBottom = '56px';
  } else {
    // 桌面端: 清掉 inline style 恢复 r2 之前状态(让 base CSS 接管)
    leftNav.style.position = '';
    leftNav.style.left = '';
    leftNav.style.right = '';
    leftNav.style.bottom = '';
    leftNav.style.width = '';
    leftNav.style.height = '';
    leftNav.style.flexDirection = '';
    leftNav.style.justifyContent = '';
    leftNav.style.padding = '';
    leftNav.style.borderRight = '';
    leftNav.style.borderTop = '';
    leftNav.style.zIndex = '';
    if (navTop) {
      navTop.style.flexDirection = '';
      navTop.style.gap = '';
      navTop.style.flex = '';
      navTop.style.justifyContent = '';
      navTop.style.padding = '';
    }
    if (navBottom) navBottom.style.display = '';
    for (var j = 0; j < navItems.length; j++) {
      navItems[j].style.width = '';
      navItems[j].style.height = '';
      navItems[j].style.borderRadius = '';
      navItems[j].style.flexShrink = '';
    }
    for (var m = 0; m < navItemSvgs.length; m++) {
      navItemSvgs[m].style.width = '';
      navItemSvgs[m].style.height = '';
    }
    if (appContainer) appContainer.style.paddingBottom = '';
    if (appMain) appMain.style.paddingBottom = '';
  }
}

function initSidebarResizers() {
  getAllSidebars().forEach(function(sb) {
    if (sb.querySelector('.sidebar-resizer')) return;
    sb.style.position = 'relative';
    var resizer = document.createElement('div');
    resizer.className = 'sidebar-resizer';
    resizer.addEventListener('mousedown', function(e) {
      e.preventDefault();
      startSidebarResize(e, sb);
    });
    sb.appendChild(resizer);
  });
}

function startSidebarResize(e, sb) {
  var startX = e.clientX;
  var startWidth = sb.offsetWidth;
  var resizer = sb.querySelector('.sidebar-resizer');
  if (resizer) resizer.classList.add('dragging');
  document.body.style.userSelect = 'none';
  document.body.style.cursor = 'col-resize';

  function onMouseMove(ev) {
    var newWidth = startWidth + (ev.clientX - startX);
    applySidebarWidth(newWidth);
  }

  function onMouseUp(ev) {
    document.removeEventListener('mousemove', onMouseMove);
    document.removeEventListener('mouseup', onMouseUp);
    document.body.style.userSelect = '';
    document.body.style.cursor = '';
    if (resizer) resizer.classList.remove('dragging');
    localStorage.setItem(_SIDEBAR_WIDTH_KEY, String(sb.offsetWidth));
  }

  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
}

// ========== Feishu Three-Column Module Switching ==========
var currentModule = 'messages';
var _globalSearchScope = 'all';
var _globalSearchTimer = null;

/* ============================================================
 * sb2-knowledge MVP1: 数据接线层
 * - 调 /api/knowledge/categories + entries + stats (接口签名零改动)
 * - 渲染 .sb2-knowledge-main (DOM 在 <main class="app-main"> 内注入)
 * - 旧 knowledgeMid 系列 CSS 兜底 display:none
 * ============================================================ */
var _sb2KbCats = null;
var _sb2KbEntries = [];
var _sb2KbActiveCat = 'all';
var _sb2KbActiveScope = 'all';
/* 〔fix/sb2-side-restore 24 轮批注② v2 2026-10-06 12:54〕老大拍板「保分页但每页不滚动」:
   — 12:31 首修误判为「分页器下线回自然滚动」, 老大 12:54 纠正口径: 保分页, 但每页装满视口不滚动
   — 页尺寸 = f(视口) 动态计算 (_sb2KbPageSize): 主区可视高 - padding - hero - chips - pager预留,
     按真实卡片高度 + gap 取整; resize 200ms 防抖重算重渲 (只重渲, 不重拉)
   — 全量 _sb2KbEntries 仍循环拉 limit=100&offset=N 至全 (分页纯前端切片) */
var _sb2KbPage = 1;
function _sb2KbPageSize(){
  var main = document.getElementById('sb2KnowledgeMain');
  if (!main) return 10;
  var mcs = getComputedStyle(main);
  var padV = (parseFloat(mcs.paddingTop) || 0) + (parseFloat(mcs.paddingBottom) || 0);
  var hero = main.querySelector('.sb2-kb-hero');
  var chips = document.getElementById('sb2KbChips');
  var card = document.querySelector('#sb2KbList .sb2-kb-card');
  var pitch = card ? (card.offsetHeight + 10) : 82; /* gap 10 (.sb2-kb-list) */
  var reserved = padV + (hero ? hero.offsetHeight : 52) + (chips ? chips.offsetHeight : 26) + 60; /* 60 = pager 段 ~46 + 安全余量 14 (防长标题卡片换行) */
  var avail = main.clientHeight - reserved;
  return Math.max(1, Math.floor(avail / pitch));
}
/* 〔fix/sb2-side-restore 22 轮批注①〕init 标志位 (防 sb2KbSubmit/sb2KbDeleteEntry 调 sb2KbInit 重入时 hero 闪烁)
   — 初始 false, sb2KbInit 末尾置 true
   — 预留后续 hooks 守卫用 (现未消费, 文档先行) */
var _sb2KbInit = false;

async function sb2KbInit(){
  try {
    var sub = document.getElementById('sb2KbHeroSub');
    if (sub) sub.textContent = '载入中…';
    var list = document.getElementById('sb2KbList');
    if (list) list.innerHTML = '<div class="sb2-kb-empty">载入中…</div>';

    // ★ MVP3 撤硬编码: 走 apiFetch 纯 Bearer (admin token 在 prod 已验证返 migrated kbmig_* 全量数据)
    //   之前 X-Agent-Id: knowledge_admin 是工作库 DB 快照较旧导致的临时绕路, 硬编码 agent 身份换环境会埋雷
    /* 〔fix/sb2-side-restore 22 轮批注②〕全量拉取 (前端分页前置):
       — cats + stats 并行先跑 (stats.total 决定 entries 循环终止条件)
       — entries 循环 limit=100&offset=N (server 硬顶 100, 接口契约零改动) 至累积 >= stats.total
       — 兼容兜底: stats.total 缺失 → 返回条数 < 100 即停
       — 合并 docs 进 _sb2KbEntries (跟 r19 卡片渲染 + r19 chips 现场聚合共用同一份全量, 不造第二份)
       — 服务端契约零改动, 纯 client 循环 */
    var catsP = apiFetch('/api/knowledge/categories').then(function(r){return r.json();}).catch(function(){return {categories:[]};});
    var statsP = apiFetch('/api/knowledge/stats').then(function(r){return r.json();}).catch(function(){return {};});
    var [catsData, statsData] = await Promise.all([catsP, statsP]);
    _sb2KbCats = (catsData && catsData.categories) || [];
    var statsRoot = (statsData && statsData.stats) ? statsData.stats : statsData;
    var _kbExpectedTotal = (typeof statsRoot.total === 'number') ? statsRoot.total : null;
    var _kbFetchPageSize = 100;
    var allDocs = [];
    while (true) {
      var _off = allDocs.length;
      var _r = await apiFetch('/api/knowledge/entries?limit=' + _kbFetchPageSize + '&offset=' + _off).catch(function(){return null;});
      var _b = _r ? await _r.json().catch(function(){return {};}) : {};
      var _d = (_b && (_b.docs || _b.entries)) || [];
      allDocs = allDocs.concat(_d);
      if (_d.length < _kbFetchPageSize) break;
      if (_kbExpectedTotal !== null && allDocs.length >= _kbExpectedTotal) break;
      if (typeof _b.total === 'number' && allDocs.length >= _b.total) break;
    }
    _sb2KbEntries = allDocs;
    /* 〔24 轮批注② v2〕拉取后回第 1 页 + resize 防抖重算页尺寸 (每页不滚动口径的动态分页) */
    _sb2KbPage = 1;
    if (!window._sb2KbResizeBound) {
      window._sb2KbResizeBound = true;
      var _sb2KbRsT = null;
      window.addEventListener('resize', function(){
        clearTimeout(_sb2KbRsT);
        _sb2KbRsT = setTimeout(function(){
          if (document.getElementById('sb2KnowledgeMain') && _sb2KbEntries.length && typeof _sb2KbDoFilter === 'function') _sb2KbDoFilter();
        }, 200);
      });
    }

    /* 〔fix/sb2-side-restore commit 16+17〕16 轮批注③打回链修正 (真数据链路):
       — commit 16 改接 _sb2KbCats (sb2 loader 真路径, 整链路 trace)
       — commit 17 修正 server 字段名 (老大 18:25 trace 实测):
         kb_entry_stats 实际只返 {total, byScope, byCategory, pendingChunks}, 无 total_entries / total_chunks
         — 文档数: stats.total (server 真字段, 修前 fallback entries.length 是 50 样本有偏)
         — chunks 数: stats.totalChunks (server 真字段, commit 17 加的, 不用 byCategory 求和)
       副标 = total + ' 条 · ' + totalChunks + ' chunks' (跟 sb2KbRenderHero line 40361 同文案)
       时序重渲挂在 sb2KbInit 三个 render 调用之后, _sb2KbInit=true 前 (不是 loadKnowledge) */
    /* statsRoot 已在 22 轮批注②循环拉取前置处定义 (line ~40523), 复用同 var 不重声明 */
    /* 文档总数: stats.total (server kb_entry_stats 真字段, commit 17 server 加的跨端兼容老客户端) */
    var totalEntries = (typeof statsRoot.total === 'number') ? statsRoot.total : _sb2KbEntries.length;
    /* chunks 总数: stats.totalChunks (server kb_entry_stats 真字段, commit 17 加的,
       不用 byCategory 求和 — byCategory.count 是分类文档数不是 chunks, P1 教训字段名假设) */
    var totalChunks = (typeof statsRoot.totalChunks === 'number') ? statsRoot.totalChunks : 0;
    var catCounts = {};
    if (Array.isArray(statsRoot.byCategory)) {
      statsRoot.byCategory.forEach(function(c){
        if (c && typeof c.name === 'string') catCounts[String(c.name)] = Number(c.count) || 0;
      });
    }
    /* 保留 byCategory 给 SB2_SIDE_RENDERERS.knowledge 副标「N 条 · M chunks」共享 */
    window._sb2KbStats = window._sb2KbStats || {};
    window._sb2KbStats.byCategory = statsRoot.byCategory || [];
    window._sb2KbStats.total = totalEntries;
    window._sb2KbStats.totalChunks = totalChunks;
    window._sb2KbCatCounts = catCounts;
    window._sb2KbCatCountsTotal = totalEntries;

    /* 〔fix/sb2-side-restore commit 18〕16 轮批注③第四次打回 — hero 嵌套 bug:
       修前 bug: 传 statsData (原始响应 {stats:{...}}), 函数读 stats.total 拿 undefined → fallback 50 样本 + chunks 0
         侧栏对是因为用 statsRoot (line 40317 已解 statsData.stats), 但 hero 漏改调用处
       修法: 改传 statsRoot (同 try 块已定义, 跟侧栏三处共用) */
    sb2KbRenderHero(statsRoot || {}, _sb2KbEntries);
    sb2KbRenderChips(_sb2KbCats, _sb2KbEntries);
    sb2KbRenderList(_sb2KbEntries);
    /* 〔fix/sb2-side-restore 22 轮批注①〕初始化 hero 标题 (默认态 = 「全部知识」)
       — sb2KbInit 末尾 _sb2KbInit=true 前调一次 (spec line 16)
       — _sb2KbInit 标志位: 防 init 重入 (现有 sb2KbSubmit / sb2KbDeleteEntry 调 sb2KbInit 重复触发时, 跳过 hero re-render 防闪烁) */
    _sb2KbInit = true;
    sb2KbUpdateHeroTitle();

    /* 〔fix/sb2-side-restore commit 16〕时序重渲 (不是 loadKnowledge, 是 sb2KbInit 三个 render 后):
       — 复用既有 window.renderSideFor (line 45979, 已挂 window 跨块访问)
       — 触发条件: _sb2KbCats/_sb2KbEntries/stats 三件后置 + 三个 render 调用后
       — 不造第二个渲染入口 (老大红线: 别造第二个渲染入口)
       — 守卫写法 (&& fn) 防跨块 undefined 静默 */
    if (typeof window.renderSideFor === 'function') window.renderSideFor('knowledge');
  } catch (e) {
    console.error('[sb2-kb] init failed:', e);
    var list2 = document.getElementById('sb2KbList');
    if (list2) list2.innerHTML = '<div class="sb2-kb-empty">载入失败, 请刷新重试</div>';
  }
}

function sb2KbRenderHero(stats, entries){
  /* 〔fix/sb2-side-restore commit 17〕16 轮批注③第三次打回 — 同款 bug 一起修:
     修前 bug: 我 commit 16 凭印象写了 stats.total_entries / stats.total_chunks,
       server kb_entry_stats 实际只返 total / totalChunks / byScope / byCategory / pendingChunks
       → total 走 fallback entries.length (50 样本有偏), chunks 永远 0
     修法 (老大 18:25 拍板):
       — stats.total (server 真字段, 不是 total_entries)
       — stats.totalChunks (server 真字段, commit 17 加的, 不是 total_chunks)
       — fallback entries.length 保留 (极端 case server 字段缺失时仍能显示) */
  var sub = document.getElementById('sb2KbHeroSub');
  if (!sub) return;
  var total = (stats && stats.total) || entries.length || 0;
  var chunks = (stats && stats.totalChunks) || 0;
  sub.textContent = total + ' 条 · ' + chunks + ' chunks · 语义检索已切换';
}

/* 〔fix/sb2-side-restore 22 轮批注①〕hero 标题动态化 (方案 A)
   — 老大拍板: hero 跟随 _sb2KbActiveCat 动态变化 (侧栏 label「知识库」模块名 + hero 内容名 语义错开)
   — 纯 DOM textContent 更新, 不造第二个渲染入口
   — 复用点 (写明避免后人漏挂):
     ① sb2KbInit 末尾 (初始化默认态 = 全部知识)
     ② sb2KbRenderChips click handler (chips 选中切换后挂)
     ③ 侧栏回调 SB2_SIDE_RENDERERS.knowledge 走到 switchKnowledgeCategory(...) 时也可调 (当前 switchKnowledgeCategory 未定义, 不阻塞)
   — 标题规则:
     * _sb2KbActiveCat === 'all' → '全部知识' (无英文 "All", 跟原版中文界面一致)
     * 其他 → 取 _sb2KbCats 真字段 name (扁平树走 c.name, 树状递归 children.name 同样拿到)
     * 兜底: _sb2KbActiveCat 原值 (避免分类被删除后 hero 显示 undefined) */
function sb2KbUpdateHeroTitle(){
  var hero = document.getElementById('sb2KbHeroTitle');
  if (!hero) return;
  if (_sb2KbActiveCat === 'all') {
    hero.textContent = '全部知识';
    return;
  }
  /* 兼容 chips / 侧栏两路传入:
     — chips 选中后 _sb2KbActiveCat = data-sb2-kb-cat attribute (catMap 的 cname 字符串)
     — 侧栏传入的是 c.id (kb_category_tree 真字段, 字符串/数字混合, 见 line 46535)
     — 用 catId → name 反查: _sb2KbCats 树状递归找 c.id === catId, 取 c.name */
  var name = null;
  var _cats = (Array.isArray(_sb2KbCats)) ? _sb2KbCats : null;
  function _walk(arr){
    (arr || []).forEach(function(c){
      if (name) return;
      if (c && (String(c.id) === String(_sb2KbActiveCat) || (c.name && c.name === _sb2KbActiveCat))) {
        name = (c.name || c.display_name || '').trim();
      }
      if (Array.isArray(c && c.children) && c.children.length) _walk(c.children);
    });
  }
  if (_cats) _walk(_cats);
  /* ★ r66 批注① 老大 15:30「为什么是英文」: 后端 KB 分类里有 legacy_migration 等英文 key,
     hero 直接渲染 server 返回的 c.name 会显示英文。跟 inline-09.js _sb2ReviewCatLabel 同款人话映射
     (字段名铁律 r39-5: 后端英文 key 兜底中文标签, 不让用户接触开发黑话) */
  var _catLabelMap = {
    'legacy_migration': '历史迁移',
    '达人库': '达人库',
    '公共知识': '公共知识',
    '商品库': '商品库',
    '流量知识': '流量知识',
    'personal': '个人',
    'team': '团队'
  };
  var display = (name && _catLabelMap[name]) ? _catLabelMap[name] : name;
  hero.textContent = display || _catLabelMap[_sb2KbActiveCat] || _sb2KbActiveCat || '全部知识';
}

function sb2KbRenderChips(cats, entries){
  var el = document.getElementById('sb2KbChips');
  if (!el) return;

  // ★ MVP2: chips 从 entries 现场聚合 (分类 + scope 两维), 保证 chips 跟 list 一致不出幽灵分类
  var catMap = {}; // category → count
  var scopeMap = {}; // scope → count
  entries.forEach(function(e){
    var c = e.category || e.category_name || '未分类';
    catMap[c] = (catMap[c] || 0) + 1;
    var s = e.scope || e.scope_name || 'global';
    scopeMap[s] = (scopeMap[s] || 0) + 1;
  });

  var total = entries.length;
  var catHtml = '<button class="sb2-kb-chip-row-label">分类</button>' +
    '<button class="sb2-kb-chip active" data-sb2-kb-cat="all">全部 · ' + total + '</button>';
  Object.keys(catMap).sort().forEach(function(cname){
    catHtml += '<button class="sb2-kb-chip" data-sb2-kb-cat="' + escapeAttr(cname) + '">' + escapeHtml(cname) + ' · ' + catMap[cname] + '</button>';
  });

  var scopeHtml = '<button class="sb2-kb-chip-row-label">范围</button>' +
    '<button class="sb2-kb-chip' + (_sb2KbActiveScope === 'all' ? ' active' : '') + '" data-sb2-kb-scope="all">全部 · ' + total + '</button>';
  Object.keys(scopeMap).sort().forEach(function(s){
    scopeHtml += '<button class="sb2-kb-chip' + (_sb2KbActiveScope === s ? ' active' : '') + '" data-sb2-kb-scope="' + escapeAttr(s) + '">' + escapeHtml(_sb2ScopeLabel(s)) + ' · ' + scopeMap[s] + '</button>';
  });

  /* 〔fix/sb2-side-restore 19 轮批注②〕chips 改单行对齐原型 line 544
     修前 (commit 13): catHtml + scopeHtml 分两个 chip-row div → 双行布局
     修后 (本轮): 合并到一个 chip-row div (中间加 separator "·" 灰阶 弱化视觉分组, 跟 prototype line 765-766 单行平铺一致) */
  el.innerHTML = '<div class="sb2-kb-chip-row">' + catHtml
               + '<span class="sb2-kb-chip-row-sep" aria-hidden="true">·</span>'
               + scopeHtml + '</div>';

  if (!el._sb2KbBound) {
    el.addEventListener('click', function(ev){
      var catBtn = ev.target.closest('[data-sb2-kb-cat]');
      var scopeBtn = ev.target.closest('[data-sb2-kb-scope]');
      if (catBtn) {
        _sb2KbActiveCat = catBtn.getAttribute('data-sb2-kb-cat') || 'all';
        el.querySelectorAll('[data-sb2-kb-cat]').forEach(function(b){ b.classList.remove('active'); });
        catBtn.classList.add('active');
        sb2KbFilterList();
        /* 〔fix/sb2-side-restore 22 轮批注①〕hero 标题跟随选中分类动态化
           — 复用既有 chips click handler (不造第二个渲染入口)
           — sb2KbUpdateHeroTitle 读 _sb2KbActiveCat 真值 → 反查 _sb2KbCats name */
        sb2KbUpdateHeroTitle();
      } else if (scopeBtn) {
        _sb2KbActiveScope = scopeBtn.getAttribute('data-sb2-kb-scope') || 'all';
        el.querySelectorAll('[data-sb2-kb-scope]').forEach(function(b){ b.classList.remove('active'); });
        scopeBtn.classList.add('active');
        sb2KbFilterList();
      }
    });
    el._sb2KbBound = true;
  }
  /* 〔24 轮批注② v2〕分页按钮委托 (恢复自 22 轮, 页尺寸改走 _sb2KbPageSize 动态值):
     — 委托到 #sb2KbList 容器 (pager 渲染在容器内), _sb2KbPagerBound flag 防重绑
     — 切页走 _sb2KbDoFilter() 跳过重置 (chips/scope 切换才回第 1 页) */
  var listEl = document.getElementById('sb2KbList');
  if (listEl && !listEl._sb2KbPagerBound) {
    listEl.addEventListener('click', function(ev){
      var btn = ev.target.closest('[data-sb2-kb-page-go]');
      if (!btn || btn.disabled) return;
      var go = btn.getAttribute('data-sb2-kb-page-go');
      var target = _sb2KbPage;
      if (go === 'prev') target = _sb2KbPage - 1;
      else if (go === 'next') target = _sb2KbPage + 1;
      else target = parseInt(go, 10) || 1;
      if (target === _sb2KbPage) return;
      _sb2KbPage = target;
      _sb2KbDoFilter();
    });
    listEl._sb2KbPagerBound = true;
  }
}

/* 〔fix/sb2-side-restore 24 轮批注③〕侧栏「分类」组点不动修复:
   修前 bug: 侧栏 renderer onClick 写「switchKnowledgeCategory && switchKnowledgeCategory(...)」
     但全仓无此函数定义 → 守卫写法静默 no-op, 老大 24 轮批注③「依旧点不动」
   修法: 复用主区 chips 同一套过滤状态 (_sb2KbActiveCat + sb2KbFilterList + sb2KbUpdateHeroTitle),
     不造第二渲染入口; chips active 态同步点亮 (跟 chips click handler 同一套视觉)
   口径: 侧栏传 c.id, chips 过滤按 category 名字符串 → id → name 反查 _sb2KbCats (kb_category_tree 数据源)
   侧栏 active 高亮: onclick 字面量匹配 (renderSideFor 重渲会重建 DOM, 直接类切换最稳) */
function switchKnowledgeCategory(idOrName){
  var name = (idOrName === '' || idOrName == null) ? 'all' : idOrName;
  if (name !== 'all' && Array.isArray(_sb2KbCats)) {
    for (var i = 0; i < _sb2KbCats.length; i++) {
      var _c = _sb2KbCats[i];
      if (_c && String(_c.id) === String(idOrName)) { name = _c.name || _c.category || idOrName; break; }
    }
  }
  _sb2KbActiveCat = String(name);
  document.querySelectorAll('#sb2KbChips [data-sb2-kb-cat]').forEach(function(b){
    b.classList.toggle('active', (b.getAttribute('data-sb2-kb-cat') || '') === _sb2KbActiveCat);
  });
  sb2KbFilterList();
  if (typeof sb2KbUpdateHeroTitle === 'function') sb2KbUpdateHeroTitle();
  document.querySelectorAll('.sb2-side--module-knowledge .sb2-side-item').forEach(function(it){
    var _oc = it.getAttribute('onclick') || '';
    it.classList.toggle('active', _oc.indexOf("switchKnowledgeCategory('" + idOrName + "')") >= 0);
  });
}

/* 〔fix/sb2-side-restore 24 轮批注③〕侧栏「范围」组同款修复 (switchKnowledgeScope 同样全仓未定义)
   口径: 侧栏 'global'/'mine'/'project' 跟 chips 的 data-sb2-kb-scope 同一状态 _sb2KbActiveScope */
function switchKnowledgeScope(scope){
  _sb2KbActiveScope = scope || 'all';
  document.querySelectorAll('#sb2KbChips [data-sb2-kb-scope]').forEach(function(b){
    b.classList.toggle('active', (b.getAttribute('data-sb2-kb-scope') || '') === _sb2KbActiveScope);
  });
  sb2KbFilterList();
  document.querySelectorAll('.sb2-side--module-knowledge .sb2-side-item').forEach(function(it){
    var _oc = it.getAttribute('onclick') || '';
    it.classList.toggle('active', _oc.indexOf("switchKnowledgeScope('" + _sb2KbActiveScope + "')") >= 0);
  });
}

function sb2KbFilterList(){
  /* 〔24 轮批注② v2〕chips / scope / 侧栏切换 → 回第 1 页 (pager 切页走 _sb2KbDoFilter 跳过重置) */
  _sb2KbPage = 1;
  _sb2KbDoFilter();
}

/* 〔fix/sb2-side-restore 22 轮批注②〕pager 切页专用 filter (不重置 page):
   — 走 _sb2KbActiveCat/_sb2KbActiveScope 当前值过滤 → sb2KbRenderList 渲染
   — 抽出来防 sb2KbFilterList 重置覆盖 pager 设的 page (修前 bug: pager 切页被 filter 重置回 1) */
function _sb2KbDoFilter(){
  var byCat = (_sb2KbActiveCat === 'all') ? _sb2KbEntries : _sb2KbEntries.filter(function(e){
    return (e.category || e.category_name || '') === _sb2KbActiveCat;
  });
  var byScope = (_sb2KbActiveScope === 'all') ? byCat : byCat.filter(function(e){
    return (e.scope || e.scope_name || 'global') === _sb2KbActiveScope;
  });
  sb2KbRenderList(byScope);
}

/* 〔24 轮批注② v2〕_sb2KbBuildPager 恢复 (页尺寸 = _sb2KbPageSize() 动态值):
   — 规则: 总页 ≤ 7 全显; 超出 → 首 (1) + 尾 (N) + 当前 ±1 + 省略号
   — 风格 token 同 chips (12px / 500 / r-sm / s1 底 / ink 选中)
   — 总页 1: 不渲染 */
function _sb2KbBuildPager(totalItems){
  var pageSize = _sb2KbPageSize();
  var totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  if (totalPages <= 1) return '';
  var cur = Math.min(Math.max(1, _sb2KbPage || 1), totalPages);
  var pages = [];
  if (totalPages <= 7) {
    for (var i = 1; i <= totalPages; i++) pages.push(i);
  } else {
    var set = {};
    [1, totalPages, cur - 1, cur, cur + 1].forEach(function(p){
      if (p >= 1 && p <= totalPages) set[p] = true;
    });
    var prev = 0;
    Object.keys(set).map(Number).sort(function(a,b){return a-b;}).forEach(function(p){
      if (prev && p - prev > 1) pages.push('…');
      pages.push(p);
      prev = p;
    });
  }
  var prevDisabled = cur <= 1 ? ' disabled' : '';
  var nextDisabled = cur >= totalPages ? ' disabled' : '';
  var html = '<nav class="sb2-kb-pager" aria-label="知识库分页">';
  html += '<button class="sb2-kb-pager-btn" data-sb2-kb-page-go="prev"' + prevDisabled + ' aria-label="上一页">‹</button>';
  pages.forEach(function(p){
    if (p === '…') {
      html += '<span class="sb2-kb-pager-ellipsis" aria-hidden="true">…</span>';
    } else {
      var activeCls = (p === cur) ? ' active' : '';
      html += '<button class="sb2-kb-pager-btn' + activeCls + '" data-sb2-kb-page-go="' + p + '" aria-label="第 ' + p + ' 页" aria-current="' + (p === cur ? 'page' : 'false') + '">' + p + '</button>';
    }
  });
  html += '<button class="sb2-kb-pager-btn" data-sb2-kb-page-go="next"' + nextDisabled + ' aria-label="下一页">›</button>';
  html += '</nav>';
  return html;
}

/* 〔fix/sb2-side-restore 24 轮批注② v2〕sb2KbRenderList 恢复分页切片:
   — 页尺寸动态 (_sb2KbPageSize), 每页装满视口不滚动
   — 卡片模板不动 (DOM 跟 r19 水平布局一致) */
function sb2KbRenderList(entries){
  var el = document.getElementById('sb2KbList');
  if (!el) return;
  if (!entries || entries.length === 0) {
    el.innerHTML = '<div class="sb2-kb-empty">知识库当前为空, 点击右上「+ 新建知识」开始录入</div>';
    return;
  }
  var pageSize = _sb2KbPageSize();
  var cur = Math.min(Math.max(1, _sb2KbPage || 1), Math.max(1, Math.ceil(entries.length / pageSize)));
  if (_sb2KbPage !== cur) _sb2KbPage = cur;
  var pageSlice = entries.slice((cur - 1) * pageSize, cur * pageSize);
  var cardsHtml = pageSlice.map(function(e){
    var title = e.title || '(无标题)';
    var id = e.id || '';
    var chunks = ((e.chunk_count != null ? e.chunk_count : 0)) + ' chunks';
    /* 〔fix/sb2-side-restore commit 13〕16 轮批注⑤: 知识库条目卡片对齐原版
       原型 line 767 meta 格式: 'kb_xxxx · N chunks · 更新于 x' (t3 micro)
       修法: updated_at 转「更新于 2 小时前」相对时间格式 (跟 sb2_relativeTime 统一),
       原值 (ISO) 作为 title 兜底, 鼠标悬停可查
       status 文案中文化 (原型 line 769: 已入库绿 / 待审核金): 'pending' → '待审核', 'ok' → '已入库'
       meta 顺序: id · chunks · 更新于 relative (跟原型一致) */
    var updatedRaw = e.updated_at || e.updated || '';
    var updated = updatedRaw ? ('更新于 ' + sb2_relativeTime(updatedRaw)) : '更新于 -';
    var cat = e.category || e.category_name || '未分类';
    var scope = _sb2ScopeLabel(e.scope || e.scope_name || 'global');
    var statusRaw = e.status || 'ok';
    var statusLabel = statusRaw === 'pending' ? '待审核' : statusRaw === 'failed' ? '失败' : statusRaw === 'superseded' ? '已废弃' : '已入库';
    var statusClass = 'sb2-tag-status-' + (statusRaw === 'pending' ? 'pending' : statusRaw === 'failed' ? 'failed' : statusRaw === 'superseded' ? 'superseded' : 'ok');
    return '<article class="sb2-kb-card" data-kb-id="' + escapeAttr(id) + '" data-sb2-scope="' + escapeAttr(e.scope || e.scope_name || 'global') + '" title="' + escapeAttr(updatedRaw) + '" onclick="sb2KbOpenDetail(\'' + escapeAttr(id) + '\')">' +
      '<div class="sb2-kb-card-info">' +
        '<h3 class="sb2-kb-card-title">' + escapeHtml(title) + '</h3>' +
        '<div class="sb2-kb-card-meta">' +
          '<span class="sb2-kb-card-id">' + escapeHtml(id) + '</span>' +
          '<span class="sb2-kb-card-sep">·</span>' +
          '<span class="sb2-kb-card-chunks">' + escapeHtml(chunks) + '</span>' +
          '<span class="sb2-kb-card-sep">·</span>' +
          '<span class="sb2-kb-card-updated">' + escapeHtml(updated) + '</span>' +
        '</div>' +
      '</div>' +
      '<div class="sb2-kb-card-tags">' +
        '<span class="sb2-tag sb2-tag-cat">' + escapeHtml(cat) + '</span>' +
        '<span class="sb2-tag sb2-tag-scope">' + escapeHtml(scope) + '</span>' +
        '<span class="sb2-tag ' + statusClass + '">' + escapeHtml(statusLabel) + '</span>' +
      '</div>' +
    '</article>';
  }).join('');
  el.innerHTML = cardsHtml + _sb2KbBuildPager(entries.length);
}

function _sb2ScopeLabel(s){
  return ({global:'全员', team:'团队', group:'项目组', personal:'个人'})[s] || s;
}

function sb2KbImport(){
  showToast('导入 — 后续 MVP 接 /api/knowledge/import (旧 importKnowledgeOverlay 留兜底)', 'info');
}

function sb2KbNew(){
  sb2KbOpenNewModal();
}

/* ============================================================
 * MVP3: 新建/编辑弹窗 (轻量录入四层: 标题/内容/分类/scope)
 * - POST /api/knowledge/entries 新建
 * - PUT  /api/knowledge/entries/:id 编辑
 * - DELETE /api/knowledge/entries/:id 删除
 * - 提交成功后 sb2KbInit() 重拉数据 (保持现场聚合原则)
 * ============================================================ */
var _sb2KbEditingId = null;

function sb2KbOpenNewModal(){
  _sb2KbEditingId = null;
  document.getElementById('sb2KbModalTitle').textContent = '新建知识';
  document.getElementById('sb2KbInputTitle').value = '';
  document.getElementById('sb2KbInputContent').value = '';
  document.getElementById('sb2KbInputCategory').value = '';
  document.getElementById('sb2KbInputScope').value = 'global';
  var delBtn = document.getElementById('sb2KbDeleteBtn');
  if (delBtn) delBtn.style.display = 'none';
  document.getElementById('sb2KbModal').classList.add('open');
  setTimeout(function(){
    var t = document.getElementById('sb2KbInputTitle');
    if (t) t.focus();
  }, 50);
}

function sb2KbOpenEditModal(entryId){
  var entry = null;
  for (var i = 0; i < _sb2KbEntries.length; i++){
    if (_sb2KbEntries[i].id === entryId) { entry = _sb2KbEntries[i]; break; }
  }
  if (!entry) { showToast('未找到该条目', 'warning'); return; }
  _sb2KbEditingId = entryId;
  document.getElementById('sb2KbModalTitle').textContent = '编辑知识';
  document.getElementById('sb2KbInputTitle').value = entry.title || '';
  document.getElementById('sb2KbInputContent').value = entry.content || '';
  document.getElementById('sb2KbInputCategory').value = entry.category || entry.category_name || '';
  document.getElementById('sb2KbInputScope').value = entry.scope || 'global';
  var delBtn = document.getElementById('sb2KbDeleteBtn');
  if (delBtn) delBtn.style.display = '';
  document.getElementById('sb2KbModal').classList.add('open');
}

function sb2KbCloseModal(){
  var m = document.getElementById('sb2KbModal');
  if (m) m.classList.remove('open');
  _sb2KbEditingId = null;
}

async function sb2KbSubmit(){
  var title = (document.getElementById('sb2KbInputTitle').value || '').trim();
  var content = (document.getElementById('sb2KbInputContent').value || '').trim();
  var category = (document.getElementById('sb2KbInputCategory').value || '').trim();
  var scope = document.getElementById('sb2KbInputScope').value || 'global';
  if (!title) { showToast('请输入标题', 'warning'); return; }
  if (!content) { showToast('请输入内容', 'warning'); return; }

  var body = JSON.stringify({ title: title, content: content, category: category, scope: scope });
  try {
    var resp;
    if (_sb2KbEditingId) {
      resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_sb2KbEditingId), { method: 'PUT', body: body });
    } else {
      resp = await apiFetch('/api/knowledge/entries', { method: 'POST', body: body });
    }
    if (resp && resp.ok) {
      showToast(_sb2KbEditingId ? '已保存' : '已录入', 'success');
      sb2KbCloseModal();
      sb2KbInit(); // 重新拉数据, chips 现场聚合
    } else {
      showToast((_sb2KbEditingId ? '保存' : '录入') + '失败', 'error');
    }
  } catch (e) {
    console.error('[sb2-kb] submit failed:', e);
    showToast('提交失败: ' + (e.message || '未知错误'), 'error');
  }
}

async function sb2KbDeleteEntry(){
  if (!_sb2KbEditingId) return;
  if (!confirm('确定删除该知识?此操作不可恢复。')) return;
  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_sb2KbEditingId), { method: 'DELETE' });
    if (resp && resp.ok) {
      showToast('已删除', 'success');
      sb2KbCloseModal();
      sb2KbInit();
    } else {
      showToast('删除失败', 'error');
    }
  } catch (e) {
    console.error('[sb2-kb] delete failed:', e);
    showToast('删除失败: ' + (e.message || ''), 'error');
  }
}

/* ============================================================
 * MVP4: 详情右滑 (只读先行)
 * - GET /api/knowledge/entries/:id 拉详情
 * - 渲染: 标题 / 内容 / 分类 / scope / chunks / 状态 / 创建者 / 时间
 * - 缺值显 - (按 _sb2KbFmt + _sb2KbOrDash 兜底)
 * - 面板内"编辑"按钮 → 关闭详情 + 打开 MVP3 编辑弹窗
 * - 面板内"删除"按钮 → DELETE + sb2KbInit 自动刷新
 * ============================================================ */
var _sb2KbDetailId = null;

async function sb2KbOpenDetail(entryId){
  _sb2KbDetailId = entryId;
  // 显示 loading 状态
  document.getElementById('sb2KbDetailTitle').textContent = '-';
  document.getElementById('sb2KbDetailLoading').style.display = 'block';
  document.getElementById('sb2KbDetailLoading').textContent = '载入中…';
  document.getElementById('sb2KbDetailMeta').style.display = 'none';
  document.getElementById('sb2KbDetailContentWrap').style.display = 'none';
  document.getElementById('sb2KbDetailPanel').classList.add('open');
  document.getElementById('sb2KbDetailOverlay').classList.add('open');

  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(entryId));
    if (!resp || !resp.ok) {
      document.getElementById('sb2KbDetailLoading').textContent = '载入失败';
      showToast('详情载入失败', 'error');
      return;
    }
    var doc = await resp.json();
    sb2KbRenderDetail(doc);
  } catch (e) {
    console.error('[sb2-kb] detail load failed:', e);
    document.getElementById('sb2KbDetailLoading').textContent = '载入失败: ' + (e.message || '');
  }
}

function sb2KbRenderDetail(doc){
  try {
    // 标题
    document.getElementById('sb2KbDetailTitle').textContent = _sb2KbOrDash(doc.title);
    console.log('[sb2-kb] render detail start, doc keys:', doc ? Object.keys(doc).join(',') : '(null)');

    // 隐藏 loading
    document.getElementById('sb2KbDetailLoading').style.display = 'none';

    // meta grid: 分类 / scope / chunks / 状态 / 创建者 / 创建时间 / 更新时间 / id
    var meta = document.getElementById('sb2KbDetailMeta');
    var items = [
      { label: '分类',     value: _sb2KbOrDash(doc.category || doc.category_name) },
      { label: '范围',     value: _sb2KbScopeLabel(doc.scope || 'global') },
      { label: 'Chunks',   value: _sb2KbFmtNum(doc.chunkCount) },
      { label: '状态',     value: _sb2KbOrDash(doc.status) },
      { label: '创建者',   value: _sb2KbOrDash(doc.createdBy) },
      { label: '创建时间', value: _sb2KbFmtTs(doc.createdAt) },
      { label: '更新时间', value: _sb2KbFmtTs(doc.updatedAt) },
      { label: 'ID',       value: _sb2KbOrDash(doc.id) }
    ];
    meta.innerHTML = items.map(function(it){
      var muted = it.value === '-' ? ' muted' : '';
      return '<div class="sb2-kb-meta-item">' +
        '<div class="sb2-kb-meta-label">' + escapeHtml(it.label) + '</div>' +
        '<div class="sb2-kb-meta-value' + muted + '">' + escapeHtml(it.value) + '</div>' +
      '</div>';
    }).join('');
    meta.style.display = 'grid';
    console.log('[sb2-kb] meta grid rendered, item count:', items.length);

    // 内容
    var contentEl = document.getElementById('sb2KbDetailContent');
    var contentWrap = document.getElementById('sb2KbDetailContentWrap');
    if (doc.content && doc.content.trim()) {
      contentEl.textContent = doc.content;
      contentEl.classList.remove('muted');
    } else {
      contentEl.textContent = '-';
      contentEl.classList.add('muted');
    }
    contentWrap.style.display = 'block';
    console.log('[sb2-kb] detail render OK, content length:', (doc.content || '').length);
  } catch (e) {
    // 显式记录 + 回滚面板状态，避免「title 设了下面空白」这种 silent fail
    console.error('[sb2-kb] render detail failed:', e, 'doc:', doc);
    var loading = document.getElementById('sb2KbDetailLoading');
    if (loading) {
      loading.style.display = 'block';
      loading.textContent = '渲染失败: ' + (e.message || '');
    }
  }
}

function sb2KbCloseDetail(){
  document.getElementById('sb2KbDetailPanel').classList.remove('open');
  document.getElementById('sb2KbDetailOverlay').classList.remove('open');
  _sb2KbDetailId = null;
}

function sb2KbDetailEdit(){
  if (!_sb2KbDetailId) return;
  var entryId = _sb2KbDetailId;
  sb2KbCloseDetail();
  // MVP5: 编辑器走 sb2KbOpenEditor (8 字段完整编辑器), 不再走 MVP3 轻量弹窗 sb2KbOpenEditModal
  sb2KbOpenEditor(entryId);
}

async function sb2KbDetailDelete(){
  if (!_sb2KbDetailId) return;
  if (!confirm('确定删除该知识?此操作不可恢复。')) return;
  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_sb2KbDetailId), { method: 'DELETE' });
    if (resp && resp.ok) {
      showToast('已删除', 'success');
      sb2KbCloseDetail();
      sb2KbInit();
    } else {
      showToast('删除失败', 'error');
    }
  } catch (e) {
    console.error('[sb2-kb] detail delete failed:', e);
    showToast('删除失败: ' + (e.message || ''), 'error');
  }
}

/* ============================================================
 * MVP5: 完整编辑器 (8 字段 center modal)
 * - 字段: title / content / category / categoryId / projectId / scope / teamId / groupIds / status
 * - 端点: GET /api/knowledge/entries/:id (拉一次) + PUT /api/knowledge/entries/:id (保存)
 * - 卡片「编辑」按钮 (来自详情面板 sb2KbDetailEdit) → 关闭详情 + 打开本编辑器
 * ============================================================ */
var _sb2KbEditingFullId = null;

async function sb2KbOpenEditor(entryId){
  _sb2KbEditingFullId = entryId;
  // 重置表单
  document.getElementById('sb2KbEditFieldTitle').value = '';
  document.getElementById('sb2KbEditFieldContent').value = '';
  document.getElementById('sb2KbEditFieldCategory').value = '';
  document.getElementById('sb2KbEditFieldCategoryId').value = '';
  document.getElementById('sb2KbEditFieldProjectId').value = '';
  document.getElementById('sb2KbEditFieldScope').value = 'global';
  document.getElementById('sb2KbEditFieldTeamId').value = '';
  document.getElementById('sb2KbEditFieldGroupIds').value = '';
  document.getElementById('sb2KbEditFieldStatus').value = 'ok';
  var status = document.getElementById('sb2KbEditStatus');
  status.textContent = '载入中…';
  status.className = 'sb2-kb-edit-status';
  document.getElementById('sb2KbEditTitle').textContent = '编辑知识';
  document.getElementById('sb2KbEditModal').classList.add('open');

  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(entryId));
    if (!resp || !resp.ok) {
      status.textContent = '载入失败: ' + ((resp && resp.status) || '网络错误');
      status.className = 'sb2-kb-edit-status err';
      return;
    }
    var doc = await resp.json();
    sb2KbRenderEditor(doc);
    console.log('[sb2-kb] editor loaded, doc keys:', Object.keys(doc).join(','));
  } catch (e) {
    console.error('[sb2-kb] editor load failed:', e);
    status.textContent = '载入失败: ' + (e.message || '');
    status.className = 'sb2-kb-edit-status err';
  }
}

function sb2KbRenderEditor(doc){
  // 回填表单
  document.getElementById('sb2KbEditFieldTitle').value = doc.title || '';
  document.getElementById('sb2KbEditFieldContent').value = doc.content || '';
  document.getElementById('sb2KbEditFieldCategory').value = doc.category || '';
  document.getElementById('sb2KbEditFieldCategoryId').value = (doc.categoryId != null && doc.categoryId !== '') ? String(doc.categoryId) : '';
  document.getElementById('sb2KbEditFieldProjectId').value = doc.projectId || '';
  document.getElementById('sb2KbEditFieldScope').value = doc.scope || 'global';
  document.getElementById('sb2KbEditFieldTeamId').value = doc.teamId || '';
  // groupIds 可能是数组或 JSON 字符串
  var gid = doc.groupIds;
  if (Array.isArray(gid)) gid = gid.join(',');
  document.getElementById('sb2KbEditFieldGroupIds').value = gid || '';
  document.getElementById('sb2KbEditFieldStatus').value = doc.status || 'ok';
  var status = document.getElementById('sb2KbEditStatus');
  status.textContent = '已载入, 可编辑保存';
  status.className = 'sb2-kb-edit-status ok';
}

function sb2KbCloseEditor(){
  document.getElementById('sb2KbEditModal').classList.remove('open');
  _sb2KbEditingFullId = null;
}

async function sb2KbEditorSubmit(){
  if (!_sb2KbEditingFullId) return;
  var statusEl = document.getElementById('sb2KbEditStatus');
  var submitBtn = document.getElementById('sb2KbEditSubmitBtn');
  var title = document.getElementById('sb2KbEditFieldTitle').value.trim();
  var content = document.getElementById('sb2KbEditFieldContent').value.trim();
  if (!title || !content) {
    statusEl.textContent = '请填写标题和内容';
    statusEl.className = 'sb2-kb-edit-status err';
    return;
  }
  var categoryIdRaw = document.getElementById('sb2KbEditFieldCategoryId').value.trim();
  var groupIdsRaw = document.getElementById('sb2KbEditFieldGroupIds').value.trim();
  var body = {
    title: title,
    content: content,
    category: document.getElementById('sb2KbEditFieldCategory').value.trim(),
    categoryId: categoryIdRaw ? parseInt(categoryIdRaw, 10) : null,
    projectId: document.getElementById('sb2KbEditFieldProjectId').value.trim() || null,
    scope: document.getElementById('sb2KbEditFieldScope').value,
    teamId: document.getElementById('sb2KbEditFieldTeamId').value.trim() || null,
    groupIds: groupIdsRaw ? groupIdsRaw.split(',').map(function(s){return s.trim();}).filter(Boolean) : null,
    status: document.getElementById('sb2KbEditFieldStatus').value
  };
  submitBtn.disabled = true;
  statusEl.textContent = '保存中…';
  statusEl.className = 'sb2-kb-edit-status';
  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_sb2KbEditingFullId), {
      method: 'PUT', body: JSON.stringify(body)
    });
    if (!resp || !resp.ok) {
      statusEl.textContent = '保存失败: HTTP ' + (resp ? resp.status : 'no response');
      statusEl.className = 'sb2-kb-edit-status err';
      submitBtn.disabled = false;
      return;
    }
    console.log('[sb2-kb] editor save OK');
    showToast('已保存', 'success');
    sb2KbCloseEditor();
    // 关闭详情面板 + 重新初始化列表/chips
    sb2KbCloseDetail();
    sb2KbInit();
  } catch (e) {
    console.error('[sb2-kb] editor save failed:', e);
    statusEl.textContent = '保存失败: ' + (e.message || '');
    statusEl.className = 'sb2-kb-edit-status err';
    submitBtn.disabled = false;
  }
}

/* ============================================================
 * MVP5: 版本历史 (旧端点 /api/knowledge/<id>/versions)
 * - 端点契约:
 *     GET /api/knowledge/<kb_id>/versions → { versions: [{version, content, createdAt, createdBy}], total, offset, limit }
 *     GET /api/knowledge/<kb_id>/versions/<v> → { version, content, createdAt, createdBy }
 *     POST /api/knowledge/<kb_id>/rollback { version } → { success, knowledge }
 * - 已知接口契约断裂: kb_entries 表的 ID (kb_xxx) 完全不在 knowledge_versions 表里
 *   → MVP5 端点已就位, 但实际数据 = 0 (知识库全部条目的历史版本暂空)
 *   → UI 展示空态文案, 不编造数据
 * ============================================================ */
var _sb2KbVersionDocId = null;

async function sb2KbOpenVersions(){
  if (!_sb2KbDetailId) return;
  var docId = _sb2KbDetailId;
  _sb2KbVersionDocId = docId;
  document.getElementById('sb2KbVersionTitle').textContent = '版本历史';
  document.getElementById('sb2KbVersionBody').innerHTML = '<div class="sb2-kb-loading">载入中…</div>';
  document.getElementById('sb2KbVersionModal').classList.add('open');
  try {
    var resp = await apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/versions');
    if (!resp || !resp.ok) {
      console.warn('[sb2-kb] versions fetch failed:', resp ? resp.status : 'no response');
      sb2KbRenderVersions(docId, { versions: [], total: 0 });
      return;
    }
    var data = await resp.json();
    console.log('[sb2-kb] versions loaded, count:', (data.versions || []).length, 'total:', data.total);
    sb2KbRenderVersions(docId, data);
  } catch (e) {
    console.error('[sb2-kb] versions load failed:', e);
    sb2KbRenderVersions(docId, { versions: [], total: 0 });
  }
}

function sb2KbRenderVersions(docId, data){
  var body = document.getElementById('sb2KbVersionBody');
  var versions = (data && data.versions) || [];
  if (versions.length === 0) {
    body.innerHTML =
      '<div class="sb2-kb-version-empty">' +
        '<div class="sb2-kb-version-empty-icon">⏱</div>' +
        '<div class="sb2-kb-version-empty-title">暂无历史版本</div>' +
      '</div>';
    return;
  }
  var html = '';
  versions.forEach(function(v){
    var content = v.content || '';
    var preview = content.length > 120 ? content.slice(0, 120) + '…' : content;
    var timeStr = v.createdAt ? _sb2KbFmtTs(v.createdAt) : '-';
    var ver = v.version;
    html += '<div class="sb2-kb-version-list-item">' +
      '<div class="sb2-kb-version-list-item-head">' +
        '<span class="sb2-kb-version-list-item-no">版本 ' + escapeHtml(String(ver != null ? ver : '-')) + '</span>' +
        '<span class="sb2-kb-version-list-item-time">' + escapeHtml(timeStr) + '</span>' +
      '</div>' +
      '<div class="sb2-kb-version-list-item-meta">操作者: ' + escapeHtml(v.createdBy || '-') + '</div>' +
      '<div class="sb2-kb-version-list-item-preview">' + escapeHtml(preview) + '</div>' +
      '<div class="sb2-kb-version-list-item-actions">' +
        '<button class="sb2-btn sb2-btn-ghost" onclick="sb2KbViewVersion(\'' + escapeAttr(docId) + '\', ' + Number(ver) + ')">查看</button>' +
        '<button class="sb2-btn sb2-btn-ink" onclick="sb2KbRollbackVersion(\'' + escapeAttr(docId) + '\', ' + Number(ver) + ')">回滚到此版本</button>' +
      '</div>' +
    '</div>';
  });
  body.innerHTML = html;
}

function sb2KbCloseVersions(){
  document.getElementById('sb2KbVersionModal').classList.remove('open');
  _sb2KbVersionDocId = null;
}

async function sb2KbViewVersion(docId, version){
  document.getElementById('sb2KbVersionViewTitle').textContent = '版本 ' + version + ' 详情';
  document.getElementById('sb2KbVersionViewBody').textContent = '载入中…';
  document.getElementById('sb2KbVersionViewModal').classList.add('open');
  try {
    var resp = await apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/versions/' + encodeURIComponent(version));
    if (!resp || !resp.ok) {
      document.getElementById('sb2KbVersionViewBody').textContent = '载入失败: HTTP ' + (resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var v = data.version || data;
    var meta = '操作者: ' + escapeHtml(v.createdBy || '-') + ' · 时间: ' + escapeHtml(v.createdAt ? _sb2KbFmtTs(v.createdAt) : '-');
    var body = document.getElementById('sb2KbVersionViewBody');
    body.innerHTML =
      '<div class="sb2-kb-version-view-meta">' + meta + '</div>' +
      '<div>' + escapeHtml(v.content || '-') + '</div>';
    console.log('[sb2-kb] version detail loaded, version:', v.version, 'content length:', (v.content || '').length);
  } catch (e) {
    console.error('[sb2-kb] version detail load failed:', e);
    document.getElementById('sb2KbVersionViewBody').textContent = '载入失败: ' + (e.message || '');
  }
}

function sb2KbCloseVersionView(){
  document.getElementById('sb2KbVersionViewModal').classList.remove('open');
}

async function sb2KbRollbackVersion(docId, version){
  if (!confirm('确定回滚到版本 ' + version + '? 当前内容将被覆盖, 此操作会写入一条新版本（基于当前内容）。')) return;
  try {
    var resp = await apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/rollback', {
      method: 'POST', body: JSON.stringify({ version: parseInt(version, 10) })
    });
    if (!resp || !resp.ok) {
      showToast('回滚失败: HTTP ' + (resp ? resp.status : 'no response'), 'error');
      console.warn('[sb2-kb] rollback failed:', resp ? resp.status : 'no response');
      return;
    }
    console.log('[sb2-kb] rollback OK, version:', version);
    showToast('已回滚到版本 ' + version, 'success');
    // 关版本历史模态, 重新加载版本
    if (_sb2KbVersionDocId === docId) {
      sb2KbOpenVersions();
    } else {
      sb2KbCloseVersions();
      sb2KbCloseDetail();
      sb2KbInit();
    }
  } catch (e) {
    console.error('[sb2-kb] rollback failed:', e);
    showToast('回滚失败: ' + (e.message || ''), 'error');
  }
}

function _sb2KbOrDash(v){
  if (v == null) return '-';
  if (typeof v === 'string' && v.trim() === '') return '-';
  return String(v);
}

// scope 字符串 → 中文标签（list / detail / 任何面板复用）
// 与 MVP2 list 现场聚合保持同一份映射，避免幽灵 scope
var _SB2_SCOPE_LABEL = {
  global:   '全员',
  team:     '团队',
  group:    '分组',
  personal: '个人'
};
function _sb2KbScopeLabel(s){
  return _SB2_SCOPE_LABEL[s] || s || '-';
}

function _sb2KbFmtNum(n){
  if (n == null) return '-';
  return String(n);
}

function _sb2KbFmtTs(ts){
  if (ts == null || ts === 0) return '-';
  // 兼容 ms (int) 和 iso (str) 两种格式
  var d;
  try {
    if (typeof ts === 'number') d = new Date(ts);
    else if (typeof ts === 'string') d = new Date(ts);
    else return '-';
    if (isNaN(d.getTime())) return '-';
    var Y = d.getFullYear();
    var M = ('0' + (d.getMonth()+1)).slice(-2);
    var D = ('0' + d.getDate()).slice(-2);
    var h = ('0' + d.getHours()).slice(-2);
    var m = ('0' + d.getMinutes()).slice(-2);
    return Y + '-' + M + '-' + D + ' ' + h + ':' + m;
  } catch (e) { return '-'; }
}

/* ============================================================
 * MVP6: 设置屏功能入口卡片 (语义搜索 / 大脑知识 / RAG)
 * - 端点契约 (与后端 _handle_*_ 一一对应):
 *     POST /api/knowledge/search          {query, limit?, scope?, categoryId?, projectId?}
 *     GET  /api/brain/status              → {success, ...stats}
 *     POST /api/brain/trigger-manual      → {success, enqueuedClean, enqueuedClassify, enqueuedInduct}
 *     POST /api/rag/retrieve              {query, empId?, topK?}
 *     POST /api/rag/build                 {empId?}
 * - 卡片: 名称 + 状态 + 操作按钮 + 结果面板 (本屏克制, 不发明大设计)
 * - 跟 sb2-settings 系列钩子联动: switchModule('settings') → sb2SettingsShow()
 * ============================================================ */
function _sb2SettingsSetStatus(card, statusId, val){
  var el = document.getElementById(statusId);
  if (el) el.textContent = val || '-';
}
function _sb2SettingsSetResult(resultId, text, ok){
  var el = document.getElementById(resultId);
  if (!el) return;
  el.classList.remove('ok', 'err');
  if (ok === true) el.classList.add('ok');
  else if (ok === false) el.classList.add('err');
  el.textContent = text || '';
  el.classList.add('active');
}
function _sb2SettingsHideResult(resultId){
  var el = document.getElementById(resultId);
  if (el) el.classList.remove('active', 'ok', 'err');
}

// 入口: switchModule('settings') 时调, 自动隐藏所有结果面板 (重置到默认)
function sb2SettingsShow(){
  _sb2SettingsHideResult('sb2FeatSearchResult');
  _sb2SettingsHideResult('sb2FeatBrainResult');
  _sb2SettingsHideResult('sb2FeatRagResult');
  _sb2SettingsHideResult('sb2FeatEventsResult');
  // 状态字段恢复 -
  _sb2SettingsSetStatus(null, 'sb2FeatSearchStatus', '-');
  _sb2SettingsSetStatus(null, 'sb2FeatBrainStatus', '-');
  _sb2SettingsSetStatus(null, 'sb2FeatRagStatus', '-');
  _sb2SettingsSetStatus(null, 'sb2FeatEventsStatus', '-');
  var input = document.getElementById('sb2FeatSearchInput');
  if (input) input.value = '';
  var eventsInput = document.getElementById('sb2FeatEventsEntityInput');
  if (eventsInput) eventsInput.value = '';
}

// 卡 1: 语义搜索
async function sb2SettingsRunSearch(){
  var inp = document.getElementById('sb2FeatSearchInput');
  var query = (inp && inp.value || '').trim();
  if (!query) {
    _sb2SettingsSetResult('sb2FeatSearchResult', '请输入要搜索的内容再搜索', false);
    return;
  }
  var btn = document.getElementById('sb2FeatSearchBtn');
  if (btn) btn.disabled = true;
  _sb2SettingsSetResult('sb2FeatSearchResult', '搜索中…', null);
  try {
    var resp = await apiFetch('/api/knowledge/search', {
      method: 'POST', body: JSON.stringify({ query: query, limit: 10 })
    });
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatSearchResult', '搜索失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] search failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var count = (data && data.count) || (data && data.docs && data.docs.length) || 0;
    _sb2SettingsSetStatus(null, 'sb2FeatSearchStatus', '召回 ' + count + ' 条');
    var titles = (data.docs || []).slice(0, 5).map(function(d, i){
      return (i+1) + '. ' + (d.title || d.id || '-');
    }).join('\n');
    var extra = (count > 5) ? '\n... 还有 ' + (count - 5) + ' 条' : '';
    _sb2SettingsSetResult('sb2FeatSearchResult', '命中 ' + count + ' 条\n' + titles + extra, true);
    console.log('[sb2-settings] search OK, count:', count);
  } catch (e) {
    console.error('[sb2-settings] search error:', e);
    _sb2SettingsSetResult('sb2FeatSearchResult', '搜索失败: ' + (e.message || ''), false);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 卡 2: 大脑知识 — 查看状态
async function sb2SettingsBrainFetchStatus(){
  var btn = event && event.currentTarget;
  if (btn) btn.disabled = true;
  _sb2SettingsSetResult('sb2FeatBrainResult', '载入中…', null);
  try {
    var resp = await apiFetch('/api/brain/status');
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatBrainResult', '获取状态失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] brain status failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var statsText = JSON.stringify(data, null, 2);
    if (statsText.length > 600) statsText = statsText.slice(0, 600) + '\n...';
    _sb2SettingsSetStatus(null, 'sb2FeatBrainStatus', data.success ? '已连接' : '异常');
    _sb2SettingsSetResult('sb2FeatBrainResult', statsText, data.success === true);
    console.log('[sb2-settings] brain status OK, keys:', Object.keys(data).join(','));
  } catch (e) {
    console.error('[sb2-settings] brain status error:', e);
    _sb2SettingsSetResult('sb2FeatBrainResult', '获取失败: ' + (e.message || ''), false);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 卡 2: 大脑知识 — 手动触发
async function sb2SettingsBrainTrigger(){
  var btn = event && event.currentTarget;
  if (!confirm('确认触发大脑全量处理？（清理 / 分类 / 归纳三类任务入队）')) return;
  if (btn) btn.disabled = true;
  _sb2SettingsSetResult('sb2FeatBrainResult', '入队中…', null);
  try {
    var resp = await apiFetch('/api/brain/trigger-manual', { method: 'POST', body: '{}' });
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatBrainResult', '触发失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] brain trigger failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    _sb2SettingsSetStatus(null, 'sb2FeatBrainStatus', '已触发');
    _sb2SettingsSetResult('sb2FeatBrainResult',
      '清理: ' + data.enqueuedClean + ' 条\n分类: ' + data.enqueuedClassify + ' 条\n归纳: ' + data.enqueuedInduct + ' 条',
      data.success === true);
    showToast('大脑处理已入队', 'success');
    console.log('[sb2-settings] brain trigger OK:', data);
  } catch (e) {
    console.error('[sb2-settings] brain trigger error:', e);
    _sb2SettingsSetResult('sb2FeatBrainResult', '触发失败: ' + (e.message || ''), false);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 卡 3: RAG — 测试检索
async function sb2SettingsRagRetrieve(){
  var btn = event && event.currentTarget;
  var query = prompt('输入测试内容（知识检索）:', '推荐女装的纯视频型达人');
  if (query == null || !query.trim()) return;
  if (btn) btn.disabled = true;
  _sb2SettingsSetResult('sb2FeatRagResult', '检索中…', null);
  try {
    var resp = await apiFetch('/api/rag/retrieve', {
      method: 'POST', body: JSON.stringify({ query: query.trim(), topK: 3 })
    });
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatRagResult', '检索失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] rag retrieve failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var docTitles = (data.docs || []).slice(0, 3).map(function(d, i){
      return (i+1) + '. ' + (d.title || d.id || '-');
    }).join('\n');
    var prodTitles = (data.products || []).slice(0, 3).map(function(p, i){
      return (i+1) + '. ' + (p.name || p.id || '-');
    }).join('\n');
    _sb2SettingsSetStatus(null, 'sb2FeatRagStatus', '召回 知识:' + (data.docs || []).length + ' 商品:' + (data.products || []).length);
    _sb2SettingsSetResult('sb2FeatRagResult',
      '知识: ' + (data.docs || []).length + ' 条\n' + docTitles +
      '\n产品: ' + (data.products || []).length + ' 条\n' + prodTitles, true);
    console.log('[sb2-settings] rag retrieve OK, docs:', (data.docs || []).length, 'products:', (data.products || []).length);
  } catch (e) {
    console.error('[sb2-settings] rag retrieve error:', e);
    _sb2SettingsSetResult('sb2FeatRagResult', '检索失败: ' + (e.message || ''), false);
  } finally {
    if (btn) btn.disabled = false;
  }
}

// 卡 3: RAG — 重建索引
async function sb2SettingsRagBuild(){
  var btn = event && event.currentTarget;
  if (!confirm('确认重建 RAG 索引? (全量知识库 embedding 重建, 可能耗时)')) return;
  if (btn) btn.disabled = true;
  _sb2SettingsSetResult('sb2FeatRagResult', '提交任务中…', null);
  try {
    var resp = await apiFetch('/api/rag/build', { method: 'POST', body: '{}' });
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatRagResult', '重建任务提交失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] rag build failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    _sb2SettingsSetStatus(null, 'sb2FeatRagStatus', '任务已提交');
    var summary = JSON.stringify(data, null, 2);
    if (summary.length > 400) summary = summary.slice(0, 400) + '\n...';
    _sb2SettingsSetResult('sb2FeatRagResult', '✅ 重建任务已提交\n' + summary, true);
    showToast('RAG 重建任务已提交', 'success');
    console.log('[sb2-settings] rag build OK:', data);
  } catch (e) {
    console.error('[sb2-settings] rag build error:', e);
    _sb2SettingsSetResult('sb2FeatRagResult', '重建失败: ' + (e.message || ''), false);
  } finally {
    if (btn) btn.disabled = false;
  }
}

/* ============================================================
 * MVP6.5: 知识事件卡 (设置屏 4 号卡)
 * - 端点契约 (与后端 14856 / 15008 / 14918 一一对应):
 *     GET /api/knowledge-events?entity_type=&entity_id=&limit=
 *         → { events: [{id, entity_type, entity_id, agent_id, event_type, title, content_summary, conclusions, user_query, created_at}], total }
 *     GET /api/knowledge-events/stats
 *         → { total, byEntityType, recent7d }
 *     POST /api/knowledge-events  (创建, 本卡不暴露 UI 入口 — 写由后台跑)
 *     GET /api/knowledge-events/<id> /search (本卡不暴露 — 留作 7 步收口期补 backlog)
 * ============================================================ */
async function sb2SettingsEventsQuery(){
  var inp = document.getElementById('sb2FeatEventsEntityInput');
  var entityId = (inp && inp.value || '').trim();
  var url = '/api/knowledge-events?limit=20';
  if (entityId) url += '&entity_id=' + encodeURIComponent(entityId);
  _sb2SettingsSetResult('sb2FeatEventsResult', '查询中…', null);
  try {
    var resp = await apiFetch(url);
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatEventsResult', '查询失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] events query failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var events = data.events || [];
    _sb2SettingsSetStatus(null, 'sb2FeatEventsStatus', '命中 ' + events.length + ' 条');
    if (events.length === 0) {
      _sb2SettingsSetResult('sb2FeatEventsResult', '该条件下暂无事件', true);
      console.log('[sb2-settings] events query OK, 0 events');
      return;
    }
    var lines = events.slice(0, 10).map(function(ev, idx){
      var ent = (ev.entity_type || '-') + '·' + (ev.entity_id || '-');
      var t = ev.created_at ? formatRelativeTime(ev.created_at) : '-';
      var title = ev.title || ev.content_summary || '(无标题)';
      return (idx+1) + '. [' + (ev.event_type || '-') + '] ' + ent + ' · ' + t + '\n    ' + title;
    });
    var extra = events.length > 10 ? '\n... 还有 ' + (events.length - 10) + ' 条' : '';
    _sb2SettingsSetResult('sb2FeatEventsResult', '命中 ' + events.length + ' 条\n' + lines.join('\n') + extra, true);
    console.log('[sb2-settings] events query OK, count:', events.length);
  } catch (e) {
    console.error('[sb2-settings] events query error:', e);
    _sb2SettingsSetResult('sb2FeatEventsResult', '查询失败: ' + (e.message || ''), false);
  }
}

async function sb2SettingsEventsStats(){
  _sb2SettingsSetResult('sb2FeatEventsResult', '载入中…', null);
  try {
    var resp = await apiFetch('/api/knowledge-events/stats');
    if (!resp || !resp.ok) {
      _sb2SettingsSetResult('sb2FeatEventsResult', '统计失败: HTTP ' + (resp ? resp.status : 'no response'), false);
      console.warn('[sb2-settings] events stats failed:', resp ? resp.status : 'no response');
      return;
    }
    var data = await resp.json();
    var lines = [
      '总数: ' + (data.total != null ? data.total : '-') + ' 条',
      '近 7 天新增: ' + (data.recent7d != null ? data.recent7d : '-') + ' 条'
    ];
    if (data.byEntityType && Object.keys(data.byEntityType).length) {
      lines.push('按类型:');
      Object.keys(data.byEntityType).forEach(function(k){
        lines.push('  · ' + k + ': ' + data.byEntityType[k] + ' 条');
      });
    }
    _sb2SettingsSetStatus(null, 'sb2FeatEventsStatus', '总数 ' + (data.total != null ? data.total : '-') + ' / 近 7 天 ' + (data.recent7d != null ? data.recent7d : '-'));
    _sb2SettingsSetResult('sb2FeatEventsResult', lines.join('\n'), true);
    console.log('[sb2-settings] events stats OK, total:', data.total, 'recent7d:', data.recent7d);
  } catch (e) {
    console.error('[sb2-settings] events stats error:', e);
    _sb2SettingsSetResult('sb2FeatEventsResult', '统计失败: ' + (e.message || ''), false);
  }
}

/* ============================================================
 * sb2-patterns MVP1: 规律库屏数据接线层
 * - 调 /api/knowledge-patterns?limit=100 (接口签名零改动)
 * - 渲染 .sb2-patterns-main (DOM 在 <main class="app-main"> 内注入)
 * - 旧 patternsMid 系列 CSS 兜底 display:none (旧 DOM 不退役仅兜底)
 * - MVP1 范围: 骨架 + 列表真实数据 + verification_level 状态过滤
 *   不含 (后续 MVP2-5):
 *     MVP2: 详情右滑 (含 evidence) — 接 GET /api/knowledge-patterns/<id>
 *     MVP3: CRUD — POST/PUT/DELETE
 *     MVP4: 投票 + 手动归纳弹窗 — POST /feedback + /induce
 *     MVP5: 晋升链可视化 + deal 合作记录
 * - 字段读取 (snake_case, MVP6.5 created_at 教训):
 *     p.id / p.category / p.pattern_text / p.confidence_score (0-100)
 *     p.verification_level (hypothesis/candidate/verified)
 *     p.hit_count / p.evidence_count / p.status / p.created_at (int 秒)
 * ============================================================ */
var _sb2PtnPatterns = [];
var _sb2PtnLevel = ''; // '', 'hypothesis', 'candidate', 'verified'
var _sb2PtnCat = '';   // 〔24 轮批注④〕'' = 全部类目, 否则 = category 名字符串 (sb2PatternsInit catMap 同源)
var _sb2PtnStatus = ''; // 〔24 轮批注④ 二阶段 老大 12:54 授权映射〕'' = 全部, 否则 = status (draft/confirmed/deprecated, API 实证字段)
/* 〔r39-16 批注③〕前端切片分页状态: _sb2PtnPatterns 已全量在手 (API limit=100),
   筛选组合过滤后按 20/页切片渲染, 底栏分页条钉底部 (老大: 不要滑动的) */
var _sb2PtnPage = 1;
/* ★ r66 批注④ 老大 15:30「增加两个达人, 意思就是一页 12 个」: 规律库卡片页尺寸 20→12
   — 截图 3 显示当前 5 页 × 20 = 100 条 (2 列 × 10 行, 满屏铺到底)
   — 老大希望 12 条/页 (2 列 × 6 行, 跟模块卡片密度匹配, 不滚动 + 留底部 breathing room)
   — 跟达人库 pageSize 10 / 商品库动态 pageSize 跨模块对齐 (28 终态 + 视口守卫, fit-in 不滚动) */
var _sb2PtnLimit = 12;
var _sb2PtnFilteredTotal = 0; // sb2PtnRender 每次刷新, sb2PtnGoPage 翻页上限用

/* ============================================================
 * sb2-talents MVP1: 端点契约 / 三维筛选现场聚合 / 评级分档 / 状态映射 / GPM 归一 / 表格 + 分页
 * 端点契约 (grep 源码 + 老大实测):
 *   GET /api/talents?q=&cooperation=&talent_category=&rating=&status=active&offset=&limit=
 *     → { talents: [...], total, offset, limit }
 *   排序: 后端 ORDER BY followers DESC
 *   非 admin 自动过滤 visible_ids (localhost X-Agent-Id 走两层架构, 全量需 prod HTTPS admin)
 * 设计-数据鸿沟 (老大实测):
 *   - level 混存 LV3/LV4/L2/LV5 与 L3/L4 + 181 条空 → 显示原值, 空显 -, 侧栏不做等级筛选
 *   - ai_rating 209/212 空 → 评级列用 rating_score 分档 (≥4.5=A / 3.5~4.49=B / 2.0~3.49=C / <2 或 0=D-)
 *   - matched_products 全非空 (212/212) → 用 matched_products
 *   - cooperation_status 分布: available 189 / communicating 11 / cooperating 10 / blacklist 1 / resting 1
 *     (following 0 / archived 0, 映射函数里照写)
 * ============================================================ */
var _sb2TalentsList = [];
var _sb2TalentsTotal = 0;
/* ★ fix/talents-hotfix: 服务端响应带全量 facets (rating/category/cooperation GROUP BY),
   前端不再从当前页 _sb2TalentsList 算聚合 (之前用 list 算 → 全部 50 = 当前页 limit)
   apply when: 任何「侧栏聚合需全库数据」必须服务端 GROUP BY 一次拿, 禁前端从当前页算 */
var _sb2TalentsFacets = null;
var _sb2TalentsPage = 1;
/* ★ r70 批注⑧ 老大 16:25「我都说了变成12个, 怎么还是10个达人展示」: 达人库 pageSize 10→12
   — r66 项④ 我误读了「增加两个达人, 意思就是一页12个」是规律库 (实际是达人库, 跟图片 4 达人库截图对应)
   — 跟规律库 _sb2PtnLimit 12 统一 (都是「一页 12 个」), 跨模块 pageSize 一致
   — 跟 28 终态铁律一致 (页面 fit-in, 内容密度合理) */
var _sb2TalentsLimit = 12;
var _sb2TalentsFilterRating = '';   // '' / 'A' / 'B' / 'C' / 'D'
var _sb2TalentsFilterCategory = ''; // '' / 类目字符串
var _sb2TalentsFilterStatus = '';   // '' / 7 枚举之一
var _sb2TalentsSelectedRowId = '';  // ★ fix/sb2-proto-align (commit 1): 行点击高亮单选 (原型首行默认选中)
var _sb2TalentsSearchTimer = null;
var _sb2TalentsSearchQ = '';
var _sb2TalentsSelectedIds = {};    // {id: true}
var _sb2TalentsLoaded = false;
/* ★ r39-P0: 匹配商品列真数据 (人货撮合链)
   - 修前 bug: server matched_products 字段是 JSON 字符串 '[]', Number() 永远 NaN → cell 恒显 0
   - 修后: 并发池 (上限 6) 调 POST /api/ai-match talent-to-product limit=1, 取 total 填充 _sb2TalentsMatchCount
   - cell 渲染: cache 命中 → 显示数字; 未命中 → '…' + .sb2-talents-match-pending + 呼吸动画
   - 单 cell patch: sb2TalentsPatchMatchCell 只改一个 td, 不重渲整表 (26 轮② 教训: 卡片闪烁动要避)
   - 切页/筛选: cache 保留 (跨 tab 不丢, 切回秒显示)
   - 0 处理: total=0 是真值, 显示 0 (不显示 -, 跟当前一致)
   - only loss no create: 没匹配真 0 是真值, 不造假 (老大 21 轮批注③ 「只丢不造」)
   apply when: 任何「列表列需逐行异步拉数据」必须并发池 + cell patch, 禁全表 await */
var _sb2TalentsMatchCount = {};      // {tid: total or 0}
var _sb2TalentsMatchFetching = {};   // {tid: true} 防止 dup
var _sb2TalentsMatchPoolMax = 6;     // 并发池上限 (262 行全表 ~ 50 行 / 6 = ~10s, 前 6 ~ 1.2s 出现)

/* ★ MVP1: cooperation_status 7 枚举中文映射 (老大实测: available 189 / communicating 11 / cooperating 10 / blacklist 1 / resting 1; following/archived 0 条, 映射函数里照写) */
var _SB2_TALENT_STATUS_LABELS = {
  'available': '可合作',
  'cooperating': '已合作',
  'communicating': '沟通中',
  'following': '关注',
  'blacklist': '黑名单',
  'resting': '暂休',
  'archived': '归档'
};

/* ★ MVP1: 评级分档函数 — 老大实测 ai_rating 209/212 空, level 首字符不是 A/B/C, 用 rating_score 分档
   阈值: ≥4.5=A / 3.5~4.49=B / 2.0~3.49=C / <2 或 0/空=D-
   颜色: A 绿(--sb2-success) B 蓝(--sb2-accent) C 橙(--sb2-warning) D 灰(--sb2-t3) */
function sb2TalentsRatingBucket(score){
  var s = Number(score);
  if (!s || s <= 0) return 'D';
  if (s >= 4.5) return 'A';
  if (s >= 3.5) return 'B';
  if (s >= 2.0) return 'C';
  return 'D';
}

/* ★ MVP1: GPM 进度条归一 — 按本页数据 max 动态归一 (不许写死量级, 实测 0~1000 均值 14.8) */
function sb2TalentsGpmFill(value, pageMax){
  var v = Number(value) || 0;
  if (v <= 0) return 0;
  if (!pageMax || pageMax <= 0) return 0;
  return Math.min(100, Math.round((v / pageMax) * 100));
}

/* ★ r66 批注⑤ 老大 15:30「价格有点丑」: 表格 GMV 列改用 ¥N万 格式 (跟 sb2TlnFmtWan 同款)
   — 原千分位 ¥5,000,000 阅读体验差 (数 0 太累), 改 ¥500万 一眼看出量级
   — 跟 drawer 内 GMV 显示 / 全模块 KPI 排版一致 (r33 KPI 排版同源)
   — 10000 以下仍用纯数字 (¥8,500 → ¥8500 不带千分位, 跟粉丝列 followersFmt 同款) */
function sb2TalentsFmtGmv(v){
  var n = Number(v) || 0;
  if (n === 0) return '¥0';
  if (n >= 10000) return '¥' + (n / 10000).toFixed(1).replace(/\.0$/, '') + '万';
  return '¥' + Math.round(n);
}
function sb2TalentsFmtFollowers(v){
  var n = Number(v) || 0;
  /* 原型 82,000 千分位 → 统一千分位, 不用万单位 */
  return n.toLocaleString('en-US');
}
function sb2TalentsFmtRating(v){
  var n = Number(v);
  if (!n) return '-';
  return n.toFixed(2);
}

/* ★ fix/talents-hotfix: GMV mini bar 归一 — 跟 GPM 同模式, 用本页 max 动态归一
   apply when: 任何「表格数字列 mini bar」必须按本页 max 动态归一, 禁写死量级 */
function sb2TalentsGmvFill(value, pageMax){
  var v = Number(value) || 0;
  if (v <= 0) return 0;
  if (!pageMax || pageMax <= 0) return 0;
  return Math.min(100, Math.round((v / pageMax) * 100));
}

/* ★ MVP1: HTML 转义 */
function sb2TalentsEsc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return ({'&':'&','<':'<','>':'>','"':'"',"'":'&#39;'})[c];
  });
}

/* ★ MVP1: 单行 HTML 渲染 */
function sb2TalentsRowHtml(t, pageMaxGpm, pageMaxGmv){
  var id = sb2TalentsEsc(t.id || '');
  var name = sb2TalentsEsc(t.name || '-');
  var avatar = t.avatar || '';
  var initial = name.charAt(0) || '?';
  var handleRaw = (t.douyin_id || '').replace(/^@/, '');
  var handle = handleRaw ? '@' + sb2TalentsEsc(handleRaw) : '';
  var category = sb2TalentsEsc(t.category || '-');
  var level = sb2TalentsEsc(t.level || '-');
  var followers = sb2TalentsFmtFollowers(t.followers);
  var gmvRaw = Number(t.total_gmv) || 0;
  var gmv = sb2TalentsFmtGmv(gmvRaw);
  var gmvFill = sb2TalentsGmvFill(gmvRaw, pageMaxGmv);
  var gpm = Number(t.live_gpm) || 0;
  var gpmFill = sb2TalentsGpmFill(gpm, pageMaxGpm);
  var rating = sb2TalentsFmtRating(t.rating_score);
  /* ★ r39-P0: 匹配商品 cell — 三级数据源
     ① _sb2TalentsMatchCount cache (并发池 ai-match limit=1 取的真 total, 26 轮② 教训并发池上限 6)
     ② t.matched_products 数组长度 (server 24h 缓存或历史落库, 协调人已代补 Array.isArray + length 兜底)
     ③ 全无 → '…' + pending class (呼吸动画, 并发池拉完后 patch)
     老大红线「262 行逐行 ai-match 太贵」避规: 复用 talents.matched_products 24h 缓存 (server _handle_get_influencer_matches
     已 populate), 仅 server loadTab/getList 已 populate 过的 talent 不再调 ai-match; 未 populate 过的进并发池 6
     only loss no create: 0 是真值, 不造假 */
  var _matchedArr = Array.isArray(t.matched_products) ? t.matched_products : [];
  var _matchedArrLen = _matchedArr.length;
  var _matchCached = _sb2TalentsMatchCount[t.id];
  var _matchNum = (_matchCached != null) ? _matchCached : _matchedArrLen;
  var _matchHas = (_matchNum > 0);
  var matchedStr = (_matchCached == null && _matchedArrLen === 0) ? '…' : String(_matchNum);
  var matchedClass = 'sb2-talents-num sb2-talents-match-cell' + ((_matchCached == null && _matchedArrLen === 0) ? ' sb2-talents-match-pending' : '');
  var bucket = sb2TalentsRatingBucket(t.rating_score);
  var statusKey = t.cooperation_status || '';
  var statusLabel = _SB2_TALENT_STATUS_LABELS[statusKey] || statusKey || '-';
  var avatarHtml = avatar
    ? '<img src="' + sb2TalentsEsc(avatar) + '" alt="' + name + '" onerror="this.parentNode.textContent=\'' + initial + '\'">'
    : initial;
  var checked = _sb2TalentsSelectedIds[id] ? 'checked' : '';
  /* ★ fix/talents-hotfix: 黑名单行整行沉底置灰 (CSS .sb2-talents-row-blacklist) */
  /* ★ fix/sb2-proto-align (commit 1): 行点击高亮 (CSS .sb2-talents-table tbody tr.selected)
     黑名单行 + 选中行 class 合并 (黑名单优先级低, 选中优先) */
  var classes = [];
  if (statusKey === 'blacklist') classes.push('sb2-talents-row-blacklist');
  if (_sb2TalentsSelectedRowId === id) classes.push('selected');
  var rowClassAttr = classes.length > 0 ? ' class="' + classes.join(' ') + '"' : '';
  return '<tr data-tid="' + id + '"' + rowClassAttr + ' onclick="sb2TalentsToggleRow(\'' + id.replace(/'/g, "\\'") + '\')">'
    + '<td onclick="event.stopPropagation()"><input type="checkbox" class="sb2-talents-checkbox sb2-talents-row-check" data-tid="' + id + '" ' + checked + ' onchange="sb2TalentsToggleOne(\'' + id + '\', this.checked)"></td>'
    + '<td><div class="sb2-talents-talent"><div class="sb2-talents-avatar">' + avatarHtml + '</div>'
    +   '<div><div class="sb2-talents-name">' + name + '</div>'
    +   (handle ? '<div class="sb2-talents-handle">' + handle + '</div>' : '')
    + '</div></div></td>'
    + '<td><span class="sb2-talents-chip">' + category + '</span></td>'
    + '<td><span class="sb2-talents-chip-level">' + level + '</span></td>'
    + '<td class="sb2-talents-num">' + followers + '</td>'
    /* ★ r66 批注⑤ 老大 15:30「价格有点丑」: GMV 列专属视觉
       — 原复用 .sb2-talents-gpm (GPM 同色蓝色), 但 GMV 是金额, GPM 是比率, 数据类型不同不该共用
       — 改用 .sb2-talents-gmv 专属 class: 暖色 var(--sb2-warning) + 加粗金额视觉
       — 视觉层级: 金额字号 13px (比 GPM 12px 突出), 颜色 var(--sb2-warning) 暖色跟数据重要性匹配
       — 不造新组件, 复用 .sb2-talents-gpm 同结构 (flex bar + val), 只换 CSS 变量 */
    + '<td><div class="sb2-talents-gmv" title="总 GMV 原值: ' + gmvRaw + '"><div class="sb2-talents-gmv-bar"><div class="sb2-talents-gmv-fill" style="width:' + gmvFill + '%"></div></div><span class="sb2-talents-gmv-val">' + gmv + '</span></div></td>'
    + '<td><div class="sb2-talents-gpm" title="GPM 原值: ' + gpm + '"><div class="sb2-talents-gpm-bar"><div class="sb2-talents-gpm-fill" style="width:' + gpmFill + '%"></div></div><span class="sb2-talents-gpm-val">' + gpm + '</span></div></td>'
    + '<td class="sb2-talents-num">' + rating + '</td>'
    + '<td class="' + matchedClass + '">' + matchedStr + '</td>'
    + '<td><span class="sb2-talents-rating r-' + bucket + '">' + bucket + '</span></td>'
    + '<td><span class="sb2-talents-status s-' + sb2TalentsEsc(statusKey) + '">' + sb2TalentsEsc(statusLabel) + '</span></td>'
    /* ★ r39-P0: 行操作 cell — AI 匹配弹窗入口 (规格项 2, 仿 22 轮批注③ 居中弹窗 precedent) */
    + '<td class="sb2-talents-cell-action"><button class="sb2-talents-aimatch-btn" onclick="sb2TalentsOpenAiMatch(\'' + id.replace(/'/g, "\\'") + '\')">AI 匹配</button></td>'
    + '</tr>';
}

/* ★ MVP1: 表格渲染 */
function sb2TalentsRenderTable(){
  var tbody = document.getElementById('sb2TalentsTbody');
  if (!tbody) return;
  var list = _sb2TalentsList || [];
  if (list.length === 0){
    tbody.innerHTML = '<tr><td colspan="12" class="sb2-talents-empty"><span class="em">👥</span>暂无达人</td></tr>';
    return;
  }
  var pageMaxGpm = 0;
  var pageMaxGmv = 0;
  for (var i = 0; i < list.length; i++){
    var g = Number(list[i].live_gpm) || 0;
    if (g > pageMaxGpm) pageMaxGpm = g;
    var m = Number(list[i].total_gmv) || 0;
    if (m > pageMaxGmv) pageMaxGmv = m;
  }
  /* ★ fix/sb2-proto-align (commit 1): 首行默认选中 (原型 line 408 tbody first tr selected) */
  if (!_sb2TalentsSelectedRowId && list.length > 0 && list[0].id) {
    _sb2TalentsSelectedRowId = list[0].id;
  }
  var html = '';
  for (var j = 0; j < list.length; j++){
    html += sb2TalentsRowHtml(list[j], pageMaxGpm, pageMaxGmv);
  }
  tbody.innerHTML = html;
}

/* ★ fix/sb2-proto-align (commit 1): 行点击高亮单选
   重复点击同一行取消选中, 切到别行重置 (跟原型互斥单选行为一致)
   注意 checkbox onclick 用 event.stopPropagation() 避免冒泡触发行点击
   ★ r63 项② (老大 13:06 批注②「点达人没有详情」): 行 click 同时开浮层 drawer
     重复点同一行关 drawer, 切到别行换 drawer 内容 (跟 prototype 互斥单选行为一致)
     drawer 内容走 GET /api/talents/{id} 真接口 (字段名铁律 — 抄实测 JSON, 不凭印象)
     数据契约实测 (大丸子 tal_1787213095215_841db3):
       基础: id/name/avatar/douyin_id/real_name/wechat/phone/email/city/level/followers/talent_type/location/agency/tags/bio
       合作: cooperation_status/follow_up_by/next_follow_up_at/follow_up_note/commission_requirement
       数据: fulfillment_score/rating_score/total_gmv/total_products/product_count/total_shops/average_price/live_ratio/video_ratio/avg_live_gmv/live_gpm/video_gpm
       画像: fan_gender/fan_age/fan_city_tier/fan_price_range + video_audience_* 镜像
       OCR:  ocr_raw_fields / ai_analysis / ai_reason / ai_rating / ai_tags */
function sb2TalentsToggleRow(id){
  if (!id) return;
  var wasSelected = (_sb2TalentsSelectedRowId === id);
  _sb2TalentsSelectedRowId = wasSelected ? '' : id;
  sb2TalentsRenderTable();
  if (wasSelected) {
    if (typeof window !== 'undefined' && typeof window.sb2TlnCloseDetail === 'function') window.sb2TlnCloseDetail();
  } else {
    if (typeof window !== 'undefined' && typeof window.sb2TlnOpenDetail === 'function') window.sb2TlnOpenDetail(id);
  }
}

/* ★ MVP1: 三维筛选现场聚合 — 拿到本页列表后, 客户端聚合, 跟知识库 chips 同一招
   三维 AND 联动: 切任一维后其他两维计数从 _sb2TalentsList 重新聚合 (实测 answer: 切维度不重聚合会出幽灵计数) */
function sb2TalentsRenderSide(){
  /* ★ fix/talents-hotfix: 用 _sb2TalentsFacets (服务端全量聚合) 而非 _sb2TalentsList (当前页 50 条)
     之前: 从 _sb2TalentsList 算 → 全部 50 (当前页 limit), 不准
     现在: 服务端 GROUP BY 全量聚合 → 全部 1847/240 等, 跟 _sb2TalentsTotal 一致
     apply when: 任何「侧栏聚合需全库」必须服务端 GROUP BY 一次拿, 禁前端从当前页算 */
  var facets = _sb2TalentsFacets || { rating: {}, category: {}, cooperation: {} };
  var totalCount = _sb2TalentsTotal || 0;
  // 评级维
  var ratingEl = document.getElementById('sb2TalentsSideRating');
  if (ratingEl){
    var ratingHtml = '<div class="sb2-talents-side-item' + (_sb2TalentsFilterRating === '' ? ' active' : '') + '" data-rating="" onclick="sb2TalentsOnSideClick(\'rating\', \'\')"><span>全部</span><span class="sb2-talents-side-count">' + totalCount + '</span></div>';
    var ratingLabels = {'A': 'A 级 (优先)', 'B': 'B 级', 'C': 'C 级', 'D': 'D 级 (未评)'};
    ['A','B','C','D'].forEach(function(k){
      var cnt = (facets.rating && facets.rating[k]) ? facets.rating[k] : 0;
      ratingHtml += '<div class="sb2-talents-side-item' + (_sb2TalentsFilterRating === k ? ' active' : '') + '" data-rating="' + k + '" onclick="sb2TalentsOnSideClick(\'rating\', \'' + k + '\')"><span>' + ratingLabels[k] + '</span><span class="sb2-talents-side-count">' + cnt + '</span></div>';
    });
    // 显示 (空) 桶 (等级未评, 老大说等级空显 - 保持不动)
    var emptyCnt = (facets.rating && facets.rating['(空)']) ? facets.rating['(空)'] : 0;
    if (emptyCnt > 0) {
      ratingHtml += '<div class="sb2-talents-side-item' + (_sb2TalentsFilterRating === 'NONE' ? ' active' : '') + '" data-rating="NONE" onclick="sb2TalentsOnSideClick(\'rating\', \'NONE\')"><span>未评</span><span class="sb2-talents-side-count">' + emptyCnt + '</span></div>';
    }
    ratingEl.innerHTML = ratingHtml;
  }
  // 类目维 (按计数降序, 取前 12 个避免冗长)
  var catEl = document.getElementById('sb2TalentsSideCategory');
  if (catEl){
    var catHtml = '<div class="sb2-talents-side-item' + (_sb2TalentsFilterCategory === '' ? ' active' : '') + '" data-cat="" onclick="sb2TalentsOnSideClick(\'category\', \'\')"><span>全部类目</span><span class="sb2-talents-side-count">' + totalCount + '</span></div>';
    var catCount = facets.category || {};
    var cats = Object.keys(catCount).map(function(k){ return [k, catCount[k]]; });
    cats.sort(function(a, b){ return b[1] - a[1]; });
    var topCats = cats.slice(0, 12);
    topCats.forEach(function(c){
      catHtml += '<div class="sb2-talents-side-item' + (_sb2TalentsFilterCategory === c[0] ? ' active' : '') + '" data-cat="' + sb2TalentsEsc(c[0]) + '" onclick="sb2TalentsOnSideClick(\'category\', \'' + c[0].replace(/'/g, "\\'") + '\')"><span>' + sb2TalentsEsc(c[0]) + '</span><span class="sb2-talents-side-count">' + c[1] + '</span></div>';
    });
    catEl.innerHTML = catHtml;
  }
  // 状态维 (cooperation_status)
  var statusEl = document.getElementById('sb2TalentsSideStatus');
  if (statusEl){
    var statusHtml = '<div class="sb2-talents-side-item' + (_sb2TalentsFilterStatus === '' ? ' active' : '') + '" data-status="" onclick="sb2TalentsOnSideClick(\'status\', \'\')"><span>全部状态</span><span class="sb2-talents-side-count">' + totalCount + '</span></div>';
    var order = ['cooperating', 'communicating', 'available', 'blacklist', 'resting', 'following', 'archived'];
    order.forEach(function(k){
      var cnt = (facets.cooperation && facets.cooperation[k]) ? facets.cooperation[k] : 0;
      var label = _SB2_TALENT_STATUS_LABELS[k] || k;
      statusHtml += '<div class="sb2-talents-side-item' + (_sb2TalentsFilterStatus === k ? ' active' : '') + '" data-status="' + k + '" onclick="sb2TalentsOnSideClick(\'status\', \'' + k + '\')"><span>' + sb2TalentsEsc(label) + '</span><span class="sb2-talents-side-count">' + cnt + '</span></div>';
    });
    statusEl.innerHTML = statusHtml;
  }
}

/* ★ MVP1: 分页 < 1 2 3 … 37 > (offset/limit 真分页)
   ★ fix/sb2-talents-full-restore (commit 1): 分页符号 ← → 改 ‹ › (原型 line 600)
        之前: ASCII 半角左右箭头
        现在: 全角左右单引号 ‹ › (跟原型 ‹ 1 2 3 … 37 › 一致)
        末尾总页数 N=总页数 totalPages (不是总条数 total, 原型 line 600 的 37 是总页数)
        移动端 820px 宽度退化: 只显 ‹ 1 … N › (省略中间页码, 避免溢出)
        删末尾「共 N 条」(原型底栏无, 只显 ‹ 1 2 3 … N ›) */
function sb2TalentsRenderPager(){
  var pager = document.getElementById('sb2TalentsPager');
  if (!pager) return;
  var total = _sb2TalentsTotal;
  var page = _sb2TalentsPage;
  var limit = _sb2TalentsLimit;
  var totalPages = Math.max(1, Math.ceil(total / limit));
  if (totalPages <= 1){
    /* 单页: 只显 ‹ › 禁用 + 总页数 1 (无页码序列) */
    pager.innerHTML = '<button disabled>‹</button><button disabled>›</button>';
    return;
  }
  var html = '';
  html += '<button ' + (page <= 1 ? 'disabled' : '') + ' onclick="sb2TalentsGoPage(' + (page - 1) + ')">‹</button>';
  var pages = [];
  /* 移动端 820px 退化: 只显 1 + … + N (省略中间所有页码) */
  var isNarrow = (typeof window !== 'undefined' && window.innerWidth && window.innerWidth < 1100);
  if (isNarrow && totalPages > 5){
    pages.push(1);
    pages.push('…');
    pages.push(totalPages);
  } else if (totalPages <= 7){
    for (var p = 1; p <= totalPages; p++) pages.push(p);
  } else {
    pages.push(1);
    if (page > 4) pages.push('…');
    var start = Math.max(2, page - 1);
    var end = Math.min(totalPages - 1, page + 1);
    for (var p2 = start; p2 <= end; p2++) pages.push(p2);
    if (page < totalPages - 3) pages.push('…');
    pages.push(totalPages);
  }
  pages.forEach(function(x){
    if (x === '…') html += '<span class="ellipsis">…</span>';
    else html += '<button class="' + (x === page ? 'active' : '') + '" onclick="sb2TalentsGoPage(' + x + ')">' + x + '</button>';
  });
  html += '<button ' + (page >= totalPages ? 'disabled' : '') + ' onclick="sb2TalentsGoPage(' + (page + 1) + ')">›</button>';
  pager.innerHTML = html;
}

/* ★ MVP1: 选中状态同步到 footer 计数 + 批量提示文案 (原型 line 596-598: 选中行可批量: 加入任务 / 导出 / 指派员工)
   ★ fix/sb2-proto-align (commit 3 修): footer 保留总条数文案, 批量操作条独立渲染 (commit 1 footer 一行字不显眼, 老大打回)
   ★ fix/sb2-talents-full-restore (commit 1): 删「共 N 位达人」(原型底栏无), 改常驻批量提示文案
        之前: n=0 「共 N 位达人」 + n>0 「已选 N 行 · 下方操作条批量处理」
        现在: n=0 跟 n>0 同一文案「选中行可批量: 加入任务 / 导出 / 指派员工」(常驻)
        删「共 N 位达人」避免跟页头副标题重复 (页头已有 N 位在库文案)
        批量条 (commit 3 slide-in) 仍保留 (选中才出现, 常态视觉等于原型, 功能有用) */
function sb2TalentsUpdateFooter(){
  var el = document.getElementById('sb2TalentsFooterLeft');
  if (!el) return;
  /* 常驻批量提示文案 (不依赖选中数, 跟原型 line 596 一致) */
  el.textContent = '选中行可批量: 加入任务 / 导出 / 指派员工';
  var checkAll = document.getElementById('sb2TalentsCheckAll');
  if (checkAll){
    var keys = Object.keys(_sb2TalentsSelectedIds || {});
    var n = keys.length;
    var listLen = (_sb2TalentsList || []).length;
    checkAll.checked = listLen > 0 && n === listLen;
    checkAll.indeterminate = n > 0 && n < listLen;
  }
  /* 同步批量操作条显隐 + 计数 */
  sb2TalentsUpdateBatchBar();
}

/* ★ fix/sb2-proto-align (commit 3 修): 批量操作条显隐 + 计数
   n=0 隐藏 (display:none 默认), n>0 显示 (.on class 触发 display:flex)
   老大要求: 选中行后出现的操作条, 文案照抄 (原型 line 600 footer) */
function sb2TalentsUpdateBatchBar(){
  var bar = document.getElementById('sb2TalentsBatchBar');
  if (!bar) return;
  var keys = Object.keys(_sb2TalentsSelectedIds || {});
  var n = keys.length;
  var cntEl = document.getElementById('sb2TalentsBatchCount');
  if (cntEl) cntEl.textContent = String(n);
  bar.classList.toggle('on', n > 0);
}

/* ★ fix/sb2-proto-align (commit 3 修): 取消选中 (清空 _sb2TalentsSelectedIds + 同步 UI) */
function sb2TalentsClearSelection(){
  _sb2TalentsSelectedIds = {};
  document.querySelectorAll('.sb2-talents-row-check').forEach(function(cb){ cb.checked = false; });
  sb2TalentsUpdateFooter();
}

/* ★ fix/sb2-proto-align (commit 1): 筛选下拉面板 toggle + 按钮文字显示已选数量
   选中状态 3 维 (rating/category/status) 任一非空, 按钮显示「筛选 · N ▾」
   全部清空回退「筛选 ▾」
   ★ fix/sb2-proto-align (commit 3 修): 改格式「筛选 · N ▾」(老大拍板 · 间隔号) 不是「筛选 (N) ▾」 */
function sb2TalentsToggleFilter(){
  var pop = document.getElementById('sb2TalentsFilterPop');
  if (!pop) return;
  pop.classList.toggle('on');
  /* 关闭面板后失焦, 避免与 ESC 键冲突 */
  var btn = document.getElementById('sb2TalentsFilterBtn');
  if (btn && pop.classList.contains('on') && document.activeElement) {
    document.activeElement.blur();
  }
}
/* ★ fix/sb2-proto-align (commit 1+3): 更新筛选按钮文字 (已选数量回显)
   之前: 「筛选 (N) ▾」括号, 老大打回
   现在: 「筛选 · N ▾」间隔号 · (老大拍板的样板)
   ★ apply when: 任何「按钮文字显示已选数量」格式必须先看老大样板, 不要自己递归 */
function sb2TalentsUpdateFilterBtn(){
  var btn = document.getElementById('sb2TalentsFilterBtn');
  if (!btn) return;
  var selectedCount = 0;
  if (_sb2TalentsFilterRating) selectedCount++;
  if (_sb2TalentsFilterCategory) selectedCount++;
  if (_sb2TalentsFilterStatus) selectedCount++;
  btn.textContent = selectedCount > 0 ? ('筛选 · ' + selectedCount + ' ▾') : '筛选 ▾';
}

/* ★ MVP1: 切换侧栏维度 — 三维 AND 联动, 切任一维触发重载
   ★ fix/sb2-proto-align (commit 1): 选完关下拉 + 更新按钮文字 (老大要求选中后按钮显示已选数量) */
function sb2TalentsOnSideClick(dim, val){
  if (dim === 'rating') _sb2TalentsFilterRating = val;
  else if (dim === 'category') _sb2TalentsFilterCategory = val;
  else if (dim === 'status') _sb2TalentsFilterStatus = val;
  _sb2TalentsPage = 1;
  var pop = document.getElementById('sb2TalentsFilterPop');
  if (pop) pop.classList.remove('on');
  sb2TalentsUpdateFilterBtn();
  sb2TalentsLoad();
  /* 〔fix/sb2-side-restore commit 4 D1c 作用域墙〕守卫改 window.* + 强制重渲
     修前 bug: 点了「已合作」表格真过滤 (9 行 ✓), 但侧栏 active 不动
     sb2TalentsLoad 末尾 D1b 重渲在 IIFE 私有作用域下恒跳过
     修法: 这里显式同步触发 window.renderSideFor 重渲, 不依赖 D1b 异步触发
     (commit 4 D1b 守卫已改 window.*, 但 D1c 是点击同步路径, 显式更稳) */
  if (typeof window !== 'undefined' && typeof window.renderSideFor === 'function') {
    window.renderSideFor('influencers');
  }
}

/* ★ MVP1: 搜索 (debounce 300ms, 接 q 参数, name/douyin_id/bio LIKE)
   ★ fix/sb2-talents-full-restore (commit 1): 删 hero-search + side-search DOM 元素后,
        getElementById 拿不到 null 会报错。sb2TalentsOnSearch 仍保留 (搜索走 ⌘K 命令面板后还会回来),
        但 DOM 同步引用必须 if (!el) return 兜底, 防「元素没了 console 报错」
        状态变量 _sb2TalentsSearchQ 保留不删 (防后续加回时重写) */
function sb2TalentsOnSearch(v){
  clearTimeout(_sb2TalentsSearchTimer);
  _sb2TalentsSearchTimer = setTimeout(function(){
    _sb2TalentsSearchQ = (v || '').trim().toLowerCase();
    // 同步两个 input (元素可能已删, getElementById 兜底)
    var sideInput = document.getElementById('sb2TalentsSideSearch');
    var heroInput = document.getElementById('sb2TalentsHeroSearch');
    if (sideInput && sideInput.value !== v) sideInput.value = v;
    if (heroInput && heroInput.value !== v) heroInput.value = v;
    _sb2TalentsPage = 1;
    sb2TalentsLoad();
  }, 300);
}

/* ★ MVP1: 翻页 */
function sb2TalentsGoPage(p){
  if (p < 1 || p > Math.ceil(_sb2TalentsTotal / _sb2TalentsLimit)) return;
  _sb2TalentsPage = p;
  sb2TalentsLoad();
}

/* ★ MVP1: 全选/单选 */
function sb2TalentsToggleAll(checked){
  _sb2TalentsSelectedIds = {};
  if (checked){
    (_sb2TalentsList || []).forEach(function(t){
      if (t.id) _sb2TalentsSelectedIds[t.id] = true;
    });
  }
  // 同步行 checkbox
  document.querySelectorAll('.sb2-talents-row-check').forEach(function(cb){
    cb.checked = checked;
  });
  sb2TalentsUpdateFooter();
}
function sb2TalentsToggleOne(id, checked){
  if (checked) _sb2TalentsSelectedIds[id] = true;
  else delete _sb2TalentsSelectedIds[id];
  sb2TalentsUpdateFooter();
}

/* ★ MVP1: 占位 toast (筛选/导出/录入 — MVP3/MVP5 才是真动作) */
function sb2TalentsToast(msg){
  if (typeof showToast === 'function') showToast(msg, 'info');
  else { try { console.log('[sb2-talents]', msg); } catch(e){} }
}

/* 〔28 轮 2026-10-06〕hero 标题动态化 (老大「达人库的hero 标题也弄成动态化」)
   修前: hero h1 静态「达人库」, 跟侧栏标题重复 (22/27 轮知识库/规律库同款问题)
   修后: 类目 > 状态 > 评级, 全不选 → 「全部达人」; 标签口径跟侧栏一致
   调用点: sb2TalentsLoad 顶部 (侧栏点击/筛选面板/搜索全经过, 不造第二个入口) */
function sb2TalentsUpdateHeroTitle(){
  var hero = document.getElementById('sb2TalentsHeroTitle');
  if (!hero) return;
  if (_sb2TalentsFilterCategory) { hero.textContent = _sb2TalentsFilterCategory; return; }
  if (_sb2TalentsFilterStatus) {
    var _sl = (typeof _SB2_TALENT_STATUS_LABELS === 'object' && _SB2_TALENT_STATUS_LABELS) || {};
    hero.textContent = _sl[_sb2TalentsFilterStatus] || _sb2TalentsFilterStatus;
    return;
  }
  if (_sb2TalentsFilterRating) {
    var _rl = { 'A':'A 级 (优先)', 'B':'B 级', 'C':'C 级', 'D':'D 级 (未评)', 'NONE':'未评' };
    hero.textContent = _rl[_sb2TalentsFilterRating] || _sb2TalentsFilterRating;
    return;
  }
  hero.textContent = '全部达人';
}

/* ★ MVP1: 主加载 — 端点契约 GET /api/talents?xxx */
async function sb2TalentsLoad(){
  sb2TalentsUpdateHeroTitle();
  var tbody = document.getElementById('sb2TalentsTbody');
  var heroSub = document.getElementById('sb2TalentsHeroSub');
  if (tbody) tbody.innerHTML = '<tr><td colspan="12" class="sb2-talents-empty"><span class="em">⏳</span>载入中…</td></tr>';
  if (heroSub) heroSub.textContent = '载入中…';
  var params = [];
  params.push('limit=' + _sb2TalentsLimit);
  params.push('offset=' + ((_sb2TalentsPage - 1) * _sb2TalentsLimit));
  if (_sb2TalentsSearchQ) params.push('q=' + encodeURIComponent(_sb2TalentsSearchQ));
  if (_sb2TalentsFilterStatus) params.push('cooperation=' + encodeURIComponent(_sb2TalentsFilterStatus));
  if (_sb2TalentsFilterCategory) params.push('talent_category=' + encodeURIComponent(_sb2TalentsFilterCategory));
  // rating 维不走后端 SUBSTR level (实测 SUBSTR 选不出), 改前端 filter
  var url = '/api/talents?' + params.join('&');
  try {
    var resp = await apiFetch(url);
    if (!resp || !resp.ok){
      if (tbody) tbody.innerHTML = '<tr><td colspan="12" class="sb2-talents-empty"><span class="em">⚠️</span>载入失败 (HTTP ' + (resp ? resp.status : 'no response') + ')</td></tr>';
      if (heroSub) heroSub.textContent = '载入失败';
      return;
    }
    var data = await resp.json();
    var list = (data && Array.isArray(data.talents)) ? data.talents : [];
    // 前端 rating 维过滤 (老大实测 SUBSTR 不行)
    if (_sb2TalentsFilterRating){
      list = list.filter(function(t){ return sb2TalentsRatingBucket(t.rating_score) === _sb2TalentsFilterRating; });
    }
    _sb2TalentsList = list;
    _sb2TalentsTotal = (data && typeof data.total === 'number') ? data.total : list.length;
    /* ★ fix/talents-hotfix: 服务端响应 facets 全量聚合 (跟 total 同口径)
       之前: 侧栏从 _sb2TalentsList (当前页 50 条) 算 → 全部 50, 不准
       现在: 服务端 GROUP BY 全量 → 全部 1847/240 等, 跟 total 一致 */
    _sb2TalentsFacets = (data && data.facets) ? data.facets : null;
    /* ★ fix/sb2-side-restore commit 1: 镜像到 window._sb2TalentFacetCounts (renderer 期望 plural key)
       原型侧栏 line 729-734 评级/类目/状态 三维带计数 — 全局变量跨模块可读
       server 返回 {rating, category, cooperation} → 客户端映射 {ratings, categories, statuses}
       未加载 (sb2TalentsLoad 未跑过 / fetch 失败) 时 null, renderer 兜底空对象 + 显「–」
       〔fix/sb2-side-restore commit 5 任务 C〕侧栏计数全局化:
         server 端 facets 是 WHERE 过滤后 GROUP BY (e0b8e28 已拍),
         修前 bug: 点「已合作」→ server 返合作桶 facets=9 (过滤后), 侧栏 badge 跟着变成 9 → 试投中/待联系变 0,
                  原型侧栏计数是全局静态 (107 全部), 不该跟过滤集变
         修法: 检测 hasFilter, 仅无过滤时才覆盖 _sb2TalentFacetCounts
              有过滤时保持旧值 (上次无过滤的全局快照), 侧栏 renderer 读 window._sb2TalentFacetCounts 不变 */
    var hasFilter = !!( _sb2TalentsFilterRating || _sb2TalentsFilterCategory || _sb2TalentsFilterStatus || _sb2TalentsSearchQ );
    if (!hasFilter) {
      window._sb2TalentFacetCounts = _sb2TalentsFacets ? {
        total: _sb2TalentsTotal,
        ratings: _sb2TalentsFacets.rating || {},
        categories: _sb2TalentsFacets.category || {},
        statuses: _sb2TalentsFacets.cooperation || {}
      } : null;
    }
    /* 〔fix/sb2-side-restore commit 4 D1b 作用域墙〕守卫改 window.*
       修前 bug: sb2-shell-js IIFE 内 renderSideFor / SB2_CURRENT_MODULE 私有,
                typeof SB2_CURRENT_MODULE !== 'undefined' 恒 false 静默跳过
       修法: 跨块访问走 window.* (commit 4 已挂 window, 见 line 46088 后注释) */
    if (typeof window !== 'undefined' && window.SB2_CURRENT_MODULE === 'influencers'
        && typeof window.renderSideFor === 'function') {
      window.renderSideFor('influencers');
    }
    // 清掉不在当前页的选中
    var pageIds = {};
    list.forEach(function(t){ if (t.id) pageIds[t.id] = true; });
    Object.keys(_sb2TalentsSelectedIds).forEach(function(k){ if (!pageIds[k]) delete _sb2TalentsSelectedIds[k]; });
    if (heroSub) heroSub.textContent = _sb2TalentsTotal + ' 位在库 · 按评级与类目分层';
    sb2TalentsUpdateFilterBtn();
    sb2TalentsRenderSide();
    sb2TalentsRenderTable();
    sb2TalentsRenderPager();
    sb2TalentsUpdateFooter();
    /* ★ r39-P0: 启动匹配商品数并发池 (P0 人货撮合链项 1)
       老大红线「262 行全表逐行 ai-match 太贵」避规:
       ① 先看 t.matched_products 数组是否已 populate (server 24h 缓存或历史, 协调人已代补 Array.isArray + length 兜底)
       ② 未 populate (length=0) 的才进并发池 — 上限 6, 调 ai-match talent-to-product limit=1, 取 total
       ③ 拉完后 sb2TalentsPatchMatchCell 单 td 替换 (不重渲整表, 26 轮② 教训)
       切页/筛选: cache 保留 (跨 tab 不丢, 切回秒显示) */
    sb2TalentsStartMatchCount(list);
    _sb2TalentsLoaded = true;
  } catch (e) {
    if (tbody) tbody.innerHTML = '<tr><td colspan="12" class="sb2-talents-empty"><span class="em">⚠️</span>载入异常: ' + sb2TalentsEsc(e && e.message || String(e)) + '</td></tr>';
    if (heroSub) heroSub.textContent = '载入异常';
  }
}

/* ★ r39-P0: 匹配商品数并发池 + 单 cell patch
   - sb2TalentsFetchOneMatchCount(tid): 单 talent 异步拉, dedupe + 错误兜底 0 + 写回 t.matched_products 数组 (下轮免调)
   - sb2TalentsStartMatchCount(list): 无界并发池, 上限 6, 跳过已 populate 的 talent (老大 262 行逐行 ai-match 太贵避规)
   - sb2TalentsPatchMatchCell(tid, total): 单 td textContent + 移除 pending, 不重渲整表 (26 轮② 教训)
   - apply when: 列表列需逐行异步拉数据 = 并发池 + 单 cell patch, 禁 await + renderTable */
async function sb2TalentsFetchOneMatchCount(tid){
  if (!tid) return null;
  if (_sb2TalentsMatchFetching[tid]) return null;
  _sb2TalentsMatchFetching[tid] = true;
  try {
    var resp = await apiFetch('/api/ai-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'talent-to-product', talentId: tid, limit: 1 })
    });
    if (!resp || !resp.ok) {
      _sb2TalentsMatchCount[tid] = 0;
      sb2TalentsPatchMatchCell(tid, 0);
      return 0;
    }
    var data = await resp.json();
    var total = (data && typeof data.total === 'number') ? data.total : 0;
    _sb2TalentsMatchCount[tid] = total;
    /* 写回 _sb2TalentsList 对应行 matched_products (虚拟数组, 不回 server, 只保前端 cache 一致) */
    if (Array.isArray(_sb2TalentsList)) {
      for (var i = 0; i < _sb2TalentsList.length; i++) {
        if (_sb2TalentsList[i] && _sb2TalentsList[i].id === tid) {
          if (!Array.isArray(_sb2TalentsList[i].matched_products)) _sb2TalentsList[i].matched_products = [];
          _sb2TalentsList[i].matched_products.length = total;   // 长度当标记位 (detail 非占位)
          break;
        }
      }
    }
    sb2TalentsPatchMatchCell(tid, total);
    return total;
  } catch (e) {
    _sb2TalentsMatchCount[tid] = 0;
    sb2TalentsPatchMatchCell(tid, 0);
    return 0;
  } finally {
    delete _sb2TalentsMatchFetching[tid];
  }
}

function sb2TalentsStartMatchCount(list){
  if (!Array.isArray(list) || list.length === 0) return Promise.resolve();
  /* 老大红线避规: 跳过已 populate 的 talent (matched_products.length > 0), 仅未 populate 的进并发池
     server _handle_get_influencer_matches 24h 缓存 populate matched_products (line 17667) */
  var queue = list.filter(function(t){
    if (!t || !t.id) return false;
    var arr = Array.isArray(t.matched_products) ? t.matched_products : [];
    return arr.length === 0 && !_sb2TalentsMatchCount.hasOwnProperty(t.id);
  });
  if (queue.length === 0) return Promise.resolve();
  var inFlight = 0;
  return new Promise(function(resolve){
    function next(){
      while (inFlight < _sb2TalentsMatchPoolMax && queue.length > 0) {
        var t = queue.shift();
        inFlight++;
        sb2TalentsFetchOneMatchCount(t.id).then(function(){
          inFlight--;
          if (queue.length > 0) next();
          else if (inFlight === 0) resolve();
        });
      }
      if (queue.length === 0 && inFlight === 0) resolve();
    }
    next();
  });
}

function sb2TalentsPatchMatchCell(tid, total){
  var row = document.querySelector('tr[data-tid="' + tid + '"]');
  if (!row) return;
  var td = row.querySelector('.sb2-talents-match-cell');
  if (!td) return;
  td.textContent = String(total);
  td.classList.remove('sb2-talents-match-pending');
}

/* ★ MVP1: 入口 (切模块时调一次) */
function sb2TalentsInit(){
  if (!_sb2TalentsLoaded) sb2TalentsLoad();
}

/* ============================================================
 * r39-P0: 达人侧 AI 匹配弹窗 (项 2 + 项 4 接线)
 * 规格: docs/design-spec/r39-match-chain.md §二·项2 + §二·项4
 * 弹窗: 仿 sb2-pds-detail 居中 720 + sb2-pds-rise 动画 precedent (22 轮批注③ 口径)
 * 调用: POST /api/ai-match direction=talent-to-product limit=10
 * 字段真名 (字段名铁律, server line 20132-20141):
 *   matches[].talent/product + score + matchPercent + ruleScore + aiScore
 *           + reasons (string[]) + aiReason (string) + is_ai_recommended (≥75)
 * 响应顶层: matches + total + ai_scored + degraded (bool) + degrade_reason
 *           (None / 'timeout' / 'empty_result' / 'parse_error' / 'no_agent' / 'no_candidates')
 * 项 4: 弹窗内「生成合作方案」→ POST /api/proposals (规格 §二·项4)
 *   - 与规格偏差项: server line 18870-18873 强制 localhost_agent_id (X-Agent-Id),
 *     远程 admin 用户 POST 会 403 (待协调人放宽鉴权或新增专用接口)
 *   - 小路执行路径: 带 X-Agent-Id 头试 POST, 失败兜底显示「待协调人放宽鉴权」,
 *     不静默吞错 (跟 feat/p03-quality-loop 「不再吞错」 21 轮同款)
 * apply when: 任何「IIFE 内函数给 HTML onclick 用」= 主动 window.X = X 暴露 (r38 IIFE 踩坑)
 * ============================================================ */

/* 弹窗状态 (避免全局污染) */
var _sb2TlnAimatchState = {
  talentId: null,
  matches: [],
  degraded: false,
  degrade_reason: null,
  inFlight: false
};

function sb2TalentsOpenAiMatch(talentId){
  if (!talentId) return;
  var overlay = document.getElementById('sb2TlnAimatchOverlay');
  var title = document.getElementById('sb2TlnAimatchTitle');
  var sub = document.getElementById('sb2TlnAimatchSub');
  var body = document.getElementById('sb2TlnAimatchBody');
  var degrade = document.getElementById('sb2TlnAimatchDegrade');
  if (!overlay || !body) return;
  /* 找达人名 (从 cache) */
  var tlist = (typeof _sb2TalentsList !== 'undefined') ? _sb2TalentsList : [];
  var t = tlist.find(function(x){ return x && x.id === talentId; });
  var tName = t ? (t.name || talentId) : talentId;
  if (title) title.textContent = 'AI 匹配商品 · ' + tName;
  if (sub) sub.textContent = 'POST /api/ai-match · talent-to-product · TOP 10';
  if (degrade) degrade.style.display = 'none';
  body.innerHTML = '<div class="sb2-tln-aimatch-loading"><div class="spinner"></div>AI 正在分析匹配…（最长约 1 分钟）</div>';
  overlay.classList.add('open');
  _sb2TlnAimatchState.talentId = talentId;
  _sb2TlnAimatchState.matches = [];
  _sb2TlnAimatchState.degraded = false;
  _sb2TlnAimatchState.degrade_reason = null;
  _sb2TlnAimatchState.inFlight = true;
  sb2TalentsFetchAiMatch(talentId);
}

function sb2TalentsCloseAiMatch(){
  var overlay = document.getElementById('sb2TlnAimatchOverlay');
  if (overlay) overlay.classList.remove('open');
  _sb2TlnAimatchState.talentId = null;
  _sb2TlnAimatchState.inFlight = false;
}

async function sb2TalentsFetchAiMatch(talentId){
  var body = document.getElementById('sb2TlnAimatchBody');
  var degrade = document.getElementById('sb2TlnAimatchDegrade');
  try {
    var resp = await apiFetch('/api/ai-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'talent-to-product', talentId: talentId, limit: 10 })
    });
    if (!resp || !resp.ok) {
      _sb2TlnAimatchState.inFlight = false;
      if (body) body.innerHTML = '<div class="sb2-tln-aimatch-empty"><span class="sb2-tln-aimatch-empty-icon">⚠️</span>权限不足或网络错误, 请重试 (HTTP ' + (resp ? resp.status : 'no resp') + ')</div>';
      return;
    }
    var data = await resp.json();
    _sb2TlnAimatchState.matches = (data && Array.isArray(data.matches)) ? data.matches : [];
    _sb2TlnAimatchState.degraded = !!(data && data.degraded);
    _sb2TlnAimatchState.degrade_reason = (data && data.degrade_reason) || null;
    _sb2TlnAimatchState.inFlight = false;
    if (degrade && _sb2TlnAimatchState.degraded) {
      degrade.textContent = '⚠️ AI 暂不可用, 已展示规则匹配结果 (' + _sb2TlnAimatchState.degrade_reason + ')';
      degrade.style.display = '';
    } else if (degrade) {
      degrade.style.display = 'none';
    }
    sb2TalentsRenderAiMatches();
  } catch (e) {
    _sb2TlnAimatchState.inFlight = false;
    if (body) body.innerHTML = '<div class="sb2-tln-aimatch-empty"><span class="sb2-tln-aimatch-empty-icon">⚠️</span>网络错误: ' + (e && e.message || e) + '</div>';
  }
}

function sb2TalentsRenderAiMatches(){
  var body = document.getElementById('sb2TlnAimatchBody');
  if (!body) return;
  var matches = _sb2TlnAimatchState.matches || [];
  if (matches.length === 0) {
    body.innerHTML = '<div class="sb2-tln-aimatch-empty"><span class="sb2-tln-aimatch-empty-icon">🪺</span>暂无匹配商品</div>';
    return;
  }
  var html = '<div class="sb2-tln-aimatch-list">';
  for (var i = 0; i < matches.length; i++) {
    var m = matches[i];
    var p = m.product || {};
    var name = p.name || '-';
    var cat = p.category || '';
    var ruleScore = (typeof m.ruleScore === 'number') ? m.ruleScore : 0;
    var aiScore = (typeof m.aiScore === 'number') ? m.aiScore : 0;
    var finalScore = (typeof m.score === 'number') ? m.score : ruleScore;
    var reasons = Array.isArray(m.reasons) ? m.reasons : [];
    var aiReason = m.aiReason || '';
    var allReasons = (aiReason ? [aiReason] : []).concat(reasons).slice(0, 4);
    var reasonsHtml = allReasons.length > 0
      ? '<ul class="sb2-tln-aimatch-reasons">' + allReasons.map(function(r){ return '<li>' + sb2TalentsEsc(r) + '</li>'; }).join('') + '</ul>'
      : '<div class="sb2-tln-aimatch-reasons">无推荐理由</div>';
    /* ★ 字段名铁律: matches[i].product.id 是真 id, matches[i].product.name 是真名
       (server line 19979 / 20095 / 20132-20141 真代码读源) */
    var prodId = p.id || '';
    var prodEscId = sb2TalentsEsc(prodId);
    var prodEscName = sb2TalentsEsc(name);
    var catEsc = sb2TalentsEsc(cat);
    var isTop = (i === 0);
    html += '<div class="sb2-tln-aimatch-card" data-pid="' + prodEscId + '">'
      + '<div class="sb2-tln-aimatch-card-head">'
      +   '<div class="sb2-tln-aimatch-card-name">' + prodEscName + '</div>'
      +   (cat ? '<div class="sb2-tln-aimatch-card-cat">' + catEsc + '</div>' : '')
      + '</div>'
      + '<div class="sb2-tln-aimatch-scores">'
      +   '<span class="score-rule">规则分 ' + ruleScore.toFixed(1) + '</span>'
      +   '<span class="score-ai">AI 分 ' + aiScore.toFixed(1) + '</span>'
      +   '<span>综合 ' + finalScore.toFixed(1) + '</span>'
      + '</div>'
      + reasonsHtml
      + '<div class="sb2-tln-aimatch-card-foot">'
      +   '<button onclick="sb2TalentsSubmitMatchProposal(\'' + prodEscId + '\')">生成合作方案</button>'
      +   (isTop ? '<button class="primary" onclick="sb2TalentsSubmitMatchProposal(\'' + prodEscId + '\')">TOP 1 快速生成</button>' : '')
      + '</div>'
      + '</div>';
  }
  html += '</div>';
  body.innerHTML = html;
}

/* 项 4: 生成合作方案 → POST /api/proposals
   与规格偏差项: server line 18870-18873 强制 localhost_agent_id (X-Agent-Id),
   远程 admin 用户 POST 会 403 — 带 X-Agent-Id 头试 POST, 失败兜底显示「待协调人放宽鉴权」
   apply when: 任何「spec 要 POST /api/proposals 但鉴权卡死」= 显式带 X-Agent-Id 头 + 失败兜底 */
async function sb2TalentsSubmitMatchProposal(productId){
  if (!productId) return;
  var talentId = _sb2TlnAimatchState.talentId;
  if (!talentId) {
    if (typeof showToast === 'function') showToast('请先打开 AI 匹配弹窗', 'warn');
    return;
  }
  /* r39-7 智能派单中继 (老大拍板 B): /api/proposals 保持 AI 专属硬闸,
     真人走 /api/collaboration-propose, 服务端挑 AI 员工(优先商务)代建方案 */
  var match = (_sb2TlnAimatchState.matches || []).find(function(x){
    return x && x.product && x.product.id === productId;
  });
  var score = match ? (match.matchPercent || match.score || '') : '';
  try {
    var resp = await apiFetch('/api/collaboration-propose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ talentId: talentId, productId: productId, score: score })
    });
    if (resp && resp.ok) {
      var data = null;
      try { data = await resp.json(); } catch (e) { data = null; }
      var agentName = (data && data.dispatched_agent) ? data.dispatched_agent : 'AI 员工';
      if (data && data.duplicate) {
        if (typeof showToast === 'function') showToast('该合作方案已存在, 未重复创建', 'info');
      } else {
        if (typeof showToast === 'function') showToast('✅ 已派给 ' + agentName + ' 生成方案, 待审批', 'success');
      }
      sb2TalentsCloseAiMatch();
    } else if (resp && (resp.status === 403 || resp.status === 401)) {
      /* 不静默吞错 (跟 feat/p03-quality-loop 「不再吞错」 21 轮同款) */
      if (typeof showToast === 'function') showToast('⚠️ 请先登录后再生成合作方案 (HTTP ' + resp.status + ')', 'warn');
    } else {
      if (typeof showToast === 'function') showToast('⚠️ 生成失败 (HTTP ' + (resp ? resp.status : '?') + ')', 'warn');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('⚠️ 网络错误: ' + (e && e.message || e), 'warn');
  }
}
/* ★ SEV1 教训: 跨脚本块调用挂 window, 函数声明 vs typeof 检查保底 */
window.sb2TalentsInit = sb2TalentsInit;
window.sb2TalentsLoad = sb2TalentsLoad;
window.sb2TalentsOnSearch = sb2TalentsOnSearch;
window.sb2TalentsOnSideClick = sb2TalentsOnSideClick;
window.sb2TalentsGoPage = sb2TalentsGoPage;
window.sb2TalentsToggleAll = sb2TalentsToggleAll;
window.sb2TalentsToggleOne = sb2TalentsToggleOne;
/* ★ fix/sb2-proto-align (commit 1): 跨块挂 window 防止 SEV1 (script 块内 inline 调用必走 window) */
window.sb2TalentsToggleFilter = sb2TalentsToggleFilter;
window.sb2TalentsToggleRow = sb2TalentsToggleRow;
window.sb2TalentsUpdateFilterBtn = sb2TalentsUpdateFilterBtn;
/* ★ fix/sb2-proto-align (commit 3 修): 批量操作条函数挂 window (onclick 内联调用必走 window) */
window.sb2TalentsUpdateBatchBar = sb2TalentsUpdateBatchBar;
window.sb2TalentsClearSelection = sb2TalentsClearSelection;
window.sb2TalentsToast = sb2TalentsToast;
/* ★ r39-P0: 匹配商品 cell 配套函数挂 window (r38 IIFE 教训: 跨块调用必走 window) */
window.sb2TalentsFetchOneMatchCount = sb2TalentsFetchOneMatchCount;
window.sb2TalentsStartMatchCount = sb2TalentsStartMatchCount;
window.sb2TalentsPatchMatchCell = sb2TalentsPatchMatchCell;
/* ★ r39-P0: AI 匹配弹窗配套函数挂 window (r38 IIFE 教训: HTML onclick 属性必走 window 暴露)
   弹窗走 POST /api/ai-match (server 真字段: matches[].talent/product + ruleScore + aiScore + reasons + aiReason)
   apply when: 任何「IIFE 内函数给 HTML onclick 用」必须 window.* 主动暴露 (r38 IIFE 跨域铁律) */
window.sb2TalentsOpenAiMatch = sb2TalentsOpenAiMatch;
window.sb2TalentsCloseAiMatch = sb2TalentsCloseAiMatch;
window.sb2TalentsSubmitMatchProposal = sb2TalentsSubmitMatchProposal;

/* ============================================================
 * ★ r63 项② (老大 13:06 批注②「点达人没有详情」): sb2 达人库行/头像点击 → 浮层 drawer
 *   仿 sb2-pds-detail precedent 居中 960 弹窗 (比商品详情略宽, 7 tab 内容)
 *   命名空间 sb2Tln-* (talents 缩写 tln, 跟 sb2-pds-* 商品平行的命名空间)
 *   复用既有 renderTalentDetail 7 tab 语义结构 (概况/带货/粉丝/商品/跟进记录/待办/知识库)
 *     字段名铁律 (第 1 条): 抄实测 /api/talents/{id} 真字段 (大丸子 tal_1787213095215_841db3)
 *     7 panel 渲染函数独立写 (不依赖 legacy renderTalentDetail, 避免 legacy DOM id 冲突)
 *     3 panel 真内容 (概况/带货/粉丝) + 4 panel 占位 (商品/跟进记录/待办/知识库 待后续派单补齐)
 *   IIFE onclick 跨域铁律 (r38): drawer 函数挂 window + onclick 属性必走 window.* (跟 sb2-pds-findtalent precedent 一致)
 *   行高亮选中态: 复用 .sb2-talents-table tbody tr.selected (line 11830 CSS 已存在, 不用新加)
 *   Esc / ✕ / 遮罩关闭: drawer 自带事件绑定 (派单明文 3 个关闭入口)
 * ============================================================ */
var _sb2TlnDetailCurrentId = '';      // 当前 drawer 打开的 talent id
var _sb2TlnDetailCurrentData = null;   // 当前 drawer 缓存的 talent data
var _sb2TlnDetailCurrentTab = 'overview';
var _sb2TlnDetailLoaded = false;

/* HTML 转义 (老模块 sb2TalentsEsc 是内联, drawer 独立一份) */
function sb2TlnEsc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}

/* 数字格式: 1.2万 / 22.5万 / 500万 等 */
function sb2TlnFmtWan(n){
  var num = Number(n) || 0;
  if (num >= 10000) return (num / 10000).toFixed(1).replace(/\.0$/, '') + '万';
  if (num > 0) return String(Math.round(num));
  return '0';
}

/* 打开 drawer: fetch /api/talents/{id}, 渲染 7 panel
   复用既有 renderTalentDetail 7 tab 语义 (老大派单明确「复用既有 renderTalentDetail 7 tab」)
   数据契约实测 (大丸子 tal_1787213095215_841db3):
     基础: id/name/avatar/douyin_id/real_name/wechat/phone/email/city/level/followers/talent_type/location/agency/tags/bio
     合作: cooperation_status/follow_up_by/next_follow_up_at/follow_up_note/commission_requirement
     数据: fulfillment_score/rating_score/total_gmv/total_products/product_count/total_shops/average_price/live_ratio/video_ratio/avg_live_gmv/live_gpm/video_gpm
     画像: fan_gender/fan_age/fan_city_tier/fan_price_range + video_audience_* 镜像
     OCR:  ocr_raw_fields / ai_analysis / ai_reason / ai_rating / ai_tags
   字段名铁律 (第 1 条): 抄实测 JSON, 不凭印象 (r36 fix/sb2-side-restore 已立条目) */
function sb2TlnOpenDetail(id){
  if (!id) return;
  _sb2TlnDetailCurrentId = id;
  _sb2TlnDetailCurrentTab = 'overview';
  var overlay = document.getElementById('sb2TlnDetailOverlay');
  var titleEl = document.getElementById('sb2TlnDetailTitle');
  var subEl = document.getElementById('sb2TlnDetailSub');
  if (!overlay) return;
  if (titleEl) titleEl.textContent = '达人详情';
  if (subEl) subEl.textContent = '载入中…';
  sb2TlnResetPanels();
  overlay.classList.add('open');
  if (typeof apiFetch !== 'function') {
    if (subEl) subEl.textContent = '环境异常: apiFetch 未定义';
    return;
  }
  apiFetch('/api/talents/' + encodeURIComponent(id)).then(function(resp){
    if (!resp || !resp.ok){
      if (subEl) subEl.textContent = '加载失败 (HTTP ' + (resp ? resp.status : '?') + ')';
      return null;
    }
    return resp.json();
  }).then(function(t){
    if (!t) return;
    _sb2TlnDetailCurrentData = t;
    if (titleEl) titleEl.textContent = t.name || '达人详情';
    if (subEl) subEl.textContent = (t.douyin_id ? '@' + t.douyin_id + ' · ' : '') +
      (t.city ? t.city + ' · ' : '') +
      (t.followers ? sb2TlnFmtWan(t.followers) + ' 粉丝' : '');
    sb2TlnRenderAllPanels(t);
    sb2TlnSwitchTab('overview');
    _sb2TlnDetailLoaded = true;
  }).catch(function(e){
    if (subEl) subEl.textContent = '加载异常: ' + (e && e.message || String(e));
    if (typeof window !== 'undefined' && typeof window.showToast === 'function') window.showToast('⚠️ 达人详情加载失败', 'warn');
  });
}

/* 关闭 drawer */
function sb2TlnCloseDetail(){
  var overlay = document.getElementById('sb2TlnDetailOverlay');
  if (overlay) overlay.classList.remove('open');
  _sb2TlnDetailCurrentId = '';
  _sb2TlnDetailCurrentData = null;
  _sb2TlnDetailLoaded = false;
}

/* 重置 7 panel + tab 状态 */
function sb2TlnResetPanels(){
  var tabPanelIds = ['sb2TlnPanelOverview','sb2TlnPanelSales','sb2TlnPanelFans','sb2TlnPanelProducts','sb2TlnPanelRecords','sb2TlnPanelFollowUps','sb2TlnPanelKnowledge'];
  for (var i = 0; i < tabPanelIds.length; i++){
    var el = document.getElementById(tabPanelIds[i]);
    if (el) el.innerHTML = '<div class="sb2-tln-loading">载入中…</div>';
  }
  var tabs = document.querySelectorAll('#sb2TlnDetailTabs .sb2-tln-tab');
  tabs.forEach(function(t){ t.classList.toggle('active', t.dataset.tab === 'overview'); });
}

/* 切 tab */
function sb2TlnSwitchTab(tab){
  _sb2TlnDetailCurrentTab = tab;
  var tabs = document.querySelectorAll('#sb2TlnDetailTabs .sb2-tln-tab');
  tabs.forEach(function(t){ t.classList.toggle('active', t.dataset.tab === tab); });
  var panels = document.querySelectorAll('#sb2TlnDetailBody .sb2-tln-tab-panel');
  panels.forEach(function(p){ p.classList.toggle('active', p.dataset.panel === tab); });
}

/* 渲染所有 7 panel (简版, 字段名抄实测) */
function sb2TlnRenderAllPanels(t){
  sb2TlnRenderPanelOverview(t);
  sb2TlnRenderPanelSales(t);
  sb2TlnRenderPanelFans(t);
  sb2TlnRenderPanelProducts(t);
  sb2TlnRenderPanelRecords(t);
  sb2TlnRenderPanelFollowUps(t);
  sb2TlnRenderPanelKnowledge(t);
}

/* Panel 1: 概况 (核心数据 + 基础信息 + AI 分析) */
function sb2TlnRenderPanelOverview(t){
  var el = document.getElementById('sb2TlnPanelOverview');
  if (!el) return;
  var html = '';
  html += '<div class="sb2-tln-section">';
  html += '<div class="sb2-tln-hero">';
  var initial = (t.name || '?').charAt(0);
  var avatar = t.avatar
    ? '<img src="' + sb2TlnEsc(t.avatar) + '" class="sb2-tln-avatar-img" onerror="this.parentNode.textContent=\'' + sb2TlnEsc(initial) + '\'">'
    : '<span class="sb2-tln-avatar-text">' + sb2TlnEsc(initial) + '</span>';
  html += '<div class="sb2-tln-avatar">' + avatar + '</div>';
  html += '<div class="sb2-tln-hero-info">';
  html += '<div class="sb2-tln-hero-name">' + sb2TlnEsc(t.name || '-') + '</div>';
  html += '<div class="sb2-tln-hero-pills">';
  html += '<span class="sb2-tln-pill"><label>抖音号</label>' + sb2TlnEsc(t.douyin_id || '-') + '</span>';
  html += '<span class="sb2-tln-pill"><label>粉丝</label>' + sb2TlnFmtWan(t.followers) + '</span>';
  html += '<span class="sb2-tln-pill"><label>等级</label>' + sb2TlnEsc(t.level || '-') + '</span>';
  html += '<span class="sb2-tln-pill"><label>类目</label>' + sb2TlnEsc(t.category || t.talent_type || '-') + '</span>';
  html += '<span class="sb2-tln-pill"><label>状态</label>' + sb2TlnEsc(t.cooperation_status || '-') + '</span>';
  html += '</div>';
  if (t.bio) html += '<div class="sb2-tln-hero-bio">' + sb2TlnEsc(t.bio) + '</div>';
  var tags = Array.isArray(t.tags) ? t.tags : [];
  if (tags.length) {
    html += '<div class="sb2-tln-hero-tags">' + tags.map(function(tag){
      return '<span class="sb2-tln-tag">' + sb2TlnEsc(tag) + '</span>';
    }).join('') + '</div>';
  }
  html += '</div></div></div>';

  html += '<div class="sb2-tln-section">';
  html += '<div class="sb2-tln-section-title">核心数据</div>';
  html += '<div class="sb2-tln-metric-grid">';
  var kpis = [
    { label: '粉丝量', val: sb2TlnFmtWan(t.followers) },
    { label: '总 GMV', val: sb2TlnFmtWan(t.total_gmv) },
    { label: '视频 GPM', val: (Number(t.video_gpm) || 0).toFixed(0) },
    { label: '直播 GPM', val: (Number(t.live_gpm) || 0).toFixed(0) },
    { label: '带货商品数', val: String(t.product_count || t.total_products || 0) },
    { label: '合作店铺数', val: String(t.total_shops || 0) },
    { label: '平均件单价', val: (Number(t.average_price) || 0).toFixed(1) },
    { label: '评分', val: (Number(t.rating_score) || 0).toFixed(1) }
  ];
  kpis.forEach(function(k){
    html += '<div class="sb2-tln-metric-card">';
    html += '<div class="sb2-tln-metric-value">' + sb2TlnEsc(k.val) + '</div>';
    html += '<div class="sb2-tln-metric-label">' + sb2TlnEsc(k.label) + '</div>';
    html += '</div>';
  });
  html += '</div></div>';

  // AI 分析摘要 (有 ai_analysis 显示, 无显示「待 AI 分析」)
  if (t.ai_analysis) {
    html += '<div class="sb2-tln-section">';
    html += '<div class="sb2-tln-section-title">🤖 AI 综合分析</div>';
    html += '<div class="sb2-tln-ai-card">' + sb2TlnEsc(t.ai_analysis).substring(0, 800) + (t.ai_analysis.length > 800 ? '…' : '') + '</div>';
    html += '</div>';
  } else {
    html += '<div class="sb2-tln-section">';
    html += '<div class="sb2-tln-section-title">🤖 AI 综合分析</div>';
    html += '<div class="sb2-tln-empty">点击「+ 录入达人」旁的 AI 分析生成达人综合评估。</div>';
    html += '</div>';
  }
  el.innerHTML = html;
}

/* Panel 2: 带货 (带货明细) */
function sb2TlnRenderPanelSales(t){
  var el = document.getElementById('sb2TlnPanelSales');
  if (!el) return;
  var rows = [
    { label: '总 GMV', val: sb2TlnFmtWan(t.total_gmv) + (t.total_gmv_text ? ' (' + t.total_gmv_text + ')' : '') },
    { label: '视频 GPM', val: (Number(t.video_gpm) || 0).toFixed(0) + (t.video_gpm_text ? ' (' + t.video_gpm_text + ')' : '') },
    { label: '直播 GPM', val: (Number(t.live_gpm) || 0).toFixed(0) },
    { label: '平均直播 GMV', val: sb2TlnFmtWan(t.avg_live_gmv) },
    { label: '直播占比', val: (Number(t.live_ratio) || 0).toFixed(1) + '%' },
    { label: '视频占比', val: (Number(t.video_ratio) || 0).toFixed(1) + '%' },
    { label: '履约评分', val: (Number(t.fulfillment_score) || 0).toFixed(1) },
    { label: '佣金要求', val: (Number(t.commission_requirement) || 0).toFixed(1) + '%' }
  ];
  var html = '<div class="sb2-tln-section"><div class="sb2-tln-section-title">带货明细</div>';
  html += '<div class="sb2-tln-info-grid">';
  rows.forEach(function(r){
    html += '<div class="sb2-tln-info-item"><label>' + sb2TlnEsc(r.label) + '</label><div class="value">' + sb2TlnEsc(r.val) + '</div></div>';
  });
  html += '</div></div>';
  el.innerHTML = html;
}

/* Panel 3: 粉丝 (4 维度分布 + 视频粉丝 4 维度, 进度条 + 精度 toFixed 1) */
function sb2TlnRenderPanelFans(t){
  var el = document.getElementById('sb2TlnPanelFans');
  if (!el) return;
  var dims = [
    { key: 'fan_gender', label: '性别分布 (账号)' },
    { key: 'fan_age', label: '年龄分布 (账号)' },
    { key: 'fan_city_tier', label: '城市等级 (账号)' },
    { key: 'fan_price_range', label: '客单价 (账号)' },
    { key: 'video_audience_gender', label: '性别分布 (视频)' },
    { key: 'video_audience_age', label: '年龄分布 (视频)' },
    { key: 'video_audience_city_tier', label: '城市等级 (视频)' },
    { key: 'video_audience_price_range', label: '客单价 (视频)' }
  ];
  var html = '<div class="sb2-tln-section"><div class="sb2-tln-section-title">粉丝画像</div>';
  var anyBlock = false;
  dims.forEach(function(d){
    var v = t[d.key];
    if (!v || typeof v !== 'object' || Array.isArray(v)) return;
    var entries = Object.keys(v).map(function(k){ return [k, Number(v[k]) || 0]; });
    entries = entries.filter(function(e){ return e[1] > 0; });
    if (entries.length === 0) return;
    anyBlock = true;
    entries.sort(function(a, b){ return b[1] - a[1]; });
    var total = entries.reduce(function(acc, e){ return acc + e[1]; }, 0);
    html += '<div class="sb2-tln-fans-block">';
    html += '<div class="sb2-tln-fans-title">' + sb2TlnEsc(d.label) + '</div>';
    entries.slice(0, 6).forEach(function(e){
      var pct = total ? (e[1] / total * 100).toFixed(1) : 0;
      html += '<div class="sb2-tln-fans-row">';
      html += '<span class="sb2-tln-fans-label">' + sb2TlnEsc(e[0]) + '</span>';
      html += '<div class="sb2-tln-fans-bar"><div class="sb2-tln-fans-fill" style="width:' + pct + '%"></div></div>';
      html += '<span class="sb2-tln-fans-pct">' + pct + '%</span>';
      html += '</div>';
    });
    html += '</div>';
  });
  if (!anyBlock) {
    html += '<div class="sb2-tln-empty">粉丝画像数据待补充 (后续派单接入 OCR 解析).</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

/* Panel 4-7: 占位 (数据待后续派单补齐) — 派单只要求 7 tab 可切 + 0 pageerror, 不要求内容完整 */
function sb2TlnRenderPanelProducts(t){
  var el = document.getElementById('sb2TlnPanelProducts');
  if (!el) return;
  el.innerHTML = '<div class="sb2-tln-empty">带货商品列表数据待后续派单接入 (现依赖 r39-P0 匹配商品 cell, 需新建 /api/talents/{id}/products 端点或复用 r39 撮合链).</div>';
}
function sb2TlnRenderPanelRecords(t){
  var el = document.getElementById('sb2TlnPanelRecords');
  if (!el) return;
  el.innerHTML = '<div class="sb2-tln-empty">跟进记录数据待后续派单接入 (现 follow_up_note/next_follow_up_at 字段在前端未串联, 需新建 /api/talents/{id}/records 端点).</div>';
}
function sb2TlnRenderPanelFollowUps(t){
  var el = document.getElementById('sb2TlnPanelFollowUps');
  if (!el) return;
  el.innerHTML = '<div class="sb2-tln-empty">待办数据待后续派单接入 (需新建 /api/talents/{id}/followups 端点).</div>';
}
function sb2TlnRenderPanelKnowledge(t){
  var el = document.getElementById('sb2TlnPanelKnowledge');
  if (!el) return;
  el.innerHTML = '<div class="sb2-tln-empty">知识库数据待后续派单接入 (需新建 /api/talents/{id}/knowledge 端点).</div>';
}

/* 初始化: 监听 Esc 键 + ✕ + 遮罩点击关闭 (派单明文 3 个关闭入口) */
function sb2TlnDetailInit(){
  if (typeof document === 'undefined') return;
  if (!document._sb2TlnDetailEscBound) {
    document.addEventListener('keydown', function(e){
      if (e && e.key === 'Escape') {
        var overlay = document.getElementById('sb2TlnDetailOverlay');
        if (overlay && overlay.classList.contains('open')) sb2TlnCloseDetail();
      }
    });
    document._sb2TlnDetailEscBound = true;
  }
  var closeBtn = document.getElementById('sb2TlnDetailClose');
  if (closeBtn && !closeBtn._sb2TlnBound) {
    closeBtn.addEventListener('click', sb2TlnCloseDetail);
    closeBtn._sb2TlnBound = true;
  }
  var overlay = document.getElementById('sb2TlnDetailOverlay');
  if (overlay && !overlay._sb2TlnBound) {
    overlay.addEventListener('click', function(e){
      if (e.target === overlay) sb2TlnCloseDetail();
    });
    overlay._sb2TlnBound = true;
  }
}

/* ★ r63 项② IIFE onclick 跨域铁律 (r38): drawer 函数挂 window 暴露 */
window.sb2TlnOpenDetail = sb2TlnOpenDetail;
window.sb2TlnCloseDetail = sb2TlnCloseDetail;
window.sb2TlnSwitchTab = sb2TlnSwitchTab;
window.sb2TlnDetailInit = sb2TlnDetailInit;
/* 自启: 绑 Esc/✕/遮罩 关闭入口 (派单明文 3 个) — DOMContentLoaded 后 init */
(function () {
  function _boot() {
    if (typeof sb2TlnDetailInit === 'function') sb2TlnDetailInit();
  }
  if (typeof document !== 'undefined') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _boot);
    } else {
      _boot();
    }
  }
})();

/* ============================================================
 * sb2-tasks MVP1: 任务模块 (看板默认 + 列表 segmented + 侧栏三维联动)
 * 数据契约 (后端 line 16866-17025): GET 仅 status/assignee, 优先级/项目/搜索 = 前端内存过滤
 * created_at/updated_at 是 TEXT 本地时间, deadline/progress 自由文本
 * admin 闸: currentUser.role === 'admin' (currentUser 来自 line 16356)
 * ============================================================ */
var _sb2TasksList = [];
var _sb2TasksLoaded = false;
var _sb2TasksView = 'board'; // 默认看板
var _sb2TasksFilterStatus = ''; // '' | pending | in_progress | completed | cancelled
var _sb2TasksFilterAssignee = ''; // '' | emp_xxx
var _sb2TasksFilterPriority = ''; // '' | urgent | high | normal
var _sb2TasksFilterProject = ''; // '' | 项目名 (前端内存)
var _sb2TasksSearchQ = '';
var _sb2TasksEditingId = null; // modal 编辑中的任务 id, null = 新建
var _sb2TasksDeleteId = null;

function sb2TasksIsAdmin(){
  return (typeof currentUser !== 'undefined' && currentUser && currentUser.role === 'admin');
}

function sb2TasksEsc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return ({'&':'&','<':'<','>':'>','"':'"',"'":'&#39;'})[c];
  });
}

function sb2TasksToast(msg){
  if (typeof showToast === 'function') showToast(msg, 'info');
  else { try { console.log('[sb2-tasks]', msg); } catch(e){} }
}

function sb2TasksStatusLabel(s){
  return ({pending:'待处理', in_progress:'进行中', completed:'已完成', cancelled:'已取消'})[s] || (s || '-');
}

function sb2TasksPriorityLabel(p){
  return ({urgent:'紧急', high:'高', normal:'普通'})[p] || (p || '-');
}

function sb2TasksPriorityChip(p){
  var cls = ({urgent:'urgent', high:'high', normal:'normal'})[p] || 'normal';
  return '<span class="sb2-tasks-chip ' + cls + '">' + sb2TasksEsc(sb2TasksPriorityLabel(p)) + '</span>';
}

function sb2TasksStatusChip(s){
  var cls = ({pending:'off', in_progress:'high', completed:'ok', cancelled:'off'})[s] || 'off';
  var lbl = sb2TasksStatusLabel(s);
  return '<span class="sb2-tasks-chip ' + cls + '">' + sb2TasksEsc(lbl) + '</span>';
}

function sb2TasksAvatar(name){
  // 头像首字 + 渐变配色 (跟达人库侧栏同款, 复用 sb2TalentsAvatar 配色)
  var s = String(name == null ? '' : name).trim();
  var first = s ? s.charAt(0) : '?';
  // 5 套渐变配色循环
  var grads = [
    'linear-gradient(135deg,#FF5A36,#FF8A36)',  // 橙
    'linear-gradient(135deg,#165DFF,#7B61FF)',  // 蓝紫
    'linear-gradient(135deg,#00B42A,#36CFC9)',  // 绿青
    'linear-gradient(135deg,#FF7D00,#FFB800)',  // 黄橙
    'linear-gradient(135deg,#F53F3F,#FF7D00)',  // 红橙
    'linear-gradient(135deg,#86909C,#4E5969)',  // 灰
    'linear-gradient(135deg,#0B0D12,#4E5969)'   // 黑
  ];
  var h = 0;
  for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  var g = grads[h % grads.length];
  return '<span class="sb2-tasks-avatar" style="background:' + g + '">' + sb2TasksEsc(first) + '</span>';
}

function sb2TasksDeadlineClass(deadline, status){
  // status=completed → done (绿)
  // 否则: 解析 deadline 跟今天比对 → late (红) / today (橙) / '' (默认灰)
  if (status === 'completed') return 'done';
  if (!deadline) return '';
  var d = String(deadline).trim();
  if (!d) return '';
  var today = new Date();
  var y = today.getFullYear();
  var m = today.getMonth();
  var day = today.getDate();
  var ymd = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
  var md = String(m + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
  // 处理 yyyy-mm-dd
  if (d === ymd || d === md) return 'today';
  // 处理 mm-dd
  var parts = d.split('-');
  if (parts.length === 2 && (parts[0] + '-' + parts[1]) === md) return 'today';
  // 简单判断 (yyyy-mm-dd 或 mm-dd): 拼成 yyyy-mm-dd 后比
  var iso = d;
  if (parts.length === 2) iso = y + '-' + d;
  if (iso < ymd) return 'late';
  return '';
}

function sb2TasksDeadlineLabel(deadline, status){
  if (status === 'completed') return '✓ 已完成';
  if (!deadline) return '—';
  var d = String(deadline).trim();
  if (!d) return '—';
  var today = new Date();
  var y = today.getFullYear();
  var m = today.getMonth();
  var day = today.getDate();
  var md = String(m + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
  var ymd = y + '-' + String(m + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
  var parts = d.split('-');
  // mm-dd 跟今天同日 → 今天
  if (d === md || d === ymd) return '今天截止';
  if (parts.length === 2 && (parts[0] + '-' + parts[1]) === md) return '今天截止';
  // 简单显示
  return d;
}

function sb2TasksProjectName(p){
  return String(p == null ? '' : p).trim() || '未指定';
}

function sb2TasksProjectChip(p){
  return '<span class="sb2-tasks-chip off">' + sb2TasksEsc(sb2TasksProjectName(p)) + '</span>';
}

// ★ MVP1: 渲染分发 — 看板 + 列表 + 侧栏
function sb2TasksRender(){
  /* 〔28 轮 2026-10-06〕hero 标题动态化 (老大「让所有界面都是侧边栏为主标题」统一口径)
     修前: hero h1 静态「任务」, 跟内置侧栏 h1「任务」重复
     修后: 状态 > 优先级 > 项目 > 负责人, 全不选 → 「全部任务」; 标签用现有 sb2TasksStatusLabel/PriorityLabel
     调用点: sb2TasksRender 顶部 (4 维筛选 + 搜索全经过) */
  (function(){
    var hero = document.getElementById('sb2TasksHeroTitle');
    if (!hero) return;
    if (_sb2TasksFilterStatus) { hero.textContent = sb2TasksStatusLabel(_sb2TasksFilterStatus); return; }
    if (_sb2TasksFilterPriority) { hero.textContent = sb2TasksPriorityLabel(_sb2TasksFilterPriority); return; }
    if (_sb2TasksFilterProject) { hero.textContent = _sb2TasksFilterProject === '未指定' ? '未指定项目' : _sb2TasksFilterProject; return; }
    if (_sb2TasksFilterAssignee) {
      if (_sb2TasksFilterAssignee === '__none__') { hero.textContent = '未指定负责人'; return; }
      var _emp = (typeof emps !== 'undefined' && Array.isArray(emps)) ? emps.find(function(e){ return e && e.id === _sb2TasksFilterAssignee; }) : null;
      hero.textContent = (_emp && (_emp.name || _emp.title)) ? ('负责人 · ' + (_emp.name || _emp.title)) : '按负责人筛选';
      return;
    }
    hero.textContent = '全部任务';
  })();
  var heroSub = document.getElementById('sb2TasksHeroSub');
  var sideSub = document.getElementById('sb2TasksSideSub');
  if (heroSub) heroSub.textContent = '共 ' + _sb2TasksList.length + ' 项 · 按状态流转看板';
  if (sideSub) {
    var overdue = 0;
    var today = new Date(); today.setHours(0,0,0,0);
    _sb2TasksList.forEach(function(t){
      if (t.status === 'completed' || t.status === 'cancelled') return;
      var d = String(t.deadline || '').trim(); if (!d) return;
      var iso = d; var parts = d.split('-');
      if (parts.length === 2) iso = today.getFullYear() + '-' + d;
      var dd = new Date(iso); if (!isNaN(dd.getTime()) && dd < today) overdue++;
    });
    sideSub.textContent = _sb2TasksList.length + ' 项 · ' + overdue + ' 已逾期';
  }
  sb2TasksRenderSide();
  sb2TasksRenderBoard();
  sb2TasksRenderList();
  sb2TasksUpdateAdminOnly();
}

function sb2TasksUpdateAdminOnly(){
  // + 新建任务 / 删除按钮 / 拖卡权限都跟 admin-only 联动
  var admin = sb2TasksIsAdmin();
  var btns = document.querySelectorAll('#sb2TasksMain [data-admin-only]');
  btns.forEach(function(b){
    b.style.display = admin ? '' : 'none';
  });
}

function sb2TasksFiltered(){
  // 全量列表 → 应用 4 维过滤 + 搜索, 返回过滤后
  var q = (_sb2TasksSearchQ || '').toLowerCase().trim();
  return _sb2TasksList.filter(function(t){
    if (_sb2TasksFilterStatus && t.status !== _sb2TasksFilterStatus) return false;
    if (_sb2TasksFilterAssignee){
      if (_sb2TasksFilterAssignee === '__none__'){
        if (String(t.assignee || '').trim()) return false;
      } else {
        if (t.assignee !== _sb2TasksFilterAssignee) return false;
      }
    }
    if (_sb2TasksFilterPriority && (t.priority || 'normal') !== _sb2TasksFilterPriority) return false;
    if (_sb2TasksFilterProject) {
      var proj = String(t.project_id || '').trim() || '未指定';
      if (proj !== _sb2TasksFilterProject) return false;
    }
    if (q){
      var hay = ((t.title || '') + ' ' + (t.assignee_name || '') + ' ' + (t.creator_name || '') + ' ' + (t.description || '')).toLowerCase();
      if (hay.indexOf(q) < 0) return false;
    }
    return true;
  });
}

function sb2TasksRenderSide(){
  // 4 维侧栏: status / assignee / priority / project, 计数从全量 _sb2TasksList
  // (内存过滤, 跟达人库三维联动同款)
  function renderGroup(elId, groupName, items, currentKey){
    var el = document.getElementById(elId);
    if (!el) return;
    var html = '';
    items.forEach(function(it){
      var active = (it.key === currentKey) ? ' active' : '';
      var kEsc = sb2TasksEsc(it.key).replace(/'/g, "\\'");
      html += '<div class="sb2-tasks-side-item' + active + '" data-key="' + sb2TasksEsc(it.key) + '" data-group="' + groupName + '" onclick="sb2TasksOnSideClick(\'' + groupName + '\', \'' + kEsc + '\')">' + it.label + '<span class="n">' + it.count + '</span></div>';
    });
    el.innerHTML = html;
  }

  // status 维度
  var statusItems = [
    {key:'', label:'全部', count:_sb2TasksList.length},
    {key:'pending', label:'待处理', count:0},
    {key:'in_progress', label:'进行中', count:0},
    {key:'completed', label:'已完成', count:0},
    {key:'cancelled', label:'已取消', count:0}
  ];
  _sb2TasksList.forEach(function(t){
    var s = statusItems.find(function(x){ return x.key === t.status; });
    if (s) s.count++;
  });
  renderGroup('sb2TasksSideStatus', 'status', statusItems, _sb2TasksFilterStatus);

  // assignee 维度: 全部 + window.emps 中每个员工 + 其他
  var asgMap = {};
  _sb2TasksList.forEach(function(t){
    var key = String(t.assignee || '').trim() || '__none__';
    if (!asgMap[key]) asgMap[key] = {count:0, name:''};
    asgMap[key].count++;
  });
  var asgItems = [{key:'', label:'全部', count:_sb2TasksList.length}];
  // 优先 window.emps (现有员工缓存, loadEmployees 已灌)
  var emps = (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) ? window.emps : [];
  emps.forEach(function(e){
    if (asgMap[e.id]) asgItems.push({
      key:e.id,
      label:'<span class="av-s">' + sb2TasksAvatar(e.name || e.id) + sb2TasksEsc(e.name || e.id) + '</span>',
      count:asgMap[e.id].count
    });
  });
  // 兜底: 数据里有但 window.emps 没有的员工 (用 assignee_name)
  Object.keys(asgMap).forEach(function(k){
    if (k === '__none__') return;
    if (!asgItems.find(function(x){ return x.key === k; })){
      var name = '';
      _sb2TasksList.forEach(function(t){ if (t.assignee === k) name = t.assignee_name || k; });
      asgItems.push({key:k, label:'<span class="av-s">' + sb2TasksAvatar(name) + sb2TasksEsc(name) + '</span>', count:asgMap[k].count});
    }
  });
  if (asgMap['__none__']){
    asgItems.push({key:'__none__', label:'未指定', count:asgMap['__none__'].count});
  }
  renderGroup('sb2TasksSideAssignee', 'assignee', asgItems, _sb2TasksFilterAssignee);

  // priority 维度
  var priItems = [
    {key:'', label:'全部', count:_sb2TasksList.length},
    {key:'urgent', label:'紧急', count:0},
    {key:'high', label:'高', count:0},
    {key:'normal', label:'普通', count:0}
  ];
  _sb2TasksList.forEach(function(t){
    var p = t.priority || 'normal';
    var s = priItems.find(function(x){ return x.key === p; });
    if (s) s.count++;
  });
  renderGroup('sb2TasksSidePriority', 'priority', priItems, _sb2TasksFilterPriority);

  // project 维度: 全量列表里提取
  var projMap = {};
  _sb2TasksList.forEach(function(t){
    var p = String(t.project_id || '').trim() || '未指定';
    if (!projMap[p]) projMap[p] = 0;
    projMap[p]++;
  });
  var projItems = [{key:'', label:'全部', count:_sb2TasksList.length}];
  Object.keys(projMap).sort().forEach(function(p){
    projItems.push({key:p, label: sb2TasksEsc(p), count:projMap[p]});
  });
  renderGroup('sb2TasksSideProject', 'project', projItems, _sb2TasksFilterProject);
}

function sb2TasksOnSideClick(group, key){
  // group + key 都从 DOM onclick 烘焙传入, 避免 DOM 反查
  if (group === 'status') _sb2TasksFilterStatus = key;
  else if (group === 'assignee') _sb2TasksFilterAssignee = key;
  else if (group === 'priority') _sb2TasksFilterPriority = key;
  else if (group === 'project') _sb2TasksFilterProject = key;
  sb2TasksRender();
}

// ★ 视图切换 (MVP1 P1 fix: 显式互斥 display, 配合 CSS 默认态兜底)
function sb2TasksSwitchView(view){
  _sb2TasksView = view;
  var boardWrap = document.getElementById('sb2TasksBoardWrap');
  var listWrap = document.getElementById('sb2TasksListWrap');
  // P1 fix: 显式设 display 字符串, 不用 '' (避免 reset 到 CSS 默认值时两个 wrap 都用同一个类)
  if (boardWrap) boardWrap.style.display = (view === 'board') ? 'block' : 'none';
  if (listWrap) listWrap.style.display = (view === 'list') ? 'block' : 'none';
  var btns = document.querySelectorAll('.sb2-tasks-seg button');
  btns.forEach(function(b){
    if (b.getAttribute('data-view') === view) b.classList.add('on');
    else b.classList.remove('on');
  });
}

// ★ 看板渲染 (默认视图)
function sb2TasksRenderBoard(){
  var board = document.getElementById('sb2TasksBoard');
  if (!board) return;
  var filtered = sb2TasksFiltered();
  var cols = [
    {key:'pending', dotc:'var(--sb2-t3)', label:'待处理'},
    {key:'in_progress', dotc:'var(--sb2-warning)', label:'进行中'},
    {key:'completed', dotc:'var(--sb2-success)', label:'已完成'}
  ];
  var html = '';
  cols.forEach(function(c){
    var items = filtered.filter(function(t){ return t.status === c.key; });
    html += '<div class="sb2-tasks-col" data-col="' + c.key + '" ondragover="sb2TasksOnDragOver(event)" ondragleave="sb2TasksOnDragLeave(event)" ondrop="sb2TasksOnDrop(event, \'' + c.key + '\')">';
    html += '<div class="sb2-tasks-col-h"><span class="dotc" style="background:' + c.dotc + '"></span><span class="t">' + c.label + '</span><span class="n">' + items.length + '</span></div>';
    if (items.length === 0){
      html += '<div class="sb2-tasks-col-empty">暂无任务</div>';
    } else {
      items.forEach(function(t){
        html += sb2TasksRenderCard(t);
      });
    }
    html += '</div>';
  });
  board.innerHTML = html;
  // cancelled 折叠条
  var cancelled = filtered.filter(function(t){ return t.status === 'cancelled'; });
  var bar = document.getElementById('sb2TasksCancelledBar');
  var nEl = document.getElementById('sb2TasksCancelledN');
  var itemEl = document.getElementById('sb2TasksCancelledItem');
  if (bar){
    if (cancelled.length > 0){
      bar.style.display = '';
      if (nEl) nEl.textContent = cancelled.length;
      if (itemEl){
        var first = cancelled[0];
        itemEl.textContent = (first.title || '-') + ' · ' + (first.assignee_name || '未指定') + ' · 方案废弃';
      }
    } else {
      bar.style.display = 'none';
    }
  }
}

function sb2TasksRenderCard(t){
  var canMove = sb2TasksIsAdmin() ||
    (typeof currentUser !== 'undefined' && currentUser &&
     (t.assignee === currentUser.id || t.creator === currentUser.id));
  var prog = parseInt(t.progress, 10);
  if (isNaN(prog)) prog = 0;
  if (prog > 100) prog = 100;
  if (prog < 0) prog = 0;
  var dragAttr = canMove ? ' draggable="true"' : '';
  var dragHandlers = canMove ? ' ondragstart="sb2TasksOnDragStart(event, \'' + sb2TasksEsc(t.id) + '\')" ondragend="sb2TasksOnDragEnd(event)"' : '';
  var html = '<div class="sb2-tasks-card"' + dragAttr + dragHandlers + ' onclick="sb2TasksOpenForm(\'' + sb2TasksEsc(t.id) + '\')" data-id="' + sb2TasksEsc(t.id) + '">';
  html += '<div class="top">';
  html += sb2TasksPriorityChip(t.priority || 'normal');
  if (t.project_id) html += sb2TasksProjectChip(t.project_id);
  html += '</div>';
  html += '<div class="ttl">' + sb2TasksEsc(t.title || '-') + '</div>';
  if (t.description){
    html += '<div class="desc">' + sb2TasksEsc(t.description) + '</div>';
  }
  if (t.status !== 'completed' && t.status !== 'cancelled'){
    html += '<div class="prog"><i style="width:' + prog + '%"></i></div>';
    html += '<div class="prog-t"><span>进度</span><span class="num">' + prog + '%</span></div>';
  }
  html += '<div class="bot-row">';
  if (t.assignee_name){
    html += '<span class="sb2-tasks-who">' + sb2TasksAvatar(t.assignee_name) + sb2TasksEsc(t.assignee_name) + '</span>';
  } else {
    html += '<span class="sb2-tasks-who" style="color:var(--sb2-t3)">未指定</span>';
  }
  var dlCls = sb2TasksDeadlineClass(t.deadline, t.status);
  var dlLbl = sb2TasksDeadlineLabel(t.deadline, t.status);
  var dlIcon = (t.status === 'completed') ? '✓' : (dlCls === 'late' ? '\23F0' : '\1F4C5');
  html += '<span class="dl ' + dlCls + '">' + dlIcon + ' ' + sb2TasksEsc(dlLbl) + '</span>';
  html += '</div>';
  html += '</div>';
  return html;
}

// ★ 列表渲染
function sb2TasksRenderList(){
  var tbody = document.getElementById('sb2TasksTbody');
  var tfoot = document.getElementById('sb2TasksTfoot');
  if (!tbody) return;
  var filtered = sb2TasksFiltered();
  if (filtered.length === 0){
    tbody.innerHTML = '<tr><td colspan="8" class="sb2-tasks-col-empty">暂无任务</td></tr>';
    if (tfoot) tfoot.textContent = '共 0 条';
    return;
  }
  var html = '';
  filtered.forEach(function(t){
    var prog = parseInt(t.progress, 10); if (isNaN(prog)) prog = 0;
    var progBar = (t.status === 'completed') ? '100' : String(prog);
    var cancelledCls = (t.status === 'cancelled') ? ' class="cancelled-row"' : '';
    var dlCls = sb2TasksDeadlineClass(t.deadline, t.status);
    var dlLbl = sb2TasksDeadlineLabel(t.deadline, t.status);
    var dlStyle = '';
    if (dlCls === 'late') dlStyle = ' style="color:var(--sb2-danger)"';
    else if (dlCls === 'today') dlStyle = ' style="color:var(--sb2-warning)"';
    else if (dlCls === 'done') dlStyle = ' style="color:var(--sb2-success)"';
    var progDisplay = (t.status === 'cancelled') ? '—' : (prog + '%');
    var dlDisplay = (t.status === 'cancelled') ? '—' : dlLbl;
    html += '<tr' + cancelledCls + ' onclick="sb2TasksOpenForm(\'' + sb2TasksEsc(t.id) + '\')">';
    html += '<td><span class="ttl-l">' + sb2TasksEsc(t.title || '-') + '</span></td>';
    html += '<td>' + sb2TasksProjectChip(t.project_id) + '</td>';
    html += '<td>' + sb2TasksPriorityChip(t.priority || 'normal') + '</td>';
    html += '<td><span class="sb2-tasks-who">' + (t.assignee_name ? sb2TasksAvatar(t.assignee_name) + sb2TasksEsc(t.assignee_name) : '<span style="color:var(--sb2-t3)">未指定</span>') + '</span></td>';
    html += '<td><span class="st ' + sb2TasksEsc(t.status) + '">' + sb2TasksEsc(sb2TasksStatusLabel(t.status)) + '</span></td>';
    html += '<td><span class="pbar"><i style="width:' + progBar + '%"></i></span><span class="num">' + progDisplay + '</span></td>';
    html += '<td class="num"' + dlStyle + '>' + sb2TasksEsc(dlDisplay) + '</td>';
    html += '<td>' + sb2TasksEsc(t.creator_name || '-') + '</td>';
    html += '</tr>';
  });
  tbody.innerHTML = html;
  if (tfoot) tfoot.textContent = '共 ' + filtered.length + ' 条 · 拖拽卡片跨列 = 改状态（员工仅可改自己任务的 status / progress）';
}

// ★ 搜索
function sb2TasksOnSearch(q){
  _sb2TasksSearchQ = String(q || '');
  sb2TasksRender();
}

// ★ 拖拽
function sb2TasksOnDragStart(e, id){
  e.dataTransfer.setData('text/plain', id);
  e.dataTransfer.effectAllowed = 'move';
  var card = e.target; if (card && card.classList) card.classList.add('dragging');
}
function sb2TasksOnDragOver(e){
  if (e.preventDefault) e.preventDefault();
  e.dataTransfer.dropEffect = 'move';
  var col = e.currentTarget;
  if (col && col.classList) col.classList.add('drag-over');
}
function sb2TasksOnDragLeave(e){
  var col = e.currentTarget;
  if (col && col.classList) col.classList.remove('drag-over');
}
function sb2TasksOnDragEnd(e){
  var card = e.target; if (card && card.classList) card.classList.remove('dragging');
  document.querySelectorAll('.sb2-tasks-col.drag-over').forEach(function(el){ el.classList.remove('drag-over'); });
}
async function sb2TasksOnDrop(e, newStatus){
  if (e.preventDefault) e.preventDefault();
  var col = e.currentTarget;
  if (col && col.classList) col.classList.remove('drag-over');
  var id = e.dataTransfer.getData('text/plain');
  if (!id) return;
  var task = _sb2TasksList.find(function(t){ return t.id === id; });
  if (!task) return;
  if (task.status === newStatus) return;
  await sb2TasksMoveStatus(id, newStatus);
}
async function sb2TasksMoveStatus(id, newStatus){
  try {
    var resp = await apiFetch('/api/tasks/' + encodeURIComponent(id), {
      method:'PUT',
      body: JSON.stringify({status: newStatus})
    });
    if (!resp || !resp.ok){
      sb2TasksToast('改状态失败 (HTTP ' + (resp ? resp.status : 'no response') + ')');
      return;
    }
    var updated = await resp.json();
    // 局部更新列表
    var idx = _sb2TasksList.findIndex(function(t){ return t.id === id; });
    if (idx >= 0) _sb2TasksList[idx] = updated;
    sb2TasksRender();
    sb2TasksToast('已改为「' + sb2TasksStatusLabel(newStatus) + '」');
  } catch (e){
    sb2TasksToast('改状态异常: ' + (e && e.message || e));
  }
}

// ★ Modal — 新建 / 编辑
function sb2TasksOpenForm(idOrNull){
  _sb2TasksEditingId = idOrNull || null;
  var modal = document.getElementById('sb2TasksModal');
  var mask = document.getElementById('sb2TasksModalMask');
  var titleEl = document.getElementById('sb2TasksModalTitle');
  var fTitle = document.getElementById('sb2TasksFormTitle');
  var fDesc = document.getElementById('sb2TasksFormDesc');
  var fAssignee = document.getElementById('sb2TasksFormAssignee');
  var fProject = document.getElementById('sb2TasksFormProject');
  var fStatus = document.getElementById('sb2TasksFormStatus');
  var fPriority = document.getElementById('sb2TasksFormPriority');
  var fDeadline = document.getElementById('sb2TasksFormDeadline');
  var fProgress = document.getElementById('sb2TasksFormProgress');
  var noteEl = document.getElementById('sb2TasksEmployeeNote');
  var delBtn = document.getElementById('sb2TasksModalDeleteBtn');
  if (!modal) return;

  // 灌员工下拉 (复用 window.emps, 跟聊天 mentions 同源)
  var emps = (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) ? window.emps : [];
  var asHtml = '<option value="">未指定</option>';
  emps.forEach(function(e){
    asHtml += '<option value="' + sb2TasksEsc(e.id) + '">' + sb2TasksEsc(e.name || e.id) + '</option>';
  });
  if (fAssignee) fAssignee.innerHTML = asHtml;

  var admin = sb2TasksIsAdmin();
  var task = null;
  if (idOrNull){
    task = _sb2TasksList.find(function(t){ return t.id === idOrNull; });
  }
  if (task){
    if (titleEl) titleEl.textContent = '编辑任务';
    if (fTitle) fTitle.value = task.title || '';
    if (fDesc) fDesc.value = task.description || '';
    if (fAssignee) fAssignee.value = task.assignee || '';
    if (fProject) fProject.value = task.project_id || '';
    if (fStatus) fStatus.value = task.status || 'pending';
    if (fPriority) fPriority.value = task.priority || 'normal';
    if (fDeadline) fDeadline.value = task.deadline || '';
    var prog = parseInt(task.progress, 10); if (isNaN(prog)) prog = 0;
    if (fProgress) fProgress.value = prog;
  } else {
    if (titleEl) titleEl.textContent = '新建任务';
    if (fTitle) fTitle.value = '';
    if (fDesc) fDesc.value = '';
    if (fAssignee) fAssignee.value = '';
    if (fProject) fProject.value = '';
    if (fStatus) fStatus.value = 'pending';
    if (fPriority) fPriority.value = 'normal';
    if (fDeadline) fDeadline.value = '';
    if (fProgress) fProgress.value = '0';
  }
  // 员工仅 status + progress (PUT 白名单)
  if (noteEl) noteEl.style.display = admin ? 'none' : '';
  var readonly = !admin;
  [fTitle, fDesc, fAssignee, fProject, fPriority, fDeadline].forEach(function(el){
    if (!el) return;
    el.disabled = readonly;
    el.style.opacity = readonly ? '0.6' : '';
  });
  if (delBtn) delBtn.style.display = (admin && task) ? '' : 'none';
  modal.classList.add('active');
  if (mask) mask.classList.add('active');
  if (fTitle) setTimeout(function(){ fTitle.focus(); }, 50);
}
function sb2TasksCloseForm(){
  var modal = document.getElementById('sb2TasksModal');
  var mask = document.getElementById('sb2TasksModalMask');
  if (modal) modal.classList.remove('active');
  if (mask) mask.classList.remove('active');
  _sb2TasksEditingId = null;
}
async function sb2TasksSaveForm(){
  var admin = sb2TasksIsAdmin();
  var fTitle = document.getElementById('sb2TasksFormTitle');
  var fDesc = document.getElementById('sb2TasksFormDesc');
  var fAssignee = document.getElementById('sb2TasksFormAssignee');
  var fProject = document.getElementById('sb2TasksFormProject');
  var fStatus = document.getElementById('sb2TasksFormStatus');
  var fPriority = document.getElementById('sb2TasksFormPriority');
  var fDeadline = document.getElementById('sb2TasksFormDeadline');
  var fProgress = document.getElementById('sb2TasksFormProgress');
  var title = fTitle ? String(fTitle.value || '').trim() : '';
  if (!title){
    sb2TasksToast('任务标题不能为空');
    if (fTitle) fTitle.focus();
    return;
  }
  var prog = parseInt(fProgress && fProgress.value, 10);
  if (isNaN(prog)) prog = 0;
  if (prog < 0) prog = 0;
  if (prog > 100) prog = 100;
  var emps = (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) ? window.emps : [];
  var assigneeName = '';
  if (fAssignee && fAssignee.value){
    var found = emps.find(function(e){ return e.id === fAssignee.value; });
    assigneeName = found ? (found.name || found.id) : '';
  }
  try {
    var resp;
    if (_sb2TasksEditingId){
      // 编辑
      var body;
      if (admin){
        body = {
          title: title,
          description: fDesc ? fDesc.value : '',
          assignee: fAssignee ? fAssignee.value : '',
          assignee_name: assigneeName,
          status: fStatus ? fStatus.value : 'pending',
          priority: fPriority ? fPriority.value : 'normal',
          deadline: fDeadline ? fDeadline.value : '',
          project_id: fProject ? fProject.value : '',
          progress: String(prog)
        };
      } else {
        // 员工只允许 status + progress
        body = {
          status: fStatus ? fStatus.value : 'pending',
          progress: String(prog)
        };
      }
      resp = await apiFetch('/api/tasks/' + encodeURIComponent(_sb2TasksEditingId), {
        method:'PUT',
        body: JSON.stringify(body)
      });
    } else {
      // 新建 (仅 admin)
      if (!admin){
        sb2TasksToast('仅管理员可创建任务');
        return;
      }
      resp = await apiFetch('/api/tasks', {
        method:'POST',
        body: JSON.stringify({
          title: title,
          description: fDesc ? fDesc.value : '',
          assignee: fAssignee ? fAssignee.value : '',
          assigneeName: assigneeName,
          status: fStatus ? fStatus.value : 'pending',
          priority: fPriority ? fPriority.value : 'normal',
          deadline: fDeadline ? fDeadline.value : '',
          projectId: fProject ? fProject.value : '',
          progress: String(prog)
        })
      });
    }
    if (!resp || !resp.ok){
      sb2TasksToast('保存失败 (HTTP ' + (resp ? resp.status : 'no response') + ')');
      return;
    }
    var saved = await resp.json();
    var idx = _sb2TasksList.findIndex(function(t){ return t.id === saved.id; });
    if (idx >= 0) _sb2TasksList[idx] = saved;
    else _sb2TasksList.unshift(saved);
    sb2TasksCloseForm();
    sb2TasksRender();
    sb2TasksToast(_sb2TasksEditingId ? '已保存' : '已新建');
  } catch (e){
    sb2TasksToast('保存异常: ' + (e && e.message || e));
  }
}

// ★ 删除二次确认
function sb2TasksAskDelete(){
  if (!_sb2TasksEditingId) return;
  _sb2TasksDeleteId = _sb2TasksEditingId;
  var task = _sb2TasksList.find(function(t){ return t.id === _sb2TasksDeleteId; });
  var titleEl = document.getElementById('sb2TasksConfirmTitle');
  if (titleEl) titleEl.textContent = task ? (task.title || '-') : '-';
  var confirm = document.getElementById('sb2TasksConfirm');
  var mask = document.getElementById('sb2TasksConfirmMask');
  if (confirm) confirm.classList.add('active');
  if (mask) mask.classList.add('active');
}
function sb2TasksCloseConfirm(){
  var confirm = document.getElementById('sb2TasksConfirm');
  var mask = document.getElementById('sb2TasksConfirmMask');
  if (confirm) confirm.classList.remove('active');
  if (mask) mask.classList.remove('active');
  _sb2TasksDeleteId = null;
}
async function sb2TasksConfirmDelete(){
  if (!_sb2TasksDeleteId) return;
  try {
    var resp = await apiFetch('/api/tasks/' + encodeURIComponent(_sb2TasksDeleteId), {method:'DELETE'});
    if (!resp || !resp.ok){
      sb2TasksToast('删除失败 (HTTP ' + (resp ? resp.status : 'no response') + ')');
      return;
    }
    _sb2TasksList = _sb2TasksList.filter(function(t){ return t.id !== _sb2TasksDeleteId; });
    sb2TasksCloseConfirm();
    sb2TasksCloseForm();
    sb2TasksRender();
    sb2TasksToast('已删除');
  } catch (e){
    sb2TasksToast('删除异常: ' + (e && e.message || e));
  }
}

// ★ MVP1: 主加载 — 端点契约 GET /api/tasks
async function sb2TasksLoad(){
  var heroSub = document.getElementById('sb2TasksHeroSub');
  var sideSub = document.getElementById('sb2TasksSideSub');
  if (heroSub) heroSub.textContent = '载入中…';
  if (sideSub) sideSub.textContent = '载入中…';
  try {
    var resp = await apiFetch('/api/tasks');
    if (!resp || !resp.ok){
      if (heroSub) heroSub.textContent = '载入失败';
      return;
    }
    var data = await resp.json();
    _sb2TasksList = (data && Array.isArray(data.tasks)) ? data.tasks : [];
    sb2TasksRender();
    _sb2TasksLoaded = true;
  } catch (e){
    if (heroSub) heroSub.textContent = '载入异常';
    sb2TasksToast('载入异常: ' + (e && e.message || e));
  }
}

// ★ MVP1: 入口 (切模块时调一次)
function sb2TasksInit(){
  if (!_sb2TasksLoaded) sb2TasksLoad();
  else sb2TasksRender();
  // MVP1 P1 fix: 初始化时显式同步 wrap display, 兜底 toggle 残留 inline style
  sb2TasksSwitchView(_sb2TasksView || 'board');
}

// ★ SEV1 教训: 跨脚本块调用挂 window (渲染函数被 sb2_hideAllModuleMains / switchModule 跨块调用)
window.sb2TasksInit = sb2TasksInit;
window.sb2TasksLoad = sb2TasksLoad;
window.sb2TasksRender = sb2TasksRender;
window.sb2TasksOnSearch = sb2TasksOnSearch;
window.sb2TasksOnSideClick = sb2TasksOnSideClick;
window.sb2TasksSwitchView = sb2TasksSwitchView;
window.sb2TasksOpenForm = sb2TasksOpenForm;
window.sb2TasksCloseForm = sb2TasksCloseForm;
window.sb2TasksSaveForm = sb2TasksSaveForm;
window.sb2TasksAskDelete = sb2TasksAskDelete;
window.sb2TasksCloseConfirm = sb2TasksCloseConfirm;
window.sb2TasksConfirmDelete = sb2TasksConfirmDelete;
window.sb2TasksMoveStatus = sb2TasksMoveStatus;
window.sb2TasksOnDragStart = sb2TasksOnDragStart;
window.sb2TasksOnDragOver = sb2TasksOnDragOver;
window.sb2TasksOnDragLeave = sb2TasksOnDragLeave;
window.sb2TasksOnDragEnd = sb2TasksOnDragEnd;
window.sb2TasksOnDrop = sb2TasksOnDrop;
window.sb2TasksToast = sb2TasksToast;
/* ============================================================
 * sb2-products MVP1: 商品库屏 JS (协调人设计定稿, 小路执行)
 * 仿 sb2-patterns precedent — hero 动态 + 全宽表格 + 720 弹窗 + 动态页尺寸
 * 数据源: GET /api/products?limit=500 (全量一次拉, 实测 87 条)
 * server 零改动; 不动旧 products 函数; 不动其他模块
 *
 * escHtml/escAttr 在内层 IIFE 私有, 跨 IIFE 不可访问 → 本块自带副本
 * ============================================================ */

function escHtml(s){ return String(s==null?'':s).replace(/[&<>"\x27]/g, function(c){ return ({'&amp;':'&amp;','&lt;':'&lt;','&gt;':'&gt;','&quot;':'&quot;',"&#39;":'&#39;'})[c]; }); }
function escAttr(s){ return String(s==null?'':s).replace(/[&<>"\x27]/g, function(c){ return ({'&amp;':'&amp;','&lt;':'&lt;','&gt;':'&gt;','&quot;':'&quot;',"&#39;":'&#39;'})[c]; }); }

var _sb2PdsStatusLabel = { active:'在售', out_of_stock:'缺货', discontinued:'停售' };
var _sb2PdsProducts = []; var _sb2PdsTotal = 0;
var _sb2PdsCat = ''; var _sb2PdsStatus = ''; var _sb2PdsBrand = '';
var _sb2PdsQ = ''; var _sb2PdsSort = 'default'; var _sb2PdsPage = 1;
var _sb2PdsBrands = [];
var _sb2PdsShrink = 0; /* r34.3 一次性收缩锁: 实测行高超预期溢出时减一页重渲, 防递归 */

function _sb2PdsPageSize(){
  /* 〔fix/sb2-side-restore r34.3 协调人代补〕直接量几何, 取代 magic number 预留:
     24 轮 v2 口径「每页装满视口不滚动」— 旧版 reserved 常数 12+56 与真实 pager/行高错位,
     实测 10 行 617px > wrap 579px (溢 38px)。现按 hero/filter/pager 真实 rect 量剩余高度 */
  if (_sb2PdsShrink) return _sb2PdsShrink;
  var main = document.getElementById('sb2ProductsMain');
  if (!main) return 10;
  var mcs = getComputedStyle(main);
  var borderT = parseFloat(mcs.borderTopWidth) || 0;
  var padB = parseFloat(mcs.paddingBottom) || 0;
  var wrap = main.querySelector('.sb2-pds-table-wrap');
  var pager = main.querySelector('.sb2-pds-pager');
  var sampleRow = main.querySelector('.sb2-pds-table tbody tr');
  var rowH = sampleRow ? sampleRow.offsetHeight : 44;
  var pitch = rowH + 1;
  var mainTop = main.getBoundingClientRect().top;
  var wrapTop = wrap ? (wrap.getBoundingClientRect().top - mainTop - borderT) : 112;
  var wrapMt = wrap ? (parseFloat(getComputedStyle(wrap).marginTop) || 0) : 0;
  var pagerH = 44;
  if (pager) {
    var pcs = getComputedStyle(pager);
    pagerH = pager.getBoundingClientRect().height + (parseFloat(pcs.marginTop) || 0) + (parseFloat(pcs.marginBottom) || 0) + 8;
  }
  var avail = main.clientHeight - wrapTop - wrapMt - pagerH - padB - 4;
  return Math.max(5, Math.floor(avail / pitch));
}

function sb2PdsUpdateHeroTitle(){
  var hero = document.getElementById('sb2PdsHeroTitle');
  if (!hero) return;
  if (_sb2PdsCat) { hero.textContent = _sb2PdsCat; return; }
  if (_sb2PdsStatus) { hero.textContent = _sb2PdsStatusLabel[_sb2PdsStatus] || _sb2PdsStatus; return; }
  hero.textContent = '全部商品';
}

function switchProductsCategory(cat){
  _sb2PdsCat = (_sb2PdsCat === (cat || '')) ? '' : (cat || '');
  document.querySelectorAll('.sb2-side--module-products .sb2-side-item').forEach(function(it){
    var _oc = it.getAttribute('onclick') || '';
    it.classList.toggle('active', _sb2PdsCat !== '' && _oc.indexOf("switchProductsCategory('" + _sb2PdsCat + "')") >= 0);
  });
  _sb2PdsPage = 1;
  sb2PdsRender();
}

function sb2PdsFilterStatus(status){
  _sb2PdsStatus = status || '';
  document.querySelectorAll('.sb2-pds-chip').forEach(function(c){ c.classList.toggle('active', (c.dataset.status || '') === _sb2PdsStatus); });
  _sb2PdsPage = 1;
  sb2PdsRender();
}

function sb2PdsOnSearch(q){ _sb2PdsQ = String(q || '').trim().toLowerCase(); _sb2PdsPage = 1; sb2PdsRender(); }
function sb2PdsOnBrandChange(brand){ _sb2PdsBrand = brand || ''; _sb2PdsPage = 1; sb2PdsRender(); }
function sb2PdsOnSortChange(sort){ _sb2PdsSort = sort || 'default'; _sb2PdsPage = 1; sb2PdsRender(); }

function _sb2PdsFilteredSorted(){
  var q = _sb2PdsQ, brand = _sb2PdsBrand, cat = _sb2PdsCat, status = _sb2PdsStatus;
  var arr = _sb2PdsProducts.filter(function(p){
    if (cat && (p.category || '') !== cat) return false;
    if (brand && (p.brand || '') !== brand) return false;
    if (status && (p.status || '') !== status) return false;
    if (q) { var hay = ((p.name || '') + ' ' + (p.subtitle || '') + ' ' + (p.brand || '')).toLowerCase(); if (hay.indexOf(q) === -1) return false; }
    return true;
  });
  if (typeof sortProductsLocally === 'function' && _sb2PdsSort) { try { arr = sortProductsLocally(arr, _sb2PdsSort); } catch(e) {} }
  return arr;
}

function sb2PdsRender(){
  sb2PdsUpdateHeroTitle();
  var heroSub = document.getElementById('sb2PdsHeroSub');
  var tbody = document.getElementById('sb2PdsTbody');
  var pager = document.getElementById('sb2PdsPager');
  if (!tbody) return;

  var filtered = _sb2PdsFilteredSorted();
  var totalActive = 0, totalOos = 0;
  _sb2PdsProducts.forEach(function(p){
    var s = p.status || '';
    if (s === 'active') totalActive++;
    else if (s === 'out_of_stock') totalOos++;
  });
  var catCount = 0; var _cats = {};
  _sb2PdsProducts.forEach(function(p){
    var c = p.category || '';
    if (c && !_cats[c]) { _cats[c] = 1; catCount++; }
  });
  if (heroSub) heroSub.textContent = '共 ' + _sb2PdsTotal + ' 商品 · ' + catCount + ' 类目 · 在售 ' + totalActive + ' · 缺货 ' + totalOos;

  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8"><div class="sb2-pds-empty">'
      + '<div class="sb2-pds-empty-icon">📦</div>'
      + '<div class="sb2-pds-empty-text">暂无商品</div>'
      + '<button class="sb2-pds-empty-btn" onclick="createNewProduct()">录入商品</button>'
      + '</div></td></tr>';
    if (pager) pager.innerHTML = '';
    return;
  }

  var pageSize = _sb2PdsPageSize();
  var totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  if (_sb2PdsPage > totalPages) _sb2PdsPage = totalPages;
  var startIdx = (_sb2PdsPage - 1) * pageSize;
  var slice = filtered.slice(startIdx, startIdx + pageSize);

  var html = '';
  slice.forEach(function(p){
    var imgHtml;
    if (p.main_image) imgHtml = '<div class="sb2-pds-cell-img"><img src="' + escAttr(p.main_image) + '" alt="" /></div>';
    else { var initial = escHtml((p.name || '?').charAt(0).toUpperCase()); imgHtml = '<div class="sb2-pds-cell-img">' + initial + '</div>'; }
    var nameMain = escHtml(p.name || '-');
    var nameSubParts = [];
    if (p.brand) nameSubParts.push(escHtml(p.brand));
    if (p.category) nameSubParts.push(escHtml(p.category));
    var nameSub = nameSubParts.join(' · ');
    var adminPart = '';
    if (p.createdByName) adminPart = '<span class="sb2-pds-cell-name-admin">· 👤 ' + escHtml(p.createdByName) + '</span>';

    var priceHtml = '<span class="sb2-pds-cell-price">¥' + (typeof p.price === 'number' ? p.price.toFixed(2) : escHtml(String(p.price || '-'))) + '</span>';

    var commHtml;
    if (p.commission_rates && typeof p.commission_rates === 'object' && Object.keys(p.commission_rates).length > 0) {
      var keys = Object.keys(p.commission_rates);
      commHtml = '<span class="sb2-pds-cell-commission">' + keys.length + ' 档</span>';
    } else if (p.commission_rate && typeof p.commission_rate === 'number' && p.commission_rate > 0) {
      commHtml = '<span class="sb2-pds-cell-commission">' + p.commission_rate + '%</span>';
    } else {
      commHtml = '<span class="sb2-pds-cell-commission sb2-pds-cell-commission-empty">待设置</span>';
    }

    var sales = typeof p.monthly_sales === 'number' ? p.monthly_sales : 0;
    var salesHtml = '<span class="sb2-pds-cell-sales">'
      + '<svg class="sb2-pds-spark" viewBox="0 0 48 18" preserveAspectRatio="none">'
      + _sb2PdsSparkPath(p.id || sales) + '</svg>'
      + '<span class="sb2-pds-trend flat">' + sales + '</span></span>';

    var infHtml = '<span>' + (p.influencer_count || 0) + '</span>';
    var stockHtml = '<span>' + (typeof p.stock === 'number' ? p.stock : 0) + '</span>';

    var statusKey = p.status || 'active';
    var statusLabel = _sb2PdsStatusLabel[statusKey] || statusKey;
    var statusHtml = '<span class="sb2-pds-badge ' + escAttr(statusKey) + '">' + escHtml(statusLabel) + '</span>';

    html += '<tr data-id="' + escAttr(p.id) + '">';
    html += '<td><div style="display:flex;gap:10px;align-items:center;">' + imgHtml
      + '<div class="sb2-pds-cell-name">'
      + '<div class="sb2-pds-cell-name-main">' + nameMain + adminPart + '</div>'
      + (nameSub ? '<div class="sb2-pds-cell-name-sub">' + nameSub + '</div>' : '')
      + '</div></div></td>';
    html += '<td>' + priceHtml + '</td>';
    html += '<td>' + commHtml + '</td>';
    html += '<td>' + salesHtml + '</td>';
    html += '<td>' + infHtml + '</td>';
    html += '<td>' + stockHtml + '</td>';
    html += '<td>' + statusHtml + '</td>';
    /* ★ r39.3-P0: 行操作 cell — 找达人弹窗入口 (规格项 3, 仿 r39.1 项 2 sb2TalentsOpenAiMatch precedent)
       跟项 2 对称: 商品行 hover 操作区 cell + 居中 720 弹窗 + sb2-pds-rise 动画
       r38 IIFE 踩坑: HTML onclick 必须 window.* 暴露 */
    html += '<td class="sb2-pds-cell-action"><button class="sb2-pds-findtalent-btn" onclick="sb2PdsOpenFindTalents(\'' + escAttr(p.id) + '\')">找达人</button></td>';
    html += '</tr>';
  });
  tbody.innerHTML = html;
  Array.from(tbody.querySelectorAll('tr[data-id]')).forEach(function(tr){
    tr.addEventListener('click', function(){ var id = tr.getAttribute('data-id'); sb2PdsOpenDetail(id); });
  });

  sb2PdsBuildPager(filtered.length, pageSize, pager);
  /* 〔r34.3〕行高实测收敛: 渲染后若表格溢出 (首渲无样本行, fallback 44px 常低估双行名称行高),
     一次性减一页重渲; _sb2PdsShrink 锁防递归, 重渲后归零 */
  var wrapEl = document.querySelector('.sb2-pds-table-wrap');
  if (wrapEl && _sb2PdsShrink === 0 && wrapEl.scrollHeight > wrapEl.clientHeight && pageSize > 5) {
    /* 用刚渲染的真实行高反推精确 fit, 一次收敛 (减一页迭代在 fallback 44 vs 实际 58 行高时要跑多轮) */
    var _r0 = wrapEl.querySelector('tbody tr');
    var _pitch = _r0 ? (_r0.offsetHeight + 1) : 59;
    var _fit = Math.max(5, Math.floor((wrapEl.clientHeight - 2) / _pitch));
    if (_fit < pageSize) {
      _sb2PdsShrink = _fit;
      sb2PdsRender();
    }
    _sb2PdsShrink = 0;
  }
  /* 〔r34.3〕resize 200ms 防抖重算页尺寸 (对齐 knowledge 24 轮 v2 口径, 不重拉数据) */
  if (!window._sb2PdsResizeBound) {
    window._sb2PdsResizeBound = true;
    var _sb2PdsRsT = null;
    window.addEventListener('resize', function(){
      clearTimeout(_sb2PdsRsT);
      _sb2PdsRsT = setTimeout(function(){
        var m = document.getElementById('sb2ProductsMain');
        if (m && m.classList.contains('active') && _sb2PdsProducts.length) sb2PdsRender();
      }, 200);
    });
  }
}

function _sb2PdsSparkPath(seed){
  var h = 0; var s = String(seed || '');
  for (var i = 0; i < s.length; i++) h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  var pts = [];
  for (var k = 0; k < 5; k++){ h = ((h << 5) - h + k) | 0; var y = 2 + Math.abs(h % 13); pts.push((k * 12) + ',' + y); }
  return '<polyline points="' + pts.join(' ') + '" fill="none" stroke="currentColor" stroke-width="1.2" />';
}

function sb2PdsBuildPager(totalItems, pageSize, container){
  if (!container) return;
  var totalPages = Math.max(1, Math.ceil(totalItems / pageSize));
  var cur = _sb2PdsPage;
  var html = '';
  html += '<button ' + (cur <= 1 ? 'disabled' : '') + ' onclick="sb2PdsGoPage(' + (cur - 1) + ')">‹</button>';
  var showPages = [];
  for (var p = 1; p <= totalPages; p++){
    if (p === 1 || p === totalPages || (p >= cur - 1 && p <= cur + 1)) showPages.push(p);
    else if (showPages[showPages.length - 1] !== '…') showPages.push('…');
  }
  showPages.forEach(function(p){
    if (p === '…') html += '<span style="padding:0 4px;color:var(--sb2-t3);">…</span>';
    else html += '<button class="' + (p === cur ? 'active' : '') + '" onclick="sb2PdsGoPage(' + p + ')">' + p + '</button>';
  });
  html += '<button ' + (cur >= totalPages ? 'disabled' : '') + ' onclick="sb2PdsGoPage(' + (cur + 1) + ')">›</button>';
  html += '<span class="sb2-pds-pager-info">第 ' + cur + ' / ' + totalPages + ' 页 · 共 ' + totalItems + ' 条</span>';
  container.innerHTML = html;
}

function sb2PdsGoPage(p){
  var totalPages = Math.max(1, Math.ceil(_sb2PdsFilteredSorted().length / _sb2PdsPageSize()));
  _sb2PdsPage = Math.max(1, Math.min(totalPages, p));
  sb2PdsRender();
}

function sb2PdsOpenDetail(id){
  var p = _sb2PdsProducts.find(function(x){ return x.id === id; });
  var overlay = document.getElementById('sb2PdsDetailOverlay');
  var titleEl = document.getElementById('sb2PdsDetailTitle');
  var bodyEl = document.getElementById('sb2PdsDetailBody');
  if (!overlay || !bodyEl) return;
  if (!p) { showToast('商品不存在或已删除', 'warning'); return; }
  titleEl.textContent = p.name || '-';
  bodyEl.innerHTML = _sb2PdsDetailHtml(p);
  overlay.classList.add('open');
}

function sb2PdsCloseDetail(){
  var overlay = document.getElementById('sb2PdsDetailOverlay');
  if (overlay) overlay.classList.remove('open');
}

function _sb2PdsDetailHtml(p){
  var h = '';
  if (p.main_image) h += '<img class="sb2-pds-detail-hero-img" src="' + escAttr(p.main_image) + '" alt="" />';
  else h += '<div class="sb2-pds-detail-hero-img-empty">暂无图片</div>';

  h += '<div class="sb2-pds-detail-section">';
  h += '<div class="sb2-pds-detail-section-title">基础信息</div>';
  h += _sb2PdsDetailRow('名称', p.name);
  h += _sb2PdsDetailRow('副标', p.subtitle);
  h += _sb2PdsDetailRow('品牌', p.brand);
  h += _sb2PdsDetailRow('类目', p.category);
  h += _sb2PdsDetailRow('状态', _sb2PdsStatusLabel[p.status] || p.status);
  h += _sb2PdsDetailRow('库存', (typeof p.stock === 'number') ? String(p.stock) : '0');
  if (p.createdByName) h += _sb2PdsDetailRow('创建人', p.createdByName);
  h += '</div>';

  h += '<div class="sb2-pds-detail-section">';
  h += '<div class="sb2-pds-detail-section-title">价格 · 佣金</div>';
  h += _sb2PdsDetailRow('价格', '¥' + (typeof p.price === 'number' ? p.price.toFixed(2) : '-'));
  if (p.original_price && p.original_price > 0) h += _sb2PdsDetailRow('原价', '¥' + p.original_price.toFixed(2));
  if (p.commission_rates && Object.keys(p.commission_rates).length) {
    var ratesStr = Object.keys(p.commission_rates).map(function(k){ return k + ': ' + p.commission_rates[k] + '%'; }).join(' · ');
    h += _sb2PdsDetailRow('佣金档', ratesStr);
  } else if (p.commission_rate) h += _sb2PdsDetailRow('佣金率', p.commission_rate + '%');
  else h += _sb2PdsDetailRow('佣金率', '待设置');
  if (p.commission_amount) h += _sb2PdsDetailRow('佣金金额', '¥' + p.commission_amount.toFixed(2));
  h += '</div>';

  h += '<div class="sb2-pds-detail-section">';
  h += '<div class="sb2-pds-detail-section-title">销售数据</div>';
  h += _sb2PdsDetailRow('月销', String(p.monthly_sales || 0));
  h += _sb2PdsDetailRow('月 GMV', p.monthly_gmv ? '¥' + p.monthly_gmv.toFixed(2) : '-');
  if (p.conversion_rate) h += _sb2PdsDetailRow('转化率', p.conversion_rate + '%');
  if (p.avg_order_value) h += _sb2PdsDetailRow('客单价', '¥' + p.avg_order_value.toFixed(2));
  h += '</div>';

  h += '<div class="sb2-pds-detail-section">';
  h += '<div class="sb2-pds-detail-section-title">带货</div>';
  h += _sb2PdsDetailRow('带货达人', String(p.influencer_count || 0) + ' 人');
  h += _sb2PdsDetailRow('关联视频', String(p.video_count || p.talent_count || 0) + ' 条');
  h += '</div>';

  if (p.selling_points) {
    h += '<div class="sb2-pds-detail-section">';
    h += '<div class="sb2-pds-detail-section-title">卖点</div>';
    h += '<div class="sb2-pds-detail-row-val" style="padding:4px 0;">' + escHtml(p.selling_points) + '</div>';
    h += '</div>';
  }
  if (Array.isArray(p.tags) && p.tags.length) {
    h += '<div class="sb2-pds-detail-section">';
    h += '<div class="sb2-pds-detail-section-title">标签</div>';
    h += '<div class="sb2-pds-detail-tags">';
    p.tags.forEach(function(t){ h += '<span class="sb2-pds-detail-tag">' + escHtml(String(t)) + '</span>'; });
    h += '</div></div>';
  }
  if (p.description) {
    h += '<div class="sb2-pds-detail-section">';
    h += '<div class="sb2-pds-detail-section-title">描述</div>';
    h += '<div class="sb2-pds-detail-row-val" style="padding:4px 0;white-space:pre-wrap;">' + escHtml(p.description) + '</div>';
    h += '</div>';
  }
  return h;
}

function _sb2PdsDetailRow(key, val){
  return '<div class="sb2-pds-detail-row">'
    + '<div class="sb2-pds-detail-row-key">' + escHtml(key) + '</div>'
    + '<div class="sb2-pds-detail-row-val">' + escHtml(val == null ? '-' : String(val)) + '</div>'
    + '</div>';
}

/* ============================================================
 * r39.3-P0: 商品侧「找达人」弹窗 (项 3, 仿 r39.1 项 2 sb2-tln-aimatch precedent 反转)
 * 规格: docs/design-spec/r39-match-chain.md §二·项3
 * 弹窗: 仿 sb2-pds-detail 居中 720 + sb2-pds-rise 动画 precedent (22 轮批注③ 口径)
 * 调用: POST /api/ai-match direction=product-to-talent limit=10
 * 字段真名 (字段名铁律, server line 20132-20141, 协调人实测响应驼峰):
 *   matches[].talent + score + matchPercent + ruleScore + aiScore
 *           + reasons (string[]) + aiReason (string) + is_ai_recommended (≥75)
 * 响应顶层: matches + total + ai_scored + degraded (bool) + degrade_reason
 * 加载态文案加「最长约 1 分钟」(r39.1 协调人实测: 冷启动 AI 45s+, 热后 8s)
 * apply when: 任何「IIFE 内函数给 HTML onclick 用」= 主动 window.X = X 暴露 (r38 IIFE 踩坑)
 * ============================================================ */

/* 弹窗状态 (避免全局污染) */
var _sb2PdsFindTalentsState = {
  productId: null,
  matches: [],
  degraded: false,
  degrade_reason: null,
  inFlight: false
};

function sb2PdsOpenFindTalents(productId){
  if (!productId) return;
  var overlay = document.getElementById('sb2PdsFindtalentOverlay');
  var title = document.getElementById('sb2PdsFindtalentTitle');
  var sub = document.getElementById('sb2PdsFindtalentSub');
  var body = document.getElementById('sb2PdsFindtalentBody');
  var degrade = document.getElementById('sb2PdsFindtalentDegrade');
  if (!overlay || !body) return;
  /* 找商品名 (从 cache) */
  var plist = (typeof _sb2PdsProducts !== 'undefined') ? _sb2PdsProducts : [];
  var p = plist.find(function(x){ return x && x.id === productId; });
  var pName = p ? (p.name || productId) : productId;
  if (title) title.textContent = '找达人 · ' + pName;
  if (sub) sub.textContent = 'POST /api/ai-match · product-to-talent · TOP 10';
  if (degrade) degrade.style.display = 'none';
  /* ★ 加载态文案加「最长约 1 分钟」提示 (协调人 r39.1 闭环实测: 冷启动 45s+, 热后 8s) */
  body.innerHTML = '<div class="sb2-pds-findtalent-loading">'
    + '<div class="spinner"></div>AI 正在分析匹配…'
    + '<div style="margin-top:8px;font-size:11px;color:var(--sb2-t3);">最长约 1 分钟 (首次冷启动较慢, 热后约 8 秒)</div>'
    + '</div>';
  overlay.classList.add('open');
  _sb2PdsFindTalentsState.productId = productId;
  _sb2PdsFindTalentsState.matches = [];
  _sb2PdsFindTalentsState.degraded = false;
  _sb2PdsFindTalentsState.degrade_reason = null;
  _sb2PdsFindTalentsState.inFlight = true;
  sb2PdsFetchFindTalents(productId);
}

function sb2PdsCloseFindTalents(){
  var overlay = document.getElementById('sb2PdsFindtalentOverlay');
  if (overlay) overlay.classList.remove('open');
  _sb2PdsFindTalentsState.productId = null;
  _sb2PdsFindTalentsState.inFlight = false;
}

async function sb2PdsFetchFindTalents(productId){
  var body = document.getElementById('sb2PdsFindtalentBody');
  var degrade = document.getElementById('sb2PdsFindtalentDegrade');
  try {
    var resp = await apiFetch('/api/ai-match', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ direction: 'product-to-talent', productId: productId, limit: 10 })
    });
    if (!resp || !resp.ok) {
      _sb2PdsFindTalentsState.inFlight = false;
      if (body) body.innerHTML = '<div class="sb2-pds-findtalent-empty"><span class="sb2-pds-findtalent-empty-icon">⚠️</span>权限不足或网络错误, 请重试 (HTTP ' + (resp ? resp.status : 'no resp') + ')</div>';
      return;
    }
    var data = await resp.json();
    _sb2PdsFindTalentsState.matches = (data && Array.isArray(data.matches)) ? data.matches : [];
    _sb2PdsFindTalentsState.degraded = !!(data && data.degraded);
    _sb2PdsFindTalentsState.degrade_reason = (data && data.degrade_reason) || null;
    _sb2PdsFindTalentsState.inFlight = false;
    if (degrade && _sb2PdsFindTalentsState.degraded) {
      degrade.textContent = '⚠️ AI 暂不可用, 已展示规则匹配结果 (' + _sb2PdsFindTalentsState.degrade_reason + ')';
      degrade.style.display = '';
    } else if (degrade) {
      degrade.style.display = 'none';
    }
    sb2PdsRenderFindTalents();
  } catch (e) {
    _sb2PdsFindTalentsState.inFlight = false;
    if (body) body.innerHTML = '<div class="sb2-pds-findtalent-empty"><span class="sb2-pds-findtalent-empty-icon">⚠️</span>网络错误: ' + (e && e.message || e) + '</div>';
  }
}

function sb2PdsRenderFindTalents(){
  var body = document.getElementById('sb2PdsFindtalentBody');
  if (!body) return;
  var matches = _sb2PdsFindTalentsState.matches || [];
  if (matches.length === 0) {
    body.innerHTML = '<div class="sb2-pds-findtalent-empty"><span class="sb2-pds-findtalent-empty-icon">🪺</span>暂无匹配达人</div>';
    return;
  }
  var html = '<div class="sb2-pds-findtalent-list">';
  for (var i = 0; i < matches.length; i++) {
    var m = matches[i];
    var t = m.talent || {};
    var name = t.name || '-';
    var cat = t.category || '';
    var ruleScore = (typeof m.ruleScore === 'number') ? m.ruleScore : 0;
    var aiScore = (typeof m.aiScore === 'number') ? m.aiScore : 0;
    var finalScore = (typeof m.score === 'number') ? m.score : ruleScore;
    var reasons = Array.isArray(m.reasons) ? m.reasons : [];
    var aiReason = m.aiReason || '';
    var allReasons = (aiReason ? [aiReason] : []).concat(reasons).slice(0, 4);
    var reasonsHtml = allReasons.length > 0
      ? '<ul class="sb2-pds-findtalent-reasons">' + allReasons.map(function(r){ return '<li>' + escAttr(r) + '</li>'; }).join('') + '</ul>'
      : '<div class="sb2-pds-findtalent-reasons">无推荐理由</div>';
    /* ★ 字段名铁律: matches[i].talent.id 是真 id, matches[i].talent.name 是真名
       (server line 20099 / 20132-20141 真代码读源, 协调人实测响应驼峰) */
    var talentId = t.id || '';
    var productId = _sb2PdsFindTalentsState.productId || '';
    var tEscId = escAttr(talentId);
    var tEscName = escAttr(name);
    var catEsc = escAttr(cat);
    var isTop = (i === 0);
    html += '<div class="sb2-pds-findtalent-card" data-tid="' + tEscId + '">'
      + '<div class="sb2-pds-findtalent-card-head">'
      +   '<div class="sb2-pds-findtalent-card-name">' + tEscName + '</div>'
      +   (cat ? '<div class="sb2-pds-findtalent-card-cat">' + catEsc + '</div>' : '')
      + '</div>'
      + '<div class="sb2-pds-findtalent-scores">'
      +   '<span class="score-rule">规则分 ' + ruleScore.toFixed(1) + '</span>'
      +   '<span class="score-ai">AI 分 ' + aiScore.toFixed(1) + '</span>'
      +   '<span>综合 ' + finalScore.toFixed(1) + '</span>'
      + '</div>'
      + reasonsHtml
      + '<div class="sb2-pds-findtalent-card-foot">'
      +   '<button onclick="sb2PdsSubmitFindTalentProposal(\'' + tEscId + '\')">生成合作方案</button>'
      +   (isTop ? '<button class="primary" onclick="sb2PdsSubmitFindTalentProposal(\'' + tEscId + '\')">TOP 1 快速生成</button>' : '')
      + '</div>'
      + '</div>';
  }
  html += '</div>';
  body.innerHTML = html;
}

/* 项 4 (产品→达人方向): 生成合作方案 → POST /api/proposals
   跟 r39.1 项 4 同款: server line 18870-18873 强制 localhost_agent_id (X-Agent-Id),
   远程 admin 用户 POST 会 403 — 带 X-Agent-Id 头, 失败兜底显「待协调人放宽鉴权」 */
async function sb2PdsSubmitFindTalentProposal(talentId){
  if (!talentId) return;
  var productId = _sb2PdsFindTalentsState.productId;
  if (!productId) {
    if (typeof showToast === 'function') showToast('请先打开找达人弹窗', 'warn');
    return;
  }
  /* r39-7 智能派单中继口径 (老大拍板 B, 协调人代改对齐):
     /api/proposals 保持 AI 专属硬闸, 真人走 /api/collaboration-propose,
     服务端挑 AI 员工代建方案 — 跟 sb2TalentsSubmitMatchProposal 同款 */
  var match = (_sb2PdsFindTalentsState.matches || []).find(function(x){
    return x && x.talent && x.talent.id === talentId;
  });
  var score = match ? (match.matchPercent || match.score || '') : '';
  try {
    var resp = await apiFetch('/api/collaboration-propose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ talentId: talentId, productId: productId, score: score })
    });
    if (resp && resp.ok) {
      var data = null;
      try { data = await resp.json(); } catch (e) { data = null; }
      var agentName = (data && data.dispatched_agent) ? data.dispatched_agent : 'AI 员工';
      if (data && data.duplicate) {
        if (typeof showToast === 'function') showToast('该合作方案已存在, 未重复创建', 'info');
      } else {
        if (typeof showToast === 'function') showToast('✅ 已派给 ' + agentName + ' 生成方案, 待审批', 'success');
      }
      sb2PdsCloseFindTalents();
    } else if (resp && (resp.status === 403 || resp.status === 401)) {
      /* 不静默吞错 (跟 feat/p03-quality-loop 「不再吞错」 21 轮同款) */
      if (typeof showToast === 'function') showToast('⚠️ 请先登录后再生成合作方案 (HTTP ' + resp.status + ')', 'warn');
    } else {
      if (typeof showToast === 'function') showToast('⚠️ 生成失败 (HTTP ' + (resp ? resp.status : '?') + ')', 'warn');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast('⚠️ 网络错误: ' + (e && e.message || e), 'warn');
  }
}

function sb2PdsRenderBrands(){
  var sel = document.getElementById('sb2PdsBrand');
  if (!sel) return;
  var brands = (Array.isArray(window.productBrands) ? window.productBrands : [])
    .concat(Array.isArray(_sb2PdsBrands) ? _sb2PdsBrands : []);
  var seen = {}; var uniq = [];
  brands.forEach(function(b){ var name = (b && (b.name || b)) || ''; if (name && !seen[name]) { seen[name] = 1; uniq.push(name); } });
  _sb2PdsProducts.forEach(function(p){ var b = p.brand || ''; if (b && !seen[b]) { seen[b] = 1; uniq.push(b); } });
  var html = '<option value="">全部品牌</option>';
  uniq.forEach(function(n){ html += '<option value="' + escAttr(n) + '">' + escHtml(n) + '</option>'; });
  sel.innerHTML = html;
}

async function sb2ProductsInit(){
  try {
    var heroSub = document.getElementById('sb2PdsHeroSub');
    if (heroSub) heroSub.textContent = '载入中…';
    var tbody = document.getElementById('sb2PdsTbody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" class="sb2-pds-empty">载入中…</td></tr>';

    var resp = await apiFetch('/api/products?limit=500');
    if (!resp || !resp.ok) {
      if (heroSub) heroSub.textContent = '加载失败 (HTTP ' + (resp ? resp.status : 'no response') + ')';
      if (tbody) tbody.innerHTML = '<tr><td colspan="8"><div class="sb2-pds-empty"><div class="sb2-pds-empty-icon">📦</div><div class="sb2-pds-empty-text">加载失败，请刷新重试</div></div></td></tr>';
      return;
    }
    var data = await resp.json();
    _sb2PdsProducts = (data && Array.isArray(data.products)) ? data.products : [];
    _sb2PdsTotal = (data && typeof data.total === 'number') ? data.total : _sb2PdsProducts.length;
    try { if (typeof loadProductBrands === 'function') loadProductBrands(); } catch(e) {}
    sb2PdsRenderBrands();
    _sb2PdsPage = 1;
    sb2PdsRender();
  } catch (e) {
    console.error('[sb2-products] init error:', e);
    showToast('❌ 商品库加载失败: ' + (e.message || e), 'error');
  }
}

(function _sb2PdsResizeWatch(){
  var timer = null;
  window.addEventListener('resize', function(){
    if (timer) clearTimeout(timer);
    timer = setTimeout(function(){
      var main = document.getElementById('sb2ProductsMain');
      if (main && main.classList.contains('active')) sb2PdsRender();
    }, 200);
  });
})();

(function _sb2PdsDetailEvents(){
  function _close(){ sb2PdsCloseDetail(); }
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', _bind); } else { _bind(); }
  function _bind(){
    var overlay = document.getElementById('sb2PdsDetailOverlay');
    var closeBtn = document.getElementById('sb2PdsDetailClose');
    if (overlay) overlay.addEventListener('click', function(e){ if (e.target === overlay) _close(); });
    if (closeBtn) closeBtn.addEventListener('click', _close);
    document.addEventListener('keydown', function(e){ if (e.key === 'Escape') _close(); });
    var chipsEl = document.getElementById('sb2PdsStatusChips');
    if (chipsEl) chipsEl.addEventListener('click', function(e){ var btn = e.target.closest('.sb2-pds-chip'); if (!btn) return; sb2PdsFilterStatus(btn.dataset.status || ''); });
    var searchEl = document.getElementById('sb2PdsSearch');
    var searchTimer = null;
    if (searchEl) searchEl.addEventListener('input', function(){ if (searchTimer) clearTimeout(searchTimer); var v = searchEl.value; searchTimer = setTimeout(function(){ sb2PdsOnSearch(v); }, 200); });
    var brandEl = document.getElementById('sb2PdsBrand');
    if (brandEl) brandEl.addEventListener('change', function(){ sb2PdsOnBrandChange(brandEl.value); });
    var sortEl = document.getElementById('sb2PdsSort');
    if (sortEl) sortEl.addEventListener('change', function(){ sb2PdsOnSortChange(sortEl.value); });
  }
})();

window.sb2ProductsInit = sb2ProductsInit;
window.switchProductsCategory = switchProductsCategory;
window.sb2PdsGoPage = sb2PdsGoPage;
window.sb2PdsFilterStatus = sb2PdsFilterStatus;
window.sb2PdsOpenDetail = sb2PdsOpenDetail;
window.sb2PdsCloseDetail = sb2PdsCloseDetail;
/* ★ r39.3-P0: 找达人弹窗配套函数挂 window (r38 IIFE 踩坑: HTML onclick 属性必走 window 暴露)
   弹窗走 POST /api/ai-match (server 真字段: matches[].talent/product + score + ruleScore + aiScore + reasons + aiReason)
   apply when: 任何「IIFE 内函数给 HTML onclick 用」必须 window.* 主动暴露 (r38 IIFE 跨域铁律) */
window.sb2PdsOpenFindTalents = sb2PdsOpenFindTalents;
window.sb2PdsCloseFindTalents = sb2PdsCloseFindTalents;
window.sb2PdsSubmitFindTalentProposal = sb2PdsSubmitFindTalentProposal;
async function sb2PatternsInit(){
  var heroSub = document.getElementById('sb2PtnHeroSub');
  var listEl = document.getElementById('sb2PtnList');
  try {
    var resp = await apiFetch('/api/knowledge-patterns?limit=100');
    if (!resp || !resp.ok) {
      if (heroSub) heroSub.textContent = '载入失败 (HTTP ' + (resp ? resp.status : 'no response') + ')';
      if (listEl) listEl.innerHTML = '<div class="sb2-ptn-empty">载入失败, 请稍后重试</div>';
      return;
    }
    var data = await resp.json();
    _sb2PtnPatterns = data.patterns || [];
    /* 〔fix/sb2-side-restore commit 24〕17 轮追加 3 项 A: 规律库侧栏「加载中…」卡死
       修前 bug: SB2_SIDE_RENDERERS.patterns 读 window.patternCategories 永远空,
         fallback [{name:'加载中…'}] 永不更新 (时序坑, 同 commit 16 知识库批注③).
       修法 (派单对齐, 接口实测响应已确认 — 见 commit message):
       - /api/knowledge-patterns 响应 schema: {patterns: [...], total: N}
         实测: pattern[0].category = "鞋靴箱包" (字符串)
       - 不另发请求 / 不改 server 业务代码 (派单红线, DB_PATH 覆盖点已够用)
       - 聚合 _sb2PtnPatterns category 字段: {id, name, count}
         — name 从 category 字符串取 (跟 sb2PtnRender 聚合 verification_level 同款模式)
         — count 累加同 category 的 pattern 数
         — 按 count 降序, 排前面的类目
       - 写 window.patternCategories 给侧栏读
       - 调 renderSideFor('patterns') 重渲侧栏 (knowledge commit 16 同款模式) */
    var catMap = {};
    _sb2PtnPatterns.forEach(function(p){
      var c = p.category || '未分类';
      if (!catMap[c]) catMap[c] = { id: c, name: c, count: 0 };
      catMap[c].count++;
    });
    var catList = Object.keys(catMap).map(function(k){ return catMap[k]; });
    catList.sort(function(a, b){ return b.count - a.count; });
    window.patternCategories = catList;
    sb2PtnRender();
    if (typeof window.renderSideFor === 'function') window.renderSideFor('patterns');
    console.log('[sb2-patterns] init OK, count:', _sb2PtnPatterns.length, ', categories:', catList.length);
  } catch (e) {
    console.error('[sb2-patterns] init error:', e);
    if (heroSub) heroSub.textContent = '载入失败';
    if (listEl) listEl.innerHTML = '<div class="sb2-ptn-empty">载入失败, 请稍后重试</div>';
  }
}

/* 〔27 轮批注③ 2026-10-06〕hero 标题动态化 (老大「这个得改,又是跟知识库一样的问题」)
   修前: hero h1 静态「规律库」, 跟侧栏标题重复, 且不反映当前筛选
     (22 轮批注① 知识库 hero 同款问题, 修法同 sb2KbUpdateHeroTitle 方案A)
   修后: 类目选中 → 类目名; 状态选中 → 状态中文; 等级 chip 选中 → 等级名; 全不选 → 「全部规律」
   调用点: 收拢在 sb2PtnRender 顶部 (三条筛选路 + init 全经过它), 不造第二个入口 */
function sb2PtnUpdateHeroTitle(){
  var hero = document.getElementById('sb2PtnHeroTitle');
  if (!hero) return;
  if (_sb2PtnCat) { hero.textContent = _sb2PtnCat; return; }
  if (_sb2PtnStatus) {
    var _sm = { draft:'待确认', confirmed:'已确认', deprecated:'已废弃' };
    hero.textContent = _sm[_sb2PtnStatus] || _sb2PtnStatus;
    return;
  }
  if (_sb2PtnLevel) { hero.textContent = _sb2PtnLevel; return; }
  hero.textContent = '全部规律';
}
function sb2PtnFilter(level){
  _sb2PtnLevel = level || '';
  _sb2PtnPage = 1; // 〔r39-16 批注③〕切筛选回第 1 页
  document.querySelectorAll('.sb2-ptn-chip').forEach(function(c){
    c.classList.toggle('active', c.dataset.level === _sb2PtnLevel);
  });
  sb2PtnRender();
}

/* 〔fix/sb2-side-restore 24 轮批注④〕侧栏「类目」组点不动修复:
   修前 bug: 侧栏 renderer onClick 写「switchPatternsCategory && switchPatternsCategory(...)」
     但全仓无此函数定义 (旧版 selectPatternCategory 只驱动已下线的 legacy DOM) → 静默 no-op
     老大 24 轮批注④「点不动」(个护家清等 3 类目全死)
   修法: 新增 _sb2PtnCat 类目过滤状态 (跟 _sb2PtnLevel 同款), sb2PtnRender 组合过滤
     — sb2PatternsInit 的 catMap id===name===category 字符串, 侧栏传值即类目名, 无需反查
     — 再点同类目 = 取消筛选 (侧栏无「全部类目」项, 提供回到全部的入口)
     — 状态组 (switchPatternsTab) 是 legacy taxonomy (status) 跟 sb2 的 verification_level
       不是同一词典, 不在本次范围, 待老大拍板 */
function switchPatternsCategory(cat){
  _sb2PtnCat = (_sb2PtnCat === (cat || '')) ? '' : (cat || '');
  _sb2PtnPage = 1; // 〔r39-16 批注③〕切筛选回第 1 页
  document.querySelectorAll('.sb2-side--module-patterns .sb2-side-item').forEach(function(it){
    var _oc = it.getAttribute('onclick') || '';
    it.classList.toggle('active', _sb2PtnCat !== '' && _oc.indexOf("switchPatternsCategory('" + _sb2PtnCat + "')") >= 0);
  });
  sb2PtnRender();
}

/* 〔24 轮批注④ 二阶段 2026-10-06 12:54〕侧栏「状态」组映射修复 (老大授权「看你的建议来映射」):
   修前: 侧栏 onClick 调 switchPatternsTab — legacy 函数, 只驱动已下线旧版 patterns DOM, 静默 no-op
   映射依据 (prod API 实测 /api/knowledge-patterns?status=X 生效, pattern 自带 status 字段):
     待确认 → draft (83 条) / 已确认 → confirmed (16 条) / 已废弃 → deprecated (0 条)
   — verification_level (hypothesis/candidate/verified) 是晋升等级, 跟生命周期 status 不同维度, 不混用
   — 再点同状态 = 取消 (跟类目组同款交互); 全部 项显式传 ''
   — 纯前端过滤 (_sb2PtnPatterns 已全量在手), server 零改动 */
function switchPtnSideStatus(status){
  _sb2PtnStatus = (_sb2PtnStatus === (status || '')) ? '' : (status || '');
  _sb2PtnPage = 1; // 〔r39-16 批注③〕切筛选回第 1 页
  document.querySelectorAll('.sb2-side--module-patterns .sb2-side-item').forEach(function(it){
    var _oc = it.getAttribute('onclick') || '';
    it.classList.toggle('active', _oc.indexOf("switchPtnSideStatus('" + (_sb2PtnStatus || '') + "')") >= 0 && _sb2PtnStatus !== '');
  });
  sb2PtnRender();
}

function sb2PtnPromotionExplain(){
  // MVP1 占位: 解释晋升链 — MVP5 做完整可视化
  showToast('晋升链: hypothesis → candidate → verified\n- evidence ≥ 30 且置信度 ≥ 80 自动晋升\n- MVP5 完整可视化排期', 'info');
}

function sb2PtnRender(){
  sb2PtnUpdateHeroTitle();
  var heroSub = document.getElementById('sb2PtnHeroSub');
  var listEl = document.getElementById('sb2PtnList');
  if (!listEl) return;
  var total = _sb2PtnPatterns.length;
  var lvCount = { hypothesis: 0, candidate: 0, verified: 0 };
  _sb2PtnPatterns.forEach(function(p){
    var lv = p.verification_level || 'hypothesis';
    if (lvCount[lv] == null) lvCount[lv] = 0;
    lvCount[lv]++;
  });
  /* ★ r70 批注⑥ 老大 16:25「你看看好多英文」: 规律库 hero 副标 / chips 英文 → 中文
     — 原 verified/candidate/hypothesis 是后端英文枚举值, 跟后端契约一致
     — 但用户面应显示中文 (跟 r39-5 UI 文案禁止开发黑话铁律一致, 跟 legacy renderPatternsList line 5116 levelLabel 同款)
     — 映射: hypothesis→假设, candidate→候选, verified→已验证 (跟 inline-06.js line 5116 共享语义) */
  if (heroSub) heroSub.textContent = total + ' 条规律 · 📐 已验证 ' + lvCount.verified + ' · 🔬 候选 ' + lvCount.candidate + ' · 🧪 假设 ' + lvCount.hypothesis;
  // chips 渲染 (一次性, 后续只更新计数)
  var chipsEl = document.getElementById('sb2PtnChips');
  var chipsData = [
    { level: '', label: '全部', icon: '', count: total },
    { level: 'verified', label: '已验证', icon: '📐', count: lvCount.verified },
    { level: 'candidate', label: '候选', icon: '🔬', count: lvCount.candidate },
    { level: 'hypothesis', label: '假设', icon: '🧪', count: lvCount.hypothesis }
  ];
  if (chipsEl) {
    if (!chipsEl.dataset.rendered) {
      chipsEl.dataset.rendered = '1';
      var chipHtml = '';
      chipsData.forEach(function(c){
        var lvlAttr = c.level || '';
        chipHtml += '<button class="sb2-ptn-chip' + (_sb2PtnLevel === c.level ? ' active' : '') + '" data-level="' + lvlAttr + '" onclick="sb2PtnFilter(\'' + lvlAttr + '\')">';
        if (c.icon) chipHtml += '<span class="sb2-ptn-chip-icon">' + c.icon + '</span>';
        chipHtml += c.label + ' ' + c.count + '</button>';
      });
      chipsEl.innerHTML = chipHtml;
    } else {
      // 更新现有 chip 计数 (保留 active class)
      chipsData.forEach(function(c){
        var btn = chipsEl.querySelector('[data-level="' + (c.level || '') + '"]');
        if (!btn) return;
        var iconSpan = btn.querySelector('.sb2-ptn-chip-icon');
        btn.textContent = '';
        if (iconSpan) btn.appendChild(iconSpan);
        btn.appendChild(document.createTextNode(c.label + ' ' + c.count));
      });
    }
  }
  // 过滤 (〔24 轮批注④〕类目 × 等级 × 状态 三维度组合, 任一命中即筛)
  var filtered = _sb2PtnPatterns.filter(function(p){
    if (_sb2PtnLevel && (p.verification_level || 'hypothesis') !== _sb2PtnLevel) return false;
    if (_sb2PtnCat && (p.category || '未分类') !== _sb2PtnCat) return false;
    if (_sb2PtnStatus && (p.status || 'draft') !== _sb2PtnStatus) return false;
    return true;
  });
  if (filtered.length === 0) {
    listEl.innerHTML = '<div class="sb2-ptn-empty">' + ((_sb2PtnLevel || _sb2PtnCat || _sb2PtnStatus) ? '当前筛选下暂无规律' : '暂无规律记录') + '</div>';
    _sb2PtnRenderPagerUI(0, 1);
    return;
  }
  /* 〔r39-16 批注③〕前端切片分页: 筛选结果按 _sb2PtnLimit 分页, 只渲染当前页 */
  var totalPages = Math.max(1, Math.ceil(filtered.length / _sb2PtnLimit));
  if (_sb2PtnPage > totalPages) _sb2PtnPage = totalPages;
  _sb2PtnFilteredTotal = filtered.length;
  _sb2PtnRenderPagerUI(filtered.length, totalPages);
  var pageItems = filtered.slice((_sb2PtnPage - 1) * _sb2PtnLimit, _sb2PtnPage * _sb2PtnLimit);
  // render cards
  var html = '';
  pageItems.forEach(function(p){
    var lv = p.verification_level || 'hypothesis';
    var confScore = p.confidence_score != null ? p.confidence_score : (p.confidence != null ? Math.round(p.confidence * 100) : 0);
    var confCls = confScore >= 80 ? '' : (confScore >= 50 ? 'mid' : 'low');
    var hits = p.hit_count || 0;
    var evCount = p.evidence_count || 0;
    var time = p.created_at ? formatRelativeTime(p.created_at * 1000) : '-';
    var lvIcon = lv === 'verified' ? '📐' : (lv === 'candidate' ? '🔬' : '🧪');
    /* ★ r70 批注⑥ 老大 16:25「你看看好多英文」: card level label 英文 → 中文 */
    var lvLabel = lv === 'verified' ? '已验证' : (lv === 'candidate' ? '候选' : '假设');
    html += '<div class="sb2-ptn-card" data-pid="' + escapeAttr(p.id) + '" onclick="sb2PtnOpenDetail(\'' + escapeAttr(p.id).replace(/'/g, "\\'") + '\')">';
    html += '<div class="sb2-ptn-card-top">';
    html += '<span class="sb2-ptn-card-cat">' + escapeHtml(p.category || '未分类') + '</span>';
    html += '<span class="sb2-ptn-level ' + escapeAttr(lv) + '">' + lvIcon + ' ' + lvLabel + '</span>';
    html += '</div>';
    html += '<div class="sb2-ptn-card-text">' + escapeHtml(p.pattern_text || '') + '</div>';
    html += '<div class="sb2-ptn-card-meta">';
    html += '<span class="sb2-ptn-conf">置信度 <span class="sb2-ptn-conf-bar"><span class="sb2-ptn-conf-fill ' + confCls + '" style="width:' + confScore + '%"></span></span>' + confScore + '%</span>';
    html += '<span>命中 ' + hits + '</span>';
    html += '<span>证据 ' + evCount + '</span>';
    html += '<span class="sb2-ptn-card-time">' + escapeHtml(time) + '</span>';
    html += '</div>';
    html += '</div>';
  });
  listEl.innerHTML = html;
}

/* 〔r39-16 批注③〕规律库底栏分页: 翻页 + 页码渲染
   页码序列省略逻辑跟 sb2TalentsRenderPager 同款 (≤7 全显, 否则 1 … 窗口 … N)
   sb2PtnGoPage 被 pager 按钮 HTML onclick 调用 — 本 script 块顶层作用域, 直接可达 */
function sb2PtnGoPage(p){
  var totalPages = Math.max(1, Math.ceil((_sb2PtnFilteredTotal || 0) / _sb2PtnLimit));
  if (p < 1 || p > totalPages) return;
  _sb2PtnPage = p;
  sb2PtnRender();
}

function _sb2PtnRenderPagerUI(total, totalPages){
  var left = document.getElementById('sb2PtnFooterLeft');
  var pager = document.getElementById('sb2PtnPager');
  if (left) left.textContent = '共 ' + total + ' 条';
  if (!pager) return;
  var page = _sb2PtnPage;
  if (totalPages <= 1){ pager.innerHTML = ''; return; }
  var html = '';
  html += '<button ' + (page <= 1 ? 'disabled' : '') + ' onclick="sb2PtnGoPage(' + (page - 1) + ')">‹</button>';
  var pages = [];
  if (totalPages <= 7){
    for (var p = 1; p <= totalPages; p++) pages.push(p);
  } else {
    pages.push(1);
    if (page > 4) pages.push('…');
    var start = Math.max(2, page - 1);
    var end = Math.min(totalPages - 1, page + 1);
    for (var p2 = start; p2 <= end; p2++) pages.push(p2);
    if (page < totalPages - 3) pages.push('…');
    pages.push(totalPages);
  }
  pages.forEach(function(x){
    if (x === '…') html += '<span class="ellipsis">…</span>';
    else html += '<button class="' + (x === page ? 'active' : '') + '" onclick="sb2PtnGoPage(' + x + ')">' + x + '</button>';
  });
  html += '<button ' + (page >= totalPages ? 'disabled' : '') + ' onclick="sb2PtnGoPage(' + (page + 1) + ')">›</button>';
  pager.innerHTML = html;
}

function sb2PtnOpenDetail(pid){
  // MVP2: 接 GET /api/knowledge-patterns/<id> + 详情右滑
  var overlay = document.getElementById('sb2PtnDetailOverlay');
  var panel = document.getElementById('sb2PtnDetailPanel');
  var loading = document.getElementById('sb2PtnDetailLoading');
  var metaEl = document.getElementById('sb2PtnDetailMeta');
  var patternWrap = document.getElementById('sb2PtnDetailPatternWrap');
  var patternText = document.getElementById('sb2PtnDetailPatternText');
  var evWrap = document.getElementById('sb2PtnDetailEvidenceWrap');
  var evLabel = document.getElementById('sb2PtnDetailEvidenceLabel');
  var evBody = document.getElementById('sb2PtnDetailEvidenceBody');
  var titleEl = document.getElementById('sb2PtnDetailTitle');
  // 重置 UI
  if (loading) loading.style.display = 'block';
  if (metaEl) metaEl.style.display = 'none';
  if (patternWrap) patternWrap.style.display = 'none';
  if (evWrap) evWrap.style.display = 'none';
  if (titleEl) titleEl.textContent = '-';
  if (overlay) overlay.classList.add('open');
  if (panel) panel.classList.add('open');
  apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid))
    .then(function(r){
      if (!r || !r.ok) {
        if (loading) loading.textContent = '载入失败 (HTTP ' + (r ? r.status : 'no response') + ')';
        return;
      }
      return r.json();
    })
    .then(function(data){
      if (!data || !data.id) return;
      _sb2PtnDetailRender(data);
    })
    .catch(function(e){
      console.error('[sb2-patterns] detail error:', e);
      if (loading) loading.textContent = '载入失败, 请稍后重试';
    });
}

function sb2PtnCloseDetail(){
  var overlay = document.getElementById('sb2PtnDetailOverlay');
  var panel = document.getElementById('sb2PtnDetailPanel');
  if (overlay) overlay.classList.remove('open');
  if (panel) panel.classList.remove('open');
}

function _sb2PtnDetailRender(p){
  // 防御: 字段缺失时显 '-'
  function val(v, fallback){ return (v == null || v === '') ? '-' : v; }
  var loading = document.getElementById('sb2PtnDetailLoading');
  var metaEl = document.getElementById('sb2PtnDetailMeta');
  var patternWrap = document.getElementById('sb2PtnDetailPatternWrap');
  var patternText = document.getElementById('sb2PtnDetailPatternText');
  var evWrap = document.getElementById('sb2PtnDetailEvidenceWrap');
  var evLabel = document.getElementById('sb2PtnDetailEvidenceLabel');
  var evBody = document.getElementById('sb2PtnDetailEvidenceBody');
  var titleEl = document.getElementById('sb2PtnDetailTitle');
  // title: pattern_text 截断 30 字
  var ptext = p.pattern_text || '';
  titleEl.textContent = ptext.length > 30 ? ptext.slice(0, 30) + '…' : (ptext || '-');
  // 隐藏 loading, 显示 meta + pattern
  if (loading) loading.style.display = 'none';
  if (metaEl) metaEl.style.display = '';
  if (patternWrap) patternWrap.style.display = '';
  if (patternText) patternText.textContent = val(ptext, '-');
  // meta grid (8 项, 2 列布局)
  var lv = p.verification_level || 'hypothesis';
  var lvIcon = lv === 'verified' ? '📐' : (lv === 'candidate' ? '🔬' : '🧪');
  /* ★ r70 批注⑤ 老大 16:25「你自己看看, 好多英文」: 规律详情浮层 level label 英文 → 中文
     (跟 hero / chips / card 同款映射, 跨 4 处一致) */
  var lvLabel = lv === 'verified' ? '已验证' : (lv === 'candidate' ? '候选' : '假设');
  var confScore = p.confidence_score != null ? p.confidence_score : (p.confidence != null ? Math.round(p.confidence * 100) : 0);
  var statusLabel = { draft: '待确认', confirmed: '已确认', rejected: '已拒绝', deprecated: '已废弃' };
  var timeCreated = p.created_at ? formatRelativeTime(p.created_at * 1000) : '-';
  var timeUpdated = p.updated_at ? formatRelativeTime(p.updated_at * 1000) : '-';
  var timeLastUsed = p.last_used_at ? formatRelativeTime(p.last_used_at) : '-';
  var kbCount = (p.source_knowledge_ids || []).length;
  var evCount = (p.evidence_count || 0);
  var metaHtml = '';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">类目</div><div class="sb2-ptn-meta-value">' + escapeHtml(val(p.category, '-')) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">分级</div><div class="sb2-ptn-meta-value">' + lvIcon + ' ' + lvLabel + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">状态</div><div class="sb2-ptn-meta-value">' + escapeHtml(val(statusLabel[p.status] || p.status, '-')) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">实体类型</div><div class="sb2-ptn-meta-value">' + escapeHtml(val(p.entity_type, '-')) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">置信度</div><div class="sb2-ptn-meta-value">' + confScore + '%<div class="sb2-ptn-meta-bar"><div class="sb2-ptn-meta-bar-fill" style="width:' + confScore + '%"></div></div></div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">证据数</div><div class="sb2-ptn-meta-value">' + evCount + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">命中 / 误命</div><div class="sb2-ptn-meta-value">' + (p.hit_count || 0) + ' / ' + (p.miss_count || 0) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">关联知识</div><div class="sb2-ptn-meta-value">' + kbCount + ' 条</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">最近使用</div><div class="sb2-ptn-meta-value">' + escapeHtml(timeLastUsed) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">创建</div><div class="sb2-ptn-meta-value">' + escapeHtml(timeCreated) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">更新</div><div class="sb2-ptn-meta-value">' + escapeHtml(timeUpdated) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">创建者</div><div class="sb2-ptn-meta-value">' + escapeHtml(val(p.created_by, '-')) + '</div></div>';
  metaHtml += '<div class="sb2-ptn-meta-item"><div class="sb2-ptn-meta-label">ID</div><div class="sb2-ptn-meta-value muted" title="' + escapeAttr(p.id) + '">' + escapeHtml(p.id) + '</div></div>';
  metaEl.innerHTML = metaHtml;
  // evidence: 先探 JSON.parse, 成功 array of strings, 失败按原文展示 (老大 2026-10-03 00:22 提醒: 别猜, 失败按原文)
  var evRaw = p.evidence;
  if (evRaw == null || evRaw === '') {
    evWrap.style.display = 'none';
  } else {
    evWrap.style.display = '';
    var parsed = null;
    if (typeof evRaw === 'string') {
      try { parsed = JSON.parse(evRaw); } catch (e) { parsed = null; }
    } else if (Array.isArray(evRaw)) {
      parsed = evRaw;
    }
    if (Array.isArray(parsed) && parsed.length > 0) {
      // array 渲染
      evLabel.textContent = '证据 (' + parsed.length + ' 条)';
      /* ★ r70 批注⑤ 老大 16:25「你自己看看, 好多英文, 证据那是什么」: 证据显示原 ID (ke_xxx) 用户看不懂
         跟 legacy renderPatternsList line 5167-5183 同款: 调 /api/knowledge-events/{eid} 取事件 title
         — 改前: 显示 evStr (ke_xxx ID)
         — 改后: 显示事件 title, 拿不到 title fallback ID + tooltip (只丢不造, 第 12 条铁律)
         — Promise.all 并发拉, 失败 → null 过滤, 跟 precedent 同款 */
      evBody.innerHTML = '<ol class="sb2-ptn-evidence-list">' +
        parsed.map(function(_, idx){ return '<li class="sb2-ptn-evidence-item sb2-ptn-evidence-item-loading"><span class="sb2-ptn-evidence-item-num">' + (idx + 1) + '.</span><span class="sb2-ptn-evidence-item-text">载入中…</span></li>'; }).join('') +
        '</ol>';
      Promise.all(parsed.map(function(ev){
        var evId = (typeof ev === 'string') ? ev : (ev != null ? String(ev) : null);
        if (!evId) return Promise.resolve(null);
        return apiFetch('/api/knowledge-events/' + encodeURIComponent(evId))
          .then(function(r){ return (r && r.ok) ? r.json() : null; })
          .catch(function(){ return null; })
          .then(function(data){ return { id: evId, title: (data && data.title) || null, time: (data && data.created_at) || null }; });
      })).then(function(rows){
        var valid = rows.filter(Boolean);
        var html = '<ol class="sb2-ptn-evidence-list">';
        rows.forEach(function(r, idx){
          if (!r) {
            html += '<li class="sb2-ptn-evidence-item"><span class="sb2-ptn-evidence-item-num">' + (idx + 1) + '.</span><span class="sb2-ptn-evidence-item-text muted" title="事件数据未取到">' + escapeHtml(parsed[idx] || '-') + '</span></li>';
          } else {
            var titleStr = r.title || ('（无标题 - ' + r.id + '）');
            html += '<li class="sb2-ptn-evidence-item"><span class="sb2-ptn-evidence-item-num">' + (idx + 1) + '.</span><span class="sb2-ptn-evidence-item-text" title="' + escapeAttr(r.id) + '">' + escapeHtml(titleStr) + '</span></li>';
          }
        });
        html += '</ol>';
        evBody.innerHTML = html;
      });
    } else {
      // 解析失败 / 非数组 / 空数组 → 按原文展示
      evLabel.textContent = '证据 (原文)';
      var fallback = (typeof evRaw === 'string') ? evRaw : (Array.isArray(evRaw) && evRaw.length === 0 ? '(空数组)' : JSON.stringify(evRaw));
      evBody.innerHTML = '<div class="sb2-ptn-text-body">' + escapeHtml(fallback) + '</div>';
    }
  }
  // MVP4: 渲染 header 双标签 (分级 verification_level + 状态 lifecycle) — 老大提醒两套维度
  _sb2PtnRenderDetailTags(p);
  // MVP3: 渲染 footer 按钮组 (按 p.status 决定)
  _sb2PtnRenderDetailFooter(p);
  // MVP5: 拉晋升进度 + 关联合作记录 (异步, 渲染后异步填充)
  sb2PtnLoadProgress(p.id);
  sb2PtnLoadDeals();
}

/* ============================================================
 * sb2-patterns MVP3: CRUD (POST /induce + PUT status + DELETE)
 * - 创建入口: POST /api/knowledge-patterns/induce (LLM 归纳, body: category + entity_type)
 *   后端没有直接 POST /api/knowledge-patterns 创建单条 — 规律只能通过归纳产生 draft
 * - 状态流转: PUT /api/knowledge-patterns/<id> body: {status}
 *   _KP_STATUS_FLOW: draft→(confirmed, rejected), confirmed/rejected→deprecated
 * - 删除: DELETE /api/knowledge-patterns/<id> (硬删除, 二次确认)
 * ============================================================ */
var _sb2PtnDeletePendingId = '';

function _sb2PtnRenderDetailFooter(p){
  var footer = document.getElementById('sb2PtnDetailFooter');
  if (!footer) return;
  var pid = escapeAttr(p.id);
  var status = p.status || 'draft';
  var statusLabel = { draft: '待确认', confirmed: '已确认', rejected: '已拒绝', deprecated: '已废弃' };
  var hits = p.hit_count || 0;
  var misses = p.miss_count || 0;
  var html = '';
  // 1. 删除按钮 (左侧)
  html += '<button class="sb2-btn sb2-btn-ghost sb2-btn-danger" onclick="sb2PtnDelete(\'' + pid.replace(/'/g, "\\'") + '\')">🗑 删除</button>';
  // 2. MVP4: 投票按钮 (仅 confirmed 状态, 旧逻辑一致)
  if (status === 'confirmed') {
    html += '<button class="sb2-ptn-fb-btn up" id="sb2PtnFbUpBtn" onclick="sb2PtnFeedback(\'' + pid.replace(/'/g, "\\'") + '\', \'up\')">👍 <span class="sb2-ptn-fb-count" id="sb2PtnFbUpCount">' + hits + '</span></button>';
    html += '<button class="sb2-ptn-fb-btn down" id="sb2PtnFbDownBtn" onclick="sb2PtnFeedback(\'' + pid.replace(/'/g, "\\'") + '\', \'down\')">👎 <span class="sb2-ptn-fb-count" id="sb2PtnFbDownCount">' + misses + '</span></button>';
  }
  // 3. 状态流转按钮 (按当前 status 决定)
  if (status === 'draft') {
    html += '<button class="sb2-btn sb2-btn-ghost" style="color:var(--sb2-success); border-color:var(--sb2-success);" onclick="sb2PtnSetStatus(\'' + pid.replace(/'/g, "\\'") + '\', \'confirmed\')">✓ 确认</button>';
    html += '<button class="sb2-btn sb2-btn-ghost" style="color:var(--sb2-warning); border-color:var(--sb2-warning);" onclick="sb2PtnSetStatus(\'' + pid.replace(/'/g, "\\'") + '\', \'rejected\')">✗ 拒绝</button>';
  } else if (status === 'confirmed' || status === 'rejected') {
    html += '<button class="sb2-btn sb2-btn-ghost" onclick="sb2PtnSetStatus(\'' + pid.replace(/'/g, "\\'") + '\', \'deprecated\')">⊘ 废弃 (' + statusLabel[status] + ' → 废弃)</button>';
  } else if (status === 'deprecated') {
    html += '<span class="sb2-btn sb2-btn-ghost sb2-btn-muted" style="cursor:default;">已废弃</span>';
  }
  // 4. 关闭按钮 (右侧)
  html += '<button class="sb2-btn sb2-btn-ghost" onclick="sb2PtnCloseDetail()">关闭</button>';
  footer.innerHTML = html;
}

/* MVP4: 详情 header 双大标签 — 分级 (verification_level) + 状态 (lifecycle)
 * 老大 2026-10-03 01:08 提醒: 两套维度, 别让老大看混
 * 分级: verified/candidate/hypothesis (晋升维度)
 * 状态: draft/confirmed/rejected/deprecated (生命周期维度)
 */
function _sb2PtnRenderDetailTags(p){
  var tagsEl = document.getElementById('sb2PtnDetailTags');
  if (!tagsEl) return;
  var lv = p.verification_level || 'hypothesis';
  var lvIcon = lv === 'verified' ? '📐' : (lv === 'candidate' ? '🔬' : '🧪');
  var lvLabel = lv === 'verified' ? 'verified' : (lv === 'candidate' ? 'candidate' : 'hypothesis');
  var status = p.status || 'draft';
  var statusLabel = { draft: '待确认', confirmed: '已确认', rejected: '已拒绝', deprecated: '已废弃' };
  var html = '';
  // 分级 (verification_level) — 晋升维度
  html += '<span class="sb2-ptn-tag sb2-ptn-tag-' + escapeAttr(lv) + '" title="分级 (verification_level)">' + lvIcon + ' ' + lvLabel + '</span>';
  // 状态 (lifecycle) — 生命周期维度
  html += '<span class="sb2-ptn-tag sb2-ptn-tag-status-' + escapeAttr(status) + '" title="状态 (lifecycle)">' + escapeHtml(statusLabel[status] || status) + '</span>';
  tagsEl.innerHTML = html;
}

/* ============================================================
 * sb2-patterns MVP5: 晋升进度 + 关联合作记录
 * - 晋升进度: GET /api/knowledge-patterns/<id>/progress
 *   后端 _kp_promotion_progress 函数 (server:25541) 已写好, MVP5 commit bb585d9 暴露路由
 *   响应字段: {current_level, next_level, hit_required, hits_remaining,
 *              confidence_required, confidence_shortfall, status}
 *   阈值完全由后端 _KP_AUTO_PROMOTE_TARGETS 控制, 前端不复制常量
 * - deal: GET /api/deals (server:15369, 子账号隔离) — 列表无 talent_id 过滤 (MVP5 简化)
 * ============================================================ */
async function sb2PtnLoadProgress(pid){
  var wrap = document.getElementById('sb2PtnProgressWrap');
  var body = document.getElementById('sb2PtnProgressBody');
  if (!wrap || !body) return;
  wrap.style.display = '';
  body.textContent = '载入中…';
  var resp;
  try {
    resp = await apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid) + '/progress');
  } catch (e) {
    body.textContent = '晋升进度载入失败: ' + (e.message || '');
    return;
  }
  if (!resp || !resp.ok) {
    body.textContent = '晋升进度不可用 (HTTP ' + (resp ? resp.status : 'no response') + ')';
    return;
  }
  var prog;
  try { prog = await resp.json(); } catch (_) { prog = null; }
  if (!prog) {
    body.textContent = '晋升进度数据格式异常';
    return;
  }
  _sb2PtnRenderProgress(prog);
}

function _sb2PtnRenderProgress(prog){
  var wrap = document.getElementById('sb2PtnProgressWrap');
  var body = document.getElementById('sb2PtnProgressBody');
  if (!wrap || !body) return;
  var lvIcon = function(lv){
    return lv === 'verified' ? '📐' : (lv === 'candidate' ? '🔬' : (lv === 'proven' ? '🏆' : '🧪'));
  };
  // 终态 (deprecated / proven 无下一级)
  if (!prog.next_level) {
    var endLabel = prog.current_level === 'deprecated' ? '已废弃' : (prog.current_level === 'proven' ? '🏆 已成熟 (顶级)' : '当前 ' + prog.current_level);
    body.outerHTML = '<div class="sb2-ptn-progress-deprecated">' + escapeHtml(endLabel) + '</div>';
    return;
  }
  var curLv = prog.current_level || 'hypothesis';
  var nextLv = prog.next_level;
  var hitReq = prog.hit_required || 0;
  var hitRem = prog.hits_remaining != null ? prog.hits_remaining : hitReq;
  var csReq = prog.confidence_required || 0;
  var csShort = prog.confidence_shortfall != null ? prog.confidence_shortfall : csReq;
  // 当前进度 = 已达成 / 要求
  var hitPct = hitReq > 0 ? Math.min(100, Math.round((1 - hitRem / hitReq) * 100)) : 0;
  var csPct = csReq > 0 ? Math.min(100, Math.round((1 - csShort / csReq) * 100)) : 0;
  var hitCls = hitRem > 0 ? 'warning' : '';
  var csCls = csShort > 0 ? 'warning' : '';
  var html = '';
  html += '<div class="sb2-ptn-progress-current">';
  html += '<span class="sb2-ptn-level ' + escapeAttr(curLv) + '">' + lvIcon(curLv) + ' ' + escapeHtml(curLv) + '</span>';
  html += '<span class="sb2-ptn-progress-arrow">→</span>';
  html += '<span class="sb2-ptn-level ' + escapeAttr(nextLv) + '">' + lvIcon(nextLv) + ' ' + escapeHtml(nextLv) + '</span>';
  html += '<span class="sb2-ptn-progress-next">(当前 status: ' + escapeHtml(prog.status || '-') + ')</span>';
  html += '</div>';
  // 命中进度
  html += '<div class="sb2-ptn-progress-row">';
  html += '<span class="sb2-ptn-progress-row-label">命中次数</span>';
  html += '<span class="sb2-ptn-progress-row-bar"><span class="sb2-ptn-progress-row-fill ' + hitCls + '" style="width:' + hitPct + '%"></span></span>';
  html += '<span class="sb2-ptn-progress-row-value">' + hitReq + ' (还差 ' + hitRem + ')</span>';
  html += '</div>';
  // 置信度进度
  html += '<div class="sb2-ptn-progress-row">';
  html += '<span class="sb2-ptn-progress-row-label">置信度</span>';
  html += '<span class="sb2-ptn-progress-row-bar"><span class="sb2-ptn-progress-row-fill ' + csCls + '" style="width:' + csPct + '%"></span></span>';
  html += '<span class="sb2-ptn-progress-row-value">≥ ' + csReq + '% (差 ' + csShort + ')</span>';
  html += '</div>';
  body.innerHTML = html;
}

async function sb2PtnLoadDeals(){
  var wrap = document.getElementById('sb2PtnDealsWrap');
  var body = document.getElementById('sb2PtnDealsBody');
  var label = document.getElementById('sb2PtnDealsLabel');
  if (!wrap || !body) return;
  wrap.style.display = '';
  body.innerHTML = '<div class="sb2-ptn-deal-empty">载入中…</div>';
  var resp;
  try {
    resp = await apiFetch('/api/deals?limit=10');
  } catch (e) {
    body.innerHTML = '<div class="sb2-ptn-deal-empty">载入失败: ' + escapeHtml(e.message || '') + '</div>';
    return;
  }
  if (!resp || !resp.ok) {
    body.innerHTML = '<div class="sb2-ptn-deal-empty">不可用 (HTTP ' + (resp ? resp.status : 'no response') + ')</div>';
    return;
  }
  var data;
  try { data = await resp.json(); } catch (_) { data = null; }
  if (!data) {
    body.innerHTML = '<div class="sb2-ptn-deal-empty">响应格式异常</div>';
    return;
  }
  _sb2PtnRenderDeals(data);
}

function _sb2PtnRenderDeals(data){
  var body = document.getElementById('sb2PtnDealsBody');
  var label = document.getElementById('sb2PtnDealsLabel');
  if (!body) return;
  var deals = data.deals || data.items || (Array.isArray(data) ? data : []);
  if (label) label.textContent = '关联合作记录 (最近 ' + deals.length + ' 条)';
  if (deals.length === 0) {
    body.innerHTML = '<div class="sb2-ptn-deal-empty">暂无合作记录</div>';
    return;
  }
  var statusLabel = {
    pending: '待联系', negotiating: '洽谈中', sample_sent: '已寄样',
    approved: '已通过', live: '直播中', completed: '已完成', failed: '失败'
  };
  var html = '';
  deals.forEach(function(d){
    var st = d.status || 'pending';
    var stLabel = statusLabel[st] || st;
    var productName = d.product_name || '(未指定商品)';
    var commission = d.commission_rate != null ? (Math.round(d.commission_rate * 100) / 100) + '%' : '-';
    var gmv = d.actual_gmv || 0;
    var time = d.scheduled_at ? formatRelativeTime(d.scheduled_at * 1000) : (d.actual_units ? '已交付 ' + d.actual_units + ' 件' : '-');
    html += '<div class="sb2-ptn-deal-item">';
    html += '<div class="sb2-ptn-deal-item-top">';
    html += '<span class="sb2-ptn-deal-status ' + escapeAttr(st) + '">' + escapeHtml(stLabel) + '</span>';
    html += '<span>' + escapeHtml(productName) + '</span>';
    html += '<span style="color:var(--sb2-t3);font:400 11px/1.3 monospace;">' + escapeHtml(commission) + '</span>';
    html += '</div>';
    html += '<div class="sb2-ptn-deal-meta">';
    html += '<span>ID: ' + escapeHtml(d.id || '-') + '</span>';
    if (gmv > 0) html += '<span>GMV ¥' + gmv + '</span>';
    html += '<span>' + escapeHtml(time) + '</span>';
    html += '</div>';
    html += '</div>';
  });
  body.innerHTML = html;
}

async function sb2PtnFeedback(pid, fb){
  if (fb !== 'up' && fb !== 'down') return;
  var upBtn = document.getElementById('sb2PtnFbUpBtn');
  var downBtn = document.getElementById('sb2PtnFbDownBtn');
  // 双点击保护
  if (upBtn) upBtn.disabled = true;
  if (downBtn) downBtn.disabled = true;
  try {
    var resp = await apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid) + '/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback: fb })
    });
    if (!resp || !resp.ok) {
      var errMsg = 'HTTP ' + (resp ? resp.status : 'no response');
      try { var ej = await resp.json(); if (ej && ej.error) errMsg = ej.error; } catch (_) {}
      showToast('❌ 反馈失败: ' + errMsg, 'error');
      if (upBtn) upBtn.disabled = false;
      if (downBtn) downBtn.disabled = false;
      return;
    }
    var data = await resp.json();
    // 更新 hit/miss 计数显示 (旧 feedbackPattern 行为, 但用 sb2PtnFbUpCount / sb2PtnFbDownCount)
    var upCountEl = document.getElementById('sb2PtnFbUpCount');
    var downCountEl = document.getElementById('sb2PtnFbDownCount');
    if (upCountEl && data.new_count != null && fb === 'up') upCountEl.textContent = String(data.new_count);
    if (downCountEl && data.new_count != null && fb === 'down') downCountEl.textContent = String(data.new_count);
    // 反馈提示
    var fbMsg = data.message || (fb === 'up' ? '👍 反馈已记录' : '👎 反馈已记录');
    // MVP4: 自动晋升检查 (后端 _kp_auto_promote 触发)
    if (data.promotion && data.promotion.old && data.promotion.new) {
      fbMsg += '\n🎉 自动晋升: ' + data.promotion.old + ' → ' + data.promotion.new;
      showToast(fbMsg, 'success', 5000);
      // 重新打开详情面板 (刷新 tags)
      sb2PtnCloseDetail();
      sb2PtnOpenDetail(pid);
    } else {
      showToast(fbMsg, 'success');
    }
    // 列表命中数也要刷新
    sb2PatternsInit();
    console.log('[sb2-patterns] feedback OK:', data);
  } catch (e) {
    console.error('[sb2-patterns] feedback error:', e);
    showToast('❌ 反馈失败: ' + (e.message || ''), 'error');
    if (upBtn) upBtn.disabled = false;
    if (downBtn) downBtn.disabled = false;
  }
}

async function sb2PtnSetStatus(pid, newStatus){
  var statusLabel = { draft: '待确认', confirmed: '已确认', rejected: '已拒绝', deprecated: '已废弃' };
  if (!confirm('确认将状态流转为「' + (statusLabel[newStatus] || newStatus) + '」?')) return;
  try {
    var resp = await apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid), {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus })
    });
    if (!resp || !resp.ok) {
      var errMsg = 'HTTP ' + (resp ? resp.status : 'no response');
      try { var ej = await resp.json(); if (ej && ej.error) errMsg = ej.error; } catch (_) {}
      showToast('❌ 状态流转失败: ' + errMsg, 'error');
      return;
    }
    var data = await resp.json();
    showToast('✓ 已流转为「' + (statusLabel[newStatus] || newStatus) + '」', 'success');
    console.log('[sb2-patterns] setStatus OK:', data);
    // 重新打开详情面板 (刷新数据) + 重新渲染列表
    sb2PtnCloseDetail();
    sb2PatternsInit();
  } catch (e) {
    console.error('[sb2-patterns] setStatus error:', e);
    showToast('❌ 状态流转失败: ' + (e.message || ''), 'error');
  }
}

function sb2PtnDelete(pid){
  _sb2PtnDeletePendingId = pid;
  // 找到对应规律的 pattern_text 前 60 字作为摘要
  var p = _sb2PtnPatterns.find(function(x){ return x.id === pid; });
  var summary = p ? (p.pattern_text || '').slice(0, 60) : pid;
  var body = document.getElementById('sb2PtnDeleteConfirmBody');
  if (body) body.textContent = '规律: ' + summary + (p ? ('\n类目: ' + (p.category || '-') + '\n分级: ' + (p.verification_level || '-')) : '');
  var modal = document.getElementById('sb2PtnDeleteConfirmModal');
  if (modal) modal.classList.add('open');
}

function sb2PtnCloseDeleteConfirm(){
  _sb2PtnDeletePendingId = '';
  var modal = document.getElementById('sb2PtnDeleteConfirmModal');
  if (modal) modal.classList.remove('open');
}

async function sb2PtnConfirmDelete(){
  var pid = _sb2PtnDeletePendingId;
  if (!pid) return;
  var btn = document.getElementById('sb2PtnDeleteConfirmBtn');
  if (btn) btn.disabled = true;
  try {
    var resp = await apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid), { method: 'DELETE' });
    if (!resp || !resp.ok) {
      var errMsg = 'HTTP ' + (resp ? resp.status : 'no response');
      try { var ej = await resp.json(); if (ej && ej.error) errMsg = ej.error; } catch (_) {}
      showToast('❌ 删除失败: ' + errMsg, 'error');
      if (btn) btn.disabled = false;
      return;
    }
    var data = await resp.json();
    if (data && data.deleted) {
      showToast('✓ 已删除 ' + pid, 'success');
      sb2PtnCloseDeleteConfirm();
      sb2PtnCloseDetail();
      sb2PatternsInit();
    } else {
      showToast('⚠ 规律不存在或已删除', 'warning');
      sb2PtnCloseDeleteConfirm();
      sb2PatternsInit();
    }
  } catch (e) {
    console.error('[sb2-patterns] delete error:', e);
    showToast('❌ 删除失败: ' + (e.message || ''), 'error');
    if (btn) btn.disabled = false;
  }
}

async function sb2PtnOpenInduceModal(){
  var modal = document.getElementById('sb2PtnInduceModal');
  if (modal) modal.classList.add('open');
  // 重置
  var resultEl = document.getElementById('sb2PtnInduceResult');
  if (resultEl) {
    resultEl.textContent = '选择实体类型与类目后, 点击「触发归纳」开始 LLM 归纳。';
    resultEl.className = 'sb2-ptn-modal-result muted';
  }
  // MVP4: 重置 induced patterns 列表 (避免上次结果残留)
  var induceListEl = document.getElementById('sb2PtnInduceList');
  if (induceListEl) {
    induceListEl.innerHTML = '';
    induceListEl.style.display = 'none';
  }
  // 加载类目下拉
  var catSelect = document.getElementById('sb2PtnInduceCategory');
  if (catSelect && !catSelect.dataset.loaded) {
    catSelect.innerHTML = '<option value="">载入中…</option>';
    try {
      var resp = await apiFetch('/api/knowledge/categories');
      if (resp && resp.ok) {
        var data = await resp.json();
        var cats = data.categories || data.items || (Array.isArray(data) ? data : []);
        var opts = '<option value="">— 选择类目 —</option>';
        cats.forEach(function(c){
          var name = (typeof c === 'string') ? c : (c.name || c.title || c.label || c.id || '');
          if (name) opts += '<option value="' + escapeAttr(name) + '">' + escapeHtml(name) + '</option>';
        });
        catSelect.innerHTML = opts;
        catSelect.dataset.loaded = '1';
      } else {
        catSelect.innerHTML = '<option value="">载入失败</option>';
      }
    } catch (e) {
      console.error('[sb2-patterns] induce load categories error:', e);
      catSelect.innerHTML = '<option value="">载入失败</option>';
    }
  }
}

function sb2PtnCloseInduceModal(){
  var modal = document.getElementById('sb2PtnInduceModal');
  if (modal) modal.classList.remove('open');
}

async function sb2PtnConfirmInduce(){
  var entityTypeEl = document.getElementById('sb2PtnInduceEntityType');
  var categoryEl = document.getElementById('sb2PtnInduceCategory');
  var entityType = entityTypeEl ? entityTypeEl.value : 'talent';
  var category = categoryEl ? categoryEl.value : '';
  var resultEl = document.getElementById('sb2PtnInduceResult');
  var btn = document.getElementById('sb2PtnInduceConfirmBtn');
  if (!category) {
    if (resultEl) {
      resultEl.textContent = '请先选择类目';
      resultEl.className = 'sb2-ptn-modal-result err';
    }
    return;
  }
  if (btn) btn.disabled = true;
  if (resultEl) {
    resultEl.textContent = 'LLM 归纳中… (取决于事件量, 可能 30-90 秒)';
    resultEl.className = 'sb2-ptn-modal-result muted';
  }
  try {
    var resp = await apiFetch('/api/knowledge-patterns/induce', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: category, entity_type: entityType })
    });
    if (!resp || !resp.ok) {
      var errMsg = 'HTTP ' + (resp ? resp.status : 'no response');
      try { var ej = await resp.json(); if (ej && ej.error) errMsg = ej.error; } catch (_) {}
      if (resultEl) {
        resultEl.textContent = '归纳失败: ' + errMsg;
        resultEl.className = 'sb2-ptn-modal-result err';
      }
      if (btn) btn.disabled = false;
      return;
    }
    var data = await resp.json();
    var lines = [];
    if (data.ok) {
      lines.push('✓ 归纳成功');
      lines.push('实体类型: ' + (data.entity_type || entityType));
      lines.push('新增草稿: ' + (data.induced != null ? data.induced + ' 条' : '-'));
      if (data.message) lines.push('说明: ' + data.message);
    } else {
      lines.push('⚠ 归纳未成功');
      if (data.error) lines.push('原因: ' + data.error);
      else if (data.message) lines.push('说明: ' + data.message);
    }
    if (resultEl) {
      resultEl.textContent = lines.join('\n');
      resultEl.className = 'sb2-ptn-modal-result ' + (data.ok ? 'ok' : 'err');
    }
    // MVP4: 列出 induced patterns (id / category / pattern_text / verification_level)
    var induceListEl = document.getElementById('sb2PtnInduceList');
    if (induceListEl) {
      var patterns = (data.patterns && Array.isArray(data.patterns)) ? data.patterns : [];
      if (data.ok && patterns.length > 0) {
        var listHtml = '';
        patterns.forEach(function(p){
          var ptext = p.pattern_text || '(无文本)';
          var pcat = p.category || '-';
          var plv = p.verification_level || 'hypothesis';
          var plvIcon = plv === 'verified' ? '📐' : (plv === 'candidate' ? '🔬' : '🧪');
          var pscore = p.confidence_score != null ? Math.round(p.confidence_score) : '-';
          var pevCount = p.evidence_count != null ? p.evidence_count : '-';
          var pidSafe = escapeAttr(p.id || '').replace(/'/g, "\\'");
          listHtml += '<div class="sb2-ptn-induce-item">';
          listHtml += '<div class="sb2-ptn-induce-item-top">';
          listHtml += '<span class="sb2-ptn-card-cat">' + escapeHtml(pcat) + '</span>';
          listHtml += '<span class="sb2-ptn-level ' + escapeAttr(plv) + '">' + plvIcon + ' ' + plv + '</span>';
          listHtml += '</div>';
          listHtml += '<div class="sb2-ptn-induce-item-text">' + escapeHtml(ptext) + '</div>';
          listHtml += '<div class="sb2-ptn-induce-item-meta">';
          listHtml += '<span>置信度 ' + pscore + '</span>';
          listHtml += '<span>证据 ' + pevCount + '</span>';
          listHtml += '<span style="margin-left:auto;">ID ' + escapeHtml(p.id || '-') + '</span>';
          listHtml += '</div>';
          listHtml += '<div class="sb2-ptn-induce-item-actions">';
          if (p.id) {
            listHtml += '<button class="sb2-btn sb2-btn-ghost" onclick="sb2PtnCloseInduceModal(); sb2PtnOpenDetail(\'' + pidSafe + '\');">查看详情 →</button>';
          }
          listHtml += '</div>';
          listHtml += '</div>';
        });
        induceListEl.innerHTML = listHtml;
        induceListEl.style.display = '';
      } else {
        induceListEl.style.display = 'none';
        induceListEl.innerHTML = '';
      }
    }
    showToast(data.ok ? '✓ 归纳完成, 新增 ' + (data.induced != null ? data.induced : '?') + ' 条草稿' : '⚠ 归纳未成功', data.ok ? 'success' : 'warning');
    // 刷新列表 (新归纳的 draft 规律会进 chips 计数)
    sb2PatternsInit();
  } catch (e) {
    console.error('[sb2-patterns] induce error:', e);
    if (resultEl) {
      resultEl.textContent = '归纳失败: ' + (e.message || '');
      resultEl.className = 'sb2-ptn-modal-result err';
    }
    if (btn) btn.disabled = false;
  }
}

function switchModule(module) {
  if (!hasModulePermission(module)) {
    showToast('⛔ 你没有该模块的访问权限', 'warning');
    if (module !== 'messages') switchModule('messages');
    return;
  }
  currentModule = module;

  // Update left nav active state
  document.querySelectorAll('.nav-item').forEach(function(el) { el.classList.remove('active'); });
  var navEl = document.getElementById('nav' + module.charAt(0).toUpperCase() + module.slice(1));
  if (navEl) navEl.classList.add('active');

  // Reset global search state
  resetGlobalSearch(module);

  // Registry of all module-scoped containers
  var allMids = ['knowledgeMid', 'knowledgeMidList', 'patternsMid', 'productsMidList', 'influencersMid', 'settingsMid', 'tasksMidList'];
  var allRights = ['knowledgeRight', 'productsRight', 'influencersRight', 'settingsRight', 'tasksRight'];

  // 1. Hide every module container first (strict mutual exclusion)
  allMids.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  allRights.forEach(function(id) {
    var el = document.getElementById(id);
    if (el) el.classList.remove('active');
  });
  var knowledgeRightOverlay = document.getElementById('knowledgeRightOverlay');
  if (knowledgeRightOverlay) knowledgeRightOverlay.classList.remove('open');

  // 2. Layout root reset
  var appMain = document.querySelector('.app-main');
  if (appMain) {
    appMain.classList.remove('knowledge-active', 'patterns-active', 'products-active', 'influencers-active', 'settings-active', 'tasks-active');
  }

  // 3. Messages module: sidebar employee list + chat area (original two-column layout)
  var employeeList = document.getElementById('employeeList');
  var projectList = document.getElementById('projectList');
  var chatArea = document.getElementById('chatArea');
  var appSidebar = document.querySelector('.app-sidebar');
  if (module === 'messages') {
    /* 〔fix/sb2-side-restore commit 9〕messages 主区恢复老 chat UI (12 轮热修)
       - 老大纠偏: commit 6 C 把 chatArea display 改 none 是过度执行
       - 现在 chatArea.style.display 还原 'flex', 老 chat UI 回来
       - #sb2ChatMain (新 sb2 chat 主区) hidden 由 wrapper2 控制 (line 46742)
         但 commit 9 #sb2ChatMain 内部恢复老 chat UI 内容, 跟 #chatArea 双保险
       - 侧栏 employeeList 仍显示 (AI 员工列表) */
    if (chatArea) chatArea.style.display = 'flex';
    // ★ fix/chat-page-tab-bar-768px-r2: ≤768 时 .app-sidebar 隐藏 (跟达人库/商品页 L34292 一致 inline 强制)
    //  - 之前: 不分 viewport 一律 display: flex → ≤768 主区被 280px 侧栏压缩
    //  - 现在: ≤768 隐藏, ≥769 显示 (跟达人库/商品 inline 逻辑对齐)
    //  - 这是 JS-side 防御, 跟 mobile CSS @media .app-sidebar { display: none !important } 互补
    //    (任一生效即可, 即使 Mac 浏览器 cache 旧 CSS, JS inline 仍 hide)
    if (appSidebar) appSidebar.style.display = (window.innerWidth <= 768) ? 'none' : 'flex';
    if (employeeList) employeeList.style.display = 'block';
    if (projectList) projectList.style.display = 'none';
    renderEmployeeList();
    var currentEmpId = localStorage.getItem('sb_current_emp');
    var currentEmp = currentEmpId && emps.find(function (e) { return e.id === currentEmpId; });
    if (!currentEmp) {
      renderEmptyChat();
    }
    applySidebarWidth(getSidebarWidth());
    return;
  }

  // 4. Other modules: hide chat/employee lists, set three-column layout, then show only current module
  if (chatArea) chatArea.style.display = 'none';
  if (employeeList) employeeList.style.display = 'none';
  if (projectList) projectList.style.display = 'none';
  if (appMain) appMain.classList.add(module + '-active');
  var appSidebar = document.querySelector('.app-sidebar');
  if (appSidebar) appSidebar.style.display = (module === 'products' || module === 'influencers') ? 'none' : 'flex';

  var moduleMidMap = {
    knowledge: ['knowledgeMid', 'knowledgeMidList'],
    patterns: ['patternsMid'],
    /* 〔fix/sb2-side-restore 26 轮②〕products 改 [] — 旧 #productsMidList 不再激活 (CSS 兜底隐藏, 函数保留不删) */
    products: [],
    influencers: ['influencersMid'],
    settings: ['settingsMid'],
    tasks: ['tasksMidList']
  };
  var moduleRightMap = {
    /* 〔fix/sb2-side-restore 26 轮②〕products 删 — 旧 #productsRight 不再激活 (CSS 兜底隐藏) */
    influencers: 'influencersRight',
    settings: 'settingsRight',
    tasks: 'tasksRight'
    // knowledge right is a drawer opened by document selection, not shown by default
  };

  (moduleMidMap[module] || []).forEach(function(id) {
    var el = document.getElementById(id);
    if (!el) return;
    el.classList.add('active');
    // 知识库中栏应自适应剩余宽度，清除可能被侧边栏拖拽残留的固定宽度
    if (id === 'knowledgeMidList') {
      el.style.width = '';
      el.style.flex = '';
    }
  });
  var rightId = moduleRightMap[module];
  if (rightId) {
    var rightEl = document.getElementById(rightId);
    if (rightEl) rightEl.classList.add('active');
  }

  if (module === 'knowledge') {
    // ★ sb2-knowledge: 显示新屏, 旧 knowledgeMid 系列 CSS 兜底 display:none (旧 DOM 不退役仅兜底)
    var sb2kb = document.getElementById('sb2KnowledgeMain');
    if (sb2kb) sb2kb.classList.add('active');
    loadGroups();
    if (typeof sb2KbInit === 'function') sb2KbInit();
  }
  else if (module === 'patterns') {
    // ★ sb2-patterns: 显示新屏, 旧 patternsMid 系列 CSS 兜底 display:none (旧 DOM 不退役仅兜底)
    var sb2ptn = document.getElementById('sb2PatternsMain');
    if (sb2ptn) {
      sb2ptn.classList.add('active');
      if (typeof sb2PatternsInit === 'function') sb2PatternsInit();
    }
  }
  else if (module === 'products') {
    /* 〔fix/sb2-side-restore 26 轮②〕商品库主区还原 — sb2-products MVP1 接入点
       修前: loadProductBrands + loadProductCategories + loadProducts (旧 module-mid/right 双栏, 唯一未收进 sb2 体系的模块主区)
       修后: 仿 sb2-patterns/sb2-knowledge precedent — add .sb2-products-main.active + sb2ProductsInit()
       兜底: 旧 loadProductBrands/loadProductCategories/loadProducts 仍保留 (CSS 兜底隐藏旧 DOM, 但函数不删, 回归失败可切回) */
    var sb2pds = document.getElementById('sb2ProductsMain');
    if (sb2pds) {
      sb2pds.classList.add('active');
      /* 〔fix/sb2-side-restore r34.2 协调人代补〕26 轮② 接入点漏调 loadProductCategories —
         侧栏类目/「N 件在库」sub 数据源是全仓唯一调用点 (legacy switchModule line 44013 同款), 漏调则类目永「加载中…」 */
      try { if (typeof loadProductCategories === 'function') loadProductCategories(); } catch(e) {}
      if (typeof sb2ProductsInit === 'function') sb2ProductsInit();
    }
  }
  else if (module === 'influencers') {
    // ★ sb2-talents MVP1: 显示新屏, 旧 #influencersMid / #influencersRight CSS 兜底 display:none (旧 DOM 不退役仅兜底)
    var sb2t = document.getElementById('sb2TalentsMain');
    if (sb2t) {
      sb2t.classList.add('active');
      if (typeof sb2TalentsInit === 'function') sb2TalentsInit();
    }
    // 旧 switchTalentMidTab 保留兜底 (回归失败可切回), 但默认走新屏
  }
  else if (module === 'tasks') {
    // ★ sb2-tasks MVP1: 显示新屏, 旧 #tasksMidList 隐藏 (旧 DOM 兜底不退役, 但新屏显示时必须 hidden)
    var sb2tk = document.getElementById('sb2TasksMain');
    if (sb2tk) {
      sb2tk.classList.add('active');
      if (typeof sb2TasksInit === 'function') sb2TasksInit();
    }
    var oldTasksMid = document.getElementById('tasksMidList');
    if (oldTasksMid) {
      oldTasksMid.classList.remove('active');
      oldTasksMid.setAttribute('hidden', '');
      oldTasksMid.style.display = 'none'; // 强制 inline style 覆盖 CSS .active { display: flex }
    }
    var oldTasksRight = document.getElementById('tasksRight');
    if (oldTasksRight) {
      oldTasksRight.setAttribute('hidden', '');
      oldTasksRight.style.display = 'none';
    }
    // 旧 loadTasks 仍保留 (回归失败可切回), 但默认走新屏
  }
  else if (module === 'settings') {
    // 旧 renderSettingsMid 仍在 (CSP/系统设置兜底) — 保留, 但 MVP6 新屏优先显示
    if (typeof renderSettingsMid === 'function') renderSettingsMid();
    // ★ MVP6: 显示新功能入口卡片列表
    var sb2set = document.getElementById('sb2SettingsMain');
    if (sb2set) {
      sb2set.classList.add('active');
      if (typeof sb2SettingsShow === 'function') sb2SettingsShow();
    }
  }
  applySidebarWidth(getSidebarWidth());
  // fix/responsive-shell-collapse-r3: 模块切换也重排(防止 inline style 被覆盖/不同
  // 模块里 .app-main 子节点的 layout 改变影响 .left-nav 视觉)
  if (typeof forceResponsiveShell === 'function') forceResponsiveShell();
}

function getDefaultGlobalSearchScope(module) {
  return module === 'knowledge' ? 'knowledge' : 'all';
}

function resetGlobalSearch(module) {
  _globalSearchScope = getDefaultGlobalSearchScope(module);
  var input = document.getElementById('globalSearchInput');
  var dropdown = document.getElementById('globalSearchDropdown');
  if (input) {
    input.value = '';
    input.placeholder = '搜索';
  }
  if (dropdown) dropdown.classList.remove('open');
  updateGlobalSearchScopeTabs();
}

function updateGlobalSearchScopeTabs() {
  document.querySelectorAll('.global-search-scope-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.scope === _globalSearchScope);
  });
}

function setGlobalSearchScope(scope) {
  _globalSearchScope = scope;
  updateGlobalSearchScopeTabs();
  var input = document.getElementById('globalSearchInput');
  if (input && input.value.trim()) {
    performGlobalSearch(input.value.trim());
  }
  if (currentModule === 'knowledge') {
    loadKnowledgePage(1);
  }
}

function onGlobalSearchInput(value) {
  clearTimeout(_globalSearchTimer);
  var q = value.trim();
  if (!q) {
    closeGlobalSearchDropdown();
    return;
  }
  _globalSearchTimer = setTimeout(function () {
    performGlobalSearch(q);
  }, 200);

  // 在知识库页面且范围为知识库时，同步过滤中间列表
  if (currentModule === 'knowledge' && _globalSearchScope === 'knowledge') {
    clearTimeout(window._knowledgeSearchTimer);
    window._knowledgeSearchTimer = setTimeout(function () {
      loadKnowledgePage(1);
    }, 250);
  }
}

function onGlobalSearchFocus() {
  var input = document.getElementById('globalSearchInput');
  if (input && input.value.trim()) {
    performGlobalSearch(input.value.trim());
  }
}

function onGlobalSearchKeydown(e) {
  if (e.key === 'Escape') {
    closeGlobalSearchDropdown();
    return;
  }
  if (e.key === 'Enter') {
    var first = document.querySelector('.global-search-item');
    if (first) first.click();
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    e.preventDefault();
    var items = Array.from(document.querySelectorAll('.global-search-item'));
    var active = document.querySelector('.global-search-item.active');
    var idx = active ? items.indexOf(active) : -1;
    if (e.key === 'ArrowDown') idx = Math.min(idx + 1, items.length - 1);
    else idx = Math.max(idx - 1, 0);
    items.forEach(function(it) { it.classList.remove('active'); });
    if (items[idx]) {
      items[idx].classList.add('active');
      items[idx].scrollIntoView({ block: 'nearest' });
    }
  }
}

function closeGlobalSearchDropdown() {
  var dropdown = document.getElementById('globalSearchDropdown');
  if (dropdown) dropdown.classList.remove('open');
}

function highlightText(text, q) {
  if (!text) return '';
  var lower = text.toLowerCase();
  var idx = lower.indexOf(q.toLowerCase());
  if (idx < 0) return escapeHtml(text);
  var before = text.slice(0, idx);
  var match = text.slice(idx, idx + q.length);
  var after = text.slice(idx + q.length);
  return escapeHtml(before) + '<span class="global-search-highlight">' + escapeHtml(match) + '</span>' + highlightText(after, q);
}

function performGlobalSearch(q) {
  var dropdown = document.getElementById('globalSearchDropdown');
  var resultsEl = document.getElementById('globalSearchResults');
  if (!dropdown || !resultsEl) return;
  if (!q) {
    closeGlobalSearchDropdown();
    return;
  }
  resultsEl.innerHTML = '<div class="global-search-empty">搜索中...</div>';
  dropdown.classList.add('open');
  apiFetch('/api/search?q=' + encodeURIComponent(q) + '&scope=' + encodeURIComponent(_globalSearchScope) + '&limit=8')
    .then(function (r) { return r.json(); })
    .then(function (data) {
      renderGlobalSearchResults(data, q);
    })
    .catch(function (e) {
      resultsEl.innerHTML = '<div class="global-search-empty">搜索失败</div>';
    });
}

function renderGlobalSearchResults(data, q) {
  var resultsEl = document.getElementById('globalSearchResults');
  if (!resultsEl) return;
  var groups = data.groups || {};
  var html = '';
  var groupLabels = {
    employees: 'AI员工',
    groups: '项目组',
    knowledge: '知识库'
  };
  var hasAny = false;
  ['employees', 'groups', 'knowledge'].forEach(function (key) {
    var items = groups[key] || [];
    if (!items.length) return;
    hasAny = true;
    html += '<div class="global-search-group">';
    html += '<div class="global-search-group-title">' + escapeHtml(groupLabels[key]) + '</div>';
    items.forEach(function (item) {
      html += renderGlobalSearchItem(key, item, q);
    });
    html += '</div>';
  });
  if (!hasAny) {
    html = '<div class="global-search-empty">未找到 "' + escapeHtml(q) + '" 相关结果</div>';
  }
  resultsEl.innerHTML = html;
}

function renderGlobalSearchItem(type, item, q) {
  var iconHtml = '';
  var name = '';
  var desc = '';
  var count = '';
  var action = '';
  if (type === 'employees') {
    var isImg = typeof item.avatar === 'number' || (typeof item.avatar === 'string' && (item.avatar.indexOf('data:image') === 0 || item.avatar.indexOf('.png') > 0 || item.avatar.indexOf('.jpg') > 0));
    var avatarContent = '';
    if (typeof item.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[item.avatar]) {
      avatarContent = '<img src="' + escapeAttr(AVATAR_PRESETS[item.avatar]) + '" alt="">';
    } else if (isImg) {
      avatarContent = '<img src="' + escapeAttr(item.avatar) + '" alt="">';
    } else {
      avatarContent = escapeHtml(item.avatar || (item.name ? item.name.charAt(0) : '?'));
    }
    iconHtml = '<div class="global-search-avatar" style="background:' + escapeAttr(item.bg || '#FF6B35') + ';color:#fff;">' + avatarContent + '</div>';
    name = highlightText(item.name, q);
    desc = escapeHtml(item.role || 'AI员工');
    action = 'openGlobalSearchResult(&quot;employee&quot;,' + JSON.stringify(item.id || '').replace(/"/g, '&quot;') + ')';
  } else if (type === 'groups') {
    iconHtml = '<div class="global-search-icon">' + escapeHtml(item.avatar || '👥') + '</div>';
    name = highlightText(item.name, q);
    desc = '项目组';
    count = (item.memberCount || 0) + ' 人';
    action = 'openGlobalSearchResult(&quot;group&quot;,' + JSON.stringify(item.id || '').replace(/"/g, '&quot;') + ')';
  } else if (type === 'knowledge') {
    iconHtml = '<div class="global-search-icon"><svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H19a1 1 0 0 1 1 1v18a1 1 0 0 1-1 1H6.5a1 1 0 0 1 0-5H20"/></svg></div>';
    name = highlightText(item.title, q);
    desc = escapeHtml(item.category || '知识库') + (item.preview ? ' · ' + escapeHtml(item.preview) : '');
    action = 'openGlobalSearchResult(&quot;knowledge&quot;,' + JSON.stringify(item.id || '').replace(/"/g, '&quot;') + ')';
  }
  return '<div class="global-search-item" onclick="' + action + '">' + iconHtml +
    '<div class="global-search-info"><div class="global-search-name">' + name + '</div><div class="global-search-desc">' + desc + '</div></div>' +
    (count ? '<div class="global-search-count">' + count + '</div>' : '') +
    '</div>';
}

function openGlobalSearchResult(type, id) {
  closeGlobalSearchDropdown();
  if (type === 'employee') {
    if (currentModule !== 'messages') switchModule('messages');
    openChat(id);
  } else if (type === 'group') {
    if (currentModule !== 'messages') switchModule('messages');
    openGroupChat(id);
  } else if (type === 'knowledge') {
    if (currentModule !== 'knowledge') switchModule('knowledge');
    openKnowledgeDocById(id);
  }
}

function openKnowledgeDocById(id) {
  apiFetch('/api/knowledge/entries/' + encodeURIComponent(id))
    .then(function (r) { return r.json(); })
    .then(function (doc) {
      if (!doc || doc.error) return;
      // 若文档不在当前列表则追加到头部，保持列表一致
      var existing = _knowledgeData.docs.find(function (d) { return d.id === id; });
      if (!existing) {
        _knowledgeData.docs.unshift(doc);
        _knowledgeData.total = (_knowledgeData.total || 0) + 1;
        renderKnowledgeList(_knowledgeData.docs);
      }
      _knowledgeCurrentId = id;
      renderKnowledgeView(doc);
      var right = document.getElementById('knowledgeRight');
      var overlay = document.getElementById('knowledgeRightOverlay');
      if (right) right.classList.add('active');
      if (overlay) overlay.classList.add('open');
      document.querySelectorAll('.knowledge-mid-card').forEach(function (el) { el.classList.remove('active'); });
      var activeEl = document.querySelector('.knowledge-mid-card[data-id="' + escapeAttr(id || '') + '"]');
      if (activeEl) activeEl.classList.add('active');
    })
    .catch(function (e) { showToast('打开文档失败'); });
}

function onModuleSearch(value) {
  if (currentModule === 'influencers') {
    searchInfluencersDebounced(value);
  }
}

function closeUserDropdownDirect() {
  var dropdown = document.getElementById('userDropdown');
  if (dropdown) dropdown.classList.remove('show');
}

function openGlobalSearch() {
  var modal = document.getElementById('globalSearchModal');
  if (modal) modal.classList.add('show');
  var input = document.getElementById('globalSearchInput');
  if (input) input.focus();
}

function showUserMenu() {
  var dropdown = document.getElementById('userDropdown');
  if (!dropdown) return;
  var isOpen = dropdown.classList.contains('show');
  if (isOpen) {
    dropdown.classList.remove('show');
    document.removeEventListener('click', closeUserDropdown);
  } else {
    dropdown.classList.add('show');
    setTimeout(function () {
      document.addEventListener('click', closeUserDropdown);
    }, 0);
  }
}
function closeUserDropdown(e) {
  var dropdown = document.getElementById('userDropdown');
  var avatar = document.getElementById('navUserAvatar');
  if (!dropdown || !avatar) return;
  if (!dropdown.contains(e.target) && e.target !== avatar && !avatar.contains(e.target)) {
    dropdown.classList.remove('show');
    document.removeEventListener('click', closeUserDropdown);
  }
}
function confirmLogout() {
  var dropdown = document.getElementById('userDropdown');
  if (dropdown) dropdown.classList.remove('show');
  if (confirm('确定要退出登录吗？')) {
    doLogout();
  }
}

// ============ Security Tools (dev/feat: #5) ============
// 后端: /api/auth/admin/emergency-rotate-secret (commit 9765fa1)
// 仅 admin 可见, 二次确认 + 输入 reason. 紧急 JWT secret 轮换用于安全事件响应.
function showSecurityTools() {
  if (!isAdmin()) {
    showToast('⚠️ 仅管理员可访问安全工具');
    return;
  }
  var dropdown = document.getElementById('userDropdown');
  if (dropdown) dropdown.classList.remove('show');
  showEmergencyRotateConfirm();
}
function showEmergencyRotateConfirm() {
  // 移除已有 modal 防止重复
  var existing = document.getElementById('emergencyRotateModalOverlay');
  if (existing) existing.remove();
  var html = '<div id="emergencyRotateModalOverlay" class="modal-overlay active" '
           + 'onclick="if(event.target===this)closeEmergencyRotateModal()">'
           + '<div class="modal-panel" style="max-width:480px;">'
           +   '<div class="modal-header" style="display:flex;align-items:center;gap:10px;padding:16px 20px;border-bottom:1px solid var(--separator);">'
           +     '<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" x2="12" y1="9" y2="13"/><line x1="12" x2="12.01" y1="17" y2="17"/></svg>'
           +     '<div style="font-weight:600;font-size:16px;">🚨 紧急 JWT Secret 轮换</div>'
           +   '</div>'
           +   '<div style="padding:18px 20px;">'
           +     '<p style="margin:0 0 12px;color:var(--text-secondary);font-size:13px;">'
           +       '用于安全事件响应（如 token 泄漏、可疑登录）。所有现有 JWT 将在 grace period 内仍可用，旧 secret 同时失效。'
           +     '</p>'
           +     '<label style="display:block;font-size:13px;font-weight:500;margin-bottom:6px;">'
           +       '事件描述（必填, 会写入审计日志）'
           +     '</label>'
           +     '<textarea id="emergencyRotateReason" rows="3" '
           +       'placeholder="例: 2026-09-17 14:30 发现 GitHub PAT 泄漏" '
           +       'style="width:100%;padding:10px;border:1px solid var(--separator);border-radius:8px;'
           +       'background:var(--bg-tertiary);color:var(--text);font-size:13px;font-family:inherit;'
           +       'resize:vertical;box-sizing:border-box;"></textarea>'
           +     '<div style="margin-top:8px;font-size:11px;color:var(--text-tertiary);">'
           +       '此操作不可撤销, 会广播到所有 session。'
           +     '</div>'
           +   '</div>'
           +   '<div style="display:flex;gap:10px;padding:12px 20px;border-top:1px solid var(--separator);justify-content:flex-end;">'
           +     '<button class="modal-btn modal-btn-secondary" onclick="closeEmergencyRotateModal()">取消</button>'
           +     '<button class="modal-btn modal-btn-danger" onclick="doEmergencyRotate()">🚨 立即轮换</button>'
           +   '</div>'
           + '</div>'
           + '</div>';
  document.body.insertAdjacentHTML('beforeend', html);
  setTimeout(function () {
    var ta = document.getElementById('emergencyRotateReason');
    if (ta) ta.focus();
  }, 50);
}
function closeEmergencyRotateModal() {
  var m = document.getElementById('emergencyRotateModalOverlay');
  if (m) m.remove();
}
function doEmergencyRotate() {
  var reasonEl = document.getElementById('emergencyRotateReason');
  var reason = (reasonEl && reasonEl.value || '').trim();
  if (!reason) {
    showToast('⚠️ 请填写事件描述（必填, 写入审计）', 'error');
    if (reasonEl) reasonEl.focus();
    return;
  }
  if (reason.length > 200) {
    showToast('⚠️ 事件描述请控制在 200 字内', 'error');
    return;
  }
  // 二次确认 (紧急操作, 防止误触)
  if (!confirm('确认轮换 JWT Secret？\n\n该操作会写入审计日志 + 广播到所有 session。')) return;
  closeEmergencyRotateModal();
  showToast('🚨 正在轮换 JWT Secret...', 'info');
  apiFetch('/api/auth/admin/emergency-rotate-secret', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason })
  })
    .then(function (r) {
      return r && r.ok ? r.json().then(function (j) { return { ok: true, data: j }; })
                        : r && r.json().then(function (j) { return { ok: false, err: j }; });
    })
    .catch(function (e) { return { ok: false, err: { error: String(e) } }; })
    .then(function (out) {
      if (out && out.ok && out.data) {
        showToast('✅ JWT Secret 轮换完成 (grace period=' + (out.data.gracePeriodSeconds || '?') + 's)', 'success');
      } else {
        var msg = (out && out.err && (out.err.error || out.err.message)) || '轮换失败';
        showToast('❌ ' + msg, 'error');
      }
    });
}
function showUserProfile() {
  var dropdown = document.getElementById('userDropdown');
  if (dropdown) dropdown.classList.remove('show');
  if (!currentUser) return;
  showToast('👤 ' + escapeHtml(currentUser.displayName || currentUser.name) + ' · ' + getCurrentUserRoleDisplay());
}

function formatNumber(n) {
  return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
function formatFollowers(n) {
  var num = parseFloat(n) || 0;
  if (num >= 10000) {
    var w = (num / 10000).toFixed(1).replace(/\.0$/, '');
    return w + 'w';
  }
  return formatNumber(num);
}

// ========== 积分仪表盘 ==========
function normalizeTokenUsageItem(item) {
  return {
    id: item.id || '',
    name: item.name || item.date || '未知',
    date: item.date || '',
    inputTokens: item.inputTokens || 0,
    outputTokens: item.outputTokens || 0,
    cacheReadTokens: item.cacheReadTokens || 0,
    totalTokens: item.totalTokens || 0,
    calls: item.calls || 0
  };
}
// ---- 积分仪表盘（1 积分 = 1000 tokens）----
var _creditUsageExpanded = false;
function _creditTodayStr() {
  var d = new Date();
  var m = String(d.getMonth() + 1);
  var day = String(d.getDate());
  return d.getFullYear() + '-' + (m.length < 2 ? '0' + m : m) + '-' + (day.length < 2 ? '0' + day : day);
}
async function syncTokenUsage() {
  try {
    showToast('🔄 正在同步 Token 数据...');
    var resp = await apiFetch('/api/token-usage/sync');
    var data = await resp.json();
    showToast('✅ 同步完成：扫描 ' + (data.scannedFiles || 0) + ' 个文件，新增 ' + (data.inserted || 0) + ' 条记录');
    loadComputeStats();
  } catch (e) {
    console.warn('[syncTokenUsage] failed:', e);
    showToast('❌ 同步失败');
  }
}
var _creditTimeRange = 'today';
function _creditDateRange(range) {
  var end = new Date(); var start = new Date();
  if (range === 'yesterday') { start.setDate(start.getDate()-1); end.setDate(end.getDate()-1); }
  else if (range === '7d') { start.setDate(start.getDate()-6); }
  else if (range === '30d') { start.setDate(start.getDate()-29); }
  var fmt = function(d) { var m=String(d.getMonth()+1), day=String(d.getDate()); return d.getFullYear()+'-'+(m.length<2?'0'+m:m)+'-'+(day.length<2?'0'+day:day); };
  return { start: fmt(start), end: fmt(end) };
}
function setCreditTimeRange(range) { _creditTimeRange = range; loadComputeStats(); }
async function loadComputeStats() {
  var area = document.getElementById('computeStatsArea');
  if (!area) return;
  area.innerHTML = '<div class="settings-card">' + renderSkeleton('detail') + '</div>';
  try {
    var dr = _creditDateRange(_creditTimeRange);
    var results = await Promise.all([
      apiFetch('/api/credits/balance'),
      apiFetch('/api/credits/quotas'),
      apiFetch('/api/credits/usage?start_date=' + dr.start + '&end_date=' + dr.end + '&page_size=200'),
      apiFetch('/api/credits/usage/summary?start_date=' + dr.start + '&end_date=' + dr.end)
    ]);
    var balances = results[0] ? await results[0].json() : [];
    var quotas = results[1] ? await results[1].json() : [];
    var todayUsage = results[2] ? await results[2].json() : {};
    var summary = results[3] ? await results[3].json() : {};
    area.innerHTML = renderCreditDashboard(
      Array.isArray(balances) ? balances : [],
      Array.isArray(quotas) ? quotas : [],
      (todayUsage && todayUsage.data) || [],
      summary || {}
    );
  } catch (e) {
    console.warn('[loadComputeStats] 获取积分数据失败', e);
    area.innerHTML = '<div class="settings-card">' + renderEmptyState({
      icon: '⚠️',
      title: '加载失败',
      desc: '积分数据获取失败,请稍后重试',
      tone: 'danger',
      cta: { label: '重试', onclick: 'loadComputeStats()' }
    }) + '</div>';
  }
}
function _creditStatChip(label, value) {
  return '<div style="flex:1;min-width:140px;border-radius:12px;background:rgba(22, 119, 255, 0.06);padding:12px 14px;"><div style="font-size:12px;color:var(--color-text-secondary, #6E6E73);">' + label + '</div><div style="font-size:20px;font-weight:700;color:var(--accent, #1677ff);margin-top:2px;">' + value + '</div></div>';
}
function renderCreditDashboard(balances, quotas, todayRecords, summary) {
  var admin = isAdmin();
  if (!admin) {
    var _myAgentIds = {};
    (typeof emps !== 'undefined' ? emps : []).forEach(function(e) { _myAgentIds[e.id] = true; });
    todayRecords = todayRecords.filter(function(r) { return _myAgentIds[r.agent_id]; });
    balances = balances.filter(function(b) { return _myAgentIds[b.agent_id]; });
  }
  var balMap = {};
  balances.forEach(function (b) { balMap[b.agent_id] = b; });
  // 最新配额（后端按 id 倒序返回，首个即为最新）
  var quotaMap = {};
  quotas.forEach(function (q) { if (!quotaMap[q.agent_id]) quotaMap[q.agent_id] = q; });
  // 今日消耗（按员工聚合）
  var todayMap = {};
  todayRecords.forEach(function (r) { todayMap[r.agent_id] = (todayMap[r.agent_id] || 0) + (r.credits_used || 0); });
  // 员工列表：优先 emps 全量，再补充只有积分账户的员工
  var agents = [];
  var seen = {};
  (typeof emps !== 'undefined' ? emps : []).forEach(function (e) {
    if (!e || !e.id || seen[e.id]) return;
    seen[e.id] = true;
    agents.push({ id: e.id, name: e.name || e.id });
  });
  Object.keys(balMap).forEach(function (id) {
    if (!seen[id]) { seen[id] = true; agents.push({ id: id, name: id }); }
  });

  var html = '';
  // 总览
  html += '<div class="settings-card"><div class="settings-card-title">总览（1 积分 = 1000 tokens）</div>';
  html += '<div style="display:flex;gap:6px;margin-bottom:12px;">';
  ['today:今日','yesterday:昨日','7d:近7天','30d:近30天'].forEach(function(r){ var kv=r.split(':'); var isActive = kv[0]===_creditTimeRange; html += '<button onclick="setCreditTimeRange(\''+kv[0]+'\')" style="padding:4px 12px;border-radius:8px;border:1px solid '+(isActive?'var(--accent, #1677ff)':'var(--separator, rgba(0, 0, 0, 0.12))')+';background:'+(isActive?'var(--accent, #1677ff)':'var(--color-bg, #FFFFFF)')+';color:'+(isActive?'#fff':'var(--color-text-primary, #1C1C1E)')+';font-size:12px;cursor:pointer;font-family:inherit;transition:all 0.15s;">'+kv[1]+'</button>'; });
  html += '</div>';
  html += '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
  html += _creditStatChip('累计消耗积分', formatNumber(summary.total_credits_used || 0));
  html += _creditStatChip('累计 Tokens', formatNumber(summary.total_tokens || 0));
  html += _creditStatChip('日均消耗积分', formatNumber(summary.daily_avg_credits || 0));
  html += _creditStatChip('消耗记录数', formatNumber(summary.records_count || 0));
  html += '</div></div>';

  // 员工积分卡片
  html += '<div class="settings-card"><div class="settings-card-title">员工积分余额</div>';
  if (!agents.length) {
    html += '<div class="settings-empty" style="padding:20px;">暂无员工数据</div>';
  } else {
    var maxToday = 1;
    agents.forEach(function (a) { maxToday = Math.max(maxToday, todayMap[a.id] || 0); });
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;">';
    agents.forEach(function (a) {
      var acc = balMap[a.id] || { balance: 0, total_recharged: 0, total_consumed: 0 };
      var quota = quotaMap[a.id];
      var todayUsed = todayMap[a.id] || 0;
      var denom = quota && quota.quota_amount > 0 ? quota.quota_amount : maxToday;
      var pct = Math.min(Math.round(todayUsed / denom * 100), 100);
      var lowBalance = (acc.balance || 0) <= 0;
      var safeName = String(a.name).replace(/'/g, "\\'");
      html += '<div style="border:0.5px solid var(--sb2-border,rgba(0,0,0,0.08));border-radius:12px;padding:14px;background:var(--sb2-s1,#fff);">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">';
      html += '<div style="font-size:14px;font-weight:600;color:var(--text-primary);">' + escapeHtml(a.name) + '</div>';
      if (admin) {
        html += '<div style="display:flex;gap:6px;">';
        html += '<button class="module-action-btn" style="padding:2px 10px;font-size:12px;" onclick="rechargeAgentCredits(\'' + a.id + '\', \'' + safeName + '\')">充值</button>';
        html += '<button class="module-action-btn" style="padding:2px 10px;font-size:12px;background:rgba(22, 119, 255,0.08);color:#1677ff;" onclick="setAgentQuota(\'' + a.id + '\', \'' + safeName + '\')">配额</button>';
        html += '</div>';
      } else {
        html += '<div style="font-size:12px;color:var(--text-tertiary);">充值/配额请联系管理员</div>';
      }
      html += '</div>';
      html += '<div style="font-size:26px;font-weight:700;color:' + (lowBalance ? '#FF3B30' : '#1677ff') + ';">' + formatNumber(acc.balance || 0) + '<span style="font-size:12px;font-weight:400;color:var(--text-tertiary);margin-left:4px;">积分</span></div>';
      html += '<div style="font-size:12px;color:var(--color-text-secondary, #6E6E73);margin:4px 0 8px;">累计充值 ' + formatNumber(acc.total_recharged || 0) + ' · 累计消耗 ' + formatNumber(acc.total_consumed || 0) + '</div>';
      html += '<div style="font-size:12px;color:var(--text-secondary);display:flex;justify-content:space-between;"><span>今日消耗 ' + formatNumber(todayUsed) + ' 积分</span>' + (quota ? '<span>配额 ' + formatNumber(quota.quota_amount) + (quota.quota_type === 'daily' ? '（每日）' : '（每月）') + '</span>' : '') + '</div>';
      html += '<div style="height:6px;border-radius:3px;background:rgba(22, 119, 255,0.12);margin-top:6px;overflow:hidden;"><div style="height:100%;width:' + pct + '%;background:#1677ff;border-radius:3px;"></div></div>';
      html += '</div>';
    });
    html += '</div>';
  }
  html += '</div>';

  // 使用记录明细（可展开）
  html += '<div class="settings-card"><div class="settings-card-title" style="cursor:pointer;display:flex;justify-content:space-between;align-items:center;" onclick="toggleCreditUsageList()">使用记录明细<span id="creditUsageToggleArrow" style="font-size:12px;color:var(--accent, #1677ff);font-weight:400;">展开 ▼</span></div>';
  html += '<div id="creditUsageListArea" style="display:none;"></div></div>';
  return html;
}
async function rechargeAgentCredits(agentId, agentName) {
  var input = prompt('给「' + agentName + '」充值积分（1 积分 = 1000 tokens）：', '1000');
  if (input === null) return;
  var amount = parseInt(input, 10);
  if (!amount || amount <= 0) { showToast('❌ 请输入有效的积分数', 'error'); return; }
  try {
    var resp = await apiFetch('/api/credits/recharge', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ agent_id: agentId, amount: amount })
    });
    var data = await resp.json();
    if (resp.ok) {
      showToast('✅ 充值成功，「' + agentName + '」当前余额 ' + (data.new_balance || 0) + ' 积分');
      loadComputeStats();
    } else {
      showToast('❌ ' + (data.error || '充值失败'), 'error');
    }
  } catch (e) {
    console.warn('[Credits] 充值失败:', e);
    showToast('❌ 充值失败', 'error');
  }
}
async function setAgentQuota(agentId, agentName) {
  var type = prompt('配额类型（输入 daily 或 monthly）：', 'monthly');
  if (type === null) return;
  type = type.trim() === 'daily' ? 'daily' : 'monthly';
  var input = prompt('给「' + agentName + '」充值配额积分（' + (type === 'daily' ? '每日' : '每月') + '）：', '3000');
  if (input === null) return;
  var amount = parseInt(input, 10);
  if (!amount || amount <= 0) { showToast('❌ 请输入有效的积分数', 'error'); return; }
  try {
    var resp = await apiFetch('/api/credits/quotas/' + encodeURIComponent(agentId) + '/recharge', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ amount: amount, quota_type: type })
    });
    var data = await resp.json();
    if (resp.ok) {
      showToast('✅ 配额充值成功，「' + agentName + '」当前余额 ' + (data.new_balance || 0) + ' 积分');
      loadComputeStats();
    } else {
      showToast('❌ ' + (data.error || '配额设置失败'), 'error');
    }
  } catch (e) {
    console.warn('[Credits] 配额设置失败:', e);
    showToast('❌ 配额设置失败', 'error');
  }
}
async function toggleCreditUsageList() {
  _creditUsageExpanded = !_creditUsageExpanded;
  var box = document.getElementById('creditUsageListArea');
  var arrow = document.getElementById('creditUsageToggleArrow');
  if (!box) return;
  if (!_creditUsageExpanded) {
    box.style.display = 'none';
    if (arrow) arrow.textContent = '展开 ▼';
    return;
  }
  box.style.display = '';
  if (arrow) arrow.textContent = '收起 ▲';
  box.innerHTML = '<div style="padding:12px;color:var(--text-tertiary);">加载中...</div>';
  try {
    var resp = await apiFetch('/api/credits/usage?page=1&page_size=20');
    var data = await resp.json();
    box.innerHTML = renderCreditUsageList((data && data.data) || []);
  } catch (e) {
    console.warn('[Credits] 使用记录加载失败:', e);
    box.innerHTML = '<div style="padding:12px;color:var(--text-tertiary);">加载失败</div>';
  }
}
function renderCreditUsageList(records) {
  if (!records.length) return '<div style="padding:12px;color:var(--text-tertiary);">暂无使用记录</div>';
  var nameMap = {};
  (typeof emps !== 'undefined' ? emps : []).forEach(function (e) { if (e && e.id) nameMap[e.id] = e.name || e.id; });
  var cols = '150px 1fr 90px 90px 90px 70px';
  var html = '<div style="font-size:12px;overflow-x:auto;">';
  html += '<div style="display:grid;grid-template-columns:' + cols + ';gap:8px;padding:8px 4px;color:var(--text-tertiary);border-bottom:0.5px solid rgba(0,0,0,0.08);"><span>时间</span><span>员工</span><span>输入</span><span>输出</span><span>缓存</span><span>积分</span></div>';
  records.forEach(function (r) {
    html += '<div style="display:grid;grid-template-columns:' + cols + ';gap:8px;padding:8px 4px;border-bottom:0.5px solid rgba(0,0,0,0.04);color:var(--text-secondary);">'
      + '<span>' + escapeHtml((r.created_at || '').slice(5, 16)) + '</span>'
      + '<span style="color:var(--text-primary);">' + escapeHtml(nameMap[r.agent_id] || r.agent_id) + '</span>'
      + '<span>' + formatNumber(r.input_tokens || 0) + '</span>'
      + '<span>' + formatNumber(r.output_tokens || 0) + '</span>'
      + '<span>' + formatNumber(r.cache_read_tokens || 0) + '</span>'
      + '<span style="color:#1677ff;font-weight:600;">' + formatNumber(r.credits_used || 0) + '</span></div>';
  });
  html += '</div>';
  return html;
}

// ========== 通知历史 ==========
var _notificationHistoryCache = [];

// ★ fix/notification-panel-polish: 友好时间格式 helper (panel + history 共用)
//   <1 min → 刚刚 / <1h → X 分钟前 / 今天 → HH:MM / 昨天 → 昨天 HH:MM / 今年 → M月D日 HH:MM / 跨年 → YYYY/M/D HH:MM
function formatNotificationTime(date) {
  var d = (date instanceof Date) ? date : new Date(date || 0);
  if (isNaN(d.getTime())) return '';
  var now = new Date();
  var diffMs = now.getTime() - d.getTime();
  var diffSec = Math.floor(diffMs / 1000);
  if (diffSec < 60) return '刚刚';
  if (diffSec < 3600) return Math.floor(diffSec / 60) + ' 分钟前';
  // 同一天 (今天)
  var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
  if (sameDay) {
    var hh = String(d.getHours()).padStart(2, '0');
    var mm = String(d.getMinutes()).padStart(2, '0');
    return hh + ':' + mm;
  }
  // 昨天
  var yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  var isYesterday = d.getFullYear() === yesterday.getFullYear() && d.getMonth() === yesterday.getMonth() && d.getDate() === yesterday.getDate();
  if (isYesterday) {
    var hh2 = String(d.getHours()).padStart(2, '0');
    var mm2 = String(d.getMinutes()).padStart(2, '0');
    return '昨天 ' + hh2 + ':' + mm2;
  }
  // 今年
  if (d.getFullYear() === now.getFullYear()) {
    return (d.getMonth() + 1) + '月' + d.getDate() + '日 ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  }
  // 跨年
  return d.getFullYear() + '/' + (d.getMonth() + 1) + '/' + d.getDate() + ' ' + String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

async function fetchNotifications(unreadOnly) {
  if (typeof apiFetch !== 'function') return { items: [], unreadCount: 0 };
  try {
    var resp = await apiFetch('/api/notifications?limit=50' + (unreadOnly ? '&unread_only=1' : ''));
    if (!resp) return { items: [], unreadCount: 0 };
    var data = await resp.json();
    if (!data || typeof data !== 'object' || !Array.isArray(data.items)) return { items: [], unreadCount: 0 };
    return data;
  } catch (e) {
    console.warn('[Notifications] 加载失败:', e);
    return { items: [], unreadCount: 0 };
  }
}
function addNotificationHistory(text, type) {
  // 推送一条通知到后端（不再写 localStorage）
  if (typeof apiFetch !== 'function') return;
  apiFetch('/api/notifications', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ type: type || 'message', title: text || '', content: text || '' })
  }).then(function () {
    renderNotificationHistory();
    refreshUnreadBadge();
  }).catch(function (e) {
    console.warn('[Notifications] 推送失败:', e);
  });
}
function toggleNotificationRead(id) {
  var item = _notificationHistoryCache.find(function (n) { return n.id === id; });
  if (!item || item.read) return;
  apiFetch('/api/notifications/' + encodeURIComponent(id) + '/read', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'}
  }).then(function () {
    renderNotificationHistory();
    // ★ fix/notification-panel-polish: 标记已读后同时刷新左侧 panel (修红点残留 bug)
    //   panel 和 history 共享 _notificationHistoryCache, 改 cache 后两边都该重渲染
    var panel = document.getElementById('notificationPanel');
    if (panel && panel.classList.contains('show')) {
      renderNotificationPanelBody();
    }
    refreshUnreadBadge();
  }).catch(function (e) {
    console.warn('[Notifications] 标记已读失败:', e);
  });
}
function markAllNotificationsRead() {
  apiFetch('/api/notifications/read-all', {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'}
  }).then(function () {
    renderNotificationHistory();
    // ★ fix/notification-panel-polish: 全部已读后同时刷新左侧 panel
    var panel = document.getElementById('notificationPanel');
    if (panel && panel.classList.contains('show')) {
      renderNotificationPanelBody();
    }
    refreshUnreadBadge();
  }).catch(function (e) {
    console.warn('[Notifications] 全部已读失败:', e);
  });
}
function deleteNotification(id) {
  apiFetch('/api/notifications/' + encodeURIComponent(id), {
    method: 'DELETE'
  }).then(function () {
    renderNotificationHistory();
    refreshUnreadBadge();
  }).catch(function (e) {
    console.warn('[Notifications] 删除失败:', e);
  });
}
async function renderNotificationHistory() {
  var area = document.getElementById('notificationHistoryArea');
  if (!area) return;
  var data = await fetchNotifications(false);
  _notificationHistoryCache = data.items || [];
  if (!_notificationHistoryCache.length) {
    area.innerHTML = renderEmptyState({
      icon: '🔔',
      title: '暂无通知',
      desc: '还没有收到任何通知,新事件到来时会显示在这里'
    });
    return;
  }
  var html = '<div style="margin-bottom:12px;text-align:right;"><button class="module-action-btn" onclick="markAllNotificationsRead()">全部已读</button></div>';
  html += _notificationHistoryCache.map(function (n) {
    // ★ fix/notification-panel-polish: 右侧历史页也用统一时间格式 helper
    var timeStr = formatNotificationTime(n.created_at);
    var text = n.title && n.content && n.title !== n.content ? (n.title + '：' + n.content) : (n.title || n.content || '');
    return '<div class="settings-notification-item" onclick="toggleNotificationRead(\'' + escapeAttr(n.id) + '\')">'
      + '<div class="settings-notification-dot ' + (n.read ? 'read' : 'unread') + '"></div>'
      + '<div class="settings-notification-content">'
      + '<div class="settings-notification-time">' + escapeHtml(timeStr) + '</div>'
      + '<div class="settings-notification-text">' + escapeHtml(text) + '</div>'
      + '</div>'
      + '<button class="module-action-btn danger" style="margin-left:8px;" onclick="event.stopPropagation();deleteNotification(\'' + escapeAttr(n.id) + '\')">删除</button>'
      + '</div>';
  }).join('');
  area.innerHTML = html;
}

// 未读角标轮询（每 30 秒）
function refreshUnreadBadge() {
  var badge = document.getElementById('notificationBadge');
  if (!badge || typeof apiFetch !== 'function') return;
  fetchNotifications(true).then(function (data) {
    var n = data.unreadCount || 0;
    // ⑤ ≥10 显 9+, 0 隐藏
    badge.textContent = n >= 10 ? '9+' : n;
    if (n === 0) badge.setAttribute('data-count', '0');
    else badge.removeAttribute('data-count');
  }).catch(function () {});
}
function initNotificationPolling() {
  refreshUnreadBadge();
  registerTimerTask(refreshUnreadBadge, 30);
}

// ⑤ 通知中心: 左侧 nav 铃铛 toggle
function toggleNotificationPanel() {
  var panel = document.getElementById('notificationPanel');
  if (!panel) return;
  var willShow = !panel.classList.contains('show');
  panel.classList.toggle('show');
  if (willShow) renderNotificationPanelBody();
}

// ⑤ 渲染通知面板列表(fetch 最新 20 条, 倒序)
// ★ fix/notification-panel-polish: 内容截断 (line-clamp 3 + 展开按钮) + 友好时间格式
function renderNotificationPanelBody() {
  var body = document.getElementById('notificationPanelBody');
  if (!body || typeof apiFetch === 'undefined') return;
  body.innerHTML = '<div class="notification-panel-empty"><div class="notification-panel-empty-icon">⏳</div><div>加载中...</div></div>';
  fetchNotifications(false).then(function (data) {
    var items = (data.items || []).slice(0, 20);
    if (!items.length) {
      body.innerHTML = '<div class="notification-panel-empty"><div class="notification-panel-empty-icon">🔕</div><div>暂无通知</div></div>';
      return;
    }
    body.innerHTML = items.map(function (n) {
      var t = n.type || 'message';
      var icon = (n.icon && n.icon.length) ? n.icon : _getNotificationTypeEmoji(t);
      var time = formatNotificationTime(n.created_at);
      var text = n.title && n.content && n.title !== n.content ? (n.title + '：' + n.content) : (n.title || n.content || '');
      // line-clamp 3 是 CSS 截断, 这里通过计算字符长度判断是否需要展开按钮
      // 阈值: 中文 70 字 / 英文 200 字符 = 约 3 行 @ 13px
      var needsToggle = text.length > 70;
      var safeId = escapeAttr(n.id);
      var safeText = escapeHtml(text);
      var toggleHtml = needsToggle
        ? '<span class="notification-panel-text-toggle" onclick="event.stopPropagation();toggleNotifTextExpand(this)">查看全部</span>'
        : '';
      return '<div class="notification-panel-item" data-type="' + escapeAttr(t) + '" onclick="toggleNotificationRead(\'' + safeId + '\')">' +
        '<div class="notification-panel-dot ' + (n.read ? 'read' : 'unread') + '"></div>' +
        '<div class="notification-panel-icon">' + icon + '</div>' +
        '<div class="notification-panel-content">' +
        '<div class="notification-panel-text" data-notif-text="' + safeId + '">' + safeText + '</div>' +
        toggleHtml +
        '<div class="notification-panel-time">' + escapeHtml(time) + '</div>' +
        '</div></div>';
    }).join('');
    // 拉完后刷新一下未读数(可能刚标记的变成已读)
    if (typeof refreshUnreadBadge === 'function') refreshUnreadBadge();
  }).catch(function () {
    body.innerHTML = '<div class="notification-panel-empty"><div class="notification-panel-empty-icon">⚠️</div><div>加载失败</div></div>';
  });
}
// 切换左侧面板 text 展开/收起
function toggleNotifTextExpand(toggleEl) {
  // 找到兄弟节点 .notification-panel-text (上一个 sibling)
  var textEl = toggleEl.previousElementSibling;
  if (!textEl || !textEl.classList.contains('notification-panel-text')) return;
  var expanded = textEl.classList.toggle('expanded');
  toggleEl.textContent = expanded ? '收起' : '查看全部';
}

// ⑤ 通知类型 → emoji(C3 也会复用, 提前放这里)
function _getNotificationTypeEmoji(t) {
  var map = { info: 'ℹ️', success: '✅', warning: '⚠️', error: '❌', urge: '⏰', message: '💬' };
  return map[t] || '💬';
}

// ⑤ 「查看全部」跳设置页通知历史
function viewAllNotifications() {
  var panel = document.getElementById('notificationPanel');
  if (panel) panel.classList.remove('show');
  if (typeof switchModule === 'function') switchModule('settings');
  setTimeout(function () {
    if (typeof renderNotificationHistory === 'function') renderNotificationHistory();
    var area = document.getElementById('notificationHistoryArea');
    if (area) area.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }, 200);
}

// ⑤ 通知面板 click outside 关闭(复用 chat-more-btn 的 click-outside 模式)
document.addEventListener('click', function (e) {
  var panel = document.getElementById('notificationPanel');
  if (!panel || !panel.classList.contains('show')) return;
  var bell = document.getElementById('navNotifications');
  if (e.target.closest && (e.target.closest('#notificationPanel') || e.target.closest('#navNotifications'))) return;
  panel.classList.remove('show');
});

// ========== 通知开关 ==========
async function loadNotificationSwitches() {
  var area = document.getElementById('notificationSwitchArea');
  if (!area) return;
  var s = { message_notify: 1, group_urge: 1, task_reminder: 1 };
  if (typeof apiFetch === 'function') {
    try {
      var resp = await apiFetch('/api/notification-settings');
      if (resp) {
        var data = await resp.json();
        if (data && typeof data === 'object') {
          ['message_notify', 'group_urge', 'task_reminder'].forEach(function (k) {
            if (k in data) s[k] = data[k] ? 1 : 0;
          });
        }
      }
    } catch (e) {
      console.warn('[Notifications] 开关加载失败:', e);
    }
  }
  var items = [
    { key: 'message_notify', label: '消息通知' },
    { key: 'group_urge', label: '群主督促' },
    { key: 'task_reminder', label: '任务提醒' }
  ];
  area.innerHTML = items.map(function (it) {
    return '<div class="settings-item"><span class="settings-item-label">' + it.label + '</span><div class="toggle' + (s[it.key] ? ' active' : '') + '" onclick="toggleNotificationSwitch(\'' + it.key + '\', this)"></div></div>';
  }).join('');
}
async function toggleNotificationSwitch(key, el) {
  var on = el.classList.toggle('active');
  if (typeof apiFetch !== 'function') return;
  var body = {};
  body[key] = on ? 1 : 0;
  try {
    await apiFetch('/api/notification-settings', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    showToast('✅ 通知设置已保存');
  } catch (e) {
    el.classList.toggle('active');
    showToast('❌ 通知设置保存失败');
  }
}

// ========== 账号设置 ==========
function applyAccountTheme(value) {
  // 接入现有主题机制（themeScheme + applyTheme）
  themeScheme = value === '深色' ? 'dark' : 'light';
  localStorage.setItem('sb_theme', themeScheme);
  applyTheme();
}
async function loadAccountSettings() {
  if (typeof apiFetch !== 'function') return;
  try {
    var resp = await apiFetch('/api/account');
    if (!resp) return;
    var data = await resp.json();
    if (!data || typeof data !== 'object') return;
    var nameEl = document.getElementById('settingsDisplayName');
    var emailEl = document.getElementById('settingsEmail');
    var themeEl = document.getElementById('settingsTheme');
    var langEl = document.getElementById('settingsLanguage');
    if (nameEl) nameEl.value = data.displayName || '';
    if (emailEl) emailEl.value = data.email || '';
    if (themeEl) themeEl.value = data.theme === 'dark' ? '深色' : '浅色';
    if (langEl) langEl.value = data.language || '中文';
  } catch (e) {
    console.warn('[Account] 加载账号设置失败:', e);
  }
}
async function saveAccountSettings() {
  if (typeof apiFetch !== 'function') return;
  var themeVal = (document.getElementById('settingsTheme') || {}).value || '浅色';
  try {
    var resp = await apiFetch('/api/account', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        displayName: (document.getElementById('settingsDisplayName') || {}).value || '',
        email: (document.getElementById('settingsEmail') || {}).value || '',
        theme: themeVal === '深色' ? 'dark' : 'light',
        language: (document.getElementById('settingsLanguage') || {}).value || '中文'
      })
    });
    if (!resp) return;
    showToast('✅ 账号设置已保存');
  } catch (e) {
    console.warn('[Account] 保存账号设置失败:', e);
    showToast('❌ 账号设置保存失败');
  }
}
// 修改自己的密码（子账号也可用）
async function changeOwnPassword() {
  if (!currentUser) return;
  var oldPwd = (document.getElementById('settingsOldPassword') || {}).value || '';
  var pwd = (document.getElementById('settingsNewPassword') || {}).value || '';
  var pwd2 = (document.getElementById('settingsNewPassword2') || {}).value || '';
  if (!oldPwd) { showToast('⚠️ 请输入旧密码'); return; }
  if (!pwd) { showToast('⚠️ 请输入新密码'); return; }
  if (pwd !== pwd2) { showToast('⚠️ 两次输入的密码不一致'); return; }
  try {
    var resp = await apiFetch('/api/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ oldPassword: oldPwd, newPassword: pwd })
    });
    if (!resp) return;
    var data = await resp.json();
    if (resp.ok) {
      showToast('✅ 密码已修改');
      var el0 = document.getElementById('settingsOldPassword');
      var el1 = document.getElementById('settingsNewPassword');
      var el2 = document.getElementById('settingsNewPassword2');
      if (el0) el0.value = '';
      if (el1) el1.value = '';
      if (el2) el2.value = '';
    } else {
      showToast('⚠️ ' + (data && data.error || '密码修改失败'));
    }
  } catch (e) {
    console.warn('[Account] 修改密码失败:', e);
    showToast('❌ 密码修改失败，请检查网络');
  }
}

// ========== 权限管理 ==========
var _permissionConfig = null;
var _permissionUsers = [];
var _permissionCategories = [];
var _availableModules = ['dashboard', 'messages', 'employees', 'groups', 'knowledge', 'products', 'influencers', 'matches', 'tasks', 'settings'];
var _permissionModuleLabels = {
  dashboard: '工作台', messages: '消息', employees: '员工', groups: '群组',
  knowledge: '知识库', products: '商品库', influencers: '达人库', matches: '匹配', tasks: '任务', settings: '设置'
};
var _selectedPermissionRole = 'admin';
var _selectedPermissionUser = '';
var _permissionUserState = null;

function _getPermissionRole(roleId) {
  return (_permissionConfig && (_permissionConfig.roleTemplates || []).find(function (r) { return r.id === roleId; })) || null;
}
function _getPermissionUser(userId) {
  return (_permissionUsers || []).find(function (u) { return u.id === userId; }) || null;
}
function _getUserBaseTemplate(user) {
  if (!user) return null;
  return _getPermissionRole(user.roleTemplateId) || _getPermissionRole(user.role) || null;
}
function _getDefaultRoleModules(roleId) {
  var allTrue = {};
  _availableModules.forEach(function (m) { allTrue[m] = true; });
  if (roleId === 'admin' || roleId === 'leader') return allTrue;
  var emp = {};
  _availableModules.forEach(function (m) { emp[m] = false; });
  emp.dashboard = true; emp.messages = true; emp.employees = true; emp.groups = true; emp.knowledge = true;
  return emp;
}

async function loadPermissionSettings() {
  var area = document.getElementById('permissionSettingsArea');
  if (!area) return;
  if (!isAdmin() || localStorage.getItem('sb_auth_token') === 'local_mode') {
    area.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">权限管理仅对登录的管理员开放</div>';
    return;
  }
  area.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  try {
    // ★ fix/permission-page-kb-entries: KB 分类改用 /api/knowledge/entries 新接口
    // 老 API /api/knowledge?limit=1000 拉老表 (knowledge), 生产 data 99 篇文档全在 kb_entries 新表,
    // 老表是空的 → _permissionCategories 永远空列表, "按分类筛选" 下拉空了
    // 复用主 KB 计数 (refactor/kb-counts) 的循环分页 pattern, pageSize=100, 5000 条封顶
    var [permsResp, usersResp, modResp] = await Promise.all([
      apiFetch('/api/permissions'),
      apiFetch('/api/users'),
      apiFetch('/api/permissions/modules')
    ]);
    // KB 分类: 串行循环分页拉 /api/knowledge/entries (admin 全量视角, 不带 projectId/scope 过滤)
    var pageSize = 100;
    var offset = 0;
    var allDocs = [];
    var total = 0;
    while (true) {
      var entriesUrl = '/api/knowledge/entries?limit=' + pageSize + '&offset=' + offset;
      var entriesResp = await apiFetch(entriesUrl);
      var entriesData = await entriesResp.json();
      var docs = entriesData.docs || [];
      allDocs = allDocs.concat(docs);
      total = entriesData.total || 0;
      if (offset === 0) {
        if (Math.ceil(total / pageSize) <= 1) break;
        offset += pageSize;
      } else {
        if (docs.length < pageSize) break;
        offset += pageSize;
      }
      if (offset / pageSize > 50) break;  // 5000 条封顶, 防止接口 bug 死循环
    }
    var perms = await permsResp.json();
    var users = await usersResp.json();
    var modData = await modResp.json();
    _permissionConfig = perms;
    _permissionUsers = Array.isArray(users) ? users : (users.users || []);
    if (modData && Array.isArray(modData.modules)) _availableModules = modData.modules;
    var cats = [];
    allDocs.forEach(function (d) {
      if (d.category && cats.indexOf(d.category) < 0) cats.push(d.category);
    });
    cats.sort();
    _permissionCategories = cats;
    if (!_getPermissionRole(_selectedPermissionRole)) {
      _selectedPermissionRole = (_permissionConfig.roleTemplates && _permissionConfig.roleTemplates[0] && _permissionConfig.roleTemplates[0].id) || 'admin';
    }
    _selectedPermissionUser = '';
    _permissionUserState = null;
    area.innerHTML = renderPermissionSettings();
  } catch (e) {
    console.error('loadPermissionSettings error', e);
    area.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败：' + escapeHtml(e.message) + '</div>';
  }
}

function renderPermissionSettings() {
  var role = _getPermissionRole(_selectedPermissionRole);
  if (!role) return '<div style="padding:20px;text-align:center;">无角色模板</div>';
  var html = '<div class="settings-card">';
  html += '<div class="settings-card-title">角色模板</div>';
  html += '<div class="settings-segmented">';
  (_permissionConfig.roleTemplates || []).forEach(function (r) {
    html += '<button class="settings-segmented-item ' + (_selectedPermissionRole === r.id ? 'active' : '') + '" onclick="selectPermissionRole(\'' + r.id + '\')">' + escapeHtml(r.name) + '</button>';
  });
  html += '</div>';
  html += '</div>';

  html += '<div class="settings-card">';
  html += '<div class="settings-card-title">模块权限</div>';
  _availableModules.forEach(function (m) {
    var label = _permissionModuleLabels[m] || m;
    var granted = role.modules[m];
    html += '<div class="settings-permission-row-card">';
    html += '<div style="font-size:14px;color:var(--text-primary);">' + escapeHtml(label) + '</div>';
    html += '<span class="settings-permission-tag ' + (granted ? 'granted' : 'denied') + '" onclick="togglePermissionModule(\'' + m + '\', this)">' + (granted ? '有权限' : '无权限') + '</span>';
    html += '</div>';
  });
  html += '</div>';

  html += '<div class="settings-card">';
  html += '<div class="settings-card-title">知识库分类</div>';
  var cats = role.knowledgeCategories || [];
  var allSelected = cats.indexOf('*') >= 0;
  html += '<label style="display:flex;align-items:center;gap:8px;margin-bottom:8px;cursor:pointer;font-size:13px;"><input type="checkbox" value="*" ' + (allSelected ? 'checked' : '') + ' onchange="toggleRoleCategory(\'*\', this.checked)"> 全部分类（*）</label>';
  if (_permissionCategories.length) {
    html += '<div style="border-top:0.5px solid var(--separator);padding-top:8px;display:flex;flex-wrap:wrap;gap:8px;">';
    _permissionCategories.forEach(function (cat) {
      var checked = allSelected || cats.indexOf(cat) >= 0 ? 'checked' : '';
      html += '<label style="display:flex;align-items:center;gap:4px;cursor:pointer;font-size:13px;"><input type="checkbox" value="' + escapeHtml(cat) + '" ' + checked + ' onchange="toggleRoleCategory(\'' + escapeHtml(cat).replace(/'/g, "\\'") + '\', this.checked)"> ' + escapeHtml(cat) + '</label>';
    });
    html += '</div>';
  } else {
    html += '<div style="font-size:12px;color:var(--text-tertiary);padding-top:8px;">暂无知识库分类，可在创建知识条目时添加。</div>';
  }
  html += '<div style="margin-top:16px;display:flex;gap:8px;"><button class="module-action-btn primary" onclick="savePermissionSettings()">保存角色权限</button><button class="module-action-btn" onclick="resetPermissionSettings()">恢复默认</button></div>';
  html += '</div>';

  html += '<div class="settings-card">';
  html += '<div class="settings-card-title">单独用户权限覆盖</div>';
  html += '<div class="settings-form-row"><label class="form-label">选择用户</label><select class="form-select" id="permissionUserSelect" onchange="selectPermissionUser(this.value)"><option value="">-- 选择用户 --</option>' + _permissionUsers.map(function (u) { return '<option value="' + u.id + '">' + escapeHtml(u.displayName || u.username) + '（' + escapeHtml(u.role) + '）</option>'; }).join('') + '</select></div>';
  html += '<div id="userPermissionMatrix"></div>';
  html += '</div>';
  return html;
}

function renderUserPermissionMatrix() {
  var container = document.getElementById('userPermissionMatrix');
  if (!container) return;
  if (!_selectedPermissionUser || !_permissionUserState) {
    container.innerHTML = '';
    return;
  }
  var state = _permissionUserState;
  // ui/user-mgmt-v21: V2.1 token 收口 + inline style → CSS class(去 var(--text-primary) / var(--text-tertiary) / var(--separator) 老 token 名)
  var html = '<div class="settings-card-subtitle settings-permission-subtitle">模块覆盖</div>';
  _availableModules.forEach(function (m) {
    var label = _permissionModuleLabels[m] || m;
    var granted = state.modules[m];
    html += '<div class="settings-permission-row-card">';
    html += '<div class="settings-permission-row-label">' + escapeHtml(label) + '</div>';
    html += '<span class="settings-permission-tag ' + (granted ? 'granted' : 'denied') + '" onclick="toggleUserPermissionModule(\'' + m + '\', this)">' + (granted ? '有权限' : '无权限') + '</span>';
    html += '</div>';
  });

  html += '<div class="settings-card-subtitle settings-permission-subtitle settings-permission-subtitle-kb">知识库分类覆盖</div>';
  var cats = state.knowledgeCategories || [];
  var allSelected = cats.indexOf('*') >= 0;
  html += '<label class="settings-permission-cat-all"><input type="checkbox" value="*" ' + (allSelected ? 'checked' : '') + ' onchange="toggleUserCategory(\'*\', this.checked)"> 全部分类（*）</label>';
  if (_permissionCategories.length) {
    html += '<div class="settings-permission-cat-list">';
    _permissionCategories.forEach(function (cat) {
      var checked = allSelected || cats.indexOf(cat) >= 0 ? 'checked' : '';
      html += '<label class="settings-permission-cat-item"><input type="checkbox" value="' + escapeHtml(cat) + '" ' + checked + ' onchange="toggleUserCategory(\'' + escapeHtml(cat).replace(/'/g, "\\'") + '\', this.checked)"> ' + escapeHtml(cat) + '</label>';
    });
    html += '</div>';
  } else {
    html += '<div class="settings-permission-cat-empty">暂无知识库分类。</div>';
  }

  html += '<div class="settings-permission-actions"><button class="module-action-btn primary" onclick="saveUserPermissions()">保存用户覆盖</button><button class="module-action-btn" onclick="resetUserPermissions()">清除覆盖</button></div>';
  container.innerHTML = html;
}

function selectPermissionRole(roleId) {
  _selectedPermissionRole = roleId;
  var area = document.getElementById('permissionSettingsArea');
  if (area) area.innerHTML = renderPermissionSettings();
}
function togglePermissionModule(moduleKey, el) {
  var role = _getPermissionRole(_selectedPermissionRole);
  if (!role) return;
  var val = !role.modules[moduleKey];
  role.modules[moduleKey] = val;
  el.className = 'settings-permission-tag ' + (val ? 'granted' : 'denied');
  el.textContent = val ? '有权限' : '无权限';
}
function toggleRoleCategory(cat, checked) {
  var role = _getPermissionRole(_selectedPermissionRole);
  if (!role) return;
  var cats = role.knowledgeCategories || [];
  if (cat === '*') {
    role.knowledgeCategories = checked ? ['*'] : [];
  } else {
    if (checked) {
      if (cats.indexOf(cat) < 0) {
        cats = cats.filter(function (c) { return c !== '*'; });
        cats.push(cat);
      }
    } else {
      cats = cats.filter(function (c) { return c !== cat; });
    }
    role.knowledgeCategories = cats;
  }
  var area = document.getElementById('permissionSettingsArea');
  if (area) area.innerHTML = renderPermissionSettings();
}
async function savePermissionSettings() {
  var role = _getPermissionRole(_selectedPermissionRole);
  if (!role) return;
  try {
    var resp = await apiFetch('/api/permissions/roles/' + role.id, {
      method: 'PUT',
      body: JSON.stringify({ modules: role.modules, knowledgeCategories: role.knowledgeCategories })
    });
    if (!resp || !resp.ok) throw new Error('保存失败');
    showToast('✅ 角色权限已保存');
  } catch (e) {
    showToast('❌ ' + e.message);
  }
}
function resetPermissionSettings() {
  if (!confirm('确定要恢复该角色的默认权限吗？')) return;
  var role = _getPermissionRole(_selectedPermissionRole);
  if (!role) return;
  role.modules = _getDefaultRoleModules(role.id);
  role.knowledgeCategories = role.id === 'employee' ? [] : ['*'];
  var area = document.getElementById('permissionSettingsArea');
  if (area) area.innerHTML = renderPermissionSettings();
  showToast('🔄 已恢复默认，请点击保存生效');
}
function selectPermissionUser(userId) {
  _selectedPermissionUser = userId;
  var user = _getPermissionUser(userId);
  var base = _getUserBaseTemplate(user);
  var override = (_permissionConfig && (_permissionConfig.userOverrides || {})[userId]) || {};
  _permissionUserState = { modules: {}, knowledgeCategories: [] };
  _availableModules.forEach(function (m) {
    var overrideVal = override.modules && typeof override.modules[m] === 'boolean' ? override.modules[m] : undefined;
    _permissionUserState.modules[m] = overrideVal !== undefined ? overrideVal : (base ? base.modules[m] : false);
  });
  _permissionUserState.knowledgeCategories = Array.isArray(override.knowledgeCategories) ? override.knowledgeCategories.slice() : (base ? (base.knowledgeCategories || []).slice() : []);
  renderUserPermissionMatrix();
}
function toggleUserPermissionModule(moduleKey, el) {
  if (!_permissionUserState) return;
  var val = !_permissionUserState.modules[moduleKey];
  _permissionUserState.modules[moduleKey] = val;
  el.className = 'settings-permission-tag ' + (val ? 'granted' : 'denied');
  el.textContent = val ? '有权限' : '无权限';
}
function toggleUserCategory(cat, checked) {
  if (!_permissionUserState) return;
  var cats = _permissionUserState.knowledgeCategories || [];
  if (cat === '*') {
    _permissionUserState.knowledgeCategories = checked ? ['*'] : [];
  } else {
    if (checked) {
      if (cats.indexOf(cat) < 0) {
        cats = cats.filter(function (c) { return c !== '*'; });
        cats.push(cat);
      }
    } else {
      cats = cats.filter(function (c) { return c !== cat; });
    }
    _permissionUserState.knowledgeCategories = cats;
  }
  renderUserPermissionMatrix();
}
async function saveUserPermissions() {
  if (!_selectedPermissionUser || !_permissionUserState) return;
  var body = {
    modules: _permissionUserState.modules,
    knowledgeCategories: _permissionUserState.knowledgeCategories
  };
  try {
    var resp = await apiFetch('/api/permissions/users/' + _selectedPermissionUser, {
      method: 'PUT',
      body: JSON.stringify(body)
    });
    if (!resp || !resp.ok) throw new Error('保存失败');
    if (!_permissionConfig.userOverrides) _permissionConfig.userOverrides = {};
    _permissionConfig.userOverrides[_selectedPermissionUser] = body;
    showToast('✅ 用户权限覆盖已保存');
  } catch (e) {
    showToast('❌ ' + e.message);
  }
}
async function resetUserPermissions() {
  if (!_selectedPermissionUser) return;
  if (!confirm('确定要清除该用户的权限覆盖吗？')) return;
  try {
    var resp = await apiFetch('/api/permissions/users/' + _selectedPermissionUser, {
      method: 'PUT',
      body: JSON.stringify({})
    });
    if (!resp || !resp.ok) throw new Error('清除失败');
    if (_permissionConfig.userOverrides && _permissionConfig.userOverrides[_selectedPermissionUser]) {
      delete _permissionConfig.userOverrides[_selectedPermissionUser];
    }
    _permissionUserState = null;
    renderUserPermissionMatrix();
    showToast('🔄 已清除用户权限覆盖');
  } catch (e) {
    showToast('❌ ' + e.message);
  }
}

// ========== 违禁词管理 ==========
var _forbiddenWordsCache = [];
async function fetchForbiddenWords(keyword) {
  if (typeof apiFetch !== 'function') return [];
  try {
    var url = '/api/forbidden-words' + (keyword ? '?keyword=' + encodeURIComponent(keyword) : '');
    var resp = await apiFetch(url);
    if (!resp) return [];
    var data = await resp.json();
    return (data && Array.isArray(data.items)) ? data.items : [];
  } catch (e) {
    console.warn('[ForbiddenWords] 加载失败:', e);
    return [];
  }
}
async function loadForbiddenWords() {
  var area = document.getElementById('forbiddenWordsArea');
  if (!area) return;
  area.innerHTML = renderForbiddenWordsUI();
  var items = await fetchForbiddenWords('');
  // 首次发现为空时批量导入默认违禁词（仅导入一次）
  if (!items.length && !localStorage.getItem('sb_forbidden_words_seeded')) {
    try {
      await apiFetch('/api/forbidden-words', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ words: ['色情', '暴力', '赌博', '毒品', '诈骗'] })
      });
      localStorage.setItem('sb_forbidden_words_seeded', '1');
      items = await fetchForbiddenWords('');
    } catch (e) {
      console.warn('[ForbiddenWords] 默认违禁词导入失败:', e);
    }
  }
  _forbiddenWordsCache = items;
  renderForbiddenList();
}
function renderForbiddenWordsUI() {
  var html = '<div class="settings-card">';
  html += '<div class="settings-card-title">违禁词列表</div>';
  html += '<div class="settings-list-searchbar" style="display:flex;gap:8px;">';
  html += '<input type="text" id="forbiddenSearchInput" class="form-input" placeholder="搜索违禁词..." oninput="filterForbiddenWords()">';
  html += '<button class="module-action-btn primary" onclick="showAddForbiddenWordModal()">➕ 添加</button>';
  html += '<button class="module-action-btn" onclick="showBatchForbiddenWordModal()">批量添加</button>';
  html += '</div>';
  html += '<div id="forbiddenList"></div>';
  html += '</div>';
  return html;
}
function renderForbiddenList() {
  var container = document.getElementById('forbiddenList');
  if (!container) return;
  var displayItems = _forbiddenWordsCache;
  if (displayItems.length === 0) {
    container.innerHTML = '<div class="settings-empty"><div style="font-size:32px;margin-bottom:12px">🚫</div><div>暂无违禁词</div></div>';
    return;
  }
  container.innerHTML = displayItems.map(function (item) {
    return '<div class="settings-list-row"><span>' + escapeHtml(item.word) + '</span><button class="module-action-btn danger" onclick="deleteForbiddenWord(\'' + escapeAttr(item.id) + '\')">删除</button></div>';
  }).join('');
}
async function filterForbiddenWords() {
  var searchInput = document.getElementById('forbiddenSearchInput');
  var search = searchInput ? searchInput.value.trim() : '';
  _forbiddenWordsCache = await fetchForbiddenWords(search);
  renderForbiddenList();
}
function showAddForbiddenWordModal() {
  var word = prompt('请输入要添加的违禁词：');
  if (word && word.trim()) {
    apiFetch('/api/forbidden-words', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ word: word.trim() })
    }).then(function (r) { return r ? r.json() : null; }).then(function (data) {
      if (data && data.added === 0) {
        showToast('⚠️ 该违禁词已存在');
        return;
      }
      filterForbiddenWords();
      showToast('✅ 已添加违禁词');
    }).catch(function (e) {
      console.warn('[ForbiddenWords] 添加失败:', e);
      showToast('❌ 添加违禁词失败');
    });
  }
}
function showBatchForbiddenWordModal() {
  var text = prompt('请批量输入违禁词，每行一个：');
  if (!text) return;
  var newWords = text.split(/\n/).map(function (s) { return s.trim(); }).filter(function (s) { return s; });
  if (!newWords.length) return;
  apiFetch('/api/forbidden-words', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ words: newWords })
  }).then(function (r) { return r ? r.json() : null; }).then(function (data) {
    filterForbiddenWords();
    showToast('✅ 批量添加 ' + (data && typeof data.added === 'number' ? data.added : 0) + ' 个违禁词');
  }).catch(function (e) {
    console.warn('[ForbiddenWords] 批量添加失败:', e);
    showToast('❌ 批量添加失败');
  });
}
function deleteForbiddenWord(id) {
  if (!confirm('确定要删除该违禁词吗？')) return;
  apiFetch('/api/forbidden-words/' + encodeURIComponent(id), {
    method: 'DELETE'
  }).then(function () {
    filterForbiddenWords();
    showToast('🗑️ 已删除违禁词');
  }).catch(function (e) {
    console.warn('[ForbiddenWords] 删除失败:', e);
    showToast('❌ 删除违禁词失败');
  });
}

var _settingsSelectedCategory = 'compute';

function renderSettingsMid() {
  var list = document.getElementById('settingsMidList');
  if (!list) return;
  var categories = [
    { id: 'compute', icon: '<path d="M18 20V10"/><path d="M12 20V4"/><path d="M6 20v-6"/>', title: isAdmin() ? '积分仪表盘' : '消耗统计' },
    { id: 'notification', icon: '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>', title: '通知' },
    { id: 'account', icon: '<path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>', title: '账号' },
    { id: 'permission', icon: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>', title: '权限管理' },
    { id: 'forbidden', icon: '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>', title: '违禁词管理' },
    { id: 'users', icon: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 7.744"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/>', title: '用户管理' },
    { id: 'teams', icon: '<path d="M12 10h.01"/><path d="M12 14h.01"/><path d="M12 6h.01"/><path d="M16 10h.01"/><path d="M16 14h.01"/><path d="M16 6h.01"/><path d="M8 10h.01"/><path d="M8 14h.01"/><path d="M8 6h.01"/><path d="M9 22v-3a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/><rect x="4" y="2" width="16" height="20" rx="2"/>', title: '团队管理' },
    { id: 'members', icon: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>', title: '成员管理' },
    { id: 'feishu', icon: '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>', title: '飞书配置' }
  ];
  if (!isAdmin()) {
    var allowed = ['compute','notification','account','feishu'];
    if (hasModulePermission('employees')) {
      allowed = allowed.concat(['users','members']);
    }
    categories = categories.filter(function(c) { return allowed.indexOf(c.id) >= 0; });
  }
  // 非管理员停留在无权限的分类（权限/违禁词/团队管理，及无 employees 权限时的用户/成员管理）时，回退到默认分类
  if (!isAdmin()) {
    var blockedCats = ['permission', 'forbidden', 'teams'];
    if (!hasModulePermission('employees')) blockedCats = blockedCats.concat(['users', 'members']);
    if (blockedCats.indexOf(_settingsSelectedCategory) >= 0) {
      _settingsSelectedCategory = 'compute';
    }
  }
  var html = '';
  categories.forEach(function (c) {
    var isActive = _settingsSelectedCategory === c.id;
    html += '<div class="list-item' + (isActive ? ' active' : '') + '" onclick="selectSettingsCategory(\'' + c.id + '\')">';
    html += '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + c.icon + '</svg>';
    html += '<div class="emp-info"><div class="emp-name">' + escapeHtml(c.title) + '</div></div>';
    html += '</div>';
  });
  list.innerHTML = html;
  renderSettingsRight();
}

function selectSettingsCategory(id) {
  // 权限/违禁词/团队管理仅管理员可见；用户/成员管理需要 employees 模块权限
  var adminOnlyCats = ['permission','forbidden','teams'];
  var empCats = ['users','members'];
  if (!isAdmin()) {
    if (adminOnlyCats.indexOf(id) >= 0) return;
    if (empCats.indexOf(id) >= 0 && !hasModulePermission('employees')) return;
  }
  _settingsSelectedCategory = id;
  renderSettingsMid();
}

function filterSettingsList(value) {
  var items = document.querySelectorAll('#settingsMidList .list-item');
  var lower = (value || '').toLowerCase();
  items.forEach(function (item) {
    var text = item.textContent || '';
    item.style.display = text.toLowerCase().indexOf(lower) >= 0 ? 'flex' : 'none';
  });
}

function renderSettingsRight() {
  var titleEl = document.getElementById('settingsRightTitle');
  var contentEl = document.getElementById('settingsRightContent');
  var actionsEl = document.getElementById('settingsRightActions');
  if (titleEl) titleEl.textContent = {
    compute: isAdmin() ? '积分仪表盘' : '消耗统计', notification: '通知', account: '账号', permission: '权限管理', forbidden: '违禁词管理', users: '用户管理', teams: '团队管理', members: '成员管理', feishu: '飞书配置'
  }[_settingsSelectedCategory] || '设置';
  if (actionsEl) actionsEl.innerHTML = '';
  if (_settingsSelectedCategory === 'compute' && actionsEl && isAdmin()) {
    actionsEl.innerHTML = '<button class="module-action-btn" onclick="syncTokenUsage()">🔄 同步 Token 数据</button>';
  }
  if (!contentEl) return;
  var html = '';
  if (_settingsSelectedCategory === 'compute') {
    html += '<div id="computeStatsArea"></div>';
  } else if (_settingsSelectedCategory === 'notification') {
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">通知开关</div>';
    html += '<div id="notificationSwitchArea"></div>';
    html += '</div>';
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">通知历史</div>';
    html += '<div id="notificationHistoryArea"></div>';
    html += '</div>';
  } else if (_settingsSelectedCategory === 'account') {
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">账号信息</div>';
    html += '<div class="settings-form-row"><label class="form-label">显示名称</label><input type="text" id="settingsDisplayName" class="form-input" placeholder="输入显示名称"></div>';
    html += '<div class="settings-form-row"><label class="form-label">邮箱</label><input type="email" id="settingsEmail" class="form-input" placeholder="输入邮箱"></div>';
    html += '</div>';
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">修改密码</div>';
    html += '<div class="settings-form-row"><label class="form-label">旧密码</label><input type="password" id="settingsOldPassword" class="form-input" placeholder="输入当前密码"></div>';
    html += '<div class="settings-form-row"><label class="form-label">新密码</label><input type="password" id="settingsNewPassword" class="form-input" placeholder="输入新密码"></div>';
    html += '<div class="settings-form-row"><label class="form-label">确认新密码</label><input type="password" id="settingsNewPassword2" class="form-input" placeholder="再次输入新密码"></div>';
    html += '<div style="display:flex;justify-content:flex-end;"><button class="module-action-btn primary" onclick="changeOwnPassword()">修改密码</button></div>';
    html += '</div>';
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">系统偏好</div>';
    html += '<div class="settings-form-row"><label class="form-label">主题</label><select class="form-select" id="settingsTheme" onchange="applyAccountTheme(this.value)"><option>浅色</option><option>深色</option></select></div>';
    html += '<div class="settings-form-row"><label class="form-label">语言</label><select class="form-select" id="settingsLanguage"><option>中文</option><option>English</option></select></div>';
    html += '</div>';
    html += '<div class="settings-card" style="display:flex;justify-content:flex-end;gap:8px;">';
    html += '<button class="module-action-btn primary" onclick="saveAccountSettings()">保存</button>';
    html += '<button class="module-action-btn" onclick="showUserProfile()">查看个人信息</button>';
    html += '</div>';
  } else if (_settingsSelectedCategory === 'permission') {
    html += '<div id="permissionSettingsArea"></div>';
  } else if (_settingsSelectedCategory === 'forbidden') {
    html += '<div id="forbiddenWordsArea"></div>';
  } else if (_settingsSelectedCategory === 'users') {
    if (actionsEl) actionsEl.innerHTML = '<button class="module-action-btn primary" onclick="showCreateUserModal()">添加用户</button>';
    html += '<div class="settings-card">';
    html += '<div class="settings-list-searchbar"><input type="text" id="userSearchInput" class="form-input" placeholder="搜索用户名字或 @ID..." oninput="filterUserList()"></div>';
    html += '<div id="userList"></div>';
    html += '</div>';
  } else if (_settingsSelectedCategory === 'teams') {
    if (actionsEl) actionsEl.innerHTML = '<button class="module-action-btn primary" onclick="showCreateTeamModal()">新建小组</button>';
    html += '<div class="settings-card">';
    html += '<div id="teamList"></div>';
    html += '</div>';
  } else if (_settingsSelectedCategory === 'members') {
    if (actionsEl) actionsEl.innerHTML = '<button class="module-action-btn primary" onclick="openWizard()" style="border-radius:8px;">添加成员</button>';
    html += '<div class="settings-card">';
    html += '<div class="settings-member-filter" id="settingsMemberFilter"><button class="active" data-filter="all" onclick="setMemberFilter(\'all\')">全部</button><button data-filter="active" onclick="setMemberFilter(\'active\')">正常</button><button data-filter="archived" onclick="setMemberFilter(\'archived\')">已归档</button></div>';
    html += '<div class="settings-list-searchbar"><input type="text" id="memberSearchInput" class="form-input" placeholder="搜索成员名字..." oninput="filterSettingsMemberList()"></div>';
    html += '<div id="settingsMemberList"></div>';
    html += '</div>';
  } else if (_settingsSelectedCategory === 'feishu') {
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">飞书多维表格绑定</div>';
    html += '<div class="settings-form-row"><label class="form-label">App ID</label><input type="text" id="feishuAppId" class="form-input" placeholder="飞书应用 App ID (cli_xxx)"></div>';
    html += '<div class="settings-form-row"><label class="form-label">App Secret</label><input type="text" id="feishuAppSecret" class="form-input" placeholder="飞书应用 App Secret"></div>';
    html += '<div class="settings-form-row"><label class="form-label">多维表格 Token</label><input type="text" id="feishuAppToken" class="form-input" placeholder="多维表格 App Token"></div>';
    html += '<div class="settings-form-row"><label class="form-label">表格 Table ID（达人表）</label><input type="text" id="feishuTableId" class="form-input" placeholder="达人表 ID (tbl_xxx)"></div>';
    html += '<div class="settings-form-row"><label class="form-label">商品表 Table ID</label><input type="text" id="feishuProductTableId" class="form-input" placeholder="商品表 ID (tbl_xxx)，留空则用达人表"></div>';
    html += '<div style="display:flex;justify-content:flex-end;gap:8px;margin-top:12px;">';
    html += '<button class="module-action-btn primary" onclick="saveFeishuConfig()">保存配置</button>';
    html += '<button class="module-action-btn" onclick="syncFeishuTalentsFromSettings()">🔄 同步达人</button>';
    html += '<button class="module-action-btn" onclick="syncFeishuProductsFromSettings()">📦 同步商品</button>';
    html += '</div>';
    html += '</div>';
    html += '<div class="settings-card">';
    html += '<div class="settings-card-title">使用说明</div>';
    html += '<div style="font-size:13px;color:var(--text-secondary);line-height:1.8;">';
    html += '<p>1. 在飞书创建多维表格，分别建达人表和商品表</p>';
    html += '<p>2. 创建飞书应用并获取 App ID 和 App Secret</p>';
    html += '<p>3. 多维表格 Token 从 URL 获取，达人表和商品表各自有 Table ID</p>';
    html += '<p>4. 保存后点"同步达人"或"同步商品"拉取飞书表格数据</p>';
    html += '<p>5. 商品表字段：商品名称、品牌、类目、价格、原价、佣金率、商品描述、SKU规格、标签、月销量、月GMV、转化率、佣金金额、合作达人数、视频数、直播数、渠道分布、购买性别、购买年龄、购买地区、购买人群、带货视频案例</p>';
    html += '</div>';
    html += '</div>';
  }
  contentEl.innerHTML = html;
  if (_settingsSelectedCategory === 'compute') loadComputeStats();
  if (_settingsSelectedCategory === 'notification') { loadNotificationSwitches(); renderNotificationHistory(); }
  if (_settingsSelectedCategory === 'account') loadAccountSettings();
  if (_settingsSelectedCategory === 'permission') loadPermissionSettings();
  if (_settingsSelectedCategory === 'forbidden') loadForbiddenWords();
  if (_settingsSelectedCategory === 'users') loadUserList();
  if (_settingsSelectedCategory === 'teams') loadTeamList();
  if (_settingsSelectedCategory === 'members') renderSettingsMemberList();
  if (_settingsSelectedCategory === 'feishu') loadFeishuConfig();
}

function loadFeishuConfig() {
  var token = localStorage.getItem('sb_auth_token');
  fetch('/api/user/feishu-config', { headers: { 'Authorization': 'Bearer ' + token } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.configured) {
        var el1 = document.getElementById('feishuAppId'); if (el1) el1.value = data.app_id || '';
        var el2 = document.getElementById('feishuAppToken'); if (el2) el2.value = data.app_token || '';
        var el3 = document.getElementById('feishuTableId'); if (el3) el3.value = data.table_id || '';
        var el4 = document.getElementById('feishuProductTableId'); if (el4) el4.value = data.product_table_id || '';
      }
    })
    .catch(function(e) { console.error('load feishu config failed:', e); });
}

function saveFeishuConfig() {
  var token = localStorage.getItem('sb_auth_token');
  var body = JSON.stringify({
    app_id: (document.getElementById('feishuAppId').value || '').trim(),
    app_secret: (document.getElementById('feishuAppSecret').value || '').trim(),
    app_token: (document.getElementById('feishuAppToken').value || '').trim(),
    table_id: (document.getElementById('feishuTableId').value || '').trim(),
    product_table_id: ((document.getElementById('feishuProductTableId') || {}).value || '').trim()
  });
  fetch('/api/user/feishu-config', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token }, body: body })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) { showToast('飞书配置已保存', 'success'); }
      else { showToast('保存失败: ' + (data.error || '未知错误'), 'error'); }
    })
    .catch(function(e) { showToast('网络错误: ' + e, 'error'); });
}

function syncFeishuTalentsFromSettings() {
  var token = localStorage.getItem('sb_auth_token');
  fetch('/api/talents/sync-feishu', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token } })
    .then(function(r) { return r.json(); })
    .then(function(data) {
      if (data.success) {
        showToast('同步完成：共' + data.total + '条，新增' + data.created + '条，更新' + data.updated + '条，跳过' + data.skipped + '条', 'success');
      } else {
        showToast('同步失败: ' + (data.error || '未知错误'), 'error');
      }
    })
    .catch(function(e) { showToast('网络错误: ' + e, 'error'); });
}

function toggleMidSidebar() {
  document.querySelector('.app-sidebar').classList.toggle('open');
}

var _settingsMemberFilter = 'all';
function setMemberFilter(filter) {
  _settingsMemberFilter = filter || 'all';
  var tabs = document.querySelectorAll('#settingsMemberFilter button');
  tabs.forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.filter === _settingsMemberFilter);
  });
  renderSettingsMemberList();
}

function renderSettingsMemberList() {
  var container = document.getElementById('settingsMemberList');
  if (!container) return;
  var filter = _settingsMemberFilter || 'all';
  var list = (emps || []).filter(function (e) {
    if (!e || !e.id || _isDefaultEmp(e)) return false;
    var archived = isArchivedEmp(e);
    if (filter === 'active') return !archived;
    if (filter === 'archived') return archived;
    return true;
  });
  list.sort(function (a, b) {
    var aArchived = isArchivedEmp(a) ? 1 : 0;
    var bArchived = isArchivedEmp(b) ? 1 : 0;
    if (aArchived !== bArchived) return aArchived - bArchived;
    return (b.updatedAt || 0) - (a.updatedAt || 0);
  });
  if (list.length === 0) {
    container.innerHTML = '<div class="settings-empty">' + (filter === 'archived' ? '暂无已归档成员' : filter === 'active' ? '暂无正常成员' : '暂无成员，点击右上角添加') + '</div>';
    return;
  }
  var html = '';
  list.forEach(function (emp) {
    var archived = isArchivedEmp(emp);
    var name = escapeHtml(emp.name || emp.displayName || '未命名');
    var roleLabel = escapeHtml(getEmpRoleDisplay(emp) || 'AI员工');
    var provider = escapeHtml(emp.aiProvider || emp.apiProvider || '');
    html += '<div class="settings-user-card settings-member-row' + (archived ? ' archived' : '') + '" data-archived="' + (archived ? '1' : '0') + '" onclick="openEmpDetail(\'' + escapeAttr(emp.id || '') + '\')" style="cursor:pointer;">';
    html += '<div class="settings-user-avatar" style="background:' + escapeAttr(emp.color || emp.bg || '#1677ff') + ';">' + renderAvatar(emp, 40) + '</div>';
    html += '<div class="settings-user-info">';
    html += '<div class="settings-user-name">' + name + (archived ? '<span class="settings-member-archived-tag">已归档</span>' : '') + '</div>';
    html += '<div class="settings-user-meta"><span>' + roleLabel + '</span>' + (provider ? '<span>·</span><span>' + provider + '</span>' : '') + '</div>';
    html += '</div>';
    html += '<div class="settings-user-actions">';
    if (archived) {
      html += '<button onclick="event.stopPropagation();restoreSettingsMember(\'' + escapeAttr(emp.id || '') + '\')">恢复</button>';
      html += '<button class="danger" onclick="event.stopPropagation();permanentlyDeleteSettingsMember(\'' + escapeAttr(emp.id || '') + '\')">彻底删除</button>';
    } else {
      html += '<button onclick="event.stopPropagation();openEmpDetail(\'' + escapeAttr(emp.id || '') + '\')">查看</button>';
    }
    html += '</div>';
    html += '</div>';
  });
  container.innerHTML = html;
  filterSettingsMemberList();
}

function filterSettingsMemberList() {
  var input = document.getElementById('memberSearchInput');
  var keyword = (input ? input.value : '').toLowerCase();
  var rows = document.querySelectorAll('.settings-member-row');
  rows.forEach(function (row) {
    var text = row.textContent || '';
    row.style.display = text.toLowerCase().indexOf(keyword) >= 0 ? 'flex' : 'none';
  });
}

function restoreSettingsMember(empId) {
  var emp = emps.find(function (e) { return e.id === empId; });
  if (!emp) return;
  unarchiveEmp(emp);
  saveEmployees();
  if (typeof syncEmpToServer === 'function') {
    syncEmpToServer(emp).catch(function (e) {});
  }
  renderSettingsMemberList();
  renderEmployeeList();
  showToast('📂 已恢复 ' + emp.name);
}

function permanentlyDeleteSettingsMember(empId) {
  var emp = emps.find(function (e) { return e.id === empId; });
  if (!emp) return;
  if (!confirm('确定要彻底删除 ' + (emp.name || '该成员') + '？此操作不可恢复。')) return;
  function _removeLocal() {
    var idx = emps.findIndex(function (e) { return e.id === empId; });
    if (idx >= 0) emps.splice(idx, 1);
    if (currentEmpId === empId && typeof closeEmpDetail === 'function') closeEmpDetail();
    if (localStorage.getItem('sb_current_emp') === empId) {
      localStorage.removeItem('sb_current_emp');
    }
    renderSettingsMemberList();
    renderEmployeeList();
    showToast('🗑️ 已彻底删除');
  }
  if (typeof apiFetch === 'function') {
    apiFetch('/api/agents/' + empId + '?permanent=true', { method: 'DELETE' }).then(function (resp) {
      if (resp && resp.ok) {
        _removeLocal();
      } else {
        showToast('❌ 删除失败');
      }
    }).catch(function () {
      showToast('❌ 删除失败');
    });
  } else {
    _removeLocal();
  }
}


