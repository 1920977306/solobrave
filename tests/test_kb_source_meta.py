# -*- coding: utf-8 -*-
"""§117 知识库达人标识 单测（2026-11-01，老大指令三部分之①③）

背景（生产实测）：daren_bot 回流条目 created_by 一律 'daren_bot'，详情页直接显示机器
标识；达人发了商品/抖音号进知识库后，审核视图看不出是哪个达人、分析了什么账号。

守卫点（行为断言，变异对照：砍白名单/砍 createdByLabel 必红）：
1. _daren_kb_ingest 收 meta ⇒ kb_entries.source_meta 落白名单 JSON
2. meta 白名单：多余键丢弃、值 str 化截断（防注入——meta 最终回传前端渲染）
3. meta=None ⇒ source_meta 空串，不报错（老调用方兼容）
4. _kb_entry_row_to_dict：sourceMeta 解析 + createdByLabel 优先 meta.tenant_label，
   无 meta 回退 created_by；坏 JSON 防崩
5. init_kb_entries_db 幂等迁移：老表无 source_meta 列 ⇒ 补列；跑两次不炸
6. reflow.tenant_meta：label 映射（mumu→木木）/ 未配置 fallback tenant_key /
   db 查询失败降级不炸
7. reflow.push_kb_insight 把 meta 放进 payload（spy _post）

铁律：零 prod 接触 — server 独立 tmp 库加载；reflow 走运行期 env（conftest 已指死端口）。
"""
import importlib
import importlib.util
import json
import os
import sqlite3
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_server(tmp):
    """照 test_daren_reflow 模式：独立 tmp 库加载 server 模块。"""
    spec = importlib.util.spec_from_file_location(
        'solobrave_server_kb_meta', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    os.environ.setdefault('SOLOBRAVE_TEST_NO_SERVE', '1')
    spec.loader.exec_module(mod)
    mod.DATA_DIR = tmp
    mod.DB_PATH = os.path.join(tmp, 'solobrave.db')
    mod.AGENTS_FILE = os.path.join(tmp, 'agents.json')
    mod.USERS_FILE = os.path.join(tmp, 'users.json')
    mod.SECRET_FILE = os.path.join(tmp, '.secret')
    mod._ensure_data_dir()
    mod.init_db()
    import knowledge_service as ks
    _prev_server = ks._SERVER
    ks._SERVER = None
    ks.DB_PATH = mod.DB_PATH
    try:
        ks.init_kb_entries_db()
    finally:
        ks._SERVER = _prev_server
    return mod


@pytest.fixture(scope='module')
def srv():
    tmp = tempfile.mkdtemp(prefix='kb_meta_srv_')
    yield _load_server(tmp)


_GOOD_TEXT = '该达人近30天场均结算额 12.5 万, 粉丝价格带集中在 50-100 元, 建议优先推家清类目'
_META = {'tenant_key': 'mumu', 'tenant_label': '木木',
         'douyin_id': '62224890070', 'douyin_name': '洁洁爱户外'}


class TestIngestStoresMeta:
    def test_meta_persisted_and_returned(self, srv):
        kb_id, rejected, _ = srv._daren_kb_ingest(
            'talent_insight', _GOOD_TEXT, title='[问答] 测试', meta=dict(_META))
        assert not rejected and kb_id
        conn = sqlite3.connect(srv.DB_PATH)
        conn.row_factory = sqlite3.Row
        raw = conn.execute('SELECT source_meta FROM kb_entries WHERE id=?', (kb_id,)).fetchone()
        row = conn.execute('SELECT * FROM kb_entries WHERE id=?', (kb_id,)).fetchone()
        conn.close()
        assert set(json.loads(raw['source_meta']).keys()) == set(_META.keys())
        import knowledge_service as ks
        doc = ks._kb_entry_row_to_dict(row)
        assert doc['sourceMeta']['tenant_label'] == '木木'
        assert doc['sourceMeta']['douyin_name'] == '洁洁爱户外'
        assert doc['createdByLabel'] == '木木', '创建者必须显 AI 真名'
        assert doc['createdBy'] == 'daren_bot', '机器标识保留在 createdBy 不动'

    def test_meta_whitelist_drops_extra_and_stringifies(self, srv):
        dirty = dict(_META, injected='<script>alert(1)</script>', other=12345)
        kb_id, rejected, _ = srv._daren_kb_ingest('talent_insight', _GOOD_TEXT, meta=dirty)
        assert not rejected
        conn = sqlite3.connect(srv.DB_PATH)
        raw = conn.execute('SELECT source_meta FROM kb_entries WHERE id=?', (kb_id,)).fetchone()[0]
        conn.close()
        obj = json.loads(raw)
        assert 'injected' not in obj and 'other' not in obj, '白名单外键必须丢弃'
        assert obj['tenant_key'] == 'mumu'

    def test_none_meta_backward_compatible(self, srv):
        kb_id, rejected, _ = srv._daren_kb_ingest('talent_insight', _GOOD_TEXT + ' 无meta')
        assert not rejected
        conn = sqlite3.connect(srv.DB_PATH)
        conn.row_factory = sqlite3.Row
        raw = conn.execute('SELECT source_meta FROM kb_entries WHERE id=?', (kb_id,)).fetchone()[0]
        row = conn.execute('SELECT * FROM kb_entries WHERE id=?', (kb_id,)).fetchone()
        conn.close()
        assert raw in ('', None)
        import knowledge_service as ks
        doc = ks._kb_entry_row_to_dict(row)
        assert doc['sourceMeta'] == {}
        assert doc['createdByLabel'] == 'daren_bot', '无 meta 回退机器标识'

    def test_meta_json_helper_edge_cases(self, srv):
        assert srv._kb_source_meta_json(None) == ''
        assert srv._kb_source_meta_json('not-a-dict') == ''
        assert srv._kb_source_meta_json({}) == ''
        assert srv._kb_source_meta_json({'tenant_key': '  mumu  '}).count('mumu') == 1


class TestRowToDictRobust:
    def test_bad_json_meta_returns_empty(self, srv):
        import knowledge_service as ks
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''CREATE TABLE kb_entries (id TEXT, title TEXT, content TEXT, category TEXT,
            category_id INTEGER, project_id TEXT, scope TEXT, team_id TEXT, group_ids TEXT,
            emp_id TEXT, status TEXT, chunk_count INTEGER, created_by TEXT, created_at INTEGER,
            updated_at INTEGER, tier TEXT, source TEXT, source_meta TEXT)''')
        conn.execute('INSERT INTO kb_entries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     ('e1', 't', 'c', '', None, '', 'global', '', '[]', '', 'pending', 0,
                      'daren_bot', 1, 1, 'analysis', 'daren_bot', '{broken json'))
        row = conn.execute('SELECT * FROM kb_entries WHERE id=?', ('e1',)).fetchone()
        doc = ks._kb_entry_row_to_dict(row)
        conn.close()
        assert doc['sourceMeta'] == {}
        assert doc['createdByLabel'] == 'daren_bot'


class TestMigration:
    def test_adds_source_meta_column_idempotent(self, srv, tmp_path):
        import knowledge_service as ks
        # 模拟老库：只有核心列、没有 source_meta
        db_file = str(tmp_path / 'legacy_kb.db')
        conn = sqlite3.connect(db_file)
        conn.execute('''CREATE TABLE kb_entries (id TEXT PRIMARY KEY, title TEXT, content TEXT,
            category TEXT DEFAULT '', category_id INTEGER, project_id TEXT DEFAULT '',
            scope TEXT DEFAULT 'global', team_id TEXT DEFAULT '', group_ids TEXT DEFAULT '[]',
            emp_id TEXT DEFAULT '', status TEXT DEFAULT 'ok', chunk_count INTEGER DEFAULT 0,
            created_by TEXT DEFAULT '', created_at INTEGER, updated_at INTEGER,
            domain TEXT NOT NULL DEFAULT 'tuangzhang')''')
        conn.execute("INSERT INTO kb_entries (id, title, content) VALUES ('old1', '老条目', '内容')")
        conn.commit()
        conn.close()

        prev = ks.DB_PATH
        prev_server = ks._SERVER
        ks._SERVER = None
        ks.DB_PATH = db_file
        try:
            ks.init_kb_entries_db()
            ks.init_kb_entries_db()  # 幂等：跑两次不炸
        finally:
            ks.DB_PATH = prev
            ks._SERVER = prev_server
        conn = sqlite3.connect(db_file)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(kb_entries)')]
        val = conn.execute("SELECT source_meta FROM kb_entries WHERE id='old1'").fetchone()[0]
        conn.close()
        assert 'source_meta' in cols
        assert val in ('', None), '老行默认值空串'


# ---------- daren_bot 侧：tenant_meta / push 带 meta ----------

class TestReflowTenantMeta:
    def test_label_mapping_and_fallback(self, monkeypatch):
        import daren_bot.config as cfg
        import daren_bot.reflow as reflow
        assert cfg.tenant_label('mumu') == '木木'
        assert cfg.tenant_label('daren_boss') == 'daren_boss', '未配置 fallback tenant_key'
        assert cfg.tenant_label('') == ''

    def test_tenant_meta_composes_and_degrades(self, monkeypatch):
        import daren_bot.reflow as reflow
        monkeypatch.setattr(reflow.db, 'get_tenant',
                            lambda tk: {'bind_douyin_id': 'hlb725'})
        monkeypatch.setattr(reflow, 'snapshot_from_solobrave',
                            lambda dy: {'name': '洁洁爱户外'})
        meta = reflow.tenant_meta('mumu')
        assert meta == {'tenant_key': 'mumu', 'tenant_label': '木木',
                        'douyin_id': 'hlb725', 'douyin_name': '洁洁爱户外'}

        # db 查询失败 ⇒ 降级空串, 不炸
        def _boom(tk):
            raise RuntimeError('db down')
        monkeypatch.setattr(reflow.db, 'get_tenant', _boom)
        meta = reflow.tenant_meta('mumu')
        assert meta['tenant_label'] == '木木' and meta['douyin_id'] == ''
        assert reflow.tenant_meta('') == {}, '空 tenant 直接空 dict'

    def test_push_kb_insight_carries_meta(self, monkeypatch):
        import daren_bot.reflow as reflow
        captured = {}
        monkeypatch.setattr(reflow, '_post',
                            lambda path, payload: captured.update(path=path, payload=payload)
                            or (200, {}))
        ok = reflow.push_kb_insight('talent_insight', _GOOD_TEXT, title='x', meta=dict(_META))
        assert ok is True
        assert captured['payload']['meta']['tenant_label'] == '木木'

    def test_push_qa_exchange_and_verdict_carry_meta(self, monkeypatch):
        import daren_bot.reflow as reflow
        captured = []
        monkeypatch.setattr(reflow, '_post',
                            lambda path, payload: captured.append(payload) or (200, {}))
        monkeypatch.setattr(reflow, 'tenant_meta', lambda tk: dict(_META, tenant_key=tk))
        reflow.push_qa_exchange('mumu', '这个商品能带吗', '适合带的理由 ' * 5)
        reflow.push_product_verdict('mumu', '某商品', '选品结论 ' * 5)
        assert all(p['meta']['tenant_key'] == 'mumu' for p in captured)
        assert len(captured) == 2
