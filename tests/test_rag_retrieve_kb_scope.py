# -*- coding: utf-8 -*-
"""
rag_retrieve_kb 新版 RAG 检索 scope 隔离单测 (feat/kb-rag-switch)

测试目标（任务要求）:
1. 非管理员只能召回自己可读的知识（global 全员 / personal 本人及本人 agent / team 团队 / group 项目组），
   看不到其他员工 personal/team/group 的私有知识。
2. 管理员全量可见（不受 scope 限制）。
3. 审核闸 + 模型隔离：只召回 status='ok' 且 embedding_model 与 query 模型一致且 embedding 非空的 chunk。
4. 返回结构 {'docs':[...],'context':str} 与 rag_retrieve 一致。

策略（与 test_kb_vectorization.py / test_rag_index.py 一致）:
- in-memory SQLite 建真实 schema，stub ks._db_conn / get_embedding_config / get_embedding_cached。
- query 向量取第 0 维为 1，chunk 向量按"想要的相似度"手工构造（首维=s, 其余维补能量），
  使余弦结果可控且确定性，不依赖网络与真实 embedding API。
"""
import os
import sys
import json
import math
import sqlite3
import struct
import unittest

import knowledge_service as ks


# 产品代码会调 conn.close()，in-memory 连接被关后测试后续操作会报错 → close 设为 no-op
class _ImmuneConn:
    def __init__(self, real):
        self._real = real

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


SCHEMA_ENTRIES = '''
CREATE TABLE kb_entries (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT DEFAULT '',
    category_id INTEGER,
    project_id TEXT DEFAULT '',
    scope TEXT DEFAULT 'global',
    team_id TEXT DEFAULT '',
    group_ids TEXT DEFAULT '[]',
    emp_id TEXT DEFAULT '',
    status TEXT DEFAULT 'ok',
    chunk_count INTEGER DEFAULT 0,
    created_by TEXT DEFAULT '',
    created_at INTEGER,
    updated_at INTEGER
)
'''

SCHEMA_CHUNKS = '''
CREATE TABLE kb_entry_chunks (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    emp_id TEXT DEFAULT '',
    chunk_index INTEGER,
    content TEXT NOT NULL,
    embedding BLOB,
    embedding_model TEXT DEFAULT '',
    created_at INTEGER
)
'''

SCHEMA_LOG = '''
CREATE TABLE kb_operation_log (
    id TEXT PRIMARY KEY,
    entry_id TEXT,
    operation TEXT NOT NULL,
    operator_id TEXT DEFAULT '',
    details TEXT DEFAULT '{}',
    created_at INTEGER
)
'''

SCHEMA_CACHE = '''
CREATE TABLE embedding_cache (
    content_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    embedding BLOB NOT NULL,
    created_at INTEGER,
    PRIMARY KEY (content_hash, model)
)
'''

MODEL = 'embedding-2'
DIM = 8


def vec_with_first(first):
    """构造 DIM 维单位向量，首维=first(0<first<1)，其余维平分剩余能量。用于可控余弦。"""
    r = math.sqrt(max(0.0, 1.0 - first * first))
    other = r / math.sqrt(DIM - 1)
    return [first] + [other] * (DIM - 1)


def pack(v):
    return struct.pack(f'{len(v)}f', *v)


