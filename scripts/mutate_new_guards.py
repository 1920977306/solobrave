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

    # ⚠️ P0-3 变异B（驾驶舱消费端改回 select 我方列）**已作废**：
    #   取数已收拢到 `reco_source`（P0-2 变异A 覆盖白名单），
    #   `_build_reco_chain` 自己不再 select 列 ⇒ 那个变异已无落点。

    # ⚠️ P0-3 变异B / 变异C（两个消费端改回 select 我方列）**已作废**：
    #   取数已收拢到 `reco_source`（P0-2 变异A 覆盖白名单），
    #   服务端/驾驶舱都不再自己 select 列 ⇒ 那两个变异已无落点。
    #   服务端「自己写一份 SQL」这个形态由 P0-4 变异A 覆盖。

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

    # ── 2026-10-25 选品推荐链路（老大目标 3）新增的变异 ──
    ("P0-2 变异A：取数白名单里加回我方出单达人数",
     "daren_cockpit/tools/reco_source.py",
     '"tags", "conversion_rate",',
     '"tags", "conversion_rate", "platform_order_daren_count",',
     "tests/test_reco_data_source.py"),

    ("P0-2 变异B：驾驶舱改回自己写一份 SQL（取数口径分叉）",
     "daren_cockpit/server.py",
     "    return reco_source.build_chain(talent_row, top_n=top_n)",
     "    return reco.recommend(dict(talent_row), [], top_n=top_n)",
     "tests/test_reco_data_source.py"),

    ("P0-2 变异C：推荐意图判据放宽（什么推荐词都拦）",
     "daren_bot/handler.py",
     "    return len(t) <= 4 or bool(_RECO_RANGE_RE.search(t))",
     "    return True",
     "tests/test_reco_bot.py"),

    ("P0-2 变异D：去掉「点名具体商品就不拦」的排除",
     "daren_bot/handler.py",
     "    if _RECO_EXCLUDE_RE.search(t):\n        return False",
     "    if False:\n        return False",
     "tests/test_reco_bot.py"),

    ("P0-2 变异E：算不出推荐时也照样发卡（不「只丢不造」）",
     "daren_bot/handler.py",
     "        if not recos:",
     "        if False:",
     "tests/test_reco_bot.py"),

    ("P0-2 变异F：推荐理由改回用内部话术（策略价带）",
     "daren_bot/feishu_card.py",
     "    if not out:\n"
     "        out = [str(x).strip() for x in (rec.get(\"reasons\") or []) if str(x).strip()]\n"
     "    return out[:limit]",
     "    return [str(x).strip() for x in (rec.get(\"reasons\") or []) "
     "if str(x).strip()][:limit]",
     "tests/test_reco_bot.py"),

    ("P0-2 变异G：推荐卡的「接这个品」按钮不带商品 id",
     "daren_bot/feishu_card.py",
     '{"action": "accept_deal", "product_id": pid},',
     '{"action": "accept_deal"},',
     "tests/test_reco_bot.py"),

    # ── 2026-10-25 服务端口径分叉（候选池被 owner 裁空）新增的变异 ──
    ("P0-4 变异A：服务端改回自己写一份取数 SQL（口径分叉）",
     "solobrave-server.py",
     "        chain = reco_source.build_chain(\n"
     "            dict(trow), top_n=top_n, db_path=_tenant_db_path(_current_tenant_id()))",
     "        import sqlite3 as _sq\n"
     "        from daren_cockpit.tools import product_reco as _pr\n"
     "        _cc = _sq.connect(str(_tenant_db_path(_current_tenant_id())))\n"
     "        _cc.row_factory = _sq.Row\n"
     "        _pp = [dict(x) for x in _cc.execute(\n"
     "            \"SELECT id,name,price,price_range,category,commission_rates,tags,\"\n"
     "            \"conversion_rate,shop_score,good_ratio,influencer_count \"\n"
     "            \"FROM products WHERE status!='archived'\")]\n"
     "        _cc.close()\n"
     "        chain = _pr.recommend(dict(trow), _pp, top_n=top_n)",
     "tests/test_reco_data_source.py"),

    ("P0-4 变异B：商品管理接口的 owner 隔离被顺手去掉",
     "solobrave-server.py",
     "        if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):\n"
     "            uid = _resolve_talent_owner_id(auth)\n"
     "            visible_ids = {uid} | set(_get_user_emp_ids(uid))\n"
     "            products = [p for p in products "
     "if (p.get('created_by') or p.get('createdBy') or '') in visible_ids]",
     "        if False:\n"
     "            uid = _resolve_talent_owner_id(auth)\n"
     "            visible_ids = {uid} | set(_get_user_emp_ids(uid))\n"
     "            products = [p for p in products "
     "if (p.get('created_by') or p.get('createdBy') or '') in visible_ids]",
     "tests/test_reco_data_source.py"),
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
