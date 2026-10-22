#!/usr/bin/env python3
"""画像摘要回填 —— 把「第一次采集就冻结」的旧摘要刷新成当前数据的真实摘要。

背景（2026-10-22 老大报「飞书里摘要和卡片数字打架」）：
飞书 Base「账号档案」里 hlb725 的两行记录，**「报告摘要」是同一份 10-16 的旧文本**
（写着「累计GMV¥10万-25万」），而同一行的「累计GMV」字段是 10-19 采集的新值 375000
（飞书卡片显示 37.5万）→ **同一页两个 GMV 数字互相矛盾**。

根因（已对库核实，勿再猜）：
1. `PUT /api/talents` 的 merge 是「老值非空 + 新值非空 → 保留老值」（老大硬约束：
   严禁覆盖核心数据）⇒ `notes`（画像摘要）**第一次采集后永远冻结**；
2. `sync_profile` 写飞书时取 `t.get("ai_summary") or t.get("notes")` ⇒ 飞书拿到的
   永远是那份冻结的旧文本；
3. 10-19 那次采集时 LLM 链还没有 MiniMax 套餐环（Kimi 429 / 开放平台 402 全灭），
   摘要压根没生成 —— 但即使生成了也会被第 1 条挡掉。

本工具做**重新派生**（不是造数据）：拿 talents 表里**当前真实的字段**，
走与采集链**完全相同**的 LLM 链和 prompt（`buyin_worker.summarize_for_notes`），
生成新摘要，然后写回两处：
  - `talents.notes`（源）—— 走 PUT + `__refresh_fields`（服务端白名单机制）
  - 飞书 Base「账号档案」最新快照行的「报告摘要」（H5 摘要也从这里读）

用法：
    python3 daren_bot/tools/refresh_profile_summary.py --douyin hlb725 --dry-run
    python3 daren_bot/tools/refresh_profile_summary.py --douyin hlb725
    python3 daren_bot/tools/refresh_profile_summary.py --douyin hlb725 --no-feishu

退出码：0 = 成功；1 = 失败。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

BASE_TOKEN = "MZEdbVJMeah4x9sYYzncuhnbnnb"      # 达人看板样板
TABLE_PROFILE = "tblhJISXu8eGABEA"               # 账号档案
LARK_CLI = "/opt/homebrew/bin/lark-cli"

# 不喂给 LLM 的噪声列（体积大 / 与画像无关 / 可能含密钥）
_SKIP_COLS = {
    "embedding", "embedding_model", "id", "created_by", "phone", "email",
    "wechat", "contact", "contact_name", "contact_phone", "contact_wechat",
    "contact_email", "products", "ocr_raw_fields", "matched_products",
    "top_products", "top_categories", "top_brands", "price_distribution",
    "category_distribution", "brand_details", "account_fans_profile",
    "video_fans_profile", "updated_at", "created_at",
}

# provenance 里的时间戳（采集链写「自动采集」，回填写「重新生成」）
PROV_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})")


def _lark(args: list, timeout: int = 60) -> dict:
    env = dict(os.environ)
    env["PATH"] = env.get("PATH", "") + ":/opt/homebrew/bin"
    out = subprocess.run([LARK_CLI] + args + ["--as", "user", "--format", "json"],
                         capture_output=True, text=True, timeout=timeout, env=env,
                         cwd=str(ROOT))
    txt = (out.stdout or "") + "\n" + (out.stderr or "")
    i = txt.find("{")
    if i < 0:
        return {"ok": False, "error": {"message": "lark-cli 无 JSON 输出: " + txt[:200]}}
    try:
        return json.loads(txt[i:])
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": {"message": f"JSON 解析失败: {e}"}}


def read_talent(douyin: str) -> dict:
    """读 talents 行（只读连接 + query_only，别用 mode=ro URI）。"""
    conn = sqlite3.connect(str(ROOT / "data" / "solobrave.db"), timeout=10)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM talents WHERE douyin_id = ?", (douyin,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def build_fields(t: dict) -> dict:
    """talents 行 → 喂给 summarize_for_notes 的结构化输入（去噪声、只留有值的）。"""
    out = {}
    for k, v in t.items():
        if k in _SKIP_COLS:
            continue
        if v is None or v == "" or v == 0 or v == "{}" or v == "[]":
            continue
        if isinstance(v, str) and len(v) > 2000:
            v = v[:2000]
        out[k] = v
    # ⚠️ total_gmv 常常是 0（平台只给档位文本「¥25万-50万」，没有精确值），
    #   0 被上面的「只留有值的」过滤掉 → 模型会说「结算额未提供」，
    #   摘要和飞书卡片的 37.5万（同一个档位中值）对不上。
    #   这里按**与飞书 Base 完全相同的口径**补出中值（fmt.gmv_mid_from_text 是唯一实现）。
    if not out.get("total_gmv") and t.get("total_gmv_text"):
        try:
            from daren_bot.fmt import gmv_mid_from_text
            mid = gmv_mid_from_text(t.get("total_gmv_text"))
            if mid:
                out["total_gmv"] = mid
        except Exception:  # noqa: BLE001
            pass
    # ⚠️ 粉丝数用「万」单位喂给模型（老大 2026-10-22 定调「1.3万这样好看点」）。
    #   原来喂 12804 → 摘要写「12804粉丝」，而飞书卡片写「1.3万」→ **同页两个写法**。
    try:
        from daren_bot.fmt import fmt_wan
        fans = t.get("followers")
        if fans:
            out["followers"] = fmt_wan(fans)      # '1.3万'
            out["粉丝"] = fmt_wan(fans)            # 中文 key，模型更容易用
    except Exception:  # noqa: BLE001
        pass
    return out


def refresh_talents_notes(douyin: str, notes: str) -> tuple:
    """通过 PUT /api/talents 写回 notes（走服务端的 __refresh_fields 白名单机制）。"""
    import buyin_worker as bw
    t = read_talent(douyin)
    tid = t.get("id")
    if not tid:
        return False, f"talents 里找不到 {douyin}"
    body = {"notes": notes, "__refresh_fields": ["notes"]}
    try:
        bw.api("PUT", f"/api/talents/{tid}", body, timeout=60, admin=True)
        return True, f"talents.notes 已更新（{tid}）"
    except Exception as e:  # noqa: BLE001
        return False, f"talents.notes 更新失败: {e}"


def push_feishu_summary(douyin: str, notes: str) -> tuple:
    """更新飞书 Base「账号档案」里最新快照=是 那行的「报告摘要」。"""
    d = _lark(["base", "+record-list", "--base-token", BASE_TOKEN,
               "--table-id", TABLE_PROFILE])
    if not d.get("ok"):
        return False, "record-list 失败: " + json.dumps(d.get("error"), ensure_ascii=False)[:150]
    data = d.get("data") or {}
    fields = data.get("fields") or []
    rows = data.get("data") or []
    rids = data.get("record_id_list") or []
    if not fields or not rows:
        return False, "record-list 返回空"
    try:
        i_dy = fields.index("抖音号")
        i_snap = fields.index("最新快照")
        i_sum = fields.index("报告摘要")
    except ValueError:
        return False, f"表里缺字段: {fields}"

    def flat(v):
        if isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("name") or v.get("text") or ""
        return str(v or "")

    target = None
    for idx, row in enumerate(rows):
        if not isinstance(row, list) or len(row) <= max(i_dy, i_snap):
            continue
        if flat(row[i_dy]) == douyin and flat(row[i_snap]) == "是":
            target = idx
            break
    if target is None:
        return False, f"表里找不到 {douyin} 的「最新快照=是」行"
    rid = rids[target] if target < len(rids) else ""
    if not rid:
        return False, "拿不到 record_id"

    up = _lark(["base", "+record-upsert", "--base-token", BASE_TOKEN,
                "--table-id", TABLE_PROFILE, "--record-id", rid,
                "--json", json.dumps({"报告摘要": notes}, ensure_ascii=False)])
    if not up.get("ok"):
        return False, "record-upsert 失败: " + json.dumps(up.get("error"), ensure_ascii=False)[:150]
    return True, f"飞书「报告摘要」已更新（{rid}）"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--douyin", required=True, help="抖音号，如 hlb725")
    ap.add_argument("--dry-run", action="store_true", help="只生成摘要，不写回")
    ap.add_argument("--no-feishu", action="store_true", help="不写飞书 Base")
    args = ap.parse_args()

    t = read_talent(args.douyin)
    if not t:
        print(f"❌ talents 表里没有 {args.douyin}")
        return 1
    print(f"📌 当前数据：{t.get('name')} | 粉丝 {t.get('followers')} | "
          f"GMV档位 {t.get('total_gmv_text')} | 评估 {t.get('risk_rating')}")
    old = str(t.get("notes") or "")
    print(f"   旧摘要（{len(old)} 字）：{old[:120]}{'…' if len(old) > 120 else ''}")

    import buyin_worker as bw
    fields = build_fields(t)
    print(f"   喂给 LLM 的字段数：{len(fields)}")
    try:
        summary = bw.summarize_for_notes(fields)
    except Exception as e:  # noqa: BLE001
        print(f"❌ 摘要生成失败: {e}")
        return 1
    if not summary.strip():
        print("❌ 摘要为空（LLM 链全灭）—— 不写回，避免把好数据冲成空")
        return 1

    prov = f"重新生成 {time.strftime('%Y-%m-%d %H:%M')}"
    new_notes = f"{summary.strip()}｜{prov}"
    print(f"   新摘要（{len(summary)} 字）：{summary.strip()[:200]}")
    print(f"   provenance：{prov}")

    if args.dry_run:
        print("🟡 dry-run，未写回")
        return 0

    ok, msg = refresh_talents_notes(args.douyin, new_notes)
    print(("  ✅ " if ok else "  ❌ ") + msg)
    if not ok:
        return 1

    if not args.no_feishu:
        ok2, msg2 = push_feishu_summary(args.douyin, new_notes)
        print(("  ✅ " if ok2 else "  ❌ ") + msg2)
        if not ok2:
            return 1

    print("✅ 回填完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
