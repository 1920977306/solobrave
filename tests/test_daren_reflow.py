"""feat/daren-reflow 测试 (2026-10-18): daren_bot → SoloBrave 数据回流三管道。

覆盖:
- 管道 1: _daren_upsert_talent 新建/更新/白名单/status 不可注入/缺 douyin_id 拒绝
- 管道 2: _daren_kb_ingest kind 白名单/长度/低质量闸/review_required 强制 pending
- 鉴权: _internal_auth_ok localhost + 密钥双校验
- 管道 3: daren_bot db save_task_payload + purge_old_payloads (行保留只清 payload)
- 快照: reflow.snapshot_from_solobrave 从 talents 取数

铁律: 零 prod 接触 — server 用独立 tmp 库加载, daren_bot 用 DAREN_DB env 指 tmp。
"""
import importlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_server(tmp):
    """照 conftest 模式 2: 独立 tmp 库加载 server 模块 (不动 prod 路径)。"""
    spec = importlib.util.spec_from_file_location(
        'solobrave_server_daren_reflow', os.path.join(ROOT, 'solobrave-server.py'))
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
    # kb_entries 由 knowledge_service 独立建表 (server 启动时调), 测试也要显式补。
    # 注意 ks._db_conn 优先走 ks._SERVER 注册的路由 —— 全量跑时可能已被别的测试文件
    # 注册到别的实例/库, 这里临时摘掉注册强制直连本 fixture 的 tmp 库, 用完恢复。
    import knowledge_service as ks
    _prev_server = ks._SERVER
    ks._SERVER = None
    ks.DB_PATH = mod.DB_PATH
    try:
        ks.init_kb_entries_db()
    finally:
        ks._SERVER = _prev_server
    # 共享密钥文件也指到 tmp, 不碰 worktree/prod data/
    mod._INTERNAL_SECRET_PATH = os.path.join(tmp, 'internal_secret.txt')
    return mod


@pytest.fixture(scope='module')
def srv():
    tmp = tempfile.mkdtemp(prefix='daren_reflow_srv_')
    yield _load_server(tmp)


@pytest.fixture()
def daren_db(monkeypatch):
    """daren_bot db 指向独立 tmp 库 (DAREN_DB env + reload config/db)。"""
    tmp = tempfile.mkdtemp(prefix='daren_reflow_bot_')
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    importlib.reload(cfg)
    importlib.reload(ddb)
    ddb.init_db()   # DDL 由服务启动时执行, 测试显式补
    return ddb


# ---------- 管道 1: 达人库 upsert ----------

