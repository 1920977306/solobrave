#!/usr/bin/env python3
"""落库兜底解析器守卫（①号单 2026-11-23，协调边派单包）。

锁的形态（验收判据对应）：
1. 空 payload + 正文「每日推荐 N 人」编号段 ⇒ 解析出带 tal id 的推荐（判据①）；
2. 空 payload + 「每日推荐 0 人」+ 待触达清单（编号项/顿号名单）⇒ 按名/按 id
   解析，判定以 talents.remark.square_judgment.verdict 为准，非「值得联系」跳过；
3. 累积清单（含「11-20及以前」「今日累计」日期锚）⇒ 不解析，防跨班汇总冒充
   本班名单（11-22 18:03 实证）；
4. 正文无推荐段 ⇒ 零行不造数（判据③）；
5. _square_write_report 集成：空 payload 有正文 ⇒ B 表有行（判据①端到端）；
   payload 非空 ⇒ 原语义不动（payload 直通）；
6. 达人名前缀反查唯一命中可用（「元宝团子」← 库名截断），多义/无命中跳过不造。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import buyin_worker as bw  # noqa: E402


# ── 真实日报正文样本（2026-11-19~11-22 生产实证，解析器的规格来源）─────────
_DETAIL_1119_SECTION = """\
【广场即时班 · 2026-11-19 09:30 第1/3班】run=ssq_1795051850681_e7c236
数字构成：深看=本班候选5人。

每日推荐 5 人
1. 海豚兄妹17号16点🔥火锅节🎉（tal_sq_2b39aa938e）｜服饰内衣主推，30天结算1万-5万（下沿10000元），粉丝指数93.5万；联系方式来源=未披露
2. 小毅全品类（收学员）（tal_sq_ef4936acf2）｜服饰内衣主推，30天结算1万-2.5万（下沿10000元），粉丝指数51.4万；联系方式来源=未披露
3. 唐森森𝐈𝐜𝐞（tal_sq_cf2253c08b）｜智能家居（生活家居口径）主推，30天结算1万-5万（下沿10000元），粉丝指数47.4万；联系方式来源=未披露
4. 盘妮语物（tal_sq_0fa940af64）｜智能家居（生活家居口径）主推，30天结算1万-2.5万（下沿10000元），粉丝指数30.7万；联系方式来源=未披露
5. 崔大琴和她的婆婆（tal_sq_41039ea5dc）｜服饰内衣主推，30天结算0.5万-1万（下沿5000元），粉丝指数14.3万；联系方式来源=未披露

