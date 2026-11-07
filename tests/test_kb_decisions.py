# -*- coding: utf-8 -*-
"""
审核决策留痕 kb_decisions 测试 (2026-11-07)

老大拍板（192 次状态变更 0 条理由的教训）：
  状态变更 ≠ 学习样本——每次审核落一行带理由的案例给 few-shot 攒料。
  表结构补三列：content_snapshot（删除存全文，不可逆数据的唯一保底）、
  confidence（sure/unsure，unsure=边界案例采集位）、reason_note（other 的出口）。

覆盖:
  1) 建表幂等
  2) rejected 存删前全文；approved 只存前 500 字（原文还在）
  3) confidence/reason_note/merged_into 落库正确
  4) PUT /api/knowledge/entries/:id {status:'ok', review:{...}} 过审后落 approved
     （无 review 的老调用不落——兼容旧行为）
  5) DELETE /api/knowledge/entries/:id {review:{...}} 拒绝后落 rejected 全文快照
  6) 写库失败不冒泡（观测层纪律）
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

import knowledge_service as ks

LONG_CONTENT = '客单价区间八十到一百二十元。' * 60  # > 500 字


def _fresh_env():
    tmp = tempfile.mkdtemp(prefix='kb_decisions_')
    db_file = os.path.join(tmp, 'solobrave.db')
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.init_kb_entries_db()
    finally:
        ks.DB_PATH = prev
    return db_file


def _insert_entry(db_file, eid, content='测试内容', status='pending'):
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            'INSERT INTO kb_entries (id, title, content, status, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?)',
            (eid, '标题' + eid, content, status,
             int(time.time() * 1000), int(time.time() * 1000)))
        conn.commit()
    finally:
        conn.close()


def _decisions(db_file):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(
            'SELECT entry_id, decision, reason_code, confidence, reason_note, '
            'LENGTH(content_snapshot), merged_into, decided_by FROM kb_decisions '
            'ORDER BY created_at').fetchall()
    finally:
        conn.close()


def test_table_created_idempotent():
    db_file = _fresh_env()
    cols = [r[1] for r in sqlite3.connect(db_file).execute(
        'PRAGMA table_info(kb_decisions)').fetchall()]
    for c in ('entry_id', 'entry_snapshot', 'content_snapshot', 'decision',
              'reason_code', 'confidence', 'reason_note', 'merged_into', 'decided_by'):
        assert c in cols, f'kb_decisions 缺列 {c}'
    # 幂等：再跑一次不炸
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.init_kb_entries_db()
    finally:
        ks.DB_PATH = prev


def test_rejected_stores_full_content():
    """★ 不可逆数据的唯一保底：rejected 存删前全文（不是 500 字截断）。"""
    db_file = _fresh_env()
    _insert_entry(db_file, 'kb_full', content=LONG_CONTENT)
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.log_kb_decision('kb_full', 'rejected', reason_code='duplicate',
                           confidence='unsure', reason_note='跟 kb_a 撞了',
                           merged_into='', decided_by='user_admin',
                           entry_row={'title': 't', 'content': LONG_CONTENT,
                                      'category': 'c', 'scope': 'global'})
    finally:
        ks.DB_PATH = prev
    rows = _decisions(db_file)
    assert len(rows) == 1
    r = rows[0]
    assert r[0] == 'kb_full' and r[1] == 'rejected' and r[2] == 'duplicate'
    assert r[3] == 'unsure' and r[4] == '跟 kb_a 撞了'
    assert r[5] == len(LONG_CONTENT), 'rejected 必须存全文'


def test_approved_stores_head_only():
    """approved 原文还在库里，存前 500 字够复盘。"""
    db_file = _fresh_env()
    _insert_entry(db_file, 'kb_head', content=LONG_CONTENT, status='pending')
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.log_kb_decision('kb_head', 'approved', reason_code='verified',
                           confidence='sure', decided_by='user_admin',
                           entry_row={'title': 't', 'content': LONG_CONTENT})
    finally:
        ks.DB_PATH = prev
    rows = _decisions(db_file)
    assert len(rows) == 1
    assert rows[0][1] == 'approved'
    assert rows[0][5] == 500, 'approved 只存前 500 字'


def test_log_failure_does_not_raise():
    prev = ks.DB_PATH
    ks.DB_PATH = '/nonexistent_dir_xyz/kb.db'
    try:
        ks.log_kb_decision('kb_x', 'rejected', entry_row={'title': 't'})
    finally:
        ks.DB_PATH = prev


def test_reason_code_labels_aligned_with_reject_codes():
    """前端 chip 口径与服务端 REJECT_REASON_CODES 对齐（别两处各写一份）。"""
    js = (ROOT / 'js' / 'inline-07.js').read_text(encoding='utf-8')
    for code in ks.REJECT_REASON_CODES:
        assert f"'{code}'" in js, f'inline-07.js 缺理由码 {code}'
    assert 'verified' in js, '通过默认 reason_code=verified 前端应体现'


# ───────────────────── E2E：审核动作真的落 kb_decisions ─────────────────────

def _load_server_env():
    """importlib 加载 solobrave-server.py + 临时数据目录（对齐 test_reseller 模式）。"""
    import http.server
    import socket
    import threading
    import urllib.error
    import urllib.request

    spec = importlib.util.spec_from_file_location(
        'solobrave_server_kbdec', ROOT / 'solobrave-server.py')
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    tmp = tempfile.mkdtemp(prefix='kb_decisions_e2e_')
    srv.DATA_DIR = tmp
    srv.AGENTS_FILE = os.path.join(tmp, 'agents.json')
    srv.USERS_FILE = os.path.join(tmp, 'users.json')
    srv.PERMISSIONS_FILE = os.path.join(tmp, 'permissions.json')
    srv.GROUPS_FILE = os.path.join(tmp, 'groups.json')
    srv.TEAMS_FILE = os.path.join(tmp, 'teams.json')
    srv.SECRET_FILE = os.path.join(tmp, '.secret')
    srv.SETTINGS_FILE = os.path.join(tmp, 'settings.json')
    srv.CHATS_DIR = os.path.join(tmp, 'chats')
    srv.MEMORY_DIR = os.path.join(tmp, 'memory')
    srv.INFLUENCER_DIR = os.path.join(tmp, 'influencers')
    srv.DB_PATH = os.path.join(tmp, 'solobrave.db')
    srv._ensure_data_dir()
    srv.init_db()

    prev_ks_db = ks.DB_PATH
    ks.DB_PATH = srv.DB_PATH
    ks.init_kb_entries_db()

    with open(srv.AGENTS_FILE, 'w', encoding='utf-8') as f:
        json.dump([], f)
    with open(srv.USERS_FILE, 'w', encoding='utf-8') as f:
        json.dump([{'id': 'user_admin', 'username': 'admin', 'role': 'admin',
                    'pwdVersion': 0}], f)

    # 过审会触发向量化兜底——测试无 embedding key，替身为空操作
    # （探针盯的是「决策落库」，不是向量化本身）
    orig_ensure = ks.kb_entry_ensure_vectorized
    ks.kb_entry_ensure_vectorized = lambda *a, **k: None

    port = socket.socket()
    port.bind(('127.0.0.1', 0))
    port_num = port.getsockname()[1]
    port.close()
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', port_num), srv.SoloBraveHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    token = srv.generate_token('user_admin', 'admin', 0)

    class Env:
        pass
    env = Env()
    env.srv, env.ks, env.port, env.token = srv, ks, port_num, token
    env.db = srv.DB_PATH

    def _http(method, path, body=None, tok=None):
        url = f'http://127.0.0.1:{port_num}{path}'
        headers = {}
        if tok:
            headers['Authorization'] = 'Bearer ' + tok
        data = None
        if body is not None:
            data = json.dumps(body).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    env.http = _http

    def _cleanup():
        httpd.shutdown()
        httpd.server_close()
        ks.kb_entry_ensure_vectorized = orig_ensure
        ks.DB_PATH = prev_ks_db
    env.cleanup = _cleanup
    return env


def _insert(env, eid, status='pending', content='E2E 测试内容，长度足够。'):
    conn = sqlite3.connect(env.db)
    try:
        conn.execute(
            'INSERT INTO kb_entries (id, title, content, status, created_at, updated_at) '
            'VALUES (?,?,?,?,?,?)',
            (eid, '标题' + eid, content, status,
             int(time.time() * 1000), int(time.time() * 1000)))
        conn.commit()
    finally:
        conn.close()


def test_e2e_approve_with_review_logs_decision():
    env = _load_server_env()
    try:
        _insert(env, 'kb_e2e_ok')
        code = env.http('PUT', '/api/knowledge/entries/kb_e2e_ok', {
            'status': 'ok',
            'review': {'reason_code': 'verified', 'confidence': 'sure'},
        }, env.token)
        assert code == 200, f'过审应 200，实际 {code}'
        rows = _decisions(env.db)
        assert len(rows) == 1
        assert rows[0][0] == 'kb_e2e_ok' and rows[0][1] == 'approved'
        assert rows[0][2] == 'verified' and rows[0][3] == 'sure'
        assert rows[0][7] == 'user_admin', 'decided_by 必须是操作人'
    finally:
        env.cleanup()


def test_e2e_reject_with_review_logs_full_snapshot():
    env = _load_server_env()
    try:
        _insert(env, 'kb_e2e_no', content=LONG_CONTENT)
        code = env.http('DELETE', '/api/knowledge/entries/kb_e2e_no', {
            'review': {'reason_code': 'duplicate', 'confidence': 'unsure',
                       'reason_note': '跟老的撞了'},
        }, env.token)
        assert code == 200, f'拒绝应 200，实际 {code}'
        rows = _decisions(env.db)
        assert len(rows) == 1
        r = rows[0]
        assert r[0] == 'kb_e2e_no' and r[1] == 'rejected' and r[2] == 'duplicate'
        assert r[3] == 'unsure' and r[4] == '跟老的撞了'
        assert r[5] == len(LONG_CONTENT), '拒绝必须存删前全文快照'
    finally:
        env.cleanup()


def test_e2e_legacy_calls_without_review_leave_no_decision():
    """兼容：不带 review 的旧调用（详情页删除等）不留痕——非审核动作不强制。"""
    env = _load_server_env()
    try:
        _insert(env, 'kb_e2e_legacy')
        code = env.http('PUT', '/api/knowledge/entries/kb_e2e_legacy',
                        {'status': 'ok'}, env.token)
        assert code == 200
        assert env.http('DELETE', '/api/knowledge/entries/kb_e2e_legacy', None,
                        env.token) == 200
        assert _decisions(env.db) == [], '无 review 的旧调用不应产生决策记录'
    finally:
        env.cleanup()

