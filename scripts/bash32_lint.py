#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bash 3.2 变量吞字符 lint —— 把「纪律」变成「闸门」。

★ 为什么要有这个（2026-10-27，B 边 §80.14.1 第七项提出，A 边实现）:
  macOS 自带 bash 3.2（PATH 里只有它）⇒ 解析 `$VAR` 时，会把**紧随其后的多字节字符的
  首字节当成变量名的一部分** ⇒ `$EXPECT）` 被当成变量 `EXPECT\\xef` ⇒ 未定义变量报错。
  ⇒ B 边 §80.14 把这条纪律写进了交接单，**当轮自己又犯了一次** ⇒
  ⇒ **纪律写进文档 ≠ 行为自动改变** ⇒ 必须配一道**机械闸门**。

★ 本脚本的判据边界（为什么不是「朴素正则扫全文」）:
  朴素正则 `\\$[A-Za-z_][A-Za-z0-9_]*[^\\x00-\\x7f]` 会**命中注释本身** ——
  本仓库现有 3 处命中**全是「讲这个坑的注释」** ⇒ ⇒ **假阳性** ⇒
  ⇒ 假阳性会让闸门变成阻塞器 ⇒ 大家绕过它 ⇒ **回到「自查形同虚设」的原始失败模式**
  （同 `scripts/md_selfcheck.py` §70 的教训：假阳性比假阴性更致命，因为它会被绕过）⇒
  ⇒ ⇒ **注释必须豁免** ⇒ 本脚本只判**会被 shell 展开的代码区**。

判定范围（只判这些）:
  ✅ 裸命令区 / 双引号内 / 反引号内 的 `$NAME` 紧跟非 ASCII
  ✅ 未加引号的 heredoc 正文（会展开）
  ❌ 注释（行首 `#`，或引号外、前面是空白的 `#`）—— 讲这个坑的注释必然命中
  ❌ 单引号字面量 —— bash 里完全字面，不展开
  ❌ `${NAME}` —— 花括号已终结变量名，安全
  ❌ `$1` / `$?` / `$@` / `$$` —— 位置/特殊参数，不是变量名
  ❌ 加引号的 heredoc（`<<'EOF'`）—— 不展开

用法:
    python3 scripts/bash32_lint.py                     # 扫全仓 shell 脚本
    python3 scripts/bash32_lint.py <file> [<file>...]  # 扫指定文件
    python3 scripts/bash32_lint.py --include-comments   # 连注释一起判（最严口径）
    python3 scripts/bash32_lint.py --selftest          # 闸门自检（负向对照必须红）

退出码:
    0 = 通过
    1 = 发现问题（**这就是闸门效果**）
    2 = 用法错误（文件不存在等）

★ 自检的判定标准（沿用本项目铁律「全绿必须先证明能红」）:
  负向用例必须**命中**、正向用例必须**不命中**；
  且 `--selftest` 内部还会跑一遍**端到端退出码**对照
  （坏文件 ⇒ main() 返回 1，好文件 ⇒ 返回 0）⇒ **证明它真的是闸门，不是一段打印**。
