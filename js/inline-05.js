/* ===== index.html 内联块 5 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: function showToast(msg, type) { */
function showToast(msg, type) {
  type = type || 'info';
  var container = document.getElementById('toastContainer');
  if (!container) return;
  var toast = document.createElement('div');
  toast.className = 'toast toast-' + type;
  toast.textContent = msg;
  container.appendChild(toast);
  requestAnimationFrame(function () {
    toast.classList.add('show');
  });
  setTimeout(function () {
    toast.classList.remove('show');
    setTimeout(function () {
      toast.remove();
    }, 300);
  }, 2500);
}

// ui/global-states-unify: 全局 empty state 渲染 (V2.1 token)
// 用法: renderEmptyState({ icon: '🔍', title: '...', desc: '...', cta: {label, onclick}, tone: 'success|warning|danger' })
function renderEmptyState(opts) {
  opts = opts || {};
  var icon = opts.icon || '📭';
  var iconHtml = opts.iconHtml || ''; // ui/user-mgmt-v21: V2.1 设计系统 SVG icon 通道 (优先于 emoji icon)
  var title = opts.title || '暂无数据';
  var desc = opts.desc || '';
  var tone = opts.tone || '';
  var toneCls = tone ? ' ' + tone : '';
  var cta = opts.cta;
  var html = '<div class="empty-state-v21">';
  if (iconHtml) {
    html += '<div class="empty-state-v21-icon' + toneCls + '">' + iconHtml + '</div>';
  } else {
    html += '<div class="empty-state-v21-icon' + toneCls + '">' + escapeHtml(icon) + '</div>';
  }
  html += '<div class="empty-state-v21-title">' + escapeHtml(title) + '</div>';
  if (desc) html += '<div class="empty-state-v21-desc">' + escapeHtml(desc) + '</div>';
  if (cta && cta.label) {
    var ghostCls = cta.ghost ? ' ghost' : '';
    var onclickAttr = cta.onclick ? ' onclick="' + escapeAttr(String(cta.onclick)) + '"' : '';
    html += '<button class="empty-state-v21-cta' + ghostCls + '"' + onclickAttr + '>' + escapeHtml(cta.label) + '</button>';
  }
  html += '</div>';
  return html;
}

// ui/global-states-unify: 全局 skeleton loading 渲染 (V2.1 token)
// 用法: renderSkeleton('list', 5)  /  renderSkeleton('card', 3)  /  renderSkeleton('detail')
// 类型: 'list' (5 行行内列表) | 'card' (3 个卡片) | 'detail' (1 个 detail panel) | 'text' (3 行)
function renderSkeleton(type, count) {
  type = type || 'list';
  count = count || (type === 'card' ? 3 : 3);
  var lines = '';
  if (type === 'list') {
    for (var i = 0; i < count; i++) {
      lines += '<div class="skeleton-v21-list-item">'
        + '<div class="skeleton-v21 avatar-sm"></div>'
        + '<div style="flex:1;min-width:0;">'
        + '<div class="skeleton-v21 line line-sm"></div>'
        + '<div class="skeleton-v21 line" style="width:40%;margin-top:6px;"></div>'
        + '</div></div>';
    }
    return '<div class="skeleton-v21-list">' + lines + '</div>';
  }
  if (type === 'card') {
    for (var j = 0; j < count; j++) {
      lines += '<div class="skeleton-v21 card" style="margin-bottom:10px;"></div>';
    }
    return lines;
  }
  if (type === 'detail') {
    return '<div class="skeleton-v21 avatar" style="margin-bottom:14px;"></div>'
      + '<div class="skeleton-v21 title"></div>'
      + '<div class="skeleton-v21 line line-lg"></div>'
      + '<div class="skeleton-v21 line"></div>'
      + '<div class="skeleton-v21 line" style="width:80%;"></div>'
      + '<div class="skeleton-v21 line line-sm" style="width:50%;"></div>';
  }
  // 'text' 默认
  for (var k = 0; k < count; k++) {
    lines += '<div class="skeleton-v21 line" style="width:' + (100 - k * 20) + '%;"></div>';
  }
  return lines;
}

// 灵魂配置 Enter 键支持
document.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && document.getElementById('soulQuestionArea') && document.getElementById('soulQuestionArea').style.display !== 'none' && document.activeElement && document.activeElement.id === 'soulAnswer') {
    answerSoulQuestion();
  }
});