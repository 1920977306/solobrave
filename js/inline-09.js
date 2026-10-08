/* ===== index.html 内联块 9 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: /* ========================================================= */

/* ============================================================
 * SoloBrave 2.0 壳层 JS (feat/redesign-shell)
 * - 图标轨桥接旧全局函数(switchModule/openLobsterOffice/...), 旧模块逻辑零改动
 * - 工作台 KPI 全部取真实接口值, 取不到显示 '-', 严禁编造
 * - 旧 left-nav 保留 DOM 隐藏(display:none), 作回归兜底
 * ============================================================ */
(function(){
'use strict';

var SB2 = { dashVisible: false };

/* ---------- 图标库(lucide 风格描边) ---------- */
function icon(p){ return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'+p+'</svg>'; }
var ICONS = {
  dash:   icon('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
  chat:   icon('<path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>'),
  lobster:icon('<circle cx="12" cy="12" r="9"/><path d="M8 12h8M12 8v8"/>'),
  book:   icon('<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>'),
  sigma:  icon('<path d="M18 4H6l6 8-6 8h12"/>'),
  box:    icon('<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.3 7l8.7 5 8.7-5"/><path d="M12 22V12"/>'),
  users:  icon('<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>'),
  task:   icon('<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
  gear:   icon('<circle cx="12" cy="12" r="3"/><path d="M12 1v4m0 14v4M4.2 4.2l2.8 2.8m9.9 9.9 2.8 2.8M1 12h4m14 0h4M4.2 19.8l2.8-2.8m9.9-9.9 2.8-2.8"/>'),
  search: icon('<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>'),
  bell:   icon('<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/>'),
  /* ★ 待办 A 2026-10-08: 项目组 rail 图标 (users-round 风格, lucide 三人头) */
  groups: icon('<path d="M18 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>')
};

/* ---------- 导航定义(permission = 旧 hasModulePermission 模块名) ---------- */
var MODULES = [
  { id:'dashboard', label:'工作台',      icon:ICONS.dash,   perm:null },
  /* 〔fix/sb2-side-restore commit 10〕messages label '即时通讯' → 'AI 办公室' (13 轮热修)
     - 老大 16:04 拍板口径修正: 模块名改回 'AI 办公室', 但主区保持老 chat UI 不动
     - 解释: 「AI 办公室」是 rail 标识符, 跟主区实际是聊天不矛盾 — 标识符代表
       "员工对话总入口" 这个定位, 主区是 chat UI 实现这个定位
     - id 仍 'messages' (防 hideAllModuleMains / 权限清单大面积回归)
     - openLobsterOffice() 函数保留不删 (旧 left-nav navOffice 兜底, 防回归) */
  { id:'messages',  label:'AI 办公室',   icon:ICONS.chat,   perm:'messages' },
  /* ★ 待办 A 2026-10-08: 项目组主导航入口 (老大 02:40「可以」) — 权限键 'groups' 模块体系有效 */
  { id:'groups',    label:'项目组',      icon:ICONS.groups, perm:'groups' },
  { id:'knowledge', label:'知识库',      icon:ICONS.book,   perm:'knowledge' },
  { id:'patterns',  label:'规律库',      icon:ICONS.sigma,  perm:'patterns' },
  /* 〔r38 批注〕rail 模块顺序对齐原版 (原型 line 296-324: 工作台/聊天/知识库/规律库/达人库/商品库/任务)
     - 达人库 排在 商品库 前 (跟原版一致) */
  { id:'influencers',label:'达人库',     icon:ICONS.users,  perm:'influencers' },
  { id:'products',  label:'商品库',      icon:ICONS.box,    perm:'products' },
  { id:'tasks',     label:'任务',        icon:ICONS.task,   perm:'tasks' },
  { id:'settings',  label:'设置',        icon:ICONS.gear,   perm:'settings' }
];

function hasPerm(p){
  if (!p) return true;
  try { if (typeof hasModulePermission === 'function') return hasModulePermission(p); } catch(e){}
  return true;
}

/* ---------- 图标轨 ---------- */
function buildRail(){
  var rail = document.createElement('nav');
  rail.className = 'sb2-rail';
  rail.id = 'sb2Rail';
  var html = '<div class="sb2-rail-logo">Q</div>';  /* 〔18 轮批注⑤〕logo SB→Q (规格 docs/design-spec/v1-18轮批注.md, 样式不变 36×36 r10 橙渐变 800 15px) */
  MODULES.forEach(function(m){
    if (m.id === 'settings') return;  /* 〔r38〕设置不在模块列, 挪 rail 底部齿轮 (对齐原版 line 330) */
    if (!hasPerm(m.perm)) return;
    /* 〔r38〕messages 按钮挂未读徽章 (对齐原版 rail-badge line 303), 数值由 hookBellBadge 桥接 */
    var badge = (m.id === 'messages') ? '<span class="sb2-rail-badge" id="sb2RailMsgBadge" style="display:none;">0</span>' : '';
    html += '<button class="sb2-rail-btn" data-sb2="'+m.id+'" onclick="sb2Go(\''+m.id+'\')">'
          + m.icon + badge + '<span class="sb2-rail-tip">'+m.label+'</span></button>';
  });
  /* 〔r37 批注〕rail 底部对齐原版 (原型 rail 尾部: 切换明暗 + 设置 + 头像, 无搜索/通知)
     - 移除 rail 上的 搜索/通知 按钮 (原版没有; 功能入口不丢: 通知=顶栏铃铛 sb2TopbarBell,
       全局搜索=⌘K 面板 sb2_openPalette + 用户菜单)
     - 加 切换明暗 (toggleTheme, 太阳图标照原型 line 327)
     〔r38 批注「都说了做成一样的」〕补齐跟原版的全部差异:
     - 设置齿轮挪到 rail 底部 (原型 line 330), 模块列里不再有设置
     - 底部用户头像 (原型 line 334-337), 点击弹用户菜单 sb2RailUserMenu
       (旧 #userDropdown 在 .left-nav display:none 里, 直接复用弹不出来, 独立浮层实现) */
  html += '<div class="sb2-rail-spacer"></div>'
        + '<button class="sb2-rail-btn" onclick="toggleTheme()" id="sb2ThemeBtn">'
        + '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.3 11.3 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>'
        + '<span class="sb2-rail-tip">切换明暗</span></button>';
  if (hasPerm('settings')) {
    html += '<button class="sb2-rail-btn" data-sb2="settings" onclick="sb2Go(\'settings\')" id="sb2RailGear">'
          + ICONS.gear + '<span class="sb2-rail-tip">设置</span></button>';
  }
  html += '<button class="sb2-rail-btn sb2-rail-userbtn" onclick="sb2RailUserMenu()" id="sb2RailUser">'
        + '<span class="sb2-rail-avatar" id="sb2RailAvatar">?</span>'
        + '<span class="sb2-rail-tip" id="sb2RailUserTip">用户菜单</span></button>';
  rail.innerHTML = html;
  var app = document.querySelector('.app-container');
  app.parentNode.insertBefore(rail, app);
  sb2RailSyncUser();  /* rail 入 DOM 后再同步头像 */
}

/* 〔r38〕rail 头像同步当前登录用户 (首字符 + 提示名, 数据源同 buildDashboard: sb_current_user) */
function sb2RailSyncUser(){
  var user = null;
  try { user = JSON.parse(localStorage.getItem('sb_current_user') || 'null'); } catch(e){}
  var name = (user && (user.displayName || user.username || user.name)) || '';
  var av = document.getElementById('sb2RailAvatar');
  if (av) av.textContent = name ? name.charAt(0) : '?';
  var tip = document.getElementById('sb2RailUserTip');
  if (tip) tip.textContent = name || '用户菜单';
}

/* 〔r38〕rail 头像用户菜单 — 独立浮层 (旧 #userDropdown 在 display:none 的 .left-nav 里弹不出来)
   菜单项复用现成函数: 个人信息/切换AI/全局搜索/设置/退出登录 */
function sb2RailUserMenu(){
  var dd = document.getElementById('sb2RailUserMenu');
  if (dd) { dd.remove(); document.removeEventListener('click', sb2RailUserMenuClose); return; }
  dd = document.createElement('div');
  dd.className = 'sb2-rail-menu';
  dd.id = 'sb2RailUserMenu';
  var user = null;
  try { user = JSON.parse(localStorage.getItem('sb_current_user') || 'null'); } catch(e){}
  var name = (user && (user.displayName || user.username || user.name)) || '用户';
  var role = (user && user.role) || '';
  dd.innerHTML = '<div class="sb2-rail-menu-hd"><div class="sb2-rail-menu-name"></div>'
    + (role ? '<div class="sb2-rail-menu-role"></div>' : '') + '</div>'
    + '<div class="sb2-rail-menu-div"></div>'
    + '<div class="sb2-rail-menu-item" data-act="profile">个人信息</div>'
    + '<div class="sb2-rail-menu-item" data-act="ai">切换 AI</div>'
    + '<div class="sb2-rail-menu-item" data-act="search">全局搜索</div>'
    /* 〔r39-2 老系统功能保留〕对齐老用户菜单 (legacy #userDropdown line 14018):
       新建对话全员可见; 用户管理/安全工具 admin-only (isAdmin 门控, 跟 legacy 一致) */
    + '<div class="sb2-rail-menu-item" data-act="newchat">新建对话</div>'
    + (typeof isAdmin === 'function' && isAdmin()
        ? '<div class="sb2-rail-menu-item" data-act="usermgmt">用户管理</div>'
        + '<div class="sb2-rail-menu-item" data-act="security">安全工具</div>'
        : '')
    + '<div class="sb2-rail-menu-item" data-act="settings">设置</div>'
    + '<div class="sb2-rail-menu-div"></div>'
    + '<div class="sb2-rail-menu-item danger" data-act="logout">退出登录</div>';
  dd.querySelector('.sb2-rail-menu-name').textContent = name;
  if (role) dd.querySelector('.sb2-rail-menu-role').textContent = role;
  dd.addEventListener('click', function(e){
    var item = e.target.closest('.sb2-rail-menu-item');
    if (!item) return;
    var act = item.getAttribute('data-act');
    sb2RailUserMenu();  /* 先关菜单 */
    if (act === 'profile' && typeof showUserProfile === 'function') showUserProfile();
    else if (act === 'ai' && typeof showAISwitcher === 'function') showAISwitcher();
    else if (act === 'search' && typeof openGlobalSearch === 'function') openGlobalSearch();
    else if (act === 'newchat' && typeof startNewChat === 'function') startNewChat();
    else if (act === 'usermgmt' && typeof showUserManagement === 'function') showUserManagement();
    else if (act === 'security' && typeof showSecurityTools === 'function') showSecurityTools();
    else if (act === 'settings') sb2Go('settings');
    else if (act === 'logout' && typeof confirmLogout === 'function') confirmLogout();
  });
  document.body.appendChild(dd);
  setTimeout(function(){ document.addEventListener('click', sb2RailUserMenuClose); }, 0);
}
function sb2RailUserMenuClose(e){
  var dd = document.getElementById('sb2RailUserMenu');
  var btn = document.getElementById('sb2RailUser');
  if (dd && !dd.contains(e.target) && e.target !== btn && !(btn && btn.contains(e.target))) {
    dd.remove();
    document.removeEventListener('click', sb2RailUserMenuClose);
  }
}
/* 暴露到 window: 本段在 IIFE 内, HTML onclick 属性够不到内部函数 (buildRail 内部调用不受限) */
window.sb2RailUserMenu = sb2RailUserMenu;
window.sb2RailUserMenuClose = sb2RailUserMenuClose;

/* 通知数桥接: 旧代码更新 #navNotifications badge 时同步到轨上 + 新顶栏徽章 */
function hookBellBadge(){
  var navBell = document.getElementById('navNotifications');
  if (!navBell) return;
  var target = navBell.querySelector('[data-count]') || navBell;
  var apply = function(){
    var n = target.getAttribute('data-count') || '0';
    var v = parseInt(n, 10) || 0;
    var railEl = document.getElementById('sb2RailBell');
    if (railEl) {
      railEl.style.display = v > 0 ? '' : 'none';
      railEl.textContent = v > 99 ? '99+' : String(v);
    }
    var topEl = document.getElementById('sb2TopbarBellBadge');
    if (topEl) {
      topEl.style.display = v > 0 ? '' : 'none';
      topEl.textContent = v > 99 ? '99+' : String(v);
    }
    /* 〔r38〕rail messages 按钮徽章同步 (对齐原版 rail-badge) */
    var railMsgEl = document.getElementById('sb2RailMsgBadge');
    if (railMsgEl) {
      railMsgEl.style.display = v > 0 ? '' : 'none';
      railMsgEl.textContent = v > 99 ? '99+' : String(v);
    }
  };
  var obs = new MutationObserver(apply);
  obs.observe(target, { attributes:true, childList:true, subtree:true, characterData:true });
  apply();
}

/* ---------- 工作台屏 ---------- */
/* MVP1: 4 张 KPI 卡升级到 .sb2-dash2-* DOM, 走 /api/stats/dashboard-kpi 真数据
   卡 1 = 「近 7 天知识注入条数」(GMV 无时序, 老大 16:23 拍板换真指标)
   sparkline: SVG 内联 7 点 + delta_pct 涨跌色
   #sb2Dash 覆盖层机制保留, 新内容 .sb2-dash2-* 前缀
   MVP2: 三 feed 区 (今日关注 + 规律洞察 + 员工动态) 接 KPI 4 卡之后
   同套 .sb2-dash2-* 前缀; 数据流 fetch + Promise.all
   踩坑 (MVP1 + MVP2 同类型): 字符串拼接 + // 注释 + kpi() 调用 = + string + + kpi() = + string + NaN
   同样 + 〔块注释〕 + expr 也会触发 + + 一元正号 = NaN, 修法: 注释挪到函数体外 〔块注释〕 */
function buildDashboard(){
  if (document.getElementById('sb2Dash')) return;
  var dash = document.createElement('div');
  dash.className = 'sb2-dash';
  dash.id = 'sb2Dash';
  dash.innerHTML =
    '<div class="sb2-dash-main"><div class="sb2-wrap">'
    +   '<div class="sb2-dash2-greet">'
    +     '<div><div class="sb2-dash2-greet-date" id="sb2GreetDate"></div>'
    +     '<h1 class="sb2-dash2-greet-title" id="sb2GreetTitle">工作台</h1>'
    /* ★ fix/sb2-proto-align (commit 2): 删 sub「今日智能协作 · 数据概览」(原型 line 369-371 没有 sub 元素) */ + '</div>'
    /* 〔dash-opt-1 2026-10-08 老大拍板方向 A: 工作台 = 待办指挥中心〕
        删「达人跟进」纯导航钮 (左侧导航栏已有同功能入口, 老大多此一举批注);
        「进入 AI 办公室」保留为唯一主 CTA */
    +     '<div class="sb2-dash2-greet-acts"><button class="sb2-btn sb2-btn-brand" onclick="sb2Go(\'messages\')">进入 AI 办公室</button></div>'
    +   '</div>'
    /* 〔dash-opt-1〕等你拍板 hero 区: 待审批提案 inline 拍板 (复用 P1b 提案卡 6 态管线, 零新交互逻辑)
        空态整块隐藏 (JS 控制), 不渲占屏占位
        〔2026-10-08 老大批注「这个怎么还是在啊」复盘: 批注对象是 5 天前的陈旧「等你回复」卡
        赖着不走, 非删整条 — 条恢复; 陈旧根治在 sb2_loadDashboardTodo() 第 4 路加 48h 新鲜度窗〕 */
    +   '<div class="sb2-dash2-todo" id="sb2Dash2Todo" style="display:none">'
    +     '<div class="sb2-dash2-feed-hd"><h2>等你拍板</h2><span class="hint">来自 AI 员工的提案, 同意后立即执行</span></div>'
    +     '<div id="sb2Dash2TodoList" class="sb2-dash2-todo-list"></div>'
    +   '</div>'
    +   '<div class="sb2-kpis">'
    +     kpi('sb2KpiInject','近 7 天知识注入','')
    +     kpi('sb2KpiTalents','在库达人','')
    +     kpi('sb2KpiKb','知识库条目','')
    +     kpi('sb2KpiAgents','AI 员工在线','')
    +   '</div>'
    +   '<div class="sb2-dash2-feed-grid">'
    +     '<div class="sb2-dash2-feed sb2-dash2-feed-focus">'
    +       '<div class="sb2-dash2-feed-hd"><h2>今日关注</h2><span class="hint">按紧急度排序</span></div>'
    +       '<div id="sb2Dash2FocusList" class="sb2-dash2-feed-focus-list"></div>'
    +     '</div>'
    +     '<div class="sb2-dash2-feed-rightcol">'
    +       '<div class="sb2-dash2-feed sb2-dash2-feed-insights">'
    +         '<div class="sb2-dash2-feed-hd"><h2>规律洞察</h2><span class="hint">本周最活跃</span></div>'
    +         '<div id="sb2Dash2InsightList" class="sb2-dash2-feed-insights-list"></div>'
    +       '</div>'
    +       '<div class="sb2-dash2-feed sb2-dash2-feed-activity">'
    +         '<div class="sb2-dash2-feed-hd"><h2>员工动态</h2><span class="hint">最近 24 小时</span></div>'
    +         '<div id="sb2Dash2ActivityList" class="sb2-dash2-feed-activity-list"></div>'
    +       '</div>'
    +     '</div>'
    +   '</div>'
    + '</div></div>';
  document.body.appendChild(dash);
  // 日期/问候 (B1 节日: 命中当天追加「· 国庆节 第N天」)
  var now = new Date();
  var wd = ['日','一','二','三','四','五','六'][now.getDay()];
  var dateStr = (now.getMonth()+1)+'月'+now.getDate()+'日 星期'+wd;
  var holidayLabel = _sb2GetHolidayLabel(now);
  if (holidayLabel) dateStr += ' · ' + holidayLabel;
  document.getElementById('sb2GreetDate').textContent = dateStr;
  var h = now.getHours();
  var greet = h < 6 ? '夜深了' : h < 12 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好';
  var user = null;
  try { user = JSON.parse(localStorage.getItem('sb_current_user') || 'null'); } catch(e){}
  var name = (user && (user.displayName || user.username || user.name)) || '';
  document.getElementById('sb2GreetTitle').textContent = greet + (name ? '，' + name : '');
}
function kpi(id, label, sub){
  return '<div class="sb2-dash2-kpi" id="'+id+'">'
       +   '<div class="sb2-dash2-kpi-top"><div class="sb2-dash2-kpi-label">'+label+'</div></div>'
       +   '<div class="sb2-dash2-kpi-value" data-v="-">-</div>'
       +   '<svg class="sb2-dash2-kpi-spark" viewBox="0 0 100 32" preserveAspectRatio="none" data-empty="1">'
       +     '<polyline class="sb2-dash2-kpi-spark-area" points=""/>'
       +     '<polyline class="sb2-dash2-kpi-spark-line" points=""/>'
       +     '<circle class="sb2-dash2-kpi-spark-dot" r="1.6" style="display:none"/>'
       +   '</svg>'
       +   '<div class="sb2-dash2-kpi-bottom">'
       +     '<span class="sb2-dash2-kpi-delta flat" data-empty="1">— 持平</span>'
       +     '<span class="sb2-dash2-kpi-sub">'+sub+'</span>'
       +   '</div>'
       + '</div>';
}
function setKpi(id, val){
  // MVP1 兼容: 老调用 setKpi(id, '...') 仅改 .sb2-dash2-kpi-value 文本
  var el = document.getElementById(id);
  if (!el) return;
  var valEl = el.querySelector('.sb2-dash2-kpi-value');
  if (valEl) { valEl.textContent = String(val); valEl.setAttribute('data-v', String(val)); }
}
/* ★ fix/sb2-proto-align (commit 2): 业务化 KPI delta + sub (卡 2/3/4)
   payload: { deltaText, deltaCls (up/down/flat), sub }
   deltaCls 用现有 .sb2-dash2-kpi-delta.up/.down/.flat (no hex, 全 var(--sb2-*))
   sub 删了 — 业务文案都在 delta 里, 不需要冗余 sub */
function setKpiBusiness(id, payload){
  var el = document.getElementById(id);
  if (!el) return;
  var deltaEl = el.querySelector('.sb2-dash2-kpi-delta');
  if (deltaEl && payload.deltaText != null){
    deltaEl.textContent = payload.deltaText;
    deltaEl.removeAttribute('data-empty');
    deltaEl.classList.remove('up','down','flat');
    if (payload.deltaCls) deltaEl.classList.add(payload.deltaCls);
    /* 卡 3 用纯文字色不需 padding (原型 style="color:var(--warning);"),
       业务化后统一走 up/down/flat 风格, 保留 padding */
  }
  var subEl = el.querySelector('.sb2-dash2-kpi-sub');
  if (subEl && payload.sub != null){
    subEl.textContent = payload.sub;
  }
}
function setKpi2(id, payload){
  // payload = { value, sparkline?, delta_pct?, down? }
  var el = document.getElementById(id);
  if (!el) return;
  var valEl = el.querySelector('.sb2-dash2-kpi-value');
  if (valEl && payload.value != null) { valEl.textContent = String(payload.value); valEl.setAttribute('data-v', String(payload.value)); }
  var sparkEl = el.querySelector('.sb2-dash2-kpi-spark');
  var areaEl = el.querySelector('.sb2-dash2-kpi-spark-area');
  var lineEl = el.querySelector('.sb2-dash2-kpi-spark-line');
  var dotEl = el.querySelector('.sb2-dash2-kpi-spark-dot');
  var deltaEl = el.querySelector('.sb2-dash2-kpi-delta');
  // sparkline 渲染 (7 点等间距 SVG, 1.5 像素线)
  if (payload.sparkline && payload.sparkline.length > 0){
    var arr = payload.sparkline.map(function(v){ return Number(v) || 0; });
    var max = Math.max.apply(null, arr.concat([1]));
    var pts = arr.map(function(v, i){
      var x = (i / (arr.length - 1)) * 100;
      var y = 32 - (v / max) * 28 - 2;
      return x.toFixed(2) + ',' + y.toFixed(2);
    });
    if (areaEl) areaEl.setAttribute('points', pts.join(' ') + ' 100,32 0,32');
    if (lineEl) { lineEl.setAttribute('points', pts.join(' ')); lineEl.classList.toggle('down', !!payload.down); }
    if (dotEl && pts.length){
      var last = pts[pts.length - 1].split(',');
      dotEl.setAttribute('cx', last[0]); dotEl.setAttribute('cy', last[1]);
      dotEl.classList.toggle('down', !!payload.down); dotEl.style.display = '';
    }
    if (sparkEl) sparkEl.removeAttribute('data-empty');
  }
  // delta 涨跌色
  if (deltaEl && payload.delta_pct !== undefined && payload.delta_pct !== null){
    deltaEl.classList.remove('up','down','flat'); deltaEl.removeAttribute('data-empty');
    var d = Number(payload.delta_pct);
    if (!isFinite(d) || Math.abs(d) < 0.5){
      deltaEl.classList.add('flat'); deltaEl.textContent = '— 持平';
    } else if (d > 0){
      deltaEl.classList.add('up'); deltaEl.textContent = '↑ ' + d.toFixed(1) + '%';
    } else {
      deltaEl.classList.add('down'); deltaEl.textContent = '↓ ' + Math.abs(d).toFixed(1) + '%';
    }
  }
}
function fmtMoney(n){
  if (n >= 1e8) return '≈¥' + (n/1e8).toFixed(2) + '亿';
  if (n >= 1e4) return '≈¥' + (n/1e4).toFixed(1) + '万';
  return '¥' + Math.round(n).toLocaleString();
}

/* ---------- KPI 真实数据 (MVP1: 单端点 /api/stats/dashboard-kpi 聚合) ---------- */
function sb2_loadDashboardKpis(){
  var tok = localStorage.getItem('sb_auth_token') || '';
  var headers = { 'Authorization': 'Bearer ' + tok };
  var xid = (window.SB2 && SB2.agentId) || localStorage.getItem('sb_agent_id') || '';
  if (xid) headers['X-Agent-Id'] = xid;
  // MVP1: 加 dashboard 模块权限头 (跟其他统计端点一致)
  try {
    var mods = JSON.parse(localStorage.getItem('sb_module_perms') || 'null');
    if (mods && Array.isArray(mods)) headers['X-Module-Perms'] = JSON.stringify(mods);
  } catch(e){}

  fetch('/api/stats/dashboard-kpi', { headers: headers })
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (!d || typeof d !== 'object') throw new Error('bad payload');
      // 卡 1: 知识注入 (含 sparkline + delta_pct)
      var k1 = d.kpi1_inject || {};
      setKpi2('sb2KpiInject', {
        value: k1.value != null ? k1.value : '-',
        sparkline: Array.isArray(k1.sparkline) ? k1.sparkline : null,
        delta_pct: typeof k1.delta_pct === 'number' ? k1.delta_pct : null,
        down: typeof k1.delta_pct === 'number' && k1.delta_pct < 0
      });
      // 卡 2: 在库达人 (value + week_new)
      var k2 = d.kpi2_talents || {};
      setKpi('sb2KpiTalents', k2.value != null ? String(k2.value) : '-');
      SB2.talentTotal = (typeof k2.value === 'number') ? k2.value : null;
      SB2.weekNewTalents = (typeof k2.week_new === 'number') ? k2.week_new : 0;
      /* ★ fix/sb2-proto-align (commit 2): 卡 2 delta 业务化「本周新增 N」(原型 line 393)
         之前: delta "— 持平" + sub "当前可见范围" (技术文案, 不业务化)
         现在: delta "▲ 本周新增 N" (有数据时) / "—" (无数据) + sub 删 */
      setKpiBusiness('sb2KpiTalents', {
        deltaText: (SB2.weekNewTalents > 0) ? ('▲ 本周新增 ' + SB2.weekNewTalents) : '—',
        deltaCls: (SB2.weekNewTalents > 0) ? 'up' : 'flat',
        sub: ''
      });
      // 卡 3: 知识库条目 (value + pending)
      var k3 = d.kpi3_kb || {};
      setKpi('sb2KpiKb', k3.value != null ? String(k3.value) : '-');
      SB2.kbTotal = (typeof k3.value === 'number') ? k3.value : null;
      SB2.kbPending = (typeof k3.pending === 'number') ? k3.pending : 0;
      SB2.kbOk = (SB2.kbTotal != null) ? (SB2.kbTotal - SB2.kbPending) : null;
      SB2.kbFullyCounted = true; // 单端点直给, 不再翻页估算
      /* ★ fix/sb2-proto-align (commit 2): 卡 3 delta 业务化「N 条待审核」(原型 line 395)
         之前: delta "— 持平" + sub "kb_entries 总数" (裸露表名)
         现在: delta "N 条待审核" (warning 颜色, 原型 style="color:var(--warning)") + sub 删 */
      setKpiBusiness('sb2KpiKb', {
        deltaText: (SB2.kbPending > 0) ? (SB2.kbPending + ' 条待审核') : '—',
        deltaCls: (SB2.kbPending > 0) ? 'down' : 'flat',
        sub: ''
      });
      // 卡 4: AI 员工在线 (online / total, gateway_offline 离线数)
      var k4 = d.kpi4_agents || {};
      var total = (typeof k4.total === 'number') ? k4.total : 0;
      var online = (typeof k4.online === 'number') ? k4.online : 0;
      setKpi('sb2KpiAgents', total ? (String(online) + ' / ' + String(total)) : '-');
      SB2.agentsTotal = total;
      SB2.agentsOffline = (typeof k4.gateway_offline === 'number') ? k4.gateway_offline : (total - online);
      /* ★ fix/sb2-proto-align (commit 2): 卡 4 delta 业务化「N 个网关离线」(原型 line 398)
         之前: delta "— 持平" + sub "/api/agents 实时" (裸露 API 路径)
         现在: delta "▼ N 个网关离线" (down 红) / "—" (无离线) + sub 删 */
      setKpiBusiness('sb2KpiAgents', {
        deltaText: (SB2.agentsOffline > 0) ? ('▼ ' + SB2.agentsOffline + ' 个网关离线') : '—',
        deltaCls: (SB2.agentsOffline > 0) ? 'down' : 'flat',
        sub: ''
      });
      // 今日关注 (MVP1: 数据齐后一次组装, 1.8s 缓冲让 DOM 落位)
      setTimeout(buildFocus, 200);
    })
    .catch(function(err){
      // 任何一项失败都显示 -, 但不破坏其它卡的兜底
      ['sb2KpiInject','sb2KpiTalents','sb2KpiKb','sb2KpiAgents'].forEach(function(id){
        if (!document.getElementById(id)) return;
        var v = document.querySelector('#'+id+' .sb2-dash2-kpi-value');
        if (v && v.getAttribute('data-v') === '-') return;
      });
    });
  /* MVP2: 三 feed 区加载 (今日关注 + 规律洞察 + 员工动态) — 与 KPI 4 卡并行 */
  sb2_loadDashboardFeeds();
  /* 〔dash-opt-1〕等你拍板 hero 区 — 与 feed 并行 */
  sb2_loadDashboardTodo();
}
// 兑底: 老 loadKpis() 仍保留 (回归失败可切回), 但 buildDashboard 默认走新函数
function loadKpis(){ sb2_loadDashboardKpis(); }

function focusItem(tagCls, tag, title, desc, target){
  return '<div class="sb2-item" onclick="sb2Go(\''+target+'\')">'
       + '<div class="body"><div class="t">'+title+'</div><div class="d">'+desc+'</div></div>'
       + '<span class="sb2-tag '+tagCls+'">'+tag+'</span></div>';
}
function buildFocus(){
  /* MVP1 兼容: 老逻辑 (写 #sb2FocusList / #sb2MiniBody), 但 MVP2 已删除, 函数无副作用 */
  return;
}

/* ---------- 〔dash-opt-1 2026-10-08 老大拍板方向 A〕等你拍板 hero 区 ---------- */
/* 工作台 = 待办指挥中心: 核心是给老板「做决定」的台面, 不是导航中转
   数据源 (3 路并行, 零编造 — 空类不渲染):
     1. 待审批提案  GET /api/proposals?status=pending&limit=10
        → .sb2-prop-card loading 占位 → window.sb2PropMountCards (P1b 既有管线) 填 6 态卡
        → inline 选项/驳回/409 处理/执行结果全部复用, 零新交互逻辑 (SEV1 教训: 不抄第二份)
     2. 知识待审核  GET /api/knowledge/entries?createdAfter=<now-7d>&limit=100 (服务端 SQL 下推时间窗,
        老大红线: 不过滤集≠全量集; 客户端只滤 status==='pending') → 通过 PUT {status:'ok'} / 驳回 DELETE
        (与知识库模块 approveKnowledgeDoc/rejectKnowledgeDoc 同契约, 但不复用函数 — 那两个成功后会
        调 loadKnowledgePage() 重渲知识模块, 在 dashboard 上下文有副作用)
     3. 逾期任务    GET /api/tasks (admin 全量) → 客户端滤 status!=='completed' && deadline<今天
        → 「去处理」跳任务模块并直接打开该任务表单 (带 payload 的跳转, 不是裸导航)
     4. 等你回复    GET /api/notifications?unread_only=1 (AI 回复推 type='message' 未读通知, 带 agent_id)
        → 按 agent 去重取最新 → 「去回复」置已读 + 打开该员工聊天 (行级照抄侧栏四步链)
   空态: 四块全空 → section display:none (不占屏, 老大「多此一举」红线 — 没待办就不渲 hero)
   拍板后: 提案卡就地切结果态; KB/任务卡在 DOM 内移除该行并重算, 全空收 section */
function sb2_loadDashboardTodo(){
  var tok = localStorage.getItem('sb_auth_token') || '';
  var headers = { 'Authorization': 'Bearer ' + tok };
  var xid = (window.SB2 && SB2.agentId) || localStorage.getItem('sb_agent_id') || '';
  if (xid) headers['X-Agent-Id'] = xid;
  try {
    var mods = JSON.parse(localStorage.getItem('sb_module_perms') || 'null');
    if (mods && Array.isArray(mods)) headers['X-Module-Perms'] = JSON.stringify(mods);
  } catch(e){}

  var proposalsP = fetch('/api/proposals?status=pending&limit=10', { headers: headers })
    .then(function(r){ return r.json(); })
    .then(function(d){
      var list = (d && Array.isArray(d.proposals)) ? d.proposals : (Array.isArray(d) ? d : []);
      return list.filter(function(p){ return p && p.id; });
    })
    .catch(function(){ return []; });

  var kbCutoff = Date.now() - 7 * 86400 * 1000;
  var kbP = fetch('/api/knowledge/entries?createdAfter=' + kbCutoff + '&limit=100', { headers: headers })
    .then(function(r){ return r.json(); })
    .then(function(d){
      var docs = (d && Array.isArray(d.docs)) ? d.docs : [];
      return docs.filter(function(x){ return x && x.status === 'pending' && x.id; });
    })
    .catch(function(){ return []; });

  var tasksP = fetch('/api/tasks', { headers: headers })
    .then(function(r){ return r.json(); })
    .then(function(d){
      var list = (d && Array.isArray(d.tasks)) ? d.tasks : [];
      var today = new Date();
      var ymd = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0') + '-' + String(today.getDate()).padStart(2, '0');
      return list.filter(function(t){
        if (!t || t.status === 'completed') return false;
        var dl = String(t.deadline || '').trim();
        if (!dl) return false;
        var parts = dl.split('-');
        var iso = parts.length === 2 ? today.getFullYear() + '-' + dl : dl;
        return iso < ymd;
      });
    })
    .catch(function(){ return []; });

  /* 4) 等你回复: AI 回复时服务端推 type='message' 未读通知 (chat POST 链路 _push_notification,
        带 agent_id + 回复摘要). 按 agent_id 去重取最新一条/人 */
  var notifP = fetch('/api/notifications?unread_only=1&limit=50', { headers: headers })
    .then(function(r){ return r.json(); })
    .then(function(d){
      var items = (d && Array.isArray(d.items)) ? d.items : [];
      /* ★ fix 2026-10-08 (老大批注「怎么还是在啊」): 48h 新鲜度窗 — 陈旧未读回复
         (如 5 天前的 Helen) 不再是「当前待办」, 不再赖在拍板条上; 点「去回复」仍正常置已读 */
      var freshCutoff = Date.now() - 48 * 3600 * 1000;
      var byAgent = {};
      items.forEach(function(n){
        if (!n || n.type !== 'message' || !n.agent_id) return;
        if ((n.created_at || 0) < freshCutoff) return;
        if (!byAgent[n.agent_id] || (n.created_at || 0) > (byAgent[n.agent_id].created_at || 0)) {
          byAgent[n.agent_id] = n;
        }
      });
      return Object.keys(byAgent).map(function(k){ return byAgent[k]; })
        .sort(function(a, b){ return (b.created_at || 0) - (a.created_at || 0); });
    })
    .catch(function(){ return []; });

  Promise.all([proposalsP, kbP, tasksP, notifP]).then(function(arr){
    var section = document.getElementById('sb2Dash2Todo');
    var el = document.getElementById('sb2Dash2TodoList');
    if (!section || !el) return;
    var proposals = arr[0], kbPending = arr[1], overdueTasks = arr[2], unreadReplies = arr[3];
    if (proposals.length === 0 && kbPending.length === 0 && overdueTasks.length === 0 && unreadReplies.length === 0){
      /* 清空旧卡再藏: 防「先有待办后清零」时残留假卡 (拍板后重刷场景) */
      el.innerHTML = '';
      section.style.display = 'none';
      return;
    }
    section.style.display = '';
    var html = '';
    html += proposals.map(function(p){
      return '<div class="sb2-prop-card loading" data-state="loading" data-proposal-id="' + escapeHtml(p.id) + '">加载提议卡片…</div>';
    }).join('');
    /* 知识待审核卡 (与提案卡同款容器语言, 行内 通过/驳回) */
    if (kbPending.length > 0){
      var kbRows = kbPending.slice(0, 3).map(function(x){
        return '<div class="sb2-dash2-todo-row" data-kb-id="' + escapeHtml(x.id) + '">'
             +   '<span class="sb2-dash2-todo-row-t" title="' + escapeHtml(x.title || '') + '">' + escapeHtml((x.title || '(无标题)').slice(0, 30)) + '</span>'
             +   '<span class="sb2-dash2-todo-row-acts">'
             +     '<button type="button" class="sb2-dash2-todo-btn ok" onclick="sb2DashTodoKbApprove(\'' + escapeHtml(x.id) + '\', this)">通过</button>'
             +     '<button type="button" class="sb2-dash2-todo-btn no" onclick="sb2DashTodoKbReject(\'' + escapeHtml(x.id) + '\', this)">驳回</button>'
             +   '</span>'
             + '</div>';
      }).join('');
      html += '<div class="sb2-prop-card sb2-dash2-todo-kb" data-state="pending">'
           +   '<div class="sb2-prop-card-head"><span class="sb2-prop-card-state pending">待审核</span><span style="font-size:11px;color:var(--sb2-t3);">知识库</span></div>'
           +   '<div class="sb2-prop-card-title">近 7 天 ' + kbPending.length + ' 条知识待审核</div>'
           +   '<div class="sb2-dash2-todo-rows">' + kbRows + '</div>'
           +   (kbPending.length > 3 ? '<div class="sb2-dash2-todo-more">还有 ' + (kbPending.length - 3) + ' 条在知识库模块处理</div>' : '')
           + '</div>';
    }
    /* 逾期任务卡 (行内 去处理 → 任务模块开表单) */
    if (overdueTasks.length > 0){
      var tkRows = overdueTasks.slice(0, 3).map(function(t){
        return '<div class="sb2-dash2-todo-row">'
             +   '<span class="sb2-dash2-todo-row-t" title="' + escapeHtml(t.title || '') + '">' + escapeHtml((t.title || '(无标题)').slice(0, 30)) + '</span>'
             +   '<span class="sb2-dash2-todo-row-meta">截止 ' + escapeHtml(t.deadline || '—') + '</span>'
             +   '<span class="sb2-dash2-todo-row-acts"><button type="button" class="sb2-dash2-todo-btn ok" onclick="sb2DashTodoOpenTask(\'' + escapeHtml(t.id) + '\')">去处理</button></span>'
             + '</div>';
      }).join('');
      html += '<div class="sb2-prop-card sb2-dash2-todo-task" data-state="pending">'
           +   '<div class="sb2-prop-card-head"><span class="sb2-prop-card-state pending">待处理</span><span style="font-size:11px;color:var(--sb2-t3);">任务</span></div>'
           +   '<div class="sb2-prop-card-title">' + overdueTasks.length + ' 个任务已过截止日未完成</div>'
           +   '<div class="sb2-dash2-todo-rows">' + tkRows + '</div>'
           +   (overdueTasks.length > 3 ? '<div class="sb2-dash2-todo-more">还有 ' + (overdueTasks.length - 3) + ' 个在任务模块处理</div>' : '')
           + '</div>';
    }
    /* 等你回复卡 (行内 去回复 → 打开该员工聊天 + 通知置已读) */
    if (unreadReplies.length > 0){
      var empsIndex = {};
      try {
        (typeof window.emps !== 'undefined' && Array.isArray(window.emps) ? window.emps : []).forEach(function(e){
          if (e && e.id) empsIndex[e.id] = e.display_name || e.name || '';
        });
      } catch(e){}
      var chatRows = unreadReplies.slice(0, 3).map(function(n){
        var empName = empsIndex[n.agent_id] || (n.title || '').replace(/ 回复了你$/, '') || '员工';
        var snippet = (n.content || '').slice(0, 30);
        var when = (typeof sb2_relativeTime === 'function') ? sb2_relativeTime(n.created_at || 0) : '';
        return '<div class="sb2-dash2-todo-row" data-nid="' + escapeHtml(n.id) + '">'
             +   '<span class="sb2-dash2-todo-row-t" title="' + escapeHtml(n.content || '') + '">' + escapeHtml(empName) + '：' + escapeHtml(snippet) + (when ? ' · ' + escapeHtml(when) : '') + '</span>'
             +   '<span class="sb2-dash2-todo-row-acts"><button type="button" class="sb2-dash2-todo-btn ok" onclick="sb2DashTodoOpenChat(\'' + escapeHtml(n.agent_id) + '\', \'' + escapeHtml(n.id) + '\')">去回复</button></span>'
             + '</div>';
      }).join('');
      html += '<div class="sb2-prop-card sb2-dash2-todo-chat" data-state="pending">'
           +   '<div class="sb2-prop-card-head"><span class="sb2-prop-card-state pending">待回复</span><span style="font-size:11px;color:var(--sb2-t3);">即时通讯</span></div>'
           +   '<div class="sb2-prop-card-title">' + unreadReplies.length + ' 位员工等你回复</div>'
           +   '<div class="sb2-dash2-todo-rows">' + chatRows + '</div>'
           +   (unreadReplies.length > 3 ? '<div class="sb2-dash2-todo-more">还有 ' + (unreadReplies.length - 3) + ' 位在 AI 办公室处理</div>' : '')
           + '</div>';
    }
    el.innerHTML = html;
    if (typeof window.sb2PropMountCards === 'function') window.sb2PropMountCards(el);
  }).catch(function(err){
    var section = document.getElementById('sb2Dash2Todo');
    if (section) section.style.display = 'none';
    console.warn('[sb2_loadDashboardTodo]', err);
  });
}

/* 知识通过: PUT {status:'ok'} (契约同 approveKnowledgeDoc, 见函数头注释不复用原因) */
function sb2DashTodoKbApprove(docId, btn){
  if (!docId || (btn && btn.disabled)) return;
  if (btn) btn.disabled = true;
  apiFetch('/api/knowledge/entries/' + encodeURIComponent(docId), {
    method: 'PUT',
    body: JSON.stringify({ status: 'ok' })
  }).then(function(resp){
    if (resp && resp.ok) {
      if (typeof showToast === 'function') showToast('✓ 已通过审核，条目进入可检索状态', 'success');
      sb2DashTodoRemoveRow(docId);
      if (typeof window.sb2RefreshDashboardBadges === 'function') window.sb2RefreshDashboardBadges();
    } else {
      if (typeof showToast === 'function') showToast('❌ 审核操作失败（HTTP ' + (resp && resp.status) + '）', 'error');
      if (btn) btn.disabled = false;
    }
  }).catch(function(e){
    if (typeof showToast === 'function') showToast('❌ 审核操作失败: ' + (e && e.message ? e.message : e), 'error');
    if (btn) btn.disabled = false;
  });
}
/* 知识驳回: DELETE (软删级联清 chunks, 契约同 rejectKnowledgeDoc) */
function sb2DashTodoKbReject(docId, btn){
  if (!docId || (btn && btn.disabled)) return;
  if (!confirm('确认驳回该条目？驳回后将被删除，不再进入检索。')) return;
  if (btn) btn.disabled = true;
  apiFetch('/api/knowledge/entries/' + encodeURIComponent(docId), { method: 'DELETE' })
    .then(function(resp){
      if (resp && resp.ok) {
        if (typeof showToast === 'function') showToast('✗ 已驳回并删除', 'success');
        sb2DashTodoRemoveRow(docId);
        if (typeof window.sb2RefreshDashboardBadges === 'function') window.sb2RefreshDashboardBadges();
      } else {
        if (typeof showToast === 'function') showToast('❌ 驳回失败（HTTP ' + (resp && resp.status) + '）', 'error');
        if (btn) btn.disabled = false;
      }
    }).catch(function(e){
      if (typeof showToast === 'function') showToast('❌ 驳回失败: ' + (e && e.message ? e.message : e), 'error');
      if (btn) btn.disabled = false;
    });
}
/* 行内移除行 (KB 通过/驳回后); 卡内无行 → 移除卡; 网格无卡 → 收 section */
function sb2DashTodoRemoveRow(docId){
  var row = document.querySelector('#sb2Dash2TodoList .sb2-dash2-todo-row[data-kb-id="' + docId + '"]');
  if (row) row.parentNode.removeChild(row);
  var card = document.querySelector('#sb2Dash2TodoList .sb2-dash2-todo-kb');
  if (card && !card.querySelector('.sb2-dash2-todo-row')) {
    card.parentNode.removeChild(card);
  }
  var el = document.getElementById('sb2Dash2TodoList');
  if (el && el.children.length === 0) {
    var section = document.getElementById('sb2Dash2Todo');
    if (section) section.style.display = 'none';
  }
}
/* 等你回复行移除 (data-nid), 结构同上 (卡类 .sb2-dash2-todo-chat) */
function sb2DashTodoRemoveChatRow(notifId){
  var row = document.querySelector('#sb2Dash2TodoList .sb2-dash2-todo-row[data-nid="' + notifId + '"]');
  if (row) row.parentNode.removeChild(row);
  var card = document.querySelector('#sb2Dash2TodoList .sb2-dash2-todo-chat');
  if (card && !card.querySelector('.sb2-dash2-todo-row')) {
    card.parentNode.removeChild(card);
  }
  var el = document.getElementById('sb2Dash2TodoList');
  if (el && el.children.length === 0) {
    var section = document.getElementById('sb2Dash2Todo');
    if (section) section.style.display = 'none';
  }
}
/* 逾期任务 → 任务模块并直接打开该任务表单 (带 payload 的跳转) */
function sb2DashTodoOpenTask(taskId){
  if (typeof sb2Go === 'function') sb2Go('tasks');
  setTimeout(function(){
    if (typeof sb2TasksOpenForm === 'function') sb2TasksOpenForm(taskId);
  }, 400);
}
/* 去回复 → 通知置已读 (PUT /api/notifications/:id/read, 契约同 inline-07:6490)
   + 跳 AI 办公室打开该员工聊天 (行级照抄 messages 侧栏 onClick 四步链, 23 轮批注③ 修法) */
function sb2DashTodoOpenChat(empId, notifId){
  if (notifId) {
    try {
      apiFetch('/api/notifications/' + encodeURIComponent(notifId) + '/read', { method: 'PUT' })
        .catch(function(){});
    } catch(e){}
    sb2DashTodoRemoveChatRow(notifId);
  }
  if (typeof sb2Go === 'function') sb2Go('messages');
  setTimeout(function(){
    try { localStorage.setItem('sb_current_emp', empId); } catch(e){}
    if (typeof openChat === 'function') { try { openChat(empId); } catch(e){} }
    if (typeof window.renderChatMain === 'function') { try { window.renderChatMain(); } catch(e){} }
    if (typeof window.renderSideFor === 'function') { try { window.renderSideFor('messages'); } catch(e){} }
  }, 400);
}
/* IIFE 作用域墙: onclick 行内调用 + 跨块手动重刷都走 window (SEV1 老教训, 跟 sb2ToggleSide 同款) */
window.sb2DashTodoKbApprove = sb2DashTodoKbApprove;
window.sb2DashTodoKbReject = sb2DashTodoKbReject;
window.sb2DashTodoOpenTask = sb2DashTodoOpenTask;
window.sb2DashTodoOpenChat = sb2DashTodoOpenChat;
window.sb2_loadDashboardTodo = sb2_loadDashboardTodo;

/* ---------- MVP2 三 feed 区加载 (knowledge-events + knowledge-patterns + kb entries) ---------- */
function sb2_loadDashboardFeeds(){
  var tok = localStorage.getItem('sb_auth_token') || '';
  var headers = { 'Authorization': 'Bearer ' + tok };
  var xid = (window.SB2 && SB2.agentId) || localStorage.getItem('sb_agent_id') || '';
  if (xid) headers['X-Agent-Id'] = xid;
  try {
    var mods = JSON.parse(localStorage.getItem('sb_module_perms') || 'null');
    if (mods && Array.isArray(mods)) headers['X-Module-Perms'] = JSON.stringify(mods);
  } catch(e){}

  var eventsP = fetch('/api/knowledge-events?limit=50', { headers: headers }).then(function(r){ return r.json(); }).then(function(d){ return (d && Array.isArray(d.events)) ? d.events : []; }).catch(function(){ return []; });
  /* MVP2 P1 修: 去掉 ?status=active (patterns 表 status 取值是 draft/confirmed/rejected/deprecated, 无 active), 后端过滤会返 0 条.
     改前端过滤 status !== 'rejected' && status !== 'deprecated', 再按 hit_count DESC */
  var patternsP = fetch('/api/knowledge-patterns?limit=50', { headers: headers }).then(function(r){ return r.json(); }).then(function(d){ return (d && Array.isArray(d.patterns)) ? d.patterns : []; }).catch(function(){ return []; });
  /* ★ fix/kb-created-after (老大 23:30 后端小改动): review 卡专属 7 天时间窗下推到 SQL
     之前架构 bug: limit=50 截断后客户端再过滤 created_at (top50 按 updated_at DESC 排序),
     updated_at 被批量 touch 的旧条目 (created 9-30) 永远进不了前 50 → 漏 5 条
     现在: 客户端传 createdAfter=<now-7d>毫秒 + limit=100, 服务端 SQL 加 AND created_at >= ?
     过滤集 = 全量集 (架构性 bug 修复)
     其他卡 (talent/done/promote/agent) 24h 不动, 仍用 knowledge_events 等端点
     apply when: 任何「分页 limit 截断后客户端再过滤」必须后端 SQL 接收过滤条件 */
  var reviewCutoff7d = Date.now() - 7 * 86400 * 1000;
  var kbP = fetch('/api/knowledge/entries?createdAfter=' + reviewCutoff7d + '&limit=100', { headers: headers }).then(function(r){ return r.json(); }).then(function(d){ return (d && Array.isArray(d.docs)) ? d.docs : []; }).catch(function(){ return []; });
  var agentsP = fetch('/api/agents', { headers: headers }).then(function(r){ return r.json(); }).then(function(d){ return Array.isArray(d) ? d : ((d && (d.agents || d.data)) || []); }).catch(function(){ return []; });

  Promise.all([eventsP, patternsP, kbP, agentsP]).then(function(arr){
    var events = arr[0], patterns = arr[1], pendingKb = arr[2], agents = arr[3];
    /* 缓存 agent 名查找表 (MVP2 解析事件 createdBy) */
    SB2.agentNameById = {};
    agents.forEach(function(a){
      if (a && a.id) SB2.agentNameById[a.id] = a.displayName || a.name || a.username || a.id;
    });
    sb2_renderFocus(events, patterns, pendingKb);
    sb2_renderInsights(patterns);
    sb2_renderActivity(events);
  }).catch(function(err){
    console.warn('[sb2_loadDashboardFeeds]', err);
  });
}

/* 今日关注: 4 类卡 (规律晋升 / 达人动态 / 知识待审核 / 员工完成), 紧急度排序
   - 任何类没数据时不渲染该类, 不渲染占位假数据 (老大 22:40 红线)
   - 7 条重复「新达人信号」合并成 1 条「近 24h 新录入达人 N 位 · 待评级」
   - 规律晋升调 /<id>/progress 端点算 next_level, 前端 0 硬编码阈值 */
function sb2_renderFocus(events, patterns, pendingKb){
  var el = document.getElementById('sb2Dash2FocusList');
  if (!el) return;
  var items = [];
  var now = Date.now();
  var cutoff24h = now - 86400 * 1000;

  /* 1) 规律晋升: status=confirmed && verification_level='hypothesis' (未 verified)
        调 GET /api/knowledge-patterns/<id>/progress 算 next_level + hits_remaining
        端点契约 grep: server.py:15434 _handle_get_knowledge_pattern_progress */
  var promoteCandidates = (patterns || []).filter(function(p){
    return p.status === 'confirmed' && p.verification_level === 'hypothesis';
  });
  /* 无晋升候选时不显示卡 (空态不渲染, 避免空骨架占位) */

  /* 2) 达人动态: knowledge_events entity_type='talent' 近 24h
        之前 bug: 7 条重复「新达人信号 · tal_xxx」霸首屏
        现在: 按 entity_id 去重, 合并成 1 条「近 24h 新录入达人 N 位 · 待评级」 */
  var talentIds = {};
  (events || []).forEach(function(ev){
    if (ev.entity_type === 'talent' && (ev.created_at || 0) >= cutoff24h && ev.entity_id){
      talentIds[ev.entity_id] = true;
    }
  });
  var talentCount = Object.keys(talentIds).length;
  if (talentCount > 0){
    items.push({
      kind: 'talent',
      title: '近 24h 新录入达人 ' + talentCount + ' 位 · 待评级',
      sub: '来自 knowledge_events entity_type=talent, 按 entity_id 去重',
      tag: '达人动态',
      tagCls: 'success',
      target: 'influencers'
    });
  }

  /* 3) 知识新入库 (老大 23:17 拍板改名 + 23:30 架构性 bug 修复):
        端点契约: GET /api/knowledge/entries?createdAfter=<now-7d 毫秒>&limit=100
        服务端 SQL 加 AND created_at >= ? 下推时间窗 (后端 fix/kb-created-after 修复)
        客户端只过滤 status (排除 deleted/superseded 兜底, kb_entry_build_where 已默认 status IN ('ok','pending'))
        + category 人话映射
        删本地 created_at 时间过滤 (后端已过滤, 再过滤是重复且会被 limit 截断 bug)
        老大红线: 任何「分页 limit 截断后客户端再过滤」都是架构性 bug,
        现在后端 SQL 接收过滤条件 → 过滤集 = 全量集 */
  /* ★ 老大红线: 不硬编码 status 取值, 从真实数据反查 (kb_entries status ∈ {ok, deleted, superseded}) */
  var BAD_STATUSES = ['deleted', 'superseded'];
  /* category 人话映射表 — 后端契约没翻译, 前端兜底 */
  var CATEGORY_LABEL = {
    'legacy_migration': '历史迁移',
    '达人库': '达人库',
    '公共知识': '公共知识',
    '商品库': '商品库',
    '流量知识': '流量知识'
  };
  function _sb2ReviewCatLabel(raw){
    if (!raw) return '未分类';
    return CATEGORY_LABEL[raw] || raw;
  }
  var catMap = {};
  (pendingKb || []).forEach(function(d){
    var status = d.status || '';
    /* 不再做本地 created_at 时间过滤 — 后端 createdAfter SQL 已下推
       (老大 23:30 拍板架构性 bug: limit 截断后客户端过滤会漏数据, 必须后端 SQL 接收过滤条件)
       ★ apply when: 任何「分页 limit 截断后客户端再过滤」必须后端 SQL 接收过滤条件 */
    if (BAD_STATUSES.indexOf(status) !== -1) return;
    var catLabel = _sb2ReviewCatLabel(d.category);
    catMap[catLabel] = (catMap[catLabel] || 0) + 1;
  });
  var catNames = Object.keys(catMap);
  if (catNames.length > 0){
    var total = catNames.reduce(function(s, c){ return s + catMap[c]; }, 0);
    var sumCat = catNames.map(function(c){ return catMap[c] + ' 条' + c; }).join(' · ');
    items.push({
      kind: 'review',
      title: '近 7 天新入库 ' + total + ' 条知识 · 拆解 ' + catNames.length + ' 类',
      sub: sumCat + ' — 来自 kb_entries (排除 deleted/superseded)',
      tag: '新入库',
      tagCls: 'brand',
      target: 'knowledge'
    });
  }

  /* 4) 员工完成: knowledge_events event_type='analysis' 24h 最新一条
        端点契约: GET /api/knowledge-events
        真实数据: prod tasks 表 status=pending 0 done, 不能从 tasks 取完成事件
        退化: knowledge_events analysis 类型 = 员工完成的「分析报告已入库」事件 */
  var doneEvents = (events || []).filter(function(ev){
    return ev.event_type === 'analysis' && (ev.created_at || 0) >= cutoff24h;
  }).sort(function(a, b){ return (b.created_at || 0) - (a.created_at || 0); });
  if (doneEvents.length > 0){
    var doneEv = doneEvents[0];
    var agentId = doneEv.agent_id || '';
    var agentName = (SB2.agentNameById && SB2.agentNameById[agentId]) || (agentId.replace(/^emp_/, '').slice(0, 6)) || 'AI';
    var doneTitle = doneEv.title || doneEv.content_summary || '分析报告';
    items.push({
      kind: 'agent',
      title: agentName + ' 完成了「' + doneTitle + '」',
      sub: sb2_relativeTime(doneEv.created_at || 0) + ' · 产出 1 条知识已入库',
      tag: '已完成',
      tagCls: 'accent',
      target: 'knowledge'
    });
  }

  /* 5) 员工离线/忙碌 (MVP1 已有) */
  if (typeof SB2.agentsOffline === 'number' && SB2.agentsOffline > 0){
    items.push({
      kind: 'agent',
      title: SB2.agentsOffline + ' 位 AI 员工离线/忙碌',
      sub: '共 ' + SB2.agentsTotal + ' 位, 点击去即时通讯检查',
      tag: '员工',
      tagCls: 'accent',
      target: 'messages'
    });
  }

  /* 晋升候选异步 (避免阻塞 render) — 不在主 cards 里硬塞, 拉到 head 段之后插入 */
  if (promoteCandidates.length > 0){
    /* 取 hit_count 最高的 1 条调 progress, 其它顺序排队 */
    var cand = promoteCandidates.sort(function(a, b){ return (b.hit_count || 0) - (a.hit_count || 0); })[0];
    var tok2 = localStorage.getItem('sb_auth_token') || '';
    var hdrs = { 'Authorization': 'Bearer ' + tok2 };
    var xid = (window.SB2 && SB2.agentId) || localStorage.getItem('sb_agent_id') || '';
    if (xid) hdrs['X-Agent-Id'] = xid;
    fetch('/api/knowledge-patterns/' + encodeURIComponent(cand.id) + '/progress', { headers: hdrs })
      .then(function(r){ return r.ok ? r.json() : null; })
      .then(function(prog){
        if (!prog) return;
        /* 校验 next_level 是当前级别下一阶, 否则不算候选 (避免端点返 next_level=null) */
        if (!prog.next_level || prog.hits_remaining === undefined) return;
        var nextLabel = ({candidate:'待验证', verified:'已验证', proven:'已证明'})[prog.next_level] || prog.next_level;
        var promoteHtml = '<div class="sb2-dash2-focus-item" onclick="sb2Go(\'patterns\')">'
                        + '<div class="sb2-dash2-focus-item-icon kind-promote"><i class=sb2-ico-alert></i></div>'
                        + '<div class="sb2-dash2-focus-item-body">'
                        +   '<div class="sb2-dash2-focus-item-t">规律「' + escapeHtml((cand.pattern_text || '').slice(0, 32)) + '」达到晋升阈值</div>'
                        +   '<div class="sb2-dash2-focus-item-d">evidence ' + (cand.evidence_count || 0) + '/' + prog.hit_required + ' · 已连续命中 ' + (cand.hit_count || 0) + ' 次 · 建议晋升为 ' + escapeHtml(nextLabel) + '</div>'
                        + '</div>'
                        + '<span class="sb2-dash2-focus-item-tag kind-promote">待确认</span>'
                        + '</div>';
        /* 插到 cards 顶部 (晋升最紧急) */
        var listEl = document.getElementById('sb2Dash2FocusList');
        if (listEl) listEl.insertAdjacentHTML('afterbegin', promoteHtml);
      }).catch(function(err){
        console.warn('[sb2_renderFocus] promote progress failed:', err);
      });
  }

  /* 渲染 (晋升卡异步插入, 不阻塞主卡片) */
  if (items.length === 0 && promoteCandidates.length === 0){
    el.innerHTML = '<div class="sb2-dash2-feed-empty"><span class="em">✓</span>今日无紧急关注事项<br/>数据正常, 没有需要立即处理的事项</div>';
    return;
  }
  el.innerHTML = items.slice(0, 8).map(function(it){
    return '<div class="sb2-dash2-focus-item" onclick="sb2Go(\''+it.target+'\')">'
         + '<div class="sb2-dash2-focus-item-icon kind-'+it.kind+'">'+(it.kind==='promote'?'<i class=sb2-ico-alert></i>':it.kind==='review'?'<i class=sb2-ico-copy></i>':it.kind==='talent'?'<i class=sb2-ico-user></i>':'<i class=sb2-ico-zap></i>')+'</div>'
         + '<div class="sb2-dash2-focus-item-body">'
         +   '<div class="sb2-dash2-focus-item-t">'+escapeHtml(it.title)+'</div>'
         +   '<div class="sb2-dash2-focus-item-d">'+escapeHtml(it.sub)+'</div>'
         + '</div>'
         + '<span class="sb2-dash2-focus-item-tag kind-'+it.kind+'">'+escapeHtml(it.tag)+'</span>'
         + '</div>';
  }).join('');
}

/* 规律洞察: 单条主推 (verified 优先) + 2 条副推 */
function sb2_renderInsights(patterns){
  var el = document.getElementById('sb2Dash2InsightList');
  if (!el) return;
  /* MVP2 P1 修: patterns 表 status 取值是 draft/confirmed/rejected/deprecated, 无 active.
     前端过滤排除 rejected/deprecated, 按 hit_count DESC */
  var arr = (patterns || []).filter(function(p){
    var s = p.status || '';
    return s !== 'rejected' && s !== 'deprecated';
  });
  if (arr.length === 0){
    el.innerHTML = '<div class="sb2-dash2-feed-empty"><span class="em"><i class=sb2-ico-ruler></i></span>暂无活跃规律</div>';
    return;
  }
  arr.sort(function(a, b){
    var ah = Number(a.hit_count) || 0, bh = Number(b.hit_count) || 0;
    return bh - ah;
  });
  var main = arr[0];
  var subs = arr.slice(1, 3);
  var mainHtml = '<div class="sb2-dash2-insight-main" onclick="sb2Go(\'patterns\')">'
                + '<div class="sb2-dash2-insight-main-cat">'+escapeHtml((main.category || '规律'))+'</div>'
                + '<div class="sb2-dash2-insight-main-text">'+escapeHtml((main.pattern_text || '').slice(0, 80))+'</div>'
                + '<div class="sb2-dash2-insight-main-meta">'
                +   '<span><b>'+(main.confidence_score != null ? main.confidence_score.toFixed(1) : '-')+'%</b> 置信</span>'
                +   '<span>证据 <b>'+(main.evidence_count || 0)+'</b></span>'
                +   '<span>命中 <b>'+(main.hit_count || 0)+'</b> 次</span>'
                +   '<span>'+(main.verification_level === 'verified' ? '✓ 已验证' : '○ ' + ({hypothesis:'假设',candidate:'候选',verified:'已验证',proven:'成熟'}[main.verification_level] || '假设'))+'</span>'
                + '</div>'
                + '</div>';
  var subsHtml = subs.map(function(p){
    return '<div class="sb2-dash2-insight-sub" onclick="sb2Go(\'patterns\')">'
         + '<div class="sb2-dash2-insight-sub-text">'+escapeHtml((p.pattern_text || '').slice(0, 60))+'</div>'
         + '<div class="sb2-dash2-insight-sub-conf">'+(p.confidence_score != null ? p.confidence_score.toFixed(1) : '-')+'%</div>'
         + '</div>';
  }).join('');
  el.innerHTML = mainHtml + subsHtml;
}

/* 员工动态: knowledge_events / tasks 完成事件, join agents 真实姓名 + 上限 6 条
   - 之前 bug: 8 条全是「AI 在 talent 上产生 1 条事件」(无主体无动作, 占位噪音)
   - 现在: 头像首字 + agent_name(join agents.json) + 具体动作 (entity_type + event_type 中文映射)
   - 数据源: knowledge_events, entity_id 取前缀 8 位, 不出现「tal_xxx」原始 ID
   - 「全部 →」 link 在 section-head (本函数不重复渲染) */
function sb2_renderActivity(events){
  var el = document.getElementById('sb2Dash2ActivityList');
  if (!el) return;
  /* 24h cutoff 过滤 */
  var now = Date.now();
  var cutoff24h = now - 86400 * 1000;
  var arr = (events || []).filter(function(ev){
    return (ev.created_at || 0) >= cutoff24h;
  }).sort(function(a, b){ return (b.created_at || 0) - (a.created_at || 0); });
  /* 按 entity_id 去重 (同实体多条合并成 1 条最新), 保持多样性 */
  var seen = {};
  var deduped = [];
  for (var i = 0; i < arr.length; i++){
    var ev = arr[i];
    var key = (ev.entity_type || '') + ':' + (ev.entity_id || ev.id || '');
    if (seen[key]) continue;
    seen[key] = true;
    deduped.push(ev);
    if (deduped.length >= 6) break;
  }
  if (deduped.length === 0){
    el.innerHTML = '<div class="sb2-dash2-feed-empty"><span class="em">⏱</span>24 小时内暂无动态</div>';
    return;
  }
  el.innerHTML = deduped.map(function(ev){
    var agentId = ev.agent_id || ev.created_by || '';
    var agentName = (SB2.agentNameById && SB2.agentNameById[agentId]) || agentId.replace(/^emp_/, '').slice(0, 6) || 'AI';
    /* 〔fix/sb2-side-restore commit 6 B2〕真头像: 查 window.emps[].avatar 索引 AVATAR_PRESETS
       agents.json avatar 是 int (0-23) → AVATAR_PRESETS[avatar] 是 PNG URL
       老 renderAvatar 函数 (line 20378) 是 prod 既有逻辑, 这里内联避免跨块
       fallback 字母头像 (color hash 配色, office-v3 / 既有 renderAvatar 同款) */
    var avatarHtml = _sb2RenderAgentAvatar(agentId, agentName);
    /* 动作描述: 中文映射 event_type + entity_type 简化 */
    var etMap = {
      analysis: '完成分析',
      vision_data: '采集视觉数据',
      promotion: '推进合作',
      feedback: '提交反馈',
      review: '审核通过',
      publish: '发布'
    };
    var entLabel = ({
      talent: '达人',
      product: '商品',
      pattern: '规律',
      knowledge: '知识',
      task: '任务'
    })[ev.entity_type] || (ev.entity_type || '');
    var evLabel = etMap[ev.event_type] || ('处理' + entLabel);
    /* entity_id 前 8 位简化 (避免 tal_xxx 原始 ID 噪音) */
    var entShort = (ev.entity_id || '').slice(0, 8) || '';
    var action = entLabel + ' ' + entShort + ' · ' + evLabel;
    var t = ev.created_at || 0;
    var ago = sb2_relativeTime(t);
    return '<div class="sb2-dash2-activity-item">'
         + '<div class="sb2-dash2-activity-avatar">'+avatarHtml+'</div>'
         + '<div class="sb2-dash2-activity-body">'
         +   '<div class="sb2-dash2-activity-line"><b>'+escapeHtml(agentName)+'</b> '+escapeHtml(action)+'</div>'
         + '</div>'
         + '<div class="sb2-dash2-activity-time">'+ago+'</div>'
         + '</div>';
  }).join('');
}

/* 〔fix/sb2-side-restore commit 9〕员工头像 helper (12 轮热修升级)
   优先级:
   1) window.emps[].avatar 是 int → AVATAR_PRESETS[avatar] (PNG 真头像, agents.json avatar 0-23)
   2) (新) 没 int 但 AVATAR_PRESETS 在 → 按 id/name hash % length 落不同 preset
      - 修前: ev.agent_id 不在 emps 里或 emps[].avatar 不是数字, 一律 fallback 字母色块,
        全员同色块 → 老大截图标注② 全员同一张图 (色块同字同色)
      - 现在: 缺数据时按 hash 落不同 preset, 全员不同图 (真分散到 24 个 preset)
   3) (老 fallback) AVATAR_PRESETS 数组不在 → 字母 hash 色块 (.sb2-dash2-activity-letter)
   - 全 --sb2-* token + rgba 透明 (COLORS 数组内 hex 老规矩, office-v3 同款)
   - 返回 HTML 字符串, 父容器 .sb2-dash2-activity-avatar 28px 圆裁切 */
function _sb2RenderAgentAvatar(agentId, agentName){
  var empAvatar = null;
  if (typeof window !== 'undefined' && Array.isArray(window.emps)){
    for (var i = 0; i < window.emps.length; i++){
      if (window.emps[i] && window.emps[i].id === agentId){ empAvatar = window.emps[i].avatar; break; }
    }
  }
  var presets = (typeof AVATAR_PRESETS !== 'undefined' && Array.isArray(AVATAR_PRESETS)) ? AVATAR_PRESETS : null;
  /* 优先级 1: int 索引 (agents.json 8 emps avatar = 0/4/6/7/17/19/21/23, 全唯一) */
  if (typeof empAvatar === 'number' && presets && presets[empAvatar]){
    return '<img src="'+escapeAttr(presets[empAvatar])+'" class="sb2-dash2-activity-img" alt="">';
  }
  /* 优先级 2: 按 id/name hash 落不同 preset (新, 修前 bug: 全员同色块) */
  if (presets && presets.length > 0){
    var key = (agentId || agentName || 'AI');
    var hash = 0;
    for (var c = 0; c < key.length; c++){ hash = (hash << 5) - hash + key.charCodeAt(c); hash = hash & hash; }
    var idx = Math.abs(hash) % presets.length;
    return '<img src="'+escapeAttr(presets[idx])+'" class="sb2-dash2-activity-img" alt="">';
  }
  /* 优先级 3: 字母 hash 色块 (AVATAR_PRESETS 数组不在的极端 fallback) */
  var name = agentName || agentId || 'AI';
  var letter = escapeHtml(name.charAt(0).toUpperCase());
  var hash2 = 0;
  for (var c2 = 0; c2 < name.length; c2++){ hash2 = (hash2 << 5) - hash2 + name.charCodeAt(c2); hash2 = hash2 & hash2; }
  var COLORS = ['#FF6B35', '#FF9500', '#34C759', '#1677ff', '#5856D6', '#AF52DE', '#FF2D55'];
  var bg = COLORS[Math.abs(hash2) % COLORS.length];
  return '<div class="sb2-dash2-activity-letter" style="background:'+bg+';">'+letter+'</div>';
}

/* 相对时间 (毫秒 → 「3 小时前」) */
function sb2_relativeTime(ms){
  if (!ms) return '-';
  var now = Date.now();
  var diff = Math.max(0, now - ms);
  if (diff < 60 * 1000) return '刚刚';
  if (diff < 3600 * 1000) return Math.floor(diff / 60000) + ' 分钟前';
  if (diff < 86400 * 1000) return Math.floor(diff / 3600000) + ' 小时前';
  if (diff < 7 * 86400 * 1000) return Math.floor(diff / 86400000) + ' 天前';
  var d = new Date(ms);
  return (d.getMonth()+1) + '/' + d.getDate();
}

function escapeHtml(s){
  if (s == null) return '';
  return String(s).replace(/[&<>"']/g, function(c){
    return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c];
  });
}

/* ---------- 显隐控制 ---------- */
function showDash(){
  // ★ P1 fix: showDash 走独立分支 (不经过 switchModule wrapper), 不调 sb2_hideAllModuleMains
  // → 兄弟容器 .sb2-knowledge-main / .sb2-settings-main / .sb2-patterns-main / .sb2-talents-main 的 .active 残留
  // 现在被覆盖层视觉盖住无感, 但违反「任意时刻恰好 1 个 main 可见」红线,
  // 覆盖层一旦意外熄灭 (SEV2 那类 bug 再来一次) 残留屏会露出来.
  // 亮工作台前先清兄弟容器, 跟 switchModule wrapper 第一步调 sb2_hideAllModuleMains 同模式.
  if (typeof sb2_hideAllModuleMains === 'function') sb2_hideAllModuleMains();
  buildDashboard();
  SB2.dashVisible = true;
  document.getElementById('sb2Dash').classList.add('on');
  var app = document.querySelector('.app-container');
  if (app) app.style.visibility = 'hidden';
  setActive('dashboard');
  SB2_CURRENT_MODULE = 'dashboard';
  /* 〔fix/sb2-side-restore commit 4〕同步挂 window (跨块访问)
     shell 块 IIFE 内赋值, 达人块访问不到 → window.* 同步 */
  window.SB2_CURRENT_MODULE = SB2_CURRENT_MODULE;
  updateCrumb();
  loadKpis();
  // ★ fix/sb2-side-restore commit 2: 异步拉侧栏 badge 数据 (proposals/kbi3)
  //   fire-and-forget, 不阻塞主流程, 拉到后 sb2RefreshDashboardBadges 内部自重渲
  if (typeof sb2RefreshDashboardBadges === 'function') sb2RefreshDashboardBadges();
}
function hideDash(){
  if (!SB2.dashVisible) return;
  SB2.dashVisible = false;
  var dash = document.getElementById('sb2Dash');
  if (dash) dash.classList.remove('on');
  var app = document.querySelector('.app-container');
  if (app) app.style.visibility = '';
}
function setActive(id){
  document.querySelectorAll('.sb2-rail-btn[data-sb2]').forEach(function(b){
    b.classList.toggle('active', b.getAttribute('data-sb2') === id);
  });
}

/* ---------- 全局导出入口 ---------- */
/* 〔29 轮视口守卫 2026-10-06〕innerHeight > outerHeight = 宿主窗口物理装不下布局视口,
   页面底部被裁剪 (老大实测消息输入框不可见)。几何不合理时把壳高锚到 outerHeight。
   resize 监听 (宿主窗口尺寸变化时重判) + 启动即跑一次 */
window.sb2ViewportGuard = function(){
  try {
    var oh = window.outerHeight, ih = window.innerHeight;
    var on = !!(oh && oh > 400 && oh < ih);
    if (document.body) {
      document.body.classList.toggle('sb2-vp-guard', on);
      if (on) document.body.style.setProperty('--sb2-vh', oh + 'px');
      else document.body.style.removeProperty('--sb2-vh');
    }
  } catch(e){}
};
window.addEventListener('resize', window.sb2ViewportGuard);
window.addEventListener('load', window.sb2ViewportGuard);
window.sb2ViewportGuard();
/* 外层宿主 webview 时机坑: 脚本首轮执行时 window.outerHeight 可能还是 0 (实测 r39 刷新后轮询读 0,
   上一轮 883), 无 resize 事件补发 → 守卫永远不触发。延迟补判两轮兜底 */
setTimeout(window.sb2ViewportGuard, 800);
setTimeout(window.sb2ViewportGuard, 3000);
window.sb2Go = function(id){
  if (id === 'dashboard') { showDash(); renderSideFor('dashboard'); updateCrumb(); return; }
  hideDash();
  renderSideFor(id);
  updateCrumb();
  var m = null;
  MODULES.forEach(function(x){ if (x.id === id) m = x; });
  if (m && m.fn && typeof window[m.fn] === 'function') { setActive(id); window[m.fn](); return; }
  if (typeof switchModule === 'function') switchModule(id);
  else setActive(id);
};

/* ---------- 桥接旧函数 ---------- */
// ★ P1 fix: 模块主容器互斥 — 各模块 hook 只「显示自己」, 没人 hide 兄弟容器
// 之前: 切到聊天时 .sb2-knowledge-main.active 不被 remove → 聊天底下压知识库 hero
// 现在: switchModule wrapper 第一步统一 hide 所有 sb2 main, 各 hook 随后显示自己
// 容器清单 (4 个, prod 已 3 + 预写 1):
//   - #sb2KnowledgeMain  → .active class 控制
//   - #sb2SettingsMain   → .active class 控制
//   - #sb2ChatMain       → hidden 属性控制 (chat IIFE 内部 set hidden)
//   - .sb2-patterns-main → .active class 控制 (prod 还未部署, 预写)
// ★ 撤回: dashboard (.sb2-dash-main) 是覆盖层 #sb2Dash.sb2-dash 的子容器
//   父级默认 display:none / .on 才 display:flex (line 10057/10061), 完全管控
//   且 buildDashboard() 早退 (line 41305: if #sb2Dash exists return) → 只建一次
//   没人重置 .sb2-dash-main 内联 display → 内联 hide 会导致切走再切回永久空白
//   之前误判「无 display:none 规则 = 永远可见」是错的, 覆盖层子容器不归这里管
// 通用策略: .active 走 removeClass, chat 走 hidden 属性, dashboard 不动
window.sb2_hideAllModuleMains = function(){
  // 1. .active class 控制容器 (knowledge/settings/patterns/talents/tasks)
  document.querySelectorAll('.sb2-knowledge-main.active, .sb2-settings-main.active, .sb2-patterns-main.active, .sb2-talents-main.active, .sb2-tasks-main.active, .sb2-products-main.active, .sb2-groups-main.active')
    .forEach(function(el){ el.classList.remove('active'); });
  // 2. hidden 属性控制容器 (chat)
  var chatEl = document.getElementById('sb2ChatMain');
  if (chatEl) chatEl.setAttribute('hidden', '');
  // 3. dashboard 不动 (覆盖层 #sb2Dash 父级管控, 见上面撤回注释)
}

// switchModule 包装: 旧代码任何入口(壳/旧UI/快捷面板)切模块都同步壳状态
var _sb2OrigSwitch = window.switchModule;
if (typeof _sb2OrigSwitch === 'function') {
  window.switchModule = function(module){
    // ★ P1 fix 第一步: hide 所有 sb2 main 兄弟容器, 必须在 _sb2OrigSwitch (原版) 之前
    sb2_hideAllModuleMains();
    var r = _sb2OrigSwitch.apply(this, arguments);
    hideDash();
    var cur = typeof currentModule !== 'undefined' ? currentModule : module;
    setActive(cur);
    renderSideFor(cur);
    updateCrumb();
    return r;
  };
}
// 登录后默认落工作台
var _sb2OrigShowMain = window.showMainApp;
if (typeof _sb2OrigShowMain === 'function') {
  window.showMainApp = function(){
    var r = _sb2OrigShowMain.apply(this, arguments);
    setTimeout(function(){
      // currentUser 此时已加载, 重建 rail 把所有模块亮出来
      var oldRail = document.getElementById('sb2Rail');
      if (oldRail) oldRail.remove();
      buildRail();
      // ★ SEV2 守卫: showMainApp 会被多次重入(WS 重连/token 刷新等),
      //   无条件 showDash 会把工作台覆盖层盖回用户当前模块 (2026-10-04 老大实测:
      //   切到聊天后覆盖层重新点亮, currentModule=messages 却看到工作台)
      if (window._sb2Booted) return;
      window._sb2Booted = true;
      showDash();
      renderSideFor('dashboard');
      updateCrumb();
    }, 200);
    return r;
  };
}

/* ---------- 顶栏 (新全局) — 通知徽章同步旧 #notificationBadge ---------- */
function sb2BellSync(){
  var old = document.getElementById('notificationBadge');
  var el = document.getElementById('sb2TopbarBellBadge');
  if (!el) return;
  var n = 0;
  if (old) {
    var dc = old.getAttribute('data-count');
    if (dc) n = parseInt(dc, 10) || 0;
    else n = parseInt((old.textContent || '0').trim(), 10) || 0;
  }
  el.style.display = n > 0 ? '' : 'none';
  el.textContent = n > 99 ? '99+' : String(n);
}

/* ---------- #side 上下文侧栏 (新全局) — 按模块动态渲染 ---------- */

/* 〔fix/sb2-side-restore commit 6 B1〕2026 法定节假日表 (日期以国务院放假安排为准)
   元旦 1.1 / 春节 2.17-2.23 / 清明 4.4-4.6 / 劳动节 5.1-5.5 / 端午 6.19-6.21 / 中秋 9.25-9.27 / 国庆 10.1-10.7
   命中当天返节日后缀 (含第 N 天), 非假期返空字符串 */
var _SB2_HOLIDAYS_2026 = [
  { name:'元旦', start:'2026-01-01', end:'2026-01-01' },
  { name:'春节', start:'2026-02-17', end:'2026-02-23' },
  { name:'清明', start:'2026-04-04', end:'2026-04-06' },
  { name:'劳动节', start:'2026-05-01', end:'2026-05-05' },
  { name:'端午', start:'2026-06-19', end:'2026-06-21' },
  { name:'中秋', start:'2026-09-25', end:'2026-09-27' },
  { name:'国庆节', start:'2026-10-01', end:'2026-10-07' }
];
function _sb2GetHolidayLabel(d){
  var y = d.getFullYear(), m = d.getMonth() + 1, day = d.getDate();
  var pad = function(n) { return n < 10 ? '0' + n : '' + n; };
  var today = y + '-' + pad(m) + '-' + pad(day);
  for (var i = 0; i < _SB2_HOLIDAYS_2026.length; i++){
    var h = _SB2_HOLIDAYS_2026[i];
    if (today >= h.start && today <= h.end){
      var dayNum = Math.floor((Date.UTC(y, m - 1, day) - Date.UTC(parseInt(h.start.slice(0,4)), parseInt(h.start.slice(5,7)) - 1, parseInt(h.start.slice(8,10)))) / 86400000) + 1;
      return h.name + ' 第' + dayNum + '天';
    }
  }
  return '';
}
var SB2_SIDE_RENDERERS = {
  /* ★ 待办 A 2026-10-08: 项目组侧栏 — 群列表, 点项进 sb2GroupsOpenChat (守卫失败 toast) */
  groups: function(){
    var groups = [];
    try { groups = (typeof window.projects !== 'undefined' && Array.isArray(window.projects)) ? window.projects : []; } catch(e){}
    return {
      title:'项目组', sub: groups.length ? groups.length+' 个群组' : '按项目组织的群聊',
      items:[
        { label:'我的群组 ('+groups.length+')', items: groups.length ? groups.slice(0, 20).map(function(g){
          return { name: (g.emoji ? g.emoji+' ' : '') + (g.name || g.display_name || '-'),
                   role: ((g.members || []).length) + ' 人',
                   onClick:"(typeof sb2GroupsOpenChat==='function' ? sb2GroupsOpenChat('"+(g.id||'')+"') : (typeof showToast==='function' ? showToast('群聊打开失败') : null))" };
        }) : [{ name:'暂无群组', onClick:"(typeof sb2GroupsInit==='function' ? sb2GroupsInit() : null)" }] },
        { label:'快捷入口', items:[
          { name:'<i class=sb2-ico-chart></i> 创建群组',
            onClick:"(typeof openGroupWizard==='function' ? openGroupWizard() : (typeof showToast==='function' ? showToast('创建群组 — 后续版本开放') : null))" }
        ]}
      ],
      searchInput:'搜索群组…'
    };
  },
  dashboard: function(){
    /* 〔fix/sb2-side-restore commit 2〕工作台侧栏还原原型 (line 678-687)
       - 快捷入口: 今日概览 (active) / 紧急待办 [badge=_sb2PendingProposals] / 数据看板 (toast) / 审核队列 [badge=_sb2PendingKbReviews]
       - 我的项目组: window.projects 真数据 (照抄 messages renderer 模式)
       - badge 兜底 0 = 不显示 (老大硬指令)
       - 删: 原 6 个 quick 按钮 (即时通讯/龙虾办公室/知识库/规律库/达人库/任务), 已被模块轨替代 */
    var pending = (typeof window._sb2PendingProposals === 'number') ? window._sb2PendingProposals : 0;
    var kbPending = (typeof window._sb2PendingKbReviews === 'number') ? window._sb2PendingKbReviews : 0;
    var groups = (typeof window.projects !== 'undefined' && Array.isArray(window.projects)) ? window.projects : [];
    return {
      title:'工作台', sub:'每天先看这里',
      items:[
        { label:'快捷入口', items:[
          { name:'<i class=sb2-ico-chart></i> 今日概览', active: true },
          { name:'<i class=sb2-ico-flame></i> 紧急待办', badge: pending > 0 ? String(pending) : '',
            onClick:"sb2Go('messages')" },
          { name:'<i class=sb2-ico-trend></i> 数据看板',
            onClick:"(typeof sb2SideScrollKpi==='function' ? sb2SideScrollKpi() : (typeof showToast==='function' ? showToast('数据看板 — 后续版本开放') : null))" },
          { name:'<i class=sb2-ico-receipt></i> 审核队列', badge: kbPending > 0 ? String(kbPending) : '',
            onClick:"sb2Go('knowledge')" }
        ]},
        { label:'我的项目组', items: groups.length ? groups.slice(0, 10).map(function(g){
          return { name: (g.name || g.display_name || '-'),
                   onClick:"(typeof sb2GroupsOpenChat==='function' ? sb2GroupsOpenChat('"+(g.id||'')+"') : (typeof showToast==='function' ? showToast('项目组群聊 — 后续版本开放') : null))" };
        }) : [{ name:'暂无项目组' }] }
      ]
    };
  },
  messages: function(){
    /* 〔fix/sb2-side-restore commit 10〕title '即时通讯' → 'AI 办公室' (13 轮热修)
       - 老大 16:04 拍板口径修正: 模块名跟主区解耦, 'AI 办公室' 作为 rail 标识符表示
         "员工对话总入口" 定位, 主区是 chat UI 实现这个定位
       - 侧栏保留: AI 员工列表 + 项目组群聊 (跟原版一致)
       - 子项 onClick 全部用守卫写法 (&& fn), 防跨块 undefined 静默 */
    var list = (typeof window.emps !== 'undefined' && Array.isArray(window.emps)) ? window.emps : [];
    var groups = [];
    try { groups = (typeof window.projects !== 'undefined' && Array.isArray(window.projects)) ? window.projects : []; } catch(e){}
    var onlineCount = 0;
    list.forEach(function(e){ if (e && e.status === 'online') onlineCount++; });
    var items = [
      { label:'AI 员工 ('+list.length+' · '+onlineCount+' 在线)', items:list.slice(0, 20).map(function(e){
        var isOnline = e.status === 'online';
        var isBusy = e.status === 'busy';
        var initials = (e.display_name || e.name || '?').slice(0,1);
        /* 〔21 轮批注①-1〕messages 侧栏员工项补 role + dot 三色 (online=绿/busy=橙/offline=灰)
           dot 模板支持 boolean 兼容 (true=online/绿, false=offline/灰) + 显式 'busy' 三色 */
        var dotState = isBusy ? 'busy' : (isOnline ? 'online' : 'offline');
        var roleDisplay = (typeof getEmpRoleDisplay === 'function') ? getEmpRoleDisplay(e) : (e.role || '');
        /* 〔23 轮批注③〕修 startNewChat shim 丢 empId 真 bug (line 32613 function startNewChat() 无参 → 完整 no-op)
           修前: onClick:"startNewChat && startNewChat('"+e.id+"')" → startNewChat 丢参, 啥也不发生
           修法: IIFE 走真链路
             1. localStorage.setItem('sb_current_emp', id) — 写选中态 (active 字段同帧可读)
             2. openChat(id) — 真源 (内部已写 sb_current_emp + 清群聊态 + legacy DOM 同步)
             3. window.renderChatMain() — 重读 sb_current_emp → 刷 topbar + 重载历史 (line 47623 已挂 window)
             4. window.renderSideFor('messages') — 重渲侧栏 → active 高亮落到新选中项 (line 47047 已挂 window)
           全部 try/catch 守卫, 单步失败不阻塞其他步 (规格 v6 修法 2 行级照抄)
           〔23 轮批注③ 修法 3〕active 字段: 读 localStorage 真值, 不造 — 与批注② .active 白底墨色联动 */
        var _curEmp = (function(){ try { return localStorage.getItem('sb_current_emp') || ''; } catch(e) { return ''; } })();
        return {
          name: (e.display_name || e.name || '-'),
          dot: dotState,
          role: roleDisplay,
          active: (_curEmp === e.id),
          onClick: "(function(id){"
                 + "if(typeof sb2ExitGroupChat==='function'){try{sb2ExitGroupChat();}catch(e){}}"
                 + "try{localStorage.setItem('sb_current_emp',id);}catch(e){}"
                 + "if(typeof openChat==='function'){try{openChat(id);}catch(e){}}"
                 + "if(typeof window.renderChatMain==='function'){try{window.renderChatMain();}catch(e){}}"
                 + "if(typeof window.renderSideFor==='function'){try{window.renderSideFor('messages');}catch(e){}}"
                 + "})('"+e.id+"')"
        };
      })}
    ];
    if (groups.length) {
      items.push({ label:'项目组群聊 ('+groups.length+')', items:groups.slice(0, 10).map(function(g){
        return { name: (g.name || g.display_name || '-'), onClick:"(typeof sb2GroupsOpenChat==='function' ? sb2GroupsOpenChat('"+g.id+"') : (typeof showToast==='function' ? showToast('打开群聊失败') : null))" };
      })});
    }
    return { title:'AI 办公室', sub: list.length+' 员工 · '+onlineCount+' 在线', items:items, searchInput:'搜索员工…' };
  },
  knowledge: function(){
    /* 〔fix/sb2-side-restore commit 16+17〕16 轮批注③打回链修正 (真数据链路):
       — commit 16: 老大 18:15 trace 实测证据:
         * _knowledgeData.categories = 0 / _knowledgeInitialized = false — loadKnowledge() 在 SB2 界面根本不会跑
         * SB2 知识库模块用的是 sb2KbInit() (line 40305), 数据落在 _sb2KbCats / _sb2KbEntries
         * 我接的 _knowledgeData 是 legacy 老界面数据路径, SB2 路径下永远空 → 侧栏永远「加载中…」
         正确接法 (老大 18:15 拍板):
         1. 分类树读 _sb2KbCats (sb2 loader 真路径)
         2. 分类计数: server 端没有 /api/knowledge/categories/counts 端点, 改用 stats 接口 byCategory 数组
            — 跟 sb2KbRenderHero 同 statsData (不另发请求)
         3. 时序重渲挂在 sb2KbInit 三个 render 调用后, 不是 loadKnowledge (loadKnowledge 死路径)
         4. 移除 loadKnowledge 的 legacy 钩子 (commit 16 第 5 项)
       — commit 17: 老大 18:25 trace 实测证据:
         * GET /api/knowledge/stats 实际只返 {total, byScope, byCategory, pendingChunks}
           — 无 total_entries / total_chunks 字段! 我 commit 16 凭印象写的字段名错
         * 文档数: stats.total (server 真字段, 不是 total_entries)
         * chunks 数: stats.totalChunks (server 真字段, commit 17 加的, 不是 total_chunks)
         修法 (server + client 跨端一 commit):
         - server: kb_entry_stats return dict 加 totalChunks (子查询复用 sql_where + params, 纯新增字段, 跨端兼容)
         - client 三处: sb2KbInit / sb2KbRenderHero / SB2_SIDE_RENDERERS.knowledge 改读真字段名
       真实数据链路 (commit 16+17 整链路 trace):
         sb2KbInit (line 40305)
           ↓ apiFetch('/api/knowledge/categories') → _sb2KbCats
           ↓ apiFetch('/api/knowledge/entries?limit=50') → _sb2KbEntries
           ↓ apiFetch('/api/knowledge/stats') → statsData {total, totalChunks, byScope, byCategory, pendingChunks}
         sb2KbInit 末尾:
           → window._sb2KbCatCounts = byCategory Map (name→count)
           → window._sb2KbCatCountsTotal = stats.total
           → window._sb2KbStats = {byCategory, total, totalChunks}
           → 三个 render 调用
           → renderSideFor('knowledge') 重渲侧栏
         SB2_SIDE_RENDERERS.knowledge:
           → 读 _sb2KbCats 真树状
           → 读 _sb2KbCatCounts 分类计数
           → 读 _sb2KbStats.total + totalChunks 副标
       跟 legacy 路径彻底切断, 不互相污染
       字段名铁律第 3 条: 不看字段 = 不写代码 (老大原话, 跨项目稳定偏好) */
    var catsTree = (Array.isArray(window._sb2KbCats)) ? window._sb2KbCats : [];
    var counts = (window._sb2KbCatCounts && typeof window._sb2KbCatCounts === 'object') ? window._sb2KbCatCounts : null;
    var totalDocs = (typeof window._sb2KbCatCountsTotal === 'number') ? window._sb2KbCatCountsTotal
      : (window._sb2KbEntries ? window._sb2KbEntries.length : 0);
    /* chunks 总数: _sb2KbStats.totalChunks (server kb_entry_stats 真字段, commit 17 加的)
       — 修前读 total_chunks (我 commit 16 凭印象写的字段名), server 实际无此字段 → 永远 0
       — 修后读 totalChunks (server 真字段, 跨端兼容老客户端) */
    var chunksTotal = (window._sb2KbStats && typeof window._sb2KbStats.totalChunks === 'number')
      ? window._sb2KbStats.totalChunks : 0;
    /* 副标: 'N 条 · M chunks' (原型 line 700 '287 条 · 531 chunks'), 真数据兜底 0 不编造
       没数据时显「加载中…」, 不写「0 分类 · 全局可见」(那是修前假数据兜底) */
    var dash = '–';
    var sub;
    if (catsTree.length === 0 && totalDocs === 0) {
      sub = '加载中…';
    } else if (totalDocs > 0 || chunksTotal > 0) {
      sub = totalDocs + ' 条 · ' + chunksTotal + ' 片段';
    } else {
      sub = catsTree.length + ' 分类';
    }
    /* flatten 树状 (递归 children, 保留层级缩进前缀 '— ' 给子分类) */
    var flatCats = [];
    (function walk(arr, depth){
      (arr || []).forEach(function(c){
        flatCats.push({ id:c.id, name:(c.name || c.display_name || '未分类'), depth:depth });
        if (Array.isArray(c.children) && c.children.length) walk(c.children, depth + 1);
      });
    })(catsTree, 0);
    /* 分类组: 首项「全部条目」 + 各分类 (扁平树 + 计数 + 兜底 dash)
       byCategory name 跟 kb_category_tree 返回的 name 是字符串 (sb2kb cats list 用字符串 id 但 byCategory 也用 name 作 key),
       — counts map 的 key 是 byCategory name (字符串), 不是数字 id, 因为 byCategory 项无 id 字段
       — 如果 byCategory 项用 catId 字符串, 用 counts[String(c.id)] 也兼容 */
    var catItems = [
      { name:'全部条目', badge: totalDocs > 0 ? String(totalDocs) : dash, active:false, dot:true,
        onClick:"switchKnowledgeCategory && switchKnowledgeCategory('all')" }
    ];
    flatCats.slice(0, 30).forEach(function(c){
      var cnt = null;
      if (counts) {
        if (typeof counts[c.name] === 'number') cnt = counts[c.name];
        else if (typeof counts[String(c.id)] === 'number') cnt = counts[String(c.id)];
      }
      var prefix = c.depth > 0 ? new Array(c.depth + 1).join('— ') : '';
      catItems.push({
        name: prefix + c.name,
        badge: cnt !== null ? String(cnt) : dash,
        active:false,
        dot:true,
        onClick:"switchKnowledgeCategory && switchKnowledgeCategory('"+(c.id || '')+"')"
      });
    });
    return {
      title:'知识库', sub:sub,
      searchInput:'语义搜索…',
      items:[
        { label:'分类', items: catItems },
        { label:'范围', items:[
          { name:'全员可读',  onClick:"switchKnowledgeScope && switchKnowledgeScope('global')" },
          { name:'我创建的',  onClick:"switchKnowledgeScope && switchKnowledgeScope('personal')" },
          { name:'项目组共享', onClick:"switchKnowledgeScope && switchKnowledgeScope('group')" }
        ]},
        { label:' ', items:[
          { name:'<i class=sb2-ico-ruler></i> 规律库入口 →', onClick:"sb2Go && sb2Go('patterns')" }
        ]}
      ]
    };
  },
  patterns: function(){
    var cats = (typeof window.patternCategories !== 'undefined' && Array.isArray(window.patternCategories)) ? window.patternCategories : [];
    return {
      title:'规律库', sub:cats.length + ' 类目 · 跨域共性',
      items:[
        { label:'类目', items: (cats.length ? cats : [{name:'加载中…'}]).slice(0, 30).map(function(c){
          return { name:(c.name || c.display_name || '-'), onClick:"switchPatternsCategory && switchPatternsCategory('"+(c.id || c.name)+"')" };
        })},
        { label:'状态', items:[
          { name:'全部',     onClick:"switchPtnSideStatus && switchPtnSideStatus('')" },
          { name:'待确认',   onClick:"switchPtnSideStatus && switchPtnSideStatus('draft')" },
          { name:'已确认',   onClick:"switchPtnSideStatus && switchPtnSideStatus('confirmed')" },
          { name:'已废弃',   onClick:"switchPtnSideStatus && switchPtnSideStatus('deprecated')" }
        ]},
        /* 〔27 轮批注① 2026-10-06〕「返回知识库 →」入口 (老大「规律库没做这个」)
           原型 patterns 侧栏底部同款 (solobrave-redesign line 721-722, hr + accent dot + 返回知识库 →)
           写法对齐 knowledge 侧栏「规律库入口 →」(line 46880 同款 label:' ' + 单 item) */
        { label:' ', items:[
          { name:'返回知识库 →', onClick:"sb2Go && sb2Go('knowledge')" }
        ]}
      ]
    };
  },
  influencers: function(){
    /* 〔fix/sb2-side-restore commit 1〕达人库侧栏还原原型 (line 724-735)
       删: 「筛选」(全部达人/高GMV/未跟进/紧急) + 「视图」(密集表格/经典卡片) — 旧 3 入口根因
       改: 评级(全部/A级优先/B级/C级/D级) + 类目(top 6) + 状态(已合作/试投中/待联系) 三维带计数
       数据: window._sb2TalentFacetCounts (sb2TalentsLoad 末尾写入, 服务端 facets 镜像, plural key 跟原型对齐)
       未加载: 显「–」, 严禁硬编码数字 (老大硬指令) */
    var fc = (typeof window._sb2TalentFacetCounts === 'object' && window._sb2TalentFacetCounts) || { ratings:{}, categories:{}, statuses:{}, total: 0 };
    var ratings = fc.ratings || {};
    var cats = fc.categories || {};
    var statuses = fc.statuses || {};
    var total = (typeof fc.total === 'number') ? fc.total : 0;
    var dash = '–';
    var ratingActive = (typeof _sb2TalentsFilterRating === 'string') ? _sb2TalentsFilterRating : '';
    var categoryActive = (typeof _sb2TalentsFilterCategory === 'string') ? _sb2TalentsFilterCategory : '';
    var statusActive = (typeof _sb2TalentsFilterStatus === 'string') ? _sb2TalentsFilterStatus : '';
    // 评级维 (原型 line 729-730: 全部 / A级(优先) / B级 / C级)
    var ratingItems = [
      { name:'全部', badge: total > 0 ? String(total) : dash, active: ratingActive === '',
        onClick:"sb2TalentsOnSideClick && sb2TalentsOnSideClick('rating','')" }
    ];
    var ratingLabels = { 'A':'A 级 (优先)', 'B':'B 级', 'C':'C 级', 'D':'D 级 (未评)' };
    ['A','B','C','D'].forEach(function(k){
      var cnt = ratings[k];
      ratingItems.push({
        name: ratingLabels[k] || (k + ' 级'),
        badge: (typeof cnt === 'number') ? String(cnt) : dash,
        active: ratingActive === k,
        onClick: "sb2TalentsOnSideClick && sb2TalentsOnSideClick('rating','" + k + "')"
      });
    });
    // 类目维 (top 6, 按计数降序)
    var catItems = [
      { name:'全部类目', badge: total > 0 ? String(total) : dash, active: categoryActive === '',
        onClick:"sb2TalentsOnSideClick && sb2TalentsOnSideClick('category','')" }
    ];
    var catList = Object.keys(cats).map(function(k){ return [k, cats[k]]; });
    /* 〔fix/sb2-side-restore commit 3 D4〕类目维排除 "(空)" key
       修前 bug: facets.category 有 "(空)":38 (类目未填), top6 降序会混进侧栏当类目名显示
       修法: 先 filter 掉 "(空)", 再 sort + slice(0, 6)
       设计哲学: 真类目 < 6 个就显示几个, 不强行补 (空) 桶占位 (跟 '真数据不编造' 一致) */
    catList = catList.filter(function(c){ return c[0] !== '(空)'; });
    catList.sort(function(a, b){ return (b[1]||0) - (a[1]||0); });
    catList.slice(0, 6).forEach(function(c){
      catItems.push({
        name: c[0],
        badge: (typeof c[1] === 'number') ? String(c[1]) : dash,
        active: categoryActive === c[0],
        onClick: "sb2TalentsOnSideClick && sb2TalentsOnSideClick('category','" + String(c[0]).replace(/'/g, "\\'") + "')"
      });
    });
    // 状态维 (原型 line 733-734: 已合作 / 试投中 / 待联系)
    var STATUS_LABELS = { 'cooperating':'已合作', 'communicating':'试投中', 'available':'待联系' };
    var statusItems = [
      { name:'全部状态', badge: total > 0 ? String(total) : dash, active: statusActive === '',
        onClick:"sb2TalentsOnSideClick && sb2TalentsOnSideClick('status','')" }
    ];
    ['cooperating','communicating','available'].forEach(function(k){
      var cnt = statuses[k];
      statusItems.push({
        name: STATUS_LABELS[k] || k,
        badge: (typeof cnt === 'number') ? String(cnt) : dash,
        active: statusActive === k,
        onClick: "sb2TalentsOnSideClick && sb2TalentsOnSideClick('status','" + k + "')"
      });
    });
    /* 〔28 轮 2026-10-06〕侧栏标题恢复「达人库」(老大「把侧边栏标题恢复,让所有界面都是侧边栏为主标题」)
       历史: commit 9 (12 轮) 按当时「全页达人库三字只出现一次 (hero h1)」清空 title/sub;
       现口径反转: 侧边栏 = 全页主标题 (模块名稳定), hero 标题已动态化 (默认「全部达人」),
       两处不再重复, 跟知识库 (侧栏「知识库」+ hero 动态) / 规律库同款结构
       sub 真字段: total 位在库 (facets 服务端全量聚合, 跟侧栏计数同源) */
    return {
      title:'达人库',
      sub:(total > 0 ? String(total) : dash) + ' 位在库',
      /* 〔fix/sb2-side-restore commit 13〕16 轮批注②: 达人库侧栏恢复「搜索达人...」输入
         14 轮 commit 11 全删了 .sb2-side-search (line 46328), 但 talents 侧栏原版有 (line 724-735 区域)
         老大截图标注①确认侧栏以原版为准, 现在恢复: 仅 talents 模块 searchInput
         - dashboard/knowledge/patterns/products 维持 14 轮删除 (按老大规矩: 原版有的模块侧栏搜索才恢复,
           talents/knowledge/messages 三个原版都有, dashboard 原版无搜索)
         - dashboard 已在 14 轮 commit 11 删, 维持现状不动 */
      searchInput:'搜索达人…',
      items:[
        { label:'评级', items: ratingItems },
        { label:'类目', items: catItems },
        { label:'状态', items: statusItems }
      ]
    };
  },
  products: function(){
    /* 〔fix/sb2-side-restore 26 轮②〕商品库侧栏 — 类目 onClick 真链路 + 返回知识库入口
       修前: 侧栏类目 onClick 调 switchProductsCategory — 全仓无此函数定义 (旧 selectProductCategory 只驱动已下线 legacy DOM) → 静默 no-op (同规律库 24 轮批注④ 病根)
       修法:
         ① 类目 onClick 改调 switchProductsCategory(name) — toggle 语义 (再点取消), hero 标题联动 sb2PdsUpdateHeroTitle 已在 sb2PdsRender 顶部
         ② 底部加「<i class=sb2-ico-book></i> 返回知识库 →」入口 (写法对齐 line 47747 knowledge 侧栏「规律库入口 →」, label:' ' + 单 item)
         ③ 类目渲染保持 window.productCategories 数据源 (loadProductCategories 已有, 不动) */
    var cats = (typeof window.productCategories !== 'undefined' && Array.isArray(window.productCategories)) ? window.productCategories : [];
    /* 〔fix/sb2-side-restore r34.1 协调人代补〕28 终态口径: sub 用 facet 总数真字段 (对标达人库「位在库」),
       不用 cats.length (类目数是结构维度不是库存维度). 数据源 productCategories[].count 全库 limit=500 聚合, 无时序坑 */
    var _pdsTotal = cats.reduce(function(s, c){ return s + (c.count || 0); }, 0);
    return {
      title:'商品库', sub:(_pdsTotal > 0 ? String(_pdsTotal) : '–') + ' 件在库',
      items:[
        { label:'类目', items: (cats.length ? cats : [{name:'加载中…'}]).slice(0, 30).map(function(c){
          return { name:(c.name || c.display_name || '-'), onClick:"switchProductsCategory && switchProductsCategory('"+(c.id || c.name)+"')" };
        })},
        { label:' ', items:[
          { name:'<i class=sb2-ico-book></i> 返回知识库 →', onClick:"sb2Go && sb2Go('knowledge')" }
        ]}
      ]
    };
  },
  tasks: function(){
    return { title:'任务', sub:'看板 · 列表双视图', items:[] };
  },
  settings: function(){
    return { title:'设置', sub:'4 项系统工具', items:[] };
  }
};

/* 〔fix/sb2-side-restore commit 2〕工作台 badge 数据源 (proposals 待办 + kb 待审)
   老大硬指令: 对应模块加载时写入, renderer 兜底 0 = 不显示 badge.
   worktree 架构约束: 没有统一 module-init 钩子, 用 dashboard 加载入口 (showDash) fire-and-forget 拉,
   拉到后写 window 全局 + 重渲 renderSideFor('dashboard'). 跨模块共享 window 缓存.
   失败 / 鉴权闸 fallback 0 (proposals admin-only spec §2.2 已拍, AI 员工 / localhost 拿不到是预期).
   抓 sb2RefreshDashboardBadges 名字跟 sb2RefreshSidebarData (commit 1 已删) 不冲突, 新建. */
window._sb2PendingProposals = 0;
window._sb2PendingKbReviews = 0;

window.sb2RefreshDashboardBadges = function(){
  if (typeof apiFetch !== 'function') return Promise.resolve();
  /* 〔fix/sb2-side-restore commit 3 D3〕apiFetch 返回 raw Response, 消费前必须 await resp.json()
     修前 bug: apiFetch 返回 Response 对象, r.total / r.proposals 对 Response 全 undefined
              → n = 0 静默吞掉, badge 永远 0 (proposals 实测有 1 条 pending 但显 0)
     修法: safeParse 统一把 Response → JSON, 已经是对象就透传, 容错返 null */
  var safeParse = function(resp){
    /* 〔fix/sb2-side-restore commit 4 D3 二次踩坑〕apiFetch 是 async, 返回 Promise 不是 Response
       commit 3 误判: safeParse 对 Promise 判 typeof resp.json === 'function' → false
       把 Promise 原样当 data 透传 → data.proposals 全 undefined → n=0
       修法: 第一行 Promise.resolve(resp) 展开 Promise → 拿 Response 对象 → 再判 .json */
    return Promise.resolve(resp).then(function(r){
      if (!r) return null;
      if (r && typeof r.json === 'function') {
        return r.json().then(function(d){ return d; }).catch(function(){ return null; });
      }
      return r;  // 已经是 plain object 就透传
    });
  };
  var pProposals = safeParse(apiFetch('/api/proposals?status=pending&limit=1')).then(function(data){
    var n = 0;
    if (data && typeof data === 'object') {
      if (typeof data.total === 'number') n = data.total;
      else if (Array.isArray(data.proposals)) n = data.proposals.length;
      else if (Array.isArray(data)) n = data.length;
    }
    return Number(n) || 0;
  }).catch(function(){ return 0; });
  var pKpi = safeParse(apiFetch('/api/stats/dashboard-kpi')).then(function(r){
    var n = (r && r.kpi3_kb && r.kpi3_kb.pending != null) ? r.kpi3_kb.pending : 0;
    return Number(n) || 0;
  }).catch(function(){ return 0; });
  return Promise.all([pProposals, pKpi]).then(function(rs){
    window._sb2PendingProposals = rs[0];
    window._sb2PendingKbReviews = rs[1];
    /* 〔fix/sb2-side-restore commit 4〕守卫改 window.* (跨 IIFE 作用域访问)
       sb2-shell-js IIFE 内 renderSideFor / SB2_CURRENT_MODULE 私有, 跨块访问走 window */
    if (typeof window !== 'undefined' && window.SB2_CURRENT_MODULE === 'dashboard'
        && typeof window.renderSideFor === 'function') {
      window.renderSideFor('dashboard');
    }
  });
};

var SB2_CURRENT_MODULE = 'messages';
/* 〔fix/sb2-side-restore commit 4 D1b 作用域墙〕挂 window 给达人块 (line 39648-45014) 访问
   修前 bug: sb2-shell-js 整块 (line 45068-46185) 包在 (function(){...})() IIFE 里,
            renderSideFor / SB2_CURRENT_MODULE 是 IIFE 私有,
            达人块 (前 IIFE) typeof SB2_CURRENT_MODULE !== 'undefined' 恒 false 静默跳过重渲
   SEV1 老教训: 跨脚本块调用挂 window, 你守卫写法对了但作用域假设错了 (IIFE 私有 vs 全局)
   修法: 同步挂 window, 后续赋值处 (showDash / renderSideFor 末尾) 也要同步 */
window.SB2_CURRENT_MODULE = SB2_CURRENT_MODULE;

/* 〔fix/sb2-side-restore commit 12〕15 轮热修: 侧栏员工列表过滤 (messages 模块专用)
   老大截图标注① 确认侧栏原版有 "搜索员工..." 输入, 14 轮误删, 现在恢复
   - 实时过滤 .sb2-side-item 文本, 不匹配则 display:none
   - 只在 messages 模块渲染 (.sb2-side-search-input, see renderSideFor)
   - 其他模块侧栏不渲染, 无 input, 无 .sb2-side-search-input (14 轮删除保持)
   - apply when: 任何「侧栏列表过滤」必须用守卫写法过滤 name, 不改 side 容器内其它 group (项目组群聊等) */
function sb2SideFilter(keyword){
  var k = (keyword || '').trim().toLowerCase();
  var items = document.querySelectorAll('#sb2Side .sb2-side-item');
  items.forEach(function(el){
    if (!k){ el.style.display = ''; return; }
    var name = (el.textContent || '').trim().toLowerCase();
    el.style.display = name.indexOf(k) >= 0 ? '' : 'none';
  });
}

/* 〔feat(r77) 2026-10-07 老大批注: 侧栏可收起, 所有页面通用〕
   折叠开关: 纯 body class 切换 (CSS 守卫在 index.html sb2-side CSS 块尾),
   状态持久化 localStorage sb2_side_collapsed, 跨模块/刷新保持
   lobster/tasks 模块 body 打 sb2-no-side, 展开钮 CSS :not(.sb2-no-side) 自动隐藏 */
function sb2ApplySideCollapsed(){
  if (typeof document === 'undefined' || !document.body) return;
  document.body.classList.toggle('sb2-side-collapsed', localStorage.getItem('sb2_side_collapsed') === '1');
}
function sb2ToggleSide(){
  var c = !document.body.classList.contains('sb2-side-collapsed');
  document.body.classList.toggle('sb2-side-collapsed', c);
  try { localStorage.setItem('sb2_side_collapsed', c ? '1' : '0'); } catch(e){}
}
window.sb2ToggleSide = sb2ToggleSide;

function renderSideFor(moduleId){
  var side = document.getElementById('sb2Side');
  if (!side) return;
  // dashboard 隐藏 #side, 让位 .sb2-dash 内置布局
  // dashboard 走 #sb2Side 全局渲染 (跟其他模块同模式, 复用统一模板 + badge 数据)
  //   〔fix/sb2-side-restore commit 2〕老大指令: 删 dashboard 早退, 让 dashboard 显侧栏
  //   行为同步: showDash → renderSideFor('dashboard') 工作台内容; 切其他模块 → renderSideFor(id) 切内容
  // lobster / tasks 模块内置侧栏 (.sb2-tasks-side 等), 全局 #sb2Side 仍隐藏让位
  // 〔fix/sb2-side-restore commit 5〕body.sb2-no-side class 控制顶栏/dash 全宽 vs 让位
  //   隐藏 #side (lobster/tasks) → body.sb2-no-side → 顶栏/dash left:60 (CSS body.sb2-no-side 守卫)
  //   显示 #side (其他模块) → body 无 sb2-no-side → 顶栏/dash left:296 让位
  if (moduleId === 'lobster' || moduleId === 'tasks') {
    side.style.display = 'none';
    if (typeof document !== 'undefined' && document.body) document.body.classList.add('sb2-no-side');
    /* 〔feat(r78) 批注①〕任务模块侧栏可收起: 打 body.sb2-tasks-active 标记,
       顶栏折叠钮 #sb2SideToggle 的 CSS 显隐守卫据此在任务页也生效 (lobster 保持原样) */
    if (moduleId === 'tasks' && typeof document !== 'undefined' && document.body) document.body.classList.add('sb2-tasks-active');
    /* 〔fix/sb2-side-restore commit 26〕17 轮追加 3 项 C: 任务模块 crumb 不同步
       修前 bug: renderSideFor 对 tasks / lobster 早退 (line 46518 原) 没设 SB2_CURRENT_MODULE,
         导致 sb2Go('tasks') 流程中 updateCrumb() 读 SB2_CURRENT_MODULE 仍是旧值 'products',
         crumb 永远显「商品库」(老大 prod 实测).
       修法 (派单: 统一走 SB2 分发): early return 前同步设 SB2_CURRENT_MODULE + window 同步,
         跟 line 46543/46546 的赋值对称. 其他模块不走 early return, 本来就 line 46543 设值.
       同步后 sb2Go('tasks') 流程:
         renderSideFor('tasks') → early return 但 SB2_CURRENT_MODULE='tasks' 已设
         updateCrumb() → 读 'tasks' → map.['tasks'] = '任务' <i class=sb2-ico-check></i>
         switchModule wrapper (line 46096) → renderSideFor('tasks') → 同样早退但 SB2_CURRENT_MODULE 保持 'tasks' */
    SB2_CURRENT_MODULE = moduleId;
    window.SB2_CURRENT_MODULE = SB2_CURRENT_MODULE;
    return;
  }
  side.style.display = '';
  if (typeof document !== 'undefined' && document.body) document.body.classList.remove('sb2-no-side', 'sb2-tasks-active');
  var fn = SB2_SIDE_RENDERERS[moduleId];
  var data = fn ? fn() : { title: moduleId, sub:'-', items:[] };
  var html = '';
  html += '<div class="sb2-side-head"><div class="sb2-side-title">'+escHtml(data.title)+'</div>';
  if (data.sub) html += '<div class="sb2-side-sub">'+escHtml(data.sub)+'</div></div>';
  /* 〔fix/sb2-side-restore commit 12〕15 轮热修: messages 模块侧栏恢复搜索输入 (15 轮恢复 14 轮误删)
     只在 data.searchInput 存在时渲染 .sb2-side-search-input — 14 轮 commit 11 全删之后
     截图发现 messages 模块原版侧栏顶部就有 "搜索员工..." 过滤 AI 员工列表
     - 其他模块 (dashboard/talents/knowledge/patterns/products) 不加 searchInput 字段, 不渲染
     - 跟 14 轮 commit 11 删 .sb2-side-search 逻辑一致: 14 轮是误删, 15 轮选择性恢复
     - data.searchInput: 占位文案 (不是 searchPlaceholder — placeholder 是 ⌘K 全局搜)
     - sb2SideFilter 函数 (line 46284+) 实时过滤 name, 不影响项目组群聊等其它 group */
  if (data.searchInput) {
    html += '<div class="sb2-side-search-input">'
         +  '<input type="text" placeholder="'+escHtml(data.searchInput)+'" oninput="sb2SideFilter(this.value)" autocomplete="off">'
         +  '</div>';
  }
  html += '<div class="sb2-side-scroll">';
  (data.items || []).forEach(function(group){
    if (group.label) html += '<div class="sb2-side-label">'+escHtml(group.label)+'</div>';
    (group.items || []).forEach(function(it){
      /* 〔21 轮批注①-1〕dot 模板支持三色 (online 绿/busy 橙/offline 灰) + 兼容 boolean
         boolean true = online (默认绿) / false = offline (灰) / 字符串 'busy' = 橙 */
      var dotClass = '';
      if (it.dot === true || it.dot === 'online') dotClass = 'dot';
      else if (it.dot === false || it.dot === 'offline') dotClass = 'dot offline';
      else if (it.dot === 'busy') dotClass = 'dot busy';
      var dot = dotClass ? '<span class="'+dotClass+'"></span>' : '';
      var badge = it.badge ? '<span class="badge">'+escHtml(it.badge)+'</span>' : '';
      /* 〔23 轮批注②〕role 文本补前导空格 (原版 "Helen · 商务" 有空格, prod 现 "Ray· AI拍板助手" 无空格, 排版不齐)
         修前: '· '+escHtml(it.role) — 无前导空格
         修后: ' · '+escHtml(it.role) — 与原版对齐 "Helen · 商务"
         配套: 删 .sb2-side-item-role margin-left:6px (line 10300), 让排版走 inline 自然空格 */
      var roleSpan = it.role ? '<span class="sb2-side-item-role"> · '+escHtml(it.role)+'</span>' : '';
      /* 〔r80 补刀 老大 00:12 批注「怎么有乱码」〕escHtml 后白名单还原图标标签
         — 根因: r80 图标体系把 name 里的 emoji 换成 <i class=sb2-ico-…>, 但渲染走 escHtml → 标签变字面文本
         — 修法: 只还原转义后的 sb2-ico 图标标签 (正则全匹配, 无属性注入面), 其余文本保持转义 */
      var safeName = escHtml(it.name || '-')
        .replace(/&lt;i class=sb2-ico-([a-z]+)&gt;&lt;\/i&gt;/g, '<i class="sb2-ico-$1"></i>');
      html += '<div class="sb2-side-item'+(it.active ? ' active' : '')+'" onclick="'+escAttr(it.onClick || '')+'">'
              + dot+'<span class="name">'+safeName+roleSpan+'</span>'+badge+'</div>';
    });
  });
  html += '</div>';
  html += '<div class="sb2-side-foot"><span class="dot"></span><span id="sb2SideFootStatus">已连接</span></div>';
  side.innerHTML = html;
  /* 〔18 轮批注③ 二打回 2026-10-06 01:53〕行级修法照抄: 设模块专属 class
     修前: CSS 用 :has(.sb2-side-search-input) 限定 messages → 误杀 talents 评级/类目/状态 badge
       (root cause: talents 模块 renderSideFor 也设了 searchInput='搜索达人…' line 46529,
        :has() 匹配 talents 侧栏 → 评级的"金牌/银牌"dot + 类目/状态的 badge 全隐藏 → 老大 940 视口实测)
     修法: 在 innerHTML 渲染后,设 side.className = 'sb2-side sb2-side--module-' + moduleId
       - messages 模块: side.className = 'sb2-side sb2-side--module-messages' (CSS 选择器在 [] 里命中)
       - talents 模块: side.className = 'sb2-side sb2-side--module-talents' (不影响评级/类目/状态 badge)
       - dashboard / knowledge / patterns / products / tasks 等: 同样不打 .sb2-side--module-messages
     行级严格: 只在 messages 模块生效, 其他模块侧栏 .badge/.dot 一律保留 */
  side.className = 'sb2-side sb2-side--module-' + moduleId;
  SB2_CURRENT_MODULE = moduleId;
  /* 〔fix/sb2-side-restore commit 4〕同步挂 window (跨块访问)
     renderSideFor 是 IIFE 私有, 达人块访问不到 → window.* 同步 */
  window.SB2_CURRENT_MODULE = SB2_CURRENT_MODULE;
}
/* 〔fix/sb2-side-restore commit 4〕挂 window, 达人块守卫才能访问
   IIFE 内 renderSideFor 是私有, 达人块 (line 39648-45014) typeof renderSideFor === 'function' 恒 false
   挂 window 后跨块调用生效 */
window.renderSideFor = renderSideFor;
/* 〔r74 反馈 Comment 1 老大 19:01「任务页面出现商品库的侧边栏」:
   - 修前: switchModule (老 nav .nav-item active 同步) 不同步新 .sb2-rail-btn active
     → 用户从 products 切到 tasks, 老 nav 高亮切到 navTasks, 新 rail 仍高亮商品库 → 视觉错位
   - 修法: window.setActive 暴露, 让 switchModule (inline-07.js) 同步新 rail active
   - 跟 r37 r38 r39 rail 对齐原版的设计债务同步:
     老 .nav-item + 新 .sb2-rail-btn 两套 nav 并存, active 同步路径必须双向打通 */
window.setActive = setActive;

function escHtml(s){ return String(s==null?'':s).replace(/[&<>\"']/g, function(c){ return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]; }); }
function escAttr(s){ return String(s==null?'':s).replace(/[&<>\"']/g, function(c){ return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[c]; }); }

/* 顶栏搜索/新建: UI 占位接 toast, ⌘K 快捷键后置 */
window.sb2_openPalette = function(){
  if (typeof openGlobalSearch === 'function') openGlobalSearch();
  else if (typeof showToast === 'function') showToast('搜索面板 — 原型待展开');
};
window.sb2_openCreate = function(){
  // 按模块给最常用的一个动作 (零编造数据, 只调现成 API)
  var m = SB2_CURRENT_MODULE;
  /* 〔r72 反馈 Comment 2 老大 17:10「这个也是点不开, 重新设计」:
     — 修前: messages 模块调 startNewChat → startNewConversation (不存在) → location.reload()
       用户感觉"点不动" (reload 后视觉无变化)
     — 修后: messages 模块直接清空 messages DOM + toast 提示, 绕过 location.reload 丑陋 fallback
     — startNewChat 是 inline-06.js:12 shim, 内部 fallback location.reload 不该走产品路径
     — 状态一致性: messages 不持久, 下次 reload 自然同步 (跟原版行为一致)
     — 跟 r38 IIFE onclick 铁律同款: 跨文件私有函数 (sb2_execSlashCommand) 调不到, 走直接 DOM 操作 */
  if (m === 'knowledge' && typeof createNewKnowledge === 'function') createNewKnowledge();
  else if (m === 'patterns' && typeof triggerPatternInduce === 'function') triggerPatternInduce();
  else if (m === 'influencers' && typeof openInfluencerLibrary === 'function') openInfluencerLibrary();
  else if (m === 'products' && typeof openProductLibrary === 'function') openProductLibrary();
  else if (m === 'tasks' && typeof createNewTask === 'function') createNewTask();
  else if (m === 'messages') {
    // 直接清空 messages DOM (跟 /clear slash 命令效果一致, 但不走 location.reload)
    var messagesEl = document.getElementById('sb2ChatMessages');
    if (messagesEl) messagesEl.innerHTML = ''
      + '<div class="sb2-chat-empty">'
      + '  <div class="sb2-chat-empty-icon">💬</div>'
      + '  <div>新对话已就绪</div>'
      + '  <div class="sb2-chat-empty-tip">输入消息开始新对话</div>'
      + '</div>';
    // 清空 _sb2Chat state (跟 /clear 一致, IIFE 私有 messages 数组)
    if (typeof window._sb2Chat !== 'undefined' && window._sb2Chat && Array.isArray(window._sb2Chat.messages)) {
      window._sb2Chat.messages = [];
    }
    // 提示用户 (跨 IIFE 兼容)
    if (typeof showToast === 'function') showToast('新对话已就绪');
    return;
  }
  else if (typeof showToast === 'function') showToast('新建 — 快捷入口待加强');
};

/* 顶栏 crumb 同步 */
function updateCrumb(){
  var main = document.getElementById('sb2CrumbMain');
  if (!main) return;
  var m = SB2_CURRENT_MODULE;
  /* 〔fix/sb2-side-restore commit 10〕13 轮热修:
     - messages '即时通讯' → 'AI 办公室' (老大 16:04 拍板口径修正, 模块名改回)
     - influencers early return 清空 — 老大要求全页「达人库」三字只出现一次 (hero h1 line 13859)
       原型 line 567-588 达人库视图 hero 只有 h1, 没顶栏 crumb 也没侧栏标题
     - 其他模块 map 不变 (工作台/规律库/商品库/任务/设置 顶栏仍显) */
  if (m === 'influencers'){ main.textContent = ''; return; }
  var map = {
    dashboard:'工作台', messages:'AI 办公室',
    knowledge:'知识库', patterns:'规律库',
    products:'商品库', tasks:'任务', settings:'设置'
  };
  main.textContent = map[m] || m;
}

/* ---------- 启动 ---------- */
function init(){
  buildRail();
  hookBellBadge();
  sb2BellSync();
  // 〔feat(r77)〕启动恢复侧栏收起态 (localStorage 持久, 晚于 body 就绪, 早于首屏渲染)
  sb2ApplySideCollapsed();
  // 启动时默认落 messages 屏
  if (document.getElementById('sb2Side')) renderSideFor('messages');
  updateCrumb();
  // 已登录刷新场景: showMainApp 可能已跑过, 主动落工作台
  var tok = localStorage.getItem('sb_auth_token') || '';
  var overlay = document.getElementById('loginOverlay');
  if (tok && overlay && overlay.hidden) {
    setTimeout(function(){
      // 登录态但 showMainApp 已跑过, currentUser 已就绪 → 重建 rail 让所有模块亮出来
      var oldRail = document.getElementById('sb2Rail');
      if (oldRail) oldRail.remove();
      buildRail();
      showDash();
      renderSideFor('dashboard');
      updateCrumb();
    }, 200);
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();

})();
