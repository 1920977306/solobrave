#!/usr/bin/env python3
"""
SoloBrave Server — Auth + CORS Proxy + OpenClaw Management API
==============================================================
功能：
  1. 静态文件服务
  2. 认证系统（JWT + 用户管理）
  3. Agent 数据存储（JSON 文件）
  4. 聊天记录存储
  5. API 代理端点 POST /api/proxy
  6. OpenClaw 管理 API

只使用 Python 标准库，无需额外依赖。
数据存储目录: ~/.solobrave-data/
"""

import http.server
import json
import os
import subprocess
import ssl
import sys
import urllib.request
import urllib.error
import hashlib
import hmac
import base64
import uuid
import time
import fcntl
from datetime import datetime
from urllib.parse import urlparse, unquote

# ─── 配置 ───────────────────────────────────────────────
PORT = 8080
BIND = '0.0.0.0'
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))
PROXY_TIMEOUT = 60  # 秒
ALLOWED_HTTP_METHODS = {'GET', 'HEAD', 'POST', 'OPTIONS', 'DELETE'}
ALLOWED_DOMAINS = []  # 域名白名单，留空不限制

# OpenClaw CLI 路径
OPENCLAW_CLI = '/opt/homebrew/bin/openclaw'
OPENCLAW_TIMEOUT = 30

# 数据存储目录
DATA_DIR = os.path.join(os.path.expanduser('~'), '.solobrave-data')
SECRET_FILE = os.path.join(DATA_DIR, '.secret')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')
GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')
CHATS_DIR = os.path.join(DATA_DIR, 'chats')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')

# JWT 配置
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天


# ─── 数据存储层 ─────────────────────────────────────────

