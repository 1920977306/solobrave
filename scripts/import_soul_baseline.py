#!/usr/bin/env python3
"""
SOUL.md 版本快照 · 存量基线导入（2026-11-11，派单一点 5）

背景：
  - solobrave-server 新增 SOUL 快照机制：每次覆盖 SOUL.md 前把旧内容存进
    <workspace>/soul_history/（含三行元信息头：version/updated_at/updated_by）
  - 机制只对「机制上线之后」的变更生效；上线前的存量 SOUL.md 没有任何历史
  - 本脚本把存量 SOUL.md 导入为 v1 基线快照（updated_by=baseline_import），
    并给现 SOUL.md 头部盖上 v1 元信息（与机制后续写入的格式一致）

幂等：
  - 已有快照的 workspace 跳过（不重复导入、不覆盖）
  - 无 SOUL.md 的 workspace 跳过
  - 默认 dry-run，--apply 才落盘

用法:
  python3 scripts/import_soul_baseline.py            # 只看，不写
  python3 scripts/import_soul_baseline.py --apply    # 真导入
"""

import argparse
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def load_server():
    """加载 solobrave-server 模块（复用其 _resolve_agent_workspace / _soul_* helper）"""
    spec = importlib.util.spec_from_file_location(
        'solobrave_server_soulbase', os.path.join(ROOT, 'solobrave-server.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_meta(text):
    return re.sub(r'<!--\s*soul_.*?-->\n?', '', text or '', flags=re.S)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真落盘（默认 dry-run）')
    args = ap.parse_args()

    srv = load_server()
    agents = srv._load_agents()
    print(f'agents.json 共 {len(agents)} 个 agent')

    plan, skipped = [], []
    for a in agents:
        aid = a.get('id')
        try:
            ws = srv._resolve_agent_workspace(aid, a)
        except Exception as e:  # 解析失败的如实报，不中断
            skipped.append((aid, f'workspace 解析失败: {e}'))
            continue
        soul = os.path.join(ws, 'SOUL.md')
        if not os.path.exists(soul):
            skipped.append((aid, f'无 SOUL.md（{ws}）'))
            continue
        snaps = srv._soul_snapshots(ws)
        if snaps:
            skipped.append((aid, f'已有 {len(snaps)} 份快照，跳过（不重复导入）'))
            continue
        with open(soul, encoding='utf-8') as f:
            body = strip_meta(f.read())
        if not body.strip():
            skipped.append((aid, 'SOUL.md 为空，跳过'))
            continue
        plan.append((aid, ws, body))

    print(f'\n=== 待导入 {len(plan)} 个 ===')
    for aid, ws, _ in plan:
        print(f'  {aid:30s} {ws}')

    print(f'\n=== 跳过 {len(skipped)} 个 ===')
    for aid, why in skipped:
        print(f'  {aid:30s} {why}')

    if not args.apply:
        print('\n(dry-run，未写盘。加 --apply 真导入)')
        return

    for aid, ws, body in plan:
        hist = srv._soul_history_dir(ws)
        os.makedirs(hist, exist_ok=True)
        ts = srv.datetime.now().strftime('%Y%m%d-%H%M%S')
        # 多个 workspace 同秒导入会撞文件名 ⇒ 文件名带 agent id 区分
        snap_path = os.path.join(hist, f'{ts}-{aid}.md')
        with open(snap_path, 'w', encoding='utf-8') as f:
            f.write(srv._soul_meta_header(1, 'baseline_import') + body)
        # 现 SOUL.md 盖 v1 元信息头（内容不动；机制的去重/比对都剥元信息，无影响）
        soul_path = os.path.join(ws, 'SOUL.md')
        with open(soul_path, 'w', encoding='utf-8') as f:
            f.write(srv._soul_meta_header(1, 'baseline_import') + body)
        print(f'  ✅ {aid}: v1 基线 → {snap_path}')

    print(f'\n完成：导入 {len(plan)} 个基线快照。')


if __name__ == '__main__':
    main()
