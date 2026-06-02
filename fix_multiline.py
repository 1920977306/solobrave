import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# Fix multi-line strings that were incorrectly converted from template literals
# Pattern: = "\n  <html...>\n  ...\n";
# Should be: = `\n  <html...>\n  ...\n`;

# We'll use a line-by-line approach
lines = content.split('\n')
result = []
i = 0
while i < len(lines):
    line = lines[i]
    # Check if this line ends with = " (start of multi-line string)
    match = re.search(r'(=\s*)"$', line.rstrip())
    if match:
        prefix = line[:match.end(1)]  # everything up to and including =
        # Find the closing "; or just "
        start = i
        j = i + 1
        found_end = False
        while j < len(lines):
            stripped = lines[j].strip()
            if stripped.endswith('";') or (stripped == '"' and j > start + 1):
                # Found the end - replace " with `
                # Reconstruct with backticks
                result.append(prefix + '`')
                for k in range(start + 1, j):
                    result.append(lines[k])
                # Last line: replace trailing "; with `;
                last_line = lines[j]
                if last_line.rstrip().endswith('";'):
                    last_line = last_line.rstrip()[:-2] + '`;'
                elif last_line.strip() == '"':
                    last_line = last_line.replace('"', '`', 1)
                result.append(last_line)
                i = j + 1
                found_end = True
                break
            j += 1
            if j - start > 50:
                break
        if not found_end:
            result.append(line)
            i += 1
    else:
        result.append(line)
        i += 1

new_content = '\n'.join(result)

# Verify we fixed them
remaining = len(re.findall(r'=\s*"\s*\n', new_content))
print(f'Remaining multi-line string issues: {remaining}')

with open('office-v3.html','w',encoding='utf-8') as f:
    f.write(new_content)

print('Fixed multi-line strings!')
