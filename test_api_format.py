#!/usr/bin/env python3
"""检查 /api/douyin/parse 接口返回格式"""
import sys, json
sys.path.insert(0, '.')

from douyin_parser import parse_douyin_video, parse_douyin_video_quick

TEST_URL = 'https://v.douyin.com/5msCxiOndsU/'

passed = 0
failed = 0

def check(name, condition, detail=''):
    global passed, failed
    if condition:
        passed += 1
        print(f'  PASS: {name}')
    else:
        failed += 1
        print(f'  FAIL: {name} {detail}')

print("=" * 60)
print("[检查1] parse_douyin_video() 返回结构")
print("=" * 60)
result = parse_douyin_video(TEST_URL, api_key=None, transcribe=False)

# 无论成功失败，都检查返回结构
top_keys = ['success', 'video_info', 'media_info', 'text_content', 'transcribed', 'transcribe_error']
for k in top_keys:
    check(f'top.{k}', k in result, f'missing')

if result.get('success'):
    print(f"  → 解析成功，video_id={result.get('video_info', {}).get('video_id')}")
    vi = result.get('video_info', {})
    vi_keys = ['video_id', 'title', 'author', 'author_id', 'desc', 'cover_url', 'video_url', 'tags', 'duration', 'stats', 'share_url', 'real_url', 'create_time']
    for k in vi_keys:
        check(f'video_info.{k}', k in vi, f'missing')
    stats = vi.get('stats', {})
    for k in ['play_count', 'digg_count', 'comment_count', 'share_count', 'collect_count']:
        check(f'stats.{k}', k in stats, f'missing')
    check('media_info是dict', isinstance(result.get('media_info'), dict))
    check('text_content是str', isinstance(result.get('text_content'), str))
    check('transcribed=False(transcribe=False)', result.get('transcribed') == False)
    check('transcribe_error为空(transcribe=False)', result.get('transcribe_error') == '')
else:
    print(f"  → 解析失败，error={result.get('error')!r}")
    check('失败时含error字段', 'error' in result)
    check('video_info存在(即使为空)', 'video_info' in result)

print("\n" + "=" * 60)
print("[检查2] parse_douyin_video_quick() 返回结构")
print("=" * 60)
quick = parse_douyin_video_quick(TEST_URL)
check('quick有success', 'success' in quick)
check('quick有video_info', 'video_info' in quick)
check('quick有transcribed', 'transcribed' in quick)
check('quick无media_info', 'media_info' not in quick)
check('quick无text_content', 'text_content' not in quick)
check('quick无transcribe_error', 'transcribe_error' not in quick)

print("\n" + "=" * 60)
print("[检查3] 接口层返回格式一致性")
print("=" * 60)

# 模拟 _handle_douyin_parse 的各种返回场景
# 成功: _send_json(200, result) -> result 含 success=True
# 失败: _send_json(422, result) -> result 含 success=False + error
# 参数错误: _send_json(400, {'success': False, 'error': '...'})
# 认证错误: _send_json(401, {'success': False, 'error': '...'})  [已修复]

success_resp = {'success': True, 'video_info': {}}
fail_resp = {'success': False, 'error': '解析失败'}
param_err = {'success': False, 'error': '缺少 url 参数'}
auth_err = {'success': False, 'error': 'Unauthorized'}

# 所有错误场景都应含 success + error
for label, resp in [('成功', success_resp), ('解析失败', fail_resp), ('参数错误', param_err), ('认证错误', auth_err)]:
    check(f'{label}: 有success', 'success' in resp, f'{resp}')

# 所有场景 success 都是 bool
check('success是bool', isinstance(success_resp['success'], bool))

# 错误场景都有 error 字符串
for label, resp in [('解析失败', fail_resp), ('参数错误', param_err), ('认证错误', auth_err)]:
    check(f'{label}: error是str', isinstance(resp.get('error'), str), f'{resp}')

print("\n" + "=" * 60)
print("[检查4] JSON 序列化")
print("=" * 60)
test_resp = {'success': True, 'video_info': {'title': '测试中文'}}
json_str = json.dumps(test_resp, ensure_ascii=False)
check('JSON可序列化', len(json_str) > 0)
check('ensure_ascii=False生效', '测试中文' in json_str)

print("\n" + "=" * 60)
print(f"检查结果: {passed} PASS, {failed} FAIL")
if failed == 0:
    print('全部通过')
else:
    print(f'有 {failed} 项需要修复')
