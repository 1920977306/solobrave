import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Find all patterns where we have:
# var x = "\n  <tag...>\n  ...\n";
# These are multi-line strings that were originally template literals

# Pattern: = "\n followed by HTML/JS content and ending with \n";
# We need to find these and replace " with ` (backtick)

# Let's find lines that start with multi-line string assignments
lines = content.split('\n')
issues = []
for i, line in enumerate(lines):
    # Look for lines that have = " and the next lines continue until ";
    if re.search(r'=\s*"$', line.rstrip()):
        # This line ends with = " - start of multi-line string
        start = i
        j = i + 1
        while j < len(lines):
            if lines[j].strip().endswith('";') or lines[j].strip() == '"':
                # Found end
                issues.append((start, j, lines[start:j+1]))
                break
            j += 1
            if j - start > 20:  # Safety limit
                break

print(f'Found {len(issues)} potential multi-line string issues')
for start, end, snippet in issues[:10]:
    print(f'\nLines {start+1}-{end+1}:')
    for s in snippet:
        print('  ', repr(s[:80]))
