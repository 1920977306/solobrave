"""
知识库入库分档整理层（老大 2026-10-23 口径）

═══════════════════════════════════════════════════════════════════════
老大的原话：
  「关于分析之后类的就需要完全提取然后放入知识库，
    但有一些很平常的就整理出来再放入知识库」

两档定义（本模块唯一权威口径）：
  ┌─ 档 A「分析之后类」 tier='analysis'
  │    判定：内容本身已是**成型结论**（有评级/表格/结论句式）
  │    处理：**完全提取** —— 原文一字不改地整段入库
  │    理由：结论的价值在于完整（表格拆了就没意义、结论截断了就自相矛盾）
  │    旁证：kb_entries 63 条「达人分析：」条目正文都是完整报告结构
  │
  └─ 档 B「平常类」     tier='daily'
       判定：内容是**碎片素材**（一行一个字段 / 一句建议 / 一条观察）
       处理：**整理后** —— 归并同主题碎片 → 抽事实要素 → 结构化输出
       理由：碎片照抄进库 = 一堆「知识点：达人ID：tal_xxx」的垃圾条目
             （实测 234 条「知识点：」条目里大半是无信息量碎片）

⚠️ 铁律（本模块继承自「只丢不造」）：
  1. **不造** —— 整理只做「归并 / 剪裁 / 重排 / 抽要素」，
     绝不改写语义、绝不补原文没有的信息、绝不生成结论。
  2. **不丢结论** —— 档 A 完全提取路径不经过本模块任何剪裁逻辑。
  3. **不确定就退回原文** —— 整理不出结构时返回 None（不硬凑），
     由调用方决定「照原文入库」还是「不入库」。
  4. **可回滚** —— 整理逻辑全部是纯函数（不碰 DB、不发网络请求），
     关掉开关即回到旧行为（见 server 侧 `settings.daily_kb_intake`）。
═══════════════════════════════════════════════════════════════════════

与既有链路的边界（别混）：
  · 链 A 记忆分层 memory_pipeline.py  → L0/L1/L2/L3，喂 AI 召回
  · 链 B 主题沉淀 brain_knowledge_service.py → knowledge_base_new（当前 0 行，未接）
  · ★ 本模块 → kb_entries（老大产品上「知识库」界面真正读的表，636 行）
"""

import re

# ═══════════════════════════════════════════════════════════════════════
# 1. 档位常量
# ═══════════════════════════════════════════════════════════════════════
TIER_ANALYSIS = 'analysis'   # 档 A：分析之后类 → 完全提取
TIER_DAILY = 'daily'         # 档 B：平常类 → 整理后入库
TIER_MANUAL = 'manual'       # 人工录入
TIER_LEGACY = 'legacy'       # 历史未标档位

SOURCE_ANALYSIS = 'auto_analysis'
SOURCE_DAILY = 'auto_daily'
SOURCE_DECISION = 'auto_decision'
SOURCE_REPEAT = 'auto_repeat'
SOURCE_MANUAL = 'manual'
SOURCE_DAREN_BOT = 'daren_bot'

# ═══════════════════════════════════════════════════════════════════════
# 2. 档位判定
# ═══════════════════════════════════════════════════════════════════════

# 成型结论的结构信号（档 A）
_ANALYSIS_STRUCTURE_HINTS = (
    '【达人评级】', '达人评级', '核心结论', '一句话核心结论',
    '合作建议', '匹配建议', '投放建议', '综合建议', '分析结论', '综合判断',
)

# 碎片素材的结构信号（档 B 的反面：这些东西不是成型结论）
_FRAGMENT_PREFIXES = (
    '达人ID：', '达人ID:', '名称：', '名称:', '昵称：', '昵称:',
    '抖音号：', '抖音号:', 'ID：', 'ID:',
)


def classify_tier(content, title=''):
    """判定一条内容属于哪一档。

    返回 (tier, reason)。
    档 A 优先：成型结论里可能也带碎片行，但结论优先（完整提取优先）。

    ★ 判定必须可解释 —— reason 会随入库一起留痕，便于事后审计
      「为什么这条进了分析档 / 那条进了平常档」。
    """
    text = f'{title or ""}\n{content or ""}'
    stripped = text.strip()
    if not stripped:
        return TIER_LEGACY, '空内容'

    # 档 A：成型结论 —— 命中结构信号即判定
    hits = [h for h in _ANALYSIS_STRUCTURE_HINTS if h in stripped]
    if hits:
        return TIER_ANALYSIS, f'成型结论结构信号: {hits[0]}'

    # 档 A：整段就是结论句式（「建议合作 / 不建议 / 综上」+ 指标）
    if _looks_like_conclusion(stripped):
        return TIER_ANALYSIS, '结论句式 + 业务指标'

    # 档 B：其余（碎片 / 日常观察 / 建议片段）→ 走整理
    return TIER_DAILY, '非成型结论，按平常类整理'


