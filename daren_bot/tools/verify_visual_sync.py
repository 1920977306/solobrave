#!/usr/bin/env python3
"""视觉守卫 —— H5 驾驶舱的结论配色/文案是否与唯一事实来源一致。

历史（2026-10-22）：当时有**两张脸**——H5 驾驶舱 + 飞书 BaseApp「达人经营驾驶
舱」——两边曾各写各的配色与文案（同一个「潜力型」H5 绿底、飞书紫底；「🟢 能带」
飞书标签甚至是红色）。本脚本就是为守「两张脸不漂」而生的。

★ 2026-11-02 飞书 BaseApp 已下线（老大拍板「只留 H5」）：写入链路 10-27 就停了，
   它冻结在旧数据上——达人看到过期数字比没有更糟。两张脸砍成一张，
   「同源对齐」问题从根上消失，本脚本只守 H5 一张脸：
   飞书侧检查（hue/本周一句话/摘要新鲜度）已随下线移除。
   若未来要在飞书侧重做仪表盘，先恢复飞书检查，别重蹈两张脸漂移的覆辙。

唯一事实来源：`daren_bot/verdict_ui.py`。本脚本**只读不写**，检查三项：

  1. H5 index.html 的 VERDICT_EMOJI JS 映射   == verdict_ui 的 emoji（跨语言对拍）
  2. H5 CSS 的 .v-* 结论色类                  齐全
  3. 结论归一化 verdictKey()                  Python 侧与 JS 侧一致

用法：
    python3 daren_bot/tools/verify_visual_sync.py            # 全查（现等同本地查）

退出码：0 = 全一致；1 = 有不一致（打印差异）。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daren_bot import verdict_ui  # noqa: E402

H5_HTML = ROOT / "daren_cockpit" / "index.html"


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
    """结论归一化：verdict_key() Python 侧与 H5 JS 侧必须一致。

    这是 2026-10-22 那个掉底色 bug 的根因（当时商品结论选项名带 emoji，
    H5 直接拿它拼 .v-* 类名 → 类不存在 → 标签没底色）。飞书端下线后
    feishu_option 仍保留在 verdict_ui 里——那是历史选项名的解析口径，
    H5 数据若再带旧格式选项名仍要靠它归一，不许删。
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
        return [("结论归一化", False, "；".join(bad))]
    return [("结论归一化", True,
             f"{len(verdict_ui.VERDICT_UI)} 个结论归一化一致")]


def main() -> int:
    ap = argparse.ArgumentParser()
    # 兼容旧调用：--offline 曾是「只查本地 H5」——飞书侧下线后本地检查就是全部，
    # 该旗标变成无操作，但旧脚本/文档里的调用不能因此报错。
    ap.add_argument("--offline", action="store_true",
                    help="已废弃（无操作）：飞书侧检查已随 BaseApp 下线移除")
    ap.parse_args()

    results = [check_h5_emoji(), check_h5_css(), check_key_mapping()]

    print("🔍 视觉守卫（H5 驾驶舱 ↔ verdict_ui 唯一事实来源）")
    print("=" * 62)
    failed = 0
    for group in results:
        for name, ok, detail in group:
            print(f"  {'✅' if ok else '❌'} {name} — {detail}")
            if not ok:
                failed += 1
    print("=" * 62)
    if failed:
        print(f"❌ {failed} 项不一致 —— 按 daren_bot/verdict_ui.py 对齐")
        return 1
    print("✅ 全部一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
