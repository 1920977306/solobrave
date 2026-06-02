import re

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

# ONLY replace const/let declarations with var
# Pattern: \b(const|let)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=
def repl(m):
    return 'var ' + m.group(2) + ' ='

content = re.sub(r'\b(const|let)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=', repl, content)

# Verify
const_count = len(re.findall(r'\bconst\s+', content))
let_count = len(re.findall(r'\blet\s+', content))
arrow_count = len(re.findall(r'\)=\u003e', content))
backtick_count = content.count('`')

print('const remaining:', const_count)
print('let remaining:', let_count)
print('arrows remaining:', arrow_count)
print('backticks remaining:', backtick_count)

with open('office-v3.html','w',encoding='utf-8') as f:
    f.write(content)

print('Done! Only const/let replaced.')
