#!/usr/bin/env python3
"""把新的飞书应用绑定成一个**独立账号**，路由到 Kimi 的测试 agent（木木）。

⚠️ 为什么不能走 `POST /api/openclaw/channels/feishu`：
   那个接口以 `tenant_id` 为 account_id 写入，本项目只有一个租户 `t_default`
   ⇒ 会**覆盖**现有「租户Bot → Ray」的账号凭据和 binding ⇒ 破坏线上。
   ⇒ 这里走 `openclaw config patch`（与 `bind_feishu_supervisor.py` 同一手法）：
     **新增**一个 account_id，bindings 全量读-改-写（幂等）。

用法：
    FEISHU_APP_SECRET=xxx python3 bind_feishu_mumu.py
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path.home() / ".openclaw" / "openclaw.json"
ACCOUNT_ID = "mumu"                 # openclaw 飞书账号 id
AGENT_ID = "emp_1787550091124_1048"  # 木木（OpenClaw 侧 model = kimi_proxy_mumu/k3）
ACCOUNT_NAME = "木木"
SECRET_NAME = "FEISHU_APPSECRET_MUMU"   # 命名跟随既有约定 FEISHU_APPSECRET_<ACCOUNT_ID>


def run(args, **kw):
    return subprocess.run(args, capture_output=True, text=True, **kw)


def main() -> int:
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id.startswith("cli_") or len(app_secret) < 8:
        sys.exit("ERR: 需要 FEISHU_APP_ID(cli_ 开头) 和 FEISHU_APP_SECRET(≥8)")

    # ── 读当前 bindings（canonical 来源，别自己猜）──
    r = run(["openclaw", "config", "get", "bindings"])
    if r.returncode != 0:
        sys.exit(f"ERR: 读 bindings 失败：{r.stderr.strip()[:200]}")
    bindings = json.loads(r.stdout)
    if not isinstance(bindings, list):
        sys.exit("ERR: bindings 不是数组")

    # ── 幂等：去掉同 accountId 的旧 binding，再加新的 ──
    kept = [b for b in bindings
            if not (isinstance(b, dict)
                    and b.get("match", {}).get("accountId") == ACCOUNT_ID)]
    kept.append({"agentId": AGENT_ID,
                 "match": {"channel": "feishu", "accountId": ACCOUNT_ID}})

    # ── 密钥进 secret store，配置里只放**引用** ──
    # ⚠️ 既有 3 个账号都是引用式：
    #     {"source":"store","provider":"default","id":"FEISHU_APPSECRET_<ID>"}
    #   明文写进 openclaw.json 与本项目的存储模型不一致（`openclaw secrets audit`
    #   会报 PLAINTEXT_FOUND），所以这里统一走 store。
    sec_file = None
    try:
        import tempfile
        fd, sec_file = tempfile.mkstemp(prefix="feishu_secret_")
        with os.fdopen(fd, "w") as fh:
            fh.write(app_secret)
        os.chmod(sec_file, 0o600)
        st = run(["openclaw", "secrets", "store", "set", SECRET_NAME,
                  "--kind", "secret", "--value-file", sec_file])
        if st.returncode != 0:
            sys.exit(f"ERR: 写 secret store 失败：{st.stderr.strip()[:200]}")
        print(f"[密钥] 已存入 secret store: {SECRET_NAME}")
    finally:
        if sec_file and os.path.exists(sec_file):
            os.remove(sec_file)

    secret_ref = {"source": "store", "provider": "default", "id": SECRET_NAME}

    # ── 备份 ──
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = CONFIG.parent / "backups" / f"openclaw.json.bak-feishu-{ACCOUNT_ID}-{ts}"
    bak.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CONFIG, bak)
    print(f"[备份] {bak}")

    patch = {
        "channels": {"feishu": {"accounts": {ACCOUNT_ID: {
            "appId": app_id, "appSecret": secret_ref,
            "name": ACCOUNT_NAME,
            # 测试账号：私聊放开（跟现有 t_default 一致），但**不进群**——
            # groupPolicy 保持 allowlist 且 groupAllowFrom 为空 ⇒ 不会被拉进任何群。
            "groupPolicy": "allowlist",
            "allowFrom": ["*"],
            "groupAllowFrom": [],
        }}}},
        "bindings": kept,
    }
    p = run(["openclaw", "config", "patch", "--stdin"], input=json.dumps(patch))
    if p.returncode != 0:
        print("PATCH FAILED:", p.stderr.strip()[:400])
        sys.exit(1)
    print("[patch] 已写入")

    # ── 验证 ──
    g = run(["openclaw", "config", "get", f"channels.feishu.accounts.{ACCOUNT_ID}.appId"])
    got = g.stdout.strip().strip('"')
    ok_id = got == app_id
    b = run(["openclaw", "config", "get", "bindings"])
    try:
        bl = json.loads(b.stdout)
    except json.JSONDecodeError:
        bl = []
    mine = [x for x in bl if isinstance(x, dict)
            and x.get("match", {}).get("accountId") == ACCOUNT_ID]
    ok_bind = len(mine) == 1 and mine[0].get("agentId") == AGENT_ID
    print(f"[验证] accounts.{ACCOUNT_ID}.appId = {got} ({'✅' if ok_id else '❌'})")
    print(f"[验证] binding feishu/{ACCOUNT_ID} → {AGENT_ID} ({'✅' if ok_bind else '❌'})")
    print(f"[验证] 账号总数 {len(bl)} 条 binding（原有 {len(bindings)} 条保留）")
    if not (ok_id and ok_bind):
        sys.exit(1)
    print("\n[下一步] 重载密钥并重启网关让新账号生效：")
    print("  openclaw secrets reload && openclaw daemon restart")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
