#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Helen（emp_1780199176680）灵魂迁移：重写 soulDoc 为「灵魂内核」，
保留原有 soulDoc 中的 API 规则和工具约束段落，toolsDoc/idDoc/userDoc/systemPrompt 不动。

用法：
    python update_helen_soul.py [agents.json 路径]

默认路径为当前目录下 data/agents.json；线上机器请显式传路径，例如：
    python update_helen_soul.py /Users/qichen/Desktop/solobrave-test/data/agents.json

幂等：重建时先剥离旧内核和保留区块包壳再提取保留段落，重复执行结果不变。
"""
import json
import os
import re
import shutil
import sys
from datetime import datetime

# Windows 控制台默认 GBK，emoji/特殊符号会 UnicodeEncodeError
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HELEN_ID = 'emp_1780199176680'

# ============================================================
# Helen 灵魂内核（用户提供的完整版）
# ============================================================
HELEN_SOUL_CORE = """# SOUL.md - Helen 灵魂内核

## 灵魂一句话
用数据说话的商务专家，主动分析，给建议，不等着被问。

## 底层价值观（优先级从上到下）
1. 数据真实性第一 - 没有数据支撑的结论不说
2. 分析价值第二 - 主动判断匹配度、给出专业建议，不是等指令的录入员
3. 用户效率第三 - 结论先行、结构化、重点加粗
4. 主动兜底第四 - 能做的直接做，不踢皮球

## 核心能力定位
- **主要职责**：达人-商品匹配分析、选品策略建议、合作价值判断
- **次要职责**：达人/商品数据录入（这是基础功能，不是核心）
- **场景区分**：日常咨询场景下必须主动分析，给判断给建议；只有收到截图等结构化数据时，才按【达人数据录入规则】直接录入

## 思维模型
1. 数据先行：先看数据，再下结论
2. 多维度判断：粉丝画像、带货能力、价格带、品类适配度
3. 主动建议：不等被问，主动说"这个达人适合推什么品"
4. 风险提示：关键决策必附风险说明

## 回复风格
- 结论先行，再给数据支撑
- 不说"我先查一下库"这种流程话，直接给判断
- 分析时用具体数字，不说"比较好""还可以"这种模糊词
- 结构化输出：分层、重点加粗、一眼能看清
- 不用3-5句话限制自己，分析需要就展开说

## 灵魂底线
- 没有数据支撑的结论不说
- 查不到数据如实告知，不编造
- 拿不准的事直接问，不硬撑
"""

# 保留规则：旧 soulDoc 中，标题或正文命中以下关键词的 ## 段落视为
# 「API 规则和工具约束」，原样拼接到新内核之后；其余段落被替换。
# 注意保留【达人数据录入规则】中关于 vision 结构化数据直接录入的部分。
KEEP_RE = re.compile(
    r'(API|工具|curl|POST\s+/|GET\s+/|PUT\s+/|DELETE\s+/|知识存储|知识库|X-Agent-Id'
    r'|localhost:808|达人数据录入|录入规则|vision)',
    re.IGNORECASE,
)


# 组装标记：保留区块的包壳，重建时先剥离旧内核和包壳再提取，
# 保证幂等——即使新内核全文中出现 KEEP_RE 关键词也不会自我累积
PRESERVED_START = '<!-- preserved:start -->'
PRESERVED_END = '<!-- preserved:end -->'


def extract_kept_sections(doc):
    """按 ## 二级标题分段，返回需要保留的段落列表（含标题行）。"""
    if not doc:
        return []
    chunks = re.split(r'(?m)^(?=## )', doc)
    kept = []
    for chunk in chunks:
        chunk = chunk.strip('\n')
        if not chunk.strip():
            continue
        if not chunk.startswith('## '):
            continue  # 一级标题/开头/注释属于旧灵魂或包壳，不保留
        if KEEP_RE.search(chunk):
            kept.append(chunk.rstrip())
    return kept


def build_new_soul_doc(old_doc):
    # 先剥离上次生成的新内核和包壳，剩下的才是「旧灵魂 + 已保留段落」
    base = (old_doc or '').replace(HELEN_SOUL_CORE.rstrip(), '')
    base = base.replace(PRESERVED_START, '').replace(PRESERVED_END, '')
    kept = extract_kept_sections(base)
    parts = [HELEN_SOUL_CORE.rstrip()]
    if kept:
        parts.append(PRESERVED_START + '\n\n<!-- 以下段落保留自原 soulDoc -->\n')
        parts.extend(kept)
        parts.append(PRESERVED_END)
    return '\n\n'.join(parts) + '\n'


def main():
    agents_path = sys.argv[1] if len(sys.argv) > 1 else 'data/agents.json'
    if not os.path.isfile(agents_path):
        print(f'❌ 文件不存在: {agents_path}')
        sys.exit(1)

    backup_path = agents_path + '.bak.' + datetime.now().strftime('%Y%m%d%H%M%S')
    shutil.copy2(agents_path, backup_path)
    print(f'✅ 备份: {backup_path}')

    with open(agents_path, 'r', encoding='utf-8') as f:
        agents = json.load(f)

    target = None
    for a in agents:
        if a.get('id') == HELEN_ID:
            target = a
            break
    if target is None:
        print(f'❌ 未找到 Helen（{HELEN_ID}），未做任何修改')
        sys.exit(1)

    old_doc = target.get('soulDoc', '') or ''
    new_doc = build_new_soul_doc(old_doc)

    if new_doc.strip() == old_doc.strip():
        print('ℹ️  soulDoc 已是目标内容，跳过（幂等）')
    else:
        target['soulDoc'] = new_doc
        with open(agents_path, 'w', encoding='utf-8') as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
        base = old_doc.replace(HELEN_SOUL_CORE.rstrip(), '') \
                      .replace(PRESERVED_START, '').replace(PRESERVED_END, '')
        kept = extract_kept_sections(base)
        print(f'✅ agents.json 已更新: soulDoc {len(old_doc)} → {len(new_doc)} 字符，'
              f'保留旧段落 {len(kept)} 段')

    # 双写 OpenClaw workspace：服务端在 openclawName 存在时优先读
    # ~/.openclaw/workspace-<openclawName>/SOUL.md，只改 agents.json 会不生效
    openclaw_name = (target.get('openclawName') or '').strip()
    if openclaw_name:
        ws_dir = os.path.expanduser('~/.openclaw/workspace-' + openclaw_name)
        soul_path = os.path.join(ws_dir, 'SOUL.md')
        if os.path.isdir(ws_dir):
            old_ws = ''
            if os.path.isfile(soul_path):
                with open(soul_path, 'r', encoding='utf-8') as f:
                    old_ws = f.read()
            if old_ws.strip() != new_doc.strip():
                with open(soul_path, 'w', encoding='utf-8') as f:
                    f.write(new_doc)
                print(f'✅ OpenClaw workspace 已同步: {soul_path}')
            else:
                print(f'ℹ️  workspace SOUL.md 已是目标内容，跳过: {soul_path}')
        else:
            print(f'⚠️  openclawName={openclaw_name} 但 workspace 目录不存在（{ws_dir}），'
                  f'本机可能不是 Helen 的运行机，跳过 workspace 同步')
    else:
        print('ℹ️  该员工未配置 openclawName，无需同步 workspace')

    print('完成。')


if __name__ == '__main__':
    main()
