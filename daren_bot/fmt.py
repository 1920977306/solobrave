"""数字格式化（2026-10-22）。

老大定调：「1.3万这样好看点」—— 飞书 App 指标卡已经用「万」单位了，
H5 / 飞书长图 / 周报文字要**同源同规则**，否则同一个数字在三个地方长得不一样，
达人会以为是两个数。

## 规则（唯一事实来源，H5 侧同名函数 `fmtFans` 必须与本文件逐位一致）

| 输入 | 输出 | 说明 |
|---|---|---|
| None / 非法 | `—` | 不编造 |
| 5,251 | `5,251` | < 1 万保持千分位原值（写「0.5万」没人看得懂） |
| 12,804 | `1.3万` | ≥ 1 万转「万」，1 位小数 |
| 375,000 | `37.5万` | 整数则不带小数：`2万` |
| 20,000 | `2万` | |

⚠️ 舍入用 `floor(x/10000*10 + 0.5)/10`（远离零的四舍五入），
**不是** Python 内置 `round()`（银行家舍入，round(2.675, 2)=2.67）。
JS 侧用完全相同的算式 → 两边逐位一致（`tests/test_fmt_consistency.py` 锁住）。
"""


def fmt_wan(v, none: str = "—") -> str:
    """按「万」单位格式化数量。Python 侧唯一入口，H5 侧对应 `fmtFans`。"""
    if v is None:
        return none
    try:
        x = float(v)
    except (TypeError, ValueError):
        return none
    if x != x or x in (float("inf"), float("-inf")):   # NaN / inf
        return none
    if abs(x) < 10000:
        return f"{int(x + 0.5) if x >= 0 else -int(-x + 0.5):,}"
    w = _round1(x / 10000)
    if w == int(w):
        return f"{int(w)}万"
    return f"{w:.1f}万"


def _round1(x: float) -> float:
    """1 位小数四舍五入（远离零），与 JS `Math.floor(x*10+0.5)/10` 同算式。"""
    import math
    if x >= 0:
        return math.floor(x * 10 + 0.5) / 10
    return -math.floor(-x * 10 + 0.5) / 10


# ---------- 平台档位文本 → 中值（2026-10-22） ----------
#
# 抖音给的 GMV 常常**不是精确数字**，而是档位文本「¥25万-50万」。
# 飞书 Base「累计GMV(万)」= 375000 = (25+50)/2*10000 —— 这个口径必须**只有一处**，
# 否则「卡片显示 37.5万 / 摘要写 ¥10万-25万」这种同页打架会再来一次。
#
# ⚠️ 原来这份解析在 `bitable_sync._gmv_from_text`，但那个模块 import 了
#   `crypto_util` → 需要 pycryptodome → **系统 python 下 import 就炸**
#   （实测 ModuleNotFoundError: No module named 'Crypto'），
#   依赖它的工具会静默跳过补值（异常被 except 吞掉）。
#   ⇒ 解析搬到这里（轻量、无重依赖），bitable_sync 改为引用本函数。
import re as _re

# ⚠️ 正则与单位判定**逐字照搬** bitable_sync 的原实现（口径必须只有一处），
#    差别会在「卡片 37.5万 / 摘要 ¥10万-25万」这种同页打架里显形。
_GMVTEXT_RE = _re.compile(r"(\d+(?:\.\d+)?)\s*[万kK]?\s*[-~—～]\s*[¥￥]?\s*(\d+(?:\.\d+)?)\s*([万kK]?)")


def _to_float_plain(v):
    try:
        return float(str(v).replace(",", "").replace("万", "")
                     .replace("¥", "").strip())
    except Exception:  # noqa: BLE001
        return None


def wan_value(v):
    """原始数值 → 飞书卡用的「万单位数值」，与 `fmt_wan` **同一套分支**。

    飞书指标卡实测**一律取整且不认字段 style**，所以万单位的小数只能落在
    **number 字段**上（列表/表格按字段自身格式显示 1.3 / 37.5），
    指标卡另在 UI「自定义配置 → 小数精度」设 1 位小数。

    ★ 分支与 `fmt_wan` **逐条一致**（这是「同一数字同源」的硬要求）：
      | 原始值 | `fmt_wan`（H5/周报/长图） | 本函数（飞书 number 字段） |
      |---|---|---|
      | 12,804 | `1.3万` | `1.3` |
      | 375,000 | `37.5万` | `37.5` |
      | 5,251 | `5,251`（<1万 用原值） | `5251`（**同分支**） |

      ⚠️ 若这里无条件除以 10000，粉丝 <1万 的达人会变成「飞书 0.5 / H5 5,251」
      —— 同一数字两种写法，又一次「两个系统」。
      （万单位在 <1万 时会丢精度：0.5万≈5000 ≠ 5251，
        H5 用原值正是为了不丢这个精度。）

    ⚠️ 舍入**必须用 `_round1`（远离零）**，与 `fmt_wan` / H5 的
    `Math.floor(x*10+0.5)/10` 逐位一致。
    **不要用 Python 内置 `round()`** —— 那是银行家舍入
    （`round(1.25, 1) == 1.2`），与 H5 的 `1.3` 不一致。
    """
    f = _to_float_plain(v)
    if f is None:
        return None
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return None
    if abs(f) < 10000:                 # 与 fmt_wan 同一分支：<1万 用原值
        return f
    return _round1(f / 10000)


def gmv_mid_from_text(text):
    """平台档位文本「¥25万-50万」→ 中值 375000.0。取不到返回 None。

    口径与飞书 Base「累计GMV(万)」公式字段完全一致（(lo+hi)/2 × 单位），
    与 `bitable_sync._gmv_value` 同一份实现（那边改为引用本函数）。
    """
    if not text:
        return None
    m = _GMVTEXT_RE.search(str(text))
    if not m:
        return _to_float_plain(text)
    lo = float(m.group(1))
    hi = float(m.group(2))
    unit = m.group(3) or ("万" if "万" in str(text) else "")
    mult = 10000 if unit in ("万",) else (1000 if unit in ("k", "K") else 1)
    return (lo + hi) / 2 * mult


