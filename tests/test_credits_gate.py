"""积分闸门（老大 2026-10-25：「每一个功能都需要积分控制 毕竟是消耗算力 不消耗的就算了」）。

锁住三条：
1. **没有白干的功能** —— 凡是消耗算力（采集 / LLM 判分 / 视觉 / 转写 / 写报告）
   的入队，都必须带**正数** `credits_cost`；只有「缓存命中」和「管理员后门」可以是 0。
   ⇒ 这条是**静态扫描整个 `daren_bot/`**：以后新加功能忘了扣分 ⇒ 直接红。
2. **成本表是唯一真源** —— 表里每个动作都要有入口在用（不许有死价）；
   代码里 `credits.charge(tk, "xxx")` 的 `"xxx"` 必须在表里（不许有没定价的动作）。
3. **闸门行为** —— 余额不足**不扣、不入队**；扣了要写流水；未登记动作直接报错。

★ 为什么用**静态扫描**而不是逐个入口写行为测试：
   入口会越来越多，人工枚举必然漏（2026-10-25 体检就查出周报 / 自动采集 /
   重采三处一直白干）⇒ 扫描「所有入队调用」才是能长期兜住的做法。
"""
import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import config, credits, db  # noqa: E402

BOT_DIR = ROOT / "daren_bot"

# ★ 允许 `credits_cost == 0` 的入口 —— **必须逐条写明理由**，
#   新增例外的人得想清楚为什么这条不收费。
ZERO_COST_ALLOWED = {
    ("handler.py", "_route_trial_weekly"):
        "管理员后门：老大手动试跑周报，扣的是平台自己的算力，不扣达人的分",
    ("handler.py", "_route_qa"):
        "问答缓存命中：根本没调 LLM ⇒ 没消耗算力 ⇒ 不扣分（但记 cache_hit 流水留痕）",
}

# ★ 闸门**实现自身**的转发层 —— 不是业务入口，不参与「白干」判定。
#   - `credits.charge_and_enqueue` 把 `config.COST_TABLE` 里的价格
#     传给底层原子入口（cost 是运行时查表得来的，不是字面量）。
#   - `credits.enqueue_free` 是**零成本**入口的实现（cost 恒为 0）。
#   ⚠️ 这里只豁免「这两个函数」；闸门**不许**再往上加豁免，
#      否则「扫不到任何业务入口」= 恒真闸门（2026-10-25 实测踩过）。
GATE_IMPL = {("credits.py", "charge_and_enqueue"), ("credits.py", "enqueue_free")}

# 最小 bot 库（只要积分相关表 + 入队表）
DDL = """
CREATE TABLE bot_credits_accounts (
  tenant_key TEXT PRIMARY KEY,
  balance    INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
CREATE TABLE bot_credits_ledger (
  id                TEXT PRIMARY KEY,
  tenant_key        TEXT NOT NULL,
  action            TEXT NOT NULL,
  cost              INTEGER NOT NULL,
  balance_after     INTEGER NOT NULL,
  cache_hit         INTEGER NOT NULL DEFAULT 0,
  ref_task_id       TEXT DEFAULT '',
  recharge_order_id TEXT DEFAULT '',
  created_at        INTEGER NOT NULL
);
CREATE TABLE bot_collect_tasks (
  id          TEXT PRIMARY KEY,
  tenant_key  TEXT NOT NULL,
  type        TEXT NOT NULL,
  target      TEXT NOT NULL,
  chat_id     TEXT DEFAULT '',
  status      TEXT NOT NULL,
  cache_hit   INTEGER DEFAULT 0,
  credits_cost INTEGER DEFAULT 0,
  payload     TEXT DEFAULT '',
  created_at  INTEGER NOT NULL
);
CREATE TABLE bot_week_counters (
  tenant_key TEXT NOT NULL,
  douyin_id  TEXT NOT NULL,
  week_no    INTEGER NOT NULL,
  count      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (tenant_key, douyin_id, week_no)
);
"""


# ─────────────── 静态扫描工具 ───────────────

