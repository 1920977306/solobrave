"""
embedding 失败留痕 + 缺向量补齐（2026-10-23）

覆盖老大口径「语义是智谱，我买的资源包」下的真问题：
  智谱余额/资源包不可用时返回 **HTTP 429**（body: {"code":"1113","message":"余额不足或无可用资源包"}）
  ⇒ 智谱把余额问题也报 429，别按状态码猜原因

核心缺陷（已修）：
  向量化失败时没有任何可查痕迹 ⇒ 新入库的分析结论永远缺向量
  ⇒ RAG 召不回（条件: status='ok' AND embedding IS NOT NULL AND embedding_model=模型）
  ⇒ 而旧重试入口只扫 status IN ('embedding_failed','error')，扫不到自动入库路径

修法：
  1. `_vectorize_kb_entry` 失败时写 kb_operation_log 'embedding_failed' 留痕
  2. **不改 status**（status 是审核闸语义 ok/pending，被管理员审核覆盖会成假绿灯）
  3. 补向量工具按「有 chunk 但 embedding IS NULL」扫描（与 kb_entry_stats.pendingChunks 同口径）

运行: python3 -m pytest tests/test_embedding_backfill.py -q
"""
import os
import sys
import sqlite3
import importlib.util

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(scope='module')
def srv():
    """加载 solobrave-server（DB 指向临时库）"""
    tmp_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_tmp_emb_backfill.db')
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    spec = importlib.util.spec_from_file_location('solobrave_server_emb', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def _conn():
        c = sqlite3.connect(tmp_db)
        c.row_factory = sqlite3.Row
        return c

    mod.DB_PATH = tmp_db
    mod._db_conn = _conn
    yield mod
    if os.path.exists(tmp_db):
        os.remove(tmp_db)


def _make_tables(srv):
    conn = srv._db_conn()
    try:
        conn.execute('''CREATE TABLE IF NOT EXISTS kb_entries (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            category TEXT DEFAULT '', category_id INTEGER, project_id TEXT DEFAULT '',
            scope TEXT DEFAULT 'global', team_id TEXT DEFAULT '', group_ids TEXT DEFAULT '[]',
            emp_id TEXT DEFAULT '', status TEXT DEFAULT 'ok', chunk_count INTEGER DEFAULT 0,
            created_by TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER,
            domain TEXT NOT NULL DEFAULT 'tuangzhang',
            tier TEXT DEFAULT 'legacy', source TEXT DEFAULT 'legacy')''')
        conn.execute('''CREATE TABLE IF NOT EXISTS kb_entry_chunks (
            id TEXT PRIMARY KEY, entry_id TEXT NOT NULL, emp_id TEXT DEFAULT '',
            chunk_index INTEGER, content TEXT NOT NULL, embedding BLOB,
            embedding_model TEXT DEFAULT '', created_at INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS kb_operation_log (
            id TEXT PRIMARY KEY, entry_id TEXT, operation TEXT NOT NULL,
            operator_id TEXT DEFAULT '', details TEXT DEFAULT '{}', created_at INTEGER)''')
        conn.commit()
    finally:
        conn.close()


class TestVectorizeFailureLeavesTrace:
    def test_failure_writes_operation_log(self, srv):
        """★ 向量化失败必须留痕 —— 否则「缺向量」这件事永远查不到、也补不回"""
        _make_tables(srv)
        conn = srv._db_conn()
        try:
            conn.execute("INSERT INTO kb_entries (id,title,content,status) VALUES ('kb_t1','T','内容','pending')")
            conn.commit()
        finally:
            conn.close()

        logged = []
        orig_log = srv.ks.kb_entry_log_operation

        def _capture(entry_id, operation, operator_id='', details=None):
            logged.append((entry_id, operation, details))
            return orig_log(entry_id, operation, operator_id, details)

        srv.ks.kb_entry_log_operation = _capture
        # 强制向量化抛错（模拟智谱 429）
        orig_vec = srv.ks._vectorize_kb_chunks
        orig_chunks = srv.ks._save_kb_chunks_without_embedding

        def _boom(*a, **k):
            raise RuntimeError('chunk 0 embedding failed')

        srv.ks._vectorize_kb_chunks = _boom
        srv.ks._save_kb_chunks_without_embedding = lambda *a, **k: None
        orig_cfg = srv.get_embedding_config
        srv.get_embedding_config = lambda emp_id=None: {
            'provider': 'zhipu', 'apiKey': 'k', 'baseUrl': 'https://x', 'model': 'embedding-2'}
        try:
            with pytest.raises(Exception):
                srv._vectorize_kb_entry('kb_t1', '内容')
        finally:
            srv.ks.kb_entry_log_operation = orig_log
            srv.ks._vectorize_kb_chunks = orig_vec
            srv.ks._save_kb_chunks_without_embedding = orig_chunks
            srv.get_embedding_config = orig_cfg

        assert len(logged) == 1, '失败时必须写 operation_log'
        entry_id, operation, details = logged[0]
        assert entry_id == 'kb_t1'
        assert operation == 'embedding_failed'
        assert details.get('provider') == 'zhipu'
        assert details.get('model') == 'embedding-2'
        assert 'hint' in details, '要留下「怎么补」的指引'

    def test_failure_does_not_change_status(self, srv):
        """★ 不许改 status —— status 是审核闸语义（ok/pending），
        被管理员审核覆盖会让「假绿灯」条目永远扫不到、永远补不回向量"""
        _make_tables(srv)
        conn = srv._db_conn()
        try:
            conn.execute("INSERT INTO kb_entries (id,title,content,status) VALUES ('kb_t2','T','内容','pending')")
            conn.commit()
        finally:
            conn.close()

        orig_vec = srv.ks._vectorize_kb_chunks
        orig_chunks = srv.ks._save_kb_chunks_without_embedding
        srv.ks._vectorize_kb_chunks = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'))
        srv.ks._save_kb_chunks_without_embedding = lambda *a, **k: None
        orig_cfg = srv.get_embedding_config
        srv.get_embedding_config = lambda emp_id=None: {
            'provider': 'zhipu', 'apiKey': 'k', 'baseUrl': 'https://x', 'model': 'embedding-2'}
        try:
            with pytest.raises(Exception):
                srv._vectorize_kb_entry('kb_t2', '内容')
        finally:
            srv.ks._vectorize_kb_chunks = orig_vec
            srv.ks._save_kb_chunks_without_embedding = orig_chunks
            srv.get_embedding_config = orig_cfg

        conn = srv._db_conn()
        try:
            row = conn.execute("SELECT status FROM kb_entries WHERE id='kb_t2'").fetchone()
        finally:
            conn.close()
        assert row['status'] == 'pending', 'status 不该被向量化失败改写'


class TestBackfillScript:
    def test_script_exists_and_parses(self):
        """补向量工具必须存在且语法正确（这是缺向量的唯一恢复入口）"""
        p = os.path.join(ROOT, 'scripts', 'backfill_missing_embeddings.py')
        assert os.path.exists(p), '缺向量补齐工具不存在'
        import ast
        ast.parse(open(p, encoding='utf-8').read())

    def test_script_scans_by_null_embedding(self):
        """★ 扫描口径必须是「有 chunk 但 embedding IS NULL」
        —— 与 kb_entry_stats.pendingChunks 同一口径，不靠 entry.status"""
        src = open(os.path.join(ROOT, 'scripts', 'backfill_missing_embeddings.py'), encoding='utf-8').read()
        assert 'embedding IS NULL' in src, '必须按 embedding IS NULL 扫'
        assert 'kb_entry_chunks' in src, '必须扫 chunk 表'
