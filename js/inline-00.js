/* ===== index.html 内联块 0 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: // Early login handler - works even if main script fails to  */
// Early login handler - works even if main script fails to load
window.doLogin = function () {
  var btn = document.getElementById('loginBtn');
  // ★ ui/login-v21-redesign: 加 .loading class 触发 spinner + "正在验证..."
  if (btn) { btn.disabled = true; btn.classList.add('loading'); }
  var username = document.getElementById('loginUsername');
  var password = document.getElementById('loginPassword');
  var errorDiv = document.getElementById('loginError');
  if (!username || !password) {
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    return;
  }
  if (!username.value.trim() || !password.value) {
    if (errorDiv) {
      errorDiv.textContent = '请输入用户名和密码';
      errorDiv.classList.add('show'); // 替换老 style.display='block'
    }
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    return;
  }
  // 通用复位 helper (success/error 都要恢复 button)
  var resetBtn = function () {
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
  };

  // Try the full login function from main script
  if (typeof _fullLogin === 'function') {
    _fullLogin().finally(resetBtn);
  } else {
    // Main script not loaded yet, try manual fetch
    apiFetch('/api/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        username: username.value.trim(),
        password: password.value
      })
    }).then(function (r) {
      return r.json();
    }).then(function (data) {
      if (data.token) {
        localStorage.setItem('sb_current_user', JSON.stringify(data.user || {
          username: username.value.trim()
        }));
        localStorage.setItem('sb_auth_token', data.token);
        location.reload();
      } else {
        if (errorDiv) {
          errorDiv.textContent = data.error || '登录失败';
          errorDiv.classList.add('show');
        }
        resetBtn();
      }
    }).catch(function (e) {
      if (errorDiv) {
        errorDiv.textContent = '连接服务器失败';
        errorDiv.classList.add('show');
      }
      resetBtn();
    });
  }
};
