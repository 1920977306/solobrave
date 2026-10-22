"""结论视觉映射的跨语言一致性测试（2026-10-22）。

老大反馈「两个设计风格没有一样，不然会以为是两个系统」。查证后确认：
H5 驾驶舱与飞书 BaseApp 展示的是**同一个达人（hlb725）的同一套数据**，
但两边曾各写各的：

| 同一个词 | H5 原来 | 飞书原来 |
|---|---|---|
| 潜力型 | 绿底（且**所有**结论一律绿底） | **紫色**标签 |
| 🟢 能带 | 绿底 | **红色**标签（emoji 与颜色反了） |
| 🔴 别带 | 红底 | **橙色**标签 |
| 待评估 | 琥珀 | **蓝色** |

同一个词两个颜色 → 用户一眼看出是两个系统。

★ 本测试的真正作用：**锁住 H5 的 JS 和 Python 不漂移**。
唯一事实来源 = `daren_bot/verdict_ui.py`；H5 的 `VERDICT_EMOJI` / `verdictKey()`
是它的 **JS 孪生** —— 两份手写实现，靠人记着同步早晚会忘，这里用 node 实跑对拍。

（涉及飞书实际 hue / 文本的检查在 `daren_bot/tools/verify_visual_sync.py`，
 那需要 lark-cli 登录态，不进单测。）
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

from daren_bot import verdict_ui

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, "daren_cockpit", "index.html")


def _html() -> str:
    return open(INDEX_HTML, encoding="utf-8").read()


def _extract_js_emoji_map() -> dict:
    """从 index.html 抠出 VERDICT_EMOJI 的 JS 字面量映射。"""
    m = re.search(r"const\s+VERDICT_EMOJI\s*=\s*\{(.*?)\}\s*;", _html(), re.S)
    assert m, "index.html 里找不到 VERDICT_EMOJI —— 是不是被改名/删了？"
    return dict(re.findall(r"'([^']+)'\s*:\s*'([^']+)'", m.group(1)))


def _extract_js_verdict_key() -> str:
    """抠出 verdictKey 函数体。"""
    m = re.search(r"function verdictKey\(v\)\{.*?\n\}", _html(), re.S)
    assert m, "index.html 里找不到 verdictKey 函数 —— 是不是被改名/删了？"
    return m.group(0)


class TestEmojiSync:
    """H5 的 emoji 映射必须和 Python 侧一致。"""

    def test_js_map_matches_python(self):
        js = _extract_js_emoji_map()
        for verdict, item in verdict_ui.VERDICT_UI.items():
            assert verdict in js, f"H5 的 VERDICT_EMOJI 里没有「{verdict}」"
            assert js[verdict] == item["emoji"], (
                f"结论「{verdict}」的 emoji 两边不一致: "
                f"H5={js[verdict]!r} Python={item['emoji']!r}"
            )

    def test_js_map_has_no_extra_verdicts(self):
        js = _extract_js_emoji_map()
        extra = set(js) - set(verdict_ui.VERDICT_UI)
        assert not extra, f"H5 多了未登记的结论: {sorted(extra)}"


class TestCssClasses:
    """每个结论都要有对应的 .v-* 色类，否则徽章掉底色。"""

    def test_all_verdicts_have_css_class(self):
        html = _html()
        missing = [v for v in verdict_ui.VERDICT_UI if f".v-{v}{{" not in html]
        assert not missing, f"index.html 缺 .v-* 色类: {missing}"


class TestVerdictKey:
    """飞书选项名可能带 emoji 前缀，归一化必须两边一致。"""

    def test_python_strips_emoji_prefix(self):
        assert verdict_ui.verdict_key("🟢 能带") == "能带"
        assert verdict_ui.verdict_key("🟡 慎带") == "慎带"
        assert verdict_ui.verdict_key("🔴 别带") == "别带"
        assert verdict_ui.verdict_key("潜力型") == "潜力型"
        assert verdict_ui.verdict_key("") == ""

    def test_feishu_option_normalizes_to_its_key(self):
        """映射表自身的自洽性：选项名归一化后必须回到 key。"""
        for key, item in verdict_ui.VERDICT_UI.items():
            got = verdict_ui.verdict_key(item["feishu_option"])
            assert got == key, (
                f"飞书选项「{item['feishu_option']}」归一化成 {got!r}，"
                f"但映射表的 key 是 {key!r}"
            )

    @pytest.mark.skipif(shutil.which("node") is None, reason="没装 node，跳过 JS 对拍")
    def test_h5_js_matches_python(self):
        js = _extract_js_verdict_key()
        cases = [item["feishu_option"] for item in verdict_ui.VERDICT_UI.values()]
        cases += ["潜力型", "能带", "别带", "", None, "🟢 能带 额外空格"]
        script = f"""
{js}
const cases = {json.dumps(cases)};
console.log(JSON.stringify(cases.map(v => verdictKey(v))));
"""
        tmp = tempfile.mkdtemp(prefix="vkey_js_")
        path = os.path.join(tmp, "vkey.js")
        with open(path, "w", encoding="utf-8") as f:
            f.write(script)
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"node 跑失败: {proc.stderr[:400]}"
        js_out = json.loads(proc.stdout.strip())

        py_out = [verdict_ui.verdict_key(v) for v in cases]
        for v, j, p in zip(cases, js_out, py_out):
            assert j == p, f"同一个输入({v!r})归一化不一致: JS={j!r} Python={p!r}"


class TestVerdictLine:
    """一句话结论的兜底行为（不造没定义的结论）。"""

    def test_known_verdict(self):
        assert verdict_ui.verdict_line("潜力型") == "涨粉健康、带货稳，优先接高佣标品"

    def test_undefined_verdict_falls_back(self):
        """没定义结论句的评级 → 走兜底，**不编句子**。"""
        assert verdict_ui.verdict_line("成长型") == verdict_ui.NO_LINE_FALLBACK
        assert verdict_ui.verdict_line("不存在的结论") == verdict_ui.NO_LINE_FALLBACK
        assert verdict_ui.verdict_line("") == verdict_ui.NO_LINE_FALLBACK