class TestRagRetrieveKbScope(unittest.TestCase):
    def setUp(self):
        self.real_conn = sqlite3.connect(':memory:', check_same_thread=False)
        self.real_conn.row_factory = sqlite3.Row
        self.real_conn.execute(SCHEMA_ENTRIES)
        self.real_conn.execute(SCHEMA_CHUNKS)
        self.real_conn.execute(SCHEMA_LOG)
        self.real_conn.execute(SCHEMA_CACHE)

        self.immune = _ImmuneConn(self.real_conn)

        self._orig_db_conn = ks._db_conn
        self._orig_get_emb_cfg = ks.get_embedding_config
        self._orig_get_emb_cached = ks.get_embedding_cached
        self._orig_rag_cache_get = ks._rag_cache_get
        self._orig_rag_cache_set = ks._rag_cache_set
        self._orig_log = ks.kb_entry_log_operation

        ks._db_conn = lambda: self.immune
        ks.get_embedding_config = lambda emp_id=None: {
            'provider': 'zhipu', 'apiKey': 'test-key',
            'model': MODEL, 'baseUrl': 'https://test'
        }
        # query 向量：首维 1，与所有 chunk（首维 s<1）余弦 = s
        ks.get_embedding_cached = lambda *a, **k: [1.0] + [0.0] * (DIM - 1)
        # 关闭 rag 结果缓存，保证每用例独立
        ks._rag_cache_get = lambda *a, **k: None
        ks._rag_cache_set = lambda *a, **k: None
        ks.kb_entry_log_operation = lambda *a, **k: None

    def tearDown(self):
        ks._db_conn = self._orig_db_conn
        ks.get_embedding_config = self._orig_get_emb_cfg
        ks.get_embedding_cached = self._orig_get_emb_cached
        ks._rag_cache_get = self._orig_rag_cache_get
        ks._rag_cache_set = self._orig_rag_cache_set
        ks.kb_entry_log_operation = self._orig_log
        self.real_conn.close()

    def _add_entry(self, eid, title, scope='global', emp_id='', team_id='', group_ids=None,
                   status='ok', model=MODEL, sim=0.9, with_embedding=True):
        content = f'{title} 正文内容，足够长以代表一条知识。'
        now = ks._now_ms()
        self.real_conn.execute(
            '''INSERT INTO kb_entries
               (id,title,content,category,scope,team_id,group_ids,emp_id,status,chunk_count,created_by,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,1,'t',?,?)''',
            (eid, title, content, 'cat', scope, team_id, json.dumps(group_ids or [], ensure_ascii=False),
             emp_id, status, now, now)
        )
        emb = pack(vec_with_first(sim)) if with_embedding else None
        self.real_conn.execute(
            '''INSERT INTO kb_entry_chunks (id,entry_id,emp_id,chunk_index,content,embedding,embedding_model,created_at)
               VALUES (?,?,?,0,?,?,?,?)''',
            (eid + '_c0', eid, emp_id, content, emb, model if with_embedding else '', now)
        )
        self.real_conn.commit()

    def _call(self, requester_id='u_alice', is_admin=False, team_ids=None, group_ids=None,
              emp_ids=None, query='测试查询', top_k=10):
        return ks.rag_retrieve_kb(
            query, '', top_k_docs=top_k,
            requester_id=requester_id, is_admin=is_admin,
            team_ids=team_ids, group_ids=group_ids, emp_ids=emp_ids
        )

    def _ids(self, result):
        return [d['id'] for d in result['docs']]

    def test_non_admin_sees_global_and_own_scope_only(self):
        """非管理员：能看到 global + 自己 personal + 自己 team + 自己 group；看不到他人私有。"""
        self._add_entry('e_global', '公共知识', scope='global', emp_id='', sim=0.90)
        self._add_entry('e_own_personal', '我自己的知识', scope='personal', emp_id='u_alice', sim=0.88)
        # u_alice 的 agent 员工创建的 personal 知识（应可读，因为 emp_ids 里带 agent id）
        self._add_entry('e_my_agent_personal', '我旗下agent的知识', scope='personal', emp_id='emp_agent_of_alice', sim=0.87)
        self._add_entry('e_own_team', '我团队知识', scope='team', emp_id='u_bob', team_id='team_red', sim=0.86)
        self._add_entry('e_own_group', '我项目组知识', scope='group', emp_id='u_bob', group_ids=['grp_alpha'], sim=0.85)
        # 他人私有 —— 不可见
        self._add_entry('e_other_personal', '别人的私有知识', scope='personal', emp_id='u_carol', sim=0.99)
        self._add_entry('e_other_team', '别的团队知识', scope='team', emp_id='u_dave', team_id='team_blue', sim=0.98)
        self._add_entry('e_other_group', '别的项目组知识', scope='group', emp_id='u_eve', group_ids=['grp_beta'], sim=0.97)

        result = self._call(
            requester_id='u_alice', is_admin=False,
            team_ids=['team_red'], group_ids=['grp_alpha'],
            emp_ids=['emp_agent_of_alice']
        )
        ids = set(self._ids(result))
        # 可见
        for expected in ('e_global', 'e_own_personal', 'e_my_agent_personal', 'e_own_team', 'e_own_group'):
            self.assertIn(expected, ids, f'非管理员应能读到 {expected}')
        # 不可见
        for blocked in ('e_other_personal', 'e_other_team', 'e_other_group'):
            self.assertNotIn(blocked, ids, f'非管理员不应读到他人私有 {blocked}')

    def test_admin_sees_all_scopes(self):
        """管理员：跨 scope 全量可见。"""
        self._add_entry('e_global', '公共', scope='global', emp_id='', sim=0.90)
        self._add_entry('e_carol_personal', 'carol私有', scope='personal', emp_id='u_carol', sim=0.89)
        self._add_entry('e_blue_team', '蓝团队', scope='team', emp_id='u_dave', team_id='team_blue', sim=0.88)
        self._add_entry('e_beta_group', 'beta组', scope='group', emp_id='u_eve', group_ids=['grp_beta'], sim=0.87)

        result = self._call(requester_id='admin_1', is_admin=True)
        ids = set(self._ids(result))
        for expected in ('e_global', 'e_carol_personal', 'e_blue_team', 'e_beta_group'):
            self.assertIn(expected, ids, f'管理员应全量可见 {expected}')

    def test_status_and_model_filter(self):
        """只召回 status=ok 且 embedding_model 匹配且 embedding 非空的 chunk。"""
        self._add_entry('e_ok', '有效知识', scope='global', status='ok', sim=0.90)
        self._add_entry('e_deleted', '已删除', scope='global', status='deleted', sim=0.95)
        self._add_entry('e_superseded', '已取代', scope='global', status='superseded', sim=0.94)
        # 模型不一致（如 bge-m3 chunk）不应被 embedding-2 query 召回
        self._add_entry('e_wrong_model', '模型不一致', scope='global', model='bge-m3', sim=0.93)
        # 向量缺失
        self._add_entry('e_no_emb', '无向量', scope='global', sim=0.92, with_embedding=False)

        result = self._call(requester_id='u_alice', is_admin=True)
        ids = set(self._ids(result))
        self.assertEqual(ids, {'e_ok'}, f'应只剩 ok+模型匹配+有向量 的条目，实际 {ids}')

    def test_return_contract(self):
        """返回结构与字段契约。"""
        self._add_entry('e_one', '唯一知识', scope='global', emp_id='', sim=0.80)
        result = self._call(requester_id='u_alice', is_admin=False, top_k=3)
        self.assertIsInstance(result, dict)
        self.assertIn('docs', result)
        self.assertIn('context', result)
        self.assertIsInstance(result['context'], str)
        self.assertEqual(len(result['docs']), 1)
        d = result['docs'][0]
        for key in ('id', 'title', 'category', 'content', 'relevantChunk', 'similarity'):
            self.assertIn(key, d, f'doc 缺字段 {key}')
        self.assertEqual(d['id'], 'e_one')
        self.assertGreater(d['similarity'], 0.0)
        self.assertTrue(d['relevantChunk'])


if __name__ == '__main__':
    unittest.main()
