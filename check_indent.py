with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    content = f.read()
idx = content.find('var messages = Store.getConversation')
print('Index:', idx)
if idx > 0:
    print(repr(content[idx:idx+300]))
