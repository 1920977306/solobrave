// ===== 更新聊天头部 =====
function updateChatHeader(empId) {
  var prompt = EMPLOYEE_PROMPTS[empId];
  if (!prompt) return;

  var header = document.querySelector('.chat-header-v2');
  if (!header) return;

  var avatarDiv = header.querySelector('.avatar');
  if (avatarDiv) avatarDiv.textContent = AVATAR_MAP[empId] || '🤖';

  var nameDiv = header.querySelector('.chat-header-name');
  if (nameDiv) nameDiv.textContent = prompt.name;

  var roleDiv = header.querySelector('.chat-header-role');
  if (roleDiv) roleDiv.textContent = prompt.role;
}

// ===== 绑定员工切换 =====
function bindEmployeeSwitch() {
  var items = document.querySelectorAll('.list-item[data-id]');
  for (var i = 0; i < items.length; i++) {
    items[i].addEventListener('click', function() {
      var empId = this.getAttribute('data-id');
      if (!empId) return;

      // 更新聊天头部
      updateChatHeader(empId);

      // 如果这个员工没有消息历史，显示欢迎消息
      var msgs = getMessages(empId);
      if (msgs.length === 0) {
        var prompt = EMPLOYEE_PROMPTS[empId];
        if (prompt) {
          var welcome = '你好！我是' + prompt.name + '，' + prompt.role + '。有什么可以帮你的？';
          addMessage(empId, 'assistant', welcome, getTimeStr());
        }
      }

      // 渲染该员工的消息
      renderMessages(empId);
    });
  }
}

// ===== 事件绑定 =====
document.addEventListener('DOMContentLoaded', function() {
  // 1. 先从 localStorage 加载历史消息
  loadChatData();

  // 2. 绑定发送按钮
  var btn = document.getElementById('sendBtn');
  var input = document.getElementById('msgInput');

  if (btn) {
    btn.addEventListener('click', function() {
      var text = input.value.trim();
      if (!text) return;
      if (currentGroupId) {
        sendGroupMessage(currentGroupId, text);
      } else {
        sendMessage(text);
      }
      input.value = '';
    });
  }

  if (input) {
    input.addEventListener('keydown', function(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (btn) btn.click();
      }
    });
  }

  // 3. 绑定员工切换
  bindEmployeeSwitch();

  // 4. 绑定项目组切换
  bindProjectSwitch();

  // 5. 绑定 @ 提及
  bindMentionEvents();

  // 6. 绑定搜索快捷键
  bindSearchShortcut();

  // 7. 尝试连接 OpenClaw Gateway
  connectOpenClaw();

  // 8. 显示当前员工的欢迎消息或恢复历史
  var empId = getCurrentEmpId();
  if (empId) {
    var msgs = getMessages(empId);
    if (msgs.length === 0) {
      var prompt = EMPLOYEE_PROMPTS[empId];
      var welcomeText = '你好！我是' + (prompt ? prompt.name : empId) + '，有什么可以帮你的？';
      addMessage(empId, 'assistant', welcomeText, getTimeStr());
    }
    renderMessages(empId);
  }
});

// ===== 群聊切换 =====
var currentGroupId = null;

function bindProjectSwitch() {
  // 方式1：绑定 group-chat-item 元素（用户截图中的实际元素）
  var groupItems = document.querySelectorAll('.group-chat-item');
  for (var i = 0; i < groupItems.length; i++) {
    groupItems[i].addEventListener('click', function() {
      var name = this.querySelector('.group-chat-name');
      var groupName = name ? name.textContent.trim() : '';
      var projId = groupNameToId(groupName);
      if (!projId) return;
      switchToGroup(projId);
    });
  }

  // 方式2：绑定 list-item[data-proj] 元素（如果有的话）
  var items = document.querySelectorAll('.list-item[data-proj]');
  for (var j = 0; j < items.length; j++) {
    items[j].addEventListener('click', function() {
      var projId = this.getAttribute('data-proj');
      if (!projId) return;
      switchToGroup(projId);
    });
  }
}

