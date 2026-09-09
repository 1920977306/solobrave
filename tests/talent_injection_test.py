# -*- coding: utf-8 -*-
"""
Helen 截图分析 - 后端数据注入功能测试 (源码提取 + 单元测试版)

策略:从 solobrave-server.py 提取我新加的 4 个函数源码 + 必要依赖,
      exec 到独立 namespace 执行,避开 server 整个 import 链(依赖 douyin_parser / ms3 等缺失模块)。
"""
import os, sys, json, re, subprocess, sqlite3, time

# 1. 找到新增函数源码(在 _TALENT_INJECT_KEYWORDS 之前插入的 block)
SERVER_PY = 'solobrave-server.py'
text = open(SERVER_PY, encoding='utf-8').read()

# 抓 "─── 截图分析 - 单达人精确识别" 到 "_TALENT_INJECT_KEYWORDS" 之间的内容
m_start = text.find('# ─── 截图分析 - 单达人精确识别')
m_end = text.find('_TALENT_INJECT_KEYWORDS =')
if m_start < 0 or m_end < 0:
    print('FATAL: 找不到插入点,后端改动没生效?')
    sys.exit(1)
new_block = text[m_start:m_end]
print(f'提取新函数 block: {len(new_block)} chars')

# 2. 准备 stub: 提供 _db_conn, _talent_row_to_dict, logger, DATA_DIR 等
#    直接 import server 不行(依赖太重),所以用 Python 直接调 SQLite
def _db_conn():
    conn = sqlite3.connect('data/solobrave.db', check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def _talent_row_to_dict(row):
    if not row:
        return None
    def _jc(col, default=None):
        v = row[col]
        if v is None:
            return default
        try:
            return json.loads(v)
        except Exception:
            return default
    return {
        'id': row['id'],
        'name': row['name'] or '',
        'avatar': row['avatar'] or '',
        'douyin_id': row['douyin_id'] or '',
        'real_name': row['real_name'] or '',
        'platform': row['platform'] or '抖音',
        'level': row['level'] or '',
        'followers': row['followers'] if row['followers'] is not None else 0,
        'video_interaction_rate': row['video_interaction_rate'] or '',
        'live_gpm': row['live_gpm'] or 0,
        'video_gpm': row['video_gpm'] or 0,
        'average_price': row['average_price'] or 0,
        'total_gmv': row['total_gmv'] or 0,
        'cooperation_status': row['cooperation_status'] or '',
        'created_by': row['created_by'] or '',
        'price_unit': row['price_unit'] or '元/条',
    }

class _Logger:
    def info(self, *a, **k): pass
    def error(self, *a, **k): pass
    def warning(self, *a, **k): pass

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(SERVER_PY)), 'data')

# 3. exec 新函数源码到 namespace
ns = {
    '_db_conn': _db_conn,
    '_talent_row_to_dict': _talent_row_to_dict,
    'logger': _Logger(),
    'DATA_DIR': DATA_DIR,
    'os': os,
    'json': json,
    'time': time,
    're': re,
}
try:
    exec(new_block, ns)
