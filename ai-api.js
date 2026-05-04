// ===== 智谱 API 调用（流式） =====
async function callZhipuAPI(messages, onChunk, onDone, onError) {
  try {
    var res = await fetch(ZHIPU_CONFIG.baseUrl, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + ZHIPU_CONFIG.apiKey
      },
      body: JSON.stringify({
        model: ZHIPU_CONFIG.model,
        messages: messages,
        max_tokens: ZHIPU_CONFIG.maxTokens,
        temperature: ZHIPU_CONFIG.temperature,
        stream: true
      })
    });

    var reader = res.body.getReader();
    var decoder = new TextDecoder();
    var buffer = '';

    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;

      buffer += decoder.decode(chunk.value, { stream: true });
      var lines = buffer.split('\n');
      buffer = lines.pop();

      for (var i = 0; i < lines.length; i++) {
        var line = lines[i].trim();
        if (!line || !line.startsWith('data:')) continue;
        var data = line.slice(5).trim();
        if (data === '[DONE]') { onDone(); return; }

        try {
          var json = JSON.parse(data);
          var delta = json.choices[0].delta.content;
          if (delta) onChunk(delta);
        } catch (e) {}
      }
    }
    onDone();
  } catch (err) {
    onError(err);
  }
}