function groupNameToId(name) {
  var map = {
    '快速研发组': 'proj1',
    '小程序开发组': 'proj2',
    'AI集成组': 'proj3',
    '公司全员大群': 'all-hands'
  };
  return map[name] || null;
}

function switchToGroup(projId) {
  currentGroupId = projId;

  var group = GROUP_CHATS[projId];
  updateChatHeaderGroup(group ? group.name : projId, group ? group.members.length : 0);

  if (getGroupMsgs(projId).length === 0) {
    addGroupMsg(projId, 'assistant', '系统', '群聊已创建，用 @名字 来呼叫AI同事。', getTimeStr());
  }
  renderGroupMessages(projId);
}

function updateChatHeaderGroup(name, count) {
  var header = document.querySelector('.chat-header-v2');
  if (!header) return;
  var avatarDiv = header.querySelector('.avatar');
  if (avatarDiv) avatarDiv.textContent = '👥';
  var nameDiv = header.querySelector('.chat-header-name');
  if (nameDiv) nameDiv.textContent = name;
  var roleDiv = header.querySelector('.chat-header-role');
  if (roleDiv) roleDiv.textContent = count + ' 位成员';
}

// ===== 消息格式化 =====
function formatMessage(text) {
  var html = escapeHtml(text);

  // 代码块：```language\ncode\n```
  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, function(match, lang, code) {
    var label = lang ? '<div class="code-label">' + lang + '</div>' : '';
    var highlighted = highlightCode(code, lang);
    return '<div class="code-block">'
      + label
      + '<pre><code>' + highlighted + '</code></pre>'
      + '<button class="code-copy" onclick="copyCode(this)">复制</button>'
      + '</div>';
  });

  // 行内代码：`code`
  html = html.replace(/`([^`]+)`/g, '<code class="inline-code">$1</code>');

  // 加粗：**text**
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');

  // 换行
  html = html.replace(/\n/g, '<br>');

  return html;
}

function copyCode(btn) {
  var code = btn.parentElement.querySelector('code');
  if (!code) return;
  var text = code.textContent;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text);
  } else {
    var ta = document.createElement('textarea');
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }
  btn.textContent = '已复制';
  setTimeout(function() { btn.textContent = '复制'; }, 1500);
}

// ===== 获取头像emoji =====
function getAvatarEmoji(empId) {
  return AVATAR_MAP[empId] || '🤖';
}

// ===== @ 成员选择列表 =====
var mentionDropdown = null;
var mentionSelectedIndex = 0;
var mentionVisible = false;

function createMentionDropdown() {
  if (mentionDropdown) return;
  mentionDropdown = document.createElement('div');
  mentionDropdown.className = 'mention-popup';
  mentionDropdown.style.display = 'none';
  document.body.appendChild(mentionDropdown);
}

function showMentionList(filter) {
  if (!mentionDropdown) createMentionDropdown();

  var members = [];
  var empIds = Object.keys(EMPLOYEE_PROMPTS);
  for (var i = 0; i < empIds.length; i++) {
    var id = empIds[i];
    var p = EMPLOYEE_PROMPTS[id];
    var name = p.name.toLowerCase();
    var filterLower = filter.toLowerCase();
    if (!filter || name.indexOf(filterLower) !== -1) {
      members.push({ id: id, name: p.name, role: p.role, emoji: getAvatarEmoji(id) });
    }
  }

  if (members.length === 0) {
    mentionDropdown.style.display = 'none';
    mentionVisible = false;
    return;
  }

  var html = '';
  for (var j = 0; j < members.length; j++) {
    var m = members[j];
    var cls = j === mentionSelectedIndex ? 'mention-item active' : 'mention-item';
    html += '<div class="' + cls + '" data-id="' + m.id + '" data-name="' + m.name + '">'
      + '<span class="mention-emoji">' + m.emoji + '</span>'
      + '<span class="mention-name">' + escapeHtml(m.name) + '</span>'
      + '<span class="mention-role">' + escapeHtml(m.role) + '</span>'
      + '</div>';
  }
  mentionDropdown.innerHTML = html;
  mentionDropdown.style.display = 'block';
  mentionVisible = true;
  mentionSelectedIndex = 0;

  positionMentionDropdown();

  var items = mentionDropdown.querySelectorAll('.mention-item');
  for (var k = 0; k < items.length; k++) {
    (function(item) {
      item.addEventListener('mousedown', function(e) {
        e.preventDefault();
        selectMention(item.getAttribute('data-name'));
      });
    })(items[k]);
  }
}

function hideMentionList() {
  if (mentionDropdown) mentionDropdown.style.display = 'none';
  mentionVisible = false;
}

function positionMentionDropdown() {
  var input = document.getElementById('msgInput');
  if (!input || !mentionDropdown) return;
  var rect = input.getBoundingClientRect();
  mentionDropdown.style.position = 'fixed';
  mentionDropdown.style.left = rect.left + 'px';
  mentionDropdown.style.bottom = (window.innerHeight - rect.top + 4) + 'px';
  mentionDropdown.style.width = Math.min(rect.width, 320) + 'px';
}

function selectMention(name) {
  var input = document.getElementById('msgInput');
  if (!input) return;
  var val = input.value;
  var atPos = val.lastIndexOf('@');
  if (atPos === -1) return;
  input.value = val.substring(0, atPos) + '@' + name + ' ';
  input.focus();
  hideMentionList();
}

function getMentionFilter() {
  var input = document.getElementById('msgInput');
  if (!input) return '';
  var val = input.value;
  var atPos = val.lastIndexOf('@');
  if (atPos === -1) return '';
  var afterAt = val.substring(atPos + 1);
  if (afterAt.indexOf(' ') !== -1) return '';
  return afterAt;
}

function bindMentionEvents() {
  var input = document.getElementById('msgInput');
  if (!input) return;

  input.addEventListener('input', function() {
    var val = this.value;
    var atPos = val.lastIndexOf('@');
    if (atPos === -1) { hideMentionList(); return; }
    var afterAt = val.substring(atPos + 1);
    if (afterAt.indexOf(' ') !== -1) { hideMentionList(); return; }
    mentionSelectedIndex = 0;
    showMentionList(afterAt);
  });

  input.addEventListener('keydown', function(e) {
    if (!mentionVisible) return;
    var items = mentionDropdown ? mentionDropdown.querySelectorAll('.mention-item') : [];
    if (items.length === 0) return;

    if (e.key === 'ArrowDown') {
      e.preventDefault();
      mentionSelectedIndex = Math.min(mentionSelectedIndex + 1, items.length - 1);
      updateMentionHighlight(items);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      mentionSelectedIndex = Math.max(mentionSelectedIndex - 1, 0);
      updateMentionHighlight(items);
    } else if (e.key === 'Enter' && mentionVisible) {
      e.preventDefault();
      var active = items[mentionSelectedIndex];
      if (active) selectMention(active.getAttribute('data-name'));
    } else if (e.key === 'Escape') {
      hideMentionList();
    }
  });

  input.addEventListener('blur', function() {
    setTimeout(hideMentionList, 200);
  });
}

function updateMentionHighlight(items) {
  for (var i = 0; i < items.length; i++) {
    if (i === mentionSelectedIndex) {
      items[i].classList.add('active');
    } else {
      items[i].classList.remove('active');
    }
  }
}

// ===== 语法高亮 =====
var KEYWORDS_JS = 'abstract arguments async await boolean break byte case catch char class const continue debugger default delete do double else enum eval export extends final finally float for from function goto if implements import in instanceof int interface let long native new of package private protected public return short static super switch synchronized this throw throws try typeof var void volatile while with yield async await'.split(' ');

var KEYWORDS_PYTHON = 'and as assert break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield True False None'.split(' ');

var KEYWORDS_HTML = 'DOCTYPE html head body div span p a h1 h2 h3 h4 h5 h6 ul ol li table tr td th form input button select option textarea label img link meta script style header footer nav main section article aside'.split(' ');

var KEYWORDS_CSS = 'color background font border margin padding display position width height top left right bottom flex grid align justify transform transition animation opacity overflow z-index box-shadow text-align'.split(' ');

function highlightCode(code, lang) {
  if (!code) return '';
  lang = (lang || '').toLowerCase();

  // 先用占位符保护已匹配的token，最后替换回来
  var tokens = [];
  var idx = 0;

  function saveToken(html) {
    var placeholder = '\x00T' + idx + '\x00';
    tokens.push({ placeholder: placeholder, html: html });
    idx++;
    return placeholder;
  }

  var result = code;

  // 1. 多行注释 /* ... */
  result = result.replace(/\/\*[\s\S]*?\*\//g, function(m) {
    return saveToken('<span class="hl-comment">' + m + '</span>');
  });

  // 2. 单行注释 // ...
  result = result.replace(/\/\/[^\n]*/g, function(m) {
    return saveToken('<span class="hl-comment">' + m + '</span>');
  });

  // 3. Python 注释 # ...
  result = result.replace(/#[^\n]*/g, function(m) {
    return saveToken('<span class="hl-comment">' + m + '</span>');
  });

  // 4. HTML 标签注释 <!-- ... -->
  result = result.replace(/&lt;!--[\s\S]*?--&gt;/g, function(m) {
    return saveToken('<span class="hl-comment">' + m + '</span>');
  });

  // 5. 双引号字符串
  result = result.replace(/"(?:[^"\\]|\\.)*"/g, function(m) {
    return saveToken('<span class="hl-string">' + m + '</span>');
  });

  // 6. 单引号字符串
  result = result.replace(/'(?:[^'\\]|\\.)*'/g, function(m) {
    return saveToken('<span class="hl-string">' + m + '</span>');
  });

  // 7. 模板字符串（简单处理）
  result = result.replace(/`(?:[^`\\]|\\.)*`/g, function(m) {
    return saveToken('<span class="hl-string">' + m + '</span>');
  });

  // 8. 数字
  result = result.replace(/\b(\d+\.?\d*)\b/g, function(m) {
    return saveToken('<span class="hl-number">' + m + '</span>');
  });

  // 9. 关键字
  var keywords = KEYWORDS_JS;
  if (lang === 'python' || lang === 'py') keywords = KEYWORDS_PYTHON;
  else if (lang === 'html' || lang === 'xml') keywords = KEYWORDS_HTML;
  else if (lang === 'css') keywords = KEYWORDS_CSS;

  for (var i = 0; i < keywords.length; i++) {
    var kw = keywords[i];
    var kwRegex = new RegExp('\\b(' + kw + ')\\b', 'g');
    result = result.replace(kwRegex, function(m) {
      return saveToken('<span class="hl-keyword">' + m + '</span>');
    });
  }

  // 10. 函数名（后面跟括号的单词）
  result = result.replace(/\b([a-zA-Z_]\w*)\s*\(/g, function(m, name) {
    return saveToken('<span class="hl-func">' + name + '</span>') + '(';
  });

  // 11. 布尔值和特殊值
  result = result.replace(/\b(true|false|null|undefined|None|True|False|NaN|Infinity)\b/g, function(m) {
    return saveToken('<span class="hl-special">' + m + '</span>');
  });

  // 把所有占位符替换回实际HTML
  for (var j = 0; j < tokens.length; j++) {
    result = result.replace(tokens[j].placeholder, tokens[j].html);
  }

  return result;
}

// ===== 消息搜索UI =====
var searchPanel = null;
var searchInput = null;
var searchResults = null;

function createSearchPanel() {
  if (searchPanel) return;
  searchPanel = document.createElement('div');
  searchPanel.className = 'search-panel';
  searchPanel.innerHTML = '<div class="search-panel-header">'
    + '<span class="search-panel-title">搜索消息</span>'
    + '<button class="search-panel-close" onclick="closeSearchPanel()">✕</button>'
    + '</div>'
    + '<div class="search-panel-input-wrap">'
    + '<input type="text" class="search-panel-input" placeholder="输入关键词搜索所有聊天..." />'
    + '</div>'
    + '<div class="search-panel-results"></div>';
  document.body.appendChild(searchPanel);

  searchInput = searchPanel.querySelector('.search-panel-input');
  searchResults = searchPanel.querySelector('.search-panel-results');

  searchInput.addEventListener('input', function() {
    var keyword = this.value.trim();
    if (!keyword) {
      searchResults.innerHTML = '<div class="search-empty">输入关键词开始搜索</div>';
      return;
    }
    var results = searchMessages(keyword);
    renderSearchResults(results, keyword);
  });

  searchInput.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeSearchPanel();
  });
}

