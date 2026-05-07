#!/usr/bin/env python3
"""
SoloBrave CORS Proxy Server + OpenClaw Management API
======================================================
替代 python3 -m http.server 8080，提供：
  1. 静态文件服务（从 solobrave-deploy/ 目录）
  2. API 代理端点 POST /api/proxy（解决浏览器 CORS 限制）
  3. OpenClaw 管理 API（创建/列表/删除/更新龙虾 Agent）

启动方式：
  cd ~/Desktop/solobrave-deploy   # 或 git 仓库根目录
  python3 solobrave-server.py

只使用 Python 标准库，无需额外依赖。
"""

import http.server
import json
import os
import subprocess
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
ALLOWED_HTTP_METHODS = {'GET', 'HEAD', 'POST', 'OPTIONS', 'DELETE'}

# AI API 常见域名白名单（可选安全增强，留空则不限制域名，仅限制 HTTPS）
ALLOWED_DOMAINS = []  # 例: ['api.openai.com', 'api.moonshot.cn', 'api.deepseek.com', 'open.bigmodel.cn']

# OpenClaw CLI 路径
OPENCLAW_CLI = '/opt/homebrew/bin/openclaw'
OPENCLAW_TIMEOUT = 30  # CLI 命令超时（秒）


# ─── OpenClaw CLI 辅助函数 ──────────────────────────────
def _run_openclaw(args, cwd=None, input_data=None):
    """执行 openclaw CLI 命令，返回 (success, stdout, stderr, returncode)"""
    cmd = [OPENCLAW_CLI] + args
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=OPENCLAW_TIMEOUT,
            cwd=cwd,
            env=env,
            input=input_data
        )
        return True, result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return False, '', f'OpenClaw CLI not found at {OPENCLAW_CLI}', -1
    except subprocess.TimeoutExpired:
        return False, '', f'Command timed out after {OPENCLAW_TIMEOUT}s', -1
    except PermissionError:
        return False, '', f'Permission denied executing {OPENCLAW_CLI}', -1
    except Exception as e:
        return False, '', str(e), -1


def _openclaw_status():
    """检查 OpenClaw Gateway 状态"""
    # 先检查 CLI 是否存在
    if not os.path.isfile(OPENCLAW_CLI):
        return {
            'available': False,
            'gateway': 'offline',
            'message': f'OpenClaw CLI not found at {OPENCLAW_CLI}',
            'cli': OPENCLAW_CLI
        }

    # 尝试运行 health 检查
    success, stdout, stderr, rc = _run_openclaw(['health'])
    if success and rc == 0:
        try:
            health_data = json.loads(stdout.strip())
            return {
                'available': True,
                'gateway': 'online',
                'health': health_data,
                'cli': OPENCLAW_CLI
            }
        except json.JSONDecodeError:
            # health 命令有输出但不是 JSON，也算在线
            return {
                'available': True,
                'gateway': 'online',
                'health': {'raw': stdout.strip()},
                'cli': OPENCLAW_CLI
            }

    # health 失败，但 CLI 存在，可能是 Gateway 没启动
    return {
        'available': True,
        'gateway': 'offline',
        'message': 'OpenClaw CLI available but Gateway appears offline',
        'cli': OPENCLAW_CLI,
        'error': stderr.strip() if stderr else ''
    }


def _default_models():
    """默认模型列表（当 CLI 不可用时）"""
    return [
        {'id': 'anthropic/claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4'},
        {'id': 'anthropic/claude-3-5-sonnet-20241022', 'name': 'Claude 3.5 Sonnet'},
        {'id': 'openai/gpt-4o', 'name': 'GPT-4o'},
        {'id': 'openai/gpt-4o-mini', 'name': 'GPT-4o Mini'},
        {'id': 'deepseek/deepseek-chat', 'name': 'DeepSeek Chat'},
        {'id': 'deepseek/deepseek-coder', 'name': 'DeepSeek Coder'},
    ]


