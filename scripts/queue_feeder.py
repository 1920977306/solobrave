#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""queue_feeder.py — 30 分钟巡检-派单流水线之发现层（老大 11-18「传送带」设计）。

每 30 分钟（错峰分）跑一轮**确定性**五路扫描（纯代码零积分）：
  ① DB 卫生（空壳漂移/脏值/口径分叉）  ② 日志异常（签名/风控/静默故障）
  ③ 机制健康（launchd 岗位/告警疲劳）  ④ 数据新鲜度（B 表/商品/达人锚）
  ⑤ 积分曲线（无 session 匀速烧分形态）
        │
        ▼
  产出 ≤5 个候选单 → data/queue_feeder_last.json（本轮）+
  data/queue_feeder_candidates.jsonl（审计追加），每条含
  id/优先级/证据/建议验收口径/fingerprint。

分流闸（老大设计，写死）：
  P1 红线类 → 入候选 + desktop 即时推送（duty_notify 双通道）
  P2 施工类 → 入候选，A 边按优先级拉单（守卫+回归+第四方验收不变）
  P3 信息类 → 入候选留档，不施工

去重：fingerprint 冷却（默认 24h 同 key 不重出，P1 不受冷却限制）+
与 dispatch_queue 开放单（待领/施工/待验）锚点包含比对，防队列爆炸。

判据所有权：协调边定稿五路各 3-5 条（ tonight ），本文件首批判据为
机械化底座，定稿后增量替换 JUDGES 表，勿在 main() 里加临时逻辑。

自验证：python3 scripts/queue_feeder.py --selftest
  造 3 类假异常喂给判据函数必须全检出 + 干净对照组必须全阴性（防恒真）。

用法：
  python3 scripts/queue_feeder.py            # 正常一轮
  python3 scripts/queue_feeder.py --selftest # 离线自检（不触 DB/网络）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duty_notify as dn  # noqa: E402  P1 即时推送复用值班通知链

DB = os.environ.get('SOLO_BRAVE_DB_SOURCE',
                    os.path.join(ROOT, 'data', 'solobrave.db'))
OUT_LAST = os.path.join(ROOT, 'data', 'queue_feeder_last.json')
OUT_AUDIT = os.path.join(ROOT, 'data', 'queue_feeder_candidates.jsonl')
EMITTED = os.path.join(ROOT, 'data', 'watchdog-markers',
                       'queue_feeder_emitted.json')
WORKER_LOG = os.path.join(ROOT, 'data', 'buyin_browser',
                          'launchd.out.log')
SERVER_LOG = os.path.join(ROOT, 'logs', 'server.log')
COOLDOWN_MS = 24 * 3600 * 1000        # 同 fingerprint 冷却（P1 豁免）
MAX_PER_ROUND = 5                    # 老大设计钉死 ≤5/轮
EXPECTED_LAUNCHD_JOBS = 12           # 机制健康基线岗位数

P1, P2, P3 = 'P1', 'P2', 'P3'


def _cand(key, priority, title, evidence, accept, anchors):
    """候选单规范化（anchors = 去重锚点词列表，开放单摘要含任一即视为重复）。"""
    fp = hashlib.sha1(key.encode()).hexdigest()[:12]
    return {'id': f'qf_{int(time.time() * 1000)}_{fp}', 'key': key,
            'fingerprint': fp, 'priority': priority, 'title': title,
            'evidence': evidence, 'suggested_acceptance': accept,
            'anchors': anchors, 'created_at': int(time.time() * 1000)}


# ─────────────────────────── 判据层（纯函数，可注入，selftest 主战场） ───────────────────────────

def judge_db_hygiene(rows, now_ms):
    """① DB 卫生。rows = [(active_cnt, hollow_cnt, bad_recdate_cnt)]，单元素即当前快照。

    判据（首批，机械化）：空壳率 ≥20% → P3 信息；B 表 rec_date 非 YYYY-MM-DD → P2。
    """
    out = []
    if not rows:
        return out
    active, hollow, bad_rd = rows[0]
    if active and hollow / max(active, 1) >= 0.2:
        out.append(_cand(
            'db_hollow_rate', P3, f'空壳率漂移：{hollow}/{active} '
            f'({hollow * 100 // max(active, 1)}%)',
            f'talents 全 active={active}，核心五字段全空 hollow={hollow}',
            '口径可计算+隔离落地后报真实空壳率（P2-① 延伸）',
            ['空壳']))
    if bad_rd:
        out.append(_cand(
            'db_recdate_fmt', P2, f'B 表 rec_date 口径分叉：{bad_rd} 行非 YYYY-MM-DD',
            f'talent_recommendations 中 rec_date 非日期格式 {bad_rd} 行',
            '存量迁移完成 + check_daily_recommendations.py 判据②直等匹配',
            ['rec_date', 'talent_recommendations']))
    return out


