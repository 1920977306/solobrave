#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小拓每日调度（A 简报 2026-11-10）—— 9:30 由 launchd 触发。

流程：
  1. 读 emp_duties 里小拓的职责行（category/quota；没有行就退出）
  2. POST buyin_worker :8095/square-scan → 轮询 /jobs/<id> 到终态（最长 30 分钟）
  3. 按简报日报格式出报告：今日深看N | 新增N | 排除N（主因）| 建议人工过目：XXX
  4. 写 notifications（user_7acb72ff）+ 回写 emp_duties.last_run_*（名片「近期动态」）

铁律沿用工具层：风控/登录态失败如实报，绝不重试。
手动跑：python3 xiaotuo_daily.py
"""
import json
import sqlite3
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib import request as _req

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
WORKER = 'http://127.0.0.1:8095'
ADMIN_ID = 'user_7acb72ff'
AGENT_ID = 'emp_xiaotuo'
POLL_SEC = 30
TIMEOUT_SEC = 30 * 60


def log(msg):
    print(f'[XiaotuoDaily {datetime.now():%H:%M:%S}] {msg}', flush=True)


def _json_call(method, url, payload=None, timeout=30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = _req.Request(url, data=data, method=method,
                       headers={'Content-Type': 'application/json'})
    with _req.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or '{}')


def load_duty():
    conn = sqlite3.connect(f'file:{DB}?mode=ro', uri=True)
    try:
        row = conn.execute('SELECT * FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        cols = [d[0] for d in conn.execute(
            'SELECT * FROM emp_duties LIMIT 0').description]
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def write_report(summary, detail):
    """通知管理员 + 回写名片近期动态。失败只记日志，不抛。"""
    now_ms = int(time.time() * 1000)
    try:
        conn = sqlite3.connect(str(DB), timeout=15)
        try:
            conn.execute(
                'INSERT INTO notifications (id, user_id, agent_id, type, title, '
                'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
                ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, AGENT_ID,
                 'task_reminder',
                 f'小拓日报 · {datetime.now():%m月%d日}', detail, now_ms))
            conn.execute(
                'UPDATE emp_duties SET last_run_at = ?, last_run_summary = ?, '
                'updated_at = ? WHERE agent_id = ?',
                (now_ms, summary, now_ms, AGENT_ID))
            conn.commit()
        finally:
            conn.close()
        log('日报已写入 notifications + emp_duties.last_run_*')
    except Exception as e:
        log(f'写报告失败（不阻断）: {e}')


def main():
    duty = load_duty()
    if not duty:
        log('emp_duties 没有小拓职责行，退出（先 PUT /api/agents/emp_xiaotuo/duties）')
        return 0
    category = (duty.get('category') or '').strip()
    quota = duty.get('quota') or 5
    log(f'职责行：category={category or "全部"} quota={quota}')

    # ① 触发扫描
    try:
        resp = _json_call('POST', f'{WORKER}/square-scan',
                          {'category': category, 'quota': quota})
    except Exception as e:
        detail = f'今日深看0 | 新增0 | 排除0（worker 不可达：{e}）| 建议人工过目：检查 buyin_worker 是否在跑'
        write_report('worker 不可达，今日未跑', detail)
        log(detail)
        return 0
    if not resp.get('ok'):
        err = resp.get('error') or resp.get('status') or 'unknown'
        detail = f'今日深看0 | 新增0 | 排除0（{err}）| 建议人工过目：无需'
        write_report(f'未执行：{err}', detail)
        log(detail)
        return 0
    cid = resp['collect_id']
    log(f'任务已入队 {cid}，开始轮询')

    # ② 轮询到终态
    deadline = time.time() + TIMEOUT_SEC
    job = {}
    while time.time() < deadline:
        time.sleep(POLL_SEC)
        try:
            job = _json_call('GET', f'{WORKER}/jobs/{cid}', timeout=15)
        except Exception as e:
            log(f'轮询网络错误（继续）: {e}')
            continue
        status = job.get('status')
        if status in ('done', 'failed', 'blocked_risk', 'need_login', 'refused'):
            log(f'终态 {status}（stage={job.get("stage", "")}）')
            break
    else:
        job = {'status': 'timeout', 'error': f'{TIMEOUT_SEC // 60} 分钟未出终态'}

    # ③ 日报（简报格式：今日深看N | 新增N | 排除N（主因）| 建议人工过目：XXX）
    status = job.get('status')
    if status == 'done':
        result = json.loads(job.get('result') or '{}')
        scanned = result.get('scanned', 0)
        inserted = result.get('inserted', 0)
        dups = result.get('duplicates', 0)
        skipped = result.get('skipped', 0)
        reasons = []
        if dups:
            reasons.append(f'重复 {dups}')
        if skipped:
            reasons.append(f'缺抖音号 {skipped}')
        main_reason = ' / '.join(reasons) if reasons else '无排除'
        excluded = dups + skipped
        eyeball = []
        for it in result.get('items') or []:
            if str(it.get('入库', '')).startswith('inserted'):
                settle = it.get('30天结算额')
                settle_txt = ''
                if isinstance(settle, list) and len(settle) == 2:
                    settle_txt = f'（30天结算 {settle[0]}-{settle[1]}）'
                eyeball.append(f'{it.get("昵称", "?")}（抖音号 {it.get("抖音号", "?")}）{settle_txt}')
        eyeball_txt = '；'.join(eyeball) if eyeball else '无'
        detail = (f'今日深看{scanned} | 新增{inserted} | 排除{excluded}（{main_reason}）'
                  f' | 建议人工过目：{eyeball_txt}')
        summary = f'今日筛出 {scanned} 位候选，新入库 {inserted} 位'
    else:
        err = job.get('error') or status
        detail = f'今日深看0 | 新增0 | 排除0（{status}：{err}）| 建议人工过目：' + (
            '风控停手，等环境冷却人工检查' if status == 'blocked_risk'
            else '团长端登录态失效，需要扫码重登' if status == 'need_login'
            else '查看 worker 日志定位原因')
        summary = f'今日未跑成（{status}）'

    write_report(summary, detail)
    log(detail)
    return 0


if __name__ == '__main__':
    sys.exit(main())