class SoloBraveHandler(http.server.SimpleHTTPRequestHandler):
    """自定义请求处理器：静态文件 + CORS 代理 + OpenClaw API"""

    def __init__(self, *args, **kwargs):
        # SimpleHTTPRequestHandler 默认以 cwd 为根，这里指定静态目录
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ─── CORS 头 ───────────────────────────────────────
    def _add_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS, HEAD')
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

    # ─── JSON 响应工具 ─────────────────────────────────
    def _send_json(self, code, data):
        """发送 JSON 响应（带 CORS 头）"""
        self.send_response(code)
        self._add_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _send_json_error(self, code, message):
        self._send_json(code, {
            'error': {'message': message, 'type': 'proxy_error', 'code': code}
        })

    # ─── 读取请求体 ────────────────────────────────────
    def _read_body(self):
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            raw = self.rfile.read(content_length)
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return None
        return None

    # ─── 路由 ──────────────────────────────────────────
    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        # API 路由
        if self.path == '/api/proxy':
            self._send_json_error(405, 'POST only')
            return
        if self.path == '/api/openclaw/agents':
            self._handle_openclaw_list_agents()
            return
        if self.path == '/api/openclaw/models':
            self._handle_openclaw_list_models()
            return
        if self.path == '/api/openclaw/status':
            self._handle_openclaw_status()
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
        elif self.path == '/api/openclaw/agents/create':
            self._handle_openclaw_create_agent()
        elif self.path == '/api/openclaw/agents/update':
            self._handle_openclaw_update_agent()
        else:
            self._send_json_error(404, 'Not found. POST only accepted at /api/proxy, /api/openclaw/agents/create, /api/openclaw/agents/update')

    def do_DELETE(self):
        if self.path.startswith('/api/openclaw/agents/'):
            # Extract agent name from path: /api/openclaw/agents/:name
            agent_name = self.path[len('/api/openclaw/agents/'):]
            if agent_name:
                self._handle_openclaw_delete_agent(agent_name)
            else:
                self._send_json_error(400, 'Agent name required')
        else:
            self._send_json_error(404, 'Not found')

    # ─── OpenClaw API 处理器 ────────────────────────────

    def _handle_openclaw_status(self):
        """GET /api/openclaw/status — 检查 OpenClaw 连接状态"""
        status = _openclaw_status()
        self._send_json(200, status)

    def _handle_openclaw_list_agents(self):
        """GET /api/openclaw/agents — 列出所有 Agent"""
        success, stdout, stderr, rc = _run_openclaw(['agents', 'list', '--json'])
        if not success:
            # CLI 不可用
            self._send_json(200, {
                'agents': [],
                'warning': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(200, {
                'agents': [],
                'warning': stderr.strip() or f'Command failed with code {rc}'
            })
            return

        try:
            data = json.loads(stdout.strip())
            # 统一格式：可能是 {"agents": [...]} 或直接是数组
            if isinstance(data, list):
                self._send_json(200, {'agents': data})
            elif isinstance(data, dict) and 'agents' in data:
                self._send_json(200, data)
            else:
                self._send_json(200, {'agents': [], 'raw': data})
        except json.JSONDecodeError:
            # 非JSON输出，尝试解析
            self._send_json(200, {
                'agents': [],
                'raw_output': stdout.strip()
            })

    def _handle_openclaw_create_agent(self):
        """POST /api/openclaw/agents/create — 创建 Agent"""
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Invalid JSON body')
            return

        name = body.get('name', '').strip()
        model = body.get('model', '').strip()
        soul = body.get('soul', '').strip()
        workspace = body.get('workspace', '').strip()

        if not name:
            self._send_json_error(400, 'Agent name is required')
            return
        if not model:
            self._send_json_error(400, 'Model is required')
            return

        # 默认 workspace 路径
        if not workspace:
            home = os.path.expanduser('~')
            workspace = os.path.join(home, '.openclaw', 'agents', name)

        # 1. 创建 workspace 目录
        try:
            os.makedirs(workspace, exist_ok=True)
        except OSError as e:
            self._send_json_error(500, f'Failed to create workspace directory: {str(e)}')
            return

        # 2. 写入 SOUL.md（如果提供了人设）
        if soul:
            soul_path = os.path.join(workspace, 'SOUL.md')
            try:
                with open(soul_path, 'w', encoding='utf-8') as f:
                    f.write(soul)
            except OSError as e:
                self._send_json_error(500, f'Failed to write SOUL.md: {str(e)}')
                return

        # 3. 调用 openclaw agents add
        success, stdout, stderr, rc = _run_openclaw([
            'agents', 'add', name,
            '--workspace', workspace,
            '--model', model,
            '--non-interactive'
        ])

        if not success:
            self._send_json(500, {
                'success': False,
                'error': stderr or 'OpenClaw CLI not available',
                'workspace': workspace
            })
            return

        if rc != 0:
            self._send_json(500, {
                'success': False,
                'error': stderr.strip() or f'Command failed with code {rc}',
                'stdout': stdout.strip(),
                'workspace': workspace
            })
            return

        self._send_json(200, {
            'success': True,
            'name': name,
            'model': model,
            'workspace': workspace,
            'soul_written': bool(soul),
            'output': stdout.strip()
        })

    def _handle_openclaw_update_agent(self):
        """POST /api/openclaw/agents/update — 更新 Agent"""
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Invalid JSON body')
            return

        name = body.get('name', '').strip()
        soul = body.get('soul', '').strip()
        model = body.get('model', '').strip()

        if not name:
            self._send_json_error(400, 'Agent name is required')
            return

        results = {'success': True, 'updates': []}

        # 1. 更新 SOUL.md
        if soul:
            # 找到 agent 的 workspace 目录
            home = os.path.expanduser('~')
            possible_workspaces = [
                os.path.join(home, '.openclaw', 'agents', name),
                os.path.join(home, '.openclaw', name),
            ]

            soul_written = False
            for ws in possible_workspaces:
                soul_path = os.path.join(ws, 'SOUL.md')
                if os.path.isdir(ws):
                    try:
                        with open(soul_path, 'w', encoding='utf-8') as f:
                            f.write(soul)
                        results['updates'].append(f'SOUL.md updated at {ws}')
                        results['workspace'] = ws
                        soul_written = True
                        break
                    except OSError as e:
                        results['updates'].append(f'Failed to write SOUL.md: {str(e)}')

            if not soul_written:
                # 尝试通过 CLI 获取 workspace
                success, stdout, stderr, rc = _run_openclaw(['agents', 'list', '--json'])
                if success and rc == 0:
                    try:
                        agents_data = json.loads(stdout.strip())
                        agents = agents_data if isinstance(agents_data, list) else agents_data.get('agents', [])
                        for agent in agents:
                            agent_name = agent.get('name', agent.get('agentId', ''))
                            if agent_name == name:
                                ws = agent.get('workspace', agent.get('path', ''))
                                if ws:
                                    soul_path = os.path.join(ws, 'SOUL.md')
                                    try:
                                        with open(soul_path, 'w', encoding='utf-8') as f:
                                            f.write(soul)
                                        results['updates'].append(f'SOUL.md updated at {ws}')
                                        results['workspace'] = ws
                                        soul_written = True
                                    except OSError as e:
                                        results['updates'].append(f'Failed to write SOUL.md: {str(e)}')
                                break
                    except (json.JSONDecodeError, KeyError):
                        pass

                if not soul_written:
                    results['updates'].append('Could not find workspace directory for SOUL.md update')

        # 2. 如果 model 变了，需要删除重建
        if model:
            # 先删除旧的
            del_success, del_stdout, del_stderr, del_rc = _run_openclaw(['agents', 'delete', name])
            if del_success and del_rc == 0:
                # 确定原来的 workspace
                ws = results.get('workspace', os.path.join(os.path.expanduser('~'), '.openclaw', 'agents', name))
                # 重新创建
                add_success, add_stdout, add_stderr, add_rc = _run_openclaw([
                    'agents', 'add', name,
                    '--workspace', ws,
                    '--model', model,
                    '--non-interactive'
                ])
                if add_success and add_rc == 0:
                    results['updates'].append(f'Agent recreated with model: {model}')
                else:
                    results['success'] = False
                    results['error'] = add_stderr.strip() or 'Failed to recreate agent with new model'
            else:
                results['updates'].append(f'Delete step: {del_stderr.strip() or "ok"}')
                # 即使删除失败，也尝试 add
                ws = os.path.join(os.path.expanduser('~'), '.openclaw', 'agents', name)
                add_success, add_stdout, add_stderr, add_rc = _run_openclaw([
                    'agents', 'add', name,
                    '--workspace', ws,
                    '--model', model,
                    '--non-interactive'
                ])
                if add_success and add_rc == 0:
                    results['updates'].append(f'Agent created with model: {model}')
                else:
                    results['success'] = False
                    results['error'] = add_stderr.strip() or 'Failed to create agent with new model'

        self._send_json(200, results)

    def _handle_openclaw_delete_agent(self, agent_name):
        """DELETE /api/openclaw/agents/:name — 删除 Agent"""
        success, stdout, stderr, rc = _run_openclaw(['agents', 'delete', agent_name])

        if not success:
            self._send_json(500, {
                'success': False,
                'error': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(500, {
                'success': False,
                'error': stderr.strip() or f'Command failed with code {rc}',
                'output': stdout.strip()
            })
            return

        self._send_json(200, {
            'success': True,
            'name': agent_name,
            'output': stdout.strip()
        })

    def _handle_openclaw_list_models(self):
        """GET /api/openclaw/models — 获取可用模型列表"""
        # 尝试从 CLI 获取
        success, stdout, stderr, rc = _run_openclaw(['models', 'list', '--json'])
        if success and rc == 0:
            try:
                data = json.loads(stdout.strip())
                if isinstance(data, list):
                    self._send_json(200, {'models': data})
                    return
                elif isinstance(data, dict) and 'models' in data:
                    self._send_json(200, data)
                    return
            except json.JSONDecodeError:
                pass

        # CLI 不可用或命令不存在，返回默认列表
        self._send_json(200, {
            'models': _default_models(),
            'source': 'default'
        })

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

    # 检查 OpenClaw CLI
    if os.path.isfile(OPENCLAW_CLI):
        print(f'  🦞 OpenClaw CLI: ✓ ({OPENCLAW_CLI})')
    else:
        print(f'  🦞 OpenClaw CLI: ✗ (not found at {OPENCLAW_CLI})')
        print(f'     OpenClaw management API will return limited data')

    server = http.server.HTTPServer((BIND, PORT), SoloBraveHandler)

    print('=' * 56)
    print('  🚀 SoloBrave Server')
    print('=' * 56)
    print(f'  📂 静态文件: {STATIC_DIR}')
    print(f'  🌐 本机访问:  http://localhost:{PORT}')
    print(f'  🌐 局域网:    http://0.0.0.0:{PORT}')
    print(f'  🔀 API 代理:  POST /api/proxy')
    print(f'  🦞 OpenClaw:  GET/POST/DELETE /api/openclaw/*')
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
