# -*- coding: utf-8 -*-
with open('js/ui-chat-simple.js','r',encoding='utf-8') as f:
    content = f.read()

# 修复第 389 行：window.soloBrave.ui.window.renderChat -> window.soloBrave.ui.renderChat
content = content.replace('window.soloBrave.ui.window.renderChat', 'window.soloBrave.ui.renderChat')

# 修复第 480 行：typeof renderChat -> typeof window.renderChat
content = content.replace("if (typeof renderChat === 'function') ns.ui.renderChat = renderChat;", 
                          "if (typeof window.renderChat === 'function') ns.ui.renderChat = window.renderChat;")

with open('js/ui-chat-simple.js','w',encoding='utf-8') as f:
    f.write(content)

print('修复完成')