function openSearchPanel() {
  createSearchPanel();
  searchPanel.classList.add('open');
  searchResults.innerHTML = '<div class="search-empty">输入关键词开始搜索</div>';
  setTimeout(function() { searchInput.focus(); }, 100);
}

function closeSearchPanel() {
  if (searchPanel) searchPanel.classList.remove('open');
}

function renderSearchResults(results, keyword) {
  if (!searchResults) return;
  if (results.length === 0) {
    searchResults.innerHTML = '<div class="search-empty">没有找到匹配的消息</div>';
    return;
  }

  var html = '<div class="search-count">找到 ' + results.length + ' 条结果</div>';
  for (var i = 0; i < results.length; i++) {
    var r = results[i];
    var snippet = getSnippet(r.text, r.matchIndex, keyword, 40);
    var highlighted = highlightKeyword(snippet, keyword);

    if (r.type === 'single') {
      html += '<div class="search-result-item" onclick="jumpToSingle(\'' + r.empId + '\')">'
        + '<div class="search-result-avatar">' + getAvatarEmoji(r.empId) + '</div>'
        + '<div class="search-result-info">'
        + '<div class="search-result-header">'
        + '<span class="search-result-name">' + escapeHtml(r.empName) + '</span>'
        + '<span class="search-result-time">' + escapeHtml(r.time || '') + '</span>'
        + '</div>'
        + '<div class="search-result-text">' + highlighted + '</div>'
        + '</div>'
        + '</div>';
    } else {
      html += '<div class="search-result-item" onclick="jumpToGroup(\'' + r.groupId + '\')">'
        + '<div class="search-result-avatar">👥</div>'
        + '<div class="search-result-info">'
        + '<div class="search-result-header">'
        + '<span class="search-result-name">' + escapeHtml(r.groupName) + ' · ' + escapeHtml(r.sender || '') + '</span>'
        + '<span class="search-result-time">' + escapeHtml(r.time || '') + '</span>'
        + '</div>'
        + '<div class="search-result-text">' + highlighted + '</div>'
        + '</div>'
        + '</div>';
    }
  }
  searchResults.innerHTML = html;
}

