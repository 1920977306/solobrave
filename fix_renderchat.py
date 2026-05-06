# -*- coding: utf-8 -*-
with open('js/ui-chat-simple.js','r',encoding='utf-8') as f:
    content = f.read()

# 把局部变量 renderChat 改成 window.renderChat
# 找到 "renderChat = function" 并改成 "window.renderChat = function"
content = content.replace('renderChat = function', 'window.renderChat = function')

with open('js/ui-chat-simple.js','w',encoding='utf-8') as f:
    f.write(content)

print('修复完成')
