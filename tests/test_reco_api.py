# -*- coding: utf-8 -*-
"""单1②③ 守卫（2026-11-23）：推荐链出口 API（P2 推荐链出口三件套）。

守卫矩阵：
GET /api/recommendations?date=
1. B 表 LEFT JOIN talents：行数据 + 抖音号/粉丝/台账当前判定（verdict_now）合并
2. date 参数校验：非法格式 400
3. 带品卡段：outreach_card 组装的卡文本附在 it['card']；组装失败不阻断列表
4. 未登录 401（_authenticate 打桩）
POST /api/recommendations/:rec_id/status
5. 白名单流转：recommended→contacted 写回 + 留痕；非白名单 400；不存在 404

零 prod 接触：server 模块独立装载，_authenticate/_db_conn 全 monkeypatch。
"""
import importlib.util
import json
import os
import sqlite3
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)

_SCHEMA_REC = '''CREATE TABLE talent_recommendations (
    rec_id TEXT PRIMARY KEY, rec_date TEXT NOT NULL, talent_id TEXT NOT NULL,
    talent_name TEXT NOT NULL DEFAULT '', matched_rules TEXT NOT NULL DEFAULT '[]',
    reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
    pipeline_status TEXT NOT NULL DEFAULT 'pending', deal_pid TEXT, gmv REAL,
    refund_flag INTEGER, run_id TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE(rec_date, talent_id))'''

_SCHEMA_TALENTS = '''CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, douyin_id TEXT, followers INTEGER,
    remark TEXT, category TEXT)'''


def _mk_db(tmp_path):
    db = tmp_path / 'reco_api.db'
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute(_SCHEMA_REC)
    conn.execute(_SCHEMA_TALENTS)
    conn.execute("INSERT INTO talents VALUES ('tal_sq_t1', '赵总外贸家居', "
                 "'DW_ZHAO', 410000, "
                 "'{\"square_judgment\": {\"verdict\": \"值得联系\"}}', '智能家居')")
    conn.execute("INSERT INTO talent_recommendations (rec_id, rec_date, talent_id, "
                 "talent_name, matched_rules, reason_text, snapshot_json, "
                 "pipeline_status, run_id, created_at, updated_at) VALUES "
                 "('rec_20261123_tal_sq_t1', '2026-11-23', 'tal_sq_t1', "
                 "'赵总外贸家居', '[]', '智能家居主推，30天结算2.5万-5万', "
                 "'{\"主推类目\": [\"智能家居\"]}', 'recommended', "
                 "'ssq_test', 1700000000000, 1700000000000)")
    conn.commit()
    conn.close()
    return db


class _FakeHandler:
    """最小 handler 替身：捕获 _send_json 输出。"""
    _RECO_STATUS_WHITELIST = srv.SoloBraveHandler._RECO_STATUS_WHITELIST

    def __init__(self, path='/api/recommendations?date=2026-11-23'):
        self.path = path
        self.client_address = ('127.0.0.1', 0)
        self.headers = {}
        self.out = None

    def _send_json(self, code, data):
        self.out = (code, data)

    def _send_json_error(self, code, message):
        self.out = (code, {'error': message})

    def _send_auth_error(self, message, status=401):
        self.out = (status, {'error': message})

    def _read_body(self):
        return self._body if hasattr(self, '_body') else None


def _authed(monkeypatch):
    a = srv.AuthResult(user_info={'userId': 'user_boss', 'role': 'admin'})
    monkeypatch.setattr(srv, '_authenticate', lambda *args, **kw: a)


def _wired(tmp_path, monkeypatch, path='/api/recommendations?date=2026-11-23'):
    db = _mk_db(tmp_path)
    def _conn():
        c = sqlite3.connect(str(db), timeout=10)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(srv, '_db_conn', _conn)
    _authed(monkeypatch)
    h = _FakeHandler(path)
    return h, db