候选·需先拿联系方式：以上5人「联系方式可得性」均为未披露。
"""

_DETAIL_TODO_NUMBERED = """\
【广场即时班 · 2026-11-21 09:30】run=ssq_1795224869300_48f340（今日第1/3班）
每日推荐 0 人：按 2026-11-20 定稿口径，推荐位只放「值得联系 + 联系方式可得」，本班值得联系者联系方式全部未披露，故推荐位0人、B表不写行。
待触达清单（来源=未披露，需人工先取联系方式，本班4人）：1. 小冲冲家居美学（tal_sq_2bae503a35）｜30天结算下沿2500元、粉丝29.3万、智能家居；2. 雪老板有好货（甫圭）（tal_sq_f066d860df）｜30天结算下沿1万、粉丝27.9万、个护家清；3. 纳迪尔（tal_sq_1b2f312e6a）｜30天结算下沿1万、粉丝33.8万、智能家居；4. 莹莹酱.（达人口播备注「莹莹酱.」，抖音号5505008，台账 talent_id 待补）｜30天结算下沿1万、粉丝3.1万、个护家清（主管裁定补判）。
今日累计（截至09:30班）：深看5 | 新增4 | 排除15。
"""

_DETAIL_TODO_PLAIN_WITH_CUM = """\
【广场即时班 · 2026-11-20 17:30】run=ssq_1795167127033_2c2f03（今日第3/3班）
每日推荐 0 人：按定稿口径推荐位只放「值得联系+联系方式可得」，本班3位值得联系者全部未披露，故推荐位0人、B表不写行。
待触达清单（来源=未披露，需人工先取联系方式）：小张的家🏠、美洋生活好物、爱家的小曹；今日累计9人：赵总外贸家居、lisa杂货铺-精选、陈七七家、元宝团子、惠子思密达_、阳哥 + 本班3人。
"""

_DETAIL_CUMULATIVE = """\
【广场即时班 · 2026-11-22 17:30】run=未发起scan（预检判定登录墙）｜类目=鞋靴箱包 配额5
每日推荐 0 人
· 待触达清单（14位，来源=未披露，需人工先取联系方式；按主管11-22裁定以14位为准）：11-20及以前6位：赵总外贸家居、lisa杂货铺-精选、陈七七家、元宝团子、惠子思密达_、阳哥；11-21 09:30班4位：小冲冲家居美学、雪老板有好货（甫圭）、纳迪尔、吉他妈妈20号早上9:00童装大上新；11-21 13:30班4位：丢丢大姑姑（丹丹崽）南昌一家人、小袁爱家居🏡、安琪安琪、莹莹酱.（主管裁定补判）。
"""

_DETAIL_NO_SECTION = """\
【广场即时班 · 2026-11-22 13:30】run=ssq_1795325406508_57b9c8
扫描情况：scan 返回 status=failed，TimeoutError 90s。
每日推荐 0 人
（本班无「值得联系」候选，推荐段为空，--rec 落空数组）
"""

_DETAIL_VERDICT_FILTER = """\
每日推荐 0 人
待触达清单（来源=未披露，本班3人）：惠子思密达_、Rosie、元宝团子。
"""


def _mk_conn(tmp_path):
    conn = sqlite3.connect(str(tmp_path / 'fb.db'))
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT, '
                 'douyin_id TEXT, remark TEXT, created_at INTEGER DEFAULT 0)')

    def tal(tid, name, verdict):
        rm = json.dumps({'square_judgment': {'verdict': verdict}},
                        ensure_ascii=False) if verdict else '{}'
        conn.execute('INSERT INTO talents (id, name, remark) VALUES (?,?,?)',
                     (tid, name, rm))
    tal('tal_sq_2b39aa938e', '海豚兄妹17号16点🔥火锅节🎉', '值得联系')
    tal('tal_sq_2bae503a35', '小冲冲家居美学', '值得联系')
    tal('tal_sq_f066d860df', '雪老板有好货（甫圭）', '值得联系')
    tal('tal_sq_1b2f312e6a', '纳迪尔', '值得联系')
    tal('tal_1789443949882_167248', '莹莹酱.', '值得联系')
    tal('tal_sq_a662b3c32d', '小张的家🏠', '值得联系')
    tal('tal_sq_3023453565', '美洋生活好物', '值得联系')
    tal('tal_sq_e0b83f1851', '爱家的小曹', '值得联系')
    tal('tal_sq_b67346720b', '惠子思密达_', '值得联系')
    tal('tal_sq_c5d898f128', 'Rosie', '排除')
    tal('tal_sq_dee5bf8f18', '优奢箱包集', '值得联系')
    # 前缀多义对：两个「测试」开头
    tal('tal_amb_1', '测试甲', '值得联系')
    tal('tal_amb_2', '测试乙', '值得联系')
    # 库名截断样本：报告写「元宝团子」，库名更长
    tal('tal_sq_5a1f7a8234', '元宝团子（品牌绑链接先看简介～', '值得联系')
    conn.commit()
    return conn


# ── 1. 编号段直取 ────────────────────────────────────────────────
def test_numbered_section_parsed(tmp_path):
    conn = _mk_conn(tmp_path)
    recs = bw._square_recs_from_report_text(_DETAIL_1119_SECTION, conn)
    assert len(recs) == 5
    assert recs[0]['talent_id'] == 'tal_sq_2b39aa938e'
    assert recs[0]['talent_name'] == '海豚兄妹17号16点🔥火锅节🎉'
    assert '93.5万' in recs[0]['reason_text']


# ── 2. 「0 人」+ 待触达编号项（含无 id 的按名反查）─────────────────
def test_todo_numbered_items_resolve_names(tmp_path):
    conn = _mk_conn(tmp_path)
    recs = bw._square_recs_from_report_text(_DETAIL_TODO_NUMBERED, conn)
    assert [r['talent_id'] for r in recs] == [
        'tal_sq_2bae503a35', 'tal_sq_f066d860df', 'tal_sq_1b2f312e6a',
        'tal_1789443949882_167248']           # 莹莹酱无 tal id ⇒ DB 精确名命中
    assert recs[1]['talent_name'] == '雪老板有好货（甫圭）'   # 名字自带全角括号保真


# ── 3. 顿号名单 + 「今日累计」尾巴只取本班 ─────────────────────────
def test_plain_names_cumulative_tail_dropped(tmp_path):
    conn = _mk_conn(tmp_path)
    recs = bw._square_recs_from_report_text(_DETAIL_TODO_PLAIN_WITH_CUM, conn)
    assert sorted(r['talent_name'] for r in recs) == ['小张的家🏠', '爱家的小曹',
                                                      '美洋生活好物']
    assert all('元宝团子' not in r['talent_name'] for r in recs), '累计尾巴不得混入'


# ── 3b. 累积清单（日期锚）整体拒绝 ─────────────────────────────────
def test_cumulative_list_rejected(tmp_path):
    conn = _mk_conn(tmp_path)
    assert bw._square_recs_from_report_text(_DETAIL_CUMULATIVE, conn) == []


# ── 4. verdict 闸：非「值得联系」跳过 ─────────────────────────────
def test_verdict_gate_filters(tmp_path):
    conn = _mk_conn(tmp_path)
    recs = bw._square_recs_from_report_text(_DETAIL_VERDICT_FILTER, conn)
    assert [r['talent_name'] for r in recs] == ['惠子思密达_', '元宝团子'], \
        'Rosie（排除）被跳过；元宝团子经前缀反查命中'


# ── 5. 无推荐段 ⇒ 零行（不造数）───────────────────────────────────
def test_no_section_returns_empty(tmp_path):
    conn = _mk_conn(tmp_path)
    assert bw._square_recs_from_report_text(_DETAIL_NO_SECTION, conn) == []
    assert bw._square_recs_from_report_text('', conn) == []
    assert bw._square_recs_from_report_text(None, conn) == []


# ── 6. 前缀反查多义 ⇒ 跳过不造 ────────────────────────────────────
def test_ambiguous_prefix_skipped(tmp_path):
    conn = _mk_conn(tmp_path)
    detail = '每日推荐 0 人\n待触达清单（本班1人）：测试。'   # 「测试」是两个库名的公共前缀
    assert bw._square_recs_from_report_text(detail, conn) == []


# ── 7. 来源裁决：payload 直通 / 正文解析 / none ────────────────────
def test_rec_source_payload_passthrough(tmp_path):
    conn = _mk_conn(tmp_path)
    payload = [{'talent_id': 'tal_x', 'talent_name': '某人'}]
    recs, src = bw._square_report_rec_source(payload, _DETAIL_1119_SECTION, conn)
    assert src == 'payload' and recs == payload


def test_rec_source_report_text(tmp_path):
    conn = _mk_conn(tmp_path)
    recs, src = bw._square_report_rec_source([], _DETAIL_1119_SECTION, conn)
    assert src == 'report_text' and len(recs) == 5


def test_rec_source_none(tmp_path):
    conn = _mk_conn(tmp_path)
    recs, src = bw._square_report_rec_source([], _DETAIL_NO_SECTION, conn)
    assert src == 'none' and recs == []


# ── 8. _square_write_report 集成：空 payload ⇒ B 表照落 ────────────
def _duty_db(tmp_path):
    db = tmp_path / 'r.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id,
                    type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)''')
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    duty_name TEXT DEFAULT '达人筛选专员',
                    supervisor_agent_id TEXT DEFAULT '', zero_streak INTEGER DEFAULT 0,
                    last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
                    updated_at INTEGER)''')
    conn.execute('''CREATE TABLE talent_recommendations (
        rec_id TEXT PRIMARY KEY, rec_date TEXT NOT NULL, talent_id TEXT NOT NULL,
        talent_name TEXT NOT NULL DEFAULT '', matched_rules TEXT NOT NULL DEFAULT '[]',
        reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
        pipeline_status TEXT NOT NULL DEFAULT 'pending', deal_pid TEXT, gmv REAL,
        refund_flag INTEGER, run_id TEXT,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        UNIQUE(rec_date, talent_id))''')
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', '达人筛选专员', '', 0, 0, '', 0)")
    conn.execute('CREATE TABLE talents (id TEXT PRIMARY KEY, name TEXT, remark TEXT)')
    for tid, name, verdict in (
            ('tal_sq_2bae503a35', '小冲冲家居美学', '值得联系'),
            ('tal_1789443949882_167248', '莹莹酱.', '值得联系')):
        rm = json.dumps({'square_judgment': {'verdict': verdict}}, ensure_ascii=False)
        conn.execute('INSERT INTO talents (id, name, remark) VALUES (?,?,?)',
                     (tid, name, rm))
    conn.commit()
    conn.close()
    return db


def test_write_report_empty_payload_falls_back_to_text(tmp_path, monkeypatch):
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report('值得联系 2', _DETAIL_TODO_NUMBERED, 'emp_xiaotuo',
                            recommendations=[], run_id='ssq_test_run')
    conn = sqlite3.connect(str(db))
    rows = conn.execute('SELECT talent_id, run_id, pipeline_status '
                        'FROM talent_recommendations ORDER BY talent_id').fetchall()
    conn.close()
    assert sorted(r[0] for r in rows) == ['tal_1789443949882_167248',
                                          'tal_sq_2bae503a35']
    assert all(r[1] == 'ssq_test_run' and r[2] == 'recommended' for r in rows)


def test_write_report_payload_path_unchanged(tmp_path, monkeypatch):
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    payload = [{'talent_id': 'tal_sq_2bae503a35', 'talent_name': '小冲冲家居美学',
                'reason_text': '命中智能家居', 'snapshot': {}}]
    bw._square_write_report('值得联系 1', '详情正文', 'emp_xiaotuo',
                            recommendations=payload, run_id='ssq_test_run')
    conn = sqlite3.connect(str(db))
    n = conn.execute('SELECT COUNT(*) FROM talent_recommendations').fetchone()[0]
    conn.close()
    assert n == 1, 'payload 非空时正文不参与解析（原语义）'


def test_write_report_no_section_zero_rows(tmp_path, monkeypatch):
    db = _duty_db(tmp_path)
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report('值得联系 0', _DETAIL_NO_SECTION, 'emp_xiaotuo',
                            recommendations=[], run_id='ssq_test_run')
    conn = sqlite3.connect(str(db))
    n = conn.execute('SELECT COUNT(*) FROM talent_recommendations').fetchone()[0]
    conn.close()
    assert n == 0, '正文无推荐段 ⇒ 零行不造数（判据③端到端）'
