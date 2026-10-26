#!/usr/bin/env python3
"""变异验证：MiniMax 协议一致性 + 「第一环死 ≠ 全员死」护栏必须**能红**。

背景（2026-10-26 真实事故：木木回复变样 / 15:12 空回复）：
  ① `_resolve_ai_base_url` 里 custom_endpoint 被内置 mapping 覆盖
     ⇒ settings.json 配的 baseUrl 静默失效 ⇒ minimax 打老端点 → 404
  ② `_call_minimax_messages` / `_try_minimax_proxy_fallback` 写死 OpenAI 协议
     ⇒ minimax 档换成套餐 Anthropic 端点后协议/端点/模型全错位
  ③ kimi key 池耗尽时在链之前就 503 ⇒ 「第一环死」被写成「全员死」，
     兜底档没机会接管（实测木木 15:12 那条直接空回复）

每个变异 = 把修复改回旧行为 → 跑对应测试 → 必须 FAIL → 还原。

★ 纪律（沿用 scripts/mutate_openclaw_workspace_guards.py）：
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
SEL = "tests/test_minimax_protocol_consistency.py"
SERVER = "solobrave-server.py"


def clear_pycache():
    for p in ROOT.rglob("__pycache__"):
        if {".venv", "venv", "node_modules"} & set(p.parts):
            continue
        shutil.rmtree(p, ignore_errors=True)


def patch(rel: str, old: str, new: str):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert old in src, f"变异锚点没找到：{rel}\n  {old[:200]}"
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
                        "--no-header", "-p", "no:cacheprovider", "-o", "addopts="],
                       capture_output=True, text=True)
    return r.returncode, (r.stdout or "")[-900:]


CASES = [
    ("变异M1：custom_endpoint 不再优先（settings 配的 baseUrl 又变成死的）",
     SERVER,
     "    custom_endpoint = (custom_endpoint or '').strip()\n"
     "    if custom_endpoint:\n"
     "        return custom_endpoint.rstrip('/')\n",
     "    custom_endpoint = (custom_endpoint or '').strip()\n"
     "    if api_provider == 'custom' and custom_endpoint:\n"
     "        return custom_endpoint.rstrip('/')\n",
     SEL),

    ("变异M2：minimax 默认端点改回老开放平台（实测 404）",
     SERVER,
     "        'minimax': 'https://api.minimax.cn/anthropic/v1',\n",
     "        'minimax': 'https://api.minimax.chat/v1/text',\n",
     SEL),

    ("变异M3：_call_minimax_messages 改回 OpenAI 协议（Bearer + /chatcompletion_v2）",
     SERVER,
     "    headers = adapter.build_headers(api_key)\n"
     "    headers['Content-Length'] = str(len(req_body))\n"
     "    target_url = adapter.build_url(base_url)\n",
     "    headers = {'Content-Type': 'application/json',\n"
     "               'Authorization': f'Bearer {api_key}'}\n"
     "    headers['Content-Length'] = str(len(req_body))\n"
     "    target_url = base_url + '/chatcompletion_v2'\n",
     SEL),

    ("变异M4：兜底请求体不再把 system 提到顶层（Anthropic 上游会 400）",
     SERVER,
     "            if system_parts:\n"
     "                minimax_body['system'] = '\\n\\n'.join(\n"
     "                    p for p in system_parts if isinstance(p, str) and p).strip()\n",
     "            if False:\n"
     "                minimax_body['system'] = '\\n\\n'.join(\n"
     "                    p for p in system_parts if isinstance(p, str) and p).strip()\n",
     SEL),

    ("变异M5：key 池耗尽时提前 503 又回来（第一环死 = 全员死）",
     SERVER,
     "    if agent_api_key is None and PROXY_ENGINE == 'chain':\n",
     "    if agent_api_key is None:\n",
     SEL),

    ("变异M6：legacy 的 503 兜底被拆掉（没有链可退时会带着空 key 往前走）",
     SERVER,
     "    elif agent_api_key is None:\n",
     "    elif False and agent_api_key is None:\n",
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
            print("  ✅ 变异后测试变红 ⇒", (line[-1] if line else out.strip()[-140:]))
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
