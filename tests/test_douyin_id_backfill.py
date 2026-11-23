# -*- coding: utf-8 -*-
"""单2 守卫（2026-11-23）：抖音号回填工具的确定性对拍逻辑。

只测纯函数（零 DB 零网络）：可用号判定 / @直剥口径 / 候选对拍三级优先级。
真机回搜链路（/square-lookup）由 dry-run 报告人工复核，不进单测。
"""
import importlib.util
import os
import sqlite3

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    'douyin_id_backfill',
    os.path.join(ROOT, 'daren_cockpit/tools/douyin_id_backfill.py'))
bib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bib)


def test_usable_douyin_id_pattern():
    assert bib._usable('lisameizhuang')
    assert bib._usable('31693707031')
    assert bib._usable('LOVE.yifeifei')
    assert not bib._usable(''), '空号不可用'
    assert not bib._usable('@limantou200w'), '@前缀是脏值不是号'
    assert not bib._usable('原来是安娜'), '中文昵称不是号'
    assert not bib._usable('has space'), '含空格不是号'
    assert not bib._usable('ab'), '过短不是号'


def test_at_strip_pattern():
    """@前缀且本体合法 ⇒ 可直剥；其余脏值进回搜名单。"""
    assert bib._is_dirty('@limantou200w')
    m = bib.re.fullmatch(r'@[A-Za-z0-9_.]{5,24}', '@limantou200w')
    assert m and '@limantou200w'[1:] == 'limantou200w'


def test_pick_candidate_uid_exact_wins():
    cands = [{'昵称': '完全不像的名字', '达人ID': 'v2_uidX', '抖音号': 'DW_REAL'},
             {'昵称': '测试达人', '达人ID': 'v2_other', '抖音号': 'DW_WRONG'}]
    pick, how = bib._pick_candidate('测试达人', cands, platform_id='v2_uidX')
    assert how == 'uid_exact' and pick['抖音号'] == 'DW_REAL', '平台ID 精确优先于昵称'


def test_pick_candidate_name_exact_then_contain():
    cands = [{'昵称': '赵总外贸家居', '达人ID': '', '抖音号': 'DW_ZHAO'},
             {'昵称': '别人', '达人ID': '', '抖音号': 'DW_OTHER'}]
    pick, how = bib._pick_candidate('赵总外贸家居', cands)
    assert how == 'name_exact' and pick['抖音号'] == 'DW_ZHAO'
    # 归一化：全角点/空白/大小写不敏感
    pick, how = bib._pick_candidate('赵总·外贸家居', cands)
    assert how == 'name_exact'
    # 单向包含兜底
    pick, how = bib._pick_candidate('赵总外贸', cands)
    assert how == 'name_contain' and pick['抖音号'] == 'DW_ZHAO'


def test_pick_candidate_no_match_never_invents():
    pick, how = bib._pick_candidate('查无此人', [{'昵称': '张三', '抖音号': 'DW_A'}])
    assert pick is None and how == 'no_match', '只丢不造：无匹配不许硬配'


def test_pick_candidate_skips_blank_douyin_id():
    cands = [{'昵称': '赵总外贸家居', '达人ID': '', '抖音号': ''}]
    pick, how = bib._pick_candidate('赵总外贸家居', cands)
    assert how == 'name_exact', '对拍只选行，空号由 apply 层再闸'
    assert pick['抖音号'] == ''


def test_gap_list_classifies(tmp_path):
    db = tmp_path / 'g.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE talents (id TEXT, name TEXT, douyin_id TEXT,
                    platform_talent_id TEXT, douyin_uid TEXT, followers INTEGER)''')
    conn.executemany('INSERT INTO talents VALUES (?,?,?,?,?,?)', [
        ('t1', '正常达人', 'good_id_1', '', '', 1000),
        ('t2', '无号达人', '', '', '', 2000),
        ('t3', '可剥达人', '@keboda', '', '', 3000),
        ('t4', '脏中文', '原来是安娜', '', '', 4000),
    ])
    conn.commit()
    conn.close()
    conn = sqlite3.connect(str(db))
    g = bib.gap_list(conn)
    conn.close()
    assert g['total'] == 4 and g['usable'] == 1
    assert len(g['at_strip']) == 1 and g['at_strip'][0]['proposed'] == 'keboda'
    names = [r['name'] for r in g['need_search']]
    assert names == ['无号达人', '脏中文'], '脏中文与无号都进回搜名单'
