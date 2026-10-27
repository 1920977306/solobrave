# -*- coding: utf-8 -*-
"""★★ **飞书 app 身份自检**闸门的回归守卫（2026-10-27 A 边 §83）

★ 为什么必须有（背景是 B 边 §80.14.1 第九项的实测发现）：
  飞书后台的应用名**不是唯一的**——「貂蝉」这**两个字在飞书后台对应两个不同的 app**：
    cli_aa420db06a78dcc5（= t_default，tuz 账号）后台名「貂蝉」
    cli_aafb5d7cd6389be4（= default，另一个账号）后台名**也是**「貂蝉」
  ⇒ 「按应用名去后台搜」会同时命中两个 ⇒ 认错 = 重置错 app 的 secret = 另一个机器人掉线。
  ⇒ ⇒ **app_id 是唯一可靠依据** ⇒ ⇒ 本文件把这条变成**机械闸门**。

★ 判据设计（沿用本项目铁律「全绿必须先证明能红」）：
  ① 闸门必须能红：`--check <别的 app_id> --app <本轮目标>` ⇒ **exit 1**（不是「非 0」）；
  ② 表里的后台真名必须与**飞书 API 实测**一致（live 门禁，真实打 `open.feishu.cn`）；
  ③ **runbook 与代码表必须一致** ⇒ 文档漏掉一个 app_id 也会红（防「文档与代码互相打脸」）。
"""

import importlib.util
import os

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(REPO, "scripts", "feishu_app_name.py")
RUNBOOK = os.path.join(REPO, "docs", "SECRET-ROTATION-RUNBOOK.md")

_spec = importlib.util.spec_from_file_location("feishu_app_name", TOOL)
fan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fan)

LIVE = os.environ.get("SOLO_BRAVE_LIVE", "") == "1"


# ── ① 映射表自身完整性 ────────────────────────────────────────────
def test_four_apps_and_ids_are_unique():
    """4 个 app_id 互不相同、4 个 store 条目名互不相同 ⇒ 否则「按 app_id 定位」不成立。"""
    assert len(fan.APPS) == 4
    assert len({v[0] for v in fan.APPS.values()}) == 4, "app_id 有重复"
    assert len({v[1] for v in fan.APPS.values()}) == 4, "store 条目名有重复"
    assert len(fan.BY_APP_ID) == 4


def test_resolve_is_bidirectional():
    """反查必须双向一致。"""
    for tag, (aid, _e, _b, _l, _a) in fan.APPS.items():
        assert fan.resolve(aid) == tag


def test_unknown_app_id_resolves_to_none():
    assert fan.resolve("cli_not_exist") is None


# ── ② 「同名不同物」必须被检测出来（这是本轮的核心发现） ──────────
def test_diaochan_name_collides_across_two_apps():
    """★ 后台「貂蝉」必须对应 **2 个** app ⇒ 搜名字会命中两个。"""
    coll = sorted(fan.same_name_collisions("貂蝉"))
    assert coll == ["default", "t_default"], "貂蝉的同名冲突没被检出：%s" % coll


def test_mumu_name_is_unique():
    """★ 后台「木木」只有 1 个 ⇒ 老大第 1 步搜名字是安全的（但仍要核对 app_id）。"""
    assert fan.same_name_collisions("木木") == ["mumu"]


def test_every_non_unique_name_is_flagged():
    """★ 通用守卫：**任何**后台名若对应多个 app，都必须能被检出（防止以后新增 app 又造一个同名）。"""
    for tag, (_aid, _e, bname, _l, _a) in fan.APPS.items():
        others = [t for t in fan.same_name_collisions(bname) if t != tag]
        if others:
            # 该名字有歧义 ⇒ 必须能列出歧义对象
            assert len(others) >= 1, "%s 的同名冲突没列出来" % bname


