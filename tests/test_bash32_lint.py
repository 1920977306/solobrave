# -*- coding: utf-8 -*-
"""★★ **bash 3.2 变量吞字符** lint 的回归守卫（2026-10-27 A 边 §81.12）

★ 为什么要有这个闸门（B 边 §80.14.1 第七项提出，A 边实现）：
  macOS 自带 bash 3.2 ⇒ `$VAR` 后面紧跟多字节字符时，**非 ASCII 首字节会被吞进变量名**
  ⇒ `$EXPECT）` 被当成变量 `EXPECT\\xef` ⇒ 未定义变量报错。
  ⇒ B 边把这条写进了交接单，**当轮自己又犯了一次** ⇒
  ⇒ **纪律写进文档 ≠ 行为自动改变** ⇒ 必须有**机械闸门**。

★ 这条测试的判据设计（沿用本项目铁律「全绿必须先证明能红」）：
  ① **必须能红**：坏样本 ⇒ `main()` 返回 1（不是「非 0」而是**具体的 1**，
     否则「脚本崩了」会被误当成「断言判红」）；
  ② **注释豁免必须成立**：本仓库现有 3 处命中**全是「讲这个坑的注释」**
     ⇒ 若闸门判它们红 ⇒ **假阳性 ⇒ 闸门变阻塞器 ⇒ 大家绕过它**
     ⇒ **回到「自查形同虚设」的原始失败模式**（同 `md_selfcheck.py` §70）；
  ③ **仓库自身必须干净**：真代码区零命中。
"""

import importlib.util
import io
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINT = os.path.join(REPO, "scripts", "bash32_lint.py")

_spec = importlib.util.spec_from_file_location("bash32_lint", LINT)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


# ── ① 坏样本必须被判红，且退出码必须是**具体的 1** ──────────────────
def test_bad_sample_is_flagged():
    """`$VAR` 紧跟中文 = 真 bug ⇒ 必须命中。"""
    hits = lint.scan_text('echo "（应等于 $MD5_BEFORE）"\n')
    assert sum(len(v) for v in hits.values()) == 1, "坏样本必须命中 1 处"


def test_gate_exit_code_is_exactly_one_on_bad_file():
    """★ 判据必须是 **rc == 1**（不是 rc != 0）—— 否则「脚本崩了」会被当成「断言判红」。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="bash32_gate_")
    try:
        bad = os.path.join(tmp, "bad.sh")
        with io.open(bad, "w", encoding="utf-8") as fh:
            fh.write('echo "（应等于 $MD5_BEFORE）"\n')
        assert lint.main([bad, "--quiet"]) == 1
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_gate_exit_code_is_zero_on_good_file():
    """正向必须 rc=0 ⇒ 证明闸门不是恒红。"""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="bash32_gate_")
    try:
        good = os.path.join(tmp, "good.sh")
        with io.open(good, "w", encoding="utf-8") as fh:
            fh.write('echo "（应等于 ${MD5_BEFORE}）"\n')
        assert lint.main([good, "--quiet"]) == 0
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


# ── ② 注释豁免必须成立（否则闸门变阻塞器 ⇒ 会被绕过） ───────────────
def test_comment_hits_are_exempted():
    """讲这个坑的注释**必须不判红** ⇒ 否则本仓库当场被自己的闸门卡住。"""
    for line in (
        "# 说明 $code（ 会被吞进变量名\n",
        'echo "ok"  # 注释 $code（\n',
    ):
        assert lint.scan_text(line) == {}, "注释必须豁免：%r" % line


def test_single_quotes_are_literal():
    """单引号内 bash 完全不展开 ⇒ 不能判红（否则是假阳性）。"""
    assert lint.scan_text("echo '$VAR中文'\n") == {}


def test_braces_and_special_params_are_safe():
    """`${VAR}` / `$1` / `$$` 都不是变量名 ⇒ 不能判红。"""
    for line in (
        'echo "（${MD5_BEFORE}）"\n',
        'echo "$1中文"\n',
        'echo "$$PID中文"\n',
    ):
        assert lint.scan_text(line) == {}, "不该判红：%r" % line


def test_expansion_contexts_are_flagged():
    """双引号内 / 裸区 / 未加引号 heredoc ⇒ 都会展开 ⇒ 必须判红。"""
    for line in (
        'echo "$VAR中文"\n',
        'echo $VAR中文\n',
        "cat <<EOF\n$VAR中文\nEOF\n",
    ):
        assert lint.scan_text(line), "该判红：%r" % line


def test_quoted_heredoc_is_literal():
    """`<<'EOF'` 不展开 ⇒ 不能判红。"""
    assert lint.scan_text("cat <<'EOF'\n$VAR中文\nEOF\n") == {}


def test_hash_inside_quotes_is_not_a_comment():
    """引号内的 `#` 不是注释 ⇒ 其后的 `$VAR中文` 仍属代码区 ⇒ 必须判红。"""
    hits = lint.scan_text('echo "# not comment $VAR中文"\n')
    assert hits, "引号内的 # 不能被当成注释起点"


# ── ③ 仓库自身必须干净（真代码区零命中） ──────────────────────────
def test_repo_shell_scripts_have_no_live_violations():
    """★ 真代码区零命中。"""
    files = sorted(lint.iter_shell_files(REPO))
    assert files, "必须至少枚举到一个 shell 脚本（否则这条断言是恒真的假绿）"
    total = 0
    detail = []
    for path in files:
        hits = lint.scan_file(path)
        if hits:
            total += len(hits)
            detail.append("%s: %s" % (os.path.relpath(path, REPO), hits))
    assert total == 0, "仓库真代码区有 %d 处命中：\n%s" % (total, "\n".join(detail))


def test_repo_comment_hits_are_exactly_three_known_documentation_lines():
    """★ 最严口径下，命中**必须恰好是那 3 处「讲这个坑的注释」**。

    ⇒ 这条断言的作用是：**如果将来有人新增了带坑的注释**，数字会变 ⇒ 红灯提醒复核；
    ⇒ 如果有人**在真代码里**写了 `$VAR中文`，默认口径那条会先红。
    """
    files = sorted(lint.iter_shell_files(REPO))
    strict = []
    for path in files:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        hits = lint.scan_text(text, include_comments=True)
        for idx, items in hits.items():
            strict.append((os.path.relpath(path, REPO), idx))
    assert len(strict) == 3, "最严口径命中数变了（原 3 处注释）：%s" % (strict,)
    for rel, idx in strict:
        assert rel in ("health_check.sh", "scripts/verify_github_pat.sh"), \
            "命中位置变了：%s L%d" % (rel, idx)