class TestTalentUpsert:
    def test_create_new_talent(self, srv):
        tid, created = srv._daren_upsert_talent({
            'douyin_id': 'reflow_dy_001', 'name': '回流测试达人',
            'followers': 12345, 'total_gmv': 67890.0,
            'ai_rating': '潜力型', 'ai_summary': '粉丝价格带集中在 50-100 元, 适合家清类目',
        })
        assert created is True
        conn = sqlite3.connect(srv.DB_PATH)
        row = conn.execute("SELECT * FROM talents WHERE id=?", (tid,)).fetchone()
        cols = [d[0] for d in conn.execute("SELECT * FROM talents LIMIT 0").description]
        conn.close()
        d = dict(zip(cols, row))
        assert d['douyin_id'] == 'reflow_dy_001'
        assert d['name'] == '回流测试达人'
        assert d['created_by'] == 'daren_bot'
        assert d['status'] == 'active'
        assert d['platform'] == '抖音'

    def test_update_existing_no_dup(self, srv):
        tid1, c1 = srv._daren_upsert_talent({'douyin_id': 'reflow_dy_002', 'name': '甲', 'followers': 1})
        tid2, c2 = srv._daren_upsert_talent({'douyin_id': 'reflow_dy_002', 'name': '乙', 'followers': 2})
        assert c1 is True and c2 is False and tid1 == tid2
        conn = sqlite3.connect(srv.DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM talents WHERE douyin_id='reflow_dy_002'").fetchone()[0]
        name = conn.execute("SELECT name FROM talents WHERE id=?", (tid1,)).fetchone()[0]
        conn.close()
        assert n == 1 and name == '乙'

    def test_whitelist_blocks_status_injection(self, srv):
        """status 不在白名单 — 即使快照带 status='archived' 也不能改 (防复活/误杀)。"""
        tid, _ = srv._daren_upsert_talent({'douyin_id': 'reflow_dy_003', 'name': '丙'})
        srv._daren_upsert_talent({'douyin_id': 'reflow_dy_003', 'status': 'archived', 'name': '丙2'})
        conn = sqlite3.connect(srv.DB_PATH)
        st = conn.execute("SELECT status FROM talents WHERE id=?", (tid,)).fetchone()[0]
        nm = conn.execute("SELECT name FROM talents WHERE id=?", (tid,)).fetchone()[0]
        conn.close()
        assert st == 'active' and nm == '丙2'

    def test_requires_douyin_id(self, srv):
        with pytest.raises(ValueError):
            srv._daren_upsert_talent({'name': '无抖音号'})


# ---------- 管道 2: 知识库 ingest ----------

class TestKbIngest:
    def test_reject_unknown_kind(self, srv):
        kb_id, rejected, reason = srv._daren_kb_ingest('chat_log', '这是一段足够长的有效业务文本 123')
        assert rejected and kb_id is None and '白名单' in reason

    def test_reject_short(self, srv):
        _, rejected, reason = srv._daren_kb_ingest('talent_insight', '太短')
        assert rejected and '过短' in reason

    def test_reject_low_quality(self, srv):
        _, rejected, _ = srv._daren_kb_ingest('talent_insight', '未能识别, 请用户重新发送截图后再分析')
        assert rejected

    def test_accept_good_insight_pending(self, srv):
        kb_id, rejected, _ = srv._daren_kb_ingest(
            'talent_insight', '该达人近30天场均结算额 12.5 万, 粉丝价格带集中在 50-100 元, 建议优先推家清类目',
            title='[达人采集] 测试达人A')
        assert not rejected and kb_id
        conn = sqlite3.connect(srv.DB_PATH)
        row = conn.execute("SELECT status, created_by, domain FROM kb_entries WHERE id=?", (kb_id,)).fetchone()
        conn.close()
        assert row[0] == 'pending'          # review_required 强制 pending, 人工审过才进 RAG
        assert row[1] == 'daren_bot'


# ---------- 鉴权 ----------

class TestInternalAuth:
    def _handler(self, srv, ip, secret):
        h = types.SimpleNamespace(
            client_address=(ip, 51234),
            headers={'X-Internal-Secret': secret})
        return h

    def test_ok_localhost_right_secret(self, srv, monkeypatch):
        monkeypatch.setattr(srv, '_internal_secret', lambda: 's3cret')
        assert srv._internal_auth_ok(self._handler(srv, '127.0.0.1', 's3cret')) is True

    def test_wrong_secret(self, srv, monkeypatch):
        monkeypatch.setattr(srv, '_internal_secret', lambda: 's3cret')
        assert srv._internal_auth_ok(self._handler(srv, '127.0.0.1', 'wrong')) is False

    def test_non_localhost_blocked(self, srv, monkeypatch):
        monkeypatch.setattr(srv, '_internal_secret', lambda: 's3cret')
        assert srv._internal_auth_ok(self._handler(srv, '192.168.1.25', 's3cret')) is False


# ---------- 管道 3: 本地留存 + 滚动清理 ----------

class TestPayloadRetention:
    def test_save_and_purge(self, daren_db):
        tid = daren_db.enqueue_task('daren_test', 'douyin', 'dy_purge_1', 0)
        daren_db.mark_task(tid, 'done')
        snap = {'douyin_id': 'dy_purge_1', 'followers': 999}
        daren_db.save_task_payload(tid, json.dumps(snap))
        task = daren_db.get_task(tid)
        assert json.loads(task['payload'])['followers'] == 999

        # 把 done_at 拨老 31 天 → purge 应清 payload 但保留行
        old_ts = daren_db.now_ts() - 31 * 86400
        with daren_db.db_ctx() as conn:
            conn.execute("UPDATE bot_collect_tasks SET done_at=? WHERE id=?", (old_ts, tid))
        n = daren_db.purge_old_payloads(days=30)
        assert n == 1
        task = daren_db.get_task(tid)
        assert task['payload'] == ''
        assert task['status'] == 'done'   # 行保留, 只清内容

    def test_purge_keeps_recent(self, daren_db):
        tid = daren_db.enqueue_task('daren_test', 'douyin', 'dy_purge_2', 0)
        daren_db.mark_task(tid, 'done')
        daren_db.save_task_payload(tid, '{"douyin_id":"dy_purge_2"}')
        assert daren_db.purge_old_payloads(days=30) == 0
        assert json.loads(daren_db.get_task(tid)['payload'])['douyin_id'] == 'dy_purge_2'


# ---------- 快照 ----------

class TestSnapshot:
    def test_snapshot_reads_talents(self, srv, monkeypatch):
        srv._daren_upsert_talent({'douyin_id': 'reflow_snap_1', 'name': '快照哥', 'followers': 777})
        from daren_bot import reflow
        monkeypatch.setattr(reflow.executors, 'SOLOBRAVE_DB', srv.DB_PATH)
        snap = reflow.snapshot_from_solobrave('reflow_snap_1')
        assert snap and snap['name'] == '快照哥' and snap['followers'] == 777

    def test_snapshot_miss_returns_none(self, srv, monkeypatch):
        from daren_bot import reflow
        monkeypatch.setattr(reflow.executors, 'SOLOBRAVE_DB', srv.DB_PATH)
        assert reflow.snapshot_from_solobrave('dy_not_exist_zzz') is None
