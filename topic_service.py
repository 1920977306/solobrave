#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIXME: 大脑知识中枢 - 主题聚类服务
===============================
负责把清洗后的记忆归类到主题，维护主题中心向量，支持主题合并与查询。
"""

import os
import json
import time
import uuid
import sqlite3
import re
from collections import Counter

import numpy as np
import knowledge_service as ks
import memory_service_v3 as ms3

# FIXME: 复用项目数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'solobrave.db')

# FIXME: 主题聚类阈值（统一收口，方便后续调整）
TOPIC_CLASSIFY_THRESHOLD = 0.60
TOPIC_MERGE_SIM_THRESHOLD = 0.75
TOPIC_MERGE_OVERLAP_THRESHOLD = 0.30


def _db_conn():
    """创建 SQLite 连接（字典行）"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now_ms():
    return int(time.time() * 1000)


def _new_id(prefix='topic'):
    return f'{prefix}_' + uuid.uuid4().hex[:8]


def _parse_json(text, default=None):
    if default is None:
        default = []
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _dump_json(obj):
    return json.dumps(obj, ensure_ascii=False)


# FIXME: 主题中心向量序列化：numpy float32 字节数组
def _vec_to_bytes(vec):
    if vec is None:
        return None
    return np.array(vec, dtype=np.float32).tobytes()


def _vec_from_bytes(blob):
    if not blob:
        return None
    return np.frombuffer(blob, dtype=np.float32).tolist()


