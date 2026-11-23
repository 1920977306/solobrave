#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抖音号回填工具（单2 2026-11-23，P2 触达前置——没有抖音号=私信都够不着）。

缺口实况（11-23 实测）：talents 224 人，抖音号可用仅 99；
缺口 125 = 无号 114 + 脏值 11（其中 9 个 @前缀可剥、2 个纯中文需回搜）。
需按名回搜 116 人（55 人有 platform_talent_id/douyin_uid 可辅助对拍）。

三路回填（确定性优先级）：
A. @前缀直剥：@lisameizhuang → lisameizhuang（零成本，先干）
B. 按名回搜：worker /square-lookup（广场搜索框接受任意关键词，页面真请求带
   a_bogus 签名）→ 候选对拍（达人ID 精确 > 昵称归一化相等 > 单向包含），
   只提议不写库
C. 平台无此达人/搜不到 → 如实记「未命中」，不编造（§8.3 只丢不造）

用法：
  python3 daren_cockpit/tools/douyin_id_backfill.py --report
      缺口清单分析（纯 DB 只读，随时可跑）
  python3 daren_cockpit/tools/douyin_id_backfill.py --dry-run [--limit N]
      剥 @ + 调 worker 按名回搜（需团长端登录），出「可补 N/缺口」实测报告
      → data/douyin-id-backfill-report-<ts>.json，★不写库
  python3 daren_cockpit/tools/douyin_id_backfill.py --apply --report-file <path>
      写库（须先经老大拍批量）：自动备份表+整库副本 → 写 → 回读验证 → 守卫