def _ensure_data_dir():
    """确保数据目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CHATS_DIR, exist_ok=True)


def _read_json(filepath, default=None):
    """读取 JSON 文件"""
    if not os.path.isfile(filepath):
        return default if default is not None else None
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default if default is not None else None


def _write_json(filepath, data):
    """写入 JSON 文件（加文件锁）"""
    _ensure_data_dir()
    tmp_path = filepath + '.tmp'
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        os.replace(tmp_path, filepath)
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ─── JWT 工具（简化实现） ───────────────────────────────

def _get_secret():
    """获取或生成 JWT 签名密钥"""
    if os.path.isfile(SECRET_FILE):
        try:
            with open(SECRET_FILE, 'r') as f:
                secret = f.read().strip()
                if secret:
                    return secret.encode('utf-8')
        except OSError:
            pass
    # 首次启动，生成随机密钥
    _ensure_data_dir()
    secret = uuid.uuid4().hex + uuid.uuid4().hex
    with open(SECRET_FILE, 'w') as f:
        f.write(secret)
    # 限制文件权限
    try:
        os.chmod(SECRET_FILE, 0o600)
    except OSError:
        pass
    return secret.encode('utf-8')


JWT_SECRET = None  # 延迟初始化


def _get_jwt_secret():
    global JWT_SECRET
    if JWT_SECRET is None:
        JWT_SECRET = _get_secret()
    return JWT_SECRET


def _base64url_encode(data):
    """Base64URL 编码（无填充）"""
    if isinstance(data, str):
        data = data.encode('utf-8')
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('utf-8')


def _base64url_decode(s):
    """Base64URL 解码"""
    if isinstance(s, str):
        s = s.encode('utf-8')
    # 补齐填充
    padding = 4 - len(s) % 4
    if padding != 4:
        s += b'=' * padding
    return base64.urlsafe_b64decode(s)


def generate_token(user_id, role):
    """生成 JWT token"""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "role": role,
        "exp": int(time.time()) + JWT_EXPIRE_SECONDS,
        "iat": int(time.time())
    }

    header_b64 = _base64url_encode(json.dumps(header, separators=(',', ':')))
    payload_b64 = _base64url_encode(json.dumps(payload, separators=(',', ':')))

    signing_input = f"{header_b64}.{payload_b64}"
    signature = hmac.new(
        _get_jwt_secret(),
        signing_input.encode('utf-8'),
        hashlib.sha256
    ).digest()
    signature_b64 = _base64url_encode(signature)

    return f"{header_b64}.{payload_b64}.{signature_b64}"


def verify_token(token):
    """验证 JWT token，返回 {userId, role} 或 None"""
    if not token:
        return None
    parts = token.split('.')
    if len(parts) != 3:
        return None
    try:
        header_b64, payload_b64, signature_b64 = parts

        # 验证签名
        signing_input = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(
            _get_jwt_secret(),
            signing_input.encode('utf-8'),
            hashlib.sha256
        ).digest()
        actual_sig = _base64url_decode(signature_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        # 解码 payload
        payload = json.loads(_base64url_decode(payload_b64))

        # 检查过期
        if payload.get('exp', 0) < time.time():
            return None

        return {
            'userId': payload.get('sub'),
            'role': payload.get('role')
        }
    except Exception:
        return None


# ─── 密码哈希 ──────────────────────────────────────────

def hash_password(password, salt=None):
    """哈希密码，返回 (hash, salt)"""
    if salt is None:
        salt = uuid.uuid4().hex[:16]
    h = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    return h, salt


def verify_password(password, pwd_hash, salt):
    """验证密码"""
    h, _ = hash_password(password, salt)
    return hmac.compare_digest(h, pwd_hash)


# ─── 用户管理 ──────────────────────────────────────────

def _load_users():
    """加载用户列表"""
    users = _read_json(USERS_FILE, [])
    return users if isinstance(users, list) else []


def _save_users(users):
    """保存用户列表"""
    _write_json(USERS_FILE, users)


def _find_user(users, key, value):
    """在用户列表中查找用户"""
    for u in users:
        if u.get(key) == value:
            return u
    return None


def _init_default_admin():
    """首次启动创建默认管理员"""
    users = _load_users()
    if len(users) == 0:
        pwd_hash, salt = hash_password('admin123')
        admin = {
            'id': 'user_' + uuid.uuid4().hex[:8],
            'username': 'admin',
            'passwordHash': pwd_hash,
            'passwordSalt': salt,
            'role': 'admin',
            'displayName': '管理员',
            'avatar': 0,
            'agentQuota': 999,
            'apiQuota': 99999,
            'createdAt': datetime.now().isoformat()
        }
        _save_users([admin])
        print('  🔑 默认管理员账号: admin / admin123，请尽快修改密码')
        return admin
    return None


# ─── Agent 管理 ─────────────────────────────────────────

def _load_agents():
    """加载 Agent 列表"""
    agents = _read_json(AGENTS_FILE, [])
    return agents if isinstance(agents, list) else []


def _save_agents(agents):
    """保存 Agent 列表"""
    _write_json(AGENTS_FILE, agents)


# ─── 群组管理 ──────────────────────────────────────────

def _load_groups():
    """加载群组列表"""
    groups = _read_json(GROUPS_FILE, [])
    return groups if isinstance(groups, list) else []


def _save_groups(groups):
    """保存群组列表"""
    _write_json(GROUPS_FILE, groups)


def _find_group(groups, key, value):
    """在群组列表中查找群组"""
    for g in groups:
        if g.get(key) == value:
            return g
    return None


# ─── 聊天记录 ──────────────────────────────────────────

def _load_chat(agent_id):
    """加载某 Agent 的聊天记录"""
    filepath = os.path.join(CHATS_DIR, f'{agent_id}.json')
    return _read_json(filepath, [])


def _save_chat(agent_id, messages):
    """保存某 Agent 的聊天记录"""
    filepath = os.path.join(CHATS_DIR, f'{agent_id}.json')
    _write_json(filepath, messages)


# ─── OpenClaw CLI 辅助函数 ──────────────────────────────

def _run_openclaw(args, cwd=None, input_data=None):
    """执行 openclaw CLI 命令"""
    cmd = [OPENCLAW_CLI] + args
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=OPENCLAW_TIMEOUT, cwd=cwd, env=env, input=input_data
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
    if not os.path.isfile(OPENCLAW_CLI):
        return {
            'available': False, 'gateway': 'offline',
            'message': f'OpenClaw CLI not found at {OPENCLAW_CLI}',
            'cli': OPENCLAW_CLI
        }
    success, stdout, stderr, rc = _run_openclaw(['health'])
    if success and rc == 0:
        try:
            health_data = json.loads(stdout.strip())
            return {
                'available': True, 'gateway': 'online',
                'health': health_data, 'cli': OPENCLAW_CLI
            }
        except json.JSONDecodeError:
            return {
                'available': True, 'gateway': 'online',
                'health': {'raw': stdout.strip()}, 'cli': OPENCLAW_CLI
            }
    return {
        'available': True, 'gateway': 'offline',
        'message': 'OpenClaw CLI available but Gateway appears offline',
        'cli': OPENCLAW_CLI,
        'error': stderr.strip() if stderr else ''
    }


def _default_models():
    """默认模型列表"""
    return [
        {'id': 'anthropic/claude-sonnet-4-20250514', 'name': 'Claude Sonnet 4'},
        {'id': 'anthropic/claude-3-5-sonnet-20241022', 'name': 'Claude 3.5 Sonnet'},
        {'id': 'openai/gpt-4o', 'name': 'GPT-4o'},
        {'id': 'openai/gpt-4o-mini', 'name': 'GPT-4o Mini'},
        {'id': 'deepseek/deepseek-chat', 'name': 'DeepSeek Chat'},
        {'id': 'deepseek/deepseek-coder', 'name': 'DeepSeek Coder'},
    ]


# ─── 认证中间件 ────────────────────────────────────────

class AuthResult:
    """认证结果"""
    def __init__(self, user_info=None, error=None, status=401):
        self.user_info = user_info  # {userId, role}
        self.error = error
        self.status = status
        self.user_record = None  # 完整用户记录

    @property
    def is_authenticated(self):
        return self.user_info is not None

    @property
    def is_admin(self):
        return self.user_info and self.user_info.get('role') == 'admin'

    def load_user_record(self):
        if self.user_info:
            users = _load_users()
            self.user_record = _find_user(users, 'id', self.user_info['userId'])
        return self.user_record


def _authenticate(headers):
    """从请求头中提取并验证 token"""
    auth_header = headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        return AuthResult(error='未登录或 token 已过期', status=401)
    token = auth_header[7:]
    user_info = verify_token(token)
    if user_info is None:
        return AuthResult(error='未登录或 token 已过期', status=401)
    return AuthResult(user_info=user_info)


def _require_admin(auth):
    """检查是否是管理员"""
    if not auth.is_authenticated:
        return auth.error, auth.status
    if not auth.is_admin:
        return '权限不足', 403
    return None, None


# ─── 请求处理器 ────────────────────────────────────────

class SoloBraveHandler(http.server.SimpleHTTPRequestHandler):
    """自定义请求处理器：静态文件 + 认证 + CORS 代理 + OpenClaw API"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    # ─── CORS ───────────────────────────────────────────
    def _add_cors_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, DELETE, OPTIONS, HEAD, PUT')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Target-URL, X-AI-API-Key')
        self.send_header('Access-Control-Max-Age', '86400')

    def _send_cors_preflight(self):
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    # ─── 日志 ──────────────────────────────────────────
    def log_message(self, format, *args):
        timestamp = datetime.now().strftime('%H:%M:%S')
        msg = format % args
        print(f'  [{timestamp}] {msg}', flush=True)

    # ─── JSON 响应 ─────────────────────────────────────
    def _send_json(self, code, data):
        self.send_response(code)
        self._add_cors_headers()
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode('utf-8'))

    def _send_json_error(self, code, message):
        self._send_json(code, {'error': {'message': message, 'type': 'proxy_error', 'code': code}})

    def _send_auth_error(self, message, status=401):
        self._send_json(status, {'error': message})

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
        path = self.path.split('?')[0]  # 去掉 query string

        # Auth routes (no auth required)
        if path == '/api/auth/me':
            self._handle_auth_me()
            return

        # Public OpenClaw routes - now require auth
        if path == '/api/openclaw/status':
            self._handle_auth_required_get(path)
            return
        if path == '/api/openclaw/agents':
            self._handle_auth_required_get(path)
            return
        if path == '/api/openclaw/models':
            self._handle_auth_required_get(path)
            return

        # Agents API
        if path == '/api/agents':
            self._handle_get_agents()
            return
        if path.startswith('/api/agents/'):
            agent_id = path[len('/api/agents/'):]
            if agent_id:
                self._handle_get_agent(agent_id)
                return

        # Users API
        if path == '/api/users':
            self._handle_get_users()
            return
        if path.startswith('/api/users/'):
            user_id = path[len('/api/users/'):]
            if user_id:
                self._handle_get_user(user_id)
                return

        # Chat API
        if path.startswith('/api/chat/'):
            agent_id = path[len('/api/chat/'):]
            if agent_id:
                self._handle_get_chat(agent_id)
                return

        # Groups API
        if path == '/api/groups':
            self._handle_get_groups()
            return
        if path.startswith('/api/groups/'):
            sub = path[len('/api/groups/'):]
            # /api/groups/:id/history
            if sub.endswith('/history'):
                group_id = sub[:-len('/history')]
                if group_id:
                    self._handle_get_group_history(group_id)
                    return
            # /api/groups/:id
            if '/' not in sub:
                if sub:
                    self._handle_get_group(sub)
                    return

        # Proxy (GET not allowed)
        if path == '/api/proxy':
            self._send_json_error(405, 'POST only')
            return

        # Static files
        super().do_GET()

    def do_HEAD(self):
        if self.path == '/api/proxy':
            self._send_json_error(405, 'POST only')
            return
        super().do_HEAD()

    def do_POST(self):
        path = self.path.split('?')[0]

        # Auth routes
        if path == '/api/auth/login':
            self._handle_auth_login()
            return
        if path == '/api/auth/register':
            self._handle_auth_register()
            return
        if path == '/api/auth/change-password':
            self._handle_change_password()
            return

        # Proxy (requires auth)
        if path == '/api/proxy':
            self._handle_proxy()
            return

        # Write SOUL.md/IDENTITY.md to OpenClaw agent workspace
        if path == '/api/openclaw/write-agent-docs':
            self._handle_write_agent_docs()
            return
        if path == '/api/openclaw/write-soul':
            self._handle_write_soul()
            return

        # OpenClaw (requires auth)
        if path == '/api/openclaw/agents/create':
            self._handle_auth_required_post(path)
            return
        if path == '/api/openclaw/agents/update':
            self._handle_auth_required_post(path)
            return

        # Agents API
        if path == '/api/agents':
            self._handle_create_agent()
            return

        # Groups API
        if path == '/api/groups':
            self._handle_create_group()
            return
        if path.startswith('/api/groups/'):
            sub = path[len('/api/groups/'):]
            # /api/groups/:id/chat
            if sub.endswith('/chat'):
                group_id = sub[:-len('/chat')]
                if group_id:
                    self._handle_group_chat(group_id)
                    return
            # /api/groups/:id/members
            if sub.endswith('/members'):
                group_id = sub[:-len('/members')]
                if group_id:
                    self._handle_add_group_member(group_id)
                    return

        # Chat API
        if path.startswith('/api/chat/'):
            agent_id = path[len('/api/chat/'):]
            if agent_id:
                self._handle_post_chat(agent_id)
                return

        self._send_json_error(404, 'Not found')

    def do_PUT(self):
        path = self.path.split('?')[0]

        # Groups API
        if path == '/api/groups':
            self._handle_batch_save_groups()
            return
        if path.startswith('/api/groups/'):
            group_id = path[len('/api/groups/'):]
            if group_id:
                self._handle_update_group(group_id)
                return

        # Agents API
        if path.startswith('/api/agents/'):
            agent_id = path[len('/api/agents/'):]
            if agent_id:
                self._handle_update_agent(agent_id)
                return

        # Users API
        if path.startswith('/api/users/'):
            user_id = path[len('/api/users/'):]
            if user_id:
                self._handle_update_user(user_id)
                return

        self._send_json_error(404, 'Not found')

    def do_DELETE(self):
        path = self.path.split('?')[0]

        # OpenClaw
        if path.startswith('/api/openclaw/agents/'):
            agent_name = path[len('/api/openclaw/agents/'):]
            if agent_name:
                self._handle_auth_required_delete(path)
                return

        # Groups API
        if path.startswith('/api/groups/'):
            sub = path[len('/api/groups/'):]
            # /api/groups/:id/members/:empId
            parts = sub.split('/')
            if len(parts) == 2 and parts[1].startswith('members'):
                pass  # handled below
            elif len(parts) == 3 and parts[1] == 'members':
                # /api/groups/:groupId/members/:empId
                self._handle_remove_group_member(parts[0], parts[2])
                return
            elif len(parts) == 1 and parts[0]:
                # /api/groups/:id
                self._handle_delete_group(parts[0])
                return

        # Agents API
        if path.startswith('/api/agents/'):
            agent_id = path[len('/api/agents/'):]
            if agent_id:
                self._handle_delete_agent(agent_id)
                return

        # Users API
        if path.startswith('/api/users/'):
            user_id = path[len('/api/users/'):]
            if user_id:
                self._handle_delete_user(user_id)
                return

        # Chat API
        if path.startswith('/api/chat/'):
            # /api/chat/:agentId/:msgId
            parts = path[len('/api/chat/'):].split('/')
            if len(parts) == 2:
                self._handle_delete_chat_message(parts[0], parts[1])
                return

        self._send_json_error(404, 'Not found')

    # ─── Auth-required passthrough for OpenClaw routes ──
    def _handle_auth_required_get(self, path):
        """需要认证的 GET 路由"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        # 原有 OpenClaw 处理逻辑
        if path == '/api/openclaw/status':
            self._handle_openclaw_status()
        elif path == '/api/openclaw/agents':
            self._handle_openclaw_list_agents()
        elif path == '/api/openclaw/models':
            self._handle_openclaw_list_models()

    def _handle_auth_required_post(self, path):
        """需要认证的 POST 路由"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if path == '/api/openclaw/agents/create':
            self._handle_openclaw_create_agent()
        elif path == '/api/openclaw/agents/update':
            self._handle_openclaw_update_agent()

    def _handle_auth_required_delete(self, path):
        """需要认证的 DELETE 路由"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent_name = path[len('/api/openclaw/agents/'):]
        self._handle_openclaw_delete_agent(agent_name)

    # ═══════════════════════════════════════════════════
    # 认证 API
    # ═══════════════════════════════════════════════════

    def _handle_auth_login(self):
        """POST /api/auth/login"""
        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        username = body.get('username', '').strip()
        password = body.get('password', '')

        if not username or not password:
            self._send_json(400, {'error': '用户名和密码不能为空'})
            return

        users = _load_users()
        user = _find_user(users, 'username', username)

        if not user or not verify_password(password, user.get('passwordHash', ''), user.get('passwordSalt', '')):
            self._send_json(401, {'error': '用户名或密码错误'})
            return

        # 生成 token
        token = generate_token(user['id'], user.get('role', 'employee'))

        self._send_json(200, {
            'token': token,
            'user': {
                'id': user['id'],
                'username': user['username'],
                'role': user.get('role', 'employee'),
                'displayName': user.get('displayName', user['username']),
                'avatar': user.get('avatar', 0)
            }
        })

    def _handle_auth_register(self):
        """POST /api/auth/register（需要 admin token）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        username = body.get('username', '').strip()
        password = body.get('password', '')
        role = body.get('role', 'employee')
        display_name = body.get('displayName', username)

        if not username or not password:
            self._send_json(400, {'error': '用户名和密码不能为空'})
            return

        if len(password) < 4:
            self._send_json(400, {'error': '密码至少 4 个字符'})
            return

        if role not in ('admin', 'employee'):
            role = 'employee'

        users = _load_users()
        if _find_user(users, 'username', username):
            self._send_json(409, {'error': '用户名已存在'})
            return

        pwd_hash, salt = hash_password(password)
        new_user = {
            'id': 'user_' + uuid.uuid4().hex[:8],
            'username': username,
            'passwordHash': pwd_hash,
            'passwordSalt': salt,
            'role': role,
            'displayName': display_name,
            'avatar': 0,
            'agentQuota': 10 if role == 'employee' else 999,
            'apiQuota': 1000 if role == 'employee' else 99999,
            'createdAt': datetime.now().isoformat()
        }
        users.append(new_user)
        _save_users(users)

        self._send_json(201, {
            'user': {
                'id': new_user['id'],
                'username': new_user['username'],
                'role': new_user['role'],
                'displayName': new_user['displayName'],
                'avatar': new_user['avatar'],
                'agentQuota': new_user['agentQuota'],
                'apiQuota': new_user['apiQuota']
            }
        })

    def _handle_auth_me(self):
        """GET /api/auth/me"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        auth.load_user_record()
        user = auth.user_record
        if not user:
            self._send_auth_error('用户不存在', 401)
            return

        self._send_json(200, {
            'id': user['id'],
            'username': user['username'],
            'role': user.get('role', 'employee'),
            'displayName': user.get('displayName', user['username']),
            'avatar': user.get('avatar', 0),
            'agentQuota': user.get('agentQuota', 10),
            'apiQuota': user.get('apiQuota', 1000)
        })

    def _handle_change_password(self):
        """POST /api/auth/change-password"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        old_password = body.get('oldPassword', '')
        new_password = body.get('newPassword', '')

        if not old_password or not new_password:
            self._send_json(400, {'error': '旧密码和新密码不能为空'})
            return

        if len(new_password) < 4:
            self._send_json(400, {'error': '新密码至少 4 个字符'})
            return

        users = _load_users()
        user = _find_user(users, 'id', auth.user_info['userId'])
        if not user:
            self._send_auth_error('用户不存在', 401)
            return

        if not verify_password(old_password, user.get('passwordHash', ''), user.get('passwordSalt', '')):
            self._send_json(400, {'error': '旧密码不正确'})
            return

        pwd_hash, salt = hash_password(new_password)
        user['passwordHash'] = pwd_hash
        user['passwordSalt'] = salt
        _save_users(users)

        self._send_json(200, {'message': '密码修改成功'})

    # ═══════════════════════════════════════════════════
    # 用户管理 API
    # ═══════════════════════════════════════════════════

    def _handle_get_users(self):
        """GET /api/users（需要 admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        users = _load_users()
        result = []
        for u in users:
            result.append({
                'id': u['id'],
                'username': u['username'],
                'role': u.get('role', 'employee'),
                'displayName': u.get('displayName', u['username']),
                'avatar': u.get('avatar', 0),
                'agentQuota': u.get('agentQuota', 10),
                'apiQuota': u.get('apiQuota', 1000),
                'createdAt': u.get('createdAt', '')
            })
        self._send_json(200, result)

    def _handle_get_user(self, user_id):
        """GET /api/users/:id（需要 admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        users = _load_users()
        user = _find_user(users, 'id', user_id)
        if not user:
            self._send_json(404, {'error': '用户不存在'})
            return

        self._send_json(200, {
            'id': user['id'],
            'username': user['username'],
            'role': user.get('role', 'employee'),
            'displayName': user.get('displayName', user['username']),
            'avatar': user.get('avatar', 0),
            'agentQuota': user.get('agentQuota', 10),
            'apiQuota': user.get('apiQuota', 1000),
            'createdAt': user.get('createdAt', '')
        })

    def _handle_update_user(self, user_id):
        """PUT /api/users/:id（需要 admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        users = _load_users()
        user = _find_user(users, 'id', user_id)
        if not user:
            self._send_json(404, {'error': '用户不存在'})
            return

        # 可更新字段
        if 'role' in body and body['role'] in ('admin', 'employee'):
            user['role'] = body['role']
        if 'displayName' in body:
            user['displayName'] = body['displayName']
        if 'avatar' in body and isinstance(body['avatar'], int):
            user['avatar'] = body['avatar']
        if 'agentQuota' in body and isinstance(body['agentQuota'], int):
            user['agentQuota'] = body['agentQuota']
        if 'apiQuota' in body and isinstance(body['apiQuota'], int):
            user['apiQuota'] = body['apiQuota']

        _save_users(users)

        self._send_json(200, {
            'id': user['id'],
            'username': user['username'],
            'role': user.get('role', 'employee'),
            'displayName': user.get('displayName', user['username']),
            'avatar': user.get('avatar', 0),
            'agentQuota': user.get('agentQuota', 10),
            'apiQuota': user.get('apiQuota', 1000)
        })

    def _handle_delete_user(self, user_id):
        """DELETE /api/users/:id（需要 admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        # 不能删自己
        if auth.user_info['userId'] == user_id:
            self._send_json(400, {'error': '不能删除自己'})
            return

        users = _load_users()
        user = _find_user(users, 'id', user_id)
        if not user:
            self._send_json(404, {'error': '用户不存在'})
            return

        users = [u for u in users if u['id'] != user_id]
        _save_users(users)

        self._send_json(200, {'message': f'用户 {user["username"]} 已删除'})

    # ═══════════════════════════════════════════════════
    # 群组 API（项目组群聊）
    # ═══════════════════════════════════════════════════

    def _check_group_access(self, auth, group_id):
        """检查用户是否有权限访问某群组"""
        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            return None, '群组不存在', 404
        # 群组创建者或管理员可访问；成员也可访问
        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            member_ids = [m.get('id') for m in group.get('members', [])]
            if auth.user_info['userId'] not in member_ids:
                # 也检查 agent 是否属于该用户
                return None, '权限不足', 403
        return group, None, None

    def _handle_get_groups(self):
        """GET /api/groups — 获取所有群组"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        groups = _load_groups()
        # 管理员看全部，普通用户看自己创建的 + 自己是成员的
        if not auth.is_admin:
            uid = auth.user_info['userId']
            agents = _load_agents()
            my_agent_ids = [a.get('id') for a in agents if a.get('createdBy') == uid or a.get('visibility') == 'all']
            result = []
            for g in groups:
                if g.get('createdBy') == uid:
                    result.append(g)
                else:
                    member_ids = [m.get('id') for m in g.get('members', [])]
                    if any(mid in my_agent_ids for mid in member_ids):
                        result.append(g)
            self._send_json(200, result)
            return

        self._send_json(200, groups)

    def _handle_get_group(self, group_id):
        """GET /api/groups/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return

        self._send_json(200, group)

    def _handle_create_group(self):
        """POST /api/groups — 创建群组"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        name = body.get('name', '').strip()
        if not name:
            self._send_json(400, {'error': '群组名称不能为空'})
            return

        members = body.get('members', [])
        if not isinstance(members, list):
            members = []
        # members 应为 [{id, role}, ...]
        valid_members = []
        for m in members:
            if isinstance(m, dict) and m.get('id'):
                valid_members.append({'id': m['id'], 'role': m.get('role', '')})
            elif isinstance(m, str):
                valid_members.append({'id': m, 'role': ''})

        lead_agent_id = body.get('leadAgentId', '')
        # 验证 leadAgentId 是成员之一
        if lead_agent_id and lead_agent_id not in [m['id'] for m in valid_members]:
            self._send_json(400, {'error': 'leadAgentId 必须是成员之一'})
            return

        new_group = {
            'id': 'grp_' + uuid.uuid4().hex[:10],
            'name': name,
            'avatar': body.get('avatar', '👥'),
            'members': valid_members,
            'leadAgentId': lead_agent_id,
            'description': body.get('description', ''),
            'createdBy': auth.user_info['userId'],
            'createdAt': datetime.now().isoformat()
        }

        groups = _load_groups()
        groups.append(new_group)
        _save_groups(groups)

        self._send_json(201, new_group)

    def _handle_update_group(self, group_id):
        """PUT /api/groups/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            self._send_json(404, {'error': '群组不存在'})
            return

        # 权限校验：创建者或管理员
        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        # 可更新字段
        updatable = ['name', 'avatar', 'description', 'leadAgentId']
        for key in updatable:
            if key in body:
                group[key] = body[key]

        # members 整体更新
        if 'members' in body:
            members = body['members']
            if isinstance(members, list):
                valid_members = []
                for m in members:
                    if isinstance(m, dict) and m.get('id'):
                        valid_members.append({'id': m['id'], 'role': m.get('role', '')})
                    elif isinstance(m, str):
                        valid_members.append({'id': m, 'role': ''})
                group['members'] = valid_members

        # 验证 leadAgentId 仍属于成员
        if group.get('leadAgentId'):
            member_ids = [m['id'] for m in group.get('members', [])]
            if group['leadAgentId'] not in member_ids:
                group['leadAgentId'] = member_ids[0] if member_ids else ''

        _save_groups(groups)
        self._send_json(200, group)

    def _handle_batch_save_groups(self):
        """PUT /api/groups — 前端批量保存群组列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body or not isinstance(body, list):
            self._send_json(400, {'error': '无效的请求体，期望数组'})
            return

        # 只允许管理员批量覆盖；普通用户只更新自己的群组
        if auth.is_admin:
            _save_groups(body)
            self._send_json(200, body)
        else:
            uid = auth.user_info['userId']
            existing = _load_groups()
            other = [g for g in existing if g.get('createdBy') != uid]
            my_new = [g for g in body if g.get('createdBy') == uid]
            merged = other + my_new
            _save_groups(merged)
            self._send_json(200, my_new)

    def _handle_delete_group(self, group_id):
        """DELETE /api/groups/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            self._send_json(404, {'error': '群组不存在'})
            return

        # 权限校验：创建者或管理员
        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        groups = [g for g in groups if g.get('id') != group_id]
        _save_groups(groups)

        # 删除群组聊天记录
        chat_file = os.path.join(CHATS_DIR, f'group_{group_id}.json')
        if os.path.isfile(chat_file):
            try:
                os.remove(chat_file)
            except OSError:
                pass

        self._send_json(200, {'message': f'群组 {group.get("name", "")} 已删除'})

    def _handle_add_group_member(self, group_id):
        """POST /api/groups/:id/members — 添加成员"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            self._send_json(404, {'error': '群组不存在'})
            return

        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        # body: {member: {id, role}} or {id, role}
        member = body.get('member', body)
        if isinstance(member, dict) and member.get('id'):
            new_member = {'id': member['id'], 'role': member.get('role', '')}
        elif isinstance(member, str):
            new_member = {'id': member, 'role': ''}
        else:
            self._send_json(400, {'error': '缺少成员 id'})
            return

        # 检查是否已存在
        existing_ids = [m['id'] for m in group.get('members', [])]
        if new_member['id'] in existing_ids:
            self._send_json(409, {'error': '该成员已在群组中'})
            return

        group.setdefault('members', []).append(new_member)
        _save_groups(groups)

        self._send_json(200, group)

    def _handle_remove_group_member(self, group_id, emp_id):
        """DELETE /api/groups/:id/members/:empId — 移除成员"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            self._send_json(404, {'error': '群组不存在'})
            return

        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        original_len = len(group.get('members', []))
        group['members'] = [m for m in group.get('members', []) if m.get('id') != emp_id]

        if len(group['members']) == original_len:
            self._send_json(404, {'error': '该成员不在群组中'})
            return

        # 如果移除的是 leadAgent，需要重新指定
        if group.get('leadAgentId') == emp_id:
            group['leadAgentId'] = group['members'][0]['id'] if group['members'] else ''

        _save_groups(groups)
        self._send_json(200, group)

    def _handle_group_chat(self, group_id):
        """POST /api/groups/:id/chat — 发送消息到群组"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        message = body.get('message', '').strip()
        if not message:
            self._send_json(400, {'error': '消息内容不能为空'})
            return

        mentions = body.get('mentions', [])
        if not isinstance(mentions, list):
            mentions = []

        # 构建消息内容，如果有 @mentions 则拼接
        content = message
        if mentions:
            mention_tags = ' '.join(f'@{mid}' for mid in mentions)
            content = f'{mention_tags} {message}'

        # 保存用户消息到群组聊天记录
        user_message = {
            'id': 'msg_' + uuid.uuid4().hex[:8],
            'sender': auth.user_info['userId'],
            'senderType': 'user',
            'content': content,
            'mentions': mentions,
            'timestamp': datetime.now().isoformat(),
            'type': 'text'
        }

        chat_key = f'group_{group_id}'
        messages = _load_chat(chat_key)
        messages.append(user_message)
        _save_chat(chat_key, messages)

        # 返回消息和群组 session 信息，前端通过 WS 发送到 leadAgent
        lead_agent = group.get('leadAgentId', '')
        session_key = f'group:{group_id}:main'

        self._send_json(200, {
            'message': user_message,
            'leadAgentId': lead_agent,
            'sessionKey': session_key,
            'status': 'sent'
        })

    def _handle_get_group_history(self, group_id):
        """GET /api/groups/:id/history — 获取群组聊天历史"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return

        chat_key = f'group_{group_id}'
        messages = _load_chat(chat_key)
        self._send_json(200, {'messages': messages})

    # ═══════════════════════════════════════════════════
    # Agent API
    # ═══════════════════════════════════════════════════

    def _handle_get_agents(self):
        """GET /api/agents"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        agents = _load_agents()
        if auth.is_admin:
            result = agents
        else:
            # employee: 只能看到自己创建的
            result = [a for a in agents
                      if a.get('createdBy') == auth.user_info['userId']]

        # 去掉不需要的字段
        safe_result = []
        for a in result:
            safe_result.append({
                'id': a.get('id', ''),
                'name': a.get('name', ''),
                'role': a.get('role', ''),
                'bg': a.get('bg', '#FF6B35'),
                'avatar': a.get('avatar', '🦞'),
                'status': a.get('status', 'online'),
                'msg': a.get('msg', ''),
                'archived': a.get('archived', False),
                'permission': a.get('permission', 'dev'),
                'visibility': a.get('visibility', 'creator'),
                'createdBy': a.get('createdBy', ''),
                'createdByName': a.get('createdByName', ''),
                'createdAt': a.get('createdAt', ''),
                'connectionType': a.get('connectionType', ''),
                'apiProvider': a.get('apiProvider', ''),
                'apiModel': a.get('apiModel', ''),
                'apiKey': a.get('apiKey', ''),
                'openclawAgent': a.get('openclawAgent', ''),
                'openclawModel': a.get('openclawModel', ''),
                'openclawName': a.get('openclawName', ''),
                'aiProvider': a.get('aiProvider', ''),
                'systemPrompt': a.get('systemPrompt', ''),
                'soulDoc': a.get('soulDoc', ''),
                'idDoc': a.get('idDoc', ''),
                'toolsDoc': a.get('toolsDoc', ''),
                'userDoc': a.get('userDoc', ''),
                'department': a.get('department', ''),
                'group': a.get('group', ''),
                'pinned': a.get('pinned', False),
                'customEndpoint': a.get('customEndpoint', ''),
            })
        self._send_json(200, safe_result)

    def _handle_get_agent(self, agent_id):
        """GET /api/agents/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break

        if not agent:
            self._send_json(404, {'error': 'Agent 不存在'})
            return

        # 权限校验
        if not auth.is_admin:
            if agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibility') != 'all':
                self._send_auth_error('权限不足', 403)
                return

        self._send_json(200, agent)

    def _handle_create_agent(self):
        """POST /api/agents"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        # employee 配额检查
        if not auth.is_admin:
            auth.load_user_record()
            user = auth.user_record
            if user:
                agents = _load_agents()
                my_count = len([a for a in agents if a.get('createdBy') == auth.user_info['userId']])
                if my_count >= user.get('agentQuota', 10):
                    self._send_json(403, {'error': f'已达到 Agent 配额上限 ({user.get("agentQuota", 10)})'})
                    return

        new_agent = {
            'id': body.get('id', 'emp_' + uuid.uuid4().hex[:6]),
            'name': body.get('name', '未命名'),
            'role': body.get('role', ''),
            'bg': body.get('bg', '#FF6B35'),
            'avatar': body.get('avatar', '🦞'),
            'status': body.get('status', 'online'),
            'msg': body.get('msg', ''),
            'archived': body.get('archived', False),
            'permission': body.get('permission', 'dev'),
            'visibility': body.get('visibility', 'creator'),
            'createdBy': auth.user_info['userId'],
            'createdAt': datetime.now().isoformat(),
            'connectionType': body.get('connectionType', ''),
            'apiProvider': body.get('apiProvider', ''),
            'apiModel': body.get('apiModel', ''),
            'apiKey': body.get('apiKey', ''),
            'openclawAgent': body.get('openclawAgent', ''),
            'openclawModel': body.get('openclawModel', ''),
            'openclawName': body.get('openclawName', ''),
            'aiProvider': body.get('aiProvider', ''),
            'systemPrompt': body.get('systemPrompt', ''),
            'department': body.get('department', ''),
            'customEndpoint': body.get('customEndpoint', ''),
        }

        agents = _load_agents()
        # 检查 ID 重复
        for a in agents:
            if a.get('id') == new_agent['id']:
                new_agent['id'] = 'emp_' + uuid.uuid4().hex[:6]
                break
        agents.append(new_agent)
        _save_agents(agents)

        self._send_json(201, new_agent)

    def _handle_update_agent(self, agent_id):
        """PUT /api/agents/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break

        if not agent:
            self._send_json(404, {'error': 'Agent 不存在'})
            return

        # 权限校验
        if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        # 可更新字段
        updatable = ['name', 'role', 'bg', 'avatar', 'status', 'msg', 'archived',
                     'permission', 'visibility', 'connectionType', 'apiProvider',
                     'apiModel', 'apiKey', 'openclawAgent', 'openclawModel',
                     'openclawName', 'aiProvider',
                     'systemPrompt', 'department', 'customEndpoint',
                     'group', 'pinned', 'idDoc', 'soulDoc', 'toolsDoc', 'userDoc']
        for key in updatable:
            if key in body:
                agent[key] = body[key]

        _save_agents(agents)
        self._send_json(200, agent)

    def _handle_delete_agent(self, agent_id):
        """DELETE /api/agents/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break

        if not agent:
            self._send_json(404, {'error': 'Agent 不存在'})
            return

        # 权限校验
        if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        agents = [a for a in agents if a.get('id') != agent_id]
        _save_agents(agents)

        # 删除聊天记录
        chat_file = os.path.join(CHATS_DIR, f'{agent_id}.json')
        if os.path.isfile(chat_file):
            try:
                os.remove(chat_file)
            except OSError:
                pass

        self._send_json(200, {'message': f'Agent {agent.get("name", "")} 已删除'})

    # ═══════════════════════════════════════════════════
    # 聊天 API
    # ═══════════════════════════════════════════════════

    def _handle_write_agent_docs(self):
        """POST /api/openclaw/write-agent-docs - Write SOUL.md and IDENTITY.md to agent workspace"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        agent_id = body.get('agentId', '')
        soul_doc = body.get('soulDoc', '')
        identity_doc = body.get('identityDoc', '')
        workspace_path = body.get('workspacePath', '~/.openclaw/workspace-' + agent_id)

        if not agent_id:
            self._send_json(400, {'error': '缺少 agentId'})
            return

        import os
        workspace_path = os.path.expanduser(workspace_path)

        try:
            os.makedirs(workspace_path, exist_ok=True)
            written = []

            if soul_doc:
                with open(os.path.join(workspace_path, 'SOUL.md'), 'w', encoding='utf-8') as f:
                    f.write(soul_doc)
                written.append('SOUL.md')

            if identity_doc:
                with open(os.path.join(workspace_path, 'IDENTITY.md'), 'w', encoding='utf-8') as f:
                    f.write(identity_doc)
                written.append('IDENTITY.md')

            self._send_json(200, {
                'ok': True,
                'agentId': agent_id,
                'written': written,
                'workspace': workspace_path
            })
        except Exception as e:
            self._send_json(500, {'error': f'写入失败: {str(e)}'})

    def _handle_write_soul(self):
        """POST /api/openclaw/write-soul - Write SOUL.md/IDENTITY.md to agent workspace"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        agent_name = body.get('agentName', '')
        soul_content = body.get('soulContent', '')
        identity_content = body.get('identityContent', '')

        if not agent_name:
            self._send_json(400, {'error': '缺少 agentName'})
            return

        import os
        workspace_base = os.path.expanduser('~/.openclaw/agents')
        agent_dir = os.path.join(workspace_base, agent_name)

        try:
            os.makedirs(agent_dir, exist_ok=True)

            if soul_content:
                with open(os.path.join(agent_dir, 'SOUL.md'), 'w', encoding='utf-8') as f:
                    f.write(soul_content)

            if identity_content:
                with open(os.path.join(agent_dir, 'IDENTITY.md'), 'w', encoding='utf-8') as f:
                    f.write(identity_content)

            self._send_json(200, {
                'success': True,
                'agentName': agent_name,
                'dir': agent_dir
            })
        except Exception as e:
            self._send_json(500, {'error': f'写入失败: {str(e)}'})


    def _check_agent_access(self, auth, agent_id):
        """检查用户是否有权限访问某 Agent 的聊天"""
        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break
        if not agent:
            return None, 'Agent 不存在', 404
        if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibility') != 'all':
            return None, '权限不足', 403
        return agent, None, None

    def _handle_get_chat(self, agent_id):
        """GET /api/chat/:agentId"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        messages = _load_chat(agent_id)
        self._send_json(200, messages)

    def _handle_post_chat(self, agent_id):
        """POST /api/chat/:agentId"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        agent, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        user_message = {
            'id': 'msg_' + uuid.uuid4().hex[:8],
            'role': 'user',
            'content': body.get('content', ''),
            'timestamp': datetime.now().isoformat(),
            'userId': auth.user_info['userId']
        }

        messages = _load_chat(agent_id)
        messages.append(user_message)

        # 如果 Agent 走 API，通过代理调用 AI API
        connection_type = agent.get('connectionType', '')
        if connection_type == 'api':
            api_reply = self._call_ai_api(agent, body.get('content', ''))
            if api_reply:
                ai_message = {
                    'id': 'msg_' + uuid.uuid4().hex[:8],
                    'role': 'assistant',
                    'content': api_reply,
                    'timestamp': datetime.now().isoformat()
                }
                messages.append(ai_message)
                _save_chat(agent_id, messages)
                self._send_json(200, {'userMessage': user_message, 'aiMessage': ai_message})
                return

        # OpenClaw 或其他
        _save_chat(agent_id, messages)

        if connection_type == 'openclaw':
            self._send_json(200, {
                'userMessage': user_message,
                'hint': '请通过 WebSocket 连接获取 AI 回复'
            })
        else:
            self._send_json(200, {'userMessage': user_message})

    def _call_ai_api(self, agent, user_message):
        """通过代理调用 AI API"""
        api_provider = agent.get('apiProvider', '')
        api_key = agent.get('apiKey', '')
        api_model = agent.get('apiModel', '')
        custom_endpoint = agent.get('customEndpoint', '')

        if not api_key:
            return None

        # 确定 base URL
        base_url = ''
        if api_provider == 'custom' and custom_endpoint:
            base_url = custom_endpoint.rstrip('/')
        elif api_provider == 'openai':
            base_url = 'https://api.openai.com/v1'
        elif api_provider == 'deepseek':
            base_url = 'https://api.deepseek.com/v1'
        elif api_provider == 'moonshot':
            base_url = 'https://api.moonshot.cn/v1'
        elif api_provider == 'zhipu':
            base_url = 'https://open.bigmodel.cn/api/paas/v4'
        elif api_provider == 'anthropic':
            base_url = 'https://api.anthropic.com/v1'
        else:
            if custom_endpoint:
                base_url = custom_endpoint.rstrip('/')
            else:
                return None

        target_url = base_url + '/chat/completions'

        system_prompt = f'你是 {agent.get("name", "AI")}，一个 {agent.get("role", "助手")}。请用第一人称回复，保持角色一致性。'
        if agent.get('systemPrompt'):
            system_prompt += '\n\n' + agent['systemPrompt']

        req_body = json.dumps({
            'model': api_model or 'gpt-4o-mini',
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message}
            ],
            'temperature': 0.8,
            'max_tokens': 2000,
            'stream': False
        }).encode('utf-8')

        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'Content-Length': str(len(req_body))
        }

        try:
            req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
            resp_data = json.loads(resp.read().decode('utf-8'))
            if resp_data.get('choices') and resp_data['choices'][0].get('message'):
                return resp_data['choices'][0]['message'].get('content', '')
        except Exception as e:
            print(f'  ❌ AI API call failed: {e}', flush=True)

        return None

    def _handle_delete_chat_message(self, agent_id, msg_id):
        """DELETE /api/chat/:agentId/:msgId"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        messages = _load_chat(agent_id)
        original_len = len(messages)
        messages = [m for m in messages if m.get('id') != msg_id]
        if len(messages) == original_len:
            self._send_json(404, {'error': '消息不存在'})
            return

        _save_chat(agent_id, messages)
        self._send_json(200, {'message': '消息已删除'})

    # ═══════════════════════════════════════════════════
    # OpenClaw API（原有功能，已加认证）
    # ═══════════════════════════════════════════════════

    def _handle_openclaw_status(self):
        """GET /api/openclaw/status"""
        status = _openclaw_status()
        self._send_json(200, status)

    def _handle_openclaw_list_agents(self):
        """GET /api/openclaw/agents"""
        success, stdout, stderr, rc = _run_openclaw(['agents', 'list', '--json'])
        if not success:
            self._send_json(200, {
                'agents': [],
                'warning': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(200, {
                'agents': [],
                'warning': stderr.strip() or f'Command failed (rc={rc})'
            })
            return

        try:
            data = json.loads(stdout.strip())
            if isinstance(data, list):
                self._send_json(200, {'agents': data})
            elif isinstance(data, dict) and 'agents' in data:
                self._send_json(200, data)
            else:
                self._send_json(200, {'agents': [data] if isinstance(data, dict) else []})
        except json.JSONDecodeError:
            # 非JSON输出，尝试解析文本
            lines = stdout.strip().split('\n')
            agents = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    agents.append({'name': line, 'source': 'cli-text'})
            self._send_json(200, {'agents': agents, 'source': 'text-parse'})

    def _handle_openclaw_list_models(self):
        """GET /api/openclaw/models"""
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
        self._send_json(200, {'models': _default_models(), 'source': 'default'})

    def _handle_openclaw_create_agent(self):
        """POST /api/openclaw/agents/create"""
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

        # 构建 CLI 参数 (--non-interactive requires --workspace)
        home = os.path.expanduser('~')
        if not workspace:
            workspace = os.path.join(home, '.openclaw', 'agents', name)
        # 确保 workspace 目录存在
        os.makedirs(workspace, exist_ok=True)

        args = ['agents', 'add', name, '--workspace', workspace, '--non-interactive']
        if model:
            args.extend(['--model', model])

        success, stdout, stderr, rc = _run_openclaw(args)

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

        # Write SOUL.md if provided
        if soul:
            soul_path = os.path.join(workspace, 'SOUL.md')
            try:
                with open(soul_path, 'w', encoding='utf-8') as f:
                    f.write(soul)
            except OSError as e:
                logging.warning(f"Failed to write SOUL.md: {e}")

        self._send_json(200, {
            'success': True,
            'name': name,
            'model': model,
            'workspace': workspace,
            'soul_written': bool(soul),
            'output': stdout.strip()
        })

    def _handle_openclaw_update_agent(self):
        """POST /api/openclaw/agents/update"""
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

        if soul:
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
                success, stdout, stderr, rc = _run_openclaw(['agents', 'list', '--json'])
                if success and rc == 0:
                    try:
                        agents_data = json.loads(stdout.strip())
                        agents_list = agents_data if isinstance(agents_data, list) else agents_data.get('agents', [])
                        for agent in agents_list:
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

        if model:
            del_success, del_stdout, del_stderr, del_rc = _run_openclaw(['agents', 'delete', name])
            if del_success and del_rc == 0:
                ws = results.get('workspace', os.path.join(os.path.expanduser('~'), '.openclaw', 'agents', name))
                add_success, add_stdout, add_stderr, add_rc = _run_openclaw([
                    'agents', 'add', name, '--workspace', ws, '--model', model, '--non-interactive'
                ])
                if add_success and add_rc == 0:
                    results['updates'].append(f'Agent recreated with model: {model}')
                else:
                    results['success'] = False
                    results['error'] = add_stderr.strip() or 'Failed to recreate agent with new model'
            else:
                results['updates'].append(f'Delete step: {del_stderr.strip() or "ok"}')
                ws = os.path.join(os.path.expanduser('~'), '.openclaw', 'agents', name)
                add_success, add_stdout, add_stderr, add_rc = _run_openclaw([
                    'agents', 'add', name, '--workspace', ws, '--model', model, '--non-interactive'
                ])
                if add_success and add_rc == 0:
                    results['updates'].append(f'Agent created with model: {model}')
                else:
                    results['success'] = False
                    results['error'] = add_stderr.strip() or 'Failed to create agent with new model'

        self._send_json(200, results)

    def _handle_openclaw_delete_agent(self, agent_name):
        """DELETE /api/openclaw/agents/:name"""
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

    # ═══════════════════════════════════════════════════
    # CORS 代理（需认证）
    # ═══════════════════════════════════════════════════

    def _handle_proxy(self):
        """POST /api/proxy（需认证）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        target_url = self.headers.get('X-Target-URL', '')
        if not target_url:
            self._send_json_error(400, 'Missing X-Target-URL header')
            return

        if not target_url.startswith('https://'):
            self._send_json_error(403, 'Only HTTPS targets are allowed')
            return

        if ALLOWED_DOMAINS:
            host = urlparse(target_url).hostname or ''
            if not any(host == d or host.endswith('.' + d) for d in ALLOWED_DOMAINS):
                self._send_json_error(403, f'Domain {host} not in allowed list')
                return

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None

        forward_headers = {}
        # 代理请求使用的是用户 AI 的 API Key，不是 SoloBrave 的 token
        # 从请求体或 header 中获取 AI API 的 Authorization
        auth_header = self.headers.get('Authorization', '')
        if auth_header.startswith('Bearer ') and not auth_header.startswith('Bearer ey'):  # 粗略区分 JWT 和 API Key
            # 如果看起来像 API Key，转发它
            pass
        # 从请求头中取 AI API Key（前端可能放在 X-AI-API-Key 中）
        ai_api_key = self.headers.get('X-AI-API-Key', '')
        if ai_api_key:
            forward_headers['Authorization'] = f'Bearer {ai_api_key}'
        elif auth_header and not auth_header.startswith('Bearer ey'):
            forward_headers['Authorization'] = auth_header

        content_type = self.headers.get('Content-Type', 'application/json')
        if content_type:
            forward_headers['Content-Type'] = content_type
        if body:
            forward_headers['Content-Length'] = str(len(body))

        print(f'  🔄 Proxy -> {target_url}', flush=True)
        try:
            req = urllib.request.Request(target_url, data=body, headers=forward_headers, method='POST')
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)

            resp_body = resp.read()
            resp_content_type = resp.headers.get('Content-Type', 'application/json')

            self.send_response(resp.status)
            self._add_cors_headers()
            self.send_header('Content-Type', resp_content_type)
            self.end_headers()
            self.wfile.write(resp_body)
            print(f'  ✅ Proxy OK ({resp.status}) <- {target_url}', flush=True)

        except urllib.error.HTTPError as e:
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
    # 确保数据目录
    _ensure_data_dir()

    # 初始化默认管理员
    _init_default_admin()

    # 检查静态目录
    if not os.path.isdir(STATIC_DIR):
        print(f'⚠️  静态文件目录不存在: {STATIC_DIR}')
        sys.exit(1)

    index_file = os.path.join(STATIC_DIR, 'index.html')
    if not os.path.isfile(index_file):
        print(f'⚠️  找不到 index.html: {index_file}')
        sys.exit(1)

    # 检查 OpenClaw CLI
    if os.path.isfile(OPENCLAW_CLI):
        print(f'  🦞 OpenClaw CLI: ✓ ({OPENCLAW_CLI})')
    else:
        print(f'  🦞 OpenClaw CLI: ✗ (not found at {OPENCLAW_CLI})')

    server = http.server.HTTPServer((BIND, PORT), SoloBraveHandler)

    print('=' * 56)
    print('  🚀 SoloBrave Server (Auth Enabled)')
    print('=' * 56)
    print(f'  📂 静态文件:  {STATIC_DIR}')
    print(f'  💾 数据目录:  {DATA_DIR}')
    print(f'  🌐 本机访问:  http://localhost:{PORT}')
    print(f'  🌐 局域网:    http://0.0.0.0:{PORT}')
    print(f'  🔐 认证 API:  /api/auth/*')
    print(f'  👤 用户管理:  /api/users/*')
    print(f'  🤖 Agent API: /api/agents/*')
    print(f'  👥 群组 API:  /api/groups/*')
    print(f'  💬 聊天 API:  /api/chat/*')
    print(f'  🔀 API 代理:  POST /api/proxy')
    print(f'  🦞 OpenClaw:  /api/openclaw/*')
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
