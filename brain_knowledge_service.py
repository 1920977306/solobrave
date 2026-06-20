#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FIXME: 大脑知识中枢 - 知识沉淀服务
=================================
负责把主题归纳成结构化知识、增量更新知识、检测知识间冲突。
"""

import os
import json
import time
import uuid
import sqlite3
from collections import Counter

import numpy as np
import knowledge_service as ks

# FIXME: 复用项目数据库路径
DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'solobrave.db')

# FIXME: 冲突检测阈值（统一收口，方便后续调整）
CONFLICT_TOP_K = 5


def _db_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _now_ms():
    return int(time.time() * 1000)


def _new_id(prefix='know'):
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


def _compute_confidence(evidence_count):
    """FIXME: 基于证据数量计算可信度"""
    return round(min(0.5 + 0.1 * max(evidence_count, 1), 0.95), 2)


def _default_infer(prompt, agent=None):
    """FIXME: 兜底 AI 调用：未传入 infer_fn 时直接返回空 JSON"""
    print(f'  [BrainKnowledge] no infer_fn, skipping AI call', flush=True)
    return []


class KnowledgeService:
    """FIXME: 大脑知识中枢知识服务"""

    def __init__(self, db_path=None, infer_fn=None):
        self.db_path = db_path or DB_PATH
        self.infer_fn = infer_fn or _default_infer

    # ═══════════════════════════════════════════════════
    # 主题 → 知识沉淀
    # ═══════════════════════════════════════════════════

    def induct_topic_to_knowledge(self, topic_id, agent=None):
        """FIXME: 把主题下有效记忆归纳为 1-3 条结构化知识"""
        conn = _db_conn()
        try:
            topic = conn.execute('SELECT * FROM memory_topics WHERE id=?', (topic_id,)).fetchone()
            if not topic or topic['status'] != 'active':
                return []

            # FIXME: 修复大脑知识沉淀时命令行参数过长的bug：限制输入记忆数量和单条长度，避免 prompt 过长
            rows = conn.execute(
                '''SELECT id, value, emp_id FROM memory
                   WHERE status='active' AND topic_ids LIKE ?
                   AND is_filler=0 AND is_duplicate=0
                   ORDER BY created_at DESC LIMIT 20''',
                (f'%"{topic_id}"%',)
            ).fetchall()
            MAX_MEM_VALUE_LEN = 500
            rows = [
                {**dict(r), 'value': (r['value'] or '')[:MAX_MEM_VALUE_LEN]}
                for r in rows
            ]
            if not rows or len(rows) < 1:
                conn.execute('UPDATE memory_topics SET pending_induct=0 WHERE id=?', (topic_id,))
                conn.commit()
                return []

            mem_lines = [f'[{r["id"]}] {r["value"]}' for r in rows]
            evidence_ids = [r['id'] for r in rows]
            emp_ids = list(dict.fromkeys([r['emp_id'] for r in rows if r['emp_id']]))

            prompt = (
                "你是 SoloBrave 大脑知识中枢。请将以下同一主题的记忆整理成 1-3 条结构化知识。\n"
                "要求：\n"
                "1. 每条知识包含 title（标题）、content（Markdown 核心内容）、key_points（关键论点数组）。\n"
                "2. 不要编造记忆中没有的信息。\n"
                "3. 严格返回 JSON 数组，不要解释。\n\n"
                "记忆：\n" + '\n'.join(mem_lines)
            )
            docs = self.infer_fn(prompt, agent)
            if not isinstance(docs, list):
                return []

            created_ids = []
            now = _now_ms()
            for d in docs:
                if not isinstance(d, dict):
                    continue
                title = str(d.get('title', '')).strip()
                content = str(d.get('content', '')).strip()
                if not title or not content:
                    continue
                key_points = d.get('key_points') or []
                kid = _new_id('know')
                confidence = _compute_confidence(len(evidence_ids))
                conn.execute('''
                    INSERT INTO knowledge_base_new (id, title, content, key_points,
                                                    evidence_mem_ids, confidence,
                                                    topic_ids, created_at, updated_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    kid, title, content, _dump_json(key_points),
                    _dump_json(evidence_ids), confidence,
                    _dump_json([topic_id]), now, now, 'active'
                ))
                created_ids.append(kid)

            # 清空待沉淀标记
            conn.execute('''
                UPDATE memory_topics SET pending_induct=0, last_active_at=? WHERE id=?
            ''', (now, topic_id))
            conn.commit()
            return created_ids
        except Exception as e:
            print(f'  [BrainKnowledge] induct failed: {e}', flush=True)
            return []
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════
    # 增量更新知识
    # ═══════════════════════════════════════════════════

    def incrementally_update_knowledge(self, knowledge_id, new_mem_ids, agent=None):
        """FIXME: 新记忆进来时增量更新知识，不全量重写"""
        if not new_mem_ids:
            return None
        conn = _db_conn()
        try:
            know = conn.execute('SELECT * FROM knowledge_base_new WHERE id=?', (knowledge_id,)).fetchone()
            if not know:
                return None

            rows = conn.execute(
                f"SELECT id, value FROM memory WHERE id IN ({','.join('?'*len(new_mem_ids))})",
                tuple(new_mem_ids)
            ).fetchall()
            if not rows:
                return None
            new_lines = [f'[{r["id"]}] {r["value"]}' for r in rows]
            new_ids = [r['id'] for r in rows]

            old_evidence = _parse_json(know['evidence_mem_ids'], [])
            merged_evidence = list(dict.fromkeys(old_evidence + new_ids))

            prompt = (
                "你是 SoloBrave 大脑知识中枢。请基于以下新知识增量更新现有知识，不要全量重写。\n\n"
                "现有知识标题：" + know['title'] + "\n"
                "现有知识内容：\n" + know['content'] + "\n\n"
                "新增记忆：\n" + '\n'.join(new_lines) + "\n\n"
                "请返回 JSON 数组 [{title, content, key_points[]}]，只输出 JSON，不要解释。"
            )
            parsed = self.infer_fn(prompt, agent)
            if isinstance(parsed, list) and parsed:
                parsed = parsed[0]
            if not isinstance(parsed, dict):
                return None

            title = str(parsed.get('title', know['title'])).strip() or know['title']
            content = str(parsed.get('content', know['content'])).strip() or know['content']
            key_points = parsed.get('key_points') or _parse_json(know['key_points'], [])
            confidence = _compute_confidence(len(merged_evidence))

            conn.execute('''
                UPDATE knowledge_base_new
                SET title=?, content=?, key_points=?, evidence_mem_ids=?,
                    confidence=?, updated_at=?
                WHERE id=?
            ''', (title, content, _dump_json(key_points), _dump_json(merged_evidence),
                  confidence, _now_ms(), knowledge_id))
            conn.commit()
            return knowledge_id
        except Exception as e:
            print(f'  [BrainKnowledge] incremental update failed: {e}', flush=True)
            return None
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════
    # 冲突检测（embedding 召回 Top5 + AI 判断）
    # ═══════════════════════════════════════════════════

    def detect_conflicts(self, knowledge_id, agent=None):
        """FIXME: 检测该知识与其他知识是否矛盾"""
        conn = _db_conn()
        try:
            know = conn.execute('SELECT * FROM knowledge_base_new WHERE id=?', (knowledge_id,)).fetchone()
            if not know or know['status'] != 'active':
                return []

            # 获取目标知识 embedding
            emb_cfg = ks.get_embedding_config(None)
            know_vec = ks.get_embedding_cached(
                know['content'], emb_cfg['apiKey'], emb_cfg['provider'],
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
            )

            # 召回候选
            candidates = conn.execute(
                "SELECT id, content FROM knowledge_base_new WHERE status='active' AND id != ?",
                (knowledge_id,)
            ).fetchall()
            scored = []
            for c in candidates:
                cand_vec = ks.get_embedding_cached(
                    c['content'], emb_cfg['apiKey'], emb_cfg['provider'],
                    model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl')
                )
                sim = _cosine_sim(know_vec, cand_vec) if know_vec and cand_vec else 0.0
                scored.append((sim, c))
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:CONFLICT_TOP_K]

            conflicts = []
            for sim, c in top:
                if sim < 0.3:  # 语义完全无关则跳过
                    continue
                prompt = (
                    "判断以下两条知识是否相互矛盾。返回 JSON 数组 [{conflict: true/false, reason: \"\"}]，"
                    "只输出 JSON，不要解释。\n\n"
                    "知识 A：" + know['content'] + "\n\n"
                    "知识 B：" + c['content']
                )
                res = self.infer_fn(prompt, agent)
                if isinstance(res, list) and res:
                    res = res[0]
                if isinstance(res, dict) and res.get('conflict'):
                    rel_id = _new_id('rel')
                    conn.execute('''
                        INSERT INTO knowledge_relations (id, source_knowledge_id, target_knowledge_id,
                                                         relation_type, confidence, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (rel_id, knowledge_id, c['id'], 'conflict', round(sim, 3), _now_ms()))
                    conflicts.append({'relationId': rel_id, 'targetId': c['id'], 'reason': res.get('reason', '')})

            if conflicts:
                conn.execute("UPDATE knowledge_base_new SET status='conflict' WHERE id=?", (knowledge_id,))
            conn.commit()
            return conflicts
        except Exception as e:
            print(f'  [BrainKnowledge] conflict detect failed: {e}', flush=True)
            return []
        finally:
            conn.close()

    # ═══════════════════════════════════════════════════
    # 查询与反馈
    # ═══════════════════════════════════════════════════

    def get_knowledge_by_topic(self, topic_id, limit=100):
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM knowledge_base_new WHERE status='active' AND topic_ids LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f'%"{topic_id}"%', limit)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def get_all_active_knowledge(self, limit=500):
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM knowledge_base_new WHERE status='active' ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]
        finally:
            conn.close()

    def feedback_knowledge(self, knowledge_id, accurate=True, delta=0.1):
        """FIXME: 用户反馈调整可信度"""
        conn = _db_conn()
        try:
            row = conn.execute('SELECT confidence FROM knowledge_base_new WHERE id=?', (knowledge_id,)).fetchone()
            if not row:
                return False
            new_conf = round(max(0.0, min(1.0, row['confidence'] + (delta if accurate else -delta))), 2)
            conn.execute('UPDATE knowledge_base_new SET confidence=?, updated_at=? WHERE id=?',
                         (new_conf, _now_ms(), knowledge_id))
            conn.commit()
            return True
        except Exception as e:
            print(f'  [BrainKnowledge] feedback failed: {e}', flush=True)
            return False
        finally:
            conn.close()

    def _row_to_dict(self, row):
        return {
            'id': row['id'],
            'title': row['title'],
            'content': row['content'],
            'keyPoints': _parse_json(row['key_points'], []),
            'evidenceMemIds': _parse_json(row['evidence_mem_ids'], []),
            'confidence': row['confidence'],
            'topicIds': _parse_json(row['topic_ids'], []),
            'createdAt': row['created_at'],
            'updatedAt': row['updated_at'],
            'status': row['status'],
        }
