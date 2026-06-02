with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

for i, c in enumerate(content):
    if c == '`':
        line = content[:i].count('\n') + 1
        col = i - content.rfind('\n', 0, i)
        start = max(0, i-30)
        end = min(len(content), i+30)
        snippet = content[start:end]
        safe = snippet.encode('ascii','replace').decode('ascii')
        print(f'HTML L{line}:{col} : {safe}')
