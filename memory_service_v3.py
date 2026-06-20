#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SoloBrave 记忆服务 v3
===================
基于 data/memories/ 目录结构的记忆管理模块。
物理隔离活跃记忆与归档记忆，支持归纳日志、优先级、标签等增强字段。

目录结构
--------
data/memories/
├── {empId}/
│   ├── memory.json        ← 活跃记忆（core + daily）
│   └── archived.json      ← 归档记忆（过期/手动归档）
└── consolidation_log.json ← 全局归纳日志

与 solobrave-server.py 解耦，可独立导入使用。
"""

import os
import json
import time
import uuid
import threading
import sqlite3
from collections import Counter

import knowledge_service as ks

# ═══════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════

MEMORY_V3_DIR = os.path.join(os.path.dirname(__file__), 'data', 'memories')

MEMORY_V3_CONFIG = {
    'core_max': 100,           # 核心记忆池上限
    'daily_max': 100,          # 日常记录池上限
    'daily_ttl_days': 30,      # 日常记录过期天数
    'inject_core_max': 50,     # 注入时核心记忆条数（上限50，实际全部注入）
    'inject_daily_max': 5,     # 注入时日常记忆条数
    'inject_archive_max': 2,   # 注入时归档补充条数
    'inject_knowledge_max': 3, # 注入时知识库条数
    'inject_value_max': 500,   # 单条记忆注入字符上限
    'store_value_max': 2000,   # 单条记忆存储字符上限
    'context_max': 500,        # daily 上下文摘要字符上限
    'dedup_threshold': 0.85,   # 智能去重相似度阈值
}

# 记忆归纳阈值（统一收口，方便后续调整）
MEMORY_INDUCTION_THRESHOLDS = {
    # 日常记录 ≥ N 条时，前端显示"建议归纳"横幅并可生成每日归纳
    'daily_consolidate_min': 2,
    # 同一项目/标签 ≥ N 条记忆时，自动创建项目归纳（pending）
    'project_summary_min': 3,
    # 知识库沉淀：重复提及 ≥ N 次即升级为 active
    'knowledge_repeat_min': 2,
    # 手动触发知识库归纳：活跃记忆 ≥ N 条且未归纳记忆 ≥ N 条即可生成
    'knowledge_induction_min': 3,
}

# FIXME: 大脑知识中枢新增：记忆元数据数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'solobrave.db')


def _db_conn():
    """创建 SQLite 连接（启用字典行）"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _ensure_dir(path):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


def _lock_file(f):
    """跨平台文件锁：Windows 用 msvcrt.locking，Unix 用 fcntl.flock"""
    if os.name == 'nt':  # Windows
        import msvcrt
        # 阻塞锁定文件 1 字节，失败时自动重试
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    else:  # Mac/Linux
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX)  # 支持文件对象或 fd


def _unlock_file(f):
    """跨平台文件解锁"""
    if os.name == 'nt':  # Windows
        import msvcrt
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # Mac/Linux
        import fcntl
        fcntl.flock(f, fcntl.LOCK_UN)


def _read_json(filepath, default=None):
    """读取 JSON 文件"""
    if not os.path.isfile(filepath):
        return default if default is not None else None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else None


