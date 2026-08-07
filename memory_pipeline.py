"""
SoloBrave Memory Pipeline - P0 实现
基于腾讯 Team Memory L0-L3 分层思路，实现 L0 结构化对话 + L1 事实提取 + Token 预算控制

层级映射：
  L0 memory_conversations  ← 结构化对话记录（扩展现有 memory 表能力）
  L1 memory_atoms          ← 从 L0 提取的事实原子（LLM 提取）
  L2 memory_scenes         ← 场景聚合（P1，预留表结构）
  L3 memory_personas       ← 长期画像（P2，预留表结构）
  token_budgets            ← Token 预算配置
  pipeline_state           ← Pipeline 调度状态
"""

import json
import hashlib
import time
import uuid
import sqlite3
import logging

logger = logging.getLogger(__name__)

# ============================================================
# 1. 建表
# ============================================================

def create_memory_tables(conn):
    """创建 L0-L3 记忆分层表 + Token 预算 + Pipeline 状态"""

    # L0: 结构化对话记录
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            turn_id INTEGER NOT NULL,
            user_content TEXT NOT NULL,
            assistant_content TEXT,
            tool_calls TEXT DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            metadata TEXT DEFAULT '{}'
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_agent_session ON memory_conversations(agent_id, session_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_created ON memory_conversations(created_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_agent_time ON memory_conversations(agent_id, created_at)')

    # L1: 事实原子（从 L0 提取）
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_atoms (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            source_conversation_id TEXT,
            atom_type TEXT NOT NULL,
            content TEXT NOT NULL,
            content_embedding TEXT,
            keywords TEXT DEFAULT '',
            scene_tag TEXT DEFAULT '',
            confidence REAL DEFAULT 0.8,
            dedup_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_atom_agent ON memory_atoms(agent_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_atom_type ON memory_atoms(atom_type)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_atom_scene ON memory_atoms(scene_tag)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_atom_dedup ON memory_atoms(dedup_hash)')

    # L2: 场景块（P1 预留）
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_scenes (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            scene_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            atom_ids TEXT NOT NULL DEFAULT '[]',
            keywords TEXT DEFAULT '',
            content_embedding TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scene_agent ON memory_scenes(agent_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scene_type ON memory_scenes(scene_type)')

    # L3: 长期画像（P2 预留）
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_personas (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            persona_type TEXT NOT NULL,
            content TEXT NOT NULL,
            scene_ids TEXT NOT NULL DEFAULT '[]',
            version INTEGER NOT NULL DEFAULT 1,
            backup_content TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_persona_agent ON memory_personas(agent_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_persona_type ON memory_personas(persona_type)')

    # Token 预算配置
    conn.execute('''
        CREATE TABLE IF NOT EXISTS token_budgets (
            id TEXT PRIMARY KEY,
            agent_id TEXT,
            budget_type TEXT NOT NULL,
            max_chars INTEGER NOT NULL,
            max_items INTEGER NOT NULL DEFAULT 5,
            score_threshold REAL NOT NULL DEFAULT 0.3,
            timeout_ms INTEGER NOT NULL DEFAULT 5000
        )
    ''')

    # 写入全局默认预算
    defaults = [
        ('default_total', None, 'total', 4000, 20, 0.3, 5000),
        ('default_l3', None, 'L3_persona', 500, 1, 0.3, 5000),
        ('default_skill', None, 'skill', 1000, 2, 0.3, 5000),
        ('default_l2', None, 'L2_scene', 1000, 3, 0.3, 5000),
        ('default_l1', None, 'L1_atom', 1500, 5, 0.3, 5000),
    ]
    for d in defaults:
        conn.execute(
            'INSERT OR IGNORE INTO token_budgets (id, agent_id, budget_type, max_chars, max_items, score_threshold, timeout_ms) VALUES (?,?,?,?,?,?,?)',
            d
        )

    # Pipeline 调度状态
    conn.execute('''
        CREATE TABLE IF NOT EXISTS pipeline_state (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            pipeline_type TEXT NOT NULL,
            last_run_at TEXT,
            next_run_at TEXT,
            conversation_count INTEGER DEFAULT 0,
            atom_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'idle',
            last_error TEXT
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_pipeline_agent ON pipeline_state(agent_id, pipeline_type)')

    conn.commit()
    logger.info('[MemoryPipeline] Tables created/verified')


# ============================================================
# 2. L0 对话记录
# ============================================================

def save_conversation(conn, agent_id, session_id, turn_id, user_content, assistant_content='', tool_calls=None):
    """保存一条 L0 对话记录"""
    conv_id = str(uuid.uuid4())
    conn.execute(
        '''INSERT INTO memory_conversations (id, agent_id, session_id, turn_id, user_content, assistant_content, tool_calls)
           VALUES (?,?,?,?,?,?,?)''',
        (conv_id, agent_id, session_id, turn_id, user_content, assistant_content, json.dumps(tool_calls or []))
    )
    conn.commit()

    # 更新 Pipeline 状态
    _increment_pipeline_counter(conn, agent_id, 'L1_extract', 'conversation_count')
    return conv_id


def get_recent_conversations(conn, agent_id, limit=5):
    """获取最近的 L0 对话记录"""
    rows = conn.execute(
        'SELECT * FROM memory_conversations WHERE agent_id=? ORDER BY created_at DESC LIMIT ?',
        (agent_id, limit)
    ).fetchall()
    return [_conv_row_to_dict(r) for r in rows]


def _conv_row_to_dict(row):
    if not row:
        return None
    cols = ['id', 'agent_id', 'session_id', 'turn_id', 'user_content', 'assistant_content', 'tool_calls', 'created_at', 'metadata']
    d = {c: row[i] for i, c in enumerate(cols)}
    d['tool_calls'] = json.loads(d['tool_calls']) if d['tool_calls'] else []
    d['metadata'] = json.loads(d['metadata']) if d.get('metadata') else {}
    return d


# ============================================================
# 3. L1 事实提取（LLM 调用）
# ============================================================

L1_EXTRACTION_PROMPT = """从以下对话中提取关于抖音团长达人撮合业务的事实原子。

提取三类记忆：
1. talent_fact: 达人事实（粉丝数、品类偏好、带货数据、报价区间、合作状态等）
2. business_constraint: 业务约束（预算上限、佣金比例、发货时效、退换货政策等）
3. decision_record: 决策记录（推荐/拒绝原因、撮合失败原因、调整策略等）

规则：
- 不提取 AI 助手自身输出
- 去重：相似内容只保留最新的一条
- 每次最多提取 10 条
- 每条包含 confidence (0-1)

对话内容：
{conversations}

请输出 JSON 数组，每条格式：
[{{"atom_type":"talent_fact","content":"达人XX粉丝3.9万，主攻鞋靴箱包品类","keywords":"达人XX,鞋靴,3.9万","scene_tag":"talent_mgmt","confidence":0.9}}]

如果没有可提取的事实，返回空数组 []"""


def extract_l1_atoms(conn, agent_id, conversations, llm_call_func=None):
    """
    L1 提取：从 L0 对话中提取事实原子
    llm_call_func: 外部传入的 LLM 调用函数，签名为 (prompt: str) -> str
    """
    if not conversations:
        return []

    if not llm_call_func:
        logger.warning('[MemoryPipeline] No LLM call function provided, skipping L1 extraction')
        return []

    # 格式化对话
    conv_text = '\n'.join([
        f"[{c['created_at']}] 用户: {c['user_content']}\nAI: {c.get('assistant_content', '')}"
        for c in conversations
    ])

    prompt = L1_EXTRACTION_PROMPT.format(conversations=conv_text)

    try:
        result = llm_call_func(prompt)
        atoms = _parse_llm_json(result)
    except Exception as e:
        logger.error(f'[MemoryPipeline] L1 extraction failed: {e}')
        _update_pipeline_error(conn, agent_id, 'L1_extract', str(e))
        return []

    if not atoms:
        logger.info(f'[MemoryPipeline] No atoms extracted from {len(conversations)} conversations')
        return []

    # 写入数据库
    new_atoms = []
    for atom in atoms[:10]:  # 最多10条
        content = atom.get('content', '').strip()
        if not content:
            continue

        dedup_hash = hashlib.md5(content.encode()).hexdigest()

        # 去重检查
        existing = conn.execute(
            'SELECT id FROM memory_atoms WHERE dedup_hash=? AND agent_id=?',
            (dedup_hash, agent_id)
        ).fetchone()
        if existing:
            continue

        atom_id = str(uuid.uuid4())
        keywords = atom.get('keywords', '')
        # 生成 embedding（如果有 embedding 函数）
        embedding = None
        # embedding = get_embedding(content)  # TODO: 接入 embedding 服务

        conn.execute(
            '''INSERT INTO memory_atoms (id, agent_id, source_conversation_id, atom_type, content, keywords, scene_tag, confidence, dedup_hash)
               VALUES (?,?,?,?,?,?,?,?,?)''',
            (atom_id, agent_id, conversations[0].get('id'), atom.get('atom_type', 'talent_fact'),
             content, keywords, atom.get('scene_tag', ''), atom.get('confidence', 0.8), dedup_hash)
        )
        new_atoms.append({**atom, 'id': atom_id})

    conn.commit()

    # 更新 Pipeline 状态
    if new_atoms:
        _increment_pipeline_counter(conn, agent_id, 'L2_aggregate', 'atom_count', len(new_atoms))

    # 更新 L1 提取状态
    _update_pipeline_run(conn, agent_id, 'L1_extract')

    logger.info(f'[MemoryPipeline] Extracted {len(new_atoms)} atoms for agent {agent_id}')
    return new_atoms


# ============================================================
# 4. RRF 混合召回
# ============================================================

def rrf_fusion(bm25_results, vector_results, k=60, max_results=5, score_threshold=0.3):
    """
    RRF (Reciprocal Rank Fusion) 融合 BM25 和向量搜索结果
    score = 1/(k + rank) for each result, then sum
    """
    scores = {}

    for rank, item in enumerate(bm25_results):
        doc_id = item.get('id') or item.get('atom_id')
        if doc_id:
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            scores.setdefault(f'{doc_id}__data', item)

    for rank, item in enumerate(vector_results):
        doc_id = item.get('id') or item.get('atom_id')
        if doc_id:
            scores[doc_id] = scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            scores.setdefault(f'{doc_id}__data', item)

    # 排序并过滤
    sorted_ids = sorted(
        [(k, v) for k, v in scores.items() if not k.endswith('__data')],
        key=lambda x: x[1], reverse=True
    )

    result = []
    for doc_id, score in sorted_ids:
        if score >= score_threshold:
            data = scores.get(f'{doc_id}__data', {})
            result.append({**data, 'id': doc_id, 'rrf_score': round(score, 4)})
        if len(result) >= max_results:
            break

    return result


def bm25_keyword_search(conn, agent_id, query, limit=10):
    """BM25 关键词搜索（使用 SQLite LIKE 模拟）"""
    keywords = query.strip().split()
    if not keywords:
        return []

    conditions = []
    params = [agent_id]
    for kw in keywords:
        conditions.append('(content LIKE ? OR keywords LIKE ?)')
        params.extend([f'%{kw}%', f'%{kw}%'])

    sql = f'''SELECT id, content, keywords, atom_type, scene_tag, confidence
              FROM memory_atoms WHERE agent_id=? AND ({" OR ".join(conditions)})
              ORDER BY created_at DESC LIMIT ?'''
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    return [
        {'id': r[0], 'content': r[1], 'keywords': r[2], 'atom_type': r[3],
         'scene_tag': r[4], 'confidence': r[5]}
        for r in rows
    ]


def vector_search(conn, agent_id, query_embedding, limit=10):
    """向量相似度搜索（使用 cosine similarity）"""
    rows = conn.execute(
        'SELECT id, content, keywords, atom_type, scene_tag, confidence, content_embedding FROM memory_atoms WHERE agent_id=? AND content_embedding IS NOT NULL',
        (agent_id,)
    ).fetchall()

    if not rows:
        return []

    results = []
    for r in rows:
        stored_embedding = r[6]
        if stored_embedding:
            try:
                stored_vec = json.loads(stored_embedding)
                similarity = _cosine_similarity(query_embedding, stored_vec)
                if similarity > 0.3:
                    results.append({
                        'id': r[0], 'content': r[1], 'keywords': r[2], 'atom_type': r[3],
                        'scene_tag': r[4], 'confidence': r[5], 'similarity': similarity
                    })
            except (json.JSONDecodeError, TypeError):
                continue

    results.sort(key=lambda x: x.get('similarity', 0), reverse=True)
    return results[:limit]


def _cosine_similarity(vec_a, vec_b):
    """计算余弦相似度"""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = sum(a * a for a in vec_a) ** 0.5
    norm_b = sum(b * b for b in vec_b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ============================================================
# 5. Token 预算控制召回
# ============================================================

def get_token_budget(conn, agent_id=None):
    """获取 Agent 的 Token 预算配置"""
    # 先查 Agent 专属配置
    if agent_id:
        rows = conn.execute(
            'SELECT budget_type, max_chars, max_items, score_threshold, timeout_ms FROM token_budgets WHERE agent_id=?',
            (agent_id,)
        ).fetchall()
        if rows:
            return {r[0]: {'max_chars': r[1], 'max_items': r[2], 'score_threshold': r[3], 'timeout_ms': r[4]} for r in rows}

    # 回退到全局默认
    rows = conn.execute(
        'SELECT budget_type, max_chars, max_items, score_threshold, timeout_ms FROM token_budgets WHERE agent_id IS NULL'
    ).fetchall()
    return {r[0]: {'max_chars': r[1], 'max_items': r[2], 'score_threshold': r[3], 'timeout_ms': r[4]} for r in rows}


def recall_with_budget(conn, agent_id, query, query_embedding=None, session_id=None):
    """
    带预算控制的召回流程
    返回: {prepend_context, append_system_context, memory_count, details}
    """
    budget = get_token_budget(conn, agent_id)
    total_budget = budget.get('total', {}).get('max_chars', 4000)
    l1_config = budget.get('L1_atom', {'max_chars': 1500, 'max_items': 5, 'score_threshold': 0.3})

    context_parts = {}
    total_chars = 0

    # Step 1: L3 画像注入（P2，暂跳过）
    persona_rows = conn.execute(
        'SELECT content FROM memory_personas WHERE agent_id=? ORDER BY created_at DESC LIMIT 1',
        (agent_id,)
    ).fetchall()
    if persona_rows:
        persona_text = persona_rows[0][0]
        persona_budget = budget.get('L3_persona', {'max_chars': 500})['max_chars']
        if len(persona_text) > persona_budget:
            persona_text = persona_text[:persona_budget] + '...'
        context_parts['persona'] = f'【Agent画像】\n{persona_text}'
        total_chars += len(persona_text)

    # Step 2: L2 场景匹配（P1，简单关键词匹配）
    l2_config = budget.get('L2_scene', {'max_chars': 1000, 'max_items': 3, 'score_threshold': 0.3})
    scene_rows = conn.execute(
        'SELECT id, title, summary, keywords FROM memory_scenes WHERE agent_id=? ORDER BY updated_at DESC LIMIT ?',
        (agent_id, l2_config['max_items'])
    ).fetchall()
    scenes = []
    for r in scene_rows:
        keywords = r[3] or ''
        if any(kw in query for kw in keywords.split(',')) if keywords else False:
            scenes.append({'id': r[0], 'title': r[1], 'summary': r[2]})

    if scenes:
        scene_text = '\n'.join([f'- {s["title"]}: {s["summary"][:100]}' for s in scenes])
        if len(scene_text) > l2_config['max_chars']:
            scene_text = scene_text[:l2_config['max_chars']] + '...'
        context_parts['scene'] = f'【工作场景】\n{scene_text}'
        total_chars += len(scene_text)

    # Step 3: L1 事实原子召回 (BM25 + 向量 + RRF)
    remaining_budget = total_budget - total_chars
    l1_budget = min(remaining_budget, l1_config['max_chars'])

    bm25_results = bm25_keyword_search(conn, agent_id, query, limit=l1_config['max_items'] * 2)

    vector_results = []
    if query_embedding:
        vector_results = vector_search(conn, agent_id, query_embedding, limit=l1_config['max_items'] * 2)

    # 如果只有 BM25 结果，直接用
    if bm25_results and not vector_results:
        rrf_results = bm25_results[:l1_config['max_items']]
    elif vector_results and not bm25_results:
        rrf_results = vector_results[:l1_config['max_items']]
    elif bm25_results and vector_results:
        rrf_results = rrf_fusion(
            bm25_results, vector_results,
            k=60, max_results=l1_config['max_items'],
            score_threshold=l1_config['score_threshold']
        )
    else:
        rrf_results = []

    if rrf_results:
        atom_lines = [f'- [{r.get("atom_type", "?")}] {r["content"]}' for r in rrf_results if r.get('content')]
        atom_text = '\n'.join(atom_lines)
        if len(atom_text) > l1_budget:
            atom_text = atom_text[:l1_budget] + '...'
        context_parts['atom'] = f'【相关记忆】\n{atom_text}'
        total_chars += len(atom_text)

    # 组装结果
    prepend = '\n\n'.join([v for k, v in context_parts.items() if k in ('persona', 'scene')])
    append_ctx = '\n\n'.join([v for k, v in context_parts.items() if k in ('atom',)])

    return {
        'prepend_context': prepend,
        'append_system_context': append_ctx,
        'memory_count': len(persona_rows) + len(scenes) + len(rrf_results),
        'details': {
            'persona_count': len(persona_rows),
            'scene_count': len(scenes),
            'atom_count': len(rrf_results),
            'total_chars': total_chars,
            'budget_used': f'{total_chars}/{total_budget}'
        }
    }


# ============================================================
# 6. Pipeline 调度
# ============================================================

PIPELINE_CONFIG = {
    'every_n_conversations': 5,       # 每5轮对话触发L1提取
    'l2_delay_seconds': 10,           # L1完成后延迟10s触发L2
    'l2_min_interval_seconds': 900,   # L2最小间隔15分钟
    'persona_trigger_every_n': 50,    # 每50条新原子触发L3画像
    'max_atoms_per_extraction': 10,   # 每次最多提取10条
}


def check_and_run_pipeline(conn, agent_id, llm_call_func=None):
    """
    检查 Pipeline 触发条件，按需执行 L1 提取
    在每次对话结束后调用
    """
    state = _get_or_init_pipeline_state(conn, agent_id, 'L1_extract')

    if state['status'] == 'running':
        return {'action': 'skipped', 'reason': 'pipeline_running'}

    config = PIPELINE_CONFIG
    if state['conversation_count'] >= config['every_n_conversations']:
        # 触发 L1 提取
        _set_pipeline_status(conn, agent_id, 'L1_extract', 'running')

        conversations = get_recent_conversations(conn, agent_id, limit=config['every_n_conversations'])
        atoms = extract_l1_atoms(conn, agent_id, conversations, llm_call_func)

        # 重置计数器
        _reset_pipeline_counter(conn, agent_id, 'L1_extract', 'conversation_count')
        _set_pipeline_status(conn, agent_id, 'L1_extract', 'idle')

        logger.info(f'[MemoryPipeline] L1 extraction completed: {len(atoms)} atoms')
        return {'action': 'L1_extracted', 'atoms_count': len(atoms)}

    return {
        'action': 'waiting',
        'conversation_count': state['conversation_count'],
        'threshold': config['every_n_conversations']
    }


# ============================================================
# 辅助函数
# ============================================================

def _get_or_init_pipeline_state(conn, agent_id, pipeline_type):
    """获取或初始化 Pipeline 状态"""
    row = conn.execute(
        'SELECT * FROM pipeline_state WHERE agent_id=? AND pipeline_type=?',
        (agent_id, pipeline_type)
    ).fetchone()
    if row:
        cols = ['id', 'agent_id', 'pipeline_type', 'last_run_at', 'next_run_at',
                'conversation_count', 'atom_count', 'status', 'last_error']
        return {c: row[i] for i, c in enumerate(cols)}
    else:
        state_id = str(uuid.uuid4())
        conn.execute(
            'INSERT INTO pipeline_state (id, agent_id, pipeline_type, conversation_count, atom_count, status) VALUES (?,?,?,?,?,?)',
            (state_id, agent_id, pipeline_type, 0, 0, 'idle')
        )
        conn.commit()
        return {'id': state_id, 'agent_id': agent_id, 'pipeline_type': pipeline_type,
                'last_run_at': None, 'next_run_at': None,
                'conversation_count': 0, 'atom_count': 0, 'status': 'idle', 'last_error': None}


def _increment_pipeline_counter(conn, agent_id, pipeline_type, counter_field, increment=1):
    """增加 Pipeline 计数器"""
    state = _get_or_init_pipeline_state(conn, agent_id, pipeline_type)
    new_val = state[counter_field] + increment
    conn.execute(
        f'UPDATE pipeline_state SET {counter_field}=? WHERE agent_id=? AND pipeline_type=?',
        (new_val, agent_id, pipeline_type)
    )
    conn.commit()


def _reset_pipeline_counter(conn, agent_id, pipeline_type, counter_field):
    """重置 Pipeline 计数器"""
    conn.execute(
        f'UPDATE pipeline_state SET {counter_field}=0 WHERE agent_id=? AND pipeline_type=?',
        (agent_id, pipeline_type)
    )
    conn.commit()


def _set_pipeline_status(conn, agent_id, pipeline_type, status, error=None):
    """设置 Pipeline 状态"""
    conn.execute(
        'UPDATE pipeline_state SET status=?, last_error=? WHERE agent_id=? AND pipeline_type=?',
        (status, error, agent_id, pipeline_type)
    )
    conn.commit()


def _update_pipeline_run(conn, agent_id, pipeline_type):
    """更新 Pipeline 上次运行时间"""
    conn.execute(
        'UPDATE pipeline_state SET last_run_at=datetime("now","localtime") WHERE agent_id=? AND pipeline_type=?',
        (agent_id, pipeline_type)
    )
    conn.commit()


def _update_pipeline_error(conn, agent_id, pipeline_type, error_msg):
    """记录 Pipeline 错误"""
    conn.execute(
        'UPDATE pipeline_state SET status=?, last_error=? WHERE agent_id=? AND pipeline_type=?',
        ('error', error_msg, agent_id, pipeline_type)
    )
    conn.commit()


def _parse_llm_json(result):
    """解析 LLM 返回的 JSON，容错处理"""
    if not result:
        return []

    # 尝试直接解析
    try:
        return json.loads(result)
    except json.JSONDecodeError:
        pass

    # 尝试提取 JSON 数组
    import re
    match = re.search(r'\[.*\]', result, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # 尝试提取 JSON 对象
    match = re.search(r'\{.*\}', result, re.DOTALL)
    if match:
        try:
            obj = json.loads(match.group())
            return [obj]
        except json.JSONDecodeError:
            pass

    logger.warning(f'[MemoryPipeline] Failed to parse LLM JSON: {result[:200]}')
    return []


# ============================================================
# 7. 查询接口（供 API 调用）
# ============================================================

def get_agent_atoms(conn, agent_id, atom_type=None, limit=50):
    """获取 Agent 的事实原子列表"""
    if atom_type:
        rows = conn.execute(
            'SELECT * FROM memory_atoms WHERE agent_id=? AND atom_type=? ORDER BY created_at DESC LIMIT ?',
            (agent_id, atom_type, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            'SELECT * FROM memory_atoms WHERE agent_id=? ORDER BY created_at DESC LIMIT ?',
            (agent_id, limit)
        ).fetchall()

    cols = ['id', 'agent_id', 'source_conversation_id', 'atom_type', 'content',
            'content_embedding', 'keywords', 'scene_tag', 'confidence', 'dedup_hash', 'created_at']
    return [{c: r[i] for i, c in enumerate(cols)} for r in rows]


def get_agent_conversations(conn, agent_id, limit=50):
    """获取 Agent 的对话历史"""
    rows = conn.execute(
        'SELECT * FROM memory_conversations WHERE agent_id=? ORDER BY created_at DESC LIMIT ?',
        (agent_id, limit)
    ).fetchall()
    return [_conv_row_to_dict(r) for r in rows]


def get_pipeline_status(conn, agent_id):
    """获取 Agent 的 Pipeline 状态"""
    rows = conn.execute(
        'SELECT * FROM pipeline_state WHERE agent_id=?',
        (agent_id,)
    ).fetchall()
    cols = ['id', 'agent_id', 'pipeline_type', 'last_run_at', 'next_run_at',
            'conversation_count', 'atom_count', 'status', 'last_error']
    return [{c: r[i] for i, c in enumerate(cols)} for r in rows]


def get_memory_stats(conn, agent_id=None):
    """获取记忆统计"""
    if agent_id:
        conv_count = conn.execute('SELECT COUNT(*) FROM memory_conversations WHERE agent_id=?', (agent_id,)).fetchone()[0]
        atom_count = conn.execute('SELECT COUNT(*) FROM memory_atoms WHERE agent_id=?', (agent_id,)).fetchone()[0]
        scene_count = conn.execute('SELECT COUNT(*) FROM memory_scenes WHERE agent_id=?', (agent_id,)).fetchone()[0]
        persona_count = conn.execute('SELECT COUNT(*) FROM memory_personas WHERE agent_id=?', (agent_id,)).fetchone()[0]
    else:
        conv_count = conn.execute('SELECT COUNT(*) FROM memory_conversations').fetchone()[0]
        atom_count = conn.execute('SELECT COUNT(*) FROM memory_atoms').fetchone()[0]
        scene_count = conn.execute('SELECT COUNT(*) FROM memory_scenes').fetchone()[0]
        persona_count = conn.execute('SELECT COUNT(*) FROM memory_personas').fetchone()[0]

    return {
        'L0_conversations': conv_count,
        'L1_atoms': atom_count,
        'L2_scenes': scene_count,
        'L3_personas': persona_count
    }
