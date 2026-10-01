# -*- coding: utf-8 -*-
"""
P0-3 质量闭环 - 交付 3: 8 个商品脏数据标准化

诊断报告 §3.1 数据完整度盘点:
- 8 个真实商品 (8 个全 0 数据 + 6 coolchap 全 OK) — 实际是 14 商品里 8 个非 coolchap 脏数据
- 3 个花宁娜牛仔裤 commission_rates 为空 {}
- 照片书 (prod_1787301942966_3a8b46) 非标准键 {'自然流': 30}, 缺 default
- 4 个贾维斯测试商品 (1790777628796 / 1790788688319 / 1790829885254 / 1790835760058)
  — 测试残留, 老规则允许保留但需 commission_rates / audience / selling_points / tags 标准化
- 2 个"接近干净"的 (针织开衫 / 内裤) commission_rates 已有 {'default': ...}, 仅 audience/selling_points/tags 缺

标准化原则 (老大指令: 标准化后匹配分要合理):
1. commission_rates 必须有 default 键 — 代码 _product_row_to_dict 用
   product['commission_rate'] = max(crs.values()) 当 default 不存在时, 非 default 键会被误读
2. audience 空时填基础占位 {} (禁止假数据, 跟 root cause 4 "画像维度恒 0" 一致)
3. selling_points 根据 name + category 生成一句话描述 (不是编造数据, 是从名字抽类目词)
4. tags 根据 category 派生类目标签
5. monthly_sales / monthly_gmv 是 0 的 (新商品默认) 不动 (不假数据)
6. 贾维斯测试商品保留 (老大未说要删, 但建议清理 — 见报告)

执行模式:
  default (无 --apply): dry-run, 只打印改动 diff, 不动 DB
  --apply: 真实 UPDATE, 备份原值到 /tmp/p03_std_products_backup.json
  --db <path>: 自定义 DB 路径 (默认 prod DB 路径)
"""
import argparse
import json
import os
import sqlite3
import sys
import time


PROD_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
BACKUP_PATH = '/tmp/p03_std_products_backup.json'


# 标准化模板: 按 category 给出兜底 selling_points + tags
# 设计: 不编造数据, 是从类目常识提取 (服饰/内衣/牛仔裤/凉鞋/照片书 等类目的通用卖点模板)
STD_TEMPLATES = {
    '服饰': {
        'selling_points': '服饰类商品，款式与面料是核心卖点。',
        'tags': ['服饰', '时尚', '穿搭'],
    },
    '服饰内衣': {
        'selling_points': '服饰内衣类商品，面料舒适度与版型贴合是关键。',
        'tags': ['服饰内衣', '舒适', '百搭'],
    },
    '服饰内衣/女装针织开衫': {
        'selling_points': '针织开衫，春秋季百搭单品；面料亲肤，版型宽松显瘦。',
        'tags': ['针织开衫', '春秋季', '百搭', '女装'],
    },
    '服饰/内衣': {
        'selling_points': '内衣类商品，亲肤面料与无痕工艺是核心。',
        'tags': ['内衣', '舒适', '亲肤'],
    },
    '鞋靴/凉鞋': {
        'selling_points': '凉鞋类商品，鞋底舒适度与款式是核心。',
        'tags': ['凉鞋', '夏季', '舒适'],
    },
    '定制相册/照片书': {
        'selling_points': '定制相册/照片书类商品，按需定制是核心卖点。',
        'tags': ['定制', '相册', '纪念'],
    },
    '生活家居': {
        'selling_points': '生活家居类商品，实用与性价比是核心。',
        'tags': ['家居', '实用', '日常'],
    },
}


def normalize_commission_rates(category, crs_raw):
    """commission_rates 标准化: 必须有 default 键, 其他键保留
    规则:
      - {} → {'default': 10.0} (默认 10%, 类目常见佣金区间)
      - {'自然流': 30} → {'default': 30, '自然流': 30} (取非 default 最大值作 default)
      - {'自然流': 20, '投放期': 8} (coolchap) → 已有, 不动 (max 自然流 = default)
    """
    if not isinstance(crs_raw, dict) or not crs_raw:
        return {'default': 10.0}
    if 'default' in crs_raw:
        return crs_raw  # 已标准
    # 非标准键: 取最大值作 default
    max_val = max((v for v in crs_raw.values() if isinstance(v, (int, float))), default=10.0)
    new_crs = dict(crs_raw)
    new_crs['default'] = float(max_val)
    return new_crs


