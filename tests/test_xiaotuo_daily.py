# -*- coding: utf-8 -*-
"""小拓每日调度守卫（2026-11-10 验收必修②：日报循环里要有小拓的脑子）。

铁律测试矩阵：
1. build_judgment_prompt 纯函数：关键字段（昵称/抖音号/粉丝/结算区间/类目/联系）都在，
   结算额缺失时如实写「未知」不造数
2. parse_judgment 各形态：守规矩两行 / 带前缀一行 / 空输出 / 无判定词 / 无理由兜底
3. judge_item 失败语义：CLI 不存在 / 退出码非 0 / 输出非 JSON / 空回复 →
   一律 ('存疑', '判定通道异常，转人工过目')，绝不抛出
4. save_judgment：合并 remark JSON 不动原键；remark 非 JSON 不覆盖；达人不存在跳过
5. 日报聚合（fake judgments 走通 main 的 done 分支）：
   「建议人工过目」只列 值得联系+存疑，排除不列；判定计数进 summary
"""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    'xiaotuo_daily', Path(__file__).resolve().parent.parent / 'xiaotuo_daily.py')
xd = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(xd)

ITEM = {
    '达人ID': 'u_x1', '抖音号': 'abc123', '昵称': '测试达人', '粉丝指数': 11101,
    '30天结算额': [10000.0, 25000.0], '联系方式可得性': '有',
    '主推类目': ['鞋靴箱包', '女鞋'], '入库': 'inserted（tal_test1）',
}


# ── 1. build_judgment_prompt ──

def test_prompt_contains_all_fields():
    p = xd.build_judgment_prompt(ITEM)
    for kw in ('测试达人', 'abc123', '11101', '10000-25000 元', '鞋靴箱包/女鞋', '有'):
        assert kw in p, kw
    assert '只输出两行' in p and '排除 / 存疑 / 值得联系' in p


def test_prompt_missing_settle_says_unknown_not_fabricated():
    item = dict(ITEM)
    item.pop('30天结算额')
    p = xd.build_judgment_prompt(item)
    assert '未知' in p and '10000' not in p


# ── 2. parse_judgment ──

def test_parse_two_line_wellformed():
    assert xd.parse_judgment('值得联系\n月结算过万，联系方式可得') == ('值得联系', '月结算过万，联系方式可得')


def test_parse_single_line_with_prefix():
    assert xd.parse_judgment('存疑：结算区间偏低，再观察') == ('存疑', '结算区间偏低，再观察')


@pytest.mark.parametrize('bad', ['', '   ', None])
def test_parse_empty_falls_to_doubt(bad):
    assert xd.parse_judgment(bad) == ('存疑', '判定输出为空，转人工过目')


def test_parse_no_verdict_word():
    assert xd.parse_judgment('这个达人我觉得还行吧') == ('存疑', '判定词缺失，转人工过目')


def test_parse_verdict_without_reason():
    v, r = xd.parse_judgment('排除')
    assert v == '排除' and r


# ── 3. judge_item 失败语义（绝不抛出） ──

def test_judge_cli_missing(monkeypatch):
    monkeypatch.setattr(xd, 'OPENCLAW_CLI', '/nonexistent/openclaw')
    assert xd.judge_item(ITEM, timeout=5) == ('存疑', '判定通道异常，转人工过目')


def test_judge_nonzero_exit(monkeypatch):
    class R:
        returncode = 2
        stdout = ''
        stderr = 'boom'
    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: R())
    assert xd.judge_item(ITEM, timeout=5) == ('存疑', '判定通道异常，转人工过目')


def test_judge_non_json_output(monkeypatch):
    class R:
        returncode = 0
        stdout = 'not json at all'
        stderr = ''
    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: R())
    assert xd.judge_item(ITEM, timeout=5) == ('存疑', '判定通道异常，转人工过目')


def test_judge_happy_path(monkeypatch):
    class R:
        returncode = 0
        stdout = json.dumps({'runId': 'x', 'status': 'ok',
                             'result': {'payloads': [{'text': '值得联系\n月结算过万'}]}})
        stderr = ''
    monkeypatch.setattr(xd.subprocess, 'run', lambda *a, **k: R())
    assert xd.judge_item(ITEM, timeout=5) == ('值得联系', '月结算过万')


