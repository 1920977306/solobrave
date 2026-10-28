"""★★ B 边（2026-10-25）独立对抗审查 —— 积分闸门的**第二双眼睛**。

为什么要有这份文件（而不是只读 A 边的 `tests/test_credits_gate.py`）：
  A 边的守卫**全部从「入队侧」出发** —— 扫 `db.enqueue_task*` / `credits.charge*`
  的调用点，问「每个入队都收费吗」。
  ⇒ 它的模型前提是：**消耗算力 = 走队列 = 先扣费后干活**。
  ⇒ 而**这个前提本身没有被任何守卫锁住**：
     一个「直接调 LLM 而不入队」的新功能，能绕过整道闸门，而 A 边的守卫全绿。
  本文件从**另一侧**出发：先枚举「真正消耗算力的调用点」，再倒查它们是否都被覆盖。
  ⇒ 两边的守卫是**互补**的，不是重复的。

★ 本文件**不修改** A 边的任何文件（`credits.py` / `config.py` / `db.py` /
  `executors.py` / `handler.py` / `weekly.py` / `tests/test_credits_gate.py` /
  `tests/test_product_intent.py`）—— B 边只新增文件，改共享模块前先打招呼。

★ 登记表（COMPUTE_SITES / ALLOWED_DEDUCT / ACTION_TASK_TYPES / OUT_OF_SCOPE）
  就是「消耗算力 = 收费」的**显式契约**：以后新增消耗点，必须在表里写明
  「收哪个 action / 为什么不收 / 一次收费最多对应几次调用」——
  写不清楚 ⇒ 说明没想清楚 ⇒ 红灯。
"""
import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daren_bot import config, db  # noqa: E402

BOT_DIR = ROOT / "daren_bot"


# ─────────────────────────── 算力入口识别 ───────────────────────────

# ★ 这些函数名 = 「真的会产生 LLM / 视觉调用」的地方。
#   ⚠️ 改名会打掉整个守卫 ⇒ 改名的人必须回来更新这张表（声明端 ≠ 使用端）。
COMPUTE_ENTRY_NAMES = {
    # 业务层的 LLM 漏斗
    "_qa_llm",                    # executors：文字模型降级链
    "_llm_product_verdict",       # executors：商品结论（绿/黄/红）
    "_vision_extract_product",    # executors：截图识商品
    "_vision_try",                # executors：视觉环的一次尝试
    "_vision_call",               # executors：vision HTTP 调用（提取/问答共用的传输）
    # 传输层
    "call_ring", "call_ring_messages",
    "call_anthropic", "call_openai",
    "call_anthropic_messages", "call_openai_messages",
    "_call_openai", "_call_anthropic",
}

# 主服务端（solobrave-server.py）的 LLM 入口 —— **不在达人闸门范围**，
# 但必须显式登记在 OUT_OF_SCOPE 里（防止以后有人误以为它漏了收费，
# 也防止有人新增一个未声明的消耗面）。
SERVER_COMPUTE_NAMES = {
    "_call_ai_api", "_call_ai_analysis", "_call_ai_for_json",
    "_call_chat_completion", "_call_chat_completion_with_fallback",
    "_call_kimi_vision", "_call_minimax_messages",
}