def _recent_lines(lines, now_ms, max_age_ms=2 * 3600 * 1000):
    """按行首 [MM-DD HH:MM:SS] 时间戳过滤最近 max_age（字节 tail 不是时间窗——
    09:39 旧风控码在 17:00 的 tail 里仍会被误当新事件，首轮实测修正）。"""
    cutoff = datetime.fromtimestamp((now_ms - max_age_ms) / 1000)
    out = []
    for l in lines:
        try:
            head = l.split(']', 1)[0].strip('[')
            mo, rest = head.split('-', 1)
            day, hm = rest.split(' ', 1)
            parts = hm.split(':')
            h, mi = parts[0], parts[1]
            sec = parts[2] if len(parts) > 2 else '0'
            ts = datetime(datetime.now().year, int(mo), int(day),
                          int(h), int(mi), int(sec))
            if ts >= cutoff:
                out.append(l)
        except Exception:
            continue
    return out


def judge_log_anomalies(worker_tail, server_tail, window_desc, now_ms=None):
    """② 日志异常。worker_tail/server_tail = 最近窗口日志行列表。

    判据：风控码 10001010A/11001（最近 2h）→ P1；server Traceback ≥3 → P2。
    """
    out = []
    worker_tail = _recent_lines(worker_tail or [], now_ms or int(time.time() * 1000))
    server_tail = _recent_lines(server_tail or [], now_ms or int(time.time() * 1000))
    risk = [l for l in worker_tail
            if '10001010A' in l or '11001' in l]
    if risk:
        out.append(_cand(
            'log_risk_code', P1,
            f'风控码 {len(risk)} 次（{window_desc}）——立即核对是否页面外重放',
            '；'.join(r.strip()[:120] for r in risk[:3]),
            '官方页面内路径复扫零码（P2-⑦ 纪律断言）', ['风控', '10001010A']))
    tb = [l for l in server_tail if 'Traceback' in l]
    if len(tb) >= 3:
        out.append(_cand(
            'log_server_traceback', P2,
            f'server.log Traceback ×{len(tb)}（{window_desc}）',
            '；'.join(l.strip()[:100] for l in tb[:2]),
            '根因行定位+回归零新增 Traceback', ['Traceback']))
    return out


def judge_mechanism_health(loaded_jobs, alert_count_24h):
    """③ 机制健康。loaded_jobs = launchd 在岗岗位数；alert_count_24h = 值班通知数。

    判据：岗位 < 基线 → P1；告警 24h >10 → P2（疲劳阈值漂移）。
    """
    out = []
    if loaded_jobs < EXPECTED_LAUNCHD_JOBS:
        out.append(_cand(
            'mech_launchd_gap', P1,
            f'launchd 岗位 {loaded_jobs}/{EXPECTED_LAUNCHD_JOBS}——机制掉岗',
            f'com.solobrave.* 在岗 {loaded_jobs} 个，低于基线 '
            f'{EXPECTED_LAUNCHD_JOBS}',
            '岗位补齐 + 连续两轮在位', ['launchd', '岗位']))
    if alert_count_24h > 10:
        out.append(_cand(
            'mech_alert_fatigue', P2,
            f'告警疲劳：24h [值班] 通知 {alert_count_24h} 条（阈值 10）',
            'notifications 24h 值班类计数超阈——连击判据或沿检测在抖动',
            '24h 计数回落 ≤10 + 无重复 case 指纹', ['告警疲劳', '值班']))
    return out


def judge_data_freshness(rec_today_cnt, collect_last_ms, now_ms):
    """④ 数据新鲜度。rec_today = B 表今日行数；collect_last_ms = 达人采集锚。

    判据：10:00 后 B 表今日 0 行 → P2；采集锚 >48h → P2。
    """
    out = []
    dt = datetime.fromtimestamp(now_ms / 1000)
    if dt.hour >= 10 and rec_today_cnt == 0:
        out.append(_cand(
            'fresh_rec_empty', P2,
            'B 表今日 0 推荐（10:00 后）——选达人线断粮',
            f'rec_date={dt:%Y-%m-%d} 行数 0；连续零推荐触发升级链',
            'square-scan 官方路径一班产出 ≥1 落 B 表', ['B 表', '零推荐']))
    if collect_last_ms and now_ms - collect_last_ms > 48 * 3600 * 1000:
        hrs = (now_ms - collect_last_ms) // 3600000
        out.append(_cand(
            'fresh_collect_stale', P2,
            f'达人采集锚停摆 {hrs}h+（>48h 阈值）',
            f'talent_collects 最后成功距今 {hrs}h',
            '采集锚出现当日成功记录（P2-⑤ 验收口径）',
            ['采集', 'talent_collects']))
    return out


