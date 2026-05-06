/**
 * SoloBrave 鑱婂ぉ椤?UI - 绠€鍖栧畬鏁寸増
 */

(function() {
  const Store = window.SoloBraveStore;
  const AI = window.SoloBraveAI;
  
  let currentEmployee = null;
  let isTyping = false;
  
  // 娓叉煋鍛樺伐鍒楄〃
  window.renderEmployeeList = function() {
    const list = document.getElementById('employee-list');
    if (!list) return;
    
    const employees = Store.get('employees') || [];
    
    var html = '';
    for (var i = 0; i < employees.length; i++) {
      var emp = employees[i];
      var activeClass = (currentEmployee && currentEmployee.id === emp.id) ? 'active' : '';
      html += '<div class="employee-card ' + activeClass + '" onclick="selectEmployee(\'' + emp.id + '\')" data-employee-id="' + emp.id + '">';
      html += '<div class="employee-avatar" style="background: linear-gradient(135deg, ' + emp.gradient[0] + ' 0%, ' + emp.gradient[1] + ' 100%)">';
      html += emp.avatar;
      html += '<span class="status-ring ' + emp.status + '"></span>';
      html += '</div>';
      html += '<div class="card-info">';
      html += '<div class="card-name">' + emp.name + '</div>';
      html += '<div class="card-role">' + emp.role + '</div>';
      html += '</div>';
      html += '<div class="card-status">';
      if (emp.status === 'online') {
        html += '<span>鍦ㄧ嚎</span>';
      }
      html += '</div>';
      html += '</div>';
    }
    
    list.innerHTML = html;
  };
  
  // 閫夋嫨鍛樺伐
  window.selectEmployee = function(employeeId) {
    var employees = Store.get('employees') || [];
    for (var i = 0; i < employees.length; i++) {
      if (employees[i].id === employeeId) {
        currentEmployee = employees[i];
        break;
      }
    }
    
    var cards = document.querySelectorAll('.employee-card');
    for (var j = 0; j < cards.length; j++) {
      if (cards[j].dataset.employeeId === employeeId) {
        cards[j].classList.add('active');
      } else {
        cards[j].classList.remove('active');
      }
    }
    
    renderChatHeader();
    renderMessages();
  };
  
  // 娓叉煋鑱婂ぉ澶撮儴
  window.renderChatHeader = function() {
    var header = document.getElementById('chat-employee-info');
    if (!header) return;
    
    if (!currentEmployee) {
      header.innerHTML = '<span>璇烽€夋嫨涓€涓憳宸ュ紑濮嬪璇?/span>';
      return;
    }
    
    var html = '';
    html += '<div class="employee-avatar" style="background: linear-gradient(135deg, ' + currentEmployee.gradient[0] + ' 0%, ' + currentEmployee.gradient[1] + ' 100%)">';
    html += currentEmployee.avatar;
    html += '<span class="status-ring ' + currentEmployee.status + '"></span>';
    html += '</div>';
    html += '<div class="employee-details">';
    html += '<span class="employee-name">' + currentEmployee.name + '</span>';
    html += '<span class="employee-role">' + currentEmployee.role + ' 路 ' + currentEmployee.personality + '</span>';
    html += '</div>';
    
    header.innerHTML = html;
  };
  
  // 娓叉煋娑堟伅
  window.renderMessages = function() {
    var container = document.getElementById('chat-messages');
    if (!container) return;
    
    if (!currentEmployee) {
      container.innerHTML = renderWelcomeScreen();
      return;
    }
    
    var messages = Store.getConversation(currentEmployee.id) || [];
    
    if (messages.length === 0) {
      container.innerHTML = renderWelcomeScreen();
      return;
    }
    
    var html = '';
    for (var i = 0; i < messages.length; i++) {
      var msg = messages[i];
      var isUser = msg.role === 'user';
      var avatarBg = isUser ? '#94a3b8' : currentEmployee.gradient[0];
      var avatarBg2 = isUser ? '#64748b' : currentEmployee.gradient[1];
      var avatarText = isUser ? '鎴? : currentEmployee.avatar;
      var nameText = isUser ? '鎴? : currentEmployee.name;
      
      html += '<div class="message-group">';
      html += '<div class="message-header">';
      html += '<div class="employee-avatar avatar" style="background: linear-gradient(135deg, ' + avatarBg + ' 0%, ' + avatarBg2 + ' 100%)">';
      html += avatarText;
      html += '</div>';
      html += '<span class="name">' + nameText + '</span>';
      html += '<span class="time">' + formatTime(msg.timestamp) + '</span>';
      html += '</div>';
      html += '<div class="message-bubble ' + msg.role + '">';
      html += escapeHtml(msg.content);
      html += '</div>';
      html += '</div>';
    }
    
    container.innerHTML = html;
    container.scrollTop = container.scrollHeight;
  };
  
  // 娓叉煋娆㈣繋鐣岄潰
  function renderWelcomeScreen() {
    if (!currentEmployee) {
      return '<div class="empty-state"><div class="empty-icon">馃</div><div class="empty-title">娆㈣繋鏉ュ埌 SoloBrave Office</div><div class="empty-desc">浠庡乏渚ч€夋嫨涓€涓?AI 鍛樺伐锛屽紑濮嬩綘鐨勯珮鏁堝崗浣滀箣鏃?/div></div>';
    }
    
    var html = '';
    html += '<div class="welcome-screen">';
    html += '<div class="employee-avatar welcome-avatar" style="background: linear-gradient(135deg, ' + currentEmployee.gradient[0] + ' 0%, ' + currentEmployee.gradient[1] + ' 100%)">';
    html += currentEmployee.avatar;
    html += '</div>';
    html += '<div class="welcome-title">' + currentEmployee.name + '</div>';
    html += '<div class="welcome-desc">鎴戞槸浣犵殑' + currentEmployee.role + '锛屾搮闀? + currentEmployee.skills.join('銆?) + '銆?br>鏈変粈涔堟垜鍙互甯綘鐨勫悧锛?/div>';
    html += '<div class="suggestion-chips">';
    html += '<button class="suggestion-chip" onclick="sendQuickMessage(\'甯垜鍒嗘瀽涓€涓嬪綋鍓嶉」鐩繘搴')">鍒嗘瀽椤圭洰杩涘害</button>';
    html += '<button class="suggestion-chip" onclick="sendQuickMessage(\'缁欐垜涓€浜涗骇鍝佷紭鍖栧缓璁甛')">浜у搧浼樺寲寤鸿</button>';
    html += '<button class="suggestion-chip" onclick="sendQuickMessage(\'甯垜鍐欎竴浠介渶姹傛枃妗')">鍐欓渶姹傛枃妗?/button>';
    html += '<button class="suggestion-chip" onclick="sendQuickMessage(\'鎬荤粨涓€涓嬩粖澶╃殑浠诲姟\')">鎬荤粨浠婃棩浠诲姟</button>';
    html += '</div>';
    html += '</div>';
    
    return html;
  }
  
  // 鍙戦€佹秷鎭?
  window.sendMessage = async function() {
    var input = document.getElementById('message-input');
    if (!input) return;
    
    var content = input.value.trim();
    if (!content || !currentEmployee || isTyping) return;
    
    Store.addMessage(currentEmployee.id, {
      role: 'user',
      content: content
    });
    
    input.value = '';
    renderMessages();
    
    showTypingIndicator();
    isTyping = true;
    
    try {
      var messages = Store.getConversation(currentEmployee.id);
      var aiContent = '';
      
      await AI.sendMessage(currentEmployee, messages, function(chunk, full) {
        aiContent = full;
        updateTypingContent(aiContent);
      });
      
      Store.addMessage(currentEmployee.id, {
        role: 'assistant',
        content: aiContent
      });
    } catch (error) {
      Store.addMessage(currentEmployee.id, {
        role
