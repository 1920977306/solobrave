with open('check.js','r',encoding='utf-8') as f:
    lines = f.readlines()
for i in range(240, 250):
    print(str(i+1) + ': ' + repr(lines[i][:120]))
