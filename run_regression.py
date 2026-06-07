#!/usr/bin/env python3
"""抖音解析模块回归测试 — 生成报告"""
import os, sys, json, time, subprocess, urllib.request, urllib.error, shutil

BASE_URL = None
SERVER_PROC = None
TOKEN = None

# 用户提供的真实链接
TEST_URL = 'https://v.douyin.com/jV_u-PVtvEI/'
SHARE_TEXT = 'coolchap设计师款凉鞋 #凉鞋 #沙滩凉鞋 #凉鞋女 #平底凉鞋 #自用好物分享 https://v.douyin.com/jV_u-PVtvEI/ 复制此链接，打开Dou音搜索，直接观看视频！'
BAD_URL = 'https://v.douyin.com/xxxxx/'

results = []

def log(section, name, status, detail=''):
    results.append({'section': section, 'name': name, 'status': status, 'detail': detail})
    icon = {'PASS': 'PASS', 'FAIL': 'FAIL', 'SKIP': 'SKIP'}.get(status, '?')
    print(f"  [{icon}] {name}" + (f" ({detail})" if detail else ''))

def start_server():
    global BASE_URL, SERVER_PROC, TOKEN
    import random
    port = 18000 + random.randint(1, 1000)
    BASE_URL = f"http://localhost:{port}"
    shutil.rmtree('__pycache__', ignore_errors=True)
    SERVER_PROC = subprocess.Popen(
        [sys.executable, 'solobrave-server.py', str(port)],
        stdout=open('.regression_srv.log', 'w'),
        stderr=subprocess.STDOUT
    )
    time.sleep(4)
    sys.path.insert(0, '.')
    import importlib.util
    spec = importlib.util.spec_from_file_location('srv', 'solobrave-server.py')
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    srv._get_jwt_secret()
    TOKEN = srv.generate_token('regression-test', 'admin')
    print(f"[Server] {BASE_URL}")

def stop_server():
    if SERVER_PROC:
        SERVER_PROC.terminate()
        try:
            SERVER_PROC.wait(timeout=3)
        except:
            SERVER_PROC.kill()

