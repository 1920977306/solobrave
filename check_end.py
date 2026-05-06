# -*- coding: utf-8 -*-
with open('js/ui-chat-simple.js','r',encoding='utf-8') as f:
    lines = f.readlines()
    for i in range(380, min(400, len(lines))):
        print(f'{i+1}: {repr(lines[i])}')