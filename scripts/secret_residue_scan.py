#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""★ 密钥明文残留扫描器（/private/tmp 等临时目录）—— 轮换后补偿控制

★★ 为什么要有这个（2026-10-27，A 边 §70.2 / §70.4 实锤）：
   守卫 `scripts/e2e_feishu_rotate_exit_codes.sh` 的「本次运行零残留」断言，
   glob 写的是 `/private/tmp/.sbfb.?????????`（**9 个 ?**），
   而被测脚本 `mktemp -d /private/tmp/.sbfb.XXXXXXXX` 产生的是 **8 位**随机名
   ⇒ **命中恒 0** ⇒ 该断言**恒真 = 判不出任何东西**（假阴性 ⇒ 假绿）。
   ⇒ ⇒ 所以轮换后**不能只信守卫的「零残留」** ⇒ 必须有一道**独立**的复核。

★ 口径（两个都重要）：
   ① **扫描范围**：默认扫 `/private/tmp`（★ 注意 `/tmp` 是它的 symlink ⇒ 只扫一个，别重复计数）。
   ② **匹配方式**：把**现役 secret 明文只装进内存**，在磁盘上**逐字节**找
      ⇒ **不是按文件名/正则猜**，而是真值命中
      ⇒ **明文绝不打印、绝不进 argv、绝不写日志**；命中时只报
      「哪个 secret（名字 + md5[:12] 指纹）+ 哪个文件路径」。

用法：
    python3 scripts/secret_residue_scan.py                 # 扫 /private/tmp
    python3 scripts/secret_residue_scan.py <dir> [...]     # 扫指定目录
    python3 scripts/secret_residue_scan.py --selftest      # 自检：证明它能命中（不是恒真）

退出码：
    0 = 没发现现役明文残留
    1 = **发现了** ⇒ 明文在盘上 ⇒ 立刻按 runbook §6 处理
    2 = 用法/环境错误

★ 「一个检查必须能失败」不能靠嘴说 ⇒ `--selftest` 会**故意**在临时目录里放一个
  **canary**（假的、不是真 secret）⇒ 扫描器**必须报出它** ⇒ 报不出 = 扫描器坏了。
"""
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile

DEFAULT_SCAN_DIRS = ["/private/tmp"]
STORE_SQLITE = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")
# 扫哪几类凭据（名字前缀 ⇒ 只取这些，别把整个 store 拉进来）
WATCH_PREFIXES = ("FEISHU_APPSECRET_", "GATEWAY_AUTH_TOKEN")


def _fingerprint(value: str) -> str:
    return hashlib.md5(value.strip().encode()).hexdigest()[:12]


def load_live_secrets():
    """从 openclaw secret store 读现役明文 —— **只进内存**，不打印。"""
    live = {}
    if not os.path.isfile(STORE_SQLITE):
        return live
    tmp = tempfile.mkdtemp(prefix="resid_scan_")
    dst = os.path.join(tmp, "store.db")
    try:
        shutil.copy2(STORE_SQLITE, dst)
        con = sqlite3.connect(dst)
        try:
            for name, value in con.execute(
                "SELECT name, value FROM secret_store_entries"
            ):
                if not name or not value:
                    continue
                if not name.startswith(WATCH_PREFIXES):
                    continue
                v = str(value).strip()
                if v:
                    live[v.encode()] = name
        finally:
            con.close()
    except Exception as e:  # 读不到 ⇒ 不能当成「没有明文」
        sys.stderr.write("⚠️ 读取 secret store 失败：%s\n" % (e,))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return live


def scan(dirs, live):
    """在 dirs 下逐字节找 live 里的每一个值 ⇒ 返回 {路径: {secret 名}}。"""
    hits = {}
    if not live:
        return hits
    for root in dirs:
        if not os.path.isdir(root):
            continue
        for cur, subdirs, files in os.walk(root):
            # 跳过明显的系统/依赖目录（避免把扫描拖成几十分钟）
            subdirs[:] = [d for d in subdirs
                          if d not in (".venv-test", "node_modules", ".git",
                                       "com.apple.Safari", "TemporaryItems")]
            for f in files:
                p = os.path.join(cur, f)
                try:
                    if os.path.islink(p) or not os.path.isfile(p):
                        continue
                    if os.path.getsize(p) > 256 * 1024 * 1024:
                        continue
                    with open(p, "rb") as fh:
                        data = fh.read()
                except Exception:
                    continue
                for val, name in live.items():
                    if val in data:
                        hits.setdefault(p, set()).add(name)
    return hits


def selftest() -> int:
    """自检：放一个 canary ⇒ 扫描器**必须**报出来 ⇒ 证明它不是恒真。"""
    tmp = tempfile.mkdtemp(prefix="resid_canary_", dir="/private/tmp")
    canary = "CANARY_fake_secret_for_residue_scan_selftest_000"
    p = os.path.join(tmp, "canary.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(canary)
    try:
        fake_live = {canary.encode(): "CANARY(not-a-real-secret)"}
        hits = scan(["/private/tmp"], fake_live)
        if hits.get(p):
            print("  ✅ canary 被命中 ⇒ 扫描器能发现明文（不是恒真）")
            return 0
        print("  ❌ canary 没被命中 ⇒ 扫描器恒绿、不可信")
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv):
    args = list(argv)
    if "--selftest" in args:
        args.remove("--selftest")
        if args:
            sys.stderr.write("用法错误：--selftest 不接受目录参数\n")
            return 2
        print("=== 残留扫描器自检 ===")
        return selftest()

    dirs = args or DEFAULT_SCAN_DIRS
    live = load_live_secrets()
    if not live:
        sys.stderr.write("⚠️ 没读到任何现役 secret（store 读失败或名字不匹配）"
                         " ⇒ 不能据此下「无残留」结论\n")
        return 2

    print("=== 密钥明文残留扫描 ===")
    print("扫描目录: %s" % ", ".join(dirs))
    print("监视凭据: %d 条（%s…）"
          % (len(live), ", ".join(sorted({n for n in live.values()})[:3])))
    hits = scan(dirs, live)
    if not hits:
        print("✅ 未发现现役明文残留")
        return 0
    print("⛔ 发现现役明文残留 %d 处：" % len(hits))
    for path, names in sorted(hits.items()):
        for n in sorted(names):
            # ★ 只报「哪个 secret 名 + 哪个路径」⇒ **不回显明文，也不回显指纹**
            print("  ❌ %s  ←  %s" % (path, n))
    print("\n⇒ 按 runbook §6 处理：先确认不是并发方在用 ⇒ 再清零内容 ⇒ 再删文件 ⇒ 再删目录")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
