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
import threading
import urllib.request
import urllib.error
import hashlib
import hmac
import base64
import uuid
import time
try:
    import fcntl
except ImportError:
    fcntl = None
from datetime import datetime
from urllib.parse import urlparse, unquote, parse_qs

# 按 agent_id 细分的聊天写入锁，防止读-修改-写竞争导致消息丢失
_chat_write_locks = {}
_chat_locks_mutex = threading.Lock()

def _get_chat_lock(agent_id):
    with _chat_locks_mutex:
        if agent_id not in _chat_write_locks:
            _chat_write_locks[agent_id] = threading.Lock()
        return _chat_write_locks[agent_id]

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
TEAMS_FILE = os.path.join(DATA_DIR, 'teams.json')
MEMORY_DIR = os.path.join(DATA_DIR, 'memory')

# 角色初始记忆种子映射：前端 role -> memory-seed 文件名
ROLE_MEMORY_SEED_MAP = {
    'CEO助理': 'Trumind',
    '战略顾问': 'Trumind',
    '前端工程师': 'Gates',
    '后端工程师': 'Gates',
    '产品经理': 'Trumind',
    '运营': 'Grace',
    '客服': 'Grace',
    '数据分析师': 'Black',
}

# JWT 配置
JWT_EXPIRE_SECONDS = 7 * 24 * 3600  # 7 天


# ─── 数据存储层 ─────────────────────────────────────────

def _ensure_data_dir():
    """确保数据目录存在"""
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(CHATS_DIR, exist_ok=True)
    os.makedirs(MEMORY_DIR, exist_ok=True)


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
    """写入 JSON 文件（加文件锁，唯一临时文件避免并发踩踏）"""
    _ensure_data_dir()
    # 确保父目录存在（agent_id 可能包含子路径）
    parent_dir = os.path.dirname(filepath)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    # 用唯一临时文件名，避免并发写入时互相覆盖tmp
    tmp_path = filepath + '.tmp.' + uuid.uuid4().hex[:8]
    try:
        with open(tmp_path, 'w', encoding='utf-8') as f:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            if fcntl:
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
            'createdAt': datetime.now().isoformat(),
            # V2 新增字段
            'teamIds': [],
            'subordinateIds': [],
            'roleTemplateId': None,
            'status': 'active',
            'lastLoginAt': None
        }
        _save_users([admin])
        print('  🔑 默认管理员账号: admin / admin123，请尽快修改密码')
        return admin
    return None


# ─── Agent 管理 ─────────────────────────────────────────

# 前端历史遗留的硬编码默认员工ID（已移除，但后端数据可能仍保留，需过滤）
_DEFAULT_EMP_IDS = {'xlcx', 'dlxc', 'zjg', 'hx', 'sy'}
# 历史遗留默认员工名字（不区分大小写）
_DEFAULT_EMP_NAMES = {'lucy', 'emily', 'grace', 'cynthia', 'luna', 'gates', 'eric', 'olivia', 'summer'}

def _is_default_agent(agent):
    """判断是否为历史遗留默认员工（按ID或名字），有createdBy的用户手动创建员工不受影响"""
    if not isinstance(agent, dict):
        return False
    # 有 createdBy 的员工是用户手动创建的，绝不视为默认员工
    created_by = agent.get('createdBy')
    if created_by and created_by != 'local' and created_by != '':
        return False
    if agent.get('id') in _DEFAULT_EMP_IDS:
        return True
    name = str(agent.get('name', '')).strip().lower()
    if name in _DEFAULT_EMP_NAMES:
        return True
    return False

def _load_agents():
    """加载 Agent 列表，过滤掉历史遗留的默认员工"""
    agents = _read_json(AGENTS_FILE, [])
    if not isinstance(agents, list):
        return []
    return [a for a in agents if not _is_default_agent(a)]

def _clean_agents_file():
    """主动清理 agents.json 中的历史遗留默认员工数据"""
    agents = _read_json(AGENTS_FILE, [])
    if not isinstance(agents, list):
        return 0
    cleaned = [a for a in agents if not _is_default_agent(a)]
    removed = len(agents) - len(cleaned)
    if removed > 0:
        _write_json(AGENTS_FILE, cleaned)
        print(f'  [Clean] 已从 agents.json 清理 {removed} 个历史遗留默认员工', flush=True)
    return removed

def _save_agents(agents):
    """保存 Agent 列表"""
    _write_json(AGENTS_FILE, agents)


def _sanitize_role(role):
    """清理职能字段：过滤掉 __custom__ 和 custom 标记"""
    if role in ('__custom__', 'custom'):
        return ''
    return role if role else ''


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


# ─── 小组管理 ─────────────────────────────────────────

def _load_teams():
    """加载小组列表"""
    teams = _read_json(TEAMS_FILE, [])
    return teams if isinstance(teams, list) else []


def _save_teams(teams):
    """保存小组列表"""
    _write_json(TEAMS_FILE, teams)


def _find_team(teams, key, value):
    """在小组列表中查找小组"""
    for t in teams:
        if t.get(key) == value:
            return t
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
        self.is_leader = False   # 是否是 leader
        self.team_ids = []       # 所属小组 ID 列表
        self.managed_team_ids = []  # 管理的小组 ID 列表

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
            # 填充 team_ids 和 managed_team_ids
            if self.user_record:
                self.team_ids = self.user_record.get('teamIds', [])
                self.is_leader = self.user_record.get('role') == 'leader'
                # leader 查找自己管理的小组
                if self.is_leader:
                    teams = _load_teams()
                    self.managed_team_ids = [t.get('id') for t in teams if t.get('leaderId') == self.user_info.get('userId')]
                    # 兼容：leaderId未设置时，把team_ids当作managed_team_ids
                    if not self.managed_team_ids and self.team_ids:
                        self.managed_team_ids = list(self.team_ids)
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
    # 创建 AuthResult 并加载用户记录以获取 team 信息
    result = AuthResult(user_info=user_info)
    result.load_user_record()
    return result


def _can_access_team(auth, team_id):
    """判断用户是否有权访问某个小组"""
    if auth.is_admin:
        return True
    if team_id in auth.managed_team_ids:
        return True
    # 检查是否是管理组的子组
    if _is_sub_team(team_id, auth.managed_team_ids):
        return True
    return False


def _is_sub_team(team_id, parent_team_ids):
    """判断 team_id 是否是某个 parent 的子组"""
    teams = _load_teams()
    team = None
    for t in teams:
        if t.get('id') == team_id:
            team = t
            break
    if team and team.get('parentId') in parent_team_ids:
        return True
    if team and team.get('parentId'):
        return _is_sub_team(team.get('parentId'), parent_team_ids)
    return False


def _get_accessible_agent_ids(auth):
    """获取用户有权访问的Agent ID列表"""
    if auth.is_admin:
        return None  # 全部
    agents = _load_agents()
    teams = _load_teams()
    users = _load_users()
    accessible = set()
    
    # 自己所属组的agentIds
    for tid in auth.team_ids:
        for t in teams:
            if t.get('id') == tid:
                for aid in t.get('agentIds', []):
                    accessible.add(aid)
                break
    
    # 找到同组/管理组的所有用户ID（直接查users.teamIds，不依赖team.members）
    if auth.is_leader:
        # leader: 自己管理的组 + leaderId指向自己的组
        managed_tids = set(auth.managed_team_ids)
        for t in teams:
            if t.get('leaderId') == auth.user_info.get('userId'):
                managed_tids.add(t.get('id'))
        # 找这些组内的所有用户
        same_team_user_ids = set()
        for u in users:
            for tid in u.get('teamIds', []):
                if tid in managed_tids:
                    same_team_user_ids.add(u.get('id'))
                    break
        # 加上管理组的agentIds
        for tid in managed_tids:
            accessible.update(_get_team_and_children_agent_ids(tid, teams))
        # 加上同组成员创建的agent
        for a in agents:
            if a.get('createdBy') in same_team_user_ids:
                accessible.add(a.get('id'))
    else:
        # employee: 自己同组的用户
        my_team_ids = set(auth.team_ids)
        same_team_user_ids = set()
        for u in users:
            for tid in u.get('teamIds', []):
                if tid in my_team_ids:
                    same_team_user_ids.add(u.get('id'))
                    break
        for a in agents:
            if a.get('createdBy') in same_team_user_ids:
                accessible.add(a.get('id'))
    
    return list(accessible)


