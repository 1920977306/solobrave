# -*- coding: utf-8 -*-
with open('js/app.js','r',encoding='utf-8') as f:
    lines = f.readlines()
    for i in range(60, min(75, len(lines))):
        print(f'{i+1}: {repr(lines[i])}')