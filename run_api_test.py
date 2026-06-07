#!/usr/bin/env python3
"""自动化：生成Token → 启动服务器 → 运行接口测试"""
import os, sys, json, time, subprocess, signal

BASE_URL = "http://localhost:8080"
DATA_DIR = os.path.join(os.path.expanduser('~'), '.solobrave-data')
SECRET_FILE = os.path.join(DATA_DIR, '.secret')

# ─── 1. 确保 JWT Secret 存在 ───────────────────────
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)
if not os.path.isfile(SECRET_FILE):
    import uuid
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    with open(SECRET_FILE, 'w') as f:
        f.write(secret)
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass

# ─── 2. 生成测试 Token ─────────────────────────────
sys.path.insert(0, '.')
import importlib.util
spec = importlib.util.spec_from_file_location('solobrave_server', 'solobrave-server.py')
server_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server_mod)
generate_token = server_mod.generate_token
_get_jwt_secret = server_mod._get_jwt_secret
_get_jwt_secret()  # 初始化
TOKEN = generate_token("test-user-001", "admin")
print(f"[Token] {TOKEN[:40]}...")

# ─── 3. 启动服务器（后台）──────────────────────────
print("[Server] 启动中...")
server_proc = subprocess.Popen(
    [sys.executable, 'solobrave-server.py', '8080'],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd='.',
)

# 等待服务器就绪
time.sleep(3)
try:
    import urllib.request
    req = urllib.request.Request(f"{BASE_URL}/")
    urllib.request.urlopen(req, timeout=5)
    print("[Server] 就绪 ✅")
except Exception as e:
    print(f"[Server] 启动可能失败: {e}")
    # 继续尝试

# ─── 4. 运行 curl 测试 ─────────────────────────────
print("\n" + "="*60)
print("[接口测试开始]")
print("="*60)

TESTS = [
    ("1-无认证", "POST", "/api/douyin/parse",
     {"Content-Type": "application/json"},
     '{"url":"https://v.douyin.com/5msCxiOndsU/"}',
     401, lambda b: b'"success"' in b and b'false' in b),

    ("2-错误Token", "POST", "/api/douyin/parse",
     {"Authorization": "Bearer invalid", "Content-Type": "application/json"},
     '{"url":"https://v.douyin.com/5msCxiOndsU/"}',
     401, lambda b: b'"success"' in b and b'false' in b),

    ("3-缺url参数", "POST", "/api/douyin/parse",
     {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
     '{}',
     400, lambda b: b'"success"' in b and b'false' in b),

    ("4-正常解析", "POST", "/api/douyin/parse",
     {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
     '{"url":"https://v.douyin.com/5msCxiOndsU/","transcribe":false}',
     200, lambda b: b'"success"' in b and b'true' in b and b'"video_info"' in b),

    ("5-转录模式", "POST", "/api/douyin/parse",
     {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
     '{"url":"https://v.douyin.com/5msCxiOndsU/","transcribe":true}',
     200, lambda b: b'"success"' in b and b'true' in b),
]

passed = 0
failed = 0
for name, method, path, headers, body, expect_code, check_fn in TESTS:
    import urllib.request
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body.encode('utf-8'), method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        code = resp.status
        resp_body = resp.read()
    except urllib.error.HTTPError as e:
        code = e.code
        resp_body = e.read()
    except Exception as e:
        code = 0
        resp_body = str(e).encode()

    ok = (code == expect_code) and check_fn(resp_body)
    status = "PASS" if ok else "FAIL"
    if ok:
        passed += 1
    else:
        failed += 1
    print(f"  [{status}] {name}: HTTP {code} (期望{expect_code})")
    if not ok:
        print(f"         响应: {resp_body[:200]}")

# ─── 5. 清理 ───────────────────────────────────────
print("\n" + "="*60)
print(f"结果: {passed} PASS, {failed} FAIL")
print("="*60)

server_proc.terminate()
try:
    server_proc.wait(timeout=3)
except:
    server_proc.kill()
print("[Server] 已停止")

sys.exit(1 if failed > 0 else 0)
