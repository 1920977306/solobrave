/**
 * Solo Brave Modules - Knowledge Base
 * 
 * 知识库系统模块化实现
 */

var KnowledgeModule = (function() {
    'use strict';
    
    // ===== 模块信息 =====
    var moduleInfo = {
        name: 'KnowledgeModule',
        version: '1.0.0',
        description: '向量搜索 + 知识图谱'
    };
    
    // ===== 存储键 =====
    var STORAGE_KEYS = {
        DOCUMENTS: 'kb_documents',
        VECTORS: 'kb_vectors',
        GRAPH: 'kb_graph',
        INDEX: 'kb_index'
    };
    
    // ===== 存储 =====
    var documents = {};
    var vectors = {};
    var graph = {};
    var index = {};
    
    // ===== 配置 =====
    var config = {
        embeddingModel: 'tfidf',
        vectorDim: 128,
        maxDocuments: 1000,
        similarityThreshold: 0.7,
        chunkSize: 500,
        chunkOverlap: 50
    };
    
    // ===== TF-IDF 向量生成 =====
    function generateTFIDF(text) {
        var words = tokenize(text);
        
        // 计算词频
        var tf = {};
        words.forEach(function(w) { tf[w] = (tf[w] || 0) + 1; });
        
        // 归一化
        var maxFreq = Math.max.apply(null, Object.values(tf).filter(Boolean)) || 1;
        Object.keys(tf).forEach(function(w) { tf[w] = tf[w] / maxFreq; });
        
        // 简化的 IDF
        var idf = {};
        Object.keys(tf).forEach(function(w) { idf[w] = 1.0; });
        
        // TF-IDF
        var tfidf = {};
        Object.keys(tf).forEach(function(w) { tfidf[w] = tf[w] * idf[w]; });
        
        // 转固定维度向量
        var vector = new Array(config.vectorDim).fill(0);
        var vocab = Object.keys(tfidf).slice(0, config.vectorDim);
        vocab.forEach(function(word, i) { vector[i] = tfidf[word]; });
        
        // L2 归一化
        var norm = Math.sqrt(vector.reduce(function(sum, v) { return sum + v * v; }, 0));
        if (norm > 0) { vector = vector.map(function(v) { return v / norm; }); }
        
        return vector;
    }
    
    // ===== 分词 =====
    function tokenize(text) {
        var words = [];
        
        // 英文
        var enWords = text.match(/[a-zA-Z]+/g) || [];
        words = words.concat(enWords.map(function(w) { return w.toLowerCase(); }));
        
        // 中文 N-gram
        var zhChars = text.match(/[\u4e00-\u9fff]/g) || [];
        for (var i = 0; i < zhChars.length; i++) {
            if (i + 1 < zhChars.length) words.push(zhChars[i] + zhChars[i + 1]);
            if (i + 2 < zhChars.length) words.push(zhChars[i] + zhChars[i + 1] + zhChars[i + 2]);
        }
        
        // 停用词
        var stopWords = ['的', '了', '是', '在', '和', '就', '都', '而', '及', '与', '着', '或'];
        words = words.filter(function(w) { return stopWords.indexOf(w) === -1 && w.length >= 2; });
        
        return words;
    }
    
    // ===== 余弦相似度 =====
    function cosineSimilarity(a, b) {
        if (a.length !== b.length) return 0;
        
        var dotProduct = 0, normA = 0, normB = 0;
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
        var sentences = text.split(/[。！？\n]+/);
        var current = '';
        
        sentences.forEach(function(sentence) {
            if (current.length + sentence.length > size) {
                if (current.length > 0) { chunks.push(current.trim()); }
                current = current.slice(-overlap) + sentence;
            } else {
                current += sentence;
            }
        });
        
        if (current.length > 0) { chunks.push(current.trim()); }
        return chunks;
    }
    
    // ===== 添加文档 =====
    function addDocument(doc) {
        var id = doc.id || 'doc_' + Date.now() + '_' + Math.random().toString(36).substr(2, 6);
        var chunks = chunkText(doc.content);
        
        var document = {
            id: id,
            title: doc.title || 'Untitled',
            content: doc.content,
            chunks: chunks,
            metadata: doc.metadata || {},
            createdAt: Date.now(),
            updatedAt: Date.now(),
            tags: doc.tags || [],
            category: doc.category || 'general'
        };
        
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
        var words = tokenize(doc.title + ' ' + doc.content);
        words.forEach(function(word) {
            if (!index[word]) index[word] = [];
            if (index[word].indexOf(id) === -1) index[word].push(id);
        });
        
        saveToStorage();
        EventBus.emit('kb:document:added', { id: id, document: document });
        
        return document;
    }
    
    // ===== 搜索 =====
    function search(query, options) {
        options = options || {};
        var limit = options.limit || 10;
        var threshold = options.threshold || config.similarityThreshold;
        var searchType = options.type || 'hybrid';
        
        var results = [];
        
        // 向量搜索
        if (searchType === 'vector' || searchType === 'hybrid') {
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
        
        // 关键词搜索
        if (searchType === 'keyword' || searchType === 'hybrid') {
            var queryWords = tokenize(query);
            queryWords.forEach(function(word) {
                if (index[word]) {
                    index[word].forEach(function(docId) {
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
        
        // 去重排序
        var seen = {};
        results = results.filter(function(r) {
            var key = r.documentId + '_' + r.chunkIndex;
            if (seen[key]) return false;
            seen[key] = true;
            return true;
        });
        
        results.sort(function(a, b) { return b.similarity - a.similarity; });
        results = results.slice(0, limit);
        
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
        
        var graphNode = {
            id: id,
            type: node.type || 'entity',
            name: node.name || '',
            description: node.description || '',
            properties: node.properties || {},
            connections: node.connections || [],
            embedding: node.embedding || generateTFIDF(node.name + ' ' + node.description)
        };
        
        graph[id] = graphNode;
        saveToStorage();
        EventBus.emit('kb:graph:node:added', graphNode);
        
        return graphNode;
    }
    
    // ===== 知识图谱 - 添加关系 =====
    function addRelation(sourceId, targetId, relation, weight) {
        weight = weight || 1.0;
        
        var source = graph[sourceId];
        var target = graph[targetId];
        
        if (!source || !target) {
            throw new Exception.NotFoundException('图谱节点');
        }
        
        source.connections.push({
            targetId: targetId,
            relation: relation,
            weight: weight
        });
        
        if (!relation.symmetric) {
            target.connections.push({
                targetId: sourceId,
                relation: relation,
                weight: weight
            });
        }
        
        saveToStorage();
        return { source: sourceId, target: targetId, relation: relation };
    }
    
    // ===== 知识图谱 - 搜索 =====
    function searchGraph(query) {
        var queryVector = generateTFIDF(query);
        var results = [];
        
        Object.values(graph).forEach(function(node) {
            if (node.embedding) {
                var similarity = cosineSimilarity(queryVector, node.embedding);
                if (similarity > 0.3) {
                    results.push({ node: node, similarity: similarity });
                }
            }
        });
        
        results.sort(function(a, b) { return b.similarity - a.similarity; });
        return results.slice(0, 20);
    }
    
    // ===== 知识图谱 - 查找相关 =====
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
            
            results.push({ node: node, level: current.level });
            
            node.connections.forEach(function(conn) {
                if (!visited[conn.targetId]) {
                    queue.push({ id: conn.targetId, level: current.level + 1 });
                }
            });
        }
        
        return results;
    }
    
    // ===== RAG - 生成上下文 =====
    function generateContext(query, options) {
        options = options || {};
        var maxLength = options.maxLength || 2000;
        
        var docs = search(query, { limit: 5, threshold: 0.5 });
        var nodes = searchGraph(query);
        
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
    
    // ===== RAG - 增强 =====
    function enhanceWithRAG(query, basePrompt) {
        var rag = generateContext(query);
        
        if (rag.sources === 0) return basePrompt;
        
        return basePrompt + '\n\n## 参考知识\n请结合以下知识回答问题：\n\n' + rag.context;
    }
    
    // ===== 存储 =====
    function saveToStorage() {
        Store.set(STORAGE_KEYS.DOCUMENTS, documents);
        Store.set(STORAGE_KEYS.VECTORS, vectors);
        Store.set(STORAGE_KEYS.GRAPH, graph);
        Store.set(STORAGE_KEYS.INDEX, index);
    }
    
    function loadFromStorage() {
        var docs = Store.get(STORAGE_KEYS.DOCUMENTS);
        var vecs = Store.get(STORAGE_KEYS.VECTORS);
        var g = Store.get(STORAGE_KEYS.GRAPH);
        var idx = Store.get(STORAGE_KEYS.INDEX);
        
        if (docs) documents = docs;
        if (vecs) vectors = vecs;
        if (g) graph = g;
        if (idx) index = idx;
    }
    
    // ===== 初始化 =====
    function init(options) {
        Object.assign(config, options || {});
        loadFromStorage();
        
        if (Object.keys(documents).length === 0) {
            addSampleData();
        }
        
        console.log('[KnowledgeModule] Initialized:', Object.keys(documents).length, 'docs,', Object.keys(graph).length, 'nodes');
        
        return getStats();
    }
    
    // ===== 示例数据 =====
    function addSampleData() {
        addDocument({
            title: 'Solo Brave 系统介绍',
            content: 'Solo Brave 是一个 AI 原生 IM 系统，支持多渠道消息收发，内置记忆系统和知识库。',
            tags: ['系统', 'AI', 'IM'],
            category: 'system'
        });
        
        addDocument({
            title: '工具系统使用指南',
            content: '工具系统提供文件操作、命令执行、网页搜索、代码运行等功能。',
            tags: ['工具', '使用'],
            category: 'docs'
        });
        
        addNode({
            type: 'concept',
            name: 'RAG',
            description: 'Retrieval-Augmented Generation，检索增强生成。'
        });
        
        addNode({
            type: 'concept',
            name: '向量搜索',
            description: '将文本转换为向量，通过余弦相似度进行语义匹配。'
        });
    }
    
    // ===== 获取 =====
    function getDocument(id) { return documents[id] || null; }
    function getAllDocuments() { return Object.values(documents); }
    function getGraph() { return Object.assign({}, graph); }
    function getStats() {
        return {
            documents: Object.keys(documents).length,
            vectors: Object.keys(vectors).length,
            nodes: Object.keys(graph).length,
            relations: Object.values(graph).reduce(function(sum, n) { return sum + n.connections.length; }, 0) / 2
        };
    }
    
    // ===== 导出 API =====
    return {
        info: moduleInfo,
        init: init,
        
        // 文档
        addDocument: addDocument,
        getDocument: getDocument,
        getAllDocuments: getAllDocuments,
        
        // 搜索
        search: search,
        
        // 图谱
        addNode: addNode,
        addRelation: addRelation,
        searchGraph: searchGraph,
        findRelated: findRelated,
        getGraph: getGraph,
        
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

           