"""★ 转化率必须**保持 NULL 当缺失维度**（2026-10-26 老大拍板）的护栏。

背景（为什么这是「不许有真值」的维度）：
  平台**没有**通用商品转化率接口 ⇒ 我们的库里从来没有过转化率的**真值**。
  但代码里到处在**编造**它：
    - 示例种子写死 `conversion_rate: 3.5`（products）
    - 示例种子写死 `[3.2, 2.8, 4.1, 3.5]`（product_talent_match）
    - 导入时缺字段落库成 `0`（= 一个编造的数字，不是「没采到」）
    - 商品分析 prompt 写死「转化率：0%」⇒ **编造的数进了 LLM 的输入**
      ⇒ 模型会拿它当事实推理（「转化率 0% 说明没人买」）⇒ 结论被带偏。

★ 危险在哪（为什么这不是小事）：
  评分器有一条「池内覆盖率 ≥ 60% 才启用该维度」的闸门。
  只要**编造的**转化率凑够 60% 覆盖，它就会**当成真值参与打分** ——
  假数直接变成推荐分数。所以「把假值留着」= 留一颗定时炸弹。

本文件锁四件事：
  ① prompt 不许出现编造的「转化率：0%」（有真值才写）
  ② 导入/读出路径不许把「没有」变成 0
  ③ 代码里**不许**再出现写死的转化率数字（AST 扫描，不是正则）
  ④ 评分器把 NULL 当**缺失维度**（退出加权），不是当 0 分
"""
import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_server():
    spec = importlib.util.spec_from_file_location('srv_conv', ROOT / 'solobrave-server.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()


# ─────────────── ① prompt：没真值就不写这一行 ───────────────

def test_prompt_omits_conversion_rate_when_absent():
    """没有转化率 ⇒ prompt 里**整行不出现**「转化率」。"""
    prompt = srv._product_analysis_prompt({'name': '测试品', 'price': 99})
    assert '转化率' not in prompt, prompt


def test_prompt_omits_conversion_rate_when_zero():
    """0 也是「没有真值」（平台 0 几乎总是「该字段没数据」）⇒ 同样不写。"""
    prompt = srv._product_analysis_prompt({'name': '测试品', 'conversion_rate': 0})
    assert '转化率' not in prompt, prompt
    prompt = srv._product_analysis_prompt({'name': '测试品', 'conversion_rate': 0.0})
    assert '转化率' not in prompt, prompt


def test_prompt_includes_conversion_rate_when_real_value():
    """真有的值**要写**（不能因为修编造就把真值也一起藏了）。"""
    prompt = srv._product_analysis_prompt({'name': '测试品', 'conversion_rate': 3.7})
    assert '转化率：3.7%' in prompt, prompt


# ─────────────── ② 导入/读出：没有 ≠ 0 ───────────────

def test_dict_to_product_row_does_not_fabricate_zero():
    """请求体没带转化率 ⇒ 落库必须是 None，**不是 0**。

    ⚠️ 0 会被读成「转化率 0%」——那是一个**编造的事实**。
    """
    row = srv._dict_to_product_row({'name': '测试品', 'price': 10})
    assert row['conversion_rate'] is None, row['conversion_rate']


def test_dict_to_product_row_keeps_real_value():
    """请求体带了真值 ⇒ 原样保留（别把真的也抹了）。"""
    row = srv._dict_to_product_row({'name': '测试品', 'conversion_rate': 4.2})
    assert row['conversion_rate'] == pytest.approx(4.2)


def test_product_row_to_dict_keeps_null_as_none():
    """库里是 NULL ⇒ 读出来还是 None（别在读出侧又变成 0）。"""
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE products (id TEXT, name TEXT, conversion_rate REAL)")
    conn.execute("INSERT INTO products VALUES ('p1','测试品', NULL)")
    row = conn.execute("SELECT * FROM products WHERE id='p1'").fetchone()
    conn.close()
    assert srv._product_row_to_dict(row)['conversion_rate'] is None


# ─────────────── ③ 代码里不许再写死转化率数字（AST 扫描）───────────────

#: 允许出现「数字字面量」的地方 = 没有任何地方。
#:   转化率只允许来自平台真值 ⇒ 代码里出现数字 = 编造。
_FAKE_KEYS = ('conversion_rate', 'conversionrate')


def _numeric_literal_nodes(value_node):
    """把「可能是数字」的表达式拆开：常量 / 数字列表 / 列表的下标。"""
    if isinstance(value_node, ast.Constant):
        return [value_node] if isinstance(value_node.value, (int, float)) \
            and not isinstance(value_node.value, bool) else []
    if isinstance(value_node, ast.Subscript):
        return _numeric_literal_nodes(value_node.value)
    if isinstance(value_node, (ast.List, ast.Tuple)):
        out = []
        for el in value_node.elts:
            out.extend(_numeric_literal_nodes(el))
        return out
    return []


def _fake_conversion_literals(path: Path):
    """扫出「给 conversion_rate 键赋了数字字面量」的地方。"""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (SyntaxError, UnicodeDecodeError):
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                continue
            if k.value.lower() not in _FAKE_KEYS:
                continue
            for num in _numeric_literal_nodes(v):
                hits.append((path.name, num.lineno, k.value, num.value))
    return hits


def test_no_hardcoded_conversion_rate_numbers_in_code():
    """★ 代码里不许再出现写死的转化率数字（编造源头）。

    ⚠️ 用 **AST** 扫，不���正则：
       正则会漏（多行、变量间接赋值、字符串拼接），
       而且会把「注释里提到这个数字」也当成命中（假红）。
       AST 只认真参与赋值的字面量。
    """
    targets = [ROOT / 'solobrave-server.py']
    targets += sorted((ROOT / 'daren_cockpit').rglob('*.py'))
    targets += sorted((ROOT / 'daren_bot').rglob('*.py'))
    hits = []
    for t in targets:
        hits.extend(_fake_conversion_literals(t))
    assert not hits, (
        '代码里又出现写死的转化率数字（= 编造的平台真值）:\n'
        + '\n'.join(f'  {f}:{ln}  {k} = {v}' for f, ln, k, v in hits))


# ─────────────── ④ 评分器把 NULL 当「缺失维度」而不是 0 分 ───────────────

def test_null_conversion_rate_is_dropped_dimension_not_zero_score():
    """池内转化率全空 ⇒ 该维度**整体退出**（dropped_dims 如实报出来）。"""
    from daren_cockpit.tools import product_reco as pr
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200, conversion_rate=None) for i in range(5)]
    recs = pr.score_products(pool, st)
    assert recs, "池子空了"
    assert '转化率' in recs[0].dropped_dims, recs[0].dropped_dims
    for r in recs:
        assert not any(d.key == 'conversion_rate' for d in r.dims), \
            "转化率进了打分维度（缺失维度被当成 0 分参与加权了）"


