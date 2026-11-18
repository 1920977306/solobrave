#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""queue_feeder.py — 30 分钟巡检-派单流水线之发现层（老大 11-18「传送带」设计）。

每 30 分钟（锚 :07/:37 错峰）跑一轮**确定性**五路扫描（纯代码零积分），产出
≤5 个候选单 → data/queue_feeder_last.json + queue_feeder_candidates.jsonl。
分流闸（写死）：P1 红线 → 候选 + desktop 即时推送（不受冷却，每次必出）；
P2 施工 → 排队（守卫+回归+第四方验收质量闸不动）；P3 信息 → 留档不施工。

判据清单 v1（2026-11-18 协调边定稿，五路 21 条，编号 1-1…5-4 与清单一一
对应；探针拿不到的锚「宁缺不编造」——judge 纯函数留 selftest 对照）。

自验证 --selftest：21 条判据各 1 正 1 负对照（替换判据后重跑必须仍全绿）。
用法：python3 scripts/queue_feeder.py [--selftest]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import duty_notify as dn  # noqa: E402  P1 即时推送复用值班通知链

DB = os.environ.get('SOLO_BRAVE_DB_SOURCE',
                    os.path.join(ROOT, 'data', 'solobrave.db'))
OUT_LAST = os.path.join(ROOT, 'data', 'queue_feeder_last.json')
OUT_AUDIT = os.path.join(ROOT, 'data', 'queue_feeder_candidates.jsonl')
MARKERS = os.path.join(ROOT, 'data', 'watchdog-markers')
EMITTED = os.path.join(MARKERS, 'queue_feeder_emitted.json')
WORKER_LOG = os.path.join(ROOT, 'data', 'buyin_browser', 'launchd.out.log')
SERVER_LOG = os.path.join(ROOT, 'logs', 'server.log')
COOLDOWN_MS = 24 * 3600 * 1000
MAX_PER_ROUND = 5
EXPECTED_LAUNCHD_JOBS = 12
HOUR = 3600 * 1000

P1, P2, P3 = 'P1', 'P2', 'P3'


def _cand(no, key, priority, title, evidence, accept):
    fp = hashlib.sha1(f'{no}:{key}'.encode()).hexdigest()[:12]
    return {'id': f'qf_{int(time.time() * 1000)}_{fp}', 'key': key,
            'judge': no, 'fingerprint': fp, 'priority': priority,
            'title': title, 'evidence': evidence,
            'suggested_acceptance': accept,
            'anchors': [no, key], 'created_at': int(time.time() * 1000)}


# ═════════════════ Lane 1 DB卫生 ═════════════════

def judge_1_1(shell_today, shell_yesterday):
    """1-1 P2 空壳率单日漂移>5pp（ talent_shell 口径：核心五字段全空/active）。"""
    if shell_yesterday is None:
        return []  # 无昨日快照，首跑只建基线不出单
    drift = shell_today - shell_yesterday
    if abs(drift) > 5.0:
        return [_cand('1-1', 'db_shell_drift', P2,
                      f'空壳率单日漂移 {drift:+.1f}pp（{shell_yesterday:.1f}%→'
                      f'{shell_today:.1f}%）',
                      f'talent_shell 口径：核心五字段全空/active，阈值 ±5pp',
                      '漂移归因（采集断供/批量入库/口径变化）+ 真实空壳率日报')]
    return []


def judge_1_2(conv_notnull_cnt):
    """1-2 P1 products.conversion_rate 非 NULL（假值回归——历史清过一次）。"""
    if conv_notnull_cnt > 0:
        return [_cand('1-2', 'db_conv_rate', P1,
                      f'products.conversion_rate 假值回归：{conv_notnull_cnt} 行非 NULL',
                      '历史清过一次；非 NULL 即上游又在写假转化率',
                      '假值清空 + 写入口封堵 + 回归零新增')]
    return []


def judge_1_3(new_unadjudicated_dupes):
    """1-3 P2 撞名组新增未裁决（与已裁决基线比对，新增组即报）。"""
    if new_unadjudicated_dupes:
        names = '、'.join(new_unadjudicated_dupes[:5])
        return [_cand('1-3', 'db_name_dupe', P2,
                      f'撞名组新增 {len(new_unadjudicated_dupes)} 组未裁决：{names}',
                      '同名 active 组不在已裁决基线内（11-15 旧案 4 组已裁）',
                      '逐组裁决（同一人合并/不同人标注）+ 基线更新')]
    return []


