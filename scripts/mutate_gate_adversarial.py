#!/usr/bin/env python3
"""★ B 边（2026-10-25）对抗守卫的**变异验证** —— 证明守卫真的会红。

★ 为什么必须做这一步：
  一条静态守卫「看着在管，其实没管」是本项目反复踩过的坑
  （恒真闸门：条件写成死值、扫描器扫错入口名、豁免名单把整个方向放行…）。
  ⇒ 唯一能证伪的办法：**故意破坏代码，看守卫会不会叫**。
  变异全绿 ⇒ 守卫是摆设（形同虚设），必须删掉或重写。

★ 本脚本做两件事：
  ① **变异 → 跑测试 → 断言「该红的红了」**
  ② **同一批变异下，A 边的 `tests/test_credits_gate.py` 是否仍然全绿**
     —— 这是本次审查的**核心论点**：
     A 边的守卫全部从「入队侧」看 ⇒ 下面 7 个变异它一个都抓不到；
     而 B 边从「算力消耗侧」反查 ⇒ 7 个全抓。

★ 安全约束：
  - 只备份**它自己动过的文件**（还原校验也只比这几个文件）——
    2026-10-25 教训：拿全仓 diff 做还原校验，会被「人同时改了别的文件」误判成
    「改动被冲掉了」。
  - 备份放**仓库外**的临时目录（§1 数据隔离铁律：不留带备份的副本在仓库里）。
  - 任何一步异常都保证还原。

用法：
    python3 scripts/mutate_gate_adversarial.py            # 全跑
    python3 scripts/mutate_gate_adversarial.py --only M3  # 只跑一个
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

A_GATE = "tests/test_credits_gate.py"          # A 边的守卫（只读，不改）
B_GATE = "tests/test_credits_gate_adversarial.py"  # B 边的守卫（本次新增）

# ── 变异定义 ────────────────────────────────────────────────────────────
# kind: "replace"（找字替换） / "append"（文件尾追加）
# expect_red: 必须变红的测试（用 "文件::测试名" 匹配）
# expect_a_green: A 边那份测试文件必须**仍然全绿**（证明盲区）
MUTATIONS = [
    {
        "id": "M1",
        "title": "新增一处不入队的 LLM 调用点（QA 入口多打一次模型）",
        "file": "daren_bot/executors.py",
        "kind": "replace",
        "find": '    content = ""\n    for attempt in (1, 2, 3):',
        "replace": '    content = _qa_llm(prompt + "（预热一次）")\n'
                   '    content = ""\n    for attempt in (1, 2, 3):',
        "expect_red": [f"{B_GATE}::test_compute_site_call_counts_match"],
        "why": "消耗点从「登记表」里长出来了，但没登记 ⇒ 口径漂了",
    },
    {
        "id": "M2",
        "title": "闸门转发层把价格写死成字面量（成本表不再是真源）",
        "file": "daren_bot/credits.py",
        "kind": "replace",
        "find": "    return db.enqueue_task_charged(tenant_key, task_type, target, cost, action,",
        "replace": "    return db.enqueue_task_charged(tenant_key, task_type, target, 100, action,",
        "expect_red": [f"{B_GATE}::test_gate_forward_layer_reads_price_from_table"],
        "why": "成本表改了价，实际收的还是写死的数 ⇒ 唯一真源被架空",
    },
    {
        "id": "M3",
        "title": "worker 退回「先 mark 再 refund」的老写法（两个独立写 + 无幂等）",
        "file": "daren_bot/worker.py",
        "kind": "replace",
        "find": '        refunded, _balance = db.fail_task_and_refund(\n'
                '            task["id"], reason, tenant_key=task.get("tenant_key", ""), cost=cost)',
        "replace": '        db.mark_task(task["id"], "failed", reason)\n'
                   '        db.refund(task["tenant_key"], cost, "refund", ref_task_id=task["id"])\n'
                   '        refunded, _balance = True, 0',
        "expect_red": [f"{B_GATE}::test_failed_task_refunds_exactly_once",
                       f"{B_GATE}::test_refund_entry_is_not_used_in_business_code"],
        "why": "退款不再幂等 ⇒ 白送积分（比多扣更糟：直接造钱）；"
               "且业务代码里又出现裸退款入口",
    },
    {
        "id": "M4",
        "title": "收费动作与任务类型配错（qa 任务收 collect_douyin 的价）",
        "file": "daren_bot/handler.py",
        "kind": "replace",
        "find": '            tk, "qa", q[:200], "qa", chat_id=chat_id)',
        "replace": '            tk, "qa", q[:200], "collect_douyin", chat_id=chat_id)',
        "expect_red": [f"{B_GATE}::test_charge_action_matches_task_type"],
        "why": "action 在成本表里（所以现有守卫看着没问题），但收的是别的动作的价",
    },
    {
        "id": "M5",
        "title": "零成本留痕点改成真扣费（db.deduct 的 cost 从 0 变 100）",
        "file": "daren_bot/credits.py",
        "kind": "replace",
        "find": "    db.deduct(tenant_key, 0, action, cache_hit=cache_hit)",
        "replace": "    db.deduct(tenant_key, 100, action, cache_hit=cache_hit)",
        "expect_red": [f"{B_GATE}::test_deduct_is_only_used_as_zero_cost_tracing"],
        "why": "裸扣费入口被用来真扣钱 ⇒ 绕过整个闸门",
    },
    {
        "id": "M6",
        "title": "新增一个未登记的裸扣费调用点（db.deduct 直接扣 50 分）",
        "file": "daren_bot/weekly.py",
        "kind": "append",
        "find": "",
        "replace": "\n\ndef _mut_probe_deduct():\n"
                   "    db.deduct(\"tk\", 50, \"collect_douyin\")\n",
        "expect_red": [f"{B_GATE}::test_deduct_is_only_used_as_zero_cost_tracing"],
        "why": "db.deduct 是公开的裸扣费入口（只扣钱不建任务）",
    },
    {
        "id": "M7",
        "title": "直接调底层入队且用错价（collect_douyin 收 1 分）",
        "file": "daren_bot/weekly.py",
        "kind": "append",
        "find": "",
        "replace": "\n\ndef _mut_probe_enqueue():\n"
                   "    db.enqueue_task_charged(\"tk\", \"qa\", \"q\", 1, \"collect_douyin\")\n",
        "expect_red": [f"{B_GATE}::test_direct_charged_enqueue_cost_must_equal_price"],
        "why": "action 合法所以现有守卫全绿，但实收 1 分（该收 100 分）⇒ 少收 99 分",
    },
    {
        "id": "M8",
        "title": "原子退款函数去掉幂等判重（退两次钱）",
        "file": "daren_bot/db.py",
        "kind": "replace",
        "find": "        already = conn.execute(\n"
                "            \"SELECT COUNT(*) FROM bot_credits_ledger \"\n"
                "            \"WHERE action='refund' AND ref_task_id=?\", (task_id,)).fetchone()[0]",
        "replace": "        already = 0",
        "expect_red": [f"{B_GATE}::test_fail_task_and_refund_is_idempotent"],
        "why": "退款不再幂等 ⇒ 同一任务退两次 = 白送积分（造钱）",
    },
    {
        "id": "M9",
        "title": "僵死回收去掉时间门槛（正在执行的任务被抢回重跑）",
        "file": "daren_bot/db.py",
        "kind": "replace",
        "find": "            \"WHERE status='collecting' AND (\"\n"
                "            \"  claimed_at IS NULL OR claimed_at < ?)\",\n"
                "            (now_ts() - STALE_COLLECTING_SECONDS,))",
        "replace": "            \"WHERE status='collecting'\")",
        "expect_red": [f"{B_GATE}::test_reclaim_stale_collecting_respects_time_threshold"],
        "why": "worker 重启重叠时正在执行的任务被重跑 ⇒ 算力双跑 + 可能双退",
    },
]


def _run_pytest(targets: list[str]) -> tuple[int, str]:
    """跑 pytest，返回 (退出码, 输出)。"""
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "--no-header", "-p", "no:cacheprovider"]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


def _failed_test_names(output: str) -> set[str]:
    """从 pytest 输出里解析出失败的测试名（文件::名字）。"""
    names = set()
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("FAILED ") or line.startswith("ERROR "):
            token = line.split()[1]
            token = token.split(" - ")[0]
            names.add(token.replace("/", "/"))
    return names


def _count(output: str, label: str) -> int:
    """从 pytest 汇总行里取「N passed / N failed」的 N（不是数子串出现次数）。"""
    import re
    m = re.findall(r"(\d+) " + label, output)
    return int(m[-1]) if m else 0


def _apply(m: dict, path: Path) -> str:
    """应用变异，返回原文件内容。"""
    original = path.read_text(encoding="utf-8")
    if m["kind"] == "replace":
        if m["find"] not in original:
            raise SystemExit(f"[{m['id']}] 锚点没找到（代码变了？）：\n{m['find'][:120]}")
        new = original.replace(m["find"], m["replace"], 1)
    elif m["kind"] == "append":
        new = original + m["replace"]
    else:
        raise SystemExit(f"[{m['id']}] 未知 kind={m['kind']}")
    path.write_text(new, encoding="utf-8")
    return original


def run_one(m: dict, workdir: Path) -> dict:
    """跑一个变异：备份 → 改 → 测 → 还原 → 校验还原。"""
    rel = m["file"]
    path = ROOT / rel
    backup = workdir / rel.replace("/", "__")
    shutil.copy2(path, backup)

    result = {"id": m["id"], "title": m["title"], "why": m["why"],
              "b_red": [], "b_green": 0, "a_red": [], "a_green": 0,
              "restore_ok": True, "error": ""}

    try:
        _apply(m, path)
        # B 边守卫
        rc_b, out_b = _run_pytest([B_GATE])
        failed_b = _failed_test_names(out_b)
        result["b_red"] = sorted(failed_b)
        result["b_green"] = _count(out_b, "passed")
        # A 边守卫
        rc_a, out_a = _run_pytest([A_GATE])
        failed_a = _failed_test_names(out_a)
        result["a_red"] = sorted(failed_a)
        result["a_green"] = _count(out_a, "passed")
    except Exception as e:      # noqa: BLE001
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        # 还原（只比**它动过的这个文件**）
        path.write_text(backup.read_text(encoding="utf-8"), encoding="utf-8")
        result["restore_ok"] = path.read_text(encoding="utf-8") == backup.read_text(encoding="utf-8")

    # 判定：该红的红了没
    want = set(m["expect_red"])
    got = set(result["b_red"])
    result["ok"] = bool(want) and want.issubset(got) and not result["error"] and result["restore_ok"]
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="只跑某个变异 id（如 M3）")
    args = ap.parse_args()

    muts = [m for m in MUTATIONS if not args.only or m["id"] == args.only]
    if not muts:
        print(f"没有 id={args.only} 的变异")
        return 2

    workdir = Path(tempfile.mkdtemp(prefix="mutate_gate_adv_"))
    print(f"★ 变异验证 —— B 边对抗守卫（备份目录：{workdir}，仓库外）\n")
    rows = []
    try:
        for m in muts:
            r = run_one(m, workdir)
            rows.append(r)
            mark = "✔" if r["ok"] else "✘"
            print(f"{mark} [{r['id']}] {r['title']}")
            print(f"     目的：{r['why']}")
            print(f"     B 边守卫：{len(r['b_red'])} 红 / {r['b_green']} 绿"
                  + (f"  红的是：{', '.join(r['b_red'][:3])}" if r["b_red"] else "  ★ 没红！"))
            print(f"     A 边守卫：{len(r['a_red'])} 红 / {r['a_green']} 绿"
                  + (f"  红的是：{', '.join(r['a_red'][:3])}" if r["a_red"] else "  ⇒ 全绿（盲区）"))
            if not r["restore_ok"]:
                print("     ★ 还原校验失败！")
            if r["error"]:
                print(f"     ★ 出错：{r['error']}")
            print()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    bad = [r for r in rows if not r["ok"]]
    a_blind = [r for r in rows if not r["a_red"]]
    print("=" * 72)
    print(f"变异 {len(rows)} 个，B 边守卫全抓：{len(rows) - len(bad)}/{len(rows)}")
    print(f"其中 A 边守卫**全绿**（结构性盲区）：{len(a_blind)}/{len(rows)}")
    if bad:
        print("\n★ 以下变异没被抓到（守卫形同虚设，必须重写）：")
        for r in bad:
            print(f"  [{r['id']}] {r['title']}  error={r['error']}")
        return 1
    print("\n⇒ 全部变异都被 B 边守卫抓住 ✔")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
