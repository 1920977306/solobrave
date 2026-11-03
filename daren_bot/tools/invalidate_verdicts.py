#!/usr/bin/env python3
"""口径变更后失效活跃结论（fix/佣金口径 2026-11-03 的正规化）。

什么时候用：改了 PRODUCT_VERDICT_PROMPT 的判定口径（比如佣金权重、🟢/🟡/🔴
标准），想让达人之再问同一个商品时**按新口径重算**，而不是被 7 天 TTL
把旧口径的结论回放给她。

做什么（全部只动 invalidated_at 标记，不删行、不改 created_at——
bot_product_verdicts 是审计档案，「哪天判的、判了什么」必须完好）：
  1. 备份库（sqlite backup API，绝不 cp 活库）；
  2. 预览：TTL 内会被回放的活跃结论按租户列出来；
  3. --apply 才标记失效（幂等，重复跑不会重复标记）；
  4. 验：读路径视同过期、行保留。

用法：
  python3 daren_bot/tools/invalidate_verdicts.py              # 干跑，只看规模
  python3 daren_bot/tools/invalidate_verdicts.py --apply      # 真标记
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from daren_bot import config            # noqa: E402
from daren_bot import db                # noqa: E402


def _backup(db_path: str) -> str:
    bak = (db_path + ".bak-" + time.strftime("%Y%m%d-%H%M%S")
           + "-before-verdict-invalidate")
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(bak)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return bak


def _active_rows():
    cutoff = db.now_ts() - db.VERDICT_CACHE_TTL
    with db.db_ctx() as conn:
        return [(r["tenant_key"], int(r["n"])) for r in conn.execute(
            "SELECT tenant_key, COUNT(*) AS n FROM bot_product_verdicts "
            "WHERE created_at>=? AND invalidated_at=0 GROUP BY tenant_key",
            (cutoff,))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true",
                    help="真标记失效（默认干跑只预览）")
    args = ap.parse_args()

    db_path = str(config.DB_PATH)
    rows = _active_rows()
    total = sum(n for _, n in rows)
    print(f"库: {db_path}")
    print(f"TTL 内会被回放的活跃结论: {total} 条")
    for tk, n in rows:
        print(f"  {tk}: {n} 条")

    if not args.apply:
        print("\n干跑模式（未动任何数据）。确认规模无误后加 --apply 执行。")
        return 0
    if not rows:
        print("没有需要标记的活跃结论，收工。")
        return 0

    bak = _backup(db_path)
    print(f"\n已备份: {bak} ({os.path.getsize(bak)} bytes)")

    n = db.verdict_cache_invalidate_active()
    print(f"已标记失效: {n} 条")

    # 验：读路径视同过期 + 行保留
    with db.db_ctx() as conn:
        kept = conn.execute(
            "SELECT COUNT(*) FROM bot_product_verdicts").fetchone()[0]
        inv = conn.execute(
            "SELECT COUNT(*) FROM bot_product_verdicts "
            "WHERE invalidated_at>0").fetchone()[0]
    print(f"档案总行数 {kept}（未删一行），累计已标记 {inv}")
    assert n == total, f"标记数 {n} 与预览数 {total} 不一致，停下来人工查"
    print("✅ 完成：达人再问这些商品会按新口径重算，旧结论仍在档案里。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
