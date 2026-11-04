"""爆款拆解 TOP N 多指标加权排序测试。

背景（2026-11-04 实测）：洁洁 6 条视频观看人数全挤在 12–18，
单按观看人数排序 ≈ 随机选 ⇒ 改多指标加权（观看50 + 点赞15 + 评论15 + 结算20）。

锁住四件事：
1. `_parse_gmv_text`：'192件；¥100万-¥250万' / '-' / '0件；¥0' 三种真实文案。
2. 加权分：观看高者排前；观看并列时点赞/评论打破平局。
3. **池内零区分度的维度如实失效**（全 0 的点赞维度不得造出区分）。
4. 新旧两种 rows 结构（auxo-table 带 cells / history-cards 空 cells）都可用。
"""
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

buyin_worker = importlib.import_module("buyin_worker")


def row(plays='', likes='', comments='', gmv='', **kw):
    r = {'index': 0, 'title': '', 'cover': '', 'plays': plays, 'interact': '',
         'gmv': gmv, 'gpm': '', 'likes': likes, 'comments': comments,
         'shares': '', 'cells': []}
    r.update(kw)
    return r


# ── 1. _parse_gmv_text ────────────────────────────────────────────────────

def test_parse_gmv_range_with_pieces():
    pieces, amt = buyin_worker._parse_gmv_text('192件；¥100万-¥250万')
    assert pieces == 192
    assert amt == 1750000.0


def test_parse_gmv_dash_and_empty():
    assert buyin_worker._parse_gmv_text('-') == (0, 0.0)
    assert buyin_worker._parse_gmv_text('') == (0, 0.0)
    assert buyin_worker._parse_gmv_text(None) == (0, 0.0)


def test_parse_gmv_pieces_only():
    # 只有件数没有金额：金额位 0，件数落库供排序兜底用
    pieces, amt = buyin_worker._parse_gmv_text('37件；¥-')
    assert pieces == 37
    assert amt == 0.0


# ── 2. 加权排序行为 ─────────────────────────────────────────────────────────

def test_higher_plays_wins():
    rows = [row(plays='18', likes='0', comments='0', gmv='-'),
            row(plays='12', likes='0', comments='0', gmv='-')]
    scores = buyin_worker._teardown_rank_scores(rows)
    assert scores[0] > scores[1]


def test_tie_on_plays_broken_by_likes():
    rows = [row(plays='18', likes='0', comments='0', gmv='-'),
            row(plays='18', likes='5', comments='0', gmv='-')]
    scores = buyin_worker._teardown_rank_scores(rows)
    assert scores[1] > scores[0]


def test_gmv_breaks_plays_tie():
    rows = [row(plays='18', likes='0', comments='0', gmv='-'),
            row(plays='18', likes='0', comments='0', gmv='10件；¥1万-¥2万')]
    scores = buyin_worker._teardown_rank_scores(rows)
    assert scores[1] > scores[0]


# ── 3. 零区分度维度如实失效 ──────────────────────────────────────────────────

def test_all_zero_dimension_creates_no_fake_spread():
    # 点赞全 0 ⇒ 点赞维度 min-max 全 0 ⇒ 分数只由观看决定，且同观看分数必相等
    rows = [row(plays='18', likes='0'), row(plays='18', likes='0'),
            row(plays='12', likes='0')]
    scores = buyin_worker._teardown_rank_scores(rows)
    assert scores[0] == scores[1]
    assert scores[0] > scores[2]


def test_all_equal_rows_all_zero_scores():
    rows = [row(plays='18', likes='0', comments='0', gmv='-') for _ in range(3)]
    scores = buyin_worker._teardown_rank_scores(rows)
    assert scores == [0.0, 0.0, 0.0]


# ── 4. 新旧 rows 结构兼容 ───────────────────────────────────────────────────

def test_both_row_shapes_scoreable():
    old_style = row(plays='3,411', likes='120', comments='30',
                    gmv='50件；¥5万-¥10万', cells=['视频信息 2026/10/01'])
    new_style = {'index': 0, 'title': 'x', 'cover': '', 'plays': '18',
                 'interact': '', 'gmv': '-', 'gpm': '', 'likes': '0',
                 'comments': '0', 'shares': '', 'cells': [],
                 'layout': 'history-cards'}
    scores = buyin_worker._teardown_rank_scores([old_style, new_style])
    assert len(scores) == 2
    assert scores[0] > scores[1]


def test_empty_rows():
    assert buyin_worker._teardown_rank_scores([]) == []
