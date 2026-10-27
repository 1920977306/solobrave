#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探针：实测「飞书回复形态（已读/卡片 vs 纯文本）」到底由谁决定。

背景（2026-10-27）
------------------
老大问：「为什么木木可以已读，回复是卡片形式，跟之前 kimi 弄的不一样？」

本探针用**代码事实**回答「形态差异来自哪一层」，不依赖任何 UI 观察：
1. openclaw 飞书插件（@openclaw/feishu）出站用 ``client.im.message.reply``（**回复**语义）
   ⇒ 飞书客户端会把被回复的那条消息标记为「已读」，且卡片消息带回复锚点。
2. daren_bot（本地服务）出站用 ``client.im.v1.message.create``（**新建**语义）
   ⇒ 直接在会话里新建一条消息，不产生「已读」标记。

因此形态差异来自**发送端 API 语义**，与 LLM provider 无关。

用法
----
    python3 scripts/probe_feishu_reply_mode.py          # 打印实测结论
    python3 scripts/probe_feishu_reply_mode.py --json   # 机器可读

退出码
------
    0 = 两侧发送端语义已确认（reply vs create）
    1 = 未能同时确认两侧（例如插件路径变了 / daren_bot 被改写）⇒ 结论不成立

「探针必须能失败」：如果 openclaw 插件路径不存在或 daren_bot 不再走 create，
本脚本会以退出码 1 明确失败，而不是打一串日志后假装成功。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# openclaw 飞书插件的安装位置（多候选，按序探测）
PLUGIN_GLOBS = [
    "~/.openclaw/npm/projects/*/node_modules/@openclaw/feishu",
    "~/.openclaw/extensions/*/node_modules/@openclaw/feishu",
    "/opt/homebrew/lib/node_modules/openclaw/node_modules/@openclaw/feishu",
]

# daren_bot 发送端实现文件
DAREN_SENDER_REL = "daren_bot/ws_manager.py"


def _first_existing(patterns: list[str]) -> Path | None:
    import glob

    for pat in patterns:
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return Path(hits[0])
    return None


def probe_openclaw() -> dict:
    """在 openclaw 飞书插件里找出发送端 API 语义。"""
    root = _first_existing(PLUGIN_GLOBS)
    if root is None:
        return {
            "found": False,
            "reason": "未找到 @openclaw/feishu 插件目录（探测过 %d 个候选路径）" % len(PLUGIN_GLOBS),
        }

    # 只扫 dist 下的 .mjs（插件的实际运行代码在 dist/）
    dist = root / "dist"
    if not dist.is_dir():
        return {"found": False, "reason": "插件目录缺少 dist/: %s" % root}

    reply_hits: list[str] = []
    create_hits: list[str] = []
    read_event_hits: list[str] = []

    for path in dist.rglob("*.mjs"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(root))
        # client.im.message.reply(...) —— 回复语义（产生「已读」标记）
        if re.search(r"client\.im\.message\.reply\s*\(", text):
            reply_hits.append(rel)
        # client.im.message.create(...) —— 新建语义（不产生「已读」标记）
        if re.search(r"client\.im\.message\.create\s*\(", text):
            create_hits.append(rel)
        # 已读回执事件订阅（飞书侧「已读」能力开关之一）
        if "im.message.message_read_v1" in text:
            read_event_hits.append(rel)

    if not reply_hits:
        return {
            "found": False,
            "reason": "插件里找不到 client.im.message.reply(...) 调用（形态判定依据消失）",
            "plugin_root": str(root),
        }

    return {
        "found": True,
        "plugin_root": str(root),
        "reply_api_files": sorted(set(reply_hits))[:5],
        "create_api_files": sorted(set(create_hits))[:5],
        "read_receipt_event_files": sorted(set(read_event_hits))[:5],
        "semantics": "reply",
        "has_read_receipt_event": bool(read_event_hits),
    }


def probe_daren_bot() -> dict:
    """在 daren_bot 里找出发送端 API 语义。"""
    path = REPO_ROOT / DAREN_SENDER_REL
    if not path.is_file():
        return {"found": False, "reason": "找不到 %s" % DAREN_SENDER_REL}

    text = path.read_text(encoding="utf-8", errors="replace")

    create_hits = re.findall(r"\.im\.v1\.message\.create\s*\(", text)
    reply_hits = re.findall(r"\.im\.v1\.message\.reply\s*\(", text)

    if not create_hits:
        return {
            "found": False,
            "reason": "%s 里找不到 message.create 调用（发送端被改写，结论需重测）" % DAREN_SENDER_REL,
        }

    # 取一条真实调用上下文，证明是「新建到 chat_id」而不是「回复某条消息」
    idx = text.find(".im.v1.message.create(")
    ctx = text[max(0, idx - 420): idx + 120]
    ctx_lines = [ln.strip() for ln in ctx.splitlines() if ln.strip()]
    return {
        "found": True,
        "file": DAREN_SENDER_REL,
        "create_call_count": len(create_hits),
        "reply_call_count": len(reply_hits),
        "semantics": "create",
        "receive_id_type": "chat_id" if 'receive_id_type("chat_id")' in ctx else None,
        "msg_types": sorted(set(re.findall(r'\.msg_type\("([a-z]+)"\)', text))),
        "evidence_tail": ctx_lines[-8:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="实测飞书回复形态由发送端 API 语义决定")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    oc = probe_openclaw()
    db = probe_daren_bot()
    result = {"openclaw": oc, "daren_bot": db}

    ok = bool(oc.get("found")) and bool(db.get("found"))
    result["conclusion"] = (
        "形态差异来自发送端 API 语义："
        "openclaw 用 message.reply（回复语义 ⇒ 飞书标记「已读」）"
        "；daren_bot 用 message.create（新建语义 ⇒ 直接回复、无「已读」）。"
        "与 LLM provider 无关。"
        if ok
        else "两侧语义未能同时确认 ⇒ 本结论不成立，需重新取证。"
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=" * 72)
        print("飞书回复形态探针（已读 / 卡片 vs 纯文本）")
        print("=" * 72)
        print("\n[1] openclaw 飞书插件（发送端 = %s）" % ("reply" if ok else "?"))
        if oc.get("found"):
            print("    插件路径 : %s" % oc["plugin_root"])
            print("    reply 调用文件 : %s" % ", ".join(oc["reply_api_files"]))
            if oc["create_api_files"]:
                print("    create 调用文件: %s（fallback 直发路径）" % ", ".join(oc["create_api_files"]))
            print("    已读回执事件   : %s" % (
                "有 im.message.message_read_v1" if oc["has_read_receipt_event"] else "无"))
        else:
            print("    ✗ %s" % oc.get("reason"))

        print("\n[2] daren_bot（发送端 = %s）" % ("create" if ok else "?"))
        if db.get("found"):
            print("    文件     : %s" % db["file"])
            print("    create 调用数 : %d   reply 调用数 : %d"
                  % (db["create_call_count"], db["reply_call_count"]))
            print("    receive_id_type: %s" % db.get("receive_id_type"))
            print("    msg_type : %s" % ", ".join(db.get("msg_types") or []) or "(无)")
            for ln in db.get("evidence_tail", [])[-4:]:
                print("      | %s" % ln[:150])
        else:
            print("    ✗ %s" % db.get("reason"))

        print("\n" + "-" * 72)
        print("结论：%s" % result["conclusion"])
        print("-" * 72)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