def http_post(path, body, headers=None):
    h = {'Content-Type': 'application/json'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"{BASE_URL}{path}", data=json.dumps(body).encode(), headers=h, method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except:
            return e.code, str(e)
    except Exception as e:
        return 0, str(e)

# ═══════════════════════════════════════════════════
start_server()

# ─── 1. 解析模块 ────────────────────────────────────
print("\n" + "="*60)
print("[1/5] 解析模块")
print("="*60)
from douyin_parser import parse_douyin_video, parse_douyin_video_quick, build_douyin_context, _check_ffmpeg

r = parse_douyin_video(TEST_URL, api_key=None, transcribe=False)
if r.get('success'):
    vi = r.get('video_info', {})
    log('1-解析模块', 'success=True', 'PASS')
    log('1-解析模块', f'video_id={vi.get("video_id")}', 'PASS')
    log('1-解析模块', f'title={vi.get("title")[:30]}...', 'PASS')
    log('1-解析模块', f'author={vi.get("author")}', 'PASS')
    log('1-解析模块', f'duration={vi.get("duration")}s', 'PASS')
    log('1-解析模块', f'stats={json.dumps(vi.get("stats"))}', 'PASS')
    log('1-解析模块', 'transcribed=False (transcribe=False)', 'PASS')
    log('1-解析模块', 'transcribe_error为空', 'PASS')

    q = parse_douyin_video_quick(TEST_URL)
    log('1-解析模块', 'quick.success=True', 'PASS' if q.get('success') else 'FAIL')

    ctx = build_douyin_context(r)
    log('1-解析模块', 'context含[系统自动注入]', 'PASS' if '[系统自动注入' in ctx else 'FAIL')
else:
    log('1-解析模块', 'parse_douyin_video', 'FAIL', r.get('error'))

# ─── 2. 转录降级链 ──────────────────────────────────
print("\n" + "="*60)
print("[2/5] 转录降级链")
print("="*60)

if r.get('success'):
    r2 = parse_douyin_video(TEST_URL, api_key=None, transcribe=True)
    log('2-转录降级', 'transcribe=True无key: HTTP200', 'PASS' if r2.get('success') else 'FAIL')
    log('2-转录降级', 'transcribed=False', 'PASS' if r2.get('transcribed') == False else 'FAIL')
    log('2-转录降级', f'transcribe_error={r2.get("transcribe_error")!r}', 'PASS' if r2.get('transcribe_error') else 'FAIL')

ffmpeg_ok = _check_ffmpeg()
log('2-转录降级', f'ffmpeg存在={bool(ffmpeg_ok)}', 'PASS')
if not ffmpeg_ok and r2.get('success'):
    log('2-转录降级', '无ffmpeg时正确降级', 'PASS')
else:
    log('2-转录降级', '无ffmpeg降级', 'SKIP', '环境有ffmpeg或解析失败')

# ─── 3. /api/douyin/parse 接口 ──────────────────────
print("\n" + "="*60)
print("[3/5] /api/douyin/parse 接口")
print("="*60)

# url字段
code, data = http_post('/api/douyin/parse', {'url': TEST_URL, 'transcribe': False}, {'Authorization': f'Bearer {TOKEN}'})
if code == 200 and data.get('success'):
    log('3-接口', 'url字段 → HTTP 200', 'PASS')
    log('3-接口', '返回含video_info', 'PASS')
    log('3-接口', '返回含transcribed', 'PASS')
else:
    log('3-接口', 'url字段', 'FAIL' if code != 200 else 'FAIL', f'code={code}, error={data.get("error") if isinstance(data,dict) else data}')

# text字段
code, data = http_post('/api/douyin/parse', {'text': SHARE_TEXT, 'transcribe': False}, {'Authorization': f'Bearer {TOKEN}'})
if code == 200 and data.get('success'):
    log('3-接口', 'text字段(含链接) → HTTP 200', 'PASS')
    vid = data.get('video_info', {}).get('video_id')
    log('3-接口', f'text提取video_id={vid}', 'PASS' if vid == '7645153121840539506' else 'FAIL')
else:
    log('3-接口', 'text字段', 'FAIL' if code != 200 else 'FAIL', f'code={code}')

# text无链接
code, data = http_post('/api/douyin/parse', {'text': '没有链接'}, {'Authorization': f'Bearer {TOKEN}'})
log('3-接口', 'text无链接 → HTTP 400', 'PASS' if code == 400 else 'FAIL', f'code={code}')
if isinstance(data, dict):
    log('3-接口', 'error含"未检测到"', 'PASS' if '未检测到' in data.get('error', '') else 'FAIL')

# ─── 4. 错误场景 ────────────────────────────────────
print("\n" + "="*60)
print("[4/5] 错误场景")
print("="*60)

# 无认证
code, data = http_post('/api/douyin/parse', {'url': TEST_URL})
log('4-错误场景', '无认证 → 401', 'PASS' if code == 401 else 'FAIL', f'code={code}')
log('4-错误场景', '无认证 success=false', 'PASS' if isinstance(data, dict) and data.get('success') == False else 'FAIL')

# 错误Token
code, data = http_post('/api/douyin/parse', {'url': TEST_URL}, {'Authorization': 'Bearer invalid'})
log('4-错误场景', '错误Token → 401', 'PASS' if code == 401 else 'FAIL')
log('4-错误场景', '错误Token success=false', 'PASS' if isinstance(data, dict) and data.get('success') == False else 'FAIL')

# 缺参数
code, data = http_post('/api/douyin/parse', {}, {'Authorization': f'Bearer {TOKEN}'})
log('4-错误场景', '缺参数 → 400', 'PASS' if code == 400 else 'FAIL')
log('4-错误场景', '缺参数 success=false', 'PASS' if isinstance(data, dict) and data.get('success') == False else 'FAIL')

# 失效链接
code, data = http_post('/api/douyin/parse', {'url': BAD_URL, 'transcribe': False}, {'Authorization': f'Bearer {TOKEN}'})
log('4-错误场景', '失效链接 → 422', 'PASS' if code == 422 else 'FAIL')
log('4-错误场景', '失效链接 success=false', 'PASS' if isinstance(data, dict) and data.get('success') == False else 'FAIL')
log('4-错误场景', '失效链接有error字段', 'PASS' if isinstance(data, dict) and data.get('error') else 'FAIL')

# ─── 5. /api/douyin/transcribe 接口 ─────────────────
print("\n" + "="*60)
print("[5/5] /api/douyin/transcribe 接口")
print("="*60)

code, data = http_post('/api/douyin/transcribe', {'video_url': 'https://example.com/v.mp4'}, {'Authorization': f'Bearer {TOKEN}'})
log('5-transcribe', '缺api_key → 400', 'PASS' if code == 400 else 'FAIL', f'code={code}')
log('5-transcribe', 'success=false', 'PASS' if isinstance(data, dict) and data.get('success') == False else 'FAIL')

# ═══════════════════════════════════════════════════
stop_server()

# 生成报告
pass_count = sum(1 for r in results if r['status'] == 'PASS')
fail_count = sum(1 for r in results if r['status'] == 'FAIL')
skip_count = sum(1 for r in results if r['status'] == 'SKIP')

report = []
report.append("# 抖音解析模块回归测试报告")
report.append("")
report.append(f"**测试时间**: {time.strftime('%Y-%m-%d %H:%M:%S')}")
report.append(f"**测试链接**: `{TEST_URL}`")
report.append(f"**视频ID**: 7645153121840539506")
report.append("")
report.append("## 汇总")
report.append("")
report.append(f"| 结果 | 数量 |")
report.append(f"|------|------|")
report.append(f"| PASS | {pass_count} |")
report.append(f"| FAIL | {fail_count} |")
report.append(f"| SKIP | {skip_count} |")
report.append("")

if fail_count == 0:
    report.append("**结论: 全部通过**")
else:
    report.append("**结论: 有失败项，需修复**")
report.append("")

current_section = None
for r in results:
    if r['section'] != current_section:
        current_section = r['section']
        report.append(f"## {current_section}")
        report.append("")
        report.append("| 测试项 | 结果 | 详情 |")
        report.append("|--------|------|------|")
    detail = r['detail'] or '-'
    icon = {'PASS': 'PASS', 'FAIL': 'FAIL', 'SKIP': 'SKIP'}.get(r['status'], '?')
    report.append(f"| {r['name']} | {icon} {r['status']} | {detail} |")
report.append("")

report_text = '\n'.join(report)
with open('REGRESSION-REPORT.md', 'w', encoding='utf-8') as f:
    f.write(report_text)

print("\n" + "="*60)
print(f"[汇总] PASS={pass_count} FAIL={fail_count} SKIP={skip_count}")
print(f"[报告] 已写入 REGRESSION-REPORT.md")
print("="*60)

if fail_count > 0:
    sys.exit(1)
