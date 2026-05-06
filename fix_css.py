# -*- coding: utf-8 -*-
with open('css/layout.css', 'r', encoding='utf-8', errors='replace') as f:
    content = f.read()

# Fix missing closing brace for .employee-card .emp-actions
content = content.replace(
    '  display: flex;\n\n/* ========== Recruit Card',
    '  display: flex;\n}\n\n/* ========== Recruit Card'
)

with open('css/layout.css', 'w', encoding='utf-8') as f:
    f.write(content)

print('Fixed!')
