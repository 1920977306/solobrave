with open('index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. empModelConfig 加保护
content = content.replace(
    "document.getElementById('empModelConfig').value",
    "(document.getElementById('empModelConfig')||{}).value"
)

# 2. 文档相关元素加保护
for el in ['empIdDoc', 'empSoulDoc', 'empToolsDoc', 'empUserDoc']:
    old = f"document.getElementById('{el}').value"
    new = f"(document.getElementById('{el}')||{{}}).value"
    content = content.replace(old, new)

# 3. showEditModal 改跳转到基础 Tab
content = content.replace(
    "setTimeout(()=> switchEmpDetailTab('docs'), 100);",
    "setTimeout(()=> switchEmpDetailTab('basic'), 100);"
)

with open('index.html', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done - null protection added')
