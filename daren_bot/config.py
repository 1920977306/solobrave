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
