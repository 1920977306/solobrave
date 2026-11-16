#!/usr/bin/env python3
"""积分看门（熔断未上线前的人盯版自动化，老大 11-13 21:31 拍板「现在就弄」）。

判定：窗口内某员工 consumption 积分累计 > --limit →  breaches。
      触发后执行 --on-breach 命令（如网关 turn 中止），并写留痕标记。
只读 solobrave.db；留痕写 workspace 标记文件（不进库，不碰 prod）。

用法：
  # 循环值守（9:25 窗口起，每 5 分钟一查，到 12:00 自动收工）
  watch_credits.py --emp emp_xiaotuo --limit 500 --window-start 09:25 --window-end 12:00
  # 单次检查（cron/人工触发）
  watch_credits.py --emp emp_xiaotuo --limit 500 --window-start 09:25 --once
  # 带中止动作（执行方接网关中止命令后启用）
  watch_credits.py --emp emp_xiaotuo --limit 500 --window-start 09:25 \
      --on-breach 'openclaw turns abort --agent emp_xiaotuo'

退出码：0 = 未越限 / 窗口未开始；1 = 检查出错；2 = 越限（已触发 on-breach 或仅告警）。
"""
import argparse
import datetime as dt
import os
import sqlite3
import subprocess
import sys
import time

DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
MARKER_DIR = '/Users/qichen/solobrave-prod/data/watchdog-markers'  # P2-② 入 prod：留痕进 prod 数据目录（data/ 已 gitignore）


def window_start_str(hhmm: str) -> str:
    h, m = hhmm.split(':')
    today = dt.date.today().strftime('%Y-%m-%d')
    return f'{today} {int(h):02d}:{int(m):02d}:00'


def burn(emp: str, since: str) -> int:
    db = sqlite3.connect(DB)  # 纯 SELECT；ro URI 在本库 journal 模式下打不开
    row = db.execute(
        "SELECT COALESCE(SUM(credits_used),0), COUNT(*) FROM credit_usage_log "
        "WHERE agent_id=? AND reason='consumption' AND created_at >= ?",
        (emp, since)).fetchone()
    db.close()
    return row[0], row[1]


def check(args) -> int:
    since = window_start_str(args.window_start)
    now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if now < since:
        print(f'窗口未开始（{now} < {since}），退出')
        return 0
    total, n = burn(args.emp, since)
    print(f'[{now}] {args.emp} 窗口内消耗 {total}/{args.limit}（{n} 条流水）')
    if total <= args.limit:
        return 0
    # 越限：留痕 + 触发中止钩子
    stamp = dt.datetime.now().strftime('%Y%m%d-%H%M%S')
    marker = os.path.join(MARKER_DIR, f'credit-watch-breach-{args.emp}-{stamp}.log')
    with open(marker, 'w') as f:
        f.write(f'emp={args.emp}\ntime={now}\nwindow_start={since}\n'
                f'burn={total}\nlimit={args.limit}\nentries={n}\n')
    print(f'⚠️ 越限：{total} > {args.limit}，留痕 {marker}')
    if args.on_breach:
        print(f'执行中止钩子: {args.on_breach}')
        subprocess.run(args.on_breach, shell=True)
    else:
        print('（未配 --on-breach，仅告警——执行方接网关中止命令后启用真中止）')
    return 2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--emp', default='emp_xiaotuo')
    ap.add_argument('--limit', type=int, default=500)
    ap.add_argument('--window-start', default='09:25')
    ap.add_argument('--window-end', default='12:00')
    ap.add_argument('--interval', type=int, default=300)
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--on-breach', default='')
    args = ap.parse_args()

    if args.once:
        sys.exit(check(args))
    end = window_start_str(args.window_end)
    while True:
        now = dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if now >= end:
            print(f'窗口结束（{now} >= {end}），收工')
            break
        check(args)
        time.sleep(args.interval)
    sys.exit(0)


if __name__ == '__main__':
    main()