def _get_team_and_children_agent_ids(team_id, teams):
    """获取小组及所有子组的 agent IDs"""
    result = set()
    for t in teams:
        if t.get('id') == team_id:
            for aid in t.get('agentIds', []):
                result.add(aid)
            # 递归子组
            for child in teams:
                if child.get('parentId') == team_id:
                    result.update(_get_team_and_children_agent_ids(child.get('id'), teams))
            break
    return result


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
    def end_headers(self):
        # 开发模式禁用缓存
        if self.path.endswith('.html') or self.path == '/' or self.path.endswith('.js'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
        super().end_headers()

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
        try:
            self._do_GET()
        except Exception as e:
            print(f'  [ERROR] GET {self.path}: {e}', flush=True)
            try:
                self._send_json(500, {'error': str(e)})
            except:
                pass

    def _do_GET(self):
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
        if path == '/api/openclaw/skills/list':
            self._handle_auth_required_get(path)
            return
        if path.startswith('/api/openclaw/skills/search'):
            self._handle_auth_required_get(path)
            return
        if path.startswith('/api/openclaw/agent-docs/'):
            self._handle_auth_required_get(path)
            return
        if path == '/api/openclaw/channels/feishu/status':
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

        # Memory API
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 1:
                self._handle_get_memory(parts[0])
                return
            elif len(parts) == 2:
                self._handle_delete_memory(parts[0], parts[1])
                return

        # Chat API
        if path.startswith('/api/chat/'):
            sub = path[len('/api/chat/'):]
            # /api/chat/summarize/:agentId
            if sub.startswith('summarize/'):
                agent_id = sub[len('summarize/'):]
                if agent_id:
                    self._handle_get_summarize(agent_id)
                    return
            # /api/chat/:agentId
            agent_id = sub
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

        # Teams API (V2)
        if path == '/api/teams':
            self._handle_get_teams()
            return
        if path.startswith('/api/teams/'):
            team_id = path[len('/api/teams/'):]
            if team_id:
                # /api/teams/:id/members/:userId (DELETE)
                if team_id.endswith('/members') and self.command == 'DELETE':
                    pass  # handled in do_DELETE
                elif '/members/' in team_id:
                    parts = team_id.split('/members/')
                    if len(parts) == 2:
                        self._handle_get_team_member(parts[0], parts[1])
                        return
                # /api/teams/:id
                elif '/' not in team_id:
                    self._handle_get_team(team_id)
                    return

        # Users subordinates API (V2)
        if path.startswith('/api/users/') and '/subordinates' in path:
            parts = path.split('/subordinates')
            if len(parts) == 2:
                user_id = parts[0][len('/api/users/'):]
                if user_id:
                    self._handle_get_user_subordinates(user_id)
                    return

        # Users role API (V2)
        if path.startswith('/api/users/') and '/role' in path:
            parts = path.split('/role')
            if len(parts) == 2:
                user_id = parts[0][len('/api/users/'):]
                if user_id and self.command == 'PUT':
                    self._handle_update_user_role(user_id)
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
        try:
            self._do_POST()
        except Exception as e:
            print(f'  [ERROR] POST {self.path}: {e}', flush=True)
            import traceback; traceback.print_exc()
            try:
                self._send_json(500, {'error': str(e)})
            except:
                pass

    def _do_POST(self):
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
        if path == '/api/openclaw/skills/install':
            self._handle_auth_required_post(path)
            return
        if path == '/api/openclaw/skills/remove':
            self._handle_auth_required_post(path)
            return
        if path == '/api/openclaw/channels/feishu':
            self._handle_auth_required_post(path)
            return
        if path == '/api/openclaw/pairing/approve':
            self._handle_auth_required_post(path)
            return
        if path == '/api/openclaw/gateway/restart':
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

        # Teams API (V2)
        if path == '/api/teams':
            self._handle_create_team()
            return
        if path.startswith('/api/teams/'):
            sub = path[len('/api/teams/'):]
            # /api/teams/:id/members
            if sub.endswith('/members'):
                team_id = sub[:-len('/members')]
                if team_id:
                    self._handle_add_team_member(team_id)
                    return

        # Memory API
        if path.startswith('/api/memory/'):
            emp_id = path[len('/api/memory/'):]
            if emp_id and '/' not in emp_id:
                self._handle_post_memory(emp_id)
                return

        # Chat API
        if path.startswith('/api/chat/'):
            sub = path[len('/api/chat/'):]
            # /api/chat/summarize/:agentId
            if sub.startswith('summarize/'):
                agent_id = sub[len('summarize/'):]
                if agent_id:
                    self._handle_summarize_chat(agent_id)
                    return
            # /api/chat/:agentId
            if sub:
                self._handle_post_chat(sub)
                return

        self._send_json_error(404, 'Not found')

    def do_PUT(self):
        try:
            self._do_PUT()
        except Exception as e:
            print(f'  [ERROR] PUT {self.path}: {e}', flush=True)
            import traceback; traceback.print_exc()
            try:
                self._send_json(500, {'error': str(e)})
            except:
                pass

    def _do_PUT(self):
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

        # Teams API (V2)
        if path.startswith('/api/teams/'):
            team_id = path[len('/api/teams/'):]
            if team_id:
                self._handle_update_team(team_id)
                return

        # Memory API
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 2:
                self._handle_update_memory(parts[0], parts[1])
                return

        self._send_json_error(404, 'Not found')

    def do_DELETE(self):
        try:
            self._do_DELETE()
        except Exception as e:
            print(f'  [ERROR] DELETE {self.path}: {e}', flush=True)
            try:
                self._send_json(500, {'error': str(e)})
            except:
                pass

    def _do_DELETE(self):
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

        # Teams API (V2)
        if path.startswith('/api/teams/'):
            sub = path[len('/api/teams/'):]
            parts = sub.split('/')
            if len(parts) == 3 and parts[1] == 'members':
                # /api/teams/:teamId/members/:userId
                self._handle_remove_team_member(parts[0], parts[2])
                return
            elif len(parts) == 1 and parts[0]:
                # /api/teams/:id
                self._handle_delete_team(parts[0])
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

        # Memory API
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 2:
                self._handle_delete_memory(parts[0], parts[1])
                return

        # Chat API
        if path.startswith('/api/chat/'):
            # /api/chat/:agentId/:msgId
            parts = path[len('/api/chat/'):].split('/')
            if len(parts) == 2:
                self._handle_delete_chat_message(parts[0], parts[1])
                return
            # /api/chat/:agentId (clear all)
            if len(parts) == 1:
                self._handle_clear_chat(parts[0])
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
        elif path == '/api/openclaw/skills/list':
            self._handle_skills_list()
        elif path.startswith('/api/openclaw/skills/search'):
            self._handle_skills_search()
        elif path.startswith('/api/openclaw/agent-docs/'):
            agent_id = path[len('/api/openclaw/agent-docs/'):]
            if agent_id:
                self._handle_get_agent_docs(agent_id)
        elif path == '/api/openclaw/channels/feishu/status':
            self._handle_feishu_status()
        elif path == '/api/openclaw/gateway/restart':
            self._handle_gateway_restart()

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
        elif path == '/api/openclaw/skills/install':
            self._handle_skills_install()
        elif path == '/api/openclaw/skills/remove':
            self._handle_skills_remove()
        elif path == '/api/openclaw/channels/feishu':
            self._handle_feishu_config()
        elif path == '/api/openclaw/pairing/approve':
            self._handle_pairing_approve()

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

        # 更新 lastLoginAt
        user['lastLoginAt'] = datetime.now().isoformat()
        _save_users(users)

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

        if role not in ('admin', 'leader', 'employee'):
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
            'createdAt': datetime.now().isoformat(),
            # V2 新增字段
            'teamIds': body.get('teamIds', []),
            'subordinateIds': [],
            'roleTemplateId': body.get('roleTemplateId', None),
            'status': 'active',
            'lastLoginAt': None
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
                'createdAt': u.get('createdAt', ''),
                # V2 新增字段
                'teamIds': u.get('teamIds', []),
                'subordinateIds': u.get('subordinateIds', []),
                'roleTemplateId': u.get('roleTemplateId'),
                'status': u.get('status', 'active'),
                'lastLoginAt': u.get('lastLoginAt')
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
            'createdAt': user.get('createdAt', ''),
            # V2 新增字段
            'teamIds': user.get('teamIds', []),
            'subordinateIds': user.get('subordinateIds', []),
            'roleTemplateId': user.get('roleTemplateId'),
            'status': user.get('status', 'active'),
            'lastLoginAt': user.get('lastLoginAt')
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
        if 'role' in body and body['role'] in ('admin', 'leader', 'employee'):
            user['role'] = body['role']
        if 'displayName' in body:
            user['displayName'] = body['displayName']
        if 'avatar' in body and isinstance(body['avatar'], int):
            user['avatar'] = body['avatar']
        if 'agentQuota' in body and isinstance(body['agentQuota'], int):
            user['agentQuota'] = body['agentQuota']
        if 'apiQuota' in body and isinstance(body['apiQuota'], int):
            user['apiQuota'] = body['apiQuota']
        # V2 新增字段
        if 'teamIds' in body and isinstance(body['teamIds'], list):
            user['teamIds'] = body['teamIds']
        if 'subordinateIds' in body and isinstance(body['subordinateIds'], list):
            user['subordinateIds'] = body['subordinateIds']
        if 'roleTemplateId' in body:
            user['roleTemplateId'] = body['roleTemplateId']
        if 'status' in body and body['status'] in ('active', 'disabled'):
            user['status'] = body['status']

        _save_users(users)

        # 同步更新 teams 的 members 和 leaderId
        teams = _load_teams()
        uid = user['id']
        new_team_ids = set(user.get('teamIds', []))
        new_role = user.get('role', 'employee')
        for t in teams:
            t_members = set(t.get('members', []))
            # 如果用户在这个组，确保members里有
            if t['id'] in new_team_ids:
                t_members.add(uid)
                t['members'] = list(t_members)
                # 如果是leader，设置leaderId
                if new_role == 'leader' and not t.get('leaderId'):
                    t['leaderId'] = uid
            else:
                # 如果用户不在这个组，从members移除
                if uid in t_members:
                    t_members.discard(uid)
                    t['members'] = list(t_members)
                # 如果是leader离开了，清除leaderId
                if t.get('leaderId') == uid:
                    t['leaderId'] = None
        _save_teams(teams)

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

    def _handle_get_user_subordinates(self, user_id):
        """GET /api/users/:id/subordinates — 获取下属列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        users = _load_users()
        user = _find_user(users, 'id', user_id)
        if not user:
            self._send_json(404, {'error': '用户不存在'})
            return

        # 权限检查：本人/admin 可以看，leader 可以看自己下属
        if not auth.is_admin:
            if auth.user_info.get('userId') != user_id:
                # 检查是否是上级
                is_leader = any(s.get('leaderId') == auth.user_info.get('userId') for s in users if s.get('id') == user_id)
                if not is_leader:
                    self._send_json(403, {'error': '权限不足'})
                    return

        # 构建下属树
        def get_subordinates(uid, depth=0):
            if depth > 5:  # 防止循环
                return []
            result = []
            for u in users:
                if u.get('leaderId') == uid:
                    result.append({
                        'id': u.get('id'),
                        'displayName': u.get('displayName', u.get('username', '')),
                        'role': u.get('role', 'employee'),
                        'teamIds': u.get('teamIds', []),
                        'subordinates': get_subordinates(u.get('id'), depth + 1)
                    })
            return result

        subordinates = get_subordinates(user_id)

        self._send_json(200, {
            'userId': user_id,
            'subordinates': subordinates
        })

    def _handle_update_user_role(self, user_id):
        """PUT /api/users/:id/role — 修改用户角色（仅admin）"""
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

        new_role = body.get('role')
        if new_role not in ('admin', 'leader', 'employee'):
            self._send_json(400, {'error': '无效的角色'})
            return

        users = _load_users()
        user = _find_user(users, 'id', user_id)
        if not user:
            self._send_json(404, {'error': '用户不存在'})
            return

        old_role = user.get('role', 'employee')
        user['role'] = new_role

        # role 从 employee → leader：需要指定管理的 teamId
        if old_role == 'employee' and new_role == 'leader':
            team_id = body.get('teamId')
            if team_id:
                user['teamIds'] = user.get('teamIds', []) + [team_id]
                # 更新小组的 leaderId
                teams = _load_teams()
                team = _find_team(teams, 'id', team_id)
                if team:
                    team['leaderId'] = user_id
                    if user_id not in team.get('members', []):
                        team['members'].append(user_id)
                    _save_teams(teams)

        # role 从 leader → employee：清除 subordinateIds 和管理的 teamId 的 leaderId
        if old_role == 'leader' and new_role == 'employee':
            # 清除 subordinateIds
            user['subordinateIds'] = []
            # 清除所有作为 leader 的小组
            teams = _load_teams()
            for t in teams:
                if t.get('leaderId') == user_id:
                    t['leaderId'] = None
            _save_teams(teams)

        _save_users(users)

        self._send_json(200, {
            'id': user.get('id'),
            'username': user.get('username', ''),
            'role': user.get('role', 'employee'),
            'displayName': user.get('displayName', user.get('username', '')),
            'teamIds': user.get('teamIds', []),
            'subordinateIds': user.get('subordinateIds', [])
        })

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
        try:
            auth = _authenticate(self.headers)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return

            groups = _load_groups()
            # 管理员看全部，普通用户只看自己创建的
            if not auth.is_admin:
                uid = auth.user_info['userId']
                result = [g for g in groups if g.get('createdBy') == uid]
                self._send_json(200, result)
                return

            self._send_json(200, groups)
        except Exception as e:
            print(f'  [ERROR] _handle_get_groups: {e}', flush=True)
            try:
                self._send_json(200, [])
            except:
                pass

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


# ─── Teams API (V2) ───────────────────────────────────

    def _handle_get_teams(self):
        """GET /api/teams — 列出小组（按权限过滤）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        teams = _load_teams()
        users = _load_users()
        agents = _load_agents()

        result = []
        for t in teams:
            # 权限过滤
            if auth.is_admin:
                visible = True
            elif auth.is_leader:
                # leader 可见自己管理的小组 + 子组
                visible = t.get('leaderId') == auth.user_info.get('userId') or t.get('id') in auth.managed_team_ids
            else:
                # employee 可见自己所属的小组
                visible = auth.user_info.get('userId') in t.get('members', [])

            if visible:
                # 构建响应
                leader_name = ''
                leader = _find_user(users, 'id', t.get('leaderId'))
                if leader:
                    leader_name = leader.get('displayName', leader.get('username', ''))

                # 计算子组
                children = [s.get('id') for s in teams if s.get('parentId') == t.get('id')]

                team_info = {
                    'id': t.get('id'),
                    'name': t.get('name', ''),
                    'description': t.get('description', ''),
                    'parentId': t.get('parentId'),
                    'leaderId': t.get('leaderId'),
                    'leader': t.get('leaderId'),
                    'leaderName': leader_name,
                    'memberCount': len(t.get('members', [])),
                    'agentCount': len(t.get('agentIds', [])),
                    'members': t.get('members', []),
                    'note': t.get('note', ''),
                    'children': children,
                    'createdAt': t.get('createdAt', '')
                }
                result.append(team_info)

        self._send_json(200, result)

    def _handle_get_team(self, team_id):
        """GET /api/teams/:id — 获取小组详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 权限检查
        if not auth.is_admin:
            if auth.is_leader:
                if team.get('leaderId') != auth.user_info.get('userId') and team_id not in auth.managed_team_ids:
                    self._send_json(403, {'error': '权限不足'})
                    return
            else:
                if auth.user_info.get('userId') not in team.get('members', []):
                    self._send_json(403, {'error': '权限不足'})
                    return

        users = _load_users()
        # 获取成员详情
        members = []
        for uid in team.get('members', []):
            u = _find_user(users, 'id', uid)
            if u:
                members.append({
                    'id': u.get('id'),
                    'username': u.get('username', ''),
                    'displayName': u.get('displayName', u.get('username', '')),
                    'role': u.get('role', 'employee'),
                    'avatar': u.get('avatar', 0)
                })

        # 获取子组
        children = [_find_team(teams, 'id', s.get('id')) for s in teams if s.get('parentId') == team_id]
        children_info = [{'id': c.get('id'), 'name': c.get('name', '')} for c in children if c]

        self._send_json(200, {
            'id': team.get('id'),
            'name': team.get('name', ''),
            'description': team.get('description', ''),
            'parentId': team.get('parentId'),
            'leaderId': team.get('leaderId'),
            'leader': team.get('leaderId'),
            'members': members,
            'memberIds': team.get('members', []),
            'agentIds': team.get('agentIds', []),
            'note': team.get('note', ''),
            'children': children_info,
            'createdAt': team.get('createdAt', ''),
            'createdBy': team.get('createdBy', '')
        })

    def _handle_get_team_member(self, team_id, user_id):
        """GET /api/teams/:id/members/:userId"""
        # 权限检查同 GET /api/teams/:id
        self._handle_get_team(team_id)

    def _handle_create_team(self):
        """POST /api/teams — 创建小组（仅admin）"""
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

        name = body.get('name', '').strip()
        if not name:
            self._send_json(400, {'error': '小组名称不能为空'})
            return

        teams = _load_teams()
        users = _load_users()

        team_id = 'team_' + uuid.uuid4().hex[:8]
        leader_id = body.get('leader') or body.get('leaderId')
        member_ids = body.get('memberIds', [])
        parent_id = body.get('parentId')
        agent_ids = body.get('agentIds', [])

        # 验证父组存在
        if parent_id:
            parent = _find_team(teams, 'id', parent_id)
            if not parent:
                self._send_json(400, {'error': '父小组不存在'})
                return

        # 更新 leader 的 subordinateIds 和 teamIds
        if leader_id:
            u = _find_user(users, 'id', leader_id)
            if u:
                if team_id not in u.get('teamIds', []):
                    u['teamIds'] = u.get('teamIds', []) + [team_id]
                # 将成员添加到 leader 的 subordinateIds
                current_subs = u.get('subordinateIds', [])
                for mid in member_ids:
                    if mid not in current_subs:
                        current_subs.append(mid)
                u['subordinateIds'] = current_subs

        # 更新成员的 teamIds
        for uid in member_ids:
            u = _find_user(users, 'id', uid)
            if u:
                if team_id not in u.get('teamIds', []):
                    u['teamIds'] = u.get('teamIds', []) + [team_id]

        # 创建小组
        team = {
            'id': team_id,
            'name': name,
            'description': body.get('description', ''),
            'parentId': parent_id,
            'leaderId': leader_id,
            'leader': leader_id,
            'members': [leader_id] + member_ids if leader_id else member_ids,
            'agentIds': agent_ids,
            'note': body.get('note', ''),
            'createdAt': datetime.now().isoformat(),
            'createdBy': auth.user_info.get('userId')
        }
        teams.append(team)
        _save_teams(teams)
        _save_users(users)

        self._send_json(201, team)

    def _handle_update_team(self, team_id):
        """PUT /api/teams/:id — 更新小组"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 权限检查：admin 可改全部，leader 只能改自己管理的组
        if not auth.is_admin:
            if not auth.is_leader or team.get('leaderId') != auth.user_info.get('userId'):
                self._send_json(403, {'error': '权限不足'})
                return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        users = _load_users()

        # 更新字段
        if body.get('name'):
            team['name'] = body.get('name').strip()
        if body.get('description') is not None:
            team['description'] = body.get('description')
        if body.get('parentId') is not None:
            team['parentId'] = body.get('parentId') or None
        old_leader = team.get('leaderId')
        new_leader = body.get('leader') or body.get('leaderId')
        if new_leader is not None:
            team['leaderId'] = new_leader
            team['leader'] = new_leader
            # leader 变更时更新相关用户的 teamIds
            if new_leader != old_leader:
                # 从新 leader 的 teamIds 中添加
                if new_leader:
                    new_leader_user = _find_user(users, 'id', new_leader)
                    if new_leader_user and team_id not in new_leader_user.get('teamIds', []):
                        new_leader_user['teamIds'] = new_leader_user.get('teamIds', []) + [team_id]
                # 从旧 leader 的 teamIds 中移除（如果不是小组成员）
                if old_leader:
                    old_leader_user = _find_user(users, 'id', old_leader)
                    if old_leader_user:
                        old_leader_user['teamIds'] = [tid for tid in old_leader_user.get('teamIds', []) if tid != team_id]
        if body.get('note') is not None:
            team['note'] = body.get('note', '')

        _save_teams(teams)
        _save_users(users)
        self._send_json(200, team)

    def _handle_delete_team(self, team_id):
        """DELETE /api/teams/:id — 删除小组（仅admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        err, status = _require_admin(auth)
        if err:
            self._send_auth_error(err, status)
            return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 检查是否有子组
        has_children = any(t.get('parentId') == team_id for t in teams)
        if has_children:
            self._send_json(403, {'error': '无法删除有子组的小组，请先删除子组'})
            return

        # 解除成员关联
        users = _load_users()
        for uid in team.get('members', []):
            u = _find_user(users, 'id', uid)
            if u:
                u['teamIds'] = [tid for tid in u.get('teamIds', []) if tid != team_id]
                if uid == team.get('leaderId'):
                    u['subordinateIds'] = [sid for sid in u.get('subordinateIds', []) if sid not in team.get('members', [])]

        # 删除小组
        teams = [t for t in teams if t.get('id') != team_id]
        _save_teams(teams)
        _save_users(users)

        self._send_json(200, {'message': '小组已删除'})

    def _handle_add_team_member(self, team_id):
        """POST /api/teams/:id/members — 添加成员"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 权限检查
        if not auth.is_admin:
            if not auth.is_leader or team.get('leaderId') != auth.user_info.get('userId'):
                self._send_json(403, {'error': '权限不足'})
                return

        body = self._read_body()
        if not body or not body.get('userIds'):
            self._send_json(400, {'error': '需要提供 userIds'})
            return

        users = _load_users()
        user_ids = body.get('userIds', [])
        leader_id = team.get('leaderId')
        leader_user = _find_user(users, 'id', leader_id) if leader_id else None

        for uid in user_ids:
            if uid not in team.get('members', []):
                team['members'].append(uid)
            u = _find_user(users, 'id', uid)
            if u and team_id not in u.get('teamIds', []):
                u['teamIds'] = u.get('teamIds', []) + [team_id]
            # 更新 leader 的 subordinateIds
            if leader_user and uid not in leader_user.get('subordinateIds', []):
                leader_user['subordinateIds'] = leader_user.get('subordinateIds', []) + [uid]

        _save_teams(teams)
        _save_users(users)

        self._send_json(200, team)

    def _handle_remove_team_member(self, team_id, user_id):
        """DELETE /api/teams/:id/members/:userId — 移除成员"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 权限检查
        if not auth.is_admin:
            if not auth.is_leader or team.get('leaderId') != auth.user_info.get('userId'):
                self._send_json(403, {'error': '权限不足'})
                return

        # 移除成员
        if user_id in team.get('members', []):
            team['members'].remove(user_id)

        # 更新用户的 teamIds
        users = _load_users()
        u = _find_user(users, 'id', user_id)
        if u:
            u['teamIds'] = [tid for tid in u.get('teamIds', []) if tid != team_id]

        # 更新 leader 的 subordinateIds
        leader_id = team.get('leaderId')
        if leader_id:
            leader_user = _find_user(users, 'id', leader_id)
            if leader_user and user_id in leader_user.get('subordinateIds', []):
                leader_user['subordinateIds'] = [sid for sid in leader_user.get('subordinateIds', []) if sid != user_id]

        _save_teams(teams)
        _save_users(users)

        self._send_json(200, team)


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
        with _get_chat_lock(chat_key):
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
        elif auth.is_leader:
            # leader: 看自己管理组+子组的agents + 自己创建的
            accessible_ids = _get_accessible_agent_ids(auth)

            result = [a for a in agents
                      if a.get('id') in accessible_ids or a.get('createdBy') == auth.user_info['userId']]
        else:
            # employee: 看自己所属组的agents + 自己创建的
            accessible_ids = _get_accessible_agent_ids(auth)

            result = [a for a in agents
                      if a.get('id') in accessible_ids or a.get('createdBy') == auth.user_info['userId']]
        


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
                'badge': a.get('badge'),
                'category': a.get('category', ''),
                'subCategory': a.get('subCategory', ''),
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
        try:
            self._handle_create_agent_inner()
        except Exception as e:
            print(f'  [POST agent] ERROR: {e}', flush=True)
            import traceback; traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_create_agent_inner(self):
        """POST /api/agents (implementation)"""
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
            'role': _sanitize_role(body.get('role', '')),
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

        # 加载角色初始记忆种子
        self._save_initial_memories(new_agent['id'], new_agent.get('role', ''))

        self._send_json(201, new_agent)

    def _handle_update_agent(self, agent_id):
        """PUT /api/agents/:id"""
        try:
            auth = _authenticate(self.headers)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return

            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return

            print(f'  [PUT agent] id={agent_id} body_keys={list(body.keys())[:10]}', flush=True)

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
                if agent.get('createdBy') != auth.user_info['userId']:
                    if not (auth.is_leader and agent.get('createdBy') in _get_team_member_ids(auth)):
                        self._send_auth_error('权限不足', 403)
                        return

            # 可更新字段
            updatable = ['name', 'role', 'bg', 'avatar', 'status', 'msg', 'archived',
                         'permission', 'visibility', 'connectionType', 'apiProvider',
                         'apiModel', 'apiKey', 'openclawAgent', 'openclawModel',
                         'openclawName', 'aiProvider',
                         'systemPrompt', 'department', 'customEndpoint',
                         'group', 'pinned', 'idDoc', 'soulDoc', 'toolsDoc', 'userDoc',
                         'badge', 'createdBy', 'createdByName']
            for key in updatable:
                if key in body:
                    if key == 'role':
                        agent[key] = _sanitize_role(body[key])
                    else:
                        agent[key] = body[key]

            _save_agents(agents)
            print(f'  [PUT agent] saved ok, sending response', flush=True)
            self._send_json(200, agent)
        except Exception as e:
            print(f'  [PUT agent] ERROR: {e}', flush=True)
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

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
        if not auth.is_admin:
            if agent.get('createdBy') != auth.user_info['userId']:
                # leader可以删管理组内成员创建的agent
                if not (auth.is_leader and agent.get('createdBy') in _get_team_member_ids(auth)):
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
        """POST /api/openclaw/write-agent-docs - Write SOUL.md/IDENTITY.md/AGENTS.md to agent workspace"""
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
        user_doc = body.get('userDoc', '')
        agents_doc = body.get('agentsDoc', '')
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

            if user_doc:
                with open(os.path.join(workspace_path, 'USER.md'), 'w', encoding='utf-8') as f:
                    f.write(user_doc)
                written.append('USER.md')

            if agents_doc:
                with open(os.path.join(workspace_path, 'AGENTS.md'), 'w', encoding='utf-8') as f:
                    f.write(agents_doc)
                written.append('AGENTS.md')

            self._send_json(200, {
                'ok': True,
                'agentId': agent_id,
                'written': written,
                'workspace': workspace_path
            })
        except Exception as e:
            self._send_json(500, {'error': f'写入失败: {str(e)}'})

    def _handle_get_agent_docs(self, agent_id):
        """GET /api/openclaw/agent-docs/:agentId?doc=SOUL.md"""
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        query_params = parse_qs(parsed.query)
        doc_name = query_params.get('doc', ['SOUL.md'])[0]

        # 先从 agents.json 找 agent 数据
        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break

        if not agent:
            self._send_json(404, {'error': 'Agent 不存在'})
            return

        openclaw_name = agent.get('openclawName', '')
        if not openclaw_name:
            # 没有 OpenClaw workspace，返回 agents.json 中的字段
            content = ''
            if doc_name == 'SOUL.md':
                content = agent.get('soulDoc', agent.get('systemPrompt', ''))
            elif doc_name == 'IDENTITY.md':
                content = agent.get('idDoc', agent.get('name', '') + ' - ' + agent.get('role', ''))
            elif doc_name == 'USER.md':
                content = agent.get('userDoc', '')
            elif doc_name == 'TOOLS.md':
                content = agent.get('agentsDoc', '')
            self._send_json(200, {'content': content, 'source': 'local'})
            return

        # 从 workspace 文件读取
        import os
        workspace_path = os.path.expanduser('~/.openclaw/workspace-' + openclaw_name)
        doc_path = os.path.join(workspace_path, doc_name)

        if os.path.exists(doc_path):
            try:
                with open(doc_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._send_json(200, {'content': content, 'source': 'workspace'})
            except Exception as e:
                self._send_json(500, {'error': str(e)})
        else:
            # 文件不存在，回退到 agents.json
            content = ''
            if doc_name == 'SOUL.md':
                content = agent.get('soulDoc', agent.get('systemPrompt', ''))
            elif doc_name == 'IDENTITY.md':
                content = agent.get('idDoc', '')
            elif doc_name == 'USER.md':
                content = agent.get('userDoc', '')
            elif doc_name == 'TOOLS.md':
                content = agent.get('agentsDoc', '')
            self._send_json(200, {'content': content, 'source': 'local_fallback'})

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


    # ─── 角色初始记忆种子 ───────────────────────────────────

    def _save_initial_memories(self, agent_id, role):
        """根据角色加载并保存初始记忆种子"""
        seed_name = ROLE_MEMORY_SEED_MAP.get(role)
        if not seed_name:
            return
        
        seed_path = os.path.join(STATIC_DIR, 'docs', 'role-templates', seed_name, 'memory-seed.json')
        if not os.path.isfile(seed_path):
            print(f'  [MemorySeed] 未找到种子文件: {seed_path}', flush=True)
            return
        
        try:
            seed_data = _read_json(seed_path, {})
            initial_memories = seed_data.get('initial_memory', [])
            if not initial_memories:
                return
            
            filepath = os.path.join(MEMORY_DIR, f'{agent_id}.json')
            memories = _read_json(filepath, [])
            
            for mem_value in initial_memories:
                if not mem_value or len(mem_value) < 3:
                    continue
                memory = {
                    'id': str(uuid.uuid4())[:8],
                    'key': 'core',
                    'value': mem_value,
                    'source': '角色初始记忆(' + seed_name + ')',
                    'time': int(time.time() * 1000)
                }
                memories.append(memory)
            
            _write_json(filepath, memories)
            print(f'  [MemorySeed] {agent_id} 已加载 {len(initial_memories)} 条初始记忆 ({seed_name})', flush=True)
        except Exception as e:
            print(f'  [MemorySeed] 加载失败: {e}', flush=True)


    # ─── 记忆 API ─────────────────────────────────────────

    # 记忆过期配置：日常记录30天后过期，核心记忆不过期
    MEMORY_DAILY_TTL_DAYS = 30

    def _cleanup_expired_memories(self, memories):
        """清理过期记忆：日常记录超过30天过期，核心记忆保留"""
        now = int(time.time() * 1000)
        ttl_ms = self.MEMORY_DAILY_TTL_DAYS * 24 * 3600 * 1000
        cleaned = []
        expired_count = 0
        for m in memories:
            key = m.get('key', 'auto')
            is_core = key not in ('auto', 'auto_extract')
            if is_core:
                cleaned.append(m)
                continue
            mem_time = m.get('time', 0)
            if mem_time and (now - mem_time) > ttl_ms:
                expired_count += 1
                continue
            cleaned.append(m)
        if expired_count > 0:
            print(f'  [MemoryCleanup] 清理 {expired_count} 条过期日常记录', flush=True)
        return cleaned

    def _handle_get_memory(self, emp_id):
        """GET /api/memory/{empId} — 获取记忆列表（自动过滤过期）"""
        filepath = os.path.join(MEMORY_DIR, f'{emp_id}.json')
        memories = _read_json(filepath, [])
        memories = self._cleanup_expired_memories(memories)
        # 如果清理后有变化，写回文件
        _write_json(filepath, memories)
        self._send_json(200, memories)

    def _handle_post_memory(self, emp_id):
        """POST /api/memory/{empId} — 添加记忆（自动清理过期）"""
        body = self._read_body()
        if not body or 'value' not in body:
            self._send_json_error(400, 'Missing value')
            return
        
        filepath = os.path.join(MEMORY_DIR, f'{emp_id}.json')
        memories = _read_json(filepath, [])
        
        # 先清理过期记忆
        memories = self._cleanup_expired_memories(memories)
        
        memory = {
            'id': str(uuid.uuid4())[:8],
            'key': body.get('key', 'auto'),
            'value': body.get('value', ''),
            'source': body.get('source', ''),
            'time': int(time.time() * 1000)
        }
        memories.append(memory)
        
        # 上限200条，超出删最旧的（避免长期使用时记忆丢失）
        if len(memories) > 200:
            memories = memories[-200:]
        
        _write_json(filepath, memories)
        self._send_json(200, memory)

    def _handle_delete_memory(self, emp_id, memory_id):
        """DELETE /api/memory/{empId}/{memoryId} — 删除单条记忆"""
        filepath = os.path.join(MEMORY_DIR, f'{emp_id}.json')
        memories = _read_json(filepath, [])
        memories = [m for m in memories if m.get('id') != memory_id]
        _write_json(filepath, memories)
        self._send_json(200, {'deleted': True})

    def _handle_update_memory(self, emp_id, memory_id):
        """PUT /api/memory/{empId}/{memoryId} — 修改单条记忆"""
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return

        filepath = os.path.join(MEMORY_DIR, f'{emp_id}.json')
        memories = _read_json(filepath, [])

        updated = None
        for m in memories:
            if m.get('id') == memory_id:
                if 'value' in body:
                    m['value'] = body['value']
                if 'key' in body:
                    m['key'] = body['key']
                if 'source' in body:
                    m['source'] = body['source']
                m['time'] = int(time.time() * 1000)
                updated = m
                break

        if not updated:
            self._send_json_error(404, 'Memory not found')
            return

        _write_json(filepath, memories)
        self._send_json(200, updated)

    def _handle_get_chat(self, agent_id):
        """GET /api/chat/:agentId?type=personal|group"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        # 解析 type 参数
        query = urlparse(self.path).query
        query_params = parse_qs(query) if query else {}
        chat_type = query_params.get('type', ['personal'])[0]

        messages = _load_chat(agent_id)
        if not isinstance(messages, list):
            messages = []

        # type=personal 时过滤掉带 groupId 的消息（群聊消息不应出现在个人聊天）
        if chat_type == 'personal':
            original_len = len(messages)
            messages = [m for m in messages if not m.get('groupId')]
            if len(messages) < original_len:
                print(f'  [ChatFilter] {agent_id}: 过滤了 {original_len - len(messages)} 条群聊消息')

        # 统计角色分布，便于排查 user 消息是否丢失
        role_counts = {}
        for m in messages:
            r = m.get('role', 'unknown')
            role_counts[r] = role_counts.get(r, 0) + 1
        print(f'  [ChatGET] {agent_id} type={chat_type} 返回 {len(messages)} 条消息, 角色分布: {role_counts}')
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

        role = body.get('role', 'user')
        if role not in ('user', 'assistant', 'system'):
            role = 'user'
        msg = {
            'id': 'msg_' + uuid.uuid4().hex[:8],
            'role': role,
            'content': body.get('content', ''),
            'timestamp': datetime.now().isoformat(),
        }
        if role == 'user':
            msg['userId'] = auth.user_info['userId']
        # 保留前端传入的 empId，便于前端渲染时做归属过滤
        _emp_id = body.get('empId')
        if _emp_id:
            msg['empId'] = _emp_id
        # 保留 groupId（如果有），便于后端过滤
        _group_id = body.get('groupId')
        if _group_id:
            msg['groupId'] = _group_id

        with _get_chat_lock(agent_id):
            messages = _load_chat(agent_id)
            if not isinstance(messages, list):
                messages = []
            messages.append(msg)
            original_len = len(messages)

            # 如果前端标记 skipAI（AI已通过OpenClaw回复），跳过API代理
            skip_ai = body.get('skipAI', False)
            connection_type = agent.get('connectionType', '')
            if not skip_ai and connection_type == 'api':
                api_reply = self._call_ai_api(agent, body.get('content', ''), auth.user_info)
                if api_reply:
                    ai_message = {
                        'id': 'msg_' + uuid.uuid4().hex[:8],
                        'role': 'assistant',
                        'content': api_reply,
                        'timestamp': datetime.now().isoformat()
                    }
                    if _emp_id:
                        ai_message['empId'] = _emp_id
                    messages.append(ai_message)
                    _save_chat(agent_id, messages)
                    print(f'  [ChatPOST] {agent_id} API代理 保存 {len(messages)} 条消息')
                    self._send_json(200, {'userMessage': msg, 'aiMessage': ai_message})
                    return

            # OpenClaw 或其他
            _save_chat(agent_id, messages)
            print(f'  [ChatPOST] {agent_id} role={role} skipAI={skip_ai} 保存后共 {len(messages)} 条消息')

        if connection_type == 'openclaw':
            self._send_json(200, {
                'userMessage': msg,
                'hint': '请通过 WebSocket 连接获取 AI 回复'
            })
        else:
            self._send_json(200, {'userMessage': msg})

    def _call_ai_api(self, agent, user_message, user_info=None):
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
        # 注入层级关系约束，防止 AI 把老板当学生/下属
        if user_info:
            user_name = user_info.get('name') or user_info.get('displayName') or '用户'
            user_role = user_info.get('role', '用户')
            role_display = '老板/负责人' if user_role == 'admin' else ('组长' if user_role == 'leader' else '员工')
            system_prompt += f'\n\n【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- {user_name}（{role_display}）是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。\n- 严禁用"教你""指导你""你做过吗""你懂吗"等居高临下的语气。\n- 老板问你问题时，直接回答，不要反问或考验老板。'

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
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8', errors='replace')
            print(f'  ❌ AI API call failed: HTTP {e.code} {e.reason}', flush=True)
            print(f'      Provider: {api_provider}, URL: {target_url}', flush=True)
            print(f'      Response: {error_body}', flush=True)
        except Exception as e:
            print(f'  ❌ AI API call failed: {e}', flush=True)

        return None

    def _handle_delete_chat_message(self, agent_id, msg_id):
        """DELETE /api/chat/:agentId/:msgId?type=..."""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        with _get_chat_lock(agent_id):
            messages = _load_chat(agent_id)
            if not isinstance(messages, list):
                messages = []
            original_len = len(messages)
            messages = [m for m in messages if m.get('id') != msg_id]
            if len(messages) == original_len:
                self._send_json(404, {'error': '消息不存在'})
                return

            _save_chat(agent_id, messages)
            print(f'  [ChatDELETE] {agent_id} 删除消息 {msg_id}，剩余 {len(messages)} 条')
        self._send_json(200, {'message': '消息已删除'})

    def _handle_clear_chat(self, agent_id):
        """DELETE /api/chat/:agentId?type=... - 清空聊天记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        chat_file = os.path.join(CHATS_DIR, f'{agent_id}.json')
        if os.path.isfile(chat_file):
            try:
                os.remove(chat_file)
                print(f'  [ChatCLEAR] {agent_id} 聊天记录已清空')
            except OSError as e:
                print(f'  [ChatCLEAR] {agent_id} 清空失败: {e}')
                pass
        else:
            print(f'  [ChatCLEAR] {agent_id} 文件不存在，无需清空')

        self._send_json(200, {'message': '聊天记录已清空'})

    def _handle_get_summarize(self, agent_id):
        """GET /api/chat/summarize/:agentId - 读取已保存的对话摘要"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
        data = _read_json(summary_file, {})
        summary = data.get('summary', '')
        self._send_json(200, {'summary': summary, 'createdAt': data.get('createdAt', '')})

    def _handle_summarize_chat(self, agent_id):
        """POST /api/chat/summarize/:agentId - 将旧对话压缩成摘要"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        agent, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return

        messages = _load_chat(agent_id)
        if len(messages) <= 15:  # 15条以内不需要压缩
            return self._send_json(200, {'summary': '', 'kept': len(messages)})

        # 取前 N-10 条做摘要（保留最近10条原文=5轮）
        old_messages = messages[:-10]

        # 拼接旧对话文本
        chat_text = ''
        for msg in old_messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            chat_text += ('用户' if role == 'user' else 'AI') + ': ' + content[:200] + '\n'

        # 调 AI 做摘要
        summary = self._call_ai_for_summary(agent, chat_text)

        # 保存摘要到单独文件
        summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
        _write_json(summary_file, {'summary': summary, 'createdAt': datetime.now().isoformat()})

        # 同时写入 Mac 桌面知识库目录，方便用户查看和编辑
        emp_name = agent.get('name', agent_id)
        desktop_knowledge_dir = os.path.join(
            os.path.expanduser('~'), 'Desktop', 'solobrave', 'knowledge',
            emp_name, 'knowledge'
        )
        os.makedirs(desktop_knowledge_dir, exist_ok=True)
        md_path = os.path.join(desktop_knowledge_dir, 'summary.md')
        md_content = f'# {emp_name} 的对话摘要\n\n> 生成时间: {datetime.now().isoformat()}\n> 压缩消息数: {len(old_messages)}\n> 保留最近: 10 条\n\n---\n\n{summary}\n'
        try:
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(md_content)
            print(f'  [Summarize] 已写入桌面知识库: {md_path}', flush=True)
        except Exception as e:
            print(f'  [Summarize] 写入桌面知识库失败: {e}', flush=True)

        self._send_json(200, {
            'summary': summary,
            'compressed': len(old_messages),
            'kept': 10
        })

    def _call_ai_for_summary(self, agent, chat_text):
        """调用AI压缩对话为摘要（带降级逻辑：AI不可用时截取最近N条消息）"""
        prompt = '请将以下对话历史压缩成一段简洁的摘要（200字以内），保留关键信息、决策和重要事实：\n\n' + chat_text
        try:
            result = self._call_ai_api(agent, prompt)
            if result:
                return result[:500]
        except Exception as e:
            print(f'  [Summary] AI摘要失败: {e}', flush=True)

        # 降级：AI不可用时，截取最近 N 条消息文本作为摘要
        lines = chat_text.strip().split('\n')
        fallback_lines = lines[-10:] if len(lines) > 10 else lines
        fallback = '\n'.join(fallback_lines).strip()
        if len(fallback) > 500:
            fallback = fallback[:500] + '...'
        if fallback:
            print(f'  [Summary] AI 不可用，已降级为文本截取（{len(fallback_lines)} 条消息）', flush=True)
            return fallback
        return ''

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
    # 技能管理 API (OpenClaw Skills)
    # ═══════════════════════════════════════════════════

    def _handle_skills_list(self):
        """GET /api/openclaw/skills/list - 列出已安装技能"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        success, stdout, stderr, rc = _run_openclaw(['skill', 'list', '--json'])

        if not success:
            self._send_json(200, {
                'skills': [],
                'warning': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(200, {
                'skills': [],
                'warning': stderr.strip() or 'Command failed'
            })
            return

        try:
            data = json.loads(stdout.strip())
            if isinstance(data, list):
                self._send_json(200, {'skills': data})
            elif isinstance(data, dict) and 'skills' in data:
                self._send_json(200, data)
            else:
                self._send_json(200, {'skills': []})
        except json.JSONDecodeError:
            lines = stdout.strip().split('\n')
            skills = []
            for line in lines:
                line = line.strip()
                if line and not line.startswith('#'):
                    parts = line.split()
                    if len(parts) >= 2:
                        skills.append({'name': parts[0], 'version': parts[1]})
                    else:
                        skills.append({'name': line, 'version': ''})
            self._send_json(200, {'skills': skills, 'source': 'text-parse'})

    def _handle_skills_search(self):
        """GET /api/openclaw/skills/search?q=keyword - 搜索社区技能"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        query = ''
        if '?' in self.path:
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            query = qs.get('q', [''])[0]

        if not query:
            self._send_json(400, {'error': 'Missing query parameter "q"'})
            return

        success, stdout, stderr, rc = _run_openclaw(['skill', 'search', query, '--json'])

        if not success:
            self._send_json(200, {
                'results': [],
                'query': query,
                'warning': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(200, {
                'results': [],
                'query': query,
                'warning': stderr.strip() or 'Command failed'
            })
            return

        try:
            data = json.loads(stdout.strip())
            if isinstance(data, list):
                self._send_json(200, {'results': data, 'query': query})
            elif isinstance(data, dict) and 'results' in data:
                self._send_json(200, data)
            else:
                self._send_json(200, {'results': [data] if isinstance(data, dict) else [], 'query': query})
        except json.JSONDecodeError:
            self._send_json(200, {'results': [], 'query': query, 'raw': stdout.strip()})

    def _handle_skills_install(self):
        """POST /api/openclaw/skills/install - 安装技能"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': 'Invalid JSON body'})
            return

        skill_name = body.get('skillName', '').strip()
        if not skill_name:
            self._send_json(400, {'error': 'skillName is required'})
            return

        success, stdout, stderr, rc = _run_openclaw(['skill', 'install', skill_name])

        if not success:
            self._send_json(500, {
                'success': False,
                'skillName': skill_name,
                'error': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(500, {
                'success': False,
                'skillName': skill_name,
                'error': stderr.strip() or 'Installation failed',
                'output': stdout.strip()
            })
            return

        self._send_json(200, {
            'success': True,
            'skillName': skill_name,
            'output': stdout.strip()
        })

    def _handle_skills_remove(self):
        """POST /api/openclaw/skills/remove - 卸载技能"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': 'Invalid JSON body'})
            return

        skill_name = body.get('skillName', '').strip()
        if not skill_name:
            self._send_json(400, {'error': 'skillName is required'})
            return

        success, stdout, stderr, rc = _run_openclaw(['skill', 'remove', skill_name])

        if not success:
            self._send_json(500, {
                'success': False,
                'skillName': skill_name,
                'error': stderr or 'OpenClaw CLI not available'
            })
            return

        if rc != 0:
            self._send_json(500, {
                'success': False,
                'skillName': skill_name,
                'error': stderr.strip() or 'Removal failed',
                'output': stdout.strip()
            })
            return

        self._send_json(200, {
            'success': True,
            'skillName': skill_name,
            'output': stdout.strip()
        })

    # ═══════════════════════════════════════════════════
    # 飞书渠道配置 API
    # ═══════════════════════════════════════════════════

    def _handle_feishu_status(self):
        """GET /api/openclaw/channels/feishu/status"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        import os
        config_path = os.path.expanduser('~/.openclaw/openclaw.json')
        config = _read_json(config_path, {})
        channels = config.get('channels', {})
        feishu = channels.get('feishu', {})
        accounts = feishu.get('accounts', {})
        default_account = accounts.get('default', accounts.get('main', {}))

        # 新格式优先从顶层读，fallback 到 accounts.default
        app_id = feishu.get('appId', default_account.get('appId', ''))
        app_secret = feishu.get('appSecret', default_account.get('appSecret', ''))
        bot_name = default_account.get('name', default_account.get('botName', '全可AI助手'))

        masked_secret = ''
        if app_secret:
            masked_secret = app_secret[:4] + '*' * (len(app_secret) - 4) if len(app_secret) > 4 else '****'

        # 检查连接状态 - 通过 openclaw channels status 判断
        connected = feishu.get('enabled', False)

        self._send_json(200, {
            'appId': app_id,
            'appSecret': masked_secret,
            'botName': bot_name,
            'dmPolicy': feishu.get('dmPolicy', 'pairing'),
            'domain': feishu.get('domain', 'feishu'),
            'enabled': feishu.get('enabled', False),
            'connected': connected,
            'paired': True  # 如果有 enabled=true 且配置完整就认为已配对
        })

    def _handle_feishu_config(self):
        """POST /api/openclaw/channels/feishu"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        app_id = body.get('appId', '').strip()
        app_secret = body.get('appSecret', '').strip()
        bot_name = body.get('botName', '全可AI助手').strip()
        dm_policy = body.get('dmPolicy', 'pairing')
        enabled = body.get('enabled', True)

        if not app_id:
            self._send_json(400, {'error': 'App ID 不能为空'})
            return

        import os
        import shutil
        config_path = os.path.expanduser('~/.openclaw/openclaw.json')

        # 读取现有配置
        config = _read_json(config_path, {})
        if 'channels' not in config:
            config['channels'] = {}

        # 备份原文件
        if os.path.exists(config_path):
            shutil.copy2(config_path, config_path + '.bak')

        # 更新飞书配置 - appSecret 为空时保留原值
        feishu_cfg = config.get('channels', {}).get('feishu', {})
        existing_accounts = feishu_cfg.get('accounts', {})
        existing_default = existing_accounts.get('default', {})
        
        if not app_secret:
            app_secret = feishu_cfg.get('appSecret', existing_default.get('appSecret', ''))
        
        # 新格式：顶层 + accounts.default 双份，与 openclaw channels add 一致
        config['channels']['feishu'] = {
            'enabled': enabled,
            'dmPolicy': dm_policy,
            'domain': feishu_cfg.get('domain', 'feishu'),
            'appId': app_id,
            'appSecret': app_secret,
            'accounts': {
                'default': {
                    'appId': app_id,
                    'appSecret': app_secret,
                    'name': bot_name
                }
            }
        }

        # 保存配置
        try:
            _write_json(config_path, config)
        except Exception as e:
            self._send_json(500, {'error': f'保存配置失败: {str(e)}'})
            return

        # 自动重启 Gateway
        success, stdout, stderr, rc = _run_openclaw(['gateway', 'restart'])
        if success and rc == 0:
            self._send_json(200, {
                'success': True,
                'message': '飞书配置已保存，Gateway 已重启',
                'appId': app_id,
                'botName': bot_name
            })
        else:
            self._send_json(200, {
                'success': True,
                'message': '飞书配置已保存，但 Gateway 重启失败',
                'warning': stderr or 'Gateway restart failed',
                'appId': app_id,
                'botName': bot_name
            })

    def _handle_pairing_approve(self):
        """POST /api/openclaw/pairing/approve"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        channel = body.get('channel', 'feishu')
        code = body.get('code', '').strip()

        if not code:
            self._send_json(400, {'error': '配对码不能为空'})
            return

        success, stdout, stderr, rc = _run_openclaw(['pairing', 'approve', channel, code])

        if success and rc == 0:
            self._send_json(200, {
                'success': True,
                'message': '配对码已批准',
                'channel': channel,
                'code': code
            })
        else:
            self._send_json(500, {
                'success': False,
                'error': stderr.strip() or '配对批准失败',
                'output': stdout.strip()
            })

    def _handle_gateway_restart(self):
        """POST /api/openclaw/gateway/restart"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        success, stdout, stderr, rc = _run_openclaw(['gateway', 'restart'])

        if success and rc == 0:
            self._send_json(200, {
                'success': True,
                'message': 'Gateway 已重启',
                'output': stdout.strip()
            })
        else:
            self._send_json(500, {
                'success': False,
                'error': stderr.strip() or 'Gateway 重启失败',
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
    global PORT, BIND
    import argparse
    _default_port = PORT
    _default_bind = BIND
    parser = argparse.ArgumentParser(description='SoloBrave Server')
    parser.add_argument('port', nargs='?', type=int, default=_default_port, help='Listen port (default: 8080)')
    parser.add_argument('--bind', default=_default_bind, help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--data', default=None, help='Data directory (default: ~/.solobrave-data)')
    args = parser.parse_args()
    PORT = args.port
    BIND = args.bind

    # Override data directory if specified
    if args.data:
        global DATA_DIR, SECRET_FILE, USERS_FILE, AGENTS_FILE, GROUPS_FILE, CHATS_DIR, SETTINGS_FILE, TEAMS_FILE, MEMORY_DIR
        DATA_DIR = os.path.abspath(args.data)
        SECRET_FILE = os.path.join(DATA_DIR, '.secret')
        USERS_FILE = os.path.join(DATA_DIR, 'users.json')
        AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')
        GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')
        CHATS_DIR = os.path.join(DATA_DIR, 'chats')
        SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
        TEAMS_FILE = os.path.join(DATA_DIR, 'teams.json')
        MEMORY_DIR = os.path.join(DATA_DIR, 'memory')

    # 确保数据目录
    _ensure_data_dir()

    # 启动时主动清理历史遗留默认员工数据
    _clean_agents_file()

    # 初始化默认管理员
    _init_default_admin()

    # 确保 teams.json 存在
    if not os.path.isfile(TEAMS_FILE):
        _save_teams([])
        print('  [TEAM] 初始化 teams.json')

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
        print(f'  [CLAW] OpenClaw CLI: OK ({OPENCLAW_CLI})')
    else:
        print(f'  [CLAW] OpenClaw CLI: NOT FOUND ({OPENCLAW_CLI})')

    # Allow port reuse to avoid "Address already in use"
    class ReuseHTTPServer(http.server.ThreadingHTTPServer):
        allow_reuse_address = True
        daemon_threads = True
    server = ReuseHTTPServer((BIND, PORT), SoloBraveHandler)

    print('=' * 56)
    print('  [SOLO] SoloBrave Server (Auth Enabled)')
    print('=' * 56)
    print(f'  [DIR] 静态文件:  {STATIC_DIR}')
    print(f'  [DIR] 数据目录:  {DATA_DIR}')
    print(f'  [URL] 本机访问:  http://localhost:{PORT}')
    print(f'  [URL] 局域网:    http://0.0.0.0:{PORT}')
    print(f'  [API] 认证:      /api/auth/*')
    print(f'  [API] 用户管理:  /api/users/*')
    print(f'  [API] Agent:     /api/agents/*')
    print(f'  [API] 群组:      /api/groups/*')
    print(f'  [API] 聊天:      /api/chat/*')
    print(f'  [API] 代理:      POST /api/proxy')
    print(f'  [API] OpenClaw:  /api/openclaw/*')
    print(f'  [API] 技能:      /api/openclaw/skills/*')
    print(f'  [CFG] 超时设置:  {PROXY_TIMEOUT}s')
    print('=' * 56)
    print('  Ctrl+C 停止服务\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n\n  [STOP] 服务已停止')
        server.server_close()


if __name__ == '__main__':
    main()
