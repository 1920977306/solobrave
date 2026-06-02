with open('office-v3.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 4857 (1-indexed = index 4856)
line = lines[4856]
print('Line 4857 length:', len(line))
print('Line 4857 content:', repr(line))
print('Char 548:', repr(line[547]) if len(line) > 547 else 'N/A')