def test_fake_conversion_rate_would_activate_the_dimension():
    """★ 反向证明「假值为什么危险」：覆盖率够 ⇒ 该维度**会**被启用并参与打分。

    ⇒ 这条也说明：光靠覆盖率闸门**挡不住**假值 —— 必须从源头不写假值。
    """
    from daren_cockpit.tools import product_reco as pr
    st = pr.build_strategies(pr.build_profile(_talent()))[0]
    pool = [_prod(f"p{i}", f"商品{i}", 200, conversion_rate=3.5) for i in range(5)]
    recs = pr.score_products(pool, st)
    assert '转化率' not in recs[0].dropped_dims, recs[0].dropped_dims
    assert any(d.key == 'conversion_rate' for d in recs[0].dims), \
        "覆盖率够了却没启用转化率维度 —— 闸门逻辑变了"


# ─────────────── 工具 ───────────────

def _talent():
    return {
        'id': 'tal_x', 'name': '测试达人', 'douyin_id': 'dy_1',
        'category_distribution': '{"个护家清": 60, "美妆": 40}',
        'price_distribution': '{"0~25": 100, "50~100": 400, "100~200": 500}',
        'average_price': 65.0, 'video_avg_price': '¥65',
        'total_products': 190, 'total_gmv': 500000, 'created_at': 1,
    }


def _prod(pid, name, price, good_ratio=90.0, conversion_rate=None, **kw):
    p = {
        'id': pid, 'name': name, 'price': price, 'price_range': f'¥{price}',
        'category': '个护家清/面霜', 'commission_rates': '{"default": 20}',
        'shop_score': 92, 'good_ratio': good_ratio, 'tags': '[]',
        'video_sales_7d': 100, 'board_rank': 3, 'influencer_count': 50,
        'conversion_rate': conversion_rate,
    }
    p.update(kw)
    return p
