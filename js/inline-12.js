/* ===== index.html 内联块 12 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: /* ========================================================= */

/* ============================================================
 * Wave 1 P1b: 聊天提议卡片 (前端) — 独立 inline script 块
 * 设计稿: docs/SB2-wave1-p1b-chat-card-spec.md
 * 挂点: sb2_renderBubble (line 45703) 同步步 + sb2_renderMessages / sb2_appendBubble 异步 mount
 * 状态机: pending / approved / rejected / expired / executed / failed (6 态, 跟 P1a spec §2.2 对齐)
 * 红线: 0 hex / snake_case / 跨脚本块挂 window (SEV1) / 注释不用 ASCII 块注释符号
 * apply when: 任何「消息 token → 卡片子组件」类 UI, 卡片作为 bubble 子组件渲染
 * ============================================================ */

// ----- 小工具 -----
function sb2PropEsc(s){
  return String(s == null ? '' : s).replace(/[&<>"']/g, function(c){
    return ({'&':'&','<':'<','>':'>','"':'"',"'":'&#39;'})[c];
  });
}
function sb2PropStateLabel(s){
  return ({
    pending:'待拍板', approved:'已采纳', rejected:'已驳回',
    expired:'已过期', executed:'已执行', failed:'执行失败'
  })[s] || s || '-';
}
function sb2PropToast(msg, kind){
  if (typeof showToast === 'function') showToast(msg, kind || 'info');
}

// ★ 同步步: 在 sb2_renderBubble 内调, 拼占位 div HTML
// apply when: 任何「消息 token → 卡片占位」同步步
function sb2PropRenderPlaceholder(proposalId){
  return '<div class="sb2-prop-card loading" data-proposal-id="' + sb2PropEsc(proposalId) + '" data-state="loading">加载提议卡片…</div>';
}

// ★ 异步入: sb2_renderMessages / sb2_appendBubble 末尾调
//   扫 [data-state="loading"] 占位 → GET /api/proposals/:id → 按 status 渲染
//   apply when: 任何「卡片 mount 异步拉详情」管线
window.sb2PropMountCards = async function(container){
  if (!container) return;
  var placeholders = container.querySelectorAll('.sb2-prop-card[data-state="loading"]');
  if (!placeholders.length) return;
  // 并发拉所有占位的详情 (各自独立失败, 不阻断其它卡片)
  var tasks = [];
  for (var i = 0; i < placeholders.length; i++) {
    (function(el){
      var proposalId = el.getAttribute('data-proposal-id');
      if (!proposalId) return;
      tasks.push(
        apiFetch('/api/proposals/' + encodeURIComponent(proposalId))
          .then(function(resp){
            if (!resp || !resp.ok) throw new Error('HTTP ' + (resp ? resp.status : 'no resp'));
            return resp.json();
          })
          .then(function(prop){
            sb2PropRenderState(el, prop);
          })
          .catch(function(){
            // 失败显「加载失败 重试」按钮 (spec §1, 不静默)
            el.setAttribute('data-state', 'error');
            el.classList.remove('loading');
            el.innerHTML = '<div class="sb2-prop-card-error">加载失败 <button type="button" class="sb2-prop-card-retry" onclick="window.sb2PropRetry(\'' + proposalId + '\', this)">重试</button></div>';
          })
      );
    })(placeholders[i]);
  }
  await Promise.all(tasks);
};

// ★ 重试: 点击重试按钮 → 重置 loading → 重 mount
window.sb2PropRetry = function(proposalId, btn){
  var card = btn && btn.closest ? btn.closest('.sb2-prop-card') : null;
  if (!card) return;
  card.setAttribute('data-state', 'loading');
  card.classList.add('loading');
  card.setAttribute('data-proposal-id', proposalId);
  card.innerHTML = '加载提议卡片…';
  // 重 mount
  var container = card.closest('#sb2ChatMessages') || document;
  if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(container);
};

