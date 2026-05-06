# -*- coding: utf-8 -*-
with open('js/app.js','r',encoding='utf-8') as f:
    lines = f.readlines()
    for i, line in enumerate(lines):
        if 'renderChat' in line:
            print(f'{i+1}: {repr(line)}')