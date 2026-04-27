/**
 * Soul Engine - 灵魂注入引擎
 * Day 3 核心模块
 */

import { generateSystemPrompt } from '../models/employee.js';

class SoulEngine {
  constructor() {
    // 记忆层级配置
    this.memoryConfig = {
      l0_system: { maxTokens: 500, always: true },
      l1_working: { maxTokens: 2000, always: true },
      l2_episodic: { maxTokens: 3000, summary: true },
      l3_semantic: { maxTokens: 4000, retrieval: true }
    };
  }

  /**
   * 为 AI 员工构建完整 Prompt
   */
  buildPrompt(employee, context = {}) {
    const parts = [];

    // L0: 系统人格（每次必带）
    const systemPrompt = generateSystemPrompt(employee);
    parts.push({
      role: 'system',
      content: systemPrompt,
      layer: 'L0'
    });

    // L1: 工作记忆（当前任务上下文）
    if (context.task) {
      parts.push({
        role: 'system',
        content: `当前任务：${context.task.title}\n${context.task.description || ''}`,
        layer: 'L1'
      });
    }

    // L2: 情景记忆（相关历史对话摘要）
    if (context.relevantMemories?.length > 0) {
      const memoryText = context.relevantMemories
        .map(m => `[${m.date}] ${m.summary}`)
        .join('\n');
      parts.push({
        role: 'system',
        content: `相关历史记忆：\n${memoryText}`,
        layer: 'L2'
      });
    }

    // L3: 语义记忆（知识库相关）
    if (context.knowledge?.length > 0) {
      const knowledgeText = context.knowledge
        .map(k => `- ${k.title}: ${k.summary}`)
        .join('\n');
      parts.push({
        role: 'system',
        content: `相关知识：\n${knowledgeText}`,
        layer: 'L3'
      });
    }

    return parts;
  }

  /**
   * 压缩对话历史为摘要
   */
  async compressConversation(messages, provider) {
    if (messages.length < 10) return null;

    const conversationText = messages
      .map(m => `${m.role}: ${m.content}`)
      .join('\n');

    const summaryPrompt = [
      {
        role: 'system',
        content: '你是一个对话摘要助手。请将以下对话压缩为简洁的摘要，保留关键信息和决策。'
      },
      {
        role: 'user',
        content: `请摘要以下对话（200字以内）：\n\n${conversationText}`
      }
    ];

    try {
      const result = await provider.chat(summaryPrompt, { maxTokens: 300 });
      return {
        summary: result.content,
        originalLength: messages.length,
        compressedAt: new Date().toISOString()
      };
    } catch (e) {
      console.warn('Conversation compression failed:', e);
      return null;
    }
  }

  /**
   * 提取关键事实（用于长期记忆）
   */
  async extractKeyFacts(message, provider) {
    const prompt = [
      {
        role: 'system',
        content: '从以下消息中提取关键事实（偏好、决策、重要信息等），每条用一句话描述。'
      },
      {
        role: 'user',
        content: message
      }
    ];

    try {
      const result = await provider.chat(prompt, { maxTokens: 200 });
      return result.content
        .split('\n')
        .map(line => line.trim())
        .filter(line => line && !line.startsWith('-'));
    } catch (e) {
      return [];
    }
  }

  /**
   * 估算 tokens（简单估算）
   */
  estimateTokens(text) {
    // 粗略估算：1 token ≈ 0.75 个汉字 或 4 个英文字符
    const chineseChars = (text.match(/[\u4e00-\u9fa5]/g) || []).length;
    const otherChars = text.length - chineseChars;
    return Math.ceil(chineseChars / 0.75 + otherChars / 4);
  }

  /**
   * 构建消息（带记忆管理）
   */
  buildMessages(employee, conversation, newMessage, provider) {
    const context = {
      task: employee.status.currentTask,
      relevantMemories: employee.memory.longTerm.slice(-3),
      knowledge: []
    };

    // 构建系统提示
    const promptParts = this.buildPrompt(employee, context);
    const systemMessages = promptParts.map(p => ({
      role: p.role,
      content: p.content
    }));

    // 添加历史对话（排除 system 消息，只保留 user 和 assistant）
    const historyMessages = conversation.messages
      .filter(m => m.role === 'user' || m.role === 'assistant')
      .slice(-10)
      .map(m => ({
        role: m.role,
        content: m.content
      }));

    // 添加新消息
    const userMessage = {
      role: 'user',
      content: newMessage
    };

    return [...systemMessages, ...historyMessages, userMessage];
  }
}

const soulEngine = new SoulEngine();
export default soulEngine;
