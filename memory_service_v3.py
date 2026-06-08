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
from collections import Counter

# ═══════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════

MEMORY_V3_DIR = os.path.join(os.path.dirname(__file__), 'data', 'memories')

MEMORY_V3_CONFIG = {
    'core_max': 100,           # 核心记忆池上限
    'daily_max': 100,          # 日常记录池上限
    'daily_ttl_days': 30,      # 日常记录过期天数
    'inject_core_max': 5,      # 注入时核心记忆条数
    'inject_daily_max': 3,     # 注入时日常记忆条数
    'inject_archive_max': 2,   # 注入时归档补充条数
    'inject_value_max': 500,   # 单条记忆注入字符上限
    'store_value_max': 2000,   # 单条记忆存储字符上限
    'context_max': 500,        # daily 上下文摘要字符上限
}

# 进程级文件锁
_file_locks = {}
_locks_mutex = threading.Lock()


def _get_file_lock(filepath):
    """获取文件路径对应的进程级写锁"""
    with _locks_mutex:
        if filepath not in _file_locks:
            _file_locks[filepath] = threading.Lock()
        return _file_locks[filepath]


def _ensure_dir(path):
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


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
    """原子写入 JSON 文件"""
    _ensure_dir(os.path.dirname(filepath))
    tmp_path = filepath + '.tmp.' + uuid.uuid4().hex[:8]
    file_lock = _get_file_lock(filepath)
    try:
        with file_lock:
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, filepath)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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
    # 更新 stats
    result['stats'] = {
        'coreCount': len(result['core']),
        'dailyCount': len(result['daily']),
        'totalAccess': sum(m.get('accessCount', 0) for m in result['core'])
    }
    return result


def save_memory(emp_id, data):
    """保存活跃记忆"""
    filepath = _memory_file_path(emp_id)
    data['updatedAt'] = int(time.time() * 1000)
    data['stats'] = {
        'coreCount': len(data.get('core', [])),
        'dailyCount': len(data.get('daily', [])),
        'totalAccess': sum(m.get('accessCount', 0) for m in data.get('core', []))
    }
    _write_json(filepath, data)


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
        'stats': {'coreCount': 0, 'dailyCount': 0, 'totalAccess': 0}
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
               priority=None, tags=None):
    """
    添加记忆
    key='auto' 或 'auto_extract' → daily 池
    其他值 → core 池
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
    else:
        memory['context'] = (context or '')[:cfg['context_max']]
        memory['expiresAt'] = now + ttl_ms

    target.append(memory)
    data[pool] = target
    save_memory(emp_id, data)
    return memory


def get_memory(emp_id, mem_id):
    """获取单条记忆（从活跃池中查找）"""
    data = load_memory(emp_id)
    for pool in ('core', 'daily'):
        for m in data.get(pool, []):
            if m.get('id') == mem_id:
                return m, pool
    return None, None


def update_memory(emp_id, mem_id, updates):
    """
    更新记忆
    updates: dict，可包含 value / key / source / priority / tags / context
    支持跨池移动（key 变更时）
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

    save_memory(emp_id, data)
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
    return restored


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
    log_data = _read_json(filepath, {'version': '3.0', 'logs': []})

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


# ═══════════════════════════════════════════════════
# 注入策略
# ═══════════════════════════════════════════════════

def inject_memories(emp_id, system_prompt=''):
    """
    为 AI 对话注入记忆，返回更新后的 system_prompt
    注入优先级：core（按priority+accessCount排序）→ daily（按时间倒序）→ archive（补充）
    """
    cfg = MEMORY_V3_CONFIG
    data = load_memory(emp_id)

    mem_lines = []

    # L1: 核心记忆 — 按 priority 降序，同 priority 按 accessCount 降序
    core_mems = sorted(
        data.get('core', []),
        key=lambda x: (x.get('priority', 5), x.get('accessCount', 0)),
        reverse=True
    )
    for m in core_mems[:cfg['inject_core_max']]:
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            mem_lines.append(f'- {val}')
            m['accessCount'] = m.get('accessCount', 0) + 1

    # L2: 日常记录 — 按时间倒序，只取未过期的
    daily_mems = sorted(
        data.get('daily', []),
        key=lambda x: x.get('createdAt', 0),
        reverse=True
    )
    for m in daily_mems[:cfg['inject_daily_max']]:
        val = m.get('value', '')[:cfg['inject_value_max']]
        if val:
            mem_lines.append(f'- {val}')

    # L3: 归档补充 — 当 L1+L2 不足时
    total_injected = len(mem_lines)
    target_total = cfg['inject_core_max'] + cfg['inject_daily_max']
    if total_injected < target_total:
        archive_data = load_archive(emp_id)
        archive_mems = sorted(
            archive_data.get('archived', []),
            key=lambda x: x.get('archivedAt', 0),
            reverse=True
        )
        need = min(cfg['inject_archive_max'], target_total - total_injected)
        for m in archive_mems[:need]:
            val = m.get('value', '')[:cfg['inject_value_max']]
            if val:
                mem_lines.append(f'- [归档] {val}')

    if mem_lines:
        system_prompt += '\n\n【关于用户的记忆】\n' + '\n'.join(mem_lines)

    # 更新 accessCount（需要写回）
    if core_mems:
        save_memory(emp_id, data)

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
