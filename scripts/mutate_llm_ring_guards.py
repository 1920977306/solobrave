#!/usr/bin/env python3
"""变异验证：LLM 降级链的「智谱不许回链 + 探针不许报假冗余」护栏必须**能红**。

★ 2026-10-26 契约反转（老大拍板：智谱资源包只做知识库 embedding，不做 LLM 对话）：
  原来这组护栏锁的是「智谱环必须在链上」（当时它是第二家独立服务商）；
  现在反过来锁 —— **智谱环/符号都不许再回到链上**，
  且「独立服务商数」这条口径必须**如实报告现状（=1，冗余归零）**。

★ 连带损伤的根因也在这里锁住（2026-10-26 实测）：
  视觉 QA 原来读 `settings.llm.providers`（**对话链的档位表**）——
  两份语义不同的事实共用一份配置源 ⇒ 对话链摘掉智谱档时，
  视觉 QA 的 witness/repair 被一起带断（字段提取精度 95%+ → ~67%）。
  ⇒ M10 专门盯这条：配置源跑回对话链 ⇒ 必须红。

★ 两条纪律（沿用 scripts/mutate_failure_guards.py）：
  ① 改完文件必须 `sleep 1.2` 让 mtime 跨秒，并清掉**仓库自己的** `__pycache__`
     （别清 venv 的，慢）—— 否则 python 可能加载旧字节码，结论不可信。
  ② **还原必须用备份副本，不能用 `git checkout --`** ——
     未提交的真改动会被一起冲掉（2026-10-25 实测踩过）。
     脚本跑完会**逐字**校验它动过的文件还原回来了。

★ 本轮要抓的「恒真闸门」形态：
  - 探针按**环数**而不是**服务商数**统计 ⇒ 把一家数成三家（变异 M5/M7）
  - 探针把「通道活着但没吐字」判成死 ⇒ 报假警报（变异 M6）
  - 视觉 QA 的 base 没剥 /embeddings ⇒ 请求 404（变异 M1）
  - 视觉 QA 的配置源跑回对话链的 llm.providers ⇒ 会被对话链带断（变异 M10）
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
SEL_VQA = "tests/test_vision_qa.py"
LC = "daren_bot/llm_channels.py"
PROBE = "daren_bot/tools/probe_llm_channels.py"
SRV = "solobrave-server.py"

#: 基线自检要跑的全部选择器（每个变异只跑自己那一条）
BASELINE = [SEL, SEL_VQA]


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


# 智谱环（变异体）：故意用**字面量**而不是模块常量 ——
# 这样 M2/M3 只测「环回链」，M9 才测「符号复活」，两个护栏互不遮蔽。
_ZHIPU_RING = (
    "    rings.append({'protocol': 'openai', 'base': 'https://open.bigmodel.cn/api/paas/v4',\n"
    "                  'key': 'fake-zhipu-key', 'model': 'glm-4-flash', 'temp': 1.0,\n"
    "                  'models': ('glm-4-flash',), 'auth': 'bearer', 'refresh': False,\n"
    "                  'vendor': 'zhipu', 'pool': 'zhipu-resource-pack',\n"
    "                  'max_tokens': DEFAULT_MAX_TOKENS, 'label': 'zhipu-glm-4-flash'})\n"
)

CASES = [
    # ── 一、视觉 QA 的 base 必须剥掉 /embeddings（实测踩过 → 404）──
    ("变异M1：视觉 QA base 不剥 /embeddings（请求打到 /embeddings/chat/completions → 404）",
     SRV,
     "        if base.endswith('/embeddings'):\n"
     "            base = base[:-len('/embeddings')]\n",
     "",
     SEL_VQA),

    # ── 二、智谱环不许回链（老大 2026-10-26 拍板）──
    ("变异M2：文字链重新接回智谱环（智谱资源包是知识库的，不该被对话流量偷额度）",
     LC,
     "    # ---- 环 3+：kimi + minimax 开放平台（当前 429/402，保留但不指望）----\n",
     _ZHIPU_RING
     + "    # ---- 环 3+：kimi + minimax 开放平台（当前 429/402，保留但不指望）----\n",
     SEL),

    ("变异M3：识图链重新接回智谱环",
     LC,
     "    try:\n"
     "        kbase, kkey = _kimi_channel()\n"
     "        if kkey:\n"
     "            rings.append({'protocol': 'openai', 'base': kbase, 'key': kkey,\n"
     "                          'model': 'moonshot-v1-8k-vision-preview', 'temp': 1.0,\n",
     _ZHIPU_RING
     + "    try:\n"
     "        kbase, kkey = _kimi_channel()\n"
     "        if kkey:\n"
     "            rings.append({'protocol': 'openai', 'base': kbase, 'key': kkey,\n"
     "                          'model': 'moonshot-v1-8k-vision-preview', 'temp': 1.0,\n",
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

    # ── 四、智谱符号不许复活（口径变更要改代码才能生效）──
    ("变异M9：重新引入 VENDOR_ZHIPU 符号（留个不用的常量 = 留条复活后门）",
     LC,
     "VENDOR_MINIMAX = 'minimax'\nVENDOR_KIMI = 'kimi'\n",
     "VENDOR_MINIMAX = 'minimax'\nVENDOR_ZHIPU = 'zhipu'\nVENDOR_KIMI = 'kimi'\n",
     SEL),

    # ── 五、视觉 QA 的配置源必须独立于对话链（连带损伤的根因）──
    ("变异M10：视觉 QA 配置源跑回对话链的 llm.providers（会被对话链改配置一起带断）",
     SRV,
     "        qa = settings.get('visionQA') or {}\n",
     "        qa = settings.get('visionQA') or {}\n"
     "        if not qa:\n"
     "            for _p in ((settings.get('llm') or {}).get('providers')) or []:\n"
     "                if 'zhipu' in (_p.get('name') or '').lower() and _p.get('apiKey'):\n"
     "                    qa = {'provider': 'zhipu', 'apiKey': _p['apiKey'],\n"
     "                          'baseUrl': _p.get('baseUrl') or ''}\n"
     "                    break\n",
     SEL_VQA),

    # ── 六、能力层的「瞬时错误 skip」不许把真契约错误吞掉 ──
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
for sel in BASELINE:
    rc, out = run_tests(sel)
    if rc != 0:
        print(f"❌ 基线不绿（{sel}）⇒ 变异验证无意义，先修好基线：")
        print(out)
        sys.exit(1)
    print(f"  ✅ {sel} 全绿")

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