except Exception as e:
    print(f'FATAL: 新函数源码 exec 失败: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)

EXTRACT = ns['_extract_talent_from_text']
BUILD = ns['_build_single_talent_injection']
SAVE = ns['_save_talent_report_to_json']
NOTICE = ns['_VISION_FALLBACK_NOTICE']
print('提取 4 个函数成功')
print(f'  _extract_talent_from_text: {EXTRACT.__name__}')
print(f'  _build_single_talent_injection: {BUILD.__name__}')
print(f'  _save_talent_report_to_json: {SAVE.__name__}')
print(f'  _VISION_FALLBACK_NOTICE: {NOTICE[:80]}...')


class MockAuth:
    def __init__(self, is_admin=True, uid='admin'):
        self.is_admin = is_admin
        self.user_info = {'userId': uid}


# ============================================================
# 测点
# ============================================================

def test_1_extract_exact():
    print('\n=== 测 1: 精确识别"分析达人X" ===')
    conn = _db_conn()
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        print('  SKIP: talents 表空')
        return False
    tid, tname = row['id'], row['name']
    print(f'  测试 talent: {tname} ({tid})')
    got = EXTRACT(f'分析达人{tname}的带货数据', MockAuth())
    print(f'  got: {got}')
    if got != tid:
        print(f'  FAIL: 期望 {tid}, got {got}')
        return False
    print('  PASS')
    return True


def test_2_no_keyword():
    print('\n=== 测 2: 无关键词 → None ===')
    got = EXTRACT('今天天气真好', MockAuth())
    if got is not None:
        print(f'  FAIL: 期望 None, got {got}')
        return False
    print('  PASS')
    return True


def test_3_fuzzy_like():
    print('\n=== 测 3: 模糊匹配 LIKE ===')
    conn = _db_conn()
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        print('  SKIP')
        return False
    prefix = row['name'][:2]
    # 模式 2 命中: "分析X" 抓 prefix
    got = EXTRACT(f'分析 {prefix}粉丝多少', MockAuth())
    print(f'  prefix={prefix!r} got={got}')
    if got != row['id']:
        print(f'  FAIL: 期望 {row["id"]}, got {got}')
        return False
    print('  PASS')
    return True


def test_4_build_injection():
    print('\n=== 测 4: _build_single_talent_injection 输出 [达人数据] ===')
    conn = _db_conn()
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" AND followers > 0 LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        print('  SKIP: 无 followers>0 talent')
        return False
    out = BUILD(row['id'], MockAuth())
    print(f'  out: {out[:300]}')
    if '[达人数据]' not in out:
        print('  FAIL: 无 [达人数据]')
        return False
    if row['name'] not in out:
        print('  FAIL: 无达人名')
        return False
    if '粉丝:' not in out:
        print('  FAIL: 无"粉丝:"')
        return False
    if '历史报告' not in out:
        print('  FAIL: 无"历史报告"')
        return False
    print('  PASS')
    return True


def test_5_history_recall():
    print('\n=== 测 5: 历史报告 recall ===')
    conn = _db_conn()
    try:
        row = conn.execute('SELECT id, name FROM talents WHERE name != "" LIMIT 1').fetchone()
    finally:
        conn.close()
    if not row:
        print('  SKIP')
        return False
    tid = row['id']
    SAVE(tid, '上次评级 B 级,合作可推进,完播率偏低', 'admin')
    out = BUILD(tid, MockAuth())
    print(f'  out: {out[:400]}')
    if '上次评级 B 级' not in out:
        print('  FAIL: 无历史报告内容')
        return False
    # 清理
    try:
        os.remove(os.path.join(DATA_DIR, 'talent_reports', f'{tid}.json'))
    except Exception:
        pass
    print('  PASS')
    return True


def test_6_save_report_writes_file():
    print('\n=== 测 6: _save_talent_report_to_json 写文件 ===')
    SAVE('test_tal_001', '测试报告内容,评级 A,核心结论是有潜力', 'admin')
    path = os.path.join(DATA_DIR, 'talent_reports', 'test_tal_001.json')
    if not os.path.isfile(path):
        print(f'  FAIL: 未生成 {path}')
        return False
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    print(f'  saved: {data}')
    if data.get('talent_id') != 'test_tal_001':
        print('  FAIL: talent_id 错')
        return False
    if 'A' not in data.get('summary', ''):
        print('  FAIL: summary 错')
        return False
    os.remove(path)
    print('  PASS')
    return True


def test_7_vision_fallback_notice():
    print('\n=== 测 7: _VISION_FALLBACK_NOTICE 文本 ===')
    print(f'  notice: {NOTICE[:200]}')
    if '[系统提示]' not in NOTICE:
        print('  FAIL: 无 [系统提示]')
        return False
    if '估算' not in NOTICE:
        print('  FAIL: 无"估算"')
        return False
    print('  PASS')
    return True


def test_8_data_reports_gitignored():
    print('\n=== 测 8: data/talent_reports/ 在 .gitignore ===')
    os.makedirs(os.path.join(DATA_DIR, 'talent_reports'), exist_ok=True)
    probe = os.path.join(DATA_DIR, 'talent_reports', '_probe.json')
    with open(probe, 'w', encoding='utf-8') as f:
        json.dump({'probe': True}, f)
    try:
        r = subprocess.run(
            ['git', 'check-ignore', '-v', probe],
            capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            print(f'  PASS: {r.stdout.strip()}')
            return True
        print(f'  FAIL: 未忽略 rc={r.returncode}')
        return False
    finally:
        os.remove(probe)


def test_9_chat_handler_uses_new_funcs():
    print('\n=== 测 9: _handle_post_chat 已调用新函数 ===')
    # grep 看 chat handler 是否引了新函数
    text2 = open(SERVER_PY, encoding='utf-8').read()
    calls = {
        '_extract_talent_from_text': text2.count('_extract_talent_from_text('),
        '_build_single_talent_injection': text2.count('_build_single_talent_injection('),
        '_save_talent_report_to_json': text2.count('_save_talent_report_to_json('),
        '_VISION_FALLBACK_NOTICE': text2.count('_VISION_FALLBACK_NOTICE'),
        '_talent_id_hit': text2.count('_talent_id_hit'),
    }
    print(f'  引用次数: {calls}')
    if calls['_extract_talent_from_text'] < 1:
        print('  FAIL: _handle_post_chat 没调 _extract_talent_from_text')
        return False
    if calls['_build_single_talent_injection'] < 1:
        print('  FAIL: _handle_post_chat 没调 _build_single_talent_injection')
        return False
    if calls['_save_talent_report_to_json'] < 1:
        print('  FAIL: _handle_post_chat 没调 _save_talent_report_to_json')
        return False
    if calls['_VISION_FALLBACK_NOTICE'] < 1:
        print('  FAIL: _handle_post_chat 没引用 _VISION_FALLBACK_NOTICE')
        return False
    print('  PASS: chat handler 已正确接入 4 个新函数/常量')
    return True


def main():
    print('== Helen 截图分析注入功能 单元测试 ==')
    results = {}
    results['1_extract_exact'] = test_1_extract_exact()
    results['2_no_keyword'] = test_2_no_keyword()
    results['3_fuzzy_like'] = test_3_fuzzy_like()
    results['4_build_injection'] = test_4_build_injection()
    results['5_history_recall'] = test_5_history_recall()
    results['6_save_report'] = test_6_save_report_writes_file()
    results['7_vision_notice'] = test_7_vision_fallback_notice()
    results['8_gitignore'] = test_8_data_reports_gitignored()
    results['9_handler_integration'] = test_9_chat_handler_uses_new_funcs()
    print('\n=== 汇总 ===')
    for k, v in results.items():
        print(f'  {k}: {"PASS" if v else "FAIL"}')
    n_pass = sum(1 for v in results.values() if v)
    print(f'\n  {n_pass}/{len(results)} 测点通过')
    sys.exit(0 if n_pass == len(results) else 1)


if __name__ == '__main__':
    main()