def _write_json(filepath, data):
    """原子写入 JSON 文件（跨平台文件锁保证多进程安全）"""
    _ensure_dir(os.path.dirname(filepath))
    tmp_path = filepath + '.tmp.' + uuid.uuid4().hex[:8]
    try:
        with open(filepath, 'a+', encoding='utf-8') as lock_f:
            _lock_file(lock_f)
            try:
                with open(tmp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            finally:
                _unlock_file(lock_f)
        # 解锁后再替换，避免 Windows 上替换被锁定文件失败
        os.replace(tmp_path, filepath)
    except (OSError, TypeError) as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ═══════════════════════════════════════════════════
# 智能去重辅助函数
# ═══════════════════════════════════════════════════


def _char_jaccard_similarity(a, b):
    """字符级 Jaccard 相似度（无 API key 时的 fallback）"""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _find_duplicate_memory(value, memories, api_key=None, provider='openai', threshold=0.85,
                            model=None, base_url=None):
    """
    在 memories 列表中查找语义重复的记忆。
    优先使用 embedding + 余弦相似度；失败或无 api_key 时 fallback 到字符 Jaccard。
    返回: (duplicate_memory, similarity)，无重复返回 (None, 0.0)
    """
    if not memories:
        return None, 0.0

    best_mem = None
    best_sim = 0.0

    # 路径 1：embedding 语义相似度
    if api_key:
        try:
            value_emb = ks.get_embedding_cached(value, api_key, provider, model=model, base_url=base_url)
            if value_emb:
                for m in memories:
                    m_value = m.get('value', '')
                    if not m_value:
                        continue
                    m_emb = ks.get_embedding_cached(m_value, api_key, provider, model=model, base_url=base_url)
                    if not m_emb:
                        continue
                    sim = ks.cosine_similarity(value_emb, m_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_mem = m
                if best_mem and best_sim >= threshold:
                    return best_mem, best_sim
        except Exception:
            pass

    # 路径 2：字符 Jaccard fallback
    best_mem = None
    best_sim = 0.0
    for m in memories:
        sim = _char_jaccard_similarity(value, m.get('value', ''))
        if sim > best_sim:
            best_sim = sim
            best_mem = m

    if best_mem and best_sim >= threshold:
        return best_mem, best_sim
    return None, 0.0


def _merge_duplicate_memory(target, source, merged_at=None):
    """
    将 source 记忆合并到 target 记忆中。
    保留更长的 value，更新 updatedAt，记录 mergedFrom（保留最近 10 条），合并 tags。
    返回合并后的 target。
    """
    if merged_at is None:
        merged_at = int(time.time() * 1000)

    # 保留更长的 value
    target_value = target.get('value', '')
    source_value = source.get('value', '')
    if len(source_value) > len(target_value):
        target['value'] = source_value

    # 更新 core 相关字段
    if 'updatedAt' in target or 'updatedAt' in source:
        target['updatedAt'] = merged_at

    # 合并 tags（去重，保持顺序）
    target_tags = list(target.get('tags', [])) if isinstance(target.get('tags'), list) else []
    source_tags = list(source.get('tags', [])) if isinstance(source.get('tags'), list) else []
    seen = set(target_tags)
    merged_tags = target_tags[:]
    for t in source_tags:
        if t not in seen:
            seen.add(t)
            merged_tags.append(t)
    target['tags'] = merged_tags

    # 记录合并来源
    merged_from = target.get('mergedFrom', [])
    if not isinstance(merged_from, list):
        merged_from = []
    merged_from.append({
        'id': source.get('id'),
        'value': source.get('value', '')[:200],
        'source': source.get('source', ''),
        'mergedAt': merged_at,
    })
    target['mergedFrom'] = merged_from[-10:]

    return target


# ═══════════════════════════════════════════════════
# 核心：加载 / 保存
# ═══════════════════════════════════════════════════

def _memory_file_path(emp_id):
    """活跃记忆文件路径"""
    return os.path.join(MEMORY_V3_DIR, emp_id, 'memory.json')


def _archive_file_path(emp_id):
    """归档记忆文件路径"""
    return os.path.join(MEMORY_V3_DIR, emp_id, 'archived.json')


def _consolidation_log_path():
    """归纳日志文件路径"""
    return os.path.join(MEMORY_V3_DIR, 'consolidation_log.json')


def load_memory(emp_id):
    """
    加载某员工的活跃记忆
    返回: {'version': '3.0', 'empId': ..., 'core': [...], 'daily': [...], 'stats': {...}}
    """
    filepath = _memory_file_path(emp_id)
    raw = _read_json(filepath, None)
    if raw is None:
        return _empty_memory(emp_id)
    # 确保字段完整
    result = {
        'version': raw.get('version', '3.0'),
        'empId': raw.get('empId', emp_id),
        'updatedAt': raw.get('updatedAt', int(time.time() * 1000)),
        'core': raw.get('core', []),
        'daily': raw.get('daily', []),
        'stats': raw.get('stats', {})
    }
    # 为旧数据（v3 之前创建）中没有 id 的记忆自动补 id
    need_save = False
    for pool in ('core', 'daily'):
        for m in result.get(pool, []):
            if not m.get('id'):
                m['id'] = 'mem_' + uuid.uuid4().hex[:8]
                need_save = True
    if need_save:
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    # 清理已过期但未归档的 daily（标记为过期，不移动到归档文件）
    result['daily'], expired_ids = _filter_expired(result['daily'])
    if expired_ids:
        # 将过期项移到归档文件
        archive_data = load_archive(emp_id)
        for m in expired_ids:
            m['archivedAt'] = int(time.time() * 1000)
            m['archiveReason'] = 'expired'
            archive_data.setdefault('archived', []).append(m)
        save_archive(emp_id, archive_data)
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    # 容量控制：活跃记忆超过 200 条时，自动归档最旧的 daily
    active_total = len(result['core']) + len(result['daily'])
    if active_total > 200 and result['daily']:
        # 按 createdAt 升序排序，取最旧的一条
        oldest = min(result['daily'], key=lambda m: m.get('createdAt', 0))
        result['daily'] = [m for m in result['daily'] if m.get('id') != oldest.get('id')]
        archive_data = load_archive(emp_id)
        oldest['archivedAt'] = int(time.time() * 1000)
        oldest['archiveReason'] = 'capacity'
        archive_data.setdefault('archived', []).append(oldest)
        save_archive(emp_id, archive_data)
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    # 更新 stats（保留已有自定义字段，如 lastMemoryConsolidationAt）
    stats = raw.get('stats', {}) if raw else {}
    result['stats'] = dict(stats)
    result['stats'].update({
        'coreCount': len(result['core']),
        'dailyCount': len(result['daily']),
        'totalAccess': sum(m.get('accessCount', 0) for m in result['core']),
        'lastKnowledgeInductionAt': stats.get('lastKnowledgeInductionAt', 0)
    })
    result['lastKnowledgeInductionAt'] = result['stats']['lastKnowledgeInductionAt']

    # 归纳提醒：日常记录 ≥ 阈值 且 自上次归纳后仍有新记录 时才建议
    daily_threshold = MEMORY_INDUCTION_THRESHOLDS['daily_consolidate_min']
    last_consolidation_at = result['stats'].get('lastMemoryConsolidationAt', 0)
    sorted_daily = sorted(
        [m for m in result['daily'] if m.get('createdAt', 0) > last_consolidation_at],
        key=lambda m: m.get('createdAt', 0)
    )
    if len(result['daily']) >= daily_threshold and sorted_daily:
        result['shouldConsolidate'] = True
        result['suggestedSourceIds'] = [m['id'] for m in sorted_daily[:5]]
    else:
        result['shouldConsolidate'] = False
        result['suggestedSourceIds'] = []

    # 冲突提示：列出所有处于 conflict 状态的核心记忆
    result['conflicts'] = [
        m for m in result.get('core', [])
        if m.get('conflictStatus') == 'conflict'
    ]

    return result


def save_memory(emp_id, data):
    """保存活跃记忆"""
    filepath = _memory_file_path(emp_id)
    data['updatedAt'] = int(time.time() * 1000)
    old_stats = data.get('stats', {})
    data['stats'] = {
        'coreCount': len(data.get('core', [])),
        'dailyCount': len(data.get('daily', [])),
        'totalAccess': sum(m.get('accessCount', 0) for m in data.get('core', [])),
        'lastKnowledgeInductionAt': old_stats.get('lastKnowledgeInductionAt', 0)
    }
    _write_json(filepath, data)


# FIXME: 大脑知识中枢新增：记忆元数据同步到 SQLite
_FILLER_PATTERNS = {'你好', '在吗', '在？', '在么', '您好', 'hello', 'hi', '谢谢', '感谢', '辛苦了', '拜拜', '再见'}


def _sync_memory_to_db(mem, emp_id, pool='daily', status='active'):
    """把单条记忆的元数据 upsert 到 memory 表；失败不抛异常"""
    try:
        conn = _db_conn()
        now = int(time.time() * 1000)
        conn.execute('''
            INSERT INTO memory (id, emp_id, value, pool, created_at, is_filler, is_duplicate,
                                source_mem_id, cleaned_at, topic_ids, inducted_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                value=excluded.value,
                pool=excluded.pool,
                is_filler=excluded.is_filler,
                is_duplicate=excluded.is_duplicate,
                source_mem_id=excluded.source_mem_id,
                cleaned_at=excluded.cleaned_at,
                topic_ids=excluded.topic_ids,
                inducted_at=excluded.inducted_at,
                status=excluded.status
        ''', (
            mem.get('id'), emp_id, mem.get('value', ''), pool,
            mem.get('createdAt') or now,
            int(mem.get('is_filler', 0)),
            int(mem.get('is_duplicate', 0)),
            mem.get('source_mem_id') or None,
            mem.get('cleaned_at', 0),
            json.dumps(mem.get('topicIds', []) or mem.get('topic_ids', []) or []),
            mem.get('inductedAt', 0),
            status
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  [MemorySyncDB] failed: {e}', flush=True)


def _cosine_sim(a, b):
    """计算两个 float 列表的余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _is_filler(value):
    """FIXME: 大脑知识中枢新增：判断是否为水话"""
    if not value:
        return True
    text = str(value).strip()
    if len(text) < 5:
        return True
    low = text.lower()
    return any(p in text or p in low for p in _FILLER_PATTERNS)


def _find_duplicate_in_window(emp_id, mem_id, value, window_days=30, threshold=0.85):
    """FIXME: 大脑知识中枢新增：查找近 30 天内相似度≥阈值的重复记忆"""
    try:
        emb_cfg = ks.get_embedding_config(emp_id)
        cur_emb = ks.get_embedding_cached(
            str(value), emb_cfg['apiKey'], emb_cfg['provider'],
            model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
        )
        if not cur_emb:
            return None
        cutoff = int(time.time() * 1000) - window_days * 24 * 3600 * 1000
        conn = _db_conn()
        rows = conn.execute(
            '''SELECT id, value FROM memory
               WHERE emp_id = ? AND id != ? AND created_at >= ?
                     AND is_filler = 0 AND status = 'active'
               ORDER BY created_at DESC LIMIT 200''',
            (emp_id, mem_id, cutoff)
        ).fetchall()
        conn.close()
        best_id = None
        best_sim = 0.0
        for row in rows:
            cand_emb = ks.get_embedding_cached(
                str(row['value']), emb_cfg['apiKey'], emb_cfg['provider'],
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
            )
            if not cand_emb:
                continue
            sim = _cosine_sim(cur_emb, cand_emb)
            if sim > best_sim:
                best_sim = sim
                best_id = row['id']
        return best_id if best_id and best_sim >= threshold else None
    except Exception as e:
        print(f'  [FindDuplicate] failed: {e}', flush=True)
        return None


def _clean_and_deduplicate(mem_id, emp_id):
    """FIXME: 大脑知识中枢新增：单条记忆清洗去重"""
    mem, pool = get_memory(emp_id, mem_id)
    if not mem:
        return None
    now = int(time.time() * 1000)
    mem['is_filler'] = 1 if _is_filler(mem.get('value', '')) else 0
    if mem.get('is_filler'):
        mem['is_duplicate'] = 0
        mem['source_mem_id'] = None
    else:
        dup_id = _find_duplicate_in_window(emp_id, mem_id, mem.get('value', ''))
        mem['is_duplicate'] = 1 if dup_id else 0
        mem['source_mem_id'] = dup_id if dup_id else None
    mem['cleaned_at'] = now
    data = load_memory(emp_id)
    target = data.get(pool, [])
    for i, m in enumerate(target):
        if m.get('id') == mem_id:
            target[i] = mem
            break
    data[pool] = target
    save_memory(emp_id, data)
    _sync_memory_to_db(mem, emp_id, pool=pool)
    return mem


def get_uncleaned_memories(emp_id, limit=200):
    """FIXME: 大脑知识中枢新增：获取待清洗记忆列表"""
    data = load_memory(emp_id)
    result = []
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if not m.get('cleaned_at'):
                result.append(m)
    return result[:limit]


def load_archive(emp_id):
    """加载归档记忆"""
    filepath = _archive_file_path(emp_id)
    raw = _read_json(filepath, None)
    if raw is None:
        return {'version': '3.0', 'empId': emp_id, 'archived': []}
    return {
        'version': raw.get('version', '3.0'),
        'empId': raw.get('empId', emp_id),
        'archived': raw.get('archived', [])
    }


def save_archive(emp_id, data):
    """保存归档记忆"""
    filepath = _archive_file_path(emp_id)
    _write_json(filepath, data)


def _empty_memory(emp_id):
    """返回空记忆结构"""
    return {
        'version': '3.0',
        'empId': emp_id,
        'updatedAt': int(time.time() * 1000),
        'core': [],
        'daily': [],
        'stats': {'coreCount': 0, 'dailyCount': 0, 'totalAccess': 0, 'lastKnowledgeInductionAt': 0},
        'lastKnowledgeInductionAt': 0
    }


def _filter_expired(daily_list):
    """过滤已过期的 daily 记忆
    返回: (保留的列表, 已过期的列表)
    """
    cfg = MEMORY_V3_CONFIG
    ttl_ms = cfg['daily_ttl_days'] * 24 * 3600 * 1000
    now = int(time.time() * 1000)
    keep = []
    expired = []
    for m in daily_list:
        expires_at = m.get('expiresAt', 0)
        if expires_at and now > expires_at:
            expired.append(m)
        else:
            keep.append(m)
    return keep, expired


# ═══════════════════════════════════════════════════
# CRUD 操作
# ═══════════════════════════════════════════════════

def add_memory(emp_id, value, key='auto', source='user_input', context=None,
               priority=None, tags=None, api_key=None, provider='openai',
               model=None, base_url=None):
    """
    添加记忆
    key='auto' 或 'auto_extract' → daily 池
    其他值 → core 池
    同一池内若存在语义重复记忆，将自动合并；使用全局 embedding 配置做去重。
    """
    cfg = MEMORY_V3_CONFIG
    if len(value) > cfg['store_value_max']:
        raise ValueError(f'Value exceeds max length {cfg["store_value_max"]}')

    pool = 'daily' if key in ('auto', 'auto_extract') else 'core'
    pool_max = cfg['daily_max'] if pool == 'daily' else cfg['core_max']

    data = load_memory(emp_id)
    target = data.get(pool, [])

    if len(target) >= pool_max:
        raise RuntimeError(f'{pool} pool full ({len(target)}/{pool_max})')

    now = int(time.time() * 1000)
    ttl_ms = cfg['daily_ttl_days'] * 24 * 3600 * 1000

    memory = {
        'id': 'mem_' + uuid.uuid4().hex[:8],
        'key': key,
        'value': value,
        'source': source,
        'createdAt': now,
    }

    if pool == 'core':
        memory['priority'] = priority if priority is not None else 5
        memory['tags'] = tags or []
        memory['updatedAt'] = now
        memory['accessCount'] = 0
        memory['expiresAt'] = None
    else:
        memory['context'] = (context or '')[:cfg['context_max']]
        memory['expiresAt'] = now + ttl_ms

    # 智能去重：在同一池中查找语义重复；使用全局 embedding 配置
    emb_cfg = ks.get_embedding_config(emp_id)
    duplicate, sim = _find_duplicate_memory(
        value, target, api_key=emb_cfg['apiKey'], provider=emb_cfg['provider'],
        threshold=cfg.get('dedup_threshold', 0.85),
        model=emb_cfg['model'], base_url=emb_cfg['baseUrl']
    )
    if duplicate:
        new_value = memory.get('value', '')
        _merge_duplicate_memory(duplicate, memory, merged_at=now)
        data[pool] = target
        save_memory(emp_id, data)
        log_consolidation(
            emp_id, 'duplicate_merge', [memory['id'], duplicate['id']], duplicate['id'],
            old_value=new_value,
            new_value=duplicate.get('value', ''),
            trigger='auto'
        )
        return duplicate

    target.append(memory)
    data[pool] = target
    save_memory(emp_id, data)
    # FIXME: 大脑知识中枢新增：同步记忆元数据到 SQLite
    _sync_memory_to_db(memory, emp_id, pool=pool)
    return memory


def get_memory(emp_id, mem_id):
    """获取单条记忆（从活跃池中查找）"""
    data = load_memory(emp_id)
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') == mem_id:
                return m, pool
    return None, None


def set_memory_topics(emp_id, mem_id, topic_ids):
    """FIXME: 大脑知识中枢新增：设置记忆所属主题（JSON + DB）"""
    data = load_memory(emp_id)
    found = False
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') == mem_id:
                m['topicIds'] = list(topic_ids)
                found = True
                break
        if found:
            break
    if found:
        save_memory(emp_id, data)
    try:
        conn = _db_conn()
        conn.execute('UPDATE memory SET topic_ids=? WHERE id=?', (json.dumps(list(topic_ids)), mem_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  [SetMemoryTopics] failed: {e}', flush=True)


def update_memory(emp_id, mem_id, updates, api_key=None, provider='openai',
                  model=None, base_url=None):
    """
    更新记忆
    updates: dict，可包含 value / key / source / priority / tags / context
    支持跨池移动（key 变更时）
    更新后若与同一池其他记忆语义重复，将自动合并；使用全局 embedding 配置做去重。
    """
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    # 查找记忆
    found = None
    old_pool = None
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') == mem_id:
                found = m
                old_pool = pool
                break
        if found:
            break

    if not found:
        return None

    # 应用更新
    if 'value' in updates:
        found['value'] = updates['value']
    if 'source' in updates:
        found['source'] = updates['source']

    # 跨池移动检查
    current_pool = old_pool
    new_key = updates.get('key')
    if new_key:
        new_pool = 'daily' if new_key in ('auto', 'auto_extract') else 'core'
        if new_pool != old_pool:
            target = data.get(new_pool, [])
            pool_max = cfg['daily_max'] if new_pool == 'daily' else cfg['core_max']
            if len(target) >= pool_max:
                raise RuntimeError(f'Cannot move: {new_pool} pool full')
            # 转换字段
            if new_pool == 'core':
                found['priority'] = updates.get('priority', 5)
                found['tags'] = updates.get('tags', [])
                found['updatedAt'] = int(time.time() * 1000)
                found['accessCount'] = found.get('accessCount', 0)
                found.pop('context', None)
                found.pop('expiresAt', None)
            else:
                found['context'] = updates.get('context', '')[:cfg['context_max']]
                found['expiresAt'] = int(time.time() * 1000) + cfg['daily_ttl_days'] * 24 * 3600 * 1000
                found.pop('priority', None)
                found.pop('tags', None)
                found.pop('updatedAt', None)
                found.pop('accessCount', None)
            # 移动
            data[old_pool] = [m for m in data[old_pool] if m.get('id') != mem_id]
            found['key'] = new_key
            target.append(found)
            data[new_pool] = target
            current_pool = new_pool
        else:
            found['key'] = new_key
            if old_pool == 'core':
                if 'priority' in updates:
                    found['priority'] = updates['priority']
                if 'tags' in updates:
                    found['tags'] = updates['tags']
                found['updatedAt'] = int(time.time() * 1000)
            else:
                if 'context' in updates:
                    found['context'] = updates['context'][:cfg['context_max']]

    # 智能去重：在当前池中排除自己后查找重复；使用全局 embedding 配置
    emb_cfg = ks.get_embedding_config(emp_id)
    pool_mems = [m for m in data.get(current_pool, []) if m.get('id') != mem_id]
    duplicate, sim = _find_duplicate_memory(
        found.get('value', ''), pool_mems, api_key=emb_cfg['apiKey'], provider=emb_cfg['provider'],
        threshold=cfg.get('dedup_threshold', 0.85),
        model=emb_cfg['model'], base_url=emb_cfg['baseUrl']
    )
    if duplicate:
        old_value = duplicate.get('value', '')
        _merge_duplicate_memory(duplicate, found, merged_at=int(time.time() * 1000))
        data[current_pool] = [m for m in data[current_pool] if m.get('id') != mem_id]
        save_memory(emp_id, data)
        log_consolidation(
            emp_id, 'duplicate_merge', [found['id'], duplicate['id']], duplicate['id'],
            old_value=old_value,
            new_value=duplicate.get('value', ''),
            trigger='auto'
        )
        # FIXME: 大脑知识中枢新增：合并后同步存活记忆元数据
        _sync_memory_to_db(duplicate, emp_id, pool=current_pool)
        return duplicate

    save_memory(emp_id, data)
    # FIXME: 大脑知识中枢新增：更新后同步记忆元数据
    _sync_memory_to_db(found, emp_id, pool=current_pool)
    return found


def delete_memory(emp_id, mem_id):
    """删除记忆（从活跃池和归档池中查找并删除）"""
    data = load_memory(emp_id)
    removed = False

    for pool in ('core', 'daily'):
        original = len(data.get(pool, []))
        data[pool] = [m for m in data.get(pool, []) if m.get('id') != mem_id]
        if len(data[pool]) < original:
            removed = True

    if removed:
        save_memory(emp_id, data)
        # FIXME: 大脑知识中枢新增：标记删除/归档状态
        try:
            conn = _db_conn()
            conn.execute("UPDATE memory SET status='archived' WHERE id=?", (mem_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f'  [DeleteSync] failed: {e}', flush=True)
    else:
        # 尝试从归档中删除
        archive_data = load_archive(emp_id)
        original = len(archive_data.get('archived', []))
        archive_data['archived'] = [m for m in archive_data.get('archived', []) if m.get('id') != mem_id]
        if len(archive_data['archived']) < original:
            save_archive(emp_id, archive_data)
            removed = True

    return removed


def promote_memory(emp_id, mem_id):
    """将 daily 记忆升级为核心记忆"""
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    mem = None
    for m in data.get('daily', []):
        if m.get('id') == mem_id:
            mem = m
            break

    if not mem:
        return None

    if len(data.get('core', [])) >= cfg['core_max']:
        raise RuntimeError(f'Core pool full ({cfg["core_max"]})')

    data['daily'] = [m for m in data['daily'] if m.get('id') != mem_id]
    mem['key'] = 'core'
    mem['priority'] = 5
    mem['tags'] = []
    mem['updatedAt'] = int(time.time() * 1000)
    mem['accessCount'] = mem.get('accessCount', 0)
    mem.pop('context', None)
    mem.pop('expiresAt', None)
    data['core'].append(mem)
    save_memory(emp_id, data)
    return mem


def archive_memory(emp_id, mem_id, reason='manual'):
    """将活跃记忆归档（从 memory.json 移到 archived.json）"""
    data = load_memory(emp_id)

    mem = None
    for pool in ('core', 'daily'):
        for i, m in enumerate(data.get(pool, [])):
            if m.get('id') == mem_id:
                mem = data[pool].pop(i)
                break
        if mem:
            break

    if not mem:
        return False

    now = int(time.time() * 1000)
    archived_entry = {
        'id': mem['id'],
        'originalKey': mem.get('key', 'auto'),
        'value': mem.get('value', ''),
        'source': mem.get('source', ''),
        'createdAt': mem.get('createdAt', now),
        'archivedAt': now,
        'archiveReason': reason,
    }

    archive_data = load_archive(emp_id)
    archive_data.setdefault('archived', []).append(archived_entry)
    save_archive(emp_id, archive_data)
    save_memory(emp_id, data)
    # FIXME: 大脑知识中枢新增：归档后同步元数据状态
    _sync_memory_to_db(archived_entry, emp_id, pool='archive', status='archived')
    return True


def restore_memory(emp_id, mem_id):
    """从归档恢复到 daily 池"""
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    if len(data.get('daily', [])) >= cfg['daily_max']:
        raise RuntimeError(f'Daily pool full ({cfg["daily_max"]})')

    archive_data = load_archive(emp_id)
    mem = None
    for i, m in enumerate(archive_data.get('archived', [])):
        if m.get('id') == mem_id:
            mem = archive_data['archived'].pop(i)
            break

    if not mem:
        return None

    now = int(time.time() * 1000)
    ttl_ms = cfg['daily_ttl_days'] * 24 * 3600 * 1000
    restored = {
        'id': mem['id'],
        'key': mem.get('originalKey', 'auto'),
        'value': mem.get('value', ''),
        'source': mem.get('source', ''),
        'createdAt': mem.get('createdAt', now),
        'context': mem.get('context', ''),
        'expiresAt': now + ttl_ms,
    }

    data['daily'].append(restored)
    save_memory(emp_id, data)
    save_archive(emp_id, archive_data)
    # FIXME: 大脑知识中枢新增：恢复后同步元数据
    _sync_memory_to_db(restored, emp_id, pool='daily')
    return restored


def consolidate_memory(emp_id, source_ids, consolidated_value, key='core',
                       priority=8, tags=None):
    """
    归纳合并：将多条 daily 记忆合并为一条 core 记忆
    source_ids: 要合并的 daily 记忆 ID 列表
    返回: (新记忆, 归档的ID列表)
    """
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    # 收集要合并的记忆
    sources = []
    for sid in source_ids:
        for m in data.get('daily', []):
            if m.get('id') == sid:
                sources.append(m)
                break

    if len(sources) < 2:
        raise RuntimeError('Need at least 2 memories to consolidate')

    # 检查 core 池容量
    if len(data.get('core', [])) >= cfg['core_max']:
        raise RuntimeError(f'Core pool full ({cfg["core_max"]})')

    now = int(time.time() * 1000)

    # 创建新的 core 记忆（归纳摘要）
    new_mem = {
        'id': 'mem_' + uuid.uuid4().hex[:8],
        'key': key,
        'value': consolidated_value,
        'source': 'induction',
        'priority': max(1, min(10, priority)),
        'tags': tags or [],
        'createdAt': now,
        'updatedAt': now,
        'accessCount': 0,
        'expiresAt': None,
    }

    # 从 daily 池移除源记忆，移入归档
    archived_ids = []
    archive_data = load_archive(emp_id)
    for s in sources:
        sid = s['id']
        data['daily'] = [m for m in data['daily'] if m.get('id') != sid]
        archived_ids.append(sid)
        archive_entry = {
            'id': sid,
            'originalKey': s.get('key', 'auto'),
            'value': s.get('value', ''),
            'source': s.get('source', ''),
            'createdAt': s.get('createdAt', now),
            'archivedAt': now,
            'archiveReason': 'consolidated',
        }
        archive_data.setdefault('archived', []).append(archive_entry)

    data['core'].append(new_mem)
    # 记录本次建议归纳时间戳，用于刷新后判断是否已经归纳过
    data.setdefault('stats', {})['lastMemoryConsolidationAt'] = now
    save_memory(emp_id, data)
    save_archive(emp_id, archive_data)

    # FIXME: 大脑知识中枢新增：同步新核心记忆，并把源记忆标记为归档
    _sync_memory_to_db(new_mem, emp_id, pool='core')
    try:
        conn = _db_conn()
        for sid in source_ids:
            conn.execute("UPDATE memory SET status='archived' WHERE id=?", (sid,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f'  [ConsolidateSync] archive source failed: {e}', flush=True)

    # 记录归纳日志
    log_consolidation(emp_id, 'merge', source_ids, new_mem['id'],
                      old_value='; '.join(s.get('value', '')[:100] for s in sources),
                      new_value=consolidated_value,
                      trigger='manual')

    return new_mem, archived_ids


# ═══════════════════════════════════════════════════
# 核心记忆候选（二期新增）
# ═══════════════════════════════════════════════════

def _core_candidates_file_path(emp_id):
    """核心记忆候选文件路径"""
    return os.path.join(MEMORY_V3_DIR, emp_id, 'core_candidates.json')


def load_core_candidates(emp_id):
    """加载核心记忆候选列表"""
    filepath = _core_candidates_file_path(emp_id)
    raw = _read_json(filepath, None)
    if raw is None:
        return {'version': '3.0', 'empId': emp_id, 'candidates': []}
    return {
        'version': raw.get('version', '3.0'),
        'empId': raw.get('empId', emp_id),
        'candidates': raw.get('candidates', []) if isinstance(raw.get('candidates'), list) else []
    }


def save_core_candidates(emp_id, data):
    """保存核心记忆候选列表"""
    filepath = _core_candidates_file_path(emp_id)
    _write_json(filepath, data)


def _candidate_id():
    return 'cand_' + uuid.uuid4().hex[:8]


def add_core_candidates(emp_id, candidate_values):
    """
    批量添加核心记忆候选（去重：同 value 不再新增）
    candidate_values: [{'value': str, 'reason': str, 'sourceIds': [str]}]
    返回新增数量
    """
    data = load_core_candidates(emp_id)
    existing_values = {c.get('value', '').strip(): True for c in data.get('candidates', []) if c.get('status') != 'dismissed'}
    added = 0
    now = int(time.time() * 1000)
    for cand in candidate_values or []:
        value = str(cand.get('value', '')).strip()
        if not value:
            continue
        if value in existing_values:
            continue
        data['candidates'].append({
            'id': cand.get('id') or _candidate_id(),
            'value': value,
            'reason': str(cand.get('reason', '')).strip(),
            'sourceIds': list(cand.get('sourceIds', [])) if isinstance(cand.get('sourceIds'), (list, tuple)) else [],
            'status': 'pending',
            'createdAt': cand.get('createdAt') or now,
        })
        existing_values[value] = True
        added += 1
    if added > 0:
        save_core_candidates(emp_id, data)
    return added


def get_pending_core_candidates(emp_id):
    """返回待确认的核心记忆候选"""
    data = load_core_candidates(emp_id)
    return [c for c in data.get('candidates', []) if c.get('status') == 'pending']


def get_core_candidate_by_id(emp_id, cand_id):
    """按 ID 查找候选"""
    data = load_core_candidates(emp_id)
    for c in data.get('candidates', []):
        if c.get('id') == cand_id:
            return c, data
    return None, data


def update_core_candidate_status(emp_id, cand_id, status):
    """更新候选状态：pending / confirmed / dismissed"""
    cand, data = get_core_candidate_by_id(emp_id, cand_id)
    if not cand:
        return None
    cand['status'] = status
    cand['updatedAt'] = int(time.time() * 1000)
    save_core_candidates(emp_id, data)
    return cand


def archive_source_memories_as_promoted(emp_id, source_ids):
    """确认候选后，将源 daily 记忆归档（archiveReason='promoted'）"""
    if not source_ids:
        return []
    data = load_memory(emp_id)
    archive_data = load_archive(emp_id)
    archived_ids = []
    now = int(time.time() * 1000)
    for pool in ('core', 'daily'):
        kept = []
        for m in data.get(pool, []):
            if m.get('id') in source_ids:
                archived_ids.append(m['id'])
                archive_entry = {
                    'id': m['id'],
                    'originalKey': m.get('key', 'auto'),
                    'value': m.get('value', ''),
                    'source': m.get('source', ''),
                    'createdAt': m.get('createdAt', now),
                    'archivedAt': now,
                    'archiveReason': 'promoted',
                }
                archive_data.setdefault('archived', []).append(archive_entry)
            else:
                kept.append(m)
        data[pool] = kept
    if archived_ids:
        save_memory(emp_id, data)
        save_archive(emp_id, archive_data)
    return archived_ids


# ═══════════════════════════════════════════════════
# 知识归纳标记（二期新增）
# ═══════════════════════════════════════════════════

def mark_memories_inducted(emp_id, mem_ids):
    """批量标记活跃记忆为已归纳"""
    if not mem_ids:
        return 0
    data = load_memory(emp_id)
    now = int(time.time() * 1000)
    marked = 0
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') in mem_ids and not m.get('inductedAt'):
                m['inductedAt'] = now
                marked += 1
    if marked > 0:
        save_memory(emp_id, data)
    return marked


def get_uninducted_active_memories(emp_id):
    """获取未归纳的活跃记忆列表"""
    data = load_memory(emp_id)
    result = []
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if not m.get('inductedAt'):
                result.append((m, pool))
    return result


def get_last_knowledge_induction_at(emp_id):
    """获取上次知识归纳时间戳"""
    data = load_memory(emp_id)
    return data.get('stats', {}).get('lastKnowledgeInductionAt', 0)


def set_last_knowledge_induction_at(emp_id, timestamp=None):
    """更新上次知识归纳时间戳"""
    data = load_memory(emp_id)
    data.setdefault('stats', {})['lastKnowledgeInductionAt'] = timestamp or int(time.time() * 1000)
    save_memory(emp_id, data)


def archive_inducted_memories(emp_id):
    """归档所有已归纳的活跃记忆，返回归档的 ID 列表"""
    data = load_memory(emp_id)
    archive_data = load_archive(emp_id)
    now = int(time.time() * 1000)
    archived_ids = []
    for pool in ('core', 'daily'):
        kept = []
        for m in data.get(pool, []):
            if m.get('inductedAt'):
                archived_ids.append(m['id'])
                archive_data.setdefault('archived', []).append({
                    'id': m['id'],
                    'originalKey': m.get('key', 'auto'),
                    'value': m.get('value', ''),
                    'source': m.get('source', ''),
                    'createdAt': m.get('createdAt', now),
                    'archivedAt': now,
                    'archiveReason': 'inducted',
                })
            else:
                kept.append(m)
        data[pool] = kept
    if archived_ids:
        save_memory(emp_id, data)
        save_archive(emp_id, archive_data)
    return archived_ids


# ═══════════════════════════════════════════════════
# 记忆冲突处理
# ═══════════════════════════════════════════════════

def _detect_conflicting_memories(core_memories, api_key=None, provider='openai', ai_resolve_fn=None):
    """
    调用 AI 检测核心记忆中的语义冲突。
    为控制 O(n^2) 成本，只检测最近 7 天内新增/更新的记忆与全部核心记忆的冲突。
    ai_resolve_fn(prompt, system_prompt) 由调用方提供，返回 JSON 列表。
    返回: [{'memoryId': id, 'conflictWith': [other_id], 'reason': str}]
    """
    if not core_memories or len(core_memories) < 2:
        return []
    if not ai_resolve_fn:
        return []

    now = int(time.time() * 1000)
    seven_days_ms = 7 * 24 * 3600 * 1000
    recent_ids = [
        m.get('id') for m in core_memories
        if (m.get('updatedAt') or m.get('createdAt') or 0) > now - seven_days_ms
    ]
    if len(recent_ids) < 1:
        return []

    # 构建要检测的记忆对（recent vs all）
    mem_map = {m.get('id'): m for m in core_memories if m.get('id')}
    pairs = []
    for rid in recent_ids:
        for m in core_memories:
            oid = m.get('id')
            if not oid or oid == rid:
                continue
            pairs.append((rid, oid))
    if not pairs:
        return []

    # 构造 prompt
    pair_texts = []
    for rid, oid in pairs:
        rmem = mem_map.get(rid, {})
        omem = mem_map.get(oid, {})
        pair_texts.append(
            f"Pair: {rid} vs {oid}\n"
            f"[{rid}] {rmem.get('value', '')}\n"
            f"[{oid}] {omem.get('value', '')}"
        )

    system_prompt = (
        "你是一个记忆冲突检测专家。请分析下面给出的核心记忆对，"
        "找出语义上相互矛盾（不能同时为真）的对。"
        "返回 JSON 数组，每个元素包含 memoryId、conflictWith（list）、reason。"
        "如果没有冲突，返回空数组 []。"
        "只输出 JSON，不要额外解释。"
    )
    prompt = (
        "请判断以下核心记忆对是否存在语义冲突：\n\n"
        + "\n\n".join(pair_texts)
        + "\n\n只输出 JSON 数组。"
    )

    try:
        result = ai_resolve_fn(prompt, system_prompt)
        if isinstance(result, list):
            return result
        if isinstance(result, str):
            parsed = json.loads(result)
            return parsed if isinstance(parsed, list) else []
    except Exception:
        return []
    return []


def detect_core_memory_conflicts(emp_id, api_key=None, provider='openai', ai_resolve_fn=None):
    """
    检测某员工核心记忆中的语义冲突。
    返回: [{'memoryId': id, 'conflictWith': [other_id], 'reason': str}]
    """
    data = load_memory(emp_id)
    core_memories = data.get('core', [])
    return _detect_conflicting_memories(core_memories, api_key, provider, ai_resolve_fn)


def mark_memory_conflict(emp_id, mem_id, conflict_with, reason=''):
    """
    标记某条核心记忆为冲突状态，并记录与其冲突的记忆 ID。
    conflict_with: list of mem_id
    """
    if not conflict_with:
        return None
    data = load_memory(emp_id)
    target = None
    for m in data.get('core', []):
        if m.get('id') == mem_id:
            target = m
            break
    if not target:
        return None

    now = int(time.time() * 1000)
    target['conflictStatus'] = 'conflict'
    existing = set(target.get('conflictWith', []))
    existing.update(conflict_with)
    target['conflictWith'] = list(existing)
    target['conflictNote'] = reason
    target['updatedAt'] = now

    # 同时反向标记对方记忆
    for oid in conflict_with:
        for m in data.get('core', []):
            if m.get('id') == oid:
                m['conflictStatus'] = 'conflict'
                m_existing = set(m.get('conflictWith', []))
                m_existing.add(mem_id)
                m['conflictWith'] = list(m_existing)
                m['updatedAt'] = now
                break

    save_memory(emp_id, data)
    return target


def resolve_memory_conflict(emp_id, mem_id, resolution=''):
    """将某条核心记忆的冲突状态标记为已解决"""
    data = load_memory(emp_id)
    target = None
    for m in data.get('core', []):
        if m.get('id') == mem_id:
            target = m
            break
    if not target:
        return None

    now = int(time.time() * 1000)
    target['conflictStatus'] = 'resolved'
    target['conflictResolvedAt'] = now
    target['conflictNote'] = resolution or target.get('conflictNote', '')
    target['updatedAt'] = now

    save_memory(emp_id, data)
    return target


# ═══════════════════════════════════════════════════
# 归纳日志
# ═══════════════════════════════════════════════════


def log_consolidation(emp_id, ctype, source_ids, target_id=None,
                      old_value=None, new_value=None, trigger='manual'):
    """
    记录归纳操作
    ctype: merge / promote / demote / delete / split
    """
    filepath = _consolidation_log_path()
    raw = _read_json(filepath, {'version': '3.0', 'logs': []})
    # 兼容旧格式：如果日志文件是列表，自动迁移为对象格式
    if isinstance(raw, list):
        log_data = {'version': '3.0', 'logs': raw}
    elif isinstance(raw, dict):
        log_data = raw
    else:
        log_data = {'version': '3.0', 'logs': []}

    log_entry = {
        'id': 'log_' + uuid.uuid4().hex[:8],
        'empId': emp_id,
        'timestamp': int(time.time() * 1000),
        'type': ctype,
        'sourceIds': source_ids or [],
        'targetId': target_id,
        'oldValue': old_value,
        'newValue': new_value,
        'trigger': trigger,
    }

    log_data.setdefault('logs', []).append(log_entry)
    # 容量控制：保留最近 1000 条日志
    if len(log_data['logs']) > 1000:
        log_data['logs'] = log_data['logs'][-1000:]
    _write_json(filepath, log_data)
    return log_entry


def get_consolidation_logs(emp_id=None, limit=50):
    """获取归纳日志，可指定 empId 过滤"""
    filepath = _consolidation_log_path()
    log_data = _read_json(filepath, {'logs': []})
    logs = log_data.get('logs', [])
    if emp_id:
        logs = [l for l in logs if l.get('empId') == emp_id]
    # 按时间倒序
    logs = sorted(logs, key=lambda x: x.get('timestamp', 0), reverse=True)
    return logs[:limit]


def get_duplicate_merge_logs(emp_id, limit=50):
    """
    获取去重合并日志（type='duplicate_merge'）。
    支持项目组：当 emp_id 以 'group_' 开头时读取对应 group 的合并记录。
    """
    filepath = _consolidation_log_path()
    log_data = _read_json(filepath, {'logs': []})
    logs = log_data.get('logs', [])
    logs = [
        l for l in logs
        if l.get('type') == 'duplicate_merge' and l.get('empId') == emp_id
    ]
    logs = sorted(logs, key=lambda x: x.get('timestamp', 0), reverse=True)
    return logs[:limit]


# ═══════════════════════════════════════════════════
# 项目组记忆（group memory）
# ═══════════════════════════════════════════════════

def _group_memory_dir():
    return os.path.join(MEMORY_V3_DIR, 'groups')


def _group_memory_file_path(group_id):
    return os.path.join(_group_memory_dir(), f'group_{group_id}.json')


def _group_archive_file_path(group_id):
    return os.path.join(_group_memory_dir(), f'group_{group_id}_archived.json')


def _empty_group_memory(group_id):
    return {
        'version': '3.0',
        'groupId': group_id,
        'updatedAt': int(time.time() * 1000),
        'core': [],
        'daily': [],
        'stats': {'coreCount': 0, 'dailyCount': 0, 'totalAccess': 0}
    }


def load_group_memory(group_id):
    """加载项目组活跃记忆（core + daily）"""
    filepath = _group_memory_file_path(group_id)
    raw = _read_json(filepath, None)
    if raw is None:
        return _empty_group_memory(group_id)
    result = {
        'version': raw.get('version', '3.0'),
        'groupId': raw.get('groupId', group_id),
        'updatedAt': raw.get('updatedAt', int(time.time() * 1000)),
        'core': raw.get('core', []),
        'daily': raw.get('daily', []),
        'stats': raw.get('stats', {})
    }
    need_save = False
    for pool in ('core', 'daily'):
        for m in result.get(pool, []):
            if not m.get('id'):
                m['id'] = 'mem_' + uuid.uuid4().hex[:8]
                need_save = True
    if need_save:
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    # 自动归档过期 daily
    result['daily'], expired = _filter_expired(result['daily'])
    if expired:
        archive_data = load_group_archive(group_id)
        for m in expired:
            m['archivedAt'] = int(time.time() * 1000)
            m['archiveReason'] = 'expired'
            archive_data.setdefault('archived', []).append(m)
        save_group_archive(group_id, archive_data)
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    # 容量控制
    active_total = len(result['core']) + len(result['daily'])
    if active_total > 200 and result['daily']:
        oldest = min(result['daily'], key=lambda m: m.get('createdAt', 0))
        result['daily'] = [m for m in result['daily'] if m.get('id') != oldest.get('id')]
        archive_data = load_group_archive(group_id)
        oldest['archivedAt'] = int(time.time() * 1000)
        oldest['archiveReason'] = 'capacity'
        archive_data.setdefault('archived', []).append(oldest)
        save_group_archive(group_id, archive_data)
        result['updatedAt'] = int(time.time() * 1000)
        _write_json(filepath, result)

    result['stats'] = {
        'coreCount': len(result['core']),
        'dailyCount': len(result['daily']),
        'totalAccess': sum(m.get('accessCount', 0) for m in result['core'])
    }
    return result


def save_group_memory(group_id, data):
    """保存项目组活跃记忆"""
    filepath = _group_memory_file_path(group_id)
    data['updatedAt'] = int(time.time() * 1000)
    _write_json(filepath, data)


def load_group_archive(group_id):
    """加载项目组归档记忆"""
    filepath = _group_archive_file_path(group_id)
    raw = _read_json(filepath, None)
    if raw is None:
        return {'version': '3.0', 'groupId': group_id, 'archived': []}
    if isinstance(raw, list):
        return {'version': '3.0', 'groupId': group_id, 'archived': raw}
    return {
        'version': raw.get('version', '3.0'),
        'groupId': raw.get('groupId', group_id),
        'archived': raw.get('archived', [])
    }


def save_group_archive(group_id, data):
    """保存项目组归档记忆"""
    filepath = _group_archive_file_path(group_id)
    _write_json(filepath, data)


def add_group_memory(group_id, value, key='daily', source='group_chat', context=None,
                     api_key=None, provider='openai', model=None, base_url=None):
    """添加项目组公共记忆；key='auto'/'auto_extract'/'daily' 入 daily，其他入 core；使用全局 embedding 配置做去重"""
    cfg = MEMORY_V3_CONFIG
    if len(value) > cfg['store_value_max']:
        raise ValueError(f'Value exceeds max length {cfg["store_value_max"]}')

    pool = 'daily' if key in ('auto', 'auto_extract', 'daily') else 'core'
    pool_max = cfg['daily_max'] if pool == 'daily' else cfg['core_max']

    data = load_group_memory(group_id)
    target = data.get(pool, [])
    if len(target) >= pool_max:
        raise RuntimeError(f'{pool} pool full ({len(target)}/{pool_max})')

    now = int(time.time() * 1000)
    ttl_ms = cfg['daily_ttl_days'] * 24 * 3600 * 1000
    memory = {
        'id': 'mem_' + uuid.uuid4().hex[:8],
        'key': key,
        'value': value,
        'source': source,
        'createdAt': now,
    }
    if pool == 'core':
        memory['priority'] = 5
        memory['tags'] = []
        memory['updatedAt'] = now
        memory['accessCount'] = 0
        memory['expiresAt'] = None
    else:
        memory['context'] = (context or '')[:cfg['context_max']]
        memory['expiresAt'] = now + ttl_ms

    # 智能去重：使用全局 embedding 配置（项目组记忆无特定员工）
    emb_cfg = ks.get_embedding_config()
    duplicate, sim = _find_duplicate_memory(
        value, target, api_key=emb_cfg['apiKey'], provider=emb_cfg['provider'],
        threshold=cfg.get('dedup_threshold', 0.85),
        model=emb_cfg['model'], base_url=emb_cfg['baseUrl']
    )
    if duplicate:
        old_value = duplicate.get('value', '')
        _merge_duplicate_memory(duplicate, memory, merged_at=now)
        data[pool] = target
        save_group_memory(group_id, data)
        log_consolidation(
            'group_' + group_id, 'duplicate_merge', [memory['id'], duplicate['id']], duplicate['id'],
            old_value=old_value,
            new_value=duplicate.get('value', ''),
            trigger='auto'
        )
        return duplicate

    target.append(memory)
    data[pool] = target
    save_group_memory(group_id, data)
    return memory


def update_group_memory(group_id, mem_id, updates, api_key=None, provider='openai',
                        model=None, base_url=None):
    """
    更新项目组记忆；支持跨池移动与去重合并；使用全局 embedding 配置做去重。
    updates: dict，可包含 value / key / source / priority / tags / context
    """
    cfg = MEMORY_V3_CONFIG
    data = load_group_memory(group_id)

    found = None
    old_pool = None
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') == mem_id:
                found = m
                old_pool = pool
                break
        if found:
            break

    if not found:
        return None

    if 'value' in updates:
        found['value'] = updates['value']
    if 'source' in updates:
        found['source'] = updates['source']

    current_pool = old_pool
    new_key = updates.get('key')
    if new_key:
        new_pool = 'daily' if new_key in ('auto', 'auto_extract', 'daily') else 'core'
        if new_pool != old_pool:
            target = data.get(new_pool, [])
            pool_max = cfg['daily_max'] if new_pool == 'daily' else cfg['core_max']
            if len(target) >= pool_max:
                raise RuntimeError(f'Cannot move: {new_pool} pool full')
            if new_pool == 'core':
                found['priority'] = updates.get('priority', 5)
                found['tags'] = updates.get('tags', [])
                found['updatedAt'] = int(time.time() * 1000)
                found['accessCount'] = found.get('accessCount', 0)
                found.pop('context', None)
                found.pop('expiresAt', None)
            else:
                found['context'] = updates.get('context', '')[:cfg['context_max']]
                found['expiresAt'] = int(time.time() * 1000) + cfg['daily_ttl_days'] * 24 * 3600 * 1000
                found.pop('priority', None)
                found.pop('tags', None)
                found.pop('updatedAt', None)
                found.pop('accessCount', None)
            data[old_pool] = [m for m in data[old_pool] if m.get('id') != mem_id]
            found['key'] = new_key
            target.append(found)
            data[new_pool] = target
            current_pool = new_pool
        else:
            found['key'] = new_key
            if old_pool == 'core':
                if 'priority' in updates:
                    found['priority'] = updates['priority']
                if 'tags' in updates:
                    found['tags'] = updates['tags']
                found['updatedAt'] = int(time.time() * 1000)
            else:
                if 'context' in updates:
                    found['context'] = updates['context'][:cfg['context_max']]

    # 智能去重：在当前池中排除自己后查找重复；使用全局 embedding 配置
    emb_cfg = ks.get_embedding_config()
    pool_mems = [m for m in data.get(current_pool, []) if m.get('id') != mem_id]
    duplicate, sim = _find_duplicate_memory(
        found.get('value', ''), pool_mems, api_key=emb_cfg['apiKey'], provider=emb_cfg['provider'],
        threshold=cfg.get('dedup_threshold', 0.85),
        model=emb_cfg['model'], base_url=emb_cfg['baseUrl']
    )
    if duplicate:
        old_value = duplicate.get('value', '')
        _merge_duplicate_memory(duplicate, found, merged_at=int(time.time() * 1000))
        data[current_pool] = [m for m in data[current_pool] if m.get('id') != mem_id]
        save_group_memory(group_id, data)
        log_consolidation(
            'group_' + group_id, 'duplicate_merge', [found['id'], duplicate['id']], duplicate['id'],
            old_value=old_value,
            new_value=duplicate.get('value', ''),
            trigger='auto'
        )
        return duplicate

    save_group_memory(group_id, data)
    return found


def get_group_memory_stats(group_id):
    """获取项目组记忆统计"""
    data = load_group_memory(group_id)
    archive_data = load_group_archive(group_id)
    cfg = MEMORY_V3_CONFIG
    return {
        'groupId': group_id,
        'core': {
            'count': len(data.get('core', [])),
            'max': cfg['core_max'],
            'usagePercent': round(len(data.get('core', [])) / cfg['core_max'] * 100, 1)
        },
        'daily': {
            'count': len(data.get('daily', [])),
            'max': cfg['daily_max'],
            'usagePercent': round(len(data.get('daily', [])) / cfg['daily_max'] * 100, 1)
        },
        'archived': {'count': len(archive_data.get('archived', []))}
    }


def inject_group_memories(group_id, system_prompt=''):
    """为群聊 AI prompt 注入项目组公共记忆（core + daily）"""
    if not group_id:
        return system_prompt
    data = load_group_memory(group_id)
    cfg = MEMORY_V3_CONFIG
    MAX_TOTAL_CHARS = 2000

    core_lines = []
    for m in sorted(data.get('core', []),
                    key=lambda x: (x.get('priority', 5), x.get('accessCount', 0)),
                    reverse=True):
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            core_lines.append(f'- {val}')
            m['accessCount'] = m.get('accessCount', 0) + 1

    daily_lines = []
    for m in sorted(data.get('daily', []),
                    key=lambda x: x.get('createdAt', 0),
                    reverse=True)[:cfg['inject_daily_max']]:
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            daily_lines.append(f'- {val}')

    sections = []
    if core_lines:
        sections.append(('core', '【项目组核心记忆】\n' + '\n'.join(core_lines)))
    if daily_lines:
        sections.append(('daily', '【项目组日常记录】\n' + '\n'.join(daily_lines)))

    final_texts = []
    total_chars = 0
    for sec_type, sec_text in sections:
        sec_len = len(sec_text)
        if total_chars + sec_len <= MAX_TOTAL_CHARS:
            final_texts.append(sec_text)
            total_chars += sec_len
        else:
            lines = sec_text.split('\n')
            keep_lines = []
            for line in lines:
                if total_chars + len(line) + 1 <= MAX_TOTAL_CHARS:
                    keep_lines.append(line)
                    total_chars += len(line) + 1
                else:
                    break
            if keep_lines:
                final_texts.append('\n'.join(keep_lines))
            break

    if final_texts:
        system_prompt += '\n\n' + '\n\n'.join(final_texts)

    if data.get('core'):
        try:
            save_group_memory(group_id, data)
        except Exception:
            pass
    return system_prompt


# ═══════════════════════════════════════════════════
# 注入策略
# ═══════════════════════════════════════════════════

def inject_memories(emp_id, system_prompt='', user_message='', api_key=None, provider='openai',
                    agent_config=None, allowed_knowledge_categories=None,
                    model=None, base_url=None):
    """
    为 AI 对话注入记忆，返回更新后的 system_prompt
    注入优先级：core（按priority+accessCount排序）→ daily（按时间倒序）→ archive（补充）→ knowledge（语义检索）

    user_message: 当前用户消息，用于知识库语义检索
    api_key/provider/agent_config/model/base_url: 已废弃，保留签名兼容；实际使用全局 embedding 配置
    allowed_knowledge_categories: 知识库分类权限过滤（None 表示不限制）
    """
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    # 使用全局 embedding 配置做记忆注入（知识库语义检索）
    emb_cfg = ks.get_embedding_config()

    # 按优先级分层收集（core > daily > archive > knowledge）
    MAX_TOTAL_CHARS = 3000

    # L1: 核心记忆 — 全部注入，按 priority 降序，同 priority 按 accessCount 降序
    core_lines = []
    core_mems = sorted(
        data.get('core', []),
        key=lambda x: (x.get('priority', 5), x.get('accessCount', 0)),
        reverse=True
    )
    for m in core_mems:
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            core_lines.append(f'- {val}')
            m['accessCount'] = m.get('accessCount', 0) + 1

    # L2: 日常记录 — 按时间倒序，只取未过期的
    daily_lines = []
    daily_mems = sorted(
        data.get('daily', []),
        key=lambda x: x.get('createdAt', 0),
        reverse=True
    )
    for m in daily_mems[:cfg['inject_daily_max']]:
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            daily_lines.append(f'- {val}')

    # L3: 归档补充 — 当 L1+L2 不足时
    archive_lines = []
    total_active = len(core_lines) + len(daily_lines)
    target_total = len(core_mems) + cfg['inject_daily_max']
    if total_active < target_total:
        archive_data = load_archive(emp_id)
        archive_mems = sorted(
            archive_data.get('archived', []),
            key=lambda x: x.get('archivedAt', 0),
            reverse=True
        )
        need = min(cfg['inject_archive_max'], target_total - total_active)
        for m in archive_mems[:need]:
            val = m.get('value', '')[:cfg['inject_value_max']]
            if val:
                archive_lines.append(f'- [归档] {val}')

    # L4: 知识库 — 语义检索获取与当前对话相关的知识
    kb_lines = []
    try:
        import knowledge_service as ks
        # 空知识库 graceful handling
        conn = ks._db_conn()
        try:
            count_row = conn.execute('SELECT COUNT(*) as c FROM knowledge WHERE status="ok" AND (emp_id IS NULL OR emp_id=?)',
                                     ('',)).fetchone()
            kb_count = count_row['c'] if count_row else 0
        finally:
            conn.close()

        if kb_count > 0 and emb_cfg['apiKey'] and user_message:
            kb_docs = ks.knowledge_search_semantic(
                user_message, emp_id, emb_cfg['apiKey'], emb_cfg['provider'], agent_config,
                limit=cfg['inject_knowledge_max'],
                allowed_categories=allowed_knowledge_categories,
                model=emb_cfg['model'], base_url=emb_cfg['baseUrl'],
                requester_id=emp_id, is_admin=False, team_ids=None
            )
            for d in kb_docs:
                # 注入内容取最相关的 chunk（如果有）
                val = (d.get('relevantChunk') or d.get('content', ''))[:cfg['inject_value_max']]
                if val:
                    kb_lines.append(f'- [{d.get("category", "知识")}] {d.get("title", "")}: {val}')
    except Exception:
        pass  # 降级：静默忽略知识库注入失败

    # 总注入量控制：不超过 3000 字符，超出按优先级截断（core > daily > archive > knowledge）
    # 先构建所有 section，然后从低优先级开始截断
    sections = []
    if core_lines:
        sections.append(('core', '【关于用户的记忆】\n' + '\n'.join(core_lines)))
    if daily_lines:
        sections.append(('daily', '\n'.join(daily_lines)))
    if archive_lines:
        sections.append(('archive', '\n'.join(archive_lines)))
    if kb_lines:
        sections.append(('knowledge', '【相关知识库】\n' + '\n'.join(kb_lines)))

    # 计算总字符数，从低优先级开始截断
    final_texts = []
    total_chars = 0
    for sec_type, sec_text in sections:
        sec_len = len(sec_text)
        if total_chars + sec_len <= MAX_TOTAL_CHARS:
            final_texts.append(sec_text)
            total_chars += sec_len
        else:
            # 超出限制，截断该 section 的行
            lines = sec_text.split('\n')
            keep_lines = []
            for line in lines:
                if total_chars + len(line) + 1 <= MAX_TOTAL_CHARS:
                    keep_lines.append(line)
                    total_chars += len(line) + 1
                else:
                    break
            if keep_lines:
                final_texts.append('\n'.join(keep_lines))
            break  # 低优先级 section 直接丢弃

    if final_texts:
        system_prompt += '\n\n' + '\n\n'.join(final_texts)

    # 更新 accessCount（需要写回）
    if core_mems:
        try:
            save_memory(emp_id, data)
        except Exception as e:
            print(f'  [MemoryInject] {emp_id} accessCount 写回失败: {e}', flush=True)

    return system_prompt


def search_memories(emp_id, query=None, tags=None, pool=None, include_archived=False):
    """
    搜索记忆
    query: 关键词模糊匹配 value
    tags: 标签精确匹配（仅 core）
    pool: 'core' / 'daily' / None（全部活跃池）
    include_archived: 是否包含归档池
    """
    results = []
    data = load_memory(emp_id)

    pools = []
    if pool in ('core', 'daily'):
        pools = [pool]
    else:
        pools = ['core', 'daily']

    for p in pools:
        for m in data.get(p, []):
            match = True
            if query and query.lower() not in m.get('value', '').lower():
                match = False
            if tags and p == 'core':
                m_tags = set(m.get('tags', []))
                if not set(tags).intersection(m_tags):
                    match = False
            if match:
                results.append({**m, '_pool': p})

    if include_archived:
        archive_data = load_archive(emp_id)
        for m in archive_data.get('archived', []):
            if query and query.lower() not in m.get('value', '').lower():
                continue
            results.append({**m, '_pool': 'archived'})

    return results


# ═══════════════════════════════════════════════════
# 容量与统计
# ═══════════════════════════════════════════════════

def get_memory_stats(emp_id):
    """获取记忆统计信息"""
    data = load_memory(emp_id)
    archive_data = load_archive(emp_id)
    cfg = MEMORY_V3_CONFIG

    return {
        'empId': emp_id,
        'core': {
            'count': len(data.get('core', [])),
            'max': cfg['core_max'],
            'usagePercent': round(len(data.get('core', [])) / cfg['core_max'] * 100, 1)
        },
        'daily': {
            'count': len(data.get('daily', [])),
            'max': cfg['daily_max'],
            'usagePercent': round(len(data.get('daily', [])) / cfg['daily_max'] * 100, 1)
        },
        'archived': {
            'count': len(archive_data.get('archived', []))
        },
        'topTags': _extract_top_tags(data.get('core', []))
    }


def _extract_top_tags(core_mems, top_n=10):
    """提取核心记忆中最常见的标签"""
    all_tags = []
    for m in core_mems:
        all_tags.extend(m.get('tags', []))
    return [tag for tag, _ in Counter(all_tags).most_common(top_n)]


# ═══════════════════════════════════════════════════
# 批量操作
# ═══════════════════════════════════════════════════

def consolidate_memories(emp_id, source_mem_ids, new_value, new_key='core',
                         priority=5, tags=None):
    """
    归纳合并多条记忆为一条核心记忆
    1. 创建新的 core 记忆
    2. 将源记忆归档（标记为 consolidated）
    3. 记录归纳日志
    """
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    if len(data.get('core', [])) >= cfg['core_max']:
        raise RuntimeError(f'Core pool full ({cfg["core_max"]})')

    now = int(time.time() * 1000)

    # 创建新核心记忆
    new_mem = {
        'id': 'mem_' + uuid.uuid4().hex[:8],
        'key': new_key,
        'value': new_value,
        'source': 'consolidation',
        'priority': priority,
        'tags': tags or [],
        'createdAt': now,
        'updatedAt': now,
        'accessCount': 0,
    }
    data['core'].append(new_mem)

    # 归档源记忆
    archive_data = load_archive(emp_id)
    for pool in ('core', 'daily'):
        remaining = []
        for m in data.get(pool, []):
            if m.get('id') in source_mem_ids:
                archived_entry = {
                    'id': m['id'],
                    'originalKey': m.get('key', 'auto'),
                    'value': m.get('value', ''),
                    'source': m.get('source', ''),
                    'createdAt': m.get('createdAt', now),
                    'archivedAt': now,
                    'archiveReason': 'consolidated',
                    'consolidatedInto': new_mem['id'],
                }
                archive_data.setdefault('archived', []).append(archived_entry)
            else:
                remaining.append(m)
        data[pool] = remaining

    save_memory(emp_id, data)
    save_archive(emp_id, archive_data)

    # 记录日志
    log_consolidation(
        emp_id, 'merge', source_mem_ids, new_mem['id'],
        old_value=None, new_value=new_value, trigger='manual'
    )

    return new_mem


# ═══════════════════════════════════════════════════
# 兼容性：从 v2 迁移到 v3
# ═══════════════════════════════════════════════════

def migrate_from_v2(v2_filepath, emp_id):
    """
    从 v2 单文件格式迁移到 v3 目录结构
    v2_filepath: 旧的 memory/{empId}.json 路径
    """
    v2_data = _read_json(v2_filepath, None)
    if v2_data is None:
        return False

    now = int(time.time() * 1000)
    ttl_ms = MEMORY_V3_CONFIG['daily_ttl_days'] * 24 * 3600 * 1000

    v3_data = _empty_memory(emp_id)
    archive_entries = []

    # 迁移 core
    for m in v2_data.get('core', []):
        v3_mem = {
            'id': m.get('id', 'mem_' + uuid.uuid4().hex[:8]),
            'key': m.get('key', 'core'),
            'value': m.get('value', ''),
            'source': m.get('source', ''),
            'priority': 5,
            'tags': [],
            'createdAt': m.get('time', now),
            'updatedAt': m.get('time', now),
            'accessCount': 0,
        }
        v3_data['core'].append(v3_mem)

    # 迁移 daily（区分活跃和归档）
    for m in v2_data.get('daily', []):
        mem_time = m.get('time', now)
        is_archived = m.get('archived', False)

        if is_archived:
            archive_entries.append({
                'id': m.get('id', 'mem_' + uuid.uuid4().hex[:8]),
                'originalKey': m.get('key', 'auto'),
                'value': m.get('value', ''),
                'source': m.get('source', ''),
                'createdAt': mem_time,
                'archivedAt': m.get('archivedTime', now),
                'archiveReason': 'expired',
            })
        else:
            v3_mem = {
                'id': m.get('id', 'mem_' + uuid.uuid4().hex[:8]),
                'key': m.get('key', 'auto'),
                'value': m.get('value', ''),
                'source': m.get('source', ''),
                'createdAt': mem_time,
                'context': '',
                'expiresAt': mem_time + ttl_ms,
            }
            v3_data['daily'].append(v3_mem)

    # 保存
    save_memory(emp_id, v3_data)
    if archive_entries:
        archive_data = load_archive(emp_id)
        archive_data.setdefault('archived', []).extend(archive_entries)
        save_archive(emp_id, archive_data)

    return True


# ═══════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════

if __name__ == '__main__':
    # 简单自测
    test_emp = 'emp_test001'

    # 清空测试数据
    for fp in [_memory_file_path(test_emp), _archive_file_path(test_emp)]:
        if os.path.exists(fp):
            os.remove(fp)

    # 添加核心记忆
    m1 = add_memory(test_emp, '用户喜欢极简风格', key='preference',
                    source='user_input', priority=8, tags=['UI', '偏好'])
    print(f'[TEST] 添加核心记忆: {m1["id"]}')

    # 添加日常记忆
    m2 = add_memory(test_emp, '用户提到下周出差北京', key='auto',
                    source='ai_extract', context='用户：我下周要去北京出差...')
    print(f'[TEST] 添加日常记忆: {m2["id"]}')

    # 加载并查看
    data = load_memory(test_emp)
    print(f'[TEST] core={data["stats"]["coreCount"]}, daily={data["stats"]["dailyCount"]}')

    # 注入测试
    prompt = inject_memories(test_emp, '你是一个AI助手。')
    print(f'[TEST] 注入后 prompt 长度: {len(prompt)}')

    # 归档日常记忆
    archive_memory(test_emp, m2['id'], reason='manual')
    print(f'[TEST] 归档记忆 {m2["id"]}')

    # 查看归档
    archive_data = load_archive(test_emp)
    print(f'[TEST] 归档数量: {len(archive_data["archived"])}')

    # 恢复
    restored = restore_memory(test_emp, m2['id'])
    print(f'[TEST] 恢复记忆: {restored["id"] if restored else "None"}')

    # 统计
    stats = get_memory_stats(test_emp)
    print(f'[TEST] 统计: {json.dumps(stats, ensure_ascii=False)}')

    print('[TEST] 全部通过')
