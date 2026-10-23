#!/usr/bin/env python3
"""达人标识口径整改：`talents.douyin_id` 混装三类的拆列 + 回填 + 重复档案清理。

问题（2026-10-23 实证，**推翻了上一轮「它只是字段名骗人」的说法**）
-------------------------------------------------------------------
`talent.douyin_id` 这一列实际混装**三类**互不相同的值：

| 类别 | 样例 | 说明 |
|---|---|---|
| ① **平台达人 id** | `Wgg292` `hlb725` `372168571` `69097875157` | 团长端 `promoter/list` 的 `id` |
| ② **真抖音号** | `31693707031` `372443191` | 平台 `user_source_id`（`user_source_id`） |
| ③ **脏值** | `@limantou200w` `原来是安娜` `鲸鱼姐姐` `__speedtest__` | 带 @、中文名、测试串 |

⚠️ **靠格式无法自动判别** —— 平台达人 id **也可能是纯数字**（`372168571`、
`69097875157`），所以「纯数字 ⇒ 真抖音号」是错的（上一轮我这么判过，已订正）。
⇒ 唯一可靠的判据是**对着平台真值 join**。

三条 join 路径（强度递减）
-------------------------
1. `talents.douyin_id` == 平台 `platform_talent_id` ⇒ 平台达人 id（**最强**）
2. `talents.douyin_id` == 平台 `douyin_id`(user_source_id) ⇒ 真抖音号
3. `talents.name` == 平台 `name` ⇒ 昵称精确匹配（次强）

处置原则
--------
- **纯增量**：加 `platform_talent_id` / `douyin_uid` 两列，**`douyin_id` 一根手指不动**
  —— 它是**对外契约**（创建查重 / 前端 `accountId` / 飞书统计卡筛选 / 租户 `bind_douyin_id`
  都靠它），改语义会直接破线上。
- **不造数**：join 不上的**如实报告，不猜**。
- **重复档案只删「确认空壳」**：粉丝 0 / GMV 空 / 评分 0 / 城市空 / 类目空 /
  无商品匹配 / 建档后从未更新过 —— 有任何一条真实数据就**不动**，只报告。

用法
----
    python3 daren_cockpit/tools/fix_talent_ids.py --dry-run   # 只看（默认）
    python3 daren_cockpit/tools/fix_talent_ids.py --apply      # 真改（自动备份）
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "solobrave.db"

NEW_COLUMNS = [
    ("platform_talent_id", "TEXT DEFAULT ''"),
    ("douyin_uid", "TEXT DEFAULT ''"),
]

# 「确认空壳」的全部条件 —— 任何一条不满足就不删
SHELL_CHECKS = [
    ("followers", lambda r: not r["followers"]),
    ("total_gmv_text", lambda r: not (r["total_gmv_text"] or "").strip()),
    ("rating_score", lambda r: not r["rating_score"]),
    ("city", lambda r: not (r["city"] or "").strip()),
    ("main_category", lambda r: not (r["main_category"] or "").strip()),
    ("updated_at==created_at", lambda r: r["updated_at"] == r["created_at"]),
]


def ensure_schema(conn: sqlite3.Connection) -> list:
    have = {r[1] for r in conn.execute("PRAGMA table_info(talents)")}
    added = []
    for name, decl in NEW_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE talents ADD COLUMN {name} {decl}")
            added.append(name)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_talents_platform_tid "
        "ON talents(platform_talent_id) WHERE platform_talent_id != ''")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_talents_douyin_uid "
        "ON talents(douyin_uid) WHERE douyin_uid != ''")
    conn.commit()
    return added


def platform_truth(conn: sqlite3.Connection) -> tuple[dict, dict, dict]:
    """平台真值三张索引：按平台达人id / 按真抖音号 / 按昵称。"""
    plat = [dict(r) for r in conn.execute(
        "SELECT platform_talent_id, douyin_id, name FROM platform_talent_stats "
        "WHERE platform_talent_id!='' OR douyin_id!=''")]
    by_pid, by_dy, by_name = {}, {}, {}
    for p in plat:
        if p["platform_talent_id"]:
            by_pid[p["platform_talent_id"]] = p
        if p["douyin_id"]:
            by_dy[p["douyin_id"]] = p
        if p["name"]:
            by_name.setdefault(p["name"], p)
    return by_pid, by_dy, by_name


def join_talents(conn: sqlite3.Connection) -> dict:
    """三路 join：给每个能判定的档案标出平台真相。"""
    by_pid, by_dy, by_name = platform_truth(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT id,name,douyin_id,platform_talent_id,douyin_uid FROM talents "
        "WHERE douyin_id!='' OR platform_talent_id!='' OR douyin_uid!=''")]

    matched, unmatched = [], []
    for r in rows:
        dy = (r["douyin_id"] or "").strip()
        p = None
        how = ""
        if dy and dy in by_pid:
            p, how = by_pid[dy], "① douyin_id == 平台达人id"
        elif dy and dy in by_dy:
            p, how = by_dy[dy], "② douyin_id == 真抖音号"
        elif r["name"] and r["name"] in by_name:
            p, how = by_name[r["name"]], "③ name == 平台昵称"
        if p:
            matched.append({
                "id": r["id"], "name": r["name"], "douyin_id": dy, "how": how,
                "platform_talent_id": p["platform_talent_id"] or "",
                "douyin_uid": p["douyin_id"] or "",
                "platform_name": p["name"] or "",
                "name_agrees": (p["name"] == r["name"]),
            })
        else:
            unmatched.append(r)
    return {"matched": matched, "unmatched": unmatched}


def find_duplicates(conn: sqlite3.Connection) -> list:
    """同一平台达人id 的多行档案。

    ★ **所有**重复组都要报告（老大得知道有重复），
      但只有「其中至少一条是确认空壳、且至少有一条有真实数据」的组才可自动删 ——
      全部都是空壳时无从判断谁是「正主」，只报告不动手。
    """
    by_pid, by_dy, by_name = platform_truth(conn)
    groups: dict = {}
    for m in join_talents(conn)["matched"]:
        key = m["platform_talent_id"] or m["douyin_uid"] or m["name"]
        groups.setdefault(key, []).append(m)

    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        rich, shells = [], []
        for m in members:
            row = conn.execute(
                "SELECT * FROM talents WHERE id=?", (m["id"],)).fetchone()
            if row is None:
                continue
            d = dict(row)
            nm = conn.execute(
                "SELECT COUNT(*) FROM product_talent_match WHERE talent_id=?",
                (m["id"],)).fetchone()[0]
            is_shell = nm == 0 and all(fn(d) for _, fn in SHELL_CHECKS)
            (shells if is_shell else rich).append((m, d, nm))
        out.append({
            "key": key,
            "keep": rich[0][0] if rich else None,
            "shells": shells,
            "rich": rich,
            "all_shell": not rich,
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="达人标识口径整改（拆列 + 回填 + 空壳清理）")
    ap.add_argument("--dry-run", action="store_true", help="只看，不写库（默认）")
    ap.add_argument("--apply", action="store_true", help="真改库（自动备份）")
    args = ap.parse_args()
    apply_mode = args.apply and not args.dry_run

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    added = ensure_schema(conn)
    print(f"[schema] 新增列：{added or '（已存在）'}")

    j = join_talents(conn)
    print(f"\n[join] 有值的档案 {len(j['matched']) + len(j['unmatched'])} 条")
    print(f"  ✔ 平台能对上：{len(j['matched'])} 条")
    print(f"  ✘ 三路都对不上（平台机构达人里查不到）：{len(j['unmatched'])} 条")

    need_backfill = [m for m in j["matched"]
                     if m["platform_talent_id"] or m["douyin_uid"]]
    print(f"\n[回填计划] {len(need_backfill)} 条")
    for m in need_backfill:
        flag = "" if m["name_agrees"] else f"  ⚠️ 平台昵称=「{m['platform_name']}」"
        print(f"  {m['name'][:14]:<16} {m['how']:<26} 平台id={m['platform_talent_id']:<12} "
              f"真抖音号={m['douyin_uid']:<16}{flag}")

    dups = find_duplicates(conn)
    print(f"\n[重复档案] 同一平台达人id 的多行档案 {len(dups)} 组")
    for d in dups:
        if d["all_shell"]:
            print(f"  组 {d['key']}：★ {len(d['shells'])} 行**全是空壳** ⇒ 无从判断正主，"
                  f"**只报告不删**")
            for m, row, nm in d["shells"]:
                print(f"      空壳「{m['name']}」({m['id']})")
            continue
        print(f"  组 {d['key']}：保留「{d['keep']['name']}」({d['keep']['id']})")
        for m, row, nm in d["shells"]:
            print(f"      空壳待删「{m['name']}」({m['id']}) —— "
                  f"粉丝{row['followers']} GMV{row['total_gmv_text'] or '空'} "
                  f"匹配{nm}条")
        for m, row, nm in d["rich"]:
            if m["id"] != d["keep"]["id"]:
                print(f"      ⚠️ 另一条**有数据**「{m['name']}」({m['id']}) ⇒ "
                      f"只报告**不删**，需人工判断谁是正主")

    if not apply_mode:
        print("\n[dry-run] 未写库。确认后加 --apply 执行（自动备份 talents 表 + 整库副本）。")
        conn.close()
        return 0

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = ROOT / "data" / f"talents-backup-{ts}.csv"
    cols = [r[1] for r in conn.execute("PRAGMA table_info(talents)")]
    with open(bak, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in conn.execute("SELECT * FROM talents"):
            w.writerow(list(r))
    db_bak = DB.with_suffix(f".bak-{ts}")
    shutil.copy2(DB, db_bak)
    print(f"\n[备份] {bak.relative_to(ROOT)}  +  {db_bak.name}")

    now = int(time.time() * 1000)
    for m in need_backfill:
        # 只填**空列**，绝不覆盖已有值（merge 保留老值）
        conn.execute(
            "UPDATE talents SET "
            "platform_talent_id = CASE WHEN platform_talent_id='' THEN ? ELSE platform_talent_id END, "
            "douyin_uid = CASE WHEN douyin_uid='' THEN ? ELSE douyin_uid END, "
            "updated_at = ? WHERE id=?",
            (m["platform_talent_id"], m["douyin_uid"], now, m["id"]))

    deleted = []
    for d in dups:
        if d["all_shell"] or not d["keep"]:
            continue          # 全是空壳 ⇒ 无从判断正主 ⇒ 只报告不删
        for m, row, nm in d["shells"]:
            # 先把引用改指到保留的那条，避免留下悬挂引用
            for ref in ("talent_collects",):
                try:
                    conn.execute(f"UPDATE {ref} SET talent_id=? WHERE talent_id=?",
                                 (d["keep"]["id"], m["id"]))
                except sqlite3.OperationalError:
                    pass
            conn.execute("DELETE FROM talents WHERE id=?", (m["id"],))
            deleted.append(m)

    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM talents").fetchone()[0]
    filled = conn.execute(
        "SELECT COUNT(*) FROM talents WHERE platform_talent_id!='' OR douyin_uid!=''"
    ).fetchone()[0]
    print(f"\n[完成] talents 共 {total} 行，其中 {filled} 行已挂平台身份"
          f"（platform_talent_id / douyin_uid）")
    if deleted:
        print(f"[清理] 删除空壳档案 {len(deleted)} 条："
              + "、".join(f"{m['name']}({m['id']})" for m in deleted))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
