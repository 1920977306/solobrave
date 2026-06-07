#!/usr/bin/env python3
"""测试 /api/douyin/parse 的 text 字段支持"""
import os, sys, json, time, subprocess, urllib.request, urllib.error

# 1. 启动服务器
print("[启动服务器...]")
proc = subprocess.Popen([sys.executable, 'solobrave-server.py', '8080'],
                        stdout=open('srv.log', 'w'), stderr=subprocess.STDOUT)
time.sleep(4)

# 2. 生成Token
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('srv', 'solobrave-server.py')
srv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(srv)
srv._get_jwt_secret()
token = srv.generate_token('test-001', 'admin')

def request(path, body):
    req = urllib.request.Request(f'http://localhost:8080{path}',
        data=json.dumps(body).encode(),
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
        method='POST')
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except:
            return e.code, str(e)

# 3. 测试 text 字段
print("\n[TEST] text=分享文本（含链接）")
code, data = request('/api/douyin/parse', {
    'text': '7.43 Pjm:/ 复制打开抖音，看看【xxx的视频】https://v.douyin.com/5msCxiOndsU/',
    'transcribe': False
})
print(f"  HTTP {code}")
print(f"  success: {data.get('success')}")
print(f"  video_id: {data.get('video_info', {}).get('video_id')}")

# 4. 测试 text 字段（无链接）
print("\n[TEST] text=纯文字（无链接）")
code, data = request('/api/douyin/parse', {
    'text': '这是一段没有链接的普通文字'
})
print(f"  HTTP {code}")
print(f"  error: {data.get('error') if isinstance(data, dict) else data}")

# 5. 测试 url 字段（兼容旧用法）
print("\n[TEST] url=直接链接")
code, data = request('/api/douyin/parse', {
    'url': 'https://v.douyin.com/5msCxiOndsU/',
    'transcribe': False
})
print(f"  HTTP {code}")
print(f"  success: {data.get('success')}")

# 清理
proc.terminate()
try:
    proc.wait(timeout=3)
except:
    proc.kill()
print("\n[服务器已停止]")
