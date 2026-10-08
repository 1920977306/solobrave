# -*- coding: utf-8 -*-
"""P0 多租户泄漏双租户沙箱实测（审计 docs/daemon-mt-audit.md #1 #2）。

性质: live 集成测试 —— 必须打真沙箱服务（线程 tid 路由是进程行为，单测盖不住）。
默认 skip，显式开启：
  MT_P0_LIVE=1 MT_P0_DATA_DIR=/tmp/mt-sandbox/data \
    python3 -m pytest tests/test_mt_p0_tenant_leak.py -v

前置（沙箱侧）:
  1. 沙箱服务带多租户 + 知识 mock 模式启动（P0-1 确定性靠 mock 文档携带记忆原文）:
       cd /tmp/mt-sandbox && SOLOBRAVE_MT=1 SOLOBRAVE_KNOWLEDGE_MOCK_MODE=1 \
         nohup python3 solobrave-server.py --data /tmp/mt-sandbox/data 18220 > server.log 2>&1 &
  2. t_acme 租户管理员 acme_admin 可登录（密码被重置过就用重置后的）
  3. P0-2 由测试自己拉第二个服务实例（HOME=隔离 fakehome，轨迹文件不碰真 ~/.openclaw）

断言口径（铁律 12 只丢不造）:
  P0-1: 租户 B 员工 auto 记忆归纳出的知识 → t_acme 库有、默认库 0
        修复前 = 默认库出现含 marker 的新行 → 红
  P0-2: 租户 B 员工 trajectory 回放 → token_usage 落 t_acme 库、默认库 0 行
        修复前 = 默认库出现该 agent 的行 → 红
"""
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
import urllib.request
import uuid

import pytest

BASE = os.environ.get('MT_P0_BASE', 'http://localhost:18220')
DATA_DIR = os.environ.get('MT_P0_DATA_DIR', '/tmp/mt-sandbox/data')
TENANT_B = os.environ.get('MT_P0_TENANT_B', 't_acme')
TENANT_ADMIN = os.environ.get('MT_P0_TENANT_ADMIN', 'acme_admin')
TENANT_ADMIN_PWD = os.environ.get('MT_P0_TENANT_ADMIN_PWD', 'Test1234')
DEFAULT_ADMIN = os.environ.get('MT_P0_DEFAULT_ADMIN', 'admin')
DEFAULT_ADMIN_PWD = os.environ.get('MT_P0_DEFAULT_ADMIN_PWD', 'admin123')
PORT2 = int(os.environ.get('MT_P0_PORT2', '18221'))

LIVE = os.environ.get('MT_P0_LIVE', '') == '1'
pytestmark = pytest.mark.skipif(not LIVE, reason='live 双租户沙箱测试: 需 MT_P0_LIVE=1 + 沙箱在跑')

DEFAULT_DB = os.path.join(DATA_DIR, 'solobrave.db')
TENANT_B_DB = os.path.join(DATA_DIR, 'tenants', TENANT_B, 'solobrave.db')
SERVER_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'solobrave-server.py')


