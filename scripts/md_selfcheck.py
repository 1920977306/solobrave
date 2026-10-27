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

退出码：
    0 = 全部通过
    1 = 发现问题（**这就是闸门效果**）
    2 = 用法错误（文件不存在等）

检查项：
    ① 行首围栏（``` / ~~~）必须成对闭合
    ② 粗体标记 `**` 必须逐行配平（行内代码内的不算）
    ③ 中文引号 `「` / `」` 必须配平
    ④ 表格列数必须逐块一致（单元格内转义的竖线不算分隔符）

★ 设计约束（都是踩过的坑）：
    - 行内代码先替换成占位符 ⇒ 否则「举例用的 `**`」会把检查器自己骗了
    - 表格按「连续行块」分组 ⇒ 不同表格之间不比列数
    - 逐行配平 ⇒ 跨行的粗体（**外**内**）不误报
"""
import io
import os
import re
import sys

DEFAULT_DOCS = [
    "docs/PARALLEL-HANDOFF-2026-10-25.md",
]

# 行内代码 → 等长占位符（保持列位置，且不含 ** 「 」 |）
_INLINE_CODE = re.compile(r"`[^`]*`")
# 转义竖线 → 占位符（表格单元格内的 \| 不是分隔符）
_ESCAPED_PIPE = "\x01"


def _strip_inline(text: str) -> str:
    text = _INLINE_CODE.sub(lambda m: "\x00" * len(m.group(0)), text)
    return text.replace("\\|", _ESCAPED_PIPE)


def _fence_open(line: str) -> bool:
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def check(path: str, from_line: int = 1):
    """返回 (问题列表, 统计)。问题列表每项 = (行号, 类型, 摘要)。"""
    issues = []
    with io.open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")

    in_fence = False
    tables = []
    cur_table = []

    for idx, raw in enumerate(lines, 1):
        line = raw.strip()
        if _fence_open(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        clean = _strip_inline(raw)

        if idx >= from_line:
            if clean.count("**") % 2:
                issues.append((idx, "粗体标记不配平", raw[:100]))
            if clean.count("「") != clean.count("」"):
                issues.append((idx, "中文引号不配平", raw[:100]))

        if line.startswith("|") and line.endswith("|"):
            cols = len(_strip_inline(raw).strip().strip("|").split("|"))
            cur_table.append((idx, cols))
            continue
        if cur_table:
            tables.append(cur_table)
            cur_table = []
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


def main(argv):
    args = list(argv)
    quiet = False
    from_line = 1
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
