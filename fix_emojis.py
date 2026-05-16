import re
with open('apply_s1_s2_s3.py','r',encoding='utf-8') as f:
    content=f.read()
content=content.replace('✅','[OK]').replace('⚠️','[WARN]').replace('🎉','[DONE]').replace('🔧','[WRENCH]')
with open('apply_s1_s2_s3.py','w',encoding='utf-8') as f:
    f.write(content)
print('Replaced emojis')
