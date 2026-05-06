with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    content = f.read()

old_code = """    try {
      var messages = Store.getConversation(currentEmployee.id);
      var aiContent = '';
      
      await AI.sendMessage(currentEmployee.id, messages, function(chunk, full) {
        aiContent = full;
        updateTypingContent(aiContent);
      });
      
      Store.addMessage(currentEmployee.id, {
        role: 'assistant',
        content: aiContent
      });
    } catch (error) {
      Store.addMessage(currentEmployee.id, {
        role: 'assistant',
        content: '抱歉，发生了错误：' + error.message
      });
    }"""

new_code = """    try {
      // 使用 OpenClawClient 发送消息
      var client = OpenClawClient.getInstance();
      var sessionKey = 'agent:' + currentEmployee.id + ':main';
      
      var res = await client.sendMessage(sessionKey, content);
      
      // 解析响应内容（支持 content 数组）
      var aiContent = '';
      var thinkingContent = '';
      
      if (res.message && res.message.content) {
        res.message.content.forEach(function(c) {
          if (c.type === 'text') {
            aiContent += c.text;
          } else if (c.type === 'thinking') {
            thinkingContent += c.thinking;
          }
        });
      }
      
      // 如果有 thinking，先显示 thinking
      if (thinkingContent) {
        updateTypingContent('思考中：' + thinkingContent.substring(0, 50) + '...');
        await new Promise(function(resolve) { setTimeout(resolve, 500); });
      }
      
      Store.addMessage(currentEmployee.id, {
        role: 'assistant',
        content: aiContent || '收到您的消息'
      });
    } catch (error) {
      console.error('Send message error:', error);
      Store.addMessage(currentEmployee.id, {
        role: 'assistant',
        content: '抱歉，发生了错误：' + error.message
      });
    }"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open('js/ui-chat-simple.js', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Successfully replaced sendMessage function')
else:
    print('Old code not found')
    print('Looking for:', repr(old_code[:100]))
