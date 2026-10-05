"""★ fix/test-dual-db: pytest 共用 conftest (relibility suite 双库改造).

作用:
1. session-scoped fixture 'solobrave_root': 返回项目根路径 (含 solobrave-server.py)
2. autouse fixture 'solobrave_sys_path': pytest collection 时自动把项目根加进 sys.path,
   让 tests/*.py 里 import solobrave_server / import memory_pipeline / import provider_adapters
   不需要每个 test 都 sys.path.insert(0, ROOT)
3. session-scoped fixture 'test_data_dir': 返回 tmp 测试数据目录 (含 solobrave.db + agents.json)
   — 取代 'data/solobrave.db' 相对路径, 测试零 prod 接触 (派单要求: prod 树跑全绿 / 干净 checkout
     无 data/ 也跑全绿 / 跑测试前后 prod data md5 不变)
4. autouse fixture 'prod_db_guard': 测试进程内禁止连接 prod data/solobrave.db 真实路径,
   sqlite3.connect mock 层 assert, 防止以后再漏 (派单要求回归闸门)

test_data_dir 优先级:
  - 有 SOLO_BRAVE_DB_SOURCE env → copy 该 DB 文件到 tmp, agents.json 拷自 prod (如果存在)
    推荐: 拷 prod 库快照 (schema + 数据一致, 跟回归口径一致)
  - 没有 → tmp 全新建库 (走 server srv.DB_PATH = tmp + srv.init_db()),
    agents.json 拷自 prod (如果存在, 让 ChatReliabilitySuite.setUpClass 不因缺 agents.json 报 error)

先例: test_analyzed_talents_decouple.py:101 srv.DB_PATH = os.path.join(self.tmpdir, 'solobrave.db')
+ srv._ensure_data_dir() + srv.init_db() — 照这个模式统一.

跑法:
  # 默认模式 (回归): 拷 prod 库到 tmp + 跑全套, 零 prod 写入
  cd /Users/qichen/sb-dev/backend-dual-db
  SOLO_BRAVE_DB_SOURCE=/Users/qichen/solobrave-prod/data/solobrave.db \\
    python3 -m pytest tests/ -v

  # 全新库模式: tmp 全新建库 (无 prod 数据), 验证 schema + 建库逻辑
  cd /Users/qichen/sb-dev/backend-dual-db
  python3 -m pytest tests/ -v

  # Live 模式 (人工验收, 打 prod 8080 + 真实 LLM 计费调用, 默认 skip):
  SOLO_BRAVE_LIVE=1 python3 -m pytest tests/test_reliability_full_suite.py -m live -v

Mac 端跑验证, Windows 无 Python.
"""
import os
import shutil
import sys
import tempfile

import pytest


# 测试数据 tmp 根目录 (所有 test 共享一个 session-scoped tmp)
_TEST_DATA_PARENT = tempfile.mkdtemp(prefix='solobrave_test_data_')