def judge_1_4(bad_pipeline_cnt):
    """1-4 P2 B 表当日行 pipeline_status≠recommended（口径分叉）。"""
    if bad_pipeline_cnt > 0:
        return [_cand('1-4', 'db_rec_status', P2,
                      f'B 表当日 {bad_pipeline_cnt} 行 pipeline_status≠recommended',
                      '入库即待商务跟进；非 recommended 即口径分叉',
                      '状态值归一 + check_daily_recommendations.py 直等匹配')]
    return []


def judge_1_5(bad_douyin_new_cnt):
    """1-3→1-5 P3 新增 douyin_id 不吻合三类值形态（平台id v2_*/纯数字/脏值特征）。"""
    if bad_douyin_new_cnt > 0:
        return [_cand('1-5', 'db_douyin_fmt', P3,
                      f'今日新增 {bad_douyin_new_cnt} 行 douyin_id 不吻合三类形态',
                      '平台 id（v2_ 前缀）/纯抖音号/脏值特征三类之外即异常写入',
                      '脏值行清单 + 写入入口校验')]
    return []


# ═════════════════ Lane 2 日志异常（时间戳窗，禁字节 tail——今日教训写死） ═════════════════

_THREAD_SICK_SIG = 'Sync API inside the asyncio loop'


def _parse_ts_line(line, now_ms, max_age_ms):
    """行首 [MM-DD HH:MM:SS] → 毫秒；解析失败/超窗 → None。"""
    try:
        head = line.split(']', 1)[0].strip('[')
        mo, rest = head.split('-', 1)
        day, hm = rest.split(' ', 1)
        parts = hm.split(':')
        h, mi = parts[0], parts[1]
        sec = parts[2] if len(parts) > 2 else '0'
        ts = datetime(datetime.now().year, int(mo), int(day),
                      int(h), int(mi), int(sec))
        ms = int(ts.timestamp() * 1000)
        return ms if now_ms - ms <= max_age_ms else None
    except Exception:
        return None


def _recent_lines(lines, now_ms, max_age_ms):
    return [l for l in (lines or [])
            if _parse_ts_line(l, now_ms, max_age_ms) is not None]


def judge_2_1(worker_lines_2h):
    """2-1 P1 风控码真新事件（时间戳 2h 窗内）。"""
    risk = [l for l in worker_lines_2h
            if '10001010A' in l or '11001' in l]
    if risk:
        return [_cand('2-1', 'log_risk_code', P1,
                      f'风控码 {len(risk)} 次（2h 窗内真新事件）',
                      '；'.join(r.strip()[:120] for r in risk[:3]),
                      '官方页面内路径复扫零码（P2-⑦ 纪律断言）')]
    return []


def judge_2_2(worker_lines_2h):
    """2-2 P1 线程病签名「Sync API inside the asyncio loop」新实例。"""
    sick = [l for l in worker_lines_2h if _THREAD_SICK_SIG in l]
    if sick:
        return [_cand('2-2', 'log_thread_sick', P1,
                      f'worker 线程病签名 {len(sick)} 次（2h 窗）',
                      'asyncio 循环污染实锤签名；worker_thread_sick 立案+kickstart',
                      '自动 kickstart 生效 + probe 恢复非空')]
    return []


def judge_2_3(server_lines_10m):
    """2-3 P2 server.log 10 分钟内 ChatPOST 异常≥3 次。"""
    bad = [l for l in server_lines_10m
           if 'ChatPOST' in l and ('error' in l.lower() or '异常' in l)]
    if len(bad) >= 3:
        return [_cand('2-3', 'log_chatpost_err', P2,
                      f'ChatPOST 异常 {len(bad)} 次/10 分钟',
                      '；'.join(b.strip()[:100] for b in bad[:2]),
                      '根因行定位 + 回归零新增')]
    return []


# 已知告警语义表（exit≠0 但属设计）：
# - daren-login-watch exit 1 = 探得死态（P1-⑨ 查明）
# - 全局 -15 = SIGTERM：kickstart -k 维护动作的固定语义（11-18 施工实测），
#   非异常；真异常 kill（如 jetsam）会另有日志签名，走 2-1/2-2 判据
_KNOWN_ALERT_EXITS = {'com.solobrave.daren-login-watch': {0, 1, -15},
                      '*': {0, -15}}


