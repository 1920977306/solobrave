#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只更新 daren_bot 租户的 app_secret_enc（最小入侵）。

为什么不用 `daren_bot.cli add-tenant`：
    它的 upsert 会**重置 bind_douyin_id**（且不碰 bitable 字段）⇒ 会误伤运营数据。
    本脚本只 UPDATE `app_secret_enc` + `updated_at`，其余列一个字节都不动。

纪律（AGENTS.md）：
- 断言先于写入 ⇒ 崩了不写坏文件
- 写后比行数 + 逐字校验其余列未变
- secret 只从 stdin 读，不进 argv、不落日志、不打印

用法：
    printf '%s' "$NEW_SECRET" | python3 scripts/rotate_daren_tenant_secret.py <app_id> <tenant_key>
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: printf '<secret>' | python3 %s <app_id> <tenant_key>"
              % Path(__file__).name, file=sys.stderr)
        return 2
    app_id, tenant_key = sys.argv[1], sys.argv[2]

    secret = sys.stdin.read().strip()
    if len(secret) != 32:
        print("✗ secret 长度应为 32，实际 %d ⇒ 拒绝写入" % len(secret), file=sys.stderr)
        return 3

    from daren_bot import crypto_util  # noqa: E402
    from daren_bot import config  # noqa: E402

    db_path = Path(config.DB_PATH)
    if not db_path.is_file():
        print("✗ 找不到 db：%s" % db_path, file=sys.stderr)
        return 2

    key = crypto_util.load_or_create_master_key()
    enc = crypto_util.encrypt_secret(key, secret)

    con = sqlite3.connect(str(db_path), timeout=30)
    con.row_factory = sqlite3.Row
    try:
        before = con.execute(
            "SELECT * FROM bot_tenants WHERE app_id=? AND tenant_key=?",
            (app_id, tenant_key)).fetchone()
        if before is None:
            print("✗ 找不到租户 app_id=%s tenant_key=%s ⇒ 不写入" % (app_id, tenant_key),
                  file=sys.stderr)
            return 4
        n_before = con.execute("SELECT COUNT(*) FROM bot_tenants").fetchone()[0]

        # --- 写前断言：解密旧值，确认当前密文可用（避免把坏状态固化）---
        try:
            crypto_util.decrypt_secret(key, before["app_secret_enc"])
        except Exception as e:
            print("✗ 旧密文无法解密（%s）⇒ 拒绝写入，先人工确认" % str(e)[:60], file=sys.stderr)
            return 5

        # --- 写入（只改两列）---
        cur = con.execute(
            "UPDATE bot_tenants SET app_secret_enc=?, updated_at=? WHERE app_id=? AND tenant_key=?",
            (enc, int(__import__("time").time()), app_id, tenant_key))
        if cur.rowcount != 1:
            print("✗ UPDATE 影响行数=%d（期望 1）⇒ 事务回滚" % cur.rowcount, file=sys.stderr)
            con.rollback()
            return 6
        con.commit()

        # --- 写后校验 ---
        after = con.execute(
            "SELECT * FROM bot_tenants WHERE app_id=? AND tenant_key=?",
            (app_id, tenant_key)).fetchone()
        n_after = con.execute("SELECT COUNT(*) FROM bot_tenants").fetchone()[0]
        if n_after != n_before:
            print("✗ 行数变了：%d → %d ⇒ 回滚" % (n_before, n_after), file=sys.stderr)
            con.rollback()
            return 7
        changed = [k for k in before.keys()
                   if before[k] != after[k] and k not in ("app_secret_enc", "updated_at")]
        if changed:
            print("✗ 非目标列被改动：%s ⇒ 回滚" % changed, file=sys.stderr)
            con.rollback()
            return 8
        # 解密验证新值确实是老大给的那把
        got = crypto_util.decrypt_secret(key, after["app_secret_enc"])
        if got != secret:
            print("✗ 解密回读与输入不一致 ⇒ 回滚", file=sys.stderr)
            con.rollback()
            return 9

        import hashlib
        fp = hashlib.md5(secret.encode()).hexdigest()[:12]
        print("✅ tenant=%s app_id=%s" % (tenant_key, app_id))
        print("   新 secret 指纹 md5[:12] = %s" % fp)
        print("   行数 %d → %d（不变）✔  非目标列零改动 ✔  解密回读一致 ✔" % (n_before, n_after))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
