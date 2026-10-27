# -*- coding: utf-8 -*-
"""tests/test_probe_feishu_reply_mode.py —— 钉住「飞书回复形态由发送端 API 语义决定」。

背景（2026-10-27）
------------------
老大问：「为什么木木可以已读，回复是卡片形式，跟之前 kimi 弄的不一样？」

结论由**代码事实**支撑，不靠 UI 观察：
- openclaw 飞书插件出站 ``client.im.message.reply(...)`` ⇒ 回复语义 ⇒ 飞书标记「已读」
- daren_bot 出站 ``.im.v1.message.create(...)`` ⇒ 新建语义 ⇒ 直接回复、无「已读」

本测试的作用是「防结论漂移」：若哪天有人把 daren_bot 改成 reply，
或把 openclaw 插件换成不提供 reply 的版本，探针会红，结论作废 ⇒ 必须重新取证。

判据纪律：每条断言都要能红；负向对照必须同时断言**退出码**与**错误文案**。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "probe_feishu_reply_mode.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("probe_feishu_reply_mode", SCRIPT)
    assert spec and spec.loader, "无法加载探针模块：%s" % SCRIPT
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = _load_module()


# ---------- 探针本体 ----------

def test_probe_script_exists_and_is_executable_path():
    assert SCRIPT.is_file(), "探针脚本缺失：%s" % SCRIPT


def test_probe_openclaw_detects_reply_semantics():
    """openclaw 侧必须能定位到 reply 调用（结论的支点之一）。"""
    res = mod.probe_openclaw()
    assert res["found"], "未定位到 openclaw reply 语义：%s" % res.get("reason")
    assert res["semantics"] == "reply"
    assert res["reply_api_files"], "reply 调用文件列表为空"


def test_probe_daren_bot_detects_create_semantics():
    """daren_bot 侧必须能定位到 create 调用（结论的支点之二）。"""
    res = mod.probe_daren_bot()
    assert res["found"], "未定位到 daren_bot create 语义：%s" % res.get("reason")
    assert res["semantics"] == "create"
    assert res["create_call_count"] >= 1
    assert res["reply_call_count"] == 0, (
        "daren_bot 出现了 reply 调用 ⇒ 「形态差异来自发送端语义」这条结论需要重新取证"
    )


def test_probe_conclusion_mentions_not_llm_provider():
    """结论必须显式排除 LLM provider —— 这是老大问题的核心澄清。

    判据取自**实际 stdout**（结论文案在 main() 里拼装，不在模块 docstring），
    而不是去读 main.__doc__（那是 None，会抛 TypeError 而不是给出有效判据）。
    """
    oc = mod.probe_openclaw()
    db = mod.probe_daren_bot()
    if not (oc.get("found") and db.get("found")):
        # 环境不具备时不得「假绿」：直接失败，交由人工复核
        raise AssertionError(
            "环境不满足双侧取证前提，需人工复核：oc=%s db=%s" % (oc, db)
        )
    assert oc["semantics"] != db["semantics"], (
        "两侧语义相同 ⇒ 「形态差异来自发送端」不成立，需重新取证"
    )
    rc, out = _run_probe()
    assert rc == 0
    assert "与 LLM provider 无关" in out, (
        "结论文案必须显式排除 LLM provider（老大的核心疑问），实际输出：\n%s" % out[-800:]
    )


# ---------- 可执行行为（退出码 + 文案） ----------

def _run_probe() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    return proc.returncode, proc.stdout + proc.stderr


def test_probe_exits_zero_on_real_environment():
    """真实环境：探针必须 rc=0 且给出结论。"""
    rc, out = _run_probe()
    assert rc == 0, "真实环境探针应 rc=0，实际 rc=%s\n%s" % (rc, out[-800:])
    assert "结论：" in out
    assert "reply" in out and "create" in out


def test_probe_fails_loudly_when_daren_bot_semantics_lost():
    """负向对照：daren_bot 不再走 create ⇒ 探针必须 rc=1 且文案点明结论不成立。

    判据同时断言**退出码**与**错误文案**（只写「rc≠0」不足以证明是断言生效，
    而非脚本崩溃）。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    assert ".im.v1.message.create(" in src

    # 不改真实仓库文件：构造一份「daren_bot 已被改写」的临时副本
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp) / "repo"
        (tmp_root / "scripts").mkdir(parents=True)
        (tmp_root / "daren_bot").mkdir(parents=True)
        shutil_copy = (REPO_ROOT / "daren_bot" / "ws_manager.py").read_text(encoding="utf-8")
        # 破坏 create 调用 ⇒ 探针在 daren_bot 侧找不到证据
        shutil_copy = shutil_copy.replace(".im.v1.message.create(", ".im.v1.message.createXX(")
        (tmp_root / "daren_bot" / "ws_manager.py").write_text(shutil_copy, encoding="utf-8")
        (tmp_root / "scripts" / "probe_feishu_reply_mode.py").write_text(src, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(tmp_root / "scripts" / "probe_feishu_reply_mode.py")],
            capture_output=True, text=True, cwd=str(tmp_root),
        )
        out = proc.stdout + proc.stderr
        assert proc.returncode == 1, (
            "daren_bot 语义丢失时探针应 rc=1，实际 rc=%s\n%s" % (proc.returncode, out[-800:])
        )
        assert "找不到 message.create 调用" in out, (
            "失败文案应点明 daren_bot 侧证据丢失，实际输出：\n%s" % out[-800:]
        )
        assert "本结论不成立" in out, "失败时应明确声明结论不成立"
