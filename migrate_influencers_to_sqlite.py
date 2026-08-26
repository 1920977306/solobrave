# -*- coding: utf-8 -*-
"""把 data/influencers/ 的 legacy JSON 达人记录迁移到 SQLite talents 表（幂等，跳过 id 已存在的）。

统一数据源改造后，服务器启动时会自动执行同样的迁移（在导出 JSON 缓存之前），
本脚本供手动核对/补迁使用：
  python migrate_influencers_to_sqlite.py                 # 迁移默认 data/ 目录
  python migrate_influencers_to_sqlite.py --data /path/to/data
"""
import argparse
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding='utf-8')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

parser = argparse.ArgumentParser(description='迁移 JSON 达人库到 SQLite talents 表')
parser.add_argument('--data', default=None, help='数据目录（默认: <项目>/data）')
args = parser.parse_args()

spec = importlib.util.spec_from_file_location(
    'solobrave_server', os.path.join(BASE_DIR, 'solobrave-server.py'))
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)

if args.data:
    data_dir = os.path.abspath(args.data)
    srv.DATA_DIR = data_dir
    srv.DB_PATH = os.path.join(data_dir, 'solobrave.db')
    srv.INFLUENCER_DIR = os.path.join(data_dir, 'influencers')

print(f'数据目录: {srv.DATA_DIR}')
print(f'数据库:   {srv.DB_PATH}')
print(f'JSON 源:  {srv.INFLUENCER_DIR}')

# 确保 talents 表结构完整（含统一数据源新增的列）
srv.init_db()

imported, skipped = srv._migrate_influencers_json_to_sqlite()
print(f'迁移完成: 导入 {imported} 条, 跳过已存在 {skipped} 条')

# 导出 JSON 只读缓存，保持 data/influencers/ 与 SQLite 一致
srv._export_influencers_json_cache()
print('JSON 只读缓存已更新')
