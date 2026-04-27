/**
 * Conversation Model - 对话数据模型
 * Day 1 核心模块
 */

/**
 * 创建新对话
 */
export function createConversation(config) {
  const now = new Date().toISOString();

  return {
    id: generateId('conv'),
    type: config.type || 'single', // single | group
    title: config.title || '新对话',
    participants: config.participants || [],
    messages: [],
    createdAt: now,
    updatedAt: now,
    metadata: {
      messageCount: 0,
      lastMessage: null,
      isPinned: false,
      tags: []
    }
  };
}

/**
 * 创建消息
 */
export function createMessage(config) {
  const now = new Date().toISOString();

  return {
    id: generateId('msg'),
    role: config.role || 'user', // user | assistant | system
    content: config.content || '',
    sender: config.sender || null, // 发送者员工 ID（AI 消息）
    timestamp: now,
    status: config.status || 'sent', // sending | sent | error
    metadata: {
      tokens: config.tokens || null,
      model: config.model || null,
      latency: config.latency || null
    }
  };
}

/**
 * 添加消息到对话
 */
export function addMessageToConversation(conversation, message) {
  const messages = [...conversation.messages, message];
  return {
    ...conversation,
    messages,
    updatedAt: new Date().toISOString(),
    metadata: {
      ...conversation.metadata,
      messageCount: messages.length,
      lastMessage: message
    }
  };
}

/**
 * 获取对话的 AI 上下文（用于发送给 API）
 */
export function getConversationContext(conversation, maxMessages = 20) {
  // 取最近的消息
  const recentMessages = conversation.messages.slice(-maxMessages);

  return recentMessages.map(msg => ({
    role: msg.role,
    content: msg.content
  }));
}

/**
 * 生成对话标题（基于第一条消息）
 */
export function generateConversationTitle(firstMessage) {
  if (!firstMessage) return '新对话';

  const content = firstMessage.content;
  if (content.length <= 20) return content;
  return content.substring(0, 20) + '...';
}

/**
 * 生成唯一 ID
 */
function generateId(prefix) {
  const timestamp = Date.now().toString(36);
  const random = Math.random().toString(36).substring(2, 6);
  return `${prefix}_${timestamp}_${random}`;
}
