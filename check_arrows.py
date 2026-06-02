import re
with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

arrow_matches = list(re.finditer(r'\)=\u003e', content))
print('arrow count:', len(arrow_matches))
for m in arrow_matches:
    line = content[:m.start()].count('\n') + 1
    start = max(0, m.start()-40)
    end = min(len(content), m.end()+40)
    snippet = content[start:end]
    # Encode to ascii-safe for printing
    safe = snippet.encode('ascii','replace').decode('ascii')
    print('L' + str(line) + ': ' + safe)
