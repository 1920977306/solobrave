"""**同名定义守卫** —— 防 Python「后定义覆盖先定义」把线上功能打哑炮。

【事故背景（2026-10-24，实测踩到并已修）】
`daren_bot/executors.py` 里曾同时存在两个 `_product_verdict`：

    def _product_verdict(tenant_key, stats) -> str:   # LLM 出结论（933/1252 行调用）
    def _product_verdict(report) -> (label, tpl):     # 卡片取结论（后定义）

Python 的规则是「同一个模块里，后 def 覆盖先 def」⇒ **卡片那个把 LLM 那个整个遮蔽了**，
于是 `execute_product_collect`（商品链接/商品ID 分析）和 `execute_image_vision`
（截图识别）里的两参调用直接 `TypeError`：

    TypeError: _product_verdict() takes 1 positional argument but 2 were given

⇒ **达人发任何没缓存过的商品 ID、或发一张截图，商品分析 100% 失败。**

为什么一直没被发现：
① 缓存命中的路径走早返回，压根碰不到被遮蔽的函数；
② 现有单测用 `monkeypatch.setattr(ex, "_product_verdict", lambda *a, **kw: ...)`
   —— **把自己的被测对象整个替掉了**，替身接受任意参数，
   于是「遮蔽」这件事在测试里被完美掩盖（探针被替身骗过）。

★ 教训：**改共享模块后必须跑一遍它的所有入口**，不能只跑自己这次改的那条；
   而「探针必须能失败」的前提是**探针不能被替身顶掉**。

⇒ 本文件把这个坑变成一条会红的闸门：
   ① 同一模块内不得出现同名函数/类定义两次（模块级作用域）；
   ② 卡片取结论与档案取结论必须**同一口径**（单一真源）。
"""
import ast
import inspect
import os

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 生产代码目录（测试目录允许同名 —— 不同文件之间不会互相遮蔽）
PROD_DIRS = ("daren_bot", "daren_cockpit")


def _module_scope_defs(tree: ast.AST) -> list:
    """收集**模块级作用域**定义的名字 + 行号（不进函数/类内部）。

    模块级的 `if / try / with / for / while` 里的 def 也算模块级
    （它们照样会参与「后定义覆盖先定义」）。
    """
    out = []

    def visit(body):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append((node.name, node.lineno))
            elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
                visit(node.body)
                visit(getattr(node, "orelse", []) or [])
                visit(getattr(node, "finalbody", []) or [])
                for h in getattr(node, "handlers", []) or []:
                    visit(h.body)

    visit(tree.body)
    return out


@pytest.mark.parametrize("pkg", PROD_DIRS)
def test_no_duplicate_top_level_defs(pkg):
    """同一模块内不得有同名函数/类定义两次。"""
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
            try:
                tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
            except SyntaxError as e:
                offenders.append(f"{path}: 语法错误 {e}")
                continue
            seen = {}
            for name, lineno in _module_scope_defs(tree):
                seen.setdefault(name, []).append(lineno)
            for name, lines in seen.items():
                if len(lines) > 1:
                    rel = os.path.relpath(path, ROOT)
                    offenders.append(
                        f"{rel}: `{name}` 定义了 {len(lines)} 次（行 {lines}）"
                        f" —— 后定义会静默覆盖先定义")
    assert not offenders, "同名定义（会静默覆盖）：\n  " + "\n  ".join(offenders)


# ---------------- 结论解析：卡片与档案必须是同一口径 ----------------

# 真报告样本：2026-10-24 线上 daren_boss / 商品 3840201369122373702 的真实结论。
# ★ 理由句里出现「能带标准」「别带门槛」两个词 —— naive 全文档扫描会判成「能带」。
REAL_REPORT = """## 结论
🟡 慎带
鞋靴类目相关，但价格偏高且佣金率仅5%

## 匹配分析
- 价格匹配：商品价格为¥459，高于该达人主要消费区间¥100-200（26.4%）。
- 佣金评估：佣金率5%，未达到8%的能带标准，且低于5%的“别带”门槛；佣金金额为¥22.95。
- 类目匹配：商品属于鞋靴-女鞋-靴子，达人带货类目以服饰内衣为主，占比92.56%。

## 行动建议
- 突出骑士靴造型、长筒显瘦和秋季穿搭效果。
"""


def test_card_verdict_matches_archive_verdict():
    """卡片上的结论必须和结论档案（`_verdict_label`）**完全一致**。

    ★ 这条断言就是「同一份事实解析只有一个真源」的可执行形态 ——
      卡片自己扫一遍词 ⇒ 结论可能和档案不同 ⇒ 达人看到的结论是反的。
    """
    from daren_bot import executors as ex

    card_label, tpl = ex._card_verdict(REAL_REPORT)
    archive_label = ex._verdict_label(REAL_REPORT)

    assert archive_label == "慎带", f"档案口径本身变了：{archive_label}"
    assert card_label == "🟡 慎带", f"卡片结论与档案不一致：{card_label} vs {archive_label}"
    assert tpl == "orange", f"慎带必须是橙色，拿到 {tpl}"


def test_card_summary_is_not_empty():
    """卡片摘要必须拿到真实理由句（不能恒空）。

    ★ 旧实现按 `"▎结论"` 切分，但真实报告标题是 `## 结论`
      ⇒ `▎结论` 全仓只出现在它自己的 docstring 里 ⇒ 摘要恒为空。
    """
    from daren_bot import executors as ex

    summary = ex._card_summary(REAL_REPORT)
    assert summary, "卡片摘要不能为空"
    assert "价格偏高" in summary, f"摘要取错了行：{summary!r}"


def test_verdict_label_rejects_lookalike_words():
    """「不能带」不能被判成「能带」；认不出来的就留空（宁可不判，不误判）。"""
    from daren_bot import executors as ex

    assert ex._verdict_label("## 结论\n🔴 不能带\n理由") == "别带"
    assert ex._verdict_label("## 结论\n🟡 慎带\n理由里有个能带标准") == "慎带"
    assert ex._verdict_label("## 结论\n（AI 分析未出结果，暂不给结论）") == ""


def test_llm_verdict_function_has_two_params():
    """`execute_product_collect` / `execute_image_vision` 调的是**两参**函数。

    ★ 遮蔽事故的直接后果就是这个签名对不上。
      名字一旦又被人改成同名两份，这里就会红。
    """
    from daren_bot import executors as ex

    fn = ex._llm_product_verdict
    params = list(inspect.signature(fn).parameters)
    assert params == ["tenant_key", "stats"], f"LLM 判定函数签名变了：{params}"


def test_verdict_call_sites_use_the_llm_function():
    """两条入口的源码里必须出现 `_llm_product_verdict(`。

    ★ 不能只看模块属性 —— 遮蔽时模块属性存在，但**调用点**可能还指向别的名字。
      这里从源码层面把「调用点 ↔ 定义」钉死。
    """
    from daren_bot import executors as ex

    for fn_name in ("execute_product_collect", "execute_image_vision"):
        src = inspect.getsource(getattr(ex, fn_name))
        assert "_llm_product_verdict(" in src, f"{fn_name} 没在调 `_llm_product_verdict`"
        assert "_card_verdict(" not in src, f"{fn_name} 调错了函数（卡片取结论的那支）"
