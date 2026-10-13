# -*- coding: utf-8 -*-
"""
rag_retrieve_kb domain 分区单测 (kb-cleanup domain 改造)

测试目标（派单验收要求）:
构造一条 global 域条目、一条 tuangzhang 域条目、一条他域(other)条目，
断言: global 召回、tuangzhang 召回、他域即使相似度更高也不召回。
现有 scope 四层隔离逻辑不受 domain 影响（组合用例覆盖）。

策略与 test_rag_retrieve_kb_scope.py 一致:
in-memory SQLite + stub _db_conn / get_embedding_config / get_embedding_cached，
query 向量首维=1，chunk 向量首维=相似度，余弦可控确定性，不依赖网络。
"""
import json
import math
import sqlite3
import struct
import unittest

import knowledge_service as ks


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
    updated_at INTEGER,
    domain TEXT NOT NULL DEFAULT 'tuangzhang'
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
    r = math.sqrt(max(0.0, 1.0 - first * first))
    other = r / math.sqrt(DIM - 1)
    return [first] + [other] * (DIM - 1)


def pack(v):
    return struct.pack(f'{len(v)}f', *v)


class TestRagRetrieveKbDomain(unittest.TestCase):
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
        ks.get_embedding_cached = lambda *a, **k: [1.0] + [0.0] * (DIM - 1)
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

    def _add_entry(self, eid, title, domain, scope='global', emp_id='', sim=0.9):
        content = f'{title} 正文内容，足够长以代表一条知识。'
        now = ks._now_ms()
        self.real_conn.execute(
            '''INSERT INTO kb_entries
               (id,title,content,category,scope,team_id,group_ids,emp_id,status,chunk_count,created_by,created_at,updated_at,domain)
               VALUES (?,?,?,?,?,?,'[]',?, 'ok', 1, 't', ?, ?, ?)''',
            (eid, title, content, 'cat', scope, '', emp_id, now, now, domain)
        )
        emb = pack(vec_with_first(sim))
        self.real_conn.execute(
            '''INSERT INTO kb_entry_chunks (id,entry_id,emp_id,chunk_index,content,embedding,embedding_model,created_at)
               VALUES (?,?,?,0,?,?,?,?)''',
            (eid + '_c0', eid, emp_id, content, emb, MODEL, now)
        )
        self.real_conn.commit()

    def _call(self, query='测试查询', top_k=10):
        return ks.rag_retrieve_kb(
            query, '', top_k_docs=top_k,
            requester_id='u_alice', is_admin=False,
            team_ids=None, group_ids=None, emp_ids=['u_alice']
        )

    def _ids(self, result):
        return [d['id'] for d in result['docs']]

    def test_domain_filter_global_and_tuangzhang_visible_other_blocked(self):
        """global 域与 tuangzhang 域召回；他域条目相似度更高也不召回。"""
        self._add_entry('e_global', '通识层知识', domain='global', sim=0.90)
        self._add_entry('e_tuangzhang', '团长业务知识', domain='tuangzhang', sim=0.88)
        self._add_entry('e_other_domain', '他域高相似知识', domain='lvyou', sim=0.99)
        # domain 列 NOT NULL DEFAULT 'tuangzhang' → 不显式插入即 tuangzhang
        self.real_conn.execute(
            '''INSERT INTO kb_entries
               (id,title,content,category,scope,emp_id,status,chunk_count,created_by,created_at,updated_at)
               VALUES ('e_default','默认域知识','默认域正文内容，足够长。','cat','global','u_alice','ok',1,'t',0,0)''')
        self.real_conn.execute(
            '''INSERT INTO kb_entry_chunks (id,entry_id,emp_id,chunk_index,content,embedding,embedding_model,created_at)
               VALUES ('e_default_c0','e_default','u_alice',0,'默认域正文内容，足够长。',?,?,0)''',
            (pack(vec_with_first(0.86)), MODEL))
        self.real_conn.commit()

        ids = set(self._ids(self._call()))
        self.assertIn('e_global', ids, 'global 域应召回')
        self.assertIn('e_tuangzhang', ids, 'tuangzhang 域应召回')
        self.assertIn('e_default', ids, '默认域(tuangzhang)应召回')
        self.assertNotIn('e_other_domain', ids, '他域条目不应召回（即使相似度更高）')

    def test_domain_filter_applies_to_admin_too(self):
        """管理员也不跨域：domain 是数据面硬隔离，与 scope 特权无关。"""
        self._add_entry('e_tuangzhang', '团长业务知识', domain='tuangzhang', sim=0.90)
        self._add_entry('e_other', '他域知识', domain='finance', sim=0.99)

        result = ks.rag_retrieve_kb(
            '测试查询', '', top_k_docs=10,
            requester_id='admin', is_admin=True,
            team_ids=None, group_ids=None, emp_ids=None
        )
        ids = set(self._ids(result))
        self.assertIn('e_tuangzhang', ids)
        self.assertNotIn('e_other', ids, '管理员同样不可见他域')

    def test_scope_isolation_still_works_with_domain(self):
        """scope 隔离在 domain 叠加后不受影响（回归防护）。"""
        self._add_entry('e_global_pub', '公共', domain='global', scope='global', sim=0.90)
        self._add_entry('e_own', '我的', domain='global', scope='personal', emp_id='u_alice', sim=0.89)
        self._add_entry('e_other_personal', '他人私有', domain='global', scope='personal', emp_id='u_carol', sim=0.99)

        ids = set(self._ids(self._call()))
        self.assertIn('e_global_pub', ids)
        self.assertIn('e_own', ids)
        self.assertNotIn('e_other_personal', ids, 'scope 私有隔离不能被 domain 改动破坏')


if __name__ == '__main__':
    unittest.main()
