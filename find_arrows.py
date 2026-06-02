import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# 提取 script 块
scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)

total = 0
for i, s in enumerate(scripts):
    # 找箭头函数
    arrows = re.findall(r'\)\s*=>\s*[\({]', s)
    if arrows:
        print('script #' + str(i) + ': ' + str(len(arrows)) + ' 个箭头函数')
        total += len(arrows)
        # 找出位置
        lines = s.split('\n')
        count = 0
        for j, line in enumerate(lines):
            if '=>' in line:
                # 排除注释和字符串
                clean = re.sub(r'//.*', '', line)
                clean = re.sub(r'"[^"]*"', '""', clean)
                clean = re.sub(r"'[^']*'", "''", clean)
                if '=>' in clean:
                    count += 1
                    if count <= 5:  # 只显示前5个
                        print('  L' + str(j+1) + ': ' + line.strip()[:80])
        if count > 5:
            print('  ... 还有 ' + str(count-5) + ' 个')

print('\n总计: ' + str(total) + ' 个箭头函数')
