import re

with open('office-v3.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find all script blocks
scripts = list(re.finditer(r'<script[^>]*>(.*?)</script>', content, re.DOTALL))
print(f'Found {len(scripts)} script blocks')

for i, m in enumerate(scripts):
    js = m.group(1)
    # Check for </script> inside JS
    if '</script>' in js:
        print(f'Script {i+1}: Contains </script> inside JS!')
        idx = js.find('</script>')
        line = js[:idx].count('\n') + 1
        print(f'  At JS line {line}')
    
    # Check for <!-- or --> inside JS
    if '<!--' in js or '-->' in js:
        print(f'Script {i+1}: Contains HTML comment inside JS')
    
    # Check for unmatched parentheses in common patterns
    lines = js.split('\n')
    for j, line in enumerate(lines):
        # Count parens
        open_p = line.count('(')
        close_p = line.count(')')
        if open_p != close_p and ('map(' in line or 'filter(' in line or 'forEach(' in line):
            print(f'Script {i+1}, JS L{j+1}: Potential paren mismatch: {line[:80]}')