// ★ 状态机 6 态渲染 (核心)
//   pending → 标题 + summary + options 按钮组 (★ AI 推荐) + 驳回 ghost
//   approved → "已采纳, 执行中..." (中间态, UI 显示执行器在跑)
//   executed → ✓ + exec_result 摘要 (任务 / 达人 / 消息)
//   rejected → ✗ + resolution_note
//   expired → ⏰ 置灰
//   failed → ⚠ + resolution_note
function sb2PropRenderState(el, prop){
  if (!el || !prop) return;
  var status = prop.status || 'pending';
  el.setAttribute('data-state', status);
  el.classList.remove('loading');
  // 已知状态加 class, 否则用空 (中性)
  if (['pending','approved','rejected','expired','executed','failed'].indexOf(status) === -1) {
    status = 'pending';
  }
  // ★ P1b B2 fix: 切态前先清掉 6 状态 class, 否则残留 (e.g. sb2-prop-card pending executed 错位)
  //   之前: 只 add 不 remove → 老大走查实证「sb2-prop-card pending executed」className 错位
  //   现在: 6 状态全 remove 再 add, 切态干净
  el.classList.remove('pending','approved','rejected','expired','executed','failed');
  el.classList.add(status);

  var title = sb2PropEsc(prop.title || '(无标题)');
  var summary = sb2PropEsc(prop.summary || '');
  var agentName = sb2PropEsc(prop.agent_name || '');
  var resolutionNote = sb2PropEsc(prop.resolution_note || '');
  var execResult = prop.exec_result;
  if (typeof execResult === 'string') {
    try { execResult = JSON.parse(execResult); } catch (e) { execResult = null; }
  }

  var html = '';
  html += '<div class="sb2-prop-card-head">';
  html += '<span class="sb2-prop-card-state ' + sb2PropEsc(status) + '">' + sb2PropEsc(sb2PropStateLabel(status)) + '</span>';
  if (agentName) html += '<span style="font-size:11px;color:var(--sb2-t3);">' + agentName + '</span>';
  html += '</div>';
  html += '<div class="sb2-prop-card-title">' + title + '</div>';
  if (summary && status === 'pending') html += '<div class="sb2-prop-card-summary">' + summary + '</div>';

  if (status === 'pending') {
    // 选项按钮组 (★ AI 推荐)
    var options = Array.isArray(prop.options) ? prop.options : [];
    var recommended = parseInt(prop.recommended || 0, 10) || 0;
    if (options.length > 0) {
      html += '<div class="sb2-prop-card-actions">';
      for (var i = 0; i < options.length; i++) {
        var opt = options[i] || {};
        var isRec = (i === recommended);
        var label = sb2PropEsc(opt.label || ('选项 ' + (i + 1)));
        var rationale = opt.rationale ? '<div class="sb2-prop-card-summary" style="margin-top:4px;">' + sb2PropEsc(opt.rationale) + '</div>' : '';
        html += '<button type="button" class="sb2-prop-card-option' + (isRec ? ' recommended' : '') + '" data-choice-index="' + i + '" onclick="window.sb2PropResolveChoice(\'' + sb2PropEsc(prop.id) + '\', ' + i + ', this)">';
        html += '<span class="star">' + (isRec ? '★' : '·') + '</span>';
        html += '<span class="label">' + label + '</span>';
        if (isRec) html += '<span class="ai-tag">AI 推荐</span>';
        html += '</button>';
        if (rationale) html += rationale;
      }
      html += '</div>';
    }
    // 驳回 ghost
    html += '<div style="text-align:right;"><button type="button" class="sb2-prop-card-reject" onclick="window.sb2PropResolveReject(\'' + sb2PropEsc(prop.id) + '\', this)">驳回</button></div>';
  } else if (status === 'executed' && execResult && typeof execResult === 'object') {
    var summaryText = '已执行';
    if (execResult.task_id) summaryText = '任务 #' + sb2PropEsc(execResult.task_id) + ' 已创建';
    else if (execResult.talent_id) summaryText = '达人 #' + sb2PropEsc(execResult.talent_id) + ' 已创建';
    else if (execResult.target_agent_id) summaryText = '消息已代发';
    html += '<div class="sb2-prop-card-result"><strong>✓</strong> ' + summaryText + '</div>';
  } else if (status === 'approved') {
    html += '<div class="sb2-prop-card-result"><strong>⏳</strong> 已采纳, 执行中…</div>';
  } else if (status === 'rejected') {
    html += '<div class="sb2-prop-card-result"><strong>✗</strong> 已驳回' + (resolutionNote ? ': ' + resolutionNote : '') + '</div>';
  } else if (status === 'expired') {
    html += '<div class="sb2-prop-card-result"><strong>⏰</strong> 已过期, 不可操作</div>';
  } else if (status === 'failed') {
    html += '<div class="sb2-prop-card-result"><strong>⚠</strong> 执行失败' + (resolutionNote ? ': ' + resolutionNote : '') + '</div>';
  }
  el.innerHTML = html;
}