def judge_2_4(launchd_rows):
    """2-4 P2 launchd 岗位 last exit ∉ {0, 已知告警语义表}。

    launchd_rows = [(label, last_exit_str)]，'-' = 无记录（正常）。
    """
    out = []
    for label, last in launchd_rows:
        if last in ('-', '', None):
            continue
        try:
            code = int(last)
        except ValueError:
            continue
        known = _KNOWN_ALERT_EXITS.get(label, _KNOWN_ALERT_EXITS['*'])
        if code not in known:
            out.append(_cand('2-4', f'exit_{label}', P2,
                             f'{label} last exit={code}（非设计语义）',
                             f'已知告警语义表={sorted(known)}，实测 {code}',
                             'exit 归因 + 语义表更新或异常修复'))
    return out


def judge_2_5(class_burn_vs_avg):
    """2-5 P1 单班积分消耗>3× 日均。class_burn_vs_avg = [(agent, cls, avg)]。"""
    out = []
    for agent, cls, avg in class_burn_vs_avg:
        if avg and cls > 3 * avg:
            out.append(_cand('2-5', f'burn_{agent}', P1,
                             f'{agent} 单班烧分 {cls} > 3× 日均 {avg:.0f}',
                             '燃烧异常形态；对照 Helen 泄漏案指纹',
                             '轨迹复合判据复核 + 止血守卫就绪'))
    return out


# ═════════════════ Lane 3 机制健康 ═════════════════

def judge_3_1(loaded_jobs):
    """3-1 P1 已加载 launchd 标签数<12（加载数≠running——错峰睡眠是设计态）。"""
    if loaded_jobs < EXPECTED_LAUNCHD_JOBS:
        return [_cand('3-1', 'mech_launchd_gap', P1,
                      f'launchd 已加载 {loaded_jobs}/{EXPECTED_LAUNCHD_JOBS}——掉岗',
                      f'com.solobrave.* 已加载 {loaded_jobs} 个',
                      '岗位补齐 + 连续两轮在位')]
    return []


def judge_3_2(worker_reachable, unreachable_since_ms, now_ms):
    """3-2 P1 worker /health 不可达>5min。unreachable_since_ms=None 表示当前可达。"""
    if worker_reachable:
        return []
    if unreachable_since_ms and now_ms - unreachable_since_ms > 5 * 60 * 1000:
        mins = (now_ms - unreachable_since_ms) // 60000
        return [_cand('3-2', 'mech_worker_down', P1,
                      f'worker /health 不可达 {mins} 分钟',
                      '8095 控制口持续无响应（>5min 阈值）',
                      'kickstart 恢复 + probe 在岗')]
    return []  # 刚断 <5min：瞬断容忍，等下轮


def judge_3_3(duty_cases_24h):
    """3-3 P2 duty_cases 24h 立案>3（告警疲劳线）。"""
    if duty_cases_24h > 3:
        return [_cand('3-3', 'mech_case_flood', P2,
                      f'24h 立案 {duty_cases_24h} 起（疲劳线 3）',
                      'duty_cases 24h 计数超阈——判据在抖动或真连环事故',
                      '指纹归并检查 + 连击判据复核')]
    return []


def judge_3_4(fuse_events_today):
    """3-4 P2 run-watchdog 当日熔断/验卷触发（每次触发都出单，防静默）。"""
    if fuse_events_today > 0:
        return [_cand('3-4', 'mech_fuse_events', P2,
                      f'当日熔断/验卷触发 {fuse_events_today} 次',
                      'fuse_events 当日计数——每次触发都应有人复盘',
                      '当次 run 轨迹回放 + 根因登记')]
    return []


def judge_3_5(kick_recent):
    """3-5 P3 哨兵 kickstart 动作发生（自愈动过手要留痕可见）。"""
    if kick_recent:
        return [_cand('3-5', 'mech_kickstart', P3,
                      '哨兵 kickstart 自愈动作发生（留痕）',
                      'worker-kick.marker 24h 内存在',
                      '留档可见即可（P3 不施工）')]
    return []


# ═════════════════ Lane 4 数据新鲜度 ═════════════════

def judge_4_1(collect_age_h):
    """4-1 P2 talent_collects 锚>48h。"""
    if collect_age_h is not None and collect_age_h > 48:
        return [_cand('4-1', 'fresh_collect', P2,
                      f'达人采集锚停摆 {collect_age_h}h+',
                      f'talent_collects 最后成功距今 {collect_age_h}h',
                      '采集锚出现当日成功记录')]
    return []


