#!/usr/bin/env python3
"""视觉同源守卫 —— H5 驾驶舱 与 飞书 BaseApp 是不是还是「一套系统」。

背景（2026-10-22 老大反馈）：「两个设计风格没有一样，不然会以为是两个系统」。
H5 和飞书 App 展示的是**同一个达人（hlb725 洁洁爱户外）的同一套数据**，
但两边曾各写各的配色与文案：同一个「潜力型」H5 是绿底、飞书是紫底；
「🟢 能带」的飞书标签甚至是**红色**（emoji 与颜色反了）。

唯一事实来源：`daren_bot/verdict_ui.py`。本脚本**只读不写**，检查四处是否一致：

  1. 飞书 Base 4 个单选字段的 options.hue   == verdict_ui 期望
  2. 飞书 App 经营总览「本周一句话」text 块   == verdict_ui.verdict_line(最新评估结论)
  3. H5 index.html 的 VERDICT_EMOJI JS 映射   == verdict_ui 的 emoji（跨语言对拍）
  4. H5 CSS 的 .v-* 结论色类                  齐全

用法：
    python3 daren_bot/tools/verify_visual_sync.py            # 全查
    python3 daren_bot/tools/verify_visual_sync.py --offline  # 只查本地 H5（不调飞书）

退出码：0 = 全一致；1 = 有不一致（打印差异）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daren_bot import verdict_ui  # noqa: E402

LARK_CLI = "/opt/homebrew/bin/lark-cli"

BASE_TOKEN = "MZEdbVJMeah4x9sYYzncuhnbnnb"      # 达人看板样板
APP_TOKEN = "ZguOb2xCba10pVsRMlXcFmgenGd"        # 飞书 BaseApp「达人经营驾驶舱」
PAGE_OVERVIEW = "pgePJ3r25SknFVsg"               # 经营总览页
TABLE_PROFILE = "tblhJISXu8eGABEA"               # 账号档案
TABLE_PRODUCT = "tbl22zFZvVtqXZnc"               # 商品推荐记录
TABLE_COOP = "tblolFFeJTNfyInn"                  # 合作机会

H5_HTML = ROOT / "daren_cockpit" / "index.html"

# 飞书 Base 单选字段 → (表 id, 字段名, 期望 hue 字典)
# ⚠️ 期望字典的 key 是**飞书选项真名**：账号档案用裸词「潜力型」，
#    商品推荐记录用带 emoji 的「🟢 能带」（见 verdict_ui.VERDICT_UI[].feishu_option）。
EXPECT_HUE = {
    (TABLE_PROFILE, "评估结论"): {
        v["feishu_option"]: v["hue"] for k, v in verdict_ui.VERDICT_UI.items()
        if k in ("潜力型", "成长型", "稳健型", "成熟型", "风险型")
    },
    (TABLE_PROFILE, "最新快照"): verdict_ui.SNAPSHOT_HUE,
    (TABLE_PRODUCT, "结论"): {
        v["feishu_option"]: v["hue"] for k, v in verdict_ui.VERDICT_UI.items()
        if k in ("能带", "慎带", "别带")
    },
    (TABLE_COOP, "状态"): verdict_ui.COOP_STATUS_HUE,
}


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


def check_hue() -> list:
    """检查飞书 Base 单选字段的实际 hue。返回 [(检查名, 是否通过, 说明)]。"""
    out = []
    for (table_id, field_name), expect in EXPECT_HUE.items():
        d = _lark(["base", "+field-get", "--base-token", BASE_TOKEN,
                   "--table-id", table_id, "--field-id", field_name])
        if not d.get("ok"):
            out.append((f"飞书色 · {field_name}", False,
                        "读取失败: " + json.dumps(d.get("error"), ensure_ascii=False)[:160]))
            continue
        field = (d.get("data") or {}).get("field") or {}
        actual = {o.get("name"): o.get("hue") for o in (field.get("options") or [])}
        bad = {k: (v, actual.get(k)) for k, v in expect.items() if actual.get(k) != v}
        if bad:
            detail = "、".join(f"{k} 期望{v} 实际{actual.get(k)}" for k, (v, _) in bad.items())
            out.append((f"飞书色 · {field_name}", False, detail))
        else:
            out.append((f"飞书色 · {field_name}", True,
                        "、".join(f"{k}={actual[k]}" for k in expect)))
    return out


def _latest_verdict() -> str:
    """取账号档案表「最新快照 = 是」那行的评估结论。

    ⚠️ record-list --format json 的结构是 `data.fields`（列名顺序）+ `data.data`（二维数组），
    **不是** items=[{fields:{}}]。按 items 解析会拿到空 → 守卫永远报「读不到」。
    """
    d = _lark(["base", "+record-list", "--base-token", BASE_TOKEN,
               "--table-id", TABLE_PROFILE])
    if not d.get("ok"):
        return ""
    data = d.get("data") or {}
    fields = data.get("fields") or []
    rows = data.get("data") or []
    if "最新快照" not in fields or "评估结论" not in fields:
        return ""
    i_snap = fields.index("最新快照")
    i_ver = fields.index("评估结论")

    def flat(v):
        if isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("name") or v.get("text") or ""
        return str(v or "")

    for row in rows:
        if not isinstance(row, list) or len(row) <= max(i_snap, i_ver):
            continue
        if flat(row[i_snap]) == "是":
            return flat(row[i_ver])
    return ""


def check_weekly_line() -> list:
    """检查飞书 App「本周一句话」块的文本 == verdict_ui.verdict_line(最新评估结论)。"""
    verdict = _latest_verdict()
    if not verdict:
        return [("飞书 · 本周一句话", False, "读不到最新快照的评估结论（无法判定）")]
    expect = verdict_ui.verdict_line(verdict)

    d = _lark(["base", "+app-block-list", "--app-token", APP_TOKEN,
               "--page-id", PAGE_OVERVIEW])
    if not d.get("ok"):
        return [("飞书 · 本周一句话", False, "block-list 失败")]
    items = (d.get("data") or {}).get("items") or (d.get("data") or {}).get("blocks") or []
    if isinstance(items, dict):
        items = items.get("items", [])
    target = None
    for b in items:
        if b.get("name") == "本周一句话":
            target = b
            break
    if not target:
        return [("飞书 · 本周一句话", False, "经营总览页找不到「本周一句话」块")]

    bid = target.get("block_id") or target.get("widget_id")
    g = _lark(["base", "+app-block-get", "--app-token", APP_TOKEN,
               "--page-id", PAGE_OVERVIEW, "--block-id", bid])
    if not g.get("ok"):
        return [("飞书 · 本周一句话", False,
                 "块读取失败: " + json.dumps(g.get("error"), ensure_ascii=False)[:160])]
    blk = (g.get("data") or {}).get("block") or (g.get("data") or {})
    text = str((blk.get("data_config") or {}).get("text") or "")
    # 块文本 = "标题\n句子"（App 里只显示 2 行）
    body = text.split("\n", 1)[1].strip() if "\n" in text else text.strip()
    if body != expect:
        return [("飞书 · 本周一句话", False,
                 f"评估结论={verdict} 期望「{expect}」实际「{body}」")]
    return [("飞书 · 本周一句话", True, f"评估结论={verdict} → 「{body}」")]


def check_h5_emoji() -> list:
    """跨语言对拍：H5 的 JS VERDICT_EMOJI 映射 == verdict_ui 的 emoji。"""
    html = H5_HTML.read_text(encoding="utf-8")
    m = re.search(r"const\s+VERDICT_EMOJI\s*=\s*\{(.*?)\}\s*;", html, re.S)
    if not m:
        return [("H5 · emoji 映射", False, "index.html 里找不到 VERDICT_EMOJI 定义")]
    js = dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", m.group(1)))
    bad = []
    for verdict, item in verdict_ui.VERDICT_UI.items():
        want = item["emoji"]
        got = js.get(verdict)
        if got != want:
            bad.append(f"{verdict} 期望{want} 实际{got}")
    extra = set(js) - set(verdict_ui.VERDICT_UI)
    if extra:
        bad.append("H5 多出未登记结论: " + "、".join(sorted(extra)))
    if bad:
        return [("H5 · emoji 映射", False, "；".join(bad))]
    return [("H5 · emoji 映射", True, f"{len(js)} 个结论 emoji 一致")]


def check_h5_css() -> list:
    """H5 CSS 里每个结论都有对应的 .v-* 色类。"""
    html = H5_HTML.read_text(encoding="utf-8")
    missing = [v for v in verdict_ui.VERDICT_UI if f".v-{v}{{" not in html]
    if missing:
        return [("H5 · 结论色类", False, "缺 .v-* 类: " + "、".join(missing))]
    return [("H5 · 结论色类", True, f"{len(verdict_ui.VERDICT_UI)} 个结论色类齐全")]


def check_key_mapping() -> list:
    """飞书选项名 → 裸词 的归一化，Python 侧与 H5 JS 侧必须一致。

    这是 2026-10-22 那个掉底色 bug 的根因：商品结论选项名带 emoji（��🟢 能带」），
    H5 直接拿它拼 .v-* 类名 → 类不存在 → 标签没底色。
    """
    bad = []
    for k, v in verdict_ui.VERDICT_UI.items():
        got = verdict_ui.verdict_key(v["feishu_option"])
        if got != k:
            bad.append(f"{v['feishu_option']}→{got}，期望 {k}")
    html = H5_HTML.read_text(encoding="utf-8")
    if "function verdictKey" not in html:
        bad.append("index.html 里找不到 verdictKey()")
    if bad:
        return [("结论归一化 · 选项名→裸词", False, "；".join(bad))]
    return [("结论归一化 · 选项名→裸词", True,
             f"{len(verdict_ui.VERDICT_UI)} 个结论归一化一致")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="只查本地 H5，不调飞书")
    args = ap.parse_args()

    results = [check_h5_emoji(), check_h5_css(), check_key_mapping()]
    if not args.offline:
        results.insert(0, check_hue())
        results.append(check_weekly_line())

    print("🔍 视觉同源守卫（H5 驾驶舱 ↔ 飞书 BaseApp）")
    print("=" * 62)
    failed = 0
    for group in results:
        for name, ok, detail in group:
            print(f"  {'✅' if ok else '❌'} {name} — {detail}")
            if not ok:
                failed += 1
    print("=" * 62)
    if failed:
        print(f"❌ {failed} 项不一致 —— 两边已经漂成两个系统了，按 daren_bot/verdict_ui.py 对齐")
        return 1
    print("✅ 全部一致 —— H5 与飞书 App 是同一套系统")
    return 0


if __name__ == "__main__":
    sys.exit(main())
