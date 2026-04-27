/**
 * AI Gateway - AI 统一调用网关
 * Day 2 核心模块
 */

import { OpenAIProvider } from '../providers/openai-provider.js';
import { AnthropicProvider } from '../providers/anthropic-provider.js';

// 提供商映射
const PROVIDERS = {
  openai: OpenAIProvider,
  anthropic: AnthropicProvider,
  // local: LocalProvider      // 未来扩展
};

class AIGateway {
  constructor() {
    this.providers = new Map();
    this.defaultProvider = 'openai';
  }

  /**
   * 注册/更新提供商配置
   */
  registerProvider(name, config) {
    const ProviderClass = PROVIDERS[name];
    if (!ProviderClass) {
      throw new Error(`未知的提供商: ${name}`);
    }
    this.providers.set(name, new ProviderClass(config));
  }

  /**
   * 获取提供商实例
   */
  getProvider(name = null) {
    const providerName = name || this.defaultProvider;
    const provider = this.providers.get(providerName);
    if (!provider) {
      throw new Error(`提供商未配置: ${providerName}`);
    }
    return provider;
  }

  /**
   * 非流式聊天
   */
  async chat(messages, options = {}) {
    const provider = this.getProvider(options.provider);
    return provider.chat(messages, options);
  }

  /**
   * 流式聊天
   */
  async *streamChat(messages, options = {}) {
    const provider = this.getProvider(options.provider);
    yield* provider.streamChat(messages, options);
  }

  /**
   * 测试连通性
   */
  async testConnection(providerName = null) {
    try {
      const provider = this.getProvider(providerName);
      return await provider.testConnection();
    } catch (error) {
      return { success: false, error: error.message };
    }
  }

  /**
   * 获取可用提供商列表
   */
  getAvailableProviders() {
    const names = {
      openai: 'OpenAI',
      anthropic: 'Anthropic/Claude'
    };
    return Object.keys(PROVIDERS).map(key => ({
      id: key,
      name: names[key] || key,
      configured: this.providers.has(key)
    }));
  }
}

// 单例
const aiGateway = new AIGateway();
export default aiGateway;
