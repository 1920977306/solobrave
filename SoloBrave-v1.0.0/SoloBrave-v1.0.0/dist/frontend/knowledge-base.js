/**
 * Solo Brave Knowledge Base - 知识库系统
 * 
 * 向量索引 + 知识图谱 + RAG 支持
 */

var KnowledgeBase = (function() {
    'use strict';
    
    // ===== 存储键 =====
    var STORAGE_KEYS = {
        DOCUMENTS: 'kb_documents',
        VECTORS: 'kb_vectors',
        GRAPH: 'kb_graph',
        INDEX: 'kb_index'
    };
    
    // ===== 文档存储 =====
    var documents = {};    // { id: Document }
    var vectors = {};      // { id: Vector }
    var graph = {};        // 知识图谱
    var index = {};        // 倒排索引
    
    // ===== 配置 =====
    var config = {
        embeddingModel: 'tfidf',    // tfidf | word2vec
        vectorDim: 128,             // 向量维度
        maxDocuments: 1000,
        similarityThreshold: 0.7,
        chunkSize: 500,             // 分块大小
        chunkOverlap: 50            // 重叠大小
    };
    
    // ===== 文档结构 =====
    var Document = {
        id: null,
        title: '',
        content: '',
        chunks: [],
        metadata: {},
        createdAt: null,
        updatedAt: null,
        tags: [],
        category: 'general'
    };
    
    // ===== 向量结构 =====
    var Vector = {
        id: null,
        documentId: null,
        chunkIndex: 0,
        values: [],        // 浮点数组
        metadata: {}
    };
    
    // ===== 节点结构 =====
    var GraphNode = {
        id: null,
        type: 'entity',     // entity | concept | topic
        name: '',
        description: '',
        properties: {},
        connections: [],    // [ { targetId, relation, weight } ]
        embedding: null
    };
    
    // ===== TF-IDF 向量生成 =====
    function generateTFIDF(text) {
        // 分词
        var words = tokenize(text);
        
        // 计算词频
        var tf = {};
        words.forEach(function(w) {
            tf[w] = (tf[w] || 0) + 1;
        });
        
        // 归一化
        var maxFreq = Math.max.apply(null, Object.values(tf));
        Object.keys(tf).forEach(function(w) {
            tf[w] = tf[w] / maxFreq;
        });
        
        // 简化的 IDF（实际应该用预计算的语料库）
        var idf = {};
        Object.keys(tf).forEach(function(w) {
            idf[w] = 1.0; // 简化：所有词 IDF = 1
        });
        
        // TF-IDF
        var tfidf = {};
        Object.keys(tf).forEach(function(w) {
            tfidf[w] = tf[w] * idf[w];
        });
        
        // 转换为固定维度向量
        var vector = new Array(config.vectorDim).fill(0);
        var vocab = Object.keys(tfidf).slice(0, config.vectorDim);
        vocab.forEach(function(word, i) {
            vector[i] = tfidf[word];
        });
        
        // L2 归一化
        var norm = Math.sqrt(vector.reduce(function(sum, v) { return sum + v * v; }, 0));
        if (norm > 0) {
            vector = vector.map(function(v) { return v / norm; });
        }
        
        return vector;
    }
    
    // ===== 简单分词 =====
    function tokenize(text) {
        // 中英文混合分词
        var words = [];
        
        // 英文按空格分
        var enWords = text.match(/[a-zA-Z]+/g) || [];
        words = words.concat(enWords.map(function(w) { return w.toLowerCase(); }));
        
        // 中文按 N-gram (2-4字)
        var zhChars = text.match(/[\u4e00-\u9fff]/g) || [];
        for (var i = 0; i < zhChars.length; i++) {
            if (i + 1 < zhChars.length) words.push(zhChars[i] + zhChars[i + 1]);
            if (i + 2 < zhChars.length) words.push(zhChars[i] + zhChars[i + 1] + zhChars[i + 2]);
        }
        
        // 停用词过滤（使用 Set 提高性能）
        var stopWords = new Set(['的', '了', '是', '在', '和', '就', '都', '而', '及', '与', '着', '或', '一个', '没有', '我们', '你们', '他们']);
        words = words.filter(function(w) { return !stopWords.has(w) && w.length >= 2; });
        
        return words;
    }
    
    // ===== 计算余弦相似度 =====
    function cosineSimilarity(a, b) {
        if (a.length !== b.length) return 0;
        
        var dotProduct = 0;
        var normA = 0;
        var normB = 0;
        
        for (var i = 0; i < a.length; i++) {
            dotProduct += a[i] * b[i];
            normA += a[i] * a[i];
            normB += b[i] * b[i];
        }
        
        normA = Math.sqrt(normA);
        normB = Math.sqrt(normB);
        
        if (normA === 0 || normB === 0) return 0;
        return dotProduct / (normA * normB);
    }
    
    // ===== 文本分块 =====
    function chunkText(text, size, overlap) {
        size = size || config.chunkSize;
        overlap = overlap || config.chunkOverlap;
        
        var chunks = [];
        
        // 按句子分割
        var sentences = text.split(/[。！？\n]+/);
        var current = '';
        
        sentences.forEach(function(sentence) {
            if (current.length + sentence.length > size) {
                if (current.length > 0) {
                    chunks.push(current.trim());
                }
                // 保留 overlap
                current = current.slice(-overlap) + sentence;
            } else {
                current += sentence;
            }
        });
        
        if (current.length > 0) {
            chunks.push(current.trim());
        }
        
        return chunks;
    }
    
    // ===== 添加文档 =====
    function addDocument(doc) {
        var id = doc.id || 'doc_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        
        // 分块
        var chunks = chunkText(doc.content);
        
        // 创建文档
        var document = Object.assign({}, Document, {
            id: id,
            title: doc.title || 'Untitled',
            content: doc.content,
            chunks: chunks,
            metadata: doc.metadata || {},
            createdAt: Date.now(),
            updatedAt: Date.now(),
            tags: doc.tags || [],
            category: doc.category || 'general'
        });
        
        documents[id] = document;
        
        // 生成向量
        chunks.forEach(function(chunk, index) {
            var vector = generateTFIDF(chunk);
            vectors[id + '_' + index] = {
                id: id + '_' + index,
                documentId: id,
                chunkIndex: index,
                values: vector,
                metadata: { text: chunk }
            };
        });
        
        // 更新倒排索引
        updateInvertedIndex(id, doc);
        
        // 保存
        saveToStorage();
        
        EventBus.emit('kb:document:added', { id: id, document: document });
        console.log('[KB] Document added:', id, '-', chunks.length, 'chunks');
        
        return document;
    }
    
    // ===== 更新倒排索引 =====
    function updateInvertedIndex(docId, doc) {
        var words = tokenize(doc.title + ' ' + doc.content);
        
        words.forEach(function(word) {
            if (!index[word]) {
                index[word] = [];
            }
            if (index[word].indexOf(docId) === -1) {
                index[word].push(docId);
            }
        });
    }
    
    // ===== 搜索 =====
    function search(query, options) {
        options = options || {};
        var limit = options.limit || 10;
        var threshold = options.threshold || config.similarityThreshold;
        var searchType = options.type || 'hybrid'; // vector | keyword | hybrid
        
        var results = [];
        
        if (searchType === 'vector' || searchType === 'hybrid') {
            // 向量搜索
            var queryVector = generateTFIDF(query);
            
            Object.values(vectors).forEach(function(v) {
                var similarity = cosineSimilarity(queryVector, v.values);
                if (similarity >= threshold) {
                    results.push({
                        documentId: v.documentId,
                        chunkIndex: v.chunkIndex,
                        text: v.metadata.text,
                        similarity: similarity,
                        type: 'vector'
                    });
                }
            });
        }
        
        if (searchType === 'keyword' || searchType === 'hybrid') {
            // 关键词搜索
            var queryWords = tokenize(query);
            
            queryWords.forEach(function(word) {
                if (index[word]) {
                    index[word].forEach(function(docId) {
                        // 检查是否已存在
                        var exists = results.some(function(r) { return r.documentId === docId; });
                        if (!exists) {
                            var doc = documents[docId];
                            if (doc) {
                                results.push({
                                    documentId: docId,
                                    chunkIndex: 0,
                                    text: doc.content.substring(0, 200),
                                    similarity: 0.5,
                                    type: 'keyword'
                                });
                            }
                        }
                    });
                }
            });
        }
        
        // 去重并排序
        var seen = {};
        results = results.filter(function(r) {
            var key = r.documentId + '_' + r.chunkIndex;
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
        
        results.sort(function(a, b) { return b.similarity - a.similarity; });
        results = results.slice(0, limit);
        
        // 添加文档信息
        results.forEach(function(r) {
            var doc = documents[r.documentId];
            if (doc) {
                r.title = doc.title;
                r.tags = doc.tags;
                r.metadata = doc.metadata;
            }
        });
        
        return results;
    }
    
    // ===== 知识图谱 - 添加节点 =====
    function addNode(node) {
        var id = node.id || 'node_' + Date.now();
        
        var graphNode = Object.assign({}, GraphNode, {
            id: id,
            type: node.type || 'entity',
            name: node.name || '',
            description: node.description || '',
            properties: node.properties || {},
            connections: node.connections || [],
            embedding: node.embedding || generateTFIDF(node.name + ' ' + node.description)
        });
        
        graph[id] = graphNode;
        
        EventBus.emit('kb:graph:node:added', graphNode);
        console.log('[KB] Graph node added:', id);
        
        return graphNode;
    }
    
    // ===== 知识图谱 - 添加关系 =====
    function addRelation(sourceId, targetId, relation, weight) {
        weight = weight || 1.0;
        
        var source = graph[sourceId];
        var target = graph[targetId];
        
        if (!source || !target) {
            throw new Error('Source or target node not found');
        }
        
        // 添加到源节点
        source.connections.push({
            targetId: targetId,
            relation: relation,
            weight: weight
        });
        
        // 如果不是对称关系，也添加到目标
        if (!relation.symmetric) {
            target.connections.push({
                targetId: sourceId,
                relation: relation,
                weight: weight
            });
        }
        
        EventBus.emit('kb:graph:relation:added', { source: sourceId, target: targetId, relation: relation });
        
        return { source: sourceId, target: targetId, relation: relation };
    }
    
    // ===== 知识图谱 - 查找相关节点 =====
    function findRelated(nodeId, depth) {
        depth = depth || 1;
        
        var results = [];
        var visited = {};
        var queue = [{ id: nodeId, level: 0 }];
        
        while (queue.length > 0) {
            var current = queue.shift();
            
            if (visited[current.id] || current.level > depth) continue;
            visited[current.id] = true;
            
            var node = graph[current.id];
            if (!node) continue;
            
            results.push({
                node: node,
                level: current.level,
                path: current.path || [nodeId]
            });
            
            // 添加连接节点到队列
            node.connections.forEach(function(conn) {
                if (!visited[conn.targetId]) {
                    queue.push({
                        id: conn.targetId,
                        level: current.level + 1,
                        path: (current.path || [nodeId]).concat([conn.targetId])
                    });
                }
            });
        }
        
        return results;
    }
    
    // ===== 知识图谱 - 搜索 =====
    function searchGraph(query) {
        var queryVector = generateTFIDF(query);
        var results = [];
        
        Object.values(graph).forEach(function(node) {
            if (node.embedding) {
                var similarity = cosineSimilarity(queryVector, node.embedding);
                if (similarity > 0.3) {
                    results.push({
                        node: node,
                        similarity: similarity
                    });
                }
            }
        });
        
        results.sort(function(a, b) { return b.similarity - a.similarity; });
        return results.slice(0, 20);
    }
    
    // ===== RAG - 生成上下文 =====
    function generateContext(query, options) {
        options = options || {};
        var maxLength = options.maxLength || 2000;
        
        // 搜索相关文档
        var docs = search(query, { limit: 5, threshold: 0.5 });
        
        // 搜索相关图谱节点
        var nodes = searchGraph(query);
        
        // 构建上下文
        var contextParts = [];
        
        if (docs.length > 0) {
            contextParts.push('## 相关文档\n');
            docs.forEach(function(result, i) {
                contextParts.push((i + 1) + '. **' + result.title + '**\n   ' + result.text.substring(0, 300) + '\n');
            });
        }
        
        if (nodes.length > 0) {
            contextParts.push('\n## 相关知识\n');
            nodes.slice(0, 3).forEach(function(item) {
                contextParts.push('- ' + item.node.name + ': ' + item.node.description.substring(0, 100));
            });
        }
        
        var context = contextParts.join('\n');
        
        // 截断
        if (context.length > maxLength) {
            context = context.substring(0, maxLength) + '\n...(已截断)';
        }
        
        return {
            context: context,
            documents: docs,
            nodes: nodes,
            sources: docs.length + nodes.length
        };
    }
    
    // ===== RAG - 增强回答 =====
    function enhanceWithRAG(query, basePrompt) {
        var rag = generateContext(query);
        
        if (rag.sources === 0) {
            return basePrompt;
        }
        
        return basePrompt + '\n\n' +
               '## 参考知识\n' +
               '请结合以下知识回答问题：\n\n' +
               rag.context;
    }
    
    // ===== 保存到存储 =====
    function saveToStorage() {
        Store.set(STORAGE_KEYS.DOCUMENTS, documents);
        Store.set(STORAGE_KEYS.VECTORS, vectors);
        Store.set(STORAGE_KEYS.GRAPH, graph);
        Store.set(STORAGE_KEYS.INDEX, index);
    }
    
    // ===== 从存储加载 =====
    function loadFromStorage() {
        var docs = Store.get(STORAGE_KEYS.DOCUMENTS);
        var vecs = Store.get(STORAGE_KEYS.VECTORS);
        var g = Store.get(STORAGE_KEYS.GRAPH);
        var idx = Store.get(STORAGE_KEYS.INDEX);
        
        if (docs) documents = docs;
        if (vecs) vectors = vecs;
        if (g) graph = g;
        if (idx) index = idx;
        
        console.log('[KB] Loaded from storage:', Object.keys(documents).length, 'docs,', Object.keys(graph).length, 'nodes');
    }
    
    // ===== 初始化 =====
    function init() {
        loadFromStorage();
        
        // 添加一些示例知识
        if (Object.keys(documents).length === 0) {
            addSampleKnowledge();
        }
        
        console.log('[KB] Knowledge base initialized');
        console.log('[KB] Documents:', Object.keys(documents).length, '| Vectors:', Object.keys(vectors).length, '| Graph:', Object.keys(graph).length);
        
        return {
            documents: Object.keys(documents).length,
            vectors: Object.keys(vectors).length,
            nodes: Object.keys(graph).length
        };
    }
    
    // ===== 添加示例知识 =====
    function addSampleKnowledge() {
        // 示例文档
        addDocument({
            title: 'Solo Brave 系统介绍',
            content: 'Solo Brave 是一个 AI 原生 IM 系统，支持多渠道消息收发，内置记忆系统和知识库。它采用三层记忆架构，支持向量搜索和 RAG 增强。',
            tags: ['系统', 'AI', 'IM'],
            category: 'system'
        });
        
        addDocument({
            title: '工具系统使用指南',
            content: '工具系统提供文件操作、命令执行、网页搜索、代码运行等功能。每个工具有名称、描述、参数定义，支持动态注册和执行统计。',
            tags: ['工具', '使用', '指南'],
            category: 'docs'
        });
        
        addDocument({
            title: '记忆系统架构',
            content: '记忆系统分为三层：L1 对话记忆（短期）、L2 日级记忆（中期）、L3 核心记忆（长期）。支持自动蒸馏和上下文构建。',
            tags: ['记忆', '架构', 'AI'],
            category: 'system'
        });
        
        // 示例图谱节点
        addNode({
            type: 'concept',
            name: 'RAG',
            description: 'Retrieval-Augmented Generation，检索增强生成。通过检索相关知识增强 AI 回答质量。'
        });
        
        addNode({
            type: 'concept',
            name: '向量搜索',
            description: '将文本转换为向量，通过余弦相似度进行语义匹配。'
        });
        
        addNode({
            type: 'concept',
            name: '知识图谱',
            description: '表示实体及其关系的图结构数据，支持多跳推理。'
        });
        
        // 添加关系
        try {
            addRelation('RAG', '向量搜索', { name: '基于', symmetric: false });
            addRelation('RAG', '知识图谱', { name: '可结合', symmetric: false });
            addRelation('向量搜索', '知识图谱', { name: '互补', symmetric: true });
        } catch (e) {
            // 忽略（节点可能未创建）
        }
    }
    
    // ===== 获取文档 =====
    function getDocument(id) {
        return documents[id] || null;
    }
    
    function getAllDocuments() {
        return Object.values(documents);
    }
    
    // ===== 删除文档 =====
    function deleteDocument(id) {
        if (!documents[id]) return false;
        
        delete documents[id];
        
        // 删除向量
        Object.keys(vectors).forEach(function(key) {
            if (key.startsWith(id + '_')) {
                delete vectors[key];
            }
        });
        
        saveToStorage();
        EventBus.emit('kb:document:deleted', { id: id });
        
        return true;
    }
    
    // ===== 获取图谱 =====
    function getGraph() {
        return Object.assign({}, graph);
    }
    
    function getNode(id) {
        return graph[id] || null;
    }
    
    // ===== 获取统计 =====
    function getStats() {
        return {
            documents: Object.keys(documents).length,
            vectors: Object.keys(vectors).length,
            nodes: Object.keys(graph).length,
            relations: Object.values(graph).reduce(function(sum, n) { return sum + n.connections.length; }, 0) / 2,
            indexSize: Object.keys(index).length
        };
    }
    
    // ===== 导出 API =====
    return {
        // 初始化
        init: init,
        
        // 文档管理
        addDocument: addDocument,
        getDocument: getDocument,
        getAllDocuments: getAllDocuments,
        deleteDocument: deleteDocument,
        
        // 搜索
        search: search,
        
        // 图谱
        addNode: addNode,
        addRelation: addRelation,
        findRelated: findRelated,
        searchGraph: searchGraph,
        getGraph: getGraph,
        getNode: getNode,
        
        // RAG
        generateContext: generateContext,
        enhanceWithRAG: enhanceWithRAG,
        
        // 工具
        tokenize: tokenize,
        cosineSimilarity: cosineSimilarity,
        
        // 统计
        getStats: getStats
    };
})();
