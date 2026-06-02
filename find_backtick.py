with open('office-v3.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找所有包含反引号的行（排除CSS注释和HTML）
for i, line in enumerate(lines, 1):
    if '`' in line:
        # 跳过CSS和HTML属性
        stripped = line.strip()
        if stripped.startswith('<') or stripped.startswith('.') or stripped.startswith('#') or stripped.startswith('/*') or stripped.startswith('*') or stripped.startswith('//'):
            continue
        # 跳过已经在字符串中的
        if 'content:' in line or 'background:' in line or 'font-family:' in line:
            continue
        print('L' + str(i) + ':', line.strip()[:120])
