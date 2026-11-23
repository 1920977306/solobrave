# -*- coding: utf-8 -*-
"""历史回填日期边界守卫（2026-11-23 事故实证：回填脚本日期范围含在飞日，
apply 先删后插把当班 5 行活数据冲空）。

铁律：历史回填是「封账」工具，绝不许碰在飞日——在飞日真源是 decide 双写/
日报交付链。守卫三层：
1. 源码锚：查询范围硬顶昨天（--through 参数 + 默认 yesterday）
2. 源码锚：apply 循环第二道闸（显式 --through 传在飞日也被跳过）
3. 行为：dry-run 对含今日通知的库，输出里今日日期不得出现
"""
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'scripts', 'backfill_square_recs.py')


def _src():
    return open(SCRIPT, encoding='utf-8').read()


def test_query_hard_capped_at_yesterday():
    src = _src()
    assert '--through' in src, '必须有 --through 参数'
    assert 'timedelta(days=1)' in src, '默认截止必须硬顶昨天'
    assert '在飞日不许填' in src or '在飞日' in src, '边界铁律注释必须在位'


def test_apply_loop_second_gate():
    src = _src()
    assert 'd >= today_str' in src, 'apply 循环必须有今日硬闸'
    assert '按铁律跳过' in src, '跳过时必须显式打印'
    assert src.count('在飞日') >= 2, '查询闸 + apply 闸双保险都在'


def test_apply_gate_inside_apply_branch():
    """硬闸必须落在 apply 分支内（dry-run 之后），且先于 DELETE。"""
    src = _src()
    apply_pos = src.find("if not args.apply:")
    gate_pos = src.find('d >= today_str')
    delete_pos = src.find('DELETE FROM talent_recommendations')
    assert apply_pos != -1 and gate_pos != -1 and delete_pos != -1
    assert apply_pos < gate_pos < delete_pos, '闸必须在 apply 分支内、DELETE 之前'


def test_dry_run_never_mentions_today(tmp_path):
    """行为守卫：库里有一条「今日」的日报通知，dry-run 输出不得出现今日日期。"""
    db = tmp_path / 'test.db'
    conn = sqlite3.connect(db)
    conn.execute(
        'CREATE TABLE notifications (id INTEGER PRIMARY KEY, agent_id TEXT, '
        'type TEXT, title TEXT, content TEXT, created_at INTEGER)')
    import datetime as dt
    now = dt.datetime.now()
    today_ms = int(now.timestamp() * 1000)
    yesterday_ms = int((now - dt.timedelta(days=1)).timestamp() * 1000)
    today_str = now.strftime('%Y-%m-%d')
    # 今日班通知（带日期锚的 canonical 形班次头）
    conn.execute(
        'INSERT INTO notifications (agent_id, type, title, content, created_at) '
        'VALUES (?,?,?,?,?)',
        ('emp_xiaotuo', 'task_reminder',
         '达人筛选专员日报 · 11月23日',
         f'广场即时班 · {today_str} 17:30\n随便什么正文', today_ms))
    # 昨日班通知
    ystr = (now - dt.timedelta(days=1)).strftime('%Y-%m-%d')
    conn.execute(
        'INSERT INTO notifications (agent_id, type, title, content, created_at) '
        'VALUES (?,?,?,?,?)',
        ('emp_xiaotuo', 'task_reminder',
         '达人筛选专员日报 · 11月22日',
         f'广场即时班 · {ystr} 17:30\n随便什么正文', yesterday_ms))
    conn.commit()
    conn.close()
    r = subprocess.run(
        [sys.executable, SCRIPT, '--db', str(db)],
        capture_output=True, text=True, cwd=ROOT, timeout=120)
    # tmp 库没有 EXPECTED 三天的真实日报，对拍必红（exit 1）——这本身是对的；
    # 本守卫只钉一件事：无论红绿，在飞日都不得出现在解析范围。
    assert today_str not in r.stdout, (
        f'dry-run 输出不得包含在飞日 {today_str}：\n{r.stdout}')
    assert ystr in r.stdout, '昨日通知必须正常出现在解析范围'
