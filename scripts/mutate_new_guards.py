#!/usr/bin/env python3
"""变异验证：新加的护栏测试必须**能红**（否则就是恒真检查）。

每个变异 = 把修复改回旧行为 → 跑对应测试 → 必须 FAIL → 还原。

★ 两条纪律：
  ① 改完文件必须 `sleep 1.2` 让 mtime 跨秒，并清掉**仓库自己的** `__pycache__`
     （别清 venv 的，慢）—— 否则 python 可能加载旧字节码，结论不可信。
  ② **还原必须用备份副本，不能用 `git checkout --`** ——
     未提交的真改动会被一起冲掉（2026-10-25 实测踩过：一轮变异把 5 个文件的
     改动全冲没了）。脚本跑完会校验 `git diff` 还在。
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

_BACKUPS: dict = {}


def clear_pycache():
    for p in ROOT.rglob("__pycache__"):
        if {".venv", "venv", "node_modules"} & set(p.parts):
            continue
        shutil.rmtree(p, ignore_errors=True)


def patch(rel: str, old: str, new: str):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert old in src, f"变异锚点没找到：{rel}\n  {old[:80]}"
    _BACKUPS.setdefault(rel, src)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    time.sleep(1.2)          # ★ 让 mtime 跨秒
    clear_pycache()


def restore(rel: str):
    """还原成**变异前的样子**（备份副本），不是 HEAD。"""
    p = ROOT / rel
    p.write_text(_BACKUPS[rel], encoding="utf-8")
    time.sleep(1.2)
    clear_pycache()


def run_tests(selector: str) -> tuple:
    r = subprocess.run([sys.executable, "-m", "pytest", selector, "-q",
                        "--no-header", "-p", "no:cacheprovider"],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "")[-800:]


CASES = [
    # (名字, 文件, 旧文本, 新文本, 测试选择器)
    ("P0-3 变异A：达人出单数改回读我方出单达人数",
     "daren_cockpit/tools/product_reco.py",
     'sold = product.get("influencer_count")',
     'sold = product.get("platform_order_daren_count")',
     "tests/test_reco_data_source.py"),

    ("P0-3 变异B：消费端改回 select 我方出单达人数",
     "daren_cockpit/server.py",
     '"influencer_count"',
     '"platform_order_daren_count"',
     "tests/test_reco_data_source.py"),

    ("P0-3 变异C：消费端(solobrave-server)改回 select 我方出单达人数",
     "solobrave-server.py",
     "'board_rank', 'influencer_count'",
     "'board_rank', 'platform_order_daren_count'",
     "tests/test_reco_data_source.py"),

    ("P0-3 变异D：维度名改回「达人已出单数」",
     "daren_cockpit/tools/product_reco.py",
     'DimSpec("talents_sold",   "带货达人数",     0.10, "log"),',
     'DimSpec("talents_sold",   "达人已出单数",   0.10, "log"),',
     "tests/test_reco_data_source.py"),

    ("P1-5 变异A：排品表卡改回只筛今天",
     "daren_bot/handler.py",
     "future = plan.future_slots(slots or [])",
     'future = [s for s in (slots or []) '
     'if s.date == __import__("datetime").datetime.now().strftime("%Y-%m-%d")]',
     "tests/test_card_plan_view.py"),

    ("P1-5 变异B：view_plan 按钮不再带 product_id",
     "daren_bot/feishu_card.py",
     '        vp_value["product_id"] = product_id',
     "        pass",
     "tests/test_card_plan_view.py"),

    ("P1-5 变异C：排品表卡不再保留「接这个品」按钮",
     "daren_bot/handler.py",
     'actions.append(fc._button("接这个品",',
     'actions.append(fc._button("接这个品X",',
     "tests/test_card_plan_view.py"),

    # ⚠️ 刻意**不做**的等价变异：把 `plan.display_name` 换成 `plan_notify._display_name`
    #   —— 后者内部就是转调前者（plan_notify.py:429），行为完全一样，
    #   属**等价变异**（不是缺陷）⇒ 不该有测试去抓它。
    #   （等价变异当缺陷 = 逼着人去改等价代码，纯浪费。）
]

before = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True).stdout

failed = []
for name, rel, old, new, sel in CASES:
    print(f"\n=== {name} ===")
    try:
        patch(rel, old, new)
    except AssertionError as e:
        print("  ⚠️ ", e)
        failed.append(name)
        continue
    try:
        rc, out = run_tests(sel)
        if rc == 0:
            print("  ❌ 变异后测试仍全绿 ⇒ 这条护栏是恒真检查，拦不住任何东西")
            failed.append(name)
            print(out)
        else:
            line = [l for l in out.splitlines() if "failed" in l or "passed" in l]
            print("  ✅ 变异后测试变红 ⇒", (line[-1] if line else out.strip()[-120:]))
    finally:
        restore(rel)

after = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True).stdout
print("\n" + "=" * 60)
if before != after:
    print("❌ 还原后工作区与变异前不一致（改动被冲掉了）：")
    print("--- before ---\n" + before + "--- after ---\n" + after)
    failed.append("工作区还原不一致")
if failed:
    print("❌ 变异验证未通过：")
    for f in failed:
        print("   -", f)
    sys.exit(1)
print("✅ 全部变异都被新护栏抓住，且工作区改动完好")