function getSnippet(text, matchIndex, keyword, contextLen) {
  if (!text) return '';
  var start = Math.max(0, matchIndex - contextLen);
  var end = Math.min(text.length, matchIndex + keyword.length + contextLen);
  var snippet = '';
  if (start > 0) snippet += '...';
  snippet += text.substring(start, end);
  if (end < text.length) snippet += '...';
  return snippet;
}

function jumpToSingle(empId) {
  closeSearchPanel();
  var item = document.querySelector('.list-item[data-id="' + empId + '"]');
  if (item) item.click();
}

function jumpToGroup(groupId) {
  closeSearchPanel();
  var item = document.querySelector('.list-item[data-proj="' + groupId + '"]');
  if (item) item.click();
}

function bindSearchShortcut() {
  document.addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      if (searchPanel && searchPanel.classList.contains('open')) {
        closeSearchPanel();
      } else {
        openSearchPanel();
      }
    }
    if (e.key === 'Escape' && searchPanel && searchPanel.classList.contains('open')) {
      closeSearchPanel();
    }
  });
}

// ===== OpenClaw 连接 =====
var openclawAgents = {};  // { agentId: { name, role, emoji, sessionKey } }
var openclawConnected = false;

function connectOpenClaw() {
  var config = window.OPENCLAW_CONFIG || OPENCLAW_CONFIG;
  if (!config || !config.token) {
    console.log('[OpenClaw] 未配置 token，跳过连接');
    showConnectionStatus('未配置', 'gray');
    return;
  }

  console.log('[OpenClaw] 开始连接:', config.url);
  showConnectionStatus('连接中...', 'orange');

  initOpenClaw(config.url, config.token)
    .then(function(client) {
      console.log('[OpenClaw] 连接成功！');
      openclawConnected = true;
      showConnectionStatus('已连接', 'green');

      // 获取 Agent 列表
      return client.agentsList();
    })
    .then(function(data) {
      console.log('[OpenClaw] Agent 列表:', data);
      if (data && data.agents) {
        updateEmployeeListFromAgents(data.agents);
      }

      // 订阅会话事件
      return openclawClient.sessionsSubscribe();
    })
    .then(function() {
      console.log('[OpenClaw] 已订阅会话事件');

      // 监听聊天事件
      openclawClient.on('chat', function(payload) {
        handleOpenClawChatEvent(payload);
      });
    })
    .catch(function(err) {
      console.error('[OpenClaw] 连接失败:', err);
      console.error('[OpenClaw] 错误详情:', err.message || err);
      showConnectionStatus('连接失败', 'red');
    });
}

