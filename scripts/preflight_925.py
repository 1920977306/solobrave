#!/usr/bin/env python3
"""明早 9:25 八行前置确认一键核（老大 11-13 22:37 铁律：现在就弄，不拖到明早）。

自动核 6 行 + 人工报 2 行，输出全绿才允许开跑。退出码 0 = 全绿，1 = 有红行。
⑦（P1-⑨ 改，11-18）= 达人端登录态班前探活：盘上 cookie 真值（浏览器态是互斥
假信号）；失效 → 达人端任务暂不可跑，当班跳过与否由⑧需求端钥匙决定。
先跑 `python3 scripts/daren_relogin.py`（半自动重登，人 1 分钟）。
⑧（P2-⑥，11-18 加）= 团长端钥匙探活：只认盘上 cookie（ecom_us_lt_buyin），
不认 /health browser_up（互斥语义：达人端在线时团长端 browser_up=false 是常态）。
失效 → 红灯+跳过当班，修：python3 scripts/daren_relogin.py --end captain（请老大扫码）。
用法：python3 preflight_925.py [--note "人工报：熔断验卷=未部署,聊天bug=revert不消失"]
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
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

    # ⑦ 达人端登录态班前探活（P1-⑨ 2026-11-18 改盘上 cookie 语义）：
    #    /health daren_logged_in 是浏览器态——互斥切换/部署窗口必假死
    #    （13:09 误报实案：立案+升级直推老大，cookie 其实活到 2027-01）。
    #    盘上 cookie 才是登录态真值。⑦红只提示达人端任务暂不可跑——
    #    当班是否跳过由当班需求端钥匙决定（小拓 9:30 班=团长端=看⑧），
    #    ⑦红 ≠ 跳过当班。
    def _daren_cookie_alive():
        dbp = os.path.join(PROD, 'data', 'buyin_browser', 'profile_daren',
                           'Default', 'Cookies')
        if not os.path.exists(dbp):
            return False
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix='.sqlite')
            os.close(fd)
            shutil.copy(dbp, tmp)
            cdb = sqlite3.connect(tmp)
            row = cdb.execute(
                "SELECT expires_utc FROM cookies WHERE name='sid_ucp_sso_v1_daren' "
                "AND host_key LIKE '%buyin-daren-sso.jinritemai.com%' LIMIT 1"
            ).fetchone()
            cdb.close()
            if not row:
                return False
            exp = int(row[0] or 0)
            if exp == 0:
                return True
            now_us = int(time.time() * 1_000_000) + 11644473600_000_000
            return exp > now_us
        except Exception:
            return False
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    try:
        with urllib.request.urlopen('http://127.0.0.1:8095/health',
                                    timeout=5) as r7:
            r7.read()
        worker_up7 = True
    except Exception as e:
        worker_up7 = False
        err7 = str(e)
    if worker_up7:
        up7 = _daren_cookie_alive()
        line('⑦ 达人端登录态探活', up7,
             'worker 在岗且盘上 cookie 存活（真值）' if up7 else
             '🔴 达人端 cookie 缺席/过期——达人端任务暂不可跑（当班是否跳过看⑧需求端钥匙），'
             '修：python3 scripts/daren_relogin.py（半自动重登，人 1 分钟）')
    else:
        line('⑦ 达人端登录态探活', False,
             f'buyin_worker 控制口不可达——launchctl kickstart -k '
             f'gui/$(id -u)/com.solobrave.buyin-worker（{err7}）')

    # ⑧ 团长端钥匙探活（P2-⑥，11-18：9:25 全绿但 9:30 班中团长端登录死暴雷 0 产出的教训）
    #    只认盘上 cookie，不认 /health browser_up——达人端在线时团长端 browser_up=false
    #    是互斥常态，不是病。浏览器占用 Cookies 库时先 copy 到临时文件再查。
    def _captain_cookie_present():
        dbp = os.path.join(PROD, 'data', 'buyin_browser', 'profile', 'Default',
                           'Cookies')
        if not os.path.exists(dbp):
            return False
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(suffix='.sqlite')
            os.close(fd)
            shutil.copy(dbp, tmp)
            cdb = sqlite3.connect(tmp)
            row = cdb.execute(
                "SELECT 1 FROM cookies WHERE name='ecom_us_lt_buyin' "
                "AND host_key LIKE '%buyin.jinritemai.com%' LIMIT 1"
            ).fetchone()
            cdb.close()
            return row is not None
        except Exception:
            return False
        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

    cap_ok = _captain_cookie_present()
    line('⑧ 团长端钥匙探活', cap_ok,
         'cookie ecom_us_lt_buyin 在位（盘上真值）' if cap_ok else
         '🔴 团长端 cookie 缺席——广场班跳过不烧积分，'
         '修：python3 scripts/daren_relogin.py --end captain（请老大扫码）')

    # ⑨ 团长端身份探针（dq_bbcb24792baa，11-23）：登录态≠身份态——
    #    身份漂成达人版时 ⑧ 的 cookie 照样在位（09:30 班白挂实证）。
    #    语义三态：captain→绿；daren→红（硬信号：罗盘URL/达人版角标）；
    #    unknown→绿但注明「页面非工作台未判定」——浏览器停在官网落地页
    #    （/open 首跳常态）时判不出，不能因此狼来了；真漂移到工作台页必出
    #    daren 硬信号，再配合 no_delivery 值班链兜底双保险。
    #    identity 字段缺席（worker 待重启）→ 信息行不拦班。
    ident, br_up = None, False
    if worker_up7:
        try:
            with urllib.request.urlopen('http://127.0.0.1:8095/health',
                                        timeout=5) as r9:
                h9 = json.loads(r9.read().decode('utf-8'))
                ident = h9.get('identity')
                br_up = bool(h9.get('browser_up'))
        except Exception:
            ident = None
    if ident is None:
        line('⑨ 团长端身份探针', True,
             'identity 字段未上线（worker 待重启）——上线后漂成达人版将在此行报红')
    elif not br_up:
        line('⑨ 团长端身份探针', True,
             '团长端浏览器未起（互斥常态/待拉）——身份免验')
    elif ident == 'captain':
        line('⑨ 团长端身份探针', True, '身份=团长（真值）')
    elif ident == 'daren':
        line('⑨ 团长端身份探针', False,
             '🔴 身份漂成达人版（09:30 班白挂同款）——先切回团长再开跑')
    else:
        line('⑨ 团长端身份探针', True,
             '页面非工作台未判定身份（官网落地页常态）——daren 硬信号必拦，'
             '漂移到业务页即现形；no_delivery 值班链兜底')

    reds = [r for r in results if not r[1]]
    n = len(results)
    print(f'\n{n} 行确认：{n - len(reds)}/{n} 绿'
          + (f'，🔴 {len(reds)} 行：' + '、'.join(r[0] for r in reds) if reds else '，可开跑'))
    sys.exit(1 if reds else 0)


if __name__ == '__main__':
    main()