def judge_credit_curve(nosession_rows_1h, now_ms):
    """⑤ 积分曲线。nosession_rows_1h = 最近 1h 无 session consumption 行数。

    判据（Helen 泄漏形态锁死）：≥30 行/h → P1 止血。
    """
    out = []
    if nosession_rows_1h >= 30:
        out.append(_cand(
            'credit_nosession_burn', P1,
            f'无 session 匀速烧分 {nosession_rows_1h} 行/h——泄漏形态',
            'consumption 无 session_id 行 ≥30/h（input 恒 208 指纹同源）',
            '止血守卫落库 quarantined=1 + 根因单闭环', ['烧分', '无 session']))
    return out


# ─────────────────────────── 采集层（真实世界探针，selftest 不触） ───────────────────────────

def _tail(path, max_bytes=200_000):
    try:
        with open(path, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode('utf-8', errors='ignore').splitlines()
    except Exception:
        return []


def collect_inputs():
    """五路探针聚合（真实跑时调用；--selftest 绝不进这里）。"""
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(DB, timeout=15)
    try:
        active = conn.execute(
            "SELECT COUNT(*) FROM talents WHERE status='active'").fetchone()[0]
        hollow = conn.execute(
            "SELECT COUNT(*) FROM talents WHERE status='active' AND "
            "COALESCE(name,'')='' AND COALESCE(douyin_id,'')='' AND "
            "COALESCE(followers,'')='' AND COALESCE(total_gmv,'')='' AND "
            "COALESCE(main_category,'')=''").fetchone()[0]
        bad_rd = conn.execute(
            "SELECT COUNT(*) FROM talent_recommendations WHERE rec_date "
            "NOT GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'").fetchone()[0]
        rec_today = conn.execute(
            "SELECT COUNT(*) FROM talent_recommendations WHERE rec_date=?",
            (datetime.now().strftime('%Y-%m-%d'),)).fetchone()[0]
        alerts = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE title LIKE '%值班%' "
            "AND created_at > ?", (now_ms - 24 * 3600 * 1000,)).fetchone()[0]
        # credit_usage_log.created_at 是字符串 'YYYY-MM-DD HH:MM:SS'
        since = datetime.fromtimestamp(now_ms / 1000 - 3600).strftime(
            '%Y-%m-%d %H:%M:%S')
        nosess = conn.execute(
            "SELECT COUNT(*) FROM credit_usage_log WHERE reason='consumption' "
            "AND (session_id IS NULL OR session_id='') AND created_at >= ?",
            (since,)).fetchone()[0]
        c_last = conn.execute(
            "SELECT MAX(created_at) FROM talent_collects WHERE "
            "status='done'").fetchone()[0]
    finally:
        conn.close()
    try:
        lp = subprocess.run(['launchctl', 'list'], capture_output=True,
                            text=True, timeout=10).stdout
        # 在岗 = 标签已加载（含 Interval 睡眠态，PID '-' 不算掉岗）；
        # running 数另行打印供观察，不作 P1 判据（错峰睡眠是设计态）。
        loaded = sum(1 for l in lp.splitlines() if 'com.solobrave.' in l)
        running = sum(1 for l in lp.splitlines()
                      if 'com.solobrave.' in l and not l.startswith('-'))
        print(f'[feeder] launchd 已加载 {loaded} / running {running}')
    except Exception:
        loaded = EXPECTED_LAUNCHD_JOBS  # 探不出不谎报掉岗
    return {
        'db_rows': [(active, hollow, bad_rd)],
        'worker_tail': _tail(WORKER_LOG),
        'server_tail': _tail(SERVER_LOG),
        'window_desc': '最近 200KB 窗口',
        'loaded_jobs': loaded,
        'alert_count_24h': alerts,
        'rec_today_cnt': rec_today,
        'collect_last_ms': c_last or 0,
        'now_ms': now_ms,
        'nosession_rows_1h': nosess,
    }


# ─────────────────────────── 去重与输出 ───────────────────────────

