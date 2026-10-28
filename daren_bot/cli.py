"""本地运维 CLI —— 凭证/额度的本机入口（安全约束：App Secret 不进聊天、不进 shell history）。

老大在 Mac 终端自己执行：
    cd /Users/qichen/solobrave-prod
    .venv-daren/bin/python -m daren_bot.cli add-tenant daren_boss cli_xxxxxxxxxxxx

secret 用 getpass 隐藏输入（终端不回显、不进 history），直接 AES-GCM 落库。
"""
import argparse
import getpass
import sys

from . import config, crypto_util, db


def _mask(s: str) -> str:
    return s[:6] + "..." + s[-4:] if len(s) > 12 else s[:2] + "..."


def cmd_add_tenant(args) -> None:
    db.init_db()
    secret = getpass.getpass("App Secret (输入不回显): ").strip()
    if not secret:
        print("ABORT: secret 为空", file=sys.stderr)
        sys.exit(1)
    confirm = getpass.getpass("再输一遍确认: ").strip()
    if secret != confirm:
        print("ABORT: 两次输入不一致", file=sys.stderr)
        sys.exit(1)
    role = getattr(args, "role", None) or config.ROLE_DAREN
    douyin_id = ""
    if role == config.ROLE_DAREN:
        douyin_id = input("绑定抖音号(可回车跳过, 之后发消息时自动绑定): ").strip()

    key = crypto_util.load_or_create_master_key()
    enc = crypto_util.encrypt_secret(key, secret)
    db.add_tenant(args.app_id, args.tenant_key, enc,
                  bind_douyin_id=douyin_id, role=role)
    print(f"OK tenant={args.tenant_key} role={role} app_id={_mask(args.app_id)} "
          f"bind_douyin_id={douyin_id or '(未绑定)'} 初始积分={config.INITIAL_CREDITS}")
    print("下一步: daren-bot 服务每 5s 自动收敛新租户（无需重启），"
          "client 在线后去飞书后台切「使用长连接接收事件」保存。")


def cmd_list(_args) -> None:
    db.init_db()
    tenants = db.get_active_tenants()
    if not tenants:
        print("(无租户, 先 add-tenant)")
        return
    for t in tenants:
        bal = db.get_balance(t["tenant_key"])
        print(f"{t['tenant_key']:20s} role={t.get('role') or 'daren':10s} "
              f"app_id={_mask(t['app_id'])} "
              f"bind={t['bind_douyin_id'] or '-':15s} balance={bal} status={t['status']}")


def cmd_grant(args) -> None:
    """运营手动发放积分（spec 第 7 章, 同 SoloBrave 签到发放范式）。"""
    db.init_db()
    t = db.get_tenant(args.tenant_key)
    if not t:
        print(f"ABORT: 租户不存在 {args.tenant_key}", file=sys.stderr)
        sys.exit(1)
    from .db import db_ctx, _ledger, now_ts
    with db_ctx() as conn:
        r = conn.execute("SELECT balance FROM bot_credits_accounts WHERE tenant_key=?",
                         (args.tenant_key,)).fetchone()
        balance = int(r["balance"]) if r else 0
        new_balance = balance + args.amount
        conn.execute(
            "INSERT INTO bot_credits_accounts (tenant_key, balance, updated_at) VALUES (?,?,?) "
            "ON CONFLICT(tenant_key) DO UPDATE SET balance=excluded.balance, "
            "updated_at=excluded.updated_at",
            (args.tenant_key, new_balance, now_ts()))
        _ledger(conn, args.tenant_key, "grant", -args.amount, new_balance)
    print(f"OK granted {args.amount} to {args.tenant_key}, balance {balance} -> {new_balance}")


def main():
    p = argparse.ArgumentParser(prog="daren_bot.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    at = sub.add_parser("add-tenant", help="交互式凭证入库(secret 不回显)")
    at.add_argument("tenant_key")
    at.add_argument("app_id")
    at.add_argument("--role", choices=(config.ROLE_DAREN, config.ROLE_COMMERCE),
                    default=config.ROLE_DAREN,
                    help="助理角色：daren=达人助理 commerce=商务助理")
    sub.add_parser("list", help="租户一览(secret 脱敏)")
    g = sub.add_parser("grant", help="手动发放积分")
    g.add_argument("tenant_key")
    g.add_argument("amount", type=int)
    args = p.parse_args()
    {"add-tenant": cmd_add_tenant, "list": cmd_list, "grant": cmd_grant}[args.cmd](args)


if __name__ == "__main__":
    main()
