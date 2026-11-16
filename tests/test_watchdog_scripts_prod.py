# -*- coding: utf-8 -*-
"""P2-② 守卫：看门脚本入 prod 的形态锁（scripts/ 就位 + 路径不外泄到工作区 + ③ 真信号）。

背景：watch_credits / check_925 从 A 边工作区迁入 prod scripts/ 时踩过两个坑——
① MARKER_DIR 指向旧任务工作区（prod 留痕写到别人地盘）；② preflight_925 的 import os
在 main 中部，遮蔽顶部引用直接 NameError/UnboundLocalError；
③ 「熔断已部署」曾用「近24h git 有无 fuse 提交」做代理信号——上线次日即假红，
真信号必须是 launchd run-watchdog 在岗。
"""
import py_compile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = ROOT / 'scripts'


def test_scripts_exist_and_compile():
    for name in ('watch_credits.py', 'preflight_925.py'):
        p = SCRIPTS / name
        assert p.exists(), f'{name} 未入 prod scripts/'
        py_compile.compile(str(p), doraise=True)


def test_marker_dir_inside_prod():
    src = (SCRIPTS / 'watch_credits.py').read_text(encoding='utf-8')
    assert 'Documents/kimi/tasks' not in src, 'MARKER_DIR 还指向任务工作区'
    assert 'solobrave-prod/data/watchdog-markers' in src


def test_check925_no_gitlog_proxy():
    src = (SCRIPTS / 'preflight_925.py').read_text(encoding='utf-8')
    assert '--since=24.hours.ago' not in src, '③ 还在用 git-log 代理信号（上线次日假红）'
    assert 'launchctl' in src and 'run-watchdog' in src, '③ 缺 run-watchdog 真信号'


def test_import_os_top_level():
    """同名遮蔽锁：os 必须在文件顶部 import（main 中部 import 会遮蔽全函数作用域）。"""
    lines = (SCRIPTS / 'preflight_925.py').read_text(encoding='utf-8').splitlines()
    top_imports = [l for l in lines[:20] if l.startswith('import ') or l.startswith('from ')]
    assert any('import os' in l for l in top_imports), 'import os 不在顶部'
    assert not any(l.strip() == 'import os' for l in lines[20:]), 'main 中部还有局部 import os（遮蔽坑）'
