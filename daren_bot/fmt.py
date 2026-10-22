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
