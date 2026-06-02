import re

with open('office-v3.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 提取 script 块
scripts = re.findall(r'(<script[^>]*>)(.*?)(</script>)', content, re.DOTALL)

fixed_count = 0
for start, script, end in scripts:
    original = script
    
    # 替换常见的箭头函数模式
    # 模式1: (param) => { ... }
    script = re.sub(r'\(([^)]*)\)\s*=>\s*\{', r'function(\1){', script)
    
    # 模式2: param => { ... }
    script = re.sub(r'([\w$]+)\s*=>\s*\{', r'function(\1){', script)
    
    # 模式3: (param) => expression
    script = re.sub(r'\(([^)]*)\)\s*=>\s*([^;{]+);', r'function(\1){return \2;}', script)
    
    # 模式4: param => expression
    script = re.sub(r'([\w$]+)\s*=>\s*([^;{]+);', r'function(\1){return \2;}', script)
    
    if script != original:
        fixed_count += 1
        content = content.replace(start + original + end, start + script + end)

print('修复了', fixed_count, '个 script 块')

with open('office-v3.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('完成')
