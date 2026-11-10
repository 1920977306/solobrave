#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小拓闹钟（修订2 2026-11-10 增量派单）—— 9:30 由 launchd 触发。

它只做一件事：把今日任务投进 emp_xiaotuo 的会话。活儿由小拓 agent 回路
自己干（tools/xiaotuo_ops.py scan → 自己判定 → decide → report 交日报，
流程见 workspace TOOLS.md）。

闹钟纪律：
- 不复述任务、不代写日报——「建议人工过目」必须由小拓自己的判定产生；
- 投完看门：CLI 等到 turn 结束，或网关已接受（turn 仍在跑）后，
  轮询 emp_duties.last_run_at 最多 45 分钟；到期没交日报 ⇒ 给管理员写告警；
- 任何一步失败都不许阻塞——闹钟响了就算尽到责，作业交没交看门说了算。

手动跑：python3 xiaotuo_daily.py
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'data' / 'solobrave.db'
ADMIN_ID = 'user_7acb72ff'
AGENT_ID = 'emp_xiaotuo'
TURN_TIMEOUT = 2400        # agent 一整轮（扫描+判定+交付）CLI 最长等待 40 分钟
WATCHDOG_SEC = 45 * 60     # turn 早退（网关接受但 CLI 超时）后再等 45 分钟看作业
OPENCLAW_CLI = shutil.which('openclaw') or '/opt/homebrew/bin/openclaw'
# launchd 环境没有 homebrew 的 node：openclaw 启动脚本是 #!/usr/bin/env node，
# PATH 缺 node 会 127（2026-11-10 真跑实锤）⇒ 显式补齐 node 所在目录
_NODE_DIRS = [p for p in ('/opt/homebrew/bin', '/usr/local/bin')
              if (Path(p) / 'node').exists()]


def log(msg):
    print(f'[XiaotuoAlarm {datetime.now():%H:%M:%S}] {msg}', flush=True)


def _subprocess_env():
    env = os.environ.copy()
    env['PATH'] = ':'.join(_NODE_DIRS + [env.get('PATH', '')]).rstrip(':')
    return env


def load_duty():
    # 活库（WAL）只读一律普通 rw 句柄——mode=ro 可能建不了 -shm（AGENTS.md §16 Y）
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT * FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        cols = [d[0] for d in conn.execute(
            'SELECT * FROM emp_duties LIMIT 0').description]
        return dict(zip(cols, row)) if row else None
    finally:
        conn.close()


def build_task_message(duty):
    """今日任务书：只投递，不解释。流程细节让 TOOLS.md 说了算。"""
    category = (duty.get('category') or '').strip() or '全部'
    quota = duty.get('quota') or 5
    return (
        f'开始今日达人拓展：类目={category}，配额 {quota}。\n'
        '流程（细节见 TOOLS.md）：\n'
        '1. tools/xiaotuo_ops.py scan 收候选；\n'
        '2. 逐个按判定三关给「排除/存疑/值得联系 + 一句话理由」，'
        '用 decide 落库（排除也记）；\n'
        '3. report 交日报，格式：今日深看N | 新增N | 排除N（主因） | '
        '建议人工过目：昵称（判定：理由）；……\n'
        '「建议人工过目」必须由你的真实判定产生，不许默认写「无」。'
    )


def deliver_task(message):
    """投任务到 emp_xiaotuo 会话。返回 (exit_note, accepted_run_id)。"""
    args = [OPENCLAW_CLI, 'agent', '--agent', AGENT_ID, '--message', message,
            '--json', '--timeout', str(TURN_TIMEOUT)]
    try:
        result = subprocess.run(args, capture_output=True, text=True,
                                encoding='utf-8', errors='replace',
                                timeout=TURN_TIMEOUT + 120, env=_subprocess_env())
    except subprocess.TimeoutExpired:
        return ('CLI 等待超时（turn 可能仍在跑）', None)
    except Exception as e:
        return (f'CLI 调用失败：{e}', None)
    if result.returncode != 0:
        return (f'CLI 退出码 {result.returncode}：{(result.stderr or "")[:200]}', None)
    try:
        output = json.loads(result.stdout)
    except Exception:
        return (f'CLI 输出非 JSON：{(result.stdout or "")[:200]}', None)
    run_id = output.get('runId') if isinstance(output, dict) else None
    if output.get('ok') is False and not run_id:
        return (f'网关拒绝：{json.dumps(output, ensure_ascii=False)[:200]}', None)
    return ('turn 已结束' if output.get('ok') else '网关已接受（turn 可能仍在跑）', run_id)


def report_delivered():
    """看门：小拓今天交作业了吗？（last_run_at 是 report 端点回写的）"""
    conn = sqlite3.connect(str(DB), timeout=10)
    try:
        row = conn.execute('SELECT last_run_at FROM emp_duties WHERE agent_id = ?',
                           (AGENT_ID,)).fetchone()
        today = datetime.now().strftime('%Y-%m-%d')
        if not row or not row[0]:
            return False
        return datetime.fromtimestamp(row[0] / 1000).strftime('%Y-%m-%d') == today
    finally:
        conn.close()


def alert_admin(note, run_id):
    """小拓没交作业 ⇒ 管理员告警（旁路，失败只记日志）。"""
    try:
        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(str(DB), timeout=15)
        try:
            conn.execute(
                'INSERT INTO notifications (id, user_id, agent_id, type, title, '
                'content, read, created_at) VALUES (?,?,?,?,?,?,0,?)',
                ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, AGENT_ID,
                 'task_reminder', f'小拓未交日报 · {datetime.now():%m月%d日}',
                 f'闹钟已投任务（{note}' + (f'，runId={run_id}' if run_id else '')
                 + '），45 分钟内未收到小拓的日报交付。请 @小拓 询问进度。',
                 now_ms))
            conn.commit()
        finally:
            conn.close()
        log('已写管理员告警（小拓未交日报）')
    except Exception as e:
        log(f'告警写入失败（不阻断）: {e}')


def main():
    duty = load_duty()
    if not duty:
        log('emp_duties 没有小拓职责行，不投任务（先 PUT /api/agents/emp_xiaotuo/duties）')
        return 0
    message = build_task_message(duty)
    log(f'投递今日任务：类目={message.split("类目=")[1].split("，")[0]} '
        f'quota={duty.get("quota")}')

    note, run_id = deliver_task(message)
    log(f'投递结果：{note}' + (f'（runId={run_id}）' if run_id else ''))

    # 看门：turn 结束后（或早退）等它交作业
    deadline = time.time() + WATCHDOG_SEC
    while time.time() < deadline:
        if report_delivered():
            log('小拓已交日报（emp_duties.last_run_at 今日已更新），收工')
            return 0
        time.sleep(60)
    alert_admin(note, run_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
