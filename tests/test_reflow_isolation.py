"""守卫 (2026-10-29): daren_bot.reflow 的 HTTP 推送在测试期必须打不到生产。

事故回放: 全量回归跑 execute_qa 时 reflow.push_qa_exchange 真调 localhost:8080
⇒ ~28 条测试租户(tk_*)问答被灌进生产 kb_entries。prod_db_guard 只挡 sqlite
直读, 挡不住 HTTP 侧路 ⇒ 本文件锁三件事:

1. `_solo_base()` 运行期读 DAREN_SOLO_BASE env (不许是 import 期常量)
2. conftest autouse fixture 把 env 指向死端口 (每个测试生效)
3. ★ AST 闸门: `_post()` 里拼 URL 必须经 `_solo_base()` ——
   改回模块常量 ⇒ 本测试红 (变异对照对象)
"""
import ast
import os

import daren_bot.reflow as reflow


def test_solo_base_reads_env(monkeypatch):
    """运行期读 env: 改了 env 立刻反映 (import 期常量做不到这一点)。"""
    monkeypatch.setenv('DAREN_SOLO_BASE', 'http://127.0.0.1:59999')
    assert reflow._solo_base() == 'http://127.0.0.1:59999'
    monkeypatch.setenv('DAREN_SOLO_BASE', 'http://127.0.0.1:1234')
    assert reflow._solo_base() == 'http://127.0.0.1:1234'


def test_solo_base_default_is_prod_localhost():
    """不设 env 时默认 localhost:8080 (部署行为与改前一致)。"""
    saved = os.environ.pop('DAREN_SOLO_BASE', None)
    try:
        assert reflow._solo_base() == reflow.SOLO_BASE_DEFAULT == 'http://127.0.0.1:8080'
    finally:
        if saved is not None:
            os.environ['DAREN_SOLO_BASE'] = saved


def test_conftest_dead_port_fixture_active():
    """conftest autouse fixture 已生效: 本测试进程内 env 指向死端口。"""
    assert os.environ.get('DAREN_SOLO_BASE') == 'http://127.0.0.1:1'
    assert reflow._solo_base() == 'http://127.0.0.1:1'


def test_post_uses_runtime_solo_base():
    """★ AST 闸门: `_post()` 拼 URL 必须经 `_solo_base()` 调用, 不许用模块常量。

    变异对照: 把 `_solo_base() + path` 改回 `SOLO_BASE + path` ⇒ 本测试红。
    """
    src = open(reflow.__file__, encoding='utf-8').read()
    tree = ast.parse(src)
    post_fn = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_post'),
        None,
    )
    assert post_fn is not None, '_post 函数必须存在'
    calls = [n for n in ast.walk(post_fn) if isinstance(n, ast.Call)]
    names = {getattr(c.func, 'id', None) for c in calls}
    assert '_solo_base' in names, (
        "_post() 必须用 _solo_base() 拼 URL —— 用模块常量会让测试隔离"
        " fixture (DAREN_SOLO_BASE) 失效, 回归又会污染生产知识库"
    )
