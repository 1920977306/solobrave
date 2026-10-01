# -*- coding: utf-8 -*-
"""
P0-3 fix/p03-real-fix: 清掉"针织开衫"商品 ai_analysis 字段里的旧匹配缓存

诊断报告 (p03_diagnosis_20261001.md) §2 根因 5 说:
  GET /api/products/:id/matches 旧缓存接口会把 matched_influencers 写回 ai_analysis,
  24h TTL。prod_1787133848854_32582b ("白色勾花镂空中长款针织拉链开衫外套...") 在
  2026-10-01 03:41 UTC 写入 117 条 matched_influencers 缓存。

老大指令: 清掉这段缓存, **不动 ai_analysis 其他字段** (ai_score / competition_analysis / selection_advice)。

执行:
  只 UPDATE 这一条商品的 ai_analysis.matched_influencers + matched_influencers_updated_at,
  其他字段保持原样。操作前打印现状、操作后打印 diff + 备份原值到 /tmp/p03_knit_cardigan_backup.json。
"""
import json
import os
import sqlite3
import sys
import time

PROD_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
PRODUCT_NAME_PATTERN = '%针织%开衫%'  # 老大原话: "商品名为针织开衫"
BACKUP_PATH = '/tmp/p03_knit_cardigan_backup.json'


def main():
    if not os.path.isfile(PROD_DB):
        print(f'ERROR: prod DB not found: {PROD_DB}')
        sys.exit(1)

    conn = sqlite3.connect(PROD_DB)
    try:
        # 1) 找出"针织开衫"商品
        rows = conn.execute(
            "SELECT id, name, ai_analysis FROM products "
            "WHERE name LIKE ?",
            (PRODUCT_NAME_PATTERN,)
        ).fetchall()

        if not rows:
            print(f'INFO: 没有匹配 name LIKE "{PRODUCT_NAME_PATTERN}" 的商品, 跳过清理')
            return

        print(f'=== 找到 {len(rows)} 条"针织开衫"商品 ===')
        backups = []
        for pid, name, ai_text in rows:
            try:
                ai = json.loads(ai_text) if ai_text else {}
            except Exception as e:
                print(f'WARN: {pid} ai_analysis 解析失败, 跳过 ({e})')
                continue

            mi_count = len(ai.get('matched_influencers', [])) if isinstance(ai.get('matched_influencers'), list) else 0
            mi_ts = ai.get('matched_influencers_updated_at', 0)
            print(f'  [{pid}] {name[:50]}...')
            print(f'    ai_analysis keys: {sorted(ai.keys())}')
            print(f'    matched_influencers: {mi_count} 条')
            print(f'    matched_influencers_updated_at: {mi_ts} ({time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mi_ts/1000)) if mi_ts else "n/a"})')
            print(f'    ai_score: {ai.get("ai_score", "n/a")}')
            print(f'    competition_analysis: {(ai.get("competition_analysis") or "")[:60]}...')
            print(f'    selection_advice: {(ai.get("selection_advice") or "")[:60]}...')

            backups.append({
                'product_id': pid,
                'product_name': name,
                'before_ai_analysis': ai,
            })

        if not any(b['before_ai_analysis'].get('matched_influencers') for b in backups):
            print('\\nINFO: 没有 matched_influencers 缓存需要清理')
            return

        # 2) 备份原值到 /tmp
        with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
            json.dump(backups, f, ensure_ascii=False, indent=2)
        print(f'\\n[备份] 原 ai_analysis 已保存到 {BACKUP_PATH}')

        # 3) UPDATE: 只清 matched_influencers + matched_influencers_updated_at, 保留其他字段
        now_ms = int(time.time() * 1000)
        cleaned = 0
        for b in backups:
            pid = b['product_id']
            ai = dict(b['before_ai_analysis'])  # 拷贝, 不污染 backup
            if 'matched_influencers' in ai:
                ai['matched_influencers'] = []
            if 'matched_influencers_updated_at' in ai:
                ai['matched_influencers_updated_at'] = 0

            # 防御: 确保我们只动了这两个字段
            other_keys_changed = []
            for k in ai:
                if k in ('matched_influencers', 'matched_influencers_updated_at'):
                    continue
                if ai.get(k) != b['before_ai_analysis'].get(k):
                    other_keys_changed.append(k)
            assert not other_keys_changed, f'BUG: 误改字段 {other_keys_changed}'

            new_text = json.dumps(ai, ensure_ascii=False)
            cursor = conn.execute(
                'UPDATE products SET ai_analysis = ?, updated_at = ? WHERE id = ?',
                (new_text, now_ms, pid)
            )
            if cursor.rowcount == 1:
                cleaned += 1
                print(f'  ✓ 清缓存 [{pid}] 其他字段保持: {sorted(set(ai.keys()) - {"matched_influencers", "matched_influencers_updated_at"})}')
            else:
                print(f'  ✗ 清缓存 [{pid}] 失败 (rowcount={cursor.rowcount})')

        conn.commit()
        print(f'\\n[完成] 共清理 {cleaned} 条商品的 matched_influencers 缓存')

        # 4) 验证
        for b in backups:
            pid = b['product_id']
            row = conn.execute('SELECT ai_analysis FROM products WHERE id = ?', (pid,)).fetchone()
            ai_after = json.loads(row[0]) if row[0] else {}
            assert ai_after.get('matched_influencers') == [], \
                f'[{pid}] matched_influencers 清理失败, 现值: {ai_after.get("matched_influencers")}'
            assert ai_after.get('matched_influencers_updated_at') == 0, \
                f'[{pid}] matched_influencers_updated_at 清理失败'
            # 确认其他字段保持
            for k in b['before_ai_analysis']:
                if k in ('matched_influencers', 'matched_influencers_updated_at'):
                    continue
                assert ai_after.get(k) == b['before_ai_analysis'].get(k), \
                    f'[{pid}] 字段 {k} 被误改!'
            print(f'  ✓ 验证 [{pid}] OK')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
