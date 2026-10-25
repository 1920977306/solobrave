#!/usr/bin/env python3
"""变异验证：LLM 降级链的「第二家独立服务商」护栏必须**能红**。

背景（2026-10-25 老大问「要再买 API 吗」的取证结论）：
  改造前降级链有 6 个环、3 个「可用」—— 实则**全是 MiniMax 一家**
  （套餐 API / Code 订阅 / 同一把 key 的另一条协议）。
  ⇒ 一家挂了全线崩，**冗余是假的**。
  ⇒ 而老大买的智谱资源包**文字与识图都实测通**，只是压根没接进链里。

每个变异 = 把「第二家」这件事改回原样 → 跑对应测试 → 必须 FAIL → 还原。

★ 两条纪律（沿用 scripts/mutate_failure_guards.py）：
  ① 改完文件必须 `sleep 1.2` 让 mtime 跨秒，并清掉**仓库自己的** `__pycache__`
     （别清 venv 的，慢）—— 否则 python 可能加载旧字节码，结论不可信。
  ② **还原必须用备份副本，不能用 `git checkout --`** ——
     未提交的真改动会被一起冲掉（2026-10-25 实测踩过）。
     脚本跑完会**逐字**校验它动过的文件还原回来了。

★ 本轮要抓的「恒真闸门」形态：
  - 探针按**环数**而不是**服务商数**统计 ⇒ 把一家数成三家（变异 M5/M7）
  - 探针把「通道活着但没吐字」判成死 ⇒ 报假警报（变异 M6）
  - 智谱环的 base 没剥 /embeddings ⇒ 请求 404（变异 M1）
  - 智谱环排在额度耗尽环后面 ⇒ 每次降级白等两轮失败（变异 M9）
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
SEL = "tests/test_llm_channels.py"
LC = "daren_bot/llm_channels.py"
PROBE = "daren_bot/tools/probe_llm_channels.py"


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
    time.sleep(1.2)          # ★ 让 mtime 跨秒
    clear_pycache()


def restore(rel: str):
    """还原成**变异前的样子**（备份副本），不是 HEAD。"""
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
    # ── 一、智谱 chat base 必须剥掉 /embeddings（实测踩过 → 404）──
    ("变异M1：智谱 base 不剥 /embeddings（请求打到 /embeddings/chat/completions → 404）",
     LC,
     "    base = env_api_key('SOLOBRAVE_EMBEDDING_BASE_URL').rstrip('/')\n"
     "    if base.endswith('/embeddings'):\n"
     "        base = base[:-len('/embeddings')]\n",
     "    base = env_api_key('SOLOBRAVE_EMBEDDING_BASE_URL').rstrip('/')\n",
     SEL),

    # ── 二、第二家服务商必须真的在链上 ──
    ("变异M2：文字链里不接智谱（退回「只有一家」的老状态）",
     LC,
     "    zbase, zkey = _zhipu_channel()\n"
     "    if zkey:\n"
     "        zmodels = list(ZHIPU_TEXT_MODELS)\n",
     "    zbase, zkey = '', ''\n"
     "    if zkey:\n"
     "        zmodels = list(ZHIPU_TEXT_MODELS)\n",
     SEL),

    ("变异M3：识图链里不接智谱（识图只剩一家）",
     LC,
     "    zbase, zkey = _zhipu_channel()\n"
     "    if zkey:\n"
     "        rings.append({'protocol': 'openai', 'base': zbase, 'key': zkey,\n"
     "                      'model': ZHIPU_VISION_MODEL, 'temp': 1.0,\n",
     "    zbase, zkey = '', ''\n"
     "    if zkey:\n"
     "        rings.append({'protocol': 'openai', 'base': zbase, 'key': zkey,\n"
     "                      'model': ZHIPU_VISION_MODEL, 'temp': 1.0,\n",
     SEL),

    ("变异M4：环上不写 vendor/pool（口径要靠下游反推 ⇒ 一家会被数成三家）",
     LC,
     "                      'vendor': VENDOR_MINIMAX,\n"
     "                      'pool': POOL_CP_SUBSCRIPTION,\n"
     "                      'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'minimax-cp-anthropic'})\n",
     "                      'vendor': VENDOR_MINIMAX,\n"
     "                      'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'minimax-cp-anthropic'})\n",
     SEL),

    # ── 三、探针的「假冗余」判据 ──
    ("变异M5：探针按额度池而不是服务商统计（把一家数成几家）",
     PROBE,
     "    vendors = sorted({_vendor_of(r) for r in live})\n",
     "    vendors = sorted({_pool_of(r) for r in live})\n",
     SEL),

    ("变异M6：探针把「活着但没吐字」判成死（识图链会报假警报）",
     PROBE,
     "    live = alive + empty\n",
     "    live = alive\n",
     SEL),

    ("变异M7：冗余判据改成按环数（3 个环就报「有冗余」）",
     PROBE,
     "        'vendor_redundant': len(vendors) >= 2,\n",
     "        'vendor_redundant': len(live) >= 2,\n",
     SEL),

    ("变异M8：把死额度池报成活的（运维会以为还有余额）",
     PROBE,
     "    dead_pools = sorted({_pool_of(r) for r in dead})\n",
     "    dead_pools = sorted({_pool_of(r) for r in live})\n",
     SEL),

    # ── 四、智谱环的归属与排序 ──
    ("变异M9：智谱环的 vendor 标成 minimax（第二家又变成同一家）",
     LC,
     "                          'vendor': VENDOR_ZHIPU,\n"
     "                          'pool': POOL_ZHIPU_PACK,\n",
     "                          'vendor': VENDOR_MINIMAX,\n"
     "                          'pool': POOL_ZHIPU_PACK,\n",
     SEL),

    ("变异M10：智谱环混进额度耗尽那一档（降级时白等两轮 HTTP 失败）",
     LC,
     "                          'vendor': VENDOR_ZHIPU,\n"
     "                          'pool': POOL_ZHIPU_PACK,\n",
     "                          'vendor': VENDOR_ZHIPU,\n"
     "                          'pool': POOL_KIMI_MONTHLY,\n",
     SEL),

    # ── 五、能力层的「瞬时错误 skip」不许把真契约错误吞掉 ──
    ("变异M11：Code 订阅 base 写错（404 真契约错误被 skip 吞掉 ⇒ 恒真绿）",
     LC,
     "CODE_SUB_BASE = 'https://agent.minimax.cn/mavis/api/v1/llm/v1'\n",
     "CODE_SUB_BASE = 'https://agent.minimax.cn/mavis/api/v1/llm/v1-BROKEN'\n",
     SEL),
]

failed = []

# ★★ 基线自检：**变异之前**测试必须是全绿的。
#   否则「变异后变红」证明不了任何事 —— 基线本来就红的话，
#   每个变异都会「被抓到」，是假象。
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

# ★ 还原校验只查**变异动过的文件**（不比全仓 diff：跑变异期间人可能在改别的文件）。
bad_restore = []
for rel, backup in _BACKUPS.items():
    cur = (ROOT / rel).read_text(encoding="utf-8")
    if cur != backup:
        bad_restore.append(rel)
if bad_restore:
    print("❌ 以下文件还原后与变异前不一致（改动被冲掉了）：")
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
