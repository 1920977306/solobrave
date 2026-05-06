# -*- coding: utf-8 -*-
with open('css/layout.css', 'rb') as f:
    content = f.read()

# Fix: replace the literal \n with actual newline
content = content.replace(
    b'  display: flex;\\n}\r\n\r\n/* ========== Recruit Card',
    b'  display: flex;\r\n}\r\n\r\n/* ========== Recruit Card'
)

with open('css/layout.css', 'wb') as f:
    f.write(content)

print('Fixed!')
