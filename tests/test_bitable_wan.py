"""万单位口径一致性测试（2026-10-22）。

飞书指标卡取整（实测：一律取整、precision 不生效）逼出两个并行口径：
  - 飞书 Base 的 number 副本字段「粉丝·卡」/「累计GMV·卡」
    → 由 `daren_bot.fmt.wan_value()` 写入，值是 **1.3 / 37.5**（1 位小数）
  - H5 驾驶舱 / 周报 / 长图
    → 由 `daren_bot.fmt.fmt_wan()` 渲染，文本是 **"1.3万" / "37.5万"**

两者是**两份手写实现**，必须给出同一个数 —— 否则飞书卡显示 1.3 而 H5 显示 1.2，
达人又看到「两个系统」（坑 20/26）。

本测试锁住：
  ① `wan_value` 与 `fmt_wan` 的**分支和舍入**逐位一致（含 <1万 的原值分支）；
  ② 飞书卡上那个数 == H5 文本里那个数；
  ③ `bitable_sync._wan` 只是薄壳（AST 静态断言，避开 crypto_util 重依赖）。
"""
import ast
import os

import pytest

from daren_bot.fmt import fmt_wan, wan_value

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BITABLE_SYNC = os.path.join(ROOT, "daren_bot", "bitable_sync.py")

# (原始值, 期望的飞书卡数值, 期望的 H5 文本)
CASES = [
    (12804, 1.3, "1.3万"),       # 洁洁爱户外
    (375000, 37.5, "37.5万"),    # 累计GMV 中值
    (5251, 5251, "5,251"),       # <1万 → 两边都用原值（不丢精度）
    (9999, 9999, "9,999"),
    (10000, 1.0, "1万"),
    (10049, 1.0, "1万"),
    (10050, 1.0, "1万"),
    (100000, 10.0, "10万"),
    (20000, 2.0, "2万"),
    (0, 0, "0"),
]


def _text_to_number(text: str) -> float:
    """把 H5 文本还原成数字：「1.3万」→ 1.3、「5,251」→ 5251、「2万」→ 2。"""
    t = text.replace(",", "").replace("万", "").strip()
    return float(t)


@pytest.mark.parametrize("raw,expect_card,expect_text", CASES)
def test_wan_value_matches_h5_text(raw, expect_card, expect_text):
    assert fmt_wan(raw) == expect_text
    assert wan_value(raw) == pytest.approx(expect_card)


@pytest.mark.parametrize("raw,expect_card,expect_text", CASES)
def test_card_number_equals_h5_number(raw, expect_card, expect_text):
    """★ 核心断言：飞书卡上的数 == H5 文本里的数（同一数字同源）。"""
    assert wan_value(raw) == pytest.approx(_text_to_number(expect_text))


def test_wan_value_accepts_numeric_string():
    """采集链里粉丝/GMV 可能是字符串，不能再炸一次。"""
    assert wan_value("12804") == pytest.approx(1.3)
    assert wan_value(12804) == pytest.approx(wan_value("12804"))


def test_wan_value_none_and_garbage():
    assert wan_value(None) is None
    assert wan_value("") is None
    assert wan_value("abc") is None


def test_rounding_is_away_from_zero_not_bankers():
    """★ 回归：内置 round() 是银行家舍入，12500→1.2，会与 H5 的 1.3 打架。"""
    assert wan_value(12500) == pytest.approx(1.3)
    assert wan_value(17500) == pytest.approx(1.8)
    assert fmt_wan(12500) == "1.3万"
    assert fmt_wan(17500) == "1.8万"


def test_gmv_card_number_matches_text_midpoint():
    """累计GMV 走「档位文本中值」→ 37.5；飞书卡也必须是 37.5。"""
    from daren_bot.fmt import gmv_mid_from_text
    mid = gmv_mid_from_text("¥25万-50万")
    assert mid == 375000.0
    assert wan_value(mid) == pytest.approx(37.5)
    assert fmt_wan(mid) == "37.5万"


def test_bitable_shell_is_thin():
    """bitable_sync._wan 必须只是薄壳（转发到 fmt.wan_value），不能自己再实现一遍。

    ⚠️ 为什么不用 import 测：`bitable_sync` import `crypto_util` → 需要
    pycryptodome → 系统 python 下 import 就炸（ModuleNotFoundError: 'Crypto'），
    依赖它的工具会静默跳过（异常被 except 吞掉）→ 口径悄悄丢失。
    ⇒ 用 AST 静态断言「转发关系」，既避开重依赖，又守住「口径只有一份」。
    """
    tree = ast.parse(open(BITABLE_SYNC, encoding="utf-8").read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_wan"), None)
    assert fn is not None, "bitable_sync._wan 不存在"
    called = {c.func.id for c in ast.walk(fn)
              if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
    assert "wan_value" in called, (
        f"bitable_sync._wan 必须转发 fmt.wan_value，实测调用了 {sorted(called)}")
    assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "round" for n in ast.walk(fn)), (
        "bitable_sync._wan 不许用内置 round()（银行家舍入，与 H5 差 0.1）")