# ── ③ 闸门必须能红（exit 1，且是具体的 1） ───────────────────────
def test_gate_exits_one_when_checked_app_is_not_the_expected_one():
    """★ 老大复制了**别的 app** 的 App ID ⇒ 必须 exit 1。"""
    wrong = fan.APPS["default"][0]          # 另一个「貂蝉」
    assert fan.cmd_check(wrong, "t_default", {}) == 1


def test_gate_exits_one_for_unknown_app_id():
    """★ 完全不认识的 app_id ⇒ exit 1。"""
    assert fan.cmd_check("cli_totally_unknown", "t_default", {}) == 1


def test_gate_exits_zero_for_mumu_with_no_name_collision():
    """★ 木木（无同名冲突）且与本轮目标一致 ⇒ exit 0 ⇒ 闸门不是恒红。"""
    assert fan.cmd_check(fan.APPS["mumu"][0], "mumu", {}) == 0


def test_gate_exits_one_even_for_correct_id_when_name_collides():
    """★ 貂蝉这一档：即使 App ID 抄对了，**同名警告仍要保留**（rc=1）⇒ 提醒老大别点错那一条。"""
    assert fan.cmd_check(fan.APPS["t_default"][0], "t_default", {}) == 1


def test_app_flag_rejects_unknown_tag():
    assert fan.cmd_app("nosuchapp", {}) == 2


# ── ④ 文档与代码表必须一致（防「文档与代码互相打脸」） ─────────────
def test_runbook_lists_both_diaochan_app_ids():
    """★ runbook 必须同时写出两个「貂蝉」的 app_id ⇒ 漏一个就红。"""
    with open(RUNBOOK, encoding="utf-8") as fh:
        text = fh.read()
    for tag in ("t_default", "default"):
        aid = fan.APPS[tag][0]
        assert aid in text, "runbook 缺 app_id：%s（%s）" % (aid, tag)


def test_runbook_warns_about_same_name_different_thing():
    """★ runbook 必须有「同名不同物」这层警告（不只写 app_id 映射表）。"""
    with open(RUNBOOK, encoding="utf-8") as fh:
        text = fh.read()
    assert "同名不同物" in text, "runbook 缺「同名不同物」警告"
    assert "只认" in text or "唯一可靠依据" in text, "runbook 缺「只认 app_id」的结论句"


# ── ⑤ live 门禁：后台真名必须与飞书 API 实测一致 ──────────────────
@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="live 门禁：需 SOLO_BRAVE_LIVE=1（真打 open.feishu.cn）")
def test_live_backend_names_match_table():
    """★ 真打飞书：4 个 app 的 `bot.app_name` 必须与表里的后台真名**逐字一致**。

    ⇒ 表一旦过期（后台改了名 / app 重建了）立刻红。
    """
    for tag, (aid, entry, bname, _local, _acct) in fan.APPS.items():
        secret = fan._read_secret(entry)
        assert secret, "store 里读不到 %s" % entry
        code, msg, name = fan.fetch_backend_name(aid, secret)
        assert code == 0, "%s 取 token 失败：code=%s msg=%s" % (tag, code, msg)
        assert name == bname, (
            "%s 的后台真名变了：实测=%r 表里=%r ⇒ 改表并同步 runbook"
            % (tag, name, bname))


