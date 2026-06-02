import re
with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

for m in re.finditer(r'\(el, i\)\s*=>', content):
    line = content[:m.start()].count('\n') + 1
    start = max(0, m.start()-50)
    end = min(len(content), m.end()+50)
    snippet = content[start:end]
    safe = snippet.encode('ascii','replace').decode('ascii')
    print(f'HTML L{line}: {safe}')
