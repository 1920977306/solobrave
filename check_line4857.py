with open('office-v3.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Line 4857 (1-indexed = index 4856)
line_idx = 4856
if line_idx < len(lines):
    line = lines[line_idx]
    print(f'Line 4857 length: {len(line)}')
    print(f'Line 4857: {repr(line)}')
    
    # If line is short, check surrounding lines
    if len(line.strip()) < 10:
        print('\nSurrounding lines:')
        for i in range(max(0, line_idx-3), min(len(lines), line_idx+4)):
            marker = '>>> ' if i == line_idx else '    '
            content = lines[i].rstrip()
            print(f'{marker}L{i+1}: {content[:120]}')
else:
    print(f'File has {len(lines)} lines, L4857 does not exist')