def judge_4_2(product_sync_age_h):
    """4-2 P2 商品 daren_synced_at 锚>24h。"""
    if product_sync_age_h is not None and product_sync_age_h > 24:
        return [_cand('4-2', 'fresh_product', P2,
                      f'商品达人端同步锚 {product_sync_age_h}h+',
                      'products.daren_synced_at 最后同步超 24h',
                      '回源同步成功锚 <24h')]
    return []


def judge_4_3(now_hour, rec_today, is_workday):
    """4-3 P2 工作日 18:00 后 B 表当日 0 行（小拓班没交卷）。"""
    if is_workday and now_hour >= 18 and rec_today == 0:
        return [_cand('4-3', 'fresh_no_rec', P2,
                      '工作日 18:00 后 B 表今日 0 推荐——班没交卷',
                      'rec_date=今日 行数 0',
                      'square-scan 一班产出 ≥1 落 B 表')]
    return []


def judge_4_4(null_runid_cnt):
    """4-4 P1 talent_recommendations 存在 run_id 空行（P2-④ 口径违规）。"""
    if null_runid_cnt > 0:
        return [_cand('4-4', 'fresh_null_runid', P1,
                      f'B 表 {null_runid_cnt} 行 run_id 空（口径违规）',
                      'P2-④ 钉死 run_id 真实回填；空行即半吊子交付',
                      'run_id 回填 + 写入方改真实 run')]
    return []


def judge_4_5(current_week_missing):
    """4-5 P3 达人周快照缺当前周（周一 08:00 后查）。

    探针表不存在时 collect 层传 None → 不出单（宁缺不编造），judge 留 selftest。
    """
    if current_week_missing:
        return [_cand('4-5', 'fresh_week_snap', P3,
                      '达人周快照缺当前周',
                      '周一 08:00 后当前周快照缺失',
                      '快照任务补齐（P3 留档）')]
    return []


# ═════════════════ Lane 5 积分曲线 ═════════════════

def judge_5_1(low_balance):
    """5-1 P1 任一员工余额<200（红线）。low_balance = [(agent, balance)]。"""
    return [_cand('5-1', f'bal_{a}', P1, f'{a} 余额 {b}<200（红线）',
                  'credit_accounts.balance 低于红线，班会被熔断掐',
                  '充值/额度调整 + 余额≥200')
            for a, b in low_balance if b < 200]


def judge_5_2(burn_today, burn_yesterday):
    """5-2 P2 日均消耗环比漂移>50%。"""
    if burn_yesterday and abs(burn_today - burn_yesterday) / burn_yesterday > 0.5:
        return [_cand('5-2', 'credit_dod_drift', P2,
                      f'日消耗环比漂移 '
                      f'{(burn_today - burn_yesterday) * 100 // burn_yesterday:+d}%'
                      f'（{burn_yesterday}→{burn_today}）',
                      '阈值 ±50%',
                      '漂移归因（班形态变化/泄漏/新任务）')]
    return []


def judge_5_3(emp_hour_burn):
    """5-3 P2 单员工 1h 消耗>300。emp_hour_burn = [(agent, credits_1h)]。"""
    return [_cand('5-3', f'hour_{a}', P2, f'{a} 1h 消耗 {c}>300',
                  '短窗燃烧超阈', '轨迹复核 + 非泄漏则调阈')
            for a, c in emp_hour_burn if c > 300]


def judge_5_4(search_share, calls):
    """5-4 P2 search 占比越线预警（>40% 且 calls>30 的 80%——熔断前预警）。

    探针需 openclaw 轨迹库（贵），collect 层默认不可用传 None；judge 留 selftest。
    """
    if calls is None:
        return []
    if calls > 30 and search_share > 0.4 * 0.8:
        return [_cand('5-4', 'credit_search_warn', P2,
                      f'search 占比 {search_share:.0%} 预警（calls={calls}）',
                      '熔断线 40%×calls>30 的 80% 前哨',
                      '轨迹复核：真漫游则熔断会拦，预警归因')]
    return []


