#!/usr/bin/env python3
"""
SoloBrave CORS Proxy Server
============================
替代 python3 -m http.server 8080，提供：
  1. 静态文件服务（从 solobrave-deploy/ 目录）
  2. API 代理端点 POST /api/proxy（解决浏览器 CORS 限制）

启动方式：
  cd ~/Desktop/solobrave-deploy   # 或 git 仓库根目录
  python3 solobrave-server.py

只使用 Python 标准库，无需额外依赖。
"""

import http.server
import json
import os
import ssl
import sys
import urllib.request
import urllib.error
from datetime import datetime

# ─── 配置 ───────────────────────────────────────────────
PORT = 8080
BIND = '0.0.0.0'
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_TIMEOUT = 60  # 秒
ALLOWED_HTTP_METHODS = {'GET', 'HEAD', 'POST', 'OPTIONS'}

# AI API 常见域名白名单（可选安全增强，留空则不限制域名，仅限制 HTTPS）
ALLOWED_DOMAINS = []  # 例: ['api.openai.com', 'api.moonshot.cn', 'api.deepseek.com', 'open.bigmodel.cn']


class SoloBraveHandler(http.server.SimpleHTTPRequestHandler):
    """自定义请求处理器：静态文件 + CORS 代理"""

    def __init__(self, *args, **kwargs):
        # SimpleHTTPRequestHandler 默认以 cwd 为根，这里指定静态目录
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ─── CORS 头 ───────────────────────────────────────
    def _add_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, HEAD')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Target-URL')
        self.send_header('Access-Control-Max-Age', '86400')

    def _send_cors_preflight(self):
        """处理 OPTIONS 预检请求"""
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    # ─── 日志 ──────────────────────────────────────────
    def log_message(self, format, *args):
        timestamp = datetime.now().strftime('%H:%M:%S')
        msg = format % args
        print(f'  [{timestamp}] {msg}', flush=True)

    # ─── 路由 ──────────────────────────────────────────
    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        # API 路由：GET 不需要代理
        if self.path == '/api/proxy':
            self._send_json_error(405, 'POST only')
            return
        # 静态文件
        super().do_GET()

    def do_HEAD(self):
        if self.path == '/api/proxy':
            self._send_json_error(405, 'POST only')
            return
        super().do_HEAD()

    def do_POST(self):
        if self.path == '/api/proxy':
            self._handle_proxy()
        else:
            self._send_json_error(404, 'Not found. POST only accepted at /api/proxy')

    # ─── 代理核心逻辑 ──────────────────────────────────
    def _handle_proxy(self):
        """处理 /api/proxy 请求"""
        # 1. 读取 X-Target-URL
        target_url = self.headers.get('X-Target-URL', '')
        if not target_url:
            self._send_json_error(400, 'Missing X-Target-URL header')
            return

        # 2. 安全检查：只允许 HTTPS
        if not target_url.startswith('https://'):
            self._send_json_error(403, 'Only HTTPS targets are allowed')
            return

        # 3. 可选域名白名单检查
        if ALLOWED_DOMAINS:
            from urllib.parse import urlparse
            host = urlparse(target_url).hostname or ''
            if not any(host == d or host.endswith('.' + d) for d in ALLOWED_DOMAINS):
                self._send_json_error(403, f'Domain {host} not in allowed list')
                return

        # 4. 读取请求体
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        # 5. 构建转发请求头
        forward_headers = {}
        auth = self.headers.get('Authorization')
        if auth:
            forward_headers['Authorization'] = auth
        content_type = self.headers.get('Content-Type', 'application/json')
        if content_type:
            forward_headers['Content-Type'] = content_type
        if body:
            forward_headers['Content-Length'] = str(len(body))

        # 6. 发起请求
        print(f'  🔄 Proxy -> {target_url}', flush=True)
        try:
            req = urllib.request.Request(
                target_url,
                data=body,
                headers=forward_headers,
                method='POST'
            )
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)

            # 7. 读取响应
            resp_body = resp.read()
            resp_content_type = resp.headers.get('Content-Type', 'application/json')

            # 8. 返回代理响应（带 CORS 头）
            self.send_response(resp.status)
            self._add_cors_headers()
            self.send_header('Content-Type', resp_content_type)
            self.end_headers()
            self.wfile.write(resp_body)
            print(f'  ✅ Proxy OK ({resp.status}) <- {target_url}', flush=True)

        except urllib.error.HTTPError as e:
            # AI API 返回了 HTTP 错误码（401/429/500 等）
            status = e.code
            try:
                err_body = e.read()
            except Exception:
                err_body = b'{}'
            
            error_messages = {
                401: 'API Key 无效或认证失败',
                403: 'API 访问被拒绝',
                429: '请求过于频繁，请稍后再试',
                500: 'AI 服务端内部错误',
                502: 'AI 服务网关错误',
                503: 'AI 服务暂不可用',
            }
            detail = error_messages.get(status, f'HTTP {status}')
            print(f'  ❌ Proxy Error ({status}): {detail} <- {target_url}', flush=True)

            self.send_response(status)
            self._add_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            # 尝试透传原始错误体，否则包装一个
            try:
                json.loads(err_body)
                self.wfile.write(err_body)
            except Exception:
                self.wfile.write(json.dumps({
                    'error': {'message': detail, 'type': 'proxy_error', 'code': status}
                }).encode())

        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, 'reason') else str(e)
            print(f'  ❌ Proxy Network Error: {reason} <- {target_url}', flush=True)
            self._send_json_error(502, f'Network error: {reason}')

        except TimeoutError:
            print(f'  ❌ Proxy Timeout ({PROXY_TIMEOUT}s) <- {target_url}', flush=True)
            self._send_json_error(504, f'Request timed out after {PROXY_TIMEOUT}s')

        except Exception as e:
            print(f'  ❌ Proxy Unexpected Error: {e} <- {target_url}', flush=True)
            self._send_json_error(500, f'Internal proxy error: {str(e)}')

    # ─── 工具方法 ──────────────────────────────────────
    def _send_json_error(self, code, message):
        self.send_response(code)
        self._add_cors_headers()
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'error': {'message': message, 'type': 'proxy_error', 'code': code}
        }).encode())


# ─── 启动 ───────────────────────────────────────────────
def main():
    # 检查静态目录
    if not os.path.isdir(STATIC_DIR):
        print(f'⚠️  静态文件目录不存在: {STATIC_DIR}')
        print(f'   请确保 solobrave-server.py 和 index.html 在同一目录')
        sys.exit(1)

    # 检查关键文件
    index_file = os.path.join(STATIC_DIR, 'index.html')
    if not os.path.isfile(index_file):
        print(f'⚠️  找不到 index.html: {index_file}')
        sys.exit(1)

    server = http.server.HTTPServer((BIND, PORT), SoloBraveHandler)

    print('=' * 56)
    print('  🚀 SoloBrave Server')
    print('=' * 56)
    print(f'  📂 静态文件: {STATIC_DIR}')
    print(f'  🌐 本机访问:  http://localhost:{PORT}')
    print(f'  🌐 局域网:    http://0.0.0.0:{PORT}')
    print(f'  🔀 API 代理:  POST /api/proxy')
    print(f'  ⏱️  超时设置:  {PROXY_TIMEOUT}s')
    print('=' * 56)
    print('  Ctrl+C 停止服务\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n\n  🛑 服务已停止')
        server.server_close()


if __name__ == '__main__':
    main()
