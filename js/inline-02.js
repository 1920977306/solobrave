/* ===== index.html 内联块 2 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: /* ui/login-register-v21-full: tab 切换 + register submit + st */
/* ui/login-register-v21-full: tab 切换 + register submit + stub 提示 */
(function () {
  // Tab 切换
  document.querySelectorAll('.login-form-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-tab');
      document.querySelectorAll('.login-form-tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.login-form-pane').forEach(function (p) { p.classList.remove('active'); });
      tab.classList.add('active');
      var pane = document.querySelector('.login-form-pane[data-pane="' + target + '"]');
      if (pane) pane.classList.add('active');
    });
  });

  // 简化版 toast (复用项目内已有 toast 系统, 此处兜底)
  window.showLoginToast = function (msg) {
    if (typeof showToast === 'function') { showToast(msg, 'info'); return; }
    var t = document.createElement('div');
    t.textContent = msg;
    t.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:rgba(28,28,30,0.92);color:#fff;padding:10px 18px;border-radius:10px;font:500 13px/1.4 -apple-system,sans-serif;z-index:99999;backdrop-filter:blur(8px);';
    document.body.appendChild(t);
    setTimeout(function () { t.remove(); }, 2400);
  };

  // 注册页跳转
  window.showRegister = function () {
    document.getElementById('loginOverlay').hidden = true;
    document.getElementById('registerOverlay').hidden = false;
    var f = document.getElementById('registerForm');
    if (f) f.reset();
    var formEl = document.getElementById('registerForm');
    if (formEl) formEl.style.display = '';
    document.getElementById('registerSuccess').classList.remove('show');
    document.getElementById('registerError').classList.remove('show');
    updateRegisterStep(1);
    setTimeout(function () {
      var u = document.getElementById('regUsername');
      if (u) u.focus();
    }, 80);
  };
  window.hideRegister = function () {
    document.getElementById('registerOverlay').hidden = true;
  };
  window.showLogin = function () {
    document.getElementById('loginOverlay').hidden = false;
    document.getElementById('registerOverlay').hidden = true;
    document.querySelectorAll('.login-form-tab').forEach(function (t) { t.classList.remove('active'); });
    document.querySelectorAll('.login-form-pane').forEach(function (p) { p.classList.remove('active'); });
    var at = document.querySelector('.login-form-tab[data-tab="account"]');
    var ap = document.querySelector('.login-form-pane[data-pane="account"]');
    if (at) at.classList.add('active');
    if (ap) ap.classList.add('active');
    setTimeout(function () {
      var u = document.getElementById('loginUsername');
      if (u) u.focus();
    }, 80);
  };
  function updateRegisterStep(step) {
    document.querySelectorAll('#registerStepDots .register-step-dot').forEach(function (d) {
      var s = parseInt(d.getAttribute('data-step'), 10);
      d.classList.toggle('active', s <= step);
    });
  }

  // 提交注册申请 (走 admin invite 模式 + localStorage 兜底)
  window.submitRegister = function () {
    var errDiv = document.getElementById('registerError');
    if (errDiv) { errDiv.classList.remove('show'); errDiv.textContent = ''; }
    var username = (document.getElementById('regUsername').value || '').trim();
    var displayName = (document.getElementById('regDisplayName').value || '').trim();
    var password = document.getElementById('regPassword').value || '';
    var invite = (document.getElementById('regInvite').value || '').trim();
    if (!username || username.length < 4) {
      if (errDiv) { errDiv.textContent = '用户名至少 4 位'; errDiv.classList.add('show'); }
      return;
    }
    if (!password || password.length < 6) {
      if (errDiv) { errDiv.textContent = '密码至少 6 位'; errDiv.classList.add('show'); }
      return;
    }
    var btn = document.getElementById('registerBtn');
    if (btn) { btn.disabled = true; btn.classList.add('loading'); }

    // 注册申请: localStorage 暂存, 管理员可读 (前端 mock, 等后端 admin invite endpoint)
    try {
      var pending = JSON.parse(localStorage.getItem('solobrave.pending_registrations') || '[]');
      var dup = pending.find(function (p) { return p.username === username; });
      if (dup) {
        if (errDiv) { errDiv.textContent = '该用户名已提交过申请, 请等待审核'; errDiv.classList.add('show'); }
        if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
        return;
      }
      pending.push({
        username: username,
        displayName: displayName || username,
        password: btoa(unescape(encodeURIComponent(password))),
        invite: invite,
        submittedAt: new Date().toISOString()
      });
      localStorage.setItem('solobrave.pending_registrations', JSON.stringify(pending));
    } catch (e) { /* localStorage 不可用时静默 */ }

    // 模拟网络延迟 + 切换到成功页
    setTimeout(function () {
      if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
      updateRegisterStep(2);
      var formEl = document.getElementById('registerForm');
      if (formEl) formEl.style.display = 'none';
      document.getElementById('registerSuccess').classList.add('show');
    }, 800);
  };
})();