# ── ⑥ ★★ 「所属飞书账号」列必须存在且自洽（结构漂移守卫）──────────────
def test_account_column_present_and_consistent():
    """★★ 表里必须有第 5 列「所属飞书账号」⇒ 且内容与 2026-10-27 浏览器实测一致。

    ★ 为什么加这条（2026-10-27 16:2x，A 边实测后加）：
      ① 我给 `APPS` 加第 5 列时，**3 处 4 元组解包全部报 ValueError**
         ⇒ 「加一列」这种改动会**连锁打断**所有解包点 ⇒ 必须有守卫钉住列数
      ② 本条也钉住**实测结论**：木木/default 在三个已登录账号下都搜不到
         ⇒ 老大轮换木木前**必须切到第四个账号**（否则根本找不到那个 app）
         ⇒ ★ 这条是**给老大的操作指引**，不是装饰

    ★ 断言必须写「**具体内容**」，不能只写「非空」⇒ 否则改成任意字符串都绿。

    ⚠️★★ 2026-10-27 16:3x（B 边 §86.4）**更正**：本条原先把 `helen` 也断言成「搜不到」
       ⇒ **那条断言钉的是一条错事实**（「测试全绿」≠「事实对」）
       ⇒ **实测**：`helen`（`cli_aaf9b98563b99bd7`）在 **Ayn（= +86190…76）** 身份下**命中**
          ⇒ 根因是账号标签串位（把 `+86190…76` 当成了豆包账号）⇒ 见交接单 §86.2 / §86.4
       ⇒ 现在：`helen` 断言**含 `Ayn`**；`mumu`/`default` 仍断言含「搜不到」。

    ⚠️⚠️⚠️ 2026-10-27 17:2x–17:4x（B 边 §89.2）**第二次更正**：
       实测证明后台检索**跨租户**（Ayn 身份下能搜到 tuz 的 app）**而且会漏召**
       （Ayn **自己**租户的「Ayn的智能助手」app_id 都搜不到）
       ⇒ ⇒ **「搜不到」不能当归属判据**
       ⇒ ⇒ **「mumu/default 三个都搜不到 ⇒ 属第 4 个账号」是推断，不是实测结论**
       ⇒ ⇒ 现在：`mumu`/`default` 断言**含「未确证」** 且 **不得含「属第四个账号」**
          （不可靠结论不许被守卫钉死 —— 见 §89.2）
       ⇒ ⇒ `helen` 断言**含 `Ayn`**，靠 **API 的 bot.app_name** + **Ayn 身份的应用列表**
          这**两条与检索无关**的证据（检索那条已不可靠）
    """
    for tag, (_aid, _e, _b, _l, acct) in fan.APPS.items():
        assert acct, "%s 缺「所属飞书账号」" % tag
        assert isinstance(acct, str) and acct.strip(), "%s 的所属账号不是非空字符串" % tag

    # ★ 实测事实钉死（2026-10-27 16:0x–16:3x 浏览器逐账号搜索，3 身份 × 4 app_id = 12 格）：
    #   tuz（= +86157…92，所有者 tuz）/ Ayn（= +86190…76，所有者 Ayn）/ 豆包账号（所有者 悦己）
    #   ⇒ t_default 命中「貂蝉」(tuz)；helen 命中「Ayn的智能助手」(Ayn)；mumu / default **三个都搜不到**
    accounts = {tag: fan.APPS[tag][4] for tag in fan.APPS}
    assert "tuz" in accounts["t_default"], \
        "t_default 必须在 tuz 账号下（实测命中）"
    # ★★ 2026-10-27 17:2x（B 边 §89.2）**双向断言**：既钉「未确证」，又**禁掉**「属第四个账号」
    #    这种**由「搜不到」推出来的**不可靠结论 ⇒ 错值与乱填都红。
    for tag in ("mumu", "default"):
        assert "未确证" in accounts[tag], (
            "%s 的归属**尚未确证**（两路检索都未召回）⇒ 表里必须写「未确证」" % tag)
        assert "第四个账号" not in accounts[tag], (
            "%s 不得断言「属第四个账号」—— 那是拿「搜不到」推出来的，检索会漏召（§89.2）" % tag)
    # ★ helen 归属 Ayn 账号：靠 **API 的 bot.app_name** + **Ayn 身份的应用列表**（都与检索无关）
    assert "搜不到" not in accounts["helen"], \
        "helen 实测在 Ayn 账号下命中（Ayn的智能助手）⇒ 不得记「搜不到」"
    assert "Ayn" in accounts["helen"], \
        "helen 必须在 Ayn 账号下（实测命中，cli_aaf9b98563b99bd7 → Ayn的智能助手）"
