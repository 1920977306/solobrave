import re

with open('office-v3.html', 'r', encoding='utf-8') as f:
    content = f.read()

scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
js = max(scripts, key=len)

lines = js.split('\n')
print('Total JS lines:', len(lines))

# JS line 2474 = index 2473
if len(lines) > 2473:
    line = lines[2473]
    print(f'JS L2474 (HTML L4857):')
    print(f'Length: {len(line)}')
    print(f'Content: {repr(line)}')
    if len(line) > 547:
        print(f'Char 548: {repr(line[547])}')
        print(f'Context: {repr(line[540:555])}')
else:
    print('JS has fewer lines')