def _load_emitted():
    try:
        with open(EMITTED, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_emitted(state):
    os.makedirs(os.path.dirname(EMITTED), exist_ok=True)
    with open(EMITTED, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def _open_queue_anchors():
    """dispatch_queue 开放单摘要集合（去重比对用，只读）。"""
    try:
        conn = sqlite3.connect(DB, timeout=15)
        try:
            rows = conn.execute(
                "SELECT summary FROM dispatch_queue WHERE status IN "
                "('待领','施工','待验')").fetchall()
        finally:
            conn.close()
        return [r[0] or '' for r in rows]
    except Exception:
        return []


def dedup(cands, emitted, open_summaries, now_ms):
    """冷却 + 开放单锚点包含比对。P1 不受冷却限制（红线每次必出）。"""
    out = []
    for c in cands:
        if any(a and a in s for s in open_summaries for a in c['anchors']):
            continue  # 队列里已有同锚点开放单
        if c['priority'] != P1:
            last = emitted.get(c['key'], 0)
            if now_ms - last < COOLDOWN_MS:
                continue
        emitted[c['key']] = now_ms
        out.append(c)
    return out


def run_round(inputs=None):
    inputs = inputs or collect_inputs()
    cands = []
    cands += judge_db_hygiene(inputs['db_rows'], inputs['now_ms'])
    cands += judge_log_anomalies(inputs['worker_tail'], inputs['server_tail'],
                                 inputs['window_desc'], inputs['now_ms'])
    cands += judge_mechanism_health(inputs['loaded_jobs'],
                                    inputs['alert_count_24h'])
    cands += judge_data_freshness(inputs['rec_today_cnt'],
                                  inputs['collect_last_ms'], inputs['now_ms'])
    cands += judge_credit_curve(inputs['nosession_rows_1h'], inputs['now_ms'])
    cands.sort(key=lambda c: c['priority'])
    cands = cands[:MAX_PER_ROUND]

    emitted = _load_emitted()
    fresh = dedup(cands, emitted, _open_queue_anchors(), inputs['now_ms'])
    _save_emitted(emitted)

    rnd = {'round_at': inputs['now_ms'],
           'round_at_human': datetime.now().isoformat(timespec='seconds'),
           'candidates': fresh, 'suppressed': len(cands) - len(fresh)}
    with open(OUT_LAST, 'w', encoding='utf-8') as f:
        json.dump(rnd, f, ensure_ascii=False, indent=1)
    os.makedirs(os.path.dirname(OUT_AUDIT), exist_ok=True)
    with open(OUT_AUDIT, 'a', encoding='utf-8') as f:
        for c in fresh:
            f.write(json.dumps(c, ensure_ascii=False) + '\n')

    for c in fresh:
        print(f"[{c['priority']}] {c['title']}")
        if c['priority'] == P1:
            dn.desktop_push(f'[传送带] {c["title"]}', c['evidence'][:180])
    print(f'\n本轮候选 {len(fresh)} 个（压制 {rnd["suppressed"]} 个重复），'
          f'落库 {OUT_LAST}')
    return rnd


# ─────────────────────────── 自验证 ───────────────────────────

def selftest():
    """3 类假异常必须全检出 + 干净对照组必须全阴性（防恒真）。"""
    now_ms = int(time.time() * 1000)
    fail = []

    # 假异常组
    cases = [
        ('风控码', judge_log_anomalies(
            [f'[{datetime.now():%m-%d %H:%M:%S}] code=10001010A 风险'],
            [], 'selftest'), 'log_risk_code'),
        ('烧分', judge_credit_curve(45, now_ms), 'credit_nosession_burn'),
        ('掉岗', judge_mechanism_health(9, 0), 'mech_launchd_gap'),
    ]
    for name, got, key in cases:
        if not any(c['key'] == key for c in got):
            fail.append(f'{name} 假异常未检出')

    # 干净对照组（全阴性才算不恒真）
    clean = []
    clean += judge_db_hygiene([(181, 5, 0)], now_ms)
    clean += judge_log_anomalies([], [], 'clean')
    clean += judge_mechanism_health(EXPECTED_LAUNCHD_JOBS, 3)
    clean += judge_data_freshness(2, now_ms - 3600 * 1000, now_ms)
    clean += judge_credit_curve(0, now_ms)
    if clean:
        fail.append(f'干净对照组误报: {[c["key"] for c in clean]}')

    # 边界：空输入不得爆炸
    if judge_db_hygiene([], now_ms):
        fail.append('空 db_rows 出单')

    if fail:
        print('SELFTEST FAIL: ' + '；'.join(fail))
        return 1
    print('SELFTEST OK：3 类假异常全检出 + 干净对照组全阴性 + 空输入安全')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    rnd = run_round()
    sys.exit(0 if rnd is not None else 1)


if __name__ == '__main__':
    main()
