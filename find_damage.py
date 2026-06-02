import re

with open('office-v3.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Find patterns that look like broken template string replacements
# Pattern: .map(... => "\n  <html>...\n") 
# or: return "; }

# Search for .map followed by multi-line string with double quotes
lines = content.split('\n')
for i, line in enumerate(lines):
    # Look for .map( followed by => and then " on same or next line
    if '.map(' in line and '=>' in line:
        # Check next few lines for multi-line string
        for j in range(i, min(i+10, len(lines))):
            if lines[j].strip().startswith('"') and '<' in lines[j]:
                print(f'Potential damage at HTML L{i+1}-{j+1}:')
                for k in range(i, min(i+8, len(lines))):
                    print(f'  L{k+1}: {lines[k][:100]}')
                print()
                break
    
    # Look for return "; } pattern
    if 'return ";' in line or 'return "; }' in line:
        print(f'Broken return at HTML L{i+1}: {line[:100]}')

# Also check for .map(function(a){ return "
for i, line in enumerate(lines):
    if '.map(function(' in line and 'return "' in line:
        print(f'Potential broken function at HTML L{i+1}: {line[:100]}')