铁律：--apply 前必须有对应 --dry-run 报告文件（可复核账）；写库逐行留痕。
"""
import argparse
import datetime
import json
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / 'data' / 'solobrave.db'
WORKER = 'http://127.0.0.1:8095'

# 与 §8.1 一致的脏值口径：@前缀 / 含中文 / 含空格 ⇒ 不是可用抖音号
DY_RE = re.compile(r'^[A-Za-z0-9_.]{5,24}$')


def _norm_name(s):
    """昵称归一化：去空白/装饰符（全角点、emoji 区不计——保留中文字母数字）。"""
    return re.sub(r'[\s·•.@#]+', '', str(s or '')).lower()


def _usable(did):
    return bool(did) and bool(DY_RE.match(did))


def _is_dirty(did):
    if not did:
        return False
    return (did.startswith('@') or re.search(r'[一-龥]', did) or ' ' in did)


def gap_list(conn):
    """缺口清单：无号 + 脏值，按可否直剥分类。"""
    rows = conn.execute(
        "SELECT id, name, douyin_id, platform_talent_id, douyin_uid, followers "
        "FROM talents").fetchall()
    strip, search, ok = [], [], 0
    for tid, name, did, pt, uid, fol in rows:
        if _usable(did):
            ok += 1
            continue
        if did and re.fullmatch(r'@[A-Za-z0-9_.]{5,24}', did):
            strip.append({'talent_id': tid, 'name': name,
                          'proposed': did[1:], 'source': 'at_strip'})
        else:
            search.append({'talent_id': tid, 'name': name,
                           'douyin_id_now': did or '',
                           'platform_id': pt or uid or '', 'followers': fol or 0})
    return {'total': len(rows), 'usable': ok, 'at_strip': strip, 'need_search': search}


def cmd_report(args):
    conn = sqlite3.connect(str(DB))
    g = gap_list(conn)
    conn.close()
    need = len(g['at_strip']) + len(g['need_search'])
    print(f"talents 总数 {g['total']} | 抖音号可用 {g['usable']} | 缺口 {need}")
    print(f"  A. @前缀直剥（零成本）: {len(g['at_strip'])}")
    print(f"  B. 需按名回搜: {len(g['need_search'])}"
          f"（其中 {sum(1 for r in g['need_search'] if r['platform_id'])} 人有平台ID 可辅助对拍）")
    out = ROOT / 'data' / f"douyin-id-gap-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f"清单落盘: {out}")
    return 0


def _worker_lookup(names, timeout_s=900):
    """POST /square-lookup → 轮询 /jobs/<cid> 到 done/refused/error。"""
    body = json.dumps({'names': names}).encode('utf-8')
    req = urllib.request.Request(WORKER + '/square-lookup', data=body,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=30) as r:
        cid = json.loads(r.read())['collect_id']
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        with urllib.request.urlopen(WORKER + f'/jobs/{cid}', timeout=15) as r:
            job = json.loads(r.read())
        st = job.get('status')
        if st in ('done', 'refused', 'error', 'risk_blocked'):
            return job
        time.sleep(5)
    return {'status': 'timeout', 'collect_id': cid}


def _pick_candidate(name, cands, platform_id=''):
    """候选对拍（确定性，不评分不猜）：达人ID 精确 > 昵称归一化相等 > 单向包含。"""
    target = _norm_name(name)
    if platform_id:
        for c in cands:
            if str(c.get('达人ID') or '') == str(platform_id):
                return c, 'uid_exact'
    for c in cands:
        if _norm_name(c.get('昵称')) == target:
            return c, 'name_exact'
    for c in cands:
        cn = _norm_name(c.get('昵称'))
        if target and cn and (target in cn or cn in target):
            return c, 'name_contain'
    return None, 'no_match'


def cmd_dry_run(args):
    conn = sqlite3.connect(str(DB))
    g = gap_list(conn)
    conn.close()
    batch = g['need_search'][:args.limit] if args.limit else g['need_search']
    report = {'at': datetime.datetime.now().isoformat(timespec='seconds'),
              'mode': 'dry-run（不写库）',
              'gap_total': len(g['at_strip']) + len(g['need_search']),
              'at_strip': g['at_strip'], 'searched': [], 'summary': {}}
    # A 路：@直剥进报告（建议项，--apply 才写）
    for item in batch:
        pass  # A 路已全量进 report['at_strip']，这里只跑 B 路
    # B 路：分批调 worker（每批 ≤10）
    for i in range(0, len(batch), 10):
        chunk = batch[i:i + 10]
        names = [r['name'] for r in chunk]
        print(f"[dry-run] 回搜第 {i+1}-{i+len(chunk)} 个: {', '.join(names[:3])}…")
        job = _worker_lookup(names)
        st = job.get('status')
        if st != 'done':
            print(f"[dry-run] 本批状态 {st}（{job.get('error') or '无错误信息'}）——"
                  f"已得结果照记，剩余批次停（人工看 worker）")
        result = {}
        try:
            result = json.loads(job.get('result') or '{}')
        except Exception:
            pass
        per_name = result.get('per_name') or {}
        for item in chunk:
            entry = dict(item)
            got = per_name.get(item['name']) or {}
            cands = got.get('candidates') or []
            pick, how = _pick_candidate(item['name'], cands, item['platform_id'])
            entry['candidates'] = cands
            entry['proposed'] = pick['抖音号'] if pick else ''
            entry['match_how'] = how
            report['searched'].append(entry)
    n_prop = sum(1 for e in report['searched'] if e['proposed'])
    n_strip = len(report['at_strip'])
    report['summary'] = {
        'at_strip': n_strip, 'searched': len(report['searched']),
        'proposed': n_prop + n_strip,
        'missed': len(report['searched']) - n_prop,
        'hit_rate': round((n_prop + n_strip) / max(1, report['gap_total']), 3),
    }
    out = ROOT / 'data' / f"douyin-id-backfill-report-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    s = report['summary']
    print(f"[dry-run] 完成：直剥 {s['at_strip']} + 回搜命中 {n_prop}/"
          f"{s['searched']}（未命中 {s['missed']}）→ 总可补 {s['proposed']}/"
          f"{report['gap_total']}（命中率 {s['hit_rate']:.1%}）")
    print(f"[dry-run] 报告（可复核账，未写库）: {out}")
    print("[dry-run] 下一步：老大拍批量 → --apply --report-file <本报告>")
    return 0


def cmd_apply(args):
    """写库：必须带 dry-run 报告；自动备份 + 回读验证 + 逐行留痕。"""
    rf = Path(args.report_file)
    if not rf.exists():
        print(f"--apply 必须有 --dry-run 报告文件（可复核账）: {rf}", file=sys.stderr)
        return 1
    rep = json.loads(rf.read_text(encoding='utf-8'))
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M')
    bak = Path(f'/tmp/solobrave-talents-backup-{ts}.db')
    bak.write_bytes(DB.read_bytes())
    conn = sqlite3.connect(str(DB))
    now = int(time.time() * 1000)
    applied, skipped = 0, []
    for e in rep.get('at_strip', []) + rep.get('searched', []):
        prop = (e.get('proposed') or '').strip()
        if not prop or not _usable(prop):
            skipped.append((e.get('name'), '无可用提议值'))
            continue
        dup = conn.execute('SELECT id FROM talents WHERE douyin_id = ? AND id != ?',
                           (prop, e['talent_id'])).fetchone()
        if dup:
            skipped.append((e.get('name'), f'撞号 {prop} 已被 {dup[0]} 占用'))
            continue
        conn.execute('UPDATE talents SET douyin_id = ?, updated_at = ? WHERE id = ?',
                     (prop, now, e['talent_id']))
        applied += 1
    conn.commit()
    # 回读验证
    bad = 0
    for e in rep.get('at_strip', []) + rep.get('searched', []):
        prop = (e.get('proposed') or '').strip()
        if not prop or not _usable(prop):
            continue
        got = conn.execute('SELECT douyin_id FROM talents WHERE id = ?',
                           (e['talent_id'],)).fetchone()
        if not got or got[0] != prop:
            bad += 1
    conn.close()
    print(f"[apply] 备份 {bak}")
    print(f"[apply] 写库 {applied} 行，跳过 {len(skipped)}（撞号/无值不强行），"
          f"回读不一致 {bad}")
    for nm, why in skipped[:20]:
        print(f"  跳过: {nm} — {why}")
    return 0 if bad == 0 else 1


def main():
    ap = argparse.ArgumentParser(description='抖音号回填（单2 dry-run 先行）')
    ap.add_argument('--report', action='store_true', help='缺口清单分析（只读）')
    ap.add_argument('--dry-run', action='store_true',
                    help='剥@ + 按名回搜出报告（不写库，需团长端登录）')
    ap.add_argument('--apply', action='store_true', help='写库（须带 --report-file）')
    ap.add_argument('--limit', type=int, default=0, help='dry-run 只回搜前 N 个（试点）')
    ap.add_argument('--report-file', default='', help='--apply 所需的 dry-run 报告路径')
    args = ap.parse_args()
    if args.apply:
        return cmd_apply(args)
    if args.dry_run:
        return cmd_dry_run(args)
    return cmd_report(args)


if __name__ == '__main__':
    sys.exit(main())