def _cosine_sim(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    a = np.array(a, dtype=np.float32)
    b = np.array(b, dtype=np.float32)
    na = np.linalg.norm(a)
    nb = np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _extract_keywords(text, topk=5):
    """FIXME: 简单提取关键词；中文按字/词分，英文按空格"""
    if not text:
        return []
    # 保留中文字符、英文单词、数字
    tokens = re.findall(r'[\u4e00-\u9fa5]{2,}|[a-zA-Z]+|\d+', str(text).lower())
    # 过滤常见停用词
    stop = {'这个', '那个', '然后', '就是', '什么', '怎么', '一个', '可以', '我们', '进行'}
    filtered = [t for t in tokens if t not in stop and len(t) > 1]
    counts = Counter(filtered)
    return [w for w, _ in counts.most_common(topk)]


def _topic_ids_contains(topic_ids_json, topic_id):
    try:
        ids = json.loads(topic_ids_json) if topic_ids_json else []
    except Exception:
        return False
    return topic_id in ids


class TopicService:
    """FIXME: 大脑知识中枢主题服务"""

    def __init__(self, db_path=None):
        self.db_path = db_path or DB_PATH

    # ═══════════════════════════════════════════════════
    # 核心：记忆归类到主题
    # ═══════════════════════════════════════════════════

    def classify_memory_to_topic(self, mem_id, emp_id):
        """FIXME: 将记忆归类到最匹配的主题；相似度<阈值则创建新主题"""
        conn = _db_conn()
        try:
            row = conn.execute(
                'SELECT value, is_filler, is_duplicate, status FROM memory WHERE id=? AND emp_id=?',
                (mem_id, emp_id)
            ).fetchone()
            if not row:
                return None
            if row['is_filler'] or row['is_duplicate'] or row['status'] != 'active':
                return None
            value = row['value'] or ''

            # 获取记忆 embedding
            emb_cfg = ks.get_embedding_config(emp_id)
            mem_vec = ks.get_embedding_cached(
                value, emb_cfg['apiKey'], emb_cfg['provider'],
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
            )
            if not mem_vec:
                return None

            # 查找所有 active 主题，计算相似度
            topics = conn.execute(
                "SELECT id, mem_count, center_embedding FROM memory_topics WHERE status='active'"
            ).fetchall()
            best_topic = None
            best_sim = 0.0
            for t in topics:
                center = _vec_from_bytes(t['center_embedding'])
                if not center:
                    continue
                sim = _cosine_sim(mem_vec, center)
                if sim > best_sim:
                    best_sim = sim
                    best_topic = t

            now = _now_ms()
            if best_topic and best_sim >= TOPIC_CLASSIFY_THRESHOLD:
                topic_id = best_topic['id']
                self._add_mem_to_topic(conn, topic_id, emp_id, mem_id, mem_vec, best_topic['mem_count'])
            else:
                topic_id = self._create_topic(conn, emp_id, mem_id, value, mem_vec, now)

            conn.commit()
            # FIXME: 同步到 JSON 记忆文件
            row = conn.execute('SELECT topic_ids FROM memory WHERE id=?', (mem_id,)).fetchone()
            merged_ids = _parse_json(row['topic_ids'] if row else '[]', [])
            ms3.set_memory_topics(emp_id, mem_id, merged_ids)
            return topic_id
        except Exception as e:
            print(f'  [TopicService] classify failed: {e}', flush=True)
            return None
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════
    # 内部：主题维护
    # ═══════════════════════════════════════════════════

    def _create_topic(self, conn, emp_id, mem_id, value, mem_vec, now):
        """FIXME: 创建新主题"""
        topic_id = _new_id('topic')
        title = (value[:40] + '...') if len(value) > 40 else value
        key_words = _extract_keywords(value)
        conn.execute('''
            INSERT INTO memory_topics (id, title, key_words, emp_ids, mem_count,
                                       first_seen_at, last_active_at, status,
                                       pending_induct, center_embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            topic_id, title, _dump_json(key_words), _dump_json([emp_id]), 1,
            now, now, 'active', 1, _vec_to_bytes(mem_vec)
        ))
        self._set_memory_topic(conn, mem_id, topic_id)
        return topic_id

    def _add_mem_to_topic(self, conn, topic_id, emp_id, mem_id, mem_vec, old_count):
        """FIXME: 把记忆加入已有主题并更新中心向量"""
        new_count = old_count + 1
        old_center = _vec_from_bytes(
            conn.execute('SELECT center_embedding FROM memory_topics WHERE id=?', (topic_id,)).fetchone()['center_embedding']
        )
        if old_center and mem_vec:
            new_center = [(old_center[i] * old_count + mem_vec[i]) / new_count for i in range(len(mem_vec))]
        else:
            new_center = mem_vec

        # 更新员工列表
        row = conn.execute('SELECT emp_ids FROM memory_topics WHERE id=?', (topic_id,)).fetchone()
        emp_ids = _parse_json(row['emp_ids'], [])
        if emp_id not in emp_ids:
            emp_ids.append(emp_id)

        conn.execute('''
            UPDATE memory_topics
            SET mem_count=?, last_active_at=?, emp_ids=?, pending_induct=1,
                center_embedding=?
            WHERE id=?
        ''', (new_count, _now_ms(), _dump_json(emp_ids), _vec_to_bytes(new_center), topic_id))
        self._set_memory_topic(conn, mem_id, topic_id)

    def _set_memory_topic(self, conn, mem_id, topic_id):
        """FIXME: 在 memory 表的 topic_ids 中追加主题"""
        row = conn.execute('SELECT topic_ids FROM memory WHERE id=?', (mem_id,)).fetchone()
        ids = _parse_json(row['topic_ids'] if row else '[]', [])
        if topic_id not in ids:
            ids.append(topic_id)
        conn.execute('UPDATE memory SET topic_ids=? WHERE id=?', (_dump_json(ids), mem_id))

    def _recompute_topic_center(self, conn, topic_id):
        """FIXME: 根据主题下所有记忆重新计算中心向量"""
        mems = self.get_topic_memories(topic_id)
        if not mems:
            return None
        emb_cfg = ks.get_embedding_config(None)
        vectors = []
        for m in mems:
            vec = ks.get_embedding_cached(
                m.get('value', ''), emb_cfg['apiKey'], emb_cfg['provider'],
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
            )
            if vec:
                vectors.append(vec)
        if not vectors:
            return None
        arr = np.array(vectors, dtype=np.float32)
        center = np.mean(arr, axis=0).tolist()
        conn.execute('UPDATE memory_topics SET center_embedding=? WHERE id=?',
                     (_vec_to_bytes(center), topic_id))
        return center

    # ═══════════════════════════════════════════════════
    # 主题合并
    # ═══════════════════════════════════════════════════

    def merge_topics(self, topic_id1, topic_id2):
        """FIXME: 相似度≥阈值且成员重叠≥阈值时自动合并主题"""
        if topic_id1 == topic_id2:
            return False
        conn = _db_conn()
        try:
            t1 = conn.execute('SELECT * FROM memory_topics WHERE id=?', (topic_id1,)).fetchone()
            t2 = conn.execute('SELECT * FROM memory_topics WHERE id=?', (topic_id2,)).fetchone()
            if not t1 or not t2:
                return False
            if t1['status'] != 'active' or t2['status'] != 'active':
                return False

            c1 = _vec_from_bytes(t1['center_embedding'])
            c2 = _vec_from_bytes(t2['center_embedding'])
            sim = _cosine_sim(c1, c2) if c1 and c2 else 0.0

            mems1 = {m['id'] for m in self.get_topic_memories(topic_id1)}
            mems2 = {m['id'] for m in self.get_topic_memories(topic_id2)}
            union = mems1 | mems2
            overlap = len(mems1 & mems2) / len(union) if union else 0.0

            if sim < TOPIC_MERGE_SIM_THRESHOLD or overlap < TOPIC_MERGE_OVERLAP_THRESHOLD:
                return False

            # 以成员多的主题为主
            if t2['mem_count'] > t1['mem_count']:
                t1, t2 = t2, t1
                topic_id1, topic_id2 = topic_id2, topic_id1
                mems1, mems2 = mems2, mems1

            now = _now_ms()
            # 迁移 t2 的记忆到 t1
            moved = 0
            for mem_id in mems2:
                row = conn.execute('SELECT topic_ids FROM memory WHERE id=?', (mem_id,)).fetchone()
                ids = _parse_json(row['topic_ids'] if row else '[]', [])
                if topic_id2 in ids:
                    ids.remove(topic_id2)
                if topic_id1 not in ids:
                    ids.append(topic_id1)
                conn.execute('UPDATE memory SET topic_ids=? WHERE id=?', (_dump_json(ids), mem_id))
                moved += 1

            new_count = t1['mem_count'] + moved
            emp_ids = list(dict.fromkeys(_parse_json(t1['emp_ids'], []) + _parse_json(t2['emp_ids'], [])))
            conn.execute('''
                UPDATE memory_topics
                SET mem_count=?, last_active_at=?, emp_ids=?, pending_induct=1
                WHERE id=?
            ''', (new_count, now, _dump_json(emp_ids), topic_id1))
            conn.execute('''
                UPDATE memory_topics
                SET status='merged', mem_count=0, last_active_at=?
                WHERE id=?
            ''', (now, topic_id2))

            # 重新计算中心向量
            self._recompute_topic_center(conn, topic_id1)
            conn.commit()
            return True
        except Exception as e:
            print(f'  [TopicService] merge failed: {e}', flush=True)
            return False
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════
    # 查询
    # ═══════════════════════════════════════════════════

    def get_emp_topics(self, emp_id, limit=100):
        """FIXME: 获取员工参与的所有 active 主题"""
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM memory_topics WHERE status='active' AND emp_ids LIKE ? ORDER BY last_active_at DESC LIMIT ?",
                (f'%"{emp_id}"%', limit)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_topic_memories(self, topic_id, limit=200):
        """FIXME: 获取主题下的 active 记忆"""
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM memory WHERE status='active' AND topic_ids LIKE ? ORDER BY created_at DESC LIMIT ?",
                (f'%"{topic_id}"%', limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_topic_by_id(self, topic_id):
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM memory_topics WHERE id=?', (topic_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        finally:
            conn.close()

    def _row_to_dict(self, row):
        return {
            'id': row['id'],
            'title': row['title'],
            'keyWords': _parse_json(row['key_words'], []),
            'empIds': _parse_json(row['emp_ids'], []),
            'memCount': row['mem_count'],
            'firstSeenAt': row['first_seen_at'],
            'lastActiveAt': row['last_active_at'],
            'status': row['status'],
            'pendingInduct': row['pending_induct'],
        }

    # ═══════════════════════════════════════════════════
    # 工具：列出待沉淀主题
    # ═══════════════════════════════════════════════════

    def get_pending_induct_topics(self, min_memories=3):
        """FIXME: 获取带待沉淀标记且有效记忆数≥阈值的 active 主题"""
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM memory_topics WHERE status='active' AND pending_induct=1 ORDER BY last_active_at DESC"
            ).fetchall()
            result = []
            for r in rows:
                mems = self.get_topic_memories(r['id'])
                valid = [m for m in mems if not m.get('is_filler') and not m.get('is_duplicate')]
                if len(valid) >= min_memories:
                    d = self._row_to_dict(r)
                    d['validMemCount'] = len(valid)
                    result.append(d)
            return result
        finally:
            conn.close()
