# -*- coding: utf-8 -*-
import re

with open('js/ui-chat-simple.js','r',encoding='utf-8') as f:
    content = f.read()
    
# 查找 renderChat 的定义
matches = re.findall(r'(window\.)?renderChat\s*=\s*function', content)
print('renderChat 定义:', matches)
