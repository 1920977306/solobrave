# -*- coding: utf-8 -*-
import os

# 读取文件为二进制
with open('js/ui-chat-simple.js', 'rb') as f:
    content = f.read()

# 检查 BOM
if content.startswith(b'\xef\xbb\xbf'):
    print('有 UTF-8 BOM，移除')
    content = content[3:]

# 检查是否是 UTF-16
if content.startswith(b'\xff\xfe') or content.startswith(b'\xfe\xff'):
    print('是 UTF-16，转换')
    if content.startswith(b'\xff\xfe'):
        text = content[2:].decode('utf-16-le')
    else:
        text = content[2:].decode('utf-16-be')
else:
    # 尝试 UTF-8
    try:
        text = content.decode('utf-8')
        print('是 UTF-8')
    except:
        # 尝试 GBK
        try:
            text = content.decode('gbk')
            print('是 GBK')
        except:
            text = content.decode('utf-8', errors='replace')
            print('无法识别编码，使用替换模式')

# 写回无 BOM 的 UTF-8
with open('js/ui-chat-simple.js', 'w', encoding='utf-8') as f:
    f.write(text)

print('编码修复完成')
