#!/usr/bin/env python3
"""
Knowledge Service — 知识库分段向量化 + 全局公共 + 权限控制
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
    global DATA_DIR, DB_PATH, SETTINGS_FILE, AGENTS_FILE
    DATA_DIR = data_dir
    DB_PATH = os.path.join(DATA_DIR, 'solobrave.db')
    SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
    AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')


# 默认路径（未被 set_data_dir 设置时使用）
DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SETTINGS_FILE = os.path.join(DEFAULT_DATA_DIR, 'settings.json')
AGENTS_FILE = os.path.join(DEFAULT_DATA_DIR, 'agents.json')


# 环境变量覆盖（与 solobrave-server.py 保持一致）
EMBEDDING_OVERRIDE_PROVIDER = os.environ.get('SOLOBRAVE_EMBEDDING_PROVIDER', '').strip()
EMBEDDING_OVERRIDE_API_KEY = os.environ.get('SOLOBRAVE_EMBEDDING_API_KEY', '').strip()


def _read_json(filepath, default=None):
    """读取 JSON 文件，失败返回 default"""
    if not os.path.exists(filepath):
        return default if default is not None else {}
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def _get_agent_by_id(emp_id):
    """根据 emp_id 查找 agent 配置"""
    if not emp_id or not AGENTS_FILE:
        return None
    agents = _read_json(AGENTS_FILE, [])
    for a in agents:
        if a.get('id') == emp_id:
            return a
    return None


def get_embedding_config(emp_id=None):
    """
    获取 embedding 配置。
    优先级：环境变量 > settings.json 全局配置 > 员工 aiProvider/apiKey。
    返回: {'provider': str, 'apiKey': str, 'baseUrl': str, 'model': str}
    """
    settings = _read_json(SETTINGS_FILE, {})
    emb_settings = settings.get('embedding', {}) or {}

    provider = EMBEDDING_OVERRIDE_PROVIDER
    api_key = EMBEDDING_OVERRIDE_API_KEY

    if not provider:
        provider = (emb_settings.get('provider') or settings.get('embeddingProvider', '')).strip()
    if not api_key:
        api_key = (emb_settings.get('apiKey') or settings.get('embeddingApiKey', '')).strip()

    base_url = (emb_settings.get('baseUrl', '')).strip()
    model = (emb_settings.get('model', '')).strip()

    if emp_id:
        agent = _get_agent_by_id(emp_id) or {}
        if not provider:
            provider = (agent.get('aiProvider', '') or agent.get('apiProvider', '')).strip()
        if not api_key:
            api_key = (agent.get('apiKey') or '').strip()
        if not model:
            model = (agent.get('embeddingModel') or '').strip()

    provider = provider or 'openai'

    provider_cfg = EMBEDDING_PROVIDERS.get(provider)
    if provider_cfg:
        if not base_url:
            base_url = provider_cfg['url']
        if not model:
            model = provider_cfg['model']

    return {
        'provider': provider,
        'apiKey': api_key,
        'baseUrl': base_url,
        'model': model,
    }


def _db_conn(timeout=30):
    """获取 SQLite 数据库连接；启用 WAL 与 busy timeout 降低 database locked 概率"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute(f'PRAGMA busy_timeout={timeout * 1000};')
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
    'kimicode': {
        'url': 'https://api.kimi.com/coding/v1/embeddings',
        'model': 'kimi-for-coding',
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
        'model': 'BAAI/bge-large-zh-v1.5',
    },
}


def _get_embedding_provider_cfg(provider):
    return EMBEDDING_PROVIDERS.get(provider, EMBEDDING_PROVIDERS['openai'])


def get_embedding(text, api_key, provider='openai', model=None, base_url=None):
    """调用 Embedding API 获取向量，纯 urllib 实现"""
    import ssl
    import urllib.request
    if api_key and isinstance(api_key, str):
        api_key = api_key.strip()
    if not text or not text.strip() or not api_key:
        return None
    cfg = _get_embedding_provider_cfg(provider)
    target_url = base_url or cfg['url']
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = json.dumps({
        'input': text[:8000],
        'model': model or cfg['model'],
        'encoding_format': 'float',
    }).encode('utf-8')
    req = urllib.request.Request(target_url, data=body, headers=headers, method='POST')
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


