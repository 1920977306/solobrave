#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""is_admin 逐处打标清单生成器（阶段 1 前置 #1，2026-11-08）

依据 docs/SUBJECTS-PACKS-RECONCILE.md §2#1 / §5：
  [platform] 平台运营面 = 租户管理/权限管理/积分中心/飞书配置/违禁词/审批中心/AI 审计/用量观测
  [tenant]   业务面     = 知识库/达人库/商品库/AI 员工管理/任务/商机/对话知识隔离
  framework  = 主开关定义与通用闸门（拆开关时它们是被引用的根基，不是消费场景）
  comment    = 注释/docstring 中出现的字样（无权限语义）

行号随代码漂移——改代码后重跑本脚本再出清单：
  python3 scripts/label_is_admin_sites.py > docs/IS-ADMIN-LABELS.md
"""
import os
import re
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'solobrave-server.py')

PLATFORM_FNS = {
    # 权限管理
    '_handle_get_permissions', '_handle_update_role_permissions',
    '_handle_update_user_permissions',
    # 组织/用户组/团队
    '_handle_get_user_subordinates', '_check_group_access',
    '_handle_get_groups', '_handle_update_group', '_handle_batch_save_groups',
    '_handle_delete_group', '_match', '_handle_add_group_member',
    '_handle_remove_group_member', '_can_access_team',
    '_handle_get_team', '_handle_update_team', '_handle_delete_team',
    '_handle_add_team_member', '_handle_remove_team_member',
    # 数据管道
    '_handle_post_pipe_start', '_handle_get_pipe_progress',
    '_handle_post_pipe_cancel', '_handle_get_pipe_list',
    # 用量观测
    '_handle_get_llm_usage', '_handle_get_token_usage',
    # 积分中心/计费
    '_handle_credits_admin_grant', '_handle_credit_recharge',
    '_handle_credit_recharge_generic', '_handle_get_feishu_credits',
    '_handle_post_feishu_credits_recharge', '_record_credit_usage',
    # 飞书配置/租户管理
    '_handle_get_feishu_console', '_handle_post_feishu_console_agent_op',
    '_handle_get_feishu_role_content', '_handle_get_feishu_chat',
    '_handle_get_feishu_facts', '_handle_post_feishu_fact_delete',
    '_handle_post_feishu_tenant_create',
    # 审批中心 / AI 调用审计
    '_handle_get_proposals', '_handle_get_proposal',
    '_handle_post_proposal_resolve', '_handle_delete_proposal',
    '_handle_get_agent_calls',
}

FRAMEWORK_LINES = {}  # 行号 → 说明（定义/框架本体，见下）

# 散文行（注释/docstring 内的 is_admin 字样，无权限语义）。
# 行号会随代码漂移——重跑时若 assert 失败，按内容（★ 开头/说明文字）重新定位。
COMMENT_LINES = set()


def _is_prose(ln, fn, txt):
    s = txt.strip()
    if s.startswith('#') or s.startswith('★'):
        return True
    # 已知散文行（注释/docstring 内嵌字样）——行号漂移时更新这里
    if ln in COMMENT_LINES:
        return True
    if fn == '_api_token_auth_result':
        return True          # :2951 一带 scoped token 说明注释
    if fn == '_build_talent_injection_text' and '数据隔离规则' in s:
        return True          # docstring 说明行
    if fn == '_wrap_reply' and s.startswith('#'):
        return True
    return False


def classify(ln, fn, txt):
    s = txt.strip()
    if s.startswith('def is_admin(self):'):
        return 'definition', 'is_admin property 本体（#4 拍板保留：权限菜单计算零件），非业务消费'
    if _is_prose(ln, fn, txt):
        return 'comment', '注释/docstring 字样，无权限语义'
    if fn == '_get_effective_permissions':
        return 'framework', '权限计算核心：hasattr 探测 is_admin，是权限管理的输入'
    if fn == '_require_admin':
        return 'framework', '通用闸门 helper 本体，消费方集中在权限/租户管理'
    if fn in PLATFORM_FNS:
        return 'platform', ''
    return 'tenant', ''


def main():
    lines = open(SRC).read().splitlines()
    defs = []  # (lineno, indent, name)
    for i, line in enumerate(lines, 1):
        m = re.match(r'^(\s*)def ([\w]+)\(', line)
        if m:
            defs.append((i, len(m.group(1)), m.group(2)))

    def enclosing(ln):
        best = None
        for d in defs:
            if d[0] <= ln and d[1] <= 4 and (best is None or d[0] > best[0]):
                best = d
        return best[2] if best else '?'

    sites = []
    for i, line in enumerate(lines, 1):
        if 'is_admin' not in line:
            continue
        bare = line.replace('is_platform_admin', '').replace('is_tenant_admin', '')
        if not re.search(r'(?<!platform)(?<!tenant)\bis_admin\b', bare):
            continue
        fn = enclosing(i)
        # 字面量 is_admin=True/False：调用 ks 层时声明「系统级操作」，非身份消费
        if re.search(r'\bis_admin\s*=\s*(?:True|False)\b', line) and 'auth.is_admin' not in line:
            label, note = 'literal', '字面量参数（ks 层系统级操作语义），非身份消费'
        else:
            label, note = classify(i, fn, line)
        sites.append((i, fn, line.strip()[:100], label, note))

    cnt = Counter(s[3] for s in sites)
    consumers = cnt['platform'] + cnt['tenant']

    print('# is_admin 逐处打标清单（阶段 1 前置 #1）')
    print()
    print('> 生成：`python3 scripts/label_is_admin_sites.py > docs/IS-ADMIN-LABELS.md`')
    print('> 口径：docs/SUBJECTS-PACKS-RECONCILE.md §2#1 / §5。改代码后行号会漂，重跑本脚本。')
    print(f'> 本次扫描：**{consumers} 处消费** = [platform] {cnt.get("platform", 0)} + [tenant] '
          f'{cnt.get("tenant", 0)}；另有 framework {cnt.get("framework", 0)}、definition 1、'
          f'comment {cnt.get("comment", 0)}、literal {cnt.get("literal", 0)}。')
    print()
    print('**拆主开关顺序（§2#1 红线）：先按本清单把 [platform] 消费切到 super_admin，'
          '[tenant] 消费保留 is_admin 语义（后续归 tenant_owner），每切一批跑全量回归。**')
    print()
    print('| 行号 | 所在函数 | 标签 | 代码 | 备注 |')
    print('|---|---|---|---|---|')
    for ln, fn, txt, label, note in sites:
        print(f'| :{ln} | `{fn}` | **[{label}]** | `{txt}` | {note} |')
    print()
    print('## 按域汇总')
    print()
    groups = {}
    for ln, fn, txt, label, note in sites:
        if label in ('platform', 'tenant'):
            groups.setdefault(label, []).append((ln, fn))
    for label in ('platform', 'tenant'):
        if label not in groups:
            print(f'### [{label}] 0 处（已清零 ✅）')
            print()
            continue
        fns = Counter(fn for _, fn in groups[label])
        print(f'### [{label}] {len(groups[label])} 处 / {len(fns)} 个函数')
        print()
        for fn, c in fns.most_common():
            print(f'- `{fn}` × {c}')
        print()


if __name__ == '__main__':
    main()
