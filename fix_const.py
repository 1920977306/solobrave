import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Find all const/let declarations
const_matches = list(re.finditer(r'\bconst\s+([a-zA-Z_\$][a-zA-Z0-9_\$]*)\s*=', content))
let_matches = list(re.finditer(r'\blet\s+([a-zA-Z_\$][a-zA-Z0-9_\$]*)\s*=', content))

print('=== const declarations ===')
for m in const_matches[:20]:
    line = content[:m.start()].count('\n') + 1
    print(f'L{line}: {m.group(0)[:40]}...')

print(f'\nTotal const: {len(const_matches)}')
print(f'Total let: {len(let_matches)}')

# Find arrow functions
arrow_matches = list(re.finditer(r'\)=\u003e', content))
print(f'\n=== arrow functions ({len(arrow_matches)}) ===')
for m in arrow_matches[:10]:
    line = content[:m.start()].count('\n') + 1
    start = max(0, m.start()-30)
    end = min(len(content), m.end()+30)
    print(f'L{line}: ...{content[start:end]}...')
