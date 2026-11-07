# -*- coding: utf-8 -*-
"""
召回留痕双表测试 (2026-11-07)

老大拍板：召回观测与 kb_operation_log 分开——
  kb_recall_log    一次查询一行（query/命中数/最高分/耗时）
  kb_recall_hits   一条命中一行（entry_id/分数/排名/used）
塞同一张表会丢掉「查了但 0 命中」，而那正是「答不出是不是检索的锅」的答案。

覆盖:
  1) init_kb_entries_db 建出两张新表（幂等，二次调用不炸）
  2) log_kb_recall 有命中：log 一行 + hits N 行，score/rank 对得上
  3) log_kb_recall 0 命中：log 一行 hit_count=0、top_score=NULL —— 最关键场景
  4) hits.used 落 NULL（召回时不知道用没用上，回填是后续的事）
  5) 服务端 _handle_post_rag_retrieve 挂了 log_kb_recall（源码契约）
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location('solobrave_server_recall', ROOT / 'solobrave-server.py')
srv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(srv)

import knowledge_service as ks


def _fresh_env():
    """临时库 + init_kb_entries_db，返回 db 路径。"""
    tmp = tempfile.mkdtemp(prefix='kb_recall_')
    db_file = os.path.join(tmp, 'solobrave.db')
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.init_kb_entries_db()
    finally:
        ks.DB_PATH = prev
    return db_file


def _rows(db_file, sql, args=()):
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def test_tables_created_idempotent():
    db_file = _fresh_env()
    cols_log = [r[1] for r in _rows(db_file, "PRAGMA table_info(kb_recall_log)")]
    cols_hits = [r[1] for r in _rows(db_file, "PRAGMA table_info(kb_recall_hits)")]
    assert 'query' in cols_log and 'hit_count' in cols_log and 'top_score' in cols_log
    assert 'query_id' in cols_hits and 'score' in cols_hits and 'rank' in cols_hits \
        and 'used' in cols_hits and 'entry_type' in cols_hits
    # 幂等：再跑一次不炸
    _fresh_env.__wrapped__ if hasattr(_fresh_env, '__wrapped__') else None
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.init_kb_entries_db()
    finally:
        ks.DB_PATH = prev


def test_log_with_hits():
    db_file = _fresh_env()
    docs = [
        {'id': 'kb_a', 'similarity': 0.91, 'type': 'kb'},
        {'id': 'kb_b', 'similarity': 0.83, 'type': 'kb'},
        {'id': 'pat_1', 'similarity': 0.77, 'type': 'pattern'},
    ]
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.log_kb_recall('客单价多少', 'user_x', '', docs, 123)
    finally:
        ks.DB_PATH = prev

    logs = _rows(db_file, 'SELECT query, hit_count, top_score, duration_ms FROM kb_recall_log')
    assert len(logs) == 1
    assert logs[0][0] == '客单价多少'
    assert logs[0][1] == 3
    assert abs(logs[0][2] - 0.91) < 1e-6
    assert logs[0][3] == 123

    hits = _rows(db_file, 'SELECT entry_id, entry_type, score, rank, used '
                          'FROM kb_recall_hits ORDER BY rank')
    assert [h[0] for h in hits] == ['kb_a', 'kb_b', 'pat_1']
    assert [h[1] for h in hits] == ['kb', 'kb', 'pattern']
    assert [h[3] for h in hits] == [1, 2, 3]
    assert all(h[4] is None for h in hits), 'used 必须落 NULL（召回时不知道用没用上）'


def test_log_zero_hits_kept():
    """★ 最关键场景：0 命中也必须留一行 —— 「答不出」的答案在这里。"""
    db_file = _fresh_env()
    prev = ks.DB_PATH
    ks.DB_PATH = db_file
    try:
        ks.log_kb_recall('完全不存在的问题', 'user_x', '', [], 45)
    finally:
        ks.DB_PATH = prev

    logs = _rows(db_file, 'SELECT hit_count, top_score FROM kb_recall_log')
    assert len(logs) == 1
    assert logs[0][0] == 0
    assert logs[0][1] is None, '0 命中时 top_score 应为 NULL，不许写 0'
    assert _rows(db_file, 'SELECT COUNT(*) FROM kb_recall_hits')[0][0] == 0


def test_log_failure_does_not_raise():
    """观测层绝不允许冒泡打死调用方：坏库路径下只记日志、不抛。"""
    prev = ks.DB_PATH
    ks.DB_PATH = '/nonexistent_dir_xyz/kb.db'
    try:
        ks.log_kb_recall('q', 'u', '', [{'id': 'x', 'similarity': 0.5}], 1)
    finally:
        ks.DB_PATH = prev


def test_server_wires_log_kb_recall():
    """源码契约：_handle_post_rag_retrieve 必须调 ks.log_kb_recall。"""
    src = (ROOT / 'solobrave-server.py').read_text(encoding='utf-8')
    marker = 'ks.log_kb_recall('
    idx = src.find(marker)
    assert idx > -1, '服务端没有挂 log_kb_recall'
    # 调用点必须落在 _handle_post_rag_retrieve 内（而不是别的函数里碰巧出现）
    handler_idx = src.find('def _handle_post_rag_retrieve')
    assert handler_idx > -1 and handler_idx < idx < src.find('def _handle_post_rag_build')