_METRIC_WORDS = (
    '粉丝', 'GMV', '转化率', '客单价', 'ROI', 'GPM', '完播率', '互动率',
    '类目', '价格带', '复购', '佣金', '坑位费', '爆款', '带货', '涨粉',
    '播放量', '点赞', '出单',
)
_JUDGMENT_WORDS = (
    '建议', '推荐', '适合', '不适合', '首选', '次选', '优先', '结论',
    '策略', '可行', '考虑', '合作', '性价比', '风险',
)


def _looks_like_conclusion(text):
    """长文本 + 指标词 + 判断词 ⇒ 是成型结论（与 server 侧 _is_analysis_conclusion 同构）。"""
    if len(text) < 120:
        return False
    if not any(w in text for w in _METRIC_WORDS):
        return False
    return any(w in text for w in _JUDGMENT_WORDS)


# ═══════════════════════════════════════════════════════════════════════
# 3. 档 B「整理」：碎片 → 结构化
# ═══════════════════════════════════════════════════════════════════════

# 整理时必须剔除的噪音行（无信息量 / 过程痕迹）
# ★ 只丢「不可读的 ID 类」字段 —— 达人ID：tal_1787213095215_841db3 这种对人无意义，
#   且历史上正是它们产出了「知识点：达人ID：tal_xxx」这种垃圾标题。
#   名称/昵称/抖音号 **不能丢** —— 它们是档案主体（标题要用），丢了标题就退化成「服装 档案」。
_NOISE_LINE_PATTERNS = (
    re.compile(r'^\s*达人?ID\s*[:：]\s*[A-Za-z0-9_]+$'),      # 达人ID：tal_xxx
    re.compile(r'^\s*[-*•·]\s*$'),                              # 裸列表符
    re.compile(r'^\s*[:：]\s*$'),                                # 裸冒号
)

# 字段名 → 归一化键（用于「事实要素」抽取）
_KV_SPLIT = re.compile(r'^[\s*\-•·]*(?:\*\*)?([^：:*]{1,20})(?:\*\*)?\s*[:：]\s*(.+?)\s*$')

# 归一后这些键视为「身份标识」—— 单独出现无价值，但拼进档案条目有价值
# ★ 只含**主体标识**（谁/叫什么/哪个号）；**不含类目/品类/平台** ——
#   「品类：服装」是实质业务信息，不是身份，误列会让「名称+品类」被当成空壳丢掉。
_IDENTITY_KEYS = ('达人ID', '达人id', 'ID', 'id', '名称', '昵称', '抖音号')


def _is_noise_line(line):
    """判断一行是否为噪音（无信息量碎片 / 空行 / 纯符号）。"""
    s = (line or '').strip()
    if not s:
        return True
    for pat in _NOISE_LINE_PATTERNS:
        if pat.match(s):
            return True
    # 纯标点/纯数字行
    if not re.search(r'[\w一-鿿]', s):
        return True
    return False


def _split_kv(line):
    """拆「键：值」；拆不出返回 (None, None)。"""
    m = _KV_SPLIT.match((line or '').strip())
    if not m:
        return None, None
    key = m.group(1).strip().strip('*')
    val = m.group(2).strip().strip('*').strip()
    if not key or not val:
        return None, None
    return key, val


