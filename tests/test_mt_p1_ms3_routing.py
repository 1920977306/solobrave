# -*- coding: utf-8 -*-
"""P1: memory_service_v3 DB 连接租户路由 —— 沙箱实测（与 P0-1 ks 同款根因）。

ms3._db_conn 旧逻辑直连模块级 DB_PATH（server 文件旁的 data/solobrave.db），
全绕开 M2 thread-local 路由。在线可观测路径 = delete_memory 的 DB 段：
DELETE FROM memory / embedding_cache 落在默认库 → 租户 B 库的 memory 表行删不掉。

红口径（铁律 12）:
  1. POST 记忆（写文件, 拿到真实 memory id）
  2. 直接 SQL 往 t_acme 库 memory 表插同 id 行（模拟 BrainScheduler 已同步状态 —
     _sync_memory_to_db 是 ms3._db_conn 的主力写方, 当前随调度器停用, 用 SQL 预置等价状态）
  3. DELETE API 删记忆 → ms3.delete_memory 文件删除成功后会走 DB 段
  4. 断言: t_acme 库 memory 表该 id 行 = 0
     修复前 = 1 🔴（DELETE 去了默认库, 租户库残留）; 修复后 = 0 🟢

跑法（沙箱需带 MT+mock 在 18220 跑）:
  MT_P0_LIVE=1 python3 -m pytest tests/test_mt_p1_ms3_routing.py -v
"""
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

import pytest

BASE = os.environ.get('MT_P0_BASE', 'http://localhost:18220')
DATA_DIR = os.environ.get('MT_P0_DATA_DIR', '/tmp/mt-sandbox/data')
TENANT_B = os.environ.get('MT_P0_TENANT_B', 't_acme')
TENANT_ADMIN = os.environ.get('MT_P0_TENANT_ADMIN', 'acme_admin')
TENANT_ADMIN_PWD = os.environ.get('MT_P0_TENANT_ADMIN_PWD', 'Test1234')

LIVE = os.environ.get('MT_P0_LIVE', '') == '1'
pytestmark = pytest.mark.skipif(not LIVE, reason='live 沙箱测试: 需 MT_P0_LIVE=1 + 沙箱在跑')

TENANT_B_DB = os.path.join(DATA_DIR, 'tenants', TENANT_B, 'solobrave.db')
SHARED_EMP = 'emp_p0b_shared'  # P0 测试创建的租户 B 共享员工, 复用省配额


def _post(base, path, body, token=None, timeout=30):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _delete(base, path, token=None, timeout=30):
    req = urllib.request.Request(base + path, method='DELETE')
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _login(base, username, password):
    d = _post(base, '/api/auth/login', {'username': username, 'password': password})
    assert 'token' in d, f'登录失败 {username}: {d}'
    return d['token']


def test_ms3_delete_routes_to_tenant_db():
    token_b = _login(BASE, TENANT_ADMIN, TENANT_ADMIN_PWD)
    run = uuid.uuid4().hex[:8]
    marker = f'P1MARKER{run}'
    value = f'{marker} ms3 路由测试记忆: 删除时 DB 段必须落租户库'

    # 1. POST 记忆（写 v3 文件, 返回真实 id）
    created = _post(BASE, f'/api/memory/{SHARED_EMP}', {'key': 'user', 'value': value}, token_b)
    mem_id = created.get('id')
    assert mem_id, f'记忆创建未返回 id: {created}'

    # 2. 确保 t_acme 库 memory 表有同 id 行（模拟 BrainScheduler 已同步状态）。
    #    修复后 ms3 写路径已路由租户库, POST 阶段可能已写入 → INSERT OR IGNORE 幂等
    conn = sqlite3.connect(TENANT_B_DB)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO memory (id, emp_id, value, pool, created_at, is_filler, is_duplicate, cleaned_at, status) "
            "VALUES (?, ?, ?, 'daily', ?, 0, 0, 0, 'active')",
            (mem_id, SHARED_EMP, value, int(time.time() * 1000)))
        conn.commit()
        pre = conn.execute('SELECT COUNT(*) FROM memory WHERE id=?', (mem_id,)).fetchone()[0]
        assert pre == 1, f'预置失败: t_acme 库无该记忆行 (id={mem_id})'
    finally:
        conn.close()

    try:
        # 3. DELETE API 删除（文件删除成功 → ms3.delete_memory 走 DB 段）
        _delete(BASE, f'/api/memory/{SHARED_EMP}/{mem_id}', token_b)

        # 4. 断言: 租户库 memory 表该 id 行必须已删
        conn = sqlite3.connect(TENANT_B_DB)
        try:
            leftover = conn.execute('SELECT COUNT(*) FROM memory WHERE id=?', (mem_id,)).fetchone()[0]
        finally:
            conn.close()
        assert leftover == 0, f'🔴 P1 泄漏: 删除记忆的 DB 段落到了默认库, t_acme 库残留 {leftover} 行 (id={mem_id})'
    finally:
        # 红运行时残留清理（不影响下次）
        conn = sqlite3.connect(TENANT_B_DB)
        try:
            conn.execute('DELETE FROM memory WHERE id=?', (mem_id,))
            conn.commit()
        finally:
            conn.close()
