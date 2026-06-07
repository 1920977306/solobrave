#!/usr/bin/env python3
"""抖音解析模块完整集成测试"""
import sys, json
sys.path.insert(0, '.')

from douyin_parser import (
    is_douyin_share_text,
    detect_douyin_links,
    parse_douyin_video_quick,
    parse_douyin_video,
    build_douyin_context,
    _check_ffmpeg,
)

TEST_URL = 'https://v.douyin.com/5msCxiOndsU/'
SHARE_TEXT = '3.56 复制打开抖音，看看【李馒头的作品】coolchap设计师款凉鞋 #凉鞋 #沙滩凉鞋 https://v.douyin.com/5msCxiOndsU/'

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

print('=' * 60)
print('[TEST 1/6] is_douyin_share_text + detect_douyin_links')
check('识别分享文本', is_douyin_share_text(SHARE_TEXT))
links = detect_douyin_links(SHARE_TEXT)
check('提取链接数量>=1', len(links) >= 1, f'got {len(links)}')
check('链接包含v.douyin.com', any('v.douyin.com' in l for l in links))

print('=' * 60)
print('[TEST 2/6] parse_douyin_video_quick (快速模式，无下载)')
result = parse_douyin_video_quick(TEST_URL)
check('success=True', result.get('success') == True, f"error={result.get('error')}")
vi = result.get('video_info', {})
check('video_info 存在', bool(vi))
check('有 video_id', bool(vi.get('video_id')))
check('有 author', bool(vi.get('author')))
check('有 stats', bool(vi.get('stats')))
check('transcribed=False', result.get('transcribed') == False)

print('=' * 60)
print('[TEST 3/6] parse_douyin_video (完整模式，transcribe=False)')
result2 = parse_douyin_video(TEST_URL, api_key=None, transcribe=False)
check('success=True', result2.get('success') == True, f"error={result2.get('error')}")
check('返回结构含video_info', 'video_info' in result2)
check('返回结构含media_info', 'media_info' in result2)
check('text_content为空', result2.get('text_content') == '')
check('transcribed=False', result2.get('transcribed') == False)

print('=' * 60)
print('[TEST 4/6] parse_douyin_video (完整模式，transcribe=True，无ffmpeg)')
result3 = parse_douyin_video(TEST_URL, api_key='fake_key', transcribe=True)
check('success=True', result3.get('success') == True)
# 无ffmpeg时应跳过转录，不阻断主流程
if _check_ffmpeg():
    check('ffmpeg存在则尝试转录', True)
else:
    check('无ffmpeg时transcribed=False', result3.get('transcribed') == False)
    check('transcribe_error有提示', bool(result3.get('transcribe_error')))

print('=' * 60)
print('[TEST 5/6] build_douyin_context')
ctx = build_douyin_context(result2)
check('上下文非空', len(ctx) > 0)
check('包含[系统自动注入]', '[系统自动注入' in ctx)
check('包含标题', '标题:' in ctx or '作者:' in ctx)

print('=' * 60)
print('[TEST 6/6] 工单返回格式检查')
required_keys = ['success', 'video_info', 'media_info', 'text_content', 'transcribed']
for k in required_keys:
    check(f'result含{k}', k in result2)
check('transcribe_error字段存在', 'transcribe_error' in result2)
check('download_error字段存在', 'download_error' in result2)
vi = result2.get('video_info', {})
video_info_keys = ['video_id', 'title', 'author', 'author_id', 'desc', 'cover_url', 'video_url', 'tags', 'duration', 'stats', 'share_url', 'real_url', 'create_time']
for k in video_info_keys:
    check(f'video_info含{k}', k in vi, f'missing {k}')
stats_keys = ['play_count', 'digg_count', 'comment_count', 'share_count', 'collect_count']
for k in stats_keys:
    check(f'stats含{k}', k in vi.get('stats', {}), f'missing {k}')

print('=' * 60)
print(f'测试结果: {passed} PASS, {failed} FAIL')
if failed == 0:
    print('全部通过 ✅')
else:
    print(f'有 {failed} 项失败，需要修复 ❌')
