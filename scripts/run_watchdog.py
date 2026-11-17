#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""熔断/验卷看门狗（P1-⑦，2026-11-16）——对「正在跑的班」生效，不只下一班。

三职责（规格在派单 dq_fuse_verify_impl）：
  1. 熔断：活跃 run 复合判据触发（calls>100 或 share>0.4 且 calls>30）
     → SIGTERM openclaw CLI（wrapper §165 自愈：换新会话键重试一次，防双烧）
     → fuse_events 记失败样本 + notifications [熔断]（含 run_id + 调用分布）
  2. 心跳看门：活跃 run 最后事件静默 > 480s → 告警不中止（一条 run 只报一次）
  3. 验卷：run 静默 > 30 分钟后，tool.call 序列无任务书点名交付工具
     → 记「未交卷」+ notifications [未交卷]（三连事故三根因，不合并统计）

幂等：fuse_events 表 (run_id, kind) 唯一——重跑/多实例不重复通知。
部署：launchd com.solobrave.run-watchdog StartInterval 300（<480s 心跳腿）。

用法：
  python3 scripts/run_watchdog.py            # 正常一轮扫描
  python3 scripts/run_watchdog.py --dry      # 只判不杀不写通知（排障用）
"""
import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fuse_core as fc
import duty_notify as dn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, 'data', 'solobrave.db')
ADMIN_ID = 'user_7acb72ff'

_log_lines = []


def log(msg):
    line = f'[{datetime.now():%H:%M:%S}] {msg}'
    _log_lines.append(line)
    print(line)


def _now_ms():
    return int(time.time() * 1000)


def ensure_fuse_events(conn):
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS fuse_events (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            kind TEXT NOT NULL,               -- fuse / heartbeat / no_delivery
            reason TEXT NOT NULL DEFAULT '',
            acted INTEGER NOT NULL DEFAULT 0, -- 1 = 已中止进程（仅 fuse）
            created_at INTEGER NOT NULL,
            UNIQUE(run_id, kind)
        )''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_fuse_agent_time '
                 'ON fuse_events(agent_id, created_at)')
    conn.commit()


def already_recorded(conn, run_id, kind):
    row = conn.execute('SELECT 1 FROM fuse_events WHERE run_id = ? AND kind = ?',
                       (run_id, kind)).fetchone()
    return bool(row)


def record_event(conn, agent_id, run_id, kind, reason, acted=0):
    try:
        conn.execute(
            'INSERT INTO fuse_events (id, agent_id, run_id, kind, reason, acted, created_at)'
            ' VALUES (?,?,?,?,?,?,?)',
            (f'fe_{uuid.uuid4().hex[:12]}', agent_id, run_id, kind, reason, acted,
             _now_ms()))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # 同 run 同 kind 已记过（幂等）


def notify(conn, agent_id, kind_tag, title, content):
    """通知落库（notifications 链三天日报实证可靠）+ 桌面横幅（P1-⑧ 双通道）。
    失败只记日志不阻断。"""
    try:
        conn.execute(
            'INSERT INTO notifications (id, user_id, agent_id, type, title, content,'
            ' read, created_at) VALUES (?,?,?,?,?,?,0,?)',
            ('notif_' + uuid.uuid4().hex[:12], ADMIN_ID, agent_id,
             'task_reminder', title, content, _now_ms()))
        conn.commit()
        dn.desktop_push(title, content)   # P1-⑧：通知落库≠有人被推动，桌面才叫醒
        log(f'  通知已落库+桌面推 [{kind_tag}] {title}')
    except Exception as e:
        log(f'  通知写入失败（不阻断）: {e}')


def _file(conn, agent_id, cause, title, content):
    """P1-⑧：立案留痕 + 连击升级判定（通知本体由 notify 已双通道送达，
    这里 notify=False 防重复）。"""
    try:
        dn.ensure_case_tables(conn)
        out = dn.file_case(conn, agent_id, cause, title, content, notify=False)
        if out['escalated']:
            log(f'  [值班升级] {cause} 连击已直推老大')
    except Exception as e:
        log(f'  立案留痕失败（不阻断）: {e}')


def find_cli_pids(agent_id):
    try:
        out = subprocess.run(['ps', '-eo', 'pid,args'], capture_output=True,
                             text=True, timeout=15).stdout
    except Exception as e:
        log(f'  ps 失败: {e}')
        return []
    return fc.parse_openclaw_pids(out, agent_id)


def scan_agent(agent_id, cfg, conn, now_ms, dry, traj_db=None):
    label = cfg['label']
    runs = fc.load_runs(agent_id, db_path=traj_db)
    if not runs:
        log(f'{label}: 轨迹库空或不可读，跳过')
        return
    # 活跃面（1 小时内有事件）：熔断/心跳只看最新 run
    recent = {rid: r for rid, r in runs.items()
              if now_ms - r['last_ts'] < fc.RUN_STALE_MS}
    # 验卷面（6 小时静默窗口，逐 run 幂等）：覆盖今天各岗全部班——
    # 首部署不追诉历史（11-13 的班 >6h 自然排除），今天的班都在窗口内。
    verdict_pool = {rid: r for rid, r in runs.items()
                    if now_ms - r['last_ts'] < fc.VERDICT_RECENT_MS}

    if recent:
        rid = max(recent, key=lambda r: recent[r]['last_ts'])
        r = recent[rid]
        silent = now_ms - r['last_ts']
        ok, why = fc.verdict(r['calls'], r['search'])
        state = '已结束' if r['ended'] else f'活跃(静默{silent // 1000}s)'
        log(f'{label}: run {rid[:8]} {state} calls={r["calls"]} '
            f'search={r["search"]} → {why}')

        # ── 1. 熔断（只对活跃 run；已结束的 run 中止无意义，交给验卷/基线回放）──
        if not ok and not r['ended']:
            if already_recorded(conn, rid, 'fuse'):
                return  # 已熔断过，等 wrapper 重试班产生新 run
            pids = find_cli_pids(agent_id)
            if dry:
                log(f'  [DRY] 熔断条件命中，将 SIGTERM {pids or "(未找到进程)"}')
            else:
                acted = 0
                for pid in pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                        acted = 1
                        log(f'  已 SIGTERM pid={pid}（wrapper 将换新会话键重试一次）')
                    except Exception as e:
                        log(f'  SIGTERM pid={pid} 失败: {e}')
                if record_event(conn, agent_id, rid, 'fuse', why, acted):
                    notify(conn, agent_id, '熔断',
                           f'[熔断] {label}班被中止 · {datetime.now():%m月%d日}',
                           f'run {rid} 命中{why}，已强制中止（wrapper 自动换新会话键重试一次）。'
                           f'调用分布：{r["calls"]} 次 tool.call，其中 tool_search '
                           f'{r["search"]} 次。中止本身计入失败样本。')
                    _file(conn, agent_id, dn.map_fuse_reason(why),
                          f'[熔断] {label}班被中止', f'run {rid} 命中{why}')

        # ── 2. 心跳看门（活跃 + 静默 > 480s，告警不中止，一条 run 报一次）──
        elif not r['ended'] and silent > fc.HEARTBEAT_SILENT_MS:
            if record_event(conn, agent_id, rid, 'heartbeat',
                            f'静默{silent // 1000}s'):
                notify(conn, agent_id, '心跳',
                       f'[心跳] {label}班静默超 {fc.HEARTBEAT_SILENT_MS // 60000} 分钟',
                       f'run {rid} 最后事件距今 {silent // 1000}s，turn 可能挂起。'
                       f'当前 {r["calls"]} 次调用。只告警不中止。')
                _file(conn, agent_id, 'heartbeat_silent',
                      f'[心跳] {label}班静默超 {fc.HEARTBEAT_SILENT_MS // 60000} 分钟',
                      f'run {rid} 静默{silent // 1000}s')
    else:
        log(f'{label}: 无活跃 run')

    # ── 3. 验卷（静默 > 30 分钟才判，给「跑完正在投递」留窗口；逐 run 幂等）──
    for rid, r in verdict_pool.items():
        silent = now_ms - r['last_ts']
        if silent <= fc.DELIVERY_QUIET_MS:
            continue
        # 同 run 事故不重复立案：已熔断（wrapper 重试班才是交付主体）或
        # 已心跳告警（事故已在处理中）的 run，不再追「未交卷」
        if already_recorded(conn, rid, 'fuse') or already_recorded(conn, rid, 'heartbeat'):
            continue
        if already_recorded(conn, rid, 'no_delivery'):
            continue
        events = fc.load_tool_calls(agent_id, rid, db_path=traj_db)
        delivered = fc.detect_delivery_events(cfg['anchors'], events)
        if delivered:
            continue
        reason = 'tool.call 参数无交付锚点'
        if record_event(conn, agent_id, rid, 'no_delivery', reason):
            log(f'{label}: run {rid[:8]} 未交卷（{r["calls"]} 次调用无交付锚点）')
            notify(conn, agent_id, '未交卷',
                   f'[未交卷] {label}班未交卷 · {datetime.now():%m月%d日}',
                   f'run {rid} 已静默 {silent // 1000}s，{r["calls"]} 次调用的参数文本'
                   f'中无交付锚点（{cfg["anchors"]}）。'
                   f'按 §145：不看 turn 状态，只看交付命令真跑过没有。')
            _file(conn, agent_id, 'no_delivery',
                  f'[未交卷] {label}班未交卷', f'run {rid} 无交付锚点（{reason}）')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true', help='只判不杀不写通知')
    ap.add_argument('--db', default=DB, help='solobrave.db 路径（测试注入）')
    ap.add_argument('--now', type=int, default=None, help='冻结当前毫秒（测试注入）')
    args = ap.parse_args()

    now_ms = args.now or _now_ms()
    conn = sqlite3.connect(args.db, timeout=15)
    try:
        ensure_fuse_events(conn)
        for agent_id, cfg in fc.AGENTS.items():
            try:
                scan_agent(agent_id, cfg, conn, now_ms, args.dry)
            except Exception as e:
                log(f'{cfg["label"]}: 扫描异常（不阻断其他岗）: {e}')
    finally:
        conn.close()
    log('本轮扫描完成')
    return 0


if __name__ == '__main__':
    sys.exit(main())
