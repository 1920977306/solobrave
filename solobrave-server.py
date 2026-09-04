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
from collections import OrderedDict, deque
from datetime import datetime, timedelta
import re
import socket
from urllib.parse import urlparse, unquote, parse_qs

# 抖音视频解析模块（拆分到独立文件）
from douyin_parser import *

# 记忆服务 v3（新目录结构：data/memories/{empId}/）
import memory_service_v3 as ms3

# V3 商品评分模型（达人选品意愿 + 带货效果两层漏斗）
import v3_scorer as v3

# 知识库服务（分段向量化 + 全局公共，独立模块避免循环导入）
import knowledge_service as ks

# FIXME: 大脑知识中枢新增服务
import topic_service as ts
import brain_knowledge_service as bks

# Memory Pipeline（L0-L3 分层记忆 + Token 预算召回）
import memory_pipeline

# 统一日志（替代散落的 print 调试输出）
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('solobrave')

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

# HTTPS / TLS 配置：给 SoloBrave 后端套 https，让前端能进入安全上下文（crypto.subtle 可用），
# WebSocket 也能用 wss://。证书优先用 mkcert 生成（带本地 CA，浏览器免警告），
# 不可用时 fallback 到 openssl 自签，最后兜底跳过 HTTPS 仅跑 HTTP。
HTTPS_PORT = 8443
HTTPS_ENABLED = True  # 总开关：False 时不启动 https server
TLS_CERT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'certs')
TLS_CERT_FILE = os.path.join(TLS_CERT_DIR, 'cert.pem')
TLS_KEY_FILE = os.path.join(TLS_CERT_DIR, 'key.pem')
# 证书里要包含的 SAN（覆盖 127.0.0.1、局域网 IP、local 主机名）；新增机器时改这里即可
TLS_CERT_HOSTS = ['127.0.0.1', 'localhost', '192.168.1.25', '*.local']

# WSS 代理：HTTPS 页面下浏览器强制 wss，但 OpenClaw Gateway（18789）只支持 ws://。
# 解决：在另一个端口用 SSLContext 包一个 wss server，把所有连接双向透传到 ws://127.0.0.1:18789。
# 端口默认 8444，复用上面同一份证书。
WSS_PROXY_PORT = 8444
WSS_PROXY_ENABLED = True
WSS_PROXY_TARGET_HOST = '127.0.0.1'  # 转发到的 Gateway 地址
WSS_PROXY_TARGET_PORT = 18789        # 转发到的 Gateway 端口

# CORS Origin 白名单（启动时会按实际端口追加 localhost/127.0.0.1 来源）
ALLOWED_ORIGINS = [
    'http://localhost:8081',
    'http://localhost:8080',
    'http://127.0.0.1:8081',
    'http://127.0.0.1:8080',
]

# 请求体大小限制（抖音视频解析接口单独放宽）
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10MB
MAX_VIDEO_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

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

# ═══ 图片识别提示词 ═══
# role == '商务' 的 AI 员工调用 /api/vision/describe 时使用该专用提取提示词，
# 其他角色沿用 _call_kimi_vision 内的通用提示词
BUSINESS_VISION_PROMPT = """你是一个专业的抖音达人数据提取员。看到截图后按以下分组逐条提取，截图里没有的字段标注null，严禁跳过字段严禁概括省略，数字保留原始精度和原始格式（允许范围值如50万-100万）。输出一个扁平JSON对象：

【总览基本信息】name(昵称)、douyin_id(抖音号)、city(城市)、followers(粉丝数)、level(等级)、bio(简介完整原文)、tags(内容标签数组)、main_category(主推带货类目，取热卖类目TOP3中占比最高的那个类目名，如"服饰内衣"，不要取内容标签)、cooperation_status(合作邀约状态)、agency(签约机构)；

【带货核心数据】product_count(带货商品数)、total_history_days(历史带货天数)、total_shops(合作店铺数)、total_gmv(结算总额保留原始格式如50万-100万)、live_ratio(直播占比百分比数值如0)、live_sessions(带货直播场次)、live_views(直播观看人数)、video_ratio(视频占比百分比数值如99.16)、video_count(带货视频数量)、video_plays(视频播放量)、single_video_settlement(单视频结算额保留原始格式)、video_gpm(视频GPM保留原始格式)、average_price(平均件单价保留原始格式)、rating_score(带货评分0-5)、fulfillment_score(合作履约分0-5)；

【带货商品列表】top_products数组，不限数量按截图列出，每条含name(商品名)、shop_name(店铺名)、price(到手价)、gmv_range(结算额区间如5万-10万)、video_count(关联短视频数)；

【热卖类目TOP3】top_categories数组，每条含name(类目名)、avg_price(均价)、gmv(结算额)、ratio(占比百分比)；

【热卖品牌TOP3】top_brands数组，每条含name(品牌名)、avg_price(均价)、gmv(结算额)、ratio(占比百分比)；

【短视频详细指标】video_completion_rate(完播率百分比)、video_likes(点赞数)、video_comments(评论数)、video_shares(转发数)、video_interaction_rate(互动率百分比)、video_avg_price(视频平均件单价)；

【粉丝分析】fan_gender(性别分布JSON如{"男":50,"女":50})、fan_age(年龄分布JSON如{"31-40":43})、fan_city_tier(城市等级分布JSON如{"三线城市":24})、fan_crowd(人群标签如都市银发23%)、fan_price_range(客单价偏好如50到100元30%)、fan_category(品类偏好如服装23%)；

【粉丝团分析】fan_group_gender(性别分布JSON)、fan_group_age(年龄分布JSON如{"18-23":x,"24-30":x,"31-40":x,"41-50":x,"50+":x})、fan_group_crowd(八大消费人群占比JSON)、fan_group_activity(活跃度分布JSON)、fan_group_device(设备分布JSON)、fan_group_price(客单价水平JSON)、fan_group_category(类目分布JSON)；

【直播间观众】live_audience_region(省份分布JSON含城市和占比)、live_audience_city_tier(城市等级分布JSON)；

【短视频观众】video_audience_region(省份分布JSON含城市和占比)、video_audience_city_tier(城市等级分布JSON)；

所有字段名必须严格使用上述英文名，数组和分布类字段输出为JSON对象或数组。"""

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
    'daily_max': 200,          # 日常记录池上限（从 100 提到 200，缓解 409 满池问题）
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
        logger.info('  [OpenClawQueue] started')

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
            logger.error(f'  [OpenClawQueue] task {task_id} failed ({task["retries"]}/{task["max_retries"]}): {e}')
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
            logger.error(f'  [BrainScheduler] AI call failed: {e}')
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
            logger.info(f'  [BrainScheduler] enqueued {count} uncleaned memories at startup')
        except Exception as e:
            logger.error(f'  [BrainScheduler] enqueue uncleaned memories failed: {e}')

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
        logger.info('  [BrainScheduler] started')

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error(f'  [BrainScheduler] tick error: {e}')
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
                logger.error(f'  [BrainScheduler] task error: {e}')

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
            logger.error(f'  [BrainScheduler] task failed ({task["retries"]}/3): {e}')
            if task['retries'] <= 3:
                delay = 1000 * (2 ** (task['retries'] - 1))
                task['run_at'] = int(time.time() * 1000) + delay
                with self._lock:
                    self._tasks.append(task)
            else:
                logger.info(f'  [BrainScheduler] task dropped after 3 retries')
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
        logger.info(f'  [BrainScheduler] clean {len(mem_ids)} memories for {emp_id}')
        agent = self._default_agent()
        for mem_id in mem_ids:
            mem = ms3._clean_and_deduplicate(mem_id, emp_id)
            if mem and not mem.get('is_filler') and not mem.get('is_duplicate'):
                # FIXME: 记忆归类到主题时只置 pending_induct=1，由调度器巡检统一入队，避免重复入队
                self._topic_svc.classify_memory_to_topic(mem_id, emp_id)
            self._today_processed += 1

    def _do_induct(self, topic_id):
        """FIXME: 执行主题知识沉淀"""
        logger.info(f'  [BrainScheduler] induct topic {topic_id}')
        # FIXME: 归纳任务执行前再校验：若 pending_induct=0 说明已被处理过，直接跳过
        conn = _db_conn()
        try:
            row = conn.execute(
                'SELECT pending_induct FROM memory_topics WHERE id=?', (topic_id,)
            ).fetchone()
            if not row or not row['pending_induct']:
                logger.info(f'  [BrainScheduler] topic {topic_id} already inducted, skip')
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
            logger.error(f'  [BrainScheduler] get_memory_row {mem_id} failed: {e}')
        return None

    def migrate_existing_memories(self):
        """FIXME: 兼容现有数据：从 v3 记忆目录 data/memory/ 迁移 daily 记忆到 memory 表并加入清洗队列"""
        logger.info('  [BrainScheduler] migrating existing memories')
        migrated = 0
        enqueued = 0
        per_emp = {}  # FIXME: 记录每个员工的迁移数量
        # FIXME: v3 记忆目录是 data/memory/（ms3.MEMORY_V3_DIR 已被 main() 覆写为 MEMORY_DIR）
        memories_dir = MEMORY_DIR
        if not os.path.isdir(memories_dir):
            logger.info(f'  [BrainScheduler] memory dir not found: {memories_dir}')
            return
        now = int(time.time() * 1000)
        for emp_id in os.listdir(memories_dir):
            # FIXME: 只处理员工目录：以 emp_ 开头，排除 groups/、archive/、{empId} 等
            if not emp_id.startswith('emp_'):
                continue
            try:
                ms3._validate_emp_id(emp_id)
            except Exception as e:
                logger.info(f'  [BrainScheduler] skip invalid emp_id {emp_id}: {e}')
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
                    logger.info(f'  [BrainScheduler] {emp_id} migrated {per_emp[emp_id]} memories')
            except Exception as e:
                logger.error(f'  [BrainScheduler] migrate {emp_id} failed: {e}')
        logger.info(f'  [BrainScheduler] migrated {migrated} memories, enqueued {enqueued} clean tasks')

    def _check_pending_topics(self):
        """FIXME: 只扫描 pending_induct=1 的主题"""
        topics = self._topic_svc.get_pending_induct_topics(min_memories=3)
        logger.info(f'  [BrainScheduler] {len(topics)} pending topics')
        for t in topics:
            self.request_induct(t['id'])

    def _daily_inspect(self):
        """FIXME: 每日全量巡检：归档不活跃主题、校验冲突"""
        logger.info('  [BrainScheduler] daily inspect')
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
                logger.error(f'  [BrainScheduler] conflict check failed: {e}')

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
    """读取 JSON 文件；读取/解析失败时记录完整异常堆栈（文件路径、异常类型、错误信息）

    并发说明：写方走 唯一tmp文件+os.replace 原子替换，读方不会看到半截内容；
    但 Windows 上读方与另一进程的 replace 竞争可能出现瞬时 PermissionError，
    因此对 OSError 做短重试；JSONDecodeError 属于真实损坏，不重试直接报错。
    """
    if not os.path.isfile(filepath):
        return default if default is not None else None
    file_lock = _get_memory_file_lock(filepath)
    for attempt in range(3):
        try:
            with file_lock:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except json.JSONDecodeError as e:
            logger.error(
                f'  [READ_JSON] JSON 解析失败: file={filepath} '
                f'err_type={type(e).__name__} err={e}\n{traceback.format_exc()}')
            return default if default is not None else None
        except OSError as e:
            if attempt < 2:
                time.sleep(0.05 * (attempt + 1))
                continue
            logger.error(
                f'  [READ_JSON] 读取失败（重试3次后放弃）: file={filepath} '
                f'err_type={type(e).__name__} err={e}\n{traceback.format_exc()}')
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
                            logger.info(f'  [WRITE_GUARD] 写入前发现 apiKey 被污染: {agent.get("id")} len={len(ak)} 已清空')
                            agent['apiKey'] = ''
            # 第一层防护：落盘前把序列化结果 loads 回来校验，不合法则拒绝写入，
            # 防止损坏数据落盘（同时提前暴露不可序列化对象）
            try:
                payload = json.dumps(data, ensure_ascii=False, indent=2)
                json.loads(payload)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                logger.error(
                    f'  [WRITE_JSON] 写入前校验失败，已拒绝写入: file={filepath} '
                    f'err_type={type(e).__name__} err={e}\n{traceback.format_exc()}')
                raise ValueError(f'写入前 JSON 校验失败: {filepath}: {e}')
            with open(tmp_path, 'w', encoding='utf-8') as f:
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(payload)
                if fcntl:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            # Windows 上若其他进程/线程正打开目标文件读取，os.replace 会抛
            # 瞬时 PermissionError，短重试可避开；重试仍失败则抛出并记录堆栈
            last_err = None
            for attempt in range(3):
                try:
                    os.replace(tmp_path, filepath)
                    last_err = None
                    break
                except OSError as e:
                    last_err = e
                    if attempt < 2:
                        time.sleep(0.05 * (attempt + 1))
            if last_err is not None:
                logger.error(
                    f'  [WRITE_JSON] os.replace 失败（重试3次后放弃）: file={filepath} '
                    f'err_type={type(last_err).__name__} err={last_err}\n{traceback.format_exc()}')
                raise last_err
    except OSError:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def _trash_file(path):
    """删除文件前先备份到 data/backups/deleted/（时间戳前缀防重名），再删除原文件。

    用于聊天记录等用户数据的删除场景，避免误删后无法恢复。
    """
    trash_dir = os.path.join(DATA_DIR, 'backups', 'deleted')
    os.makedirs(trash_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(trash_dir, f'{ts}_{os.path.basename(path)}')
    shutil.copy2(path, backup_path)
    os.remove(path)
    logger.info(f'  [Trash] 已备份后删除: {path} -> {backup_path}')


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


def _validate_agents_json():
    """第三层防护（启动保险）：agents.json 损坏时自动从最近的可用备份恢复

    启动快照（_backup_data_dir）会把整个 data/ 复制到 data/backups/<时间戳>/，
    这里按时间戳倒序找第一个能正常解析的 agents.json 恢复；损坏现场保留为
    agents.json.corrupt.<时间戳> 供排查。
    """
    if not os.path.isfile(AGENTS_FILE):
        return
    try:
        with open(AGENTS_FILE, 'r', encoding='utf-8') as f:
            json.load(f)
        return  # 文件正常，无需修复
    except (json.JSONDecodeError, OSError) as e:
        logger.error(
            f'  [Repair] agents.json 损坏: err_type={type(e).__name__} err={e}\n'
            f'{traceback.format_exc()}')

    # 保留损坏现场
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    corrupt_path = AGENTS_FILE + f'.corrupt.{ts}'
    try:
        shutil.copy2(AGENTS_FILE, corrupt_path)
    except OSError:
        pass

    # 从 data/backups/ 快照中找最近一个可正常解析的 agents.json
    backups_root = os.path.join(DATA_DIR, 'backups')
    if os.path.isdir(backups_root):
        for name in sorted(os.listdir(backups_root), reverse=True):
            cand = os.path.join(backups_root, name, 'agents.json')
            if not os.path.isfile(cand):
                continue
            try:
                with open(cand, 'r', encoding='utf-8') as f:
                    json.load(f)
            except (json.JSONDecodeError, OSError):
                continue  # 该备份也损坏，尝试更早的
            try:
                shutil.copy2(cand, AGENTS_FILE)
                logger.warning(
                    f'  [Repair] agents.json 已从备份恢复: {cand}'
                    f'（损坏现场保留在 {corrupt_path}）')
            except OSError as e:
                logger.error(f'  [Repair] 备份恢复失败: {cand} err={e}')
            return
    logger.error('  [Repair] 未找到可用备份，agents.json 保持损坏状态'
                 '（启动保护逻辑将跳过覆盖，不会丢更多数据）')


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
        logger.info('  🔑 默认管理员账号: admin / admin123，请尽快修改密码')
        return admin
    return None


def _ensure_knowledge_admin_agent():
    """确保存在系统知识库管理员 AI 员工

    注意：必须基于原始文件内容追加，不能用 _load_agents() 的过滤结果写回；
    且 agents.json 存在但读取/解析失败时必须中止，否则会把 [knowledge_admin]
    覆盖写回，导致用户创建的员工全部丢失。
    """
    raw = _read_json(AGENTS_FILE, None)
    if raw is None:
        if os.path.isfile(AGENTS_FILE):
            logger.error('  [System] agents.json 读取/解析失败，跳过 knowledge_admin 初始化以保护现有数据')
            return
        raw = []
    if not isinstance(raw, list):
        logger.error('  [System] agents.json 格式异常（非列表），跳过 knowledge_admin 初始化以保护现有数据')
        return
    agents = raw
    for a in agents:
        if isinstance(a, dict) and a.get('id') == 'knowledge_admin':
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
    logger.info('  [System] 已创建知识库管理员 AI 员工: knowledge_admin')


# ─── 权限管理 ─────────────────────────────────────────

# 可用模块列表（与 switchModule 取值对齐）
AVAILABLE_MODULES = [
    'dashboard', 'messages', 'knowledge', 'settings', 'products', 'groups', 'influencers', 'employees'
]


def _default_permission_templates():
    """默认角色权限模板

    角色：超级管理员 / 管理员 / 普通用户
    模块 key：dashboard/messages/knowledge/settings/products/groups/influencers/employees
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
        'settings': True,
        'employees': False,
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
    if hasattr(user_or_auth, 'is_admin') and user_or_auth.is_admin:
        return {'modules': {m: True for m in AVAILABLE_MODULES}, 'knowledgeCategories': ['*']}
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
            logger.info(f'  [LOAD_GUARD] 加载时发现 apiKey 被污染: {a.get("id")} len={len(ak)} 已清空')
            a['apiKey'] = ''
        # 检测 systemPrompt / soulDoc / idDoc 污染（日志写入 JSON 时可能连带污染）
        for field in ('systemPrompt', 'soulDoc', 'idDoc', 'toolsDoc', 'userDoc'):
            val = a.get(field, '')
            if _is_log_polluted(val):
                logger.info(f'  [LOAD_GUARD] 加载时发现 {field} 被污染: {a.get("id")} len={len(val)} 已清空')
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

def _get_active_agent_ids():
    """返回 agents.json 中所有正式员工的 ID 集合"""
    return {a.get('id', '') for a in _load_agents() if a.get('id')}


def _clean_agents_file():
    """主动清理 agents.json 中的历史遗留默认员工数据"""
    agents = _read_json(AGENTS_FILE, [])
    if not isinstance(agents, list):
        return 0
    cleaned = [a for a in agents if not _is_default_agent(a)]
    removed = len(agents) - len(cleaned)
    if removed > 0:
        _write_json(AGENTS_FILE, cleaned)
        logger.info(f'  [Clean] 已从 agents.json 清理 {removed} 个历史遗留默认员工')
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
        logger.info(f'  [SANITIZE] apiKey 被日志污染，长度={len(api_key)}，已清空')
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


def _user_display_name_map():
    """返回 userId -> 显示名 映射（displayName 优先，退回 username；不含敏感字段）"""
    return {u.get('id'): (u.get('displayName') or u.get('username') or '') for u in _load_users() if u.get('id')}


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


# ─── 项目组消息可见（团队动态） ─────────────────────────

def _get_agent_group_id(agent_id):
    """返回 agent 所属的项目组 ID（不在任何项目组则返回 None）"""
    if not agent_id:
        return None
    for g in _load_groups():
        gid = g.get('id')
        if not gid:
            continue
        for m in g.get('members', []):
            mid = m if isinstance(m, str) else m.get('id')
            if mid == agent_id:
                return gid
    return None


def _record_group_message(group_id, agent_id, role, content):
    """记录一条项目组对话消息到 group_messages 表"""
    if not group_id or not agent_id or not content:
        return
    conn = _db_conn()
    try:
        conn.execute(
            'INSERT INTO group_messages (id, group_id, agent_id, role, content) VALUES (?, ?, ?, ?, ?)',
            ('gm_' + uuid.uuid4().hex[:8], group_id, agent_id, role, content)
        )
        conn.commit()
    except Exception as e:
        logger.error(f'  [TeamFeed] 记录消息失败: {e}')
    finally:
        conn.close()


def _build_team_feed(group_id, exclude_agent_id):
    """查询同项目组其他 agent 最近24小时最多10条对话，格式化为【团队动态】摘要。
    不注入 exclude_agent_id 自己的消息；agent 不在任何项目组时由调用方保证不进入此函数。"""
    if not group_id:
        return ''
    conn = _db_conn()
    try:
        rows = conn.execute(
            "SELECT agent_id, role, content, created_at FROM group_messages "
            "WHERE group_id = ? AND agent_id != ? "
            "AND created_at >= datetime('now', '-24 hours') "
            "ORDER BY created_at DESC, rowid DESC LIMIT 10",
            (group_id, exclude_agent_id or '')
        ).fetchall()
    except Exception as e:
        logger.error(f'  [TeamFeed] 查询失败: {e}')
        return ''
    finally:
        conn.close()
    if not rows:
        return ''
    name_map = {a.get('id'): a.get('name', 'AI') for a in _load_agents()}
    local_offset = datetime.now() - datetime.utcnow()  # created_at 为 UTC，转本地时间显示
    lines = []
    prev = None
    for row in reversed(rows):  # 按时间正序展示
        content = (row['content'] or '').replace('\n', ' ').strip()
        if len(content) > 200:
            content = content[:200] + '…'
        name = name_map.get(row['agent_id'], 'AI')
        hm = ''
        try:
            hm = (datetime.strptime(row['created_at'], '%Y-%m-%d %H:%M:%S') + local_offset).strftime('%H:%M')
        except Exception:
            pass
        if (row['role'] == 'assistant' and prev is not None
                and prev['role'] == 'user' and prev['agent_id'] == row['agent_id'] and lines):
            # AI 回复紧跟同 agent 的用户消息时，合并为一行：用户消息→AI建议
            lines[-1] += f'→{content}'
        else:
            prefix = '' if row['role'] == 'user' else '→'
            lines.append(f'[{name} {hm}]{prefix}{content}')
        prev = row
    return '【团队动态】\n' + '\n'.join(lines)


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
        logger.info(f'  [OpenClawSync] API Key 已同步: {agent_id} provider={provider}')
        return True, stdout
    else:
        err = stderr or stdout or f'returncode={rc}'
        logger.error(f'  [OpenClawSync] API Key 同步失败: {agent_id} provider={provider} err={err}')
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


def _get_localhost_auth_result(headers, parsed_body=None):
    """本地回环地址的认证结果：优先根据 X-Agent-Id 识别 AI 员工创建者，否则检查 body 中的 agent_id，最后回退到 localhost"""
    agent_id = headers.get('X-Agent-Id', '').strip()
    if not agent_id and isinstance(parsed_body, dict):
        body_agent_id = parsed_body.get('agent_id')
        if isinstance(body_agent_id, str):
            agent_id = body_agent_id.strip()
    if agent_id:
        agent = _get_agent_by_id(agent_id)
        if agent:
            created_by = agent.get('createdBy')
            if created_by:
                result = AuthResult(user_info={'userId': created_by, 'role': 'admin'})
                # 标记为 AI 员工的本地调用：数据接口（如达人列表）需按创建者过滤，
                # 不能让 AI 员工以 admin 身份绕过权限拉取全量数据
                result.localhost_agent_id = agent_id
                return result
    return AuthResult(user_info={'userId': 'localhost', 'role': 'admin'})


def _resolve_talent_owner_id(auth):
    """达人数据归属：返回写入操作应归属的用户 ID（两层架构的子库归属）。
    AI 员工本地调用（localhost_agent_id）归属到该员工的创建者（子账号），
    其余情况归属当前登录用户。"""
    agent_id = getattr(auth, 'localhost_agent_id', None)
    if agent_id:
        agent = _get_agent_by_id(agent_id)
        if agent and agent.get('createdBy'):
            return agent['createdBy']
    return auth.user_info.get('userId', '')


def _is_unidentified_localhost(auth):
    """未携带 X-Agent-Id（或 body agent_id）的匿名 localhost 调用：归属无法确定。
    此类写入会把 created_by 落成 'localhost'，不匹配任何真实用户，子账号永远查不到，
    因此达人录入端点必须拒绝并要求调用方标识 AI 员工身份。"""
    return (auth.user_info or {}).get('userId') == 'localhost' \
        and not getattr(auth, 'localhost_agent_id', None)


# AI 员工 role → 可写入资源：商务=达人侧，运营=商品侧（与角色模板的【权限边界】一致）。
# prompt 约束不可靠，这里在服务端硬拦截——LLM 不遵守 prompt 也越不了权。
_AGENT_ROLE_WRITE_SCOPE = {
    '商务': {'talent'},
    '运营': {'product'},
}


def _check_agent_role_write_scope(auth, resource):
    """AI 员工（localhost_agent_id）按 role 硬拦截写入权限。返回 None 放行，否则 (error, status)。

    - resource: 'talent'（达人录入/修改/删除）或 'product'（商品录入/修改/删除）
    - 真实用户（含管理员、子账号本人）不受此限制，走原有权限体系
    - agent role='admin' 不限；其他 role 只放行 _AGENT_ROLE_WRITE_SCOPE 规定的资源
    """
    agent_id = getattr(auth, 'localhost_agent_id', None)
    if not agent_id:
        return None  # 非 AI 员工调用
    agent = _get_agent_by_id(agent_id)
    role = ((agent or {}).get('role') or '').strip()
    if role == 'admin':
        return None
    # Helen 单独放行：商务角色平时无商品录入权限，Helen 是核心商务 AI，
    # 需要绕过角色硬拦截录入商品。其他商务角色不受影响。
    if agent_id == 'emp_1780199176680':
        return None
    if resource in _AGENT_ROLE_WRITE_SCOPE.get(role, set()):
        return None
    label = '达人' if resource == 'talent' else '商品'
    logger.warning(f'  [RoleGuard] 拒绝 agent {agent_id}（role={role or "未设置"}）写入{label}数据')
    return (f'你的角色（{role or "未设置"}）无此权限：{label}数据的录入/修改不属于你的职责范围', 403)


def _check_talent_write_permission(auth, talent_id=None):
    """达人库两层架构（主库 + 子账号子库）的写权限校验。返回 None 放行，否则 (error, status)。

    - 录入（talent_id=None）：管理员、子账号、AI 员工都可录入，
      归属由 _resolve_talent_owner_id 决定（agent 录入自动进创建者的子库，不污染主库）。
    - 更新/删除：管理员不限；子账号及其 AI 员工只能操作自己子库
      （created_by ∈ {owner} ∪ owner 的 AI 员工 ID）；主库（created_by 为空）仅管理员可动。
    """
    if auth.is_admin and not getattr(auth, 'localhost_agent_id', None):
        return None
    if talent_id is None:
        return None
    owner_id = _resolve_talent_owner_id(auth)
    ids = {owner_id} | set(_get_user_emp_ids(owner_id))
    conn = _db_conn()
    try:
        row = conn.execute('SELECT created_by FROM talents WHERE id = ?', (talent_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return None  # 记录不存在，交给后续 404
    if (row['created_by'] or '') in ids:
        return None
    logger.warning(f'  [SubpoolGuard] 拒绝 {owner_id} 修改/删除他人子库或主库达人 {talent_id}')
    return ('只能操作自己子库的达人数据，主库数据仅管理员可修改', 403)


def _authenticate(headers, client_ip=None, request_handler=None):
    """从请求头中提取并验证 token；本地回环地址仅在无 Bearer token 时走内部快捷通道（agent 调用），
    携带 Authorization: Bearer 的请求即使在 localhost 也必须走正常 JWT 验证"""
    auth_header = headers.get('Authorization', '')
    if client_ip in ('127.0.0.1', 'localhost', '::1') and not auth_header.startswith('Bearer '):
        parsed_body = None
        if request_handler is not None:
            parsed_body = getattr(request_handler, 'cached_body', None)
            # 仅在未提供 X-Agent-Id 且请求可能带 body 时才读取，避免误消耗后续 handler 需要的 body
            if parsed_body is None and not headers.get('X-Agent-Id', '').strip():
                method = getattr(request_handler, 'command', '')
                if method in ('POST', 'PUT', 'PATCH'):
                    content_length = int(headers.get('Content-Length', 0))
                    if content_length > 0:
                        try:
                            raw = request_handler.rfile.read(content_length)
                            parsed_body = json.loads(raw)
                        except Exception:
                            parsed_body = None
                    request_handler.cached_body = parsed_body
        return _get_localhost_auth_result(headers, parsed_body)
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
        logger.error(f'  [Embedding] {entity_type} {entity_id} 生成失败: {e}')
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
        logger.info(f'  [Embedding] 全局未配置 API key，跳过批量构建')
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
    logger.info(f'  [Embedding] 批量构建完成')


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
    """获取 SQLite 数据库连接（线程安全，启用 WAL + 同步模式 NORMAL + 忙等待 5000ms）"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA synchronous=NORMAL;')
    conn.execute('PRAGMA busy_timeout=5000;')
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


def _migrate_credit_tables(conn):
    """积分制算力管控：旧版表结构（user_id/period 维度）与新结构不兼容，
    检测到旧结构时重命名为 *_legacy 保留数据，再按新结构重建。"""
    legacy_marks = {
        'credit_accounts': 'balance',
        'credit_quotas': 'quota_type',
        'credit_usage_log': 'session_id',
    }
    for table, new_col in legacy_marks.items():
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info({table})').fetchall()]
        if cols and new_col not in cols:
            conn.execute(f'ALTER TABLE {table} RENAME TO {table}_legacy')
            logger.info(f'  [Credits] 旧结构表 {table} 已重命名为 {table}_legacy')


def _backfill_openclaw_agents():
    """启动时数据修复：Helen/貂蝉/上官婉儿/孔明 的 openclawAgent 为空时回填为其 id。

    背景：这些员工在 OpenClaw 侧已存在同名 agent（agent 名 = 员工 id），
    但 agents.json 中 openclawAgent 字段为空，导致未关联。
    只在字段为空时回填，已有值不覆盖；幂等，可随每次启动重复执行。
    """
    target_ids = {'emp_1780199176680'}  # Helen
    target_names = {'helen', '貂蝉', '上官婉儿', '孔明'}
    agents = _read_json(AGENTS_FILE, None)
    if not isinstance(agents, list):
        return
    changed = False
    for a in agents:
        if not isinstance(a, dict) or a.get('openclawAgent'):
            continue
        aid = a.get('id', '')
        name = (a.get('name') or '').strip()
        if aid in target_ids or name.lower() in target_names:
            a['openclawAgent'] = aid
            changed = True
            logger.info(f'  [Backfill] openclawAgent 已回填: {name} ({aid})')
    if changed:
        _write_json(AGENTS_FILE, agents)


def _backup_data_dir():
    """启动时将 data/ 快照到 data/backups/YYYYMMDD_HHMMSS/，只保留最近 7 份。

    备份自身目录（backups/）会被排除，避免递归复制。
    """
    backups_root = os.path.join(DATA_DIR, 'backups')
    os.makedirs(backups_root, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    dst = os.path.join(backups_root, ts)
    try:
        shutil.copytree(DATA_DIR, dst, ignore=shutil.ignore_patterns('backups'))
        logger.info(f'[Backup] 数据快照已保存: {os.path.abspath(dst)}')
    except Exception as e:
        logger.error(f'[Backup] 数据快照失败: {e}')
        return

    # 只保留最近 7 份快照（按目录名时间戳识别）
    try:
        snapshots = []
        for name in os.listdir(backups_root):
            full = os.path.join(backups_root, name)
            if not os.path.isdir(full):
                continue
            try:
                datetime.strptime(name, '%Y%m%d_%H%M%S')
            except ValueError:
                continue  # deleted/ 等非快照目录不参与轮换
            snapshots.append(name)
        snapshots.sort()
        for old in snapshots[:-7]:
            shutil.rmtree(os.path.join(backups_root, old), ignore_errors=True)
            logger.info(f'[Backup] 已清理旧快照: {old}')
    except Exception as e:
        logger.error(f'[Backup] 清理旧快照失败: {e}')


def _migrate_talent_categories(conn):
    """存量修复：talents 中 top_categories 非空但 category 与 top_categories[0].name 不一致的记录，
    强制更新为 top_categories[0].name（带货实际数据优先于可能污染的内容标签）。
    top_categories 无数据的不动。幂等，每次启动执行。"""
    try:
        rows = conn.execute(
            "SELECT id, category, top_categories FROM talents "
            "WHERE top_categories IS NOT NULL AND top_categories NOT IN ('', '[]', '{}')").fetchall()
        fixed = 0
        for r in rows:
            try:
                cats = json.loads(r['top_categories'])
                if not isinstance(cats, list) or not cats:
                    continue
                first = cats[0]
                name = first.get('name', '') if isinstance(first, dict) else (first if isinstance(first, str) else '')
                name = (name or '').strip()
                if name and (r['category'] or '') != name:
                    conn.execute('UPDATE talents SET category = ? WHERE id = ?', (name, r['id']))
                    fixed += 1
            except Exception:
                continue
        if fixed:
            conn.commit()
        logger.info(f'  [TalentCategoryFix] 存量类目修正 {fixed} 条')
    except Exception as e:
        logger.warning(f'  [TalentCategoryFix] 存量类目修正失败: {e}')


def init_db():
    """初始化数据库，创建 products 等表（启动时调用）。旧 knowledge 表已废弃，不再建表。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    logger.info(f'[DB] init_db 使用数据库文件: {os.path.abspath(DB_PATH)}')
    conn = _db_conn()
    try:
        # 项目组对话消息表（团队动态：同组 AI 互相可见）
        conn.execute('''
            CREATE TABLE IF NOT EXISTS group_messages (
                id TEXT PRIMARY KEY,
                group_id TEXT,
                agent_id TEXT,
                role TEXT,
                content TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_group_messages_group_time ON group_messages(group_id, created_at DESC)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                subtitle TEXT DEFAULT '',
                main_image TEXT DEFAULT '',
                price REAL DEFAULT 0,
                price_range TEXT DEFAULT '',
                original_price REAL DEFAULT 0,
                shipping_from TEXT DEFAULT '',
                no_shipping_areas TEXT DEFAULT '',
                sku_code TEXT DEFAULT '',
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
        # 兼容旧表：补充 products 可能缺失的列（必须在 CREATE INDEX 之前）
        # 注意：需覆盖全部非主键列，旧库表结构不全时 _product_row_to_dict 会因缺列抛
        # IndexError("No item with that key")（sqlite3.Row 按名取列的行为）
        for _prod_col, _prod_dtype in [
            ('name', "TEXT NOT NULL DEFAULT ''"),
            ('subtitle', "TEXT DEFAULT ''"),
            ('main_image', "TEXT DEFAULT ''"),
            ('price', 'REAL DEFAULT 0'),
            ('price_range', "TEXT DEFAULT ''"),
            ('original_price', 'REAL DEFAULT 0'),
            ('shipping_from', "TEXT DEFAULT ''"),
            ('no_shipping_areas', "TEXT DEFAULT ''"),
            ('sku_code', "TEXT DEFAULT ''"),
            ('brand', "TEXT DEFAULT ''"),
            ('brand_id', "TEXT DEFAULT ''"),
            ('category', "TEXT DEFAULT ''"),
            ('sku_specs', "TEXT DEFAULT '{}'"),
            ('stock', 'INTEGER DEFAULT 0'),
            ('status', "TEXT DEFAULT 'active'"),
            ('monthly_sales', 'INTEGER DEFAULT 0'),
            ('monthly_gmv', 'REAL DEFAULT 0'),
            ('commission_rates', "TEXT DEFAULT '{}'"),
            ('commission_amount', 'REAL DEFAULT 0'),
            ('conversion_rate', 'REAL DEFAULT 0'),
            ('avg_order_value', 'REAL DEFAULT 0'),
            ('influencer_count', 'INTEGER DEFAULT 0'),
            ('talent_count', 'INTEGER DEFAULT 0'),
            ('video_count', 'INTEGER DEFAULT 0'),
            ('live_count', 'INTEGER DEFAULT 0'),
            ('channel_distribution', "TEXT DEFAULT '{}'"),
            ('influencers', "TEXT DEFAULT '[]'"),
            ('audience', "TEXT DEFAULT '{}'"),
            ('ai_analysis', "TEXT DEFAULT '{}'"),
            ('videos', "TEXT DEFAULT '[]'"),
            ('tags', "TEXT DEFAULT '[]'"),
            ('selling_points', "TEXT DEFAULT ''"),
            ('created_by', "TEXT DEFAULT ''"),
            ('created_at', 'INTEGER'),
            ('updated_at', 'INTEGER'),
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
                platform TEXT DEFAULT '抖音',
                price_unit TEXT DEFAULT '元/条',
                avg_views INTEGER DEFAULT 0,
                last_cooperation TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                matched_products TEXT DEFAULT '[]',
                matched_products_updated_at INTEGER DEFAULT 0,
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
            # legacy JSON 达人库（/api/influencers）统一数据源对齐字段
            ('platform', "TEXT DEFAULT '抖音'"), ('price_unit', "TEXT DEFAULT '元/条'"),
            ('avg_views', 'INTEGER DEFAULT 0'), ('last_cooperation', "TEXT DEFAULT ''"),
            ('notes', "TEXT DEFAULT ''"),
            ('matched_products', "TEXT DEFAULT '[]'"), ('matched_products_updated_at', 'INTEGER DEFAULT 0'),
            # 达人数据提取（商务 vision）新增字段，全部 TEXT
            ('total_history_days', "TEXT DEFAULT ''"), ('live_sessions', "TEXT DEFAULT ''"), ('live_views', "TEXT DEFAULT ''"),
            ('video_plays', "TEXT DEFAULT ''"), ('single_video_settlement', "TEXT DEFAULT ''"),
            ('video_completion_rate', "TEXT DEFAULT ''"), ('video_likes', "TEXT DEFAULT ''"), ('video_comments', "TEXT DEFAULT ''"),
            ('video_shares', "TEXT DEFAULT ''"), ('video_interaction_rate', "TEXT DEFAULT ''"), ('video_avg_price', "TEXT DEFAULT ''"),
            ('top_products', "TEXT DEFAULT '[]'"), ('top_categories', "TEXT DEFAULT '[]'"), ('top_brands', "TEXT DEFAULT '[]'"),
            ('fan_city_tier', "TEXT DEFAULT '{}'"), ('fan_group_gender', "TEXT DEFAULT '{}'"), ('fan_group_age', "TEXT DEFAULT '{}'"),
            ('fan_group_crowd', "TEXT DEFAULT '{}'"), ('fan_group_activity', "TEXT DEFAULT '{}'"), ('fan_group_device', "TEXT DEFAULT '{}'"),
            ('fan_group_price', "TEXT DEFAULT '{}'"), ('fan_group_category', "TEXT DEFAULT '{}'"),
            ('live_audience_region', "TEXT DEFAULT '{}'"), ('live_audience_city_tier', "TEXT DEFAULT '{}'"),
            ('video_audience_region', "TEXT DEFAULT '{}'"), ('video_audience_city_tier', "TEXT DEFAULT '{}'"),
        ]:
            _add_column_if_not_exists(conn, 'talents', _talent_col, _talent_dtype)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_status ON talents(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_cooperation ON talents(cooperation_status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_created_by ON talents(created_by)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_category ON talents(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_fan_category ON talents(fan_category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_talents_group ON talents(group_id)')

        # 用户飞书多维表格配置（每个员工绑定自己的飞书表格）
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_feishu_config (
                user_id TEXT PRIMARY KEY,
                app_id TEXT DEFAULT '',
                app_secret TEXT DEFAULT '',
                app_token TEXT DEFAULT '',
                table_id TEXT DEFAULT '',
                updated_at INTEGER DEFAULT 0
            )
        ''')
        _add_column_if_not_exists(conn, 'user_feishu_config', 'product_table_id', 'TEXT DEFAULT ""')

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

        # 知识事件表：分析结论归属到具体达人/商品（实体档案时间线）
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_events (
                id TEXT PRIMARY KEY,
                entity_type TEXT DEFAULT '',
                entity_id TEXT DEFAULT '',
                agent_id TEXT DEFAULT '',
                event_type TEXT DEFAULT 'analysis',
                title TEXT DEFAULT '',
                content_full TEXT NOT NULL,
                content_summary TEXT DEFAULT '',
                conclusions TEXT DEFAULT '{}',
                embedding BLOB,
                source_msg_id TEXT DEFAULT '',
                user_query TEXT DEFAULT '',
                created_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ke_entity ON knowledge_events(entity_type, entity_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ke_agent ON knowledge_events(agent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ke_type ON knowledge_events(event_type)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ke_created ON knowledge_events(created_at DESC)')
        # 阶段4B-P0：事件重要度评分（启发式）与最近检索命中时间
        _add_column_if_not_exists(conn, 'knowledge_events', 'importance_score', 'REAL DEFAULT 5')
        _add_column_if_not_exists(conn, 'knowledge_events', 'last_accessed_at', "TEXT DEFAULT ''")
        # 阶段4B-P0：FTS5 全文索引（三信号混合检索之 BM25 路；FTS5 不可用时置标志降级跳过）
        global _KE_FTS_ENABLED
        try:
            conn.execute('''
                CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_events_fts USING fts5(
                    event_id UNINDEXED, title, summary, conclusions_text)
            ''')
        except Exception as e:
            _KE_FTS_ENABLED = False
            logger.warning(f'  [KnowledgeEvents] FTS5 不可用，全文检索路将跳过: {e}')
        else:
            # 一次性回填：knowledge_events 有而 fts 缺失的行
            try:
                missing = conn.execute(
                    'SELECT ke.id, ke.title, ke.content_summary, ke.conclusions FROM knowledge_events ke '
                    'LEFT JOIN knowledge_events_fts f ON f.event_id = ke.id WHERE f.event_id IS NULL').fetchall()
                for r in missing:
                    conn.execute(
                        'INSERT INTO knowledge_events_fts (event_id, title, summary, conclusions_text) VALUES (?, ?, ?, ?)',
                        (r['id'], r['title'] or '', r['content_summary'] or '', r['conclusions'] or ''))
            except Exception as e:
                logger.warning(f'  [KnowledgeEvents] FTS 回填失败（不影响主流程）: {e}')

        # 规律表（L3）：从同类目分析事件中归纳的跨达人共性规律
        # status 状态机：draft → confirmed/rejected → deprecated；只有 confirmed 进入检索注入
        conn.execute('''
            CREATE TABLE IF NOT EXISTS knowledge_patterns (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                entity_type TEXT DEFAULT '',
                pattern_text TEXT NOT NULL,
                evidence TEXT DEFAULT '[]',
                confidence REAL DEFAULT 0.5,
                status TEXT DEFAULT 'draft',
                source_event_ids TEXT DEFAULT '[]',
                created_by TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kp_category ON knowledge_patterns(category)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kp_status ON knowledge_patterns(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_kp_entity_type ON knowledge_patterns(entity_type)')
        # 阶段4B：规律置信度体系（confidence_score 0-100；verification_level 五级）
        _add_column_if_not_exists(conn, 'knowledge_patterns', 'confidence_score', 'REAL DEFAULT 50')
        _add_column_if_not_exists(conn, 'knowledge_patterns', 'evidence_count', 'INTEGER DEFAULT 0')
        _add_column_if_not_exists(conn, 'knowledge_patterns', 'hit_count', 'INTEGER DEFAULT 0')
        _add_column_if_not_exists(conn, 'knowledge_patterns', 'miss_count', 'INTEGER DEFAULT 0')
        _add_column_if_not_exists(conn, 'knowledge_patterns', 'verification_level', "TEXT DEFAULT 'hypothesis'")
        # 存量迁移（幂等）：只回填仍为默认 hypothesis 的行，按旧 status 映射等级
        try:
            conn.execute("UPDATE knowledge_patterns SET verification_level = 'verified' "
                         "WHERE verification_level = 'hypothesis' AND status IN ('confirmed', 'active')")
            conn.execute("UPDATE knowledge_patterns SET verification_level = 'deprecated' "
                         "WHERE verification_level = 'hypothesis' AND status IN ('rejected', 'deprecated')")
        except Exception as e:
            logger.warning(f'  [KnowledgePatterns] verification_level 迁移跳过: {e}')

        # 合作单表（Deal）：达人-商品合作全流程跟踪
        # status 状态机：pending → negotiating → sample_sent → approved → live → completed/failed
        conn.execute('''
            CREATE TABLE IF NOT EXISTS deals (
                id TEXT PRIMARY KEY,
                talent_id TEXT NOT NULL,
                product_id TEXT DEFAULT '',
                product_name TEXT DEFAULT '',
                deal_type TEXT DEFAULT '',
                commission_rate REAL DEFAULT 0,
                status TEXT DEFAULT 'pending',
                scheduled_at INTEGER DEFAULT 0,
                actual_gmv REAL DEFAULT 0,
                actual_roi REAL DEFAULT 0,
                actual_units INTEGER DEFAULT 0,
                result_note TEXT DEFAULT '',
                predicted_conclusion TEXT DEFAULT '',
                predicted_event_id TEXT DEFAULT '',
                verification TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_deals_talent ON deals(talent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_deals_status ON deals(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_deals_product ON deals(product_id)')
        # 结构化归因字段（终态复盘）：成败原因 / 问题阶段 / 达人方反馈
        _add_column_if_not_exists(conn, 'deals', 'win_loss_category', "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, 'deals', 'key_moment', "TEXT DEFAULT ''")
        _add_column_if_not_exists(conn, 'deals', 'decision_maker_feedback', "TEXT DEFAULT ''")

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
                category_id INTEGER,
                project_id TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at INTEGER,
                updated_at INTEGER
            )
        ''')
        _add_column_if_not_exists(conn, 'knowledge_base', 'category_id', 'INTEGER')
        _add_column_if_not_exists(conn, 'knowledge_base', 'project_id', "TEXT DEFAULT ''")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_emp ON knowledge_base(emp_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_status ON knowledge_base(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_title ON knowledge_base(title)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_category_id ON knowledge_base(category_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_knowledge_base_project_id ON knowledge_base(project_id)')

        # 工具调用日志
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tool_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT,
                tool_call_id TEXT,
                tool_name TEXT,
                meta TEXT,
                output TEXT,
                exit_code INTEGER,
                duration_ms INTEGER,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_agent ON tool_calls(agent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_tool_call_id ON tool_calls(tool_call_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tool_calls_created_at ON tool_calls(created_at)')

        # 通知表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                agent_id TEXT,
                type TEXT,
                title TEXT,
                content TEXT,
                read INTEGER DEFAULT 0,
                created_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_notifications_user_created ON notifications(user_id, created_at DESC)')

        # 用户通知开关
        conn.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id TEXT PRIMARY KEY,
                message_notify INTEGER DEFAULT 1,
                group_urge INTEGER DEFAULT 1,
                task_reminder INTEGER DEFAULT 1
            )
        ''')

        # 违禁词表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS forbidden_words (
                id TEXT PRIMARY KEY,
                word TEXT UNIQUE,
                category TEXT DEFAULT 'general',
                created_by TEXT,
                created_at INTEGER
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_forbidden_words_word ON forbidden_words(word)')

        # 积分制算力管控：员工积分账户 / 配额充值记录 / 消耗明细（1 积分 = 1000 tokens）
        _migrate_credit_tables(conn)
        conn.execute('''
            CREATE TABLE IF NOT EXISTS credit_accounts (
                agent_id TEXT PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                total_recharged INTEGER DEFAULT 0,
                total_consumed INTEGER DEFAULT 0,
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS credit_quotas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                quota_type TEXT NOT NULL,
                quota_amount INTEGER DEFAULT 0,
                effective_from TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (agent_id) REFERENCES credit_accounts(agent_id)
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS credit_usage_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                credits_used INTEGER DEFAULT 0,
                session_id TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (agent_id) REFERENCES credit_accounts(agent_id)
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_credit_quotas_agent ON credit_quotas(agent_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_credit_usage_log_agent ON credit_usage_log(agent_id, created_at)')

        # 任务管理表
        conn.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                assignee TEXT DEFAULT '',
                assignee_name TEXT DEFAULT '',
                creator TEXT DEFAULT '',
                creator_name TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                priority TEXT DEFAULT 'normal',
                deadline TEXT DEFAULT '',
                project_id TEXT DEFAULT '',
                progress TEXT DEFAULT '',
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime')),
                completed_at TEXT DEFAULT ''
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_tasks_creator ON tasks(creator)')

        # FIXME: 大脑知识中枢新增表（保留旧表，不删数据）
        _init_brain_tables(conn)

        # Memory Pipeline L0-L3 分层记忆表 + Token 预算 + Pipeline 状态
        memory_pipeline.create_memory_tables(conn)

        conn.commit()

        # 旧 JSON 数据迁移（幂等）
        _migrate_json_products_to_sqlite()

        # 空表时写入 COOLCHAP 示例数据
        _seed_coolchap_data(conn)

        # 存量修复：top_categories 有数据的达人，category 强制对齐 top_categories[0].name
        _migrate_talent_categories(conn)
    finally:
        conn.close()


# 通知类型与通知开关字段的映射
_NOTIFICATION_TYPE_SWITCH = {
    'message': 'message_notify',
    'tool_call': 'message_notify',
    'group_urge': 'group_urge',
    'task_reminder': 'task_reminder',
}

# 默认通知开关
_DEFAULT_NOTIFICATION_SETTINGS = {'message_notify': 1, 'group_urge': 1, 'task_reminder': 1}


def _get_notification_settings(user_id):
    """读取用户通知开关，无记录时返回默认全开"""
    conn = _db_conn()
    try:
        row = conn.execute(
            'SELECT message_notify, group_urge, task_reminder FROM user_settings WHERE user_id = ?',
            (user_id,)
        ).fetchone()
        if not row:
            return dict(_DEFAULT_NOTIFICATION_SETTINGS)
        return {
            'message_notify': int(row['message_notify'] if row['message_notify'] is not None else 1),
            'group_urge': int(row['group_urge'] if row['group_urge'] is not None else 1),
            'task_reminder': int(row['task_reminder'] if row['task_reminder'] is not None else 1),
        }
    finally:
        conn.close()


def _push_notification(user_id, type, title, content, agent_id=None):
    """写入一条通知（先检查用户对应的通知开关，关闭则不推）"""
    if not user_id:
        return None
    switch_key = _NOTIFICATION_TYPE_SWITCH.get(type, 'message_notify')
    try:
        settings = _get_notification_settings(user_id)
        if not settings.get(switch_key, 1):
            return None
        notif_id = 'notif_' + uuid.uuid4().hex[:12]
        conn = _db_conn()
        try:
            conn.execute('''
                INSERT INTO notifications (id, user_id, agent_id, type, title, content, read, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
            ''', (notif_id, user_id, agent_id or '', type, title or '', content or '', int(time.time() * 1000)))
            conn.commit()
        finally:
            conn.close()
        return notif_id
    except Exception as e:
        logger.error(f'  [Notify] 推送通知失败 user={user_id} type={type}: {e}')
        return None


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
    'created_by', 'original_price', 'shipping_from', 'no_shipping_areas', 'sku_code', 'created_at', 'updated_at'
]


def _product_row_to_dict(row):
    """将 products 表的 sqlite3.Row 转为前端兼容 dict（容忍旧库表缺列，缺列用默认值）"""
    if not row:
        return None

    cols = set(row.keys())

    def _col(col, default=None):
        return row[col] if col in cols else default

    def _json_col(col, default=None):
        val = _col(col)
        if val is None:
            return default
        try:
            return json.loads(val)
        except Exception:
            return default

    product = {
        'id': _col('id'),
        'name': _col('name') or '',
        'subtitle': _col('subtitle') or '',
        'main_image': _col('main_image') or '',
        'price': _col('price') if _col('price') is not None else 0,
        'price_range': _col('price_range') or '',
        'brand': _col('brand') or '',
        'brand_id': _col('brand_id') or '',
        'category': _col('category') or '',
        'sku_specs': _json_col('sku_specs', {}),
        'stock': _col('stock') if _col('stock') is not None else 0,
        'status': _col('status') or 'active',
        'monthly_sales': _col('monthly_sales') if _col('monthly_sales') is not None else 0,
        'monthly_gmv': _col('monthly_gmv') if _col('monthly_gmv') is not None else 0,
        'commission_rates': _json_col('commission_rates', {}),
        'commission_amount': _col('commission_amount') if _col('commission_amount') is not None else 0,
        'conversion_rate': _col('conversion_rate') if _col('conversion_rate') is not None else 0,
        'avg_order_value': _col('avg_order_value') if _col('avg_order_value') is not None else 0,
        'influencer_count': _col('influencer_count') if _col('influencer_count') is not None else 0,
        'talent_count': _col('talent_count') if _col('talent_count') is not None else 0,
        'video_count': _col('video_count') if _col('video_count') is not None else 0,
        'live_count': _col('live_count') if _col('live_count') is not None else 0,
        'channel_distribution': _json_col('channel_distribution', {}),
        'influencers': _json_col('influencers', []),
        'audience': _json_col('audience', {}),
        'ai_analysis': _json_col('ai_analysis', {}),
        'videos': _json_col('videos', []),
        'tags': _json_col('tags', []),
        'selling_points': _col('selling_points') or '',
        'created_by': _col('created_by') or '',
        'original_price': _col('original_price') if _col('original_price') is not None else 0,
        'shipping_from': _col('shipping_from') or '',
        'shipping_note': _col('no_shipping_areas') or '',
        'sku_code': _col('sku_code') or '',
        'created_at': _col('created_at'),
        'updated_at': _col('updated_at'),
        'createdAt': _col('created_at'),
        'updatedAt': _col('updated_at'),
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
        'original_price': float(_get('original_price', 'originalPrice', default=0) or 0),
        'shipping_from': _get('shipping_from', 'shippingFrom', default='') or '',
        'no_shipping_areas': _get('no_shipping_areas', 'shipping_note', 'shippingNote', 'noShippingAreas', default='') or '',
        'sku_code': _get('sku_code', 'skuCode', default='') or '',
        'created_at': _get('created_at', 'createdAt'),
        'updated_at': _get('updated_at', 'updatedAt'),
    }


_PRODUCT_JSON_PATH = os.path.join(DATA_DIR, 'products.json')


def _load_json_products():
    """加载 data/products.json（兼容旧版格式）"""
    return _read_json(_PRODUCT_JSON_PATH, {'products': [], 'total': 0, 'version': '1.0'})


def _save_json_products(data):
    """保存 data/products.json"""
    _write_json(_PRODUCT_JSON_PATH, data)


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
    'ai_analysis',
    'total_history_days', 'live_sessions', 'live_views', 'video_plays',
    'single_video_settlement', 'video_completion_rate', 'video_likes', 'video_comments',
    'video_shares', 'video_interaction_rate', 'video_avg_price',
    'top_products', 'top_categories', 'top_brands',
    'fan_city_tier', 'fan_group_gender', 'fan_group_age', 'fan_group_crowd',
    'fan_group_activity', 'fan_group_device', 'fan_group_price', 'fan_group_category',
    'live_audience_region', 'live_audience_city_tier',
    'video_audience_region', 'video_audience_city_tier',
    'ai_reason', 'risk_rating', 'group_id', 'status', 'created_by',
    'platform', 'price_unit', 'avg_views', 'last_cooperation', 'notes',
    'matched_products', 'matched_products_updated_at',
    'created_at', 'updated_at'
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
        'category': row['category'] or '',
        'content_style': row['content_style'] or '',
        'contentStyle': row['content_style'] or '',
        'fans_profile': _json_col('fans_profile', {}),
        'ai_tags': _json_col('ai_tags', []),
        'ai_rating': row['ai_rating'] or '',
        'ai_summary': row['ai_summary'] or '',
        'ai_analysis': row['ai_analysis'] or row['ai_summary'] or '',
        'total_history_days': row['total_history_days'] or '',
        'live_sessions': row['live_sessions'] or '',
        'live_views': row['live_views'] or '',
        'video_plays': row['video_plays'] or '',
        'single_video_settlement': row['single_video_settlement'] or '',
        'video_completion_rate': row['video_completion_rate'] or '',
        'video_likes': row['video_likes'] or '',
        'video_comments': row['video_comments'] or '',
        'video_shares': row['video_shares'] or '',
        'video_interaction_rate': row['video_interaction_rate'] or '',
        'video_avg_price': row['video_avg_price'] or '',
        'top_products': _json_col('top_products', []),
        'top_categories': _json_col('top_categories', []),
        'top_brands': _json_col('top_brands', []),
        'fan_city_tier': _json_col('fan_city_tier', {}),
        'fan_group_gender': _json_col('fan_group_gender', {}),
        'fan_group_age': _json_col('fan_group_age', {}),
        'fan_group_crowd': _json_col('fan_group_crowd', {}),
        'fan_group_activity': _json_col('fan_group_activity', {}),
        'fan_group_device': _json_col('fan_group_device', {}),
        'fan_group_price': _json_col('fan_group_price', {}),
        'fan_group_category': _json_col('fan_group_category', {}),
        'live_audience_region': _json_col('live_audience_region', {}),
        'live_audience_city_tier': _json_col('live_audience_city_tier', {}),
        'video_audience_region': _json_col('video_audience_region', {}),
        'video_audience_city_tier': _json_col('video_audience_city_tier', {}),
        'ai_reason': row['ai_reason'] or '',
        'risk_rating': row['risk_rating'] or '',
        'group_id': row['group_id'] or '',
        'status': row['status'] or 'active',
        'platform': row['platform'] or '抖音',
        'price_unit': row['price_unit'] or '元/条',
        'avg_views': row['avg_views'] if row['avg_views'] is not None else 0,
        'last_cooperation': row['last_cooperation'] or '',
        'notes': row['notes'] or '',
        'matched_products': _json_col('matched_products', []),
        'matched_products_updated_at': row['matched_products_updated_at'] if row['matched_products_updated_at'] is not None else 0,
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'createdAt': row['created_at'],
        'updatedAt': row['updated_at'],
        'created_by': row['created_by'] or '',
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


def _parse_number_tolerant(val, is_int=False):
    """数值字段容错解析：'100-500' 区间格式取两个端点平均值；支持 %、逗号。
    返回 (ok, number)；无法解析时返回 (False, None)，由调用方跳过该字段。"""
    if val is None or val == '':
        return True, 0 if is_int else 0.0
    if isinstance(val, bool):
        return False, None
    if isinstance(val, (int, float)):
        return True, int(val) if is_int else float(val)
    s = str(val).replace('%', '').replace(',', '').strip()
    if not s:
        return True, 0 if is_int else 0.0
    try:
        return True, int(float(s)) if is_int else float(s)
    except ValueError:
        pass
    # 区间格式：取两个端点的平均值
    if '-' in s:
        parts = s.split('-')
        if len(parts) == 2:
            try:
                lo, hi = float(parts[0].strip()), float(parts[1].strip())
                avg = (lo + hi) / 2
                return True, int(avg) if is_int else avg
            except ValueError:
                pass
    return False, None


# PUT /api/talents 数值字段容错：区间值取均值，无法解析的字段跳过（不更新），不返回 500
_TALENT_FLOAT_FIELDS = ('commission_requirement', 'fulfillment_score', 'rating_score', 'total_gmv',
                        'average_price', 'live_ratio', 'video_ratio', 'avg_live_gmv', 'live_gpm', 'video_gpm')
_TALENT_INT_FIELDS = ('followers', 'next_follow_up_at', 'total_products', 'product_count',
                      'total_shops', 'avg_views', 'matched_products_updated_at',
                      'nextFollowUpAt', 'avgViews', 'matchedProductsUpdatedAt')


def _sanitize_talent_numeric_fields(body, talent_id=''):
    """在合并 body 前把数值字段规范化；无法解析的字段从 body 移除（保留原值）。"""
    try:
        for f in _TALENT_FLOAT_FIELDS:
            if f in body:
                ok, num = _parse_number_tolerant(body[f], is_int=False)
                if ok:
                    body[f] = num
                else:
                    logger.warning(f'  [Talents] PUT {talent_id} 字段 {f} 无法解析为数值: {body[f]!r}，跳过更新')
                    body.pop(f, None)
        for f in _TALENT_INT_FIELDS:
            if f in body:
                ok, num = _parse_number_tolerant(body[f], is_int=True)
                if ok:
                    body[f] = num
                else:
                    logger.warning(f'  [Talents] PUT {talent_id} 字段 {f} 无法解析为数值: {body[f]!r}，跳过更新')
                    body.pop(f, None)
    except Exception as e:
        logger.warning(f'  [Talents] PUT {talent_id} 数值字段容错处理异常: {e}')
    return body


def _dict_to_talent_row(t):
    def _dump(val):
        if val is None:
            return '{}'
        return json.dumps(val, ensure_ascii=False)
    now = int(time.time() * 1000)
    # category 强制优先级：main_category > top_categories[0].name > category > ''
    # top_categories 是带货实际数据，比客户端/LLM 传入的 category（可能是内容标签）可靠
    _category = t.get('main_category') or t.get('mainCategory') or ''
    if not _category:
        # top_categories 有数据时强制取第一条类目名（兼容 JSON 字符串 / list，元素兼容 dict / str）
        _top_cats = t.get('top_categories') or t.get('topCategories') or []
        if isinstance(_top_cats, str):
            try:
                _top_cats = json.loads(_top_cats)
            except Exception:
                _top_cats = []
        if isinstance(_top_cats, list) and _top_cats:
            _first = _top_cats[0]
            if isinstance(_first, dict) and _first.get('name'):
                _category = _first['name']
            elif isinstance(_first, str):
                _category = _first
    if not _category:
        _category = t.get('category') or ''
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
        'category': _category,
        'content_style': t.get('content_style') or t.get('contentStyle') or '',
        'fans_profile': _dump(t.get('fans_profile', t.get('fansProfile', {}))),
        'ai_tags': _dump(t.get('ai_tags', t.get('aiTags', []))),
        'ai_rating': t.get('ai_rating') or t.get('aiRating') or '',
        'ai_summary': t.get('ai_summary') or t.get('aiSummary') or '',
        'ai_analysis': t.get('ai_analysis') or t.get('aiAnalysis') or t.get('ai_summary') or t.get('aiSummary') or '',
        'total_history_days': t.get('total_history_days') or t.get('totalHistoryDays') or '',
        'live_sessions': t.get('live_sessions') or t.get('liveSessions') or '',
        'live_views': t.get('live_views') or t.get('liveViews') or '',
        'video_plays': t.get('video_plays') or t.get('videoPlays') or '',
        'single_video_settlement': t.get('single_video_settlement') or t.get('singleVideoSettlement') or '',
        'video_completion_rate': t.get('video_completion_rate') or t.get('videoCompletionRate') or '',
        'video_likes': t.get('video_likes') or t.get('videoLikes') or '',
        'video_comments': t.get('video_comments') or t.get('videoComments') or '',
        'video_shares': t.get('video_shares') or t.get('videoShares') or '',
        'video_interaction_rate': t.get('video_interaction_rate') or t.get('videoInteractionRate') or '',
        'video_avg_price': t.get('video_avg_price') or t.get('videoAvgPrice') or '',
        'top_products': _dump(t.get('top_products', t.get('topProducts', []))),
        'top_categories': _dump(t.get('top_categories', t.get('topCategories', []))),
        'top_brands': _dump(t.get('top_brands', t.get('topBrands', []))),
        'fan_city_tier': _dump(t.get('fan_city_tier', t.get('fanCityTier', {}))),
        'fan_group_gender': _dump(t.get('fan_group_gender', t.get('fanGroupGender', {}))),
        'fan_group_age': _dump(t.get('fan_group_age', t.get('fanGroupAge', {}))),
        'fan_group_crowd': _dump(t.get('fan_group_crowd', t.get('fanGroupCrowd', {}))),
        'fan_group_activity': _dump(t.get('fan_group_activity', t.get('fanGroupActivity', {}))),
        'fan_group_device': _dump(t.get('fan_group_device', t.get('fanGroupDevice', {}))),
        'fan_group_price': _dump(t.get('fan_group_price', t.get('fanGroupPrice', {}))),
        'fan_group_category': _dump(t.get('fan_group_category', t.get('fanGroupCategory', {}))),
        'live_audience_region': _dump(t.get('live_audience_region', t.get('liveAudienceRegion', {}))),
        'live_audience_city_tier': _dump(t.get('live_audience_city_tier', t.get('liveAudienceCityTier', {}))),
        'video_audience_region': _dump(t.get('video_audience_region', t.get('videoAudienceRegion', {}))),
        'video_audience_city_tier': _dump(t.get('video_audience_city_tier', t.get('videoAudienceCityTier', {}))),
        'ai_reason': t.get('ai_reason') or t.get('aiReason') or '',
        'risk_rating': t.get('risk_rating') or t.get('riskRating') or '',
        'group_id': t.get('group_id') or t.get('groupId') or '',
        'status': t.get('status') or 'active',
        'created_by': t.get('created_by') or t.get('createdBy') or '',
        'platform': t.get('platform') or '抖音',
        'price_unit': t.get('price_unit') or t.get('priceUnit') or '元/条',
        'avg_views': int(t.get('avg_views', t.get('avgViews', 0)) or 0),
        'last_cooperation': t.get('last_cooperation') or t.get('lastCooperation') or '',
        'notes': t.get('notes') or '',
        'matched_products': _dump(t.get('matched_products', t.get('matchedProducts', []))),
        'matched_products_updated_at': int(t.get('matched_products_updated_at', t.get('matchedProductsUpdatedAt', 0)) or 0),
        'created_at': t.get('created_at') or t.get('createdAt') or now,
        'updated_at': now,
    }


def _parse_engagement_rate(val):
    """video_interaction_rate 可能是 '5.2%' 或 '5.2' 或数值，统一解析为 float"""
    if val is None or val == '':
        return 0
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(str(val).replace('%', '').strip())
    except (TypeError, ValueError):
        return 0


def _talent_dict_to_influencer(t):
    """把 talents 表记录（_talent_row_to_dict 输出）转换为 legacy /api/influencers 的 JSON 字段形状。
    统一数据源后 /api/influencers 系列接口直查 SQLite，仅字段形状保持 legacy 兼容。"""
    return {
        'id': t.get('id') or '',
        'name': t.get('name') or '',
        'avatar': t.get('avatar') or '',
        'platform': t.get('platform') or '抖音',
        'accountId': t.get('douyin_id') or '',
        'followerCount': t.get('followers') or 0,
        'category': t.get('category') or '',
        'tags': t.get('tags') or [],
        'bio': t.get('bio') or '',
        'contentStyle': t.get('content_style') or '',
        'cooperationPrice': t.get('average_price') or 0,
        'priceUnit': t.get('price_unit') or '元/条',
        'contact': t.get('contact') or '',
        'status': t.get('cooperation_status') or 'available',
        'engagementRate': _parse_engagement_rate(t.get('video_interaction_rate')),
        'avgViews': t.get('avg_views') or 0,
        'lastCooperation': t.get('last_cooperation') or None,
        'notes': t.get('notes') or '',
        'createdBy': t.get('created_by') or '',
        'createdAt': t.get('created_at') or 0,
        'updatedAt': t.get('updated_at') or 0,
        'matched_products': t.get('matched_products') or [],
        'matched_products_updated_at': t.get('matched_products_updated_at') or 0,
    }


def _influencer_body_to_talent(body):
    """把 legacy /api/influencers 请求体/JSON 记录映射为 talents 表字段（供 _dict_to_talent_row 使用）"""
    return {
        'id': body.get('id'),
        'name': body.get('name'),
        'avatar': body.get('avatar', ''),
        'platform': body.get('platform') or '抖音',
        'douyin_id': body.get('accountId', ''),
        'followers': body.get('followerCount', 0),
        'category': body.get('category', ''),
        'tags': body.get('tags', []),
        'bio': body.get('bio', ''),
        'content_style': body.get('contentStyle', ''),
        'average_price': body.get('cooperationPrice', 0),
        'price_unit': body.get('priceUnit') or '元/条',
        'contact': body.get('contact', ''),
        'cooperation_status': body.get('status', 'available'),
        'video_interaction_rate': str(body.get('engagementRate', '') or ''),
        'avg_views': body.get('avgViews', 0),
        'last_cooperation': body.get('lastCooperation') or '',
        'notes': body.get('notes', ''),
    }


def _insert_talent_row(conn, row):
    """按 _TALENT_COLUMNS 插入一条 talents 记录"""
    conn.execute(
        f"INSERT INTO talents ({', '.join(_TALENT_COLUMNS)}) VALUES ({', '.join('?' * len(_TALENT_COLUMNS))})",
        tuple(row[c] for c in _TALENT_COLUMNS))


def _migrate_influencers_json_to_sqlite():
    """把 data/influencers/index.json 里的 legacy 达人记录幂等导入 talents 表（跳过 id 已存在的）。
    统一数据源迁移：导入后 JSON 仅作启动导出的只读缓存，不再是数据源。返回 (导入数, 跳过数)。"""
    index_path = os.path.join(INFLUENCER_DIR, 'index.json')
    data = _read_json(index_path, None)
    if not data or not isinstance(data.get('influencers'), list):
        return (0, 0)
    imported = skipped = 0
    conn = _db_conn()
    try:
        existing_ids = {r[0] for r in conn.execute('SELECT id FROM talents').fetchall()}
        for inf in data['influencers']:
            inf_id = inf.get('id')
            if not inf_id or not inf.get('name'):
                continue
            if inf_id in existing_ids:
                skipped += 1
                continue
            talent = _influencer_body_to_talent(inf)
            talent['id'] = inf_id
            talent['status'] = 'active'
            talent['created_by'] = inf.get('createdBy') or ''
            talent['created_at'] = inf.get('createdAt') or int(time.time() * 1000)
            _insert_talent_row(conn, _dict_to_talent_row(talent))
            imported += 1
        conn.commit()
    finally:
        conn.close()
    if imported or skipped:
        logger.info(f'  [Influencer] JSON→SQLite 迁移: 导入 {imported} 条, 跳过已存在 {skipped} 条')
    return (imported, skipped)


def _export_influencers_json_cache():
    """把 SQLite talents 表导出为 data/influencers/ 下的 JSON 只读缓存（index.json + 详情文件）。
    统一数据源后 JSON 仅供旧工具/调试查看，不再作为写入目标。"""
    try:
        os.makedirs(INFLUENCER_DIR, exist_ok=True)
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM talents WHERE COALESCE(status, 'active') != 'archived'").fetchall()
        finally:
            conn.close()
        influencers = [_talent_dict_to_influencer(_talent_row_to_dict(r)) for r in rows]
        _write_json(os.path.join(INFLUENCER_DIR, 'index.json'),
                    {'version': '1.0', 'influencers': influencers})
        for inf in influencers:
            _write_json(os.path.join(INFLUENCER_DIR, f"{inf['id']}.json"), inf)
    except Exception as e:
        logger.error(f'  [Influencer] 导出 JSON 缓存失败: {e}')

# ===== 飞书多维表格同步 =====
FEISHU_BITABLE_APP_ID = os.environ.get('FEISHU_BITABLE_APP_ID', '')
FEISHU_BITABLE_APP_SECRET = os.environ.get('FEISHU_BITABLE_APP_SECRET', '')
FEISHU_BITABLE_APP_TOKEN = os.environ.get('FEISHU_BITABLE_APP_TOKEN', 'QxARbgMSIaKcXxsoGEtcSJH2nvf')
FEISHU_BITABLE_TABLE_ID = os.environ.get('FEISHU_BITABLE_TABLE_ID', 'tbl2OAYCIoV6Nko8')

def _feishu_get_tenant_access_token(app_id=None, app_secret=None):
    app_id = app_id or FEISHU_BITABLE_APP_ID
    app_secret = app_secret or FEISHU_BITABLE_APP_SECRET
    url = 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal'
    data = json.dumps({'app_id': app_id, 'app_secret': app_secret}).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json; charset=utf-8'})
    resp = urllib.request.urlopen(req, timeout=10)
    result = json.loads(resp.read().decode('utf-8'))
    if result.get('code') != 0:
        raise Exception(f"Feishu auth failed: {result}")
    return result['tenant_access_token']

def _feishu_extract_val(val):
    if val is None:
        return ''
    if isinstance(val, (int, float)):
        return val
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        parts = []
        for item in val:
            if isinstance(item, dict):
                parts.append(item.get('text', '') or item.get('name', ''))
            elif isinstance(item, str):
                parts.append(item)
        return ','.join(parts) if parts else ''
    if isinstance(val, dict):
        return val.get('text', '') or val.get('name', '')
    return str(val)

def _parse_cn_number(val, default_wan=False):
    if not val:
        return 0
    s = str(val).strip().lower().replace(',', '')
    if not s:
        return 0
    try:
        mult = 1
        if s.endswith('万') or s.endswith('w'):
            mult = 10000
            s = s[:-1]
        elif s.endswith('亿'):
            mult = 100000000
            s = s[:-1]
        elif default_wan and '.' in s:
            mult = 10000
        return int(float(s) * mult)
    except (ValueError, TypeError):
        return 0

def _parse_distribution_text(val):
    """解析飞书分布列纯文本（如 "0~25 9.35%, 25~50 15.2%"）为 JSON 字符串 '{"0~25":9.35,"25~50":15.2}'；
    已是合法 JSON 时原样返回；解析失败保留原始值。"""
    if val is None or val == '':
        return '{}'
    if not isinstance(val, str):
        return val
    text = val.strip()
    if not text:
        return '{}'
    if text.startswith('{'):
        try:
            if isinstance(json.loads(text), dict):
                return text
        except Exception:
            pass
    try:
        result = {}
        for part in _re.split(r'[,，、;；\n]+', text):
            part = part.strip()
            if not part:
                continue
            m = _re.match(r'^(.+?)[\s:：]+([0-9]+(?:\.[0-9]+)?)\s*%?$', part)
            if not m:
                raise ValueError(f'无法解析分布项: {part}')
            result[m.group(1).strip()] = float(m.group(2))
        if not result:
            raise ValueError('分布为空')
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return val

def _feishu_record_to_talent(fields):
    def _g(name):
        return _feishu_extract_val(fields.get(name))
    return {
        'name': str(_g('达人名称') or ''),
        'douyin_id': str(_g('抖音账号') or ''),
        'real_name': '',
        'wechat': '',
        'phone': '',
        'email': '',
        'city': '',
        'level': '',
        'followers': _parse_cn_number(_g('粉丝量数'), default_wan=True),
        'talent_type': str(_g('内容类型') or ''),
        'agency': '',
        'tags': [],
        'category': str(_g('类目') or ''),
        'bio': '',
        'contact_name': '',
        'contact_phone': '',
        'contact_wechat': '',
        'cooperation_status': 'available',
        'commission_requirement': 0,
        'content_style': str(_g('内容风格') or ''),
        'follow_up_note': '',
        'account_fans_profile': str(_g('账号粉丝特征') or ''),
        'video_fans_profile': str(_g('短视频粉丝特征') or ''),
        'video_settlement_ratio': str(_g('视频结算额占比') or ''),
        'single_video_settlement': str(_g('单视频结算额') or ''),
        'feishu_gpm': str(_g('视频GPM') or ''),
        'video_avg_price': str(_g('视频平均件单价') or ''),
        'feishu_shops': str(_g('合作店铺数') or ''),
        'feishu_product_count': str(_g('带货商品数') or ''),
        'monthly_settlement': str(_g('月结算金额') or ''),
        'price_distribution': _parse_distribution_text(_g('价格带分布')),
        'category_distribution': _parse_distribution_text(_g('类目分布')),
        'brand_distribution': _parse_distribution_text(_g('品牌集中度')) or '{}',
        'fan_gender': _g('粉丝性别') or '{}',
        'fan_age': _g('粉丝年龄') or '{}',
        'fan_region': _g('粉丝地域') or '{}',
        'fan_crowd': str(_g('粉丝人群') or ''),
        'fan_price_range': str(_g('粉丝价格带') or ''),
        'fan_category': str(_g('粉丝类目偏好') or ''),
        'remark': str(_g('备注') or ''),
    }

def _feishu_list_all_records(token, app_token=None, table_id=None):
    app_token = app_token or FEISHU_BITABLE_APP_TOKEN
    table_id = table_id or FEISHU_BITABLE_TABLE_ID
    all_records = []
    page_token = None
    while True:
        url = f'https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100'
        if page_token:
            url += f'&page_token={page_token}'
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json; charset=utf-8'})
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read().decode('utf-8'))
        if result.get('code') != 0:
            raise Exception(f"Feishu list records failed: {result}")
        all_records.extend(result.get('data', {}).get('items', []))
        page_token = result.get('data', {}).get('page_token', '')
        if not result.get('data', {}).get('has_more', False):
            break
    return all_records


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
    logger.info('  [Product] 发现旧版 JSON 商品库，开始迁移到 SQLite...')
    data = _read_json(old_path, {'products': []})
    products = data.get('products', [])
    if not products:
        try:
            os.rename(old_path, old_path + '.bak')
            logger.info('  [Product] 旧 JSON 为空，已备份')
        except Exception as e:
            logger.error(f'  [Product] 备份旧 JSON 失败: {e}')
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
        logger.info(f'  [Product] JSON 迁移完成: 插入 {inserted} 条, 跳过 {skipped} 条')
    finally:
        conn.close()

    try:
        bak_path = old_path + '.bak'
        if os.path.exists(bak_path):
            os.remove(bak_path)
        os.rename(old_path, bak_path)
        logger.info(f'  [Product] 旧 JSON 已备份: {bak_path}')
    except Exception as e:
        logger.error(f'  [Product] 备份旧 JSON 失败: {e}')


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
        'categoryId': row['category_id'],
        'projectId': row['project_id'] or '',
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


# 知识库自动分类规则：(内容关键词组, 分类名包含字)
_KB_CATEGORY_RULES = [
    (('达人', '带货', '粉丝', '人群分析'), '达人'),
    (('商品', '选品', '开衫', 'SKU'), '商品'),
    (('投流', '自然流', '流量', 'ROI'), '流量'),
]


def _guess_kb_category_id(conn, content):
    """根据内容关键词在 knowledge_categories 全表按名称匹配分类，返回 category_id。
    命中业务关键词 → name 含对应字（达人/商品/流量）的分类，不存在则自动创建对应分类；
    未命中任何业务词 → name 含“公共”的分类，不存在则自动创建“公共知识”。
    不再依赖“抖音团长”父分类；匹配/创建成功后返回分类 id。"""
    try:
        cats = conn.execute('SELECT id, name FROM knowledge_categories').fetchall()
    except Exception:
        return None
    text = content or ''

    def _find_by_name(hint):
        for c in cats:
            if hint in (c['name'] or ''):
                return c['id']
        return None

    def _create_category(name):
        try:
            row = conn.execute(
                'SELECT COALESCE(MAX(sort_order), 0) AS max_sort FROM knowledge_categories '
                'WHERE parent_id IS NULL AND project_id = ?',
                ('',)
            ).fetchone()
            sort_order = (row['max_sort'] or 0) + 1
            cur = conn.execute(
                'INSERT INTO knowledge_categories (name, parent_id, project_id, sort_order) VALUES (?, NULL, ?, ?)',
                (name, '', sort_order)
            )
            conn.commit()
            return cur.lastrowid
        except Exception:
            return None

    def _resolve(hint, create_name):
        cid = _find_by_name(hint)
        if cid is None:
            cid = _create_category(create_name)
        return cid

    # 命中业务关键词 → 对应名称分类，不存在则自动创建
    for keywords, name_hint in _KB_CATEGORY_RULES:
        if any(kw in text for kw in keywords):
            return _resolve(name_hint, name_hint + '知识')
    # 未命中任何业务关键词 → 公共知识，不存在则自动创建
    return _resolve('公共', '公共知识')


def _kb_category_name_by_id(conn, category_id):
    """按 id 查 knowledge_categories 名称，查不到返回空串（用于同步 kb_entries.category 冗余字段）"""
    if not category_id:
        return ''
    try:
        row = conn.execute('SELECT name FROM knowledge_categories WHERE id = ?', (category_id,)).fetchone()
        return (row['name'] or '') if row else ''
    except Exception:
        return ''


def _upsert_knowledge_base(kb):
    """插入或更新 kb_entries（新版知识库表）；status='active' 映射为 'ok'，其余为 'pending'。
    旧表 knowledge_base 已废弃，不再写入。"""
    conn = _db_conn()
    try:
        now = int(time.time() * 1000)
        kb_id = kb.get('id') or ('kb_' + str(uuid.uuid4())[:8])
        content = kb.get('content', '')
        # 状态映射：active → ok；决策关键词触发也直接 ok；其余 pending
        new_status = 'ok' if kb.get('status') == 'active' or _contains_decision_keyword(content) else 'pending'
        category_id = int(kb.get('categoryId') or kb.get('category_id')) if kb.get('categoryId') or kb.get('category_id') else None
        # 未显式指定分类时按内容关键词自动分类
        if category_id is None:
            category_id = _guess_kb_category_id(conn, content)
        # category 冗余名称字段始终与 category_id 同步
        category_name = _kb_category_name_by_id(conn, category_id)
        project_id = (kb.get('projectId') or kb.get('project_id') or '').strip()
        emp_id = kb.get('empId') or kb.get('emp_id') or ''
        # 按 id 更新
        existing = conn.execute(
            'SELECT status, category_id, project_id FROM kb_entries WHERE id = ?', (kb_id,)
        ).fetchone()
        if existing:
            if existing['status'] == 'ok':
                new_status = 'ok'
            final_category_id = category_id if category_id is not None else existing['category_id']
            final_category_name = category_name if category_id is not None else _kb_category_name_by_id(conn, existing['category_id'])
            conn.execute('''
                UPDATE kb_entries SET title=?, content=?, category_id=?, category=?, project_id=?, status=?, updated_at=?
                WHERE id=?
            ''', (
                kb.get('title', ''), content,
                final_category_id, final_category_name,
                project_id or existing['project_id'],
                new_status, now, kb_id
            ))
            conn.commit()
            return kb_id
        # 按内容相似合并（简单子串匹配），避免重复条目
        candidates = conn.execute(
            "SELECT id, title, content, status FROM kb_entries WHERE (emp_id=? OR emp_id='' OR emp_id IS NULL) AND status='ok'",
            (emp_id,)
        ).fetchall()
        for cand in candidates:
            if content and (content in cand['content'] or cand['content'] in content or content in cand['title']):
                # 回填自动匹配到的 category_id 及分类名称（原条目已有分类时保留）
                if category_id is not None:
                    conn.execute(
                        'UPDATE kb_entries SET status=?, updated_at=?, category_id=?, category=? WHERE id=?',
                        ('ok' if new_status == 'ok' else cand['status'], now, category_id, category_name, cand['id'])
                    )
                else:
                    conn.execute(
                        'UPDATE kb_entries SET status=?, updated_at=? WHERE id=?',
                        ('ok' if new_status == 'ok' else cand['status'], now, cand['id'])
                    )
                conn.commit()
                return cand['id']
        created_at = kb.get('createdAt') or kb.get('created_at') or now
        conn.execute('''
            INSERT INTO kb_entries (id, title, content, category, category_id, project_id, scope, emp_id, status, chunk_count, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'global', ?, ?, 0, '', ?, ?)
        ''', (
            kb_id, kb.get('title', ''), content,
            category_name, category_id, project_id, emp_id,
            new_status, created_at, now
        ))
        conn.commit()
        return kb_id
    finally:
        conn.close()


# 知识库写入质量过滤：命中以下任一关键词的内容视为系统操作反馈（识别失败/数据为空等），不写入知识库
_KB_LOW_QUALITY_KEYWORDS = [
    '未能识别', '提取为空', '未能提取', '请用户重新发送', '请用户重发',
    '无法识别', '识别失败',
    '你识别一下', '帮我看看', '你看一下', '分析一下',
]

# 有效数据字段/线索：短内容中不含这些则判为低质量
_KB_DATA_FIELD_HINTS = ['product_count', 'total_gmv', 'gmv', '达人', '粉丝']


def _is_low_quality_knowledge(content, title=''):
    """判断内容是否为无价值的操作反馈，命中则不应写入知识库（过滤条件集中在此，便于扩展）。"""
    text = (title or '') + '\n' + (content or '')
    for kw in _KB_LOW_QUALITY_KEYWORDS:
        if kw in text:
            return True
    # vision 数据提取流程：输出扁平 JSON，若提取字段全为空（null/0/''）则视为无效提取
    if 'product_count' in text or 'total_gmv' in text:
        try:
            m = re.search(r'\{.*\}', text, re.S)
            if m:
                data = json.loads(m.group(0))
                if isinstance(data, dict) and data:
                    def _empty(v):
                        return v is None or v in ('', 0, '0', 'null')
                    if all(_empty(v) for v in data.values()):
                        return True
        except Exception:
            pass
    # 短内容且无有效数据字段/数字 → 视为无信息量的指令或寒暄
    stripped = (content or '').strip()
    if 0 < len(stripped) < 30:
        has_data = any(h in stripped for h in _KB_DATA_FIELD_HINTS) or any(ch.isdigit() for ch in stripped)
        if not has_data:
            return True
    return False


def _auto_check_knowledge(emp_id, mem_id, value, tags=None):
    """保存记忆时自动检查是否应沉淀到知识库（决策直接 active；重复>=阈值）"""
    if not value:
        return None
    content = str(value)
    # 去重护栏：近期已完整入库的分析结论不再拆成碎片写入
    try:
        if _is_recent_saved_analysis(content):
            logger.info(f'  [KnowledgeBase] {emp_id} 跳过已完整保存的分析内容碎片: {content[:60]}...')
            return None
    except Exception:
        pass
    # 质量过滤：识别失败/数据为空等操作反馈不写入知识库
    if _is_low_quality_knowledge(content):
        logger.info(f'  [KnowledgeBase] {emp_id} 跳过低质量内容写入: {content[:60]}...')
        return None
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


# ═══ AI 分析结论自动入库 ═══
# 判定为“分析结论”的强标记词（命中即判定 True）：原结论四选一 + 常见分析收口语 + 结论/推荐类措辞
_ANALYSIS_CONCLUSION_MARKERS = (
    '建议合作', '建议测试', '建议观望', '不建议', '分析结论', '综合建议', '分析结果',
    '合作建议', '匹配建议', '投放建议', '推荐合作', '推荐测试', '首选', '次选',
    '优先推荐', '合作方案', '打法建议', '综合判断', '综上', '总结',
    '合作结论', '谨慎合作', '不建议合作', '性价比低', '性价比偏低', '转化存疑',
)
_ANALYSIS_MIN_LENGTH = 80          # 低于该长度不可能是完整分析
_ANALYSIS_DEDUP_THRESHOLD = 0.92   # 语义相似度 ≥ 该值视为重复，不再写入
# 组合信号判定：长文本（≥150字）含 ≥2 个业务指标词 + ≥1 个判断/推荐词 → 视为分析结论
_ANALYSIS_COMBO_MIN_LENGTH = 150
_ANALYSIS_METRIC_WORDS = (
    '粉丝', 'GMV', '转化率', '客单价', 'ROI', 'GPM', '完播率', '互动率', '类目',
    '价格带', '画像', '复购', '坑产', '佣金', '坑位费', '爆款', '带货', '涨粉',
    '粉丝量', '播放量', '点赞',
)
_ANALYSIS_JUDGMENT_WORDS = (
    '建议', '推荐', '适合', '不适合', '首选', '次选', '优先', '打法', '方案',
    '策略', '结论', '判断', '预计', '预估',
    '存疑', '偏低', '偏高', '匹配', '不匹配', '风险', '谨慎', '划算', '不值',
)

# 近期已完整入库的分析结论指纹缓存（TTL 去重护栏）：
# _maybe_auto_save_analysis 成功保存后记录 reply 前500字符的 md5；
# _auto_check_knowledge 命中则跳过，避免同一内容再被拆成碎片 pending 条目。
# 进程重启缓存清空可接受（极端 case 最多多几条碎片）。
_RECENT_SAVED_ANALYSIS = OrderedDict()
_RECENT_SAVED_ANALYSIS_LOCK = threading.Lock()
_RECENT_SAVED_ANALYSIS_TTL = 30 * 60   # 30 分钟
_RECENT_SAVED_ANALYSIS_MAX = 200       # 容量上限


def _analysis_fingerprint(text):
    """内容指纹：前 500 字符的 md5"""
    return hashlib.md5((text or '')[:500].encode('utf-8')).hexdigest()


def _remember_saved_analysis(text):
    """记录已完整入库的分析结论指纹（异常静默，不影响主流程）"""
    try:
        fp = _analysis_fingerprint(text)
        now = time.time()
        with _RECENT_SAVED_ANALYSIS_LOCK:
            for k in [k for k, t in _RECENT_SAVED_ANALYSIS.items() if now - t > _RECENT_SAVED_ANALYSIS_TTL]:
                _RECENT_SAVED_ANALYSIS.pop(k, None)
            _RECENT_SAVED_ANALYSIS[fp] = now
            _RECENT_SAVED_ANALYSIS.move_to_end(fp)
            while len(_RECENT_SAVED_ANALYSIS) > _RECENT_SAVED_ANALYSIS_MAX:
                _RECENT_SAVED_ANALYSIS.popitem(last=False)
    except Exception:
        pass


def _is_recent_saved_analysis(text):
    """判断内容是否命中近期已完整入库的分析结论（异常时返回 False，不误拦）"""
    try:
        fp = _analysis_fingerprint(text)
        now = time.time()
        with _RECENT_SAVED_ANALYSIS_LOCK:
            ts = _RECENT_SAVED_ANALYSIS.get(fp)
            if ts is None:
                return False
            if now - ts > _RECENT_SAVED_ANALYSIS_TTL:
                _RECENT_SAVED_ANALYSIS.pop(fp, None)
                return False
            return True
    except Exception:
        return False


def _auto_save_analysis_enabled():
    """分析结论自动入库开关：settings.json 中 auto_save_analysis，默认开启；设为 false 关闭"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
    except Exception:
        return True
    value = settings.get('auto_save_analysis', True)
    if isinstance(value, str):
        return value.strip().lower() not in ('0', 'false', 'no', 'off')
    return bool(value)


def _is_analysis_conclusion(text):
    """粗判 AI 回复是否为数据分析结论（长度 + 结论标记词 / 长文本组合信号）"""
    if not text or len(text) < _ANALYSIS_MIN_LENGTH:
        return False
    # 强标记词命中即判定
    if any(m in text for m in _ANALYSIS_CONCLUSION_MARKERS):
        return True
    # 组合信号：长文本 + ≥2 个业务指标词 + ≥1 个判断/推荐词
    if len(text) >= _ANALYSIS_COMBO_MIN_LENGTH:
        metric_hits = sum(1 for w in _ANALYSIS_METRIC_WORDS if w in text)
        if metric_hits >= 2 and any(w in text for w in _ANALYSIS_JUDGMENT_WORDS):
            return True
    return False


def _extract_analysis_title(content, user_text=''):
    """生成分析结论条目标题：业务前缀 + 结论句摘要（如“达人分析：建议合作，…”）"""
    prefix = '分析结论'
    for keywords, hint in _KB_CATEGORY_RULES:
        if any(kw in (content or '') for kw in keywords):
            prefix = hint + '分析'
            break
    # 1) 命中结论标记词的行：从标记词起截取（而非行首），避免长行截到无关开头
    for line in (content or '').split('\n'):
        for m in _ANALYSIS_CONCLUSION_MARKERS:
            idx = line.find(m)
            if idx >= 0:
                seg = line[idx:idx + 24].strip().rstrip('。，,')
                if seg:
                    return f'{prefix}：{seg}'
    # 2) 组合信号判定（无标记词）：找中文序号开头的行或加粗标题作摘要
    for line in (content or '').split('\n'):
        s = line.strip()
        if not s:
            continue
        if re.match(r'^[一二三四五六七八九十]+、', s):
            return f'{prefix}：{s[:24].rstrip("。，,")}'
        if '**' in s:
            seg = s.replace('*', '').strip()[:24].rstrip('。，,')
            if seg:
                return f'{prefix}：{seg}'
    # 3) 兜底：正文第一行非空内容前 24 字
    for line in (content or '').split('\n'):
        s = line.strip()
        if s:
            return f'{prefix}：{s[:24]}'
    base = (user_text or '').strip().split('\n')[0][:24]
    return f'{prefix}：{base}'


def _find_similar_kb_entry(conn, content, threshold=_ANALYSIS_DEDUP_THRESHOLD):
    """语义去重：与已有 kb_entry_chunks 的向量做余弦相似度，>= threshold 视为重复。
    返回 (entry_id, similarity)；无 embedding 配置/无任何向量/异常时返回 None（不去重）。"""
    emb_cfg = get_embedding_config()
    api_key = emb_cfg.get('apiKey')
    if not api_key:
        return None
    try:
        query_emb = get_embedding(content[:2000], api_key, emb_cfg.get('provider', 'openai'),
                                  model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))
    except Exception as e:
        logger.warning(f'  [AutoSaveAnalysis] 去重 embedding 获取失败: {e}')
        return None
    if not query_emb:
        return None
    try:
        import struct
        rows = conn.execute(
            'SELECT entry_id, embedding FROM kb_entry_chunks WHERE embedding IS NOT NULL AND embedding_model = ?',
            (emb_cfg.get('model') or '',)
        ).fetchall()
    except Exception:
        return None
    best_id, best_sim = None, 0.0
    for row in rows:
        try:
            emb = struct.unpack(f'{len(row["embedding"]) // 4}f', row['embedding'])
            sim = cosine_similarity(query_emb, emb)
            if sim > best_sim:
                best_id, best_sim = row['entry_id'], sim
        except Exception:
            continue
    if best_id and best_sim >= threshold:
        return best_id, best_sim
    return None


def _vectorize_kb_entry(kb_id, content):
    """为入库的分析结论条目分段并生成 embedding（走 settings.json 配置的 embedding API，如硅基流动）。
    仅当条目内容与本次结论一致（新插入/同 id 更新）时才重建分段，避免覆盖被合并旧条目的分段。"""
    emb_cfg = get_embedding_config()
    api_key = emb_cfg.get('apiKey')
    if not api_key:
        logger.info('  [AutoSaveAnalysis] 未配置 embedding API Key，跳过向量化')
        return
    ks._save_kb_chunks_without_embedding(kb_id, '', content, 500, 100)
    ks._vectorize_kb_chunks(kb_id, '', api_key, emb_cfg.get('provider', 'openai'),
                            emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))


def _extract_entity_from_analysis(reply, user_text=''):
    """从分析回复+用户提问中提取归属实体（达人/商品）。
    在 talents/products 表的 active 记录名称中做包含匹配，取最长匹配；
    无法确定归属时返回 ('', '')（事件仍写入，entity 留空）。"""
    text = (user_text or '') + '\n' + (reply or '')
    if not text.strip():
        return '', ''
    try:
        conn = _db_conn()
        try:
            talents = conn.execute("SELECT id, name FROM talents WHERE status='active'").fetchall()
            products = conn.execute("SELECT id, name FROM products WHERE status='active'").fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f'  [KnowledgeEvents] 实体提取查询失败: {e}')
        return '', ''
    best_type, best_id, best_len = '', '', 0
    for entity_type, rows in (('talent', talents), ('product', products)):
        for row in rows:
            name = (row['name'] or '').strip()
            if name and name in text and len(name) > best_len:
                best_type, best_id, best_len = entity_type, row['id'], len(name)
    return best_type, best_id


def _extract_predicted_match(text):
    """从分析原文中提取商品推荐预测结论（predicted_match）。

    匹配「适合带X / 推荐X / 匹配X / 建议合作X / 可以推X」句式，X 为商品名。
    命中后到 products 表模糊匹配 product_id（精确相等优先，其次互相包含，取最长）。
    返回 {'product_name', 'product_id', 'confidence', 'raw_quote'} 或 None。异常兜底返回 None。"""
    try:
        text = (text or '').strip()
        if not text:
            return None
        m = re.search(r'(适合带|推荐|匹配|建议合作|可以推)([^\n，。,；;！!？?]{2,30})', text)
        if not m:
            return None
        keyword = m.group(1)
        product_name = m.group(2).strip()
        if not product_name:
            return None
        # 取含关键词的整句作为 raw_quote
        raw_quote = ''
        for sent in re.split(r'[\n。！？!?.]', text):
            if keyword in sent:
                raw_quote = sent.strip()
                break
        if not raw_quote:
            raw_quote = m.group(0)
        # confidence：强烈推荐/非常适合 = 0.8；可以试试/可以推 = 0.4；其余 = 0.6
        if '强烈推荐' in text or '非常适合' in text:
            confidence = 0.8
        elif '可以试试' in raw_quote or keyword == '可以推':
            confidence = 0.4
        else:
            confidence = 0.6
        # 商品名模糊匹配 product_id
        product_id = ''
        conn = _db_conn()
        try:
            rows = conn.execute("SELECT id, name FROM products WHERE status='active'").fetchall()
        finally:
            conn.close()
        best_id, best_len = '', 0
        for row in rows:
            name = (row['name'] or '').strip()
            if not name:
                continue
            if name == product_name:
                best_id, best_len = row['id'], len(name)
                break
            if (name in product_name or product_name in name) and len(name) > best_len:
                best_id, best_len = row['id'], len(name)
        product_id = best_id
        return {'product_name': product_name, 'product_id': product_id,
                'confidence': confidence, 'raw_quote': raw_quote}
    except Exception as e:
        logger.warning(f'  [Deals] 预测结论提取失败: {e}')
        return None


def _ke_fts_upsert(conn, event_id, title, summary, conclusions_text):
    """同步 knowledge_events_fts（先删后插，兼容将来更新路径）。
    FTS 不可用或失败仅记日志，绝不影响事件保存。"""
    if not _KE_FTS_ENABLED:
        return
    try:
        conn.execute('DELETE FROM knowledge_events_fts WHERE event_id = ?', (event_id,))
        conn.execute(
            'INSERT INTO knowledge_events_fts (event_id, title, summary, conclusions_text) VALUES (?, ?, ?, ?)',
            (event_id, title or '', summary or '', conclusions_text or ''))
    except Exception as e:
        logger.warning(f'  [KnowledgeEvents] FTS 同步失败（不影响事件保存）: {e}')


def _ke_compute_importance(conn, entity_type, entity_id, content_full, conclusions_dict):
    """启发式计算事件重要度（1-10，不调 LLM）：
    基准 5；该实体首个分析事件且正文 >800 字 → 8；非首个事件且正文 <300 字（例行跟进短分析）→ 4；
    conclusions 含 predicted_match → +1；关联 deal（talent 按 talent_id / product 按 product_id）→ +1。
    上限 10、下限 1，异常落默认 5。"""
    try:
        score = 5.0
        content_len = len(content_full or '')
        is_first = True
        if entity_type and entity_id:
            row = conn.execute(
                'SELECT COUNT(*) AS c FROM knowledge_events WHERE entity_type = ? AND entity_id = ?',
                (entity_type, entity_id)).fetchone()
            is_first = not row or int(row['c'] or 0) == 0
        if is_first and content_len > 800:
            score = 8.0
        elif not is_first and content_len < 300:
            score = 4.0
        if conclusions_dict and conclusions_dict.get('predicted_match'):
            score += 1
        try:
            has_deal = False
            if entity_type == 'talent' and entity_id:
                has_deal = conn.execute(
                    'SELECT 1 FROM deals WHERE talent_id = ? LIMIT 1', (entity_id,)).fetchone() is not None
            elif entity_type == 'product' and entity_id:
                has_deal = conn.execute(
                    'SELECT 1 FROM deals WHERE product_id = ? LIMIT 1', (entity_id,)).fetchone() is not None
            if has_deal:
                score += 1
        except Exception:
            pass
        return max(1.0, min(10.0, score))
    except Exception as e:
        logger.warning(f'  [KnowledgeEvents] 重要度计算失败，落默认5: {e}')
        return 5.0


def _save_knowledge_event(reply, agent_id, title, user_text=''):
    """把分析结论原文写入 knowledge_events（实体档案时间线，原文不截断）。
    同时生成 embedding 存入 embedding 列（API 不可用时留 NULL）。异常兜底返回 None。"""
    try:
        entity_type, entity_id = _extract_entity_from_analysis(reply, user_text)
        conclusions = {}
        try:
            pm = _extract_predicted_match(reply)
            if pm:
                conclusions['predicted_match'] = pm
        except Exception as e:
            logger.warning(f'  [Deals] 预测结论提取失败，不影响事件入库: {e}')
        embedding_blob = None
        try:
            emb_cfg = get_embedding_config()
            api_key = emb_cfg.get('apiKey')
            if api_key:
                emb = get_embedding(reply[:2000], api_key, emb_cfg.get('provider', 'openai'),
                                    model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))
                if emb:
                    import struct
                    embedding_blob = struct.pack(f'{len(emb)}f', *emb)
        except Exception as e:
            logger.warning(f'  [KnowledgeEvents] embedding 生成失败: {e}')
        event_id = 'ke_' + uuid.uuid4().hex[:12]
        conn = _db_conn()
        try:
            importance = _ke_compute_importance(conn, entity_type, entity_id, reply, conclusions)
            conclusions_text = json.dumps(conclusions, ensure_ascii=False)
            conn.execute('''
                INSERT INTO knowledge_events
                (id, entity_type, entity_id, agent_id, event_type, title, content_full,
                 content_summary, conclusions, embedding, source_msg_id, user_query, created_at,
                 importance_score)
                VALUES (?, ?, ?, ?, 'analysis', ?, ?, '', ?, ?, '', ?, ?, ?)
            ''', (event_id, entity_type, entity_id, agent_id or '', title, reply,
                  conclusions_text,
                  embedding_blob, user_text or '', int(time.time() * 1000), importance))
            # 同步 FTS 全文索引（失败不影响事件保存）
            _ke_fts_upsert(conn, event_id, title, '', conclusions_text)
            conn.commit()
        finally:
            conn.close()
        logger.info(f'  [KnowledgeEvents] 分析事件已入库: {event_id} entity={entity_type}:{entity_id}')
        return event_id
    except Exception as e:
        logger.error(f'  [KnowledgeEvents] 写入失败: {e}')
        return None


def _maybe_auto_save_analysis(agent_id, reply, user_text='', tool_results=None):
    """AI 员工完成分析类回答后，自动把分析结论写入知识库（kb_entries，scope=global，emp_id 留空）。
    同时双写 knowledge_events（实体档案时间线）。
    OpenClaw 路径最终回复可能只是"建档成功"等操作短语，真正的分析在 tool_result 里：
    reply 不命中时，拼接 tool_results 的 content 再检测，命中则用拼接内容入库。
    开关：settings.json auto_save_analysis（默认开启）。全流程异常兜底，不影响聊天主流程。"""
    try:
        if not _auto_save_analysis_enabled():
            return
        analysis_text = reply or ''
        if not _is_analysis_conclusion(analysis_text) and tool_results:
            combined = '\n'.join(str(r) for r in tool_results if r)
            if _is_analysis_conclusion(combined):
                analysis_text = combined
        if not _is_analysis_conclusion(analysis_text):
            return
        if _is_low_quality_knowledge(analysis_text):
            return
        # 语义去重：与已有条目高度相似则不再写入
        conn = _db_conn()
        try:
            dup = _find_similar_kb_entry(conn, analysis_text)
        finally:
            conn.close()
        if dup:
            logger.info(f'  [AutoSaveAnalysis] {agent_id} 与已有条目 {dup[0]} 相似度 {dup[1]:.3f}，跳过写入')
            return
        title = _extract_analysis_title(analysis_text, user_text)
        kb_id = _upsert_knowledge_base({
            'title': title,
            'content': analysis_text,
            'source': 'auto_analysis',
            'status': 'active',
        })
        if not kb_id:
            return
        # 记录指纹：后续记忆管线收到同一内容时不再拆成碎片 pending 条目
        _remember_saved_analysis(analysis_text)
        # 仅当条目内容就是本次结论（新插入/同 id 更新）时才生成 embedding；
        # 若被合并进内容不同的旧条目，其分段/向量保持不变
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT content FROM kb_entries WHERE id = ?', (kb_id,)).fetchone()
            finally:
                conn.close()
            if row and (row['content'] or '') == analysis_text:
                _vectorize_kb_entry(kb_id, analysis_text)
        except Exception as e:
            logger.error(f'  [AutoSaveAnalysis] {agent_id} 向量化失败: {e}')
        # 双写实体档案：knowledge_events 时间线（含实体关联 + embedding）
        _save_knowledge_event(analysis_text, agent_id, title, user_text)
        logger.info(f'  [AutoSaveAnalysis] {agent_id} 分析结论已入库: {kb_id} title={title!r}')
    except Exception as e:
        logger.error(f'  [AutoSaveAnalysis] {agent_id} failed: {e}')


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
            'created_by': 'system',
            'original_price': price,
            'shipping_from': '',
            'no_shipping_areas': '',
            'sku_code': '',
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
    logger.info(f'  [Product] 已写入 COOLCHAP 示例数据 {len(seed_items)} 条商品 / {len(base_talents)} 条达人')


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
            logger.error(f'  [Knowledge] embedding 生成失败: {e}')

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
                logger.error(f'  [Knowledge] update embedding 失败: {e}')

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
        logger.info(f'  [Knowledge] 从 JSON 迁移 {migrated} 条记录到 SQLite')
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
        # 白名单模式：仅对白名单内的 Origin 回显，其余（含无 Origin 的非浏览器请求）回空串
        origin = self.headers.get('Origin', '')
        self.send_header('Access-Control-Allow-Origin', origin if origin in ALLOWED_ORIGINS else '')
        self.send_header('Vary', 'Origin')
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
    def _check_upload_size(self):
        """请求体大小限制：超限返回 413；抖音视频解析、图片识别接口放宽到 50MB"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (TypeError, ValueError):
            content_length = 0
        path = self._normalize_path(self.path)
        # /api/vision/describe 最多承载 9 张 1920px 图片的 base64，10MB 容易不够
        limit = MAX_VIDEO_UPLOAD_SIZE if path.startswith(('/api/douyin/', '/api/vision/')) else MAX_UPLOAD_SIZE
        if content_length > limit:
            self.send_error(413, 'File too large')
            return False
        return True

    def _read_body(self):
        if hasattr(self, 'cached_body'):
            return self.cached_body
        content_length = int(self.headers.get('Content-Length', 0))
        if content_length > 0:
            raw = self.rfile.read(content_length)
            try:
                self.cached_body = json.loads(raw)
                return self.cached_body
            except json.JSONDecodeError:
                self.cached_body = None
                return None
        self.cached_body = None
        return None

    # ─── 路由 ──────────────────────────────────────────
    def _normalize_path(self, path):
        """统一处理路径：去掉 query string 和末尾斜杠（根路径除外）"""
        path = path.split('?')[0]
        if path != '/' and path.endswith('/'):
            path = path[:-1]
        return path

    def _parse_query(self):
        """解析 query string，确保中文等 UTF-8 参数正确解码。

        兼容两种情况：
        1. 标准百分号编码（如 ?q=%E6%8B%96%E9%9E%8B）— parse_qs 默认按 UTF-8 解码；
        2. 客户端直接发送未编码的 UTF-8 中文 — BaseHTTPRequestHandler 按 latin-1
           解码 request line 产生乱码，这里将其还原为正确的 UTF-8 字符串。
        """
        qs = parse_qs(urlparse(self.path).query, encoding='utf-8', errors='replace')

        def _fix(s):
            try:
                return s.encode('latin-1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                return s

        return {_fix(k): [_fix(v) for v in vals] for k, vals in qs.items()}


    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        try:
            self._do_GET()
        except Exception as e:
            logger.error(f'  [ERROR] GET {self.path}: {e}')
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
        if path == '/api/employee-templates':
            self._handle_get_employee_templates()
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

        # Memory Pipeline API（L0-L3 分层记忆）
        if path == '/api/memory/atoms':
            self._handle_memory_pipeline_atoms()
            return
        if path == '/api/memory/stats':
            self._handle_memory_pipeline_stats()
            return
        if path == '/api/memory/pipeline':
            self._handle_memory_pipeline_status()
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

        # 用户飞书配置
        if path == '/api/user/feishu-config':
            self._handle_get_feishu_config()
            return

        # 通知 API
        if path == '/api/notifications':
            self._handle_get_notifications()
            return
        if path == '/api/notification-settings':
            self._handle_get_notification_settings()
            return

        # 账号设置 API
        if path == '/api/account':
            self._handle_get_account()
            return
        if path == '/api/settings/account':
            self._handle_get_account()
            return

        # 违禁词 API
        if path == '/api/forbidden-words':
            self._handle_get_forbidden_words()
            return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path == '/api/knowledge/entries':
            self._handle_get_kb_entries()
            return
        if path.startswith('/api/knowledge/entries/'):
            sub = path[len('/api/knowledge/entries/'):]
            if sub == 'reindex':
                self._send_json_error(405, 'Use POST /api/knowledge/entries/reindex')
                return
            if sub:
                self._handle_get_kb_entry_detail(sub)
                return
        if path == '/api/knowledge/categories':
            self._handle_get_kb_categories()
            return
        if path == '/api/knowledge/stats':
            self._handle_get_kb_stats()
            return

        # 知识事件（实体档案时间线）API
        if path == '/api/knowledge-events':
            self._handle_get_knowledge_events()
            return
        if path == '/api/knowledge-events/stats':
            self._handle_get_knowledge_events_stats()
            return
        if path == '/api/knowledge-events/search':
            self._handle_search_knowledge_events()
            return

        # 规律库 API（L3）
        if path == '/api/knowledge-patterns':
            self._handle_get_knowledge_patterns()
            return
        if path.startswith('/api/knowledge-patterns/'):
            sub = path[len('/api/knowledge-patterns/'):]
            if sub and '/' not in sub:
                self._handle_get_knowledge_pattern_detail(sub)
                return
        # 合作单 API
        if path == '/api/deals':
            self._handle_get_deals()
            return
        if path.startswith('/api/deals/'):
            sub = path[len('/api/deals/'):]
            if sub and '/' not in sub:
                self._handle_get_deal_detail(sub)
                return
        if path.startswith('/api/knowledge-events/'):
            sub = path[len('/api/knowledge-events/'):]
            if sub:
                self._handle_get_knowledge_event_detail(sub)
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
        if path == '/api/token-usage':
            self._handle_get_token_usage()
            return
        if path == '/api/token-usage/sync':
            self._handle_get_token_usage_sync()
            return

        # 积分制算力管控 API（summary 精确匹配需放在 usage 之前）
        if path == '/api/credits/balance':
            self._handle_get_credit_balance()
            return
        if path == '/api/credits/quotas':
            self._handle_get_credit_quotas()
            return
        if path == '/api/credits/usage/summary':
            self._handle_get_credit_usage_summary()
            return
        if path == '/api/credits/usage':
            self._handle_get_credit_usage()
            return
        if path == '/api/credits/check':
            self._handle_get_credit_check()
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
        if path == '/api/talents/injection-text':
            self._handle_get_talent_injection_text()
            return
        if path == '/api/talents/categories':
            self._handle_get_talent_categories()
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
        if path == '/api/products/sync-feishu':
            self._handle_sync_feishu_products()
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
                    if parts[1] == 'score':
                        self._handle_get_product_score(product_id)
                        return
                self._handle_get_product(rest)
                return

        # Tasks API
        if path == '/api/tasks':
            self._handle_get_tasks()
            return
        if path.startswith('/api/tasks/'):
            task_id = path[len('/api/tasks/'):]
            if task_id:
                self._handle_get_task(task_id)
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
            # /api/chat/:agentId/heavy-status
            if sub.endswith('/heavy-status'):
                agent_id = sub[:-len('/heavy-status')]
                if agent_id:
                    self._handle_get_heavy_status(agent_id)
                    return
            # /api/chat/:agentId
            agent_id = sub
            if agent_id:
                self._handle_get_chat(agent_id)
                return

        # Team Feed API: /api/team-feed/:agentId
        if path.startswith('/api/team-feed/'):
            agent_id = path[len('/api/team-feed/'):]
            print(f'  [TeamFeed] GET agent_id={agent_id!r}', flush=True)
            if agent_id:
                _feed = ''
                try:
                    _gid = _get_agent_group_id(agent_id)
                    print(f'  [TeamFeed] GET gid={_gid!r} for agent={agent_id!r}', flush=True)
                    if _gid:
                        _feed = _build_team_feed(_gid, agent_id) or ''
                        print(f'  [TeamFeed] GET feed_len={len(_feed)} for agent={agent_id!r}', flush=True)
                except Exception as e:
                    print(f'  [TeamFeed] GET 构建失败: {e}', flush=True)
                self._send_json(200, {'teamFeed': _feed})
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
        if not self._check_upload_size():
            return
        try:
            self._do_POST()
        except Exception as e:
            logger.error(f'  [ERROR] POST {self.path}: {e}')
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

        # Tool calls log
        if path == '/api/tool-calls/log':
            self._handle_post_tool_calls_log()
            return

        # 通知 API
        if path == '/api/notifications':
            self._handle_post_notification()
            return

        # 积分制算力管控：员工配额充值 / 管理员通用充值
        if path == '/api/credits/recharge':
            self._handle_credit_recharge_generic()
            return
        if path.startswith('/api/credits/quotas/') and path.endswith('/recharge'):
            agent_id = path[len('/api/credits/quotas/'):-len('/recharge')]
            if agent_id and '/' not in agent_id:
                self._handle_credit_recharge(agent_id)
                return

        # 违禁词 API（check 需先于 /api/forbidden-words 通用匹配）
        if path == '/api/forbidden-words/check':
            self._handle_forbidden_words_check()
            return
        if path == '/api/forbidden-words':
            self._handle_post_forbidden_words()
            return

        # Proxy (requires auth)
        if path == '/api/proxy':
            self._handle_proxy()
            return

        # Kimi API 代理（OpenClaw/飞书链路积分管控，不做JWT认证，用proxy_<agent_id> key识别身份）
        if path.startswith('/api/proxy/kimi'):
            self._handle_proxy_kimi()
            return

        # 抖音视频解析 (requires auth)
        if path == '/api/douyin/parse':
            self._handle_douyin_parse()
            return

        # 抖音视频语音转文字 (requires auth)
        if path == '/api/douyin/transcribe':
            self._handle_douyin_transcribe()
            return

        # 图片转文字描述（多模态降级：OpenClaw 链路只收纯文本）
        if path == '/api/vision/describe':
            self._handle_vision_describe()
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
        if path.startswith('/api/agents/'):
            sub = path[len('/api/agents/'):]
            parts = sub.split('/')
            if len(parts) == 2 and parts[1] == 'self-update-intent':
                self._handle_agent_self_update_intent(parts[0])
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
        if path == '/api/knowledge/entries/reindex':
            self._handle_post_kb_reindex()
            return
        if path == '/api/knowledge/search':
            self._handle_post_kb_search()
            return
        if path == '/api/knowledge/categories':
            self._handle_post_kb_categories()
            return

        # 规律库：触发归纳
        if path == '/api/knowledge-patterns/induce':
            self._handle_post_induce_knowledge_patterns()
            return

        # 合作单：创建
        if path == '/api/deals':
            self._handle_post_deal()
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
        if path == '/api/talents/sync-feishu':
            self._handle_sync_feishu_talents()
            return
        if path == '/api/user/feishu-config':
            self._handle_save_feishu_config()
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
            if len(parts) == 2 and parts[1] == 'promote':
                self._handle_promote_talent(parts[0])
                return

        # Product API
        if path == '/api/products':
            self._handle_post_product()
            return
        if path == '/api/products/search':
            self._handle_search_products()
            return
        if path == '/api/products/score':
            self._handle_score_product()
            return
        if path == '/api/products/batch-score':
            self._handle_batch_score_products()
            return
        if path == '/api/products/sync-feishu':
            self._handle_sync_feishu_products()
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

        # Tasks API
        if path == '/api/tasks':
            self._handle_post_task()
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
            logger.info(f'  [ChatPOST] 路由匹配: path={path} sub={sub}')
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
        if not self._check_upload_size():
            return
        try:
            self._do_PUT()
        except Exception as e:
            logger.error(f'  [ERROR] PUT {self.path}: {e}')
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

        # 规律库：状态流转
        if path.startswith('/api/knowledge-patterns/'):
            sub = path[len('/api/knowledge-patterns/'):]
            if sub and '/' not in sub:
                self._handle_put_knowledge_pattern(sub)
                return

        # 合作单：更新（含状态流转）
        if path.startswith('/api/deals/'):
            sub = path[len('/api/deals/'):]
            if sub and '/' not in sub:
                self._handle_put_deal(sub)
                return

        # 通知 API（read-all 需先于 /api/notifications/{id} 通配匹配）
        if path == '/api/notifications/read-all':
            self._handle_notifications_read_all()
            return
        if path == '/api/notification-settings':
            self._handle_put_notification_settings()
            return
        if path.startswith('/api/notifications/'):
            sub = path[len('/api/notifications/'):]
            if sub.endswith('/read'):
                notif_id = sub[:-len('/read')]
                if notif_id:
                    self._handle_notification_read(notif_id)
                    return

        # 账号设置 API
        if path == '/api/account':
            self._handle_put_account()
            return
        if path == '/api/settings/account':
            self._handle_put_account()
            return

        # 新版知识库 API（重构后，需放在旧版 /api/knowledge/ 通配路由之前）
        if path.startswith('/api/knowledge/entries/'):
            entry_id = path[len('/api/knowledge/entries/'):]
            if entry_id:
                self._handle_put_kb_entry(entry_id)
                return
        if path.startswith('/api/knowledge/categories/'):
            category_id = path[len('/api/knowledge/categories/'):]
            if category_id:
                self._handle_put_kb_category(category_id)
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

        # Tasks API
        if path.startswith('/api/tasks/'):
            task_id = path[len('/api/tasks/'):]
            if task_id:
                self._handle_put_task(task_id)
                return

        self._send_json_error(404, 'Not found')

    def do_DELETE(self):
        if not self._check_upload_size():
            return
        try:
            self._do_DELETE()
        except Exception as e:
            logger.error(f'  [ERROR] DELETE {self.path}: {e}')
            try:
                self._send_json(500, {'error': str(e)})
            except:
                pass

    def _do_DELETE(self):
        path = self._normalize_path(self.path)

        # 规律库：硬删除
        if path.startswith('/api/knowledge-patterns/'):
            sub = path[len('/api/knowledge-patterns/'):]
            if sub and '/' not in sub:
                self._handle_delete_knowledge_pattern(sub)
                return

        # 合作单：硬删除
        if path.startswith('/api/deals/'):
            sub = path[len('/api/deals/'):]
            if sub and '/' not in sub:
                self._handle_delete_deal(sub)
                return

        # 违禁词 API
        if path.startswith('/api/forbidden-words/'):
            word_id = path[len('/api/forbidden-words/'):]
            if word_id and '/' not in word_id:
                self._handle_delete_forbidden_word(word_id)
                return

        # 通知 API
        if path.startswith('/api/notifications/'):
            notif_id = path[len('/api/notifications/'):]
            if notif_id and '/' not in notif_id:
                self._handle_delete_notification(notif_id)
                return

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
        if path.startswith('/api/knowledge/categories/'):
            category_id = path[len('/api/knowledge/categories/'):]
            if category_id:
                self._handle_delete_kb_category(category_id)
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

        # Tasks API
        if path.startswith('/api/tasks/'):
            task_id = path[len('/api/tasks/'):]
            if task_id:
                self._handle_delete_task(task_id)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated or not auth.is_admin:
            self._send_auth_error('Permission denied', 403)
            return
        perms = _load_permissions()
        perms['modules'] = AVAILABLE_MODULES
        self._send_json(200, perms)

    def _handle_get_permission_modules(self):
        """GET /api/permissions/modules — 返回可用模块列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        self._send_json(200, {'modules': AVAILABLE_MODULES})

    def _handle_get_settings(self):
        """GET /api/settings — 读取全局设置（含 embedding 配置）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        admin_id = auth.user_info.get('userId', '')
        conn = _db_conn()
        conn.execute('UPDATE products SET created_by=? WHERE created_by=?', (admin_id, user_id))
        conn.execute('UPDATE talents SET created_by=? WHERE created_by=?', (admin_id, user_id))
        conn.commit()
        conn.close()
        agents = _load_agents(include_archived=True)  # 必须包含已归档员工，否则写回时会物理清除他们
        for a in agents:
            if a.get('createdBy') == user_id:
                a['createdBy'] = admin_id
                a['createdByName'] = auth.user_info.get('displayName', '管理员')
        _save_agents(agents)

        users = [u for u in users if u['id'] != user_id]
        _save_users(users)

        self._send_json(200, {'message': f'用户 {user["username"]} 已删除'})

    def _handle_get_user_subordinates(self, user_id):
        """GET /api/users/:id/subordinates — 获取下属列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [ERROR] _handle_get_groups: {e}')
            try:
                self._send_json(200, [])
            except:
                pass

    def _handle_get_group(self, group_id):
        """GET /api/groups/:id"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        # 删除群组聊天记录（先备份到 data/backups/deleted/ 再删除）
        chat_file = os.path.join(CHATS_DIR, f'group_{group_id}.json')
        if os.path.isfile(chat_file):
            try:
                _trash_file(chat_file)
            except OSError:
                pass

        self._send_json(200, {'message': f'群组 {group.get("name", "")} 已删除'})

    def _handle_global_search(self):
        """GET /api/search?q=xxx&scope=all|employees|groups|knowledge&limit=8"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.error(f'  [ERROR] global search knowledge: {e}')

        self._send_json(200, {'q': q, 'scope': scope, 'groups': result_groups})

    def _handle_add_group_member(self, group_id):
        """POST /api/groups/:id/members — 添加成员"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        # 权限检查同 GET /api/teams/:id
        self._handle_get_team(team_id)

    def _handle_create_team(self):
        """POST /api/teams — 创建小组（仅admin）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.info(f'  [ChatArchive] {chat_key} 归档 {archived_count} 条溢出消息到 L3')
            except Exception as e:
                logger.error(f'  [ChatArchive] {chat_key} 归档失败: {e}，回退到静默截断')
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
                logger.info(f'  [GroupMemory] group_{group_id} 群聊消息已保存到项目组公共记忆')
            except Exception as e:
                logger.error(f'  [GroupMemory] group_{group_id} 保存项目组公共记忆失败: {e}')

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
                    logger.info(f'  [GroupMemory] {sender_id} (AI) 群聊消息已保存到 daily 记忆')
                except Exception as e:
                    logger.error(f'  [GroupMemory] {sender_id} 保存群聊记忆失败: {e}')

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
                    logger.info(f'  [GroupMemory] {mid} 群聊上下文已保存到 daily 记忆')
                except Exception as e:
                    logger.error(f'  [GroupMemory] {mid} 保存群聊上下文失败: {e}')

        self._send_json(200, {'saved': True, 'id': msg['id'], 'archived': archived_count})

    # ═══════════════════════════════════════════════════
    # 项目组记忆 API
    # ═══════════════════════════════════════════════════

    def _handle_get_group_memory(self, group_id):
        """GET /api/groups/:groupId/memory — 获取项目组公共记忆"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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

    def _handle_get_employee_templates(self):
        """GET /api/employee-templates — 返回所有角色 systemPrompt 预设模板（name + systemPrompt），
        供前端创建员工时展示选择"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        templates = [{'name': role, 'systemPrompt': prompt}
                     for role, prompt in _ROLE_SYSTEM_PROMPT_TEMPLATES.items()]
        self._send_json(200, {'templates': templates})

    def _handle_get_agents(self):
        """GET /api/agents — 只返回当前用户创建的 agents（严格权限）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'employees'): return

        agents = _load_agents()
        uid = auth.user_info['userId']

        # 调试日志：打印 uid 和所有 agent 的 createdBy，排查过滤问题
        logger.info(f'  [DEBUG get_agents] uid={uid} role={auth.user_info.get("role")} is_admin={auth.is_admin} is_leader={auth.is_leader}')
        for a in agents:
            logger.info(f'  [DEBUG get_agents] agent id={a.get("id")} name={a.get("name")} createdBy={repr(a.get("createdBy"))}')

        if auth.is_admin:
            result = agents
        elif auth.is_leader:
            accessible_ids = _get_accessible_agent_ids(auth)
            result = [a for a in agents
                      if a.get('id') in accessible_ids or a.get('createdBy') == uid]
        else:
            # employee: 自己创建的 + visibility=all 公开的 + 同团队共享的
            accessible_ids = set(_get_accessible_agent_ids(auth) or [])
            result = [a for a in agents
                      if a.get('createdBy') == uid
                      or a.get('visibility') == 'all'
                      or a.get('id') in accessible_ids]

        # 过滤掉系统级管理员
        result = [a for a in result if a.get('id') != 'knowledge_admin']

        logger.info(f'  [DEBUG get_agents] 过滤后返回 {len(result)} 个 agents')
        for a in result:
            logger.info(f'  [DEBUG get_agents] -> result id={a.get("id")} name={a.get("name")} createdBy={repr(a.get("createdBy"))}')

        # 返回员工完整数据（包含 apiKey，前端需要它来显示和保存）
        safe_result = []
        name_map = _user_display_name_map()
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
                'createdByName': name_map.get(a.get('createdBy'), ''),
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [POST agent] ERROR: {e}')
            import traceback; traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_create_agent_inner(self):
        """POST /api/agents (implementation)"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'employees'): return

        body = self._read_body()
        if not body:
            self._send_json(400, {'error': '无效的请求体'})
            return

        # employee 配额检查（硬上限 3，与 agentQuota 取更严格者）
        if not auth.is_admin:
            auth.load_user_record()
            user = auth.user_record
            if user:
                agents = _load_agents()
                my_count = len([a for a in agents if a.get('createdBy') == auth.user_info['userId']])
                quota = min(int(user.get('agentQuota', 10) or 10), 3)
                if my_count >= quota:
                    self._send_json(403, {'error': '子账号最多创建 3 个 AI 员工'})
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
            'toolsDoc': body.get('toolsDoc', ''),
            'department': body.get('department', ''),
            'customEndpoint': body.get('customEndpoint', ''),
        }

        agents = _load_agents(include_archived=True)
        # 检查 ID 重复
        for a in agents:
            if a.get('id') == new_agent['id']:
                new_agent['id'] = 'emp_' + uuid.uuid4().hex[:6]
                break
        # 按 role 自动套用预设 systemPrompt 模板作为默认值；用户传入的自定义 systemPrompt 优先
        if not (new_agent['systemPrompt'] or '').strip():
            template = _ROLE_SYSTEM_PROMPT_TEMPLATES.get(new_agent['role'])
            if template:
                new_agent['systemPrompt'] = template
        # 商务角色：创建时自动在 systemPrompt 末尾追加达人数据源强制约束，防止编造达人数据
        if new_agent['role'] == '商务' and '【数据源强制约束】' not in (new_agent['systemPrompt'] or ''):
            new_agent['systemPrompt'] = (new_agent['systemPrompt'] or '').rstrip() + _BUSINESS_DATA_CONSTRAINT
        # 工具使用铁律：所有新员工必须包含（角色模板/自定义/空白一视同仁），防止口头"已录入"不实际调用工具
        if '【工具使用铁律】' not in (new_agent['systemPrompt'] or ''):
            new_agent['systemPrompt'] = (new_agent['systemPrompt'] or '').rstrip() + _ANTI_FABRICATION_RULES
        # 默认工具配置：X-Agent-Id 硬编码进 TOOLS.md，不依赖 LLM 自觉携带
        if not (new_agent.get('toolsDoc') or '').strip():
            new_agent['toolsDoc'] = _build_agent_tools_doc(new_agent['id'])
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
            auth = _authenticate(self.headers, self.client_address[0], self)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return
            if not self._require_module_permission(auth, 'employees'): return

            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return

            body_keys = list(body.keys())
            logger.info(f'  [PUT agent] id={agent_id} body_keys={body_keys}')

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

            logger.info(f'  [PUT agent] id={agent_id} 实际保存字段={saved_keys}')

            # 根因排查：保存前打印 apiKey 详情
            pre_save_api_key = agent.get('apiKey', '')
            if pre_save_api_key:
                logger.info(f'  [PUT agent] id={agent_id} 保存前 apiKey len={len(pre_save_api_key)} preview={repr(pre_save_api_key[:50])}')

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
                    logger.info(f'  [PUT agent] id={agent_id} 保存后 apiKey 发生变化! pre_len={len(pre_save_api_key)} post_len={len(post_api_key)} post_preview={repr(post_api_key[:50])}')
                    import traceback
                    traceback.print_stack()
                elif post_api_key:
                    logger.info(f'  [PUT agent] id={agent_id} 保存后 apiKey 一致 len={len(post_api_key)}')

            # 自动同步 API Key 到 OpenClaw（有变动时）
            new_api_key = agent.get('apiKey', '')
            new_provider = agent.get('aiProvider', '') or agent.get('apiProvider', '')
            logger.info(f'  [PUT agent] id={agent_id} 同步检测: old_key={bool(old_api_key)} new_key={bool(new_api_key)} old_prov={old_provider} new_prov={new_provider}')
            if new_api_key and new_provider:
                if new_api_key != old_api_key or new_provider != old_provider:
                    _sync_agent_api_key_to_openclaw(agent)
                else:
                    logger.info(f'  [PUT agent] id={agent_id} API Key 未变动，跳过同步')
            else:
                logger.info(f'  [PUT agent] id={agent_id} 缺少 apiKey 或 provider，跳过同步')

            logger.info(f'  [PUT agent] saved ok, sending response')
            self._send_json(200, agent)
        except Exception as e:
            logger.error(f'  [PUT agent] ERROR: {e}')
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_agent_self_update(self, agent_id):
        """PUT /api/agents/:id/self-update - AI 员工自修改配置"""
        try:
            auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [PUT agent self-update] ERROR: {e}')
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_agent_self_update_intent(self, agent_id):
        """POST /api/agents/:id/self-update-intent - 检测自然语言自修改意图并直接应用"""
        try:
            auth = _authenticate(self.headers, self.client_address[0], self)
            if not auth.is_authenticated:
                self._send_auth_error(auth.error, auth.status)
                return
            if not self._require_module_permission(auth, 'employees'):
                return

            _, err, status = self._check_agent_access(auth, agent_id)
            if err:
                self._send_json(status, {'error': err})
                return

            body = self._read_body()
            if not body:
                self._send_json(400, {'error': '无效的请求体'})
                return

            content = body.get('content', '')
            intent_updates = _detect_self_update_intent(content)
            if not intent_updates:
                self._send_json(200, {'matched': False})
                return

            ok, su_msg, _ = _apply_agent_self_update(agent_id, intent_updates, source=f'chat:{auth.user_id}')
            if not ok:
                self._send_json(200, {'matched': False, 'error': su_msg})
                return

            field_name, new_value = intent_updates[0]
            confirmation = f'（系统已根据你的指令更新了你的{field_name}为{new_value}，请在回复中确认已更新）'
            logger.info(f'  [AgentSelfUpdateIntent] agent={agent_id} field={field_name} value={new_value}')
            self._send_json(200, {
                'matched': True,
                'field': field_name,
                'value': new_value,
                'confirmation': confirmation
            })
        except Exception as e:
            logger.error(f'  [AgentSelfUpdateIntent] ERROR: {e}')
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': str(e)})

    def _handle_delete_agent(self, agent_id):
        """DELETE /api/agents/:id"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        # 清理聊天记录（先备份到 data/backups/deleted/ 再删除）
        chat_file = os.path.join(CHATS_DIR, f'{agent_id}.json')
        if os.path.isfile(chat_file):
            try:
                _trash_file(chat_file)
            except OSError as e:
                logger.error(f'  [Cleanup] 删除聊天文件失败 {chat_file}: {e}')

        # 清理聊天摘要（先备份到 data/backups/deleted/ 再删除）
        summary_file = os.path.join(CHATS_DIR, f'{agent_id}_summary.json')
        if os.path.isfile(summary_file):
            try:
                _trash_file(summary_file)
            except OSError as e:
                logger.error(f'  [Cleanup] 删除摘要文件失败 {summary_file}: {e}')

        # 清理 v3 记忆数据目录
        try:
            import shutil
            mem_dir = os.path.join(ms3.MEMORY_V3_DIR, agent_id)
            if os.path.isdir(mem_dir):
                shutil.rmtree(mem_dir)
        except Exception as e:
            logger.error(f'  [Cleanup] 清理记忆目录失败 {agent_id}: {e}')

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
                            logger.info(f'  [Cleanup] 从 {other_id}/{mem_file} 移除该 AI 员工的群聊上下文')
                    except Exception as e:
                        logger.error(f'  [Cleanup] 清理 {other_id} 记忆失败: {e}')
        except Exception as e:
            logger.error(f'  [Cleanup] 扫描其他 AI 记忆失败: {e}')

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
                            logger.info(f'  [Cleanup] 从 {os.path.basename(group_mem_file)} 移除该 AI 员工的项目组记忆')
                    except Exception as e:
                        logger.error(f'  [Cleanup] 清理项目组记忆失败 {group_mem_file}: {e}')
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
                            logger.info(f'  [Cleanup] 从 {os.path.basename(group_arc_file)} 移除该 AI 员工的项目组归档记忆')
                    except Exception as e:
                        logger.error(f'  [Cleanup] 清理项目组归档记忆失败 {group_arc_file}: {e}')
        except Exception as e:
            logger.error(f'  [Cleanup] 扫描项目组记忆失败: {e}')

        # 清理归档文件
        archive_file = os.path.join(ARCHIVE_DIR, f'{agent_id}.json')
        if os.path.isfile(archive_file):
            try:
                os.remove(archive_file)
            except OSError as e:
                logger.error(f'  [Cleanup] 删除归档文件失败 {archive_file}: {e}')

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
                        logger.info(f'  [Cleanup] 从 {os.path.basename(group_arc_file)} 归档移除该 AI 消息')
                except Exception as e:
                    logger.error(f'  [Cleanup] 清理群聊归档失败 {group_arc_file}: {e}')
        except Exception as e:
            logger.error(f'  [Cleanup] 扫描群聊归档失败: {e}')

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
                        logger.info(f'  [Cleanup] 从 {os.path.basename(group_chat_file)} 移除 {original_len - len(filtered)} 条该 AI 消息')
                except Exception as e:
                    logger.error(f'  [Cleanup] 清理群聊文件失败 {group_chat_file}: {e}')
        except Exception as e:
            logger.error(f'  [Cleanup] 扫描群聊文件失败: {e}')

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
                logger.info(f'  [Cleanup] 已硬删除 {agent_id} 创建的 {len(talent_ids)} 个达人及关联跟进/匹配记录')

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
                logger.info(f'  [Cleanup] 已硬删除 {agent_id} 创建的 {len(product_ids)} 个商品及关联匹配记录')

            conn.commit()

            # 7.3) 更新受影响品牌的统计
            try:
                for brand_id in affected_brand_ids:
                    _update_brand_product_stats(conn, brand_id)
                conn.commit()
            except Exception as e:
                logger.error(f'  [Cleanup] 更新品牌统计失败: {e}')

            conn.close()
            logger.info(f'  [Cleanup] 已清理 {agent_id} 的数据库级联数据')
        except Exception as e:
            logger.error(f'  [Cleanup] 数据库级联清理失败 {agent_id}: {e}')

        # 清理 RAG 内存缓存中该员工的查询结果
        try:
            rag_cache = getattr(ks, '_rag_cache', None)
            if rag_cache is not None:
                keys_to_remove = [k for k in rag_cache.keys() if k.startswith(f"rag:{agent_id}:")]
                for k in keys_to_remove:
                    rag_cache.pop(k, None)
                if keys_to_remove:
                    logger.info(f'  [Cleanup] 已清理 {agent_id} 的 RAG 内存缓存 {len(keys_to_remove)} 条')
        except Exception as e:
            logger.error(f'  [Cleanup] RAG 缓存清理失败 {agent_id}: {e}')

    # ═══════════════════════════════════════════════════
    # Dreaming API
    # ═══════════════════════════════════════════════════

    def _handle_get_dreaming(self):
        """GET /api/openclaw/dreaming?agentId=xxx"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [GET dreaming] ERROR: {e}')
            self._send_json(500, {'error': str(e)})

    def _handle_post_dreaming(self):
        """POST /api/openclaw/dreaming body:{agentId, enabled}"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            agents = _load_agents(include_archived=True)  # 必须包含已归档员工，否则写回时会物理清除他们
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
            logger.error(f'  [POST dreaming] ERROR: {e}')
            self._send_json(500, {'error': str(e)})

    # ═══════════════════════════════════════════════════
    # 聊天 API
    # ═══════════════════════════════════════════════════

    def _handle_write_agent_docs(self):
        """POST /api/openclaw/write-agent-docs - Write SOUL.md/IDENTITY.md/AGENTS.md to agent workspace"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            else:
                # 注册时未提供 toolsDoc：自动写入默认工具配置（X-Agent-Id 硬编码），
                # 已有定制化 TOOLS.md 不覆盖
                tools_path = os.path.join(workspace_path, 'TOOLS.md')
                if not os.path.exists(tools_path):
                    with open(tools_path, 'w', encoding='utf-8') as f:
                        f.write(_build_agent_tools_doc(agent_id))
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.info(f'  [MemorySeed] 未找到种子文件: {seed_path}')
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
            logger.info(f'  [MemorySeed] {agent_id} 已加载 {len(initial_memories)} 条初始记忆 ({seed_name})')
        except Exception as e:
            logger.error(f'  [MemorySeed] 加载失败: {e}')


    # ─── 记忆 API ─────────────────────────────────────────

    # 记忆过期配置：日常记录30天后过期，核心记忆不过期
    MEMORY_DAILY_TTL_DAYS = 30

    # ═══════════════════════════════════════════════════
    # 记忆系统 v2 API（三层大脑架构）
    # ═══════════════════════════════════════════════════

    def _handle_get_memory(self, emp_id):
        """GET /api/memory/{empId}[?type=&key=&tag=&keyword=&limit=&offset=] — 查询记忆列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.error(f'  [MemoryAPI] 加载知识库失败: {e}')
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.error(f'  [MemoryV3] auto-generate consolidatedValue failed: {e}')
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

        logger.info(f'  [MemoryV3] {emp_id} 归纳合并 {len(archived_ids)} 条记忆 → {new_mem["id"]}')
        self._send_json(200, {
            'success': True,
            'data': {
                'newMemory': mapped,
                'archivedIds': archived_ids
            }
        })

    def _handle_search_memory(self):
        """GET /api/memory/search — 全局搜索记忆（跨员工、跨池）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        logger.info(f'  [MemoryV3] {emp_id} 保存 {pool} 记忆: {value[:50]}...')

        # FIXME: 大脑知识中枢新增：把记忆加入清洗窗口
        try:
            _brain_scheduler.request_clean(emp_id, memory.get('id'))
        except Exception as e:
            logger.error(f'  [BrainScheduler] request_clean failed: {e}')

        # FIXME: 三级知识库自动沉淀 + 二级归纳自动触发（数量/决策）
        auto_triggers = []
        if key in ('auto', 'auto_extract'):
            try:
                # 三级沉淀：决策关键词/重复提及自动入 knowledge_base
                _auto_check_knowledge(emp_id, memory.get('id'), memory.get('value'), memory.get('tags'))
                # 二级归纳：数量触发 / 决策触发 -> 创建 pending 记录，由前端 AI 生成正式内容
                auto_triggers = _auto_summarize_triggers(emp_id, memory)
            except Exception as e:
                logger.error(f'  [MemoryV3] {emp_id} 自动沉淀/归纳触发失败: {e}')

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
                logger.error(f'  [MemoryV3] {emp_id} 自动归纳触发失败: {e}')

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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not _check_agent_exists(emp_id):
            self._send_json(404, {'error': 'Agent not found'})
            return

        removed = ms3.delete_memory(emp_id, memory_id)
        if removed:
            logger.info(f'  [MemoryV3] {emp_id} 删除记忆: {memory_id}')

        self._send_json(200, {
            'success': True,
            'data': {'deleted': removed, 'id': memory_id}
        })

    def _handle_update_memory(self, emp_id, memory_id):
        """PUT /api/memory/{empId}/{memoryId} — 修改单条记忆（支持跨池移动）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        logger.info(f'  [MemoryV3] {emp_id} 升级为核心记忆: {mem.get("value", "")[:50]}...')
        self._send_json(200, result)

    def _handle_restore_memory(self, emp_id, memory_id):
        """POST /api/memory/{empId}/{memoryId}/restore — 从归档恢复为日常记忆"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        logger.info(f'  [MemoryV3] {emp_id} 恢复归档记忆到 daily: {mem.get("value", "")[:50]}...')
        self._send_json(200, {
            'success': True,
            'data': mapped
        })

    def _handle_archive_memory_cleanup(self, emp_id):
        """POST /api/memory/{empId}/archive — 手动触发归档过期日常记录"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [CoreCandidate] confirm failed: {e}')
            self._send_json_error(500, f'Confirm failed: {str(e)}')
            return
        self._send_json(200, {
            'success': True,
            'candidate': cand,
            'memory': new_mem
        })

    def _handle_dismiss_core_candidate(self, emp_id, cand_id):
        """POST /api/memory/{empId}/core-candidates/{candId}/dismiss"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [InductKnowledge] manual failed: {e}')
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [ArchiveInducted] failed: {e}')
            self._send_json_error(500, f'Archive failed: {str(e)}')
            return
        self._send_json(200, {
            'success': True,
            'archivedIds': archived_ids,
            'empId': emp_id
        })

    def _handle_get_merge_history(self, emp_id):
        """GET /api/memory/{empId}/merge-history — 获取去重合并记录"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [DetectConflicts] failed: {e}')
            self._send_json_error(500, f'Detect failed: {str(e)}')

    def _handle_resolve_conflict(self, emp_id, mem_id):
        """POST /api/memory/{empId}/{memId}/resolve-conflict — 解决冲突"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [ResolveConflict] failed: {e}')
            self._send_json_error(500, f'Resolve failed: {str(e)}')

    # FIXME: 大脑知识中枢 API 处理器
    def _handle_get_brain_status(self):
        """GET /api/brain/status — 返回大脑处理状态"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            stats = _brain_scheduler.get_stats()
            self._send_json(200, {'success': True, **stats})
        except Exception as e:
            logger.error(f'  [BrainAPI] status failed: {e}')
            self._send_json_error(500, f'Status failed: {str(e)}')

    def _handle_brain_trigger_manual(self):
        """POST /api/brain/trigger-manual — 手动触发全量处理"""
        # FIXME: 修复大脑手动触发接口鉴权：确保和其他 /api/ 接口使用相同的登录态校验
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [BrainAPI] trigger failed: {e}')
            self._send_json_error(500, f'Trigger failed: {str(e)}')

    def _handle_get_brain_topics(self):
        """GET /api/brain/topics?empId=xxx — 获取员工的主题列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [BrainAPI] topics failed: {e}')
            self._send_json_error(500, f'Topics failed: {str(e)}')

    def _handle_get_brain_knowledge(self):
        """GET /api/brain/knowledge?topicId=xxx — 获取主题下的知识"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [BrainAPI] knowledge failed: {e}')
            self._send_json_error(500, f'Knowledge failed: {str(e)}')

    def _handle_brain_knowledge_feedback(self, knowledge_id):
        """POST /api/brain/knowledge/{kid}/feedback — 准确/有误反馈"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        accurate = body.get('accurate', True)
        try:
            ok = _brain_scheduler._know_svc.feedback_knowledge(knowledge_id, accurate=accurate)
            self._send_json(200, {'success': ok})
        except Exception as e:
            logger.error(f'  [BrainAPI] feedback failed: {e}')
            self._send_json_error(500, f'Feedback failed: {str(e)}')

    # FIXME: 记忆三级沉淀 API：二级归纳（daily/project） + 三级知识库查询/标记
    def _handle_get_daily_summary(self, emp_id):
        """GET /api/memory/{empId}/daily-summary?date=YYYY-MM-DD"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [SummaryTrigger] save failed: {e}')
            self._send_json_error(500, f'Save summary failed: {str(e)}')
            return
        self._send_json(200, {'success': True, 'empId': emp_id, 'summaryId': sid})

    def _handle_get_agent_knowledge_base(self, emp_id):
        """GET /api/memory/{empId}/knowledge — 查询该员工三级知识库"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        # 质量过滤：识别失败/数据为空等操作反馈不写入知识库
        if _is_low_quality_knowledge(content, title):
            logger.info(f'  [KnowledgeBase] {emp_id} 手动标记跳过低质量内容: {str(content)[:60]}...')
            self._send_json(200, {'success': True, 'empId': emp_id, 'knowledgeId': None, 'filtered': True})
            return
        try:
            kb_id = _upsert_knowledge_base({
                'empId': emp_id,
                'title': title,
                'content': content,
                'source': body.get('source', 'manual'),
                'tags': body.get('tags', []),
                'relatedMemIds': [mem_id] if mem_id else [],
                'categoryId': body.get('categoryId') or body.get('category_id'),
                'projectId': body.get('projectId') or body.get('project_id'),
                'status': 'active'
            })
        except Exception as e:
            logger.error(f'  [KnowledgeBase] manual mark failed: {e}')
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            emp_id=target_emp_id,
            emp_ids=_get_user_emp_ids(auth.user_id)
        )
        self._send_json(200, result)

    def _handle_get_knowledge_detail(self, kid):
        """GET /api/knowledge/<id> — 单条知识详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        doc = ks.knowledge_get_by_id(kid)
        if not doc:
            self._send_json_error(404, 'Knowledge not found')
            return
        # 权限检查
        if not ks.can_read_knowledge(doc, auth.user_id, is_admin=auth.is_admin, user_team_ids=auth.team_ids, user_group_ids=auth.group_ids, emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        if not _can_access_knowledge_category(auth, doc.get('category', '')):
            self._send_auth_error('No permission for this knowledge category', 403)
            return
        self._send_json(200, doc)

    def _handle_get_knowledge_search(self):
        """GET /api/knowledge/search?q=xxx&limit=3 — 语义检索（带三层隔离）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [KnowledgeSearch] failed: {e}')
            self._send_json_error(500, f'Search failed: {str(e)}')

    def _handle_post_knowledge(self):
        """POST /api/knowledge — 新增全局公共知识（自动分段+向量化）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        # 自动修正：传了 group_ids 但 scope=team 时，按 group 处理
        if group_ids and scope == 'team':
            scope = 'group'
        if scope == 'group' and not group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        # 兼容旧前端传入 empId
        emp_id = body.get('empId') or ''
        if scope == 'personal' and not emp_id:
            emp_id = auth.user_id
        # 兼容 Agent 直连：从 X-Agent-Id 请求头自动填充 emp_id
        if not emp_id:
            agent_id_header = self.headers.get('X-Agent-Id', '').strip()
            if agent_id_header:
                emp_id = agent_id_header
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
            logger.error(f'  [Knowledge] create failed: {e}')
            self._send_json_error(500, f'Create failed: {str(e)}')

    def _handle_put_knowledge(self, doc_id):
        """PUT /api/knowledge/{docId} — 更新全局公共知识（自动重新分段+向量化）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [Knowledge] update failed: {e}')
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_knowledge(self, doc_id):
        """DELETE /api/knowledge/{docId} — 删除知识"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [KnowledgeRollback] failed: {e}')
            self._send_json_error(500, f'Rollback failed: {str(e)}')

    def _handle_knowledge_move(self, doc_id):
        """POST /api/knowledge/{docId}/move — 移动知识到指定 scope/team"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [KnowledgeMove] failed: {e}')
            self._send_json_error(500, f'Move failed: {str(e)}')

    # ═══════════════════════════════════════════════════
    # 新版知识库 API（重构后）
    # ═══════════════════════════════════════════════════

    def _handle_get_kb_entries(self):
        """GET /api/knowledge/entries — 新版知识库列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        offset = max(0, int(qs.get('offset', [0])[0]))
        limit = max(1, min(100, int(qs.get('limit', [20])[0])))
        category = qs.get('category', [''])[0] or None
        category_id = qs.get('categoryId', [''])[0] or None
        project_id = qs.get('projectId', [''])[0] or None
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
                offset=offset, limit=limit, category=category, category_id=category_id,
                project_id=project_id, keyword=keyword,
                allowed_categories=allowed_cats,
                scope=scope, team_id=team_id, user_id=auth.user_id,
                is_admin=auth.is_admin, user_team_ids=auth.team_ids,
                user_group_ids=effective_group_ids,
                created_by=created_by,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            self._send_json(200, result)
        except Exception as e:
            logger.error(f'  [KBEntries] list failed: {e}')
            self._send_json_error(500, f'List failed: {str(e)}')

    def _handle_get_kb_entry_detail(self, entry_id):
        """GET /api/knowledge/entries/<id> — 新版知识详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        # 自动修正：传了 group_ids 但 scope=team 时，按 group 处理
        if group_ids and scope == 'team':
            scope = 'group'
        if scope == 'group' and not group_ids:
            self._send_json_error(400, 'Missing group_ids for scope=group')
            return
        emp_id = body.get('empId') or ''
        if scope == 'personal' and not emp_id:
            emp_id = auth.user_id
        # 兼容 Agent 直连：从 X-Agent-Id 请求头自动填充 emp_id
        if not emp_id:
            agent_id_header = self.headers.get('X-Agent-Id', '').strip()
            if agent_id_header:
                emp_id = agent_id_header
        if not ks.can_create_knowledge(scope, auth.user_id, is_admin=auth.is_admin,
                                       team_id=team_id, user_team_ids=auth.team_ids,
                                       managed_team_ids=auth.managed_team_ids,
                                       group_ids=group_ids, user_group_ids=auth.group_ids,
                                       managed_group_ids=auth.managed_group_ids,
                                       emp_id=emp_id, emp_ids=_get_user_emp_ids(auth.user_id)):
            self._send_auth_error('Permission denied', 403)
            return
        category = body.get('category', '')
        category_id = body.get('categoryId') or body.get('category_id')
        project_id = body.get('projectId') or body.get('project_id')
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
                category_id=category_id,
                project_id=project_id,
                created_by=auth.user_id,
                scope=scope,
                team_id=team_id,
                group_ids=group_ids,
                emp_id=emp_id,
                agent_config=agent_config,
            )
            self._send_json(200, doc)
        except Exception as e:
            logger.error(f'  [KBEntry] create failed: {e}')
            self._send_json_error(500, f'Create failed: {str(e)}')

    def _handle_post_kb_reindex(self):
        """POST /api/knowledge/entries/reindex — 批量重建 pending/未向量化条目的分段与向量（管理员）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        if not auth.is_admin:
            self._send_auth_error('Admin only', 403)
            return
        try:
            stats = ks.kb_entries_reindex_pending()
            self._send_json(200, stats)
        except Exception as e:
            logger.error(f'  [KBEntry] reindex failed: {e}')
            self._send_json_error(500, f'Reindex failed: {str(e)}')

    def _handle_put_kb_entry(self, entry_id):
        """PUT /api/knowledge/entries/<id> — 更新新版知识"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        new_category_id = body.get('categoryId') or body.get('category_id')
        new_project_id = body.get('projectId') or body.get('project_id')
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
                category_id=new_category_id,
                project_id=new_project_id,
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
            logger.error(f'  [KBEntry] update failed: {e}')
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_kb_entry(self, entry_id):
        """DELETE /api/knowledge/entries/<id> — 删除新版知识"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [KBEntry] delete failed: {e}')
            self._send_json_error(500, f'Delete failed: {str(e)}')

    def _handle_get_kb_categories(self):
        """GET /api/knowledge/categories — 分类树"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        project_id = qs.get('projectId', [''])[0] or ''
        try:
            tree = ks.kb_category_tree(project_id=project_id)
            self._send_json(200, {'categories': tree, 'projectId': project_id})
        except Exception as e:
            logger.error(f'  [KBCategories] failed: {e}')
            self._send_json_error(500, f'Categories failed: {str(e)}')

    def _handle_post_kb_categories(self):
        """POST /api/knowledge/categories — 创建分类"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body() or {}
        name = body.get('name') or body.get('category')
        if not name or not str(name).strip():
            self._send_json_error(400, 'Missing category name')
            return
        try:
            cat = ks.kb_category_create(
                name=str(name).strip(),
                parent_id=body.get('parentId') or body.get('parent_id'),
                project_id=body.get('projectId') or body.get('project_id')
            )
            self._send_json(200, cat)
        except ValueError as e:
            self._send_json_error(400, str(e))
        except Exception as e:
            logger.error(f'  [KBCategories] create failed: {e}')
            self._send_json_error(500, f'Create category failed: {str(e)}')

    def _handle_put_kb_category(self, category_id):
        """PUT /api/knowledge/categories/<id> — 更新分类"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body() or {}
        try:
            cat = ks.kb_category_update(
                category_id,
                name=body.get('name'),
                sort_order=body.get('sortOrder') if body.get('sortOrder') is not None else body.get('sort_order')
            )
            if not cat:
                self._send_json_error(404, 'Category not found')
                return
            self._send_json(200, cat)
        except ValueError as e:
            self._send_json_error(400, str(e))
        except Exception as e:
            logger.error(f'  [KBCategories] update failed: {e}')
            self._send_json_error(500, f'Update category failed: {str(e)}')

    def _handle_delete_kb_category(self, category_id):
        """DELETE /api/knowledge/categories/<id> — 删除分类"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        try:
            ok = ks.kb_category_delete(category_id)
            self._send_json(200, {'success': ok})
        except Exception as e:
            logger.error(f'  [KBCategories] delete failed: {e}')
            self._send_json_error(500, f'Delete category failed: {str(e)}')

    def _handle_get_kb_stats(self):
        """GET /api/knowledge/stats — 统计面板"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        scope = qs.get('scope', [''])[0] or None
        project_id = qs.get('projectId', [''])[0] or None
        allowed_cats = _allowed_knowledge_categories(auth)
        try:
            stats = ks.kb_entry_stats(
                allowed_categories=allowed_cats,
                scope=scope, project_id=project_id, user_id=auth.user_id,
                is_admin=auth.is_admin, user_team_ids=auth.team_ids,
                user_group_ids=auth.group_ids,
                emp_ids=_get_user_emp_ids(auth.user_id)
            )
            self._send_json(200, {'stats': stats})
        except Exception as e:
            logger.error(f'  [KBStats] failed: {e}')
            self._send_json_error(500, f'Stats failed: {str(e)}')

    def _handle_get_knowledge_events(self):
        """GET /api/knowledge-events?entity_type=&entity_id= — 实体分析事件列表（不含 content_full）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        qs = parse_qs(urlparse(self.path).query)
        entity_type = qs.get('entity_type', [''])[0].strip()
        entity_id = qs.get('entity_id', [''])[0].strip()
        try:
            limit = max(1, min(200, int(qs.get('limit', [50])[0] or 50)))
        except (TypeError, ValueError):
            limit = 50
        try:
            sql = ('SELECT id, entity_type, entity_id, agent_id, event_type, title, '
                   'content_summary, conclusions, user_query, created_at FROM knowledge_events')
            conds, params = [], []
            if entity_type:
                conds.append('entity_type = ?')
                params.append(entity_type)
            if entity_id:
                conds.append('entity_id = ?')
                params.append(entity_id)
            if conds:
                sql += ' WHERE ' + ' AND '.join(conds)
            sql += ' ORDER BY created_at DESC LIMIT ?'
            params.append(limit)
            conn = _db_conn()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
            events = [dict(r) for r in rows]
            self._send_json(200, {'events': events, 'total': len(events)})
        except Exception as e:
            logger.error(f'  [KnowledgeEvents] list failed: {e}')
            self._send_json_error(500, f'List failed: {str(e)}')

    def _handle_get_knowledge_event_detail(self, event_id):
        """GET /api/knowledge-events/<id> — 单条完整事件（含 content_full，不含 embedding）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        try:
            conn = _db_conn()
            try:
                row = conn.execute(
                    'SELECT id, entity_type, entity_id, agent_id, event_type, title, '
                    'content_full, content_summary, conclusions, source_msg_id, user_query, created_at '
                    'FROM knowledge_events WHERE id = ?', (event_id,)).fetchone()
            finally:
                conn.close()
            if not row:
                self._send_json_error(404, 'Knowledge event not found')
                return
            self._send_json(200, dict(row))
        except Exception as e:
            logger.error(f'  [KnowledgeEvents] detail failed: {e}')
            self._send_json_error(500, f'Detail failed: {str(e)}')

    def _handle_get_knowledge_events_stats(self):
        """GET /api/knowledge-events/stats — 总数 / 各 entity_type 计数 / 最近7天新增数"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        try:
            since_7d = int((time.time() - 7 * 86400) * 1000)
            conn = _db_conn()
            try:
                total = conn.execute('SELECT COUNT(*) FROM knowledge_events').fetchone()[0]
                by_type_rows = conn.execute(
                    'SELECT entity_type, COUNT(*) AS c FROM knowledge_events GROUP BY entity_type').fetchall()
                recent_7d = conn.execute(
                    'SELECT COUNT(*) FROM knowledge_events WHERE created_at >= ?', (since_7d,)).fetchone()[0]
            finally:
                conn.close()
            by_entity_type = {(r['entity_type'] or 'unknown'): r['c'] for r in by_type_rows}
            self._send_json(200, {
                'total': total,
                'byEntityType': by_entity_type,
                'recent7d': recent_7d,
            })
        except Exception as e:
            logger.error(f'  [KnowledgeEvents] stats failed: {e}')
            self._send_json_error(500, f'Stats failed: {str(e)}')

    def _handle_search_knowledge_events(self):
        """GET /api/knowledge-events/search?q=&entity_type=&limit= — 三信号混合检索分析档案。
        向量 + FTS5 BM25 + 实体精确匹配 RRF 融合，叠加重要度/新鲜度打分（阶段4B-P0）。
        返回列表（不含 content_full，附 score）。"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        qs = parse_qs(urlparse(self.path).query)
        query = qs.get('q', [''])[0].strip()
        entity_type = qs.get('entity_type', [''])[0].strip()
        if not query:
            self._send_json_error(400, 'Missing q')
            return
        try:
            limit = max(1, min(50, int(qs.get('limit', [5])[0] or 5)))
        except (TypeError, ValueError):
            limit = 5
        try:
            results = _hybrid_retrieve_events(query, entity_type=entity_type, limit=limit)
            self._send_json(200, {'events': results, 'total': len(results)})
        except Exception as e:
            logger.error(f'  [KnowledgeEvents] search failed: {e}')
            self._send_json_error(500, f'Search failed: {str(e)}')

    # ═══ 规律库（knowledge_patterns，L3）═══
    def _handle_post_induce_knowledge_patterns(self):
        """POST /api/knowledge-patterns/induce — 触发同类目规律归纳（LLM），结果存 draft"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        category = ((body or {}).get('category') or '').strip()
        if not category:
            self._send_json_error(400, 'Missing category')
            return
        try:
            llm_config = _resolve_induce_llm_config((body or {}).get('agentId', '') or '')
            if not llm_config:
                self._send_json(200, {'ok': False, 'error': '未配置可用的 LLM API Key'})
                return
            ok, result = _induce_knowledge_patterns(category, llm_config, created_by=auth.user_id or '')
            payload = {'ok': ok}
            payload.update(result)
            self._send_json(200, payload)
        except Exception as e:
            logger.error(f'  [KnowledgePatterns] induce failed: {e}')
            self._send_json(200, {'ok': False, 'error': str(e)})

    def _handle_get_knowledge_patterns(self):
        """GET /api/knowledge-patterns?status=&category=&limit= — 规律列表（不含 evidence）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        qs = parse_qs(urlparse(self.path).query)
        status = qs.get('status', [''])[0].strip()
        category = qs.get('category', [''])[0].strip()
        try:
            limit = max(1, min(200, int(qs.get('limit', [50])[0] or 50)))
        except (TypeError, ValueError):
            limit = 50
        try:
            sql = 'SELECT * FROM knowledge_patterns'
            conds, params = [], []
            if status:
                conds.append('status = ?')
                params.append(status)
            if category:
                conds.append('category = ?')
                params.append(category)
            if conds:
                sql += ' WHERE ' + ' AND '.join(conds)
            sql += ' ORDER BY confidence DESC, updated_at DESC LIMIT ?'
            params.append(limit)
            conn = _db_conn()
            try:
                rows = conn.execute(sql, params).fetchall()
            finally:
                conn.close()
            patterns = [_kp_row_to_dict(r) for r in rows]
            self._send_json(200, {'patterns': patterns, 'total': len(patterns)})
        except Exception as e:
            logger.error(f'  [KnowledgePatterns] list failed: {e}')
            self._send_json_error(500, f'List failed: {str(e)}')

    def _handle_get_knowledge_pattern_detail(self, pattern_id):
        """GET /api/knowledge-patterns/<id> — 完整记录（含 evidence）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT * FROM knowledge_patterns WHERE id = ?', (pattern_id,)).fetchone()
            finally:
                conn.close()
            if not row:
                self._send_json_error(404, 'Pattern not found')
                return
            self._send_json(200, _kp_row_to_dict(row, with_evidence=True))
        except Exception as e:
            logger.error(f'  [KnowledgePatterns] detail failed: {e}')
            self._send_json_error(500, f'Detail failed: {str(e)}')

    def _handle_put_knowledge_pattern(self, pattern_id):
        """PUT /api/knowledge-patterns/<id> — 状态流转：draft→confirmed/rejected→deprecated"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        body = self._read_body()
        new_status = ((body or {}).get('status') or '').strip()
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT * FROM knowledge_patterns WHERE id = ?', (pattern_id,)).fetchone()
                if not row:
                    self._send_json_error(404, 'Pattern not found')
                    return
                cur_status = row['status'] or 'draft'
                allowed = _KP_STATUS_FLOW.get(cur_status, ())
                if new_status not in allowed:
                    self._send_json_error(400, f'非法状态转换: {cur_status} -> {new_status}')
                    return
                # 同步 verification_level：确认时尝试 hypothesis→candidate 晋升（不满足则保留 hypothesis）；
                # 拒绝/废弃统一降级为 deprecated
                cur_level = row['verification_level'] or 'hypothesis'
                new_level = cur_level
                if new_status == 'confirmed' and cur_level == 'hypothesis':
                    if _kp_can_promote('hypothesis', 'candidate', row['confidence_score'],
                                       row['evidence_count'], approved=True):
                        new_level = 'candidate'
                elif new_status in ('rejected', 'deprecated'):
                    new_level = 'deprecated'
                conn.execute('UPDATE knowledge_patterns SET status = ?, verification_level = ?, updated_at = ? WHERE id = ?',
                             (new_status, new_level, int(time.time()), pattern_id))
                conn.commit()
                row = conn.execute('SELECT * FROM knowledge_patterns WHERE id = ?', (pattern_id,)).fetchone()
            finally:
                conn.close()
            self._send_json(200, _kp_row_to_dict(row, with_evidence=True))
        except Exception as e:
            logger.error(f'  [KnowledgePatterns] update failed: {e}')
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_knowledge_pattern(self, pattern_id):
        """DELETE /api/knowledge-patterns/<id> — 硬删除"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'knowledge'): return
        try:
            conn = _db_conn()
            try:
                cur = conn.execute('DELETE FROM knowledge_patterns WHERE id = ?', (pattern_id,))
                conn.commit()
            finally:
                conn.close()
            self._send_json(200, {'deleted': cur.rowcount > 0, 'id': pattern_id})
        except Exception as e:
            logger.error(f'  [KnowledgePatterns] delete failed: {e}')
            self._send_json_error(500, f'Delete failed: {str(e)}')

    # ═══ 合作单（deals）═══
    def _handle_post_deal(self):
        """POST /api/deals — 创建合作单"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        deny = _check_agent_role_write_scope(auth, 'talent')
        if deny:
            self._send_json_error(deny[1], deny[0])
            return
        body = self._read_body() or {}
        talent_id = (body.get('talent_id') or '').strip()
        if not talent_id:
            self._send_json_error(400, 'Missing talent_id')
            return
        deny = _check_talent_write_permission(auth, talent_id)
        if deny:
            self._send_json_error(deny[1], deny[0])
            return
        status = (body.get('status') or 'pending').strip()
        if status not in _DEAL_STATUS_FLOW:
            self._send_json_error(400, f'非法状态: {status}')
            return
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT id FROM talents WHERE id = ?', (talent_id,)).fetchone()
                if not row:
                    self._send_json_error(404, 'Talent not found')
                    return
                deal_id = 'deal_' + uuid.uuid4().hex[:12]
                now = int(time.time())
                conn.execute('''
                    INSERT INTO deals
                    (id, talent_id, product_id, product_name, deal_type, commission_rate,
                     status, scheduled_at, predicted_conclusion, predicted_event_id,
                     created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (deal_id, talent_id,
                      body.get('product_id') or '', body.get('product_name') or '',
                      body.get('deal_type') or '', body.get('commission_rate') or 0,
                      status, body.get('scheduled_at') or 0,
                      body.get('predicted_conclusion') or '', body.get('predicted_event_id') or '',
                      _resolve_talent_owner_id(auth), now, now))
                conn.commit()
                row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
            finally:
                conn.close()
            self._send_json(200, _deal_row_to_dict(row))
        except Exception as e:
            logger.error(f'  [Deals] create failed: {e}')
            self._send_json_error(500, f'Create failed: {str(e)}')

    def _handle_get_deals(self):
        """GET /api/deals?talent_id=&status=&limit=&offset= — 合作单列表（子账号隔离）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        qs = parse_qs(urlparse(self.path).query)
        talent_id = qs.get('talent_id', [''])[0].strip()
        status = qs.get('status', [''])[0].strip()
        try:
            limit = max(1, min(200, int(qs.get('limit', [50])[0] or 50)))
        except (TypeError, ValueError):
            limit = 50
        try:
            offset = max(0, int(qs.get('offset', [0])[0] or 0))
        except (TypeError, ValueError):
            offset = 0
        try:
            sql = 'SELECT d.* FROM deals d JOIN talents t ON d.talent_id = t.id'
            count_sql = 'SELECT COUNT(*) FROM deals d JOIN talents t ON d.talent_id = t.id'
            conds, params = [], []
            # 子账号隔离：只能看到自己子库达人的合作单；管理员看全部
            if not auth.is_admin:
                owner = _resolve_talent_owner_id(auth)
                ids = sorted({owner} | set(_get_user_emp_ids(owner)))
                conds.append(f"t.created_by IN ({','.join('?' * len(ids))})")
                params.extend(ids)
            if talent_id:
                conds.append('d.talent_id = ?')
                params.append(talent_id)
            if status:
                conds.append('d.status = ?')
                params.append(status)
            if conds:
                sql += ' WHERE ' + ' AND '.join(conds)
                count_sql += ' WHERE ' + ' AND '.join(conds)
            sql += ' ORDER BY d.updated_at DESC LIMIT ? OFFSET ?'
            conn = _db_conn()
            try:
                total = conn.execute(count_sql, params).fetchone()[0]
                rows = conn.execute(sql, params + [limit, offset]).fetchall()
            finally:
                conn.close()
            self._send_json(200, {'deals': [_deal_row_to_dict(r) for r in rows], 'total': total})
        except Exception as e:
            logger.error(f'  [Deals] list failed: {e}')
            self._send_json_error(500, f'List failed: {str(e)}')

    def _handle_get_deal_detail(self, deal_id):
        """GET /api/deals/<id> — 完整记录"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
            finally:
                conn.close()
            if not row:
                self._send_json_error(404, 'Deal not found')
                return
            self._send_json(200, _deal_row_to_dict(row))
        except Exception as e:
            logger.error(f'  [Deals] detail failed: {e}')
            self._send_json_error(500, f'Detail failed: {str(e)}')

    def _handle_put_deal(self, deal_id):
        """PUT /api/deals/<id> — 更新合作单（含状态流转）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        deny = _check_agent_role_write_scope(auth, 'talent')
        if deny:
            self._send_json_error(deny[1], deny[0])
            return
        body = self._read_body() or {}
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
                if not row:
                    self._send_json_error(404, 'Deal not found')
                    return
                deny = _check_talent_write_permission(auth, row['talent_id'])
                if deny:
                    self._send_json_error(deny[1], deny[0])
                    return
                cur_status = row['status'] or 'pending'
                # 终态（completed/failed）不允许任何修改
                if cur_status in ('completed', 'failed'):
                    self._send_json_error(400, f'合作单已终态（{cur_status}），不可修改')
                    return
                new_status = (body.get('status') or '').strip()
                if new_status and new_status != cur_status:
                    if new_status not in _DEAL_STATUS_FLOW:
                        self._send_json_error(400, f'非法状态: {new_status}')
                        return
                    allowed = _DEAL_STATUS_FLOW.get(cur_status, ())
                    if new_status not in allowed:
                        self._send_json_error(400, f'非法状态转换: {cur_status} -> {new_status}')
                        return
                    # 切终态（completed/failed）必填 win_loss_category（body 未传则用库里现值）
                    if new_status in ('completed', 'failed'):
                        wlc = body.get('win_loss_category') if 'win_loss_category' in body else (row['win_loss_category'] or '')
                        wlc = str(wlc or '').strip()
                        if not wlc:
                            self._send_json_error(400, f'切为 {new_status} 需填写 win_loss_category（成败原因）')
                            return
                        if wlc not in _DEAL_WIN_LOSS_CATEGORIES:
                            self._send_json_error(400, f'非法 win_loss_category: {wlc}')
                            return
                    # 切 completed 要求 actual_gmv(>0) 或 result_note 至少填一个
                    if new_status == 'completed':
                        gmv = body.get('actual_gmv', row['actual_gmv']) or 0
                        note = (body.get('result_note') if 'result_note' in body else row['result_note']) or ''
                        if not gmv or float(gmv) <= 0:
                            if not str(note).strip():
                                self._send_json_error(400, '切为 completed 需填写 actual_gmv 或 result_note')
                                return
                # key_moment 非必填，但传了非空值必须在枚举内
                if 'key_moment' in body:
                    km = str(body.get('key_moment') or '').strip()
                    if km and km not in _DEAL_KEY_MOMENTS:
                        self._send_json_error(400, f'非法 key_moment: {km}')
                        return
                # 只更新 body 里出现的白名单字段
                updatable = ('product_id', 'product_name', 'deal_type', 'commission_rate',
                             'status', 'scheduled_at', 'actual_gmv', 'actual_roi',
                             'actual_units', 'result_note', 'predicted_conclusion',
                             'predicted_event_id', 'win_loss_category', 'key_moment',
                             'decision_maker_feedback')
                sets, params = [], []
                for field in updatable:
                    if field in body:
                        sets.append(f'{field} = ?')
                        params.append(body[field])
                sets.append('updated_at = ?')
                params.append(int(time.time()))
                params.append(deal_id)
                conn.execute(f'UPDATE deals SET {", ".join(sets)} WHERE id = ?', params)
                conn.commit()
                row = conn.execute('SELECT * FROM deals WHERE id = ?', (deal_id,)).fetchone()
            finally:
                conn.close()
            self._send_json(200, _deal_row_to_dict(row))
        except Exception as e:
            logger.error(f'  [Deals] update failed: {e}')
            self._send_json_error(500, f'Update failed: {str(e)}')

    def _handle_delete_deal(self, deal_id):
        """DELETE /api/deals/<id> — 硬删除"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        deny = _check_agent_role_write_scope(auth, 'talent')
        if deny:
            self._send_json_error(deny[1], deny[0])
            return
        try:
            conn = _db_conn()
            try:
                row = conn.execute('SELECT talent_id FROM deals WHERE id = ?', (deal_id,)).fetchone()
                if not row:
                    self._send_json_error(404, 'Deal not found')
                    return
                deny = _check_talent_write_permission(auth, row['talent_id'])
                if deny:
                    self._send_json_error(deny[1], deny[0])
                    return
                cur = conn.execute('DELETE FROM deals WHERE id = ?', (deal_id,))
                conn.commit()
            finally:
                conn.close()
            self._send_json(200, {'deleted': cur.rowcount > 0, 'id': deal_id})
        except Exception as e:
            logger.error(f'  [Deals] delete failed: {e}')
            self._send_json_error(500, f'Delete failed: {str(e)}')

    def _handle_post_kb_search(self):
        """POST /api/knowledge/search — 新版语义搜索"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        category_id = body.get('categoryId') or body.get('category_id')
        project_id = body.get('projectId') or body.get('project_id')
        search_emp_id = body.get('empId') or body.get('emp_id')
        allowed_cats = _allowed_knowledge_categories(auth)
        if category and not _can_access_knowledge_category(auth, category):
            self._send_json(200, {'query': query, 'docs': [], 'count': 0})
            return
        try:
            docs = ks.kb_entry_search_semantic(
                query=query, limit=limit,
                allowed_categories=allowed_cats,
                scope=scope, category=category,
                category_id=category_id, project_id=project_id,
                user_id=auth.user_id, is_admin=auth.is_admin,
                user_team_ids=auth.team_ids, user_group_ids=auth.group_ids,
                emp_ids=_get_user_emp_ids(auth.user_id),
                author_emp_id=search_emp_id
            )
            self._send_json(200, {'query': query, 'docs': docs, 'count': len(docs)})
        except Exception as e:
            logger.error(f'  [KBSearch] failed: {e}')
            self._send_json_error(500, f'Search failed: {str(e)}')

    def _handle_get_stats_compute(self):
        """GET /api/stats/compute — 真实 Token/调用统计"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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

    # ─── 积分制算力管控 API（1 积分 = 1000 tokens，字段统一 agent_id）───
    def _handle_get_credit_balance(self):
        """GET /api/credits/balance?agent_id=xxx — 查询积分余额（返回列表）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get('agent_id', [''])[0].strip()
        conn = _db_conn()
        try:
            if agent_id:
                account = _ensure_credit_account(conn, agent_id)
                conn.commit()
                rows = [account]
            else:
                rows = conn.execute('SELECT * FROM credit_accounts ORDER BY updated_at DESC').fetchall()
            items = [{
                'agent_id': r['agent_id'],
                'balance': r['balance'] or 0,
                'total_recharged': r['total_recharged'] or 0,
                'total_consumed': r['total_consumed'] or 0,
                'updated_at': r['updated_at'] or '',
            } for r in rows]
            self._send_json(200, items)
        finally:
            conn.close()

    def _handle_get_credit_quotas(self):
        """GET /api/credits/quotas?agent_id=xxx — 查询配额/充值记录列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get('agent_id', [''])[0].strip()
        conn = _db_conn()
        try:
            if agent_id:
                rows = conn.execute(
                    'SELECT * FROM credit_quotas WHERE agent_id = ? ORDER BY id DESC', (agent_id,)
                ).fetchall()
            else:
                rows = conn.execute('SELECT * FROM credit_quotas ORDER BY id DESC').fetchall()
            items = [{
                'id': r['id'],
                'agent_id': r['agent_id'],
                'quota_type': r['quota_type'] or '',
                'quota_amount': r['quota_amount'] or 0,
                'effective_from': r['effective_from'] or '',
                'created_at': r['created_at'] or '',
            } for r in rows]
            self._send_json(200, items)
        finally:
            conn.close()

    def _handle_credit_recharge(self, agent_id):
        """POST /api/credits/quotas/:agentId/recharge — 给员工充值积分并写入配额记录 {amount, quota_type}"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin:
            self._send_json_error(403, '仅管理员可充值积分')
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, '无效的请求体')
            return
        try:
            amount = int(body.get('amount'))
        except (TypeError, ValueError):
            self._send_json_error(400, 'amount 必须是整数')
            return
        if amount <= 0:
            self._send_json_error(400, 'amount 必须大于 0')
            return
        quota_type = body.get('quota_type') or 'monthly'
        if quota_type not in ('daily', 'monthly'):
            self._send_json_error(400, "quota_type 必须是 'daily' 或 'monthly'")
            return
        conn = _db_conn()
        try:
            new_balance = _recharge_credits(conn, agent_id, amount, operator=auth.user_id)
            conn.execute(
                'INSERT INTO credit_quotas (agent_id, quota_type, quota_amount, effective_from) VALUES (?, ?, ?, ?)',
                (agent_id, quota_type, amount, datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            )
            conn.commit()
            self._send_json(200, {'agent_id': agent_id, 'new_balance': new_balance, 'amount': amount})
        finally:
            conn.close()

    def _handle_credit_recharge_generic(self):
        """POST /api/credits/recharge — 通用充值接口（管理员用，不写配额记录）{agent_id, amount}"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin:
            self._send_json_error(403, '仅管理员可充值积分')
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, '无效的请求体')
            return
        agent_id = (body.get('agent_id') or '').strip()
        if not agent_id:
            self._send_json_error(400, '缺少 agent_id')
            return
        try:
            amount = int(body.get('amount'))
        except (TypeError, ValueError):
            self._send_json_error(400, 'amount 必须是整数')
            return
        if amount <= 0:
            self._send_json_error(400, 'amount 必须大于 0')
            return
        conn = _db_conn()
        try:
            new_balance = _recharge_credits(conn, agent_id, amount, operator=auth.user_id)
            conn.commit()
            self._send_json(200, {'agent_id': agent_id, 'new_balance': new_balance, 'amount': amount})
        finally:
            conn.close()

    def _handle_get_credit_usage(self):
        """GET /api/credits/usage?agent_id=&start_date=&end_date=&page=&page_size= — 使用记录（分页+日期过滤）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        try:
            page = max(int(qs.get('page', ['1'])[0] or 1), 1)
        except ValueError:
            page = 1
        try:
            page_size = min(max(int(qs.get('page_size', ['20'])[0] or 20), 1), 200)
        except ValueError:
            page_size = 20
        agent_id = qs.get('agent_id', [''])[0].strip()
        start_date = qs.get('start_date', [''])[0].strip()
        end_date = qs.get('end_date', [''])[0].strip()

        where = []
        params = []
        if agent_id:
            where.append('agent_id = ?')
            params.append(agent_id)
        if start_date:
            where.append("date(created_at) >= date(?)")
            params.append(start_date)
        if end_date:
            where.append("date(created_at) <= date(?)")
            params.append(end_date)
        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''

        conn = _db_conn()
        try:
            total = conn.execute(
                f'SELECT COUNT(*) AS c FROM credit_usage_log {where_sql}', tuple(params)
            ).fetchone()['c'] or 0
            rows = conn.execute(
                f'SELECT * FROM credit_usage_log {where_sql} ORDER BY id DESC LIMIT ? OFFSET ?',
                tuple(params) + (page_size, (page - 1) * page_size)
            ).fetchall()
            data = [{
                'id': r['id'],
                'agent_id': r['agent_id'],
                'input_tokens': r['input_tokens'] or 0,
                'output_tokens': r['output_tokens'] or 0,
                'cache_read_tokens': r['cache_read_tokens'] or 0,
                'total_tokens': r['total_tokens'] or 0,
                'credits_used': r['credits_used'] or 0,
                'session_id': r['session_id'] or '',
                'created_at': r['created_at'] or '',
            } for r in rows]
            self._send_json(200, {'total': total, 'page': page, 'page_size': page_size, 'data': data})
        finally:
            conn.close()

    def _handle_get_credit_usage_summary(self):
        """GET /api/credits/usage/summary?agent_id=&start_date=&end_date= — 使用汇总"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get('agent_id', [''])[0].strip()
        start_date = qs.get('start_date', [''])[0].strip()
        end_date = qs.get('end_date', [''])[0].strip()

        where = []
        params = []
        if agent_id:
            where.append('agent_id = ?')
            params.append(agent_id)
        if start_date:
            where.append("date(created_at) >= date(?)")
            params.append(start_date)
        if end_date:
            where.append("date(created_at) <= date(?)")
            params.append(end_date)
        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''

        conn = _db_conn()
        try:
            row = conn.execute(
                f'''SELECT COALESCE(SUM(credits_used),0) AS credits,
                           COALESCE(SUM(total_tokens),0) AS tokens,
                           COUNT(*) AS records_count,
                           COUNT(DISTINCT date(created_at)) AS active_days
                    FROM credit_usage_log {where_sql}''',
                tuple(params)
            ).fetchone()
            total_credits = row['credits'] or 0
            total_tokens = row['tokens'] or 0
            records_count = row['records_count'] or 0
            # 日均分母：指定了日期范围则按范围天数，否则按有记录的天数，至少为 1
            days = 0
            if start_date and end_date:
                try:
                    days = (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 1
                except ValueError:
                    days = 0
            if days <= 0:
                days = row['active_days'] or 0
            days = max(days, 1)
            self._send_json(200, {
                'agent_id': agent_id,
                'total_credits_used': total_credits,
                'total_tokens': total_tokens,
                'daily_avg_credits': round(total_credits / days, 2),
                'daily_avg_tokens': round(total_tokens / days, 2),
                'records_count': records_count,
            })
        finally:
            conn.close()

    def _handle_get_credit_check(self):
        """GET /api/credits/check?agent_id=xxx — 发送前检查积分是否充足"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        agent_id = qs.get('agent_id', [''])[0].strip()
        if not agent_id:
            self._send_json_error(400, '缺少 agent_id 参数')
            return
        balance, has_credits = _check_credit_balance(agent_id)
        self._send_json(200, {
            'agent_id': agent_id,
            'balance': balance,
            'has_credits': has_credits,
            'message': '积分充足' if has_credits else '积分不足',
        })

    def _handle_get_token_usage_sync(self):
        """GET /api/token-usage/sync — 从 OpenClaw trajectory 同步 token 数据"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        try:
            result = _sync_token_usage_from_trajectories()
            self._send_json(200, result)
        except Exception as e:
            logger.error(f'  [TokenUsageSync] failed: {e}')
            import traceback; traceback.print_exc()
            self._send_json_error(500, f'Sync failed: {str(e)}')

    def _handle_get_token_usage(self):
        """GET /api/token-usage — 按 agent/day 聚合 token 用量"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        agent_id = qs.get('agent_id', [''])[0] or ''
        start_date = qs.get('start_date', [''])[0] or ''
        end_date = qs.get('end_date', [''])[0] or ''
        group_by = qs.get('group_by', ['agent'])[0] or 'agent'
        if group_by not in ('agent', 'day'):
            group_by = 'agent'

        where = []
        params = []

        if agent_id:
            where.append('agent_id = ?')
            params.append(agent_id)

        def _date_to_millis(d_str, end_of_day=False):
            try:
                dt = datetime.strptime(d_str, '%Y-%m-%d')
                if end_of_day:
                    dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                return int(dt.timestamp() * 1000)
            except Exception:
                return None

        if start_date:
            ms = _date_to_millis(start_date, False)
            if ms is not None:
                where.append('ts >= ?')
                params.append(ms)
        if end_date:
            ms = _date_to_millis(end_date, True)
            if ms is not None:
                where.append('ts <= ?')
                params.append(ms)

        # 非管理员只能查看自己创建的 agent 数据
        user_emp_ids = _get_user_emp_ids(auth.user_id)
        if not auth.is_admin:
            placeholders = ', '.join('?' for _ in user_emp_ids) if user_emp_ids else None
            if placeholders:
                where.append(f'agent_id IN ({placeholders})')
                params.extend(user_emp_ids)
            else:
                # 无可用 agent 时返回空结果
                self._send_json(200, {
                    'summary': {'inputTokens': 0, 'outputTokens': 0, 'cacheReadTokens': 0, 'totalTokens': 0, 'calls': 0},
                    'groupBy': group_by,
                    'items': []
                })
                return

        where_sql = 'WHERE ' + ' AND '.join(where) if where else ''
        conn = _db_conn()
        try:
            # 总计
            sum_row = conn.execute(
                f'''SELECT COALESCE(SUM(prompt_tokens),0) AS input_tokens,
                           COALESCE(SUM(completion_tokens),0) AS output_tokens,
                           COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                           COALESCE(SUM(total_tokens),0) AS total_tokens,
                           COUNT(*) AS calls
                    FROM token_usage {where_sql}''',
                tuple(params)
            ).fetchone()

            summary = {
                'inputTokens': sum_row['input_tokens'] or 0,
                'outputTokens': sum_row['output_tokens'] or 0,
                'cacheReadTokens': sum_row['cache_read_tokens'] or 0,
                'totalTokens': sum_row['total_tokens'] or 0,
                'calls': sum_row['calls'] or 0,
            }

            if group_by == 'agent':
                rows = conn.execute(
                    f'''SELECT agent_id,
                               COALESCE(SUM(prompt_tokens),0) AS input_tokens,
                               COALESCE(SUM(completion_tokens),0) AS output_tokens,
                               COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                               COALESCE(SUM(total_tokens),0) AS total_tokens,
                               COUNT(*) AS calls
                        FROM token_usage {where_sql}
                        GROUP BY agent_id
                        ORDER BY total_tokens DESC''',
                    tuple(params)
                ).fetchall()
                agents_map = {a.get('id'): a for a in _load_agents(include_archived=True)}
                items = []
                for r in rows:
                    aid = r['agent_id'] or ''
                    agent = agents_map.get(aid)
                    name = (agent.get('name') or aid) if agent else aid
                    items.append({
                        'id': aid,
                        'name': name,
                        'inputTokens': r['input_tokens'] or 0,
                        'outputTokens': r['output_tokens'] or 0,
                        'cacheReadTokens': r['cache_read_tokens'] or 0,
                        'totalTokens': r['total_tokens'] or 0,
                        'calls': r['calls'] or 0,
                    })
            else:
                rows = conn.execute(
                    f'''SELECT date(ts/1000, 'unixepoch', 'localtime') AS d,
                               COALESCE(SUM(prompt_tokens),0) AS input_tokens,
                               COALESCE(SUM(completion_tokens),0) AS output_tokens,
                               COALESCE(SUM(cache_read_tokens),0) AS cache_read_tokens,
                               COALESCE(SUM(total_tokens),0) AS total_tokens,
                               COUNT(*) AS calls
                        FROM token_usage {where_sql}
                        GROUP BY d
                        ORDER BY d ASC''',
                    tuple(params)
                ).fetchall()
                items = []
                for r in rows:
                    items.append({
                        'date': r['d'] or '',
                        'inputTokens': r['input_tokens'] or 0,
                        'outputTokens': r['output_tokens'] or 0,
                        'cacheReadTokens': r['cache_read_tokens'] or 0,
                        'totalTokens': r['total_tokens'] or 0,
                        'calls': r['calls'] or 0,
                    })
        finally:
            conn.close()

        self._send_json(200, {
            'summary': summary,
            'groupBy': group_by,
            'items': items,
        })

    # ═══════════════════════════════════════════════════
    # RAG API
    # ═══════════════════════════════════════════════════

    def _handle_post_rag_retrieve(self):
        """POST /api/rag/retrieve — RAG 向量检索（全局知识库 + 产品库）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.error(f'  [RAG] product embedding query failed: {e}')
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
            logger.error(f'  [RAG] retrieve failed: {e}')
            import traceback; traceback.print_exc()
            self._send_json_error(500, f'RAG retrieve failed: {str(e)}')

    def _handle_post_rag_build(self):
        """POST /api/rag/build — 批量构建所有 embedding 索引"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.error(f'  [RAG] build failed: {e}')
            self._send_json_error(500, f'Build failed: {str(e)}')

    def _handle_post_tool_calls_log(self):
        """POST /api/tool-calls/log — 记录 OpenClaw 工具调用日志"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        agent_id = body.get('agent_id') or ''
        tool_call_id = body.get('tool_call_id') or ''
        tool_name = body.get('tool_name') or ''
        meta = body.get('meta') or ''
        output = body.get('output') or ''
        exit_code = body.get('exit_code')
        duration_ms = body.get('duration_ms')
        try:
            conn = _db_conn()
            try:
                conn.execute('''
                    INSERT INTO tool_calls
                    (agent_id, tool_call_id, tool_name, meta, output, exit_code, duration_ms)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    agent_id,
                    tool_call_id,
                    tool_name,
                    meta if isinstance(meta, str) else json.dumps(meta, ensure_ascii=False),
                    output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
                    int(exit_code) if exit_code is not None else None,
                    int(duration_ms) if duration_ms is not None else None,
                ))
                conn.commit()
            finally:
                conn.close()
            # 推送工具执行通知（受用户 message_notify 开关控制）
            _push_notification(
                auth.user_id, 'tool_call',
                f'工具执行完成: {tool_name or "未知工具"}',
                (output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))[:200],
                agent_id
            )
            self._send_json(200, {'success': True})
        except Exception as e:
            logger.error(f'  [TOOL-CALLS-LOG] failed: {e}')
            import traceback; traceback.print_exc()
            self._send_json_error(500, f'Log tool call failed: {str(e)}')

    # ═══════════════════════════════════════════════════
    # 通知 API
    # ═══════════════════════════════════════════════════

    def _notification_row_to_dict(self, row):
        return {
            'id': row['id'],
            'user_id': row['user_id'],
            'agent_id': row['agent_id'] or '',
            'type': row['type'] or '',
            'title': row['title'] or '',
            'content': row['content'] or '',
            'read': int(row['read'] or 0),
            'created_at': int(row['created_at'] or 0),
        }

    def _handle_get_notifications(self):
        """GET /api/notifications — 当前用户通知列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        limit = max(1, min(200, int(qs.get('limit', [50])[0] or 50)))
        unread_only = qs.get('unread_only', [''])[0] == '1'
        conn = _db_conn()
        try:
            sql = 'SELECT * FROM notifications WHERE user_id = ?'
            params = [auth.user_id]
            if unread_only:
                sql += ' AND read = 0'
            sql += ' ORDER BY created_at DESC LIMIT ?'
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            unread = conn.execute(
                'SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read = 0',
                (auth.user_id,)
            ).fetchone()['c']
            self._send_json(200, {
                'items': [self._notification_row_to_dict(r) for r in rows],
                'unreadCount': int(unread),
            })
        finally:
            conn.close()

    def _handle_post_notification(self):
        """POST /api/notifications — 推送一条通知（user_id 取当前认证用户）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        notif_type = (body.get('type') or 'message').strip()
        title = (body.get('title') or '').strip()
        content = (body.get('content') or '').strip()
        if not title and not content:
            self._send_json_error(400, 'Missing title or content')
            return
        agent_id = (body.get('agent_id') or '').strip()
        notif_id = _push_notification(auth.user_id, notif_type, title, content, agent_id)
        if not notif_id:
            # 开关关闭，不推送，返回成功但标记 skipped
            self._send_json(200, {'success': True, 'skipped': True})
            return
        self._send_json(200, {'success': True, 'id': notif_id})

    def _handle_notification_read(self, notif_id):
        """PUT /api/notifications/{id}/read — 标记单条已读（限本人）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT user_id FROM notifications WHERE id = ?', (notif_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Notification not found')
                return
            if row['user_id'] != auth.user_id:
                self._send_json_error(403, 'Permission denied')
                return
            conn.execute('UPDATE notifications SET read = 1 WHERE id = ?', (notif_id,))
            conn.commit()
            self._send_json(200, {'success': True})
        finally:
            conn.close()

    def _handle_notifications_read_all(self):
        """PUT /api/notifications/read-all — 当前用户全部已读"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        conn = _db_conn()
        try:
            conn.execute('UPDATE notifications SET read = 1 WHERE user_id = ?', (auth.user_id,))
            conn.commit()
            self._send_json(200, {'success': True})
        finally:
            conn.close()

    def _handle_delete_notification(self, notif_id):
        """DELETE /api/notifications/{id} — 删除单条（限本人）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT user_id FROM notifications WHERE id = ?', (notif_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Notification not found')
                return
            if row['user_id'] != auth.user_id:
                self._send_json_error(403, 'Permission denied')
                return
            conn.execute('DELETE FROM notifications WHERE id = ?', (notif_id,))
            conn.commit()
            self._send_json(200, {'success': True})
        finally:
            conn.close()

    def _handle_get_notification_settings(self):
        """GET /api/notification-settings — 当前用户通知开关"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        self._send_json(200, _get_notification_settings(auth.user_id))

    def _handle_put_notification_settings(self):
        """PUT /api/notification-settings — 保存通知开关"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        current = _get_notification_settings(auth.user_id)
        for key in ('message_notify', 'group_urge', 'task_reminder'):
            if key in body:
                current[key] = 1 if body.get(key) else 0
        conn = _db_conn()
        try:
            conn.execute('''
                INSERT OR REPLACE INTO user_settings (user_id, message_notify, group_urge, task_reminder)
                VALUES (?, ?, ?, ?)
            ''', (auth.user_id, current['message_notify'], current['group_urge'], current['task_reminder']))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, current)

    def _handle_get_account(self):
        """GET /api/account — 当前用户账号设置（用户数据存 users.json）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        user = _find_user(_load_users(), 'id', auth.user_id)
        if not user:
            self._send_json_error(404, 'User not found')
            return
        self._send_json(200, {
            'displayName': user.get('displayName', ''),
            'username': user.get('username', ''),
            'role': user.get('role', 'employee'),
            'avatar': user.get('avatar', 0),
            'email': user.get('email', ''),
            'theme': user.get('theme', 'light'),
            'language': user.get('language', '中文'),
        })

    def _handle_put_account(self):
        """PUT /api/account — 保存账号设置（仅更新传入的字段）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body()
        if not isinstance(body, dict):
            self._send_json_error(400, 'Invalid request body')
            return
        users = _load_users()
        user = _find_user(users, 'id', auth.user_id)
        if not user:
            self._send_json_error(404, 'User not found')
            return
        updated = False
        if 'displayName' in body:
            display_name = str(body.get('displayName') or '').strip()
            if not display_name:
                self._send_json_error(400, 'displayName 不能为空')
                return
            user['displayName'] = display_name
            updated = True
        if 'email' in body:
            email = str(body.get('email') or '').strip()
            if email and ('@' not in email or len(email) > 100):
                self._send_json_error(400, 'email 格式不正确')
                return
            user['email'] = email
            updated = True
        if 'theme' in body:
            theme = str(body.get('theme') or '').strip()
            if theme not in ('light', 'dark', 'auto'):
                self._send_json_error(400, 'theme 仅支持 light/dark/auto')
                return
            user['theme'] = theme
            updated = True
        if 'language' in body:
            language = str(body.get('language') or '').strip()
            if language not in ('中文', 'English'):
                self._send_json_error(400, 'language 仅支持 中文/English')
                return
            user['language'] = language
            updated = True
        if 'avatar' in body:
            avatar = body.get('avatar')
            if not isinstance(avatar, int) or avatar < 0:
                self._send_json_error(400, 'avatar 必须为非负整数')
                return
            user['avatar'] = avatar
            updated = True
        if not updated:
            self._send_json_error(400, 'No fields to update')
            return
        _save_users(users)
        self._send_json(200, {
            'displayName': user.get('displayName', ''),
            'username': user.get('username', ''),
            'role': user.get('role', 'employee'),
            'avatar': user.get('avatar', 0),
            'email': user.get('email', ''),
            'theme': user.get('theme', 'light'),
            'language': user.get('language', '中文'),
        })

    # ═══════════════════════════════════════════════════
    # 违禁词 API
    # ═══════════════════════════════════════════════════

    def _forbidden_word_row_to_dict(self, row):
        return {
            'id': row['id'],
            'word': row['word'],
            'category': row['category'] or 'general',
            'created_by': row['created_by'] or '',
            'created_at': int(row['created_at'] or 0),
        }

    def _handle_get_forbidden_words(self):
        """GET /api/forbidden-words — 违禁词列表（keyword 模糊搜索）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        keyword = (qs.get('keyword', [''])[0] or '').strip()
        conn = _db_conn()
        try:
            if keyword:
                rows = conn.execute(
                    'SELECT * FROM forbidden_words WHERE word LIKE ? ORDER BY created_at DESC',
                    (f'%{keyword}%',)
                ).fetchall()
            else:
                rows = conn.execute('SELECT * FROM forbidden_words ORDER BY created_at DESC').fetchall()
            self._send_json(200, {'items': [self._forbidden_word_row_to_dict(r) for r in rows]})
        finally:
            conn.close()

    def _handle_post_forbidden_words(self):
        """POST /api/forbidden-words — 添加违禁词（支持 {word} 单个或 {words: [...]} 批量，重复跳过）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        words = []
        if isinstance(body.get('words'), list):
            words = body['words']
        elif body.get('word'):
            words = [body['word']]
        words = [str(w).strip() for w in words if str(w).strip()]
        if not words:
            self._send_json_error(400, 'Missing word or words')
            return
        category = (body.get('category') or 'general').strip() or 'general'
        now = int(time.time() * 1000)
        added = 0
        conn = _db_conn()
        try:
            for w in words:
                cur = conn.execute(
                    'INSERT OR IGNORE INTO forbidden_words (id, word, category, created_by, created_at) VALUES (?, ?, ?, ?, ?)',
                    ('fw_' + uuid.uuid4().hex[:12], w, category, auth.user_id, now)
                )
                added += cur.rowcount
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'success': True, 'added': added})

    def _handle_delete_forbidden_word(self, word_id):
        """DELETE /api/forbidden-words/{id} — 删除违禁词"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        conn = _db_conn()
        try:
            cur = conn.execute('DELETE FROM forbidden_words WHERE id = ?', (word_id,))
            conn.commit()
            if cur.rowcount == 0:
                self._send_json_error(404, 'Forbidden word not found')
                return
            self._send_json(200, {'success': True})
        finally:
            conn.close()

    def _handle_forbidden_words_check(self):
        """POST /api/forbidden-words/check — 检查文本是否命中违禁词（子串包含）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body() or {}
        text = str(body.get('text') or '')
        if not text:
            self._send_json(200, {'hasViolation': False, 'words': []})
            return
        conn = _db_conn()
        try:
            rows = conn.execute('SELECT word FROM forbidden_words').fetchall()
        finally:
            conn.close()
        hits = [r['word'] for r in rows if r['word'] and r['word'] in text]
        self._send_json(200, {'hasViolation': len(hits) > 0, 'words': hits})

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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            kws = query['q'][0].lower().split()
            def _match_product(p):
                fields = ' '.join([
                    str(p.get('id') or ''), str(p.get('name') or ''), str(p.get('description') or ''),
                    str(p.get('brand') or ''), str(p.get('category') or ''), str(p.get('subtitle') or '')
                ] + [str(t) for t in (p.get('tags') or [])]).lower()
                return all(kw in fields for kw in kws)
            products = [p for p in products if _match_product(p)]
        if not auth.is_admin:
            uid = auth.user_info.get('userId', '')
            ids = {uid} | set(_get_user_emp_ids(uid))
            products = [p for p in products if (p.get('created_by') or p.get('createdBy') or '') in ids]
        # 分页
        offset = int(query.get('offset', [0])[0])
        limit = int(query.get('limit', [50])[0])
        total = len(products)
        products = products[offset:offset + limit]
        name_map = _user_display_name_map()
        for p in products:
            p['createdByName'] = name_map.get(p.get('created_by') or p.get('createdBy'), '')
        self._send_json(200, {'products': products, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_product(self, product_id):
        """GET /api/products/:id — 获取单个商品详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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

    # ─── V3 商品评分（v3_scorer 集成）────────────────────────────────

    @staticmethod
    def _product_to_v3_input(d):
        """把商品 dict（前端/数据库字段）映射为 v3_scorer 的中文输入字段。"""
        commission = d.get('commission_rate')
        if commission is None:
            rates = d.get('commission_rates') or {}
            if isinstance(rates, dict):
                commission = rates.get('default')
        return {
            '商品名称': d.get('name') or d.get('product_name') or '',
            '品类': d.get('category') or '',
            '价格': float(d.get('price') or 0),
            '页面佣金率': float(commission or 0),
            '好评率': float(d.get('review_rate') or d.get('好评率') or 95),
            '店铺评分': float(d.get('store_score') or d.get('店铺评分') or 95),
            '品牌类型': d.get('brand_type') or d.get('品牌类型') or '白牌/无品牌',
            '月销量': float(d.get('monthly_sales') or 0),
        }

    def _handle_get_product_score(self, product_id):
        """GET /api/products/:id/score — 用 V3 模型给已录入商品打分"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        result = v3.score_product(self._product_to_v3_input(product))
        result['product_id'] = product_id
        self._send_json(200, result)

    def _handle_score_product(self):
        """POST /api/products/score — 对请求体中的单个商品打分（不入库）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing product body')
            return
        result = v3.score_product(self._product_to_v3_input(body))
        self._send_json(200, result)

    def _handle_batch_score_products(self):
        """POST /api/products/batch-score — 批量打分，body: {"products": [...]}"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        body = self._read_body()
        products = (body or {}).get('products')
        if not isinstance(products, list) or not products:
            self._send_json_error(400, 'Missing products list')
            return
        results = [v3.score_product(self._product_to_v3_input(p)) for p in products]
        self._send_json(200, {'results': results, 'total': len(results)})

    # ─── 任务管理 API ────────────────────────────────────

    def _handle_get_tasks(self):
        """GET /api/tasks — 任务列表，支持status/assignee过滤"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        qs = parse_qs(urlparse(self.path).query)
        status_filter = qs.get('status', [None])[0]
        assignee_filter = qs.get('assignee', [None])[0]
        conn = _db_conn()
        try:
            sql = 'SELECT * FROM tasks WHERE 1=1'
            params = []
            if status_filter:
                sql += ' AND status = ?'
                params.append(status_filter)
            if assignee_filter:
                sql += ' AND assignee = ?'
                params.append(assignee_filter)
            if not auth.is_admin:
                uid = auth.user_info.get('userId', '')
                sql += ' AND (assignee = ? OR creator = ?)'
                params.extend([uid, uid])
            sql += ' ORDER BY created_at DESC'
            rows = conn.execute(sql, params).fetchall()
            tasks = [dict(r) for r in rows]
        finally:
            conn.close()
        self._send_json(200, {'tasks': tasks})

    def _handle_get_task(self, task_id):
        """GET /api/tasks/:id — 单个任务详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            self._send_json_error(404, '任务不存在')
            return
        task = dict(row)
        if not auth.is_admin:
            uid = auth.user_info.get('userId', '')
            if task.get('assignee') != uid and task.get('creator') != uid:
                self._send_json_error(403, '无权查看此任务')
                return
        self._send_json(200, task)

    def _handle_post_task(self):
        """POST /api/tasks — 创建任务（仅管理员）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin:
            self._send_json_error(403, '仅管理员可创建任务')
            return
        body = self._read_body()
        if not body or not body.get('title'):
            self._send_json_error(400, '任务标题不能为空')
            return
        task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        conn = _db_conn()
        try:
            conn.execute('''INSERT INTO tasks (id, title, description, assignee, assignee_name, creator, creator_name, status, priority, deadline, project_id, progress)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', (
                task_id,
                body.get('title', '').strip(),
                body.get('description', ''),
                body.get('assignee', ''),
                body.get('assigneeName', ''),
                auth.user_info.get('userId', ''),
                auth.user_info.get('displayName', ''),
                body.get('status', 'pending'),
                body.get('priority', 'normal'),
                body.get('deadline', ''),
                body.get('projectId', ''),
                body.get('progress', '')
            ))
            conn.commit()
            row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(201, dict(row))

    def _handle_put_task(self, task_id):
        """PUT /api/tasks/:id — 更新任务（管理员可改全部，员工只能改status和progress）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
            if not row:
                self._send_json_error(404, '任务不存在')
                return
            task = dict(row)
            uid = auth.user_info.get('userId', '')
            if not auth.is_admin:
                if task.get('assignee') != uid and task.get('creator') != uid:
                    self._send_json_error(403, '无权修改此任务')
                    return
            # 员工只能改 status 和 progress，管理员可改全部
            allowed_fields = ['status', 'progress'] if not auth.is_admin else ['title', 'description', 'assignee', 'assignee_name', 'status', 'priority', 'deadline', 'project_id', 'progress']
            updates = []
            params = []
            for field in allowed_fields:
                if field in body:
                    updates.append(f'{field} = ?')
                    params.append(body[field])
            if updates:
                updates.append("updated_at = datetime('now', 'localtime')")
                if body.get('status') == 'completed':
                    updates.append("completed_at = datetime('now', 'localtime')")
                params.append(task_id)
                conn.execute(f'UPDATE tasks SET {", ".join(updates)} WHERE id = ?', params)
                conn.commit()
            row = conn.execute('SELECT * FROM tasks WHERE id = ?', (task_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(200, dict(row))

    def _handle_delete_task(self, task_id):
        """DELETE /api/tasks/:id — 删除任务（仅管理员）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not auth.is_admin:
            self._send_json_error(403, '仅管理员可删除任务')
            return
        conn = _db_conn()
        try:
            cur = conn.execute('DELETE FROM tasks WHERE id = ?', (task_id,))
            conn.commit()
            deleted = cur.rowcount > 0
        finally:
            conn.close()
        self._send_json(200, {'deleted': deleted, 'id': task_id})

    def _handle_memory_pipeline_atoms(self):
        """GET /api/memory/atoms?agent_id=xxx&type=xxx&limit=50 — 获取 L1 事实原子列表"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        qs = self._parse_query()
        agent_id = qs.get('agent_id', [''])[0]
        if not agent_id:
            self._send_json(400, {'success': False, 'error': '缺少 agent_id 参数'})
            return
        atom_type = qs.get('type', [None])[0]
        try:
            limit = max(1, min(200, int(qs.get('limit', ['50'])[0])))
        except ValueError:
            limit = 50

        conn = _db_conn()
        try:
            atoms = memory_pipeline.get_agent_atoms(conn, agent_id, atom_type=atom_type, limit=limit)
        finally:
            conn.close()
        # embedding 体积大且无前端用途，返回前剔除
        for a in atoms:
            a.pop('content_embedding', None)
        self._send_json(200, {'success': True, 'data': atoms})


    def _handle_memory_pipeline_stats(self):
        """GET /api/memory/stats?agent_id=xxx — 获取记忆统计（agent_id 可选，缺省为全局）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        qs = self._parse_query()
        agent_id = qs.get('agent_id', [None])[0]

        conn = _db_conn()
        try:
            stats = memory_pipeline.get_memory_stats(conn, agent_id=agent_id)
        finally:
            conn.close()
        self._send_json(200, {'success': True, 'data': stats})


    def _handle_memory_pipeline_status(self):
        """GET /api/memory/pipeline?agent_id=xxx — 获取 Pipeline 调度状态"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return

        qs = self._parse_query()
        agent_id = qs.get('agent_id', [''])[0]
        if not agent_id:
            self._send_json(400, {'success': False, 'error': '缺少 agent_id 参数'})
            return

        conn = _db_conn()
        try:
            status = memory_pipeline.get_pipeline_status(conn, agent_id)
        finally:
            conn.close()
        self._send_json(200, {'success': True, 'data': status})


    def _handle_sync_feishu_talents(self):
        """POST /api/talents/sync-feishu — 从飞书多维表格同步达人数据"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        user_id = auth.user_info.get('userId', '')
        _cfg_conn = _db_conn()
        _cfg_row = _cfg_conn.execute('SELECT * FROM user_feishu_config WHERE user_id = ?', (user_id,)).fetchone()
        _cfg_conn.close()
        _fs_app_id = _cfg_row['app_id'] if _cfg_row and _cfg_row['app_id'] else FEISHU_BITABLE_APP_ID
        _fs_app_secret = _cfg_row['app_secret'] if _cfg_row and _cfg_row['app_secret'] else FEISHU_BITABLE_APP_SECRET
        _fs_app_token = _cfg_row['app_token'] if _cfg_row and _cfg_row['app_token'] else FEISHU_BITABLE_APP_TOKEN
        _fs_table_id = _cfg_row['table_id'] if _cfg_row and _cfg_row['table_id'] else FEISHU_BITABLE_TABLE_ID
        try:
            token = _feishu_get_tenant_access_token(_fs_app_id, _fs_app_secret)
            records = _feishu_list_all_records(token, _fs_app_token, _fs_table_id)
        except Exception as e:
            self._send_json_error(500, f'飞书API调用失败: {e}')
            return
        created = 0
        updated = 0
        skipped = 0
        error_list = []
        conn = _db_conn()
        try:
            for rec in records:
                try:
                    fields = rec.get('fields', {})
                    talent_data = _feishu_record_to_talent(fields)
                    if not talent_data['name']:
                        skipped += 1
                        continue
                    douyin_id = talent_data['douyin_id']
                    existing = None
                    if douyin_id:
                        existing = conn.execute(
                            "SELECT * FROM talents WHERE LOWER(douyin_id) = LOWER(?) LIMIT 1",
                            (douyin_id,)
                        ).fetchone()
                    if existing:
                        existing_dict = _talent_row_to_dict(existing)
                        existing_dict.update(talent_data)
                        existing_dict['id'] = existing['id']
                        row = _dict_to_talent_row(existing_dict)
                        conn.execute(
                            f"UPDATE talents SET {', '.join(f'{c} = ?' for c in _TALENT_COLUMNS)} WHERE id = ?",
                            tuple(row[c] for c in _TALENT_COLUMNS) + (existing['id'],)
                        )
                        updated += 1
                    else:
                        row = _dict_to_talent_row(talent_data)
                        if not row.get('created_by'):
                            row['created_by'] = auth.user_info.get('userId', '')
                        conn.execute(
                            f"INSERT INTO talents ({', '.join(_TALENT_COLUMNS)}) VALUES ({', '.join('?' * len(_TALENT_COLUMNS))})",
                            tuple(row[c] for c in _TALENT_COLUMNS)
                        )
                        created += 1
                except BaseException as be:
                    print('  [SyncProducts] EXCEPTION: ' + str(type(be).__name__) + ': ' + str(be), flush=True)
                    error_list.append({'record_id': str(rec.get('record_id', '')), 'error': str(be)})
            conn.commit()
            if error_list:
                print(f'  [SyncProducts] {len(error_list)} errors: {error_list}', flush=True)
        except Exception as e:
            conn.rollback()
            self._send_json_error(500, f'同步失败: {e}')
            return
        finally:
            conn.close()
        if error_list:
            print(f'  [SyncProducts] {len(error_list)} errors: {error_list}', flush=True)
        self._send_json(200, {
            'success': True,
            'total': len(records),
            'created': created,
            'updated': updated,
            'skipped': skipped,
            'errors': error_list
        })


    def _handle_sync_feishu_products(self):
        """POST /api/products/sync-feishu — 从飞书多维表格同步商品数据"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        user_id = auth.user_info.get('userId', '')
        _cfg_conn = _db_conn()
        _cfg_row = _cfg_conn.execute('SELECT * FROM user_feishu_config WHERE user_id = ?', (user_id,)).fetchone()
        _cfg_conn.close()
        _fs_app_id = _cfg_row['app_id'] if _cfg_row and _cfg_row['app_id'] else FEISHU_BITABLE_APP_ID
        _fs_app_secret = _cfg_row['app_secret'] if _cfg_row and _cfg_row['app_secret'] else FEISHU_BITABLE_APP_SECRET
        _fs_app_token = _cfg_row['app_token'] if _cfg_row and _cfg_row['app_token'] else FEISHU_BITABLE_APP_TOKEN
        _fs_product_table_id = ''
        if _cfg_row:
            try:
                _fs_product_table_id = _cfg_row['product_table_id'] or ''
            except (IndexError, KeyError):
                _fs_product_table_id = ''
        if not _fs_product_table_id:
            _fs_product_table_id = _cfg_row['table_id'] if _cfg_row and _cfg_row['table_id'] else FEISHU_BITABLE_TABLE_ID
        try:
            token = _feishu_get_tenant_access_token(_fs_app_id, _fs_app_secret)
            records = _feishu_list_all_records(token, _fs_app_token, _fs_product_table_id)
        except Exception as e:
            self._send_json_error(500, f'飞书API调用失败: {e}')
            return
        created = 0
        updated = 0
        skipped = 0
        error_list = []
        conn = _db_conn()
        try:
            for rec in records:
                try:
                    fields = rec.get('fields', {})
                    product_data = _feishu_record_to_product(fields)
                    if not product_data:
                        skipped += 1
                        continue
                    existing = None
                    if product_data.get('name'):
                        existing = conn.execute(
                            "SELECT * FROM products WHERE name = ? LIMIT 1",
                            (product_data['name'],)
                        ).fetchone()
                    if existing:
                        existing_dict = _product_row_to_dict(existing)
                        for k, v in product_data.items():
                            existing_dict[k] = v
                        existing_dict['id'] = existing['id']
                        row = _dict_to_product_row(existing_dict)
                        conn.execute(
                            f"UPDATE products SET {', '.join(f'{c} = ?' for c in _PRODUCT_COLUMNS)} WHERE id = ?",
                            tuple(row[c] for c in _PRODUCT_COLUMNS) + (existing['id'],)
                        )
                        updated += 1
                    else:
                        import time as _t
                        product_data['id'] = f"prod_{int(_t.time()*1000)}_{_t.time_ns()%1000000:06d}"
                        product_data['created_by'] = auth.user_info.get('userId', '')
                        product_data['created_at'] = int(_t.time() * 1000)
                        product_data['updated_at'] = int(_t.time() * 1000)
                        row = _dict_to_product_row(product_data)
                        conn.execute(
                            f"INSERT INTO products ({', '.join(_PRODUCT_COLUMNS)}) VALUES ({', '.join('?' * len(_PRODUCT_COLUMNS))})",
                            tuple(row[c] for c in _PRODUCT_COLUMNS)
                        )
                        created += 1
                except Exception as e:
                    error_list.append({'record_id': rec.get('record_id', ''), 'error': str(e)})
            conn.commit()
        except Exception as e:
            conn.rollback()
            self._send_json_error(500, f'同步失败: {e}')
            return
        finally:
            conn.close()
        self._send_json(200, {
            'success': True,
            'total': len(records),
            'created': created,
            'updated': updated,
            'skipped': skipped,
            'errors': error_list
        })


    def _handle_get_feishu_config(self):
        """GET /api/user/feishu-config — 获取当前用户的飞书多维表格配置"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        user_id = auth.user_info.get('userId', '')
        conn = _db_conn()
        try:
            row = conn.execute('SELECT app_id, app_token, table_id, updated_at FROM user_feishu_config WHERE user_id = ?', (user_id,)).fetchone()
        finally:
            conn.close()
        if row:
            self._send_json(200, {'configured': True, 'app_id': row['app_id'], 'app_token': row['app_token'], 'table_id': row['table_id'], 'product_table_id': row['product_table_id'] if 'product_table_id' in row.keys() else '', 'updated_at': row['updated_at']})
        else:
            self._send_json(200, {'configured': False, 'app_id': '', 'app_token': '', 'table_id': '', 'product_table_id': '', 'updated_at': 0})


    def _handle_save_feishu_config(self):
        """POST /api/user/feishu-config — 保存当前用户的飞书多维表格配置"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        data = json.loads(body) if isinstance(body, str) else body
        app_id = str(data.get('app_id', '')).strip()
        app_secret = str(data.get('app_secret', '')).strip()
        app_token = str(data.get('app_token', '')).strip()
        table_id = str(data.get('table_id', '')).strip()
        product_table_id = str(data.get('product_table_id', '')).strip()
        if not app_token or not table_id:
            self._send_json_error(400, 'app_token 和 table_id 不能为空')
            return
        user_id = auth.user_info.get('userId', '')
        now = int(time.time())
        conn = _db_conn()
        try:
            conn.execute('INSERT OR REPLACE INTO user_feishu_config (user_id, app_id, app_secret, app_token, table_id, product_table_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (user_id, app_id, app_secret, app_token, table_id, product_table_id, now))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'success': True, 'message': '飞书配置已保存'})


    def _handle_check_forbidden_words(self):
        """POST /api/forbidden-words/check — 违禁词检测"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Empty body')
            return
        text = body.get('text', '')
        if not text:
            self._send_json_error(400, 'Missing "text" field')
            return

        FORBIDDEN_WORDS = [
            '最', '第一', '顶级', '极品', '万能', '完美', '绝对', '100%', '百分百',
            '永久', '根治', '包治', '痊愈', '零风险', '无风险', '稳赚', '保本',
            '日入', '月入', '年入', '躺赚', '暴富', '一夜暴富',
            '微信', '支付宝', '加我', '私聊', '扫码', '二维码',
            '假货', '高仿', 'A货', '原单', '尾单',
            '最低价', '最便宜', '全网最低', '史上最低',
            '国家级', '世界级', '顶尖', '第一品牌',
        ]

        found = []
        text_lower = text.lower()
        for word in FORBIDDEN_WORDS:
            if word.lower() in text_lower:
                found.append(word)

        self._send_json(200, {
            'has_forbidden': len(found) > 0,
            'words': found,
            'count': len(found),
            'suggestion': '请替换以上违禁词后再发送' if found else '检测通过'
        })


    def _handle_get_product_matches(self, product_id):
        """GET /api/products/:id/matches — 获取商品的匹配达人列表
        优先读取商品自身的 matched_influencers，为空或超24小时则重新计算并缓存"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        data = self._load_influencers()
        influencer = next((i for i in data.get('influencers', []) if i.get('id') == inf_id), None)
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
        # 保存计算结果到达人（用于缓存；统一数据源后写入 SQLite talents 表，不再写 JSON）
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
        conn = _db_conn()
        try:
            conn.execute(
                'UPDATE talents SET matched_products = ?, matched_products_updated_at = ?, updated_at = ? WHERE id = ?',
                (json.dumps(cached_matches, ensure_ascii=False), now, now, inf_id))
            conn.commit()
        finally:
            conn.close()
        self._send_json(200, {'influencer_id': inf_id, 'matches': results[:limit], 'total': len(results), 'source': 'live'})

    def _handle_post_product(self):
        """POST /api/products — 录入商品（仅当 name+brand 完全一致时算重复）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        if not auth.is_admin:
            self._send_json_error(403, 'Only admin can create products')
            return
        # 角色硬拦截：AI 员工仅 role='运营'（或 admin）可录入商品，商务等其他角色 403
        role_guard = _check_agent_role_write_scope(auth, 'product')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
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
        if not row.get('created_by'):
            row['created_by'] = auth.user_info.get('userId', '')
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
        logger.info(f'  [Product] 录入商品: {product_out["name"]} ({product_out["id"]})')
        self._send_json(200, product_out)

    def _handle_put_product(self, product_id):
        """PUT /api/products/{id} — 更新商品（同时同步 SQLite 与 data/products.json；SQLite 不存在时回退到 JSON）"""
        logger.info(f'  [ProductPUT] 入口 product_id={product_id}')
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            logger.info(f'  [ProductPUT] 返回 401: 未认证 product_id={product_id}')
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'):
            logger.info(f'  [ProductPUT] 返回 403: 无 products 模块权限 product_id={product_id}')
            return
        # 角色硬拦截：AI 员工仅 role='运营'（或 admin）可修改商品
        role_guard = _check_agent_role_write_scope(auth, 'product')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        body = self._read_body()
        logger.info(f'  [ProductPUT] 请求体 product_id={product_id} body={repr(body)[:500]}')
        if not body:
            logger.info(f'  [ProductPUT] 返回 400: 请求体为空 product_id={product_id}')
            self._send_json_error(400, 'Missing body')
            return

        logger.info(f'  [ProductPUT] 查询SQLite前 product_id={product_id}')
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
            existing = _product_row_to_dict(row)
        finally:
            conn.close()
        logger.info(f'  [ProductPUT] 查询SQLite后 product_id={product_id} existing={bool(existing)}')

        if not existing:
            logger.info(f'  [ProductPUT] 返回 404: SQLite中未找到 product_id={product_id}')
            self._send_json_error(404, 'Product not found')
            return
        exists_in_sql = True

        now_ts = int(time.time() * 1000)
        updated = dict(existing)
        updated.update(body)
        # 确保 description 与 subtitle 双向同步
        if 'description' in body and 'subtitle' not in body:
            updated['subtitle'] = updated['description']
        if 'subtitle' in body and 'description' not in body:
            updated['description'] = updated['subtitle']
        updated['id'] = product_id
        updated['updatedAt'] = now_ts
        if not updated.get('createdAt'):
            updated['createdAt'] = now_ts
        # 兼容旧字段 commission_rate -> commission_rates
        if 'commission_rate' in body and 'commission_rates' not in body:
            updated['commission_rates'] = {'default': float(body['commission_rate'])}
        # 自动计算佣金金额（当 price 或 commission_rate 变更且未显式提供 commission_amount 时）
        if ('price' in body or 'commission_rate' in body) and 'commission_amount' not in body:
            price = float(updated.get('price', 0) or 0)
            rate = float(updated.get('commission_rate', 0) or 0)
            updated['commission_amount'] = round(price * rate / 100, 2)
        row = _dict_to_product_row(updated)
        row['created_at'] = updated.get('createdAt')
        row['updated_at'] = now_ts

        old_brand_id = existing.get('brand_id')
        logger.info(f'  [ProductPUT] 写入SQLite前 product_id={product_id} op={"UPDATE" if exists_in_sql else "INSERT"}')
        conn = _db_conn()
        try:
            _sync_product_brand(conn, row)
            if exists_in_sql:
                conn.execute(
                    f"UPDATE products SET {', '.join(f'{c} = ?' for c in _PRODUCT_COLUMNS)} WHERE id = ?",
                    tuple(row[c] for c in _PRODUCT_COLUMNS) + (product_id,)
                )
            else:
                conn.execute(
                    f"INSERT INTO products ({', '.join(_PRODUCT_COLUMNS)}) VALUES ({', '.join('?' * len(_PRODUCT_COLUMNS))})",
                    tuple(row[c] for c in _PRODUCT_COLUMNS)
                )
            conn.commit()
            for bid in {b for b in [old_brand_id, row.get('brand_id')] if b}:
                _update_brand_product_stats(conn, bid)
            conn.commit()
        finally:
            conn.close()
        logger.info(f'  [ProductPUT] 写入SQLite完成 product_id={product_id}')

        # 同步到 data/products.json
        json_data = _load_json_products()
        products = json_data.get('products', [])
        found = False
        for p in products:
            if p.get('id') == product_id:
                p.update(updated)
                found = True
                break
        if not found:
            products.append(updated)
        json_data['products'] = products
        json_data['total'] = len(products)
        _save_json_products(json_data)

        logger.info(f'  [ProductPUT] 返回 200 product_id={product_id} name={updated.get("name", "")}')
        self._send_json(200, updated)

    def _handle_delete_product(self, product_id):
        """DELETE /api/products/{id} — 删除商品（硬删除，符合常规 CRUD 语义）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'products'): return
        # 角色硬拦截：AI 员工仅 role='运营'（或 admin）可删除商品
        role_guard = _check_agent_role_write_scope(auth, 'product')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.info(f'  [Analyze] product_analyze AI response is not a valid JSON object: {content[:1000]}')
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            logger.debug(f'[DEBUG] GET /api/brands SQL: {sql} params={params}')
            rows = conn.execute(sql, params).fetchall()
            logger.debug(f'[DEBUG] GET /api/brands rows={len(rows)}')
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
            logger.error(f'[ERROR] GET /api/brands failed: {e}')
            import traceback
            traceback.print_exc()
            self._send_json(500, {'error': f'获取品牌列表失败: {str(e)}'})
        finally:
            conn.close()

    def _handle_get_brand(self, brand_id):
        """GET /api/brands/:id — 获取单个品牌"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
            talents = [_talent_row_to_dict(r) for r in rows]
        finally:
            conn.close()
        if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):
            # 两层架构可见性：仅自己子库（自己录入 + 自己的 AI 员工录入）。
            # 主库（created_by 为空）只有管理员可见；AI 员工本地调用同样按创建者子库过滤
            uid = _resolve_talent_owner_id(auth)
            visible_ids = {uid} | set(_get_user_emp_ids(uid))
            talents = [t for t in talents if (t.get('created_by') or '') in visible_ids]
        total = len(talents)
        talents = talents[offset:offset + limit]
        self._send_json(200, {'talents': talents, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_talent_categories(self):
        """GET /api/talents/categories — 返回 active 达人的去重类目列表（供规律归纳弹窗下拉）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        try:
            conn = _db_conn()
            try:
                rows = conn.execute(
                    "SELECT DISTINCT category FROM talents WHERE category != '' AND status = 'active' "
                    "ORDER BY category").fetchall()
            finally:
                conn.close()
            self._send_json(200, {'categories': [r['category'] for r in rows]})
        except Exception as e:
            logger.error(f'  [Talents] categories failed: {e}')
            self._send_json_error(500, f'Categories failed: {str(e)}')

    def _handle_get_talent_injection_text(self):
        """GET /api/talents/injection-text — 返回达人数据注入文本（含禁止编造约束）。

        供前端 sendViaOpenClaw 命中达人关键词时调用，把返回文本拼到消息体末尾，
        OpenClaw 直接从消息看到真实数据，不再依赖 SOUL.md 缓存。
        """
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        self._send_json(200, {'text': _build_talent_injection_text(auth)})

    def _handle_get_talent(self, talent_id):
        """GET /api/talents/:id — 获取达人详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        """POST /api/talents — 录入达人（douyin_id 完全一致时报重复；同用户同名时合并更新原记录）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 两层架构：录入对所有身份开放，AI 员工录入自动归属创建者子库（见下方 created_by 强制归属）
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可录入达人，运营等其他角色 403
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
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
        # 匿名 localhost 调用（无 X-Agent-Id / body agent_id）无法确定归属，拒绝写入，
        # 避免 created_by='localhost' 的脏数据（不匹配任何真实用户，子账号查不到）
        if _is_unidentified_localhost(auth):
            logger.warning('  [SubpoolGuard] 拒绝匿名 localhost 录入达人：缺少 X-Agent-Id，归属无法确定')
            self._send_json(403, {'error': '无法确定数据归属：AI 员工本地调用必须携带 X-Agent-Id 请求头（或请求体包含 agent_id）'})
            return
        # 两层架构：created_by 强制归属当前操作者（AI 员工录入归属其创建者子库），
        # 不允许请求体伪造 created_by（否则 agent 可传空值直接写进主库）
        row['created_by'] = _resolve_talent_owner_id(auth)
        conn = _db_conn()
        try:
            # 同一用户下已存在同名达人：更新原记录而非新建，防止重复创建
            same_name = conn.execute(
                'SELECT * FROM talents WHERE created_by = ? AND name = ? LIMIT 1',
                (row['created_by'], name)
            ).fetchone()
            if same_name:
                merged = _talent_row_to_dict(same_name)
                merged.update(body)
                merged['id'] = same_name['id']
                merged['created_by'] = same_name['created_by']
                merged['updated_at'] = int(time.time() * 1000)
                upd_row = _dict_to_talent_row(merged)
                conn.execute(
                    f"UPDATE talents SET {', '.join(f'{c} = ?' for c in _TALENT_COLUMNS)} WHERE id = ?",
                    tuple(upd_row[c] for c in _TALENT_COLUMNS) + (same_name['id'],)
                )
                conn.commit()
                if upd_row.get('group_id'):
                    _update_brand_product_stats(conn, upd_row['group_id'])
                    conn.commit()
                merged_out = conn.execute('SELECT * FROM talents WHERE id = ?', (same_name['id'],)).fetchone()
                result = _talent_row_to_dict(merged_out)
                result['merged'] = True
                result['message'] = f'已存在同名达人「{name}」，已更新原记录'
                self._send_json(200, result)
                return
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

    def _handle_promote_talent(self, talent_id):
        """POST /api/talents/:id/promote — 管理员把子库达人提升到主库。

        提升后 created_by 置空，收归主库由管理员统一管理（主库仅管理员可见）。
        仅限真实管理员前端操作，AI 员工本地调用无权提升。
        """
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):
            self._send_json(403, {'error': '仅管理员可提升达人到主库'})
            return
        conn = _db_conn()
        try:
            row = conn.execute('SELECT created_by, name FROM talents WHERE id = ?', (talent_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Talent not found')
                return
            conn.execute("UPDATE talents SET created_by = '', updated_at = ? WHERE id = ?",
                         (int(time.time() * 1000), talent_id))
            conn.commit()
            row_out = conn.execute('SELECT * FROM talents WHERE id = ?', (talent_id,)).fetchone()
        finally:
            conn.close()
        logger.info(f'  [Talent] 达人提升到主库: {row["name"]} ({talent_id})，原归属 {row["created_by"] or "主库"}')
        result = _talent_row_to_dict(row_out)
        result['promoted'] = True
        self._send_json(200, result)

    def _handle_put_talent(self, talent_id):
        """PUT /api/talents/:id — 更新达人"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可修改达人
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        # 两层架构：非管理员（含 AI 员工）只能操作自己子库的达人，主库仅管理员可动
        write_guard = _check_talent_write_permission(auth, talent_id)
        if write_guard:
            self._send_json(write_guard[1], {'error': write_guard[0]})
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        # 数值字段容错：'100-500' 区间取均值，无法解析的字段跳过（不更新），不返回 500
        body = _sanitize_talent_numeric_fields(body, talent_id)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可删除达人
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        # 两层架构：非管理员（含 AI 员工）只能操作自己子库的达人，主库仅管理员可动
        write_guard = _check_talent_write_permission(auth, talent_id)
        if write_guard:
            self._send_json(write_guard[1], {'error': write_guard[0]})
            return
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        """加载达人列表（legacy JSON 字段形状）。
        统一数据源后直查 SQLite talents 表并映射为 legacy 形状返回；
        data/influencers/*.json 仅为启动时导出的只读缓存，不再作为数据源。
        """
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM talents WHERE COALESCE(status, 'active') != 'archived'").fetchall()
        finally:
            conn.close()
        return {'version': '1.0',
                'influencers': [_talent_dict_to_influencer(_talent_row_to_dict(r)) for r in rows]}

    def _handle_get_influencers(self):
        """GET /api/influencers — 获取达人列表（支持 query 筛选）"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        # 两层架构可见性：仅自己子库（自己 + 自己的 AI 员工录入的）；
        # 主库（createdBy 为空）只有管理员可见；AI 员工本地调用同样按创建者子库过滤
        if not auth.is_admin or getattr(auth, 'localhost_agent_id', None):
            uid = _resolve_talent_owner_id(auth)
            ids = {uid} | set(_get_user_emp_ids(uid))
            influencers = [i for i in influencers if (i.get('createdBy') or '') in ids]
        if query.get('q'):
            kw = query['q'][0].lower()
            influencers = [i for i in influencers if kw in (i.get('id') or '').lower() or kw in (i.get('name') or '').lower() or kw in (i.get('accountId') or '').lower() or kw in (i.get('bio') or '').lower() or any(kw in t.lower() for t in (i.get('tags') or []))]
        offset = int(query.get('offset', [0])[0])
        limit = int(query.get('limit', [50])[0])
        total = len(influencers)
        influencers = influencers[offset:offset + limit]
        name_map = _user_display_name_map()
        for i in influencers:
            i['createdByName'] = name_map.get(i.get('createdBy'), '')
        self._send_json(200, {'influencers': influencers, 'total': total, 'offset': offset, 'limit': limit})

    def _handle_get_influencer(self, inf_id):
        """GET /api/influencers/:id — 获取单个达人详情"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 两层架构：录入对所有身份开放，AI 员工录入自动归属创建者子库（见下方 created_by 强制归属）
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可录入达人，运营等其他角色 403
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        body = self._read_body()
        if not body or 'name' not in body:
            self._send_json_error(400, 'Missing name')
            return
        # 统一数据源：录入只写 SQLite talents 表，JSON 文件不再作为写入目标
        now = int(time.time() * 1000)
        talent = _influencer_body_to_talent(body)
        talent['id'] = body.get('id') or f'inf_{now}_{uuid.uuid4().hex[:6]}'
        talent['status'] = 'active'
        # 匿名 localhost 调用（无 X-Agent-Id / body agent_id）无法确定归属，拒绝写入，
        # 避免 created_by='localhost' 的脏数据（不匹配任何真实用户，子账号查不到）
        if _is_unidentified_localhost(auth):
            logger.warning('  [SubpoolGuard] 拒绝匿名 localhost 录入达人：缺少 X-Agent-Id，归属无法确定')
            self._send_json(403, {'error': '无法确定数据归属：AI 员工本地调用必须携带 X-Agent-Id 请求头（或请求体包含 agent_id）'})
            return
        # 两层架构：created_by 强制归属当前操作者（AI 员工录入归属其创建者子库）
        talent['created_by'] = _resolve_talent_owner_id(auth)
        talent['created_at'] = now
        row = _dict_to_talent_row(talent)
        conn = _db_conn()
        try:
            _insert_talent_row(conn, row)
            conn.commit()
        finally:
            conn.close()
        influencer = _talent_dict_to_influencer(_talent_row_to_dict(row))
        logger.info(f'  [Influencer] 录入达人: {influencer["name"]} ({influencer["id"]})')
        self._send_json(200, influencer)

    def _handle_put_influencer(self, inf_id):
        """PUT /api/influencers/{id} — 更新达人"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可修改达人
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        # 两层架构：非管理员（含 AI 员工）只能操作自己子库的达人，主库仅管理员可动
        write_guard = _check_talent_write_permission(auth, inf_id)
        if write_guard:
            self._send_json(write_guard[1], {'error': write_guard[0]})
            return
        body = self._read_body()
        if not body:
            self._send_json_error(400, 'Missing body')
            return
        # 统一数据源：更新只写 SQLite talents 表（legacy 字段经映射落到对应列）
        field_map = {'name': 'name', 'avatar': 'avatar', 'platform': 'platform',
                     'accountId': 'douyin_id', 'followerCount': 'followers', 'category': 'category',
                     'tags': 'tags', 'bio': 'bio', 'contentStyle': 'content_style',
                     'cooperationPrice': 'average_price', 'priceUnit': 'price_unit', 'contact': 'contact',
                     'status': 'cooperation_status', 'engagementRate': 'video_interaction_rate',
                     'avgViews': 'avg_views', 'lastCooperation': 'last_cooperation', 'notes': 'notes'}
        conn = _db_conn()
        try:
            row = conn.execute('SELECT * FROM talents WHERE id = ?', (inf_id,)).fetchone()
            if not row:
                self._send_json_error(404, 'Influencer not found')
                return
            existing = _talent_row_to_dict(row)
            for legacy_field, talent_field in field_map.items():
                if legacy_field in body:
                    existing[talent_field] = body[legacy_field]
            existing['id'] = inf_id
            existing['updated_at'] = int(time.time() * 1000)
            new_row = _dict_to_talent_row(existing)
            conn.execute(
                f"UPDATE talents SET {', '.join(f'{c} = ?' for c in _TALENT_COLUMNS)} WHERE id = ?",
                tuple(new_row[c] for c in _TALENT_COLUMNS) + (inf_id,))
            conn.commit()
            row_out = conn.execute('SELECT * FROM talents WHERE id = ?', (inf_id,)).fetchone()
        finally:
            conn.close()
        self._send_json(200, _talent_dict_to_influencer(_talent_row_to_dict(row_out)))

    def _handle_delete_influencer(self, inf_id):
        """DELETE /api/influencers/{id} — 删除达人"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        if not self._require_module_permission(auth, 'influencers'): return
        # 角色硬拦截：AI 员工仅 role='商务'（或 admin）可删除达人
        role_guard = _check_agent_role_write_scope(auth, 'talent')
        if role_guard:
            self._send_json(role_guard[1], {'error': role_guard[0]})
            return
        # 两层架构：非管理员（含 AI 员工）只能操作自己子库的达人，主库仅管理员可动
        write_guard = _check_talent_write_permission(auth, inf_id)
        if write_guard:
            self._send_json(write_guard[1], {'error': write_guard[0]})
            return
        # 统一数据源：删除只操作 SQLite talents 表
        conn = _db_conn()
        try:
            cur = conn.execute('DELETE FROM talents WHERE id = ?', (inf_id,))
            conn.execute('DELETE FROM product_talent_match WHERE talent_id = ?', (inf_id,))
            conn.commit()
            removed = cur.rowcount
        finally:
            conn.close()
        self._send_json(200, {'deleted': removed > 0, 'id': inf_id})

    def _handle_search_influencers(self):
        """POST /api/influencers/search — 高级搜索/匹配"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        t_cat = (talent.get('category') or talent.get('fan_category') or '').strip()
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
            logger.error(f'  [AI Match] scoring failed: {e}')
            return {}

    def _handle_get_product_talents(self, product_id):
        """GET /api/products/:id/talents — 带该商品的Top达人排名"""
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
        auth = _authenticate(self.headers, self.client_address[0], self)
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
                logger.info(f'  [ChatFilter] {agent_id}: 过滤了 {original_len - len(messages)} 条群聊消息')

        # 引用消息展开：带 reply_to 的消息附带被引用消息摘要，被删/不存在时为 null
        id_map = {m.get('id'): m for m in messages if isinstance(m, dict) and m.get('id')}
        for m in messages:
            if isinstance(m, dict) and m.get('reply_to'):
                src = id_map.get(m['reply_to'])
                m['reply_to_message'] = ({
                    'id': src.get('id'),
                    'role': src.get('role'),
                    'content': src.get('content', ''),
                    'images': src.get('images') or [],
                    'created_at': src.get('timestamp') or src.get('created_at') or '',
                } if src else None)

        # 统计角色分布，便于排查 user 消息是否丢失
        role_counts = {}
        for m in messages:
            r = m.get('role', 'unknown')
            role_counts[r] = role_counts.get(r, 0) + 1
        logger.info(f'  [ChatGET] {agent_id} type={chat_type} 返回 {len(messages)} 条消息, 角色分布: {role_counts}')
        self._send_json(200, messages)

    def _handle_get_heavy_status(self, agent_id):
        """GET /api/chat/:agentId/heavy-status?jobId=xxx - 查询多图旁路任务状态"""
        auth = _authenticate(self.headers, self.client_address[0], self)
        if not auth.is_authenticated:
            self._send_auth_error(auth.error, auth.status)
            return
        _, err, status = self._check_agent_access(auth, agent_id)
        if err:
            self._send_json(status, {'error': err})
            return
        query = parse_qs(urlparse(self.path).query)
        job_id = query.get('jobId', [''])[0]
        job = _heavy_job_get(job_id)
        if not job or job.get('agent_id') != agent_id:
            self._send_json(404, {'error': '任务不存在'})
            return
        self._send_json(200, {'status': job['status'], 'error': job.get('error', ''), 'stage': job.get('stage', '')})

    def _handle_post_chat(self, agent_id):
        """POST /api/chat/:agentId"""
        logger.info(f'  [ChatPOST] 收到请求: {agent_id} path={self.path}')
        auth = _authenticate(self.headers, self.client_address[0], self)
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

        # 达人相关提问的处理（架构级防编造，不依赖 LLM 自觉）：
        # 命中关键词时后端直接查 SQLite talents 表（按当前用户权限过滤），
        # 以【系统数据】标签包裹的查询结果由后端生成——
        # - OpenClaw 员工（skipAI=True）：注入文本随响应返回（talentInjection），
        #   由前端拼进发给 OpenClaw 的用户消息末尾；
        # - 其他员工：skipAI=True 强制置 False，后端调 AI 时把系统数据拼到用户消息末尾。
        talent_injection = ''
        if role == 'user':
            _content_for_check = body.get('content', '')
            talent_injection = _build_talent_injection(_content_for_check, auth)
            _talent_hit = bool(talent_injection)
            _is_openclaw = bool(agent.get('openclawName') or agent.get('connectionType') == 'openclaw')
            if _talent_hit and not _is_openclaw and body.get('skipAI', False):
                body['skipAI'] = False
                logger.info(f'  [TalentInject] {agent_id} 命中达人关键词，skipAI 强制改为 False，走后端注入路径')

        # 积分管控（后端拦截）：用户消息且需要后端实际调用 AI 时，先检查员工积分余额
        # 积分不足直接返回，不转发给 OpenClaw / AI API
        credit_info = None
        if role == 'user' and not body.get('skipAI', False):
            balance, has_credits = _check_credit_balance(agent_id)
            credit_info = {'balance': balance, 'has_credits': has_credits}
            if not has_credits:
                self._send_json(429, {'error': '积分不足', 'balance': balance})
                return

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
        # 引用消息：记录被引用消息 id（回复/转发场景）
        _reply_to = body.get('reply_to') or body.get('replyTo')
        if _reply_to:
            msg['reply_to'] = _reply_to

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
                    logger.info(f'  [ChatArchive] {agent_id} 个人聊天归档 {archived_count} 条溢出消息到 L3')
                except Exception as e:
                    logger.error(f'  [ChatArchive] {agent_id} 归档失败: {e}，回退到静默截断')
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
                # 记忆提取场景不需要加载历史记录，避免 token 超限和干扰
                is_extract = '【记忆提取任务】' in content

                # 后端拦截自然语言自修改指令，不依赖 AI 输出 [SELF_UPDATE] 标记
                if role == 'user':
                    intent_updates = _detect_self_update_intent(content)
                    if intent_updates:
                        ok, su_msg, _ = _apply_agent_self_update(agent_id, intent_updates, source=f'chat:{auth.user_id}')
                        if ok:
                            field_name, new_value = intent_updates[0]
                            confirmation = f'（系统已根据你的指令更新了你的{field_name}为{new_value}，请在回复中确认已更新）'
                            content = confirmation + '\n\n' + content
                            msg['content'] = content
                            logger.info(f'  [ChatPOST] {agent_id} self-update intent applied: {field_name}={new_value}')
                        else:
                            logger.error(f'  [ChatPOST] {agent_id} self-update intent apply failed: {su_msg}')

                images = body.get('images', [])
                # 达人相关提问：把后端直查的【系统数据】拼到用户消息末尾，
                # LLM 只做分析和润色，不负责数据查询，从架构上杜绝编造
                if talent_injection:
                    content = content + talent_injection
                    logger.info(f'  [TalentInject] {agent_id} 命中达人关键词，系统数据拼入用户消息 len={len(talent_injection)}')
                if images:
                    user_payload = [{'type': 'text', 'text': content}]
                    for img in images:
                        user_payload.append({'type': 'image_url', 'image_url': {'url': img.get('base64', '')}})
                else:
                    user_payload = content
                allowed_cats = _allowed_knowledge_categories(auth)
                api_reply = _call_ai_api(
                    agent, user_payload, auth.user_info, include_history=not is_extract,
                    allowed_knowledge_categories=allowed_cats,
                    requester_id=auth.user_id, is_admin=auth.is_admin, team_ids=auth.team_ids,
                    group_ids=auth.group_ids
                )
                if api_reply:
                    logger.info(f'  [ChatPOST] {agent_id} api_reply_len={len(api_reply)} preview={repr(api_reply[:200])}')
                    # 解析并应用 AI 自修改标记，移除后保存到聊天记录
                    try:
                        self_updates, cleaned_reply = _parse_self_updates(api_reply)
                        logger.info(f'  [ChatPOST] {agent_id} self_updates={self_updates} cleaned_len={len(cleaned_reply)}')
                        if self_updates:
                            ok, su_msg, _ = _apply_agent_self_update(agent_id, self_updates, source=f'chat:{auth.user_id}')
                            logger.info(f'  [ChatPOST] {agent_id} apply_self_update ok={ok} msg={su_msg}')
                    except Exception as self_update_err:
                        logger.error(f'  [ChatPOST] {agent_id} self_update processing error: {self_update_err}')
                        import traceback
                        traceback.print_exc()
                        cleaned_reply = api_reply

                    if not cleaned_reply:
                        logger.info(f'  [ChatPOST] {agent_id} cleaned_reply is empty, falling back to original api_reply')
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
                    logger.info(f'  [ChatPOST] {agent_id} API代理 保存 {len(messages)} 条消息 ai_content_len={len(ai_message["content"])}')
                    # 分析结论自动入库（默认开启，settings.json auto_save_analysis: false 可关闭）
                    if not is_extract:
                        _maybe_auto_save_analysis(agent_id, cleaned_reply, content)
                    # 记录项目组对话到 group_messages（供同组其他 AI 感知团队动态；记忆提取任务不记录）
                    if not is_extract:
                        try:
                            agent_group_id = _get_agent_group_id(agent_id)
                            if agent_group_id:
                                _record_group_message(agent_group_id, agent_id, 'user', content)
                                _record_group_message(agent_group_id, agent_id, 'assistant', ai_message['content'])
                        except Exception as feed_err:
                            logger.error(f'  [TeamFeed] {agent_id} 记录失败: {feed_err}')
                    # 推送 AI 回复通知（受用户 message_notify 开关控制）
                    _push_notification(
                        auth.user_id, 'message',
                        f'{agent.get("name", agent_id)} 回复了你',
                        (cleaned_reply or '')[:200],
                        agent_id
                    )
                    resp_data = {'userMessage': msg, 'aiMessage': ai_message, 'archived': archived_count}
                    if credit_info:
                        resp_data['credit'] = credit_info
                    self._send_json(200, resp_data)
                    return

            # OpenClaw 或其他
            _save_chat(agent_id, messages)
            logger.info(f'  [ChatPOST] {agent_id} role={role} skipAI={skip_ai} 保存后共 {len(messages)} 条消息')
            # OpenClaw 链路：AI 回复由前端回传（role=assistant），同样做分析结论自动入库
            # 注意：最终回复可能只是"建档成功"，真正的分析在 tool_result 里，
            # 收集当前 turn（assistant 之前连续 role=tool 的消息）的 content 一并检测
            if role == 'assistant':
                tool_results = []
                for m in reversed(messages[:-1]):
                    if m.get('role') == 'tool':
                        tool_results.append(m.get('content', ''))
                    elif m.get('role') == 'user':
                        break
                tool_results.reverse()
                _maybe_auto_save_analysis(agent_id, msg.get('content', ''), tool_results=tool_results)

        # 多图重任务旁路：>=2 张图片的 OpenClaw 消息不进入 gateway（重活会把调度队列堵死），
        # 由后端 Python 线程完成 vision+分析后落库；前端凭 heavyPipe+jobId 轮询结果，
        # 失败时回落 OpenClaw 主干道重发。立即返回占位提示并落库，让用户知道系统在干活。
        if _should_heavy_bypass(role, images, agent):
            job_id = _heavy_job_create(agent_id)
            threading.Thread(
                target=_heavy_pipe_worker,
                args=(job_id, agent, body.get('content', ''), images, auth.user_id),
                daemon=True, name=f'HeavyPipe-{job_id}',
            ).start()
            placeholder_msg = {
                'id': 'msg_' + uuid.uuid4().hex[:8],
                'role': 'assistant',
                'content': f'正在分析 {len(images)} 张截图数据，预计需要 3-5 分钟，完成后会发送完整分析报告。',
                'timestamp': datetime.now().isoformat(),
                'heavyPipePlaceholder': True,
            }
            with _get_chat_lock(agent_id):
                messages = _load_chat(agent_id)
                if not isinstance(messages, list):
                    messages = []
                messages.append(placeholder_msg)
                _save_chat(agent_id, messages)
            logger.info(f'  [HeavyPipe] {job_id} 已旁路: {agent_id} images={len(images)}')
            self._send_json(200, {
                'userMessage': msg, 'aiMessage': placeholder_msg,
                'heavyPipe': True, 'jobId': job_id,
            })
            return

        if connection_type == 'openclaw':
            self._send_json(200, {
                'userMessage': msg,
                'hint': '请通过 WebSocket 连接获取 AI 回复',
                'talentInjection': talent_injection
            })
        else:
            self._send_json(200, {'userMessage': msg, 'talentInjection': talent_injection})

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
        'minimax': 'https://api.minimax.chat/v1/text',
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
        'minimax': 'MiniMax-Text-01',
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

    # kimi-for-coding / kimicode 只接受 temperature=1，其他模型保持 0.8
    temperature = 1 if (resolved_model == 'kimi-for-coding' or api_provider == 'kimicode') else 0.8

    # kimicode（kimi-for-coding）走 Anthropic Messages API（/messages），非 OpenAI chat/completions 格式
    if api_provider == 'kimicode':
        return _call_kimicode_messages(base_url, resolved_model, api_key, messages, timeout, max_tokens)

    req_body = json.dumps({
        'model': resolved_model,
        'messages': messages,
        'temperature': temperature,
        'max_tokens': max_tokens,
        'stream': False
    }).encode('utf-8')

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'Content-Length': str(len(req_body))
    }

    masked_key = f'{api_key[:4]}...' if api_key and len(api_key) > 4 else '(none)'
    logger.info(f'  [API] chat completion request: provider={api_provider} model={resolved_model} url={target_url} key={masked_key}')
    try:
        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        status = resp.status
        raw = resp.read().decode('utf-8', errors='replace')
        logger.info(f'  [API] chat completion response: HTTP {status}')
        resp_data = json.loads(raw)
        if resp_data.get('choices') and resp_data['choices'][0].get('message'):
            return resp_data['choices'][0]['message'].get('content', '')
        logger.info(f'  [API] chat completion unexpected format: {raw[:500]}')
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        logger.error(f'  ❌ AI API call failed: HTTP {e.code} {e.reason}')
        logger.info(f'      Provider: {api_provider}, Model: {resolved_model}, URL: {target_url}')
        logger.error(f'      Request body preview: {req_body[:500].decode("utf-8", errors="replace")}')
        logger.error(f'      Response: {error_body}')
    except Exception as e:
        logger.error(f'  ❌ AI API call failed: {e}')
        traceback.print_exc()
    return None


def _call_kimicode_messages(base_url, model, api_key, messages, timeout=300, max_tokens=2000):
    """kimicode（kimi-for-coding）的 Anthropic Messages API 调用，返回字符串内容或 None。
    POST {base_url}/messages，认证用 x-api-key + anthropic-version（不用 Authorization Bearer）；
    不传 temperature；system 消息转为顶层 system 字段；响应从 content 数组的 text 块取文本。"""
    system_parts = []
    chat_messages = []
    for m in messages or []:
        if m.get('role') == 'system':
            if m.get('content'):
                system_parts.append(m['content'])
        else:
            chat_messages.append({'role': m.get('role', 'user'), 'content': m.get('content', '')})
    body = {'model': model, 'max_tokens': max_tokens, 'messages': chat_messages}
    if system_parts:
        body['system'] = '\n\n'.join(system_parts)
    req_body = json.dumps(body).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'x-api-key': api_key,
        'anthropic-version': '2023-06-01',
        'Content-Length': str(len(req_body)),
    }
    target_url = base_url + '/messages'
    masked_key = f'{api_key[:4]}...' if api_key and len(api_key) > 4 else '(none)'
    logger.info(f'  [API] kimicode messages request: model={model} url={target_url} key={masked_key}')
    try:
        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raw = resp.read().decode('utf-8', errors='replace')
        logger.info(f'  [API] kimicode messages response: HTTP {resp.status}')
        resp_data = json.loads(raw)
        thinking_text = ''
        for block in resp_data.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'text' and block.get('text'):
                return block['text']
            if isinstance(block, dict) and block.get('type') == 'thinking' and block.get('thinking'):
                thinking_text = block['thinking']
        if thinking_text:
            logger.info('  [API] kimicode messages 无 text 块，兜底返回 thinking 内容')
            return thinking_text
        logger.info(f'  [API] kimicode messages unexpected format: {raw[:500]}')
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        logger.error(f'  ❌ kimicode messages call failed: HTTP {e.code} {e.reason}')
        logger.error(f'      Response: {error_body}')
    except Exception as e:
        logger.error(f'  ❌ kimicode messages call failed: {e}')
        traceback.print_exc()
    return None


def _call_minimax_messages(base_url, model, api_key, messages, timeout=300, max_tokens=4096):
    """MiniMax chat completion API 调用（OpenAI 兼容格式）。
    POST {base_url}/chatcompletion_v2，认证用 Authorization: Bearer。
    响应取 choices[0].message.content。"""
    body = {'model': model, 'messages': messages, 'max_tokens': max_tokens, 'stream': False}
    req_body = json.dumps(body).encode('utf-8')
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'Content-Length': str(len(req_body)),
    }
    target_url = base_url + '/chatcompletion_v2'
    masked_key = f'{api_key[:8]}...' if api_key and len(api_key) > 8 else '(none)'
    logger.info(f'  [API] minimax request: model={model} url={target_url} key={masked_key}')
    try:
        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        raw = resp.read().decode('utf-8', errors='replace')
        logger.info(f'  [API] minimax response: HTTP {resp.status}')
        resp_data = json.loads(raw)
        if resp_data.get('choices') and resp_data['choices'][0].get('message'):
            return resp_data['choices'][0]['message'].get('content', '')
        logger.info(f'  [API] minimax unexpected format: {raw[:500]}')
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8', errors='replace')
        logger.error(f'  ❌ minimax call failed: HTTP {e.code} {e.reason}')
        logger.error(f'      Response: {error_body}')
    except Exception as e:
        logger.error(f'  ❌ minimax call failed: {e}')
        traceback.print_exc()
    return None


# ═══ Provider 降级计数器 + 多 provider 降级调用 ═══
# 当某个 provider 连续失败 _PROVIDER_DEGRADED_THRESHOLD 次后，降级调用会跳过该 provider，
# 避免每个请求都白白等超时。成功一次后自动重置计数。
_PROVIDER_FAIL_COUNTS = {}
_PROVIDER_DEGRADED_THRESHOLD = 3


def _mark_provider_failed(provider_name):
    _PROVIDER_FAIL_COUNTS[provider_name] = _PROVIDER_FAIL_COUNTS.get(provider_name, 0) + 1
    count = _PROVIDER_FAIL_COUNTS[provider_name]
    if count >= _PROVIDER_DEGRADED_THRESHOLD:
        logger.warning(f'  [Fallback] Provider "{provider_name}" degraded after {count} consecutive failures, skipping')


def _is_provider_degraded(provider_name):
    return _PROVIDER_FAIL_COUNTS.get(provider_name, 0) >= _PROVIDER_DEGRADED_THRESHOLD


def _mark_provider_ok(provider_name):
    if provider_name in _PROVIDER_FAIL_COUNTS:
        del _PROVIDER_FAIL_COUNTS[provider_name]


def _call_chat_completion_with_fallback(messages, timeout=PROXY_TIMEOUT, max_tokens=2000):
    """多 provider 降级调用：按 settings.json 中 providers 数组的 priority 顺序尝试。
    优先调用 priority 最小的；该 provider 失败则按 priority 递增依次降级；
    已降级（连续失败 >= 阈值）的 provider 直接跳过。任一 provider 成功即返回其内容；全部失败返回 None。
    兼容旧单 provider 格式（无 providers 数组，只有 apiKey/baseUrl/model 字段）。"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
    except Exception:
        settings = {}
    llm = settings.get('llm') or {}

    # 构建 provider 列表（兼容旧单 provider 格式）
    providers_to_try = []
    if isinstance(llm.get('providers'), list) and llm['providers']:
        providers_to_try = sorted(llm['providers'], key=lambda p: p.get('priority', 99))
    elif llm.get('apiKey'):
        providers_to_try = [{
            'name': llm.get('provider', 'default'),
            'apiKey': llm['apiKey'],
            'baseUrl': llm.get('baseUrl', ''),
            'model': llm.get('model', ''),
            'priority': 1,
        }]

    if not providers_to_try:
        logger.info('  [Fallback] settings.json llm 无可用 provider 配置，跳过降级')
        return None

    for p in providers_to_try:
        name = p.get('name', 'unknown')
        if _is_provider_degraded(name):
            logger.info(f'  [Fallback] skip degraded provider: {name}')
            continue
        api_key = (p.get('apiKey', '') or '').strip()
        base_url = (p.get('baseUrl', '') or '').strip()
        model = p.get('model', '') or ''
        if not api_key or not base_url:
            logger.info(f'  [Fallback] provider {name} 缺 apiKey/baseUrl，跳过')
            continue
        logger.info(f'  [Fallback] trying provider: {name} (priority={p.get("priority", 99)})')
        try:
            if name == 'minimax':
                content = _call_minimax_messages(base_url, model, api_key, messages, timeout=timeout, max_tokens=max_tokens)
            elif name == 'kimicode':
                content = _call_kimicode_messages(base_url, model, api_key, messages, timeout=timeout, max_tokens=max_tokens)
            else:
                # 其他 provider 走通用 OpenAI chat/completions 路径
                req_body = json.dumps({
                    'model': model, 'messages': messages, 'max_tokens': max_tokens, 'stream': False,
                }).encode('utf-8')
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {api_key}',
                    'Content-Length': str(len(req_body)),
                }
                masked_key = f'{api_key[:8]}...' if api_key and len(api_key) > 8 else '(none)'
                logger.info(f'  [API] {name} request: model={model} url={base_url}/chat/completions key={masked_key}')
                req = urllib.request.Request(base_url + '/chat/completions', data=req_body, headers=headers, method='POST')
                ctx = ssl.create_default_context()
                resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
                raw = resp.read().decode('utf-8', errors='replace')
                resp_data = json.loads(raw)
                content = resp_data['choices'][0]['message'].get('content', '') if resp_data.get('choices') else None
        except Exception as e:
            logger.error(f'  ❌ {name} call raised: {e}')
            content = None
        if content:
            _mark_provider_ok(name)
            logger.info(f'  ✅ [Fallback] provider {name} succeeded')
            return content
        _mark_provider_failed(name)
        logger.info(f'  ⚠️ [Fallback] provider {name} failed, trying next')

    logger.info('  [Fallback] all providers failed')
    return None


def _try_minimax_proxy_fallback(body_json, log_prefix='Proxy', request_format='anthropic'):
    """代理转发失败时（典型 403）尝试 minimax 兜底。
    输入：body_json 是 Anthropic Messages 或 OpenAI chat/completions 格式的 dict。
    request_format: 'anthropic' 或 'openai'，决定返回的响应格式（与前端原始请求一致）。
    返回：minimax 响应已转为对应格式的 JSON 字节流；失败返回 None。
    不修改输入。调 minimax 失败会更新 _PROVIDER_FAIL_COUNTS，连续失败 3 次后跳过。"""
    if not isinstance(body_json, dict):
        return None
    if _is_provider_degraded('minimax'):
        logger.info(f'  [{log_prefix}Fallback] minimax 已 degraded，跳过')
        return None

    # 读 settings.json 拿 minimax 配置
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        llm = settings.get('llm') or {}
        minimax_cfg = None
        for p in (llm.get('providers') or []):
            if isinstance(p, dict) and p.get('name') == 'minimax':
                minimax_cfg = p
                break
        if not minimax_cfg:
            logger.info(f'  [{log_prefix}Fallback] settings.json 无 minimax 配置，跳过')
            return None
        api_key = (minimax_cfg.get('apiKey', '') or '').strip()
        base_url = (minimax_cfg.get('baseUrl', '') or '').strip()
        model = minimax_cfg.get('model', '') or 'MiniMax-Text-01'
        if not api_key or not base_url:
            logger.info(f'  [{log_prefix}Fallback] minimax 配置缺 apiKey/baseUrl，跳过')
            return None
    except Exception as e:
        logger.error(f'  [{log_prefix}Fallback] 读 settings.json 失败: {e}')
        return None

    # 把请求归一为 OpenAI chat/completions 消息列表（minimax 后端吃 OpenAI 格式）
    # 输入 body_json 可能是 OpenAI 或 Anthropic 格式（_handle_proxy 在请求侧已可能做过转换）
    try:
        oai_messages = []
        # 判定输入 body 是 Anthropic 还是 OpenAI：顶层有 'system' 字段或 'max_tokens' 视为 Anthropic
        input_is_anthropic = isinstance(body_json, dict) and (
            'system' in body_json or 'max_tokens' in body_json
        )
        if input_is_anthropic:
            system_text = body_json.get('system', '')
            if isinstance(system_text, str) and system_text:
                oai_messages.append({'role': 'system', 'content': system_text})
            elif isinstance(system_text, list):
                sys_texts = [b.get('text', '') for b in system_text if isinstance(b, dict) and b.get('type') == 'text']
                if sys_texts:
                    oai_messages.append({'role': 'system', 'content': '\n'.join(sys_texts)})
            for msg in body_json.get('messages', []) or []:
                if not isinstance(msg, dict):
                    continue
                role = msg.get('role')
                if role == 'system':
                    continue
                content = msg.get('content')
                if isinstance(content, str):
                    oai_messages.append({'role': role, 'content': content})
                elif isinstance(content, list):
                    texts = []
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            texts.append(item.get('text', ''))
                    if texts:
                        oai_messages.append({'role': role, 'content': '\n'.join(texts)})
        else:
            # OpenAI chat/completions: system 在 messages 第一个，user/assistant 顺序
            for msg in body_json.get('messages', []) or []:
                if not isinstance(msg, dict):
                    continue
                role = msg.get('role')
                content = msg.get('content')
                if isinstance(content, str):
                    oai_messages.append({'role': role, 'content': content})
                elif isinstance(content, list):
                    texts = [item.get('text', '') for item in content
                             if isinstance(item, dict) and item.get('type') == 'text']
                    if texts:
                        oai_messages.append({'role': role, 'content': '\n'.join(texts)})
        if not oai_messages:
            logger.info(f'  [{log_prefix}Fallback] 没有可转换的文本消息，跳过')
            return None

        minimax_body = {
            'model': model,
            'messages': oai_messages,
            'max_tokens': body_json.get('max_tokens', 4096),
            'stream': False,
        }
        req_body = json.dumps(minimax_body, ensure_ascii=False).encode('utf-8')
        target_url = base_url + '/chatcompletion_v2'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'Content-Length': str(len(req_body)),
        }
        masked_key = f'{api_key[:8]}...' if api_key and len(api_key) > 8 else '(none)'
        logger.info(f'  [{log_prefix}Fallback] 上游失败 → 尝试 minimax 兜底: model={model} url={target_url} key={masked_key} request_format={request_format}')

        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
        raw = resp.read().decode('utf-8', errors='replace')
        logger.info(f'  [{log_prefix}Fallback] minimax response: HTTP {resp.status}')
        resp_data = json.loads(raw)
        if not resp_data.get('choices') or not resp_data['choices'][0].get('message'):
            logger.error(f'  [{log_prefix}Fallback] minimax unexpected format: {raw[:500]}')
            _mark_provider_failed('minimax')
            return None
        content_text = resp_data['choices'][0]['message'].get('content', '')
        if not content_text:
            _mark_provider_failed('minimax')
            return None

        # 按 request_format 构造响应
        if request_format == 'openai':
            # OpenAI chat.completion 格式
            usage_raw = resp_data.get('usage') or {}
            prompt_tokens = usage_raw.get('prompt_tokens') or usage_raw.get('input_tokens') or 0
            completion_tokens = usage_raw.get('completion_tokens') or usage_raw.get('output_tokens') or 0
            total_tokens = usage_raw.get('total_tokens') or (prompt_tokens + completion_tokens)
            openai_resp = {
                'id': resp_data.get('id', f'minimax_{int(time.time() * 1000)}'),
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': model,
                'choices': [{
                    'index': 0,
                    'message': {'role': 'assistant', 'content': content_text},
                    'finish_reason': 'stop',
                }],
                'usage': {
                    'prompt_tokens': prompt_tokens,
                    'completion_tokens': completion_tokens,
                    'total_tokens': total_tokens,
                },
            }
            _mark_provider_ok('minimax')
            logger.info(f'  ✅ [{log_prefix}Fallback] minimax 兜底成功(OpenAI格式), content_len={len(content_text)}')
            return json.dumps(openai_resp, ensure_ascii=False).encode('utf-8')
        else:
            # Anthropic Messages 格式（保持向后兼容）
            anthropic_resp = {
                'id': resp_data.get('id', f'minimax_{int(time.time() * 1000)}'),
                'type': 'message',
                'role': 'assistant',
                'content': [{'type': 'text', 'text': content_text}],
                'model': model,
                'stop_reason': 'end_turn',
                'usage': resp_data.get('usage', {'input_tokens': 0, 'output_tokens': 0}),
            }
            _mark_provider_ok('minimax')
            logger.info(f'  ✅ [{log_prefix}Fallback] minimax 兜底成功(Anthropic格式), content_len={len(content_text)}')
            return json.dumps(anthropic_resp, ensure_ascii=False).encode('utf-8')
    except urllib.error.HTTPError as e:
        try:
            err_body_text = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            err_body_text = ''
        logger.error(f'  [{log_prefix}Fallback] minimax HTTP {e.code}: {err_body_text}')
        _mark_provider_failed('minimax')
    except Exception as e:
        logger.error(f'  [{log_prefix}Fallback] minimax 调用异常: {e}')
        _mark_provider_failed('minimax')
    return None


# ═══ Kimi 多模态图片识别（图片转文字，供 OpenClaw 纯文本链路使用）═══
# key 解析与 _handle_proxy_kimi 一致：员工 apiKey > settings.json vision.apiKey > KIMI_KEY_POOL 轮询
KIMI_VISION_URL = 'https://api.kimi.com/coding/v1/messages'
KIMI_VISION_MODEL = 'kimi-for-coding'


def _get_settings_vision_config():
    """读取 settings.json 中的 vision 配置（管理后台可配）。
    返回 {'apiKey','model','baseUrl'}；占位符/空值自动忽略。"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        vision = settings.get('vision', {}) or {}
        api_key = (vision.get('apiKey', '') or '').strip()
        # 占位符（非 sk- 开头）视为未配置
        if api_key and not api_key.startswith('sk-'):
            api_key = ''
        return {
            'apiKey': api_key,
            'model': (vision.get('model', '') or '').strip(),
            'baseUrl': (vision.get('baseUrl', '') or '').strip(),
        }
    except Exception:
        return {'apiKey': '', 'model': '', 'baseUrl': ''}


def _call_kimi_vision(image_base64, agent_id=None, role=None):
    """调用 Kimi Code（Anthropic Messages）端点将图片转成文字描述；成功返回描述文字，失败返回 None。
    认证方式与 _handle_proxy_kimi 一致：x-api-key + anthropic-version，直连 api.kimi.com；
    key 来源：KIMI_KEY_POOL 轮询；401/429 时拉黑当前 key 并取池内下一个重建请求重试，
    重试上限为池大小，全部失败返回 None（403 仍走 minimax 视觉降级）。
    agent_id 参数仅为兼容调用方保留，不再参与 key 选择。
    model/baseUrl 同样优先取 settings.json vision 配置。
    image_base64 可传完整 data URL（data:image/...;base64,...）或纯 base64 串。
    role 为该 AI 员工的角色：role == '商务' 时 system 提示词替换为 BUSINESS_VISION_PROMPT，
    其他 role 沿用原有通用提示词（不加 system 字段）。"""
    if not image_base64:
        return None
    vision_cfg = _get_settings_vision_config()
    current_key = KIMI_KEY_POOL.get_key()
    if not current_key:
        logger.error('  [Vision] Kimi Key 池已空，无可用 key，跳过图片识别')
        return None
    vision_model = vision_cfg['model'] or KIMI_VISION_MODEL
    vision_url = _resolve_kimi_coding_target_url('kimi')
    media_type = 'image/jpeg'
    data = image_base64
    if image_base64.startswith('data:'):
        try:
            header, data = image_base64.split(',', 1)
            media_type = header.split(';')[0].split(':')[1]
        except Exception:
            logger.error('  [Vision] data URL 解析失败')
            return None
    body = {
        'model': vision_model,
        'max_tokens': 1024,
        # 关闭 extended thinking，强制模型只输出 text 块，不输出 thinking 块
        'thinking': {'type': 'disabled'},
        'messages': [{
            'role': 'user',
            'content': [
                {'type': 'image', 'source': {'type': 'base64', 'media_type': media_type, 'data': data}},
                {'type': 'text', 'text': '请描述这张图片的内容'}
            ]
        }]
    }
    if role == '商务':
        body['system'] = BUSINESS_VISION_PROMPT
    req_body = json.dumps(body).encode('utf-8')
    ctx = ssl.create_default_context()
    key_retry_count = 0
    while True:
        headers = {
            'Content-Type': 'application/json',
            'x-api-key': current_key,
            'anthropic-version': '2023-06-01',
            'Content-Length': str(len(req_body))
        }
        try:
            req = urllib.request.Request(vision_url, data=req_body, headers=headers, method='POST')
            resp = urllib.request.urlopen(req, timeout=60, context=ctx)
            resp_data = json.loads(resp.read().decode('utf-8', errors='replace'))
            # content 数组可能以 thinking 思考块开头（无 text 字段）：跳过 thinking 块，
            # 取第一个 type 为 text 的块；若全部为 thinking 块，则拼接所有 thinking 内容降级输出
            thinking_parts = []
            for block in resp_data.get('content') or []:
                if not isinstance(block, dict):
                    continue
                if block.get('type') == 'text' and block.get('text'):
                    logger.info(f'  [Vision] 图片描述成功 len={len(block["text"])}')
                    return block['text']
                if block.get('type') == 'thinking' and block.get('thinking'):
                    thinking_parts.append(block['thinking'])
            if thinking_parts:
                fallback = '\n'.join(thinking_parts)
                logger.info(f'  [Vision] 响应无 text 块，降级返回 thinking 内容 len={len(fallback)}')
                return fallback
            logger.error(f'  [Vision] 响应格式异常: {str(resp_data)[:300]}')
            return None
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')
            # key 日志脱敏：只显示前10后4位
            _masked = (current_key[:10] + '...' + current_key[-4:]) if len(current_key) > 14 else '****'
            logger.error(f'  [Vision] Kimi vision 调用失败: HTTP {e.code} key={_masked} {err_body[:300]}')
            # 401/429：当前 key 失效或被限流，拉黑后取池内下一个 key 重试，上限为池大小
            if e.code in (401, 429) and key_retry_count < KIMI_KEY_POOL.size:
                key_retry_count += 1
                KIMI_KEY_POOL.mark_failed(current_key)
                next_key = KIMI_KEY_POOL.get_key()
                if next_key is None:
                    logger.error('  [Vision] Key 池已空，放弃重试')
                    return None
                current_key = next_key
                logger.info(f'  [Vision] {e.code} 轮换 key 重试 ({key_retry_count}/{KIMI_KEY_POOL.size})')
                continue
            # 403 时尝试 minimax 视觉降级（用 minimax 自身的多模态 chat/completions 能力）
            if e.code == 403:
                try:
                    fb_text = _call_minimax_vision_fallback(image_base64, media_type, role)
                    if fb_text:
                        return fb_text
                except Exception as fb_err:
                    logger.error(f'  [Vision] minimax vision 降级异常: {fb_err}')
            return None
        except Exception as e:
            logger.error(f'  [Vision] Kimi vision 调用异常: {e}')
            return None


def _call_minimax_vision_fallback(image_base64, media_type='image/jpeg', role=None):
    """Kimi vision 403 时的 minimax 降级。复用 minimax 的 OpenAI 兼容 chat/completions
    多模态能力（image_url），失败返回 None。连续失败 3 次后 provider 走 degraded 跳过。"""
    if _is_provider_degraded('minimax'):
        logger.info('  [Vision] minimax 已 degraded，跳过 vision 降级')
        return None

    # 读 minimax 配置
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        llm = settings.get('llm') or {}
        minimax_cfg = None
        for p in (llm.get('providers') or []):
            if isinstance(p, dict) and p.get('name') == 'minimax':
                minimax_cfg = p
                break
        if not minimax_cfg:
            logger.info('  [Vision] settings.json 无 minimax 配置，跳过 vision 降级')
            return None
        api_key = (minimax_cfg.get('apiKey', '') or '').strip()
        base_url = (minimax_cfg.get('baseUrl', '') or '').strip()
        model = minimax_cfg.get('model', '') or 'MiniMax-Text-01'
        if not api_key or not base_url:
            logger.info('  [Vision] minimax 配置缺 apiKey/baseUrl，跳过 vision 降级')
            return None
    except Exception as e:
        logger.error(f'  [Vision] 读 settings.json 失败: {e}')
        return None

    # 处理 data URL → 纯 base64
    data = image_base64
    if image_base64.startswith('data:'):
        try:
            header, data = image_base64.split(',', 1)
            media_type = header.split(';')[0].split(':')[1] or media_type
        except Exception:
            logger.error('  [Vision] minimax 降级 data URL 解析失败')
            return None

    image_data_url = f'data:{media_type};base64,{data}'

    # 构造 OpenAI 多模态 messages
    messages = []
    if role == '商务':
        messages.append({'role': 'system', 'content': BUSINESS_VISION_PROMPT})
    messages.append({
        'role': 'user',
        'content': [
            {'type': 'image_url', 'image_url': {'url': image_data_url}},
            {'type': 'text', 'text': '请描述这张图片的内容'}
        ]
    })

    body = {
        'model': model,
        'messages': messages,
        'max_tokens': 1024,
        'stream': False
    }

    try:
        req_body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        target_url = base_url + '/chatcompletion_v2'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'Content-Length': str(len(req_body))
        }
        masked_key = f'{api_key[:8]}...' if api_key and len(api_key) > 8 else '(none)'
        logger.info(f'  [Vision] Kimi 403 → 尝试 minimax vision 降级: model={model} url={target_url} key={masked_key} role={role or "(default)"}')

        req = urllib.request.Request(target_url, data=req_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=60, context=ctx)
        resp_data = json.loads(resp.read().decode('utf-8', errors='replace'))
        if not resp_data.get('choices') or not resp_data['choices'][0].get('message'):
            logger.error(f'  [Vision] minimax vision 降级失败: 响应格式异常 {str(resp_data)[:300]}')
            _mark_provider_failed('minimax')
            return None
        text = (resp_data['choices'][0].get('message') or {}).get('content', '') or ''
        if not text:
            logger.error('  [Vision] minimax vision 降级失败: 响应 content 为空')
            _mark_provider_failed('minimax')
            return None
        _mark_provider_ok('minimax')
        logger.info(f'  [Vision] minimax vision 降级成功: text_len={len(text)}')
        return text
    except urllib.error.HTTPError as e:
        try:
            err_text = e.read().decode('utf-8', errors='replace')[:300]
        except Exception:
            err_text = ''
        logger.error(f'  [Vision] minimax vision 降级失败: HTTP {e.code} {err_text}')
        _mark_provider_failed('minimax')
    except Exception as e:
        logger.error(f'  [Vision] minimax vision 降级失败: {e}')
        _mark_provider_failed('minimax')
    return None


# ═══ 多图重任务旁路管道（HeavyPipe）═══════════════════════════════════
# 背景：OpenClaw gateway 是单 Node 进程，多图（>=3 张）重分析会把它的调度队列堵死
# （health 还活着但消息不再 dispatch）。多图消息在 POST /api/chat 处旁路：
# 后端 Python 线程串行完成 vision 识别 → 达人名提取 → 达人库预查 → 单次 Kimi 深度分析，
# 落库后由前端轮询 heavy-status 取结果；任何阶段失败置 failed，前端降级回 OpenClaw 主干道。

_HEAVY_JOBS = {}
_heavy_jobs_lock = threading.Lock()
_HEAVY_JOB_MAX = 100     # 任务注册表保留上限（超出淘汰最旧的）
_HEAVY_IMAGE_MIN = 2     # 触发旁路的最小图片数
_HEAVY_IMAGE_MAX = 9     # 单条消息图片上限（与前端发图上限一致）

_HEAVY_TALENT_EXTRACT_PROMPT = '从以下达人数据截图识别结果中提取所有出现的达人名称/抖音昵称，只输出名称列表每行一个，无则输出无'


def _heavy_job_create(agent_id):
    job_id = 'heavy_' + uuid.uuid4().hex[:8]
    with _heavy_jobs_lock:
        if len(_HEAVY_JOBS) >= _HEAVY_JOB_MAX:
            oldest = min(_HEAVY_JOBS.items(), key=lambda kv: kv[1].get('created_at', 0))[0]
            _HEAVY_JOBS.pop(oldest, None)
        _HEAVY_JOBS[job_id] = {
            'agent_id': agent_id,
            'status': 'analyzing',
            'error': '',
            'created_at': int(time.time() * 1000),
        }
    return job_id


def _heavy_job_set(job_id, **fields):
    with _heavy_jobs_lock:
        job = _HEAVY_JOBS.get(job_id)
        if job is not None:
            job.update(fields)


def _heavy_job_get(job_id):
    with _heavy_jobs_lock:
        job = _HEAVY_JOBS.get(job_id)
        return dict(job) if job else None


def _heavy_minimax_fallback(system_prompt, user_text, max_tokens):
    """Kimi 全部失败后的降级通道：读 settings.json 的 minimax provider 配置，
    走 _call_minimax_messages（OpenAI 兼容格式）。返回与 _heavy_kimi_call 同构的 dict，失败返回 None。"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        llm = settings.get('llm') or {}
        minimax_cfg = None
        for p in (llm.get('providers') or []):
            if isinstance(p, dict) and p.get('name') == 'minimax':
                minimax_cfg = p
                break
        if not minimax_cfg:
            logger.warning('  [HeavyPipe] settings.json 无 minimax 配置，无法降级')
            return None
        api_key = (minimax_cfg.get('apiKey', '') or '').strip()
        base_url = (minimax_cfg.get('baseUrl', '') or '').strip()
        model = minimax_cfg.get('model', '') or 'MiniMax-Text-01'
        if not api_key or not base_url:
            logger.warning('  [HeavyPipe] minimax 配置缺 apiKey/baseUrl，无法降级')
            return None
    except Exception as e:
        logger.error(f'  [HeavyPipe] 读 minimax 配置失败: {e}')
        return None
    messages = [
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_text},
    ]
    content = _call_minimax_messages(base_url, model, api_key, messages,
                                     timeout=180, max_tokens=max_tokens)
    if not content:
        return None
    return {
        'text': content,
        'stop_reason': None,
        'input_tokens': None,
        'output_tokens': None,
        'content_types': ['text'],
        'provider': 'minimax',
    }


def _heavy_llm_call(system_prompt, user_text, max_tokens=4096):
    """HeavyPipe LLM 调用入口：先走 Kimi（key 池轮询重试），全部失败或返回空 text
    （thinking 块吃光额度 / content 为空）时自动降级 MiniMax。"""
    result = _heavy_kimi_call(system_prompt, user_text, max_tokens)
    if result is not None and (result.get('text') or '').strip():
        return result
    if result is not None:
        logger.warning(f'  [HeavyPipe] Kimi 返回空 text（stop_reason={result.get("stop_reason")} '
                       f'content_types={result.get("content_types")}），降级到 MiniMax')
    else:
        logger.warning('  [HeavyPipe] Kimi 全部失败，降级到 MiniMax')
    return _heavy_minimax_fallback(system_prompt, user_text, max_tokens)


def _heavy_kimi_call(system_prompt, user_text, max_tokens=4096):
    """内部单次 Kimi 调用（anthropic messages 格式）。key 走 KIMI_KEY_POOL 轮询，
    401/429 拉黑换 key 重试（上限=池大小）；全部失败返回 None。
    默认禁用 extended thinking（thinking 块会吃掉 max_tokens 导致 text 为空），
    若 API 返回 400 不认 thinking 参数则去掉后重试一次。
    成功时返回 dict：{'text', 'stop_reason', 'input_tokens', 'output_tokens', 'content_types'}。"""
    req_payload = {
        'model': 'kimi-for-coding',
        'max_tokens': max_tokens,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': user_text}],
        'thinking': {'type': 'disabled'},
    }
    req_body = json.dumps(req_payload, ensure_ascii=False).encode('utf-8')
    current_key = KIMI_KEY_POOL.get_key()
    if not current_key:
        logger.error('  [HeavyPipe] Key 池已空，无法调用 LLM')
        return None
    key_retry_count = 0
    while True:
        req = urllib.request.Request(
            KIMI_PROXY_REAL_BASE_URL + '/v1/messages',
            data=req_body,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': current_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST')
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode('utf-8', errors='replace'))
            usage = data.get('usage') or {}
            text_val = ''.join(p.get('text', '') for p in data.get('content', [])
                               if isinstance(p, dict) and p.get('type') == 'text')
            logger.info(f'  [HeavyPipe] kimi_call 返回 text_len={len(text_val)} stop_reason={data.get("stop_reason")}')
            logger.info(f'  [HeavyPipe] stage4_meta: stop_reason={data.get("stop_reason")} '
                        f'content_types={[p.get("type") for p in data.get("content", [])]} '
                        f'usage={data.get("usage")}')
            return {
                'text': text_val,
                'stop_reason': data.get('stop_reason'),
                'input_tokens': usage.get('input_tokens'),
                'output_tokens': usage.get('output_tokens'),
                'content_types': [p.get('type') for p in data.get('content', [])
                                  if isinstance(p, dict)],
            }
        except urllib.error.HTTPError as e:
            err_text = e.read().decode('utf-8', errors='replace')[:300]
            _masked = (current_key[:10] + '...' + current_key[-4:]) if len(current_key) > 14 else '****'
            logger.error(f'  [HeavyPipe] Kimi 调用失败: HTTP {e.code} key={_masked} {err_text}')
            if e.code == 400 and 'thinking' in err_text.lower() and req_payload.pop('thinking', None):
                req_body = json.dumps(req_payload, ensure_ascii=False).encode('utf-8')
                logger.warning('  [HeavyPipe] API 不支持 thinking 参数，去掉后重试一次')
                continue
            if e.code in (401, 429) and key_retry_count < KIMI_KEY_POOL.size:
                key_retry_count += 1
                KIMI_KEY_POOL.mark_failed(current_key)
                next_key = KIMI_KEY_POOL.get_key()
                if next_key is None:
                    logger.error('  [HeavyPipe] Key 池已空，放弃重试')
                    return None
                current_key = next_key
                logger.info(f'  [HeavyPipe] {e.code} 轮换 key 重试 ({key_retry_count}/{KIMI_KEY_POOL.size})')
                continue
            return None
        except Exception as e:
            logger.error(f'  [HeavyPipe] Kimi 调用异常: {type(e).__name__}: {e}')
            return None


def _resolve_agent_soul(agent):
    """读取员工灵魂全文：优先 OpenClaw workspace 的 SOUL.md（与 _handle_get_agent_docs 一致），
    文件不存在时回落 agents.json 的 soulDoc/systemPrompt。"""
    openclaw_name = (agent.get('openclawName') or '').strip()
    if openclaw_name:
        soul_path = os.path.join(os.path.expanduser('~/.openclaw/workspace-' + openclaw_name), 'SOUL.md')
        try:
            if os.path.isfile(soul_path):
                with open(soul_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                if content:
                    return content
        except Exception as e:
            logger.warning(f'  [HeavyPipe] 读取 workspace SOUL.md 失败: {e}')
    return (agent.get('soulDoc') or agent.get('systemPrompt') or '').strip()


def _heavy_fetch_talents(names):
    """按达人名批量预查达人库（与 GET /api/talents?q= 同款 SQL：名称模糊匹配、粉丝数倒序取第一）。"""
    talents = []
    if not names:
        return talents
    conn = _db_conn()
    try:
        for name in names[:10]:  # 最多查 10 个，防止 prompt 膨胀
            row = conn.execute(
                "SELECT * FROM talents WHERE status='active' AND LOWER(name) LIKE ? "
                "ORDER BY followers DESC LIMIT 1",
                (f'%{name.lower()}%',)
            ).fetchone()
            if row:
                talents.append(_talent_row_to_dict(row))
    finally:
        conn.close()
    return talents


def _should_heavy_bypass(role, images, agent):
    """多图旁路触发条件：用户消息 + 图片数 >= _HEAVY_IMAGE_MIN + OpenClaw 链路员工"""
    return (role == 'user'
            and len(images or []) >= _HEAVY_IMAGE_MIN
            and bool(agent.get('openclawName') or agent.get('connectionType') == 'openclaw'))


def _heavy_pipe_worker(job_id, agent, user_content, images, user_id):
    """多图重分析管道（后台 daemon 线程）。任何阶段失败都把 job 置 failed，
    由前端降级回 OpenClaw 主干道重发。"""
    agent_id = agent.get('id', '')
    agent_name = agent.get('name', agent_id)
    t0 = time.perf_counter()

    def _stage(label, since):
        logger.info(f'  [HeavyPipe] {job_id} {label}: {time.perf_counter() - since:.1f}s')

    try:
        # Stage 1：逐张 vision 识别（走 KIMI_KEY_POOL）
        _t = time.perf_counter()
        vision_texts = []
        _img_total = min(len(images), _HEAVY_IMAGE_MAX)
        for idx, img in enumerate(images[:_HEAVY_IMAGE_MAX], 1):
            _heavy_job_set(job_id, stage=f'正在识别截图（{idx}/{_img_total}）…')
            b64 = img.get('base64', '') if isinstance(img, dict) else str(img)
            desc = _call_kimi_vision(b64, agent_id=agent_id, role=agent.get('role'))
            if desc:
                logger.info(f'  [HeavyPipe] {job_id} 图片{idx}识别结果（{len(desc)}字）: {desc}')
                vision_texts.append(f'【图片{idx}识别结果】\n{desc}')
            else:
                logger.warning(f'  [HeavyPipe] {job_id} 第 {idx} 张图片识别失败，跳过')
        _stage(f'stage1 vision（{len(vision_texts)}/{len(images)} 张成功）', _t)
        if not vision_texts:
            raise RuntimeError('vision 识别全部失败')

        # Stage 2：提取达人名（单次 LLM 调用）
        _heavy_job_set(job_id, stage='正在提取达人名称…')
        _t = time.perf_counter()
        vision_all = '\n\n'.join(vision_texts)
        logger.info(f'  [HeavyPipe] {job_id} vision 内容预览（前500字）: {vision_all[:500]}')
        names_result = _heavy_llm_call(_HEAVY_TALENT_EXTRACT_PROMPT, vision_all, max_tokens=1024)
        names_text = names_result.get('text', '') if isinstance(names_result, dict) else (names_result or '')
        if isinstance(names_result, dict) and names_result.get('stop_reason') not in (None, 'end_turn', 'stop_sequence'):
            logger.warning(f'  [HeavyPipe] {job_id} stage2 达人名提取可能被截断: '
                           f'stop_reason={names_result.get("stop_reason")} '
                           f'output_tokens={names_result.get("output_tokens")}')
        talent_names = [ln.strip() for ln in names_text.splitlines()
                        if ln.strip() and ln.strip() != '无']
        _stage(f'stage2 达人名提取（{talent_names}）', _t)

        # Stage 3：达人库预查
        _heavy_job_set(job_id, stage='正在检索达人数据…')
        _t = time.perf_counter()
        talents = _heavy_fetch_talents(talent_names)
        _stage(f'stage3 达人预查（命中 {len(talents)}/{len(talent_names)}）', _t)

        # Stage 4：单次 Kimi 深度分析（灵魂人格 + vision 全文 + 达人详情 + 用户原始指令）
        _heavy_job_set(job_id, stage='正在深度分析…')
        _t = time.perf_counter()
        soul = _resolve_agent_soul(agent)
        system_prompt = (
            f'你是 {agent_name}，一个 {agent.get("role", "助手")}。请用第一人称回复，保持角色一致性。'
            + ('\n\n' + soul if soul else '')
            + '\n\n## 分析输出要求（必须严格遵守，缺一项就是不合格）\n'
              '1. 达人评级：开头必须给出明确评级（A/B/C 或 推荐/观望/放弃），附一句话理由\n'
              '2. 核心结论：一句话概括这个达人的商业价值定位\n'
              '3. 基本面：粉丝量级、增长趋势、带货方式、经验值，用表格呈现\n'
              '4. 带货结构：类目集中度、价格带分布、代表品牌/商品，必须指出哪类商品出单率最高\n'
              '5. 流量效率：视频 vs 直播的播放、互动、转化率对比，判断是爆款驱动型还是稳定型\n'
              '6. 人群画像：粉丝画像与短视频观众画像分开，必须指出差异点和营销启示\n'
              '7. 选品建议：必须具体到品类+价格带+风格方向，举例适合推的品（如"30-80元基础款女装"），不要说"女性向商品"这种泛话\n'
              '8. 合作策略：给出试水方案建议（建议投几条、预估单条GMV区间、风险对冲方式）\n'
              '9. 风险提示：最多3条关键风险，每条附应对策略\n'
              '10. 末尾给出明确的下一步建议（如"建议录入档案，优先级B，适合XX类品测试"），不要问老板"要不要录入"\n'
              '\n'
              '关键：你是商务专家，输出的是合作决策建议，不是数据搬运。每个结论都要有判断，不是描述数据就完事。\n'
              '这是一次性分析任务，你没有搜索工具可用。必须根据截图识别结果和达人数据，直接输出完整分析报告。'
              '禁止说"我先查一下"或"让我看看"等执行意图的话，直接输出分析结论。'
        )
        user_parts = []
        if talents:
            user_parts.append('【系统预查到达人信息-以下数据已由系统自动查到，无需再执行搜索，直接使用】\n'
                              + json.dumps(talents, ensure_ascii=False, indent=1))
        user_parts.append('【达人数据截图识别结果】\n' + vision_all)
        user_parts.append('【用户原始指令】\n' + (user_content or ''))
        result = _heavy_llm_call(system_prompt, '\n\n'.join(user_parts), max_tokens=8192)
        _stage('stage4 深度分析', _t)
        logger.info(f'  [HeavyPipe] {job_id} stage4 result: {repr(result)[:500]}')
        if isinstance(result, dict):
            logger.info(f'  [HeavyPipe] {job_id} stage4 stop_reason={result.get("stop_reason")} '
                        f'input_tokens={result.get("input_tokens")} '
                        f'output_tokens={result.get("output_tokens")} '
                        f'reply_len={len(result.get("text") or "")}')
            if result.get('stop_reason') in ('length', 'max_tokens'):
                raise RuntimeError(f'stage4 输出被截断（stop_reason={result.get("stop_reason")}），降级回 OpenClaw 主干道')
            if not (result.get('text') or '').strip() and 'thinking' in (result.get('content_types') or []):
                raise RuntimeError('stage4 模型只返回 thinking 块没有 text，降级回 OpenClaw 主干道')
        reply = result.get('text', '') if isinstance(result, dict) else (result or '')
        if not reply:
            raise RuntimeError('Kimi 深度分析调用失败')

        # Stage 5：落库（等价 skipAI=True 直接保存，不再触发 AI）+ 通知
        _heavy_job_set(job_id, stage='正在保存分析结果…')
        _t = time.perf_counter()
        ai_message = {
            'id': 'msg_' + uuid.uuid4().hex[:8],
            'role': 'assistant',
            'content': reply,
            'timestamp': datetime.now().isoformat(),
            'heavyPipe': True,
        }
        with _get_chat_lock(agent_id):
            messages = _load_chat(agent_id)
            if not isinstance(messages, list):
                messages = []
            messages.append(ai_message)
            _save_chat(agent_id, messages)
        _maybe_auto_save_analysis(agent_id, reply, user_content or '')
        try:
            agent_group_id = _get_agent_group_id(agent_id)
            if agent_group_id:
                _record_group_message(agent_group_id, agent_id, 'user', user_content or '')
                _record_group_message(agent_group_id, agent_id, 'assistant', reply)
        except Exception as feed_err:
            logger.error(f'  [HeavyPipe] {job_id} TeamFeed 记录失败: {feed_err}')
        _push_notification(user_id, 'message', f'{agent_name} 的图像分析已完成', (reply or '')[:200], agent_id)
        _stage('stage5 落库+通知', _t)

        # Stage 6：记忆沉淀（复用 memory pipeline L0，失败不阻断）
        _t = time.perf_counter()
        try:
            conn = _db_conn()
            try:
                memory_pipeline.save_conversation(
                    conn, agent_id, session_id=agent_id,
                    turn_id=int(time.time()),
                    user_content=user_content or '', assistant_content=reply)
            finally:
                conn.close()
        except Exception as mem_err:
            logger.error(f'  [HeavyPipe] {job_id} 记忆沉淀失败（不阻断）: {mem_err}')
        _stage('stage6 记忆沉淀', _t)

        _heavy_job_set(job_id, status='done')
        logger.info(f'  [HeavyPipe] {job_id} 完成，总耗时 {time.perf_counter() - t0:.1f}s')
    except Exception as e:
        logger.error(f'  [HeavyPipe] {job_id} 降级原因: {type(e).__name__}: {e}（前端将回落 OpenClaw 主干道）')
        _heavy_job_set(job_id, status='failed', error=str(e))


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

_SELF_UPDATE_INTENT_FIELD_MAP = {
    '描述': 'description',
    '简介': 'description',
    '角色': 'role',
    '行为指令': 'system_prompt',
}

_SELF_UPDATE_INTENT_PATTERNS = [
    _re.compile(r'(?:把|将)\s*你的\s*(描述|简介|角色|行为指令)\s*(?:改成|改为|改[为成])\s*([^\n。！？；,.]+)'),
    _re.compile(r'修改\s*你的\s*(描述|简介|角色|行为指令)\s*为\s*([^\n。！？；,.]+)'),
]


def _detect_self_update_intent(text):
    """检测用户消息中是否包含修改自身配置的自然语言指令，返回 (field, value) 列表"""
    if not text or not isinstance(text, str):
        return []
    text = text.strip()
    for pat in _SELF_UPDATE_INTENT_PATTERNS:
        m = pat.search(text)
        if m:
            cn_field = m.group(1).strip()
            value = m.group(2).strip()
            field = _SELF_UPDATE_INTENT_FIELD_MAP.get(cn_field)
            if field and value:
                return [(field, value)]
    return []


# 达人分析类员工的强制数据源约束（防止编造不存在的达人数据）
_INFLUENCER_DATA_CONSTRAINT = (
    '\n\n【数据源强制约束】\n'
    '1. 达人数据只能从 /api/influencers 接口读取，禁止编造不存在的达人\n'
    '2. 如果 API 返回空列表，必须如实告知用户「当前没有达人数据」，不能编造\n'
    '3. 分析报告中提到的每个达人必须能在 API 返回的数据中找到对应记录\n'
    '4. 禁止使用知识库或本地缓存中的旧数据替代 API 实时数据'
)

# 商务角色员工创建时自动追加的达人数据源约束（防止编造不存在的达人数据）
_BUSINESS_DATA_CONSTRAINT = (
    '\n\n【数据源强制约束】\n'
    '- 达人数据只能从系统 API 实时获取，禁止编造不存在的达人\n'
    '- 如果 API 返回空数据，必须如实告知用户「当前没有达人数据」，不能编造\n'
    '- 分析报告中提到的每个达人必须能在 API 返回的数据中找到对应记录'
)

# 角色 systemPrompt 预设模板：创建员工时按 role 自动套用为默认值（用户仍可自定义编辑）
# 每个模板末尾含该角色的通用数据约束段落
_ANTI_FABRICATION_RULES = (
    '\n\n【工具使用铁律】\n'
    '- 你没有录入达人的记忆能力，所有数据必须通过调用工具写入系统，严禁口头回复"已录入"而不实际调用工具\n'
    '- 如果你没有某个工具权限，必须如实告知用户，严禁假装已完成'
)

_ROLE_SYSTEM_PROMPT_TEMPLATES = {
    # 商务 = 达人侧：负责达人录入与达人带货能力分析，商品只读
    '商务': (
        '你是一名商务专员，核心职责是达人开发、达人录入与合作跟进。\n\n'
        '【核心职责】\n'
        '- 根据客户提供的达人信息，负责达人录入（调用达人录入接口，必须携带 X-Agent-Id 请求头）\n'
        '- 分析达人带货能力（粉丝画像、流量结构、历史带货数据），评估合作价值\n'
        '- 跟进达人合作进度，记录合作状态与跟进记录\n'
        '- 输出明确的合作结论和判断依据，有风险点主动说明\n\n'
        '【权限边界】\n'
        '- 达人数据可录入和维护，由你负责\n'
        '- 商品数据只读分析，不可录入或修改商品\n\n'
        '【数据源强制约束】\n'
        '达人/商品数据只能从系统 API 实时获取，禁止编造。API 返回空数据时如实告知用户。'
    ),
    # 运营 = 商品侧：负责商品录入与商品-达人匹配分析，达人只读
    '运营': (
        '你是一名运营专员，核心职责是商品管理与商品-达人匹配分析。\n\n'
        '【核心职责】\n'
        '- 根据管理员提供的达人数据截图和商品数据截图，分析商品是否适合推广\n'
        '- 对照系统达人库，找出适合带该商品的达人，评估匹配度\n'
        '- 分析达人卖得好的商品数据，判断我们是否需要跟进推广\n'
        '- 负责商品录入（调用商品录入接口，必须携带 X-Agent-Id 请求头）\n'
        '- 输出明确的商品推广结论和判断依据，有风险点主动说明\n\n'
        '【权限边界】\n'
        '- 达人数据只读分析，不可录入或修改达人\n'
        '- 商品数据可录入，由管理员授权管理\n\n'
        '【数据源强制约束】\n'
        '商品/达人数据只能从系统 API 实时获取，禁止编造。API 返回空数据时如实告知用户。'
    ),
    '助理': (
        '你是一名助理，负责日程管理、信息整理与工作汇报。\n\n'
        '【数据源强制约束】\n'
        '所有业务数据必须从系统 API 实时获取，禁止编造。'
    ),
}


def _append_influencer_data_constraint(agent, system_prompt):
    """达人分析相关员工（role 含 达人/数据分析/analyst）的 systemPrompt 末尾自动追加数据源约束"""
    role = (agent.get('role', '') or '').lower()
    if '达人' in role or '数据分析' in role or 'analyst' in role:
        if '【数据源强制约束】' not in system_prompt:
            return system_prompt + _INFLUENCER_DATA_CONSTRAINT
    return system_prompt


def _build_agent_tools_doc(agent_id):
    """生成 AI 员工的 TOOLS.md（OpenClaw 工具配置）：所有 curl 命令硬编码 X-Agent-Id。

    创建/注册 agent 时自动写入 workspace，不依赖 LLM 自己记得带 header——
    达人录入等写入接口缺 X-Agent-Id 会被服务端 SubpoolGuard 拒绝（归属无法确定）。
    """
    base = f'http://localhost:{PORT}'
    return f'''# 工具能力（SoloBrave 本地 API）

用 exec 工具执行 curl 调用本地 API。**所有请求必须携带 X-Agent-Id 请求头**（已固定为你的员工 ID，照抄即可，不要改成别的值）：
- Content-Type: application/json
- X-Agent-Id: {agent_id}

## 达人录入（收到达人资料必须实际调用，严禁口头回复"已录入"）
curl -s -X POST {base}/api/talents \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"name":"达人昵称","douyin_id":"抖音号","followers":500000,"category":"美妆"}}'

兼容旧接口（等价）：POST {base}/api/influencers，同样必须带 X-Agent-Id。

## 数据查询
- 达人列表: curl -s {base}/api/talents -H "X-Agent-Id: {agent_id}"
- 商品列表: curl -s {base}/api/products -H "X-Agent-Id: {agent_id}"
- 项目组列表: curl -s {base}/api/groups -H "X-Agent-Id: {agent_id}"

## 知识库
先查分类：
curl -s {base}/api/knowledge/categories -H "X-Agent-Id: {agent_id}"

再存储：
curl -s -X POST {base}/api/knowledge/entries \\
  -H "Content-Type: application/json" \\
  -H "X-Agent-Id: {agent_id}" \\
  -d '{{"title":"标题","content":"内容...","categoryId":1}}'

## 使用规则
1. 所有请求必须携带 X-Agent-Id: {agent_id}，缺了会被服务端拒绝（归属无法确定）
2. 数据写入必须实际调用 API，成功后才可回复"已录入"；调用失败要如实告知用户
3. 查询结果以 API 返回为准，禁止编造不存在的数据
'''


# 达人相关提问的关键词（任一命中即触发实时数据注入）
_TALENT_INJECT_KEYWORDS = ('达人', '网红', 'KOL', '主播', '带货', '分析', '报告')
_TALENT_INJECT_LIMIT = 50


def _build_talent_injection(user_text, auth):
    """用户消息命中达人相关关键词时返回达人数据注入文本，否则返回 ''"""
    if not user_text or not isinstance(user_text, str):
        return ''
    if not any(k in user_text.upper() for k in _TALENT_INJECT_KEYWORDS):
        return ''
    return _build_talent_injection_text(auth)


def _build_talent_injection_text(auth):
    """查询当前登录用户的达人数据并格式化为注入文本（供消息内联注入，含禁止编造约束）。
    数据隔离规则：管理员（is_admin）看全部，非管理员只看自己（created_by=user_id）录入的。
    """
    try:
        conn = _db_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM talents WHERE status = 'active' ORDER BY followers DESC LIMIT ?",
                (_TALENT_INJECT_LIMIT,)).fetchall()
            talents = [_talent_row_to_dict(r) for r in rows]
        finally:
            conn.close()
        if not auth.is_admin:
            # 两层架构：仅自己子库（自己录入的）；主库（created_by 为空）只有管理员可见
            uid = auth.user_info.get('userId', '')
            talents = [t for t in talents if (t.get('created_by') or '') == uid]
    except Exception as e:
        logger.error(
            f'  [TalentInject] 查询达人数据失败: err_type={type(e).__name__} err={e}\n'
            f'{traceback.format_exc()}')
        return ''
    if not talents:
        return ('\n\n【系统数据 - 达人数据查询结果】\n'
                '系统查询结果：当前没有任何达人数据。你必须如实告知用户暂无数据。\n'
                '严禁编造任何达人姓名、平台、粉丝数等信息。如果你编造了达人数据，将导致严重后果。\n\n')
    lines = ['\n\n【系统数据 - 达人数据查询结果】',
             '以下是系统查询的真实达人数据，你必须基于这些数据分析和回复。禁止编造任何不在列表中的达人。']
    for t in talents:
        price = t.get('single_video_settlement') or t.get('video_avg_price') or t.get('average_price') or '-'
        rate = t.get('video_interaction_rate') or '-'
        status = t.get('cooperation_status') or t.get('status') or '-'
        lines.append(f"{t.get('name') or '-'} | 抖音 | 粉丝:{t.get('followers') or 0} | 报价:{price} | 互动率:{rate} | 状态:{status}")
    lines.append('你必须仅使用以上达人数据回复，不得编造上述列表之外的任何达人。')
    return '\n'.join(lines) + '\n\n'


# ═══ 知识事件检索注入（分析档案召回）═══
# user_text 命中以下关键词时才执行检索（在 _TALENT_INJECT_KEYWORDS 基础上扩展）
_KE_INJECT_KEYWORDS = _TALENT_INJECT_KEYWORDS + ('匹配', '推荐', '选品', '商品', '合作', '评估')
_KE_CONTEXT_MAX_LEN = 2000      # 注入文本总长度上限（含历史规律参考段）
_KE_EVENT_SNIPPET_LEN = 200     # 每条事件 content_full 截取长度
_KE_SAME_ENTITY_LIMIT = 3       # 同实体历史取最近 N 条
_KE_SAME_CATEGORY_LIMIT = 2     # 同类目其他达人分析取最近 N 条
_KE_SEMANTIC_LIMIT = 3          # embedding 语义兜底取 top N
_KE_FTS_ENABLED = True          # FTS5 可用标志（init_db 探测失败时置 False，FTS 路直接跳过）
# 阶段4B-P0 可调参：混合检索最终打分权重 final = 0.6*hybrid_norm + 0.25*(importance/10) + 0.15*recency
_KE_W_HYBRID = 0.6              # 三路 RRF 融合分（批内归一化）权重
_KE_W_IMPORTANCE = 0.25         # 事件重要度（importance_score/10）权重
_KE_W_RECENCY = 0.15            # 新鲜度（30 天线性衰减）权重
_KE_RRF_K = 60                  # RRF 平滑常数
_KE_RECENCY_WINDOW_MS = 30 * 24 * 3600 * 1000  # 新鲜度线性衰减窗口（30 天，毫秒）


def _extract_entities_from_text(text):
    """提取文本中提到的所有达人/商品实体。
    返回 [(entity_type, entity_id, name, category), ...]，名称最长优先；
    已被更长名称覆盖的短名跳过（如文本含"赵西瓜"时不再匹配"赵西"）。"""
    if not text or not text.strip():
        return []
    try:
        conn = _db_conn()
        try:
            talents = conn.execute(
                "SELECT id, name, category FROM talents WHERE status='active'").fetchall()
            products = conn.execute(
                "SELECT id, name FROM products WHERE status='active'").fetchall()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f'  [KnowledgeInject] 实体提取查询失败: {e}')
        return []
    found = []
    for row in talents:
        name = (row['name'] or '').strip()
        if name and name in text:
            found.append(('talent', row['id'], name, row['category'] or ''))
    for row in products:
        name = (row['name'] or '').strip()
        if name and name in text:
            found.append(('product', row['id'], name, ''))
    found.sort(key=lambda x: len(x[2]), reverse=True)
    result = []
    for item in found:
        if any(item[2] != kept[2] and item[2] in kept[2] for kept in result):
            continue
        result.append(item)
    return result


def _ke_time_ago(created_at_ms):
    """毫秒时间戳 -> 'N天前/N小时前/N分钟前'"""
    if not created_at_ms:
        return '时间未知'
    delta = time.time() - created_at_ms / 1000.0
    if delta < 3600:
        return f'{max(1, int(delta // 60))}分钟前'
    if delta < 86400:
        return f'{int(delta // 3600)}小时前'
    return f'{int(delta // 86400)}天前'


def _ke_entity_names(conn, entity_refs):
    """批量查实体名称：{(entity_type, entity_id): name}"""
    names = {}
    for etype, eid in entity_refs:
        if (etype, eid) in names:
            continue
        try:
            table = 'talents' if etype == 'talent' else 'products'
            row = conn.execute(f'SELECT name FROM {table} WHERE id = ?', (eid,)).fetchone()
            names[(etype, eid)] = (row['name'] if row else '') or ''
        except Exception:
            names[(etype, eid)] = ''
    return names


def _retrieve_knowledge_context(user_text, agent_id='', auth=None):
    """检索 knowledge_events 中与本次提问相关的历史分析结论，格式化为注入文本。
    策略优先级：同实体历史 > 同类目相似分析 > embedding 语义兜底。
    无匹配或异常时返回 ''（不注入任何内容）。"""
    try:
        if not user_text or not isinstance(user_text, str):
            return ''
        if not any(k in user_text.upper() for k in _KE_INJECT_KEYWORDS):
            return ''
        entities = _extract_entities_from_text(user_text)
        picked = []        # [(event_row, label_prefix)]
        seen_ids = set()
        patterns = []      # 第四级：confirmed 规律
        conn = _db_conn()
        try:
            # 1) 同实体历史：每个识别到的实体取最近 N 条
            for etype, eid, name, _cat in entities:
                rows = conn.execute(
                    'SELECT * FROM knowledge_events WHERE entity_type = ? AND entity_id = ? '
                    'ORDER BY created_at DESC LIMIT ?',
                    (etype, eid, _KE_SAME_ENTITY_LIMIT)).fetchall()
                label = '达人' if etype == 'talent' else '商品'
                for r in rows:
                    if r['id'] in seen_ids:
                        continue
                    seen_ids.add(r['id'])
                    picked.append((r, f"{label}「{name}」分析"))
            # 2) 同类目相似分析：同 category 的其他达人，取最近 N 条
            category_queries = []  # (category, exclude_entity_id)
            for etype, eid, name, category in entities:
                if etype == 'talent' and category:
                    category_queries.append((category, eid))
            if not category_queries:
                # 无具体达人实体时，用类目关键词直接匹配 talents.category
                try:
                    cat_rows = conn.execute(
                        "SELECT DISTINCT category FROM talents WHERE status='active' AND category != ''").fetchall()
                    for cr in cat_rows:
                        if cr['category'] and cr['category'] in user_text:
                            category_queries.append((cr['category'], ''))
                except Exception:
                    pass
            for category, exclude_id in category_queries:
                if exclude_id:
                    rows = conn.execute(
                        "SELECT ke.* FROM knowledge_events ke "
                        "JOIN talents t ON ke.entity_type = 'talent' AND ke.entity_id = t.id "
                        "WHERE t.category = ? AND ke.entity_id != ? "
                        "ORDER BY ke.created_at DESC LIMIT ?",
                        (category, exclude_id, _KE_SAME_CATEGORY_LIMIT * 3)).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT ke.* FROM knowledge_events ke "
                        "JOIN talents t ON ke.entity_type = 'talent' AND ke.entity_id = t.id "
                        "WHERE t.category = ? "
                        "ORDER BY ke.created_at DESC LIMIT ?",
                        (category, _KE_SAME_CATEGORY_LIMIT * 3)).fetchall()
                count = 0
                for r in rows:
                    if r['id'] in seen_ids or count >= _KE_SAME_CATEGORY_LIMIT:
                        continue
                    seen_ids.add(r['id'])
                    other = conn.execute('SELECT name FROM talents WHERE id = ?', (r['entity_id'],)).fetchone()
                    other_name = (other['name'] if other else '') or '未知达人'
                    picked.append((r, f'同类目达人「{other_name}」分析'))
                    count += 1
            # 3) 兜底混合检索：向量 + FTS5 BM25 + 实体精确匹配三路 RRF 融合（阶段4B-P0，
            # 替代原纯 embedding 语义检索，embedding 不可用时 FTS/实体路仍可召回）
            try:
                hy_items = [it for it in _hybrid_retrieve_events(user_text, '', _KE_SEMANTIC_LIMIT * 3)
                            if it.get('id') not in seen_ids][:_KE_SEMANTIC_LIMIT]
                if hy_items:
                    names = _ke_entity_names(conn, [(it['entity_type'], it['entity_id']) for it in hy_items])
                    for it in hy_items:
                        row = conn.execute('SELECT * FROM knowledge_events WHERE id = ?', (it['id'],)).fetchone()
                        if not row:
                            continue
                        seen_ids.add(it['id'])
                        ename = names.get((it['entity_type'], it['entity_id']), '')
                        label = f"达人「{ename}」分析" if it['entity_type'] == 'talent' and ename else '相关历史分析'
                        picked.append((row, label))
            except Exception as e:
                logger.warning(f'  [KnowledgeInject] 混合检索失败，跳过: {e}')
            # 4) 历史规律：proven/verified/candidate 正常注入；兼容旧数据（level=hypothesis 且 status=confirmed
            # 按 verified 待遇注入）；category 匹配当前实体类目 OR entity_type 匹配，置信度降序取 top 2
            try:
                p_cats = {c for c, _ in category_queries if c}
                p_cats.update(cat for et, _id, _n, cat in entities if et == 'talent' and cat)
                p_types = {et for et, _id, _n, _c in entities}
                sub_conds, p_params = [], []
                if p_cats:
                    sub_conds.append('category IN (%s)' % ','.join('?' * len(p_cats)))
                    p_params.extend(sorted(p_cats))
                if p_types:
                    sub_conds.append('entity_type IN (%s)' % ','.join('?' * len(p_types)))
                    p_params.extend(sorted(p_types))
                if sub_conds:
                    patterns = conn.execute(
                        "SELECT * FROM knowledge_patterns WHERE (verification_level IN ('proven','verified','candidate')"
                        " OR (verification_level = 'hypothesis' AND status = 'confirmed')) AND ("
                        + ' OR '.join(sub_conds) + ') ORDER BY confidence_score DESC LIMIT 2',
                        p_params).fetchall()
            except Exception as e:
                logger.warning(f'  [KnowledgeInject] 规律查询失败，跳过: {e}')
        finally:
            conn.close()
        # 格式化输出 + 截断控制
        parts = []
        if picked:
            header = '【相关分析参考 - 仅供分析参考，不要直接复述】'
            footer = '以上为历史分析记录，请结合当前达人实际情况判断。'
            blocks = []
            used = len(header) + len(footer) + 2
            for r, label in picked:
                snippet = (r['content_full'] or '')[:_KE_EVENT_SNIPPET_LEN]
                block = f"▶ {label}（{_ke_time_ago(r['created_at'])}）：\n"
                if r['title']:
                    block += f"{r['title']}\n"
                block += snippet
                if used + len(block) + 1 > _KE_CONTEXT_MAX_LEN:
                    break
                blocks.append(block)
                used += len(block) + 1
            if blocks:
                parts.append(header + '\n' + '\n'.join(blocks) + '\n' + footer)
        if patterns:
            plines = ['【历史规律参考】']
            for p in patterns:
                try:
                    conf = float(p['confidence_score'] or 0)
                except (TypeError, ValueError):
                    conf = 0.0
                level = p['verification_level'] or 'hypothesis'
                if level == 'hypothesis' and p['status'] == 'confirmed':
                    level = 'verified'  # 旧数据兼容：按 verified 待遇注入、不加标注
                mark = ''
                if level == 'proven':
                    mark = '【高置信规律】'
                elif level == 'candidate':
                    mark = '【待更多验证】'
                plines.append(f"▶ {mark}{p['pattern_text']}（置信度: {conf:.0f}%）")
            parts.append('\n'.join(plines))
        if not parts:
            return ''
        result = '\n\n'.join(parts)
        if len(result) > _KE_CONTEXT_MAX_LEN:
            result = result[:_KE_CONTEXT_MAX_LEN - 3] + '...'
        return result
    except Exception as e:
        logger.error(f'  [KnowledgeInject] 检索失败: {e}')
        return ''


def _ke_event_to_list_item(r, score=None):
    """knowledge_events 行 -> 列表项 dict（不含 content_full / embedding）"""
    item = {
        'id': r['id'],
        'entity_type': r['entity_type'],
        'entity_id': r['entity_id'],
        'agent_id': r['agent_id'],
        'event_type': r['event_type'],
        'title': r['title'],
        'content_summary': r['content_summary'],
        'user_query': r['user_query'],
        'created_at': r['created_at'],
    }
    if score is not None:
        item['score'] = round(score, 4)
    return item


def _search_knowledge_events(query, entity_type='', limit=5):
    """语义搜索分析档案：embedding 可用按余弦相似度排序；不可用降级为 title+content_full LIKE。
    返回列表项（不含 content_full，语义检索附 score）。异常时返回 []。"""
    try:
        conn = _db_conn()
        try:
            if entity_type:
                rows = conn.execute(
                    'SELECT * FROM knowledge_events WHERE entity_type = ?', (entity_type,)).fetchall()
            else:
                rows = conn.execute('SELECT * FROM knowledge_events').fetchall()
            # 优先语义检索
            emb_cfg = get_embedding_config()
            api_key = emb_cfg.get('apiKey')
            if api_key:
                try:
                    query_emb = get_embedding(query[:2000], api_key, emb_cfg.get('provider', 'openai'),
                                              model=emb_cfg.get('model'), base_url=emb_cfg.get('baseUrl'))
                    if query_emb:
                        import struct
                        scored = []
                        for r in rows:
                            if not r['embedding']:
                                continue
                            try:
                                emb = struct.unpack(f'{len(r["embedding"]) // 4}f', r['embedding'])
                                scored.append((cosine_similarity(query_emb, emb), r))
                            except Exception:
                                continue
                        scored.sort(key=lambda x: x[0], reverse=True)
                        return [_ke_event_to_list_item(r, score=s) for s, r in scored[:limit]]
                except Exception as e:
                    logger.warning(f'  [KnowledgeEvents] 语义搜索失败，降级模糊匹配: {e}')
            # 降级：LIKE 模糊匹配
            like = f'%{query}%'
            matched = [r for r in rows if like.strip('%') and
                       (like.strip('%') in (r['title'] or '') or like.strip('%') in (r['content_full'] or ''))]
            matched.sort(key=lambda r: r['created_at'] or 0, reverse=True)
            return [_ke_event_to_list_item(r) for r in matched[:limit]]
        finally:
            conn.close()
    except Exception as e:
        logger.error(f'  [KnowledgeEvents] search failed: {e}')
        return []


# ═══ 三信号混合检索（阶段4B-P0）：向量 + FTS5 BM25 + 实体精确匹配，RRF 融合 ═══
_KE_LIST_COLS = 'id, entity_type, entity_id, agent_id, event_type, title, content_summary, user_query, created_at'


def _ke_fts_escape_query(query):
    """把用户 query 转成安全的 FTS5 MATCH 表达式：去特殊字符，
    拉丁/数字按原词、中文按 bigram 切分，OR 连接（quoted 防注入）。处理后为空返回 ''。"""
    try:
        query = (query or '').strip()
        if not query:
            return ''
        terms = []
        for seg in re.findall(r'[一-鿿]+|[A-Za-z0-9_]+', query):
            if re.match(r'[A-Za-z0-9_]', seg):
                terms.append(seg)
            elif len(seg) == 1:
                terms.append(seg)
            else:
                terms.extend(seg[i:i + 2] for i in range(len(seg) - 1))
        terms = [t.replace('"', '') for t in terms if t.strip('"')]
        if not terms:
            return ''
        return ' OR '.join(f'"{t}"' for t in terms[:20])
    except Exception:
        return ''


def _ke_extract_query_entities(query):
    """路3 实体提取：达人ID（inf_/tal_ 前缀正则）、active 商品名、达人名
    （名称出现在 query 中，最长优先，各最多取3个）、类目词（talents/products.category distinct 值）。
    返回 {'entity_refs': [(entity_type, entity_id), ...], 'categories': [...]}。异常返回空结构。"""
    result = {'entity_refs': [], 'categories': []}
    try:
        text = (query or '').strip()
        if not text:
            return result
        refs = []
        cats = []
        conn = _db_conn()
        try:
            # 显式达人 ID（不校验存在性，事件表按 entity_id 直接查即可）
            for m in re.findall(r'(inf_[a-zA-Z0-9_]+|tal_[a-zA-Z0-9_]+)', text):
                refs.append(('talent', m))
            # 商品名（status='active'，出现在 query 中，最长优先，最多3个）
            prows = conn.execute("SELECT id, name FROM products WHERE status='active'").fetchall()
            p_hits = sorted(((r['id'], r['name'].strip()) for r in prows
                             if r['name'] and r['name'].strip() and r['name'].strip() in text),
                            key=lambda x: len(x[1]), reverse=True)[:3]
            refs.extend(('product', pid) for pid, _n in p_hits)
            # 达人名同理
            trows = conn.execute("SELECT id, name FROM talents WHERE status='active'").fetchall()
            t_hits = sorted(((r['id'], r['name'].strip()) for r in trows
                             if r['name'] and r['name'].strip() and r['name'].strip() in text),
                            key=lambda x: len(x[1]), reverse=True)[:3]
            refs.extend(('talent', tid) for tid, _n in t_hits)
            # 类目词：talents.category / products.category distinct 值出现在 query 中
            crows = conn.execute(
                "SELECT DISTINCT category FROM talents WHERE status='active' AND category != ''").fetchall()
            crows += conn.execute(
                "SELECT DISTINCT category FROM products WHERE status='active' AND category != ''").fetchall()
            cats = sorted({r['category'].strip() for r in crows
                           if r['category'] and r['category'].strip() and r['category'].strip() in text},
                          key=len, reverse=True)[:3]
        finally:
            conn.close()
        seen = set()
        for ref in refs:
            if ref not in seen:
                seen.add(ref)
                result['entity_refs'].append(ref)
        result['categories'] = cats
    except Exception as e:
        logger.warning(f'  [HybridRetrieve] query 实体提取失败: {e}')
    return result


def _ke_entity_match_events(query, entity_type='', limit=5):
    """路3 实体精确匹配：收 query 命中实体（及命中类目下）的最近事件（created_at DESC，每实体/类目最多 limit 条）。
    返回 (ranked_ids, {id: list_item})。异常返回 ([], {})。"""
    ids, items, seen = [], {}, set()
    try:
        found = _ke_extract_query_entities(query)
        refs = [(et, eid) for et, eid in found['entity_refs'] if not entity_type or et == entity_type]
        cats = found['categories']
        if not refs and not cats:
            return ids, items
        conn = _db_conn()
        try:
            def _add_rows(rows):
                for r in rows:
                    if r['id'] in seen:
                        continue
                    seen.add(r['id'])
                    ids.append(r['id'])
                    items[r['id']] = _ke_event_to_list_item(r)

            for et, eid in refs:
                rows = conn.execute(
                    f'SELECT {_KE_LIST_COLS} FROM knowledge_events WHERE entity_type = ? AND entity_id = ? '
                    'ORDER BY created_at DESC LIMIT ?', (et, eid, limit)).fetchall()
                _add_rows(rows)
            for cat in cats:
                if entity_type in ('', 'talent'):
                    rows = conn.execute(
                        f"SELECT ke.{', ke.'.join(_KE_LIST_COLS.split(', '))} FROM knowledge_events ke "
                        "JOIN talents t ON ke.entity_type = 'talent' AND ke.entity_id = t.id "
                        'WHERE t.category = ? ORDER BY ke.created_at DESC LIMIT ?', (cat, limit)).fetchall()
                    _add_rows(rows)
                if entity_type in ('', 'product'):
                    rows = conn.execute(
                        f"SELECT ke.{', ke.'.join(_KE_LIST_COLS.split(', '))} FROM knowledge_events ke "
                        "JOIN products p ON ke.entity_type = 'product' AND ke.entity_id = p.id "
                        'WHERE p.category = ? ORDER BY ke.created_at DESC LIMIT ?', (cat, limit)).fetchall()
                    _add_rows(rows)
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f'  [HybridRetrieve] 实体匹配路失败: {e}')
    return ids, items


def _hybrid_retrieve_events(query, entity_type='', limit=5):
    """三信号混合检索：路1 向量语义（复用 _search_knowledge_events，内部 LIKE 降级）、
    路2 FTS5 BM25、路3 实体精确匹配；RRF 融合（score = Σ 1/(60+rank)）后叠加
    final = 0.6*hybrid_norm + 0.25*(importance_score/10) + 0.15*recency（30天线性衰减），
    按 final 降序取 top limit，返回 item 结构同 _ke_event_to_list_item（score 为 final，round 4 位）。
    任一路失败记日志降级为其余路；三路全失败返回 []。整体绝不抛异常。"""
    try:
        query = (query or '').strip()
        if not query:
            return []
        limit = max(1, int(limit or 5))
        ranked_lists = []   # 各路名次 id 列表（名次从 1 开始）
        items = {}          # id -> list item dict
        # 路1：向量语义检索（内部逻辑不动，embedding 不可用时其自身降级 LIKE）
        try:
            v_items = _search_knowledge_events(query, entity_type, limit)
            if v_items:
                ranked_lists.append([it['id'] for it in v_items])
                for it in v_items:
                    items.setdefault(it['id'], it)
        except Exception as e:
            logger.warning(f'  [HybridRetrieve] 向量路失败，跳过: {e}')
        # 路2：FTS5 BM25（FTS5 不可用时跳过）
        try:
            if _KE_FTS_ENABLED:
                match_q = _ke_fts_escape_query(query)
                if match_q:
                    conn = _db_conn()
                    try:
                        fts_rows = conn.execute(
                            'SELECT event_id, bm25(knowledge_events_fts) AS bm FROM knowledge_events_fts '
                            'WHERE knowledge_events_fts MATCH ? ORDER BY bm LIMIT ?',
                            (match_q, limit)).fetchall()
                        fids = [r['event_id'] for r in fts_rows]
                        if fids and entity_type:
                            ph = ','.join('?' * len(fids))
                            ok = {r['id'] for r in conn.execute(
                                f'SELECT id FROM knowledge_events WHERE entity_type = ? AND id IN ({ph})',
                                [entity_type] + fids).fetchall()}
                            fids = [i for i in fids if i in ok]
                        if fids:
                            ph = ','.join('?' * len(fids))
                            rows = conn.execute(
                                f'SELECT {_KE_LIST_COLS} FROM knowledge_events WHERE id IN ({ph})', fids).fetchall()
                            by_id = {r['id']: r for r in rows}
                            fids = [i for i in fids if i in by_id]
                            if fids:
                                ranked_lists.append(fids)
                                for i in fids:
                                    items.setdefault(i, _ke_event_to_list_item(by_id[i]))
                    finally:
                        conn.close()
        except Exception as e:
            logger.warning(f'  [HybridRetrieve] FTS 路失败，跳过: {e}')
        # 路3：实体精确匹配
        try:
            e_ids, e_items = _ke_entity_match_events(query, entity_type, limit)
            if e_ids:
                ranked_lists.append(e_ids)
                for k, v in e_items.items():
                    items.setdefault(k, v)
        except Exception as e:
            logger.warning(f'  [HybridRetrieve] 实体匹配路失败，跳过: {e}')
        if not ranked_lists:
            return []
        # RRF 融合（按 id 去重合并）
        rrf = {}
        for id_list in ranked_lists:
            for rank, eid in enumerate(id_list, 1):
                rrf[eid] = rrf.get(eid, 0.0) + 1.0 / (_KE_RRF_K + rank)
        # 取重要度/时间做最终打分
        cand_ids = list(rrf.keys())
        ph = ','.join('?' * len(cand_ids))
        conn = _db_conn()
        try:
            meta = {r['id']: r for r in conn.execute(
                f'SELECT id, importance_score, created_at FROM knowledge_events WHERE id IN ({ph})',
                cand_ids).fetchall()}
        finally:
            conn.close()
        max_rrf = max(rrf.values())
        now_ms = int(time.time() * 1000)
        finals = []
        for eid, rrf_s in rrf.items():
            m = meta.get(eid)
            if not m or eid not in items:
                continue
            hybrid_norm = rrf_s / max_rrf if max_rrf > 0 else 0.0
            try:
                imp = float(m['importance_score']) if m['importance_score'] is not None else 5.0
            except (TypeError, ValueError):
                imp = 5.0
            age_ms = max(0, now_ms - int(m['created_at'] or 0))
            recency = max(0.0, 1.0 - age_ms / _KE_RECENCY_WINDOW_MS)
            final = _KE_W_HYBRID * hybrid_norm + _KE_W_IMPORTANCE * (imp / 10.0) + _KE_W_RECENCY * recency
            finals.append((final, eid))
        finals.sort(key=lambda x: (-x[0], -(meta[x[1]]['created_at'] or 0)))
        out = []
        for final, eid in finals[:limit]:
            item = dict(items[eid])
            item['score'] = round(final, 4)
            out.append(item)
        # 检索命中批量更新 last_accessed_at（失败不影响返回）
        if out:
            try:
                conn = _db_conn()
                try:
                    ph2 = ','.join('?' * len(out))
                    conn.execute(
                        f'UPDATE knowledge_events SET last_accessed_at = ? WHERE id IN ({ph2})',
                        [str(now_ms)] + [it['id'] for it in out])
                    conn.commit()
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(f'  [HybridRetrieve] last_accessed_at 更新失败: {e}')
        return out
    except Exception as e:
        logger.error(f'  [HybridRetrieve] 混合检索失败: {e}')
        return []


# ═══ 规律归纳（L3）：跨达人共性规律 ═══
_KP_INDUCE_MIN_EVENTS = 5       # 归纳所需最少同类目事件数
_KP_INDUCE_MAX_EVENTS = 20      # 归纳取最近 N 条事件
_KP_EVENT_SNIPPET_LEN = 300     # 每条事件 content_full 截取长度
# 状态机：draft → confirmed/rejected → deprecated
_KP_STATUS_FLOW = {
    'draft': ('confirmed', 'rejected'),
    'confirmed': ('deprecated',),
    'rejected': ('deprecated',),
}
# 置信等级枚举：hypothesis(假设) → candidate(候选) → verified(已验证) → proven(成熟)；deprecated 为终态
_KP_LEVELS = ('hypothesis', 'candidate', 'verified', 'proven', 'deprecated')


def _kp_can_promote(cur_level, target_level, confidence_score, evidence_count, approved=False):
    """规律置信等级晋升门槛校验（只做校验，不做自动晋升）：
    hypothesis→candidate 需审核通过且证据≥3；candidate→verified 需置信度≥70且证据≥10；
    verified→proven 需置信度≥85且证据≥30；其余组合不允许。"""
    try:
        confidence_score = float(confidence_score or 0)
        evidence_count = int(evidence_count or 0)
    except (TypeError, ValueError):
        return False
    if cur_level == 'hypothesis' and target_level == 'candidate':
        return approved and evidence_count >= 3
    if cur_level == 'candidate' and target_level == 'verified':
        return confidence_score >= 70 and evidence_count >= 10
    if cur_level == 'verified' and target_level == 'proven':
        return confidence_score >= 85 and evidence_count >= 30
    return False


def _kp_row_to_dict(r, with_evidence=False):
    """knowledge_patterns 行 -> dict。列表不含 evidence，详情才含。"""
    item = {
        'id': r['id'],
        'category': r['category'],
        'entity_type': r['entity_type'],
        'pattern_text': r['pattern_text'],
        'confidence': r['confidence'],
        'status': r['status'],
        'confidence_score': r['confidence_score'],
        'evidence_count': r['evidence_count'],
        'verification_level': r['verification_level'] or 'hypothesis',
        'source_event_ids': json.loads(r['source_event_ids'] or '[]'),
        'created_by': r['created_by'],
        'created_at': r['created_at'],
        'updated_at': r['updated_at'],
    }
    if with_evidence:
        try:
            item['evidence'] = json.loads(r['evidence'] or '[]')
        except Exception:
            item['evidence'] = []
    return item


# ═══ 合作单（deals）：达人-商品合作全流程 ═══
# 状态机：pending → negotiating → sample_sent → approved → live → completed/failed
_DEAL_STATUS_FLOW = {
    'pending': ('negotiating', 'failed'),
    'negotiating': ('sample_sent', 'failed'),
    'sample_sent': ('approved', 'failed'),
    'approved': ('live', 'failed'),
    'live': ('completed', 'failed'),
    'completed': (),
    'failed': (),
}
# 终态归因枚举：成败原因 / 问题阶段
_DEAL_WIN_LOSS_CATEGORIES = ('price_commission', 'tone_mismatch', 'product_weak',
                             'experience', 'competitor', 'schedule', 'other')
_DEAL_KEY_MOMENTS = ('first_contact', 'negotiating', 'sample_sent',
                     'pre_live', 'during_live', 'post_live')


def _deal_row_to_dict(r):
    """deals 行 -> dict"""
    return {
        'id': r['id'],
        'talent_id': r['talent_id'],
        'product_id': r['product_id'],
        'product_name': r['product_name'],
        'deal_type': r['deal_type'],
        'commission_rate': r['commission_rate'],
        'status': r['status'],
        'scheduled_at': r['scheduled_at'],
        'actual_gmv': r['actual_gmv'],
        'actual_roi': r['actual_roi'],
        'actual_units': r['actual_units'],
        'result_note': r['result_note'],
        'predicted_conclusion': r['predicted_conclusion'],
        'predicted_event_id': r['predicted_event_id'],
        'verification': r['verification'],
        'win_loss_category': r['win_loss_category'] if 'win_loss_category' in r.keys() else '',
        'key_moment': r['key_moment'] if 'key_moment' in r.keys() else '',
        'decision_maker_feedback': r['decision_maker_feedback'] if 'decision_maker_feedback' in r.keys() else '',
        'created_by': r['created_by'],
        'created_at': r['created_at'],
        'updated_at': r['updated_at'],
    }


def _resolve_induce_llm_config(agent_id=''):
    """解析规律归纳用 LLM 配置。
    优先读 settings.json 的 llm 字段（provider/apiKey/baseUrl/model，映射为 apiProvider/apiKey/customEndpoint/apiModel）；
    不存在或 apiKey 为空时 fallback 到 agents.json：优先指定 agent，否则第一个配置了 apiKey 的员工。
    返回 dict 或 None。"""
    try:
        settings = _read_json(SETTINGS_FILE, {}) or {}
        llm = settings.get('llm') or {}
        if isinstance(llm, dict) and (llm.get('apiKey') or '').strip():
            return {
                'apiProvider': (llm.get('provider') or '').strip(),
                'apiKey': llm['apiKey'].strip(),
                'apiModel': (llm.get('model') or '').strip(),
                'customEndpoint': (llm.get('baseUrl') or '').strip(),
            }
    except Exception as e:
        logger.warning(f'  [KnowledgePatterns] settings.llm 读取失败: {e}')
    try:
        agents = _read_json(AGENTS_FILE, []) or []
        candidates = []
        if agent_id:
            candidates = [a for a in agents if a.get('id') == agent_id]
        candidates += [a for a in agents if a.get('id') != agent_id]
        for a in candidates:
            key = (a.get('apiKey') or '').strip()
            if key:
                return {
                    'apiProvider': a.get('aiProvider', '') or a.get('apiProvider', ''),
                    'apiKey': key,
                    'apiModel': a.get('apiModel', ''),
                    'customEndpoint': a.get('customEndpoint', ''),
                }
    except Exception as e:
        logger.warning(f'  [KnowledgePatterns] LLM 配置解析失败: {e}')
    return None


def _induce_knowledge_patterns(category, llm_config, created_by=''):
    """对指定类目的分析事件做 LLM 规律归纳，结果写入 knowledge_patterns（status=draft）。
    返回 (ok, result_dict)；数据不足 / LLM 失败 / JSON 非法时返回 (False, {error})。全流程异常兜底。"""
    try:
        conn = _db_conn()
        try:
            # knowledge_events 无 category 列，通过 entity_id JOIN talents 关联类目
            rows = conn.execute(
                "SELECT ke.id, ke.title, ke.content_full, ke.created_at FROM knowledge_events ke "
                "JOIN talents t ON ke.entity_type = 'talent' AND ke.entity_id = t.id "
                "WHERE t.category = ? ORDER BY ke.created_at DESC LIMIT ?",
                (category, _KP_INDUCE_MAX_EVENTS)).fetchall()
        finally:
            conn.close()
        if len(rows) < _KP_INDUCE_MIN_EVENTS:
            return False, {'error': f'数据不足，至少需要{_KP_INDUCE_MIN_EVENTS}条同类目分析事件',
                           'current_count': len(rows)}
        event_ids = [r['id'] for r in rows]
        lines = []
        for i, r in enumerate(rows, 1):
            snippet = (r['content_full'] or '')[:_KP_EVENT_SNIPPET_LEN]
            lines.append(f'事件{i}（ID: {r["id"]}）\n标题: {r["title"] or ""}\n内容: {snippet}')
        user_prompt = f'以下是「{category}」类目的 {len(rows)} 条达人分析事件：\n\n' + '\n\n'.join(lines)
        messages = [
            {'role': 'system', 'content': '你是达人撮合业务的知识归纳专家。请从以下多条分析事件中提炼跨达人的共性规律。'
                                          '每条规律要具体、可操作，包含适用条件和预期效果。'
                                          '输出 JSON 数组，每条包含 pattern_text(规律描述)、evidence_event_ids(支撑的事件ID列表)、confidence(置信度0-1)。'},
            {'role': 'user', 'content': user_prompt},
        ]
        # kimi-for-coding 是推理模型，thinking 块会先消耗 max_tokens，需要给足余量；推理耗时长，timeout 放宽到 300s
        raw = _call_chat_completion(llm_config.get('apiProvider', ''), llm_config.get('apiKey', ''),
                                    llm_config.get('apiModel', ''), llm_config.get('customEndpoint', ''),
                                    messages, timeout=300, max_tokens=8000)
        if not raw:
            return False, {'error': 'LLM调用失败'}
        m = re.search(r'\[.*\]', raw.strip(), re.S)
        patterns = None
        if m:
            try:
                patterns = json.loads(m.group(0))
            except Exception:
                patterns = None
        if not isinstance(patterns, list):
            logger.warning(f'  [KnowledgePatterns] LLM 返回非法 JSON: {(raw or "")[:200]}')
            return False, {'error': 'LLM解析失败'}
        now = int(time.time())
        saved = []
        conn = _db_conn()
        try:
            for p in patterns:
                if not isinstance(p, dict) or not (p.get('pattern_text') or '').strip():
                    continue
                ev_ids = p.get('evidence_event_ids')
                if not isinstance(ev_ids, list):
                    ev_ids = []
                try:
                    conf = float(p.get('confidence', 0.5))
                except (TypeError, ValueError):
                    conf = 0.5
                pid = 'kp_' + uuid.uuid4().hex[:12]
                conn.execute(
                    "INSERT INTO knowledge_patterns (id, category, entity_type, pattern_text, evidence, "
                    "confidence, status, source_event_ids, created_by, created_at, updated_at, "
                    "confidence_score, evidence_count, verification_level) "
                    "VALUES (?, ?, 'talent', ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, 'hypothesis')",
                    (pid, category, p['pattern_text'].strip(), json.dumps(ev_ids, ensure_ascii=False),
                     conf, json.dumps(event_ids, ensure_ascii=False), created_by, now, now,
                     round(conf * 100, 1), len(ev_ids)))
                saved.append({'id': pid, 'category': category, 'entity_type': 'talent',
                              'pattern_text': p['pattern_text'].strip(), 'confidence': conf,
                              'status': 'draft', 'source_event_ids': event_ids,
                              'confidence_score': round(conf * 100, 1), 'evidence_count': len(ev_ids),
                              'verification_level': 'hypothesis',
                              'created_at': now, 'updated_at': now})
            conn.commit()
        finally:
            conn.close()
        logger.info(f'  [KnowledgePatterns] {category} 归纳出 {len(saved)} 条规律')
        return True, {'induced': len(saved), 'patterns': saved}
    except Exception as e:
        logger.error(f'  [KnowledgePatterns] 归纳失败: {e}')
        return False, {'error': str(e)}


def _append_self_update_prompt(system_prompt):
    """在 system_prompt 末尾追加自修改工具声明"""
    if not system_prompt:
        system_prompt = ''
    declaration = (
        '\n\n---\n'
        '【重要指令 - 自我修改能力】\n'
        '当老板要求你修改自己的简介/描述/角色/行为指令时，'
        '你必须在回复中输出以下标记（这是唯一生效的方式，只口头承诺无效）：\n\n'
        '示例1 - 老板说"把你的描述改成xxx"：\n'
        '你的回复必须包含：[SELF_UPDATE]description=xxx[/SELF_UPDATE]\n\n'
        '示例2 - 老板说"改一下你的角色"：\n'
        '你的回复必须包含：[SELF_UPDATE]role=新角色[/SELF_UPDATE]\n\n'
        '注意：这是改你自己的配置，不是改商品！'
        '严禁调用add_product或update_product来修改自己的描述。\n'
        '---'
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
    logger.info(f'  [SELF_UPDATE] agent={agent_id} source={source} fields={fields}')


def _apply_agent_self_update(agent_id, updates, source='openclaw'):
    """将自修改更新应用到 agents.json；返回 (success, message, agent_or_none)"""
    if not agent_id or not updates:
        return True, '无更新', None
    # 安全过滤：只保留允许字段
    allowed = []
    for field, value in updates:
        if field not in _SELF_UPDATE_ALLOWED_FIELDS:
            logger.info(f'  [SELF_UPDATE] 忽略不允许的字段: {field}')
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
        logger.info(f'  [OpenClaw] CLI not found at {OPENCLAW_CLI}')
        return None

    full_prompt = ''
    if system_prompt:
        full_prompt += system_prompt + '\n\n'
    full_prompt += prompt

    # 与大脑知识中枢保持一致：过长 prompt 截断
    MAX_PROMPT_LEN = 10000
    if len(full_prompt) > MAX_PROMPT_LEN:
        logger.info(f'  [OpenClaw] WARNING: prompt too long ({len(full_prompt)}), truncating to {MAX_PROMPT_LEN}')
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
        logger.info(f'  [OpenClaw] {name} cmd: {" ".join(args)}')
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
                logger.error(f'  [OpenClaw] {name} failed (code={returncode}):')
                logger.info(f'      stderr: {stderr}')
                logger.info(f'      stdout: {stdout}')
                # 如果当前命令不存在，继续尝试旧命令；否则直接返回 None
                if 'unknown command' in (stderr or '').lower():
                    continue
                return None
            try:
                output = json.loads(stdout)
            except Exception:
                logger.info(f'  [OpenClaw] {name} output is not JSON: {stdout[:500]}')
                continue
            content = _extract_text_from_openclaw_output(output)
            if content:
                logger.info(f'  [OpenClaw] {name} success, content length={len(content)}')
                return content
            logger.info(f'  [OpenClaw] {name} returned empty/unrecognized content: {stdout[:500]}')
        except subprocess.TimeoutExpired as e:
            logger.info(f'  [OpenClaw] {name} timed out after {timeout}s (gateway offline?): {e}')
            return None
        except Exception as e:
            logger.error(f'  [OpenClaw] {name} _call_openclaw_infer failed: {e}')
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
                logger.info(f'  [AI] fallback to agent {agent.get("id")} AI config for {context}')
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
    logger.info(f'  [AI] start analysis context={context} provider={provider} chat_model={chat_model} key={masked_key} openclaw={OPENCLAW_CLI}')

    # 1. 优先 OpenClaw（项目主推的 AI 网关）
    if os.path.isfile(OPENCLAW_CLI):
        oc_timeout = timeout if timeout is not None else OPENCLAW_TIMEOUT
        content = _call_openclaw_infer(full_prompt, model=chat_model, system_prompt=system_prompt, timeout=oc_timeout)
        if content:
            return content
        logger.error(f'  [AI] OpenClaw failed for {context}, will try direct API fallback')
    else:
        logger.info(f'  [AI] OpenClaw CLI not available for {context}, skip to direct API')

    # 2. 兜底：API 直连（需配置 API Key）
    if api_key:
        api_timeout = timeout if timeout is not None else PROXY_TIMEOUT
        content = _call_chat_completion(provider, api_key, chat_model, base_url, messages, timeout=api_timeout, max_tokens=max_tokens)
        if content:
            return content
    else:
        logger.info(f'  [AI] no API key configured for {context}, skip direct API fallback')

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
        logger.info(f'  [Knowledge] mock mode enabled for {agent.get("id", "?")}, returning sample docs')
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
        logger.info(f'  [OpenClaw] WARNING: prompt too long ({len(full_prompt)}), truncating to {MAX_PROMPT_LEN}')
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
    # 达人分析类员工自动追加数据源强制约束，防止编造达人数据
    system_prompt = _append_influencer_data_constraint(agent, system_prompt)
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
            logger.error(f'  [MemoryInject] {agent_id} 注入失败: {e}')

        # 注入项目组公共记忆（群聊场景）
        if group_id:
            try:
                system_prompt = ms3.inject_group_memories(group_id, system_prompt)
            except Exception as e:
                logger.error(f'  [GroupMemoryInject] {group_id} 注入失败: {e}')

        # 注入团队动态（同项目组其他 agent 最近24小时对话摘要；不注入自己的消息，
        # 不在任何项目组的 agent 不注入；include_history=False 的摘要/提取任务不注入）
        if include_history:
            try:
                agent_group_id = _get_agent_group_id(agent_id)
                if agent_group_id:
                    team_feed = _build_team_feed(agent_group_id, agent_id)
                    if team_feed:
                        system_prompt += '\n\n' + team_feed
            except Exception as e:
                logger.error(f'  [TeamFeed] {agent_id} 注入失败: {e}')

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
            logger.error(f'  [RAG] {agent_id} 注入失败: {e}')

        # 注入知识事件检索结果（分析档案历史结论召回）
        if include_history:
            try:
                ke_context = _retrieve_knowledge_context(user_text, agent_id, None)
                if ke_context:
                    system_prompt += f'\n\n{ke_context}'
                    logger.info(f'  [KnowledgeInject] {agent_id} 注入知识事件上下文 {len(ke_context)} 字')
            except Exception as e:
                logger.error(f'  [KnowledgeInject] {agent_id} 注入失败: {e}')

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

    # 优先尝试 agent 自己的 provider 配置；如果失败，走 settings.json 多 provider 降级
    result = _call_chat_completion(api_provider, api_key, api_model, custom_endpoint, messages, timeout=PROXY_TIMEOUT)
    if result is None and not custom_endpoint:
        # agent 级别调用失败，尝试 settings.json 的 provider 列表降级
        logger.info(f'  [Fallback] Agent provider "{api_provider}" failed, trying settings.json fallback')
        result = _call_chat_completion_with_fallback(messages, timeout=PROXY_TIMEOUT)
    return result

def _handle_delete_chat_message(self, agent_id, msg_id):
    """DELETE /api/chat/:agentId/:msgId?type=..."""
    auth = _authenticate(self.headers, self.client_address[0], self)
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
        logger.info(f'  [ChatDELETE] {agent_id} 删除消息 {msg_id}，剩余 {len(messages)} 条')
    self._send_json(200, {'message': '消息已删除'})

def _handle_clear_chat(self, agent_id):
    """DELETE /api/chat/:agentId?type=... - 清空聊天记录"""
    auth = _authenticate(self.headers, self.client_address[0], self)
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
            _trash_file(chat_file)
            logger.info(f'  [ChatCLEAR] {agent_id} 聊天记录已清空（已备份到 backups/deleted/）')
        except OSError as e:
            logger.error(f'  [ChatCLEAR] {agent_id} 清空失败: {e}')
            pass
    else:
        logger.info(f'  [ChatCLEAR] {agent_id} 文件不存在，无需清空')

    self._send_json(200, {'message': '聊天记录已清空'})

def _handle_get_summarize(self, agent_id):
    """GET /api/chat/summarize/:agentId - 读取已保存的对话摘要"""
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
        logger.info(f'  [Summarize] {agent_id} 摘要已存入 L3 归档层')
    except Exception as e:
        logger.error(f'  [Summarize] 存入 L3 归档层失败: {e}')

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
        logger.error(f'  [Summary] AI摘要失败: {e}')

    # 降级：AI不可用时，截取最近 N 条消息文本作为摘要
    lines = chat_text.strip().split('\n')
    fallback_lines = lines[-10:] if len(lines) > 10 else lines
    fallback = '\n'.join(fallback_lines).strip()
    if len(fallback) > 500:
        fallback = fallback[:500] + '...'
    if fallback:
        logger.info(f'  [Summary] AI 不可用，已降级为文本截取（{len(fallback_lines)} 条消息）')
        return fallback
    return ''

# ═══════════════════════════════════════════════════
# OpenClaw API（原有功能，已加认证）
# ═══════════════════════════════════════════════════

def _handle_openclaw_status(self):
    """GET /api/openclaw/status"""
    auth = _authenticate(self.headers, self.client_address[0], self)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return
    status = _openclaw_status()
    self._send_json(200, status)

def _handle_openclaw_list_agents(self):
    """GET /api/openclaw/agents"""
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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

    # 写入默认 TOOLS.md（X-Agent-Id 硬编码进 curl 命令），已有定制化文件不覆盖
    tools_path = os.path.join(workspace, 'TOOLS.md')
    if not os.path.exists(tools_path):
        try:
            with open(tools_path, 'w', encoding='utf-8') as f:
                f.write(_build_agent_tools_doc(name))
        except OSError as e:
            logging.warning(f"Failed to write TOOLS.md: {e}")

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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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


def _detect_request_format(target_url, body_json):
    """根据目标 URL path 和 body 结构判断前端期望的请求格式。
    返回 'openai' 或 'anthropic'。默认 anthropic（Kimi coding 兼容）。"""
    if target_url:
        url_lower = target_url.lower()
        # OpenAI 风格 path
        if '/chat/completions' in url_lower or '/v1/completions' in url_lower:
            return 'openai'
        # Anthropic 风格 path
        if '/v1/messages' in url_lower or url_lower.endswith('/messages'):
            return 'anthropic'
    # body 兜底：Anthropic 通常带 system + max_tokens 顶层字段
    if isinstance(body_json, dict):
        if 'system' in body_json and 'max_tokens' in body_json:
            return 'anthropic'
    return 'anthropic'


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

    logger.info(f'  [ToolUse] 检测到tool_use续调用, 图片数={len(image_items)}')

    if not image_items:
        logger.info('  [Proxy] Anthropic tool_use 续调用跳过：原始请求中未找到图片内容')
        return None

    def _fetch_image_description(image_item, image_index):
        """构造独立的图片识别请求，调用同一个 Kimi API endpoint 获取真实描述。"""
        try:
            logger.info(f'  [ImageDesc] 开始获取图片描述, imageIndex={image_index}')
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
            logger.info(f'  [ImageDesc] 获取成功, 描述长度={len(description)}')
            return description
        except Exception as e:
            logger.error(f'  [ImageDesc] 获取失败, error={e}')
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
                    logger.info(f'  [Proxy] describe_image imageIndex 越界: {image_index} (共 {len(image_items)} 张)')
            else:
                logger.info(f'  [Proxy] describe_image imageIndex 无效: {image_index}')

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

        logger.info(f'  [ToolUse] 重新调用API, messages数={len(messages)}')
        headers['Content-Length'] = str(len(new_body))
        req = urllib.request.Request(target_url, data=new_body, headers=headers, method='POST')
        ctx = ssl.create_default_context()
        resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
        resp_body = resp.read()
        current_resp = json.loads(resp_body.decode('utf-8', errors='replace'))

        # 日志
        resp_content_items = current_resp.get('content', []) if isinstance(current_resp.get('content'), list) else []
        resp_text = ''.join(item.get('text', '') for item in resp_content_items if isinstance(item, dict) and item.get('type') == 'text')
        logger.info(f'  [Proxy] API返回(Anthropic tool_use续调用 retry={retry + 1}) status={resp.status} content_len={len(resp_text)} <- {target_url}')

        if current_resp.get('stop_reason') != 'tool_use':
            break

    # 取最终响应中 type 为 text 的 content 作为 AI 回复
    final_content_items = current_resp.get('content', []) if isinstance(current_resp.get('content'), list) else []
    final_texts = [item.get('text', '') for item in final_content_items if isinstance(item, dict) and item.get('type') == 'text']
    final_text = ''.join(final_texts)

    final_resp = dict(current_resp)
    final_resp['content'] = [{'type': 'text', 'text': final_text}]
    logger.info(f'  [ToolUse] 续调用完成, 最终content_len={len(final_text)}')
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
        logger.info(f'  [Proxy] token usage log skipped: {e}')


# ═══════════════════════════════════════════════════
# 积分制算力管控（1 积分 = 1000 tokens）
# ═══════════════════════════════════════════════════

CREDITS_PER_TOKENS = 1000          # 1 积分 = 1000 tokens
CREDIT_SYNC_INTERVAL_S = 300       # OpenClaw 聊天积分定时同步间隔（秒）


def _ensure_credit_account(conn, agent_id):
    """确保员工积分账户存在（首次使用按 0 余额创建），返回账户行"""
    row = conn.execute('SELECT * FROM credit_accounts WHERE agent_id = ?', (agent_id,)).fetchone()
    if row is None:
        conn.execute(
            'INSERT OR IGNORE INTO credit_accounts (agent_id, balance, total_recharged, total_consumed) VALUES (?, 0, 0, 0)',
            (agent_id,)
        )
        row = conn.execute('SELECT * FROM credit_accounts WHERE agent_id = ?', (agent_id,)).fetchone()
    return row


def _check_credit_balance(agent_id):
    """检查员工是否有足够积分发送消息。返回 (balance, has_credits)"""
    conn = _db_conn()
    try:
        account = _ensure_credit_account(conn, agent_id)
        conn.commit()
        balance = account['balance'] or 0
        return (balance, balance > 0)
    finally:
        conn.close()


def _recharge_credits(conn, agent_id, amount, operator=''):
    """给员工充值积分：余额与累计充值同时增加（调用方负责 commit）"""
    _ensure_credit_account(conn, agent_id)
    conn.execute(
        "UPDATE credit_accounts SET balance = balance + ?, total_recharged = total_recharged + ?, updated_at = datetime('now','localtime') WHERE agent_id = ?",
        (amount, amount, agent_id)
    )
    row = conn.execute('SELECT balance FROM credit_accounts WHERE agent_id = ?', (agent_id,)).fetchone()
    new_balance = row['balance'] or 0
    logger.info(f'  [Credits] 充值 agent={agent_id} amount={amount} new_balance={new_balance} operator={operator}')
    return new_balance


def _record_credit_usage(conn, agent_id, input_tokens, output_tokens, cache_read_tokens, session_id='', created_at=None):
    """记录一条算力消耗：写入 credit_usage_log 并扣减 credit_accounts.balance。
    积分 = ceil(total_tokens / 1000)；余额可以扣到 0，但不为负数（调用方负责幂等与 commit）"""
    total_tokens = int(input_tokens or 0) + int(output_tokens or 0) + int(cache_read_tokens or 0)
    credits_used = int(math.ceil(total_tokens / float(CREDITS_PER_TOKENS))) if total_tokens > 0 else 0
    if total_tokens <= 0:
        return 0
    _ensure_credit_account(conn, agent_id)
    if created_at:
        conn.execute(
            '''INSERT INTO credit_usage_log (agent_id, input_tokens, output_tokens, cache_read_tokens, total_tokens, credits_used, session_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (agent_id, input_tokens, output_tokens, cache_read_tokens, total_tokens, credits_used, session_id, created_at)
        )
    else:
        conn.execute(
            '''INSERT INTO credit_usage_log (agent_id, input_tokens, output_tokens, cache_read_tokens, total_tokens, credits_used, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (agent_id, input_tokens, output_tokens, cache_read_tokens, total_tokens, credits_used, session_id)
        )
    # 扣减余额：允许透支到 0，但不能为负数
    conn.execute(
        "UPDATE credit_accounts SET balance = MAX(balance - ?, 0), total_consumed = total_consumed + ?, updated_at = datetime('now','localtime') WHERE agent_id = ?",
        (credits_used, credits_used, agent_id)
    )
    return credits_used


# ═══════════════════════════════════════════════════
# OpenClaw trajectory token usage 同步
# ═══════════════════════════════════════════════════

import glob as _glob


def _trajectory_base_path():
    """OpenClaw agents 目录"""
    return os.path.expanduser('~/.openclaw/agents')


def _glob_trajectory_files():
    """扫描所有 trajectory JSONL 文件"""
    base = _trajectory_base_path()
    if not os.path.isdir(base):
        return []
    pattern = os.path.join(base, '*', 'sessions', '*.trajectory.jsonl')
    return sorted(_glob.glob(pattern))


def _ts_to_millis(ts):
    """将 trajectory 中的 ts 统一转换为毫秒时间戳"""
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        # 秒或毫秒：大于 1e12 视为毫秒
        ts_num = int(ts)
        if ts_num > 1_000_000_000_000:
            return ts_num
        return ts_num * 1000
    if isinstance(ts, str):
        s = ts.strip()
        if not s:
            return None
        # 尝试纯数字
        try:
            return _ts_to_millis(float(s))
        except ValueError:
            pass
        # ISO 8601
        try:
            # 处理带 Z 与带时区的情况
            if s.endswith('Z'):
                s = s[:-1] + '+00:00'
            dt = datetime.fromisoformat(s)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    return None


def _agent_id_from_session_key(session_key):
    """从 sessionKey 提取 agent/empId。
    支持格式：
      agent:empId:chat / agent:empId:memory_extract → empId
      agent:main:chat-empId（员工 agent 不存在时降级 main，前端仍按员工隔离 session）→ empId
      agent:main:main → main（平台主会话，非员工）
    """
    if not session_key or not isinstance(session_key, str):
        return ''
    parts = session_key.split(':')
    if len(parts) >= 3 and parts[1] == 'main' and parts[2].startswith('chat-'):
        # 降级到 main 的员工会话：真实员工 id 在第三段 chat- 前缀之后
        return parts[2][len('chat-'):]
    if len(parts) >= 2:
        return parts[1]
    return session_key


def _parse_trajectory_event(line):
    """解析单行 trajectory JSONL，仅返回 model.completed 事件的数据"""
    try:
        obj = json.loads(line)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get('type') != 'model.completed':
        return None
    data = obj.get('data') or {}
    usage = data.get('usage') or {}
    ts = _ts_to_millis(obj.get('ts'))
    session_key = obj.get('sessionKey') or ''
    model_id = obj.get('modelId') or ''
    if not ts:
        # 没有时间戳则无法去重，跳过
        return None
    input_tokens = int(usage.get('input') or 0)
    output_tokens = int(usage.get('output') or 0)
    cache_read = int(usage.get('cacheRead') or 0)
    total = int(usage.get('total') or (input_tokens + output_tokens + cache_read))
    return {
        'ts': ts,
        'session_key': session_key,
        'agent_id': _agent_id_from_session_key(session_key),
        'model_id': model_id,
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'cache_read_tokens': cache_read,
        'total_tokens': total,
    }


def _repair_misattributed_credits(conn, active_ids):
    """修复历史错记：聊天降级到 main 时 sessionKey 为 agent:main:chat-<empId>，
    旧版归属逻辑把积分记到了 main 名下，导致总览有消耗记录但员工余额未扣。
    这里按 sessionKey 重新解析归属到正式员工并补扣余额。
    幂等：修复后 agent_id 已变为员工 id，重复执行不会再次命中。
    （调用方负责 commit）"""
    if not active_ids:
        return 0
    fixed = 0
    placeholders = ','.join('?' * len(active_ids))
    active_tuple = tuple(active_ids)
    # credit_usage_log：agent_id 非正式员工、但 sessionKey 能解析出正式员工的记录重新归属
    rows = conn.execute(
        f'SELECT id, agent_id, session_id, credits_used FROM credit_usage_log WHERE agent_id NOT IN ({placeholders})',
        active_tuple
    ).fetchall()
    for r in rows:
        emp_id = _agent_id_from_session_key(r['session_id'])
        if not emp_id or emp_id == r['agent_id'] or emp_id not in active_ids:
            continue
        credits = r['credits_used'] or 0
        conn.execute('UPDATE credit_usage_log SET agent_id = ? WHERE id = ?', (emp_id, r['id']))
        _ensure_credit_account(conn, emp_id)
        conn.execute(
            "UPDATE credit_accounts SET balance = MAX(balance - ?, 0), total_consumed = total_consumed + ?, updated_at = datetime('now','localtime') WHERE agent_id = ?",
            (credits, credits, emp_id)
        )
        fixed += 1
        logger.info(f'  [Credits] 错记归属修复: {r["agent_id"]} -> {emp_id} credits={credits} session={r["session_id"]}')
    # token_usage 同步修复归属，保证按员工统计一致
    rows2 = conn.execute(
        f'SELECT id, agent_id, session_key FROM token_usage WHERE agent_id NOT IN ({placeholders})',
        active_tuple
    ).fetchall()
    for r in rows2:
        emp_id = _agent_id_from_session_key(r['session_key'])
        if emp_id and emp_id != r['agent_id'] and emp_id in active_ids:
            conn.execute('UPDATE token_usage SET agent_id = ? WHERE id = ?', (emp_id, r['id']))
            fixed += 1
    # 清理从未充值过的非员工垃圾账户（纯由错记产生）
    conn.execute(
        f'DELETE FROM credit_accounts WHERE agent_id NOT IN ({placeholders}) AND total_recharged = 0',
        active_tuple
    )
    return fixed


def _sync_token_usage_from_trajectories():
    """扫描 trajectory 文件并写入 token_usage 表，按 ts+session_key 去重"""
    files = _glob_trajectory_files()
    scanned_events = 0
    inserted = 0

    conn = _db_conn()
    try:
        # 先修复历史错记归属（降级 main 的会话被记到 main 名下）；即使没有新 trajectory 也要执行
        active_ids = _get_active_agent_ids()
        repaired = _repair_misattributed_credits(conn, active_ids)
        if repaired:
            logger.info(f'  [Credits] 错记归属修复完成: {repaired} 条')
            conn.commit()
        if not files:
            return {'scannedFiles': 0, 'scannedEvents': 0, 'inserted': 0, 'repaired': repaired}
        for filepath in files:
            try:
                with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        ev = _parse_trajectory_event(line)
                        if not ev:
                            continue
                        scanned_events += 1
                        try:
                            before = conn.total_changes
                            conn.execute('''
                                INSERT OR IGNORE INTO token_usage
                                (id, user_id, agent_id, model_id, session_key,
                                 prompt_tokens, completion_tokens, cache_read_tokens,
                                 total_tokens, ts, source, created_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ''', (
                                uuid.uuid4().hex[:12],
                                '',
                                ev['agent_id'],
                                ev['model_id'],
                                ev['session_key'],
                                ev['input_tokens'],
                                ev['output_tokens'],
                                ev['cache_read_tokens'],
                                ev['total_tokens'],
                                ev['ts'],
                                'trajectory',
                                int(time.time() * 1000)
                            ))
                            if conn.total_changes > before:
                                inserted += 1
                                # 积分制算力管控：1 积分=1000 tokens（向上取整），写入消耗明细并扣减余额
                                # （仅在新插入时扣减，沿用 INSERT OR IGNORE 去重保证幂等）
                                try:
                                    # 只给正式员工扣积分；main 等非员工会话只记 token_usage，不产生积分账户
                                    if ev['agent_id'] and ev['agent_id'] in active_ids:
                                        credit_created_at = datetime.fromtimestamp(ev['ts'] / 1000).strftime('%Y-%m-%d %H:%M:%S')
                                        _record_credit_usage(
                                            conn, ev['agent_id'],
                                            ev['input_tokens'], ev['output_tokens'], ev['cache_read_tokens'],
                                            session_id=ev['session_key'], created_at=credit_created_at
                                        )
                                    elif ev['agent_id']:
                                        logger.info(f'  [Credits] 跳过非员工会话积分扣减: agent_id={ev["agent_id"]} session={ev["session_key"]}')
                                except Exception as credit_err:
                                    logger.error(f'  [Credits] trajectory 积分扣减失败: {credit_err}')
                        except Exception as e:
                            logger.info(f'  [TrajectorySync] insert skipped: {e}')
            except Exception as e:
                logger.error(f'  [TrajectorySync] read failed {filepath}: {e}')
        conn.commit()
    finally:
        conn.close()
    return {'scannedFiles': len(files), 'scannedEvents': scanned_events, 'inserted': inserted}


def _sync_credits_from_token_usage():
    """从 token_usage 表反向同步积分：为已有 token_usage 记录补充 credit_usage_log 条目。
    匹配键：agent_id + ts + total_tokens，已存在则跳过。
    跳过不在 agents.json 中的 agent，并清理其历史记录。"""
    conn = _db_conn()
    synced = 0
    active_ids = _get_active_agent_ids()
    try:
        # 清理非正式 agent 的历史记录
        for table in ('credit_usage_log', 'token_usage', 'credit_accounts'):
            conn.execute(f'DELETE FROM {table} WHERE agent_id NOT IN ({",".join("?"*len(active_ids))})', tuple(active_ids))
        cleaned = conn.total_changes
        if cleaned:
            print(f'  [CreditsSync] cleaned {cleaned} records for inactive agents', flush=True)
        rows = conn.execute('''
            SELECT agent_id, prompt_tokens, completion_tokens, cache_read_tokens, total_tokens, ts, session_key
            FROM token_usage
        ''').fetchall()
        for r in rows:
            if r['agent_id'] not in active_ids:
                continue
            existing = conn.execute(
                'SELECT 1 FROM credit_usage_log WHERE agent_id=? AND created_at=? AND total_tokens=? LIMIT 1',
                (r['agent_id'], r['ts'], r['total_tokens'])
            ).fetchone()
            if existing:
                continue
            try:
                _record_credit_usage(conn, r['agent_id'],
                    r['prompt_tokens'], r['completion_tokens'], r['cache_read_tokens'],
                    session_id=r['session_key'] or '', created_at=r['ts'])
                synced += 1
            except Exception as ce:
                print(f'  [CreditsSync] skip: {ce}', flush=True)
        conn.commit()
    finally:
        conn.close()
    return synced



def _handle_proxy(self):
    """POST /api/proxy（需认证）"""
    auth = _authenticate(self.headers, self.client_address[0], self)
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

    # 解析 body（后续日志和自修改拦截都需要）
    body_json = None
    if body:
        try:
            body_json = json.loads(body.decode('utf-8'))
        except Exception:
            pass

    # 判断是否为 Kimi coding / Anthropic Messages 格式请求
    provider = self.headers.get('X-AI-Provider', '').lower()
    is_kimi_coding = _is_kimi_coding_request(provider, target_url)

    # 检测请求格式（OpenAI / Anthropic）。前端发到 /v1/chat/completions 是 OpenAI；
    # 后续 URL 可能被改写成 /v1/messages（Anthropic），但最终响应要还原成前端期望的格式。
    request_format = _detect_request_format(target_url, body_json)

    # 检测并处理 AI 自修改自然语言意图（在消息到达上游 AI 前拦截）
    if agent_id and body_json:
        try:
            messages = body_json.get('messages', [])
            user_msg = None
            for m in reversed(messages):
                if isinstance(m, dict) and m.get('role') == 'user':
                    user_msg = m
                    break
            if user_msg:
                original_content = user_msg.get('content', '')
                text_content = ''
                content_list = None
                if isinstance(original_content, str):
                    text_content = original_content
                elif isinstance(original_content, list):
                    for item in original_content:
                        if isinstance(item, dict) and item.get('type') == 'text':
                            text_content = item.get('text', '')
                            content_list = original_content
                            break
                if text_content:
                    intent_updates = _detect_self_update_intent(text_content)
                    if intent_updates:
                        ok, su_msg, _ = _apply_agent_self_update(agent_id, intent_updates, source=f'proxy:{auth.user_id}')
                        if ok:
                            field_name, new_value = intent_updates[0]
                            confirmation = f'（系统已根据你的指令更新了你的{field_name}为{new_value}，请在回复中确认已更新）\n\n'
                            if isinstance(original_content, str):
                                user_msg['content'] = confirmation + original_content
                            elif content_list is not None:
                                for item in content_list:
                                    if isinstance(item, dict) and item.get('type') == 'text':
                                        item['text'] = confirmation + item.get('text', '')
                                        break
                            body = json.dumps(body_json, ensure_ascii=False).encode('utf-8')
                            logger.info(f'  [Proxy] self-update intent applied: {field_name}={new_value}')
                        else:
                            logger.error(f'  [Proxy] self-update intent apply failed: {su_msg}')
        except Exception as self_update_err:
            logger.error(f'  [Proxy] self-update intent processing error: {self_update_err}')
            import traceback
            traceback.print_exc()

    forward_headers = {}
    if is_kimi_coding and body_json:
        # Kimi coding API 是 Anthropic 原生端点。请求体若是 OpenAI 格式（含或不含 image_url），
        # 都统一转成 Anthropic Messages 再转发；响应回前端时再按 request_format 还原。
        target_url = _resolve_kimi_coding_target_url(provider)

        if request_format == 'openai':
            logger.info('  [Proxy] OpenAI 格式请求 → 转为 Anthropic Messages 格式后转发给 kimi coding')
            body_json = _transform_openai_to_anthropic(body_json)
            body = json.dumps(body_json, ensure_ascii=False).encode('utf-8')
        else:
            # 兼容旧路径：仅在含 image_url 时兜底转换
            has_openai_image = False
            for msg in body_json.get('messages', []):
                content = msg.get('content', '')
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get('type') == 'image_url':
                            has_openai_image = True
                            break
                if has_openai_image:
                    break
            if has_openai_image:
                logger.info('  [Proxy] 警告: 收到含 image_url 的 OpenAI 格式，正在转换为 Anthropic Messages 格式')
                body_json = _transform_openai_to_anthropic(body_json)
                body = json.dumps(body_json).encode('utf-8')

        ai_api_key = self.headers.get('X-AI-API-Key', '')
        if ai_api_key:
            forward_headers['x-api-key'] = ai_api_key
        forward_headers['anthropic-version'] = ANTHROPIC_VERSION
        forward_headers['Content-Type'] = 'application/json'
        if body:
            forward_headers['Content-Length'] = str(len(body))
    else:
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
    if body_json:
        try:
            body_info = f"model={body_json.get('model','?')} messages={len(body_json.get('messages',[]))}"
        except Exception:
            body_info = f'body_len={len(body)}'
    elif body:
        body_info = f'body_len={len(body)}'
    logger.info(f'  [Proxy] 收到请求 -> {target_url} {body_info}')

    # Kimi coding：构建 key 轮询列表（agent 自己的 key 优先 → KIMI_KEY_POOL 兜底）
    # 401/429 时 mark_failed 当前 key 换下一个；池空返回 503；任何重试都在返回响应头之前完成
    keys_to_try = None
    if is_kimi_coding:
        keys_to_try = []
        # 1) 前端直接传过来的 X-AI-API-Key
        if ai_api_key:
            keys_to_try.append(ai_api_key)
        # 2) agent 在 agents.json 里配置的 apiKey（兼容没传 X-AI-API-Key 的情况）
        agent_stored_key = _get_agent_api_key(agent_id) if agent_id else None
        if agent_stored_key and agent_stored_key not in keys_to_try:
            keys_to_try.append(agent_stored_key)
        # 3) 池里取一个（池空则 None，循环会自动到 503）
        pool_key = KIMI_KEY_POOL.get_key()
        if pool_key and pool_key not in keys_to_try:
            keys_to_try.append(pool_key)
        if not keys_to_try:
            logger.error('[Proxy] Kimi coding 请求无可用 key（agent 无 key + 池空），返回 503')
            self.send_response(503)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'type': 'error',
                'error': {
                    'type': 'api_key_pool_exhausted',
                    'message': 'Kimi API Key 池暂时全部不可用，请稍后重试'
                }
            }).encode('utf-8'))
            return
        logger.info(f'  [Proxy] Kimi coding 准备 {len(keys_to_try)} 个 key 用于轮询')

    try:
        resp = None
        resp_body = None
        resp_content_type = 'application/json'
        last_http_error = None

        if is_kimi_coding:
            # Key 轮询：每个 key 一次 urlopen，401/429 拉黑换下一个
            for key_idx, current_key in enumerate(keys_to_try):
                forward_headers['x-api-key'] = current_key
                masked = (current_key[:10] + '...' + current_key[-4:]) if current_key and len(current_key) > 14 else '****'
                try:
                    req = urllib.request.Request(target_url, data=body, headers=forward_headers, method='POST')
                    ctx = ssl.create_default_context()
                    resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
                    resp_body = resp.read()
                    resp_content_type = resp.headers.get('Content-Type', 'application/json')
                    last_http_error = None
                    logger.info(f'  [Proxy] Kimi coding key #{key_idx+1} 命中: {masked}')
                    break
                except urllib.error.HTTPError as e:
                    # 任何 HTTPError 都先记下来；401/429 走轮换，其他直接抛给外层
                    last_http_error = e
                    if e.code in (401, 429):
                        KIMI_KEY_POOL.mark_failed(current_key)
                        logger.warning(f'  [Proxy] Kimi coding {e.code} 轮换 key ({key_idx+1}/{len(keys_to_try)}): {masked}')
                        # 池大到 size 上限时跳出循环，让外层用 last_http_error 走降级
                        if key_idx >= KIMI_KEY_POOL.size:
                            break
                        continue
                    raise
            # 全部 key 用完还没成功 → 把最后一次错误抛给外层 except 走降级
            if resp is None and last_http_error is not None:
                raise last_http_error
        else:
            # 非 Kimi coding：单次调用，行为不变
            req = urllib.request.Request(target_url, data=body, headers=forward_headers, method='POST')
            ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=PROXY_TIMEOUT, context=ctx)
            resp_body = resp.read()
            resp_content_type = resp.headers.get('Content-Type', 'application/json')

        print(f"[Proxy] 原始响应前200字符: {resp_body[:200].decode('utf-8', errors='replace')}", flush=True)

        # Kimi coding 返回 Anthropic Messages 格式，前端已原生解析，后端直接透传。
        if is_kimi_coding:
            try:
                anthropic_resp = json.loads(resp_body.decode('utf-8', errors='replace'))
                content_items = anthropic_resp.get('content', []) if isinstance(anthropic_resp.get('content'), list) else []
                content_text = ''.join(
                    item.get('text', '') for item in content_items
                    if isinstance(item, dict) and item.get('type') == 'text'
                )
                logger.info(f'  [Proxy] API返回(Anthropic原生) status={resp.status} content_len={len(content_text)} <- {target_url}')

                # 处理 Anthropic tool_use 续调用
                stop_reason = anthropic_resp.get('stop_reason')
                content_types = [c.get('type') for c in content_items if isinstance(c, dict)]
                has_tool_use = any(t == 'tool_use' for t in content_types)
                if stop_reason == 'tool_use' and has_tool_use and isinstance(body_json, dict):
                    continued_body = _continue_anthropic_tool_use(
                        target_url, forward_headers, body_json, anthropic_resp, max_retries=2
                    )
                    if continued_body is not None:
                        resp_body = continued_body
                        logger.info(f'  [Proxy] Anthropic tool_use 续调用完成 <- {target_url}')
            except Exception as e:
                logger.error(f'  [Proxy] Anthropic 响应解析失败: {e}')

            # 若前端是 OpenAI 格式请求，把 Anthropic 响应转回 OpenAI 透传
            if request_format == 'openai':
                try:
                    anthropic_parsed = json.loads(resp_body.decode('utf-8', errors='replace'))
                    if isinstance(anthropic_parsed, dict) and anthropic_parsed.get('type') == 'message':
                        openai_parsed = _transform_anthropic_to_openai(anthropic_parsed)
                        resp_body = json.dumps(openai_parsed, ensure_ascii=False).encode('utf-8')
                        logger.info('  [Proxy] 响应已从 Anthropic 转换为 OpenAI 格式')
                except Exception as conv_err:
                    logger.error(f'  [Proxy] Anthropic -> OpenAI 响应转换失败: {conv_err}')
        else:
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
            logger.info(f'  [Proxy] API返回 status={resp.status}{choices_info} <- {target_url}')

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

        # 兜底：kimi coding 失败时（403 配额、401/429 key 全失效）尝试 minimax 降级
        # 401/429 走完 key 轮询还是 401/429，说明所有可用 key 都失效，再降级才有意义
        fallback_body = None
        if is_kimi_coding and body_json and status in (401, 403, 429):
            try:
                fallback_body = _try_minimax_proxy_fallback(body_json, log_prefix='Proxy', request_format=request_format)
            except Exception as fb_err:
                logger.error(f'  [Proxy] minimax fallback exception: {fb_err}')

        if fallback_body is not None:
            self.send_response(200)
            self._add_cors_headers()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(fallback_body)
            return

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
        logger.error(f'  [Proxy] API错误 status={status} detail={detail} err={err_text} <- {target_url}')

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
        logger.error(f'  ❌ Proxy Network Error: {reason} <- {target_url}')
        self._send_json_error(502, f'Network error: {reason}')

    except TimeoutError:
        logger.error(f'  ❌ Proxy Timeout ({PROXY_TIMEOUT}s) <- {target_url}')
        self._send_json_error(504, f'Request timed out after {PROXY_TIMEOUT}s')

    except Exception as e:
        logger.error(f'  ❌ Proxy Unexpected Error: {e} <- {target_url}')
        self._send_json_error(500, f'Internal proxy error: {str(e)}')

# ═══════════════════════════════════════════════════
# Kimi API 代理（积分管控）— 见 KIMI_API_PROXY_SPEC.md
# ═══════════════════════════════════════════════════

# ═══ Kimi API Key 池：多 key 轮询 + 401/429 失败拉黑自动轮换 ═══
class KimiKeyPool:
    """多 key 轮询；key 被拉黑（默认 30 分钟）期间不参与分配，到期自动恢复。"""

    def __init__(self, keys):
        self._keys = list(keys)
        self._lock = threading.Lock()
        self._index = 0
        self._blocked = {}

    @property
    def size(self):
        return len(self._keys)

    def get_key(self):
        with self._lock:
            now = time.time()
            expired = [k for k, t in self._blocked.items() if now >= t]
            for k in expired:
                del self._blocked[k]
            active_keys = [k for k in self._keys if k not in self._blocked]
            if not active_keys:
                return None
            key = active_keys[self._index % len(active_keys)]
            self._index = (self._index + 1) % len(active_keys)
            return key

    def mark_failed(self, key, block_seconds=1800):
        with self._lock:
            self._blocked[key] = time.time() + block_seconds
            # key 日志脱敏：只显示前10后4位
            masked = f'{key[:10]}...{key[-4:]}' if key and len(key) > 14 else '****'
            logger.warning(f'[KimiKeyPool] Key blocked: {masked}, recover in {block_seconds}s')


# 真实 Kimi API Key 池：环境变量 KIMI_API_KEY（逗号分隔可配多个）优先，否则用内置 key 列表
_env_kimi_keys = [k.strip() for k in os.environ.get('KIMI_API_KEY', '').split(',') if k.strip()]
KIMI_KEY_POOL = KimiKeyPool(_env_kimi_keys or [
    "sk-kimi-o35k9gcgprEzAZ0Q9Kw9bqiPGJyD66qXbe2biTZoZKaBm9DEszUQnSGML7qJBfaE",
    "sk-kimi-EEcskfgXT82jqiOLenbK6x1diNxalfzqnAoX2zajMzfPdvjh16RTLXHjPOZKJ90j",
    "sk-kimi-nl2dFFqoGPKpA6boPerKqeFtaQKySphOnVQ6mLtbrITg5ivOygnh5dtFMDayoqtx",
    "sk-kimi-EKcvRlizd3g4TggDDHOlCyTfOtvr9nTWc47FPKK1uaddgTOojXM2IUpYmfhR4ybN",
])
# 上游 base url：可用环境变量覆盖（便于本地 mock 测试），默认真实 Kimi coding endpoint
KIMI_PROXY_REAL_BASE_URL = os.environ.get('KIMI_PROXY_BASE_URL', '').strip() or 'https://api.kimi.com/coding'


def _extract_last_user_message(body):
    """从 Anthropic messages 请求体中提取最后一条用户消息的纯文本"""
    messages = body.get('messages') or []
    for msg in reversed(messages):
        if not isinstance(msg, dict) or msg.get('role') != 'user':
            continue
        content = msg.get('content')
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = [b.get('text', '') for b in content
                     if isinstance(b, dict) and b.get('type') == 'text']
            return '\n'.join(t for t in texts if t).strip()
    return ''


def _talent_presearch(conn, user_message):
    """达人预搜索：用户消息中提及 active 达人名时，查询完整信息并格式化为简洁文本

    查询逻辑与 GET /api/talents?q=达人名 一致（名称模糊匹配，按粉丝数排序取第一条）。
    返回格式化文本，无匹配时返回空字符串。
    """
    # 1. 取出所有 active 达人名称，子串匹配用户消息（跳过过短名称避免误匹配）
    names = conn.execute(
        "SELECT id, name FROM talents WHERE status='active'"
    ).fetchall()
    matched = []
    for r in names:
        name = (r['name'] or '').strip()
        if len(name) >= 2 and name in user_message and name not in matched:
            matched.append(name)
    if not matched:
        return ''
    print(f'  [TalentPreSearch] matched={matched}', flush=True)

    # 2. 对每个匹配到的达人名，按 /api/talents?q= 同款查询取完整信息
    lines = []
    for name in matched[:3]:  # 最多3个达人，防止 prompt 膨胀
        row = conn.execute(
            "SELECT * FROM talents WHERE status='active' AND LOWER(name) LIKE ? "
            "ORDER BY followers DESC LIMIT 1",
            (f'%{name.lower()}%',)
        ).fetchone()
        if not row:
            continue

        # 3. 粉丝画像四字段（fan_gender/fan_age/fan_region/fan_price_range）是否为空
        fan_fields = {
            '性别': row['fan_gender'], '年龄': row['fan_age'],
            '地域': row['fan_region'], '价格区间': row['fan_price_range'],
        }
        filled = [k for k, v in fan_fields.items() if v and v not in ('{}', '')]
        fans_profile = '已填写(' + ','.join(filled) + ')' if filled else '未填写'

        category = row['fan_category'] or row['category'] or '未知'
        lines.append(
            f"【系统预查到达人信息-以下数据已由系统自动查到，无需再执行搜索，直接使用】"
            f"达人ID: {row['id']}, 名称: {row['name']}, "
            f"粉丝: {row['followers'] or 0}, 品类: {category}, 粉丝画像: {fans_profile}"
        )

    return '\n'.join(lines)


def _prepend_system_context(body, context_text):
    """把记忆上下文注入到 Anthropic 请求体的 system prompt 前部"""
    system = body.get('system')
    if not system:
        body['system'] = context_text
    elif isinstance(system, str):
        body['system'] = context_text + '\n\n' + system
    elif isinstance(system, list):
        body['system'] = [{'type': 'text', 'text': context_text}] + system


def _drop_orphan_tool_messages(msgs):
    """清理裁剪后消息列表中孤立的 tool 调用，避免 Kimi 400（tool_call 无对应 response）。

    1. 反复移除开头的 assistant+tool_calls/tool_use 消息（其 tool_result 可能已被截断），
       直到第一条是 user 或不带 tool 调用的 assistant；
    2. 扫描内部：assistant 带 tool 调用但后面没有紧跟对应 tool_result
       （OpenAI: role=tool + tool_call_id；Anthropic: user 消息 content 中的
       tool_result/tool_use_id），则删除该 assistant 及其孤立的部分 tool 响应。
    """
    def _tool_call_ids(m):
        """提取 assistant 消息中的 tool 调用 id（OpenAI tool_calls / Anthropic tool_use）"""
        ids = []
        tc = m.get('tool_calls')
        if isinstance(tc, list):
            ids += [t.get('id', '') for t in tc if isinstance(t, dict)]
        c = m.get('content')
        if isinstance(c, list):
            ids += [b.get('id', '') for b in c
                    if isinstance(b, dict) and b.get('type') == 'tool_use']
        return [i for i in ids if i]

    def _tool_response_ids(m):
        """提取消息中回答的 tool 调用 id（role=tool / user 消息里的 tool_result 块）"""
        if not isinstance(m, dict):
            return []
        if m.get('role') == 'tool' and m.get('tool_call_id'):
            return [m['tool_call_id']]
        if m.get('role') == 'user' and isinstance(m.get('content'), list):
            return [b['tool_use_id'] for b in m['content']
                    if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id')]
        return []

    def _has_tool_calls(m):
        return isinstance(m, dict) and m.get('role') == 'assistant' and bool(_tool_call_ids(m))

    def _is_pure_tool_response(m):
        """整条消息仅为 tool 响应（role=tool，或 content 全是 tool_result 块的 user 消息）"""
        if not isinstance(m, dict):
            return False
        if m.get('role') == 'tool':
            return True
        c = m.get('content')
        return (m.get('role') == 'user' and isinstance(c, list) and bool(c)
                and all(isinstance(b, dict) and b.get('type') == 'tool_result' for b in c))

    kept = list(msgs)

    # 1. 开头清理：带 tool 调用的 assistant 及其孤立 tool 响应都移除，
    #    直到第一条是 user 或不带 tool 调用的 assistant
    while kept and (_has_tool_calls(kept[0]) or _is_pure_tool_response(kept[0])):
        kept.pop(0)

    # 2. 内部孤立调用清理
    skip = set()
    n = len(kept)
    for idx, m in enumerate(kept):
        if idx in skip or not _has_tool_calls(m):
            continue
        ids = set(_tool_call_ids(m))
        answered = set()
        followers = []
        j = idx + 1
        while j < n:
            resp_ids = _tool_response_ids(kept[j])
            if not resp_ids:
                break
            answered.update(resp_ids)
            followers.append(j)
            j += 1
        if ids and not ids <= answered:
            skip.update(followers)  # 一并移除其部分 tool 响应，避免留下孤立 tool_result
            skip.add(idx)
    kept = [m for idx, m in enumerate(kept) if idx not in skip]

    # 3. 反向验证：user 消息中的 tool_result 必须对应某个 assistant 的 tool_use，
    #    否则为孤立块（如截断残留的 "process:3"），整条或按块移除
    all_tool_use_ids = set()
    for m in kept:
        if _has_tool_calls(m):
            all_tool_use_ids.update(_tool_call_ids(m))
    orphan_ids = []
    drop_idx = set()
    for idx, m in enumerate(kept):
        if not (isinstance(m, dict) and m.get('role') == 'user' and isinstance(m.get('content'), list)):
            continue
        content = m['content']
        if not content:
            continue

        def _is_orphan_tr(b):
            return (isinstance(b, dict) and b.get('type') == 'tool_result'
                    and b.get('tool_use_id') not in all_tool_use_ids)

        orphans = [b for b in content if _is_orphan_tr(b)]
        if not orphans:
            continue
        orphan_ids.extend(b.get('tool_use_id') for b in orphans)
        all_tr = all(isinstance(b, dict) and b.get('type') == 'tool_result' for b in content)
        if all_tr and len(orphans) == len(content):
            drop_idx.add(idx)  # 整条全是孤立 tool_result → 删除
        else:
            # 混合了 text 等正常块：只移除孤立 tool_result 块，保留其余内容（拷贝不改原对象）
            new_m = dict(m)
            new_m['content'] = [b for b in content if not _is_orphan_tr(b)]
            kept[idx] = new_m
    if drop_idx:
        kept = [m for idx, m in enumerate(kept) if idx not in drop_idx]
    if orphan_ids:
        logger.info(f'  [KimiProxy] 移除孤立 tool_result, tool_use_id={orphan_ids}')

    # 4. 反向清理后再做一轮开头检查，移除新暴露的头部孤立消息
    while kept and (_has_tool_calls(kept[0]) or _is_pure_tool_response(kept[0])):
        kept.pop(0)

    return kept


def _strip_unpaired_tool_assistants(msgs):
    """发送前完整性校验：assistant 的每个 tool 调用必须由紧跟的 tool 响应全部回答，
    否则删除该 assistant 及其部分响应（不追加假 tool_result）。

    返回 (cleaned_msgs, removed_ids)。正常路径下 _drop_orphan_tool_messages 已清理过，
    本函数是裁剪后发送前的最后一道防线。
    """
    def _call_ids(m):
        ids = []
        tc = m.get('tool_calls')
        if isinstance(tc, list):
            ids += [t.get('id', '') for t in tc if isinstance(t, dict)]
        c = m.get('content')
        if isinstance(c, list):
            ids += [b.get('id', '') for b in c
                    if isinstance(b, dict) and b.get('type') == 'tool_use']
        return [i for i in ids if i]

    def _resp_ids(m):
        if not isinstance(m, dict):
            return []
        if m.get('role') == 'tool' and m.get('tool_call_id'):
            return [m['tool_call_id']]
        if m.get('role') == 'user' and isinstance(m.get('content'), list):
            return [b['tool_use_id'] for b in m['content']
                    if isinstance(b, dict) and b.get('type') == 'tool_result' and b.get('tool_use_id')]
        return []

    skip = set()
    removed = []
    n = len(msgs)
    for idx, m in enumerate(msgs):
        if idx in skip:
            continue
        if not (isinstance(m, dict) and m.get('role') == 'assistant'):
            continue
        ids = _call_ids(m)
        if not ids:
            continue
        answered = set()
        followers = []
        j = idx + 1
        while j < n:
            rids = _resp_ids(msgs[j])
            if not rids:
                break
            answered.update(rids)
            followers.append(j)
            j += 1
        missing = [i for i in ids if i not in answered]
        if missing:
            removed.extend(missing)
            skip.add(idx)
            skip.update(followers)  # 部分响应一并移除，避免留下孤立 tool_result
    if not skip:
        return msgs, []
    return [m for i, m in enumerate(msgs) if i not in skip], removed


def _get_agent_api_key(agent_id):
    """从 data/agents.json 读取该员工的 apiKey；找不到或为空返回 None（调用方 fallback 全局 key）"""
    if not agent_id:
        return None
    try:
        agents = _read_json(AGENTS_FILE, [])
        if not isinstance(agents, list):
            return None
        for a in agents:
            if isinstance(a, dict) and a.get('id') == agent_id:
                key = (a.get('apiKey') or '').strip()
                return key or None
    except Exception as e:
        logger.error(f'  [KimiProxy] 读取 agent apiKey 失败 {agent_id}: {e}')
    return None


def _get_agent_role(agent_id):
    """从 data/agents.json 读取该员工的 role 字段；找不到返回 None"""
    if not agent_id:
        return None
    try:
        agents = _read_json(AGENTS_FILE, [])
        if not isinstance(agents, list):
            return None
        for a in agents:
            if isinstance(a, dict) and a.get('id') == agent_id:
                return a.get('role') or None
    except Exception as e:
        logger.error(f'  [Vision] 读取 agent role 失败 {agent_id}: {e}')
    return None


def _memory_pipeline_llm_call(model=None, api_key=None):
    """构造供 memory_pipeline 使用的 LLM 调用函数，签名 (prompt: str) -> str"""
    def _call(prompt):
        effective_key = api_key or KIMI_KEY_POOL.get_key()
        if not effective_key:
            raise RuntimeError('Kimi API Key 池已空，无法调用 LLM')
        req_body = json.dumps({
            'model': model or 'kimi-for-coding',
            'max_tokens': 2000,
            'messages': [{'role': 'user', 'content': prompt}],
        }).encode('utf-8')
        req = urllib.request.Request(
            KIMI_PROXY_REAL_BASE_URL + '/v1/messages',
            data=req_body,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': effective_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST')
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
        parts = data.get('content', [])
        return ''.join(p.get('text', '') for p in parts
                       if isinstance(p, dict) and p.get('type') == 'text')
    return _call


def _parse_vision_description(raw_desc):
    """解析 vision 响应：兼容 markdown 代码块包裹的 JSON 和纯文本"""
    if not raw_desc:
        return raw_desc
    import re
    # 去掉 ```json ... ``` 包裹
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', raw_desc, re.DOTALL)
    if m:
        json_str = m.group(1).strip()
        try:
            obj = json.loads(json_str)
            return obj.get('description', json_str)
        except (json.JSONDecodeError, AttributeError):
            return json_str
    return raw_desc


def _handle_vision_describe(self):
    """POST /api/vision/describe — 图片转文字描述。
    body: {images: [{base64}|{url}|string, ...]}（最多9张，与前端发图上限一致）
    返回: {text: "【图片1描述】xxx\n【图片2描述】xxx"}，供前端拼进纯文本消息后再走 OpenClaw。"""
    auth = _authenticate(self.headers, self.client_address[0], self)
    if not auth.is_authenticated:
        self._send_auth_error(auth.error, auth.status)
        return

    body = self._read_body()
    if not body:
        self._send_json(400, {'error': '无效的请求体'})
        return
    images = body.get('images') or []
    if not isinstance(images, list) or not images:
        self._send_json(400, {'error': '缺少 images 字段'})
        return
    agent_id = body.get('agent_id')  # 可选：传入则优先用该员工自己的 apiKey（同 Kimi proxy 逻辑）
    agent_role = _get_agent_role(agent_id)  # 查询该员工角色：'商务' 走专用提取提示词

    parts = []
    failed = 0
    for idx, img in enumerate(images[:9], 1):
        if isinstance(img, dict):
            b64 = img.get('base64') or img.get('url') or ''
        else:
            b64 = str(img)
        desc = _call_kimi_vision(b64, agent_id=agent_id, role=agent_role)
        desc = _parse_vision_description(desc)  # 新增：清理格式
        if not desc:
            failed += 1
        parts.append(f'【图片{idx}描述】{desc if desc else "（图片识别失败）"}')
    total = len(images[:9])
    logger.info(f'[Vision] 图片描述完成 total={total} failed={failed}: {chr(10).join(parts)[:500]}')
    self._send_json(200, {'text': '\n'.join(parts), 'total': total, 'failed': failed})


def _handle_proxy_kimi(self):
    """POST /api/proxy/kimi/* — Kimi API代理，带积分管控（OpenClaw/飞书链路专用）"""

    # 1. 从请求头提取API Key（anthropic格式用x-api-key）
    proxy_key = self.headers.get('x-api-key', '') or ''
    if not proxy_key:
        auth_header = self.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            proxy_key = auth_header[7:]

    # 2. 从proxy_key提取agent_id（格式: "proxy_<agent_id>"）
    if proxy_key.startswith('proxy_'):
        agent_id = proxy_key[6:]  # 去掉"proxy_"前缀
    else:
        # 非代理key，直接转发（兼容旧配置）
        agent_id = None

    # 计时工具：在关键步骤前后打点，日志可直接看出各环节耗时
    _t_start = time.perf_counter()
    def _timing(label, since):
        print(f'  [KimiProxy] TIMING {label}: {time.perf_counter() - since:.3f}s', flush=True)

    # 2.5 优先使用该员工在 agents.json 中配置的 apiKey 转发，未配置则从 key 池轮询取一个
    agent_api_key = _get_agent_api_key(agent_id) if agent_id else None
    if not agent_api_key:
        agent_api_key = KIMI_KEY_POOL.get_key()
    if agent_api_key is None:
        # key 池全部处于拉黑冷却期，无可用 key
        logger.error('[KimiProxy] Key 池已空，返回 503')
        self.send_response(503)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({
            'type': 'error',
            'error': {
                'type': 'api_key_pool_exhausted',
                'message': 'Kimi API Key 池暂时全部不可用，请稍后重试'
            }
        }).encode())
        return

    # 3. 检查积分余额
    if agent_id:
        _t = time.perf_counter()
        balance, has_credits = _check_credit_balance(agent_id)
        _timing('credit_check', _t)
        if not has_credits:
            # 返回anthropic格式错误
            self.send_response(403)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                'type': 'error',
                'error': {
                    'type': 'insufficient_credits',
                    'message': '积分余额不足，请联系管理员充值'
                }
            }).encode())
            return

    # 4. 读取请求体
    _t = time.perf_counter()
    body = self._read_body()
    _timing('read_body', _t)
    if not body:
        self._send_json_error(400, 'Empty body')
        return

    # 4.1 入口日志：记录请求关键信息（agent_id/messages 条数/最后一条用户消息/大小），
    #     便于事后定位是哪一步、哪个请求出问题
    # DIAG: 打印最后一条 user 消息的完整 content 结构
    try:
        _diag_last_user = None
        for _m in (body.get('messages') or []):
            if isinstance(_m, dict) and _m.get('role') == 'user':
                _diag_last_user = _m
        if _diag_last_user is not None:
            _diag_content = _diag_last_user.get('content')
            _diag_json = json.dumps(_diag_content, ensure_ascii=False)[:500]
            if isinstance(_diag_content, list):
                _diag_types = [b.get('type', '?') if isinstance(b, dict) else type(b).__name__ for b in _diag_content]
            else:
                _diag_types = type(_diag_content).__name__
            print(f'  [KimiProxy] DIAG last_user content_types={_diag_types} content={_diag_json!r}', flush=True)
    except Exception as _diag_err:
        print(f'  [KimiProxy] DIAG异常: {_diag_err}', flush=True)
    user_message = _extract_last_user_message(body)
    _msgs = body.get('messages') or []
    _img_info = []
    for _m in (_msgs if isinstance(_msgs, list) else []):
        _content = _m.get('content')
        if isinstance(_content, list):
            for _block in _content:
                if isinstance(_block, dict) and _block.get('type') == 'image':
                    _src = _block.get('source', {})
                    _data = _src.get('data', '')
                    _img_info.append(f'type={_src.get("type","?")} media={_src.get("media_type","?")} size={len(_data)}chars')
    _body_str = json.dumps(body, ensure_ascii=False)
    logger.info(
        f'[KimiProxy] 请求进入: agent_id={agent_id} images={_img_info} '
        f'messages={len(_msgs) if isinstance(_msgs, list) else "?"} '
        f'last_user={(user_message or "")[:200]!r} '
        f'body_bytes={len(_body_str.encode("utf-8"))} ~tokens={len(_body_str) // 4}'
    )

    # 4.5 Memory Pipeline：召回记忆注入 system prompt + 保存 L0 对话记录
    #     （失败不阻断代理转发）
    if agent_id and user_message:
        _t = time.perf_counter()
        conn = _db_conn()
        try:
            # 召回相关记忆（L3画像/L2场景/L1事实原子，带Token预算控制）
            recall = memory_pipeline.recall_with_budget(conn, agent_id, user_message)
            memory_ctx = '\n\n'.join(p for p in (
                recall.get('prepend_context', ''),
                recall.get('append_system_context', ''),
            ) if p)
            if memory_ctx:
                _prepend_system_context(body, memory_ctx)
            # 保存 L0 结构化对话记录
            memory_pipeline.save_conversation(
                conn, agent_id,
                session_id=agent_id,
                turn_id=int(time.time()),
                user_content=user_message)
        except Exception as e:
            print(f'  [MemoryPipeline] recall/save failed: {e}', flush=True)
        finally:
            conn.close()
        _timing('memory_recall', _t)

    # 4.6 达人预搜索：搜索所有user消息中的达人名（OpenClaw会把用户消息包装在runtime context里）
    all_user_text = user_message or ''
    for m in (body.get('messages') or []):
        if m.get('role') == 'user':
            c = m.get('content')
            if isinstance(c, str):
                all_user_text += ' ' + c
            elif isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get('type') == 'text':
                        all_user_text += ' ' + b.get('text', '')
    if all_user_text.strip():
        _t = time.perf_counter()
        conn = _db_conn()
        try:
            talent_ctx = _talent_presearch(conn, all_user_text)
            if talent_ctx:
                _prepend_system_context(body, talent_ctx)
                msgs = body.get('messages') or []
                for m in reversed(msgs):
                    if isinstance(m, dict) and m.get('role') == 'user':
                        c = m.get('content')
                        if isinstance(c, str):
                            m['content'] = talent_ctx + '\n\n' + c
                        elif isinstance(c, list):
                            c.insert(0, {'type': 'text', 'text': talent_ctx})
                        break
        except Exception as e:
            print(f'  [TalentPreSearch] failed: {e}', flush=True)
        finally:
            conn.close()
        _timing('talent_presearch', _t)

    # 4.7 修补缺失的 tool response 消息
    #     OpenClaw exec 工具执行后，有时不会在 messages 中附带 tool role 的 response，
    #     导致 Kimi API 返回 400: "tool_call_ids did not have response messages: exec:0"
    #     这里在转发前扫描 messages，为缺失的 tool_call 补全 response。
    messages = body.get('messages') or []
    if isinstance(messages, list) and agent_id:
        _t = time.perf_counter()
        print(f'  [KimiProxy] DEBUG: agent_id={agent_id} messages_count={len(messages)}', flush=True)
        for idx, m in enumerate(messages):
            if not isinstance(m, dict):
                continue
            role = m.get('role', '?')
            tc = m.get('tool_calls')
            content = m.get('content')
            has_tool_use = isinstance(content, list) and any(isinstance(c, dict) and c.get('type') == 'tool_use' for c in content)
            if role == 'assistant' and (tc or has_tool_use):
                tc_ids = [t.get('id','') for t in tc] if tc else [c.get('id','') for c in content if isinstance(c, dict) and c.get('type') == 'tool_use']
                print(f'  [KimiProxy] DEBUG: msg[{idx}] role=assistant has_tool_calls={bool(tc)} has_tool_use={has_tool_use} ids={tc_ids}', flush=True)
            if role in ('tool', 'user') and isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get('type') in ('tool_result',):
                        print(f'  [KimiProxy] DEBUG: msg[{idx}] role={role} tool_result_id={c.get("tool_use_id","")}', flush=True)
        patched = []
        modified = False
        for idx, m in enumerate(messages):
            patched.append(m)
            if not (isinstance(m, dict) and m.get('role') == 'assistant'):
                continue
            # OpenAI 格式：assistant.tool_calls
            tool_calls = m.get('tool_calls') or []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                tc_id = tc.get('id', '')
                if not tc_id:
                    continue
                has_resp = any(
                    isinstance(rm, dict) and rm.get('role') == 'tool' and rm.get('tool_call_id') == tc_id
                    for rm in messages[idx + 1:]
                )
                if has_resp:
                    continue
                # 查询 tool_calls 表获取结果
                tool_output = None
                try:
                    conn = _db_conn()
                    try:
                        row = conn.execute(
                            'SELECT output FROM tool_calls WHERE agent_id=? AND tool_call_id=? ORDER BY created_at DESC LIMIT 1',
                            (agent_id, tc_id)
                        ).fetchone()
                        if row:
                            tool_output = row[0]
                    finally:
                        conn.close()
                except Exception:
                    pass
                if not tool_output:
                    tool_output = '[工具执行完成，无输出记录]'
                patched.append({
                    'role': 'tool',
                    'tool_call_id': tc_id,
                    'content': tool_output if isinstance(tool_output, str) else json.dumps(tool_output, ensure_ascii=False)
                })
                modified = True
                print(f'  [KimiProxy] 补丁: tool_call_id={tc_id} 插入tool response', flush=True)
            # Anthropic 格式：content 列表中含 tool_use
            content = m.get('content')
            if isinstance(content, list):
                for item in content:
                    if not (isinstance(item, dict) and item.get('type') == 'tool_use'):
                        continue
                    tu_id = item.get('id', '')
                    if not tu_id:
                        continue
                    has_resp = False
                    for rm in messages[idx + 1:]:
                        rm_c = rm.get('content') if isinstance(rm, dict) else None
                        if isinstance(rm_c, list):
                            for rc in rm_c:
                                if isinstance(rc, dict) and rc.get('type') == 'tool_result' and rc.get('tool_use_id') == tu_id:
                                    has_resp = True
                                    break
                        if has_resp:
                            break
                    if has_resp:
                        continue
                    tool_output = None
                    try:
                        conn = _db_conn()
                        try:
                            row = conn.execute(
                                'SELECT output FROM tool_calls WHERE agent_id=? AND tool_call_id=? ORDER BY created_at DESC LIMIT 1',
                                (agent_id, tu_id)
                            ).fetchone()
                            if row:
                                tool_output = row[0]
                        finally:
                            conn.close()
                    except Exception:
                        pass
                    if not tool_output:
                        tool_output = '[工具执行完成，无输出记录]'
                    patched.append({
                        'role': 'user',
                        'content': [{'type': 'tool_result', 'tool_use_id': tu_id, 'content': tool_output if isinstance(tool_output, str) else json.dumps(tool_output, ensure_ascii=False)}]
                    })
                    modified = True
                    print(f'  [KimiProxy] 补丁: tool_use_id={tu_id} 插入tool_result (Anthropic格式)', flush=True)
        if modified:
            body['messages'] = patched
            print(f'  [KimiProxy] 消息修补完成: {len(messages)} -> {len(patched)} 条', flush=True)
        _timing('tool_patch', _t)

    # 4.8 裁剪 messages：保留第一条 system（内容超 4000 字符截断并追加提示）
    #     + 最近 14 条对话消息（user/assistant/tool），中间历史不发给 Kimi，
    #     控制 token 规模、降低响应延迟与 400 概率；任何异常降级为不裁剪
    try:
        _msgs = body.get('messages') or []
        if isinstance(_msgs, list):
            _orig_count = len(_msgs)
            _sys_msgs = [m for m in _msgs if isinstance(m, dict) and m.get('role') == 'system']
            _chat_msgs = [m for m in _msgs if isinstance(m, dict) and m.get('role') != 'system']
            _kept = []
            _sys_chars = 0
            if _sys_msgs:
                _sys = _sys_msgs[0]
                _content = _sys.get('content')
                if isinstance(_content, str):
                    _sys_chars = len(_content)
                    if _sys_chars > 4000:
                        _sys = dict(_sys)
                        _sys['content'] = _content[:4000] + '......[系统提示已截断]'
                elif isinstance(_content, list):
                    _sys_chars = sum(len(c.get('text', '')) for c in _content if isinstance(c, dict))
                _kept.append(_sys)
            _kept.extend(_drop_orphan_tool_messages(_chat_msgs[-14:]))
            logger.info(f'[KimiProxy] 裁剪前 messages={_orig_count} 裁剪后={len(_kept)} system_chars={_sys_chars}')
            body['messages'] = _kept
    except Exception as e:
        logger.error(f'[KimiProxy] 消息裁剪失败，降级为不裁剪: {e}')

    # 4.9 裁剪后完整性校验：assistant 的 tool 调用必须由紧跟的响应全部回答，
    #     配对缺失则删除该 assistant 及其部分响应（不追加假 tool_result）
    try:
        _final_msgs = body.get('messages') or []
        if isinstance(_final_msgs, list):
            _cleaned, _removed_ids = _strip_unpaired_tool_assistants(_final_msgs)
            if _removed_ids:
                body['messages'] = _cleaned
                logger.info(
                    f'[KimiProxy] 裁剪后校验: 移除配对缺失消息, '
                    f'tool_call_ids={_removed_ids} messages={len(_final_msgs)} -> {len(_cleaned)}'
                )
    except Exception as e:
        logger.error(f'[KimiProxy] 裁剪后校验失败，跳过: {e}')

    # 4.95 拆分混合 user 消息：content 同时含 tool_result 和 text 块时拆成两条
    #     （第一条全 tool_result，第二条全 text），其他消息不动
    try:
        _msgs3 = body.get('messages') or []
        if isinstance(_msgs3, list):
            _split_count = 0
            _new_msgs = []
            for m in _msgs3:
                if isinstance(m, dict) and m.get('role') == 'user' and isinstance(m.get('content'), list):
                    _content = m['content']
                    _tr = [b for b in _content if isinstance(b, dict) and b.get('type') == 'tool_result']
                    _tx = [b for b in _content if isinstance(b, dict) and b.get('type') == 'text']
                    # 仅当整条消息恰好由 text+tool_result 组成且两者都有时拆分
                    if _tr and _tx and len(_tr) + len(_tx) == len(_content):
                        _split_count += 1
                        _new_msgs.append({'role': 'user', 'content': _tr})
                        _new_msgs.append({'role': 'user', 'content': _tx})
                        continue
                _new_msgs.append(m)
            if _split_count:
                body['messages'] = _new_msgs
            logger.info(f'[KimiProxy] 拆分混合消息: {_split_count}条')
    except Exception as e:
        logger.error(f'[KimiProxy] 拆分混合消息失败，跳过: {e}')

    # 5. 构造转发请求到真实Kimi API
    # 提取原始请求路径中的子路径（如/v1/messages）
    path = self._normalize_path(self.path)
    path_suffix = path[len('/api/proxy/kimi'):]
    if not path_suffix:
        path_suffix = '/v1/messages'
    target_url = KIMI_PROXY_REAL_BASE_URL + path_suffix

    # 构造请求头（用员工自己的 apiKey 或 key 池取到的 key 替换 proxy key；
    # agent_api_key 在步骤 2.5 已保证非 None）
    forward_headers = {
        'Content-Type': 'application/json',
        'x-api-key': agent_api_key,
        'anthropic-version': self.headers.get('anthropic-version', '2023-06-01'),
    }

    req_body = json.dumps(body).encode('utf-8')
    forward_headers['Content-Length'] = str(len(req_body))

    # 6. 判断是否为流式请求
    is_streaming = body.get('stream', False)

    # 7. 转发请求
    req = urllib.request.Request(target_url, data=req_body, headers=forward_headers, method='POST')

    retry_count = 0
    MAX_RETRIES = 2
    current_key = agent_api_key  # 当前转发使用的 key，401/429 时轮换
    key_retry_count = 0
    while True:
        try:
            _t = time.perf_counter()
            resp = urllib.request.urlopen(req, timeout=120)
            _timing('kimi_api_ttfb', _t)  # urlopen 返回即收到响应头（首字节）
            break
        except urllib.error.HTTPError as e:
            err_body = e.read()
            err_text = err_body.decode('utf-8', errors='replace')
            # ERROR 日志：脱敏后的转发 key（前10后4位）+ 状态码 + 响应 body 前 500 字符
            _fwd_key = forward_headers.get('x-api-key', '')
            _masked = (_fwd_key[:10] + '...' + _fwd_key[-4:]) if len(_fwd_key) > 14 else '****'
            logger.error(
                f'[KimiProxy] Kimi API 调用失败: agent_id={agent_id} status={e.code} '
                f'x-api-key={_masked} resp_body[:500]={err_text[:500]!r}'
            )

            # 401/429：当前 key 失效或被限流，拉黑后取池内下一个 key 重试，
            # 最多重试 key 池大小次；员工自带 key 失败时也会借此回落到池内 key
            if e.code in (401, 429) and key_retry_count < KIMI_KEY_POOL.size:
                key_retry_count += 1
                KIMI_KEY_POOL.mark_failed(current_key)
                next_key = KIMI_KEY_POOL.get_key()
                if next_key is None:
                    logger.error('[KimiProxy] Key 池已空，返回 503')
                    self.send_response(503)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({
                        'type': 'error',
                        'error': {
                            'type': 'api_key_pool_exhausted',
                            'message': 'Kimi API Key 池暂时全部不可用，请稍后重试'
                        }
                    }).encode())
                    return
                current_key = next_key
                forward_headers['x-api-key'] = current_key
                req = urllib.request.Request(target_url, data=req_body, headers=forward_headers, method='POST')
                logger.info(f'[KimiProxy] {e.code} 轮换 key 重试 ({key_retry_count}/{KIMI_KEY_POOL.size})')
                continue

            # 400时dump完整messages到文件用于调试
            if e.code == 400:
                try:
                    dump_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', f'kimi_400_dump_{agent_id}_{int(time.time())}.json')
                    with open(dump_path, 'w', encoding='utf-8') as df:
                        json.dump({'error': err_text, 'messages': body.get('messages', []), 'agent_id': agent_id}, df, ensure_ascii=False, indent=2)
                    print(f'  [KimiProxy] 400 dump saved: {dump_path}', flush=True)
                except Exception:
                    pass

            if e.code == 400 and 'tool_call_ids did not have response' in err_text and retry_count < MAX_RETRIES:
                retry_count += 1
                import re as _re
                match = _re.search(r"did not have response messages: ([^\"]+)", err_text)
                if not match:
                    self.send_response(e.code)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(err_body)
                    return
                missing_raw = match.group(1).strip().strip('"').strip("'")
                missing_ids = [mid.strip() for mid in missing_raw.split(',') if mid.strip()]
                print(f'  [KimiProxy] 400自动修复 (retry {retry_count}/{MAX_RETRIES}): 缺失={missing_ids}', flush=True)

                msgs = body.get('messages', [])
                exec_counter = 0
                exec_map = {}
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    content = m.get('content')
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get('type') == 'tool_use':
                                exec_map[f'exec:{exec_counter}'] = c.get('id', '')
                                exec_counter += 1
                    tc = m.get('tool_calls')
                    if tc:
                        for t in tc:
                            if isinstance(t, dict):
                                exec_map[f'exec:{exec_counter}'] = t.get('id', '')
                                exec_counter += 1

                patched_msgs = list(msgs)
                any_fixed = False
                for mid in missing_ids:
                    target_id = exec_map.get(mid, mid)
                    has_resp = False
                    for rm in msgs:
                        if not isinstance(rm, dict):
                            continue
                        rm_c = rm.get('content')
                        if isinstance(rm_c, list):
                            for rc in rm_c:
                                if isinstance(rc, dict) and rc.get('type') == 'tool_result' and rc.get('tool_use_id') == target_id:
                                    has_resp = True
                                    break
                        if has_resp:
                            break
                        if rm.get('role') == 'tool' and rm.get('tool_call_id') == target_id:
                            has_resp = True
                            break
                    if has_resp:
                        # 配对已存在却仍报缺失：说明问题不在消息配对（可能是 id 映射或格式问题），
                        # 继续删改消息只会越修越坏，直接返回原始错误让上层重试
                        print(f'  [KimiProxy] 400自动修复: {mid} -> {target_id} 已有配对 tool_result，放弃修复，返回原始错误', flush=True)
                        self.send_response(e.code)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(err_body)
                        return
                    else:
                        # 完全没有tool_result，从数据库补全
                        tool_output = None
                        try:
                            conn = _db_conn()
                            try:
                                row = conn.execute('SELECT output FROM tool_calls WHERE agent_id=? AND tool_call_id=? ORDER BY created_at DESC LIMIT 1', (agent_id, target_id)).fetchone()
                                if row:
                                    tool_output = row[0]
                                if not tool_output:
                                    row = conn.execute('SELECT output FROM tool_calls WHERE agent_id=? AND tool_call_id=? ORDER BY created_at DESC LIMIT 1', (agent_id, mid)).fetchone()
                                    if row:
                                        tool_output = row[0]
                            finally:
                                conn.close()
                        except Exception:
                            pass
                        if not tool_output:
                            tool_output = '[工具执行完成，无输出记录]'
                        patched_msgs.append({
                            'role': 'user',
                            'content': [{'type': 'tool_result', 'tool_use_id': target_id, 'content': tool_output if isinstance(tool_output, str) else json.dumps(tool_output, ensure_ascii=False)}]
                        })
                        any_fixed = True
                        print(f'  [KimiProxy] 400自动修复: 追加tool_result for {mid} -> {target_id}', flush=True)

                if any_fixed:
                    body['messages'] = patched_msgs
                    req_body = json.dumps(body).encode('utf-8')
                    forward_headers['Content-Length'] = str(len(req_body))
                    req = urllib.request.Request(target_url, data=req_body, headers=forward_headers, method='POST')
                    print(f'  [KimiProxy] 400自动修复: 重试中... messages {len(msgs)} -> {len(patched_msgs)}', flush=True)
                    continue
                else:
                    print(f'  [KimiProxy] 400自动修复: 无法修复，返回原始错误', flush=True)

            # 兜底：上游 403 时尝试 minimax 降级（支持流式和非流式；流式场景拿 minimax
            # 的非流式完整回复后包装成 Anthropic messages 流式 SSE 返回给 OpenClaw 网关）
            # OpenClaw 的 kimi_proxy_* provider 都配的 api: 'anthropic-messages'，必须吐
            # Anthropic 风格的 SSE（message_start / content_block_* / message_delta / message_stop），
            # 而不是 OpenAI 的 chat.completion.chunk。格式对不上 OpenClaw 解析不了。
            fallback_body = None
            if e.code == 403:
                try:
                    # 强制 request_format='anthropic'，让 fallback 返回 Anthropic 格式 JSON；
                    # 下面 SSE 包装从 content[0].text 提取文本
                    fallback_body = _try_minimax_proxy_fallback(body, log_prefix='KimiProxy', request_format='anthropic')
                except Exception as fb_err:
                    logger.error(f'  [KimiProxy] minimax fallback exception: {fb_err}')
            if fallback_body is not None:
                if is_streaming:
                    # 把非流式 minimax Anthropic 响应包装为 Anthropic messages 流式 SSE
                    try:
                        fb_data = json.loads(fallback_body.decode('utf-8', errors='replace'))
                        # 从 Anthropic 格式里抠文本：data.content[0].text
                        fb_text = ''
                        if isinstance(fb_data, dict) and isinstance(fb_data.get('content'), list) and fb_data['content']:
                            for blk in fb_data['content']:
                                if isinstance(blk, dict) and blk.get('type') == 'text' and blk.get('text'):
                                    fb_text = blk['text']
                                    break
                        # 输出 token 数（minimax 给的可能叫 input_tokens / output_tokens）
                        in_tok = 0
                        out_tok = 0
                        if isinstance(fb_data.get('usage'), dict):
                            in_tok = fb_data['usage'].get('input_tokens', 0) or 0
                            out_tok = fb_data['usage'].get('output_tokens', 0) or 0
                        msg_id = 'msg-minimax-fallback-' + str(int(time.time() * 1000))
                        model_name = 'minimax-fallback'

                        # Anthropic messages streaming event 序列
                        def _sse_event(event_name, data_obj):
                            return f'event: {event_name}\ndata: {json.dumps(data_obj, ensure_ascii=False)}\n\n'

                        sse_parts = []
                        # 1) message_start
                        sse_parts.append(_sse_event('message_start', {
                            'type': 'message_start',
                            'message': {
                                'id': msg_id,
                                'type': 'message',
                                'role': 'assistant',
                                'content': [],
                                'model': model_name,
                                'stop_reason': None,
                                'stop_sequence': None,
                                'usage': {'input_tokens': in_tok, 'output_tokens': 0}
                            }
                        }))
                        # 2) content_block_start
                        sse_parts.append(_sse_event('content_block_start', {
                            'type': 'content_block_start',
                            'index': 0,
                            'content_block': {'type': 'text', 'text': ''}
                        }))
                        # 3) content_block_delta（把整段文本一次推过去）
                        if fb_text:
                            sse_parts.append(_sse_event('content_block_delta', {
                                'type': 'content_block_delta',
                                'index': 0,
                                'delta': {'type': 'text_delta', 'text': fb_text}
                            }))
                        # 4) content_block_stop
                        sse_parts.append(_sse_event('content_block_stop', {
                            'type': 'content_block_stop',
                            'index': 0
                        }))
                        # 5) message_delta
                        sse_parts.append(_sse_event('message_delta', {
                            'type': 'message_delta',
                            'delta': {
                                'stop_reason': 'end_turn',
                                'stop_sequence': None
                            },
                            'usage': {'output_tokens': out_tok}
                        }))
                        # 6) message_stop
                        sse_parts.append(_sse_event('message_stop', {
                            'type': 'message_stop'
                        }))

                        sse_payload = ''.join(sse_parts).encode('utf-8')
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Cache-Control', 'no-cache')
                        self.end_headers()
                        self.wfile.write(sse_payload)
                        print(f'  [KimiProxy] 403 minimax降级(Anthropic流式包装): text_len={len(fb_text)}', flush=True)
                        return
                    except Exception as sse_err:
                        logger.error(f'  [KimiProxy] minimax SSE 包装失败，降级为非流式返回: {sse_err}')
                        # 包装失败时退回到原始非流式响应（仍能让 OpenClaw 拿到内容）
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(fallback_body)
                return

            self.send_response(e.code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(err_body)
            return
        except Exception as e:
            self._send_json_error(502, f'Proxy error: {str(e)}')
            return

    # 8. 处理响应
    if is_streaming:
        # 流式响应：逐行转发SSE，同时解析usage
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()

        input_tokens = 0
        output_tokens = 0
        buffer = b''
        _chunk_count = 0
        _first_chunk_logged = False

        _t = time.perf_counter()
        for chunk in iter(lambda: resp.read(4096), b''):
            _chunk_count += 1
            # 前3个chunk打样本日志
            if not _first_chunk_logged and _chunk_count <= 3:
                sample = chunk[:300].decode('utf-8', errors='replace')
                print(f'  [KimiProxy] SSE chunk#{_chunk_count} len={len(chunk)} sample={sample!r}', flush=True)
                if _chunk_count == 3:
                    _first_chunk_logged = True
            # 转发给客户端
            self.wfile.write(chunk)
            self.wfile.flush()

            # 解析SSE事件提取usage
            buffer += chunk
            while b'\n\n' in buffer:
                event_str, buffer = buffer.split(b'\n\n', 1)
                try:
                    event_text = event_str.decode('utf-8')
                    data_str = None
                    for line in event_text.split('\n'):
                        if line.startswith('data:'):
                            data_str = line[5:].strip()
                            break
                    if data_str and data_str != '[DONE]':
                            data_json = json.loads(data_str)
                            evt_type = data_json.get('type', '')
                            if evt_type == 'message_start':
                                usage = data_json.get('message', {}).get('usage', {})
                                input_tokens = usage.get('input_tokens', 0)
                            elif evt_type == 'message_delta':
                                usage = data_json.get('usage', {})
                                output_tokens = usage.get('output_tokens', output_tokens)
                            elif evt_type == 'message_stop':
                                pass
                            else:
                                print(f'  [KimiProxy] SSE未识别事件type={evt_type!r} keys={list(data_json.keys())}', flush=True)
                except Exception as sse_err:
                    print(f'  [KimiProxy] SSE解析异常: {sse_err}', flush=True)

        # 9. 扣减积分（响应完成后扣减，不阻塞响应）
        _timing('stream_transfer', _t)
        print(f'  [KimiProxy] 流式结束: agent_id={agent_id} input_tokens={input_tokens} output_tokens={output_tokens} chunks={_chunk_count}', flush=True)
        if agent_id and (input_tokens or output_tokens):
            conn = _db_conn()
            try:
                _record_credit_usage(conn, agent_id, input_tokens, output_tokens, 0)
                conn.commit()
            finally:
                conn.close()

    else:
        # 非流式响应：读取完整响应，提取usage，扣减积分，返回
        _t = time.perf_counter()
        resp_body = resp.read()
        _timing('read_response', _t)
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(resp_body)

        # 解析usage
        try:
            resp_json = json.loads(resp_body)
            usage = resp_json.get('usage', {})
            input_tokens = usage.get('input_tokens', 0)
            output_tokens = usage.get('output_tokens', 0)

            print(f'  [KimiProxy] 非流式结束: agent_id={agent_id} input_tokens={input_tokens} output_tokens={output_tokens}', flush=True)
            if agent_id and (input_tokens or output_tokens):
                conn = _db_conn()
                try:
                    _record_credit_usage(conn, agent_id, input_tokens, output_tokens, 0)
                    conn.commit()
                finally:
                    conn.close()
        except Exception as ns_err:
            print(f'  [KimiProxy] 非流式usage解析异常: {ns_err}', flush=True)

    # 10. Memory Pipeline：检查是否触发 L1 事实提取（响应已发出，失败不影响客户端）
    if agent_id:
        _t = time.perf_counter()
        conn = _db_conn()
        try:
            memory_pipeline.check_and_run_pipeline(
                conn, agent_id,
                llm_call_func=_memory_pipeline_llm_call(body.get('model'), api_key=agent_api_key))
        except Exception as e:
            print(f'  [MemoryPipeline] pipeline check failed: {e}', flush=True)
        finally:
            conn.close()
        _timing('pipeline_check', _t)

    _timing('total', _t_start)


def _handle_douyin_parse(self):
    """POST /api/douyin/parse（需认证）
    请求体: {"url": "链接"} 或 {"text": "分享文本"}，可选 "transcribe": true
    响应: parse_douyin_video() 的结果
    """
    auth = _authenticate(self.headers, self.client_address[0], self)
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

    logger.info(f'  [Douyin] parse -> {url[:80]}... transcribe={transcribe}')
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
    auth = _authenticate(self.headers, self.client_address[0], self)
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
        logger.info(f'  [Douyin] downloading video...')
        video_path, temp_dir = _download_video_to_temp(video_url)
        logger.info(f'  [Douyin] video saved: {video_path} ({os.path.getsize(video_path)} bytes)')

        # 2. 提取音频
        logger.info(f'  [Douyin] extracting audio with ffmpeg...')
        audio_path = _extract_audio_with_ffmpeg(video_path)
        if not audio_path:
            self._send_json(502, {'success': False, 'error': 'ffmpeg 音频提取失败'})
            return
        logger.info(f'  [Douyin] audio saved: {audio_path} ({os.path.getsize(audio_path)} bytes)')

        # 3. 语音转文字
        logger.info(f'  [Douyin] transcribing with SiliconFlow...')
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
                logger.info(f'  [Douyin] cover extracted: {len(cover_base64)} bytes')
        except Exception as e:
            logger.info(f'  [Douyin] cover extraction skipped: {e}')

        # 5. 获取媒体信息（可选，不阻断主流程）
        media_info = None
        try:
            media_info = _get_media_info(video_path)
            if media_info:
                logger.info(f'  [Douyin] media info: {media_info.get("width")}x{media_info.get("height")}, {media_info.get("duration")}s')
        except Exception as e:
            logger.info(f'  [Douyin] media info skipped: {e}')

        logger.info(f'  [Douyin] transcribe OK, length={len(text)}')
        result_data = {'text': text}
        if cover_base64:
            result_data['cover_base64'] = cover_base64
        if media_info:
            result_data['media_info'] = media_info
        self._send_json(200, {'success': True, 'data': result_data})

    except ValueError as e:
        logger.error(f'  [Douyin] transcribe error: {e}')
        self._send_json(400, {'success': False, 'error': str(e)})
    except Exception as e:
        logger.error(f'  [Douyin] transcribe error: {e}')
        self._send_json(500, {'success': False, 'error': f'转写失败: {str(e)}'})
    finally:
        # 清理临时文件
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f'  [Douyin] temp cleaned: {temp_dir}')


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
    '_handle_proxy_kimi', '_handle_vision_describe',
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
            logger.info(f'  [DailyJob] 下次执行时间: {datetime.fromtimestamp(next_run).isoformat()} (约 {sleep_seconds // 3600}h {sleep_seconds % 3600 // 60}m 后)')
            time.sleep(sleep_seconds)
            _run_daily_memory_jobs(startup=False)
        except Exception as e:
            logger.error(f'  [DailyJob] 循环异常: {e}')
            time.sleep(60)


def _run_daily_memory_jobs(startup=False):
    """遍历所有 agent，执行核心记忆候选生成和知识归纳"""
    label = '启动补跑' if startup else '每日记忆任务'
    logger.info(f'  [DailyJob] 开始执行{label}...')
    agents = _load_agents()
    if not agents:
        logger.info(f'  [DailyJob] 无 agent，跳过')
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
                logger.error(f'  [DailyJob] {emp_id} 创建每日归纳 pending 失败: {e}')
            processed += 1
        except Exception as e:
            logger.error(f'  [DailyJob] agent {agent.get("id")} 处理失败: {e}')
    logger.info(f'  [DailyJob] {label}完成，共处理 {processed}/{len(agents)} 个 agent')


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
        logger.info(f'  [DailyJob] {emp_id} 检测到 {len(detected)} 组核心记忆冲突')
        return len(detected)
    except Exception as e:
        logger.error(f'  [DailyJob] {emp_id} 冲突检测失败: {e}')
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
    logger.info(f'  [DailyJob] {emp_id} 生成 {added} 条核心记忆候选')
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
            logger.error(f'  [DailyJob] {emp_id} 知识文档创建失败: {e}')

    if created_count > 0:
        # 标记所有本次参与归纳的源记忆为已归纳
        source_ids = [m['id'] for m, _ in uninducted]
        ms3.mark_memories_inducted(emp_id, source_ids)
        ms3.set_last_knowledge_induction_at(emp_id)
        logger.info(f'  [DailyJob] {emp_id} 归纳 {created_count} 篇知识文档')
        return created_count, None
    return 0, 'AI 返回的文档未通过校验（缺少标题或正文），未生成知识文档'


def _ensure_tls_cert():
    """确保 TLS 证书存在。优先用 mkcert（带本地 CA），其次 openssl 自签，最后抛错。
    返回 (cert_path, key_path) 或 None（应跳过 HTTPS）。"""
    if os.path.isfile(TLS_CERT_FILE) and os.path.isfile(TLS_KEY_FILE):
        # 已存在证书：检查有效期，太短就重新生成
        try:
            import datetime as _dt
            proc = subprocess.run(
                ['openssl', 'x509', '-enddate', '-noout', '-in', TLS_CERT_FILE],
                capture_output=True, text=True, timeout=5
            )
            if proc.returncode == 0:
                # 输出形如 "notAfter=May  1 12:00:00 2027 GMT"
                line = proc.stdout.strip()
                if 'notAfter=' in line:
                    end_str = line.split('notAfter=', 1)[1].strip()
                    end_dt = _dt.datetime.strptime(end_str, '%b %d %H:%M:%S %Y %Z')
                    if end_dt > _dt.datetime.now() + _dt.timedelta(days=7):
                        logger.info(f'  [TLS] 复用已有证书: {TLS_CERT_FILE} (到期 {end_str})')
                        return TLS_CERT_FILE, TLS_KEY_FILE
                    logger.info(f'  [TLS] 证书即将到期 ({end_str})，重新生成')
        except Exception as e:
            logger.warning(f'  [TLS] 证书有效期检查失败: {e}，将重新生成')

    os.makedirs(TLS_CERT_DIR, exist_ok=True)

    # 1) 优先 mkcert
    mkcert_path = shutil.which('mkcert')
    if mkcert_path:
        try:
            cmd = [mkcert_path, '-cert-file', TLS_CERT_FILE, '-key-file', TLS_KEY_FILE] + TLS_CERT_HOSTS
            logger.info(f'  [TLS] 用 mkcert 生成证书: {" ".join(cmd)}')
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and os.path.isfile(TLS_CERT_FILE):
                logger.info(f'  [TLS] mkcert 证书生成成功: {TLS_CERT_FILE}')
                return TLS_CERT_FILE, TLS_KEY_FILE
            logger.warning(f'  [TLS] mkcert 失败: {proc.stderr.strip() or proc.stdout.strip()}')
        except Exception as e:
            logger.warning(f'  [TLS] mkcert 异常: {e}')

    # 2) fallback: openssl 自签（覆盖同样 SAN 列表）
    openssl_path = shutil.which('openssl')
    if openssl_path:
        try:
            san = 'DNS:localhost,IP:127.0.0.1' + ''.join(f',IP:{h}' for h in TLS_CERT_HOSTS if h.count('.') == 3 and not h.startswith('*'))
            cmd = [
                openssl_path, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', TLS_KEY_FILE, '-out', TLS_CERT_FILE, '-days', '365',
                '-subj', '/CN=solobrave-local',
                '-addext', f'subjectAltName={san}'
            ]
            logger.info(f'  [TLS] 用 openssl 生成自签证书 (SAN={san})')
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and os.path.isfile(TLS_CERT_FILE):
                logger.info(f'  [TLS] openssl 自签证书生成成功: {TLS_CERT_FILE}')
                logger.warning('  [TLS] 自签证书需用户在浏览器手动信任，否则会有警告')
                return TLS_CERT_FILE, TLS_KEY_FILE
            logger.warning(f'  [TLS] openssl 失败: {proc.stderr.strip() or proc.stdout.strip()}')
        except Exception as e:
            logger.warning(f'  [TLS] openssl 异常: {e}')

    # 3) 都不可用：跳过 HTTPS
    logger.error('  [TLS] mkcert 和 openssl 都不可用，跳过 HTTPS（仅 HTTP）')
    return None


def _make_https_server(http_server_cls, handler_cls, bind, port, cert_file, key_file):
    """在 http_server_cls 基础上包一层 SSLContext，端口绑定后启动 serve_forever。
    返回 (server, thread) — thread 已在 daemon 模式跑 serve_forever。"""
    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
    ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    server = http_server_cls((bind, port), handler_cls)
    server.socket = ssl_ctx.wrap_socket(server.socket, server_side=True)
    t = threading.Thread(target=server.serve_forever, daemon=True, name=f'HTTPS-{port}')
    t.start()
    return server, t


def _start_wss_proxy(cert_file, key_file, bind, port, target_host, target_port):
    """在独立 daemon 线程里跑 asyncio 事件循环，启 wss://{bind}:{port} server，
    把每条进来的连接双向透传到 ws://{target_host}:{target_port}（OpenClaw Gateway）。
    依赖 websockets 库（pip install websockets）。库缺失时返回 None 并打 warning。

    关键：process_request 拦截握手把客户端 Origin 头存到 ws._client_origin；
    _proxy_handler 再把它原样传给 websockets.connect(uri, origin=...) 拼到上游 Origin 头。
    网关（OpenClaw v3）以 Origin 头做允许列表判定 + device identity 校验，缺了会
    直接拒绝（CONTROL_UI_ORIGIN_NOT_ALLOWED / "origin missing or invalid"）。"""
    try:
        import asyncio
        import websockets
    except ImportError:
        logger.warning('  [WSS] websockets 库未安装，跳过 WSS 代理；前端 wss:// 连接会失败')
        logger.warning('        安装: pip install websockets')
        return None, None

    def _extract_origin(request_headers):
        """从客户端握手 headers 里抠出 Origin。websockets 16+ 的 request.headers 是
        websockets.datastructures.Headers（大小写不敏感、MultiDict-like）。"""
        try:
            # 新版 Headers 支持 .get(None, 'Origin') 这种大小写不敏感查找
            for key in ('Origin', 'origin', 'ORIGIN'):
                v = request_headers.get(key)
                if v:
                    return v
        except Exception:
            pass
        # 兜底：手动遍历 items
        try:
            for k, v in request_headers.raw_items():
                if k.lower() == 'origin':
                    return v
        except Exception:
            pass
        return None

    async def _process_request(ws, request):
        """拦截握手：把客户端 Origin 头挂到 ws 实例上，供后续 _proxy_handler 读取。
        返回 None 让握手继续。"""
        origin = _extract_origin(request.headers)
        ws._client_origin = origin
        # 关闭 websockets 库自带的 Origin 校验（库默认会按 origins 参数拒掉没在白名单里的 origin，
        # 我们自己控制转发逻辑）
        return None

    async def _proxy_handler(client_ws):
        target_uri = f'ws://{target_host}:{target_port}'
        # 取出客户端 Origin；缺失时给个默认值（网关 allowedOrigins=*，任意值都行，关键是别空着）
        client_origin = getattr(client_ws, '_client_origin', None) or f'https://{bind}:{port}'
        # 兼容新旧 websockets：origin 参数在 13+ 才有；旧版用 additional_headers
        connect_kwargs = {
            'max_size': 64 * 1024 * 1024,
            'origin': client_origin,
        }
        upstream = None
        try:
            try:
                upstream = await websockets.connect(target_uri, **connect_kwargs)
            except TypeError:
                # 旧版 websockets 没有 origin 参数；用 additional_headers 兜底
                upstream = await websockets.connect(
                    target_uri,
                    max_size=64 * 1024 * 1024,
                    additional_headers={'Origin': client_origin} if client_origin else None,
                )
        except Exception as e:
            logger.error(f'  [WSS] 转发到 {target_uri} 失败: {e}')
            try:
                await client_ws.close()
            except Exception:
                pass
            return
        logger.info(f'  [WSS] 客户端已连接 (Origin={client_origin})，转发到 {target_uri}')

        async def _c2u():
            try:
                async for msg in client_ws:
                    _openclaw_sniff_c2u(msg)  # 看门狗：登记 chat.send run 开始
                    await upstream.send(msg)
            except websockets.ConnectionClosed:
                pass
            except Exception as e:
                logger.warning(f'  [WSS] client→upstream 异常: {e}')

        async def _u2c():
            try:
                async for msg in upstream:
                    _openclaw_sniff_u2c(msg)  # 看门狗：lifecycle end/error 清除 run
                    await client_ws.send(msg)
            except websockets.ConnectionClosed:
                pass
            except Exception as e:
                logger.warning(f'  [WSS] upstream→client 异常: {e}')

        try:
            await asyncio.gather(_c2u(), _u2c())
        finally:
            for ws in (client_ws, upstream):
                if ws is None:
                    continue
                try:
                    await ws.close()
                except Exception:
                    pass

    async def _serve():
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
        ssl_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        server = await websockets.serve(
            _proxy_handler, bind, port, ssl=ssl_ctx,
            process_request=_process_request,
            # origins=None 关闭库的 origin 校验，让我们完全交给上游网关判
            origins=None,
        )
        logger.info(f'  [WSS] 代理已启动: wss://{bind}:{port} → ws://{target_host}:{target_port}（含 Origin 透传）')
        await asyncio.Future()  # 永远等待

    def _thread_main():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except Exception as e:
            logger.error(f'  [WSS] 事件循环异常: {e}')
        finally:
            loop.close()

    t = threading.Thread(target=_thread_main, daemon=True, name=f'WSSProxy-{port}')
    t.start()
    return t, None


# ═══ OpenClaw 看门狗：health 探测 + dispatch 停滞检测 ═══
# 背景：gateway 是单 Node 进程，重活会把调度队列堵死——health 探活还活着，
# 但消息不再 dispatch（假死）。光看 health 发现不了，所以增加 dispatch 停滞检测：
# 经 WSS 透传代理嗅探 chat.send / lifecycle 帧，登记每个 run 的起止时间；
# run 超 150s 未结束、或 10 分钟内累计 2 次超时，判定假死并 kickstart 重启。

_OPENCLAW_RUNS = {}                    # sessionKey -> start_ts（嗅探 chat.send 登记）
_openclaw_runs_lock = threading.Lock()
_OPENCLAW_TIMEOUTS = deque()           # run 超时事件时间戳（10 分钟窗口）
_openclaw_health_fails = 0             # health 连续失败计数

WATCHDOG_INTERVAL_S = 30               # 巡检周期
WATCHDOG_RUN_STALL_S = 150             # 单 run 停滞阈值
WATCHDOG_TIMEOUT_WINDOW_S = 600        # 超时事件统计窗口
WATCHDOG_TIMEOUT_COUNT = 2             # 窗口内超时次数阈值
WATCHDOG_HEALTH_FAIL_LIMIT = 3         # health 连续异常次数阈值


def _openclaw_note_run_start(session_key, now=None):
    with _openclaw_runs_lock:
        _OPENCLAW_RUNS[session_key] = now if now is not None else time.time()


def _openclaw_note_run_end(session_key):
    """lifecycle end/error 时清除 run。payload 没带 sessionKey 且只剩一个活跃 run 时，
    按单活跃 run 推断清除（gateway 同 session 串行，这是安全的近似）。"""
    with _openclaw_runs_lock:
        if session_key and session_key in _OPENCLAW_RUNS:
            _OPENCLAW_RUNS.pop(session_key, None)
        elif not session_key and len(_OPENCLAW_RUNS) == 1:
            _OPENCLAW_RUNS.clear()


def _openclaw_sniff_c2u(msg):
    """嗅探 client→gateway 帧：chat.send 登记 run 开始。永不抛异常、绝不影响透传。"""
    try:
        if not isinstance(msg, str) or 'chat.send' not in msg:
            return
        data = json.loads(msg)
        if data.get('type') != 'req' or data.get('method') != 'chat.send':
            return
        params = data.get('params') or {}
        key = params.get('sessionKey') or params.get('idempotencyKey')
        if key:
            _openclaw_note_run_start(key)
    except Exception:
        pass


def _openclaw_sniff_u2c(msg):
    """嗅探 gateway→client 帧：lifecycle end/error 清除 run。永不抛异常。"""
    try:
        if not isinstance(msg, str) or 'lifecycle' not in msg:
            return
        data = json.loads(msg)
        if data.get('type') != 'event':
            return
        payload = data.get('payload') or data.get('params') or {}
        if payload.get('stream') != 'lifecycle':
            return
        phase = (payload.get('data') or {}).get('phase')
        if phase in ('end', 'error'):
            _openclaw_note_run_end(payload.get('sessionKey'))
    except Exception:
        pass


def _watchdog_check(now=None):
    """dispatch 停滞检测。返回重启原因字符串，不需要重启返回 None。

    - 存在 run 持续超过 WATCHDOG_RUN_STALL_S 未结束 → 假死（记录超时事件后重启）
    - 最近 WATCHDOG_TIMEOUT_WINDOW_S 内累计 >= WATCHDOG_TIMEOUT_COUNT 次超时 → 假死
    """
    now = now if now is not None else time.time()
    with _openclaw_runs_lock:
        stalled_keys = [k for k, ts in _OPENCLAW_RUNS.items()
                        if now - ts > WATCHDOG_RUN_STALL_S]
        for k in stalled_keys:
            _OPENCLAW_TIMEOUTS.append(now)
            _OPENCLAW_RUNS.pop(k, None)
        while _OPENCLAW_TIMEOUTS and now - _OPENCLAW_TIMEOUTS[0] > WATCHDOG_TIMEOUT_WINDOW_S:
            _OPENCLAW_TIMEOUTS.popleft()
        if stalled_keys:
            return f'run 持续超过 {WATCHDOG_RUN_STALL_S}s 未结束: {stalled_keys}'
        if len(_OPENCLAW_TIMEOUTS) >= WATCHDOG_TIMEOUT_COUNT:
            return (f'{WATCHDOG_TIMEOUT_WINDOW_S // 60} 分钟内 '
                    f'{len(_OPENCLAW_TIMEOUTS)} 次 run 未收到 lifecycle end')
    return None


def _watchdog_probe_health():
    """TCP 探测 gateway 端口存活（health 探活）"""
    try:
        with socket.create_connection((WSS_PROXY_TARGET_HOST, WSS_PROXY_TARGET_PORT), timeout=5):
            return True
    except Exception:
        return False


def _restart_openclaw_gateway(reason):
    """重启 OpenClaw gateway 并清空停滞记录。生产机是 macOS（launchctl）；
    其他平台只记录不执行。重启后通过通知让前端刷新重连。"""
    with _openclaw_runs_lock:
        _OPENCLAW_RUNS.clear()
        _OPENCLAW_TIMEOUTS.clear()
    global _openclaw_health_fails
    _openclaw_health_fails = 0
    if sys.platform == 'darwin':
        try:
            subprocess.run(
                ['/bin/sh', '-c', 'launchctl kickstart -k gui/$(id -u)/ai.openclaw.gateway'],
                timeout=30, capture_output=True)
            logger.info('  [Watchdog] launchctl kickstart 已执行')
        except Exception as e:
            logger.error(f'  [Watchdog] launchctl kickstart 执行失败: {e}')
    else:
        logger.warning('  [Watchdog] 非 macOS 环境，跳过 launchctl 重启（仅记录）')
    try:
        users = _read_json(USERS_FILE, []) or []
        admin = next((u for u in users if u.get('role') == 'admin'), None)
        if admin:
            _push_notification(admin.get('userId') or admin.get('id'), 'message',
                               'OpenClaw 网关已自动重启', f'原因: {reason}。请刷新页面重连。')
    except Exception as e:
        logger.error(f'  [Watchdog] 重启通知失败: {e}')


def _openclaw_watchdog_tick(restart_fn=None, health_probe=None):
    """单次巡检：health 探测 + dispatch 停滞检测。返回触发原因或 None（可注入 mock 便于测试）。"""
    global _openclaw_health_fails
    restart_fn = restart_fn or _restart_openclaw_gateway
    health_probe = health_probe or _watchdog_probe_health
    reason = None
    if health_probe():
        _openclaw_health_fails = 0
    else:
        _openclaw_health_fails += 1
        if _openclaw_health_fails >= WATCHDOG_HEALTH_FAIL_LIMIT:
            reason = f'health 连续 {_openclaw_health_fails} 次异常'
    if reason is None:
        reason = _watchdog_check()
    if reason:
        logger.warning(f'  [Watchdog] 检测到dispatch停滞触发重启: {reason}')
        restart_fn(reason)
    return reason


def _openclaw_watchdog_loop():
    while True:
        try:
            _openclaw_watchdog_tick()
        except Exception as e:
            logger.error(f'  [Watchdog] 巡检异常: {e}')
        time.sleep(WATCHDOG_INTERVAL_S)


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

    # 按实际监听端口补充 CORS Origin 白名单
    for _origin in (f'http://localhost:{PORT}', f'http://127.0.0.1:{PORT}'):
        if _origin not in ALLOWED_ORIGINS:
            ALLOWED_ORIGINS.append(_origin)

    # Override data directory if specified
    if args.data:
        global DATA_DIR, SECRET_FILE, USERS_FILE, AGENTS_FILE, GROUPS_FILE, CHATS_DIR, SETTINGS_FILE, TEAMS_FILE, PERMISSIONS_FILE, MEMORY_DIR, DB_PATH, INFLUENCER_DIR
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
        INFLUENCER_DIR = os.path.join(DATA_DIR, 'influencers')

    # 确保数据目录
    _ensure_data_dir()

    # 启动前快照 data/ 目录（保留最近 7 份，先于 init_db 以便保留迁移前状态）
    _backup_data_dir()

    # 数据修复：回填指定员工缺失的 openclawAgent（在快照之后执行，快照保留修复前状态）
    _backfill_openclaw_agents()

    # 初始化 SQLite 数据库（知识库）
    init_db()  # 保留旧 init_db 兼容
    ks.set_data_dir(DATA_DIR)
    ks.init_db()
    # 新版知识库表
    ks.init_kb_entries_db()

    # 达人库统一数据源：先把 legacy JSON（data/influencers/）幂等迁入 SQLite talents 表
    # （跳过 id 已存在的），再把 SQLite 导出回 JSON 作为只读缓存。顺序不能反，
    # 否则未迁移的 JSON 数据会被导出覆盖。
    _migrate_influencers_json_to_sqlite()
    _export_influencers_json_cache()
    # 旧数据迁移已停用（旧 knowledge 表/JSON 不再作为数据源，函数定义保留备查）
    # ks.knowledge_migrate_from_json(DATA_DIR, lambda eid: _get_agent_by_id(eid) or {})
    # 旧 knowledge 表数据迁移到新版 kb_entries（幂等）
    # ks.kb_migrate_from_old_knowledge()

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

    # 第三层防护：agents.json 损坏时先从启动快照自动恢复，再走后续初始化
    _validate_agents_json()

    # 启动时主动清理历史遗留默认员工数据
    _clean_agents_file()

    # 初始化默认管理员
    _init_default_admin()

    # 确保系统知识库管理员 AI 员工存在
    _ensure_knowledge_admin_agent()

    # 确保 teams.json 存在
    if not os.path.isfile(TEAMS_FILE):
        _save_teams([])
        logger.info('  [TEAM] 初始化 teams.json')

    # 检查静态目录
    if not os.path.isdir(STATIC_DIR):
        logger.warning(f'⚠️  静态文件目录不存在: {STATIC_DIR}')
        sys.exit(1)

    index_file = os.path.join(STATIC_DIR, 'index.html')
    if not os.path.isfile(index_file):
        logger.warning(f'⚠️  找不到 index.html: {index_file}')
        sys.exit(1)

    # 检查 OpenClaw CLI
    if os.path.isfile(OPENCLAW_CLI):
        logger.info(f'  [CLAW] OpenClaw CLI: OK ({OPENCLAW_CLI})')
    else:
        logger.info(f'  [CLAW] OpenClaw CLI: NOT FOUND ({OPENCLAW_CLI})')

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

    # OpenClaw 聊天积分同步：聊天走 WebSocket 直连网关、不经过 KimiProxy，
    # 积分只能靠 trajectory 回放补扣；除手动 /api/token-usage/sync 外，启动即跑一次并定时巡检
    def _credit_sync_loop():
        while True:
            try:
                result = _sync_token_usage_from_trajectories()
                if result.get('inserted') or result.get('repaired'):
                    logger.info(f'  [CreditsSync] trajectory 定时同步: {result}')
            except Exception as e:
                logger.error(f'  [CreditsSync] trajectory 定时同步失败: {e}')
            time.sleep(CREDIT_SYNC_INTERVAL_S)
    threading.Thread(target=_credit_sync_loop, daemon=True, name='CreditSyncLoop').start()
    logger.info(f'  [CreditsSync] 积分定时同步已启动（每 {CREDIT_SYNC_INTERVAL_S} 秒）')

    # OpenClaw 看门狗：health 探测 + dispatch 停滞检测（假死自愈）
    threading.Thread(target=_openclaw_watchdog_loop, daemon=True, name='OpenClawWatchdog').start()
    logger.info(f'  [Watchdog] OpenClaw 看门狗已启动（每 {WATCHDOG_INTERVAL_S}s 巡检）')
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

    # HTTPS：让前端进安全上下文（crypto.subtle 可用，wss:// 也能用）
    https_server = None
    https_thread = None
    https_port = HTTPS_PORT
    if HTTPS_ENABLED:
        try:
            tls_paths = _ensure_tls_cert()
            if tls_paths:
                cert_file, key_file = tls_paths
                # 选个未占用的 HTTPS 端口
                try:
                    https_server, https_thread = _make_https_server(
                        ReuseHTTPServer, SoloBraveHandler, BIND, https_port, cert_file, key_file
                    )
                except OSError as bind_err:
                    # 8443 被占了，试 8444/8445…
                    if 'Address already in use' in str(bind_err) or getattr(bind_err, 'errno', None) == 10048:
                        for alt_port in range(HTTPS_PORT + 1, HTTPS_PORT + 20):
                            try:
                                https_port = alt_port
                                https_server, https_thread = _make_https_server(
                                    ReuseHTTPServer, SoloBraveHandler, BIND, alt_port, cert_file, key_file
                                )
                                break
                            except OSError:
                                continue
                    if https_server is None:
                        raise bind_err
        except Exception as tls_err:
            logger.error(f'  [TLS] HTTPS 启动失败，回退到仅 HTTP: {tls_err}')
            https_server = None

    # WSS 代理：HTTPS 页面下浏览器强制 wss://，但 OpenClaw Gateway（ws://18789）不支持 TLS。
    # 在 wss://{BIND}:{WSS_PROXY_PORT} 启一个反向代理，把所有连接双向透传到 ws://Gateway。
    wss_thread = None
    if WSS_PROXY_ENABLED and https_server is not None:
        try:
            # 用同一份证书（前面 _ensure_tls_cert 已经写过）
            cert_path = TLS_CERT_FILE
            key_path = TLS_KEY_FILE
            if not (os.path.isfile(cert_path) and os.path.isfile(key_path)):
                cert_path = None  # _start_wss_proxy 内部会再尝试
            wss_thread, _ = _start_wss_proxy(
                cert_path or TLS_CERT_FILE,
                key_path or TLS_KEY_FILE,
                BIND, WSS_PROXY_PORT,
                WSS_PROXY_TARGET_HOST, WSS_PROXY_TARGET_PORT
            )
        except Exception as wss_err:
            logger.error(f'  [WSS] WSS 代理启动失败: {wss_err}')

    logger.info('=' * 56)
    logger.info('  [SOLO] SoloBrave Server (Auth Enabled)')
    logger.info('=' * 56)
    logger.info(f'  [DIR] 静态文件:  {STATIC_DIR}')
    logger.info(f'  [DIR] 数据目录:  {DATA_DIR}')
    logger.info(f'  [URL] 本机 HTTP:  http://localhost:{PORT}')
    logger.info(f'  [URL] 局域网 HTTP: http://{BIND}:{PORT}')
    if https_server is not None:
        logger.info(f'  [URL] 本机 HTTPS: https://localhost:{https_port}')
        logger.info(f'  [URL] 局域网 HTTPS: https://{BIND}:{https_port}')
        logger.info(f'  [TLS] 证书: {TLS_CERT_FILE}')
    else:
        logger.info(f'  [URL] HTTPS:      (未启用，仅 HTTP)')
    if wss_thread is not None:
        logger.info(f'  [URL] WSS 代理:   wss://{BIND}:{WSS_PROXY_PORT} → ws://{WSS_PROXY_TARGET_HOST}:{WSS_PROXY_TARGET_PORT}')
    else:
        logger.info(f'  [WSS] 代理:       (未启用)')
    logger.info(f'  [API] 认证:      /api/auth/*')
    logger.info(f'  [API] 用户管理:  /api/users/*')
    logger.info(f'  [API] Agent:     /api/agents/*')
    logger.info(f'  [API] 全局搜索:  GET /api/search')
    logger.info(f'  [API] 群组:      /api/groups/*')
    logger.info(f'  [API] 聊天:      /api/chat/*')
    logger.info(f'  [API] 代理:      POST /api/proxy')
    logger.info(f'  [API] 抖音解析:  POST /api/douyin/parse')
    logger.info(f'  [API] 抖音转写:  POST /api/douyin/transcribe')
    logger.info(f'  [API] OpenClaw:  /api/openclaw/*')
    logger.info(f'  [API] 技能:      /api/openclaw/skills/*')
    logger.info(f'  [CFG] 超时设置:  {PROXY_TIMEOUT}s')
    logger.info('=' * 56)
    logger.info('  Ctrl+C 停止服务\n')

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('\n\n  [STOP] 服务已停止')
        _brain_scheduler.stop()
        server.server_close()
        if https_server is not None:
            try:
                https_server.server_close()
            except Exception:
                pass


def _feishu_record_to_product(fields):
    def _g(name):
        return _feishu_extract_val(fields.get(name))
    def _g_json(name, default=None):
        v = _g(name)
        if not v:
            return default
        if isinstance(v, (dict, list)):
            return v
        try:
            return json.loads(v)
        except Exception:
            return default
    name = str(_g('商品名称') or '')
    if not name:
        return None
    commission_rates = _g_json('佣金率', {})
    if not commission_rates:
        cr = _g('佣金率')
        if cr:
            try:
                commission_rates = float(cr)
                commission_rates = {'自然流': commission_rates}
            except (ValueError, TypeError):
                import re
                pairs = re.findall(r'([\u4e00-\u9fa5a-zA-Z]+)(\d+(?:\.\d+)?)', str(cr))
                if pairs:
                    commission_rates = {name.strip(): float(val) for name, val in pairs}
                else:
                    commission_rates = {}
    sku_specs = _g_json('SKU规格', {})
    if not sku_specs:
        ss = _g('SKU规格')
        if ss and isinstance(ss, str):
            import re
            sku_specs = {}
            current_key = None
            current_vals = []
            for line in ss.split('\n'):
                line = line.strip()
                if not line:
                    continue
                if '：' in line or ':' in line:
                    if current_key and current_vals:
                        sku_specs[current_key] = current_vals
                    sep = '：' if '：' in line else ':'
                    key, _, vals = line.partition(sep)
                    current_key = key.strip()
                    vals = vals.strip()
                    if vals:
                        current_vals = [v.strip() for v in re.split(r'[、,，]', vals) if v.strip()]
                    else:
                        current_vals = []
                else:
                    if current_key:
                        current_vals.extend([v.strip() for v in re.split(r'[、,，]', line) if v.strip()])
            if current_key and current_vals:
                sku_specs[current_key] = current_vals
            if not sku_specs:
                sku_specs = {'规格': ss}
    tags_raw = _g('标签')
    if isinstance(tags_raw, list):
        tags = tags_raw
    elif tags_raw:
        import re
        tags = [t.strip() for t in re.split(r'[、,，]', str(tags_raw)) if t.strip()]
    else:
        tags = []
    audience = {}
    for key, col in [('gender', '购买性别'), ('age', '购买年龄'), ('region', '购买地区'), ('occupation', '购买人群')]:
        val = _g_json(col)
        if val and isinstance(val, dict):
            audience[key] = val
        else:
            sv = _g(col)
            if sv:
                parts = {}
                for pair in re.split(r'[、,，]', str(sv)):
                    pair = pair.strip()
                    if not pair:
                        continue
                    m = re.match(r'^(.+?)(\d+(?:\.\d+)?)\s*%\Z', pair)
                    if m:
                        parts[m.group(1).strip()] = float(m.group(2))
                    elif ':' in pair:
                        k, v = pair.split(':', 1)
                        try:
                            parts[k.strip()] = float(v.strip())
                        except ValueError:
                            parts[k.strip()] = v.strip()
                    else:
                        parts[pair] = 100
                if parts:
                    audience[key] = parts
    videos = _g_json('带货视频案例', [])
    if not videos:
        vv = _g('带货视频案例')
        if vv and isinstance(vv, str):
            try:
                videos = json.loads(vv)
            except Exception:
                videos = []
    channel_distribution = {}
    cd_raw = _g('渠道分布')
    if cd_raw:
        for line in re.split(r'[\n、]', str(cd_raw).strip()):
            line = line.strip()
            if not line:
                continue
            m = re.match(r'^(.+?)(\d+(?:\.\d+)?)\s*%\Z', line)
            if m:
                channel_distribution[m.group(1).strip()] = float(m.group(2))
    return {
        'name': name,
        'subtitle': str(_g('商品描述') or ''),
        'main_image': str(_g('主图URL') or ''),
        'price': float(_g('价格') or 0),
        'price_range': '',
        'brand': str(_g('品牌') or ''),
        'brand_id': '',
        'category': str(_g('类目') or ''),
        'scene': str(_g('穿搭场景') or ''),
        'sku_specs': sku_specs if sku_specs else {},
        'status': 'active',
        'monthly_sales': int(float(_g('月销量') or 0)),
        'monthly_gmv': float(_g('月GMV') or 0),
        'commission_rates': commission_rates if commission_rates else {},
        'commission_amount': float(_g('佣金金额') or 0),
        'conversion_rate': float(_g('转化率') or 0),
        'avg_order_value': 0,
        'influencer_count': int(float(_g('合作达人数') or 0)),
        'talent_count': int(float(_g('合作达人数') or 0)),
        'video_count': int(float(_g('视频数') or 0)),
        'live_count': int(float(_g('直播数') or 0)),
        'channel_distribution': channel_distribution if channel_distribution else {},
        'influencers': [],
        'audience': audience if audience else {},
        'ai_analysis': {},
        'videos': videos if videos else [],
        'tags': tags if tags else [],
        'selling_points': '',
        'original_price': float(_g('原价') or 0),
    }



if __name__ == '__main__':
    main()
