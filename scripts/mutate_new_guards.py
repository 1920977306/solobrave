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

    # ── 2026-10-25 积分闸门（老大：每一个功能都需要积分控制）新增的变异 ──
    ("积分变异A：周报生成不扣费（白干）",
     "daren_bot/weekly.py",
     '    status, _task_id, bal = credits.charge_and_enqueue(\n'
     '        tenant_key, "weekly_report", douyin_id, "weekly_report",',
     '    status, _task_id, bal = credits.enqueue_free(\n'
     '        tenant_key, "weekly_report", douyin_id, "weekly_report",',
     "tests/test_credits_gate.py"),

    ("积分变异B：周报自动采集不扣费（白干）",
     "daren_bot/weekly.py",
     '        status, _task_id, bal = credits.charge_and_enqueue(\n'
     '            tk, "douyin", dyid, "weekly_collect", chat_id=chat_id,',
     '        status, _task_id, bal = credits.enqueue_free(\n'
     '            tk, "douyin", dyid, "weekly_collect", chat_id=chat_id,',
     "tests/test_credits_gate.py"),

    ("积分变异C：采集失败重采不扣费（白干）",
     "daren_bot/executors.py",
     '                _st, _tid, _bal = _credits.charge_and_enqueue(\n'
     '                    task["tenant_key"], "douyin", _p[\'dyid\'], "recollect",',
     '                _st, _tid, _bal = _credits.enqueue_free(\n'
     '                    task["tenant_key"], "douyin", _p[\'dyid\'], "recollect",',
     "tests/test_credits_gate.py"),

    ("积分变异D：成本表里删掉一个动作（漏定价）",
     "daren_bot/config.py",
     '    "weekly_report": COST_WEEKLY_REPORT,\n',
     '',
     "tests/test_credits_gate.py"),

    ("积分变异E：成本表塞进一个没人用的动作（死价）",
     "daren_bot/config.py",
     '    "recollect": COST_RECOLLECT,\n',
     '    "recollect": COST_RECOLLECT,\n    "never_used_probe": 7,\n',
     "tests/test_credits_gate.py"),

    ("积分变异F：未登记动作不再报错（悄悄变成免费白干）",
     "daren_bot/credits.py",
     '    try:\n'
     '        return config.COST_TABLE[action]\n'
     '    except KeyError:\n'
     '        raise KeyError(\n'
     '            f"未登记的计费动作 {action!r} —— "\n'
     '            "消耗算力的功能必须先在 config.COST_TABLE 里定价"\n'
     '        ) from None',
     '    return config.COST_TABLE.get(action, 0)',
     "tests/test_credits_gate.py"),

    ("积分变异G：余额不足也照样扣费（扣成负数）",
     "daren_bot/db.py",
     "        balance = int(br[\"balance\"]) if br else 0\n"
     "        if balance < cost:\n"
     "            return \"insufficient\", \"\", balance\n",
     "        balance = int(br[\"balance\"]) if br else 0\n"
     "        if False:\n"
     "            return \"insufficient\", \"\", balance\n",
     "tests/test_credits_gate.py"),

    # ★ 核心：把「幂等命中也扣费」改回来 ⇒ 重复请求净多扣（2026-10-25 实测的真漏洞）
    ("积分变异H：幂等命中也照样扣费（重复请求净多扣）",
     "daren_bot/db.py",
     "        r = _find_pending(conn, tenant_key, task_type, target)\n"
     "        if r:\n"
     "            br = conn.execute(\"SELECT balance FROM bot_credits_accounts \"\n"
     "                              \"WHERE tenant_key=?\", (tenant_key,)).fetchone()\n"
     "            return \"duplicate\", r[\"id\"], int(br[\"balance\"]) if br else 0\n",
     "        r = None\n",
     "tests/test_credits_gate.py"),

    ("积分变异I：先扣费再入队（两事务 ⇒ 重复扣费只建一个任务）",
     "daren_bot/credits.py",
     "    cost = cost_of(action)\n"
     "    return db.enqueue_task_charged(tenant_key, task_type, target, cost, action,\n"
     "                                   cache_hit=cache_hit, chat_id=chat_id,\n"
     "                                   payload=payload)",
     "    cost = cost_of(action)\n"
     "    db.deduct(tenant_key, cost, action, cache_hit=cache_hit)\n"
     "    tid = db.enqueue_task(tenant_key, task_type, target, cost,\n"
     "                          cache_hit=cache_hit, chat_id=chat_id, payload=payload)\n"
     "    return \"ok\", tid, db.get_balance(tenant_key)",
     "tests/test_credits_gate.py"),

    ("积分变异J：扣费流水不关联任务 id（退款/查账对不上）",
     "daren_bot/db.py",
     "        _ledger(conn, tenant_key, action, cost, new_balance, cache_hit, task_id)\n",
     "        _ledger(conn, tenant_key, action, cost, new_balance, cache_hit, '')\n",
     "tests/test_credits_gate.py"),

    ("积分变异K：任务上记的退款额 ≠ 实扣额",
     "daren_bot/db.py",
     "            (task_id, tenant_key, task_type, target, chat_id,\n"
     "             cache_hit, cost, payload, now_ts()))",
     "            (task_id, tenant_key, task_type, target, chat_id,\n"
     "             cache_hit, 1, payload, now_ts()))",
     "tests/test_credits_gate.py"),

    ("积分变异L：恢复裸的 credits.charge() 入口（两事务坑的源头）",
     "daren_bot/credits.py",
     "def enqueue_free(tenant_key: str, task_type: str, target: str,",
     "def charge(tenant_key: str, action: str, ref_task_id: str = '',\n"
     "           cache_hit: int = 0) -> tuple:\n"
     "    cost = cost_of(action)\n"
     "    return db.deduct(tenant_key, cost, action, ref_task_id=ref_task_id,\n"
     "                     cache_hit=cache_hit)\n"
     "\n"
     "\n"
     "def enqueue_free(tenant_key: str, task_type: str, target: str,",
     "tests/test_credits_gate.py"),

    # ⚠️ 刻意**不做**的变异：`credits.charge_and_enqueue` 改叫别的名字
    #   —— 那属于「改名」，不是「护栏失效」，会连带打红一堆无关测试，
    #   属于噪声变异（不是等价变异，也不是缺陷）。
]

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

# ★ 还原校验只查**变异动过的文件**，不看全仓 diff。
#   为什么：跑变异期间人可能同时在改别的文件（本轮就撞上了：跑变异时
#   我在写 AGENTS.md ⇒ 全仓 diff 必然不同 ⇒ 误报「改动被冲掉了」）。
#   ⇒ 真正要验的是「我改的那几个文件，还原回来了没有」。
#   ⇒ 用**备份副本的内容**逐字比对（比 git diff 更直接、更可信）。
bad_restore = []
for rel, backup in _BACKUPS.items():
    cur = (ROOT / rel).read_text(encoding="utf-8")
    if cur != backup:
        bad_restore.append(rel)
if bad_restore:
    print("❌ 以下文件还原后与变异前不一致（改动被冲掉了）：")
    for r in bad_restore:
        print("   -", r)
    failed.append("工作区还原不一致")
else:
    print(f"✅ 变异动过的 {len(_BACKUPS)} 个文件都已逐字还原")

print("\n" + "=" * 60)
if failed:
    print("❌ 变异验证未通过：")
    for f in failed:
        print("   -", f)
    sys.exit(1)
print("✅ 全部变异都被新护栏抓住，且工作区改动完好")
