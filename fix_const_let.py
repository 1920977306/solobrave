import re
import sys

with open('office-v3.html','r',encoding='utf-8') as f:
    content = f.read()

original = content

# 1. Replace const/let declarations with var
# Pattern: \b(const|let)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*=
# But NOT inside string literals or comments - we'll do simple regex
# and be careful

def replace_declarations(text):
    # Replace const/let at start of line or after ; or { or (
    # Use a callback to preserve the variable name
    pattern = r'\b(const|let)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*='
    
    def repl(m):
        return 'var ' + m.group(2) + ' ='
    
    return re.sub(pattern, repl, text)

content = replace_declarations(content)

# 2. Replace 3 arrow functions we found
# L3804: setTimeout(()=>showToast(...),1500);
content = content.replace(
    "setTimeout(()=>showToast('✅ 网关重启成功', 'success'),1500);",
    "setTimeout(function(){ showToast('✅ 网关重启成功', 'success'); },1500);"
)

# L4766: setInterval(()=>{...},8000);
# Find the exact pattern
content = content.replace(
    "setInterval(()=>{",
    "setInterval(function(){"
)

# L4855: document.addEventListener('DOMContentLoaded', async ()=>{
content = content.replace(
    "document.addEventListener('DOMContentLoaded', async ()=>{",
    "document.addEventListener('DOMContentLoaded', async function(){"
)

# 3. Replace template literals that use ${...} with string concatenation
# Find backtick strings with ${...} and replace them
# Pattern: `...${expr}...`

def replace_template_literals(text):
    # Find all template literals (backtick strings)
    # This is tricky - we'll find pairs of backticks and process
    result = []
    i = 0
    while i < len(text):
        if text[i] == '`':
            # Find closing backtick
            j = i + 1
            while j < len(text) and text[j] != '`':
                if text[j] == '\\':
                    j += 2
                else:
                    j += 1
            if j < len(text):
                literal = text[i+1:j]
                # Replace ${expr} with " + expr + "
                # But need to handle nested braces
                parts = []
                k = 0
                while k < len(literal):
                    if literal[k:k+2] == '${':
                        # Find matching }
                        depth = 1
                        m = k + 2
                        while m < len(literal) and depth > 0:
                            if literal[m] == '{':
                                depth += 1
                            elif literal[m] == '}':
                                depth -= 1
                            m += 1
                        expr = literal[k+2:m-1]
                        parts.append('" + (' + expr + ') + "')
                        k = m
                    else:
                        parts.append(literal[k])
                        k += 1
                
                replacement = '"' + ''.join(parts) + '"'
                result.append(replacement)
                i = j + 1
                continue
        result.append(text[i])
        i += 1
    return ''.join(result)

content = replace_template_literals(content)

# Count remaining issues
const_count = len(re.findall(r'\bconst\s+', content))
let_count = len(re.findall(r'\blet\s+', content))
arrow_count = len(re.findall(r'\)=\u003e', content))
template_count = len(re.findall(r'`[^`]*\$\{[^}]*\}[^`]*`', content))

print('Remaining const:', const_count)
print('Remaining let:', let_count)
print('Remaining arrows:', arrow_count)
print('Remaining templates:', template_count)

# Write back
with open('office-v3.html','w',encoding='utf-8') as f:
    f.write(content)

print('Done!')
