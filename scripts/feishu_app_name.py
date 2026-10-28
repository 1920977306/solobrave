#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""飞书 app 身份自检 —— 在轮换**之前**确认「后台那个 app 到底是不是本轮要轮换的那个」。

★ 为什么要有这个（B 边 §80.14.1 第九项实测发现，A 边 §83 落地）:
  飞书后台的应用名**不是唯一的**——「貂蝉」这**两个字在飞书后台对应两个不同的 app**：
    cli_aa420db06a78dcc5（= t_default，tuz 账号）后台名「貂蝉」
    cli_aafb5d7cd6389be4（= default，另一个账号）后台名**也是**「貂蝉」
  ⇒ ⇒ **按应用名去后台搜会同时命中两个** ⇒ ⇒ **认错 = 重置错 app 的 secret = 另一个机器人掉线**。
  ⇒ ⇒ ⇒ **app_id 是唯一可靠依据**（后台「凭证与基础信息」页顶部「应用凭证 → App ID」）。

★ ★★ 但「文档里写了警告」拦不住人（本项目铁律：「纪律写进文档 ≠ 行为自动改变」）
  ⇒ 本脚本把「app_id 是唯一可靠依据」变成**机械闸门**：
    ① `--app <标签>`  ⇒ 打印**本轮该找的 App ID**（老大照着这串在后台核对）
    ② `--check <app_id>` ⇒ 老大把**后台页面上看到的 App ID** 粘进来 ⇒ 脚本告诉他这是谁
    ③ `--check <app_id> --app <标签>` ⇒ **不一致就 exit 1** ⇒ 可以直接当闸门

★ 取数纪律（不可放宽）:
    · app_secret 从 openclaw store **只读**、**只在内存里过**、**绝不落盘**、**绝不回显**
    · 只输出：app_id / 后台应用名 / secret 指纹（md5[:12]）⇒ **不出 secret、不出 token**
    · store 用 `mode=ro` 打开（不写、不建 -wal）

★ 「后台真名」怎么来的（与轮换脚本验证 secret 用的是同一条接口）:
    POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal {app_id, app_secret}
    GET  https://open.feishu.cn/open-apis/bot/v3/info   Authorization: Bearer <tenant_access_token>
    ⇒ 响应里 bot.app_name = 后台应用名
    ⇒ ★ 方法可信度自证：两行**既有实测记录**（t_default→貂蝉、helen→Ayn的智能助手）逐字对得上
      ⇒ 另外两行（mumu→木木、default→貂蝉）同法测出 ⇒ 可信。

用法:
    python3 scripts/feishu_app_name.py --all                 # 全表 + 同名冲突警告
    python3 scripts/feishu_app_name.py --app mumu            # 本轮该找的 App ID + 指纹
    python3 scripts/feishu_app_name.py --check cli_xxxx       # 我在后台看到的是这个
    python3 scripts/feishu_app_name.py --check cli_xxxx --app t_default   # ★ 闸门模式
    python3 scripts/feishu_app_name.py --selftest            # 闸门自检（负向对照必须红）

退出码:
    0 = 确认无误
    1 = 有警告/不一致（**闸门效果**：别继续，去后台核对 App ID）
    2 = 用法/环境错误（store 读不到、网络不通等）
