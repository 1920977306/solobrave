import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Extract JS
scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
js = max(scripts, key=len)

# Find all lines with "" inside what looks like a string
lines = js.split('\n')
for i, line in enumerate(lines):
    # Look for pattern: "...""..." (two consecutive quotes inside a string context)
    # This is hard to detect perfectly, so let's look for common broken patterns
    
    # Pattern: = "<tag attr="" + var + "" attr2="" + var2 + "">
    if re.search(r'"[^"]*""[^"]*"', line) and ('=' in line or 'return' in line):
        # Check if this is actually a broken pattern (not just two strings concatenated)
        # Count quotes
        quotes = line.count('"')
        if quotes >= 4:
            print(f'JS L{i+1}: {line[:120]}')
