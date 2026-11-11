#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""库管家闹钟（组织第一批 2026-11-11）—— launchd 触发，参数化复用给所有 maintain 型管家。

与小拓闹钟（xiaotuo_daily.py）同款纪律：
- 只投任务不复述：活儿由管家 agent 回路自己干（keeper_ops.py 体检 → 自己组织日报
  → /square-report 交付，/square-report 已通用 agent_id + duty_name 动态标题）；
- 投完看门：CLI 等到 turn 结束，或网关已接受但 CLI 超时后，轮询 emp_duties.last_run_at
  最多 45 分钟；到期没交日报 ⇒ 给管理员写告警；
- 任何一步失败都不许阻塞——闹钟响了就算尽到责。

launchd 一岗一单（com.solobrave.keeper-talent / keeper-goods），时错峰开 9:30。

手动跑：python3 keeper_daily.py --agent emp_talent_keeper
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
ADMIN_ID = 'user_7acb72ff'
TURN_TIMEOUT = 900          # 体检+写日报一轮 15 分钟足够（体检本身 <1s）
WATCHDOG_SEC = 45 * 60      # turn 早退后再等 45 分钟看作业
OPENCLAW_CLI = shutil.which('openclaw') or '/opt/homebrew/bin/openclaw'
_NODE_DIRS = [p for p in ('/opt/homebrew/bin', '/usr/local/bin')
              if (Path(p) / 'node').exists()]


def log(msg):
    print(f'[KeeperAlarm {datetime.now():%H:%M:%S}] {msg}', flush=True)


def _subprocess_env():
    env = os.environ.copy()
    env['PATH'] = ':'.join(_NODE_DIRS + [env.get('PATH', '')]).rstrip(':')
    return env


def load_duty(agent_id):
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT * FROM emp_duties WHERE agent_id = ?',
                           (agent_id,)).fetchone()
        cols = [d[0] for d in conn.execute(
            'SELECT * FROM emp_duties LIMIT 0').description]
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def deliver_alarm(agent_id, duty):
    """把今日任务投进管家的 openclaw 会话；CLI 等到 turn 结束。返回 (投成功, turn是否跑完)。"""
    duty_name = duty.get('duty_name') or agent_id
    prompt = (
        f"【今日任务·{datetime.now():%m月%d日}】{duty_name}每日体检。\n"
        f"步骤：1) 运行 python3 tools/keeper_ops.py --agent {agent_id} --json 拿到体检清单；\n"
        f"2) 按你的交付标准组织日报（三项清单，如实报数，无则写「无」）；\n"
        f"3) 用 tools 里的报告通道交付（POST 127.0.0.1:8095/square-report，"
        f"summary 一句话 + detail 全文，agent_id={agent_id}）。\n"
        f"纪律：体检只读，不新建/不删除/不合并任何记录。"
    )
    try:
        result = subprocess.run(
            [OPENCLAW_CLI, 'agent', '--agent', agent_id, '--message', prompt,
             '--json', '--timeout', str(TURN_TIMEOUT)],
            capture_output=True, text=True, timeout=TURN_TIMEOUT + 120,
            env=_subprocess_env())
        if result.returncode == 0:
            return True, True
        log(f'CLI 退出码 {result.returncode}（可能网关已接受 turn 仍在跑）: '
            f'{(result.stderr or "")[:200]}')
        return True, False   # 消息大概率已投进，turn 还在跑
    except subprocess.TimeoutExpired:
        log(f'CLI 超时 {TURN_TIMEOUT + 120}s（turn 可能仍在跑）')
        return True, False
    except Exception as e:
        log(f'投递失败: {e}')
        return False, False


def watch_last_run(agent_id, before_ms):
    """看门：轮询 last_run_at 是否刷新，最多 WATCHDOG_SEC。返回是否交了作业。"""
    deadline = time.time() + WATCHDOG_SEC
    while time.time() < deadline:
        time.sleep(60)
        duty = load_duty(agent_id)
        if duty and (duty.get('last_run_at') or 0) > before_ms:
            return True
    return False


def notify_admin(agent_id, text):
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        conn.execute(
            'INSERT INTO notifications (id, user_id, agent_id, type, title, content, '
            'read, created_at) VALUES (?,?,?,?,?,?,0,?)',
            ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, agent_id,
             'task_reminder', f'⚠️ 管家未交日报 · {datetime.now():%m月%d日}',
             text, int(time.time() * 1000)))
        conn.commit()
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--agent', required=True)
    args = ap.parse_args()
    duty = load_duty(args.agent)
    if not duty:
        log(f'无职责行：{args.agent}（先 PUT duties 再排班）')
        return
    before = duty.get('last_run_at') or 0
    log(f'投今日任务给 {args.agent}（{duty.get("duty_name")}）…')
    delivered, finished = deliver_alarm(args.agent, duty)
    if not delivered:
        notify_admin(args.agent, f'{args.agent} 今日任务投递失败（CLI 异常），请手动检查。')
        return
    if finished:
        duty2 = load_duty(args.agent)
        if (duty2 or {}).get('last_run_at', 0) > before:
            log('turn 跑完且日报已交付 ✅')
            return
        log('turn 跑完但 last_run_at 未刷新，看门 45 分钟…')
    else:
        log('turn 仍在跑，看门 45 分钟…')
    if watch_last_run(args.agent, before):
        log('看门确认：日报已交付 ✅')
    else:
        log('看门超时：日报未交付，告警管理员')
        notify_admin(args.agent,
                     f'{duty.get("duty_name")}（{args.agent}）任务已投递但 45 分钟内未交日报，'
                     f'请检查 agent 状态。')


if __name__ == '__main__':
    main()
