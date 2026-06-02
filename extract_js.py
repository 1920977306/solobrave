import re
with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()
scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
js = '\n'.join(scripts)
with open('extracted.js','w',encoding='utf-8') as f:
    f.write(js)
print('提取完成，JS长度:', len(js))
