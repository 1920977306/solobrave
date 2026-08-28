# -*- coding: utf-8 -*-
"""单元测试：达人录入 category 取值修复
1. main_category 优先
2. 仅 category -> 用 category
3. category 空 + top_categories(list of dict) -> 取第一条 name
4. top_categories 为 JSON 字符串 -> 兼容解析
5. main_category/category/top_categories 全空 -> ''（不再回退 fan_category）
运行: python test_talent_category.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

spec = importlib.util.spec_from_file_location(
    'solobrave_server',
    os.path.join(os.path.dirname(os.path.abspath(__file__)), 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

failures = 0


def check(label, ok, extra=''):
    global failures
    print(f'{label} {"OK" if ok else "FAIL " + extra}')
    failures += 0 if ok else 1


check('1. main_category 优先',
      srv._dict_to_talent_row({'name': 'x', 'main_category': '服饰内衣', 'category': '时尚'})['category'] == '服饰内衣')
check('2. 仅 category',
      srv._dict_to_talent_row({'name': 'x', 'category': '鞋靴'})['category'] == '鞋靴')
check('3. top_categories list 兜底',
      srv._dict_to_talent_row({'name': 'x', 'top_categories': [{'name': '服饰内衣', 'ratio': 90}]})['category'] == '服饰内衣')
check('4. top_categories JSON 字符串兜底',
      srv._dict_to_talent_row({'name': 'x', 'top_categories': '[{"name": "美妆", "ratio": 80}]'})['category'] == '美妆')
r = srv._dict_to_talent_row({'name': 'x', 'fan_category': '服装23%'})
check('5. 全空不回退 fan_category', r['category'] == '', repr(r['category']))

# 强制优先级：top_categories 有数据时不信任客户端传入的 category
check('6. category=时尚 + top_categories 有数据 -> 强制取带货类目',
      srv._dict_to_talent_row({'name': 'x', 'category': '时尚',
                               'top_categories': [{'name': '服饰内衣', 'ratio': 87}]})['category'] == '服饰内衣')
check('7. category=时尚 + 无带货数据 -> 保持原值不猜测',
      srv._dict_to_talent_row({'name': 'x', 'category': '时尚'})['category'] == '时尚')
check('8. main_category 最优先（高于 top_categories）',
      srv._dict_to_talent_row({'name': 'x', 'main_category': '鞋靴箱包',
                               'top_categories': [{'name': '服饰内衣'}]})['category'] == '鞋靴箱包')

# 存量修复：top_categories 有数据但 category 不一致 -> 启动时自动修正
import sqlite3
import tempfile
tmpdir = tempfile.mkdtemp(prefix='sb_test_cat_')
srv.DB_PATH = os.path.join(tmpdir, 'test.db')
srv.init_db()
conn = sqlite3.connect(srv.DB_PATH)
now = 1700000000000
conn.execute("INSERT INTO talents (id, name, category, top_categories, status, created_at, updated_at) "
             "VALUES ('t_bad', '污染达人', '时尚', '[{\"name\": \"服饰内衣\", \"ratio\": 87}]', 'active', ?, ?)", (now, now))
conn.execute("INSERT INTO talents (id, name, category, top_categories, status, created_at, updated_at) "
             "VALUES ('t_nodata', '无数据达人', '时尚', '[]', 'active', ?, ?)", (now, now))
conn.execute("INSERT INTO talents (id, name, category, top_categories, status, created_at, updated_at) "
             "VALUES ('t_ok', '正常达人', '鞋靴', '[{\"name\": \"鞋靴\"}]', 'active', ?, ?)", (now, now))
conn.commit()
conn.close()
mconn = srv._db_conn()
srv._migrate_talent_categories(mconn)
mconn.close()
# 用独立连接读（迁移内部已 commit）
conn = sqlite3.connect(srv.DB_PATH)
bad = conn.execute("SELECT category FROM talents WHERE id='t_bad'").fetchone()[0]
nodata = conn.execute("SELECT category FROM talents WHERE id='t_nodata'").fetchone()[0]
okk = conn.execute("SELECT category FROM talents WHERE id='t_ok'").fetchone()[0]
conn.close()
check('9. 存量修复：污染记录被修正', bad == '服饰内衣', bad)
check('9b. 无带货数据的不动', nodata == '时尚', nodata)
check('9c. 一致的不动', okk == '鞋靴', okk)
import shutil
shutil.rmtree(tmpdir, ignore_errors=True)

print()
if failures:
    print(f'共 {failures} 项失败')
    sys.exit(1)
print('全部通过')
