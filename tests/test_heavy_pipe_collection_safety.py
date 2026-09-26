# -*- coding: utf-8 -*-
"""★ fix/pytest-heavy-pipe-collection: 验证 test_heavy_pipe.py 模块级 sys.exit / 重逻辑已收进 _init_ns.

老大原话 (2026-09-25):
  "修tests/test_heavy_pipe.py: 把L111附近模块级sys.exit(1)及模块级直接执行的heavy pipe
   流程收进if __name__=="__main__":守卫, 使import/pytest collection不再退出;
   原有独立运行(python3 tests/test_heavy_pipe.py)行为保持不变;
   确保该文件被pytest正常收集且其case通过, 不再需要--ignore.
   配相应测试, 全绿."

测试设计:
  1. test_heavy_pipe_test_module_import_no_exit
     验证: import tests.test_heavy_pipe 不再触发 SystemExit (之前会 sys.exit(1) 让 pytest 整个套退出).
  2. test_heavy_pipe_test_module_no_executed_namespace_at_import
     验证: 模块级没有 ns 全局变量 (heavy pipe 流程延迟到 _init_ns() 调, 不再 import 时就跑).

⚠️ 测试本身不依赖 real heavy pipe, 不调 _init_ns (避免引入 sqlite/文件读取副作用).

跑法 (Mac 端):
  cd .worktree-pytest-heavy-pipe && python -m pytest tests/heavy_pipe_collection_safety_test.py -v
期望: 2/2 PASS.
"""
import importlib
import sys


def test_heavy_pipe_test_module_import_no_exit():
    """★ fix/pytest-heavy-pipe-collection: 验证 import 不触发 SystemExit.

    修复前: 模块级 exc_class/task_class/mgr_class/reindex_fn = extract_*(text) +
            if not all([...]) + sys.exit(1) → import 时触发 SystemExit(1) → pytest 整个套退出.
    修复后: 模块级只剩 helper + unittest.TestCase, sys.exit 收进 _init_ns() 函数
            (改为 raise RuntimeError), import 安全.

    本测试验证 import 成功 + 不抛 SystemExit.
    """
    # 强制清缓存, 确保这次走完整 import 流程
    sys.modules.pop('tests.test_heavy_pipe', None)
    sys.modules.pop('test_heavy_pipe', None)

    # 尝试 import; 修复前会触发 SystemExit, 修复后正常返回模块
    try:
        hpt = importlib.import_module('tests.test_heavy_pipe')
    except SystemExit as e:
        # 修复失败: import 触发 sys.exit(1)
        raise AssertionError(
            f'Import 触发了 SystemExit({e.code}) — 模块级 sys.exit(1) 没收到 _init_ns() 里, '
            f'pytest collection 会因这个退出. 请检查 tests/test_heavy_pipe.py 模块级直接执行.'
        )
    except FileNotFoundError as e:
        # 修复失败: import 时 text = open(KS_PY) 抛 FileNotFoundError
        raise AssertionError(
            f'Import 抛 FileNotFoundError({e}) — knowledge_service.py 路径在 import 时就读了, '
            f'KS_PY 相对路径依赖 cwd. 请检查 tests/test_heavy_pipe.py 模块级文件读取.'
        )
    except Exception as e:
        raise AssertionError(
            f'Import 抛 {type(e).__name__}({e}) — 模块级不应该有这种执行代码. '
            f'请检查 tests/test_heavy_pipe.py 模块级代码.'
        )

    # 验证模块已加载
    assert 'tests.test_heavy_pipe' in sys.modules, 'import 后模块应在 sys.modules 中'

    # 验证模块关键元素存在
    assert hasattr(hpt, '_init_ns'), '模块应有 _init_ns() 函数'
    assert hasattr(hpt, 'wait_for_status'), '模块应有 wait_for_status() helper'
    assert hasattr(hpt, 'TestProgress'), '模块应有 TestProgress class (pytest 自动收集)'


def test_heavy_pipe_test_module_no_executed_namespace_at_import():
    """★ 验证 import 时 ns 没被初始化 (heavy pipe 流程延迟到 setUpClass 调 _init_ns).

    修复前: 模块级 ns = {...} + exec(combined, ns) + ns['TIMEOUT_HEAVY_PIPE_MS'] = 500
            在 import 时就跑 (耗时 + 副作用: sqlite3 connect + exec HeavyPipe 类).
    修复后: ns 只在 _init_ns() 函数内建, import 时模块级没 ns (重逻辑延迟).

    验证:
      - hasattr(hpt, 'ns') 为 False, 或 hpt.ns 是 None / 不是 dict
      - _init_ns() 函数体内有 ns (function-level scope, 不污染模块)
    """
    # 强制清缓存
    sys.modules.pop('tests.test_heavy_pipe', None)
    sys.modules.pop('test_heavy_pipe', None)
    sys.modules.pop('test_heavy_pipe', None)
    hpt = importlib.import_module('tests.test_heavy_pipe')

    # 模块级不应该有 ns 全局变量 (延迟到 _init_ns 函数内)
    assert not hasattr(hpt, 'ns') or hpt.__dict__.get('ns') is None or not isinstance(hpt.__dict__.get('ns'), dict), \
        '模块级 ns 不应该被初始化 (pytest collection 时不应触发 heavy pipe 流程)'

    # _init_ns() 函数应存在且是函数对象
    assert callable(getattr(hpt, '_init_ns', None)), '_init_ns() 必须是可调用函数'

    # 模块级不应该有 _db (重逻辑延迟, _db 只在 _init_ns 函数内建)
    assert not hasattr(hpt, '_db') or hpt.__dict__.get('_db') is None, \
        '模块级 _db 不应该被初始化 (延迟到 _init_ns 函数内)'