#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Helen 分析达人「分析即入库」历史回填脚本
=========================================

老大反馈: Helen 历史分析过的 26 个达人 (knowledge_events, entity_id='name:XXX')
与 talents 主表 61 条零重合, 分析完从不建档. 部分 entity_id 还含脏名
(LLM 把报告序号当名字, 存成 'name:1. 小楚当妈(捡漏版)' 而不是
'name:小楚当妈(捡漏版)').

回填脚本:
1. 默认 dry-run, 只打印计划
2. --apply 才真正写库 (前自动备份 data/solobrave.db)
3. 按干净名分组, 调 _ensure_talent_from_analysis 建档/更新
4. **脏名合并**: 把脏实体 entity_id UPDATE 成 'name:干净名'
   (保留 name: 前缀, 不要改成 tal_id — 前端 index.html L26469
   只认 name: 前缀, 改成 tal_id 会被过滤消失)

执行: python3 scripts/backfill_analyzed_talents.py [--apply]
"""
import argparse
import importlib.util
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

# 路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
DB_PATH = os.path.join(ROOT_DIR, 'data', 'solobrave.db')
BACKUP_DIR = os.path.join(ROOT_DIR, 'data', 'backups')
SERVER_PY = os.path.join(ROOT_DIR, 'solobrave-server.py')


def load_server_module():
    """importlib 加载 solobrave-server 模块, 复用 _clean_talent_name / _ensure_talent_from_analysis 等"""
    spec = importlib.util.spec_from_file_location('solobrave_server', SERVER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect_known_talents(server):
    """从 talents 主表拿现有 61 条, 用 set 查重"""
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, name FROM talents WHERE status = 'active'"
        ).fetchall()
    finally:
        conn.close()
    return {row[1]: row[0] for row in rows}


def collect_events_by_name(server, conn):
    """遍历 knowledge_events WHERE event_type='analysis' AND entity_id LIKE 'name:%'.
    entity_id 去 'name:' 前缀, 过 _clean_talent_name 清洗, 按干净名分组.
    每组取: latest analysis event + latest vision_data event
    """
    cur = conn.execute(
        "SELECT id, entity_id, content_full, created_at, event_type "
        "FROM knowledge_events "
        "WHERE event_type IN ('analysis', 'vision_data') AND entity_id LIKE 'name:%'"
    )
    rows = cur.fetchall()

    by_clean = defaultdict(lambda: {'analysis': None, 'vision_data': None, 'dirty_entities': set()})
    for r in rows:
        eid, content_full, created_at, etype = r[0], r[1], r[2], r[3]
        # content_full 才是字段 3 (id=0, entity_id=1, content_full=2, created_at=3, event_type=4)
        # 实际 SELECT 顺序: id, entity_id, content_full, created_at, event_type
        eid_str = eid or ''
        if not eid_str.startswith('name:'):
            continue
        raw_name = eid_str[5:]  # 去 'name:' 前缀
        clean = server._clean_talent_name(raw_name)
        if not clean:
            continue
        bucket = by_clean[clean]
        # 比较 created_at: 越大越新 (epoch ms)
        cur_event = bucket.get(etype)
        if cur_event is None or created_at > cur_event[1]:
            bucket[etype] = (content_full, created_at)
            # 记录脏实体 (跟干净名不同的 entity_id)
            if raw_name != clean:
                bucket['dirty_entities'].add(eid_str)
    return by_clean


def plan_backfill(server, dry_run=True):
    """规划回填, 返回 (新建名单, 更新名单, 合并脏实体映射)."""
    print(f'[{datetime.now().isoformat()}] 开始 {"dry-run" if dry_run else "apply"} 规划...')

    known = collect_known_talents(server)
    print(f'  现有 talents 表: {len(known)} 条 active')

    conn = sqlite3.connect(DB_PATH)
    try:
        by_clean = collect_events_by_name(server, conn)
    finally:
        conn.close()
    print(f'  历史 analysis/vision_data 事件按干净名分组: {len(by_clean)} 个干净名')

    will_create = []
    will_update = []
    will_merge_dirty = []

    for clean_name, bucket in sorted(by_clean.items()):
        if clean_name in known:
            will_update.append(clean_name)
        else:
            will_create.append(clean_name)
        for dirty_eid in bucket.get('dirty_entities', set()):
            # dirty 是 'name:1. 小楚当妈(捡漏版)', clean_name 是 '小楚当妈(捡漏版)'
            # 合并后: 'name:小楚当妈(捡漏版)'
            will_merge_dirty.append((dirty_eid, f'name:{clean_name}'))

    return will_create, will_update, will_merge_dirty


def print_plan(will_create, will_update, will_merge_dirty):
    print(f'\n=== 回填计划 ===')
    print(f'  新建达人: {len(will_create)} 个')
    for name in will_create[:5]:
        print(f'    + {name}')
    if len(will_create) > 5:
        print(f'    ... 还有 {len(will_create) - 5} 个')
    print(f'\n  更新现有达人 (OCR/LLM 字段回写): {len(will_update)} 个')
    for name in will_update[:5]:
        print(f'    ↻ {name}')
    if len(will_update) > 5:
        print(f'    ... 还有 {len(will_update) - 5} 个')
    print(f'\n  合并脏实体 (knowledge_events entity_id): {len(will_merge_dirty)} 条')
    for dirty, clean in will_merge_dirty[:5]:
        print(f'    {dirty} → {clean}')
    if len(will_merge_dirty) > 5:
        print(f'    ... 还有 {len(will_merge_dirty) - 5} 条')


def backup_db():
    if not os.path.isfile(DB_PATH):
        raise FileNotFoundError(f'DB 不存在: {DB_PATH}')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(BACKUP_DIR, f'solobrave_pre_backfill_{ts}.db')
    shutil.copy2(DB_PATH, backup_path)
    print(f'  备份成功: {backup_path}')
    return backup_path


def apply_backfill(server):
    """真正写库: 备份 → 新建/更新 → 合并脏实体."""
    backup_path = backup_db()

    will_create, will_update, will_merge_dirty = plan_backfill(server, dry_run=False)

    created_ids = []
    updated_count = 0
    merged_count = 0

    conn = sqlite3.connect(DB_PATH)
    try:
        # server 模块初始化 DB 路径 (用 solobrave-server 的 _db_conn)
        # 但 server 模块默认 _db_conn 走全局 DB_PATH, 我们 importlib 加载后会用相同路径
        for clean_name, _bucket in sorted(server._analyze_events.items()) if False else []:
            pass  # 不用这条, 直接走下面 server._ensure_talent_from_analysis
    except Exception:
        pass
    finally:
        conn.close()

    # 走 server 的 helper 做建档/更新 (OCR 字段 + LLM JSON 回写由 helper 内部处理)
    for clean_name, bucket in sorted(_collect_clean(server).items()):
        try:
            # 从 vision_data 配对事件拿 OCR 字段
            vision_content = (bucket.get('vision_data') or [None])[0]
            vision_field_maps = []
            if vision_content:
                vision_field_maps = server._heavy_vision_coverage(
                    [ln for ln in vision_content.split('\n') if ln.strip()]
                )[1]
            # 从 analysis 事件拿 LLM JSON
            analysis_content = (bucket.get('analysis') or [None])[0]
            llm_json = {}
            if analysis_content:
                llm_json = server._parse_llm_json_block(analysis_content)

            ensured = server._ensure_talent_from_analysis(
                clean_name, vision_field_maps, llm_json, user_id=''
            )
            if ensured and ensured.get('id'):
                if clean_name in {n for n, _ in _collect_clean(server).items() if n == clean_name}:
                    # 简化: 记录创建/更新
                    if clean_name in collect_known_talents(server).keys():
                        updated_count += 1
                    else:
                        created_ids.append(ensured['id'])
        except Exception as e:
            print(f'  [Backfill] 处理 {clean_name} 失败: {e}')

    # 合并脏实体 (UPDATE knowledge_events.entity_id)
    if will_merge_dirty:
        conn = sqlite3.connect(DB_PATH)
        try:
            for dirty_eid, clean_eid in will_merge_dirty:
                cur = conn.execute(
                    "UPDATE knowledge_events SET entity_id = ? WHERE entity_id = ?",
                    (clean_eid, dirty_eid)
                )
                merged_count += cur.rowcount
            conn.commit()
        finally:
            conn.close()

    return {
        'backup': backup_path,
        'created': len(created_ids),
        'updated': updated_count,
        'merged_events': merged_count,
        'create_ids': created_ids,
    }


def _collect_clean(server):
    """重跑一次 collect_events_by_name 给 apply_backfill 用"""
    conn = sqlite3.connect(DB_PATH)
    try:
        return collect_events_by_name(server, conn)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='Helen 分析达人历史回填 (默认 dry-run)')
    parser.add_argument('--apply', action='store_true', help='真正写库 (前自动备份 DB)')
    args = parser.parse_args()

    if not os.path.isfile(DB_PATH):
        print(f'[ERROR] DB 不存在: {DB_PATH}')
        sys.exit(1)
    if not os.path.isfile(SERVER_PY):
        print(f'[ERROR] solobrave-server.py 不存在: {SERVER_PY}')
        sys.exit(1)

    print(f'  DB: {DB_PATH}')
    print(f'  server: {SERVER_PY}')

    server = load_server_module()

    if args.apply:
        result = apply_backfill(server)
        print(f'\n=== apply 完成 ===')
        print(f'  备份: {result["backup"]}')
        print(f'  新建达人: {result["created"]} 条 (ids={result["create_ids"]})')
        print(f'  更新达人: {result["updated"]} 条 (OCR/LLM 字段回写)')
        print(f'  合并脏实体 (knowledge_events UPDATE): {result["merged_events"]} 条')
    else:
        will_create, will_update, will_merge_dirty = plan_backfill(server, dry_run=True)
        print_plan(will_create, will_update, will_merge_dirty)
        print(f'\n  这是 dry-run, 没改 DB. 加 --apply 才真正写库 (前自动备份).')


if __name__ == '__main__':
    main()