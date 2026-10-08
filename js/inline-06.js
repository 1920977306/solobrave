/* ===== index.html 内联块 6 外置 (r39-20 ⑥体积治理) =====
   原内联 script, 内容零改动; 原首行: function showAISwitcher() { */

function showAISwitcher() {
  if (typeof showGlobalSearch === 'function') {
    document.getElementById('globalSearchModal').style.display = 'flex';
    document.getElementById('globalSearchInput').focus();
  } else {
    showToast('AI 切换器: 可快速切到指定 AI 员工的对话', 'info');
  }
}
function startNewChat() {
  if (typeof startNewConversation === 'function') {
    startNewConversation();
  } else if (typeof startNewChat === 'function') {
    // 避免递归调用
  } else {
    location.reload();
  }
}
function showSettings() {
  if (typeof toggleSettings === 'function') {
    toggleSettings();
  } else {
    showToast('设置面板: 配置账号 / 权限 / 系统', 'info');
  }
}
function openLobsterOffice() {
  window.open('./office-v3.html', '_blank');
}

// ========== 用户管理面板 ==========
function showUserManagement() {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以管理用户');
    return;
  }
  closeUserDropdownDirect();
  _settingsSelectedCategory = 'users';
  switchModule('settings');
}
function closeUserManagement() {
  _settingsSelectedCategory = 'compute';
  renderSettingsMid();
}

// 加载用户列表
var _userListCache = [];

function loadUserList() {
  var list = document.getElementById('userList');
  if (!list) return;
  apiFetch('/api/users').then(function (res) {
    if (!res) {
      _userListCache = [];
      renderUserList(list, []);
      return;
    }
    return res.json();
  }).then(function (users) {
    if (users) {
      _userListCache = Array.isArray(users) ? users : (users.users || []);
      renderUserList(list, _userListCache);
    }
  }).catch(function () {
    _userListCache = [];
    if (list) list.innerHTML = renderEmptyState({
      iconHtml: '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
      title: '加载用户列表失败',
      desc: '请稍后重试,或检查网络连接与权限设置',
      tone: 'danger'
    });
  });
}
function renderUserList(list, users) {
  if (!list) return;
  if (!users || users.length === 0) {
    list.innerHTML = renderEmptyState({
      iconHtml: '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>',
      title: '暂无用户',
      desc: '没有可显示的用户,等待新成员加入或检查权限设置'
    });
    return;
  }
  list.innerHTML = users.map(function (u) {
    var displayName = u.displayName || u.name || u.username;
    var roleBadge = '';
    var roleSvg;
    if (u.role === 'admin') {
      roleSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9H4.5a2.5 2.5 0 0 1 0-5H6"/><path d="M18 9h1.5a2.5 2.5 0 0 0 0-5H18"/><path d="M4 22h16"/><path d="M10 14.66V17c0 .55-.47.98-.97 1.21C7.85 18.75 7 20.24 7 22"/><path d="M14 14.66V17c0 .55.47.98.97 1.21C16.15 18.75 17 20.24 17 22"/><path d="M18 2H6v7a6 6 0 0 0 12 0V2Z"/></svg>';
      roleBadge = '<span class="settings-tag settings-tag-warning">' + roleSvg + ' 管理员</span>';
    } else if (u.role === 'leader') {
      roleSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>';
      roleBadge = '<span class="settings-tag settings-tag-primary">' + roleSvg + ' 组长</span>';
    } else {
      roleSvg = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>';
      roleBadge = '<span class="settings-tag settings-tag-success">' + roleSvg + ' 员工</span>';
    }
    var teamTag = '';
    if (u.teamIds && u.teamIds.length > 0) {
      teamTag = '<span class="settings-tag settings-tag-primary"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg> 已有小组</span>';
    }
    var avatarHtml = renderAvatar({ name: displayName, avatar: u.avatar }, 40);
    return '<div class="settings-user-card">'
      + '<div class="settings-user-avatar">' + avatarHtml + '</div>'
      + '<div class="settings-user-info">'
      + '<div class="settings-user-name">' + escapeHtml(displayName) + '</div>'
      + '<div class="settings-user-meta">@' + escapeHtml(u.username) + ' · ' + roleBadge + '</div>'
      + (teamTag ? '<div class="settings-user-meta">' + teamTag + '</div>' : '')
      + '</div>'
      + '<div class="settings-user-actions">'
      + '<button onclick="editUser(\'' + escapeAttr(u.id) + '\')">编辑</button>'
      + (u.id !== (currentUser && currentUser.id) ? '<button class="danger" onclick="deleteUser(\'' + escapeAttr(u.id) + '\')">删除</button>' : '')
      + '</div></div>';
  }).join('');
}
function filterUserList() {
  var input = document.getElementById('userSearchInput');
  var list = document.getElementById('userList');
  if (!input || !list) return;
  var value = (input.value || '').toLowerCase();
  var filtered = _userListCache.filter(function (u) {
    var name = (u.displayName || u.name || u.username || '').toLowerCase();
    var username = (u.username || '').toLowerCase();
    return name.indexOf(value) >= 0 || username.indexOf(value) >= 0;
  });
  renderUserList(list, filtered);
}

// 创建用户Modal相关变量
var createUserModalTeams = [];

// 显示创建用户弹窗
function showCreateUserModal() {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以创建用户');
    return;
  }
  // 清空表单
  document.getElementById('newUsername').value = '';
  document.getElementById('newPassword').value = '';
  document.getElementById('newDisplayName').value = '';
  document.getElementById('newRole').value = 'employee';
  document.getElementById('newUserTeams').value = '';
  // 加载小组列表
  loadTeamsForUserModal();
  // 显示弹窗
  document.getElementById('createUserOverlay').classList.add('active');
  document.getElementById('createUserModal').classList.add('active');
}

// 关闭创建用户弹窗
function closeCreateUserModal() {
  document.getElementById('createUserOverlay').classList.remove('active');
  document.getElementById('createUserModal').classList.remove('active');
}

// 加载小组列表到用户Modal
function loadTeamsForUserModal() {
  apiFetch('/api/teams').then(function (res) {
    if (res && res.ok) {
      return res.json();
    }
    return [];
  }).then(function (teams) {
    createUserModalTeams = teams || [];
    var select = document.getElementById('newUserTeams');
    if (select) {
      select.innerHTML = '<option value="">-- 不分配小组 --</option>' + teams.map(function (t) {
        return '<option value="' + t.id + '">' + t.name + '</option>';
      }).join('');
    }
  }).catch(function () {
    createUserModalTeams = [];
  });
}

// 创建用户
function createUser() {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以创建用户');
    return;
  }
  var username = document.getElementById('newUsername').value.trim();
  var password = document.getElementById('newPassword').value;
  var displayName = document.getElementById('newDisplayName').value.trim() || username;
  var role = document.getElementById('newRole').value;
  var teamId = document.getElementById('newUserTeams').value;
  if (!username) {
    showToast('⚠️ 请输入用户名');
    return;
  }
  if (!password) {
    showToast('⚠️ 请输入密码');
    return;
  }
  var newUser = {
    username: username,
    password: password,
    role: role,
    displayName: displayName
  };
  if (teamId) {
    newUser.teamIds = [teamId];
  }
  apiFetch('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(newUser)
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 用户 ' + username + ' 创建成功');
      closeCreateUserModal();
      loadUserList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用，无法创建用户');
  });
}

// 删除用户
// ========== 团队管理函数 ==========
var allTeams = [];

// 显示团队管理面板
function showTeamManagement() {
  closeUserDropdownDirect();
  _settingsSelectedCategory = 'teams';
  switchModule('settings');
}

// 关闭团队管理面板
function closeTeamManagement() {
  _settingsSelectedCategory = 'compute';
  renderSettingsMid();
}

// 加载小组列表
function loadTeamList() {
  apiFetch('/api/teams').then(function (res) {
    if (res && res.ok) {
      return res.json();
    }
    return [];
  }).then(async function (teams) {
    if (typeof loadEmployees === 'function' && !emps.length) {
      try { await loadEmployees(); } catch (e) {}
    }
    allTeams = teams || [];
    renderTeamList(allTeams);
    refreshTeamMenus();
  }).catch(function () {
    allTeams = [];
    renderTeamList([]);
    refreshTeamMenus();
  });
}

// 统一刷新所有分组菜单（从 allTeams 读取，消除硬编码）
function refreshTeamMenus() {
  var options = getTeamNameList();
  var submenuHtml = options.map(function (name) {
    return '<div class="submenu-item" onclick="event.stopPropagation();empSettingsActionMove(\'' + escapeAttr(name) + '\')">' + escapeHtml(name) + '</div>';
  }).join('');

  // 更新 empSettingsMenu 的子菜单（所有匹配 .submenu 的容器）
  var empMenus = document.querySelectorAll('#empSettingsMenu .submenu');
  empMenus.forEach(function (sub) {
    sub.innerHTML = submenuHtml;
  });

  // 更新 gearMenuTemplate
  var gearMenu = document.getElementById('gearMenuTemplate');
  if (gearMenu) {
    var groupItems = gearMenu.querySelectorAll('.gear-menu-item[onclick*="gearMoveToGroup"]');
    groupItems.forEach(function (el) {
      el.remove();
    });
    var separators = gearMenu.querySelectorAll('.gear-menu-separator');
    var targetSep = separators.length > 1 ? separators[separators.length - 1] : separators[0];
    options.forEach(function (name) {
      var item = document.createElement('div');
      item.className = 'gear-menu-item';
      item.setAttribute('onclick', 'gearMoveToGroup(\'' + escapeAttr(name) + '\')');
      item.textContent = name;
      if (targetSep && targetSep.parentNode) {
        targetSep.parentNode.insertBefore(item, targetSep);
      }
    });
  }
}

// 渲染小组列表（树形卡片结构）
function renderTeamMemberAvatars(agentIds) {
  if (!agentIds || !agentIds.length) return '';
  var ids = agentIds.slice(0, 5);
  var extra = agentIds.length - ids.length;
  var html = '<div class="settings-team-members">';
  ids.forEach(function (id) {
    var emp = emps.find(function (e) { return e.id === id; });
    html += '<div class="settings-team-member-avatar" title="' + escapeAttr(emp ? emp.name : id) + '">' + renderAvatar(emp || { name: id }, 28) + '</div>';
  });
  if (extra > 0) {
    html += '<div class="settings-team-member-avatar settings-team-member-extra">+' + extra + '</div>';
  }
  html += '</div>';
  return html;
}

function renderTeamList(teams) {
  var list = document.getElementById('teamList');
  if (!list) return;
  if (!teams) teams = [];
  if (!teams.length) {
    list.innerHTML = renderEmptyState({
      iconHtml: '<svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 21h18"/><path d="M5 21V7l8-4v18"/><path d="M19 21V11l-6-4"/></svg>',
      title: '暂无小组',
      desc: '还没有创建任何小组,可以联系管理员创建'
    });
    return;
  }

  var treePrefix = ['', '├─ ', '│  ├─ ', '│  │  ├─ ', '│  │  │  ├─ '];
  function renderTeamItem(team, level) {
    var children = teams.filter(function (t) { return t.parentId === team.id; });
    var hasChildren = children.length > 0;
    var prefix = treePrefix[level] || ('│  '.repeat(level) + '├─ ');
    var memberCount = team.memberCount !== undefined ? team.memberCount : (team.agentIds ? team.agentIds.length : 0);
    var leaderName = team.leaderName || '未设置';
    var childHtml = children.map(function (c) { return renderTeamItem(c, level + 1); }).join('');
    // ui/user-mgmt-v21: 树形图标 SVG 化(原 📂/📁)
    var folderIcon = hasChildren
      ? '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7v13a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-7l-2-2H5a2 2 0 0 0-2 2z"/></svg>'
      : '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.93a2 2 0 0 1-1.66-.9l-.82-1.2A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2z"/></svg>';
    var leaderIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>';
    var memberIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>';
    var plusIcon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>';
    return '<div class="settings-team-card">'
      + '<div class="settings-team-icon">' + folderIcon + '</div>'
      + '<div class="settings-team-info">'
      + '<div class="settings-team-name"><span class="settings-team-indent">' + prefix + '</span>' + escapeHtml(team.name) + '</div>'
      + '<div class="settings-team-meta">' + leaderIcon + ' ' + escapeHtml(leaderName) + ' · ' + memberIcon + ' ' + memberCount + ' 名成员' + (team.description ? ' · ' + escapeHtml(team.description) : '') + '</div>'
      + renderTeamMemberAvatars(team.agentIds)
      + '</div>'
      + '<div class="settings-team-actions">'
      + '<button onclick="showAddSubTeamModal(\'' + escapeAttr(team.id) + '\')">' + plusIcon + ' 子小组</button>'
      + '<button onclick="showTeamMembersModal(\'' + escapeAttr(team.id) + '\')">成员</button>'
      + '<button onclick="showEditTeamModal(\'' + escapeAttr(team.id) + '\')">编辑</button>'
      + '<button class="danger" onclick="deleteTeamById(\'' + escapeAttr(team.id) + '\')">删除</button>'
      + '</div></div>' + childHtml;
  }
  var rootTeams = teams.filter(function (t) { return !t.parentId; });
  list.innerHTML = rootTeams.map(function (t) { return renderTeamItem(t, 0); }).join('');
}

// 显示创建小组弹窗
function showCreateTeamModal() {
  document.getElementById('newTeamName').value = '';
  document.getElementById('newTeamDesc').value = '';
  document.getElementById('newTeamNote').value = '';

  // 加载负责人选项（从员工列表）
  var leaderSelect = document.getElementById('newTeamLeader');
  var leaderHtml = '<option value="">-- 选择负责人 --</option>';
  var seenLeaderIds = {};
  emps.forEach(function (e) {
    if (!seenLeaderIds[e.id]) {
      seenLeaderIds[e.id] = true;
      leaderHtml += '<option value="' + e.id + '">' + escapeHtml(e.name || '未命名') + '</option>';
    }
  });
  leaderSelect.innerHTML = leaderHtml;

  // 加载父小组选项（去重）
  var parentSelect = document.getElementById('newTeamParent');
  var parentHtml = '<option value="">-- 无父小组 --</option>';
  var seenParentIds = {};
  allTeams.forEach(function (t) {
    if (!seenParentIds[t.id]) {
      seenParentIds[t.id] = true;
      parentHtml += '<option value="' + t.id + '">' + escapeHtml(t.name) + '</option>';
    }
  });
  parentSelect.innerHTML = parentHtml;
  document.getElementById('createTeamOverlay').classList.add('active');
  document.getElementById('createTeamModal').classList.add('active');
}

// 关闭创建小组弹窗
function closeCreateTeamModal() {
  document.getElementById('createTeamOverlay').classList.remove('active');
  document.getElementById('createTeamModal').classList.remove('active');
}

// 创建小组
function createTeam() {
  var name = document.getElementById('newTeamName').value.trim();
  var description = document.getElementById('newTeamDesc').value.trim();
  var leaderId = document.getElementById('newTeamLeader').value;
  var parentId = document.getElementById('newTeamParent').value;
  var note = document.getElementById('newTeamNote').value.trim();
  if (!name) {
    showToast('⚠️ 请输入小组名称');
    return;
  }
  var teamData = {
    name: name
  };
  if (description) teamData.description = description;
  if (leaderId) teamData.leader = leaderId;
  if (parentId) teamData.parentId = parentId;
  if (note) teamData.note = note;
  apiFetch('/api/teams', {
    method: 'POST',
    body: JSON.stringify(teamData)
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 小组创建成功');
      closeCreateTeamModal();
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用');
  });
}

// 显示编辑小组弹窗
function showEditTeamModal(teamId) {
  var team = allTeams.find(function (t) {
    return t.id === teamId;
  });
  if (!team) return;
  document.getElementById('editTeamId').value = team.id;
  document.getElementById('editTeamName').value = team.name || '';
  document.getElementById('editTeamDesc').value = team.description || '';

  // 加载负责人选项（从员工列表）
  var leaderSelect = document.getElementById('editTeamLeader');
  var leaderHtml = '<option value="">-- 选择负责人 --</option>';
  var seenLeaderIds = {};
  emps.forEach(function (e) {
    if (!seenLeaderIds[e.id]) {
      seenLeaderIds[e.id] = true;
      var selected = (team.leader === e.id || team.leaderId === e.id) ? 'selected' : '';
      leaderHtml += '<option value="' + e.id + '" ' + selected + '>' + escapeHtml(e.name || '未命名') + '</option>';
    }
  });
  leaderSelect.innerHTML = leaderHtml;

  // 加载父小组选项（去重，排除当前小组及其子组）
  var parentSelect = document.getElementById('editTeamParent');
  var parentHtml = '<option value="">-- 无父小组 --</option>';
  var seenParentIds = {};
  var childIds = {};
  function markChildren(pid) {
    allTeams.forEach(function (t) {
      if (t.parentId === pid) {
        childIds[t.id] = true;
        markChildren(t.id);
      }
    });
  }
  markChildren(team.id);
  allTeams.forEach(function (t) {
    if (t.id !== team.id && !childIds[t.id] && !seenParentIds[t.id]) {
      seenParentIds[t.id] = true;
      var selected = team.parentId === t.id ? 'selected' : '';
      parentHtml += '<option value="' + t.id + '" ' + selected + '>' + escapeHtml(t.name) + '</option>';
    }
  });
  parentSelect.innerHTML = parentHtml;
  document.getElementById('editTeamOverlay').classList.add('active');
  document.getElementById('editTeamModal').classList.add('active');
}

// 关闭编辑小组弹窗
function closeEditTeamModal() {
  document.getElementById('editTeamOverlay').classList.remove('active');
  document.getElementById('editTeamModal').classList.remove('active');
}

// 保存小组编辑
function saveTeamEdit() {
  var teamId = document.getElementById('editTeamId').value;
  var name = document.getElementById('editTeamName').value.trim();
  var description = document.getElementById('editTeamDesc').value.trim();
  var leaderId = document.getElementById('editTeamLeader').value;
  if (!name) {
    showToast('⚠️ 请输入小组名称');
    return;
  }
  var parentId = document.getElementById('editTeamParent').value;
  var teamData = {
    name: name
  };
  if (description) teamData.description = description;
  if (leaderId) teamData.leader = leaderId;
  teamData.parentId = parentId || null;
  apiFetch('/api/teams/' + teamId, {
    method: 'PUT',
    body: JSON.stringify(teamData)
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 小组更新成功');
      closeEditTeamModal();
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用');
  });
}

// 删除小组
function deleteTeam() {
  var teamId = document.getElementById('editTeamId').value;
  var team = allTeams.find(function (t) {
    return t.id === teamId;
  });
  if (!team) {
    showToast('⚠️ 小组不存在');
    return;
  }

  // 检查是否有子组
  var hasChildren = allTeams.some(function (t) {
    return t.parentId === teamId;
  });
  if (hasChildren) {
    showToast('⚠️ 请先删除子小组');
    return;
  }
  if (!confirm('确定要删除小组 "' + team.name + '" 吗？')) return;
  apiFetch('/api/teams/' + teamId, {
    method: 'DELETE'
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 小组已删除');
      closeEditTeamModal();
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用');
  });
}

// 通过ID删除小组（列表行按钮用）
function deleteTeamById(teamId) {
  var team = allTeams.find(function (t) {
    return t.id === teamId;
  });
  if (!team) return;
  var memberCount = team.memberCount !== undefined ? team.memberCount : (team.agentIds ? team.agentIds.length : 0);
  if (memberCount > 0) {
    showToast('⚠️ 请先移走小组中的 ' + memberCount + ' 名成员');
    return;
  }
  var hasChildren = allTeams.some(function (t) {
    return t.parentId === teamId;
  });
  if (hasChildren) {
    showToast('⚠️ 请先删除子小组');
    return;
  }
  if (!confirm('确定要删除小组 "' + team.name + '" 吗？')) return;
  apiFetch('/api/teams/' + teamId, {
    method: 'DELETE'
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 小组已删除');
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用');
  });
}

// 显示新增子小组弹窗
function showAddSubTeamModal(parentTeamId) {
  document.getElementById('newTeamName').value = '';
  document.getElementById('newTeamDesc').value = '';
  document.getElementById('newTeamNote').value = '';

  // 加载负责人选项
  var leaderSelect = document.getElementById('newTeamLeader');
  var leaderHtml = '<option value="">-- 选择负责人 --</option>';
  var seenLeaderIds = {};
  emps.forEach(function (e) {
    if (!seenLeaderIds[e.id]) {
      seenLeaderIds[e.id] = true;
      leaderHtml += '<option value="' + e.id + '">' + escapeHtml(e.name || '未命名') + '</option>';
    }
  });
  leaderSelect.innerHTML = leaderHtml;

  // 加载父小组选项（去重，默认选中当前小组）
  var parentSelect = document.getElementById('newTeamParent');
  var parentHtml = '<option value="">-- 无父小组 --</option>';
  var seenParentIds = {};
  allTeams.forEach(function (t) {
    if (!seenParentIds[t.id]) {
      seenParentIds[t.id] = true;
      var selected = t.id === parentTeamId ? 'selected' : '';
      parentHtml += '<option value="' + t.id + '" ' + selected + '>' + escapeHtml(t.name) + '</option>';
    }
  });
  parentSelect.innerHTML = parentHtml;
  document.getElementById('createTeamOverlay').classList.add('active');
  document.getElementById('createTeamModal').classList.add('active');
}

// 显示小组成员弹窗
function showTeamMembersModal(teamId) {
  document.getElementById('teamMembersTeamId').value = teamId;
  var team = allTeams.find(function (t) {
    return t.id === teamId;
  });
  document.getElementById('teamMembersTitle').textContent = (team ? team.name : '小组') + ' · 成员管理';

  // 加载小组详情获取成员列表
  apiFetch('/api/teams/' + teamId).then(function (res) {
    if (res && res.ok) return res.json();
    return null;
  }).then(function (teamData) {
    renderTeamMembers(teamData);
    // 加载可添加的成员
    loadAddableMembers(teamData);
  }).catch(function () {
    var list = document.getElementById('teamMembersList');
    if (list) list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">加载成员失败</div>';
  });
  document.getElementById('teamMembersOverlay').classList.add('active');
  document.getElementById('teamMembersModal').classList.add('active');
}

// 渲染小组当前成员
function renderTeamMembers(team) {
  var list = document.getElementById('teamMembersList');
  if (!list) return;
  // 后端返回 members 为成员详情数组，memberIds 为原始 ID 数组，agentIds 为关联 AI 员工
  var members = team && team.members ? team.members : [];
  var memberIds = team && team.memberIds ? team.memberIds : [];
  if (members.length === 0 && memberIds.length === 0) {
    list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-secondary);">暂无成员</div>';
    return;
  }
  // 优先使用 members 详情，缺失的用 memberIds 兜底
  var items = [];
  if (members.length > 0) {
    members.forEach(function (m) {
      items.push({ id: m.id || m, name: m.displayName || m.username || m.name || '未知用户' });
    });
  } else {
    memberIds.forEach(function (id) {
      items.push({ id: id, name: '用户(ID: ' + id + ')' });
    });
  }
  list.innerHTML = items.map(function (item) {
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:8px;background:var(--bg-secondary);border-radius:6px;margin-bottom:6px;">' + '<span>' + escapeHtml(item.name) + '</span>' + '<button onclick="removeTeamMember(\'' + escapeAttr(item.id) + '\')" style="padding:4px 8px;border-radius:4px;background:#FF3B30;color:white;border:none;cursor:pointer;font-size:11px;">移除</button>' + '</div>';
  }).join('');
}

// 加载可添加的成员
function loadAddableMembers(team) {
  var currentIds = [];
  if (team && team.members) {
    team.members.forEach(function (m) {
      currentIds.push(m.id || m);
    });
  } else if (team && team.memberIds) {
    currentIds = team.memberIds.slice();
  }
  var select = document.getElementById('addTeamMemberSelect');
  if (!select) return;

  // 从后端拉取全部用户供选择
  apiFetch('/api/users').then(function (res) {
    if (res && res.ok) return res.json();
    return [];
  }).then(function (users) {
    var addable = (users || []).filter(function (u) {
      return currentIds.indexOf(u.id) < 0;
    });
    select.innerHTML = '<option value="">-- 选择用户 --</option>' + addable.map(function (u) {
      return '<option value="' + escapeAttr(u.id) + '">' + escapeHtml(u.displayName || u.username || '未知') + '</option>';
    }).join('');
  }).catch(function () {
    select.innerHTML = '<option value="">加载用户失败</option>';
  });
}

// 添加小组成员
function addTeamMember() {
  var teamId = document.getElementById('teamMembersTeamId').value;
  var memberId = document.getElementById('addTeamMemberSelect').value;
  if (!memberId) {
    showToast('⚠️ 请选择要添加的成员');
    return;
  }
  apiFetch('/api/teams/' + teamId + '/members', {
    method: 'POST',
    body: JSON.stringify({
      userIds: [memberId]
    })
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 成员添加成功');
      showTeamMembersModal(teamId);
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 添加成员失败，请检查网络');
  });
}

// 移除小组成员
function removeTeamMember(agentId) {
  var teamId = document.getElementById('teamMembersTeamId').value;
  apiFetch('/api/teams/' + teamId + '/members/' + agentId, {
    method: 'DELETE'
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 成员已移除');
      showTeamMembersModal(teamId);
      loadTeamList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 移除成员失败，请检查网络');
  });
}

// 关闭小组成员弹窗
function closeTeamMembersModal() {
  document.getElementById('teamMembersOverlay').classList.remove('active');
  document.getElementById('teamMembersModal').classList.remove('active');
}
function deleteUser(userId) {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以删除用户');
    return;
  }
  if (!confirm('确定要删除这个用户吗?')) return;
  apiFetch('/api/users/' + userId, {
    method: 'DELETE'
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 用户已删除');
      loadUserList();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用，无法删除用户');
  });
}

// 编辑用户 - 打开弹窗
function editUser(userId) {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以编辑用户');
    return;
  }

  // 先尝试从后端获取用户
  apiFetch('/api/users/' + userId).then(function (res) {
    if (!res) {
      showToast('⚠️ 后端不可用');
      return null;
    }
    return res.json();
  }).then(function (user) {
    if (user) showUserEditModal(user);
  }).catch(function () {
    showToast('⚠️ 加载用户信息失败');
  });
}

// 显示用户编辑弹窗
var editingUserId = null;
var editUserModalTeams = [];
function showUserEditModal(user) {
  editingUserId = user.id;
  document.getElementById('editUserId').value = user.id;
  document.getElementById('editUserName').value = user.username || user.name || '';
  document.getElementById('editDisplayName').value = user.displayName || user.name || '';
  document.getElementById('editPassword').value = '';
  // 设置角色
  var roleSelect = document.getElementById('editRole');
  if (user.role === 'admin') roleSelect.value = 'admin';else if (user.role === 'leader') roleSelect.value = 'leader';else roleSelect.value = 'employee';
  document.getElementById('editAgentQuota').value = user.agentQuota || 10;
  // 加载小组列表
  loadEditUserTeams(user);
  document.getElementById('userEditOverlay').classList.add('active');
  document.getElementById('userEditModal').classList.add('active');
}

// 加载编辑用户的小组选择
function loadEditUserTeams(user) {
  apiFetch('/api/teams').then(function (res) {
    if (res && res.ok) {
      return res.json();
    }
    return [];
  }).then(function (teams) {
    editUserModalTeams = teams || [];
    var select = document.getElementById('editUserTeam');
    if (select) {
      select.innerHTML = '<option value="">-- 不分配小组 --</option>' + teams.map(function (t) {
        var selected = user.teamIds && user.teamIds.indexOf(t.id) >= 0 ? 'selected' : '';
        return '<option value="' + t.id + '" ' + selected + '>' + t.name + '</option>';
      }).join('');
    }
  }).catch(function () {
    editUserModalTeams = [];
  });
}
function closeUserEditModal() {
  document.getElementById('userEditOverlay').classList.remove('active');
  document.getElementById('userEditModal').classList.remove('active');
  editingUserId = null;
}
function saveUserEdit() {
  if (!editingUserId) return;
  var userId = editingUserId;
  var displayName = document.getElementById('editDisplayName').value.trim();
  var password = document.getElementById('editPassword').value;
  var role = document.getElementById('editRole').value;
  var agentQuota = parseInt(document.getElementById('editAgentQuota').value) || 10;
  if (!displayName) {
    showToast('⚠️ 请输入显示名称');
    return;
  }
  var teamSelect = document.getElementById('editUserTeam');
  var teamId = teamSelect ? teamSelect.value : '';
  var teamIds = teamId ? [teamId] : [];
  var payload = {
    displayName: displayName,
    role: role,
    agentQuota: agentQuota,
    teamIds: teamIds
  };
  if (password) {
    payload.password = password;
  }
  apiFetch('/api/users/' + userId, {
    method: 'PUT',
    body: JSON.stringify(payload)
  }).then(function (res) {
    if (res && res.ok) {
      showToast('✅ 用户信息已更新');
      loadUserList();
      closeUserEditModal();
    } else {
      return res ? res.json() : null;
    }
  }).then(function (data) {
    if (data && data.error) showToast('⚠️ ' + data.error);
  }).catch(function () {
    showToast('⚠️ 后端不可用，无法更新用户');
    closeUserEditModal();
  });
}

// 配额管理
function setUserQuota(userId) {
  if (!isAdmin()) {
    showToast('⚠️ 只有管理员可以设置配额');
    return;
  }
  var quota = prompt('设置员工数量上限 (0=无限制):');
  if (quota === null) return;
  var numQuota = parseInt(quota);
  if (isNaN(numQuota) || numQuota < 0) {
    showToast('⚠️ 请输入有效的数字');
    return;
  }
  var quotas = JSON.parse(localStorage.getItem('sb_quotas') || '{}');
  quotas[userId] = numQuota;
  localStorage.setItem('sb_quotas', JSON.stringify(quotas));
  showToast('✅ 配额已设置');
}

// 检查配额
function checkQuota() {
  if (isAdmin()) return true; // 管理员无限制

  var quotas = JSON.parse(localStorage.getItem('sb_quotas') || '{}');
  var myQuota = quotas[currentUser && currentUser.id] || 0;
  if (myQuota === 0) return true; // 0 = 无限制

  var myEmployees = emps.filter(function (e) {
    return e.createdBy === (currentUser && currentUser.id);
  });
  if (myEmployees.length >= myQuota) {
    showToast('⚠️ 已达到员工数量上限 (' + myQuota + ')');
    return false;
  }
  return true;
}

// ========== 商品库 ==========
var _productData = { products: [], total: 0 };
var _productBrandData = { brands: [], total: 0 };
var _productCurrentBrandName = '';
var _productCurrentStatus = '';
var _productCurrentSort = '';
var _productCurrentId = null;
var _productPage = 1;
var _productPageSize = 20;
var _productTab = 'detail';

function openProductLibrary() { switchModule('products'); }
function closeProductLibrary() { switchModule('messages'); closeProductRight(); }

function closeProductRight() {
  _productCurrentId = null;
  document.querySelectorAll('.products-mid-card').forEach(function(el) { el.classList.remove('active'); });
  showProductEmptyState();
}

function showProductEmptyState() {
  var empty = document.getElementById('productsEmptyState');
  var ai = document.getElementById('productsAICard');
  var tabs = document.getElementById('productsTabs');
  var panels = document.getElementById('productsTabPanels');
  if (empty) {
    var hasData = (_productData.total || 0) > 0;
    empty.innerHTML = '<div class="module-welcome-icon">📦</div>' +
      '<div class="module-welcome-title">管理你的商品库</div>' +
      '<div class="module-welcome-subtitle">录入商品信息，AI自动匹配达人带货</div>' +
      '<div class="module-welcome-cards">' +
      '<div class="module-welcome-card" onclick="onWelcomeCreateProduct()">' +
      '<span class="module-welcome-card-icon">➕</span>' +
      '<span class="module-welcome-card-text">新增商品</span>' +
      '</div>' +
      '<div class="module-welcome-card' + (hasData ? '' : ' disabled') + '"' + (hasData ? ' onclick="onWelcomeViewProductData()"' : '') + '>' +
      '<span class="module-welcome-card-icon">📊</span>' +
      '<span class="module-welcome-card-text">查看数据</span>' +
      '</div>' +
      '</div>';
    empty.style.display = 'flex';
  }
  if (ai) ai.style.display = 'none';
  if (tabs) tabs.style.display = 'none';
  if (panels) panels.style.display = 'none';
  var title = document.getElementById('productsRightTitle');
  if (title) title.textContent = '商品详情';
}

function hideProductEmptyState() {
  var empty = document.getElementById('productsEmptyState');
  var ai = document.getElementById('productsAICard');
  var tabs = document.getElementById('productsTabs');
  var panels = document.getElementById('productsTabPanels');
  if (empty) empty.style.display = 'none';
  if (ai) ai.style.display = 'block';
  if (tabs) tabs.style.display = 'flex';
  if (panels) panels.style.display = 'block';
}

function loadProducts(page) {
  if (page) _productPage = page;
  var offset = (_productPage - 1) * _productPageSize;
  var qs = ['limit=' + _productPageSize, 'offset=' + offset];
  if (_productCurrentBrandName) qs.push('brand=' + encodeURIComponent(_productCurrentBrandName));
  if (_productCurrentStatus) qs.push('status=' + encodeURIComponent(_productCurrentStatus));
  if (_productCurrentSort) qs.push('sort=' + encodeURIComponent(_productCurrentSort));
  var kw = document.getElementById('productsMidSearch') ? document.getElementById('productsMidSearch').value.trim() : '';
  if (kw) qs.push('q=' + encodeURIComponent(kw));
  var url = '/api/products?' + qs.join('&');
  var itemsEl = document.getElementById('productsMidItems');
  if (itemsEl) itemsEl.innerHTML = renderSkeleton('card', 4);
  return apiFetch(url).then(function(r){return r.json();}).then(function(data){
    _productData.products = sortProductsLocally(data.products || [], _productCurrentSort);
    _productData.total = data.total || 0;
    renderProductList(_productData.products);
    renderProductPagination(_productData.total, _productPage, _productPageSize);
    renderProductBrandOptions();
    if (!_productCurrentId) showProductEmptyState();
    // 〔fix/sb2-side-restore commit 25 B 补修〕商品库侧栏 categories 改全库聚合:
    //   loadProducts 内部 page-level 聚合删了 (派单 22:04 拍板: 全库聚合, 不能 page-level).
    //   改由 loadProductCategories(切模块时触发一次)做全库聚合 + renderSideFor('products') 重渲.
  }).catch(function(e){
    if (itemsEl) itemsEl.innerHTML = '<div class="products-empty"><div class="products-empty-text">加载失败</div></div>';
  });
}

function sortProductsLocally(products, sort) {
  if (!sort || !products || !products.length) return products;
  var list = products.slice();
  switch (sort) {
    case 'price_asc':
      list.sort(function(a,b){ return (a.price || 0) - (b.price || 0); });
      break;
    case 'price_desc':
      list.sort(function(a,b){ return (b.price || 0) - (a.price || 0); });
      break;
    case 'sales_desc':
      list.sort(function(a,b){ return (b.monthly_sales || 0) - (a.monthly_sales || 0); });
      break;
    case 'commission_desc':
      list.sort(function(a,b){ return (b.commission_rate || 0) - (a.commission_rate || 0); });
      break;
    case 'influencer_count_desc':
      list.sort(function(a,b){ return (b.influencer_count || 0) - (a.influencer_count || 0); });
      break;
  }
  return list;
}

function selectProductSort(sort) {
  _productCurrentSort = sort || '';
  _productPage = 1;
  var sel = document.getElementById('productsMidSort');
  if (sel) sel.value = _productCurrentSort;
  loadProducts();
}

function loadProductBrands() {
  var sel = document.getElementById('productsMidBrand');
  if (sel) sel.disabled = true;
  apiFetch('/api/brands').then(function(r){return r.json();}).then(function(data){
    _productBrandData.brands = data.brands || [];
    _productBrandData.total = data.total || 0;
    renderProductBrandOptions();
    if (sel) sel.disabled = false;
  }).catch(function(e){
    if (sel) sel.disabled = false;
  });
}

/* 〔fix/sb2-side-restore commit 25 B 补修〕商品库侧栏 classes 全库聚合
   派单 22:04 打回 B: 「商品库侧栏类目必须全库聚合」(page-level 不够, 87 条样本只占 50-60% 覆盖)
   修法:
   - 仿 loadProductBrands 模式, 加 loadProductCategories 函数
   - 调 /api/products?limit=500 拿全库样本 (派单说"主区正常 87 条", 500 限够覆盖)
   - 聚合 products.category 字段 → {id, name, count}, 按 count 降序
   - 写 window.productCategories 给侧栏读
   - 调 renderSideFor('products') 重渲侧栏
   - 触发点: 切到 products 模块时由 switchModule (line 486) 触发一次, 切 page 不重跑 (避免冗余)
   - 接口实测响应已确认 (worktree dev server 18210): product[0].category = "生活家居" (字符串)
   - 跟规律库 sb2PatternsInit 聚合 (commit 24 A) 同款模式, 但触发点不同:
     规律库 sb2PatternsInit 是模块 init 入口 (必然跑一次), 商品库 loadProducts 是 page-level (切 page 重复触发) */
function loadProductCategories(){
  /* 〔fix/sb2-side-restore commit 25 B 补修〕全库聚合: 用 limit=500 而非 page-level 样本.
     火塘型 fire-and-forget: 不阻塞商品库主区 page 加载, 异步写 window + renderSideFor 重渲.
     失败兜底: window.productCategories 保持上次值 (空 fallback 仍走「加载中…」) */
  apiFetch('/api/products?limit=500').then(function(r){return r.json();}).then(function(data){
    var products = (data && data.products) || [];
    var catMap = {};
    products.forEach(function(p){
      var c = p.category || '未分类';
      if (!catMap[c]) catMap[c] = { id: c, name: c, count: 0 };
      catMap[c].count++;
    });
    var catList = Object.keys(catMap).map(function(k){ return catMap[k]; });
    catList.sort(function(a, b){ return b.count - a.count; });
    window.productCategories = catList;
    if (typeof window.renderSideFor === 'function') window.renderSideFor('products');
    console.log('[products] categories 聚合完成, count:', catList.length);
  }).catch(function(e){
    console.warn('[products] categories 聚合失败:', e);
  });
}

function renderProductBrandOptions() {
  var sel = document.getElementById('productsMidBrand');
  if (!sel) return;
  var currentVal = sel.value;
  var html = '<option value="">全部品牌</option>';
  _productBrandData.brands.forEach(function(b){
    html += '<option value="' + escapeAttr(b.name) + '">' + escapeHtml(b.name) + '</option>';
  });
  sel.innerHTML = html;
  if (currentVal) sel.value = currentVal;
}

function selectProductBrand(brandName) {
  _productCurrentBrandName = brandName || '';
  _productPage = 1;
  loadProducts();
}

function selectProductStatus(status) {
  _productCurrentStatus = status || '';
  _productPage = 1;
  document.querySelectorAll('#productsStatusFilter .segmented-item').forEach(function(el){ el.classList.toggle('active', el.dataset.status === _productCurrentStatus); });
  loadProducts();
}

function onProductSearch(value) {
  clearTimeout(window._productSearchTimer);
  window._productSearchTimer = setTimeout(function(){ _productPage = 1; loadProducts(); }, 250);
}

function renderProductList(products) {
  var el = document.getElementById('productsMidItems');
  var countEl = document.getElementById('productsMidCount');
  if (countEl) countEl.textContent = '共 ' + (_productData.total || 0) + ' 条';
  if (!el) return;
  if (!products || products.length === 0) {
    el.innerHTML = '<div class="products-empty"><div class="products-empty-icon">📦</div><div class="products-empty-text">暂无商品</div><button class="products-empty-btn" onclick="createNewProduct()">录入商品</button></div>';
    return;
  }
  var statusMap = {active:'在售', inactive:'停售', out_of_stock:'缺货'};
  var html = '';
  products.forEach(function(p){
    var isActive = p.id === _productCurrentId;
    var placeholder = getProductPlaceholder(p);
    var hasBrand = p.brand && p.brand !== '-';
    html += '<div class="products-mid-card' + (isActive ? ' active' : '') + '" data-id="' + escapeAttr(p.id) + '" onclick="selectProductItem(\'' + escapeAttr(p.id).replace(/'/g,"\\'") + '\')">';
    html += placeholder;
    html += '<div class="products-mid-card-info">';
    html += '<div style="display:flex;align-items:center;gap:6px;">' + (hasBrand ? '<div class="products-brand-block" style="background:' + getBrandGradient(p.brand) + ';"></div>' : '') + '<div class="products-mid-card-title">' + escapeHtml(p.name) + '</div></div>';
    html += '<div class="products-mid-card-subtitle">' + (hasBrand ? escapeHtml(p.brand) + ' · ' : '') + escapeHtml(p.category || '-') + (isAdmin() && p.createdByName ? ' · 👤 ' + escapeHtml(p.createdByName) : '') + '</div>';
    html += '<div class="products-mid-card-metrics">';
    html += '<div class="products-mid-card-price-row">';
    html += '<span class="products-mid-card-price">¥' + (p.price || 0).toFixed(2) + '</span>';
    var hasRates = p.commission_rates && Object.keys(p.commission_rates).length > 0;
    var commissionUnset = !hasRates && (p.commission_rate === null || p.commission_rate === undefined || Number(p.commission_rate) === 0);
    var commissionDisplay = '';
    if (hasRates) {
      commissionDisplay = '佣金 ' + Object.keys(p.commission_rates).map(function(k){ return k + p.commission_rates[k] + '%'; }).join('/');
    } else if (!commissionUnset) {
      commissionDisplay = '佣金' + Number(p.commission_rate).toFixed(0) + '%';
    } else {
      commissionDisplay = '佣金·待设置';
    }
    html += '<span class="products-mid-card-commission' + (commissionUnset ? '' : ' commission-set') + '">' + commissionDisplay + '</span>';
    html += '</div>';
    // ui/products-avatar-color: 月销旁加 sparkline + trend ▲▼ — 跟 talent/products-detail KPI 卡一致
    var _salesArr = _sparkFromSeedP(p.monthly_sales || (p.id ? String(p.id).length * 17 + 7 : 0), 7);
    var _salesTrend = _trendFromArrP(_salesArr);
    var _salesColor = _salesTrend.dir > 0 ? 'var(--color-rating-a, #34C759)' : (_salesTrend.dir < 0 ? 'var(--color-status-lost, #FF3B30)' : 'var(--color-text-tertiary, #AEAEB2)');
    var _trendIcon = _salesTrend.dir > 0 ? '▲' : (_salesTrend.dir < 0 ? '▼' : '·');
    html += '<div class="products-mid-card-sales-row">';
    html += '<span class="products-mid-card-sales">月销 ' + formatNumber(p.monthly_sales || 0) + '</span>';
    html += '<span class="products-mid-card-sales-trend" style="color:' + _salesColor + '">' + _renderSparkSVGP(_salesArr, { w: 36, h: 14, color: _salesColor }) + '<span class="products-mid-card-sales-trend-num">' + _trendIcon + ' ' + _salesTrend.pct + '%</span></span>';
    html += '<span class="products-mid-card-influencers">' + (p.influencer_count || 0) + '位达人带货</span>';
    html += '<span class="products-status-' + (p.status || 'active') + '">' + (statusMap[p.status] || p.status) + '</span>';
    html += '</div></div></div></div>';
  });
  el.innerHTML = html;
}

// ui/products-avatar-color: 8 色 HSL 调色板 — 按 brand name 哈希出色(同类商品彩色区分)
// 取代旧 brandColors 1:1 字典 + 单蓝 fallback,7+ 商品视觉立即拉开
function _productAvatarColor(seed) {
  var PALETTE = ['#5B8DEF','#FF8C42','#9D6BDF','#5DC4A6','#F06B83','#5BB3D9','#F2C849','#86909C'];
  var s = String(seed || '');
  var h = 5381;
  for (var i = 0; i < s.length; i++) { h = ((h << 5) + h) ^ s.charCodeAt(i); h = h & h; }
  return PALETTE[Math.abs(h) % PALETTE.length];
}

// ui/products-avatar-color: sparkline + trend helper(从 ui/talent-pix-v21-redesign 复用)
// product 列表只暴露 monthly_sales 单值,用 sales 作 seed 派 sparkline(无数据时画平线)
// P 后缀避免与 talent 模块同名 _sparkFromSeed / _renderSparkSVG / _trendFromArr 冲突
// (dev 上 talent 版本是 string seed + LCG + area+path + mid-split trend;这里是 number seed + polyline + first-vs-last)
function _sparkFromSeedP(seed, len) {
  len = len || 7;
  var arr = [];
  var v = Math.max(1, Number(seed) || 1);
  for (var i = 0; i < len; i++) {
    var j = (seed * (i + 1) + i * 7919) % 100003;
    var noise = ((j % 31) - 15) / 100;
    var trend = (i / (len - 1) - 0.5) * 0.4;
    arr.push(Math.max(0, v * (1 + noise + trend)));
  }
  return arr;
}
function _renderSparkSVGP(arr, opts) {
  opts = opts || {};
  var w = opts.w || 36;
  var h = opts.h || 14;
  var color = opts.color || 'var(--accent, #1677ff)';
  var min = Math.min.apply(null, arr);
  var max = Math.max.apply(null, arr);
  var range = max - min || 1;
  var stepX = w / (arr.length - 1);
  var pts = arr.map(function(v, i) {
    var x = i * stepX;
    var y = h - ((v - min) / range) * h;
    return x.toFixed(1) + ',' + y.toFixed(1);
  }).join(' ');
  return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" style="display:block;flex-shrink:0;"><polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
}
function _trendFromArrP(arr) {
  if (!arr || arr.length < 2) return { pct: 0, dir: 0 };
  var first = arr[0], last = arr[arr.length - 1];
  if (first === 0) return { pct: last > 0 ? 100 : 0, dir: last > 0 ? 1 : 0 };
  var pct = Math.round(((last - first) / first) * 100);
  return { pct: Math.abs(pct), dir: pct > 0 ? 1 : (pct < 0 ? -1 : 0) };
}

function getProductPlaceholder(p) {
  if (p.main_image) {
    return '<img class="products-mid-card-image" src="' + escapeAttr(p.main_image) + '" alt="' + escapeAttr(p.name) + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';"><div class="products-mid-card-placeholder" style="display:none;">' + escapeHtml((p.name || '?').charAt(0)) + '</div>';
  }
  // ui/products-avatar-color: 用 brand (无 brand 退到 name) hash 出色,稳定且视觉区分
  var color = _productAvatarColor(p.brand || p.name || '');
  var ch = (p.name || '?').charAt(0);
  return '<div class="products-mid-card-placeholder" style="background:' + color + '">' + escapeHtml(ch) + '</div>';
}

function getBrandGradient(brand) {
  // ui/products-avatar-color: 跟 _productAvatarColor 同源 8 色 hash — 品牌色块 + letter avatar 视觉统一
  // 取代旧 26 色 first-letter 字典 + COOLCHAP 硬编码,2 个识别体系合并成 1 个
  var c = _productAvatarColor(brand || '?');
  return 'linear-gradient(135deg, ' + c + ', ' + c + 'cc)';
}

function renderProductPagination(total, page, pageSize) {
  var el = document.getElementById('productsMidPagination');
  if (!el) return;
  var totalPages = Math.ceil(total / pageSize) || 1;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  var html = '';
  html += '<button onclick="loadProducts(' + (page - 1) + ')" ' + (page <= 1 ? 'disabled' : '') + '>上一页</button>';
  for (var i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
      html += '<button class="' + (i === page ? 'active' : '') + '" onclick="loadProducts(' + i + ')">' + i + '</button>';
    } else if (i === page - 2 || i === page + 2) {
      html += '<span style="color:#86909c;font-size:13px;">...</span>';
    }
  }
  html += '<button onclick="loadProducts(' + (page + 1) + ')" ' + (page >= totalPages ? 'disabled' : '') + '>下一页</button>';
  el.innerHTML = html;
}

// ★ feat/p03-quality-loop 交付 4: 异步拉 V3 商品评分, 在商品详情头部显示 S/A/B/C/D chip
function loadProductV3Rating(productId) {
  var chipEl = document.getElementById('productsV3RatingChip');
  if (!chipEl) return;
  chipEl.style.display = 'none';
  chipEl.textContent = '';
  delete chipEl.dataset.rating;
  delete chipEl.dataset.error;
  apiFetch('/api/products/' + encodeURIComponent(productId) + '/score')
    .then(function(r){ return r.json(); })
    .then(function(data){
      var grade = data && data['评级'];
      var score = data && data['综合分'];
      if (!grade) {
        chipEl.style.display = 'inline-flex';
        chipEl.dataset.error = '1';
        chipEl.textContent = '暂无评级';
        return;
      }
      chipEl.style.display = 'inline-flex';
      chipEl.dataset.rating = grade;
      chipEl.textContent = 'V3 评级 ' + grade + ' (' + (score != null ? score : '-') + ')';
      chipEl.title = 'V3 评分: 选品意愿 ' + (data['选品意愿分'] || '-') + ' · 带货效果 ' + (data['带货效果分'] || '-') + ' · 综合 ' + (score || '-');
    })
    .catch(function(e){
      // ★ feat/p03-quality-loop 交付 4: 静默降级 — 网络错/404 不弹错, 只显提示
      console.warn('[loadProductV3Rating] failed', e);
      chipEl.style.display = 'inline-flex';
      chipEl.dataset.error = '1';
      chipEl.textContent = 'V3 暂不可用';
    });
}

function selectProductItem(id) {
  _productCurrentId = id;
  var p = _productData.products.find(function(x){return x.id === id;});
  if (!p) return;
  apiFetch('/api/products/' + encodeURIComponent(id)).then(function(r){return r.json();}).then(function(product){
    renderProductDetail(product);
    document.querySelectorAll('.products-mid-card').forEach(function(el){el.classList.remove('active');});
    var activeEl = document.querySelector('.products-mid-card[data-id="' + escapeAttr(id) + '"]');
    if (activeEl) activeEl.classList.add('active');
  }).catch(function(e){ console.error('[selectProductItem] 加载失败:', e); showToast('加载失败'); });
}

function renderProductDetail(p) {
  hideProductEmptyState();
  var title = document.getElementById('productsRightTitle');
  if (title) title.textContent = p.name || '商品详情';
  // AI card
  var ai = p.ai_analysis || {};
  var scoreEl = document.getElementById('productsAIScore');
  var statusEl = document.getElementById('productsAIStatus');
  var bodyEl = document.getElementById('productsAIBody');
  if (ai.ai_score) {
    if (scoreEl) scoreEl.textContent = ai.ai_score + '星';
    if (statusEl) statusEl.textContent = 'AI 已完成分析';
    if (bodyEl) bodyEl.innerHTML = '<div class="products-ai-section"><div class="products-ai-label">竞争分析</div><div class="products-ai-text">' + formatMessageContent(ai.competition_analysis || '-') + '</div></div><div class="products-ai-section"><div class="products-ai-label">选品建议</div><div class="products-ai-text">' + formatMessageContent(ai.selection_advice || '-') + '</div></div>';
  } else {
    if (scoreEl) scoreEl.textContent = '-';
    if (statusEl) statusEl.textContent = '点击按钮生成分析';
    if (bodyEl) bodyEl.innerHTML = '';
  }
  // ★ ui/products-detail-v21-kpi: 4 KPI mini 渲染 (月销/达人/佣金率/单价) + sparkline mini
  renderProductKPIGrid(p);
  // Reset tab
  _productTab = 'detail';
  document.querySelectorAll('.products-tab').forEach(function(t){ t.classList.toggle('active', t.dataset.tab === 'detail'); });
  document.querySelectorAll('.products-tab-panel').forEach(function(p){ p.classList.toggle('active', p.dataset.panel === 'detail'); });
  // Render panels
  renderProductPanelDetail(p);
  renderProductPanelSales(p);
  renderProductPanelInfluencers(p);
  renderProductPanelAudience(p);
  // ★ feat/p03-quality-loop 交付 4: 异步拉 V3 评分, 显示 S/A/B/C/D 角标
  loadProductV3Rating(p.id);
}

// ★ ui/products-detail-v21-kpi: 4 KPI mini (跟 talent-detail KPI 风格对齐)
// Sparkline / trend / helper 跟 talent 一致 (复制避免跨 worktree 引用)
function _productSparkFromSeed(seed, len) {
  var s = 0;
  for (var i = 0; i < seed.length; i++) s = (s * 31 + seed.charCodeAt(i)) >>> 0;
  var arr = [];
  var prev = 50 + (s % 50);
  for (var i = 0; i < len; i++) {
    s = (s * 1103515245 + 12345) >>> 0;
    var delta = ((s % 17) - 8) * 0.8;
    prev = Math.max(5, Math.min(95, prev + delta));
    arr.push(prev);
  }
  return arr;
}
function _productRenderSparkSVG(arr, opts) {
  opts = opts || {};
  var w = opts.w || 80, h = opts.h || 24, pad = 1;
  var min = Math.min.apply(null, arr), max = Math.max.apply(null, arr);
  var range = max - min || 1;
  var stepX = (w - pad * 2) / (arr.length - 1);
  var pts = arr.map(function (v, i) {
    var x = pad + i * stepX;
    var y = pad + (h - pad * 2) * (1 - (v - min) / range);
    return [x, y];
  });
  var path = 'M ' + pts.map(function (p) { return p[0].toFixed(1) + ',' + p[1].toFixed(1); }).join(' L ');
  var areaPath = path + ' L ' + pts[pts.length - 1][0].toFixed(1) + ',' + (h - pad) + ' L ' + pts[0][0].toFixed(1) + ',' + (h - pad) + ' Z';
  return '<svg class="products-kpi-spark" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none">'
    + '<path class="area" d="' + areaPath + '"></path>'
    + '<path d="' + path + '"></path>'
    + '</svg>';
}
function _productTrendFromArr(arr) {
  if (arr.length < 2) return { dir: 'flat', pct: 0 };
  var mid = Math.floor(arr.length / 2);
  var first = arr.slice(0, mid).reduce(function (a, b) { return a + b; }, 0) / mid;
  var second = arr.slice(mid).reduce(function (a, b) { return a + b; }, 0) / (arr.length - mid);
  if (second === 0) return { dir: 'flat', pct: 0 };
  var pct = ((second - first) / first) * 100;
  var dir = Math.abs(pct) < 2 ? 'flat' : (pct > 0 ? 'up' : 'down');
  return { dir: dir, pct: Math.round(Math.abs(pct)) };
}
function renderProductKPIGrid(p) {
  var grid = document.getElementById('productsKPIGrid');
  if (!grid) return;
  var commissionVal = (p.commission_rates && Object.keys(p.commission_rates).length)
    ? Object.keys(p.commission_rates).map(function (k) { return p.commission_rates[k]; }).join('/')
    : ((p.commission_rate || 0) + '%');
  var seed = (p.id || 'p') + '-kpi';
  var kpis = [
    { label: '月销',      val: formatNumber(p.monthly_sales || 0),          seed: 'ms' },
    { label: '带货达人',  val: (p.influencer_count || 0) + ' 位',           seed: 'ifc' },
    { label: '佣金率',    val: commissionVal,                              seed: 'cr' },
    { label: '单价',      val: '¥' + (p.price || 0).toFixed(2),             seed: 'pr' }
  ];
  var html = '';
  kpis.forEach(function (k) {
    var arr = _productSparkFromSeed(seed + k.seed, 12);
    var trend = _productTrendFromArr(arr);
    var trendCls = trend.dir;
    var arrow = trend.dir === 'up' ? '▲' : (trend.dir === 'down' ? '▼' : '–');
    var cardCls = 'products-kpi-card ' + trendCls;
    html += '<div class="' + cardCls + '">'
      + '<div class="products-kpi-value">' + escapeHtml(k.val) + '</div>'
      + '<div class="products-kpi-label">' + escapeHtml(k.label) + '</div>'
      + '<div class="products-kpi-trend ' + trendCls + '">' + arrow + ' ' + trend.pct + '%</div>'
      + _productRenderSparkSVG(arr)
      + '</div>';
  });
  grid.innerHTML = html;
}

function switchProductTab(tab) {
  _productTab = tab;
  document.querySelectorAll('.products-tab').forEach(function(t){ t.classList.toggle('active', t.dataset.tab === tab); });
  document.querySelectorAll('.products-tab-panel').forEach(function(p){ p.classList.toggle('active', p.dataset.panel === tab); });
}

function renderProductPanelDetail(p) {
  var el = document.getElementById('productsPanelDetail');
  if (!el) return;
  var statusMap = {active:'在售', inactive:'停售', out_of_stock:'缺货'};
  var html = '';
  // dev/feat: products 修复 #3 — main_image 100% 空时显示 placeholder (与列表卡片对齐)
  // 真实图优先; 无图时用 getProductPlaceholder (品牌色 + name 首字); 图加载失败降级
  html += '<div class="products-panel-section" style="text-align:center;">';
  if (p.main_image) {
    html += '<img src="' + escapeAttr(p.main_image) + '" style="max-width:200px;max-height:200px;border-radius:12px;border:1px solid var(--border);" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\';">';
    html += '<div style="display:none;width:200px;height:200px;margin:0 auto;align-items:center;justify-content:center;">' + getProductPlaceholder(p) + '</div>';
  } else {
    html += getProductPlaceholder(p);
  }
  html += '</div>';
  html += '<div class="products-panel-section">';
  html += '<div class="products-panel-title">基本信息</div>';
  html += '<div class="products-detail-grid">';
  html += '<div class="products-detail-item"><div class="products-detail-label">商品名称</div><div class="products-detail-value">' + escapeHtml(p.name || '-') + '</div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">品牌</div><div class="products-detail-value">' + escapeHtml(p.brand || '-') + '</div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">类目</div><div class="products-detail-value">' + escapeHtml(p.category || '-') + '</div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">穿搭场景</div><div class="products-detail-value">' + escapeHtml(p.scene || '-') + '</div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">价格</div><div class="products-detail-value">¥' + (p.price || 0).toFixed(2) + '</div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">状态</div><div class="products-detail-value"><span class="products-status-' + (p.status || 'active') + '">' + (statusMap[p.status] || p.status || '-') + '</span></div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">原价</div><div class="products-detail-value">¥' + (p.original_price || 0).toFixed(2) + '</div></div>';
  var commRateHtml = '';
  if (p.commission_rates && Object.keys(p.commission_rates).length > 0) {
    commRateHtml = Object.keys(p.commission_rates).map(function(k){ return escapeHtml(k) + ' ' + p.commission_rates[k] + '%'; }).join(' / ');
  } else {
    commRateHtml = (p.commission_rate || 0) + '%';
  }
  html += '<div class="products-detail-item"><div class="products-detail-label">佣金率</div><div class="products-detail-value">' + commRateHtml + '</div></div>';
  html += '</div></div>';
  if (p.description || p.subtitle) {
    html += '<div class="products-panel-section">';
    html += '<div class="products-panel-title">商品描述</div>';
    html += '<div style="font-size:13px;color:var(--text-secondary);line-height:1.6;">' + escapeHtml(p.description || p.subtitle || '-') + '</div>';
    html += '</div>';
  }
  if (p.sku_specs && Object.keys(p.sku_specs).length > 0) {
    html += '<div class="products-panel-section">';
    html += '<div class="products-panel-title">SKU 规格</div>';
    html += '<table class="products-sku-table"><tbody>';
    Object.keys(p.sku_specs).forEach(function(k){
      var vals = p.sku_specs[k];
      var valStr = Array.isArray(vals) ? vals.join(', ') : String(vals);
      html += '<tr><td><span class="sku-spec-key">' + escapeHtml(k) + '</span><span class="sku-spec-val">' + escapeHtml(valStr) + '</span></td></tr>';
    });
    html += '</tbody></table></div>';
  }
  if (p.tags && p.tags.length) {
    html += '<div class="products-panel-section">';
    html += '<div class="products-panel-title">标签</div>';
    html += '<div class="products-tag-list">';
    p.tags.forEach(function(tag){ html += '<span class="products-tag">' + escapeHtml(tag) + '</span>'; });
    html += '</div></div>';
  }
  // 带货案例视频（存储于 products.videos，JSON数组，元素格式 {title, cover, url, views, likes}）
  html += '<div class="products-panel-section">';
  html += '<div class="products-panel-title">带货案例视频</div>';
  var videos = Array.isArray(p.videos) ? p.videos : [];
  if (videos.length) {
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;">';
    videos.forEach(function(v){
      var hasUrl = v.url && /^https?:\/\//.test(v.url);
      html += '<div style="border:1px solid var(--sb2-border,#EBEBEB);border-radius:10px;overflow:hidden;background:var(--sb2-s1,#fff);' + (hasUrl ? 'cursor:pointer;' : '') + '"' + (hasUrl ? ' onclick="window.open(\'' + escapeAttr(v.url) + '\',\'_blank\')"' : '') + '>';
      if (v.cover) {
        html += '<div style="width:100%;aspect-ratio:16/9;background:#F5F5F5;"><img src="' + escapeAttr(v.cover) + '" style="width:100%;height:100%;object-fit:cover;" onerror="this.parentNode.style.display=\'none\';"></div>';
      } else {
        html += '<div style="width:100%;aspect-ratio:16/9;background:#F5F5F5;display:flex;align-items:center;justify-content:center;font-size:28px;color:#C0C4CC;">▶</div>';
      }
      html += '<div style="padding:8px 10px;">';
      html += '<div style="font-size:13px;font-weight:500;color:var(--text-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(v.title || '未命名视频') + '</div>';
      html += '<div style="font-size:12px;color:var(--text-secondary);margin-top:4px;">播放 ' + formatNumber(v.views || 0) + ' · 点赞 ' + formatNumber(v.likes || 0) + '</div>';
      html += '</div></div>';
    });
    html += '</div>';
  } else {
    html += '<div style="font-size:13px;color:var(--text-secondary);">暂无带货案例视频</div>';
  }
  html += '</div>';
  html += '<div style="display:flex;gap:10px;padding:0 4px;margin-top:8px;">';
  html += '<button class="product-modal-btn save" onclick="editCurrentProduct()" style="flex:1;padding:10px 16px;">编辑商品</button>';
  html += '<button class="product-modal-btn cancel" onclick="deleteCurrentProduct()" style="flex:1;padding:10px 16px;color:#FF3B30;">删除商品</button>';
  html += '</div>';
  el.innerHTML = html;
}

function renderProductPanelSales(p) {
  var el = document.getElementById('productsPanelSales');
  if (!el) return;
  var inputStyle = 'width:100%;padding:6px 8px;border:1px solid #E0E0E0;border-radius:8px;font-size:14px;color:var(--text-primary);font-weight:500;text-align:right;outline:none;background:#fff;box-sizing:border-box;';
  var html = '<div class="products-panel-section">';
  html += '<div class="products-panel-title"><span>销售概览</span><button class="ai-match-btn" style="padding:6px 12px;font-size:12px;margin-left:auto;" onclick="saveProductSales()">保存</button></div>';
  html += '<div class="products-detail-grid">';
  html += '<div class="products-detail-item"><div class="products-detail-label">月销量</div><input type="number" id="productSales-monthly_sales" value="' + (p.monthly_sales || 0) + '" step="1" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">月 GMV</div><input type="number" id="productSales-monthly_gmv" value="' + (p.monthly_gmv || 0) + '" step="0.01" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">佣金金额</div><input type="number" id="productSales-commission_amount" value="' + (p.commission_amount || 0) + '" step="0.01" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">转化率</div><div style="position:relative;"><input type="number" id="productSales-conversion_rate" value="' + (p.conversion_rate || 0) + '" step="0.01" style="' + inputStyle + 'padding-right:28px;"><span style="position:absolute;right:8px;top:50%;transform:translateY(-50%);color:var(--text-secondary);font-size:14px;pointer-events:none;">%</span></div></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">客单价</div><input type="number" id="productSales-avg_order_value" value="' + (p.avg_order_value || 0) + '" step="0.01" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">合作达人数</div><input type="number" id="productSales-influencer_count" value="' + (p.influencer_count || 0) + '" step="1" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">视频数</div><input type="number" id="productSales-video_count" value="' + (p.video_count || 0) + '" step="1" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">直播数</div><input type="number" id="productSales-live_count" value="' + (p.live_count || 0) + '" step="1" style="' + inputStyle + '"></div>';
  html += '</div></div>';
  if (p.commission_rates && Object.keys(p.commission_rates).length > 0) {
    html += '<div class="products-panel-section">';
    html += '<div class="products-panel-title">佣金率（分周期）</div>';
    Object.keys(p.commission_rates).forEach(function(k){
      html += '<div class="products-rate-row"><span class="products-rate-label">' + escapeHtml(k) + '</span><span class="products-rate-value">' + p.commission_rates[k] + '%</span></div>';
    });
    html += '</div>';
  }
  if (p.channel_distribution && Object.keys(p.channel_distribution).length > 0) {
    html += '<div class="products-panel-section">';
    html += '<div class="products-panel-title">渠道分布</div>';
    var channels = p.channel_distribution;
    if (typeof channels === 'string') {
      var parsed = {};
      channels.split(/[\n、]/).forEach(function(line) {
        var m = line.match(/^(.+?)(\d+(?:\.\d+)?)\s*%$/);
        if (m) parsed[m[1].trim()] = parseFloat(m[2]);
      });
      channels = parsed;
    }
    var cKeys = Object.keys(channels).filter(function(k){ return k !== 'brand_info' && (parseFloat(channels[k])||0) > 0; });
    var cTotal = cKeys.reduce(function(a,k){ return a + (parseFloat(channels[k])||0); }, 0);
    if (cTotal > 0) {
      var cMaxKey = cKeys[0], cMaxVal = 0;
      cKeys.forEach(function(k){ var v = parseFloat(channels[k])||0; if (v > cMaxVal) { cMaxVal = v; cMaxKey = k; } });
      var cMaxPct = cTotal ? (cMaxVal / cTotal * 100) : 0;
      html += '<div style="font-size:12px;color:var(--text-tertiary);margin-bottom:10px;">以' + escapeHtml(cMaxKey) + '为主，占比' + cMaxPct.toFixed(2) + '%</div>';
      var cColors = ['#FF4D6D', '#1677ff', '#34C759', '#FF9500', '#AF52DE'];
      var cRadius = 48, cCirc = 2 * Math.PI * cRadius, cOffset = 0;
      html += '<div style="display:flex;align-items:center;justify-content:center;padding:4px 0 10px;">';
      html += '<svg width="130" height="130" viewBox="0 0 130 130">';
      html += '<circle cx="65" cy="65" r="' + cRadius + '" fill="none" stroke="var(--bg-tertiary)" stroke-width="9"/>';
      cKeys.forEach(function(k, i) {
        var v = parseFloat(channels[k]) || 0;
        var pct = cTotal ? (v / cTotal * 100) : 0;
        var dashLen = (pct / 100) * cCirc;
        var color = cColors[i % cColors.length];
        html += '<circle cx="65" cy="65" r="' + cRadius + '" fill="none" stroke="' + color + '" stroke-width="9" stroke-dasharray="' + dashLen.toFixed(2) + ' ' + (cCirc - dashLen).toFixed(2) + '" stroke-dashoffset="' + (-cOffset).toFixed(2) + '" transform="rotate(-90 65 65)"/>';
        cOffset += dashLen;
      });
      html += '<text x="65" y="62" text-anchor="middle" font-size="19" font-weight="600" fill="var(--text-primary)">' + cMaxPct.toFixed(1) + '%</text>';
      html += '<text x="65" y="80" text-anchor="middle" font-size="10" fill="var(--text-tertiary)">' + escapeHtml(cMaxKey.length > 6 ? cMaxKey.substring(0,6) + '…' : cMaxKey) + '</text>';
      html += '</svg>';
      html += '</div>';
      html += '<div style="display:flex;flex-wrap:wrap;gap:10px 16px;justify-content:center;">';
      cKeys.forEach(function(k, i) {
        var v = parseFloat(channels[k]) || 0;
        var pct = cTotal ? (v / cTotal * 100) : 0;
        var color = cColors[i % cColors.length];
        html += '<div style="display:flex;align-items:center;gap:5px;"><span style="width:7px;height:7px;border-radius:50%;background:' + color + ';display:inline-block;flex-shrink:0;"></span><span style="font-size:12px;color:var(--text-secondary);">' + escapeHtml(k) + ' ' + pct.toFixed(2) + '%</span></div>';
      });
      html += '</div>';
    }
    html += '</div>';
  }
  el.innerHTML = html;
}

function saveProductSales() {
  if (!_productCurrentId) { showToast('请选择商品'); return; }
  var body = {
    monthly_sales: parseInt(document.getElementById('productSales-monthly_sales').value) || 0,
    monthly_gmv: parseFloat(document.getElementById('productSales-monthly_gmv').value) || 0,
    commission_amount: parseFloat(document.getElementById('productSales-commission_amount').value) || 0,
    conversion_rate: parseFloat(document.getElementById('productSales-conversion_rate').value) || 0,
    avg_order_value: parseFloat(document.getElementById('productSales-avg_order_value').value) || 0,
    influencer_count: parseInt(document.getElementById('productSales-influencer_count').value) || 0,
    video_count: parseInt(document.getElementById('productSales-video_count').value) || 0,
    live_count: parseInt(document.getElementById('productSales-live_count').value) || 0
  };
  apiFetch('/api/products/' + encodeURIComponent(_productCurrentId), {
    method: 'PUT',
    body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(data){
    showToast('✅ 保存成功');
    selectProductItem(_productCurrentId);
  }).catch(function(e){
    console.error(e);
    showToast('❌ 保存失败');
  });
}

function renderProductPanelInfluencers(p) {
  var el = document.getElementById('productsPanelInfluencers');
  if (!el) return;
  _currentProductMatchTalents = { product: p, matches: [], sortBy: 'score' };
  var html = '<div class="products-panel-section">';
  html += '<div class="ai-match-header">';
  html += '<div class="products-panel-title" style="margin:0;padding:0;border:none;">AI 推荐可合作达人</div>';
  html += '<div class="ai-match-actions">';
  html += '<select class="ai-match-sort" id="productTalentSort" onchange="onProductTalentSortChange(this.value)">';
  html += '<option value="score">按匹配度</option>';
  html += '<option value="followers">按粉丝数</option>';
  html += '<option value="gmv">按历史GMV</option>';
  html += '</select>';
  html += '<button class="ai-match-btn" id="btnMatchProductTalents" onclick="runProductTalentMatch(\'' + escapeAttr(p.id) + '\')">AI 匹配达人</button>';
  html += '</div></div>';
  html += '<div id="productTalentMatchBody" class="ai-match-body">';
  html += '<div class="products-empty"><div class="products-empty-text">点击上方「AI 匹配达人」按钮，为该商品智能推荐最合适的达人</div></div>';
  html += '</div></div>';
  el.innerHTML = html;
}

function onProductTalentSortChange(sortBy) {
  _currentProductMatchTalents.sortBy = sortBy;
  _renderProductTalentMatches();
}

function runProductTalentMatch(productId) {
  var btn = document.getElementById('btnMatchProductTalents');
  if (btn) { btn.disabled = true; btn.textContent = '匹配中...'; }
  var body = document.getElementById('productTalentMatchBody');
  if (body) body.innerHTML = '<div class="products-loading"><div class="spinner"></div>AI 正在分析匹配...</div>';
  apiFetch('/api/products/' + encodeURIComponent(productId) + '/match-talents', {
    method: 'POST',
    body: JSON.stringify({ limit: 10, agentId: getCurrentEmpId() || '' })
  }).then(function(r){ return r.json(); }).then(function(data){
    _currentProductMatchTalents.matches = data.matches || [];
    _currentProductMatchTalents.degraded = !!data.degraded;  // ★ feat/p03-quality-loop 交付 2
    _currentProductMatchTalents.degrade_reason = data.degrade_reason || null;
    _renderProductTalentMatches();
    if (data.degraded) {
      // ★ feat/p03-quality-loop: 不再吞错 — 降级时显示明确提示, 区分 timeout/empty/parse_error
      showDegradeToast(data.degrade_reason, 'product');
    }
    if (btn) { btn.disabled = false; btn.textContent = '重新匹配'; }
  }).catch(function(e){
    // ★ feat/p03-quality-loop: 不再吞错 — 网络/权限错显式区分
    console.error('[runProductTalentMatch] network error', e);
    if (body) body.innerHTML = '<div class="products-empty"><div class="products-empty-text">网络错误或权限不足，请重试 (err: ' + (e && e.message || e) + ')</div></div>';
    if (typeof showToast === 'function') showToast('⚠️ 网络错误或权限不足: ' + (e && e.message || e), 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'AI 匹配达人'; }
  });
}

// ★ feat/p03-quality-loop 交付 2: 降级 toast — 不同 reason 给具体提示, 用户能区分问题
function showDegradeToast(reason, direction) {
  var dirLabel = direction === 'product' ? '商品匹配达人' : '达人匹配商品';
  var detail = '';
  if (reason === 'timeout') {
    detail = 'AI 分析超时(已超过 ' + (typeof OPENCLAW_TIMEOUT !== 'undefined' ? OPENCLAW_TIMEOUT : 90) + 's), 已展示规则匹配结果';
  } else if (reason === 'empty_result') {
    detail = 'AI 接口返回为空(网关或 key 异常), 已展示规则匹配结果';
  } else if (reason === 'parse_error') {
    detail = 'AI 输出无法解析为 JSON, 已展示规则匹配结果';
  } else if (reason === 'no_agent') {
    detail = '未配置 AI 员工, 已展示规则匹配结果';
  } else if (reason === 'no_candidates') {
    detail = '无候选可匹配, 已展示规则匹配结果';
  } else {
    detail = 'AI 阶段暂不可用, 已展示规则匹配结果';
  }
  if (typeof showToast === 'function') showToast('ℹ️ ' + dirLabel + ': ' + detail, 'info');
}

function _renderProductTalentMatches() {
  var body = document.getElementById('productTalentMatchBody');
  if (!body) return;
  var matches = (_currentProductMatchTalents.matches || []).slice();
  var sortBy = _currentProductMatchTalents.sortBy || 'score';
  matches.sort(function(a, b){
    if (sortBy === 'followers') {
      var fa = a.talent && a.talent.followers ? a.talent.followers : 0;
      var fb = b.talent && b.talent.followers ? b.talent.followers : 0;
      return fb - fa;
    }
    if (sortBy === 'gmv') {
      var ga = a.talent && a.talent.total_gmv ? a.talent.total_gmv : 0;
      var gb = b.talent && b.talent.total_gmv ? b.talent.total_gmv : 0;
      return gb - ga;
    }
    return (b.score || 0) - (a.score || 0);
  });
  if (matches.length === 0) {
    body.innerHTML = '<div class="products-empty"><div class="products-empty-text">暂无匹配达人</div></div>';
    return;
  }
  var html = '';
  matches.forEach(function(m){
    var t = m.talent || {};
    var reason = m.aiReason || (m.reasons && m.reasons[0]) || '';
    var avatar = t.avatar ? '<img class="ai-match-card-avatar" src="' + escapeAttr(t.avatar) + '" style="width:100%;height:100%;object-fit:cover;border-radius:8px;" alt="">' : '<div class="ai-match-card-avatar" style="background:var(--accent);">' + escapeHtml((t.name || '?').charAt(0)) + '</div>';
    html += '<div class="ai-match-card">';
    html += '<div class="ai-match-card-left">' + avatar + '</div>';
    html += '<div class="ai-match-card-main">';
    html += '<div class="ai-match-card-title">' + escapeHtml(t.name || '-') + ' <span style="font-size:11px;color:var(--text-secondary);font-weight:400;">' + (t.level || '') + '</span></div>';
    html += '<div class="ai-match-card-meta">粉丝 ' + formatNumber(t.followers || 0) + ' · ' + escapeHtml(String(t.fan_category || t.category || '未分类')) + '</div>';
    html += '<div class="ai-match-card-tags">';
    html += '<span class="ai-match-tag">历史GMV ¥' + formatNumber(t.total_gmv || 0) + '</span>';
    html += '</div>';
    html += '<div class="ai-match-card-reason">' + escapeHtml(reason) + '</div>';
    html += '</div>';
    html += '<div class="ai-match-card-right">';
    html += '<div class="ai-match-score">' + (m.matchPercent || 0).toFixed(0) + '<span>%</span></div>';
    html += '<div class="ai-match-score-label">匹配度</div>';
    html += '</div>';
    html += '</div>';
  });
  body.innerHTML = html;
}

function renderProductPanelAudience(p) {
  var el = document.getElementById('productsPanelAudience');
  if (!el) return;
  var audience = p.audience || {};
  var inputStyle = 'width:100%;padding:6px 8px;border:1px solid #E0E0E0;border-radius:8px;font-size:13px;color:var(--text-primary);outline:none;background:#fff;box-sizing:border-box;font-family:inherit;';
  function distToText(obj) {
    if (!obj || Object.keys(obj).length === 0) return '';
    return Object.keys(obj).map(function(k){ return k + ':' + obj[k] + '%'; }).join(', ');
  }
  var html = '<div class="products-panel-section">';
  html += '<div class="products-panel-title"><span>受众画像</span><button class="ai-match-btn" style="padding:6px 12px;font-size:12px;margin-left:auto;" onclick="saveProductAudience()">保存</button></div>';
  html += '<div class="products-detail-grid">';
  html += '<div class="products-detail-item"><div class="products-detail-label">性别分布</div><input type="text" id="audience-gender" value="' + escapeAttr(distToText(audience.gender)) + '" placeholder="如: 女:60%, 男:40%" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">年龄分布</div><input type="text" id="audience-age" value="' + escapeAttr(distToText(audience.age)) + '" placeholder="如: 18-24:30%, 25-34:50%" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">地域分布</div><input type="text" id="audience-region" value="' + escapeAttr(distToText(audience.region)) + '" placeholder="如: 浙江:40%, 广东:30%" style="' + inputStyle + '"></div>';
  html += '<div class="products-detail-item"><div class="products-detail-label">职业分布</div><input type="text" id="audience-occupation" value="' + escapeAttr(distToText(audience.occupation)) + '" placeholder="如: 学生:30%, 上班族:50%" style="' + inputStyle + '"></div>';
  var _interests = audience.interests; if (typeof _interests === 'string') _interests = _interests.split(',').map(function(s){return s.trim();}).filter(Boolean); if (!Array.isArray(_interests)) _interests = [];
  html += '<div class="products-detail-item" style="grid-column:1/-1;"><div class="products-detail-label">兴趣标签</div><input type="text" id="audience-interests" value="' + escapeAttr(_interests.join(', ')) + '" placeholder="多个标签用逗号分隔，如: 时尚, 穿搭, 女鞋" style="' + inputStyle + '"></div>';
  html += '</div></div>';
  el.innerHTML = html;
}
function saveProductAudience() {
  if (!_productCurrentId) { showToast('请选择商品'); return; }
  var saveBtn = event && event.target ? event.target : null;
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = '保存中...'; }
  function parseDist(text) {
    var obj = {};
    if (!text || !text.trim()) return obj;
    text.split(',').forEach(function(pair) {
      var parts = pair.trim().split(':');
      if (parts.length >= 2) {
        var k = parts[0].trim();
        var v = parseFloat(parts.slice(1).join(':').trim()) || 0;
        if (k) obj[k] = v;
      }
    });
    return obj;
  }
  var body = {
    audience: {
      gender: parseDist(document.getElementById('audience-gender').value),
      age: parseDist(document.getElementById('audience-age').value),
      region: parseDist(document.getElementById('audience-region').value),
      occupation: parseDist(document.getElementById('audience-occupation').value),
      interests: document.getElementById('audience-interests').value.split(',').map(function(s){return s.trim();}).filter(Boolean)
    }
  };
  apiFetch('/api/products/' + encodeURIComponent(_productCurrentId), {method:'PUT', body: JSON.stringify(body)}).then(function(){ showToast('✅ 受众画像已保存'); loadProducts().then(function(){ selectProductItem(_productCurrentId); }); }).catch(function(){ showToast('❌ 保存失败'); }).finally(function(){ if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = '保存'; } });
}

function analyzeProductAI() {
  if (!_productCurrentId) return;
  var btn = document.getElementById('productsAIBtn');
  if (btn) { btn.disabled = true; btn.textContent = '分析中...'; }
  apiFetch('/api/products/' + encodeURIComponent(_productCurrentId) + '/analyze', {method:'POST'}).then(function(r){return r.json();}).then(function(data){
    var p = _productData.products.find(function(x){return x.id === _productCurrentId;});
    if (p) p.ai_analysis = data.ai_analysis;
    renderProductDetail(p || data);
    showToast('✅ AI 分析完成');
  }).catch(function(e){ showToast('❌ 分析失败'); }).finally(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'AI 分析'; }
  });
}

function renderProductBrandOptions(selectedName) {
  var sel = document.getElementById('productBrandSelect');
  if (!sel) return;
  var html = '<option value="">选择品牌</option>';
  _productBrandData.brands.forEach(function(b){
    html += '<option value="' + escapeAttr(b.name) + '"' + (b.name === selectedName ? ' selected' : '') + '>' + escapeHtml(b.name) + '</option>';
  });
  sel.innerHTML = html;
}

function onProductBrandChange(brandName) {
  var b = _productBrandData.brands.find(function(x){ return x.name === brandName; });
  document.getElementById('productBrandId').value = b ? b.id : '';
}

function uploadProductMainImage(input) {
  var file = input.files && input.files[0];
  if (!file) return;
  var reader = new FileReader();
  reader.onload = function(e) {
    var url = e.target.result;
    document.getElementById('productMainImage').value = url;
    var prev = document.getElementById('productMainImagePreview');
    prev.innerHTML = '<img src="' + url + '" style="max-width:120px;max-height:120px;border-radius:8px;border:1px solid var(--border);">';
  };
  reader.readAsDataURL(file);
}
function onProductMainImageInput() {
  var url = document.getElementById('productMainImage').value.trim();
  var prev = document.getElementById('productMainImagePreview');
  if (url) {
    prev.innerHTML = '<img src="' + escapeAttr(url) + '" style="max-width:120px;max-height:120px;border-radius:8px;border:1px solid var(--border);" onerror="this.style.display=\'none\'">';
  } else {
    prev.innerHTML = '';
  }
}
function createNewProduct() {
  document.getElementById('productEditId').value = '';
  document.getElementById('productName').value = '';
  document.getElementById('productMainImage').value = '';
  document.getElementById('productMainImagePreview').innerHTML = '';
  document.getElementById('productDescription').value = '';
  renderProductBrandOptions();
  document.getElementById('productBrandInput').value = _productCurrentBrandName || '';
  document.getElementById('productCategory').value = '拖鞋';
  document.getElementById('productScene').value = '';
  document.getElementById('productPrice').value = '';
  document.getElementById('productOriginalPrice').value = '';
  document.getElementById('productCommissionRate1').value = '';
  document.getElementById('productCommissionRate2').value = '';
  renderSkuSpecs({});
  document.getElementById('productStatus').value = 'active';
  document.getElementById('productModalTitle').textContent = '录入商品';
  document.getElementById('productModalOverlay').classList.add('active');
}

function editCurrentProduct() {
  if (!_productCurrentId) { showToast('请选择商品'); return; }
  apiFetch('/api/products/' + encodeURIComponent(_productCurrentId)).then(function(r){return r.json();}).then(function(p){
    document.getElementById('productEditId').value = p.id;
    document.getElementById('productName').value = p.name || '';
    document.getElementById('productMainImage').value = p.main_image || '';
    var prevEl = document.getElementById('productMainImagePreview');
    if (p.main_image) {
      prevEl.innerHTML = '<img src="' + escapeAttr(p.main_image) + '" style="max-width:120px;max-height:120px;border-radius:8px;border:1px solid var(--border);" onerror="this.style.display=\'none\'">';
    } else {
      prevEl.innerHTML = '';
    }
    document.getElementById('productDescription').value = p.description || p.subtitle || '';
    document.getElementById('productBrandInput').value = p.brand || '';
    document.getElementById('productCategory').value = p.category || '';
    document.getElementById('productScene').value = p.scene || '';
    document.getElementById('productPrice').value = p.price || '';
    document.getElementById('productOriginalPrice').value = p.original_price || '';
    document.getElementById('productCommissionRate1').value = (p.commission_rates && p.commission_rates['自然流']) || p.commission_rate || '';
    document.getElementById('productCommissionRate2').value = (p.commission_rates && p.commission_rates['投放期']) || '';
    renderSkuSpecs(p.sku_specs);
    document.getElementById('productStatus').value = p.status || 'active';
    document.getElementById('productModalTitle').textContent = '编辑商品';
    document.getElementById('productModalOverlay').classList.add('active');
  });
}

function closeProductModal(e) { if (e && e.target !== e.currentTarget) return; document.getElementById('productModalOverlay').classList.remove('active'); }

var _skuSpecsData = [];
function renderSkuSpecs(specs) {
  _skuSpecsData = [];
  if (specs && typeof specs === 'object') {
    Object.keys(specs).forEach(function(k) { _skuSpecsData.push({ name: k, values: specs[k].join(', ') }); });
  }
  _renderSkuSpecsUI();
}
function _renderSkuSpecsUI() {
  var c = document.getElementById('productSkuSpecsContainer');
  c.innerHTML = '';
  _skuSpecsData.forEach(function(spec, idx) {
    var row = document.createElement('div'); row.className = 'sku-spec-row';
    var ni = document.createElement('input'); ni.className = 'sku-spec-name'; ni.value = spec.name; ni.placeholder = '规格名';
    ni.oninput = function() { _skuSpecsData[idx].name = this.value; };
    row.appendChild(ni);
    var vi = document.createElement('input'); vi.className = 'sku-spec-values'; vi.value = spec.values; vi.placeholder = '选项，逗号分隔';
    vi.oninput = function() { _skuSpecsData[idx].values = this.value; };
    row.appendChild(vi);
    var rb = document.createElement('button'); rb.className = 'sku-spec-remove'; rb.innerHTML = '×';
    rb.onclick = function() { _skuSpecsData.splice(idx, 1); _renderSkuSpecsUI(); };
    row.appendChild(rb); c.appendChild(row);
  });
}
function addSkuSpecRow() {
  _skuSpecsData.push({ name: '', values: '' }); _renderSkuSpecsUI();
  var c = document.getElementById('productSkuSpecsContainer'); var lr = c.lastElementChild;
  if (lr) { var ni = lr.querySelector('.sku-spec-name'); if (ni) ni.focus(); }
}
function collectSkuSpecs() {
  var r = {}; _skuSpecsData.forEach(function(s) {
    if (s.name.trim()) { var opts = s.values.split(',').map(function(v){ return v.trim(); }).filter(Boolean); if (opts.length) r[s.name.trim()] = opts; }
  }); return r;
}

function saveProduct() {
  var id = document.getElementById('productEditId').value;
  var skuSpecs = collectSkuSpecs();
  var body = {
    name: document.getElementById('productName').value.trim(),
    main_image: document.getElementById('productMainImage').value.trim(),
    description: document.getElementById('productDescription').value.trim(),
    brand: document.getElementById('productBrandInput').value.trim(),
    category: document.getElementById('productCategory').value.trim(),
    scene: document.getElementById('productScene').value.trim(),
    price: parseFloat(document.getElementById('productPrice').value) || 0,
    original_price: parseFloat(document.getElementById('productOriginalPrice').value) || 0,
    commission_rate: parseFloat(document.getElementById('productCommissionRate1').value) || 0,
    commission_rates: { '自然流': parseFloat(document.getElementById('productCommissionRate1').value) || 0, '投放期': parseFloat(document.getElementById('productCommissionRate2').value) || 0 },
    sku_specs: skuSpecs,
    status: document.getElementById('productStatus').value
  };
  if (!body.name) { showToast('请输入商品名称'); return; }
  var url = '/api/products' + (id ? '/' + encodeURIComponent(id) : '');
  var method = id ? 'PUT' : 'POST';
  apiFetch(url, {method: method, body: JSON.stringify(body)}).then(function(){ showToast('✅ 已保存'); closeProductModal(); loadProducts(); if (id) { selectProductItem(id); } }).catch(function(){ showToast('❌ 保存失败'); });
}

function deleteCurrentProduct() {
  if (!_productCurrentId) { showToast('请选择商品'); return; }
  if (!confirm('确定删除此商品？')) return;
  apiFetch('/api/products/' + encodeURIComponent(_productCurrentId), {method:'DELETE'}).then(function(){ showToast('✅ 已删除'); closeProductRight(); loadProducts(); }).catch(function(){ showToast('❌ 删除失败'); });
}

// ========== 任务管理 ==========
var _tasksData = [];       // 当前任务列表缓存
var _taskFilter = '';      // 当前筛选状态
var _currentTaskId = null; // 当前查看的任务

var _TASK_STATUS_MAP = {
  pending: '待处理',
  in_progress: '进行中',
  completed: '已完成',
  cancelled: '已取消'
};
var _TASK_PRIORITY_MAP = {
  high: '高',
  normal: '中',
  low: '低'
};

function _taskStatusBadge(status) {
  var s = status || 'pending';
  return '<span class="task-badge task-badge-status-' + escapeAttr(s) + '">' + (_TASK_STATUS_MAP[s] || s) + '</span>';
}

function _taskPriorityBadge(priority) {
  var p = priority || 'normal';
  return '<span class="task-badge task-badge-priority-' + escapeAttr(p) + '">优先级:' + (_TASK_PRIORITY_MAP[p] || p) + '</span>';
}

function loadTasks() {
  var qs = [];
  if (_taskFilter) qs.push('status=' + encodeURIComponent(_taskFilter));
  var url = '/api/tasks' + (qs.length ? '?' + qs.join('&') : '');
  var itemsEl = document.getElementById('tasksListItems');
  if (itemsEl) itemsEl.innerHTML = renderSkeleton('list', 5);
  apiFetch(url).then(function(r){return r.json();}).then(function(data){
    var tasks = Array.isArray(data) ? data : (data.tasks || []);
    _tasksData = tasks;
    renderTaskList(tasks);
    var countEl = document.getElementById('tasksMidCount');
    if (countEl) countEl.textContent = '共 ' + tasks.length + ' 条';
  }).catch(function(e){
    if (itemsEl) itemsEl.innerHTML = renderEmptyState({
      icon: '⚠️',
      title: '加载失败',
      desc: '网络异常,请稍后重试',
      tone: 'danger',
      cta: { label: '重试', onclick: 'loadTasks()' }
    });
  });
}

function renderTaskList(tasks) {
  var itemsEl = document.getElementById('tasksListItems');
  if (!itemsEl) return;
  if (!tasks || !tasks.length) {
    var cta = isAdmin() ? { label: '+ 新建任务', onclick: 'openTaskForm()' } : undefined;
    itemsEl.innerHTML = renderEmptyState({
      icon: '✅',
      title: '暂无任务',
      desc: '点击右上角"+ 新建任务"创建第一个任务',
      cta: cta
    });
    return;
  }
  var html = '';
  tasks.forEach(function(t) {
    html += '<div class="task-card' + (t.id === _currentTaskId ? ' active' : '') + '" onclick="showTaskDetail(\'' + escapeAttr(t.id) + '\')">'
      + '<div class="task-card-title">' + escapeHtml(t.title) + '</div>'
      + '<div class="task-card-meta">'
      + '<span class="task-card-assignee">👤 ' + escapeHtml(t.assignee_name || t.assignee || '未指派') + '</span>'
      + (t.deadline ? '<span class="task-card-deadline">📅 ' + escapeHtml(t.deadline) + '</span>' : '')
      + '</div>'
      + '<div class="task-card-footer">'
      + _taskPriorityBadge(t.priority)
      + _taskStatusBadge(t.status)
      + '</div>'
      + '</div>';
  });
  itemsEl.innerHTML = html;
}

function filterTasks(status) {
  _taskFilter = status || '';
  document.querySelectorAll('#tasksStatusFilter .segmented-item').forEach(function(el) {
    el.classList.toggle('active', el.getAttribute('data-status') === _taskFilter);
  });
  loadTasks();
}

function showTaskDetail(id) {
  if (!id) return;
  _currentTaskId = id;
  renderTaskList(_tasksData); // 刷新列表选中态
  var contentEl = document.getElementById('tasksRightContent');
  if (contentEl) contentEl.innerHTML = renderSkeleton('detail');
  apiFetch('/api/tasks/' + encodeURIComponent(id)).then(function(r){return r.json();}).then(function(t){
    if (t && t.error) { showToast('❌ ' + t.error, 'error'); return; }
    _currentTaskId = t.id;
    renderTaskDetail(t);
  }).catch(function(e){
    if (contentEl) contentEl.innerHTML = renderEmptyState({
      icon: '⚠️',
      title: '加载失败',
      desc: '任务详情获取失败,请稍后重试',
      tone: 'danger',
      cta: { label: '重试', onclick: 'showTaskDetail(\'' + escapeAttr(id) + '\')' }
    });
  });
}

// 渲染任务详情（管理员可编辑，员工只改状态和进度）
function renderTaskDetail(t) {
  var contentEl = document.getElementById('tasksRightContent');
  if (!contentEl) return;
  var titleEl = document.getElementById('tasksRightTitle');
  if (titleEl) titleEl.textContent = '任务详情';
  var admin = isAdmin();
  var status = t.status || 'pending';
  var statusActions = '';
  if (status === 'pending') {
    statusActions = '<button class="module-action-btn primary" onclick="updateTaskStatus(\'' + escapeAttr(t.id) + '\', \'in_progress\')">开始处理</button>';
  } else if (status === 'in_progress') {
    statusActions = '<button class="module-action-btn primary" onclick="updateTaskStatus(\'' + escapeAttr(t.id) + '\', \'completed\')">标记完成</button>';
  }
  if (admin && status !== 'cancelled' && status !== 'completed') {
    statusActions += '<button class="module-action-btn" onclick="updateTaskStatus(\'' + escapeAttr(t.id) + '\', \'cancelled\')">取消任务</button>';
  }

  var html = '';
  if (admin) {
    // 管理员：可编辑所有字段
    html += '<div class="task-detail-section">'
      + '<div class="task-detail-title">' + escapeHtml(t.title) + '</div>'
      + '<div class="module-form-row"><label>标题</label><input type="text" id="taskEditTitle" value="' + escapeAttr(t.title) + '"></div>'
      + '<div class="module-form-row"><label>描述</label><textarea id="taskEditDesc">' + escapeHtml(t.description || '') + '</textarea></div>'
      + '<div class="module-form-row"><label>指派给</label><select id="taskEditAssignee"><option value="">加载中...</option></select></div>'
      + '<div class="module-form-row"><label>优先级</label><select id="taskEditPriority">'
      + '<option value="high"' + (t.priority === 'high' ? ' selected' : '') + '>高</option>'
      + '<option value="normal"' + (!t.priority || t.priority === 'normal' ? ' selected' : '') + '>中</option>'
      + '<option value="low"' + (t.priority === 'low' ? ' selected' : '') + '>低</option>'
      + '</select></div>'
      + '<div class="module-form-row"><label>截止日期</label><input type="date" id="taskEditDeadline" value="' + escapeAttr(t.deadline || '') + '"></div>'
      + '<div class="module-form-row"><label>进度备注</label><textarea id="taskEditProgress" placeholder="填写进度说明">' + escapeHtml(t.progress || '') + '</textarea></div>'
      + '<div class="task-status-actions">'
      + '<button class="module-action-btn primary" onclick="saveTask(\'' + escapeAttr(t.id) + '\')">保存</button>'
      + '<button class="module-action-btn danger" onclick="deleteTask(\'' + escapeAttr(t.id) + '\')">删除</button>'
      + statusActions
      + '</div></div>';
  } else {
    // 员工：只读信息 + 状态切换 + 进度备注
    html += '<div class="task-detail-section">'
      + '<div class="task-detail-title">' + escapeHtml(t.title) + '</div>'
      + '<div class="task-detail-row"><div class="task-detail-label">状态</div><div class="task-detail-value">' + _taskStatusBadge(status) + '</div></div>'
      + '<div class="task-detail-row"><div class="task-detail-label">优先级</div><div class="task-detail-value">' + _taskPriorityBadge(t.priority) + '</div></div>'
      + '<div class="task-detail-row"><div class="task-detail-label">指派人</div><div class="task-detail-value">' + escapeHtml(t.assignee_name || t.assignee || '未指派') + '</div></div>'
      + '<div class="task-detail-row"><div class="task-detail-label">创建人</div><div class="task-detail-value">' + escapeHtml(t.creator_name || t.creator || '-') + '</div></div>'
      + (t.deadline ? '<div class="task-detail-row"><div class="task-detail-label">截止日期</div><div class="task-detail-value">' + escapeHtml(t.deadline) + '</div></div>' : '')
      + '<div class="task-detail-row"><div class="task-detail-label">创建时间</div><div class="task-detail-value">' + escapeHtml(t.created_at || '-') + '</div></div>'
      + (t.completed_at ? '<div class="task-detail-row"><div class="task-detail-label">完成时间</div><div class="task-detail-value">' + escapeHtml(t.completed_at) + '</div></div>' : '')
      + '</div>';
    if (t.description) {
      html += '<div class="task-detail-section"><div class="task-detail-label" style="margin-bottom:6px;">描述</div><div class="task-detail-desc">' + escapeHtml(t.description) + '</div></div>';
    }
    html += '<div class="task-detail-section">'
      + '<div class="module-form-row"><label>进度备注</label><textarea id="taskProgressInput" placeholder="填写进度说明">' + escapeHtml(t.progress || '') + '</textarea></div>'
      + '<div class="task-status-actions">'
      + '<button class="module-action-btn" onclick="submitTaskProgress(\'' + escapeAttr(t.id) + '\')">更新进度</button>'
      + statusActions
      + '</div></div>';
  }
  contentEl.innerHTML = html;
  if (admin) loadTaskAssigneeOptions('taskEditAssignee', t.assignee);
}

// 加载AI员工列表到指派人下拉框
function loadTaskAssigneeOptions(selectId, selectedId) {
  var sel = document.getElementById(selectId);
  if (!sel) return;
  apiFetch('/api/agents').then(function(r){return r.json();}).then(function(data){
    var agents = Array.isArray(data) ? data : (data.agents || []);
    var html = '<option value="">未指派</option>';
    agents.forEach(function(a) {
      html += '<option value="' + escapeAttr(a.id) + '"' + (a.id === selectedId ? ' selected' : '') + '>' + escapeHtml(a.name || a.id) + '</option>';
    });
    sel.innerHTML = html;
  }).catch(function(e){
    sel.innerHTML = '<option value="">加载失败</option>';
  });
}

function openTaskForm() {
  if (!isAdmin()) { showToast('⛔ 仅管理员可新建任务', 'warning'); return; }
  _currentTaskId = null;
  renderTaskList(_tasksData);
  var titleEl = document.getElementById('tasksRightTitle');
  if (titleEl) titleEl.textContent = '新建任务';
  var contentEl = document.getElementById('tasksRightContent');
  if (!contentEl) return;
  contentEl.innerHTML = '<div class="task-detail-section">'
    + '<div class="task-detail-title">新建任务</div>'
    + '<div class="module-form-row"><label>标题 *</label><input type="text" id="taskFormTitle" placeholder="请输入任务标题"></div>'
    + '<div class="module-form-row"><label>描述</label><textarea id="taskFormDesc" placeholder="请输入任务描述"></textarea></div>'
    + '<div class="module-form-row"><label>指派给</label><select id="taskFormAssignee"><option value="">加载中...</option></select></div>'
    + '<div class="module-form-row"><label>优先级</label><select id="taskFormPriority">'
    + '<option value="high">高</option><option value="normal" selected>中</option><option value="low">低</option>'
    + '</select></div>'
    + '<div class="module-form-row"><label>截止日期</label><input type="date" id="taskFormDeadline"></div>'
    + '<div class="task-status-actions">'
    + '<button class="module-action-btn primary" onclick="submitTask()">提交</button>'
    + '<button class="module-action-btn" onclick="closeTaskDetail()">取消</button>'
    + '</div></div>';
  loadTaskAssigneeOptions('taskFormAssignee', '');
}

function submitTask() {
  var title = document.getElementById('taskFormTitle').value.trim();
  if (!title) { showToast('请输入任务标题', 'warning'); return; }
  var body = {
    title: title,
    description: document.getElementById('taskFormDesc').value.trim(),
    assignee: document.getElementById('taskFormAssignee').value,
    priority: document.getElementById('taskFormPriority').value,
    deadline: document.getElementById('taskFormDeadline').value
  };
  apiFetch('/api/tasks', {method: 'POST', body: JSON.stringify(body)}).then(function(r){return r.json();}).then(function(t){
    showToast('✅ 任务已创建', 'success');
    loadTasks();
    if (t && t.id) showTaskDetail(t.id);
  }).catch(function(e){ showToast('❌ 创建失败', 'error'); });
}

function updateTaskStatus(id, status) {
  apiFetch('/api/tasks/' + encodeURIComponent(id), {method: 'PUT', body: JSON.stringify({status: status})}).then(function(r){return r.json();}).then(function(){
    showToast('✅ 状态已更新', 'success');
    loadTasks();
    showTaskDetail(id);
  }).catch(function(e){ showToast('❌ 更新失败', 'error'); });
}

function updateTaskProgress(id, progress) {
  apiFetch('/api/tasks/' + encodeURIComponent(id), {method: 'PUT', body: JSON.stringify({progress: progress})}).then(function(r){return r.json();}).then(function(){
    showToast('✅ 进度已更新', 'success');
    loadTasks();
    showTaskDetail(id);
  }).catch(function(e){ showToast('❌ 更新失败', 'error'); });
}

// 员工更新进度备注
function submitTaskProgress(id) {
  var input = document.getElementById('taskProgressInput');
  updateTaskProgress(id, input ? input.value.trim() : '');
}

// 管理员保存编辑（全部字段）
function saveTask(id) {
  var title = document.getElementById('taskEditTitle').value.trim();
  if (!title) { showToast('请输入任务标题', 'warning'); return; }
  var body = {
    title: title,
    description: document.getElementById('taskEditDesc').value.trim(),
    assignee: document.getElementById('taskEditAssignee').value,
    priority: document.getElementById('taskEditPriority').value,
    deadline: document.getElementById('taskEditDeadline').value,
    progress: document.getElementById('taskEditProgress').value.trim()
  };
  apiFetch('/api/tasks/' + encodeURIComponent(id), {method: 'PUT', body: JSON.stringify(body)}).then(function(r){return r.json();}).then(function(){
    showToast('✅ 已保存', 'success');
    loadTasks();
    showTaskDetail(id);
  }).catch(function(e){ showToast('❌ 保存失败', 'error'); });
}

function deleteTask(id) {
  if (!confirm('确定删除此任务？')) return;
  apiFetch('/api/tasks/' + encodeURIComponent(id), {method: 'DELETE'}).then(function(){
    showToast('✅ 已删除', 'success');
    _currentTaskId = null;
    loadTasks();
    closeTaskDetail();
  }).catch(function(e){ showToast('❌ 删除失败', 'error'); });
}

function closeTaskDetail() {
  _currentTaskId = null;
  renderTaskList(_tasksData);
  var titleEl = document.getElementById('tasksRightTitle');
  if (titleEl) titleEl.textContent = '任务详情';
  var contentEl = document.getElementById('tasksRightContent');
  if (contentEl) {
    contentEl.innerHTML = '<div class="module-welcome">'
      + '<div class="module-welcome-icon">✅</div>'
      + '<div class="module-welcome-title">任务管理</div>'
      + '<div class="module-welcome-subtitle">从左侧选择一个任务查看详情，或新建任务</div>'
      + '</div>';
  }
}

// ========== 达人库 ==========
var _talentData = { talents: [], total: 0 };
var _talentCurrentId = null;
// ★ feat/sb2-dense-views: 视图切换 ('table' | 'card'), 默认表格 (老大指令), localStorage 持久化
var _talentViewMode = (function(){
  try { return localStorage.getItem('sb2_talentViewMode') || 'table'; } catch(e){ return 'table'; }
})();
var _talentCooperation = '';
var _talentCategory = '';
var _talentSearchKw = '';
var _talentCurrentSort = '';
var _talentPage = 1;
var _talentPageSize = 20;
var _talentTab = 'overview';
var _talentSearchTimer = null;
var _talentAllCategories = [];
var _talentActiveTab = 'analyzed';
var _talentIncludeDemo = false;  // 主库 Tab 是否包含演示种子数据(status='demo')，默认 false
var _talentRatingFilter = '';    // 评级筛选：''/A/B/C/D/none
var _talentCategoryFilter = '';  // 类目筛选：''/服饰/美妆/运动/食品/户外/生活方式
var _analyzedList = [];
var _analyzedPage = 1;
var _analyzedPageSize = 20;
var _analyzedSearchKw = '';
var _analyzedSearchTimer = null;

function openInfluencerLibrary() { switchModule('influencers'); }
function closeInfluencerLibrary() { switchModule('messages'); }

var _TALENT_STATUS_MAP = {
  following: { label: '关注', cls: 'talents-status-following' },
  cooperating: { label: '已合作', cls: 'talents-status-cooperating' },
  communicating: { label: '沟通中', cls: 'talents-status-communicating' },
  available: { label: '可合作', cls: 'talents-status-available' },
  resting: { label: '暂休', cls: 'talents-status-resting' },
  blacklist: { label: '黑名单', cls: 'talents-status-blacklist' },
  archived: { label: '已归档', cls: 'talents-status-archived' }
};
var _TALENT_STATUS_OPTIONS = [
  { value: 'available', label: '可合作' },
  { value: 'following', label: '关注' },
  { value: 'communicating', label: '沟通中' },
  { value: 'cooperating', label: '已合作' },
  { value: 'resting', label: '暂休' },
  { value: 'blacklist', label: '黑名单' },
  { value: 'archived', label: '已归档' }
];

// 评级徽章色板：A 绿 / B 蓝 / C 灰 / D 红；其他值降级为灰并展示原文
var _TALENT_RATING_COLORS = { A: '#10B981', B: '#3B82F6', C: '#6B7280', D: '#EF4444' };
// 卡片 V3 评级配色：色条 + 头像底色（C 黄底配深色字，未评级中性灰）
var _CARD_RATING_STYLE = {
  A: { bg: '#34C759', fg: '#ffffff' },
  B: { bg: '#1677ff', fg: '#ffffff' },
  C: { bg: '#FFCC00', fg: '#3a3000' },
  D: { bg: '#8E8E93', fg: '#ffffff' }
};
function _renderTalentRatingBadge(rating) {
  if (!rating) return '';
  var r = String(rating).trim().toUpperCase();
  if (!r) return '';
  var first = r.charAt(0);
  if (!_TALENT_RATING_COLORS[first]) return '';
  var label = ({A:'A级',B:'B级',C:'C级',D:'D级'})[first] || (first + '级');
  return '<span class="rating-chip" data-rating="' + first + '" title="评级 ' + escapeAttr(r) + '">' + escapeHtml(label) + '</span>';
}

// 卡片底部合作建议条：取自 cooperation_status，有值则彩色高亮，无值不渲染
function _renderTalentCooperationAdvice(status) {
  if (!status) return '';
  var st = _TALENT_STATUS_MAP[status] || _TALENT_STATUS_MAP['available'];
  return '<div class="talent-cooperation-advice">'
    + '<span class="talent-cooperation-advice-label">合作建议</span>'
    + '<span class="talents-status-tag ' + st.cls + '">' + escapeHtml(st.label) + '</span>'
    + '</div>';
}

// ★ ui/talent-detail-redesign-v2-cleanup: 4 个假数据生成函数已删除 (老大 2026-09-23)
//   - _hashString: 字符串 hash 当种子 (sin/linear congruential)
//   - _sparkFromSeed: KPI 6 宫格 sparkline mini 假数据
//   - _renderSparkSVG: 渲染 sparkline SVG
//   - _trendFromArr: sparkline 算 up/down/flat + pct
//   全部 commit 1 干掉, KPI 卡只画 value + label
//
// 注: _sparkFromSeedP / _renderSparkSVGP / _trendFromArrP (P 后缀) 是 products 模块独立函数,
//   仍在 L29264+ 使用, 不在本 commit 范围 (产品模块的 mid-card sparkline 由产品 redesign 处理)
// 注: _getTalentMockData 保留 (其他面板依赖字段 fallback 包装, 但 mock 不再含假数据生成)

function _getTalentMockData(t) {
  return {
    category: (typeof t.category === 'string' ? t.category : '') || (typeof t.fan_category === 'string' ? t.fan_category : ''),
    city: t.city || '',
    level: t.level || '',
    risk_rating: t.risk_rating || '',
    ai_tags: (t.ai_tags && t.ai_tags.length) ? t.ai_tags : [],
    real_name: t.real_name || '',
    phone: t.phone || t.contact_phone || '',
    wechat: t.wechat || t.contact_wechat || '',
    email: t.email || t.contact_email || '',
    content_style: t.content_style || t.contentStyle || '',
    product_count: t.product_count || t.total_products || 0,
    average_price: t.average_price || 0,
    total_gmv: t.total_gmv || 0,
    ai_analysis: t.ai_analysis || t.ai_summary || '',
    fans_profile: (t.fans_profile && Object.keys(t.fans_profile).length) ? t.fans_profile : {}
  };
}

function _formatRiskLabel(risk) {
  var map = { low: '低风险', medium: '中风险', high: '高风险' };
  return map[risk] || risk || '未评估';
}

function _renderStatusTag(status, opts) {
  opts = opts || {};
  var st = _TALENT_STATUS_MAP[status] || _TALENT_STATUS_MAP['available'];
  var cls = st.cls + (opts.clickable ? ' clickable' : '');
  var extra = opts.extra || '';
  return '<span class="talents-status-tag ' + cls + '"' + extra + '>' + st.label + '</span>';
}

function loadTalents(page) {
  if (page) _talentPage = page;
  var offset = (_talentPage - 1) * _talentPageSize;
  var qs = ['limit=' + _talentPageSize, 'offset=' + offset];
  if (_talentCooperation) qs.push('cooperation=' + encodeURIComponent(_talentCooperation));
  if (_talentCategory) qs.push('category=' + encodeURIComponent(_talentCategory));
  if (_talentSearchKw) qs.push('q=' + encodeURIComponent(_talentSearchKw));
  if (_talentCurrentSort) qs.push('sort=' + encodeURIComponent(_talentCurrentSort));
  if (_talentRatingFilter) qs.push('rating=' + encodeURIComponent(_talentRatingFilter));
  if (_talentCategoryFilter) qs.push('talent_category=' + encodeURIComponent(_talentCategoryFilter));
  // 演示种子数据开关：默认关，UI 勾选时传 1，后端把 status='demo' 也纳入
  qs.push('include_demo=' + (_talentIncludeDemo ? '1' : '0'));
  var url = '/api/talents?' + qs.join('&');
  var itemsEl = document.getElementById('talentsMidItems');
  if (itemsEl) itemsEl.innerHTML = renderSkeleton('list', 6);
  apiFetch(url).then(function(r){return r.json();}).then(function(data){
    _talentData.talents = sortTalentsLocally(data.talents || [], _talentCurrentSort);
    _talentData.total = data.total || 0;
    renderTalentList(_talentData.talents);
    renderTalentPagination(_talentData.total, _talentPage, _talentPageSize);
    if (!_talentCurrentId) showTalentEmptyState();
  }).catch(function(e){
    console.error('[loadTalents] 渲染失败:', e);
    if (itemsEl) itemsEl.innerHTML = '<div class="products-empty"><div class="products-empty-text">加载失败</div></div>';
  });
}

function sortTalentsLocally(talents, sort) {
  if (!talents || !talents.length) return talents;
  // ★ 默认排序(无 sort 或 sort=='default'): A → B → C → D → unrated, 同评级按粉丝降序
  if (!sort || sort === 'default') {
    var order = {A: 0, B: 1, C: 2, D: 3, unrated: 4};
    return talents.slice().sort(function(a, b){
      // ★ 修 indexOf('') === 0 的 JS 老坑:level 为空时必须显式返回 'unrated',否则 ra='' 排第一
      var _a = String(a.level || '').charAt(0).toUpperCase();
      var _b = String(b.level || '').charAt(0).toUpperCase();
      var ra = (_a && 'ABCD'.indexOf(_a) >= 0) ? _a : 'unrated';
      var rb = (_b && 'ABCD'.indexOf(_b) >= 0) ? _b : 'unrated';
      var oa = order[ra] !== undefined ? order[ra] : 4;
      var ob = order[rb] !== undefined ? order[rb] : 4;
      if (oa !== ob) return oa - ob;
      return (b.followers || 0) - (a.followers || 0);
    });
  }
  // 兜底空值 (合并 upstream 与 stashed changes 双侧保护)
  if (!talents || !talents.length) return talents;
  var list = talents.slice();
  switch (sort) {
    case 'followers_desc':
      list.sort(function(a,b){ return (b.followers || 0) - (a.followers || 0); });
      break;
    case 'products_desc':
      list.sort(function(a,b){ return (b.total_products || 0) - (a.total_products || 0); });
      break;
    case 'gmv_desc':
      list.sort(function(a,b){ return (b.total_gmv || 0) - (a.total_gmv || 0); });
      break;
  }
  return list;
}

function selectTalentSort(sort) {
  _talentCurrentSort = sort || '';
  _talentPage = 1;
  var sel = document.getElementById('talentsMidSort');
  if (sel) sel.value = _talentCurrentSort;
  loadTalents();
}

function onTalentSearch(val) {
  clearTimeout(_talentSearchTimer);
  _talentSearchTimer = setTimeout(function(){
    _talentSearchKw = val;
    _talentPage = 1;
    loadTalents();
  }, 250);
}

// "含演示数据" 开关：勾选时主库列表把 status='demo' 也纳入；用于搜索演示种子
function onTalentIncludeDemoChange(checked) {
  _talentIncludeDemo = !!checked;
  _talentPage = 1;
  loadTalents();
}

// 评级筛选：A/B/C/D 命中 level 字段首字符；'none' 命中空值；'' 不过滤
function filterTalentsByRating(val) {
  _talentRatingFilter = val || '';
  _talentPage = 1;
  loadTalents();
}

// 类目筛选：精确匹配 talents.category 字段（服饰/美妆/运动/食品/户外/生活方式）；'' 不过滤
function filterTalentsByCategory(val) {
  _talentCategoryFilter = val || '';
  _talentPage = 1;
  loadTalents();
}

function selectTalentCooperation(status) {
  _talentCooperation = status;
  _talentPage = 1;
  document.querySelectorAll('#talentsCooperationFilter .segmented-item').forEach(function(el){ el.classList.toggle('active', el.dataset.cooperation === status); });
  loadTalents();
}

function recommendTalentsAI() {
  showToast('✨ AI 推荐达人功能开发中');
}

function renderTalentList(talents) {
  // ★ feat/sb2-dense-views: 视图分发器 — 表格 (默认) / 卡片 (可回退)
  // 接口签名不变 (输入 talents 数组), 内部按 _talentViewMode 分发到:
  //   - 'table' → renderTalentListDense (sb2 高密度表格)
  //   - 'card'  → renderTalentListCard (老卡片逻辑完整保留)
  var el = document.getElementById('talentsMidItems');
  var countEl = document.getElementById('talentsMidCount');
  if (countEl) countEl.textContent = '共 ' + (_talentData.total || 0) + ' 条';
  if (!el) return;
  if (!talents || talents.length === 0) {
    el.innerHTML = '<div class="products-empty"><div class="products-empty-icon">🎙️</div><div class="products-empty-text">暂无达人</div><button class="products-empty-btn" onclick="createNewTalent()">录入达人</button></div>';
    return;
  }
  // 同步 segmented-control 选中态
  if (typeof _syncTalentViewSwitch === 'function') _syncTalentViewSwitch();
  if (_talentViewMode === 'table') {
    renderTalentListDense(talents);
  } else {
    renderTalentListCard(talents);
  }
}

// ★ feat/sb2-dense-views: 旧卡片渲染 (原 renderTalentList 主体逻辑, 完整保留可回退)
function renderTalentListCard(talents) {
  var el = document.getElementById('talentsMidItems');
  if (!el) return;
  // 平台 chip 颜色映射（设计系统 --platform-* token）
  var PLATFORM_COLOR = {
    '抖音': '#FE2C55',
    '快手': '#FF4906',
    '小红书': '#FF2442',
    'B站': '#00AEEC',
    '视频号': '#07C160'
  };
  var html = '';
  talents.forEach(function(t){
    var mock = _getTalentMockData(t);
    // 评级：取 level/ai_rating 首字符，归一到 A/B/C/D；无值时降级为 D（保持左色条+头像底色可见）
    var rating = String(t.level || t.ai_rating || 'D').trim().charAt(0).toUpperCase();
    if ('ABCD'.indexOf(rating) < 0) rating = 'D';
    // 合作状态：映射到设计系统的 data-status 值
    var status = t.cooperation_status || 'available';
    // 头像内容：有图用图，无图用评级色背景的首字
    var avatarInner = t.avatar
      ? '<img src="' + escapeAttr(t.avatar) + '" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">'
      : '<span>' + escapeHtml((t.name || '?').charAt(0)) + '</span>';
    // 平台 chip（color 从映射取，无则用主题默认色）
    var platform = t.platform || '抖音';
    var platformColor = PLATFORM_COLOR[platform] || '';
    var platformChip = platformColor
      ? '<span class="chip chip-platform" style="--chip-color:' + platformColor + ';">' + escapeHtml(platform) + '</span>'
      : '<span class="chip chip-platform">' + escapeHtml(platform) + '</span>';
    // 类目 chips（按 / , 拆，最多 2 个）
    var catSource = (typeof t.category === 'string' ? t.category : '') || (typeof t.fan_category === 'string' ? t.fan_category : '') || (typeof mock.category === 'string' ? mock.category : '') || '';
    var categories = catSource.split(/[\/，,]/).map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 2);
    var catChips = categories.map(function(c){ return '<span class="chip">' + escapeHtml(c) + '</span>'; }).join('');
    // 数字
    var followersText = formatFollowers(t.followers || 0).replace('w', '万');
    var gmvRaw = t.total_gmv || mock.total_gmv || 0;
    var gmvText = gmvRaw >= 10000 ? ((gmvRaw / 10000).toFixed(1).replace(/\.0$/, '') + '万') : (gmvRaw > 0 ? formatNumber(gmvRaw) : '');
    var engagement = (t.video_interaction_rate || '').toString().replace(/[^\d.]/g, '');
    // 状态徽章 label (dev/feat: talents 修复 #3 — 补 resting/archived; 与 _TALENT_STATUS_MAP 对齐)
    var stMap = { following: '关注', cooperating: '已合作', communicating: '沟通中', available: '可合作', resting: '暂休', blacklist: '黑名单', archived: '已归档' };
    var statusLabel = stMap[status] || status;
    // AI 匹配分（暂用占位，0 分时省略前缀）
    var aiScore = t.ai_rating && ['S','A','B','C'].indexOf(t.ai_rating) >= 0
      ? (4 - ['S','A','B','C'].indexOf(t.ai_rating)) * 25
      : 0;
    var metaText = aiScore ? 'AI 匹配 ' + aiScore + '%' : '近期无报告';
    // 卡片根
    html += '<div class="talent-card' + (_talentCurrentId === t.id ? ' active' : '') + '"'
      + ' data-id="' + escapeAttr(t.id) + '"'
      + ' data-rating="' + rating + '"'
      + ' data-status="' + escapeAttr(status) + '"'
      + ' onclick="selectTalentItem(\'' + escapeAttr(t.id).replace(/'/g,"\\'") + '\')">';
    // 头像：内联 --rating-color 让设计系统 .talent-card .avatar { background: var(--rating-color) } 生效
    html += '<div class="avatar" style="--rating-color: var(--color-rating-' + rating.toLowerCase() + ');">' + avatarInner + '</div>';
    // meta：名字 + chips
    html += '<div class="meta">';
    html += '<div class="name">' + escapeHtml(t.name || '-') + '</div>';
    html += '<div class="chips">' + platformChip + catChips + '</div>';
    html += '</div>';
    // 3 列 metrics（数字 + 可选量级单位 + 标签）
    html += '<div class="metrics">';
    // 粉丝量：followersText 已带"万"后缀，不再加重复单位
    html += '<div class="metric"><span class="metric-primary">' + followersText + '</span><div class="metric-label">粉丝量</div></div>';
    if (engagement) {
      html += '<div class="metric"><span class="metric-primary">' + engagement + '</span><span class="unit">%</span><div class="metric-label">互动率</div></div>';
    } else {
      html += '<div class="metric"><span class="metric-secondary">-</span><div class="metric-label">互动率</div></div>';
    }
    if (gmvText) {
      html += '<div class="metric"><span class="metric-primary">' + gmvText + '</span><div class="metric-label">带货 GMV</div></div>';
    } else {
      html += '<div class="metric"><span class="metric-secondary">-</span><div class="metric-label">带货 GMV</div></div>';
    }
    html += '</div>';
    // footer
    html += '<div class="footer">';
    html += '<span class="meta-text">' + escapeHtml(metaText) + '</span>';
    html += '<span class="badge-status" data-status="' + escapeAttr(status) + '">' + escapeHtml(statusLabel) + '</span>';
    html += '</div>';
    html += '</div>';
  });
  el.innerHTML = html;
}

// ★ feat/sb2-dense-views: 视图切换 (表格/卡片), 默认表格 (老大指令)
function setTalentViewMode(mode) {
  if (mode !== 'table' && mode !== 'card') return;
  _talentViewMode = mode;
  try { localStorage.setItem('sb2_talentViewMode', mode); } catch(e){}
  // 同步 segmented-control 选中态
  _syncTalentViewSwitch();
  // 重渲染当前达人列表 (不重新 fetch, 用现有数据)
  if (_talentData && _talentData.talents) {
    renderTalentList(_talentData.talents);
  }
}

function _syncTalentViewSwitch() {
  var btns = document.querySelectorAll('#talentsViewSwitch .segmented-item');
  btns.forEach(function(b){
    b.classList.toggle('active', b.dataset.view === _talentViewMode);
  });
}

// ★ feat/sb2-dense-views: 达人库 sb2 高密度表格渲染
// 列: 头像/名字(宽) | 平台 | 类目 | 粉丝(等宽) | 带货GMV(等宽) | GPM 进度条 | 评级 chip | 状态
// 接口不变: 输入 talents 数组 (后端 GET /api/talents 返回结构)
// 字段全部真实, 缺值显示 '-' (老大硬约束 #2), 禁止编造
function renderTalentListDense(talents) {
  var el = document.getElementById('talentsMidItems');
  if (!el) return;
  // 平台颜色 (与卡片视图同源)
  var PLATFORM_COLOR = {
    '抖音': '#FE2C55', '快手': '#FF4906', '小红书': '#FF2442',
    'B站': '#00AEEC', '视频号': '#07C160'
  };
  // 状态映射
  var stMap = { following: '关注', cooperating: '已合作', communicating: '沟通中', available: '可合作', resting: '暂休', blacklist: '黑名单', archived: '已归档' };

  var html = '';
  html += '<div class="sb2-talents-table-wrap">';
  html += '<table class="sb2-talents-table">';
  html += '<thead><tr>';
  html += '<th class="sb2-col-name">达人</th>';
  html += '<th class="sb2-col-platform">平台</th>';
  html += '<th class="sb2-col-category">类目</th>';
  html += '<th class="sb2-col-num sb2-num">粉丝</th>';
  html += '<th class="sb2-col-num sb2-num">带货 GMV</th>';
  html += '<th class="sb2-col-gpm">GPM</th>';
  html += '<th class="sb2-col-rating">评级</th>';
  html += '<th class="sb2-col-status">状态</th>';
  html += '</tr></thead>';
  html += '<tbody>';

  talents.forEach(function(t){
    var mock = _getTalentMockData(t);
    var isActive = _talentCurrentId === t.id;

    // 1. 头像 + 名字
    var avatarHtml = t.avatar
      ? '<img class="sb2-talents-avatar" src="' + escapeAttr(t.avatar) + '" alt="">'
      : '<span class="sb2-talents-avatar sb2-talents-avatar-fallback">' + escapeHtml((t.name || '?').charAt(0)) + '</span>';
    var nameText = t.name || '-';
    var douyinText = t.douyin_id ? '<span class="sb2-talents-douyin">' + escapeHtml(t.douyin_id) + '</span>' : '';

    // 2. 平台 chip
    var platform = t.platform || '';
    var platformChip;
    if (platform) {
      var pColor = PLATFORM_COLOR[platform] || '';
      platformChip = pColor
        ? '<span class="sb2-tag sb2-tag-platform" style="--sb2-platform-color:' + pColor + ';">' + escapeHtml(platform) + '</span>'
        : '<span class="sb2-tag sb2-tag-platform">' + escapeHtml(platform) + '</span>';
    } else {
      platformChip = '<span class="sb2-tag sb2-tag-empty">-</span>';
    }

    // 3. 类目 chips (按 / , 拆, 最多 2 个)
    var catSource = (typeof t.category === 'string' ? t.category : '') || (typeof t.fan_category === 'string' ? t.fan_category : '') || '';
    var categories = catSource.split(/[\/，,]/).map(function(s){ return s.trim(); }).filter(Boolean).slice(0, 2);
    var catChips = categories.length
      ? categories.map(function(c){ return '<span class="sb2-tag">' + escapeHtml(c) + '</span>'; }).join('')
      : '<span class="sb2-tag sb2-tag-empty">-</span>';

    // 4. 粉丝 (等宽数字, tabular-nums)
    var followersVal = t.followers || 0;
    var followersText = followersVal > 0 ? formatFollowers(followersVal).replace('w', '万') : '-';

    // 5. 带货 GMV (等宽数字, 真实数据, 无值显 '-')
    var gmvRaw = t.total_gmv || 0;
    var gmvText = gmvRaw > 0
      ? (gmvRaw >= 10000 ? (gmvRaw / 10000).toFixed(1).replace(/\.0$/, '') + '万' : formatNumber(gmvRaw))
      : '-';

    // 6. GPM 内嵌进度条 — 用 video_gpm (短视频 GPM, 0-1000 区间常见), 真实数据
    //    进度条宽度按 video_gpm / 1000 * 100 算 (0-100% 范围, 超过 100% 按 100% 截断)
    var videoGpm = Number(t.video_gpm || 0);
    var liveGpm = Number(t.live_gpm || 0);
    var gpmMax = Math.max(videoGpm, liveGpm, 1);
    var gpmPct = Math.min(100, Math.round((videoGpm / 1000) * 100));
    var gpmBar;
    if (videoGpm > 0 || liveGpm > 0) {
      gpmBar = '<div class="sb2-gpm-bar" title="短视频 GPM ' + videoGpm.toFixed(1) + ' / 直播 GPM ' + liveGpm.toFixed(1) + '">'
        + '<div class="sb2-gpm-bar-fill" style="width:' + gpmPct + '%;"></div>'
        + '<span class="sb2-gpm-bar-text">' + (videoGpm > 0 ? videoGpm.toFixed(0) : (liveGpm > 0 ? liveGpm.toFixed(0) + '(L)' : '-')) + '</span>'
        + '</div>';
    } else {
      gpmBar = '<div class="sb2-gpm-bar sb2-gpm-bar-empty"><span class="sb2-gpm-bar-text">-</span></div>';
    }

    // 7. 评级 chip (S/A/B/C/D, 读 ai_rating 真实字段, 缺值显 '-' 或空 D)
    var rating = String(t.ai_rating || '').trim().charAt(0).toUpperCase();
    var ratingHtml;
    if (rating && 'SABCD'.indexOf(rating) >= 0) {
      ratingHtml = '<span class="sb2-rating-chip sb2-rating-' + rating + '" data-rating="' + rating + '">' + rating + '</span>';
    } else {
      ratingHtml = '<span class="sb2-rating-chip sb2-rating-empty">-</span>';
    }

    // 8. 状态
    var status = t.cooperation_status || '';
    var statusLabel = stMap[status] || status || '-';
    var statusHtml = status
      ? '<span class="sb2-status-tag" data-status="' + escapeAttr(status) + '">' + escapeHtml(statusLabel) + '</span>'
      : '<span class="sb2-status-tag sb2-tag-empty">-</span>';

    // 行
    html += '<tr class="sb2-talents-row' + (isActive ? ' active' : '') + '"'
      + ' data-id="' + escapeAttr(t.id) + '"'
      + ' data-rating="' + escapeAttr(rating) + '"'
      + ' data-status="' + escapeAttr(status) + '"'
      + ' onclick="selectTalentItem(\'' + escapeAttr(t.id).replace(/'/g,"\\'") + '\')">';
    html += '<td class="sb2-col-name"><div class="sb2-talents-name-wrap">' + avatarHtml + '<div class="sb2-talents-name-meta"><div class="sb2-talents-name">' + escapeHtml(nameText) + '</div>' + douyinText + '</div></div></td>';
    html += '<td class="sb2-col-platform">' + platformChip + '</td>';
    html += '<td class="sb2-col-category"><div class="sb2-tags-wrap">' + catChips + '</div></td>';
    html += '<td class="sb2-col-num sb2-num">' + escapeHtml(followersText) + '</td>';
    html += '<td class="sb2-col-num sb2-num">' + escapeHtml(gmvText) + '</td>';
    html += '<td class="sb2-col-gpm">' + gpmBar + '</td>';
    html += '<td class="sb2-col-rating">' + ratingHtml + '</td>';
    html += '<td class="sb2-col-status">' + statusHtml + '</td>';
    html += '</tr>';
  });

  html += '</tbody></table></div>';
  el.innerHTML = html;
}

function _renderTalentStatusDropdown(talentId, currentStatus, inList) {
  var st = _TALENT_STATUS_MAP[currentStatus] || _TALENT_STATUS_MAP['available'];
  var dropdownId = 'talent-status-dd-' + talentId;
  var html = '<span class="talents-status-dropdown" id="' + dropdownId + '" onclick="event.stopPropagation()">';
  html += '<span class="talents-status-tag ' + st.cls + ' clickable" onclick="toggleTalentStatusDropdown(\'' + talentId + '\')">' + st.label + '</span>';
  html += '<div class="talents-status-dropdown-menu" id="' + dropdownId + '-menu">';
  _TALENT_STATUS_OPTIONS.forEach(function(opt){
    html += '<button class="talents-status-dropdown-item" onclick="quickUpdateTalentStatus(\'' + talentId + '\', \'' + opt.value + '\')">' + opt.label + '</button>';
  });
  html += '</div></span>';
  return html;
}

function toggleTalentStatusDropdown(talentId) {
  var menu = document.getElementById('talent-status-dd-' + talentId + '-menu');
  if (!menu) return;
  var isActive = menu.classList.contains('active');
  document.querySelectorAll('.talents-status-dropdown-menu').forEach(function(m){ m.classList.remove('active'); });
  if (!isActive) menu.classList.add('active');
}

document.addEventListener('click', function(e){
  if (!e.target.closest('.talents-status-dropdown')) {
    document.querySelectorAll('.talents-status-dropdown-menu').forEach(function(m){ m.classList.remove('active'); });
  }
});

function quickUpdateTalentStatus(talentId, status) {
  if (!talentId) return;
  var dd = document.getElementById('talent-status-dd-' + talentId);
  if (dd) dd.classList.add('updating');
  apiFetch('/api/talents/' + encodeURIComponent(talentId), {method:'PUT', body: JSON.stringify({cooperation_status: status})}).then(function(){
    showToast('✅ 状态已更新');
    var t = _talentData.talents.find(function(x){ return x.id === talentId; });
    if (t) t.cooperation_status = status;
    if (_currentTalent && _currentTalent.id === talentId) _currentTalent.cooperation_status = status;
    renderTalentList(_talentData.talents);
    if (_talentCurrentId === talentId && _currentTalent) renderTalentDetail(_currentTalent);
  }).catch(function(){ showToast('❌ 状态更新失败'); }).finally(function(){
    if (dd) dd.classList.remove('updating');
  });
}

function renderTalentPagination(total, page, pageSize) {
  var el = document.getElementById('talentsMidPagination');
  if (!el) return;
  var totalPages = Math.ceil(total / pageSize) || 1;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  var html = '';
  html += '<button onclick="loadTalents(' + (page - 1) + ')" ' + (page <= 1 ? 'disabled' : '') + '>上一页</button>';
  for (var i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
      html += '<button class="' + (i === page ? 'active' : '') + '" onclick="loadTalents(' + i + ')">' + i + '</button>';
    } else if (i === page - 2 || i === page + 2) {
      html += '<span style="color:#86909c;font-size:13px;">...</span>';
    }
  }
  html += '<button onclick="loadTalents(' + (page + 1) + ')" ' + (page >= totalPages ? 'disabled' : '') + '>下一页</button>';
  el.innerHTML = html;
}

// ===== 达人库 Tab 切换：已分析达人 / 达人主库 =====
function switchTalentMidTab(tab) {
  _talentActiveTab = tab === 'main' ? 'main' : 'analyzed';
  var isAnalyzed = _talentActiveTab === 'analyzed';
  document.querySelectorAll('.talent-tab-btn').forEach(function(btn){
    btn.classList.toggle('active', btn.dataset.tab === _talentActiveTab);
  });
  var analyzedEl = document.getElementById('talentsAnalyzedItems');
  var mainEl = document.getElementById('talentsMainItems');
  if (analyzedEl) analyzedEl.style.display = isAnalyzed ? 'flex' : 'none';
  if (mainEl) mainEl.style.display = isAnalyzed ? 'none' : 'flex';
  // 头部的搜索框/合作状态筛选/排序只适用于达人主库
  // 〔fix/sb2-side-restore commit 11〕14 轮热修: 头部的搜索框 DOM 已删 (line 14654), querySelector 返 null
  //   headerSearch 守卫 (if) 接住 null, 不会 NPE, 函数逻辑保留防回归
  //   filterRow (.talents-filter-row) 仍在, 保留
  var headerSearch = document.querySelector('.talents-mid-header .talents-mid-search');
  var filterRow = document.querySelector('.talents-mid-header .talents-filter-row');
  if (headerSearch) headerSearch.style.display = isAnalyzed ? 'none' : '';
  if (filterRow) filterRow.style.display = isAnalyzed ? 'none' : '';
  // 切换Tab即清空右侧详情，只有主动点击卡片才展示
  _talentCurrentId = null;
  _currentTalent = null;
  showTalentEmptyState();
  if (isAnalyzed) loadAnalyzedTalents();
  else loadTalents();
}

function loadAnalyzedTalents() {
  var listEl = document.getElementById('talentsAnalyzedList');
  if (listEl) listEl.innerHTML = renderSkeleton('list', 4);
  // fix/analyzed-talents-decouple: 改用语义接口 /api/talents/analyzed
  // （旧 /api/knowledge-events?entity_type=talent 要求 knowledge 模块权限，
  // 贺主管子账号 knowledge:false 会 403 且被静默吞成空）。
  // 新接口以 talents 为主、按达人可见性聚合最近一条 analysis，元素含
  // talent_id/talent_name + 完整事件字段；critical 让 403/错误走可见提示。
  apiFetch('/api/talents/analyzed?limit=200', { critical: true }).then(function(r){return r.json();}).then(function(data){
    var events = data.events || [];
    // 后端已按达人聚合（每达人最近一条 analysis）并完成可见性过滤，直接使用
    _analyzedList = events;
    renderAnalyzedPage();
    if (!_talentCurrentId) showTalentEmptyState();
  }).catch(function(){
    if (listEl) listEl.innerHTML = '<div class="products-empty"><div class="products-empty-text">加载失败（可能无权限），请稍后重试或联系管理员</div></div>';
  });
}

function _filteredAnalyzedList() {
  var kw = (_analyzedSearchKw || '').trim().toLowerCase();
  if (!kw) return _analyzedList;
  return _analyzedList.filter(function(it){
    var hay = [it.entity_id, it.title, it.conclusions, it.content_summary].join('\n').toLowerCase();
    return hay.indexOf(kw) >= 0;
  });
}

function onAnalyzedTalentSearch(val) {
  clearTimeout(_analyzedSearchTimer);
  _analyzedSearchTimer = setTimeout(function(){
    _analyzedSearchKw = val;
    _analyzedPage = 1;
    renderAnalyzedPage();
  }, 250);
}

function loadAnalyzedPage(page) {
  _analyzedPage = page;
  renderAnalyzedPage();
}

function renderAnalyzedPage() {
  var listEl = document.getElementById('talentsAnalyzedList');
  var countEl = document.getElementById('talentsMidCount');
  if (!listEl) return;
  var filtered = _filteredAnalyzedList();
  if (countEl) countEl.textContent = '共 ' + filtered.length + ' 条';
  var totalPages = Math.ceil(filtered.length / _analyzedPageSize) || 1;
  if (_analyzedPage > totalPages) _analyzedPage = totalPages;
  var start = (_analyzedPage - 1) * _analyzedPageSize;
  var pageItems = filtered.slice(start, start + _analyzedPageSize);
  renderAnalyzedPagination(filtered.length, _analyzedPage, _analyzedPageSize);
  if (!pageItems.length) {
    listEl.innerHTML = '<div class="products-empty"><div class="products-empty-icon">📊</div><div class="products-empty-text">暂无已分析达人</div></div>';
    return;
  }
  // 新接口已返回 content_full，直接渲染（旧逻辑会按事件 id 补拉 /api/knowledge-events/:id，
  // 该接口要求 knowledge 权限，ayn 等子账号会 403）
  try {
    listEl.innerHTML = pageItems.map(renderAnalyzedTalentCard).join('');
  } catch (e) {
    console.error('[AnalyzedTalents] render failed:', e);
    listEl.innerHTML = '<div class="products-empty"><div class="products-empty-text">渲染失败，请稍后重试</div></div>';
  }
}

function renderAnalyzedPagination(total, page, pageSize) {
  var el = document.getElementById('talentsAnalyzedPagination');
  if (!el) return;
  var totalPages = Math.ceil(total / pageSize) || 1;
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  var html = '';
  html += '<button onclick="loadAnalyzedPage(' + (page - 1) + ')" ' + (page <= 1 ? 'disabled' : '') + '>上一页</button>';
  for (var i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= page - 1 && i <= page + 1)) {
      html += '<button class="' + (i === page ? 'active' : '') + '" onclick="loadAnalyzedPage(' + i + ')">' + i + '</button>';
    } else if (i === page - 2 || i === page + 2) {
      html += '<span style="color:#86909c;font-size:13px;">...</span>';
    }
  }
  html += '<button onclick="loadAnalyzedPage(' + (page + 1) + ')" ' + (page >= totalPages ? 'disabled' : '') + '>下一页</button>';
  el.innerHTML = html;
}

// 从分析文本提取结构化指标：关键词与数字严格邻接 + 多候选 + 合理性校验，
// 避免抓到「粉丝量、互动率等5个维度」「涨粉1.01万」这类干扰数字；校验不过一律留空显示 '-'
var _ANALYZED_PLATFORM_MAP = { douyin: ['🎵', '抖音'], xiaohongshu: ['📕', '小红书'], bilibili: ['📺', 'B站'], kuaishou: ['⚡', '快手'], shipinhao: ['🎬', '视频号'], weibo: ['🔥', '微博'] };
function _extractAnalysisMetrics(text) {
  var res = { rating: '', followers: 0, followersText: '', interaction: '', gmv: 0, gmvText: '', category: '', platform: '' };
  text = String(text || '');
  var m;
  var ratingM = text.match(/评级[：:]\s*"?([SABCD])\s*级?"?/i) || text.match(/\b([ABCD])\s*级/);
  if (ratingM) res.rating = ratingM[1].toUpperCase();
  function cnNum(num, unit) {
    var v = parseFloat(String(num).replace(/,/g, ''));
    if (isNaN(v)) return 0;
    if (unit === '亿') v *= 100000000;
    else if (unit === '万') v *= 10000;
    return v;
  }
  function fmtCn(v) {
    if (v >= 100000000) return (v / 100000000).toFixed(2).replace(/\.?0+$/, '') + '亿';
    if (v >= 10000) return (v / 10000).toFixed(2).replace(/\.?0+$/, '') + '万';
    return String(Math.round(v));
  }
  // 粉丝量：严格邻接（冒号/空白/约近超达等修饰词），<1000 视为提取失败
  var fanRe = /粉丝(?:量|数)[：:\s]*(?:约|近|超过|超|达|已有|有|为)?\s*([\d,]+(?:\.\d+)?)\s*(亿|万)?/g;
  while ((m = fanRe.exec(text))) {
    var fv = cnNum(m[1], m[2]);
    if (fv >= 1000 && fv < 1e11) { res.followers = Math.round(fv); res.followersText = fmtCn(fv); break; }
  }
  if (!res.followers) {
    // 宽松回退：必须带 万/亿 单位（过滤「等5个维度」这类无单位干扰）
    var fanLoose = /粉丝(?:量|数)[^\d]{0,15}([\d,]+(?:\.\d+)?)\s*(亿|万)/g;
    while ((m = fanLoose.exec(text))) {
      var fv2 = cnNum(m[1], m[2]);
      if (fv2 >= 1000 && fv2 < 1e11) { res.followers = Math.round(fv2); res.followersText = fmtCn(fv2); break; }
    }
  }
  // 互动率：优先冒号邻接的结构化写法，其次排除「行业均值/基准」语境的候选
  var irCands = [];
  var irRe = /互动率([：:]?)\s*(?:约|近|达|为|平均|高达)?\s*([\d.]+)\s*%/g;
  while ((m = irRe.exec(text))) {
    var iv = parseFloat(m[2]);
    if (!(iv > 0 && iv <= 100)) continue;
    var ctx = text.slice(Math.max(0, m.index - 6), m.index);
    irCands.push({ v: m[2].replace(/\.$/, ''), colon: !!m[1], benchmark: !m[1] && /均值|行业|基准|大盘|同类|平均/.test(ctx) });
  }
  var irPick = irCands.filter(function (c) { return c.colon; })[0]
            || irCands.filter(function (c) { return !c.benchmark; })[0]
            || irCands[0];
  if (irPick) res.interaction = irPick.v + '%';
  // 带货GMV：<100 视为提取失败（过滤「场次10」「商品8」这类错位数字）
  var gmvRe = /(?:GMV|结算额|带货额|销售额)[：:\s]*(?:约|近|达|累计|月均|为)?\s*([\d,]+(?:\.\d+)?)\s*(亿|万|元)?/gi;
  while ((m = gmvRe.exec(text))) {
    var gv = cnNum(m[1], m[2] === '元' ? '' : m[2]);
    if (gv >= 100 && gv < 1e12) { res.gmv = Math.round(gv); res.gmvText = fmtCn(gv); break; }
  }
  var catM = text.match(/(?:类目|赛道|领域|垂类)[：:\s]+([^\s，,。；;：:、|（(]{2,10})/);
  if (catM) res.category = catM[1];
  var platKeys = [['抖音', 'douyin'], ['小红书', 'xiaohongshu'], ['哔哩哔哩', 'bilibili'], ['B站', 'bilibili'], ['快手', 'kuaishou'], ['视频号', 'shipinhao'], ['微博', 'weibo']];
  for (var i = 0; i < platKeys.length; i++) {
    if (text.indexOf(platKeys[i][0]) >= 0) { res.platform = platKeys[i][1]; break; }
  }
  return res;
}

// 分析事件文本数据源：content_full > content_summary > 非空 conclusions
// （conclusions 常为 '{}' 空 JSON 字符串，truthy 但无内容，不能直接参与回退）
function _analyzedEventText(ev) {
  if (!ev) return '';
  if (ev.content_full) return ev.content_full;
  if (ev.content_summary) return ev.content_summary;
  var c = ev.conclusions;
  if (!c) return '';
  if (typeof c !== 'string') { try { c = JSON.stringify(c); } catch (e) { return ''; } }
  var t = String(c).trim();
  if (!t || t === '{}' || t === '[]' || t === 'null') return '';
  return t;
}

function renderAnalyzedTalentCard(item) {
  var text = _analyzedEventText(item);
  var metrics = _extractAnalysisMetrics(text);
  var rating = metrics.rating || 'unrated';
  // 名称优先取 talent_name（新接口以 talents 为主聚合）；
  // 兼容旧事件形态：entity_id 的 'name:XXX' 部分，title 需去掉「截图识别原始数据：」前缀
  var rawId = String(item.talent_id || item.entity_id || '');
  var name = '';
  if (item.talent_name) name = String(item.talent_name).trim();
  if (!name) {
    var _eid = String(item.entity_id || '');
    if (_eid.indexOf('name:') === 0) name = _eid.slice(5).trim();
  }
  if (!name && item.title) name = String(item.title).replace(/^截图识别原始数据[：:]\s*/, '').trim();
  if (!name) name = rawId || '未知达人';
  var timeText = item.created_at ? new Date(item.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
  var ratingLabel = ({A:'A级',B:'B级',C:'C级',D:'D级',unrated:'待评级'})[rating];
  // 平台 key 归一(对齐 renderTalentList)
  var _platRaw = metrics.platform || '';
  var _platformKey = ({'douyin':'douyin','抖音':'douyin','kuaishou':'kuaishou','快手':'kuaishou','xiaohongshu':'xiaohongshu','小红书':'xiaohongshu','bilibili':'bilibili','B站':'bilibili','wechat':'wechat','视频号':'wechat'})[_platRaw] || _platRaw;
  var platInfo = _ANALYZED_PLATFORM_MAP[metrics.platform];
  // 头像字符(未评级显 '?')
  var _avChar = (rating === 'unrated') ? '?' : (name.charAt(0) || '?').toUpperCase();
  // ★ v2 结构:与 renderTalentList 对齐(.talent-card + .avatar-neutral + .rating-chip + .chip-subtle)
  var html = '<div class="talent-card"';
  html += ' data-id="' + escapeAttr(rawId) + '"';
  html += ' data-rating="' + rating + '"';
  html += ' data-status="analyzed"';
  html += ' onclick="openAnalyzedTalentDetail(\'' + escapeAttr(rawId).replace(/'/g, "\\'") + '\', \'' + escapeAttr(name).replace(/'/g, "\\'") + '\')">';
  // 头像(v2:白底+评级色 1.5px 描边)
  html += '<div class="avatar-neutral" data-rating="' + rating + '"><span>' + escapeHtml(_avChar) + '</span></div>';
  // meta:名字 + 评级 chip
  html += '<div class="meta">';
  html += '<div class="name">' + escapeHtml(name) + '</div>';
  html += '<div class="chips">';
  html += '<span class="rating-chip" data-rating="' + rating + '">' + ratingLabel + '</span>';
  // 平台 chip (subtle)
  if (platInfo) {
    html += '<span class="chip-subtle" data-platform="' + escapeAttr(_platformKey) + '">' + escapeHtml(platInfo[1] || _platRaw) + '</span>';
  }
  // 类目 chip (subtle)
  if (metrics.category) html += '<span class="chip-subtle">' + escapeHtml(metrics.category) + '</span>';
  html += '</div></div>';
  // 3 列 metrics(对齐 renderTalentList)
  html += '<div class="metrics">';
  html += '<div class="metric"><span class="metric-primary">' + (metrics.followersText ? escapeHtml(metrics.followersText) : '-') + '</span><div class="metric-label">粉丝量</div></div>';
  html += '<div class="metric"><span class="metric-primary">' + (metrics.interaction ? escapeHtml(metrics.interaction) : '-') + '</span><div class="metric-label">互动率</div></div>';
  html += '<div class="metric"><span class="metric-primary">' + (metrics.gmvText ? escapeHtml(metrics.gmvText) : '-') + '</span><div class="metric-label">带货GMV</div></div>';
  html += '</div>';
  // 底部:AI 分析时间
  html += '<div class="footer">';
  html += '<span class="badge-status" data-status="analyzed">' + escapeHtml(timeText) + '</span>';
  html += '</div>';
  html += '</div>';
  return html;
}

// 点击已分析达人卡片：新接口元素带 talent_id（一定在 talents 表），
// 直接选中主库达人；兼容旧 name:XXX 游离事件（无 talents row）走 stub 渲染
function openAnalyzedTalentDetail(entityId, name) {
  var item = _analyzedList.find(function(it){
    return String(it.talent_id || it.entity_id || '') === String(entityId || '');
  });
  if (item && item.talent_id) {
    _talentCurrentId = item.talent_id;
    selectTalentItem(item.talent_id);
    return;
  }
  var key = String(entityId || '').replace(/^name:/, '') || name || '';
  if (!key) return;
  apiFetch('/api/talents?q=' + encodeURIComponent(key) + '&limit=50').then(function(r){ return r.json(); }).then(function(data){
    var list = data.talents || [];
    var t = list.find(function(x){ return x.id === entityId; })
         || list.find(function(x){ return x.name === key || x.name === name; })
         || list[0];
    if (t) { selectTalentItem(t.id); return; }
    // 主库查不到：用列表元素（新接口已带 content_full）构造最小达人对象渲染右侧面板
    var ev = _analyzedList.find(function(it){ return String(it.entity_id || '') === String(entityId || ''); })
          || _analyzedList.find(function(it){ return String(it.entity_id || '').replace(/^name:/, '') === key; });
    var stub = _buildAnalyzedTalentStub(entityId, name, ev);
    _talentCurrentId = stub.id;
    renderTalentDetail(stub);
  }).catch(function(){ showToast('加载失败'); });
}

// 从最新 analysis 事件提取字段，构造兼容右侧面板的最小达人对象（_analyzedOnly 标记非主库来源）
function _buildAnalyzedTalentStub(entityId, name, ev) {
  var text = _analyzedEventText(ev);
  var metrics = _extractAnalysisMetrics(text);
  return {
    id: String(entityId || ''),  // 分析档案面板用 entity_id 反查 knowledge_events
    name: name || String(entityId || '').replace(/^name:/, '') || '未知达人',
    followers: metrics.followers,
    level: metrics.rating,
    ai_rating: metrics.rating,
    ai_analysis: text,
    video_interaction_rate: metrics.interaction,
    total_gmv: metrics.gmv,
    cooperation_status: '',
    category: metrics.category,
    ai_tags: [],
    event_type: ev ? ev.event_type : 'analysis',
    _analyzedOnly: true
  };
}

function selectTalentItem(id) {
  _talentCurrentId = id;
  apiFetch('/api/talents/' + encodeURIComponent(id)).then(function(r){return r.json();}).then(function(t){
    renderTalentDetail(t);
    document.querySelectorAll('.talent-card').forEach(function(el){ el.classList.remove('active'); });
    var activeEl = document.querySelector('.talent-card[data-id="' + escapeAttr(id) + '"]');
    if (activeEl) activeEl.classList.add('active');
  }).catch(function(e){ console.error('[selectTalentItem]', e); showToast('加载失败: ' + ((e && e.message) ? e.message : String(e))); });
}

function renderTalentDetail(t) {
  hideTalentEmptyState();
  _currentTalent = t;
  var titleEl = document.getElementById('talentsRightTitle');
  if (titleEl) titleEl.textContent = t.name || '达人详情';
  // 两层架构：管理员可对子库达人（created_by 非空）执行"提升到主库"
  var promoteBtn = document.getElementById('talentPromoteBtn');
  if (promoteBtn) promoteBtn.style.display = (isAdmin() && t.created_by) ? '' : 'none';
  // 分析达人（仅 knowledge_events 有记录、不在主库）隐藏主库操作按钮
  var actionsEl = document.getElementById('talentsRightActions');
  if (actionsEl) actionsEl.style.display = t._analyzedOnly ? 'none' : '';
  // Reset tab
  _talentTab = 'overview';
  document.querySelectorAll('.talents-tab').forEach(function(tab){ tab.classList.toggle('active', tab.dataset.tab === 'overview'); });
  document.querySelectorAll('.talents-tab-panel').forEach(function(p){ p.classList.toggle('active', p.dataset.panel === 'overview'); });
  renderTalentPanelOverview(t);
  renderTalentPanelSales(t);
  renderTalentPanelFans(t);
  renderTalentPanelProducts(t);
  renderTalentPanelRecords(t);
  renderTalentPanelFollowUps(t);
  renderTalentPanelKnowledge(t);
}

function showTalentEmptyState() {
  var empty = document.getElementById('talentsEmptyState');
  var tabs = document.getElementById('talentsTabs');
  var panels = document.getElementById('talentsTabPanels');
  var header = document.getElementById('talentsRightHeader');
  if (empty) empty.style.display = 'flex';
  if (tabs) tabs.style.display = 'none';
  if (panels) panels.style.display = 'none';
  if (header) header.style.display = 'none';
}

function hideTalentEmptyState() {
  var empty = document.getElementById('talentsEmptyState');
  var tabs = document.getElementById('talentsTabs');
  var panels = document.getElementById('talentsTabPanels');
  var header = document.getElementById('talentsRightHeader');
  if (empty) empty.style.display = 'none';
  if (tabs) tabs.style.display = 'flex';
  if (panels) panels.style.display = 'block';
  if (header) header.style.display = 'flex';
}

function switchTalentTab(tab) {
  _talentTab = tab;
  document.querySelectorAll('.talents-tab').forEach(function(t){ t.classList.toggle('active', t.dataset.tab === tab); });
  document.querySelectorAll('.talents-tab-panel').forEach(function(p){ p.classList.toggle('active', p.dataset.panel === tab); });
  // ★ ui/talent-detail-redesign-v2-cleanup: GMV canvas 已删除, setTimeout 画图调用一并删
}

// 解析达人 AI 分析报告 Markdown，提取结构化字段
function parseTalentAIReport(text) {
  var result = {score:'', rating:'', summary:'', dimensions:[], highlights:[], issues:[], risks:[], cooperation:[]};
  if (!text) return result;

  var sections = {};
  var sectionRe = /##\s*(\d+)\.\s*([^\n]+)([\s\S]*?)(?=(?:##\s*\d+\.\s*[^\n]+)|$)/g;
  var m;
  while ((m = sectionRe.exec(text)) !== null) {
    sections[m[1]] = {title: m[2].trim(), body: m[3]};
  }

  // ★ ui/talent-detail-redesign-v2-cleanup commit 6 (老大 2026-09-23):
  //   检测 "预估 GMV 50万/曝光 10万/转化率 5%" 等无来源数字, 给段落加 unverified 标志
  //   渲染时整段不显示 + 提示 "无来源数据, 已隐藏" (前端防御 + 警示)
  //   根因是 Helen LLM 编造数字, 治本靠修改 Helen system_prompt 加 "禁止编造数字" 规则
  //   (后端 Mini 端在 data/agents.json 改, Mavis 不能碰 data/ 目录)
  function _hasUnverifiedNumbers(body) {
    if (!body) return false;
    // ★ ui/talent-detail-redesign-v2-cleanup commit 8 (老大 2026-09-23 review 风险):
    //   commit 6 旧 regex 模式 3/4/5 没有"预估/预计"意图前缀,会误杀真实数据:
    //     - "GMV 50万/月" (真实) 被藏 ✗
    //     - "转化率 3.5%, 行业平均 5%" (真实) 被藏 ✗
    //     - "50万 GMV, 月均带货 12场" (真实) 被藏 ✗
    //   修复: 模式 3/4/5 全部加"预估"前缀, 只藏 LLM 编造数字, 真实数据不藏
    //   老大要求"显式标记/白名单, 而不是见到数字就藏" — 意图前缀是最稳的白名单
    var patterns = [
      /预估[^\n]{0,20}(GMV|曝光|转化率|销售额|成交|GPM|佣金)/i,
      /预计[^\n]{0,20}(GMV|曝光|转化率|销售额|成交|GPM|佣金)/i,
      /预估[^\n]{0,20}\d+\s*[万千wW]/i,
      /预估[^\n]{0,20}\d+%\s*(转化率|转化|CTR|点击率)/i,
      /预估[^\n]{0,20}\d+\s*[万千wW]\s*(GMV|销售额|成交额)/i
    ];
    for (var i = 0; i < patterns.length; i++) {
      if (patterns[i].test(body)) return true;
    }
    return false;
  }

  // 1. 综合匹配度
  if (sections['1']) {
    var body = sections['1'].body;
    var scoreMatch = body.match(/综合评分[：:]?\s*(\d+(?:\.\d+)?)\s*分?/);
    if (scoreMatch) result.score = parseFloat(scoreMatch[1]);
    var ratingMatch = body.match(/匹配评级[：:]?\s*(高|中|低)/);
    if (ratingMatch) result.rating = ratingMatch[1];
    var summaryMatch = body.match(/一句话总结[：:]?\s*(.+)/);
    if (summaryMatch) result.summary = summaryMatch[1].trim();
    if (_hasUnverifiedNumbers(body)) result.summaryUnverified = true;
  }

  // 2. 六维评分表格
  if (sections['2']) {
    var tableLines = sections['2'].body.split('\n').filter(function(l){ return l.trim().startsWith('|'); });
    if (tableLines.length >= 3) {
      for (var i = 2; i < tableLines.length; i++) {
        var cells = tableLines[i].trim().slice(1, -1).split('|').map(function(c){ return c.trim(); });
        if (cells.length >= 3) {
          var s = parseFloat(cells[1]);
          result.dimensions.push({name: cells[0], score: isNaN(s) ? 0 : s, desc: cells[2]});
        }
      }
    }
  }

  // 通用列表提取
  function extractItems(body) {
    return body.split('\n').map(function(l){ return l.trim(); }).filter(function(l){
      // 跳过纯 --- 分隔符行(已用 hr 替代)
      if (/^-+$/.test(l)) return false;
      return /^[-*✓!•·]+(?:\s*[-*✓!•·]+)*\s*/.test(l);
    }).map(function(l){ return l.replace(/^[-*✓!•·]+(?:\s*[-*✓!•·]+)*\s*/, ''); });
  }

  if (sections['3']) {
    result.highlights = extractItems(sections['3'].body);
    result.highlightsUnverified = _hasUnverifiedNumbers(sections['3'].body);
  }
  if (sections['4']) {
    result.issues = extractItems(sections['4'].body);
    result.issuesUnverified = _hasUnverifiedNumbers(sections['4'].body);
  }
  if (sections['5']) {
    result.risks = extractItems(sections['5'].body);
    result.risksUnverified = _hasUnverifiedNumbers(sections['5'].body);
  }

  // 6. 合作建议 key-value
  if (sections['6']) {
    var kvRe = /^[-*]?\s*(合作方式|预期效果|触达策略)[：:]\s*(.*)$/;
    sections['6'].body.split('\n').forEach(function(l){
      var line = l.trim();
      var kv = line.match(kvRe);
      if (kv) {
        result.cooperation.push({label: kv[1], value: kv[2]});
        if (_hasUnverifiedNumbers(kv[2])) result.cooperationUnverified = true;
      }
    });
  }

  return result;
}

// 将解析后的达人 AI 报告渲染为灵邀风格卡片式 HTML
function renderTalentAIReport(text) {
  var report = parseTalentAIReport(text);
  if (report.score === '' && report.dimensions.length === 0 && report.highlights.length === 0 &&
      report.issues.length === 0 && report.cooperation.length === 0 && report.risks.length === 0) {
    return formatMessageContent(text);
  }

  // ★ fix/ai-report-unverified-precision: 精修未验证数字处理
  //   合作建议段: 只删除段落中含意图前缀 (预估/预计/大约/约/左右/大概/估算/预测/推测/假设) + 数字的句子
  //   断句: 中英文分号句号 ( ; ； . 。 ! ! ? ? \n)
  //   删除后段落为空 → 显示 "等待 AI 模型修复后重新生成"
  //   提示文案 (行内克制, 段末): "此段含未验证数字，部分已隐藏"
  //   风险点与缓解建议段不变 (老大硬约束)
  function _filterUnverifiedSentences(text) {
    if (!text) return { filtered: '', hasUnverified: false };
    var SENTENCE_SPLIT_REGEX = /([;；。.!?！？\n]+)/;
    var parts = String(text).split(SENTENCE_SPLIT_REGEX);
    // 重组: 句子 + 分隔符 配对 (parts 长度 = 2*N+1)
    var sentences = [];
    for (var i = 0; i < parts.length; i += 2) {
      var sentence = (parts[i] || '').trim();
      var sep = parts[i + 1] || '';
      if (sentence) sentences.push(sentence + sep);
    }
    if (!sentences.length) return { filtered: '', hasUnverified: false };

    var UNVERIFIED_INTENT_REGEX = /(预估|预计|大约|大概|约莫|约|左右|近似|估算|预测|推测|假设)[^\n\u3002\uff1b\uff01\uff1f]*?\d/;
    var filteredSentences = sentences.filter(function (s) { return !UNVERIFIED_INTENT_REGEX.test(s); });
    var filtered = filteredSentences.join('').trim();
    var hasUnverified = filteredSentences.length < sentences.length;
    return { filtered: filtered, hasUnverified: hasUnverified };
  }

  // ★ fix/ai-report-unverified-precision: 合作建议段行内克制提示 (段末)
  function _precisionWarningSuffix(hasUnverified) {
    if (!hasUnverified) return '';
    return ' <span class="precision-warning-inline">此段含未验证数字，部分已隐藏</span>';
  }

  // ★ ui/talent-detail-redesign-v2-cleanup commit 6 (老大 2026-09-23):
  //   unverified 段整段不渲染, 改为占位 "❓ 此段含未验证数字, 已隐藏"
  //   治本是修改 Helen system_prompt 加 "禁止编造具体数字" 规则 (后端 Mini 端)
  //   前端做防御: 隐藏具体数字段, 等后端修
  //   注: ★ fix/ai-report-unverified-precision: 仅匹配亮点/潜在问题/风险点保留此行为,
  //      合作建议段改为精修 (只删含意图前缀句子, 见下方 _filterUnverifiedSentences)
  function _renderUnverifiedPlaceholder(title) {
    return '<div class="ai-section"><div class="ai-section-title">' + escapeHtml(title) + ' <span style="font-size:11px;color:var(--color-status-lost,#FF3B30);font-weight:400;">⚠ 此段含未验证数字, 已隐藏</span></div><div class="products-empty-text">等待 AI 模型修复后重新生成</div></div>';
  }

  var html = '<div class="ai-report-body">';

  // 顶部综合匹配度横幅
  var ratingText = report.rating === '高' ? '高匹配' : report.rating === '中' ? '中匹配' : report.rating === '低' ? '低匹配' : (report.rating || '未评级');
  html += '<div class="ai-match-banner">';
  html += '<div class="ai-match-main">';
  html += '<div class="ai-match-score">' + (report.score || '-') + '<span>分</span></div>';
  html += '<div class="ai-match-info"><div class="ai-match-label">综合匹配度</div><div class="ai-match-rating">' + escapeHtml(ratingText) + '</div></div>';
  html += '</div>';
  if (report.summary) {
    if (report.summaryUnverified) {
      html += '<div class="ai-match-summary" style="background:rgba(255,59,48,0.05);border-left:3px solid var(--color-status-lost,#FF3B30);padding:8px 12px;border-radius:6px;font-size:12px;">⚠ 此段含未验证数字, 已隐藏. 等待 AI 模型修复</div>';
    } else {
      html += '<div class="ai-match-summary">' + escapeHtml(report.summary) + '</div>';
    }
  }
  html += '</div>';

  // 六维评分卡片 (6 维评分是表格格式, LLM 不会编造数字, 保留)
  if (report.dimensions.length) {
    html += '<div class="ai-dimension-grid">';
    report.dimensions.forEach(function(d){
      var level = d.score >= 80 ? 'high' : (d.score >= 60 ? 'mid' : 'low');
      html += '<div class="ai-dimension-card">';
      html += '<div class="ai-dimension-header"><div class="ai-dimension-name">' + escapeHtml(d.name) + '</div><div class="ai-dimension-score ' + level + '">' + d.score + '</div></div>';
      html += '<div class="ai-progress-bar"><div class="ai-progress-fill ' + level + '" style="width:' + d.score + '%"></div></div>';
      html += '<div class="ai-dimension-desc">' + escapeHtml(d.desc) + '</div>';
      html += '</div>';
    });
    html += '</div>';
  }

  // 匹配亮点 (含未验证数字时整段占位)
  if (report.highlightsUnverified) {
    html += _renderUnverifiedPlaceholder('匹配亮点');
  } else if (report.highlights.length) {
    html += '<div class="ai-section"><div class="ai-section-title">匹配亮点</div><ul class="ai-section-list ai-highlights">';
    report.highlights.forEach(function(item){ html += '<li>' + formatMessageInline(item) + '</li>'; });
    html += '</ul></div>';
  }

  // 潜在问题 (含未验证数字时整段占位)
  if (report.issuesUnverified) {
    html += _renderUnverifiedPlaceholder('潜在问题');
  } else if (report.issues.length) {
    html += '<div class="ai-section"><div class="ai-section-title">潜在问题</div><ul class="ai-section-list ai-issues">';
    // ★ fix/ai-issues-double-punct (2026-09-30): CSS .ai-issues li::before 已显示 '!',
    //   去掉每条 issue 文本行首自带的标点 (!！.。• 等), 避免双感叹号. risks 不受影响.
    report.issues.forEach(function(item){ html += '<li>' + formatMessageInline(String(item).replace(/^[\s!！.。•·:：]+/, '')) + '</li>'; });
    html += '</ul></div>';
  }
  // 1-2 / 2-3 / 3-4 section 之间插 1px divider(只在两段都渲染时)
  if ((report.highlights.length || report.issues.length) && (report.issues.length || report.cooperation.length)) {
    html += '<hr class="ai-section-divider">';
  }

  // ★ fix/ai-report-unverified-precision: 合作建议段精修 (只删含意图前缀句子, 不再整段占位)
  //   每条 c.value 走 _filterUnverifiedSentences 过滤, 含意图前缀+数字的句子被删
  //   删除后 c.value 为空 → 该条不显示 (单条)
  //   所有 c 过滤后都为空 → 显示 "等待 AI 模型修复后重新生成"
  //   段末行内提示 (克制, 不显眼): "此段含未验证数字，部分已隐藏"
  var _adviceUnverifiedAny = false;
  var _adviceRows = [];
  report.cooperation.forEach(function (c) {
    var f = _filterUnverifiedSentences(c.value || '');
    if (f.hasUnverified) _adviceUnverifiedAny = true;
    if (f.filtered) {
      _adviceRows.push('<tr><th>' + escapeHtml(c.label) + '</th><td>' + formatMessageInline(f.filtered) + '</td></tr>');
    }
  });
  if (_adviceRows.length) {
    html += '<div class="ai-section"><div class="ai-section-title">合作建议' + _precisionWarningSuffix(_adviceUnverifiedAny) + '</div><table class="ai-cooperation-table">';
    html += _adviceRows.join('');
    html += '</table></div>';
  } else if (report.cooperation.length) {
    // 所有行被过滤后都为空 → 显示 "等待 AI 模型修复后重新生成"
    html += '<div class="ai-section"><div class="ai-section-title">合作建议</div><div class="ai-advice-section empty">等待 AI 模型修复后重新生成</div></div>';
  }
  // 2-3 divider
  if ((report.cooperation.length) && (report.risks.length)) {
    html += '<hr class="ai-section-divider">';
  }

  // 风险点与缓解建议 (含未验证数字时整段占位)
  if (report.risksUnverified) {
    html += _renderUnverifiedPlaceholder('风险点与缓解建议');
  } else if (report.risks.length) {
    html += '<div class="ai-section"><div class="ai-section-title">风险点与缓解建议</div><ul class="ai-section-list ai-risks">';
    report.risks.forEach(function(item){ html += '<li>' + formatMessageInline(item) + '</li>'; });
    html += '</ul></div>';
  }

  html += '</div>';
  return html;
}

// ★ fix/ocr-field-count-leaf (老大 2026-09-24):
//   1 层展开叶子计数 (顶层 key + dict sub keys + list items, 不深入 2 层嵌套)
//   李婶儿: 53 顶层 → 180 展开 (extra_fields 内层不再爆涨)
//   空 dict/list 字段存在仍算 1 (字段已采集, 只是无内容)
//   scalar 算 1
//   用法: _countOcrFieldsLeaf(t.ocr_raw_fields) 给"数据完整度"卡片显示
function _countOcrFieldsLeaf(obj) {
  if (!obj || typeof obj !== 'object') return 0;
  var n = 0;
  var keys = Object.keys(obj);
  for (var i = 0; i < keys.length; i++) {
    var v = obj[keys[i]];
    n += 1;  // 顶层 key 自身算 1
    if (v && typeof v === 'object' && !Array.isArray(v)) {
      // dict: 加 sub key 数 (1 层, 不再下钻)
      n += Object.keys(v).length;
    } else if (Array.isArray(v)) {
      // list: 加 item 数 (不展开 item 内的 dict 字段)
      n += v.length;
    }
  }
  return n;
}

// ★ fix/talent-age-normalize (老大 2026-09-24 09:00 反馈):
//   截图 5 显示粉丝年龄 "31-40岁 30.1%" 和 "31-40 30.1%" 同时出现 (key 字符串不同)
//   之前 commit 4 的 _dedup 是按字面 key dedup (no-op, dict key 天然唯一), 救不了"同义异名"
//   治标: key 归一化 (去"岁" / 去空格 / 全半角统一 / 横线统一 / trim) + 合并 (取较大值 + console.warn)
//   治本: 后端 OCR dedup 阶段做同义归一 (Mini 端任务)
// ★ fix/mini-test-code-repair-20260925 12:00: 这 2 个函数原在 renderTalentPanelFans 函数体内 (L31840-L31878),
//   但 _buildFansProfile 在 renderTalentPanelOverview 函数体内 (L31393) 调用 _mergeDistByNormalizedKey,
//   两个不同函数作用域互相看不见 → ReferenceError: _mergeDistByNormalizedKey is not defined.
//   移到顶层 (老大 brief), 让两个函数都能 hoist 访问 (Mavis 25e3ccf 误判 hoist 跨函数生效).
function _normalizeDistKey(k) {
  if (!k) return '';
  return String(k)
    .replace(/[ \t\u3000]/g, '')            // 去半角空格/tab/全角空格
    .replace(/岁/g, '')                      // 去"岁"字
    .replace(/[‐-―—]/g, '-')                 // 各种横线统一为半角 -
    .replace(/[\uFF01-\uFF5E]/g, function(c){ // 全角 → 半角
      return String.fromCharCode(c.charCodeAt(0) - 0xFEE0);
    })
    .trim()
    .replace(/城市$/, '');             // 城市档: 新一线 / 新一线城市 归一到同档, merge 才能合并
}
// ★ fix/mini-test-code-repair-20260925 (老大 2026-09-29 工单: 档位自然排序):
//   新增可选第 2 参 orderSpec (不传 = 老行为, 按 value 降序, 一行不差):
//     - function(key) → 返回 rank 数字, 小的排前面 (年龄 / 客单价用)
//     - object {归一key: rank} → 查表排 (城市等级等语义序用)
//   未知 key 一律排最后, 未知之间仍按 value 降序 (不打乱已有观感)
// ★ 纵深防御 (原生产 35b8384 守卫, 切 dev 前补回): 只接受 {str:number} 分布 dict,
//   拒绝 {标题/说明/数据:list} 复杂 dict, 不渲染垃圾墙, 走空/暂无数据.
function _isValidDistDict(v) {
  if (!v || typeof v !== 'object' || Array.isArray(v)) return false;
  var ks = Object.keys(v);
  if (!ks.length) return false;
  for (var i = 0; i < ks.length; i++) {
    var raw = v[ks[i]];
    var n = (typeof raw === 'number') ? raw : parseFloat(raw);
    if (raw === null || raw === '' || typeof raw === 'object' || isNaN(n)) return false;
  }
  return true;
}
function _mergeDistByNormalizedKey(obj, orderSpec) {
  if (!_isValidDistDict(obj)) return [];
  var merged = {};        // 归一化 key → { value, displayKey }
  Object.keys(obj).forEach(function(rawKey){
    var norm = _normalizeDistKey(rawKey);
    if (!norm) return;
    var v = parseFloat(obj[rawKey]) || 0;
    if (merged[norm] === undefined) {
      merged[norm] = { value: v, displayKey: rawKey };
    } else {
      // 同义异名: 取较大值, 记录告警
      if (Math.abs(v - merged[norm].value) > 0.001) {
        console.warn('[renderDist] 同义异名 key 占比不同, 取较大值: "'
          + merged[norm].displayKey + '"(' + merged[norm].value
          + '%) vs "' + rawKey + '"(' + v + '%)');
      }
      if (v > merged[norm].value) {
        merged[norm] = { value: v, displayKey: rawKey };
      }
    }
  });
  // 转成渲染用的 { displayKey: value } 数组
  //   ★ 2026-09-29 老大: key 显示映射下沉到这里 (函数出口) ——
  //   genz → Z世代 之前只活在 _renderCrowdPrefText (人群偏好区) 和 _INLINE_DIST_KEY_DISPLAY (价格带/类目)
  //   两个 helper 里, 走 _mergeDistByNormalizedKey 的分布卡拿不到 ——
  //   renderDist 的短视频人群偏好卡 / 直播间人群卡 / 概览 _buildFansProfile 截图显示原始 'genz 15.5%'.
  //   出口统一走 _crowdPrefLabel, 所有 dict 路径共用, 不用逐个渲染点补.
  //   合并与同义异名告警仍用原始 key (merged[].displayKey), 只在最终展示层映射, 不影响去重逻辑.
  //   排序也吃这个映射后的 key: 但映射只改 genz 一种, 而 genz 不是 age/price/city_tier 档位,
  //   _distNumericRank / _DIST_ORDER_CITY_TIER 查表结果不受影响.
  var arr = Object.keys(merged).map(function(n){
    var _dispKey = _crowdPrefLabel(merged[n].displayKey);
    // 城市档合并后统一显示长名 (归一已剥去结尾"城市", 出口补回)
    if (_CITY_TIER_LONG_NAME[n]) _dispKey = _CITY_TIER_LONG_NAME[n];
    return { key: _dispKey, value: merged[n].value };
  });
  if (orderSpec) {
    var isRankFn = (typeof orderSpec === 'function');
    arr.sort(function(a, b){
      var ra = isRankFn ? orderSpec(a.key) : orderSpec[_normalizeDistKey(a.key)];
      var rb = isRankFn ? orderSpec(b.key) : orderSpec[_normalizeDistKey(b.key)];
      var aUnknown = (ra === undefined || ra === null || isNaN(ra));
      var bUnknown = (rb === undefined || rb === null || isNaN(rb));
      if (aUnknown && bUnknown) return b.value - a.value;   // 都认不出 → 维持占比降序
      if (aUnknown) return 1;                              // 认不出的排最后
      if (bUnknown) return -1;
      if (ra !== rb) return ra - rb;                       // 自然序 (小的在前)
      return b.value - a.value;
    });
  } else {
    arr.sort(function(a, b){ return b.value - a.value; });
  }
  return arr;
}

// ★ 年龄 / 客单价自然序: 取 key 里的首个数字当 rank.
//   为什么不用硬编码 5 档查表: 真实数据分档写法不统一 —— '18-23' / '18-25' / '31-40岁' /
//   '41+' / '50岁以上' / '0~25' / '500+' 都见过 (老 demo 数据是 18-25/26-30/31-35/36-40/41+,
//   OCR 数据是 18-23/24-30/31-40/41-50/50岁以上). 按下界数值排 → 两种写法都落到同一条自然序,
//   且完全符合老大 brief: 18-23 → 24-30 → 31-40 → 41-50 → 50岁以上.
function _distNumericRank(key) {
  var m = String(key == null ? '' : key).match(/\d+(\.\d+)?/);
  return m ? parseFloat(m[0]) : NaN;
}

// ★ 城市等级自然序: 语义序, 数字推不出来 → 显式 7 档表 (老大拍板的 7 档长名).
//   长短形式都收: b1896ba 之前的存量数据 / OCR 原样还有 '一线' / '新一线' / '六线及以下' 短形式.
//   ★ 2026-09-29 老大复验后改档: 新一线排在 一线 之前 (实测 '新一线 17.6% → 一线 8.1%'),
//   与今天早上工单里写的 '一线→新一线' 相反, 以本次拍板为准.
var _CITY_TIER_LONG_NAME = {
  '新一线': '新一线城市', '一线': '一线城市', '二线': '二线城市',
  '三线': '三线城市', '四线': '四线城市', '五线': '五线城市',
  '六线及以下': '六线及以下'
};
var _DIST_ORDER_CITY_TIER = (function () {
  var buckets = [
    ['新一线', '新一线城市', '新1线', '新1线城市'],
    ['一线', '一线城市', '1线', '1线城市'],
    ['二线', '二线城市', '2线', '2线城市'],
    ['三线', '三线城市', '3线', '3线城市'],
    ['四线', '四线城市', '4线', '4线城市'],
    ['五线', '五线城市', '5线', '5线城市'],
    ['六线及以下', '六线及以下城市', '六线以下', '六线及以下']
  ];
  var map = {};
  buckets.forEach(function (aliases, i) {
    aliases.forEach(function (a) { var n = _normalizeDistKey(a); if (n) map[n] = i; });
  });
  return map;
})();

// ★ 字段 → 排序规则: 只给年龄 / 客单价 / 城市等级三个维度开自然序.
//   性别 / 地域 / 设备 / 活跃度 / 人群 / 品类 传 null → 维持原占比降序 (老大: 只改这三处).
function _distOrderForField(field) {
  if (!field) return null;
  if (/city_tier/.test(field)) return _DIST_ORDER_CITY_TIER;
  if (/age|price/.test(field)) return _distNumericRank;
  return null;
}

// ★ 人群偏好标签归一: OCR / 旧数据里 genz / GenZ / z世代 混用, 统一显示 'Z世代'
function _crowdPrefLabel(key) {
  var s = String(key == null ? '' : key).trim();
  if (!s) return '';
  var low = s.toLowerCase();
  if (low === 'genz' || low === 'z世代' || low === 'z世代人群') return 'Z世代';
  return s;
}

// ★ 人群偏好 (fan_crowd / fan_group_crowd) 纯文本渲染, 三路输入兼容:
//   dict        → 按占比降序, 每一项一行 '标签 百分比%', 不截断 (老大 2026-09-29 改: 8 项全展示,
//                 跟年龄 / 性别 / 城市等级分布同风格; 百分比按本组 total 归一)
//   JSON 字符串 → 先 JSON.parse, 再按 dict 走 (DB TEXT 列原样返回的情况)
//   其它标量   → legacy '精致妈妈' / 'Z世代' 这类单值, 原样显示
//   null / 空   → '' (调用方兜 '待补充')
// ⚠️ 返回纯文本不是 HTML: 调用方必须 escapeHtml, 靠 .products-detail-value 的
//    white-space: pre-line 换行 (跟 _buildFansProfile 概览分行同一套方案, 不开 html:true).
function _renderCrowdPrefText(v) {
  var obj = v;
  if (typeof obj === 'string') {
    var s = obj.trim();
    if (!s) return '';
    if (s.charAt(0) === '{') {
      try {
        var p = JSON.parse(s);
        if (p && typeof p === 'object' && !Array.isArray(p)) obj = p;
        else return _crowdPrefLabel(s);
      } catch (e) { return _crowdPrefLabel(s); }   // 坏 JSON → 当标量原样, 不甩给用户
    }
    if (obj === v) return _crowdPrefLabel(s);
  }
  if (!obj || typeof obj !== 'object' || Array.isArray(obj)) return '';
  var arr = Object.keys(obj).map(function (k) {
    return { key: k, value: parseFloat(obj[k]) || 0 };
  }).filter(function (e) { return e.key !== ''; });
  if (!arr.length) return '';
  arr.sort(function (a, b) { return b.value - a.value; });
  var total = arr.reduce(function (a, b) { return a + b.value; }, 0);
  return arr.map(function (e) {
    var pct = total ? (e.value / total * 100) : 0;
    return _crowdPrefLabel(e.key) + ' ' + pct.toFixed(1) + '%';
  }).join('\n');
}

function renderTalentPanelOverview(t) {
  var el = document.getElementById('talentsPanelOverview');
  if (!el) return;
  var mock = _getTalentMockData(t);
  var st = _TALENT_STATUS_MAP[t.cooperation_status] || _TALENT_STATUS_MAP['available'];
  var ai = t.ai_reason ? (function(){ try { return JSON.parse(t.ai_reason); } catch(e){ return {}; } })() : {};
  var tags = (t.ai_tags && t.ai_tags.length) ? t.ai_tags : mock.ai_tags;
  var avatar = t.avatar ? '<img src="' + escapeAttr(t.avatar) + '" style="width:100%;height:100%;object-fit:cover;border-radius:50%;">' : '<span style="font-size:28px;">' + (t.name || '?').charAt(0) + '</span>';
  var category = (typeof t.category === 'string' ? t.category : '') || (typeof t.fan_category === 'string' ? t.fan_category : '') || (typeof mock.category === 'string' ? mock.category : '') || '-';
  var productCount = t.product_count || t.total_products || mock.product_count || 0;
  var averagePrice = t.average_price || mock.average_price || 0;
  var totalGmv = t.total_gmv || mock.total_gmv || 0;
  var html = '';

  // 核心信息卡片
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-overview-header">';
  html += '<div class="talents-overview-avatar" style="background:' + (t.avatar ? '#f5f7fa' : 'var(--accent)') + ';">' + avatar + '</div>';
  html += '<div class="talents-overview-info">';
  // Hero 评级 chip:复用 _renderTalentRatingBadge(列表 v2 outline 5 评级色),和左侧列表完全一致
  var _heroRating = t.ai_rating ? _renderTalentRatingBadge(t.ai_rating) : (mock.level ? _renderTalentRatingBadge(mock.level) : '');
  html += '<div class="talents-overview-name">' + escapeHtml(t.name || '-') + ' ' + _renderTalentStatusDropdown(t.id, t.cooperation_status) + (_heroRating ? ' ' + _heroRating : '') + '</div>';
  html += '<div class="talents-overview-pills">';
  html += '<span class="talents-overview-pill"><label>抖音号</label>' + escapeHtml(t.douyin_id || '-') + '</span>';
  html += '<span class="talents-overview-pill"><label>粉丝</label>' + formatFollowers(t.followers || 0) + '</span>';
  html += '<span class="talents-overview-pill"><label>类目</label>' + escapeHtml(String(category)) + '</span>';
  html += '<span class="talents-overview-pill"><label>风险</label>' + escapeHtml(_formatRiskLabel(t.risk_rating || mock.risk_rating)) + '</span>';
  html += '</div>';
  if (t.bio) html += '<div class="talents-overview-bio">' + escapeHtml(t.bio) + '</div>';
  if (tags.length) {
    html += '<div class="talents-overview-tags">' + tags.map(function(tag){ return '<span class="talents-overview-tag">' + escapeHtml(tag) + '</span>'; }).join('') + '</div>';
  }
  html += '</div>';
  html += '</div></div>';

  // 核心数据（飞书带货指标）— ui/talent-pix-v21: KPI 升级 (6 列 + trend + sparkline mini)
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">核心数据</div>';
  // ★ ui/talent-detail-redesign-v2-cleanup: 删 KPI 假 sparkline + trend % (老大 2026-09-23)
  //   无后端时间序列接口时画假折线 + 假趋势 %, 等于编造数字 → 全删, 只留 value + label
  // ★ fix/talent-kpi-card-key-bind: 4 张卡 key 改名 (feishu_* / monthly_settlement → 实际库列),
  //   redesign 时前端 key 名改飞了, 库返 23/22/300/250万, 前端读错 key 走 || '-' 全显 -.
  //   复用 L30531 GMV 万元模式 (>= 10000 转 "xw" 格式, 页面已存在的内联逻辑) + formatNumber 千位分隔.
  // ★ fix/ocr-canonical-sync-v2: 区间值双轨显示 (老大 2026-09-25)
  //   月结算金额 / 视频 GPM 优先读对应 _text 列 (¥100万-500万 区间原文), 无 _text 回退数值
  //   _kpiRange 提取 ¥X万-Y万 / ¥X-Y 区间数字, 简化显示 (例: "¥100万-500万" → "100万-500万")
  function _kpiWan(v) {
    // ★ 复用页面已有 GMV 万元模式 (talent-detail L30531): >= 10000 转万元, 否则千位分隔
    var n = parseFloat(v) || 0;
    return n >= 10000 ? ((n / 10000).toFixed(1).replace(/\.0$/, '') + '万') : (n > 0 ? formatNumber(n) : '');
  }
  function _kpiNum(v) {
    var n = parseFloat(v) || 0;
    return n > 0 ? formatNumber(n) : '';
  }
  // ★ fix/ocr-canonical-sync-v2: _kpiRange 提取区间值数字
  //   输入 '¥100万-500万' / '¥5,000-2万' / '¥2万-10万' → 输出 "100万-500万" (去 ¥, 加千位)
  //   优先级: _text 原文 (区间精确值) > 数值列 (_kpiWan 转万元) > '-'
  function _kpiRange(text) {
    if (!text) return '';
    var s = String(text).trim().replace(/^[¥￥]+/, '').trim();
    if (!s || !s.match(/[\d\u4e00-\u9fff]/)) return '';
    return s;
  }
  var _kpis = [
    { label: '粉丝量',      val: t.followers ? formatFollowers(t.followers) : '-' },
    { label: '月结算金额',   val: _kpiRange(t.total_gmv_text) || _kpiWan(t.total_gmv) || '-' },
    { label: '带货商品数',   val: _kpiNum(t.product_count) || '-' },
    { label: '视频GPM',      val: _kpiRange(t.video_gpm_text) || _kpiNum(t.video_gpm) || '-' },
    { label: '单视频结算额', val: t.single_video_settlement || '-' },
    { label: '合作店铺数',   val: _kpiNum(t.total_shops) || '-' }
  ];
  html += '<div class="talents-metric-grid">';
  _kpis.forEach(function (k) {
    html += '<div class="talents-metric-card">'
      + '<div class="talents-metric-value">' + escapeHtml(k.val) + '</div>'
      + '<div class="talents-metric-label">' + escapeHtml(k.label) + '</div>'
      + '</div>';
  });
  html += '</div></div>';

  // 基础信息（飞书录入字段）
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">基础信息</div>';
  // 8 项基础信息:任一有值就展示,全空时合并成一行 placeholder
  // ★ fix/ocr-canonical-sync-v3 (2026-09-25): 概览页 fan 4 段渲染 (账号粉丝特征 / 短视频粉丝特征)
  //   老大原话: "概览'账号粉丝特征'卡片取 fan_gender/fan_city_tier/fan_age/fan_consumption 4 个字段"
  //   "短视频粉丝特征卡片取 video_audience_* 对应字段"
  //   之前 _ocrFallback 只返标量, dict 字段 (fan_gender/fan_age/fan_city_tier/fan_price_range 等)
  //   全失败 → 卡片永远 '待补充'. v3 加 _buildFansProfile 从 4 个 dict 字段拼可读文本:
  //     fan_gender {男: 65, 女: 35}  → '男65%/女35%'
  //     fan_age {31-40岁: 38}        → '31-40岁38%'
  //   多个字段用 ' / ' 拼接, 4 字段任一非空返值.
    // ★ fix/ocr-canonical-sync-v4 amend (2026-09-25): 检测 4 个 city_tier 字段 incomplete 状态
  //   server 端 _normalize_distribution 标记的 _distribution_incomplete (sidecar JSON)
  //   4 个 city_tier 字段任一 incomplete=true → 拼接文本末尾追加 "（分布不完整）"
  //   老大原话: "禁止硬凑 100, 变成 50/50" — 渲染层必须显式提示用户原始值不完整
  // ★ fix/overview-fans-profile-panels 16:29: 概览 tab 粉丝特征改分行 + 进度条 (老大拍板方案 B)
  //   老大截图实证: 4 维度 (性别/年龄/城市等级/价格带) 拼单行 ' / ' 文本, 跨 3 视觉行, 一堵墙
  //   精度 bug: 老实现 `label + pct + '%'` 没 toFixed → 16% 和 83.73% 混着显示像格式错乱
  //   修法: 复用粉丝画像 tab renderDist 的 CSS class (products-progress-* / talents-panel-*),
  //         每个维度一个 talents-panel-section 区块, 档位用进度条 + pct.toFixed(1) 统一精度
  //   沿用现有 CSS class, 不另起风格; 保留 _distribution_incomplete "（分布不完整）" 提示逻辑 (per-dim)
  //   _mergeDistByNormalizedKey (L31813 function 声明) hoist 安全, 运行时可用
  var _FANS_DIM_LABELS = {
    'fan_gender': '性别分布', 'fan_age': '年龄分布',
    'fan_city_tier': '城市等级分布', 'fan_price_range': '客单价分布',
    'fan_crowd': '人群偏好', 'fan_category': '品类偏好', 'fan_region': '地域分布',
    'video_audience_gender': '性别分布', 'video_audience_age': '年龄分布',
    'video_audience_city_tier': '城市等级分布', 'video_audience_price_range': '客单价分布',
    'video_audience_region': '地域分布',
    'fan_group_gender': '性别分布', 'fan_group_age': '年龄分布',
    'fan_group_city_tier': '城市等级分布', 'fan_group_price': '客单价分布',
    'fan_group_crowd': '人群偏好', 'fan_group_category': '品类偏好',
    'fan_group_activity': '活跃度分布', 'fan_group_device': '设备分布',
  };
  // ★ fix/overview-fans-profile-panels 20:23: 概览 tab 粉丝特征改分行 (老大拍板方案 A)
  //   老大截图实证: 4 维度 (性别/年龄/城市等级/客单价) 拼单行 ' / ' 文本, 跨 3 视觉行, 一堵墙
  //   老大 3 条铁约束: (1) 不碰 html:true / 不跳过 escapeHtml (2) 只动本函数 (3) 241 passed 才算完
  //   老大拍板方案 A: 纯文本 \n 分行 + .value CSS white-space: pre-line (不生成进度条, 不开 XSS 洞)
  //   撤 639bb2f (老大否决 html:true 路线): 不再用 <div> 块 / products-progress-* 进度条
  //   格式: 每档 '档位 百分比%' (档位名与百分比之间一个空格), 百分比统一 toFixed(1)
  //   4 维度之间用 \n\n (空行) 分隔 = 视觉上 4 个独立小块
  //   ⚠️ 返回纯文本 (不是 HTML), 必须走 escapeHtml (老大铁约束 1)
  //   ⚠️ 维度名走 _FANS_DIM_LABELS (639bb2f 已加, 保留), incomplete 提示保留 (per-dim)
  function _buildFansProfile(t, fieldKeys) {
    if (!t) return '';
    var incomplete = (t._distribution_incomplete && typeof t._distribution_incomplete === 'object') ? t._distribution_incomplete : {};
    var blocks = [];
    fieldKeys.forEach(function(k) {
      var v = t[k];
      if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length) {
        var entries = _mergeDistByNormalizedKey(v, _distOrderForField(k));
        if (entries.length) {
          var total = entries.reduce(function(a, b){ return a + b.value; }, 0);
          var dimLabel = _FANS_DIM_LABELS[k] || k;
          var dimTitle = dimLabel + (incomplete[k] ? '（分布不完整）' : '');
          var lines = [dimTitle];
          entries.forEach(function(e) {
            var pct = total ? (e.value / total * 100) : 0;
            lines.push(e.key + ' ' + pct.toFixed(1) + '%');
          });
          blocks.push(lines.join('\n'));
        }
      }
    });
    // 4 维度之间用 \n\n (空行) 分隔 = 视觉上 4 个独立小块
    return blocks.join('\n\n');
  }
  // ★ feature/influencer-form-validation: 空值 fallback '-' → '待补充' + 加 empty flag (CSS class value-empty 高亮橙色)
  // ★ fix/ocr-fallback: talent 表字段为空时, 从 ocr_raw_fields 模糊匹配填充
  //   (e.g. talent_type 为空 → 试 ocr.talent_type → 试 ocr.main_category)
  var _bi = [
    {label:'内容类型',       val:(t.talent_type      || _ocrFallback(t, 'talent_type')      || '待补充'),     empty:!(t.talent_type      || _ocrFallback(t, 'talent_type')),      full:false},
    {label:'类目',           val:((typeof t.category === 'string' ? t.category : '')         || _ocrFallback(t, 'category')         || (typeof mock.category === 'string' ? mock.category : '') || '待补充'), empty:!((typeof t.category === 'string' ? t.category : '')         || _ocrFallback(t, 'category')         || (typeof mock.category === 'string' ? mock.category : '')), full:false},
    {label:'内容风格',       val:(t.content_style    || _ocrFallback(t, 'content_style')    || mock.content_style || '待补充'), empty:!(t.content_style    || _ocrFallback(t, 'content_style')    || mock.content_style), full:false},
    {label:'视频结算额占比',  val:(t.video_settlement_ratio || _ocrFallback(t, 'video_settlement_ratio') || '待补充'), empty:!(t.video_settlement_ratio || _ocrFallback(t, 'video_settlement_ratio')), full:false},
    {label:'视频平均件单价',  val:(t.video_avg_price  || _ocrFallback(t, 'video_avg_price')  || '待补充'),     empty:!(t.video_avg_price  || _ocrFallback(t, 'video_avg_price')),  full:false},
    // ★ fix/ocr-canonical-sync-v3: 优先用 _buildFansProfile 从 4 个 fan_* dict 字段拼可读文本
    //   (兜底: t.account_fans_profile 标量 → _ocrFallback)
    {label:'账号粉丝特征',   val:(_buildFansProfile(t, ['fan_gender', 'fan_age', 'fan_city_tier', 'fan_price_range']) || t.account_fans_profile || _ocrFallback(t, 'account_fans_profile') || '待补充'), empty:!(_buildFansProfile(t, ['fan_gender', 'fan_age', 'fan_city_tier', 'fan_price_range']) || t.account_fans_profile || _ocrFallback(t, 'account_fans_profile')), full:true},
    {label:'短视频粉丝特征',  val:(_buildFansProfile(t, ['video_audience_gender', 'video_audience_age', 'video_audience_city_tier', 'video_audience_price_range']) || _buildFansProfile(t, ['video_audience_region', 'video_audience_city_tier']) || t.video_fans_profile || _ocrFallback(t, 'video_fans_profile') || '待补充'), empty:!(_buildFansProfile(t, ['video_audience_gender', 'video_audience_age', 'video_audience_city_tier', 'video_audience_price_range']) || _buildFansProfile(t, ['video_audience_region', 'video_audience_city_tier']) || t.video_fans_profile || _ocrFallback(t, 'video_fans_profile')), full:true},
    {label:'备注',           val:(t.remark           || _ocrFallback(t, 'remark')           || '待补充'),     empty:!(t.remark           || _ocrFallback(t, 'remark')),            full:true},
  ];
  var _biHasAny = _bi.some(function(i){ return !i.empty; });
  html += '<div class="talents-info-grid">';
  if (!_biHasAny) {
    html += '<div class="talents-info-item full-width placeholder">基础信息待 AI 识别(AI 分析后将自动填充)</div>';
  } else {
    for (var _i = 0; _i < _bi.length; _i++) {
      var _it = _bi[_i];
      // ★ feature/influencer-form-validation: 空值字段加 value-empty class (橙色高亮)
      html += '<div class="talents-info-item' + (_it.full ? ' full-width' : '') + '"><label>' + escapeHtml(_it.label) + '</label><div class="value' + (_it.empty ? ' value-empty' : '') + '">' + escapeHtml(_it.val) + '</div></div>';
    }
  }
  html += '</div></div>';

  // AI 分析摘要
  var aiText = t.ai_analysis || mock.ai_analysis || '';
  html += '<div class="talents-ai-card">';
  html += '<div class="talents-ai-title">🤖 AI 综合分析</div>';
  if (aiText) {
    html += renderTalentAIReport(aiText);
  } else {
    html += '<div class="talents-ai-text">点击右上角「AI 分析」生成达人综合评估。</div>';
    if (!t.ai_analysis && ai.cooperation_advice) {
      // ★ fix/ai-report-unverified-precision: fallback 合作建议也走精修过滤
      var _adviceFb = _filterUnverifiedSentences(ai.cooperation_advice);
      if (_adviceFb.filtered) {
        html += '<div class="talents-ai-title" style="margin-top:10px;">合作建议' + _precisionWarningSuffix(_adviceFb.hasUnverified) + '</div><div class="talents-ai-text">' + formatMessageContent(_adviceFb.filtered) + '</div>';
      } else if (ai.cooperation_advice) {
        // 全部被过滤 → 显示 "等待 AI 模型修复后重新生成"
        html += '<div class="talents-ai-title" style="margin-top:10px;">合作建议</div><div class="talents-ai-text ai-advice-section empty">等待 AI 模型修复后重新生成</div>';
      }
    }
    if (!t.ai_analysis && ai.risk_warnings) html += '<div class="talents-ai-title" style="margin-top:10px;">风险提示</div><div class="talents-ai-text">' + formatMessageContent(ai.risk_warnings) + '</div>';
  }
  html += '</div>';

  // ★ ui/talent-detail-redesign-v2-cleanup commit 3 (老大 2026-09-23):
  //   删概览 tab 底部 610 字段 OCR 长列表 (含 extra 473 字段 + 英文 key 平铺)
  //   改成"数据完整度"卡片: 展示已识别字段数 + 覆盖的维度 (6 维度分类)
  //   原始 OCR 字段移到 commit 7 分析档案 tab 做语义分组折叠,字段名全中文
  // ★ fix/ocr-field-count-leaf (老大 2026-09-24):
  //   计数口径从 "Object.keys(ocr).length" (顶层 key 数, 李婶儿 = 53)
  //   改为 "_countOcrFieldsLeaf(ocr)" (1 层展开叶子数, 李婶儿 = 180)
  //   口径: 顶层 key 算 1 + dict value 算 sub key 数 + list value 算 item 数, 不深入 2 层嵌套
  //   理由: 顶层 53 让用户误以为字段少, 但实际可用字段数是 180 (含 dict 子键 + list 元素)
  //   不算全递归 (李婶儿 631) 是因为 extra_fields 内部深层嵌套会让数字爆炸, 用户看不到重点
  var ocr = (t && t.ocr_raw_fields) || {};
  var totalFields = _countOcrFieldsLeaf(ocr);
  var dims = [];
  if (ocr.name || ocr.douyin_id || ocr.followers || ocr.total_gmv || ocr.monthly_settlement || ocr.gpm || ocr.feishu_product_count) dims.push('商业核心');
  if (ocr.talent_type || ocr.category || ocr.content_style || ocr.bio || ocr.city || ocr.level) dims.push('基础');
  if (ocr.fan_gender || ocr.fan_age || ocr.fan_region || ocr.fan_city_tier || ocr.fan_crowd || ocr.fan_price_range || ocr.fan_category) dims.push('粉丝画像');
  if (ocr.fan_group_gender || ocr.fan_group_age || ocr.fan_group_active || ocr.fan_group_device) dims.push('群组粉丝');
  if (ocr.price_distribution || ocr.category_distribution || ocr.brand_distribution || ocr.top_brands || ocr.hot_brands) dims.push('热卖分布');
  if (ocr.live_stream_sessions || ocr.live_stream_viewers || ocr.live_ratio || ocr.video_ratio || ocr.avg_live_gmv) dims.push('直播数据');
  if (dims.length === 0) dims.push('待识别');
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">数据完整度</div>';
  html += '<div style="background:var(--surface);border:0.5px solid var(--separator);border-radius:var(--radius-lg);padding:14px 16px;">';
  html += '<div style="font-size:14px;color:var(--text-primary);margin-bottom:8px;">已识别 <strong style="color:var(--accent);font-size:18px;">' + totalFields + '</strong> 个字段</div>';
  html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">已覆盖维度: ' + dims.map(function(d){ return '<span style="display:inline-block;background:var(--accent-light);color:var(--accent);font-size:11px;padding:3px 8px;border-radius:6px;margin-right:4px;margin-bottom:4px;">' + escapeHtml(d) + '</span>'; }).join('') + '</div>';
  html += '<div style="font-size:11px;color:var(--text-tertiary);">完整字段在「分析档案」tab 查阅</div>';
  html += '</div></div>';

  el.innerHTML = html;
}

// ★ ui/talent-detail-redesign-v2-cleanup commit 3: _renderOcrFieldsPanel 函数已删除 (老大 2026-09-23)
//   原函数将 ocr_raw_fields 全部 610 字段 (含 extra 473 字段 + 英文 key 平铺) 渲染到概览底部
//   用户体验差: 字段太多 + 英文 key 不友好 + 占用过多空间
//   改用 commit 3 的"数据完整度"卡片 + commit 7 的分析档案 tab 语义分组折叠


// ★ fix/ocr-fallback: 从 ocr_raw_fields 模糊匹配填充前端 '待补充' 字段
//   老大反馈 '前端要显示的待补充字段' (内容类型/内容风格/视频平均件单价/账号粉丝特征/短视频粉丝特征/备注)
//   这些字段 talent 表里空 (legacy 录入没填), 但 ocr_raw_fields 里有同义字段 (talent_type/content_style/...)
//   这里做 lookup 表, renderTalentPanelOverview 调用 _ocrFallback(t, 'talent_type') 拿到 fallback 值.
var _OCR_FALLBACK_MAP = {
  'talent_type':        ['talent_type', 'main_category'],
  'category':           ['main_category', 'top_categories', 'category'],
  'content_style':      ['content_style', 'bio'],
  'video_settlement_ratio': ['video_settlement_ratio'],
  'video_avg_price':    ['video_avg_price', 'average_price'],
  'account_fans_profile':   ['account_fans_profile', 'fan_gender', 'fan_age', 'fan_city_tier', 'fan_crowd', 'fan_price_range', 'fan_category'],
  'video_fans_profile': ['video_fans_profile', 'video_audience_region', 'video_audience_city_tier'],
  'remark':             ['remark', 'notes'],
};
function _ocrFallback(t, field) {
  var ocr = t && t.ocr_raw_fields;
  if (!ocr) return '';
  var keys = _OCR_FALLBACK_MAP[field];
  if (!keys) return '';
  for (var i = 0; i < keys.length; i++) {
    var v = ocr[keys[i]];
    if (v && typeof v !== 'object') return String(v);
  }
  return '';
}

function renderTalentPanelSales(t) {
  var el = document.getElementById('talentsPanelSales');
  if (!el) return;
  var mock = _getTalentMockData(t);
  var html = '';

  // ★ ui/talent-detail-redesign-v2-cleanup: GMV 趋势图组件已删 (老大 2026-09-23)
  //   原本是基于 _getTalentMockData.total_gmv + sin 波合成 30/90 天假折线
  //   DB 无 gmv_trend 字段, OCR 也无时间序列, 后端没有 GMV 时间序列接口
  //   现在直接显示"暂无 GMV 趋势数据"占位, 等后端接入真实接口再加
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">GMV 趋势</div>';
  html += '<div class="talents-empty-text">暂无 GMV 趋势数据(后端时间序列接口未接入)</div>';
  html += '</div>';

  // 直播/视频数据
  html += '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">内容带货占比</div>';
  html += '<div class="talents-metric-grid">';
  html += '<div class="talents-metric-card"><div class="talents-metric-value">' + (t.live_ratio || 0) + '%</div><div class="talents-metric-label">直播占比</div></div>';
  html += '<div class="talents-metric-card"><div class="talents-metric-value">' + (t.video_ratio || 0) + '%</div><div class="talents-metric-label">视频占比</div></div>';
  html += '<div class="talents-metric-card"><div class="talents-metric-value">¥' + formatNumber(t.avg_live_gmv || 0) + '</div><div class="talents-metric-label">场均直播GMV</div></div>';
  html += '</div></div>';

  // 价格带与类目分布
  html += '<div class="talents-distribution-grid">';
  html += '<div class="talents-distribution-card">';
  html += '<div class="talents-distribution-title">价格带分布</div>';
  var priceDistribution = _getTalentPriceDistribution(t);
  if (priceDistribution.length) {
    // ★ ui/talent-detail-redesign-v2-cleanup commit 4: 价格带占比归一化校验
    //   OCR 数据脏时 (总和 >100% / <100%) 按当前总和归一化显示,避免 "76+29.67+33.94..." 累加超 100%
    var priceTotal = priceDistribution.reduce(function(s, x){ return s + (parseFloat(x.value)||0); }, 0);
    priceDistribution.forEach(function(item, idx){
      var colors = ['#1677ff', '#36cfc9', '#73d13d', '#faad14', '#ff7875'];
      var pct = priceTotal ? ((parseFloat(item.value) || 0) / priceTotal * 100) : 0;
      html += '<div class="talents-price-bubble"><span class="talents-price-bubble-dot" style="background:' + colors[idx % colors.length] + ';"></span><span class="talents-price-bubble-label">' + escapeHtml(item.label) + '</span><span class="talents-price-bubble-value">' + pct.toFixed(1) + '%</span></div>';
    });
  } else {
    html += '<div class="products-empty-text" style="padding:12px 0;">暂无数据</div>';
  }
  html += '</div>';
  html += '<div class="talents-distribution-card">';
  html += '<div class="talents-distribution-title">类目分布</div>';
  var catDistribution = _getTalentCategoryDistribution(t);
  if (catDistribution.length) {
    // ★ ui/talent-detail-redesign-v2-cleanup commit 4: 类目占比归一化校验 (老大反馈 2026-09-23)
    //   "类目分布占比加起来超过 180% (76+29.67+33.94…),脏数据直接渲染"
    //   前端归一化: sum > 100% 时按 value/sum 计算真实占比,显示给用户真实占比
    var catTotal = catDistribution.reduce(function(s, x){ return s + (parseFloat(x.value)||0); }, 0);
    catDistribution.forEach(function(item){
      var pct = catTotal ? ((parseFloat(item.value) || 0) / catTotal * 100) : 0;
      html += '<div class="products-progress-item"><div class="products-progress-header"><span>' + escapeHtml(item.label) + '</span><span>' + pct.toFixed(1) + '%</span></div><div class="products-progress-bar"><div class="products-progress-fill" style="width:' + Math.min(pct, 100) + '%"></div></div></div>';
    });
    // ★ fix/ocr-canonical-sync-v3 (2026-09-25): 删 v2 之前的 "⚠ OCR 录入占比总和" 警告
    //   老大原话: "前端类目分布底部 '⚠ OCR录入占比总和198.0%' 改成 '已按归一化显示' 或隐藏
    //   因为 v2 已归一到 100%, 原始总和不应再展示"
    //   v2 在 _canonicalize_talent_row 已把 category_distribution > 105 归一到 100,
    //   前端再显示 198% 警告会让用户误以为数据脏 (实际后端已修正).
  } else {
    html += '<div class="products-empty-text" style="padding:12px 0;">暂无数据</div>';
  }
  html += '</div>';
  // 品牌集中度环形图（与类目分布相同的数据解析逻辑）
  html += '<div class="talents-distribution-card">';
  html += '<div class="talents-distribution-title">品牌集中度</div>';
  var brandDistribution = _getTalentBrandDistribution(t);
  if (brandDistribution.length) {
    html += _renderTalentDonut(brandDistribution);
  } else {
    html += '<div class="products-empty-text" style="padding:12px 0;">暂无数据</div>';
  }
  html += '</div></div>';

  // 带货商品列表
  html += '<div class="talents-panel-section" id="talentSalesProductsSection">';
  html += '<div class="talents-panel-title">带货商品列表</div>';
  html += '<div class="products-loading">加载中...</div>';
  html += '</div>';
  el.innerHTML = html;

  // 加载商品(GMV canvas 已删)
  _loadTalentSalesProducts(t);
}

// ★ ui/talent-detail-redesign-v2-cleanup: switchTalentGmvRange / _getTalentGmvTrend / _drawTalentGmvChart
//   已删除 (老大 2026-09-23): GMV 趋势图原为 sin 波 + hash 合成假数据,违反零编造规则
//   后端无 gmv_trend 字段, OCR 也无时间序列, 后端无 GMV 时间序列接口
//   保留 _getTalentGmvTrend 函数签名空 stub,等后端接入真实数据时再实现

function _getTalentPriceDistribution(t) {
  // ★ ui/talent-detail-redesign-v2-cleanup (老大 2026-09-23): 价格带分布接 ocr_raw_fields
  //   优先级: t.price_distribution (DB) → t.ocr_raw_fields.price_distribution (OCR v3 dict 字段)
  //   兜底: 无数据返回 [] 显示"暂无数据"
  //   删原 hash 兜底 (mock.average_price + _hashString 合成假分布, 老大禁 fake 数据)
  var pd = t.price_distribution;
  if (!pd || typeof pd !== 'object' || !Object.keys(pd).length) {
    var _ocr = t.ocr_raw_fields || {};
    pd = _ocr.price_distribution;
  }
  if (pd && typeof pd === 'object' && Object.keys(pd).length) {
    return Object.keys(pd).map(function(k){
      var label;
      if (k.indexOf('+') >= 0) {
        label = '> ¥' + k.replace('+', '');
      } else {
        var parts = k.split('-');
        label = parts.length === 2 ? '¥' + parts[0] + ' - ¥' + parts[1] : k;
      }
      return { label: label, value: pd[k] };
    });
  }
  return [];
}

function _getTalentCategoryDistribution(t) {
  // ★ fix/ocr-distribution-fallback: 优先 t.category_distribution, 兜底 t.ocr_raw_fields.category_distribution
  //   _OCR_TO_TALENT_FIELDS 没同步 category_distribution (talent 表无此列), OCR dict 必走 ocr_raw_fields
  // ★ ui/talent-detail-redesign-v2-cleanup: 删 mock 参数 (其他面板已不依赖 mock 兜底)
  var cd = t.category_distribution;
  if (!cd || typeof cd !== 'object' || !Object.keys(cd).length) {
    var _ocr = t.ocr_raw_fields || {};
    cd = _ocr.category_distribution;
  }
  if (cd && typeof cd === 'object' && Object.keys(cd).length) {
    return Object.keys(cd).map(function(key){
      return { label: key, value: cd[key] };
    });
  }
  // ★ fix/ocr-distribution-no-demo: 移除 demo 假数据兜底 (老大反馈 2026-09-22)
  //   之前无数据时回退到 "相关类目1 27%/相关类目2 43%" 是假数据, 误导用户
  //   现在无数据直接返 [], 前端渲染"暂无数据"
  return [];
}

function _getTalentBrandDistribution(t) {
  // ★ fix/ocr-distribution-fallback: 优先 t.brand_distribution, 兜底 ocr_raw_fields.brand_distribution (dict)
  //   OCR v3 (commit d32c7ff) 加了 brand_distribution dict 字段, 直接用 dict 渲染
  // 1) talent.brand_distribution (dict) — talent 表实际无此列, 跳过
  var _bd = t.brand_distribution;
  if (!(_bd && typeof _bd === 'object' && Object.keys(_bd).length)) {
    var _ocr = t.ocr_raw_fields || {};
    _bd = _ocr.brand_distribution;
  }
  if (_bd && typeof _bd === 'object' && !Array.isArray(_bd) && Object.keys(_bd).length) {
    return Object.keys(_bd).map(function(key){ return { label: key, value: _bd[key] }; });
  }
  // 2) fallback: ocr_raw_fields.top_brands / hot_brands (字段名变体, list 格式)
  var ocr = t.ocr_raw_fields;
  if (ocr) {
    var _src = ocr.top_brands || ocr.hot_brands;
    if (_src) {
      if (Array.isArray(_src)) {
        // list 格式 [{name, ratio, ...}, ...] → 转 {label, value}
        var _mapped = _src.map(function(item){
          if (item && typeof item === 'object') {
            var _name = item.name || item.brand || item.label || '';
            var _ratio = item.ratio || item.share || item.value || item.percentage || item.pct || 0;
            return _name ? { label: _name, value: _ratio } : null;
          }
          return { label: String(item), value: 0 };
        }).filter(Boolean);
        if (_mapped.length) return _mapped;
      } else if (typeof _src === 'object' && Object.keys(_src).length) {
        return Object.keys(_src).map(function(key){ return { label: key, value: _src[key] }; });
      }
    }
  }
  return [];
}

function _renderTalentDonut(items) {
  // 环形图：conic-gradient 扇区 + 图例，扇区角度按占比归一化
  // ★ ui/talent-detail-redesign-v2-cleanup: V2.1 token 兑换 #1677ff → #1677ff
  var colors = ['#1677ff', '#36cfc9', '#73d13d', '#faad14', '#ff7875', '#b37feb'];
  var total = items.reduce(function(a, b){ return a + (parseFloat(b.value) || 0); }, 0);
  if (!total) return '';
  var acc = 0;
  var stops = items.map(function(item, idx){
    var pct = (parseFloat(item.value) || 0) / total * 100;
    var start = acc;
    acc += pct;
    return colors[idx % colors.length] + ' ' + start + '% ' + acc + '%';
  });
  var html = '<div class="talents-donut-wrap">';
  html += '<div class="talents-donut" style="background:conic-gradient(' + stops.join(',') + ');"></div>';
  html += '<div class="talents-donut-legend">';
  items.forEach(function(item, idx){
    // ★ ui/talent-detail-redesign-v2-cleanup commit 4: legend 也按归一化后 pct 显示
    //   之前显示原始 item.value (OCR 数据脏时可能是 1% 1% 1% 累计 <13%)
    var pct = total ? ((parseFloat(item.value) || 0) / total * 100) : 0;
    html += '<div class="talents-price-bubble"><span class="talents-price-bubble-dot" style="background:' + colors[idx % colors.length] + ';"></span><span class="talents-price-bubble-label">' + escapeHtml(item.label) + '</span><span class="talents-price-bubble-value">' + pct.toFixed(1) + '%</span></div>';
  });
  html += '</div></div>';
  return html;
}

function _loadTalentSalesProducts(t) {
  var section = document.getElementById('talentSalesProductsSection');
  if (!section) return;
  // ★ fix/ocr-panel-fallback: 内部 _renderProducts 抽出, 支持 API + OCR 兜底两路
  function _renderProducts(products, sourceLabel) {
    if (!products || products.length === 0) {
      section.innerHTML = '<div class="talents-panel-title">带货商品列表</div><div class="products-empty"><div class="products-empty-text">暂无带货商品数据</div></div>';
      return;
    }
    var html = '<div class="talents-panel-title">带货商品列表 (' + products.length + ')' + (sourceLabel ? ' <span style="font-size:11px;color:var(--text-secondary);font-weight:400;">' + sourceLabel + '</span>' : '') + '</div>';
    products.forEach(function(p, pIdx){
      // ★ fix/ocr-product-field-mapping: OCR v3 (commit d32c7ff) 5 字段 name+shop_name+price+gmv_range+video_count
      var _name = (p && (p.name || p.title || p.product_name)) || '-';
      var _shop = (p && (p.shop_name || p.shop || p.store)) || '-';
      var _price = (p && (p.price || p['到手价'] || p.sale_price)) || 0;
      var _gmv = (p && (p.gmv_range || p.gmv || p['结算额'] || p.settlement)) || '';
      var _ratio = parseFloat((p && (p.ratio || p.share || p['占比'] || p.percentage)) || 0);
      var _videoCount = (p && (p.video_count || p['关联短视频数'] || p.videoCount)) || '';
      var _matchReason = (p && (p.match_reason || p.reason)) || '';
      var _matchScore = parseFloat((p && (p.match_score || p.score)) || 0);
      // ★ ui/talent-detail-redesign-v2-cleanup commit 5 (老大 2026-09-23):
      //   - 删首字色块占位符 (老大截图 "优【钢" 字块, 丑)
      //   - 改规范商品卡: SKU 编号 + 商品名 + 品牌 + 价格 + 匹配理由 + 关联视频
      //   - 占比/match_score 为 0 时不显示 "0.0%" / "0 分" 占位
      var _metaParts = [_shop];
      if (_price) _metaParts.push('¥' + (parseFloat(_price) || 0).toFixed(2));
      if (_gmv) _metaParts.push('结算额 ' + _gmv);
      var _meta = _metaParts.join(' · ');
      html += '<div class="talents-product-card">';
      // 替换首字色块: 用 SKU 编号 + 渐变蓝底显示更规范
      html += '<div class="talents-product-sku" style="background:linear-gradient(135deg, #1677ff, #0E62D9);color:#fff;font-weight:600;font-size:13px;">#' + (pIdx + 1) + '</div>';
      html += '<div class="talents-product-info">';
      html += '<div class="talents-product-name">' + escapeHtml(_name) + '</div>';
      html += '<div class="talents-product-meta">' + escapeHtml(_meta) + '</div>';
      if (_matchReason) html += '<div class="talents-product-meta" style="margin-top:4px;color:var(--text-secondary);">' + escapeHtml(_matchReason) + '</div>';
      if (_videoCount && _videoCount !== '0') html += '<div class="talents-product-meta" style="margin-top:2px;color:var(--text-secondary);font-size:11px;">关联视频 ' + escapeHtml(String(_videoCount)) + ' 个</div>';
      html += '</div>';
      // 右上角评分: 占比/match_score 都为 0 时整段不渲染 (无数据走空状态)
      if (sourceLabel) {
        // OCR 模式: 占比 >0 才显示
        if (_ratio > 0) {
          html += '<div class="talents-product-score"><div class="talents-product-score-value">' + _ratio.toFixed(1) + '%</div><div class="talents-mid-score-label">占比</div></div>';
        }
      } else {
        // API 模式: 匹配分 >0 才显示
        if (_matchScore > 0) {
          html += '<div class="talents-product-score"><div class="talents-product-score-value">' + _matchScore.toFixed(0) + '</div><div class="talents-mid-score-label">匹配分</div></div>';
        }
      }
      html += '</div>';
    });
    section.innerHTML = html;
  }
  apiFetch('/api/talents/' + encodeURIComponent(t.id) + '/products?limit=10').then(function(r){return r.json();}).then(function(data){
    var products = data.products || [];
    if (products.length > 0) {
      _renderProducts(products, '');
      return;
    }
    // ★ fallback ocr_raw_fields.top_products / hot_products
    var ocr = t.ocr_raw_fields;
    if (ocr) {
      var _tp = ocr.top_products || ocr.hot_products;
      if (Array.isArray(_tp) && _tp.length) {
        _renderProducts(_tp, '· OCR 识别');
        return;
      }
      if (_tp && typeof _tp === 'object' && !Array.isArray(_tp) && Object.keys(_tp).length) {
        // dict 格式 {商品: 占比} → 转成 [{name, ratio}]
        var _items = Object.keys(_tp).map(function(k){
          return { name: k, ratio: _tp[k] };
        });
        _renderProducts(_items, '· OCR 识别');
        return;
      }
    }
    _renderProducts([], '');
  }).catch(function(e){
    section.innerHTML = '<div class="talents-panel-title">带货商品列表</div><div class="products-empty"><div class="products-empty-text">加载失败</div></div>';
  });
}

function renderTalentPanelFans(t) {
  var el = document.getElementById('talentsPanelFans');
  if (!el) return;
  var mock = _getTalentMockData(t);
  var fans = t.fans_profile || mock.fans_profile || {};
  // ★ fix/ocr-panel-fallback: 老大反馈 '粉丝画像标签页没读 ocr_raw_fields'
  //   talent 表 fan_gender/fan_age/fan_region/fan_city_tier 等列通常空
  //   (_OCR_TO_TALENT_FIELDS 只同步 17 个标量字段, 没同步这些 dict 字段)
  //   OCR 识别的粉丝画像都在 ocr_raw_fields 里, 这里加 ocr_raw_fields 兜底
  var ocr = t.ocr_raw_fields || {};
  // ★ fix/live-dist-incomplete (2026-09-30): _distribution_incomplete 后端可能返 JSON 字符串,
  //   前端 typeof==='object' 判断会漏 → 这里统一 parse 成对象供 renderDist / 标题判断使用.
  var _incompleteObj = {};
  if (t._distribution_incomplete) {
    if (typeof t._distribution_incomplete === 'object') {
      _incompleteObj = t._distribution_incomplete;
    } else {
      try { _incompleteObj = JSON.parse(t._distribution_incomplete) || {}; } catch (e) { _incompleteObj = {}; }
    }
  }
  // _pickOcr: 按顺序找第一个非空值 (dict 类型用 Object.keys.length 判空)
  function _pickOcrDict() {
    for (var i = 0; i < arguments.length; i++) {
      var v = ocr[arguments[i]];
      if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length) return v;
    }
    return null;
  }
  function _pickOcrScalar() {
    for (var i = 0; i < arguments.length; i++) {
      var v = ocr[arguments[i]];
      if (v && (typeof v !== 'object' || Array.isArray(v))) return v;
    }
    return '';
  }
  // ★ fix/vision-json-repair: "标签+百分比" 字符串 → 单档分布 {label: pct}.
  //   vision 模型对性别/年龄/人群/客单价/品类常给 "女性95%" / "31-40岁44%" / "资深中产18%" 这种串,
  //   canonical 只解析 dict, 字符串维度落库成 {} → 前端整块丢. 这里只做提取 (不造数):
  //   拆出尾部数字占比与中文标签, 拆不出返 null.
  function _parseTraitString(v) {
    if (v == null) return null;
    if (typeof v === 'object') return (v && !Array.isArray(v) && Object.keys(v).length) ? v : null;
    var s = String(v).trim();
    if (!s) return null;
    var m = s.match(/^(.*?)(\d+(?:\.\d+)?)\s*%?$/);
    if (!m) return null;
    var label = m[1].replace(/[：:]+$/, '').trim();
    var num = parseFloat(m[2]);
    if (!label || isNaN(num)) return null;
    var o = {}; o[label] = num; return o;
  }
  // 分布取值兜底: 顶层 canonical/ocr dict → extra_fields.<中文段>.<维度中文键> 字符串解析.
  function _distFallback(topKey, blockName, cnKey) {
    var top = ocr[topKey];
    if (top && typeof top === 'object' && !Array.isArray(top) && Object.keys(top).length) return top;
    var extra = ocr.extra_fields;
    if (extra && extra[blockName] && extra[blockName][cnKey] != null) {
      return _parseTraitString(extra[blockName][cnKey]);
    }
    return null;
  }
  var html = '';

  // ★ 档位自然排序: 第 3 参 fieldKey 决定排序规则
  //   (年龄 / 客单价 / 城市等级 = 自然序, 性别 / 地域 / 设备 / 活跃度 / 人群 / 品类 = 维持占比降序)
  function renderDist(title, obj, fieldKey, forceIncomplete) {
    if (!obj || (typeof obj === 'object' && !Array.isArray(obj) && Object.keys(obj).length === 0)) return '';
    var entries = _mergeDistByNormalizedKey(obj, _distOrderForField(fieldKey));
    if (!entries.length) return '';
    // ★ fix/live-dist-incomplete: 残档不硬凑 100.
    //   incomplete = 后端 sidecar 标记 (fieldKey) / 调用方 forceIncomplete / 单档 (OCR 抓不全).
    //   残档时每档直接显示原始数值 (本就是占比) 或标注 "原始值", 不再按 total 比例放大成 100%.
    var _isIncomplete = !!forceIncomplete || (fieldKey && _incompleteObj[fieldKey]) || entries.length === 1;
    var total = entries.reduce(function(a, b){ return a + b.value; }, 0);
    var section = '<div class="talents-panel-section"><div class="talents-panel-title">' + title + (_isIncomplete ? '（分布不完整）' : '') + '</div>';
    entries.forEach(function(e){
      var pct;
      if (_isIncomplete) {
        // 残档: 值在 0-100 区间当占比直接显示; 否则视为原始计数, 显示原始值不换算成百分比
        pct = (e.value >= 0 && e.value <= 100) ? e.value : null;
      } else {
        pct = total ? (e.value / total * 100) : 0;
      }
      var _label = pct === null ? escapeHtml(String(e.value)) : (pct.toFixed(1) + '%');
      var _fill = pct === null ? 0 : Math.min(pct, 100);
      section += '<div class="products-progress-item"><div class="products-progress-header"><span>' + escapeHtml(e.key) + '</span><span>' + _label + '</span></div><div class="products-progress-bar"><div class="products-progress-fill" style="width:' + _fill + '%"></div></div></div>';
    });
    section += '</div>';
    return section;
  }

  // 紧凑分布 (内嵌单行 Top-N)
  //   - dict      → 'key1 pct% · key2 pct% · key3 pct%' (Top N 按 value 降序)
  //   - JSON 串   → JSON.parse 后走 dict 路径 (治 OCR v3 存 JSON 字符串未 parse 的兼容)
  //   - 标量 string → escapeHtml 原样输出 (治 demo/mock 老数据, e.g. '精致妈妈')
  //   - 空 / null / undefined → '-'
  // 治 fan_crowd / fan_price_range / fan_category 等原本按标量设计、但 Mini OCR v3
  //   (b80f16b) 收进 _DIST_FIELDS_NORMALIZE 变 dict 后前端 escapeHtml 误 dump JSON 字符串的 bug
  // key 归一映射: ★ 老大 2026-09-29 brief 钉死 — OCR 模型偶尔把 'Z世代' 误识别为 'genz',
  //   这里只换这一个 key 的显示文本, 其他 key 原样 (不要扩大归一, 跟 _normalize_dist_key 解耦)
  var _INLINE_DIST_KEY_DISPLAY = { 'genz': 'Z世代' };
  function _inlineDistTopN(v, n) {
    if (v === null || v === undefined || v === '') return '-';
    var obj = v;
    if (typeof v === 'string') {
      var trimmed = v.trim();
      if (trimmed.charAt(0) === '{') {
        try { obj = JSON.parse(trimmed); } catch (e) { return escapeHtml(v); }
      } else {
        return escapeHtml(v);
      }
    }
    if (typeof obj === 'object' && !Array.isArray(obj)) {
      var entries = Object.keys(obj)
        .map(function(k){ return { k: (_INLINE_DIST_KEY_DISPLAY[k] || k), v: Number(obj[k]) || 0 }; })
        .filter(function(e){ return e.k && e.v > 0; })
        .sort(function(a, b){ return b.v - a.v; })
        .slice(0, n || 3);
      if (!entries.length) return '-';
      return entries.map(function(e){ return escapeHtml(e.k) + ' ' + e.v.toFixed(1) + '%'; }).join(' · ');
    }
    return escapeHtml(String(v));
  }

  // 粉丝画像总览 (加 ocr 兜底)
  var hasFansData = ((fans.cities && fans.cities.length) || (fans.interest && fans.interest.length) || _pickOcrDict('fan_gender','fan_group_gender') || _pickOcrDict('fan_age','fan_group_age') || _pickOcrDict('fan_region','fan_city_tier','live_audience_city_tier'));
  if (hasFansData) {
    html += '<div class="talents-panel-section">';
    html += '<div class="talents-panel-title">粉丝画像</div>';
    html += '<div class="talents-info-grid">';
    if (fans.cities && fans.cities.length) {
      html += '<div class="talents-info-item full-width"><label>TOP 城市</label><div class="value">' + fans.cities.slice(0, 5).map(function(c){ return escapeHtml(c); }).join('、') + '</div></div>';
    }
    if (fans.interest && fans.interest.length) {
      html += '<div class="talents-info-item full-width"><label>兴趣偏好</label><div class="value"><div class="talents-overview-tags" style="margin-top:0;">' + fans.interest.map(function(i){ return '<span class="talents-overview-tag">' + escapeHtml(i) + '</span>'; }).join('') + '</div></div></div>';
    }
    html += '</div></div>';
  }

  // ★ ocr 兜底: 性别 / 年龄 / 地域 / 城市等级 / 设备 / 活跃度
  var _fanGender = (t.fan_gender && Object.keys(t.fan_gender).length) ? t.fan_gender : (_pickOcrDict('fan_gender','fan_group_gender') || fans.gender);
  var _fanAge = (t.fan_age && Object.keys(t.fan_age).length) ? t.fan_age : (_pickOcrDict('fan_age','fan_group_age') || fans.age);
  var _fanRegion = (t.fan_region && Object.keys(t.fan_region).length) ? t.fan_region : (_pickOcrDict('fan_region') || (fans.region || {}));
  html += renderDist('性别分布', _fanGender);
  html += renderDist('年龄分布', _fanAge, 'fan_age');
  html += renderDist('地域分布', _fanRegion);
  // ★ 2026-09-29 老大: 城市等级分布取值改跟 L32056-57 性别/年龄同模式
  //   优先级: t.fan_city_tier (canonical 列, OCR v3 落库后的真实来源)
  //          → t.video_audience_city_tier
  //          → _pickOcrDict('fan_city_tier','live_audience_city_tier','video_audience_city_tier') 兜底
  //   根因: 老写法只读 ocr_raw_fields, canonical 列有值时反而取不到 → 城市等级分布整块不显示.
  //   加 typeof/Array.isArray 守卫: Object.keys('字符串') 非空会误判为真, 跟 _distSource 同一套.
  var _cityTier = (t.fan_city_tier && typeof t.fan_city_tier === 'object' && !Array.isArray(t.fan_city_tier) && Object.keys(t.fan_city_tier).length) ? t.fan_city_tier
                : ((t.video_audience_city_tier && typeof t.video_audience_city_tier === 'object' && !Array.isArray(t.video_audience_city_tier) && Object.keys(t.video_audience_city_tier).length) ? t.video_audience_city_tier
                   : _pickOcrDict('fan_city_tier','live_audience_city_tier','video_audience_city_tier'));
  if (_cityTier) {
    // ★ v4 amend: 4 个 city_tier 字段 (fan_city_tier / fan_group_city_tier / live_audience_city_tier /
    //   video_audience_city_tier) 任一 incomplete → 标题后追加 "（分布不完整）" 提示用户原始值不完整
    var _ctIncomplete = (t._distribution_incomplete && typeof t._distribution_incomplete === 'object' && (
      t._distribution_incomplete['fan_city_tier'] ||
      t._distribution_incomplete['fan_group_city_tier'] ||
      t._distribution_incomplete['live_audience_city_tier'] ||
      t._distribution_incomplete['video_audience_city_tier']
    )) ? '（分布不完整）' : '';
    html += renderDist('城市等级分布' + _ctIncomplete, _cityTier, 'fan_city_tier');
  }  var _device = _pickOcrDict('fan_group_device');
  if (_device) html += renderDist('粉丝团设备', _device);
  var _activity = _pickOcrDict('fan_group_activity');
  if (_activity) html += renderDist('粉丝团活跃度', _activity);
  // ★ 人群偏好区三路取源 (dict / JSON 串 / 老标量) — 2026-09-29 merge 合并时统一
  //   根因: OCR v3 (b80f16b) 把 fan_crowd/fan_price_range/fan_category 收进 _DIST_FIELDS_NORMALIZE
  //   后变成 dict, 老写法 (typeof x === 'string' ? x : '') 会把 dict 判空 → 价格带/类目偏好显 '-'.
  //   8e46d01 只治了人群画像那一格 (_fanCrowd 没有 typeof 守卫), 这两格仍被守卫挡着, 这次一并修.
  function _distSource(field, ocrKeyA, ocrKeyB) {
    var v = t[field];
    if (v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length) return v;
    if (typeof v === 'string' && v.trim()) return v;
    return _pickOcrDict(ocrKeyA, ocrKeyB) || _pickOcrScalar(ocrKeyA, ocrKeyB);
  }
  // 人群画像: 8 项每行 '\n' 纯文本 (老大 2026-09-29 拍板), genz → Z世代, 禁甩原始 JSON
  var _fanCrowd = _renderCrowdPrefText(_distSource('fan_crowd', 'fan_crowd', 'fan_group_crowd'));
  var _fanPrice = _distSource('fan_price_range', 'fan_price_range', 'fan_group_price');
  var _fanCategory = _distSource('fan_category', 'fan_category', 'fan_group_category');
  if (_fanCrowd || _fanPrice || _fanCategory) {
    html += '<div class="talents-panel-section"><div class="talents-panel-title">人群偏好</div>';
    html += '<div class="products-detail-grid">';
    // 人群画像: 8 项每行 (7ff3739 + 64eb782); 价格带/类目偏好: 保留 dev 8e46d01 的 _inlineDistTopN Top3
    html += '<div class="products-detail-item"><div class="products-detail-label">人群画像</div><div class="products-detail-value">' + escapeHtml(_fanCrowd || '待补充') + '</div></div>';
    html += '<div class="products-detail-item"><div class="products-detail-label">价格带</div><div class="products-detail-value">' + _inlineDistTopN(_fanPrice, 3) + '</div></div>';
    html += '<div class="products-detail-item"><div class="products-detail-label">类目偏好</div><div class="products-detail-value">' + _inlineDistTopN(_fanCategory, 3) + '</div></div>';
    html += '</div></div>';
  }

  // ★ fix/mini-test-code-repair-20260925 04:15: 3 个新面板 (沿用 talents-panel-section + renderDist)
  //   - 直播间受众 (live_audience 7 维: gender/age/crowd/price_range/category/region/city_tier)
  //   - 短视频受众 (video_audience 7 维)
  //   - 粉丝端画像 (fan_activity + fan_device 2 维, fan_ 前缀跟 fan_group_ 区分)
  // incomplete 检测按后端实际写 key (老大 brief 钉死别猜):
  //   后端 _set_distribution_incomplete / canonical 实际写入的 key: 4 city_tier + category_distribution
  //   - 直播间 panel: 检测 live_audience_city_tier
  //   - 短视频 panel: 检测 video_audience_city_tier
  //   - 粉丝端 panel: 后端没写 fan_activity / fan_device → 不加 incomplete 检测

  // ----- 直播间受众 (7 维) -----
  var _liveGender = _distFallback('live_audience_gender', '直播间特征', '性别');
  var _liveAge = _distFallback('live_audience_age', '直播间特征', '年龄');
  var _liveCrowd = _distFallback('live_audience_crowd', '直播间特征', '人群');
  var _livePrice = _distFallback('live_audience_price_range', '直播间特征', '客单价');
  var _liveCategory = _distFallback('live_audience_category', '直播间特征', '品类偏好');
  var _liveRegion = _pickOcrDict('live_audience_region');
  var _liveCityTier = _distFallback('live_audience_city_tier', '直播间特征', '城市等级');

  // ★ fix/live-dist-incomplete: 直播间受众数据 OCR 多为残档 (单 key / 原始计数 95,21),
  //   任一维度残档 → 面板标题整体标 "（分布不完整）", 各 renderDist 传对应 incomplete key.
  var _liveAnyIncomplete = (
    _incompleteObj['live_audience_city_tier'] || _incompleteObj['live_audience_age'] ||
    _incompleteObj['live_audience_crowd'] || _incompleteObj['live_audience_price_range'] ||
    _incompleteObj['live_audience_category'] || _incompleteObj['live_audience_region'] ||
    (_liveGender && Object.keys(_liveGender).length === 1)
  );
  if (_liveGender || _liveAge || _liveCrowd || _livePrice || _liveCategory || _liveRegion || _liveCityTier) {
    html += '<div class="talents-panel-section"><div class="talents-panel-title">直播间受众' + (_liveAnyIncomplete ? '（分布不完整）' : '') + '</div>';
    html += renderDist('性别分布', _liveGender, 'live_audience_gender', _liveGender && Object.keys(_liveGender).length === 1);
    html += renderDist('年龄分布', _liveAge, 'live_audience_age');
    html += renderDist('人群偏好', _liveCrowd, 'live_audience_crowd');
    html += renderDist('客单价分布', _livePrice, 'live_audience_price_range');
    html += renderDist('品类偏好', _liveCategory, 'live_audience_category');
    html += renderDist('地域分布', _liveRegion, 'live_audience_region');
    html += renderDist('城市等级分布', _liveCityTier, 'live_audience_city_tier');
    html += '</div>';
  }

  // ----- 短视频受众 (7 维) -----
  var _videoGender = _distFallback('video_audience_gender', '短视频特征', '性别');
  var _videoAge = _distFallback('video_audience_age', '短视频特征', '年龄');
  var _videoCrowd = _distFallback('video_audience_crowd', '短视频特征', '人群');
  var _videoPrice = _distFallback('video_audience_price_range', '短视频特征', '客单价');
  var _videoCategory = _distFallback('video_audience_category', '短视频特征', '品类偏好');
  var _videoRegion = _pickOcrDict('video_audience_region');
  var _videoCityTier = _distFallback('video_audience_city_tier', '短视频特征', '城市等级');

  if (_videoGender || _videoAge || _videoCrowd || _videoPrice || _videoCategory || _videoRegion || _videoCityTier) {
    var _videoIncomplete = (t._distribution_incomplete && typeof t._distribution_incomplete === 'object' && (
      t._distribution_incomplete['video_audience_city_tier']
    )) ? '（分布不完整）' : '';
    html += '<div class="talents-panel-section"><div class="talents-panel-title">短视频受众' + _videoIncomplete + '</div>';
    html += renderDist('性别分布', _videoGender);
    html += renderDist('年龄分布', _videoAge, 'video_audience_age');
    html += renderDist('人群偏好', _videoCrowd);
    html += renderDist('客单价分布', _videoPrice, 'video_audience_price_range');
    html += renderDist('品类偏好', _videoCategory);
    html += renderDist('地域分布', _videoRegion);
    html += renderDist('城市等级分布', _videoCityTier, 'video_audience_city_tier');
    html += '</div>';
  }

  // ----- 粉丝端画像 (fan_activity + fan_device, fan_ 前缀跟 fan_group_ 区分) -----
  // 后端没写 fan_activity / fan_device incomplete 标记, 不加 incomplete 检测
  var _fanActivity = (t.fan_activity && Object.keys(t.fan_activity).length) ? t.fan_activity : _pickOcrDict('fan_activity');
  var _fanDevice = (t.fan_device && Object.keys(t.fan_device).length) ? t.fan_device : _pickOcrDict('fan_device');

  if (_fanActivity || _fanDevice) {
    html += '<div class="talents-panel-section"><div class="talents-panel-title">粉丝端画像</div>';
    html += renderDist('活跃度分布', _fanActivity);
    html += renderDist('设备分布', _fanDevice);
    html += '</div>';
  }

  if (!html) html = '<div class="products-empty"><div class="products-empty-text">暂无粉丝画像数据</div></div>';
  el.innerHTML = html;
}

function renderTalentPanelProducts(t) {
  var el = document.getElementById('talentsPanelProducts');
  if (!el) return;
  _currentTalentMatchProducts = { talent: t, matches: [], sortBy: 'score' };
  var html = '<div class="talents-panel-section">';
  html += '<div class="ai-match-header">';
  html += '<div class="talents-panel-title" style="margin:0;padding:0;border:none;">AI 推荐可合作商品</div>';
  html += '<div class="ai-match-actions">';
  html += '<select class="ai-match-sort" id="talentProductSort" onchange="onTalentProductSortChange(this.value)">';
  html += '<option value="score">按匹配度</option>';
  html += '<option value="commission">按佣金率</option>';
  html += '<option value="price">按价格</option>';
  html += '</select>';
  html += '<button class="ai-match-btn" id="btnMatchTalentProducts" onclick="runTalentProductMatch(\'' + escapeAttr(t.id) + '\')">AI 匹配商品</button>';
  html += '</div></div>';
  html += '<div id="talentProductMatchBody" class="ai-match-body">';
  html += '<div class="products-empty"><div class="products-empty-text">点击上方「AI 匹配商品」按钮，为该达人智能推荐最合适的商品</div></div>';
  html += '</div></div>';
  el.innerHTML = html;
}

function onTalentProductSortChange(sortBy) {
  _currentTalentMatchProducts.sortBy = sortBy;
  _renderTalentProductMatches();
}

function runTalentProductMatch(talentId) {
  var btn = document.getElementById('btnMatchTalentProducts');
  if (btn) { btn.disabled = true; btn.textContent = '匹配中...'; }
  var body = document.getElementById('talentProductMatchBody');
  if (body) body.innerHTML = '<div class="products-loading"><div class="spinner"></div>AI 正在分析匹配...</div>';
  apiFetch('/api/talents/' + encodeURIComponent(talentId) + '/match-products', {
    method: 'POST',
    body: JSON.stringify({ limit: 10, agentId: getCurrentEmpId() || '' })
  }).then(function(r){ return r.json(); }).then(function(data){
    _currentTalentMatchProducts.matches = data.matches || [];
    _currentTalentMatchProducts.degraded = !!data.degraded;  // ★ feat/p03-quality-loop 交付 2
    _currentTalentMatchProducts.degrade_reason = data.degrade_reason || null;
    _renderTalentProductMatches();
    if (data.degraded) {
      // ★ feat/p03-quality-loop: 不再吞错 — 降级时显示明确提示, 区分 timeout/empty/parse_error
      showDegradeToast(data.degrade_reason, 'talent');
    }
    if (btn) { btn.disabled = false; btn.textContent = '重新匹配'; }
  }).catch(function(e){
    // ★ feat/p03-quality-loop: 不再吞错 — 网络/权限错显式区分
    console.error('[runTalentProductMatch] network error', e);
    if (body) body.innerHTML = '<div class="products-empty"><div class="products-empty-text">网络错误或权限不足，请重试 (err: ' + (e && e.message || e) + ')</div></div>';
    if (typeof showToast === 'function') showToast('⚠️ 网络错误或权限不足: ' + (e && e.message || e), 'error');
    if (btn) { btn.disabled = false; btn.textContent = 'AI 匹配商品'; }
  });
}

function _renderTalentProductMatches() {
  var body = document.getElementById('talentProductMatchBody');
  if (!body) return;
  var matches = (_currentTalentMatchProducts.matches || []).slice();
  var sortBy = _currentTalentMatchProducts.sortBy || 'score';
  matches.sort(function(a, b){
    if (sortBy === 'commission') {
      var ca = a.product && a.product.commission_rate ? a.product.commission_rate : 0;
      var cb = b.product && b.product.commission_rate ? b.product.commission_rate : 0;
      return cb - ca;
    }
    if (sortBy === 'price') {
      var pa = a.product && a.product.price ? a.product.price : 0;
      var pb = b.product && b.product.price ? b.product.price : 0;
      return pb - pa;
    }
    return (b.score || 0) - (a.score || 0);
  });
  if (matches.length === 0) {
    body.innerHTML = '<div class="products-empty"><div class="products-empty-text">暂无匹配商品</div></div>';
    return;
  }
  var html = '';
  matches.forEach(function(m){
    var p = m.product || {};
    var reason = m.aiReason || (m.reasons && m.reasons[0]) || '';
    html += '<div class="ai-match-card">';
    html += '<div class="ai-match-card-left">';
    html += '<div class="ai-match-card-avatar" style="background:' + (p.brand === 'COOLCHAP' ? '#FF6B35' : '#1677ff') + ';">' + (p.name || '?').charAt(0) + '</div>';
    html += '</div>';
    html += '<div class="ai-match-card-main">';
    html += '<div class="ai-match-card-title">' + escapeHtml(p.name || '-') + '</div>';
    html += '<div class="ai-match-card-meta">' + escapeHtml(p.brand || '-') + ' · ' + escapeHtml(p.category || '未分类') + ' · ¥' + (p.price || 0).toFixed(2) + '</div>';
    html += '<div class="ai-match-card-tags">';
    var matchCommText = (p.commission_rates && Object.keys(p.commission_rates).length > 0) ? Object.keys(p.commission_rates).map(function(k){ return p.commission_rates[k] + '%'; }).join('/') : (p.commission_rate || 0) + '%';
    html += '<span class="ai-match-tag">佣金率 ' + matchCommText + '</span>';
    html += '</div>';
    html += '<div class="ai-match-card-reason">' + escapeHtml(reason) + '</div>';
    html += '</div>';
    html += '<div class="ai-match-card-right">';
    html += '<div class="ai-match-score">' + (m.matchPercent || 0).toFixed(0) + '<span>%</span></div>';
    html += '<div class="ai-match-score-label">匹配度</div>';
    html += '</div>';
    html += '</div>';
  });
  body.innerHTML = html;
}

// ═══ 达人详情 - 合作记录（deals API）═══
var _currentTalentDeals = [];

function _dealStatusLabel(s) {
  return { pending: '待推进', negotiating: '洽谈中', sample_sent: '已寄样', approved: '已确认', live: '已上播', completed: '已完成', failed: '已失败' }[s] || s || '待推进';
}

function _dealWinLossLabel(c) {
  return { price_commission: '价格佣金', tone_mismatch: '调性不符', product_weak: '商品力不足', experience: '对接体验', competitor: '竞品截胡', schedule: '档期问题', other: '其他' }[c] || '';
}

function renderTalentPanelRecords(t) {
  var el = document.getElementById('talentsPanelRecords');
  if (!el) return;
  var header = '<div class="talents-follow-up-header"><div class="talents-panel-title" style="margin:0;padding:0;border:none;">合作记录</div><button class="talents-follow-up-add" onclick="createNewDeal()">+ 新增合作</button></div>';
  el.innerHTML = '<div class="talents-panel-section">' + header + '<div class="products-loading">加载中...</div></div>';
  apiFetch('/api/deals?talent_id=' + encodeURIComponent(t.id) + '&limit=50').then(function(r){return r.json();}).then(function(data){
    var deals = data.deals || [];
    _currentTalentDeals = deals;
    var html = '<div class="talents-panel-section">' + header;
    if (deals.length === 0) {
      html += '<div class="products-empty"><div class="products-empty-text">暂无合作记录，点击新增</div></div></div>';
      el.innerHTML = html;
      return;
    }
    deals.forEach(function(d){ html += _renderDealCardHTML(d); });
    html += '</div>';
    el.innerHTML = html;
  }).catch(function(e){
    el.innerHTML = '<div class="talents-panel-section">' + header + '<div class="products-empty"><div class="products-empty-text">加载失败</div></div></div>';
  });
}

function _renderDealCardHTML(d) {
  var status = d.status || 'pending';
  var isTerminal = (status === 'completed' || status === 'failed');
  var dateStr = '-';
  if (d.created_at) {
    var dt = new Date(d.created_at * 1000);
    dateStr = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0');
  }
  var html = '<div class="talents-record-item">';
  html += '<div class="talents-record-date">' + escapeHtml(dateStr) + '</div>';
  html += '<div class="talents-record-body">';
  var title = '<span class="deal-status-badge ' + escapeAttr(status) + '">' + escapeHtml(_dealStatusLabel(status)) + '</span>' + escapeHtml(d.product_name || '未关联商品');
  if (d.deal_type) title += ' · ' + escapeHtml(d.deal_type);
  if (d.commission_rate) title += ' · 佣金 ' + escapeHtml(String(d.commission_rate)) + '%';
  html += '<div class="talents-record-title">' + title + '</div>';
  if (d.scheduled_at) html += '<div class="deal-meta">排期：' + escapeHtml(new Date(d.scheduled_at * 1000).toLocaleDateString('zh-CN')) + '</div>';
  if (status === 'completed') {
    var parts = [];
    if (d.actual_gmv) parts.push('GMV ¥' + formatNumber(d.actual_gmv));
    if (d.actual_roi) parts.push('ROI ' + d.actual_roi);
    if (d.actual_units) parts.push('销量 ' + formatNumber(d.actual_units));
    if (parts.length) html += '<div class="deal-meta">成果：' + escapeHtml(parts.join(' / ')) + '</div>';
  }
  if (d.result_note) html += '<div class="talents-record-note">' + escapeHtml(d.result_note) + '</div>';
  if (isTerminal && d.win_loss_category) {
    var wlcLabel = _dealWinLossLabel(d.win_loss_category) || d.win_loss_category;
    html += '<div class="deal-meta">成败原因：<span class="deal-status-badge ' + escapeAttr(status) + '">' + escapeHtml(wlcLabel) + '</span></div>';
  }
  if (d.predicted_conclusion) html += '<div class="deal-predicted"><span class="deal-predicted-tag">AI预测</span>' + escapeHtml(d.predicted_conclusion) + '</div>';
  html += '<div class="talents-timeline-actions" style="margin-top:6px;">';
  if (!isTerminal) html += '<button onclick="editDeal(\'' + escapeAttr(String(d.id)).replace(/'/g,"\\'") + '\')">编辑</button>';
  html += '<button class="delete" onclick="deleteDeal(\'' + escapeAttr(String(d.id)).replace(/'/g,"\\'") + '\')">删除</button>';
  html += '</div>';
  html += '</div></div>';
  return html;
}

function createNewDeal() {
  if (!_currentTalent || !_currentTalent.id) { showToast('请先选择达人'); return; }
  openDealModal(_currentTalent.id, null);
}

function editDeal(dealId) {
  var d = (_currentTalentDeals || []).find(function(x){ return String(x.id) === String(dealId); });
  if (!d) return;
  openDealModal(d.talent_id || (_currentTalent && _currentTalent.id), d);
}

function _tsToDateInput(ts) {
  var dt = new Date(ts * 1000);
  return dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
}

function openDealModal(talentId, deal) {
  if (!talentId) return;
  document.getElementById('dealModalOverlay').dataset.talentId = talentId;
  document.getElementById('dealEditId').value = deal ? deal.id : '';
  document.getElementById('dealPredictedEventId').value = deal ? (deal.predicted_event_id || '') : '';
  document.getElementById('dealPredictedConclusion').value = deal ? (deal.predicted_conclusion || '') : '';
  document.getElementById('dealType').value = deal ? (deal.deal_type || '') : '';
  document.getElementById('dealCommissionRate').value = (deal && deal.commission_rate != null) ? deal.commission_rate : '';
  document.getElementById('dealStatus').value = deal ? (deal.status || 'pending') : 'pending';
  document.getElementById('dealScheduledAt').value = (deal && deal.scheduled_at) ? _tsToDateInput(deal.scheduled_at) : '';
  document.getElementById('dealActualGmv').value = (deal && deal.actual_gmv != null) ? deal.actual_gmv : '';
  document.getElementById('dealActualRoi').value = (deal && deal.actual_roi != null) ? deal.actual_roi : '';
  document.getElementById('dealActualUnits').value = (deal && deal.actual_units != null) ? deal.actual_units : '';
  document.getElementById('dealResultNote').value = deal ? (deal.result_note || '') : '';
  document.getElementById('dealWinLossCategory').value = deal ? (deal.win_loss_category || '') : '';
  document.getElementById('dealKeyMoment').value = deal ? (deal.key_moment || '') : '';
  document.getElementById('dealDecisionMakerFeedback').value = deal ? (deal.decision_maker_feedback || '') : '';
  document.getElementById('dealModalTitle').textContent = deal ? '编辑合作' : '新增合作';
  onDealStatusChange();
  _loadDealProductOptions(deal ? deal.product_id : '');
  _loadDealPredictions(talentId, deal ? (deal.predicted_event_id || '') : '');
  document.getElementById('dealModalOverlay').classList.add('active');
}

function closeDealModal(e) { if (e && e.target !== e.currentTarget) return; document.getElementById('dealModalOverlay').classList.remove('active'); }

function onDealStatusChange() {
  var st = document.getElementById('dealStatus').value;
  document.getElementById('dealResultArea').style.display = (st === 'completed' || st === 'failed') ? '' : 'none';
}

function _loadDealProductOptions(selectedId) {
  var sel = document.getElementById('dealProduct');
  sel.innerHTML = '<option value="">加载中...</option>';
  apiFetch('/api/products?limit=100').then(function(r){return r.json();}).then(function(data){
    var products = data.products || (Array.isArray(data) ? data : []);
    var html = '<option value="">请选择商品</option>';
    products.forEach(function(p){
      html += '<option value="' + escapeAttr(String(p.id)) + '"' + (String(p.id) === String(selectedId) ? ' selected' : '') + '>' + escapeHtml(p.name || String(p.id)) + '</option>';
    });
    sel.innerHTML = html;
  }).catch(function(){
    sel.innerHTML = '<option value="">加载失败</option>';
  });
}

function _loadDealPredictions(talentId, selectedEventId) {
  var field = document.getElementById('dealPredictedField');
  var sel = document.getElementById('dealPredictedSelect');
  field.style.display = 'none';
  sel.innerHTML = '<option value="">不关联</option>';
  sel._dealPredictedItems = [];
  apiFetch('/api/knowledge-events?entity_type=talent&entity_id=' + encodeURIComponent(talentId)).then(function(r){return r.json();}).then(function(data){
    var events = data.events || [];
    var items = [];
    events.forEach(function(ev){
      var concl = ev.conclusions;
      if (typeof concl === 'string') { try { concl = JSON.parse(concl); } catch(e) { concl = null; } }
      if (concl && concl.predicted_match) items.push({ id: ev.id, title: ev.title || 'AI 预测', match: concl.predicted_match });
    });
    if (items.length === 0) return;
    var html = '<option value="">不关联</option>';
    items.forEach(function(it){
      var label = it.title + '：' + (it.match.product_name || '');
      if (it.match.confidence != null) label += '（置信度 ' + it.match.confidence + '）';
      html += '<option value="' + escapeAttr(String(it.id)) + '"' + (String(it.id) === String(selectedEventId) ? ' selected' : '') + '>' + escapeHtml(label) + '</option>';
    });
    sel._dealPredictedItems = items;
    sel.innerHTML = html;
    field.style.display = '';
  }).catch(function(){ /* 无预测事件则保持隐藏 */ });
}

function onDealPredictedSelect() {
  var sel = document.getElementById('dealPredictedSelect');
  var items = sel._dealPredictedItems || [];
  var it = items.find(function(x){ return String(x.id) === sel.value; });
  document.getElementById('dealPredictedEventId').value = it ? it.id : '';
  var concl = '';
  if (it) {
    concl = '推荐商品：' + (it.match.product_name || '-');
    if (it.match.confidence != null) concl += '，置信度 ' + it.match.confidence;
    if (it.match.raw_quote) concl += '。依据：' + String(it.match.raw_quote).slice(0, 80);
  }
  document.getElementById('dealPredictedConclusion').value = concl;
}

function saveDeal() {
  var overlay = document.getElementById('dealModalOverlay');
  var talentId = overlay.dataset.talentId;
  if (!talentId) return;
  var id = document.getElementById('dealEditId').value;
  var productSel = document.getElementById('dealProduct');
  var productId = productSel.value;
  if (!productId) { showToast('请选择商品'); return; }
  var productName = productSel.options[productSel.selectedIndex] ? productSel.options[productSel.selectedIndex].textContent : '';
  var status = document.getElementById('dealStatus').value;
  var dateVal = document.getElementById('dealScheduledAt').value;
  var commissionVal = document.getElementById('dealCommissionRate').value;
  var body = {
    talent_id: talentId,
    product_id: productId,
    product_name: productName,
    deal_type: document.getElementById('dealType').value,
    commission_rate: commissionVal === '' ? 0 : parseFloat(commissionVal),
    status: status,
    scheduled_at: dateVal ? Math.floor(new Date(dateVal + 'T00:00:00').getTime() / 1000) : 0,
    predicted_event_id: document.getElementById('dealPredictedEventId').value,
    predicted_conclusion: document.getElementById('dealPredictedConclusion').value
  };
  if (status === 'completed' || status === 'failed') {
    var gmv = document.getElementById('dealActualGmv').value;
    var roi = document.getElementById('dealActualRoi').value;
    var units = document.getElementById('dealActualUnits').value;
    var note = document.getElementById('dealResultNote').value.trim();
    if (status === 'completed' && (gmv === '' || parseFloat(gmv) <= 0) && !note) { showToast('标记为已完成需填写 GMV 或结果备注'); return; }
    var wlc = document.getElementById('dealWinLossCategory').value;
    if (!wlc) { showToast('请选择成败原因'); return; }
    body.actual_gmv = gmv === '' ? 0 : parseFloat(gmv);
    body.actual_roi = roi === '' ? 0 : parseFloat(roi);
    body.actual_units = units === '' ? 0 : parseInt(units, 10);
    body.result_note = note;
    body.win_loss_category = wlc;
    body.key_moment = document.getElementById('dealKeyMoment').value;
    body.decision_maker_feedback = document.getElementById('dealDecisionMakerFeedback').value.trim();
  }
  var url = id ? '/api/deals/' + encodeURIComponent(id) : '/api/deals';
  var method = id ? 'PUT' : 'POST';
  apiFetch(url, { method: method, body: JSON.stringify(body) }).then(function(r){return r.json();}).then(function(res){
    if (res && res.error) { showToast('保存失败: ' + (res.error.message || res.error), 'error'); return; }
    showToast(id ? '✅ 已更新' : '✅ 已新增合作');
    closeDealModal();
    if (_currentTalent) renderTalentPanelRecords(_currentTalent);
    /* 〔feat/deals-records 2026-10-09〕sb2 达人详情 Records tab 同步刷新 */
    if (typeof window.sb2TlnReloadRecords === 'function') window.sb2TlnReloadRecords();
  }).catch(function(){ showToast('❌ 保存失败'); });
}

function deleteDeal(dealId) {
  if (!confirm('确定删除此合作记录？')) return;
  apiFetch('/api/deals/' + encodeURIComponent(dealId), { method: 'DELETE' }).then(function(r){return r.json();}).then(function(res){
    if (res && res.error) { showToast('删除失败: ' + (res.error.message || res.error), 'error'); return; }
    showToast('✅ 已删除');
    if (_currentTalent) renderTalentPanelRecords(_currentTalent);
    /* 〔feat/deals-records 2026-10-09〕sb2 达人详情 Records tab 同步刷新 */
    if (typeof window.sb2TlnReloadRecords === 'function') window.sb2TlnReloadRecords();
  }).catch(function(){ showToast('❌ 删除失败'); });
}

function renderTalentPanelFollowUps(t) {
  var el = document.getElementById('talentsPanelFollowUps');
  if (!el) return;
  el.innerHTML = '<div class="talents-panel-section"><div class="talents-follow-up-header"><div class="talents-panel-title" style="margin:0;padding:0;border:none;">跟进记录</div><button class="talents-follow-up-add" onclick="createNewTalentFollowUp()">+ 新增跟进</button></div><div class="products-loading">加载中...</div></div>';
  apiFetch('/api/talents/' + encodeURIComponent(t.id) + '/follow-ups').then(function(r){return r.json();}).then(function(data){
    var followUps = data.follow_ups || [];
    // ★ ui/talent-detail-redesign-v2-cleanup: 删 _getMockTalentFollowUps 假数据兜底
    //   老大 2026-09-23: 跟进记录为空时直接显示"暂无跟进记录"占位, 不塞 fake 跟进
    _currentTalentFollowUps = followUps;
    el.innerHTML = _renderTalentFollowUpsHTML(t, followUps);
  }).catch(function(e){
    el.innerHTML = '<div class="talents-panel-section"><div class="talents-follow-up-header"><div class="talents-panel-title" style="margin:0;padding:0;border:none;">跟进记录</div><button class="talents-follow-up-add" onclick="createNewTalentFollowUp()">+ 新增跟进</button></div><div class="products-empty"><div class="products-empty-text">加载失败</div></div></div>';
  });
}

// ★ ui/talent-detail-redesign-v2-cleanup: _getMockTalentFollowUps 已删除 (老大 2026-09-23)
//   原函数用 hash 合成 "张经理/李运营/王商务/赵达人" 假跟进记录
//   跟进记录为空时显示"暂无跟进记录"占位, 等真实跟进数据接入

function _renderTalentFollowUpsHTML(t, followUps) {
  var html = '<div class="talents-panel-section">';
  html += '<div class="talents-follow-up-header">';
  html += '<div class="talents-panel-title" style="margin:0;padding:0;border:none;">跟进记录 (' + followUps.length + ')</div>';
  html += '<button class="talents-follow-up-add" onclick="createNewTalentFollowUp()">+ 新增跟进</button>';
  html += '</div>';
  html += '<div class="talents-timeline">';
  followUps.forEach(function(fu){
    var statusClass = fu.status || 'completed';
    var statusLabel = { completed: '已完成', planned: '待跟进', cancelled: '已取消' }[statusClass] || '已完成';
    var time = fu.follow_up_at ? new Date(fu.follow_up_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
    var nextTime = fu.next_follow_up_at ? ('下次：' + new Date(fu.next_follow_up_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })) : '';
    html += '<div class="talents-timeline-item">';
    html += '<div class="talents-timeline-dot ' + statusClass + '"></div>';
    html += '<div class="talents-timeline-card">';
    html += '<div class="talents-timeline-meta">';
    html += '<div class="talents-timeline-time">' + escapeHtml(time) + '</div>';
    html += '<div class="talents-timeline-by">' + escapeHtml(fu.follow_up_by || '未指定') + '</div>';
    html += '</div>';
    if (fu.content) html += '<div class="talents-timeline-content">' + escapeHtml(fu.content) + '</div>';
    if (fu.result) html += '<div class="talents-timeline-result">结果：' + escapeHtml(fu.result) + '</div>';
    html += '<div class="talents-timeline-footer">';
    html += '<span class="talents-timeline-status ' + statusClass + '">' + statusLabel + '</span>';
    html += '<div class="talents-timeline-actions">';
    if (nextTime) html += '<span style="font-size:11px;color:var(--text-secondary);">' + escapeHtml(nextTime) + '</span>';
    if (!fu.is_mock) {
      html += '<button onclick="editTalentFollowUp(\'' + escapeAttr(fu.id).replace(/'/g,"\\'") + '\')">编辑</button>';
      html += '<button class="delete" onclick="deleteTalentFollowUp(\'' + escapeAttr(fu.id).replace(/'/g,"\\'") + '\')">删除</button>';
    }
    html += '</div></div>';
    html += '</div></div>';
  });
  html += '</div></div>';
  return html;
}

// ═══ 达人详情 - 分析档案（knowledge_events 时间线）═══
// ★ ui/talent-detail-redesign-v2-cleanup commit 7 (老大 2026-09-23):
//   原始 OCR 字段移到分析档案 tab 做语义分组折叠,字段名全中文
//   6 维度: 商业核心 / 基础 / 粉丝画像 / 群组粉丝 / 热卖分布 / 直播数据
//   dict/list 格式化 (数字 ≤100 加 %, list bullet, 不显示 JSON 字符串和英文 key)

// OCR 字段名 → 中文标签映射 (从 commit 3 删除的版本恢复 + commit 7 扩展)
var OCR_FIELD_LABELS = {
  name: '达人昵称', douyin_id: '抖音号', city: '所在地', location: '地区', followers: '粉丝量', level: '等级',
  bio: '简介', tags: '内容标签', main_category: '主营类目', cooperation_status: '合作状态', agency: '所属机构',
  talent_type: '内容类型', content_style: '内容风格', product_count: '带货商品数', total_history_days: '历史带货天数',
  total_shops: '合作店铺数', total_gmv: '总结算额', live_ratio: '直播占比', live_sessions: '直播场次',
  live_views: '直播观看量', video_ratio: '视频占比', video_count: '视频数', video_plays: '视频播放量',
  single_video_settlement: '单视频结算', video_gpm: '视频 GPM', live_gpm: '直播 GPM',
  video_settlement_ratio: '视频结算额占比', video_avg_price: '视频平均件单价',
  video_completion_rate: '视频完播率', video_likes: '视频点赞数', video_comments: '视频评论数',
  video_shares: '视频转发数', video_interaction_rate: '视频互动率', average_price: '平均件单价',
  fulfillment_score: '履约分', rating_score: '评分', top_products: '带货商品 TOP',
  top_categories: '热卖类目 TOP', top_brands: '热卖品牌 TOP',
  fan_gender: '粉丝性别', fan_age: '粉丝年龄', fan_city_tier: '粉丝城市等级', fan_crowd: '粉丝人群',
  fan_price_range: '粉丝价格带', fan_category: '粉丝品类偏好', fan_region: '粉丝地域',
  fan_group_gender: '粉丝团性别', fan_group_age: '粉丝团年龄', fan_group_crowd: '粉丝团人群',
  fan_group_activity: '粉丝团活跃度', fan_group_device: '粉丝团设备', fan_group_price: '粉丝团价格带',
  fan_group_category: '粉丝团品类',
  live_audience_region: '直播间观众地域', live_audience_city_tier: '直播间观众城市等级',
  video_audience_region: '短视频观众地域', video_audience_city_tier: '短视频观众城市等级',
  extra_fields: '其他字段 (extra)', account_fans_profile: '账号粉丝特征',
  video_fans_profile: '短视频粉丝特征', remark: '备注', notes: '备注',
  price_distribution: '价格带分布', category_distribution: '类目分布', brand_distribution: '品牌集中度',
  cooperation_days: '合作天数', cooperating_brands: '合作中品牌', brand_details: '品牌详情',
  brand_commission: '品牌佣金', cooperation_brand_count: '合作品牌数',
  products: '带货商品', data_period: '数据周期', live_stream_sessions: '直播场次',
  live_stream_viewers: '直播总观看', avg_live_gmv: '场均直播 GMV', cooperation_status: '合作状态'
};

// 6 维度分组
var OCR_FIELD_GROUPS = {
  '商业核心': ['name', 'douyin_id', 'followers', 'total_gmv', 'monthly_settlement', 'gpm', 'feishu_gpm', 'feishu_product_count', 'feishu_shops', 'single_video_settlement', 'product_count', 'total_products', 'average_price', 'rating_score', 'fulfillment_score'],
  '基础': ['talent_type', 'category', 'main_category', 'content_style', 'bio', 'city', 'location', 'level', 'agency', 'tags', 'cooperation_status', 'real_name', 'phone', 'wechat', 'email', 'contact_phone', 'contact_wechat', 'contact_email', 'risk_rating', 'risk_level'],
  '粉丝画像': ['fan_gender', 'fan_age', 'fan_region', 'fan_city_tier', 'fan_crowd', 'fan_price_range', 'fan_category', 'account_fans_profile'],
  '群组粉丝': ['fan_group_gender', 'fan_group_age', 'fan_group_crowd', 'fan_group_activity', 'fan_group_device', 'fan_group_price', 'fan_group_category', 'video_fans_profile'],
  '热卖分布': ['price_distribution', 'category_distribution', 'brand_distribution', 'top_brands', 'hot_brands', 'top_categories', 'top_products', 'cooperating_brands', 'brand_details', 'brand_commission', 'cooperation_brand_count', 'products', 'cooperation_days'],
  '直播数据': ['live_ratio', 'live_sessions', 'live_views', 'live_stream_sessions', 'live_stream_viewers', 'video_ratio', 'avg_live_gmv', 'live_gpm', 'video_count', 'video_plays', 'video_settlement_ratio', 'video_avg_price', 'video_completion_rate', 'video_likes', 'video_comments', 'video_shares', 'video_interaction_rate', 'video_audience_region', 'video_audience_city_tier', 'live_audience_region', 'live_audience_city_tier', 'data_period']
};

// dict/list 格式化 (数字 ≤100 加 %, list bullet)
function _formatOcrValue(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') {
    if (!isNaN(v) && v <= 100) return v + '%';
    return String(v);
  }
  return String(v);
}
function _renderOcrNestedValue(v) {
  if (v === null || v === undefined) return '';
  if (typeof v === 'object' && !Array.isArray(v)) {
    var parts = [];
    Object.keys(v).forEach(function(k){
      var val = v[k];
      var display;
      if (val !== null && typeof val === 'object') {
        display = _renderOcrNestedValue(val);
        parts.push('<div style="padding:4px 0;"><div style="color:var(--text-secondary);font-size:11px;margin-bottom:4px;">' + escapeHtml(OCR_FIELD_LABELS[k] || k) + '</div><div style="padding-left:10px;border-left:2px solid var(--bg-secondary);">' + display + '</div></div>');
      } else {
        display = _formatOcrValue(val);
        parts.push('<div style="display:flex;justify-content:space-between;padding:3px 0;font-size:12px;border-bottom:1px solid var(--bg-secondary);"><span style="color:var(--text-secondary);">' + escapeHtml(OCR_FIELD_LABELS[k] || k) + '</span><span style="color:var(--accent);font-weight:500;">' + escapeHtml(display) + '</span></div>');
      }
    });
    return parts.join('');
  }
  if (Array.isArray(v)) {
    return v.map(function(item){
      if (item !== null && typeof item === 'object') {
        return '<div style="padding:4px 0;font-size:12px;">• ' + escapeHtml(_formatOcrValue(item)) + '</div>';
      }
      return '<div style="padding:3px 0;font-size:12px;color:var(--text-primary);">• ' + escapeHtml(_formatOcrValue(item)) + '</div>';
    }).join('');
  }
  return '<span>' + escapeHtml(_formatOcrValue(v)) + '</span>';
}

// OCR 字段按 6 维度分组渲染 (commit 7)
function _renderOcrSemanticGroups(ocrRaw) {
  if (!ocrRaw || typeof ocrRaw !== 'object') return '';
  var keys = Object.keys(ocrRaw);
  if (keys.length === 0) return '';

  var html = '<div class="talents-panel-section">';
  html += '<div class="talents-panel-title">📋 OCR 完整字段 <span style="font-weight:400;color:var(--text-secondary);font-size:12px;margin-left:6px;">(' + keys.length + ' 字段 / 6 维度分组)</span></div>';

  // 用 6 维度顺序遍历,未分类的归到 "其他字段"
  var _grouped = {};
  Object.keys(OCR_FIELD_GROUPS).forEach(function(g){ _grouped[g] = []; });
  _grouped['其他字段'] = [];
  keys.forEach(function(k){
    var _found = false;
    Object.keys(OCR_FIELD_GROUPS).forEach(function(g){
      if (OCR_FIELD_GROUPS[g].indexOf(k) >= 0) { _grouped[g].push(k); _found = true; }
    });
    if (!_found) _grouped['其他字段'].push(k);
  });

  Object.keys(_grouped).forEach(function(g){
    var items = _grouped[g];
    if (items.length === 0) return;
    html += '<details style="background:var(--bg-tertiary);border-radius:var(--radius-md);padding:10px 14px;margin-bottom:8px;">';
    html += '<summary style="cursor:pointer;font-size:13px;color:var(--text-primary);font-weight:600;list-style:none;">';
    html += '<span style="display:inline-block;width:14px;transition:transform 0.2s;">▶</span> ';
    html += escapeHtml(g) + ' <span style="color:var(--text-secondary);font-weight:400;">(' + items.length + ' 字段)</span>';
    html += '</summary>';
    html += '<div style="margin:10px 0 0 8px;line-height:1.6;">';
    items.forEach(function(k){
      var v = ocrRaw[k];
      if (v === null || v === undefined || v === '' || v === '未提供') return;
      html += '<div style="display:flex;justify-content:space-between;padding:5px 0;font-size:12px;border-bottom:1px solid var(--bg-secondary);"><span style="color:var(--text-secondary);">' + escapeHtml(OCR_FIELD_LABELS[k] || k) + '</span><span style="color:var(--accent);font-weight:500;">';
      if (typeof v === 'object') {
        html += _renderOcrNestedValue(v);
      } else {
        html += escapeHtml(_formatOcrValue(v));
      }
      html += '</span></div>';
    });
    html += '</div></details>';
  });

  html += '</div>';
  return html;
}

function renderTalentPanelKnowledge(t) {
  var el = document.getElementById('talentsPanelKnowledge');
  if (!el || !t || !t.id) return;
  el.innerHTML = '<div class="talents-panel-section"><div class="talents-panel-title">分析档案</div><div class="products-loading">加载中...</div></div>';
  // ★ commit 7: OCR 完整字段折叠区在 events 之前渲染, 加载 events 后拼接
  var _ocrHtml = _renderOcrSemanticGroups(t.ocr_raw_fields);
  apiFetch('/api/knowledge-events?entity_type=talent&entity_id=' + encodeURIComponent(t.id))
    .then(function(r){ return r.json(); })
    .then(function(data){
      var events = data.events || [];
      if (events.length === 0) {
        el.innerHTML = _ocrHtml
          + '<div class="talents-panel-section"><div class="talents-panel-title">分析档案</div>'
          + '<div class="talent-empty-guide" style="text-align:center;padding:40px 20px;background:#F9FAFB;border-radius:12px;margin:20px;">'
          + '<div style="font-size:48px;margin-bottom:16px;">📊</div>'
          + '<div style="font-size:16px;font-weight:600;color:#111827;margin-bottom:8px;">该达人尚未生成分析报告</div>'
          + '<div style="font-size:14px;color:#6B7280;margin-bottom:20px;">上传达人数据截图，AI 自动分析粉丝画像、带货结构、合作建议</div>'
          + '<button onclick="triggerTalentAnalysis()" style="background:#1677ff;color:white;border:none;padding:10px 24px;border-radius:8px;font-size:14px;cursor:pointer;">上传截图分析</button>'
          + '</div></div>';
        return;
      }
      var html = '<div class="talents-panel-section"><div class="talents-panel-title">分析档案 (' + events.length + ')</div>';
      html += '<div class="talents-timeline">';
      events.forEach(function(ev){
        var time = ev.created_at ? new Date(ev.created_at).toLocaleString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
        html += '<div class="talents-timeline-item">';
        html += '<div class="talents-timeline-dot completed"></div>';
        html += '<div class="talents-timeline-card" style="cursor:pointer;" onclick="toggleKnowledgeEventCard(this, \'' + escapeAttr(ev.id) + '\')">';
        html += '<div class="talents-timeline-meta">';
        html += '<div class="talents-timeline-time">' + escapeHtml(time) + '</div>';
        html += '<div class="talents-timeline-by">' + escapeHtml(ev.agent_id || '') + '</div>';
        html += '</div>';
        html += '<div class="talents-timeline-content" style="font-weight:600;">' + escapeHtml(ev.title || '分析结论') + '</div>';
        if (ev.user_query) html += '<div class="talents-timeline-result">提问：' + escapeHtml(ev.user_query.length > 60 ? ev.user_query.slice(0, 60) + '…' : ev.user_query) + '</div>';
        html += '<div class="ke-full-content" style="display:none;white-space:pre-wrap;margin-top:8px;font-size:12px;color:var(--text-secondary);"></div>';
        html += '</div></div>';
      });
      html += '</div></div>';
      // ★ commit 7: OCR 完整字段分组折叠区在 events 时间线后追加
      el.innerHTML = _ocrHtml + html;
    })
    .catch(function(e){
      el.innerHTML = _ocrHtml
        + '<div class="talents-panel-section"><div class="talents-panel-title">分析档案</div><div class="products-empty"><div class="products-empty-text">加载失败</div></div></div>';
    });
}

function toggleKnowledgeEventCard(cardEl, eventId) {
  var body = cardEl.querySelector('.ke-full-content');
  if (!body) return;
  if (body.style.display !== 'none') {
    body.style.display = 'none';
    return;
  }
  if (body.dataset.loaded === '1') {
    body.style.display = '';
    return;
  }
  body.textContent = '加载中...';
  body.style.display = '';
  apiFetch('/api/knowledge-events/' + encodeURIComponent(eventId))
    .then(function(r){ return r.json(); })
    .then(function(data){
      body.textContent = data.content_full || '（无内容）';
      body.dataset.loaded = '1';
    })
    .catch(function(e){
      body.textContent = '加载失败';
    });
}

// ═══ 达人分析引导：上传截图触发 Helen 分析流程 ═══
function _findHelenEmp() {
  var list = (typeof emps !== 'undefined' && emps) || [];
  var hit = list.find(function(e){
    var label = ((e.name || '') + ' ' + (getEmpRoleDisplay(e) || '')).toLowerCase();
    return label.indexOf('helen') !== -1;
  });
  if (hit) return hit;
  return list.find(function(e){
    var label = ((e.name || '') + ' ' + (getEmpRoleDisplay(e) || '')).toLowerCase();
    return /商务|bd|合作/.test(label);
  }) || null;
}

function triggerTalentAnalysis() {
  if (!_currentTalent || !_currentTalent.id) { showToast('请先选择达人'); return; }
  var input = document.getElementById('talentAnalysisFileInput');
  if (!input) {
    input = document.createElement('input');
    input.type = 'file';
    input.id = 'talentAnalysisFileInput';
    input.accept = 'image/*';
    input.multiple = true;
    input.style.display = 'none';
    document.body.appendChild(input);
    input.addEventListener('change', function(){
      var files = Array.prototype.slice.call(input.files || []);
      input.value = '';
      if (files.length) _uploadTalentAnalysisImages(files);
    });
  }
  input.click();
}

function _uploadTalentAnalysisImages(files) {
  var t = _currentTalent;
  var helen = _findHelenEmp();
  if (!helen) { showToast('❌ 未找到 Helen（商务）员工，请先在员工列表中配置'); return; }
  showToast('📤 正在上传 ' + files.length + ' 张截图...');
  Promise.all(files.map(function(f){ return compressImageToBase64(f); }))
    .then(function(images){
      var text = '请分析达人「' + (t.name || '') + '」的数据截图，生成达人分析报告（粉丝画像、带货结构、合作建议）。';
      return apiFetch('/api/chat/' + encodeURIComponent(helen.id), {
        method: 'POST',
        body: JSON.stringify({ role: 'user', content: text, skipAI: true, empId: helen.id, images: images })
      });
    })
    .then(function(r){ return r.json(); })
    .then(function(data){
      if (data && data.heavyPipe) {
        showToast('🔬 Helen 正在深度分析截图，完成后可在分析档案查看');
      } else {
        showToast('✅ 已提交给 Helen 分析');
      }
      // HeavyPipe 分析耗时较长，延迟刷新分析档案面板
      setTimeout(function(){ if (_currentTalent) renderTalentPanelKnowledge(_currentTalent); }, 30000);
    })
    .catch(function(e){
      console.error('[TalentAnalysis] 上传失败:', e);
      showToast('❌ 上传失败');
    });
}

var _currentTalentFollowUps = [];

// ═══ 规律库（knowledge_patterns，L3）═══
var _patternsStatus = '';
var _patternsCategory = '';
var _patternsCache = [];

function switchPatternsTab(status) {
  _patternsStatus = status;
  document.querySelectorAll('.patterns-tab').forEach(function(t){ t.classList.toggle('active', t.dataset.status === status); });
  loadPatternsPage();
}

function selectPatternCategory(cat) {
  _patternsCategory = cat;
  document.querySelectorAll('.patterns-cat-item').forEach(function(el){ el.classList.toggle('active', el.dataset.cat === cat); });
  renderPatternsList();
}

function loadPatternsPage() {
  var listEl = document.getElementById('patternsList');
  if (listEl) listEl.innerHTML = renderSkeleton('card', 4);
  var url = '/api/knowledge-patterns?limit=100';
  if (_patternsStatus) url += '&status=' + encodeURIComponent(_patternsStatus);
  apiFetch(url).then(function(r){ return r.json(); }).then(function(data){
    _patternsCache = data.patterns || [];
    renderPatternsCats();
    renderPatternsList();
  }).catch(function(e){
    if (listEl) listEl.innerHTML = renderEmptyState({
      icon: '⚠️',
      title: '加载失败',
      desc: '网络异常,请稍后重试',
      tone: 'danger',
      cta: { label: '重试', onclick: 'loadPatternsPage()' }
    });
  });
}

function renderPatternsCats() {
  var el = document.getElementById('patternsCats');
  if (!el) return;
  var cats = [];
  _patternsCache.forEach(function(p){ if (p.category && cats.indexOf(p.category) < 0) cats.push(p.category); });
  var html = '<div class="patterns-cat-item' + (_patternsCategory === '' ? ' active' : '') + '" data-cat="" onclick="selectPatternCategory(\'\')">全部类目</div>';
  cats.forEach(function(c){
    html += '<div class="patterns-cat-item' + (_patternsCategory === c ? ' active' : '') + '" data-cat="' + escapeAttr(c) + '" onclick="selectPatternCategory(\'' + escapeAttr(c).replace(/'/g, "\\'") + '\')">' + escapeHtml(c) + '</div>';
  });
  el.innerHTML = html;
}

function renderPatternsList() {
  var el = document.getElementById('patternsList');
  if (!el) return;
  var list = _patternsCache.filter(function(p){ return !_patternsCategory || p.category === _patternsCategory; });
  if (list.length === 0) {
    var isFiltering = _patternsCategory || _patternsStatus;
    el.innerHTML = renderEmptyState({
      icon: '🧠',
      title: isFiltering ? '当前过滤下无规律' : '暂无规律记录',
      desc: isFiltering ? '试试切换其他类目或状态' : '类目积累足够历史事件后可触发归纳',
      cta: isFiltering ? undefined : { label: '⚡ 触发归纳', onclick: 'triggerPatternInduce()' }
    });
    return;
  }
  var statusLabel = { draft: '待确认', confirmed: '已确认', rejected: '已拒绝', deprecated: '已废弃' };
  var levelLabel = { hypothesis: '假设', candidate: '候选', verified: '已验证', proven: '成熟', deprecated: '已废弃' };
  var html = '';
  list.forEach(function(p){
    var conf = Math.round(p.confidence_score != null ? p.confidence_score : (p.confidence || 0) * 100);
    var level = p.verification_level || 'hypothesis';
    if (level === 'hypothesis' && p.status === 'confirmed') level = 'verified'; // 与后端注入兼容规则一致
    var time = p.created_at ? new Date(p.created_at * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '-';
    var pid = escapeAttr(p.id);
    html += '<div class="pattern-card" onclick="togglePatternEvidence(\'' + pid + '\')">';
    html += '<div class="pattern-card-top">';
    html += '<span class="pattern-cat-tag">' + escapeHtml(p.category || '未分类') + '</span>';
    html += '<span class="pattern-level-badge ' + escapeAttr(level) + '">' + (levelLabel[level] || level) + '</span>';
    html += '<span class="pattern-status-badge ' + escapeAttr(p.status || 'draft') + '">' + (statusLabel[p.status] || p.status || '待确认') + '</span>';
    html += '</div>';
    html += '<div class="pattern-card-text">' + escapeHtml(p.pattern_text || '') + '</div>';
    html += '<div class="pattern-card-meta">';
    html += '<span>置信度</span><div class="pattern-conf-bar"><div class="pattern-conf-fill" style="width:' + conf + '%"></div></div><span>' + conf + '%</span>';
    html += '<span>支撑案例 ' + (p.evidence_count || 0) + '</span>';
    // dev/feat: #9 patterns ↔ knowledge 主表互引 — 显示关联知识文档数
    var kbCount = (p.source_knowledge_ids || []).length;
    if (kbCount > 0) {
      html += '<span title="' + escapeAttr((p.source_knowledge_ids || []).join(',')) + '">📚 关联知识 ' + kbCount + '</span>';
    }
    html += '<span style="margin-left:auto;">' + escapeHtml(time) + '</span>';
    if (p.status === 'draft') {
      html += '<span class="pattern-card-actions">';
      html += '<button class="pattern-action-btn" onclick="event.stopPropagation(); setPatternStatus(\'' + pid + '\', \'confirmed\')">✓ 确认</button>';
      html += '<button class="pattern-action-btn" onclick="event.stopPropagation(); setPatternStatus(\'' + pid + '\', \'rejected\')">✗ 拒绝</button>';
      html += '</span>';
    } else if (p.status === 'confirmed') {
      // dev/feat: #2 规律反馈按钮 (只对已确认的, 收集 hit/miss 数据)
      var hits = p.hit_count || 0;
      var misses = p.miss_count || 0;
      html += '<span class="pattern-card-actions">';
      html += '<button class="pattern-fb-btn up" title="这条规律帮到你了" onclick="event.stopPropagation(); feedbackPattern(\'' + pid + '\', \'up\')">👍 ' + hits + '</button>';
      html += '<button class="pattern-fb-btn down" title="这条规律误导了" onclick="event.stopPropagation(); feedbackPattern(\'' + pid + '\', \'down\')">👎 ' + misses + '</button>';
      html += '</span>';
    }
    html += '</div>';
    html += '<div class="pattern-evidence" id="patternEvidence_' + pid + '" style="display:none;"></div>';
    html += '</div>';
  });
  el.innerHTML = html;
}

function togglePatternEvidence(pid) {
  var box = document.getElementById('patternEvidence_' + pid);
  if (!box) return;
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  if (box.dataset.loaded === '1') { box.style.display = ''; return; }
  box.innerHTML = '<div style="padding:8px 0;">' + renderSkeleton('text', 3) + '</div>';
  box.style.display = '';
  apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid)).then(function(r){ return r.json(); }).then(function(data){
    var ev = data.evidence || [];
    if (ev.length === 0) { box.innerHTML = '<div style="padding:8px 0;color:var(--color-text-tertiary, #AEAEB2);">无支撑事件</div>'; box.dataset.loaded = '1'; return; }
    // evidence 是事件ID列表，逐个取事件标题和时间
    Promise.all(ev.map(function(eid){
      return apiFetch('/api/knowledge-events/' + encodeURIComponent(eid)).then(function(r){ return r.ok ? r.json() : null; }).catch(function(){ return null; });
    })).then(function(events){
      var valid = events.filter(Boolean);
      var html = '<div style="margin-bottom:4px;">支撑事件 (' + valid.length + ')</div>';
      valid.forEach(function(ev2){
        var t = ev2.created_at ? new Date(ev2.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit' }) : '-';
        html += '<div>· ' + escapeHtml(ev2.title || ev2.id) + '（' + t + '）</div>';
      });
      box.innerHTML = html;
      box.dataset.loaded = '1';
    });
  }).catch(function(e){ box.innerHTML = '<div style="padding:8px 0;color:var(--color-status-lost, #FF3B30);">加载失败</div>'; });
}

function setPatternStatus(pid, status) {
  apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status: status })
  }).then(function(r){ return r.json(); }).then(function(data){
    if (data && data.id) { showToast(status === 'confirmed' ? '已确认' : '已拒绝', 'success'); loadPatternsPage(); }
    else { showToast('操作失败: ' + ((data && data.error) || ''), 'error'); }
  }).catch(function(e){ showToast('操作失败', 'error'); });
}

// dev/feat: #2 规律反馈 — 让规律有生命周期数据 (hit/miss 计数)
// 之前 16/16 条 hit/miss 全 0, 因为前端没暴露反馈入口
function feedbackPattern(pid, feedback) {
  apiFetch('/api/knowledge-patterns/' + encodeURIComponent(pid) + '/feedback', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ feedback: feedback })
  }).then(function(r) {
    if (r && r.ok) return r.json();
    return r && r.json ? r.json().then(function(j) { return { ok: false, err: j }; }) : { ok: false };
  }).then(function(data) {
    if (data && data.ok) {
      showToast(data.message || (feedback === 'up' ? '👍 反馈已记录' : '👎 反馈已记录'), 'success');
      loadPatternsPage();  // 刷新 hit/miss 计数显示
    } else {
      var msg = (data && data.error) || '反馈失败';
      showToast('❌ ' + msg, 'error');
    }
  }).catch(function(e) { showToast('反馈失败', 'error'); });
}

function triggerPatternInduce() {
  var sel = document.getElementById('patternInduceCategory');
  sel.innerHTML = '<option value="">加载中...</option>';
  document.getElementById('patternInduceModalOverlay').classList.add('active');
  apiFetch('/api/talents/categories').then(function(r){ return r.json(); }).then(function(data){
    var cats = data.categories || data.docs || data || [];
    _patternInduceCats = cats.map(function(c){
      return (typeof c === 'string') ? { id: c, name: c } : { id: c.id || c.name || '', name: c.name || '' };
    }).filter(function(c){ return c.name; });
    if (_patternInduceCats.length === 0) {
      sel.innerHTML = '<option value="">（暂无类目，请先在知识库创建分类）</option>';
      return;
    }
    var html = '<option value="">请选择类目</option>';
    _patternInduceCats.forEach(function(c){
      html += '<option value="' + escapeAttr(c.id) + '">' + escapeHtml(c.name) + '</option>';
    });
    sel.innerHTML = html;
  }).catch(function(e){
    sel.innerHTML = '<option value="">类目加载失败</option>';
  });
}

var _patternInduceCats = [];

function closePatternInduceModal(e) {
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('patternInduceModalOverlay').classList.remove('active');
}

function confirmPatternInduce() {
  var sel = document.getElementById('patternInduceCategory');
  var etSel = document.getElementById('patternInduceEntityType');
  var categoryId = sel.value;
  if (!categoryId) { showToast('请选择类目', 'warning'); return; }
  var entityType = (etSel && etSel.value) || 'talent';
  var cat = _patternInduceCats.find(function(c){ return String(c.id) === String(categoryId); }) || {};
  var categoryName = cat.name || categoryId;
  closePatternInduceModal();
  var entityLabel = {talent: '达人', product: '产品', brand: '品牌', category: '类目'}[entityType] || entityType;
  showToast('正在归纳「' + categoryName + '」类目的' + entityLabel + '规律...', 'info');
  apiFetch('/api/knowledge-patterns/induce', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ categoryId: categoryId, category: categoryName, entity_type: entityType })
  }).then(function(r){ return r.json(); }).then(function(res){
    if (res && res.ok) { showToast('归纳完成，新增 ' + res.induced + ' 条 ' + entityLabel + '规律', 'success'); loadPatternsPage(); }
    else { showToast('归纳失败: ' + ((res && res.error) || '未知错误'), 'error'); }
  }).catch(function(e){ showToast('归纳请求失败', 'error'); });
}



function createNewTalentFollowUp() {
  if (!_talentCurrentId) return;
  document.getElementById('talentFollowUpEditId').value = '';
  document.getElementById('talentFollowUpAt').value = new Date().toISOString().slice(0, 16);
  document.getElementById('talentFollowUpBy2').value = (currentUser && currentUser.name) || '';
  document.getElementById('talentFollowUpContent').value = '';
  document.getElementById('talentFollowUpResult').value = '';
  document.getElementById('talentFollowUpNextAt').value = '';
  document.getElementById('talentFollowUpStatus').value = 'completed';
  document.getElementById('talentFollowUpModalTitle').textContent = '新增跟进';
  document.getElementById('talentFollowUpModalOverlay').classList.add('active');
}

function editTalentFollowUp(followUpId) {
  if (!_talentCurrentId) return;
  var fu = (_currentTalentFollowUps || []).find(function(x){ return x.id === followUpId; });
  if (!fu) return;
  document.getElementById('talentFollowUpEditId').value = fu.id;
  document.getElementById('talentFollowUpAt').value = fu.follow_up_at ? new Date(fu.follow_up_at).toISOString().slice(0, 16) : '';
  document.getElementById('talentFollowUpBy2').value = fu.follow_up_by || '';
  document.getElementById('talentFollowUpContent').value = fu.content || '';
  document.getElementById('talentFollowUpResult').value = fu.result || '';
  document.getElementById('talentFollowUpNextAt').value = fu.next_follow_up_at ? new Date(fu.next_follow_up_at).toISOString().slice(0, 16) : '';
  document.getElementById('talentFollowUpStatus').value = fu.status || 'completed';
  document.getElementById('talentFollowUpModalTitle').textContent = '编辑跟进';
  document.getElementById('talentFollowUpModalOverlay').classList.add('active');
}

function closeTalentFollowUpModal(e) { if (e && e.target !== e.currentTarget) return; document.getElementById('talentFollowUpModalOverlay').classList.remove('active'); }

function saveTalentFollowUp() {
  if (!_talentCurrentId) return;
  var id = document.getElementById('talentFollowUpEditId').value;
  var followUpAtVal = document.getElementById('talentFollowUpAt').value;
  var nextFollowUpAtVal = document.getElementById('talentFollowUpNextAt').value;
  if (!followUpAtVal) { showToast('请选择跟进时间'); return; }
  var body = {
    follow_up_at: new Date(followUpAtVal).getTime(),
    next_follow_up_at: nextFollowUpAtVal ? new Date(nextFollowUpAtVal).getTime() : 0,
    follow_up_by: document.getElementById('talentFollowUpBy2').value.trim(),
    content: document.getElementById('talentFollowUpContent').value.trim(),
    result: document.getElementById('talentFollowUpResult').value.trim(),
    status: document.getElementById('talentFollowUpStatus').value
  };
  var url = id ? '/api/talents/' + encodeURIComponent(_talentCurrentId) + '/follow-ups/' + encodeURIComponent(id) : '/api/talents/' + encodeURIComponent(_talentCurrentId) + '/follow-ups';
  var method = id ? 'PUT' : 'POST';
  apiFetch(url, {method: method, body: JSON.stringify(body)}).then(function(r){return r.json();}).then(function(){
    showToast(id ? '✅ 已更新' : '✅ 跟进已记录');
    closeTalentFollowUpModal();
    if (_currentTalent) renderTalentPanelFollowUps(_currentTalent);
    if (_talentCurrentId) loadTalents();
  }).catch(function(){ showToast('❌ 保存失败'); });
}

function deleteTalentFollowUp(followUpId) {
  if (!_talentCurrentId) return;
  if (!confirm('确定删除此跟进记录？')) return;
  apiFetch('/api/talents/' + encodeURIComponent(_talentCurrentId) + '/follow-ups/' + encodeURIComponent(followUpId), {method:'DELETE'}).then(function(){
    showToast('✅ 已删除');
    if (_currentTalent) renderTalentPanelFollowUps(_currentTalent);
  }).catch(function(){ showToast('❌ 删除失败'); });
}

function analyzeTalentAI() {
  if (!_talentCurrentId) return;
  var btn = document.getElementById('talentsAIBtn');
  if (btn) { btn.disabled = true; btn.textContent = '分析中...'; }
  apiFetch('/api/talents/' + encodeURIComponent(_talentCurrentId) + '/analyze', {method:'POST'}).then(function(r){return r.json();}).then(function(data){
    var t = _talentData.talents.find(function(x){return x.id === _talentCurrentId;});
    if (t) {
      t.ai_rating = data.ai_analysis.rating || '';
      t.ai_tags = data.ai_analysis.tags || [];
      t.ai_summary = data.ai_analysis.suitable_products || '';
      t.ai_analysis = data.ai_analysis.content || (typeof data.ai_analysis === 'string' ? data.ai_analysis : JSON.stringify(data.ai_analysis));
      t.ai_reason = JSON.stringify(data.ai_analysis);
    }
    if (_currentTalent) _currentTalent = Object.assign(_currentTalent, t);
    renderTalentDetail(t || _currentTalent);
    showToast('✅ AI 分析完成');
  }).catch(function(e){ showToast('❌ 分析失败'); }).finally(function(){
    if (btn) { btn.disabled = false; btn.textContent = 'AI 分析'; }
  });
}

function createNewTalent() {
  document.getElementById('talentEditId').value = '';
  document.getElementById('talentName').value = '';
  document.getElementById('talentDouyinId').value = '';
  document.getElementById('talentRealName').value = '';
  document.getElementById('talentFollowers').value = '0';
  document.getElementById('talentCategory').value = '';
  document.getElementById('talentLevel').value = '';
  document.getElementById('talentRiskRating').value = '';
  document.getElementById('talentCity').value = '';
  document.getElementById('talentBio').value = '';
  document.getElementById('talentContentStyle').value = '';
  // ★ feature/influencer-form-validation: 4 必填字段新增
  document.getElementById('talentType').value = '';
  document.getElementById('accountFansProfile').value = '';
  document.getElementById('videoFansProfile').value = '';
  document.getElementById('talentAIAnalysis').value = '';
  document.getElementById('talentContactName').value = '';
  document.getElementById('talentContactPhone').value = '';
  document.getElementById('talentContactWechat').value = '';
  document.getElementById('talentContactEmail').value = '';
  document.getElementById('talentCooperationStatus').value = 'available';
  document.getElementById('talentFollowUpBy').value = '';
  document.getElementById('talentNextFollowUpAt').value = '';
  document.getElementById('talentFollowUpNote').value = '';
  document.getElementById('talentModalTitle').textContent = '录入达人';
  document.getElementById('talentModalOverlay').classList.add('active');
}

function editCurrentTalent() {
  if (!_talentCurrentId) { showToast('请选择达人'); return; }
  apiFetch('/api/talents/' + encodeURIComponent(_talentCurrentId)).then(function(r){return r.json();}).then(function(t){
    document.getElementById('talentEditId').value = t.id;
    document.getElementById('talentName').value = t.name || '';
    document.getElementById('talentDouyinId').value = t.douyin_id || '';
    document.getElementById('talentRealName').value = t.real_name || '';
    document.getElementById('talentFollowers').value = t.followers || 0;
    document.getElementById('talentCategory').value = (typeof t.category === 'string' ? t.category : '') || (typeof t.fan_category === 'string' ? t.fan_category : '');
    document.getElementById('talentLevel').value = t.level || '';
    document.getElementById('talentRiskRating').value = t.risk_rating || '';
    document.getElementById('talentCity').value = t.city || '';
    document.getElementById('talentBio').value = t.bio || '';
    document.getElementById('talentContentStyle').value = t.content_style || t.contentStyle || '';
    // ★ feature/influencer-form-validation: 4 必填字段回填 (snake_case → camelCase id)
    document.getElementById('talentType').value = t.talent_type || t.talentType || '';
    document.getElementById('accountFansProfile').value = t.account_fans_profile || t.accountFansProfile || '';
    document.getElementById('videoFansProfile').value = t.video_fans_profile || t.videoFansProfile || '';
    document.getElementById('talentAIAnalysis').value = t.ai_analysis || t.ai_summary || '';
    document.getElementById('talentContactName').value = t.contact_name || '';
    document.getElementById('talentContactPhone').value = t.phone || t.contact_phone || '';
    document.getElementById('talentContactWechat').value = t.wechat || t.contact_wechat || '';
    document.getElementById('talentContactEmail').value = t.email || t.contact_email || '';
    document.getElementById('talentCooperationStatus').value = t.cooperation_status || 'available';
    document.getElementById('talentFollowUpBy').value = t.follow_up_by || '';
    document.getElementById('talentNextFollowUpAt').value = t.next_follow_up_at ? new Date(t.next_follow_up_at).toISOString().slice(0,16) : '';
    document.getElementById('talentFollowUpNote').value = t.follow_up_note || '';
    document.getElementById('talentModalTitle').textContent = '编辑达人';
    document.getElementById('talentModalOverlay').classList.add('active');
  });
}

function closeTalentModal(e) { if (e && e.target !== e.currentTarget) return; document.getElementById('talentModalOverlay').classList.remove('active'); }

// ★ feature/influencer-form-validation: 4 必填字段校验 (内容类型 / 内容风格 / 账号粉丝特征 / 短视频粉丝特征)
// 备注保持选填, 不进校验
function validateTalentForm() {
  var required = [
    {id: 'talentType',           label: '内容类型'},
    {id: 'talentContentStyle',   label: '内容风格'},
    {id: 'accountFansProfile',   label: '账号粉丝特征'},
    {id: 'videoFansProfile',     label: '短视频粉丝特征'},
  ];
  var missing = [];
  required.forEach(function(item) {
    var field = document.getElementById(item.id);
    if (field && !field.value.trim()) {
      missing.push(item.label);
      field.classList.add('field-missing');
    } else if (field) {
      field.classList.remove('field-missing');
    }
  });
  if (missing.length) {
    showToast('请补充必填字段: ' + missing.join('、'), 'error');
    return false;
  }
  return true;
}

function saveTalent() {
  var id = document.getElementById('talentEditId').value;
  var name = document.getElementById('talentName').value.trim();
  var douyinId = document.getElementById('talentDouyinId').value.trim();
  if (!name) { showToast('请输入达人昵称'); return; }
  if (!douyinId) { showToast('请输入抖音号'); return; }
  // ★ feature/influencer-form-validation: 4 必填字段校验 (内容类型 / 内容风格 / 账号粉丝特征 / 短视频粉丝特征)
  if (!validateTalentForm()) return;

  // ★ bug/talent-deduplicate: 新建场景 (id 为空) 前置查重, 命中已有 → confirm 让用户选
  //   确认 → 调 updateExistingTalent 走更新 (只覆盖 AI 字段, 保留已有基础字段)
  //   取消 → 继续原新建流程
  if (!id) {
    return _preCheckTalentAndSave(name);
  }

  var nextFollowUpVal = document.getElementById('talentNextFollowUpAt').value;
  var nextFollowUpAt = nextFollowUpVal ? new Date(nextFollowUpVal).getTime() : 0;
  var body = {
    name: name,
    douyin_id: douyinId,
    real_name: document.getElementById('talentRealName').value.trim(),
    followers: parseInt(document.getElementById('talentFollowers').value || '0') || 0,
    category: document.getElementById('talentCategory').value.trim(),
    fan_category: document.getElementById('talentCategory').value.trim(),
    level: document.getElementById('talentLevel').value.trim(),
    risk_rating: document.getElementById('talentRiskRating').value.trim(),
    city: document.getElementById('talentCity').value.trim(),
    bio: document.getElementById('talentBio').value.trim(),
    content_style: document.getElementById('talentContentStyle').value.trim(),
    // ★ feature/influencer-form-validation: 4 必填字段新增 (camelCase id → snake_case body field)
    talent_type: document.getElementById('talentType').value.trim(),
    account_fans_profile: document.getElementById('accountFansProfile').value.trim(),
    video_fans_profile: document.getElementById('videoFansProfile').value.trim(),
    ai_analysis: document.getElementById('talentAIAnalysis').value.trim(),
    contact_name: document.getElementById('talentContactName').value.trim(),
    contact_phone: document.getElementById('talentContactPhone').value.trim(),
    phone: document.getElementById('talentContactPhone').value.trim(),
    contact_wechat: document.getElementById('talentContactWechat').value.trim(),
    wechat: document.getElementById('talentContactWechat').value.trim(),
    contact_email: document.getElementById('talentContactEmail').value.trim(),
    email: document.getElementById('talentContactEmail').value.trim(),
    cooperation_status: document.getElementById('talentCooperationStatus').value,
    follow_up_by: document.getElementById('talentFollowUpBy').value.trim(),
    next_follow_up_at: nextFollowUpAt,
    follow_up_note: document.getElementById('talentFollowUpNote').value.trim()
  };
  var url = id ? '/api/talents/' + encodeURIComponent(id) : '/api/talents';
  var method = id ? 'PUT' : 'POST';
  // 防重复提交：请求完成前禁用保存按钮并显示 loading
  var btn = document.querySelector('#talentModalOverlay .product-modal-btn.save');
  if (btn && btn.disabled) return;
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  var restoreBtn = function () { if (btn) { btn.disabled = false; btn.textContent = '保存'; } };
  apiFetch(url, {method: method, body: JSON.stringify(body)}).then(function(){
    showToast(id ? '✅ 已更新' : '✅ 达人已录入');
    closeTalentModal();
    loadTalents();
    if (id && _talentCurrentId === id) selectTalentItem(id);
  }).catch(function(){ showToast('❌ 保存失败'); }).finally(restoreBtn);
}

// ★ bug/talent-deduplicate: 前置查重 + confirm 路由
// 新建场景 (saveTalent id='') 先 GET /api/talents/search, 命中已有 → confirm
// 确认 → updateExistingTalent(走更新, 只覆盖 AI 字段); 取消 → 原 POST 新建流程
function _preCheckTalentAndSave(name) {
  // fetch 用原生 (apiFetch 是 wrapper, 但查重是辅助操作, 用原生更直接)
  fetch('/api/talents/search?name=' + encodeURIComponent(name), {headers: {'Accept': 'application/json'}})
    .then(function(r){ return r.ok ? r.json() : {talents: []}; })
    .catch(function(){ return {talents: []}; })
    .then(function(data){
      var hits = (data.talents || []).filter(function(t){ return !t._archived; });
      if (hits.length === 0) {
        // 没命中 → 走原新建流程
        return _doCreateTalentPost();
      }
      var match = hits[0];
      var confirmed = window.confirm(
        '达人「' + match.name + '」已存在 (ID: ' + match.id + ', ' + (match.followers||0) + ' 粉丝)\n\n' +
        '点击"确定" = 更新档案 (保留已有基础字段, 只覆盖 AI 分析 / 跟进信息)\n' +
        '点击"取消" = 继续新建新达人记录 (会创建一条新的)'
      );
      if (confirmed) {
        return updateExistingTalent(match.id);
      }
      // 取消 → 继续原新建
      return _doCreateTalentPost();
    });
}

// ★ fix/talent-full-sync: 走更新 (传 modal 实际有 input 的全部字段)
// 之前 bug/talent-deduplicate (1017212) 只传 6 字段漏核心数据, Helen 分析结果的核心数据丢失
// 现在传 modal 真实存在的 14 个字段 (基础信息 + AI + 跟进), 后端 _merge_talent_only_empty 只补空值严禁覆盖
// modal 里没 input 的字段 (total_gmv / video_gpm / product_count 等核心数据) 走 Helen OCR 自动回写 (_update_talent_from_ocr_fields)
function updateExistingTalent(talentId) {
  var nextFollowUpVal = document.getElementById('talentNextFollowUpAt').value;
  var nextFollowUpAt = nextFollowUpVal ? new Date(nextFollowUpVal).getTime() : 0;
  var followersVal = parseInt(document.getElementById('talentFollowers').value, 10);
  // ───── modal 实际有 input 的字段 (14 个) ─────
  var body = {
    // 基础信息 (10 项)
    douyin_id: document.getElementById('talentDouyinId').value.trim(),
    followers: isNaN(followersVal) ? 0 : followersVal,
    talent_type: document.getElementById('talentType').value.trim(),
    content_style: document.getElementById('talentContentStyle').value.trim(),
    account_fans_profile: document.getElementById('accountFansProfile').value.trim(),
    video_fans_profile: document.getElementById('videoFansProfile').value.trim(),
    bio: document.getElementById('talentBio').value.trim(),
    level: document.getElementById('talentLevel').value.trim(),
    city: document.getElementById('talentCity').value.trim(),
    category: document.getElementById('talentCategory').value.trim(),
    // AI 字段 (1 项)
    ai_analysis: document.getElementById('talentAIAnalysis').value.trim(),
    // 跟进字段 (4 项)
    follow_up_by: document.getElementById('talentFollowUpBy').value.trim(),
    next_follow_up_at: nextFollowUpAt,
    follow_up_note: document.getElementById('talentFollowUpNote').value.trim(),
    cooperation_status: document.getElementById('talentCooperationStatus').value,
  };
  var btn = document.querySelector('#talentModalOverlay .product-modal-btn.save');
  if (btn && btn.disabled) return Promise.resolve();
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  var restoreBtn = function () { if (btn) { btn.disabled = false; btn.textContent = '保存'; } };
  return apiFetch('/api/talents/' + encodeURIComponent(talentId), {
    method: 'PUT',
    body: JSON.stringify(body)
  }).then(function(){
    showToast('✅ 达人「' + document.getElementById('talentName').value.trim() + '」档案已更新');
    closeTalentModal();
    loadTalents();
    if (_talentCurrentId === talentId) selectTalentItem(talentId);
  }).catch(function(){
    showToast('❌ 更新失败');
  }).then(restoreBtn, restoreBtn);
}

// ★ bug/talent-deduplicate: 原 saveTalent 里"新建"分支 (POST /api/talents) 抽出来
// 让 _preCheckTalentAndSave 在查重 miss / 用户取消 confirm 后复用
function _doCreateTalentPost() {
  var id = document.getElementById('talentEditId').value;
  var name = document.getElementById('talentName').value.trim();
  var douyinId = document.getElementById('talentDouyinId').value.trim();
  var nextFollowUpVal = document.getElementById('talentNextFollowUpAt').value;
  var nextFollowUpAt = nextFollowUpVal ? new Date(nextFollowUpVal).getTime() : 0;
  var body = {
    name: name,
    douyin_id: douyinId,
    real_name: document.getElementById('talentRealName').value.trim(),
    followers: parseInt(document.getElementById('talentFollowers').value || '0') || 0,
    category: document.getElementById('talentCategory').value.trim(),
    fan_category: document.getElementById('talentCategory').value.trim(),
    level: document.getElementById('talentLevel').value.trim(),
    risk_rating: document.getElementById('talentRiskRating').value.trim(),
    city: document.getElementById('talentCity').value.trim(),
    bio: document.getElementById('talentBio').value.trim(),
    content_style: document.getElementById('talentContentStyle').value.trim(),
    ai_analysis: document.getElementById('talentAIAnalysis').value.trim(),
    contact_name: document.getElementById('talentContactName').value.trim(),
    contact_phone: document.getElementById('talentContactPhone').value.trim(),
    phone: document.getElementById('talentContactPhone').value.trim(),
    contact_wechat: document.getElementById('talentContactWechat').value.trim(),
    wechat: document.getElementById('talentContactWechat').value.trim(),
    contact_email: document.getElementById('talentContactEmail').value.trim(),
    email: document.getElementById('talentContactEmail').value.trim(),
    cooperation_status: document.getElementById('talentCooperationStatus').value,
    follow_up_by: document.getElementById('talentFollowUpBy').value.trim(),
    next_follow_up_at: nextFollowUpAt,
    follow_up_note: document.getElementById('talentFollowUpNote').value.trim()
  };
  var btn = document.querySelector('#talentModalOverlay .product-modal-btn.save');
  if (btn && btn.disabled) return Promise.resolve();
  if (btn) { btn.disabled = true; btn.textContent = '保存中…'; }
  var restoreBtn = function () { if (btn) { btn.disabled = false; btn.textContent = '保存'; } };
  return apiFetch('/api/talents', {method: 'POST', body: JSON.stringify(body)}).then(function(){
    showToast('✅ 达人已录入');
    closeTalentModal();
    loadTalents();
  }).catch(function(){ showToast('❌ 保存失败'); }).then(restoreBtn, restoreBtn);
}

function deleteCurrentTalent() {
  if (!_talentCurrentId) { showToast('请选择达人'); return; }
  if (!confirm('确定删除此达人？')) return;
  apiFetch('/api/talents/' + encodeURIComponent(_talentCurrentId), {method:'DELETE'}).then(function(){ showToast('✅ 已删除'); _talentCurrentId = null; showTalentEmptyState(); loadTalents(); }).catch(function(){ showToast('❌ 删除失败'); });
}

// 两层架构：管理员把子库达人提升到主库（created_by 置空，收归主库仅管理员可见）
function promoteCurrentTalent() {
  if (!_talentCurrentId) { showToast('请选择达人'); return; }
  if (!confirm('提升到主库后，该达人将从子账号的子库收归主库，仅管理员可见和管理。确认提升？')) return;
  apiFetch('/api/talents/' + encodeURIComponent(_talentCurrentId) + '/promote', {method: 'POST'}).then(function(r){
    if (!r || !r.ok) throw new Error('promote failed');
    showToast('✅ 已提升到主库');
    loadTalents();
    selectTalentItem(_talentCurrentId);
  }).catch(function(){ showToast('❌ 提升失败（仅管理员可操作）'); });
}

var _currentTalent = null;
var _currentTalentMatchProducts = { talent: null, matches: [], sortBy: 'score' };
var _currentProductMatchTalents = { product: null, matches: [], sortBy: 'score' };

// ========== Knowledge Page ==========
var _knowledgeData = { docs: [], categories: [], categoryMap: {}, total: 0 };
var _knowledgeCurrentCat = '';
var _knowledgeCurrentCategoryId = null;
var _knowledgeCurrentId = null;
var _knowledgeScope = 'all';
var _knowledgeProjectId = '';
var _knowledgePreviewMode = false;
var _knowledgeViewMode = (function(){
  try {
    var saved = localStorage.getItem('sb2_knowledgeViewMode');
    if (saved === 'sb2' || saved === 'list' || saved === 'card') return saved;
  } catch(e){}
  // ★ feat/sb2-dense-views: 默认 'sb2' (高密度表格), 老大指令
  return 'sb2';
})();
var _knowledgePage = 1;
var _knowledgeLastQuery = '';  // ① 当前搜索 query(空 = 列表模式, 非空 = 搜索模式), 用于 renderKnowledgeList 区分空态
// ② 分类计数缓存: 全量拉取后按 doc.categoryId 聚合, 父分类递归累加
// 首次打开 KB 面板 / 手动刷新时拉一次, 切分类翻页不重复拉
var _knowledgeCatCounts = null;  // { [categoryId]: number } 从 0 开始, 1000 封顶
var _knowledgeCatCountsTotal = 0;  // 缓存里的总文档数(可能 = _knowledgeCatCounts 总和, 也可能 < 1000 封顶)
var _knowledgePageSize = 20;
var _knowledgeInitialized = false;
var _knowledgeExpandedCats = {};
var _knowledgeDragCat = null;
var _knowledgeCatContextTarget = null;

var _knowledgeScopeNames = { all: '全部文档', global: '全局知识库', group: '项目组知识库', personal: '个人知识库' };

function _getCurrentUserAgentIds() {
  if (!currentUser) return [];
  var uid = currentUser.userId || currentUser.id;
  return emps.filter(function(e) { return e.createdBy === uid; }).map(function(e) { return e.id; });
}

function _getCurrentUserGroups() {
  var myAgentIds = _getCurrentUserAgentIds();
  var map = {};
  groups.forEach(function(g) {
    (g.members || []).forEach(function(m) {
      var mid = (typeof m === 'object' && m !== null) ? m.id : m;
      if (myAgentIds.indexOf(mid) >= 0) {
        map[g.id] = g;
      }
    });
  });
  return Object.keys(map).map(function(id) { return map[id]; });
}

function _getGroupName(groupId) {
  var g = groups.find(function(x) { return x.id === groupId; });
  return g ? (g.name || groupId) : groupId;
}

function renderKnowledgeGroupSelector(containerId, selectedIds) {
  var container = document.getElementById(containerId);
  if (!container) return;
  var userGroups = _getCurrentUserGroups();
  if (userGroups.length === 0) {
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  selectedIds = selectedIds || [];
  var html = '<div class="knowledge-edit-groups-title">所属项目组（可多选）</div>';
  userGroups.forEach(function(g) {
    var checked = selectedIds.indexOf(g.id) >= 0 ? ' checked' : '';
    html += '<label class="knowledge-edit-group-item"><input type="checkbox" value="' + escapeAttr(g.id) + '"' + checked + '> ' + escapeHtml(g.name || g.id) + '</label>';
  });
  container.innerHTML = html;
  container.style.display = 'block';
}

function getSelectedKnowledgeGroupIds(containerId) {
  var container = document.getElementById(containerId);
  if (!container) return [];
  return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map(function(cb) { return cb.value; });
}

function openKnowledgePage() {
  switchModule('knowledge');
  loadGroups();
  // ② 首次打开 KB 面板时拉一次分类计数缓存(切分类翻页复用, 不重复拉)
  if (!_knowledgeCatCounts) {
    loadKnowledgeCategoryCounts().then(function () {
      // 拉完后强制重渲染分类树(让计数 chip 出现)
      var tree = document.getElementById('knowledgeMidCategory');
      if (tree && _knowledgeData && _knowledgeData.categories) renderKnowledgeCategories(_knowledgeData.categories);
    });
  }
}
function closeKnowledgePage() { switchModule('messages'); closeKnowledgeEditor(); _knowledgeInitialized = false; _knowledgeCurrentId = null; }
function openKnowledgeEditor() {
  var right = document.getElementById('knowledgeRight');
  var overlay = document.getElementById('knowledgeRightOverlay');
  if (right) right.classList.add('active');
  if (overlay) overlay.classList.add('open');
  var viewPanel = document.getElementById('knowledgeViewPanel');
  var editPanel = document.getElementById('knowledgeEditPanel');
  if (viewPanel) viewPanel.classList.add('hidden');
  if (editPanel) editPanel.classList.remove('hidden');
}
function closeKnowledgeEditor() {
  var editPanel = document.getElementById('knowledgeEditPanel');
  var viewPanel = document.getElementById('knowledgeViewPanel');
  if (editPanel) editPanel.classList.add('hidden');
  if (viewPanel) viewPanel.classList.remove('hidden');
  if (!_knowledgeCurrentId) {
    var right = document.getElementById('knowledgeRight');
    var overlay = document.getElementById('knowledgeRightOverlay');
    if (right) right.classList.remove('active');
    if (overlay) overlay.classList.remove('open');
  } else {
    var doc = _knowledgeData.docs.find(function(d) { return d.id === _knowledgeCurrentId; });
    if (doc) renderKnowledgeView(doc);
  }
}
function closeKnowledgeRight() {
  _knowledgeCurrentId = null;
  var right = document.getElementById('knowledgeRight');
  var overlay = document.getElementById('knowledgeRightOverlay');
  if (right) right.classList.remove('active');
  if (overlay) overlay.classList.remove('open');
  var viewPanel = document.getElementById('knowledgeViewPanel');
  var editPanel = document.getElementById('knowledgeEditPanel');
  if (viewPanel) viewPanel.classList.remove('hidden');
  if (editPanel) editPanel.classList.add('hidden');
  document.querySelectorAll('.knowledge-mid-card').forEach(function(el) { el.classList.remove('active'); });
}
function _updateKnowledgeNewButtonVisibility() {
  var btn = document.querySelector('.knowledge-mid-new');
  if (!btn) return;
  // 仅管理员或有知识库模块权限的用户可新建
  btn.style.display = hasModulePermission('knowledge') ? '' : 'none';
}
async function loadKnowledgePage(page) {
  if (page) _knowledgePage = page;
  var midItems = document.getElementById('knowledgeMidItems');
  if (midItems) midItems.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">加载中...</div>';
  try {
    await loadGroups();
    renderKnowledgeProjectTabs();
    var offset = (_knowledgePage - 1) * _knowledgePageSize;
    var url = '/api/knowledge/entries?limit=' + _knowledgePageSize + '&offset=' + offset;
    if (_knowledgeScope && _knowledgeScope !== 'all') url += '&scope=' + encodeURIComponent(_knowledgeScope);
    if (_knowledgeCurrentCategoryId) url += '&categoryId=' + encodeURIComponent(_knowledgeCurrentCategoryId);
    if (_knowledgeProjectId) url += '&projectId=' + encodeURIComponent(_knowledgeProjectId);
    var searchInput = document.getElementById('knowledgeMidSearch');
    var kw = searchInput ? searchInput.value.trim() : '';
    if (kw) url += '&q=' + encodeURIComponent(kw);
    var catUrl = '/api/knowledge/categories?projectId=' + encodeURIComponent(_knowledgeProjectId || '');
    var statsUrl = '/api/knowledge/stats?projectId=' + encodeURIComponent(_knowledgeProjectId || '');
    if (_knowledgeScope && _knowledgeScope !== 'all') {
      catUrl += '&scope=' + encodeURIComponent(_knowledgeScope);
      statsUrl += '&scope=' + encodeURIComponent(_knowledgeScope);
    }
    var [listRes, catRes, statsRes] = await Promise.all([apiFetch(url), apiFetch(catUrl), apiFetch(statsUrl)]);
    var data = await listRes.json();
    _knowledgeData.docs = data.docs || [];
    _knowledgeData.total = data.total || 0;
    var catData = await catRes.json();
    _knowledgeData.categories = catData.categories || [];
    _knowledgeData.categoryMap = {};
    function walk(cats) { cats.forEach(function(c) { _knowledgeData.categoryMap[c.id] = c; if (c.children) walk(c.children); }); }
    walk(_knowledgeData.categories);
    renderKnowledgeCategories(_knowledgeData.categories);
    renderKnowledgeList(_knowledgeData.docs);
    renderKnowledgePagination(_knowledgeData.total, _knowledgePage, _knowledgePageSize);
    var statsData = await statsRes.json();
    renderKnowledgeStats(statsData.stats || {});
    _updateKnowledgeNewButtonVisibility();
    _updateKnowledgeEditorProjectCategoryOptions();
    _knowledgeInitialized = true;
  } catch (e) {
    console.warn('[KnowledgePage] 加载失败:', e);
    if (midItems) midItems.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">加载失败</div>';
  }
}
function _getKnowledgeCategoryIcon(cat) {
  var map = {'brain':'📁','产品规范':'📋','工作流程':'⚙️'};
  return map[cat] || '📄';
}
function _getKnowledgeCategoryColorVar(cat) {
  var map = {'brain':'var(--knowledge-cat-brain)','产品规范':'var(--knowledge-cat-product)','工作流程':'var(--knowledge-cat-workflow)'};
  return map[cat] || 'var(--knowledge-cat-default)';
}

function renderKnowledgeProjectTabs() {
  var container = document.getElementById('knowledgeProjectTabs');
  if (!container) return;
  var userGroups = _getCurrentUserGroups();
  var html = '<button class="knowledge-project-tab' + (_knowledgeProjectId === '' ? ' active' : '') + '" data-project="" onclick="selectKnowledgeProject(\'\')">公共知识</button>';
  userGroups.forEach(function(g) {
    html += '<button class="knowledge-project-tab' + (_knowledgeProjectId === g.id ? ' active' : '') + '" data-project="' + escapeAttr(g.id) + '" onclick="selectKnowledgeProject(\'' + escapeAttr(g.id).replace(/'/g, "\\'") + '\')">' + escapeHtml(g.name || g.id) + '</button>';
  });
  container.innerHTML = html;
}

async function selectKnowledgeProject(projectId) {
  _knowledgeProjectId = projectId || '';
  _knowledgeCurrentCategoryId = null;
  _knowledgeCurrentCat = '';
  _knowledgePage = 1;
  var titleEl = document.getElementById('knowledgeMidTitle');
  if (titleEl) titleEl.textContent = _getKnowledgeProjectName(_knowledgeProjectId);
  await loadKnowledgePage(1);
}

function _getKnowledgeProjectName(projectId) {
  if (!projectId) return '公共知识';
  var g = groups.find(function(x) { return x.id === projectId; });
  return g ? (g.name || projectId) : projectId;
}

function renderKnowledgeCategories(categories) {
  var list = document.getElementById('knowledgeMidCategory');
  if (!list) return;
  var html = '<div class="knowledge-cat-tree-node">';
  html += _renderKnowledgeCatNode({ id: '', name: '全部分类', children: [] }, 0, true);
  (categories || []).forEach(function(c) { html += _renderKnowledgeCatNode(c, 0, false); });
  html += '</div>';
  list.innerHTML = html;
  _bindKnowledgeCatTreeEvents(list);
  var titleEl = document.getElementById('knowledgeMidTitle');
  if (titleEl) titleEl.textContent = _getKnowledgeProjectName(_knowledgeProjectId);
}

function _renderKnowledgeCatNode(cat, depth, isAll) {
  var children = cat.children || [];
  var hasChildren = children.length > 0;
  var expanded = isAll || _knowledgeExpandedCats[cat.id] || false;
  var active = isAll ? !_knowledgeCurrentCategoryId : (_knowledgeCurrentCategoryId === cat.id);
  var catIdVal = cat.id === '' ? 'null' : JSON.stringify(cat.id);
  // ② 父分类计数 = 自身 + 所有后代 categoryId 累加; 「全部分类」特殊: 显总数
  // 计数超过 1000(API 封顶)显 999+
  var count;
  if (isAll) {
    count = _knowledgeCatCountsTotal || 0;
  } else {
    count = _getCategoryCountWithChildren(cat);
  }
  var countChip = '<span class="kb-cat-count">' + (count > 1000 ? '999+' : count) + '</span>';
  var html = '<div class="knowledge-cat-tree-node" data-cat-id="' + escapeAttr(String(cat.id)) + '">';
  html += '<div class="knowledge-cat-tree-row' + (active ? ' active' : '') + '" draggable="' + (isAll ? 'false' : 'true') + '" style="padding-left:' + (8 + depth * 14) + 'px;"' + (isAll ? '' : ' oncontextmenu="showKnowledgeCatContextMenu(event, ' + catIdVal + ')"') + '>';
  html += '<span class="knowledge-cat-toggle ' + (hasChildren ? (expanded ? 'expanded' : '') : 'leaf') + '" onclick="toggleKnowledgeCategory(event, ' + catIdVal + ')">▶</span>';
  html += '<span class="knowledge-cat-icon">' + (isAll ? '📁' : '📄') + '</span>';
  html += '<span class="knowledge-cat-name" onclick="selectKnowledgeCategoryId(event, ' + catIdVal + ', ' + isAll + ')">' + escapeHtml(cat.name || '未命名') + '</span>';
  // ② 计数 chip 嵌在 row 末尾(用 inline-block 撑开, flex 自然推)
  html += countChip;
  html += '</div>';
  if (hasChildren) {
    html += '<div class="knowledge-cat-tree-children" style="display:' + (expanded ? 'block' : 'none') + '">';
    children.forEach(function(child) { html += _renderKnowledgeCatNode(child, depth + 1, false); });
    html += '</div>';
  }
  html += '</div>';
  return html;
}

function _bindKnowledgeCatTreeEvents(container) {
  var rows = container.querySelectorAll('.knowledge-cat-tree-row');
  rows.forEach(function(row) {
    row.addEventListener('dragstart', function(e) {
      var node = row.closest('.knowledge-cat-tree-node');
      _knowledgeDragCat = node ? parseInt(node.dataset.catId, 10) : null;
      if (_knowledgeDragCat) {
        e.dataTransfer.setData('text/plain', String(_knowledgeDragCat));
      }
      e.dataTransfer.effectAllowed = 'move';
      row.classList.add('dragging');
    });
    row.addEventListener('dragend', function() {
      row.classList.remove('dragging');
      _knowledgeDragCat = null;
    });
    row.addEventListener('dragover', function(e) {
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      var rect = row.getBoundingClientRect();
      var offsetY = e.clientY - rect.top;
      var isBefore = offsetY < rect.height / 2;
      row.classList.remove('drag-over-before', 'drag-over-after');
      row.classList.add(isBefore ? 'drag-over-before' : 'drag-over-after');
    });
    row.addEventListener('dragleave', function(e) {
      if (!row.contains(e.relatedTarget)) {
        row.classList.remove('drag-over-before', 'drag-over-after');
      }
    });
    row.addEventListener('drop', function(e) {
      e.preventDefault();
      var rect = row.getBoundingClientRect();
      var offsetY = e.clientY - rect.top;
      var position = offsetY < rect.height / 2 ? 'before' : 'after';
      row.classList.remove('drag-over-before', 'drag-over-after');
      var node = row.closest('.knowledge-cat-tree-node');
      var targetId = node ? parseInt(node.dataset.catId, 10) : null;
      if (_knowledgeDragCat && !isNaN(targetId) && _knowledgeDragCat !== targetId) {
        _moveKnowledgeCategory(_knowledgeDragCat, targetId, position);
      }
      _knowledgeDragCat = null;
    });
  });
}

async function _moveKnowledgeCategory(dragId, targetId, position) {
  try {
    var dragCat = _knowledgeData.categoryMap[dragId];
    var target = _knowledgeData.categoryMap[targetId];
    if (!dragCat || !target) return;
    // 仅允许同 project_id 内同层级排序，不允许跨项目/跨父级拖拽
    if (dragCat.projectId !== target.projectId || dragCat.parentId !== target.parentId) {
      showToast('❌ 仅支持同层级内排序');
      return;
    }
    var siblings = [];
    (_knowledgeData.categories || []).forEach(function walk(c) {
      if ((c.parentId || null) === (target.parentId || null) && c.projectId === target.projectId) {
        siblings.push(c);
      }
      if (c.children) c.children.forEach(walk);
    });
    siblings.sort(function(a, b) { return (a.sortOrder || 0) - (b.sortOrder || 0) || a.id - b.id; });
    var targetIndex = siblings.findIndex(function(c) { return c.id === targetId; });
    if (targetIndex < 0) return;
    var newIndex = position === 'before' ? targetIndex : targetIndex + 1;
    // 同层级拖拽时，先把自己从原位置移除，再计算目标位置
    var dragIndex = siblings.findIndex(function(c) { return c.id === dragId; });
    if (dragIndex >= 0 && dragIndex < newIndex) {
      newIndex -= 1;
    }
    await apiFetch('/api/knowledge/categories/' + encodeURIComponent(dragId), {
      method: 'PUT',
      body: JSON.stringify({ sortOrder: newIndex })
    });
    showToast('✅ 已调整排序');
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 排序调整失败');
    console.warn('[KnowledgeCat] move failed:', e);
  }
}

function toggleKnowledgeCategory(e, catId) {
  if (e) e.stopPropagation();
  if (!catId) return;
  _knowledgeExpandedCats[catId] = !_knowledgeExpandedCats[catId];
  var node = document.querySelector('.knowledge-cat-tree-node[data-cat-id="' + catId + '"]');
  if (!node) return;
  var children = node.querySelector(':scope > .knowledge-cat-tree-children');
  var toggle = node.querySelector(':scope > .knowledge-cat-tree-row > .knowledge-cat-toggle');
  if (children) children.style.display = _knowledgeExpandedCats[catId] ? 'block' : 'none';
  if (toggle) toggle.classList.toggle('expanded', _knowledgeExpandedCats[catId]);
}

async function selectKnowledgeCategoryId(e, catId, isAll) {
  if (e) e.stopPropagation();
  _knowledgeCurrentCategoryId = isAll ? null : (catId || null);
  _knowledgeCurrentCat = isAll ? '' : ((_knowledgeData.categoryMap[catId] || {}).name || '');
  _knowledgePage = 1;
  document.querySelectorAll('.knowledge-cat-tree-row').forEach(function(row) {
    var node = row.closest('.knowledge-cat-tree-node');
    var id = node ? node.dataset.catId : '';
    row.classList.toggle('active', (_knowledgeCurrentCategoryId === null ? id === '' : id === String(_knowledgeCurrentCategoryId)));
  });
  await loadKnowledgePage(1);
}

function onKnowledgeMidCategoryChange(cat) {
  selectKnowledgeCategory(cat);
}
async function selectKnowledgeCategory(cat) {
  var found = null;
  Object.keys(_knowledgeData.categoryMap || {}).forEach(function(id) {
    if (_knowledgeData.categoryMap[id].name === cat) found = parseInt(id, 10);
  });
  await selectKnowledgeCategoryId(null, found, !found);
}
async function selectKnowledgeScope(scope) {
  _knowledgeScope = scope || 'all';
  _knowledgePage = 1;
  _knowledgeCurrentCat = '';
  _knowledgeCurrentCategoryId = null;
  await loadKnowledgePage(1);
}

function showKnowledgeCatContextMenu(e, catId) {
  if (e) e.preventDefault();
  if (catId === '' || catId === null || catId === undefined) return;
  _knowledgeCatContextTarget = catId;
  var menu = document.getElementById('knowledgeCatContextMenu');
  if (!menu) return;
  menu.innerHTML = '<div class="knowledge-cat-context-item" onclick="addChildKnowledgeCategory()">新建子分类</div>' +
    '<div class="knowledge-cat-context-item" onclick="renameKnowledgeCategory()">重命名</div>' +
    '<div class="knowledge-cat-context-item danger" onclick="deleteKnowledgeCategory()">删除</div>';
  menu.style.left = (e.clientX || 0) + 'px';
  menu.style.top = (e.clientY || 0) + 'px';
  menu.classList.remove('hidden');
}

function hideKnowledgeCatContextMenu() {
  var menu = document.getElementById('knowledgeCatContextMenu');
  if (menu) menu.classList.add('hidden');
  _knowledgeCatContextTarget = null;
}

document.addEventListener('click', function(e) {
  var menu = document.getElementById('knowledgeCatContextMenu');
  if (menu && !menu.contains(e.target)) hideKnowledgeCatContextMenu();
});

async function createNewKnowledgeCategory() {
  var name = prompt('请输入新分类名称');
  if (!name || !name.trim()) return;
  try {
    await apiFetch('/api/knowledge/categories', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), projectId: _knowledgeProjectId })
    });
    showToast('✅ 分类已创建');
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 创建分类失败');
    console.warn('[KnowledgeCat] create failed:', e);
  }
}

async function addChildKnowledgeCategory() {
  if (!_knowledgeCatContextTarget) return;
  var name = prompt('请输入子分类名称');
  if (!name || !name.trim()) return;
  try {
    await apiFetch('/api/knowledge/categories', {
      method: 'POST',
      body: JSON.stringify({ name: name.trim(), parentId: _knowledgeCatContextTarget, projectId: _knowledgeProjectId })
    });
    hideKnowledgeCatContextMenu();
    showToast('✅ 子分类已创建');
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 创建子分类失败');
    console.warn('[KnowledgeCat] add child failed:', e);
  }
}

async function renameKnowledgeCategory() {
  if (!_knowledgeCatContextTarget) return;
  var cat = _knowledgeData.categoryMap[_knowledgeCatContextTarget];
  var name = prompt('重命名分类', cat ? cat.name : '');
  if (!name || !name.trim()) return;
  try {
    await apiFetch('/api/knowledge/categories/' + encodeURIComponent(_knowledgeCatContextTarget), {
      method: 'PUT',
      body: JSON.stringify({ name: name.trim() })
    });
    hideKnowledgeCatContextMenu();
    showToast('✅ 已重命名');
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 重命名失败');
    console.warn('[KnowledgeCat] rename failed:', e);
  }
}

function _updateKnowledgeEditorProjectCategoryOptions() {
  var projEl = document.getElementById('knowledgeEditProject');
  var catEl = document.getElementById('knowledgeEditCategory');
  if (projEl) {
    var html = '<option value="">公共知识</option>';
    _getCurrentUserGroups().forEach(function(g) {
      html += '<option value="' + escapeAttr(g.id) + '">' + escapeHtml(g.name || g.id) + '</option>';
    });
    var currentVal = projEl.value;
    projEl.innerHTML = html;
    projEl.value = currentVal || _knowledgeProjectId || '';
    projEl.onchange = function() {
      _populateKnowledgeEditCategoryOptions(catEl, projEl.value, null);
    };
  }
  if (catEl) {
    _populateKnowledgeEditCategoryOptions(catEl, projEl ? projEl.value : _knowledgeProjectId, _knowledgeCurrentCategoryId);
  }
}

function _populateKnowledgeEditCategoryOptions(catEl, projectId, selectedId) {
  if (!catEl) return;
  var opts = '<option value="">选择分类</option>';
  function walk(cats, depth) {
    cats.forEach(function(c) {
      if ((c.projectId || '') !== (projectId || '')) return;
      opts += '<option value="' + escapeAttr(String(c.id)) + '">' + '　'.repeat(depth) + escapeHtml(c.name) + '</option>';
      if (c.children && c.children.length) walk(c.children, depth + 1);
    });
  }
  walk(_knowledgeData.categories || [], 0);
  catEl.innerHTML = opts;
  catEl.value = selectedId ? String(selectedId) : '';
}

async function deleteKnowledgeCategory() {
  if (!_knowledgeCatContextTarget) return;
  var cat = _knowledgeData.categoryMap[_knowledgeCatContextTarget];
  if (!confirm('确定删除分类“' + (cat ? cat.name : '') + '”？子分类和关联知识将变为未分类。')) return;
  try {
    await apiFetch('/api/knowledge/categories/' + encodeURIComponent(_knowledgeCatContextTarget), { method: 'DELETE' });
    hideKnowledgeCatContextMenu();
    if (_knowledgeCurrentCategoryId === _knowledgeCatContextTarget) {
      _knowledgeCurrentCategoryId = null;
      _knowledgeCurrentCat = '';
    }
    showToast('✅ 已删除');
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 删除失败');
    console.warn('[KnowledgeCat] delete failed:', e);
  }
}

function getKnowledgePreview(content) {
  if (!content) return '';
  return content.replace(/[#*`\[\]()!>\-]/g, '').replace(/\s+/g, ' ').trim();
}
function renderKnowledgeList(docs) {
  var el = document.getElementById('knowledgeMidItems');
  if (!el) return;
  el.classList.toggle('list-view', _knowledgeViewMode === 'list');
  // ★ feat/sb2-dense-views: 视图分发 (sb2 高密度 / list / card), 老大指令默认 sb2
  if (_knowledgeViewMode === 'sb2') {
    renderKnowledgeListDense(docs);
    return;
  }
  if (!docs || docs.length === 0) {
    // ① 区分搜索空态 vs 库空态: 搜索时无结果显「无匹配」+ 查看全部 CTA, 库空时显「暂无文档」+ 手动添加
    if (_knowledgeLastQuery) {
      el.innerHTML = '<div class="kb-search-state kb-search-empty">'
        + '<div class="kb-search-empty-icon">🔍</div>'
        + '<div class="kb-search-empty-text">无匹配文档 · 试试别的关键词</div>'
        + '<button class="kb-search-empty-btn" onclick="onKnowledgeSearchClear()">查看全部文档</button>'
        + '</div>';
    } else {
      el.innerHTML = '<div class="knowledge-mid-empty"><div class="knowledge-mid-empty-icon">📖</div><div class="knowledge-mid-empty-text">暂无知识文档</div><button class="knowledge-mid-empty-btn" onclick="createNewKnowledge()">手动添加</button></div>';
    }
    return;
  }
  var scopeClassMap = { global: 'scope-global', group: 'scope-group', personal: 'scope-personal' };
  var scopeNameMap = { global: '全局', group: '项目组', personal: '个人' };
  var html = '';
  if (_knowledgeViewMode === 'list') {
    docs.forEach(function(d) {
      var isActive = d.id === _knowledgeCurrentId;
      var scope = d.scope || 'global';
      // ④ pending 视觉: 整行加 .kb-list-item-pending 类, 浅黄底 + 待审核 chip
      var pendingClass = d.status === 'pending' ? ' kb-list-item-pending' : '';
      html += '<div class="knowledge-list-item' + (isActive ? ' active' : '') + pendingClass + '" data-id="' + escapeAttr(d.id || '') + '" style="--cat-color: ' + _getKnowledgeCategoryColorVar(d.category) + ';">';
      html += '<div class="knowledge-list-cat-line"></div>';
      html += '<div class="knowledge-list-main" onclick="selectKnowledgeItem(\'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')">';
      // ④ 搜索关键词高亮: 当 _knowledgeLastQuery 非空时, title 用 highlightSearch 包装
      var titleText = highlightSearch(d.title || d.name || '未命名', _knowledgeLastQuery);
      html += '<div class="knowledge-list-title-row"><div class="knowledge-list-title">' + titleText + (d.status === 'pending' ? '<span class="kb-pending-badge">待审核</span>' : '') + '</div><span class="knowledge-mid-card-tag">' + escapeHtml(d.category || '未分类') + '</span></div>';
      html += '<div class="knowledge-list-meta">';
      html += '<span class="knowledge-view-scope ' + (scopeClassMap[scope] || 'scope-global') + '">' + (scopeNameMap[scope] || '全局') + '</span>';
      html += '<span>' + formatRelativeTime(d.updatedAt) + '</span>';
      html += '</div></div>';
      html += '<div class="knowledge-list-actions">';
      if (d.status === 'pending') {
        // ④ 审核按钮加 data-attr 让 approve/reject handler 可 disable 防双击
        html += '<button class="kb-review-btn kb-review-approve" data-doc-id="' + escapeAttr(d.id || '') + '" onclick="event.stopPropagation();reviewKnowledgeDoc(this, \'approve\', \'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')" title="确认通过">✓</button>';
        html += '<button class="kb-review-btn kb-review-reject" data-doc-id="' + escapeAttr(d.id || '') + '" onclick="event.stopPropagation();reviewKnowledgeDoc(this, \'reject\', \'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')" title="驳回">✗</button>';
      }
      html += '<button onclick="event.stopPropagation();editKnowledgeDoc(\'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')" title="编辑">✏️</button>';
      html += '<button onclick="event.stopPropagation();deleteKnowledgeDoc(\'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')" title="删除">🗑️</button>';
      html += '</div>';
      html += '</div>';
    });
  } else {
    docs.forEach(function(d) {
      var isActive = d.id === _knowledgeCurrentId;
      var preview = getKnowledgePreview(d.content).slice(0, 100);
      var scope = d.scope || 'global';
      // ④ pending 视觉: 整卡片加 .kb-mid-card-pending 类
      var pendingClass = d.status === 'pending' ? ' kb-mid-card-pending' : '';
      html += '<div class="knowledge-mid-card' + (isActive ? ' active' : '') + pendingClass + '" data-id="' + escapeAttr(d.id || '') + '" style="--cat-color: ' + _getKnowledgeCategoryColorVar(d.category) + ';" onclick="selectKnowledgeItem(\'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')">';
      html += '<div class="knowledge-mid-card-header">';
      // ④ 搜索关键词高亮: title + preview
      var titleText = highlightSearch(d.title || d.name || '未命名', _knowledgeLastQuery);
      var previewText = highlightSearch(preview, _knowledgeLastQuery);
      html += '<div class="knowledge-mid-card-title-wrap"><div class="knowledge-mid-card-title">' + titleText + (d.status === 'pending' ? '<span class="kb-pending-badge">待审核</span>' : '') + '</div></div>';
      html += '<span class="knowledge-mid-card-tag">' + escapeHtml(d.category || '未分类') + '</span></div>';
      html += '<div class="knowledge-mid-card-body">' + previewText + (preview.length >= 100 ? '...' : '') + '</div>';
      if (d.status === 'pending') {
        html += '<div class="kb-card-review-actions">';
        html += '<button class="kb-review-btn kb-review-approve" data-doc-id="' + escapeAttr(d.id || '') + '" onclick="event.stopPropagation();reviewKnowledgeDoc(this, \'approve\', \'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')">✓ 确认通过</button>';
        html += '<button class="kb-review-btn kb-review-reject" data-doc-id="' + escapeAttr(d.id || '') + '" onclick="event.stopPropagation();reviewKnowledgeDoc(this, \'reject\', \'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')">✗ 驳回</button>';
        html += '</div>';
      }
      html += '<div class="knowledge-mid-card-meta">';
      html += '<span class="knowledge-view-scope ' + (scopeClassMap[scope] || 'scope-global') + '">' + (scopeNameMap[scope] || '全局') + '</span>';
      if (scope === 'group' && d.groupIds && d.groupIds.length > 0) {
        d.groupIds.slice(0, 3).forEach(function(gid) {
          html += '<span class="knowledge-mid-card-group-label">' + escapeHtml(_getGroupName(gid)) + '</span>';
        });
        if (d.groupIds.length > 3) html += '<span class="knowledge-mid-card-group-label">+' + (d.groupIds.length - 3) + '</span>';
      }
      html += '<span>' + formatRelativeTime(d.updatedAt) + '</span>';
      html += '</div>';
      html += '</div>';
    });
  }
  el.innerHTML = html;
}

// ★ feat/sb2-dense-views: 知识库 sb2 高密度表格 — 一行一条, ID+标题+chunks+更新时间, 三枚 tag
// 字段全部真实, 缺值显示 '-'; 行点击走现有 selectKnowledgeItem 逻辑
// 保留搜索三态 (有结果/无匹配/库空) + 空态
function renderKnowledgeListDense(docs) {
  var el = document.getElementById('knowledgeMidItems');
  if (!el) return;
  if (!docs || docs.length === 0) {
    // 复用现有空态逻辑 (搜索 vs 库空)
    if (_knowledgeLastQuery) {
      el.innerHTML = '<div class="kb-search-state kb-search-empty">'
        + '<div class="kb-search-empty-icon">🔍</div>'
        + '<div class="kb-search-empty-text">无匹配文档 · 试试别的关键词</div>'
        + '<button class="kb-search-empty-btn" onclick="onKnowledgeSearchClear()">查看全部文档</button>'
        + '</div>';
    } else {
      el.innerHTML = '<div class="knowledge-mid-empty"><div class="knowledge-mid-empty-icon">📖</div><div class="knowledge-mid-empty-text">暂无知识文档</div><button class="knowledge-mid-empty-btn" onclick="createNewKnowledge()">手动添加</button></div>';
    }
    return;
  }
  // 范围映射 (与原 list/card 同源)
  var scopeNameMap = { global: '全局', group: '项目组', personal: '个人' };
  var html = '';
  html += '<div class="sb2-kb-list-wrap"><div class="sb2-kb-list">';
  docs.forEach(function(d){
    var isActive = d.id === _knowledgeCurrentId;
    var scope = d.scope || 'global';
    var status = d.status || 'ok';
    var title = d.title || d.name || '未命名';
    var titleText = highlightSearch(title, _knowledgeLastQuery);
    var pendingClass = status === 'pending' ? ' sb2-kb-pending' : '';
    // ID (前缀 + 短 ID, 缺值显 '-')
    var shortId = d.id ? d.id.replace(/^kb_/, '').slice(0, 12) : '-';
    var idText = d.id ? '<span class="sb2-kb-list-id" title="' + escapeAttr(d.id) + '">' + escapeHtml(shortId) + '</span>' : '<span class="sb2-kb-list-id">-</span>';
    // 副信息: chunks 数 + 更新时间
    var chunksCount = (typeof d.chunks_count === 'number') ? d.chunks_count : (Array.isArray(d.chunks) ? d.chunks.length : 0);
    var updated = d.updatedAt ? formatRelativeTime(d.updatedAt) : '-';
    var subText = '<b>' + (chunksCount > 0 ? chunksCount : '-') + ' chunks</b> · ' + escapeHtml(updated);
    // 三枚 tag: 分类 / 范围 / 状态
    var catTag = d.category
      ? '<span class="sb2-tag">' + escapeHtml(d.category) + '</span>'
      : '<span class="sb2-tag sb2-tag-empty">-</span>';
    var scopeTag = '<span class="sb2-tag sb2-tag-scope">' + escapeHtml(scopeNameMap[scope] || '全局') + '</span>';
    var statusTag = '<span class="sb2-tag sb2-kb-tag-status" data-status="' + escapeAttr(status) + '">' + escapeHtml(status === 'ok' ? '已发布' : status === 'pending' ? '待审核' : status) + '</span>';
    // 行点击走 selectKnowledgeItem (现有打开逻辑, 老大硬约束)
    html += '<div class="sb2-kb-list-item' + (isActive ? ' active' : '') + pendingClass + '" data-id="' + escapeAttr(d.id || '') + '" onclick="selectKnowledgeItem(\'' + escapeAttr(d.id || '').replace(/'/g, "\\'") + '\')">';
    html += idText;
    html += '<div class="sb2-kb-list-main"><div class="sb2-kb-list-title">' + titleText + '</div><div class="sb2-kb-list-sub">' + subText + '</div></div>';
    html += '<div class="sb2-kb-list-tags">' + catTag + scopeTag + '</div>';
    html += '<div class="sb2-kb-list-tags">' + statusTag + '</div>';
    html += '</div>';
  });
  html += '</div></div>';
  el.innerHTML = html;
}

// ④ 审核按钮包装: 防双击(loading 0.5s) + 调原 approve/reject 函数
// 旧 onclick 直接调 approveKnowledgeDoc, 用户连续点两下会触发两次 API
// 包装后: 第一次点击立即 disabled + 改文案, 0.5s 内重复点击不响应
function reviewKnowledgeDoc(btnEl, action, docId) {
  if (!btnEl || btnEl.disabled) return;
  btnEl.disabled = true;
  var originalText = btnEl.textContent;
  btnEl.textContent = '⏳';
  setTimeout(function () { btnEl.disabled = false; btnEl.textContent = originalText; }, 500);
  if (action === 'approve') {
    if (typeof approveKnowledgeDoc === 'function') approveKnowledgeDoc(docId);
  } else if (action === 'reject') {
    if (typeof rejectKnowledgeDoc === 'function') rejectKnowledgeDoc(docId);
  }
}
function switchKnowledgeView(mode) {
  // ★ feat/sb2-dense-views: 加 'sb2' 模式 (高密度表格, 老大指令默认)
  if (mode === 'sb2' || mode === 'list' || mode === 'card') {
    _knowledgeViewMode = mode;
    try { localStorage.setItem('sb2_knowledgeViewMode', mode); } catch(e){}
  }
  var sb2Btn = document.getElementById('knowledgeViewSb2Btn');
  var cardBtn = document.getElementById('knowledgeViewCardBtn');
  var listBtn = document.getElementById('knowledgeViewListBtn');
  if (sb2Btn) sb2Btn.classList.toggle('active', _knowledgeViewMode === 'sb2');
  if (cardBtn) cardBtn.classList.toggle('active', _knowledgeViewMode === 'card');
  if (listBtn) listBtn.classList.toggle('active', _knowledgeViewMode === 'list');
  renderKnowledgeList(_knowledgeData.docs);
}

function editKnowledgeDoc(docId) {
  var doc = _knowledgeData.docs.find(function(d) { return d.id === docId; });
  if (!doc) return;
  _knowledgeCurrentId = docId;
  renderKnowledgeView(doc, true);
  _knowledgePreviewMode = false;
  updateKnowledgeEditorView();
  openKnowledgeEditor();
  var contentEl = document.getElementById('knowledgeEditContent');
  if (contentEl) {
    contentEl.focus();
    contentEl.setSelectionRange(contentEl.value.length, contentEl.value.length);
  }
}

// 知识审核闸：pending 条目确认通过（→ok 进 RAG）/ 驳回（→删除并级联清理 chunks）
async function approveKnowledgeDoc(docId) {
  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(docId), {
      method: 'PUT',
      body: JSON.stringify({ status: 'ok' })
    });
    if (resp && resp.ok) {
      showToast('✓ 已通过审核，条目进入可检索状态', 'success');
      await loadKnowledgePage();
    } else {
      showToast('❌ 审核操作失败（HTTP ' + (resp && resp.status) + '）', 'error');
    }
  } catch (e) {
    showToast('❌ 审核操作失败: ' + e.message, 'error');
  }
}

async function rejectKnowledgeDoc(docId) {
  if (!confirm('确认驳回该条目？驳回后将被删除，不再进入检索。')) return;
  try {
    var resp = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(docId), { method: 'DELETE' });
    if (resp && resp.ok) {
      showToast('✗ 已驳回并删除', 'success');
      await loadKnowledgePage();
    } else {
      showToast('❌ 驳回失败（HTTP ' + (resp && resp.status) + '）', 'error');
    }
  } catch (e) {
    showToast('❌ 驳回失败: ' + e.message, 'error');
  }
}

function renderKnowledgePagination(total, page, pageSize) {
  var el = document.getElementById('knowledgeMidPagination');
  if (!el || !total || total <= pageSize) {
    if (el) el.innerHTML = '';
    return;
  }
  var totalPages = Math.ceil(total / pageSize);
  var html = '';
  html += '<button ' + (page <= 1 ? 'disabled' : '') + ' onclick="loadKnowledgePage(' + (page - 1) + ')">←</button>';
  html += '<span>第' + page + '页 / 共' + totalPages + '页</span>';
  html += '<button ' + (page >= totalPages ? 'disabled' : '') + ' onclick="loadKnowledgePage(' + (page + 1) + ')">→</button>';
  el.innerHTML = html;
}
function renderKnowledgeStats(stats) {
  var el = document.getElementById('knowledgeMidStats');
  if (!el) return;
  stats = stats || {};
  var total = stats.total || 0;
  var byScope = stats.byScope || {};
  var byCategory = (stats.byCategory || []).slice(0, 8);
  var pending = stats.pendingChunks || 0;

  var scopeMap = [
    { key: 'global', label: '全局' },
    { key: 'team', label: '团队' },
    { key: 'personal', label: '个人' },
    { key: 'group', label: '项目组' }
  ];
  var scopeCards = '';
  scopeMap.forEach(function(item) {
    var count = byScope[item.key] || 0;
    scopeCards += '<div class="knowledge-stats-card"><div class="knowledge-stats-value">' + count + '</div><div class="knowledge-stats-label">' + item.label + '</div></div>';
  });

  var pendingCard = '';
  if (pending > 0) {
    pendingCard = '<div class="knowledge-stats-card knowledge-stats-pending"><div class="knowledge-stats-value">' + pending + '</div><div class="knowledge-stats-label">待向量</div></div>';
  }

  var catHtml = '';
  if (byCategory.length > 0) {
    catHtml = '<div class="knowledge-stats-categories">' + byCategory.map(function(c) {
      return '<span class="knowledge-stats-cat">' + escapeHtml(c.name) + '<span style="opacity:0.7"> · ' + c.count + '</span></span>';
    }).join('') + '</div>';
  }

  el.innerHTML = '<div class="knowledge-stats-container">' +
    '<div class="knowledge-stats-card knowledge-stats-total"><div class="knowledge-stats-value">' + total + '</div><div class="knowledge-stats-label">知识总数</div></div>' +
    '<div class="knowledge-stats-divider"></div>' +
    scopeCards +
    pendingCard +
    '</div>' + catHtml;
  el.style.display = '';
}
// ① 搜索空态的「查看全部文档」按钮 handler: 清空 input + query + 重新加载
function onKnowledgeSearchClear() {
  _knowledgeLastQuery = '';
  var input = document.getElementById('knowledgeMidSearch');
  if (input) input.value = '';
  loadKnowledgePage();
}

// ④ 搜索关键词高亮 helper: 先 escapeHtml(text), 再 escapeHtml(query),
// 在转义后文本上 case-insensitive 替换, 命中片段包 <mark>
// 严格防注入: text 和 query 都先转义, query 还做正则元字符转义
// 仅当 _knowledgeLastQuery 非空时调用, 正常浏览列表不高亮
function highlightSearch(text, query) {
  if (!text) return '';
  var safe = escapeHtml(text);
  if (!query) return safe;
  // query 转义正则元字符, 再 escapeHtml 防注入
  var escapedQuery = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  var safeQuery = escapeHtml(escapedQuery);
  // case-insensitive 全局替换, 保留原文大小写
  try {
    var re = new RegExp('(' + safeQuery + ')', 'gi');
    return safe.replace(re, '<mark class="kb-search-hl">$1</mark>');
  } catch (e) {
    return safe;  // 正则构造失败时兑底返回原文, 不抛错
  }
}

// ② 分类计数: 全量拉取 docs(limit=1000), 本地按 doc.categoryId 聚合
// 父分类计数 = 自身 + 所有后代 categoryId 的累加
// 仅首次打开 KB / 手动刷新时拉, 切分类翻页复用缓存
// ★ fix/kb-counts: 数据源从 /api/knowledge?limit=1000 (老表, 字段只有 category 文本名)
//   改到循环分页拉 /api/knowledge/entries (新表 kb_entries, 字段完整有 categoryId)。
//   生产 data 99 篇文档全在 kb_entries, 老表是空的 → 计数全 0。pageSize=100 (后端封顶),
//   用 data.total 算总页数, 5000 条封顶防御死循环, 每次请求带 _knowledgeProjectId + scope
//   (跟 L27978 拉取模式一致, 不传 categoryId 否则漏数)
async function loadKnowledgeCategoryCounts() {
  try {
    var pageSize = 100;
    var offset = 0;
    var allDocs = [];
    var total = 0;
    while (true) {
      var url = '/api/knowledge/entries?limit=' + pageSize + '&offset=' + offset;
      if (_knowledgeProjectId) url += '&projectId=' + encodeURIComponent(_knowledgeProjectId);
      if (_knowledgeScope && _knowledgeScope !== 'all') url += '&scope=' + encodeURIComponent(_knowledgeScope);
      var resp = await apiFetch(url);
      var data = await resp.json();
      var docs = data.docs || [];
      allDocs = allDocs.concat(docs);
      total = data.total || 0;
      if (offset === 0) {
        if (Math.ceil(total / pageSize) <= 1) break;
        offset += pageSize;
      } else {
        if (docs.length < pageSize) break;
        offset += pageSize;
      }
      if (offset / pageSize > 50) break;  // 5000 条封顶, 防止接口 bug 死循环
    }
    var counts = {};
    allDocs.forEach(function (d) {
      if (d.categoryId === null || d.categoryId === undefined) return;
      var k = String(d.categoryId);
      counts[k] = (counts[k] || 0) + 1;
    });
    _knowledgeCatCounts = counts;
    _knowledgeCatCountsTotal = total;
    return counts;
  } catch (e) {
    console.warn('[KB Counts] 拉取失败:', e);
    _knowledgeCatCounts = {};
    _knowledgeCatCountsTotal = 0;
    return {};
  }
}

// ② 递归算父分类总数 = 自身 categoryId 的文档数 + 所有 children 的累加
// _renderKnowledgeCatNode 在渲染每个节点时调用, 拿到的就是该分类(及后代)的总文档数
function _getCategoryCountWithChildren(cat) {
  if (!_knowledgeCatCounts) return 0;
  var self = (_knowledgeCatCounts[String(cat.id)] || 0);
  var children = cat.children || [];
  children.forEach(function (c) {
    self += _getCategoryCountWithChildren(c);
  });
  return self;
}

// ② 强制刷新分类计数(绑到手动 refresh 按钮)
async function refreshKnowledgeCategoryCounts() {
  await loadKnowledgeCategoryCounts();
  var tree = document.getElementById('knowledgeMidCategory');
  if (tree && typeof renderKnowledgeCategories === 'function') {
    // 重新触发分类树渲染(从缓存读 _knowledgeData.categories)
    var cats = (window._knowledgeData && window._knowledgeData.categories) || [];
    renderKnowledgeCategories(cats);
  }
}

function onKnowledgeSearch(value) {
  clearTimeout(window._knowledgeSearchTimer);
  window._knowledgeSearchTimer = setTimeout(async function () {
    _knowledgePage = 1;
    var kw = (value || '').trim();
    _knowledgeLastQuery = kw;  // ① 记录当前 query, 让 renderKnowledgeList 区分搜索空态 vs 库空态
    if (!kw) {
      loadKnowledgePage();
      return;
    }
    var midItems = document.getElementById('knowledgeMidItems');
    // ① spinner + 文字(复用 :root 现有 @keyframes spin 0.8s linear infinite)
    if (midItems) midItems.innerHTML = '<div class="kb-search-state kb-search-loading"><div class="kb-search-spinner"></div><div>搜索中...</div></div>';
    try {
      var r = await apiFetch('/api/knowledge/search', {
        method: 'POST',
        body: JSON.stringify({ query: kw, limit: 20, scope: (_knowledgeScope !== 'all' ? _knowledgeScope : null), category: _knowledgeCurrentCat || null, categoryId: _knowledgeCurrentCategoryId || null, projectId: _knowledgeProjectId || null })
      });
      var data = await r.json();
      var docs = (data.docs || []).map(function(d) { d.scope = d.scope || 'global'; return d; });
      _knowledgeData.docs = docs;
      _knowledgeData.total = docs.length;
      renderKnowledgeList(docs);
      renderKnowledgePagination(0, 1, _knowledgePageSize);
    } catch (e) {
      console.warn('[KnowledgeSearch] failed:', e);
      // ① 失败: spinner 替换为 retry 按钮(避免用户刷新整页)
      if (midItems) midItems.innerHTML = '<div class="kb-search-state kb-search-fail"><div class="kb-search-fail-icon">⚠️</div><div class="kb-search-fail-text">搜索失败,请重试</div><button class="kb-search-retry-btn" onclick="onKnowledgeSearch(document.getElementById(\'knowledgeMidSearch\').value)">重试</button></div>';
    }
  }, 300);
}
function selectKnowledgeItem(id) {
  _knowledgeCurrentId = id;
  var doc = _knowledgeData.docs.find(function(d) { return d.id === id; });
  if (!doc) return;
  renderKnowledgeView(doc);
  var right = document.getElementById('knowledgeRight');
  var overlay = document.getElementById('knowledgeRightOverlay');
  if (right) right.classList.add('active');
  if (overlay) overlay.classList.add('open');
  // Update active card/list item
  document.querySelectorAll('.knowledge-mid-card, .knowledge-list-item').forEach(function(el) {
    el.classList.remove('active');
  });
  var activeEl = document.querySelector('.knowledge-mid-card[data-id="' + escapeAttr(id || '') + '"], .knowledge-list-item[data-id="' + escapeAttr(id || '') + '"]');
  if (activeEl) activeEl.classList.add('active');
}

function renderKnowledgeView(doc, skipPanelToggle) {
  var viewPanel = document.getElementById('knowledgeViewPanel');
  var editPanel = document.getElementById('knowledgeEditPanel');
  if (!skipPanelToggle) {
    if (viewPanel) viewPanel.classList.remove('hidden');
    if (editPanel) editPanel.classList.add('hidden');
  }

  var titleEl = document.getElementById('knowledgeViewTitle');
  var catEl = document.getElementById('knowledgeViewCategory');
  var scopeEl = document.getElementById('knowledgeViewScope');
  var timeEl = document.getElementById('knowledgeViewTime');
  var bodyEl = document.getElementById('knowledgeViewBody');

  if (titleEl) titleEl.textContent = doc.title || doc.name || '未命名';
  if (catEl) catEl.textContent = doc.category || '未分类';

  var scopeClassMap = { global: 'scope-global', group: 'scope-group', personal: 'scope-personal' };
  var scopeNameMap = { global: '全局', group: '项目组', personal: '个人' };
  var scope = doc.scope || 'global';
  if (scopeEl) {
    scopeEl.textContent = scopeNameMap[scope] || '全局';
    scopeEl.className = 'knowledge-view-scope ' + (scopeClassMap[scope] || 'scope-global');
  }
  // 显示所属项目组标签
  var groupsEl = document.getElementById('knowledgeViewGroups');
  if (groupsEl) {
    var gids = doc.groupIds || [];
    if (scope === 'group' && gids.length > 0) {
      groupsEl.innerHTML = gids.map(function(gid) { return '<span class="knowledge-view-group-label">' + escapeHtml(_getGroupName(gid)) + '</span>'; }).join('');
    } else {
      groupsEl.innerHTML = '';
    }
  }
  if (timeEl) timeEl.textContent = '更新于 ' + formatDate(doc.updatedAt);
  if (bodyEl) bodyEl.innerHTML = renderMarkdown(doc.content || '');
  // 编辑模式下同步项目组选择器（仅 group 知识）
  if (scope === 'group') {
    renderKnowledgeGroupSelector('knowledgeEditGroups', doc.groupIds || []);
  } else {
    renderKnowledgeGroupSelector('knowledgeEditGroups', []);
  }

  // 同步编辑表单
  var editTitle = document.getElementById('knowledgeEditTitle');
  var editProj = document.getElementById('knowledgeEditProject');
  var editCat = document.getElementById('knowledgeEditCategory');
  var editContent = document.getElementById('knowledgeEditContent');
  if (editTitle) editTitle.value = doc.title || doc.name || '';
  if (editContent) editContent.value = doc.content || '';
  _updateKnowledgeEditorProjectCategoryOptions();
  if (editProj) editProj.value = doc.projectId || _knowledgeProjectId || '';
  if (editCat) {
    _populateKnowledgeEditCategoryOptions(editCat, editProj ? editProj.value : _knowledgeProjectId, doc.categoryId || _knowledgeCurrentCategoryId);
  }
  _knowledgePreviewMode = false;
  updateKnowledgeEditorView();
}
function createNewKnowledge() {
  _knowledgeCurrentId = null;
  var titleEl = document.getElementById('knowledgeEditTitle');
  var projEl = document.getElementById('knowledgeEditProject');
  var catEl = document.getElementById('knowledgeEditCategory');
  var contentEl = document.getElementById('knowledgeEditContent');
  var statusEl = document.getElementById('knowledgeEditStatus');
  if (titleEl) titleEl.value = '';
  if (projEl) projEl.value = _knowledgeProjectId || '';
  if (catEl) _populateKnowledgeEditCategoryOptions(catEl, projEl ? projEl.value : _knowledgeProjectId, _knowledgeCurrentCategoryId);
  if (contentEl) contentEl.value = '';
  if (statusEl) statusEl.textContent = '';
  _knowledgePreviewMode = false;
  updateKnowledgeEditorView();
  openKnowledgeEditor();
  // 项目组 tab 下新建时，默认选中第一个项目组
  if (_knowledgeScope === 'group') {
    var userGroups = _getCurrentUserGroups();
    renderKnowledgeGroupSelector('knowledgeEditGroups', userGroups.length > 0 ? [userGroups[0].id] : []);
  } else {
    renderKnowledgeGroupSelector('knowledgeEditGroups', []);
  }
  if (titleEl) {
    try { titleEl.focus(); } catch (e) {}
  }
  console.debug('[Knowledge] 新建知识，表单已清空');
}
function updateKnowledgeEditorView() {
  var textarea = document.getElementById('knowledgeEditContent');
  var preview = document.getElementById('knowledgeEditPreview');
  if (!textarea || !preview) return;
  if (_knowledgePreviewMode) {
    if (typeof marked === 'undefined') {
      showToast('Markdown 渲染库未加载，已切换回纯文本编辑');
      _knowledgePreviewMode = false;
      document.querySelectorAll('.knowledge-editor-tab').forEach(function(t) {
        t.classList.toggle('active', t.dataset.tab === 'edit');
      });
      textarea.classList.remove('hidden');
      preview.classList.add('hidden');
      return;
    }
    textarea.classList.add('hidden');
    preview.classList.remove('hidden');
    preview.innerHTML = renderMarkdown(textarea.value);
  } else {
    textarea.classList.remove('hidden');
    preview.classList.add('hidden');
  }
}
function switchKnowledgeTab(tab) {
  _knowledgePreviewMode = (tab === 'preview');
  document.querySelectorAll('.knowledge-editor-tab').forEach(function(t) {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  updateKnowledgeEditorView();
}
function insertMarkdown(before, after) {
  var textarea = document.getElementById('knowledgeEditContent');
  if (!textarea) return;
  var start = textarea.selectionStart || 0;
  var end = textarea.selectionEnd || 0;
  var selected = textarea.value.substring(start, end);
  var replacement = before + selected + after;
  textarea.value = textarea.value.substring(0, start) + replacement + textarea.value.substring(end);
  textarea.selectionStart = start + before.length;
  textarea.selectionEnd = start + before.length + selected.length;
  textarea.focus();
}
function renderMarkdown(text) {
  if (typeof marked !== 'undefined') {
    try {
      return marked.parse(text || '');
    } catch (e) {
      console.warn('[Markdown] 解析失败:', e);
    }
  }
  // fallback: 简单转换
  return escapeHtml(text || '').replace(/\n/g, '<br>');
}
async function saveKnowledgeFromEditor() {
  var title = document.getElementById('knowledgeEditTitle').value.trim();
  var content = document.getElementById('knowledgeEditContent').value.trim();
  var category = document.getElementById('knowledgeEditCategory');
  var project = document.getElementById('knowledgeEditProject');
  var categoryId = category ? category.value : '';
  var projectId = project ? project.value : '';
  if (!title || !content) {
    showToast('请填写标题和内容');
    return;
  }
  var statusEl = document.getElementById('knowledgeEditStatus');
  statusEl.textContent = '保存中...';
  try {
    var categoryName = categoryId && _knowledgeData.categoryMap[categoryId] ? _knowledgeData.categoryMap[categoryId].name : '';
    var body = { title: title, content: content, category: categoryName, categoryId: categoryId ? parseInt(categoryId, 10) : null, projectId: projectId || null };
    if (!_knowledgeCurrentId) {
      // 新建时默认使用当前选中的 scope
      var createScope = (_knowledgeScope && _knowledgeScope !== 'all') ? _knowledgeScope : 'global';
      body.scope = createScope;
      if (createScope === 'group') {
        var selectedGroupIds = getSelectedKnowledgeGroupIds('knowledgeEditGroups');
        if (selectedGroupIds.length === 0) {
          showToast('请选择至少一个项目组');
          statusEl.textContent = '';
          return;
        }
        body.groupIds = selectedGroupIds;
      }
      if (createScope === 'personal') {
        body.empId = currentUser ? currentUser.userId || currentUser.id : '';
      }
    }
    var url = _knowledgeCurrentId ? '/api/knowledge/entries/' + encodeURIComponent(_knowledgeCurrentId) : '/api/knowledge/entries';
    var method = _knowledgeCurrentId ? 'PUT' : 'POST';
    var r = await apiFetch(url, { method: method, body: JSON.stringify(body) });
    var result = await r.json();
    statusEl.textContent = '知识库更新中...';
    showToast('✅ 已保存，正在生成向量...');
    // 刷新列表
    await loadKnowledgePage();
    if (result && result.id) {
      selectKnowledgeItem(result.id);
    }
    statusEl.textContent = '已保存并生成向量';
    setTimeout(function() { statusEl.textContent = ''; }, 3000);
  } catch (e) {
    statusEl.textContent = '保存失败';
    showToast('❌ 保存失败');
    console.warn('[KnowledgePage] 保存失败:', e);
  }
}

function editCurrentKnowledge() {
  if (!_knowledgeCurrentId) return;
  var doc = _knowledgeData.docs.find(function (d) { return d.id === _knowledgeCurrentId; });
  if (doc) renderKnowledgeView(doc, true);
  _knowledgePreviewMode = false;
  updateKnowledgeEditorView();
  openKnowledgeEditor();
  var contentEl = document.getElementById('knowledgeEditContent');
  if (contentEl) {
    contentEl.focus();
    contentEl.setSelectionRange(contentEl.value.length, contentEl.value.length);
  }
}

async function deleteCurrentKnowledge() {
  if (!_knowledgeCurrentId) {
    showToast('请先选择要删除的知识');
    return;
  }
  if (!confirm('确定删除该文档吗？此操作不可恢复')) return;
  try {
    await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_knowledgeCurrentId), { method: 'DELETE', silent: true });
    showToast('✅ 已删除');
    _knowledgeCurrentId = null;
    closeKnowledgeRight();
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 删除失败');
    console.warn('[KnowledgePage] 删除失败:', e);
  }
}

async function moveCurrentKnowledge() {
  if (!_knowledgeCurrentId) return;
  var doc = _knowledgeData.docs.find(function (d) { return d.id === _knowledgeCurrentId; });
  if (!doc) return;
  var currentScope = doc.scope || 'global';
  var userGroups = _getCurrentUserGroups();
  var currentGroupIds = doc.groupIds || [];
  var groupCheckboxesHtml = '';
  if (userGroups.length === 0) {
    groupCheckboxesHtml = '<div style="font-size:13px;color:var(--text-tertiary);padding:8px 0;">暂无可用项目组</div>';
  } else {
    userGroups.forEach(function (g) {
      var checked = currentGroupIds.indexOf(g.id) >= 0 ? ' checked' : '';
      groupCheckboxesHtml += '<label style="display:flex;align-items:center;gap:8px;padding:8px 0;cursor:pointer;font-size:14px;color:var(--text-primary);"><input type="checkbox" name="knowledgeMoveGroupIds" value="' + escapeAttr(g.id) + '"' + checked + ' style="width:16px;height:16px;"> ' + escapeHtml(g.name || g.id) + '</label>';
    });
  }
  var dialogId = 'knowledgeMoveDialog' + Date.now();
  var html = '<div id="' + dialogId + '" style="padding:8px 0;">'
    + '<div style="font-size:14px;color:var(--text-secondary);margin-bottom:16px;">选择要移动到的目标知识库：</div>'
    + '<label style="display:flex;align-items:center;gap:10px;padding:12px;border:1px solid var(--separator);border-radius:8px;margin-bottom:10px;cursor:pointer;"><input type="radio" name="knowledgeMoveScope" value="global"' + (currentScope === 'global' ? ' checked' : '') + ' style="width:18px;height:18px;"><span style="font-size:14px;">全局知识库</span></label>'
    + '<label style="display:flex;align-items:center;gap:10px;padding:12px;border:1px solid var(--separator);border-radius:8px;margin-bottom:10px;cursor:pointer;"><input type="radio" name="knowledgeMoveScope" value="group"' + (currentScope === 'group' ? ' checked' : '') + ' style="width:18px;height:18px;"><span style="font-size:14px;">项目组知识库</span></label>'
    + '<div id="knowledgeMoveGroupWrap" style="margin:-4px 0 10px 28px;' + (currentScope === 'group' ? '' : 'display:none;') + '">' + groupCheckboxesHtml + '</div>'
    + '<label style="display:flex;align-items:center;gap:10px;padding:12px;border:1px solid var(--separator);border-radius:8px;margin-bottom:4px;cursor:pointer;"><input type="radio" name="knowledgeMoveScope" value="personal"' + (currentScope === 'personal' ? ' checked' : '') + ' style="width:18px;height:18px;"><span style="font-size:14px;">个人知识库</span></label>'
    + '</div>';
  showModal('移动文档', html);

  setTimeout(function () {
    var dialog = document.getElementById(dialogId);
    if (!dialog) return;
    var radios = dialog.querySelectorAll('input[name="knowledgeMoveScope"]');
    var groupWrap = document.getElementById('knowledgeMoveGroupWrap');
    radios.forEach(function (radio) {
      radio.addEventListener('change', function () {
        if (groupWrap) groupWrap.style.display = radio.value === 'group' && radio.checked ? 'block' : 'none';
      });
    });
    var panel = dialog.closest('.modal-panel');
    if (panel) {
      var footer = document.createElement('div');
      footer.style.cssText = 'display:flex;justify-content:flex-end;gap:10px;margin-top:20px;padding-top:16px;border-top:1px solid var(--separator);';
      footer.innerHTML = '<button onclick="closeModal()" style="padding:8px 18px;border-radius:8px;border:none;background:var(--bg-secondary);color:var(--text-primary);font-size:14px;cursor:pointer;">取消</button><button id="knowledgeMoveConfirmBtn" style="padding:8px 18px;border-radius:8px;border:none;background:var(--accent);color:#fff;font-size:14px;cursor:pointer;">确认移动</button>';
      panel.appendChild(footer);
      document.getElementById('knowledgeMoveConfirmBtn').addEventListener('click', async function () {
        var selected = dialog.querySelector('input[name="knowledgeMoveScope"]:checked');
        if (!selected) { showToast('请选择目标知识库'); return; }
        var targetScope = selected.value;
        var groupIds = [];
        if (targetScope === 'group') {
          groupIds = Array.from(dialog.querySelectorAll('input[name="knowledgeMoveGroupIds"]:checked')).map(function(cb) { return cb.value; });
          if (groupIds.length === 0) { showToast('请至少选择一个项目组'); return; }
        }
        closeModal();
        try {
          var r = await apiFetch('/api/knowledge/' + encodeURIComponent(_knowledgeCurrentId) + '/move', {
            method: 'POST',
            body: JSON.stringify({ scope: targetScope, groupIds: groupIds })
          });
          await r.json();
          showToast('✅ 已移动');
          await loadKnowledgePage();
          var moved = _knowledgeData.docs.find(function (d) { return d.id === _knowledgeCurrentId; });
          if (moved) renderKnowledgeView(moved);
        } catch (e) {
          showToast('❌ 移动失败');
          console.warn('[KnowledgePage] 移动失败:', e);
        }
      });
    }
  }, 50);
}

// ========== 知识库编辑弹窗 ==========
var _knowledgeModalDocId = null;
function openKnowledgeModal(docId) {
  var doc = (_knowledgeData.docs || []).find(function(d) { return d.id === docId; });
  if (!doc) return;
  _knowledgeModalDocId = docId;
  document.getElementById('knowledgeModalTitle').value = doc.title || doc.name || '';
  document.getElementById('knowledgeModalContent').value = doc.content || '';
  // 分类下拉选项：从现有分类来
  var catInput = document.getElementById('knowledgeModalCategory');
  var dataList = document.getElementById('knowledgeModalCategoryList');
  var cats = (_knowledgeData.categories || []).slice();
  if (doc.category && cats.indexOf(doc.category) === -1) {
    cats.push(doc.category);
  }
  dataList.innerHTML = cats.map(function(cat) { return '<option value="' + escapeAttr(cat) + '">' + escapeHtml(cat) + '</option>'; }).join('');
  catInput.value = doc.category || '';
  document.getElementById('knowledgeModalOverlay').classList.add('active');
}
function closeKnowledgeModal(e) {
  // 允许点击遮罩或关闭按钮；e 不存在时直接关闭
  if (e && e.target !== e.currentTarget) return;
  document.getElementById('knowledgeModalOverlay').classList.remove('active');
  _knowledgeModalDocId = null;
}
async function saveKnowledgeFromModal() {
  if (!_knowledgeModalDocId) return;
  var title = document.getElementById('knowledgeModalTitle').value.trim();
  var category = document.getElementById('knowledgeModalCategory').value.trim();
  var content = document.getElementById('knowledgeModalContent').value.trim();
  if (!title || !content) {
    showToast('请填写标题和内容');
    return;
  }
  var saveBtn = document.querySelector('.knowledge-modal-save');
  var originalText = saveBtn.textContent;
  saveBtn.textContent = '保存中...';
  saveBtn.disabled = true;
  try {
    var body = { title: title, content: content, category: category };
    var r = await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_knowledgeModalDocId), {
      method: 'PUT',
      body: JSON.stringify(body)
    });
    await r.json();
    showToast('✅ 已保存');
    await loadKnowledgePage();
    closeKnowledgeModal();
  } catch (e) {
    showToast('❌ 保存失败');
    console.warn('[KnowledgePage] 弹窗保存失败:', e);
  } finally {
    saveBtn.textContent = originalText;
    saveBtn.disabled = false;
  }
}

async function deleteKnowledgeFromEditor() {
  if (!_knowledgeCurrentId) {
    showToast('请先选择要删除的知识');
    return;
  }
  if (!confirm('确定删除该知识？此操作不可恢复。')) return;
  try {
    await apiFetch('/api/knowledge/entries/' + encodeURIComponent(_knowledgeCurrentId), { method: 'DELETE', silent: true });
    showToast('✅ 已删除');
    closeKnowledgeRight();
    await loadKnowledgePage();
  } catch (e) {
    showToast('❌ 删除失败');
    console.warn('[KnowledgePage] 删除失败:', e);
  }
}
// (旧达人库表单函数已移除，兼容别名指向新达人库)
function searchInfluencers() { if (_talentActiveTab === 'analyzed') { _analyzedPage = 1; renderAnalyzedPage(); } else loadTalents(1); }
function renderInfluencerList() {}
function createNewInfluencer() { createNewTalent(); }
function deleteInfluencer(infId) {
  _talentCurrentId = infId;
  deleteCurrentTalent();
}

// ========== 智能匹配引擎 ==========
function matchProductToInfluencer(productId) {
  document.getElementById('matchModalTitle').textContent = '智能匹配达人';
  document.getElementById('matchModalBody').innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">正在分析匹配...</div>';
  document.getElementById('matchModalOverlay').classList.add('open');
  apiFetch('/api/match/product-to-influencer', {method: 'POST', body: JSON.stringify({productId: productId, limit: 10})})
    .then(function(r){ return r.json(); })
    .then(function(data){ renderMatchResults(data, 'product'); })
    .catch(function(e){
      document.getElementById('matchModalBody').innerHTML = '<div style="padding:40px;text-align:center;color:var(--danger);">匹配失败</div>';
      console.warn('[Match] product->influencer failed:', e);
    });
}
function matchInfluencerToProduct(infId) {
  document.getElementById('matchModalTitle').textContent = '智能匹配商品';
  document.getElementById('matchModalBody').innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">正在分析匹配...</div>';
  document.getElementById('matchModalOverlay').classList.add('open');
  apiFetch('/api/match/influencer-to-product', {method: 'POST', body: JSON.stringify({influencerId: infId, limit: 10})})
    .then(function(r){ return r.json(); })
    .then(function(data){ renderMatchResults(data, 'influencer'); })
    .catch(function(e){
      document.getElementById('matchModalBody').innerHTML = '<div style="padding:40px;text-align:center;color:var(--danger);">匹配失败</div>';
      console.warn('[Match] influencer->product failed:', e);
    });
}
function renderMatchResults(data, type) {
  var results = data.results || [];
  var subject = type === 'product' ? data.product : data.influencer;
  var html = '';
  if (results.length === 0) {
    html = '<div style="padding:40px;text-align:center;color:var(--text-tertiary);">未找到合适的匹配项</div>';
    document.getElementById('matchModalBody').innerHTML = html;
    return;
  }
  html += '<div style="margin-bottom:12px;color:var(--text-secondary);font-size:13px;">';
  html += '为 <strong style="color:var(--text-primary);">' + escapeHtml(subject.name) + '</strong> 找到 ' + results.length + ' 个匹配项';
  html += '</div>';
  results.forEach(function(r, idx){
    var item = type === 'product' ? r.influencer : r.product;
    var score = r.score;
    var percent = r.matchPercent;
    var reasons = (r.reasons || []).map(function(rr){ return '<span class="product-tag" style="background:rgba(59,130,246,0.12);color:#3b82f6;">' + escapeHtml(rr) + '</span>'; }).join(' ');
    var scoreColor = percent >= 80 ? '#22c55e' : (percent >= 50 ? '#f59e0b' : '#ef4444');
    html += '<div style="display:flex;align-items:center;gap:12px;padding:14px;border-radius:10px;background:var(--card-bg);margin-bottom:10px;border:1px solid var(--border-color);">';
    html += '<div style="flex-shrink:0;width:48px;height:48px;border-radius:10px;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center;font-size:20px;">' + (type==='product'?'🎙️':'📦') + '</div>';
    html += '<div style="flex:1;min-width:0;">';
    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">';
    html += '<div style="font-weight:600;color:var(--text-primary);font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + escapeHtml(item.name) + '</div>';
    html += '<div style="font-weight:700;font-size:16px;color:' + scoreColor + ';">' + score + '分</div>';
    html += '</div>';
    html += '<div style="width:100%;height:6px;background:var(--bg-tertiary);border-radius:3px;margin-bottom:6px;overflow:hidden;">';
    html += '<div style="width:' + percent + '%;height:100%;background:' + scoreColor + ';border-radius:3px;transition:width 0.4s ease;"></div>';
    html += '</div>';
    html += '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:4px;">';
    if (type === 'product') {
      html += '<span>' + escapeHtml(item.platform || '-') + '</span> · ';
      html += '<span>粉丝: ' + (item.followerCount >= 10000 ? (item.followerCount/10000).toFixed(1)+'w' : (item.followerCount||0)) + '</span> · ';
      html += '<span>报价: ¥' + (item.cooperationPrice||0).toFixed(0) + '</span> · ';
      html += '<span>互动: ' + (item.engagementRate||0) + '%</span>';
    } else {
      html += '<span>SKU: ' + escapeHtml(item.sku || '-') + '</span> · ';
      html += '<span>分类: ' + escapeHtml(item.category || '未分类') + '</span> · ';
      html += '<span>¥' + (item.price||0).toFixed(2) + '</span>';
    }
    html += '</div>';
    html += '<div style="display:flex;flex-wrap:wrap;gap:4px;">' + reasons + '</div>';
    html += '</div>';
    html += '</div>';
  });
  document.getElementById('matchModalBody').innerHTML = html;
}
function closeMatchModal() {
  document.getElementById('matchModalOverlay').classList.remove('open');
}