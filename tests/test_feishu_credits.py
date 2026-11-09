# -*- coding: utf-8 -*-
"""§117 飞书 AI 积分进积分中心 单测（2026-11-01，老大指令三部分之②）

背景（生产实测）：积分中心只有 OpenClaw 员工积分（credit_accounts），木木等飞书 AI
的消耗在 daren_bot 账本（bot_credits_accounts/ledger），两套账互不相通 ⇒ 没法给
飞书 AI 分配积分。

守卫点（行为断言，变异对照：砍 usage30d 过滤/砍代理 token 必红）：
1. daren_bot admin._get_credits：accounts 聚合（tenant_label 映射/绑定抖音号）+
   usage30d 只算 cost>0 且近 30 天的消耗（grant/refund 的负 cost 不计入、30 天外不计入）
2. server _daren_bot_admin_api：token 从现读文件取（不缓存）、URL 拼 token、
   带 payload 走 POST、对端 4xx 原样透传 status、连不上 ⇒ 502 不抛
3. server 充值 handler 参数校验：缺 tenant / 非整数 amount / 负数 amount ⇒ 400

铁律：零 prod 接触 — daren_bot db 走 DAREN_DB env 指 tmp（conftest 无此闸门，本文件
显式 reload）；server 代理的 urlopen 一律 monkeypatch，绝不真连 8092。
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
    spec = importlib.util.spec_from_file_location(
        'solobrave_server_feishu_credits', os.path.join(ROOT, 'solobrave-server.py'))
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
    return mod


@pytest.fixture(scope='module')
def srv():
    tmp = tempfile.mkdtemp(prefix='feishu_credits_srv_')
    yield _load_server(tmp)


@pytest.fixture()
def bot_db(monkeypatch):
    """daren_bot db 指向独立 tmp 库（DAREN_DB env + reload config/db）。"""
    tmp = tempfile.mkdtemp(prefix='feishu_credits_bot_')
    monkeypatch.setenv('DAREN_DB', os.path.join(tmp, 'daren_bot.db'))
    import daren_bot.config as cfg
    import daren_bot.db as ddb
    importlib.reload(cfg)
    importlib.reload(ddb)
    ddb.init_db()
    return ddb


# ---------- 守卫 1：bot 侧聚合 ----------

class TestBotCreditsAggregation:
    def _seed(self, ddb):
        with ddb.db_ctx() as conn:
            # bot_tenants: app_id 是主键（全局唯一）⇒ 每租户一个假 app_id
            for tk, app, dyid in (('mumu', 'cli_app_mumu', '62224890070'),
                                  ('daren_test2', 'cli_app_t2', '324733241')):
                conn.execute(
                    "INSERT INTO bot_tenants (app_id, tenant_key, app_secret_enc, bind_douyin_id, "
                    "status, created_at, updated_at) VALUES (?,?,?,?, 'active', 1, 1)",
                    (app, tk, 'enc', dyid))
            conn.execute("INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at) "
                         "VALUES ('mumu', 42, 1)")
            conn.execute("INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at) "
                         "VALUES ('daren_test2', 100, 1)")
            # mumu: 近30天消耗 100+20=120；一条 40 天前的老消耗不计；一条 grant(-500) 不计
            now = ddb.now_ts()
            for i, (cost, ts) in enumerate(((100, now - 86400), (20, now - 86400 * 5),
                                            (999, now - 86400 * 40), (-500, now - 86400))):
                conn.execute("INSERT INTO bot_credits_ledger (id, tenant_key, action, cost, "
                             "balance_after, cache_hit, ref_task_id, created_at) "
                             "VALUES (?,?,?,?,?,0,'',?)",
                             (f'l-mumu-{i}', 'mumu', 'probe', cost, 0, ts))
            conn.execute("INSERT INTO bot_credits_ledger (id, tenant_key, action, cost, "
                         "balance_after, cache_hit, ref_task_id, created_at) "
                         "VALUES ('l-t2','daren_test2','probe',30,0,0,'',?)", (now,))
        return ddb

    def test_get_credits_aggregates(self, bot_db):
        from daren_bot import admin
        self._seed(bot_db)
        data = admin._get_credits()
        assert 'accounts' in data and 'usage30d' in data
        accts = {a['tenant_key']: a for a in data['accounts']}
        assert accts['mumu']['tenant_label'] == '木木'
        assert accts['mumu']['balance'] == 42
        assert accts['mumu']['douyin_id'] == '62224890070'
        assert accts['daren_test2']['tenant_label'] == 'daren_test2', '未配置 fallback key'
        # usage30d：只算近 30 天 cost>0
        assert data['usage30d']['mumu'] == 120
        assert data['usage30d']['daren_test2'] == 30


# ---------- 守卫 2：server 代理 ----------

class TestProxy:
    def test_get_forwards_token_and_parses(self, srv, monkeypatch, tmp_path):
        tok = tmp_path / 'admin_token.txt'
        tok.write_text('test-token-abc')
        monkeypatch.setattr(srv, '_daren_bot_admin_token_path', lambda: str(tok))
        captured = {}

        class _Resp:
            status = 200

            def read(self):
                return json.dumps({'ok': True, 'accounts': []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            captured['url'] = req.full_url
            captured['method'] = req.get_method()
            return _Resp()

        monkeypatch.setattr(srv.urllib.request, 'urlopen', fake_urlopen)
        status, obj = srv._daren_bot_admin_api('/admin/api/credits')
        assert status == 200 and obj['ok'] is True
        assert 'token=test-token-abc' in captured['url']
        assert captured['method'] == 'GET'

    def test_post_payload_and_error_passthrough(self, srv, monkeypatch, tmp_path):
        tok = tmp_path / 'admin_token.txt'
        tok.write_text('tk')
        monkeypatch.setattr(srv, '_daren_bot_admin_token_path', lambda: str(tok))
        captured = {}

        class _Resp:
            status = 400

            def read(self):
                return json.dumps({'ok': False, 'error': '租户不存在'}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=0):
            captured['body'] = json.loads(req.data.decode())
            captured['method'] = req.get_method()
            return _Resp()

        monkeypatch.setattr(srv.urllib.request, 'urlopen', fake_urlopen)
        status, obj = srv._daren_bot_admin_api(
            '/admin/api/grant', payload={'tenant_key': 'mumu', 'amount': 500})
        assert status == 400, '对端 4xx 必须原样透传，不能吞成 200'
        assert obj['error'] == '租户不存在'
        assert captured['method'] == 'POST'
        assert captured['body'] == {'tenant_key': 'mumu', 'amount': 500}

    def test_unreachable_returns_502_not_raise(self, srv, monkeypatch, tmp_path):
        tok = tmp_path / 'admin_token.txt'
        tok.write_text('tk')
        monkeypatch.setattr(srv, '_daren_bot_admin_token_path', lambda: str(tok))

        def boom(req, timeout=0):
            raise OSError('connection refused')
        monkeypatch.setattr(srv.urllib.request, 'urlopen', boom)
        status, obj = srv._daren_bot_admin_api('/admin/api/credits')
        assert status == 502 and obj['ok'] is False

    def test_missing_token_file_returns_502(self, srv, monkeypatch, tmp_path):
        monkeypatch.setattr(srv, '_daren_bot_admin_token_path',
                            lambda: str(tmp_path / 'nonexistent.txt'))
        status, obj = srv._daren_bot_admin_api('/admin/api/credits')
        assert status == 502 and 'token' in obj['error']


# ---------- 守卫 3：充值 handler 参数校验 ----------

class _FakeHandler:
    """最小 handler 替身：只要 _read_body 行为， auth 由用例 monkeypatch _authenticate。"""


def _make_recharge_handler(srv, body):
    """构造 _handle_post_feishu_credits_recharge 的可调用替身（跳过 HTTP 层）。"""
    sent = {}

    class H:
        headers = {}
        client_address = ('127.0.0.1', 51234)

        def _read_body(self):
            return body

        def _send_json(self, code, obj):
            sent['code'] = code
            sent['obj'] = obj

        def _send_json_error(self, code, msg):
            sent['code'] = code
            sent['obj'] = {'error': msg}

    h = H()
    h._authenticate = None
    return h, sent


class TestRechargeValidation:
    def _call(self, srv, monkeypatch, body, auth_ok=True):
        h, sent = _make_recharge_handler(srv, body)

        auth = type('A', (), {'is_authenticated': True, 'is_admin': True, 'is_platform_admin': True,
                              'is_tenant_admin': False, 'user_id': 'boss',
                              'error': None, 'status': 401})
        monkeypatch.setattr(srv, '_authenticate', lambda *a, **k: auth)
        called = {}
        monkeypatch.setattr(srv, '_daren_bot_admin_api',
                            lambda path, payload=None: called.update(path=path, payload=payload)
                            or (200, {'ok': True, 'balance': 542}))
        import types
        bound = types.MethodType(srv.SoloBraveHandler._handle_post_feishu_credits_recharge, h) \
            if hasattr(srv, 'SoloBraveHandler') else None
        assert bound is not None, 'Handler 类名变了，探针要盯真名'
        bound()
        return sent, called

    def test_missing_tenant_rejected(self, srv, monkeypatch):
        sent, called = self._call(srv, monkeypatch, {'amount': 100})
        assert sent['code'] == 400 and 'tenant' in sent['obj']['error']
        assert not called, '参数不齐不许碰代理'

    def test_non_int_amount_rejected(self, srv, monkeypatch):
        sent, called = self._call(srv, monkeypatch, {'tenant': 'mumu', 'amount': 'abc'})
        assert sent['code'] == 400
        assert not called

    def test_negative_amount_rejected(self, srv, monkeypatch):
        sent, called = self._call(srv, monkeypatch, {'tenant': 'mumu', 'amount': -5})
        assert sent['code'] == 400
        assert not called

    def test_ok_forwards_and_renames(self, srv, monkeypatch):
        sent, called = self._call(srv, monkeypatch, {'tenant': 'mumu', 'amount': 500})
        assert sent['code'] == 200
        assert called['path'] == '/admin/api/grant'
        assert called['payload'] == {'tenant_key': 'mumu', 'amount': 500}
        assert sent['obj']['new_balance'] == 542