# ★ 扫描的**两个**入队入口 —— 少扫一个 = 闸门有缺口（恒真闸门）：
#   ① `db.enqueue_task_charged`  —— 消耗算力的路径（扣费+入队，一个原子操作）
#   ② `db.enqueue_task`          —— 零成本 / 管理员后门
#   ⚠️ 2026-10-25 实测：扣费与入队合并成原子操作后，原先只扫 `enqueue_task`
#      的扫描器**扫不到任何消耗算力的路径了** ⇒ 闸门恒真 ⇒ 必须两个都扫。
CHARGE_ENTRY = "enqueue_task_charged"
FREE_ENTRY = "enqueue_task"


def _enqueue_calls():
    """扫 `daren_bot/` 下所有入队调用（原子入口 + 零成本入口）。

    返回 [(文件名, 所属函数名, Call 节点)]。
    """
    out = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
                if name in (CHARGE_ENTRY, FREE_ENTRY):
                    out.append((path.name, func.name, node))
    # 嵌套函数会重复计数（ast.walk 会重复进入）⇒ 按行号去重
    seen = set()
    uniq = []
    for fname, func_name, node in out:
        key = (fname, node.lineno)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((fname, func_name, node))
    return uniq


def _cost_value(node):
    """解析 `credits_cost` 实参：字面量 int 或 `config.COST_*`。
    解析不出来返回 None（= 闸门判不了 ⇒ 也要红）。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id == "config":
        val = getattr(config, node.attr, None)
        return val if isinstance(val, int) else None
    return None


def _is_cache_hit(node) -> bool:
    return any(kw.arg == "cache_hit" and isinstance(kw.value, ast.Constant)
               and kw.value.value == 1 for kw in node.keywords)


def _entry_name(node) -> str:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _free_entry_calls():
    """扫所有 `credits.enqueue_free(...)` 调用点 → [(文件名, 函数名)]。

    ★ 为什么单独扫：它是**闸门自己的零成本入口**，实参里**没有 cost**
      ⇒ 通用扫描器看不到它 ⇒ 有人把收费路径改成调它就能绕过整道闸门
      （2026-10-25 变异验证实测抓到：18 条测试全绿）。
    """
    out = []
    for path in sorted(BOT_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                fn = node.func
                if not (isinstance(fn, ast.Attribute) and fn.attr == "enqueue_free"):
                    continue
                if not (isinstance(fn.value, ast.Name)
                        and fn.value.id.lstrip("_") == "credits"):
                    continue
                out.append((path.name, func.name))
    return out


# ─────────────── ① 没有白干的功能 ───────────────

def test_every_compute_task_is_charged():
    """★ 凡是消耗算力的入队，`credits_cost` 必须是正数。

    允许 0 的只有两种：**缓存命中**（本来就不调 LLM）和**管理员后门**（白名单）。

    ⚠️ 原子入口（`enqueue_task_charged`）的签名是
       `(tenant_key, task_type, target, cost, action, ...)`
       ⇒ cost 在**第 4 位**（下标 3）；零成本入口 `enqueue_task` 同签名。
    """
    offenders = []
    for fname, func_name, node in _enqueue_calls():
        if (fname, func_name) in GATE_IMPL:
            continue          # 闸门实现自身（转发层，不是业务入口）
        if len(node.args) < 4:
            offenders.append(f"{fname}:{node.lineno} {func_name} —— 说不清成本（参数不足 4 个）")
            continue
        cost = _cost_value(node.args[3])
        if cost is None:
            offenders.append(
                f"{fname}:{node.lineno} {func_name} —— 成本表达式解析不了"
                "（请用 config.COST_* 或整数字面量）")
            continue
        if cost > 0:
            continue
        if _is_cache_hit(node):
            continue          # 缓存命中：不调 LLM ⇒ 不收费（既有语义）
        if (fname, func_name) in ZERO_COST_ALLOWED:
            continue          # 管理员后门
        offenders.append(
            f"{fname}:{node.lineno} {func_name} —— 消耗算力却 credits_cost=0（白干）")
    assert not offenders, (
        "以下入队消耗算力但没收费：\n  " + "\n  ".join(sorted(set(offenders)))
        + "\n⇒ 消耗算力的功能必须在 config.COST_TABLE 里定价，并走 credits.charge_and_enqueue()")


def test_charged_entries_use_the_atomic_gate():
    """★ 消耗算力的入队**必须**走原子入口 `enqueue_task_charged`。

    为什么：入队是**幂等**的（同租户+同类型+同目标已有 pending ⇒ 返回旧任务、不新建）。
    若「扣费」与「入队」是两次独立调用 ⇒ 达人重复发一次就**扣两次钱、只建一个任务**
    ⇒ 任务失败时只退一份 ⇒ **净多扣**（2026-10-25 实测：扣 200 / 只退 100）。
    ⇒ 扣费与入队必须合并成**一个事务**。
    """
    bad = []
    for fname, func_name, node in _enqueue_calls():
        if (fname, func_name) in GATE_IMPL:
            continue          # 闸门实现自身
        if _entry_name(node) != CHARGE_ENTRY:
            continue
        # 原子入口的 action 在第 5 位（下标 4）⇒ 必须是个定价过的动作名
        if len(node.args) < 5 or not isinstance(node.args[4], ast.Constant):
            bad.append(f"{fname}:{node.lineno} {func_name} —— 原子入口没带 action")
            continue
        action = node.args[4].value
        if action not in config.COST_TABLE:
            bad.append(f"{fname}:{node.lineno} {func_name} —— action {action!r} 没在成本表里")
    assert not bad, "以下原子入队的 action 不对：\n  " + "\n  ".join(sorted(set(bad)))


def test_zero_cost_entries_are_explicitly_whitelisted():
    """★ 走零成本入口 `enqueue_task` 的，必须是**白名单里逐条写明理由**的。

    这是「白名单不许扩大」的守卫：以后有人想图省事用零成本入口，
    必须先在 ZERO_COST_ALLOWED 里写清楚为什么这条不收费。
    """
    unlisted = []
    for fname, func_name, node in _enqueue_calls():
        if (fname, func_name) in GATE_IMPL:
            continue          # 闸门实现自身
        if _entry_name(node) != FREE_ENTRY:
            continue
        cost = _cost_value(node.args[3]) if len(node.args) >= 4 else None
        if cost is not None and cost > 0:
            continue          # 走零成本入口但报了正数成本 ⇒ 性质不同，另算
        if _is_cache_hit(node):
            continue          # 缓存命中
        if (fname, func_name) in ZERO_COST_ALLOWED:
            continue
        unlisted.append(f"{fname}:{node.lineno} {func_name}")
    assert not unlisted, (
        "以下入队走了零成本入口但没登记白名单：\n  " + "\n  ".join(sorted(set(unlisted)))
        + "\n⇒ 要么它其实消耗算力（请改走原子入口 charge_and_enqueue），"
          "要么在 ZERO_COST_ALLOWED 里写明为什么不收费")


def test_free_entry_calls_are_all_whitelisted():
    """★★ `credits.enqueue_free(...)` 的**每个调用点**都必须登记理由。

    ★ 为什么这条必须单独写（2026-10-25 变异验证抓出来的**真缺口**）：
      静态扫描只看 `db.enqueue_task*` 调用点的 cost 实参，
      而 `credits.enqueue_free(...)` 是**闸门自己的零成本入口**，
      它的实参里**没有 cost** ⇒ 扫不到 ⇒
      有人把收费路径改调 `enqueue_free`（cost 恒为 0）就能**绕过整道闸门**，
      测试却全绿。
      （实测：变异把「周报自动采集」「采集失败重采」两处收费改成 `enqueue_free`
        ⇒ 18 条测试全过 ⇒ 护栏形同虚设。）
      ⇒ 所以必须**单独扫这个入口的调用点**，逐条登记「为什么这次不收费」。
    """
    unlisted = []
    scanned = 0
    for fname, func_name in _free_entry_calls():
        scanned += 1
        if (fname, func_name) in ZERO_COST_ALLOWED:
            continue
        unlisted.append(f"{fname} {func_name}")
    assert not unlisted, (
        "以下调用了零成本入口 credits.enqueue_free 但没登记理由：\n  "
        + "\n  ".join(sorted(set(unlisted)))
        + "\n⇒ 它是不是真的没消耗算力？确定的话，在 ZERO_COST_ALLOWED 里写明原因；"
          "否则改走原子入口 charge_and_enqueue()（消耗算力必须收费）")
    assert scanned > 0, "★ 一个 enqueue_free 调用点都没扫到 ⇒ 测试已恒真（形同虚设）"


def test_zero_cost_allowlist_entries_are_still_referenced():
    """★ 白名单不许挂着已经不存在的入口（免得例外悄悄失效）。

    ★ 白名单项可能出现在**两个**入口里（判定口径要跟实现走）：
      - `db.enqueue_task(...)`     —— 零成本入队（管理员后门）
      - `credits.enqueue_free(...)` —— 闸门的零成本入口（缓存命中）
      ⇒ 两边都算「还引用着」。
    """
    calls = {(f, fn) for f, fn, _ in _enqueue_calls()}
    calls |= set(_free_entry_calls())
    stale = [f"{f}::{fn}（{why}）" for (f, fn), why in ZERO_COST_ALLOWED.items()
             if (f, fn) not in calls]
    assert not stale, f"白名单里的入口已经不存在了，请删掉：{stale}"


# ─────────────── ② 成本表是唯一真源 ───────────────

def test_cost_table_has_no_dead_prices():
    """★ 表里不许有死价（定义了没人用 ⇒ 以后会忘了改、也说明想错了）。"""
    src = "\n".join(
        p.read_text(encoding="utf-8")
        for p in BOT_DIR.rglob("*.py")
        if "__pycache__" not in p.parts and p.name != "config.py")
    unused = [a for a in config.COST_TABLE if f'"{a}"' not in src]
    assert not unused, f"成本表里有没被任何入口使用的动作：{unused}"


def test_every_charge_action_is_priced():
    """★ 代码里扣费动作名必须在成本表里（不许有「忘了定价」的动作）。

    扫描**两个**扣费入口（声明端要跟使用端一致！）：
      - `credits.charge(tk, "xxx")`          —— action 在第 2 位（下标 1）
      - `credits.charge_and_enqueue(tk, type, target, "xxx", ...)`
                                           —— action 在第 4 位（下标 3）
    ⚠️ 2026-10-25 实测：扣费与入队合并成原子入口后，若只扫旧的 `charge`，
       这个测试会**扫不到任何调用点** ⇒ 恒真闸门（看着在管，其实没管）。
       ⇒ 所以两个入口都必须扫。
    """
    missing = []
    scanned = 0
    for path in sorted(BOT_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Attribute)
                    and isinstance(fn.value, ast.Name) and fn.value.id == "credits"):
                continue
            if fn.attr == "charge":
                arg_idx = 1
            elif fn.attr == "charge_and_enqueue":
                arg_idx = 3
            else:
                continue
            if len(node.args) <= arg_idx or not isinstance(node.args[arg_idx], ast.Constant):
                continue
            action = node.args[arg_idx].value
            scanned += 1
            if action not in config.COST_TABLE:
                missing.append(f"{path.name}:{node.lineno} {action}")
    assert not missing, f"这些扣费动作没在 config.COST_TABLE 里定价：{missing}"
    assert scanned > 0, (
        "★ 一个扣费调用点都没扫到 ⇒ 这个测试已经恒真（形同虚设）。"
        "扣费入口改名了？记得同步更新扫描规则（声明端 ≠ 使用端）。")


def test_cost_table_covers_all_consuming_features():
    """★ 已知的消耗算力功能都必须在表里（防「新增功能忘了加进表」）。"""
    for action in ("collect_douyin", "collect_product", "collect_product_cached",
                   "vision", "qa", "video",
                   "weekly_collect", "weekly_report", "recollect"):
        assert action in config.COST_TABLE, f"成本表少了 {action}"
        assert config.COST_TABLE[action] > 0, f"{action} 的价格必须为正"


# ─────────────── ③ 闸门行为 ───────────────

@pytest.fixture()
def botdb(tmp_path, monkeypatch):
    path = tmp_path / "bot.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(DDL)
    conn.execute("INSERT INTO bot_credits_accounts (tenant_key,balance,updated_at) "
                 "VALUES ('tk_x',1000,1)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def test_charge_deducts_and_writes_ledger(botdb):
    """★ 扣费成功 ⇒ 扣了钱 + 写了流水（流水能看出「这分是干什么的」）。"""
    st, _tid, bal = credits.charge_and_enqueue(
        "tk_x", "qa", "q_1", "qa", chat_id="oc_x")
    assert st == "ok"
    assert bal == 1000 - config.COST_QA
    conn = sqlite3.connect(str(botdb))
    row = conn.execute("SELECT action, cost, balance_after FROM bot_credits_ledger "
                       "WHERE tenant_key='tk_x'").fetchone()
    conn.close()
    assert row == ("qa", config.COST_QA, 1000 - config.COST_QA)


def test_charge_refuses_when_insufficient_and_does_not_write_ledger(botdb):
    conn = sqlite3.connect(str(botdb))
    conn.execute("UPDATE bot_credits_accounts SET balance=0 WHERE tenant_key='tk_x'")
    conn.commit()
    conn.close()
    st, tid, bal = credits.charge_and_enqueue(
        "tk_x", "douyin", "dy_x", "collect_douyin", chat_id="oc_x")
    assert st == "insufficient"
    assert tid == ""
    assert bal == 0
    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger").fetchone()[0]
    conn.close()
    assert n == 0, "余额不足却写了流水（等于白扣）"


def test_cost_of_unknown_action_raises():
    with pytest.raises(KeyError):
        credits.cost_of("压根没登记的动作")


def test_no_bare_charge_entry_exists():
    """★ 闸门**不许**提供「只扣费不建任务」的裸入口。

    为什么：入队是幂等的 ⇒ 「先扣费、再入队」两个独立事务 ⇒ 重复请求会
    **扣两次钱、只建一个任务** ⇒ 失败只退一份 ⇒ **净多扣**（2026-10-25 实测）。
    ⇒ 只有一个原子入口 `charge_and_enqueue()`，
    想要「只扣费」的入口 ⇒ 迟早会有人写出两事务坑。
    """
    assert not hasattr(credits, "charge"), \
        "credits.charge() 是「先扣费再干活」两事务坑的源头，请统一用 charge_and_enqueue()"


class _Sender:
    def __init__(self):
        self.texts = []

    def send_text(self, chat_id, text):
        self.texts.append(text)


def test_weekly_report_is_charged(botdb, monkeypatch):
    """★ 周报要调 LLM 写报告 ⇒ 必须扣分（原来一直白干）。"""
    from daren_bot import weekly
    monkeypatch.setattr(weekly.db, "get_latest_chat_id", lambda tk: "oc_x")
    sender = _Sender()
    weekly._enqueue_report("tk_x", "dy_x", missing=0, sender=sender)
    assert db.get_balance("tk_x") == 1000 - config.COST_WEEKLY_REPORT
    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_collect_tasks "
                     "WHERE type='weekly_report'").fetchone()[0]
    conn.close()
    assert n == 1, "周报没入队"


def test_weekly_report_skipped_when_out_of_credits(botdb, monkeypatch):
    """★ 余额不足 ⇒ 不生成周报（不白干），且**主动告诉达人**为什么收不到。"""
    from daren_bot import weekly
    monkeypatch.setattr(weekly.db, "get_latest_chat_id", lambda tk: "oc_x")
    conn = sqlite3.connect(str(botdb))
    conn.execute("UPDATE bot_credits_accounts SET balance=0 WHERE tenant_key='tk_x'")
    conn.commit()
    conn.close()
    sender = _Sender()
    weekly._enqueue_report("tk_x", "dy_x", missing=0, sender=sender)
    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_collect_tasks").fetchone()[0]
    conn.close()
    assert n == 0, "余额不足还生成了周报（白干）"
    assert sender.texts, "余额不足时没告诉达人（订阅了却收不到 = 静默失联）"
    assert "积分" in sender.texts[0]


def test_zero_cost_features_are_not_in_cost_table():
    """★ 零成本功能（纯查库 + 纯函数）不许进成本表 ——
    进表 = 给白干的路开闸门。"""
    for action in ("plan_query", "reco_query", "accept_deal", "balance_query",
                   "weekly_sub", "cockpit_link"):
        assert action not in config.COST_TABLE, \
            f"{action} 是零成本功能，不该进成本表"


# ─────────────── ⑤ 扣费与入队必须原子（2026-10-25 实测的真漏洞）───────────────

def test_repeat_request_does_not_double_charge(botdb):
    """★★ 达人重复发同一个请求 ⇒ **只扣一次钱、只建一个任务**。

    ★ 为什么这是必须锁死的（2026-10-25 实测出来的真漏洞）：
      入队是**幂等**的（同租户+同类型+同目标已有 pending/collecting ⇒ 返回旧任务、
      **不新建**）。若「扣费」与「入队」是两个独立事务：
        第 1 次：扣 100 + 建任务
        第 2 次：又扣 100 + 命中幂等（**没建新任务**）
      ⇒ 流水扣了 200，任务只有一个（credits_cost=100）
      ⇒ 任务失败时 worker 只按任务上记的金额退 100 ⇒ **净多扣 100**。
      ⇒ 现在扣费与入队合并成**一个原子操作**（`enqueue_task_charged`），
        且**只有真建了新任务才扣分**（命中幂等 = 没多消耗算力 = 不收费）。
    """
    from daren_bot import db as _db
    st1, tid1, bal1 = _db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    assert st1 == "ok", st1
    st2, tid2, bal2 = _db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")

    assert st2 == "duplicate", "第二次应命中幂等"
    assert tid2 == tid1, "命中幂等应复用同一个任务"
    assert bal2 == bal1, "命中幂等**不应再扣分**（余额不变）"
    assert _db.get_balance("tk_x") == 1000 - config.COST_COLLECT_DOUYIN, \
        "重复请求只应扣一次钱（不能净多扣）"

    conn = sqlite3.connect(str(botdb))
    n_tasks = conn.execute("SELECT COUNT(*) FROM bot_collect_tasks").fetchone()[0]
    n_ledger = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger").fetchone()[0]
    conn.close()
    assert n_tasks == 1, f"应只有 1 个任务，实际 {n_tasks}"
    assert n_ledger == 1, f"应只有 1 条扣费流水，实际 {n_ledger}（重复扣费了）"


def test_charged_enqueue_writes_ledger_and_task_together(botdb):
    """★ 扣费流水必须带 `ref_task_id` ⇒ 退款/查账能双向对得上。"""
    from daren_bot import db as _db
    st, tid, _bal = _db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    assert st == "ok"
    conn = sqlite3.connect(str(botdb))
    row = conn.execute("SELECT action, cost, ref_task_id FROM bot_credits_ledger").fetchone()
    task_cost = conn.execute("SELECT credits_cost FROM bot_collect_tasks").fetchone()[0]
    conn.close()
    assert row[0] == "collect_douyin"
    assert row[1] == config.COST_COLLECT_DOUYIN
    assert row[2] == tid, "流水必须关联到任务 id（否则退款/查账对不上）"
    assert task_cost == config.COST_COLLECT_DOUYIN, \
        "任务上记的退款额必须 == 实扣额（否则失败时退款对不上）"


def test_insufficient_credits_creates_no_task(botdb):
    """★ 余额不足 ⇒ 不扣费、**不入队**、不写流水。"""
    from daren_bot import db as _db
    conn = sqlite3.connect(str(botdb))
    conn.execute("UPDATE bot_credits_accounts SET balance=0 WHERE tenant_key='tk_x'")
    conn.commit()
    conn.close()
    st, tid, bal = _db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    assert st == "insufficient"
    assert tid == ""
    assert bal == 0
    conn = sqlite3.connect(str(botdb))
    n_tasks = conn.execute("SELECT COUNT(*) FROM bot_collect_tasks").fetchone()[0]
    n_ledger = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger").fetchone()[0]
    conn.close()
    assert n_tasks == 0, "余额不足却建了任务（会白跑算力）"
    assert n_ledger == 0, "余额不足却写了流水"


# ─────────────── ④ 扣费与承诺的顺序 ───────────────

def test_douyin_collect_never_promises_before_charging(botdb, monkeypatch):
    """★ 2026-10-25 修的顺序 bug：原来「先回『已开始采集』→ 再扣费」。

    ⇒ 并发下余额被别人扣光时，达人收到「已开始采集」但任务**根本没入队**
    ⇒ 永远等不到报告，还多一句「余额不足」自相矛盾。
    ⇒ 现在必须**先扣费成功**，才允许承诺。
    """
    from daren_bot import handler as H
    conn = sqlite3.connect(str(botdb))
    conn.execute("UPDATE bot_credits_accounts SET balance=0 WHERE tenant_key='tk_x'")
    conn.commit()
    conn.close()
    sender = _Sender()
    H.Router(None)._route_douyin(
        {"tenant_key": "tk_x", "bind_douyin_id": "dy_x"}, "oc_x", "dy_x", sender)
    text = "\n".join(sender.texts)
    assert "已开始采集" not in text, \
        "还没扣费就承诺「已开始采集」（并发下达人永远等不到报告）"
    assert "积分" in text, "余额不足时该说清楚原因"