JUDGES = [
    ('1-1', judge_1_1), ('1-2', judge_1_2), ('1-3', judge_1_3),
    ('1-4', judge_1_4), ('1-5', judge_1_5),
    ('2-1', judge_2_1), ('2-2', judge_2_2), ('2-3', judge_2_3),
    ('2-4', judge_2_4), ('2-5', judge_2_5),
    ('3-1', judge_3_1), ('3-2', judge_3_2), ('3-3', judge_3_3),
    ('3-4', judge_3_4), ('3-5', judge_3_5),
    ('4-1', judge_4_1), ('4-2', judge_4_2), ('4-3', judge_4_3),
    ('4-4', judge_4_4), ('4-5', judge_4_5),
    ('5-1', judge_5_1), ('5-2', judge_5_2), ('5-3', judge_5_3),
    ('5-4', judge_5_4),
]

PRIORITY_ORDER = {P1: 0, P2: 1, P3: 2}


# ─────────────────────────── 采集层（selftest 不触） ───────────────────────────

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
    now_ms = int(time.time() * 1000)
    today = datetime.now().strftime('%Y-%m-%d')
    hour_now = datetime.now().hour
    wd = datetime.now().weekday() < 5
    conn = sqlite3.connect(DB, timeout=15)
    g = lambda q, p=(): conn.execute(q, p).fetchone()
    try:
        active = g("SELECT COUNT(*) FROM talents WHERE status='active'")[0]
        hollow = g("SELECT COUNT(*) FROM talents WHERE status='active' AND "
                   "COALESCE(name,'')='' AND COALESCE(douyin_id,'')='' AND "
                   "COALESCE(followers,'')='' AND COALESCE(total_gmv,'')='' AND "
                   "COALESCE(main_category,'')=''")[0]
        shell_today = round(hollow * 100 / max(active, 1), 1)
        conv = g("SELECT COUNT(*) FROM products WHERE conversion_rate "
                 "IS NOT NULL")[0]
        bad_rec = g("SELECT COUNT(*) FROM talent_recommendations WHERE "
                    "rec_date=? AND pipeline_status!='recommended'",
                    (today,))[0]
        bad_dyd = g("SELECT COUNT(*) FROM talents WHERE created_at>=? AND "
                    "douyin_id NOT LIKE 'v2_%' AND douyin_id NOT GLOB '*[0-9]*' "
                    "AND douyin_id NOT LIKE '% %' AND douyin_id NOT LIKE '%http%'",
                    (today,))[0]
        cases24 = g("SELECT COUNT(*) FROM duty_cases WHERE created_at>?",
                    (now_ms - 24 * HOUR,))[0]
        fuse_today = g("SELECT COUNT(*) FROM fuse_events WHERE created_at>=? "
                       "AND created_at<?",
                       (int(datetime.now().replace(hour=0, minute=0, second=0,
                                                   microsecond=0).timestamp() * 1000),
                        now_ms))[0]
        rec_today = g("SELECT COUNT(*) FROM talent_recommendations WHERE "
                      "rec_date=?", (today,))[0]
        null_rid = g("SELECT COUNT(*) FROM talent_recommendations WHERE "
                     "run_id IS NULL OR run_id=''")[0]
        c_last = g("SELECT MAX(created_at) FROM talent_collects")[0]
        c_age = ((now_ms - c_last) // 3600000) if c_last else None
        p_last = g("SELECT MAX(CAST(daren_synced_at AS INTEGER)) "
                   "FROM products")[0]
        p_age = ((now_ms - p_last) // 3600000) if p_last else None
        bal_low = [(r[0], r[1]) for r in conn.execute(
            "SELECT agent_id, balance FROM credit_accounts "
            "WHERE balance < 200")]
        burn_today = g("SELECT COALESCE(SUM(credits_used),0) FROM "
                       "credit_usage_log WHERE created_at>=?", (today,))[0]
        burn_yday = g("SELECT COALESCE(SUM(credits_used),0) FROM "
                      "credit_usage_log WHERE created_at>=? AND created_at<?",
                      ((datetime.now().fromtimestamp(
                          time.time() - 86400)).strftime('%Y-%m-%d'),
                       today))[0]
        since_h = datetime.fromtimestamp(time.time() - 3600).strftime(
            '%Y-%m-%d %H:%M:%S')
        emp_hour = [(r[0], r[1]) for r in conn.execute(
            "SELECT agent_id, SUM(credits_used) FROM credit_usage_log "
            "WHERE created_at>=? GROUP BY agent_id HAVING SUM(credits_used)>300",
            (since_h,))]
        # 2-5 单班烧分：今日 vs 近 7 日日均（简化：今日 total / 当天已过小时 ×24
        # 太噪，用「今日总消耗 vs 前 7 天日均总消耗」）
        week_ago = (datetime.now().fromtimestamp(
            time.time() - 7 * 86400)).strftime('%Y-%m-%d')
        avg7 = g("SELECT COALESCE(SUM(credits_used),0)/7.0 FROM "
                 "credit_usage_log WHERE created_at>=? AND created_at<?",
                 (week_ago, today))[0]
        class_burn = [('all', burn_today, avg7 or 0)]
        # 1-3 撞名组：同名 active 组 vs 已裁决基线（首跑建基线）
        dupes = [r[0] for r in conn.execute(
            "SELECT name FROM talents WHERE status='active' AND name!='' "
            "GROUP BY name HAVING COUNT(*)>1")]
    finally:
        conn.close()

    # 1-1 空壳率快照
    snap_path = os.path.join(MARKERS, 'shell_snapshot.json')
    try:
        shell_yday = json.load(open(snap_path)).get('ratio')
    except Exception:
        shell_yday = None
    os.makedirs(MARKERS, exist_ok=True)
    with open(snap_path, 'w') as f:
        json.dump({'date': today, 'ratio': shell_today}, f)

    # 1-3 撞名基线
    base_path = os.path.join(MARKERS, 'dupe_baseline.json')
    try:
        baseline = set(json.load(open(base_path)).get('names', []))
    except Exception:
        baseline = set(dupes)  # 首跑：现状即基线
    new_dupes = [d for d in dupes if d not in baseline]
    if not os.path.exists(base_path):
        with open(base_path, 'w') as f:
            json.dump({'names': sorted(dupes)}, f)

    # 2-4 launchd last exit
    try:
        lp = subprocess.run(['launchctl', 'list'], capture_output=True,
                            text=True, timeout=10).stdout
        rows = []
        loaded = 0
        for l in lp.splitlines():
            if 'com.solobrave.' not in l:
                continue
            loaded += 1
            parts = l.split()
            if len(parts) >= 3:
                rows.append((' '.join(parts[2:]), parts[1]))
    except Exception:
        rows, loaded = [], EXPECTED_LAUNCHD_JOBS

    # 3-2 worker 可达性（marker 记不可谈起始）
    down_marker = os.path.join(MARKERS, 'worker-down.marker')
    try:
        with urllib.request.urlopen('http://127.0.0.1:8095/health',
                                    timeout=5) as r:
            r.read()
        reachable = True
        if os.path.exists(down_marker):
            os.unlink(down_marker)
        down_since = None
    except Exception:
        reachable = False
        if os.path.exists(down_marker):
            down_since = json.load(open(down_marker)).get('since_ms')
        else:
            down_since = now_ms
            with open(down_marker, 'w') as f:
                json.dump({'since_ms': now_ms}, f)

    worker_lines = _tail(WORKER_LOG)
    server_lines = _tail(SERVER_LOG)
    kick_recent = os.path.exists(os.path.join(MARKERS, 'worker-kick.marker'))

    return {
        'now_ms': now_ms, 'hour_now': hour_now, 'workday': wd,
        'judge_args': {
            'judge_1_1': (shell_today, shell_yday),
            'judge_1_2': (conv,),
            'judge_1_3': (new_dupes,),
            'judge_1_4': (bad_rec,),
            'judge_1_5': (bad_dyd,),
            'judge_2_1': (_recent_lines(worker_lines, now_ms, 2 * HOUR),),
            'judge_2_2': (_recent_lines(worker_lines, now_ms, 2 * HOUR),),
            'judge_2_3': (_recent_lines(server_lines, now_ms, 600000),),
            'judge_2_4': (rows,),
            'judge_2_5': (class_burn,),
            'judge_3_1': (loaded,),
            'judge_3_2': (reachable, down_since, now_ms),
            'judge_3_3': (cases24,),
            'judge_3_4': (fuse_today,),
            'judge_3_5': (kick_recent,),
            'judge_4_1': (c_age,),
            'judge_4_2': (p_age,),
            'judge_4_3': (hour_now, rec_today, wd),
            'judge_4_4': (null_rid,),
            'judge_4_5': (None,),   # 快照表不存在：宁缺不编造
            'judge_5_1': (bal_low,),
            'judge_5_2': (burn_today, burn_yday),
            'judge_5_3': (emp_hour,),
            'judge_5_4': (None, None),  # 轨迹探针默认不可用
        },
    }


# ─────────────────────────── 去重与输出 ───────────────────────────

def _load_emitted():
    try:
        return json.load(open(EMITTED, encoding='utf-8'))
    except Exception:
        return {}


def _save_emitted(state):
    os.makedirs(os.path.dirname(EMITTED), exist_ok=True)
    with open(EMITTED, 'w', encoding='utf-8') as f:
        json.dump(state, f)


def _open_queue_anchors():
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
    out = []
    for c in cands:
        if any(a and a in s for s in open_summaries for a in c['anchors']):
            continue
        if c['priority'] != P1:
            if now_ms - emitted.get(c['key'], 0) < COOLDOWN_MS:
                continue
        emitted[c['key']] = now_ms
        out.append(c)
    return out


def run_round(inputs=None):
    if inputs is None:
        inputs = collect_inputs()
    args = inputs['judge_args']
    cands = []
    for no, fn in JUDGES:
        try:
            cands += fn(*args.get(f'judge_{no.replace("-", "_")}', ())) or []
        except Exception as e:
            print(f'[{no}] judge 异常（跳过）: {e}')
    cands.sort(key=lambda c: PRIORITY_ORDER.get(c['priority'], 9))
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
        print(f"[{c['priority']}][{c['judge']}] {c['title']}")
        if c['priority'] == P1:
            dn.desktop_push(f'[传送带] {c["title"]}', c['evidence'][:180])
    print(f'\n本轮候选 {len(fresh)} 个（压制 {rnd["suppressed"]} 个重复）')
    return rnd


# ─────────────────────────── 自验证：21 判据各 1 正 1 负 ───────────────────────────

def selftest():
    now_ms = int(time.time() * 1000)
    ts_now = datetime.now().strftime('%m-%d %H:%M:%S')
    fails = []

    cases = [
        # (判据号, 正例参数, 负例参数)
        ('1-1', (15.0, 8.0), (10.0, 8.0)),
        ('1-2', (3,), (0,)),
        ('1-3', (['张三', '李四'],), ([],)),
        ('1-4', (2,), (0,)),
        ('1-5', (4,), (0,)),
        ('2-1', ([f'[{ts_now}] code=10001010A'],), ([f'[{ts_now}] ok'],)),
        ('2-2', ([f'[{ts_now}] Sync API inside the asyncio loop'],),
         ([f'[{ts_now}] normal'],)),
        ('2-3', ([f'[{ts_now}] ChatPOST error x'] * 3,),
         ([f'[{ts_now}] ChatPOST ok'],)),
        ('2-4', ([('com.solobrave.x', '2')],), ([('com.solobrave.x', '0')],)),
        ('2-5', ([('emp_a', 900, 100)],), ([('emp_a', 200, 100)],)),
        ('3-1', (9,), (EXPECTED_LAUNCHD_JOBS,)),
        ('3-2', (False, now_ms - 6 * 60000, now_ms), (True, None, now_ms)),
        ('3-3', (5,), (2,)),
        ('3-4', (2,), (0,)),
        ('3-5', (True,), (False,)),
        ('4-1', (63,), (10,)),
        ('4-2', (30,), (5,)),
        ('4-3', (19, 0, True), (19, 3, True)),
        ('4-4', (1,), (0,)),
        ('4-5', (True,), (False,)),
        ('5-1', ([('emp_a', 150)],), ([('emp_a', 999)],)),
        ('5-2', (300, 100), (120, 100)),
        ('5-3', ([('emp_a', 450)],), ([('emp_a', 100)],)),
        ('5-4', (0.35, 50), (0.1, 50)),
    ]
    fn_map = dict(JUDGES)
    for no, pos, neg in cases:
        pos_out = fn_map[no](*pos) or []
        neg_out = fn_map[no](*neg) or []
        if not pos_out:
            fails.append(f'{no} 正例未检出')
        if neg_out:
            fails.append(f'{no} 负例误报')

    if fails:
        print('SELFTEST FAIL: ' + '；'.join(fails))
        return 1
    print(f'SELFTEST OK：{len(cases)} 判据组各 1 正 1 负全过（21 判据全覆盖）')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--selftest', action='store_true')
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    run_round()
    sys.exit(0)


if __name__ == '__main__':
    main()