"""

import hashlib
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

FEISHU_BASE = "https://open.feishu.cn"
STORE_DB = os.path.expanduser("~/.openclaw/state/openclaw.sqlite")

# ★ 标签 → (app_id, store 条目名, 飞书后台应用名, openclaw 本地别名, 所属飞书账号)
#   第三列 = 2026-10-27 用飞书 API 实测出来的后台真名（B 边 §80.14.1 第九项 + A 边独立复现 4/4）
#   ⚠️ 第四列是 openclaw 侧的**本地别名**，**不是**后台应用名（同名不同物，见 runbook §2）
#   ★ 第五列 = **所属飞书账号**
#     测法：飞书开放平台后台按 **App ID** 搜索（URL 参数 filters 驱动）。
#     ⚠️★★★ 2026-10-28 11:5x–12:0x（B 边 §89.2.1，**第三次更正 §89.2**）**机制定案**：
#        **检索的作用域 = 当前租户**（**不跨租户**），且**本租户自有 app 召得回**（**不漏召**）。
#        ⇒ 「搜不到」⇒ **该 app 不在当前租户** ⇒ **推不出「不存在」，更推不出「在第几个身份」**
#        ⇒ ⇒ **唯一可靠路径 = 切到该账号 → 看它的「企业自建应用」列表 → 核对 App ID**
#        ★ 证据：A 边 §98.2 身份锚定实测 4 格 + B 边同日 4 格 + 1 次复跑（**5/5 与 A 边一致**）
#        ★★ 旧的「跨租户」「会漏召」两条结论**作废** ⇒ 根因 = **身份串位**
#           （把「非当前租户的结果」读成了「跨租户」；把**加载未完成**读成了「漏召」）
#           ⇒ 完整实测表见交接单 §89.2.1（**§89.2 原文一字不删**）与 A 边 §98。
#     ⚠️ ★★ **每格检索前必须先锚定身份**（无筛选的应用列表内容 + 个人中心面板逐字，两路一致）
#        ⇒ 否则 MISS 会被读成否定结论（这正是 §89.2 栽的坑）。
#     ⚠️ ★★ 三个已登录身份的「昵称 / 手机号 / 所有者」是三套不同标签，**别按位置认人**
#        （子菜单顺序随当前账号变）—— 完整对照表见交接单 §86.2 / runbook §2。
APPS = {
    # 标签        app_id                    store 条目                      后台真名        本地别名    所属飞书账号
    "t_default": ("cli_aa420db06a78dcc5", "FEISHU_APPSECRET_T_DEFAULT", "貂蝉", "租户Bot",
                  "tuz / +86157******92（同一租户，tuz 是所有者；Ayn 租户下搜其 app_id 不召回 ⇒ 不在 Ayn 租户）"),
    "mumu":      ("cli_aa51b1b62a7a9bf6", "FEISHU_APPSECRET_MUMU", "木木", "木木",
                  "⚠️ 在已登录的 3 个租户(Ayn 2个/tuz 7个/悦己 1个)下 app_id 与应用名**均未召回** ⇒ 检索作用域=当前租户 ⇒ **可判定不在已登录的 3 个租户** ⇒ 归属未确证（疑在第 4 个身份，须在其应用列表里按 App ID 核对）"),
    "default":   ("cli_aafb5d7cd6389be4", "FEISHU_APPSECRET_MAIN", "貂蝉", "貂蝉",
                  "⚠️ 在已登录的 3 个租户(Ayn 2个/tuz 7个/悦己 1个)下 app_id 与应用名**均未召回** ⇒ 检索作用域=当前租户 ⇒ **可判定不在已登录的 3 个租户** ⇒ 归属未确证（疑在第 4 个身份，须在其应用列表里按 App ID 核对）"),
    "helen":     ("cli_aaf9b98563b99bd7", "FEISHU_APPSECRET_HELEN", "Ayn的智能助手", "Helen",
                  "Ayn（= +86190******76，所有者 Ayn；2026-10-28 实测：Ayn 身份下 app_id 与全名均可召回 ⇒ 本租户召得回）"),
}
# app_id → 标签（反查）
BY_APP_ID = {v[0]: k for k, v in APPS.items()}


def _read_secret(entry_name):
    """从 openclaw store 只读取 secret（内存，不落盘）。读不到返回 None。"""
    if not os.path.isfile(STORE_DB):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % STORE_DB, uri=True)
    try:
        row = con.execute(
            "SELECT value FROM secret_store_entries WHERE name=? AND deleted_at_ms IS NULL",
            (entry_name,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        con.close()
    return row[0] if row else None


def _post_json(path, payload, timeout=20):
    req = urllib.request.Request(
        FEISHU_BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(path, token, timeout=20):
    req = urllib.request.Request(
        FEISHU_BASE + path,
        headers={"Authorization": "Bearer " + token},
        method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_backend_name(app_id, secret, timeout=20):
    """返回 (code, msg, 后台应用名)。网络/接口异常抛异常由调用方兜。"""
    tk = _post_json("/open-apis/auth/v3/tenant_access_token/internal",
                    {"app_id": app_id, "app_secret": secret}, timeout=timeout)
    if tk.get("code") != 0:
        return tk.get("code"), tk.get("msg", ""), None
    info = _get_json("/open-apis/bot/v3/info", tk.get("tenant_access_token", ""),
                     timeout=timeout)
    if info.get("code") != 0:
        return info.get("code"), info.get("msg", ""), None
    return 0, info.get("msg") or "ok", (info.get("bot") or {}).get("app_name")


def fingerprint(secret):
    return hashlib.md5(secret.encode("utf-8")).hexdigest()[:12]


def same_name_collisions(name):
    """返回后台真名与 name 相同、但 app_id 不同的其它标签。"""
    out = []
    for tag, (_aid, _entry, bname, _local, _acct) in APPS.items():
        if bname == name:
            out.append(tag)
    return out


def resolve(app_id):
    """app_id → 标签；未知返回 None。"""
    return BY_APP_ID.get(app_id)


def _fmt_app(tag, secret=None, live_name=None):
    aid, entry, bname, local, acct = APPS[tag]
    print("  标签 %-11s app_id=%-22s store 条目=%s" % (tag, aid, entry))
    print("     后台应用名 = %s%s" % (
        bname, "" if live_name in (None, bname) else "  ⚠️ 实测=%s" % live_name))
    print("     openclaw 本地别名 = %s   ← ★ 这是本地别名，**不是**后台应用名" % local)
    print("     ★ 所属飞书账号 = %s" % acct)
    coll = [t for t in same_name_collisions(bname) if t != tag]
    if coll:
        for t in coll:
            print("     ⚠️⚠️ 同名不同物：后台「%s」**也**是标签 %s（app_id=%s）"
                  % (bname, t, APPS[t][0]))
        print("     ⇒ ★ 按应用名搜会同时命中多个 ⇒ **只认 app_id**")
    if secret:
        print("     现役 secret 指纹 md5[:12] = %s" % fingerprint(secret))
    return coll


def cmd_all(live):
    print("=== 4 个待轮换 app · 飞书后台真名全表 ===")
    for tag in ("t_default", "mumu", "default", "helen"):
        _fmt_app(tag, live.get(tag, {}).get("secret"),
                 live.get(tag, {}).get("name"))
        print()
    print("★ 轮换顺序（老大拍板）：木木 --phase store → 貂蝉(t_default) --phase all"
          " → default / helen --phase store")


def cmd_app(tag, live):
    if tag not in APPS:
        sys.stderr.write("未知标签 '%s'；可用：%s\n" % (tag, ", ".join(APPS)))
        return 2
    print("=== 本轮要轮换的 app：%s ===" % tag)
    _fmt_app(tag, live.get(tag, {}).get("secret"), live.get(tag, {}).get("name"))
    aid = APPS[tag][0]
    print()
    print("★ 老大在飞书后台的操作：")
    print("   1) 切到该 app 所属的飞书账号（实测结论见上面「所属飞书账号」那一行）")
    print("   2) 搜应用名（可能命中多个）⇒ **只认下面这串 App ID**：")
    print("      >>> %s <<<" % aid)
    print("   3) 打开「凭证与基础信息」⇒ 核对页面顶部 App ID 与上面这串**逐字一致**")
    print("   4) 重置 App Secret ⇒ 复制 ⇒ 粘给轮换脚本")
    print("   5) 粘错会被机械拦住：脚本用 app_id 验 secret ⇒ 错 app 的 secret ⇒ exit 5 零写入")
    return 0


def cmd_check(app_id, expect_tag, live):
    tag = resolve(app_id)
    print("=== 你从后台读到的 App ID：%s ===" % app_id)
    if tag is None:
        print("  ❌ 这**不是**本项目 4 个待轮换 app 中的任何一个")
        print("     本项目的 4 个 app_id：")
        for t, (aid, _e, _b, _l, _a) in APPS.items():
            print("       %-11s %s" % (t, aid))
        return 1
    print("  ✅ 命中：标签 %s" % tag)
    _fmt_app(tag, live.get(tag, {}).get("secret"), live.get(tag, {}).get("name"))
    coll = [t for t in same_name_collisions(APPS[tag][2]) if t != tag]
    rc = 0
    if coll:
        print("  ⚠️⚠️ **同名不同物警告**：后台「%s」对应 %d 个 app（%s）"
              % (APPS[tag][2], len(coll) + 1, tag + " / " + " / ".join(coll)))
        print("     ⇒ 你复制的 App ID 必须**逐字**等于上面那串，否则就是选错了 app")
        rc = 1
    if expect_tag:
        want = APPS[expect_tag][0]
        if app_id == want:
            print("  ✅ 与本轮目标（%s）一致 ⇒ 可以继续" % expect_tag)
        else:
            print("  ❌ 与本轮目标（%s）**不一致**" % expect_tag)
            print("     本轮该找的 App ID = %s" % want)
            print("     ⇒ 你复制的是**别的 app** ⇒ **不要重置它** ⇒ 退出码 1")
            rc = 1
    return rc


# ------------------------------------------------------------------ 自检
def selftest():
    """闸门自检：负向对照必须红、正向必须绿。"""
    failures = 0
    cases = [
        ("已知 app_id 能反查标签",
         lambda: resolve("cli_aa420db06a78dcc5") == "t_default", None),
        ("未知 app_id 反查返回 None",
         lambda: resolve("cli_not_exist") is None, None),
        ("★ 同名检测：貂蝉 命中 2 个标签",
         lambda: sorted(same_name_collisions("貂蝉")) == ["default", "t_default"], None),
        ("★ 同名检测：木木 只有 1 个",
         lambda: same_name_collisions("木木") == ["mumu"], None),
        ("指纹是 12 位 hex",
         lambda: len(fingerprint("x" * 32)) == 12, None),
    ]
    print("── 纯逻辑自检 ──")
    for name, fn, _ in cases:
        try:
            ok = bool(fn())
        except Exception as e:
            ok = False
            print("      异常: %s" % e)
        if ok:
            print("  ✅ %s" % name)
        else:
            failures += 1
            print("  ❌ %s" % name)

    print("── 闸门模式自检（--check 与 --app 不一致 ⇒ 必须 exit 1）──")
    rc = cmd_check("cli_aafb5d7cd6389be4", "t_default", {})
    if rc == 1:
        print("  ✅ 错 app_id + 指定目标 ⇒ rc=1（闸门生效）")
    else:
        failures += 1
        print("  ❌ 错 app_id + 指定目标 ⇒ rc=%d（期望 1）" % rc)

    rc = cmd_check("cli_aa420db06a78dcc5", "t_default", {})
    if rc == 1:
        # 貂蝉本身就是同名 app ⇒ 仍应 rc=1（提醒老大「还有另一个貂蝉」）
        print("  ✅ 正确 app_id 但后台同名 ⇒ rc=1（同名提醒仍生效，符合预期）")
    else:
        failures += 1
        print("  ❌ 正确 app_id 但同名冲突 ⇒ rc=%d（期望 1）" % rc)

    rc = cmd_check("cli_aa51b1b62a7a9bf6", "mumu", {})
    if rc == 0:
        print("  ✅ 木木（无同名冲突）⇒ rc=0")
    else:
        failures += 1
        print("  ❌ 木木（无同名冲突）⇒ rc=%d（期望 0）" % rc)

    total = len(cases) + 4
    if failures:
        print("\n⛔ 自检 %d/%d 项失败 ⇒ 闸门本身不可信（退出码 1）" % (failures, total))
        return 1
    print("\n✅ 自检 %d/%d 全过 ⇒ 闸门能红也能绿" % (total, total))
    return 0


def load_live(only=None):
    """实测后台真名 + 现役指纹。失败则降级为「只用表里的静态值」。"""
    live = {}
    for tag, (aid, entry, bname, _local, _acct) in APPS.items():
        if only and tag not in only:
            continue
        sec = _read_secret(entry)
        info = {"secret": sec, "name": None}
        if sec:
            try:
                code, msg, name = fetch_backend_name(aid, sec)
                if code == 0:
                    info["name"] = name
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                pass
        live[tag] = info
    return live


def main(argv):
    args = list(argv)
    if "--selftest" in args:
        args.remove("--selftest")
        if args:
            sys.stderr.write("用法错误：--selftest 不接受其它参数\n")
            return 2
        return selftest()

    app_tag = None
    check_id = None
    want_all = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--app":
            app_tag = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif a == "--check":
            check_id = args[i + 1] if i + 1 < len(args) else ""
            i += 2
        elif a == "--all":
            want_all = True
            i += 1
        else:
            sys.stderr.write("未知参数: %s\n" % a)
            return 2

    if not (want_all or app_tag or check_id):
        want_all = True

    only = set()
    if app_tag:
        only.add(app_tag)
    if check_id:
        t = resolve(check_id)
        if t:
            only.add(t)
    live = load_live(only or None)

    if want_all:
        cmd_all(live)
        return 0
    if app_tag and not check_id:
        return cmd_app(app_tag, live)
    if check_id:
        return cmd_check(check_id, app_tag, live)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