"""

import io
import os
import re
import shutil
import sys
import tempfile

SHELL_SUFFIXES = (".sh", ".bash", ".zsh")

# 依赖目录 / 第三方目录 / 超大目录一律跳过
# ★ `data/`（24.7 万文件 / 8.4G）与 `tmp_verify/`（1.3G）是本仓库的体积坑
#   ⇒ git 枚举路径天然绕开；os.walk 兜底路径必须显式排除，否则闸门会被拖死
SKIP_DIRS = {
    ".git", ".venv", ".venv-test", ".venv-daren", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache", "site-packages",
    ".idea", ".vscode",
    "data", "tmp_verify", "logs", "avatars", "minutes",
}

_NAME_START = re.compile(r"[A-Za-z_]")
_NAME_CHAR = re.compile(r"[A-Za-z0-9_]")
# heredoc 起始：<<WORD（会展开）/ <<'WORD'（不展开）/ <<-WORD
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
# 无扩展名但带 bash/zsh shebang 的文件也扫
_SHEBANG = re.compile(rb"^#!.*\b(bash|zsh)\b")


def _is_non_ascii(ch):
    return ord(ch) > 127


def scan_line(line, include_comments=False):
    """扫描一行。

    返回 (hits, code_end)：
      hits     = [(列号0基, 变量名, 紧跟的非 ASCII 字符), ...]
      code_end = 注释起始列（整行无注释时 = len(line)）
    """
    hits = []
    i = 0
    n = len(line)
    state = "out"          # out | sq(单引号) | dq(双引号) | bt(反引号)
    code_end = n

    while i < n:
        c = line[i]

        # ── 双引号内：反斜杠转义下一个字符
        if state == "dq" and c == "\\":
            i += 2
            continue

        if state == "out":
            if c == "'":
                state = "sq"
                i += 1
                continue
            if c == '"':
                state = "dq"
                i += 1
                continue
            if c == "`":
                state = "bt"
                i += 1
                continue
            if c == "#" and (i == 0 or line[i - 1] in " \t"):
                # 注释开始（`#` 在引号外、且位于词首）
                code_end = i
                break
        elif state == "sq":
            # 单引号内完全字面 ⇒ 不判变量
            if c == "'":
                state = "out"
            i += 1
            continue
        elif state == "dq":
            if c == '"':
                state = "out"
                i += 1
                continue
        elif state == "bt":
            if c == "`":
                state = "out"
                i += 1
                continue

        # ── 到这里 state ∈ {out, dq, bt} ⇒ 变量会被展开
        if c == "$":
            nxt = line[i + 1] if i + 1 < n else ""
            if nxt == "{":
                # ${NAME} ⇒ 花括号已终结变量名 ⇒ 安全，整段跳过
                end = line.find("}", i + 2)
                i = (end + 1) if end != -1 else (i + 2)
                continue
            if nxt and _NAME_START.match(nxt):
                j = i + 1
                while j < n and _NAME_CHAR.match(line[j]):
                    j += 1
                name = line[i + 1:j]
                # 前一个是字符是 `$` ⇒ 实际是 `$$`（PID）⇒ 不是变量名
                if i > 0 and line[i - 1] == "$":
                    i = j
                    continue
                if j < n and _is_non_ascii(line[j]):
                    hits.append((i, name, line[j]))
                i = j
                continue
        i += 1

    if include_comments:
        # 最严口径：注释也扫（用于「全仓有没有残留」这类一次性盘点）
        extra = []
        for m in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*", line):
            end = m.end()
            if end < len(line) and _is_non_ascii(line[end]):
                if not any(h[0] == m.start() for h in hits):
                    extra.append((m.start(), m.group(0)[1:], line[end]))
        hits = hits + extra

    return hits, code_end


def scan_text(text, include_comments=False):
    """扫描整段文本 ⇒ {行号(1基): [(列号, 变量名, 尾字符), ...]}"""
    result = {}
    skip_terminator = None          # 加引号的 heredoc ⇒ 跳过其正文
    for idx, raw in enumerate(text.split("\n"), 1):
        if skip_terminator is not None:
            if raw.strip() == skip_terminator:
                skip_terminator = None
            continue
        hits, code_end = scan_line(raw, include_comments=include_comments)
        if hits:
            result[idx] = hits
        code = raw[:code_end]
        for m in _HEREDOC.finditer(code):
            quoted, word = m.group(1), m.group(2)
            if quoted:
                skip_terminator = word
    return result


def scan_file(path, include_comments=False):
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return scan_text(fh.read())


def iter_shell_files(root):
    """产出 root 下所有 shell 脚本（含无扩展名但带 bash shebang 的）。

    ★ 优先用 git 枚举（快，且天然尊重 .gitignore）：
      `data/` 在本仓库有 **24.7 万个文件 / 8.4G**，os.walk 进去能把闸门拖死
      （实测：直接 walk 会跑到超时）⇒ ⇒ **必须用 git ls-files 取作用域**。
    ★ 非 git 仓库时退回 os.walk，但必须带**跳过名单**兜底。
    """
    tracked = []
    try:
        import subprocess
        out = subprocess.check_output(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=root, stderr=subprocess.DEVNULL,
        )
        tracked = [p for p in out.decode("utf-8", "replace").split("\0") if p]
    except Exception:
        tracked = None

    if tracked is not None:
        candidates = [os.path.join(root, p) for p in tracked]
    else:
        candidates = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in SKIP_DIRS and not d.startswith(".venv")
            )
            for fn in sorted(filenames):
                candidates.append(os.path.join(dirpath, fn))

    for full in candidates:
        if not os.path.isfile(full):
            continue
        if os.path.basename(full).endswith(SHELL_SUFFIXES):
            yield full
            continue
        try:
            with open(full, "rb") as fh:
                first = fh.readline(256)
        except (OSError, IOError):
            continue
        if _SHEBANG.search(first):
            yield full


def report(path, hits_by_line, lines):
    print("── %s" % path)
    for idx in sorted(hits_by_line):
        raw = lines[idx - 1] if idx - 1 < len(lines) else ""
        for col, name, tail in hits_by_line[idx]:
            print("  ❌ L%-5d $%s 紧跟非 ASCII「%s」（列 %d）"
                  % (idx, name, tail, col))
            print("       %s" % raw.strip()[:120])


def check_paths(paths, include_comments=False, quiet=False):
    """返回 (命中总数, 处理文件数)。"""
    total = 0
    scanned = 0
    for path in paths:
        if not os.path.isfile(path):
            sys.stderr.write("文件不存在：%s\n" % path)
            return -1, scanned
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        hits_by_line = scan_text("\n".join(lines), include_comments=include_comments)
        scanned += 1
        if hits_by_line:
            total += len(hits_by_line)
            report(path, hits_by_line, lines)
        elif not quiet:
            print("── %s ✅" % path)
    return total, scanned


# ------------------------------------------------------------------ 自检
# (用例名, 脚本内容, 期望命中数)
_SELFTEST = [
    ("$VAR 后跟 ASCII 必须绿",
     'echo "$VAR-suffix"\n', 0),
    ("★ $VAR 紧跟中文必须红（真实坑型）",
     'echo "（应等于 $MD5_BEFORE）"\n', 1),
    ("${VAR} 紧跟中文必须绿（花括号已终结变量名）",
     'echo "（应等于 ${MD5_BEFORE}）"\n', 0),
    ("整行注释里的 $VAR中文 必须绿（讲坑的注释不判）",
     "# 说明 $code（ 会被吞进变量名\n", 0),
    ("行尾注释里的 $VAR中文 必须绿",
     'echo "ok"  # 注释 $code（\n', 0),
    ("单引号字面量必须绿（bash 里不展开）",
     "echo '$VAR中文'\n", 0),
    ("双引号内必须红（会展开）",
     'echo "$VAR中文"\n', 1),
    ("位置参数 $1 后跟中文必须绿（$1 不是变量名）",
     'echo "$1中文"\n', 0),
    ("$$VAR 必须绿（PID，不是变量名）",
     'echo "$$PID中文"\n', 0),
    ("引号内的 # 不是注释 ⇒ 必须红",
     'echo "# not comment $VAR中文"\n', 1),
    ("加引号的 heredoc 必须绿（<<'EOF' 不展开）",
     "cat <<'EOF'\n$VAR中文\nEOF\n", 0),
    ("未加引号的 heredoc 必须红（<<EOF 会展开）",
     "cat <<EOF\n$VAR中文\nEOF\n", 1),
    ("真实现场复刻必须红：路径变量 + 中文",
     'echo "路径 $HOME目录不存在"\n', 1),
    ("转义 \\$VAR 后跟中文必须绿（\\$ 不展开）",
     'echo "\\$VAR中文"\n', 0),
]


def selftest():
    """闸门自检：负向对照必须红、正向必须绿 ⇒ 证明闸门不是恒真。"""
    print("── 检测器自检 ──")
    failures = 0
    for name, content, expect in _SELFTEST:
        got = scan_text(content)
        count = sum(len(v) for v in got.values())
        if count == expect:
            print("  ✅ %s" % name)
        else:
            failures += 1
            print("  ❌ %s" % name)
            print("      期望命中 %d，实际 %d（%s）"
                  % (expect, count, got if count else "无"))

    # ★ 端到端：证明**退出码**真的非 0（否则它只是打印，不是闸门）
    print("── 端到端退出码自检（证明是闸门，不是一段打印）──")
    tmp = tempfile.mkdtemp(prefix="bash32lint_")
    try:
        bad = os.path.join(tmp, "bad.sh")
        good = os.path.join(tmp, "good.sh")
        with io.open(bad, "w", encoding="utf-8") as fh:
            fh.write('echo "（应等于 $MD5_BEFORE）"\n')
        with io.open(good, "w", encoding="utf-8") as fh:
            fh.write('echo "（应等于 ${MD5_BEFORE}）"\n')
        rc_bad = main([bad, "--quiet"])
        rc_good = main([good, "--quiet"])
        if rc_bad == 1 and rc_good == 0:
            print("  ✅ 坏文件 rc=1 / 好文件 rc=0 ⇒ 退出码契约成立")
        else:
            failures += 1
            print("  ❌ 坏文件 rc=%d（期望 1）/ 好文件 rc=%d（期望 0）"
                  % (rc_bad, rc_good))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = len(_SELFTEST) + 1
    if failures:
        print("\n⛔ 自检 %d/%d 项失败 ⇒ 闸门本身不可信（退出码 1）"
              % (failures, total))
        return 1
    print("\n✅ 自检 %d/%d 全过 ⇒ 闸门能红也能绿" % (total, total))
    return 0


def main(argv):
    args = list(argv)
    quiet = False
    include_comments = False

    if "--selftest" in args:
        args.remove("--selftest")
        if args:
            sys.stderr.write("用法错误：--selftest 不接受文件参数\n")
            return 2
        return selftest()
    if "--quiet" in args:
        quiet = True
        args.remove("--quiet")
    if "--include-comments" in args:
        include_comments = True
        args.remove("--include-comments")

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = args or sorted(iter_shell_files(root))
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        sys.stderr.write("文件不存在：%s\n" % ", ".join(missing))
        return 2

    total, scanned = check_paths(paths, include_comments, quiet)
    if total < 0:
        return 2

    if total:
        print("\n⛔ 发现 %d 处「$VAR 紧跟非 ASCII」⇒ 退出码 1"
              "（bash 3.2 会把非 ASCII 首字节吞进变量名 ⇒ 必红）" % total)
        print("   修法：写成 ${VAR}（花括号终结变量名）")
        return 1
    print("\n✅ 扫了 %d 个 shell 脚本，全部通过" % scanned)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