def _iter_python(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _call_name(node: ast.Call) -> str:
    fn = node.func
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _enclosing_funcs(tree: ast.Module):
    """产出 (函数名, 该函数体内的所有 Call 节点)。"""
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            calls = [n for n in ast.walk(func) if isinstance(n, ast.Call)]
            yield func.name, calls


def _detect_compute_sites():
    """扫 `daren_bot/` 下所有算力调用点 → {(相对路径, 所在函数): 静态点数}。

    ★ 用**相对路径**做键（不是文件名）：`daren_bot/tools/` 下的工具和
      `daren_bot/` 根下的模块同名文件不能混为一谈。
    """
    sites = {}
    for path in _iter_python(BOT_DIR):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func_name, calls in _enclosing_funcs(tree):
            n = sum(1 for c in calls if _call_name(c) in COMPUTE_ENTRY_NAMES)
            if n:
                sites[(rel, func_name)] = sites.get((rel, func_name), 0) + n
    return sites


# ─────────────────────────── 消耗点登记表 ───────────────────────────

# action 的三种特殊取值：
#   "BY_CALLER" —— 这一层自己不定价，由**调用方**计费（写 why 说明谁计费）
#   "TRANSPORT" —— 纯传输层（HTTP 封装），由上层漏斗计费
#   "OPS"       —— 运维工具/探针，烧的是**平台自己的算力**，不扣达人分
ACTION_BY_CALLER = "BY_CALLER"
ACTION_TRANSPORT = "TRANSPORT"
ACTION_OPS = "OPS"

# (相对路径, 所在函数) -> (action, 静态调用点数, 一次收费最多对应几次调用, 理由)
COMPUTE_SITES = {
    ("daren_bot/executors.py", "execute_product_collect"): (
        "collect_product", 1, 1,
        "商品链接/商品ID 采集 ⇒ 收费 collect_product（handler._route_product 入队时收）"),
    ("daren_bot/executors.py", "execute_image_vision"): (
        "vision", 2, 1,
        "截图识图 + 出商品结论 ⇒ 收费 vision（handler._route_image 入队时收）"),
    ("daren_bot/executors.py", "_vision_dashboard_qa"): (
        ACTION_BY_CALLER, 1, 1,
        "非商品截图的通用截图问答（罗盘/数据页，2026-10-28）：由调用方 "
        "execute_image_vision 计费 —— 同一次 image 任务内的识别降级分支，"
        "不另收费（一次 vision 收费覆盖「商品提取 或 截图问答」两条互斥分支）。"
        "★ 输出是 Markdown 自由文本，走 _vision_call 原文通道，不做 JSON 解析"),
    ("daren_bot/executors.py", "_vision_try"): (
        ACTION_TRANSPORT, 1, 1,
        "vision HTTP 调用的 JSON 字段解析薄壳（商品提取协议）：由上层漏斗计费 "
        "（_vision_extract_product → execute_image_vision=vision）"),
    ("daren_bot/executors.py", "execute_commission_recalc"): (
        "commission", 1, 1,
        "专属佣金重算（2026-10-28 老大拍板：公开佣金≠达人后台专属佣金）"
        "⇒ 收费 commission（handler._route_commission 入队时收，1 分）"),
    ("daren_bot/executors.py", "execute_qa"): (
        "qa", 1, 3,
        "问答 ⇒ 收费 qa。★ 一次收费最多对应 3 次 LLM 调用："
        "for attempt in (1,2,3) 重试（降级链失败时重试），"
        "口径是「按功能收费」不是「按调用收费」⇒ 倍数在此显式登记"),
    ("daren_bot/executors.py", "_llm_product_verdict"): (
        ACTION_BY_CALLER, 1, 1,
        "商品结论的 LLM 漏斗：由调用方计费 —— execute_product_collect=collect_product、"
        "execute_image_vision=vision"),
    ("daren_bot/executors.py", "_vision_extract_product"): (
        ACTION_BY_CALLER, 1, 1,
        "截图识商品的视觉漏斗：由调用方 execute_image_vision 计费（vision）"),
    ("daren_bot/executors.py", "_qa_llm"): (
        ACTION_TRANSPORT, 2, 4,
        "文字模型降级链的传输层：由上层漏斗计费。"
        "★ 一次调用最多打 4 条降级环（Code订阅/kimi/minimax×2）⇒ 最多 4 次 HTTP"),
    ("daren_bot/llm_channels.py", "call_ring"): (
        ACTION_TRANSPORT, 2, 1, "环传输层：由 _qa_llm / llm_text 的上层漏斗计费"),
    ("daren_bot/llm_channels.py", "call_ring_messages"): (
        ACTION_TRANSPORT, 2, 1, "环传输层（messages 协议）：由上层漏斗计费"),
    ("daren_bot/weekly.py", "_ai_tips"): (
        "weekly_report", 1, 1,
        "周报的 AI 经营建议 ⇒ 收费 weekly_report（weekly._enqueue_report 入队时收）"),
    ("daren_bot/tools/audit_verdict.py", "audit"): (
        ACTION_OPS, 1, 1,
        "运维审计工具（人工跑，复用生产结论）⇒ 烧平台自己的算力，不扣达人分"),
    ("daren_bot/tools/probe_llm_channels.py", "_probe_text_ring"): (
        ACTION_OPS, 2, 1,
        "运维探针（查哪条降级环还活着）⇒ 烧平台自己的算力，不扣达人分"),
}

# `db.deduct` 是**公开的裸扣费入口**（只扣费、不建任务）。
# A 边的守卫只查 `credits.charge` 不存在，没查它 ⇒ 这是一条后门。
# ⇒ 这里登记它**当前唯一合法**的调用点，且 cost 必须是 0（纯留痕）。
ALLOWED_DEDUCT = {
    ("daren_bot/credits.py", "enqueue_free"): (
        0,
        "零成本入口的留痕：记一条 0 分流水（cache_hit=1）让「这次没花算力」可对账。"
        "cost 恒为 0 ⇒ 不构成「扣了钱却没建任务」的两事务坑"),
}

# `db.refund` 是**公开的裸退款入口**（只退钱、不碰任务状态、不管幂等）。
# ★ 2026-10-25 B 边：退款已下沉到原子函数 `db.fail_task_and_refund()`，
#   业务代码里**不许再出现裸退款**（否则又变回「两个独立写」的老坑）。
ALLOWED_REFUND = {
    ("scripts/probe_credits_real_db.py", "main"): (
        "A 边的真库探针：探针自己扣的分，测完手工退回来"
        "（人工运维操作，探针跑完即退出，不在业务链路上）"),
}

# action → 允许的 task_type（收费动作与任务类型必须对得上）。
# ⚠️ 一个 task_type 可以对应多个 action（如 douyin：主动采集 / 周报自动采集 / 重采），
#    但**反过来**不成立：qa 任务不能收 collect_douyin 的价。
ACTION_TASK_TYPES = {
    "collect_douyin":        {"douyin"},
    "weekly_collect":        {"douyin"},
    "recollect":             {"douyin"},
    "collect_product":       {"product"},
    "collect_product_cached": {"product"},
    "vision":                {"image"},
    "commission":            {"commission"},
    "qa":                    {"qa"},
    "video":                 {"video"},
    "weekly_report":         {"weekly_report"},
}

# 闸门**范围之外**的消耗面（必须显式声明，防止「以为漏了」也防「悄悄扩大」）。
OUT_OF_SCOPE = {
    "solobrave-server.py": (
        "运营侧链路：AI 员工聊天（_call_ai_api）、驾驶舱的 AI 商品分析 / AI 达人分析"
        "（_call_ai_analysis）⇒ 烧平台自己的算力，**不扣达人分**（扣分对象是达人）"),
    "daren_bot/tools/": "运维探针与审计工具 ⇒ 烧平台自己的算力，不扣达人分",
}


# ─────────────────────────── ① 算力消耗点必须登记 ───────────────────────────

def test_compute_call_sites_are_registered():
    """★★ **从「算力消耗侧」反查**：每个真正调 LLM 的地方都必须在登记表里。

    ★ 为什么这条不能省（A 边守卫的结构性盲区）：
      A 边的守卫全部从「入队侧」看 ⇒ 只要新功能**直接调 LLM 而不入队**，
      它就不在任何守卫的视野里 ⇒ 绕过整道闸门而测试全绿。
      ⇒ 这条从另一侧兜住：**消耗点必须先登记，才谈得上收费**。
    """
    detected = _detect_compute_sites()
    registered = set(COMPUTE_SITES)

    unregistered = sorted(f"{f}::{fn}（{n} 处）"
                          for (f, fn), n in detected.items()
                          if (f, fn) not in registered)
    assert not unregistered, (
        "以下地方消耗算力但没登记在 COMPUTE_SITES 里：\n  "
        + "\n  ".join(unregistered)
        + "\n⇒ 新增消耗点必须先在 COMPUTE_SITES 写明：收哪个 action / 为什么不收 /"
          "一次收费最多对应几次调用 —— 写不清楚说明没想清楚，别让它白干。")

    stale = sorted(f"{f}::{fn}（{why[:40]}…）"
                   for (f, fn), (_a, _c, _m, why) in COMPUTE_SITES.items()
                   if (f, fn) not in detected)
    assert not stale, (
        "登记表里的消耗点已经不存在了（函数被改名/删除）⇒ 请删掉登记：\n  "
        + "\n  ".join(stale))


def test_compute_site_call_counts_match():
    """★ 登记的**静态调用点数**必须与代码一致 —— 新增一处消耗点要留痕。

    为什么：只登记「这个函数消耗算力」不够 —— 半年后有人在同一个函数里
    又加了一处 LLM 调用（更贵的那种），登记看着没变 ⇒ 口径悄悄漂了。
    ⇒ 数量对不上 ⇒ 逼你更新登记并写清理由。
    """
    detected = _detect_compute_sites()
    bad = []
    for key, (_action, calls, _max_calls, _why) in COMPUTE_SITES.items():
        actual = detected.get(key, 0)
        if actual != calls:
            bad.append(f"{key[0]}::{key[1]} —— 登记 {calls} 处，代码里 {actual} 处")
    assert not bad, (
        "以下消耗点的调用点数与登记不一致：\n  " + "\n  ".join(sorted(bad))
        + "\n⇒ 代码里增删了 LLM 调用点，请同步更新 COMPUTE_SITES 并写明理由")


def test_registered_compute_actions_are_priced():
    """★ 登记成「要收费」的消耗点，其 action 必须真在成本表里且价格为正。"""
    bad = []
    for (f, fn), (action, _c, _m, _why) in COMPUTE_SITES.items():
        if action in (ACTION_BY_CALLER, ACTION_TRANSPORT, ACTION_OPS):
            continue
        if action not in config.COST_TABLE:
            bad.append(f"{f}::{fn} —— action {action!r} 不在 config.COST_TABLE 里")
        elif config.COST_TABLE[action] <= 0:
            bad.append(f"{f}::{fn} —— action {action!r} 的价格不是正数")
    assert not bad, "以下消耗点的计费动作没定价：\n  " + "\n  ".join(sorted(bad))


def test_unpriced_compute_sites_explain_themselves():
    """★ 「不收费」的消耗点（BY_CALLER / TRANSPORT / OPS）必须写清理由。

    ★ 为什么不收费也要写理由：这是最容易藏白干的地方 ——
      「这层自己不定价，由上层收」听起来合理，但没写清上层是谁，
      就等于没人负责。写不出上层 ⇒ 就是白干。
    """
    bad = []
    for (f, fn), (action, _c, _m, why) in COMPUTE_SITES.items():
        if action in (ACTION_BY_CALLER, ACTION_TRANSPORT, ACTION_OPS):
            if not why.strip():
                bad.append(f"{f}::{fn} —— 标成 {action} 却没写理由")
    assert not bad, "以下不收费的消耗点没写理由：\n  " + "\n  ".join(sorted(bad))


def test_multi_call_sites_declare_their_multiplier():
    """★ 一次收费对应**多次**算力调用时，必须显式登记倍数。

    ★ 为什么锁这条：口径是「按功能收费」还是「按调用收费」？
      现在是前者（qa 收 1 分，但降级链 + 重试最多打 3 次 LLM）。
      这没问题 —— 但必须**写在纸上**，否则以后有人按「1 次调用 = 1 次收费」
      去算成本，会算错；或者有人加了一轮重试却没登记 ⇒ 成本悄悄翻倍。
    """
    bad = []
    for (f, fn), (_action, _c, max_calls, why) in COMPUTE_SITES.items():
        if max_calls is None or max_calls <= 1:
            continue
        if not why.strip():
            bad.append(f"{f}::{fn} —— 一次收费对应 {max_calls} 次调用，但没写明原因")
    assert not bad, ("以下消耗点的调用倍数没登记理由：\n  " + "\n  ".join(sorted(bad))
                     + "\n⇒ 在 COMPUTE_SITES 的理由里写清「为什么一次收费能打多次」")


# ─────────────────────────── ② 收费动作与任务类型要对得上 ───────────────────────────

def _charge_and_enqueue_calls():
    """扫所有 `credits.charge_and_enqueue(...)` 调用点 → (文件, 行, task_type, action)。"""
    out = []
    for path in _iter_python(BOT_DIR):
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _fn, calls in _enclosing_funcs(tree):
            for c in calls:
                f = c.func
                if not (isinstance(f, ast.Attribute) and f.attr == "charge_and_enqueue"):
                    continue
                if not (isinstance(f.value, ast.Name) and f.value.id == "credits"):
                    continue
                args = c.args
                if len(args) < 4:
                    out.append((rel, c.lineno, None, None))
                    continue
                tt = args[1].value if isinstance(args[1], ast.Constant) else ast.unparse(args[1])
                ac = args[3].value if isinstance(args[3], ast.Constant) else ast.unparse(args[3])
                out.append((rel, c.lineno, tt, ac))
    return out


def test_charge_action_matches_task_type():
    """★★ 收费动作必须与任务类型**对得上** —— 否则就是收错价。

    ★ 为什么这是独立缺口：`charge_and_enqueue(tenant, task_type, target, action)`
      里 `task_type` 和 `action` 是**两个独立参数**，没有任何东西保证它们一致。
      ⇒ 写错就是**按别的动作的价收费**：
        任务类型是 qa（该收 1 分）却传了 "collect_douyin"（100 分）⇒ 收 100 倍；
        任务类型是 product（该收 20 分）却传了 "qa"（1 分）⇒ **少收 19 分**。
      ⇒ A 边的守卫只查「action 在不在成本表里」，查不出「配错」。
    """
    bad = []
    scanned = 0
    for rel, lineno, tt, action in _charge_and_enqueue_calls():
        scanned += 1
        if tt is None or action is None:
            bad.append(f"{rel}:{lineno} —— 参数不足，判不了 task_type/action 配对")
            continue
        if action not in ACTION_TASK_TYPES:
            bad.append(f"{rel}:{lineno} —— action {action!r} 不在配对表里（先在 COST_TABLE 定价）")
            continue
        allowed = ACTION_TASK_TYPES[action]
        if tt not in allowed:
            bad.append(f"{rel}:{lineno} —— task_type={tt!r} 配 action={action!r} 不对"
                       f"（该 action 只允许 task_type ∈ {sorted(allowed)}）")
    assert not bad, ("以下收费动作与任务类型配错：\n  " + "\n  ".join(sorted(bad))
                     + "\n⇒ 收错价 = 少收（白干）或多收（被投诉），配错必须红")
    assert scanned > 0, "★ 一个收费调用点都没扫到 ⇒ 本测试已恒真（扣费入口改名了？）"


def test_every_cost_table_action_has_a_valid_task_type():
    """★ 成本表里每个 action 都得在配对表里登记 —— 否则配对表会漏。"""
    missing = sorted(a for a in config.COST_TABLE if a not in ACTION_TASK_TYPES)
    assert not missing, (
        f"成本表里有动作没登记 task_type 配对：{missing}\n"
        "⇒ 请在 ACTION_TASK_TYPES 里写明它属于哪种任务类型")


# ─────────────────────────── ③ 底层入口不许被绕过 ───────────────────────────

def test_direct_charged_enqueue_cost_must_equal_price():
    """★ 直接调 `db.enqueue_task_charged(...)` 时，cost 必须**等于成本表里的价**。

    ★ 为什么这是独立缺口：A 边的守卫查「action 在不在成本表里」，
      **不查「传进去的 cost 对不对」** ⇒
      `db.enqueue_task_charged(tk, "douyin", target, 1, "collect_douyin")`
      —— action 合法、cost 是 1 ⇒ **所有现有守卫全绿**，但收 1 分而不是 100 分。
      ⇒ 少收 99 分，且没有任何东西会报警。
    """
    bad = []
    scanned = 0
    for path in _iter_python(BOT_DIR):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "daren_bot/db.py":
            continue        # 定义处
        if rel == "daren_bot/credits.py":
            continue        # ★ 闸门自己的转发层：action 是变量（运行时查表）
                              #   它的价格一致性由 test_gate_forward_layer_reads_price_from_table 管
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for _fn, calls in _enclosing_funcs(tree):
            for c in calls:
                if _call_name(c) != "enqueue_task_charged":
                    continue
                if rel == "daren_bot/db.py":
                    continue        # 定义处
                scanned += 1
                if len(c.args) < 5:
                    bad.append(f"{rel}:{c.lineno} —— 参数不足 5 个，判不了价")
                    continue
                cost_arg, action = c.args[3], c.args[4]
                if not isinstance(action, ast.Constant):
                    bad.append(f"{rel}:{c.lineno} —— action 不是字面量，判不了价")
                    continue
                price = config.COST_TABLE.get(action.value)
                if price is None:
                    bad.append(f"{rel}:{c.lineno} —— action {action.value!r} 不在成本表里")
                    continue
                if isinstance(cost_arg, ast.Constant):
                    if cost_arg.value != price:
                        bad.append(f"{rel}:{c.lineno} —— cost={cost_arg.value} "
                                   f"≠ 成本表里 {action.value!r} 的价 {price}")
                elif isinstance(cost_arg, ast.Name):
                    # 变量：从表里查来的才算数（credits.py 的转发层）
                    if cost_arg.id == "cost":
                        continue
                    bad.append(f"{rel}:{c.lineno} —— cost 用的是变量 {cost_arg.id!r}，"
                               "闸门判不了它是不是表价")
                else:
                    bad.append(f"{rel}:{c.lineno} —— cost 表达式判不了")
    assert not bad, ("以下底层入队的金额与成本表不一致：\n  " + "\n  ".join(sorted(bad))
                     + "\n⇒ 少收 = 白干，多收 = 投诉。金额必须走成本表。")


def test_gate_forward_layer_reads_price_from_table():
    """★ 闸门的转发层必须**查表**拿价，不能写死数字。

    ★ 为什么：`credits.charge_and_enqueue` 是业务代码与原子入口之间唯一的桥。
      它若写死 `db.enqueue_task_charged(..., 100, action)` ⇒ 成本表改了价，
      **实际收的还是 100** ⇒ 成本表不再是唯一真源（而它自称是）。
    """
    src = (ROOT / "daren_bot" / "credits.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    bad = []
    found = 0
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name != "charge_and_enqueue":
            continue
        for n in ast.walk(func):
            if not isinstance(n, ast.Call) or _call_name(n) != "enqueue_task_charged":
                continue
            found += 1
            if len(n.args) < 4:
                bad.append("charge_and_enqueue 里调用 enqueue_task_charged 的参数不足")
                continue
            cost_arg = n.args[3]
            if isinstance(cost_arg, ast.Constant):
                bad.append(f"credits.py:{n.lineno} —— cost 写死成字面量 {cost_arg.value!r}"
                           "（成本表必须是真源）")
            elif isinstance(cost_arg, ast.Name) and cost_arg.id != "cost":
                bad.append(f"credits.py:{n.lineno} —— cost 用的不是查表得来的 cost 变量")
    assert found > 0, "★ 转发层里找不到 enqueue_task_charged 调用 ⇒ 闸门被改形了"
    assert not bad, "闸门转发层没有走成本表：\n  " + "\n  ".join(bad)


def test_deduct_is_only_used_as_zero_cost_tracing():
    """★★ `db.deduct` 是**公开的裸扣费入口**（只扣钱、不建任务）—— 必须封死。

    ★ 为什么 A 边的守卫漏了它：那边查的是 `credits.charge` **不存在**
      （断言 `not hasattr(credits, "charge")`）——
      但真正能扣钱的 `db.deduct` 一直**公开存在**，
      任何人写 `db.deduct(tk, 100, "collect_douyin")` 就绕过了整个闸门
      （钱扣了、任务没建 ⇒ 要么白扣，要么他自己去建任务 ⇒ 两事务坑复活）。
      ⇒ 现在只允许它出现在登记过的零成本留痕点上，且 cost 必须是 0。
    """
    bad = []
    scanned = 0
    for path in _iter_python(BOT_DIR):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "daren_bot/db.py":
            continue            # 定义处
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func_name, calls in _enclosing_funcs(tree):
            for c in calls:
                f = c.func
                is_db_deduct = (isinstance(f, ast.Attribute) and f.attr == "deduct"
                                and isinstance(f.value, ast.Name) and f.value.id == "db")
                is_bare = isinstance(f, ast.Name) and f.id == "deduct"
                if not (is_db_deduct or is_bare):
                    continue
                scanned += 1
                key = (rel, func_name)
                if key not in ALLOWED_DEDUCT:
                    bad.append(f"{rel}:{c.lineno} {func_name} —— 裸扣费入口 db.deduct "
                               "没登记（消耗算力请走 credits.charge_and_enqueue）")
                    continue
                allow_cost, _why = ALLOWED_DEDUCT[key]
                if len(c.args) < 2:
                    bad.append(f"{rel}:{c.lineno} —— 判不了 cost")
                    continue
                cost_arg = c.args[1]
                if isinstance(cost_arg, ast.Constant) and cost_arg.value != allow_cost:
                    bad.append(f"{rel}:{c.lineno} —— cost={cost_arg.value!r} ≠ 登记的 {allow_cost}")
    assert not bad, ("以下裸扣费调用不合规：\n  " + "\n  ".join(sorted(bad))
                     + "\n⇒ db.deduct 是「扣了钱却没建任务」的后门，只许零成本留痕用。")
    assert scanned > 0, "★ 一个 db.deduct 调用点都没扫到 ⇒ 本测试已恒真（入口改名了？）"


# ─────────────────────────── ④ 退款侧（本次审查的真缺口）───────────────────────────

@pytest.fixture()
def botdb(tmp_path, monkeypatch):
    """最小真库：只建表 + 一个有余额的账号（不碰生产数据）。"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "IMAGE_DIR", tmp_path / "data" / "images")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "data" / "bot.db")
    db.init_db()
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.execute("INSERT INTO bot_credits_accounts (tenant_key,balance,updated_at) "
                 "VALUES ('tk_x',1000,1)")
    conn.commit()
    conn.close()
    return config.DB_PATH


class _Sender:
    def __init__(self):
        self.texts = []

    def send_text(self, chat_id, text):
        self.texts.append(text)


class _WM:
    def __init__(self):
        self.sender = _Sender()

    def get_sender(self, tk):
        return self.sender


def test_failed_task_refunds_exactly_once(botdb):
    """★★ 同一个任务失败两次 ⇒ **只退一次钱**（退款必须幂等）。

    ★★ 为什么这是本次审查抓到的**真缺口**（2026-10-25 B 边）：
      原来的 `worker._fail` 是「先 mark_task(failed) 再 refund」两个独立写，
      **没有幂等保护** ⇒ 同一个 task 被 `_fail` 两次就**退两次钱**。
      可达路径：worker 重启时 `reclaim_stale_collecting()` 把 `collecting`
      打回 `pending`（**无时间门槛**，正在执行的任务也会被回收）⇒ 重跑又失败
      ⇒ 双退；而流水表**没有** `ref_task_id` 唯一约束 ⇒ DB 层拦不住
      ⇒ **白送积分**（比多扣更糟：那是直接造钱）。
      ⇒ 现在按**流水**判重（退款发生没发生），并把顺序改成「先退款后标状态」
      （这样崩溃窗口的代价是「多跑一次算力」，而不是「白扣达人的分」）。
    """
    from daren_bot.worker import Worker
    st, task_id, bal = db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    assert st == "ok", st
    after_charge = bal

    worker = Worker(_WM())
    task = db.get_task(task_id)
    worker._fail(task, "第一次失败")
    after_first = db.get_balance("tk_x")
    assert after_first == after_charge + config.COST_COLLECT_DOUYIN, "第一次失败应全额退还"

    # ★ 再失败一次（重跑 / 重复调用 _fail）⇒ 绝不能再退
    worker._fail(task, "第二次失败")
    after_second = db.get_balance("tk_x")
    assert after_second == after_first, (
        f"重复失败退了两笔钱：{after_first} → {after_second}（白送积分）")

    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger "
                     "WHERE action='refund' AND ref_task_id=?", (task_id,)).fetchone()[0]
    conn.close()
    assert n == 1, f"同一任务应只有 1 条退款流水，实际 {n} 条"


def test_refund_amount_equals_charged_amount(botdb):
    """★ 退款额必须**等于**该任务的实扣额（不能多退也不能少退）。"""
    from daren_bot.worker import Worker
    st, task_id, _bal = db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    assert st == "ok"
    before = db.get_balance("tk_x")
    Worker(_WM())._fail(db.get_task(task_id), "炸了")
    after = db.get_balance("tk_x")
    assert before + config.COST_COLLECT_DOUYIN == after, \
        f"退款额不对：扣了 {config.COST_COLLECT_DOUYIN} 却退回来 {after - before}"


def test_zero_cost_task_is_not_refunded(botdb):
    """★ cost=0 的任务（缓存命中 / 管理员后门）失败时**不许退款** —— 否则白送钱。"""
    from daren_bot.worker import Worker
    conn = sqlite3.connect(str(botdb))
    conn.execute("INSERT INTO bot_collect_tasks "
                 "(id,tenant_key,type,target,chat_id,status,cache_hit,credits_cost,"
                 "payload,created_at) VALUES ('t_zero','tk_x','qa','q','oc_x','pending',1,0,'',1)")
    conn.commit()
    conn.close()
    before = db.get_balance("tk_x")
    Worker(_WM())._fail(db.get_task("t_zero"), "炸了")
    assert db.get_balance("tk_x") == before, "零成本任务失败却退款了（白送积分）"


def test_refund_happens_even_if_task_row_missing(botdb):
    """★★ 任务行**查不到**时 ⇒ **照样退款**（兜底用调用方给的值）。

    ★ 为什么这条必须锁死：**钱的事上「拿不准就不退」是错的** ——
      静默跳过退款 = 达人的分被吞（客诉 + 信任损失）。
      宁可按调用方给的值退（最多退多，但有流水可查、幂等判重仍生效），
      也不能因为查不到行就一分钱不退。
    ★ 这条也是 A 边 `test_failure_text.py::test_fail_refunds_still_happens`
      用「不存在于库里的假任务」能验到退款的前提。
    """
    refunded, bal = db.fail_task_and_refund(
        "t_not_in_db", "炸了", tenant_key="tk_x", cost=config.COST_QA)
    assert refunded is True, "任务行不存在就不退款了（达人的分被吞）"
    assert bal == 1000 + config.COST_QA, f"退款额不对：{bal}"
    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger "
                     "WHERE action='refund' AND ref_task_id='t_not_in_db'").fetchone()[0]
    conn.close()
    assert n == 1, "退款流水应记 1 条"


def test_fail_task_and_refund_is_idempotent(botdb):
    """★★ 原子退款函数：同一任务调两次 ⇒ **只退一次**。

    ★ 这是 2026-10-25 B 边把退款下沉到 `db.fail_task_and_refund()` 的核心保证：
      「查幂等（流水里退过没有）→ 标记 → 退款」全在一个事务里。
    """
    _st, task_id, after_charge = db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    refunded1, bal1 = db.fail_task_and_refund(task_id, "炸了")
    assert refunded1 is True, "第一次应真的退款"
    assert bal1 == after_charge + config.COST_COLLECT_DOUYIN

    refunded2, bal2 = db.fail_task_and_refund(task_id, "又炸了")
    assert refunded2 is False, "第二次应命中幂等（不再退）"
    assert bal2 == bal1, f"重复退款：{bal1} → {bal2}（白送积分）"

    conn = sqlite3.connect(str(botdb))
    n = conn.execute("SELECT COUNT(*) FROM bot_credits_ledger "
                     "WHERE action='refund' AND ref_task_id=?", (task_id,)).fetchone()[0]
    conn.close()
    assert n == 1, f"同一任务应只有 1 条退款流水，实际 {n} 条"


def test_fail_task_and_refund_marks_task_failed(botdb):
    """★ 原子退款必须**同时**把任务标成 failed（一个事务，不能只退钱不标记）。"""
    _st, task_id, _bal = db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    db.fail_task_and_refund(task_id, "采集失败")
    row = db.get_task(task_id)
    assert row["status"] == "failed", f"任务状态应为 failed，实际 {row['status']!r}"
    assert row["error"] == "采集失败"


def test_reclaim_stale_collecting_respects_time_threshold(botdb):
    """★★ 僵死回收必须**有时间门槛**：刚被领走的任务不许被抢回重跑。

    ★ 为什么（2026-10-25 B 边对抗审查）：
      原来的回收是「把所有 collecting 打回 pending」—— **不区分是不是真卡死** ⇒
      worker 重启的重叠窗口里，另一个 worker 正在执行的任务会被抢回重跑 ⇒
        ① 算力跑两遍（平台成本双份）
        ② 两个 worker 都失败时 ⇒ 退款两次（白送积分）
      ⇒ 现在只回收「领走超过 STALE_COLLECTING_SECONDS 秒」的。
    """
    _st, task_id, _bal = db.enqueue_task_charged(
        "tk_x", "douyin", "dy_x", config.COST_COLLECT_DOUYIN, "collect_douyin")
    claimed = db.claim_next_task()
    assert claimed and claimed["id"] == task_id, "任务没被领走"
    assert db.get_task(task_id)["status"] == "collecting"

    # 刚领走的 ⇒ **不许**被回收
    n = db.reclaim_stale_collecting()
    assert n == 0, f"刚领走的任务被回收了（{n} 条）⇒ 正在执行的任务会被重跑"
    assert db.get_task(task_id)["status"] == "collecting", "刚领走的任务被打回 pending 了"

    # 领走很久了（模拟僵死）⇒ 该回收
    conn = sqlite3.connect(str(botdb))
    conn.execute("UPDATE bot_collect_tasks SET claimed_at=? WHERE id=?",
                 (db.now_ts() - db.STALE_COLLECTING_SECONDS - 60, task_id))
    conn.commit()
    conn.close()
    n = db.reclaim_stale_collecting()
    assert n == 1, f"僵死任务没被回收（{n} 条）"
    assert db.get_task(task_id)["status"] == "pending", "僵死任务应打回 pending"


def test_refund_entry_is_not_used_in_business_code():
    """★★ 业务代码里**不许**出现裸退款 `db.refund(...)`。

    ★ 为什么：退款已下沉到原子函数 `db.fail_task_and_refund()`
      （查幂等 + 标记 + 退款，一个事务）。
      业务代码再直接调 `db.refund()` ⇒ 绕开幂等与状态标记 ⇒
      「退两次钱」「标记没改」两个老坑全部复活。
      ⇒ 只允许**登记过的运维脚本**用（人工探针，跑完即退）。
    """
    bad = []
    scanned = 0
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", "docs/")) or "__pycache__" in path.parts:
            continue
        if rel == "daren_bot/db.py":
            continue            # 定义处
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for func_name, calls in _enclosing_funcs(tree):
            for c in calls:
                f = c.func
                is_db_refund = (isinstance(f, ast.Attribute) and f.attr == "refund"
                                and isinstance(f.value, ast.Name) and f.value.id == "db")
                is_bare = isinstance(f, ast.Name) and f.id == "refund"
                if not (is_db_refund or is_bare):
                    continue
                scanned += 1
                if (rel, func_name) not in ALLOWED_REFUND:
                    bad.append(f"{rel}:{c.lineno} {func_name} —— 裸退款入口 db.refund "
                               "没登记（退款请走 db.fail_task_and_refund()）")
    assert not bad, ("以下裸退款调用不合规：\n  " + "\n  ".join(sorted(bad))
                     + "\n⇒ 退款必须走原子函数 db.fail_task_and_refund()"
                       "（幂等 + 标记 + 退款一个事务）。")
    assert scanned > 0, "★ 一个 db.refund 调用点都没扫到 ⇒ 本测试已恒真（入口改名了？）"


# ─────────────────────────── ⑤ 闸门范围要显式声明 ───────────────────────────

def _is_test_like(rel: str) -> bool:
    """测试/文档脚本不算「消耗面」—— 它们烧的是开发者自己的算力。

    ★ 排除项写死在这儿（而不是默认排除），是为了让「什么没被扫」**可见**：
      加新的排除项 ⇒ 必须同时更新 OUT_OF_SCOPE 或本函数，让审查者看得见。
    """
    if rel.startswith(("tests/", "scripts/", "docs/", "tools/")):
        return True
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


def test_out_of_scope_compute_is_declared():
    """★ 闸门**范围之外**的消耗面必须显式登记 —— 防止「以为漏了」和「悄悄扩大」。

    ★ 为什么要写这条：主服务端 `solobrave-server.py` 有自己的 LLM 链路
      （AI 员工聊天、驾驶舱的 AI 商品分析 / AI 达人分析）——
      那是**运营侧**的算力，不扣达人分（扣分对象是达人）。
      不写清楚 ⇒ 以后每个来审查的人都要重新怀疑一遍；
      写清楚 ⇒ 「这是有意为之，改口径要改这张表」。
    """
    found = {}
    for path in _iter_python(ROOT):
        rel = path.relative_to(ROOT).as_posix()
        if _is_test_like(rel):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for _fn, calls in _enclosing_funcs(tree):
            for c in calls:
                if _call_name(c) in SERVER_COMPUTE_NAMES:
                    found.setdefault(rel, 0)
                    found[rel] += 1
    undeclared = sorted(f for f in found if not any(f.startswith(p) for p in OUT_OF_SCOPE))
    assert not undeclared, (
        "以下文件在消耗算力，但没登记在 OUT_OF_SCOPE 里：\n  "
        + "\n  ".join(f"{f}（{found[f]} 处）" for f in undeclared)
        + "\n⇒ 要么它该走达人闸门（那就把它挪进 daren_bot 并接 charge_and_enqueue），"
          "要么它是平台侧算力 —— 那就写进 OUT_OF_SCOPE 说明理由。")


def test_out_of_scope_declarations_are_real():
    """★ 登记的范围声明必须**指向真实存在的消耗面** —— 不许挂空。"""
    missing = []
    for prefix, why in OUT_OF_SCOPE.items():
        if prefix.endswith("/"):
            hit = any(p.relative_to(ROOT).as_posix().startswith(prefix)
                      for p in _iter_python(ROOT))
        else:
            hit = (ROOT / prefix).exists()
        if not hit:
            missing.append(f"{prefix}（{why[:40]}…）")
    assert not missing, f"OUT_OF_SCOPE 里指向不存在的路径：{missing}"
