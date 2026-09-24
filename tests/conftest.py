"""★ fix/historical-tests: pytest 共用 conftest.

作用:
1. session-scoped fixture 'solobrave_root': 返回项目根路径 (含 solobrave-server.py)
2. autouse fixture 'solobrave_sys_path': pytest collection 时自动把项目根加进 sys.path,
   让 tests/*.py 里 import solobrave_server / import memory_pipeline / import provider_adapters
   不需要每个 test 都 sys.path.insert(0, ROOT)

注意: 大部分 tests 用 importlib.util.spec_from_file_location 自己加载 server (不走 sys.path),
本 conftest 主要提供 'solobrave_root' fixture 给需要 ROOT 路径的 test 用 (让测试用 fixture 拿
路径, 不再依赖当前工作目录).

跑法:
  cd /path/to/solobrave
  python3 -m pytest tests/ -v

Mac 端跑验证, Windows 无 Python.
"""
import os
import sys

import pytest


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