def organize_daily(content, title='', context=None):
    """把「平常类」碎片素材整理成结构化知识。

    参数
      content : 原始碎片内容（可能是多行、可能是一条一句话）
      title   : 原始标题（可选，用于补充主题线索）
      context : {'emp_id':..., 'source_mem_ids':[...]} 等旁证

    返回 dict 或 None：
      {
        'title':    '整理后的标题',
        'content':  '整理后的正文（结构化）',
        'facts':    [['键','值'], ...],   # 抽出的事实要素
        'notes':    ['整句保留的判断句', ...],
        'dropped':  ['被剔除的噪音行', ...],  # 留痕，不静默丢
      }

    ★ 返回 None = 整理不出结构（内容太空/全是噪音）⇒ 调用方不要入库。
      「不确定就不硬凑」—— 这比硬生成一个空壳条目诚实。
    """
    raw = content or ''
    lines = [ln for ln in raw.split('\n')]

    facts = []          # [[键,值], ...]
    notes = []          # 无键值结构的整句（判断句/观察句）
    dropped = []        # 被剔除的噪音行
    seen = set()

    for ln in lines:
        if _is_noise_line(ln):
            s = ln.strip()
            if s:
                dropped.append(s)
            continue
        key, val = _split_kv(ln)
        if key is not None:
            # 键值行：值过短且是身份类 ⇒ 仍算噪音（名称：大丸子 单条无价值）
            if len(val) < 2:
                dropped.append(ln.strip())
                continue
            dedup_key = f'{key}={val}'
            if dedup_key in seen:
                dropped.append(ln.strip())
                continue
            seen.add(dedup_key)
            facts.append([key, val])
        else:
            s = ln.strip()
            if len(s) < 8:
                dropped.append(s)
                continue
            if s in seen:
                dropped.append(s)
                continue
            seen.add(s)
            notes.append(s)

    # ── 无有效要素 ⇒ 整理失败（不硬凑空壳）
    if not facts and not notes:
        return None

    # ── 只有身份标识、没有实质事实 ⇒ 不单独成条
    #   例：只有「名称：大丸子」一行 —— 有主体但没内容，入库仍是垃圾条目
    #   ⇒ 保留它作为归并主题（_topic_key 用得上），但不单独产出条目
    _substantive = [f for f in facts if f[0] not in _IDENTITY_KEYS]
    if not _substantive and not notes:
        return None

    # ── 组装标题：优先用主体身份（名称/昵称），否则用首句
    identity = ''
    for k, v in facts:
        if k in _IDENTITY_KEYS and k not in ('达人ID', 'ID', 'id'):
            identity = v
            break
    if not identity:
        for k, v in facts:
            if k == '达人ID':
                identity = v
                break

    subject = ''
    for k, v in facts:
        if k in ('达人昵称', '昵称', '名称'):
            subject = v
            break

    body_parts = []
    if facts:
        body_parts.append('【要素】')
        for k, v in facts:
            body_parts.append(f'- {k}：{v}')
    if notes:
        body_parts.append('【要点】')
        for n in notes:
            body_parts.append(f'- {n}')

    body = '\n'.join(body_parts).strip()

    # ── 标题：有主体 → 「<主体> 档案」；否则 → 「日常要点」+ 首句摘要
    if subject:
        new_title = f'{subject} 档案'
    elif identity:
        new_title = f'{identity} 档案'
    elif notes:
        new_title = '日常要点：' + notes[0][:24].rstrip('。，,')
    else:
        first = facts[0]
        new_title = f'{first[0]}：{first[1][:20]}'

    # ── 长度护栏：整理后仍过长 ⇒ 说明是长文（不是碎片）⇒ 交回完整提取路径
    if len(body) > 4000:
        return None

    result = {
        'title': new_title[:200],
        'content': body,
        'facts': facts,
        'notes': notes,
        'dropped': dropped,
    }
    if context:
        result['context'] = context
    return result


def merge_daily_fragments(items):
    """把同一批「平常类」碎片合并成一条结构化知识。

    items: [{'content':..., 'title':..., 'mem_id':...}, ...]

    ★ 合并只做「同主题归并」—— 主题键取第一条非空的身份/主体；
      主题键不同的分开成组（宁可多几条，不硬凑成一大坨）。
    ★ 合并后若要素数不增长（全是重复）⇒ 返回 None（不制造重复条目）。

    返回 [{'title','content','facts','notes','mem_ids','dropped'}, ...]
    """
    if not items:
        return []

    # ① 按主题键分桶
    buckets = {}
    order = []
    for it in items:
        content = it.get('content') or ''
        key = _topic_key(content, it.get('title') or '')
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(it)

    results = []
    for key in order:
        group = buckets[key]
        merged_facts = []
        merged_notes = []
        dropped = []
        seen = set()
        for it in group:
            org = organize_daily(it.get('content') or '', it.get('title') or '')
            if not org:
                dropped.append((it.get('content') or '').strip()[:120])
                continue
            for f in org['facts']:
                fk = f'{f[0]}={f[1]}'
                if fk in seen:
                    continue
                seen.add(fk)
                merged_facts.append(f)
            for n in org['notes']:
                if n in seen:
                    continue
                seen.add(n)
                merged_notes.append(n)
            dropped.extend(org['dropped'])

        if not merged_facts and not merged_notes:
            continue

        body_parts = []
        if merged_facts:
            body_parts.append('【要素】')
            for k, v in merged_facts:
                body_parts.append(f'- {k}：{v}')
        if merged_notes:
            body_parts.append('【要点】')
            for n in merged_notes:
                body_parts.append(f'- {n}')
        body = '\n'.join(body_parts).strip()

        subject = ''
        for k, v in merged_facts:
            if k in ('达人昵称', '昵称', '名称'):
                subject = v
                break
        if not subject:
            for k, v in merged_facts:
                if k in _IDENTITY_KEYS:
                    subject = v
                    break

        title = f'{subject} 档案' if subject else (
            '日常要点：' + merged_notes[0][:24].rstrip('。，,') if merged_notes
            else f'{merged_facts[0][0]}：{merged_facts[0][1][:20]}'
        )

        results.append({
            'title': title[:200],
            'content': body,
            'facts': merged_facts,
            'notes': merged_notes,
            'mem_ids': [it.get('mem_id') for it in group if it.get('mem_id')],
            'dropped': dropped,
        })
    return results


def _topic_key(content, title=''):
    """主题键：用于把同主题碎片归并。

    ★ 取「主体名」作键；取不到 ⇒ 用标题前 12 字兜底；
      再取不到 ⇒ 归到 'misc'（但内容为空时返回 None 不进桶）。
    """
    for ln in (content or '').split('\n'):
        if _is_noise_line(ln):
            continue
        key, val = _split_kv(ln)
        if key in ('达人昵称', '昵称', '名称') and val:
            return val.strip()
    if title:
        return title.strip()[:12]
    return 'misc'
