#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_decisions 历史回填（2026-11-07 沉没成本回收）

背景：soft_delete 192 次 0 条理由（状态变更 ≠ 学习样本）。
docs/KB-DELETED-REVIEW.md 清单由老大人工补理由后，用本脚本落库。

输入 TSV（默认 data/kb-deleted-reasons.tsv，列）：
  entry_id <TAB> reason_code(1-6 或英文码) <TAB> 备注(可空)

reason_code 映射（与 knowledge_service.REJECT_REASON_CODES 对齐）：
  1=duplicate 2=stale 3=inaccurate 4=out_of_scope 5=unclear 6=other

行为：
  - 默认 dry-run：只打印将写入什么，不写库
  - --apply 才落 kb_decisions（decision='rejected'，confidence='sure'，
    content_snapshot 取删前全文——软删行还在库里，原文读得到）
  - 跳过：条目不存在 / 理由码非法 / 该行已回填过（幂等）
用法：python3 scripts/backfill_kb_decisions.py [--apply] [--tsv path]
"""
import os
import sqlite3
import sys

REASON_MAP = {
    '1': 'duplicate', '2': 'stale', '3': 'inaccurate',
    '4': 'out_of_scope', '5': 'unclear', '6': 'other',
    'duplicate': 'duplicate', 'stale': 'stale', 'inaccurate': 'inaccurate',
    'out_of_scope': 'out_of_scope', 'unclear': 'unclear', 'other': 'other',
}

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'solobrave.db')
DEFAULT_TSV = os.path.join(ROOT, 'data', 'kb-deleted-reasons.tsv')


def main():
    apply = '--apply' in sys.argv
    tsv = DEFAULT_TSV
    if '--tsv' in sys.argv:
        tsv = sys.argv[sys.argv.index('--tsv') + 1]
    if not os.path.isfile(tsv):
        print(f'❌ TSV 不存在: {tsv}\n   先按 docs/KB-DELETED-REVIEW.md 填理由，存成该路径（entry_id\\t理由码\\t备注）')
        sys.exit(1)

    conn = sqlite3.connect(DB)
    try:
        done = 0
        skipped = []
        with open(tsv, encoding='utf-8') as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.rstrip('\n')
                if not line.strip():
                    continue
                parts = line.split('\t')
                entry_id = parts[0].strip()
                code_raw = parts[1].strip() if len(parts) > 1 else ''
                note = parts[2].strip() if len(parts) > 2 else ''
                reason = REASON_MAP.get(code_raw)
                if not entry_id or not reason:
                    skipped.append((lineno, entry_id or '(空)', '理由码非法或为空'))
                    continue
                row = conn.execute(
                    'SELECT title, content, category, scope FROM kb_entries WHERE id = ?',
                    (entry_id,)).fetchone()
                if not row:
                    skipped.append((lineno, entry_id, '条目不存在（可能已物理清理）'))
                    continue
                dup = conn.execute(
                    "SELECT 1 FROM kb_decisions WHERE entry_id = ? AND decision = 'rejected'",
                    (entry_id,)).fetchone()
                if dup:
                    skipped.append((lineno, entry_id, '已回填过（幂等跳过）'))
                    continue
                title, content, category, scope = row
                if apply:
                    import json
                    import time
                    import uuid
                    now = int(time.time() * 1000)
                    snapshot = json.dumps(
                        {'title': title or '', 'category': category or '',
                         'scope': scope or 'global', 'content_head': (content or '')[:500]},
                        ensure_ascii=False)
                    conn.execute(
                        'INSERT INTO kb_decisions (id, entry_id, entry_snapshot, content_snapshot, '
                        'decision, reason_code, confidence, reason_note, merged_into, decided_by, created_at) '
                        'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (f'kbd_{now}_{uuid.uuid4().hex[:8]}', entry_id, snapshot,
                         content or '', 'rejected', reason, 'sure', note,
                         '', 'manual-backfill-20261107', now))
                done += 1
                print(f'  {"✅" if apply else "·"} {entry_id}  {reason}  {note}')
        if apply:
            conn.commit()
        print(f'\n{"已写入" if apply else "将写入（dry-run，加 --apply 才落库）"}: {done} 条；跳过: {len(skipped)} 条')
        for lineno, eid, why in skipped:
            print(f'  跳过 line{lineno} {eid}: {why}')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
