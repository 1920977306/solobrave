"""数字格式化跨语言一致性测试（2026-10-22）。

老大定调「1.3万这样好看点」后，粉丝量在**四个地方**展示：
  1. 飞书 App 指标卡（公式字段 `ROUND([粉丝]/10000, 1)`）
  2. H5 驾驶舱（`index.html` 的 `fmtFans()`）
  3. 飞书长图看板（`daren_bot/dashboard_card.py` 的 `fmt_wan()`）
  4. 周报文字（`daren_bot/weekly.py` 的 `fmt_wan()`）

四个地方必须给出**同一个字符串** —— 同一个数字长得不一样，达人会以为是两个数
（这正是坑 14「口径文案要与 H5 同源」的数字版）。

★ 本测试的真正作用：**锁住 H5 的 JS 和 Python 不漂移**。
  两边是两份手写实现，靠人记着同步早晚会忘 —— 这里用 node 实跑 JS 跟 Python 对拍。
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from daren_bot.fmt import fmt_wan

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, "daren_cockpit", "index.html")

# 覆盖边界：0 / 小于万 / 临界点 / 万位 / 百万 / 非整数 / 负数 / 非法
CASES = [0, 1, 999, 1000, 5251, 9999, 9999.4, 10000, 10049, 10050,
         10499, 10500, 12804, 99999, 100000, 375000, 999999, 1000000,
         1234567, 5251.6, -12804, None, "abc"]


def _extract_fmt_fans() -> str:
    """从 index.html 里抠出 fmtFans 函数体（连同它依赖的 Math 等内置）。"""
    src = open(INDEX_HTML, encoding="utf-8").read()
    m = re.search(r"function fmtFans\(v\)\{.*?\n\}", src, re.S)
    assert m, "index.html 里找不到 fmtFans 函数 —— 是不是被改名/删了？"
    return m.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="没装 node，跳过 JS 对拍")
def test_h5_js_matches_python():
    """H5 的 fmtFans 与 Python 的 fmt_wan 必须逐位一致。"""
    js = _extract_fmt_fans()
    script = f"""
{js}
const cases = {json.dumps(CASES)};
const out = cases.map(v => fmtFans(v));
console.log(JSON.stringify(out));
"""
    tmp = tempfile.mkdtemp(prefix="fmt_js_")
    path = os.path.join(tmp, "fmt.js")
    with open(path, "w", encoding="utf-8") as f:
        f.write(script)
    proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, f"node 跑失败: {proc.stderr[:400]}"
    js_out = json.loads(proc.stdout.strip())

    py_out = [fmt_wan(v) for v in CASES]
    assert len(js_out) == len(py_out)
    for v, j, p in zip(CASES, js_out, py_out):
        assert j == p, f"同一个数({v!r})两边不一致: JS={j!r} Python={p!r}"


class TestFmtWanRule:
    """规则本身（老大定调：1.3万 好看）。"""

    def test_below_ten_thousand_keeps_thousands_separator(self):
        assert fmt_wan(5251) == "5,251"
        assert fmt_wan(999) == "999"
        assert fmt_wan(0) == "0"

    def test_ten_thousand_and_above_uses_wan(self):
        assert fmt_wan(12804) == "1.3万"
        assert fmt_wan(375000) == "37.5万"

    def test_integer_wan_has_no_decimal(self):
        assert fmt_wan(20000) == "2万"
        assert fmt_wan(10000) == "1万"

    def test_none_and_invalid(self):
        assert fmt_wan(None) == "—"
        assert fmt_wan("abc") == "—"

    def test_rounding_is_half_away_from_zero_not_bankers(self):
        """⚠️ 不能用 Python 内置 round()（银行家舍入：round(2.675,2)=2.67）。
        这里锁死「远离零」—— 跟 JS 的 Math.floor(x*10+0.5)/10 同算式。"""
        assert fmt_wan(12500) == "1.3万"     # 1.25 → 1.3
        assert fmt_wan(13500) == "1.4万"     # 1.35 → 1.4
