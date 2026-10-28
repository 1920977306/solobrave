"""配置：全部可用环境变量覆盖，默认值对齐 spec v1.1（独立库、8092 端口）。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file() -> None:
    """从项目根 `.env` 补齐环境变量（**只在没设过时**补，不覆盖真实环境变量）。

    ★ 为什么 daren_bot 也要读 `.env`：
      launchd 拉起的服务**没有 shell 环境**，配置只能落在 `.env` 里
      （`EnvironmentVariables` 只能写死几个键，改一次要 unload/load plist，太重）。
    ★ 只补「`os.environ` 里还没有」的键 ⇒ 真实环境变量优先
      （launchd / CI / 手工 `DAREN_XXX=... python -m daren_bot.main serve` 都能覆盖）。
    ★ 绝对路径读 `ROOT/.env`，不依赖 CWD（2026-10-21 §7 的坑：只依赖
      `os.environ` 会在「加载太晚」时静默失效）。
    """
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val
    except Exception:      # noqa: BLE001 —— 配置读取失败不许拖垮服务
        pass


_load_env_file()

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
# ★ 2026-10-28：专属佣金重算（达人报了自己后台的专属佣金 → 用新佣金重出一版商品结论）。
#   只调 1 次结论 LLM（比 qa 略重、远轻于 20 分的整链商品分析）⇒ 按 qa 同价。
COST_COMMISSION = 1
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
#   ⇒ 闸门见 `daren_bot/credits.py`；防漏测试见 `tests/test_credits_gate.py`
#     （入队侧）与 `tests/test_credits_gate_adversarial.py`（算力消耗侧）。
#
# ★★ 计费口径（2026-10-25 老大拍板，B 边对抗审查时确认写死）：
#     **按「功能」收费，不按「调用次数」收费。**
#     一次收费覆盖该功能在**一次请求内**的所有重试与降级环尝试：
#       - `qa` 收 1 分，但降级链 + 重试最多打 **3 次** LLM；
#       - `collect_product` 收 20 分，覆盖「开浏览器采集 + 出商品结论」全部调用；
#       - `weekly_report` 收 30 分，覆盖周报生成 + AI 经营建议。
#     ⇒ 为什么这么定：
#       ① 达人对「我发一条消息扣多少分」要有**确定答案** ——
#          按调用计费 ⇒ 同一条消息可能扣 1 分也可能扣 3 分 ⇒ 达人对不了账。
#       ② 降级环与重试是**我们的可用性工程**，不是达人的选择 ——
#          让达人为我们的重试买单不合理。
#       ③ 但**倍数必须登记**（`tests/test_credits_gate_adversarial.py` 的
#          `COMPUTE_SITES.max_calls` 字段）⇒ 成本核算不靠猜，靠表。
#     ⚠️ 口径要改 ⇒ 改这里 + 改那张登记表，两处一起改（单改一处 = 口径漂移）。
COST_TABLE = {
    "collect_douyin": COST_COLLECT_DOUYIN,
    "collect_product": COST_COLLECT_PRODUCT,
    "collect_product_cached": COST_PRODUCT_CACHED,
    "vision": COST_VISION,
    "qa": COST_QA,
    "commission": COST_COMMISSION,
    "video": COST_VIDEO,
    "weekly_collect": COST_WEEKLY_COLLECT,
    "weekly_report": COST_WEEKLY_REPORT,
    "recollect": COST_RECOLLECT,
}

# 对标分析：内测期每个达人最多同时跟踪的对标号数量
MAX_PEER_IDS = int(os.environ.get("DAREN_MAX_PEER_IDS", "5"))
# 商务助理：查/分析达人就是本职工作 ⇒ 对标号上限放宽（仍受「每号每周 1 次」约束）
MAX_PEER_IDS_COMMERCE = int(os.environ.get("DAREN_MAX_PEER_IDS_COMMERCE", "30"))

# ── 租户角色（2026-10-28 飞书助理平台化，老大拍板）──────────────────
# daren=达人助理（选品/账号分析/佣金，现状）；
# commerce=商务助理（查达人库/达人分析/图片识别，建联洽谈话术）；
# merchant=商家助理（**预留，不做**）。
ROLE_DAREN = "daren"
ROLE_COMMERCE = "commerce"
ROLE_MERCHANT = "merchant"
VALID_ROLES = (ROLE_DAREN, ROLE_COMMERCE, ROLE_MERCHANT)

# ── 运营告警（2026-10-25）──────────────────────────────────────────
# 运营会话：**留空 = 自动取该租户最近一次会话**
#   （`db.get_latest_chat_id()`，跟 weekly 的运营告警**同一条通道**）
#   ⇒ 老大不用手填 chat id，换了会话也不会失效。
#
# ★ 实测边界（2026-10-25，在这台机器上真跑过）：
#   - 老大自己的私聊 `oc_c08606d042c0e149b110f4390451e6ec` **发不进去**：
#     `code=230002 Bot/User can NOT be out of the chat` —— 那是**另一个 app** 的
#     会话，达人 bot 不在里面 ⇒ **别把它填进来**。
#   - `daren_boss` 那条通道老大确认「我看得见」⇒ 就是它。
#   ⇒ 要固定到别的会话，在 .env 里显式配 DAREN_OPS_CHAT_ID。
OPS_CHAT_ID = os.environ.get("DAREN_OPS_CHAT_ID", "")   # 空 = 自动取租户最近会话
OPS_TENANT_KEY = os.environ.get("DAREN_OPS_TENANT", "daren_boss")
OPS_ALERT_FILE = Path(os.environ.get("DAREN_OPS_ALERT_FILE",
                                    str(DATA_DIR / "ops_alerts.jsonl")))

# 采集通道登录态巡检间隔（秒）
LOGIN_WATCH_INTERVAL = int(os.environ.get("DAREN_LOGIN_WATCH_INTERVAL", "60"))
