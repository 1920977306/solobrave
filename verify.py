import re
with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

print('File size:', len(content))
print('Lines:', content.count(chr(10)))

arrows = re.findall(r'\)=\u003e', content)
print('Arrows remaining:', len(arrows))

consts = re.findall(r'\bconst\s+', content)
lets = re.findall(r'\blet\s+', content)
print('const remaining:', len(consts))
print('let remaining:', len(lets))

backticks = [m.start() for m in re.finditer(r'`', content)]
print('Backtick count:', len(backticks))