def test_get_recommendations_merges_talent_fields(tmp_path, monkeypatch):
    h, db = _wired(tmp_path, monkeypatch)
    srv.SoloBraveHandler._handle_get_recommendations(h)
    code, data = h.out
    assert code == 200 and data['total'] == 1
    it = data['items'][0]
    assert it['rec_id'] == 'rec_20261123_tal_sq_t1'
    assert it['talent_name'] == '赵总外贸家居'
    assert it['douyin_id'] == 'DW_ZHAO', '台账合并：抖音号带出'
    assert it['followers'] == 410000
    assert it['verdict_now'] == '值得联系', '台账当前判定带出'
    assert it['pipeline_status'] == 'recommended'
    assert it['run_id'] == 'ssq_test'
    assert '智能家居' in it['reason_text']
    assert it['matched_rules'] == [], '宁可空存：JSON 解析后回数组'


def test_get_recommendations_bad_date_400(tmp_path, monkeypatch):
    h, db = _wired(tmp_path, monkeypatch,
                   path='/api/recommendations?date=20261123')
    srv.SoloBraveHandler._handle_get_recommendations(h)
    assert h.out[0] == 400


def test_get_recommendations_card_failure_never_blocks(tmp_path, monkeypatch):
    h, db = _wired(tmp_path, monkeypatch)

    def _boom(*a, **kw):
        raise RuntimeError('outreach_card 炸了')

    def _conn():
        c = sqlite3.connect(str(db), timeout=10)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(srv, '_db_conn', _conn)
    import sys
    mod = types.ModuleType('daren_cockpit.tools.outreach_card')
    mod.build_outreach_cards = _boom
    monkeypatch.setitem(sys.modules, 'daren_cockpit.tools.outreach_card', mod)
    monkeypatch.setitem(sys.modules, 'daren_cockpit.tools',
                        types.ModuleType('daren_cockpit.tools'))
    sys.modules['daren_cockpit.tools'].__path__ = []
    srv.SoloBraveHandler._handle_get_recommendations(h)
    assert h.out[0] == 200 and h.out[1]['total'] == 1, '卡组装失败不阻断列表'


def test_get_recommendations_requires_auth(tmp_path, monkeypatch):
    db = _mk_db(tmp_path)
    def _conn():
        c = sqlite3.connect(str(db), timeout=10)
        c.row_factory = sqlite3.Row
        return c
    monkeypatch.setattr(srv, '_db_conn', _conn)
    a = srv.AuthResult(user_info=None, error='unauthorized', status=401)
    monkeypatch.setattr(srv, '_authenticate', lambda *args, **kw: a)
    h = _FakeHandler()
    srv.SoloBraveHandler._handle_get_recommendations(h)
    assert h.out[0] == 401


def test_status_flow_whitelist_and_audit(tmp_path, monkeypatch):
    h, db = _wired(tmp_path, monkeypatch,
                   path='/api/recommendations/rec_20261123_tal_sq_t1/status')
    h._body = {'status': 'contacted'}
    srv.SoloBraveHandler._handle_update_recommendation_status(
        h, 'rec_20261123_tal_sq_t1')
    code, data = h.out
    assert code == 200 and data['from'] == 'recommended' and data['to'] == 'contacted'
    conn = sqlite3.connect(str(db))
    st = conn.execute("SELECT pipeline_status FROM talent_recommendations "
                      "WHERE rec_id='rec_20261123_tal_sq_t1'").fetchone()[0]
    conn.close()
    assert st == 'contacted', '状态写回留痕'


def test_status_flow_rejects_unknown_and_missing(tmp_path, monkeypatch):
    h, db = _wired(tmp_path, monkeypatch)
    h._body = {'status': '随便什么'}
    srv.SoloBraveHandler._handle_update_recommendation_status(
        h, 'rec_20261123_tal_sq_t1')
    assert h.out[0] == 400, '非白名单拒'
    h._body = {'status': 'contacted'}
    srv.SoloBraveHandler._handle_update_recommendation_status(h, 'rec_不存在')
    assert h.out[0] == 404, '不存在拒'
    conn = sqlite3.connect(str(db))
    st = conn.execute("SELECT pipeline_status FROM talent_recommendations "
                      "WHERE rec_id='rec_20261123_tal_sq_t1'").fetchone()[0]
    conn.close()
    assert st == 'recommended', '两次拒绝都不许污染存量'
