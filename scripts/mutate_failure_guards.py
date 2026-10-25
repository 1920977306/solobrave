#!/usr/bin/env python3
"""变异验证：失败话术层 / 运营告警 / 登录态巡检 的新护栏必须**能红**。

每个变异 = 把修复改回旧行为 → 跑对应测试 → 必须 FAIL → 还原。

★ 两条纪律（沿用 scripts/mutate_new_guards.py）：
  ① 改完文件必须 `sleep 1.2` 让 mtime 跨秒，并清掉**仓库自己的** `__pycache__`
     （别清 venv 的，慢）—— 否则 python 可能加载旧字节码，结论不可信。
  ② **还原必须用备份副本，不能用 `git checkout --`** ——
     未提交的真改动会被一起冲掉（2026-10-25 实测踩过）。
     脚本跑完会**逐字**校验它动过的文件还原回来了。

★ 本轮要抓的「恒真闸门」形态：
  - 告警守卫写成「什么都报」⇒ 抓不到真问题（本文件 变异M5 专门打它）
  - 巡检把「浏览器没开」当掉登录 ⇒ 天天误报（变异M6）
  - 达人话术里混进内部词 ⇒ 静态闸门抓（变异M11）
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
SEL = "tests/test_failure_text.py"


def clear_pycache():
    for p in ROOT.rglob("__pycache__"):
        if {".venv", "venv", "node_modules"} & set(p.parts):
            continue
        shutil.rmtree(p, ignore_errors=True)


def patch(rel: str, old: str, new: str):
    p = ROOT / rel
    src = p.read_text(encoding="utf-8")
    assert old in src, f"变异锚点没找到：{rel}\n  {old[:120]}"
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
    # ── 一、登录态类错误不许回退（§8.1：回退链路查团长端）──
    ("变异M1：登录态失效回退到页面解析链路（查团长端，掩盖真因）",
     "daren_bot/executors.py",
     "        except (ProductNotFound, NeedLogin):",
     "        except (ProductNotFound,):",
     SEL),

    ("变异M2：平台查无此商品也回退（回退本来就没用）",
     "daren_bot/executors.py",
     "        except (ProductNotFound, NeedLogin):",
     "        except (NeedLogin,):",
     SEL),

    # ── 二、达人看到的是人话，不是异常原文 ──
    ("变异M3：把原始异常原文甩给达人",
     "daren_bot/worker.py",
     "            user_text = failure_text.user_text_for(exc, product=product_scene)",
     '            user_text = f"任务失败：{reason}"',
     SEL),

    ("变异M4：登录态失效不告警运营（老大继续不知道）",
     "daren_bot/worker.py",
     "        if exc is not None and failure_text.is_login_failure(exc):",
     "        if False:",
     SEL),

    ("变异M5：任何失败都告警运营（恒真闸门：什么都报=什么都不是）",
     "daren_bot/worker.py",
     "        if exc is not None and failure_text.is_login_failure(exc):",
     "        if exc is not None:",
     SEL),

    ("变异M6：通用场景误用商品话术（对标号任务里冒「商品/截图」）",
     "daren_bot/failure_text.py",
     "    table = _USER_TEXT_PRODUCT if product else _USER_TEXT_GENERIC",
     "    table = _USER_TEXT_PRODUCT",
     SEL),

    ("变异M7：达人话术里混进内部词（异常类名）",
     "daren_bot/failure_text.py",
     '        "这个商品的数据我暂时没取到（我们这边的取数通道在维护）。\\n"',
     '        "RuntimeError: 这个商品的数据我暂时没取到（我们这边的取数通道在维护）。\\n"',
     SEL),

    # ── 三、巡检判定口径（浏览器没开 ≠ 掉登录）──
    ("变异M8：把「浏览器没开」当成掉登录（天天误报）",
     "daren_bot/login_watcher.py",
     '    if not up:\n'
     '        # 浏览器没开（切到团长端 / 还没启动）—— **不是掉登录**\n'
     '        return "unknown"',
     '    if not up:\n'
     '        return "bad"',
     SEL),

    ("变异M9：单次采样就告警（去掉防抖，抖动即误报）",
     "daren_bot/login_watcher.py",
     "            if self._bad_streak >= BAD_STREAK_THRESHOLD and not self._bad_notified:",
     "            if not self._bad_notified:",
     SEL),

    ("变异M10：同一轮故障反复告警（没有边沿触发）",
     "daren_bot/login_watcher.py",
     "            if self._bad_streak >= BAD_STREAK_THRESHOLD and not self._bad_notified:",
     "            if True:",
     SEL),

    # ── 四、前置预检 ──
    ("变异M11：前置预检被去掉（掉登录也照常入队扣分）",
     "daren_bot/handler.py",
     '        if _exec.daren_channel_state() == "logged_out":',
     "        if False:",
     SEL),

    ("变异M12：前置预检把「浏览器没开」当掉登录（正常请求被误拦）",
     "daren_bot/executors.py",
     '    if not bool(d.get("daren_browser_up")):\n'
     '        return "unknown"          # 浏览器没开（可能是切到团长端了）',
     '    if not bool(d.get("daren_browser_up")):\n'
     '        return "logged_out"',
     SEL),

    # ── 五、告警出口本身 ──
    ("变异M13：告警去重失效（一次掉登录刷屏）",
     "daren_bot/ops_alert.py",
     "        if last and now - last < DEDUP_SECONDS:",
     "        if False:",
     SEL),

    ("变异M14：告警通道异常冒到主链路（告警失败拖垮采集）",
     "daren_bot/ops_alert.py",
     'def _append_file(title: str, body: str, kind: str) -> bool:\n'
     '    """落盘（admin 后台读它）。"""\n'
     "    try:",
     'def _append_file(title: str, body: str, kind: str) -> bool:\n'
     '    """落盘（admin 后台读它）。"""\n'
     '    raise OSError("disk full")\n'
     "    try:",
     SEL),

    ("变异M15：巡检线程用错事件对象（`Thread._stop` 是方法 ⇒ 线程一起来就死）",
     "daren_bot/login_watcher.py",
     "        while not self._stop_event.is_set():",
     "        while not self._stop.is_set():",
     SEL),
]

failed = []

# ★★ 基线自检：**变异之前**测试必须是全绿的。
#   否则「变异后变红」证明不了任何事 —— 基线本来就红的话，
#   每个变异都会「被抓到」，是假象（2026-10-25 实测踩过：
#   `test_alert_survives_channel_failure` 在基线就是红的，
#   变异M14 的「被抓住」其实是基线红，不是护栏起作用）。
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
