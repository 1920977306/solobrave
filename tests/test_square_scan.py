# -*- coding: utf-8 -*-
"""square_scan 工具守卫（2026-11-10，B 简报）。

铁律测试矩阵：
1. quota 硬顶 5（传 50 也只执行 5）
2. 工作时段闸 9:00-21:00
3. 字段映射——断言样本逐字来自 2026-11-10 真机响应
   （团长端有头浏览器实测 search_feed_author code=0，
     dump 于 /Users/qichen/Documents/kimi/tasks/2026-09-30/15-47-22-6b6fc1a8/square-api-dump-headed.json）
4. 风控断言：10001010A → SquareRiskBlocked（负向对照：code=0 放行）
5. 抖音号查重 + 入库 created_by=square_scan_<日期>（tmp sqlite，不碰真库）
6. 客户端类目兜底筛选
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

import buyin_worker as bw

# ── 真机样本（2026-11-10 有头实测第 1 位达人；长 URL 已截断，结构/数值逐字保留） ──
REAL_AUTHOR = {
    "author_base": {
        "uid": "v2_0a2c0c691a204aa14b7acf3785ea2bed3c815",
        "author_id": 0,
        "nickname": "幸运佳",
        "avatar": "https://p26-sign.douyinpic.com/aweme/200x200/aweme-avatar/to",
        "fans_num": 11101,
        "gender": 1,
        "city": "河南·洛阳",
        "author_level": 4,
        "avatar_big": "",
        "aweme_id": "LOVE.yifeifei",
    },
    "author_tag": {
        "main_cate": ["3C数码家电", "智能家居", "个护家清"],
        "dark_horse": "",
        "contact_icon": "达人自主披露联系方式，你可点击小眼睛进行查看",
        "high_reply": "",
        "invitation_status": 0,
        "invite_status": 0,
        "satisfy_requirement": 2,
        "already_cooperated": False,
        "is_star": False,
        "high_response_rate": "回复率高",
        "is_open_invoice": False,
        "author_rec_reasons": [
            {"tag_type": 1016, "reason": "潜力合作", "extra": "",
             "extra_map": {"category": "green", "tips": "稳定"}},
            {"tag_type": 1014, "reason": "结算率高", "extra": "",
             "extra_map": {"category": "strong", "tips": "高于平均"}},
            {"tag_type": 1006, "reason": "有联系方式", "extra": "",
             "extra_map": {"category": "strong", "tips": "自主披露"}},
        ],
        "high_settlement_rate": "结算率高",
        "cooperation_level": "潜力合作",
    },
    "author_contact": {"phone": "", "wechat": "", "lark": "", "douyin": ""},
    "author_live": {
        "watching_number": 13415, "sale_status": 0, "sale_low": 50000,
        "sale_high": 100000, "GPM_status": 0, "GPM_low": 1000, "GPM_high": 0,
        "watching_times": 587627, "all_live_num_30d": 33,
        "sale_status_settle": 0, "sale_low_settle": 50000,
        "sale_high_settle": 100000, "gpm_status_settle": 0,
        "gpm_low_settle": 1000, "gpm_high_settle": 0,
    },
    "author_video": {
        "play_median": 0, "GPM_status": 2, "GPM_low": 0, "GPM_high": 0,
        "video_sale_status": 0, "video_sale_low": 0, "video_sale_high": 250,
        "watching_times": 0, "all_video_num_30d": 0, "gpm_status_settle": 2,
        "gpm_low_settle": 0, "gpm_high_settle": 0,
        "video_sale_status_settle": 0, "video_sale_low_settle": 0,
        "video_sale_high_settle": 250,
    },
    "author_conf": {"sale_requirement": {"type": 1, "value": "15%", "price_type": 1}},
    "author_score": {
        "expr_score": {"score": 0, "level": "", "percentage": 0},
        "product_score": {"score": 0, "level": "", "percentage": 0},
        "logistics_score": {"score": 0, "level": "", "percentage": 0},
        "service_score": {"score": 0, "level": "", "percentage": 0},
    },
    "author_sale": {
        "sale_status": 0, "sale_d30_low": 2500000, "sale_d30_high": 5000000,
        "main_sale_type": "直播为主", "promotion_num": 0,
        "cooperate_content_num": 0, "cooperate_product_num": 0,
        "sale_status_settle": 0, "sale_d30_low_settle": 1000000,
        "sale_d30_high_settle": 2500000,
    },
    "sale_info": {
        "total_sales": {"sale_status": 0, "sale_value": 0,
                        "sale_low": 2500000, "sale_high": 5000000},
        "live_total_sales": {"sale_status": 0, "sale_value": 0,
                             "sale_low": 2500000, "sale_high": 5000000},
        "video_total_sales": {"sale_status": 0, "sale_value": 0,
                              "sale_low": 250, "sale_high": 500},
        "image_text_total_sales": {"sale_status": 0, "sale_value": 0,
                                   "sale_low": 0, "sale_high": 250},
        "window_total_sales": {"sale_status": 0, "sale_value": 0,
                               "sale_low": 25000, "sale_high": 50000},
        "total_sales_settle": {"sale_status": 0, "sale_value": 0,
                               "sale_low": 1000000, "sale_high": 2500000},
        "live_total_sales_settle": {"sale_status": 0, "sale_value": 0,
                                    "sale_low": 1000000, "sale_high": 2500000},
        "video_total_sales_settle": {"sale_status": 0, "sale_value": 0,
                                     "sale_low": 0, "sale_high": 250},
        "image_text_total_sales_settle": {"sale_status": 0, "sale_value": 0,
                                          "sale_low": 0, "sale_high": 250},
        "window_total_sales_settle": {"sale_status": 0, "sale_value": 0,
                                      "sale_low": 25000, "sale_high": 50000},
    },
    "author_image_text": {
        "image_text_num": 0, "watching_times": 0, "GPM_status": 2,
        "GPM_low": 0, "GPM_high": 0, "sale_low": 0, "sale_high": 250,
        "all_image_text_num_30d": 0, "sale_status": 0,
        "gpm_status_settle": 2, "gpm_low_settle": 0, "gpm_high_settle": 0,
        "sale_status_settle": 0, "sale_low_settle": 0, "sale_high_settle": 250,
    },
    "author_window": {"product_num": 181, "order_low": 500,
                      "order_high": 1000, "status": 0},
    "author_crm": {"is_join": False},
}

# 风控响应样本：FINDINGS.md 实锤（headless 克隆 profile 场景），逐字引用
RISK_PAYLOAD = {"code": "10001010A", "msg": "当前环境存在风险，请稍后重试"}


def test_quota_hard_cap():
    assert bw._square_quota(50) == 5, '传 50 也必须只执行 5（铁律）'
    assert bw._square_quota(5) == 5
    assert bw._square_quota(3) == 3
    assert bw._square_quota(0) == 1, '0 压到 1'
    assert bw._square_quota(-3) == 1, '负数压到 1'
    assert bw._square_quota('abc') == 5, '非数字回退默认 5'
    assert bw._square_quota(None) == 5


def test_work_hours_gate():
    assert not bw._square_work_hours_ok(datetime(2026, 11, 10, 8, 59))
    assert bw._square_work_hours_ok(datetime(2026, 11, 10, 9, 0))
    assert bw._square_work_hours_ok(datetime(2026, 11, 10, 12, 0))
    assert bw._square_work_hours_ok(datetime(2026, 11, 10, 20, 59))
    assert not bw._square_work_hours_ok(datetime(2026, 11, 10, 21, 0))


def test_author_fields_mapping_real_sample():
    f = bw._square_scan_author_to_fields(REAL_AUTHOR)
    assert f['douyin_id'] == 'LOVE.yifeifei', '抖音号 = aweme_id（去重键）'
    assert f['douyin_uid'].startswith('v2_'), '达人ID = uid'
    assert f['name'] == '幸运佳'
    assert f['followers'] == 11101
    # 分 → 元：结算 1_000_000 分 = ¥10,000 / 2_500_000 分 = ¥25,000
    assert f['settle_d30_low'] == 10000.0
    assert f['settle_d30_high'] == 25000.0
    assert f['contact_available'] is False, '联系方式全空 → 未披露'
    assert f['main_cate'] == ['3C数码家电', '智能家居', '个护家清']
    assert f['category'] == '3C数码家电/智能家居/个护家清'
    assert f['city'] == '河南·洛阳'
    assert f['level'] == '4'
    remark = json.loads(f['remark'])
    assert remark['main_sale_type'] == '直播为主'
    assert remark['live_sessions_30d'] == 33
    # 只丢不造：平台没给的字段不编造
    assert f['contact_wechat'] == ''


def test_author_fields_contact_present():
    a = json.loads(json.dumps(REAL_AUTHOR))
    a['author_contact'] = {'phone': '', 'wechat': 'wx123', 'lark': '', 'douyin': ''}
    f = bw._square_scan_author_to_fields(a)
    assert f['contact_available'] is True
    assert f['contact_wechat'] == 'wx123'


def test_risk_assert_blocks_10001010A():
    with pytest.raises(bw.SquareRiskBlocked):
        bw._square_assert_not_risk(RISK_PAYLOAD)
    # 负向对照：正常响应必须放行（探针能红的另一半）
    bw._square_assert_not_risk({'code': 0, 'msg': '', 'data': {'list': []}})


def test_risk_assert_blocks_risk_msg_variants():
    with pytest.raises(bw.SquareRiskBlocked):
        bw._square_assert_not_risk({'code': 0, 'msg': '操作存在风险，请稍后再试'})


def _tmp_talents_db(tmp_path, monkeypatch):
    db = tmp_path / 'test.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT, avatar TEXT, douyin_id TEXT,
        douyin_uid TEXT, city TEXT, level TEXT, followers INTEGER,
        tags TEXT, category TEXT, contact_wechat TEXT, contact_phone TEXT,
        remark TEXT, created_by TEXT, platform TEXT, status TEXT,
        cooperation_status TEXT, created_at INTEGER, updated_at INTEGER)''')
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    return db


def test_insert_and_dedupe_by_douyin_id(tmp_path, monkeypatch):
    _tmp_talents_db(tmp_path, monkeypatch)
    f = bw._square_scan_author_to_fields(REAL_AUTHOR)
    assert bw._square_talent_exists('LOVE.yifeifei') is False
    tid = bw._square_insert_talent(f, 'square_scan_20261110')
    assert tid.startswith('tal_sq_')
    assert bw._square_talent_exists('LOVE.yifeifei') is True, '入库后查重必须命中'
    # 负向对照：不同抖音号不能误命中
    assert bw._square_talent_exists('OTHER.id') is False
    conn = sqlite3.connect(str(bw.DB_PATH))
    row = conn.execute('SELECT created_by, platform, status FROM talents '
                       'WHERE id = ?', (tid,)).fetchone()
    conn.close()
    assert row[0] == 'square_scan_20261110', 'source 标记 = square_scan_<日期>'
    assert row[1] == '抖音' and row[2] == 'active'


def test_pick_by_category_fallback():
    a_shoe = json.loads(json.dumps(REAL_AUTHOR))
    a_shoe['author_tag']['main_cate'] = ['鞋靴箱包', '服饰内衣']
    picked = bw._square_pick_by_category([REAL_AUTHOR, a_shoe], '鞋靴箱包', 5)
    assert len(picked) == 1 and picked[0] is a_shoe
    # 类目不匹配 → 空（不编造数据）
    assert bw._square_pick_by_category([REAL_AUTHOR], '鞋靴箱包', 5) == []


# ── 必修①（2026-11-10 验收返工）：_square_collect_new_candidates 跨页收集 ──
# 合成的 author dict 形状照真机样本（REAL_AUTHOR）：author_base.author_tag.author_contact

def _mk_author(aweme_id, nickname, cate=('鞋靴箱包',)):
    return {
        'author_base': {'uid': 'u_' + (aweme_id or 'noid'), 'author_id': 0,
                        'nickname': nickname, 'avatar': '', 'fans_num': 1000,
                        'aweme_id': aweme_id},
        'author_tag': {'main_cate': list(cate)},
        'author_contact': {},
    }


def test_collect_all_old_page_then_new_page():
    """第一页全在库 → 翻页后从第二页取到新候选（必修①核心场景：不再永远重复 5）。"""
    page1 = [_mk_author('OLD1', '老人1'), _mk_author('OLD2', '老人2')]
    page2 = [_mk_author('NEW1', '新人1'), _mk_author('NEW2', '新人2')]
    exists = lambda did: did.startswith('OLD')
    coll = bw._square_collect_new_candidates([page1], exists, '', 5)
    assert coll['picked'] == [] and coll['dup_skipped'] == 2   # 只看第一页时一无所获
    coll = bw._square_collect_new_candidates([page1, page2], exists, '', 5)
    assert [ (a['author_base']['aweme_id']) for a in coll['picked'] ] == ['NEW1', 'NEW2']
    assert coll['dup_skipped'] == 2 and coll['pages_used'] == 2


def test_collect_quota_truncation():
    """quota 硬顶：7 个新候选只取 5 个。"""
    pages = [[_mk_author(f'N{i}', f'新人{i}') for i in range(7)]]
    coll = bw._square_collect_new_candidates(pages, lambda did: False, '', 5)
    assert len(coll['picked']) == 5


def test_collect_max_pages_cap():
    """翻满 5 页上限即停：6 页全旧 → pages_used=5、 picked 空（不无限翻）。"""
    pages = [[_mk_author(f'O{p}_{i}', f'旧{p}-{i}') for i in range(2)] for p in range(6)]
    coll = bw._square_collect_new_candidates(pages, lambda did: True, '', 5)
    assert coll['pages_used'] == bw.SQUARE_MAX_PAGES == 5
    assert coll['picked'] == [] and coll['dup_skipped'] == 10


def test_collect_skips_no_douyin_id():
    """平台未给抖音号的候选 skip 且不计入 picked（缺一不入库铁律）。"""
    page = [_mk_author('', '无号达人'), _mk_author('OK1', '正常人')]
    coll = bw._square_collect_new_candidates([page], lambda did: False, '', 5)
    assert coll['noid_skipped'] == 1
    assert [a['author_base']['aweme_id'] for a in coll['picked']] == ['OK1']


def test_collect_cross_page_dedup():
    """同一抖音号跨页出现只取一次（翻页窗口内 SPA 可能重发行）。"""
    a = _mk_author('SAME', '重复人')
    pages = [[a, _mk_author('X1', '别人')], [json.loads(json.dumps(a))]]
    coll = bw._square_collect_new_candidates(pages, lambda did: False, '', 5)
    assert [x['author_base']['aweme_id'] for x in coll['picked']] == ['SAME', 'X1']


def test_collect_category_filter():
    """client_side_filter 口径：类目不匹配的候选被滤掉。"""
    page = [_mk_author('S1', '鞋靴人', ('鞋靴箱包',)), _mk_author('D1', '数码人', ('3C数码家电',))]
    coll = bw._square_collect_new_candidates([page], lambda did: False, '鞋靴箱包', 5)
    assert [a['author_base']['aweme_id'] for a in coll['picked']] == ['S1']


# ── 修订2（2026-11-10 增量派单）：员工驱动——_square_decide / _square_write_report ──

_CAND = {
    '达人ID': 'v2_testuid', '抖音号': 'LOVE.yifeifei', '昵称': '幸运佳',
    '粉丝指数': 11101, '30天结算额': [10000.0, 25000.0],
    '联系方式可得性': '有', '主推类目': ['鞋靴箱包', '女鞋'], '达人等级': '4', '城市': '河南·洛阳',
}


def test_decide_accept_inserts_with_judgment(tmp_path, monkeypatch):
    db = _tmp_talents_db(tmp_path, monkeypatch)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '值得联系',
                                   'reason': '月结算过万，类目匹配'})
    assert code == 200 and out['ok'] and out['inserted'] and out['talent_id'].startswith('tal_sq_')
    conn = sqlite3.connect(str(db))
    row = conn.execute('SELECT remark, created_by FROM talents WHERE douyin_id = ?',
                       ('LOVE.yifeifei',)).fetchone()
    conn.close()
    rem = json.loads(row[0])
    assert rem['square_judgment']['verdict'] == '值得联系'
    assert rem['square_judgment']['reason'] == '月结算过万，类目匹配'
    assert rem['square_judgment']['judged_by'] == 'emp_xiaotuo'
    assert row[1].startswith('square_scan_'), 'created_by 口径不变'
    # 再判一次 → duplicate，不入第二行
    out2, code2 = bw._square_decide({'candidate': _CAND, 'verdict': '存疑', 'reason': '再看看'})
    assert code2 == 200 and out2['duplicate'] is True and out2['inserted'] is False
    conn = sqlite3.connect(str(db))
    n = conn.execute('SELECT COUNT(*) FROM talents').fetchone()[0]
    conn.close()
    assert n == 1


def test_decide_reject_writes_audit_not_talent(tmp_path, monkeypatch):
    db = _tmp_talents_db(tmp_path, monkeypatch)
    rejects = tmp_path / 'rejects.jsonl'
    monkeypatch.setattr(bw, 'SQUARE_REJECTS_LOG', rejects)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '排除',
                                   'reason': '近30天基本无成交'})
    assert code == 200 and out['ok'] and out['inserted'] is False
    conn = sqlite3.connect(str(db))
    n = conn.execute('SELECT COUNT(*) FROM talents').fetchone()[0]
    conn.close()
    assert n == 0, '排除不入库'
    lines = rejects.read_text(encoding='utf-8').strip().splitlines()
    assert len(lines) == 1 and '基本无成交' in lines[0] and 'LOVE.yifeifei' in lines[0]


def test_decide_validation(tmp_path, monkeypatch):
    _tmp_talents_db(tmp_path, monkeypatch)
    out, code = bw._square_decide({'candidate': _CAND, 'verdict': '随便', 'reason': 'x'})
    assert code == 400 and out['ok'] is False
    out, code = bw._square_decide({'candidate': {'昵称': '无号'}, 'verdict': '存疑', 'reason': 'x'})
    assert code == 400, '缺抖音号拒绝（缺一不入库）'


def test_write_report_notifications_and_duty(tmp_path, monkeypatch):
    db = tmp_path / 'r.db'
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE notifications (id TEXT, user_id TEXT, agent_id TEXT,
                    type TEXT, title TEXT, content TEXT, read INTEGER, created_at INTEGER)''')
    conn.execute('''CREATE TABLE emp_duties (agent_id TEXT PRIMARY KEY,
                    duty_name TEXT DEFAULT '达人筛选专员',
                    supervisor_agent_id TEXT DEFAULT '', zero_streak INTEGER DEFAULT 0,
                    last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
                    updated_at INTEGER)''')
    conn.execute("INSERT INTO emp_duties VALUES ('emp_xiaotuo', '达人筛选专员', '', 0, 0, '', 0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    bw._square_write_report('今日新入库 3 位', '今日深看5 | 新增3 | ……建议人工过目：甲（值得联系：理由）')
    conn = sqlite3.connect(str(db))
    n = conn.execute("SELECT COUNT(*) FROM notifications WHERE agent_id = 'emp_xiaotuo'").fetchone()[0]
    row = conn.execute("SELECT last_run_summary FROM emp_duties WHERE agent_id = 'emp_xiaotuo'").fetchone()
    conn.close()
    assert n == 1 and row[0] == '今日新入库 3 位'


# ── ②主管协同位（2026-11-10）：日报双投递 + 升级触发器 ──

def _tmp_duty_db(tmp_path, monkeypatch, supervisor_agent_id='', zero_streak=0,
                 name='test.db'):
    """emp_duties + notifications 两表的 tmp 库；openclaw 投递打 spy。"""
    db = tmp_path / name
    conn = sqlite3.connect(str(db))
    conn.execute('''CREATE TABLE talents (
        id TEXT PRIMARY KEY, name TEXT, avatar TEXT, douyin_id TEXT,
        douyin_uid TEXT, city TEXT, level TEXT, followers INTEGER,
        tags TEXT, category TEXT, contact_wechat TEXT, contact_phone TEXT,
        remark TEXT, created_by TEXT, platform TEXT, status TEXT,
        cooperation_status TEXT, created_at INTEGER, updated_at INTEGER)''')
    conn.execute('''CREATE TABLE emp_duties (
        agent_id TEXT PRIMARY KEY, duty_name TEXT DEFAULT '', scope TEXT DEFAULT '',
        category TEXT DEFAULT '', quota INTEGER DEFAULT 5, goods TEXT DEFAULT '',
        duty_desc TEXT DEFAULT '[]', supervisor TEXT DEFAULT '',
        supervisor_agent_id TEXT DEFAULT '', boundary TEXT DEFAULT '',
        zero_streak INTEGER DEFAULT 0,
        last_run_at INTEGER DEFAULT 0, last_run_summary TEXT DEFAULT '',
        created_at INTEGER, updated_at INTEGER)''')
    conn.execute('''CREATE TABLE notifications (
        id TEXT, user_id TEXT, agent_id TEXT, type TEXT, title TEXT,
        content TEXT, read INTEGER, created_at INTEGER)''')
    conn.execute("INSERT INTO emp_duties (agent_id, supervisor_agent_id, zero_streak) "
                 "VALUES ('emp_xiaotuo', ?, ?)", (supervisor_agent_id, zero_streak))
    conn.commit()
    conn.close()
    monkeypatch.setattr(bw, 'DB_PATH', db)
    delivered = []
    monkeypatch.setattr(bw, '_square_deliver_to_agent',
                        lambda aid, msg: delivered.append((aid, msg)))
    return db, delivered


def _notifs(db):
    conn = sqlite3.connect(str(db))
    rows = conn.execute('SELECT agent_id, type, title FROM notifications '
                        'ORDER BY created_at, rowid').fetchall()
    conn.close()
    return rows


def test_report_dual_delivery_to_supervisor(tmp_path, monkeypatch):
    """有 supervisor_agent_id ⇒ 管理员通知 + 抄送通知 + 主管会话投递，三件事都发生。"""
    db, delivered = _tmp_duty_db(tmp_path, monkeypatch, supervisor_agent_id='emp_helen')
    bw._square_write_report('鞋靴箱包深看5：值得联系3、存疑2', '今日深看5 | 新增5', 'emp_xiaotuo')
    rows = _notifs(db)
    assert ('emp_xiaotuo', 'task_reminder') in [(r[0], r[1]) for r in rows]
    cc = [r for r in rows if r[0] == 'emp_helen' and r[1] == 'task_reminder']
    assert len(cc) == 1 and '抄送' in cc[0][2]
    assert delivered and delivered[0][0] == 'emp_helen'
    # last_run_* 回写
    conn = sqlite3.connect(str(db))
    summary = conn.execute("SELECT last_run_summary FROM emp_duties "
                           "WHERE agent_id='emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert '值得联系3' in summary


def test_report_no_supervisor_no_cc(tmp_path, monkeypatch):
    """无 supervisor_agent_id ⇒ 只有管理员通知，不投递主管。"""
    db, delivered = _tmp_duty_db(tmp_path, monkeypatch)
    bw._square_write_report('鞋靴箱包深看5：值得联系2、存疑3', '今日深看5 | 新增5', 'emp_xiaotuo')
    rows = _notifs(db)
    assert len(rows) == 1 and rows[0][0] == 'emp_xiaotuo'
    assert delivered == []


def test_report_escalation_on_second_zero_day(tmp_path, monkeypatch):
    """streak=1 + 本次值得联系=0 ⇒ 升级通知（主管+管理员各一条），streak 闩锁清零。"""
    db, _ = _tmp_duty_db(tmp_path, monkeypatch, supervisor_agent_id='emp_helen',
                         zero_streak=1)
    bw._square_write_report('鞋靴箱包深看5：值得联系0、存疑5', '今日深看5 | 新增0', 'emp_xiaotuo')
    rows = _notifs(db)
    esc = [r for r in rows if r[1] == 'escalation']
    assert len(esc) == 2, f'应给管理员+主管各一条升级通知，实际 {esc}'
    assert {r[0] for r in esc} == {'emp_xiaotuo', 'emp_helen'}
    conn = sqlite3.connect(str(db))
    streak = conn.execute("SELECT zero_streak FROM emp_duties "
                          "WHERE agent_id='emp_xiaotuo'").fetchone()[0]
    conn.close()
    assert streak == 0   # 闩锁：通知一次后重新累计


def test_report_no_escalation_when_worthy_or_first_zero(tmp_path, monkeypatch):
    """值得联系>0 ⇒ streak 清零无升级；首次 0 ⇒ streak=1 无升级。"""
    db, _ = _tmp_duty_db(tmp_path, monkeypatch, supervisor_agent_id='emp_helen',
                         zero_streak=1)
    bw._square_write_report('鞋靴箱包深看5：值得联系2、存疑3', 'x', 'emp_xiaotuo')
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT zero_streak FROM emp_duties "
                        "WHERE agent_id='emp_xiaotuo'").fetchone()[0] == 0
    conn.close()
    assert not [r for r in _notifs(db) if r[1] == 'escalation']

    db2, delivered2 = _tmp_duty_db(tmp_path, monkeypatch, supervisor_agent_id='emp_helen',
                                   name='test2.db')
    bw._square_write_report('鞋靴箱包深看5：值得联系0、存疑5', 'x', 'emp_xiaotuo')
    conn = sqlite3.connect(str(db2))
    assert conn.execute("SELECT zero_streak FROM emp_duties "
                        "WHERE agent_id='emp_xiaotuo'").fetchone()[0] == 1
    conn.close()
    assert not [r for r in _notifs(db2) if r[1] == 'escalation']