// ★ resolve 交互: 点选项 → POST /api/proposals/:id/resolve {choice_index}
//   200 → 渲染结果态 (一次性, 不重复渲染按钮组 — spec §2 铁律)
//   409 → GET 重查最新 status → 渲染结果态 (spec §2.2)
//   网络错 → toast「操作失败请重试」+ 卡片保持 pending 可再点
window.sb2PropResolveChoice = async function(proposalId, choiceIndex, btn){
  if (!proposalId || btn.disabled) return;
  var card = btn.closest('.sb2-prop-card');
  if (!card) return;
  // 锁按钮 (避免双击)
  var buttons = card.querySelectorAll('.sb2-prop-card-option, .sb2-prop-card-reject');
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
  try {
    var resp = await apiFetch('/api/proposals/' + encodeURIComponent(proposalId) + '/resolve', {
      method: 'POST',
      body: JSON.stringify({ choice_index: choiceIndex })
    });
    if (resp && resp.ok) {
      var prop = await resp.json();
      sb2PropRenderState(card, prop);
      // ★ spec §3: 采纳成功 → 本地 append 系统气泡 (P1b 不落盘)
      if (prop.status === 'executed' || prop.status === 'failed') {
        var agentName = prop.agent_name || 'AI 员工';
        var resultSummary = '已采纳并执行';
        if (prop.exec_result && typeof prop.exec_result === 'object') {
          if (prop.exec_result.task_id) resultSummary = '任务 #' + prop.exec_result.task_id + ' 已创建';
          else if (prop.exec_result.talent_id) resultSummary = '达人 #' + prop.exec_result.talent_id + ' 已创建';
          else if (prop.exec_result.target_agent_id) resultSummary = '消息已代发';
        }
        if (prop.status === 'failed') resultSummary = '采纳了但执行失败';
        sb2PropAppendSystemBubble(agentName, resultSummary);
      }
      return;
    }
    // 409 已被他人处理或过期 → 重查最新 status
    if (resp && resp.status === 409) {
      var refetch = await apiFetch('/api/proposals/' + encodeURIComponent(proposalId));
      if (refetch && refetch.ok) {
        var latest = await refetch.json();
        sb2PropRenderState(card, latest);
        sb2PropToast('状态已更新: ' + sb2PropStateLabel(latest.status), 'info');
        return;
      }
      sb2PropToast('状态已被他人处理, 请刷新聊天', 'warning');
      return;
    }
    // 其它 4xx
    if (resp) {
      var errBody = await resp.json().catch(function(){ return {}; });
      var errMsg = (errBody && errBody.error && errBody.error.message) ? errBody.error.message : ('HTTP ' + resp.status);
      sb2PropToast('操作失败: ' + errMsg, 'error');
      // 恢复按钮可点
      for (var j = 0; j < buttons.length; j++) buttons[j].disabled = false;
      return;
    }
    // resp null (apiFetch 内部 mock / 网络错)
    sb2PropToast('操作失败请重试', 'error');
    for (var k = 0; k < buttons.length; k++) buttons[k].disabled = false;
  } catch (e) {
    console.error('[sb2PropResolveChoice]', e);
    sb2PropToast('网络错误: ' + (e && e.message ? e.message : e), 'error');
    for (var m = 0; m < buttons.length; m++) buttons[m].disabled = false;
  }
};

