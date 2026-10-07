#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Phase 0 / M1 租户底座引导迁移（幂等，可重跑）。

做的事：
1. 建 data/tenants.json 注册表，登记默认租户 t_default（存量全部数据归属它）
2. users.json / agents.json / teams.json / groups.json 每条记录补 tenant_id：
   - 普通记录 → t_default
   - 系统级记录（agents 里的 knowledge_admin 等 createdBy 缺失者）→ 'platform'
   - role='admin' 的用户仍归 t_default（他是默认租户的管理员；平台超管由 role 表达，M1 不拆户）
3. 预生成 localhost 内部通道签名密钥 data/certs/internal_secret（0600；已存在则不动）

每次写入前留 .bak.tenant-<时间戳> 备份。全程不改业务库 solobrave.db（那是 M2 的事）。

用法：
  python3 scripts/migrate_tenant_bootstrap.py            # 项目 data/
  python3 scripts/migrate_tenant_bootstrap.py --data-dir /path/to/data
"""
import argparse, json, os, shutil, sys, time

DEFAULT_TENANT_ID = 't_default'
PLATFORM_TENANT_ID = 'platform'

def log(msg):
    print(f'[migrate-tenant] {msg}', flush=True)

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        log(f'!! {path} 解析失败: {e}')
        sys.exit(1)

def backup_and_write(path, data, ts):
    if os.path.exists(path):
        shutil.copy2(path, f'{path}.bak.tenant-{ts}')
    tmp = path + f'.tmp.migrate'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    log(f'  已写 {path}（备份 .bak.tenant-{ts}）')

def ensure_tenants_file(data_dir, ts):
    path = os.path.join(data_dir, 'tenants.json')
    tenants = load_json(path, [])
    if isinstance(tenants, dict):
        tenants = tenants.get('tenants', [])
    if any(t.get('id') == DEFAULT_TENANT_ID for t in tenants if isinstance(t, dict)):
        log(f'  tenants.json 已有 {DEFAULT_TENANT_ID}，跳过')
        return False
    tenants.append({
        'id': DEFAULT_TENANT_ID,
        'name': '默认租户（存量数据）',
        'plan': 'legacy',
        'status': 'active',
        'createdAt': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'migratedFrom': 'single-tenant',
    })
    backup_and_write(path, tenants, ts)
    return True

def ensure_tenant_id_field(data_dir, fname, ts, platform_rule=None):
    """给 JSON list 每条记录补 tenant_id。返回 (改动条数, 总数)。"""
    path = os.path.join(data_dir, fname)
    items = load_json(path, [])
    if not isinstance(items, list):
        log(f'  {fname} 非 list（{type(items).__name__}），跳过')
        return 0, 0
    changed = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get('tenant_id'):
            continue
        it['tenant_id'] = platform_rule(it) if platform_rule else DEFAULT_TENANT_ID
        changed += 1
    if changed:
        backup_and_write(path, items, ts)
    return changed, len(items)

def ensure_internal_secret(data_dir):
    import secrets as _secrets
    cert_dir = os.path.join(data_dir, 'certs')
    os.makedirs(cert_dir, exist_ok=True)
    path = os.path.join(cert_dir, 'internal_secret')
    if os.path.exists(path) and open(path).read().strip():
        log('  内部签名密钥已存在，跳过')
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(_secrets.token_hex(32))
    log(f'  已生成内部签名密钥 {path}（0600）')

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))
    args = ap.parse_args()
    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        log(f'!! data 目录不存在: {data_dir}')
        sys.exit(1)
    ts = time.strftime('%Y%m%d-%H%M%S')
    log(f'目标 data 目录: {data_dir}')

    ensure_tenants_file(data_dir, ts)
    c1, n1 = ensure_tenant_id_field(data_dir, 'users.json', ts)
    log(f'  users.json: {c1}/{n1} 条补 tenant_id')
    c2, n2 = ensure_tenant_id_field(
        data_dir, 'agents.json', ts,
        platform_rule=lambda a: PLATFORM_TENANT_ID if not a.get('createdBy') else DEFAULT_TENANT_ID)
    log(f'  agents.json: {c2}/{n2} 条补 tenant_id（无 createdBy 的系统代理归 {PLATFORM_TENANT_ID}）')
    c3, n3 = ensure_tenant_id_field(data_dir, 'teams.json', ts)
    log(f'  teams.json: {c3}/{n3} 条补 tenant_id')
    c4, n4 = ensure_tenant_id_field(data_dir, 'groups.json', ts)
    log(f'  groups.json: {c4}/{n4} 条补 tenant_id')
    ensure_internal_secret(data_dir)

    # 幂等自检：重跑一遍应零改动
    log('自检（二次扫描，应全为 0/...）：')
    c1b, _ = ensure_tenant_id_field(data_dir, 'users.json', ts)
    c2b, _ = ensure_tenant_id_field(data_dir, 'agents.json', ts, platform_rule=lambda a: PLATFORM_TENANT_ID if not a.get('createdBy') else DEFAULT_TENANT_ID)
    c3b, _ = ensure_tenant_id_field(data_dir, 'teams.json', ts)
    c4b, _ = ensure_tenant_id_field(data_dir, 'groups.json', ts)
    if c1b or c2b or c3b or c4b:
        log(f'!! 幂等自检失败: {c1b}/{c2b}/{c3b}/{c4b}')
        sys.exit(1)
    log('完成 ✅')

if __name__ == '__main__':
    main()
