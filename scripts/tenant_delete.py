#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删租户（物理清除 + 平台侧清扫）。M5 配套。

清除顺序（先收 id，后删数据，最后清平台记录）：
1. 拒绝 t_default / platform（兜底防误删存量）
2. 平台 JSON 收号：tenant 的 users + agents + groups
3. 租户业务库（data/tenants/<tid>/）在删库前查出 talent/product 真实 id 清单
4. sweep 全局遗留目录（老租户/agent 可能写过全局路径）:
   data/chats/<agent>.json* / data/memory|memories|messages|knowledge/<agent>/ / data/influencers/<id 前缀>/
   data/matches/match_cache.json 按 talent/product id 过滤
5. rm -rf data/tenants/<tid>/（库 + 租户目录全在这）
6. 平台侧删除 users/agents/groups 的 tenant_id 记录；tenants.json 状态置 deleted（留审计, --purge 才物理删）

用法:
  python3 scripts/tenant_delete.py t_xxx            # 预演（dry-run, 只打印不动）
  python3 scripts/tenant_delete.py t_xxx --execute  # 真删
  python3 scripts/tenant_delete.py t_xxx --execute --purge
"""
import argparse, json, os, shutil, sys, time, glob

DEFAULT_TENANT_ID = 't_default'
PLATFORM_TENANT_ID = 'platform'

def log(m): print(f'[tenant-delete] {m}', flush=True)

def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('tenant_id')
    ap.add_argument('--data-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))
    ap.add_argument('--execute', action='store_true', help='真删（默认 dry-run）')
    ap.add_argument('--purge', action='store_true', help='连 tenants.json 记录也物理删（默认留 deleted 审计）')
    args = ap.parse_args()
    tid = args.tenant_id
    data = os.path.abspath(args.data_dir)

    if tid in (DEFAULT_TENANT_ID, PLATFORM_TENANT_ID):
        log(f'!! 拒绝删除保留租户: {tid}')
        sys.exit(1)
    tenants = load_json(os.path.join(data, 'tenants.json'), [])
    trec = next((t for t in tenants if isinstance(t, dict) and t.get('id') == tid), None)
    if not trec:
        log(f'!! 注册表无此租户: {tid}')
        sys.exit(1)
    log(f'目标租户: {tid} ({trec.get("name")}) 模式: {"EXECUTE" if args.execute else "DRY-RUN"}')

    users = load_json(os.path.join(data, 'users.json'), [])
    agents = load_json(os.path.join(data, 'agents.json'), [])
    groups = load_json(os.path.join(data, 'groups.json'), [])
    t_users = [u for u in users if isinstance(u, dict) and u.get('tenant_id') == tid]
    # 双口径收号: tenant_id 章 + createdBy ∈ 租户用户（防 M5 前的存量员工没盖章漏收）
    t_agents = [a for a in agents if isinstance(a, dict)
                and (a.get('tenant_id') == tid
                     or a.get('createdBy') in {u.get('id') for u in t_users})]
    t_groups = [g for g in groups if isinstance(g, dict) and g.get('tenant_id') == tid]
    uids = {u.get('id') for u in t_users}
    aids = {a.get('id') for a in t_agents}
    log(f'平台侧: {len(t_users)} 用户 / {len(t_agents)} 员工 / {len(t_groups)} 群组')

    # 租户库真实业务 id（删库前收）
    talent_ids, product_ids = set(), set()
    dbp = os.path.join(data, 'tenants', tid, 'solobrave.db')
    if os.path.exists(dbp):
        import sqlite3
        c = sqlite3.connect(dbp)
        try:
            talent_ids = {r[0] for r in c.execute('SELECT id FROM talents')}
            product_ids = {r[0] for r in c.execute('SELECT id FROM products')}
        except Exception as e:
            log(f'  (租户库读取部分失败, 按已收 id 继续: {e})')
        finally:
            c.close()
        # ★ 扣除默认库 id: init_db 给每个新库种同一批 demo 种子(tal_dapeishi_w 等),
        #   不扣除会把默认租户的全局 influencers 文件误删
        default_db = os.path.join(data, 'solobrave.db')
        if os.path.exists(default_db):
            dc = sqlite3.connect(default_db)
            try:
                d_talents = {r[0] for r in dc.execute('SELECT id FROM talents')}
                d_products = {r[0] for r in dc.execute('SELECT id FROM products')}
                talent_ids -= d_talents
                product_ids -= d_products
            except Exception:
                pass
            finally:
                dc.close()
        log(f'租户库(扣默认种子后): {len(talent_ids)} 达人 / {len(product_ids)} 商品')

    # sweep 计划
    plan = []  # (描述, 路径列表)
    sweep_ids = aids | uids
    for base, pattern in [('chats', '{id}.json*'), ('memory', '{id}*'), ('memories', '{id}*'),
                          ('messages', '{id}*'), ('knowledge', '{id}*')]:
        hits = []
        for i in sweep_ids:
            hits += glob.glob(os.path.join(data, base, pattern.format(id=i)))
        if hits:
            plan.append((f'{base}/ ({len(hits)} 项)', hits))
    # influencers: 目录按达人 id 建子目录的, 前缀匹配
    inf_hits = []
    for i in talent_ids:
        inf_hits += glob.glob(os.path.join(data, 'influencers', i + '*'))
    if inf_hits:
        plan.append((f'influencers/ ({len(inf_hits)} 项)', inf_hits))
    # talent_reports
    rep_hits = []
    for i in talent_ids:
        rep_hits += glob.glob(os.path.join(data, 'talent_reports', i + '*'))
    if rep_hits:
        plan.append((f'talent_reports/ ({len(rep_hits)} 项)', rep_hits))
    # match_cache 过滤
    mc_path = os.path.join(data, 'matches', 'match_cache.json')
    mc = load_json(mc_path, [])
    mc_left = mc
    if isinstance(mc, list) and mc and (talent_ids or product_ids):
        def _belongs(e):
            s = json.dumps(e, ensure_ascii=False)
            return any(i in s for i in talent_ids | product_ids)
        removed = [e for e in mc if _belongs(e)]
        mc_left = [e for e in mc if not _belongs(e)]
        if removed:
            plan.append((f'match_cache 条目 ({len(removed)} 条)', ['__match_cache__']))

    tenant_dir = os.path.join(data, 'tenants', tid)
    if os.path.exists(tenant_dir):
        plan.append((f' tenants/{tid}/ 整目录', [tenant_dir]))

    if not plan:
        log('没有需要清除的物理数据（可能未 provision 或未使用）')
    for desc, paths in plan:
        log(f'  将删 {desc}')
        for p in paths[:5]:
            log(f'      - {p}')
        if len(paths) > 5:
            log(f'      ... 等 {len(paths)} 项')

    if not args.execute:
        log('DRY-RUN 结束。加 --execute 真删。')
        return

    ts = time.strftime('%Y%m%d-%H%M%S')
    for desc, paths in plan:
        for p in paths:
            if p == '__match_cache__':
                continue
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
            except Exception as e:
                log(f'  !! 删除失败 {p}: {e}')
    if isinstance(mc, list) and mc_left != mc:
        shutil.copy2(mc_path, f'{mc_path}.bak.delete-{ts}')
        with open(mc_path, 'w', encoding='utf-8') as f:
            json.dump(mc_left, f, ensure_ascii=False, indent=2)
        log(f'  match_cache 已过滤并备份 .bak.delete-{ts}')

    # 网关摘路由（best effort: openclaw 不在也继续删库）
    try:
        import subprocess as _sp
        get = _sp.run(['openclaw', 'config', 'get', 'bindings'],
                      capture_output=True, text=True, timeout=15)
        if get.returncode == 0:
            bindings = json.loads(get.stdout)
            if isinstance(bindings, list):
                kept = [b for b in bindings
                        if not (isinstance(b, dict) and b.get('match', {}).get('accountId') == tid)]
                if len(kept) != len(bindings):
                    patch = {'channels': {'feishu': {'accounts': {tid: {'appId': '', 'appSecret': ''}}}},
                             'bindings': kept}
                    r = _sp.run(['openclaw', 'config', 'patch', '--stdin'],
                                input=json.dumps(patch), text=True, capture_output=True, timeout=20)
                    log(f'  网关摘路由: {"OK" if r.returncode == 0 else r.stderr.strip()[:100]}')
    except Exception as e:
        log(f'  网关摘路由跳过（best effort）: {str(e)[:80]}')

    # 平台侧记录
    def _rewrite(fname, items, keep_pred):
        path = os.path.join(data, fname)
        kept = [x for x in items if keep_pred(x)]
        if len(kept) != len(items):
            shutil.copy2(path, f'{path}.bak.delete-{ts}')
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(kept, f, ensure_ascii=False, indent=2)
            log(f'  {fname}: 删 {len(items)-len(kept)} 留 {len(kept)}')

    _rewrite('users.json', users, lambda u: not (isinstance(u, dict) and u.get('tenant_id') == tid))
    # 员工双口径删除, 与上方收号逻辑严格同构（tenant_id 章 或 createdBy ∈ 被删用户）
    deleted_uids = {u.get('id') for u in t_users}
    _rewrite('agents.json', agents,
              lambda a: not (isinstance(a, dict) and (a.get('tenant_id') == tid or a.get('createdBy') in deleted_uids)))
    _rewrite('groups.json', groups, lambda g: not (isinstance(g, dict) and g.get('tenant_id') == tid))

    tpath = os.path.join(data, 'tenants.json')
    if args.purge:
        tenants = [t for t in tenants if not (isinstance(t, dict) and t.get('id') == tid)]
    else:
        for t in tenants:
            if isinstance(t, dict) and t.get('id') == tid:
                t['status'] = 'deleted'
                t['deletedAt'] = time.strftime('%Y-%m-%dT%H:%M:%S')
    shutil.copy2(tpath, f'{tpath}.bak.delete-{ts}')
    with open(tpath, 'w', encoding='utf-8') as f:
        json.dump(tenants, f, ensure_ascii=False, indent=2)
    log(f'完成 ✅ tenants.json 已{"物理删除记录" if args.purge else "标记 deleted（留审计）"}')

if __name__ == '__main__':
    main()
