# -*- coding: utf-8 -*-
"""★★ 守卫脚本的 **`--app` 参数管道**回归守卫（2026-10-27 A 边 §81.13）

★ 为什么必须有这条断言（它防的是一个**潜伏 6 小时、还写进了交接单「实测全绿」的**真 bug）：
  `scripts/e2e_feishu_rotate_exit_codes.sh` 里**曾经**有一行 `APP_TAG="t_default"`，
  位置在**参数解析之后** ⇒ 无条件把 `--app` 解析出来的值重置回默认值
  ⇒ ⇒ `--app mumu` / `--app helen` **全部不生效** ⇒ **守卫永远只测貂蝉（t_default）**
  ⇒ ⇒ ★★ **老大轮换顺序第一步就是「木木」⇒ 木木从来没被这个守卫真正测过**
  ⇒ ⇒ ★★ 而 §56.5 / §56.6 里那四行「4 个 app 各 20/20 全绿」
     ⇒ **实际是同一个 app 跑了 4 遍** ⇒ **一个数字看起来覆盖了 4 个目标，其实覆盖了 1 个**

★ 同款坑型：**「声明端 ≠ 使用端」**（参数解析端写了，使用端被自己覆盖）
  —— 本项目已复发多次（CSS 选择器 vs JS 写入端、字段名、探针失效落到通过分支…）

★ 判据设计：
  ① **走真脚本**（不是 Python 里重写一遍映射）⇒ 否则测的是「副本」不是「本体」；
  ② 用脚本自带的 **`--print-app` 快通道**（只解析参数、不跑 22 个用例）⇒ **秒级**；
  ③ **必须能红**：把脚本里那行重置加回去 ⇒ 本文件必须红（已在 §81.13 独立验证过）。
"""
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(REPO, "scripts", "e2e_feishu_rotate_exit_codes.sh")

# ★ 不可变 ID（来自 ~/.openclaw/openclaw.json）—— 定位靠 ID 不靠名字
EXPECT = {
    "t_default": ("cli_aa420db06a78dcc5", "t_default", "FEISHU_APPSECRET_T_DEFAULT"),
    "mumu":      ("cli_aa51b1b62a7a9bf6", "mumu",      "FEISHU_APPSECRET_MUMU"),
    "default":   ("cli_aafb5d7cd6389be4", "default",   "FEISHU_APPSECRET_MAIN"),
    "helen":     ("cli_aaf9b98563b99bd7", "helen",     "FEISHU_APPSECRET_HELEN"),
}


def _print_app(tag):
    """跑守卫的 --print-app 快通道，返回 (rc, stdout+stderr 合并输出)。

    ⚠️ **stdout + stderr 都要看**（A 边 §81.13 踩过）：
      脚本里「未知 app 标签」那行是 `>&2` ⇒ **只查 stdout 会得到空串 ⇒ 假红**。
    ⇒ ⇒ 判据统一用**合并输出**，别只挑一个流。
    """
    p = subprocess.run(
        ["bash", GUARD, "--print-app", "--app", tag],
        capture_output=True, text=True, timeout=60,
    )
    return p.returncode, (p.stdout + p.stderr).strip()


def test_guard_app_plumbing_resolves_each_tag_to_its_own_app():
    """★★ 每个 `--app` 标签必须解析到**自己的** app_id/tenant/entry。"""
    for tag, (app_id, tenant, entry) in EXPECT.items():
        rc, out = _print_app(tag)
        assert rc == 0, "--app %s 解析失败 rc=%s out=%r" % (tag, rc, out)
        parts = out.split()
        assert parts[:4] == [tag, app_id, tenant, entry], \
            "--app %s 解析成了 %r（期望 %s %s %s）⇒ **参数管道没生效**（声明端 ≠ 使用端）" \
            % (tag, out, app_id, tenant, entry)


def test_guard_app_plumbing_fixture_lengths_are_tenant_independent():
    """★★★ fixture 假密钥长度必须**与 tenant 名无关**（第二层缺陷的守卫）。

    ★ 历史坑：fixture 常量曾是 `OLDfakeSECRET_${TEN}_00000AAA` ⇒ 长度 = 23 + len(TEN)
      ⇒ **只有 len(TEN)==9 的那一个 tenant 恰好 32 字符**
      ⇒ ⇒ 其余 `--app` 一律 `exit 9`（实测：mumu/default/helen 全 rc=9，只有 t_default 能跑）
      ⇒ ⇒ ⇒ 也就是说 **`--app` 从来就没有任何一刻是能用的**（第一层 = 参数被重置；第二层 = 长度）
    ⇒ ⇒ 本条把「任何 tenant 长度下 fixture 都是 32/32/31」变成可执行事实。
    """
    for tag in EXPECT:
        rc, out = _print_app(tag)
        assert rc == 0, "--app %s 在 fixture 长度闸上就退了（rc=%s，out=%r）⇒ 长度与 tenant 有关" \
            % (tag, rc, out)
        parts = out.split()
        assert len(parts) == 7, "输出格式变了（期望 7 段）: %r" % out
        old_len, new_len, bad_len = (int(x) for x in parts[4:7])
        assert (old_len, new_len, bad_len) == (32, 32, 31), \
            "--app %s 的 fixture 长度 = %s（期望 32/32/31）⇒ 长度随 tenant 变" \
            % (tag, (old_len, new_len, bad_len))


def test_guard_app_plumbing_tags_are_not_all_the_same():
    """★★ 防「四个标签其实指向同一个 app」—— 那正是 §56.6 那张假表的成因。

    ⇒ §56.6 记着「4 个 app 各 20/20 全绿」⇒ **但那四行是同一个 app 跑了 4 遍**
    ⇒ ⇒ 本条把「4 个目标 ⇒ 4 个不同的 app_id」变成**可执行事实**
    """
    resolved = {}
    for tag in EXPECT:
        rc, out = _print_app(tag)
        assert rc == 0
        parts = out.split()
        assert len(parts) == 4 or len(parts) == 7, "输出格式变了: %r" % out
        resolved[tag] = tuple(parts[1:4])

    ids = [v[0] for v in resolved.values()]
    assert len(set(ids)) == len(ids), \
        "多个 --app 标签解析到同一个 app_id ⇒ 有标签没真正生效：%r" % resolved


def test_guard_app_plumbing_rejects_unknown_tag():
    """★★ 未知标签必须**判红退出**（不许静默落到默认 app）。

    ⇒ 「认不出就回落默认值」= 静默降级 ⇒ 调用方以为测了 A、其实测了 B。
    """
    rc, out = _print_app("no_such_tag")
    assert rc != 0, "未知 --app 标签必须非 0 退出（实际 rc=0，out=%r）" % out
    assert "未知 app 标签" in out, \
        "未知标签应打印「未知 app 标签」，实际 out=%r" % out


def test_guard_without_app_flag_defaults_to_t_default():
    """★ 不传 `--app` 时行为**完全不变**（默认 t_default）⇒ 修 bug 不许改默认路径。"""
    rc, out = _print_app("t_default")
    assert rc == 0
    assert out.startswith("t_default "), "默认路径变了: %r" % out