@pytest.fixture(scope='session')
def solobrave_root():
    """项目根路径 (含 solobrave-server.py / memory_pipeline.py / provider_adapters.py)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def solobrave_sys_path(solobrave_root):
    """autouse: pytest 每个 test 前把项目根加进 sys.path (idempotent, 已存在不重复加)."""
    if solobrave_root not in sys.path:
        sys.path.insert(0, solobrave_root)
    yield
    # 不主动 pop, 让 sys.path 保持 (后续 test 也可能用到)


@pytest.fixture(scope='session')
def test_data_dir(solobrave_root):
    """session-scoped 测试数据 tmp 目录 (含 solobrave.db + agents.json + 其他 data/ 文件).

    优先级 (派单要求):
      1. SOLO_BRAVE_DB_SOURCE env 指定 DB 路径 → copy 该 DB 到 tmp + 拷 agents.json
      2. 都没有 → tmp 全新建库 (走 srv.DB_PATH + srv.init_db()) + 拷 agents.json

    拷 agents.json: ChatReliabilitySuite.setUpClass (line 215 _load_non_archived_agents)
    需要 agents.json, 如果没拷, live 测试 default skip 后 setUpClass 仍跑,
    会因 FileNotFoundError error 整个 suite (跟现在一样).

    返回: tmp dir 路径 (含 solobrave.db / agents.json)
    """
    db_filename = 'solobrave.db'
    tmp_dir = os.path.join(_TEST_DATA_PARENT, 'data')
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_db = os.path.join(tmp_dir, db_filename)

    source_db = os.environ.get('SOLO_BRAVE_DB_SOURCE')
    if source_db and os.path.isfile(source_db):
        # 模式 1: 拷 prod 库快照 (schema + 数据一致)
        shutil.copy2(source_db, tmp_db)
        # 同时拷 agents.json (如果 prod 有)
        prod_agents = os.path.join(solobrave_root, 'data', 'agents.json')
        if os.path.isfile(prod_agents):
            shutil.copy2(prod_agents, os.path.join(tmp_dir, 'agents.json'))
        # 〔fix/test-dual-db commit 3+1〕拷 prod .secret (JWT 签名密钥)
        # 修前 bug: worktree 自己的 data/.secret 跟 prod 不一致 (worktree 创建时 server 启过,
        # 生成新 secret, 跟 prod 的 secret 不同). test 进程 importlib generate_token 用
        # worktree secret, subprocess server 用 tmp 自己生成的 secret → JWT 签名验证失败 401.
        # 修法: 拷 prod .secret 到 tmp, 让 test 进程 + subprocess server 共享同一 secret.
        # 注: 拷的是 prod 的 .secret 不是 worktree 的 (worktree 的可能跟 prod 早就不同步了)
        prod_data_dir = os.path.dirname(source_db)  # /Users/qichen/solobrave-prod/data
        prod_secret = os.path.join(prod_data_dir, '.secret')
        if os.path.isfile(prod_secret):
            shutil.copy2(prod_secret, os.path.join(tmp_dir, '.secret'))
    else:
        # 模式 2: tmp 全新建库 (走 server init_db 逻辑)
        # 复用 test_analyzed_talents_decouple.py:101 先例: srv.DB_PATH = tmp + srv.init_db()
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location('solobrave_server',
                                            os.path.join(solobrave_root, 'solobrave-server.py'))
        mod = _ilu.module_from_spec(spec)
        os.environ.setdefault('SOLOBRAVE_TEST_NO_SERVE', '1')
        spec.loader.exec_module(mod)
        # 覆盖 server 全局路径到 tmp
        mod.DATA_DIR = tmp_dir
        mod.DB_PATH = tmp_db
        mod.AGENTS_FILE = os.path.join(tmp_dir, 'agents.json')
        mod.USERS_FILE = os.path.join(tmp_dir, 'users.json')
        mod.PERMISSIONS_FILE = os.path.join(tmp_dir, 'permissions.json')
        mod.GROUPS_FILE = os.path.join(tmp_dir, 'groups.json')
        mod.TEAMS_FILE = os.path.join(tmp_dir, 'teams.json')
        mod.SECRET_FILE = os.path.join(tmp_dir, '.secret')
        mod.SETTINGS_FILE = os.path.join(tmp_dir, 'settings.json')
        mod.CHATS_DIR = os.path.join(tmp_dir, 'chats')
        mod.MEMORY_DIR = os.path.join(tmp_dir, 'memory')
        mod.INFLUENCER_DIR = os.path.join(tmp_dir, 'influencers')
        os.makedirs(mod.CHATS_DIR, exist_ok=True)
        os.makedirs(mod.MEMORY_DIR, exist_ok=True)
        os.makedirs(mod.INFLUENCER_DIR, exist_ok=True)
        mod._ensure_data_dir()
        mod.init_db()
        # 同时拷 agents.json (如果 prod 有), 让 ChatReliabilitySuite.setUpClass 不因缺 agents.json 报 error
        prod_agents = os.path.join(solobrave_root, 'data', 'agents.json')
        if os.path.isfile(prod_agents):
            shutil.copy2(prod_agents, mod.AGENTS_FILE)
        # 全新建库模式: 让 mod._ensure_data_dir() 已经创建 SECRET_FILE dir, 但 .secret 文件还没写
        # _get_secret() 会在 subprocess server 启动时再调用生成. 但 test 进程 importlib 加载时
        # _get_secret() 没被显式调用, generate_token 才调. 这里提前生成一个跟 server 一致的 secret:
        import uuid
        new_secret = uuid.uuid4().hex + uuid.uuid4().hex
        with open(os.path.join(tmp_dir, '.secret'), 'w') as f:
            f.write(new_secret)
        # 重置 mod.JWT_SECRET 缓存, 让 generate_token 读新 secret
        mod.JWT_SECRET = None

    # 设环境变量让 unittest 风格 test 也能拿到 (TestCase setUp 不能直接接 pytest fixture)
    os.environ['SOLO_BRAVE_TEST_DATA_DIR'] = tmp_dir
    os.environ['SOLO_BRAVE_TEST_DB_PATH'] = tmp_db

    return tmp_dir


@pytest.fixture(autouse=True)
def prod_db_guard(solobrave_root, test_data_dir, monkeypatch):
    """autouse 回归闸门 (派单要求): 测试进程内禁止连接 prod data/solobrave.db 真实路径.

    监控 sqlite3.connect 调用: 如果传入路径等于 <solobrave_root>/data/solobrave.db (绝对化),
    且不等于 test_data_dir 内的 solobrave.db, 抛 AssertionError 防止以后再漏.

    例外: 测试环境自己显式传 prod 路径做兼容性验证 (本套件不允许, 直接拒绝).
    """
    import sqlite3 as _sqlite3
    prod_db_abs = os.path.realpath(os.path.join(solobrave_root, 'data', 'solobrave.db'))
    tmp_db_abs = os.path.realpath(os.path.join(test_data_dir, 'solobrave.db'))

    _orig_connect = _sqlite3.connect

    def _guarded_connect(database, *args, **kwargs):
        # database 可以是路径或 ':memory:' 等特殊字符串
        if isinstance(database, str) and database != ':memory:':
            db_abs = os.path.realpath(database)
            if db_abs == prod_db_abs and db_abs != tmp_db_abs:
                raise AssertionError(
                    f'[prod_db_guard] 测试禁止连接 prod 库: {database}\n'
                    f'  → 改用 test_data_dir fixture: {test_data_dir}\n'
                    f'  → 派单要求: 测试零 prod 接触 (回归闸门)'
                )
        return _orig_connect(database, *args, **kwargs)

    monkeypatch.setattr(_sqlite3, 'connect', _guarded_connect)
    yield
    # monkeypatch 自动还原