function showConnectionStatus(text, color) {
  var statusEl = document.getElementById('connectionStatus');
  if (!statusEl) {
    // 创建状态指示器
    statusEl = document.createElement('div');
    statusEl.id = 'connectionStatus';
    statusEl.style.cssText = 'position:fixed;top:8px;right:8px;padding:4px 10px;border-radius:12px;font-size:11px;font-weight:600;z-index:1000;transition:all 0.3s;';
    document.body.appendChild(statusEl);
  }
  statusEl.textContent = '● ' + text;
  statusEl.style.color = '#fff';
  if (color === 'green') statusEl.style.background = '#34c759';
  else if (color === 'orange') statusEl.style.background = '#ff9500';
  else if (color === 'red') statusEl.style.background = '#ff3b30';
  else statusEl.style.background = '#8e8e93';
}

function updateEmployeeListFromAgents(agents) {
  // 清空现有映射
  openclawAgents = {};

  for (var i = 0; i < agents.length; i++) {
    var agent = agents[i];
    var id = agent.id;
    var name = id.charAt(0).toUpperCase() + id.slice(1);
    if (id === 'main') name = 'Lucy';

    openclawAgents[id] = {
      name: name,
      role: agent.model ? (agent.model.primary || 'AI Agent') : 'AI Agent',
      emoji: getEmojiForAgent(id),
      sessionKey: 'agent:' + id + ':main'
    };

    // 同步到 EMPLOYEE_PROMPTS
    EMPLOYEE_PROMPTS[id] = {
      name: name,
      role: openclawAgents[id].role,
      system: '你是 ' + name + '，OpenClaw Agent。用中文回答。'
    };

    // 同步到 AVATAR_MAP
    AVATAR_MAP[id] = openclawAgents[id].emoji;
  }

  console.log('[OpenClaw] 已更新员工列表:', Object.keys(openclawAgents));

  // 重建侧栏员工列表
  rebuildSidebarAgents();
}

