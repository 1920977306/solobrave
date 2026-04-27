/**
 * OpenAI Provider - OpenAI API 适配器
 * Day 2 核心模块
 */

export class OpenAIProvider {
  constructor(config = {}) {
    this.apiKey = config.apiKey || '';
    this.baseUrl = config.baseUrl || 'https://api.openai.com/v1';
    this.model = config.model || 'gpt-4o-mini';
    this.temperature = config.temperature ?? 0.7;
    this.maxTokens = config.maxTokens || 2000;
  }

  /**
   * 验证配置
   */
  validate() {
    if (!this.apiKey) {
      return { valid: false, error: 'API Key 未设置' };
    }
    // 支持多种 API Key 格式：OpenAI (sk-...), Kimi (sk-kimi-...), 智谱AI (任意格式), 等
    // 智谱AI Key 格式不固定，不做严格校验
    return { valid: true };
  }

  /**
   * 非流式聊天
   */
  async chat(messages, options = {}) {
    const validation = this.validate();
    if (!validation.valid) {
      throw new Error(validation.error);
    }

    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.apiKey}`
      },
      body: JSON.stringify({
        model: options.model || this.model,
        messages,
        temperature: options.temperature ?? this.temperature,
        max_tokens: options.maxTokens || this.maxTokens,
        stream: false
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || `HTTP ${response.status}`);
    }

    const data = await response.json();
    return {
      content: data.choices[0]?.message?.content || '',
      usage: data.usage,
      model: data.model
    };
  }

  /**
   * 流式聊天（SSE）
   */
  async *streamChat(messages, options = {}) {
    const validation = this.validate();
    if (!validation.valid) {
      throw new Error(validation.error);
    }

    const response = await fetch(`${this.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.apiKey}`
      },
      body: JSON.stringify({
        model: options.model || this.model,
        messages,
        temperature: options.temperature ?? this.temperature,
        max_tokens: options.maxTokens || this.maxTokens,
        stream: true
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed || trimmed === 'data: [DONE]') continue;
          if (trimmed.startsWith('data: ')) {
            try {
              const data = JSON.parse(trimmed.slice(6));
              const delta = data.choices?.[0]?.delta;
              if (delta?.content) {
                yield {
                  content: delta.content,
                  finishReason: data.choices?.[0]?.finish_reason
                };
              }
            } catch (e) {
              // 忽略解析错误
            }
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  /**
   * 测试连通性
   */
  async testConnection() {
    try {
      const result = await this.chat(
        [{ role: 'user', content: 'Hi' }],
        { maxTokens: 5, model: this.model }
      );
      return { success: true, latency: Date.now() };
    } catch (error) {
      return { success: false, error: error.message };
    }
  }

  /**
   * 获取可用模型列表
   */
  async listModels() {
    const response = await fetch(`${this.baseUrl}/models`, {
      headers: {
        'Authorization': `Bearer ${this.apiKey}`
      }
    });

    if (!response.ok) {
      throw new Error('获取模型列表失败');
    }

    const data = await response.json();
    return data.data
      .filter(m => m.id.includes('gpt'))
      .map(m => ({
        id: m.id,
        name: m.id
      }));
  }
}

export default OpenAIProvider;
