"""2026-11-05 达人视频拆解链路核查发现的两处缺陷的回归（Kimi Work 核查边交付）。

核查证据（生产库 talent_videos，4/4 样本命中）：
1. `visual_desc` 混入 MiniMax-M3 的 <think>…</think> 思考块——字段 3000 字符里
   大半是推理噪音，真正画面描述只有末尾约 250 字。
2. 排序只落库 TOP N：6 候选只存 2 行，其余候选指标无任何留存，
   加权排序事后无法全池重算核对。

修复（buyin_worker.py）：
- `_strip_think` 纯函数 + `_vision_chat` 唯一出口统一剥离（列表/直链两条
  拆解管线共用，不用各改一处）。
- `_teardown_rank_audit_lines` 纯函数 + 排序前打全池审计行（标题+观看/赞/评/
  结算+总分），让 TOP N 永远可审计。

守门要点（与 §0.1/§16 同思路）：
- 剥 think 是**行为守卫**：monkeypatch 掉 urlopen，喂带 think 块的假响应，
  断言 `_vision_chat` 返回不含思考块——不是只查符号在不在。
- 审计行必须**带上每候选的真实指标与分数**，只断言「有日志行」等于没守。
"""
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

buyin_worker = importlib.import_module("buyin_worker")


def row(plays='', likes='', comments='', gmv='', title='', **kw):
    r = {'index': 0, 'title': title, 'cover': '', 'plays': plays, 'interact': '',
         'gmv': gmv, 'gpm': '', 'likes': likes, 'comments': comments,
         'shares': '', 'cells': []}
    r.update(kw)
    return r


# ── 1. _strip_think ───────────────────────────────────────────────────────

def test_strip_think_removes_block():
    raw = '<think>先想想……\n想完了</think>最终画面描述：灰绿拼色中筒靴。'
    assert buyin_worker._strip_think(raw) == '最终画面描述：灰绿拼色中筒靴。'


def test_strip_think_multiline_block():
    raw = '<think>\n第1帧……\n第2帧……\n</think>\n答案'
    assert buyin_worker._strip_think(raw) == '答案'


def test_strip_think_no_block_unchanged():
    assert buyin_worker._strip_think('普通回答') == '普通回答'


def test_strip_think_only_block_returns_empty():
    assert buyin_worker._strip_think('<think>只有思考</think>') == ''
    assert buyin_worker._strip_think('') == ''
    assert buyin_worker._strip_think(None) == ''


def test_strip_think_block_not_at_start():
    raw = '前缀<think>中途插入的思考</think>后缀'
    assert buyin_worker._strip_think(raw) == '前缀后缀'


# ── 2. _vision_chat 唯一出口：行为守卫（喂假响应，断言返回被剥净）────────

def test_vision_chat_returns_stripped_content(monkeypatch):
    think_blob = '<think>We need answer Chinese <=300 chars……（省略推理）' \
                 '</think>1）前 3 秒为低机位钩子。'
    payload = json.dumps(
        {'choices': [{'message': {'content': think_blob}}]}).encode('utf-8')

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return payload

    monkeypatch.setattr(buyin_worker.urllib.request, 'urlopen',
                        lambda *a, **kw: FakeResp())
    out = buyin_worker._vision_chat('https://x/v1', 'k', 'm', 'p', [], 0.0)
    assert '<think>' not in out
    assert out == '1）前 3 秒为低机位钩子。'


# ── 3. _teardown_rank_audit_lines：全池审计可重算 ─────────────────────────

def test_audit_lines_cover_every_candidate():
    rows = [row(plays='171', likes='0', comments='0', gmv='-', title='舒适又好看的搭配'),
            row(plays='34', likes='1', comments='0', gmv='-', title='男士棉服'),
            row(plays='60', likes='0', comments='0', gmv='-', title='扭花针织衫')]
    scores = buyin_worker._teardown_rank_scores(rows)
    lines = buyin_worker._teardown_rank_audit_lines(rows, scores)
    assert len(lines) == 3                      # 一行不少：漏候选 = 审计形同虚设
    assert lines[0].startswith('候选1/3:')
    assert lines[2].startswith('候选3/3:')


def test_audit_lines_carry_real_metrics_and_score():
    # 每行必须带真实指标 + 该候选自己的总分，才能事后重算核对
    rows = [row(plays='18', likes='5', comments='2', gmv='10件；¥1万-¥2万',
                title='摇粒绒外套'),
            row(plays='12', likes='0', comments='0', gmv='-', title='羽绒服')]
    scores = buyin_worker._teardown_rank_scores(rows)
    lines = buyin_worker._teardown_rank_audit_lines(rows, scores)
    assert '观看18' in lines[0] and '赞5' in lines[0]
    assert '评2' in lines[0] and '结算10件；¥1万-¥2万' in lines[0]
    assert f'分{scores[0]:.2f}' in lines[0]
    assert '观看12' in lines[1] and '结算-' in lines[1]
    assert f'分{scores[1]:.2f}' in lines[1]


def test_audit_lines_scores_match_rows_order():
    # 审计行顺序必须与 rows/scores 对齐（DOM 序），错行就核对不了谁是谁
    rows = [row(plays='9', title='A'), row(plays='900', title='B')]
    scores = buyin_worker._teardown_rank_scores(rows)
    lines = buyin_worker._teardown_rank_audit_lines(rows, scores)
    assert 'A' in lines[0] and f'分{scores[0]:.2f}' in lines[0]
    assert 'B' in lines[1] and f'分{scores[1]:.2f}' in lines[1]


def test_audit_lines_empty_rows():
    assert buyin_worker._teardown_rank_audit_lines([], []) == []
