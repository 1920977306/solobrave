"""★★ 把「脚本式测试」接进 pytest —— 别让覆盖继续是死的。

★ 这是在解决一个**假覆盖**问题（2026-10-26 B 边）：
  `tests/` 下有 4 个文件是**脚本式**的（`check()` + `main()` + `if __name__ == '__main__'`），
  它们的断言全在 `main()` 里 ⇒ **pytest 收集到 0 条** ⇒
  「看着有覆盖，CI 其实跑不到」。

  ⚠️ 特别提醒一个容易自我安慰的坑：
  「我跑过了，57/57 全绿」—— **那是用 `python tests/xxx.py` 跑的**，
  而 CI 跑的是 `pytest` ⇒ 两者**根本不是同一件事**。
  ⇒ 判据只有一个：**`pytest --collect-only` 收集到几条**。

★ 本文件的做法（不重写脚本、只包一层）：
  - 用**子进程**跑脚本 ⇒ ① 隔离全局状态（脚本会改模块级冷却/半开状态，
    在 pytest 进程内跑会污染别的测试 ⇒ 顺序依赖型 flaky）；
    ② 脚本崩了也是红。
  - 断言脚本的**退出码 + 逐条 [FAIL]** ⇒ 失败时能看出是哪条检查挂了。
  - 需要真实环境（真 server / 真 API）的标 `live`（项目既有约定：
    `pytest.ini` 的 `markers = live`，默认 skip，`SOLO_BRAVE_LIVE=1` 才跑）
    ⇒ **至少是「看得见的 skip + 有原因」，不是隐形死代码**。

★ 末尾那条守卫防的是「以后又新增一个脚本式测试没人发现」。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# (脚本相对路径, 运行模式, 为什么)
#   模式 "run"  —— 无条件跑（纯本地，CI 安全）
#   模式 "env"  —— 需要仓库根有 .env（凭据）；没有就 skip 并写明原因
#   模式 "live" —— 需要真实环境（真 server / 真 API），`SOLO_BRAVE_LIVE=1` 才跑
SCRIPT_SUITES = [
    ("tests/test_provider_adapters.py", "run",
     "纯模块断言（双向转换 / max_tokens 分场景 / system 抽取 / SSE / 冷却半开 / 注册表），零外部依赖"),
    ("tests/test_proxy_engine.py", "env",
     "A~E 组 49 条走本地 mock（localhost，不打真实上游）；"
     "F 组 12 条是 **handler 级**（上游**仍是本地 mock**，不打真实 API），"
     "但 handler 自己要从 .env 取 kimi key 才能构造降级链 ⇒ **要凭据**（仓库根 .env）"
     "（实测：无 .env 的目录跑出 49/57，红的正是 F1/F2/F3/F8/F9/F10/F11/F12；"
     "有 .env 的目录 57/57）"),
    ("tests/test_kimi_response.py", "live",
     "含真实上游 E2E（Kimi/minimax）＋ 1 条**已知真 bug**："
     "C4「HTML 标签去除 + 取前 300 字」—— `_extract_text_from_garbage()` 的关键词正则"
     "会把散文里的 'error' 当成错误（'…that is not an error but garbage data' ⇒ 返回"
     " 'Kimi 服务提示: but garbage data'）⇒ **修在 solobrave-server.py（A 边占用中）**"),
    ("tests/test_proxy_e2e.py", "live",
     "要起真 server + 真登录态（起本地服务 + 注册登录）"),
]

_FAIL_RE = re.compile(r"\[FAIL\]\s*(.+)")


def _has_env() -> bool:
    """仓库根有没有 .env（凭据）。

    ⚠️ 只看**文件在不在**，不去读内容（§7：不碰密钥明文）。
    """
    return (ROOT / ".env").exists()


def _run_script(script: str):
    proc = subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=600,
    )
    fails = [m.strip() for m in _FAIL_RE.findall(proc.stdout)]
    return proc, fails


@pytest.mark.parametrize(
    "script,mode,why",
    SCRIPT_SUITES,
    ids=[s[0] for s in SCRIPT_SUITES],
)
def test_script_suite_is_green(script, mode, why):
    """★ 脚本式套件在 pytest 里**真跑**，且失败能定位到具体检查项。

    ★ 为什么用**子进程**而不是直接 `import` 跑 `main()`：
      这些脚本会改模块级状态（provider 冷却 / 半开探测 / 端口占用）⇒
      在 pytest 进程内跑 ⇒ 污染同一次会话里其它测试（顺序依赖型 flaky，
      而且「昨天绿今天红」最难查）。⇒ 子进程隔离最省心。
    """
    if mode == "live" and not os.environ.get("SOLO_BRAVE_LIVE"):
        pytest.skip(f"需要真实环境：{why}（设 SOLO_BRAVE_LIVE=1 才跑）")
    if mode == "env" and not _has_env():
        pytest.skip(f"需要凭据（仓库根 .env）：{why}")
    proc, fails = _run_script(script)
    assert proc.returncode == 0, (
        f"{script} 退出码 {proc.returncode}，{len(fails)} 条检查失败：\n"
        + "\n".join(f"  - {f}" for f in fails)
        + f"\n----- stdout 尾部 -----\n{proc.stdout[-3000:]}"
        + f"\n----- stderr 尾部 -----\n{proc.stderr[-2000:]}")


def test_every_script_style_test_is_registered():
    """★★ 守卫：`tests/` 下**pytest 收集 0 条**的文件，必须登记在本文件里。

    ★ 为什么锁这条：脚本式测试文件「看着有覆盖、CI 跑不到」，
      而且**新加的人不会有人提醒**（因为它自己 `python xxx.py` 跑是绿的）⇒
      ⇒ 一旦出现「`tests/` 下有文件没有 `def test_*`」，就必须登记到
        `SCRIPT_SUITES` 里（要么进 CI，要么显式标 live + 写明原因）。
    """
    unregistered = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        src = path.read_text(encoding="utf-8")
        has_test_func = re.search(r"^\s*def test_", src, re.M) is not None
        has_test_class = re.search(r"^\s*class Test", src, re.M) is not None
        if has_test_func or has_test_class:
            continue                      # 正常 pytest 文件，不管
        rel = path.relative_to(ROOT).as_posix()
        if rel not in {s[0] for s in SCRIPT_SUITES}:
            unregistered.append(rel)
    assert not unregistered, (
        "以下测试文件没有 `def test_*`（pytest 收集 0 条 ⇒ CI 跑不到），"
        "但没登记在 SCRIPT_SUITES 里：\n  " + "\n  ".join(unregistered)
        + "\n⇒ 把它接进 pytest（改成 test_ 函数），或登记到 SCRIPT_SUITES 并写明"
          "为什么需要真实环境 —— 别让它继续是『看着有覆盖、CI 跑不到』的假覆盖。")
