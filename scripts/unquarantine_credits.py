#!/usr/bin/env python3
"""quarantine 冲回脚本（P1-⑤ 止血守卫的解除端，协调边硬条件②：禁止能冻不能解）。

背景：emp_1780199176680 的空 session 泄漏行被止血守卫冻结（行照写 +
quarantined=1 + 余额不扣减 + quarantined_credits 累计）。根因修复后，
用本脚本把冻结的分数冲回余额并解除标记。

⚠️ 纪律：
- 默认 dry-run（只打印不动库），必须显式 --apply 才真正执行；
- 只处理指定 agent 且 quarantined=1 的行，其他行零接触；
- 冲回金额 = credit_usage_log 里 quarantined=1 的 credits_used 合计，
  与 credit_accounts.quarantined_credits 对账，不一致则以行为准并告警
  （能冻不能对账 = 新的洞，禁止放行）。

用法：
  python3 scripts/unquarantine_credits.py                      # dry-run
  python3 scripts/unquarantine_credits.py --apply            # 真冲回
  python3 scripts/unquarantine_credits.py --apply --agent XXX  # 指定 agent
"""
import argparse
import sqlite3

DEFAULT_DB = '/Users/qichen/solobrave-prod/data/solobrave.db'
DEFAULT_AGENT = 'emp_1780199176680'


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=DEFAULT_DB)
    ap.add_argument('--agent', default=DEFAULT_AGENT)
    ap.add_argument('--apply', action='store_true', help='真正执行冲回（默认 dry-run）')
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        'SELECT COUNT(*) AS n, COALESCE(SUM(credits_used),0) AS total '
        'FROM credit_usage_log WHERE agent_id=? AND quarantined=1',
        (args.agent,)).fetchone()
    n_rows, total = rows['n'], int(rows['total'])

    acc = conn.execute(
        'SELECT balance, COALESCE(quarantined_credits,0) AS qc '
        'FROM credit_accounts WHERE agent_id=?', (args.agent,)).fetchone()
    if not acc:
        print(f'❌ credit_accounts 无此 agent：{args.agent}')
        return 1
    acc_qc = int(acc['qc'])

    mode = 'APPLY' if args.apply else 'DRY-RUN'
    print(f'── unquarantine · {mode} · agent={args.agent} ──')
    print(f'quarantined 行数 = {n_rows}，行合计冻结 = {total} 分')
    print(f'credit_accounts.quarantined_credits = {acc_qc}，当前 balance = {acc["balance"]}')

    if acc_qc != total:
        print(f'⚠️ 对账不一致：账户累计 {acc_qc} ≠ 行合计 {total}，以行合计为准冲回，请排查差异来源')

    if not args.apply:
        print('（dry-run：加 --apply 才真正冲回）')
        conn.close()
        return 0

    if n_rows == 0:
        print('无 quarantined 行，无需冲回')
        conn.close()
        return 0

    conn.execute('BEGIN')
    conn.execute(
        'UPDATE credit_accounts SET balance = balance + ?, '
        'quarantined_credits = 0, updated_at = datetime(\'now\',\'localtime\') '
        'WHERE agent_id = ?', (total, args.agent))
    conn.execute(
        'UPDATE credit_usage_log SET quarantined = 0 '
        'WHERE agent_id = ? AND quarantined = 1', (args.agent,))
    conn.commit()

    after = conn.execute(
        'SELECT balance FROM credit_accounts WHERE agent_id=?', (args.agent,)).fetchone()
    print(f'✅ 冲回完成：balance {acc["balance"]} → {after["balance"]}（+{total}），'
          f'{n_rows} 行解除标记')
    conn.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
