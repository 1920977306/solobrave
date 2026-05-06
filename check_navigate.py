# -*- coding: utf-8 -*-
with open('js/app.js','r',encoding='utf-8') as f:
    content = f.read()
    if 'ns.navigate' in content:
        print('app.js 包含 ns.navigate')
    else:
        print('app.js 不包含 ns.navigate')
