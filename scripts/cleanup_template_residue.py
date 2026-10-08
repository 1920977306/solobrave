#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模板残留清洗（emp_001 / {empId} 字面量目录）。多租户遗留清扫。

背景（phase0 调研 docs/phase0-tenant-isolation.md 遗留项）:
  data/ 下存在模板时代遗留目录（当时员工 ID 还叫 emp_001，以及一个
  从未被替换的 {empId} 字面量目录）:
    data/messages/emp_001/      v1 消息存储（chatlog.json），现消息走 sqlite + data/chats/
    data/knowledge/emp_001/     v1 知识库存储（docs.json 4 篇），现知识走 knowledge_base_new 表
    data/knowledge/{empId}/     占位模板空目录（0 docs）
    data/memories/emp_001/      v3 记忆 6 core + 8 daily + 6 archived（demo 数据）

代码侧验证（清洗前 grep 全仓）:
  - 0 个 chatlog.json / knowledge/*/docs.json 读取方（solobrave-server.py / memory_service_v3.py）
  - sqlite knowledge / memory / group_messages 表对 emp_001 与 '{{empId}}' 引用均 0
  - agents.json 无 emp_001 员工；BrainScheduler 迁移有 _validate_emp_id 兜底跳过
  - 前端 js/inline-*.js / index.html 0 命中

行为:
  1. 运行时安全闸：agents.json 若出现 emp_001 立即退出（防误删真员工数据）
  2. 打包残留 → data/backups/residue-cleanup-<ts>.tar.gz（可回滚）
  3. 删除原目录；父目录 messages/ knowledge/ 若因此清空则一并移除
     （memories/ 下还有真实员工目录与 consolidation_log.json，不动父级）

幂等：目标不存在 → 跳过记 SKIP，重复执行安全。

用法:
  python3 scripts/cleanup_template_residue.py               # 预演（dry-run，只打印不动）
  python3 scripts/cleanup_template_residue.py --execute     # 真删（先打包备份）
  python3 scripts/cleanup_template_residue.py --data-dir /path/to/data
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

# 残留目标（data 目录下相对路径）。{empId} 是字面量目录名。
RESIDUE_TARGETS = [
    os.path.join('messages', 'emp_001'),
    os.path.join('knowledge', 'emp_001'),
    os.path.join('knowledge', '{empId}'),
    os.path.join('memories', 'emp_001'),
]

# 父目录若清空则一并移除（仅这两个纯残留父级；memories/ 有真实数据不列）
EMPTY_PARENT_PRUNE = ['messages', 'knowledge']


def log(m):
    print(f'[residue-cleanup] {m}', flush=True)


def load_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data'))
    ap.add_argument('--execute', action='store_true', help='真删（默认 dry-run，只打印）')
    args = ap.parse_args()
    data = os.path.abspath(args.data_dir)
    log(f'数据目录: {data}  模式: {"EXECUTE" if args.execute else "DRY-RUN"}')

    # ---- 安全闸 1: data 目录必须像真的（有 agents.json）----
    agents_path = os.path.join(data, 'agents.json')
    if not os.path.isfile(agents_path):
        log(f'!! {agents_path} 不存在，拒绝在陌生目录操作')
        sys.exit(1)

    # ---- 安全闸 2: emp_001 不得是真实员工 ----
    agents = load_json(agents_path, [])
    agent_ids = {a.get('id') for a in agents if isinstance(a, dict)}
    if 'emp_001' in agent_ids:
        log('!! agents.json 中存在 emp_001 员工，拒绝清洗（防误删真数据）')
        sys.exit(1)
    log(f'安全闸通过: agents.json {len(agent_ids)} 个员工，无 emp_001')

    # ---- 收集现存目标 ----
    found = [t for t in RESIDUE_TARGETS if os.path.isdir(os.path.join(data, t))]
    missing = [t for t in RESIDUE_TARGETS if not os.path.isdir(os.path.join(data, t))]
    for t in found:
        size = subprocess.run(['du', '-sk', os.path.join(data, t)],
                              capture_output=True, text=True).stdout.split()[0]
        log(f'TARGET  {t}  ({int(size) // 1024} MB)')
    for t in missing:
        log(f'SKIP    {t}  (不存在)')

    if not found:
        log('无残留目标，收工')
        return

    if not args.execute:
        log(f'dry-run 完毕: {len(found)} 个目标待清理。加 --execute 真删（会先打包备份）')
        return

    # ---- 打包备份（可回滚）----
    os.makedirs(os.path.join(data, 'backups'), exist_ok=True)
    ts = time.strftime('%Y%m%d_%H%M%S')
    tarname = f'residue-cleanup-{ts}.tar.gz'
    tarpath = os.path.join(data, 'backups', tarname)
    rc = subprocess.run(['tar', '-czf', tarpath, '-C', data] + found,
                        capture_output=True, text=True)
    if rc.returncode != 0:
        log(f'!! 打包失败: {rc.stderr.strip()}，中止（不动数据）')
        sys.exit(1)
    log(f'已备份: backups/{tarname}')

    # ---- 删除 ----
    for t in found:
        shutil.rmtree(os.path.join(data, t), ignore_errors=False)
        log(f'DELETED {t}')

    # ---- 空父级剪枝 ----
    for parent in EMPTY_PARENT_PRUNE:
        pdir = os.path.join(data, parent)
        if os.path.isdir(pdir) and not os.listdir(pdir):
            os.rmdir(pdir)
            log(f'PRUNED  空父目录 {parent}/')

    log(f'完成: 清理 {len(found)} 个残留目标，备份在 backups/{tarname}')


if __name__ == '__main__':
    main()
