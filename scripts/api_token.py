#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scoped API token 管理 CLI —— 写类 API 开放前置三件套之一。

token 格式（sbk_ + 48 hex）、存储（data/api_tokens.json，只存 sha256）与校验逻辑
全部归属 solobrave-server.py（_api_token_* 族）。本脚本只是复用同一份实现做
签发/列举/吊销，保证 CLI 与服务端**永远不会出现两套判据**。

用法（在仓库根目录运行）：
  python3 scripts/api_token.py create --name mumu-ro --user-id emp_xxx --scopes read,write
  python3 scripts/api_token.py list
  python3 scripts/api_token.py revoke --name mumu-ro

注意：明文 token 只在 create 时打印一次，务必当场复制；库里只有 sha256。
"""
import argparse
import importlib.util
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_spec = importlib.util.spec_from_file_location('solobrave_server', os.path.join(ROOT, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(srv)


def cmd_create(args):
    scopes = [s.strip() for s in (args.scopes or 'read').split(',') if s.strip()]
    bad = [s for s in scopes if s not in ('read', 'write', 'admin')]
    if bad:
        print(f'非法 scope: {bad}（只允许 read/write/admin）', file=sys.stderr)
        return 2
    raw, rec = srv._api_token_mint(args.name, args.user_id, tuple(scopes))
    print(f'✅ 已签发: name={rec["name"]!r} user_id={rec["user_id"]!r} scopes={rec["scopes"]}')
    print()
    print('  明文 token（仅此一次显示，请立即复制保存）：')
    print(f'    {raw}')
    print()
    print('  使用: curl -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/...')
    return 0


def cmd_list(_args):
    store = srv._api_token_load()
    if not store:
        print('（库为空）')
        return 0
    print(f'{"name":<20} {"user_id":<24} {"scopes":<20} {"revoked":<8} {"last_used_at"}')
    for _h, rec in sorted(store.items(), key=lambda kv: str((kv[1] or {}).get('created_at', 0))):
        if not isinstance(rec, dict):
            continue
        lu = rec.get('last_used_at', 0)
        lu_s = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(lu)) if lu else '从未'
        print(f'{rec.get("name", ""):<20} {rec.get("user_id", ""):<24} '
              f'{",".join(rec.get("scopes") or []):<20} {str(bool(rec.get("revoked"))):<8} {lu_s}')
    return 0


def cmd_revoke(args):
    n = srv._api_token_revoke(args.name)
    print(f'{"✅ 已吊销" if n else "⚠️  未找到未吊销记录"}: name={args.name!r}（{n} 条）')
    return 0 if n else 1


def main():
    p = argparse.ArgumentParser(description='Scoped API token 管理（数据归属 solobrave-server.py）')
    sub = p.add_subparsers(dest='cmd', required=True)

    c = sub.add_parser('create', help='签发新 token（明文只显示一次）')
    c.add_argument('--name', required=True, help='显示名（审计用，如 mumu-ro）')
    c.add_argument('--user-id', required=True, help='绑定的用户 ID（仅作数据归属，不会放大权限）')
    c.add_argument('--scopes', default='read', help='逗号分隔：read/write/admin（默认 read）')
    c.set_defaults(fn=cmd_create)

    l = sub.add_parser('list', help='列出全部 token（不显示 hash）')
    l.set_defaults(fn=cmd_list)

    r = sub.add_parser('revoke', help='按显示名吊销')
    r.add_argument('--name', required=True)
    r.set_defaults(fn=cmd_revoke)

    args = p.parse_args()
    return args.fn(args)


if __name__ == '__main__':
    sys.exit(main())
