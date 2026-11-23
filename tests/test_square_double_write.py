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


# ── canonical「每日推荐」段系统兜底（dq_ab39888f5c49，11-23）──

_RECS = [
    {'talent_id': 'tal_sq_aaa111', 'talent_name': '挖宝小泽🌟',
     'reason_text': '个护家清主推命中，30天结算额下沿1万元'},
    {'talent_id': 'tal_sq_bbb222', 'talent_name': '小新大王',
     'reason_text': '个护家清主推命中，30天结算额下沿2.5万元'},
]


def test_canonical_section_passthrough_when_present():
    body = '导语\n【每日推荐】\n1. 旧格式某人（tal_x）｜理由\n\n别的段'
    assert bw._square_ensure_canonical_section(body, _RECS) == body


def test_canonical_section_appended_and_acceptance_parseable():
    """与验收脚本 extract_section 跨模块对拍：补齐段必须被识别为恰好 N 条目。"""
    import importlib.util as _il
    spec = _il.spec_from_file_location(
        'check_daily',
        __file__.replace('tests/test_square_double_write.py',
                         'scripts/check_daily_recommendations.py'))
    chk = _il.module_from_spec(spec)
    spec.loader.exec_module(chk)
    out = bw._square_ensure_canonical_section('今日深看5 | 新增3 | 排除2', _RECS)
    entries, heading = chk.extract_section(out)
    assert heading.startswith('【每日推荐】')
    assert len(entries) == 2          # 段尾注记行不得产生幻条目
    assert all(chk.RECO_RE.match(e.strip()) for e in entries)
    assert '挖宝小泽🌟' in entries[0] and '小新大王' in entries[1]


def test_canonical_section_empty_reason_and_empty_recs():
    out = bw._square_ensure_canonical_section('正文', [
        {'talent_id': 'tal_sq_ccc333', 'talent_name': '某人'}])
    assert '1. 某人（tal_sq_ccc333）' in out and '｜' not in out.splitlines()[2]
    # 零名单不造段
    assert bw._square_ensure_canonical_section('正文', []) == '正文'
    assert bw._square_ensure_canonical_section('正文', None) == '正文'


def test_canonical_section_capped_at_15():
    recs = [{'talent_id': f'tal_sq_x{i:03d}', 'talent_name': f'达人{i}'}
            for i in range(20)]
    out = bw._square_ensure_canonical_section('', recs)
    assert '15. 达人14' in out and '16.' not in out


def test_write_report_wires_canonical_repair(tmp_path, monkeypatch):
    """端到端：_square_write_report 对缺段正文自动补齐（临时库，不碰 data/）。"""
    db = tmp_path / 'w.db'
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id TEXT, "
                 "type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)")
    conn.execute("CREATE TABLE emp_duties (agent_id TEXT, supervisor_agent_id TEXT, "
                 "zero_streak INTEGER, duty_name TEXT, last_run_at INTEGER, "
                 "last_run_summary TEXT, updated_at INTEGER)")
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo','',0,'达人筛选专员',0,'',0)")
    conn.execute("CREATE TABLE talent_recommendations (rec_id TEXT PRIMARY KEY, "
                 "rec_date TEXT, talent_id TEXT, talent_name TEXT, matched_rules TEXT, "
                 "reason_text TEXT, snapshot_json TEXT, pipeline_status TEXT, "
                 "deal_pid TEXT, gmv REAL, refund_flag INTEGER, run_id TEXT, "
                 "created_at INTEGER, updated_at INTEGER)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report(
        '今日深看5 | 新增2 | 排除3 | 建议人工过目：甲、乙',
        '今日深看5 | 新增2 | 排除3（主因重复）',  # 正文无 canonical 段
        agent_id=bw.SQUARE_DUTY_AGENT, recommendations=_RECS,
        run_id='ssq_testrun_1')
    conn = sqlite3.connect(str(db))
    row = conn.execute("SELECT content FROM notifications WHERE agent_id='emp_xiaotuo' "
                       "AND title NOT LIKE '%抄送%' ORDER BY created_at DESC LIMIT 1").fetchone()
    conn.close()
    assert '【每日推荐】' in row[0]
    assert '1. 挖宝小泽🌟（tal_sq_aaa111）' in row[0]
