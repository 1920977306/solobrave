// ===== 消息历史（按员工ID隔离） =====
var chatMessages = {};

function getCurrentEmpId() {
  var info = getCurrentEmployeeInfo ? getCurrentEmployeeInfo() : null;
  return info ? info.id : 'lucy';
}

function getMessages(empId) {
  if (!chatMessages[empId]) chatMessages[empId] = [];
  return chatMessages[empId];
}

function addMessage(empId, role, text, time) {
  var msgs = getMessages(empId);
  msgs.push({ role: role, text: text, time: time });
  if (msgs.length > 50) msgs.splice(0, msgs.length - 50);
  saveChatData();
}

function getTimeStr() {
  var now = new Date();
  var h = now.getHours().toString();
  var m = now.getMinutes().toString();
  if (h.length < 2) h = '0' + h;
  if (m.length < 2) m = '0' + m;
  return h + ':' + m;
}

// ===== HTML转义 =====
function escapeHtml(text) {
  var div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// ===== 构建消息气泡 =====
function buildUserBubble(text, time) {
  return '<div class="msg own"><div class="msg-content"><div class="msg-sender"><span class="msg-sender-time">' + (time || getTimeStr()) + '</span><span class="msg-sender-name">你</span></div><div class="msg-bubble">' + escapeHtml(text) + '</div></div></div>';
}

function buildAIBubble(empId, text, time) {
  var prompt = EMPLOYEE_PROMPTS[empId] || { name: empId, role: '' };
  var avatar = AVATAR_MAP[empId] || '🤖';
  return '<div class="msg" data-sender="' + empId + '"><div class="msg-avatar"><div class="avatar small" style="background:linear-gradient(135deg,#FF6B35,#E55A2B);">' + avatar + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + prompt.name + '</span><span class="msg-sender-role">' + prompt.role + '</span><span class="msg-sender-time">' + (time || getTimeStr()) + '</span></div><div class="msg-bubble">' + formatMessage(text) + '</div></div></div>';
}

// ===== 渲染消息 =====
function renderMessages(empId) {
  var area = document.getElementById('messagesArea');
  if (!area) return;
  area.innerHTML = '';
  var msgs = getMessages(empId);
  for (var i = 0; i < msgs.length; i++) {
    var m = msgs[i];
    if (m.role === 'user') {
      area.innerHTML += buildUserBubble(m.text, m.time);
    } else {
      area.innerHTML += buildAIBubble(empId, m.text, m.time);
    }
  }
  area.scrollTop = area.scrollHeight;
}

// ===== 发送消息 =====
async function sendMessage() {
  var input = document.getElementById('msgInput');
  if (!input) return;
  var text = input.value.trim();
  if (!text) return;

  var empId = getCurrentEmpId();
  var time = getTimeStr();

  // 1. 显示用户消息
  var area = document.getElementById('messagesArea');
  if (area) {
    area.innerHTML += buildUserBubble(text, time);
    area.scrollTop = area.scrollHeight;
  }
  addMessage(empId, 'user', text, time);
  input.value = '';

  // 2. 创建AI消息容器
  var prompt = EMPLOYEE_PROMPTS[empId] || { name: empId, role: '' };
  var avatar = AVATAR_MAP[empId] || '🤖';
  var aiDiv = document.createElement('div');
  aiDiv.className = 'msg';
  aiDiv.setAttribute('data-sender', empId);
  aiDiv.innerHTML = '<div class="msg-avatar"><div class="avatar small" style="background:linear-gradient(135deg,#FF6B35,#E55A2B);">' + avatar + '</div></div><div class="msg-content"><div class="msg-sender"><span class="msg-sender-name">' + prompt.name + '</span><span class="msg-sender-role">' + prompt.role + '</span><span class="msg-sender-time">' + time + '</span></div><div class="msg-bubble streaming"></div></div>';
  if (area) {
    area.appendChild(aiDiv);
    area.scrollTop = area.scrollHeight;
  }

  var bubble = aiDiv.querySelector('.msg-bubble');
  var fullText = '';
  var streamId = 's' + Date.now();
  if (aiDiv) aiDiv.setAttribute('data-stream-id', streamId);

  // 3. 优先使用 OpenClaw
  if (openclawConnected && openclawClient && openclawAgents[empId]) {
    await sendOpenClawMessage(empId, text, bubble, area);
    return;
  }

  // 4. 备用：智谱API
  var msgs = getMessages(empId);
  var apiMessages = [{ role: 'system', content: prompt.system }];
  for (var i = 0; i < msgs.length; i++) {
    apiMessages.push({ role: msgs[i].role, content: msgs[i].text });
  }

  await callZhipuAPI(apiMessages,
    function(chunk) {
      fullText += chunk;
      if (bubble) {
        bubble.innerHTML = formatMessage(fullText) + '<span class="cursor"></span>';
      }
      if (area) area.scrollTop = area.scrollHeight;
    },
    function() {
      if (bubble) {
        bubble.classList.remove('streaming');
        bubble.innerHTML = formatMessage(fullText);
      }
      addMessage(empId, 'assistant', fullText, getTimeStr());
    },
    function(err) {
      if (bubble) {
        bubble.classList.remove('streaming');
        bubble.innerHTML = '⚠️ 请求失败：' + err.message;
      }
    }
  );
}

// ===== 群聊消息管理 =====
function getGroupMsgs(groupId) {
  if (!groupMessages[groupId]) groupMessages[groupId] = [];
  return groupMessages[groupId];
}

function addGroupMsg(groupId, role, sender, text, time) {
  var msgs = getGroupMsgs(groupId);
  msgs.push({ role: role, sender: sender, text: text, time: time });
  saveChatData();
}

function parseMentions(text) {
  var regex = /@(\w+)/g;
  var result = [];
  var match;
  while ((match = regex.exec(text)) !== null) {
    var name = match[1].toLowerCase();
    var id = nameToId(name);
    if (id && EMPLOYEE_PROMPTS[id]) {
      result.push(id);
    }
  }
  return result;
}

function nameToId(name) {
  var map = {
    'lucy': 'xlcx', 'emily': 'emily', 'grace': 'grace',
    'cynthia': 'cynthia', 'gates': 'gates', 'eric': 'eric',
    'olivia': 'olivia', 'summer': 'summer'
  };
  return map[name] || name;
}

function sendGroupMessage(groupId, text) {
  var time = getTimeStr();
  addGroupMsg(groupId, 'user', '你', text, time);
  renderGroupMessages(groupId);

  var mentioned = parseMentions(text);
  if (mentioned.length === 0) return;

  var group = GROUP_CHATS[groupId];
  var i = 0;

  function nextReply() {
    if (i >= mentioned.length) return;
    var empId = mentioned[i];
    i++;

    var prompt = EMPLOYEE_PROMPTS[empId];
    var bubble = createGroupAIBubble(groupId, empId);

    var context = getGroupMsgs(groupId).slice(-10);
    var messages = [
      { role: 'system', content: prompt.system + '\n你正在群聊【' + group.name + '】中，请简洁回复。' }
    ];
    for (var j = 0; j < context.length; j++) {
      messages.push({
        role: context[j].role === 'user' ? 'user' : 'assistant',
        content: context[j].sender + ': ' + context[j].text
      });
    }

    callZhipuAPI(messages,
      function(chunk) { appendToBubble(bubble, chunk); },
      function() {
        finalizeBubble(bubble);
        var aiText = getBubbleText(bubble);
        addGroupMsg(groupId, 'assistant', prompt.name, aiText, getTimeStr());
        nextReply();
      },
      function(err) { setBubbleText(bubble, '请求失败'); nextReply(); }
    );
  }

  nextReply();
}

// ===== localStorage 持久化 =====
var STORAGE_KEY = 'solobrave_chats';

function saveChatData() {
  try {
    var data = {
      single: chatMessages,
      group: groupMessages,
      savedAt: new Date().toISOString()
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(data));
  } catch (e) {
    // localStorage 满了或不可用，静默失败
  }
}

function loadChatData() {
  try {
    var raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    var data = JSON.parse(raw);
    if (data.single) chatMessages = data.single;
    if (data.group) groupMessages = data.group;

    // 数据迁移：旧静态ID -> 新agent ID
    migrateChatDataIfNeeded();
  } catch (e) {
    // 数据损坏，忽略
  }
}

function migrateChatDataIfNeeded() {
  var idMap = {
    'xlcx': 'main',
    'emily': 'emily',
    'grace': 'grace',
    'gates': 'gates',
    'eric': 'eric',
    'olivia': 'olivia',
    'summer': 'summer'
  };

  var hasMigration = false;
  for (var oldId in idMap) {
    var newId = idMap[oldId];
    if (oldId !== newId && chatMessages[oldId]) {
      // 合并旧数据到新ID（如果新ID已有数据则追加）
      if (!chatMessages[newId]) {
        chatMessages[newId] = [];
      }
      // 把旧数据追加到新数组
      var oldMsgs = chatMessages[oldId];
      for (var i = 0; i < oldMsgs.length; i++) {
        chatMessages[newId].push(oldMsgs[i]);
      }
      delete chatMessages[oldId];
      hasMigration = true;
      console.log('[Chat] 迁移聊天记录:', oldId, '->', newId, '(' + oldMsgs.length + '条)');
    }
  }

  if (hasMigration) {
    saveChatData();
  }
}

function clearChatData() {
  localStorage.removeItem(STORAGE_KEY);
  chatMessages = {};
  groupMessages = {};
}

// ===== 消息搜索 =====
function searchMessages(keyword) {
  var results = [];
  if (!keyword || !keyword.trim()) return results;
  var kw = keyword.toLowerCase().trim();

  // 搜索单聊
  var empIds = Object.keys(chatMessages);
  for (var i = 0; i < empIds.length; i++) {
    var empId = empIds[i];
    var msgs = chatMessages[empId];
    if (!msgs) continue;
    for (var j = 0; j < msgs.length; j++) {
      var m = msgs[j];
      if (m.text && m.text.toLowerCase().indexOf(kw) !== -1) {
        var prompt = EMPLOYEE_PROMPTS[empId];
        results.push({
          type: 'single',
          empId: empId,
          empName: prompt ? prompt.name : empId,
          role: m.role,
          text: m.text,
          time: m.time,
          matchIndex: m.text.toLowerCase().indexOf(kw)
        });
      }
    }
  }

  // 搜索群聊
  var groupIds = Object.keys(groupMessages);
  for (var k = 0; k < groupIds.length; k++) {
    var gId = groupIds[k];
    var gMsgs = groupMessages[gId];
    if (!gMsgs) continue;
    var group = GROUP_CHATS[gId];
    for (var l = 0; l < gMsgs.length; l++) {
      var gm = gMsgs[l];
      if (gm.text && gm.text.toLowerCase().indexOf(kw) !== -1) {
        results.push({
          type: 'group',
          groupId: gId,
          groupName: group ? group.name : gId,
          sender: gm.sender,
          role: gm.role,
          text: gm.text,
          time: gm.time,
          matchIndex: gm.text.toLowerCase().indexOf(kw)
        });
      }
    }
  }

  return results;
}

function highlightKeyword(text, keyword) {
  if (!text || !keyword) return escapeHtml(text || '');
  var escaped = escapeHtml(text);
  var kw = escapeHtml(keyword);
  var regex = new RegExp('(' + kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return escaped.replace(regex, '<mark class="search-highlight">$1</mark>');
}

// ===== OpenClaw 消息收发 =====
async function sendOpenClawMessage(empId, text, bubble, area) {
  if (!openclawClient || !openclawAgents[empId]) return;

  var agent = openclawAgents[empId];
  var sessionKey = agent.sessionKey;
  var fullText = '';
  var isStreaming = false;

  try {
    // 发送消息
    await openclawClient.chatSend(sessionKey, text);

    // 设置流式回复监听
    var messageHandler = function(payload) {
      if (!payload || payload.sessionKey !== sessionKey) return;

      if (payload.delta) {
        // 流式增量内容
        isStreaming = true;
        fullText += payload.delta;
        if (bubble) {
          bubble.innerHTML = formatMessage(fullText) + '<span class="cursor"></span>';
        }
        if (area) area.scrollTop = area.scrollHeight;
      }

      if (payload.done || (payload.message && payload.message.role === 'assistant')) {
        // 流结束或完整消息
        if (bubble) {
          bubble.classList.remove('streaming');
          bubble.innerHTML = formatMessage(fullText);
        }
        if (fullText) {
          addMessage(empId, 'assistant', fullText, getTimeStr());
        }
        // 移除监听
        openclawClient.off('chat', messageHandler);
      }
    };

    openclawClient.on('chat', messageHandler);

    // 超时处理（30秒）
    setTimeout(function() {
      if (isStreaming && fullText) {
        // 有内容但可能没收到 done，保存已有内容
        if (bubble) {
          bubble.classList.remove('streaming');
          bubble.innerHTML = formatMessage(fullText);
        }
        addMessage(empId, 'assistant', fullText, getTimeStr());
        openclawClient.off('chat', messageHandler);
      } else if (!isStreaming) {
        // 完全没有收到回复
        if (bubble) {
          bubble.classList.remove('streaming');
          bubble.innerHTML = '⚠️ 等待回复超时';
        }
        openclawClient.off('chat', messageHandler);
      }
    }, 30000);

  } catch (err) {
    console.error('[OpenClaw] 发送失败:', err);
    if (bubble) {
      bubble.classList.remove('streaming');
      bubble.innerHTML = '⚠️ OpenClaw 发送失败：' + (err.message || '未知错误');
    }
  }
}
