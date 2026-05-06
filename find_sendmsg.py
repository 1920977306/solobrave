with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    content = f.read()

idx = content.find('window.sendMessage = async function()')
if idx > 0:
    print('Found at index:', idx)
    print('---')
    print(content[idx:idx+1200])
    print('---')
else:
    print('Not found')
