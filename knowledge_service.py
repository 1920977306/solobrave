#!/usr/bin/env python3
"""
Knowledge Service — 知识库分段向量化 + 员工隔离 + 权限控制
=============================================================
被 solobrave-server.py 和 memory_service_v3.py 共用，避免循环导入。
"""

import json
import os
import uuid
import time
import math
import sqlite3
import hashlib

# ═══════════════════════════════════════════════════
# 配置（与 solobrave-server.py 共享 DATA_DIR）
# ═══════════════════════════════════════════════════

# 数据目录由调用方设置
DATA_DIR = None
DB_PATH = None

def set_data_dir(data_dir):
    """设置数据目录（由 solobrave-server.py 启动时调用）"""
    global DATA_DIR, DB_PATH
    DATA_DIR = data_dir
    DB_PATH = os.path.join(DATA_DIR, 'solobrave.db')


def _db_conn():
    """获取 SQLite 数据库连接"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now_ms():
    return int(time.time() * 1000)


def _gen_id(prefix='kb'):
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════
# Embedding API（复用 solobrave-server.py 配置，但独立实现）
# ═══════════════════════════════════════════════════

# 约定维度：当前固定使用 text-embedding-3-small（1536 维）
# EMBEDDING_DIM 仅作为标记/约定，不强制截断/补 0
# 不同模型的向量语义空间完全不同，严禁混用
# 真要换模型时，需写脚本遍历所有 chunk 重新生成向量
EMBEDDING_DIM = 1536

EMBEDDING_PROVIDERS = {
    'openai': {
        'url': 'https://api.openai.com/v1/embeddings',
        'model': 'text-embedding-3-small',
    },
    'kimi': {
        'url': 'https://api.moonshot.cn/v1/embeddings',
        'model': 'moonshot-v3-embedding',
    },
    'moonshot': {
        'url': 'https://api.moonshot.cn/v1/embeddings',
        'model': 'moonshot-v3-embedding',
    },
    'zhipu': {
        'url': 'https://open.bigmodel.cn/api/paas/v4/embeddings',
        'model': 'embedding-2',
    },
    'deepseek': {
        'url': 'https://api.deepseek.com/v1/embeddings',
        'model': 'text-embedding',
    },
    'siliconflow': {
        'url': 'https://api.siliconflow.cn/v1/embeddings',
        'model': 'BAAI/bge-m3',
    },
}


def _get_embedding_provider_cfg(provider):
    return EMBEDDING_PROVIDERS.get(provider, EMBEDDING_PROVIDERS['openai'])


def get_embedding(text, api_key, provider='openai', model=None):
    """调用 Embedding API 获取向量，纯 urllib 实现"""
    import ssl
    import urllib.request
    if api_key and isinstance(api_key, str):
        api_key = api_key.strip()
    if not text or not text.strip() or not api_key:
        return None
    cfg = _get_embedding_provider_cfg(provider)
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = json.dumps({
        'input': text[:8000],
        'model': model or cfg['model'],
        'encoding_format': 'float',
    }).encode('utf-8')
    req = urllib.request.Request(cfg['url'], data=body, headers=headers, method='POST')
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('data') and len(data['data']) > 0:
            emb = data['data'][0].get('embedding')
            if emb and isinstance(emb, list):
                return emb
    return None


def cosine_similarity(a, b):
    """纯 Python 计算余弦相似度（支持 list 或 bytes）"""
    if not a or not b:
        return 0.0
    if isinstance(a, bytes):
        import struct
        a = struct.unpack(f'{len(a)//4}f', a)
    if isinstance(b, bytes):
        import struct
        b = struct.unpack(f'{len(b)//4}f', b)
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ═══════════════════════════════════════════════════
# Embedding 缓存（MD5(content) 做 key）
# ═══════════════════════════════════════════════════

def _get_embedding_cached(content_hash, model):
    """从 SQLite 缓存读取 embedding"""
    conn = _db_conn()
    try:
        row = conn.execute(
            'SELECT embedding FROM embedding_cache WHERE content_hash = ? AND model = ?',
            (content_hash, model)
        ).fetchone()
        return row['embedding'] if row else None
    finally:
        conn.close()


def _save_embedding_cache(content_hash, embedding_bytes, model):
    """保存 embedding 到 SQLite 缓存"""
    conn = _db_conn()
    try:
        conn.execute('''
            INSERT OR REPLACE INTO embedding_cache (content_hash, embedding, model, created_at)
            VALUES (?, ?, ?, ?)
        ''', (content_hash, embedding_bytes, model, _now_ms()))
        conn.commit()
    finally:
        conn.close()


def get_embedding_cached(text, api_key, provider, model=None):
    """
    获取 embedding，带 MD5 缓存。
    返回 float list（非 bytes），维度由模型决定（当前约定 1536 维）。
    """
    if not text or not text.strip() or not api_key:
        return None
    cfg = _get_embedding_provider_cfg(provider)
    model = model or cfg['model']
    content_hash = hashlib.md5(text.encode('utf-8')).hexdigest()

    # 查缓存
    cached_bytes = _get_embedding_cached(content_hash, model)
    if cached_bytes:
        import struct
        return list(struct.unpack(f'{len(cached_bytes)//4}f', cached_bytes))

    # 调用 API
    emb = get_embedding(text, api_key, provider, model)
    if emb:
        import struct
        emb_bytes = struct.pack(f'{len(emb)}f', *emb)
        _save_embedding_cache(content_hash, emb_bytes, model)
        return emb
    return None


# ═══════════════════════════════════════════════════
# 语义搜索结果缓存（内存，5 分钟 TTL）
# ═══════════════════════════════════════════════════

_rag_cache = {}

def _rag_cache_key(emp_id, query_hash, top_k, model):
    return f"rag:{emp_id}:{query_hash}:{top_k}:{model}"


def _rag_cache_get(cache_key, ttl=300):
    """获取缓存结果，ttl 单位秒"""
    entry = _rag_cache.get(cache_key)
    if entry:
        result, expire_at = entry
        if time.time() < expire_at:
            return result
        # 过期，删除
        del _rag_cache[cache_key]
    return None


def _rag_cache_set(cache_key, result, ttl=300):
    _rag_cache[cache_key] = (result, time.time() + ttl)


def _rag_cache_clear():
    """清理过期缓存"""
    now = time.time()
    expired = [k for k, (_, exp) in _rag_cache.items() if now > exp]
    for k in expired:
        del _rag_cache[k]


# ═══════════════════════════════════════════════════
# 分段策略（段落感知 + 固定长度混合）
# ═══════════════════════════════════════════════════

def chunk_text(text, chunk_size=500, overlap=100):
    """
    段落感知 + 固定长度混合分段。
    chunk_size: 目标片段大小（字符数）
    overlap: 片段间重叠字符数
    """
    if not text:
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]

    # 先按段落切分（\n\n 优先，其次 \n）
    paragraphs = text.split('\n\n')
    if len(paragraphs) <= 1:
        paragraphs = text.split('\n')

    chunks = []
    current = ''

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        plen = len(para)

        # 段落长度在目标范围内，直接作为一个 chunk
        if chunk_size * 0.5 <= plen <= chunk_size * 1.6:
            if current:
                chunks.append(current.strip())
                current = ''
            chunks.append(para)
            continue

        # 段落太短，尝试合并
        if plen < chunk_size * 0.5:
            if current and len(current) + 1 + plen <= chunk_size * 1.2:
                current += '\n\n' + para
            else:
                if current:
                    chunks.append(current.strip())
                current = para
            continue

        # 段落太长，按固定长度切分
        if current:
            chunks.append(current.strip())
            current = ''

        start = 0
        while start < plen:
            end = min(start + chunk_size, plen)
            piece = para[start:end]
            chunks.append(piece)
            start += chunk_size - overlap
            if start >= plen:
                break

    if current:
        chunks.append(current.strip())

    # 过滤空 chunk
    return [c for c in chunks if c.strip()]


# ═══════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════

def init_db():
    """初始化数据库，创建所有知识库相关表"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _db_conn()
    try:
        # 主表（先创建基础结构）
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT '',
                embedding TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')

        # 兼容：给旧表添加新字段（必须先于索引创建）
        _add_column_if_not_exists(conn, 'knowledge', 'emp_id', "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, 'knowledge', 'status', "TEXT DEFAULT 'ok'")
        _add_column_if_not_exists(conn, 'knowledge', 'chunk_count', "INTEGER DEFAULT 0")

        # 创建索引
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_emp ON knowledge(emp_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_created ON knowledge(created_at)')

        # 分段表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id TEXT PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                emp_id TEXT NOT NULL,
                chunk_index INTEGER,
                content TEXT NOT NULL,
                embedding BLOB,
                embedding_model TEXT DEFAULT '',
                created_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_kid ON knowledge_chunks(knowledge_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_emp ON knowledge_chunks(emp_id)')

        # 兼容：给旧 chunks 表添加 embedding_model 字段
        _add_column_if_not_exists(conn, 'knowledge_chunks', 'embedding_model', "TEXT DEFAULT ''")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chunks_model ON knowledge_chunks(embedding_model)')

        # embedding 缓存表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS embedding_cache (
                content_hash TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding BLOB NOT NULL,
                created_at INTEGER,
                PRIMARY KEY (content_hash, model)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_cache_model ON embedding_cache(model)')

        conn.commit()
        print('  [KnowledgeService] DB initialized', flush=True)
    finally:
        conn.close()


def _add_column_if_not_exists(conn, table, column, def_type):
    """安全地添加列（如果列不存在）"""
    try:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {def_type}')
    except sqlite3.OperationalError:
        pass  # 列已存在


# ═══════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════

def _knowledge_row_to_dict(row):
    """将 sqlite3.Row 转为前端兼容 dict"""
    if not row:
        return None
    return {
        'id': row['id'],
        'empId': row['emp_id'],
        'title': row['title'],
        'name': row['title'],  # 兼容旧前端
        'content': row['content'],
        'category': row['category'] or '',
        'status': row['status'] or 'ok',
        'chunkCount': row['chunk_count'] or 0,
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'icon': '📄',
        'linkedEmployees': [],
    }


def knowledge_create(title, content, category, emp_id, api_key, provider, agent_config=None):
    """
    创建知识条目，自动分段 + 向量化。
    agent_config 用于读取 chunkSize / chunkOverlap / embeddingModel。
    如果 api_key 为空，只保存数据不生成向量（status='pending'）。
    """
    kid = _gen_id('kb')
    now = _now_ms()

    # agent 配置
    cfg = (agent_config or {}).get('knowledge', {})
    chunk_size = cfg.get('chunkSize', 500)
    overlap = cfg.get('chunkOverlap', 100)
    embedding_model = cfg.get('embeddingModel', 'text-embedding-3-small')

    conn = _db_conn()
    try:
        conn.execute('''
            INSERT INTO knowledge (id, emp_id, title, content, category, status, chunk_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?)
        ''', (kid, emp_id, title, content, category or '', now, now))
        conn.commit()
    finally:
        conn.close()

    # 分段（无论是否有 API key，先保存 chunks）
    try:
        _save_chunks_without_embedding(kid, emp_id, content, chunk_size, overlap)
    except Exception as e:
        print(f'  [Knowledge] chunking failed: {e}', flush=True)
        conn = _db_conn()
        try:
            conn.execute('UPDATE knowledge SET status="error" WHERE id=?', (kid,))
            conn.commit()
        finally:
            conn.close()
        raise

    # 没有 API key，只分段不生成向量
    if not api_key:
        print(f'  [Knowledge] No API key, chunked without embedding: {title}', flush=True)
        return knowledge_get_by_id(kid)

    # 向量化
    try:
        _vectorize_chunks(kid, emp_id, api_key, provider, embedding_model)
        conn = _db_conn()
        try:
            conn.execute('UPDATE knowledge SET status="ok" WHERE id=?', (kid,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f'  [Knowledge] vectorize failed: {e}', flush=True)
        conn = _db_conn()
        try:
            conn.execute('UPDATE knowledge SET status="error" WHERE id=?', (kid,))
            conn.commit()
        finally:
            conn.close()
        raise

    return knowledge_get_by_id(kid)


def knowledge_update(kid, title=None, content=None, category=None, emp_id=None,
                     api_key=None, provider='openai', agent_config=None):
    """更新知识条目，内容变更时重新分段+向量化"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        if not row:
            return None

        updates = {}
        if title is not None:
            updates['title'] = title
        if content is not None:
            updates['content'] = content
        if category is not None:
            updates['category'] = category
        updates['updated_at'] = _now_ms()

        if not updates:
            return _knowledge_row_to_dict(row)

        fields = ', '.join(f'{k} = ?' for k in updates.keys())
        values = list(updates.values()) + [kid]
        conn.execute(f'UPDATE knowledge SET {fields} WHERE id = ?', values)
        conn.commit()
    finally:
        conn.close()

    # 内容变更时重新分段+向量化
    if content is not None:
        try:
            cfg = (agent_config or {}).get('knowledge', {})
            chunk_size = cfg.get('chunkSize', 500)
            overlap = cfg.get('chunkOverlap', 100)
            embedding_model = cfg.get('embeddingModel', 'text-embedding-3-small')
            actual_emp_id = emp_id or row['emp_id']

            # 先分段（无论是否有 API key）
            _save_chunks_without_embedding(kid, actual_emp_id, content, chunk_size, overlap)

            # 有 API key 再向量化
            if api_key:
                _vectorize_chunks(kid, actual_emp_id, api_key, provider, embedding_model)
                conn = _db_conn()
                try:
                    conn.execute('UPDATE knowledge SET status="ok" WHERE id=?', (kid,))
                    conn.commit()
                finally:
                    conn.close()
            else:
                print(f'  [Knowledge] No API key, re-chunked without embedding: {kid}', flush=True)
        except Exception as e:
            print(f'  [Knowledge] update chunk/vectorize failed: {e}', flush=True)
            conn = _db_conn()
            try:
                conn.execute('UPDATE knowledge SET status="error" WHERE id=?', (kid,))
                conn.commit()
            finally:
                conn.close()
            raise

    return knowledge_get_by_id(kid)


def knowledge_delete(kid):
    """删除知识条目，级联删除 chunks"""
    conn = _db_conn()
    try:
        conn.execute('DELETE FROM knowledge_chunks WHERE knowledge_id = ?', (kid,))
        cur = conn.execute('DELETE FROM knowledge WHERE id = ?', (kid,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def knowledge_get_by_id(kid):
    """获取单条知识详情"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        return _knowledge_row_to_dict(row)
    finally:
        conn.close()


def knowledge_list(offset=0, limit=50, category=None, keyword=None, emp_id=None):
    """知识列表（支持分页、分类筛选、关键词搜索、员工隔离）"""
    conn = _db_conn()
    try:
        where = []
        params = []
        if emp_id:
            where.append('emp_id = ?')
            params.append(emp_id)
        if category:
            where.append('category = ?')
            params.append(category)
        if keyword:
            where.append('(title LIKE ? OR content LIKE ?)')
            like = f'%{keyword}%'
            params.extend([like, like])

        sql = 'SELECT * FROM knowledge'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY updated_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        docs = [_knowledge_row_to_dict(r) for r in rows]

        # 总数
        count_sql = 'SELECT COUNT(*) FROM knowledge'
        count_params = []
        if where:
            count_sql += ' WHERE ' + ' AND '.join(where)
            count_params = params[:-2]  # 去掉 limit 和 offset
        total = conn.execute(count_sql, count_params).fetchone()[0]

        return {'docs': docs, 'total': total, 'offset': offset, 'limit': limit}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════
# 分段 + 向量化（核心）
# ═══════════════════════════════════════════════════

def _save_chunks_without_embedding(kid, emp_id, content, chunk_size, overlap):
    """分段并保存 chunks（embedding 为 NULL），用于无 API key 场景"""
    # 1. 删除旧 chunks
    conn = _db_conn()
    try:
        conn.execute('DELETE FROM knowledge_chunks WHERE knowledge_id = ?', (kid,))
        conn.commit()
    finally:
        conn.close()

    # 2. 分段
    chunks = chunk_text(content, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        conn = _db_conn()
        try:
            conn.execute('UPDATE knowledge SET chunk_count=0 WHERE id=?', (kid,))
            conn.commit()
        finally:
            conn.close()
        return

    # 3. 保存 chunks（无 embedding）
    conn = _db_conn()
    try:
        for i, chunk_content in enumerate(chunks):
            conn.execute('''
                INSERT INTO knowledge_chunks (id, knowledge_id, emp_id, chunk_index, content, embedding, created_at)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
            ''', (f'{kid}_c{i}', kid, emp_id, i, chunk_content, _now_ms()))
        conn.execute('UPDATE knowledge SET chunk_count=? WHERE id=?', (len(chunks), kid))
        conn.commit()
    finally:
        conn.close()


def _vectorize_chunks(kid, emp_id, api_key, provider, embedding_model):
    """为已存在的 chunks 生成 embedding，并记录模型名"""
    import struct
    conn = _db_conn()
    try:
        rows = conn.execute(
            'SELECT id, chunk_index, content FROM knowledge_chunks WHERE knowledge_id = ? AND embedding IS NULL',
            (kid,)
        ).fetchall()
        for row in rows:
            emb = get_embedding_cached(row['content'], api_key, provider, embedding_model)
            if emb is None:
                raise RuntimeError(f'chunk {row["chunk_index"]} embedding failed')
            emb_bytes = struct.pack(f'{len(emb)}f', *emb)
            conn.execute(
                'UPDATE knowledge_chunks SET embedding = ?, embedding_model = ? WHERE id = ?',
                (emb_bytes, embedding_model, row['id'])
            )
        conn.commit()
    finally:
        conn.close()


def _rechunk_and_vectorize(kid, emp_id, content, api_key, provider,
                           embedding_model, chunk_size, overlap):
    """
    删除旧 chunks，重新分段并生成 embedding。
    失败时抛出异常，但旧 chunks 已被删除。
    """
    _save_chunks_without_embedding(kid, emp_id, content, chunk_size, overlap)
    _vectorize_chunks(kid, emp_id, api_key, provider, embedding_model)


# ═══════════════════════════════════════════════════
# RAG 检索
# ═══════════════════════════════════════════════════

def rag_retrieve(query, emp_id, api_key, provider, agent_config=None, top_k_docs=3):
    """
    RAG 检索：在 knowledge_chunks 中搜索，返回关联的原始文档。
    带员工隔离和 5 分钟缓存。
    整个函数被 try-catch 保护，出错时降级返回空结果，避免拖垮主流程。
    """
    try:
        if isinstance(query, list):
            text_parts = [item.get('text', '') for item in query if isinstance(item, dict) and item.get('type') == 'text']
            query = ''.join(text_parts)
        if not query or not query.strip() or not api_key:
            return {'docs': [], 'context': ''}

        cfg = (agent_config or {}).get('knowledge', {})
        provider_cfg = EMBEDDING_PROVIDERS.get(provider, EMBEDDING_PROVIDERS['openai'])
        embedding_model = cfg.get('embeddingModel', provider_cfg['model'])

        # 1. 获取 query embedding
        query_emb = get_embedding_cached(query, api_key, provider, embedding_model)
        if not query_emb:
            return {'docs': [], 'context': ''}

        # 2. 语义搜索结果缓存
        query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()
        cache_key = _rag_cache_key(emp_id, query_hash, top_k_docs, embedding_model)
        cached = _rag_cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached

        # 3. 在 chunks 中计算余弦相似度（带员工隔离 + 模型隔离）
        import struct
        q_vec = query_emb

        conn = _db_conn()
        try:
            rows = conn.execute('''
                SELECT k.id, k.title, k.category, k.content, k.emp_id, k.status,
                       c.chunk_index, c.content as chunk_content, c.embedding
                FROM knowledge_chunks c
                JOIN knowledge k ON c.knowledge_id = k.id
                WHERE c.emp_id = ? AND k.status = 'ok' AND c.embedding_model = ? AND c.embedding IS NOT NULL
            ''', (emp_id, embedding_model)).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            try:
                chunk_emb = struct.unpack(f'{len(row["embedding"])//4}f', row['embedding'])
                sim = cosine_similarity(q_vec, chunk_emb)
                if sim > 0.0:
                    results.append({
                        'knowledge_id': row['id'],
                        'title': row['title'],
                        'category': row['category'],
                        'full_content': row['content'],
                        'chunk_index': row['chunk_index'],
                        'chunk_content': row['chunk_content'],
                        'similarity': sim
                    })
            except Exception:
                continue

        # 4. 按 chunk 相似度排序，取 Top-N
        results.sort(key=lambda x: x['similarity'], reverse=True)
        top_chunks = results[:top_k_docs * 2]

        # 5. 按 knowledge_id 聚合（去重）
        seen = set()
        docs = []
        for r in top_chunks:
            if r['knowledge_id'] not in seen:
                seen.add(r['knowledge_id'])
                docs.append({
                    'id': r['knowledge_id'],
                    'title': r['title'],
                    'category': r['category'],
                    'content': r['full_content'],
                    'relevantChunk': r['chunk_content'],
                    'similarity': r['similarity']
                })
                if len(docs) >= top_k_docs:
                    break

        # 6. 格式化上下文
        context = format_rag_context(docs)

        result = {'docs': docs, 'context': context}
        _rag_cache_set(cache_key, result, ttl=300)
        return result
    except Exception as e:
        print(f'  [RAG] rag_retrieve 异常: {e}', flush=True)
        return {'docs': [], 'context': ''}


def format_rag_context(docs):
    """将检索结果格式化为注入 system prompt 的文本"""
    lines = []
    if docs:
        lines.append('【知识库文档】')
        for d in docs:
            content = (d.get('relevantChunk') or d.get('content') or '')[:1200]
            lines.append(f"━━━ 📄 {d.get('title', '未命名')} ━━━")
            lines.append(content)
            if len(d.get('content', '')) > 1200:
                lines.append('...（内容已截取）')
            lines.append('')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════
# Fallback：关键词搜索
# ═══════════════════════════════════════════════════

def knowledge_search_fallback(query, emp_id, limit=3):
    """API 失败时的关键词搜索 fallback（LIKE）"""
    if isinstance(query, list):
        text_parts = [item.get('text', '') for item in query if isinstance(item, dict) and item.get('type') == 'text']
        query = ''.join(text_parts)
    if not query or not query.strip():
        return []
    keyword = f'%{query}%'
    conn = _db_conn()
    try:
        rows = conn.execute('''
            SELECT id, title, content, category, created_at, updated_at
            FROM knowledge
            WHERE emp_id = ? AND status = 'ok' AND (title LIKE ? OR content LIKE ?)
            ORDER BY updated_at DESC
            LIMIT ?
        ''', (emp_id, keyword, keyword, limit)).fetchall()
        return [_knowledge_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════
# 语义搜索（供 MS3 和 API 使用）
# ═══════════════════════════════════════════════════

def knowledge_search_semantic(query, emp_id, api_key, provider, agent_config=None, limit=3):
    """
    语义检索，带 fallback。
    供 MS3 inject_memories 和 API 搜索使用。
    """
    try:
        result = rag_retrieve(query, emp_id, api_key, provider, agent_config, top_k_docs=limit)
        return result.get('docs', [])
    except Exception as e:
        print(f'  [KnowledgeSearch] semantic failed, fallback to keyword: {e}', flush=True)
        return knowledge_search_fallback(query, emp_id, limit)


# ═══════════════════════════════════════════════════
# 权限检查
# ═══════════════════════════════════════════════════

def check_knowledge_permission(requester_id, target_emp_id, requester_role=None):
    """检查 requester 是否有权限访问 target_emp_id 的知识库"""
    if requester_role == 'admin':
        return True
    return requester_id == target_emp_id


# ═══════════════════════════════════════════════════
# 旧数据迁移（幂等）
# ═══════════════════════════════════════════════════

def knowledge_migrate_from_json(data_dir, get_agent_config_fn):
    """
    从旧版 JSON 知识库迁移到 SQLite（启动时调用）。
    幂等：按 emp_id + title 去重，重复执行不会重复导入。
    自动触发分段和向量化。
    """
    kb_dir = os.path.join(data_dir, 'knowledge')
    if not os.path.exists(kb_dir):
        return 0

    migrated = 0
    for emp_id in os.listdir(kb_dir):
        emp_kb_dir = os.path.join(kb_dir, emp_id)
        docs_file = os.path.join(emp_kb_dir, 'docs.json')
        if not os.path.exists(docs_file):
            continue

        docs = []
        try:
            with open(docs_file, 'r', encoding='utf-8') as f:
                docs = json.load(f).get('docs', [])
        except Exception:
            continue

        for doc in docs:
            title = doc.get('title') or doc.get('name', '未命名')
            content = doc.get('content', '')
            category = doc.get('category', '')

            # 幂等：检查是否已存在（按 title + emp_id 去重）
            conn = _db_conn()
            try:
                existing = conn.execute(
                    'SELECT id FROM knowledge WHERE emp_id=? AND title=?',
                    (emp_id, title)
                ).fetchone()
                if existing:
                    print(f'  [Migrate] Skip existing: {title}', flush=True)
                    continue
            finally:
                conn.close()

            # 获取 agent 配置（用于 chunkSize / embeddingModel）
            agent_config = None
            api_key = None
            provider = 'openai'
            try:
                agent_config = get_agent_config_fn(emp_id)
                api_key = agent_config.get('apiKey')
                provider = agent_config.get('aiProvider', 'openai')
            except Exception:
                pass

            # 导入（自动触发分段和向量化）
            try:
                knowledge_create(title, content, category, emp_id, api_key, provider, agent_config)
                migrated += 1
                print(f'  [Migrate] Migrated: {title}', flush=True)
            except Exception as e:
                print(f'  [Migrate] Failed to migrate {title}: {e}', flush=True)
                # 即使向量化失败，数据也已导入（status='error'）
                pass

    print(f'  [Migrate] Total migrated: {migrated}', flush=True)
    return migrated
