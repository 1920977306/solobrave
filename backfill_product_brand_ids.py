#!/usr/bin/env python3
"""products.brand_id 关联回填 (dev/feat: products 修复 #4).

策略:
  1. 扫描 products 表 status='active' 且 brand_id='' 但 brand 非空
  2. 按 brand 名在 brands 表查 — 存在则 UPDATE products.brand_id
  3. 不存在则 INSERT brands 行 (auto_id), 再 UPDATE products.brand_id
  4. 同时聚合 brands.total_products / main_category 兜底

执行: python3 backfill_product_brand_ids.py (dry-run 默认, --apply 真正改库)
"""
import argparse
import os
import sqlite3
import sys
import uuid

DB_PATH = '/Users/qichen/solobrave-prod/data/solobrave.db'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正改库')
    args = ap.parse_args()

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        # 找 brand_id='' 但 brand 非空的 products
        products = con.execute("""
            SELECT id, name, brand, brand_id, category, monthly_sales, monthly_gmv
            FROM products
            WHERE status = 'active'
              AND (brand_id IS NULL OR brand_id = '')
              AND brand IS NOT NULL AND brand != ''
            ORDER BY brand, name
        """).fetchall()
        print(f'  待回填 {len(products)} 个 product:', flush=True)

        actions = []  # [(product_id, brand, action_type, brand_id)]
        # dedup 同一 brand 只生成一个 brand_id (避免花宁娜 x3 三个 id)
        new_brand_id_map = {}
        for p in products:
            brand = p['brand']
            existing = con.execute(
                "SELECT id, name FROM brands WHERE name = ? LIMIT 1", (brand,)
            ).fetchone()
            if existing:
                actions.append((p['id'], brand, 'link', existing['id']))
                print(f'  [link]  {p["id"]}  brand={brand!r} -> brands.id={existing["id"]}', flush=True)
            else:
                if brand not in new_brand_id_map:
                    new_brand_id_map[brand] = f'brand_auto_{uuid.uuid4().hex[:8]}'
                bid = new_brand_id_map[brand]
                actions.append((p['id'], brand, 'create+link', bid))
                print(f'  [new+] {p["id"]}  brand={brand!r} -> brands.id={bid} (will create)', flush=True)

        if not args.apply:
            print('\n  [DRY-RUN] 加 --apply 真正改库', flush=True)
            return

        import time as _t
        now_ms = int(_t.time() * 1000)

        # 1. 新建 brands 行
        created_brands = set()
        for product_id, brand, action, brand_id in actions:
            if action == 'create+link' and brand_id not in created_brands:
                # 找该 brand 的 main_category (优先 category 频次)
                cat_row = con.execute(
                    "SELECT category, COUNT(*) AS n FROM products "
                    "WHERE brand = ? AND status='active' AND category != '' "
                    "GROUP BY category ORDER BY n DESC LIMIT 1",
                    (brand,),
                ).fetchone()
                main_cat = cat_row['category'] if cat_row else ''
                # 聚合 total_products
                total_row = con.execute(
                    "SELECT COUNT(*) AS n FROM products WHERE brand = ? AND status='active'",
                    (brand,),
                ).fetchone()
                total_products = total_row['n'] if total_row else 0
                con.execute(
                    "INSERT OR IGNORE INTO brands (id, name, main_category, total_products, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 'active', ?, ?)",
                    (brand_id, brand, main_cat, total_products, now_ms, now_ms),
                )
                created_brands.add(brand_id)
                print(f'  [created brands] {brand_id}  name={brand!r}  main_category={main_cat!r}  total_products={total_products}', flush=True)

        # 2. UPDATE products.brand_id
        linked = 0
        for product_id, brand, action, brand_id in actions:
            con.execute(
                "UPDATE products SET brand_id = ?, updated_at = ? WHERE id = ?",
                (brand_id, now_ms, product_id),
            )
            linked += 1
        con.commit()
        print(f'\n  [DONE] linked {linked} products; created {len(created_brands)} brands', flush=True)
    finally:
        con.close()


if __name__ == '__main__':
    main()