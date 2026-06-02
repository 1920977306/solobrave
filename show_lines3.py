with open('check.js','r',encoding='utf-8') as f:
    lines = f.readlines()
for i in range(493, 503):
    safe = lines[i][:120].encode('ascii','replace').decode('ascii')
    print(str(i+1) + ': ' + safe)
