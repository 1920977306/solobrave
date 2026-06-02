with open('check.js','r',encoding='utf-8') as f:
    content = f.read()

# Find the problematic pattern - multi-line string with double quotes
idx = content.find('select.innerHTML = "')
print('Found at index:', idx)
print('Context:', repr(content[idx:idx+200]))

# Also check for other multi-line string patterns
import re
# Find all "...\n..." patterns (multi-line double-quoted strings)
matches = list(re.finditer(r'"[^"]*\n[^"]*"', content))
print('\nMulti-line double-quoted strings found:', len(matches))
for m in matches[:5]:
    line = content[:m.start()].count('\n') + 1
    print('JS Line', line, ':', repr(m.group()[:80]))
