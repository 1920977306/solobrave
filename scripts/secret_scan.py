#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
密钥扫描器 —— 防止明文密钥再次进仓库。

背景（2026-10-21 事故）:
  4 把生产 Kimi API key 明文硬编码在 solobrave-server.py 里，被提交进 git 历史
  并推送到 GitHub。撤代码撤不回历史，只能靠「轮换密钥」作废。
  根因不是笔误，是**流程性漏洞**：没有任何机制在提交前拦住它。
  本脚本就是那道闸门。

铁律（与 AGENTS.md §7 密钥管理铁律配套）:
  1. 密钥只进 .env（已 gitignore，权限 600），**代码里只读不算**
  2. 提交前必跑本脚本，退出码非 0 不许提交
  3. 任何带密钥的备份文件一律放 repo 外（~/.solobrave-backups/），
     因为 .gitignore 对已跟踪文件不生效，备份文件最容易混进去

用法:
  scripts/secret_scan.py                # 扫工作区（git 跟踪文件 + 未跟踪且未被 ignore 的新文件）★ 默认
  scripts/secret_scan.py --all          # 同上（显式全量，发版前用）
  scripts/secret_scan.py --staged       # 只扫暂存区改动（提交前用，最快）
  scripts/secret_scan.py --tracked-only # 只扫 git 跟踪文件（不含未跟踪新文件）
  scripts/secret_scan.py --history      # 扫 git 历史所有版本（轮换后确认残留，约 3-5 秒）

⚠️ 2026-10-21 修过一个「恒真闸门」级别的坑:
   旧的 `--all` 实现是 `extra = [] if mode == 'all' else untracked_not_ignored()`
   —— 即 **`--all` 反而把未跟踪的新文件全跳过了**，与「all」字面意思相反。
   新加的泄漏恰恰都在未跟踪文件里（还没 git add），于是「用了 --all 就万事大吉」
   实际是个**假安全感**。现在 `--all` = 真全量（跟踪 + 未跟踪），并另开
   `--tracked-only` 给确实只想扫跟踪文件的场景。