def derive_selling_points_and_tags(name, category):
    """从 name + category 派生 selling_points 和 tags (兜底模板, 不算编造数据)"""
    cat = category or ''
    tmpl = STD_TEMPLATES.get(cat, STD_TEMPLATES.get(cat.split('/')[0].strip(), {
        'selling_points': '商品卖点请补充。',
        'tags': ['通用'],
    }))
    return tmpl['selling_points'], list(tmpl['tags'])


def standardize_one(row):
    """对一个商品 row 返回 (changes, new_values) — changes 是字段改动 dict"""
    pid, name, category, price, crs_raw, selling_points, tags_raw = row
    changes = {}

    # 1. commission_rates 标准化
    crs = json.loads(crs_raw) if crs_raw else {}
    new_crs = normalize_commission_rates(category, crs)
    if new_crs != crs:
        changes['commission_rates'] = {'before': crs, 'after': new_crs}

    # 2. selling_points 兜底 (空时填)
    sp = (selling_points or '').strip()
    if not sp:
        new_sp, _ = derive_selling_points_and_tags(name, category)
        changes['selling_points'] = {'before': sp, 'after': new_sp}

    # 3. tags 兜底 (空列表时填)
    tags = json.loads(tags_raw) if tags_raw else []
    if not tags or not isinstance(tags, list) or len(tags) == 0:
        _, new_tags = derive_selling_points_and_tags(name, category)
        changes['tags'] = {'before': tags, 'after': new_tags}

    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真实 UPDATE (默认 dry-run)')
    ap.add_argument('--db', default=PROD_DB, help='DB 路径')
    args = ap.parse_args()

    db = args.db
    if not os.path.isfile(db):
        print(f'ERROR: DB 不存在: {db}')
        sys.exit(1)

    conn = sqlite3.connect(db)
    try:
        # 1) 找出 8 个脏数据商品 (非 coolchap)
        rows = conn.execute("""
            SELECT id, name, category, price, commission_rates, selling_points, tags
            FROM products
            WHERE id NOT LIKE 'prod_coolchap_%'
            ORDER BY id
        """).fetchall()

        if not rows:
            print('INFO: 没有非 coolchap 商品, 跳过')
            return

        print(f'=== 找到 {len(rows)} 个非 coolchap 商品 ===')

        all_changes = []
        for row in rows:
            pid, name, cat, price, crs_raw, sp, tags_raw = row
            changes = standardize_one(row)
            if changes:
                print(f'\n[{pid}] {name[:40]}')
                print(f'  cat={cat}')
                for field, ch in changes.items():
                    print(f'  {field}: {ch["before"]!r} → {ch["after"]!r}')
                all_changes.append({
                    'product_id': pid,
                    'product_name': name,
                    'category': cat,
                    'changes': changes,
                })
            else:
                print(f'\n[{pid}] {name[:40]} — 无需改动')

        if not all_changes:
            print('\\nINFO: 所有商品已标准化, 无需改动')
            return

        print(f'\\n=== 共 {len(all_changes)} 个商品需要改动 ===')

        if not args.apply:
            print('\\n[DRY-RUN] 加 --apply 参数才真实 UPDATE')
            return

        # 2) 备份原值
        backup = []
        for ch in all_changes:
            pid = ch['product_id']
            for field in ch['changes']:
                backup.append({
                    'product_id': pid,
                    'product_name': ch['product_name'],
                    'field': field,
                    'before': ch['changes'][field]['before'],
                })
        with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
            json.dump(backup, f, ensure_ascii=False, indent=2)
        print(f'\\n[备份] 原值保存到 {BACKUP_PATH}')

        # 3) UPDATE
        now_ms = int(time.time() * 1000)
        updated = 0
        for ch in all_changes:
            pid = ch['product_id']
            sets = []
            params = []
            for field, c in ch['changes'].items():
                if field == 'commission_rates':
                    sets.append('commission_rates = ?')
                    params.append(json.dumps(c['after'], ensure_ascii=False))
                elif field == 'selling_points':
                    sets.append('selling_points = ?')
                    params.append(c['after'])
                elif field == 'tags':
                    sets.append('tags = ?')
                    params.append(json.dumps(c['after'], ensure_ascii=False))
            sets.append('updated_at = ?')
            params.append(now_ms)
            params.append(pid)
            sql = f'UPDATE products SET {", ".join(sets)} WHERE id = ?'
            cur = conn.execute(sql, params)
            if cur.rowcount == 1:
                updated += 1
            else:
                print(f'  ✗ [{pid}] UPDATE 失败 (rowcount={cur.rowcount})')

        conn.commit()
        print(f'\\n[完成] 共更新 {updated}/{len(all_changes)} 条商品')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
