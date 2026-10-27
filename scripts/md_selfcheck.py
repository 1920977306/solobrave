#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown 格式自查闸门（交接单 / 文档）。

★ 为什么要有这个（2026-10-27，B 边 §68.8 实锤）：
  我那轮把「自查脚本」接在 `&&` 链上跑，**它发现了 2 处粗体标记奇数，
  但退出码仍是 0** ⇒ 链条照样走到 `git commit` ⇒ **带格式错的内容进了 git**。
  ⇒ **没有非零退出码的自查 = 不是闸门，只是一段打印。**
  ⇒ 本脚本的**唯一职责**就是：**发现问题时 exit 1**，从而能真正拦住提交。

用法：
    python3 scripts/md_selfcheck.py                      # 扫默认文档
    python3 scripts/md_selfcheck.py <file> [<file>...]  # 扫指定文件
    python3 scripts/md_selfcheck.py --from-line N <file>  # 只查第 N 行起的新增段
    python3 scripts/md_selfcheck.py --quiet <file>      # 只在失败时打印
    python3 scripts/md_selfcheck.py --selftest          # 闸门自检（负向对照）

退出码：
    0 = 全部通过
    1 = 发现问题（**这就是闸门效果**）
    2 = 用法错误（文件不存在等）

检查项：
    ① 行首围栏（``` / ~~~）必须成对闭合
    ② 粗体标记 `**` 必须配平
    ③ 中文引号 `「` / `」` 必须配平
    ④ 表格列数必须逐块一致（单元格内转义的竖线不算分隔符）

★ 设计约束（都是踩过的坑）：
    - 行内代码先替换成占位符 ⇒ 否则「举例用的 `**`」会把检查器自己骗了
    - 表格按「连续行块」分组 ⇒ 不同表格之间不比列数

★★ 2026-10-27（A 边 §70 实锤后修正，A 边接管本文件并保留 B 边全部内容）：
   **配平口径从「逐行」改成「按段落」** —— 原实现是 `clean.count("**") % 2` 逐行判，
   而 Markdown 里 **粗体/引号合法跨行**（`……**粗体\n跨行**……`）⇒
   实测在 AGENTS.md 上报 **43 条**、交接单上报 **44 条**，抽查 **绝大多数是假阳性**
   （例：AGENTS.md L128 开、L129 闭的跨行粗体被判「粗体标记不配平」）。
   ⇒ **假阳性会让闸门变成阻塞器** ⇒ 每次改 AGENTS.md / 交接单都被拦 ⇒
      实际后果是大家绕过它 ⇒ **回到「自查形同虚设」的原始失败模式**。
   ⇒ 改法：粗体/引号**按段落块累计**（空行 / 围栏边界断块）⇒ 跨行合法、真未闭合仍红；
      **表格行按行判**（单元格是原子的，跨行粗体不存在）。
   ⇒ ★ 「一个测试必须能红」不能靠嘴说 ⇒ 新增 `--selftest`：**6 个用例**
      （3 个必须红 / 3 个必须绿）⇒ 闸门自己证明自己不是恒真。
"""
import io
import os
import re
import shutil
import sys
import tempfile

DEFAULT_DOCS = [
    "docs/PARALLEL-HANDOFF-2026-10-25.md",
]

# 行内代码 → 等长占位符（保持列位置，且不含 ** 「 」 |)
_INLINE_CODE = re.compile(r"`[^`]*`")
# 转义竖线 → 占位符（表格单元格内的 \| 不是分隔符）
_ESCAPED_PIPE = "\x01"


def _strip_inline(text: str) -> str:
    text = _INLINE_CODE.sub(lambda m: "\x00" * len(m.group(0)), text)
    return text.replace("\\|", _ESCAPED_PIPE)


def _fence_open(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def _is_table_row(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") and s.endswith("|")


def check(path: str, from_line: int = 1):
    """返回 (问题列表, 统计)。问题列表每项 = (行号, 类型, 摘要)。"""
    issues = []
    with io.open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    in_fence = False
    tables = []
    cur_table = []
    # ★ 段落缓冲：粗体/引号按**段落**累计（跨行合法）
    para = []            # [(idx, raw)]
    fence_lines = set()  # 围栏行本身（内容不参与任何配平判定）

    def flush_para():
        """段落结束 ⇒ 判一次配平。表格行按行判，散文按段判。"""
        if not para:
            return
        rows = [x for x in para if _is_table_row(x[1])]
        prose = [x for x in para if not _is_table_row(x[1])]
        # 表格行 = 原子单元格 ⇒ 逐行判
        for idx, raw in rows:
            clean = _strip_inline(raw)
            if clean.count("**") % 2:
                issues.append((idx, "粗体标记不配平", raw[:100]))
            if clean.count("「") != clean.count("」"):
                issues.append((idx, "中文引号不配平", raw[:100]))
        # 散文 = 可跨行 ⇒ 按段落累计
        if prose:
            start = prose[0][0]
            if max(i for i, _ in prose) >= from_line:
                bold = sum(_strip_inline(r).count("**") for _, r in prose)
                q_open = sum(_strip_inline(r).count("「") for _, r in prose)
                q_close = sum(_strip_inline(r).count("」") for _, r in prose)
                if bold % 2:
                    issues.append((start, "粗体标记不配平",
                                   "段落内 ** 共 %d 个（奇数）⇒ 可能未闭合" % bold))
                if q_open != q_close:
                    issues.append((start, "中文引号不配平",
                                   "段落内 「%d / 」%d ⇒ 可能未闭合" % (q_open, q_close)))
        del para[:]

    for idx, raw in enumerate(lines, 1):
        line = raw.strip()
        if _fence_open(line):
            flush_para()
            in_fence = not in_fence
            fence_lines.add(idx)
            continue
        if in_fence:
            continue
        if not line:
            flush_para()
        else:
            para.append((idx, raw))

        if _is_table_row(line):
            cols = len(_strip_inline(raw).strip().strip("|").split("|"))
            cur_table.append((idx, cols))
            continue
        if cur_table:
            tables.append(cur_table)
            cur_table = []
    flush_para()
    if cur_table:
        tables.append(cur_table)

    for block in tables:
        if block[0][0] < from_line:
            continue
        widths = {c for _, c in block}
        if len(widths) > 1:
            issues.append(
                (block[0][0], "表格列数不一致 %s" % sorted(widths),
                 "起始行 %d，共 %d 行" % (block[0][0], len(block)))
            )

    if in_fence:
        issues.append((len(lines), "围栏未闭合", "文件结束时仍在代码块内"))

    stats = {
        "lines": len(lines),
        "fences_balanced": not in_fence,
        "tables": len(tables),
    }
    return issues, stats


# ------------------------------------------------------------------ 自检
_SELFTEST = [
    # (名称, 文件内容, 必须命中的问题类型子集, 期望是否报错)
    ("干净文档应通过",
     "# 标题\n\n普通段落。\n", set(), False),
    ("未闭合粗体必须红",
     "段落\n\n**未闭合粗体\n", {"粗体标记不配平"}, True),
    ("未闭合中文引号必须红",
     "段落\n\n他说「未闭合引号\n", {"中文引号不配平"}, True),
    ("未闭合围栏必须红",
     "```\ncode\n", {"围栏未闭合"}, True),
    ("表格列数不齐必须红",
     "| a | b |\n|---|---|\n| c |\n", {"表格列数不一致"}, True),
    ("跨行粗体/跨行引号**必须绿**（回归守卫：旧逐行口径会误报）",
     "他说「这里\n跨行」并且**粗体\n也跨行**结束\n", set(), False),
]


def selftest() -> int:
    """闸门自检：负向对照必须红、正向必须绿 ⇒ 证明闸门不是恒真。"""
    tmp = tempfile.mkdtemp(prefix="mdselfcheck_")
    failures = 0
    try:
        for name, content, expect_kinds, expect_issue in _SELFTEST:
            p = os.path.join(tmp, "case.md")
            with io.open(p, "w", encoding="utf-8") as fh:
                fh.write(content)
            issues, _ = check(p)
            kinds = {k for _, k, _ in issues}
            # ★ 前缀匹配：表格那条的 kind 带列数列表（"表格列数不一致 [1, 2]"）⇒ 精确串匹配会假红
            hit = any(any(k.startswith(exp) for k in kinds) for exp in expect_kinds)
            ok = (hit == expect_issue) if expect_kinds else (not issues)
            if ok:
                print("  ✅ %s" % name)
            else:
                failures += 1
                print("  ❌ %s" % name)
                print("      期望 %s，实际问题：%s" % (
                    ("命中 %s" % sorted(expect_kinds)) if expect_kinds else "无问题",
                    sorted(kinds) or "无"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if failures:
        print("\n⛔ 自检 %d 项失败 ⇒ 闸门本身不可信（退出码 1）" % failures)
        return 1
    print("\n✅ 自检 %d/%d 全过 ⇒ 闸门能红也能绿" % (len(_SELFTEST), len(_SELFTEST)))
    return 0


def main(argv):
    args = list(argv)
    quiet = False
    from_line = 1
    if "--selftest" in args:
        args.remove("--selftest")
        if args:
            sys.stderr.write("用法错误：--selftest 不接受文件参数\n")
            return 2
        return selftest()
    if "--quiet" in args:
        quiet = True
        args.remove("--quiet")
    if "--from-line" in args:
        i = args.index("--from-line")
        try:
            from_line = int(args[i + 1])
        except (IndexError, ValueError):
            sys.stderr.write("用法错误：--from-line 需要一个整数\n")
            return 2
        del args[i:i + 2]

    paths = args or DEFAULT_DOCS
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing:
        sys.stderr.write("文件不存在：%s\n" % ", ".join(missing))
        return 2

    total = 0
    for path in paths:
        issues, stats = check(path, from_line)
        if not quiet:
            print("── %s（%d 行，表格 %d 块，围栏 %s）"
                  % (path, stats["lines"], stats["tables"],
                     "闭合" if stats["fences_balanced"] else "未闭合"))
        for line_no, kind, detail in issues:
            total += 1
            print("  ❌ L%-6d %-16s %s" % (line_no, kind, detail))
        if not issues and not quiet:
            print("  ✅ 通过")

    if total:
        print("\n⛔ 发现 %d 处格式问题 ⇒ 退出码 1（闸门生效，**不要提交**）" % total)
        return 1
    print("\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