// ★ 驳回交互: 走 reject 路径 (B1 fix 越界校验已豁免 reject)
window.sb2PropResolveReject = async function(proposalId, btn){
  if (!proposalId || (btn && btn.disabled)) return;
  var card = btn && btn.closest ? btn.closest('.sb2-prop-card') : null;
  if (!card) return;
  var buttons = card.querySelectorAll('.sb2-prop-card-option, .sb2-prop-card-reject');
  for (var i = 0; i < buttons.length; i++) buttons[i].disabled = true;
  try {
    var resp = await apiFetch('/api/proposals/' + encodeURIComponent(proposalId) + '/resolve', {
      method: 'POST',
      body: JSON.stringify({ reject: true })
    });
    if (resp && resp.ok) {
      var prop = await resp.json();
      sb2PropRenderState(card, prop);
      return;
    }
    if (resp && resp.status === 409) {
      var refetch = await apiFetch('/api/proposals/' + encodeURIComponent(proposalId));
      if (refetch && refetch.ok) {
        var latest = await refetch.json();
        sb2PropRenderState(card, latest);
        sb2PropToast('状态已更新: ' + sb2PropStateLabel(latest.status), 'info');
        return;
      }
      sb2PropToast('状态已被他人处理, 请刷新聊天', 'warning');
      return;
    }
    if (resp) {
      var errBody = await resp.json().catch(function(){ return {}; });
      var errMsg = (errBody && errBody.error && errBody.error.message) ? errBody.error.message : ('HTTP ' + resp.status);
      sb2PropToast('操作失败: ' + errMsg, 'error');
      for (var j = 0; j < buttons.length; j++) buttons[j].disabled = false;
      return;
    }
    sb2PropToast('操作失败请重试', 'error');
    for (var k = 0; k < buttons.length; k++) buttons[k].disabled = false;
  } catch (e) {
    console.error('[sb2PropResolveReject]', e);
    sb2PropToast('网络错误: ' + (e && e.message ? e.message : e), 'error');
    for (var m = 0; m < buttons.length; m++) buttons[m].disabled = false;
  }
};

// ★ 采纳后本地 append 系统气泡 (P1b 不落盘, spec §3 红线)
//   通过 _sb2Chat.messages 推一条 + 调 sb2_appendBubble 渲染, 不调 API
function sb2PropAppendSystemBubble(agentName, summary){
  var text = '✅ ' + (agentName || 'AI 员工') + ' 的建议已被你采纳: ' + (summary || '');
  var sysMsg = {
    role: 'system',
    content: text,
    timestamp: new Date().toISOString()
  };
  // ★ P1b B2 fix: 改读 window._sb2Chat (IIFE 内部 scope 跨块不可见, 必须读挂出去的引用)
  //   之前: Array.isArray(window._sb2Chat && _sb2Chat.messages) — && 永远返回 messages (数组), 但 _sb2Chat 自身在 IIFE 内不可见 → ReferenceError 静默 swallow
  //   现在: 明确 window._sb2Chat && Array.isArray(window._sb2Chat.messages)
  if (window._sb2Chat && Array.isArray(window._sb2Chat.messages)) window._sb2Chat.messages.push(sysMsg);
  // ★ P1b B2 fix: 走 window.sb2_appendBubble (跨块 typeof 永远是 undefined 必须挂 window)
  if (typeof window.sb2_appendBubble === 'function') {
    window.sb2_appendBubble(sysMsg, true);
  }
}
// ★ P1b B2 fix: 自身也挂 window (防 SEV1 跨块)
window.sb2PropAppendSystemBubble = sb2PropAppendSystemBubble;
