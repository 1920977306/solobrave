# -*- coding: utf-8 -*-
import re

with open('js/ui-chat-simple.js','r',encoding='utf-8') as f:
    content = f.read()

# 查找函数定义方式
funcs = re.findall(r'(window\.)?(\w+)\s*=\s*function', content)
print('函数定义方式:')
for match in funcs[:10]:
    prefix = 'window.' if match[0] else ''
    print(f'  {prefix}{match[1]} = function')