# ── 4. save_judgment（tmp sqlite，不碰真库） ──

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    db = tmp_path / 'test.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, remark TEXT)')
    conn.execute("INSERT INTO talents VALUES ('tal_test1', ?)",
                 (json.dumps({'origin': 'square'}),))
    conn.execute("INSERT INTO talents VALUES ('tal_bad', 'not-json{{')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', db)
    return db


def test_save_judgment_merges_json(tmp_db):
    xd.save_judgment('tal_test1', '值得联系', '月结算过万')
    conn = sqlite3.connect(str(tmp_db))
    rem = json.loads(conn.execute("SELECT remark FROM talents WHERE id='tal_test1'").fetchone()[0])
    conn.close()
    assert rem['origin'] == 'square'                       # 原键不动
    assert rem['square_judgment']['verdict'] == '值得联系'
    assert rem['square_judgment']['reason'] == '月结算过万'
    assert rem['square_judgment']['judged_by'] == 'emp_xiaotuo'


def test_save_judgment_skips_non_json_remark(tmp_db):
    xd.save_judgment('tal_bad', '存疑', 'x')
    conn = sqlite3.connect(str(tmp_db))
    raw = conn.execute("SELECT remark FROM talents WHERE id='tal_bad'").fetchone()[0]
    conn.close()
    assert raw == 'not-json{{'    # 非 JSON 不覆盖


def test_save_judgment_missing_talent_no_raise(tmp_db):
    xd.save_judgment('tal_ghost', '存疑', 'x')   # 不抛即过


# ── 5. 日报聚合：fake judgments 走通 main 的 done 分支 ──

def _fake_worker_result():
    return {
        'scanned': 3, 'inserted': 3, 'duplicates': 2, 'skipped': 1,
        'pages_used': 2, 'has_more': True,
        'items': [
            dict(ITEM, 入库='inserted（tal_a）', 昵称='达人甲'),
            dict(ITEM, 入库='inserted（tal_b）', 昵称='达人乙',
                 **{'30天结算额': [50.0, 100.0]}),
            dict(ITEM, 入库='inserted（tal_c）', 昵称='达人丙',
                 **{'30天结算额': [500.0, 900.0]}),
            dict(ITEM, 入库='duplicate（达人库已存在）', 昵称='旧人'),
        ],
    }


def test_main_done_branch_aggregates_judgments(tmp_path, monkeypatch):
    db = tmp_path / 't.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, remark TEXT)')
    for tid in ('tal_a', 'tal_b', 'tal_c'):
        conn.execute('INSERT INTO talents VALUES (?, ?)', (tid, '{}'))
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', db)

    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'agent_id': 'emp_xiaotuo', 'category': '鞋靴箱包', 'quota': 5})
    calls = {'post': 0}

    def fake_json_call(method, url, payload=None, timeout=30):
        if method == 'POST':
            return {'ok': True, 'collect_id': 'job_1'}
        return {'status': 'done', 'result': json.dumps(_fake_worker_result(), ensure_ascii=False)}
    monkeypatch.setattr(xd, '_json_call', fake_json_call)

    fake_verdicts = iter([('值得联系', '月结算过万'), ('排除', '基本无成交'), ('存疑', '区间偏低')])

    def fake_judge(item, timeout=xd.JUDGE_TIMEOUT):
        return next(fake_verdicts)
    monkeypatch.setattr(xd, 'judge_item', fake_judge)
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)

    captured = {}
    monkeypatch.setattr(xd, 'write_report',
                        lambda summary, detail: captured.update(summary=summary, detail=detail))

    assert xd.main() == 0
    d = captured['detail']
    # 计数与排除主因
    assert '今日深看3' in d and '新增3' in d
    assert '判定：值得联系1 / 存疑1 / 排除1' in d
    assert '排除3（重复 2 / 缺抖音号 1）' in d
    # 建议人工过目只列 值得联系+存疑，排除（达人乙）不出现，duplicate（旧人）不参与判定
    assert '达人甲（值得联系：月结算过万）' in d
    assert '达人丙（存疑：区间偏低）' in d
    assert '达人乙' not in d.split('建议人工过目：')[1]
    # 判定结论已回写 remark
    conn = sqlite3.connect(str(db))
    rem = json.loads(conn.execute("SELECT remark FROM talents WHERE id='tal_a'").fetchone()[0])
    conn.close()
    assert rem['square_judgment']['verdict'] == '值得联系'


def test_main_all_excluded_eyeball_is_none(tmp_path, monkeypatch):
    """全部判定排除时「建议人工过目：无」——不硬凑。"""
    db = tmp_path / 't2.db'
    conn = sqlite3.connect(str(db))
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, remark TEXT)')
    conn.execute("INSERT INTO talents VALUES ('tal_a', '{}')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(xd, 'DB', db)
    monkeypatch.setattr(xd, 'load_duty',
                        lambda: {'agent_id': 'emp_xiaotuo', 'category': '', 'quota': 5})
    monkeypatch.setattr(xd, '_json_call', lambda method, url, payload=None, timeout=30:
                        {'ok': True, 'collect_id': 'job_1'} if method == 'POST'
                        else {'status': 'done', 'result': json.dumps(
                            {'scanned': 1, 'inserted': 1, 'duplicates': 0, 'skipped': 0,
                             'items': [dict(ITEM, 入库='inserted（tal_a）')]}, ensure_ascii=False)})
    monkeypatch.setattr(xd, 'judge_item', lambda item, timeout=150: ('排除', '基本无成交'))
    monkeypatch.setattr(xd.time, 'sleep', lambda s: None)
    captured = {}
    monkeypatch.setattr(xd, 'write_report',
                        lambda summary, detail: captured.update(summary=summary, detail=detail))
    assert xd.main() == 0
    assert captured['detail'].endswith('建议人工过目：无')
    assert '判定：值得联系0 / 存疑0 / 排除1' in captured['detail']
