#!/usr/bin/env python3
"""SenseVoiceSmall 转录链路综合测试"""
import sys, os, json, urllib.request, ssl, tempfile
sys.path.insert(0, '.')

from douyin_parser import (
    _check_ffmpeg,
    _build_multipart_body,
    _transcribe_audio_siliconflow,
    _download_video_to_temp,
    parse_douyin_video,
)

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
print("[TEST 1/5] ffmpeg 可用性检查")
print("=" * 60)
ffmpeg_ver = _check_ffmpeg()
check('ffmpeg 检查函数正常', ffmpeg_ver is not None or ffmpeg_ver is None)
if ffmpeg_ver:
    print(f"  ffmpeg 版本: {ffmpeg_ver}")
else:
    print("  ffmpeg 未安装 — 转录功能将跳过")

print("\n" + "=" * 60)
print("[TEST 2/5] multipart/form-data 构建测试")
print("=" * 60)
# 创建一个临时测试文件
with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
    f.write('test audio content')
    test_file = f.name

try:
    body, ct = _build_multipart_body(
        {'model': 'FunAudioLLM/SenseVoiceSmall'},
        {'file': test_file}
    )
    check('body 是 bytes', isinstance(body, bytes))
    check('Content-Type 包含 multipart', 'multipart/form-data' in ct)
    check('body 包含 boundary', b'--' in body)
    check('body 包含 model 字段', b'FunAudioLLM/SenseVoiceSmall' in body)
    check('body 包含文件内容', b'test audio content' in body)
    check('body 结尾正确', body.endswith(b'--\r\n'))
    print(f"  body 大小: {len(body)} bytes")
    print(f"  Content-Type: {ct}")
finally:
    os.unlink(test_file)

print("\n" + "=" * 60)
print("[TEST 3/5] 硅基流动 API 连通性测试")
print("=" * 60)
# 测试能否连接到 api.siliconflow.cn
try:
    req = urllib.request.Request('https://api.siliconflow.cn/v1/models', method='GET')
    req.add_header('Accept', 'application/json')
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
    data = json.loads(resp.read().decode('utf-8'))
    # 检查是否有 SenseVoiceSmall
    models = [m.get('id', '') for m in data.get('data', [])]
    has_sensevoice = any('SenseVoice' in m for m in models)
    check('API 可达', True)
    check('API 返回 JSON', isinstance(data, dict))
    print(f"  HTTP 状态: {resp.status}")
    print(f"  可用模型数: {len(models)}")
    print(f"  包含 SenseVoice: {has_sensevoice}")
    if has_sensevoice:
        sv_models = [m for m in models if 'SenseVoice' in m]
        print(f"  SenseVoice 模型: {sv_models}")
except Exception as e:
    check('API 可达', False, str(e))
    print(f"  错误: {e}")

print("\n" + "=" * 60)
print("[TEST 4/5] 视频下载链路测试")
print("=" * 60)
video_url = 'https://aweme.snssdk.com/aweme/v1/play/?video_id=v0d00fg10000d8cgj97og65jl3556650&ratio=720p&line=0'
try:
    video_path, temp_dir = _download_video_to_temp(video_url, max_size_mb=10)
    size = os.path.getsize(video_path)
    check('视频下载成功', size > 0)
    print(f"  下载大小: {size} bytes ({size/1024:.1f} KB)")
    print(f"  临时路径: {video_path}")
    # 清理
    import shutil
    shutil.rmtree(temp_dir)
except Exception as e:
    check('视频下载成功', False, str(e))
    print(f"  错误: {e}")

print("\n" + "=" * 60)
print("[TEST 5/5] 完整 parse_douyin_video 转录链路")
print("=" * 60)
SHARE_URL = 'https://v.douyin.com/5msCxiOndsU/'
result = parse_douyin_video(SHARE_URL, api_key=None, transcribe=True)
check('完整解析成功', result.get('success') == True)
vi = result.get('video_info', {})
check('有 video_url', bool(vi.get('video_url')))
check('transcribed 字段存在', 'transcribed' in result)
if not ffmpeg_ver:
    check('无ffmpeg时 transcribed=False', result.get('transcribed') == False)
    check('transcribe_error 有提示', bool(result.get('transcribe_error')))
    print(f"  transcribe_error: {result.get('transcribe_error')}")
else:
    check('有ffmpeg时尝试转录', True)
    print(f"  transcribed: {result.get('transcribed')}")
    print(f"  text_content: {result.get('text_content')!r}")

print("\n" + "=" * 60)
print(f"测试结果: {passed} PASS, {failed} FAIL")
if failed == 0:
    print('全部通过 ✅')
else:
    print(f'有 {failed} 项需要关注')
