/**
 * Anthropic Provider - Claude API 适配器
 * 支持 Anthropic 原生 API 和兼容 OpenAI 格式的代理
 */

export class AnthropicProvider {
  constructor(config = {}) {
    this.apiKey = config.apiKey || '';
    this.baseUrl = config.baseUrl || 'https://api.anthropic.com';
    this.model = config.model || 'claude-3-sonnet-20240229';
    this.temperature = config.temperature ?? 0.7;
    this.maxTokens = config.maxTokens || 2000;
    this.compatibilityMode = config.compatibilityMode || 'auto';
  }

  validate() {
    if (!this.apiKey) {
      return { valid: false, error: 'API Key 未设置' };
    }
    return { valid: true };
  }

  async detectMode() {
    if (this.compatibilityMode !== 'auto') {
      return this.compatibilityMode;
    }
    // 先尝试 OpenAI 兼容格式
    try {
      const response = await fetch(`${this.baseUrl}/v1/models`, {
        headers: { 'Authorization': `Bearer ${this.apiKey}` }
      });
      if (response.ok) {
        this.compatibilityMode = 'openai';
        return 'openai';
      }
    } catch (e) {}
    // 再尝试 Anthropic 原生格式
    try {
      const response = await fetch(`${this.baseUrl}/v1/models`, {
        headers: {
          'x-api-key': this.apiKey,
          'anthropic-version': '2023-06-01'
        }
      });
      if (response.ok) {
        this.compatibilityMode = 'native';
        return 'native';
      }
    } catch (e) {}
    // 默认使用 OpenAI 兼容模式
    this.compatibilityMode = 'openai';
    return 'openai';
  }

  _convertMessages(messages) {
    const system = messages.find(m => m.role === 'system')?.content || '';
    const conversation = messages
      .filter(m => m.role !== 'system')
      .map(m => ({
        role: m.role === 'assistant' ? 'assistant' : 'user',
        content: m.content
      }));
    return { system, messages: conversation };
  }

  async _chatNative(messages, options = {}) {
    const { system, messages: conversation } = this._convertMessages(messages);
    const response = await fetch(`${this.baseUrl}/v1/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': this.apiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: options.model || this.model,
        max_tokens: options.maxTokens || this.maxTokens,
        temperature: options.temperature ?? this.temperature,
        system,
        messages: conversation
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || `HTTP ${response.status}`);
    }

    const data = await response.json();
    return {
      content: data.content?.[0]?.text || '',
      usage: data.usage,
      model: data.model
    };
  }

  async _chatOpenAI(messages, options = {}) {
    const response = await fetch(`${this.baseUrl}/v1/chat/completions`, {
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

  async chat(messages, options = {}) {
    const validation = this.validate();
    if (!validation.valid) {
      throw new Error(validation.error);
    }
    const mode = await this.detectMode();
    if (mode === 'openai') {
      return this._chatOpenAI(messages, options);
    }
    return this._chatNative(messages, options);
  }

  async *_streamOpenAI(messages, options = {}) {
    const response = await fetch(`${this.baseUrl}/v1/chat/completions`, {
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
            } catch (e) {}
          }
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  async *_streamNative(messages, options = {}) {
    const { system, messages: conversation } = this._convertMessages(messages);
    const response = await fetch(`${this.baseUrl}/v1/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': this.apiKey,
        'anthropic-version': '2023-06-01'
      },
      body: JSON.stringify({
        model: options.model || this.model,
        max_tokens: options.maxTokens || this.maxTokens,
        temperature: options.temperature ?? this.temperature,
        system,
        messages: conversation,
        stream: true
      })
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.error?.message || `HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        const lines = chunk.split('\n');
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'content_block_delta' && data.delta?.text) {
              yield {
                content: data.delta.text,
                finishReason: null
              };
            }
          } catch (e) {}
        }
      }
    } finally {
      reader.releaseLock();
    }
  }

  async *streamChat(messages, options = {}) {
    const validation = this.validate();
    if (!validation.valid) {
      throw new Error(validation.error);
    }
    const mode = await this.detectMode();
    if (mode === 'openai') {
      yield* this._streamOpenAI(messages, options);
    } else {
      yield* this._streamNative(messages, options);
    }
  }

  async testConnection() {
    // 先尝试 OpenAI 兼容格式
    try {
      const response = await fetch(`${this.baseUrl}/v1/models`, {
        headers: { 'Authorization': `Bearer ${this.apiKey}` }
      });
      if (response.ok) {
        this.compatibilityMode = 'openai';
        return { success: true, mode: 'openai' };
      }
    } catch (e) {}
    
    // 再尝试 Anthropic 原生格式
    try {
      const response = await fetch(`${this.baseUrl}/v1/models`, {
        headers: {
          'x-api-key': this.apiKey,
          'anthropic-version': '2023-06-01'
        }
      });
      if (response.ok) {
        this.compatibilityMode = 'native';
        return { success: true, mode: 'native' };
      }
    } catch (e) {}
    
    return { success: false, error: '无法连接到 API，请检查 Key 和 Base URL' };
  }

  async listModels() {
    const mode = await this.detectMode();
    if (mode === 'openai') {
      const response = await fetch(`${this.baseUrl}/v1/models`, {
        headers: { 'Authorization': `Bearer ${this.apiKey}` }
      });
      if (!response.ok) throw new Error('获取模型列表失败');
      const data = await response.json();
      return data.data.map(m => ({ id: m.id, name: m.id }));
    }
    return [
      { id: 'claude-3-opus-20240229', name: 'Claude 3 Opus' },
      { id: 'claude-3-sonnet-20240229', name: 'Claude 3 Sonnet' },
      { id: 'claude-3-haiku-20240307', name: 'Claude 3 Haiku' }
    ];
  }
}

export default AnthropicProvider;