def get_embedding_cached(text, api_key, provider, model=None, base_url=None):
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
    emb = get_embedding(text, api_key, provider, model=model, base_url=base_url)
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
        _add_column_if_not_exists(conn, 'knowledge', 'scope', "TEXT DEFAULT 'global'")
        _add_column_if_not_exists(conn, 'knowledge', 'team_id', "TEXT DEFAULT ''")
        # FIXME: 项目组维度改造：新增 group_ids 字段（JSON 数组字符串，支持一条知识属于多个项目组）
        _add_column_if_not_exists(conn, 'knowledge', 'group_ids', "TEXT DEFAULT '[]'")

        # 创建索引
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_emp ON knowledge(emp_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_status ON knowledge(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_created ON knowledge(created_at)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_scope ON knowledge(scope)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_team_id ON knowledge(team_id)')

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

        # 知识版本历史表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_versions (
                id TEXT PRIMARY KEY,
                knowledge_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT '',
                emp_id TEXT DEFAULT '',
                scope TEXT DEFAULT 'global',
                team_id TEXT DEFAULT '',
                status TEXT DEFAULT 'ok',
                chunk_count INTEGER DEFAULT 0,
                created_by TEXT,
                created_at INTEGER,
                UNIQUE(knowledge_id, version)
            )
        ''')
        # FIXME: 项目组维度改造：给历史版本表也添加 group_ids
        _add_column_if_not_exists(conn, 'knowledge_versions', 'group_ids', "TEXT DEFAULT '[]'")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_versions_kid ON knowledge_versions(knowledge_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_versions_created ON knowledge_versions(created_at)')

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

def _parse_group_ids(val):
    """FIXME: 项目组维度改造：安全解析 group_ids JSON 数组"""
    if not val:
        return []
    try:
        parsed = json.loads(val)
        return parsed if isinstance(parsed, list) else []
    except Exception:
        return []


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
        'scope': row['scope'] or 'global',
        'teamId': row['team_id'] or '',
        # FIXME: 项目组维度改造：返回 groupIds 数组
        'groupIds': _parse_group_ids(row['group_ids']),
        'status': row['status'] or 'ok',
        'chunkCount': row['chunk_count'] or 0,
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'icon': '📄',
        'linkedEmployees': [],
    }


def knowledge_create(title, content, category, emp_id, api_key=None, provider='openai', agent_config=None, model=None, base_url=None,
                     scope='global', team_id='', group_ids=None):
    """
    创建知识条目，自动分段 + 向量化。
    优先使用全局 embedding 配置（通过 get_embedding_config），不再依赖传入的 api_key/provider。
    如果全局未配置 api_key，只保存数据不生成向量（status='pending'）。
    emp_id 留空表示全局公共知识库；scope 支持 global / team / personal / group。
    """
    kid = _gen_id('kb')
    now = _now_ms()
    # 全局知识库使用空字符串
    actual_emp_id = emp_id or ''
    actual_scope = scope or 'global'
    actual_team_id = team_id or ''
    # FIXME: 项目组维度改造：统一 group_ids 为 JSON 字符串
    if group_ids is None:
        actual_group_ids = '[]'
    elif isinstance(group_ids, str):
        actual_group_ids = group_ids
    else:
        actual_group_ids = json.dumps(group_ids, ensure_ascii=False)

    # 使用全局 embedding 配置（不再依赖传入的 api_key/provider）
    emb_cfg = get_embedding_config(emp_id or None)
    api_key = emb_cfg['apiKey']
    provider = emb_cfg['provider']

    # agent 配置仅用于 chunkSize / chunkOverlap
    cfg = (agent_config or {}).get('knowledge', {})
    chunk_size = cfg.get('chunkSize', 500)
    overlap = cfg.get('chunkOverlap', 100)
    embedding_model = emb_cfg['model']

    conn = _db_conn()
    try:
        # FIXME: 项目组维度改造：插入时包含 group_ids
        conn.execute('''
            INSERT INTO knowledge (id, emp_id, title, content, category, scope, team_id, group_ids, status, chunk_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)
        ''', (kid, actual_emp_id, title, content, category or '', actual_scope, actual_team_id, actual_group_ids, now, now))
        conn.commit()
    finally:
        conn.close()

    # 分段（无论是否有 API key，先保存 chunks）
    try:
        _save_chunks_without_embedding(kid, actual_emp_id, content, chunk_size, overlap)
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
        _vectorize_chunks(kid, actual_emp_id, api_key, provider, embedding_model, base_url=emb_cfg['baseUrl'])
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


def _save_knowledge_version(kid, created_by=None):
    """把 knowledge 当前记录保存为历史版本"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        if not row:
            return None
        next_version = conn.execute(
            'SELECT COALESCE(MAX(version), 0) + 1 FROM knowledge_versions WHERE knowledge_id = ?',
            (kid,)
        ).fetchone()[0]
        vid = _gen_id('kbv')
        # FIXME: 项目组维度改造：历史版本也保存 group_ids
        conn.execute('''
            INSERT INTO knowledge_versions (id, knowledge_id, version, title, content, category,
                                            emp_id, scope, team_id, group_ids, status, chunk_count, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            vid, kid, next_version, row['title'], row['content'], row['category'] or '',
            row['emp_id'] or '', row['scope'] or 'global', row['team_id'] or '', row['group_ids'] or '[]',
            row['status'] or 'ok', row['chunk_count'] or 0,
            created_by or '', _now_ms()
        ))
        conn.commit()
        return vid
    finally:
        conn.close()


