#!/usr/bin/env python3
"""明早 9:25 七行前置确认一键核（老大 11-13 22:37 铁律：现在就弄，不拖到明早）。

自动核 5 行 + 人工报 2 行，输出全绿才允许开跑。退出码 0 = 全绿，1 = 有红行。
⑦（P2-⑤ 三件套之①，11-17 加）= 达人端登录态班前探活：失效 → 当班跳过不烧积分，
先跑 `python3 scripts/daren_relogin.py`（半自动重登，人 1 分钟）。
用法：python3 preflight_925.py [--note "人工报：熔断验卷=未部署,聊天bug=revert不消失"]
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request

DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
PROD = '/Users/qichen/solobrave-prod'
results = []


def line(name, ok, detail, auto=True):
    results.append((name, ok, detail, auto))
    mark = '✅' if ok else '🔴'
    src = '自动' if auto else '人工'
    print(f'{mark} {name}（{src}）：{detail}')


def probe(url, method='GET', body=None, headers=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception as e:
        return -1, str(e).encode()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--note', default='', help='人工两行报数，格式 k=v 逗号分隔')
    args = ap.parse_args()
    manual = dict(kv.split('=', 1) for kv in args.note.split(',') if '=' in kv)

    # ① 身份总闸/越权焊点：agent 直连带 priority 必须 403
    code, _ = probe('http://localhost:8080/api/dispatch-queue', 'POST',
                    {'summary': '925前置探测', 'priority': 1},
                    {'X-Agent-Id': 'emp_xiaotuo'})
    line('① 优先级焊点（agent 直连 403）', code == 403, f'实测 HTTP {code}')
    for rid, probe_url in [('闸', 'http://localhost:8080/api/users'), ]:
        pass  # 总闸上线后在此追加 admin 端点探测

    # ② B 表 talent_recommendations
    db = sqlite3.connect(DB)
    tbls = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='talent_recommendations'")]
    line('② B 表已建', bool(tbls), 'talent_recommendations 存在' if tbls else '不存在，管线欠账')
    db.close()

    # ③ 熔断/验卷部署（P2-② 修真信号：launchd run-watchdog 在岗——git-log 代理上线次日即假红，作废）
    rc = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/com.solobrave.run-watchdog'],
                          capture_output=True, text=True).returncode
    fuse_landed = (rc == 0)
    line('③ 熔断/验卷已部署', fuse_landed,
         'run-watchdog 在岗（launchd 常驻，300s 扫）' if fuse_landed else 'run-watchdog 未加载——按「未部署人盯」口径')

    # ④ 积分看门脚本就位
    wp = os.path.join(PROD, 'scripts', 'watch_credits.py')
    line('④ 积分看门就位', os.path.exists(wp),
         os.path.exists(wp) and 'prod scripts/watch_credits.py 在' or '未移入 prod，人盯无工具')

    # ⑤ roles 补丁（buyin_worker 含角色选择分支）
    src = open(os.path.join(PROD, 'buyin_worker.py'), encoding='utf-8',
               errors='ignore').read()
    has = ('角色' in src and ('选择' in src or 'role' in src.lower()))
    line('⑤ worker 角色页补丁', has, 'buyin_worker 含角色选择处理（以执行方登记为准）')

    # ⑥ 聊天 bug（人工）
    v = manual.get('聊天bug', '')
    line('⑥ 聊天 bug 处置结论', v.lower() in ('已修', 'revert已验', '根因定位'),
         v or '待人工报数', auto=False)

    # ⑦ 达人端登录态班前探活（P2-⑤ 三件套之①，老大 11-17 19:46：
    #    失效直接跳过当班不烧积分——探活入口挂 preflight 9:25 档）
    #    注意：不能用 probe()——它 body 截断 [:200]，health JSON 会被切断。
    try:
        with urllib.request.urlopen('http://127.0.0.1:8095/health',
                                    timeout=5) as r7:
            st = json.loads(r7.read().decode('utf-8', errors='ignore'))
        up7 = bool(st.get('daren_browser_up')) and bool(st.get('daren_logged_in'))
        line('⑦ 达人端登录态探活', up7,
             'buyin_worker 在岗且 daren_logged_in=true' if up7 else
             '🔴 登录态失效/浏览器未起——当班跳过不烧积分，'
             '修：python3 scripts/daren_relogin.py（半自动重登，人 1 分钟）')
    except Exception as e:
        line('⑦ 达人端登录态探活', False,
             f'buyin_worker 控制口不可达——launchctl kickstart -k '
             f'gui/$(id -u)/com.solobrave.buyin-worker（{e}）')

    reds = [r for r in results if not r[1]]
    print(f'\n七行确认：{len(results) - len(reds)}/{len(results)} 绿'
          + (f'，🔴 {len(reds)} 行：' + '、'.join(r[0] for r in reds) if reds else '，可开跑'))
    sys.exit(1 if reds else 0)


if __name__ == '__main__':
    main()
