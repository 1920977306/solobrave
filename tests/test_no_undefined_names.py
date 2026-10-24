"""**未定义名静态扫描** —— 防 `NameError` 式运行时炸。

【事故背景（2026-10-24 实测）】
飞书卡片按钮「看我的排品表」→ `_card_view_plan()` 里用了
`datetime.datetime.now()`，但 `daren_bot/handler.py` **没有 import datetime** ⇒
点按钮必然 `NameError` ⇒ 回调返回「排品表算不出来」。
写的时候人眼完全看不出来（IDE 不报、py_compile 不报、pytest 也不报
—— 因为那条路径**从来没被测过**）。

⇒ 用 `symtable` 静态扫：**函数里引用了、但模块作用域没定义、也不是内置**的全局名
   ⇒ 直接判红。这样「按钮回调」「定时任务」这类冷路径也能被覆盖。

⚠️ 只报**真·全局未定义**（不是局部变量、不是闭包变量、不是内置）。
"""
import builtins
import os
import symtable

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROD_DIRS = ("daren_bot", "daren_cockpit")

_BUILTINS = set(dir(builtins))

# import 系统在模块作用域注入的 dunder（symtable 不列它们 ⇒ 会误报）
_MODULE_DUNDERS = {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__cached__", "__debug__",
}


def _unresolved_globals(src: str, filename: str):
    """返回 [(scope_name, name)] —— 引用了但模块作用域没有的全局名。"""
    top = symtable.symtable(src, filename, "exec")
    top_names = set(top.get_identifiers())

    bad = []

    def walk(table, scope_chain):
        for sym in table.get_symbols():
            # is_global(): 在本作用域里是「全局引用」；
            # is_referenced(): 被读到了（不是只赋值）。
            # 闭包变量（free）不在 is_global 里 ⇒ 不会误报。
            if not sym.is_global() or not sym.is_referenced():
                continue
            name = sym.get_name()
            if name in top_names or name in _BUILTINS or name in _MODULE_DUNDERS:
                continue
            bad.append((scope_chain, name))
        for child in table.get_children():
            walk(child, f"{scope_chain}.{child.get_name()}")

    walk(top, filename)
    return bad


@pytest.mark.parametrize("pkg", PROD_DIRS)
def test_no_undefined_global_names(pkg):
    """生产代码里不得有「引用了但没定义/没导入」的全局名。"""
    base = os.path.join(ROOT, pkg)
    assert os.path.isdir(base), f"找不到生产目录 {pkg}"
    offenders = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".venv", "venv", "node_modules")]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            src = open(path, encoding="utf-8").read()
            try:
                table = symtable.symtable(src, path, "exec")
            except SyntaxError as e:
                offenders.append(f"{os.path.relpath(path, ROOT)}: 语法错误 {e}")
                continue
            top_names = set(table.get_identifiers())
            for scope, name in _unresolved_globals(src, path):
                if name in top_names or name in _BUILTINS or name in _MODULE_DUNDERS:
                    continue
                offenders.append(
                    f"{os.path.relpath(path, ROOT)}: `{name}` 在 {scope} 里被引用，"
                    f"但模块作用域没定义（多半是漏 import）")
    assert not offenders, "未定义的全局名（运行到就会 NameError）：\n  " + "\n  ".join(
        sorted(set(offenders)))