def _post(base, path, body, token=None, timeout=30):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get(base, path, token=None, timeout=60):
    req = urllib.request.Request(base + path)
    if token:
        req.add_header('Authorization', 'Bearer ' + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _login(base, username, password):
    d = _post(base, '/api/auth/login', {'username': username, 'password': password})
    assert 'token' in d, f'登录失败 {username}: {d}'
    return d['token']


def _db_count(db_path, sql, args=()):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def _wait_for(cond, desc, timeout_s=60, interval=2):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(interval)
    return False


@pytest.fixture(scope='module')
def tokens():
    return {
        'b': _login(BASE, TENANT_ADMIN, TENANT_ADMIN_PWD),
        'default': _login(BASE, DEFAULT_ADMIN, DEFAULT_ADMIN_PWD),
    }


@pytest.fixture(scope='module')
def emp_b(tokens):
    """模块级共享租户 B 测试员工。

    建员工有每用户硬上限 3 个，且 DELETE 走软删仍会留档 —— 每次新建几次就跑满配额永久 403。
    策略: 优先复用既有 'emp_p0b_shared'，没有再建；跨 run 共享，永不删除（沙箱专用）。
    """
    shared_id = 'emp_p0b_shared'
    # 直接读沙箱 agents.json 找既有共享员工（tenant_admin 对 GET /api/agents 无权限，走磁盘）
    with open(os.path.join(DATA_DIR, 'agents.json'), encoding='utf-8') as f:
        _all = json.load(f)
    existing = {a.get('id') for a in _all
                if a.get('tenant_id') == TENANT_B
                and a.get('status') != 'archived' and not a.get('archived')}
    if shared_id in existing:
        return shared_id
    try:
        _post(BASE, '/api/agents', {'id': shared_id, 'name': 'P0共享测试员工'}, tokens['b'])
    except urllib.error.HTTPError as e:
        if e.code == 403:
            # 配额已满（历史 run 残留）→ 退而求其次复用本租户任一既有员工
            fallback = sorted(existing)[0] if existing else None
            assert fallback, '租户 B 无可用员工且配额满，无法测试'
            return fallback
        raise
    return shared_id


def test_p0_1_memory_induction_routes_to_tenant_db(tokens, emp_b):
    """P0-1（审计 #1）: 租户 B 员工记忆归纳的知识必须落租户 B 库，默认库 0 新增。"""
    run = uuid.uuid4().hex[:8]
    marker = f'P0MARKER{run}'
    emp_id = emp_b

    # 1. 租户 B 管理员建员工
    _post(BASE, '/api/agents', {'id': emp_id, 'name': f'P0测试员工{run}'}, tokens['b'])

    # 2. 种 4 条带 marker 的 auto 记忆（阈值 knowledge_induction_min=3）
    #    注意: 内容必须真实不同 —— ms3 add_memory 有相似去重，仅换序号的 4 条会被折叠成 1 条
    seed_values = [
        f'{marker} ACME 客户只接受凉鞋类目，佣金区间 18-22%',
        f'{marker} ACME 客户直播档期只有工作日晚 8 点后，周末全天可排',
        f'{marker} ACME 客户退货率红线 10%，超线需商务介入谈判',
        f'{marker} ACME 客户要求短视频种草在发布前 48 小时给审片',
    ]
    for value in seed_values:
        _post(BASE, f'/api/memory/{emp_id}', {'key': 'auto', 'value': value}, tokens['b'])

    # 3. 轮询两个库的 knowledge 表增量（mock 文档携带记忆原文 → marker 必命中）
    #    注: 知识真表是 legacy `knowledge`（init_db 注释说废弃但实际仍是活表）;
    #    t_acme 等新租户库可能还没这张表（schema 漂移，本测试不预设）→ 查询容错为 0
    like = f'%{marker}%'

    def _kb_count(db_path):
        try:
            return _db_count(db_path,
                             'SELECT COUNT(*) FROM knowledge WHERE content LIKE ?', (like,))
        except sqlite3.OperationalError:
            return 0  # 表不存在 = 0 行

    # 先等「写入确实发生」（任一库出现 marker），排除 mock/阈值未触发的不可用结论
    happened = _wait_for(lambda: _kb_count(DEFAULT_DB) + _kb_count(TENANT_B_DB) > 0,
                         '任一库出现 marker（归纳发生）', timeout_s=90)
    assert happened, '90s 内两库都无 marker —— 归纳未发生，结论不可用（查 mock 模式/阈值）'
    assert _kb_count(DEFAULT_DB) == 0, '🔴 P0-1 泄漏: 租户 B 记忆归纳的知识写进了默认租户库'
    assert _kb_count(TENANT_B_DB) >= 1, '🟢→🔴 归纳发生了但两个库都找不到 —— 数据写丢了？'


def test_p0_2_credit_sync_routes_to_tenant_db(tokens, emp_b, tmp_path):
    """P0-2（审计 #2）: 租户 B 员工 trajectory 回放扣账必须落租户 B 库，默认库 0 行。"""
    run = uuid.uuid4().hex[:8]
    emp_id = emp_b
    session_key = f'agent:{emp_id}:chat-p0-{run}'

    # 1. 隔离 fakehome + 伪造 trajectory（不碰真 ~/.openclaw）
    fakehome = tmp_path / 'fakehome'
    traj_dir = fakehome / '.openclaw' / 'agents' / emp_id / 'sessions'
    traj_dir.mkdir(parents=True)
    now_ms = int(time.time() * 1000)
    events = [
        {'type': 'model.completed', 'ts': now_ms - 2000, 'sessionKey': session_key,
         'modelId': 'p0-test-model', 'data': {'usage': {'input': 500, 'output': 300, 'cacheRead': 0}}},
        {'type': 'model.completed', 'ts': now_ms, 'sessionKey': session_key,
         'modelId': 'p0-test-model', 'data': {'usage': {'input': 700, 'output': 400, 'cacheRead': 100}}},
    ]
    with open(traj_dir / 'p0test.trajectory.jsonl', 'w', encoding='utf-8') as f:
        for ev in events:
            f.write(json.dumps(ev) + '\n')

    # 2. 第二个服务实例（HOME=fakehome 才能扫到伪造轨迹；同数据目录）
    #    先清端口残留进程 —— 之前实例若成僵尸，_ping 会打到旧实例上（其 HOME 指向别处，
    #    轨迹永远扫不到，测试假阴性）
    _kill_port_listener(PORT2)
    env = dict(os.environ, HOME=str(fakehome), SOLOBRAVE_MT='1')
    proc = subprocess.Popen(
        ['python3', SERVER_PY, '--data', DATA_DIR, str(PORT2)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    try:
        base2 = f'http://localhost:{PORT2}'
        assert _wait_for(lambda: _ping(base2), '第二实例就绪', timeout_s=45, interval=1), \
            'HOME=fakehome 第二实例 45s 未就绪'

        # 3. 第二实例启动时会自动跑一次 CreditSyncLoop（启动即同步，无需手动端点）；
        #    轮询两个库等同步落库（修复前落默认库=红，修复后落 t_acme=绿）
        def _tu_count(db_path):
            try:
                return _db_count(db_path,
                                 'SELECT COUNT(*) FROM token_usage WHERE session_key = ?', (session_key,))
            except sqlite3.OperationalError:
                return 0

        synced = _wait_for(lambda: _tu_count(DEFAULT_DB) + _tu_count(TENANT_B_DB) >= 2,
                           '轨迹同步落库', timeout_s=60)
        assert synced, '60s 内两库 token_usage 都无本测试会话 —— 同步未发生，结论不可用'
        assert _tu_count(DEFAULT_DB) == 0, '🔴 P0-2 泄漏: 租户 B 员工 token 记录写进默认租户库'
        assert _tu_count(TENANT_B_DB) >= 2, '🟢→🔴 同步发生了但 t_acme 库找不到 —— 数据写丢了？'
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def _kill_port_listener(port):
    """清掉占用端口的残留进程（best effort，无视结果）。"""
    try:
        out = subprocess.run(['lsof', '-ti', f':{port}'], capture_output=True, text=True)
        for pid in out.stdout.split():
            try:
                os.kill(int(pid), signal.SIGKILL)
            except Exception:
                pass
        if out.stdout.strip():
            time.sleep(1)
    except Exception:
        pass


def _ping(base):
    """进程活着就算就绪：任何 HTTP 响应（含 4xx）都说明服务在监听。"""
    try:
        urllib.request.urlopen(base + '/', timeout=2)
        return True
    except urllib.error.HTTPError:
        return True  # 有响应 = 端口在监听
    except Exception:
        return False
