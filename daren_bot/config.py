"""配置：全部可用环境变量覆盖，默认值对齐 spec v1.1（独立库、8092 端口）。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("DAREN_DATA_DIR", ROOT / "data" / "daren-bot"))
DB_PATH = Path(os.environ.get("DAREN_DB", DATA_DIR / "daren_bot.db"))
IMAGE_DIR = Path(os.environ.get("DAREN_IMAGE_DIR", DATA_DIR / "images"))
MASTER_KEY_PATH = Path(os.environ.get("DAREN_MASTER_KEY", DATA_DIR / "master.key"))

HTTP_PORT = int(os.environ.get("DAREN_PORT", "8092"))

# 初始额度（spec 第 7 章：500 分 ≈ 5 次周抓 + 日常问答余量）
INITIAL_CREDITS = int(os.environ.get("DAREN_INITIAL_CREDITS", "500"))

# 积分价格（spec 第 7 章）
COST_COLLECT_DOUYIN = 100
COST_COLLECT_PRODUCT = 20
COST_PRODUCT_CACHED = 5
COST_VISION = 10
COST_QA = 1
COST_VIDEO = 30        # 视频拆解：下载+妙记转写+vision+报告，重操作
# ★ 2026-10-25 老大：「每一个功能都需要积分控制 毕竟是消耗算力 不消耗的就算了」
#   —— 下面三个是体检时查出来的**漏网**（消耗算力但一直免费）：
COST_WEEKLY_COLLECT = 100   # 周报触发的自动采集（浏览器采集，跟达人主动采集同价）
COST_WEEKLY_REPORT = 30     # 周报生成（LLM 写经营报告，比问答重、比视频轻）
COST_RECOLLECT = 100        # 采集失败后的重采（= 又采一次，成本同首次采集）

# ★★ 计费动作总表（**单一真源**）——
#   凡是**消耗算力**（采集 / LLM 判分 / 视觉 / 转写 / 写报告）的功能，
#   都必须在这里登记一个动作名 + 价格，入口一律走
#   `credits.charge_and_enqueue()`（扣费+入队**一个原子操作**）。
#   **不消耗算力**的功能（纯查库 + 纯函数：排品表查询 / 选品推荐 / 接品登记 /
#   余额查询 / 订阅开关）**不扣分**，也不许出现在这张表里
#   （塞进来 = 给白干的路开闸门）。
#   ⇒ 闸门见 `daren_bot/credits.py`；防漏测试见 `tests/test_credits_gate.py`。
COST_TABLE = {
    "collect_douyin": COST_COLLECT_DOUYIN,
    "collect_product": COST_COLLECT_PRODUCT,
    "collect_product_cached": COST_PRODUCT_CACHED,
    "vision": COST_VISION,
    "qa": COST_QA,
    "video": COST_VIDEO,
    "weekly_collect": COST_WEEKLY_COLLECT,
    "weekly_report": COST_WEEKLY_REPORT,
    "recollect": COST_RECOLLECT,
}

# 对标分析：内测期每个达人最多同时跟踪的对标号数量
MAX_PEER_IDS = int(os.environ.get("DAREN_MAX_PEER_IDS", "5"))

# ── 运营告警（2026-10-25）──────────────────────────────────────────
# 飞书运营会话：**默认空 = 不发飞书**。
# ★ 为什么默认不发（实测结论，不是猜的）：
#   ① 老大私聊是**另一个 app** 的会话，达人 bot 发不进去
#      （实测 `code=230002 Bot/User can NOT be out of the chat`）；
#   ② 既有 daren_boss 告警通道是**达人**的聊天 —— 把内部细节发过去
#      = 泄漏给第三方达人。
#   ⇒ 默认只走「本机弹窗 + 日志 + 落盘 + admin 页面」四条本机通道。
#   ⇒ 老大要飞书推送时，在 .env 里显式配 DAREN_OPS_CHAT_ID（他自己指定的会话）。
OPS_CHAT_ID = os.environ.get("DAREN_OPS_CHAT_ID", "")
OPS_TENANT_KEY = os.environ.get("DAREN_OPS_TENANT", "daren_boss")
OPS_ALERT_FILE = Path(os.environ.get("DAREN_OPS_ALERT_FILE",
                                    str(DATA_DIR / "ops_alerts.jsonl")))

# 采集通道登录态巡检间隔（秒）
LOGIN_WATCH_INTERVAL = int(os.environ.get("DAREN_LOGIN_WATCH_INTERVAL", "60"))
