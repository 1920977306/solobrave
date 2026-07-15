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
  6. 抖音视频解析 POST /api/douyin/parse
  7. OpenClaw 管理 API

只使用 Python 标准库，无需额外依赖。
数据存储目录: <project>/data/ (可通过 --data 覆盖)
"""

import http.server
import json
import os
import subprocess
import ssl
import sys
import threading
import traceback
import urllib.request
import urllib.error
import hashlib
import hmac
import base64
import uuid
import time
import tempfile
import mimetypes
import shutil
import math
import sqlite3
try:
    import fcntl
except ImportError:
    fcntl = None
from datetime import datetime, timedelta
from urllib.parse import urlparse, unquote, parse_qs

# 抖音视频解析模块（拆分到独立文件）
from douyin_parser import *

# 记忆服务 v3（新目录结构：data/memories/{empId}/）
import memory_service_v3 as ms3

# 知识库服务（分段向量化 + 全局公共，独立模块避免循环导入）
import knowledge_service as ks

# FIXME: 大脑知识中枢新增服务
import topic_service as ts
import brain_knowledge_service as bks

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

# OpenClaw CLI 路径（支持环境变量 / PATH 探测 / mac 默认回退）
def _detect_openclaw_cli():
    env_cli = os.environ.get('OPENCLAW_CLI', '').strip()
    if env_cli and os.path.isfile(env_cli):
        return env_cli
    which_cli = shutil.which('openclaw')
    if which_cli:
        return which_cli
    return '/opt/homebrew/bin/openclaw'

OPENCLAW_CLI = _detect_openclaw_cli()
OPENCLAW_TIMEOUT = 120
OPENCLAW_DEFAULT_AGENT = os.environ.get('OPENCLAW_DEFAULT_AGENT', '').strip() or 'main'

# 数据存储目录（项目内 data/ 目录，支持 --data 覆盖）
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
SECRET_FILE = os.path.join(DATA_DIR, '.secret')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')
GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')
CHATS_DIR = os.path.join(DATA_DIR, 'chats')
SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
TEAMS_FILE = os.path.join(DATA_DIR, 'teams.json')
PERMISSIONS_FILE = os.path.join(DATA_DIR, 'permissions.json')
MEMORY_DIR = os.path.join(DATA_DIR, 'memory')
ARCHIVE_DIR = os.path.join(DATA_DIR, 'memory', 'archive')
KNOWLEDGE_DIR = os.path.join(DATA_DIR, 'knowledge')
PRODUCT_DIR = os.path.join(DATA_DIR, 'products')
INFLUENCER_DIR = os.path.join(DATA_DIR, 'influencers')
EMBEDDING_DIR = os.path.join(DATA_DIR, 'embeddings')
DB_PATH = os.path.join(DATA_DIR, 'solobrave.db')

# ═══════════════════════════════════════════════════
# Embedding 配置（RAG 向量检索）
# ═══════════════════════════════════════════════════
EMBEDDING_PROVIDERS = {
    'openai': {
        'url': 'https://api.openai.com/v1/embeddings',
        'model': 'text-embedding-3-small',
        'dim': 1536,
    },
    'kimi': {
        'url': 'https://api.moonshot.cn/v1/embeddings',
        'model': 'moonshot-v3-embedding',
        'dim': 1536,
    },
    'moonshot': {
        'url': 'https://api.moonshot.cn/v1/embeddings',
        'model': 'moonshot-v3-embedding',
        'dim': 1536,
    },
    'kimicode': {
        'url': 'https://api.kimi.com/coding/v1/embeddings',
        'model': 'kimi-for-coding',
        'dim': 1536,
    },
    'zhipu': {
        'url': 'https://open.bigmodel.cn/api/paas/v4/embeddings',
        'model': 'embedding-2',
        'dim': 1024,
    },
    'deepseek': {
        'url': 'https://api.deepseek.com/v1/embeddings',
        'model': 'text-embedding',
        'dim': 1536,
    },
    'siliconflow': {
        'url': 'https://api.siliconflow.cn/v1/embeddings',
        'model': 'BAAI/bge-large-zh-v1.5',
        'dim': 1024,
    },
}

# 全局 embedding 覆盖配置（允许 RAG 使用与聊天不同的 provider/API Key）
# 优先级：环境变量 > settings.json > agent 自身配置
EMBEDDING_OVERRIDE_PROVIDER = os.environ.get('SOLOBRAVE_EMBEDDING_PROVIDER', '').strip()
EMBEDDING_OVERRIDE_API_KEY = os.environ.get('SOLOBRAVE_EMBEDDING_API_KEY', '').strip()


# 知识归纳模拟模式开关：无真实 API Key 时返回示例知识文档，便于测试/演示
# 优先级：环境变量 > settings.json
SOLOBRAVE_KNOWLEDGE_MOCK_MODE = os.environ.get('SOLOBRAVE_KNOWLEDGE_MOCK_MODE', '').strip().lower() in ('1', 'true', 'yes', 'on')


def get_embedding_config(emp_id=None):
    """
    获取全局 embedding 配置。
    优先级：环境变量 > settings.json 中的 embedding 配置 > 员工自身 AI 配置。
    返回: {'provider': str, 'apiKey': str, 'baseUrl': str, 'model': str}
    """
    settings = _read_json(SETTINGS_FILE, {})
    emb_settings = settings.get('embedding', {}) or {}

    # 环境变量最高优先级
    provider = EMBEDDING_OVERRIDE_PROVIDER
    api_key = EMBEDDING_OVERRIDE_API_KEY

    # settings.json 中的 embedding 配置（新嵌套格式优先，兼容旧平铺格式）
    if not provider:
        provider = (emb_settings.get('provider') or settings.get('embeddingProvider', '')).strip()
    if not api_key:
        api_key = (emb_settings.get('apiKey') or settings.get('embeddingApiKey', '')).strip()

    base_url = (emb_settings.get('baseUrl', '')).strip()
    model = (emb_settings.get('model', '')).strip()

    # 全局未配置时 fallback 到员工的 aiProvider / apiKey
    if emp_id:
        agent = _get_agent_by_id(emp_id) or {}
        if not provider:
            provider = (agent.get('aiProvider', '') or agent.get('apiProvider', '')).strip()
        if not api_key:
            api_key = (agent.get('apiKey') or '').strip()
        if not model:
            model = (agent.get('embeddingModel') or '').strip()

    provider = provider or 'openai'

    # 未指定 baseUrl / model 时，从 EMBEDDING_PROVIDERS 补全
    provider_cfg = EMBEDDING_PROVIDERS.get(provider)
    if provider_cfg:
        if not base_url:
            base_url = provider_cfg['url']
        if not model:
            model = provider_cfg['model']

    return {
        'provider': provider,
        'apiKey': api_key,
        'baseUrl': base_url,
        'model': model,
    }


def _get_embedding_override():
    """获取全局 embedding 覆盖配置，返回 (provider, api_key) 或 ('', '')"""
    cfg = get_embedding_config()
    return cfg['provider'], cfg['apiKey']


def _get_embedding_config_for_user():
    """
    获取当前用户的全局 embedding 配置。
    不关联任何员工，直接返回 settings.json / 环境变量中的全局配置。
    """
    return get_embedding_config()


def _get_knowledge_mock_mode():
    """是否开启知识归纳模拟模式"""
    if SOLOBRAVE_KNOWLEDGE_MOCK_MODE:
        return True
    settings = _read_json(SETTINGS_FILE, {})
    value = settings.get('knowledgeMockMode', False)
    return str(value).strip().lower() in ('1', 'true', 'yes', 'on')

# ═══════════════════════════════════════════════════
# 记忆系统 v2 配置（三层大脑架构）
# ═══════════════════════════════════════════════════
MEMORY_CONFIG = {
    'core_max': 100,           # 核心记忆池上限
    'daily_max': 100,          # 日常记录池上限
    'daily_ttl_days': 30,      # 日常记录过期天数
    'inject_core_max': 5,      # 注入时核心记忆条数
    'inject_daily_max': 5,     # 注入时日常记忆条数
    'inject_knowledge_max': 3,  # 注入时知识库条数
    'inject_value_max': 500,   # 单条记忆注入字符上限
    'store_value_max': 2000,   # 单条记忆存储字符上限
    'history_inject_max': 10,  # 聊天历史注入条数
    'summarize_threshold': 20, # 归纳触发阈值（统一前后端）
    'chat_store_max': 500,     # 聊天记录存储上限
}

# 记忆归纳阈值（统一由 memory_service_v3.py 维护，便于后续调整）
MEMORY_INDUCTION_THRESHOLDS = ms3.MEMORY_INDUCTION_THRESHOLDS

# FIXME: 大脑知识中枢：后端 OpenClaw AI 调用队列（统一串行 + 重试）
class _OpenClawTaskQueue:
    """OpenClaw 任务队列：所有大脑 AI 调用统一走这里，priority=-1 最低优先级，失败重试 3 次"""

    MAX_RETRIES = 3
    RETRY_DELAY_BASE_S = 1

    def __init__(self):
        self._lock = threading.Lock()
        self._queue = []
        self._events = {}
        self._results = {}
        self._thread = None
        self._running = False

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name='OpenClawTaskQueue')
        self._thread.start()
        print('  [OpenClawQueue] started', flush=True)

    def stop(self):
        self._running = False

    def submit(self, prompt, agent=None, system_prompt=None, priority=-1, max_retries=3):
        """提交 AI 任务并阻塞等待结果；所有大脑调用 priority=-1"""
        task_id = 'oc_' + uuid.uuid4().hex[:8]
        event = threading.Event()
        with self._lock:
            self._events[task_id] = event
            self._queue.append({
                'id': task_id,
                'prompt': prompt,
                'agent': agent or {},
                'system_prompt': system_prompt,
                'priority': priority,
                'max_retries': max_retries,
                'retries': 0,
                'created_at': time.time(),
            })
            self._queue.sort(key=lambda x: x['priority'], reverse=True)
        # 等待结果，最多 120 秒
        if not event.wait(timeout=OPENCLAW_TIMEOUT + 10):
            return None
        with self._lock:
            return self._results.pop(task_id, None)

    def _loop(self):
        while self._running:
            task = None
            with self._lock:
                if self._queue:
                    task = self._queue.pop(0)
            if task:
                self._process(task)
            else:
                time.sleep(0.2)

    def _process(self, task):
        task_id = task['id']
        try:
            result = _call_ai_for_json(task['prompt'], task['agent'], system_prompt=task.get('system_prompt'))
            if result is None and task['retries'] < task['max_retries']:
                raise RuntimeError('AI returned None')
            with self._lock:
                self._results[task_id] = result
                event = self._events.pop(task_id, None)
            if event:
                event.set()
        except Exception as e:
            task['retries'] += 1
            print(f'  [OpenClawQueue] task {task_id} failed ({task["retries"]}/{task["max_retries"]}): {e}', flush=True)
            if task['retries'] <= task['max_retries']:
                delay = self.RETRY_DELAY_BASE_S * (2 ** (task['retries'] - 1))
                time.sleep(delay)
                with self._lock:
                    self._queue.append(task)
                    self._queue.sort(key=lambda x: x['priority'], reverse=True)
            else:
                with self._lock:
                    self._results[task_id] = None
                    event = self._events.pop(task_id, None)
                if event:
                    event.set()


_openclaw_queue = _OpenClawTaskQueue()

# FIXME: 大脑知识中枢全局调度器（单例，守护线程）
class _BrainScheduler:
    """后台调度器：清洗窗口聚合、主题沉淀、全量巡检"""

    # FIXME: 清洗窗口：同员工 30 秒内新增记忆合并为一次批量清洗
    CLEAN_WINDOW_MS = 30 * 1000
    INDUCT_INTERVAL_MS = 5 * 60 * 1000
    INACTIVE_TOPIC_DAYS = 30

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._running = False
        # 员工级清洗窗口：emp_id -> {'mem_ids': set(), 'run_at': ms}
        self._clean_batches = {}
        # 一次性任务队列
        self._tasks = []
        # FIXME: 归纳队列去重：记录已入队的待沉淀主题 id，防止同一主题重复入队
        self._pending_induct_ids = set()
        self._topic_svc = ts.TopicService()
        self._know_svc = bks.KnowledgeService(infer_fn=self._brain_infer)
        self._last_induct_check = 0
        self._last_daily_inspect = 0
        self._last_uncleaned_scan = 0  # FIXME: 大脑调度器定期巡检待清洗记忆
        self._today_processed = 0
        self._today_date = datetime.now().strftime('%Y-%m-%d')

    # FIXME: 大脑 AI 调用统一走后端 OpenClaw 队列，priority=-1 最低优先级
    def _brain_infer(self, prompt, agent=None):
        try:
            return _openclaw_queue.submit(
                prompt, agent=agent or self._default_agent(), priority=-1, max_retries=3
            )
        except Exception as e:
            print(f'  [BrainScheduler] AI call failed: {e}', flush=True)
            return []

    def _default_agent(self):
        """默认 agent：取任意一个可用 agent，否则返回空 dict"""
        try:
            agents = _load_agents().get('agents', [])
            return agents[0] if agents else {}
        except Exception:
            return {}

    def request_clean(self, emp_id, mem_id):
        """FIXME: 请求延迟清洗；同员工落入 30 秒窗口"""
        now = int(time.time() * 1000)
        with self._lock:
            batch = self._clean_batches.get(emp_id)
            if batch is None:
                batch = {'mem_ids': set(), 'run_at': now + self.CLEAN_WINDOW_MS}
                self._clean_batches[emp_id] = batch
            batch['mem_ids'].add(mem_id)

    def request_induct(self, topic_id):
        """FIXME: 请求沉淀某个主题；同一主题在队列中只保留一个任务"""
        with self._lock:
            # FIXME: 归纳队列去重：同一个主题 id 只能有一个待执行的归纳任务
            if topic_id in self._pending_induct_ids:
                return
            self._pending_induct_ids.add(topic_id)
            self._tasks.append({
                'type': 'induct',
                'run_at': int(time.time() * 1000),
                'payload': {'topic_id': topic_id},
                'retries': 0
            })

    def request_classify(self, emp_id, mem_id):
        """FIXME: 请求对单条记忆做主题归类"""
        with self._lock:
            self._tasks.append({
                'type': 'classify',
                'run_at': int(time.time() * 1000),
                'payload': {'emp_id': emp_id, 'mem_id': mem_id},
                'retries': 0
            })

    def _enqueue_uncleaned_memories(self):
        """FIXME: 启动时扫描所有 cleaned_at=0 的记忆并加入清洗队列"""
        try:
            conn = _db_conn()
            rows = conn.execute(
                "SELECT id, emp_id FROM memory WHERE cleaned_at = 0 AND status = 'active'"
            ).fetchall()
            conn.close()
            count = 0
            for row in rows:
                self.request_clean(row['emp_id'], row['id'])
                count += 1
            print(f'  [BrainScheduler] enqueued {count} uncleaned memories at startup', flush=True)
        except Exception as e:
            print(f'  [BrainScheduler] enqueue uncleaned memories failed: {e}', flush=True)

    def start(self):
        """FIXME: 启动大脑调度器守护线程"""
        if self._running:
            return
        self._running = True
        # FIXME: 大脑调度器启动扫库：启动时先把数据库里未清洗的记忆加入队列，不能只等新记忆
        self._enqueue_uncleaned_memories()
        self._last_uncleaned_scan = int(time.time() * 1000)
        self._thread = threading.Thread(target=self._loop, daemon=True, name='BrainScheduler')
        self._thread.start()
        print('  [BrainScheduler] started', flush=True)

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f'  [BrainScheduler] tick error: {e}', flush=True)
            time.sleep(1)

    def _tick(self):
        now = int(time.time() * 1000)
        today = datetime.now().strftime('%Y-%m-%d')
        if today != self._today_date:
            self._today_date = today
            self._today_processed = 0

        # FIXME: 大脑调度器定期巡检待清洗记忆
        if now - self._last_uncleaned_scan >= 60 * 1000:
            self._enqueue_uncleaned_memories()
            self._last_uncleaned_scan = now

        ready_tasks = []
        with self._lock:
            # 清洗窗口到期则生成任务
            for emp_id, batch in list(self._clean_batches.items()):
                if now >= batch['run_at']:
                    ready_tasks.append({'type': 'clean', 'payload': {'emp_id': emp_id, 'mem_ids': list(batch['mem_ids'])}, 'retries': 0})
                    del self._clean_batches[emp_id]
            # 取出到期任务
            remaining = []
            for t in self._tasks:
                if t.get('run_at', 0) <= now:
                    t.setdefault('retries', 0)
                    ready_tasks.append(t)
                else:
                    remaining.append(t)
            self._tasks = remaining

        for task in ready_tasks:
            try:
                self._execute_task(task)
            except Exception as e:
                print(f'  [BrainScheduler] task error: {e}', flush=True)

        # 每 5 分钟巡检待沉淀主题
        if now - self._last_induct_check >= self.INDUCT_INTERVAL_MS:
            self._last_induct_check = now
            self._check_pending_topics()

        # 每日凌晨 3 点全量巡检
        if datetime.now().hour == 3 and now - self._last_daily_inspect >= 24 * 3600 * 1000:
            self._last_daily_inspect = now
            self._daily_inspect()

    def _execute_task(self, task):
        """FIXME: 执行任务；失败时最多重试 3 次"""
        typ = task.get('type')
        payload = task.get('payload', {})
        topic_id = payload.get('topic_id') if typ == 'induct' else None
        try:
            if typ == 'clean':
                self._do_clean(payload['emp_id'], payload['mem_ids'])
            elif typ == 'induct':
                self._do_induct(topic_id)
            elif typ == 'classify':
                self._do_classify(payload['emp_id'], payload['mem_id'])
        except Exception as e:
            task['retries'] = task.get('retries', 0) + 1
            print(f'  [BrainScheduler] task failed ({task["retries"]}/3): {e}', flush=True)
            if task['retries'] <= 3:
                delay = 1000 * (2 ** (task['retries'] - 1))
                task['run_at'] = int(time.time() * 1000) + delay
                with self._lock:
                    self._tasks.append(task)
            else:
                print(f'  [BrainScheduler] task dropped after 3 retries', flush=True)
                # FIXME: 归纳队列去重：任务最终失败时释放主题 id，允许后续重新入队
                if topic_id:
                    with self._lock:
                        self._pending_induct_ids.discard(topic_id)
            return
        # FIXME: 归纳队列去重：任务执行成功后释放主题 id
        if topic_id:
            with self._lock:
                self._pending_induct_ids.discard(topic_id)

    def _do_clean(self, emp_id, mem_ids):
        """FIXME: 批量清洗 + 自动主题归类；归类只置 pending_induct=1，不直接入队"""
        print(f'  [BrainScheduler] clean {len(mem_ids)} memories for {emp_id}', flush=True)
        agent = self._default_agent()
        for mem_id in mem_ids:
            mem = ms3._clean_and_deduplicate(mem_id, emp_id)
            if mem and not mem.get('is_filler') and not mem.get('is_duplicate'):
                # FIXME: 记忆归类到主题时只置 pending_induct=1，由调度器巡检统一入队，避免重复入队
                self._topic_svc.classify_memory_to_topic(mem_id, emp_id)
            self._today_processed += 1

    def _do_induct(self, topic_id):
        """FIXME: 执行主题知识沉淀"""
        print(f'  [BrainScheduler] induct topic {topic_id}', flush=True)
        # FIXME: 归纳任务执行前再校验：若 pending_induct=0 说明已被处理过，直接跳过
        conn = _db_conn()
        try:
            row = conn.execute(
                'SELECT pending_induct FROM memory_topics WHERE id=?', (topic_id,)
            ).fetchone()
            if not row or not row['pending_induct']:
                print(f'  [BrainScheduler] topic {topic_id} already inducted, skip', flush=True)
                return
        finally:
            conn.close()
        agent = self._default_agent()
        self._know_svc.induct_topic_to_knowledge(topic_id, agent=agent)

    def _do_classify(self, emp_id, mem_id):
        """FIXME: 对记忆做主题归类；归类只置 pending_induct=1，不直接入队"""
        # FIXME: 记忆归类到主题时只置 pending_induct=1，由调度器巡检统一入队，避免重复入队
        self._topic_svc.classify_memory_to_topic(mem_id, emp_id)

    def _get_memory_row(self, mem_id):
        """FIXME: 查询 memory 表单条记录，用于迁移幂等判断"""
        try:
            conn = _db_conn()
            row = conn.execute(
                "SELECT id, cleaned_at, topic_ids FROM memory WHERE id = ? AND status='active'",
                (mem_id,)
            ).fetchone()
            conn.close()
            if row:
                return {'id': row['id'], 'cleaned_at': row['cleaned_at'], 'topic_ids': row['topic_ids']}
        except Exception as e:
            print(f'  [BrainScheduler] get_memory_row {mem_id} failed: {e}', flush=True)
        return None

    def migrate_existing_memories(self):
        """FIXME: 兼容现有数据：从 v3 记忆目录 data/memory/ 迁移 daily 记忆到 memory 表并加入清洗队列"""
        print('  [BrainScheduler] migrating existing memories', flush=True)
        migrated = 0
        enqueued = 0
        per_emp = {}  # FIXME: 记录每个员工的迁移数量
        # FIXME: v3 记忆目录是 data/memory/（ms3.MEMORY_V3_DIR 已被 main() 覆写为 MEMORY_DIR）
        memories_dir = MEMORY_DIR
        if not os.path.isdir(memories_dir):
            print(f'  [BrainScheduler] memory dir not found: {memories_dir}', flush=True)
            return
        now = int(time.time() * 1000)
        for emp_id in os.listdir(memories_dir):
            # FIXME: 只处理员工目录：以 emp_ 开头，排除 groups/、archive/、{empId} 等
            if not emp_id.startswith('emp_'):
                continue
            try:
                ms3._validate_emp_id(emp_id)
            except Exception as e:
                print(f'  [BrainScheduler] skip invalid emp_id {emp_id}: {e}', flush=True)
                continue
            mem_path = os.path.join(memories_dir, emp_id, 'memory.json')
            if not os.path.isfile(mem_path):
                continue
            try:
                data = ms3.load_memory(emp_id)
                # 为缺失 id 的 daily 记忆补 id（load_memory 已处理，但再保险一次）
                for m in data.get('daily', []):
                    if not m.get('id'):
                        m['id'] = 'mem_' + uuid.uuid4().hex[:8]
                # 只迁移 daily 池旧记忆；core 视为已人工确认，不再进入清洗
                for m in data.get('daily', []):
                    mem_id = m.get('id')
                    if not mem_id:
                        continue
                    # FIXME: 幂等：已存在的记忆不再重复插入/覆盖，只补字段
                    existing = self._get_memory_row(mem_id)
                    if existing:
                        # 若已清洗或已归类，则保持现状，不再重置
                        if existing.get('cleaned_at') or existing.get('topic_ids'):
                            continue
                    # 初始化待清洗状态，不直接归类
                    m['is_filler'] = 0
                    m['is_duplicate'] = 0
                    m['cleaned_at'] = 0
                    m['topicIds'] = []
                    m.setdefault('createdAt', now)
                    ms3._sync_memory_to_db(m, emp_id, pool='daily')
                    migrated += 1
                    per_emp[emp_id] = per_emp.get(emp_id, 0) + 1
                    # 加入清洗队列，由清洗流程自动完成去重+归类
                    self.request_clean(emp_id, mem_id)
                    enqueued += 1
                # 把补齐后的 daily 写回文件，保证后续清洗流程读取一致
                if data.get('daily'):
                    ms3.save_memory(emp_id, data)
                # FIXME: 打印每个员工的迁移数量
                if emp_id in per_emp:
                    print(f'  [BrainScheduler] {emp_id} migrated {per_emp[emp_id]} memories', flush=True)
            except Exception as e:
                print(f'  [BrainScheduler] migrate {emp_id} failed: {e}', flush=True)
        print(f'  [BrainScheduler] migrated {migrated} memories, enqueued {enqueued} clean tasks', flush=True)

    def _check_pending_topics(self):
        """FIXME: 只扫描 pending_induct=1 的主题"""
        topics = self._topic_svc.get_pending_induct_topics(min_memories=3)
        print(f'  [BrainScheduler] {len(topics)} pending topics', flush=True)
        for t in topics:
            self.request_induct(t['id'])

    def _daily_inspect(self):
        """FIXME: 每日全量巡检：归档不活跃主题、校验冲突"""
        print('  [BrainScheduler] daily inspect', flush=True)
        now = int(time.time() * 1000)
        cutoff = now - self.INACTIVE_TOPIC_DAYS * 24 * 3600 * 1000
        conn = _db_conn()
        try:
            # 归档长期未活跃主题
            conn.execute("UPDATE memory_topics SET status='archived' WHERE status='active' AND last_active_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
        # 对 active 知识做冲突检测
        agent = self._default_agent()
        for know in self._know_svc.get_all_active_knowledge(limit=200):
            try:
                self._know_svc.detect_conflicts(know['id'], agent=agent)
            except Exception as e:
                print(f'  [BrainScheduler] conflict check failed: {e}', flush=True)

    def get_stats(self):
        """FIXME: 返回大脑状态统计"""
        conn = _db_conn()
        try:
            pending_clean = conn.execute(
                "SELECT COUNT(*) FROM memory WHERE status='active' AND cleaned_at=0"
            ).fetchone()[0]
            topic_count = conn.execute(
                "SELECT COUNT(*) FROM memory_topics WHERE status='active'"
            ).fetchone()[0]
            knowledge_count = conn.execute(
                "SELECT COUNT(*) FROM knowledge_base_new WHERE status='active'"
            ).fetchone()[0]
            return {
                'pending_clean': pending_clean,
                'topic_count': topic_count,
                'knowledge_count': knowledge_count,
                'today_processed': self._today_processed,
            }
        finally:
            conn.close()

    def enqueue_all_pending(self):
        """FIXME: 手动触发：把所有待清洗/待归类记忆和待沉淀主题加入队列"""
        conn = _db_conn()
        enqueued_clean = 0
        enqueued_classify = 0
        enqueued_induct = 0
        try:
            rows = conn.execute(
                "SELECT DISTINCT emp_id FROM memory WHERE status='active' AND cleaned_at=0"
            ).fetchall()
            for r in rows:
                mems = conn.execute(
                    "SELECT id FROM memory WHERE status='active' AND cleaned_at=0 AND emp_id=?",
                    (r['emp_id'],)
                ).fetchall()
                for m in mems:
                    self.request_clean(r['emp_id'], m['id'])
                enqueued_clean += len(mems)

            # 已清洗但未归类
            rows2 = conn.execute(
                "SELECT DISTINCT emp_id FROM memory WHERE status='active' AND cleaned_at>0 AND (topic_ids='[]' OR topic_ids IS NULL)"
            ).fetchall()
            for r in rows2:
                mems = conn.execute(
                    "SELECT id FROM memory WHERE status='active' AND cleaned_at>0 AND (topic_ids='[]' OR topic_ids IS NULL) AND emp_id=?",
                    (r['emp_id'],)
                ).fetchall()
                for m in mems:
                    self.request_classify(r['emp_id'], m['id'])
                enqueued_classify += len(mems)

            topics = self._topic_svc.get_pending_induct_topics(min_memories=1)
            for t in topics:
                self.request_induct(t['id'])
                enqueued_induct += 1
            return enqueued_clean, enqueued_classify, enqueued_induct
        finally:
            conn.close()


_brain_scheduler = _BrainScheduler()

# 进程级文件锁（跨平台替代 fcntl，Windows 兼容）
_memory_file_locks = {}
_memory_locks_mutex = threading.Lock()

def _get_memory_file_lock(filepath):
    """获取文件路径对应的进程级写锁"""
    with _memory_locks_mutex:
        if filepath not in _memory_file_locks:
            _memory_file_locks[filepath] = threading.Lock()
        return _memory_file_locks[filepath]


# 角色初始记忆种子映射：前端 role -> memory-seed 文件名
# 只映射严格匹配的角色，避免加载不相关记忆导致AI行为混乱
ROLE_MEMORY_SEED_MAP = {
    '战略顾问': 'Trumind',   # Trumind = CEO战略顾问（不是CEO助理）
    '前端工程师': 'Gates',    # Gates = 技术负责人/全栈
    '后端工程师': 'Gates',
    '数据分析师': 'Black',    # Black = 商业情报/战略分析
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
    parent_dir = os.path.dirname(filepath)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    tmp_path = filepath + '.tmp.' + uuid.uuid4().hex[:8]
    # 跨平台文件锁：Unix 用 fcntl，Windows 用进程级 threading.Lock
    file_lock = _get_memory_file_lock(filepath)
    try:
        with file_lock:
            # 防御：写入前检查 agents.json 中是否有 apiKey 被污染
            if filepath == AGENTS_FILE and isinstance(data, list):
                for agent in data:
                    if isinstance(agent, dict):
                        ak = agent.get('apiKey', '')
                        if _is_log_polluted(ak):
                            print(f'  [WRITE_GUARD] 写入前发现 apiKey 被污染: {agent.get("id")} len={len(ak)} 已清空', flush=True)
                            agent['apiKey'] = ''
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


# ═══════════════════════════════════════════════════
# 记忆系统 v3（使用 memory_service_v3 模块）
# ═══════════════════════════════════════════════════
# 旧函数 _load_memory_v2 / _save_memory_v2 / _cleanup_and_archive_expired 已移除
# 活跃记忆与归档记忆物理隔离：
#   <DATA_DIR>/memory/{empId}/memory.json   ← core + daily
#   <DATA_DIR>/memory/{empId}/archived.json ← 归档
#   <DATA_DIR>/memory/consolidation_log.json ← 归纳日志
#
# v2 → v3 迁移：首次加载时自动调用 ms3.migrate_from_v2()


def _load_archive(emp_id):
    """加载某员工的归档记忆（聊天记录归档等仍使用）"""
    filepath = os.path.join(ARCHIVE_DIR, f'{emp_id}.json')
    return _read_json(filepath, {'memories': [], 'summaries': [], 'version': '1.0'})


def _save_archive(emp_id, data):
    """保存某员工的归档记忆（聊天记录归档等仍使用）"""
    filepath = os.path.join(ARCHIVE_DIR, f'{emp_id}.json')
    data['version'] = '1.0'
    _write_json(filepath, data)


def _check_agent_exists(emp_id):
    """检查员工是否存在（用于记忆API权限校验的基础检查）"""
    agents = _load_agents()
    for a in agents:
        if a.get('id') == emp_id:
            return a
    return None


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


def _ensure_knowledge_admin_agent():
    """确保存在系统知识库管理员 AI 员工"""
    agents = _load_agents(include_archived=True)
    for a in agents:
        if a.get('id') == 'knowledge_admin':
            return
    admin = {
        'id': 'knowledge_admin',
        'name': '知识库管理员',
        'role': 'operator',
        'bg': '#3B82F6',
        'avatar': '📚',
        'status': 'online',
        'msg': '',
        'archived': False,
        'permission': 'dev',
        'visibility': 'creator',
        'createdBy': 'system',
        'createdAt': datetime.now().isoformat(),
        'connectionType': '',
        'apiProvider': '',
        'apiModel': '',
        'apiKey': '',
        'openclawAgent': '',
        'openclawModel': '',
        'openclawName': '',
        'aiProvider': '',
        'systemPrompt': '',
        'department': '',
        'customEndpoint': ''
    }
    agents.append(admin)
    _save_agents(agents)
    print('  [System] 已创建知识库管理员 AI 员工: knowledge_admin', flush=True)


# ─── 权限管理 ─────────────────────────────────────────

# 可用模块列表（与 switchModule 取值对齐）
AVAILABLE_MODULES = [
    'dashboard', 'messages', 'knowledge', 'settings', 'products', 'groups', 'influencers'
]


def _default_permission_templates():
    """默认角色权限模板

    角色：超级管理员 / 管理员 / 普通用户
    模块 key：dashboard/messages/knowledge/settings/products/groups/influencers
    """
    superadmin_modules = {m: True for m in AVAILABLE_MODULES}
    admin_modules = {m: True for m in AVAILABLE_MODULES}
    # 管理员不能进入 settings（权限管理在 settings 内）
    admin_modules['settings'] = False
    user_modules = {
        'dashboard': True,
        'messages': True,
        'knowledge': True,
        'products': True,
        'groups': True,
        'influencers': True,
        'settings': False,
    }
    return {
        'version': '1.0',
        'roleTemplates': [
            {'id': 'admin', 'name': '超级管理员', 'modules': superadmin_modules, 'knowledgeCategories': ['*']},
            {'id': 'leader', 'name': '管理员', 'modules': admin_modules, 'knowledgeCategories': ['*']},
            {'id': 'employee', 'name': '普通用户', 'modules': user_modules, 'knowledgeCategories': ['*']},
        ],
        'userOverrides': {}
    }


def _load_permissions():
    """加载权限配置；不存在时初始化默认模板"""
    data = _read_json(PERMISSIONS_FILE, None)
    if not isinstance(data, dict):
        data = _default_permission_templates()
        _save_permissions(data)
    # 兼容补齐
    if 'roleTemplates' not in data or not isinstance(data['roleTemplates'], list):
        data['roleTemplates'] = _default_permission_templates()['roleTemplates']
    if 'userOverrides' not in data or not isinstance(data['userOverrides'], dict):
        data['userOverrides'] = {}
    # 补齐缺失模块键：优先使用默认模板中的值，保持向后兼容
    # 例如 products 模块是新加入的，旧权限文件缺少该键，默认给 True 避免误拒
    default_templates = {t['id']: t for t in _default_permission_templates()['roleTemplates']}
    # 如果默认角色模板被意外删除，补回默认模板，避免用户因找不到模板而被误拒
    existing_ids = {t.get('id') for t in data['roleTemplates']}
    for tmpl in _default_permission_templates()['roleTemplates']:
        if tmpl['id'] not in existing_ids:
            data['roleTemplates'].append(dict(tmpl))
    for tmpl in data['roleTemplates']:
        modules = tmpl.get('modules', {})
        default_modules = default_templates.get(tmpl.get('id'), {}).get('modules', {})
        for m in AVAILABLE_MODULES:
            if m not in modules:
                modules[m] = bool(default_modules.get(m, False))
        tmpl['modules'] = modules
    return data


def _save_permissions(data):
    """保存权限配置"""
    _write_json(PERMISSIONS_FILE, data)


def _get_role_template(permissions, role_or_template_id):
    """按 roleTemplateId 或 role 查找模板"""
    if not role_or_template_id:
        return None
    for tmpl in permissions.get('roleTemplates', []):
        if tmpl.get('id') == role_or_template_id:
            return tmpl
    # 回退：按 role 字段匹配
    fallback_map = {'admin': 'admin', 'leader': 'leader', 'employee': 'employee'}
    tid = fallback_map.get(role_or_template_id)
    if tid:
        for tmpl in permissions.get('roleTemplates', []):
            if tmpl.get('id') == tid:
                return tmpl
    return None


def _get_effective_permissions(user_or_auth):
    """合并角色模板 + 用户覆盖，返回 {modules, knowledgeCategories}"""
    permissions = _load_permissions()
    if hasattr(user_or_auth, 'user_record') and user_or_auth.user_record:
        user = user_or_auth.user_record
    elif isinstance(user_or_auth, dict):
        user = user_or_auth
    else:
        # 默认最小权限
        return {'modules': {m: False for m in AVAILABLE_MODULES}, 'knowledgeCategories': []}

    role = user.get('role', 'employee')
    template_id = user.get('roleTemplateId') or role
    template = (_get_role_template(permissions, template_id)
                or _get_role_template(permissions, role)
                or _get_role_template(permissions, 'employee')
                or {})

    base_modules = dict(template.get('modules', {}))
    base_cats = list(template.get('knowledgeCategories', []))

    override = permissions.get('userOverrides', {}).get(user.get('id', '')) or {}
    override_modules = override.get('modules', {})
    override_cats = override.get('knowledgeCategories')

    merged_modules = {m: base_modules.get(m, False) for m in AVAILABLE_MODULES}
    if isinstance(override_modules, dict):
        for m, v in override_modules.items():
            if m in AVAILABLE_MODULES:
                merged_modules[m] = bool(v)

    merged_cats = base_cats
    if isinstance(override_cats, list):
        merged_cats = override_cats

    return {'modules': merged_modules, 'knowledgeCategories': merged_cats}


def _has_module_permission(user_or_auth, module):
    """检查用户是否有某模块权限"""
    if module not in AVAILABLE_MODULES:
        return True
    perms = _get_effective_permissions(user_or_auth)
    return perms.get('modules', {}).get(module, False)


def _allowed_knowledge_categories(user_or_auth):
    """返回用户允许查看的知识库分类列表；['*'] 表示全部"""
    perms = _get_effective_permissions(user_or_auth)
    return perms.get('knowledgeCategories', [])


def _can_access_knowledge_category(user_or_auth, category):
    """检查用户是否有权访问某知识库分类"""
    cats = _allowed_knowledge_categories(user_or_auth)
    if '*' in cats:
        return True
    if not category:
        # 未分类默认允许，除非显式被排除？这里按允许列表控制
        return '' in cats
    return category in cats


def _validate_agent_for_ai(agent):
    """AI 调用前校验：员工必须存在且未删除，systemPrompt/soulDoc 必须包含身份约束关键字"""
    if not isinstance(agent, dict):
        return False, '员工不存在'
    if agent.get('status') == 'archived' or agent.get('archived'):
        return False, '员工不存在'
    effective_prompt = (agent.get('soulDoc') or agent.get('systemPrompt') or '').strip()
    if not effective_prompt:
        return False, 'AI身份约束缺失，禁止调用AI'
    if '管理员是你的老板' not in effective_prompt:
        return False, 'AI身份约束缺失，禁止调用AI'
    return True, None


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

def _load_agents(include_archived=False):
    """加载 Agent 列表，过滤掉历史遗留的默认员工与已删除(archived)员工，并检测关键字段污染"""
    agents = _read_json(AGENTS_FILE, [])
    if not isinstance(agents, list):
        return []
    cleaned = []
    for a in agents:
        if _is_default_agent(a):
            continue
        # 默认过滤已归档/软删除的员工，避免删除后仍影响列表、权限和新员工创建
        if not include_archived and (a.get('status') == 'archived' or a.get('archived')):
            continue
        # 检测 apiKey 污染
        ak = a.get('apiKey', '')
        if _is_log_polluted(ak):
            print(f'  [LOAD_GUARD] 加载时发现 apiKey 被污染: {a.get("id")} len={len(ak)} 已清空', flush=True)
            a['apiKey'] = ''
        # 检测 systemPrompt / soulDoc / idDoc 污染（日志写入 JSON 时可能连带污染）
        for field in ('systemPrompt', 'soulDoc', 'idDoc', 'toolsDoc', 'userDoc'):
            val = a.get(field, '')
            if _is_log_polluted(val):
                print(f'  [LOAD_GUARD] 加载时发现 {field} 被污染: {a.get("id")} len={len(val)} 已清空', flush=True)
                a[field] = ''
        cleaned.append(a)
    return cleaned


def _get_agent_by_id(agent_id):
    """根据 ID 获取单个 Agent"""
    agents = _load_agents()
    for a in agents:
        if a.get('id') == agent_id:
            return a
    return None

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


import re as _re
_LOG_POLLUTION_PATTERNS = [
    _re.compile(r'\[\d{2}:\d{2}:\d{2}\]\s+"(GET|POST|PUT|DELETE|OPTIONS)\s+[^"]*\s+HTTP/1\.1"\s+\d+'),
    _re.compile(r'\[\d{2}:\d{2}:\d{2}\]\s+\['),
    _re.compile(r'\[PUT agent\]|\[GET agents\]|\[POST agent\]|\[OpenClawSync\]'),
]
_SELF_UPDATE_MARKER_RE = _re.compile(
    r'\[SELF_UPDATE\]\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.*?)\s*\[/SELF_UPDATE\]',
    _re.DOTALL
)

def _is_log_polluted(value):
    """检测值是否被服务器日志污染"""
    if not isinstance(value, str) or len(value) < 30:
        return False
    for pat in _LOG_POLLUTION_PATTERNS:
        if pat.search(value):
            return True
    return False

def _sanitize_api_key(api_key):
    """清理 apiKey：如果被日志污染则返回空字符串"""
    if not isinstance(api_key, str):
        return ''
    if _is_log_polluted(api_key):
        print(f'  [SANITIZE] apiKey 被日志污染，长度={len(api_key)}，已清空', flush=True)
        return ''
    return api_key.strip()


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


def _get_user_emp_ids(user_id):
    """根据 user_id 返回该用户创建的 AI 员工 ID 列表"""
    if not user_id:
        return []
    agents = _load_agents()
    return [a.get('id') for a in agents if a.get('createdBy') == user_id and a.get('id')]


def _get_user_group_ids(user_id):
    """根据 user_id 返回该用户（通过其创建的 AI 员工）所属的项目组 ID 列表"""
    if not user_id:
        return []
    agents = _load_agents()
    my_agent_ids = {a.get('id') for a in agents if a.get('createdBy') == user_id}
    groups = _load_groups()
    result = []
    for g in groups:
        gid = g.get('id')
        if not gid:
            continue
        for m in g.get('members', []):
            mid = m if isinstance(m, str) else m.get('id')
            if mid in my_agent_ids:
                result.append(gid)
                break
    return result


def _get_user_managed_group_ids(user_id):
    """根据 user_id 返回该用户创建/管理的项目组 ID 列表"""
    if not user_id:
        return []
    groups = _load_groups()
    return [g.get('id') for g in groups if g.get('createdBy') == user_id and g.get('id')]


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
            cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
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


def _sync_agent_api_key_to_openclaw(agent):
    """
    将员工的 API Key 同步到 OpenClaw。
    调用: echo <api_key> | openclaw models auth paste-api-key --provider <provider> --profile-id <agent_id>:manual
    API Key 通过 stdin 传递。
    """
    agent_id = agent.get('id')
    api_key = agent.get('apiKey', '').strip()
    # 优先 aiProvider（前端实际选择的 AI 供应商），其次 apiProvider
    provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
    if not api_key or not provider:
        return False, '缺少 apiKey 或 provider'
    if not os.path.isfile(OPENCLAW_CLI):
        return False, f'OpenClaw CLI 未找到: {OPENCLAW_CLI}'

    args = ['models', 'auth', 'paste-api-key', '--provider', provider, '--profile-id', f'{agent_id}:manual']
    success, stdout, stderr, rc = _run_openclaw(args, input_data=api_key)
    if success and rc == 0:
        print(f'  [OpenClawSync] API Key 已同步: {agent_id} provider={provider}', flush=True)
        return True, stdout
    else:
        err = stderr or stdout or f'returncode={rc}'
        print(f'  [OpenClawSync] API Key 同步失败: {agent_id} provider={provider} err={err}', flush=True)
        return False, err


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
        self.group_ids = []      # 所属项目组 ID 列表
        self.managed_group_ids = []  # 管理的项目组 ID 列表

    @property
    def is_authenticated(self):
        return self.user_info is not None

    @property
    def is_admin(self):
        return self.user_info and self.user_info.get('role') == 'admin'

    @property
    def user_id(self):
        return self.user_info.get('userId') if self.user_info else None

    @property
    def role(self):
        return self.user_info.get('role') if self.user_info else None

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
                # 填充 group_ids 和 managed_group_ids（通过用户创建的 AI 员工匹配群组成员）
                if self.user_info:
                    uid = self.user_info.get('userId')
                    self.group_ids = _get_user_group_ids(uid)
                    self.managed_group_ids = _get_user_managed_group_ids(uid)
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


# ═══════════════════════════════════════════════════
# Embedding / RAG 向量检索（纯 Python 标准库实现）
# ═══════════════════════════════════════════════════

def get_embedding(text, api_key, provider='openai', model=None, base_url=None):
    """调用 Embedding API 获取向量，纯 urllib 实现"""
    if not text or not text.strip():
        return None
    cfg = EMBEDDING_PROVIDERS.get(provider, EMBEDDING_PROVIDERS['openai'])
    target_url = base_url or cfg['url']
    target_model = model or cfg['model']
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
    }
    body = json.dumps({
        'input': text[:8000],  # 限制长度，避免超长
        'model': target_model,
        'encoding_format': 'float',
    }).encode('utf-8')
    req = urllib.request.Request(target_url, data=body, headers=headers, method='POST')
    # 创建 SSL context，忽略证书验证（避免部分环境的证书问题）
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
        data = json.loads(resp.read().decode('utf-8'))
        if data.get('data') and len(data['data']) > 0:
            emb = data['data'][0].get('embedding')
            if emb and isinstance(emb, list):
                return emb
    return None


def cosine_similarity(a, b):
    """纯 Python 计算余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def _get_embedding_cache_path(entity_type, entity_id):
    return os.path.join(EMBEDDING_DIR, f'{entity_type}_{entity_id}.json')


def load_embedding(entity_type, entity_id):
    """加载缓存的 embedding"""
    path = _get_embedding_cache_path(entity_type, entity_id)
    if os.path.exists(path):
        data = _read_json(path, None)
        if data and 'embedding' in data:
            return data['embedding']
    return None


def save_embedding(entity_type, entity_id, embedding):
    """保存 embedding 到缓存"""
    os.makedirs(EMBEDDING_DIR, exist_ok=True)
    path = _get_embedding_cache_path(entity_type, entity_id)
    _write_json(path, {
        'embedding': embedding,
        'updatedAt': int(time.time() * 1000),
    })


def delete_embedding_cache(entity_type, entity_id):
    """删除 embedding 缓存"""
    path = _get_embedding_cache_path(entity_type, entity_id)
    if os.path.exists(path):
        os.remove(path)


def build_entity_text(entity_type, entity):
    """构建用于 embedding 的文本"""
    if entity_type == 'doc':
        parts = [entity.get('name', '')]
        if entity.get('category'):
            parts.append(f"分类: {entity['category']}")
        if entity.get('tags'):
            parts.append(f"标签: {', '.join(entity['tags'])}")
        parts.append(entity.get('content', ''))
        return '\n'.join(parts)
    elif entity_type == 'product':
        parts = [entity.get('name', '')]
        if entity.get('category'):
            parts.append(f"分类: {entity['category']}")
        if entity.get('tags'):
            parts.append(f"标签: {', '.join(entity['tags'])}")
        if entity.get('description'):
            parts.append(entity['description'])
        if entity.get('selling_points'):
            parts.append(f"卖点: {entity['selling_points']}")
        if entity.get('sku'):
            parts.append(f"SKU: {entity['sku']}")
        return '\n'.join(parts)
    return ''


def ensure_embedding(entity_type, entity, api_key, provider='openai', model=None, base_url=None):
    """确保 entity 的 embedding 已生成，没有则实时生成"""
    entity_id = entity.get('id')
    if not entity_id:
        return None
    emb = load_embedding(entity_type, entity_id)
    if emb:
        return emb
    text = build_entity_text(entity_type, entity)
    if not text.strip():
        return None
    try:
        emb = get_embedding(text, api_key, provider, model=model, base_url=base_url)
        if emb:
            save_embedding(entity_type, entity_id, emb)
        return emb
    except Exception as e:
        print(f'  [Embedding] {entity_type} {entity_id} 生成失败: {e}', flush=True)
        return None


def build_all_embeddings(api_key=None, provider='openai', model=None, base_url=None):
    """批量构建所有知识库文档和产品的 embedding；使用全局 embedding 配置，不再依赖传入参数"""
    # 使用全局 embedding 配置
    emb_cfg = get_embedding_config()
    api_key = emb_cfg['apiKey']
    provider = emb_cfg['provider']
    model = emb_cfg['model']
    base_url = emb_cfg['baseUrl']
    if not api_key:
        print(f'  [Embedding] 全局未配置 API key，跳过批量构建', flush=True)
        return

    os.makedirs(EMBEDDING_DIR, exist_ok=True)
    # 知识库文档（从 SQLite 读取，更新 embedding 列）
    conn = _db_conn()
    try:
        rows = conn.execute('SELECT * FROM knowledge').fetchall()
        for row in rows:
            doc = _knowledge_row_to_dict(row)
            emb = ensure_embedding('doc', doc, api_key, provider, model=model, base_url=base_url)
            if emb:
                conn.execute('UPDATE knowledge SET embedding = ? WHERE id = ?',
                             (json.dumps(emb), row['id']))
        conn.commit()
    finally:
        conn.close()
    # 产品（从 SQLite 读取）
    conn = _db_conn()
    try:
        rows = conn.execute('SELECT * FROM products WHERE status != ?', ('archived',)).fetchall()
        products = [_product_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    for product in products:
        ensure_embedding('product', product, api_key, provider, model=model, base_url=base_url)
    print(f'  [Embedding] 批量构建完成', flush=True)


def rag_retrieve(query, api_key, provider='openai', top_k_docs=3, top_k_products=3, model=None, base_url=None,
                 requester_id=None, is_admin=False, team_ids=None, group_ids=None):
    """RAG 检索：基于向量相似度返回相关知识库文档和产品（支持 group 隔离）"""
    if not query or not query.strip() or not api_key:
        return {'docs': [], 'products': [], 'context': ''}

    # 1. 获取 query 的 embedding
    query_emb = get_embedding(query, api_key, provider, model=model, base_url=base_url)
    if not query_emb:
        return {'docs': [], 'products': [], 'context': ''}

    results = {'docs': [], 'products': [], 'context': ''}

    # 2. 知识库文档检索（从 SQLite 读取带 embedding 的知识，按 scope 做权限过滤）
    conn = _db_conn()
    doc_scores = []
    try:
        sql = '''
            SELECT id, title, content, category, scope, emp_id, team_id, group_ids, embedding, created_at, updated_at
            FROM knowledge
            WHERE embedding IS NOT NULL AND status = 'ok'
        '''
        params = []
        if requester_id is not None and not is_admin:
            clauses = [
                "scope IS NULL OR scope = 'global'",
                "(scope = 'personal' AND emp_id = ?)"
            ]
            params.append(requester_id)
            if team_ids:
                clauses.append("(scope = 'team' AND team_id IN ({}))".format(', '.join('?' for _ in team_ids)))
                params.extend(team_ids)
            if group_ids:
                clauses.append("(scope = 'group' AND EXISTS (SELECT 1 FROM json_each(group_ids) WHERE value IN ({})))".format(', '.join('?' for _ in group_ids)))
                params.extend(group_ids)
            sql += ' AND (' + ' OR '.join(clauses) + ')'
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    for row in rows:
        try:
            emb = json.loads(row['embedding'])
            score = cosine_similarity(query_emb, emb)
            if score > 0.0:
                doc_scores.append((score, _knowledge_row_to_dict(row)))
        except Exception:
            continue
    doc_scores.sort(key=lambda x: x[0], reverse=True)
    results['docs'] = [d for _, d in doc_scores[:top_k_docs]]

    # 3. 产品库检索（从 SQLite 读取）
    conn = _db_conn()
    try:
        rows = conn.execute('SELECT * FROM products WHERE status != ?', ('archived',)).fetchall()
        products = [_product_row_to_dict(r) for r in rows]
    finally:
        conn.close()
    product_scores = []
    for product in products:
        emb = load_embedding('product', product.get('id', ''))
        if emb:
            score = cosine_similarity(query_emb, emb)
            if score > 0.0:
                product_scores.append((score, product))
    product_scores.sort(key=lambda x: x[0], reverse=True)
    results['products'] = [p for _, p in product_scores[:top_k_products]]

    # 4. 格式化上下文
    results['context'] = format_rag_context(results['docs'], results['products'])
    return results


def format_rag_context(docs, products):
    """将检索结果格式化为注入 system prompt 的文本"""
    lines = []
    if docs:
        lines.append('【知识库文档】')
        for d in docs:
            content = (d.get('content') or '')[:1200]
            lines.append(f"━━━ {d.get('icon', '📄')} {d.get('name', '未命名')} ━━━")
            lines.append(content)
            if len(d.get('content', '')) > 1200:
                lines.append('...（内容已截取）')
            lines.append('')
    if products:
        lines.append('【产品信息】')
        for p in products:
            lines.append(f"━━━ 📦 {p.get('name', '未命名')} ━━━")
            lines.append(f"价格: ¥{p.get('price', 0)} | 分类: {p.get('category', '未分类')} | SKU: {p.get('sku', 'N/A')}")
            if p.get('description'):
                lines.append(f"描述: {p.get('description')[:400]}")
            if p.get('selling_points'):
                lines.append(f"卖点: {p.get('selling_points')[:300]}")
            if p.get('tags'):
                lines.append(f"标签: {', '.join(p.get('tags', []))}")
            if p.get('commission_rate'):
                lines.append(f"佣金: {p.get('commission_rate')}%")
            lines.append('')
    return '\n'.join(lines)


# ═══════════════════════════════════════════════════
# SQLite 数据库初始化与知识库 ORM
# ═══════════════════════════════════════════════════

def _db_conn():
    """获取 SQLite 数据库连接（线程安全）"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_brain_tables(conn):
    """FIXME: 大脑知识中枢数据层初始化（memory/topics/knowledge_new/relations）"""
    # 记忆元数据索引表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            id TEXT PRIMARY KEY,
            emp_id TEXT NOT NULL,
            value TEXT,
            pool TEXT DEFAULT 'daily',
            created_at INTEGER,
            is_filler BOOLEAN DEFAULT 0,
            is_duplicate BOOLEAN DEFAULT 0,
            source_mem_id TEXT,
            cleaned_at INTEGER DEFAULT 0,
            topic_ids TEXT DEFAULT '[]',
            inducted_at INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active'
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_emp_cleaned ON memory(emp_id, cleaned_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_emp_filler ON memory(emp_id, is_filler)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_source ON memory(source_mem_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_topic ON memory(topic_ids)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_status ON memory(status)')

    # 主题表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS memory_topics (
            id TEXT PRIMARY KEY,
            title TEXT,
            key_words TEXT DEFAULT '[]',
            emp_ids TEXT DEFAULT '[]',
            mem_count INTEGER DEFAULT 0,
            first_seen_at INTEGER,
            last_active_at INTEGER,
            status TEXT DEFAULT 'active',
            pending_induct BOOLEAN DEFAULT 0,
            center_embedding BLOB
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_topics_status ON memory_topics(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_topics_pending ON memory_topics(pending_induct, status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_topics_last_active ON memory_topics(last_active_at)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_topics_emp ON memory_topics(emp_ids)')

    # 新版知识库表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_base_new (
            id TEXT PRIMARY KEY,
            title TEXT,
            content TEXT,
            key_points TEXT DEFAULT '[]',
            evidence_mem_ids TEXT DEFAULT '[]',
            confidence REAL DEFAULT 0.5,
            topic_ids TEXT DEFAULT '[]',
            created_at INTEGER,
            updated_at INTEGER,
            status TEXT DEFAULT 'active'
        )
    ''')
    # 向后兼容：新增 scope / team_id / group_ids 字段，与 knowledge 表同步（必须在 CREATE INDEX 之前）
    _add_column_if_not_exists(conn, 'knowledge_base_new', 'scope', "TEXT DEFAULT 'global'")
    _add_column_if_not_exists(conn, 'knowledge_base_new', 'team_id', "TEXT DEFAULT ''")
    _add_column_if_not_exists(conn, 'knowledge_base_new', 'group_ids', "TEXT DEFAULT '[]'")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kb_new_status ON knowledge_base_new(status)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kb_new_topics ON knowledge_base_new(topic_ids)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kb_new_updated ON knowledge_base_new(updated_at)')

    # 知识关系表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS knowledge_relations (
            id TEXT PRIMARY KEY,
            source_knowledge_id TEXT,
            target_knowledge_id TEXT,
            relation_type TEXT,
            confidence REAL,
            created_at INTEGER
        )
    ''')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kr_source ON knowledge_relations(source_knowledge_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kr_target ON knowledge_relations(target_knowledge_id)')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_kr_type ON knowledge_relations(relation_type)')


def _add_column_if_not_exists(conn, table, column, def_type):
    """如果表不存在某列，则添加该列（用于向后兼容升级）"""
    try:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {def_type}')
    except sqlite3.OperationalError as e:
        if 'duplicate column name' not in str(e).lower():
            raise


def init_db():
    """初始化数据库，创建 knowledge/products 表（启动时调用）"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = _db_conn()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                category TEXT DEFAULT '',
                embedding TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_category ON knowledge(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_created ON knowledge(created_at)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                main_image TEXT DEFAULT '',
                price REAL DEFAULT 0,
                price_range TEXT DEFAULT '',
                brand TEXT DEFAULT '',
                brand_id TEXT DEFAULT '',
                category TEXT DEFAULT '',
                sku_specs TEXT DEFAULT '{}',
                stock INTEGER DEFAULT 0,
                status TEXT DEFAULT 'active',
                monthly_sales INTEGER DEFAULT 0,
                monthly_gmv REAL DEFAULT 0,
                commission_rates TEXT DEFAULT '{}',
                commission_amount REAL DEFAULT 0,
                conversion_rate REAL DEFAULT 0,
                avg_order_value REAL DEFAULT 0,
                influencer_count INTEGER DEFAULT 0,
                talent_count INTEGER DEFAULT 0,
                video_count INTEGER DEFAULT 0,
                live_count INTEGER DEFAULT 0,
                channel_distribution TEXT DEFAULT '{}',
                influencers TEXT DEFAULT '[]',
                audience TEXT DEFAULT '{}',
                ai_analysis TEXT DEFAULT '{}',
                videos TEXT DEFAULT '[]',
                tags TEXT DEFAULT '[]',
                selling_points TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        # 兼容旧表：补充 products 可能缺失的新列（必须在 CREATE INDEX 之前）
        for _prod_col, _prod_dtype in [
            ('tags', "TEXT DEFAULT '[]'"),
            ('selling_points', "TEXT DEFAULT ''"),
            ('brand_id', "TEXT DEFAULT ''"),
            ('talent_count', 'INTEGER DEFAULT 0'),
            ('created_by', "TEXT DEFAULT ''"),
        ]:
            _add_column_if_not_exists(conn, 'products', _prod_col, _prod_dtype)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_products_brand ON products(brand)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_products_brand_id ON products(brand_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)')

        # 品牌库
        conn.execute('''
            CREATE TABLE IF NOT EXISTS brands (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                logo TEXT DEFAULT '',
                shop_score REAL DEFAULT 0,
                shop_type TEXT DEFAULT '',
                main_category TEXT DEFAULT '',
                total_products INTEGER DEFAULT 0,
                total_talents INTEGER DEFAULT 0,
                avg_commission REAL DEFAULT 0,
                group_id TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        # 兼容旧表：确保 brands 所有列都存在
        for _brand_col, _brand_dtype in [
            ('logo', "TEXT DEFAULT ''"),
            ('shop_score', 'REAL DEFAULT 0'),
            ('shop_type', "TEXT DEFAULT ''"),
            ('main_category', "TEXT DEFAULT ''"),
            ('total_products', 'INTEGER DEFAULT 0'),
            ('total_talents', 'INTEGER DEFAULT 0'),
            ('avg_commission', 'REAL DEFAULT 0'),
            ('group_id', "TEXT DEFAULT ''"),
            ('status', "TEXT DEFAULT 'active'"),
            ('created_at', 'INTEGER'),
            ('updated_at', 'INTEGER'),
        ]:
            _add_column_if_not_exists(conn, 'brands', _brand_col, _brand_dtype)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_brands_status ON brands(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_brands_group ON brands(group_id)')

        # 达人库
        conn.execute('''
            CREATE TABLE IF NOT EXISTS talents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                avatar TEXT DEFAULT '',
                douyin_id TEXT DEFAULT '',
                real_name TEXT DEFAULT '',
                wechat TEXT DEFAULT '',
                phone TEXT DEFAULT '',
                email TEXT DEFAULT '',
                city TEXT DEFAULT '',
                level TEXT DEFAULT '',
                followers INTEGER DEFAULT 0,
                talent_type TEXT DEFAULT '',
                location TEXT DEFAULT '',
                agency TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                bio TEXT DEFAULT '',
                contact TEXT DEFAULT '',
                contact_name TEXT DEFAULT '',
                contact_phone TEXT DEFAULT '',
                contact_wechat TEXT DEFAULT '',
                contact_email TEXT DEFAULT '',
                cooperation_status TEXT DEFAULT 'available',
                follow_up_by TEXT DEFAULT '',
                next_follow_up_at INTEGER DEFAULT 0,
                follow_up_note TEXT DEFAULT '',
                commission_requirement REAL DEFAULT 0,
                fulfillment_score REAL DEFAULT 0,
                rating_score REAL DEFAULT 0,
                total_gmv REAL DEFAULT 0,
                total_products INTEGER DEFAULT 0,
                product_count INTEGER DEFAULT 0,
                total_shops INTEGER DEFAULT 0,
                average_price REAL DEFAULT 0,
                live_ratio REAL DEFAULT 0,
                video_ratio REAL DEFAULT 0,
                avg_live_gmv REAL DEFAULT 0,
                live_gpm REAL DEFAULT 0,
                video_gpm REAL DEFAULT 0,
                fan_gender TEXT DEFAULT '{}',
                fan_age TEXT DEFAULT '{}',
                fan_region TEXT DEFAULT '{}',
                fan_crowd TEXT DEFAULT '',
                fan_price_range TEXT DEFAULT '',
                fan_category TEXT DEFAULT '',
                category TEXT DEFAULT '',
                content_style TEXT DEFAULT '',
                fans_profile TEXT DEFAULT '{}',
                ai_tags TEXT DEFAULT '[]',
                ai_rating TEXT DEFAULT '',
                ai_summary TEXT DEFAULT '',
                ai_analysis TEXT DEFAULT '',
                ai_reason TEXT DEFAULT '',
                risk_rating TEXT DEFAULT '',
                group_id TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                created_by TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        # 兼容旧表：补充 talents 可能缺失的新列
        for _talent_col, _talent_dtype in [
            ('avatar', "TEXT DEFAULT ''"), ('douyin_id', "TEXT DEFAULT ''"), ('real_name', "TEXT DEFAULT ''"),
            ('wechat', "TEXT DEFAULT ''"), ('phone', "TEXT DEFAULT ''"), ('email', "TEXT DEFAULT ''"),
            ('city', "TEXT DEFAULT ''"), ('level', "TEXT DEFAULT ''"),
            ('followers', 'INTEGER DEFAULT 0'), ('talent_type', "TEXT DEFAULT ''"), ('location', "TEXT DEFAULT ''"),
            ('agency', "TEXT DEFAULT ''"), ('tags', "TEXT DEFAULT '[]'"), ('bio', "TEXT DEFAULT ''"),
            ('contact', "TEXT DEFAULT ''"),
            ('contact_name', "TEXT DEFAULT ''"), ('contact_phone', "TEXT DEFAULT ''"), ('contact_wechat', "TEXT DEFAULT ''"), ('contact_email', "TEXT DEFAULT ''"),
            ('cooperation_status', "TEXT DEFAULT 'available'"),
            ('follow_up_by', "TEXT DEFAULT ''"), ('next_follow_up_at', 'INTEGER DEFAULT 0'), ('follow_up_note', "TEXT DEFAULT ''"),
            ('commission_requirement', 'REAL DEFAULT 0'), ('fulfillment_score', 'REAL DEFAULT 0'),
            ('rating_score', 'REAL DEFAULT 0'), ('total_gmv', 'REAL DEFAULT 0'), ('total_products', 'INTEGER DEFAULT 0'),
            ('product_count', 'INTEGER DEFAULT 0'), ('total_shops', 'INTEGER DEFAULT 0'), ('average_price', 'REAL DEFAULT 0'),
            ('live_ratio', 'REAL DEFAULT 0'), ('video_ratio', 'REAL DEFAULT 0'),
            ('avg_live_gmv', 'REAL DEFAULT 0'), ('live_gpm', 'REAL DEFAULT 0'), ('video_gpm', 'REAL DEFAULT 0'),
            ('fan_gender', "TEXT DEFAULT '{}'"), ('fan_age', "TEXT DEFAULT '{}'"), ('fan_region', "TEXT DEFAULT '{}'"),
            ('fan_crowd', "TEXT DEFAULT ''"), ('fan_price_range', "TEXT DEFAULT ''"), ('fan_category', "TEXT DEFAULT ''"),
            ('category', "TEXT DEFAULT ''"), ('content_style', "TEXT DEFAULT ''"), ('fans_profile', "TEXT DEFAULT '{}'"),
            ('ai_tags', "TEXT DEFAULT '[]'"), ('ai_rating', "TEXT DEFAULT ''"), ('ai_summary', "TEXT DEFAULT ''"),
            ('ai_analysis', "TEXT DEFAULT ''"), ('ai_reason', "TEXT DEFAULT ''"), ('risk_rating', "TEXT DEFAULT ''"),
            ('group_id', "TEXT DEFAULT ''"), ('status', "TEXT DEFAULT 'active'"),
            ('created_by', "TEXT DEFAULT ''"),
        ]:
            _add_column_if_not_exists(conn, 'talents', _talent_col, _talent_dtype)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_status ON talents(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_cooperation ON talents(cooperation_status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_created_by ON talents(created_by)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_category ON talents(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_fan_category ON talents(fan_category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_group ON talents(group_id)')

        # 商品-达人匹配关系
        conn.execute('''
            CREATE TABLE IF NOT EXISTS product_talent_match (
                id TEXT PRIMARY KEY,
                product_id TEXT NOT NULL,
                talent_id TEXT NOT NULL,
                match_score REAL DEFAULT 0,
                match_reason TEXT DEFAULT '',
                sales_volume INTEGER DEFAULT 0,
                conversion_rate REAL DEFAULT 0,
                is_ai_recommended INTEGER DEFAULT 0,
                created_at INTEGER,
                updated_at INTEGER,
                UNIQUE(product_id, talent_id)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ptm_product ON product_talent_match(product_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ptm_talent ON product_talent_match(talent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ptm_score ON product_talent_match(match_score)')

        # 达人 CRM 跟进记录
        conn.execute('''
            CREATE TABLE IF NOT EXISTS talent_follow_ups (
                id TEXT PRIMARY KEY,
                talent_id TEXT NOT NULL,
                follow_up_by TEXT DEFAULT '',
                follow_up_at INTEGER DEFAULT 0,
                next_follow_up_at INTEGER DEFAULT 0,
                content TEXT DEFAULT '',
                result TEXT DEFAULT '',
                status TEXT DEFAULT 'completed',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tfu_talent_id ON talent_follow_ups(talent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tfu_follow_up_at ON talent_follow_ups(follow_up_at)')

        # FIXME: 新增记忆三级沉淀表（二级归纳、三级知识库），保持原有 knowledge/products 表不变
        conn.execute('''
            CREATE TABLE IF NOT EXISTS memory_summary (
                id TEXT PRIMARY KEY,
                emp_id TEXT NOT NULL,
                summary_type TEXT NOT NULL,
                title TEXT NOT NULL,
                date TEXT,
                project_name TEXT,
                status TEXT DEFAULT 'pending',
                key_points TEXT DEFAULT '[]',
                decisions TEXT DEFAULT '[]',
                pending TEXT DEFAULT '[]',
                action_items TEXT DEFAULT '[]',
                related_mem_ids TEXT DEFAULT '[]',
                source_mem_ids TEXT DEFAULT '[]',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_summary_emp ON memory_summary(emp_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_summary_type ON memory_summary(summary_type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_summary_date ON memory_summary(date)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_memory_summary_project ON memory_summary(project_name)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id TEXT PRIMARY KEY,
                emp_id TEXT,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT DEFAULT 'manual',
                tags TEXT DEFAULT '[]',
                evidence_count INTEGER DEFAULT 1,
                related_mem_ids TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_emp ON knowledge_base(emp_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_status ON knowledge_base(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_title ON knowledge_base(title)')

        # FIXME: 大脑知识中枢新增表（保留旧表，不删数据）
        _init_brain_tables(conn)

        conn.commit()

        # 旧 JSON 数据迁移（幂等）
        _migrate_json_products_to_sqlite()

        # 空表时写入 COOLCHAP 示例数据
        _seed_coolchap_data(conn)
    finally:
        conn.close()


def _knowledge_row_to_dict(row):
    """将 sqlite3.Row 转为前端兼容 dict（保留 name/icon/linkedEmployees 兼容字段）"""
    if not row:
        return None
    return {
        'id': row['id'],
        'title': row['title'],
        'name': row['title'],  # 兼容旧前端
        'content': row['content'],
        'category': row['category'] or '',
        'embedding': json.loads(row['embedding']) if row['embedding'] else None,
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'icon': '📄',  # 兼容旧前端
        'linkedEmployees': [],  # 兼容旧前端（SQLite 版不再使用）
    }


# ─── 商品库 SQLite 辅助函数 ─────────────────────────────

_PRODUCT_COLUMNS = [
    'id', 'name', 'subtitle', 'main_image', 'price', 'price_range', 'brand', 'brand_id',
    'category', 'sku_specs', 'stock', 'status', 'monthly_sales', 'monthly_gmv',
    'commission_rates', 'commission_amount', 'conversion_rate', 'avg_order_value',
    'influencer_count', 'talent_count', 'video_count', 'live_count', 'channel_distribution',
    'influencers', 'audience', 'ai_analysis', 'videos', 'tags', 'selling_points',
    'created_by', 'created_at', 'updated_at'
]


def _product_row_to_dict(row):
    """将 products 表的 sqlite3.Row 转为前端兼容 dict"""
    if not row:
        return None

    def _json_col(col, default=None):
        val = row[col]
        if val is None:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default

    product = {
        'id': row['id'],
        'name': row['name'] or '',
        'subtitle': row['subtitle'] or '',
        'main_image': row['main_image'] or '',
        'price': row['price'] if row['price'] is not None else 0,
        'price_range': row['price_range'] or '',
        'brand': row['brand'] or '',
        'brand_id': row['brand_id'] or '',
        'category': row['category'] or '',
        'sku_specs': _json_col('sku_specs', {}),
        'stock': row['stock'] if row['stock'] is not None else 0,
        'status': row['status'] or 'active',
        'monthly_sales': row['monthly_sales'] if row['monthly_sales'] is not None else 0,
        'monthly_gmv': row['monthly_gmv'] if row['monthly_gmv'] is not None else 0,
        'commission_rates': _json_col('commission_rates', {}),
        'commission_amount': row['commission_amount'] if row['commission_amount'] is not None else 0,
        'conversion_rate': row['conversion_rate'] if row['conversion_rate'] is not None else 0,
        'avg_order_value': row['avg_order_value'] if row['avg_order_value'] is not None else 0,
        'influencer_count': row['influencer_count'] if row['influencer_count'] is not None else 0,
        'talent_count': row['talent_count'] if row['talent_count'] is not None else 0,
        'video_count': row['video_count'] if row['video_count'] is not None else 0,
        'live_count': row['live_count'] if row['live_count'] is not None else 0,
        'channel_distribution': _json_col('channel_distribution', {}),
        'influencers': _json_col('influencers', []),
        'audience': _json_col('audience', {}),
        'ai_analysis': _json_col('ai_analysis', {}),
        'videos': _json_col('videos', []),
        'tags': _json_col('tags', []),
        'selling_points': row['selling_points'] or '',
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }

    # 兼容旧代码/匹配逻辑/RAG 格式化的字段
    product['description'] = product['subtitle']
    if not isinstance(product['tags'], list):
        product['tags'] = []
    product['sku'] = ''
    if isinstance(product['sku_specs'], dict):
        product['attributes'] = product['sku_specs']
        product['sku'] = product['sku_specs'].get('SKU') or product['sku_specs'].get('sku') or ''
    else:
        product['attributes'] = {}
    product['images'] = [product['main_image']] if product['main_image'] else []
    product['priceRange'] = product['price_range']
    rates = product['commission_rates']
    if isinstance(rates, dict) and rates:
        product['commission_rate'] = max(
            (v for v in rates.values() if isinstance(v, (int, float))),
            default=0
        )
    else:
        product['commission_rate'] = 0
    return product


def _dict_to_product_row(p):
    """将请求体/旧 dict 转换为 products 表行数据（含 JSON 序列化）"""
    def _get(*keys, default=None):
        for k in keys:
            if k in p and p[k] is not None:
                return p[k]
        return default

    def _dump(val):
        if val is None:
            return '{}'
        return json.dumps(val, ensure_ascii=False)

    sku_specs = _get('sku_specs', 'skuSpecs', 'attributes')
    if sku_specs is None:
        sku_val = _get('sku')
        if sku_val:
            sku_specs = {'SKU': sku_val}
    if isinstance(sku_specs, str):
        sku_specs = {'SKU': sku_specs}
    if sku_specs is None:
        sku_specs = {}

    commission_rates = _get('commission_rates', 'commissionRates', 'commission_rate', 'commissionRate')
    if isinstance(commission_rates, (int, float)):
        commission_rates = {'default': commission_rates}
    if commission_rates is None:
        commission_rates = {}

    main_image = _get('main_image', 'mainImage')
    if not main_image:
        images = _get('images', default=[])
        if isinstance(images, list) and images:
            main_image = images[0]

    return {
        'id': p.get('id'),
        'name': p.get('name'),
        'subtitle': _get('subtitle', 'description') or '',
        'main_image': main_image or '',
        'price': float(_get('price', default=0) or 0),
        'price_range': _get('price_range', 'priceRange') or '',
        'brand': _get('brand', default='') or '',
        'brand_id': _get('brand_id', 'brandId', default='') or '',
        'category': _get('category', default='') or '',
        'sku_specs': _dump(sku_specs),
        'stock': int(_get('stock', default=0) or 0),
        'status': _get('status', default='active') or 'active',
        'monthly_sales': int(_get('monthly_sales', 'monthlySales', default=0) or 0),
        'monthly_gmv': float(_get('monthly_gmv', 'monthlyGmv', 'monthlyGMV', default=0) or 0),
        'commission_rates': _dump(commission_rates),
        'commission_amount': float(_get('commission_amount', 'commissionAmount', default=0) or 0),
        'conversion_rate': float(_get('conversion_rate', 'conversionRate', default=0) or 0),
        'avg_order_value': float(_get('avg_order_value', 'avgOrderValue', default=0) or 0),
        'influencer_count': int(_get('influencer_count', 'influencerCount', default=0) or 0),
        'talent_count': int(_get('talent_count', 'talentCount', default=0) or 0),
        'video_count': int(_get('video_count', 'videoCount', default=0) or 0),
        'live_count': int(_get('live_count', 'liveCount', default=0) or 0),
        'channel_distribution': _dump(_get('channel_distribution', 'channelDistribution', default={})),
        'influencers': _dump(_get('influencers', 'matched_influencers', 'matchedInfluencers', default=[])),
        'audience': _dump(_get('audience', default={})),
        'ai_analysis': _dump(_get('ai_analysis', 'aiAnalysis', default={})),
        'videos': _dump(_get('videos', 'hot_videos', 'hotVideos', default=[])),
        'tags': _dump(_get('tags', default=[])),
        'selling_points': _get('selling_points', 'sellingPoints', default='') or '',
        'created_by': _get('created_by', 'createdBy', default='') or '',
        'created_at': _get('created_at', 'createdAt'),
        'updated_at': _get('updated_at', 'updatedAt'),
    }


# ─── 品牌库 / 达人库 SQLite 辅助函数 ─────────────────────────────

_BRAND_COLUMNS = [
    'id', 'name', 'logo', 'shop_score', 'shop_type', 'main_category',
    'total_products', 'total_talents', 'avg_commission', 'group_id', 'status',
    'created_at', 'updated_at'
]

_TALENT_COLUMNS = [
    'id', 'name', 'avatar', 'douyin_id', 'real_name', 'wechat', 'phone', 'email',
    'city', 'level', 'followers', 'talent_type', 'location', 'agency', 'tags',
    'bio', 'contact', 'contact_name', 'contact_phone', 'contact_wechat',
    'contact_email', 'cooperation_status', 'follow_up_by', 'next_follow_up_at',
    'follow_up_note', 'commission_requirement', 'fulfillment_score', 'rating_score',
    'total_gmv', 'total_products', 'product_count', 'total_shops', 'average_price',
    'live_ratio', 'video_ratio', 'avg_live_gmv', 'live_gpm', 'video_gpm',
    'fan_gender', 'fan_age', 'fan_region', 'fan_crowd', 'fan_price_range',
    'fan_category', 'category', 'content_style', 'fans_profile', 'ai_tags', 'ai_rating', 'ai_summary',
    'ai_analysis', 'ai_reason', 'risk_rating', 'group_id', 'status', 'created_by', 'created_at', 'updated_at'
]

_FOLLOW_UP_COLUMNS = [
    'id', 'talent_id', 'follow_up_by', 'follow_up_at', 'next_follow_up_at',
    'content', 'result', 'status', 'created_at', 'updated_at'
]

_PTM_COLUMNS = [
    'id', 'product_id', 'talent_id', 'match_score', 'match_reason', 'sales_volume',
    'conversion_rate', 'is_ai_recommended', 'created_at', 'updated_at'
]


def _brand_row_to_dict(row):
    if not row:
        return None
    d = dict(row)
    return {
        'id': d.get('id') or '',
        'name': d.get('name') or '',
        'logo': d.get('logo') or '',
        'shop_score': d.get('shop_score') if d.get('shop_score') is not None else 0,
        'shop_type': d.get('shop_type') or '',
        'main_category': d.get('main_category') or '',
        'total_products': d.get('total_products') if d.get('total_products') is not None else 0,
        'total_talents': d.get('total_talents') if d.get('total_talents') is not None else 0,
        'avg_commission': d.get('avg_commission') if d.get('avg_commission') is not None else 0,
        'group_id': d.get('group_id') or '',
        'status': d.get('status') or 'active',
        'created_at': d.get('created_at') or 0,
        'updated_at': d.get('updated_at') or 0,
        'createdAt': d.get('created_at') or 0,
        'updatedAt': d.get('updated_at') or 0,
    }


def _talent_row_to_dict(row):
    if not row:
        return None
    def _json_col(col, default=None):
        val = row[col]
        if val is None:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default
    return {
        'id': row['id'],
        'name': row['name'] or '',
        'avatar': row['avatar'] or '',
        'douyin_id': row['douyin_id'] or '',
        'real_name': row['real_name'] or '',
        'wechat': row['wechat'] or row['contact_wechat'] or '',
        'phone': row['phone'] or row['contact_phone'] or '',
        'email': row['email'] or row['contact_email'] or '',
        'city': row['city'] or '',
        'level': row['level'] or '',
        'followers': row['followers'] if row['followers'] is not None else 0,
        'talent_type': row['talent_type'] or '',
        'location': row['location'] or '',
        'agency': row['agency'] or '',
        'tags': _json_col('tags', []),
        'bio': row['bio'] or '',
        'contact': row['contact'] or '',
        'contact_name': row['contact_name'] or '',
        'contact_phone': row['contact_phone'] or '',
        'contact_wechat': row['contact_wechat'] or '',
        'contact_email': row['contact_email'] or '',
        'cooperation_status': row['cooperation_status'] or 'available',
        'follow_up_by': row['follow_up_by'] or '',
        'next_follow_up_at': row['next_follow_up_at'] if row['next_follow_up_at'] is not None else 0,
        'follow_up_note': row['follow_up_note'] or '',
        'commission_requirement': row['commission_requirement'] if row['commission_requirement'] is not None else 0,
        'fulfillment_score': row['fulfillment_score'] if row['fulfillment_score'] is not None else 0,
        'rating_score': row['rating_score'] if row['rating_score'] is not None else 0,
        'total_gmv': row['total_gmv'] if row['total_gmv'] is not None else 0,
        'total_products': row['total_products'] if row['total_products'] is not None else 0,
        'product_count': row['product_count'] if row['product_count'] is not None else (row['total_products'] if row['total_products'] is not None else 0),
        'total_shops': row['total_shops'] if row['total_shops'] is not None else 0,
        'average_price': row['average_price'] if row['average_price'] is not None else 0,
        'live_ratio': row['live_ratio'] if row['live_ratio'] is not None else 0,
        'video_ratio': row['video_ratio'] if row['video_ratio'] is not None else 0,
        'avg_live_gmv': row['avg_live_gmv'] if row['avg_live_gmv'] is not None else 0,
        'live_gpm': row['live_gpm'] if row['live_gpm'] is not None else 0,
        'video_gpm': row['video_gpm'] if row['video_gpm'] is not None else 0,
        'fan_gender': _json_col('fan_gender', {}),
        'fan_age': _json_col('fan_age', {}),
        'fan_region': _json_col('fan_region', {}),
        'fan_crowd': row['fan_crowd'] or '',
        'fan_price_range': row['fan_price_range'] or '',
        'fan_category': row['fan_category'] or '',
        'category': row['category'] or row['fan_category'] or '',
        'content_style': row['content_style'] or '',
        'contentStyle': row['content_style'] or '',
        'fans_profile': _json_col('fans_profile', {}),
        'ai_tags': _json_col('ai_tags', []),
        'ai_rating': row['ai_rating'] or '',
        'ai_summary': row['ai_summary'] or '',
        'ai_analysis': row['ai_analysis'] or row['ai_summary'] or '',
        'ai_reason': row['ai_reason'] or '',
        'risk_rating': row['risk_rating'] or '',
        'group_id': row['group_id'] or '',
        'status': row['status'] or 'active',
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def _follow_up_row_to_dict(row):
    if not row:
        return None
    return {
        'id': row['id'],
        'talent_id': row['talent_id'] or '',
        'follow_up_by': row['follow_up_by'] or '',
        'follow_up_at': row['follow_up_at'] if row['follow_up_at'] is not None else 0,
        'next_follow_up_at': row['next_follow_up_at'] if row['next_follow_up_at'] is not None else 0,
        'content': row['content'] or '',
        'result': row['result'] or '',
        'status': row['status'] or 'completed',
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
    }


def _dict_to_follow_up_row(f):
    now = int(time.time() * 1000)
    return {
        'id': f.get('id') or ('tfu_' + str(now) + '_' + uuid.uuid4().hex[:6]),
        'talent_id': f.get('talent_id') or '',
        'follow_up_by': f.get('follow_up_by') or f.get('followUpBy') or '',
        'follow_up_at': int(f.get('follow_up_at', f.get('followUpAt', now)) or now),
        'next_follow_up_at': int(f.get('next_follow_up_at', f.get('nextFollowUpAt', 0)) or 0),
        'content': f.get('content') or '',
        'result': f.get('result') or '',
        'status': f.get('status') or 'completed',
        'created_at': f.get('created_at') or f.get('createdAt') or now,
        'updated_at': now,
    }


def _dict_to_brand_row(b):
    now = int(time.time() * 1000)
    return {
        'id': b.get('id') or ('brand_' + str(now) + '_' + uuid.uuid4().hex[:6]),
        'name': b.get('name') or '',
        'logo': b.get('logo') or '',
        'shop_score': float(b.get('shop_score', 0) or 0),
        'shop_type': b.get('shop_type') or '',
        'main_category': b.get('main_category') or '',
        'total_products': int(b.get('total_products', 0) or 0),
        'total_talents': int(b.get('total_talents', 0) or 0),
        'avg_commission': float(b.get('avg_commission', 0) or 0),
        'group_id': b.get('group_id') or '',
        'status': b.get('status') or 'active',
        'created_at': b.get('created_at') or b.get('createdAt') or now,
        'updated_at': now,
    }


def _dict_to_talent_row(t):
    def _dump(val):
        if val is None:
            return '{}'
        return json.dumps(val, ensure_ascii=False)
    now = int(time.time() * 1000)
    return {
        'id': t.get('id') or ('tal_' + str(now) + '_' + uuid.uuid4().hex[:6]),
        'name': t.get('name') or '',
        'avatar': t.get('avatar') or '',
        'douyin_id': t.get('douyin_id') or t.get('douyinId') or '',
        'real_name': t.get('real_name') or t.get('realName') or '',
        'wechat': t.get('wechat') or t.get('contact_wechat') or t.get('contactWechat') or '',
        'phone': t.get('phone') or t.get('contact_phone') or t.get('contactPhone') or '',
        'email': t.get('email') or t.get('contact_email') or t.get('contactEmail') or '',
        'city': t.get('city') or '',
        'level': t.get('level') or '',
        'followers': int(t.get('followers', 0) or 0),
        'talent_type': t.get('talent_type') or t.get('talentType') or '',
        'location': t.get('location') or '',
        'agency': t.get('agency') or '',
        'tags': _dump(t.get('tags', [])),
        'bio': t.get('bio') or '',
        'contact': t.get('contact') or '',
        'contact_name': t.get('contact_name') or t.get('contactName') or '',
        'contact_phone': t.get('contact_phone') or t.get('contactPhone') or '',
        'contact_wechat': t.get('contact_wechat') or t.get('contactWechat') or '',
        'contact_email': t.get('contact_email') or t.get('contactEmail') or '',
        'cooperation_status': t.get('cooperation_status') or t.get('cooperationStatus') or 'available',
        'follow_up_by': t.get('follow_up_by') or t.get('followUpBy') or '',
        'next_follow_up_at': int(t.get('next_follow_up_at', t.get('nextFollowUpAt', 0)) or 0),
        'follow_up_note': t.get('follow_up_note') or t.get('followUpNote') or '',
        'commission_requirement': float(t.get('commission_requirement', 0) or 0),
        'fulfillment_score': float(t.get('fulfillment_score', 0) or 0),
        'rating_score': float(t.get('rating_score', 0) or 0),
        'total_gmv': float(t.get('total_gmv', 0) or 0),
        'total_products': int(t.get('total_products', 0) or 0),
        'product_count': int(t.get('product_count', t.get('total_products', 0)) or 0),
        'total_shops': int(t.get('total_shops', 0) or 0),
        'average_price': float(t.get('average_price', 0) or 0),
        'live_ratio': float(t.get('live_ratio', 0) or 0),
        'video_ratio': float(t.get('video_ratio', 0) or 0),
        'avg_live_gmv': float(t.get('avg_live_gmv', 0) or 0),
        'live_gpm': float(t.get('live_gpm', 0) or 0),
        'video_gpm': float(t.get('video_gpm', 0) or 0),
        'fan_gender': _dump(t.get('fan_gender', t.get('fanGender', {}))),
        'fan_age': _dump(t.get('fan_age', t.get('fanAge', {}))),
        'fan_region': _dump(t.get('fan_region', t.get('fanRegion', {}))),
        'fan_crowd': t.get('fan_crowd') or t.get('fanCrowd') or '',
        'fan_price_range': t.get('fan_price_range') or t.get('fanPriceRange') or '',
        'fan_category': t.get('fan_category') or t.get('fanCategory') or '',
        'category': t.get('category') or t.get('fan_category') or t.get('fanCategory') or '',
        'content_style': t.get('content_style') or t.get('contentStyle') or '',
        'fans_profile': _dump(t.get('fans_profile', t.get('fansProfile', {}))),
        'ai_tags': _dump(t.get('ai_tags', t.get('aiTags', []))),
        'ai_rating': t.get('ai_rating') or t.get('aiRating') or '',
        'ai_summary': t.get('ai_summary') or t.get('aiSummary') or '',
        'ai_analysis': t.get('ai_analysis') or t.get('aiAnalysis') or t.get('ai_summary') or t.get('aiSummary') or '',
        'ai_reason': t.get('ai_reason') or t.get('aiReason') or '',
        'risk_rating': t.get('risk_rating') or t.get('riskRating') or '',
        'group_id': t.get('group_id') or t.get('groupId') or '',
        'status': t.get('status') or 'active',
        'created_by': t.get('created_by') or t.get('createdBy') or '',
        'created_at': t.get('created_at') or t.get('createdAt') or now,
        'updated_at': now,
    }


def _sync_product_brand(conn, product):
    """根据 brand_id 或 brand 名称双向同步"""
    brand_id = product.get('brand_id') or ''
    brand_name = product.get('brand') or ''
    if brand_id and not brand_name:
        row = conn.execute('SELECT name FROM brands WHERE id = ?', (brand_id,)).fetchone()
        if row:
            product['brand'] = row['name']
    elif brand_name and not brand_id:
        row = conn.execute('SELECT id FROM brands WHERE name = ?', (brand_name,)).fetchone()
        if row:
            product['brand_id'] = row['id']


def _update_brand_product_stats(conn, brand_id):
    """同步品牌的商品数/达人数/平均佣金"""
    if not brand_id:
        return
    total_products = conn.execute(
        "SELECT COUNT(*) FROM products WHERE brand_id = ? AND status != 'archived'", (brand_id,)
    ).fetchone()[0]
    avg_comm = conn.execute(
        "SELECT AVG(commission_amount) FROM products WHERE brand_id = ? AND status != 'archived'", (brand_id,)
    ).fetchone()[0] or 0
    total_talents = conn.execute(
        "SELECT COUNT(DISTINCT talent_id) FROM product_talent_match WHERE product_id IN (SELECT id FROM products WHERE brand_id = ?)",
        (brand_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE brands SET total_products = ?, total_talents = ?, avg_commission = ?, updated_at = ? WHERE id = ?",
        (total_products, total_talents, round(avg_comm, 2), int(time.time() * 1000), brand_id)
    )


def _update_product_talent_count(conn, product_id):
    """同步商品的带货达人数"""
    if not product_id:
        return
    count = conn.execute(
        "SELECT COUNT(DISTINCT talent_id) FROM product_talent_match WHERE product_id = ?", (product_id,)
    ).fetchone()[0]
    conn.execute(
        "UPDATE products SET talent_count = ?, updated_at = ? WHERE id = ?",
        (count, int(time.time() * 1000), product_id)
    )


def _migrate_json_products_to_sqlite():
    """将旧版 data/products/index.json 迁移到 SQLite products 表"""
    old_path = os.path.join(PRODUCT_DIR, 'index.json')
    if not os.path.isfile(old_path):
        return
    print('  [Product] 发现旧版 JSON 商品库，开始迁移到 SQLite...', flush=True)
    data = _read_json(old_path, {'products': []})
    products = data.get('products', [])
    if not products:
        try:
            os.rename(old_path, old_path + '.bak')
            print('  [Product] 旧 JSON 为空，已备份', flush=True)
        except Exception as e:
            print(f'  [Product] 备份旧 JSON 失败: {e}', flush=True)
        return

    conn = _db_conn()
    try:
        inserted = 0
        skipped = 0
        for p in products:
            pid = p.get('id')
            if not pid:
                continue
            if conn.execute('SELECT 1 FROM products WHERE id = ?', (pid,)).fetchone():
                skipped += 1
                continue
            row = _dict_to_product_row(p)
            now = int(time.time() * 1000)
            if not row['created_at']:
                row['created_at'] = now
            if not row['updated_at']:
                row['updated_at'] = now
            conn.execute(
                f"INSERT INTO products ({', '.join(_PRODUCT_COLUMNS)}) VALUES ({', '.join('?' * len(_PRODUCT_COLUMNS))})",
                tuple(row[c] for c in _PRODUCT_COLUMNS)
            )
            inserted += 1
        conn.commit()
        print(f'  [Product] JSON 迁移完成: 插入 {inserted} 条, 跳过 {skipped} 条', flush=True)
    finally:
        conn.close()

    try:
        bak_path = old_path + '.bak'
        if os.path.exists(bak_path):
            os.remove(bak_path)
        os.rename(old_path, bak_path)
        print(f'  [Product] 旧 JSON 已备份: {bak_path}', flush=True)
    except Exception as e:
        print(f'  [Product] 备份旧 JSON 失败: {e}', flush=True)


# FIXME: 记忆三级沉淀辅助函数（二级归纳 memory_summary、三级知识库 knowledge_base）
def _parse_json_col(val, default=None):
    """安全解析 SQLite JSON 列"""
    if val is None:
        return default
    try:
        return json.loads(val)
    except Exception:
        return default


def _dump_json_col(val):
    """Python 对象 -> SQLite JSON 文本"""
    if val is None:
        return '[]'
    return json.dumps(val, ensure_ascii=False)


def _memory_summary_row_to_dict(row):
    """memory_summary 行 -> 前端兼容 dict"""
    if not row:
        return None
    return {
        'id': row['id'],
        'empId': row['emp_id'],
        'summaryType': row['summary_type'],
        'title': row['title'],
        'date': row['date'],
        'projectName': row['project_name'],
        'status': row['status'],
        'keyPoints': _parse_json_col(row['key_points'], []),
        'decisions': _parse_json_col(row['decisions'], []),
        'pending': _parse_json_col(row['pending'], []),
        'actionItems': _parse_json_col(row['action_items'], []),
        'relatedMemIds': _parse_json_col(row['related_mem_ids'], []),
        'sourceMemIds': _parse_json_col(row['source_mem_ids'], []),
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'time': row['created_at'],
    }


def _knowledge_base_row_to_dict(row):
    """knowledge_base 行 -> 前端兼容 dict"""
    if not row:
        return None
    return {
        'id': row['id'],
        'empId': row['emp_id'],
        'title': row['title'],
        'content': row['content'],
        'source': row['source'],
        'tags': _parse_json_col(row['tags'], []),
        'evidenceCount': row['evidence_count'],
        'relatedMemIds': _parse_json_col(row['related_mem_ids'], []),
        'status': row['status'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'time': row['created_at'],
    }


# FIXME: 决策关键词触发二级归纳
_DECISION_KEYWORDS = ['定了', '确认', '就这么办', 'deadline', '标准', '参数', '方案确定', '已确定', '决定', '拍板']


def _contains_decision_keyword(text):
    """判断文本是否包含决策关键词"""
    if not text:
        return False
    text = str(text)
    return any(k in text for k in _DECISION_KEYWORDS)


def _load_memory_summaries(emp_id, summary_type=None, date=None, project_name=None, keyword=None, limit=50):
    """查询 memory_summary 列表（默认只返回 active，避免已删除/归档数据污染 AI 分析）"""
    conn = _db_conn()
    try:
        conds = ['emp_id = ?', "status = 'active'"]
        params = [emp_id]
        if summary_type:
            conds.append('summary_type = ?')
            params.append(summary_type)
        if date:
            conds.append('date = ?')
            params.append(date)
        if project_name:
            conds.append('project_name = ?')
            params.append(project_name)
        if keyword:
            conds.append('(title LIKE ? OR project_name LIKE ?)')
            params.append('%' + keyword + '%')
            params.append('%' + keyword + '%')
        sql = 'SELECT * FROM memory_summary WHERE ' + ' AND '.join(conds) + ' ORDER BY updated_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_memory_summary_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _save_memory_summary(summary):
    """保存/更新 memory_summary（UPSERT）"""
    conn = _db_conn()
    try:
        now = int(time.time() * 1000)
        summary_id = summary.get('id') or ('sum_' + str(uuid.uuid4())[:8])
        created_at = summary.get('createdAt') or summary.get('created_at') or now
        updated_at = now
        conn.execute('''
            INSERT INTO memory_summary (id, emp_id, summary_type, title, date, project_name, status,
                key_points, decisions, pending, action_items, related_mem_ids, source_mem_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                date=excluded.date,
                project_name=excluded.project_name,
                status=excluded.status,
                key_points=excluded.key_points,
                decisions=excluded.decisions,
                pending=excluded.pending,
                action_items=excluded.action_items,
                related_mem_ids=excluded.related_mem_ids,
                source_mem_ids=excluded.source_mem_ids,
                updated_at=excluded.updated_at
        ''', (
            summary_id, summary.get('empId') or summary.get('emp_id'),
            summary.get('summaryType') or summary.get('summary_type'),
            summary.get('title', ''), summary.get('date'),
            summary.get('projectName') or summary.get('project_name'),
            summary.get('status', 'pending'),
            _dump_json_col(summary.get('keyPoints') or summary.get('key_points')),
            _dump_json_col(summary.get('decisions')),
            _dump_json_col(summary.get('pending')),
            _dump_json_col(summary.get('actionItems') or summary.get('action_items')),
            _dump_json_col(summary.get('relatedMemIds') or summary.get('related_mem_ids')),
            _dump_json_col(summary.get('sourceMemIds') or summary.get('source_mem_ids')),
            created_at, updated_at
        ))
        conn.commit()
        return summary_id
    finally:
        conn.close()


def _load_knowledge_base(emp_id, keyword=None, status=None, limit=200):
    """查询 knowledge_base 列表（默认只返回 active，避免已删除/归档数据污染 AI 分析）"""
    conn = _db_conn()
    try:
        conds = ['(emp_id = ? OR emp_id IS NULL)', "status = 'active'"]
        params = [emp_id]
        if status:
            conds.append('status = ?')
            params.append(status)
        if keyword:
            conds.append('(title LIKE ? OR content LIKE ?)')
            params.append('%' + keyword + '%')
            params.append('%' + keyword + '%')
        sql = 'SELECT * FROM knowledge_base WHERE ' + ' AND '.join(conds) + ' ORDER BY updated_at DESC LIMIT ?'
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_knowledge_base_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def _upsert_knowledge_base(kb):
    """插入或更新 knowledge_base；evidence_count>=阈值 或决策触发时标记 active"""
    conn = _db_conn()
    try:
        now = int(time.time() * 1000)
        kb_id = kb.get('id') or ('kb_' + str(uuid.uuid4())[:8])
        # 按 id 更新
        existing = conn.execute(
            'SELECT evidence_count, related_mem_ids, status FROM knowledge_base WHERE id = ?', (kb_id,)
        ).fetchone()
        if existing:
            old_ids = _parse_json_col(existing['related_mem_ids'], [])
            new_ids = list(dict.fromkeys(old_ids + _parse_json_col(kb.get('relatedMemIds') or kb.get('related_mem_ids'), [])))
            count = existing['evidence_count'] + int(kb.get('evidenceCount') or 1)
            kb_min = MEMORY_INDUCTION_THRESHOLDS['knowledge_repeat_min']
            new_status = 'active' if count >= kb_min or kb.get('status') == 'active' or existing['status'] == 'active' else existing['status']
            conn.execute('''
                UPDATE knowledge_base SET title=?, content=?, source=?, tags=?,
                evidence_count=?, related_mem_ids=?, status=?, updated_at=?
                WHERE id=?
            ''', (
                kb.get('title', ''), kb.get('content', ''), kb.get('source', 'manual'),
                _dump_json_col(kb.get('tags')), count, _dump_json_col(new_ids), new_status, now, kb_id
            ))
            conn.commit()
            return kb_id
        # 按内容相似合并（简单子串匹配）
        candidates = conn.execute(
            "SELECT id, title, content, evidence_count, related_mem_ids, status FROM knowledge_base WHERE (emp_id=? OR emp_id IS NULL) AND status='active'",
            (kb.get('empId') or kb.get('emp_id'),)
        ).fetchall()
        content = kb.get('content', '')
        kb_min = MEMORY_INDUCTION_THRESHOLDS['knowledge_repeat_min']
        for cand in candidates:
            if content and (content in cand['content'] or cand['content'] in content or content in cand['title']):
                old_ids = _parse_json_col(cand['related_mem_ids'], [])
                new_ids = list(dict.fromkeys(old_ids + _parse_json_col(kb.get('relatedMemIds') or kb.get('related_mem_ids'), [])))
                count = cand['evidence_count'] + int(kb.get('evidenceCount') or 1)
                new_status = 'active' if count >= kb_min or kb.get('status') == 'active' or cand['status'] == 'active' else cand['status']
                conn.execute(
                    'UPDATE knowledge_base SET evidence_count=?, related_mem_ids=?, status=?, updated_at=? WHERE id=?',
                    (count, _dump_json_col(new_ids), new_status, now, cand['id'])
                )
                conn.commit()
                return cand['id']
        created_at = kb.get('createdAt') or kb.get('created_at') or now
        status = kb.get('status', 'pending')
        if _contains_decision_keyword(content):
            status = 'active'
        conn.execute('''
            INSERT INTO knowledge_base (id, emp_id, title, content, source, tags, evidence_count, related_mem_ids, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            kb_id, kb.get('empId') or kb.get('emp_id'), kb.get('title', ''), kb.get('content', ''),
            kb.get('source', 'manual'), _dump_json_col(kb.get('tags')),
            int(kb.get('evidenceCount') or 1),
            _dump_json_col(kb.get('relatedMemIds') or kb.get('related_mem_ids')),
            status, created_at, now
        ))
        conn.commit()
        return kb_id
    finally:
        conn.close()


def _auto_check_knowledge(emp_id, mem_id, value, tags=None):
    """保存记忆时自动检查是否应沉淀到知识库（决策直接 active；重复>=阈值）"""
    if not value:
        return None
    content = str(value)
    # 决策触发：直接沉淀为 active
    if _contains_decision_keyword(content):
        title = content[:40] + ('...' if len(content) > 40 else '')
        return _upsert_knowledge_base({
            'empId': emp_id,
            'title': '决策：' + title,
            'content': content,
            'source': 'auto_decision',
            'tags': tags or [],
            'relatedMemIds': [mem_id],
            'status': 'active'
        })
    # 重复提及：创建 pending，evidence_count 由 upsert 累加
    title = content[:40] + ('...' if len(content) > 40 else '')
    return _upsert_knowledge_base({
        'empId': emp_id,
        'title': '知识点：' + title,
        'content': content,
        'source': 'auto_repeat',
        'tags': tags or [],
        'relatedMemIds': [mem_id],
        'status': 'pending'
    })


def _count_memories_by_tag(emp_id, tag):
    """统计某员工含指定标签的记忆数量及 ID 列表"""
    data = ms3.load_memory(emp_id)
    count = 0
    ids = []
    for m in data.get('core', []) + data.get('daily', []):
        tags = m.get('tags') or []
        if tag in tags:
            count += 1
            ids.append(m.get('id'))
    return count, ids


def _create_pending_summary(emp_id, summary_type, title, date=None, project_name=None, mem_ids=None):
    """创建待 AI 生成的二级归纳记录；如已存在则复用"""
    conn = _db_conn()
    try:
        existing = None
        if summary_type == 'daily' and date:
            existing = conn.execute(
                "SELECT id FROM memory_summary WHERE emp_id=? AND summary_type=? AND date=? AND status='active'",
                (emp_id, summary_type, date)
            ).fetchone()
        elif summary_type == 'project' and project_name:
            existing = conn.execute(
                "SELECT id FROM memory_summary WHERE emp_id=? AND summary_type=? AND project_name=? AND status='active'",
                (emp_id, summary_type, project_name)
            ).fetchone()
        if existing:
            return existing['id']
        now = int(time.time() * 1000)
        sid = 'sum_' + str(uuid.uuid4())[:8]
        conn.execute('''
            INSERT INTO memory_summary (id, emp_id, summary_type, title, date, project_name, status,
                key_points, decisions, pending, action_items, related_mem_ids, source_mem_ids, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', '[]', '[]', '[]', '[]', ?, ?, ?, ?)
        ''', (sid, emp_id, summary_type, title, date, project_name,
              _dump_json_col(mem_ids or []), _dump_json_col(mem_ids or []), now, now))
        conn.commit()
        return sid
    finally:
        conn.close()


def _auto_summarize_triggers(emp_id, memory):
    """记忆保存后自动触发二级归纳 pending 记录：数量触发 / 决策触发"""
    value = memory.get('value', '')
    mem_id = memory.get('id')
    tags = memory.get('tags') or []
    triggered = []
    project_min = MEMORY_INDUCTION_THRESHOLDS['project_summary_min']
    # 数量触发：任一标签对应记忆 >= 项目归纳阈值 条时自动创建项目归纳
    checked_tags = set()
    for tag in tags:
        if not tag or tag in checked_tags:
            continue
        checked_tags.add(tag)
        count, ids = _count_memories_by_tag(emp_id, tag)
        if count >= project_min:
            sid = _create_pending_summary(emp_id, 'project', '项目归纳：' + tag, project_name=tag, mem_ids=ids)
            triggered.append({'type': 'count', 'tag': tag, 'summaryId': sid})
    # 每日归纳触发：当天日常记录 >= 每日归纳阈值 条 或 包含决策关键词
    today = datetime.now().strftime('%Y-%m-%d')
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_ms = int(today_start.timestamp() * 1000)
    daily_min = MEMORY_INDUCTION_THRESHOLDS['daily_consolidate_min']
    data = ms3.load_memory(emp_id)
    today_daily_ids = [
        m.get('id') for m in data.get('daily', [])
        if m.get('createdAt', 0) >= today_start_ms
    ]
    if len(today_daily_ids) >= daily_min or _contains_decision_keyword(value):
        sid = _create_pending_summary(emp_id, 'daily', today + ' 每日归纳', date=today, mem_ids=today_daily_ids or [mem_id])
        triggered.append({'type': 'daily', 'summaryId': sid})
    return triggered


def _seed_coolchap_data(conn):
    """当不存在 COOLCHAP 品牌商品时，写入 COOLCHAP 品牌示例数据（含品牌、达人、商品、匹配关系）"""
    count = conn.execute("SELECT COUNT(*) FROM products WHERE brand = 'COOLCHAP'").fetchone()[0]
    if count > 0:
        return

    now = int(time.time() * 1000)

    # 创建品牌
    brand_id = 'brand_coolchap_' + uuid.uuid4().hex[:6]
    conn.execute(
        f"INSERT INTO brands ({', '.join(_BRAND_COLUMNS)}) VALUES ({', '.join('?' * len(_BRAND_COLUMNS))})",
        (brand_id, 'COOLCHAP', '', 4.8, '官方旗舰店', '鞋靴', 0, 0, 10.5, '', 'active', now, now)
    )

    brand_info = {
        'name': 'COOLCHAP',
        'nameCn': '酷恰',
        'origin': '西班牙马略卡岛',
        'style': '地中海度假风',
        'keywords': ['地中海度假风', '自由浪漫', '艺术小众', '软底舒适', '百搭实穿'],
        'priceBand': '300-800元',
        'icon': '👟',
        'category': '鞋履',
        'store': 'COOLCHAP官方旗舰店',
        'note': '源自西班牙马略卡岛，主打地中海度假风与舒适实穿性'
    }
    base_channel = {
        'brand_info': brand_info,
        '达人带货': 97.25,
        '视频': 92.72,
        '直播': 15.3,
        '商城': 2.75,
        '其他': 0
    }
    base_audience = {
        'gender': {'女': 96.83, '男': 3.17},
        'age': {'31-35': 33.48, '26-30': 28.12, '36-40': 18.67, '18-25': 12.45, '41+': 7.28},
        'region': {'四川': 7.81, '广东': 6.92, '浙江': 6.54, '江苏': 6.12, '河南': 5.88, '山东': 5.43},
        'occupation': {'精致妈妈': 31.45, '都市白领': 24.18, 'Z世代': 18.62, '小镇青年': 14.75, '其他': 11.0},
        'interests': {'时尚穿搭': 45.2, '美妆护肤': 22.1, '家居生活': 15.3, '亲子育儿': 10.4, '其他': 7.0}
    }
    base_talents = [
        {
            'id': 'tal_lumama',
            'name': '璐妈妈',
            'avatar': '',
            'douyin_id': 'lumama520',
            'level': 'L4',
            'followers': 528000,
            'talent_type': '达人号',
            'location': '杭州',
            'agency': '星耀文化',
            'tags': ['精致妈妈', '时尚穿搭', '亲子'],
            'bio': '专注品质穿搭与好物分享的精致妈妈',
            'contact': '微信 lumama520',
            'cooperation_status': 'cooperating',
            'commission_requirement': 15,
            'fulfillment_score': 4.7,
            'rating_score': 4.8,
            'total_gmv': 1884560,
            'total_products': 86,
            'total_shops': 12,
            'live_ratio': 35,
            'video_ratio': 65,
            'avg_live_gmv': 12500,
            'live_gpm': 850,
            'video_gpm': 420,
            'fan_gender': {'女': 92, '男': 8},
            'fan_age': {'31-35': 38, '26-30': 28, '36-40': 18, '18-25': 10, '41+': 6},
            'fan_region': {'浙江': 12, '江苏': 9, '广东': 8, '四川': 7, '山东': 6},
            'fan_crowd': '精致妈妈',
            'fan_price_range': '300-600',
            'fan_category': '鞋靴/凉鞋',
        },
        {
            'id': 'tal_dapeishi_w',
            'name': '搭配师W',
            'avatar': '',
            'douyin_id': 'dapeishi_w',
            'level': 'L3',
            'followers': 123000,
            'talent_type': '达人号',
            'location': '上海',
            'agency': '独立',
            'tags': ['时尚穿搭', '设计师款', '小众'],
            'bio': '用搭配表达态度，发掘小众设计师好物',
            'contact': '微信 dapeishi_w',
            'cooperation_status': 'available',
            'commission_requirement': 12,
            'fulfillment_score': 4.5,
            'rating_score': 4.6,
            'total_gmv': 520000,
            'total_products': 45,
            'total_shops': 8,
            'live_ratio': 20,
            'video_ratio': 80,
            'avg_live_gmv': 6800,
            'live_gpm': 720,
            'video_gpm': 380,
            'fan_gender': {'女': 88, '男': 12},
            'fan_age': {'26-30': 35, '18-25': 30, '31-35': 20, '36-40': 10, '41+': 5},
            'fan_region': {'上海': 14, '广东': 10, '浙江': 9, '北京': 8, '江苏': 7},
            'fan_crowd': '都市白领',
            'fan_price_range': '400-800',
            'fan_category': '鞋靴/凉鞋',
        },
        {
            'id': 'tal_chaoxie',
            'name': '潮鞋研究所',
            'avatar': '',
            'douyin_id': 'chaoxie_lab',
            'level': 'L5',
            'followers': 891000,
            'talent_type': '达人号',
            'location': '广州',
            'agency': '鞋履MCN',
            'tags': ['潮鞋', '测评', '运动'],
            'bio': '专业测评百双潮鞋，帮你避坑选好鞋',
            'contact': '商务 chaoxie@mcn.com',
            'cooperation_status': 'available',
            'commission_requirement': 8,
            'fulfillment_score': 4.8,
            'rating_score': 4.7,
            'total_gmv': 3200000,
            'total_products': 120,
            'total_shops': 25,
            'live_ratio': 40,
            'video_ratio': 60,
            'avg_live_gmv': 22000,
            'live_gpm': 950,
            'video_gpm': 510,
            'fan_gender': {'男': 55, '女': 45},
            'fan_age': {'18-25': 32, '26-30': 30, '31-35': 20, '36-40': 12, '41+': 6},
            'fan_region': {'广东': 13, '四川': 9, '浙江': 8, '江苏': 7, '河南': 6},
            'fan_crowd': 'Z世代',
            'fan_price_range': '200-500',
            'fan_category': '鞋靴/凉鞋',
        },
        {
            'id': 'tal_xiaomei',
            'name': '小美穿搭日记',
            'avatar': '',
            'douyin_id': 'xiaomei_riji',
            'level': 'L3',
            'followers': 245000,
            'talent_type': '达人号',
            'location': '成都',
            'agency': '小美工作室',
            'tags': ['甜美', '度假风', '日常穿搭'],
            'bio': '分享甜美度假风穿搭，做你的衣橱闺蜜',
            'contact': '微信 xiaomei_riji',
            'cooperation_status': 'communicating',
            'commission_requirement': 10,
            'fulfillment_score': 4.6,
            'rating_score': 4.7,
            'total_gmv': 890000,
            'total_products': 62,
            'total_shops': 10,
            'live_ratio': 25,
            'video_ratio': 75,
            'avg_live_gmv': 9200,
            'live_gpm': 680,
            'video_gpm': 360,
            'fan_gender': {'女': 95, '男': 5},
            'fan_age': {'18-25': 38, '26-30': 32, '31-35': 18, '36-40': 8, '41+': 4},
            'fan_region': {'四川': 11, '广东': 9, '浙江': 8, '江苏': 7, '湖南': 6},
            'fan_crowd': 'Z世代',
            'fan_price_range': '300-600',
            'fan_category': '鞋靴/凉鞋',
        }
    ]

    # 兼容旧字段
    base_influencers = [
        {
            'id': t['id'],
            'name': t['name'],
            'followerCount': t['followers'],
            'sales': [1324, 568, 2103, 892][i],
            'settlementAmount': [188456, 80952, 299784, 127312][i],
            'conversionRate': [3.2, 2.8, 4.1, 3.5][i],
            'commissionRate': [20, 15, 5, 10][i],
            'source': '抖音精选联盟' if i % 2 == 0 else '手动录入'
        }
        for i, t in enumerate(base_talents)
    ]

    def make_videos(product_name):
        return [
            {'title': f'{product_name} 开箱测评', 'cover': '', 'url': '', 'views': 120000, 'likes': 5600},
            {'title': f'{product_name} 穿搭推荐', 'cover': '', 'url': '', 'views': 85000, 'likes': 3200}
        ]

    seed_items = [
        {
            'name': '嘭嘭爱心系列人字拖',
            'subtitle': 'COOLCHAP 经典爱心造型人字拖，Q弹软底贴合足弓，地中海度假风轻松出行',
            'price': 329,
            'monthly_sales': 4200,
            'rate': 12,
            'tags': ['软底舒适', '地中海度假风', '爱心造型', '夏日必备'],
            'selling_points': '嘭嘭爱心立体造型，EVA软底久走不累；地中海配色，度假与日常轻松切换。'
        },
        {
            'name': '设计师款凉鞋',
            'subtitle': 'COOLCHAP 设计师联名款凉鞋，简约线条搭配软垫鞋床，诠释自由浪漫',
            'price': 599,
            'monthly_sales': 1850,
            'rate': 10,
            'tags': ['设计师款', '软底舒适', '自由浪漫', '百搭实穿'],
            'selling_points': '设计师操刀鞋型，脚床加厚软垫；可盐可甜，通勤度假两相宜。'
        },
        {
            'name': '平底沙滩鞋',
            'subtitle': 'COOLCHAP 平底沙滩鞋，轻盈透气防滑底，马略卡岛海滨灵感',
            'price': 379,
            'monthly_sales': 3100,
            'rate': 11,
            'tags': ['平底', '沙滩鞋', '地中海度假风', '透气防滑'],
            'selling_points': '轻量化鞋身+防滑大底，海边漫步不累脚；编织透气鞋面，清爽一夏。'
        },
        {
            'name': '铆钉装饰凉鞋',
            'subtitle': 'COOLCHAP 铆钉装饰凉鞋，艺术小众设计，软底舒适与个性态度兼具',
            'price': 469,
            'monthly_sales': 2200,
            'rate': 9,
            'tags': ['铆钉', '艺术小众', '软底舒适', '个性穿搭'],
            'selling_points': '手工感铆钉点缀，艺术小众不撞款；软弹鞋底平衡个性与舒适。'
        },
        {
            'name': '厚底松糕拖鞋',
            'subtitle': 'COOLCHAP 厚底松糕拖鞋，隐形增高拉长腿型，软底踩云感',
            'price': 359,
            'monthly_sales': 2800,
            'rate': 13,
            'tags': ['厚底', '松糕', '软底舒适', '百搭实穿'],
            'selling_points': '4cm厚底自然增高，松糕底却轻量；软底踩云感，久站不累。'
        },
        {
            'name': '蝴蝶结凉拖',
            'subtitle': 'COOLCHAP 蝴蝶结凉拖，甜美蝴蝶结与软底舒适结合，地中海浪漫气息',
            'price': 419,
            'monthly_sales': 2600,
            'rate': 10,
            'tags': ['蝴蝶结', '甜美', '软底舒适', '地中海度假风'],
            'selling_points': '立体蝴蝶结点缀，浪漫度假风；一体成型软底，轻盈回弹好打理。'
        },
    ]

    product_ids = []
    for idx, item in enumerate(seed_items, 1):
        pid = f'prod_coolchap_{idx}_{uuid.uuid4().hex[:6]}'
        product_ids.append(pid)
        price = item['price']
        monthly_sales = item['monthly_sales']
        rate = item['rate']
        monthly_gmv = round(price * monthly_sales, 2)
        commission_amount = round(price * rate / 100, 2)
        row = {
            'id': pid,
            'name': item['name'],
            'subtitle': item['subtitle'],
            'main_image': '',
            'price': price,
            'price_range': f'¥{price}',
            'brand': 'COOLCHAP',
            'brand_id': brand_id,
            'category': '鞋靴/凉鞋',
            'sku_specs': json.dumps({'颜色': ['米白', '棕色', '黑色'], '尺码': ['35-40']}, ensure_ascii=False),
            'stock': 10000,
            'status': 'active',
            'monthly_sales': monthly_sales,
            'monthly_gmv': monthly_gmv,
            'commission_rates': json.dumps({'投放期': rate, '常规活动期': max(5, rate // 2), '其他': 5}, ensure_ascii=False),
            'commission_amount': commission_amount,
            'conversion_rate': 3.5,
            'avg_order_value': price,
            'influencer_count': len(base_influencers),
            'talent_count': len(base_talents),
            'video_count': 2,
            'live_count': 1,
            'channel_distribution': json.dumps(base_channel, ensure_ascii=False),
            'influencers': json.dumps(base_influencers, ensure_ascii=False),
            'audience': json.dumps(base_audience, ensure_ascii=False),
            'ai_analysis': json.dumps({}, ensure_ascii=False),
            'videos': json.dumps(make_videos(item['name']), ensure_ascii=False),
            'tags': json.dumps(item.get('tags', []), ensure_ascii=False),
            'selling_points': item.get('selling_points', ''),
            'created_at': now,
            'updated_at': now,
        }
        conn.execute(
            f"INSERT INTO products ({', '.join(_PRODUCT_COLUMNS)}) VALUES ({', '.join('?' * len(_PRODUCT_COLUMNS))})",
            tuple(row[c] for c in _PRODUCT_COLUMNS)
        )

    # 写入示例达人
    for t in base_talents:
        t['group_id'] = brand_id
        row = _dict_to_talent_row(t)
        row['created_at'] = now
        row['updated_at'] = now
        conn.execute(
            f"INSERT INTO talents ({', '.join(_TALENT_COLUMNS)}) VALUES ({', '.join('?' * len(_TALENT_COLUMNS))})",
            tuple(row[c] for c in _TALENT_COLUMNS)
        )

    # 写入商品-达人匹配关系
    sales_list = [1324, 568, 2103, 892]
    for pid in product_ids:
        for i, t in enumerate(base_talents):
            ptm_id = 'ptm_' + str(now) + '_' + uuid.uuid4().hex[:6]
            score, reasons = (88, ['类目一致', '价格带匹配', '粉丝画像契合']) if i % 2 == 0 else (72, ['类目一致', '价格带基本匹配'])
            conn.execute(
                f"INSERT INTO product_talent_match ({', '.join(_PTM_COLUMNS)}) VALUES ({', '.join('?' * len(_PTM_COLUMNS))})",
                (ptm_id, pid, t['id'], score, '；'.join(reasons), sales_list[i], [3.2, 2.8, 4.1, 3.5][i], 1 if score >= 75 else 0, now, now)
            )

    _update_brand_product_stats(conn, brand_id)
    for pid in product_ids:
        _update_product_talent_count(conn, pid)
    conn.commit()
    print(f'  [Product] 已写入 COOLCHAP 示例数据 {len(seed_items)} 条商品 / {len(base_talents)} 条达人', flush=True)


def knowledge_create(title, content, category='', embedding=None, api_key=None, provider='openai', model=None, base_url=None):
    """创建知识条目，自动生成 embedding"""
    kid = 'kb_' + uuid.uuid4().hex[:8]
    now = int(time.time() * 1000)

    # 如果没有传入 embedding 但有 api_key，自动生成
    if embedding is None and api_key:
        try:
            text = f'{title}\n{content}'
            if category:
                text = f'分类: {category}\n' + text
            emb = get_embedding(text[:8000], api_key, provider, model=model, base_url=base_url)
            embedding = json.dumps(emb) if emb else None
        except Exception as e:
            print(f'  [Knowledge] embedding 生成失败: {e}', flush=True)

    conn = _db_conn()
    try:
        conn.execute('''
            INSERT INTO knowledge (id, title, content, category, embedding, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (kid, title, content, category or '', embedding, now, now))
        conn.commit()
        return _knowledge_row_to_dict(conn.execute(
            'SELECT * FROM knowledge WHERE id = ?', (kid,)
        ).fetchone())
    finally:
        conn.close()


def knowledge_get_by_id(kid):
    """获取单条知识详情"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        return _knowledge_row_to_dict(row)
    finally:
        conn.close()


def knowledge_list(offset=0, limit=50, category=None, keyword=None):
    """知识列表（支持分页、分类筛选、关键词搜索）"""
    conn = _db_conn()
    try:
        where = []
        params = []
        if category:
            where.append('category = ?')
            params.append(category)
        if keyword:
            where.append('(title LIKE ? OR content LIKE ?)')
            like = f'%{keyword}%'
            params.extend([like, like])

        sql = 'SELECT * FROM knowledge'
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY updated_at DESC LIMIT ? OFFSET ?'
        params.extend([limit, offset])

        rows = conn.execute(sql, params).fetchall()
        docs = [_knowledge_row_to_dict(r) for r in rows]

        # 总数
        count_sql = 'SELECT COUNT(*) FROM knowledge'
        if where:
            count_sql += ' WHERE ' + ' AND '.join(where[:-2] if where else [])
            # 简化：直接重新构造 count 条件
        count_params = params[:-2]  # 去掉 limit 和 offset
        # 重新构造 count
        count_where = []
        count_params = []
        if category:
            count_where.append('category = ?')
            count_params.append(category)
        if keyword:
            count_where.append('(title LIKE ? OR content LIKE ?)')
            like = f'%{keyword}%'
            count_params.extend([like, like])
        count_sql = 'SELECT COUNT(*) FROM knowledge'
        if count_where:
            count_sql += ' WHERE ' + ' AND '.join(count_where)
        total = conn.execute(count_sql, count_params).fetchone()[0]

        return {'docs': docs, 'total': total, 'offset': offset, 'limit': limit}
    finally:
        conn.close()


def knowledge_update(kid, title=None, content=None, category=None, embedding=None, api_key=None, provider='openai', model=None, base_url=None):
    """更新知识条目，内容变更时自动更新 embedding"""
    conn = _db_conn()
    try:
        row = conn.execute('SELECT * FROM knowledge WHERE id = ?', (kid,)).fetchone()
        if not row:
            return None

        updates = {}
        if title is not None:
            updates['title'] = title
        if content is not None:
            updates['content'] = content
        if category is not None:
            updates['category'] = category

        # 如果内容或标题变更，且有 api_key，重新生成 embedding
        if ('title' in updates or 'content' in updates or 'category' in updates) and api_key:
            try:
                new_title = updates.get('title', row['title'])
                new_content = updates.get('content', row['content'])
                new_cat = updates.get('category', row['category'])
                text = f'{new_title}\n{new_content}'
                if new_cat:
                    text = f'分类: {new_cat}\n' + text
                emb = get_embedding(text[:8000], api_key, provider, model=model, base_url=base_url)
                if emb:
                    updates['embedding'] = json.dumps(emb)
            except Exception as e:
                print(f'  [Knowledge] update embedding 失败: {e}', flush=True)

        if embedding is not None and 'embedding' not in updates:
            updates['embedding'] = json.dumps(embedding) if isinstance(embedding, list) else embedding

        if not updates:
            return _knowledge_row_to_dict(row)

        updates['updated_at'] = int(time.time() * 1000)
        fields = ', '.join(f'{k} = ?' for k in updates.keys())
        values = list(updates.values()) + [kid]
        conn.execute(f'UPDATE knowledge SET {fields} WHERE id = ?', values)
        conn.commit()
        return _knowledge_row_to_dict(conn.execute(
            'SELECT * FROM knowledge WHERE id = ?', (kid,)
        ).fetchone())
    finally:
        conn.close()


def knowledge_delete(kid):
    """删除知识条目"""
    conn = _db_conn()
    try:
        cur = conn.execute('DELETE FROM knowledge WHERE id = ?', (kid,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def knowledge_search_semantic(query, api_key, provider='openai', limit=3, model=None, base_url=None):
    """语义检索：用 embedding 向量相似度返回最相关的知识"""
    if not query or not query.strip() or not api_key:
        return []

    # 1. 获取 query 的 embedding
    query_emb = get_embedding(query, api_key, provider, model=model, base_url=base_url)
    if not query_emb:
        return []

    # 2. 加载所有带 embedding 的知识
    conn = _db_conn()
    try:
        rows = conn.execute(
            'SELECT id, title, content, category, embedding, created_at, updated_at FROM knowledge WHERE embedding IS NOT NULL'
        ).fetchall()
    finally:
        conn.close()

    # 3. 计算余弦相似度并排序
    scored = []
    for row in rows:
        try:
            emb = json.loads(row['embedding'])
            score = cosine_similarity(query_emb, emb)
            if score > 0.0:
                scored.append((score, row))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)
    return [_knowledge_row_to_dict(r) for _, r in scored[:limit]]


def knowledge_migrate_from_json():
    """从旧版 JSON 知识库迁移到 SQLite（启动时调用）"""
    json_path = os.path.join(KNOWLEDGE_DIR, 'index.json')
    if not os.path.exists(json_path):
        return 0
    data = _read_json(json_path, {'docs': []})
    docs = data.get('docs', [])
    if not docs:
        return 0

    conn = _db_conn()
    migrated = 0
    try:
        for doc in docs:
            # 检查是否已存在
            existing = conn.execute('SELECT 1 FROM knowledge WHERE id = ?', (doc.get('id'),)).fetchone()
            if existing:
                continue
            now = doc.get('createdAt', int(time.time() * 1000))
            conn.execute('''
                INSERT INTO knowledge (id, title, content, category, embedding, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                doc.get('id'),
                doc.get('name', doc.get('title', '未命名')),
                doc.get('content', ''),
                doc.get('category', ''),
                None,
                now,
                doc.get('updatedAt', now),
            ))
            migrated += 1
        conn.commit()
        print(f'  [Knowledge] 从 JSON 迁移 {migrated} 条记录到 SQLite', flush=True)
    finally:
        conn.close()
    return migrated


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
        try:
            line = f'  [{timestamp}] {msg}'
        except Exception:
            line = f'  [{timestamp}] <log encode error>'
        try:
            sys.stdout.buffer.write(line.encode('utf-8', errors='replace') + b'\n')
            sys.stdout.buffer.flush()
        except Exception:
            pass

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
    def _normalize_path(self, path):
        """统一处理路径：去掉 query string 和末尾斜杠（根路径除外）"""
        path = path.split('?')[0]
        if path != '/' and path.endswith('/'):
            path = path[:-1]
        return path

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
        path = self._normalize_path(self.path)

        # Auth routes (no auth required)
        if path == '/api/auth/me' or path == '/auth/me':
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
        if path == '/api/openclaw/dreaming':
            self._handle_get_dreaming()
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

        # Health check (no auth required)
        if path == '/api/health':
            self._send_json(200, {
                'status': 'ok',
                'timestamp': time.time(),
                'features': {
                    'douyin_parse': True,
                    'douyin_transcribe': True,
                    'ffmpeg': _check_ffmpeg()
                }
            })
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

        # FIXME: 大脑知识中枢 API
        if path == '/api/brain/status':
            self._handle_get_brain_status()
            return
        if path == '/api/brain/topics':
            self._handle_get_brain_topics()
            return
        if path == '/api/brain/knowledge':
            self._handle_get_brain_knowledge()
            return

        # Memory API v2
        if path == '/api/memory/archived':
            self._handle_get_archived_memories()
            return
        if path == '/api/memory/search':
            self._handle_search_memory()
            return
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 1:
                self._handle_get_memory(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'core-candidates':
                self._handle_get_core_candidates(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'merge-history':
                self._handle_get_merge_history(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'conflicts':
                self._handle_get_conflicts(parts[0])
                return
            # FIXME: 记忆三级沉淀查询路由
            if len(parts) == 2 and parts[1] == 'daily-summary':
                self._handle_get_daily_summary(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'project-summary':
                self._handle_get_project_summary(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'knowledge':
                self._handle_get_agent_knowledge_base(parts[0])
                return

        # Permissions API
        if path == '/api/permissions':
            self._handle_get_permissions()
            return
        if path == '/api/permissions/modules':
            self._handle_get_permission_modules()
            return

        # Settings API
        if path == '/api/settings':
            self._handle_get_settings()
            return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path == '/api/knowledge/entries':
            self._handle_get_kb_entries()
            return
        if path.startswith('/api/knowledge/entries/'):
            sub = path[len('/api/knowledge/entries/'):]
            if sub:
                self._handle_get_kb_entry_detail(sub)
                return
        if path == '/api/knowledge/categories':
            self._handle_get_kb_categories()
            return
        if path == '/api/knowledge/stats':
            self._handle_get_kb_stats()
            return

        # Knowledge API
        if path == '/api/knowledge':
            self._handle_get_knowledge()
            return
        if path == '/api/knowledge/search':
            self._handle_get_knowledge_search()
            return
        if path.startswith('/api/knowledge/'):
            sub = path[len('/api/knowledge/'):]
            parts = sub.split('/')
            if len(parts) == 1:
                self._handle_get_knowledge_detail(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'versions':
                self._handle_get_knowledge_versions(parts[0])
                return
            if len(parts) == 3 and parts[1] == 'versions':
                self._handle_get_knowledge_version(parts[0], parts[2])
                return

        # Stats API
        if path == '/api/stats/compute':
            self._handle_get_stats_compute()
            return

        # Brand API
        if path == '/api/brands' or path == '/api/brands/':
            self._handle_get_brands()
            return
        if path.startswith('/api/brands/'):
            brand_id = path[len('/api/brands/'):]
            if brand_id:
                self._handle_get_brand(brand_id)
            else:
                self._handle_get_brands()
            return

        # Talent API
        if path == '/api/talents':
            self._handle_get_talents()
            return
        if path.startswith('/api/talents/'):
            rest = path[len('/api/talents/'):]
            if rest:
                if '/' in rest:
                    parts = rest.split('/')
                    talent_id = parts[0]
                    if parts[1] == 'products':
                        self._handle_get_talent_products(talent_id)
                        return
                    if parts[1] == 'follow-ups' and len(parts) == 2:
                        self._handle_get_talent_follow_ups(talent_id)
                        return
                self._handle_get_talent(rest)
            return

        # Product API
        if path == '/api/products':
            self._handle_get_products()
            return
        if path == '/api/products/search':
            self._handle_search_products()
            return
        if path.startswith('/api/products/'):
            rest = path[len('/api/products/'):]
            if rest:
                # 处理 /api/products/:id/matches
                if '/' in rest:
                    parts = rest.split('/')
                    product_id = parts[0]
                    if parts[1] == 'matches':
                        self._handle_get_product_matches(product_id)
                        return
                    if parts[1] == 'talents':
                        self._handle_get_product_talents(product_id)
                        return
                self._handle_get_product(rest)
                return

        # Influencer API (legacy JSON)
        if path == '/api/influencers':
            self._handle_get_influencers()
            return
        if path == '/api/influencers/search':
            self._handle_search_influencers()
            return
        if path.startswith('/api/influencers/'):
            rest = path[len('/api/influencers/'):]
            if rest:
                # 处理 /api/influencers/:id/matches
                if '/' in rest:
                    parts = rest.split('/')
                    inf_id = parts[0]
                    if parts[1] == 'matches':
                        self._handle_get_influencer_matches(inf_id)
                        return
                self._handle_get_influencer(rest)
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

        # Global Search API
        if path == '/api/search':
            self._handle_global_search()
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
            # /api/groups/:id/memory
            if sub.endswith('/memory'):
                group_id = sub[:-len('/memory')]
                if group_id:
                    self._handle_get_group_memory(group_id)
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
        path = self._normalize_path(self.path)

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

        # 抖音视频解析 (requires auth)
        if path == '/api/douyin/parse':
            self._handle_douyin_parse()
            return

        # 抖音视频语音转文字 (requires auth)
        if path == '/api/douyin/transcribe':
            self._handle_douyin_transcribe()
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
        if path == '/api/openclaw/dreaming':
            self._handle_post_dreaming()
            return

        # RAG API
        if path == '/api/rag/retrieve':
            self._handle_post_rag_retrieve()
            return
        if path == '/api/rag/build':
            self._handle_post_rag_build()
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
            # /api/groups/:id/history
            if sub.endswith('/history'):
                group_id = sub[:-len('/history')]
                if group_id:
                    self._handle_post_group_history(group_id)
                    return
            # /api/groups/:id/memory
            if sub.endswith('/memory'):
                group_id = sub[:-len('/memory')]
                if group_id:
                    self._handle_post_group_memory(group_id)
                    return
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
            # /api/groups/:groupId/memory/:memId/promote
            gmem_parts = sub.split('/')
            if len(gmem_parts) == 4 and gmem_parts[1] == 'memory' and gmem_parts[3] == 'promote':
                self._handle_promote_group_memory(gmem_parts[0], gmem_parts[2])
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

        # FIXME: 大脑知识中枢 API
        if path == '/api/brain/trigger-manual':
            self._handle_brain_trigger_manual()
            return
        if path.startswith('/api/brain/knowledge/') and path.endswith('/feedback'):
            sub = path[len('/api/brain/knowledge/'):-len('/feedback')]
            if sub and '/' not in sub:
                self._handle_brain_knowledge_feedback(sub)
                return

        # Memory API v2
        if path == '/api/memory/consolidate':
            self._handle_consolidate_memory()
            return
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 1:
                self._handle_post_memory(parts[0])
                return
            elif len(parts) == 2 and parts[1] == 'archive':
                self._handle_archive_memory_cleanup(parts[0])
                return
            elif len(parts) == 3 and parts[2] == 'promote':
                self._handle_promote_memory(parts[0], parts[1])
                return
            elif len(parts) == 3 and parts[2] == 'restore':
                self._handle_restore_memory(parts[0], parts[1])
                return
            elif len(parts) == 2 and parts[1] == 'induct-to-knowledge':
                self._handle_induct_to_knowledge(parts[0])
                return
            elif len(parts) == 2 and parts[1] == 'archive-inducted':
                self._handle_archive_inducted(parts[0])
                return
            elif len(parts) == 4 and parts[1] == 'core-candidates' and parts[3] == 'confirm':
                self._handle_confirm_core_candidate(parts[0], parts[2])
                return
            elif len(parts) == 4 and parts[1] == 'core-candidates' and parts[3] == 'dismiss':
                self._handle_dismiss_core_candidate(parts[0], parts[2])
                return
            elif len(parts) == 2 and parts[1] == 'detect-conflicts':
                self._handle_detect_conflicts(parts[0])
                return
            elif len(parts) == 4 and parts[2] == 'resolve-conflict':
                self._handle_resolve_conflict(parts[0], parts[1])
                return
            # FIXME: 记忆三级沉淀写入路由
            elif len(parts) == 2 and parts[1] == 'trigger-summary':
                self._handle_trigger_summary(parts[0])
                return
            elif len(parts) == 2 and parts[1] == 'knowledge':
                self._handle_post_agent_knowledge_base(parts[0])
                return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path == '/api/knowledge/entries':
            self._handle_post_kb_entry()
            return
        if path == '/api/knowledge/search':
            self._handle_post_kb_search()
            return

        # Knowledge API
        if path == '/api/knowledge':
            self._handle_post_knowledge()
            return
        if path.startswith('/api/knowledge/'):
            sub = path[len('/api/knowledge/'):]
            parts = sub.split('/')
            if len(parts) == 2 and parts[1] == 'rollback':
                self._handle_knowledge_rollback(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'move':
                self._handle_knowledge_move(parts[0])
                return

        # Brand API
        if path == '/api/brands':
            self._handle_post_brand()
            return

        # Talent API
        if path == '/api/talents':
            self._handle_post_talent()
            return
        if path.startswith('/api/talents/'):
            sub = path[len('/api/talents/'):]
            parts = sub.split('/')
            if len(parts) == 2 and parts[1] == 'analyze':
                self._handle_analyze_talent_ai(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'match-products':
                self._handle_match_talent_products(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'follow-ups':
                self._handle_post_talent_follow_up(parts[0])
                return

        # Product API
        if path == '/api/products':
            self._handle_post_product()
            return
        if path == '/api/products/search':
            self._handle_search_products()
            return
        if path.startswith('/api/products/'):
            sub = path[len('/api/products/'):]
            parts = sub.split('/')
            if len(parts) == 2 and parts[1] == 'analyze':
                self._handle_analyze_product_ai(parts[0])
                return
            if len(parts) == 2 and parts[1] == 'match-talents':
                self._handle_match_product_talents(parts[0])
                return

        # Influencer API (legacy JSON)
        if path == '/api/influencers':
            self._handle_post_influencer()
            return
        if path == '/api/influencers/search':
            self._handle_search_influencers()
            return

        # Match API
        if path == '/api/match/product-to-influencer':
            self._handle_match_product_to_influencer()
            return
        if path == '/api/match/influencer-to-product':
            self._handle_match_influencer_to_product()
            return
        if path == '/api/ai-match':
            self._handle_ai_match()
            return

        # Chat API
        if path.startswith('/api/chat/'):
            sub = path[len('/api/chat/'):]
            print(f'  [ChatPOST] 路由匹配: path={path} sub={sub}', flush=True)
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
        path = self._normalize_path(self.path)

        # Groups API
        if path == '/api/groups':
            self._handle_batch_save_groups()
            return
        if path.startswith('/api/groups/'):
            sub = path[len('/api/groups/'):]
            # /api/groups/:groupId/memory/:memId
            if '/memory/' in sub:
                parts = sub.split('/memory/')
                if len(parts) == 2 and parts[0] and parts[1]:
                    self._handle_update_group_memory(parts[0], parts[1])
                    return
            group_id = sub
            if group_id:
                self._handle_update_group(group_id)
                return

        # Agents API
        if path.startswith('/api/agents/'):
            sub = path[len('/api/agents/'):]
            if sub.endswith('/self-update'):
                agent_id = sub[:-len('/self-update')]
                if agent_id:
                    self._handle_agent_self_update(agent_id)
                    return
            if sub:
                self._handle_update_agent(sub)
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

        # Memory API v2
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 2:
                self._handle_update_memory(parts[0], parts[1])
                return

        # Permissions API
        if path.startswith('/api/permissions/roles/'):
            role_id = path[len('/api/permissions/roles/'):]
            if role_id:
                self._handle_update_role_permissions(role_id)
                return
        if path.startswith('/api/permissions/users/'):
            user_id = path[len('/api/permissions/users/'):]
            if user_id:
                self._handle_update_user_permissions(user_id)
                return

        # Settings API
        if path == '/api/settings':
            self._handle_put_settings()
            return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path.startswith('/api/knowledge/entries/'):
            entry_id = path[len('/api/knowledge/entries/'):]
            if entry_id:
                self._handle_put_kb_entry(entry_id)
                return

        # Knowledge API
        if path.startswith('/api/knowledge/'):
            doc_id = path[len('/api/knowledge/'):]
            if doc_id:
                self._handle_put_knowledge(doc_id)
                return

        # Brand API
        if path.startswith('/api/brands/'):
            brand_id = path[len('/api/brands/'):]
            if brand_id:
                self._handle_put_brand(brand_id)
                return

        # Talent API
        if path.startswith('/api/talents/'):
            sub = path[len('/api/talents/'):]
            if sub:
                if '/' in sub:
                    parts = sub.split('/')
                    if len(parts) == 3 and parts[1] == 'follow-ups':
                        self._handle_put_talent_follow_up(parts[0], parts[2])
                        return
                self._handle_put_talent(sub)
                return

        # Product API
        if path.startswith('/api/products/'):
            product_id = path[len('/api/products/'):]
            if product_id:
                self._handle_put_product(product_id)
                return

        # Influencer API (legacy JSON)
        if path.startswith('/api/influencers/'):
            inf_id = path[len('/api/influencers/'):]
            if inf_id:
                self._handle_put_influencer(inf_id)
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
        path = self._normalize_path(self.path)

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
            if len(parts) == 3 and parts[1] == 'memory':
                # /api/groups/:groupId/memory/:memId
                self._handle_delete_group_memory(parts[0], parts[2])
                return
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

        # Memory API v2
        if path.startswith('/api/memory/'):
            sub = path[len('/api/memory/'):]
            parts = sub.split('/')
            if len(parts) == 2:
                self._handle_delete_memory(parts[0], parts[1])
                return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path.startswith('/api/knowledge/entries/'):
            entry_id = path[len('/api/knowledge/entries/'):]
            if entry_id:
                self._handle_delete_kb_entry(entry_id)
                return

        # Knowledge API
        if path.startswith('/api/knowledge/'):
            doc_id = path[len('/api/knowledge/'):]
            if doc_id:
                self._handle_delete_knowledge(doc_id)
                return

        # Brand API
        if path.startswith('/api/brands/'):
            brand_id = path[len('/api/brands/'):]
            if brand_id:
                self._handle_delete_brand(brand_id)
                return

        # Talent API
        if path.startswith('/api/talents/'):
            sub = path[len('/api/talents/'):]
            if sub:
                if '/' in sub:
                    parts = sub.split('/')
                    if len(parts) == 3 and parts[1] == 'follow-ups':
                        self._handle_delete_talent_follow_up(parts[0], parts[2])
                        return
                self._handle_delete_talent(sub)
                return

        # Product API
        if path.startswith('/api/products/'):
            product_id = path[len('/api/products/'):]
            if product_id:
                self._handle_delete_product(product_id)
                return

        # Influencer API (legacy JSON)
        if path.startswith('/api/influencers/'):
            inf_id = path[len('/api/influencers/'):]
            if inf_id:
                self._handle_delete_influencer(inf_id)
                return

        # Chat API
        if path.startswith('/api/chat/'):
            # /api/chat/:agentId/:msgId
            parts = path[len('/api/chat/'):].split('/')
            if len(parts) == 2:
                _handle_delete_chat_message(self, parts[0], parts[1])
                return
            # /api/chat/:agentId (clear all)
            if len(parts) == 1:
                _handle_clear_chat(self, parts[0])
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
                'avatar': user.get('avatar', 0),
                'agentQuota': user.get('agentQuota', 10),
                'apiQuota': user.get('apiQuota', 1000),
                'teamIds': user.get('teamIds', []),
                'subordinateIds': user.get('subordinateIds', []),
                'roleTemplateId': user.get('roleTemplateId'),
                'permissions': _get_effective_permissions({'id': user['id'], 'role': user.get('role', 'employee'), 'roleTemplateId': user.get('roleTemplateId')})
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
            'apiQuota': user.get('apiQuota', 1000),
            'permissions': _get_effective_permissions(auth),
            'roleTemplateId': user.get('roleTemplateId')
        })

    def _handle_get_permissions(self):
        """GET /api/permissions — 获取完整权限配置（仅 admin）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated or not auth.is_admin:
            self._send_auth_error('Permission denied', 403)
            return
        perms = _load_permissions()
        perms['modules'] = AVAILABLE_MODULES
        self._send_json(200, perms)

    def _handle_get_permission_modules(self):
        """GET /api/permissions/modules — 返回可用模块列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        self._send_json(200, {'modules': AVAILABLE_MODULES})

    def _handle_get_settings(self):
        """GET /api/settings — 读取全局设置（含 embedding 配置）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'settings'):
            return
        settings = _read_json(SETTINGS_FILE, {})
        # 统一返回 embedding 嵌套结构（兼容旧平铺字段）
        emb = settings.get('embedding', {}) or {}
        if not emb.get('provider') and settings.get('embeddingProvider'):
            emb['provider'] = settings['embeddingProvider']
        if not emb.get('apiKey') and settings.get('embeddingApiKey'):
            emb['apiKey'] = settings['embeddingApiKey']
        settings['embedding'] = emb
        self._send_json(200, settings)

    def _handle_put_settings(self):
        """PUT /api/settings — 更新全局设置（含 embedding 配置）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'settings'):
            return
        body = self._read_body()
        if not body or not isinstance(body, dict):
            self._send_json_error(400, 'Invalid body')
            return
        settings = _read_json(SETTINGS_FILE, {})

        # 仅允许更新白名单内的顶层字段，避免污染
        allowed_top_keys = {'embedding', 'knowledgeMockMode', 'embeddingProvider', 'embeddingApiKey'}
        for key in allowed_top_keys:
            if key in body:
                settings[key] = body[key]

        # 同步兼容：embedding 嵌套结构与旧平铺字段保持一致
        emb = settings.get('embedding', {}) or {}
        if 'embeddingProvider' in body:
            emb['provider'] = body['embeddingProvider']
        if 'embeddingApiKey' in body:
            emb['apiKey'] = body['embeddingApiKey']
        if 'embedding' in body:
            if body['embedding']:
                settings['embeddingProvider'] = body['embedding'].get('provider', '')
                settings['embeddingApiKey'] = body['embedding'].get('apiKey', '')
            else:
                settings.pop('embeddingProvider', None)
                settings.pop('embeddingApiKey', None)
        settings['embedding'] = emb

        _write_json(SETTINGS_FILE, settings)
        self._send_json(200, settings)

    def _handle_update_role_permissions(self, role_id):
        """PUT /api/permissions/roles/{roleId}"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated or not auth.is_admin:
            self._send_auth_error('Permission denied', 403)
            return
        body = self._read_body()
        if not body or not isinstance(body, dict):
            self._send_json_error(400, 'Invalid body')
            return
        perms = _load_permissions()
        template = None
        for tmpl in perms.get('roleTemplates', []):
            if tmpl.get('id') == role_id:
                template = tmpl
                break
        if not template:
            self._send_json_error(404, 'Role template not found')
            return
        if 'modules' in body and isinstance(body['modules'], dict):
            merged = {m: bool(template.get('modules', {}).get(m, False)) for m in AVAILABLE_MODULES}
            for m, v in body['modules'].items():
                if m in AVAILABLE_MODULES:
                    merged[m] = bool(v)
            template['modules'] = merged
        if 'knowledgeCategories' in body and isinstance(body['knowledgeCategories'], list):
            template['knowledgeCategories'] = [str(c) for c in body['knowledgeCategories']]
        _save_permissions(perms)
        self._send_json(200, {'success': True, 'roleTemplate': template})

    def _handle_update_user_permissions(self, user_id):
        """PUT /api/permissions/users/{userId} — 更新用户权限覆盖；body 为空对象则删除覆盖"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated or not auth.is_admin:
            self._send_auth_error('Permission denied', 403)
            return
        body = self._read_body()
        if body is None:
            self._send_json_error(400, 'Invalid body')
            return
        perms = _load_permissions()
        overrides = perms.setdefault('userOverrides', {})
        if not isinstance(body, dict) or (not body.get('modules') and not body.get('knowledgeCategories')):
            # 删除覆盖
            if user_id in overrides:
                del overrides[user_id]
            _save_permissions(perms)
            self._send_json(200, {'success': True, 'userOverride': None})
            return
        override = overrides.setdefault(user_id, {})
        if 'modules' in body and isinstance(body['modules'], dict):
            override['modules'] = {m: bool(v) for m, v in body['modules'].items() if m in AVAILABLE_MODULES}
        if 'knowledgeCategories' in body and isinstance(body['knowledgeCategories'], list):
            override['knowledgeCategories'] = [str(c) for c in body['knowledgeCategories']]
        _save_permissions(perms)
        self._send_json(200, {'success': True, 'userOverride': override})

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        # 管理员和创建者直接放行
        if auth.is_admin or group.get('createdBy') == auth.user_info.get('userId'):
            return group, None, None
        # 其他人：检查其 AI 员工是否在群组成员中
        # 兼容 members 的两种格式：字符串数组 和 字典数组
        member_ids = set()
        for m in group.get('members', []):
            if isinstance(m, dict):
                member_ids.add(m.get('id'))
            elif isinstance(m, str):
                member_ids.add(m)
        # 加载当前用户的所有 agent，检查是否有交集
        agents = _load_agents()
        my_agent_ids = {a.get('id') for a in agents if a.get('createdBy') == auth.user_info.get('userId')}
        if member_ids & my_agent_ids:
            return group, None, None
        return None, '权限不足', 403

    def _handle_get_groups(self):
        """GET /api/groups — 获取所有群组，members 附带基础信息（name/avatar/bg/role）"""
        try:
            auth = _authenticate(self.headers)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return
            if not self._require_module_permission(auth, 'groups'): return

            groups = _load_groups()
            agents = _load_agents()
            agent_map = {a.get('id'): a for a in agents}

            # 管理员看全部，普通用户看：自己创建的 + 包含自己AI员工的
            if not auth.is_admin:
                uid = auth.user_info['userId']
                my_agent_ids = {a.get('id') for a in agents if a.get('createdBy') == uid}
                result = []
                for g in groups:
                    if g.get('createdBy') == uid:
                        result.append(g)
                        continue
                    # 兼容 members 的两种格式：字符串数组 和 字典数组
                    members = g.get('members', [])
                    group_member_ids = set()
                    for m in members:
                        if isinstance(m, dict):
                            group_member_ids.add(m.get('id'))
                        elif isinstance(m, str):
                            group_member_ids.add(m)
                    if group_member_ids & my_agent_ids:
                        result.append(g)
            else:
                result = groups

            # 为每个 group 的 members 补充基础信息（name/avatar/bg/role），
            # 让前端即使 emps 查不到也能显示正确名字和头像
            for g in result:
                members = g.get('members', [])
                enriched = []
                for m in members:
                    mid = m.get('id') if isinstance(m, dict) else m
                    agent = agent_map.get(mid)
                    if agent:
                        enriched.append({
                            'id': mid,
                            'name': agent.get('name', ''),
                            'avatar': agent.get('avatar', '🦞'),
                            'bg': agent.get('bg', '#FF6B35'),
                            'role': agent.get('role', ''),
                            'createdBy': agent.get('createdBy', ''),
                            'openclawName': agent.get('openclawName', ''),
                        })
                    elif isinstance(m, dict):
                        enriched.append(m)
                    else:
                        enriched.append({'id': m})
                g['members'] = enriched

            self._send_json(200, result)
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
        if not self._require_module_permission(auth, 'groups'): return

        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return

        # 补充 members 基础信息
        agents = _load_agents()
        agent_map = {a.get('id'): a for a in agents}
        members = group.get('members', [])
        enriched = []
        for m in members:
            mid = m.get('id') if isinstance(m, dict) else m
            agent = agent_map.get(mid)
            if agent:
                enriched.append({
                    'id': mid,
                    'name': agent.get('name', ''),
                    'avatar': agent.get('avatar', '🦞'),
                    'bg': agent.get('bg', '#FF6B35'),
                    'role': agent.get('role', ''),
                    'createdBy': agent.get('createdBy', ''),
                    'openclawName': agent.get('openclawName', ''),
                })
            elif isinstance(m, dict):
                enriched.append(m)
            else:
                enriched.append({'id': m})
        group['members'] = enriched

        self._send_json(200, group)

    def _handle_create_group(self):
        """POST /api/groups — 创建群组"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return

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

        groups = _load_groups()

        # 幂等：前端若已提供 id 且已存在，则返回已有群组，避免重复创建
        provided_id = body.get('id', '').strip()
        if provided_id:
            existing = _find_group(groups, 'id', provided_id)
            if existing:
                self._send_json(200, existing)
                return

        new_group = {
            'id': provided_id or 'grp_' + uuid.uuid4().hex[:10],
            'name': name,
            'avatar': body.get('avatar', '👥'),
            'members': valid_members,
            'leadAgentId': lead_agent_id,
            'description': body.get('description', ''),
            'createdBy': auth.user_info['userId'],
            'createdAt': datetime.now().isoformat()
        }

        groups.append(new_group)
        _save_groups(groups)

        self._send_json(201, new_group)

    def _handle_update_group(self, group_id):
        """PUT /api/groups/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return

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

        # 统一转换 members 格式：字符串数组 -> 字典数组，避免后续读取异常
        for g in body:
            members = g.get('members', [])
            if isinstance(members, list):
                valid_members = []
                for m in members:
                    if isinstance(m, dict) and m.get('id'):
                        valid_members.append({'id': m['id'], 'role': m.get('role', '')})
                    elif isinstance(m, str):
                        valid_members.append({'id': m, 'role': ''})
                g['members'] = valid_members

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
        if not self._require_module_permission(auth, 'groups'): return

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

    def _handle_global_search(self):
        """GET /api/search?q=xxx&scope=all|employees|groups|knowledge&limit=8"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        q = (qs.get('q', [''])[0] or '').strip().lower()
        scope = qs.get('scope', ['all'])[0] or 'all'
        try:
            limit = max(1, min(20, int(qs.get('limit', ['8'])[0])))
        except Exception:
            limit = 8

        if not q:
            self._send_json(200, {'q': '', 'scope': scope, 'groups': {}})
            return

        allowed_scopes = {'all', 'employees', 'groups', 'knowledge'}
        if scope not in allowed_scopes:
            scope = 'all'

        result_groups = {}

        def _match(text):
            if not isinstance(text, str):
                return False
            return q in text.lower()

        # AI员工
        if scope in ('all', 'employees') and _has_module_permission(auth, 'employees'):
            agents = _load_agents()
            matched = []
            for a in agents:
                texts = [
                    a.get('name', ''),
                    a.get('role', ''),
                    a.get('department', ''),
                    a.get('systemPrompt', ''),
                    a.get('msg', ''),
                ]
                if any(_match(t) for t in texts):
                    matched.append({
                        'id': a.get('id'),
                        'name': a.get('name', ''),
                        'role': a.get('role', ''),
                        'avatar': a.get('avatar'),
                        'bg': a.get('bg', '#FF6B35'),
                    })
                if len(matched) >= limit:
                    break
            if matched:
                result_groups['employees'] = matched

        # 项目组
        if scope in ('all', 'groups') and _has_module_permission(auth, 'groups'):
            groups = _load_groups()
            matched = []
            for g in groups:
                texts = [
                    g.get('name', ''),
                    g.get('description', ''),
                ]
                if any(_match(t) for t in texts):
                    members = g.get('members', []) or []
                    matched.append({
                        'id': g.get('id'),
                        'name': g.get('name', ''),
                        'avatar': g.get('avatar', '👥'),
                        'memberCount': len(members),
                    })
                if len(matched) >= limit:
                    break
            if matched:
                result_groups['groups'] = matched

        # 知识库
        if scope in ('all', 'knowledge') and _has_module_permission(auth, 'knowledge'):
            try:
                allowed_cats = _allowed_knowledge_categories(auth)
                res = ks.knowledge_list(
                    offset=0, limit=limit, keyword=q,
                    allowed_categories=allowed_cats,
                    user_id=auth.user_id,
                    is_admin=auth.is_admin,
                    user_team_ids=auth.team_ids,
                    user_group_ids=auth.group_ids
                )
                docs = res.get('docs', []) or []
                matched = []
                for d in docs:
                    content = d.get('content', '') or ''
                    preview = _re.sub(r'[#*`\[\]()!>-]', ' ', content)
                    preview = _re.sub(r'\s+', ' ', preview).strip()[:80]
                    matched.append({
                        'id': d.get('id'),
                        'title': d.get('title', ''),
                        'category': d.get('category', ''),
                        'preview': preview,
                    })
                if matched:
                    result_groups['knowledge'] = matched
            except Exception as e:
                print(f'  [ERROR] global search knowledge: {e}', flush=True)

        self._send_json(200, {'q': q, 'scope': scope, 'groups': result_groups})

    def _handle_add_group_member(self, group_id):
        """POST /api/groups/:id/members — 添加成员"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return

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

        # 先统一转换现有 members 格式（兼容历史数据中的字符串数组）
        raw_members = group.get('members', [])
        normalized_members = []
        for m in raw_members:
            if isinstance(m, dict) and m.get('id'):
                normalized_members.append({'id': m['id'], 'role': m.get('role', '')})
            elif isinstance(m, str):
                normalized_members.append({'id': m, 'role': ''})
        group['members'] = normalized_members

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
        if not self._require_module_permission(auth, 'groups'): return

        groups = _load_groups()
        group = _find_group(groups, 'id', group_id)
        if not group:
            self._send_json(404, {'error': '群组不存在'})
            return

        if not auth.is_admin and group.get('createdBy') != auth.user_info['userId']:
            self._send_auth_error('权限不足', 403)
            return

        # 先统一转换现有 members 格式（兼容历史数据中的字符串数组）
        raw_members = group.get('members', [])
        normalized_members = []
        for m in raw_members:
            if isinstance(m, dict) and m.get('id'):
                normalized_members.append({'id': m['id'], 'role': m.get('role', '')})
            elif isinstance(m, str):
                normalized_members.append({'id': m, 'role': ''})
        group['members'] = normalized_members

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
        if not self._require_module_permission(auth, 'settings'): return

        teams = _load_teams()
        users = _load_users()
        agents = _load_agents()

        result = []
        for t in teams:
            # 所有已认证用户均可查看团队列表（团队是组织架构分类，读取不敏感）
            # 写入权限（创建/修改/删除）仍按角色严格控制
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
                'agentIds': t.get('agentIds', []),
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
        if not self._require_module_permission(auth, 'settings'): return

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
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        """DELETE /api/teams/:id — 删除小组（admin 或小组负责人）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'settings'): return

        teams = _load_teams()
        team = _find_team(teams, 'id', team_id)
        if not team:
            self._send_json(404, {'error': '小组不存在'})
            return

        # 权限检查：admin 可删全部，leader 只能删自己负责的小组
        if not auth.is_admin:
            if not auth.is_leader or team.get('leaderId') != auth.user_info.get('userId'):
                self._send_auth_error('权限不足', 403)
                return

        # 检查是否有子组
        has_children = any(t.get('parentId') == team_id for t in teams)
        if has_children:
            self._send_json(403, {'error': '无法删除有子组的小组，请先删除子组'})
            return

        # 检查是否仍有成员
        members = team.get('members', []) or []
        if members:
            self._send_json(403, {'error': f'小组仍有 {len(members)} 名成员，请先移除成员'})
            return

        # 解除 leader 关联
        users = _load_users()
        leader_id = team.get('leaderId')
        if leader_id:
            u = _find_user(users, 'id', leader_id)
            if u:
                u['teamIds'] = [tid for tid in u.get('teamIds', []) if tid != team_id]

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'settings'): return

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
        if not self._require_module_permission(auth, 'groups'): return

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

    def _handle_post_group_history(self, group_id):
        """POST /api/groups/:id/history — 保存群组聊天消息"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return

        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        chat_key = f'group_{group_id}'
        messages = _load_chat(chat_key)
        if not isinstance(messages, list):
            messages = []

        msg = {
            'id': body.get('id', 'msg_' + str(uuid.uuid4())[:8]),
            'role': body.get('role', 'user'),
            'content': body.get('content', ''),
            'senderId': body.get('senderId', ''),
            'senderName': body.get('senderName', ''),
            'senderType': body.get('senderType', 'user'),
            'groupId': group_id,
            'time': body.get('time', int(time.time() * 1000))
        }
        messages.append(msg)

        # 上限 500 条，超出时归档旧消息到 L3（非静默丢弃）
        archived_count = 0
        if len(messages) > 500:
            old_messages = messages[:-300]  # 保留最近 300 条
            # 归档到 L3（不调用 AI，避免 POST 超时）
            try:
                archive_data = _load_archive(chat_key)
                chat_summary = []
                for om in old_messages:
                    role = '用户' if om.get('role') == 'user' else 'AI'
                    content = (om.get('content', '') or '')[:100]
                    chat_summary.append(f'{role}: {content}')
                archive_data['summaries'].append({
                    'id': 'sum_' + str(uuid.uuid4())[:8],
                    'type': 'chat_overflow',
                    'period': f'{old_messages[0].get("time", 0)} ~ {old_messages[-1].get("time", 0)}',
                    'summary': '\n'.join(chat_summary),
                    'compressedCount': len(old_messages),
                    'createdAt': int(time.time() * 1000)
                })
                _save_archive(chat_key, archive_data)
                archived_count = len(old_messages)
                messages = messages[-300:]
                print(f'  [ChatArchive] {chat_key} 归档 {archived_count} 条溢出消息到 L3', flush=True)
            except Exception as e:
                print(f'  [ChatArchive] {chat_key} 归档失败: {e}，回退到静默截断', flush=True)
                messages = messages[-500:]

        _save_chat(chat_key, messages)

        # 群聊记忆：同步到项目组公共记忆 + 参与 AI 的个人记忆
        sender_id = msg.get('senderId', '')
        sender_type = msg.get('senderType', 'user')
        content = msg.get('content', '')
        if content:
            memory_value = f"【群聊】{msg.get('senderName', 'AI')}说：{content[:500]}"
            # 群聊去重使用全局配置或当前用户 agent key
            chat_emb_cfg = _get_embedding_config_for_user()
            # 1) 项目组公共记忆（原始消息作为日常记录）
            try:
                ms3.add_group_memory(
                    group_id,
                    value=memory_value,
                    key='daily',
                    source='群聊对话',
                    context=content[:500],
                    api_key=chat_emb_cfg['apiKey'],
                    provider=chat_emb_cfg['provider'],
                    model=chat_emb_cfg['model'],
                    base_url=chat_emb_cfg['baseUrl'],
                    sender_id=sender_id if sender_type == 'agent' else None
                )
                print(f'  [GroupMemory] group_{group_id} 群聊消息已保存到项目组公共记忆', flush=True)
            except Exception as e:
                print(f'  [GroupMemory] group_{group_id} 保存项目组公共记忆失败: {e}', flush=True)

            # 2) 发送者 AI 的个人记忆
            if sender_type == 'agent' and sender_id:
                try:
                    sender_cfg = get_embedding_config(sender_id)
                    ms3.add_memory(
                        sender_id,
                        value=memory_value,
                        key='daily',
                        tags=['group_chat'],
                        source='群聊对话',
                        api_key=sender_cfg['apiKey'] or chat_emb_cfg['apiKey'],
                        provider=sender_cfg['provider'] or chat_emb_cfg['provider'],
                        model=sender_cfg['model'] or chat_emb_cfg['model'],
                        base_url=sender_cfg['baseUrl'] or chat_emb_cfg['baseUrl'],
                        sender_id=sender_id
                    )
                    print(f'  [GroupMemory] {sender_id} (AI) 群聊消息已保存到 daily 记忆', flush=True)
                except Exception as e:
                    print(f'  [GroupMemory] {sender_id} 保存群聊记忆失败: {e}', flush=True)

            # 3) 所有参与 AI（含群主）都保存一份群聊上下文，确保任何 AI 被触发时都能拿到完整群聊背景
            member_ids = set()
            for m in group.get('members', []):
                mid = m.get('id') if isinstance(m, dict) else m
                if mid:
                    member_ids.add(mid)
            lead_id = group.get('leadAgentId', '')
            if lead_id:
                member_ids.add(lead_id)
            for mid in member_ids:
                if mid == sender_id and sender_type == 'agent':
                    continue  # 发送者已在上面保存
                try:
                    member_cfg = get_embedding_config(mid)
                    ms3.add_memory(
                        mid,
                        value=memory_value,
                        key='daily',
                        tags=['group_chat', 'context'],
                        source='群聊对话',
                        api_key=member_cfg['apiKey'] or chat_emb_cfg['apiKey'],
                        provider=member_cfg['provider'] or chat_emb_cfg['provider'],
                        model=member_cfg['model'] or chat_emb_cfg['model'],
                        base_url=member_cfg['baseUrl'] or chat_emb_cfg['baseUrl'],
                        sender_id=sender_id
                    )
                    print(f'  [GroupMemory] {mid} 群聊上下文已保存到 daily 记忆', flush=True)
                except Exception as e:
                    print(f'  [GroupMemory] {mid} 保存群聊上下文失败: {e}', flush=True)

        self._send_json(200, {'saved': True, 'id': msg['id'], 'archived': archived_count})

    # ═══════════════════════════════════════════════════
    # 项目组记忆 API
    # ═══════════════════════════════════════════════════

    def _handle_get_group_memory(self, group_id):
        """GET /api/groups/:groupId/memory — 获取项目组公共记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        type_filter = qs.get('type', qs.get('pool', ['']))[0]
        keyword = qs.get('keyword', [''])[0].lower()
        include_archived = qs.get('include_archived', ['false'])[0].lower() in ('true', '1', 'yes')
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int(qs.get('offset', ['0'])[0]))
        except ValueError:
            offset = 0

        data = ms3.load_group_memory(group_id)
        archive_data = ms3.load_group_archive(group_id) if include_archived else {'archived': []}

        def _map_mem(m):
            r = dict(m)
            if 'createdAt' in r:
                r['time'] = r.pop('createdAt')
            if 'updatedAt' in r:
                r.pop('updatedAt', None)
            if 'expiresAt' in r:
                r.pop('expiresAt', None)
            if 'context' in r:
                r.pop('context', None)
            if 'accessCount' in r:
                r.pop('accessCount', None)
            return r

        def _map_arch(m):
            r = dict(m)
            if 'createdAt' in r:
                r['time'] = r.pop('createdAt')
            if 'archivedAt' in r:
                r['archivedTime'] = r.pop('archivedAt')
            if 'originalKey' in r:
                r.pop('originalKey', None)
            return r

        def _matches(m):
            if keyword:
                value = (m.get('value') or '').lower()
                if keyword not in value:
                    return False
            return True

        def _apply_filters(items):
            filtered = [m for m in items if _matches(m)]
            return filtered[offset:offset + limit]

        include_core = type_filter in ('', 'core', 'active')
        include_daily = type_filter in ('', 'daily', 'active')
        include_archive = type_filter in ('', 'archive')

        core_list, daily_list, archive_list = [], [], []
        if include_core:
            core_list = [_map_mem(m) for m in _apply_filters(data.get('core', []))]
        if include_daily:
            daily_list = [_map_mem(m) for m in _apply_filters(data.get('daily', []))]
        if include_archive:
            archive_list = [_map_arch(m) for m in _apply_filters(archive_data.get('archived', []))]

        all_memories = []
        for m in core_list:
            m['pool'] = 'core'
            all_memories.append(m)
        for m in daily_list:
            m['pool'] = 'daily'
            all_memories.append(m)
        for m in archive_list:
            m['pool'] = 'archive'
            all_memories.append(m)

        self._send_json(200, {
            'memories': all_memories,
            'total': len(all_memories),
            'limit': limit,
            'offset': offset,
            'core': core_list,
            'daily': daily_list,
            'archive': archive_list,
            'version': '3.0'
        })

    def _handle_post_group_memory(self, group_id):
        """POST /api/groups/:groupId/memory — 添加项目组公共记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return
        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return
        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return
        value = (body.get('value') or '').strip()
        if not value:
            self._send_json(400, {'error': '记忆内容不能为空'})
            return
        key = body.get('type') or body.get('key', 'auto')

        # 去重需要调用 Embedding API，使用全局配置或当前用户任意一个 agent 的 key
        emb_cfg = _get_embedding_config_for_user()

        try:
            memory = ms3.add_group_memory(
                group_id, value,
                key=key,
                source=body.get('source', 'user_input'),
                context=body.get('context', ''),
                api_key=emb_cfg['apiKey'],
                provider=emb_cfg['provider'],
                model=emb_cfg['model'],
                base_url=emb_cfg['baseUrl']
            )
            self._send_json(200, {
                'id': memory['id'],
                'key': memory['key'],
                'pool': 'daily' if key in ('auto', 'auto_extract', 'daily') else 'core',
                'value': memory['value'],
                'createdAt': memory['createdAt']
            })
        except ValueError as e:
            self._send_json(400, {'error': str(e)})
        except RuntimeError as e:
            self._send_json(409, {'error': str(e)})
        except Exception as e:
            self._send_json(500, {'error': str(e)})

    def _handle_update_group_memory(self, group_id, mem_id):
        """PUT /api/groups/:groupId/memory/:memId — 修改项目组记忆"""
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

        updates = {}
        if 'value' in body:
            updates['value'] = body['value']
        if 'source' in body:
            updates['source'] = body['source']
        if 'key' in body:
            updates['key'] = body['key']
        if 'priority' in body:
            updates['priority'] = body['priority']
        if 'tags' in body:
            updates['tags'] = body['tags']
        if 'context' in body:
            updates['context'] = body['context']

        # 去重需要 Embedding API，使用全局配置或当前用户任意一个 agent 的 key
        emb_cfg = _get_embedding_config_for_user()

        try:
            updated = ms3.update_group_memory(
                group_id, mem_id, updates,
                api_key=emb_cfg['apiKey'],
                provider=emb_cfg['provider'],
                model=emb_cfg['model'],
                base_url=emb_cfg['baseUrl']
            )
        except RuntimeError as e:
            self._send_json(409, {'error': str(e)})
            return

        if not updated:
            self._send_json(404, {'error': '记忆不存在'})
            return
        self._send_json(200, {'success': True, 'id': updated.get('id', mem_id)})

    def _handle_delete_group_memory(self, group_id, mem_id):
        """DELETE /api/groups/:groupId/memory/:memId — 删除项目组记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return
        data = ms3.load_group_memory(group_id)
        removed = False
        for pool in ('core', 'daily'):
            original = len(data.get(pool, []))
            data[pool] = [m for m in data.get(pool, []) if m.get('id') != mem_id]
            if len(data[pool]) < original:
                removed = True
        if removed:
            ms3.save_group_memory(group_id, data)
        else:
            archive_data = ms3.load_group_archive(group_id)
            original = len(archive_data.get('archived', []))
            archive_data['archived'] = [m for m in archive_data.get('archived', []) if m.get('id') != mem_id]
            if len(archive_data['archived']) < original:
                ms3.save_group_archive(group_id, archive_data)
                removed = True
        if removed:
            self._send_json(200, {'success': True})
        else:
            self._send_json(404, {'error': '记忆不存在'})

    def _handle_promote_group_memory(self, group_id, mem_id):
        """POST /api/groups/:groupId/memory/:memId/promote — 升级为项目组核心记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'groups'): return
        group, err, status = self._check_group_access(auth, group_id)
        if err:
            self._send_json(status, {'error': err})
            return
        data = ms3.load_group_memory(group_id)
        mem = None
        for m in data.get('daily', []):
            if m.get('id') == mem_id:
                mem = m
                break
        if not mem:
            self._send_json(404, {'error': '日常记录不存在'})
            return
        cfg = ms3.MEMORY_V3_CONFIG
        if len(data.get('core', [])) >= cfg['core_max']:
            self._send_json(409, {'error': f'Core pool full ({cfg["core_max"]})'})
            return
        data['daily'] = [m for m in data['daily'] if m.get('id') != mem_id]
        mem['key'] = 'core'
        mem['priority'] = 5
        mem['tags'] = []
        mem['updatedAt'] = int(time.time() * 1000)
        mem['accessCount'] = mem.get('accessCount', 0)
        mem.pop('context', None)
        mem.pop('expiresAt', None)
        data['core'].append(mem)
        ms3.save_group_memory(group_id, data)
        self._send_json(200, {'success': True, 'id': mem_id})

    # ═══════════════════════════════════════════════════
    # Agent API
    # ═══════════════════════════════════════════════════

    def _handle_get_agents(self):
        """GET /api/agents — 只返回当前用户创建的 agents（严格权限）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'employees'): return

        agents = _load_agents()
        uid = auth.user_info['userId']

        # 调试日志：打印 uid 和所有 agent 的 createdBy，排查过滤问题
        print(f'  [DEBUG get_agents] uid={uid} role={auth.user_info.get("role")} is_admin={auth.is_admin} is_leader={auth.is_leader}')
        for a in agents:
            print(f'  [DEBUG get_agents] agent id={a.get("id")} name={a.get("name")} createdBy={repr(a.get("createdBy"))}')

        if auth.is_admin:
            result = agents
        elif auth.is_leader:
            accessible_ids = _get_accessible_agent_ids(auth)
            result = [a for a in agents
                      if a.get('id') in accessible_ids or a.get('createdBy') == uid]
        else:
            # employee: 严格只返回自己创建的 agents，侧边栏不显示其他人的 AI
            result = [a for a in agents if a.get('createdBy') == uid]

        print(f'  [DEBUG get_agents] 过滤后返回 {len(result)} 个 agents')
        for a in result:
            print(f'  [DEBUG get_agents] -> result id={a.get("id")} name={a.get("name")} createdBy={repr(a.get("createdBy"))}')

        # 返回员工完整数据（包含 apiKey，前端需要它来显示和保存）
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
                'archived': bool(a.get('archived')) or a.get('status') == 'archived',
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
        if not self._require_module_permission(auth, 'employees'): return

        agents = _load_agents()
        agent = None
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break

        if not agent:
            self._send_json(404, {'error': '员工不存在'})
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
        if not self._require_module_permission(auth, 'employees'): return

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
            'apiKey': _sanitize_api_key(body.get('apiKey', '')),
            'openclawAgent': body.get('openclawAgent', ''),
            'openclawModel': body.get('openclawModel', ''),
            'openclawName': body.get('openclawName', ''),
            'aiProvider': body.get('aiProvider', ''),
            'systemPrompt': body.get('systemPrompt', ''),
            'department': body.get('department', ''),
            'customEndpoint': body.get('customEndpoint', ''),
        }

        agents = _load_agents(include_archived=True)
        # 检查 ID 重复
        for a in agents:
            if a.get('id') == new_agent['id']:
                new_agent['id'] = 'emp_' + uuid.uuid4().hex[:6]
                break
        agents.append(new_agent)
        _save_agents(agents)
        # 自动同步 API Key 到 OpenClaw
        if new_agent.get('apiKey') and (new_agent.get('apiProvider') or new_agent.get('aiProvider')):
            _sync_agent_api_key_to_openclaw(new_agent)

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
            if not self._require_module_permission(auth, 'employees'): return

            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return

            body_keys = list(body.keys())
            print(f'  [PUT agent] id={agent_id} body_keys={body_keys}', flush=True)

            agents = _load_agents(include_archived=True)
            agent = None
            for a in agents:
                if a.get('id') == agent_id:
                    agent = a
                    break

            if not agent:
                self._send_json(404, {'error': '员工不存在'})
                return
            # 已归档员工只有在请求中明确取消归档时才允许更新
            is_unarchive = ('archived' in body and body.get('archived') is False) or ('status' in body and body.get('status') != 'archived')
            if (agent.get('status') == 'archived' or agent.get('archived')) and not is_unarchive:
                self._send_json(404, {'error': '员工不存在'})
                return

            # 权限校验
            if not auth.is_admin:
                if agent.get('createdBy') != auth.user_info['userId']:
                    if not (auth.is_leader and agent.get('createdBy') in _get_team_member_ids(auth)):
                        self._send_auth_error('权限不足', 403)
                        return

            # 检测 API Key 是否变动（优先 aiProvider，与 _sync_agent_api_key_to_openclaw 一致）
            old_api_key = agent.get('apiKey', '')
            old_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')

            # 可更新字段
            updatable = ['name', 'role', 'bg', 'avatar', 'status', 'msg', 'archived',
                         'permission', 'visibility', 'connectionType', 'apiProvider',
                         'apiModel', 'apiKey', 'openclawAgent', 'openclawModel',
                         'openclawName', 'aiProvider',
                         'systemPrompt', 'department', 'customEndpoint',
                         'group', 'pinned', 'idDoc', 'soulDoc', 'toolsDoc', 'userDoc',
                         'badge', 'createdBy', 'createdByName']
            saved_keys = []
            for key in updatable:
                if key in body:
                    if key == 'role':
                        agent[key] = _sanitize_role(body[key])
                    elif key == 'apiKey':
                        agent[key] = _sanitize_api_key(body[key])
                    else:
                        agent[key] = body[key]
                    saved_keys.append(key)

            print(f'  [PUT agent] id={agent_id} 实际保存字段={saved_keys}', flush=True)

            # 根因排查：保存前打印 apiKey 详情
            pre_save_api_key = agent.get('apiKey', '')
            if pre_save_api_key:
                print(f'  [PUT agent] id={agent_id} 保存前 apiKey len={len(pre_save_api_key)} preview={repr(pre_save_api_key[:50])}', flush=True)

            _save_agents(agents)

            # 根因排查：保存后重新加载并对比
            post_agents = _load_agents()
            post_agent = None
            for a in post_agents:
                if a.get('id') == agent_id:
                    post_agent = a
                    break
            if post_agent:
                post_api_key = post_agent.get('apiKey', '')
                if post_api_key != pre_save_api_key:
                    print(f'  [PUT agent] id={agent_id} 保存后 apiKey 发生变化! pre_len={len(pre_save_api_key)} post_len={len(post_api_key)} post_preview={repr(post_api_key[:50])}', flush=True)
                    import traceback
                    traceback.print_stack()
                elif post_api_key:
                    print(f'  [PUT agent] id={agent_id} 保存后 apiKey 一致 len={len(post_api_key)}', flush=True)

            # 自动同步 API Key 到 OpenClaw（有变动时）
            new_api_key = agent.get('apiKey', '')
            new_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
            print(f'  [PUT agent] id={agent_id} 同步检测: old_key={bool(old_api_key)} new_key={bool(new_api_key)} old_prov={old_provider} new_prov={new_provider}', flush=True)
            if new_api_key and new_provider:
                if new_api_key != old_api_key or new_provider != old_provider:
                    _sync_agent_api_key_to_openclaw(agent)
                else:
                    print(f'  [PUT agent] id={agent_id} API Key 未变动，跳过同步', flush=True)
            else:
                print(f'  [PUT agent] id={agent_id} 缺少 apiKey 或 provider，跳过同步', flush=True)

            print(f'  [PUT agent] saved ok, sending response', flush=True)
            self._send_json(200, agent)
        except Exception as e:
            print(f'  [PUT agent] ERROR: {e}', flush=True)
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_agent_self_update(self, agent_id):
        """PUT /api/agents/:id/self-update - AI 员工自修改配置"""
        try:
            auth = _authenticate(self.headers)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return
            if not self._require_module_permission(auth, 'employees'):
                return

            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return

            # 通过 agent_id 校验只能修改自身
            body_agent_id = body.get('agent_id', '')
            if body_agent_id != agent_id:
                self._send_json(403, {'error': 'agent_id 不匹配，只能修改自身数据'})
                return

            # 校验访问权限
            _, err, status = self._check_agent_access(auth, agent_id)
            if err:
                self._send_json(status, {'error': err})
                return

            # 禁止携带不允许的字段
            forbidden = [k for k in body.keys() if k in _SELF_UPDATE_FORBIDDEN_FIELDS]
            if forbidden:
                self._send_json(400, {'error': '包含不允许修改的字段: ' + ', '.join(forbidden)})
                return

            updates = []
            for field in _SELF_UPDATE_ALLOWED_FIELDS.keys():
                if field in body:
                    updates.append((field, body[field]))

            if not updates:
                self._send_json(400, {'error': '没有可更新的字段'})
                return

            ok, message, agent = _apply_agent_self_update(agent_id, updates, source=f'api:{auth.user_id}')
            if not ok:
                status = 404 if '不存在' in message else 400
                self._send_json(status, {'error': message})
                return

            self._send_json(200, {'success': True, 'agent': agent, 'message': message})
        except Exception as e:
            print(f'  [PUT agent self-update] ERROR: {e}', flush=True)
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_delete_agent(self, agent_id):
        """DELETE /api/agents/:id"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'employees'): return

        qs = parse_qs(urlparse(self.path).query)
        permanent = qs.get('permanent', ['false'])[0].lower() in ('true', '1', 'yes')

        agents = _load_agents(include_archived=True)
        agent = None
        agent_idx = -1
        for i, a in enumerate(agents):
            if a.get('id') == agent_id:
                agent = a
                agent_idx = i
                break

        if not agent:
            self._send_json(404, {'error': '员工不存在'})
            return

        is_archived = agent.get('status') == 'archived' or agent.get('archived')

        # 权限校验
        if not auth.is_admin:
            if agent.get('createdBy') != auth.user_info['userId']:
                # leader可以删管理组内成员创建的agent
                if not (auth.is_leader and agent.get('createdBy') in _get_team_member_ids(auth)):
                    self._send_auth_error('权限不足', 403)
                    return

        if permanent:
            # 彻底删除：仅从 agents.json 移除（仅限已归档员工）
            if not is_archived:
                self._send_json(400, {'error': '只能彻底删除已归档员工'})
                return
            if agent_idx >= 0:
                agents.pop(agent_idx)
            _save_agents(agents)
            # 清理关联数据，避免残留影响后续同名/同 ID 新员工
            self._cleanup_agent_data(agent_id)
            self._send_json(200, {'message': f'Agent {agent.get("name", "")} 已彻底删除'})
            return

        # 非归档员工才能软删除；已归档员工走 ?permanent=true
        if is_archived:
            self._send_json(404, {'error': '员工不存在'})
            return

        # 软删除：保留数据，标记为 archived
        agent['status'] = 'archived'
        agent['archived'] = True
        agent['archivedAt'] = datetime.now().isoformat()
        _save_agents(agents)

        self._send_json(200, {'message': f'Agent {agent.get("name", "")} 已删除'})

    def _cleanup_agent_data(self, agent_id):
        """彻底删除员工时清理其聊天记录、记忆文件、归档文件、数据库沉淀及缓存等残留数据"""
        # 清理聊天记录
        chat_file = os.path.join(CHATS_DIR, f'{agent_id}.json')
        if os.path.isfile(chat_file):
            try:
                os.remove(chat_file)
            except OSError as e:
                print(f'  [Cleanup] 删除聊天文件失败 {chat_file}: {e}', flush=True)

        # 清理聊天摘要
        summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
        if os.path.isfile(summary_file):
            try:
                os.remove(summary_file)
            except OSError as e:
                print(f'  [Cleanup] 删除摘要文件失败 {summary_file}: {e}', flush=True)

        # 清理 v3 记忆数据目录
        try:
            import shutil
            mem_dir = os.path.join(ms3.MEMORY_V3_DIR, agent_id)
            if os.path.isdir(mem_dir):
                shutil.rmtree(mem_dir)
        except Exception as e:
            print(f'  [Cleanup] 清理记忆目录失败 {agent_id}: {e}', flush=True)

        # 清理其他 AI 员工个人记忆中来自该员工的项目组上下文
        try:
            for other_id in os.listdir(ms3.MEMORY_V3_DIR):
                other_dir = os.path.join(ms3.MEMORY_V3_DIR, other_id)
                if not os.path.isdir(other_dir) or other_id == agent_id or other_id == 'groups':
                    continue
                for mem_file in ('memory.json', 'archived.json'):
                    fp = os.path.join(other_dir, mem_file)
                    if not os.path.isfile(fp):
                        continue
                    try:
                        with open(fp, 'r', encoding='utf-8') as f:
                            mem_data = json.load(f)
                        changed = False
                        for pool in ('core', 'daily'):
                            original = mem_data.get(pool, [])
                            if not isinstance(original, list):
                                continue
                            filtered = []
                            for m in original:
                                sender = m.get('senderId')
                                if isinstance(sender, list):
                                    if agent_id in sender:
                                        sender = [s for s in sender if s != agent_id]
                                        if not sender:
                                            m = None
                                        else:
                                            m['senderId'] = sender
                                elif sender == agent_id:
                                    m = None
                                if m is not None:
                                    filtered.append(m)
                            if len(filtered) < len(original):
                                mem_data[pool] = filtered
                                changed = True
                        if changed:
                            mem_data['updatedAt'] = int(time.time() * 1000)
                            with open(fp, 'w', encoding='utf-8') as f:
                                json.dump(mem_data, f, ensure_ascii=False, indent=2)
                            print(f'  [Cleanup] 从 {other_id}/{mem_file} 移除该 AI 员工的群聊上下文', flush=True)
                    except Exception as e:
                        print(f'  [Cleanup] 清理 {other_id} 记忆失败: {e}', flush=True)
        except Exception as e:
            print(f'  [Cleanup] 扫描其他 AI 记忆失败: {e}', flush=True)

        # 清理项目组公共记忆中该 AI 员工的发言记录（活跃 + 归档）
        try:
            import glob as _glob
            group_dir = os.path.join(ms3.MEMORY_V3_DIR, 'groups')
            if os.path.isdir(group_dir):
                # 活跃记忆
                for group_mem_file in _glob.glob(os.path.join(group_dir, 'group_*.json')):
                    try:
                        with open(group_mem_file, 'r', encoding='utf-8') as f:
                            gm_data = json.load(f)
                        changed = False
                        for pool in ('core', 'daily'):
                            original = gm_data.get(pool, [])
                            if not isinstance(original, list):
                                continue
                            filtered = [m for m in original if m.get('senderId') != agent_id]
                            if len(filtered) < len(original):
                                gm_data[pool] = filtered
                                changed = True
                        if changed:
                            gm_data['updatedAt'] = int(time.time() * 1000)
                            with open(group_mem_file, 'w', encoding='utf-8') as f:
                                json.dump(gm_data, f, ensure_ascii=False, indent=2)
                            print(f'  [Cleanup] 从 {os.path.basename(group_mem_file)} 移除该 AI 员工的项目组记忆', flush=True)
                    except Exception as e:
                        print(f'  [Cleanup] 清理项目组记忆失败 {group_mem_file}: {e}', flush=True)
                # 归档记忆
                for group_arc_file in _glob.glob(os.path.join(group_dir, 'group_*_archived.json')):
                    try:
                        with open(group_arc_file, 'r', encoding='utf-8') as f:
                            ga_data = json.load(f)
                        archived = ga_data.get('archived', [])
                        if not isinstance(archived, list):
                            continue
                        filtered = [m for m in archived if m.get('senderId') != agent_id]
                        if len(filtered) < len(archived):
                            ga_data['archived'] = filtered
                            ga_data['updatedAt'] = int(time.time() * 1000)
                            with open(group_arc_file, 'w', encoding='utf-8') as f:
                                json.dump(ga_data, f, ensure_ascii=False, indent=2)
                            print(f'  [Cleanup] 从 {os.path.basename(group_arc_file)} 移除该 AI 员工的项目组归档记忆', flush=True)
                    except Exception as e:
                        print(f'  [Cleanup] 清理项目组归档记忆失败 {group_arc_file}: {e}', flush=True)
        except Exception as e:
            print(f'  [Cleanup] 扫描项目组记忆失败: {e}', flush=True)

        # 清理归档文件
        archive_file = os.path.join(ARCHIVE_DIR, f'{agent_id}.json')
        if os.path.isfile(archive_file):
            try:
                os.remove(archive_file)
            except OSError as e:
                print(f'  [Cleanup] 删除归档文件失败 {archive_file}: {e}', flush=True)

        # 清理群聊归档（L3 overflow）中该 AI 员工发送的消息
        try:
            import glob as _glob
            for group_arc_file in _glob.glob(os.path.join(ARCHIVE_DIR, 'group_*.json')):
                try:
                    with open(group_arc_file, 'r', encoding='utf-8') as f:
                        ga_data = json.load(f)
                    changed = False
                    # memories 中可能保存原始消息对象
                    memories = ga_data.get('memories', [])
                    if isinstance(memories, list):
                        filtered_mem = [
                            m for m in memories
                            if not (m.get('senderType') == 'agent' and m.get('senderId') == agent_id)
                        ]
                        if len(filtered_mem) < len(memories):
                            ga_data['memories'] = filtered_mem
                            changed = True
                    # summaries 是文本摘要，无法精确识别发送者，保留
                    if changed:
                        ga_data['updatedAt'] = int(time.time() * 1000)
                        with open(group_arc_file, 'w', encoding='utf-8') as f:
                            json.dump(ga_data, f, ensure_ascii=False, indent=2)
                        print(f'  [Cleanup] 从 {os.path.basename(group_arc_file)} 归档移除该 AI 消息', flush=True)
                except Exception as e:
                    print(f'  [Cleanup] 清理群聊归档失败 {group_arc_file}: {e}', flush=True)
        except Exception as e:
            print(f'  [Cleanup] 扫描群聊归档失败: {e}', flush=True)

        # 清理群聊中该 AI 员工发送的消息
        try:
            import glob as _glob
            for group_chat_file in _glob.glob(os.path.join(CHATS_DIR, 'group_*.json')):
                try:
                    with open(group_chat_file, 'r', encoding='utf-8') as f:
                        gc_data = json.load(f)
                    if not isinstance(gc_data, list):
                        continue
                    original_len = len(gc_data)
                    filtered = [
                        m for m in gc_data
                        if not (
                            m.get('senderType') == 'agent' and
                            m.get('senderId') == agent_id
                        )
                    ]
                    if len(filtered) < original_len:
                        with open(group_chat_file, 'w', encoding='utf-8') as f:
                            json.dump(filtered, f, ensure_ascii=False, indent=2)
                        print(f'  [Cleanup] 从 {os.path.basename(group_chat_file)} 移除 {original_len - len(filtered)} 条该 AI 消息', flush=True)
                except Exception as e:
                    print(f'  [Cleanup] 清理群聊文件失败 {group_chat_file}: {e}', flush=True)
        except Exception as e:
            print(f'  [Cleanup] 扫描群聊文件失败: {e}', flush=True)

        # 清理数据库中的员工级联数据（记忆、沉淀、知识库、向量缓存等）
        try:
            conn = _db_conn()

            # 1) 收集该员工所有 memory id 与 value hash，用于后续级联清理
            mem_rows = conn.execute(
                "SELECT id, value FROM memory WHERE emp_id=?", (agent_id,)
            ).fetchall()
            mem_ids = [r['id'] for r in mem_rows]
            content_hashes = set()
            for r in mem_rows:
                v = r['value'] or ''
                if v:
                    content_hashes.add(hashlib.md5(str(v).encode('utf-8')).hexdigest())

            # 2) 清理 embedding_cache（按该员工记忆 value 的 hash）
            if content_hashes:
                placeholders = ','.join('?' * len(content_hashes))
                conn.execute(
                    f"DELETE FROM embedding_cache WHERE content_hash IN ({placeholders})",
                    tuple(content_hashes)
                )

            # 3) 清理二级归纳、三级知识库引用
            if mem_ids:
                mem_id_set = set(mem_ids)
                # memory_summary：删除所有 evidence 全部来自该员工的归纳；否则移除引用
                summary_rows = conn.execute(
                    "SELECT id, related_mem_ids, source_mem_ids FROM memory_summary WHERE emp_id=?",
                    (agent_id,)
                ).fetchall()
                for row in summary_rows:
                    sid = row['id']
                    related = json.loads(row['related_mem_ids'] or '[]')
                    sources = json.loads(row['source_mem_ids'] or '[]')
                    related = [x for x in related if x not in mem_ids]
                    sources = [x for x in sources if x not in mem_ids]
                    if not related and not sources:
                        conn.execute("DELETE FROM memory_summary WHERE id=?", (sid,))
                    else:
                        conn.execute(
                            "UPDATE memory_summary SET related_mem_ids=?, source_mem_ids=?, updated_at=? WHERE id=?",
                            (json.dumps(related, ensure_ascii=False), json.dumps(sources, ensure_ascii=False), int(time.time() * 1000), sid)
                        )

                # knowledge_base：删除所有 evidence 全部来自该员工的条目；否则移除引用
                kb_rows = conn.execute(
                    f"SELECT id, related_mem_ids FROM knowledge_base WHERE emp_id=?",
                    (agent_id,)
                ).fetchall()
                for row in kb_rows:
                    kid = row['id']
                    related = json.loads(row['related_mem_ids'] or '[]')
                    related = [x for x in related if x not in mem_id_set]
                    if not related:
                        conn.execute("DELETE FROM knowledge_base WHERE id=?", (kid,))
                    else:
                        conn.execute(
                            "UPDATE knowledge_base SET related_mem_ids=?, updated_at=? WHERE id=?",
                            (json.dumps(related, ensure_ascii=False), int(time.time() * 1000), kid)
                        )

                # knowledge_base_new：按 evidence_mem_ids 中包含的 memory id 清理
                kb_new_rows = conn.execute(
                    "SELECT id, evidence_mem_ids FROM knowledge_base_new"
                ).fetchall()
                for row in kb_new_rows:
                    kid = row['id']
                    evidence = json.loads(row['evidence_mem_ids'] or '[]')
                    new_evidence = [x for x in evidence if x not in mem_id_set]
                    if len(new_evidence) < len(evidence):
                        if not new_evidence:
                            conn.execute("DELETE FROM knowledge_base_new WHERE id=?", (kid,))
                        else:
                            conn.execute(
                                "UPDATE knowledge_base_new SET evidence_mem_ids=?, updated_at=? WHERE id=?",
                                (json.dumps(new_evidence, ensure_ascii=False), int(time.time() * 1000), kid)
                            )

            # 4) 清理 memory_topics：从 emp_ids 中移除该员工；若为空则删除 topic
            topic_rows = conn.execute(
                "SELECT id, emp_ids, mem_count FROM memory_topics WHERE emp_ids LIKE ?",
                (f'%"{agent_id}"%',)
            ).fetchall()
            for row in topic_rows:
                tid = row['id']
                emp_ids = json.loads(row['emp_ids'] or '[]')
                if agent_id in emp_ids:
                    emp_ids.remove(agent_id)
                if not emp_ids:
                    conn.execute("DELETE FROM memory_topics WHERE id=?", (tid,))
                else:
                    # 重新统计该 topic 下剩余活跃记忆数
                    remaining = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM memory WHERE status='active' AND topic_ids LIKE ?",
                        (f'%"{tid}"%',)
                    ).fetchone()['cnt']
                    conn.execute(
                        "UPDATE memory_topics SET emp_ids=?, mem_count=? WHERE id=?",
                        (json.dumps(emp_ids, ensure_ascii=False), max(0, remaining), tid)
                    )

            # 5) 删除员工个人知识库文档、分块、版本
            conn.execute("DELETE FROM knowledge WHERE emp_id=?", (agent_id,))
            conn.execute("DELETE FROM knowledge_chunks WHERE emp_id=?", (agent_id,))
            conn.execute("DELETE FROM knowledge_versions WHERE emp_id=?", (agent_id,))

            # 6) 最后删除记忆主表（级联后的根数据）
            conn.execute("DELETE FROM memory WHERE emp_id=?", (agent_id,))

            # 7) 硬删除该 AI 员工通过工具创建的业务实体
            # 7.1) 达人及其关联数据
            talent_ids = [r['id'] for r in conn.execute(
                "SELECT id FROM talents WHERE created_by=?", (agent_id,)
            ).fetchall()]
            if talent_ids:
                placeholders = ','.join('?' * len(talent_ids))
                conn.execute(f"DELETE FROM talent_follow_ups WHERE talent_id IN ({placeholders})", tuple(talent_ids))
                conn.execute(f"DELETE FROM product_talent_match WHERE talent_id IN ({placeholders})", tuple(talent_ids))
                conn.execute(f"DELETE FROM talents WHERE id IN ({placeholders})", tuple(talent_ids))
                print(f'  [Cleanup] 已硬删除 {agent_id} 创建的 {len(talent_ids)} 个达人及关联跟进/匹配记录', flush=True)

            # 7.2) 商品及其关联匹配数据
            product_ids = [r['id'] for r in conn.execute(
                "SELECT id, brand_id FROM products WHERE created_by=?", (agent_id,)
            ).fetchall()]
            affected_brand_ids = set()
            if product_ids:
                for row in conn.execute(
                    "SELECT DISTINCT brand_id FROM products WHERE created_by=? AND brand_id != ''",
                    (agent_id,)
                ).fetchall():
                    affected_brand_ids.add(row['brand_id'])
                placeholders = ','.join('?' * len(product_ids))
                conn.execute(f"DELETE FROM product_talent_match WHERE product_id IN ({placeholders})", tuple(product_ids))
                conn.execute(f"DELETE FROM products WHERE id IN ({placeholders})", tuple(product_ids))
                print(f'  [Cleanup] 已硬删除 {agent_id} 创建的 {len(product_ids)} 个商品及关联匹配记录', flush=True)

            conn.commit()

            # 7.3) 更新受影响品牌的统计
            try:
                for brand_id in affected_brand_ids:
                    _update_brand_product_stats(conn, brand_id)
                conn.commit()
            except Exception as e:
                print(f'  [Cleanup] 更新品牌统计失败: {e}', flush=True)

            conn.close()
            print(f'  [Cleanup] 已清理 {agent_id} 的数据库级联数据', flush=True)
        except Exception as e:
            print(f'  [Cleanup] 数据库级联清理失败 {agent_id}: {e}', flush=True)

        # 清理 RAG 内存缓存中该员工的查询结果
        try:
            rag_cache = getattr(ks, '_rag_cache', None)
            if rag_cache is not None:
                keys_to_remove = [k for k in rag_cache.keys() if k.startswith(f"rag:{agent_id}:")]
                for k in keys_to_remove:
                    rag_cache.pop(k, None)
                if keys_to_remove:
                    print(f'  [Cleanup] 已清理 {agent_id} 的 RAG 内存缓存 {len(keys_to_remove)} 条', flush=True)
        except Exception as e:
            print(f'  [Cleanup] RAG 缓存清理失败 {agent_id}: {e}', flush=True)

    # ═══════════════════════════════════════════════════
    # Dreaming API
    # ═══════════════════════════════════════════════════

    def _handle_get_dreaming(self):
        """GET /api/openclaw/dreaming?agentId=xxx"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            qs = parse_qs(urlparse(self.path).query)
            agent_id = qs.get('agentId', [''])[0]
            if not agent_id:
                self._send_json(400, {'error': '缺少 agentId'})
                return
            agents = _load_agents()
            agent = None
            for a in agents:
                if a.get('id') == agent_id:
                    agent = a
                    break
            if not agent:
                self._send_json(404, {'error': '员工不存在'})
                return
            dreaming = agent.get('dreaming', {'enabled': False, 'phase': 'idle'})
            self._send_json(200, {'agentId': agent_id, 'enabled': dreaming.get('enabled', False), 'phase': dreaming.get('phase', 'idle')})
        except Exception as e:
            print(f'  [GET dreaming] ERROR: {e}', flush=True)
            self._send_json(500, {'error': str(e)})

    def _handle_post_dreaming(self):
        """POST /api/openclaw/dreaming body:{agentId, enabled}"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return
            agent_id = body.get('agentId')
            enabled = body.get('enabled')
            if not agent_id or enabled is None:
                self._send_json(400, {'error': '缺少 agentId 或 enabled'})
                return
            agents = _load_agents()
            agent = None
            for a in agents:
                if a.get('id') == agent_id:
                    agent = a
                    break
            if not agent:
                self._send_json(404, {'error': '员工不存在'})
                return
            if not auth.is_admin:
                if agent.get('createdBy') != auth.user_info['userId']:
                    self._send_auth_error('权限不足', 403)
                    return
            dreaming = agent.get('dreaming', {})
            dreaming['enabled'] = bool(enabled)
            if enabled:
                dreaming['phase'] = 'light'
            else:
                dreaming['phase'] = 'idle'
            agent['dreaming'] = dreaming
            _save_agents(agents)
            self._send_json(200, {'agentId': agent_id, 'enabled': dreaming['enabled'], 'phase': dreaming['phase']})
        except Exception as e:
            print(f'  [POST dreaming] ERROR: {e}', flush=True)
            self._send_json(500, {'error': str(e)})

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
        tools_doc = body.get('toolsDoc', '')
        workspace_path = body.get('workspacePath', '')

        if not agent_id:
            self._send_json(400, {'error': '缺少 agentId'})
            return

        import os
        if not workspace_path:
            # 默认 workspace 路径与 get-agent-docs 保持一致：优先使用 openclawName
            agents = _load_agents()
            openclaw_name = ''
            for a in agents:
                if a.get('id') == agent_id:
                    openclaw_name = a.get('openclawName', '')
                    break
            workspace_path = '~/.openclaw/workspace-' + (openclaw_name or agent_id)
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

            if tools_doc:
                with open(os.path.join(workspace_path, 'TOOLS.md'), 'w', encoding='utf-8') as f:
                    f.write(tools_doc)
                written.append('TOOLS.md')

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
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
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
            self._send_json(404, {'error': '员工不存在'})
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
                content = agent.get('toolsDoc', agent.get('agentsDoc', ''))
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
                content = agent.get('toolsDoc', agent.get('agentsDoc', ''))
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
            return None, '员工不存在', 404
        if not auth.is_admin and agent.get('createdBy') != auth.user_info['userId'] and agent.get('visibility') != 'all':
            return None, '权限不足', 403
        return agent, None, None

    def _require_module_permission(self, auth, module):
        """检查当前用户是否有指定模块权限，无权限时直接返回 403"""
        if not _has_module_permission(auth, module):
            self._send_auth_error('Permission denied', 403)
            return False
        return True


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

    # ═══════════════════════════════════════════════════
    # 记忆系统 v2 API（三层大脑架构）
    # ═══════════════════════════════════════════════════

    def _handle_get_memory(self, emp_id):
        """GET /api/memory/{empId}[?type=&key=&tag=&keyword=&limit=&offset=] — 查询记忆列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        # 解析查询参数
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        # type 优先，兼容旧版 pool 参数
        type_filter = qs.get('type', qs.get('pool', ['']))[0]
        key_filter = qs.get('key', [''])[0]
        tag_filter = qs.get('tag', [''])[0]
        keyword = qs.get('keyword', [''])[0].lower()
        include_archived = qs.get('include_archived', ['false'])[0].lower() in ('true', '1', 'yes')
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int(qs.get('offset', ['0'])[0]))
        except ValueError:
            offset = 0

        # v3：活跃记忆（load_memory 内部自动归档过期 daily 到 archived.json）
        data = ms3.load_memory(emp_id)
        archive_data = ms3.load_archive(emp_id) if include_archived else {'archived': []}

        # 字段映射：v3 createdAt → v2 time（前端兼容）
        def _map_mem(m):
            r = dict(m)
            if 'createdAt' in r:
                r['time'] = r.pop('createdAt')
            if 'updatedAt' in r:
                r.pop('updatedAt', None)
            if 'expiresAt' in r:
                r.pop('expiresAt', None)
            if 'context' in r:
                r.pop('context', None)
            # priority / tags 保留：前端核心记忆面板需要显示优先级火焰和标签
            if 'accessCount' in r:
                r.pop('accessCount', None)
            return r

        def _map_arch(m):
            r = dict(m)
            if 'createdAt' in r:
                r['time'] = r.pop('createdAt')
            if 'archivedAt' in r:
                r['archivedTime'] = r.pop('archivedAt')
            # archiveReason 保留：前端归档面板需要显示归档原因标签
            if 'originalKey' in r:
                r.pop('originalKey', None)
            return r

        def _map_knowledge(doc):
            """知识库文档 → 记忆格式（兼容前端），过期时间 90 天"""
            created_at = doc.get('createdAt') or int(time.time() * 1000)
            ttl_90d = 90 * 24 * 3600 * 1000
            return {
                'id': doc.get('id'),
                'key': 'knowledge',
                'value': f"[{doc.get('category', '知识')}] {doc.get('title')}: {doc.get('content', '')[:200]}",
                'source': 'knowledge_base',
                'time': created_at,
                'expiresAt': created_at + ttl_90d,
                '_origin': doc  # 保留原始数据供前端扩展
            }

        # 过滤 + 搜索逻辑
        def _matches(m):
            if key_filter and m.get('key') != key_filter:
                return False
            if tag_filter:
                tags = set(m.get('tags', []) or [])
                required = set(t.strip() for t in tag_filter.split(',') if t.strip())
                if not (tags & required):  # OR 匹配：交集为空则排除
                    return False
            if keyword:
                value = (m.get('value') or '').lower()
                if keyword not in value:
                    return False
            return True

        def _apply_filters_and_paging(items):
            filtered = [m for m in items if _matches(m)]
            return filtered[offset:offset + limit]

        # type 过滤：core / daily / knowledge / active / archive / 空=全部
        include_core = type_filter in ('', 'core', 'active')
        include_daily = type_filter in ('', 'daily', 'active')
        include_archive = type_filter in ('', 'archive')
        include_knowledge = type_filter in ('', 'knowledge')

        core_list = []
        daily_list = []
        archive_list = []
        knowledge_list = []

        if include_core:
            core_raw = data.get('core', [])
            core_list = [_map_mem(m) for m in _apply_filters_and_paging(core_raw)]
        if include_daily:
            daily_raw = data.get('daily', [])
            daily_list = [_map_mem(m) for m in _apply_filters_and_paging(daily_raw)]
        if include_archive:
            arch_raw = archive_data.get('archived', [])
            archive_list = [_map_arch(m) for m in _apply_filters_and_paging(arch_raw)]
        if include_knowledge:
            # v3：知识库已改为全局公共，从 SQLite 统一读取
            try:
                kb_result = ks.knowledge_list(
                    offset=offset, limit=limit, category=None,
                    keyword=keyword if keyword else None,
                    user_id=auth.user_id, is_admin=auth.is_admin,
                    user_team_ids=auth.team_ids,
                    user_group_ids=auth.group_ids
                )
                kb_docs = kb_result.get('docs', [])
            except Exception as e:
                print(f'  [MemoryAPI] 加载知识库失败: {e}', flush=True)
                kb_docs = []
            knowledge_list = [_map_knowledge(d) for d in kb_docs]

        # 合并为统一 memories 数组（每个项带 pool 字段）
        all_memories = []
        for m in core_list:
            m['pool'] = 'core'
            all_memories.append(m)
        for m in daily_list:
            m['pool'] = 'daily'
            all_memories.append(m)
        for m in archive_list:
            m['pool'] = 'archive'
            all_memories.append(m)
        for m in knowledge_list:
            m['pool'] = 'knowledge'
            all_memories.append(m)

        # 直接返回 data（前端兼容 v2 格式，不包装 success）
        self._send_json(200, {
            'memories': all_memories,
            'total': len(all_memories),
            'limit': limit,
            'offset': offset,
            'core': core_list,
            'daily': daily_list,
            'archive': archive_list,
            'knowledge': knowledge_list,
            'archivedToday': 0,
            'version': '3.0',
            'config': {k: v for k, v in MEMORY_CONFIG.items() if k in ('core_max', 'daily_max', 'daily_ttl_days')},
            'shouldConsolidate': data.get('shouldConsolidate', False),
            'suggestedSourceIds': data.get('suggestedSourceIds', []),
            # FIXME: 修复知识库归纳提示判断逻辑混乱：统一用"未归纳总数 >= 阈值 + 冷却期"模型
            'shouldInductKnowledge': (
                len([m for m in data.get('core', []) + data.get('daily', []) if not m.get('inductedAt')])
                >= MEMORY_INDUCTION_THRESHOLDS['knowledge_induction_min']
            ) and (
                data.get('lastKnowledgeInductionAttemptAt', 0) == 0
                or (int(time.time() * 1000) - data.get('lastKnowledgeInductionAttemptAt', 0) > 3600 * 1000)
            ),
            # FIXME: 调试字段：帮助排查 shouldInductKnowledge 显示异常
            '_debug': {
                'uninductedCount': len([m for m in data.get('core', []) + data.get('daily', []) if not m.get('inductedAt')]),
                'lastKnowledgeInductionAttemptAt': data.get('lastKnowledgeInductionAttemptAt', 0),
            }
        })

    def _handle_get_archived_memories(self):
        """GET /api/memory/archived — 查看全局归档记忆（支持分页/搜索）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        # 解析查询参数
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        keyword = qs.get('keyword', [''])[0].lower()
        reason_filter = qs.get('archived_reason', [''])[0]
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int(qs.get('offset', ['0'])[0]))
        except ValueError:
            offset = 0

        # 遍历所有员工的归档文件
        archived_list = []
        memories_dir = os.path.join(DATA_DIR, 'memories')
        if os.path.isdir(memories_dir):
            for emp_id in os.listdir(memories_dir):
                arch_path = os.path.join(memories_dir, emp_id, 'archived.json')
                if os.path.exists(arch_path):
                    arch_data = _read_json(arch_path, {'archived': []})
                    for m in arch_data.get('archived', []):
                        if keyword:
                            value = (m.get('value') or '').lower()
                            if keyword not in value:
                                continue
                        if reason_filter:
                            if m.get('archiveReason') != reason_filter:
                                continue
                        mapped = dict(m)
                        if 'createdAt' in mapped:
                            mapped['time'] = mapped.pop('createdAt')
                        if 'archivedAt' in mapped:
                            mapped['archivedTime'] = mapped.pop('archivedAt')
                        mapped['empId'] = emp_id
                        mapped['pool'] = 'archive'
                        archived_list.append(mapped)

        # 按 archivedTime 倒序
        archived_list.sort(key=lambda m: m.get('archivedTime', 0), reverse=True)
        total = len(archived_list)
        paginated = archived_list[offset:offset + limit]

        self._send_json(200, {
            'success': True,
            'data': {
                'memories': paginated,
                'total': total,
                'limit': limit,
                'offset': offset
            }
        })

    def _handle_consolidate_memory(self):
        """POST /api/memory/consolidate — 归纳合并多条 daily 记忆为 core 记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return

        emp_id = body.get('empId')
        if not emp_id:
            self._send_json_error(400, 'Missing empId')
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        source_ids = body.get('sourceIds', [])
        if len(source_ids) < 2:
            self._send_json_error(400, 'Need at least 2 sourceIds')
            return

        consolidated_value = body.get('consolidatedValue', '')
        # FIXME: 修复建议归纳分页导致源记忆不足：后端未收到 consolidatedValue 时自动生成
        if not consolidated_value:
            try:
                mem_data = ms3.load_memory(emp_id)
                source_memories = [
                    m for m in mem_data.get('daily', [])
                    if m.get('id') in source_ids
                ]
                if len(source_memories) < 2:
                    self._send_json_error(400, '源记忆不足')
                    return
                consolidated_value = '\n'.join('• ' + (m.get('value', '') or '') for m in source_memories)
            except Exception as e:
                print(f'  [MemoryV3] auto-generate consolidatedValue failed: {e}', flush=True)
                self._send_json_error(500, '生成归纳内容失败')
                return

        if len(consolidated_value) < 1:
            self._send_json_error(400, 'consolidatedValue cannot be empty')
            return
        cfg = MEMORY_CONFIG
        if len(consolidated_value) > cfg['store_value_max']:
            self._send_json_error(400, f'consolidatedValue exceeds max length {cfg["store_value_max"]}')
            return

        try:
            new_mem, archived_ids = ms3.consolidate_memory(
                emp_id,
                source_ids,
                consolidated_value,
                key=body.get('key', 'core'),
                priority=body.get('priority', 8),
                tags=body.get('tags', [])
            )
        except RuntimeError as e:
            # FIXME: 修复建议归纳失败后一直显示：失败后也更新 lastMemoryConsolidationAt 冷却提示
            ms3.set_last_memory_consolidation_at(emp_id)
            self._send_json(409, {'success': False, 'error': str(e)})
            return

        # 字段映射
        mapped = dict(new_mem)
        if 'createdAt' in mapped:
            mapped['time'] = mapped.pop('createdAt')
        mapped.pop('updatedAt', None)
        mapped.pop('expiresAt', None)
        mapped.pop('accessCount', None)

        print(f'  [MemoryV3] {emp_id} 归纳合并 {len(archived_ids)} 条记忆 → {new_mem["id"]}', flush=True)
        self._send_json(200, {
            'success': True,
            'data': {
                'newMemory': mapped,
                'archivedIds': archived_ids
            }
        })

    def _handle_search_memory(self):
        """GET /api/memory/search — 全局搜索记忆（跨员工、跨池）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        # 解析查询参数
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        keyword = qs.get('keyword', [''])[0].lower()
        tag_filter = qs.get('tag', [''])[0]
        type_filter = qs.get('type', [''])[0]
        key_filter = qs.get('key', [''])[0]
        emp_id_filter = qs.get('empId', [''])[0]
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        try:
            offset = max(0, int(qs.get('offset', ['0'])[0]))
        except ValueError:
            offset = 0

        results = []
        memories_dir = os.path.join(DATA_DIR, 'memories')

        def _matches(m):
            if keyword:
                value = (m.get('value') or '').lower()
                if keyword not in value:
                    return False
            if tag_filter:
                tags = set(m.get('tags', []) or [])
                required = set(t.strip() for t in tag_filter.split(',') if t.strip())
                if not (tags & required):
                    return False
            if key_filter:
                if m.get('key') != key_filter:
                    return False
            return True

        def _map_mem(m, emp_id, pool):
            r = dict(m)
            if 'createdAt' in r:
                r['time'] = r.pop('createdAt')
            r.pop('updatedAt', None)
            r.pop('expiresAt', None)
            r.pop('context', None)
            r.pop('accessCount', None)
            r['empId'] = emp_id
            r['pool'] = pool
            return r

        if os.path.isdir(memories_dir):
            for emp_id in os.listdir(memories_dir):
                if emp_id_filter and emp_id != emp_id_filter:
                    continue
                # 活跃记忆
                mem_path = os.path.join(memories_dir, emp_id, 'memory.json')
                if os.path.exists(mem_path):
                    mem_data = _read_json(mem_path, {'core': [], 'daily': []})
                    pools_to_search = []
                    if type_filter in ('', 'core', 'active'):
                        pools_to_search.append(('core', mem_data.get('core', [])))
                    if type_filter in ('', 'daily', 'active'):
                        pools_to_search.append(('daily', mem_data.get('daily', [])))
                    for pool_name, pool_list in pools_to_search:
                        for m in pool_list:
                            if _matches(m):
                                results.append(_map_mem(m, emp_id, pool_name))

                # 归档记忆
                if type_filter in ('', 'archive'):
                    arch_path = os.path.join(memories_dir, emp_id, 'archived.json')
                    if os.path.exists(arch_path):
                        arch_data = _read_json(arch_path, {'archived': []})
                        for m in arch_data.get('archived', []):
                            if _matches(m):
                                r = dict(m)
                                if 'createdAt' in r:
                                    r['time'] = r.pop('createdAt')
                                if 'archivedAt' in r:
                                    r['archivedTime'] = r.pop('archivedAt')
                                r['empId'] = emp_id
                                r['pool'] = 'archive'
                                results.append(r)

        # 按时间倒序
        results.sort(key=lambda m: m.get('time', 0), reverse=True)
        total = len(results)
        paginated = results[offset:offset + limit]

        self._send_json(200, {
            'success': True,
            'data': {
                'memories': paginated,
                'total': total,
                'limit': limit,
                'offset': offset
            }
        })

    def _handle_post_memory(self, emp_id):
        """POST /api/memory/{empId} — 添加记忆到对应分池（容量检查，超出返回 409）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        body = self._read_body()
        if not body or 'value' not in body:
            self._send_json_error(400, 'Missing value')
            return

        cfg = MEMORY_CONFIG
        value = body.get('value', '')
        warning = None
        if len(value) > cfg['store_value_max']:
            warning = f'Value truncated to {cfg["store_value_max"]} chars (original: {len(value)})'
            value = value[:cfg['store_value_max']]
        if len(value) < 1:
            self._send_json_error(400, 'Value cannot be empty')
            return

        key = body.get('type') or body.get('key', 'auto')
        pool = 'daily' if key in ('auto', 'auto_extract') else 'core'

        # 提取可选参数
        priority = body.get('priority')
        if priority is not None:
            try:
                priority = max(1, min(10, int(priority)))
            except (ValueError, TypeError):
                priority = None
        tags = body.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        tags = [str(t).strip() for t in tags if str(t).strip()][:10]  # 最多 10 个标签

        # 去重需要调用 Embedding API，优先使用全局配置，否则 fallback 到该 agent 自身 key
        agent = _get_agent_by_id(emp_id) or {}
        emb_cfg = get_embedding_config((agent or {}).get('id'))

        try:
            memory = ms3.add_memory(
                emp_id, value, key=key,
                source=body.get('source', 'user_input'),
                context=body.get('context', ''),
                priority=priority,
                tags=tags if tags else None,
                api_key=emb_cfg['apiKey'],
                provider=emb_cfg['provider'],
                model=emb_cfg['model'],
                base_url=emb_cfg['baseUrl']
            )
        except RuntimeError as e:
            self._send_json(409, {
                'success': False,
                'error': str(e),
                'pool': pool,
                'max': cfg['core_max'] if pool == 'core' else cfg['daily_max'],
                'suggestion': 'Archive or delete old memories first'
            })
            return

        # 字段映射：v3 → v2 前端兼容
        mapped = dict(memory)
        if 'createdAt' in mapped:
            mapped['time'] = mapped.pop('createdAt')
        mapped.pop('updatedAt', None)
        mapped.pop('expiresAt', None)
        mapped.pop('context', None)
        mapped.pop('accessCount', None)
        # priority / tags 保留给前端展示

        print(f'  [MemoryV3] {emp_id} 保存 {pool} 记忆: {value[:50]}...', flush=True)

        # FIXME: 大脑知识中枢新增：把记忆加入清洗窗口
        try:
            _brain_scheduler.request_clean(emp_id, memory.get('id'))
        except Exception as e:
            print(f'  [BrainScheduler] request_clean failed: {e}', flush=True)

        # FIXME: 三级知识库自动沉淀 + 二级归纳自动触发（数量/决策）
        auto_triggers = []
        if key in ('auto', 'auto_extract'):
            try:
                # 三级沉淀：决策关键词/重复提及自动入 knowledge_base
                _auto_check_knowledge(emp_id, memory.get('id'), memory.get('value'), memory.get('tags'))
                # 二级归纳：数量触发 / 决策触发 -> 创建 pending 记录，由前端 AI 生成正式内容
                auto_triggers = _auto_summarize_triggers(emp_id, memory)
            except Exception as e:
                print(f'  [MemoryV3] {emp_id} 自动沉淀/归纳触发失败: {e}', flush=True)

        # 自动提取的记忆（auto/auto_extract）尝试触发知识归纳到个人知识库
        if key in ('auto', 'auto_extract'):
            try:
                agent = _get_agent_by_id(emp_id) or {}
                threading.Thread(
                    target=_induct_knowledge_for_agent,
                    args=(agent, auth.user_id),
                    daemon=True
                ).start()
            except Exception as e:
                print(f'  [MemoryV3] {emp_id} 自动归纳触发失败: {e}', flush=True)

        result = {
            'success': True,
            'data': mapped,
            'id': mapped.get('id')
        }
        if warning:
            result['warning'] = warning
        # FIXME: 返回自动触发标记，前端可据此立即刷新记忆汇总
        if auto_triggers:
            result['summaryTriggers'] = auto_triggers
        self._send_json(200, result)

    def _handle_delete_memory(self, emp_id, memory_id):
        """DELETE /api/memory/{empId}/{memoryId} — 删除单条记忆（支持 archived 数据）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        removed = ms3.delete_memory(emp_id, memory_id)
        if removed:
            print(f'  [MemoryV3] {emp_id} 删除记忆: {memory_id}', flush=True)

        self._send_json(200, {
            'success': True,
            'data': {'deleted': removed, 'id': memory_id}
        })

    def _handle_update_memory(self, emp_id, memory_id):
        """PUT /api/memory/{empId}/{memoryId} — 修改单条记忆（支持跨池移动）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return

        cfg = MEMORY_CONFIG
        updates = {}
        warning = None
        if 'value' in body:
            value = body['value']
            if len(value) > cfg['store_value_max']:
                warning = f'Value truncated to {cfg["store_value_max"]} chars (original: {len(value)})'
                value = value[:cfg['store_value_max']]
            updates['value'] = value
        if 'source' in body:
            updates['source'] = body['source']
        if 'type' in body:
            updates['key'] = body['type']
        elif 'key' in body:
            updates['key'] = body['key']
        if 'priority' in body:
            updates['priority'] = body['priority']
        if 'tags' in body:
            updates['tags'] = body['tags']
        if 'context' in body:
            updates['context'] = body['context']

        # 去重需要调用 Embedding API，优先使用全局配置，否则 fallback 到该 agent 自身 key
        agent = _get_agent_by_id(emp_id) or {}
        emb_cfg = get_embedding_config((agent or {}).get('id'))

        try:
            updated = ms3.update_memory(
                emp_id, memory_id, updates,
                api_key=emb_cfg['apiKey'],
                provider=emb_cfg['provider'],
                model=emb_cfg['model'],
                base_url=emb_cfg['baseUrl']
            )
        except RuntimeError as e:
            self._send_json(409, {'error': str(e)})
            return

        if not updated:
            self._send_json_error(404, 'Memory not found')
            return

        # 字段映射：v3 → v2 前端兼容
        mapped = dict(updated)
        if 'createdAt' in mapped:
            mapped['time'] = mapped.pop('createdAt')
        mapped.pop('updatedAt', None)
        mapped.pop('expiresAt', None)
        mapped.pop('accessCount', None)

        result = {
            'success': True,
            'data': mapped
        }
        if warning:
            result['warning'] = warning
        self._send_json(200, result)

    def _handle_promote_memory(self, emp_id, memory_id):
        """POST /api/memory/{empId}/{memoryId}/promote — 升级为核心记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        try:
            mem = ms3.promote_memory(emp_id, memory_id)
        except RuntimeError as e:
            self._send_json(409, {'error': str(e)})
            return

        if not mem:
            self._send_json_error(404, 'Memory not found in daily pool')
            return

        # 字段映射：v3 createdAt → v2 time（前端兼容）
        result = dict(mem)
        if 'createdAt' in result:
            result['time'] = result.pop('createdAt')
        result.pop('expiresAt', None)
        result.pop('context', None)

        print(f'  [MemoryV3] {emp_id} 升级为核心记忆: {mem.get("value", "")[:50]}...', flush=True)
        self._send_json(200, result)

    def _handle_restore_memory(self, emp_id, memory_id):
        """POST /api/memory/{empId}/{memoryId}/restore — 从归档恢复为日常记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        try:
            mem = ms3.restore_memory(emp_id, memory_id)
        except RuntimeError as e:
            self._send_json(409, {'error': str(e)})
            return

        if not mem:
            self._send_json_error(404, 'Memory not found in archive')
            return

        # 字段映射：v3 → v2 前端兼容
        mapped = dict(mem)
        if 'createdAt' in mapped:
            mapped['time'] = mapped.pop('createdAt')
        mapped.pop('expiresAt', None)
        mapped.pop('context', None)

        print(f'  [MemoryV3] {emp_id} 恢复归档记忆到 daily: {mem.get("value", "")[:50]}...', flush=True)
        self._send_json(200, {
            'success': True,
            'data': mapped
        })

    def _handle_archive_memory_cleanup(self, emp_id):
        """POST /api/memory/{empId}/archive — 手动触发归档过期日常记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        # v3：load_memory 内部已自动归档过期项
        data = ms3.load_memory(emp_id)
        archived = 0
        archive_data = ms3.load_archive(emp_id)
        self._send_json(200, {'archived': len(archive_data.get('archived', [])), 'empId': emp_id})

    def _handle_get_core_candidates(self, emp_id):
        """GET /api/memory/{empId}/core-candidates — 获取核心记忆候选列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        candidates = ms3.get_pending_core_candidates(emp_id)
        self._send_json(200, {
            'empId': emp_id,
            'candidates': candidates,
            'total': len(candidates)
        })

    def _handle_confirm_core_candidate(self, emp_id, cand_id):
        """POST /api/memory/{empId}/core-candidates/{candId}/confirm"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        cand = ms3.update_core_candidate_status(emp_id, cand_id, 'confirmed')
        if not cand:
            self._send_json_error(404, 'Candidate not found')
            return
        try:
            confirm_cfg = get_embedding_config(emp_id)
            new_mem = ms3.add_memory(
                emp_id,
                value=cand['value'],
                key='core',
                source='candidate',
                priority=8,
                tags=['AI提炼'],
                api_key=confirm_cfg['apiKey'],
                provider=confirm_cfg['provider'],
                model=confirm_cfg['model'],
                base_url=confirm_cfg['baseUrl']
            )
            # 归档源 daily 记忆
            ms3.archive_source_memories_as_promoted(emp_id, cand.get('sourceIds', []))
        except Exception as e:
            print(f'  [CoreCandidate] confirm failed: {e}', flush=True)
            self._send_json_error(500, f'Confirm failed: {str(e)}')
            return
        self._send_json(200, {
            'success': True,
            'candidate': cand,
            'memory': new_mem
        })

    def _handle_dismiss_core_candidate(self, emp_id, cand_id):
        """POST /api/memory/{empId}/core-candidates/{candId}/dismiss"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        cand = ms3.update_core_candidate_status(emp_id, cand_id, 'dismissed')
        if not cand:
            self._send_json_error(404, 'Candidate not found')
            return
        self._send_json(200, {'success': True, 'candidate': cand})

    def _handle_induct_to_knowledge(self, emp_id):
        """POST /api/memory/{empId}/induct-to-knowledge — 手动触发知识归纳"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin and auth.user_id != emp_id:
            self._send_auth_error('Permission denied', 403)
            return
        agent = _get_agent_by_id(emp_id)
        if not agent:
            self._send_json(404, {'error': 'Agent not found'})
            return
        try:
            count, reason = _induct_knowledge_for_agent(agent, owner_user_id=auth.user_id)
        except Exception as e:
            print(f'  [InductKnowledge] manual failed: {e}', flush=True)
            self._send_json_error(500, f'Induction failed: {str(e)}')
            return
        self._send_json(200, {
            'success': True,
            'createdDocs': count,
            'reason': reason,
            'empId': emp_id
        })

    def _handle_archive_inducted(self, emp_id):
        """POST /api/memory/{empId}/archive-inducted — 归档所有已归纳的活跃记忆"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin and auth.user_id != emp_id:
            self._send_auth_error('Permission denied', 403)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return
        try:
            archived_ids = ms3.archive_inducted_memories(emp_id)
        except Exception as e:
            print(f'  [ArchiveInducted] failed: {e}', flush=True)
            self._send_json_error(500, f'Archive failed: {str(e)}')
            return
        self._send_json(200, {
            'success': True,
            'archivedIds': archived_ids,
            'empId': emp_id
        })

    def _handle_get_merge_history(self, emp_id):
        """GET /api/memory/{empId}/merge-history — 获取去重合并记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        logs = ms3.get_duplicate_merge_logs(emp_id, limit=limit)
        self._send_json(200, {'success': True, 'empId': emp_id, 'merges': logs})

    def _handle_get_conflicts(self, emp_id):
        """GET /api/memory/{empId}/conflicts — 获取核心记忆冲突列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        data = ms3.load_memory(emp_id)
        conflicts = [m for m in data.get('core', []) if m.get('conflictStatus') == 'conflict']
        self._send_json(200, {'success': True, 'empId': emp_id, 'conflicts': conflicts})

    def _handle_detect_conflicts(self, emp_id):
        """POST /api/memory/{empId}/detect-conflicts — 手动触发冲突检测"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        agent = _get_agent_by_id(emp_id)
        if not agent:
            self._send_json(404, {'error': 'Agent not found'})
            return
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        if not emb_cfg['apiKey']:
            self._send_json_error(400, 'Agent has no API key, cannot detect conflicts')
            return

        def _ai_resolve(prompt, system_prompt):
            return _call_ai_for_json(prompt, agent, system_prompt=system_prompt)

        try:
            detected = ms3.detect_core_memory_conflicts(emp_id, emb_cfg['apiKey'], emb_cfg['provider'], _ai_resolve)
            self._send_json(200, {'success': True, 'empId': emp_id, 'detected': detected})
        except Exception as e:
            print(f'  [DetectConflicts] failed: {e}', flush=True)
            self._send_json_error(500, f'Detect failed: {str(e)}')

    def _handle_resolve_conflict(self, emp_id, mem_id):
        """POST /api/memory/{empId}/{memId}/resolve-conflict — 解决冲突"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        body = self._read_body() or {}
        resolution = body.get('resolution', '')
        try:
            mem = ms3.resolve_memory_conflict(emp_id, mem_id, resolution=resolution)
            if not mem:
                self._send_json_error(404, 'Memory not found')
                return
            self._send_json(200, {'success': True, 'empId': emp_id, 'memory': mem})
        except Exception as e:
            print(f'  [ResolveConflict] failed: {e}', flush=True)
            self._send_json_error(500, f'Resolve failed: {str(e)}')

    # FIXME: 大脑知识中枢 API 处理器
    def _handle_get_brain_status(self):
        """GET /api/brain/status — 返回大脑处理状态"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            stats = _brain_scheduler.get_stats()
            self._send_json(200, {'success': True, **stats})
        except Exception as e:
            print(f'  [BrainAPI] status failed: {e}', flush=True)
            self._send_json_error(500, f'Status failed: {str(e)}')

    def _handle_brain_trigger_manual(self):
        """POST /api/brain/trigger-manual — 手动触发全量处理"""
        # FIXME: 修复大脑手动触发接口鉴权：确保和其他 /api/ 接口使用相同的登录态校验
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            enqueued_clean, enqueued_classify, enqueued_induct = _brain_scheduler.enqueue_all_pending()
            self._send_json(200, {
                'success': True,
                'enqueuedClean': enqueued_clean,
                'enqueuedClassify': enqueued_classify,
                'enqueuedInduct': enqueued_induct
            })
        except Exception as e:
            print(f'  [BrainAPI] trigger failed: {e}', flush=True)
            self._send_json_error(500, f'Trigger failed: {str(e)}')

    def _handle_get_brain_topics(self):
        """GET /api/brain/topics?empId=xxx — 获取员工的主题列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        emp_id = qs.get('empId', [''])[0]
        if not emp_id:
            self._send_json_error(400, 'Missing empId')
            return
        try:
            topics = _brain_scheduler._topic_svc.get_emp_topics(emp_id, limit=100)
            self._send_json(200, {'success': True, 'empId': emp_id, 'topics': topics})
        except Exception as e:
            print(f'  [BrainAPI] topics failed: {e}', flush=True)
            self._send_json_error(500, f'Topics failed: {str(e)}')

    def _handle_get_brain_knowledge(self):
        """GET /api/brain/knowledge?topicId=xxx — 获取主题下的知识"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        topic_id = qs.get('topicId', [''])[0]
        if not topic_id:
            self._send_json_error(400, 'Missing topicId')
            return
        try:
            knowledge = _brain_scheduler._know_svc.get_knowledge_by_topic(topic_id, limit=100)
            self._send_json(200, {'success': True, 'topicId': topic_id, 'knowledge': knowledge})
        except Exception as e:
            print(f'  [BrainAPI] knowledge failed: {e}', flush=True)
            self._send_json_error(500, f'Knowledge failed: {str(e)}')

    def _handle_brain_knowledge_feedback(self, knowledge_id):
        """POST /api/brain/knowledge/{kid}/feedback — 准确/有误反馈"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        accurate = body.get('accurate', True)
        try:
            ok = _brain_scheduler._know_svc.feedback_knowledge(knowledge_id, accurate=accurate)
            self._send_json(200, {'success': ok})
        except Exception as e:
            print(f'  [BrainAPI] feedback failed: {e}', flush=True)
            self._send_json_error(500, f'Feedback failed: {str(e)}')

    # FIXME: 记忆三级沉淀 API：二级归纳（daily/project） + 三级知识库查询/标记
    def _handle_get_daily_summary(self, emp_id):
        """GET /api/memory/{empId}/daily-summary?date=YYYY-MM-DD"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        date = qs.get('date', [''])[0]
        keyword = qs.get('keyword', [''])[0]
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        summaries = _load_memory_summaries(emp_id, summary_type='daily', date=date, keyword=keyword, limit=limit)
        self._send_json(200, {'success': True, 'empId': emp_id, 'date': date, 'summaries': summaries})

    def _handle_get_project_summary(self, emp_id):
        """GET /api/memory/{empId}/project-summary?project=xxx"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        project = qs.get('project', [''])[0]
        keyword = qs.get('keyword', [''])[0]
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50
        summaries = _load_memory_summaries(emp_id, summary_type='project', project_name=project, keyword=keyword, limit=limit)
        self._send_json(200, {'success': True, 'empId': emp_id, 'project': project, 'summaries': summaries})

    def _handle_trigger_summary(self, emp_id):
        """POST /api/memory/{empId}/trigger-summary — 手动触发/保存归纳结果"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin and auth.user_id != emp_id:
            self._send_json_error('Permission denied', 403)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return
        body = self._read_body() or {}
        summary = body.get('summary') or body
        summary['empId'] = emp_id
        try:
            sid = _save_memory_summary(summary)
        except Exception as e:
            print(f'  [SummaryTrigger] save failed: {e}', flush=True)
            self._send_json_error(500, f'Save summary failed: {str(e)}')
            return
        self._send_json(200, {'success': True, 'empId': emp_id, 'summaryId': sid})

    def _handle_get_agent_knowledge_base(self, emp_id):
        """GET /api/memory/{empId}/knowledge — 查询该员工三级知识库"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        agent, err, status = self._check_agent_access(auth, emp_id)
        if err:
            self._send_json(status, {'error': err})
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        keyword = qs.get('keyword', [''])[0]
        status_filter = qs.get('status', [''])[0]
        try:
            limit = max(1, min(500, int(qs.get('limit', ['200'])[0])))
        except ValueError:
            limit = 200
        entries = _load_knowledge_base(emp_id, keyword=keyword, status=status_filter, limit=limit)
        self._send_json(200, {'success': True, 'empId': emp_id, 'entries': entries})

    def _handle_post_agent_knowledge_base(self, emp_id):
        """POST /api/memory/{empId}/knowledge — 手动标记记忆为知识库"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin and auth.user_id != emp_id:
            self._send_json_error('Permission denied', 403)
            return
        body = self._read_body() or {}
        mem_id = body.get('memId')
        title = body.get('title')
        content = body.get('content')
        if not title or not content:
            # 允许只传 memId，从记忆中取内容
            if mem_id:
                data = ms3.load_memory(emp_id)
                found = None
                for m in data.get('core', []) + data.get('daily', []):
                    if m.get('id') == mem_id:
                        found = m
                        break
                if found:
                    content = found.get('value', '')
                    title = found.get('value', '')[:40]
                else:
                    self._send_json_error(404, 'Memory not found')
                    return
            else:
                self._send_json_error(400, 'Missing title/content or memId')
                return
        try:
            kb_id = _upsert_knowledge_base({
                'empId': emp_id,
                'title': title,
                'content': content,
                'source': body.get('source', 'manual'),
                'tags': body.get('tags', []),
                'relatedMemIds': [mem_id] if mem_id else [],
                'status': 'active'
            })
        except Exception as e:
            print(f'  [KnowledgeBase] manual mark failed: {e}', flush=True)
            self._send_json_error(500, f'Mark knowledge failed: {str(e)}')
            return
        self._send_json(200, {'success': True, 'empId': emp_id, 'knowledgeId': kb_id})

    # ═══════════════════════════════════════════════════
    # 知识库 API（后端持久化，替代 localStorage sb_docs）
    # ═══════════════════════════════════════════════════

    def _load_knowledge(self):
        """加载全局知识库文档列表"""
        filepath = os.path.join(KNOWLEDGE_DIR, 'index.json')
        return _read_json(filepath, {'docs': [], 'version': '1.0'})

    def _save_knowledge(self, data):
        """保存全局知识库文档列表"""
        filepath = os.path.join(KNOWLEDGE_DIR, 'index.json')
        data['version'] = '1.0'
        _write_json(filepath, data)

    def _handle_get_knowledge(self):
        """GET /api/knowledge — 获取知识库列表（支持分页、分类、关键词、scope 四层隔离：all/global/team/personal/group）
        项目组维度支持 scope=group，以及 groupId / groupIds 过滤参数。"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        offset = max(0, int(qs.get('offset', [0])[0]))
        limit = max(1, min(100, int(qs.get('limit', [20])[0])))  # 默认20条
        category = qs.get('category', [''])[0] or None
        keyword = qs.get('q', [''])[0] or None
        scope = qs.get('scope', [''])[0] or None
        team_id = qs.get('teamId', [''])[0] or None
        group_id = qs.get('groupId', [''])[0] or None
        group_ids_param = qs.get('groupIds', [''])[0] or ''
        target_emp_id = qs.get('empId', [''])[0] or None  # 兼容旧参数

        allowed_cats = _allowed_knowledge_categories(auth)
        # 如果用户请求了具体分类，校验是否有权限
        if category and not _can_access_knowledge_category(auth, category):
            self._send_json(200, {'docs': [], 'total': 0, 'offset': offset, 'limit': limit})
            return

        # 解析并校验项目组过滤参数
        requested_group_ids = []
        if group_id:
            requested_group_ids.append(group_id)
        if group_ids_param:
            requested_group_ids.extend([g.strip() for g in group_ids_param.split(',') if g.strip()])
        if auth.is_admin:
            effective_group_ids = requested_group_ids or auth.group_ids
        else:
            allowed = set(auth.group_ids)
            effective_group_ids = [g for g in requested_group_ids if g in allowed] if requested_group_ids else list(allowed)

        result = ks.knowledge_list(
            offset=offset, limit=limit, category=category, keyword=keyword,
            allowed_categories=allowed_cats,
            scope=scope, team_id=team_id, user_id=auth.user_id,
            is_admin=auth.is_admin, user_team_ids=auth.team_ids,
            user_group_ids=effective_group_ids,
            emp_id=target_emp_id
        )
        self._send_json(200, result)

    def _handle_get_knowledge_detail(self, kid):
        """GET /api/knowledge/<id> — 单条知识详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(kid)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        # 权限检查
        if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids, user_group_ids=auth.group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        self._send_json(200, doc)

    def _handle_get_knowledge_search(self):
        """GET /api/knowledge/search?q=xxx&limit=3 — 语义检索（带三层隔离）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        query = qs.get('q', [''])[0]
        limit = min(10, max(1, int(qs.get('limit', [3])[0])))
        if not query:
            self._send_json_error(400, 'Missing query param q')
            return

        target_emp_id = qs.get('empId', [''])[0]  # 空表示全局（用于 embedding 配置）

        # 获取 API key 和 provider（全局知识库使用当前用户 agent 配置，支持全局 embedding 配置）
        agent = _get_agent_by_id(target_emp_id or auth.user_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        if not api_key:
            self._send_json_error(400, 'No API key available. Please configure AI provider.')
            return
        agent_config = dict(agent) if agent else None
        if agent_config and emb_cfg.get('model'):
            agent_config['embeddingModel'] = emb_cfg['model']

        try:
            allowed_cats = _allowed_knowledge_categories(auth)
            docs = ks.knowledge_search_semantic(
                query, target_emp_id, api_key, provider, agent_config,
                limit, allowed_categories=allowed_cats,
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'),
                requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,
                group_ids=auth.group_ids
            )
            self._send_json(200, {'query': query, 'docs': docs, 'count': len(docs)})
        except Exception as e:
            print(f'  [KnowledgeSearch] failed: {e}', flush=True)
            self._send_json_error(500, f'Search failed: {str(e)}')

    def _handle_post_knowledge(self):
        """POST /api/knowledge — 新增全局公共知识（自动分段+向量化）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        # 兼容旧前端：name 字段映射为 title
        title = body.get('title') or body.get('name')
        if not body or not title or 'content' not in body:
            self._send_json_error(400, 'Missing title or content')
            return

        scope = body.get('scope', 'global')
        team_id = body.get('teamId') or ''
        group_ids = body.get('groupIds') or body.get('group_ids') or body.get('groupId') or []
        if isinstance(group_ids, str):
            group_ids = [g.strip() for g in group_ids.split(',') if g.strip()]
        if scope == 'group' and not group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        # 兼容旧前端传入 empId
        emp_id = body.get('empId') or ''
        if scope == 'personal' and not emp_id:
            emp_id = auth.user_id
        if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,
                                       team_id=team_id, user_team_ids=auth.team_ids,
                                       managed_team_ids=auth.managed_team_ids,
                                       group_ids=group_ids, user_group_ids=auth.group_ids,
                                       managed_group_ids=auth.managed_group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        category = body.get('category', '')
        if not _can_access_knowledge_category(auth, category):
            self._send_auth_error('No permission for this knowledge category', 403)
            return

        # 获取 API key 和 agent 配置（支持全局 embedding 配置）
        agent = _get_agent_by_id(auth.user_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        agent_config = dict(agent) if agent else None
        if agent_config and emb_cfg.get('model'):
            agent_config['embeddingModel'] = emb_cfg['model']

        try:
            doc = ks.knowledge_create(
                title=title,
                content=body['content'],
                category=body.get('category', ''),
                emp_id=emp_id,
                api_key=api_key,
                provider=provider,
                agent_config=agent_config,
                model=emb_cfg.get('model'),
                base_url=emb_cfg.get('baseUrl'),
                scope=scope,
                team_id=team_id,
                group_ids=group_ids,
            )
            self._send_json(200, doc)
        except Exception as e:
            print(f'  [Knowledge] create failed: {e}', flush=True)
            self._send_json_error(500, f'Create failed: {str(e)}')

    def _handle_put_knowledge(self, doc_id):
        """PUT /api/knowledge/{docId} — 更新全局公共知识（自动重新分段+向量化）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return

        # 先查出原知识，检查权限
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                     managed_team_ids=auth.managed_team_ids,
                                     managed_group_ids=auth.managed_group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        # 分类权限：必须对原文档分类有权限，且不能修改到无权限的分类
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        new_category = body.get('category')
        if new_category is not None and not _can_access_knowledge_category(auth, new_category):
            self._send_auth_error('No permission for target knowledge category', 403)
            return

        # 获取 API key 和 agent 配置（支持全局 embedding 配置）
        emp_id = doc.get('empId') or ''
        agent = _get_agent_by_id(auth.user_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        agent_config = dict(agent) if agent else None
        if agent_config and emb_cfg.get('model'):
            agent_config['embeddingModel'] = emb_cfg['model']

        # 兼容旧前端：name 字段映射为 title
        title = body.get('title') or body.get('name')
        new_scope = body.get('scope')
        new_team_id = body.get('teamId')
        new_group_ids = body.get('groupIds') or body.get('group_ids') or body.get('groupId') or []
        if isinstance(new_group_ids, str):
            new_group_ids = [g.strip() for g in new_group_ids.split(',') if g.strip()]
        if new_scope == 'group' and not new_group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        # 允许更新 empId（旧前端兼容）
        new_emp_id = body.get('empId')
        if new_emp_id is not None:
            emp_id = new_emp_id
        # 变更 scope / teamId / group_ids 时，校验目标权限
        target_scope = new_scope if new_scope is not None else doc.get('scope') or 'global'
        target_team_id = new_team_id if new_team_id is not None else doc.get('teamId') or ''
        target_group_ids = new_group_ids if (new_group_ids or new_scope == 'group') else (doc.get('groupIds') or [])
        if new_scope is not None or new_team_id is not None or new_group_ids:
            if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,
                                           team_id=target_team_id, user_team_ids=auth.team_ids,
                                           managed_team_ids=auth.managed_team_ids,
                                           group_ids=target_group_ids, user_group_ids=auth.group_ids,
                                           managed_group_ids=auth.managed_group_ids):
                self._send_auth_error('Permission denied for target scope', 403)
                return
        try:
            updated = ks.knowledge_update(
                kid=doc_id,
                title=title,
                content=body.get('content'),
                category=body.get('category'),
                emp_id=emp_id,
                api_key=api_key,
                provider=provider,
                agent_config=agent_config,
                created_by=auth.user_id,
                model=emb_cfg.get('model'),
                base_url=emb_cfg.get('baseUrl'),
                scope=new_scope,
                team_id=new_team_id,
                group_ids=new_group_ids,
            )
            self._send_json(200, updated)
        except Exception as e:
            print(f'  [Knowledge] update failed: {e}', flush=True)
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_knowledge(self, doc_id):
        """DELETE /api/knowledge/{docId} — 删除知识"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        # 检查权限
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                       managed_team_ids=auth.managed_team_ids,
                                       managed_group_ids=auth.managed_group_ids,
                                       user_group_ids=auth.group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        deleted = ks.knowledge_delete(doc_id)
        self._send_json(200, {'deleted': deleted, 'id': doc_id})

    def _handle_get_knowledge_versions(self, doc_id):
        """GET /api/knowledge/<id>/versions — 获取历史版本列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        offset = max(0, int(qs.get('offset', [0])[0]))
        limit = max(1, min(100, int(qs.get('limit', [20])[0])))
        result = ks.knowledge_get_versions(doc_id, offset, limit)
        self._send_json(200, result)

    def _handle_get_knowledge_version(self, doc_id, version):
        """GET /api/knowledge/<id>/versions/<version> — 获取某一历史版本"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        try:
            version = int(version)
        except ValueError:
            self._send_json_error(400, 'Invalid version')
            return
        v = ks.knowledge_get_version(doc_id, version)
        if not v:
            self._send_json_error(404, 'Version not found')
            return
        if not _can_access_knowledge_category(auth, v.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        self._send_json(200, v)

    def _handle_knowledge_rollback(self, doc_id):
        """POST /api/knowledge/<id>/rollback — 回滚到指定版本"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                     managed_team_ids=auth.managed_team_ids,
                                     managed_group_ids=auth.managed_group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        body = self._read_body() or {}
        version = body.get('version')
        if version is None:
            self._send_json_error(400, 'Missing version')
            return
        try:
            version = int(version)
        except ValueError:
            self._send_json_error(400, 'Invalid version')
            return

        agent = _get_agent_by_id(auth.user_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        agent_config = dict(agent) if agent else None
        if agent_config and emb_cfg.get('model'):
            agent_config['embeddingModel'] = emb_cfg['model']

        try:
            rolled = ks.knowledge_rollback(
                doc_id, version,
                api_key=api_key,
                provider=provider,
                agent_config=agent_config,
                created_by=auth.user_id,
                model=emb_cfg.get('model'),
                base_url=emb_cfg.get('baseUrl')
            )
            if not rolled:
                self._send_json_error(404, 'Rollback target not found')
                return
            self._send_json(200, {'success': True, 'knowledge': rolled})
        except Exception as e:
            print(f'  [KnowledgeRollback] failed: {e}', flush=True)
            self._send_json_error(500, f'Rollback failed: {str(e)}')

    def _handle_knowledge_move(self, doc_id):
        """POST /api/knowledge/{docId}/move — 移动知识到指定 scope/team"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(doc_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        # 原知识编辑权限
        if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                     managed_team_ids=auth.managed_team_ids,
                                     managed_group_ids=auth.managed_group_ids):
            self._send_auth_error('Permission denied', 403)
            return
        body = self._read_body() or {}
        new_scope = body.get('scope')
        new_team_id = body.get('teamId') or ''
        new_group_ids = body.get('groupIds') or body.get('group_ids') or body.get('groupId') or []
        if isinstance(new_group_ids, str):
            new_group_ids = [g.strip() for g in new_group_ids.split(',') if g.strip()]
        if new_scope == 'group' and not new_group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        if new_scope not in ('global', 'team', 'personal', 'group'):
            self._send_json_error(400, 'Invalid scope')
            return
        # 目标 scope 创建权限
        if not ks.can_create_knowledge(new_scope, auth.user_id, is_admin=auth.is_admin,
                                       team_id=new_team_id, user_team_ids=auth.team_ids,
                                       managed_team_ids=auth.managed_team_ids,
                                       group_ids=new_group_ids, user_group_ids=auth.group_ids,
                                       managed_group_ids=auth.managed_group_ids):
            self._send_auth_error('Permission denied for target scope', 403)
            return
        try:
            moved = ks.knowledge_move(doc_id, new_scope, new_team_id, group_ids=new_group_ids, moved_by=auth.user_id)
            self._send_json(200, {'success': True, 'knowledge': moved})
        except Exception as e:
            print(f'  [KnowledgeMove] failed: {e}', flush=True)
            self._send_json_error(500, f'Move failed: {str(e)}')

    # ═══════════════════════════════════════════════════
    # 新版知识库 API（重构后）
    # ═══════════════════════════════════════════════════

    def _handle_get_kb_entries(self):
        """GET /api/knowledge/entries — 新版知识库列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        offset = max(0, int(qs.get('offset', [0])[0]))
        limit = max(1, min(100, int(qs.get('limit', [20])[0])))
        category = qs.get('category', [''])[0] or None
        keyword = qs.get('q', [''])[0] or None
        scope = qs.get('scope', [''])[0] or None
        team_id = qs.get('teamId', [''])[0] or None
        group_id = qs.get('groupId', [''])[0] or None
        group_ids_param = qs.get('groupIds', [''])[0] or ''
        created_by = qs.get('createdBy', [''])[0] or None

        allowed_cats = _allowed_knowledge_categories(auth)
        if category and not _can_access_knowledge_category(auth, category):
            self._send_json(200, {'docs': [], 'total': 0, 'offset': offset, 'limit': limit})
            return

        requested_group_ids = []
        if group_id:
            requested_group_ids.append(group_id)
        if group_ids_param:
            requested_group_ids.extend([g.strip() for g in group_ids_param.split(',') if g.strip()])
        if auth.is_admin:
            effective_group_ids = requested_group_ids or auth.group_ids
        else:
            allowed = set(auth.group_ids)
            effective_group_ids = [g for g in requested_group_ids if g in allowed] if requested_group_ids else list(allowed)

        try:
            result = ks.kb_entry_list(
                offset=offset, limit=limit, category=category, keyword=keyword,
                allowed_categories=allowed_cats,
                scope=scope, team_id=team_id, user_id=auth.user_id,
                is_admin=auth.is_admin, user_team_ids=auth.team_ids,
                user_group_ids=effective_group_ids,
                created_by=created_by,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            self._send_json(200, result)
        except Exception as e:
            print(f'  [KBEntries] list failed: {e}', flush=True)
            self._send_json_error(500, f'List failed: {str(e)}')

    def _handle_get_kb_entry_detail(self, entry_id):
        """GET /api/knowledge/entries/<id> — 新版知识详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.kb_entry_get_by_id(entry_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids, user_group_ids=auth.group_ids, emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        self._send_json(200, doc)

    def _handle_post_kb_entry(self):
        """POST /api/knowledge/entries — 创建新版知识"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        title = body.get('title') or body.get('name')
        if not body or not title or 'content' not in body:
            self._send_json_error(400, 'Missing title or content')
            return

        scope = body.get('scope', 'global')
        team_id = body.get('teamId') or ''
        group_ids = body.get('groupIds') or body.get('group_ids') or body.get('groupId') or []
        if isinstance(group_ids, str):
            group_ids = [g.strip() for g in group_ids.split(',') if g.strip()]
        if scope == 'group' and not group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        emp_id = body.get('empId') or ''
        if scope == 'personal' and not emp_id:
            emp_id = auth.user_id
        if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,
                                       team_id=team_id, user_team_ids=auth.team_ids,
                                       managed_team_ids=auth.managed_team_ids,
                                       group_ids=group_ids, user_group_ids=auth.group_ids,
                                       managed_group_ids=auth.managed_group_ids,
                                       emp_id=emp_id, emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        category = body.get('category', '')
        if not _can_access_knowledge_category(auth, category):
            self._send_auth_error('No permission for this knowledge category', 403)
            return

        agent = _get_agent_by_id(auth.user_id)
        agent_config = dict(agent) if agent else None
        try:
            doc = ks.kb_entry_create(
                title=title,
                content=body['content'],
                category=category,
                created_by=auth.user_id,
                scope=scope,
                team_id=team_id,
                group_ids=group_ids,
                emp_id=emp_id,
                agent_config=agent_config,
            )
            self._send_json(200, doc)
        except Exception as e:
            print(f'  [KBEntry] create failed: {e}', flush=True)
            self._send_json_error(500, f'Create failed: {str(e)}')

    def _handle_put_kb_entry(self, entry_id):
        """PUT /api/knowledge/entries/<id> — 更新新版知识"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return

        doc = ks.kb_entry_get_by_id(entry_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_edit_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                     managed_team_ids=auth.managed_team_ids,
                                     managed_group_ids=auth.managed_group_ids,
                                     emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        new_category = body.get('category')
        if new_category is not None and not _can_access_knowledge_category(auth, new_category):
            self._send_auth_error('No permission for target knowledge category', 403)
            return

        group_ids = body.get('groupIds') or body.get('group_ids') or body.get('groupId')
        if isinstance(group_ids, str):
            group_ids = [g.strip() for g in group_ids.split(',') if g.strip()]

        new_scope = body.get('scope')
        new_team_id = body.get('teamId')
        target_scope = new_scope if new_scope is not None else doc.get('scope') or 'global'
        target_team_id = new_team_id if new_team_id is not None else doc.get('teamId') or ''
        target_group_ids = group_ids if group_ids is not None else (doc.get('groupIds') or [])
        if new_scope is not None or new_team_id is not None or group_ids is not None:
            if not ks.can_create_knowledge(target_scope, auth.user_id, is_admin=auth.is_admin,
                                           team_id=target_team_id, user_team_ids=auth.team_ids,
                                           managed_team_ids=auth.managed_team_ids,
                                           group_ids=target_group_ids, user_group_ids=auth.group_ids,
                                           managed_group_ids=auth.managed_group_ids):
                self._send_auth_error('Permission denied for target scope', 403)
                return

        title = body.get('title') or body.get('name')
        agent = _get_agent_by_id(auth.user_id)
        agent_config = dict(agent) if agent else None
        try:
            updated = ks.kb_entry_update(
                entry_id=entry_id,
                title=title,
                content=body.get('content'),
                category=body.get('category'),
                scope=new_scope,
                team_id=new_team_id,
                group_ids=group_ids,
                emp_id=body.get('empId'),
                created_by=auth.user_id,
                agent_config=agent_config,
                is_admin=auth.is_admin,
            )
            self._send_json(200, updated)
        except Exception as e:
            print(f'  [KBEntry] update failed: {e}', flush=True)
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_kb_entry(self, entry_id):
        """DELETE /api/knowledge/entries/<id> — 删除新版知识"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.kb_entry_get_by_id(entry_id)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        if not ks.can_delete_knowledge(doc, auth.user_id, is_admin=auth.is_admin,
                                       managed_team_ids=auth.managed_team_ids,
                                       managed_group_ids=auth.managed_group_ids,
                                       user_group_ids=auth.group_ids,
                                       emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        try:
            deleted = ks.kb_entry_delete(entry_id, is_admin=auth.is_admin)
            self._send_json(200, {'success': deleted, 'id': entry_id})
        except Exception as e:
            print(f'  [KBEntry] delete failed: {e}', flush=True)
            self._send_json_error(500, f'Delete failed: {str(e)}')

    def _handle_get_kb_categories(self):
        """GET /api/knowledge/categories — 分类统计"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        scope = qs.get('scope', [''])[0] or None
        allowed_cats = _allowed_knowledge_categories(auth)
        try:
            cats = ks.kb_entry_categories(
                allowed_categories=allowed_cats,
                scope=scope, user_id=auth.user_id,
                is_admin=auth.is_admin, user_team_ids=auth.team_ids,
                user_group_ids=auth.group_ids,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            total = sum(c['count'] for c in cats)
            self._send_json(200, {'categories': cats, 'total': total})
        except Exception as e:
            print(f'  [KBCategories] failed: {e}', flush=True)
            self._send_json_error(500, f'Categories failed: {str(e)}')

    def _handle_get_kb_stats(self):
        """GET /api/knowledge/stats — 统计面板"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        scope = qs.get('scope', [''])[0] or None
        allowed_cats = _allowed_knowledge_categories(auth)
        try:
            stats = ks.kb_entry_stats(
                allowed_categories=allowed_cats,
                scope=scope, user_id=auth.user_id,
                is_admin=auth.is_admin, user_team_ids=auth.team_ids,
                user_group_ids=auth.group_ids,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            self._send_json(200, {'stats': stats})
        except Exception as e:
            print(f'  [KBStats] failed: {e}', flush=True)
            self._send_json_error(500, f'Stats failed: {str(e)}')

    def _handle_post_kb_search(self):
        """POST /api/knowledge/search — 新版语义搜索"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        if not body or not body.get('query'):
            self._send_json_error(400, 'Missing query')
            return
        query = body['query']
        limit = min(50, max(1, int(body.get('limit', [10])[0]) if isinstance(body.get('limit'), list) else body.get('limit', 10)))
        scope = body.get('scope') or None
        category = body.get('category') or None
        allowed_cats = _allowed_knowledge_categories(auth)
        if category and not _can_access_knowledge_category(auth, category):
            self._send_json(200, {'query': query, 'docs': [], 'count': 0})
            return
        try:
            docs = ks.kb_entry_search_semantic(
                query=query, limit=limit,
                allowed_categories=allowed_cats,
                scope=scope, category=category,
                user_id=auth.user_id, is_admin=auth.is_admin,
                user_team_ids=auth.team_ids, user_group_ids=auth.group_ids,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            self._send_json(200, {'query': query, 'docs': docs, 'count': len(docs)})
        except Exception as e:
            print(f'  [KBSearch] failed: {e}', flush=True)
            self._send_json_error(500, f'Search failed: {str(e)}')

    def _handle_get_stats_compute(self):
        """GET /api/stats/compute — 真实 Token/调用统计"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        group_by = qs.get('groupBy', ['agent'])[0] or 'agent'

        user_emp_ids = _get_user_emp_ids(auth.user_id)
        where = []
        params = []
        if not auth.is_admin:
            placeholders = ', '.join('?' for _ in user_emp_ids) if user_emp_ids else None
            if group_by == 'agent' and placeholders:
                where.append(f'(agent_id IN ({placeholders}) OR user_id = ?)')
                params.extend(user_emp_ids)
                params.append(auth.user_id)
            else:
                where.append('user_id = ?')
                params.append(auth.user_id)

        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''
        conn = _db_conn()
        try:
            total_row = conn.execute(
                f'SELECT COALESCE(SUM(total_tokens),0) AS t, COUNT(*) AS c FROM token_usage {where_sql}',
                tuple(params)
            ).fetchone()
            total_tokens = total_row['t'] or 0
            total_calls = total_row['c'] or 0

            group_col = 'agent_id' if group_by == 'agent' else 'user_id'
            rows = conn.execute(
                f'''SELECT {group_col} AS gid,
                           COALESCE(SUM(prompt_tokens),0) AS input_tokens,
                           COALESCE(SUM(completion_tokens),0) AS output_tokens,
                           COALESCE(SUM(total_tokens),0) AS total_tokens,
                           COUNT(*) AS calls
                    FROM token_usage {where_sql}
                    GROUP BY {group_col}''',
                tuple(params)
            ).fetchall()

            agents_map = {a.get('id'): a for a in _load_agents(include_archived=True)}
            users_map = {u.get('id'): u for u in _load_users()}
            employee_stats = []
            for r in rows:
                gid = r['gid'] or ''
                name = '未知'
                if group_by == 'agent':
                    agent = agents_map.get(gid)
                    if agent:
                        name = agent.get('name') or gid
                    else:
                        user = users_map.get(gid)
                        if user:
                            name = user.get('displayName') or user.get('username') or gid
                else:
                    user = users_map.get(gid)
                    if user:
                        name = user.get('displayName') or user.get('username') or gid
                employee_stats.append({
                    'id': gid,
                    'name': name,
                    'inputTokens': r['input_tokens'] or 0,
                    'outputTokens': r['output_tokens'] or 0,
                    'tokens': r['total_tokens'] or 0,
                    'calls': r['calls'] or 0,
                })

            # 近 7 天（本地时间）
            time_rows = conn.execute(
                f'''SELECT date(created_at/1000, 'unixepoch', 'localtime') AS d,
                           COALESCE(SUM(total_tokens),0) AS tokens,
                           COUNT(*) AS calls
                    FROM token_usage {where_sql}
                    GROUP BY d ORDER BY d DESC LIMIT 7''',
                tuple(params)
            ).fetchall()
            day_map = {r['d']: {'tokens': r['tokens'] or 0, 'calls': r['calls'] or 0} for r in time_rows}
            from datetime import datetime, timedelta
            time_stats = []
            weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
            for i in range(6, -1, -1):
                d = datetime.now() - timedelta(days=i)
                d_str = d.strftime('%Y-%m-%d')
                time_stats.append({
                    'date': weekdays[d.weekday()],
                    'tokens': day_map.get(d_str, {}).get('tokens', 0),
                    'calls': day_map.get(d_str, {}).get('calls', 0),
                })
        finally:
            conn.close()

        self._send_json(200, {
            'totalTokens': total_tokens,
            'totalCalls': total_calls,
            'employeeStats': employee_stats,
            'timeStats': time_stats,
        })

    # ═══════════════════════════════════════════════════
    # RAG API
    # ═══════════════════════════════════════════════════

    def _handle_post_rag_retrieve(self):
        """POST /api/rag/retrieve — RAG 向量检索（全局知识库 + 产品库）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body()
        if not body or 'query' not in body:
            self._send_json_error(400, 'Missing query')
            return
        query = body['query']
        emp_id = body.get('empId') or ''  # 空表示全局知识库
        top_k = min(10, max(1, body.get('topK', 3)))

        # 权限检查：只要登录即可使用 RAG，具体文档隔离由 rag_retrieve 内部按 scope 过滤
        if not self._require_module_permission(auth, 'knowledge'):
            return

        # 获取 API key 和 provider（全局知识库使用当前用户配置，支持全局 embedding 配置）
        agent = _get_agent_by_id(auth.user_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        if not api_key:
            self._send_json_error(400, 'No API key available for embedding. Please configure AI provider in employee settings.')
            return
        agent_config = dict(agent) if agent else None
        if agent_config and emb_cfg.get('model'):
            agent_config['embeddingModel'] = emb_cfg['model']

        try:
            allowed_cats = _allowed_knowledge_categories(auth)
            result = ks.rag_retrieve(
                query, emp_id, api_key, provider, agent_config,
                top_k_docs=top_k, allowed_categories=allowed_cats,
                model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'),
                requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,
                group_ids=auth.group_ids
            )
            # 同时检索产品库（所有员工共享，从 SQLite 读取）
            conn = _db_conn()
            try:
                rows = conn.execute('SELECT * FROM products WHERE status != ?', ('archived',)).fetchall()
                products = [_product_row_to_dict(r) for r in rows]
            finally:
                conn.close()
            try:
                query_emb = ks.get_embedding(query, api_key, provider, model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))
            except Exception as e:
                print(f'  [RAG] product embedding query failed: {e}', flush=True)
                query_emb = None
            if query_emb:
                product_scores = []
                for product in products:
                    emb = load_embedding('product', product.get('id', ''))
                    if emb:
                        score = ks.cosine_similarity(query_emb, emb)
                        if score > 0.0:
                            product_scores.append((score, product))
                product_scores.sort(key=lambda x: x[0], reverse=True)
                result['products'] = [p for _, p in product_scores[:top_k]]
            self._send_json(200, result)
        except Exception as e:
            print(f'  [RAG] retrieve failed: {e}', flush=True)
            import traceback; traceback.print_exc()
            self._send_json_error(500, f'RAG retrieve failed: {str(e)}')

    def _handle_post_rag_build(self):
        """POST /api/rag/build — 批量构建所有 embedding 索引"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        emp_id = body.get('empId')
        agent = _get_agent_by_id(emp_id)
        emb_cfg = get_embedding_config((agent or {}).get('id'))
        api_key = emb_cfg['apiKey']
        provider = emb_cfg['provider']
        if not api_key:
            self._send_json_error(400, 'No API key available')
            return
        try:
            build_all_embeddings(api_key, provider, model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))
            self._send_json(200, {'success': True, 'message': 'Embedding index built'})
        except Exception as e:
            print(f'  [RAG] build failed: {e}', flush=True)
            self._send_json_error(500, f'Build failed: {str(e)}')

    # ═══════════════════════════════════════════════════
    # 商品库 API
    # ═══════════════════════════════════════════════════

    def _load_products(self):
        """从 SQLite 加载全部商品（返回兼容旧格式的 dict）"""
        conn = _db_conn()
        try:
            rows = conn.execute('SELECT * FROM products ORDER BY updated_at DESC').fetchall()
            products = [_product_row_to_dict(r) for r in rows]
            return {'products': products, 'total': len(products), 'version': '1.0'}
        finally:
            conn.close()

    def _save_products(self, data):
        """保留签名兼容；商品库已迁移到 SQLite，此函数不再执行文件写入"""
        pass

    def _handle_get_products(self):
        """GET /api/products — 获取商品列表（支持 query 筛选）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        data = self._load_products()
        products = data.get('products', [])
        # 解析 query string 做筛选
        query = parse_qs(urlparse(self.path).query)
        if query.get('category'):
            cat = query['category'][0]
            products = [p for p in products if p.get('category') == cat]
        if query.get('brand'):
            brand = query['brand'][0]
            products = [p for p in products if p.get('brand') == brand]
        if query.get('status'):
            status = query['status'][0]
            products = [p for p in products if p.get('status') == status]
        if query.get('q'):
            kw = query['q'][0].lower()
            products = [p for p in products if kw in (p.get('id') or '').lower()
                        or kw in (p.get('name') or '').lower()
                        or kw in (p.get('description') or '').lower()
                        or kw in (p.get('brand') or '').lower()
                        or kw in (p.get('category') or '').lower()
                        or any(kw in t.lower() for t in (p.get('tags') or []))]
        # 分页
        offset = int(query.get('offset', [0])[0])
        limit = int(query.get('limit', [50])[0])
        total = len(products)
        products = products[offset:offset + limit]
        self._send_json(200, {'products': products, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_product(self, product_id):
        """GET /api/products/:id — 获取单个商品详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            product = _product_row_to_dict(row)
        finally:
            conn.close()
        if not product:
            self._send_json_error(404, 'Product not found')
            return
        self._send_json(200, product)

    def _handle_get_product_matches(self, product_id):
        """GET /api/products/:id/matches — 获取商品的匹配达人列表
        优先读取商品自身的 matched_influencers，为空或超24小时则重新计算并缓存"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        # 从 SQLite 加载商品
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            product = _product_row_to_dict(row)
        finally:
            conn.close()
        if not product:
            self._send_json_error(404, 'Product not found')
            return
        query = parse_qs(urlparse(self.path).query)
        limit = int(query.get('limit', [20])[0])
        now = int(time.time() * 1000)
        DAY_MS = 86400000
        ai_analysis = product.get('ai_analysis') or {}
        stored = ai_analysis.get('matched_influencers') or product.get('matched_influencers')
        last_updated = ai_analysis.get('matched_influencers_updated_at', 0)
        is_fresh = stored and last_updated and (now - last_updated) < DAY_MS
        if is_fresh:
            results = []
            for item in stored:
                results.append({
                    'influencer': {
                        'id': item.get('id'),
                        'name': item.get('name'),
                        'platform': item.get('platform'),
                        'followerCount': item.get('followerCount'),
                    },
                    'score': item.get('score', item.get('matchPercent', 0)),
                    'reasons': item.get('reasons', [])
                })
            self._send_json(200, {'product_id': product_id, 'matches': results[:limit], 'total': len(results), 'source': 'cached'})
            return
        # 缓存为空或过期：实时计算并保存
        inf_data = self._load_influencers()
        results = []
        for inf in inf_data.get('influencers', []):
            score, reasons = self._calculate_match_score(product, inf)
            results.append({'influencer': inf, 'score': score, 'reasons': reasons})
        results.sort(key=lambda x: x['score'], reverse=True)
        # 保存计算结果到商品（用于缓存）
        cached_matches = []
        for r in results:
            inf = r['influencer']
            cached_matches.append({
                'id': inf.get('id'),
                'name': inf.get('name'),
                'platform': inf.get('platform'),
                'followerCount': inf.get('followerCount'),
                'score': r['score'],
                'matchPercent': r['score'],
                'reasons': r['reasons']
            })
        ai_analysis['matched_influencers'] = cached_matches
        ai_analysis['matched_influencers_updated_at'] = now
        conn = _db_conn()
        try:
            conn.execute(
                'UPDATE products SET ai_analysis = ?, updated_at = ? WHERE id = ?',
                (json.dumps(ai_analysis, ensure_ascii=False), now, product_id)
            )
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'product_id': product_id, 'matches': results[:limit], 'total': len(results), 'source': 'live'})

    def _handle_get_influencer_matches(self, inf_id):
        """GET /api/influencers/:id/matches — 获取达人的匹配商品列表
        优先读取达人自身的 matched_products，为空或超24小时则重新计算并缓存"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        data = self._load_influencers()
        influencer = next((i for i in data.get('influencers', []) if i.get('id') == inf_id), None)
        if not influencer:
            detail_path = os.path.join(INFLUENCER_DIR, f'{inf_id}.json')
            if os.path.exists(detail_path):
                influencer = _read_json(detail_path, None)
        if not influencer:
            self._send_json_error(404, 'Influencer not found')
            return
        query = parse_qs(urlparse(self.path).query)
        limit = int(query.get('limit', [20])[0])
        now = int(time.time() * 1000)
        DAY_MS = 86400000
        stored = influencer.get('matched_products')
        last_updated = influencer.get('matched_products_updated_at', 0)
        is_fresh = stored and last_updated and (now - last_updated) < DAY_MS
        if is_fresh:
            results = []
            for item in stored:
                results.append({
                    'product': {
                        'id': item.get('id'),
                        'name': item.get('name'),
                        'category': item.get('category'),
                        'price': item.get('price'),
                    },
                    'score': item.get('score', item.get('matchPercent', 0)),
                    'reasons': item.get('reasons', [])
                })
            self._send_json(200, {'influencer_id': inf_id, 'matches': results[:limit], 'total': len(results), 'source': 'cached'})
            return
        # 缓存为空或过期：实时计算并保存
        prod_data = self._load_products()
        results = []
        for prod in prod_data.get('products', []):
            score, reasons = self._calculate_match_score(prod, influencer)
            results.append({'product': prod, 'score': score, 'reasons': reasons})
        results.sort(key=lambda x: x['score'], reverse=True)
        # 保存计算结果到达人（用于缓存）
        cached_matches = []
        for r in results:
            prod = r['product']
            cached_matches.append({
                'id': prod.get('id'),
                'name': prod.get('name'),
                'category': prod.get('category'),
                'price': prod.get('price'),
                'score': r['score'],
                'matchPercent': r['score'],
                'reasons': r['reasons']
            })
        influencer['matched_products'] = cached_matches
        influencer['matched_products_updated_at'] = now
        influencer['updatedAt'] = now
        self._save_influencers(data)
        self._sync_influencer_file(influencer)
        self._send_json(200, {'influencer_id': inf_id, 'matches': results[:limit], 'total': len(results), 'source': 'live'})

    def _handle_post_product(self):
        """POST /api/products — 录入商品（仅当 name+brand 完全一致时算重复）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body or 'name' not in body:
            self._send_json_error(400, 'Missing name')
            return

        name = str(body.get('name', '')).strip()
        brand = str(body.get('brand') or '').strip()

        # 去重检查：仅当名称和品牌均非空且完全一致时才算重复
        conn = _db_conn()
        existing = None
        try:
            if name and brand:
                existing = conn.execute(
                    "SELECT * FROM products WHERE LOWER(name) = LOWER(?) AND LOWER(brand) = LOWER(?) LIMIT 1",
                    (name, brand)
                ).fetchone()
            if existing:
                result = _product_row_to_dict(existing)
                result['duplicate'] = True
                result['can_update'] = True
                result['message'] = f"该商品（名称：{name}，品牌：{brand}）已存在，是否需要更新信息？"
                self._send_json(200, result)
                return
        finally:
            conn.close()

        now_ts = int(time.time() * 1000)
        product = dict(body)
        product.setdefault('id', f'prod_{now_ts}_{uuid.uuid4().hex[:6]}')
        product.setdefault('createdAt', now_ts)
        product.setdefault('updatedAt', now_ts)
        # 兼容旧字段 commission_rate -> commission_rates
        if 'commission_rate' in body and 'commission_rates' not in body:
            product['commission_rates'] = {'default': float(body['commission_rate'])}
        # 自动计算佣金金额：commission_amount = price * commission_rate / 100
        if 'commission_rate' in body and 'commission_amount' not in body:
            product['commission_amount'] = round(float(body.get('price', 0) or 0) * float(body['commission_rate']) / 100, 2)
        row = _dict_to_product_row(product)
        row['created_at'] = row['created_at'] or now_ts
        row['updated_at'] = row['updated_at'] or now_ts
        conn = _db_conn()
        try:
            _sync_product_brand(conn, row)
            conn.execute(
                f"INSERT INTO products ({', '.join(_PRODUCT_COLUMNS)}) VALUES ({', '.join('?' * len(_PRODUCT_COLUMNS))})",
                tuple(row[c] for c in _PRODUCT_COLUMNS)
            )
            conn.commit()
            if row.get('brand_id'):
                _update_brand_product_stats(conn, row['brand_id'])
                conn.commit()
            row_out = conn.execute('SELECT * FROM products WHERE id = ?', (row['id'],)).fetchone()
            product_out = _product_row_to_dict(row_out)
        finally:
            conn.close()
        print(f'  [Product] 录入商品: {product_out["name"]} ({product_out["id"]})', flush=True)
        self._send_json(200, product_out)

    def _handle_put_product(self, product_id):
        """PUT /api/products/{id} — 更新商品"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            existing = _product_row_to_dict(row)
        finally:
            conn.close()
        if not existing:
            self._send_json_error(404, 'Product not found')
            return
        now_ts = int(time.time() * 1000)
        updated = dict(existing)
        updated.update(body)
        updated['id'] = product_id
        updated['updatedAt'] = now_ts
        # 兼容旧字段 commission_rate -> commission_rates
        if 'commission_rate' in body and 'commission_rates' not in body:
            updated['commission_rates'] = {'default': float(body['commission_rate'])}
        # 自动计算佣金金额（当 price 或 commission_rate 变更且未显式提供 commission_amount 时）
        if ('price' in body or 'commission_rate' in body) and 'commission_amount' not in body:
            price = float(updated.get('price', 0) or 0)
            rate = float(updated.get('commission_rate', 0) or 0)
            updated['commission_amount'] = round(price * rate / 100, 2)
        row = _dict_to_product_row(updated)
        row['created_at'] = existing['created_at']
        row['updated_at'] = now_ts
        conn = _db_conn()
        try:
            old_brand_id = existing.get('brand_id')
            _sync_product_brand(conn, row)
            conn.execute(
                f"UPDATE products SET {', '.join(f'{c} = ?' for c in _PRODUCT_COLUMNS)} WHERE id = ?",
                tuple(row[c] for c in _PRODUCT_COLUMNS) + (product_id,)
            )
            conn.commit()
            for bid in {b for b in [old_brand_id, row.get('brand_id')] if b}:
                _update_brand_product_stats(conn, bid)
            conn.commit()
            row_out = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            product_out = _product_row_to_dict(row_out)
        finally:
            conn.close()
        print(f'  [Product] 更新商品: {product_out["name"]} ({product_id})', flush=True)
        self._send_json(200, product_out)

    def _handle_delete_product(self, product_id):
        """DELETE /api/products/{id} — 删除商品（硬删除，符合常规 CRUD 语义）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        conn = _db_conn()
        try:
            cur = conn.execute('DELETE FROM products WHERE id = ?', (product_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            conn.close()
        # 同步清理可能存在的 embedding 缓存文件
        if deleted:
            try:
                cache_path = _get_embedding_cache_path('product', product_id)
                if os.path.exists(cache_path):
                    os.remove(cache_path)
            except Exception:
                pass
        self._send_json(200, {'deleted': deleted, 'id': product_id})

    def _handle_search_products(self):
        """POST /api/products/search — 高级搜索/匹配"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        data = self._load_products()
        products = data.get('products', [])
        results = []
        for p in products:
            score = 0
            matched = []
            # 名称匹配
            if body.get('name'):
                name_kw = body['name'].lower()
                if name_kw in (p.get('name') or '').lower():
                    score += 10
                    matched.append('name')
            # 分类匹配
            if body.get('category'):
                if body['category'] == p.get('category'):
                    score += 8
                    matched.append('category')
            # 标签匹配
            if body.get('tags'):
                search_tags = set(t.lower() for t in (body['tags'] if isinstance(body['tags'], list) else [body['tags']]))
                product_tags = set(t.lower() for t in (p.get('tags') or []))
                tag_match = search_tags & product_tags
                if tag_match:
                    score += len(tag_match) * 5
                    matched.append('tags:' + ','.join(tag_match))
            # 价格区间
            if body.get('minPrice') is not None and p.get('price', 0) < float(body['minPrice']):
                continue
            if body.get('maxPrice') is not None and p.get('price', 0) > float(body['maxPrice']):
                continue
            # 属性匹配
            if body.get('attributes'):
                attrs_match = True
                for k, v in body['attributes'].items():
                    if str(p.get('attributes', {}).get(k, '')).lower() != str(v).lower():
                        attrs_match = False
                        break
                if attrs_match:
                    score += 6
                    matched.append('attributes')
            # SKU 精确匹配
            if body.get('sku'):
                if body['sku'].lower() == (p.get('sku') or '').lower():
                    score += 15
                    matched.append('sku')
            # 状态过滤
            if body.get('status') and p.get('status') != body['status']:
                continue
            if score > 0 or not any(k in body for k in ('name', 'category', 'tags', 'sku', 'attributes')):
                results.append({'product': p, 'score': score, 'matched': matched})
        # 按匹配度排序
        results.sort(key=lambda x: x['score'], reverse=True)
        limit = int(body.get('limit', 20))
        self._send_json(200, {'results': results[:limit], 'total': len(results)})

    def _handle_analyze_product_ai(self, product_id):
        """POST /api/products/:id/analyze — 调用 AI 生成选品分析"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return

        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            product = _product_row_to_dict(row)
        finally:
            conn.close()
        if not product:
            self._send_json_error(404, 'Product not found')
            return

        cfg = get_embedding_config()
        # 优先使用 kimicode；未配置时回退到全局 embedding provider
        cfg['provider'] = 'kimicode' if cfg['provider'] == 'kimicode' else (cfg['provider'] or 'kimicode')
        cfg['model'] = cfg['model'] or _resolve_ai_model(cfg['provider'], '')
        cfg['baseUrl'] = cfg['baseUrl'] or _resolve_ai_base_url(cfg['provider'], '')

        prompt = (
            f"请为以下商品做选品分析，只返回 JSON，不要返回其他内容。\n"
            f"JSON 格式：{{\"ai_score\": 1-5 的整数, \"competition_analysis\": \"...\", \"selection_advice\": \"...\"}}\n\n"
            f"商品名称：{product.get('name', '')}\n"
            f"品牌：{product.get('brand', '')}\n"
            f"分类：{product.get('category', '')}\n"
            f"价格：¥{product.get('price', 0)}\n"
            f"月销量：{product.get('monthly_sales', 0)}\n"
            f"月 GMV：¥{product.get('monthly_gmv', 0)}\n"
            f"佣金策略：{json.dumps(product.get('commission_rates', {}), ensure_ascii=False)}\n"
            f"转化率：{product.get('conversion_rate', 0)}%\n"
            f"受众画像：{json.dumps(product.get('audience', {}), ensure_ascii=False)}\n"
        )
        messages = [
            {'role': 'system', 'content': '你是电商选品分析助手，擅长根据商品数据给出结构化分析。'},
            {'role': 'user', 'content': prompt}
        ]
        content = _call_ai_analysis(messages, cfg=cfg, context='product_analyze')
        if not content:
            self._send_json_error(503, 'AI analysis failed or returned empty response')
            return

        # 解析 JSON（兼容 markdown 代码块、冗余文本）
        analysis = _extract_json_object(content)
        if not isinstance(analysis, dict):
            print(f'  [Analyze] product_analyze AI response is not a valid JSON object: {content[:1000]}', flush=True)
            self._send_json_error(503, 'AI response is not valid JSON')
            return

        now_ts = int(time.time() * 1000)
        conn = _db_conn()
        try:
            conn.execute(
                'UPDATE products SET ai_analysis = ?, updated_at = ? WHERE id = ?',
                (json.dumps(analysis, ensure_ascii=False), now_ts, product_id)
            )
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'id': product_id, 'ai_analysis': analysis})

    # ═══════════════════════════════════════════════════
    # 品牌库 API
    # ═══════════════════════════════════════════════════

    def _handle_get_brands(self):
        """GET /api/brands — 获取品牌列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        query = parse_qs(urlparse(self.path).query)
        status = query.get('status', ['active'])[0]
        q = query.get('q', [''])[0].lower()
        conn = _db_conn()
        try:
            # 自动修复 brands 表结构（兼容旧 DB）
            for _brand_col, _brand_dtype in [
                ('logo', "TEXT DEFAULT ''"),
                ('shop_score', 'REAL DEFAULT 0'),
                ('shop_type', "TEXT DEFAULT ''"),
                ('main_category', "TEXT DEFAULT ''"),
                ('total_products', 'INTEGER DEFAULT 0'),
                ('total_talents', 'INTEGER DEFAULT 0'),
                ('avg_commission', 'REAL DEFAULT 0'),
                ('group_id', "TEXT DEFAULT ''"),
                ('status', "TEXT DEFAULT 'active'"),
                ('created_at', 'INTEGER'),
                ('updated_at', 'INTEGER'),
            ]:
                _add_column_if_not_exists(conn, 'brands', _brand_col, _brand_dtype)

            sql = "SELECT * FROM brands WHERE 1=1"
            params = []
            if status:
                sql += " AND status = ?"
                params.append(status)
            if q:
                sql += " AND (LOWER(name) LIKE ? OR LOWER(main_category) LIKE ?)"
                params.extend([f'%{q}%', f'%{q}%'])
            sql += " ORDER BY updated_at DESC"
            print(f'[DEBUG] GET /api/brands SQL: {sql} params={params}', flush=True)
            rows = conn.execute(sql, params).fetchall()
            print(f'[DEBUG] GET /api/brands rows={len(rows)}', flush=True)
            brands = [_brand_row_to_dict(r) for r in rows]
            # Fallback：brands 表为空时，从 products 表聚合生成品牌列表，兼容旧数据
            if not brands:
                agg_rows = conn.execute('''
                    SELECT brand, COUNT(*) as count
                    FROM products
                    WHERE status != ? AND brand IS NOT NULL AND brand != ''
                    GROUP BY brand
                    ORDER BY count DESC, brand ASC
                ''', ('archived',)).fetchall()
                now = int(time.time() * 1000)
                brands = [{
                    'id': '',
                    'name': r['brand'] or '',
                    'logo': '',
                    'shop_score': 0,
                    'shop_type': '',
                    'main_category': '',
                    'total_products': r['count'],
                    'total_talents': 0,
                    'avg_commission': 0,
                    'group_id': '',
                    'status': 'active',
                    'created_at': now,
                    'updated_at': now,
                    'createdAt': now,
                    'updatedAt': now,
                } for r in agg_rows]
            self._send_json(200, {'brands': brands, 'total': len(brands)})
        except Exception as e:
            print(f'[ERROR] GET /api/brands failed: {e}', flush=True)
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': f'获取品牌列表失败: {str(e)}'})
        finally:
            conn.close()

    def _handle_get_brand(self, brand_id):
        """GET /api/brands/:id — 获取单个品牌"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM brands WHERE id = ?', (brand_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            self._send_json_error(404, 'Brand not found')
            return
        self._send_json(200, _brand_row_to_dict(row))

    def _handle_post_brand(self):
        """POST /api/brands — 创建品牌"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body or not body.get('name'):
            self._send_json_error(400, 'Missing name')
            return
        row = _dict_to_brand_row(body)
        conn = _db_conn()
        try:
            conn.execute(
                f"INSERT INTO brands ({', '.join(_BRAND_COLUMNS)}) VALUES ({', '.join('?' * len(_BRAND_COLUMNS))})",
                tuple(row[c] for c in _BRAND_COLUMNS)
            )
            conn.commit()
            row_out = conn.execute('SELECT * FROM brands WHERE id = ?', (row['id'],)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _brand_row_to_dict(row_out))

    def _handle_put_brand(self, brand_id):
        """PUT /api/brands/:id — 更新品牌"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM brands WHERE id = ?', (brand_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Brand not found')
                return
            existing = _brand_row_to_dict(row)
            existing.update(body)
            existing['id'] = brand_id
            existing['updated_at'] = int(time.time() * 1000)
            row = _dict_to_brand_row(existing)
            conn.execute(
                f"UPDATE brands SET {', '.join(f'{c} = ?' for c in _BRAND_COLUMNS)} WHERE id = ?",
                tuple(row[c] for c in _BRAND_COLUMNS) + (brand_id,)
            )
            conn.commit()
            row_out = conn.execute('SELECT * FROM brands WHERE id = ?', (brand_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _brand_row_to_dict(row_out))

    def _handle_delete_brand(self, brand_id):
        """DELETE /api/brands/:id — 删除品牌"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        conn = _db_conn()
        try:
            cur = conn.execute('DELETE FROM brands WHERE id = ?', (brand_id,))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'deleted': cur.rowcount > 0, 'id': brand_id})

    # ═══════════════════════════════════════════════════
    # 达人库 API (SQLite)
    # ═══════════════════════════════════════════════════

    def _handle_get_talents(self):
        """GET /api/talents — 获取达人列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        query = parse_qs(urlparse(self.path).query)
        q = query.get('q', [''])[0].lower()
        cooperation = query.get('cooperation', [''])[0]
        category = query.get('category', [''])[0]
        status = query.get('status', ['active'])[0]
        offset = int(query.get('offset', ['0'])[0])
        limit = int(query.get('limit', ['50'])[0])
        conn = _db_conn()
        try:
            sql = "SELECT * FROM talents WHERE 1=1"
            params = []
            if status:
                sql += " AND status = ?"
                params.append(status)
            if cooperation:
                sql += " AND cooperation_status = ?"
                params.append(cooperation)
            if category:
                sql += " AND fan_category = ?"
                params.append(category)
            if q:
                sql += " AND (LOWER(name) LIKE ? OR LOWER(douyin_id) LIKE ? OR LOWER(bio) LIKE ?)"
                params.extend([f'%{q}%', f'%{q}%', f'%{q}%'])
            sql += " ORDER BY followers DESC"
            rows = conn.execute(sql, params).fetchall()
            total = len(rows)
            talents = [_talent_row_to_dict(r) for r in rows[offset:offset + limit]]
        finally:
            conn.close()
        self._send_json(200, {'talents': talents, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_talent(self, talent_id):
        """GET /api/talents/:id — 获取达人详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            self._send_json_error(404, 'Talent not found')
            return
        self._send_json(200, _talent_row_to_dict(row))

    def _handle_post_talent(self):
        """POST /api/talents — 录入达人（仅当 douyin_id 完全一致时算重复）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body or not body.get('name'):
            self._send_json_error(400, 'Missing name')
            return

        name = str(body.get('name', '')).strip()
        douyin_id = str(body.get('douyin_id') or body.get('douyinId') or '').strip()

        # 去重检查：仅当抖音号非空且完全一致时才算重复
        conn = _db_conn()
        existing = None
        try:
            if douyin_id:
                existing = conn.execute(
                    "SELECT * FROM talents WHERE LOWER(douyin_id) = LOWER(?) LIMIT 1",
                    (douyin_id,)
                ).fetchone()
            if existing:
                result = _talent_row_to_dict(existing)
                result['duplicate'] = True
                result['can_update'] = True
                result['message'] = f"该达人（抖音号{douyin_id}）已存在，是否需要更新信息？"
                self._send_json(200, result)
                return
        finally:
            conn.close()

        row = _dict_to_talent_row(body)
        conn = _db_conn()
        try:
            conn.execute(
                f"INSERT INTO talents ({', '.join(_TALENT_COLUMNS)}) VALUES ({', '.join('?' * len(_TALENT_COLUMNS))})",
                tuple(row[c] for c in _TALENT_COLUMNS)
            )
            conn.commit()
            if row.get('group_id'):
                _update_brand_product_stats(conn, row['group_id'])
                conn.commit()
            row_out = conn.execute('SELECT * FROM talents WHERE id = ?', (row['id'],)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _talent_row_to_dict(row_out))

    def _handle_put_talent(self, talent_id):
        """PUT /api/talents/:id — 更新达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Talent not found')
                return
            existing = _talent_row_to_dict(row)
            existing.update(body)
            existing['id'] = talent_id
            existing['updated_at'] = int(time.time() * 1000)
            row = _dict_to_talent_row(existing)
            conn.execute(
                f"UPDATE talents SET {', '.join(f'{c} = ?' for c in _TALENT_COLUMNS)} WHERE id = ?",
                tuple(row[c] for c in _TALENT_COLUMNS) + (talent_id,)
            )
            conn.commit()
            if row.get('group_id'):
                _update_brand_product_stats(conn, row['group_id'])
                conn.commit()
            row_out = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _talent_row_to_dict(row_out))

    def _handle_delete_talent(self, talent_id):
        """DELETE /api/talents/:id — 删除达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        conn = _db_conn()
        try:
            talent = conn.execute('SELECT group_id FROM talents WHERE id = ?', (talent_id,)).fetchone()
            cur = conn.execute('DELETE FROM talents WHERE id = ?', (talent_id,))
            conn.execute('DELETE FROM product_talent_match WHERE talent_id = ?', (talent_id,))
            conn.commit()
            if talent and talent['group_id']:
                _update_brand_product_stats(conn, talent['group_id'])
                conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'deleted': cur.rowcount > 0, 'id': talent_id})

    def _handle_get_talent_follow_ups(self, talent_id):
        """GET /api/talents/:id/follow-ups — 获取跟进记录列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        conn = _db_conn()
        try:
            exists = conn.execute('SELECT 1 FROM talents WHERE id = ?', (talent_id,)).fetchone()
            if not exists:
                self._send_json_error(404, 'Talent not found')
                return
            rows = conn.execute(
                'SELECT * FROM talent_follow_ups WHERE talent_id = ? ORDER BY follow_up_at DESC',
                (talent_id,)
            ).fetchall()
            follow_ups = [_follow_up_row_to_dict(r) for r in rows]
        finally:
            conn.close()
        self._send_json(200, {'follow_ups': follow_ups})

    def _handle_post_talent_follow_up(self, talent_id):
        """POST /api/talents/:id/follow-ups — 新增跟进记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            exists = conn.execute('SELECT 1 FROM talents WHERE id = ?', (talent_id,)).fetchone()
            if not exists:
                self._send_json_error(404, 'Talent not found')
                return
            body['talent_id'] = talent_id
            row = _dict_to_follow_up_row(body)
            conn.execute(
                f"INSERT INTO talent_follow_ups ({', '.join(_FOLLOW_UP_COLUMNS)}) VALUES ({', '.join('?' * len(_FOLLOW_UP_COLUMNS))})",
                tuple(row[c] for c in _FOLLOW_UP_COLUMNS)
            )
            conn.commit()
            # 同步达人最近跟进人与下次跟进时间
            conn.execute(
                'UPDATE talents SET follow_up_by = ?, next_follow_up_at = ?, updated_at = ? WHERE id = ?',
                (row['follow_up_by'], row['next_follow_up_at'], row['updated_at'], talent_id)
            )
            conn.commit()
            row_out = conn.execute('SELECT * FROM talent_follow_ups WHERE id = ?', (row['id'],)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _follow_up_row_to_dict(row_out))

    def _handle_put_talent_follow_up(self, talent_id, follow_up_id):
        """PUT /api/talents/:id/follow-ups/:follow_up_id — 更新跟进记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            row = conn.execute(
                'SELECT * FROM talent_follow_ups WHERE id = ? AND talent_id = ?', (follow_up_id, talent_id)
            ).fetchone()
            if not row:
                self._send_json_error(404, 'Follow-up not found')
                return
            existing = _follow_up_row_to_dict(row)
            existing.update(body)
            existing['id'] = follow_up_id
            existing['talent_id'] = talent_id
            existing['updated_at'] = int(time.time() * 1000)
            row = _dict_to_follow_up_row(existing)
            conn.execute(
                f"UPDATE talent_follow_ups SET {', '.join(f'{c} = ?' for c in _FOLLOW_UP_COLUMNS)} WHERE id = ?",
                tuple(row[c] for c in _FOLLOW_UP_COLUMNS) + (follow_up_id,)
            )
            conn.commit()
            conn.execute(
                'UPDATE talents SET follow_up_by = ?, next_follow_up_at = ?, updated_at = ? WHERE id = ?',
                (row['follow_up_by'], row['next_follow_up_at'], row['updated_at'], talent_id)
            )
            conn.commit()
            row_out = conn.execute('SELECT * FROM talent_follow_ups WHERE id = ?', (follow_up_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _follow_up_row_to_dict(row_out))

    def _handle_delete_talent_follow_up(self, talent_id, follow_up_id):
        """DELETE /api/talents/:id/follow-ups/:follow_up_id — 删除跟进记录"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        conn = _db_conn()
        try:
            cur = conn.execute(
                'DELETE FROM talent_follow_ups WHERE id = ? AND talent_id = ?', (follow_up_id, talent_id)
            )
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'deleted': cur.rowcount > 0, 'id': follow_up_id})

    # ═══════════════════════════════════════════════════
    # 达人库 API (旧 JSON 兼容)
    # ═══════════════════════════════════════════════════

    def _load_influencers(self):
        """加载达人库索引"""
        filepath = os.path.join(INFLUENCER_DIR, 'index.json')
        return _read_json(filepath, {'influencers': [], 'version': '1.0'})

    def _save_influencers(self, data):
        """保存达人库索引"""
        filepath = os.path.join(INFLUENCER_DIR, 'index.json')
        data['version'] = '1.0'
        _write_json(filepath, data)

    def _sync_influencer_file(self, influencer):
        """同步单个达人详情到独立文件 {id}.json"""
        filepath = os.path.join(INFLUENCER_DIR, f'{influencer["id"]}.json')
        _write_json(filepath, influencer)

    def _remove_influencer_file(self, inf_id):
        """删除单个达人详情文件"""
        filepath = os.path.join(INFLUENCER_DIR, f'{inf_id}.json')
        if os.path.exists(filepath):
            os.remove(filepath)

    def _handle_get_influencers(self):
        """GET /api/influencers — 获取达人列表（支持 query 筛选）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        data = self._load_influencers()
        influencers = data.get('influencers', [])
        query = parse_qs(urlparse(self.path).query)
        if query.get('platform'):
            platform = query['platform'][0]
            influencers = [i for i in influencers if i.get('platform') == platform]
        if query.get('category'):
            cat = query['category'][0]
            influencers = [i for i in influencers if i.get('category') == cat]
        if query.get('status'):
            status = query['status'][0]
            influencers = [i for i in influencers if i.get('status') == status]
        if query.get('q'):
            kw = query['q'][0].lower()
            influencers = [i for i in influencers if kw in (i.get('id') or '').lower() or kw in (i.get('name') or '').lower() or kw in (i.get('accountId') or '').lower() or kw in (i.get('bio') or '').lower() or any(kw in t.lower() for t in (i.get('tags') or []))]
        offset = int(query.get('offset', [0])[0])
        limit = int(query.get('limit', [50])[0])
        total = len(influencers)
        influencers = influencers[offset:offset + limit]
        self._send_json(200, {'influencers': influencers, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_influencer(self, inf_id):
        """GET /api/influencers/:id — 获取单个达人详情"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        data = self._load_influencers()
        influencer = next((i for i in data.get('influencers', []) if i.get('id') == inf_id), None)
        if not influencer:
            self._send_json_error(404, 'Influencer not found')
            return
        self._send_json(200, influencer)

    def _handle_post_influencer(self):
        """POST /api/influencers — 录入达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body or 'name' not in body:
            self._send_json_error(400, 'Missing name')
            return
        data = self._load_influencers()
        influencer = {
            'id': body.get('id') or f'inf_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}',
            'name': body['name'],
            'avatar': body.get('avatar', ''),
            'platform': body.get('platform', '抖音'),
            'accountId': body.get('accountId', ''),
            'followerCount': int(body.get('followerCount', 0)),
            'category': body.get('category', '未分类'),
            'tags': body.get('tags', []),
            'bio': body.get('bio', ''),
            'contentStyle': body.get('contentStyle', ''),
            'cooperationPrice': float(body.get('cooperationPrice', 0)),
            'priceUnit': body.get('priceUnit', '元/条'),
            'contact': body.get('contact', ''),
            'status': body.get('status', 'available'),
            'engagementRate': float(body.get('engagementRate', 0)),
            'avgViews': int(body.get('avgViews', 0)),
            'lastCooperation': body.get('lastCooperation'),
            'notes': body.get('notes', ''),
            'createdBy': auth.user_info.get('userId'),
            'createdAt': int(time.time() * 1000),
            'updatedAt': int(time.time() * 1000)
        }
        data['influencers'].append(influencer)
        self._save_influencers(data)
        self._sync_influencer_file(influencer)
        print(f'  [Influencer] 录入达人: {influencer["name"]} ({influencer["id"]})', flush=True)
        self._send_json(200, influencer)

    def _handle_put_influencer(self, inf_id):
        """PUT /api/influencers/{id} — 更新达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        data = self._load_influencers()
        updated = None
        for i in data.get('influencers', []):
            if i.get('id') == inf_id:
                for field in ('name', 'avatar', 'platform', 'accountId', 'followerCount', 'category', 'tags', 'bio', 'contentStyle', 'cooperationPrice', 'priceUnit', 'contact', 'status', 'engagementRate', 'avgViews', 'lastCooperation', 'notes'):
                    if field in body:
                        i[field] = body[field]
                        if field in ('followerCount', 'avgViews'):
                            i[field] = int(body[field])
                        if field in ('cooperationPrice', 'engagementRate'):
                            i[field] = float(body[field])
                i['updatedAt'] = int(time.time() * 1000)
                updated = i
                break
        if not updated:
            self._send_json_error(404, 'Influencer not found')
            return
        self._save_influencers(data)
        self._sync_influencer_file(updated)
        self._send_json(200, updated)

    def _handle_delete_influencer(self, inf_id):
        """DELETE /api/influencers/{id} — 删除达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        data = self._load_influencers()
        original = len(data.get('influencers', []))
        data['influencers'] = [i for i in data.get('influencers', []) if i.get('id') != inf_id]
        removed = original - len(data['influencers'])
        self._save_influencers(data)
        if removed > 0:
            self._remove_influencer_file(inf_id)
        self._send_json(200, {'deleted': removed > 0, 'id': inf_id})

    def _handle_search_influencers(self):
        """POST /api/influencers/search — 高级搜索/匹配"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        data = self._load_influencers()
        influencers = data.get('influencers', [])
        results = []
        for i in influencers:
            score = 0
            matched = []
            # 名称匹配
            if body.get('name'):
                name_kw = body['name'].lower()
                if name_kw in (i.get('name') or '').lower():
                    score += 10
                    matched.append('name')
            # 账号匹配
            if body.get('accountId'):
                if body['accountId'].lower() == (i.get('accountId') or '').lower():
                    score += 12
                    matched.append('accountId')
            # 平台匹配
            if body.get('platform'):
                if body['platform'] == i.get('platform'):
                    score += 7
                    matched.append('platform')
            # 分类匹配
            if body.get('category'):
                if body['category'] == i.get('category'):
                    score += 8
                    matched.append('category')
            # 标签匹配
            if body.get('tags'):
                search_tags = set(t.lower() for t in (body['tags'] if isinstance(body['tags'], list) else [body['tags']]))
                inf_tags = set(t.lower() for t in (i.get('tags') or []))
                tag_match = search_tags & inf_tags
                if tag_match:
                    score += len(tag_match) * 5
                    matched.append('tags:' + ','.join(tag_match))
            # 粉丝数区间
            if body.get('minFollowers') is not None and i.get('followerCount', 0) < int(body['minFollowers']):
                continue
            if body.get('maxFollowers') is not None and i.get('followerCount', 0) > int(body['maxFollowers']):
                continue
            # 报价区间
            if body.get('minPrice') is not None and i.get('cooperationPrice', 0) < float(body['minPrice']):
                continue
            if body.get('maxPrice') is not None and i.get('cooperationPrice', 0) > float(body['maxPrice']):
                continue
            # 互动率下限
            if body.get('minEngagement') is not None and i.get('engagementRate', 0) < float(body['minEngagement']):
                continue
            # 状态过滤
            if body.get('status') and i.get('status') != body['status']:
                continue
            if score > 0 or not any(k in body for k in ('name', 'accountId', 'platform', 'category', 'tags')):
                results.append({'influencer': i, 'score': score, 'matched': matched})
        results.sort(key=lambda x: x['score'], reverse=True)
        limit = int(body.get('limit', 20))
        self._send_json(200, {'results': results[:limit], 'total': len(results)})

    # ═══════════════════════════════════════════════════
    # 匹配引擎
    # ═══════════════════════════════════════════════════

    def _parse_price_range(self, price_range):
        """解析商品价格区间，返回 (min, max, avg)"""
        if not price_range:
            return (0, 999999, 100)
        s = str(price_range).strip().replace(' ', '')
        import re
        m = re.match(r'^(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)$', s)
        if m:
            mn = float(m.group(1))
            mx = float(m.group(2))
            return (mn, mx, (mn + mx) / 2)
        m = re.match(r'^(\d+(?:\.\d+)?)$', s)
        if m:
            v = float(m.group(1))
            return (v, v, v)
        m = re.match(r'^(?:低于|小于|以下)?\s*(\d+(?:\.\d+)?).*$', s)
        if m:
            v = float(m.group(1))
            return (0, v, v / 2)
        m = re.match(r'^(?:高于|大于|以上)?\s*(\d+(?:\.\d+)?).*$', s)
        if m:
            v = float(m.group(1))
            return (v, 999999, v * 1.5)
        return (0, 999999, 100)

    def _calculate_match_score(self, product, influencer):
        """计算商品与达人的匹配分数 (0-100+)"""
        score = 0
        reasons = []

        # 1. 分类匹配
        if product.get('category') and influencer.get('category'):
            if product['category'] == influencer['category']:
                score += 25
                reasons.append('分类一致')
            else:
                reasons.append('分类不同')
        else:
            reasons.append('缺少分类信息')

        # 2. 标签匹配
        p_tags = set(t.lower() for t in (product.get('tags') or []))
        i_tags = set(t.lower() for t in (influencer.get('tags') or []))
        tag_common = p_tags & i_tags
        if tag_common:
            tag_score = min(len(tag_common) * 8, 24)
            score += tag_score
            reasons.append(f'标签匹配 {len(tag_common)} 个')
        else:
            reasons.append('无匹配标签')

        # 3. 价格匹配（无 priceRange 时用 price 作为回退基准）
        price_range = product.get('priceRange')
        if not price_range and product.get('price') is not None:
            p = float(product['price'])
            price_min, price_max, price_avg = (p * 0.5, p * 1.5, p)
        else:
            price_min, price_max, price_avg = self._parse_price_range(price_range)
        inf_price = influencer.get('cooperationPrice', 0) or 0
        if price_min <= inf_price <= price_max:
            score += 20
            reasons.append('报价在商品价格区间内')
        elif price_min * 0.5 <= inf_price <= price_max * 1.5:
            score += 10
            reasons.append('报价接近商品价格区间')
        else:
            reasons.append('报价与商品价格区间偏差较大')

        # 4. 粉丝数匹配（从商品定价角度看受众规模需求）
        followers = influencer.get('followerCount', 0) or 0
        if price_avg < 100:
            if followers >= 50000:
                score += 15; reasons.append('粉丝量充足')
            elif followers >= 10000:
                score += 10; reasons.append('粉丝量良好')
            elif followers >= 1000:
                score += 5; reasons.append('粉丝量一般')
            else:
                reasons.append('粉丝量较少')
        elif price_avg < 500:
            if followers >= 200000:
                score += 20; reasons.append('粉丝量非常充足')
            elif followers >= 50000:
                score += 15; reasons.append('粉丝量充足')
            elif followers >= 10000:
                score += 10; reasons.append('粉丝量良好')
            else:
                reasons.append('粉丝量偏少')
        else:
            if followers >= 500000:
                score += 25; reasons.append('头部达人，粉丝量极佳')
            elif followers >= 200000:
                score += 20; reasons.append('粉丝量非常充足')
            elif followers >= 50000:
                score += 15; reasons.append('粉丝量充足')
            else:
                reasons.append('粉丝量可能不足')

        # 5. 互动率加分
        engagement = influencer.get('engagementRate', 0) or 0
        if isinstance(engagement, str):
            engagement = engagement.replace('%', '').strip()
            try:
                engagement = float(engagement)
            except ValueError:
                engagement = 0
        if engagement > 1:
            engagement = engagement / 100
        if engagement >= 0.10:
            score += 15; reasons.append('互动率极佳 (>10%)')
        elif engagement >= 0.05:
            score += 10; reasons.append('互动率优秀 (>5%)')
        elif engagement >= 0.02:
            score += 5; reasons.append('互动率良好 (>2%)')
        else:
            reasons.append('互动率一般')

        return score, reasons

    def _handle_match_product_to_influencer(self):
        """POST /api/match/product-to-influencer — 为商品匹配达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'matches'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        product_id = body.get('productId')
        if not product_id:
            self._send_json_error(400, 'Missing productId')
            return

        pdata = self._load_products()
        product = None
        for p in pdata.get('products', []):
            if p.get('id') == product_id:
                product = p
                break
        if not product:
            self._send_json_error(404, 'Product not found')
            return

        idata = self._load_influencers()
        influencers = idata.get('influencers', [])

        results = []
        for inf in influencers:
            if inf.get('status') in ('inactive', 'blacklist'):
                continue
            score, reasons = self._calculate_match_score(product, inf)
            min_score = float(body.get('minScore', 0))
            if score >= min_score:
                results.append({
                    'influencer': inf,
                    'score': round(score, 1),
                    'reasons': reasons,
                    'matchPercent': min(100, int(score))
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        limit = int(body.get('limit', 10))
        self._send_json(200, {
            'product': product,
            'results': results[:limit],
            'total': len(results)
        })

    def _handle_match_influencer_to_product(self):
        """POST /api/match/influencer-to-product — 为达人匹配商品"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'matches'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        influencer_id = body.get('influencerId')
        if not influencer_id:
            self._send_json_error(400, 'Missing influencerId')
            return

        idata = self._load_influencers()
        influencer = None
        for i in idata.get('influencers', []):
            if i.get('id') == influencer_id:
                influencer = i
                break
        if not influencer:
            self._send_json_error(404, 'Influencer not found')
            return

        pdata = self._load_products()
        products = pdata.get('products', [])

        results = []
        for prod in products:
            if prod.get('status') in ('inactive', 'out_of_stock'):
                continue
            score, reasons = self._calculate_match_score(prod, influencer)
            min_score = float(body.get('minScore', 0))
            if score >= min_score:
                results.append({
                    'product': prod,
                    'score': round(score, 1),
                    'reasons': reasons,
                    'matchPercent': min(100, int(score))
                })

        results.sort(key=lambda x: x['score'], reverse=True)
        limit = int(body.get('limit', 10))
        self._send_json(200, {
            'influencer': influencer,
            'results': results[:limit],
            'total': len(results)
        })

    # ═══════════════════════════════════════════════════
    # 品牌达人匹配 API (V2)
    # ═══════════════════════════════════════════════════

    def _calculate_match_score_v2(self, product, talent):
        """基于规则的匹配打分：类目40 + 价格带30 + 粉丝画像20 + 佣金10 = 100"""
        score = 0
        reasons = []

        # 1. 类目匹配 (40分)
        p_cat = (product.get('category') or '').strip()
        t_cat = (talent.get('fan_category') or talent.get('category') or '').strip()
        if p_cat and t_cat:
            if p_cat == t_cat:
                score += 40
                reasons.append('类目高度一致')
            elif p_cat in t_cat or t_cat in p_cat:
                score += 25
                reasons.append('类目相关')
            else:
                p_tags = set(t.lower() for t in (product.get('tags') or []))
                t_tags = set(t.lower() for t in (talent.get('tags') or []))
                common = p_tags & t_tags
                if common:
                    score += min(len(common) * 8, 24)
                    reasons.append(f'标签匹配 {len(common)} 个')
                else:
                    reasons.append('类目关联度低')
        else:
            reasons.append('缺少类目信息')

        # 2. 价格带匹配 (30分)
        pr = product.get('priceRange') or product.get('price_range') or ''
        if not pr and product.get('price') is not None:
            pr = str(product.get('price'))
        p_min, p_max, p_avg = self._parse_price_range(pr)
        t_pr = talent.get('fan_price_range') or talent.get('priceRange') or ''
        if not t_pr and talent.get('cooperationPrice') is not None:
            t_pr = str(talent.get('cooperationPrice'))
        t_min, t_max, t_avg = self._parse_price_range(t_pr)
        if p_min <= t_avg <= p_max or t_min <= p_avg <= t_max:
            score += 30
            reasons.append('价格带完全契合')
        elif p_min * 0.5 <= t_avg <= p_max * 1.5 or t_min * 0.5 <= p_avg <= t_max * 1.5:
            score += 18
            reasons.append('价格带基本匹配')
        else:
            reasons.append('价格带偏差较大')

        # 3. 粉丝画像匹配 (20分)
        p_aud = product.get('audience') or {}
        t_gender = talent.get('fan_gender') or {}
        t_age = talent.get('fan_age') or {}
        t_region = talent.get('fan_region') or {}
        fan_score = 0
        if isinstance(p_aud, dict):
            p_gender = p_aud.get('gender') or {}
            p_age = p_aud.get('age') or {}
            p_region = p_aud.get('region') or {}
            if p_gender and t_gender:
                common_gender = set(p_gender.keys()) & set(t_gender.keys())
                if common_gender:
                    fan_score += 8
                    reasons.append('性别画像匹配')
            if p_age and t_age:
                common_age = set(p_age.keys()) & set(t_age.keys())
                if common_age:
                    fan_score += 7
                    reasons.append('年龄画像匹配')
            if p_region and t_region:
                common_region = set(p_region.keys()) & set(t_region.keys())
                if common_region:
                    fan_score += 5
                    reasons.append('地域画像匹配')
        score += min(fan_score, 20)
        if fan_score == 0:
            reasons.append('粉丝画像数据不足')

        # 4. 佣金吸引力 (10分)
        rates = product.get('commission_rates') or {}
        if isinstance(rates, dict) and rates:
            max_rate = max((v for v in rates.values() if isinstance(v, (int, float))), default=0)
        else:
            max_rate = float(product.get('commission_rate') or 0)
        t_comm = float(talent.get('commission_requirement') or 0)
        if max_rate >= t_comm:
            score += 10
            reasons.append('佣金有吸引力')
        elif max_rate >= t_comm * 0.7:
            score += 5
            reasons.append('佣金基本达标')
        else:
            reasons.append('佣金偏低')

        return min(100, score), reasons

    def _ai_match_candidates(self, source, candidates, target_type, agent, limit=10):
        """
        使用 OpenClaw/AI 对候选列表进行语义打分。
        source: 达人或商品 dict
        candidates: 候选列表（dict 列表）
        target_type: 'products' 或 'talents'
        agent: 当前 AI 员工配置 dict
        返回: {candidate_id: {'ai_score': float, 'ai_reason': str}}
        """
        if not candidates or not agent:
            return {}

        source_label = '达人' if target_type == 'products' else '商品'
        target_label = '商品' if target_type == 'products' else '达人'

        # 格式化 source 信息
        if target_type == 'products':
            source_text = (
                f"昵称：{source.get('name', '-')}\n"
                f"抖音号：{source.get('douyin_id', '-')}"
                f"主营类目：{source.get('category') or source.get('fan_category', '-')}\n"
                f"粉丝数：{source.get('followers', 0)}\n"
                f"合作等级：{source.get('level', '-')}\n"
                f"简介：{(source.get('bio') or '')[:200]}\n"
                f"标签：{', '.join(source.get('tags') or [])}"
            )
        else:
            source_text = (
                f"名称：{source.get('name', '-')}\n"
                f"品牌：{source.get('brand', '-')}\n"
                f"类目：{source.get('category', '-')}\n"
                f"价格：{source.get('price', 0)}\n"
                f"卖点：{(source.get('selling_points') or '')[:200]}\n"
                f"佣金率：{source.get('commission_rate', 0)}%\n"
                f"标签：{', '.join(source.get('tags') or [])}"
            )

        # 格式化候选列表，控制长度
        candidate_lines = []
        for idx, c in enumerate(candidates[:30], 1):
            if target_type == 'products':
                line = (
                    f"{idx}. ID:{c.get('id')} 名称:{c.get('name', '-')} "
                    f"品牌:{c.get('brand', '-')} 类目:{c.get('category', '-')} "
                    f"价格:{c.get('price', 0)} 卖点:{(c.get('selling_points') or '')[:80]} "
                    f"佣金率:{c.get('commission_rate', 0)}%"
                )
            else:
                line = (
                    f"{idx}. ID:{c.get('id')} 昵称:{c.get('name', '-')} "
                    f"抖音号:{c.get('douyin_id', '-')} 类目:{c.get('category') or c.get('fan_category', '-')} "
                    f"粉丝数:{c.get('followers', 0)} 等级:{c.get('level', '-')} "
                    f"简介:{(c.get('bio') or '')[:80]}"
                )
            candidate_lines.append(line)
        candidates_text = '\n'.join(candidate_lines)

        system_prompt = '你是一位资深电商选品与达人匹配专家，擅长根据商品和达人的多维信息做出精准匹配判断。'
        prompt = (
            f"请根据以下{source_label}信息，从候选{target_label}列表中挑选最匹配的 Top {limit}，"
            f"并给出匹配度分数（0-100）和一句不超过30字的推荐理由。\n\n"
            f"{source_label}信息：\n{source_text}\n\n"
            f"候选{target_label}列表（共{len(candidate_lines)}个）：\n{candidates_text}\n\n"
            f"要求：\n"
            f"1. 分数要体现匹配程度，100分为最匹配\n"
            f"2. 推荐理由要具体，说明为什么匹配\n"
            f"3. 只返回 JSON 数组，不要任何额外说明，格式如下：\n"
            f'[{{"id": "候选ID", "matchScore": 85, "reason": "推荐理由"}}]'
        )

        try:
            ai_result = _call_ai_for_json(prompt, agent, system_prompt=system_prompt)
            if not ai_result or not isinstance(ai_result, list):
                return {}
            scores = {}
            for item in ai_result:
                if isinstance(item, dict) and item.get('id'):
                    scores[item['id']] = {
                        'ai_score': max(0, min(100, float(item.get('matchScore', 0)))),
                        'ai_reason': str(item.get('reason', '')).strip()[:100]
                    }
            return scores
        except Exception as e:
            print(f'  [AI Match] scoring failed: {e}', flush=True)
            return {}

    def _handle_get_product_talents(self, product_id):
        """GET /api/products/:id/talents — 带该商品的Top达人排名"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        query = parse_qs(urlparse(self.path).query)
        limit = int(query.get('limit', ['20'])[0])
        conn = _db_conn()
        try:
            rows = conn.execute('''
                SELECT t.*, ptm.match_score, ptm.match_reason, ptm.sales_volume, ptm.conversion_rate, ptm.is_ai_recommended
                FROM talents t
                JOIN product_talent_match ptm ON t.id = ptm.talent_id
                WHERE ptm.product_id = ? AND t.status = 'active'
                ORDER BY ptm.sales_volume DESC, ptm.match_score DESC
                LIMIT ?
            ''', (product_id, limit)).fetchall()
            talents = []
            for r in rows:
                t = _talent_row_to_dict(r)
                t['sales_volume'] = r['sales_volume'] or 0
                t['conversion_rate'] = r['conversion_rate'] or 0
                t['match_score'] = r['match_score'] or 0
                t['is_ai_recommended'] = bool(r['is_ai_recommended'])
                talents.append(t)
        finally:
            conn.close()
        self._send_json(200, {'product_id': product_id, 'talents': talents, 'total': len(talents)})

    def _handle_get_talent_products(self, talent_id):
        """GET /api/talents/:id/products — 达人匹配商品列表"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        query = parse_qs(urlparse(self.path).query)
        limit = int(query.get('limit', ['20'])[0])
        conn = _db_conn()
        try:
            rows = conn.execute('''
                SELECT p.*, ptm.match_score, ptm.match_reason, ptm.sales_volume, ptm.conversion_rate, ptm.is_ai_recommended
                FROM products p
                JOIN product_talent_match ptm ON p.id = ptm.product_id
                WHERE ptm.talent_id = ? AND p.status = 'active'
                ORDER BY ptm.match_score DESC, ptm.sales_volume DESC
                LIMIT ?
            ''', (talent_id, limit)).fetchall()
            products = []
            for r in rows:
                p = _product_row_to_dict(r)
                p['match_score'] = r['match_score'] or 0
                p['match_reason'] = r['match_reason'] or ''
                p['sales_volume'] = r['sales_volume'] or 0
                p['conversion_rate'] = r['conversion_rate'] or 0
                p['is_ai_recommended'] = bool(r['is_ai_recommended'])
                products.append(p)
        finally:
            conn.close()
        self._send_json(200, {'talent_id': talent_id, 'products': products, 'total': len(products)})

    def _handle_match_product_talents(self, product_id):
        """POST /api/products/:id/match-talents — AI语义匹配推荐达人"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body() or {}
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            product = _product_row_to_dict(row)
        finally:
            conn.close()
        if not product:
            self._send_json_error(404, 'Product not found')
            return
        limit = int(body.get('limit', 20))
        min_score = float(body.get('minScore', 0))
        top_n_for_ai = int(body.get('aiCandidates', 30))

        # 加载当前 AI 员工配置（用于调用 OpenClaw）
        agent_id = body.get('agentId') or auth.user_id
        agent = None
        agents_data = _load_agents()
        agents = agents_data.get('agents', []) if isinstance(agents_data, dict) else agents_data
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break
        if not agent and agents:
            agent = agents[0]

        conn = _db_conn()
        try:
            talent_rows = conn.execute("SELECT * FROM talents WHERE status = 'active'").fetchall()
        finally:
            conn.close()

        # 阶段1：规则初筛
        rule_results = []
        for r in talent_rows:
            talent = _talent_row_to_dict(r)
            rule_score, rule_reasons = self._calculate_match_score_v2(product, talent)
            if rule_score < min_score:
                continue
            rule_results.append({
                'talent': talent,
                'rule_score': rule_score,
                'rule_reasons': rule_reasons
            })
        rule_results.sort(key=lambda x: x['rule_score'], reverse=True)

        # 阶段2：AI 语义打分（对前 N 个候选）
        ai_candidates = rule_results[:top_n_for_ai]
        ai_scores = {}
        if agent and ai_candidates:
            ai_scores = self._ai_match_candidates(
                product, [r['talent'] for r in ai_candidates], 'talents', agent, limit=min(limit, 10)
            )

        # 阶段3：合并规则分与 AI 分，生成最终结果
        results = []
        for r in rule_results:
            talent = r['talent']
            rule_score = r['rule_score']
            rule_reasons = r['rule_reasons']
            ai_info = ai_scores.get(talent['id'], {})
            ai_score = ai_info.get('ai_score', 0)
            ai_reason = ai_info.get('ai_reason', '')

            if ai_score > 0:
                # 40% 规则 + 60% AI
                final_score = round(rule_score * 0.4 + ai_score * 0.6, 1)
                final_reasons = ([ai_reason] if ai_reason else []) + rule_reasons[:2]
            else:
                final_score = round(rule_score, 1)
                final_reasons = rule_reasons

            results.append({
                'talent': talent,
                'score': final_score,
                'matchPercent': final_score,
                'ruleScore': rule_score,
                'aiScore': ai_score,
                'reasons': final_reasons,
                'aiReason': ai_reason,
                'is_ai_recommended': final_score >= 75
            })

        results.sort(key=lambda x: x['score'], reverse=True)

        # 阶段4：缓存推荐结果到 product_talent_match（幂等更新）
        now = int(time.time() * 1000)
        conn = _db_conn()
        try:
            for r in results[:limit]:
                t = r['talent']
                conn.execute('''
                    INSERT INTO product_talent_match (id, product_id, talent_id, match_score, match_reason, is_ai_recommended, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id, talent_id) DO UPDATE SET
                        match_score = excluded.match_score,
                        match_reason = excluded.match_reason,
                        is_ai_recommended = excluded.is_ai_recommended,
                        updated_at = excluded.updated_at
                ''', (
                    'ptm_' + str(now) + '_' + uuid.uuid4().hex[:6],
                    product_id, t['id'], r['score'], '；'.join(r['reasons'][:3]),
                    1 if r['is_ai_recommended'] else 0, now, now
                ))
            conn.commit()
            _update_product_talent_count(conn, product_id)
            if product.get('brand_id'):
                _update_brand_product_stats(conn, product.get('brand_id'))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {
            'product_id': product_id,
            'matches': results[:limit],
            'total': len(results),
            'ai_scored': len(ai_scores)
        })

    def _handle_match_talent_products(self, talent_id):
        """POST /api/talents/:id/match-products — AI语义匹配推荐商品"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        body = self._read_body() or {}
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
            talent = _talent_row_to_dict(row)
        finally:
            conn.close()
        if not talent:
            self._send_json_error(404, 'Talent not found')
            return
        limit = int(body.get('limit', 20))
        min_score = float(body.get('minScore', 0))
        top_n_for_ai = int(body.get('aiCandidates', 30))

        # 加载当前 AI 员工配置（用于调用 OpenClaw）
        agent_id = body.get('agentId') or auth.user_id
        agent = None
        agents_data = _load_agents()
        agents = agents_data.get('agents', []) if isinstance(agents_data, dict) else agents_data
        for a in agents:
            if a.get('id') == agent_id:
                agent = a
                break
        if not agent and agents:
            agent = agents[0]

        conn = _db_conn()
        try:
            product_rows = conn.execute("SELECT * FROM products WHERE status = 'active'").fetchall()
        finally:
            conn.close()

        # 阶段1：规则初筛
        rule_results = []
        for r in product_rows:
            product = _product_row_to_dict(r)
            rule_score, rule_reasons = self._calculate_match_score_v2(product, talent)
            if rule_score < min_score:
                continue
            rule_results.append({
                'product': product,
                'rule_score': rule_score,
                'rule_reasons': rule_reasons
            })
        rule_results.sort(key=lambda x: x['rule_score'], reverse=True)

        # 阶段2：AI 语义打分（对前 N 个候选）
        ai_candidates = rule_results[:top_n_for_ai]
        ai_scores = {}
        if agent and ai_candidates:
            ai_scores = self._ai_match_candidates(
                talent, [r['product'] for r in ai_candidates], 'products', agent, limit=min(limit, 10)
            )

        # 阶段3：合并规则分与 AI 分，生成最终结果
        results = []
        for r in rule_results:
            product = r['product']
            rule_score = r['rule_score']
            rule_reasons = r['rule_reasons']
            ai_info = ai_scores.get(product['id'], {})
            ai_score = ai_info.get('ai_score', 0)
            ai_reason = ai_info.get('ai_reason', '')

            if ai_score > 0:
                final_score = round(rule_score * 0.4 + ai_score * 0.6, 1)
                final_reasons = ([ai_reason] if ai_reason else []) + rule_reasons[:2]
            else:
                final_score = round(rule_score, 1)
                final_reasons = rule_reasons

            results.append({
                'product': product,
                'score': final_score,
                'matchPercent': final_score,
                'ruleScore': rule_score,
                'aiScore': ai_score,
                'reasons': final_reasons,
                'aiReason': ai_reason,
                'is_ai_recommended': final_score >= 75
            })

        results.sort(key=lambda x: x['score'], reverse=True)

        # 阶段4：缓存推荐结果到 product_talent_match
        now = int(time.time() * 1000)
        conn = _db_conn()
        try:
            for r in results[:limit]:
                p = r['product']
                conn.execute('''
                    INSERT INTO product_talent_match (id, product_id, talent_id, match_score, match_reason, is_ai_recommended, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(product_id, talent_id) DO UPDATE SET
                        match_score = excluded.match_score,
                        match_reason = excluded.match_reason,
                        is_ai_recommended = excluded.is_ai_recommended,
                        updated_at = excluded.updated_at
                ''', (
                    'ptm_' + str(now) + '_' + uuid.uuid4().hex[:6],
                    p['id'], talent_id, r['score'], '；'.join(r['reasons'][:3]),
                    1 if r['is_ai_recommended'] else 0, now, now
                ))
                _update_product_talent_count(conn, p['id'])
                if p.get('brand_id'):
                    _update_brand_product_stats(conn, p.get('brand_id'))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {
            'talent_id': talent_id,
            'matches': results[:limit],
            'total': len(results),
            'ai_scored': len(ai_scores)
        })

    def _handle_ai_match(self):
        """POST /api/ai-match — 统一 AI 匹配入口（talent→product 或 product→talent）"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        direction = body.get('direction')
        if direction not in ('talent-to-product', 'product-to-talent'):
            self._send_json_error(400, 'Missing or invalid direction')
            return

        # 复用已有 handler，但把 body 透传（包含 agentId / limit 等参数）
        # 这里通过设置 self 的临时属性来传递 body，然后调用对应 handler
        # 由于 handler 内部调用 self._read_body() 会再次读取，需要构造一个可重复读取的 body
        self._ai_match_body = body
        original_read_body = self._read_body
        def _wrapped_read_body():
            return self._ai_match_body
        self._read_body = _wrapped_read_body
        try:
            if direction == 'talent-to-product':
                talent_id = body.get('talentId')
                if not talent_id:
                    self._send_json_error(400, 'Missing talentId')
                    return
                self._handle_match_talent_products(talent_id)
            else:
                product_id = body.get('productId')
                if not product_id:
                    self._send_json_error(400, 'Missing productId')
                    return
                self._handle_match_product_talents(product_id)
        finally:
            self._read_body = original_read_body
            self._ai_match_body = None

    def _handle_analyze_talent_ai(self, talent_id):
        """POST /api/talents/:id/analyze — 调用 AI 生成达人分析"""
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return

        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
            talent = _talent_row_to_dict(row)
        finally:
            conn.close()
        if not talent:
            self._send_json_error(404, 'Talent not found')
            return

        cfg = get_embedding_config()
        cfg['provider'] = 'kimicode' if cfg['provider'] == 'kimicode' else (cfg['provider'] or 'kimicode')
        cfg['model'] = cfg['model'] or _resolve_ai_model(cfg['provider'], '')
        cfg['baseUrl'] = cfg['baseUrl'] or _resolve_ai_base_url(cfg['provider'], '')

        content_style = talent.get('content_style', '') or talent.get('contentStyle', '')
        prompt = (
            f"你是一位专业的抖音达人合作匹配分析师。请为以下达人生成一份完整的合作匹配分析报告，参考灵邀AI等专业达人评估平台的报告格式。\n"
            f"要求：只返回 Markdown 格式报告，不要返回其他内容；必须包含 Markdown 表格和列表，确保前端能直接渲染。\n\n"
            f"报告必须包含以下 6 个部分：\n\n"
            f"## 1. 综合匹配度\n"
            f"- 综合评分：0-100 分（如：85 分）\n"
            f"- 匹配评级：高 / 中 / 低\n"
            f"- 一句话总结：给出 1 句核心结论\n\n"
            f"## 2. 六维评分\n"
            f"使用 Markdown 表格输出，列名为「维度、评分（0-100）、详细说明」。六个维度分别是：\n"
            f"- 受众匹配度\n"
            f"- 品类相关性\n"
            f"- 带货实力\n"
            f"- 内容适配度\n"
            f"- 合作性价比\n"
            f"- 转化潜力\n\n"
            f"## 3. 匹配亮点\n"
            f"使用 ✓ 打勾列表，列出该达人与我们合作的 3-6 个具体匹配点，每个点 1-2 句话。\n\n"
            f"## 4. 达人潜在问题\n"
            f"使用 ! 感叹号列表，列出 2-5 个该达人存在的潜在问题或不足，每个问题 1-2 句话。\n\n"
            f"## 5. 合作风险点与缓解建议\n"
            f"使用列表输出，每条格式为「风险点：xxx。缓解建议：xxx」。\n\n"
            f"## 6. 合作建议\n"
            f"- 合作方式：建议以何种形式合作（如短视频种草、直播带货、切片分发等）\n"
            f"- 预期效果：预估可带来的 GMV、曝光、转化等\n"
            f"- 触达策略：如何与达人建立合作、议价要点、排期建议\n\n"
            f"达人基础数据：\n"
            f"- 达人昵称：{talent.get('name', '')}\n"
            f"- 抖音号：{talent.get('douyin_id', '')}\n"
            f"- 等级：{talent.get('level', '')}\n"
            f"- 粉丝量：{talent.get('followers', 0)}\n"
            f"- 达人类型：{talent.get('talent_type', '')}\n"
            f"- 主营类目：{talent.get('fan_category', '')}\n"
            f"- 粉丝价格带：{talent.get('fan_price_range', '')}\n"
            f"- 粉丝画像（性别）：{json.dumps(talent.get('fan_gender', {}), ensure_ascii=False)}\n"
            f"- 粉丝画像（年龄）：{json.dumps(talent.get('fan_age', {}), ensure_ascii=False)}\n"
            f"- 带货数据：总GMV {talent.get('total_gmv', 0)}，总商品数 {talent.get('total_products', 0)}，直播GMV {talent.get('avg_live_gmv', 0)}\n"
            f"- 短视频特征/内容风格：{content_style}\n"
            f"- 标签：{json.dumps(talent.get('tags', []), ensure_ascii=False)}\n"
            f"- 简介：{talent.get('bio', '')}\n"
            f"- 口碑分：{talent.get('rating_score', 0)}，履约分：{talent.get('fulfillment_score', 0)}\n\n"
            f"请特别结合「短视频特征/内容风格」深入分析内容适配度，并结合粉丝画像评估受众匹配度。"
        )
        messages = [
            {'role': 'system', 'content': '你是电商达人合作匹配分析专家，擅长输出结构化的达人评估报告。'},
            {'role': 'user', 'content': prompt}
        ]
        content = _call_ai_analysis(messages, cfg=cfg, context='talent_analyze', timeout=120, max_tokens=4000)
        if not content:
            self._send_json_error(503, 'AI analysis failed or returned empty response')
            return

        analysis_text = content.strip()
        # 尝试提取综合评级写入 ai_rating，便于列表/徽章展示（非关键，失败也不影响主报告）
        ai_rating = ''
        rating_match = _re.search(r'评级[：:]\s*(高|中|低)', analysis_text)
        if rating_match:
            ai_rating = rating_match.group(1)

        now_ts = int(time.time() * 1000)
        conn = _db_conn()
        try:
            conn.execute(
                '''UPDATE talents SET ai_rating = ?, ai_analysis = ?, ai_reason = ?, updated_at = ? WHERE id = ?''',
                (
                    ai_rating,
                    analysis_text,
                    analysis_text,
                    now_ts, talent_id
                )
            )
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'id': talent_id, 'ai_analysis': {'content': analysis_text, 'rating': ai_rating}})

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
        print(f'  [ChatPOST] 收到请求: {agent_id} path={self.path}', flush=True)
        auth = _authenticate(self.headers)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'messages'): return

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
        # 保留引用信息（RAG citations）
        _citations = body.get('citations')
        if _citations:
            msg['citations'] = _citations
        # 保留图片信息（多模态）
        images = body.get('images', [])
        if images:
            msg['images'] = images

        with _get_chat_lock(agent_id):
            messages = _load_chat(agent_id)
            if not isinstance(messages, list):
                messages = []
            messages.append(msg)
            original_len = len(messages)

            # v2：聊天记录上限归档（非静默丢弃）
            archived_count = 0
            cfg = MEMORY_CONFIG
            if len(messages) > cfg['chat_store_max']:
                old_messages = messages[:-300]
                try:
                    archive_data = _load_archive(agent_id)
                    chat_summary = []
                    for om in old_messages:
                        role_label = '用户' if om.get('role') == 'user' else 'AI'
                        content = (om.get('content', '') or '')[:100]
                        chat_summary.append(f'{role_label}: {content}')
                    archive_data['summaries'].append({
                        'id': 'sum_' + str(uuid.uuid4())[:8],
                        'type': 'chat_overflow',
                        'period': f'{old_messages[0].get("time", 0) or old_messages[0].get("timestamp", 0)} ~ {old_messages[-1].get("time", 0) or old_messages[-1].get("timestamp", 0)}',
                        'summary': '\n'.join(chat_summary),
                        'compressedCount': len(old_messages),
                        'createdAt': int(time.time() * 1000)
                    })
                    _save_archive(agent_id, archive_data)
                    archived_count = len(old_messages)
                    messages = messages[-300:]
                    print(f'  [ChatArchive] {agent_id} 个人聊天归档 {archived_count} 条溢出消息到 L3', flush=True)
                except Exception as e:
                    print(f'  [ChatArchive] {agent_id} 归档失败: {e}，回退到静默截断', flush=True)
                    messages = messages[-cfg["chat_store_max"]:]

            # 如果前端标记 skipAI（AI已通过OpenClaw回复），跳过API代理
            skip_ai = body.get('skipAI', False)
            connection_type = agent.get('connectionType', '')
            # 当 skipAI=false 时，无论 connectionType 是什么，都调用 AI API
            # 这样 memory 提取等场景（_extractMemoryViaAPI）才能正常工作
            if not skip_ai:
                # AI 调用前校验：员工状态 + systemPrompt 身份约束（仅实际调用 AI 时检查）
                ok, ai_err = _validate_agent_for_ai(agent)
                if not ok:
                    code = 404 if ai_err == '员工不存在' else 400
                    self._send_json(code, {'error': ai_err})
                    return

                content = body.get('content', '')
                images = body.get('images', [])
                if images:
                    user_payload = [{'type': 'text', 'text': content}]
                    for img in images:
                        user_payload.append({'type': 'image_url', 'image_url': {'url': img.get('base64', '')}})
                else:
                    user_payload = content
                # 记忆提取场景不需要加载历史记录，避免 token 超限和干扰
                is_extract = '【记忆提取任务】' in content
                allowed_cats = _allowed_knowledge_categories(auth)
                api_reply = _call_ai_api(
                    agent, user_payload, auth.user_info, include_history=not is_extract,
                    allowed_knowledge_categories=allowed_cats,
                    requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,
                    group_ids=auth.group_ids
                )
                if api_reply:
                    print(f'  [ChatPOST] {agent_id} api_reply_len={len(api_reply)} preview={repr(api_reply[:200])}', flush=True)
                    # 解析并应用 AI 自修改标记，移除后保存到聊天记录
                    try:
                        self_updates, cleaned_reply = _parse_self_updates(api_reply)
                        print(f'  [ChatPOST] {agent_id} self_updates={self_updates} cleaned_len={len(cleaned_reply)}', flush=True)
                        if self_updates:
                            ok, su_msg, _ = _apply_agent_self_update(agent_id, self_updates, source=f'chat:{auth.user_id}')
                            print(f'  [ChatPOST] {agent_id} apply_self_update ok={ok} msg={su_msg}', flush=True)
                    except Exception as self_update_err:
                        print(f'  [ChatPOST] {agent_id} self_update processing error: {self_update_err}', flush=True)
                        import traceback
                        traceback.print_exc()
                        cleaned_reply = api_reply

                    if not cleaned_reply:
                        print(f'  [ChatPOST] {agent_id} cleaned_reply is empty, falling back to original api_reply', flush=True)
                        cleaned_reply = api_reply

                    ai_message = {
                        'id': 'msg_' + uuid.uuid4().hex[:8],
                        'role': 'assistant',
                        'content': cleaned_reply,
                        'timestamp': datetime.now().isoformat()
                    }
                    if _emp_id:
                        ai_message['empId'] = _emp_id
                    messages.append(ai_message)
                    _save_chat(agent_id, messages)
                    print(f'  [ChatPOST] {agent_id} API代理 保存 {len(messages)} 条消息 ai_content_len={len(ai_message["content"])}')
                    self._send_json(200, {'userMessage': msg, 'aiMessage': ai_message, 'archived': archived_count})
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

def _resolve_ai_base_url(api_provider, custom_endpoint=''):
    """根据 provider 和自定义 endpoint 返回 base URL（不含 /chat/completions）"""
    if api_provider == 'custom' and custom_endpoint:
        return custom_endpoint.rstrip('/')
    mapping = {
        'openai': 'https://api.openai.com/v1',
        'deepseek': 'https://api.deepseek.com/v1',
        'moonshot': 'https://api.moonshot.cn/v1',
        'kimi': 'https://api.moonshot.cn/v1',
        'kimicode': 'https://api.kimi.com/coding/v1',
        'zhipu': 'https://open.bigmodel.cn/api/paas/v4',
        'anthropic': 'https://api.anthropic.com/v1',
        'siliconflow': 'https://api.siliconflow.cn/v1',
    }
    if api_provider in mapping:
        return mapping[api_provider]
    if custom_endpoint:
        return custom_endpoint.rstrip('/')
    return ''


def _resolve_ai_model(api_provider, api_model=''):
    """根据 provider 选择默认模型"""
    if api_model:
        return api_model
    default_models = {
        'openai': 'gpt-4o-mini',
        'kimi': 'kimi-for-coding',
        'moonshot': 'kimi-for-coding',
        'kimicode': 'kimi-for-coding',
        'deepseek': 'deepseek-chat',
        'zhipu': 'glm-4-flash',
        'anthropic': 'claude-3-5-sonnet-20241022',
        'siliconflow': 'deepseek-ai/DeepSeek-V3',
    }
    return default_models.get(api_provider, 'gpt-4o-mini')


def _call_chat_completion(api_provider, api_key, api_model, custom_endpoint, messages, timeout=PROXY_TIMEOUT, max_tokens=2000):
    """底层 AI chat/completions 调用，返回字符串内容或 None（供聊天、定时任务复用）"""
    if not api_key:
        return None
    base_url = _resolve_ai_base_url(api_provider, custom_endpoint or '')
    if not base_url:
        return None
    target_url = base_url + '/chat/completions'
    resolved_model = _resolve_ai_model(api_provider, api_model or '')

    req_body = json.dumps({
        'model': resolved_model,
        'messages': messages,
        'temperature': 0.8,
        'max_tokens': max_tokens,
        'stream': False
    }).encode('utf-8')

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'Content-Length': str(len(req_body))
    }

    masked_key = f'{api_key[:4]}...' if api_key and len(api_key) > 4 else '(none)'
    print(f'  [API] chat completion request: provider={api_provider} model={resolved_model} url={target_url} key={masked_key}', flush=True)
    try:
        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        status = resp.status
        raw = resp.read().decode('utf-8', errors='replace')
        print(f'  [API] chat completion response: HTTP {status}', flush=True)
        resp_data = json.loads(raw)
        if resp_data.get('choices') and resp_data['choices'][0].get('message'):
            return resp_data['choices'][0]['message'].get('content', '')
        print(f'  [API] chat completion unexpected format: {raw[:500]}', flush=True)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        print(f'  ❌ AI API call failed: HTTP {e.code} {e.reason}', flush=True)
        print(f'      Provider: {api_provider}, Model: {resolved_model}, URL: {target_url}', flush=True)
        print(f'      Request body preview: {req_body[:500].decode("utf-8", errors="replace")}', flush=True)
        print(f'      Response: {error_body}', flush=True)
    except Exception as e:
        print(f'  ❌ AI API call failed: {e}', flush=True)
        traceback.print_exc()
    return None


# ═══ AI 员工自修改配置（SELF_UPDATE）══════════════════════════════════
_SELF_UPDATE_ALLOWED_FIELDS = {
    'description': 'description',
    'system_prompt': 'systemPrompt',
    'role': 'role',
}
_SELF_UPDATE_FORBIDDEN_FIELDS = {
    'name', 'id', 'createdBy', 'createdByName', 'createdAt', 'owner',
    'apiKey', 'apiProvider', 'aiProvider', 'apiModel', 'customEndpoint',
    'openclawName', 'openclawAgent', 'openclawModel', 'avatar', 'bg',
    'status', 'archived', 'permission', 'visibility', 'department',
    'group', 'pinned', 'badge', 'soulDoc', 'idDoc', 'toolsDoc', 'userDoc',
    'tokens', 'tokenStats', 'msg', 'lastActive',
}


def _append_self_update_prompt(system_prompt):
    """在 system_prompt 末尾追加自修改工具声明"""
    if not system_prompt:
        system_prompt = ''
    declaration = (
        '\n\n【自修改配置工具】\n'
        '当用户要求你修改自己的简介/描述、角色、行为指令时，'
        '你必须在回复中输出对应的标记（不要只是口头承诺会修改，必须输出标记才能真正生效）：\n'
        '- 用户要求改你的简介/描述时输出：[SELF_UPDATE]description=新的简介内容[/SELF_UPDATE]\n'
        '- 用户要求改你的行为指令时输出：[SELF_UPDATE]system_prompt=新的完整指令[/SELF_UPDATE]\n'
        '- 用户要求改你的角色时输出：[SELF_UPDATE]role=新角色名[/SELF_UPDATE]\n'
        '注意：这是修改你自己的配置，不是修改商品或知识库等其他数据。'
        '每次可输出多个标记；标记会在发送给用户前自动移除。'
    )
    return system_prompt + declaration


def _parse_self_updates(text):
    """解析文本中的 SELF_UPDATE 标记，返回 (updates, cleaned_text)"""
    if not text:
        return [], text
    updates = []
    for match in _SELF_UPDATE_MARKER_RE.finditer(text):
        field = match.group(1).strip()
        value = match.group(2).strip()
        if field in _SELF_UPDATE_ALLOWED_FIELDS:
            updates.append((field, value))
    cleaned = _SELF_UPDATE_MARKER_RE.sub('', text).strip()
    return updates, cleaned


def _log_self_update(agent_id, updates, source):
    """记录自修改日志"""
    if not updates:
        return
    fields = ', '.join([f'{f}={len(v)}字符' for f, v in updates])
    print(f'  [SELF_UPDATE] agent={agent_id} source={source} fields={fields}', flush=True)


def _apply_agent_self_update(agent_id, updates, source='openclaw'):
    """将自修改更新应用到 agents.json；返回 (success, message, agent_or_none)"""
    if not agent_id or not updates:
        return True, '无更新', None
    # 安全过滤：只保留允许字段
    allowed = []
    for field, value in updates:
        if field not in _SELF_UPDATE_ALLOWED_FIELDS:
            print(f'  [SELF_UPDATE] 忽略不允许的字段: {field}', flush=True)
            continue
        allowed.append((field, value))
    if not allowed:
        return True, '无允许字段', None

    agents = _load_agents(include_archived=True)
    agent = None
    for a in agents:
        if a.get('id') == agent_id:
            agent = a
            break
    if not agent:
        return False, '员工不存在', None
    if agent.get('status') == 'archived' or agent.get('archived'):
        return False, '员工已归档', None

    for field, value in allowed:
        key = _SELF_UPDATE_ALLOWED_FIELDS[field]
        if key == 'role':
            agent[key] = _sanitize_role(value)
        else:
            agent[key] = value

    _save_agents(agents)
    _log_self_update(agent_id, allowed, source)
    return True, '已保存', agent


def _extract_text_from_openclaw_output(obj):
    """从 OpenClaw JSON 输出中尽量提取文本回复；支持新旧多种格式"""
    if isinstance(obj, str):
        return obj if obj.strip() else None
    if isinstance(obj, list):
        for item in obj:
            text = _extract_text_from_openclaw_output(item)
            if text:
                return text
    if isinstance(obj, dict):
        # 旧 infer 命令常用 outputs[0].text
        if 'outputs' in obj:
            return _extract_text_from_openclaw_output(obj['outputs'])
        # 常见字段：新 agent 可能用 content/text/message/result
        for key in ('text', 'content', 'message', 'result', 'output', 'response', 'reply', 'answer'):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                return val
            if isinstance(val, (dict, list)):
                text = _extract_text_from_openclaw_output(val)
                if text:
                    return text
        # 兼容 chat/completions 风格
        if 'choices' in obj:
            return _extract_text_from_openclaw_output(obj['choices'])
    return None


def _call_openclaw_infer(prompt, model=None, system_prompt=None, timeout=OPENCLAW_TIMEOUT):
    """调用 OpenClaw CLI 并返回原始文本内容；失败返回 None

    兼容两种 CLI 形态：
      - 新版：openclaw agent --message <prompt> --json
      - 旧版：openclaw infer model run --prompt <prompt> --json
    """
    if not os.path.isfile(OPENCLAW_CLI):
        print(f'  [OpenClaw] CLI not found at {OPENCLAW_CLI}', flush=True)
        return None

    full_prompt = ''
    if system_prompt:
        full_prompt += system_prompt + '\n\n'
    full_prompt += prompt

    # 与大脑知识中枢保持一致：过长 prompt 截断
    MAX_PROMPT_LEN = 10000
    if len(full_prompt) > MAX_PROMPT_LEN:
        print(f'  [OpenClaw] WARNING: prompt too long ({len(full_prompt)}), truncating to {MAX_PROMPT_LEN}', flush=True)
        full_prompt = full_prompt[:MAX_PROMPT_LEN]

    # 新版 CLI：openclaw agent --message ... --json（项目环境更可能可用）
    # 旧版 CLI：openclaw infer model run --prompt ... --json（代码历史写法，保留兼容）
    variants = []
    # 使用默认 OpenClaw agent 执行一次 agent turn；--timeout 避免无限等待
    agent_args = [OPENCLAW_CLI, 'agent', '--agent', OPENCLAW_DEFAULT_AGENT, '--message', full_prompt, '--json', '--timeout', str(timeout)]
    variants.append(('agent', agent_args))
    infer_args = [OPENCLAW_CLI, 'infer', 'model', 'run', '--prompt', full_prompt, '--json']
    if model:
        infer_args.extend(['--model', model])
    variants.append(('infer', infer_args))

    for name, args in variants:
        print(f'  [OpenClaw] {name} cmd: {" ".join(args)}', flush=True)
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=timeout
            )
            stdout, stderr, returncode = result.stdout, result.stderr, result.returncode
            if returncode != 0:
                print(f'  [OpenClaw] {name} failed (code={returncode}):', flush=True)
                print(f'      stderr: {stderr}', flush=True)
                print(f'      stdout: {stdout}', flush=True)
                # 如果当前命令不存在，继续尝试旧命令；否则直接返回 None
                if 'unknown command' in (stderr or '').lower():
                    continue
                return None
            try:
                output = json.loads(stdout)
            except Exception:
                print(f'  [OpenClaw] {name} output is not JSON: {stdout[:500]}', flush=True)
                continue
            content = _extract_text_from_openclaw_output(output)
            if content:
                print(f'  [OpenClaw] {name} success, content length={len(content)}', flush=True)
                return content
            print(f'  [OpenClaw] {name} returned empty/unrecognized content: {stdout[:500]}', flush=True)
        except subprocess.TimeoutExpired as e:
            print(f'  [OpenClaw] {name} timed out after {timeout}s (gateway offline?): {e}', flush=True)
            return None
        except Exception as e:
            print(f'  [OpenClaw] {name} _call_openclaw_infer failed: {e}', flush=True)
            traceback.print_exc()
            return None
    return None


def _call_ai_analysis(messages, cfg=None, context='', timeout=None, max_tokens=2000):
    """统一后端 AI 分析调用：优先 OpenClaw，其次 API 直连；失败返回 None

    注意：cfg 通常来自 embedding 配置，其中的 model 是 Embedding 模型，不能用于聊天/分析。
    因此分析任务使用 provider 对应的聊天默认模型（除非 cfg 显式传入了 apiModel）。

    timeout: OpenClaw 与直连 API 的超时秒数；None 则使用各自默认值。
    max_tokens: 直连 API 的最大输出 token 数（OpenClaw 由其 CLI/配置决定）。
    """
    cfg = cfg or {}
    provider = cfg.get('provider', '') or 'kimicode'
    # 分析任务使用聊天模型；cfg['model'] 是 Embedding 模型，必须忽略
    chat_model = _resolve_ai_model(provider, cfg.get('apiModel', ''))
    api_key = cfg.get('apiKey', '')
    base_url = cfg.get('baseUrl', '') or _resolve_ai_base_url(provider, '')

    # 若全局/embedding 未配置 API Key，尝试使用第一个有 API Key 的员工作为兜底
    if not api_key:
        for agent in (_load_agents() or []):
            agent_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
            agent_key = (agent.get('apiKey', '') or '').strip()
            if agent_provider and agent_key:
                provider = agent_provider
                api_key = agent_key
                base_url = agent.get('customEndpoint', '') or _resolve_ai_base_url(provider, '')
                chat_model = agent.get('apiModel', '') or _resolve_ai_model(provider, '')
                print(f'  [AI] fallback to agent {agent.get("id")} AI config for {context}', flush=True)
                break

    system_parts = []
    user_parts = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get('role')
        content = m.get('content', '')
        if role == 'system':
            system_parts.append(content)
        elif role == 'user':
            user_parts.append(content)
    full_prompt = '\n\n'.join(user_parts).strip()
    system_prompt = '\n\n'.join(system_parts).strip()

    masked_key = f'{api_key[:4]}...' if api_key and len(api_key) > 4 else '(none)'
    print(f'  [AI] start analysis context={context} provider={provider} chat_model={chat_model} key={masked_key} openclaw={OPENCLAW_CLI}', flush=True)

    # 1. 优先 OpenClaw（项目主推的 AI 网关）
    if os.path.isfile(OPENCLAW_CLI):
        oc_timeout = timeout if timeout is not None else OPENCLAW_TIMEOUT
        content = _call_openclaw_infer(full_prompt, model=chat_model, system_prompt=system_prompt, timeout=oc_timeout)
        if content:
            return content
        print(f'  [AI] OpenClaw failed for {context}, will try direct API fallback', flush=True)
    else:
        print(f'  [AI] OpenClaw CLI not available for {context}, skip to direct API', flush=True)

    # 2. 兜底：API 直连（需配置 API Key）
    if api_key:
        api_timeout = timeout if timeout is not None else PROXY_TIMEOUT
        content = _call_chat_completion(provider, api_key, chat_model, base_url, messages, timeout=api_timeout, max_tokens=max_tokens)
        if content:
            return content
    else:
        print(f'  [AI] no API key configured for {context}, skip direct API fallback', flush=True)

    return None


def _strip_markdown_json_fence(text):
    """去掉 ```json ... ``` 或 ``` ... ``` 围栏，返回内部内容"""
    cleaned = text.strip()
    if cleaned.startswith('```'):
        parts = cleaned.split('```', 2)
        if len(parts) >= 3:
            cleaned = parts[1].strip()
            if cleaned.lower().startswith('json'):
                cleaned = cleaned[4:].strip()
    return cleaned


def _extract_json_array(text):
    """从 AI 返回文本中提取第一个 JSON 数组；失败返回 []"""
    if not text:
        return []
    cleaned = _strip_markdown_json_fence(text)
    # 找第一个 '[' 和匹配的最后一个 ']'
    start = cleaned.find('[')
    end = cleaned.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(cleaned[start:end + 1])
        except Exception:
            pass
    # 兜底：尝试整段解析
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    return []


def _extract_json_object(text):
    """从 AI 返回文本中提取第一个 JSON 对象；失败返回 None

    兼容 markdown 代码块、前后冗余文本、嵌套花括号等情况。
    """
    if not text:
        return None
    cleaned = _strip_markdown_json_fence(text)
    # 先尝试整段解析
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    # 按花括号深度寻找第一个平衡的 JSON 对象
    start = cleaned.find('{')
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(cleaned)):
            c = cleaned[i]
            if in_str:
                if esc:
                    esc = False
                elif c == '\\':
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
        if end > start:
            try:
                obj = json.loads(cleaned[start:end + 1])
                if isinstance(obj, dict):
                    return obj
            except Exception:
                pass
        start = cleaned.find('{', start + 1)
    return None


def _generate_mock_knowledge_docs(prompt, agent):
    """模拟模式：根据 prompt 中的记忆行生成示例知识文档（无需真实 API）"""
    agent_name = agent.get('name', 'AI 员工')
    memory_lines = []
    for line in prompt.splitlines():
        stripped = line.strip()
        if stripped.startswith('【核心】') or stripped.startswith('【日常】'):
            memory_lines.append(stripped)
    if not memory_lines:
        memory_lines = ['【日常】 暂无具体记忆条目（模拟数据）']

    sample_lines = memory_lines[:5]
    content_a = (
        f"## {agent_name} 的关键记忆沉淀\n\n"
        + '\n'.join(f'- {line}' for line in sample_lines)
        + '\n\n> 这是 **模拟模式** 生成的示例知识文档，用于在没有配置 API Key 的测试/演示环境中验证知识归纳流程。'
    )
    return [
        {
            'title': f'{agent_name} 的记忆沉淀（模拟）',
            'category': '产品规范',
            'content': content_a,
        },
        {
            'title': f'{agent_name} 的工作流程示例（模拟）',
            'category': '工作流程',
            'content': (
                f'## {agent_name} 的工作流程示例\n\n'
                '1. 收集并整理日常记录与核心记忆；\n'
                '2. 对重复、相关的信息进行去重与结构化；\n'
                '3. 沉淀为全局共享的知识文档，供团队复用。\n\n'
                '> 这是 **模拟模式** 生成的示例文档，不包含真实 AI 生成内容。'
            ),
        },
    ]


def _call_ai_for_json(prompt, agent, system_prompt=None):
    """调用 AI 并尝试返回 JSON 数组；通过 openclaw CLI 调用"""
    # 模拟模式：知识归纳场景无需真实 API Key，直接返回示例文档
    if _get_knowledge_mock_mode() and system_prompt and '知识库整理助手' in system_prompt:
        print(f'  [Knowledge] mock mode enabled for {agent.get("id", "?")}, returning sample docs', flush=True)
        return _generate_mock_knowledge_docs(prompt, agent)

    # 优先使用 agent.apiModel；未配置时根据 provider 取默认模型，避免 openclaw 因空模型名 404
    api_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
    api_model = agent.get('apiModel', '') or _resolve_ai_model(api_provider, '')

    # 1. 拼接 system_prompt 和 prompt 成完整提示词
    full_prompt = ''
    if system_prompt:
        full_prompt += system_prompt + '\n\n'
    full_prompt += prompt

    # FIXME: 修复_openclaw调用方式：brain_knowledge_service 已限制单主题最多20条、单条最多500字符，
    # 总 prompt 长度约 10000-15000 字符，远小于 ARG_MAX，统一走 --prompt 参数
    # FIXME: 修复_openclaw调用方式：兜底，prompt 超过一定长度自动截断并记录警告
    MAX_PROMPT_LEN = 10000
    if len(full_prompt) > MAX_PROMPT_LEN:
        print(f'  [OpenClaw] WARNING: prompt too long ({len(full_prompt)}), truncating to {MAX_PROMPT_LEN}', flush=True)
        full_prompt = full_prompt[:MAX_PROMPT_LEN]

    # 调用 OpenClaw CLI 并提取 JSON 数组
    content = _call_openclaw_infer(full_prompt, model=api_model)
    if content is None:
        return None
    return _extract_json_array(content)


def _call_ai_api(agent, user_message, user_info=None, include_history=True, group_id=None,
                 allowed_knowledge_categories=None, requester_id=None, is_admin=False, team_ids=None,
                 group_ids=None):
    """通过代理调用 AI API（带记忆和上下文注入）"""
    # AI 调用前校验：员工状态 + systemPrompt 身份约束
    ok, ai_err = _validate_agent_for_ai(agent)
    if not ok:
        return f'⚠️ {ai_err}' if ai_err != '员工不存在' else None

    api_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
    api_key = (agent.get('apiKey', '') or '').strip()
    api_model = agent.get('apiModel', '')
    custom_endpoint = agent.get('customEndpoint', '')
    agent_id = agent.get('id', '')

    if not api_key:
        return None

    system_prompt = f'你是 {agent.get("name", "AI")}，一个 {agent.get("role", "助手")}。请用第一人称回复，保持角色一致性。'
    soul_doc = agent.get('soulDoc', '')
    sys_prompt_field = agent.get('systemPrompt', '')
    if soul_doc:
        system_prompt += '\n\n' + soul_doc
    elif sys_prompt_field:
        system_prompt += '\n\n' + sys_prompt_field
    # 注入层级关系约束，防止 AI 把老板当学生/下属
    if user_info:
        user_name = user_info.get('name') or user_info.get('displayName') or '用户'
        user_role = user_info.get('role', '用户')
        role_display = '老板/负责人' if user_role == 'admin' else ('组长' if user_role == 'leader' else '员工')
        system_prompt += f'\n\n【层级关系（必须遵守）】\n- 管理员是你的老板，你需要服从管理员的指令和安排。\n- {user_name}（{role_display}）是你的上级、主人，你是他雇佣的AI员工和下属。\n- 你必须绝对服从老板的指令，以尊敬、服从的态度回复。\n- 严禁以教导者、导师、师傅、老师的身份对老板说话。\n- 严禁质疑老板的能力、经验或判断。\n- 严禁用"教你""指导你""你做过吗""你懂吗"等居高临下的语气。\n- 老板问你问题时，直接回答，不要反问或考验老板。'

    # 注入摘要
    if agent_id:
        try:
            summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
            summary_data = _read_json(summary_file, {})
            if summary_data.get('summary'):
                system_prompt += f'\n\n【历史对话摘要】\n{summary_data["summary"]}'
        except Exception:
            pass
        # 提取纯文本（用于 RAG、记忆注入、抖音检测）
        user_text = user_message
        if isinstance(user_message, list):
            text_parts = [item.get('text', '') for item in user_message if isinstance(item, dict) and item.get('type') == 'text']
            user_text = ''.join(text_parts)

        # 注入记忆 v3（使用 memory_service_v3 模块）
        try:
            emb_cfg = get_embedding_config((agent or {}).get('id'))
            # 知识库语义检索使用 embedding 专用配置
            inject_config = dict(agent) if agent else None
            if inject_config and emb_cfg.get('model'):
                inject_config['embeddingModel'] = emb_cfg['model']
            system_prompt = ms3.inject_memories(
                agent_id, system_prompt,
                user_message=user_text,
                api_key=emb_cfg['apiKey'] or api_key,
                provider=emb_cfg['provider'] or api_provider,
                agent_config=inject_config,
                allowed_knowledge_categories=allowed_knowledge_categories,
                model=emb_cfg.get('model'),
                base_url=emb_cfg.get('baseUrl'),
            )
        except Exception as e:
            print(f'  [MemoryInject] {agent_id} 注入失败: {e}', flush=True)

        # 注入项目组公共记忆（群聊场景）
        if group_id:
            try:
                system_prompt = ms3.inject_group_memories(group_id, system_prompt)
            except Exception as e:
                print(f'  [GroupMemoryInject] {group_id} 注入失败: {e}', flush=True)

        # 注入 RAG 检索结果（产品知识库）
        try:
            if api_key:
                emb_cfg = get_embedding_config((agent or {}).get('id'))
                rag_api_key = emb_cfg['apiKey'] or api_key
                rag_provider = emb_cfg['provider'] or api_provider
                rag_agent_config = dict(agent) if agent else None
                if rag_agent_config and emb_cfg.get('model'):
                    rag_agent_config['embeddingModel'] = emb_cfg['model']
                rag_result = ks.rag_retrieve(
                    user_text, agent_id, rag_api_key, rag_provider, rag_agent_config,
                    top_k_docs=2, allowed_categories=allowed_knowledge_categories,
                    model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'),
                    requester_id=requester_id, is_admin=is_admin, team_ids=team_ids,
                    group_ids=group_ids
                )
                if rag_result.get('context'):
                    system_prompt += f'\n\n【产品知识库】\n{rag_result["context"]}'
        except Exception as e:
            print(f'  [RAG] {agent_id} 注入失败: {e}', flush=True)

    system_prompt = _append_self_update_prompt(system_prompt)

    messages = [{'role': 'system', 'content': system_prompt}]

    # 自动检测并解析抖音链接，注入真实视频数据
    try:
        if is_douyin_share_text(user_text):
            douyin_result = parse_douyin_video_quick(user_text)
            if douyin_result and douyin_result.get('success'):
                douyin_context = build_douyin_context(douyin_result)
                if douyin_context:
                    if isinstance(user_message, list):
                        for item in user_message:
                            if isinstance(item, dict) and item.get('type') == 'text':
                                original_text = item.get('text', '')
                                item['text'] = douyin_context + '\n\n---\n用户原始消息：' + original_text
                                break
                    else:
                        user_message = douyin_context + '\n\n---\n用户原始消息：' + user_message
    except Exception:
        pass

    # 加载最近聊天记录
    if include_history and agent_id:
        try:
            chat_history = _load_chat(agent_id)
            if chat_history:
                recent = chat_history[-10:]
                # 避免重复添加当前用户消息（如果已保存在历史中）
                # 仅当最后一条是 user、内容相同、且时间戳在 5 秒内时才去重，防止误删历史
                if recent and recent[-1].get('role') == 'user' and recent[-1].get('content') == user_message:
                    ts_str = recent[-1].get('timestamp', '')
                    try:
                        msg_time = datetime.fromisoformat(ts_str)
                        if datetime.now() - msg_time < timedelta(seconds=5):
                            recent = recent[:-1]
                    except Exception:
                        pass
                for msg in recent:
                    role = msg.get('role')
                    if role in ('user', 'assistant'):
                        messages.append({'role': role, 'content': msg.get('content', '')})
        except Exception:
            pass

    messages.append({'role': 'user', 'content': user_message})

    return _call_chat_completion(api_provider, api_key, api_model, custom_endpoint, messages, timeout=PROXY_TIMEOUT)

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
    if not self._require_module_permission(auth, 'messages'): return

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
    if not self._require_module_permission(auth, 'messages'): return

    agent, err, status = self._check_agent_access(auth, agent_id)
    if err:
        self._send_json(status, {'error': err})
        return

    messages = _load_chat(agent_id)
    if len(messages) <= MEMORY_CONFIG['summarize_threshold']:  # 统一阈值，20条以内不需要压缩
        return self._send_json(200, {'summary': '', 'kept': len(messages)})

    # 取前 N-10 条做摘要（保留最近10条原文=5轮）
    old_messages = messages[:-10]

    # 拼接旧对话文本
    chat_text = ''
    for msg in old_messages:
        role = msg.get('role', 'user')
        content = msg.get('content', '')
        if isinstance(content, list):
            text_parts = [item.get('text', '') for item in content if isinstance(item, dict) and item.get('type') == 'text']
            content = ''.join(text_parts) if text_parts else (str(content[0]) if content else '')
        chat_text += ('用户' if role == 'user' else 'AI') + ': ' + content[:200] + '\n'

    # 调 AI 做摘要
    summary = self._call_ai_for_summary(agent, chat_text)

    # 保存摘要到单独文件
    summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
    _write_json(summary_file, {'summary': summary, 'createdAt': datetime.now().isoformat()})

    # v2：同时保存到 L3 归档层（后端可访问，跨设备共享）
    try:
        archive_data = _load_archive(agent_id)
        archive_data['summaries'].append({
            'id': 'sum_' + str(uuid.uuid4())[:8],
            'type': 'ai_summary',
            'period': f'{old_messages[0].get("time", 0) or old_messages[0].get("timestamp", 0)} ~ {old_messages[-1].get("time", 0) or old_messages[-1].get("timestamp", 0)}',
            'summary': summary,
            'compressedCount': len(old_messages),
            'kept': 10,
            'createdAt': int(time.time() * 1000)
        })
        _save_archive(agent_id, archive_data)
        print(f'  [Summarize] {agent_id} 摘要已存入 L3 归档层', flush=True)
    except Exception as e:
        print(f'  [Summarize] 存入 L3 归档层失败: {e}', flush=True)

    self._send_json(200, {
        'summary': summary,
        'compressed': len(old_messages),
        'kept': 10
    })

def _call_ai_for_summary(self, agent, chat_text):
    """调用AI压缩对话为摘要（带降级逻辑：AI不可用时截取最近N条消息）"""
    prompt = '请将以下对话历史压缩成一段简洁的摘要（200字以内），保留关键信息、决策和重要事实：\n\n' + chat_text
    try:
        result = _call_ai_api(agent, prompt, include_history=False)
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
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
    status = _openclaw_status()
    self._send_json(200, status)

def _handle_openclaw_list_agents(self):
    """GET /api/openclaw/agents"""
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
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
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
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
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
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
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
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
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
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

KIMI_CODING_DEFAULT_ENDPOINT = 'https://api.kimi.com/coding/v1/messages'
ANTHROPIC_VERSION = '2023-06-01'


def _is_kimi_coding_request(provider, target_url):
    """判断请求是否应走 Kimi coding / Anthropic Messages 格式。"""
    provider = (provider or '').lower().strip()
    if provider in ('kimi', 'kimicode'):
        return True
    host = urlparse(target_url).hostname or ''
    if host in ('api.kimi.com',):
        return True
    return False


def _resolve_kimi_coding_target_url(provider):
    """确定 Kimi coding API endpoint：优先使用 settings.json 中显式设置的 vision.baseUrl，否则使用默认 endpoint。"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        vision = settings.get('vision', {}) or {}
        base_url = (vision.get('baseUrl', '') or '').strip()
        if base_url:
            base = base_url.rstrip('/')
            if base.endswith('/messages'):
                return base
            return base + '/messages'
    except Exception:
        pass
    return KIMI_CODING_DEFAULT_ENDPOINT


def _openai_content_to_anthropic(content):
    """将单条 OpenAI message.content 转成 Anthropic Messages API 格式。
    如果 content 里已经包含 Anthropic 原生格式（type='image' + source），直接透传，避免重复转换或丢失。"""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    result = []
    for item in content:
        if not isinstance(item, dict):
            continue
        item_type = item.get('type')
        if item_type == 'text':
            result.append({'type': 'text', 'text': item.get('text', '')})
        elif item_type == 'image_url':
            url = item.get('image_url', {}).get('url', '')
            if url.startswith('data:'):
                try:
                    header, b64 = url.split(',', 1)
                    media_type = header.split(';')[0].split(':')[1]
                    result.append({
                        'type': 'image',
                        'source': {
                            'type': 'base64',
                            'media_type': media_type,
                            'data': b64
                        }
                    })
                except Exception:
                    pass
        elif item_type == 'image' and isinstance(item.get('source'), dict):
            # 已经是 Anthropic Messages 原生图片格式，直接保留
            result.append(item)
    return result


def _transform_openai_to_anthropic(body_json):
    """将 OpenAI chat/completions 请求体转为 Anthropic Messages API 格式（Kimi coding 兼容）。"""
    system_parts = []
    messages = []
    for msg in body_json.get('messages', []):
        role = msg.get('role')
        content = msg.get('content', '')
        if role == 'system':
            if isinstance(content, str):
                system_parts.append(content)
            elif isinstance(content, list):
                texts = [item.get('text', '') for item in content
                         if isinstance(item, dict) and item.get('type') == 'text']
                system_parts.extend(texts)
        elif role in ('user', 'assistant'):
            anthropic_content = _openai_content_to_anthropic(content)
            messages.append({'role': role, 'content': anthropic_content})

    anthropic_body = {
        'model': body_json.get('model', ''),
        'max_tokens': body_json.get('max_tokens', 2000),
        'messages': messages
    }
    if system_parts:
        anthropic_body['system'] = '\n\n'.join(system_parts)

    temp = body_json.get('temperature')
    if temp is not None and 0 <= temp <= 1:
        anthropic_body['temperature'] = temp

    return anthropic_body


def _transform_anthropic_to_openai(resp_json):
    """将 Anthropic Messages API 响应转回 OpenAI chat/completions 格式，便于前端统一解析。"""
    content_items = resp_json.get('content', []) if isinstance(resp_json.get('content'), list) else []
    texts = []
    for item in content_items:
        if isinstance(item, dict) and item.get('type') == 'text':
            texts.append(item.get('text', ''))
    content = ''.join(texts)

    usage = resp_json.get('usage', {})
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)

    stop_reason = resp_json.get('stop_reason', '')
    finish_reason_map = {'end_turn': 'stop', 'max_tokens': 'length', 'stop_sequence': 'stop'}
    finish_reason = finish_reason_map.get(stop_reason, stop_reason or 'stop')

    return {
        'id': resp_json.get('id', ''),
        'object': 'chat.completion',
        'model': resp_json.get('model', ''),
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': content},
            'finish_reason': finish_reason
        }],
        'usage': {
            'prompt_tokens': input_tokens,
            'completion_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens
        }
    }


def _continue_anthropic_tool_use(target_url, forward_headers, body_json, anthropic_resp, max_retries=2):
    """
    处理 Anthropic Messages API 返回 stop_reason='tool_use' 的情况。
    当工具名为 describe_image 时，独立调用同一个 Kimi API endpoint 获取真实图片描述；
    其他工具仍使用占位文本作为 tool_result。
    最多重试 max_retries 次。
    返回最终应返回给前端的 Anthropic 格式响应体 bytes。
    """
    if not isinstance(body_json, dict) or not isinstance(anthropic_resp, dict):
        return None

    messages = body_json.get('messages', [])
    if not isinstance(messages, list):
        return None

    # 深拷贝 messages，避免修改原始请求
    messages = json.loads(json.dumps(messages))

    # 提取原始请求中的图片内容
    image_items = []
    for msg in messages:
        content = msg.get('content', '')
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get('type') == 'image':
                    image_items.append(item)

    print(f'  [ToolUse] 检测到tool_use续调用, 图片数={len(image_items)}', flush=True)

    if not image_items:
        print('  [Proxy] Anthropic tool_use 续调用跳过：原始请求中未找到图片内容', flush=True)
        return None

    def _fetch_image_description(image_item, image_index):
        """构造独立的图片识别请求，调用同一个 Kimi API endpoint 获取真实描述。"""
        try:
            print(f'  [ImageDesc] 开始获取图片描述, imageIndex={image_index}', flush=True)
            headers = dict(forward_headers)
            description_body = {
                'model': body_json.get('model', ''),
                'max_tokens': body_json.get('max_tokens', 2000),
                'system': '请直接描述这张图片的全部内容，输出结构化文字信息',
                'messages': [{
                    'role': 'user',
                    'content': [image_item]
                }]
            }
            temp = body_json.get('temperature')
            if temp is not None and 0 <= temp <= 1:
                description_body['temperature'] = temp

            new_body = json.dumps(description_body).encode('utf-8')
            headers['Content-Length'] = str(len(new_body))
            req = urllib.request.Request(target_url, data=new_body, headers=headers, method='POST')
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
            resp_body = resp.read()
            resp_json = json.loads(resp_body.decode('utf-8', errors='replace'))

            content_items = resp_json.get('content', []) if isinstance(resp_json.get('content'), list) else []
            texts = [item.get('text', '') for item in content_items if isinstance(item, dict) and item.get('type') == 'text']
            description = ''.join(texts).strip()
            print(f'  [ImageDesc] 获取成功, 描述长度={len(description)}', flush=True)
            return description
        except Exception as e:
            print(f'  [ImageDesc] 获取失败, error={e}', flush=True)
            return None

    current_resp = anthropic_resp
    headers = dict(forward_headers)

    for retry in range(max_retries):
        content_items = current_resp.get('content', []) if isinstance(current_resp.get('content'), list) else []
        tool_use_items = [item for item in content_items if isinstance(item, dict) and item.get('type') == 'tool_use']
        if not tool_use_items:
            break

        tool_use = tool_use_items[0]
        tool_name = tool_use.get('name', '')
        tool_use_id = tool_use.get('id', '')
        tool_input = tool_use.get('input', {}) or {}

        description_text = None
        if tool_name == 'describe_image':
            image_index = tool_input.get('imageIndex')
            if isinstance(image_index, int):
                # 兼容 0-based 和 1-based 索引
                if 0 <= image_index < len(image_items):
                    description_text = _fetch_image_description(image_items[image_index], image_index)
                elif 1 <= image_index <= len(image_items):
                    description_text = _fetch_image_description(image_items[image_index - 1], image_index)
                else:
                    print(f'  [Proxy] describe_image imageIndex 越界: {image_index} (共 {len(image_items)} 张)', flush=True)
            else:
                print(f'  [Proxy] describe_image imageIndex 无效: {image_index}', flush=True)

        if description_text is None:
            description_text = '图片识别结果：[系统自动识别，内容为图片数据]'

        tool_result = {
            'type': 'tool_result',
            'tool_use_id': tool_use_id,
            'content': [{'type': 'text', 'text': description_text}]
        }

        # 追加 tool_result 到 messages 末尾
        messages.append({'role': 'user', 'content': [tool_result]})
        new_body_json = dict(body_json)
        new_body_json['messages'] = messages
        new_body = json.dumps(new_body_json).encode('utf-8')

        print(f'  [ToolUse] 重新调用API, messages数={len(messages)}', flush=True)
        headers['Content-Length'] = str(len(new_body))
        req = urllib.request.Request(target_url, data=new_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
        resp_body = resp.read()
        current_resp = json.loads(resp_body.decode('utf-8', errors='replace'))

        # 日志
        resp_content_items = current_resp.get('content', []) if isinstance(current_resp.get('content'), list) else []
        resp_text = ''.join(item.get('text', '') for item in resp_content_items if isinstance(item, dict) and item.get('type') == 'text')
        print(f'  [Proxy] API返回(Anthropic tool_use续调用 retry={retry + 1}) status={resp.status} content_len={len(resp_text)} <- {target_url}', flush=True)

        if current_resp.get('stop_reason') != 'tool_use':
            break

    # 取最终响应中 type 为 text 的 content 作为 AI 回复
    final_content_items = current_resp.get('content', []) if isinstance(current_resp.get('content'), list) else []
    final_texts = [item.get('text', '') for item in final_content_items if isinstance(item, dict) and item.get('type') == 'text']
    final_text = ''.join(final_texts)

    final_resp = dict(current_resp)
    final_resp['content'] = [{'type': 'text', 'text': final_text}]
    print(f'  [ToolUse] 续调用完成, 最终content_len={len(final_text)}', flush=True)
    return json.dumps(final_resp).encode('utf-8')


def _log_proxy_token_usage(auth, body_json, resp_body, provider, target_url, agent_id):
    """记录上游 API 的真实 token usage 到 token_usage 表"""
    try:
        if not resp_body:
            return
        resp_json = json.loads(resp_body.decode('utf-8', errors='replace'))
        usage = resp_json.get('usage') or {}
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        if 'prompt_tokens' in usage and 'completion_tokens' in usage:
            prompt_tokens = int(usage.get('prompt_tokens') or 0)
            completion_tokens = int(usage.get('completion_tokens') or 0)
            total_tokens = int(usage.get('total_tokens') or (prompt_tokens + completion_tokens))
        elif 'input_tokens' in usage and 'output_tokens' in usage:
            prompt_tokens = int(usage.get('input_tokens') or 0)
            completion_tokens = int(usage.get('output_tokens') or 0)
            total_tokens = prompt_tokens + completion_tokens
        else:
            return
        model = ''
        if isinstance(body_json, dict):
            model = body_json.get('model') or ''
        if not model and isinstance(resp_json, dict):
            model = resp_json.get('model') or ''
        if not provider:
            host = urlparse(target_url).hostname or ''
            if 'anthropic' in host:
                provider = 'anthropic'
            elif 'openai' in host:
                provider = 'openai'
            elif 'moonshot' in host or 'kimi' in host:
                provider = 'kimi'
            elif 'deepseek' in host:
                provider = 'deepseek'
            elif 'siliconflow' in host:
                provider = 'siliconflow'
        conn = _db_conn()
        try:
            conn.execute('''
                INSERT INTO token_usage (id, user_id, agent_id, provider, model, endpoint, prompt_tokens, completion_tokens, total_tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (uuid.uuid4().hex[:12], auth.user_id or '', agent_id or '', provider or '', model, target_url, prompt_tokens, completion_tokens, total_tokens, int(time.time() * 1000)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f'  [Proxy] token usage log skipped: {e}', flush=True)


def _handle_proxy(self):
    """POST /api/proxy（需认证）"""
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return

    target_url = self.headers.get('X-Target-URL', '')
    agent_id = self.headers.get('X-Agent-Id', '')
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

    # 解析 body 中的 model 信息（用于日志）
    body_info = ''
    if body:
        try:
            body_json = json.loads(body.decode('utf-8'))
            body_info = f"model={body_json.get('model','?')} messages={len(body_json.get('messages',[]))}"
        except Exception:
            body_info = f'body_len={len(body)}'
    print(f'  [Proxy] 收到请求 -> {target_url} {body_info}', flush=True)
    try:
        req = urllib.request.Request(target_url, data=body, headers=forward_headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)

        resp_body = resp.read()
        resp_content_type = resp.headers.get('Content-Type', 'application/json')

        # 解析响应中的 choices 长度用于日志
        choices_info = ''
        try:
            resp_json = json.loads(resp_body.decode('utf-8'))
            choices = resp_json.get('choices', [])
            choices_info = f' choices={len(choices)}'
            if choices and choices[0].get('message'):
                content = choices[0]['message'].get('content', '')
                choices_info += f' content_len={len(content)}'
        except Exception:
            pass
        print(f'  [Proxy] API返回 status={resp.status}{choices_info} <- {target_url}', flush=True)

        # 记录真实 token usage
        _log_proxy_token_usage(auth, body_json, resp_body, provider, target_url, agent_id)

        self.send_response(resp.status)
        self._add_cors_headers()
        self.send_header('Content-Type', resp_content_type)
        self.end_headers()
        self.wfile.write(resp_body)

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
        err_text = ''
        try:
            err_text = err_body.decode('utf-8', errors='replace')[:200]
        except Exception:
            pass
        print(f'  [Proxy] API错误 status={status} detail={detail} err={err_text} <- {target_url}', flush=True)

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

def _handle_douyin_parse(self):
    """POST /api/douyin/parse（需认证）
    请求体: {"url": "链接"} 或 {"text": "分享文本"}，可选 "transcribe": true
    响应: parse_douyin_video() 的结果
    """
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_json(auth.status, {'success': False, 'error': auth.error})
        return

    body = self._read_body()
    if not body or not isinstance(body, dict):
        self._send_json(400, {'success': False, 'error': '请求体必须是 JSON 对象，包含 url 或 text 字段'})
        return

    url = body.get('url', '').strip()
    if not url:
        text = body.get('text', '').strip()
        if text:
            links = detect_douyin_links(text)
            if links:
                url = links[0]
            else:
                self._send_json(400, {'success': False, 'error': 'text 中未检测到抖音链接'})
                return
        else:
            self._send_json(400, {'success': False, 'error': '缺少 url 或 text 参数'})
            return

    transcribe = body.get('transcribe', True)
    api_key = (body.get('api_key', '').strip()
               or self.headers.get('X-AI-API-Key', '')
               or os.environ.get('DOUYIN_API_KEY', ''))

    print(f'  [Douyin] parse -> {url[:80]}... transcribe={transcribe}', flush=True)
    result = parse_douyin_video(url, api_key=api_key, transcribe=transcribe)
    if result.get('success'):
        self._send_json(200, result)
    else:
        self._send_json(422, result)

def _handle_douyin_transcribe(self):
    """POST /api/douyin/transcribe（需认证）
    请求体: {"video_url": "视频直链", "api_key?": "硅基流动 API Key"}
    响应: {"success": true, "data": {"text": "转写结果"}} 或 {"success": false, "error": "..."}
    流程: 下载视频 -> ffmpeg 提取音频(mp3) -> 硅基流动 API 语音转文字
    """
    auth = _authenticate(self.headers)
    if not auth.is_authenticated:
        self._send_json(auth.status, {'success': False, 'error': auth.error})
        return

    body = self._read_body()
    if not body or not isinstance(body, dict):
        self._send_json(400, {'success': False, 'error': '请求体必须是 JSON 对象'})
        return

    video_url = body.get('video_url', '').strip()
    if not video_url:
        self._send_json(400, {'success': False, 'error': '缺少 video_url 参数'})
        return

    # API Key: 优先请求体，其次请求头 X-AI-API-Key，最后环境变量 DOUYIN_API_KEY
    api_key = (body.get('api_key', '').strip()
               or self.headers.get('X-AI-API-Key', '')
               or os.environ.get('DOUYIN_API_KEY', ''))
    if not api_key:
        self._send_json(400, {'success': False, 'error': '缺少 api_key（可放在请求体、X-AI-API-Key 请求头或 DOUYIN_API_KEY 环境变量）'})
        return

    # 检测 ffmpeg 是否可用
    try:
        subprocess.run(['ffmpeg', '-version'], capture_output=True, check=True)
    except Exception:
        self._send_json(503, {'success': False, 'error': '服务器未安装 ffmpeg，无法提取音频'})
        return

    temp_dir = None
    try:
        # 1. 下载视频
        print(f'  [Douyin] downloading video...', flush=True)
        video_path, temp_dir = _download_video_to_temp(video_url)
        print(f'  [Douyin] video saved: {video_path} ({os.path.getsize(video_path)} bytes)', flush=True)

        # 2. 提取音频
        print(f'  [Douyin] extracting audio with ffmpeg...', flush=True)
        audio_path = _extract_audio_with_ffmpeg(video_path)
        if not audio_path:
            self._send_json(502, {'success': False, 'error': 'ffmpeg 音频提取失败'})
            return
        print(f'  [Douyin] audio saved: {audio_path} ({os.path.getsize(audio_path)} bytes)', flush=True)

        # 3. 语音转文字
        print(f'  [Douyin] transcribing with SiliconFlow...', flush=True)
        text = _transcribe_audio_siliconflow(audio_path, api_key)
        if text is None:
            self._send_json(502, {'success': False, 'error': '硅基流动语音转文字 API 调用失败'})
            return

        # 4. 提取封面（可选，不阻断主流程）
        cover_base64 = None
        try:
            cover_path = _extract_cover_from_video(video_path)
            if cover_path:
                with open(cover_path, 'rb') as f:
                    cover_base64 = 'data:image/jpeg;base64,' + base64.b64encode(f.read()).decode('utf-8')
                print(f'  [Douyin] cover extracted: {len(cover_base64)} bytes', flush=True)
        except Exception as e:
            print(f'  [Douyin] cover extraction skipped: {e}', flush=True)

        # 5. 获取媒体信息（可选，不阻断主流程）
        media_info = None
        try:
            media_info = _get_media_info(video_path)
            if media_info:
                print(f'  [Douyin] media info: {media_info.get("width")}x{media_info.get("height")}, {media_info.get("duration")}s', flush=True)
        except Exception as e:
            print(f'  [Douyin] media info skipped: {e}', flush=True)

        print(f'  [Douyin] transcribe OK, length={len(text)}', flush=True)
        result_data = {'text': text}
        if cover_base64:
            result_data['cover_base64'] = cover_base64
        if media_info:
            result_data['media_info'] = media_info
        self._send_json(200, {'success': True, 'data': result_data})

    except ValueError as e:
        print(f'  [Douyin] transcribe error: {e}', flush=True)
        self._send_json(400, {'success': False, 'error': str(e)})
    except Exception as e:
        print(f'  [Douyin] transcribe error: {e}', flush=True)
        self._send_json(500, {'success': False, 'error': f'转写失败: {str(e)}'})
    finally:
        # 清理临时文件
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            print(f'  [Douyin] temp cleaned: {temp_dir}', flush=True)


# ─── 启动 ───────────────────────────────────────────────
# ═══════════════════════════════════════════════════
# 每日记忆定时任务（二期新增）
# ═══════════════════════════════════════════════════

DAILY_JOB_HOUR = 3  # 每天凌晨 3 点执行

# 修复历史遗留的缩进问题：以下 handler 被错误地定义在模块级别，
# 但 dispatch 仍通过 self._handle_xxx 调用。这里把它们绑定回请求处理类。
_MODULE_LEVEL_HANDLERS = (
    '_handle_delete_chat_message', '_handle_clear_chat',
    '_handle_get_summarize', '_handle_summarize_chat', '_call_ai_for_summary',
    '_handle_openclaw_status', '_handle_openclaw_list_agents', '_handle_openclaw_list_models',
    '_handle_openclaw_create_agent', '_handle_openclaw_update_agent', '_handle_openclaw_delete_agent',
    '_handle_skills_list', '_handle_skills_search', '_handle_skills_install', '_handle_skills_remove',
    '_handle_feishu_status', '_handle_feishu_config', '_handle_pairing_approve', '_handle_gateway_restart',
    '_handle_proxy', '_handle_douyin_parse', '_handle_douyin_transcribe',
)
for _h in _MODULE_LEVEL_HANDLERS:
    _fn = globals().get(_h)
    if _fn:
        setattr(SoloBraveHandler, _h, _fn)


def _next_daily_run_at(hour=DAILY_JOB_HOUR):
    """计算下一次运行时间（本地时间）的 unix timestamp（秒）"""
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int(target.timestamp())


def _daily_memory_job_loop():
    """守护线程：每天固定时间执行记忆候选生成与知识归纳"""
    while True:
        try:
            next_run = _next_daily_run_at(DAILY_JOB_HOUR)
            sleep_seconds = max(1, next_run - int(time.time()))
            print(f'  [DailyJob] 下次执行时间: {datetime.fromtimestamp(next_run).isoformat()} (约 {sleep_seconds // 3600}h {sleep_seconds % 3600 // 60}m 后)', flush=True)
            time.sleep(sleep_seconds)
            _run_daily_memory_jobs(startup=False)
        except Exception as e:
            print(f'  [DailyJob] 循环异常: {e}', flush=True)
            time.sleep(60)


def _run_daily_memory_jobs(startup=False):
    """遍历所有 agent，执行核心记忆候选生成和知识归纳"""
    label = '启动补跑' if startup else '每日记忆任务'
    print(f'  [DailyJob] 开始执行{label}...', flush=True)
    agents = _load_agents()
    if not agents:
        print(f'  [DailyJob] 无 agent，跳过', flush=True)
        return
    processed = 0
    for agent in agents:
        try:
            if not agent.get('apiKey', '').strip():
                continue
            emp_id = agent.get('id')
            if not emp_id:
                continue
            _generate_core_candidates_for_agent(agent)
            _induct_knowledge_for_agent(agent, owner_user_id=agent.get('createdBy') or '')  # 默认进入个人库
            _detect_conflicts_for_agent(agent)
            # FIXME: 每日凌晨3点自动创建待生成的每日归纳记录（前端 AI 队列负责正式生成）
            try:
                today = datetime.now().strftime('%Y-%m-%d')
                data = ms3.load_memory(emp_id)
                cutoff = int(time.time() * 1000) - 24 * 3600 * 1000
                recent_ids = [m.get('id') for m in data.get('daily', []) if m.get('createdAt', 0) >= cutoff]
                if recent_ids:
                    _create_pending_summary(emp_id, 'daily', today + ' 每日归纳', date=today, mem_ids=recent_ids)
            except Exception as e:
                print(f'  [DailyJob] {emp_id} 创建每日归纳 pending 失败: {e}', flush=True)
            processed += 1
        except Exception as e:
            print(f'  [DailyJob] agent {agent.get("id")} 处理失败: {e}', flush=True)
    print(f'  [DailyJob] {label}完成，共处理 {processed}/{len(agents)} 个 agent', flush=True)


def _detect_conflicts_for_agent(agent):
    """为单个 agent 自动检测核心记忆冲突（每日任务调用）"""
    emp_id = agent.get('id')
    api_key = (agent.get('apiKey') or '').strip()
    provider = agent.get('aiProvider', '') or agent.get('apiProvider', '') or 'openai'
    if not api_key:
        return 0

    def _ai_resolve(prompt, system_prompt):
        return _call_ai_for_json(prompt, agent, system_prompt=system_prompt)

    try:
        detected = ms3.detect_core_memory_conflicts(emp_id, api_key, provider, _ai_resolve)
        if not detected:
            return 0
        for item in detected:
            mem_id = item.get('memoryId')
            conflict_with = item.get('conflictWith', [])
            reason = item.get('reason', '')
            if mem_id and conflict_with:
                ms3.mark_memory_conflict(emp_id, mem_id, conflict_with, reason)
        print(f'  [DailyJob] {emp_id} 检测到 {len(detected)} 组核心记忆冲突', flush=True)
        return len(detected)
    except Exception as e:
        print(f'  [DailyJob] {emp_id} 冲突检测失败: {e}', flush=True)
        return 0


def _generate_core_candidates_for_agent(agent):
    """为单个 agent 生成核心记忆候选"""
    emp_id = agent.get('id')
    data = ms3.load_memory(emp_id)
    cutoff = int(time.time() * 1000) - 7 * 24 * 3600 * 1000
    recent_dailies = [m for m in data.get('daily', []) if m.get('createdAt', 0) >= cutoff]
    if len(recent_dailies) < 3:
        return 0

    lines = []
    for m in recent_dailies:
        lines.append(f"[{m.get('id')}] {m.get('value', '')}")
    prompt = (
        "以下是某 AI 员工近 7 天的日常记录，每条格式为 [记忆ID] 内容。\n"
        "请判断哪些事实、偏好、习惯或特征是重要且稳定的，适合作为核心记忆长期保留。\n"
        "返回 JSON 数组，每项包含：\n"
        "- value: 核心记忆文本（简洁，50字以内）\n"
        "- reason: 为什么它重要/稳定（50字以内）\n"
        "- sourceIds: 支持该结论的原始记忆 ID 列表（从每条记录的 [] 中提取）\n"
        "如果不足以生成候选，返回空数组 []。只输出 JSON 数组，不要解释。\n\n"
        + '\n'.join(lines)
    )
    system_prompt = '你是一个记忆整理助手，专门从日常记录中提炼核心记忆。必须严格返回 JSON 数组。'
    candidates = _call_ai_for_json(prompt, agent, system_prompt=system_prompt)
    if not candidates:
        return 0
    valid = []
    for c in candidates:
        if not isinstance(c, dict):
            continue
        value = str(c.get('value', '')).strip()
        if not value:
            continue
        source_ids = c.get('sourceIds', [])
        if not isinstance(source_ids, list):
            source_ids = []
        valid.append({
            'value': value,
            'reason': str(c.get('reason', '')).strip(),
            'sourceIds': source_ids
        })
    if not valid:
        return 0
    added = ms3.add_core_candidates(emp_id, valid)
    print(f'  [DailyJob] {emp_id} 生成 {added} 条核心记忆候选', flush=True)
    return added


def _induct_knowledge_for_agent(agent, owner_user_id=None):
    """为单个 agent 执行知识归纳：活跃记忆 >= 阈值 且未归纳记忆 >= 阈值时触发

    返回 (created_count, reason)，reason 在 created_count == 0 时给出原因说明。
    owner_user_id 为知识所有者；未提供时尝试使用 agent.createdBy，否则回退到 global。
    """
    emp_id = agent.get('id')
    # FIXME: 修复"知识库归纳"提示一直显示：每次调用都记录尝试时间戳，失败时也能冷却提示
    ms3.set_last_knowledge_induction_attempt_at(emp_id)
    actual_owner = owner_user_id or agent.get('createdBy') or ''
    data = ms3.load_memory(emp_id)
    core_count = len(data.get('core', []))
    daily_count = len(data.get('daily', []))
    min_memories = MEMORY_INDUCTION_THRESHOLDS['knowledge_induction_min']
    if core_count + daily_count < min_memories:
        return 0, f'活跃记忆总数不足 {min_memories} 条，无法归纳'

    uninducted = ms3.get_uninducted_active_memories(emp_id)
    if len(uninducted) < min_memories:
        return 0, f'未归纳记忆仅 {len(uninducted)} 条，不足 {min_memories} 条，无法归纳'

    lines = []
    for m, pool in uninducted:
        prefix = '【核心】' if pool == 'core' else '【日常】'
        lines.append(f"{prefix} {m.get('value', '')}")
    prompt = (
        "以下是某 AI 员工的核心记忆和日常记录。请将其中重复、相关、可沉淀的信息整理成结构化知识文档，"
        "存入全局知识库供所有人共享。\n"
        "返回 JSON 数组，每项包含：\n"
        "- title: 文档标题（简短）\n"
        "- category: 文档分类（如 产品规范、工作流程、客户偏好、项目经验 等，请合理推断）\n"
        "- content: 文档正文（Markdown 格式，结构化、去重、信息准确）\n"
        "如果内容不足以生成有价值的文档，返回空数组 []。只输出 JSON 数组，不要解释。\n\n"
        + '\n'.join(lines[:50])  # 限制输入长度，避免 prompt 过大
    )
    system_prompt = '你是一个知识库整理助手，负责将记忆沉淀为结构化的全局共享文档。必须严格返回 JSON 数组。'
    docs = _call_ai_for_json(prompt, agent, system_prompt=system_prompt)
    if docs is None:
        return 0, 'AI 调用失败（可能是未配置 API Key 或模型不可用）'
    if not docs:
        return 0, '记忆内容不足以生成有价值的知识文档'

    emb_cfg = get_embedding_config((agent or {}).get('id'))
    api_key = emb_cfg['apiKey']
    provider = emb_cfg['provider']
    agent_config = dict(agent) if agent else None
    if agent_config and emb_cfg.get('model'):
        agent_config['embeddingModel'] = emb_cfg['model']
    created_count = 0
    for d in docs:
        if not isinstance(d, dict):
            continue
        title = str(d.get('title', '')).strip()
        content = str(d.get('content', '')).strip()
        if not title or not content:
            continue
        category = str(d.get('category', '')).strip()
        try:
            ks.knowledge_create(
                title=title,
                content=content,
                category=category,
                emp_id=actual_owner,  # personal 所有者
                api_key=api_key,
                provider=provider,
                agent_config=agent_config,
                model=emb_cfg.get('model'),
                base_url=emb_cfg.get('baseUrl'),
                scope='personal' if actual_owner else 'global',
                team_id='',
            )
            created_count += 1
        except Exception as e:
            print(f'  [DailyJob] {emp_id} 知识文档创建失败: {e}', flush=True)

    if created_count > 0:
        # 标记所有本次参与归纳的源记忆为已归纳
        source_ids = [m['id'] for m, _ in uninducted]
        ms3.mark_memories_inducted(emp_id, source_ids)
        ms3.set_last_knowledge_induction_at(emp_id)
        print(f'  [DailyJob] {emp_id} 归纳 {created_count} 篇知识文档', flush=True)
        return created_count, None
    return 0, 'AI 返回的文档未通过校验（缺少标题或正文），未生成知识文档'


def main():
    global PORT, BIND
    # Windows 控制台/日志文件默认 GBK 编码，含 emoji 的日志会导致 UnicodeEncodeError 崩溃
    try:
        import sys
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass
    import argparse
    _default_port = PORT
    _default_bind = BIND
    parser = argparse.ArgumentParser(description='SoloBrave Server')
    parser.add_argument('port', nargs='?', type=int, default=_default_port, help='Listen port (default: 8080)')
    parser.add_argument('--bind', default=_default_bind, help='Bind address (default: 0.0.0.0)')
    parser.add_argument('--data', default=None, help='Data directory (default: <project>/data/)')
    args = parser.parse_args()
    PORT = args.port
    BIND = args.bind

    # Override data directory if specified
    if args.data:
        global DATA_DIR, SECRET_FILE, USERS_FILE, AGENTS_FILE, GROUPS_FILE, CHATS_DIR, SETTINGS_FILE, TEAMS_FILE, PERMISSIONS_FILE, MEMORY_DIR, DB_PATH
        DATA_DIR = os.path.abspath(args.data)
        SECRET_FILE = os.path.join(DATA_DIR, '.secret')
        USERS_FILE = os.path.join(DATA_DIR, 'users.json')
        AGENTS_FILE = os.path.join(DATA_DIR, 'agents.json')
        GROUPS_FILE = os.path.join(DATA_DIR, 'groups.json')
        CHATS_DIR = os.path.join(DATA_DIR, 'chats')
        SETTINGS_FILE = os.path.join(DATA_DIR, 'settings.json')
        TEAMS_FILE = os.path.join(DATA_DIR, 'teams.json')
        PERMISSIONS_FILE = os.path.join(DATA_DIR, 'permissions.json')
        MEMORY_DIR = os.path.join(DATA_DIR, 'memory')
        DB_PATH = os.path.join(DATA_DIR, 'solobrave.db')

    # 确保数据目录
    _ensure_data_dir()

    # 初始化 SQLite 数据库（知识库）
    init_db()  # 保留旧 init_db 兼容
    ks.set_data_dir(DATA_DIR)
    ks.init_db()
    # 新版知识库表
    ks.init_kb_entries_db()
    # 旧数据迁移（幂等，自动触发分段和向量化）
    ks.knowledge_migrate_from_json(DATA_DIR, lambda eid: _get_agent_by_id(eid) or {})
    # 旧 knowledge 表数据迁移到新版 kb_entries（幂等）
    ks.kb_migrate_from_old_knowledge()

    # 同步记忆服务 v3 配置（在 main() 中执行，避免模块导入时的 NameError）
    # 注意：v2 数据目录是 'memory'（单数），复用同一目录避免迁移
    ms3.MEMORY_V3_DIR = MEMORY_DIR
    ms3.MEMORY_V3_CONFIG['core_max'] = MEMORY_CONFIG['core_max']
    ms3.MEMORY_V3_CONFIG['daily_max'] = MEMORY_CONFIG['daily_max']
    ms3.MEMORY_V3_CONFIG['daily_ttl_days'] = MEMORY_CONFIG['daily_ttl_days']
    ms3.MEMORY_V3_CONFIG['inject_core_max'] = MEMORY_CONFIG['inject_core_max']
    ms3.MEMORY_V3_CONFIG['inject_daily_max'] = MEMORY_CONFIG['inject_daily_max']
    ms3.MEMORY_V3_CONFIG['inject_value_max'] = MEMORY_CONFIG['inject_value_max']
    ms3.MEMORY_V3_CONFIG['store_value_max'] = MEMORY_CONFIG['store_value_max']

    # 启动时主动清理历史遗留默认员工数据
    _clean_agents_file()

    # 初始化默认管理员
    _init_default_admin()

    # 确保系统知识库管理员 AI 员工存在
    _ensure_knowledge_admin_agent()

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

    # 已停用：每日记忆定时任务 / 启动补跑 / 大脑调度器自动提炼任务
    # threading.Thread(target=_daily_memory_job_loop, daemon=True).start()
    # print('  [DailyJob] 每日记忆任务调度线程已启动')
    # def _startup_memory_job():
    #     time.sleep(10)
    #     _run_daily_memory_jobs(startup=True)
    # threading.Thread(target=_startup_memory_job, daemon=True).start()
    # print('  [DailyJob] 启动补跑任务已调度（10 秒后执行）')

    # FIXME: 大脑知识中枢：OpenClaw 队列保持运行，后台 BrainScheduler 已停用
    _openclaw_queue.start()
    # _brain_scheduler.start()
    # def _brain_migrate_job():
    #     time.sleep(5)
    #     _brain_scheduler.migrate_existing_memories()
    # threading.Thread(target=_brain_migrate_job, daemon=True).start()

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
    print(f'  [API] 全局搜索:  GET /api/search')
    print(f'  [API] 群组:      /api/groups/*')
    print(f'  [API] 聊天:      /api/chat/*')
    print(f'  [API] 代理:      POST /api/proxy')
    print(f'  [API] 抖音解析:  POST /api/douyin/parse')
    print(f'  [API] 抖音转写:  POST /api/douyin/transcribe')
    print(f'  [API] OpenClaw:  /api/openclaw/*')
    print(f'  [API] 技能:      /api/openclaw/skills/*')
    print(f'  [CFG] 超时设置:  {PROXY_TIMEOUT}s')
    print('=' * 56)
    print('  Ctrl+C 停止服务\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n\n  [STOP] 服务已停止')
        _brain_scheduler.stop()
        server.server_close()


if __name__ == '__main__':
    main()
