#!/usr/bin/env python3
"""
飞书自建应用绑主管 channel —— SoloBrave 任务3 收尾脚本

前置(必须人工完成, 2026-09-30):
  1) open.feishu.cn 开发者后台创建「企业自建应用」(如命名 "SoloBrave 主管")
  2) 权限: im:message / im:message.p2p_msg / im:message.group_at_msg
  3) 事件订阅: 长连接方式, 订阅 im.message.receive_v1
  4) 创建版本并发布(企业自建应用管理员审核通过)

本脚本: 把新 App ID/Secret 写进 OpenClaw channels.feishu.accounts.zhuguan,
并加 binding 让该账号的消息路由到 sb_supervisor (主管)。

用法:
  FEISHU_APP_ID=cli_xxx FEISHU_APP_SECRET=xxx python3 bind_feishu_supervisor.py
  python3 bind_feishu_supervisor.py --app-id cli_xxx --app-secret xxx

幂等: 可重复执行; 写前自动备份 openclaw.json。
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OPENCLAW_CONFIG = Path.home() / ".openclaw" / "openclaw.json"
ACCOUNT_ID = "zhuguan"
AGENT_ID = "sb_supervisor"


def _args():
    app_id = ""
    app_secret = ""
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == "--app-id" and i + 1 < len(argv):
            app_id = argv[i + 1].strip()
        elif a == "--app-secret" and i + 1 < len(argv):
            app_secret = argv[i + 1].strip()
    import os
    app_id = app_id or os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "").strip()
    return app_id, app_secret


def main() -> None:
    app_id, app_secret = _args()
    if not app_id.startswith("cli_") or len(app_secret) < 8:
        sys.exit("ERR: 需要有效的 --app-id (cli_ 开头) 和 --app-secret (≥8 字符)\n"
                 "      去 open.feishu.cn 建自建应用后拿到凭证再来")

    bak = OPENCLAW_CONFIG.parent / f"openclaw.json.bak.feishu-zhuguan.{datetime.now():%Y%m%d_%H%M%S}"
    bak.write_bytes(OPENCLAW_CONFIG.read_bytes())
    print(f"Backup → {bak.name}")

    patch = {
        "channels": {
            "feishu": {
                "accounts": {
                    ACCOUNT_ID: {
                        "appId": app_id,
                        "appSecret": app_secret,
                        "name": "主管",
                        "groupPolicy": "open",
                        "allowFrom": ["*"],
                        "groupAllowFrom": ["*"],
                    }
                }
            }
        },
        "bindings": [
            {"agentId": AGENT_ID, "match": {"channel": "feishu", "accountId": ACCOUNT_ID}},
        ],
    }
    proc = subprocess.run(
        ["openclaw", "config", "patch", "--stdin"],
        input=json.dumps(patch), text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print("PATCH FAILED:", proc.stderr.strip())
        sys.exit(1)
    print(proc.stdout.strip())

    # 验证
    get = subprocess.run(
        ["openclaw", "config", "get", f"channels.feishu.accounts.{ACCOUNT_ID}.appId"],
        capture_output=True, text=True,
    )
    got = get.stdout.strip()
    print(f"\n=== verify ===")
    print(f"  accounts.{ACCOUNT_ID}.appId = {got} ({'✅' if got == app_id else '❌'})")
    bl = subprocess.run(["openclaw", "config", "get", "bindings"], capture_output=True, text=True)
    ok_bind = f'"accountId": "{ACCOUNT_ID}"' in bl.stdout or f'"accountId":"{ACCOUNT_ID}"' in bl.stdout
    print(f"  binding feishu/{ACCOUNT_ID} → {AGENT_ID}: {'✅' if ok_bind else '❌'}")
    if got == app_id and ok_bind:
        print("\n绑定完成。飞书里给「主管」机器人发消息即可走 supervisor 调用链。")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