退出码: 0 = 干净 · 1 = 发现疑似明文密钥 · 2 = 用法/环境错误
"""

import os
import re
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 供应商 token 特征 ───────────────────────────────────────────────
# 每条 = (名字, 正则)。正则必须要求足够长的随机串，避免撞上普通单词。
TOKEN_PATTERNS = [
    # ⚠️ 顺序有意义：去重逻辑对**等长**命中取「先命中者」（items.sort 按长度降序 + 稳定排序），
    #   所以具体厂商的规则必须排在通用 `sk-` 规则**前面**，
    #   否则 `sk-cp-…` / `sk-api-…` 会被误标成「Moonshot/Kimi key」→ allowlist 的
    #   「原因」栏写错，误导后续维护者判断该找谁轮换。
    ('MiniMax 套餐 key', re.compile(r'\bsk-cp-[A-Za-z0-9_\-]{24,}\b')),
    ('MiniMax 开放平台 key', re.compile(r'\bsk-api-[A-Za-z0-9_\-]{24,}\b')),
    ('Code OAuth token', re.compile(r'\bmm(?:oat|ort)_[A-Za-z0-9_\-]{20,}\b')),
    ('Moonshot/Kimi key', re.compile(r'\bsk-[A-Za-z0-9_\-]{24,}\b')),
    ('Kimi coding key',   re.compile(r'\bsk-kimi-[A-Za-z0-9]{32,}\b')),
    ('GitHub PAT',        re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b')),
    ('GitHub fine-grained', re.compile(r'\bgithub_pat_[A-Za-z0-9_]{50,}\b')),
    ('AWS access key',    re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ('Google API key',    re.compile(r'\bAIza[0-9A-Za-z_\-]{35}\b')),
    ('Slack token',       re.compile(r'\bxox[abprs]-[A-Za-z0-9\-]{20,}\b')),
    ('Anthropic key',     re.compile(r'\bsk-ant-[A-Za-z0-9_\-]{24,}\b')),
    ('OpenAI key',        re.compile(r'\bsk-proj-[A-Za-z0-9_\-]{24,}\b')),
    ('Telegram bot token', re.compile(r'\b\d{8,10}:[A-Za-z0-9_\-]{35}\b')),
]

# 「变量 = 明文」形式：比裸 token 更明确地指向硬编码
ASSIGN_PATTERNS = [
    ('硬编码 api_key/secret/token',
     re.compile(
         r'''(?ix)
         \b(?:[A-Za-z0-9_]*(?:api[_-]?key|apikey|secret|passwd|password|token|credential)[A-Za-z0-9_]*)
         \s*[:=]\s*
         ["']([^"'\s]{16,})["']
         ''')),
    ('硬编码 Authorization 头',
     re.compile(r'''(?i)["'](?:Bearer|Basic)\s+[A-Za-z0-9_\-\.=]{20,}["']''')),
]

# 明显的占位/示例/脱敏值，不算泄漏
ALLOW = re.compile(
    r'(?ix)^(?:'
    r'x{3,}|\.{3,}|\*{3,}|redacted|masked|placeholder|example|changeme|your[_-]?\w*'
    r'|none|null|true|false|test|dummy|sample|lorem'
    r'|sk-?(?:xxx|yyy|zzz|test|demo|example|placeholder)?$'
    r'|<[^>]{0,40}>|\{[^}]{0,40}\}'
    r')$'
)

# 扫描时跳过的路径（这些是密钥的合法归宿或非源码）
SKIP_PATH_RE = re.compile(
    r'(?i)(^|/)(\.git|node_modules|__pycache__|\.venv|venv|dist|build|coverage)'
    r'|(^|/)\.env($|\.)'
    r'|\.bak[-.]'
    r'|\.(pyc|pyo|so|dylib|dll|exe|bin|zip|tar|gz|png|jpg|jpeg|gif|webp|mp4|mov|pdf)$'
)

# 这些文件名里出现 token 形态几乎必然是误报（示例数据/夹具），但仍会打印出来供人工确认
LOW_PRIORITY_HINT = re.compile(
    r'(?i)(^|/)(tests?|fixtures?|examples?|samples?|demo|mock)/'
    r'|\.(md|docx|txt)$'
)

BIN_EXT = {'.py', '.js', '.ts', '.tsx', '.jsx', '.sh', '.bash', '.json', '.yaml', '.yml',
           '.toml', '.ini', '.cfg', '.conf', '.env', '.html', '.css', '.sql', '.md',
           '.plist', '.xml', '.txt', '.rb', '.go', '.java', '.php', '.mjs', '.cjs'}


def run(cmd, cwd=ROOT):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return p.stdout


def tracked_files():
    out = run(['git', 'ls-files', '-z'])
    return [f for f in out.split('\0') if f]


def untracked_not_ignored():
    out = run(['git', 'ls-files', '-z', '--others', '--exclude-standard'])
    return [f for f in out.split('\0') if f]


def staged_files():
    out = run(['git', 'diff', '--cached', '--name-only', '-z', '--diff-filter=ACMR'])
    return [f for f in out.split('\0') if f]


def staged_diff_lines():
    """暂存区新增行 → [(file, lineno, text)]，用于精确定位"""
    return _diff_added_lines(['git', 'diff', '--cached', '-U0', '--no-color'])


def history_diff_lines():
    """git 历史所有版本的新增行 → [(file, lineno, text)]。

    ⚠️ 2026-10-21 真正实现（之前文档写着「--all 连历史一起扫」，代码里根本没有）。
    用途：密钥改代码不算修，**历史里还在就必须去供应商后台轮换**，
    这个模式就是用来确认「历史里还剩几把、分别在哪几个提交引入的」。
    """
    return _diff_added_lines(['git', 'log', '--all', '-p', '-U0', '--no-color'])


def _diff_added_lines(cmd):
    out = run(cmd)
    hits = []
    fname, lineno = None, 0
    for line in out.split('\n'):
        m = re.match(r'^\+\+\+ b/(.*)$', line)
        if m:
            fname = m.group(1)
            continue
        m = re.match(r'^@@ -\S+ \+(\d+)', line)
        if m:
            lineno = int(m.group(1))
            continue
        if line.startswith('+') and not line.startswith('+++'):
            hits.append((fname, lineno, line[1:]))
            lineno += 1
        elif line.startswith('-') or line.startswith(' '):
            lineno += 1
    return hits


def looks_like_placeholder(value):
    v = (value or '').strip()
    if not v:
        return True
    if ALLOW.match(v):
        return True
    # 哨兵值：__USE_KIMI_KEY_POOL__ / __PLACEHOLDER__
    if re.match(r'^__[A-Za-z0-9_]+__$', v):
        return True
    # 环境变量名常量：SOLOBRAVE_EMBEDDING_API_KEY / SOME_APP_SECRET
    if re.match(r'^[A-Z][A-Z0-9_]{3,}$', v) and re.search(r'(KEY|SECRET|TOKEN|PASSWORD|PASSWD|CRED)', v):
        return True
    # 明显是引用变量而不是字面量
    if re.match(r'(?i)^(os\.environ|process\.env|\{|\$|<%|%s|\$)', v):
        return True
    # 全是小写单词/短横线词组，不是随机串
    if re.match(r'^[a-z][a-z0-9_\-]{3,}$', v) and not re.search(r'\d', v):
        return True
    return False


def scan_text(path, text):
    hits = []
    for name, pat in TOKEN_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(0)
            if looks_like_placeholder(val):
                continue
            line = text.count('\n', 0, m.start()) + 1
            hits.append((name, line, val))
    for name, pat in ASSIGN_PATTERNS:
        for m in pat.finditer(text):
            val = m.group(1) if m.groups() else m.group(0)
            if looks_like_placeholder(val):
                continue
            line = text.count('\n', 0, m.start()) + 1
            hits.append((name, line, val))
    return hits


def read_text(path):
    full = os.path.join(ROOT, path)
    try:
        if os.path.getsize(full) > 4 * 1024 * 1024:
            return None
        ext = os.path.splitext(full)[1].lower()
        if ext in ('.docx', '.xlsx', '.pptx'):
            if zipfile.is_zipfile(full):
                with zipfile.ZipFile(full) as z:
                    parts = [n for n in z.namelist() if n.endswith(('.xml', '.txt', '.json'))
                             and 'word/media' not in n]
                    if len(parts) > 30:
                        return None
                    return '\n'.join(z.read(n).decode('utf-8', 'ignore') for n in parts)
            return None
        if ext not in BIN_EXT:
            return None
        raw = open(full, 'rb').read()
        if b'\x00' in raw[:2048]:
            return None
        return raw.decode('utf-8', 'ignore')
    except Exception:
        return None


def redact(val):
    v = val if isinstance(val, str) else str(val)
    if len(v) <= 14:
        return v[:4] + '…'
    return v[:10] + '…' + v[-4:]


def fingerprint(val):
    """已登记项的指纹 = sha256(值)[:12]。不用明文，避免 allowlist 本身变成新的泄漏点。"""
    import hashlib
    return hashlib.sha256(val.encode('utf-8', 'ignore')).hexdigest()[:12]


ALLOWLIST_FILE = os.path.join(ROOT, 'scripts', 'secret_scan_allowlist.txt')


def load_allowlist():
    """返回 {指纹: (原因, 负责人, 登记日期)}"""
    out = {}
    if not os.path.exists(ALLOWLIST_FILE):
        return out
    with open(ALLOWLIST_FILE, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split('|')]
            if len(parts) < 2:
                continue
            fp = parts[0]
            reason = parts[1]
            owner = parts[2] if len(parts) > 2 else '—'
            date = parts[3] if len(parts) > 3 else '—'
            out[fp] = (reason, owner, date)
    return out


def main():
    argv = sys.argv[1:]

    # --fingerprint <值>：生成 allowlist 登记用的指纹（先于参数校验处理）
    if '--fingerprint' in argv:
        i = argv.index('--fingerprint')
        if i + 1 >= len(argv):
            print('用法: secret_scan.py --fingerprint "<值>"', file=sys.stderr)
            return 2
        print(fingerprint(argv[i + 1]))
        return 0

    mode = 'work'
    for a in argv:
        if a == '--staged':
            mode = 'staged'
        elif a == '--all':
            mode = 'all'
        elif a == '--tracked-only':
            mode = 'tracked'
        elif a == '--history':
            mode = 'history'
        elif a in ('-h', '--help'):
            print(__doc__)
            return 0
        else:
            print(f'未知参数: {a}', file=sys.stderr)
            print(__doc__, file=sys.stderr)
            return 2

    if not os.path.isdir(os.path.join(ROOT, '.git')):
        print('❌ 不在 git 仓库里', file=sys.stderr)
        return 2


    allow = load_allowlist()
    findings = []

    if mode == 'staged':
        print('🔑 密钥扫描 · 范围: 暂存区改动')
        for fname, lineno, text in staged_diff_lines():
            if not fname or SKIP_PATH_RE.search(fname):
                continue
            for name, _, val in scan_text(fname, text):
                findings.append((fname, lineno, name, val, 'staged'))
    elif mode == 'history':
        print('🔑 密钥扫描 · 范围: git 历史所有版本（慢，约 3-5 秒）')
        for fname, lineno, text in history_diff_lines():
            if not fname or SKIP_PATH_RE.search(fname):
                continue
            for name, _, val in scan_text(fname, text):
                findings.append((fname, lineno, name, val, 'history'))
    else:
        files = tracked_files()
        # ⚠️ 只有显式 --tracked-only 才跳过未跟踪文件。
        #   （旧的 --all 反而跳过 → 新泄漏恰恰在未跟踪文件里 → 假安全感）
        extra = [] if mode == 'tracked' else untracked_not_ignored()
        print(f'🔑 密钥扫描 · 范围: git 跟踪文件 {len(files)} 个'
              + (f' + 未跟踪新文件 {len(extra)} 个' if extra else ''))
        for path in files + extra:
            if SKIP_PATH_RE.search(path):
                continue
            text = read_text(path)
            if text is None:
                continue
            for name, lineno, val in scan_text(path, text):
                findings.append((path, lineno, name, val, 'tracked' if path in files else 'untracked'))

    if not findings:
        print('✅ 未发现明文密钥')
        return 0

    # ── 去重 ──
    # 1) 同一行：若一个命中值是另一个的子串（如 sk-02e90… 与裸 02e90…），保留更长的那个
    by_line = {}
    for path, lineno, name, val, src in findings:
        by_line.setdefault((path, lineno), []).append((name, val, src))
    cleaned = []
    for (path, lineno), items in by_line.items():
        items.sort(key=lambda x: -len(x[1]))
        kept = []
        for name, val, src in items:
            if any(val != kv and val in kv for _, kv, _ in kept):
                continue
            kept.append((name, val, src))
        for name, val, src in kept:
            cleaned.append((path, lineno, name, val, src))

    # 2) 跨文件同值合并（同一个 key 出现在 N 个文件 → 一条，别刷屏）
    by_val = {}
    for path, lineno, name, val, src in cleaned:
        by_val.setdefault(val, []).append((path, lineno, name, src))

    # 3) 已登记项降级为「不阻塞但必须处理」，未登记项阻塞提交
    known, new = {}, {}
    for val, items in by_val.items():
        (known if fingerprint(val) in allow else new)[val] = items

    if known:
        print()
        print(f'⚠️  已登记待处理 {len(known)} 个（不阻塞提交，但每条都有负责人和期限）：')
        for val, items in sorted(known.items(), key=lambda kv: -len(kv[1])):
            reason, owner, date = allow[fingerprint(val)]
            path, lineno, name, _ = items[0]
            extra = f'  (+{len(items) - 1} 个文件同值)' if len(items) > 1 else ''
            print(f'  [{name}] {path}:{lineno}{extra}')
            print(f'      {redact(val)}  ← {reason}')
            print(f'      负责人: {owner} · 登记: {date} · 指纹: {fingerprint(val)}')
            for p, ln, _, _ in items[1:]:
                print(f'        └─ 同值: {p}:{ln}')

    if new:
        print()
        print(f'⛔ 新发现 {len(new)} 个疑似明文密钥（**阻塞提交**）：')
        for val, items in sorted(new.items(), key=lambda kv: -len(kv[1])):
            path, lineno, name, _ = items[0]
            extra = f'  (+{len(items) - 1} 个文件同值)' if len(items) > 1 else ''
            hint = '  (低优先: 示例/文档目录，仍需人工确认)' if LOW_PRIORITY_HINT.search(path) else ''
            print(f'  [{name}] {path}:{lineno}{extra}')
            print(f'      {redact(val)}{hint}')
            print(f'      指纹: {fingerprint(val)}  ← 确认是误报/待处理项时登记到 {os.path.relpath(ALLOWLIST_FILE, ROOT)}')
            for p, ln, _, _ in items[1:]:
                print(f'        └─ 同值: {p}:{ln}')
        print()
        print('处理方式:')
        print('  1) 密钥移到 .env（已 gitignore, chmod 600），代码改成 os.environ.get(...) 读')
        print('  2) 若已提交过 → **必须去供应商后台轮换该密钥**，改代码撤不回 git 历史')
        print('  3) 备份文件放 repo 外（~/.solobrave-backups/），别留在仓库里')
        print('  4) 确认是误报 → 用 --fingerprint 生成指纹登记进 allowlist，别直接忽略整类')
        return 1

    print()
    print('✅ 没有未登记的明文密钥')
    return 0


if __name__ == '__main__':
    sys.exit(main())
