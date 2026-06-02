import re

with open('office-v3.html', 'r', encoding='utf-8') as f:
    content = f.read()

scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
js = max(scripts, key=len)

lines = js.split('\n')
print('Total JS lines:', len(lines))

# Check line 4857 in JS (if exists)
if len(lines) > 4856:
    line = lines[4856]
    print('JS Line 4857 length:', len(line))
    print('JS Line 4857 content:', repr(line[:100]))
    if len(line) > 547:
        print('Char 548:', repr(line[547]))
else:
    print('JS has fewer than 4857 lines')
    # Check last few lines
    for i in range(max(0, len(lines)-5), len(lines)):
        print(f'JS Line {i+1}:', repr(lines[i][:80]))