function getEmojiForAgent(id) {
  var map = {
    'main': '👩‍💻',
    'pi': '🥧',
    'emily': '🦞',
    'grace': '👨‍💼',
    'gates': '🐠',
    'eric': '🐱',
    'olivia': '🐺',
    'summer': '🐰'
  };
  return map[id] || '🤖';
}

function rebuildSidebarAgents() {
  // 找到员工列表容器（sub-group-content）
  var container = document.querySelector('.sub-group-content');
  if (!container) {
    console.log('[OpenClaw] 找不到员工列表容器');
    return;
  }

  // 清空现有员工列表
  container.innerHTML = '';

  // 按顺序添加每个 Agent
  var agentIds = Object.keys(openclawAgents);
  for (var i = 0; i < agentIds.length; i++) {
    var id = agentIds[i];
    var agent = openclawAgents[id];

    var item = document.createElement('div');
    item.className = 'list-item' + (i === 0 ? ' active' : '');
    item.setAttribute('data-id', id);
    item.innerHTML =
      '<div class="avatar" style="background:linear-gradient(135deg,#FF6B35,#E55A2B);">' + agent.emoji + '</div>' +
      '<div class="emp-info">' +
        '<div class="emp-top"><span class="emp-name">' + agent.name + '</span><span class="emp-role">' + agent.role + '</span></div>' +
        '<div class="emp-preview">点击开始对话</div>' +
      '</div>' +
      '<div class="item-actions">' +
        '<button class="emp-action-btn" onclick="showGearMenu(event, this)" title="设置">⚙</button>' +
      '</div>';

    // 绑定点击事件
    item.addEventListener('click', function() {
      var empId = this.getAttribute('data-id');
      if (!empId) return;

      // 更新激活状态
      var items = container.querySelectorAll('.list-item');
      for (var j = 0; j < items.length; j++) {
        items[j].classList.remove('active');
      }
      this.classList.add('active');

      // 更新聊天头部
      updateChatHeader(empId);

      // 切换消息历史
      currentGroupId = null;
      var msgs = getMessages(empId);
      if (msgs.length === 0) {
        var prompt = EMPLOYEE_PROMPTS[empId];
        if (prompt) {
          var welcome = '你好！我是' + prompt.name + '，' + prompt.role + '。有什么可以帮你的？';
          addMessage(empId, 'assistant', welcome, getTimeStr());
        }
      }
      renderMessages(empId);
    });

    container.appendChild(item);
  }

  console.log('[OpenClaw] 侧栏已重建，' + agentIds.length + ' 个员工');
}

