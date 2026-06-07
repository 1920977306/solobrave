#!/usr/bin/env python3
"""方式3: 直接用 curl 手动测试 /api/douyin/parse"""
import os, sys, json, time, subprocess, urllib.request, urllib.error

BASE_URL = "http://localhost:8080"
TEST_URL = "https://v.douyin.com/5msCxiOndsU/"

# 1. 生成Token
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('solobrave_server', 'solobrave-server.py')
server_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_mod)
server_mod._get_jwt_secret()
TOKEN = server_mod.generate_token("test-user-001", "admin")
print(f"[Token] {TOKEN[:50]}...")

# 2. 启动服务器
print("[Server] 启动中...")
proc = subprocess.Popen([sys.executable, 'solobrave-server.py', '8080'],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)

def curl_json(method, path, headers, body):
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body.encode() if body else None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return e.code, json.loads(body)
        except:
            return e.code, body
    except Exception as e:
        return 0, str(e)

def show_curl(method, path, headers, body):
    hstr = ' \\\n  '.join(f'-H "{k}: {v}"' for k, v in headers.items())
    print(f"\n$ curl -X {method} {BASE_URL}{path} \\")
    print(f"  {hstr} \\")
    if body:
        print(f"  -d '{body}'")

def show_resp(code, data):
    print(f"\n# HTTP {code}")
    print(json.dumps(data, ensure_ascii=False, indent=2))

# ─── 测试1: 无认证 ──────────────────────────────────
show_curl("POST", "/api/douyin/parse",
          {"Content-Type": "application/json"},
          '{"url":"' + TEST_URL + '"}')
code, data = curl_json("POST", "/api/douyin/parse",
                       {"Content-Type": "application/json"},
                       '{"url":"' + TEST_URL + '"}')
show_resp(code, data)

# ─── 测试2: 正常解析（不转录）────────────────────────
show_curl("POST", "/api/douyin/parse",
          {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
          '{"url":"' + TEST_URL + '","transcribe":false}')
code, data = curl_json("POST", "/api/douyin/parse",
                       {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                       '{"url":"' + TEST_URL + '","transcribe":false}')
show_resp(code, data)

# 提取 video_url 供转录测试
video_url = ""
if isinstance(data, dict) and data.get('success'):
    video_url = data.get('video_info', {}).get('video_url', '')
    print(f"\n[提取] video_url = {video_url[:80]}...")

# ─── 测试3: 转录模式 ────────────────────────────────
show_curl("POST", "/api/douyin/parse",
          {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
          '{"url":"' + TEST_URL + '","transcribe":true}')
code, data = curl_json("POST", "/api/douyin/parse",
                       {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                       '{"url":"' + TEST_URL + '","transcribe":true}')
show_resp(code, data)

# ─── 测试4: 直接转录（如有video_url）─────────────────
if video_url:
    show_curl("POST", "/api/douyin/transcribe",
              {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
              '{"video_url":"' + video_url + '"}')
    code, data = curl_json("POST", "/api/douyin/transcribe",
                           {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                           '{"video_url":"' + video_url + '"}')
    show_resp(code, data)

# 清理
proc.terminate()
try:
    proc.wait(timeout=3)
except:
    proc.kill()
print("\n[Server] 已停止")
