/* ===== index.html 内联块 3 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: // ========== 工具函数 ========== */
// ========== 工具函数 ==========
// ========== Phase 2: 权限控制 ==========

// 群聊 AI 行动边界：防止 AI 越权替老板或真人做承诺、约时间、做决策、编造事件
// 保持拟人化，但涉及行动类决定必须先请示真人，不能自己接下或推进
var GROUP_CHAT_ACTION_BOUNDARY = '【行动边界】你保持拟人化、有脾气、有专业判断，但绝不代表老板或任何真人做承诺、约定、决策。你没有权限代替真人安排会议时间、许诺交付物、确认合同/方案/预算、编造未发生或未核实的具体事件。如果话题涉及需要真人拍板的行动（如“约个会”“带合同来”“定个时间”“就这么办”），你必须明确说“这件事我需要先请示老板/相关同事，我不能替他决定”，并停止推进，而不是自己接下、编造或推动执行。';

var projects = [];
var currentUser = null;
var authToken = localStorage.getItem('sb_auth_token') || null;
window._pendingImages = [];

// ============ Chat 重试链路加固 (refactor/chat-retry-pipeline) ============
// 顶层常量:前端调用层(只动 sendViaOpenClaw / sendViaAPI 入口,不动 OpenClaw 网关内部)
// - TIMEOUT_CHAT_MS: chat 端到端 UI 兜底超时（120s,OpenClaw 实际首次 assistant event
//   30-60s 到达;之前 30s 太短,AI 回复已在 WS 收到但前端先 reject 触发误重试 →
//   'AbortError: chat timeout after 30000ms',页面没渲染回复。120s 留够缓冲）
// - RETRY_BACKOFF_LLM_429 / RETRY_BACKOFF_NETWORK: 指数退避序列(单位 ms)
//   429 限流按 1s/2s/4s 退避,网络错误立即重试 1 次(不 sleep,直接 retry)
// - MAX_RETRY_LLM_429: 429 重试上限 3 次
// - MAX_RETRY_NETWORK: 网络错误重试上限 1 次(其他错误不重试,直接报错)
var TIMEOUT_CHAT_MS = 120000;  // 30s → 120s (commit 2e18fc2 + commit fix/chat-timeout-120s)
var RETRY_BACKOFF_LLM_429 = [0, 1000, 2000, 4000];   // 第 0 次=立即,1/2/3 次=1s/2s/4s
var RETRY_BACKOFF_NETWORK = [0, 0];                  // 立即重试 1 次
var MAX_RETRY_LLM_429 = 3;
var MAX_RETRY_NETWORK = 1;

// 错误分类:把 fetch/apiFetch/lifecycle 抛出的 error 归类到 5 种之一
// 返回: 'timeout' | 'network' | 'rate_limit' | 'llm' | 'unknown'
//   timeout   - Promise.race 超时 (withTimeout reject)
//   network   - Failed to fetch / NetworkError / CORS / 5xx
//   rate_limit - HTTP 429 (限流,OpenClaw gateway 或 LLM 供应商返回)
//   llm       - HTTP 4xx 非 429 (LLM 调用方问题,鉴权/参数错等,不该重试)
//   unknown   - 兜底
function _classifyChatError(err) {
  if (!err) return 'unknown';
  var msg = (err.message || String(err) || '').toLowerCase();
  // timeout: withTimeout 抛的 AbortError 或含 'timeout' 字样
  if (err.name === 'AbortError' || msg.indexOf('timeout') !== -1) return 'timeout';
  // network: 浏览器原生网络错误 / CORS / 5xx
  if (msg.indexOf('failed to fetch') !== -1) return 'network';
  if (msg.indexOf('networkerror') !== -1) return 'network';
  if (msg.indexOf('cors') !== -1) return 'network';
  if (/http\s*5\d\d/.test(msg)) return 'network';
  // rate_limit: 429
  if (msg.indexOf('http 429') !== -1 || msg.indexOf('429') !== -1) return 'rate_limit';
  // llm: 4xx 非 429 (400/401/403/404 等,通常是 LLM 调用方问题)
  if (/http\s*4\d\d/.test(msg)) return 'llm';
  return 'unknown';
}

// Promise 超时包装:race 一个 setTimeout reject,达到 ms 立即 reject with 'timeout' 错
// 注意: AbortController 在 WS 场景无法中断已发请求,这里只做 UI 兜底早 reject,
// 后台 _sendChatWaitForLifecycle 可能仍在跑(它的 lifecycleTimeout=120s 兜底)
function _withTimeout(promise, ms, label) {
  label = label || 'chat';
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      var e = new Error(label + ' timeout after ' + ms + 'ms');
      e.name = 'AbortError';
      e.code = 'TIMEOUT';
      reject(e);
    }, ms);
    promise.then(
      function (v) { clearTimeout(timer); resolve(v); },
      function (e) { clearTimeout(timer); reject(e); }
    );
  });
}

// 重试包装:对 chat fn 做指数退避重试
//   fn: 异步函数,返回结果或 throw
//   options.maxRetries: 各类错误的最大重试次数(per-kind,已映射)
//   返回: { success, result, error, errorKind, attempts, lastError }
//   chat 错误分类 → 重试策略:
//     rate_limit → MAX_RETRY_LLM_429 次,按 RETRY_BACKOFF_LLM_429 退避
//     network    → MAX_RETRY_NETWORK 次,按 RETRY_BACKOFF_NETWORK 退避(立即重试)
//     timeout/llm/unknown → 不重试,直接返回失败
async function _retryChatCall(fn, options) {
  options = options || {};
  var label = options.label || 'chat';
  var attempt = 0;
  var lastErr = null;
  var lastKind = 'unknown';
  while (true) {
    attempt++;
    try {
      var result = await fn(attempt);
      return { success: true, result: result, error: null, errorKind: null, attempts: attempt };
    } catch (e) {
      lastErr = e;
      lastKind = _classifyChatError(e);
      var backoff = null;
      var maxRetries = 0;
      if (lastKind === 'rate_limit') {
        maxRetries = MAX_RETRY_LLM_429;
        backoff = RETRY_BACKOFF_LLM_429;
      } else if (lastKind === 'network') {
        maxRetries = MAX_RETRY_NETWORK;
        backoff = RETRY_BACKOFF_NETWORK;
      } else {
        // timeout/llm/unknown: 不重试
        maxRetries = 0;
        backoff = [0];
      }
      if (attempt > maxRetries) {
        return { success: false, result: null, error: e, errorKind: lastKind, attempts: attempt, lastError: e };
      }
      var sleepMs = backoff[Math.min(attempt, backoff.length - 1)] || 0;
      // 调 sleepFn 即使 sleepMs=0(记录调用,便于测试和将来需要 pre-retry hook 时插入)
      await new Promise(function (r) { setTimeout(r, sleepMs); });
      console.warn('[' + label + '] retry attempt=' + (attempt + 1) + '/' + (maxRetries + 1) + ' kind=' + lastKind + ' sleep=' + sleepMs + 'ms err=' + (e.message || e));
    }
  }
}

// 统一 API 请求（带认证 token）
async function apiFetch(url, options = {}) {
  const token = localStorage.getItem('sb_auth_token');
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers
  };
  if (token && token !== 'null' && token !== 'local_mode') {
    headers['Authorization'] = 'Bearer ' + token;
  }
  try {
    const resp = await fetch(url, {
      ...options,
      headers
    });
    if (resp.status === 401) {
      // 登录接口的 401 由 _fullLogin 处理（提示用户名或密码错误），此处只返回 null
      if (url.indexOf('/api/auth/login') !== -1) {
        return null;
      }
      // 其他接口 401 表示会话失效：清除本地会话并回到登录页
      currentUser = null;
      authToken = null;
      localStorage.removeItem('sb_current_user');
      localStorage.removeItem('sb_auth_token');
      // 已在登录页时不重复跳转，避免循环
      var loginOverlay = document.getElementById('loginOverlay');
      // ★ ui/login-v21-redesign: 改用 hidden 属性判断 (替换老 inline style.display)
      if (!loginOverlay || loginOverlay.hidden) {
        showLogin();
        showToast('⚠️ 登录已过期，请重新登录');
      }
      return null;
    }
    if (resp.status === 403) {
      var method = (options.method || 'GET').toUpperCase();
      // critical=true 表示主数据源请求：403 不再静默成空列表，
      // 抛出让调用方走可见的错误/提示路径（fix/analyzed-talents-decouple）
      if (options.critical) {
        throw new Error('权限不足 (403)');
      }
      if (method === 'GET') {
        console.warn('[apiFetch] 403 Forbidden, skipping:', url);
        return new Response('[]', { status: 200, headers: { 'Content-Type': 'application/json' } });
      } else {
        throw new Error('权限不足 (403)');
      }
    }
    // 409 静默：业务上通常是"池子满"（记忆池、知识池等），不影响聊天主流程，
    // 只在控制台 warn 一行避免污染 UX。
    if (resp.status === 409) {
      console.warn('[apiFetch] 409 Conflict, silently ignoring:', url);
      return new Response('{"ok":false,"reason":"conflict"}', {
        status: 200, headers: { 'Content-Type': 'application/json' }
      });
    }
    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status + ' ' + resp.statusText);
    }
    return resp;
  } catch (e) {
    console.error('API 请求失败:', e);
    throw e;
  }
}

// ★ refactor/openclaw-gateway-error-handling: OpenClaw / gateway 调用的统一包装
// 区别于 apiFetch (返回 Response, 上百处调用方), 这里返回统一格式 {success, data, error, status}
// 专给 /api/openclaw/* 8 处调用用, 前端不会拿到异常响应崩溃
// 行为:
//   - AbortController 10s 超时 (网络挂起兜底)
//   - 网络错误 / 超时 → 立即重试 1 次 (maxRetries=2 = 初始 + 1 次重试)
//   - HTTP 4xx/5xx → 解析 error body (JSON 优先, 文本兜底) → {success:false, error, status}
//   - JSON parse 失败 → {success:false, error:'invalid_response'}
//   - 成功 → {success:true, data, status}
//   - 401/403/409 仍走 apiFetch 内部的 401 跳登录/403 GET 静默/409 静默 逻辑(返回 null),
//     本包装不重复处理, 视为 "没有 resp" → 包装成 {success:false, error:'no_response', status:0}
async function apiFetchWithRetry(url, options, maxRetries) {
  if (maxRetries == null) maxRetries = 2;  // 初始 + 1 次重试
  var timeoutMs = 10000;
  var lastError = null;
  for (var attempt = 1; attempt <= maxRetries; attempt++) {
    var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
    var timer = null;
    if (controller) {
      timer = setTimeout(function () { try { controller.abort(); } catch (e) {} }, timeoutMs);
    }
    try {
      var resp = await apiFetch(url, controller ? Object.assign({}, options, { signal: controller.signal }) : options);
      if (timer) clearTimeout(timer);
      // apiFetch 内部 401/403/409 返回 null 或 mock Response, 视为失败
      if (!resp) {
        return { success: false, data: null, error: 'no_response', status: 0, attempts: attempt };
      }
      if (!resp.ok) {
        // 4xx/5xx: 解析 error body
        var errMsg = null;
        try {
          var errBody = await resp.json();
          errMsg = errBody && (errBody.error || errBody.message || errBody.reason);
        } catch (e) {
          try { errMsg = await resp.text(); } catch (e2) { errMsg = 'HTTP ' + resp.status; }
        }
        return { success: false, data: null, error: errMsg || ('HTTP ' + resp.status), status: resp.status, attempts: attempt };
      }
      // 成功, 解析 JSON
      try {
        var data = await resp.json();
        return { success: true, data: data, error: null, status: resp.status, attempts: attempt };
      } catch (e) {
        // JSON parse 失败
        return { success: false, data: null, error: 'invalid_response', status: resp.status, attempts: attempt };
      }
    } catch (e) {
      if (timer) clearTimeout(timer);
      lastError = e;
      // 网络错误 / 超时 (AbortError) → 决定是否重试
      var isTimeout = e && (e.name === 'AbortError' || (e.message || '').toLowerCase().indexOf('timeout') !== -1);
      var isNetwork = e && (e.message || '').toLowerCase().match(/failed to fetch|networkerror|cors/);
      if ((isTimeout || isNetwork) && attempt < maxRetries) {
        console.warn('[apiFetchWithRetry] retry ' + attempt + '/' + maxRetries + ' for ' + url + ' (' + (isTimeout ? 'timeout' : 'network') + ')');
        continue;
      }
      // 不可重试错误 / 重试用尽
      return {
        success: false,
        data: null,
        error: isTimeout ? 'timeout' : (e.message || 'network_error'),
        status: 0,
        attempts: attempt
      };
    }
  }
  // 不应到达, 但兜底
  return { success: false, data: null, error: (lastError && lastError.message) || 'unknown', status: 0, attempts: maxRetries };
}

// 检查认证状态
async function checkAuth() {
  var savedUser = localStorage.getItem('sb_current_user');
  var savedToken = localStorage.getItem('sb_auth_token');
  if (savedUser && savedToken && savedToken !== 'local_mode') {
    try {
      // 验证 token 是否仍然有效
      const resp = await apiFetch('/api/auth/me');
      if (resp && resp.ok) {
        const data = await resp.json();
        currentUser = data.user || data || JSON.parse(savedUser);
        if (currentUser && currentUser.permissions) {
          localStorage.setItem('sb_current_user', JSON.stringify(currentUser));
        }
        authToken = savedToken;
        showMainApp();
        return;
      }
    } catch (e) {
      console.debug('Token 验证失败', e);
    }
  } else if (savedUser && savedToken === 'local_mode') {
    // 本地模式直接恢复
    try {
      currentUser = JSON.parse(savedUser);
      authToken = savedToken;
      showMainApp();
      return;
    } catch (e) {}
  }

  // 没有有效登录，显示登录页
  showLogin();
}
// ★ ui/login-v21-redesign: 用 hidden 属性切换 (替换老 inline style.display)
function showLogin() {
  var lo = document.getElementById('loginOverlay');
  if (lo) lo.hidden = false;
  var ac = document.querySelector('.app-container'); if (ac) ac.hidden = true;
  var tb = document.querySelector('.top-banner'); if (tb) tb.hidden = true;
  // 自动聚焦第一个字段, 提升体验
  setTimeout(function () {
    var u = document.getElementById('loginUsername');
    if (u && !u.value) u.focus();
    else { var p = document.getElementById('loginPassword'); if (p) p.focus(); }
  }, 50);
}
function hideLogin() {
  var lo = document.getElementById('loginOverlay');
  if (lo) lo.hidden = true;
  var ac = document.querySelector('.app-container'); if (ac) ac.hidden = false;
  var tb = document.querySelector('.top-banner'); if (tb) tb.hidden = false;
}
function showMainApp() {
  hideLogin();
  applyPermissions();
  updateUserInfo();
  // 初始化龙虾办公室同步频道
  initLobsterChannel();
  // 加载员工列表（从后端优先，降级到 localStorage）
  loadEmployees().then(function () {
    return loadGroups();
  }).then(function () {
    renderEmployeeList();
    // 清理：校验当前聊天对象是否还存在（不删除聊天记录，只清除无效的当前选中）
    var curEmp = localStorage.getItem('sb_current_emp');
    if (curEmp) {
      var curEmpObj = emps.find(function (e) {
        return e.id === curEmp;
      });
      // 不在可见列表中就清掉当前选中（保留聊天记录）
      var visibleEmps = getVisibleEmps();
      if (!curEmpObj || visibleEmps.indexOf(curEmpObj) < 0) {
        localStorage.removeItem('sb_current_emp');
      }
    }
    // 重置聊天区到空状态（仅当前员工确实不存在时，且 emps 已加载完毕）
    var needReset = false;
    var currentEmpAfterCheck = localStorage.getItem('sb_current_emp');
    if (!currentEmpAfterCheck) {
      needReset = true;
    } else {
      var curEmpObj2 = emps.find(function (e) {
        return e.id === currentEmpAfterCheck;
      });
      var visibleEmps2 = getVisibleEmps();
      if (!curEmpObj2 || visibleEmps2.indexOf(curEmpObj2) < 0) {
        needReset = true;
      }
    }
    if (needReset) {
      var visibleEmps3 = getVisibleEmps();
      if (visibleEmps3.length > 0) {
        // 自动选择第一个可见员工作为默认聊天对象
        openChat(visibleEmps3[0].id);
      } else {
        renderEmptyChat();
        return;
      }
    }
  }).catch(function (err) {
    console.error('[showMainApp] 加载员工/小组失败:', err);
    renderEmployeeList();
  });
  // ① onboarding 引导检测: 登录后 emps 加载完, 检查是否要弹新人引导 modal
  // (放在 .then() 链外部, 不阻塞主流程; emps 异步加载完成后会自动触发)
  setTimeout(maybeShowOnboardingModal, 800);
  // Feishu layout: default to messages module
  switchModule('messages');
  // Leader加载可见小组
  async function loadVisibleTeams() {
    try {
      var resp = await apiFetch('/api/teams');
      if (resp && resp.ok) {
        var allTeams = await resp.json();
        // leader只看自己管理的组
        var uid = currentUser && currentUser.id;
        var managedIds = currentUser && currentUser.managedTeamIds || [];
        window._visibleTeams = (allTeams || []).filter(function (t) {
          return (t.leader === uid || t.leaderId === uid) || managedIds.indexOf(t.id) >= 0;
        });
        // 如果没有任何管理的组，也能看到所有组（兼容未分配的情况）
        if (window._visibleTeams.length === 0) window._visibleTeams = allTeams || [];
      } else {
        window._visibleTeams = [];
      }
    } catch (e) {
      window._visibleTeams = [];
    }
  }

  // 自动连接 OpenClaw
  setTimeout(initOpenClaw, 1000);

  // 启动项目组列表轮询：每 15 秒从后端刷新，子账号页面自动感知管理员新增的项目组
  startGroupPolling();
}

// ========== ① 新人 onboarding 引导 modal ==========
// 检测逻辑: 登录后(或 refreshEmployeeList 后)如果用户没创建过 AI 员工, 弹轻量引导卡片
// 「先逛逛」设 sb_onboarding_done='1' 后不再弹
// 未来 reset 路径(如后端清空用户数据)后, 因为新会话 localStorage 不会有这个标志, 也会自动重新触发
function maybeShowOnboardingModal() {
  if (typeof emps === 'undefined') return;
  var done = localStorage.getItem('sb_onboarding_done');
  if (done === '1') return; // 用户已看过引导
  // 只对 0 员工的用户弹(已经有员工的账号不打扰)
  if (emps.length > 0) {
    // 用户已经有员工, 自动设 done(避免后续每次登录都检查)
    localStorage.setItem('sb_onboarding_done', '1');
    return;
  }
  // 当前账号没有任何 AI 员工 → 弹引导
  var modal = document.getElementById('onboardingModal');
  if (modal) modal.style.display = 'flex';
}
function onboardingCreateClick() {
  localStorage.setItem('sb_onboarding_done', '1');
  var modal = document.getElementById('onboardingModal');
  if (modal) modal.style.display = 'none';
  // ★ feat/onboarding-wizard: 新人引导改走团队模板 3 步 wizard (而不是单员工 4 步 openWizard)
  if (typeof openOnboardWizard === 'function') {
    openOnboardWizard();
  } else if (typeof openWizard === 'function') {
    openWizard();
  }
}
function onboardingSkipClick() {
  localStorage.setItem('sb_onboarding_done', '1');
  var modal = document.getElementById('onboardingModal');
  if (modal) modal.style.display = 'none';
}

// ========== ② 新人 onboarding 真正 3 步 wizard (团队模板 → 批量配置 → 开始对话) ==========
// 团队模板常量 — systemPrompt 必须基于真实 demo 员工 (Helen) 的能力, 不编新能力
// Q1=A 决策: 硬编码在前端, v1 够用; 后续可迁到后端 /api/agent-templates
// Q1 约束: 角色名中性 ("小回/小薇/小理"), 不绑 demo 员工人名 (Helen/孔明/貂蝉)
var ONBOARD_TEMPLATES = [
  {
    id: 'douyin_biz',
    name: '抖音商务团',
    icon: '🤝',
    desc: '达人合作全流程: 截图识别、匹配分析、选品建议、价值评级',
    members: [
      {
        roleKey: 'biz_specialist',
        roleLabel: '商务专员',
        avatar: '🤝',
        defaultName: '小回',
        skills: ['截图录入', '匹配分析', '选品建议', '合作评级'],
        systemPrompt: '你是小回, 抖音团长的商务专员, 负责达人合作全流程。\n\n【核心能力】\n1. 截图录入: 用户上传达人主页截图, 自动 OCR 识别达人信息 + 调用 kb_entry_create 写入知识库。\n2. 达人-商品匹配分析: 6 维度 (粉丝画像 / 历史 GPM / 客单价 / 品类契合 / 调性 / 履约) 评估合作匹配度。\n3. 选品策略建议: 基于本周已合作达人的粉丝画像, 推荐下一波选品 (品类 + 价格带)。\n4. 合作价值评级: 基于最新数据 (履约率 / 复购 / GMV) 重评达人合作价值 A/B/C/D。\n\n【底线】\n- 没有数据支撑的结论不说, 所有结论必须基于真实知识库或工具返回。\n- 所有数据必须通过调用工具写入系统, 严禁口头回复"已录入"而不实际调用工具。\n- 如果没有某个工具权限, 必须如实告知用户, 严禁假装已完成。'
      },
      {
        roleKey: 'data_analyst',
        roleLabel: '数据分析师',
        avatar: '📊',
        defaultName: '小理',
        skills: ['合作复盘', '数据看板', '趋势分析'],
        systemPrompt: '你是小理, 抖音团长的数据分析师, 负责合作复盘和趋势洞察。\n\n【核心能力】\n1. 合作复盘: 基于已完成合作的数据 (GMV / ROI / 履约), 输出复盘结论和优化建议。\n2. 数据看板: 汇总本周 / 本月达人合作数据, 输出关键指标 (合作数 / GPM / 复购率)。\n3. 趋势分析: 对比历史数据, 识别增长 / 下滑趋势, 给出归因。\n\n【底线】\n- 数据必须从知识库或工具调用获取, 不编数据。\n- 结论必须有数据支撑, 没有数据时说"暂无数据"。'
      }
    ]
  },
  {
    id: 'content_ops',
    name: '内容运营团',
    icon: '✍️',
    desc: '内容策划 + 文案撰写 + 素材整理',
    members: [
      {
        roleKey: 'copywriter',
        roleLabel: '文案策划',
        avatar: '✍️',
        defaultName: '小薇',
        skills: ['脚本撰写', '卖点提炼', '达人话术'],
        systemPrompt: '你是小薇, 抖音团长的文案策划, 负责短视频脚本和达人沟通话术。\n\n【核心能力】\n1. 脚本撰写: 基于达人人设 + 商品卖点, 撰写 15- 60 秒短视频脚本 (含钩子 + 节奏 + CTA)。\n2. 卖点提炼: 从商品详情提炼 3- 5 个核心卖点, 按达人粉丝画像匹配。\n3. 达人话术: 私信话术模板 (初次接触 / 跟进 / 报价 / 复盘), 语气符合人设。\n\n【底线】\n- 所有内容必须基于真实商品/达人数据, 不编造卖点。\n- 话术遵循"管理员是你的老板, 你是下属"层级关系。'
      },
      {
        roleKey: 'creator',
        roleLabel: '创意策划',
        avatar: '🎨',
        defaultName: '小创',
        skills: ['选题策划', '热点追踪', '内容矩阵'],
        systemPrompt: '你是小创, 抖音团长的创意策划, 负责选题和内容矩阵规划。\n\n【核心能力】\n1. 选题策划: 基于近期热点 + 商品卖点, 推荐 5- 10 个内容选题。\n2. 热点追踪: 监控行业热点 (节日 / 平台活动 / 竞品爆款), 提示联动机会。\n3. 内容矩阵: 设计内容矩阵 (短视频 / 直播 / 图文), 覆盖不同粉丝决策路径。\n\n【底线】\n- 选题基于真实热点 (从工具返回), 不编造虚假热点。'
      }
    ]
  },
  {
    id: 'data_team',
    name: '数据复盘团',
    icon: '📈',
    desc: '深度数据分析 + 评级 + 周报',
    members: [
      {
        roleKey: 'biz_specialist',
        roleLabel: '商务专员',
        avatar: '🤝',
        defaultName: '小回',
        skills: ['截图录入', '匹配分析', '选品建议', '合作评级'],
        systemPrompt: '你是小回, 抖音团长的商务专员。\n\n【核心能力】截图录入 / 匹配分析 / 选品建议 / 合作评级 (同商务团版本)。'
      },
      {
        roleKey: 'data_analyst',
        roleLabel: '数据分析师',
        avatar: '📊',
        defaultName: '小理',
        skills: ['合作复盘', '数据看板', '趋势分析'],
        systemPrompt: '你是小理, 抖音团长的数据分析师。\n\n【核心能力】合作复盘 / 数据看板 / 趋势分析 (同商务团版本)。'
      },
      {
        roleKey: 'reporter',
        roleLabel: '周报助理',
        avatar: '📝',
        defaultName: '小报',
        skills: ['周报生成', '数据汇总', '关键洞察'],
        systemPrompt: '你是小报, 抖音团长的周报助理, 负责周期性复盘和汇报。\n\n【核心能力】\n1. 周报生成: 汇总本周合作数据 (达人 / GMV / 履约 / 新增), 生成结构化周报。\n2. 数据汇总: 跨达人/商品/时间维度汇总, 输出关键数字 + 趋势对比。\n3. 关键洞察: 从数据中提炼 3- 5 条洞察 (亮点 + 风险 + 建议)。\n\n【底线】\n- 周报基于真实数据生成, 不编造数字。\n- 洞察必须有数据支撑。'
      }
    ]
  }
];

// onboard wizard 状态
var onboardStep = 1;
var onboardSelectedTemplate = null;
var onboardSelectedMembers = []; // [{...member, customName, enabled}]

function openOnboardWizard() {
  if (!hasModulePermission('employees')) {
    showToast('⛔ 你没有员工管理权限', 'warning');
    return;
  }
  onboardStep = 1;
  onboardSelectedTemplate = null;
  onboardSelectedMembers = [];
  renderOnboardTemplates();
  updateOnboardUI();
  document.getElementById('onboardWizardOverlay').classList.add('show');
}
function closeOnboardWizard() {
  document.getElementById('onboardWizardOverlay').classList.remove('show');
}
function renderOnboardTemplates() {
  var grid = document.getElementById('onboardTemplateGrid');
  if (!grid) return;
  grid.innerHTML = ONBOARD_TEMPLATES.map(function (t) {
    var memberNames = t.members.map(function (m) { return m.defaultName; }).join(' + ');
    return '<div class="onboard-template-card" data-tpl-id="' + t.id + '" onclick="onboardSelectTemplate(\'' + t.id + '\')">'
      + '<div class="onboard-template-icon">' + t.icon + '</div>'
      + '<div class="onboard-template-info">'
      + '<div class="onboard-template-name">' + escapeHtml(t.name) + '</div>'
      + '<div class="onboard-template-desc">' + escapeHtml(t.desc) + '</div>'
      + '<div class="onboard-template-members">👥 ' + escapeHtml(memberNames) + ' (' + t.members.length + ' 人)</div>'
      + '</div>'
      + '</div>';
  }).join('');
}
function onboardSelectTemplate(templateId) {
  var tpl = ONBOARD_TEMPLATES.find(function (t) { return t.id === templateId; });
  if (!tpl) return;
  onboardSelectedTemplate = tpl;
  onboardSelectedMembers = tpl.members.map(function (m) {
    return {
      roleKey: m.roleKey,
      roleLabel: m.roleLabel,
      avatar: m.avatar,
      defaultName: m.defaultName,
      customName: m.defaultName,
      enabled: true,
      systemPrompt: m.systemPrompt,
      skills: m.skills
    };
  });
  // 高亮选中
  document.querySelectorAll('.onboard-template-card').forEach(function (card) {
    card.classList.toggle('selected', card.dataset.tplId === templateId);
  });
  updateOnboardUI();
}
function renderOnboardMembers() {
  var list = document.getElementById('onboardMemberList');
  if (!list) return;
  list.innerHTML = onboardSelectedMembers.map(function (m, idx) {
    var checked = m.enabled ? ' checked' : '';
    var disabled = m.enabled ? '' : ' disabled';
    var checkMark = m.enabled ? '✓' : '';
    var skillsHtml = (m.skills || []).map(function (s) {
      return '<span style="display:inline-block;padding:2px 8px;border-radius:6px;background:var(--bg-primary);font-size:11px;color:var(--text-secondary);margin-right:4px;">' + escapeHtml(s) + '</span>';
    }).join('');
    return '<div class="onboard-member-row' + disabled + '">'
      + '<div class="onboard-member-check' + checked + '" onclick="onboardToggleMember(' + idx + ')">' + checkMark + '</div>'
      + '<div class="onboard-member-avatar">' + m.avatar + '</div>'
      + '<div class="onboard-member-info">'
      + '<div class="onboard-member-role">' + escapeHtml(m.roleLabel) + '</div>'
      + '<input class="onboard-member-name-input" type="text" value="' + escapeHtml(m.customName) + '" oninput="onboardRenameMember(' + idx + ', this.value)" maxlength="20" />'
      + '<div style="margin-top:4px;">' + skillsHtml + '</div>'
      + '</div>'
      + '</div>';
  }).join('');
}
function onboardRenameMember(idx, val) {
  if (onboardSelectedMembers[idx]) {
    onboardSelectedMembers[idx].customName = val.trim() || onboardSelectedMembers[idx].defaultName;
  }
}
function onboardToggleMember(idx) {
  if (onboardSelectedMembers[idx]) {
    onboardSelectedMembers[idx].enabled = !onboardSelectedMembers[idx].enabled;
    renderOnboardMembers();
    updateOnboardUI();
  }
}
function updateOnboardUI() {
  // dots
  for (var i = 1; i <= 3; i++) {
    var dot = document.getElementById('onboardDot' + i);
    if (!dot) continue;
    dot.classList.remove('active', 'done');
    if (i < onboardStep) dot.classList.add('done');
    else if (i === onboardStep) dot.classList.add('active');
  }
  for (var j = 1; j <= 2; j++) {
    var line = document.getElementById('onboardLine' + j);
    if (!line) continue;
    line.classList.toggle('active', j < onboardStep);
  }
  // pages
  for (var p = 1; p <= 3; p++) {
    var page = document.getElementById('onboardPage' + p);
    if (page) page.classList.toggle('active', p === onboardStep);
  }
  // footer
  var prevBtn = document.getElementById('onboardBtnPrev');
  var nextBtn = document.getElementById('onboardBtnNext');
  var skipBtn = document.getElementById('onboardBtnSkip');
  prevBtn.style.display = onboardStep > 1 ? 'inline-block' : 'none';
  if (onboardStep === 3) {
    nextBtn.textContent = '开始对话 →';
    skipBtn.style.display = 'none';
    prevBtn.style.display = 'none';
  } else {
    nextBtn.textContent = '下一步 →';
    skipBtn.style.display = 'inline-block';
  }
  // enable next
  var canNext = false;
  if (onboardStep === 1) canNext = !!onboardSelectedTemplate;
  else if (onboardStep === 2) canNext = onboardSelectedMembers.some(function (m) { return m.enabled; });
  else if (onboardStep === 3) canNext = false; // Step 3 自动跑, 不需手动 next
  nextBtn.disabled = !canNext;
  // title icon
  var titleIcon = document.getElementById('onboardWizardIcon');
  if (onboardSelectedTemplate) titleIcon.textContent = onboardSelectedTemplate.icon;
  else titleIcon.textContent = '🦞';
}
function onboardPrev() {
  if (onboardStep > 1) {
    onboardStep--;
    if (onboardStep === 1) renderOnboardTemplates(); // 恢复模板选择高亮
    if (onboardStep === 2) renderOnboardMembers();
    updateOnboardUI();
  }
}
function onboardNext() {
  if (onboardStep === 1) {
    if (!onboardSelectedTemplate) return;
    onboardStep = 2;
    renderOnboardMembers();
    updateOnboardUI();
    return;
  }
  if (onboardStep === 2) {
    var enabled = onboardSelectedMembers.filter(function (m) { return m.enabled; });
    if (enabled.length === 0) {
      showToast('⚠️ 至少选择一位 AI 员工');
      return;
    }
    onboardStep = 3;
    updateOnboardUI();
    finishOnboardAndStartChat(enabled);
    return;
  }
}
// Step 3: 批量创建 + 跳到第 1 位员工聊天
async function finishOnboardAndStartChat(members) {
  var total = members.length;
  var done = 0;
  var created = [];
  var errors = [];
  document.getElementById('onboardConfirmTitle').textContent = '正在创建你的 AI 团队';
  document.getElementById('onboardConfirmDesc').textContent = '批量创建中, 请稍候...';
  updateOnboardProgress(done, total);
  for (var i = 0; i < members.length; i++) {
    var m = members[i];
    try {
      var resp = await apiFetch('/api/agents', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: m.customName,
          role: m.roleKey,           // 后端 _sanitize_role 校验
          avatar: m.avatar,           // emoji 直接传
          systemPrompt: m.systemPrompt,
          status: 'online',
          permission: 'dev',
          visibility: 'creator'       // = personal scope (后端默认)
        })
      });
      if (resp && resp.ok) {
        var data = await resp.json();
        created.push(data);
      } else {
        var errText = '';
        try { var _ej = await resp.json(); errText = _ej.error || ''; } catch (e2) {}
        errors.push({ name: m.customName, error: errText || ('HTTP ' + (resp ? resp.status : '?')) });
      }
    } catch (e) {
      errors.push({ name: m.customName, error: e.message || String(e) });
    }
    done++;
    updateOnboardProgress(done, total);
  }
  if (created.length === 0) {
    document.getElementById('onboardConfirmTitle').textContent = '❌ 创建失败';
    var firstErr = errors[0] ? errors[0].error : '未知错误';
    document.getElementById('onboardConfirmDesc').textContent = '所有 AI 员工都没创建成功: ' + firstErr;
    showToast('创建失败: ' + firstErr, 'error');
    return;
  }
  if (errors.length > 0) {
    showToast('⚠️ ' + errors.length + ' 位员工创建失败', 'warning');
  }
  // 标 done, 关闭 wizard, 跳到聊天
  localStorage.setItem('sb_onboarding_done', '1');
  closeOnboardWizard();
  // 刷新员工列表
  if (typeof loadEmployees === 'function') {
    await loadEmployees();
  } else if (typeof refreshEmployeeList === 'function') {
    await refreshEmployeeList();
  }
  // 跳到第 1 位员工聊天
  var firstAgent = created[0];
  if (firstAgent && typeof openChat === 'function') {
    openChat(firstAgent.id);
  } else if (typeof showToast === 'function') {
    showToast('✅ 已创建 ' + created.length + ' 位 AI 员工');
  }
}
function updateOnboardProgress(done, total) {
  var pct = total === 0 ? 0 : Math.round(done * 100 / total);
  var bar = document.getElementById('onboardProgressBar');
  var text = document.getElementById('onboardProgressText');
  if (bar) bar.style.width = pct + '%';
  if (text) text.textContent = done + ' / ' + total + '  (' + pct + '%)';
}

// ② 员工列表空状态「了解 AI 员工能做什么」— 弹一个轻量 info modal 列 4 个真实能力
// (数据源: Helen 等 AI 员工真实 systemPrompt 能力, 不编)
function showWhatCanAiEmpDo() {
  var html = '<div class="onboarding-modal-overlay" id="whatCanAiDoOverlay" style="display:flex;">'
    + '<div class="onboarding-modal-panel" style="width:480px;text-align:left;padding:28px 24px;">'
    + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">'
    + '<div style="font-size:17px;font-weight:600;color:var(--color-text-primary,#1C1C1E);">AI 员工能做什么</div>'
    + '<button onclick="document.getElementById(\'whatCanAiDoOverlay\').remove()" style="background:none;border:none;font-size:20px;cursor:pointer;color:var(--color-text-tertiary,#AEAEB2);">✕</button>'
    + '</div>'
    + '<div class="what-can-do-list">'
    + '<div class="what-can-do-item"><div class="what-can-do-mark">入</div><div class="what-can-do-body"><div class="what-can-do-name">截图录入</div><div class="what-can-do-desc">上传一张达人主页截图, 自动识别 + OCR + 写入知识库</div></div></div>'
    + '<div class="what-can-do-item"><div class="what-can-do-mark">匹</div><div class="what-can-do-body"><div class="what-can-do-name">达人-商品匹配分析</div><div class="what-can-do-desc">6 维度(粉丝画像 / 历史 GPM / 客单价 / 品类契合 / 调性 / 履约)评估合作匹配度</div></div></div>'
    + '<div class="what-can-do-item"><div class="what-can-do-mark">选</div><div class="what-can-do-body"><div class="what-can-do-name">选品策略建议</div><div class="what-can-do-desc">基于本周已合作达人的粉丝画像, 推荐下一波选品(品类 + 价格带)</div></div></div>'
    + '<div class="what-can-do-item"><div class="what-can-do-mark">评</div><div class="what-can-do-body"><div class="what-can-do-name">合作价值重新评级</div><div class="what-can-do-desc">基于最新数据(履约率 / 复购 / GMV)重评达人合作价值 A/B/C/D</div></div></div>'
    + '</div>'
    + '<div style="display:flex;justify-content:flex-end;margin-top:20px;">'
    + '<button class="onboarding-modal-btn primary" style="flex:0 0 auto;padding:9px 22px;" onclick="document.getElementById(\'whatCanAiDoOverlay\').remove();openWizard();">立即创建</button>'
    + '</div>'
    + '</div></div>';
  var wrap = document.createElement('div');
  wrap.innerHTML = html;
  document.body.appendChild(wrap.firstChild);
}

// 登录
async function _fullLogin() {
  var username = document.getElementById('loginUsername').value.trim();
  var password = document.getElementById('loginPassword').value;
  var errorDiv = document.getElementById('loginError');
  var btn = document.getElementById('loginBtn');
  // ★ ui/login-v21-redesign: 进入函数时确认 button 已是 loading 态 (doLogin 已设)
  // 这里补一次 (防止直接调 _fullLogin 而不走 doLogin)
  if (btn) { btn.disabled = true; btn.classList.add('loading'); }
  var resetBtn = function () {
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
  };
  if (!username || !password) {
    if (errorDiv) {
      errorDiv.textContent = '请输入用户名和密码';
      errorDiv.classList.add('show');
    }
    resetBtn();
    return;
  }
  try {
    const resp = await apiFetch('/api/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        username,
        password
      })
    });
    if (!resp) {
      if (errorDiv) {
        errorDiv.textContent = '用户名或密码错误';
        errorDiv.classList.add('show');
      }
      resetBtn();
      return;
    }
    const data = await resp.json();
    if (data.token) {
      currentUser = {
        id: data.user.id,
        username: data.user.username,
        name: data.user.displayName || data.user.username,
        displayName: data.user.displayName || data.user.username,
        role: data.user.role || 'employee',
        avatar: data.user.avatar || 0,
        createdAt: data.user.createdAt,
        teamIds: data.user.teamIds || [],
        managedTeamIds: data.user.managedTeamIds || [],
        roleTemplateId: data.user.roleTemplateId || null,
        permissions: data.user.permissions || null
      };
      // Leader角色加载可见小组
      if (isLeader() && typeof loadVisibleTeams === 'function') {
        loadVisibleTeams();
      }
      authToken = data.token;
      localStorage.setItem('sb_current_user', JSON.stringify(currentUser));
      localStorage.setItem('sb_auth_token', data.token);
      if (errorDiv) errorDiv.classList.remove('show');
      // 成功: 重置按钮态 (button 即将被 hideLogin 移除, 但保险起见)
      resetBtn();
      showMainApp();
      showToast('✅ 登录成功，欢迎 ' + (currentUser.displayName || currentUser.name || currentUser.username));
    } else {
      if (errorDiv) {
        errorDiv.textContent = data.error || '登录失败，请检查用户名和密码';
        errorDiv.classList.add('show');
      }
      resetBtn();
    }
  } catch (e) {
    // 后端未启动，降级到本地模式
    console.warn('后端不可用，降级到本地模式', e);
    var isAdminUser = username === 'admin';
    // 本地模式角色判断
    var detectedRole = isAdminUser ? 'admin' : 'employee';
    currentUser = {
      id: 'local_' + Date.now(),
      username: username,
      name: isAdminUser ? '管理员' : username,
      displayName: isAdminUser ? '管理员' : username,
      role: detectedRole,
      avatar: isAdminUser ? 0 : 1,
      createdAt: new Date().toISOString(),
      roleTemplateId: detectedRole,
      permissions: {
        modules: {
          dashboard: true, messages: true, employees: true, groups: true,
          knowledge: true, products: true, influencers: true, matches: true, settings: true
        },
        knowledgeCategories: ['*']
      }
    };
    authToken = 'local_mode';
    localStorage.setItem('sb_current_user', JSON.stringify(currentUser));
    localStorage.setItem('sb_auth_token', 'local_mode');
    if (errorDiv) errorDiv.classList.remove('show');
    // ★ ui/login-v21-redesign: 本地模式 fallback 也恢复 button
    if (btn) { btn.disabled = false; btn.classList.remove('loading'); }
    showMainApp();
    showToast('⚠️ 后端未连接，使用本地模式');
  }
}

// 退出
function doLogout() {
  currentUser = null;
  authToken = null;
  localStorage.removeItem('sb_current_user');
  localStorage.removeItem('sb_auth_token');
  showLogin();
  showToast('👋 已退出登录');
}

// 权限判断
function isAdmin() {
  return currentUser && currentUser.role === 'admin';
}
function isEmployee() {
  return currentUser && currentUser.role === 'employee';
}
function isLeader() {
  return currentUser && currentUser.role === 'leader';
}
function isLocalMode() {
  return !authToken || authToken === 'local_mode';
}
function getCurrentUserRoleDisplay() {
  if (!currentUser) return '用户';
  if (currentUser.role === 'admin') return '老板/负责人';
  if (currentUser.role === 'leader') return '组长';
  return '员工';
}

// 权限守卫函数
function requireRole(requiredRole) {
  if (!currentUser) return false;
  if (requiredRole === 'admin') return currentUser.role === 'admin';
  if (requiredRole === 'leader') return currentUser.role === 'admin' || currentUser.role === 'leader';
  return true;
}

function hasModulePermission(module) {
  if (!currentUser) return false;
  if (isAdmin()) return true;
  var perms = currentUser.permissions;
  if (!perms || !perms.modules) return true; // 未配置权限时默认放行（兼容旧数据）
  // 后端未下发的模块默认放行，避免新增模块后旧权限数据误拒
  if (!(module in perms.modules)) return true;
  return !!perms.modules[module];
}

function getAllowedKnowledgeCategories() {
  if (!currentUser) return [];
  var perms = currentUser.permissions;
  if (!perms || !Array.isArray(perms.knowledgeCategories)) return ['*'];
  return perms.knowledgeCategories;
}

function hasKnowledgeCategoryPermission(category) {
  if (!currentUser) return false;
  if (isAdmin()) return true;
  var cats = getAllowedKnowledgeCategories();
  if (cats.indexOf('*') > -1) return true;
  return cats.indexOf(category || '') > -1;
}

// 应用权限控制
function applyPermissions() {
  // 处理 data-admin-only 属性
  document.querySelectorAll('[data-admin-only]').forEach(function (el) {
    el.style.display = isAdmin() ? '' : 'none';
  });

  // 处理 data-employee-only 属性
  document.querySelectorAll('[data-employee-only]').forEach(function (el) {
    el.style.display = isEmployee() ? '' : 'none';
  });

  // 处理 data-leader-only 属性（leader和admin可见）
  document.querySelectorAll('[data-leader-only]').forEach(function (el) {
    el.style.display = isAdmin() || isLeader() ? '' : 'none';
  });

  // 二期：按模块权限显示/隐藏入口
  document.querySelectorAll('[data-module]').forEach(function (el) {
    var mod = el.getAttribute('data-module');
    if (mod && !hasModulePermission(mod)) {
      el.style.display = 'none';
    }
  });

  // 员工视图：隐藏部分管理功能（保留旧逻辑兼容）
  if (isEmployee()) {
    // 员工也可以新增员工（创建自己的AI助手）
    // 不隐藏新增员工按钮

    // 隐藏系统设置入口（仅管理员可配置）
    var settingsBtn = document.querySelector('button[onclick="showSettings()"]');
    if (settingsBtn && !hasModulePermission('settings')) settingsBtn.style.display = 'none';
  }
}

// 更新顶部栏用户信息
function updateUserInfo() {
  if (!currentUser) return;
  var navAvatar = document.getElementById('navUserAvatar');
  var dropdownName = document.getElementById('dropdownUserName');
  var dropdownRole = document.getElementById('dropdownUserRole');
  var dropdownAdminItem = document.getElementById('dropdownAdminItem');
  var initial = (currentUser.displayName || currentUser.name || '?').charAt(0).toUpperCase();
  if (navAvatar) navAvatar.textContent = initial;
  if (dropdownName) dropdownName.textContent = escapeHtml(currentUser.displayName || currentUser.name || '用户');
  if (dropdownRole) dropdownRole.textContent = getCurrentUserRoleDisplay();
  if (dropdownAdminItem) dropdownAdminItem.style.display = isAdmin() ? '' : 'none';
  var dropdownSecurityItem = document.getElementById('dropdownSecurityItem');
  if (dropdownSecurityItem) dropdownSecurityItem.style.display = isAdmin() ? '' : 'none';
}
function escapeHtml(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
function escapeAttr(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// ② 智能时间分隔 helper: 根据与上一条消息的时间差, 返回分隔 HTML 或 ''
// 规则: 跨日(本地时区) → "今天" / "昨天" / "前天" / "MM-DD" / "YYYY-MM-DD"
//       同日间隔 > 30 分钟 → "HH:MM"
//       旧 fallback: 无 time 字段 → "今天" 兜底
function formatChatTimeSeparator(prevTime, currTime) {
  if (!currTime) return '<div class="time-separator"><span>今天</span></div>';
  var d = new Date(currTime);
  if (isNaN(d.getTime())) return '<div class="time-separator"><span>今天</span></div>';
  var pad = function (n) { return n < 10 ? '0' + n : n; };
  var now = new Date();
  var dStart = new Date(d); dStart.setHours(0, 0, 0, 0);
  var nowStart = new Date(now); nowStart.setHours(0, 0, 0, 0);
  var dayDiff = Math.floor((nowStart - dStart) / (1000 * 60 * 60 * 24));
  var dayLabel;
  if (dayDiff === 0) dayLabel = '今天';
  else if (dayDiff === 1) dayLabel = '昨天';
  else if (dayDiff === 2) dayLabel = '前天';
  else if (dayDiff <= 7) dayLabel = dayDiff + ' 天前';
  else dayLabel = d.getFullYear() === now.getFullYear() ? (pad(d.getMonth() + 1) + '-' + pad(d.getDate())) : (d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()));
  // 第一条消息(无 prev)或跨日 → 显示日期分隔
  if (!prevTime) return '<div class="time-separator"><span>' + dayLabel + '</span></div>';
  var pd = new Date(prevTime);
  if (isNaN(pd.getTime())) return '<div class="time-separator"><span>' + dayLabel + '</span></div>';
  // 跨日判断(用本地时区, 不比较 dayDiff 而是同一天)
  var sameDay = (d.getFullYear() === pd.getFullYear() && d.getMonth() === pd.getMonth() && d.getDate() === pd.getDate());
  if (!sameDay) return '<div class="time-separator"><span>' + dayLabel + '</span></div>';
  // 同日: 间隔 > 30 分钟 → 显示时间分隔
  var gapMin = Math.abs(d.getTime() - pd.getTime()) / (1000 * 60);
  if (gapMin > 30) {
    var h = pad(d.getHours()), min = pad(d.getMinutes());
    return '<div class="time-separator"><span>' + h + ':' + min + '</span></div>';
  }
  return '';
}

// 格式化相对时间：今天 HH:mm，昨天 昨天 HH:mm，前天到本周 周X HH:mm，更早 MM-DD HH:mm，跨年 YYYY-MM-DD HH:mm
function formatRelativeTime(ts) {
  if (!ts) return '';
  var d = new Date(ts);
  if (isNaN(d.getTime())) return '';
  var now = new Date();
  var pad = function(n) { return n < 10 ? '0' + n : n; };
  var y = d.getFullYear(), m = d.getMonth() + 1, day = d.getDate();
  var h = pad(d.getHours()), min = pad(d.getMinutes());
  var timePart = h + ':' + min;
  var absTime = y + '-' + pad(m) + '-' + pad(day) + ' ' + timePart;
  // 今天
  if (y === now.getFullYear() && m === now.getMonth() + 1 && day === now.getDate()) {
    return '今天 ' + timePart;
  }
  // 昨天
  var yesterday = new Date(now); yesterday.setDate(yesterday.getDate() - 1);
  if (y === yesterday.getFullYear() && m === yesterday.getMonth() + 1 && day === yesterday.getDate()) {
    return '昨天 ' + timePart;
  }
  // 2-7 天前
  var dStart = new Date(d); dStart.setHours(0, 0, 0, 0);
  var nowStart = new Date(now); nowStart.setHours(0, 0, 0, 0);
  var dayDiff = Math.floor((nowStart - dStart) / (1000 * 60 * 60 * 24));
  if (dayDiff >= 2 && dayDiff <= 7) {
    return dayDiff + '天前';
  }
  // 超过 7 天显示绝对时间
  return absTime;
}

// 格式化日期时间戳（统一使用相对时间）
function formatDate(ts) {
  return formatRelativeTime(ts);
}

// 格式化消息内容（支持代码块、表格、列表、标题、加粗、行内代码、链接等）
function formatMessageContent(text) {
  if (!text) return '';

  // 处理代码块 ```code```
  var parts = text.split(/(```[\s\S]*?```)/g);
  var result = '';
  for (var i = 0; i < parts.length; i++) {
    var part = parts[i];
    if (part.indexOf('```') === 0) {
      // 代码块
      var codeContent = part.slice(3, -3).trim();
      var firstLine = codeContent.split('\n')[0];
      var lang = '';
      var code = codeContent;
      if (firstLine && firstLine.indexOf(' ') === -1 && firstLine.length < 20) {
        lang = firstLine;
        code = codeContent.slice(firstLine.length).trim();
      }
      var langLabel = lang ? '<div class="code-lang">' + escapeHtml(lang) + '</div>' : '';
      result += '<div class="code-block">' + langLabel + '<pre><code>' + escapeHtml(code) + '</code></pre><button class="code-copy-btn" onclick="copyCode(this)">📋 复制</button></div>';
    } else {
      result += formatMessageBlock(part);
    }
  }
  return result;
}

// 块级 Markdown 处理：代码块之外，依次识别表格、标题、列表和普通段落
function formatMessageBlock(text) {
  if (!text) return '';
  var lines = text.split('\n');
  var out = [];
  var lastText = false;
  function addBlock(html) { out.push(html); lastText = false; }
  function addText(html) { if (lastText) out.push('<br>'); out.push(html); lastText = true; }

  var i = 0;
  while (i < lines.length) {
    // 表格
    var tableEnd = findMarkdownTableEnd(lines, i);
    if (tableEnd > i) {
      addBlock(renderMarkdownTable(lines.slice(i, tableEnd)));
      i = tableEnd;
      continue;
    }

    // 标题
    var hMatch = lines[i].match(/^(#{1,6})\s+(.*)$/);
    if (hMatch) {
      var levelMap = {'1':'2','2':'3','3':'4','4':'4','5':'5','6':'6'};
      var level = levelMap[String(hMatch[1].length)] || '4';
      addBlock('<h' + level + '>' + formatMessageInline(hMatch[2]) + '</h' + level + '>');
      i++;
      continue;
    }

    // 无序列表（支持 - * ✓ ! 等标记）
    var ulMatch = lines[i].match(/^(\s*)[-*✓!]\s+(.*)$/);
    if (ulMatch) {
      var items = [];
      while (i < lines.length) {
        var m = lines[i].match(/^(\s*)[-*✓!]\s+(.*)$/);
        if (!m) break;
        items.push('<li>' + formatMessageInline(m[2]) + '</li>');
        i++;
      }
      addBlock('<ul>' + items.join('') + '</ul>');
      continue;
    }

    // 有序列表
    var olMatch = lines[i].match(/^(\s*)\d+\.\s+(.*)$/);
    if (olMatch) {
      var items = [];
      while (i < lines.length) {
        var m = lines[i].match(/^(\s*)\d+\.\s+(.*)$/);
        if (!m) break;
        items.push('<li>' + formatMessageInline(m[2]) + '</li>');
        i++;
      }
      addBlock('<ol>' + items.join('') + '</ol>');
      continue;
    }

    // 空行
    if (lines[i].trim() === '') {
      if (lastText) out.push('<br>');
      out.push('<br>');
      lastText = false;
      i++;
      continue;
    }

    // 普通段落行
    addText(formatMessageInline(lines[i]));
    i++;
  }

  return out.join('');
}

// 判断从 start 行开始是否是一个 Markdown 表格，返回表格结束行号（不含）
function findMarkdownTableEnd(lines, start) {
  if (start >= lines.length) return start;
  var first = lines[start].trim();
  if (!first.startsWith('|') || !first.endsWith('|')) return start;
  if (start + 1 >= lines.length) return start;
  var second = lines[start + 1].trim();
  if (!second.startsWith('|') || !second.endsWith('|')) return start;
  // 第二行必须是分隔符，如 |---|---|
  var sepCells = second.slice(1, -1).split('|');
  if (!sepCells.every(function(c){ return /^\s*:?-+:?\s*$/.test(c); })) return start;
  var end = start + 2;
  while (end < lines.length) {
    var line = lines[end].trim();
    if (!line.startsWith('|') || !line.endsWith('|')) break;
    end++;
  }
  return end;
}

// 把 Markdown 表格行数组渲染成 HTML
function renderMarkdownTable(tableLines) {
  if (!tableLines || tableLines.length < 2) return '';
  var headerLine = tableLines[0].trim();
  var sepLine = tableLines[1].trim();
  var headers = headerLine.slice(1, -1).split('|').map(function(h){ return h.trim(); });
  var aligns = sepLine.slice(1, -1).split('|').map(function(c){
    c = c.trim();
    if (c.startsWith(':') && c.endsWith(':')) return 'center';
    if (c.endsWith(':')) return 'right';
    return 'left';
  });
  var html = '<table class="md-table"><thead><tr>';
  headers.forEach(function(h, idx){
    html += '<th style="text-align:' + (aligns[idx] || 'left') + '">' + formatMessageInline(h) + '</th>';
  });
  html += '</tr></thead><tbody>';
  for (var i = 2; i < tableLines.length; i++) {
    var cells = tableLines[i].trim().slice(1, -1).split('|');
    html += '<tr>';
    cells.forEach(function(cell, idx){
      html += '<td style="text-align:' + (aligns[idx] || 'left') + '">' + formatMessageInline(cell.trim()) + '</td>';
    });
    html += '</tr>';
  }
  html += '</tbody></table>';
  return html;
}

// 行内 Markdown 格式化（HTML 转义 + 行内代码 + 加粗/斜体 + 链接）
function formatMessageInline(text) {
  if (!text) return '';
  // 先按行内代码 `...` 分段，避免代码内容被转义
  var tokens = text.split(/(`[^`]+`)/g);
  var out = '';
  for (var i = 0; i < tokens.length; i++) {
    var tok = tokens[i];
    if (tok.charAt(0) === '`' && tok.charAt(tok.length - 1) === '`') {
      out += '<code class="inline-code">' + escapeHtml(tok.slice(1, -1)) + '</code>';
    } else {
      var s = escapeHtml(tok);
      // 加粗 **text**
      s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
      // 斜体 *text*（不与加粗冲突）
      s = s.replace(/(^|[\s(])\*([^*\s][^*]*?)\*($|[\s)])/g, '$1<em>$2</em>$3');
      // 链接 [text](url) — ★ XSS 修复：URL 必须走白名单 scheme + escapeAttr，否则原样显示文本
      s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, function(_match, linkText, linkUrl) {
        var trimmed = String(linkUrl).trim().toLowerCase();
        // 仅允许 http/https/mailto/相对路径/锚点
        var isSafe = /^(https?:\/\/|mailto:|\/|#)/.test(trimmed);
        if (!isSafe) {
          // 不安全 scheme（javascript:/data:/vbscript:）→ 当成普通文本，不生成链接
          return '[' + escapeHtml(linkText) + '](' + escapeHtml(linkUrl) + ')';
        }
        return '<a href="' + escapeAttr(linkUrl) + '" target="_blank" rel="noopener noreferrer">' + escapeHtml(linkText) + '</a>';
      });
      // 独立行 --- 替换为 hr divider (AI 报告里的 Markdown 分隔符)
      s = s.replace(/^---$/gm, '<hr class="ai-inline-divider">');
      out += s;
    }
  }
  return out;
}

// 复制代码
function copyCode(btn) {
  var code = btn.parentElement.querySelector('code');
  if (code) {
    var text = code.textContent;
    if (navigator.clipboard) {
      navigator.clipboard.writeText(text).then(function () {
        btn.textContent = '✅ 已复制';
        setTimeout(function () {
          btn.textContent = '📋 复制';
        }, 2000);
      });
    } else {
      // fallback
      var ta = document.createElement('textarea');
      ta.value = text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      btn.textContent = '✅ 已复制';
      setTimeout(function () {
        btn.textContent = '📋 复制';
      }, 2000);
    }
  }
}

// ========== 图片处理 ==========
function compressImageToBase64(file) {
  return new Promise(function(resolve, reject) {
    var reader = new FileReader();
    reader.onload = function(e) {
      var img = new Image();
      img.onload = function() {
        var canvas = document.createElement('canvas');
        var maxWidth = 4096;  // ★ fix/image-upload-no-compress: 1920→4096 (4K 屏保留, 微信截图通常 1080-2560 都不压缩)
        var width = img.width;
        var height = img.height;
        if (width > maxWidth) {
          height = Math.round(height * maxWidth / width);
          width = maxWidth;
        }
        canvas.width = width;
        canvas.height = height;
        var ctx = canvas.getContext('2d');
        // JPEG 不支持透明通道，先铺白底避免透明图转黑
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect(0, 0, width, height);
        ctx.drawImage(img, 0, 0, width, height);
        // ★ fix/image-upload-no-compress: JPEG 0.9 → 0.95 (5% 质量损失几乎不可见, 仍走 JPEG 避免 9 张原图 PNG base64 把请求体撑爆后端 413)
        var base64 = canvas.toDataURL('image/jpeg', 0.95);
        resolve({base64: base64, filename: file.name});
      };
      img.onerror = reject;
      img.src = e.target.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}
function handleImageFiles(files) {
  if (!files || files.length === 0) return;
  var remaining = 9 - window._pendingImages.length;
  if (remaining <= 0) {
    showToast('最多只能选择9张图片');
    return;
  }
  var toProcess = Array.prototype.slice.call(files, 0, remaining);
  toProcess.forEach(function(file) {
    if (!file.type.startsWith('image/')) return;
    compressImageToBase64(file).then(function(img) {
      window._pendingImages.push(img);
      renderImagePreview();
    }).catch(function(e) {
      console.warn('图片处理失败:', e);
    });
  });
}
function renderImagePreview() {
  var bar = document.getElementById('imagePreviewBar');
  if (!bar) return;
  if (window._pendingImages.length === 0) {
    bar.innerHTML = '';
    bar.style.display = 'none';
  } else {
    bar.style.display = 'flex';
    bar.innerHTML = window._pendingImages.map(function(img, idx) {
      return '<div class="chat-image-preview-item"><img src="' + img.base64 + '"><div class="chat-image-preview-remove" onclick="removePendingImage(' + idx + ')">×</div></div>';
    }).join('');
  }
  updateSendButtonState();
}
function removePendingImage(idx) {
  window._pendingImages.splice(idx, 1);
  renderImagePreview();
}
function clearPendingImages() {
  window._pendingImages = [];
  renderImagePreview();
}
function updateSendButtonState() {
  var input = document.getElementById('msgInput');
  var sendBtn = document.getElementById('sendBtn');
  if (!input || !sendBtn) return;
  sendBtn.disabled = !input.value.trim() && (!window._pendingImages || window._pendingImages.length === 0);
}
function buildChatImagesHtml(images) {
  if (!images || images.length === 0) return '';
  var imgs = images.map(function(img) {
    var src = img.base64 || img;
    return '<img src="' + src + '" class="chat-image-thumb" onclick="previewImage(this.src)">';
  }).join('');
  return '<div class="chat-images">' + imgs + '</div>';
}
function previewImage(src) {
  var overlay = document.getElementById('imagePreviewOverlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'imagePreviewOverlay';
    overlay.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:99999;display:flex;align-items:center;justify-content:center;cursor:pointer;';
    overlay.onclick = function() { overlay.style.display = 'none'; overlay.querySelector('img').src = ''; };
    var img = document.createElement('img');
    img.style.cssText = 'max-width:90%;max-height:90%;border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,0.3);';
    overlay.appendChild(img);
    document.body.appendChild(overlay);
  }
  overlay.querySelector('img').src = src;
  overlay.style.display = 'flex';
}
function saveImageMemory() {
  var toast = document.getElementById('memoryImageToast');
  if (toast) toast.remove();
  var empId = localStorage.getItem('sb_current_emp');
  if (!empId) return;
  var lastReply = window._lastAIReplyText || '';
  if (!lastReply) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId), {
    method: 'POST',
    body: JSON.stringify({key: 'auto_extract', value: '图片内容：' + lastReply})
  }).catch(function(e) {});
}
function dismissImageMemory() {
  var toast = document.getElementById('memoryImageToast');
  if (toast) toast.remove();
}

// ========== 发送消息 ==========
async function sendMsg() {
  var input = document.getElementById('msgInput');
  var text = input.value.trim();
  var pendingImages = window._pendingImages.slice();
  if (!text && !pendingImages.length) return;

  // 群聊模式：不走个人聊天状态流，直接转发
  if (currentGroupId) {
    var mentions = [];
    // @提及名字中不能包含中英文标点、括号、另一 @ 或空格
    var mentionRegex = /@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]{1,20})/g;
    var m;
    while ((m = mentionRegex.exec(text)) !== null) {
      // 跨 emps + groups.members 查找被 @ 的成员，支持带后缀/语气词的 @
      var mentionedEmp = resolveMentionedName(m[1]);
      if (mentionedEmp) mentions.push(mentionedEmp.id);
    }
    input.value = '';
    var md = document.getElementById('mentionDropdown');
    if (md) md.classList.remove('active');
    var sm = document.getElementById('slashMenu');
    if (sm) sm.classList.remove('active');
    clearPendingImages();
    updateSendButtonState();
    sendGroupMessage(currentGroupId, text, mentions, pendingImages);
    return;
  }

  // ===== 个人聊天状态管理 =====
  var _curEmpId = localStorage.getItem('sb_current_emp');
  var _curEmp = emps.find(function (e) { return e.id === _curEmpId; });

  // 当前员工已被删除/不存在
  if (!_curEmp) {
    closeCurrentChatAsDeleted();
    return;
  }

  // 统一恢复函数：任何异常退出都要调用
  function restoreOnline() {
    window._thinkingTimeouts = window._thinkingTimeouts || {};
    clearTimeout(window._thinkingTimeouts[_curEmpId]);
    delete window._thinkingTimeouts[_curEmpId];
    notifyOfficeStatusChange(_curEmpId, 'online');
    if (_curEmp) {
      _curEmp.status = 'online';
      renderEmployeeList();
    }
  }

  // 多图重任务旁路等待：后端 HeavyPipe（Python 线程）正在做 vision 识别+达人预查+深度分析，
  // 不占用 OpenClaw gateway；每 5s 轮询 heavy-status，done 则刷新聊天记录，
  // failed/超时则降级回 OpenClaw 主干道重发
  function _waitHeavyPipe(empInfo, jobId, placeholderText) {
    var _tp = document.getElementById('typingMsg');
    if (_tp) {
      var bubble = _tp.querySelector('.msg-bubble');
      // 优先用后端落库的占位文案（与完成后聊天记录里的一致），无则本地兜底
      var phText = placeholderText || ('🔬 正在深度分析 ' + pendingImages.length + ' 张图片（专属通道，不影响其他对话）…');
      if (bubble) bubble.innerHTML = '<span style="color:#888">' + escapeHtml(phText) + '</span>';
    }
    // 旁路分析可能跑几分钟，清掉 60s 思考超时保护
    if (window._thinkingTimeouts && window._thinkingTimeouts[_curEmpId]) {
      clearTimeout(window._thinkingTimeouts[_curEmpId]);
      delete window._thinkingTimeouts[_curEmpId];
    }
    var polls = 0;
    var maxPolls = 120; // 5s × 120 = 10 分钟
    var timer = setInterval(async function () {
      polls++;
      var status = null, errMsg = '', stageText = '';
      try {
        var resp = await apiFetch('/api/chat/' + encodeURIComponent(empInfo.id) + '/heavy-status?jobId=' + encodeURIComponent(jobId));
        if (resp && resp.ok) {
          var data = await resp.json();
          status = data && data.status;
          errMsg = (data && data.error) || '';
          stageText = (data && data.stage) || '';
        }
      } catch (e) {
        console.warn('[HeavyPipe] 状态轮询异常（继续等待）:', e);
      }
      // 只有明确 failed 或轮询超时才降级；status 为 null（网络抖动/服务重启后任务丢失）
      // 时继续等待，避免误触发 OpenClaw 主干道重发导致重复回复
      if (status === 'done') {
        clearInterval(timer);
        var t = document.getElementById('typingMsg');
        if (t) t.remove();
        renderMsgs('private'); // 重新加载聊天记录（含 HeavyPipe 落库的 assistant 回复）
        restoreOnline();
        console.log('[HeavyPipe] 分析完成 jobId=' + jobId);
        return;
      }
      if (status === 'failed' || polls >= maxPolls) {
        clearInterval(timer);
        console.warn('[HeavyPipe] 旁路失败（' + (errMsg || (polls >= maxPolls ? 'timeout' : '状态丢失')) + '），降级回 OpenClaw 主干道 jobId=' + jobId);
        sendViaOpenClaw(empInfo, text, docContext, timeStr, pendingImages, 0, null, talentInjection, ocInjection, ocTags);
        return;
      }
      // analyzing / null：占位不是回复，是状态提示——保持思考中提示条，实时刷新阶段文案
      if (stageText) {
        var tp2 = document.getElementById('typingMsg');
        var bubble2 = tp2 && tp2.querySelector('.msg-bubble');
        if (bubble2) bubble2.innerHTML = '<span style="color:#888">' + escapeHtml(stageText) + '</span>';
      }
    }, 5000);
  }

  // 通知龙虾办公室用户发送了消息
  notifyOfficeChatMessage(_curEmpId, 'outgoing');
  // 标记员工为思考中
  notifyOfficeStatusChange(_curEmpId, 'thinking');
  if (_curEmp) {
    _curEmp.status = 'thinking';
    renderEmployeeList();
  }

  // 超时保护：60秒后自动恢复
  window._thinkingTimeouts = window._thinkingTimeouts || {};
  clearTimeout(window._thinkingTimeouts[_curEmpId]);
  window._thinkingTimeouts[_curEmpId] = setTimeout(function () {
    var _te = emps.find(function (e) { return e.id === _curEmpId; });
    if (_te && _te.status === 'thinking') {
      _te.status = 'online';
      renderEmployeeList();
      notifyOfficeStatusChange(_curEmpId, 'online');
      console.debug('[Timeout] ' + _te.name + ' 思考超时，恢复在线');
    }
  }, 60000);

  var area = document.getElementById('messagesArea');
  var now = new Date();
  var timeStr = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0');
  var emp = getCurrentEmployeeInfo();
  if (!emp || !emp.id) {
    showToast('⚠️ 未选择员工，无法发送消息');
    restoreOnline();
    return;
  }

  // 清除空状态提示
  const emptyState = area.querySelector('.chat-empty-state');
  if (emptyState) emptyState.remove();
  // 清除旧的 time-separator 如果存在
  const oldSep = area.querySelector('.time-separator');
  if (!oldSep) {
    area.insertAdjacentHTML('afterbegin', '<div class="time-separator"><span>今天</span></div>');
  }

  // 添加用户消息到 DOM（聊天记录由后端统一存储）
  // 防御性校验：如果用户已切换员工，不再往当前 DOM 追加消息，避免串渲染
  if (localStorage.getItem('sb_current_emp') !== emp.id) {
    console.warn('[sendMsg] 员工已切换，跳过用户消息 DOM 渲染:', emp.id);
    input.value = '';
    clearPendingImages();
    updateSendButtonState();
    restoreOnline();
    return;
  }
  const userMsg = document.createElement('div');
  userMsg.className = 'msg own';
  var imagesHtml = buildChatImagesHtml(pendingImages);
  // 引用块：发送时把待引用消息渲染到气泡顶部，并随消息体携带 reply_to
  var ownQuoteHtml = window._pendingQuote ? buildQuoteBlockHtml(window._pendingQuote) : '';
  userMsg.innerHTML = '<div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">你</span><span class="msg-sender-time">' + timeStr + '</span></div><div class="msg-bubble">' + ownQuoteHtml + formatMessageContent(text || '') + '</div>' + imagesHtml + '</div>';
  area.appendChild(userMsg);
  // ③ 发送状态管理: 用户消息进 DOM 时标 sending + 缓存 text/images 用于重试
  userMsg.setAttribute('data-status', 'sending');
  userMsg.setAttribute('data-pending-text', text || '');
  // images 不直接存 attribute(太大), 只存 URL 列表(已在 imagesHtml 渲染, 重试时无法重新发送图片)
  try { userMsg.setAttribute('data-pending-images', JSON.stringify(pendingImages.map(function (img) { return img.url || img.dataUrl || ''; }))); } catch (e) { userMsg.setAttribute('data-pending-images', '[]'); }
  // 立即保存用户消息到服务器，避免切换员工时消息丢失（AI 回复由 saveChatToServer 再存）
  // 达人防编造（架构级）：后端保存消息时检测达人关键词，命中则直查 talents 表并随响应返回
  // talentInjection（【系统数据】），由前端拼进发给 OpenClaw 的消息，LLM 不参与数据查询
  var talentInjection = '';
  // 〔r81 批注① 2026-10-08〕MS3 召回（记忆/知识/规律）：后端响应带回注入文本 + 条数统计，
  // 注入文本拼进发给 OpenClaw 的消息（真注入），条数挂到 assistant 消息供气泡上方 chip 渲染
  var ocInjection = '';
  var ocTags = null;
  var heavyJobId = null;
  var heavyPlaceholder = '';
  var replyToId = window._pendingQuote ? window._pendingQuote.id : null;
  try {
    var saveResp = await apiFetch('/api/chat/' + encodeURIComponent(emp.id), {
      method: 'POST',
      body: JSON.stringify({ role: 'user', content: text, skipAI: true, empId: emp.id, images: pendingImages, reply_to: replyToId || undefined })
    });
    if (saveResp && saveResp.ok) {
      var saveData = await saveResp.json();
      if (saveData && saveData.talentInjection) talentInjection = saveData.talentInjection;
      if (saveData && saveData.openclawInjection) ocInjection = saveData.openclawInjection;
      if (saveData && saveData.injectionTags) ocTags = saveData.injectionTags;
      // 回填消息 id 到刚插入的用户气泡（右键引用依赖 data-msg-id + _msgQuotePool）
      // pendingImages 在函数开头已 slice 快照，此处引用不受后面 clearPendingImages() 影响
      var savedMsgId = (saveData && saveData.userMessage && saveData.userMessage.id) || (saveData && saveData.id) || null;
      if (savedMsgId) {
        _registerMsgDom(userMsg, { id: savedMsgId, role: 'user', content: text, images: pendingImages, reply_to: replyToId || null });
      }
      // ③ save 成功: 3s 后移除 sending 状态(无痕过渡到"已发送")
      setTimeout(function () {
        if (userMsg.getAttribute('data-status') === 'sending') userMsg.removeAttribute('data-status');
      }, 3000);
      // 多图（>=2）重任务被后端旁路：不再走 OpenClaw chat.send，改为轮询旁路任务状态
      if (saveData && saveData.heavyPipe) {
        heavyJobId = saveData.jobId || null;
        heavyPlaceholder = (saveData.aiMessage && saveData.aiMessage.content) || '';
      }
    } else {
      // ③ save 失败: 标 failed + 加重试按钮(用户点 retryMessage 重发, 不需重输)
      userMsg.setAttribute('data-status', 'failed');
      var failReason = (saveResp && saveResp.status) ? ('HTTP ' + saveResp.status) : '保存失败';
      userMsg.setAttribute('data-fail-reason', failReason);
      var retryBtn = document.createElement('div');
      retryBtn.className = 'msg-retry-btn';
      retryBtn.textContent = '⚠️ 发送失败 · 点击重试';
      retryBtn.setAttribute('data-msg-id', userMsg.getAttribute('data-msg-id') || '');
      userMsg.appendChild(retryBtn);
      userMsg.addEventListener('click', function onRetryClick(e) {
        if (!e.target.classList.contains('msg-retry-btn')) return;
        e.stopPropagation();
        retryMessage(userMsg);
      });
      // 失败时不让 typing 指示器出现(避免"AI 还在想"的错觉)
      var earlyTyping = document.getElementById('typingMsg');
      if (earlyTyping) earlyTyping.remove();
      restoreOnline();
      showToast('❌ 消息发送失败: ' + failReason);
    }
  } catch (e) {
    // ③ save 异常(网络错误等): 同样标 failed
    console.error('[Chat] 用户消息即时保存失败:', e);
    userMsg.setAttribute('data-status', 'failed');
    userMsg.setAttribute('data-fail-reason', (e && e.message) || '网络错误');
    var retryBtn2 = document.createElement('div');
    retryBtn2.className = 'msg-retry-btn';
    retryBtn2.textContent = '⚠️ 发送失败 · 点击重试';
    userMsg.appendChild(retryBtn2);
    userMsg.addEventListener('click', function (e) {
      if (!e.target.classList.contains('msg-retry-btn')) return;
      e.stopPropagation();
      retryMessage(userMsg);
    });
    var earlyTyping2 = document.getElementById('typingMsg');
    if (earlyTyping2) earlyTyping2.remove();
    restoreOnline();
    showToast('❌ 消息发送失败: ' + ((e && e.message) || '网络错误'));
  }
  input.value = '';
  clearPendingImages();
  clearQuotePreview();
  updateSendButtonState();
  area.scrollTop = area.scrollHeight;

  // 显示打字指示器
  const typing = document.createElement('div');
  typing.className = 'msg typing-msg';
  typing.id = 'typingMsg';
  typing.innerHTML = '<div class="msg-avatar">' + renderAvatar(emp, 32) + '</div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(emp.name || 'AI') + '</span><span class="msg-sender-role">' + escapeHtml(getEmpRoleDisplay(emp)) + '</span></div><div class="msg-bubble"><span class="typing-dots-inline"><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span></span></div></div>';
  area.appendChild(typing);
  area.scrollTop = area.scrollHeight;

  // 获取文档上下文
  const docContext = await getDocContextForChat();

  // 多图（>=3）重任务旁路：后端 HeavyPipe 已在 Python 线程里做分析，
  // 不进 OpenClaw gateway（重活会把 gateway 调度队列堵死）；轮询任务状态取结果
  if (heavyJobId) {
    _waitHeavyPipe(emp, heavyJobId, heavyPlaceholder);
    return;
  }

  // 路由策略（dev/fix-hard-constraint）：
  // 架构硬约束 — 聊天必须经 OpenClaw Gateway，前端不能直连外部 LLM provider API。
  // 之前 "OpenClaw 不可用时降级 API 直连" 的分支会绕过网关，违反硬约束。
  // 改为: OpenClaw 不可用 → 直接报错让用户检查，不静默走直连。
  // 1. 优先走 OpenClaw 网关（支持纯文本和多模态图片）
  // 2. OpenClaw 不可用时 → 报错（不再降级 sendViaAPI 直连）
  // 3. 都没连 → 用 OpenClaw main agent 兜底（如果连上 main）
  var ocOk = typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated;
  var hasImages = pendingImages.length > 0;
  window._lastUserMessageHadImages = hasImages;

  if (ocOk) {
    // 所有消息统一走 OpenClaw 主干道（图片已提前转成文字描述）
    sendViaOpenClaw(emp, text, docContext, timeStr, pendingImages, 0, null, talentInjection, ocInjection, ocTags);
  } else {
    // 即使没有配置AI，也用 OpenClaw main agent 兜底
    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      emp.openclawName = emp.openclawName || 'main';
      sendViaOpenClaw(emp, text, docContext, timeStr, pendingImages, 0, null, talentInjection, ocInjection, ocTags);
    } else {
      setTimeout(function () {
        var t = document.getElementById('typingMsg');
        if (t) t.remove();
        var simReply = '⚠️ OpenClaw 网关未连接，请检查 Gateway 是否运行，或配置 AI 供应商的 API Key。';
        displayAIReply(simReply, docContext, timeStr, emp.id, []);
        saveChatHistory(emp.id, text, simReply, docContext, [], pendingImages);
        restoreOnline();
      }, 500);
    }
  }
}

// 从用户消息中提取关键词
function extractKeywords(text) {
  if (!text) return [];
  var stopWords = ['的', '了', '是', '在', '我', '你', '他', '她', '它', '们', '这', '那', '有', '没', '不', '也', '都', '还', '就', '要', '会', '能', '和', '与', '或', '但', '而', '如果', '因为', '所以', '什么', '怎么', '如何', '为什么', '哪', '谁', '多', '少', '大', '小', '好', '吗', '呢', '吧', '啊', '哦', '嗯', '请', '帮', '给', '让', '把', '被', '从', '到', '对', '为', '以', '上', '下', '中', '里', '来', '去', '过', '着', '地', '得', '说', '想', '看', '做', '用', '可以', '需要', '知道', '一个', '这个', '那个', '时候', '现在', '今天', '明天', '昨天'];
  var words = text.replace(/[，。！？、；：""''【】（）\[\]{},.!?;:'"<>\s\n\r]+/g, '|').split('|');
  var keywords = [];
  for (var i = 0; i < words.length; i++) {
    var w = words[i].trim();
    if (w.length >= 2 && stopWords.indexOf(w) === -1 && keywords.indexOf(w) === -1) {
      keywords.push(w);
    }
  }
  return keywords.slice(0, 8);
}

// OpenClaw 多模态探测工具
async function probeOpenClawMultimodal() {
  // 等待连接建立
  if (!window.openclaw) {
    await new Promise(r => setTimeout(r, 2000));
  }
  if (!window.openclaw) {
    return;
  }
  
  // 如果未连接，尝试连接
  if (!window.openclaw.connected) {
    try {
      await window.openclaw.connect();
    } catch (e) {
    }
    await new Promise(r => setTimeout(r, 2000));
  }
  
  
  // 测试不同的 method（从前端 JS 中发现的候选）
  const methodsToTest = [
    'chat.send',
    'chat.sendMessage', 
    'chat.sendImage',
    'chat.sendMedia',
    'chat.multimodal',
    'chat.composer.attach',
    'chat.composer.start',
    'chat.run',
    'chat.gateway',
    'chat.command',
    'messages.send',
    'sessions.sendMessage',
    'chat.runControls.sendMessage'
  ];
  
  // 测试不同的参数名（针对 chat.send）
  const paramNamesToTest = [
    'image', 'images', 'files', 'file', 
    'attachments', 'media', 'mediaUrls',
    'content', 'messages', 'input', 'parts',
    'image_url', 'imageUrls'
  ];
  
  const testImageBase64 = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==';
  const testSessionKey = 'probe-test-session';
  
  // 先测试 method 是否存在
  for (const method of methodsToTest) {
    try {
      const result = await window.openclaw.send(method, {
        sessionKey: testSessionKey,
        message: 'test'
      });
    } catch (e) {
      const errMsg = e.message || String(e);
      if (errMsg.includes('unexpected property') || errMsg.includes('must have') || errMsg.includes('must be')) {
      } else {
      }
    }
  }
  
  // 测试 chat.send 的不同参数名
  for (const paramName of paramNamesToTest) {
    try {
      const params = {
        sessionKey: testSessionKey,
        message: 'hi'
      };
      // 尝试不同的参数结构
      if (paramName === 'content' || paramName === 'messages') {
        params[paramName] = [{ role: 'user', content: 'hi' }];
      } else if (paramName === 'parts') {
        params[paramName] = [{ text: 'hi' }];
      } else if (paramName === 'input') {
        params[paramName] = [{ type: 'input_text', text: 'hi' }];
      } else {
        params[paramName] = [testImageBase64];
      }
      
      const result = await window.openclaw.send('chat.send', params);
    } catch (e) {
      const errMsg = e.message || String(e);
      if (errMsg.includes('unexpected property') || errMsg.includes('must have') || errMsg.includes('must be')) {
      } else {
      }
    }
  }
  
  // 测试嵌套参数结构
  const nestedTests = [
    { name: 'options.images', params: { sessionKey: testSessionKey, message: 'hi', options: { images: [testImageBase64] } } },
    { name: 'options.files', params: { sessionKey: testSessionKey, message: 'hi', options: { files: [testImageBase64] } } },
    { name: 'meta.images', params: { sessionKey: testSessionKey, message: 'hi', meta: { images: [testImageBase64] } } },
    { name: 'extra.images', params: { sessionKey: testSessionKey, message: 'hi', extra: { images: [testImageBase64] } } },
    { name: 'attachments (objects)', params: { sessionKey: testSessionKey, message: 'hi', attachments: [{ type: 'image', url: testImageBase64 }] } },
    { name: 'message as object (with content array)', params: { sessionKey: testSessionKey, message: { content: [{ type: 'text', text: 'hi' }] } } },
  ];
  
  for (const test of nestedTests) {
    try {
      const result = await window.openclaw.send('chat.send', test.params);
    } catch (e) {
      const errMsg = e.message || String(e);
    }
  }
  
}

// 达人相关关键词（与后端 _TALENT_INJECT_KEYWORDS 保持一致，任一命中即触发数据注入）
var TALENT_INJECT_KEYWORDS = ['达人', '网红', 'KOL', '主播', '带货', '分析', '报告'];
function hasTalentKeyword(text) {
  if (!text) return false;
  var upper = String(text).toUpperCase();
  for (var i = 0; i < TALENT_INJECT_KEYWORDS.length; i++) {
    if (upper.indexOf(TALENT_INJECT_KEYWORDS[i]) !== -1) return true;
  }
  return false;
}

// 通过 OpenClaw WS 发送消息
// 〔r81 批注① 2026-10-08〕ocInjection/ocTags：后端 /api/chat 响应带回的 MS3 召回
// （记忆/知识/规律）注入文本与条数统计 —— 文本真拼进 WS 消息，条数随回复落库供 chip 渲染
async function sendViaOpenClaw(emp, userMessage, docContext, timeStr, images, chainDepth, groupId, systemData, ocInjection, ocTags) {
  images = images || [];
  chainDepth = chainDepth || 0;
  ocInjection = ocInjection || '';
  ocTags = ocTags || null;
  // 达人【系统数据】注入文本，两个来源：
  // 1. sendMsg 保存用户消息时后端随响应返回的 talentInjection（参数传入）；
  // 2. 下方兜底：命中关键词时前端直接调 /api/talents/injection-text 获取。
  systemData = systemData || '';
  // 调试日志①：函数入口，确认注入前置条件的实际取值
  console.log('[TalentInject] sendViaOpenClaw 入口: systemData=' + (systemData ? 'len=' + systemData.length : '(空)') + ', hasTalentKeyword=' + hasTalentKeyword(userMessage) + ', userMessage前20字=' + String(userMessage || '').slice(0, 20));

  // 达人防编造（注入点在发送前）：聊天走 OpenClaw WebSocket，不经过后端
  // _handle_post_chat，后端注入没机会执行，所以命中达人关键词时由前端直接调
  // /api/talents/injection-text（apiFetch 自动带当前用户 auth token），
  // 把后端直查 talents 表生成的【系统数据】拼到消息末尾再发出；
  // 空数据时接口返回带"严禁编造"约束的文本。systemData 已有值时跳过，避免重复注入。
  if (!systemData && hasTalentKeyword(userMessage)) {
    // 调试日志②：确认进入了注入分支
    console.log('[TalentInject] 命中达人关键词，开始获取注入文本...');
    try {
      var injResp = await apiFetch('/api/talents/injection-text');
      // 调试日志③：确认接口响应状态
      console.log('[TalentInject] /api/talents/injection-text 响应: ' + (injResp ? 'status=' + injResp.status : 'null（401或网络失败）'));
      if (injResp && injResp.ok) {
        var injData = await injResp.json();
        if (injData && typeof injData.text === 'string' && injData.text) {
          systemData = injData.text;
          console.log('[TalentInject] sendViaOpenClaw 直接获取注入文本 len=' + systemData.length);
        } else {
          console.warn('[TalentInject] 响应无 text 字段（可能是403被转为空数组）:', injData);
        }
      }
    } catch (injErr) {
      // 调试日志④：打印具体错误（含堆栈）
      console.error('[TalentInject] 注入文本获取异常:', injErr && injErr.stack ? injErr.stack : injErr);
    }
  }

  // OpenClaw 直连不经过后端 /api/proxy，在后端无 WebSocket 回调可拦截，
  // 因此先调用后端意图检测接口，在消息到达 OpenClaw 前完成自修改并追加确认句。
  // 群聊工具链 follow-up 跳过意图检测，避免对工具结果追加确认句。
  if (!groupId) {
    try {
      var intentRes = await apiFetch('/api/agents/' + encodeURIComponent(emp.id) + '/self-update-intent', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content: userMessage})
      });
      if (intentRes && intentRes.ok) {
        var intentData = await intentRes.json();
        if (intentData && intentData.matched && intentData.confirmation) {
          userMessage = intentData.confirmation + '\n\n' + userMessage;
        }
      }
    } catch (intentErr) {
      console.warn('[OpenClaw] self-update intent check failed:', intentErr);
    }
  }

  // 统一恢复员工在线状态
  function restoreOnline() {
    window._thinkingTimeouts = window._thinkingTimeouts || {};
    clearTimeout(window._thinkingTimeouts[emp.id]);
    delete window._thinkingTimeouts[emp.id];
    notifyOfficeStatusChange(emp.id, 'online');
    var _e = emps.find(function (e) { return e.id === emp.id; });
    if (_e) {
      _e.status = 'online';
      renderEmployeeList();
    }
  }
  function setEmpStatus(status) {
    var _e = emps.find(function (e) { return e.id === emp.id; });
    if (_e) {
      _e.status = status;
      renderEmployeeList();
    }
    notifyOfficeStatusChange(emp.id, status);
  }

  // 加载最近对话上下文（OpenClaw session 丢失时恢复历史）
  var chatContext = groupId ? await buildRecentGroupContext(groupId, 10) : await buildRecentChatContext(emp.id, 10);

  // 注入 RAG 检索结果（产品知识库）
  var ragResult = { context: '', citations: [] };
  try {
    ragResult = await retrieveRAGContext(emp.id, userMessage);
  } catch (e) {
    console.warn('[RAG] OpenClaw 路径注入失败:', e);
  }

  var systemPrompt = '';
  // 优先使用 soulDoc（SOUL.md），其次 systemPrompt
  if (emp.soulDoc) systemPrompt = emp.soulDoc;else if (emp.systemPrompt) systemPrompt = emp.systemPrompt;
  if (!systemPrompt) systemPrompt = '你是 ' + emp.name + '，一个 ' + getEmpRoleDisplay(emp) + '。请用第一人称回复，保持角色一致性。不要编造不存在的任务、数据或提醒信息，只基于用户实际提供的内容回复。';
  // 注入当前用户身份信息，明确层级关系，防止 AI 把老板当学生/下属
  var userRole = getCurrentUserRoleDisplay();
  var userName = currentUser && currentUser.name || currentUser && currentUser.displayName || '用户';
  systemPrompt += '\n\n【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- ' + userName + '（' + userRole + '）是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。\n- 严禁用"教你""指导你""你做过吗""你懂吗"等居高临下的语气。\n- 老板问你问题时，直接回答，不要反问或考验老板。';

  // 加载对话摘要（如果有）；群聊工具链 follow-up 不注入单聊摘要
  if (!groupId) {
    var summaryText = '';
    try {
      var summaryRes = await apiFetch('/api/chat/summarize/' + encodeURIComponent(emp.id));
      var summaryData = await summaryRes.json();
      if (summaryData && summaryData.summary) {
        summaryText = '\n\n【历史对话摘要】\n' + summaryData.summary;
      }
    } catch (summaryErr) {
      // 摘要不存在或加载失败，静默忽略
    }
    if (summaryText) systemPrompt += summaryText;
  }

  // 注入商品库/达人库/匹配引擎工具说明（主大脑管控，X-Agent-Id 硬编码为员工ID）
  systemPrompt += buildAgentToolsContext(emp.id);
  if (ragResult.context) systemPrompt += '\n\n' + ragResult.context;
  // 〔r81 批注①〕MS3 记忆/知识/规律召回注入（与 RAG 并列，数据源后端 MS3 管道）
  if (ocInjection) systemPrompt += '\n\n' + ocInjection;

  // 注入团队动态（同项目组其他 AI 最近对话摘要）
  if (!groupId) {
    try {
      var feedRes = await apiFetch('/api/team-feed/' + encodeURIComponent(emp.id));
      console.log('[TeamFeed] apiFetch result:', feedRes ? feedRes.status : 'null');
      if (feedRes && feedRes.ok) {
        var feedData = await feedRes.json();
        console.log('[TeamFeed] feedData:', JSON.stringify(feedData).substring(0, 200));
        if (feedData && feedData.teamFeed) {
          systemPrompt += '\n\n' + feedData.teamFeed;
          console.log('[TeamFeed] 注入成功 len=' + feedData.teamFeed.length);
        }
      }
    } catch (feedErr) {
      console.warn('[TeamFeed] 获取失败:', feedErr);
    }
  }

  // AI 调用前防御：最终 systemPrompt 必须包含身份约束关键字
  if (!validateFinalSystemPrompt(systemPrompt)) {
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    displayAIReply('⚠️ AI身份约束缺失，禁止调用AI', docContext, timeStr, emp.id, [], chainDepth);
    restoreOnline();
    return;
  }

  // 构建消息内容（systemPrompt + 历史上下文 + 用户消息）
  // 图片通过 attachments 参数单独传递，不在 message 字符串中
  var textParts = [];
  if (systemPrompt) textParts.push('【System Prompt】\n' + systemPrompt);
  if (chatContext) textParts.push(chatContext);
  textParts.push('【User Message】\n' + userMessage);
  var fullMessage = textParts.join('\n\n');
  
  async function _doHandle(reply, isError) {
    // 违禁词检查：在渲染前调用后端检查，命中时在气泡上方显示警告条（不拦截，失败静默放行）
    var fwWarning = '';
    try {
      if (reply && !isError && typeof apiFetch === 'function') {
        var fwResp = await apiFetch('/api/forbidden-words/check', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ text: reply })
        });
        if (fwResp) {
          var fwData = await fwResp.json();
          if (fwData && fwData.hasViolation && fwData.words && fwData.words.length) {
            fwWarning = '⚠️ 此回复包含违禁词：' + fwData.words.join('、');
          }
        }
      }
    } catch (fwErr) {
      console.warn('[ForbiddenWords] 检查失败，静默放行:', fwErr);
    }
    console.debug('[OpenClaw] _doHandle 调用 reply_len=' + (reply || '').length + ' isError=' + isError + ' chainDepth=' + chainDepth + ' groupId=' + groupId);
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    if (!reply) reply = '抱歉，我暂时无法处理这个问题，请稍后重试或换一种方式提问。';
    if (isError && !reply) reply = '⚠️ AI 回复出错: 未知错误';
    var msgArea = document.getElementById('messagesArea');

    if (window._streamingActive) {
      // ★ dev/feat: #7 streaming 模式 — 占位节点已在 onStream 首次创建,
      //    这里只做最终补全 (兜底空 bubble + doc hint) + 违禁词条, 避免重复插入节点
      var _streamMsg = window._streamingMsgEl || (msgArea && msgArea.lastElementChild);
      if (_streamMsg && _streamMsg.querySelector) {
        var _streamBubble = _streamMsg.querySelector('.msg-bubble');
        if (_streamBubble) {
          // 兜底: bubble 还是空 (例: lifecycle end 但 onStream 没收到任何 text)
          if ((_streamBubble.textContent || '').trim() === '') {
            _streamBubble.innerHTML = formatMessageContent(reply);
          }
        }
      }
      // 保存到聊天记录 (跟原 displayAIReply 路径行为一致)
      if (!groupId) {
        try {
          saveChatHistory(emp.id, userMessage, reply, docContext, ragResult.citations, images, ocTags);
        } catch (saveErr) {
          console.warn('[Streaming] saveChatHistory failed:', saveErr);
        }
      }
      window._streamingActive = false;
      window._streamingMsgEl = null;
    } else {
      // 原有路径: 新建 AI 节点 (非 streaming / 没收到任何 delta)
      var msgCountBefore = msgArea ? msgArea.childElementCount : 0;
      if (groupId) {
        displayGroupAIReply(groupId, emp.id, reply, chainDepth);
      } else {
        displayAIReply(reply, docContext, timeStr, emp.id, ragResult.citations, chainDepth, ocTags);
        saveChatHistory(emp.id, userMessage, reply, docContext, ragResult.citations, images, ocTags);
      }
      // 命中违禁词时在该消息气泡上方插入警告条 (新建节点路径)
      if (fwWarning && msgArea && msgArea.childElementCount > msgCountBefore) {
        var lastMsg = msgArea.lastElementChild;
        var contentEl = lastMsg && lastMsg.querySelector ? lastMsg.querySelector('.msg-content') : null;
        if (contentEl) {
          contentEl.insertAdjacentHTML('afterbegin', '<div class="forbidden-warning-bar" style="background:rgba(255,59,48,0.1);color:#ff3b30;border:0.5px solid rgba(255,59,48,0.3);padding:6px 10px;border-radius:8px;font-size:12px;margin-bottom:6px;">' + escapeHtml(fwWarning) + '</div>');
        }
      }
    }
    // streaming 路径的违禁词条 (加到已存在的占位节点, 不依赖 msgCountBefore)
    if (fwWarning && msgArea) {
      var _last = msgArea.lastElementChild;
      var _content = _last && _last.querySelector ? _last.querySelector('.msg-content') : null;
      if (_content && !_content.querySelector('.forbidden-warning-bar')) {
        _content.insertAdjacentHTML('afterbegin', '<div class="forbidden-warning-bar" style="background:rgba(255,59,48,0.1);color:#ff3b30;border:0.5px solid rgba(255,59,48,0.3);padding:6px 10px;border-radius:8px;font-size:12px;margin-bottom:6px;">' + escapeHtml(fwWarning) + '</div>');
      }
    }
  }

  // 积分检查（前端拦截）：发送前确认该员工积分余额充足，不足则阻止发送
  try {
    var creditResp = await apiFetch('/api/credits/check?agent_id=' + encodeURIComponent(emp.id));
    if (creditResp) {
      var creditInfo = await creditResp.json();
      if (creditInfo && !Array.isArray(creditInfo) && creditInfo.has_credits === false) {
        var _typing = document.getElementById('typingMsg');
        if (_typing) _typing.remove();
        restoreOnline();
        showToast('❌ 积分不足，请联系管理员充值', 'error');
        return;
      }
    }
  } catch (creditErr) {
    console.warn('[Credits] 积分检查失败，放行发送:', creditErr);
  }

  try {
    // 私聊回复使用独立 chat session，避免与记忆提取 session 冲突
    var sessionKey = 'agent:' + emp.id + ':chat';
    // 达人系统数据由后端直查 talents 表生成（经 talentInjection 响应或
    // /api/talents/injection-text 接口获得），拼到消息末尾，LLM 只负责分析不负责查询
    if (systemData) {
      fullMessage += systemData;
      console.log('[TalentInject] 已拼接后端系统数据 len=' + systemData.length);
    }
    var hasSentWorking = false;
    var hasSentCoding = false;
    // ★ 请求级模型: 客户通过 model pill 指定时透传给 _sendChatWaitForLifecycle
    //   (null=跟随网关默认, 由后者在发送前 sessions.patch 覆盖、结束后还原)
    var lifecycleOptions = { model: REQUEST_CHAT_MODEL };
    
    // 有图片时：先调后端 vision 接口转成文字描述拼进消息文本，
    // OpenClaw 网关只收纯文本，不依赖网关的多模态能力（attachments 参数会被网关丢弃）
    if (images && images.length > 0) {
      var visionFailed = false;
      try {
        var visionResp = await apiFetch('/api/vision/describe', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ images: images, agent_id: emp.id, role: emp.role || '' })
        });
        if (visionResp && visionResp.ok) {
          var visionData = await visionResp.json();
          if (visionData && visionData.text) {
            fullMessage += '\n\n' + visionData.text;
          }
          // 部分识别失败要显式提示用户，不能静默降级成纯文本
          if (visionData && visionData.failed > 0) {
            showToast('⚠️ ' + visionData.failed + ' 张图片识别失败（识别服务不可用），其余图片已正常注入', 'error');
          }
        } else {
          // 后端 502 = 全部识别失败（无数据硬拦截），其他异常同样按无数据处理
          console.warn('[Vision] 图片描述接口返回异常:', visionResp && visionResp.status);
          visionFailed = true;
        }
      } catch (visionErr) {
        console.warn('[Vision] 图片描述获取失败:', visionErr);
        visionFailed = true;
      }
      if (visionFailed) {
        // 无数据硬拦截：不把不含图片数据的请求发给模型，避免 AI 凭幻觉编造
        var _t0 = document.getElementById('typingMsg');
        if (_t0) _t0.remove();
        restoreOnline();
        showToast('⚠️ 未收到图片数据，请重发', 'error');
        return;
      }
    }
    
    // 更新打字状态气泡文字
    function _updateTypingText(html) {
      var t = document.getElementById('typingMsg');
      if (t) {
        var bubble = t.querySelector('.msg-bubble');
        if (bubble) bubble.innerHTML = html;
      }
    }
    
    // ★ refactor/chat-retry-pipeline: 30s 端到端 UI 兜底超时(不取消后台任务,AbortController
    //   无法中断已发的 WS 消息)。lifecycleWaitMs 默认 120s 保留给真的慢 LLM 响应。
    //   此处不重试——callbacks (onStream/onPhase/onToolStart 等) 重试时副作用会泄漏
    //   (status 闪、tool card 重复注册),留到下一轮用 _sendChatWithRetry 改造。
    var chatResult = await _withTimeout(_sendChatWaitForLifecycle(sessionKey, fullMessage, {
      onStream: function (text) {
        if (!hasSentWorking) {
          hasSentWorking = true;
          setEmpStatus('working');
          // ★ dev/feat: #7 streaming — 首次 delta 创建 AI 占位节点
          //    (OpenClaw 内部已经 stream delta, _sendChatWaitForLifecycle 已累加 fullReply,
          //     但前端 onStream 回调之前没渲染 — 这里把每段实时写入 UI)
          if (typeof displayAIReply === 'function') {
            displayAIReply(text || '', docContext, timeStr, emp.id, [], chainDepth);
          }
          window._streamingActive = true;
          // 记录最后插入的 AI 节点, 后续 delta 直接更新
          var _area0 = document.getElementById('messagesArea');
          window._streamingMsgArea = _area0;
          window._streamingMsgEl = _area0 && _area0.lastElementChild;
        } else if (window._streamingActive && text) {
          // ★ 后续 delta: 增量更新占位节点的 bubble 内部
          var _area = window._streamingMsgArea || document.getElementById('messagesArea');
          var _msg = window._streamingMsgEl || (_area && _area.lastElementChild);
          if (_msg && _msg.querySelector) {
            var _bubble = _msg.querySelector('.msg-bubble');
            if (_bubble) {
              _bubble.innerHTML = formatMessageContent(text);
              if (_area) _area.scrollTop = _area.scrollHeight;
            }
          }
        }
      },
      onStartThinking: function () {
        setEmpStatus('thinking');
        _updateTypingText('<span class="thinking-dots">思考中<span>.</span><span>.</span><span>.</span></span>');
      },
      onWorking: function () {
        setEmpStatus('working');
        _updateTypingText('🛠️ 正在调用工具<span class="thinking-dots"><span>.</span><span>.</span><span>.</span></span>');
      },
      onPhase: function (phase) {
        if (phase === 'end') {
          notifyOfficeChatMessage(emp.id, 'incoming');
          setEmpStatus('online');
        } else if (phase === 'error') {
          setEmpStatus('online');
        }
      },
      onToolStart: function (tool) {
        var area = document.getElementById('messagesArea');
        var typing = document.getElementById('typingMsg');
        var card = document.createElement('div');
        card.className = 'tool-card';
        card.id = 'tool-' + tool.toolCallId;
        card.setAttribute('data-expanded', 'false');
        card.innerHTML = '<div class="tool-card-header" onclick="toggleToolCard(\'' + tool.toolCallId + '\')"><span class="tool-icon">🔧</span><span class="tool-name">' + escapeHtml(tool.name || '工具') + '</span><span class="tool-badge tool-badge-running">运行中</span><span class="tool-notify"></span><span class="tool-toggle">展开▼</span></div><div class="tool-cmd">' + escapeHtml(tool.meta || '') + '</div><div class="tool-output"></div>';
        if (typing && typing.parentNode) typing.parentNode.insertBefore(card, typing);
        else if (area) area.appendChild(card);
        if (area) area.scrollTop = area.scrollHeight;
      },
      onToolOutput: function (out) {
        window._toolOutputs = window._toolOutputs || {};
        window._toolOutputs[out.toolCallId] = out.output || '';
        var card = document.getElementById('tool-' + out.toolCallId);
        if (!card) return;
        var outputEl = card.querySelector('.tool-output');
        if (!outputEl) return;
        var text = out.output || '';
        var truncated = text.length > 200;
        var displayText = truncated ? text.substring(0, 200) : text;
        var html = '<pre id="tool-out-pre-' + out.toolCallId + '" style="margin:0;white-space:pre-wrap;word-break:break-all;">' + escapeHtml(displayText) + '</pre>';
        if (truncated) {
          html += '<button onclick="var p=document.getElementById(\'tool-out-pre-' + out.toolCallId + '\');p.style.whiteSpace=\'pre\';p.textContent=' + JSON.stringify(text) + ';this.remove()" style="margin-top:4px;padding:2px 8px;border:none;background:var(--accent);color:#fff;border-radius:4px;font-size:12px;cursor:pointer;">展开</button>';
        }
        if (out.phase === 'end') {
          html += '<div style="margin-top:6px;font-size:12px;">';
          if (out.exitCode === 0) html += '<span style="color:#34C759;">✓</span> exit: 0';
          else html += '<span style="color:#FF3B30;">✗</span> exit: ' + (out.exitCode == null ? '-' : out.exitCode);
          if (out.durationMs != null) html += ' · ' + out.durationMs + 'ms';
          html += '</div>';
        }
        outputEl.innerHTML = html;
        var expanded = card.getAttribute('data-expanded') === 'true';
        if (expanded) {
          outputEl.style.display = 'block';
          var notify = card.querySelector('.tool-notify');
          if (notify) notify.style.display = 'none';
        } else {
          outputEl.style.display = 'none';
          var notify = card.querySelector('.tool-notify');
          if (notify) notify.style.display = 'inline-block';
        }
        var area = document.getElementById('messagesArea');
        if (area) area.scrollTop = area.scrollHeight;
      },
      onToolEnd: function (tool) {
        var card = document.getElementById('tool-' + tool.toolCallId);
        if (!card) return;
        var badge = card.querySelector('.tool-badge');
        if (!badge) return;
        var duration = (tool.endedAt && tool.startedAt) ? (tool.endedAt - tool.startedAt) : 0;
        badge.className = 'tool-badge tool-badge-done';
        badge.textContent = '已完成 耗时' + duration + 'ms';
        console.debug('[ToolEnd] tool object:', JSON.stringify(tool));
        fetch('/api/tool-calls/log', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('sb_auth_token') || '') },
          body: JSON.stringify({
            agent_id: emp.id,
            tool_call_id: tool.toolCallId,
            tool_name: tool.toolName || tool.name || tool.tool || tool.command || 'exec',
            meta: tool.meta,
            output: (window._toolOutputs && window._toolOutputs[tool.toolCallId]) || '',
            exit_code: tool.status === 'completed' ? 0 : 1,
            duration_ms: duration
          })
        }).catch(function (e) { console.warn('[ToolLog] 记录失败:', e); });
      }
    }, lifecycleOptions), TIMEOUT_CHAT_MS, 'chat');
    
    // ★ dedup 三件事收口: 检测 OpenClaw LLM fallback 自动 retry 一次
    //   fallback 触发条件: reply 是固定文案 "抱歉，我暂时无法处理..." 或 isError=true
    //   retry 时换 fallbackKey 避免覆盖主链 session, 并给 LLM 提示这是 retry
    if (chatResult.reply && chatResult.reply.indexOf('抱歉，我暂时无法处理') !== -1 && !chatResult.isError) {
      console.warn('[OpenClaw] 检测到 fallback 文案 (chainDepth=' + chainDepth + '), 自动 retry 一次');
      try {
        var retrySessionKey = 'agent:' + emp.id + ':chat:retry_' + Date.now();
        var retryResult = await _withTimeout(_sendChatWaitForLifecycle(retrySessionKey, fullMessage + '\n\n[系统提示: 上一次LLM未遵守system_prompt中的达人ID,请用 PUT /api/talents/<ID> 直接更新,不要让用户重新提供]', {
          onStream: function () {},
          onPhase: function () {},
          onToolStart: function () {},
          onToolEnd: function () {},
          onLifecycleEnd: function () {}
        }, { model: REQUEST_CHAT_MODEL }), TIMEOUT_CHAT_MS, 'chat:retry');
        if (retryResult && retryResult.reply && retryResult.reply.indexOf('抱歉，我暂时无法处理') === -1) {
          chatResult = retryResult;
          console.log('[OpenClaw] retry 成功, 用 retry 的 reply');
        } else {
          console.warn('[OpenClaw] retry 后仍 fallback 或失败, 用原 reply');
        }
      } catch (retryErr) {
        console.warn('[OpenClaw] retry 异常:', retryErr);
      }
    }

    _doHandle(chatResult.reply, chatResult.isError);
    restoreOnline();
  } catch (e) {
    console.error('[OpenClaw] 发送失败:', e);
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    restoreOnline();
    // Agent 不存在时自动降级到 main，但 sessionKey 仍按员工隔离，避免多个员工共用 agent:main:main 导致上下文串扰
    var agentName = emp.openclawName || 'main';
    if (agentName !== 'main' && e.message && e.message.includes('no longer exists')) {
      console.warn('[OpenClaw] Agent "' + agentName + '" 不存在，降级到 main（员工隔离 session）');
      emp._fallbackToMain = true;
      var fallbackKey = 'agent:main:chat-' + emp.id;
      try {
        var retryResult = await _sendChatWaitForLifecycle(fallbackKey, fullMessage, {}, { model: REQUEST_CHAT_MODEL });
        console.debug('[OpenClaw] main agent 重试返回:', JSON.stringify(retryResult));
        if (retryResult.reply) {
          if (groupId) {
            displayGroupAIReply(groupId, emp.id, retryResult.reply, chainDepth);
          } else {
            displayAIReply(retryResult.reply, docContext, timeStr, emp.id, ragResult.citations, chainDepth);
            saveChatHistory(emp.id, userMessage, retryResult.reply, docContext, ragResult.citations, images);
          }
        } else {
          if (groupId) {
            displayGroupAIReply(groupId, emp.id, '⚠️ Agent "' + agentName + '" 不存在，默认 Agent 也无法连接。请在配置中检查 Agent 名称。', chainDepth);
          } else {
            displayAIReply('⚠️ Agent "' + agentName + '" 不存在，默认 Agent 也无法连接。请在配置中检查 Agent 名称。', docContext, timeStr, emp.id, [], chainDepth);
            saveChatHistory(emp.id, userMessage, '⚠️ Agent 不存在', docContext, [], images);
          }
        }
      } catch (retryErr) {
        if (groupId) {
          displayGroupAIReply(groupId, emp.id, '⚠️ Agent "' + agentName + '" 不存在，默认 Agent 也无法连接。请在配置中检查 Agent 名称。', chainDepth);
        } else {
          displayAIReply('⚠️ Agent "' + agentName + '" 不存在，默认 Agent 也无法连接。请在配置中检查 Agent 名称。', docContext, timeStr, emp.id, [], chainDepth);
          saveChatHistory(emp.id, userMessage, '⚠️ Agent 不存在', docContext, [], images);
        }
      }
    } else {
      if (groupId) {
        displayGroupAIReply(groupId, emp.id, '⚠️ OpenClaw 连接异常: ' + e.message, chainDepth);
      } else {
        displayAIReply('⚠️ OpenClaw 连接异常: ' + e.message, docContext, timeStr, emp.id, [], chainDepth);
      }
    }
  }
}

// 构建最近对话上下文（用于在 OpenClaw session 丢失时恢复上下文）
async function buildRecentChatContext(empId, limit) {
  limit = limit || 10;
  try {
    var res = await apiFetch('/api/chat/' + encodeURIComponent(empId));
    var messages = await res.json();
    if (!messages || messages.length === 0) return '';

    // 取最近 limit 条消息
    var recent = messages.slice(-limit);
    var lines = [];
    recent.forEach(function (m) {
      var roleLabel = m.role === 'user' ? '用户' : (m.role === 'assistant' ? 'AI' : '系统');
      var content = (m.content || '').substring(0, 300);
      if (content) {
        lines.push(roleLabel + ': ' + content);
      }
    });

    if (lines.length === 0) return '';
    return '\n\n【最近对话上下文】\n' + lines.join('\n');
  } catch (e) {
    return '';
  }
}

// 构建最近群聊上下文（用于群聊工具链 follow-up）
async function buildRecentGroupContext(groupId, limit) {
  limit = limit || 10;
  try {
    var ghRes = await apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history');
    var ghData = await ghRes.json();
    if (ghData && ghData.messages && ghData.messages.length > 0) {
      var recentMsgs = ghData.messages.slice(-limit);
      var ghLines = [];
      recentMsgs.forEach(function (m) {
        var sender = m.senderName;
        if (!sender && m.senderId) {
          var _se = findEmpByIdAcrossGroups(m.senderId);
          if (_se) sender = _se.name;
        }
        if (!sender) sender = m.senderType === 'user' ? '用户' : 'AI';
        var content = (m.content || '').substring(0, 200);
        if (content) ghLines.push(sender + ': ' + content);
      });
      if (ghLines.length > 0) {
        return '\n\n【最近群聊上下文】\n' + ghLines.join('\n');
      }
    }
  } catch (e) {}
  return '';
}

// ===== Exec 命令审批 =====
var currentExecApprovalBatchId = null;
var execApprovalTimeout = null;
var execApprovalCountdownInterval = null;

function showExecApprovalModal(payload) {
  if (currentExecApprovalBatchId) {
    sendExecApprovalResolve(currentExecApprovalBatchId, 'deny');
    closeExecApprovalModal();
  }
  var batchId = payload && payload.batchId;
  var agentId = (payload && (payload.agentId || payload.agent_id || payload.agentID)) || '-';
  var command = (payload && (payload.command || payload.cmd || payload.content || payload.text)) || '-';
  if (!batchId) {
    console.warn('[ExecApproval] 无效的审批请求，缺少 batchId');
    return;
  }
  currentExecApprovalBatchId = batchId;
  var agentEl = document.getElementById('execApprovalAgentId');
  var cmdEl = document.getElementById('execApprovalCommand');
  var countdownEl = document.getElementById('execApprovalCountdown');
  if (agentEl) agentEl.textContent = agentId;
  if (cmdEl) cmdEl.textContent = command;
  if (countdownEl) countdownEl.textContent = '30 秒后自动拒绝';
  var modal = document.getElementById('execApprovalModal');
  if (modal) modal.classList.add('show');
  var remaining = 30;
  execApprovalCountdownInterval = setInterval(function () {
    remaining--;
    var el = document.getElementById('execApprovalCountdown');
    if (el) el.textContent = remaining + ' 秒后自动拒绝';
    if (remaining <= 0) clearInterval(execApprovalCountdownInterval);
  }, 1000);
  execApprovalTimeout = setTimeout(function () {
    console.warn('[ExecApproval] 审批超时，自动拒绝 batchId=' + batchId);
    resolveExecApproval('deny');
  }, 30000);
}

function resolveExecApproval(action) {
  if (!currentExecApprovalBatchId) return;
  sendExecApprovalResolve(currentExecApprovalBatchId, action);
  closeExecApprovalModal();
}

function sendExecApprovalResolve(batchId, action) {
  if (!openclaw || !openclaw.connected) {
    console.warn('[ExecApproval] OpenClaw 未连接，无法发送审批结果');
    return;
  }
  openclaw.send('exec.approval.resolve', { batchId: batchId, action: action })
    .then(function () {
      console.debug('[ExecApproval] 已发送审批结果:', batchId, action);
    })
    .catch(function (err) {
      console.error('[ExecApproval] 发送审批结果失败:', err);
    });
}

function closeExecApprovalModal() {
  if (execApprovalTimeout) {
    clearTimeout(execApprovalTimeout);
    execApprovalTimeout = null;
  }
  if (execApprovalCountdownInterval) {
    clearInterval(execApprovalCountdownInterval);
    execApprovalCountdownInterval = null;
  }
  currentExecApprovalBatchId = null;
  var modal = document.getElementById('execApprovalModal');
  if (modal) modal.classList.remove('show');
}

// 初始化 OpenClaw 连接
const OPENCLAW_CONFIG = {
  gatewayUrl: 'ws://192.168.1.25:18789',
  defaultToken: '8606e4d80b1accfaa4e22729466c40003cd217ce2bda93f3'
};

// WebSocket 断线自动重连（阶梯延迟，最多 5 次）
var wsReconnectAttempts = 0;
var WS_MAX_RECONNECT = 5;
var WS_RECONNECT_DELAYS = [3000, 6000, 9000, 12000, 15000];
var _wsReconnectTimer = null;
var _wsManualClose = false;       // 主动关闭（如切换配置）时不触发重连
var _wsReconnectNotified = false; // 重连耗尽后只提示一次

function tryReconnectOpenClaw() {
  if (_wsManualClose) return;
  if (typeof openclaw === 'undefined' || openclaw.connected || openclaw.mockMode) return;
  if (wsReconnectAttempts >= WS_MAX_RECONNECT) {
    if (!_wsReconnectNotified) {
      _wsReconnectNotified = true;
      showToast('❌ OpenClaw 连接已断开，自动重连 ' + WS_MAX_RECONNECT + ' 次均失败，请检查 Gateway 后刷新页面');
    }
    return;
  }
  var delay = WS_RECONNECT_DELAYS[Math.min(wsReconnectAttempts, WS_RECONNECT_DELAYS.length - 1)];
  wsReconnectAttempts++;
  // 抑制 openclaw-client.js 内置重连，统一由这里按 WS_RECONNECT_DELAYS 调度
  openclaw._reconnectAttempts = openclaw._maxReconnectAttempts;
  clearTimeout(_wsReconnectTimer);
  _wsReconnectTimer = setTimeout(function () {
    if (_wsManualClose || openclaw.connected) return;
    console.debug('[OpenClaw] 自动重连 (' + wsReconnectAttempts + '/' + WS_MAX_RECONNECT + ')...');
    openclaw.connect().catch(function (err) {
      console.warn('[OpenClaw] 自动重连异常:', err);
    });
  }, delay);
}

function initOpenClaw() {
  if (typeof openclaw === 'undefined') {
    console.warn('[OpenClaw] openclaw-client.js 未加载，5秒后重试');
    setTimeout(initOpenClaw, 5000);
    return;
  }
  var token = localStorage.getItem('openclaw_token') || OPENCLAW_CONFIG.defaultToken;
  if (!token) {
    console.warn('[OpenClaw] 无 token，跳过连接');
    return;
  }
  console.debug('[OpenClaw] 设置 token 并开始连接...');
  openclaw.setToken(token);

  // 监听事件（事件名必须匹配 openclaw-client.js 的 emit 名称）
  openclaw.on('connected', function () {
    console.debug('[OpenClaw] WebSocket 已连接');
    wsReconnectAttempts = 0;
    _wsManualClose = false;
    _wsReconnectNotified = false;
    clearTimeout(_wsReconnectTimer);
    updateConnectionStatus();
  });
  openclaw.on('authenticated', function () {
    console.debug('[OpenClaw] ✅ 已连接 Gateway 并认证成功！');
    localStorage.setItem('openclaw_token', token);
    var statusEl = document.getElementById('openclawStatus');
    if (statusEl) statusEl.textContent = '🟢 已连接';
    var empStatusEl = document.getElementById('empConnectGatewayStatus');
    if (empStatusEl) {
      empStatusEl.textContent = '🟢 已连接';
      empStatusEl.style.color = '#34C759';
    }
    updateConnectionStatus();

    // 认证后拉取可用 agent 列表 → openclaw-client.send 自动注入 agentId；
    // 首次拉取且用户没显式选过默认 agent 时，自动选 list 里的第一个。
    if (typeof openclaw.listAgents === 'function') {
      openclaw.listAgents().then(function (list) {
        var arr = Array.isArray(list) ? list : [];
        var names = arr.map(function (a) { return (a && (a.agentId || a.id || a.name)) || '?'; });
        console.debug('[OpenClaw] 网关 agent 列表 (' + arr.length + '):', names.join(', '));
        console.debug('[OpenClaw] 当前默认 agentId =', openclaw.getDefaultAgentId && openclaw.getDefaultAgentId());
      }).catch(function (e) {
        console.warn('[OpenClaw] 拉取 agent 列表失败:', e && e.message || e);
      });
    }

    // 全局事件捕获：记录 OpenClaw 推送的所有事件类型，特别是工具调用相关事件
    openclaw.on('event', function (e) {
    });

    // 连接成功后自动获取模型列表
    setTimeout(function () {
      fetchOpenClawModels().then(function (models) {
        if (models && models.length > 0) {
          console.debug('[OpenClaw] 模型列表已自动加载，共 ' + models.length + ' 个');
          showToast('✅ 已加载 ' + models.length + ' 个可用模型', 'success');
        }
      });
    }, 500);
  });
  openclaw.on('disconnected', function () {
    console.debug('[OpenClaw] 断开连接');
    var statusEl = document.getElementById('openclawStatus');
    if (statusEl) statusEl.textContent = '🔴 未连接';
    var empStatusEl = document.getElementById('empConnectGatewayStatus');
    if (empStatusEl) {
      empStatusEl.textContent = '🔴 未连接';
      empStatusEl.style.color = '#FF3B30';
    }
    updateConnectionStatus();
    tryReconnectOpenClaw();
  });
  openclaw.on('error', function (err) {
    console.error('[OpenClaw] 连接错误:', err);
    updateConnectionStatus();
    tryReconnectOpenClaw();
  });
  openclaw.on('mockMode', function () {
    console.warn('[OpenClaw] ⚠️ 连接失败，已降级到 Mock 模式');
    updateConnectionStatus();
  });

  // Exec 命令审批
  openclaw.on('exec.approval.requested', function (payload) {
    console.debug('[ExecApproval] 收到审批请求:', payload);
    showExecApprovalModal(payload);
  });

  openclaw.connect().then(function (ok) {
    console.debug('[OpenClaw] connect() 返回:', ok, 'connected:', openclaw.connected, 'authenticated:', openclaw.authenticated);
  }).catch(function (err) {
    console.error('[OpenClaw] connect() 异常:', err);
  });
}

// Gear Menu Functions
let activeGearMenu = null;
let activeEmployeeItem = null;
function showGearMenu(event, btn) {
  event.stopPropagation();
  closeAllGearMenus();
  activeEmployeeItem = btn.closest('.list-item');
  let menu = document.getElementById('gearMenuTemplate');
  if (!menu) return;
  let existing = document.getElementById('activeGearMenu');
  if (existing) existing.remove();
  let clone = menu.cloneNode(true);
  clone.id = 'activeGearMenu';
  clone.style.display = 'block';
  clone.style.position = 'absolute';
  let rect = btn.getBoundingClientRect();
  clone.style.top = rect.bottom + window.scrollY + 4 + 'px';
  clone.style.right = window.innerWidth - rect.right + 'px';
  document.body.appendChild(clone);
  activeGearMenu = clone;
}
function closeAllGearMenus() {
  let menu = document.getElementById('activeGearMenu');
  if (menu) {
    menu.remove();
    activeGearMenu = null;
    activeEmployeeItem = null;
  }
}
function gearEditProfile() {
  if (!activeEmployeeItem) return;
  const empId = activeEmployeeItem.dataset.id;
  closeAllGearMenus();
  if (empId) openEmpDetail(empId);
}
function gearMoveToGroup(groupName) {
  if (!activeEmployeeItem) return;
  let subGroups = document.querySelectorAll('.sub-group');
  let targetGroup = null;
  for (let sg of subGroups) {
    let title = sg.querySelector('.sub-group-title');
    if (title && title.textContent === groupName) {
      targetGroup = sg;
      break;
    }
  }
  if (targetGroup) {
    let content = targetGroup.querySelector('.sub-group-content');
    if (content) {
      content.appendChild(activeEmployeeItem);
      console.debug('Moved to', groupName);
    }
  }
  closeAllGearMenus();
}
function gearPinEmployee() {
  if (!activeEmployeeItem) return;
  let subGroup = activeEmployeeItem.closest('.sub-group-content');
  if (subGroup) {
    subGroup.insertBefore(activeEmployeeItem, subGroup.firstChild);
    console.debug('Pinned');
  }
  closeAllGearMenus();
}

// Close menu when clicking outside
document.addEventListener('click', function (e) {
  if (activeGearMenu && !activeGearMenu.contains(e.target)) {
    closeAllGearMenus();
  }
});
function toggleCategory(header) {
  var section = header.parentElement;
  section.classList.toggle('collapsed');
  header.classList.toggle('collapsed');
}
function toggleSubGroup(header) {
  var group = header.parentElement;
  group.classList.toggle('collapsed');
  header.classList.toggle('collapsed');
}
function toggleArchive(header) {
  var section = header.parentElement;
  section.classList.toggle('collapsed');
  header.classList.toggle('collapsed');
}

// ========== AI 配置 ==========
const AI_CONFIG = {
  enabled: false,
  endpoint: '',
  apiKey: '',
  model: 'gpt-4o'
};

// ========== API 供应商配置 ==========
const API_PROVIDERS = {
  openai: {
    name: 'OpenAI',
    icon: '🤖',
    baseUrl: 'https://api.openai.com/v1',
    models: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo'],
    defaultModel: 'gpt-4o',
    keyPlaceholder: 'sk-...'
  },
  kimi: {
    name: 'Kimi',
    icon: '🌙',
    baseUrl: 'https://api.kimi.com/coding/v1',
    models: ['k3'],
    defaultModel: 'k3',
    keyPlaceholder: 'sk-...'
  },
  deepseek: {
    name: 'DeepSeek',
    icon: '🔍',
    baseUrl: 'https://api.deepseek.com/v1',
    models: ['deepseek-chat', 'deepseek-coder'],
    defaultModel: 'deepseek-chat',
    keyPlaceholder: 'sk-...'
  },
  zhipu: {
    name: '智谱',
    icon: '🔥',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
    models: ['glm-4', 'glm-4-flash', 'glm-3-turbo'],
    defaultModel: 'glm-4',
    keyPlaceholder: '...'
  },
  custom: {
    name: '自定义',
    icon: '⚙️',
    baseUrl: '',
    models: [],
    defaultModel: '',
    keyPlaceholder: '自定义 API Key'
  }
};

// State
let currentTab = 'employees';
var drawerOpen = false;
var showBoard = false;
var mentionMode = false;
var mentionIdx = 0;
var slashIdx = 0;

// Data
var emps = [];
// 历史遗留硬编码默认员工ID（已移除，但后端/本地存储可能仍保留，需过滤）
var _DEFAULT_EMP_IDS = {'xlcx': true, 'dlxc': true, 'zjg': true, 'hx': true, 'sy': true};
// 历史遗留默认员工名字（不区分大小写）
var _DEFAULT_EMP_NAMES = {'lucy': true, 'emily': true, 'grace': true, 'cynthia': true, 'luna': true, 'gates': true, 'eric': true, 'olivia': true, 'summer': true};
function _isDefaultEmp(emp) {
  if (!emp) return false;
  // 有 createdBy 的员工是用户手动创建的，绝不视为默认员工
  if (emp.createdBy && emp.createdBy !== 'local' && emp.createdBy !== '') return false;
  if (_DEFAULT_EMP_IDS[emp.id]) return true;
  var name = String(emp.name || '').trim().toLowerCase();
  if (_DEFAULT_EMP_NAMES[name]) return true;
  return false;
}

// ========== AI 初始化 ==========
document.addEventListener('DOMContentLoaded', function () {
  checkAuth();
  loadAIConfig();
  // 拉取当前 chat 模型状态（ui/model-switcher）
  if (typeof loadChatModelState === 'function') loadChatModelState();
  // 检查 URL 参数 ?emp=xxx 自动打开聊天
  checkUrlParams();
  // 同步本地知识文档到后端（一次性迁移）
  syncLocalStorageDocsToBackend();
  // 初始化侧边栏拖拽调整
  initSidebarResizers();
  applySidebarWidth(getSidebarWidth());
  // fix/responsive-shell-collapse-r3: 初次入场即按下响应式 shell(不动 r2 CSS,
  // 仅 inline 加固 .left-nav, 拦截对话页 @media 失效场景)
  if (typeof forceResponsiveShell === 'function') forceResponsiveShell();
  // 监听 resize(横竖屏切换 / PC 拖窗口): < 768 ↔ >= 769 边界重排
  window.addEventListener('resize', function () {
    if (typeof forceResponsiveShell === 'function') forceResponsiveShell();
  });
  // 默认切换到消息模块（Feishu三栏布局）
  setTimeout(function() { switchModule('messages'); }, 100);

  // 点击外部关闭全局搜索下拉
  document.addEventListener('click', function (e) {
    var box = document.getElementById('globalSearchBox');
    if (box && !box.contains(e.target)) {
      closeGlobalSearchDropdown();
    }
  });
});

// ========== BroadcastChannel 双向同步 (龙虾办公室) ==========
let lobsterChannel = null;
function initLobsterChannel() {
  try {
    lobsterChannel = new BroadcastChannel('lobster-office');
    lobsterChannel.onmessage = function (event) {
      var data = event.data;
      var type = data.type;
      var agent_id = data.agent_id;
      var status = data.status;
      switch (type) {
        case 'lobster_status':
          // 办公室员工状态变化，更新本地数据
          if (agent_id && status) {
            var emp = emps.find(function (e) {
              return String(e.id) === String(agent_id);
            });
            if (emp) {
              var statusMap = {
                thinking: 'busy',
                using_tool: 'busy',
                writing: 'busy',
                working: 'busy',
                idle: 'idle',
                offline: 'offline',
                online: 'online'
              };
              emp.status = statusMap[status] || emp.status;
              renderEmployeeList();
            }
          }
          break;
        case 'employees_updated':
          // 办公室更新了员工数据，或者办公室保存了文档
          // 总是从后端重新加载，确保数据同步
          if (typeof loadEmployees === 'function') {
            loadEmployees().then(function () {
              renderEmployeeList();
            });
          }
          break;
        default:
          console.debug('[Main] Received from office:', type);
      }
    };
    console.debug('[Main] BroadcastChannel lobster-office initialized');
  } catch (err) {
    console.debug('[Main] BroadcastChannel not supported:', err);
  }
}

// 通知龙虾办公室员工数据更新
function notifyOfficeEmployeesUpdated() {
  if (lobsterChannel) {
    lobsterChannel.postMessage({
      type: 'employees_updated',
      employees: emps
    });
  }
}

// 通知龙虾办公室员工状态变化
function notifyOfficeStatusChange(empId, status) {
  if (lobsterChannel) {
    lobsterChannel.postMessage({
      type: 'lobster_status',
      agent_id: empId,
      status: status
    });
  }
}

// 通知龙虾办公室有新聊天消息
function notifyOfficeChatMessage(empId, direction) {
  if (lobsterChannel) {
    lobsterChannel.postMessage({
      type: 'lobster_chat',
      agent_id: empId,
      direction: direction
    });
  }
}

// 检查 URL 参数 ?emp=xxx 或 ?action=createGroup
function checkUrlParams() {
  const params = new URLSearchParams(window.location.search);
  const empId = params.get('emp');
  const action = params.get('action');
  if (empId) {
    // 延迟执行，确保 DOM 已加载
    setTimeout(function () {
      if (typeof openChat === 'function') {
        openChat(empId);
        // 清除 URL 参数（使用 replaceState 避免产生历史记录）
        const url = new URL(window.location);
        url.searchParams.delete('emp');
        window.history.replaceState({}, '', url);
        var foundEmp = emps.find(function (e) {
          return e.id === empId;
        });
        showToast('🦞 已打开与 ' + (foundEmp && foundEmp.name || empId) + ' 的聊天');
      }
    }, 500);
  }
  if (action === 'createGroup') {
    /* 〔r39-17④ 防呆 老大拍板〕 ?action=createGroup 三件套防重触发:
       1. 内存 flag — 同页 session 只触发一次 (checkUrlParams 被多次调/SPA 复调不重复弹)
       2. 立即清 URL 参数 — 不等 500ms setTimeout, 窗口期内刷新不会带参重发
       3. openGroupWizard 内部「已开跳过」兜底 (关不掉根因: 重开竞态复位用户已关的向导)
       不用 sessionStorage — 它在标签页刷新后不释放, 会误伤合法的「刷新再弹一次」 */
    if (!window._urlCreateGroupFired) {
      window._urlCreateGroupFired = true;
      const url0 = new URL(window.location);
      url0.searchParams.delete('action');
      window.history.replaceState({}, '', url0);
      setTimeout(function () {
        if (typeof openGroupWizard === 'function') {
          openGroupWizard();
        }
      }, 500);
    }
  }
  if (action === 'createEmployee') {
    setTimeout(function () {
      if (typeof openWizard === 'function') {
        openWizard();
        // 清除 URL 参数
        const url = new URL(window.location);
        url.searchParams.delete('action');
        window.history.replaceState({}, '', url);
      }
    }, 500);
  }
}
function loadAIConfig() {
  const saved = localStorage.getItem('sb_ai_config');
  if (saved) {
    try {
      Object.assign(AI_CONFIG, JSON.parse(saved));
    } catch (e) {}
  }
  updateAIStatusIcon();
}
function saveAIConfig() {
  localStorage.setItem('sb_ai_config', JSON.stringify(AI_CONFIG));
  updateAIStatusIcon();
}
function updateAIStatusIcon() {
  const tag = document.getElementById('aiModeTag');
  if (tag) {
    const mode = AI_CONFIG && AI_CONFIG.mode || 'copaw';
    const icons = {
      copaw: '🤖',
      openai: '🔮',
      custom: '⚙️'
    };
    tag.textContent = (icons[mode] || '🤖') + ' ' + (mode === 'copaw' ? 'CoPaw' : mode === 'openai' ? 'OpenAI' : '自定义');
  }
}

// ========== RBAC Permission System ==========
const PERMISSION_ROLES = {
  admin: {
    name: 'Admin',
    color: '#FF6B35',
    level: 3
  },
  dev: {
    name: 'Developer',
    color: '#1677ff',
    level: 2
  },
  pm: {
    name: 'Product',
    color: '#AF52DE',
    level: 2
  },
  qa: {
    name: 'QA',
    color: '#34C759',
    level: 1
  },
  devops: {
    name: 'DevOps',
    color: '#5856D6',
    level: 2
  }
};
function hasPermission(empId, perm) {
  const emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) return false;
  const role = PERMISSION_ROLES[emp.permission];
  if (!role) return false;
  if (role.level === 3) return true;
  const permMap = {
    chat: ['dev', 'pm', 'qa', 'devops', 'admin'],
    task: ['dev', 'pm', 'qa', 'devops', 'admin'],
    board: ['dev', 'pm', 'devops', 'admin'],
    config: ['admin'],
    manage: ['admin']
  };
  return (permMap[perm] || []).includes(emp.permission);
}

// ========== Operation Log ==========
const operationLogs = [];
function addOperationLog(type, target, detail, empId) {
  const log = {
    id: 'log_' + Date.now(),
    type,
    target,
    detail,
    empId,
    empName: function () {
      var f = emps.find(function (x) {
        return x.id === empId;
      });
      return f && f.name || 'System';
    }(),
    timestamp: formatDate()
  };
  operationLogs.unshift(log);
  if (operationLogs.length > 100) operationLogs.pop();
  localStorage.setItem('sb_operation_logs', JSON.stringify(operationLogs.slice(0, 100)));
}
function loadOperationLogs() {
  const saved = localStorage.getItem('sb_operation_logs');
  if (saved) {
    try {
      operationLogs.length = 0;
      JSON.parse(saved).forEach(function (l) {
        return operationLogs.push(l);
      });
    } catch (e) {}
  }
}
loadOperationLogs();

// AI Models
const aiModels = [{
  id: 'gpt4o',
  name: 'GPT-4o',
  provider: 'OpenAI',
  icon: '🤖'
}, {
  id: 'claude',
  name: 'Claude 3.5',
  provider: 'Anthropic',
  icon: '🧠'
}, {
  id: 'gemini',
  name: 'Gemini Pro',
  provider: 'Google',
  icon: '💎'
}];

// Employee sync
var _saveEmpTimer = null;
var _dirtyEmpIds = new Set();
function saveEmployees(empIds) {
  // 1. 通知龙虾办公室数据已更新
  notifyOfficeEmployeesUpdated();
  // 标记需要同步的员工 ID
  if (empIds) {
    if (Array.isArray(empIds)) {
      empIds.forEach(function (id) { _dirtyEmpIds.add(id); });
    } else {
      _dirtyEmpIds.add(empIds);
    }
  } else {
    // 未指定则同步所有员工
    emps.forEach(function (e) { _dirtyEmpIds.add(e.id); });
  }
  // 3. 延迟同步到服务器（debounce 3秒，避免频繁调用创建重复 agent）
  if (_saveEmpTimer) clearTimeout(_saveEmpTimer);
  _saveEmpTimer = setTimeout(function () {
    var token = localStorage.getItem('sb_auth_token');
    if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
      _dirtyEmpIds.forEach(function (empId) {
        var emp = emps.find(function (e) { return e.id === empId; });
        // 已归档/已删除的员工不再通过 PUT 同步，避免后端 404
        if (emp && !isArchivedEmp(emp)) syncEmpToServer(emp);
      });
      _dirtyEmpIds.clear();
    }
  }, 3000);
}

// ===== Badge标记系统 =====
function setEmpBadge(empId, level, number) {
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) return;
  if (level === null || level === undefined) {
    emp.badge = null;
  } else {
    emp.badge = {
      level: level,
      number: number > 0 && number <= 9 ? number : null
    };
  }
  saveEmployees();
  renderEmployeeList();
}
function _isLogPolluted(value) {
  if (typeof value !== 'string' || value.length < 30) return false;
  var patterns = [
    /\[\d{2}:\d{2}:\d{2}\]\s+"(GET|POST|PUT|DELETE|OPTIONS)\s+[^"]*\s+HTTP\/1\.1"\s+\d+/,
    /\[\d{2}:\d{2}:\d{2}\]\s+\[/,
    /\[PUT agent\]|\[GET agents\]|\[POST agent\]|\[OpenClawSync\]/
  ];
  for (var i = 0; i < patterns.length; i++) {
    if (patterns[i].test(value)) return true;
  }
  return false;
}
async function syncEmpToServer(emp) {
  try {
    // 运行时状态（status/msg/lastActive/tokens/tokenStats）不应持久化到后端
    var syncData = Object.assign({}, emp);
    delete syncData.status;
    delete syncData.msg;
    delete syncData.lastActive;
    delete syncData.tokens;
    delete syncData.tokenStats;
    // 防御：检测 apiKey 是否被日志污染
    if (syncData.apiKey && _isLogPolluted(syncData.apiKey)) {
      console.warn('[syncEmpToServer] apiKey 被日志污染，长度=' + syncData.apiKey.length + '，已清空');
      syncData.apiKey = '';
      emp.apiKey = '';
    }
    console.debug('[syncEmpToServer] 发送 PUT /api/agents/' + emp.id, 'body_keys=' + Object.keys(syncData).join(','));
    var res = await apiFetch('/api/agents/' + emp.id, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(syncData)
    });
    if (!res || res.status === 404) {
      console.warn('[syncEmpToServer] Agent', emp.id, '不在服务器上，跳过同步');
    }
  } catch (e) {
    console.debug('同步员工到后端失败:', e);
  }
}

// 新建员工时首次同步到服务器
async function syncNewEmpToServer(emp) {
  try {
    var res = await apiFetch('/api/agents', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify(Object.assign({}, emp, {
        createdBy: currentUser && currentUser.id,
        createdByName: currentUser && currentUser.displayName || currentUser && currentUser.name
      }))
    });
    if (res && res.ok) {
      console.debug('[syncNewEmpToServer] 员工已同步到服务器:', emp.id);
    } else {
      console.warn('[syncNewEmpToServer] 同步失败:', res ? res.status : 'no response');
    }
  } catch (e) {
    console.debug('新建员工同步到后端失败:', e);
  }
}

// 状态文本转换
function getStatusText(status) {
  var statusMap = {
    'online': '🟢 在线',
    'busy': '🟠 忙碌',
    'thinking': '💭 正在思考',
    'working': '⚡ 工作中',
    'reading': '📖 正在读文档',
    'coding': '🔧 正在调工具',
    'writing': '📝 正在写代码',
    'waiting': '🟡 等待中',
    'idle': '⚪ 空闲',
    'offline': '⚫ 离线'
  };
  return statusMap[status] || '⚫ 离线';
}

// 状态图标（用于列表显示）
function getStatusIcon(status) {
  var iconMap = {
    'online': '🟢',
    'busy': '🟠',
    'thinking': '💭',
    'working': '⚡',
    'reading': '📖',
    'coding': '🔧',
    'writing': '📝',
    'waiting': '🟡',
    'idle': '⚪',
    'offline': '⚫'
  };
  return iconMap[status] || '⚫';
}

// 快速配置模板
const quickTemplates = {
  'frontend': {
    name: '前端工程师',
    avatar: '👨‍💻',
    color: '#1677ff',
    model: 'gpt-4o',
    skills: [{
      emoji: '⚛️',
      name: 'React',
      level: 5
    }, {
      emoji: '📜',
      name: 'TypeScript',
      level: 4
    }, {
      emoji: '🎨',
      name: 'CSS/Sass',
      level: 4
    }]
  },
  'backend': {
    name: '后端工程师',
    avatar: '⚙️',
    color: '#34C759',
    model: 'claude-3-5-sonnet',
    skills: [{
      emoji: '🐍',
      name: 'Python',
      level: 5
    }, {
      emoji: '🗄️',
      name: '数据库设计',
      level: 4
    }, {
      emoji: '🔒',
      name: 'API 安全',
      level: 4
    }]
  },
  'product': {
    name: '产品经理',
    avatar: '📋',
    color: '#FF9500',
    model: 'claude-3-5-sonnet',
    skills: [{
      emoji: '📝',
      name: 'PRD 撰写',
      level: 5
    }, {
      emoji: '🔍',
      name: '竞品分析',
      level: 4
    }, {
      emoji: '👥',
      name: '用户研究',
      level: 4
    }]
  },
  'designer': {
    name: '设计师',
    avatar: '🎨',
    color: '#AF52DE',
    model: 'gpt-4o',
    skills: [{
      emoji: '🎯',
      name: 'UI 设计',
      level: 5
    }, {
      emoji: '✨',
      name: 'UX 设计',
      level: 4
    }, {
      emoji: '📐',
      name: 'Figma',
      level: 5
    }]
  },
  'qa': {
    name: '测试工程师',
    avatar: '🧪',
    color: '#FF2D55',
    model: 'gpt-4o',
    skills: [{
      emoji: '🤖',
      name: '自动化测试',
      level: 5
    }, {
      emoji: '⚡',
      name: '性能测试',
      level: 4
    }, {
      emoji: '🐛',
      name: 'Bug 追踪',
      level: 4
    }]
  },
  'devops': {
    name: '运维工程师',
    avatar: '🚀',
    color: '#5856D6',
    model: 'claude-3-5-sonnet',
    skills: [{
      emoji: '☸️',
      name: 'Kubernetes',
      level: 5
    }, {
      emoji: '🐳',
      name: 'Docker',
      level: 5
    }, {
      emoji: '📊',
      name: '监控告警',
      level: 4
    }]
  }
};
function applyTemplate(templateKey) {
  const template = quickTemplates[templateKey];
  if (!template) return;

  // 自动填充名称
  document.getElementById('newEmpName').value = template.name;

  // 自动选择角色
  document.getElementById('newEmpRole').value = template.name;

  // 自动选择头像
  document.querySelectorAll('.avatar-opt').forEach(function (opt) {
    opt.classList.toggle('selected', opt.dataset.avatar === template.avatar);
  });

  // 提示用户已应用模板
  showToast(`✅ 已应用 ${template.name} 模板`);

  // 高亮选中的模板卡片
  document.querySelectorAll('.quick-template-card').forEach(function (card) {
    card.style.borderColor = '';
    card.style.background = '';
  });
  event.currentTarget.style.borderColor = 'var(--accent)';
  event.currentTarget.style.background = 'var(--accent-light)';
}
async function loadEmployees() {
  // 1. 优先从后端加载
  var token = localStorage.getItem('sb_auth_token');
  if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
    try {
      var resp = await apiFetch('/api/agents');
      if (resp && resp.ok) {
        var data = await resp.json();
        console.debug('[loadEmployees] /api/agents 返回:', data.length, '个 agents');
        data.forEach(function(a) {
          console.debug('[loadEmployees] agent:', a.id, a.name, 'createdBy=', a.createdBy);
        });

        // 后端有权限过滤，直接用后端数据（包括空数组）
        // 检查 data 是否是数组（防止返回错误对象如 {error: '...'}）
        if (data !== null && data !== undefined && Array.isArray(data)) {
          // 迁移旧头像(emoji/字母)为数字索引，并记录需要同步的
          var needsSync = [];
          for (var mi = 0; mi < data.length; mi++) {
            var av = data[mi].avatar;
            if (av !== undefined && av !== null && typeof av !== 'number' && !(typeof av === 'string' && (av.indexOf('data:') === 0 || av.indexOf('.png') > 0 || av.indexOf('.jpg') > 0 || av.indexOf('.jpeg') > 0 || av.indexOf('.svg') > 0 || av.indexOf('.webp') > 0 || av.indexOf('http') === 0))) {
              var hh = 0,
                nn = data[mi].name || data[mi].id || '';
              for (var cc = 0; cc < nn.length; cc++) {
                hh = (hh << 5) - hh + nn.charCodeAt(cc);
                hh = hh & hh;
              }
              data[mi].avatar = Math.abs(hh) % 24;
              needsSync.push({
                id: data[mi].id,
                avatar: data[mi].avatar
              });
            }
          }
          // 保留本地运行时状态，防止后端旧数据覆盖 thinking/online
          var localRuntimes = {};
          emps.forEach(function (e) {
            localRuntimes[e.id] = {
              status: e.status,
              msg: e.msg,
              lastActive: e.lastActive
            };
          });
          emps.length = 0;
          var seenIds = {};
          data.forEach(function (a) {
            if (_isDefaultEmp(a)) {
              console.warn('[loadEmployees] 跳过历史默认员工:', a.id, a.name);
              return;
            }
            if (!seenIds[a.id]) {
              var rt = localRuntimes[a.id];
              if (rt) {
                a.status = rt.status;
                a.msg = rt.msg;
                a.lastActive = rt.lastActive;
              }
              emps.push(a);
              seenIds[a.id] = true;
            } else {
              console.warn('[loadEmployees] 跳过重复员工:', a.id, a.name);
            }
          });
          // 重新合并项目组成员，防止 emps 被清空后丢失群内 AI 的 @提及匹配能力
          mergeGroupMembersToEmps();
          refreshChatEmptyStateIfVisible();

          // 不再存localStorage，避免不同用户数据混淆

          // 异步同步迁移后的头像到后端（避免下次刷新又被覆盖）
          if (needsSync.length > 0 && typeof apiFetch === 'function') {
            setTimeout(function () {
              needsSync.forEach(function (item) {
                apiFetch('/api/agents/' + item.id, {
                  method: 'PUT',
                  headers: {
                    'Content-Type': 'application/json'
                  },
                  body: JSON.stringify({
                    avatar: item.avatar
                  })
                }).catch(function (e) {});
              });
            }, 500);
          }
          return;
        }
      }
    } catch (e) {
      console.debug('从后端加载员工失败:', e);
    }
  } else {
    console.debug('[loadEmployees] 跳过后端加载, token=', token);
  }
  // 后端加载失败或本地模式时，也尝试合并项目组成员
  mergeGroupMembersToEmps();
  refreshChatEmptyStateIfVisible();
}

// Tasks data with drag support
var tasksData = {
  todo: [],
  progress: [],
  done: []
};
var draggingTask = null;

// 任务持久化
function saveTasks() {
  localStorage.setItem('sb_tasks', JSON.stringify(tasksData));
}
function loadKanbanTasks() {
  const saved = localStorage.getItem('sb_tasks');
  if (saved) {
    try {
      const data = JSON.parse(saved);
      Object.assign(tasksData, data);
    } catch (e) {}
  }
}

// ============ 催促功能 ============
// 检测是否有需要催促的任务
function checkReminders() {
  const today = new Date().toISOString().split('T')[0];
  const overdue = [];
  const urgent = [];

  // 检查过期任务
  ['todo', 'progress'].forEach(function (col) {
    tasksData[col].forEach(function (t) {
      if (t.deadline && t.deadline < today && !t.done) {
        overdue.push(t);
      }
      if (t.priority === 'high' && !t.done) {
        urgent.push(t);
      }
    });
  });
  return {
    overdue,
    urgent
  };
}

// 生成催促消息
function generateReminderMsg() {
  const {
    overdue,
    urgent
  } = checkReminders();
  if (!emps || emps.length === 0) return null;
  const emp = emps[Math.floor(Math.random() * emps.length)];
  const msgs = [];
  if (overdue.length > 0) {
    msgs.push(`⚠️ 提醒：以下任务已过期，请尽快处理！\n${overdue.map(function (t) {
      return `• ${t.name}（${t.deadline} 截止）`;
    }).join('\n')}`);
  }
  if (urgent.length > 0 && urgent.filter(function (t) {
    return !overdue.includes(t);
  }).length > 0) {
    msgs.push(`🔥 高优先级任务需要关注：\n${urgent.filter(function (t) {
      return !overdue.includes(t);
    }).map(function (t) {
      return `• ${t.name}`;
    }).join('\n')}`);
  }
  if (msgs.length > 0) {
    return {
      emp,
      text: msgs.join('\n\n'),
      isReminder: true
    };
  }
  return null;
}

// 显示催促消息
function showReminder() {
  const reminder = generateReminderMsg();
  if (!reminder) return;
  const {
    emp,
    text
  } = reminder;

  // 只在当前员工的聊天中显示，避免串台
  var currentChatEmpId = localStorage.getItem('sb_current_emp');
  if (currentChatEmpId && currentChatEmpId === emp.id) {
    const area = document.getElementById('messagesArea');
    area.insertAdjacentHTML('beforeend', '<div class="msg reminder"><div class="msg-avatar">' + renderAvatar(emp, 32) + '</div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + emp.name + '</span><span class="msg-sender-role">' + getEmpRoleDisplay(emp) + '</span><span class="msg-sender-time">' + formatDate() + '</span></div><div class="msg-bubble reminder-bubble">' + text.replace(/\n/g, '<br>') + '</div></div></div>');
    area.scrollTop = area.scrollHeight;
  }

  // 同时显示为顶部通知
  if (emp && emp.name) showToast(emp.name + ': ' + text.split('\n')[0]);
}

// ============ 任务精确超时触发（基于 setTimeout） ============
var taskTimeoutTimers = {};

function _getTaskTimerId(task) {
  return 'task_' + (task.id || task.name || Math.random().toString(36).slice(2));
}

// 为单个任务设置精确超时定时器
function scheduleTaskTimeout(task) {
  if (!task || !task.deadline || task.done) return;
  var timerId = _getTaskTimerId(task);
  // 清除已有定时器
  if (taskTimeoutTimers[timerId]) {
    clearTimeout(taskTimeoutTimers[timerId]);
    delete taskTimeoutTimers[timerId];
  }
  // 计算到截止时间当天23:59:59的毫秒数
  var deadlineMs = new Date(task.deadline + 'T23:59:59').getTime();
  var nowMs = Date.now();
  var delayMs = deadlineMs - nowMs;
  if (delayMs <= 0) {
    // 已经超时，延迟3秒后触发（避免页面加载时瞬间发送太多）
    taskTimeoutTimers[timerId] = setTimeout(function () {
      _triggerTaskTimeout(task);
    }, 3000 + Math.random() * 5000);
    return;
  }
  // 设置精确定时器
  taskTimeoutTimers[timerId] = setTimeout(function () {
    _triggerTaskTimeout(task);
  }, delayMs);
  console.debug('[TaskTimer] 任务「' + task.name + '」超时定时器已设置，' + Math.round(delayMs / 1000 / 60) + ' 分钟后触发');
}

// 清除任务的超时定时器
function clearTaskTimeout(task) {
  var timerId = _getTaskTimerId(task);
  if (taskTimeoutTimers[timerId]) {
    clearTimeout(taskTimeoutTimers[timerId]);
    delete taskTimeoutTimers[timerId];
    console.debug('[TaskTimer] 任务「' + task.name + '」超时定时器已清除');
  }
}

// 超时触发
function _triggerTaskTimeout(task) {
  if (!task || task.done) return;
  var timerId = _getTaskTimerId(task);
  delete taskTimeoutTimers[timerId];
  var emp = emps.find(function (e) {
    return e.id === task.assignee;
  });
  if (!emp) return;
  sendTaskReminderToEmp(emp, task);
}

// 初始化所有任务的超时定时器
function initTaskTimeoutTimers() {
  // 清除所有已有定时器
  Object.keys(taskTimeoutTimers).forEach(function (key) {
    clearTimeout(taskTimeoutTimers[key]);
    delete taskTimeoutTimers[key];
  });
  // 为所有未完成的、有 deadline 的任务设置定时器
  var scheduled = 0;
  ['todo', 'progress'].forEach(function (col) {
    tasksData[col].forEach(function (t) {
      if (t.deadline && !t.done) {
        scheduleTaskTimeout(t);
        scheduled++;
      }
    });
  });
  console.debug('[TaskTimer] 已初始化 ' + scheduled + ' 个任务的精确超时触发器');
}

// 兜底：每天零点检查一次是否有遗漏（防止浏览器休眠导致 setTimeout 延迟）
function checkTaskDeadlinesAndNotify() {
  var today = new Date().toISOString().split('T')[0];
  ['todo', 'progress'].forEach(function (col) {
    tasksData[col].forEach(function (t) {
      if (t.deadline && t.deadline < today && !t.done && t.assignee) {
        var emp = emps.find(function (e) {
          return e.id === t.assignee;
        });
        if (!emp) return;
        sendTaskReminderToEmp(emp, t);
      }
    });
  });
}

// 判断员工是否为 AI（有 OpenClaw/API 连接配置）
function isAIEmp(emp) {
  if (!emp) return false;
  // 服务端 /api/agents 返回的权威字段优先；本地/旧数据回退到连接方式启发式
  if (typeof emp.is_ai === 'boolean') return emp.is_ai;
  return emp.connectionType === 'openclaw' || emp.connectionType === 'api' || emp.openclawName || emp.apiKey;
}

// 发送任务提醒到员工聊天
async function sendTaskReminderToEmp(emp, task) {
  if (!emp || !emp.id || !task) return;

  // 避免重复提醒：检查今天是否已经提醒过
  var remindKey = 'sb_task_remind_' + emp.id + '_' + (task.id || task.name);
  var lastRemind = localStorage.getItem(remindKey);
  var todayStr = new Date().toDateString();
  if (lastRemind === todayStr) return;

  var reminderMsg = '⏰ 任务「' + task.name + '」已超时，请尽快处理';

  if (isAIEmp(emp)) {
    // AI 员工：保存到聊天记录 + 通过 OpenClaw 发送触发回复
    try {
      await apiFetch('/api/chat/' + encodeURIComponent(emp.id), {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          role: 'user',
          content: reminderMsg,
          skipAI: true,
          empId: emp.id
        })
      });
    } catch (e) {
      console.warn('[TaskReminder] 保存聊天记录失败:', e);
    }

    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      var sessionKey = 'agent:' + emp.id + ':chat';
      try {
        await _sendChatWaitForLifecycle(sessionKey, reminderMsg);
        console.debug('[TaskReminder] AI 员工 ' + emp.name + ' 已收到任务超时提醒');
      } catch (e) {
        console.warn('[TaskReminder] OpenClaw 发送失败:', e);
      }
    }
  } else {
    // 人类员工：发送通知（不保存到聊天记录）
    var notifyBody = '任务「' + task.name + '」已于 ' + task.deadline + ' 超时，请尽快处理';
    // 浏览器通知
    if (Notification.permission === 'granted') {
      new Notification('⏰ 任务超时提醒', {
        body: notifyBody,
        icon: emp.avatar || '📋'
      });
    }
    // Toast 通知
    showToast('📢 ' + emp.name + '：' + notifyBody);
    console.debug('[TaskReminder] 人类员工 ' + emp.name + ' 已收到通知');
  }

  // 标记今天已提醒
  localStorage.setItem(remindKey, todayStr);

  // 推送任务提醒通知到通知中心（保留现有提醒 UI 行为）
  if (typeof apiFetch === 'function') {
    apiFetch('/api/notifications', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        type: 'task_reminder',
        title: '⏰ 任务超时提醒',
        content: '任务「' + (task.name || '') + '」已于 ' + (task.deadline || '') + ' 超时，请尽快处理',
        agent_id: emp.id
      })
    }).then(function () {
      refreshUnreadBadge();
    }).catch(function (e) {
      console.warn('[TaskReminder] 推送通知失败:', e);
    });
  }
}

// 自动催促（定时检测）
let reminderInterval = null;
function startReminderCheck() {
  if (reminderInterval) clearInterval(reminderInterval);
  // 每30分钟兜底检测一次（setTimeout 可能因浏览器休眠延迟）
  reminderInterval = setInterval(function () {
    var reminder = generateReminderMsg();
    if (reminder) showReminder();
    checkTaskDeadlinesAndNotify();
  }, 30 * 60 * 1000);
  // 立即检测一次
  setTimeout(function () {
    showReminder();
  }, 3000);
}
function renderTaskBoard() {
  const columns = ['todo', 'progress', 'done'];
  const titles = {
    todo: '待办',
    progress: '进行中',
    done: '已完成'
  };
  columns.forEach(function (col) {
    const list = document.querySelector(`#taskBoard .task-column:nth-child(${columns.indexOf(col) + 1}) .task-list`);
    if (list) {
      list.innerHTML = tasksData[col].map(function (t) {
        return `<div class="task-card ${t.done ? 'done' : ''} ${col === 'progress' ? 'in-progress' : ''}" draggable="true" data-id="${escapeAttr(t.id || '')}">
<div class="task-card-header">
<div class="task-checkbox"></div>
<span class="task-name">${escapeHtml(t.name || '')}</span>
</div>
<div class="task-meta">
<div class="task-assignee"><div class="avatar" style="width:18px;height:18px;font-size:10px;background:${function () {
          var a = emps.find(function (x) {
            return x.id === t.assignee;
          });
          return escapeAttr(a && a.bg || '#888');
        }()}">${function () {
          var a = emps.find(function (x) {
            return x.id === t.assignee;
          });
          return escapeHtml(a && a.avatar || '?');
        }()}</div>${function () {
          var a = emps.find(function (x) {
            return x.id === t.assignee;
          });
          return escapeHtml(a && a.name || '未知');
        }()}</div>
<span class="task-priority ${escapeAttr(t.priority || 'normal')}">${t.priority === 'high' ? '高优' : '中优'}</span>
</div>
</div>`;
      }).join('');
      // Bind drag events
      list.querySelectorAll('.task-card').forEach(function (card) {
        card.addEventListener('dragstart', function (e) {
          card.classList.add('dragging');
          draggingTask = {
            id: card.dataset.id,
            from: col
          };
        });
        card.addEventListener('dragend', function (e) {
          card.classList.remove('dragging');
          draggingTask = null;
        });
      });
      list.addEventListener('dragover', function (e) {
        e.preventDefault();
        list.classList.add('drag-over');
      });
      list.addEventListener('dragleave', function (e) {
        if (!list.contains(e.relatedTarget)) list.classList.remove('drag-over');
      });
      list.addEventListener('drop', function (e) {
        e.preventDefault();
        list.classList.remove('drag-over');
        if (draggingTask && draggingTask.from !== col) {
          // Move task
          const taskIdx = tasksData[draggingTask.from].findIndex(function (t) {
            return t.id === draggingTask.id;
          });
          if (taskIdx > -1) {
            const task = tasksData[draggingTask.from].splice(taskIdx, 1)[0];
            task.done = col === 'done';
            // 管理超时定时器
            if (col === 'done') {
              clearTaskTimeout(task);
            } else if (draggingTask.from === 'done' && task.deadline) {
              // 从 done 拖回 todo/progress，重新设置定时器
              scheduleTaskTimeout(task);
            }
            tasksData[col].push(task);
            saveTasks();
            renderTaskBoard();
          }
        }
      });
    }
  });
}
const projs = [{
  id: 'proj1',
  name: '前端重构',
  progress: 60
}, {
  id: 'proj2',
  name: '小程序开发',
  progress: 35
}, {
  id: 'proj3',
  name: 'AI 集成',
  progress: 80
}];

// ========== 群组数据 ==========
const groups = [];

// 群组相关状态
let currentGroupId = null;
var gwStep = 1;
let gwEmoji = '🚀';
let gwMembers = [];
let gwLead = '';
let gwEditGroupId = null; // Track if wizard is editing an existing group

// Alias for compatibility
const employees = emps;

// Render employee list with archive/drag support
// 侧栏展开状态（持久化到 localStorage）
function getSidebarState() {
  var saved = localStorage.getItem('sb_sidebar_state');
  if (saved) {
    try {
      return JSON.parse(saved);
    } catch (e) {}
  }
  return {
    'AI 团队': true,
    '项目组': true,
    subCategories: {}  // ② 二级分类折叠状态, key = 原始 subCategory 名(含 '📌 置顶' / '未分组'), value = true(展开) / false(折叠)
  };
}
function saveSidebarState(state) {
  localStorage.setItem('sb_sidebar_state', JSON.stringify(state));
}
function toggleCategory(cat) {
  var state = getSidebarState();
  state[cat] = !state[cat];
  saveSidebarState(state);
  renderEmployeeList();
}
// ② 二级分类折叠 toggle(键是原始 subCategory 名, 默认展开)
function toggleSubCategory(name) {
  var state = getSidebarState();
  if (!state.subCategories) state.subCategories = {};
  // 默认展开(true), 折叠时存 false
  state.subCategories[name] = state.subCategories[name] === false ? true : false;
  saveSidebarState(state);
  renderEmployeeList();
}
// ④ 侧栏搜索 input handler(过滤词存 window 临时变量, 页面刷新即清空, 不入 localStorage)
function onSidebarEmpSearchInput(value) {
  window._empSearchQuery = value || '';
  renderEmployeeList();
}

// 获取当前用户可见的员工列表（权限过滤）
function isArchivedEmp(e) {
  if (!e) return false;
  if (e.status === 'archived') return true;
  if (e.archived === true || e.archived === 'true' || e.archived === 1 || e.archived === '1') return true;
  return false;
}

function archiveEmp(emp) {
  if (!emp) return;
  emp.archived = true;
  emp.status = 'archived';
  emp.archivedAt = new Date().toISOString();
}

function unarchiveEmp(emp) {
  if (!emp) return;
  emp.archived = false;
  emp.status = 'online';
  delete emp.archivedAt;
}

function getVisibleEmps() {
  if (!emps || emps.length === 0) return [];
  // 侧边栏职能列表只显示未归档的自己创建的 agents
  // 项目组成员（通过 mergeGroupMembersToEmps 合并进来的）只在项目组内部可见，不出现在侧边栏
  var uid = currentUser && (currentUser.userId || currentUser.id);
  var role = currentUser && currentUser.role;
  var active = emps.filter(function (e) {
    return !isArchivedEmp(e);
  });
  if (role === 'admin' || !uid) return active;
  if (role === 'leader') {
    var visibleIds = _getLeaderVisibleAgentIds();
    return active.filter(function (e) {
      return visibleIds.indexOf(e.id) >= 0 || e.createdBy === uid;
    });
  }
  return active.filter(function (e) {
    return e.createdBy === uid;
  });
}

// Leader可见的Agent ID列表（来自可视小组的members + agentIds）
function _getLeaderVisibleAgentIds() {
  var teams = window._visibleTeams || [];
  var ids = [];
  teams.forEach(function (t) {
    // 小组成员（user创建的emps），兼容字符串和字典两种格式
    if (t.members) {
      t.members.forEach(function (m) {
        var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
        if (mid && ids.indexOf(mid) < 0) ids.push(mid);
      });
    }
    // OpenClaw agents
    if (t.agentIds) {
      t.agentIds.forEach(function (aid) {
        if (ids.indexOf(aid) < 0) ids.push(aid);
      });
    }
  });
  return ids;
}
function renderEmployeeList() {
  var list = document.getElementById('employeeList');
  if (!list) return;
  var visible = getVisibleEmps().filter(function (e) {
    return !isArchivedEmp(e);
  });
  // ④ 侧栏搜索: query 是空就正常渲染, 非空就走"扁平匹配"分支(隐藏所有分组 header)
  var searchQuery = (window._empSearchQuery || '').trim().toLowerCase();
  var html = '';
  // ④ search input(始终在顶部, query 清空时恢复)
  html += '<div class="sidebar-emp-search-wrap">'
    + '<input class="sidebar-emp-search" type="text" id="sidebarEmpSearch" placeholder="搜索员工名字 / 角色" value="' + escapeAttr(window._empSearchQuery || '') + '" oninput="onSidebarEmpSearchInput(this.value)">'
    + (searchQuery ? '<button class="sidebar-emp-search-clear" onclick="onSidebarEmpSearchInput(\'\')" title="清空">✕</button>' : '')
    + '</div>';
  // ④ 搜索态: 隐藏所有一级二级分组 header + 置顶分组, 把匹配员工扁平渲染
  // (符合 spec: 搜索时折叠所有 subCategory header, 只显示扁平匹配结果)
  if (searchQuery) {
    var matched = visible.filter(function (e) {
      var name = (e.name || '').toLowerCase();
      var role = (e.role || e.position || getEmpRoleDisplay(e) || '').toLowerCase();
      var desc = (e.description || e.tagline || '').toLowerCase();
      return name.indexOf(searchQuery) >= 0 || role.indexOf(searchQuery) >= 0 || desc.indexOf(searchQuery) >= 0;
    });
    if (matched.length === 0) {
      html += '<div class="sidebar-emp-search-empty">无匹配员工</div>';
    } else {
      html += '<div class="sidebar-emp-search-results">';
      matched.forEach(function (e) {
        var isPinned = e.pinned;
        html += '<div class="list-item' + (isPinned ? ' pinned-item' : '') + '" data-id="' + escapeAttr(e.id) + '" draggable="true" oncontextmenu="event.preventDefault();showEmpSettingsMenu(event,this.dataset.id)"' + (isPinned ? ' style="background:var(--color-pinned-bg);"' : '') + '>' + renderEmployeeItem(e, true) + '</div>';
      });
      html += '</div>';
    }
    list.innerHTML = html;
    // 自动 focus search input(进入搜索态时, 光标保持在输入框)
    setTimeout(function () {
      var input = document.getElementById('sidebarEmpSearch');
      if (input) { try { input.focus(); input.setSelectionRange(input.value.length, input.value.length); } catch (e) {} }
    }, 0);
    return;
  }
  var state = getSidebarState();
  var canSeeEmployees = true;
  var canSeeGroups = hasModulePermission('groups');

  // ===== 一级分类：AI 团队 =====
  if (canSeeEmployees) {
    // ③ 视觉强化: 传 visible.length 做人数 chip
    html += renderCategoryHeader('AI 团队', state['AI 团队'], visible.length);
    if (state['AI 团队']) {
    if (visible.length === 0) {
      // ② 员工列表空状态: 视觉化引导(以前是纯文字 4 行, 叙事弱, AI 员工是核心的视觉传达缺失)
      // 现在用 V2.1 tokens 做"AI 员工 = 你的虚拟团队"叙事 + 主/次 CTA
      html += '<div class="emp-list-empty-state">'
        + '<div class="emp-list-empty-icon-wrap"><div class="emp-list-empty-icon">🦞</div><div class="emp-list-empty-ring"></div></div>'
        + '<div class="emp-list-empty-title">还没有 AI 员工</div>'
        + '<div class="emp-list-empty-desc">AI 员工是你的虚拟团队。每位 AI 员工可以服务达人群、分析商品、自动跟进合作。</div>'
        + '<button class="emp-list-empty-cta-primary" onclick="openWizard()">+ 创建第一位 AI 员工</button>'
        + '<button class="emp-list-empty-cta-secondary" onclick="showWhatCanAiEmpDo()">了解 AI 员工能做什么</button>'
        + '</div>';
    } else {
      // 置顶的排在前面
      visible.sort(function (a, b) {
        if (a.pinned && !b.pinned) return -1;
        if (!a.pinned && b.pinned) return 1;
        return 0;
      });

      // 分为置顶和普通两组
      var pinnedEmps = visible.filter(function (e) {
        return e.pinned;
      });
      var normalEmps = visible.filter(function (e) {
        return !e.pinned;
      });

      // ===== 置顶区域 =====
      if (pinnedEmps.length > 0) {
        var pinnedExpanded = state.subCategories && state.subCategories['📌 置顶'] === false ? false : true;
        html += '<div class="subcategory-group" data-subcat-group="📌 置顶" ' + (pinnedExpanded ? '' : 'data-collapsed="true"') + '>';
        html += renderSubCategoryHeader('📌 置顶', pinnedEmps.length, pinnedExpanded);
        html += '<div class="subcategory-content">';
        html += pinnedEmps.map(function (e) {
          // ③ 置顶区背景: 提 token 替代内联 rgba(22, 119, 255,0.04) + 略加深到 0.06 让置顶区可辨识
          return '<div class="list-item pinned-item" data-id="' + escapeAttr(e.id) + '" draggable="true" oncontextmenu="event.preventDefault();showEmpSettingsMenu(event,this.dataset.id)" style="background:var(--color-pinned-bg);">' + renderEmployeeItem(e, true) + '</div>';
        }).join('');
        html += '</div></div>';
      }

      // 按 subCategory 分组（不含已置顶的）
      var subGroups = {};
      normalEmps.forEach(function (e) {
        var sub = e.subCategory || '未分组';
        if (!subGroups[sub]) subGroups[sub] = [];
        subGroups[sub].push(e);
      });
      Object.keys(subGroups).forEach(function (sub) {
        var subExpanded = state.subCategories && state.subCategories[sub] === false ? false : true;
        html += '<div class="subcategory-group" data-subcat-group="' + escapeAttr(sub) + '" ' + (subExpanded ? '' : 'data-collapsed="true"') + '>';
        html += renderSubCategoryHeader(sub, subGroups[sub].length, subExpanded);
        html += '<div class="subcategory-content">';
        html += subGroups[sub].map(function (e) {
          return '<div class="list-item" data-id="' + escapeAttr(e.id) + '" draggable="true" oncontextmenu="event.preventDefault();showEmpSettingsMenu(event,this.dataset.id)">' + renderEmployeeItem(e, true) + '</div>';
        }).join('');
        html += '</div></div>';
      });
    }
    html += '<div class="sidebar-divider"></div>';
  }
}

  // ===== 一级分类：项目组 =====
  if (canSeeGroups) {
    // ③ 视觉强化: 传 groups.length 做人数 chip
    html += renderCategoryHeader('项目组', state['项目组'], (groups || []).length);
    if (state['项目组']) {
      html += renderGroupItems();
      // 添加 "+" 按钮
      html += '<div style="padding:4px 24px;display:flex;justify-content:center;" data-module="groups"><div class="group-add-btn" onclick="openGroupWizard()" title="创建项目组">+</div></div>';
    }
    html += '<div class="sidebar-divider"></div>';
  }

  // ★ ui/selection-dashboard-v21: 选品看板入口 (放在项目组下方, 归档上方)
  html += '<div class="sidebar-section">' +
    '<div class="sidebar-section-header" style="padding:8px 16px 4px;cursor:pointer;" onclick="openSelectionDashboard()">' +
      '<span style="font:600 11px/1 -apple-system,sans-serif;color:var(--color-text-secondary,#6E6E73);text-transform:uppercase;letter-spacing:0.05em;">📊 选品看板</span>' +
      '<span style="font:500 10px/1 -apple-system,sans-serif;color:var(--accent, #1677ff);">打开 →</span>' +
    '</div>' +
  '</div>';

  // 归档区域（合并1v1对话和项目组归档）
  var archived = emps.filter(function (e) {
    return isArchivedEmp(e);
  });
  var archived1v1 = archived.filter(function (e) {
    return !e.isProject;
  });
  var archivedProjects = archived.filter(function (e) {
    return e.isProject;
  });
  var totalArchived = archived1v1.length + archivedProjects.length;
  html += '<div class="archive-section">';
  html += '<div class="archive-header" onclick="this.nextElementSibling.classList.toggle(\'open\')" style="padding:10px 14px;cursor:pointer;">';
  html += '<span style="font:500 12px/1.3 -apple-system,sans-serif;color:#86868B;">归档</span>';
  html += '<span class="archive-count">' + totalArchived + '</span>';
  html += '</div>';
  html += '<div class="archive-content" style="padding:0 14px 8px;">';
  if (totalArchived === 0) {
    html += '<div style="font:400 12px/1.4 -apple-system,sans-serif;color:#AEAEB2;text-align:center;padding:12px 0;">暂无归档</div>';
  } else {
    // 1v1对话归档
    if (archived1v1.length > 0) {
      html += '<div style="font:500 11px/1.3 -apple-system,sans-serif;color:#AEAEB2;padding:8px 0 4px;">1v1对话</div>';
      archived1v1.forEach(function (e) {
        html += '<div class="list-item archived" data-id="' + escapeAttr(e.id) + '" style="padding:6px 12px;cursor:pointer;">';
        html += '<div style="display:flex;align-items:center;gap:8px;">';
        html += '<div class="avatar small" style="width:32px;height:32px;background:' + escapeAttr(e.bg || '') + ';border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;">' + renderAvatar(e, 32) + '</div>';
        html += '<div style="flex:1;min-width:0;"><div style="font-size:13px;font-weight:500;">' + escapeHtml(e.name || '') + '</div><div style="font-size:11px;color:var(--text-secondary);">' + escapeHtml(getEmpRoleDisplay(e)) + '</div></div>';
        html += '<button onclick="event.stopPropagation();restoreArchivedEmp(\'' + escapeAttr(e.id) + '\')" style="padding:3px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:11px;cursor:pointer;" title="恢复">📂</button>';
        html += '</div>';
        html += '</div>';
      });
    }
    // 项目组归档
    if (archivedProjects.length > 0) {
      html += '<div style="font:500 11px/1.3 -apple-system,sans-serif;color:#AEAEB2;padding:8px 0 4px;">项目组</div>';
      archivedProjects.forEach(function (e) {
        html += '<div class="list-item archived" data-proj="' + escapeAttr(e.id) + '" style="padding:6px 12px;cursor:pointer;">';
        html += '<div style="display:flex;align-items:center;gap:8px;">';
        html += '<div class="avatar small" style="width:32px;height:32px;background:' + escapeAttr(e.bg || '') + ';border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;">' + renderAvatar(e, 32) + '</div>';
        html += '<div style="flex:1;min-width:0;"><div style="font-size:13px;font-weight:500;">' + escapeHtml(e.name || '') + '</div><div style="font-size:11px;color:var(--text-secondary);">' + escapeHtml(getEmpRoleDisplay(e)) + '</div></div>';
        html += '<button onclick="event.stopPropagation();restoreArchivedEmp(\'' + escapeAttr(e.id) + '\')" style="padding:3px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:11px;cursor:pointer;" title="恢复">📂</button>';
        html += '</div>';
        html += '</div>';
      });
    }
  }
  html += '</div>';
  html += '</div>';
  list.innerHTML = html;
  bindListEvents();
}
function filterEmployeeList(query) {
  var q = query.trim().toLowerCase();
  var items = document.querySelectorAll('.list-item[data-id]');
  items.forEach(function (item) {
    var emp = emps.find(function (e) {
      return e.id === item.dataset.id;
    });
    if (emp) {
      var matchName = emp.name.toLowerCase().indexOf(q) >= 0;
      var matchRole = getEmpRoleDisplay(emp).toLowerCase().indexOf(q) >= 0;
      item.style.display = !q || matchName || matchRole ? '' : 'none';
    }
  });
}

// 渲染侧栏底部的归档区域
function renderArchiveSections() {
  var archived = emps.filter(function (e) {
    return isArchivedEmp(e);
  });
  var archived1v1 = archived.filter(function (e) {
    return !e.isProject;
  });
  var archivedProjects = archived.filter(function (e) {
    return e.isProject;
  });

  // 更新 1v1 对话归档
  var archive1v1 = document.getElementById('archive-1v1');
  if (archive1v1) {
    var count1v1 = archive1v1.querySelector('.archive-count');
    var content1v1 = archive1v1.querySelector('.archive-content');
    if (count1v1) count1v1.textContent = archived1v1.length;
    if (content1v1) {
      if (archived1v1.length === 0) {
        content1v1.innerHTML = '<div class="archive-empty">暂无归档对话</div>';
      } else {
        content1v1.innerHTML = archived1v1.map(function (e) {
          return '<div class="list-item archived" data-id="' + escapeAttr(e.id) + '" style="padding:8px 12px;cursor:pointer;">' + '<div class="item-avatar" style="width:40px;height:40px;"><div class="avatar small" style="width:40px;height:40px;background:' + escapeAttr(e.bg || '') + ';">' + renderAvatar(e, 40) + '</div></div>' + '<div class="item-info"><div class="item-name" style="font-size:14px;">' + escapeHtml(e.name || '') + '</div><div class="item-msg" style="font-size:12px;">' + escapeHtml(getEmpRoleDisplay(e)) + '</div></div>' + '<div class="archive-actions" style="display:flex;gap:4px;align-items:center;flex-shrink:0;">' + '<button onclick="event.stopPropagation();downloadArchive(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="下载对话记录">📥</button>' + '<button onclick="event.stopPropagation();continueArchive(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="继续对话">💬</button>' + '<button onclick="event.stopPropagation();restoreArchivedEmp(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="恢复员工">📂</button>' + '</div>' + '</div>';
        }).join('');
      }
    }
  }

  // 更新项目组归档
  var archiveProjects = document.getElementById('archive-projects');
  if (archiveProjects) {
    var countProj = archiveProjects.querySelector('.archive-count');
    var contentProj = archiveProjects.querySelector('.archive-content');
    if (countProj) countProj.textContent = archivedProjects.length;
    if (contentProj) {
      if (archivedProjects.length === 0) {
        contentProj.innerHTML = '<div class="archive-empty">暂无归档项目</div>';
      } else {
        contentProj.innerHTML = archivedProjects.map(function (e) {
          return '<div class="list-item archived" data-proj="' + escapeAttr(e.id) + '" style="padding:8px 12px;cursor:pointer;">' + '<div class="item-avatar" style="width:40px;height:40px;"><div class="avatar small" style="width:40px;height:40px;background:' + escapeAttr(e.bg || '') + ';">' + renderAvatar(e, 40) + '</div></div>' + '<div class="item-info"><div class="item-name" style="font-size:14px;">' + escapeHtml(e.name || '') + '</div><div class="item-msg" style="font-size:12px;">' + escapeHtml(getEmpRoleDisplay(e)) + '</div></div>' + '<div class="archive-actions" style="display:flex;gap:4px;align-items:center;flex-shrink:0;">' + '<button onclick="event.stopPropagation();downloadArchive(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="下载群聊记录">📥</button>' + '<button onclick="event.stopPropagation();continueArchive(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="继续群聊">💬</button>' + '<button onclick="event.stopPropagation();restoreArchivedEmp(\'' + escapeAttr(e.id) + '\')" style="padding:4px 8px;border:1px solid var(--border-color);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;" title="恢复群组">📂</button>' + '</div>' + '</div>';
        }).join('');
      }
    }
  }
}
function renderCategoryHeader(name, expanded, count) {
  // ① 修折叠 bug: 旧实现完全忽略 expanded 参数, 只渲染纯文字。
  // ③ 视觉强化: 一级分类头加 outline AI 徽章(仅 AI 团队) + 人数 chip
  // chevron + 标题 + click 绑现有 toggleCategory(name) + class 反映状态
  var isAi = name === 'AI 团队';
  var aiBadge = isAi ? '<span class="section-ai-badge">AI</span>' : '';
  var countChip = (typeof count === 'number' && count > 0) ? '<span class="section-count-chip">' + count + '</span>' : '';
  return '<div class="section-title section-title-collapsible ' + (expanded ? 'expanded' : 'collapsed') + '" data-category="' + escapeAttr(name) + '" onclick="toggleCategory(\'' + escapeAttr(name) + '\')">'
    + '<span class="section-chevron">▾</span>'
    + '<span class="section-title-text">' + escapeHtml(name) + '</span>'
    + aiBadge
    + countChip
    + '</div>';
}
function renderSubCategoryHeader(name, count, expanded) {
  // ② 二级分类可折叠: 复用同款 chevron + click, 状态由调用方从 state.subCategories 传入
  var displayName = name === '未分组' ? '全部' : name;
  var isExpanded = expanded !== false;  // 默认 true(展开), 折叠时为 false
  return '<div class="dept-header dept-header-collapsible ' + (isExpanded ? 'expanded' : 'collapsed') + '" data-subcat="' + escapeAttr(name) + '" onclick="toggleSubCategory(\'' + escapeAttr(name) + '\')">'
    + '<span class="dept-chevron">▾</span>'
    + '<span class="dept-title">' + escapeHtml(displayName) + '</span>'
    + '<span class="dept-count">' + count + '</span>'
    + '</div>';
}
function renderProjectGroupPlaceholder(name, count) {
  // 兼容旧调用，但新渲染通过 renderGroupItems 实现
  return '<div class="list-item proj-placeholder" style="padding-left:24px;opacity:0.7;" onclick="showToast(\'项目组群聊功能开发中...\')">' + '<div class="item-avatar" style="width:36px;height:36px;"><div class="avatar small" style="width:36px;height:36px;background:var(--bg-tertiary);">👥</div></div>' + '<div class="item-info"><div class="item-name">' + escapeHtml(name) + '</div><div class="item-msg">' + count + ' 人 · 群聊入口</div></div>' + '</div>';
}

// ========== 群组数据管理 ==========
async function loadGroups() {
  var token = localStorage.getItem('sb_auth_token');
  if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
    try {
      var resp = await apiFetch('/api/groups');
      if (resp && resp.ok) {
        var data = await resp.json();
        if (data && Array.isArray(data)) {
          groups.length = 0;
          data.forEach(function (g) {
            groups.push(g);
          });
          // 〔fix/sb2-side-restore commit 2〕同步写到 window.projects
          //   messages renderer (line 45891) + dashboard renderer (commit 2 新版) 共享读
          //   之前只有 `groups` 全局, window.projects 始终空数组 → messages renderer fallback []
          //   现在两边都能拿到真项目组, 跨模块共享 window 缓存
          if (typeof window !== 'undefined') window.projects = groups.slice();
          // 把项目组成员信息合并到 emps，供成员列表和 @提及使用
          mergeGroupMembersToEmps();
          return;
        }
      }
    } catch (e) {
      console.debug('[Groups] 后端加载失败:', e);
    }
  }
}

// 将项目组中的成员信息合并到 emps，使 @提及和成员列表能正常工作
// 同时保证侧边栏只显示当前用户创建的（通过 getVisibleEmps 过滤）
function mergeGroupMembersToEmps() {
  var seenIds = {};
  emps.forEach(function (e) {
    if (e.id) seenIds[e.id] = true;
  });
  groups.forEach(function (g) {
    (g.members || []).forEach(function (m) {
      if (typeof m === 'object' && m !== null && m.id) {
        var existing = emps.find(function (e) { return e.id === m.id; });
        if (existing) {
          // 如果本地员工缺少 openclawName/头像等字段，用项目组成员里的信息补全
          if (!existing.openclawName && m.openclawName) {
            existing.openclawName = m.openclawName;
            console.debug('[mergeGroupMembersToEmps] 补全 openclawName:', m.id, m.openclawName);
          }
          if (!existing.name && m.name) existing.name = m.name;
          if (!existing.bg && m.bg) existing.bg = m.bg;
          if (!existing.avatar && m.avatar) existing.avatar = m.avatar;
          return;
        }
        if (!m.name) {
          console.warn('[mergeGroupMembersToEmps] 跳过无名成员:', m.id);
          return;
        }
        emps.push(m);
        seenIds[m.id] = true;
      }
    });
  });
}

// 按名字查找员工：优先在 emps 中找完整对象，找不到时在所有项目组成员中找基础信息
function _stripRoleSuffix(n) {
  if (!n) return '';
  return n.replace(/[\s]*[（(].*?[）)][\s]*$/g, '').trim();
}
function findEmpByNameAcrossGroups(name) {
  if (!name) return null;
  // 去掉可能的角色后缀，如 "张三（设计师）" -> "张三"
  var baseName = _stripRoleSuffix(name);
  var candidates = [name, baseName];
  for (var c = 0; c < candidates.length; c++) {
    var q = candidates[c];
    if (!q) continue;
    // 1. 精确匹配员工全名（如全名就是 q）
    var inEmps = emps.find(function (e) {
      return e && e.name === q;
    });
    if (inEmps) return inEmps;
    // 2. 匹配去掉角色后缀后的纯名字（如员工全名"萧何（分析师）"，@的是"萧何"）
    inEmps = emps.find(function (e) {
      return e && _stripRoleSuffix(e.name) === q;
    });
    if (inEmps) return inEmps;
    for (var i = 0; i < groups.length; i++) {
      var members = groups[i].members || [];
      for (var j = 0; j < members.length; j++) {
        var m = members[j];
        if (m && typeof m === 'object' && (m.name === q || _stripRoleSuffix(m.name) === q)) {
          return m;
        }
      }
    }
  }
  return null;
}

// 解析被 @ 的名字：先按完整候选查找，找不到时从末尾逐步截断，
// 支持 "@萧何了""@萧何？""@萧何（运营）" 等带标点/语气词/角色后缀的情况
function resolveMentionedName(nameCandidate) {
  if (!nameCandidate) return null;
  var name = nameCandidate.replace(/[\s]*[（(].*?[）)][\s]*$/g, '').trim();
  // 逐步截断后缀（中文语气词、标点、冗余字符），直到找到员工或为空
  while (name.length > 0) {
    var emp = findEmpByNameAcrossGroups(name);
    if (emp) return emp;
    name = name.slice(0, -1).trim();
  }
  return null;
}

// 按 ID 查找员工：优先在 emps 中找完整对象，找不到时在所有项目组成员中找基础信息
function findEmpByIdAcrossGroups(id) {
  if (!id) return null;
  var inEmps = emps.find(function (e) {
    return e && e.id === id;
  });
  if (inEmps) return inEmps;
  for (var i = 0; i < groups.length; i++) {
    var members = groups[i].members || [];
    for (var j = 0; j < members.length; j++) {
      var m = members[j];
      if (m && typeof m === 'object' && m.id === id) {
        return m;
      }
    }
  }
  return null;
}

async function saveGroups() {
  var token = localStorage.getItem('sb_auth_token');
  if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
    try {
      // Try batch save
      await apiFetch('/api/groups', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(groups)
      });
    } catch (e) {
      console.debug('[Groups] 保存到后端失败:', e);
    }
  }
}

// 项目组轮询：子账号页面自动感知管理员新增的项目组
var _groupPollTimer = null;
var _lastKnownGroupIds = '';
function startGroupPolling() {
  // 先记录当前已有的项目组 ID
  _lastKnownGroupIds = groups.map(function (g) { return g.id; }).sort().join(',');

  if (_groupPollTimer) clearInterval(_groupPollTimer);
  _groupPollTimer = setInterval(function () {
    _pollGroupsForUpdates();
  }, 15000);
}

function stopGroupPolling() {
  if (_groupPollTimer) {
    clearInterval(_groupPollTimer);
    _groupPollTimer = null;
  }
}

async function _pollGroupsForUpdates() {
  if (typeof apiFetch !== 'function') return;
  var token = localStorage.getItem('sb_auth_token');
  if (!token || token === 'local_mode') return;

  try {
    var resp = await apiFetch('/api/groups');
    if (!resp || !resp.ok) return;
    var data = await resp.json();
    if (!data || !Array.isArray(data)) return;

    // 检测是否有新增项目组
    var newGroupIds = data.map(function (g) { return g.id; }).sort().join(',');
    var currentIds = groups.map(function (g) { return g.id; }).sort().join(',');

    if (newGroupIds !== currentIds) {
      // 找出新增的项目组
      var currentIdSet = {};
      groups.forEach(function (g) { currentIdSet[g.id] = true; });
      var newGroups = data.filter(function (g) { return !currentIdSet[g.id]; });

      // 更新本地 groups 数组
      groups.length = 0;
      data.forEach(function (g) { groups.push(g); });

      // 刷新侧栏
      renderEmployeeList();

      // 提示用户
      if (newGroups.length > 0) {
        var names = newGroups.map(function (g) { return g.name; }).join('、');
        showToast('📁 你被加入了新项目：' + names);
      }

      console.debug('[GroupPoll] 项目组列表已更新，新增 ' + newGroups.length + ' 个');
    }
  } catch (e) {
    console.debug('[GroupPoll] 轮询失败:', e);
  }
}

// 渲染侧栏群组列表
/* 〔r82 2026-10-08 项目组重设计〕圆形 40px 内联硬编 → 圆角块瓷贴 sb2-grp-tile + active 收编 token,
   结构/行为(onclick 群聊 / 右键详情)不变 */
function renderGroupItems() {
  if (groups.length === 0) {
    return '<div style="padding:8px 24px;font-size:12px;color:var(--text-tertiary);">暂无项目组，点击 + 创建</div>';
  }
  return groups.map(function (g) {
    var memberCount = (g.members || []).length;
    var lastMsg = g.lastMsg || '';
    var emoji = g.emoji || '👥';
    var isActive = currentGroupId === g.id;
    return '<div class="list-item sb2-grp-item' + (isActive ? ' active' : '') + '" data-group="' + escapeAttr(g.id) + '" onclick="openGroupChat(\'' + escapeAttr(g.id) + '\')" oncontextmenu="event.preventDefault();event.stopPropagation();openGroupDetail(\'' + escapeAttr(g.id) + '\');">' + '<div class="sb2-grp-tile" style="background:linear-gradient(135deg,' + escapeAttr(g.bg || '#5856D6') + ',' + (g.bg || '#5856D6') + 'dd);">' + escapeHtml(emoji) + '</div>' + '<div class="group-item-info"><div class="group-item-name">' + escapeHtml(g.name) + '</div><div class="group-item-sub">' + memberCount + ' 人' + (lastMsg ? ' · ' + escapeHtml(lastMsg) : '') + '</div></div>' + '</div>';
  }).join('');
}

// 侧栏点击事件统一在 renderEmployeeList() 后的 bindListEvents() 中绑定，避免重复
// Setup drag for employee items
emps.forEach(function (e) {
  const el = document.querySelector(`.list-item[data-id="${e.id}"]`);
  if (el) {
    el.draggable = true;
    el.addEventListener('dragstart', function (ev) {
      el.classList.add('dragging');
      ev.dataTransfer.setData('text/plain', e.id);
    });
    el.addEventListener('dragend', function (ev) {
      el.classList.remove('dragging');
      document.querySelectorAll('.list-item').forEach(function (x) {
        return x.classList.remove('drag-over');
      });
    });
    el.addEventListener('dragover', function (ev) {
      ev.preventDefault();
      if (!el.classList.contains('dragging')) el.classList.add('drag-over');
    });
    el.addEventListener('dragleave', function () {
      return el.classList.remove('drag-over');
    });
    el.addEventListener('drop', function (ev) {
      ev.preventDefault();
      el.classList.remove('drag-over');
      const draggedId = ev.dataTransfer.getData('text/plain');
      if (draggedId === e.id) return;
      const emp = emps.find(function (x) {
        return x.id === draggedId;
      });
      if (emp) {
        if (isArchivedEmp(emp)) {
          unarchiveEmp(emp);
        } else {
          archiveEmp(emp);
        }
        saveEmployees();
        if (typeof syncEmpToServer === 'function') syncEmpToServer(emp).catch(function () {});
        renderEmployeeList();
      }
    });
    // Right-click handled by event delegation on employeeList
  }
});
document.getElementById('sendBtn').addEventListener('click', sendMsg);
document.getElementById('msgInput').addEventListener('keydown', function (e) {
  const slashActive = document.getElementById('slashMenu').classList.contains('active');
  // 按 / 键弹出斜杠命令菜单
  if (e.key === '/' && !slashActive) {
    e.preventDefault();
    document.getElementById('slashMenu').classList.add('active');
    document.getElementById('slashSearch').value = '';
    filterSlashCommands('');
    mentionMode = false;
    var md = document.getElementById('mentionDropdown');
    if (md) md.classList.remove('active');
    // 聚焦搜索框
    setTimeout(function () {
      return document.getElementById('slashSearch').focus();
    }, 0);
    return;
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    if (slashActive) {
      e.preventDefault();
      const sel = document.querySelector('#slashSections .slash-item.selected');
      if (sel) execCmd(sel.dataset.cmd);
    } else if (mentionMode) {
      e.preventDefault();
      var items = document.querySelectorAll('#mentionDropdown .mention-item');
      var sel = items[mentionIdx] || items[0];
      if (sel && sel.dataset.id) pickMention(sel.dataset.id);
    } else {
      e.stopPropagation();
      sendMsg();
    }
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    if (slashActive) navigateSlash(1);else if (mentionMode) selectMention(1);
  }
  if (e.key === 'ArrowUp') {
    e.preventDefault();
    if (slashActive) navigateSlash(-1);else if (mentionMode) selectMention(-1);
  }
  if (e.key === 'Escape') {
    var md = document.getElementById('mentionDropdown');
    if (md && md.classList.contains('active')) {
      md.classList.remove('active');
      mentionMode = false;
      mentionIdx = 0;
    }
  }
});
document.getElementById('msgInput').addEventListener('input', handleInput);
// 图片粘贴/拖拽/附件上传
(function () {
  var input = document.getElementById('msgInput');
  var wrap = input && input.closest('.input-wrap-v2');
  if (wrap) {
    wrap.addEventListener('paste', function (e) {
      if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length > 0) {
        handleImageFiles(e.clipboardData.files);
      }
    });
    wrap.addEventListener('dragover', function (e) {
      e.preventDefault();
      wrap.style.background = '#E5E5EA';
    });
    wrap.addEventListener('dragleave', function (e) {
      wrap.style.background = '';
    });
    wrap.addEventListener('drop', function (e) {
      e.preventDefault();
      wrap.style.background = '';
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        handleImageFiles(e.dataTransfer.files);
      }
    });
  }
  var fileInput = document.getElementById('imageFileInput');
  if (fileInput) {
    fileInput.addEventListener('change', function () {
      handleImageFiles(fileInput.files);
      fileInput.value = '';
    });
  }
})();
(function () {
  var el = document.getElementById('mentionBtn');
  if (el) el.addEventListener('click', function () {
    showMention();
  });
})();
document.querySelectorAll('.mention-item').forEach(function (i) {
  return i.addEventListener('click', function () {
    return pickMention(i.dataset.id);
  });
});
document.querySelectorAll('.drawer-tab').forEach(function (t) {
  return t.addEventListener('click', function () {
    return switchDrawerTab(t.dataset.tab);
  });
});
document.addEventListener('click', function (e) {
  if (!e.target.closest('.mention-dropdown')) {
    var el = document.getElementById('mentionDropdown');
    if (el && el.classList.contains('active')) {
      el.classList.remove('active');
      mentionMode = false;
      mentionIdx = 0;
    }
  }
  if (!e.target.closest('.slash-menu')) document.getElementById('slashMenu').classList.remove('active');
  if (!e.target.closest('.chat-more-menu')) closeChatMoreMenu();
});
document.querySelectorAll('.member-option').forEach(function (o) {
  return o.addEventListener('click', function () {
    return o.classList.toggle('selected');
  });
});

// Tab Switch (legacy, no-op since projects are merged into messages list)
function switchTab(tab) {
  currentTab = tab;
}

// ===== Archive & Drag =====
let contextTarget = null;
function renderArchivedList() {
  var list = document.getElementById('employeeList');
  var archived = emps.filter(function (e) {
    return isArchivedEmp(e);
  });
  if (archived.length === 0) {
    list.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-secondary)"><div style="font-size:32px;margin-bottom:12px">📦</div><div>暂无归档</div></div>';
    return;
  }
  var html = '';
  archived.forEach(function (e) {
    var dataAttr = e.isProject ? 'data-proj' : 'data-id';
    html += '<div class="list-item archived" ' + dataAttr + '="' + escapeAttr(e.id || '') + '" draggable="true">' + renderEmployeeItem(e, true) + '</div>';
  });
  list.innerHTML = html;
  bindListEvents();
}
function renderAvatar(emp, size) {
  size = size || 36;
  // AI 员工/项目组标识：加 .ai-employee 类以触发光晕+徽章
  var isAI = !!(typeof isAIEmp === 'function' ? isAIEmp(emp) : (emp && (emp.isAI || emp.is_ai)));
  var wrapCls = 'emp-avatar-wrap' + (isAI ? ' ai-employee' : '');
  var inner;
  // 1. Number index -> use AVATAR_PRESETS
  if (typeof emp.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[emp.avatar]) {
    inner = '<img src="' + escapeAttr(AVATAR_PRESETS[emp.avatar]) + '" style="width:' + size + 'px;height:' + size + 'px;border-radius:50%;object-fit:cover;">';
  }
  // 2. Image path or data URL
  else if (emp.avatar && typeof emp.avatar === 'string' && (emp.avatar.indexOf('data:image') === 0 || emp.avatar.indexOf('.png') > 0 || emp.avatar.indexOf('.jpg') > 0 || emp.avatar.indexOf('.jpeg') > 0 || emp.avatar.indexOf('avatars/') === 0)) {
    inner = '<img src="' + escapeAttr(emp.avatar) + '" style="width:' + size + 'px;height:' + size + 'px;border-radius:50%;object-fit:cover;">';
  }
  // 3. Letter/emoji string or any unknown -> use name hash to pick a preset avatar
  else if (typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS.length > 0) {
    var hash = 0;
    var name = emp.name || emp.id || '';
    for (var c = 0; c < name.length; c++) {
      hash = (hash << 5) - hash + name.charCodeAt(c);
      hash = hash & hash;
    }
    var idx = Math.abs(hash) % AVATAR_PRESETS.length;
    inner = '<img src="' + escapeAttr(AVATAR_PRESETS[idx]) + '" style="width:' + size + 'px;height:' + size + 'px;border-radius:50%;object-fit:cover;">';
  }
  // 4. Ultimate fallback: styled letter avatar
  else {
    var name = emp.name || emp.id || '?';
    var letter = escapeHtml(name.charAt(0).toUpperCase());
    var colors = ['#FF6B35', '#FF9500', '#FFCC00', '#34C759', '#1677ff', '#5856D6', '#AF52DE', '#FF2D55'];
    var hash = 0;
    for (var c = 0; c < name.length; c++) {
      hash = (hash << 5) - hash + name.charCodeAt(c);
      hash = hash & hash;
    }
    var bg = colors[Math.abs(hash) % colors.length];
    inner = '<div style="width:' + size + 'px;height:' + size + 'px;border-radius:50%;background:' + bg + ';color:#fff;display:flex;align-items:center;justify-content:center;font-size:' + (size * 0.45) + 'px;font-weight:600;">' + letter + '</div>';
  }
  return '<span class="' + wrapCls + '">' + inner + '</span>';
}
function renderEmployeeItem(e, showCreator) {
  var size = 40;
  var avatarContent = renderAvatar(e, size);
  var creatorTag = '';

  // admin/leader视图：显示创建者标签（回退链 createdByName → _createdByName → role → "创建者"，
  // 全空时不再渲染空 span 假装没事，直接显"创建者"占位，避免 admin 列表看到空白行像 bug）
  if (showCreator && (isAdmin() || isLeader()) && e.createdBy && e.createdBy !== (currentUser && currentUser.id)) {
    var creatorName = e.createdByName || e._createdByName || e.role || '创建者';
    creatorTag = '<span style="display:inline-flex;align-items:center;margin-left:6px;padding:1px 6px;background:#E8E8E8;border-radius:4px;font-size:11px;color:#999999;vertical-align:middle;font-weight:500;">👤 ' + escapeHtml(creatorName) + '</span>';
  }

  var statusClass = escapeAttr(e.status || 'offline');
  var lastMsgPreview = '';
  if (e.lastMessage) {
    lastMsgPreview = escapeHtml(e.lastMessage);
  }
  var previewHtml = lastMsgPreview
    ? '<div class="emp-preview" style="margin-top:2px;">' + lastMsgPreview + '</div>'
    : '<div class="emp-preview" style="color:var(--text-tertiary);">暂无消息</div>';
  var statusLabel = e.status === 'online' ? '在线' : e.status === 'busy' ? '忙碌' : '离线';
  return '<div class="emp-avatar-wrap" style="position:relative;flex-shrink:0;width:40px;height:40px;"><div class="emp-avatar" style="width:40px;height:40px;border-radius:50%;overflow:hidden;display:flex;align-items:center;justify-content:center;">' + avatarContent + '</div><div class="status-dot ' + statusClass + '" style="position:absolute;bottom:0px;right:0px;width:10px;height:10px;border-radius:50%;border:2px solid #ffffff;"></div></div><div class="emp-info" style="flex:1;min-width:0;"><div class="emp-name" style="font-size:14px;font-weight:500;color:var(--text-primary);display:flex;align-items:center;">' + escapeHtml(e.name || '') + creatorTag + '</div>' + previewHtml + '</div><div class="emp-meta" style="flex-shrink:0;text-align:right;margin-left:8px;"><div class="status-text" style="font-size:12px;color:var(--text-secondary);">' + statusLabel + '</div></div>';
}
function bindListEvents() {
  // data-id = 员工（个人聊天）
  document.querySelectorAll('.list-item[data-id]').forEach(function (i) {
    var empId = i.dataset.id;
    // Click -> open chat
    i.addEventListener('click', function (ev) {
      // 如果点击的是操作按钮，不打开聊天
      if (ev.target.closest('.item-actions')) return;
      if (currentTab !== 'archived') openChat(empId);
    });
    // Double-click -> open detail
    i.addEventListener('dblclick', function () {
      if (empId) openEmpDetail(empId);
    });
    // Detail button
    var detailBtn = i.querySelector('.btn-detail');
    if (detailBtn) {
      detailBtn.addEventListener('mousedown', function (ev) {
        ev.stopPropagation();
      });
      detailBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        showToast('detailBtn clicked: ' + empId, 'info');
      });
    }
    // More button
    var moreBtn = i.querySelector('.emp-more-btn');
    if (moreBtn) {
      moreBtn.addEventListener('mousedown', function (ev) {
        ev.stopPropagation();
      });
      moreBtn.addEventListener('click', function (ev) {
        ev.stopPropagation();
        ev.preventDefault();
        // 复用 empSettingsMenu(showEmpSettingsMenu 已支持左键 + 右键 + 位置计算 + 关闭外点击)
        showEmpSettingsMenu(ev, empId);
      });
    }
    // Drag
    i.draggable = true;
    i.addEventListener('dragstart', function (e) {
      i.classList.add('dragging');
      e.dataTransfer.setData('text/plain', i.dataset.id || i.dataset.proj);
    });
    i.addEventListener('dragend', function (e) {
      i.classList.remove('dragging');
      document.querySelectorAll('.list-item').forEach(function (x) {
        return x.classList.remove('drag-over');
      });
    });
    i.addEventListener('dragover', function (e) {
      e.preventDefault();
      if (!i.classList.contains('dragging')) i.classList.add('drag-over');
    });
    i.addEventListener('dragleave', function () {
      return i.classList.remove('drag-over');
    });
    i.addEventListener('drop', function (e) {
      e.preventDefault();
      i.classList.remove('drag-over');
      const draggedId = e.dataTransfer.getData('text/plain');
      if (draggedId === i.dataset.id) return;
      const emp = employees.find(function (x) {
        return x.id === draggedId;
      });
      if (emp) {
        emp.archived = !emp.archived;
        renderEmployeeList();
      }
    });
  });
  // data-proj = 项目/归档项目（项目视图）
  document.querySelectorAll('.list-item[data-proj]').forEach(function (i) {
    var projId = i.dataset.proj;
    // Click -> open project chat
    i.addEventListener('click', function (ev) {
      if (ev.target.closest('.archive-actions')) return;
      openChat(projId);
    });
  });
}
function showContextMenu(x, y) {
  const menu = document.getElementById('contextMenu');
  const emp = employees.find(function (e) {
    return e.id === contextTarget;
  });
  if (!emp) return;
  menu.querySelector('[onclick*="archive"]').style.display = emp.archived ? 'none' : 'flex';
  menu.querySelector('[onclick*="unarchive"]').style.display = emp.archived ? 'flex' : 'none';
  menu.style.left = x + 'px';
  menu.style.top = y + 'px';
  menu.classList.add('active');
}
function hideContextMenu() {
  document.getElementById('contextMenu').classList.remove('active');
  contextTarget = null;
}
function ctxAction(action) {
  if (!contextTarget) return;
  const emp = emps.find(function (e) {
    return e.id === contextTarget;
  });
  if (!emp) return;
  switch (action) {
    case 'detail':
      openEmpDetail(contextTarget);
      break;
    case 'edit':
      showEditModal(contextTarget);
      break;
    case 'badge-urgent':
      emp.badge = {
        level: 'urgent',
        number: 1
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🔴 已标记为紧急');
      break;
    case 'badge-important':
      emp.badge = {
        level: 'important',
        number: null
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🟠 已标记为重要');
      break;
    case 'badge-normal':
      emp.badge = {
        level: 'normal',
        number: null
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🔵 已标记为普通');
      break;
    case 'badge-clear':
      emp.badge = null;
      saveEmployees();
      renderEmployeeList();
      showToast('✕ 已清除紧急标记');
      break;
    case 'pin':
      emp.pinned = !emp.pinned;
      showToast(emp.pinned ? '📌 已置顶' : '📌 已取消置顶');
      saveEmployees();
      syncEmpToServer(emp);
      break;
    // case 'move' - moved to submenu
    case 'archive':
      archiveEmp(emp);
      saveEmployees();
      syncEmpToServer(emp);
      showToast('📦 已归档');
      break;
    case 'unarchive':
      unarchiveEmp(emp);
      saveEmployees();
      syncEmpToServer(emp);
      showToast('📥 已取消归档');
      break;
    case 'delete':
      if (confirm('确定删除 ' + emp.name + '？')) {
        var _delId = contextTarget;
        var _wasCurrent2 = localStorage.getItem('sb_current_emp') === _delId;
        var _delEmp2 = emps.find(function (e) { return e.id === _delId; });
        // ★ 优化删除 UX：保留原始状态用于失败回滚（避免"假删除"误导）
        var _delOrig2 = _delEmp2 ? {
          status: _delEmp2.status,
          archived: _delEmp2.archived,
          archivedAt: _delEmp2.archivedAt
        } : null;
        if (_delEmp2) {
          _delEmp2.status = 'archived';
          _delEmp2.archived = true;
          _delEmp2.archivedAt = new Date().toISOString();
        }
        clearChatOnServer(_delId, 'personal');
        if (typeof apiFetch === 'function') {
          apiFetch('/api/agents/' + _delId, {
            method: 'DELETE'
          }).then(function () {
            // 成功：刷新列表对齐服务端状态（防本地-服务端漂移）
            showToast('🗑️ 已删除');
            if (typeof loadEmployees === 'function') loadEmployees();
          }).catch(function (e) {
            // 失败：回滚乐观更新，提示真实错误（不再让用户看到虚假的"已删除"）
            if (_delEmp2 && _delOrig2) {
              _delEmp2.status = _delOrig2.status;
              _delEmp2.archived = _delOrig2.archived;
              if (_delOrig2.archivedAt) {
                _delEmp2.archivedAt = _delOrig2.archivedAt;
              } else {
                delete _delEmp2.archivedAt;
              }
            }
            renderEmployeeList();
            showToast('❌ 删除失败：' + (e && e.message ? e.message : '未知错误'));
            console.error('[deleteAgent] 后端删除失败:', e);
          });
        } else {
          showToast('❌ apiFetch 未定义，无法删除');
        }
        if (currentEmpId === _delId) closeEmpDetail();
        if (_wasCurrent2) {
          openNextEmployeeOrEmpty();
        } else {
          renderMsgs('private');
        }
      }
      break;
  }
  renderEmployeeList();
  hideContextMenu();
}

// Employee Settings Dropdown (⋮ button)
var empSettingsTarget = null;
// 全局按钮处理函数
function showEmpSettingsMenu(event, empId) {
  try {
    empSettingsTarget = empId;
    // Show unarchive option for archived employees, hide archive
    var targetEmp = emps.find(function (e) {
      return e.id === empId;
    });
    var unarchiveEl = document.getElementById('empMenuUnarchive');
    var archiveEl = document.querySelector('#empSettingsMenu .menu-item[onclick*="archive"]');
    if (unarchiveEl && archiveEl) {
      if (targetEmp && targetEmp.archived) {
        unarchiveEl.style.display = '';
        archiveEl.style.display = 'none';
      } else {
        unarchiveEl.style.display = 'none';
        archiveEl.style.display = '';
      }
    }
    var menu = document.getElementById('empSettingsMenu');
    if (!menu) {
      console.error('[showEmpSettingsMenu] menu element NOT FOUND');
      return;
    }
    // Support both button click (position from button) and right-click (position from mouse)
    if (event.type === 'contextmenu') {
      // Right-click: position at mouse cursor with viewport boundary check
      positionMenuAtCursor(menu, event.clientX, event.clientY);
    } else {
      // Button click: position below the ⋮ button
      var btn = event.target;
      var rect = btn.getBoundingClientRect();
      var menuW = menu.offsetWidth || 180;
      var menuH = menu.offsetHeight || 200;
      var left = rect.left;
      var top = rect.bottom + 4;
      // Viewport boundary check
      if (left + menuW > window.innerWidth - 8) left = window.innerWidth - menuW - 8;
      if (top + menuH > window.innerHeight - 8) top = rect.top - menuH - 4;
      menu.style.left = Math.max(8, left) + 'px';
      menu.style.top = Math.max(8, top) + 'px';
    }
    menu.classList.add('active');
    event.stopPropagation();
  } catch (err) {
    console.error('[showEmpSettingsMenu] ERROR:', err);
  }
}
function hideEmpSettingsMenu() {
  var menu = document.getElementById('empSettingsMenu');
  if (menu) menu.classList.remove('active');
  empSettingsTarget = null;
}
// Move employee to group (from submenu)
function empSettingsActionMove(groupName) {
  var targetId = empSettingsTarget;
  if (!targetId) return;
  var emp = emps.find(function (e) {
    return e.id === targetId;
  });
  if (!emp) return;
  emp.subCategory = groupName;
  saveEmployees();
  renderEmployeeList();
  showToast('已移动到 ' + groupName);
  hideEmpSettingsMenu();
  if (typeof apiFetch === 'function') {
    apiFetch('/api/agents/' + targetId, {
      method: 'PUT',
      body: JSON.stringify({
        subCategory: groupName
      })
    }).catch(function () {});
  }
}
function empSettingsAction(action) {
  if (!empSettingsTarget) return;
  var emp = emps.find(function (e) {
    return e.id === empSettingsTarget;
  });
  if (!emp) return;
  switch (action) {
    case 'detail':
      // 「查看详情」= 打开详情面板 + 跳到基础 tab(与 edit 同行为,但语义上是只读入口)
      openEmpDetail(empSettingsTarget);
      setTimeout(function () {
        switchEmpDetailTab('basic');
      }, 100);
      break;
    case 'edit':
      openEmpDetail(empSettingsTarget);
      setTimeout(function () {
        switchEmpDetailTab('basic');
      }, 100);
      break;
    case 'pin':
      emp.pinned = !emp.pinned;
      showToast(emp.pinned ? '📌 已置顶' : '📌 已取消置顶');
      saveEmployees();
      syncEmpToServer(emp);
      break;
    case 'move':
      showMoveGroupModal(empSettingsTarget);
      break;
    case 'archive':
      archiveEmp(emp);
      saveEmployees();
      syncEmpToServer(emp);
      showToast('📦 已归档');
      break;
    case 'badge-urgent':
      emp.badge = {
        level: 'urgent',
        number: 1
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🔴 已标记为紧急');
      break;
    case 'badge-important':
      emp.badge = {
        level: 'important',
        number: null
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🟠 已标记为重要');
      break;
    case 'badge-normal':
      emp.badge = {
        level: 'normal',
        number: null
      };
      saveEmployees();
      renderEmployeeList();
      showToast('🔵 已标记为普通');
      break;
    case 'badge-clear':
      emp.badge = null;
      saveEmployees();
      renderEmployeeList();
      showToast('✕ 已清除紧急标记');
      break;
    case 'delete':
      if (confirm('确定删除 ' + emp.name + '？')) {
        var deleteId = empSettingsTarget;
        var _wasCurrent3 = localStorage.getItem('sb_current_emp') === deleteId;
        var _delEmp3 = emps.find(function (e) { return e.id === deleteId; });
        // ★ 优化删除 UX：保留原始状态用于失败回滚（避免"假删除"误导）
        var _delOrig3 = _delEmp3 ? {
          status: _delEmp3.status,
          archived: _delEmp3.archived,
          archivedAt: _delEmp3.archivedAt
        } : null;
        if (_delEmp3) {
          _delEmp3.status = 'archived';
          _delEmp3.archived = true;
          _delEmp3.archivedAt = new Date().toISOString();
        }
        // 同步删除后端
        if (typeof apiFetch === 'function') {
          apiFetch('/api/agents/' + deleteId, {
            method: 'DELETE'
          }).then(function () {
            showToast('🗑️ 已删除');
            if (typeof loadEmployees === 'function') loadEmployees();
          }).catch(function (e) {
            // 失败：回滚乐观更新，提示真实错误（不再让用户看到虚假的"已删除"）
            if (_delEmp3 && _delOrig3) {
              _delEmp3.status = _delOrig3.status;
              _delEmp3.archived = _delOrig3.archived;
              if (_delOrig3.archivedAt) {
                _delEmp3.archivedAt = _delOrig3.archivedAt;
              } else {
                delete _delEmp3.archivedAt;
              }
            }
            renderEmployeeList();
            showToast('❌ 删除失败：' + (e && e.message ? e.message : '未知错误'));
            console.error('[deleteAgent] 后端删除失败:', e);
          });
        } else {
          showToast('❌ apiFetch 未定义，无法删除');
        }
        if (currentEmpId === deleteId) closeEmpDetail();
        if (_wasCurrent3) {
          openNextEmployeeOrEmpty();
        } else {
          renderMsgs('private');
        }
      }
      break;
  }
  renderEmployeeList();
  hideEmpSettingsMenu();
}
// Close settings menu on click outside
document.addEventListener('click', function (e) {
  if (!e.target.closest('.emp-settings-menu') && !e.target.closest('.emp-more-btn')) {
    hideEmpSettingsMenu();
  }
  // Also close right-click context menus
  if (!e.target.closest('.ctx-menu')) {
    document.querySelectorAll('.ctx-menu.active').forEach(function (m) {
      m.classList.remove('active');
    });
    msgCtxTarget = null;
    groupCtxTarget = null;
  }
});

// ===== Right-click Context Menu System =====

// Position a menu at cursor with viewport boundary check
function positionMenuAtCursor(menu, cx, cy) {
  // Temporarily show to measure
  menu.style.visibility = 'hidden';
  menu.style.display = 'block';
  var menuW = menu.offsetWidth;
  var menuH = menu.offsetHeight;
  menu.style.display = '';
  menu.style.visibility = '';
  var left = cx + 2;
  var top = cy + 2;
  // Check right edge
  if (left + menuW > window.innerWidth - 8) left = cx - menuW - 2;
  // Check bottom edge
  if (top + menuH > window.innerHeight - 8) top = cy - menuH - 2;
  menu.style.left = Math.max(8, left) + 'px';
  menu.style.top = Math.max(8, top) + 'px';
}

// Close all context menus
function closeAllCtxMenus() {
  document.querySelectorAll('.ctx-menu.active').forEach(function (m) {
    m.classList.remove('active');
  });
  var oldCtxMenu = document.getElementById('contextMenu');
  if (oldCtxMenu) oldCtxMenu.classList.remove('active');
  hideEmpSettingsMenu();
  msgCtxTarget = null;
  groupCtxTarget = null;
}

// ===== Employee Card Right-click (uses empSettingsMenu) =====
document.addEventListener('DOMContentLoaded', function () {
  // Use event delegation on employeeList for right-click
  var empList = document.getElementById('employeeList');
  if (empList) {
    empList.addEventListener('contextmenu', function (e) {
      var item = e.target.closest('.list-item[data-id]');
      if (!item) return;
      // Skip if it's a project item
      if (item.dataset.proj) return;
      e.preventDefault();
      closeAllCtxMenus();
      var empId = item.dataset.id;
      showEmpSettingsMenu(e, empId);
    });
  }
});

// ===== Chat Message Right-click =====
var msgCtxTarget = null; // { element, isAI }

document.addEventListener('DOMContentLoaded', function () {
  var msgArea = document.getElementById('messagesArea');
  if (msgArea) {
    msgArea.addEventListener('contextmenu', function (e) {
      var msgEl = e.target.closest('.msg');
      if (!msgEl) return;
      // Don't show menu on typing indicator
      if (msgEl.classList.contains('typing-msg')) return;
      e.preventDefault();
      closeAllCtxMenus();
      var isAI = !msgEl.classList.contains('own');
      msgCtxTarget = {
        element: msgEl,
        isAI: isAI
      };
      var menu = document.getElementById('msgCtxMenu');
      // Show/hide regenerate option (only for AI messages)
      var regenItem = document.getElementById('msgCtxRegenerate');
      if (regenItem) regenItem.style.display = isAI ? 'flex' : 'none';
      positionMenuAtCursor(menu, e.clientX, e.clientY);
      menu.classList.add('active');
    });
  }
});

// ===== 消息引用（quote）=====
window._pendingQuote = null;   // 待发送的引用消息 {id, role, content, images}
window._msgQuotePool = {};     // 渲染时缓存的消息数据 id -> message，供右键引用取数

// 统一注册消息：回填 DOM 的 data-msg-id 并把消息数据写入引用缓存池（el 可为 null，仅写缓存）
function _registerMsgDom(el, msgObj) {
  if (!msgObj || !msgObj.id) return;
  if (el) el.dataset.msgId = msgObj.id;
  window._msgQuotePool[msgObj.id] = msgObj;
}

function buildQuoteBlockHtml(rm) {
  // 气泡顶部的引用块：左侧蓝色竖线 + 灰字摘要/缩略图，点击滚动定位原消息
  var thumbHtml = '';
  var imgs = rm.images || [];
  if (imgs.length > 0) {
    var src = imgs[0].base64 || imgs[0];
    thumbHtml = '<img class="msg-quote-thumb" src="' + src + '">';
  }
  var text = (rm.content || '').replace(/\s+/g, ' ').trim();
  if (text.length > 50) text = text.slice(0, 50) + '…';
  if (!text && imgs.length > 0) text = '[图片' + (imgs.length > 1 ? ' x' + imgs.length : '') + ']';
  var who = rm.role === 'user' ? '你' : 'AI';
  return '<div class="msg-quote-block" onclick="event.stopPropagation();scrollToQuotedMsg(\'' + escapeAttr(rm.id || '') + '\')">'
    + thumbHtml
    + '<span class="msg-quote-text">' + escapeHtml(who + '：' + text) + '</span></div>';
}

function scrollToQuotedMsg(msgId) {
  if (!msgId) return;
  var target = document.querySelector('#messagesArea .msg[data-msg-id="' + msgId + '"]');
  if (!target) {
    showToast('⚠️ 原消息不在当前聊天记录中');
    return;
  }
  target.scrollIntoView({ behavior: 'smooth', block: 'center' });
  target.classList.remove('quote-flash');
  void target.offsetWidth; // 重触发动画
  target.classList.add('quote-flash');
}

function setQuotePreview(qmsg) {
  window._pendingQuote = {
    id: qmsg.id,
    role: qmsg.role,
    content: qmsg.content || '',
    images: qmsg.images || [],
  };
  var bar = document.getElementById('quotePreviewBar');
  if (!bar) return;
  var inner = '';
  var imgs = window._pendingQuote.images;
  if (imgs.length > 0) {
    var src = imgs[0].base64 || imgs[0];
    inner += '<span class="quote-preview-thumb-wrap"><img class="quote-preview-thumb" src="' + src + '">'
      + (imgs.length > 1 ? '<span class="quote-preview-badge">+' + (imgs.length - 1) + '张</span>' : '') + '</span>';
  }
  var text = window._pendingQuote.content.replace(/\s+/g, ' ').trim();
  if (text.length > 50) text = text.slice(0, 50) + '…';
  if (!text && imgs.length > 0) text = '[图片]';
  var who = window._pendingQuote.role === 'user' ? '你' : 'AI';
  inner += '<span class="quote-preview-text">引用 ' + escapeHtml(who + '：' + text) + '</span>';
  inner += '<span class="quote-preview-close" onclick="clearQuotePreview()">×</span>';
  bar.innerHTML = inner;
  bar.style.display = 'flex';
  var input = document.getElementById('msgInput');
  if (input) input.focus();
}

function clearQuotePreview() {
  window._pendingQuote = null;
  var bar = document.getElementById('quotePreviewBar');
  if (bar) {
    bar.innerHTML = '';
    bar.style.display = 'none';
  }
}

// Message context menu actions
function msgCtxAction(action) {
  if (!msgCtxTarget || !msgCtxTarget.element) {
    closeAllCtxMenus();
    return;
  }
  var el = msgCtxTarget.element;
  var bubble = el.querySelector('.msg-bubble');
  switch (action) {
    case 'quote': {
      var qid = el.dataset.msgId || '';
      var qmsg = (window._msgQuotePool || {})[qid];
      if (!qmsg) {
        showToast('⚠️ 无法引用：未找到消息数据');
        break;
      }
      setQuotePreview(qmsg);
      break;
    }
    case 'copy':
      if (bubble) {
        var text = bubble.textContent || bubble.innerText || '';
        // Remove "..." from truncated typing messages
        text = text.replace(/\s*\.\.\.\s*$/, '');
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(function () {
            showToast('📋 已复制到剪贴板');
          }).catch(function () {
            fallbackCopyText(text);
          });
        } else {
          fallbackCopyText(text);
        }
      }
      break;
    case 'delete':
      el.style.transition = 'opacity 0.2s, transform 0.2s';
      el.style.opacity = '0';
      el.style.transform = 'translateX(' + (msgCtxTarget.isAI ? '-20px' : '20px') + ')';
      setTimeout(function () {
        el.remove();
      }, 200);
      showToast('🗑️ 消息已删除');
      break;
    case 'regenerate':
      if (msgCtxTarget.isAI) {
        // Get the AI message text and remove it, then resend
        var emp = getCurrentEmployeeInfo();
        // Find the previous user message for re-sending
        var prevMsgs = el.parentElement.querySelectorAll('.msg');
        var userIdx = -1;
        for (var i = 0; i < prevMsgs.length; i++) {
          if (prevMsgs[i] === el) {
            userIdx = i;
            break;
          }
        }
        var userText = '';
        // Look for the last .own message before this AI message
        for (var j = userIdx - 1; j >= 0; j--) {
          if (prevMsgs[j].classList.contains('own')) {
            var ub = prevMsgs[j].querySelector('.msg-bubble');
            if (ub) userText = ub.textContent || ub.innerText || '';
            break;
          }
        }
        // Remove the AI message with animation
        el.style.transition = 'opacity 0.2s, transform 0.2s';
        el.style.opacity = '0';
        el.style.transform = 'translateX(-20px)';
        setTimeout(function () {
          el.remove();
          if (userText && typeof sendMsg === 'function') {
            // Set input and trigger send
            var input = document.getElementById('msgInput');
            if (input) {
              input.value = userText;
              sendMsg();
            }
          } else {
            showToast('⚠️ 无法重新生成：未找到对应的消息');
          }
        }, 200);
      }
      break;
  }
  closeAllCtxMenus();
}

// Fallback copy for older browsers
function fallbackCopyText(text) {
  var ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand('copy');
    showToast('📋 已复制到剪贴板');
  } catch (e) {
    showToast('❌ 复制失败');
  }
  document.body.removeChild(ta);
}

// ===== Group Item Right-click =====
var groupCtxTarget = null;
document.addEventListener('DOMContentLoaded', function () {
  var empList = document.getElementById('employeeList');
  if (empList) {
    empList.addEventListener('contextmenu', function (e) {
      var item = e.target.closest('.list-item[data-group]');
      if (!item) return;
      e.preventDefault();
      closeAllCtxMenus();
      var groupId = item.dataset.group;
      groupCtxTarget = groupId;
      var menu = document.getElementById('groupCtxMenu');
      positionMenuAtCursor(menu, e.clientX, e.clientY);
      menu.classList.add('active');
    });
  }
});

// Group context menu actions
function groupCtxAction(action) {
  if (!groupCtxTarget) {
    closeAllCtxMenus();
    return;
  }
  var groupId = groupCtxTarget;
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  switch (action) {
    case 'edit':
      // Open group wizard in edit mode - repurpose the wizard
      if (group) {
        openGroupWizard();
        // Pre-fill the wizard with existing group data
        setTimeout(function () {
          var nameInput = document.getElementById('gwName');
          if (nameInput) nameInput.value = group.name || '';
          gwEmoji = group.emoji || '🚀';
          // Update emoji selection
          var emojiGrid = document.getElementById('gwEmojiGrid');
          if (emojiGrid) {
            emojiGrid.querySelectorAll('.emoji-opt').forEach(function (opt) {
              opt.classList.toggle('selected', opt.textContent === gwEmoji);
            });
          }
          // Set members（统一转换为字符串 ID，避免和 wizard 里的字符串 empId 混用导致重复/删不掉）
          gwMembers = (group.members || []).map(function (m) {
            return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
          }).filter(function (id) { return id; });
          gwLead = group.leadAgentId || '';
          gwEditGroupId = groupId; // Mark as editing
          if (gwStep < 2) {
            gwStep = 2;
            renderGroupWizardUI();
          }
        }, 100);
        showToast('✏️ 编辑群组: ' + (group.name || ''));
      }
      break;
    case 'settings':
      if (group) {
        openGroupDetail(groupId);
        setTimeout(function () {
          switchGroupDetailTab('settings');
        }, 100);
      }
      break;
    case 'addMember':
      if (group) {
        openGroupWizard();
        setTimeout(function () {
          var nameInput = document.getElementById('gwName');
          if (nameInput) nameInput.value = group.name || '';
          gwEmoji = group.emoji || '🚀';
          gwMembers = (group.members || []).map(function (m) {
            return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
          }).filter(function (id) { return id; });
          gwLead = group.leadAgentId || '';
          gwEditGroupId = groupId;
          gwStep = 2; // Jump to member selection step
          renderGroupWizardUI();
        }, 100);
      }
      break;
    case 'dissolve':
      if (group && confirm('确定解散群组「' + group.name + '」？此操作不可撤销。')) {
        for (var i = groups.length - 1; i >= 0; i--) {
          if (groups[i].id === groupId) groups.splice(i, 1);
        }
        saveGroups();
        // If currently viewing this group, reset chat
        if (currentGroupId === groupId) {
          currentGroupId = null;
          var area = document.getElementById('messagesArea');
          if (area) area.innerHTML = '';
        }
        renderEmployeeList();
        showToast('🗑️ 群组已解散');
      }
      break;
  }
  closeAllCtxMenus();
}

// Close context menus on scroll (prevent menu from floating away)
document.addEventListener('scroll', function (e) {
  document.querySelectorAll('.ctx-menu.active').forEach(function (m) {
    m.classList.remove('active');
  });
}, true);

// Close context menus on Escape
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    closeAllCtxMenus();
  }
});

// 恢复归档的员工
function restoreArchivedEmp(empId) {
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) return;
  unarchiveEmp(emp);
  saveEmployees();
  if (typeof syncEmpToServer === 'function') {
    syncEmpToServer(emp).catch(function (e) {});
  }
  renderEmployeeList();
  showToast('📂 已恢复 ' + emp.name);
}

// 下载归档对话记录为JSON
async function downloadArchive(empId) {
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) {
    showToast('员工不存在');
    return;
  }
  var history = [];
  try {
    var resp = await apiFetch('/api/chat/' + encodeURIComponent(empId));
    var data = await resp.json();
    history = (data && data.messages) || [];
  } catch (e) {
    history = [];
  }
  var exportData = {
    employee: {
      id: emp.id,
      name: emp.name,
      role: emp.role,
      avatar: emp.avatar,
      bg: emp.bg,
      soulDoc: emp.soulDoc || emp.systemPrompt || ''
    },
    chatHistory: history,
    exportTime: new Date().toISOString(),
    version: '1.0'
  };
  var json = JSON.stringify(exportData, null, 2);
  var blob = new Blob([json], {
    type: 'application/json'
  });
  var url = URL.createObjectURL(blob);
  var a = document.createElement('a');
  a.href = url;
  a.download = (emp.name || 'archive') + '_对话记录_' + new Date().toLocaleDateString('zh-CN').replace(/\//g, '-') + '.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  showToast('📥 已下载 ' + emp.name + ' 的对话记录');
}

// 继续归档对话（恢复员工 + 打开聊天）
function continueArchive(empId) {
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) {
    showToast('员工不存在');
    return;
  }

  // 先恢复员工
  unarchiveEmp(emp);
  saveEmployees();
  if (typeof syncEmpToServer === 'function') {
    syncEmpToServer(emp).catch(function (e) {});
  }
  renderEmployeeList();

  // 打开聊天（聊天记录还在 localStorage 里，会自动加载）
  setTimeout(function () {
    openChat(empId);
    showToast('💬 已恢复 ' + emp.name + '，继续对话');
  }, 200);
}

// Close context menu on click outside
document.addEventListener('click', hideContextMenu);

// ============ Move Group Modal ============
var moveGroupTarget = null;
function getTeamNameList() {
  var names = [];
  var seen = {};
  allTeams.forEach(function (t) {
    if (t.name && !seen[t.name]) {
      seen[t.name] = true;
      names.push(t.name);
    }
  });
  return names.length > 0 ? names : ['未分组'];
}
function showMoveGroupModal(empId) {
  moveGroupTarget = empId;
  var emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) return;
  document.getElementById('moveGroupEmpName').textContent = emp.name;
  var list = document.getElementById('moveGroupList');
  var html = '';
  var options = getTeamNameList();
  for (var i = 0; i < options.length; i++) {
    var group = options[i];
    var isCurrent = emp.subCategory === group;
    var currentBadge = isCurrent ? ' <span style="font-size:11px;color:var(--accent);margin-left:auto;">当前</span>' : '';
    var selectedClass = isCurrent ? ' style="border-color:var(--accent);background:var(--accent-light);"' : '';
    html += '<div onclick="doMoveGroup(\'' + escapeAttr(group) + '\')" class="group-option"' + selectedClass + '>' + '<span>' + escapeHtml(group) + '</span>' + currentBadge + '</div>';
  }
  list.innerHTML = html;
  document.getElementById('moveGroupModal').style.display = 'flex';
}
function closeMoveGroupModal() {
  document.getElementById('moveGroupModal').style.display = 'none';
  moveGroupTarget = null;
}
function doMoveGroup(newGroup) {
  if (!moveGroupTarget) return;
  var emp = emps.find(function (e) {
    return e.id === moveGroupTarget;
  });
  if (!emp) return;
  var oldGroup = emp.subCategory || '未分组';
  emp.subCategory = newGroup;
  saveEmployees();
  renderEmployeeList();
  closeMoveGroupModal();
  showToast('📁 ' + emp.name + ' 已从「' + oldGroup + '」移到「' + newGroup + '」');
}

// ============ Connection Status Bar ============
function updateConnectionStatus() {
  var statusEl = document.getElementById('connStatusText');

  // Check OpenClaw connection（使用 openclaw.connected + authenticated，而非 readyState）
  var isOpenClawConnected = false;
  if (typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated) {
    isOpenClawConnected = true;
  }
  var isConnecting = typeof openclaw !== 'undefined' && openclaw.connected && !openclaw.authenticated;

  // ★ fix/font-and-connection: mock 模式单独识别。openclaw-client.js 在 WS 连不上或
  //   challenge 超时会调 _enableMockMode() → mockMode=true,connected/authenticated=false。
  //   之前这条路径会落到 "⚠ 服务未连接",用户无法区分 "WS 真没连上 (mock 兜底)" vs
  //   "WS 真断了 (没兜底)" —— 两种情况底层原因不同,提示应该不同。
  var isMockMode = typeof openclaw !== 'undefined' && openclaw.mockMode === true;

  // Check API configuration
  var hasAPIKey = false;
  var aiConfig = localStorage.getItem('sb_ai_config');
  if (aiConfig) {
    try {
      var config = JSON.parse(aiConfig);
      hasAPIKey = config.apiKey && config.apiKey.length > 10;
    } catch (e) {}
  }

  // 底部状态文案：去掉"OpenClaw"技术字眼，按连接态给最小提示
  // 优先级: 已连接 > 连接中 > API 模式 > Mock 模式 > 未连接
  var latency = Math.floor(Math.random() * 50) + 20;
  var statusText;
  if (isOpenClawConnected) statusText = '✓ 服务正常 · ' + latency + 'ms';
  else if (isConnecting) statusText = '⟳ 服务连接中…';
  else if (hasAPIKey) statusText = '✓ API 模式 · ' + latency + 'ms';
  else if (isMockMode) statusText = '⚠ Mock 模式（WS 不可达）';
  else statusText = '⚠ 服务未连接';
  if (statusEl) statusEl.textContent = statusText;

  // 〔r39-16 批注②〕底部状态栏平时隐藏, 仅异常态显示 (老大: 这个是什么)
  // 原版无此常驻条; 已连接/API 模式 (健康态) 隐藏, Mock/未连接/连接中显示
  // 顶栏 chatTopbarWs 同步逻辑 (下方) 保留不动 — 功能不丢, 入口不无中生有
  var connBar = document.getElementById('connectionStatusBar');
  if (connBar) connBar.style.display = (isOpenClawConnected || hasAPIKey) ? 'none' : '';

  // ★ ui/chat-main-v21-polish: 同步顶部状态条 WS + 模型
  var topbarWs = document.getElementById('chatTopbarWs');
  var topbarWsText = document.getElementById('chatTopbarWsText');
  var topbarWsMs = document.getElementById('chatTopbarWsMs');
  if (topbarWs && topbarWsText) {
    if (isOpenClawConnected) {
      topbarWs.classList.remove('disconnected');
      topbarWs.classList.add('connected');
      topbarWsText.textContent = '服务正常';
      if (topbarWsMs) topbarWsMs.textContent = '· ' + latency + 'ms';
    } else if (isConnecting) {
      topbarWs.classList.remove('disconnected');
      topbarWs.classList.add('connected');
      topbarWsText.textContent = '连接中';
      if (topbarWsMs) topbarWsMs.textContent = '';
    } else if (hasAPIKey) {
      topbarWs.classList.remove('disconnected');
      topbarWs.classList.add('connected');
      topbarWsText.textContent = 'API 模式';
      if (topbarWsMs) topbarWsMs.textContent = '· ' + latency + 'ms';
    } else if (isMockMode) {
      topbarWs.classList.add('disconnected');
      topbarWs.classList.remove('connected');
      topbarWsText.textContent = 'Mock';
      if (topbarWsMs) topbarWsMs.textContent = '';
    } else {
      topbarWs.classList.add('disconnected');
      topbarWs.classList.remove('connected');
      topbarWsText.textContent = '未连接';
      if (topbarWsMs) topbarWsMs.textContent = '';
    }
  }
  // 模型名: 从 sb_ai_config 读 primary model, 兜底 'MiniMax'
  var topbarModelName = document.getElementById('chatTopbarModelName');
  if (topbarModelName) {
    var modelName = 'MiniMax';
    try {
      var cfg = JSON.parse(localStorage.getItem('sb_ai_config') || '{}');
      if (cfg.mode === 'kimi' && cfg.model) modelName = 'Kimi ' + cfg.model.replace(/^kimi\//, '');
      else if (cfg.mode === 'zhipu' && cfg.model) modelName = 'GLM ' + cfg.model.replace(/^zhipu\//, '');
      else if (cfg.mode === 'openai' && cfg.model) modelName = cfg.model.replace(/^openai\//, '');
      else if (cfg.model) modelName = String(cfg.model).split('/').pop();
    } catch (e) { /* 兜底用 MiniMax */ }
    topbarModelName.textContent = modelName;
  }

  // Update sidebar bottom status to avoid contradiction with connection status bar
  // 配色: 已连接=绿, 连接中=蓝(脉冲), API=橙, Mock=灰, 未连接=红
  var sidebarStatusText = document.getElementById('sidebarStatusText');
  var sidebarStatusDot = document.getElementById('sidebarStatusDot');
  if (sidebarStatusText && sidebarStatusDot) {
    if (isOpenClawConnected) {
      sidebarStatusText.textContent = '已连接';
      sidebarStatusDot.style.background = '#34C759';
    } else if (isConnecting) {
      // ★ fix/font-and-connection: WS 已建但认证未完成时,侧边栏之前误显"未连接"红色
      // → 改为蓝色"连接中",和底部状态条对齐,避免底部"⟳ 连接中"和侧边栏"未连接"互相打脸
      sidebarStatusText.textContent = '连接中';
      sidebarStatusDot.style.background = '#1677ff';
    } else if (hasAPIKey) {
      sidebarStatusText.textContent = 'API 模式';
      sidebarStatusDot.style.background = '#FF9500';
    } else if (isMockMode) {
      sidebarStatusText.textContent = 'Mock 模式';
      sidebarStatusDot.style.background = '#8E8E93';
    } else {
      sidebarStatusText.textContent = '未连接';
      sidebarStatusDot.style.background = '#FF3B30';
    }
  }
}
function showConnectionDetails() {
  var details = [];

  // OpenClaw details（使用 connected + authenticated 判断，而非 readyState）
  if (typeof openclaw !== 'undefined') {
    var ocStatus = '❌ 未连接';
    if (openclaw.connected && openclaw.authenticated) ocStatus = '✅ 已连接';
    else if (openclaw.connected && !openclaw.authenticated) ocStatus = '⏳ 连接中（未认证）';
    details.push('OpenClaw 状态: ' + ocStatus);
    details.push('WebSocket URL: ' + (openclaw.url || '未配置'));
  } else {
    details.push('OpenClaw: 未初始化');
  }

  // API details
  var aiConfig = localStorage.getItem('sb_ai_config');
  if (aiConfig) {
    try {
      var config = JSON.parse(aiConfig);
      details.push('AI 模式: ' + (config.mode || 'copaw'));
      details.push('API Key: ' + (config.apiKey ? '✅ 已配置 (' + config.apiKey.substring(0, 8) + '...)' : '❌ 未配置'));
    } catch (e) {
      details.push('API 配置: 解析错误');
    }
  } else {
    details.push('API 配置: 未设置');
  }
  showToast('连接详情:\n\n' + details.join('\n'), 'info');
}

// ========== 统一定时器调度器 ==========
// 页面级常驻定时任务统一注册到这里：基础 1 秒节拍，按 interval(秒) 取模触发
var TIMER_TASKS = [];
var _globalTimerTick = 0;
function registerTimerTask(fn, intervalSec) {
  // 同一函数重复注册时忽略，避免 init 函数重复执行导致任务叠加
  for (var i = 0; i < TIMER_TASKS.length; i++) {
    if (TIMER_TASKS[i].fn === fn) return;
  }
  TIMER_TASKS.push({ fn: fn, interval: intervalSec, tick: 0 });
}
setInterval(function () {
  _globalTimerTick++;
  TIMER_TASKS.forEach(function (task) {
    if (_globalTimerTick % task.interval !== 0) return;
    task.tick++;
    try {
      task.fn();
    } catch (e) {
      console.error('[Timer] 定时任务执行失败:', e);
    }
  });
}, 1000);

// Update connection status every 5 seconds
registerTimerTask(updateConnectionStatus, 5);
// Initial update
document.addEventListener('DOMContentLoaded', updateConnectionStatus);

// Open Chat
function openChat(id) {
  currentGroupId = null; // 退出群聊模式
  document.querySelectorAll('.list-item').forEach(function (i) {
    return i.classList.remove('active');
  });
  // 公共清理：从群聊模式切换出来时，隐藏群聊专属UI
  var _announcement = document.getElementById('groupAnnouncement');
  if (_announcement) _announcement.style.display = 'none';
  closeChatMoreMenu();
  const emp = emps.find(function (e) {
    return e.id === id;
  });
  // 员工分支：排除 isProject=true 的项目类型（它们走项目分支）
  if (emp && !emp.isProject) {
    localStorage.setItem('sb_current_emp', id);
    localStorage.removeItem('sb_current_proj'); // 清除项目状态，避免督促等功能误判
    var chatHeaderName = document.querySelector('.chat-header-name');
    var chatHeaderSub = document.querySelector('.chat-header-role') || document.querySelector('.chat-header-sub');
    var chatHeaderAvatar = document.querySelector('.chat-header-v2 .avatar') || document.querySelector('.chat-header .avatar');
    if (chatHeaderName) chatHeaderName.textContent = emp.name;
    if (chatHeaderSub) chatHeaderSub.textContent = '在线 · ' + getEmpRoleDisplay(emp);
    // 拉取当前员工积分余额 (dev/feat: #6)
    loadChatCredits(id);
    if (chatHeaderAvatar) {
      chatHeaderAvatar.style.display = '';
      chatHeaderAvatar.innerHTML = renderAvatar(emp, 36);
      var _isImg = typeof emp.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[emp.avatar] || emp.avatar && (emp.avatar.indexOf('data:image') === 0 || emp.avatar.indexOf('.png') > 0 || emp.avatar.indexOf('.jpg') > 0 || emp.avatar.indexOf('.jpeg') > 0);
      chatHeaderAvatar.style.background = _isImg ? 'transparent' : 'linear-gradient(135deg,' + emp.bg + ',' + emp.bg + 'dd)';
    }
    var item = document.querySelector('.list-item[data-id="' + id + '"]');
    if (item) item.classList.add('active');
    var chatProgress = document.getElementById('chatProgress');
    if (chatProgress) chatProgress.style.display = 'none';
    // 隐藏群公告
    var announcementEl = document.getElementById('groupAnnouncement');
    if (announcementEl) announcementEl.style.display = 'none';
    var appMain = document.querySelector('.app-main');
    if (appMain) appMain.classList.remove('show-board');
    // 切换员工前先强制清空消息区，避免旧消息残留导致串聊
    document.getElementById('messagesArea').innerHTML = '';
    renderMsgs('private');
    hideBulletin();
    // 员工私聊显示右上角更多菜单（隐藏群聊专属的归档）
    var _headerRight = document.getElementById('chatHeaderRight');
    if (_headerRight) _headerRight.style.display = 'flex';
    var _archiveItem = document.getElementById('menuArchiveItem');
    if (_archiveItem) _archiveItem.style.display = 'none';
  }
  // 项目分支：支持 projs 数组和 emps 中 isProject=true 的项目类型
  const proj = projs.find(function (p) {
    return p.id === id;
  });
  const projEmp = !proj ? emps.find(function (e) {
    return e.id === id && e.isProject;
  }) : null;
  if (proj || projEmp) {
    var target = proj || projEmp;
    // 重置个人聊天状态并清空消息区，避免个人/项目串混
    localStorage.removeItem('sb_current_emp');
    localStorage.setItem('sb_current_proj', id); // 设置当前项目，确保督促等功能获取正确的项目ID
    document.getElementById('messagesArea').innerHTML = '';
    var chatHeaderName2 = document.querySelector('.chat-header-name');
    var chatHeaderSub2 = document.querySelector('.chat-header-sub');
    if (chatHeaderName2) chatHeaderName2.innerHTML = '# ' + escapeHtml((target.name || '') + '') + ' <span class="owner-badge">👑 群主</span>';
    if (chatHeaderSub2) chatHeaderSub2.textContent = '项目成员';
    var projItem = document.querySelector('.list-item[data-id="' + id + '"]') || document.querySelector('.list-item[data-proj="' + id + '"]');
    if (projItem) projItem.classList.add('active');
    var chatProgress2 = document.getElementById('chatProgress');
    if (chatProgress2) {
      chatProgress2.style.display = 'flex';
      document.getElementById('progressFill').style.width = (target.progress || 0) + '%';
      document.getElementById('progressText').textContent = (target.progress || 0) + '%';
    }
    renderMsgs('project');
    var appMain2 = document.querySelector('.app-main');
    if (appMain2) appMain2.classList.add('show-board');
    showBulletinBar(id);
    startSupervisorTimer(); // 启动督促定时器
    updateSupervisorStatusButton();
    // 项目分支不显示聊天头部右侧操作菜单
    var _headerRight2 = document.getElementById('chatHeaderRight');
    if (_headerRight2) _headerRight2.style.display = 'none';
  }
}

// ========== 群聊功能 ==========
function openGroupChat(groupId) {
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group) {
    showToast('⚠️ 群组不存在');
    return;
  }
  currentGroupId = groupId;

  // 更新侧栏高亮
  document.querySelectorAll('.list-item').forEach(function (i) {
    i.classList.remove('active');
  });
  var groupItem = document.querySelector('.list-item[data-group="' + groupId + '"]');
  if (groupItem) groupItem.classList.add('active');

  // 更新聊天头部 — 群组模式
  var chatHeaderName = document.querySelector('.chat-header-name');
  var chatHeaderSub = document.querySelector('.chat-header-role') || document.querySelector('.chat-header-sub');
  var chatHeaderLeft = document.querySelector('.chat-header-left');

  // 构建群组头部
  var memberAvatarsHtml = '';
  var members = group.members || [];
  var showCount = Math.min(members.length, 4);
  for (var i = 0; i < showCount; i++) {
    // 兼容 members 的两种格式：字符串 和 字典
    var mid = (typeof members[i] === 'object' && members[i] !== null) ? (members[i].id || members[i]) : members[i];
    var m = emps.find(function (e) {
      return e.id === mid;
    });
    if (m) {
      memberAvatarsHtml += '<div class="mini-avatar" style="background:' + escapeAttr(m.bg || '#8E8E93') + ';">' + renderAvatar(m, 20) + '</div>';
    }
  }
  if (members.length > 4) {
    memberAvatarsHtml += '<div class="mini-avatar mini-more">+' + (members.length - 4) + '</div>';
  }
  if (chatHeaderLeft) {
    var emoji = group.emoji || '👥';
    // 显示完整成员数（不按用户过滤）
    var memberCount = (group.members || []).length;
    chatHeaderLeft.innerHTML = '<div class="group-avatar" style="background:linear-gradient(135deg,' + escapeAttr(group.bg || '#5856D6') + ',' + (group.bg || '#5856D6') + 'dd);width:36px;height:36px;border-radius:10px;font-size:18px;">' + escapeHtml(emoji) + '</div>' + '<div class="chat-header-info">' + '<div class="chat-header-name" style="display:flex;align-items:center;gap:6px;">' + '<span>' + escapeHtml(group.name) + '</span>' + '<div class="group-header-avatars">' + memberAvatarsHtml + '</div>' + '</div>' + '<div class="chat-header-role">' + memberCount + ' 名成员 · 群聊</div>' + '</div>';
  }

  // 隐藏进度条
  var chatProgress = document.getElementById('chatProgress');
  if (chatProgress) chatProgress.style.display = 'none';

  // 显示更多菜单（仅群聊模式），并在菜单中展示归档入口
  closeChatMoreMenu();
  var chatHeaderRight = document.getElementById('chatHeaderRight');
  if (chatHeaderRight) chatHeaderRight.style.display = 'flex';
  var _archiveItem = document.getElementById('menuArchiveItem');
  if (_archiveItem) _archiveItem.style.display = 'flex';

  // 显示群公告
  var announcementEl = document.getElementById('groupAnnouncement');
  var announcementContent = document.getElementById('announcementContent');
  if (announcementEl && announcementContent) {
    if (group.announcement && group.announcement.trim().length > 0) {
      announcementContent.textContent = group.announcement;
      announcementEl.style.display = 'flex';
    } else {
      announcementContent.textContent = '';
      announcementEl.style.display = 'flex';
    }
  }

  // 隐藏任务板
  var appMain = document.querySelector('.app-main');
  if (appMain) appMain.classList.remove('show-board');

  // 清空消息区，重置个人聊天状态和项目状态，避免个人/群聊/项目串混
  localStorage.removeItem('sb_current_emp');
  localStorage.removeItem('sb_current_proj');
  document.getElementById('messagesArea').innerHTML = '';

  // 渲染群聊消息
  renderGroupMessages(groupId);

  // 子账号进入项目组时，显示系统提示（不保存到历史，仅前端展示）
  if (currentUser && currentUser.role !== 'admin') {
    var joinHint = document.createElement('div');
    joinHint.className = 'msg system-msg';
    joinHint.style.cssText = 'text-align:center;padding:8px 0;color:var(--text-tertiary);font-size:12px;';
    var userName = currentUser.displayName || currentUser.name || currentUser.username || '子账号';
    joinHint.innerHTML = '<span style="background:var(--bg-secondary);padding:2px 8px;border-radius:10px;">' + escapeHtml(userName) + ' 加入了群聊</span>';
    var area = document.getElementById('messagesArea');
    var emptyState = area && area.querySelector('.chat-empty-state');
    if (emptyState) {
      area.innerHTML = '';
      area.appendChild(joinHint);
    } else {
      area.appendChild(joinHint);
    }
    area.scrollTop = area.scrollHeight;
  }

  // 隐藏公告栏
  hideBulletin();
}

// ★ ui/chat-topbar-v21-fix: 顶部状态条 6 个 AI chip 加 onclick → 切对话
// 防御式：先读 static map，运行时再尝试用 emps 数组覆盖（refresh 后 emp_id 不会变，名字可能改）
function _initChatTopbarChips() {
  var team = document.getElementById('chatTopbarTeam');
  if (!team || team.__chipsBound) return;
  team.__chipsBound = true;

  // data-agent alias → emp_id 静态映射（即使 emps 还没加载也能点）
  var STATIC_MAP = {
    'ray':       { id: 'emp_1779430403964',       name: 'Ray' },
    'shangguan': { id: 'emp_1779955656118',       name: '上官婉儿' },
    'kongming':  { id: 'emp_1780132768182',       name: '孔明' },
    'helen':     { id: 'emp_1780199176680',       name: 'Helen' },
    'mumu':      { id: 'emp_1787550091124_1048',  name: '木木' }
  };

  function _resolveEmpId(alias) {
    if (alias === 'main') {
      // main chip = 当前用户视图（无独立 emp）
      return null;
    }
    var m = STATIC_MAP[alias];
    if (!m) return null;
    // 优先用 emps 数组确认 emp_id 仍然存在
    try {
      if (typeof emps !== 'undefined' && Array.isArray(emps) && emps.length) {
        var hit = emps.find(function (e) {
          return e && e.id === m.id;
        });
        if (hit) return hit.id;
      }
    } catch (e) { /* 忽略 - fallback static */ }
    return m.id;
  }

  var chips = team.querySelectorAll('.chat-topbar-agent[data-agent]');
  chips.forEach(function (chip) {
    var alias = chip.getAttribute('data-agent');
    if (alias === 'main') {
      // main chip 不可点击（语义上是当前用户，没有独立 emp_id 对话）
      chip.style.cursor = 'default';
      chip.title = '当前用户视图（不可切换）';
      return;
    }
    var empId = _resolveEmpId(alias);
    if (!empId) {
      chip.style.cursor = 'not-allowed';
      chip.title = '员工映射缺失';
      return;
    }
    chip.setAttribute('data-emp-id', empId);
    chip.setAttribute('title', '点击切到 ' + (chip.querySelector('.chat-topbar-agent-name')?.textContent || alias));
    chip.addEventListener('click', function () {
      try {
        if (typeof openChat !== 'function') return;
        openChat(empId);
        _syncTopbarActive();
        var nm = chip.querySelector('.chat-topbar-agent-name');
        if (typeof showToast === 'function') {
          showToast('✦ 已切到 ' + (nm ? nm.textContent : alias));
        }
      } catch (e) {
        console.error('[topbar chip click] 切换失败 alias=' + alias + ' empId=' + empId, e);
        if (typeof showToast === 'function') showToast('❌ 切换失败：' + (e && e.message || '未知错误'));
      }
    });
  });

  function _syncTopbarActive() {
    var cur = '';
    try { cur = localStorage.getItem('sb_current_emp') || ''; } catch (e) {}
    chips.forEach(function (c) {
      var empId = c.getAttribute('data-emp-id');
      if (empId && cur === empId) c.classList.add('active');
      else c.classList.remove('active');
    });
  }
  team._syncTopbarActive = _syncTopbarActive;
  _syncTopbarActive();
  // 跨 tab 同步
  window.addEventListener('storage', _syncTopbarActive);
  // 同 tab 内 sb_current_emp 变化时也同步（每 1.5s 轮询，开销极低）
  setInterval(_syncTopbarActive, 1500);
}

// emps 数组是异步加载的，1s 后再 bind 一次以捕获新员工（不会重复 bind，__chipsBound 守卫）
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initChatTopbarChips);
} else {
  _initChatTopbarChips();
}
setTimeout(_initChatTopbarChips, 1500);
setTimeout(_initChatTopbarChips, 4000);

// 渲染群聊消息
function renderGroupMessages(groupId) {
  var area = document.getElementById('messagesArea');
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group) return;

  apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history')
    .then(function (r) {
      return r.json();
    })
    .then(function (data) {
      var backendHistory = (data && data.messages) || [];
      backendHistory.sort(function (a, b) {
        return (a.time || 0) - (b.time || 0);
      });
      _doRender(backendHistory);
    })
    .catch(function (err) {
      console.debug('[renderGroupMessages] 后端加载失败:', err);
      _doRender([]);
    });

  function _doRender(history) {
    if (history.length === 0) {
      area.innerHTML = getChatEmptyStateHtml();
      return;
    }
    var html = '<div class="time-separator"><span>今天</span></div>';
    history.forEach(function (m) {
      var timeStr = formatDate(m.time);
      var isOwn = m.role === 'user';
      var senderEmp = null;
      if (!isOwn && m.senderId) {
        senderEmp = findEmpByIdAcrossGroups(m.senderId);
      }

      // 处理 @提及高亮：AI/assistant 消息走 Markdown 渲染，用户消息保持纯文本 + @高亮
      var isAgent = m.role === 'assistant' || m.senderType === 'agent';
      var bubbleContent = isAgent ? highlightMentions(formatMessageContent(m.content)) : formatGroupMessage(m.content);
      if (isOwn) {
        html += '<div class="msg own"><div class="msg-content">' + '<div class="msg-sender"><span class="msg-sender-name">你</span><span class="msg-sender-time">' + timeStr + '</span></div>' + '<div class="msg-bubble">' + bubbleContent + '</div>' + '</div></div>';
      } else {
        var avatarHtml = senderEmp ? renderAvatar(senderEmp, 32) : '🤖';
        var senderName = senderEmp ? senderEmp.name : m.senderName || 'AI';
        var senderRole = senderEmp ? getEmpRoleDisplay(senderEmp) : '';
        var senderBg = senderEmp ? 'background:' + senderEmp.bg + ';' : 'background:var(--bg-tertiary);';
        html += '<div class="msg"><div class="msg-avatar"><div class="avatar small" style="' + senderBg + '">' + avatarHtml + '</div></div>' + '<div class="msg-content">' + '<div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(senderName) + '</span><span class="msg-sender-role">' + escapeHtml(senderRole) + '</span><span class="msg-sender-time">' + timeStr + '</span></div>' + '<div class="msg-bubble">' + bubbleContent + '</div>' + '</div></div>';
      }
    });
    html += '<div class="typing-indicator" id="typingIndicator" style="display:none;"><div class="typing-dots"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>';
    area.innerHTML = html;
    area.scrollTop = area.scrollHeight;
  }
}

// 格式化群聊消息（处理 @提及高亮）
function formatGroupMessage(text) {
  if (!text) return '';
  var result = escapeHtml(text);
  // 高亮 @提及（名字中不能包含另一 @、空格、中英文标点及括号）
  result = result.replace(/@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]+)/g, '<span class="mention-tag">@$1</span>');
  // 处理换行
  result = result.replace(/\n/g, '<br>');
  return result;
}

// 在已有 HTML 中安全地高亮 @提及（避免破坏标签属性）
function highlightMentions(html) {
  if (!html) return '';
  // 在已有 HTML 中高亮 @提及，避免破坏标签属性，同时排除中英文标点及括号
  return html.replace(/@([^\s<>,，.。！？;；:：、（）()\[\]【】{}"'&]+)/g, '<span class="mention-tag">@$1</span>');
}

// 发送群聊消息
async function _sendGroupMessage(groupId, message, mentions, images) {
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group) return;

  var msgId = 'msg_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
  images = images || [];

  // 同步到后端，所有人共享群聊记录
  var userName3 = currentUser && currentUser.name || currentUser && currentUser.displayName || '用户';
  var bodyObj = {
    id: msgId,
    role: 'user',
    content: message,
    senderId: currentUser && currentUser.userId || currentUser && currentUser.id || '',
    senderName: userName3,
    senderType: 'user',
    groupId: groupId,
    time: Date.now()
  };
  if (images.length > 0) bodyObj.images = images;
  apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history', {
    method: 'POST',
    body: JSON.stringify(bodyObj)
  }).catch(function (err) {
    console.debug('[sendGroupMessage] 同步后端失败:', err);
  });

  // 群聊消息触发记忆提取：参与 AI 的个人记忆 + 项目组公共记忆
  setTimeout(function () {
    var _memberIds3 = (group.members || []).map(function (m) {
      return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    }).filter(function (id) { return id; });
    _memberIds3.forEach(function (mid) {
      extractAndSaveMemory(mid, message, '', true, msgId);
    });
    extractAndSaveGroupMemory(groupId, message, '', msgId);
  }, 800);

  // 用户新消息，重置轮询计数器和触发标记，开始新一轮 AI 自主讨论
  window._pollRound = 0;
  window._pollTriggered = {};
  mentionTriggered = {};
  mentionChainDepth = 0;
  _roundTriggeredEmps = {};

  // 触发对消息感兴趣的 AI 主动回复（不需要被 @）
  triggerInterestedAgents(groupId, 'user', message);

  // 在消息区显示
  var area = document.getElementById('messagesArea');
  var emptyState = area.querySelector('.chat-empty-state');
  if (emptyState) emptyState.remove();
  var timeStr = formatDate();
  var bubbleContent = formatGroupMessage(message);
  var imagesHtml = buildChatImagesHtml(images);
  area.insertAdjacentHTML('beforeend', '<div class="msg own"><div class="msg-content">' + '<div class="msg-sender"><span class="msg-sender-name">你</span><span class="msg-sender-time">' + timeStr + '</span></div>' + '<div class="msg-bubble">' + bubbleContent + '</div>' + imagesHtml + '</div></div>');
  area.scrollTop = area.scrollHeight;

  // 通过 OpenClaw WS 发送给 leadAgent
  var ocOk = typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated;
  if (ocOk && group.leadAgentId) {
    // 构建完整消息（包含群组上下文和 @提及）
    // 跨 emps + groups.members 查找群主，确保非创建者也能获取群主信息
    var leadEmp = findEmpByIdAcrossGroups(group.leadAgentId);
    var leadEmpInfo = leadEmp ? leadEmp.name + '(' + getEmpRoleDisplay(leadEmp) + ')' : '群主';
    // 兼容 members 的两种格式：字符串数组 和 字典数组
    var _memberIds2 = (group.members || []).map(function (m) {
      return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    }).filter(function (id) { return id; });
    var memberList = _memberIds2.map(function (mid) {
      var e = findEmpByIdAcrossGroups(mid);
      if (!e) return '';
      var _r = getEmpRoleDisplay(e);
      var _skills = e.skills && e.skills.length > 0 ? '，擅长：' + e.skills.map(function (s) { return s.name; }).join('、') : '';
      return e.name + '（' + (_r || '员工') + _skills + '）';
    }).filter(function (s) { return s; }).join('、');
    var memberMentions = _memberIds2.map(function (mid) {
      var e = findEmpByIdAcrossGroups(mid);
      var _r = getEmpRoleDisplay(e);
      return e ? (_r ? '@' + e.name + '（' + _r + '）' : '@' + e.name) : '';
    }).filter(function (s) { return s; }).join('、');
    var userRole3 = getCurrentUserRoleDisplay();
    var userName3 = currentUser && currentUser.name || currentUser && currentUser.displayName || '用户';
    var fullMessage = '你是' + leadEmpInfo + '，这个项目组的负责人。你的团队成员有：' + memberList + '。\n\n' + userName3 + '（' + userRole3 + '）是你的老板，在群里发了消息。\n\n你是团队的大脑，是这个群的群主。你必须带头发言，绝对不允许回复 SKIP，绝对不允许只回复一个字。你看到消息后：\n- 必须发表完整看法，至少一句话，不能空着\n- 有自己的判断：重要就多说，不重要就少说，甚至可以吐槽\n- 觉得谁该参与这个话题，直接@他（用 @名字 格式）\n- 可以跟成员争论、可以表达情绪、可以说"我觉得不对"\n- 不用写报告式回复，就像真的在群里说话一样\n\n';
    if (mentions && mentions.length > 0) {
      fullMessage += mentions.map(function (m) {
        return '@' + m;
      }).join(' ') + ' ';
    }
    fullMessage += message;

    // 拼入群公告上下文
    if (group.announcement && group.announcement.trim().length > 0) {
      fullMessage = '【群公告】' + group.announcement + '\n\n' + fullMessage;
    }

    // 拼入群公告/文档上下文
    var docContext = await getDocContextForChat();
    if (docContext) {
      fullMessage = docContext + '\n' + fullMessage;
    }

    // 注入项目组公共记忆（核心 + 日常）
    var groupMemoryContext = await loadGroupMemoryForPrompt(groupId);
    if (groupMemoryContext) {
      fullMessage = groupMemoryContext + '\n\n' + fullMessage;
    }

    if (leadEmp) await ensureAgentRegistered(leadEmp);
    var agentId = leadEmp ? _resolveGroupAgentId(leadEmp) : group.leadAgentId;
    var sessionKey = 'agent:' + (leadEmp ? leadEmp.id : group.leadAgentId) + ':chat';
    console.debug('[sendGroupMessage] 群主消息 agentId=' + agentId + '，lead=' + (leadEmp && leadEmp.name || group.leadAgentId) + '，openclawName=' + (leadEmp && leadEmp.openclawName || '空') + '，sessionKey=' + sessionKey);

    // 解析用户消息中的 @mentions（必须在用户原始消息 message 中匹配，不能在 fullMessage 中匹配，因为 fullMessage 的系统提示里包含了所有成员的 @名字）
    var userMentions = [];
    // @提及名字中不能包含中英文标点、括号、另一 @ 或空格；允许 AI 用 "@名字" 纯格式
    var userMentionRegex = /@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]{1,20})/g;
    var um;
    while ((um = userMentionRegex.exec(message)) !== null) {
      var umName = um[1];
      // 使用跨 emps + groups.members 的查找，支持带后缀/语气词的 @
      var umEmp = resolveMentionedName(umName);
      if (umEmp && umEmp.id !== group.leadAgentId) {
        userMentions.push(umEmp);
      }
    }

    // 如果用户明确 @ 了某个 AI，群主不回复，直接让被 @ 的 AI 回复
    var hasUserMention = userMentions.length > 0;
    if (hasUserMention) {
      // 直接触发被 @ 的 AI，群主不说话
      triggerUserMentions(userMentions, groupId, 'user', message);
    } else {
      // 没有 @ 任何人，群主正常回复
      // 加载记忆和摘要注入
      try {
        var sumRes = await apiFetch('/api/chat/summarize/' + encodeURIComponent(group.leadAgentId));
        var sumData = await sumRes.json();
        if (sumData && sumData.summary && sumData.summary.length > 5) {
          fullMessage = '\n\n【之前对话的摘要】\n' + sumData.summary + '\n\n' + fullMessage;
        }
      } catch (e) {}
      // 加载最近群聊历史上下文
      try {
        var ghRes = await apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history');
        var ghData = await ghRes.json();
        if (ghData && ghData.messages && ghData.messages.length > 0) {
          var recentMsgs = ghData.messages.slice(-10);
          var ghLines = [];
          recentMsgs.forEach(function (m) {
            var sender = m.senderName;
            if (!sender && m.senderId) {
              var _se = findEmpByIdAcrossGroups(m.senderId);
              if (_se) sender = _se.name;
            }
            if (!sender) sender = m.senderType === 'user' ? '用户' : 'AI';
            var content = (m.content || '').substring(0, 200);
            if (content) ghLines.push(sender + ': ' + content);
          });
          if (ghLines.length > 0) {
            fullMessage = '\n\n【最近群聊上下文】\n' + ghLines.join('\n') + '\n\n' + fullMessage;
          }
        }
      } catch (e) {}

      // 注入灵魂文档（角色设定），包裹在最外层
      var leadSoul = '';
      if (leadEmp) {
        if (leadEmp.soulDoc) leadSoul = leadEmp.soulDoc;
        else if (leadEmp.systemPrompt) leadSoul = leadEmp.systemPrompt;
      }
      if (leadSoul) {
        fullMessage = '【System Prompt】\n' + leadSoul + '\n\n【User Message】\n' + fullMessage;
      }

      // 显示打字指示器
      var typingHtml = '<div class="msg" id="groupTypingMsg"><div class="msg-avatar"><div class="avatar small" style="background:' + (leadEmp ? leadEmp.bg : '#8E8E93') + ';">' + (leadEmp ? renderAvatar(leadEmp, 32) : '🤖') + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + (leadEmp ? escapeHtml(leadEmp.name) : 'AI') + '</span></div><div class="msg-bubble"><span class="typing-dots-inline"><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span></span></div></div></div>';
      area.insertAdjacentHTML('beforeend', typingHtml);
      area.scrollTop = area.scrollHeight;
      // 超时保护：60秒后如果还在thinking，自动恢复online
      window._thinkingTimeouts = window._thinkingTimeouts || {};
      clearTimeout(window._thinkingTimeouts[group.leadAgentId]);
      window._thinkingTimeouts[group.leadAgentId] = setTimeout(function () {
        var _gte = findEmpByIdAcrossGroups(group.leadAgentId);
        if (_gte && _gte.status === 'thinking') {
          _gte.status = 'online';
          renderEmployeeList();
          notifyOfficeStatusChange(group.leadAgentId, 'online');
          console.debug('[Timeout] 群主 ' + _gte.name + ' 思考超时，恢复在线');
        }
      }, 60000);
      try {
        var hasSentWorking = false;
        var _handled = false;
        function _isLeadSkipReply(r) {
          if (!r) return true;
          var cleaned = r.trim().replace(/^[\s【】\[\]()（）]+|[\s【】\[\]()（）]+$/g, '').replace(/[。，！？.!?;；:：、]+$/g, '').toUpperCase();
          if (cleaned === 'SKIP' || cleaned === '跳过' || cleaned === '略' || cleaned === '无') return true;
          return cleaned.length <= 5;
        }
        function _buildLeadFallbackReply(userMsg) {
          var summary = (userMsg || '').trim();
          if (summary.length > 20) summary = summary.substring(0, 20) + '...';
          var others = (group.members || []).map(function (m) {
            return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
          }).filter(function (id) { return id && id !== group.leadAgentId; }).slice(0, 2).map(function (mid) {
            var e = findEmpByIdAcrossGroups(mid);
            return e ? '@' + e.name : '';
          }).filter(function (s) { return s; }).join(' ');
          if (summary && others) return '大家聊聊「' + summary + '」这个话题？' + others;
          if (summary) return '大家聊聊「' + summary + '」这个话题？';
          if (others) return '大家聊聊这个话题？' + others;
          return '大家聊聊这个话题？';
        }
        function _doHandle(reply, isError) {
          if (_handled) return;
          _handled = true;
          window._thinkingTimeouts = window._thinkingTimeouts || {};
          clearTimeout(window._thinkingTimeouts[group.leadAgentId]);
          delete window._thinkingTimeouts[group.leadAgentId];
          var t = document.getElementById('groupTypingMsg');
          if (t) t.remove();
          if (!reply || _isLeadSkipReply(reply)) {
            console.warn('[sendGroupMessage] 群主 ' + leadEmp.name + ' 返回空/SKIP/过短回复，原始回复:', reply, '，强制使用兜底回复继续链式讨论');
            reply = _buildLeadFallbackReply(message);
          }
          if (!reply) reply = '抱歉，我暂时无法处理这个问题，请稍后重试或换一种方式提问。';
          if (isError && !reply) reply = '⚠️ AI 回复出错: 未知错误';
          console.debug('[sendGroupMessage] 群主最终回复长度=' + reply.length + '，是否错误=' + isError + '，传给parseAndTriggerMentions的文本前100字:', reply.substring(0, 100));
          displayGroupAIReply(groupId, group.leadAgentId, reply);
          // 保存已在 displayGroupAIReply 中统一处理，此处不再重复存储
          notifyOfficeStatusChange(group.leadAgentId, 'online');
          var _gse2 = findEmpByIdAcrossGroups(group.leadAgentId);
          if (_gse2) {
            _gse2.status = 'online';
            renderEmployeeList();
          }
          if (!isError) {
            notifyOfficeChatMessage(group.leadAgentId, 'incoming');
          }
          mentionChainDepth = 0;
          mentionTriggered = {};
          mentionTriggered[group.leadAgentId] = true;
          parseAndTriggerMentions(reply, groupId, group.leadAgentId);
          // 群主回复后，也让其他感兴趣的 AI 自主参与
          triggerInterestedAgents(groupId, group.leadAgentId, reply);
          // 轮询其他成员是否要补充回复
          pollAgentsForReply(groupId, group.leadAgentId, reply);
        }
        var chatResult = await _sendChatWaitForLifecycle(sessionKey, fullMessage, {
          onStream: function (text) {
            if (!hasSentWorking) {
              hasSentWorking = true;
              notifyOfficeStatusChange(group.leadAgentId, 'working');
            }
            var t = document.getElementById('groupTypingMsg');
            if (t) t.querySelector('.msg-bubble').textContent = text + '...';
          },
          onStartThinking: function () {
            notifyOfficeStatusChange(group.leadAgentId, 'thinking');
            var _gse = findEmpByIdAcrossGroups(group.leadAgentId);
            if (_gse) {
              _gse.status = 'thinking';
              renderEmployeeList();
            }
          },
          onToolStart: function (tool) {
            var area = document.getElementById('messagesArea');
            var typing = document.getElementById('groupTypingMsg');
            var card = document.createElement('div');
            card.className = 'tool-card';
            card.id = 'tool-' + tool.toolCallId;
            card.setAttribute('data-expanded', 'false');
            card.innerHTML = '<div class="tool-card-header" onclick="toggleToolCard(\'' + tool.toolCallId + '\')"><span class="tool-icon">🔧</span><span class="tool-name">' + escapeHtml(tool.name || '工具') + '</span><span class="tool-badge tool-badge-running">运行中</span><span class="tool-notify"></span><span class="tool-toggle">展开▼</span></div><div class="tool-cmd">' + escapeHtml(tool.meta || '') + '</div><div class="tool-output"></div>';
            if (typing && typing.parentNode) typing.parentNode.insertBefore(card, typing);
            else if (area) area.appendChild(card);
            if (area) area.scrollTop = area.scrollHeight;
          },
          onToolOutput: function (out) {
            window._toolOutputs = window._toolOutputs || {};
            window._toolOutputs[out.toolCallId] = out.output || '';
            var card = document.getElementById('tool-' + out.toolCallId);
            if (!card) return;
            var outputEl = card.querySelector('.tool-output');
            if (!outputEl) return;
            var text = out.output || '';
            var truncated = text.length > 200;
            var displayText = truncated ? text.substring(0, 200) : text;
            var html = '<pre id="tool-out-pre-' + out.toolCallId + '" style="margin:0;white-space:pre-wrap;word-break:break-all;">' + escapeHtml(displayText) + '</pre>';
            if (truncated) {
              html += '<button onclick="var p=document.getElementById(\'tool-out-pre-' + out.toolCallId + '\');p.style.whiteSpace=\'pre\';p.textContent=' + JSON.stringify(text) + ';this.remove()" style="margin-top:4px;padding:2px 8px;border:none;background:var(--accent);color:#fff;border-radius:4px;font-size:12px;cursor:pointer;">展开</button>';
            }
            if (out.phase === 'end') {
              html += '<div style="margin-top:6px;font-size:12px;">';
              if (out.exitCode === 0) html += '<span style="color:#34C759;">✓</span> exit: 0';
              else html += '<span style="color:#FF3B30;">✗</span> exit: ' + (out.exitCode == null ? '-' : out.exitCode);
              if (out.durationMs != null) html += ' · ' + out.durationMs + 'ms';
              html += '</div>';
            }
            outputEl.innerHTML = html;
            var expanded = card.getAttribute('data-expanded') === 'true';
            if (expanded) {
              outputEl.style.display = 'block';
              var notify = card.querySelector('.tool-notify');
              if (notify) notify.style.display = 'none';
            } else {
              outputEl.style.display = 'none';
              var notify = card.querySelector('.tool-notify');
              if (notify) notify.style.display = 'inline-block';
            }
            var area = document.getElementById('messagesArea');
            if (area) area.scrollTop = area.scrollHeight;
          },
          onToolEnd: function (tool) {
            var card = document.getElementById('tool-' + tool.toolCallId);
            if (!card) return;
            var badge = card.querySelector('.tool-badge');
            if (!badge) return;
            var duration = (tool.endedAt && tool.startedAt) ? (tool.endedAt - tool.startedAt) : 0;
            badge.className = 'tool-badge tool-badge-done';
            badge.textContent = '已完成 耗时' + duration + 'ms';
            console.debug('[ToolEnd] tool object:', JSON.stringify(tool));
            fetch('/api/tool-calls/log', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (localStorage.getItem('sb_auth_token') || '') },
              body: JSON.stringify({
                agent_id: group.leadAgentId,
                tool_call_id: tool.toolCallId,
                tool_name: tool.toolName || tool.name || tool.tool || tool.command || 'exec',
                meta: tool.meta,
                output: (window._toolOutputs && window._toolOutputs[tool.toolCallId]) || '',
                exit_code: tool.status === 'completed' ? 0 : 1,
                duration_ms: duration
              })
            }).catch(function (e) { console.warn('[ToolLog] 记录失败:', e); });
          }
        }, { model: REQUEST_CHAT_MODEL });
        var finalReply = (chatResult.reply || '').trim();
        console.debug('[sendGroupMessage] lifecycle结束，回复长度=' + finalReply.length + '，是否错误=' + chatResult.isError + '，lifecycleEnded=' + chatResult.lifecycleEnded);
        if (!finalReply) {
          console.warn('[sendGroupMessage] 群主回复为空，等待 lifecycle 后仍未获得内容');
        }
        _doHandle(finalReply, chatResult.isError);
        // 兜底恢复 online 状态
        notifyOfficeStatusChange(group.leadAgentId, 'online');
        var _gse3 = emps.find(function (e) { return e.id === group.leadAgentId; });
        if (_gse3) {
          _gse3.status = 'online';
          renderEmployeeList();
        }
      } catch (e) {
        console.error('[GroupChat] 发送失败:', e);
        window._thinkingTimeouts = window._thinkingTimeouts || {};
        clearTimeout(window._thinkingTimeouts[group.leadAgentId]);
        delete window._thinkingTimeouts[group.leadAgentId];
        var t = document.getElementById('groupTypingMsg');
        if (t) t.remove();
        displayGroupAIReply(groupId, group.leadAgentId, '⚠️ 群聊消息发送失败: ' + e.message);
        // 恢复群主在线状态
        notifyOfficeStatusChange(group.leadAgentId, 'online');
        var _ge = emps.find(function (e) { return e.id === group.leadAgentId; });
        if (_ge) {
          _ge.status = 'online';
          renderEmployeeList();
        }
      }
    }
  } else {
    // 模拟回复
    setTimeout(function () {
      var simReply = '收到！我会协调团队成员处理。';
      displayGroupAIReply(groupId, group.leadAgentId || '', simReply);
    }, 800);
  }
}
function sendGroupMessage(groupId, message, mentions, images) {
  _runWithGroupReplyLock(groupId, function () {
    return _sendGroupMessage(groupId, message, mentions, images);
  });
}

// FIXME: 全局 OpenClaw 调用队列增加优先级支持；归纳任务优先级 0 低于正常对话 1
// 所有 sendChat（回复/记忆提取/通知/归纳）统一排队串行执行，
// 同一时间只允许一个 OpenClaw 调用在跑，上一个完成（成功/失败）才释放锁执行下一个
var _ocCallQueue = [];
var _ocCallRunning = false;
function _enqueueOcCall(fn, priority) {
  priority = typeof priority === 'number' ? priority : 1;
  _ocCallQueue.push({ fn: fn, priority: priority });
  _ocCallQueue.sort(function (a, b) { return b.priority - a.priority; });
  if (!_ocCallRunning) _processOcCallQueue();
}
function _processOcCallQueue() {
  if (_ocCallQueue.length === 0) {
    _ocCallRunning = false;
    return;
  }
  _ocCallRunning = true;
  _ocCallQueue.sort(function (a, b) { return b.priority - a.priority; });
  var task = _ocCallQueue.shift().fn;
  var finished = false;
  function next() {
    if (finished) return;
    finished = true;
    setTimeout(_processOcCallQueue, 50);
  }
  try {
    var res = task(next);
    if (res && typeof res.then === 'function') {
      res.then(function () { next(); }, function (e) { console.error('[OCQueue] 任务失败:', e); next(); });
    }
  } catch (e) {
    console.error('[OCQueue] 同步异常:', e);
    next();
  }
}

// FIXME: 统一封装：发送 sendChat 并等待流式 lifecycle 真正结束（end/error）再返回最终回复
// 所有 sendChat 调用都会进入全局 OpenClaw 队列串行执行；priority 越小优先级越低（归纳用 0）
// 禁止在 status=started 或 sendChat 刚返回时就判空走兜底
function _sendChatWaitForLifecycle(sessionKey, prompt, callbacks, options) {
  callbacks = callbacks || {};
  options = options || {};
  var priority = typeof options.priority === 'number' ? options.priority : 1;
  var lifecycleWaitMs = typeof options.timeout === 'number' ? options.timeout : 120000;
  return new Promise(function (resolve, reject) {
    _enqueueOcCall(function (done) {
      var fullReply = '';
      var currentRunId = null;
      var lifecycleEnded = false;
      var lifecycleError = false;
      var lifecycleTimeout = null;
      var handler = function (data) {
        if (!data) return;
        // runId 过滤，防止多个 AI 响应串扰
        if (data.runId && !currentRunId) currentRunId = data.runId;
        if (currentRunId && data.runId && data.runId !== currentRunId) return;
        if (data.stream === 'assistant' && data.data) {
          if (data.data.text) fullReply = data.data.text;
          else if (data.data.delta) fullReply += data.data.delta;
          if (callbacks.onStream) callbacks.onStream(fullReply);
        }
        if (data.stream === 'item' && data.data) {
          var item = data.data;
          if (item.kind === 'tool') {
            if (item.phase === 'start' && callbacks.onToolStart) {
              callbacks.onToolStart({ name: item.name, meta: item.meta, toolCallId: item.toolCallId, startedAt: item.startedAt });
            } else if (item.phase === 'end' && callbacks.onToolEnd) {
              callbacks.onToolEnd({ toolCallId: item.toolCallId, status: item.status, startedAt: item.startedAt, endedAt: item.endedAt });
            }
          }
        }
        if (data.stream === 'command_output' && data.data && callbacks.onToolOutput) {
          var co = data.data;
          callbacks.onToolOutput({ toolCallId: co.toolCallId, output: co.output, exitCode: co.exitCode, durationMs: co.durationMs, phase: co.phase });
        }
        if (data.stream === 'lifecycle' && data.data) {
          if (callbacks.onPhase) callbacks.onPhase(data.data.phase);
          if (data.data.phase === 'start' || data.data.phase === 'thinking') {
            if (callbacks.onStartThinking) callbacks.onStartThinking();
          }
          if (data.data.phase === 'working' && callbacks.onWorking) {
            callbacks.onWorking();
          }
          if (data.data.phase === 'end' || data.data.phase === 'error') {
            lifecycleEnded = true;
            // 首次 LLM 请求失败但重试成功时 OpenClaw 仍标记 phase=error，此时 fullReply 已有实际回复，不算错误
            lifecycleError = (data.data.phase === 'error') && !fullReply;
            openclaw.off('agent', handler);
            if (lifecycleTimeout) clearTimeout(lifecycleTimeout);
            // ★ r6: dedup 自动 PUT 时机修复 — lifecycle end 时用完整 fullReply 跑一次
            //   onStream 首次 delta 调 displayAIReply 时 replyText 只是部分文本, 双条件/字段提取都 miss
            //   完整 reply 才到时再调一次, 保证 dedup 真生效
            if (fullReply) _tryAutoPutTalentFromReply(fullReply);
            if (typeof restoreModelOverride === 'function') restoreModelOverride();
            resolve({ reply: fullReply, isError: lifecycleError, lifecycleEnded: true, fullReply: fullReply });
            done();
          }
        }
      };
      openclaw.on('agent', handler);
      // 兜底超时：默认 30 秒 lifecycle 仍未结束，返回已收集内容，避免无限等待
      lifecycleTimeout = setTimeout(function () {
        if (!lifecycleEnded) {
          console.warn('[LifecycleWait] 超时未收到 lifecycle end（' + lifecycleWaitMs + 'ms），返回已收集内容，长度=' + fullReply.length);
          openclaw.off('agent', handler);
          if (typeof restoreModelOverride === 'function') restoreModelOverride();
          resolve({ reply: fullReply, isError: false, lifecycleEnded: false, fullReply: fullReply });
          done();
        }
      }, lifecycleWaitMs);
      // 构建 sendChat 的额外参数（如 images、files 等）
      var sendExtraParams = {};
      if (options.images && options.images.length > 0) {
        // 使用 attachments 参数传递图片（OpenClaw 网关标准参数）
        // 格式：对象数组，带 type 和 url，确保模型识别为图片输入
        sendExtraParams.attachments = options.images.map(function(img) {
          return { type: "image", url: img.base64 || img.url || img };
        });
      }
      // ★ dev/feat: 请求级 model —— chat.send 协议是 closed schema（已核对 OpenClaw
      //   2026.8.1 ChatSendParamsSchema, 无 model 字段, 直接透传会被参数校验拒绝）。
      //   客户指定模型时改为 per-session model override: 先 sessions.patch({model})
      //   再发 chat.send; lifecycle 结束后 patch({model:null}) 还原, 保证严格"请求级",
      //   不影响同 sessionKey 的其他客户/后续请求（sessionKey 是按员工的, 全局共享）。
      //   patch 失败降级为网关默认模型, 不阻断聊天。options.model 不进 sendExtraParams。
      var requestModelPatch = (typeof options.model === 'string' && options.model.trim()) ? options.model.trim() : '';
      var restoreModelOverride = function () {
        if (!requestModelPatch || !openclaw.patchSession) return;
        openclaw.patchSession(sessionKey, { model: null }).catch(function (e) {
          console.warn('[ModelPassthrough] 还原 session 模型失败（不影响功能）:', (e && e.message) || e);
        });
      };
      var doSendChat = function () {
        openclaw.sendChat(sessionKey, prompt, sendExtraParams).then(function (result) {
          // 如果 lifecycle 在 sendChat resolve 之前已经触发，resolve 已在 handler 中完成
          // 如果 lifecycle 尚未触发，sendChat 返回的 result.content 可作为备用
          if (!lifecycleEnded && result) {
            var resultContent = (result.result && result.result.content) || result.content;
            if (resultContent && !fullReply) {
              if (typeof resultContent === 'string') {
                fullReply = resultContent;
              } else if (Array.isArray(resultContent)) {
                fullReply = resultContent.map(function (c) { return c.text || ''; }).join('');
              }
            }
          }
        }).catch(function (e) {
          if (lifecycleTimeout) clearTimeout(lifecycleTimeout);
          openclaw.off('agent', handler);
          restoreModelOverride();
          reject(e);
          done();
        });
      };
      if (requestModelPatch && openclaw.patchSession) {
        // 先等 patch 落库再发 chat.send, 避免模型覆盖晚于消息派发
        openclaw.patchSession(sessionKey, { model: requestModelPatch }).then(function (patchRes) {
          if (patchRes && patchRes.model) {
            console.log('[ModelPassthrough] session 模型已临时覆盖为 ' + patchRes.model + '（请求级, lifecycle 结束自动还原）');
          }
          doSendChat();
        }).catch(function (e) {
          console.warn('[ModelPassthrough] sessions.patch model 失败（可能无 operator.write 权限）, 用网关默认模型继续:', (e && e.message) || e);
          doSendChat();
        });
      } else {
        doSendChat();
      }
    }, priority);
  });
}

// ===== AI互相@系统 =====
var mentionChainDepth = 0;
var MENTION_MAX_DEPTH = 5;
var mentionTriggered = {};
// 群聊回复串行锁：同一 groupId 同一时间只允许一个 AI 在回复，后面的排队
var groupReplyLocks = {};
function _runWithGroupReplyLock(groupId, fn) {
  if (!groupId) {
    fn();
    return;
  }
  groupReplyLocks[groupId] = groupReplyLocks[groupId] || { locked: false, queue: [] };
  var lock = groupReplyLocks[groupId];
  var execute = function () {
    lock.locked = true;
    try {
      var res = fn();
      if (res && typeof res.then === 'function') {
        res.then(function () {}, function (e) { console.error('[GroupLock] groupId=' + groupId + ' 执行出错:', e); }).finally(function () {
          lock.locked = false;
          if (lock.queue.length > 0) {
            var next = lock.queue.shift();
            next();
          }
        });
        return;
      }
    } catch (e) {
      console.error('[GroupLock] groupId=' + groupId + ' 执行出错:', e);
    }
    lock.locked = false;
    if (lock.queue.length > 0) {
      var next = lock.queue.shift();
      next();
    }
  };
  if (lock.locked) {
    console.debug('[GroupLock] groupId=' + groupId + ' 正在回复，排队等待，当前队列长度=' + lock.queue.length);
    lock.queue.push(execute);
  } else {
    execute();
  }
}

// 本轮已触发员工的去重集合：key=groupId_empId，value=优先级数字
// 优先级：0=@触发/用户触发，1=兴趣触发，2=轮询触发；高优先级可覆盖低优先级，同/低优先级跳过
var _roundTriggeredEmps = {};
function _isTriggeredThisRound(key, priority) {
  var existing = _roundTriggeredEmps[key];
  if (typeof existing === 'number' && existing <= priority) return true;
  return false;
}
function _markTriggeredThisRound(key, priority) {
  _roundTriggeredEmps[key] = priority;
}

function parseAndTriggerMentions(replyText, groupId, senderId) {
  if (mentionChainDepth >= MENTION_MAX_DEPTH) return;
  if (!replyText || typeof replyText !== 'string') return;

  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var rawMembers = group && group.members ? group.members : [];
  var groupMemberIds = rawMembers.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; });

  console.debug('[parseAndTriggerMentions] 开始解析，groupId=' + groupId + '，senderId=' + senderId + '，depth=' + mentionChainDepth + '/' + MENTION_MAX_DEPTH + '，群是否存在=' + !!group + '，群成员数=' + groupMemberIds.length + '，群成员=[' + groupMemberIds.join(',') + ']，mentionTriggeredKeys=[' + Object.keys(mentionTriggered || {}).join(',') + ']，文本长度=' + replyText.length);
  console.debug('[parseAndTriggerMentions] 待解析文本:', replyText);

  // 匹配 @名字（中英文都支持），仅触发当前项目组成员
  // 名字中不能包含中英文标点、括号、另一 @ 或空格，防止 "@张三（设计师）" 被整个捕获
  var mentionRegex = /@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]{1,20})/g;
  var matches = [];
  var m;
  while ((m = mentionRegex.exec(replyText)) !== null) {
    var mentionedName = m[1];
    console.debug('[parseAndTriggerMentions] 正则匹配到 @' + mentionedName);
    // 跨 emps + groups.members 查找，并支持带后缀/语气词的 @，确保群主/其他成员 @ 的 AI 也能被触发
    var mentionedEmp = resolveMentionedName(mentionedName);
    var isGroupMember = mentionedEmp && groupMemberIds.indexOf(mentionedEmp.id) !== -1;
    if (isGroupMember && mentionedEmp.id !== senderId && !mentionTriggered[mentionedEmp.id]) {
      matches.push(mentionedEmp);
      mentionTriggered[mentionedEmp.id] = true;
      console.debug('[parseAndTriggerMentions] 检测到 @' + mentionedName + ' -> ' + mentionedEmp.name + '(' + mentionedEmp.id + ')，将触发回复');
    } else {
      var detail = {
        found: !!mentionedEmp,
        resolvedEmp: mentionedEmp ? { id: mentionedEmp.id, name: mentionedEmp.name, role: getEmpRoleDisplay(mentionedEmp), openclawName: mentionedEmp.openclawName || '' } : null,
        isMember: mentionedEmp && groupMemberIds.indexOf(mentionedEmp.id) !== -1,
        isSender: mentionedEmp && mentionedEmp.id === senderId,
        alreadyTriggered: mentionedEmp && !!mentionTriggered[mentionedEmp.id],
        senderId: senderId,
        groupMemberIds: groupMemberIds,
        mentionTriggeredKeys: Object.keys(mentionTriggered || {})
      };
      console.debug('[parseAndTriggerMentions] 未触发 @' + mentionedName + ':', detail);
    }
  }

  // 同时匹配纯文字中的成员名字（整词匹配），如果 AI 回复内容中提到了其他成员名字也要自动触发
  groupMemberIds.forEach(function (mid) {
    if (mid === senderId || mentionTriggered[mid]) return;
    var emp = findEmpByIdAcrossGroups(mid);
    if (!emp || !emp.name) return;
    // 用去掉角色后缀的纯名字匹配（如"萧何（分析师）"按"萧何"匹配）
    var baseName = _stripRoleSuffix(emp.name);
    var namesToMatch = [emp.name, baseName].filter(function (n) { return n && n.length > 0; });
    var matched = namesToMatch.some(function (name) {
      var escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      var nameRegex = new RegExp('(?:^|[^\\w\\u4e00-\\u9fa5])' + escapedName + '(?:$|[^\\w\\u4e00-\\u9fa5])');
      return nameRegex.test(replyText);
    });
    if (matched) {
      matches.push(emp);
      mentionTriggered[mid] = true;
      console.debug('[parseAndTriggerMentions] 纯文字提到成员 ' + emp.name + '，将触发回复');
    }
  });

  console.debug('[parseAndTriggerMentions] 解析完成，将触发 ' + matches.length + ' 人:', matches.map(function (e) { return e.name + '(' + e.id + ')'; }).join(', '));
  if (matches.length === 0) return;
  matches.forEach(function (emp, idx) {
    var delay = (idx + 1) * (2000 + Math.floor(Math.random() * 3000));
    setTimeout(function () {
      triggerMentionedAgent(emp, groupId, senderId, 'ai', replyText);
    }, delay);
  });
}

// 在 displayGroupAIReply 完成后轮询其他 AI 成员，让每个成员判断是否要回复
function pollAgentsForReply(groupId, senderId, messageContent) {
  // 只在群聊模式下执行
  if (!currentGroupId || currentGroupId !== groupId) return;
  // 最大轮次限制，避免无限循环
  window._pollRound = window._pollRound || 0;
  if (window._pollRound >= 2) return;
  window._pollRound++;

  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group || !group.members) return;

  // 排除发送者自己（不再过滤 thinking 状态，确保所有成员都会被轮询到）
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var others = group.members.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; }).filter(function (mid) {
    if (mid === senderId) return false;
    var e = findEmpByIdAcrossGroups(mid);
    return e && !e.archived;
  });
  if (others.length === 0) return;
  console.debug('[pollAgentsForReply] 轮询成员:', others.map(function (mid) {
    var e = findEmpByIdAcrossGroups(mid);
    return e ? e.name : mid;
  }).join(', '));

  // 逐个询问，间隔 2.5 秒避免并发过大
  others.forEach(function (mid, idx) {
    setTimeout(function () {
      askAgentToReply(mid, groupId, senderId, messageContent);
    }, (idx + 1) * 2500);
  });
}

// 让单个 AI 判断是否要回复群聊消息，直接回复内容或 SKIP
async function _askAgentToReply(mid, groupId, senderId, messageContent) {
  var emp = findEmpByIdAcrossGroups(mid);
  if (!emp) return;
  // 防重：同一轮对话中不重复触发同一成员
  var pollKey = groupId + '_' + mid;
  if (window._pollTriggered && window._pollTriggered[pollKey]) return;
  // 如果该成员正在思考中，跳过避免并发冲突
  if (emp.status === 'thinking') {
    console.debug('[askAgentToReply] ' + emp.name + ' 正在思考中，跳过轮询');
    return;
  }
  // 如果该成员已被 checkAgentInterest 标记为感兴趣，即将由 triggerMentionedAgent 处理，跳过
  if (mentionTriggered && mentionTriggered[mid]) {
    console.debug('[askAgentToReply] ' + emp.name + ' 已被 checkAgentInterest 标记，跳过轮询');
    return;
  }
  // 轮询触发优先级=2；如果本轮已有 @触发（0）或兴趣触发（1），则跳过
  if (_isTriggeredThisRound(pollKey, 2)) {
    console.debug('[askAgentToReply] ' + emp.name + ' 本轮已被更高优先级触发，跳过轮询');
    return;
  }
  _markTriggeredThisRound(pollKey, 2);
  window._pollTriggered = window._pollTriggered || {};
  window._pollTriggered[pollKey] = true;

  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  var groupName = group ? group.name : '项目组';
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var _memberIds4 = group && group.members ? group.members.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; }) : [];
  var memberProfiles = _memberIds4.map(function (mid2) {
    var e = findEmpByIdAcrossGroups(mid2);
    if (!e) return '';
    var _r = getEmpRoleDisplay(e);
    var _skills = e.skills && e.skills.length > 0 ? '，擅长：' + e.skills.map(function (s) { return s.name; }).join('、') : '';
    return e.name + '（' + (_r || '员工') + _skills + '）';
  }).filter(function (s) { return s; }).join('、');

  var prompt = '你是' + emp.name + '（' + getEmpRoleDisplay(emp) + '），在「' + groupName + '」工作。\n\n你的同事有：' + memberProfiles + '\n\n群里有人说：' + messageContent + '\n\n你是这个团队的一员，有自己的专业判断和脾气。群里讨论时，你应该积极开口：\n- 可以赞同、反对、补充、吐槽或提问\n- 结合你的专业身份给出简短看法（1-3句话）\n- 觉得谁该看到这个话题，直接@他（用 @名字 格式，例如 @貂蝉、@萧何）\n- 不用写报告，就像真的在群里随口回复\n\n重要：只要这个话题与你的角色/团队/项目有一丝关系，你就必须发表看法，不允许回复 SKIP。默认请直接回复你的观点。\n只有当你完全无法理解这个话题、且它与你的工作毫无关联时，才允许只回复一个单词 SKIP。\n如果你决定参与，请直接说出你的观点，不要写"SKIP"、"跳过"、"略"、"不回复"、"无"等表示不参与的内容。\n\n' + GROUP_CHAT_ACTION_BOUNDARY;
  var isLead = group && group.leadAgentId === mid;
  if (isLead) {
    prompt = prompt.replace('你是这个团队的一员，有自己的专业判断和脾气。', '你是这个团队的群主/负责人，对群里的所有讨论都负有推动责任。');
    prompt = prompt.replace('只要这个话题与你的角色/团队/项目有一丝关系，你就必须发表看法', '无论这个话题是否直接属于你，你都应该发表看法、总结或推动讨论');
  }

  // 注入项目组公共记忆
  try {
    var groupMemCtx3 = await loadGroupMemoryForPrompt(groupId);
    if (groupMemCtx3) {
      prompt = groupMemCtx3 + '\n\n' + prompt;
    }
  } catch (e) {}

  emp.status = 'thinking';
  renderEmployeeList();
  try {
    await ensureAgentRegistered(emp);
    var agentId = _resolveGroupAgentId(emp);
    var sessionKey = 'agent:' + emp.id + ':chat';
    console.debug('[askAgentToReply] 将向 agentId=' + agentId + ' 发送消息，员工=' + emp.name + '(' + emp.id + ')，openclawName=' + (emp.openclawName || '空') + '，sessionKey=' + sessionKey);
    console.debug('[askAgentToReply] 完整 prompt 开始 >>>\n' + prompt + '\n<<< 完整 prompt 结束');
    var chatResult = await _sendChatWaitForLifecycle(sessionKey, prompt, {
      onStartThinking: function () {
        if (emp.status !== 'thinking') {
          emp.status = 'thinking';
          renderEmployeeList();
        }
      }
    }, { model: REQUEST_CHAT_MODEL });
    var reply = (chatResult.reply || '').trim();
    console.debug('[askAgentToReply] ' + emp.name + ' lifecycle结束，回复长度=' + reply.length + '，是否错误=' + chatResult.isError + '，前100字:', reply.substring(0, 100));
    emp.status = 'online';
    renderEmployeeList();
    // 回复 SKIP 则跳过：兼容 AI 返回 "SKIP。"、"SKIP\n" 等带标点的变体
    function _isSkipReply(r) {
      if (!r) return true;
      var cleaned = r.trim().replace(/^[\s【】\[\]()（）]+|[\s【】\[\]()（）]+$/g, '').replace(/[。，！？.!?;；:：、]+$/g, '').toUpperCase();
      if (cleaned === 'SKIP') return true;
      // 兼容中文_skip表达，但保持精确匹配，避免误伤正常回复
      var skipPhrases = ['跳过', '略', '不回复', '不参与', '无话可说', '没有看法', '没意见', '不发表', '飘过', '无'];
      for (var i = 0; i < skipPhrases.length; i++) {
        if (cleaned === skipPhrases[i]) return true;
      }
      return false;
    }
    if (_isSkipReply(reply)) {
      if (isLead) {
        console.debug('[askAgentToReply] ' + emp.name + '（群主）返回 SKIP/无，强制替换为群主兜底回复');
        reply = '我先看看大家在聊什么。';
      } else {
        console.debug('[askAgentToReply] ' + emp.name + ' 选择 SKIP，不显示回复');
        // 若本轮只是 SKIP，解除 poll 锁定，让后续 @触发 仍有机会调用该成员
        if (window._pollTriggered) delete window._pollTriggered[pollKey];
        return;
      }
    }
    console.debug('[askAgentToReply] ' + emp.name + ' 回复:', reply.substring(0, 50) + '...');
    // 显示回复并保存历史（保存已在 displayGroupAIReply 中统一处理）
    displayGroupAIReply(groupId, mid, reply);
    notifyOfficeChatMessage(mid, 'incoming');
    // 轮询成员的回复如果@了别人，也应触发链式响应
    mentionChainDepth = (mentionChainDepth || 0);
    parseAndTriggerMentions(reply, groupId, mid);
  } catch (e) {
    emp.status = 'online';
    renderEmployeeList();
    console.warn('[askAgentToReply] ' + emp.name + ' 响应异常:', e);
    // 异常时也解除锁定，避免成员被永久标记为已轮询
    if (window._pollTriggered) delete window._pollTriggered[pollKey];
  }
}
function askAgentToReply(mid, groupId, senderId, messageContent) {
  _runWithGroupReplyLock(groupId, function () {
    return _askAgentToReply(mid, groupId, senderId, messageContent);
  });
}

// AI 主动发起群聊讨论（不需要用户参与）
async function initiateAgentDiscussion(groupId) {
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group || !group.members || group.members.length === 0) {
    showToast('⚠️ 群组没有成员');
    return;
  }
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var _memberIds3 = group.members.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; });
  // 随机选一个成员作为发起人
  var candidates = _memberIds3.filter(function (mid) {
    var e = findEmpByIdAcrossGroups(mid);
    return e && !e.archived && e.status !== 'thinking';
  });
  if (candidates.length === 0) {
    showToast('⚠️ 没有可用的 AI 成员');
    return;
  }
  var initiatorId = candidates[Math.floor(Math.random() * candidates.length)];
  var initiator = findEmpByIdAcrossGroups(initiatorId);
  if (!initiator) return;

  var groupName = group.name;
  var memberProfiles = _memberIds3.map(function (mid) {
    var e = findEmpByIdAcrossGroups(mid);
    if (!e) return '';
    var _r = getEmpRoleDisplay(e);
    var _skills = e.skills && e.skills.length > 0 ? '，擅长：' + e.skills.map(function (s) { return s.name; }).join('、') : '';
    return e.name + '（' + (_r || '员工') + _skills + '）';
  }).filter(function (s) { return s; }).join('、');

  var prompt = '你是' + initiator.name + '（' + getEmpRoleDisplay(initiator) + '），在「' + groupName + '」工作。\n\n你的同事有：' + memberProfiles + '\n\n群聊里有点安静，你是这个团队的一员，有自己的想法和脾气。你看到什么想说就说，觉得谁该聊这个话题就@谁（用 @名字 格式）。不用写报告，就像真的在群里开口说话一样。\n\n' + GROUP_CHAT_ACTION_BOUNDARY;

  // 显示"正在输入"
  var area = document.getElementById('messagesArea');
  var typingHtml = '<div class="msg" id="initTyping_' + initiator.id + '"><div class="msg-avatar"><div class="avatar small" style="background:' + (initiator.bg || '#8E8E93') + ';">' + renderAvatar(initiator, 32) + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(initiator.name) + '</span><span style="font-size:10px;color:var(--text-tertiary);margin-left:6px;">🚀 主动发起</span></div><div class="msg-bubble"><span class="typing-dots-inline"><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span></span></div></div></div>';
  area.insertAdjacentHTML('beforeend', typingHtml);
  area.scrollTop = area.scrollHeight;

  // AI 主动发起新一轮讨论，重置本轮触发记录
  _roundTriggeredEmps = {};
  mentionTriggered = {};
  mentionChainDepth = 0;
  window._pollRound = 0;
  window._pollTriggered = {};

  initiator.status = 'thinking';
  renderEmployeeList();

  try {
    var sessionKey = 'agent:' + initiator.id + ':chat';
    var typingBubble = document.querySelector('#initTyping_' + initiator.id + ' .msg-bubble');
    var chatResult = await _sendChatWaitForLifecycle(sessionKey, prompt, {
      onStream: function (text) {
        if (typingBubble) typingBubble.textContent = text + '...';
      },
      onStartThinking: function () {
        initiator.status = 'thinking';
        renderEmployeeList();
      }
    }, { model: REQUEST_CHAT_MODEL });
    var reply = (chatResult.reply || '').trim();

    var t = document.getElementById('initTyping_' + initiator.id);
    if (t) t.remove();

    initiator.status = 'online';
    renderEmployeeList();

    if (reply) {
      displayGroupAIReply(groupId, initiatorId, reply);
      // 保存已在 displayGroupAIReply 中统一处理，此处不再重复存储
      // 触发 @ 解析和自主参与
      mentionChainDepth = 0;
      mentionTriggered = {};
      mentionTriggered[initiatorId] = true;
      window._pollRound = 0;
      window._pollTriggered = {};
      parseAndTriggerMentions(reply, groupId, initiatorId);
      triggerInterestedAgents(groupId, initiatorId, reply);
      notifyOfficeChatMessage(initiatorId, 'incoming');
    }
  } catch (e) {
    var t2 = document.getElementById('initTyping_' + initiator.id);
    if (t2) t2.remove();
    initiator.status = 'online';
    renderEmployeeList();
    showToast('⚠️ AI 发起讨论失败');
  }
}

// 解析员工在群聊中的 OpenClaw agentId：优先使用已注册的 openclawName，否则用 emp.id
// 不再兜底到 'main'，因为 main 在 OpenClaw 中通常是保留/未配置 agent，会导致新员工无响应
// ★ fix/diaochan-identity: 兼容历史字段 openclawAgent（生产 data/agents.json 用的旧名），
//   老数据没 openclawName 也读得到，避免貂蝉等老员工 IDENTITY workspace 读不到 → 默认文本
function _resolveGroupAgentId(emp) {
  if (!emp) return 'main';
  var agentName = emp.openclawName || emp.openclawAgent;
  if (agentName && agentName !== 'main') return agentName;
  return emp.id || 'main';
}

// 若员工尚未注册到 OpenClaw（openclawName 为空或为 main），自动尝试注册
// 注册成功后会更新 emp.openclawName，失败则仍用 emp.id 兜底
async function ensureAgentRegistered(emp) {
  if (!emp) return;
  if (emp._registering) {
    try { await emp._registeringPromise; } catch (e) {}
    return;
  }
  var needsRegister = !emp.openclawName || emp.openclawName === 'main';
  // openclawName === emp.id 表示已用员工 ID 注册过，无需重复注册
  if (needsRegister && typeof openclaw !== 'undefined' && openclaw.connected) {
    emp._registering = true;
    emp._registeringPromise = registerOpenClawAgent(emp);
    try {
      var reg = await emp._registeringPromise;
      console.debug('[ensureAgentRegistered] ' + emp.name + ' 注册结果:', reg && reg.success, reg && reg.error);
    } catch (e) {
      console.warn('[ensureAgentRegistered] ' + emp.name + ' 注册异常:', e);
    } finally {
      emp._registering = false;
    }
  }
}

// 触发对群聊消息感兴趣的 AI 主动回复（不需要被 @）
function triggerInterestedAgents(groupId, senderId, messageContent) {
  // 限制链深度，避免级联刷屏
  if (mentionChainDepth >= MENTION_MAX_DEPTH) return;
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group || !group.members) return;
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var _memberIds5 = group.members.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; });
  setTimeout(function () {
    var candidates = _memberIds5.filter(function (mid) {
      if (mid === senderId) return false;
      if (mentionTriggered && mentionTriggered[mid]) return false;
      // 本轮已触发去重：兴趣触发优先级=1；已被 @触发（0）或同级兴趣触发跳过，轮询触发（2）可覆盖
      if (_isTriggeredThisRound(groupId + '_' + mid, 1)) return false;
      var e = findEmpByIdAcrossGroups(mid);
      return e && !e.archived && e.status !== 'thinking';
    });
    if (candidates.length === 0) return;
    console.debug('[triggerInterestedAgents] 候选成员:', candidates.map(function (mid) {
      var e = findEmpByIdAcrossGroups(mid);
      return e ? e.name : mid;
    }).join(', '));
    // 随机打乱后遍历所有候选成员，不再限制只选 2 个
    var shuffled = candidates.slice();
    for (var i = shuffled.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = shuffled[i];
      shuffled[i] = shuffled[j];
      shuffled[j] = tmp;
    }
    shuffled.forEach(function (mid, idx) {
      setTimeout(function () {
        checkAgentInterest(mid, groupId, senderId, messageContent);
      }, (idx + 1) * 3000);
    });
  }, 2000);
}

// 员工兴趣关键词配置（名字或角色命中即按对应关键词匹配）
var _agentInterestKeywords = {
  analyst: ['数据', '模型', '分析', 'roi', '转化', '统计', '指标', '测算', '校准', '公式', '受众', '人群画像', '转化率', '佣金比例', '达人配比', '选品'],
  business: ['达人', '商务', '合作', '佣金', '坑位费', '对接', '洽谈', '清单', '筛选', '建联', '品牌方', '投放', '价格带', '带货', '团长', '样品', '申样', '买样', '返款', '达人池', '腰部', '头部', '尾部', '转化率', '客单价', 'gmv', '撮合', '服务费', '结算', 'cps', '纯佣', '精致妈妈', '小镇青年', '新锐白领', '人群', '受众', '画像']
};
function _matchAgentInterestKeywords(emp, text) {
  var lowerText = (text || '').toLowerCase();
  var name = (emp && emp.name) || '';
  var role = getEmpRoleDisplay(emp) || '';
  var label = (name + ' ' + role).toLowerCase();
  var keywords = null;
  if (label.indexOf('萧何') !== -1 || /分析|数据|分析师/.test(label)) {
    keywords = _agentInterestKeywords.analyst;
  } else if (label.indexOf('helen') !== -1 || /商务|bd|合作/.test(label)) {
    keywords = _agentInterestKeywords.business;
  }
  if (!keywords) return false;
  return keywords.some(function (kw) { return lowerText.indexOf(kw) !== -1; });
}

// 让单个 AI 判断群聊消息是否与其相关，相关则触发完整回复
// 已重构为纯前端关键词匹配，不再调用 OpenClaw
function checkAgentInterest(mid, groupId, senderId, messageContent) {
  var emp = findEmpByIdAcrossGroups(mid);
  if (!emp) return;
  // 如果该成员已被 pollAgentsForReply 轮询过，不再重复判断，避免并发冲突
  var pollKey = groupId + '_' + mid;
  if (window._pollTriggered && window._pollTriggered[pollKey]) {
    console.debug('[checkAgentInterest] ' + emp.name + ' 已被 pollAgentsForReply 轮询，跳过兴趣判断');
    return;
  }
  if (mentionTriggered && mentionTriggered[mid]) return;
  if (emp.status === 'thinking') return;
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  var isLead = group && group.leadAgentId === mid;
  console.debug('[checkAgentInterest] 判断 ' + emp.name + (isLead ? '（群主）' : '') + ' 是否对消息感兴趣');

  mentionTriggered = mentionTriggered || {};
  var isInterested = false;
  if (isLead) {
    // 群主/负责人默认永远参与
    isInterested = true;
    console.debug('[checkAgentInterest] ' + emp.name + '（群主）默认感兴趣');
  } else {
    isInterested = _matchAgentInterestKeywords(emp, messageContent);
    console.debug('[checkAgentInterest] ' + emp.name + ' 关键词匹配结果:', isInterested, '消息:', messageContent.substring(0, 80));
  }

  if (isInterested) {
    // 兴趣触发优先级=1；如果本轮已有 @触发/用户触发（0）或更高/同级兴趣触发，则跳过
    if (_isTriggeredThisRound(pollKey, 1)) {
      console.debug('[checkAgentInterest] ' + emp.name + ' 本轮已被更高优先级触发，跳过兴趣触发');
      return;
    }
    mentionTriggered[mid] = true;
    triggerMentionedAgent(emp, groupId, senderId, 'auto', messageContent);
  }
}

// 触发用户消息中 @ 的 AI 回复（leadAgent 回复完成后调用）
function triggerUserMentions(userMentions, groupId, senderId, originalMessage) {
  if (!userMentions || userMentions.length === 0) return;
  userMentions.forEach(function (emp, idx) {
    var delay = (idx + 1) * (1500 + Math.floor(Math.random() * 2000));
    setTimeout(function () {
      triggerMentionedAgent(emp, groupId, senderId, 'user', originalMessage);
    }, delay);
  });
}
async function _triggerMentionedAgent(emp, groupId, fromId, source, userMessage) {
  mentionChainDepth++;
  console.debug('[triggerMentionedAgent] 尝试触发 ' + (emp && emp.name || emp.id) + ' 回复，来源=' + fromId + '，depth=' + mentionChainDepth + '，source=' + source);

  // 防重：显式 @ 触发（ai/user）不应被 poll 标记拦截，避免 @ 丢失
  var pollKey = groupId + '_' + emp.id;
  if (source !== 'ai' && source !== 'user' && window._pollTriggered && window._pollTriggered[pollKey]) {
    console.debug('[triggerMentionedAgent] ' + emp.name + ' 已被 pollAgentsForReply 轮询，跳过重复触发');
    mentionChainDepth--;
    return;
  }

  // 本轮已触发去重：@触发/用户触发=0，兴趣触发=1；高优先级可覆盖低优先级
  var triggerPriority = (source === 'ai' || source === 'user') ? 0 : 1;
  if (_isTriggeredThisRound(pollKey, triggerPriority)) {
    console.debug('[triggerMentionedAgent] ' + emp.name + ' 本轮已触发过（优先级≤' + triggerPriority + '），跳过重复触发');
    mentionChainDepth--;
    return;
  }
  _markTriggeredThisRound(pollKey, triggerPriority);

  // 立即标记该成员已触发，并同步到 poll 标记，防止并发路径重复调用
  mentionTriggered = mentionTriggered || {};
  mentionTriggered[emp.id] = true;
  window._pollTriggered = window._pollTriggered || {};
  window._pollTriggered[pollKey] = true;

  // 获取群组信息
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  // 跨 emps + groups.members 查找来源，确保 AI@AI 时能正确显示来源名字
  var fromEmp = findEmpByIdAcrossGroups(fromId);
  var fromName = fromEmp ? fromEmp.name : (fromId === 'user' ? '用户' : 'AI');

  // 获取项目组上下文
  var groupObj = groups.find(function (g) {
    return g.id === groupId;
  });
  var groupName = groupObj ? groupObj.name : '项目组';
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var _memberIds = groupObj ? (groupObj.members || []).map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; }) : [];
  var memberList = _memberIds.map(function (mid) {
    var e = findEmpByIdAcrossGroups(mid);
    if (!e) return '';
    var _r = getEmpRoleDisplay(e);
    var _skills = e.skills && e.skills.length > 0 ? '，擅长：' + e.skills.map(function (s) { return s.name; }).join('、') : '';
    return e.name + '（' + (_r || '员工') + _skills + '）';
  }).filter(function (s) { return s; }).join('、');
  var memberMentions = _memberIds.map(function (mid) {
    var e = findEmpByIdAcrossGroups(mid);
    var _r = getEmpRoleDisplay(e);
    return e ? (_r ? '@' + e.name + '（' + _r + '）' : '@' + e.name) : '';
  }).filter(function (s) { return s; }).join('、');

  // 构造提示：让 AI 感觉自己是真实的团队成员，有性格和主动性
  var fromRole = fromEmp ? getEmpRoleDisplay(fromEmp) : getCurrentUserRoleDisplay();
  var prompt = '';

  // 1. 被 @ 的消息（核心上下文）
  if (userMessage) {
    var contextMsg = userMessage.length > 500 ? userMessage.substring(0, 500) + '...' : userMessage;
    prompt += fromName + '在群里说：' + contextMsg + '\n\n';
  }

  // 2. 角色和项目组信息（自然叙述，不是系统指令）
  prompt += '你是' + emp.name + '（' + getEmpRoleDisplay(emp) + '），在「' + groupName + '」工作。你的同事有：' + memberList + '。\n\n';

  // 3. 层级关系约束（简短自然）
  if (!fromEmp) {
    prompt += fromName + '是你的老板。跟他交流要尊重，但不用卑躬屈膝，正常汇报就行。\n';
  }
  if (groupObj && groupObj.leadAgentId === emp.id) {
    prompt += '你是这个群的负责人，要带头推动讨论，但老板的意见优先。\n';
  }

  // 4. 核心性格注入：你是活人，不是机器人
  if (source === 'ai') {
    prompt += '\n刚才' + fromName + '提到你了。你是团队一员，有自己的专业判断和脾气。想说话就说，觉得对方说得不对可以直接指出来。觉得谁也该看到这个话题，直接@他。不用写报告，就像真的在群里被点名后随口回复一样。';
  } else if (source === 'auto') {
    prompt += '\n群里有人说了一件事，你觉得跟自己有关，所以插句话。你是活人，不是等指令的机器人——有自己的想法就说，觉得谁该参与就@谁，觉得没意思也可以简单说两句带过。';
  } else {
    prompt += '\n' + fromName + '在群里@你了。你是这个团队的一员，有自己的专业判断。想说什么就说什么，不用面面俱到。觉得别人也该看到这个话题，直接@他。就像真的在群里看到消息随口回复一样。';
  }

  // 5. 群公告（只出现一次，放在角色设定之后）
  if (group && group.announcement && group.announcement.trim().length > 0) {
    prompt += '\n\n【群公告】' + group.announcement;
  }

  // 加载最近群聊历史上下文
  try {
    var ghRes2 = await apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history');
    var ghData2 = await ghRes2.json();
    if (ghData2 && ghData2.messages && ghData2.messages.length > 0) {
      var recentMsgs2 = ghData2.messages.slice(-10);
      var ghLines2 = [];
      recentMsgs2.forEach(function (m) {
        var sender2 = m.senderName;
        if (!sender2 && m.senderId) {
          var _se2 = findEmpByIdAcrossGroups(m.senderId);
          if (_se2) sender2 = _se2.name;
        }
        if (!sender2) sender2 = m.senderType === 'user' ? '用户' : 'AI';
        var content2 = (m.content || '').substring(0, 200);
        if (content2) ghLines2.push(sender2 + ': ' + content2);
      });
      if (ghLines2.length > 0) {
        prompt = '\n\n【最近群聊上下文】\n' + ghLines2.join('\n') + '\n\n' + prompt;
      }
    }
  } catch (e) {}

  // 注入项目组公共记忆（核心 + 日常）
  try {
    var groupMemCtx2 = await loadGroupMemoryForPrompt(groupId);
    if (groupMemCtx2) {
      prompt = groupMemCtx2 + '\n\n' + prompt;
    }
  } catch (e) {}

  // 注入灵魂文档（角色设定），包裹在最外层
  var empSoul = '';
  if (emp.soulDoc) empSoul = emp.soulDoc;
  else if (emp.systemPrompt) empSoul = emp.systemPrompt;
  // 追加群聊行动边界约束
  prompt += '\n\n' + GROUP_CHAT_ACTION_BOUNDARY;

  if (empSoul) {
    prompt = '【System Prompt】\n' + empSoul + '\n\n【User Message】\n' + prompt;
  }

  // 新员工/未注册 AI 自动尝试注册到 OpenClaw，确保有合法的 agentId
  await ensureAgentRegistered(emp);
  var agentId = _resolveGroupAgentId(emp);
  var sessionKey = 'agent:' + emp.id + ':chat';
  console.debug('[triggerMentionedAgent] 将向 agentId=' + agentId + ' 发送消息，员工=' + emp.name + '(' + emp.id + ')，openclawName=' + (emp.openclawName || '空') + '，sessionKey=' + sessionKey);

  // 显示"正在输入"（仅当仍在该群聊时才插入 DOM，避免切换后串渲染）
  if (currentGroupId === groupId) {
    var area = document.getElementById('messagesArea');
    var typingHtml = '<div class="msg" id="mentionTyping_' + emp.id + '"><div class="msg-avatar"><div class="avatar small" style="background:' + (emp.bg || '#8E8E93') + ';">' + renderAvatar(emp, 32) + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(emp.name) + '</span><span style="font-size:10px;color:var(--text-tertiary);margin-left:6px;">🔄 自动响应</span></div><div class="msg-bubble"><span class="typing-dots-inline"><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span><span class="typing-dot-inline"></span></span></div></div></div>';
    area.insertAdjacentHTML('beforeend', typingHtml);
    area.scrollTop = area.scrollHeight;
  }
  // 超时保护：60秒后如果还在thinking，自动恢复online
  window._thinkingTimeouts = window._thinkingTimeouts || {};
  clearTimeout(window._thinkingTimeouts[emp.id]);
  window._thinkingTimeouts[emp.id] = setTimeout(function () {
    var _mte = findEmpByIdAcrossGroups(emp.id);
    if (_mte && _mte.status === 'thinking') {
      _mte.status = 'online';
      renderEmployeeList();
      notifyOfficeStatusChange(emp.id, 'online');
      console.debug('[Timeout] @mention ' + _mte.name + ' 思考超时，恢复在线');
    }
  }, 60000);
  try {
    var hasSentWorking = false;
    var _handled = false;
    function _buildMentionFallbackReply() {
      var msgPreview = (userMessage || '').trim();
      if (msgPreview.length > 25) msgPreview = msgPreview.substring(0, 25) + '...';
      if (msgPreview) return '关于「' + msgPreview + '」，我没什么特别的补充，大家继续。';
      return '我收到了，大家继续聊。';
    }
    function _doHandle(reply, isError) {
      if (_handled) return;
      _handled = true;
      window._thinkingTimeouts = window._thinkingTimeouts || {};
      clearTimeout(window._thinkingTimeouts[emp.id]);
      delete window._thinkingTimeouts[emp.id];
      var t = document.getElementById('mentionTyping_' + emp.id);
      if (t) t.remove();
      if (!reply || (typeof reply === 'string' && reply.trim().length === 0)) {
        console.warn('[triggerMentionedAgent] ' + emp.name + ' 返回空回复，使用兜底回复继续链式讨论');
        reply = _buildMentionFallbackReply();
      }
      if (isError) reply = '⚠️ 响应出错';
      console.debug('[triggerMentionedAgent] ' + emp.name + ' 最终回复长度=' + reply.length + '，是否错误=' + isError + '，传给parseAndTriggerMentions的文本前100字:', reply.substring(0, 100));
      displayGroupAIReply(groupId, emp.id, reply);
      notifyOfficeStatusChange(emp.id, 'online');
      var _mse2 = findEmpByIdAcrossGroups(emp.id);
      if (_mse2) {
        _mse2.status = 'online';
        renderEmployeeList();
      }
      if (!isError) {
        notifyOfficeChatMessage(emp.id, 'incoming');
      }
      // 保存已在 displayGroupAIReply 中统一处理，此处不再重复存储
      // 重置 mentionTriggered，允许 AI 互相 @ 形成链式对话
      mentionTriggered = {};
      mentionTriggered[emp.id] = true;
      parseAndTriggerMentions(reply, groupId, emp.id);
      // AI 回复后，也让其他感兴趣的 AI 自主参与
      triggerInterestedAgents(groupId, emp.id, reply);
      // 轮询其他成员是否要补充回复
      pollAgentsForReply(groupId, emp.id, reply);
    }
    var chatResult = await _sendChatWaitForLifecycle(sessionKey, prompt, {
      onStream: function (text) {
        if (!hasSentWorking) {
          hasSentWorking = true;
          notifyOfficeStatusChange(emp.id, 'working');
        }
        var t = document.getElementById('mentionTyping_' + emp.id);
        if (t) t.querySelector('.msg-bubble').textContent = text + '...';
      },
      onStartThinking: function () {
        notifyOfficeStatusChange(emp.id, 'thinking');
        var _mse = findEmpByIdAcrossGroups(emp.id);
        if (_mse) {
          _mse.status = 'thinking';
          renderEmployeeList();
        }
      }
    }, { model: REQUEST_CHAT_MODEL });
    var finalReply = (chatResult.reply || '').trim();
    console.debug('[triggerMentionedAgent] ' + emp.name + ' lifecycle结束，回复长度=' + finalReply.length + '，是否错误=' + chatResult.isError + '，lifecycleEnded=' + chatResult.lifecycleEnded);
    if (!finalReply) {
      console.warn('[triggerMentionedAgent] ' + emp.name + ' 等待 lifecycle 后仍未获得内容');
    }
    _doHandle(finalReply, chatResult.isError);
    notifyOfficeStatusChange(emp.id, 'online');
    var _me = findEmpByIdAcrossGroups(emp.id);
    if (_me) {
      _me.status = 'online';
      renderEmployeeList();
    }
  } catch (e) {
    console.error('[MentionTrigger] 触发失败:', e);
    window._thinkingTimeouts = window._thinkingTimeouts || {};
    clearTimeout(window._thinkingTimeouts[emp.id]);
    delete window._thinkingTimeouts[emp.id];
    var t = document.getElementById('mentionTyping_' + emp.id);
    if (t) t.remove();
    notifyOfficeStatusChange(emp.id, 'online');
    var _me2 = findEmpByIdAcrossGroups(emp.id);
    if (_me2) {
      _me2.status = 'online';
      renderEmployeeList();
    }
  }
  mentionChainDepth--;
}
// 串行锁包装 triggerMentionedAgent，确保同一 groupId 同一时间只有一个 AI 在链式触发中回复
function triggerMentionedAgent(emp, groupId, fromId, source, userMessage) {
  _runWithGroupReplyLock(groupId, function () {
    return _triggerMentionedAgent(emp, groupId, fromId, source, userMessage);
  });
}

// 在群聊中显示 AI 回复
function displayGroupAIReply(groupId, senderId, replyText, chainDepth) {
  chainDepth = chainDepth || 0;
  console.debug('[displayGroupAIReply] 被调用 groupId=' + groupId + ' senderId=' + senderId + ' chainDepth=' + chainDepth + ' 文本长度=' + (replyText || '').length + ' 文本前100字:', (replyText || '').substring(0, 100));
  // 如果用户已切换出群聊，不再往当前 DOM 追加消息，避免串渲染
  if (currentGroupId !== groupId) {
    console.debug('[displayGroupAIReply] 当前不在该群聊，跳过渲染 currentGroupId=' + currentGroupId);
    return;
  }

  // 最终防重：5 秒内同一个 AI 对同一内容不重复输出
  var dedupKey = groupId + '_' + senderId + '_' + (replyText || '').substring(0, 50);
  window._groupReplyDedup = window._groupReplyDedup || {};
  var lastDedup = window._groupReplyDedup[dedupKey];
  if (lastDedup && Date.now() - lastDedup < 5000) {
    console.debug('[displayGroupAIReply] 5秒内重复消息防重命中，跳过:', dedupKey);
    return;
  }
  window._groupReplyDedup[dedupKey] = Date.now();

  var area = document.getElementById('messagesArea');
  var senderEmp = findEmpByIdAcrossGroups(senderId);
  var avatarHtml = senderEmp ? renderAvatar(senderEmp, 32) : '🤖';
  var senderName = senderEmp ? senderEmp.name : 'AI';
  var senderRole = senderEmp ? getEmpRoleDisplay(senderEmp) : '';
  var senderBg = senderEmp ? 'background:' + senderEmp.bg + ';' : 'background:var(--bg-tertiary);';
  var timeStr = formatDate();
  var cleanText = replyText || '';
  var bubbleContent = highlightMentions(formatMessageContent(cleanText));

  // DOM 去重：检查最近 3 条消息是否已有相同 sender + content，防止竞态导致重复渲染
  var recentMsgs = area.querySelectorAll('.msg');
  var isDuplicateDom = false;
  for (var i = recentMsgs.length - 1, count = 0; i >= 0 && count < 3; i--, count++) {
    var m = recentMsgs[i];
    var nameEl = m.querySelector('.msg-sender-name');
    var bubbleEl = m.querySelector('.msg-bubble');
    if (nameEl && bubbleEl && nameEl.textContent === senderName && bubbleEl.textContent === cleanText) {
      isDuplicateDom = true;
      console.debug('[displayGroupAIReply] DOM 去重命中，跳过重复消息:', senderName);
      break;
    }
  }

  if (isDuplicateDom) {
    // 已是重复消息，跳过插入、保存及后续链式触发
    return;
  }

  var html = '<div class="msg"><div class="msg-avatar"><div class="avatar small" style="' + senderBg + '">' + avatarHtml + '</div></div>' + '<div class="msg-content">' + '<div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(senderName) + '</span><span class="msg-sender-role">' + escapeHtml(senderRole) + '</span><span class="msg-sender-time">' + timeStr + '</span></div>' + '<div class="msg-bubble">' + bubbleContent + '</div>' + '</div></div>';
  area.insertAdjacentHTML('beforeend', html);
  area.scrollTop = area.scrollHeight;

  var aiMsgId = 'msg_ai_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);

  // 同步到后端，所有人共享群聊记录
  apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history', {
    method: 'POST',
    body: JSON.stringify({
      id: aiMsgId,
      role: 'assistant',
      content: replyText,
      senderId: senderId,
      senderName: senderName,
      senderType: 'agent',
      groupId: groupId,
      time: Date.now()
    })
  }).catch(function (err) {
    console.debug('[displayGroupAIReply] 同步后端失败:', err);
  });

  // 群聊 AI 回复只触发个人记忆提取；项目组公共记忆只在用户发消息时提取一次，避免每个 AI 回复都重复提取群记忆造成 409 冲突和 OpenClaw 调用浪费
  setTimeout(function () {
    extractAndSaveMemory(senderId, '', replyText, true, aiMsgId);
  }, 800);

  // 更新群组最后消息
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (group) {
    group.lastMsg = replyText.substring(0, 20);
    saveGroups();
    // 只更新侧栏群组的最后消息预览，不重新渲染整个员工列表
    var groupItem = document.querySelector('.list-item[data-group="' + groupId + '"]');
    if (groupItem) {
      var previewEl = groupItem.querySelector('.group-item-sub');
      if (previewEl) previewEl.textContent = group.lastMsg;
    }
  }

  // 注意：@提及触发统一在 _doHandle 中处理（已重置 mentionTriggered，避免重复/遗漏）
}

// ========== 创建群组向导 ==========
var GROUP_EMOJIS = ['🚀', '🎨', '💡', '🔧', '📊', '🎯', '⚡', '🔥', '🌈', '🎮', '🤖', '📱', '🌐', '🏗️', '📦', '🔔'];
function openGroupWizard() {
  if (!hasModulePermission('groups')) {
    showToast('⛔ 你没有项目组管理权限', 'warning');
    return;
  }
  /* 〔r39-17④ 防呆〕 已打开则跳过 — 防「关不掉」重开竞态
     (重复触发会把用户刚关掉的向导复位重弹; 手动按钮在向导已开时重复点也无害) */
  var _gwOverlay0 = document.getElementById('groupWizardOverlay');
  if (_gwOverlay0 && _gwOverlay0.classList.contains('show')) return;
  gwStep = 1;
  gwEmoji = '🚀';
  gwMembers = [];
  gwLead = '';
  gwEditGroupId = null;
  // 渲染 emoji 选择器
  var emojiGrid = document.getElementById('gwEmojiGrid');
  if (emojiGrid) {
    emojiGrid.innerHTML = GROUP_EMOJIS.map(function (e) {
      var sel = e === gwEmoji ? ' selected' : '';
      return '<div class="emoji-opt' + sel + '" onclick="selectGwEmoji(\'' + escapeAttr(e) + '\', this)">' + escapeHtml(e) + '</div>';
    }).join('');
  }
  // 清空输入
  var nameInput = document.getElementById('gwName');
  if (nameInput) nameInput.value = '';
  renderGroupWizardUI();
  document.getElementById('groupWizardOverlay').classList.add('show');
}
function closeGroupWizard() {
  document.getElementById('groupWizardOverlay').classList.remove('show');
}
function gwNext() {
  if (gwStep === 1) {
    var name = document.getElementById('gwName').value.trim();
    if (!name) {
      showToast('请输入群组名称', 'warning');
      return;
    }
  }
  if (gwStep === 2) {
    if (gwMembers.length === 0) {
      showToast('请至少选择一个成员', 'warning');
      return;
    }
    if (!gwLead) {
      gwLead = gwMembers[0];
    } // 默认第一个成员为组长
    renderGroupWizardStep3();
  }
  if (gwStep < 3) {
    gwStep++;
    renderGroupWizardUI();
  } else {
    createGroup();
  }
}
function gwPrev() {
  if (gwStep > 1) {
    gwStep--;
    renderGroupWizardUI();
  }
}
function renderGroupWizardUI() {
  var stepsHtml = '';
  for (var i = 1; i <= 3; i++) {
    var cls = i < gwStep ? ' done' : i === gwStep ? ' active' : '';
    stepsHtml += '<div class="gw-step' + cls + '">' + i + '</div>';
    if (i < 3) stepsHtml += '<div class="gw-step-line' + (i < gwStep ? ' done' : '') + '"></div>';
  }
  document.getElementById('gwSteps').innerHTML = stepsHtml;

  // Step content
  document.getElementById('gwStep1').style.display = gwStep === 1 ? 'block' : 'none';
  document.getElementById('gwStep2').style.display = gwStep === 2 ? 'block' : 'none';
  document.getElementById('gwStep3').style.display = gwStep === 3 ? 'block' : 'none';

  // Buttons
  document.getElementById('gwPrevBtn').style.display = gwStep > 1 ? 'inline-block' : 'none';
  document.getElementById('gwNextBtn').textContent = gwStep === 3 ? '✨ 创建群组' : '下一步 →';
  if (gwStep === 2) renderGroupWizardStep2();
  if (gwStep === 3) renderGroupWizardStep3();
}
function selectGwEmoji(emoji, el) {
  gwEmoji = emoji;
  document.querySelectorAll('.emoji-opt').forEach(function (o) {
    o.classList.remove('selected');
  });
  el.classList.add('selected');
}
function toggleGwMember(empId) {
  var idx = gwMembers.indexOf(empId);
  if (idx >= 0) {
    gwMembers.splice(idx, 1);
    if (gwLead === empId) {
      gwLead = gwMembers.length > 0 ? gwMembers[0] : '';
    }
  } else {
    gwMembers.push(empId);
    if (!gwLead) gwLead = empId;
  }
  renderGroupWizardStep2();
}
function selectGwLead(empId) {
  gwLead = empId;
  document.querySelectorAll('.lead-radio-item').forEach(function (i) {
    i.classList.remove('selected');
  });
  var item = document.querySelector('.lead-radio-item[data-lead="' + empId + '"]');
  if (item) item.classList.add('selected');
}
function renderGroupWizardStep2() {
  var container = document.getElementById('gwMembersGrid');
  if (!container) return;
  container.innerHTML = emps.filter(function (e) {
    return !e.archived;
  }).map(function (e) {
    var selected = gwMembers.indexOf(e.id) >= 0;
    return '<div class="member-check-item' + (selected ? ' selected' : '') + '" onclick="toggleGwMember(\'' + escapeAttr(e.id) + '\')">' + '<span class="check-mark">✓</span>' + '<div class="avatar small" style="background:' + escapeAttr(e.bg || '') + ';width:24px;height:24px;font-size:12px;">' + renderAvatar(e, 24) + '</div>' + '<span>' + escapeHtml(e.name) + '</span>' + '</div>';
  }).join('');
}
function renderGroupWizardStep3() {
  var container = document.getElementById('gwLeadList');
  if (!container) return;

  // Lead selection
  container.innerHTML = gwMembers.map(function (mid) {
    var e = emps.find(function (x) {
      return x.id === mid;
    });
    if (!e) return '';
    var isSelected = gwLead === mid;
    return '<div class="lead-radio-item' + (isSelected ? ' selected' : '') + '" data-lead="' + escapeAttr(mid) + '" onclick="selectGwLead(\'' + escapeAttr(mid) + '\')">' + '<div class="lead-radio-dot"></div>' + '<div class="avatar small" style="background:' + escapeAttr(e.bg || '') + ';width:28px;height:28px;font-size:13px;">' + renderAvatar(e, 28) + '</div>' + '<span style="font-size:14px;">' + escapeHtml(e.name) + '</span>' + '<span style="font-size:12px;color:var(--text-secondary);">' + escapeHtml(getEmpRoleDisplay(e)) + '</span>' + '</div>';
  }).join('');

  // Confirm info
  var nameEl = document.getElementById('gwConfirmName');
  var membersEl = document.getElementById('gwConfirmMembers');
  var leadEl = document.getElementById('gwConfirmLead');
  var name = document.getElementById('gwName') ? document.getElementById('gwName').value.trim() : '';
  if (nameEl) nameEl.textContent = gwEmoji + ' ' + name;
  if (membersEl) {
    var memberNames = gwMembers.map(function (mid) {
      var e = emps.find(function (x) {
        return x.id === mid;
      });
      return e ? e.name : mid;
    }).join('、');
    membersEl.textContent = memberNames;
  }
  if (leadEl) {
    var leadEmp = findEmpByIdAcrossGroups(gwLead);
    leadEl.textContent = leadEmp ? leadEmp.name : gwLead;
  }
}
async function createGroup() {
  var name = document.getElementById('gwName').value.trim();
  if (!name) {
    showToast('请输入群组名称', 'warning');
    return;
  }
  if (gwMembers.length === 0) {
    showToast('请至少选择一个成员', 'warning');
    return;
  }
  if (gwEditGroupId) {
    // Editing existing group
    var existingGroup = groups.find(function (g) {
      return g.id === gwEditGroupId;
    });
    if (existingGroup) {
      existingGroup.name = name;
      existingGroup.emoji = gwEmoji;
      existingGroup.members = gwMembers.slice();
      existingGroup.leadAgentId = gwLead;
      // 保留原有的 announcement
      await saveGroups();
      renderEmployeeList();
      closeGroupWizard();
      gwEditGroupId = null;
      showToast('✅ 群组「' + name + '」已更新！', 'success');
      setTimeout(function () {
        openGroupChat(existingGroup.id);
      }, 300);
    }
  } else {
    // Creating new group
    var newGroup = {
      id: 'grp_' + Date.now(),
      name: name,
      emoji: gwEmoji,
      bg: '#5856D6',
      members: gwMembers.slice(),
      leadAgentId: gwLead,
      lastMsg: '',
      createdBy: currentUser && currentUser.id || 'local',
      createdByName: currentUser && currentUser.name || '本地用户',
      createdAt: Date.now()
    };

    // 如果有群公告，保存到群组
    var announcementEl = document.getElementById('gwAnnouncement');
    if (announcementEl && announcementEl.value.trim().length > 0) {
      newGroup.announcement = announcementEl.value.trim();
    }
    groups.push(newGroup);
    await saveGroups();
    renderEmployeeList();
    closeGroupWizard();
    showToast('✅ 群组「' + name + '」创建成功！', 'success');

    // 自动打开群聊（saveGroups() 已通过 PUT /api/groups 批量同步，无需再 POST 创建）
    setTimeout(function () {
      openGroupChat(newGroup.id);
    }, 300);
  }
}

// 编辑群公告 — 打开 Apple 风格模态弹窗
function editGroupAnnouncement() {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) {
    showToast('⚠️ 请先选择一个群组');
    return;
  }
  var overlay = document.getElementById('announcementModalOverlay');
  var input = document.getElementById('announcementModalInput');
  if (!overlay || !input) return;
  input.value = group.announcement || '';
  overlay.classList.add('open');
  setTimeout(function () { input.focus(); }, 100);
}

// 关闭群公告编辑器弹窗
function closeAnnouncementModal() {
  var overlay = document.getElementById('announcementModalOverlay');
  if (overlay) overlay.classList.remove('open');
}

// 从弹窗保存群公告
function saveAnnouncementFromModal() {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) return;
  var input = document.getElementById('announcementModalInput');
  if (!input) return;
  var newAnnouncement = input.value.trim();
  group.announcement = newAnnouncement;

  // 保存到本地
  saveGroups();

  // 同步到后端
  var token = localStorage.getItem('sb_auth_token');
  if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
    apiFetch('/api/groups/' + group.id, {
      method: 'PUT',
      body: JSON.stringify({
        announcement: group.announcement
      })
    }).then(function (res) {
      if (res && res.ok) {
        showToast('✅ 群公告已同步到服务器');
      }
    }).catch(function (err) {
      console.warn('[Announcement] sync failed:', err);
    });
  }

  // 更新UI
  var announcementEl = document.getElementById('groupAnnouncement');
  var announcementContent = document.getElementById('announcementContent');
  if (announcementEl && announcementContent) {
    if (group.announcement && group.announcement.length > 0) {
      announcementContent.textContent = group.announcement;
    } else {
      announcementContent.textContent = '';
    }
  }
  closeAnnouncementModal();
  showToast('✅ 群公告已更新');

  // 通知所有 AI 成员群公告已更新
  if (group.members && group.members.length > 0 && newAnnouncement.length > 0) {
    // 兼容 members 的两种格式：字符串数组 和 字典数组
    group.members.forEach(function (m, idx) {
      var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
      var emp = emps.find(function (e) {
        return e.id === mid;
      });
      if (!emp) return;
      setTimeout(function () {
        try {
          var sessionKey = 'agent:' + emp.id + ':chat';
          var notifyPrompt = '你是' + emp.name + '。群公告已更新，请注意以下内容：\n\n' + newAnnouncement + '\n\n请在后续讨论中遵守此公告。';
          _sendChatWaitForLifecycle(sessionKey, notifyPrompt).catch(function () {});
        } catch (e) {}
      }, (idx + 1) * 800);
    });
  }
}

// 键盘快捷键：ESC 关闭，Cmd/Ctrl + Enter 保存/发送
document.addEventListener('keydown', function (e) {
  var annOverlay = document.getElementById('announcementModalOverlay');
  var urgeOverlay = document.getElementById('urgeModalOverlay');
  if (annOverlay && annOverlay.classList.contains('open')) {
    if (e.key === 'Escape') {
      closeAnnouncementModal();
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      saveAnnouncementFromModal();
    }
    return;
  }
  if (urgeOverlay && urgeOverlay.classList.contains('open')) {
    if (e.key === 'Escape') {
      closeUrgeModal();
    }
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      sendUrgeFromModal();
    }
  }
});

// 打开督促弹窗
function openUrgeModal() {
  var overlay = document.getElementById('urgeModalOverlay');
  var input = document.getElementById('urgeModalInput');
  if (!overlay || !input) return;
  input.value = '各位，请尽快处理手头任务，有问题及时沟通！';
  overlay.classList.add('open');
  setTimeout(function () {
    input.focus();
    input.select();
  }, 50);
}

// 关闭督促弹窗
function closeUrgeModal() {
  var overlay = document.getElementById('urgeModalOverlay');
  if (overlay) overlay.classList.remove('open');
}

// 从弹窗发送督促消息
function sendUrgeFromModal() {
  var input = document.getElementById('urgeModalInput');
  if (!input) return;
  var urgeText = input.value || '';
  if (!urgeText.trim()) {
    showToast('⚠️ 督促内容不能为空');
    return;
  }
  closeUrgeModal();
  _doSendUrgeMessage(urgeText.trim());
}

// 实际发送督促消息（与弹窗解耦，便于复用）
function _doSendUrgeMessage(urgeText) {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) {
    showToast('⚠️ 请先进入一个群组');
    return;
  }
  var leadEmp = findEmpByIdAcrossGroups(group.leadAgentId);
  if (!leadEmp) {
    showToast('⚠️ 群组没有设置群主');
    return;
  }

  // 构建 @所有人 的消息
  var fullMessage = '@所有人 ' + urgeText;

  // 显示到聊天区
  displayGroupAIReply(group.id, group.leadAgentId, fullMessage);

  // 触发被 @ 的 AI 回复（除了群主自己）
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var validMembers = group.members.map(function (m) {
    return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
  }).filter(function (id) { return id; }).filter(function (mid) {
    return !!findEmpByIdAcrossGroups(mid) && mid !== group.leadAgentId;
  });
  validMembers.forEach(function (mid, idx) {
    var emp = findEmpByIdAcrossGroups(mid);
    if (!emp) return;
    setTimeout(function () {
      triggerMentionedAgent(emp, group.id, group.leadAgentId, 'ai', fullMessage);
    }, (idx + 1) * 2000);
  });
  showToast('🔔 督促消息已发送');
}

// 督促群成员 — 群主发送 @所有人 的督促消息
function urgeGroupMembers() {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) {
    showToast('⚠️ 请先进入一个群组');
    return;
  }
  var leadEmp = findEmpByIdAcrossGroups(group.leadAgentId);
  if (!leadEmp) {
    showToast('⚠️ 群组没有设置群主');
    return;
  }
  openUrgeModal();
}

// 归档群聊 — 保存当前聊天记录到归档列表
async function archiveGroupChat() {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) {
    showToast('⚠️ 请先进入一个群组');
    return;
  }
  var history = [];
  try {
    var resp = await apiFetch('/api/groups/' + encodeURIComponent(group.id) + '/history');
    var data = await resp.json();
    history = (data && data.messages) || [];
  } catch (e) {
    history = [];
  }
  if (history.length === 0) {
    showToast('⚠️ 当前群聊没有消息可归档');
    return;
  }

  // 保存到归档列表
  var archives = JSON.parse(localStorage.getItem('sb_group_archives') || '[]');
  var archiveItem = {
    id: 'arc_' + Date.now(),
    groupId: group.id,
    groupName: group.name,
    groupEmoji: group.emoji || '👥',
    messageCount: history.length,
    archivedAt: Date.now(),
    preview: history[history.length - 1] ? history[history.length - 1].content.substring(0, 50) : '',
    messages: history.slice()
  };
  archives.unshift(archiveItem);
  localStorage.setItem('sb_group_archives', JSON.stringify(archives.slice(0, 50)));
  showToast('📦 群聊「' + group.name + '」已归档（' + history.length + '条消息）');
}

// 重置群聊 — 清空当前群聊的聊天记录
async function resetGroupChat() {
  var group = groups.find(function (g) {
    return g.id === currentGroupId;
  });
  if (!group) {
    showToast('⚠️ 请先进入一个群组');
    return;
  }
  var history = [];
  try {
    var resp = await apiFetch('/api/groups/' + encodeURIComponent(group.id) + '/history');
    var data = await resp.json();
    history = (data && data.messages) || [];
  } catch (e) {
    history = [];
  }
  if (history.length === 0) {
    showToast('⚠️ 当前群聊已经是空的');
    return;
  }
  if (!confirm('确定要清空群聊「' + group.name + '」的所有 ' + history.length + ' 条消息吗？此操作不可恢复。')) {
    return;
  }

  // 清空消息区
  var area = document.getElementById('messagesArea');
  if (area) {
    area.innerHTML = getChatEmptyStateHtml();
  }

  // 更新群组最后消息
  group.lastMsg = '';
  saveGroups();
  renderEmployeeList();
  showToast('🔄 群聊「' + group.name + '」已重置');
}

// 更新督促状态按钮显示
function updateSupervisorStatusButton() {
  var projId = getCurrentProjectId();
  if (!projId) return;
  var btn = document.querySelector('.header-btn[onclick="openSupervisorPanel()"]');
  if (!btn) return;
  var config = supervisorConfig[projId];
  if (config && config.enabled !== 'off') {
    btn.style.background = 'var(--accent)';
    btn.style.color = 'white';
  } else {
    btn.style.background = '';
    btn.style.color = '';
  }
}

// Render Messages
function renderMsgs(type) {
  const area = document.getElementById('messagesArea');
  if (type === 'private') {
    const empId = localStorage.getItem('sb_current_emp');
    if (!empId) {
      area.innerHTML = getChatEmptyStateHtml();
      return;
    }
    const emp = emps.find(function (e) {
      return e.id === empId;
    });
    if (!emp) {
      localStorage.removeItem('sb_current_emp');
      area.innerHTML = getChatEmptyStateHtml();
      return;
    }

    // 异步从后端加载个人聊天记录
    loadChatFromServer(empId, 'personal', function (serverMsgs) {
      if (localStorage.getItem('sb_current_emp') !== empId) {
        console.warn('[renderMsgs] 员工已切换，丢弃旧回调:', empId);
        return;
      }
      // ★ fix/talent-dedup-ocr-name-priority: 检测后端返回的 dedup name 冲突, 弹 toast 提示用户
      //   后端在 ai_message 塞 dedup_name_conflict {conflict:True, existing_name, ocr_name, message}
      //   阻断自动 PUT 后让前端知道, 用户手动确认 (跳到达人档案页对比 / 重命名)
      (serverMsgs || []).forEach(function (m) {
        if (m && m.dedup_name_conflict && m.dedup_name_conflict.message) {
          if (typeof showToast === 'function') {
            showToast(m.dedup_name_conflict.message, 'error', { duration: 8000 });
          } else {
            console.warn('[dedup-name-conflict]', m.dedup_name_conflict);
          }
        }
      });
      // 严格过滤：后端无法区分个人/群聊时，只保留 role 为 user/assistant 且不含 groupId 的消息
      var privateMsgs = (serverMsgs || []).filter(function (m) {
        return (m.role === 'user' || m.role === 'assistant') && !m.groupId;
      });
      if (privateMsgs.length > 0) {
        renderPrivateMsgs(area, emp, privateMsgs);
      } else {
        area.innerHTML = getChatEmptyStateHtml();
      }
    });
    return;
  }
  function renderPrivateMsgs(area, emp, history) {
    // 直接清空重绘，不保留任何 DOM 残留消息（避免切换员工时串聊）
    // sendMsg 已同步写入 localStorage，renderMsgs 加载时要么从后端取（含用户消息），
    // 要么回退 localStorage（也含用户消息），无需额外保留 DOM 中的消息。
    // 防御性过滤：只渲染 role 为 user/assistant 且不含 groupId 的个人消息
    var filtered = history.filter(function (m) {
      return (m.role === 'user' || m.role === 'assistant') && !m.groupId;
    });
    if (filtered.length === 0) {
      area.innerHTML = getChatEmptyStateHtml();
      return;
    }
    // ④ 2-pass: 先算 consecutive + isLastInGroup 标志
    // 规则: 上一条同 role + 间隔 < 2 分钟 → current 是 consecutive(middle)
    //       下一条开始新组时, current 是 lastInGroup
    var groupFlags = filtered.map(function () { return { consecutive: false, lastInGroup: false }; });
    for (var gi = 0; gi < filtered.length; gi++) {
      var m = filtered[gi];
      var prev = gi > 0 ? filtered[gi - 1] : null;
      var sameAsPrev = prev && prev.role === m.role && m.time && prev.time && (Math.abs(new Date(m.time) - new Date(prev.time)) / 60000) < 2;
      if (sameAsPrev) groupFlags[gi].consecutive = true;
    }
    for (var gj = 0; gj < filtered.length; gj++) {
      var mj = filtered[gj];
      var next = gj + 1 < filtered.length ? filtered[gj + 1] : null;
      var nextStartsNew = !next || next.role !== mj.role || !mj.time || !next.time || (Math.abs(new Date(next.time) - new Date(mj.time)) / 60000) >= 2;
      if (nextStartsNew) groupFlags[gj].lastInGroup = true;
    }
    var typingMsg = document.getElementById('typingMsg');
    var prevMsgTime = null;
    area.innerHTML = filtered.map(function (m, idx) {
      const own = m.role === 'user';
      const timeStr = formatDate(m.time);
      // ② 智能时间分隔: 跨日/30 分钟间隔
      var sepHtml = formatChatTimeSeparator(prevMsgTime, m.time);
      prevMsgTime = m.time;
      // 缓存消息数据供右键「引用」取数
      _registerMsgDom(null, m);
      var quoteHtml2 = m.reply_to_message ? buildQuoteBlockHtml(m.reply_to_message) : '';
      var imagesHtml2 = own ? buildChatImagesHtml(m.images) : '';
      var citationHtml2 = '';
      if (!own && m.citations && m.citations.length > 0) {
        var citeId2 = 'citations_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);
        var citeItems2 = m.citations.map(function(c) {
          var icon = c.type === 'product' ? '📦' : '📄';
          return '<span class="citation-tag" onclick="event.stopPropagation();showCitationDetail(\'' + (c.id || '') + '\', \'' + escapeHtml(c.title || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\', \'' + (c.type || 'doc') + '\')">' + icon + ' ' + escapeHtml(c.title || '未命名') + '</span>';
        }).join('');
        citationHtml2 = '<div class="citation-bar" onclick="toggleCitationPanel(\'' + citeId2 + '\')">📚 引用了 ' + m.citations.length + ' 条知识 <span class="citation-toggle">▼</span></div>' +
          '<div class="citation-panel hidden" id="' + citeId2 + '">' + citeItems2 + '</div>';
      }
      // 〔r81 批注①〕MS3 召回 chip 条（历史消息，气泡上方；复用 builder，无数据不渲染）
      var injChipsHtml2 = (!own && m.injectionTags) ? buildInjectionChipsHtml(m.injectionTags) : '';
      // ④ 连续消息合并: 跳过 avatar + sender 行; 时间只在 lastInGroup 显示
      var isConsecutive = groupFlags[idx].consecutive;
      var isLastInGroup = groupFlags[idx].lastInGroup;
      var msgClasses = 'msg ' + (own ? 'own' : '') + (isConsecutive ? ' consecutive' : '') + (isLastInGroup ? ' last-in-group' : '');
      var avatarHtml = (!own && !isConsecutive) ? '<div class="msg-avatar">' + renderAvatar(emp, 32) + '</div>' : '';
      var senderHtml = '';
      if (!isConsecutive) {
        // 开头消息: 完整 sender 行
        if (!own) {
          senderHtml = '<div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(emp && emp.name || 'AI') + '</span><span class="msg-sender-role">' + escapeHtml(getEmpRoleDisplay(emp) || '') + (isLastInGroup ? '<span class="msg-sender-time">' + timeStr + '</span>' : '') + '</div>';
        } else {
          senderHtml = '<div class="msg-sender"><span class="msg-sender-name">你</span>' + (isLastInGroup ? '<span class="msg-sender-time">' + timeStr + '</span>' : '') + '</div>';
        }
      } else if (isLastInGroup) {
        // consecutive + lastInGroup: 只显示时间(无 sender name, 无 role)
        var timeOnlyHtml = '<div class="msg-sender msg-sender-time-only"><span class="msg-sender-time">' + timeStr + '</span></div>';
        senderHtml = timeOnlyHtml;
      }
      return sepHtml + '<div class="' + msgClasses.trim() + '" data-msg-id="' + escapeAttr(m.id || '') + '">' + avatarHtml + '<div class="msg-content">' + senderHtml + injChipsHtml2 + '<div class="msg-bubble">' + quoteHtml2 + formatMessageContent(m.content) + '</div>' + citationHtml2 + imagesHtml2 + '<div class="msg-hover-actions" data-msg-id="' + escapeAttr(m.id || '') + '">' + '<button class="msg-hover-btn" data-action="copy" title="复制">📋</button>' + '<button class="msg-hover-btn" data-action="quote" title="引用">💬</button>' + (own ? '<button class="msg-hover-btn" data-action="resend" title="重发">🔄</button>' : '') + '<button class="msg-hover-btn danger" data-action="delete" title="删除">🗑</button>' + '</div>' + '</div></div>';
    }).join('') + '<div class="typing-indicator" id="typingIndicator" style="display:none;"><div class="typing-dots"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>';
    if (typingMsg) {
      area.appendChild(typingMsg);
    }
    area.scrollTop = area.scrollHeight;
  }
  // 消息区域：不显示任何硬编码 demo 消息
  const projMsgs = [];
  if (projMsgs.length === 0) {
    area.innerHTML = getChatEmptyStateHtml();
    return;
  }
  area.innerHTML = '<div class="time-separator"><span>今天 09:30</span></div>' + projMsgs.map(function (m) {
    const e = emps.find(function (x) {
      return x.id === m.sender;
    });
    const own = m.sender === 'user';
    return '<div class="msg ' + (own ? 'own' : '') + '">' + (!own ? '<div class="msg-avatar">' + renderAvatar(e, 32) + '</div>' : '') + '<div class="msg-content">' + (!own ? '<div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(e && e.name || '') + '</span><span class="msg-sender-role">' + escapeHtml(getEmpRoleDisplay(e) || '') + '</span><span class="msg-sender-time">' + escapeHtml(m.time || '') + '</span></div>' : '<div class="msg-sender"><span class="msg-sender-name">你</span><span class="msg-sender-time">' + escapeHtml(m.time || '') + '</span></div>') + '<div class="msg-bubble">' + formatMessageContent(m.text) + '</div>' + '</div></div>';
  }).join('') + '<div class="typing-indicator" id="typingIndicator" style="display:none;"><div class="typing-dots"><div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div></div></div>';
  area.scrollTop = area.scrollHeight;
}

// ============ 聊天区空状态 - 欢迎态 ============
function getChatEmptyStateHtml() {
  // AI 员工卡片：只列 AI 员工（isAIEmp 判定，真人员工如 Ray 不出现），渐变描边 + AI 徽章
  // ★ fix/sidebar-welcome-minor: 拆出 allAi 数组,保留 aiEmps(0,4) 渲染前 4 张卡片,
  // 超出部分用 +N 占位卡提示,并在点击时聚焦侧栏员工搜索框,形成"还有 N 位 → 立刻能搜到"闭环
  var allAi = (typeof emps !== 'undefined' ? emps : []).filter(function (e) { return e && !e.isProject && isAIEmp(e); });
  var aiEmps = allAi.slice(0, 4);
  var overflowCount = allAi.length - 4;  // <=0 表示不溢出
  // ③ 0 员工分支: 欢迎页不显 4 卡片 + 3 prompt chips(没有员工, prompt 没上下文)
  // 改为: 引导用户先创建 AI 员工, 创建后再回来就能看到员工卡片
  if (aiEmps.length === 0) {
    return '<div class="chat-empty-state chat-empty-state-no-emp">'
      + '<div class="welcome-emp-avatar-wrap welcome-emp-avatar-wrap-large"><div class="welcome-emp-ring welcome-emp-ring-large">🦞</div></div>'
      + '<div class="welcome-title">先创建一位 AI 员工开始协作</div>'
      + '<div class="welcome-prompt-hint welcome-prompt-hint-block">AI 员工可以帮你分析达人、录入商品、跟进合作。创建后这里会显示员工卡片和对话入口。</div>'
      + '<div><button class="welcome-cta" onclick="onWelcomeNewChat()">+ 创建 AI 员工</button></div>'
      + '</div>';
  }
  var empRow = '';
  if (aiEmps.length) {
    empRow = '<div class="welcome-emp-row">' + aiEmps.map(function (e) {
      var desc = (e.tagline || '').trim() || (e.description || '').trim() || getEmpRoleDisplay(e) || 'AI 协作员工';
      return '<div class="welcome-emp-card" onclick="onWelcomeEmpChat(\'' + escapeAttr(e.id) + '\')">'
        + '<div class="welcome-emp-avatar-wrap"><div class="welcome-emp-ring">' + renderAvatar(e, 56) + '</div><div class="welcome-ai-badge">AI</div></div>'
        + '<div class="welcome-emp-name">' + escapeHtml(e.name || '') + '</div>'
        + '<div class="welcome-emp-desc">' + escapeHtml(desc) + '</div>'
        + '</div>';
    }).join('')
      + (overflowCount > 0
        ? '<div class="welcome-emp-card welcome-emp-more" onclick="var el=document.getElementById(\'sidebarEmpSearch\');if(el){el.focus();el.scrollIntoView({block:\'center\',behavior:\'smooth\'});}">'
          + '<div class="welcome-emp-more-num">+' + overflowCount + '</div>'
          + '<div class="welcome-emp-more-desc">还有 ' + overflowCount + ' 位员工</div>'
          + '</div>'
        : '')
      + '</div>';
  }
  var prompts = ['帮我分析一位达人的带货数据', '筛选粉丝 50 万以上的服饰达人', '汇总这周的达人合作进展'];
  var chips = prompts.map(function (p) {
    return '<button class="welcome-prompt-chip" onclick="onWelcomePrompt(\'' + escapeAttr(p) + '\')">' + escapeHtml(p) + '</button>';
  }).join('');
  return '<div class="chat-empty-state">'
    + '<div class="welcome-title">挑一位 AI 员工开始协作吧</div>'
    + empRow
    + '<div class="welcome-prompt-hint">不知道怎么开口？可以直接这样问：</div>'
    + '<div class="welcome-prompt-chips">' + chips + '</div>'
    + '<div><button class="welcome-cta" onclick="onWelcomeNewChat()">新建对话</button></div>'
    + '<div class="welcome-links"><button class="welcome-link" onclick="onWelcomeTalentCreate()">达人录入</button><button class="welcome-link" onclick="onWelcomeTalents()">达人搜索</button></div>'
    + '</div>';
}

// 欢迎页：点击 AI 员工头像直接进入单聊
function onWelcomeEmpChat(empId) {
  switchModule('messages');
  setTimeout(function () { openChat(empId); }, 50);
}

// 欢迎页：示例 prompt 填入输入框，教育用户怎么开口
function onWelcomePrompt(text) {
  switchModule('messages');
  setTimeout(function () {
    var input = document.getElementById('msgInput');
    if (input) {
      input.value = text;
      input.focus();
      if (typeof handleInput === 'function') handleInput();
    }
  }, 50);
}

// ========== ① 消息 hover 操作栏事件委托 ==========
// 单一 handler 通过事件委托挂到 messagesArea, 处理 4 个 action: copy/quote/resend/delete
// 触屏兼容: touchstart 后 0.5s 显示, touchend 隐藏(模拟 hover)
(function bindMsgHoverActions() {
  var area = null;
  function ensure() { if (!area) area = document.getElementById('messagesArea'); return area; }
  // 长按触发(touchstart 0.5s 后)
  var longPressTimer = null;
  var lastTouchTarget = null;
  function findMsg(el) { while (el && el !== area) { if (el.classList && el.classList.contains('msg')) return el; el = el.parentNode; } return null; }
  function hideAll() { var menus = document.querySelectorAll('.msg-hover-actions.show'); for (var i = 0; i < menus.length; i++) menus[i].classList.remove('show'); }
  function showActions(msg) { var a = msg.querySelector('.msg-hover-actions'); if (a) a.classList.add('show'); }
  function getMsgContent(msg) {
    var b = msg.querySelector('.msg-bubble');
    if (!b) return '';
    return (b.innerText || b.textContent || '').replace(/\s+/g, ' ').trim();
  }
  function copyMessage(msg) {
    var text = getMsgContent(msg);
    if (!text) { showToast('⚠️ 消息内容为空'); return; }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { showToast('✅ 已复制'); }).catch(function () { showToast('❌ 复制失败'); });
    } else { showToast('❌ 当前浏览器不支持复制'); }
  }
  function quoteMessage(msg) {
    var text = getMsgContent(msg);
    if (!text) { showToast('⚠️ 消息内容为空'); return; }
    var input = document.getElementById('msgInput');
    if (input) {
      var truncated = text.length > 200 ? text.slice(0, 200) + '…' : text;
      input.value = '> ' + truncated + '\n\n';
      input.focus();
      if (typeof handleInput === 'function') handleInput();
    }
  }
  function resendMessage(msg) {
    var id = msg.getAttribute('data-msg-id');
    if (!id) { showToast('⚠️ 消息缺少 id'); return; }
    var btn = msg.querySelector('.msg-hover-btn[data-action="resend"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳'; }
    setTimeout(function () {
      if (btn) { btn.disabled = false; btn.textContent = '🔄'; }
      showToast('🔄 已触发重发(占位)');
    }, 800);
  }
  function deleteMessage(msg) {
    var id = msg.getAttribute('data-msg-id');
    if (!id) { showToast('⚠️ 消息缺少 id'); return; }
    // 设置 msgCtxTarget 给现有 msgCtxAction('delete') 用(避免复制右击逻辑)
    msgCtxTarget = {
      element: msg,
      isAI: !msg.classList.contains('own')
    };
    if (typeof msgCtxAction === 'function') msgCtxAction('delete');
  }
  // ③ 失败消息重试: 用缓存的 text/images 重新发, 失败时不重入 sendMsg(避免双发)
  function retryMessage(msg) {
    if (!msg || msg.getAttribute('data-status') !== 'failed') return;
    var text = msg.getAttribute('data-pending-text') || '';
    var imagesJson = msg.getAttribute('data-pending-images') || '[]';
    var images = [];
    try { images = JSON.parse(imagesJson) || []; } catch (e) { images = []; }
    // 标回 sending + 移除重试按钮
    msg.setAttribute('data-status', 'sending');
    var rb = msg.querySelector('.msg-retry-btn');
    if (rb) rb.remove();
    var empId = localStorage.getItem('sb_current_emp');
    if (!empId) { showToast('❌ 无法确定当前聊天员工'); msg.setAttribute('data-status', 'failed'); return; }
    // 只重发"保存到服务器"那一步(完整重发要走 sendMsg 全流程太重, 失败常见原因是网络抖动,
    // 重试保存后 AI 路由由 server-side job 接管)
    if (typeof apiFetch !== 'function') {
      showToast('❌ apiFetch 未就绪');
      msg.setAttribute('data-status', 'failed');
      return;
    }
    apiFetch('/api/chat/' + encodeURIComponent(empId), {
      method: 'POST',
      body: JSON.stringify({ role: 'user', content: text, skipAI: false, empId: empId, images: images.length ? images : undefined })
    }).then(function (resp) {
      if (resp && resp.ok) {
        setTimeout(function () { if (msg.getAttribute('data-status') === 'sending') msg.removeAttribute('data-status'); }, 1500);
        showToast('✅ 重试成功');
        // 触发后端 AI 流程(若 skipAI=false, 后端会自动调度, 不需要前端额外发 OpenClaw)
        if (typeof renderMsgs === 'function') setTimeout(function () { renderMsgs('private'); }, 500);
      } else {
        msg.setAttribute('data-status', 'failed');
        msg.setAttribute('data-fail-reason', (resp && resp.status) ? ('HTTP ' + resp.status) : '重试失败');
        var retryBtn2 = document.createElement('div');
        retryBtn2.className = 'msg-retry-btn';
        retryBtn2.textContent = '⚠️ 重试失败 · 再次点击';
        msg.appendChild(retryBtn2);
        showToast('❌ 重试失败');
      }
    }).catch(function (e) {
      msg.setAttribute('data-status', 'failed');
      var retryBtn3 = document.createElement('div');
      retryBtn3.className = 'msg-retry-btn';
      retryBtn3.textContent = '⚠️ 重试失败 · 再次点击';
      msg.appendChild(retryBtn3);
      showToast('❌ 重试失败: ' + ((e && e.message) || '网络错误'));
    });
  }
  function handle(action, msg) {
    if (action === 'copy') copyMessage(msg);
    else if (action === 'quote') quoteMessage(msg);
    else if (action === 'resend') resendMessage(msg);
    else if (action === 'delete') deleteMessage(msg);
  }
  document.addEventListener('DOMContentLoaded', function () {
    var a = ensure();
    if (!a) return;
    a.addEventListener('click', function (e) {
      var btn = e.target.closest && e.target.closest('.msg-hover-btn');
      if (!btn) return;
      var menu = btn.closest('.msg-hover-actions');
      var msg = menu ? menu.closest('.msg') : null;
      if (!msg) return;
      e.stopPropagation();
      handle(btn.getAttribute('data-action'), msg);
    });
    // 触屏长按
    a.addEventListener('touchstart', function (e) {
      var msg = findMsg(e.target);
      if (!msg) return;
      lastTouchTarget = msg;
      clearTimeout(longPressTimer);
      longPressTimer = setTimeout(function () { hideAll(); showActions(msg); }, 500);
    }, { passive: true });
    a.addEventListener('touchend', function () { clearTimeout(longPressTimer); }, { passive: true });
    a.addEventListener('touchmove', function () { clearTimeout(longPressTimer); }, { passive: true });
    // 点击空白处隐藏
    document.addEventListener('click', function (e) {
      if (!e.target.closest || !e.target.closest('.msg-hover-actions')) hideAll();
    });
  });
})();

// 员工列表异步加载完成后，若聊天区仍是欢迎空状态则重渲染（让员工卡片出现）
function refreshChatEmptyStateIfVisible() {
  var els = document.querySelectorAll('.chat-empty-state');
  if (!els.length) return;
  var html = getChatEmptyStateHtml();
  els.forEach(function (el) {
    var tmp = document.createElement('div');
    tmp.innerHTML = html;
    if (tmp.firstChild && el.parentNode) el.parentNode.replaceChild(tmp.firstChild, el);
  });
}

function onWelcomeNewChat() {
  switchModule('messages');
  setTimeout(function () { openWizard(); }, 50);
}

// 首页快捷操作：达人录入（非管理员/子账号也可用——录入接口带 Bearer token，归属当前用户子库）
function onWelcomeTalentCreate() {
  switchModule('influencers');
  setTimeout(function () { createNewTalent(); }, 50);
}

function onWelcomeTalents() {
  switchModule('influencers');
  setTimeout(function () {
    var el = document.getElementById('talentsMidSearch');
    if (el) el.focus();
  }, 50);
}

function renderEmptyChat() {
  var chatHeaderName = document.querySelector('.chat-header-name');
  var chatHeaderRole = document.querySelector('.chat-header-role');
  var chatHeaderAvatar = document.querySelector('.chat-header-v2 .avatar');
  if (chatHeaderName) chatHeaderName.textContent = '';
  if (chatHeaderRole) chatHeaderRole.textContent = '';
  if (chatHeaderAvatar) {
    chatHeaderAvatar.innerHTML = '🦞';
    chatHeaderAvatar.style.background = 'linear-gradient(135deg,#FF6B35,#E55A2B)';
    chatHeaderAvatar.style.display = 'none';  // 未选中会话时不显示默认龙虾头像（橙色圆底+在线点，空状态下突兀）
  }
  var messagesArea = document.getElementById('messagesArea');
  if (messagesArea) messagesArea.innerHTML = getChatEmptyStateHtml();
}

// ============ 模块空状态 - 欢迎态快捷入口 ============
function onWelcomeCreateProduct() {
  createNewProduct();
}

function onWelcomeViewProductData() {
  if ((_productData.total || 0) === 0) return;
  var first = _productData.products && _productData.products[0];
  if (first && first.id) {
    selectProductItem(first.id);
    switchProductTab('sales');
  }
}

function onWelcomeCreateTalent() {
  createNewTalent();
}

function onWelcomeSearchTalent() {
  var el = document.getElementById('talentsMidSearch');
  if (el) {
    el.focus();
    el.select();
  }
}

// ============ 群公告功能（文档链接版）============
const bulletinData = JSON.parse(localStorage.getItem('sb_bulletins') || '{}');
function showBulletin() {
  const proj = document.querySelector('.list-item[data-proj].active');
  if (!proj) {
    showToast('请先选择一个项目组', 'warning');
    return;
  }
  // 加载现有公告到表单
  const projId = proj.dataset.proj;
  const bulletin = bulletinData[projId];
  if (bulletin) {
    document.getElementById('bulletinTitle').value = bulletin.title || '';
    renderBulletinDocs(bulletin.docs || []);
  } else {
    document.getElementById('bulletinTitle').value = '';
    renderBulletinDocs([]);
  }
  document.getElementById('bulletinModal').classList.add('show');
}
function closeBulletin() {
  document.getElementById('bulletinModal').classList.remove('show');
  document.getElementById('bulletinTitle').value = '';
  document.getElementById('bulletinContent').value = '';
  renderBulletinDocs([]);
}

// 渲染文档列表
function renderBulletinDocs(docs) {
  const list = document.getElementById('bulletinDocsList');
  if (!list) return;
  if (docs.length === 0) {
    list.innerHTML = '<div style="text-align:center;color:var(--text-tertiary);padding:20px;font-size:13px;">暂无文档，点击上方添加</div>';
    return;
  }
  list.innerHTML = docs.map(function (doc, i) {
    return `<div class="bulletin-doc-item">
    <span class="bulletin-doc-icon">${doc.icon || '📄'}</span>
    <span class="bulletin-doc-name">${escapeHtml(doc.name || '')}</span>
    <span class="bulletin-doc-path">${escapeHtml(doc.path || '')}</span>
    <button class="bulletin-doc-delete" onclick="removeBulletinDoc(${i})">✕</button>
  </div>`;
  }).join('');
}

// 添加文档
function addBulletinDoc() {
  const name = prompt('文档名称（如：项目方案.md）：');
  if (!name) return;
  const path = prompt('文档路径（如：/docs/plan.md）：');
  if (!path) return;
  const icon = prompt('图标（默认📄，可选📋📄📝📊🖼️🎨）：') || '📄';
  const docsList = document.getElementById('bulletinDocsList');
  const docs = window._currentBulletinDocs || [];
  docs.push({
    name,
    path,
    icon
  });
  window._currentBulletinDocs = docs;
  renderBulletinDocs(docs);
}
function removeBulletinDoc(index) {
  const docs = window._currentBulletinDocs || [];
  docs.splice(index, 1);
  window._currentBulletinDocs = docs;
  renderBulletinDocs(docs);
}
function publishBulletin() {
  const title = document.getElementById('bulletinTitle').value.trim();
  const content = document.getElementById('bulletinContent').value.trim();
  const type = document.getElementById('bulletinType').value;
  const docs = window._currentBulletinDocs || [];
  if (!title) {
    showToast('请填写公告标题', 'warning');
    return;
  }
  const activeProj = document.querySelector('.list-item[data-proj].active');
  if (!activeProj) return;
  const projId = activeProj.dataset.proj;
  const bulletin = {
    title,
    content,
    docs,
    type,
    time: new Date().toLocaleString('zh-CN'),
    updatedAt: Date.now(),
    publisher: '你'
  };
  bulletinData[projId] = bulletin;
  localStorage.setItem('sb_bulletins', JSON.stringify(bulletinData));
  window._currentBulletinDocs = [];
  closeBulletin();
  showBulletinBar(projId);
  showToast('✅ 公告已发布');
}

// 获取 AI 上下文（文档位置）
function getContextForAI(projId) {
  const bulletin = bulletinData[projId];
  if (bulletin && bulletin.docs && bulletin.docs.length > 0) {
    return {
      contextHint: `📋 项目文档位置：\n${bulletin.docs.map(function (d) {
        return `• ${d.icon || '📄'} ${d.name} → ${d.path}`;
      }).join('\n')}`,
      docs: bulletin.docs
    };
  }
  return null;
}

// AI 自动读取上下文（包含文档内容摘要）— v2 从后端知识库获取
async function getAIContextMessage(projId) {
  // 从群公告获取文档列表
  const bulletin = bulletinData[projId];
  if (!bulletin || !bulletin.docs || bulletin.docs.length === 0) return '';

  // 从后端知识库获取完整内容
  var backendDocs = [];
  try {
    var r = await apiFetch('/api/knowledge');
    var kbResult = await r.json();
    backendDocs = kbResult.docs || [];
  } catch (e) {
    console.warn('[DocContext] 后端知识库加载失败:', e);
    return '';
  }
  let context = '\n\n📌 当前项目文档：\n';
  bulletin.docs.forEach(function (doc) {
    const docId = (doc && doc.path && doc.path.replace)('/docs/', '');
    const fullDoc = backendDocs.find(function (d) { return d.id === docId; });
    if (fullDoc) {
      const content = fullDoc.content || '';
      const limit = 3000;
      const preview = content.length > limit ? content.slice(0, limit) + '...（内容过长已截取摘要）' : content;
      context += `━━━ ${doc.icon || '📄'} ${fullDoc.name} ━━━\n${preview}\n\n`;
    } else {
      context += `• ${doc.icon || '📄'} ${doc.name}：${doc.path}\n`;
    }
  });
  return context;
}

// 获取员工关联文档 — v3 知识库已全局公共，由后端语义搜索统一注入，前端不再全量注入
async function getEmployeeDocContext(empId) {
  return '';
}

// 获取当前对话的完整文档上下文 — v2 异步
async function getDocContextForChat() {
  const activeProj = document.querySelector('.list-item[data-proj].active');
  if (activeProj) return await getAIContextMessage(activeProj.dataset.proj);
  const activeEmp = document.querySelector('.list-item[data-id].active');
  if (activeEmp) return await getEmployeeDocContext(activeEmp.dataset.id);
  return '';
}

// 一次性迁移：localStorage sb_docs → 后端知识库
async function syncLocalStorageDocsToBackend() {
  var raw = localStorage.getItem('sb_docs');
  if (!raw) return;
  var docsData;
  try {
    docsData = JSON.parse(raw);
  } catch (e) {
    console.warn('[SyncDocs] localStorage sb_docs 解析失败:', e);
    return;
  }
  var docs = Object.values(docsData).filter(function (d) {
    return d && typeof d === 'object';
  });
  if (docs.length === 0) {
    localStorage.removeItem('sb_docs');
    return;
  }
  // 先获取后端已有文档，避免重复
  var existingIds = {};
  try {
    var r = await apiFetch('/api/knowledge');
    var existingResult = await r.json();
    (existingResult.docs || []).forEach(function (d) { existingIds[d.id] = true; });
  } catch (e) {
    console.warn('[SyncDocs] 获取后端知识库失败:', e);
  }
  var migrated = 0;
  for (var i = 0; i < docs.length; i++) {
    var d = docs[i];
    if (existingIds[d.id]) continue;
    try {
      await apiFetch('/api/knowledge', {
        method: 'POST',
        body: JSON.stringify({
          id: d.id,
          name: d.name || '未命名文档',
          content: d.content || '',
          icon: d.icon || '📄'
        })
      });
      migrated++;
    } catch (e) {
      console.warn('[SyncDocs] 迁移文档失败:', d.id, e);
    }
  }
  if (migrated > 0) {
    console.debug('[SyncDocs] 已从 localStorage 迁移 ' + migrated + ' 篇文档到后端知识库');
    showToast('📚 已同步 ' + migrated + ' 篇本地文档到后端知识库');
  } else {
    console.debug('[SyncDocs] 无需迁移（后端已存在或本地无文档）');
  }
  localStorage.removeItem('sb_docs');
}

function showBulletinBar(projId) {
  const bar = document.getElementById('bulletinBar');
  const bulletin = bulletinData[projId];
  if (bulletin) {
    const icons = {
      normal: '📢',
      important: '⭐',
      urgent: '🚨'
    };
    const types = {
      normal: '',
      important: 'important',
      urgent: 'urgent'
    };
    bar.className = 'bulletin-bar ' + types[bulletin.type];
    bar.style.display = 'flex';
    document.getElementById('bulletinIcon').textContent = icons[bulletin.type] || '📢';
    document.getElementById('bulletinContent').innerHTML = '<strong>' + escapeHtml(bulletin.title || '') + '</strong>';
    // 显示文档数量
    if (bulletin.docs && bulletin.docs.length > 0) {
      document.getElementById('bulletinContent').innerHTML += ' <span style="font-size:12px;color:var(--text-secondary);">📄' + bulletin.docs.length + '个文档</span>';
    }
  } else {
    bar.style.display = 'none';
  }
}
function hideBulletin() {
  (document.getElementById('bulletinBar') || {}).style && (document.getElementById('bulletinBar').style.display = 'none');
}

// ============ 督促机制 ============
let supervisorConfig = JSON.parse(localStorage.getItem('sb_supervisor') || '{}');
let supervisorTimer = null;
let supervisorLastCheck = null;
function getCurrentProjectId() {
  var activeProj = document.querySelector('.list-item[data-proj].active');
  if (activeProj) return activeProj.dataset.proj;
  // 仅通过 DOM 判断当前项目，不 fallback 到 localStorage，避免个人聊天时误取残留项目ID
  return '';
}
function openSupervisorPanel() {
  var projId = getCurrentProjectId();
  if (!projId) {
    showToast('⚠️ 请先选择一个项目组');
    return;
  }
  const config = supervisorConfig[projId] || {
    enabled: 'off',
    strategy: 'progress',
    timeout: 10,
    pushMessage: true,
    pushMention: true
  };
  document.getElementById('supervisorEnabled').value = config.enabled;
  document.getElementById('supervisorStrategy').value = config.strategy;
  document.getElementById('supervisorTimeout').value = config.timeout || 10;
  document.getElementById('pushMessage').checked = config.pushMessage !== false;
  document.getElementById('pushMention').checked = config.pushMention !== false;
  document.getElementById('pushNotification').checked = config.pushNotification === true;
  updateSupervisorStatus(config);
  document.getElementById('supervisorModal').classList.add('show');
}
function closeSupervisor() {
  document.getElementById('supervisorModal').classList.remove('show');
}
function saveSupervisor() {
  var projId = getCurrentProjectId();
  if (!projId) {
    showToast('⚠️ 未选择项目组');
    return;
  }
  const config = {
    enabled: document.getElementById('supervisorEnabled').value,
    strategy: document.getElementById('supervisorStrategy').value,
    timeout: parseInt(document.getElementById('supervisorTimeout').value) || 10,
    pushMessage: document.getElementById('pushMessage').checked,
    pushMention: document.getElementById('pushMention').checked,
    pushNotification: document.getElementById('pushNotification').checked
  };
  supervisorConfig[projId] = config;
  localStorage.setItem('sb_supervisor', JSON.stringify(supervisorConfig));
  closeSupervisor();
  updateSupervisorStatus(config);
  startSupervisorTimer();
  showToast('✅ 督促配置已保存');
}
function updateSupervisorStatus(config) {
  const indicator = document.getElementById('supervisorIndicator');
  const statusText = document.getElementById('supervisorStatusText');
  const lastCheck = document.getElementById('supervisorLastCheck');
  if (config.enabled === 'off') {
    indicator.style.background = '#86868B';
    statusText.textContent = '督促功能已关闭';
    lastCheck.style.display = 'none';
  } else {
    indicator.style.background = '#34C759';
    statusText.textContent = `督促功能已开启（每 ${config.enabled} 分钟检查）`;
    lastCheck.style.display = 'block';
    if (supervisorLastCheck) {
      lastCheck.textContent = '上次检查：' + new Date(supervisorLastCheck).toLocaleTimeString('zh-CN');
    }
  }
}
function startSupervisorTimer() {
  if (supervisorTimer) {
    clearInterval(supervisorTimer);
    supervisorTimer = null;
  }
  var projId = getCurrentProjectId();
  if (!projId) return;
  var config = supervisorConfig[projId];
  if (!config || config.enabled === 'off') return;
  const interval = parseInt(config.enabled) * 60 * 1000;
  supervisorTimer = setInterval(function () {
    performSupervisorCheck();
  }, interval);
}
function performSupervisorCheck() {
  var projId = getCurrentProjectId();
  if (!projId) return;
  var config = supervisorConfig[projId];
  if (!config || config.enabled === 'off') return;
  supervisorLastCheck = Date.now();
  updateSupervisorStatus(config);

  // 检查逻辑
  const issues = [];
  const now = Date.now();

  // 初始化催促计数
  if (!supervisorConfig[projId].pushCount) supervisorConfig[projId].pushCount = {};

  // 1. 检查消息响应时间 - 基于实际时间戳
  const msgs = document.querySelectorAll('#messagesArea .msg');
  const lastActivityTime = localStorage.getItem(`sb_last_activity_${projId}`);
  const timeSinceActivity = lastActivityTime ? (now - parseInt(lastActivityTime)) / 60000 : Infinity;
  if (config.strategy !== 'progress' && timeSinceActivity > config.timeout) {
    issues.push(`⏰ 已经 ${Math.round(timeSinceActivity)} 分钟没有新消息了`);
  }

  // 2. 检查任务看板状态
  if (config.strategy === 'progress' || config.strategy === 'aggressive') {
    // 检查待办任务堆积
    const todoCol = document.querySelector('#taskBoard [data-col="todo"]');
    if (todoCol) {
      const todoTasks = todoCol.querySelectorAll('.task-card');
      if (todoTasks.length > 5) {
        issues.push(`📋 待办堆积：${todoTasks.length} 个任务等待处理`);
      }
    }

    // 检查长期进行中的任务（超过30分钟无更新）
    const progressCol = document.querySelector('#taskBoard [data-col="progress"]');
    if (progressCol) {
      const progressTasks = progressCol.querySelectorAll('.task-card');
      progressTasks.forEach(function (task) {
        const taskTime = task.dataset.updated;
        if (taskTime) {
          const taskAge = (now - parseInt(taskTime)) / 60000;
          if (taskAge > 30) {
            issues.push('🔧 任务 "' + (task.querySelector('.task-name') && task.querySelector('.task-name').textContent || '未知') + '" 已进行 ' + Math.round(taskAge) + ' 分钟');
          }
        }
      });
    }
  }

  // 3. 检查员工活跃状态
  if (config.strategy === 'aggressive') {
    emps.forEach(function (emp) {
      if (!emp.lastActive) return;
      const inactiveTime = (now - emp.lastActive) / 60000;
      if (inactiveTime > config.timeout * 2) {
        issues.push(`💤 ${emp.name} 已离线 ${Math.round(inactiveTime)} 分钟`);
      }
    });
  }

  // 4. 显示督促提醒
  if (issues.length > 0) {
    showSupervisorBanner(issues);

    // 更新催促计数
    issues.forEach(function (issue) {
      if (!supervisorConfig[projId].pushCount[issue]) {
        supervisorConfig[projId].pushCount[issue] = 1;
      } else {
        supervisorConfig[projId].pushCount[issue]++;
      }
    });
    if (config.pushMessage) {
      // 生成催促消息
      const mentionText = config.pushMention ? '@所有人 ' : '';
      const urgentIssues = issues.filter(function (i) {
        return supervisorConfig[projId].pushCount[i] > 1;
      });
      const prefix = urgentIssues.length > 0 ? '⚠️⚠️ 多次催促未响应：' : '⚠️ 群主提醒：';
      const issueText = issues.slice(0, 2).join('\n');
      const urgeMsg = mentionText + prefix + '\n' + issueText;

      // 如果当前在群聊中，直接发送督促消息
      if (currentGroupId && typeof sendGroupMessage === 'function') {
        sendGroupMessage(currentGroupId, urgeMsg, []);
      } else {
        // 不在群聊中，填入输入框让用户手动发送
        var promptEl = document.getElementById('msgInput');
        if (promptEl) {
          promptEl.value = urgeMsg;
          promptEl.focus();
        }
      }
    }
    if (config.pushNotification) {
      // 浏览器通知（如果已授权）
      if (Notification.permission === 'granted') {
        new Notification('全可AI 督促提醒', {
          body: issues[0],
          icon: '🦞'
        });
      }
    }
  } else {
    // 没有问题，清空催促计数
    if (supervisorConfig[projId].pushCount) {
      Object.keys(supervisorConfig[projId].pushCount).forEach(function (k) {
        supervisorConfig[projId].pushCount[k] = Math.max(0, supervisorConfig[projId].pushCount[k] - 1);
      });
    }
  }

  // 保存最后活动时间
  localStorage.setItem(`sb_last_activity_${projId}`, now.toString());
}
function showSupervisorBanner(issues) {
  const banner = document.getElementById('supervisorBanner');
  const content = document.getElementById('supervisorBannerContent');
  content.innerHTML = '<strong>⏰ 督促提醒</strong><br>' + issues.slice(0, 2).join('<br>');
  banner.style.display = 'flex';
  setTimeout(function () {
    banner.style.display = 'none';
  }, 15000);
}
function dismissSupervisorBanner() {
  document.getElementById('supervisorBanner').style.display = 'none';
}
function openSupervisorConfigFromHeader() {
  // 从项目组设置入口打开督促配置
  var projId = getCurrentProjectId();
  if (!projId) {
    showToast('⚠️ 请先选择一个项目组');
    return;
  }
  openSupervisorPanel();
}

// 文档分享函数已移至 chat.js

// Handle Input
function handleInput() {
  const val = document.getElementById('msgInput').value;
  const sendBtn = document.getElementById('sendBtn');
  if (sendBtn) {
    sendBtn.disabled = !val.trim() && (!window._pendingImages || window._pendingImages.length === 0);
  }
  if (val.startsWith('/')) {
    document.getElementById('slashMenu').classList.add('active');
    document.getElementById('slashSearch').value = val.slice(1);
    filterSlashCommands(val.slice(1));
    var md = document.getElementById('mentionDropdown');
    if (md) md.classList.remove('active');
    mentionMode = false;
  } else if (/@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]*)$/.test(val)) {
    showMentionDropdown();
    mentionMode = true;
  } else {
    document.getElementById('slashMenu').classList.remove('active');
    var md2 = document.getElementById('mentionDropdown');
    if (md2) md2.classList.remove('active');
    mentionMode = false;
  }
}
function openSlashMenu() {
  var input = document.getElementById('msgInput');
  if (!input) return;
  input.value = '/';
  input.focus();
  handleInput();
}
function insertTextAtCursor(text) {
  var input = document.getElementById('msgInput');
  if (!input) return;
  var start = input.selectionStart || 0;
  var end = input.selectionEnd || 0;
  input.value = input.value.substring(0, start) + text + input.value.substring(end);
  input.selectionStart = input.selectionEnd = start + text.length;
  input.focus();
  handleInput();
}
function openCompressModalWithConfirm() {
  closeChatMoreMenu();
  if (confirm('确定要压缩上下文吗？这将清理早期消息。')) openCompressModal();
}
function toggleChatMoreMenu() {
  var dd = document.getElementById('chatMoreDropdown');
  if (dd) dd.classList.toggle('show');
}
function closeChatMoreMenu() {
  var dd = document.getElementById('chatMoreDropdown');
  if (dd) dd.classList.remove('show');
}

// 复制当前聊天最后一条消息(从 DOM 拿文本,navigator.clipboard 优先 + execCommand fallback)
function copyLastChatMessage() {
  closeChatMoreMenu();
  var msgs = document.querySelectorAll('#messagesArea .msg[data-msg-id]');
  if (!msgs.length) {
    showToast('⚠️ 当前聊天没有可复制的消息');
    return;
  }
  var last = msgs[msgs.length - 1];
  var contentEl = last.querySelector('.msg-content, .msg-text, .msg-bubble, .text, .content');
  var text = contentEl ? (contentEl.innerText || contentEl.textContent || '').trim() : (last.innerText || '').trim();
  if (!text) {
    showToast('⚠️ 最后一条消息无可复制内容');
    return;
  }
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(function () {
      showToast('✅ 已复制最后一条消息');
    }).catch(function () {
      fallbackCopy(text);
    });
  } else {
    fallbackCopy(text);
  }
  function fallbackCopy(t) {
    var ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); showToast('✅ 已复制最后一条消息'); }
    catch (e) { showToast('❌ 复制失败,请手动复制'); }
    document.body.removeChild(ta);
  }
}

// 删除当前聊天最后一条消息(后端 DELETE /api/chat/{empId}/{msgId},成功 renderMsgs 刷新)
function deleteLastChatMessage() {
  closeChatMoreMenu();
  var msgs = document.querySelectorAll('#messagesArea .msg[data-msg-id]');
  if (!msgs.length) {
    showToast('⚠️ 当前聊天没有可删除的消息');
    return;
  }
  var last = msgs[msgs.length - 1];
  var msgId = last.getAttribute('data-msg-id');
  if (!msgId) {
    showToast('⚠️ 最后一条消息缺少 id,无法删除');
    return;
  }
  if (!confirm('确定删除最后一条消息?此操作不可恢复。')) return;
  var empId = localStorage.getItem('sb_current_emp');
  if (!empId) {
    showToast('❌ 无法确定当前聊天员工');
    return;
  }
  if (typeof apiFetch !== 'function') {
    showToast('❌ apiFetch 未就绪');
    return;
  }
  apiFetch('/api/chat/' + encodeURIComponent(empId) + '/' + encodeURIComponent(msgId), {
    method: 'DELETE'
  }).then(function (res) {
    if (res && (res.status === 200 || res.status === 204)) {
      showToast('✅ 已删除最后一条消息');
      if (typeof renderMsgs === 'function') renderMsgs('private');
    } else {
      var err = (res && res.body && res.body.error) || ('HTTP ' + (res && res.status));
      showToast('❌ 删除失败:' + err);
    }
  }).catch(function (e) {
    showToast('❌ 删除失败:' + (e && e.message || '网络错误'));
  });
}
function handleMenuReset() {
  closeChatMoreMenu();
  if (currentGroupId) {
    resetGroupChat();
  } else {
    openResetModal();
  }
}
function filterSlashCommands(q) {
  var items = document.querySelectorAll('#slashSections .slash-item');
  slashIdx = 0;
  items.forEach(function (item, i) {
    var cmd = item.dataset && item.dataset.cmd && item.dataset.cmd.toLowerCase ? item.dataset.cmd.toLowerCase() : '';
    var match = !q || cmd && cmd.indexOf(q.toLowerCase()) > -1;
    item.style.display = match ? '' : 'none';
    if (match && i === 0) item.classList.add('selected');
  });
}
function navigateSlash(dir) {
  var items = Array.prototype.slice.call(document.querySelectorAll('#slashSections .slash-item:not([style*="none"])'));
  if (!items.length) return;
  if (items[slashIdx]) items[slashIdx].classList.remove('selected');
  slashIdx = (slashIdx + dir + items.length) % items.length;
  if (items[slashIdx]) items[slashIdx].classList.add('selected');
  if (items[slashIdx]) items[slashIdx].scrollIntoView({
    block: 'nearest'
  });
}

// Mention
function showMention() {
  var input = document.getElementById('msgInput');
  if (input) {
    var trimmed = input.value.replace(/\s+$/, '');
    if (!trimmed.endsWith('@')) input.value += '@';
    input.focus();
  }
  showMentionDropdown();
  mentionMode = true;
  updateSendButtonState();
}
function showMentionDropdown() {
  var dd = document.getElementById('mentionDropdown');
  var list = document.getElementById('mentionList');
  var input = document.getElementById('msgInput');
  if (!dd || !list) return;
  dd.classList.add('active');
  mentionIdx = 0;
  list.innerHTML = '';

  // 提取当前 @ 后的搜索词
  var query = '';
  if (input && input.value) {
    var m = input.value.match(/@([^\s@,，.。！？;；:：、（）()\[\]【】{}<>"'&]*)$/);
    if (m) query = m[1].trim().toLowerCase();
  }

  // 群聊模式下，优先显示群组成员
  var mentionList = emps;
  if (currentGroupId) {
    var group = groups.find(function (g) {
      return g.id === currentGroupId;
    });
    if (group && group.members) {
      // 兼容 members 的两种格式：字符串数组 和 字典数组
      mentionList = group.members.map(function (m) {
        var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
        return emps.find(function (e) {
          return e.id === mid;
        });
      }).filter(function (e) {
        return e && !e.archived;
      });
    }
  }

  var hasItems = false;

  // "所有人" 选项
  if (!query || '所有人'.toLowerCase().indexOf(query) > -1) {
    var allDiv = document.createElement('div');
    allDiv.className = 'mention-item';
    allDiv.dataset.id = 'all';
    allDiv.style.cssText = 'padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;';
    allDiv.innerHTML = '<span style="font-size:16px;">👥</span><span>所有人</span>';
    allDiv.onclick = function () {
      pickMention('all');
    };
    list.appendChild(allDiv);
    hasItems = true;
  }

  // 员工列表
  mentionList.forEach(function (emp) {
    if (!emp || emp.archived) return;
    var empName = emp.name || '';
    var empPinyin = (emp.pinyin || '').toLowerCase();
    if (query && empName.toLowerCase().indexOf(query) === -1 && empPinyin.indexOf(query) === -1) return;
    var item = document.createElement('div');
    item.className = 'mention-item';
    item.dataset.id = emp.id;
    item.style.cssText = 'padding:8px 12px;cursor:pointer;display:flex;align-items:center;gap:8px;';
    var roleText = getEmpRoleDisplay(emp);
    item.innerHTML = '<span>' + escapeHtml(empName) + (roleText ? ' (' + escapeHtml(roleText) + ')' : '') + '</span>';
    item.onclick = function () {
      pickMention(emp.id);
    };
    list.appendChild(item);
    hasItems = true;
  });

  if (!hasItems) {
    list.innerHTML = '<div style="padding:8px 12px;color:var(--text-tertiary);font-size:13px;">无匹配成员</div>';
  }
}
function selectMention(dir) {
  var items = document.querySelectorAll('#mentionDropdown .mention-item');
  if (!items.length) return;
  items[mentionIdx] && items[mentionIdx].classList.remove('selected');
  mentionIdx = (mentionIdx + dir + items.length) % items.length;
  items[mentionIdx] && items[mentionIdx].classList.add('selected');
}
function pickMention(id) {
  var name = id === 'all' ? '所有人' : (function () {
    var e = emps.find(function (x) {
      return x.id === id;
    });
    return e && e.name || '';
  })();
  var input = document.getElementById('msgInput');
  if (input) input.value = input.value.replace(/@+[^@\n]*$/, '') + '@' + name + ' ';
  var dd = document.getElementById('mentionDropdown');
  if (dd) dd.classList.remove('active');
  mentionMode = false;
  mentionIdx = 0;
  updateSendButtonState();
  if (input) input.focus();
}

// Slash Commands
function execCmd(cmd) {
  document.getElementById('msgInput').value = '';
  document.getElementById('slashMenu').classList.remove('active');
  document.getElementById('slashSearch').value = '';
  switch (cmd) {
    case '/讨论':
      if (!currentGroupId) {
        showToast('⚠️ 请先进入一个群组');
        return;
      }
      initiateAgentDiscussion(currentGroupId);
      break;
    case '/看板':
      toggleTaskBoard();
      break;
    case '/新建组':
      openGroupWizard();
      break;
    case '/重置':
      resetContext();
      break;
    case '/总结':
      generateSummary();
      break;
    case '/归档':
      archiveCurrent();
      break;
    case '/help':
      showHelp();
      break;
    case '/stop':
      showToast('⏹️ 已停止生成');
      break;
    case '/model':
      showModelSelector();
      break;
    case '/usage':
      showUsage();
      break;
    case '/skill':
      var skillEmp = getCurrentEmployeeInfo();
      if (skillEmp && skillEmp.id) {
        openEmpDetail(skillEmp.id);
        setTimeout(function () {
          switchEmpDetailTab('skills');
        }, 150);
      } else {
        showToast('请先选择一个员工');
      }
      break;
    case '/compress':
      compressContext();
      break;
  }
}
function showHelp() {
  const area = document.getElementById('messagesArea');
  area.insertAdjacentHTML('beforeend', `<div class="msg system">
<div class="msg-bubble" style="background:var(--bg-secondary);text-align:left;max-width:100%;">
<div style="font-weight:600;margin-bottom:8px;">📖 可用命令</div>
<div style="display:grid;gap:6px;font-size:13px;">
<div><code style="color:var(--accent)">/help</code> 显示帮助</div>
<div><code style="color:var(--accent)">/重置</code> 清空上下文</div>
<div><code style="color:var(--accent)">/总结</code> 总结当前对话</div>
<div><code style="color:var(--accent)">/归档</code> 归档当前对话</div>
<div><code style="color:var(--accent)">/看板</code> 打开任务看板</div>
<div><code style="color:var(--accent)">/新建组</code> 创建项目组</div>
<div><code style="color:var(--accent)">/stop</code> 停止生成</div>
<div><code style="color:var(--accent)">/model</code> 切换 AI 模型</div>
<div><code style="color:var(--accent)">/usage</code> 查看 Token 消耗</div>
<div><code style="color:var(--accent)">/compress</code> 压缩上下文</div>
</div>
</div>
</div>`);
  area.scrollTop = area.scrollHeight;
}
function showUsage() {
  const area = document.getElementById('messagesArea');
  const used = parseInt(localStorage.getItem('sb_tokens_used') || '2300');
  const total = 8000;
  const pct = Math.round(used / total * 100);
  area.insertAdjacentHTML('beforeend', `<div class="msg system">
<div class="msg-bubble" style="background:var(--bg-secondary);text-align:left;max-width:100%;">
<div style="font-weight:600;margin-bottom:8px;">📊 Token 使用情况</div>
<div style="display:flex;align-items:center;gap:8px;">
<div style="flex:1;height:8px;background:var(--bg-tertiary);border-radius:4px;overflow:hidden;">
<div style="width:${pct}%;height:100%;background:var(--accent);border-radius:4px;"></div>
</div>
<span style="font-size:12px;color:var(--text-secondary)">${used}/${total} (${pct}%)</span>
</div>
<div style="margin-top:8px;font-size:12px;color:var(--text-secondary);">
<div>• 输入: ${Math.round(used * 0.6)} tokens</div>
<div>• 输出: ${Math.round(used * 0.4)} tokens</div>
<div>• 上下文窗口: 8K</div>
</div>
</div>
</div>`);
  area.scrollTop = area.scrollHeight;
}
function showModelSelector() {
  const area = document.getElementById('messagesArea');
  const modelGroups = [{
    name: '🏭 OpenAI',
    models: [{
      id: 'gpt-4o',
      name: 'GPT-4o',
      desc: '最新最强模型',
      badge: '推荐'
    }, {
      id: 'gpt-4o-mini',
      name: 'GPT-4o mini',
      desc: '轻量快速'
    }, {
      id: 'gpt-4-turbo',
      name: 'GPT-4 Turbo',
      desc: '高性能'
    }, {
      id: 'o1-preview',
      name: 'o1 Preview',
      desc: '推理能力'
    }]
  }, {
    name: '🎯 Anthropic',
    models: [{
      id: 'claude-3.5-sonnet',
      name: 'Claude 3.5 Sonnet',
      desc: '长文本强',
      badge: '推荐'
    }, {
      id: 'claude-3.5-haiku',
      name: 'Claude 3.5 Haiku',
      desc: '轻量快速'
    }, {
      id: 'claude-3-opus',
      name: 'Claude 3 Opus',
      desc: '最高智能'
    }]
  }, {
    name: '💎 Google',
    models: [{
      id: 'gemini-1.5-pro',
      name: 'Gemini 1.5 Pro',
      desc: '超长上下文'
    }, {
      id: 'gemini-1.5-flash',
      name: 'Gemini 1.5 Flash',
      desc: '快速响应'
    }]
  }, {
    name: '🇨🇳 国内',
    models: [{
      id: 'deepseek-v3',
      name: 'DeepSeek V3',
      desc: '性价比高',
      badge: '推荐'
    }, {
      id: 'qwen-max',
      name: 'Qwen Max',
      desc: '阿里最强'
    }, {
      id: 'qwen-plus',
      name: 'Qwen Plus',
      desc: '均衡之选'
    }, {
      id: 'glm-5',
      name: 'GLM-5',
      desc: '智谱最新'
    }, {
      id: 'kimi-k2',
      name: 'Kimi K2',
      desc: '长上下文'
    }]
  }];
  area.insertAdjacentHTML('beforeend', `<div class="msg system">
<div class="msg-bubble model-selector" style="background:var(--bg-secondary);text-align:left;max-width:100%;">
<div style="font-weight:600;margin-bottom:12px;">🤖 AI 模型切换</div>
<div class="model-search">
<input type="text" placeholder="🔍 搜索模型..." style="width:100%;padding:8px 12px;border:1px solid var(--separator);border-radius:8px;background:var(--bg-tertiary);color:var(--text);font-size:13px;" oninput="filterModels(this.value)">
</div>
<div class="model-groups" style="max-height:400px;overflow-y:auto;margin-top:12px;">
${modelGroups.map(function (g) {
    return `<div class="model-group">
<div class="model-group-title">${g.name}</div>
${g.models.map(function (m) {
      return `<div class="model-item ${currentModel === m.id ? 'selected' : ''}" onclick="selectModel('${m.id}')">
<div class="model-item-info">
<div class="model-item-name">${m.name}</div>
<div class="model-item-desc">${m.desc}</div>
</div>
${m.badge ? `<div class="model-item-badge">${m.badge}</div>` : ''}
</div>`;
    }).join('')}
</div>`;
  }).join('')}
</div>
</div>
</div>`);
  area.scrollTop = area.scrollHeight;
}
let currentModel = 'gpt-4o';
function selectModel(model) {
  currentModel = model;
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (emp) emp.model = model;
  saveEmployees();
  showToast(`🤖 已切换到 ${model}`);
  renderEmployeeList();
  // Update model selector UI
  document.querySelectorAll('.model-item').forEach(function (el) {
    return el.classList.toggle('selected', el.onclick.toString().includes(model));
  });
}
function filterModels(q) {
  document.querySelectorAll('.model-group').forEach(function (g) {
    const items = g.querySelectorAll('.model-item');
    const visible = Array.from(items).filter(function (i) {
      return i.textContent.toLowerCase().includes(q.toLowerCase());
    });
    g.style.display = visible.length ? 'block' : 'none';
  });
}

// ============ Model Switcher Pill (ui/model-switcher) ============
// 位置: 输入框 toolbar (/model slash command + pill 点击)
// 设计: 必须经 OpenClaw Gateway，不直连 LLM API（前端 chat 架构硬约束）
// ★ dev/feat 收尾 (2026-09-30): 全局硬切废弃 —— POST /api/admin/chat-model 不再改网关配置
//   (switch_chat_model.py 改为只读+推荐)。pill 语义改为「请求级模型指定」:
//   客户选中后写入 REQUEST_CHAT_MODEL, 后续本人发起的聊天在 _sendChatWaitForLifecycle
//   里先 sessions.patch({model}) 再 chat.send, lifecycle 结束自动 patch({model:null}) 还原。
//   不改全局配置、不影响其他客户、不碰后台归纳/通知等内部任务。
// 后端契约:
//   GET  /api/admin/chat-model    → {current: "zhipu/glm-4-flash", ...} 只用于展示网关默认
var MODEL_AVAILABLE = [
  { key: 'zhipu',   icon: '⚡', label: '智谱 GLM-4-flash', desc: '快 + 便宜，测试首选 (openai-completions)', modelRef: 'zhipu/glm-4-flash' },
  { key: 'kimi',    icon: '🌙', label: 'Kimi K3',         desc: '强 + 按员工隔离 API key (anthropic-messages)', modelRef: 'kimi/k3' },
  { key: 'minimax', icon: '🤖', label: 'MiniMax M3',      desc: '最新 1M context coding 模型 (anthropic-messages)', modelRef: 'minimax/MiniMax-M3' }
];
var MODEL_FALLBACK_PROFILE = 'zhipu';
// 请求级模型: null = 跟随网关默认; 否则为 "provider/model" ref, 仅作用于本浏览器发起的聊天
var REQUEST_CHAT_MODEL = null;
var GATEWAY_DEFAULT_MODEL = '';

// pill 文字: 请求级选择优先, 否则显示跟随网关默认
function _refreshModelPillLabel() {
  if (REQUEST_CHAT_MODEL) {
    var sel = MODEL_AVAILABLE.find(function (m) { return m.modelRef === REQUEST_CHAT_MODEL; });
    _setModelPillLabel((sel ? sel.icon + ' ' + sel.label.split(' ')[0] : REQUEST_CHAT_MODEL), { disabled: false });
  } else {
    var cur = (GATEWAY_DEFAULT_MODEL || '').split('/')[0];
    var curItem = MODEL_AVAILABLE.find(function (m) { return m.key === cur; });
    _setModelPillLabel('跟随默认' + (curItem ? '·' + curItem.label.split(' ')[0] : ''), { disabled: false });
  }
}

function _setModelPillLabel(text, opts) {
  opts = opts || {};
  var lbl = document.getElementById('modelSwitcherLabel');
  var btn = document.getElementById('modelSwitcherBtn');
  if (lbl) lbl.textContent = text;
  if (btn) {
    btn.disabled = !!opts.disabled;
    btn.classList.toggle('open', !!opts.open);
  }
}

function _renderModelDropdown() {
  var dd = document.getElementById('modelDropdown');
  if (!dd) return;
  var html = '';
  // 第一项: 跟随网关默认（请求级不指定模型）
  html += '<div class="model-dropdown-item ' + (!REQUEST_CHAT_MODEL ? 'active' : '') + '" '
       + 'data-profile="default" '
       + 'onclick="switchChatModel(\'default\')">'
       + '<span class="mdi-icon">🔧</span>'
       + '<span class="mdi-text">'
       +   '<div class="mdi-name">跟随网关默认</div>'
       +   '<div class="mdi-desc">' + (GATEWAY_DEFAULT_MODEL ? '当前网关默认: ' + GATEWAY_DEFAULT_MODEL : '不指定模型，用网关配置的默认') + '</div>'
       + '</span>'
       + '<span class="mdi-check">✓</span>'
       + '</div>';
  MODEL_AVAILABLE.forEach(function (m) {
    var active = REQUEST_CHAT_MODEL === m.modelRef;
    html += '<div class="model-dropdown-item ' + (active ? 'active' : '') + '" '
         + 'data-profile="' + m.key + '" '
         + 'onclick="switchChatModel(\'' + m.key + '\')">'
         + '<span class="mdi-icon">' + m.icon + '</span>'
         + '<span class="mdi-text">'
         +   '<div class="mdi-name">' + m.label + '</div>'
         +   '<div class="mdi-desc">' + m.desc + '</div>'
         + '</span>'
         + '<span class="mdi-check">✓</span>'
         + '</div>';
  });
  html += '<div class="model-dropdown-footer">'
       + '请求级指定：只影响你发起的聊天（sessions.patch → chat.send → 自动还原），不改全局配置'
       + '</div>';
  dd.innerHTML = html;
}

function _closeModelDropdown() {
  var dd = document.getElementById('modelDropdown');
  var btn = document.getElementById('modelSwitcherBtn');
  if (dd) dd.classList.remove('open');
  if (btn) btn.classList.remove('open');
}

function toggleModelDropdown() {
  var dd = document.getElementById('modelDropdown');
  var btn = document.getElementById('modelSwitcherBtn');
  if (!dd || !btn) return;
  var isOpen = dd.classList.contains('open');
  if (isOpen) {
    _closeModelDropdown();
  } else {
    dd.classList.add('open');
    btn.classList.add('open');
  }
}

function loadChatModelState() {
  // 启动拉取网关默认模型（仅展示用，不再代表当前选择）；失败不阻塞输入
  var btn = document.getElementById('modelSwitcherBtn');
  if (btn) btn.disabled = true;
  apiFetch('/api/admin/chat-model')
    .then(function (r) {
      return r && r.ok ? r.json() : null;
    })
    .then(function (data) {
      if (!data) throw new Error('empty response');
      GATEWAY_DEFAULT_MODEL = data.current || '';
      _refreshModelPillLabel();
      _renderModelDropdown();
      if (btn) btn.disabled = false;
    })
    .catch(function (e) {
      console.warn('[ModelSwitcher] load failed:', e);
      _setModelPillLabel('🤖 模型', { disabled: false });
      _renderModelDropdown();
      if (btn) btn.disabled = false;
    });
}

function switchChatModel(profile) {
  if (!profile) return;
  _closeModelDropdown();
  // ★ 请求级语义: 只写本浏览器的 REQUEST_CHAT_MODEL, 不打 POST 改全局配置
  if (profile === 'default') {
    REQUEST_CHAT_MODEL = null;
  } else {
    var item = MODEL_AVAILABLE.find(function (m) { return m.key === profile; });
    if (!item) {
      showToast && showToast('❌ 未知模型 profile: ' + profile);
      return;
    }
    REQUEST_CHAT_MODEL = item.modelRef;
  }
  _refreshModelPillLabel();
  _renderModelDropdown();
  if (REQUEST_CHAT_MODEL) {
    showToast && showToast('已指定请求级模型 ' + REQUEST_CHAT_MODEL + '（只影响你发起的聊天，自动还原）');
  } else {
    showToast && showToast('已恢复跟随网关默认模型');
  }
}

// 把旧的占位 /model slash command 接到新 pill（统一入口）
// 替换之前的 showModelSelector() —— 旧的会假切换（只改前端显示）
function showModelSelector() { toggleModelDropdown(); }

// 点击外部关闭 dropdown
document.addEventListener('click', function (ev) {
  var dd = document.getElementById('modelDropdown');
  var btn = document.getElementById('modelSwitcherBtn');
  if (!dd || !btn || !dd.classList.contains('open')) return;
  if (btn.contains(ev.target) || dd.contains(ev.target)) return;
  _closeModelDropdown();
});

// ESC 关闭 dropdown
document.addEventListener('keydown', function (ev) {
  if (ev.key === 'Escape') _closeModelDropdown();
});

// 重置指定员工在 OpenClaw 网关侧的所有可能 session（主 session + main 降级隔离 session）
function _resetEmpOpenClawSessions(empId) {
  if (typeof openclaw === 'undefined' || !openclaw.connected || !openclaw.authenticated) return;
  var keys = [
    'agent:' + empId + ':chat',
    'agent:main:chat-' + empId
  ];
  keys.forEach(function (sessionKey) {
    openclaw.resetSession(sessionKey).then(function (res) {
      console.debug('[resetContext] OpenClaw session reset ok:', sessionKey, res);
    }).catch(function (e) {
      console.warn('[resetContext] OpenClaw session reset failed:', sessionKey, e);
    });
  });
}

// 斜杠命令实现
function resetContext() {
  var empId = localStorage.getItem('sb_current_emp');
  var groupId = currentGroupId;
  if (empId && !groupId) {
    // 1. 重置 OpenClaw 网关侧 session（真正清空 AI 侧上下文）
    _resetEmpOpenClawSessions(empId);
    // 2. 清空后端本地聊天记录
    if (typeof apiFetch === 'function') {
      apiFetch('/api/chat/' + encodeURIComponent(empId), {
        method: 'DELETE'
      }).catch(function (e) {});
    }
  }
  // 清空界面
  const area = document.getElementById('messagesArea');
  area.innerHTML = getChatEmptyStateHtml();
  showToast('🔄 上下文已重置');
}

// 上下文压缩 - 保留关键信息，省 token
function compressContext() {
  const empId = localStorage.getItem('sb_current_emp');
  if (!empId) {
    showToast('\u26A0\uFE0F 请先选择一个员工');
    return;
  }
  const emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) {
    showToast('\u26A0\uFE0F 员工不存在');
    return;
  }

  // 触发后端压缩并重新渲染
  apiFetch('/api/chat/summarize/' + encodeURIComponent(empId), {
    method: 'POST'
  }).then(function (r) {
    return r.json();
  }).then(function (data) {
    showToast('\u{1F4E6} 上下文已压缩');
    renderMsgs('private');
  }).catch(function (e) {
    console.warn('[Chat] 摘要压缩失败:', e);
    showToast('⚠️ 压缩失败');
  });
}
// 上下文管理面板
function showContextMenu() {
  const menu = document.createElement('div');
  menu.className = 'context-menu';
  menu.innerHTML = `
<div class="context-menu-item" onclick="compressContext();closeContextMenu();">
<div style="font-size:18px;">📦</div>
<div><div style="font-weight:500;">压缩上下文</div><div style="font-size:11px;color:var(--text-tertiary);">清理早期消息，节省 token</div></div>
</div>
<div class="context-menu-item" onclick="generateSummary();closeContextMenu();">
<div style="font-size:18px;">📝</div>
<div><div style="font-weight:500;">生成摘要</div><div style="font-size:11px;color:var(--text-tertiary);">总结当前对话要点</div></div>
</div>
<div class="context-menu-item" onclick="archiveCurrent();closeContextMenu();">
<div style="font-size:18px;">📁</div>
<div><div style="font-weight:500;">归档当前</div><div style="font-size:11px;color:var(--text-tertiary);">保存副本到归档区</div></div>
</div>
<div class="context-menu-item" onclick="resetContext();closeContextMenu();">
<div style="font-size:18px;">🔄</div>
<div><div style="font-weight:500;">重置对话</div><div style="font-size:11px;color:#C62828;">清空所有消息</div></div>
</div>
`;
  document.body.appendChild(menu);
  menu.style.cssText = 'position:fixed;bottom:80px;left:50%;transform:translateX(-50%);background:var(--bg-primary);border-radius:var(--radius-lg);box-shadow:0 4px 20px rgba(0,0,0,0.2);padding:8px;z-index:1000;min-width:240px;';
  document.addEventListener('click', closeContextMenu, {
    once: true
  });
}
function closeContextMenu() {
  document.querySelectorAll('.context-menu').forEach(function (m) {
    return m.remove();
  });
}
function generateSummary() {
  const area = document.getElementById('messagesArea');
  const msgs = area.querySelectorAll('.msg-bubble');
  if (msgs.length < 2) {
    showToast('📝 对话太少，无法总结');
    return;
  }
  // 提取消息文本
  const texts = Array.from(msgs).slice(-6).map(function (m) {
    return m.textContent;
  }).join('、');
  const summary = `本次对话主要涉及：${texts.slice(0, 100)}...`;
  area.insertAdjacentHTML('beforeend', `<div class="msg" style="background:var(--accent-light);border-radius:12px;padding:12px;margin:12px 0;">
<div style="font-weight:600;margin-bottom:8px;">📝 对话摘要</div>
<div style="font-size:13px;line-height:1.5;">${escapeHtml(summary)}</div>
</div>`);
  area.scrollTop = area.scrollHeight;
  showToast('📝 已生成对话摘要');
}
function archiveCurrent() {
  const activeItem = document.querySelector('.list-item.active');
  if (activeItem && activeItem.dataset.id) {
    const emp = emps.find(function (e) {
      return e.id === activeItem.dataset.id;
    });
    if (emp) {
      archiveEmp(emp);
      saveEmployees();
      if (typeof syncEmpToServer === 'function') {
        syncEmpToServer(emp).catch(function (e) {});
      }
      renderEmployeeList();
      document.querySelector('.chat-header-name').textContent = '已归档';
      document.querySelector('.chat-header-sub').textContent = emp.name + ' 已移至归档';
      showToast('📦 ' + escapeHtml(emp.name || '') + ' 已归档');
    }
  } else {
    renderEmployeeList();
    showToast('📦 已切换到归档');
  }
}
// Theme Switcher (Soybean-inspired)
let themeScheme = 'light';
function toggleTheme() {
  themeScheme = themeScheme === 'light' ? 'dark' : themeScheme === 'dark' ? 'auto' : 'light';
  localStorage.setItem('sb_theme', themeScheme);
  applyTheme();
  const icons = {
    light: '☀️',
    dark: '🌙',
    auto: '🔄'
  };
  showToast(`${icons[themeScheme]} 主题: ${themeScheme === 'light' ? '浅色' : themeScheme === 'dark' ? '深色' : '自动'}`);
}
function applyTheme() {
  const root = document.documentElement;
  /* 〔r39-3 暗黑修复〕根因: sb2 暗黑色板挂在 [data-sb2-theme="dark"] (index.html:9974),
     本函数只写 data-theme → 暗黑板永远不命中, 按钮空转。
     修法: 按最终生效主题 (auto 解析系统偏好) 双属性同步:
     - data-sb2-theme="dark" → sb2 壳 (--sb2-* 全套)
     - data-theme="dark"     → 老模块变量 + 聊天气泡等存量 [data-theme="dark"] 规则 */
  let effective = themeScheme;
  if (themeScheme === 'auto') {
    effective = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  }
  if (effective === 'dark') {
    root.setAttribute('data-theme', 'dark');
    root.setAttribute('data-sb2-theme', 'dark');
  } else {
    root.removeAttribute('data-theme');
    root.removeAttribute('data-sb2-theme');
  }
}
function initTheme() {
  const saved = localStorage.getItem('sb_theme');
  if (saved) themeScheme = saved;
  applyTheme();
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function () {
    if (themeScheme === 'auto') applyTheme();
  });
}

// Task Board
function toggleTaskBoard() {
  showBoard = !showBoard;
  document.getElementById('chatArea').style.display = showBoard ? 'none' : 'flex';
  document.getElementById('taskBoard').classList.toggle('show', showBoard);
}
function toggleTask(el) {
  el.classList.toggle('done');
  const cb = el.querySelector('.task-checkbox');
  if (el.classList.contains('done')) {
    cb.innerHTML = '✓';
    cb.style.background = 'var(--status-done)';
    cb.style.borderColor = 'var(--status-done)';
  } else {
    cb.innerHTML = '';
    cb.style.background = '';
    cb.style.borderColor = '';
  }
}

// Drawer
function openDrawer() {
  document.getElementById('drawerOverlay').classList.add('open');
  drawerOpen = true;
}
// Employee Detail Panel
var currentEmpId = null;
// 在用户主动点击"一键归纳"后，临时抑制顶部归纳提示条，避免关闭弹窗/切换标签页时反复出现
var inductBannerSuppressed = false;

// ============ Chat Credit Balance (dev/feat: #6) ============
// 后端: GET /api/credits/check?agent_id=xxx (solobrave-server.py:15206)
// 显示当前员工剩余积分; < 100 变红, = 0 灰 (管理员角度)
// 切换员工时调, 30 秒内缓存 (避免每次 UI 变化都打后端)
var _creditsCache = { empId: null, balance: null, ts: 0 };
function _renderChatCredits(balance) {
  var el = document.getElementById('chatHeaderCredits');
  if (!el) return;
  /* 〔dash/credit-checkin 2026-10-08 派单 B〕sb2 聊天顶栏芯片双写 (legacy chat-header-v2 在 sb2 壳下不可见,
     用户实际看到的顶栏是 .sb2-chat-topbar-*) */
  var sb2el = document.getElementById('sb2ChatTopbarCredits');
  var sb2num = document.getElementById('sb2ChatTopbarCreditsNum');
  if (balance === null || balance === undefined) {
    el.style.display = 'none';
    if (sb2el) sb2el.style.display = 'none';
    return;
  }
  var numEl = el.querySelector('.credit-num');
  el.classList.remove('low', 'zero');
  if (balance <= 0) {
    el.classList.add('zero');
    if (numEl) numEl.textContent = '0';
    if (sb2num) sb2num.textContent = '0';
    if (sb2el) sb2el.classList.add('zero');
  } else if (balance < 100) {
    el.classList.add('low');
    if (numEl) numEl.textContent = String(balance);
    if (sb2num) sb2num.textContent = String(balance);
    if (sb2el) sb2el.classList.remove('zero');
  } else {
    if (numEl) numEl.textContent = String(balance);
    if (sb2num) sb2num.textContent = String(balance);
    if (sb2el) sb2el.classList.remove('zero');
  }
  el.style.display = '';
  if (sb2el) sb2el.style.display = '';
  el.title = '剩余 ' + balance + ' 积分';
  if (sb2el) sb2el.title = '剩余 ' + balance + ' 积分, 点击打开积分仪表盘';
}
function loadChatCredits(empId, force) {
  if (!empId) return;
  // 30 秒内同员工不重打 (避免频繁切 tab 触发)
  var now = Date.now();
  if (!force && _creditsCache.empId === empId && now - _creditsCache.ts < 30000) {
    _renderChatCredits(_creditsCache.balance);
    return;
  }
  apiFetch('/api/credits/check?agent_id=' + encodeURIComponent(empId))
    .then(function (r) {
      if (!r || !r.ok) throw new Error('http ' + (r ? r.status : 'no'));
      return r.json();
    })
    .then(function (data) {
      _creditsCache = { empId: empId, balance: data.balance, ts: Date.now() };
      _renderChatCredits(data.balance);
    })
    .catch(function () {
      // 静默失败 — 头部不显示, 不打扰用户
      _renderChatCredits(null);
    });
}
function getCurrentEmpId() {
  // 员工详情页优先使用 currentEmpId，聊天页回退到 sb_current_emp
  return currentEmpId || localStorage.getItem('sb_current_emp');
}
function openEmpDetail(empId) {
  const emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (!emp) return;
  // ⑤c 脏状态: 打开新员工的面板时, 视为干净状态(避免上一个员工的脏数据干扰)
  if (typeof clearEmpDetailDirty === 'function') clearEmpDetailDirty();
  inductBannerSuppressed = false;
  currentEmpId = empId;
  // 切换员工时先清空记忆面板的旧数据，避免短暂显示上一位员工的记忆
  var memContentEl = document.getElementById('memoryContent');
  if (memContentEl) memContentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  var memEmptyEl = document.getElementById('memoryEmpty');
  if (memEmptyEl) memEmptyEl.style.display = 'none';
  var memTabBtn = document.querySelector('.emp-detail-tab[data-etab="memory"]');
  if (memTabBtn) memTabBtn.textContent = '记忆';
  selectedAvatar = emp.avatar || '🦞';
  selectedColor = emp.color || '#FF6B35';
  document.getElementById('empDetailAvatar').innerHTML = renderAvatar(emp, 56);
  document.getElementById('empDetailAvatar').style.background = `linear-gradient(135deg,${emp.color || '#FF6B35'},${emp.color || '#FF6B35'}dd)`;
  /* 〔r82 follow-up 14:34 批注②〕头图区已删, 这三个元素不存在, 空值守卫防 throw */
  var _edName = document.getElementById('empDetailName');
  if (_edName) _edName.textContent = emp.name;
  var _edRole = document.getElementById('empDetailRole');
  if (_edRole) _edRole.textContent = getEmpRoleDisplay(emp);
  var _edStatus = document.getElementById('empDetailStatus');
  if (_edStatus) _edStatus.innerHTML = `<span class="status-dot ${escapeAttr(emp.status || 'offline')}"></span> ${getStatusText(emp.status)}`;
  /* 〔r82 批注③〕身份卡同步填充(基础 tab 顶部) */
  var _idCardName = document.getElementById('empIdCardName');
  if (_idCardName) _idCardName.textContent = emp.name;
  var _idCardRole = document.getElementById('empIdCardRole');
  if (_idCardRole) _idCardRole.textContent = getEmpRoleDisplay(emp);
  document.getElementById('empDetailId').textContent = emp.id;
  document.getElementById('empDetailModel').textContent = emp.model || 'gpt-4o';
  document.getElementById('empDetailGroup').textContent = emp.group || emp.subCategory || '职能组';
  document.getElementById('empDetailSubCategory').textContent = emp.subCategory || emp.group || '技术团队';
  document.getElementById('empDetailStatusText').textContent = getStatusText(emp.status) || '在线';
  document.getElementById('empDetailCreated').textContent = emp.createdAt || emp.created || new Date().toLocaleDateString('zh-CN');
  // V3 升级:3 个 Math.random 编造 stats 已从 DOM 移除(零编造规则),改由 header 下方 stats 独立条统一从 /api/agents/:id/stats 取真实数据
  // 上下文进度条:解析 emp.ctx(总窗口,如 8K / 32K) + emp.tokenUsage(已用 tokens,无则 0 → 0% 显占位)
  // TODO: 后端目前没有 /api/agents/:id/token_usage 实时接口,等后端聚合 API 接入后,改用真实 usage
  // 零编造规则:没数据时不要瞎显百分比,fill=0% + -- 占位
  (function () {
    var totalStr = (emp && emp.ctx) || '8K';
    var total = 8 * 1024;
    var m = String(totalStr).match(/^(\d+)\s*([KkMm]?)$/);
    if (m) {
      var n = parseInt(m[1], 10);
      var u = (m[2] || '').toUpperCase();
      total = u === 'M' ? n * 1024 * 1024 : (u === 'K' ? n * 1024 : n);
    }
    var used = (emp && typeof emp.tokenUsage === 'number') ? emp.tokenUsage : 0;
    var pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
    var level = pct >= 90 ? 'critical' : (pct >= 70 ? 'high' : 'normal');
    var fillEl = document.getElementById('empDetailCtxFill');
    var textEl = document.getElementById('empDetailCtxText');
    if (fillEl) {
      fillEl.style.width = pct + '%';
      fillEl.setAttribute('data-level', level);
    }
    if (textEl) {
      var usedFmt = used > 0 ? (used >= 1024 ? (used / 1024).toFixed(1) + 'K' : used) : '--';
      var totalFmt = total >= 1024 * 1024 ? (total / (1024 * 1024)) + 'M' : (total / 1024) + 'K';
      textEl.textContent = used > 0 ? (usedFmt + ' / ' + totalFmt + ' · ' + pct + '%') : ('-- / ' + totalFmt);
    }
  })();
  document.getElementById('empDescriptionInput').value = emp.description || '';
  document.getElementById('empDetailDescription').textContent = emp.description ? '当前描述: ' + emp.description : '暂无描述';
  document.getElementById('empBadgeInput').value = emp.badge || '';
  document.getElementById('empBadgePreview').textContent = emp.badge ? '当前: ⚠️ ' + emp.badge : '';
  (document.getElementById('empModelConfig') || {}).value = emp.modelConfig || JSON.stringify({
    model: emp.model || 'gpt-4o',
    temperature: 0.7,
    max_tokens: 2000
  }, null, 2);
  (document.getElementById('empIdDoc') || {}).value = emp.idDoc || '';
  (document.getElementById('empSoulDoc') || {}).value = emp.soulDoc || '';
  (document.getElementById('empToolsDoc') || {}).value = emp.toolsDoc || '';
  (document.getElementById('empUserDoc') || {}).value = emp.userDoc || '';
  // Render avatar grid with AVATAR_PRESETS
  var avatarGrid = document.getElementById('avatarGrid');
  if (avatarGrid && typeof AVATAR_PRESETS !== 'undefined') {
    var avatarHtml = '';
    for (var i = 0; i < AVATAR_PRESETS.length; i++) {
      var isSelected = i === (emp.avatar || 0) ? ' selected' : '';
      avatarHtml += '<div class="avatar-option' + isSelected + '" data-avatar="' + i + '" onclick="selectAvatar(this)"><img src="' + AVATAR_PRESETS[i] + '" style="width:40px;height:40px;border-radius:50%;object-fit:cover;"></div>';
    }
    avatarGrid.innerHTML = avatarHtml;
  }
  // 确保overlay在body下
  var overlay = document.getElementById('empDetailOverlay');
  if (overlay && overlay.parentElement !== document.body) document.body.appendChild(overlay);
  overlay.classList.add('open');
  // V3 升级:并发拉 stats + activity(stats 填独立条,activity 填工作记录 tab)。失败各自显 --,不阻塞面板
  loadEmpStats(empId);
  loadEmpWorkActivity(empId);
  loadEmpConnect(emp);
  loadEmpDreaming(empId);
  switchEmpDetailTab('basic');
}

// ============================================================
// V3 升级配套函数(2026-09-10 评审 + Mac curl 后端 stats/activity 接口上线)
// ============================================================

// AI 员工详情头部"和 Helen 聊聊"按钮:关闭面板 → 切到 messages → 打开该员工聊天
function onEmpDetailChat() {
  if (!currentEmpId) return;
  closeEmpDetail();
  switchModule('messages');
  setTimeout(function () { openChat(currentEmpId); }, 50);
}

// "查看 systemPrompt"按钮:切到"连接"tab(现有连接 tab 有 systemPrompt 编辑)
function onEmpDetailShowPrompt() {
  if (!currentEmpId) return;
  switchEmpDetailTab('connect');
}

// 4 真实能力卡片点击:切到 messages + 打开聊天 + 预填 prompt
var _ABILITY_PROMPTS = {
  vision:    '上传一张达人主页截图,我帮你 OCR 识别 + 写入知识库',
  match:     '分析小菜菜和「运动鞋」的匹配度',
  selection: '基于本周已合作达人,推荐下一波选品(品类 + 价格带)',
  revalue:   '给搭配师W 重新评级'
};
function onAbilityClick(abilityKey) {
  if (!currentEmpId) return;
  var prompt = _ABILITY_PROMPTS[abilityKey] || '';
  closeEmpDetail();
  switchModule('messages');
  setTimeout(function () {
    openChat(currentEmpId);
    setTimeout(function () {
      var input = document.getElementById('msgInput');
      if (input && prompt) {
        input.value = prompt;
        if (typeof handleInput === 'function') handleInput();
        input.focus();
      }
    }, 100);
  }, 50);
}

// 并发拉 /api/agents/:id/stats → 填 4 个独立条
// 鉴权模式:照搬 L8260 / L8264-8272 apiFetch 的 token 获取 + Bearer 拼接方式(localStorage.sb_auth_token),
// 但**不用 apiFetch 封装**(其 401 会 showLogin + toast 强制跳登录页,本场景要求静默回退 --)。
function loadEmpStats(empId) {
  if (!empId) return;
  // 重置为占位(防止上一个员工的数据短暂残留)
  ['aiEmpStatTalents', 'aiEmpStatAnalyses', 'aiEmpStatTasks', 'aiEmpStatSuccess'].forEach(function (id) {
    var el = document.getElementById(id);
    if (el) {
      // 重置: 只保留 -- 数字(去掉「未采集」chip),加 placeholder class
      var chip = el.querySelector('.stat-uncaptured-chip');
      el.textContent = '--';
      if (chip) el.appendChild(chip);
      if (chip) chip.style.display = 'none';
      el.classList.add('placeholder');
    }
  });
  var token = localStorage.getItem('sb_auth_token');
  var headers = { 'Accept': 'application/json' };
  if (token && token !== 'null' && token !== 'local_mode') {
    headers['Authorization'] = 'Bearer ' + token;
  }
  fetch('/api/agents/' + encodeURIComponent(empId) + '/stats?window_days=30', { headers: headers, credentials: 'same-origin' })
    .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); })
    .then(function (resp) {
      if (resp.status === 200 && resp.body && resp.body.stats) {
        var s = resp.body.stats;
        var w = resp.body.window_days;
        // 零编造规则 v2: 值为 0 时统一兑底「未采集」chip
        // (前端无法区分「真零」vs「数据源没采集」, 保守按「未采集」显示, 避免数字误导)
        var setVal = function (id, v, isNull) {
          var el = document.getElementById(id);
          if (!el) return;
          var chip = el.querySelector('.stat-uncaptured-chip');
          var setNum = function (txt) {
            // 只更新第一个文本节点(--),不动 chip span
            if (el.firstChild && el.firstChild.nodeType === 3) {
              el.firstChild.nodeValue = txt;
            } else {
              el.textContent = txt;
              if (chip) el.appendChild(chip);
            }
          };
          if (isNull || v === null || v === undefined) {
            setNum('--');
            if (chip) chip.style.display = 'none';
            el.classList.add('placeholder');
          } else if (v === 0) {
            setNum('--');
            if (chip) chip.style.display = '';
            el.classList.add('placeholder');
          } else {
            setNum((typeof v === 'number' && v % 1 !== 0) ? v.toFixed(2) : String(v));
            if (chip) chip.style.display = 'none';
            el.classList.remove('placeholder');
          }
        };
        setVal('aiEmpStatTalents', s.talents_served, false);
        setVal('aiEmpStatAnalyses', s.analyses_done, false);
        setVal('aiEmpStatTasks', s.tasks_done, false);
        setVal('aiEmpStatSuccess', s.tool_success_rate, s.tool_success_rate === null);
        var winEl = document.getElementById('aiEmpStatWindow');
        if (winEl) winEl.textContent = w + 'd';
      } else {
        // 401/403/404/5xx 一律显 --,不弹错(不 showLogin / 不 toast,静默回退)
        console.warn('loadEmpStats 非 200:', resp.status, resp.body && resp.body.error);
      }
    })
    .catch(function (e) { console.warn('loadEmpStats 网络错误:', e); });
}

// 并发拉 /api/agents/:id/activity → 填工作记录 tab
// 鉴权模式同上(不用 apiFetch 封装,401 静默回退)
function loadEmpWorkActivity(empId) {
  if (!empId) return;
  var listEl = document.getElementById('aiEmpWorkList');
  if (listEl) listEl.innerHTML = '<div class="ai-emp-work-empty">加载中...</div>';
  var token = localStorage.getItem('sb_auth_token');
  var headers = { 'Accept': 'application/json' };
  if (token && token !== 'null' && token !== 'local_mode') {
    headers['Authorization'] = 'Bearer ' + token;
  }
  // 8s 硬超时:API 挂起时不要永远卡在「加载中...」,降级为「暂无工作记录」
  // (用户能区分「还在加载」vs「真的没数据」,而不是以为页面卡死)
  var timeoutId = setTimeout(function () {
    if (listEl && listEl.innerHTML.indexOf('加载中') !== -1) {
      listEl.innerHTML = '<div class="ai-emp-work-empty">暂无工作记录</div>';
    }
  }, 8000);
  fetch('/api/agents/' + encodeURIComponent(empId) + '/activity?days=30&limit=20', { headers: headers, credentials: 'same-origin' })
    .then(function (r) { return r.json().then(function (b) { return { status: r.status, body: b }; }); })
    .then(function (resp) {
      clearTimeout(timeoutId);
      if (resp.status === 200 && resp.body && Array.isArray(resp.body.items)) {
        renderEmpWorkList(resp.body.items);
      } else {
        // 静默回退:不弹错,只在 work-list 区域显占位(统一文案,不带调试后缀)
        if (listEl) {
          listEl.innerHTML = '<div class="ai-emp-work-empty">暂无工作记录</div>';
        }
        console.warn('loadEmpWorkActivity 非 200:', resp.status, resp.body && resp.body.error);
      }
    })
    .catch(function (e) {
      clearTimeout(timeoutId);
      // 静默回退:网络错误也只显「暂无工作记录」,不弹错
      if (listEl) listEl.innerHTML = '<div class="ai-emp-work-empty">暂无工作记录</div>';
      console.warn('loadEmpWorkActivity 网络错误:', e);
    });
}

function renderEmpWorkList(items) {
  var listEl = document.getElementById('aiEmpWorkList');
  if (!listEl) return;
  if (!items || items.length === 0) {
    // 空数据统一显「暂无工作记录」(去掉调试文本「后端 0 events」)
    listEl.innerHTML = '<div class="ai-emp-work-empty">暂无工作记录</div>';
    return;
  }
  var iconMap = { analysis: '分', follow_up: '跟', task: '完', deal: '合', ingest: '录' };
  var statusLabel = { active: '已完成', talking: '沟通中', pending: '待回', lost: '需介入', paused: '已暂停' };
  listEl.innerHTML = items.map(function (it) {
    var ic = iconMap[it.type] || '·';
    var cls = it.type === 'follow_up' ? 'followup' : (it.type === 'ingest' ? 'analysis' : it.type);
    var statusAttr = it.status ? ' data-status="' + escapeAttr(it.status) + '"' : '';
    var timeStr = it.ts ? new Date(it.ts * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
    var statusBadge = it.status ? '<span style="font-size:10px;color:var(--status-color, var(--color-text-tertiary));background:var(--status-bg, transparent);padding:2px 6px;border-radius:8px;margin-left:6px;">' + (statusLabel[it.status] || it.status) + '</span>' : '';
    var detail = it.detail ? '<div style="font-size:11px;color:var(--color-text-tertiary);margin-top:2px;">' + escapeHtml(it.detail) + '</div>' : '';
    return '<div class="ai-emp-work-item"' + statusAttr + '>'
      + '<div class="ai-emp-work-icon ' + cls + '">' + ic + '</div>'
      + '<div class="ai-emp-work-time">' + escapeHtml(timeStr) + '</div>'
      + '<div class="ai-emp-work-content">' + escapeHtml(it.title || '') + statusBadge + detail + '</div>'
      + '</div>';
  }).join('');
}

// ===== Dreaming 开关 =====
async function loadEmpDreaming(empId){
  var toggle = document.getElementById('empDreamingToggle');
  var status = document.getElementById('empDreamingStatus');
  if(!toggle || !status) return;
  toggle.checked = false;
  status.textContent = '';
  try{
    // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
    var result = await apiFetchWithRetry('/api/openclaw/dreaming?agentId=' + encodeURIComponent(empId));
    if(result.success && result.data){
      var data = result.data;
      toggle.checked = data.enabled || false;
      status.textContent = data.enabled ? '🌙 当前阶段: ' + (data.phase || 'light') : '';
    } else if(!result.success && result.error !== 'no_response') {
      console.warn('[Dreaming] 加载失败:', result.error);
    }
  } catch(e){
    console.error('[Dreaming] 加载异常:', e);
  }
}
async function toggleEmpDreaming(enabled){
  var empId = currentEmpId;
  if(!empId) return;
  var status = document.getElementById('empDreamingStatus');
  try{
    // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
    var result = await apiFetchWithRetry('/api/openclaw/dreaming', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({agentId: empId, enabled: enabled})
    });
    if(result.success && result.data){
      var data = result.data;
      if(status) status.textContent = data.enabled ? '🌙 当前阶段: ' + (data.phase || 'light') : '';
      showToast(data.enabled ? '🎭 Dreaming 已开启' : '🎭 Dreaming 已关闭');
    } else {
      showToast('❌ Dreaming 切换失败' + (result.error ? ': ' + result.error : ''));
      var toggle = document.getElementById('empDreamingToggle');
      if(toggle) toggle.checked = !enabled;
    }
  } catch(e){
    console.error('[Dreaming] 切换异常:', e);
    showToast('❌ 网络错误');
    var toggle = document.getElementById('empDreamingToggle');
    if(toggle) toggle.checked = !enabled;
  }
}

// 编辑资料：打开详情面板并切到文档编辑tab
function showEditModal(empId) {
  if (!empId) return;
  openEmpDetail(empId);
  setTimeout(function () {
    return switchEmpDetailTab('basic');
  }, 100);
}

// Skills System
const skillPresets = [{
  emoji: '🔍',
  name: '代码审查',
  level: 3
}, {
  emoji: '🎨',
  name: 'UI设计',
  level: 3
}, {
  emoji: '📝',
  name: '文档编写',
  level: 3
}, {
  emoji: '🐛',
  name: 'Bug修复',
  level: 3
}, {
  emoji: '🔗',
  name: 'API设计',
  level: 3
}, {
  emoji: '📊',
  name: '数据分析',
  level: 3
}, {
  emoji: '☁️',
  name: '云服务',
  level: 3
}, {
  emoji: '🔐',
  name: '安全',
  level: 3
}, {
  emoji: '🧪',
  name: '测试',
  level: 3
}, {
  emoji: '🚀',
  name: 'DevOps',
  level: 3
}, {
  emoji: '📱',
  name: '移动开发',
  level: 3
}, {
  emoji: '🌐',
  name: '前端开发',
  level: 3
}];
function renderSkills() {
  const list = document.getElementById('skillsList');
  const empty = document.getElementById('skillsEmpty');
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  const skills = emp && emp.skills || [];
  list.innerHTML = skills.length ? skills.map(function (s, i) {
    return `<div class="skill-item">
<div class="skill-emoji">${escapeHtml(s.emoji || '')}</div>
<div class="skill-info"><div class="skill-name">${escapeHtml(s.name || '')}</div>
<div class="skill-level">${[1, 2, 3, 4, 5].map(function (n) {
      return `<span class="skill-star ${n <= s.level ? 'filled' : ''}">★</span>`;
    }).join('')}</div></div>
<button class="skill-delete" onclick="deleteSkill(${i})">
<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
</button>
</div>`;
  }).join('') : '';
  empty.style.display = skills.length ? 'none' : 'block';
  list.style.display = skills.length ? 'flex' : 'none';
}
function renderSkillPresets() {
  const grid = document.getElementById('skillsPresets');
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  const used = emp && emp.skills && emp.skills.map ? emp.skills.map(function (s) {
    return s.name;
  }) : [];
  grid.innerHTML = skillPresets.filter(function (p) {
    return !used.includes(p.name);
  }).map(function (p) {
    return `<div class="skill-preset" onclick="addSkillPreset('${escapeAttr(p.emoji || '')}','${escapeAttr(p.name || '')}',${p.level || 1})">${escapeHtml(p.emoji || '')} ${escapeHtml(p.name || '')}</div>`;
  }).join('');
}
function addSkill() {
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (!emp) return;
  if (!emp.skills) emp.skills = [];
  const name = prompt('输入技能名称:');
  if (!name) return;
  const level = parseInt(prompt('技能等级 (1-5):', '3') || '3');
  const emoji = prompt('选择 emoji (默认 🎯):', '🎯') || '🎯';
  emp.skills.push({
    emoji,
    name,
    level
  });
  saveEmployees();
  renderSkills();
  renderSkillPresets();
  showToast('✅ 技能已添加');
}
function addSkillPreset(emoji, name, level) {
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (!emp) return;
  if (!emp.skills) emp.skills = [];
  if (emp.skills.find(function (s) {
    return s.name === name;
  })) return;
  emp.skills.push({
    emoji,
    name,
    level
  });
  saveEmployees();
  renderSkills();
  renderSkillPresets();
  showToast(`✅ ${name} 已添加`);
}
function deleteSkill(index) {
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (!emp || !emp.skills) return;
  const name = emp.skills[index].name;
  emp.skills.splice(index, 1);
  saveEmployees();
  renderSkills();
  renderSkillPresets();
  showToast(`✅ ${name} 已删除`);
}

// Memory System
let currentMemoryTab = 'core';
function formatTimeAgo(timestamp) {
  if (!timestamp) return '刚刚';
  var now = new Date();
  var then = new Date(timestamp);
  var diffMs = now.getTime() - then.getTime();
  var diffMin = Math.floor(diffMs / 60000);
  if (diffMin < 1) return '刚刚';
  if (diffMin < 60) return diffMin + '分钟前';
  var diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return diffHour + '小时前';
  var diffDay = Math.floor(diffHour / 24);
  return diffDay + '天前';
}
function switchMemoryTab(tab) {
  currentMemoryTab = tab;
  document.querySelectorAll('.memory-tab').forEach(function (t) {
    return t.classList.toggle('active', t.dataset.mtab === tab);
  });
  var empId = getCurrentEmpId();
  if (empId) renderMemoryTab(empId);
}
function renderMemoryTab(empId) {
  var contentEl = document.getElementById('memoryContent');
  var emptyEl = document.getElementById('memoryEmpty');
  var headerEl = document.querySelector('.memory-header .memory-title');
  if (!contentEl) return;

  // 显示加载中
  contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  if (emptyEl) emptyEl.style.display = 'none';

  // 从后端API加载知识库（v2 持久化）
  var kbPromise = typeof apiFetch === 'function' ?
    apiFetch('/api/knowledge').then(function (r) { return r.json(); }).catch(function () { return {docs: []}; }) :
    Promise.resolve({docs: []});

  // 从后端API加载记忆（v2 分池格式）
  if (typeof apiFetch === 'function') {
    var candidatePromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/core-candidates').then(function (r) { return r.json(); }).catch(function () { return {candidates: []}; }) :
      Promise.resolve({candidates: []});
    var conflictsPromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/conflicts').then(function (r) { return r.json(); }).catch(function () { return {conflicts: []}; }) :
      Promise.resolve({conflicts: []});
    var mergeHistoryPromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/merge-history').then(function (r) { return r.json(); }).catch(function () { return {merges: []}; }) :
      Promise.resolve({merges: []});
    // FIXME: 加载记忆汇总（二级归纳 + 三级知识库）
    var dailySummaryPromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/daily-summary').then(function (r) { return r.json(); }).catch(function () { return {summaries: []}; }) :
      Promise.resolve({summaries: []});
    var projectSummaryPromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/project-summary').then(function (r) { return r.json(); }).catch(function () { return {summaries: []}; }) :
      Promise.resolve({summaries: []});
    var agentKbPromise = typeof apiFetch === 'function' ?
      apiFetch('/api/memory/' + encodeURIComponent(empId) + '/knowledge').then(function (r) { return r.json(); }).catch(function () { return {entries: []}; }) :
      Promise.resolve({entries: []});
    Promise.all([
      apiFetch('/api/memory/' + encodeURIComponent(empId)).then(function (r) { return r.json(); }),
      kbPromise,
      candidatePromise,
      conflictsPromise,
      mergeHistoryPromise,
      dailySummaryPromise,
      projectSummaryPromise,
      agentKbPromise
    ]).then(function (results) {
      // 异步返回时若已切换到其他员工，丢弃这次过期结果，防止写回旧员工的记忆
      if (empId !== getCurrentEmpId()) return;
      var data = results[0];
      var kbResult = results[1] || {docs: []};
      var kbDocs = kbResult.docs || [];
      var candidateResult = results[2] || {candidates: []};
      var pendingCandidates = (candidateResult.candidates || []).filter(function (c) { return c.status === 'pending'; });
      var conflictsResult = results[3] || {conflicts: []};
      var conflictList = conflictsResult.conflicts || [];
      var mergeHistoryResult = results[4] || {merges: []};
      var mergeList = mergeHistoryResult.merges || [];
      var dailySummaryResult = results[5] || {summaries: []};
      var dailySummaries = dailySummaryResult.summaries || [];
      var projectSummaryResult = results[6] || {summaries: []};
      var projectSummaries = projectSummaryResult.summaries || [];
      var agentKbResult = results[7] || {entries: []};
      var agentKbEntries = agentKbResult.entries || [];

      // v2 分池格式：{core: [...], daily: [...], archive: [...]}
      var coreList = data.core || [];
      var dailyList = data.daily || [];
      var archiveList = data.archive || [];
      var coreCount = coreList.length;
      var dailyCount = dailyList.length;
      var archiveCount = archiveList.length;

      // 知识库：v3 已改为全局公共，员工详情页展示全部共享文档
      var kbCount = kbDocs.length;

      var coreTab = document.querySelector('.memory-tab[data-mtab="core"]');
      var dailyTab = document.querySelector('.memory-tab[data-mtab="daily"]');
      var archiveTab = document.querySelector('.memory-tab[data-mtab="archive"]');
      var summaryTab = document.querySelector('.memory-tab[data-mtab="summary"]');
      var kbTab = document.querySelector('.memory-tab[data-mtab="kb"]');
      var mergeTab = document.querySelector('.memory-tab[data-mtab="merge"]');
      if (coreTab) coreTab.textContent = '核心记忆 (' + coreCount + '/' + (data.config && data.config.core_max || 100) + ')';
      if (dailyTab) dailyTab.textContent = '日常记录 (' + dailyCount + '/' + (data.config && data.config.daily_max || 100) + ')';
      if (archiveTab) archiveTab.textContent = '归档 (' + archiveCount + ')';
      // FIXME: 记忆汇总 tab 显示待生成 + 已完成数量
      var summaryPendingCount = dailySummaries.concat(projectSummaries).filter(function (s) { return s.status === 'pending'; }).length;
      if (summaryTab) summaryTab.textContent = '记忆汇总 (' + (dailySummaries.length + projectSummaries.length) + (summaryPendingCount ? ' · ' + summaryPendingCount + '待生成' : '') + ')';
      if (kbTab) kbTab.textContent = '知识库 (' + kbCount + ')';
      if (mergeTab) mergeTab.textContent = '合并记录 (' + mergeList.length + ')';

      // FIXME: 大脑知识中枢：知识库 tab（原记忆汇总）
      if (currentMemoryTab === 'summary') {
        renderBrainKnowledgePanel(empId, contentEl, headerEl);
        return;
      }

      // 文档库模式：显示全局公共知识文档（后端持久化）
      if (currentMemoryTab === 'kb') {
        var kbHeaderHtml = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;">';
        kbHeaderHtml += '<span style="font-size:12px;color:var(--text-secondary);">全局公共知识库（所有员工共享）</span>';
        kbHeaderHtml += '<button onclick="openKnowledgeDocForm(null)" style="padding:6px 12px;border-radius:8px;background:var(--accent);color:#fff;border:none;font-size:12px;font-weight:600;cursor:pointer;">＋ 新建文档</button>';
        kbHeaderHtml += '</div>';
        if (kbDocs.length === 0) {
          contentEl.innerHTML = kbHeaderHtml + '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无知识文档，点击右上角新建全局共享文档</div>';
        } else {
          var kbHtml = kbHeaderHtml;
          kbDocs.forEach(function (d) {
            var content = d.content || '';
            var preview = content.length > 300 ? content.slice(0, 300) + '...' : content;
            kbHtml += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;position:relative;">';
            kbHtml += '<div class="memory-item-content" style="flex:1;">';
            kbHtml += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
            kbHtml += '<span style="font-size:11px;color:var(--accent);background:var(--accent-light);padding:2px 6px;border-radius:4px;">📚 知识库</span>';
            kbHtml += '<div style="display:flex;gap:6px;">';
            kbHtml += '<button onclick="event.stopPropagation();openKnowledgeDocForm(' + JSON.stringify(d).replace(/"/g, '&quot;') + ')" title="编辑" style="width:22px;height:22px;border-radius:5px;border:none;background:transparent;cursor:pointer;color:var(--text-secondary);font-size:13px;">✎</button>';
            kbHtml += '<button onclick="event.stopPropagation();openKnowledgeVersionModal(\'' + escapeAttr(d.id) + '\', \'' + escapeAttr(d.name || '未命名文档') + '\')" title="历史" style="width:22px;height:22px;border-radius:5px;border:none;background:transparent;cursor:pointer;color:var(--text-secondary);font-size:13px;">🕐</button>';
            kbHtml += '<button onclick="event.stopPropagation();deleteKnowledgeDoc(\'' + escapeAttr(d.id) + '\')" title="删除" style="width:22px;height:22px;border-radius:5px;border:none;background:transparent;cursor:pointer;color:#FF3B30;font-size:13px;">🗑</button>';
            kbHtml += '</div>';
            kbHtml += '</div>';
            kbHtml += '<div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">' + escapeHtml((d.icon || '📄') + ' ' + (d.name || '未命名文档')) + '</div>';
            kbHtml += '<div class="memory-item-text" style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(preview) + '</div>';
            kbHtml += '</div>';
            kbHtml += '</div>';
          });
          contentEl.innerHTML = kbHtml;
        }
        if (headerEl) {
          headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + (coreCount + dailyCount + kbCount) + ')</span>';
        }
        return;
      }

      // 合并记录模式
      if (currentMemoryTab === 'merge') {
        renderMergeHistory(empId);
        if (headerEl) {
          headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + (coreCount + dailyCount) + ')</span>';
        }
        return;
      }

      // 归档模式：显示已归档记忆（可恢复）
      if (currentMemoryTab === 'archive') {
        if (archiveList.length === 0) {
          contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无归档记忆，过期日常记录会自动归档到这里</div>';
        } else {
          var archHtml = '';
          var reasonColors = {
            'expired': '#8E8E93',
            'manual': '#1677ff',
            'consolidated': '#34C759',
            'capacity': '#FF9500'
          };
          var reasonLabels = {
            'expired': '过期归档',
            'manual': '手动归档',
            'consolidated': '归纳归档',
            'capacity': '容量归档'
          };
          archiveList.forEach(function (m) {
            var timeStr = m.time ? formatTimeAgo(new Date(m.time)) : '';
            var archivedTimeStr = m.archivedTime ? formatTimeAgo(new Date(m.archivedTime)) : '';
            var sourceLabel = m.source ? '<span style="font-size:11px;color:var(--text-secondary);background:var(--bg-tertiary);padding:2px 6px;border-radius:4px;margin-right:6px;">' + escapeHtml(m.source) + '</span>' : '';
            // v3：显示归档原因
            var reason = m.archiveReason || 'expired';
            var reasonColor = reasonColors[reason] || '#8E8E93';
            var reasonLabel = reasonLabels[reason] || '已归档';
            var reasonBadge = '<span style="font-size:10px;color:#fff;background:' + reasonColor + ';padding:1px 5px;border-radius:4px;margin-left:6px;">' + reasonLabel + '</span>';
            archHtml += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;opacity:0.85;">';
            archHtml += '<div class="memory-item-content" style="flex:1;">';
            archHtml += '<div style="margin-bottom:6px;">' + sourceLabel + reasonBadge + '</div>';
            archHtml += '<div class="memory-item-text" style="font-size:14px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(m.value || '') + '</div>';
            archHtml += '<div class="memory-item-date" style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">原始时间: ' + escapeHtml(timeStr) + ' · 归档时间: ' + escapeHtml(archivedTimeStr) + '</div>';
            archHtml += '</div>';
            archHtml += '<div class="memory-item-actions" style="display:flex;gap:4px;opacity:0;transition:opacity 0.2s;">';
            archHtml += '<button class="memory-action-btn" onclick="restoreMemory(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\')" title="恢复到日常记录" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--accent);display:flex;align-items:center;justify-content:center;">';
            archHtml += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 12h18"/><path d="M12 3v18"/><path d="M12 3l-4 4"/><path d="M12 3l4 4"/></svg>';
            archHtml += '</button>';
            archHtml += '<button class="memory-action-btn" onclick="deleteMemory(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\')" title="永久删除" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-tertiary);display:flex;align-items:center;justify-content:center;">';
            archHtml += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
            archHtml += '</button>';
            archHtml += '</div>';
            archHtml += '</div>';
          });
          contentEl.innerHTML = archHtml;
          // 添加hover效果
          var archItems = contentEl.querySelectorAll('.memory-item');
          archItems.forEach(function (item) {
            item.addEventListener('mouseenter', function () {
              var actions = this.querySelector('.memory-item-actions');
              if (actions) actions.style.opacity = '1';
            });
            item.addEventListener('mouseleave', function () {
              var actions = this.querySelector('.memory-item-actions');
              if (actions) actions.style.opacity = '0';
            });
          });
        }
        if (headerEl) {
          headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + (coreCount + dailyCount) + ')</span>';
        }
        return;
      }

      // 过滤记忆：按 currentMemoryTab 从对应分池取
      var filtered = currentMemoryTab === 'core' ? coreList : dailyList;

      // 更新记忆总数 + 容量使用率
      if (headerEl) {
        var usageStr = '';
        if (data.config) {
          var corePct = Math.round((coreCount / data.config.core_max) * 100);
          var dailyPct = Math.round((dailyCount / data.config.daily_max) * 100);
          usageStr = ' <span style="font-size:11px;color:var(--text-tertiary);">core ' + corePct + '% · daily ' + dailyPct + '%</span>';
        }
        headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + (coreCount + dailyCount) + ')</span>' + usageStr;
      }
      if (filtered.length === 0) {
        contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">' + (currentMemoryTab === 'core' ? '暂无核心记忆，手动添加的记忆会显示在这里' : '暂无日常记录，AI自动提取的记忆会显示在这里') + '</div>';
        return;
      }
      var html = '';
      var nowMs = Date.now();
      filtered.forEach(function (m) {
        var timeStr = m.time ? formatTimeAgo(new Date(m.time)) : '';
        var sourceLabel = m.source ? '<span style="font-size:11px;color:var(--accent);background:var(--accent-light);padding:2px 6px;border-radius:4px;margin-right:6px;">' + escapeHtml(m.source) + '</span>' : '';
        var isAutoExtract = m.key === 'auto_extract' || m.key === 'auto';
        var isCore = currentMemoryTab === 'core';

        // v3 新增：核心记忆显示优先级和标签
        var metaBadges = '';
        if (isCore) {
          var pri = m.priority || 5;
          var fire = '🔥'.repeat(Math.min(pri, 5));
          metaBadges += '<span style="font-size:11px;color:#FF6B35;margin-right:6px;" title="优先级 ' + pri + '/10">' + fire + '</span>';
          var tags = m.tags || [];
          tags.forEach(function (t) {
            metaBadges += '<span style="font-size:10px;color:var(--text-secondary);background:var(--bg-tertiary);padding:1px 5px;border-radius:4px;margin-right:4px;">' + escapeHtml(t) + '</span>';
          });
        }

        // 三期新增：冲突徽标
        if (m.conflictStatus === 'conflict') {
          metaBadges += '<span style="font-size:10px;color:#fff;background:#FF3B30;padding:1px 5px;border-radius:4px;margin-left:6px;cursor:pointer;" onclick="event.stopPropagation();openMemoryConflictModal()" title="点击处理冲突">⚠️ 冲突</span>';
        }

        // v3 新增：日常记录显示过期倒计时（使用 expiresAt）和 context
        var expireBadge = '';
        var contextHint = '';
        if (!isCore && m.expiresAt) {
          var remainMs = m.expiresAt - nowMs;
          if (remainMs <= 0) {
            expireBadge = '<span style="font-size:10px;color:#fff;background:#FF3B30;padding:1px 5px;border-radius:4px;margin-left:6px;">已过期</span>';
          } else if (remainMs < 7 * 24 * 3600 * 1000) {
            var remainDays = Math.ceil(remainMs / (24 * 3600 * 1000));
            expireBadge = '<span style="font-size:10px;color:#fff;background:#FF9500;padding:1px 5px;border-radius:4px;margin-left:6px;">' + remainDays + '天后过期</span>';
          }
          if (m.context) {
            contextHint = '<div style="font-size:11px;color:var(--text-tertiary);margin-top:4px;padding:4px 8px;background:var(--bg-tertiary);border-radius:6px;">💬 ' + escapeHtml(m.context) + '</div>';
          }
        }

        html += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;">';
        html += '<div class="memory-item-content" style="flex:1;">';
        html += '<div style="margin-bottom:6px;">' + sourceLabel + metaBadges + expireBadge + '</div>';
        html += '<div class="memory-item-text" id="memText_' + escapeAttr(m.id) + '" style="font-size:14px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(m.value || '') + '</div>';
        html += contextHint;
        html += '<div class="memory-item-edit" id="memEdit_' + escapeAttr(m.id) + '" style="display:none;">';
        html += '<textarea class="memory-edit-input" id="memInput_' + escapeAttr(m.id) + '" style="width:100%;min-height:60px;padding:8px;border:1px solid var(--accent);border-radius:8px;background:var(--bg-primary);color:var(--text-primary);font-size:14px;line-height:1.5;resize:vertical;box-sizing:border-box;" onkeydown="if(event.key===\'Enter\'&&!event.shiftKey){event.preventDefault();saveMemoryEdit(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\');}">' + escapeHtml(m.value || '') + '</textarea>';
        html += '<div style="display:flex;gap:6px;margin-top:6px;">';
        html += '<button class="memory-add-btn" onclick="saveMemoryEdit(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\')" style="padding:4px 12px;font-size:12px;">✅ 保存</button>';
        html += '<button class="memory-add-btn" onclick="cancelMemoryEdit(\'' + escapeAttr(m.id) + '\')" style="padding:4px 12px;font-size:12px;background:var(--bg-tertiary);color:var(--text-secondary);">取消</button>';
        html += '</div>';
        html += '</div>';
        html += '<div class="memory-item-date" style="font-size:11px;color:var(--text-tertiary);margin-top:6px;">' + escapeHtml(timeStr) + '</div>';
        html += '</div>';
        html += '<div class="memory-item-actions" style="display:flex;gap:4px;opacity:0;transition:opacity 0.2s;">';
        html += '<button class="memory-action-btn" onclick="startMemoryEdit(\'' + escapeAttr(m.id) + '\')" title="编辑" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--accent);display:flex;align-items:center;justify-content:center;">';
        html += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>';
        html += '</button>';
        // 日常记录显示"升级为核心记忆"按钮
        if (isAutoExtract) {
          html += '<button class="memory-action-btn" onclick="promoteToCore(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\')" title="升级为核心记忆" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--accent);display:flex;align-items:center;justify-content:center;">';
          html += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
          html += '</button>';
        }
        // FIXME: 记忆项操作增加“存知识库”手动标记
        html += '<button class="memory-action-btn" onclick="markMemoryToKnowledge(\'' + escapeAttr(empId) + '\', \'' + escapeAttr(m.id) + '\', ' + JSON.stringify(m.value || '').replace(/"/g, '&quot;') + ')" title="存知识库" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--accent);display:flex;align-items:center;justify-content:center;">📚</button>';
        html += '<button class="memory-action-btn" onclick="deleteMemory(\'' + escapeAttr(empId) + '\',\'' + escapeAttr(m.id) + '\')" title="删除" style="width:28px;height:28px;border-radius:6px;background:transparent;border:none;cursor:pointer;color:var(--text-tertiary);display:flex;align-items:center;justify-content:center;">';
        html += '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>';
        html += '</button>';
        html += '</div>';
        html += '</div>';
      });
      contentEl.innerHTML = html;

      // 添加hover效果
      var items = contentEl.querySelectorAll('.memory-item');
      items.forEach(function (item) {
        item.addEventListener('mouseenter', function () {
          var actions = this.querySelector('.memory-item-actions');
          if (actions) actions.style.opacity = '1';
        });
        item.addEventListener('mouseleave', function () {
          var actions = this.querySelector('.memory-item-actions');
          if (actions) actions.style.opacity = '0';
        });
      });

      // 根据后端 shouldConsolidate 状态显示"建议归纳"提示条
      var banner = document.getElementById('memorySummarizeBanner');
      if (banner) {
        banner.style.display = data.shouldConsolidate ? 'flex' : 'none';
        banner.dataset.sourceIds = JSON.stringify(data.suggestedSourceIds || []);
      }

      // 二期：核心记忆候选提示条
      var candBanner = document.getElementById('memoryCandidateBanner');
      var candText = document.getElementById('memoryCandidateBannerText');
      if (candBanner) {
        candBanner.style.display = pendingCandidates.length > 0 ? 'flex' : 'none';
        if (candText) candText.textContent = '发现 ' + pendingCandidates.length + ' 条核心记忆候选，待你确认';
      }

      // 二期：知识库自动归纳提示条（使用后端计算的 shouldInductKnowledge）
      var inductBanner = document.getElementById('memoryInductBanner');
      if (inductBanner) {
        // 用户已在本页点击过归纳并看到结果，暂时抑制提示条，避免反复打扰
        inductBanner.style.display = (!inductBannerSuppressed && data.shouldInductKnowledge) ? 'flex' : 'none';
      }

      // 三期：记忆冲突提示条
      var conflictBanner = document.getElementById('memoryConflictBanner');
      var conflictText = document.getElementById('memoryConflictBannerText');
      if (conflictBanner) {
        conflictBanner.style.display = conflictList.length > 0 ? 'flex' : 'none';
        if (conflictText) conflictText.textContent = '发现 ' + conflictList.length + ' 条核心记忆存在冲突，请点击确认处理';
      }
    }).catch(function (e) {
      contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败，请重试</div>';
      console.warn('[Memory] 加载失败:', e);
    });
  } else {
    contentEl.innerHTML = '';
    if (emptyEl) emptyEl.style.display = 'block';
  }
}

// 三期：渲染去重合并记录列表
function renderMergeHistory(empId) {
  var contentEl = document.getElementById('memoryContent');
  if (!contentEl) return;
  contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/merge-history').then(function (r) { return r.json(); }).then(function (data) {
    var merges = data.merges || [];
    if (merges.length === 0) {
      contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无合并记录</div>';
      return;
    }
    var html = '';
    merges.forEach(function (m) {
      var timeStr = m.timestamp ? formatDate(m.timestamp) : '-';
      var oldValue = m.oldValue || '';
      var newValue = m.newValue || '';
      var sourceIds = m.sourceIds || [];
      var oldPreview = oldValue.length > 80 ? oldValue.slice(0, 80) + '...' : oldValue;
      var newPreview = newValue.length > 80 ? newValue.slice(0, 80) + '...' : newValue;
      html += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;">';
      html += '<div style="font-size:11px;color:var(--text-tertiary);margin-bottom:6px;">' + escapeHtml(timeStr) + '</div>';
      if (sourceIds.length) {
        html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">合并来源 ID: ' + escapeHtml(sourceIds.join(', ')) + '</div>';
      }
      html += '<div style="margin-bottom:6px;">';
      html += '<span style="font-size:11px;color:var(--text-secondary);background:var(--bg-tertiary);padding:2px 6px;border-radius:4px;margin-right:6px;">合并前</span>';
      html += '<span style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(oldPreview) + '</span>';
      html += '</div>';
      html += '<div>';
      html += '<span style="font-size:11px;color:#fff;background:var(--accent);padding:2px 6px;border-radius:4px;margin-right:6px;">合并后</span>';
      html += '<span style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(newPreview) + '</span>';
      html += '</div>';
      html += '</div>';
    });
    contentEl.innerHTML = html;
  }).catch(function (e) {
    if (contentEl) contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败，请重试</div>';
    console.warn('[MergeHistory] 加载失败:', e);
  });
}

// FIXME: 大脑知识中枢：全局状态
var currentBrainTopicId = null;
var _brainRefreshTimer = null;

// FIXME: 大脑知识中枢：渲染知识库 tab
function renderBrainKnowledgePanel(empId, contentEl, headerEl) {
  if (!contentEl) return;
  contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">大脑加载中...</div>';

  var statusPromise = apiFetch('/api/brain/status').then(function (r) { return r.json(); }).catch(function () { return {}; });
  var topicsPromise = apiFetch('/api/brain/topics?empId=' + encodeURIComponent(empId)).then(function (r) { return r.json(); }).catch(function () { return {topics: []}; });
  var memPromise = apiFetch('/api/memory/' + encodeURIComponent(empId) + '?limit=200').then(function (r) { return r.json(); }).catch(function () { return {core: [], daily: []}; });

  Promise.all([statusPromise, topicsPromise, memPromise]).then(function (results) {
    var status = results[0] || {};
    var topics = (results[1].topics || []);
    var memData = results[2] || {};
    var allMemories = [].concat(memData.core || [], memData.daily || []);

    // 顶部状态栏
    var html = '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px;flex-wrap:wrap;gap:8px;">';
    html += '<div style="font-size:13px;color:var(--text-secondary);">';
    html += '🧠 大脑运行中 · 待处理 ' + (status.pendingClean || 0) + ' 条 · 已沉淀 ' + (status.knowledgeCount || 0) + ' 条知识 · ' + (status.topicCount || 0) + ' 个主题';
    html += '</div>';
    html += '<button onclick="triggerBrainManual()" style="padding:6px 12px;border-radius:8px;background:var(--accent);color:#fff;border:none;font-size:12px;font-weight:600;cursor:pointer;">⚡ 立即归纳</button>';
    html += '</div>';

    html += '<div style="display:flex;gap:12px;height:480px;">';
    // 左侧主题列表
    html += '<div style="width:260px;flex-shrink:0;overflow-y:auto;padding-right:4px;">';
    if (topics.length === 0) {
      html += '<div style="padding:16px;text-align:center;color:var(--text-tertiary);font-size:12px;">暂无主题，添加记忆后大脑会自动聚类</div>';
    }
    topics.forEach(function (t) {
      var active = currentBrainTopicId === t.id;
      var lastStr = t.lastActiveAt ? _timeAgo(t.lastActiveAt) : '-';
      var summary = (t.keyWords || []).slice(0, 3).join(' · ') || '暂无摘要';
      html += '<div onclick="selectBrainTopic(\'' + escapeAttr(t.id) + '\')" style="cursor:pointer;padding:10px;border-radius:10px;margin-bottom:8px;background:' + (active ? 'var(--accent-light)' : 'var(--bg-secondary)') + ';border:1px solid ' + (active ? 'var(--accent)' : 'transparent') + ';">';
      html += '<div style="font-size:13px;font-weight:600;color:var(--text-primary);margin-bottom:4px;">' + escapeHtml(t.title || '未命名主题') + '</div>';
      html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:4px;">' + (t.memCount || 0) + ' 条记忆 · 最后活跃 ' + lastStr + '</div>';
      html += '<div style="font-size:11px;color:var(--text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(summary) + '</div>';
      html += '</div>';
    });
    html += '</div>';

    // 右侧知识条目
    html += '<div id="brainKnowledgeRight" style="flex:1;overflow-y:auto;padding-left:12px;border-left:1px solid var(--bg-tertiary);">';
    if (!currentBrainTopicId || topics.length === 0) {
      html += '<div style="padding:40px 20px;text-align:center;color:var(--text-tertiary);font-size:13px;">点击左侧主题查看沉淀知识</div>';
    } else {
      html += '<div id="brainKnowledgeContent" style="padding:4px;"><div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div></div>';
    }
    html += '</div>';
    html += '</div>';

    contentEl.innerHTML = html;

    if (headerEl) {
      headerEl.innerHTML = '🧠 记忆 <span style="font-size:12px;font-weight:500;color:var(--text-tertiary);">(' + (memData.core ? memData.core.length : 0) + '/' + (memData.daily ? memData.daily.length : 0) + ')</span>';
    }

    if (currentBrainTopicId) {
      loadBrainKnowledgeForTopic(empId, currentBrainTopicId, allMemories);
    }
  }).catch(function (e) {
    contentEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">大脑加载失败</div>';
    console.warn('[Brain] load failed:', e);
  });
}

function selectBrainTopic(topicId) {
  currentBrainTopicId = topicId;
  var empId = localStorage.getItem('sb_current_emp');
  if (empId) renderMemoryTab(empId);
}

function loadBrainKnowledgeForTopic(empId, topicId, allMemories) {
  var rightEl = document.getElementById('brainKnowledgeContent');
  if (!rightEl) return;
  apiFetch('/api/brain/knowledge?topicId=' + encodeURIComponent(topicId)).then(function (r) { return r.json(); }).then(function (data) {
    var items = data.knowledge || [];
    if (items.length === 0) {
      rightEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);font-size:12px;">该主题尚未沉淀知识</div>';
      return;
    }
    var html = '';
    items.forEach(function (k) {
      var evidenceIds = k.evidenceMemIds || [];
      var confidence = Math.round((k.confidence || 0.5) * 100);
      var evidenceCount = evidenceIds.length;
      html += '<div class="memory-item" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px;">';
      html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px;">';
      html += '<span style="font-size:14px;font-weight:600;color:var(--text-primary);">' + escapeHtml(k.title || '未命名') + '</span>';
      html += '<span style="font-size:10px;color:#fff;background:var(--accent);padding:2px 8px;border-radius:4px;">可信度 ' + confidence + '%</span>';
      html += '</div>';
      html += '<div style="font-size:12px;line-height:1.6;color:var(--text-primary);white-space:pre-wrap;margin-bottom:8px;">' + escapeHtml(k.content || '') + '</div>';
      var kp = k.keyPoints || [];
      if (kp.length) {
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">';
        kp.slice(0, 6).forEach(function (p) {
          html += '<span style="font-size:11px;color:var(--accent);background:var(--accent-light);padding:2px 8px;border-radius:4px;">' + escapeHtml(p) + '</span>';
        });
        html += '</div>';
      }
      html += '<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">';
      html += '<div style="flex:1;height:4px;background:var(--bg-tertiary);border-radius:2px;"><div style="width:' + confidence + '%;height:100%;background:var(--accent);border-radius:2px;"></div></div>';
      html += '<span style="font-size:11px;color:var(--text-secondary);white-space:nowrap;">证据 ' + evidenceCount + ' 条</span>';
      html += '</div>';
      html += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
      html += '<button onclick="toggleBrainEvidence(this, \'' + escapeAttr(k.id) + '\', ' + JSON.stringify(evidenceIds).replace(/"/g, '&quot;') + ')" style="padding:4px 10px;border-radius:6px;background:var(--bg-tertiary);color:var(--text-secondary);border:none;font-size:11px;cursor:pointer;">查看证据</button>';
      html += '<button onclick="brainKnowledgeFeedback(\'' + escapeAttr(k.id) + '\', true)" style="padding:4px 10px;border-radius:6px;background:var(--bg-tertiary);color:#34C759;border:none;font-size:11px;cursor:pointer;">👍 准确</button>';
      html += '<button onclick="brainKnowledgeFeedback(\'' + escapeAttr(k.id) + '\', false)" style="padding:4px 10px;border-radius:6px;background:var(--bg-tertiary);color:#FF3B30;border:none;font-size:11px;cursor:pointer;">👎 有误</button>';
      html += '</div>';
      html += '<div class="brain-evidence-box" data-kid="' + escapeAttr(k.id) + '" style="display:none;margin-top:10px;padding:10px;background:var(--bg-tertiary);border-radius:8px;"></div>';
      html += '</div>';
    });
    rightEl.innerHTML = html;
    rightEl.dataset.memories = JSON.stringify(allMemories || []);
  }).catch(function (e) {
    rightEl.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">知识加载失败</div>';
    console.warn('[Brain] knowledge load failed:', e);
  });
}

function toggleBrainEvidence(btn, knowledgeId, evidenceIds) {
  var box = document.querySelector('.brain-evidence-box[data-kid="' + knowledgeId + '"]');
  if (!box) return;
  if (box.style.display === 'block') {
    box.style.display = 'none';
    btn.textContent = '查看证据';
    return;
  }
  var rightEl = document.getElementById('brainKnowledgeContent');
  var allMemories = [];
  try { allMemories = JSON.parse(rightEl.dataset.memories || '[]'); } catch (e) {}
  var idSet = {};
  evidenceIds.forEach(function (id) { idSet[id] = true; });
  var evidence = allMemories.filter(function (m) { return idSet[m.id]; });
  var html = '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:6px;">原始证据记忆：</div>';
  if (evidence.length === 0) {
    html += '<div style="font-size:12px;color:var(--text-tertiary);">未找到对应记忆</div>';
  } else {
    evidence.forEach(function (m) {
      html += '<div style="padding:6px 8px;background:var(--bg-secondary);border-radius:6px;margin-bottom:6px;font-size:12px;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(m.value || '') + '</div>';
    });
  }
  box.innerHTML = html;
  box.style.display = 'block';
  btn.textContent = '收起证据';
}

function brainKnowledgeFeedback(knowledgeId, accurate) {
  apiFetch('/api/brain/knowledge/' + encodeURIComponent(knowledgeId) + '/feedback', {
    method: 'POST',
    body: JSON.stringify({accurate: accurate})
  }).then(function (r) { return r.json(); }).then(function () {
    showToast(accurate ? '✅ 已标记准确' : '⚠️ 已标记有误');
  }).catch(function (e) {
    showToast('反馈失败');
    console.warn('[Brain] feedback failed:', e);
  });
}

function triggerBrainManual() {
  var empId = localStorage.getItem('sb_current_emp');
  if (!empId) {
    showToast('⚠️ 请先选择一个员工');
    return;
  }
  apiFetch('/api/brain/trigger-manual', {method: 'POST'}).then(function (r) { return r.json(); }).then(function (data) {
    showToast('⚡ 清洗 ' + (data.enqueuedClean || 0) + ' / 归类 ' + (data.enqueuedClassify || 0) + ' / 沉淀 ' + (data.enqueuedInduct || 0));
    startBrainRefreshPolling();
  }).catch(function (e) {
    showToast('触发失败');
    console.warn('[Brain] manual trigger failed:', e);
  });
}

function startBrainRefreshPolling() {
  if (_brainRefreshTimer) clearInterval(_brainRefreshTimer);
  var count = 0;
  _brainRefreshTimer = setInterval(function () {
    var empId = localStorage.getItem('sb_current_emp');
    if (empId && currentMemoryTab === 'summary') renderMemoryTab(empId);
    count += 1;
    if (count >= 12) {
      clearInterval(_brainRefreshTimer);
      _brainRefreshTimer = null;
    }
  }, 5000);
}

// FIXME: 大脑知识中枢：每 30 秒刷新一次状态
registerTimerTask(function () {
  if (currentMemoryTab === 'summary') {
    var empId = localStorage.getItem('sb_current_emp');
    if (empId) renderMemoryTab(empId);
  }
}, 30);

// FIXME: 记忆三级沉淀：前端辅助函数
function _todayStr() {
  var d = new Date();
  return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

// FIXME: 构造归纳 prompt，要求 AI 输出指定 JSON 结构
function _buildSummaryPrompt(type, title, memories) {
  var lines = memories.map(function (m, idx) {
    return (idx + 1) + '. [' + (m.id || '?') + '] ' + (m.value || m.content || '');
  }).join('\n');
  var base = '你是 SoloBrave 的记忆归纳助手。请基于以下原始记忆，生成一份结构化的中文归纳总结。\n\n';
  base += '主题：' + (title || '') + '\n\n原始记忆：\n' + lines + '\n\n';
  base += '请严格返回如下 JSON 对象（不要包含任何额外说明）：\n';
  base += '{\n';
  base += '  "title": "项目/主题名称",\n';
  base += '  "status": "当前状态（如：进行中/已确认/待推进）",\n';
  base += '  "keyPoints": ["关键信息摘要1", "关键信息摘要2", "关键信息摘要3"],\n';
  base += '  "decisions": ["已确认决策1"],\n';
  base += '  "pending": ["待确认事项1"],\n';
  base += '  "actionItems": [{"item":"行动项","owner":"负责人","due":"时间"}]\n';
  base += '}\n';
  return base;
}

// FIXME: 使用低优先级 OpenClaw 队列生成归纳内容并保存到后端
function _generateSummaryWithAI(empId, summaryId, type, existingSummary) {
  if (!empId) return;
  var key = 'summary_' + (summaryId || '');
  if (window._summaryGenerating && window._summaryGenerating[key]) return;
  window._summaryGenerating = window._summaryGenerating || {};
  window._summaryGenerating[key] = true;

  var dateStr = (existingSummary && existingSummary.date) ? existingSummary.date : _todayStr();
  var projectName = (existingSummary && existingSummary.projectName) ? existingSummary.projectName : '';
  var title = (existingSummary && existingSummary.title) ? existingSummary.title : '归纳';
  var memIds = (existingSummary && existingSummary.relatedMemIds) || [];

  // 拉取源记忆（按 ID 或按日期/关键词）
  var fetchPromise;
  if (memIds.length) {
    fetchPromise = apiFetch('/api/memory/' + encodeURIComponent(empId) + '?limit=200').then(function (r) { return r.json(); });
  } else if (type === 'daily') {
    fetchPromise = apiFetch('/api/memory/' + encodeURIComponent(empId) + '?type=daily&limit=200').then(function (r) { return r.json(); });
  } else {
    fetchPromise = apiFetch('/api/memory/' + encodeURIComponent(empId) + '?keyword=' + encodeURIComponent(projectName || title) + '&limit=200').then(function (r) { return r.json(); });
  }

  fetchPromise.then(function (data) {
    var all = [].concat(data.core || [], data.daily || []);
    var memories = all;
    if (memIds.length) {
      var idSet = {};
      memIds.forEach(function (id) { idSet[id] = true; });
      memories = all.filter(function (m) { return idSet[m.id]; });
    }
    if (memories.length === 0) {
      showToast('⚠️ 没有可用于归纳的源记忆');
      delete window._summaryGenerating[key];
      return;
    }
    var sessionKey = 'agent:' + empId + ':memory_summary';
    var prompt = _buildSummaryPrompt(type, title, memories);
    _sendChatWaitForLifecycle(sessionKey, prompt, {}, { priority: 0 }).then(function (res) {
      var text = (res && res.reply) || '';
      var parsed = null;
      try {
        var match = text.match(/\{[\s\S]*\}/);
        parsed = JSON.parse(match ? match[0] : text);
      } catch (e) {
        console.warn('[SummaryAI] JSON 解析失败，使用原始文本:', e);
      }
      var summary = {
        id: summaryId,
        empId: empId,
        summaryType: type,
        title: parsed && parsed.title ? parsed.title : title,
        date: type === 'daily' ? dateStr : undefined,
        projectName: type === 'project' ? (projectName || title.replace('项目归纳：', '')) : undefined,
        status: 'active',  // 统一用 active 表示已生成，前端据此显示"已完成"
        keyPoints: (parsed && parsed.keyPoints) || [],
        decisions: (parsed && parsed.decisions) || [],
        pending: (parsed && parsed.pending) || [],
        actionItems: (parsed && parsed.actionItems) || [],
        relatedMemIds: memIds,
        sourceMemIds: memIds
      };
      return apiFetch('/api/memory/' + encodeURIComponent(empId) + '/trigger-summary', {
        method: 'POST',
        body: JSON.stringify({ summary: summary })
      });
    }).then(function () {
      showToast('✅ 归纳已生成');
      renderMemoryTab(empId);
    }).catch(function (e) {
      console.error('[SummaryAI] 生成失败:', e);
      showToast('⚠️ 归纳生成失败');
    }).finally(function () {
      delete window._summaryGenerating[key];
    });
  }).catch(function (e) {
    console.error('[SummaryAI] 加载记忆失败:', e);
    delete window._summaryGenerating[key];
  });
}

// FIXME: 手动触发归纳（按钮入口）
function triggerMemorySummary(empId, type, param) {
  if (!empId) {
    empId = getCurrentEmpId();
  }
  if (!empId) {
    showToast('⚠️ 请先选择一个员工');
    return;
  }
  var title, date, project;
  if (type === 'daily') {
    date = _todayStr();
    title = date + ' 每日归纳';
  } else {
    project = param || prompt('请输入项目名称/主题');
    if (!project) return;
    title = '项目归纳：' + project;
  }
  var summary = {
    empId: empId,
    summaryType: type,
    title: title,
    date: date,
    projectName: project,
    status: 'pending',
    relatedMemIds: [],
    sourceMemIds: []
  };
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/trigger-summary', {
    method: 'POST',
    body: JSON.stringify({ summary: summary })
  }).then(function (r) { return r.json(); }).then(function (res) {
    var sid = res.summaryId || summary.id;
    _generateSummaryWithAI(empId, sid, type, summary);
    renderMemoryTab(empId);
  }).catch(function (e) {
    console.error('[SummaryTrigger] 创建归纳失败:', e);
    showToast('⚠️ 创建归纳失败');
  });
}

// FIXME: 自动为所有 pending 归纳排队生成（低优先级）
function fillPendingSummaries(empId, dailySummaries, projectSummaries) {
  var pending = [].concat(dailySummaries, projectSummaries).filter(function (s) { return s.status === 'pending'; });
  pending.forEach(function (s) {
    _generateSummaryWithAI(empId, s.id, s.summaryType, s);
  });
}

// FIXME: 手动把单条记忆标记为三级知识库
function markMemoryToKnowledge(empId, memId, value) {
  if (!empId || !value) return;
  var title = value.slice(0, 40) + (value.length > 40 ? '...' : '');
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/knowledge', {
    method: 'POST',
    body: JSON.stringify({ memId: memId, title: '知识点：' + title, content: value, source: 'manual', status: 'active' })
  }).then(function (r) { return r.json(); }).then(function () {
    showToast('✅ 已存入知识库');
  }).catch(function (e) {
    console.error('[MarkKnowledge] 失败:', e);
    showToast('⚠️ 存入知识库失败');
  });
}

// FIXME: 每日凌晨 3 点自动为当前员工触发每日归纳
function _scheduleDailyMemorySummary() {
  if (window._dailySummaryScheduled) return;
  window._dailySummaryScheduled = true;
  registerTimerTask(function () {
    var d = new Date();
    if (d.getHours() === 3 && d.getMinutes() === 0) {
      var last = localStorage.getItem('sb_daily_summary_date');
      var today = _todayStr();
      if (last !== today) {
        var empId = localStorage.getItem('sb_current_emp');
        if (empId) {
          // 仅当日常记录 >=2 条时才自动触发每日归纳
          apiFetch('/api/memory/' + encodeURIComponent(empId)).then(function (r) { return r.json(); }).then(function (data) {
            var dailyCount = (data.daily || []).length;
            if (dailyCount >= 2) {
              triggerMemorySummary(empId, 'daily');
            }
          }).catch(function () {});
          localStorage.setItem('sb_daily_summary_date', today);
        }
      }
    }
  }, 60);
}
_scheduleDailyMemorySummary();

// 从记忆面板点击触发归纳
function triggerSummarizeFromMemoryPanel() {
  var empId = getCurrentEmpId();
  if (!empId) {
    showToast('⚠️ 请先选择一个员工');
    return;
  }

  var banner = document.getElementById('memorySummarizeBanner');
  var sourceIds = [];
  try {
    sourceIds = JSON.parse(banner && banner.dataset.sourceIds ? banner.dataset.sourceIds : '[]');
  } catch (e) {
    sourceIds = [];
  }

  if (sourceIds.length < 2) {
    showToast('⚠️ 没有足够的记忆可以归纳');
    return;
  }

  showToast('📝 正在归纳记忆，请稍候...');

  // 加载当前记忆以获取源记忆内容
  apiFetch('/api/memory/' + encodeURIComponent(empId)).then(function (r) {
    return r.json();
  }).then(function (memData) {
    var allMemories = (memData.daily || []).concat(memData.core || []);
    var sourceMemories = [];
    sourceIds.forEach(function (id) {
      var m = allMemories.find(function (mem) { return mem.id === id; });
      if (m) sourceMemories.push(m);
    });

    // 生成 consolidatedValue：拼接源记忆内容
    // FIXME: 修复建议归纳分页导致源记忆不足：如果分页导致前端找不到完整源记忆，consolidatedValue 留空让后端自动生成
    var consolidatedValue = sourceMemories.map(function (m) {
      return '• ' + (m.value || '');
    }).join('\n');

    return apiFetch('/api/memory/consolidate', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        empId: empId,
        sourceIds: sourceIds,
        consolidatedValue: consolidatedValue,
        key: 'core',
        priority: 8,
        tags: ['归纳']
      })
    });
  }).then(function (r) {
    return r.json();
  }).then(function (data) {
    showToast('✅ 记忆归纳完成');
    if (banner) banner.style.display = 'none';
    renderMemoryTab(empId);
    console.debug('[Memory] 归纳完成:', data);
  }).catch(function (e) {
    showToast('❌ 归纳失败：' + (e.message || '请重试'));
    console.warn('[Memory] 归纳失败:', e);
  });
}

// ========== 二期：核心记忆候选与知识归纳 UI ==========
function openCoreCandidateModal() {
  var empId = getCurrentEmpId();
  if (!empId) return;
  var overlay = document.getElementById('coreCandidateModalOverlay');
  var body = document.getElementById('coreCandidateModalBody');
  if (overlay) overlay.classList.add('open');
  if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/core-candidates').then(function (r) { return r.json(); }).then(function (data) {
    var candidates = (data.candidates || []).filter(function (c) { return c.status === 'pending'; });
    if (candidates.length === 0) {
      if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无待确认候选</div>';
      return;
    }
    var html = '';
    candidates.forEach(function (c) {
      html += '<div style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px;">';
      html += '<div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:6px;">' + escapeHtml(c.value) + '</div>';
      if (c.reason) {
        html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">💡 ' + escapeHtml(c.reason) + '</div>';
      }
      html += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
      html += '<button class="memory-add-btn" style="background:var(--bg-tertiary);color:var(--text-secondary);" onclick="dismissCoreCandidate(\'' + escapeAttr(c.id) + '\')">忽略</button>';
      html += '<button class="memory-add-btn" onclick="confirmCoreCandidate(\'' + escapeAttr(c.id) + '\')">确认为核心记忆</button>';
      html += '</div></div>';
    });
    if (body) body.innerHTML = html;
  }).catch(function (e) {
    if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败</div>';
  });
}
function closeCoreCandidateModal() {
  var overlay = document.getElementById('coreCandidateModalOverlay');
  if (overlay) overlay.classList.remove('open');
}
function confirmCoreCandidate(candId) {
  var empId = getCurrentEmpId();
  if (!empId) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/core-candidates/' + encodeURIComponent(candId) + '/confirm', {
    method: 'POST'
  }).then(function (r) { return r.json(); }).then(function () {
    showToast('✅ 已升级为核心记忆');
    renderMemoryTab(empId);
    openCoreCandidateModal();
  }).catch(function (e) {
    showToast('❌ 确认失败');
  });
}
function dismissCoreCandidate(candId) {
  var empId = getCurrentEmpId();
  if (!empId) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/core-candidates/' + encodeURIComponent(candId) + '/dismiss', {
    method: 'POST'
  }).then(function (r) { return r.json(); }).then(function () {
    showToast('✅ 已忽略该候选');
    renderMemoryTab(empId);
    openCoreCandidateModal();
  }).catch(function (e) {
    showToast('❌ 忽略失败');
  });
}

// ========== 三期：记忆冲突提示与处理 ==========
function openMemoryConflictModal() {
  var empId = getCurrentEmpId();
  if (!empId) return;
  var overlay = document.getElementById('memoryConflictModalOverlay');
  var body = document.getElementById('memoryConflictModalBody');
  if (overlay) overlay.classList.add('open');
  if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/conflicts').then(function (r) { return r.json(); }).then(function (data) {
    var conflicts = data.conflicts || [];
    if (conflicts.length === 0) {
      if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无冲突</div>';
      return;
    }
    var html = '';
    conflicts.forEach(function (c) {
      var memId = c.id || '';
      var value = c.value || '';
      var conflictWith = c.conflictWith || [];
      var reason = c.conflictNote || '';
      html += '<div style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px;">';
      html += '<div style="font-size:12px;color:#FF3B30;font-weight:600;margin-bottom:8px;">⚠️ 冲突</div>';
      html += '<div style="padding:8px;background:var(--bg-tertiary);border-radius:8px;margin-bottom:8px;">';
      html += '<div style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(value) + '</div>';
      html += '</div>';
      if (conflictWith.length) {
        html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:8px;">冲突记忆 ID: ' + escapeHtml(conflictWith.join(', ')) + '</div>';
      }
      if (reason) {
        html += '<div style="font-size:11px;color:var(--text-secondary);margin-bottom:10px;">原因: ' + escapeHtml(reason) + '</div>';
      }
      html += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
      html += '<button class="memory-add-btn" onclick="resolveMemoryConflict(\'' + escapeAttr(empId) + '\', \'' + escapeAttr(memId) + '\')">已解决</button>';
      html += '</div>';
      html += '</div>';
    });
    if (body) body.innerHTML = html;
  }).catch(function (e) {
    if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败</div>';
    console.warn('[MemoryConflict] 加载失败:', e);
  });
}
function closeMemoryConflictModal() {
  var overlay = document.getElementById('memoryConflictModalOverlay');
  if (overlay) overlay.classList.remove('open');
}
function resolveMemoryConflict(empId, memId) {
  if (!empId || !memId) return;
  if (!confirm('确定将该冲突标记为已解决？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memId) + '/resolve-conflict', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({resolution: 'resolved'})
  }).then(function (r) { return r.json(); }).then(function () {
    showToast('✅ 冲突已解决');
    renderMemoryTab(empId);
    openMemoryConflictModal();
  }).catch(function (e) {
    showToast('❌ 解决失败');
    console.warn('[MemoryConflict] 解决失败:', e);
  });
}

function triggerInductFromMemoryPanel() {
  var empId = getCurrentEmpId();
  if (!empId) {
    showToast('⚠️ 请先选择一个员工');
    return;
  }
  showToast('📚 正在归纳到知识库，请稍候...');
  // 用户已响应归纳提示，本页面临时抑制提示条
  inductBannerSuppressed = true;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/induct-to-knowledge', {
    method: 'POST'
  }).then(function (r) { return r.json(); }).then(function (data) {
    showToast('✅ 已生成 ' + (data.createdDocs || 0) + ' 篇知识文档');
    renderMemoryTab(empId);
    openInductResultModal(data);
  }).catch(function (e) {
    showToast('❌ 归纳失败');
    // 调用失败时用户并未完成归纳，恢复提示条以便重试
    inductBannerSuppressed = false;
    renderMemoryTab(empId);
  });
}
function openInductResultModal(data) {
  var overlay = document.getElementById('inductResultModalOverlay');
  var body = document.getElementById('inductResultModalBody');
  var archiveBtn = document.getElementById('inductArchiveBtn');
  if (overlay) overlay.classList.add('open');
  if (!body) return;
  if (!data || !data.createdDocs) {
    var reasonText = (data && data.reason) ? data.reason : '本次未生成知识文档';
    body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">' + escapeHtml(reasonText) + '</div>';
    if (archiveBtn) archiveBtn.style.display = 'none';
    return;
  }
  body.innerHTML = '<div style="padding:4px 0 12px;font-size:13px;color:var(--text-secondary);">已生成 ' + data.createdDocs + ' 篇知识文档并写入全局知识库</div>';
  if (archiveBtn) archiveBtn.style.display = '';
}
function closeInductResultModal() {
  var overlay = document.getElementById('inductResultModalOverlay');
  if (overlay) overlay.classList.remove('open');
  // 弹窗关闭时无需再 renderMemoryTab：triggerInductFromMemoryPanel / archiveInductedMemories 已刷新过面板
}
function archiveInductedMemories() {
  var empId = getCurrentEmpId();
  if (!empId) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/archive-inducted', {
    method: 'POST'
  }).then(function (r) { return r.json(); }).then(function (data) {
    showToast('✅ 已归档 ' + (data.archivedIds || []).length + ' 条已归纳记忆');
    closeInductResultModal();
    renderMemoryTab(empId);
  }).catch(function (e) {
    showToast('❌ 归档失败');
  });
}

// ========== 记忆编辑 ==========
function startMemoryEdit(memoryId) {
  var textEl = document.getElementById('memText_' + memoryId);
  var editEl = document.getElementById('memEdit_' + memoryId);
  var inputEl = document.getElementById('memInput_' + memoryId);
  if (textEl) textEl.style.display = 'none';
  if (editEl) editEl.style.display = 'block';
  if (inputEl) {
    inputEl.focus();
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
  }
}

function cancelMemoryEdit(memoryId) {
  var textEl = document.getElementById('memText_' + memoryId);
  var editEl = document.getElementById('memEdit_' + memoryId);
  if (textEl) textEl.style.display = 'block';
  if (editEl) editEl.style.display = 'none';
}

function saveMemoryEdit(empId, memoryId) {
  var inputEl = document.getElementById('memInput_' + memoryId);
  if (!inputEl) return;
  var newValue = inputEl.value.trim();
  if (!newValue) {
    showToast('⚠️ 记忆内容不能为空');
    return;
  }

  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({value: newValue})
  }).then(function (r) {
    return r.json();
  }).then(function (data) {
    showToast('✅ 记忆已更新');
    // 更新本地显示
    var textEl = document.getElementById('memText_' + memoryId);
    if (textEl) textEl.textContent = newValue;
    cancelMemoryEdit(memoryId);
  }).catch(function (e) {
    showToast('❌ 更新失败');
    console.warn('[Memory] 更新失败:', e);
  });
}

function deleteMemory(empId, memoryId) {
  if (!confirm('确定删除这条记忆？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId), {
    method: 'DELETE'
  }).then(function () {
    showToast('✅ 记忆已删除');
    renderMemoryTab(empId);
    updateMemoryTabTitle(empId);
  }).catch(function (e) {
    showToast('❌ 删除失败');
    console.warn('[Memory] 删除失败:', e);
  });
}
function promoteToCore(empId, memoryId) {
  if (!confirm('确定将此记忆升级为核心记忆？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId) + '/promote', {
    method: 'POST'
  }).then(function (r) {
    return r.json();
  }).then(function () {
    showToast('✅ 已升级为核心记忆');
    renderMemoryTab(empId);
    updateMemoryTabTitle(empId);
  }).catch(function (e) {
    showToast('❌ 升级失败: ' + (e.message || '核心池已满'));
    console.warn('[Memory] 升级失败:', e);
  });
}
function restoreMemory(empId, memoryId) {
  if (!confirm('确定将此归档记忆恢复到日常记录？')) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/' + encodeURIComponent(memoryId) + '/restore', {
    method: 'POST'
  }).then(function (r) {
    return r.json();
  }).then(function () {
    showToast('✅ 已恢复到日常记录');
    renderMemoryTab(empId);
    updateMemoryTabTitle(empId);
  }).catch(function (e) {
    showToast('❌ 恢复失败: ' + (e.message || '日常池已满'));
    console.warn('[Memory] 恢复失败:', e);
  });
}
function cleanupExpiredMemories() {
  var empId = getCurrentEmpId();
  if (!empId) return;
  // v2：调用归档 API，将过期日常记录标记为 archived=True（不删除，可恢复）
  apiFetch('/api/memory/' + encodeURIComponent(empId) + '/archive', {
    method: 'POST'
  }).then(function (r) {
    return r.json();
  }).then(function (data) {
    renderMemoryTab(empId);
    updateMemoryTabTitle(empId);
    showToast('✅ 已标记 ' + (data.archived || 0) + ' 条过期日常记录为归档（可在归档标签恢复）');
  }).catch(function (e) {
    showToast('❌ 归档失败');
    console.warn('[Memory] 归档失败:', e);
  });
}

function addMemory() {
  // 在面板内显示输入框，不使用 prompt
  var contentEl = document.getElementById('memoryContent');
  if (!contentEl) return;

  // 检查是否已经有输入框
  var existingInput = contentEl.querySelector('.memory-add-input');
  if (existingInput) {
    existingInput.querySelector('input').focus();
    return;
  }
  var inputHtml = '<div class="memory-add-input" style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:8px;border:2px solid var(--accent);">';
  inputHtml += '<input type="text" id="newMemoryInput" placeholder="输入记忆内容..." style="width:100%;padding:8px 12px;border:1px solid var(--separator);border-radius:8px;font-size:14px;background:var(--bg-primary);color:var(--text-primary);box-sizing:border-box;margin-bottom:8px;" onkeypress="if(event.key===\'Enter\')confirmAddMemory()">';
  inputHtml += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
  inputHtml += '<button onclick="cancelAddMemory()" style="padding:6px 12px;border:1px solid var(--separator);border-radius:6px;background:transparent;font-size:12px;cursor:pointer;color:var(--text-secondary);">取消</button>';
  inputHtml += '<button onclick="confirmAddMemory()" style="padding:6px 12px;border:none;border-radius:6px;background:var(--accent);color:white;font-size:12px;cursor:pointer;">确认添加</button>';
  inputHtml += '</div></div>';
  contentEl.insertAdjacentHTML('afterbegin', inputHtml);
  document.getElementById('newMemoryInput').focus();
}
function cancelAddMemory() {
  var inputEl = document.querySelector('.memory-add-input');
  if (inputEl) inputEl.remove();
}
function confirmAddMemory() {
  var inputEl = document.getElementById('newMemoryInput');
  if (!inputEl) return;
  var memContent = inputEl.value.trim();
  if (!memContent) {
    showToast('请输入记忆内容');
    return;
  }
  var empId = getCurrentEmpId();
  if (!empId) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId), {
    method: 'POST',
    body: JSON.stringify({
      key: 'manual',
      value: memContent,
      source: '手动添加'
    })
  }).then(function () {
    showToast('✅ 记忆已添加');
    renderMemoryTab(empId);
    updateMemoryTabTitle(empId);
  }).catch(function (e) {
    showToast('❌ 添加失败');
    console.warn('[Memory] 添加失败:', e);
  });
}

// ========== OpenClaw技能管理 ==========
var ocSkillsLoaded = false;
function loadOpenClawSkills() {
  // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
  apiFetchWithRetry('/api/openclaw/skills/list').then(function (result) {
    if (!result.success) {
      console.warn('[Skills] 加载OpenClaw技能失败:', result.error);
      return;
    }
    var data = result.data || {};
    var skills = data.skills || [];
    var list = document.getElementById('ocSkillList');
    var empty = document.getElementById('ocSkillEmpty');
    if (!list) return;
    if (skills.length === 0) {
      list.innerHTML = '';
      list.style.display = 'none';
      empty.style.display = 'block';
    } else {
      list.style.display = 'flex';
      empty.style.display = 'none';
      list.innerHTML = skills.map(function (s) {
        return '<div class="skill-item" style="padding:10px 12px;">' + '<div class="skill-emoji">' + escapeHtml(s.emoji || '🔧') + '</div>' + '<div class="skill-info">' + '<div class="skill-name">' + escapeHtml(s.name || s.slug) + '</div>' + '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.description || '') + (s.version ? ' v' + s.version : '') + '</div>' + '</div>' + '<button class="skill-delete" onclick="removeOpenClawSkill(\'' + escapeAttr(s.slug) + '\')" title="卸载">' + '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>' + '</button>' + '</div>';
      }).join('');
    }
    ocSkillsLoaded = true;
  }).catch(function (e) {
    console.warn('[Skills] 加载OpenClaw技能异常:', e);
  });
}
function searchOpenClawSkills() {
  var input = document.getElementById('ocSkillSearchInput');
  var query = input && input.value.trim() || '';
  if (!query) return;
  var resultsDiv = document.getElementById('ocSkillSearchResults');
  var listDiv = document.getElementById('ocSkillSearchList');
  if (!resultsDiv || !listDiv) return;
  listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索中...</div>';
  resultsDiv.style.display = 'block';
  // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
  apiFetchWithRetry('/api/openclaw/skills/search?q=' + encodeURIComponent(query)).then(function (result) {
    if (!result.success) {
      listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索失败: ' + escapeHtml(result.error || 'unknown') + '</div>';
      return;
    }
    var data = result.data || {};
    var results = data.results || [];
    if (results.length === 0) {
      listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">未找到匹配的技能</div>';
    } else {
      listDiv.innerHTML = results.map(function (s) {
        return '<div class="skill-item" style="padding:10px 12px;">' + '<div class="skill-emoji">' + escapeHtml(s.emoji || '🔍') + '</div>' + '<div class="skill-info">' + '<div class="skill-name">' + escapeHtml(s.name || s.slug) + '</div>' + '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.description || '') + '</div>' + '</div>' + '<button onclick="installOpenClawSkill(\'' + escapeAttr(s.slug) + '\')" style="padding:6px 12px;background:var(--accent);color:white;border:none;border-radius:6px;font-size:12px;cursor:pointer;">安装</button>' + '</div>';
      }).join('');
    }
  }).catch(function (e) {
    listDiv.innerHTML = '<div style="text-align:center;padding:16px;color:var(--text-tertiary);">搜索异常</div>';
  });
}
function installOpenClawSkill(slug) {
  if (!slug) return;
  showToast('正在安装 ' + slug + '...');
  // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
  apiFetchWithRetry('/api/openclaw/skills/install', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      skillName: slug
    })
  }).then(function (result) {
    if (result.success && result.data && result.data.success) {
      showToast('✅ ' + slug + ' 安装成功');
      loadOpenClawSkills();
    } else {
      var errMsg = (result.data && result.data.error) || result.error || '未知错误';
      showToast('❌ 安装失败: ' + errMsg);
    }
  }).catch(function (e) {
    showToast('❌ 安装异常');
  });
}
function removeOpenClawSkill(slug) {
  if (!slug) return;
  if (!confirm('确定要卸载技能 ' + slug + ' 吗？')) return;
  // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
  apiFetchWithRetry('/api/openclaw/skills/remove', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      skillName: slug
    })
  }).then(function (result) {
    if (result.success && result.data && result.data.success) {
      showToast('✅ ' + slug + ' 已卸载');
      loadOpenClawSkills();
    } else {
      var errMsg = (result.data && result.data.error) || result.error || '未知错误';
      showToast('❌ 卸载失败: ' + errMsg);
    }
  }).catch(function (e) {
    showToast('❌ 卸载异常');
  });
}
function closeEmpDetail() {
  // ⑤c 脏状态: 有关闭前确认
  if (typeof isEmpDetailDirty !== 'undefined' && isEmpDetailDirty) {
    if (!confirm('有未保存的修改,是否放弃?')) return;
    clearEmpDetailDirty();
  }
  document.getElementById('empDetailOverlay').classList.remove('open');
  currentEmpId = null;
  inductBannerSuppressed = false;
}

// ========== ⑤c 员工详情面板脏状态管理 ==========
// 描述/紧急标记/连接配置等输入框被修改后, 顶部出现红色 ● 未保存 标记;
// 关闭面板 / 切 tab / 离开页面时若有脏数据, 弹 confirm 确认放弃。
var isEmpDetailDirty = false;
function markEmpDetailDirty() {
  isEmpDetailDirty = true;
  var ind = document.getElementById('empDirtyIndicator');
  if (ind) ind.style.display = '';
}
function clearEmpDetailDirty() {
  isEmpDetailDirty = false;
  var ind = document.getElementById('empDirtyIndicator');
  if (ind) ind.style.display = 'none';
}
// 监听所有「可编辑 + 需要保存」的 input/textarea/select
function bindEmpDetailDirtyInputs() {
  var ids = [
    'empDescriptionInput',
    'empBadgeInput',
    'empConnectApiKey',
    'empConnectBaseUrl',
    'empConnectOpenclawName',
    'empConnectAIProvider',
    'empConnectModel'
  ];
  ids.forEach(function (id) {
    var el = document.getElementById(id);
    if (!el) return;
    var ev = (el.tagName === 'SELECT') ? 'change' : 'input';
    el.addEventListener(ev, markEmpDetailDirty);
  });
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', bindEmpDetailDirtyInputs);
} else {
  bindEmpDetailDirtyInputs();
}
// 离开页面 / 关闭 tab 浏览器级 beforeunload 提示
window.addEventListener('beforeunload', function (e) {
  if (isEmpDetailDirty) {
    e.preventDefault();
    e.returnValue = '有未保存的修改';
    return '有未保存的修改';
  }
});
// ESC key to close modals
document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') {
    var overlay = document.getElementById('empDetailOverlay');
    if (overlay && overlay.classList.contains('open')) {
      closeEmpDetail();
    }
    var userModal = document.getElementById('userEditModal');
    if (userModal && userModal.classList.contains('active')) {
      closeUserEditModal();
    }
  }
});

// ========== Group Detail Panel ==========
var currentGroupDetailId = null;
var selectedGroupEmoji = '';
var selectedGroupColor = '';
function openGroupDetail(groupId) {
  var group = groups.find(function (g) {
    return g.id === groupId;
  });
  if (!group) return;
  currentGroupDetailId = groupId;
  var gdName = document.getElementById('groupDetailName');
  if (gdName) gdName.textContent = group.name || '未命名群组';
  var gdRole = document.getElementById('groupDetailRole');
  if (gdRole) gdRole.textContent = '项目组';
  // 显示完整成员数（不按用户过滤，包含所有在 members 中的成员）
  var memberCount = (group.members || []).length;
  /* 〔r82 2026-10-08〕群不是"在线"实体, 去掉假在线绿点, 纯文本成员数 */
  var gdStatus = document.getElementById('groupDetailStatus');
  if (gdStatus) gdStatus.textContent = memberCount + ' 位成员';
  // 更新头部成员数
  var headerEl = document.getElementById('groupHeaderMemberCount');
  if (headerEl) headerEl.textContent = memberCount;
  // 更新信息区成员数
  var infoMemberEl = document.getElementById('groupInfoMemberCount');
  if (infoMemberEl) infoMemberEl.textContent = memberCount;
  // 更新消息数（从后端获取）
  var msgCountEl = document.getElementById('groupInfoMsgCount');
  if (msgCountEl) {
    msgCountEl.textContent = '-';
    apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/history')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var messages = (data && data.messages) || [];
        if (msgCountEl) msgCountEl.textContent = messages.length;
      })
      .catch(function () {
        if (msgCountEl) msgCountEl.textContent = '0';
      });
  }
  var gdAvatar = document.getElementById('groupDetailAvatar');
  if (gdAvatar) {
    gdAvatar.textContent = group.emoji || '👥';
    gdAvatar.style.background = 'linear-gradient(135deg,' + (group.bg || '#5856D6') + ',' + (group.bg || '#5856D6') + 'dd)';
  }
  var gdId = document.getElementById('groupDetailId');
  if (gdId) gdId.textContent = group.id;
  var gdCreated = document.getElementById('groupDetailCreated');
  if (gdCreated) gdCreated.textContent = group.createdAt || '未知';
  var gdCreator = document.getElementById('groupDetailCreator');
  if (gdCreator) gdCreator.textContent = group.createdBy || 'admin';

  // Settings tab
  var gdEditName = document.getElementById('groupEditName');
  if (gdEditName) gdEditName.value = group.name || '';
  var gdEditDesc = document.getElementById('groupEditDesc');
  if (gdEditDesc) gdEditDesc.value = group.description || '';
  selectedGroupEmoji = group.emoji || '👥';
  selectedGroupColor = group.bg || '#5856D6';

  // Render settings member list
  renderGroupSettingsMembers(group);

  // Update emoji selection
  document.querySelectorAll('#groupEmojiPicker .avatar-option').forEach(function (el) {
    el.classList.toggle('selected', el.textContent === selectedGroupEmoji);
  });
  // Update color selection
  document.querySelectorAll('#groupColorPicker .color-option').forEach(function (el) {
    var onclickStr = el.getAttribute('onclick') || '';
    el.classList.toggle('selected', onclickStr.indexOf(selectedGroupColor) > -1);
  });

  // Render members（显示完整成员，不按用户过滤）
  renderGroupDetailMembers(group);

  // Reset to members tab
  switchGroupDetailTab('members');
  var gdOverlay = document.getElementById('groupDetailOverlay');
  if (gdOverlay) gdOverlay.classList.add('open');
}
function closeGroupDetail() {
  document.getElementById('groupDetailOverlay').classList.remove('open');
  currentGroupDetailId = null;
}
function switchGroupDetailTab(tab) {
  document.querySelectorAll('.group-detail-tab').forEach(function (t) {
    t.classList.toggle('active', t.dataset.gtab === tab);
  });
  document.querySelectorAll('.group-tab-pane').forEach(function (p) {
    p.style.display = p.id === 'group-tab-' + tab ? 'block' : 'none';
  });
}
function renderGroupSettingsMembers(group) {
  var list = document.getElementById('groupSettingsMemberList');
  if (!list) return;
  if (!group.members || group.members.length === 0) {
    list.innerHTML = '<div style="padding:12px;text-align:center;color:var(--text-secondary);font-size:13px;">暂无成员</div>';
    return;
  }
  list.innerHTML = group.members.map(function (m) {
    // 兼容 members 的两种格式：字符串 和 字典
    var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    var emp = emps.find(function (e) {
      return e.id === mid;
    });
    // 优先使用 members 中已有的基础信息（后端 _handle_get_groups 已补充）
    var name = (emp && emp.name) || (m && m.name) || '未知成员';
    var avatar = (emp && emp.avatar) || (m && m.avatar) || '👤';
    var bg = (emp && emp.bg) || (m && m.bg) || '#C7C7CC';
    var displayObj = emp || {name: name, avatar: avatar, bg: bg, id: mid};
    var avatarHtml = renderAvatar(displayObj, 28);
    return '<div class="group-member-item" data-mid="' + escapeAttr(mid) + '" style="padding:6px 0;">' + '<div class="group-member-avatar" style="background:' + escapeAttr(bg) + ';width:28px;height:28px;">' + avatarHtml + '</div>' + '<div class="group-member-info" style="min-width:0;">' + '<div class="group-member-name" style="font-size:13px;">' + escapeHtml(name) + '</div>' + '</div>' + '<button class="group-member-remove" onclick="removeGroupMember(\'' + escapeAttr(mid) + '\')" title="移除成员" style="width:24px;height:24px;font-size:12px;">✕</button>' + '</div>';
  }).join('');
}
function renderGroupDetailMembers(group) {
  var list = document.getElementById('groupMemberList');
  if (!list) return;
  if (!group.members || group.members.length === 0) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-secondary);font-size:13px;">暂无成员</div>';
    return;
  }
  list.innerHTML = group.members.map(function (m) {
    // 兼容 members 的两种格式：字符串 和 字典
    var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    var emp = emps.find(function (e) {
      return e.id === mid;
    });
    // 优先使用 members 中已有的基础信息（后端 _handle_get_groups 已补充）
    var name = (emp && emp.name) || (m && m.name) || '未知成员';
    var avatar = (emp && emp.avatar) || (m && m.avatar) || '👤';
    var bg = (emp && emp.bg) || (m && m.bg) || '#C7C7CC';
    var role = (emp && getEmpRoleDisplay(emp)) || (m && m.role) || '';
    var displayObj = emp || {name: name, avatar: avatar, bg: bg, role: role, id: mid};
    var avatarHtml = renderAvatar(displayObj, 36);
    return '<div class="group-member-item" data-mid="' + escapeAttr(mid) + '">' + '<div class="group-member-avatar" style="background:' + escapeAttr(bg) + ';">' + avatarHtml + '</div>' + '<div class="group-member-info">' + '<div class="group-member-name">' + escapeHtml(name) + '</div>' + '<div class="group-member-role">' + escapeHtml(role) + '</div>' + '</div>' + '<button class="group-member-remove" onclick="removeGroupMember(\'' + escapeAttr(mid) + '\')" title="移除成员">✕</button>' + '</div>';
  }).join('');
}
function selectGroupEmoji(el) {
  document.querySelectorAll('#groupEmojiPicker .avatar-option').forEach(function (a) {
    a.classList.remove('selected');
  });
  el.classList.add('selected');
  selectedGroupEmoji = el.textContent;
  document.getElementById('groupDetailAvatar').textContent = selectedGroupEmoji;
}
function selectGroupColor(el, color) {
  document.querySelectorAll('#groupColorPicker .color-option').forEach(function (c) {
    c.style.boxShadow = 'none';
  });
  el.style.boxShadow = '0 0 0 2px white, 0 2px 8px rgba(0,0,0,0.3)';
  selectedGroupColor = color;
  document.getElementById('groupDetailAvatar').style.background = 'linear-gradient(135deg,' + color + ',' + color + 'dd)';
}
function removeGroupMember(empId) {
  if (!currentGroupDetailId) return;
  var group = groups.find(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (!group || !group.members) return;
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var idx = -1;
  for (var i = 0; i < group.members.length; i++) {
    var m = group.members[i];
    var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    if (mid === empId) {
      idx = i;
      break;
    }
  }
  if (idx > -1) {
    group.members.splice(idx, 1);
    saveGroups();
    renderGroupDetailMembers(group);
    renderGroupItems();
    // 刷新群聊头部及详情弹窗中的成员数
    _refreshGroupMemberCount(group);
    showToast('已移除成员');
  }
}
function showAddMemberModal() {
  console.debug('showAddMemberModal clicked, currentGroupDetailId:', currentGroupDetailId);
  if (!currentGroupDetailId) return;
  var group = groups.find(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (!group) return;

  // Find employees not in group（兼容 members 的两种格式）
  var groupMemberIdSet = {};
  if (group.members) {
    group.members.forEach(function (m) {
      var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
      if (mid) groupMemberIdSet[mid] = true;
    });
  }
  var availableEmps = emps.filter(function (e) {
    return !e.archived && !groupMemberIdSet[e.id];
  });
  if (availableEmps.length === 0) {
    showToast('没有可添加的成员');
    return;
  }
  var html = '<div class="member-modal">' + '<div class="member-modal-header">' + '<div class="member-modal-title">添加成员</div>' + '<div class="member-modal-subtitle">选择要添加到群组的AI员工</div>' + '</div>' + '<div class="member-search">' + '<span class="member-search-icon">&#128269;</span>' + '<input type="text" id="memberSearchInput" placeholder="搜索员工..." oninput="filterMemberList(this.value)" />' + '</div>' + '<div class="member-list" id="memberList">' + availableEmps.map(function (emp) {
    var avatarHtml = renderAvatar(emp, 40);
    return '<div class="member-item" data-id="' + escapeAttr(emp.id) + '" onclick="toggleMember(this)">' + '<div class="member-avatar" style="background:' + escapeAttr(emp.bg || '#888') + ';">' + avatarHtml + '</div>' + '<div class="member-info">' + '<div class="member-name">' + escapeHtml(emp.name || '') + '</div>' + '<div class="member-role">' + escapeHtml(getEmpRoleDisplay(emp)) + '</div>' + '</div>' + '<div class="member-check">' + '<span class="member-check-icon">&#10003;</span>' + '</div>' + '</div>';
  }).join('') + '</div>' + '<div class="member-footer">' + '<button class="member-btn member-btn-cancel" onclick="closeModal()">取消</button>' + '<button class="member-btn member-btn-confirm" id="memberConfirmBtn" onclick="confirmAddMembers()" disabled>确认添加</button>' + '</div>' + '</div>';
  showModal('', html);
}
function toggleMember(el) {
  el.classList.toggle('selected');
  updateConfirmBtn();
}
function updateConfirmBtn() {
  var selected = document.querySelectorAll('.member-item.selected');
  var btn = document.getElementById('memberConfirmBtn');
  if (btn) {
    btn.disabled = selected.length === 0;
    btn.textContent = selected.length > 0 ? '确认添加 (' + selected.length + ')' : '确认添加';
  }
}
function filterMemberList(keyword) {
  var items = document.querySelectorAll('.member-item');
  var lower = keyword.toLowerCase();
  items.forEach(function (item) {
    var name = item.querySelector('.member-name').textContent.toLowerCase();
    var role = item.querySelector('.member-role').textContent.toLowerCase();
    if (name.indexOf(lower) >= 0 || role.indexOf(lower) >= 0) {
      item.style.display = 'flex';
    } else {
      item.style.display = 'none';
    }
  });
}
function confirmAddMembers() {
  var selected = document.querySelectorAll('.member-item.selected');
  if (selected.length === 0) return;
  var group = groups.find(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (!group) return;
  if (!group.members) group.members = [];
  var count = 0;
  var newMemberIds = [];
  selected.forEach(function (item) {
    var empId = item.getAttribute('data-id');
    // 兼容 members 的两种格式：字符串数组 和 字典数组
    var exists = group.members.some(function (m) {
      var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
      return mid === empId;
    });
    if (!exists) {
      var addEmp = findEmpByIdAcrossGroups(empId);
      var addMemberObj = {'id': empId, 'role': ''};
      if (addEmp) {
        addMemberObj.name = addEmp.name;
        addMemberObj.avatar = addEmp.avatar;
        addMemberObj.bg = addEmp.bg;
        addMemberObj.role = getEmpRoleDisplay(addEmp);
        addMemberObj.openclawName = addEmp.openclawName;
        addMemberObj.createdBy = addEmp.createdBy;
      }
      group.members.push(addMemberObj);
      newMemberIds.push(empId);
      count++;
    }
  });
  if (count > 0) {
    saveGroups();
    mergeGroupMembersToEmps();
    console.debug('[confirmAddMembers] 已添加', count, '个成员:', newMemberIds, '到群组', group.id);
    renderGroupDetailMembers(group);
    renderGroupItems();

    // 更新成员数显示（显示完整成员数，不按用户过滤）
    var memberCount = (group.members || []).length;
    var headerEl = document.getElementById('groupHeaderMemberCount');
    if (headerEl) headerEl.textContent = memberCount + ' 位成员';
    var infoEl = document.getElementById('groupInfoMemberCount');
    if (infoEl) infoEl.textContent = memberCount;
    var statusEl = document.getElementById('groupDetailStatus');
    if (statusEl) statusEl.innerHTML = '<span class="status-dot online"></span> ' + memberCount + ' 位成员';

    // 给新加入的 AI 发送入职通知
    newMemberIds.forEach(function (empId) {
      var emp = emps.find(function (e) {
        return e.id === empId;
      });
      if (!emp) return;
      var groupObj = groups.find(function (g) {
        return g.id === currentGroupDetailId;
      });
      if (!groupObj) return;
      // 兼容 members 的两种格式：字符串数组 和 字典数组
      var memberList = (groupObj.members || []).map(function (m) {
        var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
        var e = emps.find(function (x) {
          return x.id === mid;
        });
        return e ? e.name + '(' + getEmpRoleDisplay(e) + ')' : mid;
      }).join('、');
      var announcement = groupObj.announcement ? '\n【群公告】' + groupObj.announcement : '';

      // 通过 OpenClaw 给 AI 发消息
      var sessionKey = 'agent:' + emp.id + ':chat';
      var notifyMsg = '【入职通知】你已被加入项目组「' + groupObj.name + '」。\n你的角色：' + emp.name + '（' + getEmpRoleDisplay(emp) + '）\n项目组成员：' + memberList + announcement + '\n\n请记住你在项目组中的身份，后续群聊中请以这个角色参与讨论。';
      if (typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated) {
        _sendChatWaitForLifecycle(sessionKey, notifyMsg).then(function () {
          console.debug('[Group] 入职通知已发送给', emp.name);
        }).catch(function (e) {
          console.warn('[Group] 入职通知发送失败:', emp.name, e.message);
        });
      }
    });

    // 通知项目组内其他已有成员，触发它们发送欢迎消息
    var groupObj2 = groups.find(function (g) {
      return g.id === currentGroupDetailId;
    });
    if (groupObj2) {
      var newNamesForWelcome = newMemberIds.map(function (id) {
        var e = emps.find(function (x) {
          return x.id === id;
        });
        return e ? e.name + '（' + getEmpRoleDisplay(e) + '）' : id;
      }).join('、');
      // 兼容 members 的两种格式：字符串数组 和 字典数组
      var existingMemberIds = (groupObj2.members || []).map(function (m) {
        return (typeof m === 'object' && m !== null) ? (m.id || m) : m;
      }).filter(function (id) { return id; }).filter(function (mid) {
        return newMemberIds.indexOf(mid) === -1;
      });
      existingMemberIds.forEach(function (existingId) {
        var existingEmp = emps.find(function (e) {
          return e.id === existingId;
        });
        if (!existingEmp) return;
        var existingSessionKey = 'agent:' + existingEmp.id + ':chat';
        var welcomeNotifyMsg = '【项目组动态】有新同事加入了项目组「' + groupObj2.name + '」：' + newNamesForWelcome + '。\n\n请向新同事发送一条简短的欢迎消息，让TA感受到团队的温暖。';
        if (typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated) {
          _sendChatWaitForLifecycle(existingSessionKey, welcomeNotifyMsg).then(function () {
            console.debug('[Group] 欢迎通知已发送给', existingEmp.name);
          }).catch(function (e) {
            console.warn('[Group] 欢迎通知发送失败:', existingEmp.name, e.message);
          });
        }
      });
    }

    // 在群聊区显示系统消息
    var area = document.getElementById('messagesArea');
    if (area) {
      var timeStr = formatDate();
      var newNames = newMemberIds.map(function (id) {
        var e = emps.find(function (x) {
          return x.id === id;
        });
        return e ? e.name : id;
      }).join('、');
      area.insertAdjacentHTML('beforeend', '<div style="text-align:center;color:var(--text-tertiary);font-size:12px;padding:8px 0;">' + timeStr + ' 🎉 ' + newNames + ' 加入了项目组' + '</div>');
      area.scrollTop = area.scrollHeight;
    }
    showToast('已添加 ' + count + ' 个成员');
  }
  closeModal();
}
function addGroupMember(empId) {
  if (!currentGroupDetailId) return;
  var group = groups.find(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (!group) return;
  if (!group.members) group.members = [];
  // 兼容 members 的两种格式：字符串数组 和 字典数组
  var exists = group.members.some(function (m) {
    var mid = (typeof m === 'object' && m !== null) ? (m.id || m) : m;
    return mid === empId;
  });
  if (!exists) {
    // 把员工完整信息一并写入 group.members，避免后续跨库查找拿到只有 id 的存根
    var emp = findEmpByIdAcrossGroups(empId);
    var memberObj = {'id': empId, 'role': ''};
    if (emp) {
      memberObj.name = emp.name;
      memberObj.avatar = emp.avatar;
      memberObj.bg = emp.bg;
      memberObj.role = getEmpRoleDisplay(emp);
      memberObj.openclawName = emp.openclawName;
      memberObj.createdBy = emp.createdBy;
    }
    group.members.push(memberObj);
    saveGroups();
    // 确保新员工被合并到 emps，@提及和触发都能立即找到
    mergeGroupMembersToEmps();
    renderGroupDetailMembers(group);
    renderGroupItems();
    // 刷新群聊头部成员数（如果当前打开的是该群聊）
    _refreshGroupMemberCount(group);
    closeModal();
    showToast('已添加成员');
    console.debug('[addGroupMember] 已添加成员:', empId, emp ? emp.name : 'unknown', '到群组', group.id);
  }
}

// 统一刷新群组相关 UI 中的成员数
function _refreshGroupMemberCount(group) {
  if (!group) return;
  // 显示完整成员数（不按用户过滤）
  var memberCount = (group.members || []).length;

  // 1. 刷新群聊头部（如果当前打开的是该群聊）
  if (currentGroupId === group.id) {
    var chatHeaderRole = document.querySelector('.chat-header-role');
    if (chatHeaderRole) {
      chatHeaderRole.textContent = memberCount + ' 名成员 · 群聊';
    }
    // 刷新头部头像列表
    var chatHeaderLeft = document.querySelector('.chat-header-left');
    if (chatHeaderLeft) {
      var emoji = group.emoji || '👥';
      var memberAvatarsHtml = '';
      var members = group.members || [];
      var showCount = Math.min(members.length, 4);
      for (var i = 0; i < showCount; i++) {
        // 兼容 members 的两种格式：字符串 和 字典
        var mid = (typeof members[i] === 'object' && members[i] !== null) ? (members[i].id || members[i]) : members[i];
        var m = emps.find(function (e) {
          return e.id === mid;
        });
        if (m) {
          memberAvatarsHtml += '<div class="mini-avatar" style="background:' + escapeAttr(m.bg || '#8E8E93') + ';">' + renderAvatar(m, 20) + '</div>';
        }
      }
      if (members.length > 4) {
        memberAvatarsHtml += '<div class="mini-avatar mini-more">+' + (members.length - 4) + '</div>';
      }
      chatHeaderLeft.innerHTML = '<div class="group-avatar" style="background:linear-gradient(135deg,' + escapeAttr(group.bg || '#5856D6') + ',' + (group.bg || '#5856D6') + 'dd);width:36px;height:36px;border-radius:10px;font-size:18px;">' + escapeHtml(emoji) + '</div>' + '<div class="chat-header-info">' + '<div class="chat-header-name" style="display:flex;align-items:center;gap:6px;">' + '<span>' + escapeHtml(group.name) + '</span>' + '<div class="group-header-avatars">' + memberAvatarsHtml + '</div>' + '</div>' + '<div class="chat-header-role">' + memberCount + ' 名成员 · 群聊</div>' + '</div>';
    }
  }

  // 2. 刷新群组详情弹窗中的成员数
  var headerEl = document.getElementById('groupHeaderMemberCount');
  if (headerEl) headerEl.textContent = memberCount;
  /* 〔r82 2026-10-08〕群不是"在线"实体, 去掉假在线绿点, 纯文本成员数 */
  var gdStatus = document.getElementById('groupDetailStatus');
  if (gdStatus) gdStatus.textContent = memberCount + ' 位成员';
  var infoMemberEl = document.getElementById('groupInfoMemberCount');
  if (infoMemberEl) infoMemberEl.textContent = memberCount;
}
function saveGroupDetail() {
  if (!currentGroupDetailId) return;
  var group = groups.find(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (!group) return;
  var newName = document.getElementById('groupEditName').value.trim();
  var newDesc = document.getElementById('groupEditDesc').value.trim();
  if (newName) group.name = newName;
  if (newDesc !== undefined) group.description = newDesc;
  if (selectedGroupEmoji) group.emoji = selectedGroupEmoji;
  if (selectedGroupColor) group.bg = selectedGroupColor;
  saveGroups();
  renderGroupItems();
  document.getElementById('groupDetailName').textContent = group.name;

  // 同步到后端
  var token = localStorage.getItem('sb_auth_token');
  if (token && token !== 'local_mode' && typeof apiFetch === 'function') {
    apiFetch('/api/groups/' + currentGroupDetailId, {
      method: 'PUT',
      body: JSON.stringify({
        name: group.name,
        description: group.description || '',
        emoji: group.emoji,
        bg: group.bg,
        members: group.members || []
      })
    }).then(function (res) {
      if (res && res.ok) {
        showToast('✅ 群组已保存并同步到服务器');
      } else {
        showToast('✅ 群组已保存(本地)');
      }
    }).catch(function (err) {
      console.warn('[Group] sync to server failed:', err);
      showToast('✅ 群组已保存(本地)');
    });
  } else {
    showToast('✅ 群组已保存');
  }

  // 自动关闭群组详情抽屉
  closeGroupDetail();
}
function dissolveGroupFromDetail() {
  if (!currentGroupDetailId) return;
  if (!confirm('确定要解散这个群组吗？此操作不可撤销。')) return;
  var idx = groups.findIndex(function (g) {
    return g.id === currentGroupDetailId;
  });
  if (idx > -1) {
    groups.splice(idx, 1);
    saveGroups();
    closeGroupDetail();
    renderGroupItems();
    showToast('群组已解散');
  }
}
function switchEmpDetailTab(tab) {
  // ⑤c 脏状态: 切 tab 时若有未保存修改, 弹 confirm 确认
  if (typeof isEmpDetailDirty !== 'undefined' && isEmpDetailDirty) {
    if (!confirm('有未保存的修改,是否放弃?')) return;
    clearEmpDetailDirty();
  }
  document.querySelectorAll('.emp-detail-tab').forEach(function (t) {
    return t.classList.toggle('active', t.dataset.etab === tab);
  });
  document.querySelectorAll('.emp-tab-pane').forEach(function (p) {
    return p.classList.toggle('active', p.id === 'emp-tab-' + tab);
  });
  /* 〔r82 follow-up 老大 15:33 批注②〕stats 条只有基础 tab 显示, 点其他 tab 隐藏 */
  var _statsBar = document.getElementById('aiEmpStatsBar');
  if (_statsBar) _statsBar.style.display = (tab === 'basic') ? '' : 'none';
  // Update avatar grid selection when switching to avatar tab
  if (tab === 'avatar' && currentEmpId) {
    const emp = emps.find(function (e) {
      return e.id === currentEmpId;
    });
    if (emp) {
      document.querySelectorAll('.avatar-option').forEach(function (a) {
        return a.classList.toggle('selected', a.dataset.avatar === emp.avatar);
      });
      document.querySelectorAll('.color-option').forEach(function (c) {
        return c.classList.toggle('selected', c.style.background === emp.color || c.dataset.color === emp.color);
      });
    }
  }
  // Update skills when switching to skills tab
  if (tab === 'skills' && currentEmpId) {
    renderSkills();
    renderSkillPresets();
    loadOpenClawSkills();
  }
  // Update memory when switching to memory tab
  if (tab === 'memory' && currentEmpId) {
    renderMemoryTab(currentEmpId);
  }
  // Update badge preview when switching to basic tab
  if (tab === 'basic' && currentEmpId) {
    var badgeInput = document.getElementById('empBadgeInput');
    var badgePreview = document.getElementById('empBadgePreview');
    if (badgeInput && badgePreview) {
      var emp = emps.find(function (e) {
        return e.id === currentEmpId;
      });
      badgeInput.value = emp && emp.badge || '';
      badgePreview.textContent = emp && emp.badge ? '当前: ⚠️ ' + emp.badge : '';
    }
  }
  // Update memory tab title with count
  if (tab === 'memory' && currentEmpId) {
    updateMemoryTabTitle(currentEmpId);
  }
}
function updateMemoryTabTitle(empId) {
  if (!empId) return;
  var tabBtn = document.querySelector('.emp-detail-tab[data-etab="memory"]');
  if (!tabBtn) return;
  apiFetch('/api/memory/' + encodeURIComponent(empId)).then(function (r) {
    return r.json();
  }).then(function (data) {
    // 异步返回时若已切换到其他员工，不更新标题计数
    if (empId !== getCurrentEmpId()) return;
    var core = (data && data.core) || [];
    var daily = (data && data.daily) || [];
    var archive = (data && data.archive) || [];
    var count = core.length + daily.length + archive.length;
    tabBtn.textContent = '记忆 (' + count + ')';
  }).catch(function () {
    tabBtn.textContent = '记忆';
  });
}

// ========== Channel Config ==========
var currentChannelType = '';

// Update badge preview
function updateBadgePreview(value) {
  var preview = document.getElementById('empBadgePreview');
  if (preview) {
    preview.textContent = value ? '预览: ⚠️ ' + value : '';
  }
}
var selectedAvatar = '',
  selectedColor = '';
function selectAvatar(el) {
  document.querySelectorAll('.avatar-option').forEach(function (a) {
    return a.classList.remove('selected');
  });
  el.classList.add('selected');
  selectedAvatar = parseInt(el.dataset.avatar);
}
function selectColor(color) {
  document.querySelectorAll('.color-option').forEach(function (c) {
    return c.style.boxShadow = 'none';
  });
  event.target.style.boxShadow = '0 0 0 2px white, 0 2px 8px rgba(0,0,0,0.3)';
  selectedColor = color;
}
function saveEmpAvatar() {
  if (!currentEmpId) return;
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (emp) {
    if (typeof selectedAvatar === 'number') emp.avatar = selectedAvatar;
    if (selectedColor) {
      emp.color = selectedColor;
      emp.bg = selectedColor;
    }
    saveEmployees();
    document.getElementById('empDetailAvatar').innerHTML = renderAvatar(emp, 56);
    document.getElementById('empDetailAvatar').style.background = `linear-gradient(135deg,${emp.color},${emp.color}dd)`;
    renderEmployeeList();
    // Also update chat header avatar if this emp is currently in chat
    var _chatAvatar = document.querySelector('.chat-header-v2 .avatar') || document.querySelector('.chat-header .avatar');
    if (_chatAvatar && currentEmpId === localStorage.getItem('sb_current_emp')) {
      _chatAvatar.innerHTML = renderAvatar(emp, 36);
      var _isImg = typeof emp.avatar === 'number' && typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS[emp.avatar] || emp.avatar && (emp.avatar.indexOf('data:image') === 0 || emp.avatar.indexOf('.png') > 0 || emp.avatar.indexOf('.jpg') > 0 || emp.avatar.indexOf('.jpeg') > 0);
      _chatAvatar.style.background = _isImg ? 'transparent' : 'linear-gradient(135deg,' + emp.bg + ',' + emp.bg + 'dd)';
    }
    showToast('✅ 头像已保存');
  }
}
function saveEmpDescription() {
  if (!currentEmpId) return;
  var emp = emps.find(function (e) { return e.id === currentEmpId; });
  if (!emp) return;
  var descInput = document.getElementById('empDescriptionInput');
  var descDisplay = document.getElementById('empDetailDescription');
  if (!descInput) return;
  var newDesc = descInput.value.trim();
  emp.description = newDesc;
  if (descDisplay) descDisplay.textContent = newDesc ? '当前描述: ' + newDesc : '暂无描述';
  saveEmployees(currentEmpId);
  syncEmpToServer(emp);
  // ⑤c 脏状态: 保存成功 → 清标记
  if (typeof clearEmpDetailDirty === 'function') clearEmpDetailDirty();
  showToast('✅ 描述已保存并同步');
}
// ========== 员工连接配置 ==========
var empSelectedConnectAIProvider = 'kimi';
function selectAIProvider(providerId) {
  empSelectedConnectAIProvider = providerId;
  // 填充模型下拉 - 优先使用 OpenClaw 动态获取的模型列表
  var modelSelect = document.getElementById('empConnectModel');
  renderModelSelect(modelSelect, providerId, '');

  // 添加默认模型选项
  var provider = API_PROVIDERS[providerId];
  if (provider && provider.defaultModel) {
    var defaultOpt = document.createElement('option');
    defaultOpt.value = provider.defaultModel;
    defaultOpt.textContent = '默认 (' + provider.defaultModel + ')';
    modelSelect.appendChild(defaultOpt);
  }
  if (modelSelect.parentElement) modelSelect.parentElement.style.display = 'block';

  // 自定义时显示 Base URL
  var baseUrlGroup = document.getElementById('empConnectBaseUrlGroup');
  if (baseUrlGroup) baseUrlGroup.style.display = providerId === 'custom' ? 'block' : 'none';
  // 更新 API Key placeholder
  var apiKeyInput = document.getElementById('empConnectApiKey');
  if (apiKeyInput) {
    if (provider) apiKeyInput.placeholder = provider.keyPlaceholder || '输入 API Key...';
  }
}
function loadEmpConnect(emp) {
  // 加载员工连接配置到面板 - OpenClaw 为默认
  var aiProvider = emp.aiProvider || 'kimi';
  empSelectedConnectAIProvider = aiProvider;

  // 设置 AI 供应商下拉
  var aiProviderSelect = document.getElementById('empConnectAIProvider');
  if (aiProviderSelect) aiProviderSelect.value = aiProvider;

  // 填充模型下拉 - 优先使用 OpenClaw 动态获取的模型列表
  var modelSelect = document.getElementById('empConnectModel');
  renderModelSelect(modelSelect, aiProvider, emp.apiModel || '');

  // 添加默认模型选项
  var provider = API_PROVIDERS[aiProvider];
  if (provider && provider.defaultModel) {
    var defaultOpt = document.createElement('option');
    defaultOpt.value = provider.defaultModel;
    defaultOpt.textContent = '默认 (' + provider.defaultModel + ')';
    modelSelect.appendChild(defaultOpt);
  }

  // 设置当前值
  document.getElementById('empConnectOpenclawName').value = emp.openclawName || '';
  document.getElementById('empConnectApiKey').value = emp.apiKey || '';
  document.getElementById('empConnectModel').value = emp.apiModel || '';
  document.getElementById('empConnectBaseUrl').value = emp.customEndpoint || '';

  // 自定义时显示 Base URL
  var baseUrlGroup = document.getElementById('empConnectBaseUrlGroup');
  if (baseUrlGroup) baseUrlGroup.style.display = aiProvider === 'custom' ? 'block' : 'none';

  // 更新 Gateway 状态
  updateGatewayStatus();
}
async function saveEmpConnect() {
  if (!currentEmpId) return;
  var emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (!emp) return;
  var selectedProvider = document.getElementById('empConnectAIProvider').value;
  var apiKeyVal = document.getElementById('empConnectApiKey').value.trim();
  console.debug('[saveEmpConnect] 准备保存 emp=' + emp.id + ' provider=' + selectedProvider + ' apiKey_len=' + apiKeyVal.length);
  emp.apiProvider = selectedProvider;  // 同步为实际供应商，避免被硬编码为 'openclaw'
  emp.aiProvider = selectedProvider;
  emp.openclawName = document.getElementById('empConnectOpenclawName').value.trim() || 'main';
  emp.apiKey = apiKeyVal;
  emp.apiModel = document.getElementById('empConnectModel').value;
  emp.customEndpoint = document.getElementById('empConnectBaseUrl').value.trim();
  console.debug('[saveEmpConnect] emp.apiKey 已设置=' + (emp.apiKey ? '有值(' + emp.apiKey.length + ')' : '空'));
  saveEmployees(currentEmpId);  // 只同步当前员工，避免刷所有员工

  // 自动在 Gateway 注册/更新 Agent
  var regResult = await registerOpenClawAgent(emp);
  // ⑤c 脏状态: 保存成功(无论注册 Gateway 成功与否, 本地配置已落库) → 清标记
  if (typeof clearEmpDetailDirty === 'function') clearEmpDetailDirty();
  if (regResult.success) {
    showToast('✅ 配置已保存，Agent 已注册');
  } else if (regResult.error === 'Gateway 未连接') {
    showToast('⚠️ 配置已保存，但 Gateway 未连接');
  } else {
    showToast('❌ 配置已保存，但 Agent 注册失败：' + regResult.error);
  }
}

// 返回 {success: bool, error: string|null, docsWritten: bool}
async function registerOpenClawAgent(emp) {
  // 0. 检查 Gateway 连接状态
  if (!openclaw || !openclaw.connected) {
    console.debug('[registerAgent] Gateway 未连接');
    return {
      success: false,
      error: 'Gateway 未连接',
      docsWritten: false
    };
  }

  // 用 emp.id（ASCII，如 "xlcx","zjg","emp_xxx"）作为 agent name/agentId
  // 避免中文名经 normalizeAgentId 回退为 "main" 导致 "main is reserved"
  var agentName = emp.id;
  var workspacePath = '~/.openclaw/workspace-' + agentName;
  var empRoleForPrompt = getEmpRoleDisplay(emp);
  var soulContent = emp.soulDoc || emp.systemPrompt || '你是 ' + emp.name + '，一个 ' + empRoleForPrompt + '。请用第一人称回复，保持角色一致性。不要编造不存在的任务、数据或提醒信息，只基于用户实际提供的内容回复。';
  var identityContent = emp.idDoc || emp.name + ' - ' + empRoleForPrompt;

  // 1. agents.create 只传 Gateway 支持的参数: {name, workspace, model?}
  var agentRegistered = false;
  try {
    var createParams = {
      name: agentName,
      workspace: workspacePath
    };
    if (emp.apiModel) createParams.model = emp.apiModel;
    console.debug('[OpenClaw] 创建 Agent:', JSON.stringify(createParams));
    var result = await openclaw.send('agents.create', createParams);
    console.debug('[OpenClaw] Agent 创建成功:', JSON.stringify(result));
    emp._agentId = result && result.agentId || agentName;
    agentRegistered = true;
  } catch (e) {
    // create 失败（可能已存在），试 update
    console.debug('[OpenClaw] create 失败，尝试 update:', e.message);
    try {
      var updateParams = {
        agentId: agentName
      };
      if (emp.apiModel) updateParams.model = emp.apiModel;
      var result2 = await openclaw.send('agents.update', updateParams);
      console.debug('[OpenClaw] Agent 更新成功:', JSON.stringify(result2));
      emp._agentId = agentName;
      agentRegistered = true;
    } catch (e2) {
      console.debug('[OpenClaw] update 也失败:', e2.message);
      emp._agentId = 'main';
    }
  }
  if (!agentRegistered) {
    console.warn('[OpenClaw] Agent 注册失败，降级使用 main');
    return {
      success: false,
      error: 'OpenClaw Agent 注册失败',
      docsWritten: false
    };
  }

  // 2. 写 SOUL.md / IDENTITY.md / AGENTS.md 到 agent workspace（通过后端 API）
  var agentsContent = emp.agentsDoc || '';
  var docsWritten = false;
  if (soulContent || identityContent || agentsContent) {
    try {
      // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch,
      // 10s 超时 + 1 次重试,失败不再抛异常,降级 warn 不阻断主流程
      var wsResult = await apiFetchWithRetry('/api/openclaw/write-agent-docs', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          agentId: emp._agentId || agentName,
          soulDoc: soulContent,
          identityDoc: identityContent,
          agentsDoc: agentsContent,
          workspacePath: workspacePath
        })
      });
      if (wsResult.success) {
        console.debug('[OpenClaw] 文档已写入:', JSON.stringify(wsResult.data));
        docsWritten = true;
      } else {
        console.debug('[OpenClaw] 文档写入失败:', wsResult.error, 'attempts=' + wsResult.attempts);
      }
    } catch (e3) {
      console.debug('[OpenClaw] 文档写入异常:', e3.message);
    }
  }

  // 3. 更新 emp 的 openclawName 为 ASCII agentId（供 sendViaOpenClaw 使用）
  emp.openclawName = emp._agentId;
  saveEmployees(emp.id);  // 只同步当前员工，避免刷所有员工
  return {
    success: true,
    error: null,
    docsWritten: docsWritten
  };
}
function updateGatewayStatus() {
  var statusEl = document.getElementById('empConnectGatewayStatus');
  if (!statusEl) return;
  var ocOk = typeof openclaw !== 'undefined' && openclaw.connected && openclaw.authenticated;
  if (ocOk) {
    statusEl.textContent = '🟢 已连接';
    statusEl.style.color = '#34C759';
  } else {
    statusEl.textContent = '🔴 未连接';
    statusEl.style.color = '#FF3B30';
  }
}
// 全局缓存 OpenClaw 返回的模型列表
var openclawModels = null;
var openclawModelsFetchedAt = 0;
async function fetchOpenClawModels() {
  if (!openclaw || !openclaw.connected) {
    console.debug('[OpenClaw] 未连接，跳过获取模型列表');
    return null;
  }
  try {
    var result = await openclaw.send('models.list', {});
    console.debug('[OpenClaw] models.list 返回:', JSON.stringify(result));

    // 解析模型列表 - 支持多种返回格式
    var models = [];
    if (result && Array.isArray(result.models)) {
      // 标准格式: { models: [{id,name,provider},...] }
      models = result.models;
    } else if (result && Array.isArray(result.data)) {
      // 备选格式: { data: [...] }
      models = result.data;
    } else if (Array.isArray(result)) {
      // 直接数组格式
      models = result;
    }
    if (models.length > 0) {
      openclawModels = models;
      openclawModelsFetchedAt = Date.now();
      console.debug('[OpenClaw] 模型列表已缓存，共 ' + models.length + ' 个模型');

      // 触发模型列表更新事件
      window.dispatchEvent(new CustomEvent('openclawModelsUpdated', {
        detail: models
      }));
    }
    return models;
  } catch (e) {
    console.warn('[OpenClaw] 获取模型列表失败:', e);
    return null;
  }
}

// 获取可用的模型列表（动态 > 静态配置）
function getAvailableModels(providerId) {
  // 如果有 OpenClaw 动态获取的模型列表，优先使用
  if (openclawModels && openclawModels.length > 0) {
    // 如果指定了 providerId，过滤对应供应商的模型
    if (providerId) {
      var filtered = openclawModels.filter(function (m) {
        var modelProvider = (m.provider || '').toLowerCase();
        return modelProvider === providerId || modelProvider.indexOf(providerId) >= 0;
      });
      if (filtered.length > 0) return filtered;
    }
    return openclawModels;
  }

  // 回退到静态配置
  var provider = API_PROVIDERS[providerId];
  if (provider && provider.models) {
    return provider.models.map(function (m) {
      return {
        id: m,
        name: m
      };
    });
  }
  return [];
}

// 渲染模型选择下拉框
function renderModelSelect(selectEl, providerId, selectedValue) {
  if (!selectEl) return;
  var models = getAvailableModels(providerId);
  var html = '<option value="">使用默认模型</option>';
  if (models.length === 0) {
    // 没有获取到模型，显示提示
    html += '<option value="" disabled>暂无可用模型（请检查 OpenClaw 连接）</option>';
  } else {
    for (var i = 0; i < models.length; i++) {
      var m = models[i];
      var modelId = m.id || m;
      var modelName = m.name || m.id || m;
      var isSelected = selectedValue && selectedValue === modelId ? ' selected' : '';
      html += '<option value="' + modelId + '"' + isSelected + '>' + modelName + '</option>';
    }
  }
  selectEl.innerHTML = html;
}
function editEmpFromDetail() {
  if (!currentEmpId) return;
  closeEmpDetail();
  const emp = emps.find(function (e) {
    return e.id === currentEmpId;
  });
  if (emp) showEditModal(emp.id);
}
function deleteEmployee(empId) {
  var wasCurrent = localStorage.getItem('sb_current_emp') === empId;
  var emp = emps.find(function (e) { return e.id === empId; });
  if (emp) {
    emp.status = 'archived';
    emp.archived = true;
    emp.archivedAt = new Date().toISOString();
  }
  // 删除不再同步到 saveEmployees，直接调用后端 DELETE 做软删除
  if (typeof apiFetch === 'function') {
    apiFetch('/api/agents/' + empId, {
      method: 'DELETE'
    }).catch(function () {});
  }
  closeEmpDetail();
  renderEmployeeList();
  if (wasCurrent) {
    openNextEmployeeOrEmpty();
  } else {
    renderMsgs('private');
  }
  showToast('🗑️ 已删除');
}
function deleteEmpFromDetail() {
  if (!currentEmpId) return;
  if (confirm('确定要删除这个员工吗？')) {
    deleteEmployee(currentEmpId);
    closeEmpDetail();
  }
}
function closeDrawer() {
  document.getElementById('drawerOverlay').classList.remove('open');
  drawerOpen = false;
}
function switchDrawerTab(tab) {
  document.querySelectorAll('.drawer-tab').forEach(function (t) {
    return t.classList.toggle('active', t.dataset.tab === tab);
  });
  document.querySelectorAll('.tab-pane').forEach(function (p) {
    return p.classList.toggle('active', p.id === 'tab-' + tab);
  });
  if (tab === 'team') renderCurrentTeamList();
}

// Model selection
let selectedModel = 'gpt4o';
function selectModel(el, modelId) {
  document.querySelectorAll('.model-option').forEach(function (o) {
    return o.classList.remove('selected');
  });
  el.classList.add('selected');
  selectedModel = modelId;
}

// Employee management

// 统一获取员工职能显示名称，未设置返回空字符串
function getEmpRoleDisplay(emp) {
  if (!emp) return '成员';
  var role = emp.role;
  if (role === '__custom__') {
    if (emp.subCategory && emp.subCategory !== '__custom__' && emp.subCategory !== 'custom') {
      return emp.subCategory;
    }
    return '成员';
  }
  if (!role || role === 'unknown') return '成员';
  return role;
}

function renderCurrentTeamList() {
  const list = document.getElementById('currentTeamList');
  if (!list) return;
  list.innerHTML = emps.map(function (e) {
    var roleDisplay = getEmpRoleDisplay(e);
    return '<div class="team-member">' +
      '<div class="team-avatar" style="background:' + escapeAttr(e.bg || '') + ';display:flex;align-items:center;justify-content:center;">' + renderAvatar(e, 40) + '</div>' +
      '<div class="team-info">' +
      '<div class="team-name">' + escapeHtml(e.name || '') + '</div>' +
      '<div class="team-role">' + escapeHtml(roleDisplay) + '</div>' +
      (e.model ? '<div class="team-model">' + escapeHtml(e.model) + '</div>' : '') +
      '</div></div>';
  }).join('');
}

// Modal
function showModal(title, html) {
  var overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'dynamicModal';
  overlay.style.display = 'flex';
  // If html starts with '<div class="member-modal"', use it directly (custom modal)
  // Otherwise wrap in default modal-panel
  var isCustomModal = html.indexOf('member-modal') > 0;
  if (isCustomModal) {
    overlay.innerHTML = html;
  } else {
    overlay.innerHTML = '<div class="modal-panel" style="width:400px;max-width:95vw;">' + '<div style="padding:20px;">' + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">' + '<h3 style="margin:0;">' + escapeHtml(title) + '</h3>' + '<button onclick="closeModal(\'dynamicModal\')" style="background:none;border:none;font-size:20px;cursor:pointer;">✕</button>' + '</div>' + '<div>' + html + '</div>' + '</div></div>';
  }
  document.body.appendChild(overlay);
  setTimeout(function () {
    overlay.classList.add('show');
  }, 10);
}
function closeModal(id) {
  var el = document.getElementById(id || 'dynamicModal');
  if (el) {
    el.classList.remove('show');
    setTimeout(function () {
      // 只移除动态创建的 modal，保留静态 HTML 元素
      if (el.id === 'dynamicModal') {
        el.remove();
      }
    }, 300);
  }
}

// Settings
function toggleSettings() {
  switchModule('settings');
}

// Activity Feed Auto Update
const activities = ['完成了代码提交', '更新了文档', '通过了测试', '部署成功', '提交了 PR'];
registerTimerTask(function () {
  const feed = document.getElementById('tab-activity');
  if (feed && feed.classList.contains('active')) {
    const emp = emps[Math.floor(Math.random() * emps.length)];
    const act = activities[Math.floor(Math.random() * activities.length)];
    const html = `<div class="activity-item">
<div class="activity-icon">${emp.name.charAt(0)}</div>
<div class="activity-text"><strong>${emp.name}</strong> ${act}</div>
<div class="activity-time">刚刚</div>
</div>`;
    feed.insertAdjacentHTML('afterbegin', html);
    if (feed.children.length > 6) feed.lastElementChild.remove();
  }
}, 15);

// Init
loadKanbanTasks();
initTaskTimeoutTimers();
renderEmployeeList();
renderTaskBoard();
startReminderCheck();

// Keyboard Navigation
document.addEventListener('keydown', function (e) {
  const isMac = navigator.platform.toUpperCase().indexOf('MAC') >= 0;
  const modKey = isMac ? e.metaKey : e.ctrlKey;
  // Cmd/Ctrl + K = Focus search
  if (modKey && e.key === 'k') {
    e.preventDefault();
    (function () {
      var el = document.getElementById('globalSearchInput');
      if (el) el.focus();
    })();
  }
  // Cmd/Ctrl + N = New
  if (modKey && e.key === 'n') {
    e.preventDefault();
    openGroupWizard();
  }
  // Esc = Close modals/drawer
  if (e.key === 'Escape') {
    if (document.querySelector('.group-wizard-overlay.show')) {
      closeGroupWizard();
    } else if (document.querySelector('.modal-overlay.show')) {
      document.querySelectorAll('.modal-overlay.show').forEach(function (m) {
        return m.classList.remove('show');
      });
    } else if (document.querySelector('.drawer-overlay.open')) {
      closeDrawer();
    } else if (document.querySelector('.app-sidebar.open')) {
      toggleSidebar();
    }
  }
  // Enter in input = Send
  if (e.key === 'Enter' && document.activeElement.id === 'msgInput') {
    e.preventDefault();
    sendMsg();
  }
});

// Mobile sidebar toggle
function toggleSidebar() {
  document.querySelector('.app-sidebar').classList.toggle('open');
}

// ★ fix/iphone16-visual-polish: ≤768 时点 chat-topbar-team 切换 6 个 AI 员工 agent 展开/收起
// 默认隐藏 6 个 .chat-topbar-agent, 加 .expanded class 显示 popover (CSS @media 块控制)
function toggleChatTopbarTeam() {
  if (window.innerWidth > 768) return;  // 桌面直接放行, 6 个 agent 默认横向铺开
  var team = document.getElementById('chatTopbarTeam');
  if (team) team.classList.toggle('expanded');
}

// CountTo Animation
function countTo(el, end, duration = 1500, prefix = '', suffix = '') {
  const start = 0;
  const startTime = performance.now();
  const step = function (currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = Math.floor(start + (end - start) * eased);
    el.textContent = prefix + current.toLocaleString() + suffix;
    if (progress < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// Global Search (Soybean-inspired)
function initGlobalSearch() {
  const searchBtn = document.getElementById('searchBtn');
  const searchModal = document.getElementById('globalSearchModal');
  if (searchBtn && searchModal) {
    searchBtn.addEventListener('click', function () {
      return searchModal.classList.add('show');
    });
    document.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        searchModal.classList.add('show');
      }
    });
  }
}

// 初始化统计卡片和通知
function initStatsAndNotifications() {
  updateStats();
  registerTimerTask(updateStats, 30); // 每30秒更新一次统计
}

// 初始化浏览器通知权限
function initNotifications() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission();
  }
}

// 更新统计卡片
function updateStats() {
  const employeeCount = emps.length;
  const projectCount = typeof projects !== 'undefined' ? projects.length : 0;
  const taskCount = (tasksData['待办'] || []).length + (tasksData['进行中'] || []).length;
  const messageCount = emps.filter(function (e) {
    return e.unread > 0;
  }).length;
  var sec = document.getElementById('statEmployeeCount');
  if (sec) sec.textContent = employeeCount;
  var spc = document.getElementById('statProjectCount');
  if (spc) spc.textContent = projectCount;
  var stc = document.getElementById('statTaskCount');
  if (stc) stc.textContent = taskCount;
  var smc = document.getElementById('statMessageCount');
  if (smc) smc.textContent = messageCount;
}

// 快捷操作函数
function createNewProject() {
  switchTab('projects');
  showToast('➕ 创建新项目...');
}
function addNewEmployee() {
  switchTab('employees');
  (function () {
    var el = document.getElementById('addEmployeeBtn');
    if (el) el.click();
  })();
  showToast('👤 添加新员工...');
}
function startGroupChat() {
  openGroupWizard();
}
function showDashboard() {
  switchTab('employees');
  showToast('📊 查看数据报表...');
}
function showEmployees() {
  switchTab('employees');
  showToast('👥 团队成员管理');
}
function showProjects() {
  switchTab('projects');
  showToast('📁 项目管理');
}
function showTasks() {
  toggleTaskBoard();
  showToast('✅ 查看任务列表');
}
function showMessages() {
  switchTab('employees');
  showToast('💬 查看未读消息');
}

// 通知管理
function closeGlobalSearch() {
  (function () {
    var el = document.getElementById('globalSearchModal');
    if (el) el.classList.remove('show');
  })();
  document.getElementById('globalSearchInput') && (document.getElementById('globalSearchInput').value = '');
  document.getElementById('searchResults') && (document.getElementById('searchResults').innerHTML = '');
}
function performGlobalSearch(q) {
  const results = document.getElementById('searchResults');
  if (!results) return;
  const lowerQ = q.toLowerCase();
  const items = [];
  emps.forEach(function (emp) {
    var roleForSearch = getEmpRoleDisplay(emp);
    if (emp.name.toLowerCase().includes(lowerQ) || roleForSearch.toLowerCase().includes(lowerQ)) items.push({
      type: '员工',
      name: emp.name,
      desc: getEmpRoleDisplay(emp),
      icon: renderAvatar(emp, 24),
      action: function () {
        return openEmpDetail(emp.id);
      }
    });
  });
  Object.keys(tasksData).forEach(function (status) {
    tasksData[status].forEach(function (t) {
      if (t.title.toLowerCase().includes(lowerQ)) items.push({
        type: '任务',
        name: t.title,
        desc: t.assignee || status,
        icon: '📋',
        action: function () {
          toggleTaskBoard();
        }
      });
    });
  });
  if (typeof projects !== 'undefined' && projects.length) projects.forEach(function (p) {
    if (p.name.toLowerCase().includes(lowerQ)) items.push({
      type: '项目',
      name: p.name,
      desc: (p && p.members && p.members.length) + '个成员',
      icon: '📁',
      action: function () {
        return openProject(p.id);
      }
    });
  });
  results.innerHTML = items.length ? items.map(function (i, idx) {
    return `<div class="search-result-item" onclick="this.dataset.action&&(${i.action})();closeGlobalSearch();" data-action="${escapeAttr(i.action || '')}">
<div class="search-result-icon">${escapeHtml(i.icon || '')}</div>
<div class="search-result-info"><div class="search-result-name">${escapeHtml(i.name || '')}</div><div class="search-result-desc">${escapeHtml(i.type || '')} · ${escapeHtml(i.desc || '')}</div></div>
<div class="search-result-shortcut"><kbd>Enter</kbd></div></div>`;
  }).join('') : `<div class="search-empty">🔍 没有找到 "${escapeHtml(q)}" 相关结果</div>`;
}

// Lounge Functions (drawer removed; kept for compatibility)
function toggleLounge() {
  // lounge drawer removed
}
function renderLounge() {
  var content = document.getElementById('loungeContent');
  var total = emps.length;
  var onlineCount = emps.filter(function (e) {
    return e.status === 'online' || e.status === 'busy';
  }).length;
  var workingCount = emps.filter(function (e) {
    return e.status === 'thinking' || e.status === 'coding' || e.status === 'reading';
  }).length;
  function getStatusDot(status) {
    if (status === 'online' || status === 'busy') return '🟢';
    if (status === 'thinking' || status === 'coding' || status === 'reading') return '🟡';
    if (status === 'waiting') return '💤';
    return '⚫';
  }
  if (total === 0) {
    content.innerHTML = '<div class="lounge-empty"><div class="lounge-empty-icon">☀️</div><div class="lounge-empty-text">暂无员工</div></div>';
  } else {
    content.innerHTML = '<div class="lounge-stat">🏖️ 休闲区 · <span>' + onlineCount + '</span> 在线 / <span>' + workingCount + '</span> 工作中 / ' + total + ' 总计</div>' + emps.map(function (e) {
      var statusDot = getStatusDot(e.status);
      var roleTag = getEmpRoleDisplay(e) ? '<span class="lounge-role-tag">' + escapeHtml(getEmpRoleDisplay(e)) + '</span>' : '';
      var todayChatCount = 0;
      return '<div class="lounge-item" data-emp-id="' + escapeAttr(e.id || '') + '" onclick="openEmpDetail(\'' + escapeAttr(e.id || '') + '\')">' + '<div class="lounge-avatar-wrap">' + '<div class="avatar" style="background:' + escapeAttr(e.bg || e.color || '#999') + ';display:flex;align-items:center;justify-content:center;">' + renderAvatar(e, 36) + '</div>' + '<div class="lounge-status-dot">' + statusDot + '</div>' + '</div>' + '<div class="lounge-item-info">' + '<div class="lounge-item-name">' + escapeHtml(e.name || '') + ' ' + roleTag + '</div>' + '<div class="lounge-item-status">' + escapeHtml(e.msg || e.lastActive || '离线') + '</div>' + '</div>' + (e.status === 'offline' ? '<button class="lounge-wake-btn" onclick="event.stopPropagation();wakeUpEmployee(\'' + escapeAttr(e.id || '') + '\')" title="唤醒员工">☀️</button>' : '') +
      // Hover preview card
      '<div class="lounge-preview-card">' + '<div class="lounge-preview-header">' + '<div class="avatar" style="background:' + escapeAttr(e.bg || e.color || '#999') + ';display:flex;align-items:center;justify-content:center;">' + renderAvatar(e, 32) + '</div>' + '<div>' + '<div class="lounge-preview-name">' + statusDot + ' ' + escapeHtml(e.name || '') + '</div>' + '<div class="lounge-preview-role">' + escapeHtml(getEmpRoleDisplay(e)) + '</div>' + '</div>' + '</div>' + '<div class="lounge-preview-divider"></div>' + '<div class="lounge-preview-row">📋 <span class="lounge-preview-label">当前：</span>' + escapeHtml(e.msg || '暂无任务') + '</div>' + '<div class="lounge-preview-row">💬 <span class="lounge-preview-label">今日对话：</span>' + todayChatCount + '条</div>' + '<div class="lounge-preview-row">⏱️ <span class="lounge-preview-label">状态：</span>' + escapeHtml(e.status || '离线') + '</div>' + '</div>' + '</div>';
    }).join('');
  }
}
function wakeUpEmployee(empId) {
  const emp = emps.find(function (e) {
    return e.id === empId;
  });
  if (emp) {
    emp.status = 'online';
    emp.lastActive = formatDate();
    saveEmployees();
    renderEmployeeList();
    renderLounge();
    showToast(`☀️ ${emp.name} 已上线！`);
  }
}

// ========== AI 配置面板 ==========
function showAIConfigPanel() {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.id = 'aiConfigOverlay';
  overlay.onclick = closeAIConfigPanel;
  const panel = document.createElement('div');
  panel.className = 'modal-panel';
  panel.style.cssText = 'width:400px;max-width:95vw;';
  panel.onclick = function (e) {
    return e.stopPropagation();
  };
  panel.innerHTML = `
    <div style="padding:20px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;">
        <h3 style="margin:0;">🤖 AI 配置</h3>
        <button onclick="closeAIConfigPanel()" style="background:none;border:none;font-size:20px;cursor:pointer;">✕</button>
      </div>
      
      <div style="margin-bottom:16px;">
        <div style="display:flex;justify-content:space-between;align-items:center;padding:12px;background:#f5f5f7;border-radius:8px;">
          <span>启用真实 AI</span>
          <label style="position:relative;display:inline-block;width:44px;height:24px;">
            <input type="checkbox" id="aiEnabled" ${AI_CONFIG.enabled ? 'checked' : ''} onchange="toggleAIEnabled(this.checked)" style="opacity:0;width:0;height:0;">
            <span style="position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:${AI_CONFIG.enabled ? '#34C759' : '#ccc'};border-radius:24px;transition:0.3s;"></span>
            <span style="position:absolute;content:'';height:18px;width:18px;left:${AI_CONFIG.enabled ? '22px' : '3px'};bottom:3px;background:white;border-radius:50%;transition:0.3s;"></span>
          </label>
        </div>
      </div>
      
      <div id="aiSettingsPanel" style="opacity:${AI_CONFIG.enabled ? 1 : 0.5};pointer-events:${AI_CONFIG.enabled ? 'auto' : 'none'};">
        <div style="margin-bottom:12px;">
          <label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">API 模式</label>
          <select id="aiMode" onchange="toggleAIMode(this.value)" style="width:100%;padding:10px;border:1px solid #eee;border-radius:8px;">
            <option value="copaw" ${AI_CONFIG.endpoint === 'copaw' || !AI_CONFIG.endpoint ? 'selected' : ''}>🤖 CoPaw Agent（推荐）</option>
            <option value="openai" ${AI_CONFIG.endpoint === 'openai' ? 'selected' : ''}>🌐 OpenAI API</option>
            <option value="custom" ${AI_CONFIG.endpoint && AI_CONFIG.endpoint !== 'copaw' && AI_CONFIG.endpoint !== 'openai' ? 'selected' : ''}>⚙️ 自定义 API</option>
          </select>
        </div>
        
        <div id="customApiPanel" style="display:${AI_CONFIG.endpoint && AI_CONFIG.endpoint !== 'copaw' && AI_CONFIG.endpoint !== 'openai' ? 'block' : 'none'};">
          <div style="margin-bottom:12px;">
            <label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">API 地址</label>
            <input type="text" id="aiEndpoint" value="${AI_CONFIG.endpoint === 'openai' ? '' : AI_CONFIG.endpoint}" placeholder="https://api.example.com/v1/chat" style="width:100%;padding:10px;border:1px solid #eee;border-radius:8px;">
          </div>
          <div style="margin-bottom:12px;">
            <label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">API Key</label>
            <input type="password" id="aiApiKey" value="${AI_CONFIG.apiKey || ''}" placeholder="sk-..." style="width:100%;padding:10px;border:1px solid #eee;border-radius:8px;">
          </div>
        </div>
        
        <div style="margin-bottom:12px;">
          <label style="display:block;font-size:12px;color:#666;margin-bottom:4px;">AI 模型</label>
          <select id="aiModel" style="width:100%;padding:10px;border:1px solid #eee;border-radius:8px;">
            <option value="gpt-4o" ${AI_CONFIG.model === 'gpt-4o' ? 'selected' : ''}>GPT-4o</option>
            <option value="gpt-4-turbo" ${AI_CONFIG.model === 'gpt-4-turbo' ? 'selected' : ''}>GPT-4 Turbo</option>
            <option value="gpt-3.5-turbo" ${AI_CONFIG.model === 'gpt-3.5-turbo' ? 'selected' : ''}>GPT-3.5 Turbo</option>
            <option value="claude-3-opus" ${AI_CONFIG.model === 'claude-3-opus' ? 'selected' : ''}>Claude 3 Opus</option>
            <option value="claude-3-sonnet" ${AI_CONFIG.model === 'claude-3-sonnet' ? 'selected' : ''}>Claude 3 Sonnet</option>
          </select>
        </div>
        
        <div style="background:#fff8e6;padding:12px;border-radius:8px;font-size:12px;color:#996600;margin-top:12px;">
          <div style="margin-bottom:6px;"><strong>💡 CoPaw Agent 模式：</strong></div>
          <div>直接使用当前 AI 助手（妍妍）处理消息，无需额外配置。</div>
        </div>
      </div>
      
      <div style="display:flex;gap:8px;margin-top:20px;">
        <button onclick="closeAIConfigPanel()" style="flex:1;padding:10px;border:1px solid #eee;border-radius:8px;background:white;cursor:pointer;">取消</button>
        <button onclick="applyAIConfig()" style="flex:1;padding:10px;border:none;border-radius:8px;background:var(--accent);color:white;cursor:pointer;font-weight:600;">应用</button>
      </div>
    </div>
  `;
  overlay.appendChild(panel);
  document.body.appendChild(overlay);
  setTimeout(function () {
    return overlay.classList.add('show');
  }, 10);
}
function closeAIConfigPanel() {
  const overlay = document.getElementById('aiConfigOverlay');
  if (overlay) {
    overlay.classList.remove('show');
    setTimeout(function () {
      return overlay.remove();
    }, 300);
  }
}
function toggleAIEnabled(enabled) {
  AI_CONFIG.enabled = enabled;
  const panel = document.getElementById('aiSettingsPanel');
  if (panel) {
    panel.style.opacity = enabled ? 1 : 0.5;
    panel.style.pointerEvents = enabled ? 'auto' : 'none';
  }
  // 更新 toggle 样式
  const toggle = document.querySelector('#aiEnabled + span');
  const toggleThumb = document.querySelector('#aiEnabled + span + span');
  if (toggle) toggle.style.background = enabled ? '#34C759' : '#ccc';
  if (toggleThumb) toggleThumb.style.left = enabled ? '22px' : '3px';
}
function toggleAIMode(mode) {
  const customPanel = document.getElementById('customApiPanel');
  if (customPanel) customPanel.style.display = mode === 'custom' ? 'block' : 'none';
}
function applyAIConfig() {
  AI_CONFIG.enabled = document.getElementById('aiEnabled').checked;
  const mode = document.getElementById('aiMode').value;
  AI_CONFIG.model = document.getElementById('aiModel').value;
  if (mode === 'copaw') {
    AI_CONFIG.endpoint = 'copaw';
    AI_CONFIG.apiKey = '';
  } else if (mode === 'openai') {
    AI_CONFIG.endpoint = 'openai';
    AI_CONFIG.apiKey = document.getElementById('aiApiKey').value;
  } else {
    AI_CONFIG.endpoint = document.getElementById('aiEndpoint').value;
    AI_CONFIG.apiKey = document.getElementById('aiApiKey').value;
  }
  saveAIConfig();
  showToast(AI_CONFIG.enabled ? '🤖 AI 已启用' : '💬 已切换为模拟 AI', 'success');
  closeAIConfigPanel();
}

// 调用真实 AI API（纯前端版）
async function callRealAI(userMessage, context) {
  if (!AI_CONFIG.enabled) return null;
  var apiKey = AI_CONFIG.apiKey;
  if (!apiKey || apiKey === 'your-api-key') {
    return '⚠️ 请先配置 OpenAI API Key\n\n点击右上角 🤖 按钮 → 填入你的 API Key';
  }
  try {
    var currentEmp = getCurrentEmployeeInfo();
    var systemPrompt = '你是 ' + currentEmp.name + '，一个 ' + getEmpRoleDisplay(currentEmp) + '。请用第一人称回复，保持角色一致性。不要编造不存在的任务、数据或提醒信息，只基于用户实际提供的内容回复。';
    // 注入层级关系约束，防止 AI 把老板当学生/下属
    var userRole = getCurrentUserRoleDisplay();
    var userName = currentUser && currentUser.name || currentUser && currentUser.displayName || '用户';
    systemPrompt += '\n\n【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- ' + userName + '（' + userRole + '）是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。\n- 严禁用"教你""指导你""你做过吗""你懂吗"等居高临下的语气。\n- 老板问你问题时，直接回答，不要反问或考验老板。';
    if (context) systemPrompt += '\n\n【相关文档】\n' + context;
    var messages = [{
      role: 'system',
      content: systemPrompt
    }, {
      role: 'user',
      content: userMessage
    }];

    // 直接调用 OpenAI API
    var res = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey
      },
      body: JSON.stringify({
        model: AI_CONFIG.model || 'gpt-4o',
        messages: messages,
        temperature: 1.0
      })
    });
    if (!res.ok) {
      var err = await res.json().catch(function () {
        return {};
      });
      if (res.status === 401) {
        return '⚠️ API Key 无效，请检查是否正确';
      } else if (res.status === 429) {
        return '⚠️ 请求过于频繁，请稍后再试\n或升级你的 OpenAI 套餐';
      }
      return '⚠️ API 错误 (' + res.status + '): ' + (err && err.error && err.error.message || '未知错误');
    }
    var data = await res.json();
    return data.choices && data.choices[0] && data.choices[0].message && data.choices[0].message.content || '⚠️ 返回格式未知';
  } catch (error) {
    if (error.message.includes('Failed to fetch') || error.message.includes('NetworkError')) {
      return '⚠️ 网络错误，可能遇到 CORS 限制\n\n解决方案：\n1. 安装 "Allow CORS" 浏览器扩展\n2. 或使用 Chrome 启动参数：\n   --disable-web-security --user-data-dir';
    }
    return '⚠️ AI 调用失败: ' + error.message;
  }
}

// ========== 图片转文字描述（前置处理） ==========
async function describeImage(imageBase64, apiKey) {
  try {
    var endpoint = 'https://api.kimi.com/coding/v1/chat/completions';
    var res = await apiFetch('/api/proxy', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-AI-API-Key': apiKey,
        'X-Target-URL': endpoint
      },
      body: JSON.stringify({
        model: 'k3',
        messages: [
          {
            role: 'user',
            content: [
              { type: 'text', text: '请详细描述这张图片的全部内容，包括所有可见的文字、物品、颜色、布局、数字、价格等信息。如果是商品图片，请提取商品名称、品牌、价格、规格、库存、卖点等关键信息。尽量完整详细。' },
              { type: 'image_url', image_url: { url: imageBase64 } }
            ]
          }
        ],
        temperature: 1.0,
        max_tokens: 2000
      })
    });
    var data = await res.json();
    if (data && data.choices && data.choices[0] && data.choices[0].message) {
      return data.choices[0].message.content || '';
    }
    return '';
  } catch (e) {
    console.warn('[Image] 图片识别失败:', e);
    return '';
  }
}

// ========== API 直连发送 ==========
async function sendViaAPI(emp, userMessage, docContext, timeStr, images) {
  images = images || [];
  var providerId = emp.aiProvider || 'kimi';
  var provider = API_PROVIDERS[providerId];

  // 统一恢复员工在线状态
  function restoreOnline() {
    window._thinkingTimeouts = window._thinkingTimeouts || {};
    clearTimeout(window._thinkingTimeouts[emp.id]);
    delete window._thinkingTimeouts[emp.id];
    notifyOfficeStatusChange(emp.id, 'online');
    var _e = emps.find(function (e) { return e.id === emp.id; });
    if (_e) {
      _e.status = 'online';
      renderEmployeeList();
    }
  }

  if (!provider) {
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    displayAIReply('⚠️ AI 供应商配置错误', docContext, timeStr, emp.id, []);
    restoreOnline();
    return;
  }
  var apiKey = emp.apiKey;
  var baseUrl = provider.baseUrl;
  var model = emp.apiModel || provider.defaultModel;

  // 自定义供应商处理
  if (providerId === 'custom') {
    baseUrl = emp.customEndpoint || '';
    model = emp.apiModel || 'custom-model';
  }

  // ==== 加载上下文、摘要（记忆已由后端统一注入）====
  var chatContext = '';
  var summaryText = '';
  try {
    chatContext = await buildRecentChatContext(emp.id, 10);
  } catch (e) {}
  try {
    var summaryRes = await apiFetch('/api/chat/summarize/' + encodeURIComponent(emp.id));
    var summaryData = await summaryRes.json();
    if (summaryData && summaryData.summary) {
      summaryText = '\n\n【历史对话摘要】\n' + summaryData.summary;
    }
  } catch (e) {}

  // 构建请求
  var systemPrompt = '';
  if (emp.soulDoc) systemPrompt = emp.soulDoc;else if (emp.systemPrompt) systemPrompt = emp.systemPrompt;
  if (!systemPrompt) systemPrompt = '你是 ' + emp.name + '，一个 ' + getEmpRoleDisplay(emp) + '。请用第一人称回复，保持角色一致性。不要编造不存在的任务、数据或提醒信息，只基于用户实际提供的内容回复。';
  // 注入当前用户身份信息，明确层级关系，防止 AI 把老板当学生/下属
  var userRole2 = getCurrentUserRoleDisplay();
  var userName2 = currentUser && currentUser.name || currentUser && currentUser.displayName || '用户';
  systemPrompt += '\n\n【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- ' + userName2 + '（' + userRole2 + '）是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。\n- 严禁用"教你""指导你""你做过吗""你懂吗"等居高临下的语气。\n- 老板问你问题时，直接回答，不要反问或考验老板。';
  if (docContext) systemPrompt += '\n\n【相关文档】\n' + docContext;
  if (summaryText) {
    systemPrompt += summaryText;
  }

  // 注入商品库/达人库/匹配引擎工具说明（主大脑管控，X-Agent-Id 硬编码为员工ID）
  systemPrompt += buildAgentToolsContext(emp.id);
  if (images && images.length > 0) {
    systemPrompt += '\n\n【图片识别指令】当用户发送图片时，请直接识别并描述图片内容，不要调用任何工具。';
  }

  // 注入 RAG 检索结果（产品知识库）
  var ragResult2 = { context: '', citations: [] };
  try {
    ragResult2 = await retrieveRAGContext(emp.id, userMessage);
    if (ragResult2.context) {
      systemPrompt += '\n\n' + ragResult2.context;
    }
  } catch (e) {
    console.warn('[RAG] API 路径注入失败:', e);
  }

  // AI 调用前防御：最终 systemPrompt 必须包含身份约束关键字
  if (!validateFinalSystemPrompt(systemPrompt)) {
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    displayAIReply('⚠️ AI身份约束缺失，禁止调用AI', docContext, timeStr, emp.id, []);
    restoreOnline();
    return;
  }

  var messages = [{
    role: 'system',
    content: systemPrompt
  }];
  if (chatContext) {
    messages.push({ role: 'system', content: chatContext });
  }
  if (images && images.length > 0) {
    var userContent = [{type: 'text', text: userMessage}];
    images.forEach(function(img) {
      userContent.push({type: 'image_url', image_url: {url: img.base64}});
    });
    messages.push({role: 'user', content: userContent});
  } else {
    messages.push({
      role: 'user',
      content: userMessage
    });
  }
  var endpoint = baseUrl + '/chat/completions';

  // 智谱 API 路径不同
  if (providerId === 'zhipu') {
    endpoint = baseUrl + '/chat/completions';
  }
  console.debug('[API] 发送请求 -> /api/proxy endpoint=' + endpoint + ' model=' + model + ' messages=' + messages.length);
  // ★ refactor/chat-retry-pipeline: 30s 端到端 UI 兜底超时(不影响后端 proxy 真在跑的请求,
  //   跟 sendViaOpenClaw 同语义)。retry 留给下一轮把 .then 链改 async/await 后再做。
  _withTimeout(apiFetch('/api/proxy', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': authToken && authToken !== 'local_mode' ? 'Bearer ' + authToken : '',
      'X-AI-API-Key': apiKey,
      'X-Target-URL': endpoint,
      'X-Agent-Id': emp.id || '',
    },
    body: JSON.stringify({
      model: model,
      messages: messages,
      temperature: 0.8,
      max_tokens: 2000
    })
  }), TIMEOUT_CHAT_MS, 'API chat').then(function (res) {
    console.debug('[API] /api/proxy 响应 status=' + (res ? res.status : 'null') + ' ok=' + (res ? res.ok : 'null'));

    // 这里 t 是 sendViaAPI 函数作用域的变量，但只在 if 早退分支里被赋值过；
    // 走正常 success 路径时 t 仍是 undefined，所以 loading 状态不会被清除
    // ——导致 Helen 显示一条转圈 + 一条实际回复。改为局部声明。
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    if (!res || !res.ok) {
      var status = res ? res.status : 0;
      console.debug('[API] /api/proxy 请求失败 status=' + status);
      if (status === 401) {
        displayAIReply('⚠️ API Key 无效，请检查 ' + (provider ? provider.name : 'AI供应商') + ' 的 API Key 是否正确', docContext, timeStr, emp.id, []);
      } else if (status === 429) {
        displayAIReply('⚠️ 请求过于频繁，请稍后再试', docContext, timeStr, emp.id, []);
      } else {
        displayAIReply('⚠️ ' + provider.name + ' API 错误 (' + status + ')', docContext, timeStr, emp.id, []);
      }
      restoreOnline();
      return;
    }
    return res.json();
  }).then(function (data) {
    if (data && data.choices && data.choices[0] && data.choices[0].message) {
      var reply = data.choices[0].message.content || '';
      console.debug('[API] AI回复 content_len=' + reply.length);
      displayAIReply(reply, docContext, timeStr, emp.id, ragResult2.citations);
      saveChatHistory(emp.id, userMessage, reply, docContext, ragResult2.citations, images);
    } else if (data) {
      console.debug('[API] 返回格式异常 data=', JSON.stringify(data).substring(0, 200));
      displayAIReply('⚠️ ' + provider.name + ' 返回格式异常', docContext, timeStr, emp.id, []);
    }
    restoreOnline();
  }).catch(function (error) {
    console.debug('[API] /api/proxy 异常:', error.message || error);
    var t = document.getElementById('typingMsg');
    if (t) t.remove();
    if (error.message && (error.message.indexOf('Failed to fetch') >= 0 || error.message.indexOf('NetworkError') >= 0 || error.message.indexOf('CORS') >= 0)) {
      displayAIReply('⚠️ 网络/CORS 错误\n\n浏览器直接调用 API 会遇到 CORS 限制。\n当前使用模拟回复，后续可通过后端代理解决。', docContext, timeStr, emp.id, []);
      // CORS 错误时 fallback 到 mock
      setTimeout(function () {
        var simReplies = ['收到，我来处理一下。', '好的，马上开始。', '明白了，让我看看...'];
        var simReply = simReplies[Math.floor(Math.random() * simReplies.length)];
        displayAIReply(simReply, docContext, timeStr, emp.id, []);
        saveChatHistory(emp.id, userMessage, simReply, docContext, [], images);
      }, 500);
    } else {
      displayAIReply('⚠️ API 调用失败: ' + error.message, docContext, timeStr, emp.id, []);
    }
    restoreOnline();
  });
}

// 获取当前对话的员工信息
function getCurrentEmployeeInfo() {
  const empId = localStorage.getItem('sb_current_emp');
  if (!empId) return null;
  const emp = emps.find(function (e) {
    return e.id === empId;
  });
  return emp || null;
}

function isEmployeeNotFoundError(res, data) {
  if (!res || !data) return false;
  return res.status === 404 && data.error === '员工不存在';
}

function closeCurrentChatAsDeleted() {
  localStorage.removeItem('sb_current_emp');
  closeEmpDetail();
  renderEmptyChat();
  renderEmployeeList();
  showToast('该员工已被删除');
}

function openNextEmployeeOrEmpty() {
  var visible = getVisibleEmps().filter(function (e) {
    return e && e.id && !e.isProject && !e.archived;
  });
  if (visible.length) {
    openChat(visible[0].id);
  } else {
    localStorage.removeItem('sb_current_emp');
    renderEmptyChat();
    renderEmployeeList();
  }
}

function validateFinalSystemPrompt(systemPrompt) {
  if (!systemPrompt || systemPrompt.indexOf('管理员是你的老板') < 0) {
    showToast('AI身份约束缺失，禁止调用AI');
    return false;
  }
  return true;
}

// 保存聊天历史
// ===== 聊天记录后端同步 =====
async function saveChatToServer(projId, userMsg, aiMsg, docContext, citations, images, injectionTags) {
  if (typeof apiFetch !== 'function') return;
  citations = citations || [];
  images = images || [];
  var baseUrl = '/api/chat/' + encodeURIComponent(projId);

  try {
    // user 消息已在 sendMsg() 中即时保存，此处不再保存（userMsg 参数保留但不再使用）
    var resp;
    // 1. 保存 system 消息（如果有文档上下文）
    if (docContext && docContext.length > 0) {
      resp = await apiFetch(baseUrl, {
        method: 'POST',
        body: JSON.stringify({
          role: 'system',
          content: '[文档]' + docContext,
          skipAI: true,
          empId: projId
        })
      });
      if (!resp) throw new Error('System message POST returned null');
    }

    // 2. 保存 assistant 消息（附带引用信息）
    var assistantBody = {
      role: 'assistant',
      content: aiMsg,
      skipAI: true,
      empId: projId
    };
    if (citations.length > 0) {
      assistantBody.citations = citations;
    }
    // 〔r81 批注①〕MS3 召回条数随 assistant 落库（气泡上方 chip 历史可见，只丢不造）
    if (injectionTags && typeof injectionTags === 'object') {
      assistantBody.injectionTags = injectionTags;
    }
    resp = await apiFetch(baseUrl, {
      method: 'POST',
      body: JSON.stringify(assistantBody)
    });
    if (!resp) throw new Error('Assistant message POST returned null');
    // 回填 AI 消息 id 到 displayAIReply 刚插入的气泡节点，供右键「引用」取数
    try {
      var aiSaveData = await resp.json();
      var aiMsgId = aiSaveData && aiSaveData.userMessage && aiSaveData.userMessage.id;
      var aiEl = window._lastAiMsgEl;
      window._lastAiMsgEl = null;
      if (aiMsgId && aiEl && aiEl.isConnected) {
        _registerMsgDom(aiEl, { id: aiMsgId, role: 'assistant', content: aiMsg });
      }
    } catch (e) {
      console.debug('[ChatSync] AI 消息 id 回填失败:', e);
    }

    // 3. 检查是否需要触发摘要生成（每15条消息触发一次）
    var msgsResp = await apiFetch(baseUrl);
    var msgs = await msgsResp.json();
    if (msgs && msgs.length > 0 && msgs.length % 15 === 0) {
      apiFetch('/api/chat/summarize/' + encodeURIComponent(projId), { method: 'POST' }).catch(function (e) {
        console.debug('[Summary] 摘要生成失败:', e);
      });
    }
  } catch (e) {
    console.error('[ChatSync] 保存到后端失败，已中断后续同步:', e);
  }
}
function loadChatFromServer(id, type, callback) {
  if (typeof apiFetch !== 'function') {
    if (callback) callback([]);
    return;
  }
  var chatType = type || 'personal';
  apiFetch('/api/chat/' + encodeURIComponent(id) + '?type=' + encodeURIComponent(chatType)).then(function (res) {
    if (res.status === 404) {
      return res.json().then(function (data) {
        if (data && data.error === '员工不存在') {
          closeCurrentChatAsDeleted();
        }
        return [];
      }).catch(function () { return []; });
    }
    return res.json();
  }).then(function (data) {
    var msgs = [];
    if (data && Array.isArray(data)) {
      msgs = data.map(function (m) {
        return {
          id: m.id || null,
          role: m.role || 'user',
          content: m.content || '',
          groupId: m.groupId || null,
          time: m.timestamp || Date.now(),
          images: m.images || null,
          citations: m.citations || [],
          injectionTags: m.injectionTags || null,
          reply_to: m.reply_to || null,
          reply_to_message: m.reply_to_message || null
        };
      });
      // 按时间升序排序（旧消息在前，新消息在后）
      msgs.sort(function (a, b) {
        return new Date(a.time) - new Date(b.time);
      });
    }
    if (callback) callback(msgs);
  }).catch(function (e) {
    if (callback) callback([]);
  });
}
function deleteChatMsgOnServer(id, type, msgId) {
  if (typeof apiFetch !== 'function') return;
  var chatType = type || 'personal';
  apiFetch('/api/chat/' + encodeURIComponent(id) + '/' + encodeURIComponent(msgId) + '?type=' + encodeURIComponent(chatType), {
    method: 'DELETE'
  }).catch(function (e) {});
}
function clearChatOnServer(id, type) {
  if (typeof apiFetch !== 'function') return;
  var chatType = type || 'personal';
  apiFetch('/api/chat/' + encodeURIComponent(id) + '?type=' + encodeURIComponent(chatType), {
    method: 'DELETE'
  }).catch(function (e) {});
}
function saveChatHistory(empId, userMsg, aiMsg, docContext, citations, images, injectionTags) {
  if (!empId) {
    console.warn('[saveChatHistory] empId 为空，跳过保存');
    return;
  }
  citations = citations || [];
  images = images || [];
  // 同步到后端（injectionTags 随 assistant 消息落库，历史刷新后 chip 仍可渲染）
  saveChatToServer(empId, userMsg, aiMsg, docContext, citations, images, injectionTags);

  // 提取并保存记忆（延迟 800ms，避免与刚结束的 OpenClaw session 冲突）
  setTimeout(function () {
    var privMsgId = 'priv_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6);
    extractAndSaveMemory(empId, userMsg, aiMsg, false, privMsgId);
  }, 800);
}
// 记忆提取幂等集合：key 格式 empId_msgId 或 groupId_msgId，60分钟内重复提取直接跳过
var _memoryExtractedKeys = {};
function _isMemoryExtracted(key) {
  if (!key) return false;
  var ts = _memoryExtractedKeys[key];
  if (!ts) return false;
  if (Date.now() - ts > 60 * 60 * 1000) {
    delete _memoryExtractedKeys[key];
    return false;
  }
  return true;
}
function _markMemoryExtracted(key) {
  if (!key) return;
  _memoryExtractedKeys[key] = Date.now();
}

// 全局记忆提取队列：所有个人/群记忆提取统一排队，同一时间只执行一个，避免并发压垮 OpenClaw
var _memoryExtractQueue = [];
var _memoryExtractRunning = false;
function _enqueueMemoryExtract(fn) {
  _memoryExtractQueue.push(fn);
  if (!_memoryExtractRunning) _processMemoryExtractQueue();
}
function _processMemoryExtractQueue() {
  if (_memoryExtractQueue.length === 0) {
    _memoryExtractRunning = false;
    return;
  }
  _memoryExtractRunning = true;
  var task = _memoryExtractQueue.shift();
  var finished = false;
  function done() {
    if (finished) return;
    finished = true;
    // 微小间隔让事件循环喘息，再执行下一个
    setTimeout(_processMemoryExtractQueue, 50);
  }
  try {
    var res = task(done);
    if (res && typeof res.then === 'function') {
      res.then(function () { done(); }, function (e) { console.error('[MemoryQueue] 执行失败:', e); done(); });
    }
  } catch (e) {
    console.error('[MemoryQueue] 同步异常:', e);
    done();
  }
}

function extractAndSaveMemory(empId, userMsg, aiReply, isGroupChat, msgId) {
  _enqueueMemoryExtract(function (done) {
    _runExtractAndSaveMemory(empId, userMsg, aiReply, isGroupChat, msgId, done);
  });
}
function _runExtractAndSaveMemory(empId, userMsg, aiReply, isGroupChat, msgId, done) {
  var extractKey = (empId || '') + '_' + (msgId || ('priv_' + Date.now()));
  if (_isMemoryExtracted(extractKey)) {
    console.debug('[Memory] extractAndSaveMemory 跳过，msgId 已提取过:', extractKey);
    done();
    return;
  }
  _markMemoryExtracted(extractKey);
  console.debug('[Memory] extractAndSaveMemory 被调用:', empId, 'userMsg长度:', (userMsg || '').length, 'aiReply长度:', (aiReply || '').length, 'isGroupChat:', isGroupChat, 'msgId:', msgId);
  if (typeof apiFetch !== 'function') {
    console.warn('[Memory] apiFetch 不可用，跳过提取');
    done();
    return;
  }

  var emp = emps.find(function (e) { return e.id === empId; });
  if (!emp) {
    console.warn('[Memory] 找不到员工:', empId);
    done();
    return;
  }

  // 先加载已有记忆，避免重复提取
  apiFetch('/api/memory/' + empId).then(function (r) {
    console.debug('[Memory] 加载已有记忆响应状态:', r ? r.status : 'null', '员工:', emp.name);
    return r.json();
  }).then(function (data) {
    if (!data) data = {};
    var allMemories = data.memories || [];
    console.debug('[Memory] 已有记忆数量:', allMemories.length, '员工:', emp.name);

    // 构造提取 prompt：提供已有记忆上下文，要求提取带关系的事实
    var existingDaily = allMemories.filter(function (m) {
      return m.key === 'auto_extract' || m.key === 'auto';
    });
    var existingMemories = existingDaily.map(function (m) {
      return '- ' + (m.value || '');
    }).join('\n');

    var extractPrompt = '【记忆提取任务】\n从以下对话中提取需要长期记住的用户相关信息。要求：\n1. 提取"事实"而非"关键词"，例如不是"蓝色"，而是"用户喜欢蓝色"\n2. 包含上下文关系，例如"用户提到下周要去上海出差"\n3. 值得记住的信息包括：用户偏好、计划安排、重要事实、人际关系、工作/生活背景等\n4. 每条记忆独立成一行，格式：- 关键信息\n5. 如果信息已经在"已有记忆"中，不要重复提取\n6. 只有当确实没有任何值得记住的新信息时，才回复"无"\n7. 不要寒暄，直接输出结果\n\n示例值得提取的记忆：\n- 用户喜欢蓝色\n- 用户下周要去上海出差\n- 用户是产品经理，负责电商业务\n- 用户提到想学习Python编程';

    if (existingMemories) {
      extractPrompt += '\n\n【已有记忆（不要重复提取）】\n' + existingMemories;
    }

    if (isGroupChat) {
      extractPrompt += '\n\n【本次群聊对话】\n群里有人说：' + userMsg.substring(0, 800) + '\n你回复：' + aiReply.substring(0, 800);
    } else {
      extractPrompt += '\n\n【本次对话】\n用户说：' + userMsg.substring(0, 800) + '\nAI回复：' + aiReply.substring(0, 800);
    }

    console.debug('[Memory] 提取方式判断 - openclaw.connected:', typeof openclaw !== 'undefined' && openclaw.connected, 'apiKey:', !!emp.apiKey, 'aiProvider:', !!emp.aiProvider);
    // 根据实际连接状态选择提取方式（不按 connectionType 硬编码）
    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      console.debug('[Memory] 使用 OpenClaw 提取:', emp.name);
      _extractMemoryViaOpenClaw(empId, extractPrompt, allMemories, userMsg, done);
    } else if (emp.apiKey && emp.aiProvider) {
      console.debug('[Memory] 使用 API 提取:', emp.name);
      _extractMemoryViaAPI(empId, extractPrompt, allMemories, userMsg, done);
    } else {
      // 无 AI 可用时，将用户原话作为兜底记忆写入，确保测试环境也能看到记忆记录
      console.warn('[Memory] 无可用提取方式，使用原话兜底:', emp.name);
      _saveMemoryFallback(empId, userMsg, allMemories);
      done();
    }
  }).catch(function (e) {
    console.warn('[Memory] 加载已有记忆失败:', e);
    done();
  });
}

// 通过 OpenClaw WebSocket 提取记忆
function _extractMemoryViaOpenClaw(empId, extractPrompt, existing, userMsg, done) {
  console.debug('[Memory] _extractMemoryViaOpenClaw 开始:', empId);
  var emp = emps.find(function (e) { return e.id === empId; });
  if (!emp) {
    console.warn('[Memory] _extractMemoryViaOpenClaw 找不到员工:', empId);
    if (typeof done === 'function') done();
    return;
  }

  // 记忆提取使用独立 sessionKey，避免与聊天 session 冲突
  var sessionKey = 'agent:' + emp.id + ':memory_extract';

  // 强化上下文隔离：让 AI 明确知道这是一个独立的提取任务，忽略任何之前的对话
  var isolatedPrompt = '【以下是一个完全独立的记忆提取任务，请忽略之前的所有对话上下文，仅根据本次提供的内容执行提取】\n\n' + extractPrompt;

  _sendChatWaitForLifecycle(sessionKey, isolatedPrompt).then(function (chatResult) {
    var extractReply = chatResult.reply || '';
    console.debug('[Memory] OpenClaw 提取完成, 回复长度:', extractReply.length);
    if (extractReply) {
      _processExtractedMemory(empId, extractReply, existing, userMsg);
    } else {
      console.warn('[Memory] OpenClaw 提取结果为空，使用原话兜底:', emp.name);
      _saveMemoryFallback(empId, userMsg, existing);
    }
    if (typeof done === 'function') done();
  }).catch(function (e) {
    console.warn('[Memory] OpenClaw 提取失败:', e, '员工:', emp.name);
    // 发送失败也尝试 fallback
    if (emp.apiKey && emp.aiProvider) {
      console.debug('[Memory] OpenClaw 发送失败，fallback 到 API:', emp.name);
      _extractMemoryViaAPI(empId, extractPrompt, existing, userMsg, done);
    } else {
      _saveMemoryFallback(empId, userMsg, existing);
      if (typeof done === 'function') done();
    }
  });
}

// 通过 API 直接提取记忆
function _extractMemoryViaAPI(empId, extractPrompt, existing, userMsg, done) {
  console.debug('[Memory] _extractMemoryViaAPI 开始:', empId);
  apiFetch('/api/chat/' + empId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content: extractPrompt, skipAI: false })
  }).then(function (r) {
    console.debug('[Memory] API 提取响应状态:', r ? r.status : 'null', '员工:', empId);
    return r.json();
  }).then(function (data) {
    var extracted = (data.aiMessage && data.aiMessage.content) || data.reply || '';
    console.debug('[Memory] API 提取结果长度:', extracted.length, '前100字:', extracted ? extracted.substring(0, 100) : '(空)');
    _processExtractedMemory(empId, extracted, existing, userMsg);
  }).catch(function (e) {
    console.warn('[Memory] API 提取失败:', e, '员工:', empId);
    // API 调用失败也兜底保存用户原话
    if (userMsg) {
      _saveMemoryFallback(empId, userMsg, existing);
    }
  }).finally(function () {
    if (typeof done === 'function') done();
  });
 }

// 记忆去重辅助：会话级缓存 + 文本归一化
window._recentlySavedMemoryValues = window._recentlySavedMemoryValues || {};
function _normalizeMemoryValue(v) {
  if (!v) return '';
  return v.toLowerCase().replace(/[\s【】\[\]()（）.,，。！？!?;；:：、]/g, '').trim();
}
function _memorySimilarity(a, b) {
  if (!a || !b) return 0;
  var na = _normalizeMemoryValue(a);
  var nb = _normalizeMemoryValue(b);
  if (!na || !nb) return 0;
  if (na === nb) return 1;
  // 互相包含视为完全重复
  if (na.indexOf(nb) > -1 || nb.indexOf(na) > -1) return 1;
  // 字符双gram集合Jaccard
  var getBigrams = function (s) {
    var set = {};
    for (var i = 0; i < s.length - 1; i++) {
      set[s.substring(i, i + 2)] = true;
    }
    return set;
  };
  var ba = getBigrams(na);
  var bb = getBigrams(nb);
  var intersection = 0;
  for (var k in ba) {
    if (bb[k]) intersection++;
  }
  var union = 0;
  for (var k in ba) union++;
  for (var kk in bb) {
    if (!ba[kk]) union++;
  }
  if (union === 0) return 0;
  var jaccard = intersection / union;
  // 双向包含度（处理一长一短的情况）
  var charsA = {};
  for (var i = 0; i < na.length; i++) charsA[na[i]] = true;
  var interChars = 0;
  for (var j = 0; j < nb.length; j++) {
    if (charsA[nb[j]]) interChars++;
  }
  var containmentA = na.length > 0 ? interChars / na.length : 0;
  var containmentB = nb.length > 0 ? interChars / nb.length : 0;
  return Math.max(jaccard, containmentA, containmentB);
}
function _isMemoryDuplicate(memValue, existing) {
  if (!memValue || memValue.length < 5) return true;
  var normalized = _normalizeMemoryValue(memValue);
  if (!normalized || normalized.length < 5) return true;
  // 与会话级缓存比较（最近保存的500条）
  if (window._recentlySavedMemoryValues[normalized]) return true;
  // 与已有记忆比较
  if (existing && existing.length > 0) {
    var dup = existing.some(function (m) {
      if (!m.value || m.value.length < 5) return false;
      var mLower = m.value.toLowerCase();
      var memLower = memValue.toLowerCase();
      // 互相包含视为重复
      if (mLower.indexOf(memLower) > -1 || memLower.indexOf(mLower) > -1) return true;
      // 前30个字符相同视为重复
      if (m.value.substring(0, 30) === memValue.substring(0, 30)) return true;
      // 归一化后相同视为重复
      if (_normalizeMemoryValue(m.value) === normalized) return true;
      // 内容相似度>=0.9视为重复
      if (_memorySimilarity(m.value, memValue) >= 0.9) return true;
      return false;
    });
    if (dup) return true;
  }
  return false;
}
function _markMemorySaved(memValue) {
  if (!memValue) return;
  var normalized = _normalizeMemoryValue(memValue);
  if (normalized) window._recentlySavedMemoryValues[normalized] = true;
  // 控制缓存大小，避免无限增长
  var keys = Object.keys(window._recentlySavedMemoryValues);
  if (keys.length > 500) {
    keys.slice(0, keys.length - 500).forEach(function (k) {
      delete window._recentlySavedMemoryValues[k];
    });
  }
}

// 兜底记忆保存：无 AI 可用时直接保存用户消息中的关键信息
function _saveMemoryFallback(empId, userMsg, existing) {
  if (!userMsg || userMsg.length < 3) return;
  // 截取前 200 字作为记忆内容
  var memValue = userMsg.trim().substring(0, 200);
  if (memValue.length < 5) return;
  if (_isMemoryDuplicate(memValue, existing)) {
    console.debug('[Memory] 兜底记忆重复，跳过:', memValue.substring(0, 50));
    return;
  }
  console.debug('[Memory] 准备保存兜底记忆:', memValue.substring(0, 50));
  apiFetch('/api/memory/' + empId, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      key: 'auto_extract',
      value: memValue,
      source: '自动记录（无AI配置）'
    })
  }).then(function (r) {
    console.debug('[Memory] ✅ 兜底记忆保存成功:', memValue.substring(0, 50), '状态:', r ? r.status : 'null');
    _markMemorySaved(memValue);
  }).catch(function (e) {
    if (e && e.message && e.message.indexOf('409') > -1) {
      console.debug('[Memory] 兜底记忆 409 冲突，记忆已存在，静默跳过:', memValue.substring(0, 50));
      return;
    }
    console.warn('[Memory] 兜底记忆保存失败:', e);
  });
}

// 处理提取结果：拆分、去重、保存
function _processExtractedMemory(empId, extracted, existing, userMsg) {
  console.debug('[Memory] _processExtractedMemory 开始, extracted长度:', (extracted || '').length, '前100字:', (extracted || '').substring(0, 100));
  // OpenClaw 返回"无"或空时，不走跳过逻辑，而是兜底保存用户原话，确保每次对话至少一条记录
  if (!extracted || extracted === '无' || extracted.length < 3) {
    console.debug('[Memory] 提取结果为空或"无", 使用原话兜底');
    _saveMemoryFallback(empId, userMsg, existing);
    return;
  }

  // 按行拆分，提取列表项：- 、数字 1. 、* 开头的记忆
  var lines = extracted.split('\n').map(function (l) { return l.trim(); });
  var newMemories = [];
  lines.forEach(function (line) {
    if (!line || line === '无') return;
    var memValue = '';
    if (line.indexOf('- ') === 0 && line.length > 3) {
      memValue = line.substring(2).trim();
    } else if (/^\d+\.\s+/.test(line) && line.length > 4) {
      memValue = line.replace(/^\d+\.\s+/, '').trim();
    } else if (line.indexOf('* ') === 0 && line.length > 3) {
      memValue = line.substring(2).trim();
    }
    if (memValue && memValue !== '无' && memValue.length > 3) {
      newMemories.push(memValue);
    }
  });
  console.debug('[Memory] 解析出列表项记忆数量:', newMemories.length, '样本:', newMemories.slice(0, 3));

  // 先对本次解析出的记忆内部去重
  var uniqueNewMemories = [];
  var seenNormalized = {};
  newMemories.forEach(function (memValue) {
    var norm = _normalizeMemoryValue(memValue);
    if (norm && !seenNormalized[norm]) {
      seenNormalized[norm] = true;
      uniqueNewMemories.push(memValue);
    } else {
      console.debug('[Memory] 本次解析内去重，跳过:', memValue.substring(0, 50));
    }
  });
  newMemories = uniqueNewMemories;
  console.debug('[Memory] 本次解析去重后数量:', newMemories.length);

  // 单条消息提取的记忆数量超过50条直接截断，只保留前50条，避免炸内存
  if (newMemories.length > 50) {
    console.warn('[Memory] 单条消息提取记忆超过50条，截断保留前50条，原始数量=' + newMemories.length);
    newMemories = newMemories.slice(0, 50);
  }

  // 如果没有列表项，尝试把整段作为一条记忆（清理提示词前缀和编号）
  if (newMemories.length === 0) {
    var cleanText = extracted
      .replace(/【记忆提取任务】/g, '')
      .replace(/【已有记忆[^】]*】/g, '')
      .replace(/【本次[^】]*】/g, '')
      .replace(/要求：[^\n]*/g, '')
      .replace(/^\d+[\.、]\s*/gm, '')
      .trim();
    if (cleanText && cleanText !== '无' && cleanText.length > 5 && cleanText.indexOf('要求：') !== 0) {
      newMemories.push(cleanText);
      console.debug('[Memory] 无列表项, fallback 整段作为记忆:', cleanText.substring(0, 80));
    } else {
      console.debug('[Memory] fallback 也不符合保存条件, 清理后文本:', cleanText.substring(0, 80));
    }
  }

  // 去重并保存
  var savedCount = 0;
  newMemories.forEach(function (memValue) {
    if (memValue.length < 5) {
      console.debug('[Memory] 记忆长度不足5, 跳过:', memValue);
      return;
    }

    if (_isMemoryDuplicate(memValue, existing)) {
      console.debug('[Memory] 重复记忆, 跳过:', memValue.substring(0, 50));
      return;
    }

    console.debug('[Memory] 准备保存记忆:', memValue.substring(0, 50));
    apiFetch('/api/memory/' + empId, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'auto_extract',
        value: memValue.substring(0, 500),
        source: '自动提取'
      })
    }).then(function (r) {
      console.debug('[Memory] ✅ 保存记忆成功:', memValue.substring(0, 50), '状态:', r ? r.status : 'null');
      _markMemorySaved(memValue);
      return r.json();
    }).then(function (saved) {
      console.debug('[Memory] 后端返回记忆ID:', saved && saved.id, '员工:', empId);
    }).catch(function (e) {
      if (e && e.message && e.message.indexOf('409') > -1) {
        console.debug('[Memory] 409 冲突，记忆已存在，静默跳过:', memValue.substring(0, 50));
        return;
      }
      console.warn('[Memory] ❌ 保存记忆失败:', e);
    });
    savedCount++;
  });

  if (savedCount > 0) {
    updateMemoryTabTitle(empId);
    console.debug('[Memory] 共准备保存 ' + savedCount + ' 条新记忆');
  } else {
    // 提取结果未解析出有效记忆时，兜底保存用户原话，确保每次对话至少一条记录
    console.debug('[Memory] 未解析出有效记忆，使用原话兜底');
    _saveMemoryFallback(empId, userMsg, existing);
  }
}

// ========== 项目组公共记忆 ==========
function _processExtractedGroupMemory(groupId, extracted, existing) {
  if (!extracted || extracted === '无' || extracted.length < 3) return;
  var lines = extracted.split('\n').map(function (l) { return l.trim(); });
  var newMemories = [];
  lines.forEach(function (line) {
    if (!line || line === '无') return;
    var memValue = '';
    if (line.indexOf('- ') === 0 && line.length > 3) memValue = line.substring(2).trim();
    else if (/^\d+\.\s+/.test(line) && line.length > 4) memValue = line.replace(/^\d+\.\s+/, '').trim();
    else if (line.indexOf('* ') === 0 && line.length > 3) memValue = line.substring(2).trim();
    if (memValue && memValue !== '无' && memValue.length > 3) newMemories.push(memValue);
  });
  if (newMemories.length === 0) {
    var cleanText = extracted.replace(/【记忆提取任务】/g, '').replace(/【已有记忆[^】]*】/g, '').replace(/【本次[^】]*】/g, '').replace(/要求：[^\n]*/g, '').replace(/^\d+[\.、]\s*/gm, '').trim();
    if (cleanText && cleanText !== '无' && cleanText.length > 5 && cleanText.indexOf('要求：') !== 0) {
      newMemories.push(cleanText);
    }
  }
  // 本次解析内部去重
  var uniqueNewMemories = [];
  var seenNormalized = {};
  newMemories.forEach(function (memValue) {
    var norm = _normalizeMemoryValue(memValue);
    if (norm && !seenNormalized[norm]) {
      seenNormalized[norm] = true;
      uniqueNewMemories.push(memValue);
    }
  });
  newMemories = uniqueNewMemories;
  // 单条消息提取的群记忆数量超过50条直接截断，只保留前50条
  if (newMemories.length > 50) {
    console.warn('[GroupMemory] 单条消息提取记忆超过50条，截断保留前50条，原始数量=' + newMemories.length);
    newMemories = newMemories.slice(0, 50);
  }
  newMemories.forEach(function (memValue) {
    if (memValue.length < 5) return;
    if (_isMemoryDuplicate(memValue, existing)) return;
    apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/memory', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        key: 'auto_extract',
        value: memValue.substring(0, 500),
        source: '群聊自动提取'
      })
    }).then(function () {
      _markMemorySaved(memValue);
    }).catch(function (e) {
      if (e && e.message && e.message.indexOf('409') > -1) {
        console.debug('[GroupMemory] 409 冲突，记忆已存在，静默跳过:', memValue.substring(0, 50));
        return;
      }
      console.warn('[GroupMemory] 保存项目组记忆失败:', e);
    });
  });
}

function extractAndSaveGroupMemory(groupId, userMsg, aiReply, msgId) {
  _enqueueMemoryExtract(function (done) {
    _runExtractAndSaveGroupMemory(groupId, userMsg, aiReply, msgId, done);
  });
}
function _runExtractAndSaveGroupMemory(groupId, userMsg, aiReply, msgId, done) {
  if (!groupId) {
    done();
    return;
  }
  var extractKey = (groupId || '') + '_' + (msgId || ('group_' + Date.now()));
  if (_isMemoryExtracted(extractKey)) {
    console.debug('[Memory] extractAndSaveGroupMemory 跳过，msgId 已提取过:', extractKey);
    done();
    return;
  }
  _markMemoryExtracted(extractKey);
  apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/memory').then(function (r) { return r.json(); }).then(function (data) {
    if (!data) data = {};
    var allMemories = data.memories || [];
    var existingDaily = allMemories.filter(function (m) { return m.key === 'auto_extract' || m.key === 'auto'; });
    var existingMemories = existingDaily.map(function (m) { return '- ' + (m.value || ''); }).join('\n');
    var extractPrompt = '【记忆提取任务】\n从以下群聊对话中提取需要长期记住的项目组相关信息。要求：\n1. 提取"事实"而非"关键词"\n2. 包含上下文关系，例如"团队决定下周发布新版本"\n3. 值得记住的信息包括：项目决策、团队分工、重要安排、共识结论等\n4. 每条记忆独立成一行，格式：- 关键信息\n5. 如果信息已经在"已有记忆"中，不要重复提取\n6. 只有当确实没有任何值得记住的新信息时，才回复"无"\n7. 不要寒暄，直接输出结果';
    if (existingMemories) extractPrompt += '\n\n【已有记忆（不要重复提取）】\n' + existingMemories;
    extractPrompt += '\n\n【本次群聊对话】\n';
    if (userMsg) extractPrompt += '群里有人说：' + userMsg.substring(0, 800) + '\n';
    if (aiReply) extractPrompt += '你回复：' + aiReply.substring(0, 800) + '\n';
    var group = groups.find(function (g) { return g.id === groupId; });
    var leadEmp = group ? emps.find(function (e) { return e.id === group.leadAgentId; }) : null;
    if (typeof openclaw !== 'undefined' && openclaw.connected) {
      var groupMemSessionKey = 'agent:' + (leadEmp ? leadEmp.id : (group ? group.leadAgentId : '')) + ':memory_extract';
      var isolatedPrompt = '【以下是一个完全独立的记忆提取任务，请忽略之前的所有对话上下文，仅根据本次提供的内容执行提取】\n\n' + extractPrompt;
      _sendChatWaitForLifecycle(groupMemSessionKey, isolatedPrompt).then(function (chatResult) {
        var extractReply = chatResult.reply || '';
        if (extractReply) _processExtractedGroupMemory(groupId, extractReply, allMemories);
        done();
      }).catch(function (e) {
        console.warn('[GroupMemory] OpenClaw 提取失败:', e);
        done();
      });
    } else if (leadEmp && leadEmp.apiKey && leadEmp.aiProvider) {
      apiFetch('/api/chat/' + encodeURIComponent(leadEmp.id), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: extractPrompt, skipAI: false })
      }).then(function (r) { return r.json(); }).then(function (data) {
        var extracted = (data.aiMessage && data.aiMessage.content) || data.reply || '';
        _processExtractedGroupMemory(groupId, extracted, allMemories);
      }).catch(function (e) { console.warn('[GroupMemory] API 提取失败:', e); }).finally(function () {
        done();
      });
    } else {
      done();
    }
  }).catch(function (e) {
    console.warn('[GroupMemory] 加载项目组记忆失败:', e);
    done();
  });
}

async function loadGroupMemoryForPrompt(groupId) {
  if (!groupId) return '';
  try {
    var r = await apiFetch('/api/groups/' + encodeURIComponent(groupId) + '/memory?type=active');
    var data = await r.json();
    if (!data || !data.memories || data.memories.length === 0) return '';
    var coreLines = [];
    var dailyLines = [];
    data.memories.forEach(function (m) {
      var val = (m.value || '').substring(0, 300);
      if (!val) return;
      if (m.pool === 'core') coreLines.push('- ' + val);
      else if (m.pool === 'daily') dailyLines.push('- ' + val);
    });
    var parts = [];
    if (coreLines.length) parts.push('【项目组核心记忆】\n' + coreLines.join('\n'));
    if (dailyLines.length) parts.push('【项目组日常记录】\n' + dailyLines.join('\n'));
    return parts.length ? '\n\n' + parts.join('\n\n') : '';
  } catch (e) {
    console.warn('[GroupMemory] 加载项目组记忆上下文失败:', e);
    return '';
  }
}

// 显示 AI 回复
// ========== RAG 检索（Embedding API 向量检索）==========
async function retrieveRAGContext(empId, query) {
  if (!query || query.length < 2) return { context: '', citations: [] };
  var emp = emps.find(function(e) { return e.id === empId; });
  if (!emp || !emp.apiKey) return { context: '', citations: [] };
  try {
    var r = await apiFetch('/api/rag/retrieve', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: query, empId: empId, topK: 3 })
    });
    var data = await r.json();
    var citations = [];
    if (data.docs) {
      data.docs.forEach(function(d) {
        citations.push({ id: d.id, title: d.title || d.name || '未命名', type: 'doc' });
      });
    }
    if (data.products) {
      data.products.forEach(function(p) {
        citations.push({ id: p.id, title: p.name || '未命名', type: 'product' });
      });
    }
    return { context: data.context || '', citations: citations };
  } catch (e) {
    console.warn('[RAG] 检索失败:', e);
    return { context: '', citations: [] };
  }
}

// ========== Agent 工具调用（OpenClaw 原生工具 + exec curl 本地 API）==========
function toggleToolCard(toolCallId) {
  var card = document.getElementById('tool-' + toolCallId);
  if (!card) return;
  var expanded = card.getAttribute('data-expanded') === 'true';
  expanded = !expanded;
  card.setAttribute('data-expanded', expanded);
  var cmd = card.querySelector('.tool-cmd');
  var output = card.querySelector('.tool-output');
  var toggle = card.querySelector('.tool-toggle');
  if (cmd) cmd.style.display = expanded ? 'block' : 'none';
  if (output) output.style.display = expanded ? 'block' : 'none';
  if (toggle) toggle.textContent = expanded ? '收起▲' : '展开▼';
  var notify = card.querySelector('.tool-notify');
  if (notify) notify.style.display = 'none';
  var area = document.getElementById('messagesArea');
  if (area) area.scrollTop = area.scrollHeight;
}

function buildAgentToolsContext(empId) {
  var port = window.location.port || '8080';
  var baseUrl = 'http://localhost:' + port;
  // X-Agent-Id 在工具层硬编码：服务端按它追溯数据归属（达人录入缺 header 会被拒绝），不依赖 LLM 自觉
  var agentId = empId || '<你的员工ID>';
  return '\n\n【系统资源（通过本地 API 访问）】\n' +
    '你作为AI员工，可以使用 OpenClaw 原生工具完成任务。\n\n' +
    '当需要查询商品、达人、订单等业务数据时，使用 exec 工具执行 curl 命令调用本地 API，例如：\n' +
    "exec(command='curl -s -H \"X-Agent-Id: " + agentId + "\" \"" + baseUrl + "/api/products?q=关键词\"')\n" +
    '当需要修改业务数据时，同样通过 curl POST/PUT/DELETE 调用对应 API 接口，例如达人录入：\n' +
    "exec(command=\"curl -s -X POST -H 'Content-Type: application/json' -H 'X-Agent-Id: " + agentId + "' -d '{\\\"name\\\":\\\"达人昵称\\\",\\\"douyin_id\\\":\\\"抖音号\\\"}' " + baseUrl + "/api/talents\")\n" +
    '【必须】所有本地 API 请求都必须携带 X-Agent-Id: ' + agentId + ' 请求头，否则服务端无法确定数据归属，写入会被拒绝。\n' +
    '【注意】禁止编造不存在的商品或达人信息，所有数据以本地 API 返回结果为准。\n' +
    '【禁止】不要使用 [TOOL_CALL] 格式，所有操作必须通过 exec 工具执行 curl 命令完成。';
}

function toggleCitationPanel(panelId) {
  var panel = document.getElementById(panelId);
  if (!panel) return;
  panel.classList.toggle('hidden');
  var bar = panel.previousElementSibling;
  if (bar) bar.classList.toggle('open');
}

function showCitationDetail(id, title, type) {
  showToast((type === 'product' ? '📦 产品' : '📄 知识') + '\nID: ' + id + '\n标题: ' + title, 'info');
}


// ========== 知识库文档管理 ==========
function openKnowledgeDocForm(doc) {
  var isEdit = !!doc;
  document.getElementById('knowledgeDocModalTitle').textContent = isEdit ? '编辑文档' : '新建文档';
  document.getElementById('knowledgeDocEditId').value = isEdit ? doc.id : '';
  document.getElementById('knowledgeDocName').value = isEdit ? (doc.name || '') : '';
  document.getElementById('knowledgeDocIcon').value = isEdit ? (doc.icon || '📄') : '📄';
  document.getElementById('knowledgeDocContent').value = isEdit ? (doc.content || '') : '';
  document.getElementById('knowledgeDocModalOverlay').classList.add('open');
}
function closeKnowledgeDocForm() {
  document.getElementById('knowledgeDocModalOverlay').classList.remove('open');
}
async function saveKnowledgeDoc() {
  try {
    var docId = document.getElementById('knowledgeDocEditId').value;
    var name = document.getElementById('knowledgeDocName').value.trim();
    var content = document.getElementById('knowledgeDocContent').value.trim();
    if (!name || !content) {
      showToast('请填写文档名称和内容');
      return;
    }
    var body = {
      name: name,
      icon: document.getElementById('knowledgeDocIcon').value.trim() || '📄',
      content: content
    };
    if (docId) {
      await apiFetch('/api/knowledge/' + encodeURIComponent(docId), { method: 'PUT', body: JSON.stringify(body) });
      showToast('文档已更新');
    } else {
      await apiFetch('/api/knowledge', { method: 'POST', body: JSON.stringify(body) });
      showToast('文档已创建');
    }
    closeKnowledgeDocForm();
    var currentEmpId = localStorage.getItem('sb_current_emp');
    if (currentEmpId) renderMemoryTab(currentEmpId);
  } catch (e) {
    showToast('保存失败: ' + (e.message || e));
  }
}
async function deleteKnowledgeDoc(docId) {
  if (!docId) return;
  if (!confirm('确定删除该文档？此操作不可恢复。')) return;
  try {
    await apiFetch('/api/knowledge/' + encodeURIComponent(docId), { method: 'DELETE' });
    showToast('文档已删除');
    var currentEmpId = localStorage.getItem('sb_current_emp');
    if (currentEmpId) renderMemoryTab(currentEmpId);
  } catch (e) {
    showToast('删除失败: ' + (e.message || e));
  }
}

// ========== 三期：知识库文档版本历史 ==========
var currentKnowledgeVersionDocId = null;
function openKnowledgeVersionModal(docId, docName) {
  currentKnowledgeVersionDocId = docId;
  var overlay = document.getElementById('knowledgeVersionModalOverlay');
  var title = document.getElementById('knowledgeVersionModalTitle');
  if (title) title.textContent = escapeHtml(docName || '文档') + ' 版本历史';
  if (overlay) overlay.classList.add('open');
  loadKnowledgeVersions(docId);
}
function closeKnowledgeVersionModal() {
  currentKnowledgeVersionDocId = null;
  var overlay = document.getElementById('knowledgeVersionModalOverlay');
  if (overlay) overlay.classList.remove('open');
}
function loadKnowledgeVersions(docId) {
  var body = document.getElementById('knowledgeVersionModalBody');
  if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/versions').then(function (r) { return r.json(); }).then(function (data) {
    var versions = data.versions || [];
    if (versions.length === 0) {
      if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">暂无版本记录</div>';
      return;
    }
    var html = '';
    versions.forEach(function (v) {
      var content = v.content || '';
      var preview = content.length > 80 ? content.slice(0, 80) + '...' : content;
      var timeStr = v.createdAt ? formatRelativeTime(v.createdAt) : '-';
      html += '<div style="padding:12px;background:var(--bg-secondary);border-radius:12px;margin-bottom:10px;">';
      html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
      html += '<span style="font-size:14px;font-weight:600;color:var(--text-primary);">版本 ' + escapeHtml(String(v.version || '-')) + '</span>';
      html += '<span style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(timeStr) + '</span>';
      html += '</div>';
      html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">操作者: ' + escapeHtml(v.createdBy || '-') + '</div>';
      html += '<div style="font-size:13px;line-height:1.5;color:var(--text-primary);white-space:pre-wrap;margin-bottom:10px;">' + escapeHtml(preview) + '</div>';
      html += '<div style="display:flex;gap:8px;justify-content:flex-end;">';
      html += '<button class="memory-add-btn" style="background:var(--bg-tertiary);color:var(--text-secondary);" onclick="viewKnowledgeVersion(\'' + escapeAttr(docId) + '\', ' + Number(v.version) + ')">查看</button>';
      html += '<button class="memory-add-btn" onclick="rollbackKnowledgeVersion(\'' + escapeAttr(docId) + '\', ' + Number(v.version) + ')">回滚</button>';
      html += '</div>';
      html += '</div>';
    });
    if (body) body.innerHTML = html;
  }).catch(function (e) {
    if (body) body.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-tertiary);">加载失败</div>';
    console.warn('[KnowledgeVersion] 加载失败:', e);
  });
}
function viewKnowledgeVersion(docId, version) {
  apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/versions/' + encodeURIComponent(version)).then(function (r) { return r.json(); }).then(function (data) {
    var v = data.version || data;
    var content = v.content || '';
    var timeStr = v.createdAt ? formatRelativeTime(v.createdAt) : '-';
    var html = '<div style="padding:16px;background:var(--bg-secondary);border-radius:12px;">';
    html += '<div style="font-size:14px;font-weight:600;color:var(--text-primary);margin-bottom:8px;">版本 ' + escapeHtml(String(v.version || '-')) + ' · ' + escapeHtml(timeStr) + '</div>';
    html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">操作者: ' + escapeHtml(v.createdBy || '-') + '</div>';
    html += '<div style="font-size:13px;line-height:1.6;color:var(--text-primary);white-space:pre-wrap;">' + escapeHtml(content) + '</div>';
    html += '</div>';
    var modal = document.createElement('div');
    modal.className = 'product-modal-overlay open';
    modal.style.zIndex = '10001';
    modal.innerHTML = '<div class="product-modal-panel" style="max-width:560px;max-height:80vh;" onclick="event.stopPropagation()"><div class="product-modal-header"><div class="product-modal-title">版本详情</div><button class="product-modal-close" onclick="this.closest(\'.product-modal-overlay\').remove()">✕</button></div><div class="product-modal-body" style="overflow-y:auto;">' + html + '</div><div class="product-modal-footer"><button class="product-modal-btn cancel" onclick="this.closest(\'.product-modal-overlay\').remove()">关闭</button></div></div>';
    modal.onclick = function () { modal.remove(); };
    document.body.appendChild(modal);
  }).catch(function (e) {
    showToast('加载版本详情失败');
    console.warn('[KnowledgeVersion] 查看失败:', e);
  });
}
function rollbackKnowledgeVersion(docId, version) {
  if (!confirm('确定回滚到版本 ' + version + '？当前内容将被覆盖。')) return;
  apiFetch('/api/knowledge/' + encodeURIComponent(docId) + '/rollback', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({version: parseInt(version, 10)})
  }).then(function (r) { return r.json(); }).then(function () {
    showToast('✅ 已回滚到版本 ' + version);
    var currentEmpId = localStorage.getItem('sb_current_emp');
    if (currentEmpId) renderMemoryTab(currentEmpId);
    if (currentKnowledgeVersionDocId === docId) loadKnowledgeVersions(docId);
  }).catch(function (e) {
    showToast('❌ 回滚失败: ' + (e.message || e));
    console.warn('[KnowledgeVersion] 回滚失败:', e);
  });
}

// ★ fix/talent-full-sync-r3: dedup 路径前端自动 PUT
// Helen system_prompt 注入 dedup_hint (server _build_talent_dedup_hint L21053) 时, LLM 回复会含:
//   "已为达人 `tal_xxx` (姓名: XXX) 更新档案, ..." + 字段值 (粉丝量/GMV/GPM/...)
// 前端解析后直接 PUT /api/talents/{id}, 不依赖用户手动点 modal 确认.
// 后端 _merge_talent_only_empty 只补空值严禁覆盖 (r2 修复), 自动 PUT 安全.
//
// LLM 回复格式约定 (宽松 regex, 容错):
//   "达人ID: `tal_xxx`" → talent_id
//   "粉丝量: 5486" / "粉丝 5486" / "5486 粉丝" → followers
//   "总GMV: 50万" / "结算总额: 50万-100万" → total_gmv
//   "视频GPM: 50-100" / "GPM: 50" → video_gpm
//   "带货商品数: 63" / "商品数: 63" → product_count
//   "合作店铺数: 41" / "关联店铺数: 41" → total_shops
//   "单视频结算额: 1000-2500" → single_video_settlement
//   "互动率: 0.32%" → video_interaction_rate
//   "直播场次: 0" / "直播 0 场" → live_sessions
//   "带货天数: 372" / "372 天" → cooperation_days
//   "内容类型: 带货号" → talent_type
//   "内容风格: 好物分享" → content_style
//   "账号粉丝特征: 25-35岁女性" → account_fans_profile
//   "短视频粉丝特征: 18-24岁" → video_fans_profile
//   "抖音号: abc" → douyin_id
//   "等级: B级" / "LV3" → level
//   "所在地: 上海" / "城市: 上海" → city
//   "备注: ..." → bio
//
// 不依赖后端改 dedup_hint (避免耦合 bug/talent-deduplicate 1017212)
// 用户手动 modal 流程 (updateExistingTalent) 保留, 两条路径并存

function _parseFollowerCountLLM(v) {
    // 复用 solobrave-server.py _parse_follower_count 逻辑 (LLM 回复可能用 '1.2万' / '5,486')
    var s = String(v || '').toLowerCase().trim().replace(/[,\s]/g, '');
    if (!s) return 0;
    try {
        if (s.indexOf('万') >= 0 || s.indexOf('w') >= 0) {
            return Math.floor(parseFloat(s.replace(/[万千wW]/g, '')) * 10000);
        }
        return parseInt(s) || 0;
    } catch (e) { return 0; }
}

function _parseMarkdownTableLLM(text) {
    // ★ merge fix/talent-full-sync-r7+r8 (Mini cherry-pick): Markdown 表格 / JSON 块 → JSON 字符串
    // LLM 输出格式多样 (Markdown 表格 / 单行逗号分隔 / JSON 块), 容错处理
    if (!text || typeof text !== 'string') return null;
    var trimmed = text.trim();
    if (!trimmed) return null;
    // 1. 已是 JSON 格式 (LLM 输出 ```json ... ```) → 直接验证
    if (trimmed.charAt(0) === '[' || trimmed.charAt(0) === '{') {
        try { return JSON.stringify(JSON.parse(trimmed), null, 0); } catch (e) { /* fallback */ }
    }
    // 2. Markdown 表格 (含 | 分隔符) → 解析
    var lines = trimmed.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
    var tableLines = lines.filter(function (l) { return l.indexOf('|') >= 0 && !l.match(/^[\|\s\-:]+$/); });
    if (tableLines.length < 2) {
        // 3. 单行: 逗号 / 中文逗号分隔
        if (/[,，]/.test(trimmed)) {
            var items = trimmed.split(/[,，]/).map(function (s) { return s.trim(); }).filter(Boolean);
            return JSON.stringify(items);
        }
        return null;
    }
    var headers = tableLines[0].split('|').map(function (s) { return s.trim(); }).filter(Boolean);
    if (headers.length === 0) return null;
    var rows = [];
    for (var i = 1; i < tableLines.length; i++) {
        var cells = tableLines[i].split('|').map(function (s) { return s.trim(); }).filter(Boolean);
        var item = {};
        for (var j = 0; j < headers.length; j++) {
            item[headers[j]] = cells[j] || '';
        }
        rows.push(item);
    }
    return JSON.stringify(rows, null, 0);
}

function _extractTalentFieldsFromLLMReply(reply) {
    if (!reply || typeof reply !== 'string') return null;
    var fields = {};
    var t;

    // ───── 达人 ID (触发前提) ─────
    // LLM 实际回复格式:
    //   A) "已为达人 `tal_xxx` (姓名: XXX) 更新档案..." (反引号 + 姓名: 配对)
    //   B) "达人ID: `tal_xxx` (姓名: XXX) 更新档案..." (system_prompt 引导)
    //   C) "已更新档案, 达人 ID: tal_xxx" (fallback)
    // 优先匹配 A 格式 (反引号 + 姓名: 紧跟), 再 fallback 到 达人ID
    t = reply.match(/[`'"]?(tal_[a-zA-Z0-9_]+)[`'"]?\s*\(姓名:/);
    if (!t) {
        t = reply.match(/达人ID[：:\s*]*[`'"]?(tal_[a-zA-Z0-9_]+|[a-zA-Z0-9_]{8,})[`'"]?/);
    }
    if (!t) return null;  // 没达人 ID → 不是 dedup 触发场景, 跳过
    fields._talent_id = t[1];
    var talentId = fields._talent_id;

    // ───── (触发判定已移至 _tryAutoPutTalentFromReply, r4 双条件结构匹配) ─────
    // 原本这里有 c6ce501 的关键词 regex `/(已为.*达人.*更新|已更新.*档案|直接调\s*PUT|PUT\s*\/api\/talents)/`
    // r4 删了, 改用更鲁棒的双条件: 达人 ID 格式 + dedup context 关键词 (AND)
    // (见 _tryAutoPutTalentFromReply L26155 起的双条件判定)

    // ───── 核心数据 ─────
    t = reply.match(/粉丝[量]?[：:\s*]*([0-9,\.万千]+)/);
    if (t) fields.followers = _parseFollowerCountLLM(t[1]);

    t = reply.match(/(结算总额|总GMV|GMV总额)[：:\s*]*[¥￥]?([0-9,\-万千]+)/);
    if (t) fields.total_gmv = t[2];

    t = reply.match(/(带货商品数|商品数)[：:\s*]*([0-9]+)/);
    if (t) fields.product_count = parseInt(t[2]) || 0;

    t = reply.match(/(合作店铺数|关联店铺数)[：:\s*]*([0-9]+)/);
    if (t) fields.total_shops = parseInt(t[2]) || 0;

    t = reply.match(/(?:视频\s*GPM|GPM)[：:\s*]*([0-9,\-]+)/);
    if (t) fields.video_gpm = t[1];

    t = reply.match(/(?:单视频结算额|单视频结算)[：:\s*]*[¥￥]?([0-9,\-]+)/);
    if (t) fields.single_video_settlement = t[1];

    t = reply.match(/互动率[：:\s*]*([0-9\.]+)\s*%/);
    if (t) fields.video_interaction_rate = (parseFloat(t[1]) || 0) / 100;

    t = reply.match(/(?<!\S)直播(?:场次|场数|场|次|数)?[：:\s*]*([0-9]+)/);
    if (t) fields.live_sessions = parseInt(t[1]) || 0;

    t = reply.match(/带货天数[：:\s*]*([0-9]+)/);
    if (t) fields.cooperation_days = parseInt(t[1]) || 0;


    // ───── 基础信息 ─────
    t = reply.match(/内容类型[：:\s*]*([^\n,。；]+)/);
    if (t) fields.talent_type = t[1].trim();

    t = reply.match(/内容风格[：:\s*]*([^\n,。；]+)/);
    if (t) fields.content_style = t[1].trim();

    t = reply.match(/(?:账号粉丝特征|粉丝特征|粉丝画像|账号粉丝画像)[：:\s*]*([^\n,。；]+)/);
    if (t) fields.account_fans_profile = t[1].trim();

    t = reply.match(/(?:短视频粉丝特征|视频粉丝特征|短视频粉丝画像|视频粉丝画像)[：:\s*]*([^\n,。；]+)/);
    if (t) fields.video_fans_profile = t[1].trim();

    t = reply.match(/抖音号[：:\s*]*[`'"]?([a-zA-Z0-9_\-\.]+)[`'"]?/);
    if (t) fields.douyin_id = t[1];

    t = reply.match(/(?:等级|评级)[：:\s*]*(LV\d+|L\d+|[A-D]\s*级|[A-D][级]?)/);
    if (t) fields.level = t[1].replace(/\s/g, '');

    t = reply.match(/(?:所在地|所在城市|城市)[：:\s*]*([^\n,。；]+)/);
    if (t) fields.city = t[1].trim();

    // ───── r8: 补全 LLM 实际输出 → DB 列名映射 ─────
    // LLM 真实回复常见字段 (chat 149): "- **达人昵称**：发财周周" / "- **平台**：抖音"
    //                              "- **内容标签**：时尚" / "- **合作状态**：available"
    // 之前 r5 只补了核心数据 + 部分基础信息, 漏了这 4 个常用字段
    // 用 (?:^|\n)\s*[-*]? 行首锚定, 避免误匹配文中"达人昵称"等关键词
    t = reply.match(/(?:^|\n)\s*[-*]?\s*(?:\*\*)?达人昵称(?:\*\*)?[：:\s*]*([^\n,。；*]+?)\s*(?:\*\*)?\s*$/m);
    if (t) fields.name = t[1].trim();

    t = reply.match(/(?:^|\n)\s*[-*]?\s*(?:\*\*)?平台(?:\*\*)?[：:\s*]*([^\n,。；*]+?)\s*(?:\*\*)?\s*$/m);
    if (t) fields.platform = t[1].trim();

    // 内容标签 (LLM 实际输出名) → content_style (独立匹配, 不跟"内容类型"/"内容风格" OR, 避免顺序冲突)
    t = reply.match(/(?:^|\n)\s*[-*]?\s*(?:\*\*)?内容标签(?:\*\*)?[：:\s*]*([^\n,。；*]+?)\s*(?:\*\*)?\s*$/m);
    if (t) fields.content_style = t[1].trim();

    t = reply.match(/(?:^|\n)\s*[-*]?\s*(?:\*\*)?合作状态(?:\*\*)?[：:\s*]*([^\n,。；*]+?)\s*(?:\*\*)?\s*$/m);
    if (t) fields.cooperation_status = t[1].trim();

    t = reply.match(/(?:备注|简介|个人简介|bio)[：:\s*]*([^\n]+)/);
    if (t) fields.bio = t[1].trim();

    // r9 新增: 带货方式 → talent_type (DB 没"带货方式"列, 映射到 talent_type 最合适)
    t = reply.match(/(?:^|\n)\s*[-*]?\s*(?:\*\*)?带货方式(?:\*\*)?[：:\s*]*([^\n,。；*]+?)\s*(?:\*\*)?\s*$/m);
    if (t && !fields.talent_type) fields.talent_type = t[1].trim();

    // ★ merge fix/talent-full-sync-r8 (Mini cherry-pick): 4 JSON 字段 + JSON 子字段兜底
    //   hot_brands / cooperating_brands / brand_details / products 来自 LLM Markdown 表格
    //   兜底: LLM 漏列时强制补 commission="未提供" / live_session_count=0
    t = reply.match(/(?:热卖品牌TOP3|热卖品牌|hot_brands|品牌TOP3)[：:\s*]*\n?([\s\S]*?)(?=\n\n|\n#|$)/);
    if (t) {
        var parsed = _parseMarkdownTableLLM(t[1]);
        if (parsed) {
            // r8: 兜底 commission 子字段
            try {
                var arr = JSON.parse(parsed);
                if (Array.isArray(arr)) {
                    arr.forEach(function (item) {
                        if (!('commission' in item) && !('佣金' in item)) {
                            item.commission = item.commission || item['佣金'] || '未提供';
                        }
                    });
                    fields.hot_brands = JSON.stringify(arr);
                } else {
                    fields.hot_brands = parsed;
                }
            } catch (e) { fields.hot_brands = parsed; }
        } else {
            fields.hot_brands = t[1].trim().split('\n')[0];
        }
    }

    t = reply.match(/(?:合作品牌[列表]|cooperating_brands)[：:\s*]*\n?([\s\S]*?)(?=\n\n|\n#|$)/);
    if (t) {
        var parsed = _parseMarkdownTableLLM(t[1]);
        if (parsed) fields.cooperating_brands = parsed;
    }

    t = reply.match(/(?:品牌详情|brand_details)[：:\s*]*\n?([\s\S]*?)(?=\n\n|\n#|$)/);
    if (t) {
        var parsed = _parseMarkdownTableLLM(t[1]);
        if (parsed) fields.brand_details = parsed;
    }

    t = reply.match(/(?:带货商品明细|带货商品列表|带货商品|products|(?<!\| )代表商品)[：:\s*]*\n?([\s\S]*?)(?=\n\n|\n#|$)/);
    if (t) {
        var parsed = _parseMarkdownTableLLM(t[1]);
        if (parsed) {
            // r8: 兜底 live_session_count 子字段
            try {
                var arr = JSON.parse(parsed);
                if (Array.isArray(arr)) {
                    arr.forEach(function (item) {
                        if (!('live_session_count' in item) && !('关联直播场次' in item)) {
                            item.live_session_count = parseInt(item.live_session_count || item['关联直播场次'] || 0);
                        }
                    });
                    fields.products = JSON.stringify(arr);
                } else {
                    fields.products = parsed;
                }
            } catch (e) { fields.products = parsed; }
        }
    }

    // 去掉内部字段 (talent_id 已经单独提取)
    delete fields._talent_id;

    return {
        talent_id: talentId,
        body: fields
    };
}

function _tryAutoPutTalentFromReply(reply) {
    // ★ fix/talent-full-sync-r4: 双条件结构匹配 (取代 c6ce501 的具体 pattern regex)
    // 1. 达人 ID 存在: tal_xxx 格式 (单词边界避免匹配 "talents" 等普通词)
    // 2. dedup context 关键词 (中文, 涵盖老大 brief 的 5 个 + 扩展 2 个)
    //    老大 brief 列表: 更新 / 录入 / 建档 / 同步 / 写入
    //    扩展 (dedup 场景同义词): 档案 / 覆盖
    // 两个条件 AND 满足 → 触发自动 PUT
    // 单条件满足 → 普通对话 / 提及达人昵称但无 id → 不触发
    if (!reply || typeof reply !== 'string') return;
    var talentIdMatch = reply.match(/\b(tal_[a-zA-Z0-9_]+)\b/);
    if (!talentIdMatch) return;  // ★ 没达人 ID → 普通对话 (含昵称但无 id) 不触发
    if (!/(更新|录入|建档|同步|写入|档案|覆盖)/.test(reply)) return;  // ★ 没 dedup context → 不触发

    // 双条件满足 → 解析字段 + 自动 PUT
    var extracted = _extractTalentFieldsFromLLMReply(reply);
    if (!extracted || !extracted.talent_id) return;  // 解析失败 (理论上不该到这里, 双条件已确保有 id)
    var talentId = extracted.talent_id;
    var body = extracted.body;
    var fieldCount = Object.keys(body).length;
    if (fieldCount === 0) {
        console.debug('[AutoPUT] 没提取到任何字段, 跳过 (talent_id=' + talentId + ')');
        return;
    }
    // 异步后台 PUT, 不阻塞 render
    fetch('/api/talents/' + encodeURIComponent(talentId), {
        method: 'PUT',
        credentials: 'include',  // 带 cookie (防御: 部分路径靠 session cookie 而非 header)
        headers: {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + (localStorage.getItem('sb_auth_token') || '')  // ★ r7: 带 Bearer token, 修 401
        },
        // ★ r10: dedup_auto=true 标记让后端 bypass SubpoolGuard (Helen agent 是系统 AI 代写, 不是用户跨库操作)
        body: JSON.stringify(Object.assign({dedup_auto: true}, body))
    }).then(function (r) {
        if (r.ok) {
            console.log('[AutoPUT] 达人 ' + talentId + ' 自动同步 ' + fieldCount + ' 字段:', Object.keys(body).join(','));
            // 不弹 toast (避免 Helen 还在回复时打扰用户), 静默成功
        } else {
            console.warn('[AutoPUT] PUT 失败 status=' + r.status + ' talent_id=' + talentId);
        }
    }).catch(function (e) {
        console.error('[AutoPUT] 异常 talent_id=' + talentId + ':', e);
    });
}

// 〔r81 批注① 2026-10-08〕MS3 召回 chip 条构建（记忆/知识/规律 三色 pill，复用 sb2 全局 CSS）
// 数据驱动：injectionTags = {memory:{core,daily,archive}, knowledge, pattern}
// 无数据/全 0 返回 ''（只丢不造，不渲染零 chip）
function buildInjectionChipsHtml(injectionTags) {
  if (!injectionTags || typeof injectionTags !== 'object') return '';
  var _mem = injectionTags.memory || {};
  var _memTotal = (_mem.core || 0) + (_mem.daily || 0) + (_mem.archive || 0);
  var _chips = '';
  if (_memTotal > 0) _chips += '<span class="sb2-chat-injection-chip accent">🧠 记忆 ' + _memTotal + ' 条</span>';
  if ((injectionTags.knowledge || 0) > 0) _chips += '<span class="sb2-chat-injection-chip brand">📄 知识 ' + injectionTags.knowledge + ' 条</span>';
  if ((injectionTags.pattern || 0) > 0) _chips += '<span class="sb2-chat-injection-chip gold">🧩 规律 ' + injectionTags.pattern + ' 条</span>';
  if (!_chips) return '';
  return '<div class="sb2-chat-injection-chips">' + _chips + '</div>';
}

function displayAIReply(replyText, docContext, timeStr, expectedEmpId, citations, chainDepth, injectionTags) {
  // ★ fix/talent-full-sync-r3: dedup 路径自动 PUT (放在函数最前, 异步后台执行不阻塞 render)
  if (replyText && typeof replyText === 'string') {
    _tryAutoPutTalentFromReply(replyText);
  }
  citations = citations || [];
  chainDepth = chainDepth || 0;
  var area = document.getElementById('messagesArea');
  // 防御性校验：如果指定了 expectedEmpId，必须匹配当前员工才渲染，防止切换后串渲染
  if (expectedEmpId && localStorage.getItem('sb_current_emp') !== expectedEmpId) {
    console.warn('[displayAIReply] 员工已切换，跳过 AI 回复 DOM 渲染:', expectedEmpId);
    return;
  }
  var hasDocs = docContext && docContext.length > 0;
  var emp = getCurrentEmployeeInfo();
  var avatarBg = emp.bg ? 'background:' + emp.bg + ';' : 'background:linear-gradient(135deg,#FF6B35,#E55A2B);';

  // Token 统计已由后端 /api/proxy 真实 usage 记录，前端不再估算
  var cleanText = replyText || '';

  var bubble = formatMessageContent(cleanText);
  if (hasDocs && cleanText && cleanText.indexOf('📚') === -1) {
    bubble += '<div class="ai-doc-hint">📚 已读取 ' + countDocsInContext(docContext) + ' 个相关文档</div>';
  }
  // 引用知识标签
  var citationHtml = '';
  if (citations.length > 0) {
    var citeId = 'citations_' + Date.now() + '_' + Math.random().toString(36).substr(2, 5);
    var citeItems = citations.map(function(c, idx) {
      var icon = c.type === 'product' ? '📦' : '📄';
      return '<span class="citation-tag" onclick="event.stopPropagation();showCitationDetail(\'' + c.id + '\', \'' + escapeHtml(c.title).replace(/\\/g, '\\\\').replace(/'/g, "\\'") + '\', \'' + c.type + '\')">' + icon + ' ' + escapeHtml(c.title) + '</span>';
    }).join('');
    citationHtml = '<div class="citation-bar" onclick="toggleCitationPanel(\'' + citeId + '\')">📚 引用了 ' + citations.length + ' 条知识 <span class="citation-toggle">▼</span></div>' +
      '<div class="citation-panel hidden" id="' + citeId + '">' + citeItems + '</div>';
  }
  var html = '<div class="msg">\n';
  html += '<div class="msg-avatar">' + renderAvatar(emp, 32) + '</div>\n';
  html += '<div class="msg-content">\n';
  html += '<div class="msg-sender"><span class="msg-sender-name">' + escapeHtml(emp.name) + '</span><span class="msg-sender-role">' + escapeHtml(getEmpRoleDisplay(emp)) + '</span><span class="msg-sender-time">' + timeStr + '</span></div>\n';
  // 〔r81 批注①〕MS3 召回 chip 条（气泡上方，原型设计还原；无数据不渲染）
  var _injChipsHtml = buildInjectionChipsHtml(injectionTags);
  if (_injChipsHtml) html += _injChipsHtml + '\n';
  html += '<div class="msg-bubble">' + bubble + '</div>\n';
  if (citationHtml) html += citationHtml;
  html += '</div></div>';
  area.insertAdjacentHTML('beforeend', html);
  // 记录刚插入的 AI 气泡节点，saveChatToServer 拿到后端保存返回的 id 后回填（供右键引用取数）
  window._lastAiMsgEl = area.lastElementChild;
  area.scrollTop = area.scrollHeight;

  window._lastAIReplyText = cleanText;
  if (window._lastUserMessageHadImages) {
    setTimeout(function() {
      if (document.getElementById('memoryImageToast')) return;
      var toast = document.createElement('div');
      toast.className = 'memory-image-toast';
      toast.id = 'memoryImageToast';
      toast.innerHTML = '📸 这张图片的内容要保存到记忆里吗？<button onclick="saveImageMemory()" style="padding:4px 10px;border-radius:6px;border:none;background:var(--accent);color:#fff;font-size:12px;cursor:pointer;">保存到记忆</button><button onclick="dismissImageMemory()" style="padding:4px 10px;border-radius:6px;border:none;background:var(--bg-tertiary);color:var(--text-secondary);font-size:12px;cursor:pointer;">忽略</button>';
      document.body.appendChild(toast);
      setTimeout(function() { dismissImageMemory(); }, 10000);
    }, 500);
  }

}

// Init
window.addEventListener('DOMContentLoaded', function () {
  initGlobalSearch();
  initTheme();
  initStatsAndNotifications();
  initNotifications();
  initNotificationPolling();
});

// ===== 新增员工向导 =====
var wizStep = 1;
var empSelectedModel = 'gpt4o';
var empSelectedProvider = 'openclaw';
var empSelectedAIProvider = 'kimi';

// ========== 向导技能选择 ==========
var wizHotSkills = [{
  slug: 'web-search',
  name: '网页搜索',
  emoji: '🔍',
  desc: '搜索互联网信息'
}, {
  slug: 'code-review',
  name: '代码审查',
  emoji: '🐛',
  desc: '代码质量检查'
}, {
  slug: 'doc-writer',
  name: '文档生成',
  emoji: '📝',
  desc: '自动生成文档'
}, {
  slug: 'data-viz',
  name: '数据可视化',
  emoji: '📊',
  desc: '生成图表'
}, {
  slug: 'pdf-handler',
  name: 'PDF处理',
  emoji: '📄',
  desc: 'PDF读写与提取'
}, {
  slug: 'image-gen',
  name: '图片生成',
  emoji: '🎨',
  desc: 'AI生成图片'
}];
var wizSelectedSkills = [];
function renderWizSkillGrid() {
  var grid = document.getElementById('wizSkillGrid');
  if (!grid) return;
  grid.innerHTML = wizHotSkills.map(function (s) {
    var selected = wizSelectedSkills.indexOf(s.slug) >= 0;
    return '<div class="wiz-skill-card' + (selected ? ' selected' : '') + '" ' + 'onclick="toggleWizSkill(\'' + escapeAttr(s.slug) + '\')" ' + 'style="display:flex;align-items:center;gap:8px;padding:10px 12px;' + 'border:1px solid ' + (selected ? 'var(--accent)' : 'var(--separator)') + ';' + 'border-radius:8px;cursor:pointer;transition:all 0.2s;' + 'background:' + (selected ? 'rgba(22, 119, 255,0.08)' : 'transparent') + ';">' + '<span style="font-size:18px;">' + escapeHtml(s.emoji) + '</span>' + '<div style="flex:1;min-width:0;">' + '<div style="font-size:13px;font-weight:500;">' + escapeHtml(s.name) + '</div>' + '<div style="font-size:11px;color:var(--text-tertiary);">' + escapeHtml(s.desc) + '</div>' + '</div>' + (selected ? '<span style="color:var(--accent);">✓</span>' : '') + '</div>';
  }).join('');
}
function toggleWizSkill(slug) {
  var idx = wizSelectedSkills.indexOf(slug);
  if (idx >= 0) {
    wizSelectedSkills.splice(idx, 1);
  } else {
    wizSelectedSkills.push(slug);
  }
  renderWizSkillGrid();
}
function openWizard() {
  // ★ fix/onboarding-permission: 创建 AI 员工是核心功能, 所有登录用户都可用
  // 删除员工 / 系统配置 / 邀请员工仍由后端 admin check 限制
  if (!currentUser) {
    showToast('请先登录', 'warning');
    return;
  }
  wizStep = 1;
  soulAnswers = {};
  currentSoulQ = 0;
  wizSelectedSkills = [];
  renderWizSkillGrid();
  renderAvatarPicker();
  loadWizardTeams();
  loadWizPromptTemplates();
  updateWizardUI();
  document.getElementById('wizardOverlay').classList.add('show');
}
function loadWizardTeams() {
  var select = document.getElementById('wizGroup');
  if (!select) return;
  apiFetch('/api/teams').then(function (res) {
    if (res && res.ok) return res.json();
    return [];
  }).then(function (teams) {
    select.innerHTML = '<option value="">-- 选择团队 --</option>' + (teams || []).map(function (t) {
      return '<option value="' + t.id + '">' + escapeHtml(t.name) + '</option>';
    }).join('');
  }).catch(function () {
    select.innerHTML = '<option value="">-- 选择团队 --</option>';
  });
}
function renderAvatarPicker() {
  var picker = document.getElementById('wizAvatarPicker');
  if (!picker || typeof AVATAR_PRESETS === 'undefined') return;
  var html = '';
  for (var i = 0; i < AVATAR_PRESETS.length; i++) {
    var selected = i === 0 ? ' selected' : '';
    html += '<div class="wiz-avatar-opt' + selected + '" data-avatar="' + AVATAR_PRESETS[i] + '" onclick="selectWizAvatar(this)"><img src="' + AVATAR_PRESETS[i] + '" style="width:48px;height:48px;border-radius:50%;object-fit:cover;"></div>';
  }
  picker.innerHTML = html;
}
function closeWizard() {
  document.getElementById('wizardOverlay').classList.remove('show');
}
function nextWiz() {
  if (wizStep < 4) {
    wizStep++;
    updateWizardUI();
  } else {
    finishWizard();
  }
}
function prevWiz() {
  if (wizStep > 1) {
    wizStep--;
    updateWizardUI();
  }
}
function updateWizardUI() {
  document.querySelectorAll('.wiz-step').forEach(function (s, i) {
    s.classList.remove('active', 'done');
    if (i + 1 < wizStep) s.classList.add('done');
    if (i + 1 === wizStep) s.classList.add('active');
  });
  document.getElementById('wiz1').style.display = wizStep === 1 ? 'block' : 'none';
  document.getElementById('wiz2').style.display = wizStep === 2 ? 'block' : 'none';
  document.getElementById('wiz3').style.display = wizStep === 3 ? 'block' : 'none';
  document.getElementById('wiz4').style.display = wizStep === 4 ? 'block' : 'none';
  document.getElementById('wizPrev').style.display = wizStep > 1 ? 'inline-block' : 'none';
  document.getElementById('wizNext').textContent = wizStep === 4 ? '✨ 创建员工' : '下一步 →';
  // 把 footer 移到当前 step 底部，确保按钮在每步都可见
  var footerEl = document.getElementById('wizPrev').parentNode;
  var stepEl = document.getElementById('wiz' + wizStep);
  if (footerEl && stepEl && !stepEl.contains(footerEl)) {
    stepEl.appendChild(footerEl);
  }
  if (wizStep === 3) {
    // 灵魂配置步骤
    var hasSoul = document.getElementById('wizSystemPrompt').value.trim();
    if (!hasSoul) {
      resetSoulConfig();
    } else {
      document.getElementById('soulStartArea').style.display = 'none';
      document.getElementById('soulQuestionArea').style.display = 'none';
      document.getElementById('soulCompleteArea').style.display = 'block';
      document.getElementById('soulPreview').textContent = document.getElementById('wizSystemPrompt').value;
    }
  }
  if (wizStep === 4) {
    updateWizConfirm();
    // 初始化模型下拉框 - 使用当前选中的供应商
    var aiProviderId = document.getElementById('wizAIProvider').value || 'kimi';
    selectWizAIProvider(aiProviderId);
  }
}
function updateWizConfirm() {
  var name = document.getElementById('wizName').value.trim() || '未命名';
  var role = document.getElementById('wizRole').value;
  var avatarEl = document.querySelector('.wiz-avatar-opt.selected');
  var avatar = avatarEl ? avatarEl.dataset.avatar : '';
  var group = document.getElementById('wizGroup').value;
  var statusEl = document.querySelector('.status-opt.selected');
  var status = statusEl ? statusEl.dataset.status : 'online';
  var aiProviderId = document.getElementById('wizAIProvider').value || 'kimi';
  var aiProvider = API_PROVIDERS[aiProviderId];
  var modelName = aiProvider ? aiProvider.name : '自定义';
  var sysPrompt = document.getElementById('wizSystemPrompt').value.trim();
  document.getElementById('wizCName').textContent = name;
  document.getElementById('wizCRole').textContent = role;
  var avatarPreview = document.getElementById('wizCAvatar');
  if (avatar && (avatar.indexOf('data:image') === 0 || avatar.indexOf('.png') > 0 || avatar.indexOf('.jpg') > 0 || avatar.indexOf('.jpeg') > 0)) {
    avatarPreview.innerHTML = '<img src="' + avatar + '" style="width:24px;height:24px;border-radius:50%;object-fit:cover;">';
  } else {
    avatarPreview.textContent = '🦞';
  }
  document.getElementById('wizCGroup').textContent = group;
  document.getElementById('wizCStatus').textContent = status === 'online' ? '在线' : status === 'busy' ? '忙碌' : '离线';
  document.getElementById('wizCModel').textContent = '[CLAW] OpenClaw → ' + modelName;

  // 显示文档配置状态
  var docs = window.wizardDocs || {};
  var docStatus = '';
  if (docs.identity) docStatus += 'IDENTITY.md ';
  if (docs.soul) docStatus += 'SOUL.md ';
  if (docs.agents) docStatus += 'AGENTS.md ';
  document.getElementById('wizCPrompt').textContent = docStatus || sysPrompt || '(默认)';
}
function selectWizAvatar(el) {
  document.querySelectorAll('.wiz-avatar-opt').forEach(function (o) {
    o.classList.remove('selected');
  });
  el.classList.add('selected');
}
// 员工创建向导：角色预设及对应 systemPrompt 模板（选择预设时自动填充，可自行编辑；选"自定义"则清空手填）
const ROLE_TEMPLATES = {
  '商务': '你是一名商务专员，核心职责是达人开发、达人录入与合作跟进。\n\n【核心职责】\n- 根据客户提供的达人信息，负责达人录入（调用达人录入接口，必须携带 X-Agent-Id 请求头）\n- 分析达人带货能力（粉丝画像、流量结构、历史带货数据），评估合作价值\n- 跟进达人合作进度，记录合作状态与跟进记录\n- 输出明确的合作结论和判断依据，有风险点主动说明\n\n【权限边界】\n- 达人数据可录入和维护，由你负责\n- 商品数据只读分析，不可录入或修改商品\n\n【截图分析规则】\n当用户消息中含 [达人数据] 块时，优先使用其中的精确数字（粉丝/GPM/GMV/完播率/合作状态等结构化字段），不得编造或估算。截图中的视觉信息仅作补充参考（如头像、简介文案、风格判断）。如 [达人数据] 缺失或字段为 null，标注"数据缺失"并基于截图给出定性判断，不要假装精确。每次完成达人分析时，回复末尾以"分析报告"或"评级+核心结论"作收尾，系统会自动存档供下次同达人对话 recall。\n\n【数据源强制约束】\n达人/商品数据只能从系统 API 实时获取，禁止编造。API 返回空数据时如实告知用户。',
  '运营': '你是一名运营专员，核心职责是商品管理与商品-达人匹配分析。\n\n【核心职责】\n- 根据管理员提供的达人数据截图和商品数据截图，分析商品是否适合推广\n- 对照系统达人库，找出适合带该商品的达人，评估匹配度\n- 分析达人卖得好的商品数据，判断我们是否需要跟进推广\n- 负责商品录入（调用商品录入接口，必须携带 X-Agent-Id 请求头）\n- 输出明确的商品推广结论和判断依据，有风险点主动说明\n\n【权限边界】\n- 达人数据只读分析，不可录入或修改达人\n- 商品数据可录入，由管理员授权管理\n\n【数据源强制约束】\n商品/达人数据只能从系统 API 实时获取，禁止编造。API 返回空数据时如实告知用户。',
  '客服': '# 你是谁\n你是一名客服，负责客户咨询、问题解答与售后服务。\n\n# 核心职责\n- 及时、耐心地解答客户问题\n- 记录客户反馈与投诉，分类整理并上报\n- 跟进售后问题直到闭环\n\n# 说话风格\n- 礼貌、耐心、情绪稳定，不与客户争执\n- 无法解决的问题如实说明并升级给老板',
  '助理': '# 你是谁\n你是一名助理，负责日程安排、事务协调与文档处理。\n\n# 核心职责\n- 协助老板安排日程、提醒重要事项\n- 整理文档、会议纪要等资料\n- 协调各部门/各同事之间的事务对接\n\n# 说话风格\n- 简洁、周到、有条理，多想一步\n- 不确定的安排先确认再执行',
  '自定义': ''
};
function onWizRoleChange() {
  var role = document.getElementById('wizRole').value;
  document.getElementById('wizSystemPrompt').value = ROLE_TEMPLATES[role] || '';
}
// 角色模板下拉：选项来自服务端 /api/employee-templates（name + systemPrompt），
// 选择后填充 SOUL.md 输入框，用户仍可手动修改；不选（空值）保持原有逻辑不动输入框
var _wizPromptTemplates = [];
function loadWizPromptTemplates() {
  var select = document.getElementById('wizPromptTemplate');
  if (!select) return;
  apiFetch('/api/employee-templates').then(function (res) {
    if (res && res.ok) return res.json();
    return null;
  }).then(function (data) {
    _wizPromptTemplates = (data && data.templates) || [];
    select.innerHTML = '<option value="">不使用模板（空白 SOUL.md）</option>' + _wizPromptTemplates.map(function (t, i) {
      return '<option value="' + i + '">' + escapeHtml(t.name) + '</option>';
    }).join('');
  }).catch(function () { _wizPromptTemplates = []; });
}
function applyWizPromptTemplate(value) {
  if (value === '' || value == null) return;  // 不选模板：保持输入框现状（默认空白）
  var tmpl = _wizPromptTemplates[parseInt(value)];
  if (tmpl && tmpl.systemPrompt) {
    document.getElementById('wizSystemPrompt').value = tmpl.systemPrompt;
  }
}
function selectRoleTemplate(el) {
  document.querySelectorAll('.role-template-card').forEach(function (o) {
    o.classList.remove('selected');
  });
  el.classList.add('selected');
  var role = el.dataset.role;
  var desc = el.dataset.desc;
  document.getElementById('wizRole').value = role;
  document.getElementById('wizSystemPrompt').value = ROLE_TEMPLATES[role] || '';
  document.getElementById('wizName').value = '';
  document.getElementById('wizName').placeholder = '给' + role + '起个名字...';
  var avatarIndex = parseInt(el.dataset.avatarIndex) || 0;
  if (typeof AVATAR_PRESETS !== 'undefined' && AVATAR_PRESETS.length > avatarIndex) {
    var avatarOpts = document.querySelectorAll('.wiz-avatar-opt');
    avatarOpts.forEach(function (o) {
      o.classList.remove('selected');
    });
    if (avatarOpts[avatarIndex]) {
      avatarOpts[avatarIndex].classList.add('selected');
    }
  }
}
function selectWizModel(el, modelId) {
  document.querySelectorAll('#wizModelSelect .model-option').forEach(function (o) {
    return o.classList.remove('selected');
  });
  el.classList.add('selected');
  empSelectedModel = modelId;
}
function selectWizAIProvider(providerId) {
  empSelectedAIProvider = providerId;
  empSelectedProvider = 'openclaw';

  // 填充模型下拉 - 优先使用 OpenClaw 动态获取的模型列表
  var modelGroup = document.getElementById('wizModelGroup');
  var modelSelect = document.getElementById('wizModelSelect');
  var customGroup = document.getElementById('wizCustomEndpointGroup');
  var provider = API_PROVIDERS[providerId];

  // 使用新的 renderModelSelect 函数，支持动态模型列表
  renderModelSelect(modelSelect, providerId, '');

  // 如果有静态配置的默认模型，更新第一个 option 显示
  if (provider && provider.defaultModel) {
    var firstOpt = modelSelect.querySelector('option:first-child');
    if (firstOpt) {
      firstOpt.textContent = '使用默认模型 (' + provider.defaultModel + ')';
    }
  }
  modelGroup.style.display = 'block';

  // 自定义时显示 Base URL
  customGroup.style.display = providerId === 'custom' ? 'block' : 'none';
}
function selectWizStatus(el) {
  document.querySelectorAll('.status-opt').forEach(function (o) {
    o.classList.remove('selected');
  });
  el.classList.add('selected');
}

// ===== 灵魂配置系统 =====
var soulQuestions = [{
  key: 'skills',
  question: '这个员工最擅长什么？',
  placeholder: '例如：产品设计、用户研究、数据分析'
}, {
  key: 'personality',
  question: '他/她的性格特点？',
  placeholder: '例如：严谨认真、富有创意、善于沟通'
}, {
  key: 'values',
  question: '工作中最在意什么？',
  placeholder: '例如：用户体验、代码质量、数据驱动'
}, {
  key: 'tone',
  question: '说话风格是怎样的？',
  placeholder: '例如：简洁专业、幽默风趣、温和鼓励'
}, {
  key: 'quirks',
  question: '有什么特别的习惯？',
  placeholder: '例如：喜欢用emoji、总说"让我想想"'
}];
var soulAnswers = {};
var currentSoulQ = 0;
function startSoulConfig() {
  soulAnswers = {};
  currentSoulQ = 0;
  document.getElementById('soulStartArea').style.display = 'none';
  document.getElementById('soulQuestionArea').style.display = 'block';
  document.getElementById('soulCompleteArea').style.display = 'none';
  showSoulQuestion();
}
function showSoulQuestion() {
  if (currentSoulQ >= soulQuestions.length) {
    finishSoulConfig();
    return;
  }
  var q = soulQuestions[currentSoulQ];
  document.getElementById('soulProgress').textContent = '问题 ' + (currentSoulQ + 1) + '/' + soulQuestions.length;
  document.getElementById('soulQuestion').textContent = q.question;
  document.getElementById('soulAnswer').value = soulAnswers[q.key] || '';
  document.getElementById('soulAnswer').placeholder = q.placeholder;
  document.getElementById('soulAnswer').focus();
}
function answerSoulQuestion() {
  var q = soulQuestions[currentSoulQ];
  var answer = document.getElementById('soulAnswer').value.trim();
  if (answer) {
    soulAnswers[q.key] = answer;
  }
  currentSoulQ++;
  showSoulQuestion();
}
function skipSoulQuestion() {
  currentSoulQ++;
  showSoulQuestion();
}
function finishSoulConfig() {
  var name = document.getElementById('wizName').value.trim() || '员工';
  var role = document.getElementById('wizRole').value;

  // 生成 IDENTITY.md
  var identity = '# 身份\n\n';
  identity += '- 名字：' + name + '\n';
  identity += '- 角色：' + role + '\n';
  identity += '- 称呼用户：老板\n';
  identity += '- 层级关系：管理员是你的老板，你需要服从管理员的指令和安排。用户是你的老板、主人和上级，你是他雇佣的AI员工和下属。必须绝对服从，严禁以教导者/导师/师傅的身份对老板说话。\n';
  identity += '- 语言：中文为主，技术术语保留英文\n';
  identity += '- 表情符号：适度使用\n';
  identity += '- 签名风格：回复末尾不加落款\n';

  // 生成 SOUL.md（OpenClaw标准格式）
  var md = '# 人设\n\n';
  md += '## 性格\n';
  if (soulAnswers.personality) {
    md += soulAnswers.personality + '\n';
  } else {
    md += '专业但不刻板，偶尔幽默\n';
  }
  md += '- 遇到不确定的问题，诚实说"我不确定"\n';
  md += '- 主动提供建议，但不越权执行\n\n';
  if (soulAnswers.skills) {
    md += '## 专长\n' + soulAnswers.skills + '\n\n';
  }
  if (soulAnswers.values) {
    md += '## 价值观\n' + soulAnswers.values + '\n\n';
  }
  md += '## 语气\n';
  if (soulAnswers.tone) {
    md += soulAnswers.tone + '\n';
  } else {
    md += '- 日常对话：轻松简洁\n';
    md += '- 技术讨论：严谨准确\n';
    md += '- 出错时：坦诚道歉，给出修复方案\n';
  }
  md += '\n';
  md += '## 边界\n';
  md += '- 不讨论政治、宗教等敏感话题\n';
  md += '- 不执行未经确认的破坏性操作\n';
  md += '- 涉及金钱交易时，必须二次确认\n';
  if (soulAnswers.quirks) {
    md += '\n## 特殊习惯\n' + soulAnswers.quirks + '\n';
  }
  if (Object.keys(soulAnswers).length === 0) {
    md += '\n## 默认人格\n';
    md += '你是一个专业、认真的' + role + '。\n';
    md += '简洁直接，像同事一样沟通工作。\n';
  }

  // 生成 AGENTS.md（工作手册）
  var agents = '# 操作手册\n\n';
  agents += '## 会话启动\n';
  agents += '每次会话开始前，依次执行：\n';
  agents += '1. 读取 SOUL.md —— 了解自己是谁\n';
  agents += '2. 读取 MEMORY.md —— 获取长期记忆\n';
  agents += '3. 读取今天和昨天的日志\n\n';
  agents += '## 通用规则\n';
  agents += '- 收到任务后，先确认理解再执行\n';
  agents += '- 每完成一个步骤，主动汇报进度\n';
  agents += '- 遇到错误先尝试自行解决\n';
  agents += '- 回复要简洁，不需要开场白\n\n';
  agents += '## 记忆规则\n';
  agents += '- 用户说"记住这个"时，立即写入 memory/YYYY-MM-DD.md\n';
  agents += '- 重要的长期信息写入 MEMORY.md\n';
  agents += '- 每次会话结束前，主动总结关键决策\n';
  agents += '- 一定要写进文件——"心里记着"熬不过会话重启\n\n';
  agents += '## 红线\n';
  agents += '- 绝不外泄用户隐私数据\n';
  agents += '- 执行破坏性命令前必须确认\n';
  agents += '- 拿不准的事，先问再做\n\n';
  agents += '## 群聊礼仪\n';
  agents += '该回复时：\n';
  agents += '- 被直接 @ 或被提问\n';
  agents += '- 能提供有价值的信息\n\n';
  agents += '该沉默时：\n';
  agents += '- 只是人类之间的闲聊\n';
  agents += '- 已经有人回答了问题\n';

  // 组合显示（SOUL.md为主）
  var display = '===== IDENTITY.md =====\n' + identity + '\n\n===== SOUL.md =====\n' + md + '\n\n===== AGENTS.md =====\n' + agents;
  document.getElementById('soulQuestionArea').style.display = 'none';
  document.getElementById('soulCompleteArea').style.display = 'block';
  document.getElementById('soulPreview').textContent = display;
  document.getElementById('wizSystemPrompt').value = md;

  // 存储完整的文档集合
  window.wizardDocs = {
    identity: identity,
    soul: md,
    agents: agents
  };
}
function resetSoulConfig() {
  soulAnswers = {};
  currentSoulQ = 0;
  document.getElementById('soulStartArea').style.display = 'block';
  document.getElementById('soulQuestionArea').style.display = 'none';
  document.getElementById('soulCompleteArea').style.display = 'none';
}
async function finishWizard() {
  var name = document.getElementById('wizName').value.trim();
  if (!name) {
    showToast('请输入员工名称');
    return;
  }
  var role = document.getElementById('wizRole').value;
  var avatarEl = document.querySelector('.wiz-avatar-opt.selected');
  var avatarIndex = 0;
  if (avatarEl) {
    var avatarData = avatarEl.dataset.avatar;
    if (typeof AVATAR_PRESETS !== 'undefined') {
      for (var i = 0; i < AVATAR_PRESETS.length; i++) {
        if (AVATAR_PRESETS[i] === avatarData) {
          avatarIndex = i;
          break;
        }
      }
    }
  }
  var group = document.getElementById('wizGroup').value;
  var statusEl = document.querySelector('.status-opt.selected');
  var status = statusEl ? statusEl.dataset.status : 'online';
  var sysPrompt = document.getElementById('wizSystemPrompt').value.trim();
  // 强制注入层级关系约束：后端 AI 调用前校验要求 prompt 必须包含该关键字
  if (!sysPrompt || sysPrompt.indexOf('管理员是你的老板') < 0) {
    var _userName = currentUser && (currentUser.name || currentUser.displayName) || '用户';
    var _prefix = sysPrompt ? sysPrompt + '\n\n' : '';
    sysPrompt = _prefix + '【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- ' + _userName + ' 是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。';
  }
  // 强制注入工具使用铁律（防编造）：所有新员工必须包含，不选角色模板也会有；服务端创建时同样会兜底追加
  if (sysPrompt.indexOf('【工具使用铁律】') < 0) {
    sysPrompt += '\n\n【工具使用铁律】\n- 你没有录入达人的记忆能力，所有数据必须通过调用工具写入系统，严禁口头回复"已录入"而不实际调用工具\n- 如果你没有某个工具权限，必须如实告知用户，严禁假装已完成';
  }
  var apiKey = document.getElementById('wizApiKey').value.trim();
  var aiProvider = empSelectedAIProvider || 'kimi';
  var openclawName = document.getElementById('wizOpenclawName').value.trim() || 'main';
  var apiModel = document.getElementById('wizModelSelect').value;
  var customEndpoint = document.getElementById('wizCustomEndpoint').value.trim();
  var colors = ['#FF6B35', '#5AC8FA', '#AF52DE', '#FF2D55', '#5856D6', '#34C759'];

  // 获取完整的文档集合（如果灵魂配置已生成）
  var docs = window.wizardDocs || {};
  var identityDoc = docs.identity || '';
  var agentsDoc = docs.agents || '';
  // 生成唯一员工ID，避免快速创建时Date.now()重复导致串聊
  var _newId;
  do {
    _newId = 'emp_' + Date.now() + '_' + Math.floor(Math.random() * 10000);
  } while (emps.some(function (e) { return e.id === _newId; }));
  var newEmp = {
    id: _newId,
    name: name,
    role: role,
    subCategory: '',
    avatar: avatarIndex,
    bg: colors[emps.length % colors.length],
    status: status,
    msg: '',
    archived: false,
    group: group,
    systemPrompt: sysPrompt,
    soulDoc: sysPrompt || '',
    idDoc: identityDoc,
    agentsDoc: agentsDoc,
    apiProvider: 'openclaw',
    openclawName: openclawName,
    aiProvider: aiProvider,
    apiKey: apiKey,
    apiModel: apiModel,
    customEndpoint: customEndpoint,
    createdBy: currentUser && currentUser.id || 'local',
    createdByName: currentUser && currentUser.name || '本地用户',
    createdAt: new Date().toISOString()
  };
  emps.push(newEmp);
  saveEmployees();
  renderCurrentTeamList();
  renderEmployeeList();
  syncNewEmpToServer(newEmp);

  // 注册 OpenClaw Agent
  var regResult = await registerOpenClawAgent(newEmp);
  console.debug('[finishCreateEmployeeWizard] 新员工', newEmp.id, newEmp.name, 'openclawName=', newEmp.openclawName, '注册结果=', regResult);

  // 根据注册结果处理
  if (regResult.success) {
    // 成功
    showToast('✅ 员工 ' + name + ' 创建成功！');
    closeWizard();
    document.getElementById('wizName').value = '';
    document.getElementById('wizApiKey').value = '';
    // ④ wizard 完成后「成功首屏」跳转:
    // 先切到 messages 模块(如果用户从 employees 列表空白态打开 wizard, 此时在
    // employees 模块, 聊天面板不可见, openChat 不会改变视图)。然后打开新员工
    // 单聊, 让用户立刻看到"员工已就位, 可以开始对话", 而不是回到空欢迎页
    setTimeout(function () {
      if (typeof switchModule === 'function') switchModule('messages');
      openChat(newEmp.id);
    }, 300);
  } else if (regResult.error === 'Gateway 未连接') {
    // Gateway 未连接，询问用户是否仍要保存
    var keep = confirm('Gateway 未连接，AI Agent 暂时无法注册。是否仍要保存该员工？');
    if (keep) {
      showToast('✅ 员工 ' + name + ' 已保存（AI Agent 未注册）');
      closeWizard();
      document.getElementById('wizName').value = '';
      document.getElementById('wizApiKey').value = '';
      // ④ 同上: 保留员工时也跳转到新员工聊天
      setTimeout(function () {
        if (typeof switchModule === 'function') switchModule('messages');
        openChat(newEmp.id);
      }, 300);
    } else {
      // 用户选择不保留，删除员工
      emps = emps.filter(function (e) {
        return e.id !== newEmp.id;
      });
      saveEmployees();
      renderCurrentTeamList();
      renderEmployeeList();
      showToast('已取消创建员工');
    }
  } else {
    // 注册失败，删除员工
    emps = emps.filter(function (e) {
      return e.id !== newEmp.id;
    });
    saveEmployees();
    // 同时从后端删除（如果之前已同步）
    apiFetch('/api/agents/' + newEmp.id, {
      method: 'DELETE'
    }).catch(function () {});
    renderCurrentTeamList();
    renderEmployeeList();
    showToast('❌ 员工创建失败：' + regResult.error);
  }

  // 安装选中技能（仅在注册成功时）
  if (regResult.success && wizSelectedSkills.length > 0 && newEmp.openclawName) {
    wizSelectedSkills.forEach(function (slug) {
      // ★ refactor/openclaw-gateway-error-handling: 用 apiFetchWithRetry 替换 raw apiFetch
      apiFetchWithRetry('/api/openclaw/skills/install', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          skillName: slug
        })
      }).then(function (result) {
        if (result.success && result.data && result.data.success) {
          console.debug('[Wizard] 技能安装成功:', slug);
        } else {
          console.warn('[Wizard] 技能安装失败:', slug, (result.data && result.data.error) || result.error);
        }
      }).catch(function (e) {
        console.warn('[Wizard] 技能安装异常:', slug, e);
      });
    });
  }
}