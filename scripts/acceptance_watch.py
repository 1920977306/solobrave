#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收值守（dq_9e5618bf3cf9 ②）：工作日 18:30 自动跑 check_daily_recommendations。

三层防护第一层真正自动运转：launchd 定时（com.solobrave.acceptance-watch，
Weekday 1-5 18:30）→ 跑当日验收 → 失败即桌面推送（复用值班通知链 duty_notify）
+ 落 data/acceptance_watch.log 留痕。验收脚本自身崩溃（exit≥2/超时）同样告警——
「值守没跑」和「验收红了」是同一等级的失守。

用法：python3 scripts/acceptance_watch.py [--date YYYY-MM-DD]（launchd 直调无参）
"""
import datetime as dt
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHECK = os.path.join(ROOT, 'scripts', 'check_daily_recommendations.py')
LOG = os.path.join(ROOT, 'data', 'acceptance_watch.log')
TIMEOUT = 300  # 验收脚本 5 分钟不返回视为失守


def _push(title, body=''):
    try:
        sys.path.insert(0, os.path.join(ROOT, 'scripts'))
        import duty_notify as dn
        dn.desktop_push(title, body[:180])
    except Exception as e:  # 推送失败不吞验收结果
        print(f'[push-fail] {e}')


def _log(line):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def main():
    today = dt.date.today().isoformat()
    args = [sys.executable, CHECK]
    if '--date' in sys.argv:
        args += ['--date', sys.argv[sys.argv.index('--date') + 1]]
    else:
        args += ['--date', today]
    started = dt.datetime.now().isoformat(timespec='seconds')
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
        tail = '\n'.join((p.stdout or '').strip().splitlines()[-3:])
        if p.returncode == 0:
            _log(f'[{started}] {today} PASS')
            print(f'验收 PASS（{today}）')
            return 0
        body = tail or (p.stderr or '')[-180:]
        _push(f'[验收值守] {today} 未过（exit={p.returncode}）', body)
        _log(f'[{started}] {today} FAIL exit={p.returncode} :: {body[:200]}')
        print(f'验收 FAIL（{today}）\n{body}')
        return 1
    except subprocess.TimeoutExpired:
        _push(f'[验收值守] {today} 验收脚本超时（>{TIMEOUT}s）', '值守异常，需人工查')
        _log(f'[{started}] {today} TIMEOUT')
        return 2
    except Exception as e:
        _push(f'[验收值守] {today} 值守自身异常', str(e))
        _log(f'[{started}] {today} WATCH-ERROR {e}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