def knowledge_update(kid, title=None, content=None, category=None, emp_id=None,
                     api_key=None, provider='openai', agent_config=None, created_by=None,
                     model=None, base_url=None, scope=None, team_id=None, group_ids=None,
                     is_admin=False):
    """更新知识条目，内容变更时先保存历史版本，再重新分段+向量化；使用全局 embedding 配置。
    非 admin 禁止修改 scope=global 或 scope=team 的文档。"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        if not row:
            return None

        doc_scope = (row['scope'] or 'global')
        if not is_admin and doc_scope in ('global', 'team'):
            raise PermissionError('Permission denied: non-admin cannot update global/team knowledge')

        # 内容或标题变更时保留历史版本
        will_change_content = title is not None or content is not None or category is not None
        if will_change_content:
            _save_knowledge_version(kid, created_by=created_by)

        updates = {}
        if title is not None:
            updates['title'] = title
        if content is not None:
            updates['content'] = content
        if category is not None:
            updates['category'] = category
        if emp_id is not None:
            updates['emp_id'] = emp_id
        if scope is not None:
            updates['scope'] = scope
        if team_id is not None:
            updates['team_id'] = team_id
        # FIXME: 项目组维度改造：支持更新 group_ids
        if group_ids is not None:
            if isinstance(group_ids, str):
                updates['group_ids'] = group_ids
            else:
                updates['group_ids'] = json.dumps(group_ids, ensure_ascii=False)
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
            actual_emp_id = emp_id or row['emp_id'] or ''

            # 使用全局 embedding 配置（不再依赖传入的 api_key/provider）
            emb_cfg = get_embedding_config(actual_emp_id or None)
            api_key = emb_cfg['apiKey']
            provider = emb_cfg['provider']

            cfg = (agent_config or {}).get('knowledge', {})
            chunk_size = cfg.get('chunkSize', 500)
            overlap = cfg.get('chunkOverlap', 100)
            embedding_model = emb_cfg['model']

            # 先分段（无论是否有 API key）
            _save_chunks_without_embedding(kid, actual_emp_id, content, chunk_size, overlap)

            # 有 API key 再向量化
            if api_key:
                _vectorize_chunks(kid, actual_emp_id, api_key, provider, embedding_model, base_url=emb_cfg['baseUrl'])
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


def knowledge_delete(kid, is_admin=False):
    """删除知识条目，级联删除 chunks 和历史版本。
    非 admin 禁止删除 scope=global 或 scope=team 的文档。"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT scope FROM knowledge WHERE id = ?', (kid,)).fetchone()
        if not row:
            return False
        doc_scope = (row['scope'] or 'global')
        if not is_admin and doc_scope in ('global', 'team'):
            raise PermissionError('Permission denied: non-admin cannot delete global/team knowledge')
        conn.execute('DELETE FROM knowledge_chunks WHERE knowledge_id = ?', (kid,))
        conn.execute('DELETE FROM knowledge_versions WHERE knowledge_id = ?', (kid,))
        cur = conn.execute('DELETE FROM knowledge WHERE id = ?', (kid,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _knowledge_version_row_to_dict(row):
    """将 knowledge_versions 行转为前端兼容 dict"""
    if not row:
        return None
    return {
        'id': row['id'],
        'knowledgeId': row['knowledge_id'],
        'version': row['version'],
        'title': row['title'],
        'content': row['content'],
        'category': row['category'] or '',
        'empId': row['emp_id'] or '',
        'scope': row['scope'] or 'global',
        'teamId': row['team_id'] or '',
        # FIXME: 项目组维度改造：历史版本返回 groupIds
        'groupIds': _parse_group_ids(row['group_ids']),
        'status': row['status'] or 'ok',
        'chunkCount': row['chunk_count'] or 0,
        'createdBy': row['created_by'] or '',
        'createdAt': row['created_at'],
    }


def knowledge_get_versions(kid, offset=0, limit=50):
    """获取知识文档的历史版本列表"""
    conn = _db_conn()
    try:
        rows = conn.execute('''
            SELECT * FROM knowledge_versions WHERE knowledge_id = ?
            ORDER BY version DESC LIMIT ? OFFSET ?
        ''', (kid, limit, offset)).fetchall()
        total = conn.execute(
            'SELECT COUNT(*) FROM knowledge_versions WHERE knowledge_id = ?', (kid,)
        ).fetchone()[0]
        return {
            'versions': [_knowledge_version_row_to_dict(r) for r in rows],
            'total': total,
            'offset': offset,
            'limit': limit
        }
    finally:
        conn.close()


def knowledge_get_version(kid, version):
    """获取知识文档某一具体版本"""
    conn = _db_conn()
    try:
        row = conn.execute('''
            SELECT * FROM knowledge_versions WHERE knowledge_id = ? AND version = ?
        ''', (kid, version)).fetchone()
        return _knowledge_version_row_to_dict(row)
    finally:
        conn.close()


def knowledge_rollback(kid, version, api_key=None, provider='openai', agent_config=None, created_by=None,
                       model=None, base_url=None, is_admin=False):
    """回滚知识文档到指定历史版本；embedding 配置由 knowledge_update 内部获取"""
    target = knowledge_get_version(kid, version)
    if not target:
        return None
    # 获取当前文档 emp_id，用于 embedding 配置查找
    current = knowledge_get_by_id(kid)
    emp_id = current.get('empId') if current else ''
    # 先保存当前版本
    _save_knowledge_version(kid, created_by=created_by)
    # 用历史内容更新主表
    return knowledge_update(
        kid=kid,
        title=target['title'],
        content=target['content'],
        category=target['category'],
        emp_id=emp_id,
        api_key=api_key,
        provider=provider,
        agent_config=agent_config,
        created_by=created_by,
        model=model,
        base_url=base_url,
        is_admin=is_admin
    )


def knowledge_get_by_id(kid):
    """获取单条知识详情"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        return _knowledge_row_to_dict(row)
    finally:
        conn.close()


def _group_ids_where_clause(user_group_ids):
    """FIXME: 项目组维度改造：生成 group_ids JSON 数组的交集过滤条件"""
    if not user_group_ids:
        return None, []
    placeholders = ', '.join('?' for _ in user_group_ids)
    return f"EXISTS (SELECT 1 FROM json_each(group_ids) WHERE value IN ({placeholders}))", list(user_group_ids)


def knowledge_list(offset=0, limit=50, category=None, keyword=None, allowed_categories=None,
                   scope=None, team_id=None, user_id=None, is_admin=False, user_team_ids=None,
                   emp_id=None, user_group_ids=None, emp_ids=None):
    """
    知识列表（支持分页、分类筛选、关键词搜索、四层隔离：global/team/personal/group）。
    scope: all/global/team/personal/group；None 默认按可读权限返回全部。
    emp_id 为旧参数：空字符串等价于 global，非空等价于 personal(user_id=emp_id)。
    emp_ids 为新参数：允许传入多个 emp_id（如 [user_id] + 用户创建的 agent ids），用于 personal scope 过滤。
    """
    if user_group_ids is None:
        user_group_ids = []
    if emp_ids is None:
        emp_ids = []
    # 兼容旧 user_id 参数：把 user_id 合并进 emp_ids
    if user_id and user_id not in emp_ids:
        emp_ids = list(emp_ids) + [user_id]
    conn = _db_conn()
    try:
        where = ['status = ?']
        params = ['ok']

        # 兼容旧 emp_id 参数
        if emp_id is not None and scope is None:
            if emp_id == '' or emp_id is None:
                scope = 'global'
            else:
                scope = 'personal'
                if emp_id not in emp_ids:
                    emp_ids = list(emp_ids) + [emp_id]

        # 四层隔离过滤（global / team / personal / group）
        if scope == 'global':
            where.append("(scope IS NULL OR scope = 'global')")
        elif scope == 'personal':
            where.append("scope = 'personal'")
            if emp_ids:
                placeholders = ', '.join('?' for _ in emp_ids)
                where.append(f'emp_id IN ({placeholders})')
                params.extend(emp_ids)
        elif scope == 'team':
            where.append("scope = 'team'")
            if team_id:
                where.append('team_id = ?')
                params.append(team_id)
            elif user_team_ids:
                placeholders = ', '.join('?' for _ in user_team_ids)
                where.append(f'team_id IN ({placeholders})')
                params.extend(user_team_ids)
            elif not is_admin:
                where.append('1 = 0')
        elif scope == 'group':
            # FIXME: 项目组维度改造：scope='group' 时过滤 group_ids 与用户所属项目组有交集的知识
            where.append("scope = 'group'")
            if not is_admin:
                group_clause, group_params = _group_ids_where_clause(user_group_ids)
                if group_clause:
                    where.append(group_clause)
                    params.extend(group_params)
                else:
                    where.append('1 = 0')
        else:
            # 默认：按可读权限返回（admin 可读写 global；普通用户可读 global + 自己的 personal + 所在 group）
            if not is_admin:
                readable = ["(scope IS NULL OR scope = 'global')"]
                if emp_ids:
                    placeholders = ', '.join('?' for _ in emp_ids)
                    readable.append(f"(scope = 'personal' AND emp_id IN ({placeholders}))")
                    params.extend(emp_ids)
                group_clause, group_params = _group_ids_where_clause(user_group_ids)
                if group_clause:
                    readable.append(f"(scope = 'group' AND {group_clause})")
                    params.extend(group_params)
                where.append('(' + ' OR '.join(readable) + ')')

        if category:
            where.append('category = ?')
            params.append(category)
        # 分类权限过滤（allowed_categories=None 表示不限制；['*'] 表示全部）
        if allowed_categories is not None and '*' not in allowed_categories:
            if allowed_categories:
                placeholders = ', '.join('?' for _ in allowed_categories)
                where.append(f'category IN ({placeholders})')
                params.extend(allowed_categories)
            else:
                where.append('1 = 0')
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


def _vectorize_chunks(kid, emp_id, api_key, provider, embedding_model, base_url=None):
    """为已存在的 chunks 生成 embedding，并记录模型名。
    调用 Embedding API 期间不持有数据库连接，避免长时间占用写锁。"""
    import struct
    # 1. 读取待向量化的 chunks，立即释放读锁
    conn = _db_conn()
    try:
        rows = conn.execute(
            'SELECT id, chunk_index, content FROM knowledge_chunks WHERE knowledge_id = ? AND embedding IS NULL',
            (kid,)
        ).fetchall()
    finally:
        conn.close()

    # 2. 调用 Embedding API（此阶段不持有数据库锁）
    updates = []
    for row in rows:
        emb = get_embedding_cached(row['content'], api_key, provider, embedding_model, base_url=base_url)
        if emb is None:
            raise RuntimeError(f'chunk {row["chunk_index"]} embedding failed')
        emb_bytes = struct.pack(f'{len(emb)}f', *emb)
        updates.append((emb_bytes, embedding_model, row['id']))

    # 3. 批量写入 embedding，缩短写锁持有时间
    if updates:
        conn = _db_conn()
        try:
            conn.executemany(
                'UPDATE knowledge_chunks SET embedding = ?, embedding_model = ? WHERE id = ?',
                updates
            )
            conn.commit()
        finally:
            conn.close()


def _rechunk_and_vectorize(kid, emp_id, content, api_key, provider,
                           embedding_model, chunk_size, overlap, base_url=None):
    """
    删除旧 chunks，重新分段并生成 embedding。
    失败时抛出异常，但旧 chunks 已被删除。
    """
    _save_chunks_without_embedding(kid, emp_id, content, chunk_size, overlap)
    _vectorize_chunks(kid, emp_id, api_key, provider, embedding_model, base_url=base_url)


# ═══════════════════════════════════════════════════
# RAG 检索
# ═══════════════════════════════════════════════════

def rag_retrieve(query, emp_id, api_key=None, provider='openai', agent_config=None, top_k_docs=3, allowed_categories=None,
                 model=None, base_url=None, requester_id=None, is_admin=False, team_ids=None, group_ids=None, emp_ids=None):
    """
    RAG 检索：在 knowledge_chunks 中搜索，返回关联的原始文档。
    支持四层隔离：global 全员可读，personal 仅自己，team 仅团队成员可读，group 仅项目组成员可读。
    使用全局 embedding 配置，不再依赖传入的 api_key/provider。
    整个函数被 try-catch 保护，出错时降级返回空结果，避免拖垮主流程。
    emp_ids 允许传入多个 emp_id（用户自身 id 及其创建的 agent ids）。
    """
    if group_ids is None:
        group_ids = []
    if emp_ids is None:
        emp_ids = []
    if requester_id and requester_id not in emp_ids:
        emp_ids = list(emp_ids) + [requester_id]
    try:
        if isinstance(query, list):
            text_parts = [item.get('text', '') for item in query if isinstance(item, dict) and item.get('type') == 'text']
            query = ''.join(text_parts)
        if not query or not query.strip():
            return {'docs': [], 'context': ''}

        # 使用全局 embedding 配置
        emb_cfg = get_embedding_config(emp_id or None)
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        embedding_model = emb_cfg['model']
        base_url = emb_cfg['baseUrl']
        if not api_key:
            return {'docs': [], 'context': ''}

        # 1. 获取 query embedding
        query_emb = get_embedding_cached(query, api_key, provider, embedding_model, base_url=base_url)
        if not query_emb:
            return {'docs': [], 'context': ''}

        # 2. 语义搜索结果缓存（加入分类权限 + 请求者身份影响 key）
        query_hash = hashlib.md5(query.encode('utf-8')).hexdigest()
        cats_hash = ''
        if allowed_categories is not None:
            cats_hash = hashlib.md5(json.dumps(allowed_categories, sort_keys=True).encode()).hexdigest()[:8]
        scope_hash = hashlib.md5(f'{requester_id}:{is_admin}:{sorted(team_ids or [])}:{sorted(group_ids or [])}'.encode()).hexdigest()[:8]
        cache_key = _rag_cache_key(emp_id, query_hash, top_k_docs, embedding_model + ':' + cats_hash + ':' + scope_hash)
        cached = _rag_cache_get(cache_key, ttl=300)
        if cached is not None:
            return cached

        # 3. 在 chunks 中计算余弦相似度（带三层隔离 + 模型隔离 + 分类权限）
        import struct
        q_vec = query_emb

        where_clauses = ["k.status = 'ok'", "c.embedding_model = ?", "c.embedding IS NOT NULL"]
        sql_params = [embedding_model]

        # 四层隔离过滤（未提供 requester_id 时不限制，保持兼容）
        # employee 可读 global 文档
        if requester_id is not None and not is_admin:
            readable = ["(k.scope IS NULL OR k.scope = 'global')"]
            if emp_ids:
                placeholders = ', '.join('?' for _ in emp_ids)
                readable.append(f"(k.scope = 'personal' AND k.emp_id IN ({placeholders}))")
                sql_params.extend(emp_ids)
            if group_ids:
                placeholders = ', '.join('?' for _ in group_ids)
                readable.append(f"(k.scope = 'group' AND EXISTS (SELECT 1 FROM json_each(k.group_ids) WHERE value IN ({placeholders})))")
                sql_params.extend(group_ids)
            where_clauses.append('(' + ' OR '.join(readable) + ')')

        if allowed_categories is not None and '*' not in allowed_categories:
            if allowed_categories:
                placeholders = ', '.join('?' for _ in allowed_categories)
                where_clauses.append(f'k.category IN ({placeholders})')
                sql_params.extend(allowed_categories)
            else:
                where_clauses.append('1 = 0')

        conn = _db_conn()
        try:
            rows = conn.execute(f'''
                SELECT k.id, k.title, k.category, k.content, k.emp_id, k.status, k.scope, k.team_id,
                       c.chunk_index, c.content as chunk_content, c.embedding
                FROM knowledge_chunks c
                JOIN knowledge k ON c.knowledge_id = k.id
                WHERE {' AND '.join(where_clauses)}
            ''', tuple(sql_params)).fetchall()
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

def knowledge_search_fallback(query, emp_id=None, limit=3, requester_id=None, is_admin=False, team_ids=None, group_ids=None, emp_ids=None):
    """API 失败时的关键词搜索 fallback（LIKE）；带四层隔离"""
    if group_ids is None:
        group_ids = []
    if emp_ids is None:
        emp_ids = []
    if requester_id and requester_id not in emp_ids:
        emp_ids = list(emp_ids) + [requester_id]
    if isinstance(query, list):
        text_parts = [item.get('text', '') for item in query if isinstance(item, dict) and item.get('type') == 'text']
        query = ''.join(text_parts)
    if not query or not query.strip():
        return []
    keyword = f'%{query}%'
    conn = _db_conn()
    try:
        where = ["status = 'ok'", "(title LIKE ? OR content LIKE ?)"]
        params = [keyword, keyword]
        # employee 可读 global 文档
        if requester_id is not None and not is_admin:
            readable = ["(scope IS NULL OR scope = 'global')"]
            if emp_ids:
                placeholders = ', '.join('?' for _ in emp_ids)
                readable.append(f"(scope = 'personal' AND emp_id IN ({placeholders}))")
                params.extend(emp_ids)
            if group_ids:
                placeholders = ', '.join('?' for _ in group_ids)
                readable.append(f"(scope = 'group' AND EXISTS (SELECT 1 FROM json_each(group_ids) WHERE value IN ({placeholders})))")
                params.extend(group_ids)
            where.append('(' + ' OR '.join(readable) + ')')
        sql = '''
            SELECT id, title, content, category, scope, team_id, emp_id, created_at, updated_at
            FROM knowledge
            WHERE ''' + ' AND '.join(where) + '''
            ORDER BY updated_at DESC
            LIMIT ?
        '''
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        return [_knowledge_row_to_dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════
# 语义搜索（供 MS3 和 API 使用）
# ═══════════════════════════════════════════════════

def knowledge_search_semantic(query, emp_id, api_key=None, provider='openai', agent_config=None, limit=3, allowed_categories=None,
                              model=None, base_url=None,
                              requester_id=None, is_admin=False, team_ids=None, group_ids=None, emp_ids=None):
    """
    语义检索，带 fallback；使用全局 embedding 配置，不再依赖传入的 api_key/provider。
    供 MS3 inject_memories 和 API 搜索使用。
    emp_ids 允许传入多个 emp_id（用户自身 id 及其创建的 agent ids）。
    """
    if group_ids is None:
        group_ids = []
    if emp_ids is None:
        emp_ids = []
    if requester_id and requester_id not in emp_ids:
        emp_ids = list(emp_ids) + [requester_id]
    try:
        # 使用全局 embedding 配置
        emb_cfg = get_embedding_config(emp_id or None)
        result = rag_retrieve(query, emp_id, emb_cfg['apiKey'], emb_cfg['provider'], agent_config,
                              top_k_docs=limit, allowed_categories=allowed_categories,
                              model=emb_cfg['model'], base_url=emb_cfg['baseUrl'],
                              requester_id=requester_id, is_admin=is_admin, team_ids=team_ids, group_ids=group_ids, emp_ids=emp_ids)
        return result.get('docs', [])
    except Exception as e:
        print(f'  [KnowledgeSearch] semantic failed, fallback to keyword: {e}', flush=True)
        return knowledge_search_fallback(query, emp_id, limit, requester_id=requester_id, is_admin=is_admin, team_ids=team_ids, group_ids=group_ids, emp_ids=emp_ids)


# ═══════════════════════════════════════════════════
# 权限检查
# ═══════════════════════════════════════════════════

def check_knowledge_permission(requester_id, target_emp_id, requester_role=None):
    """检查 requester 是否有权限访问知识库；兼容旧版按 emp_id 判断"""
    if requester_role == 'admin':
        return True
    if not target_emp_id:
        return True
    return requester_id == target_emp_id


def _doc_scope(doc):
    """从 doc dict 获取 scope（兼容缺失 scope 的旧数据）"""
    return (doc.get('scope') or 'global') if doc else 'global'


def _has_any(items_a, items_b):
    """FIXME: 项目组维度改造：判断两个列表是否有交集"""
    if not items_a or not items_b:
        return False
    return bool(set(items_a) & set(items_b))


def can_read_knowledge(doc, user_id=None, is_admin=False, user_team_ids=None, user_group_ids=None, emp_ids=None):
    """判断用户是否可读某条知识
    global / team scope 对 employee 只读开放；personal 仅自己；group 需所属项目组有交集。
    emp_ids 允许传入多个 emp_id（包含用户自身 id 及其创建的 agent ids）。
    """
    if emp_ids is None:
        emp_ids = []
    if user_id and user_id not in emp_ids:
        emp_ids = list(emp_ids) + [user_id]
    scope = _doc_scope(doc)
    if scope == 'global':
        return True
    if scope == 'personal':
        return is_admin or doc.get('empId') in emp_ids
    if scope == 'team':
        team_id = doc.get('teamId')
        return is_admin or (user_team_ids and team_id in user_team_ids)
    if scope == 'group':
        # 项目组维度：group 知识与用户所属项目组有交集即可读
        return is_admin or _has_any(doc.get('groupIds') or [], user_group_ids)
    return False


def can_edit_knowledge(doc, user_id=None, is_admin=False, managed_team_ids=None, managed_group_ids=None, emp_ids=None):
    """判断用户是否可编辑/移动某条知识（group 维度仅群主/管理员可编辑）"""
    if emp_ids is None:
        emp_ids = []
    if user_id and user_id not in emp_ids:
        emp_ids = list(emp_ids) + [user_id]
    scope = _doc_scope(doc)
    if scope == 'global':
        return is_admin
    if scope == 'personal':
        return is_admin or doc.get('empId') in emp_ids
    if scope == 'team':
        team_id = doc.get('teamId')
        return is_admin or (managed_team_ids and team_id in managed_team_ids)
    if scope == 'group':
        # FIXME: 项目组维度改造：管理员或管理的项目组有交集即可编辑
        return is_admin or _has_any(doc.get('groupIds') or [], managed_group_ids)
    return False


def can_delete_knowledge(doc, user_id=None, is_admin=False, managed_team_ids=None, managed_group_ids=None, user_group_ids=None, emp_ids=None):
    """判断用户是否可删除某条知识（group 维度项目组成员或管理员可删除）"""
    if emp_ids is None:
        emp_ids = []
    if user_id and user_id not in emp_ids:
        emp_ids = list(emp_ids) + [user_id]
    scope = _doc_scope(doc)
    if scope == 'global':
        return is_admin
    if scope == 'personal':
        return is_admin or doc.get('empId') in emp_ids
    if scope == 'team':
        team_id = doc.get('teamId')
        return is_admin or (managed_team_ids and team_id in managed_team_ids)
    if scope == 'group':
        # FIXME: 项目组维度改造：管理员、群主或项目组成员均可删除
        return is_admin or _has_any(doc.get('groupIds') or [], managed_group_ids) or _has_any(doc.get('groupIds') or [], user_group_ids)
    return False


def can_create_knowledge(scope, user_id=None, is_admin=False, team_id=None, user_team_ids=None, managed_team_ids=None,
                         group_ids=None, user_group_ids=None, managed_group_ids=None, emp_id=None, emp_ids=None):
    """判断用户是否可在指定 scope 下新建知识
    personal scope 下 emp_id 必须在 emp_ids 中（用户自身或其创建的 agent）。
    """
    if emp_ids is None:
        emp_ids = []
    if user_id and user_id not in emp_ids:
        emp_ids = list(emp_ids) + [user_id]
    if scope == 'global':
        return is_admin
    if scope == 'personal':
        return is_admin or (emp_id in emp_ids)
    if scope == 'team':
        if is_admin:
            return True
        # 团队成员即可在所在团队创建；严格编辑权限可由 managed_team_ids 控制
        if managed_team_ids and team_id in managed_team_ids:
            return True
        if user_team_ids and team_id in user_team_ids:
            return True
        return False
    if scope == 'group':
        # FIXME: 项目组维度改造：管理员或目标 group_ids 与用户管理的项目组有交集即可创建
        if is_admin:
            return True
        if managed_group_ids and _has_any(group_ids or [], managed_group_ids):
            return True
        if user_group_ids and _has_any(group_ids or [], user_group_ids):
            return True
        return False
    return False


def knowledge_move(kid, scope, team_id='', group_ids=None, moved_by=''):
    """移动知识到指定 scope/team/group_ids，并保存历史版本"""
    doc = knowledge_get_by_id(kid)
    if not doc:
        return None
    actual_scope = scope or 'global'
    actual_team_id = team_id or ''
    # FIXME: 项目组维度改造：移动时支持设置 group_ids
    if group_ids is None:
        actual_group_ids = '[]'
    elif isinstance(group_ids, str):
        actual_group_ids = group_ids
    else:
        actual_group_ids = json.dumps(group_ids, ensure_ascii=False)
    _save_knowledge_version(kid, created_by=moved_by)
    conn = _db_conn()
    try:
        now = _now_ms()
        conn.execute(
            'UPDATE knowledge SET scope=?, team_id=?, group_ids=?, updated_at=? WHERE id=?',
            (actual_scope, actual_team_id, actual_group_ids, now, kid)
        )
        conn.commit()
    finally:
        conn.close()
    return knowledge_get_by_id(kid)


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

            # v3：旧版按员工隔离的知识库统一迁移为全局公共知识库（按 title 去重）
            conn = _db_conn()
            try:
                existing = conn.execute(
                    'SELECT id FROM knowledge WHERE (emp_id IS NULL OR emp_id = ?) AND title=?',
                    ('', title)
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
                knowledge_create(title, content, category, '', api_key, provider, agent_config)
                migrated += 1
                print(f'  [Migrate] Migrated: {title}', flush=True)
            except Exception as e:
                print(f'  [Migrate] Failed to migrate {title}: {e}', flush=True)
                # 即使向量化失败，数据也已导入（status='error'）
                pass

    print(f'  [Migrate] Total migrated: {migrated}', flush=True)
    return migrated
