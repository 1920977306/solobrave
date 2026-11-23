# -*- coding: utf-8 -*-
"""单1① 守卫（2026-11-23）：_square_decide 判定即双写 B 表（talent_recommendations）。

背景：11-20 起班级交付管道切到即时班后，判定结果只在日报文字里、B 表恒空，
验收网两判据红、传送带假旗。双写 = 源头写（decide 时）+ 交付时兜底解析器
（test_square_report_fallback.py）两层互补。

守卫矩阵：
1. 值得联系（新入库）→ talents 一行 + B 表一行（rec_id=rec_{ymd}_{tid}，
   pipeline_status=recommended，reason_text 走 canonical 单一出口非空，
   snapshot_json 带结构化真值）
2. 幂等：同一候选再判值得联系（duplicate 路径）→ B 表仍只有一行，内容刷新
3. 改判存疑（duplicate 路径）→ 当日 B 表行被删（B 表=当日值得联系集合）
4. 排除（库里已有该抖音号）→ 当日 B 表行被删 + 审计日志 + 不入库
5. 存疑（新候选）→ talents 入库但 B 表零行（存疑不占推荐位）
6. 双写失败不阻断判定主流程（normalize 返回空 ⇒ 静默返回；异常只 log）
"""
import json
import sqlite3
from datetime import datetime

import pytest

import buyin_worker as bw

_CAND = {
    '达人ID': 'v2_testuid_dw', '抖音号': 'DW.test2026', '昵称': '双写测试达人',
    '粉丝指数': 88000, '30天结算额': [20000.0, 50000.0],
    '联系方式可得性': '未披露', '主推类目': ['智能家居'], '达人等级': '4', '城市': '浙江·杭州',
}

_SCHEMA_TALENTS = '''CREATE TABLE talents (
    id TEXT PRIMARY KEY, name TEXT, avatar TEXT, douyin_id TEXT,
    douyin_uid TEXT, city TEXT, level TEXT, followers INTEGER,
    tags TEXT, category TEXT, contact_wechat TEXT, contact_phone TEXT,
    remark TEXT, created_by TEXT, platform TEXT, status TEXT,
    cooperation_status TEXT, created_at INTEGER, updated_at INTEGER)'''

_SCHEMA_REC = '''CREATE TABLE talent_recommendations (
    rec_id TEXT PRIMARY KEY, rec_date TEXT NOT NULL, talent_id TEXT NOT NULL,
    talent_name TEXT NOT NULL DEFAULT '', matched_rules TEXT NOT NULL DEFAULT '[]',
    reason_text TEXT NOT NULL DEFAULT '', snapshot_json TEXT,
    pipeline_status TEXT NOT NULL DEFAULT 'pending', deal_pid TEXT, gmv REAL,
    refund_flag INTEGER, run_id TEXT,
    created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
    UNIQUE(rec_date, talent_id))'''


def _mk_db(tmp_path, monkeypatch):
    db = tmp_path / 'dw.db'
    conn = sqlite3.connect(str(db))
    conn.execute(_SCHEMA_TALENTS)
    conn.execute(_SCHEMA_REC)
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    # 锚文件指向 tmp，防误读生产落盘锚（真实日期命中会让 run_id 断言漂移）
    monkeypatch.setattr(bw, 'SQUARE_ANCHOR_FILE', tmp_path / 'anchor.json')
    return db


def _rec_rows(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute('SELECT rec_id, rec_date, talent_id, talent_name, '
                        'matched_rules, reason_text, snapshot_json, '
                        'pipeline_status FROM talent_recommendations').fetchall()
    conn.close()
    return rows


def test_decide_worthy_double_writes_rec(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '值得联系',
                                   'reason': '结算顶格'})
    assert code == 200 and out['inserted']
    rows = _rec_rows(db)
    assert len(rows) == 1
    rec_id, rec_date, tid, name, rules, reason, snap_js, status = rows[0]
    ymd = datetime.now().strftime('%Y%m%d')
    assert rec_id == f'rec_{ymd}_{out["talent_id"]}'
    assert rec_date == datetime.now().strftime('%Y-%m-%d')
    assert tid == out['talent_id'] and name == '双写测试达人'
    assert rules == '[]', 'matched_rules 宁可空存不可编造'
    assert status == 'recommended'
    # canonical 单一出口：结构化真值组装，不含噪音字段
    assert '智能家居' in reason and '30天结算' in reason
    assert '未披露' not in reason, '联系方式噪音永不入正文'
    snap = json.loads(snap_js)
    assert snap['主推类目'] == ['智能家居']
    assert snap['30天结算额'] == [20000.0, 50000.0]
    assert snap['粉丝指数'] == 88000


def test_decide_duplicate_worthy_idempotent_refresh(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    bw._square_decide({'candidate': _CAND, 'verdict': '值得联系', 'reason': 'r1'})
    cand2 = dict(_CAND, **{'30天结算额': [30000.0, 60000.0]})
    out, code = bw._square_decide({'candidate': cand2, 'verdict': '值得联系',
                                   'reason': 'r2'})
    assert code == 200 and out['duplicate'] is True and out['inserted'] is False
    rows = _rec_rows(db)
    assert len(rows) == 1, '同日同人幂等：REPLACE 不叠行'
    snap = json.loads(rows[0][6])
    assert snap['30天结算额'] == [30000.0, 60000.0], '内容刷新'


def test_decide_doubtful_after_worthy_removes_rec(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    bw._square_decide({'candidate': _CAND, 'verdict': '值得联系', 'reason': 'r'})
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '存疑',
                                   'reason': '再看看'})
    assert code == 200 and out['duplicate'] is True
    assert _rec_rows(db) == [], '存疑改判：当日推荐位撤下（delete）'


def test_decide_reject_removes_rec_and_audits(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    bw._square_decide({'candidate': _CAND, 'verdict': '值得联系', 'reason': 'r'})
    rejects = tmp_path / 'rejects.jsonl'
    monkeypatch.setattr(bw, 'SQUARE_REJECTS_LOG', rejects)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '排除',
                                   'reason': '数据存疑'})
    assert code == 200 and out['inserted'] is False
    assert _rec_rows(db) == [], '排除改判：当日推荐位撤下'
    assert len(rejects.read_text(encoding='utf-8').strip().splitlines()) == 1


def test_decide_doubtful_new_talent_no_rec(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '存疑',
                                   'reason': '再观察'})
    assert code == 200 and out['inserted']
    assert _rec_rows(db) == [], '存疑不占推荐位：talents 入库但 B 表零行'


def test_double_write_failure_never_blocks_decide(tmp_path, monkeypatch):
    db = _mk_db(tmp_path, monkeypatch)
    # B 表被「拿走」⇒ 双写 INSERT 必炸；判定主流程（talents 入库）必须照走
    conn = sqlite3.connect(str(db))
    conn.execute('DROP TABLE talent_recommendations')
    conn.commit()
    conn.close()
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '值得联系',
                                   'reason': 'r'})
    assert code == 200 and out['ok'] and out['inserted']
