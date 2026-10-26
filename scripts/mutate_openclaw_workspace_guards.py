#!/usr/bin/env python3
"""变异验证：OpenClaw workspace 路径解析的护栏必须**能红**。

背景（2026-10-26 真实事故）：
  服务端三处按 `agents.json` 的 `openclawName` 拼 workspace 目录（**声明端**），
  而 OpenClaw 运行时用的是 `openclaw.json` 的 `entry.workspace`（**使用端**）。
  木木的声明是 'main' ⇒ 读写全错位（详见 tests/test_openclaw_workspace.py）。

每个变异 = 把修复改回旧行为 → 跑对应测试 → 必须 FAIL → 还原。

★ 纪律（沿用 scripts/mutate_failure_guards.py）：
  ① 改完文件 `sleep 1.2` 让 mtime 跨秒 + 清**仓库自己的** `__pycache__`
  ② **还原必须用备份副本，不能用 `git checkout --`**（会冲掉未提交真改动）
  ③ 还原校验只比**变异动过的文件**
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
SEL = "tests/test_openclaw_workspace.py"
SERVER = "solobrave-server.py"


def clear_pycache():
    for p in ROOT.rglob("__pycache__"):
        if {".venv", "venv", "node_modules"} & set(p.parts):
            continue
        shutil.rmtree(p, ignore_errors=True)


def patch(rel: str, old: str, new: str):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert old in src, f"变异锚点没找到：{rel}\n  {old[:160]}"
    _BACKUPS.setdefault(rel, src)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    time.sleep(1.2)
    clear_pycache()


def restore(rel: str):
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
    ("变异W1：路径改回按 openclawName 拼（声明端）—— 木木回到老 bug",
     SERVER,
     "    entry = (_openclaw_agent_entries() or {}).get(agent_id) or {}\n"
     "    ws = (entry.get('workspace') or '').strip()\n"
     "    if ws:\n"
     "        return os.path.abspath(os.path.expanduser(ws))\n",
     "    entry = (_openclaw_agent_entries() or {}).get(agent_id) or {}\n"
     "    ws = (entry.get('workspace') or '').strip()\n"
     "    if False:\n"
     "        return os.path.abspath(os.path.expanduser(ws))\n",
     SEL),

    ("变异W2：没有 entry 时不再回退（新建员工写不进文档）",
     SERVER,
     "    logger.warning(\n"
     "        f'[OpenClawWS] {agent_id} 在 openclaw.json 里没有 entry，'\n"
     "        f'回退到声明端拼法 {fallback}')\n"
     "    return fallback\n",
     "    logger.warning(\n"
     "        f'[OpenClawWS] {agent_id} 在 openclaw.json 里没有 entry，'\n"
     "        f'回退到声明端拼法 {fallback}')\n"
     "    return ''\n",
     SEL),

    ("变异W3：写前归属校验永远放行（又能覆盖别人的 SOUL.md）",
     SERVER,
     "    owner = _workspace_owner_agent(workspace_path)\n"
     "    if owner and owner != agent_id:\n"
     "        return owner\n"
     "    return ''\n",
     "    return ''\n",
     SEL),

    ("变异W4：目录归属反查恒返回空（归属校验形同虚设）",
     SERVER,
     "        if ws and os.path.abspath(os.path.expanduser(ws)) == target:\n"
     "            return aid\n"
     "    return ''\n",
     "        pass\n"
     "    return ''\n",
     SEL),

    ("变异W5：_resolve_agent_soul 改回按 openclawName 拼（读到别人的灵魂）",
     SERVER,
     "    agent_id = (agent.get('id') or '').strip()\n"
     "    soul_path = os.path.join(_resolve_agent_workspace(agent_id, agent), 'SOUL.md')\n",
     "    openclaw_name = (agent.get('openclawName') or '').strip()\n"
     "    soul_path = os.path.join(os.path.expanduser('~/.openclaw/workspace-' + openclaw_name), 'SOUL.md')\n",
     SEL),
]

failed = []

print("=== 基线自检（变异前必须全绿）===")
rc, out = run_tests(SEL)
if rc != 0:
    print("❌ 基线不绿 ⇒ 变异验证无意义，先修好基线：")
    print(out)
    sys.exit(1)
print("  ✅ 基线全绿")

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

bad_restore = []
for rel, backup in _BACKUPS.items():
    cur = (ROOT / rel).read_text(encoding="utf-8")
    if cur != backup:
        bad_restore.append(rel)
if bad_restore:
    print("❌ 以下文件还原后与变异前不一致：")
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
