with open('js/ui-chat-simple.js', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find the line with 'await AI.sendMessage'
start_line = None
end_line = None
for i, line in enumerate(lines):
    if 'await AI.sendMessage' in line:
        start_line = i
    if start_line and 'catch (error)' in line:
        end_line = i
        break

print(f'Found block from line {start_line+1} to {end_line+1}')

if start_line and end_line:
    # Replace the block
    new_block = """      // 使用 OpenClawClient 发送消息
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
    """
    
    new_lines = new_block.split('\n')
    new_lines = [line + '\n' for line in new_lines if line]
    
    # Replace lines
    result = lines[:start_line-2] + new_lines + lines[end_line:]
    
    with open('js/ui-chat-simple.js', 'w', encoding='utf-8') as f:
        f.writelines(result)
    print('Successfully replaced')
else:
    print('Could not find the block')
