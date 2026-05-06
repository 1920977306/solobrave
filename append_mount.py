# -*- coding: utf-8 -*-
with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    content = f.read()

append_code = """
// 延迟挂载到全局
setTimeout(function() {
  var ns = window.soloBrave = window.soloBrave || {};
  ns.ui = ns.ui || {};
  if (typeof renderChat === 'function') ns.ui.renderChat = renderChat;
  if (typeof renderEmployeeList === 'function') ns.ui.renderEmployeeList = renderEmployeeList;
  if (typeof doSend === 'function') ns.ui.doSend = doSend;
  console.log('ui-chat-simple 挂载完成:', typeof ns.ui.renderChat);
}, 0);
"""

with open('js/ui-chat-simple.js', 'w', encoding='utf-8') as f:
    f.write(content + append_code)

print('追加完成')