function handleOpenClawChatEvent(payload) {
  console.log('[OpenClaw] 聊天事件:', payload);
  if (!payload || !payload.sessionKey) return;

  // 流式增量和完整消息由 sendOpenClawMessage 中的监听器处理
  // 这里只处理其他类型的事件（如系统消息）
  if (payload.message && payload.message.role === 'system') {
    var sessionKey = payload.sessionKey;
    var agentId = sessionKey.replace('agent:', '').replace(':main', '');
    var text = extractTextFromContent(payload.message.content);
    addMessage(agentId, 'assistant', '[系统] ' + text, getTimeStr());
    renderMessages(agentId);
  }
}

function appendOpenClawMessage(agentId, delta) {
  // 找到或创建当前流式消息气泡
  var area = document.getElementById('messagesArea');
  if (!area) return;

  var bubble = area.querySelector('.msg.streaming[data-agent="' + agentId + '"]');
  if (!bubble) {
    // 创建新的流式气泡
    var prompt = EMPLOYEE_PROMPTS[agentId] || { name: agentId, role: '' };
    var avatar = AVATAR_MAP[agentId] || '🤖';
    bubble = document.createElement('div');
    bubble.className = 'msg streaming';
    bubble.setAttribute('data-agent', agentId);
    bubble.innerHTML = '<div class="msg-avatar"><div class="avatar small" style="background:linear-gradient(135deg,#FF6B35,#E55A2B);">' + avatar + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + prompt.name + '</span><span class="msg-sender-role">' + prompt.role + '</span><span class="msg-sender-time">' + getTimeStr() + '</span></div><div class="msg-bubble streaming"><span class="bubble-text"></span></div></div>';
    area.appendChild(bubble);
    area.scrollTop = area.scrollHeight;
  }

  var textSpan = bubble.querySelector('.bubble-text');
  if (textSpan) {
    textSpan.textContent += delta;
    area.scrollTop = area.scrollHeight;
  }
}

function extractTextFromContent(content) {
  if (!content) return '';
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    var result = '';
    for (var i = 0; i < content.length; i++) {
      var item = content[i];
      if (item.type === 'text') {
        result += item.text;
      } else if (item.type === 'thinking') {
        result += '[思考] ' + item.thinking;
      }
    }
    return result;
  }
  return JSON.stringify(content);
}
