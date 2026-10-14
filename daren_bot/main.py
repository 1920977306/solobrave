"""daren-bot 主入口（独立进程 :8092，spec v1.1 第 2 章）。

用法:
  .venv-daren/bin/python -m daren_bot.main init-db
  .venv-daren/bin/python -m daren_bot.main add-tenant <tenant_key> <app_id> <app_secret> [douyin_id]
  .venv-daren/bin/python -m daren_bot.main serve          # 长连接 + worker + 健康检查

P0 门禁（spec 第 1/8 章）: add-tenant + serve 起来后，飞书后台把该应用事件订阅
切成「使用长连接接收事件」并保存 → 给机器人发测试消息 → 10 秒内日志出现
im.message.receive_v1 即通过；kill 进程重启 → 消息不丢即重连通过。
"""
import argparse
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, crypto_util, db
from .handler import Router
from .worker import Worker
from .ws_manager import WsManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
# lark SDK 自带 logger 默认 WARNING, 长连接状态(connected/connect failed)都看不到。
# 且它自带的 handler 绑 import 时的 stdout, daemon 场景不可靠 —— 换成 stderr 显式接管。
_lark = logging.getLogger("Lark")
_lark.setLevel(logging.DEBUG)
for _h in list(_lark.handlers):
    _lark.removeHandler(_h)
_lark.addHandler(logging.StreamHandler(sys.stderr))
log = logging.getLogger("daren-bot.main")
_STATE = {"ws": None}


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.rstrip("/") in ("", "/healthz"):
            body = json.dumps({
                "ok": True,
                "service": "daren-bot",
                "db": str(config.DB_PATH),
                "ws": _STATE["ws"].stats() if _STATE["ws"] else {},
            }, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # 静默访问日志
        pass


def cmd_init_db(_args):
    db.init_db()
    print(f"db ready: {config.DB_PATH}")


def cmd_add_tenant(args):
    db.init_db()
    key = crypto_util.load_or_create_master_key()
    enc = crypto_util.encrypt_secret(key, args.app_secret)
    db.add_tenant(args.app_id, args.tenant_key, enc,
                  bind_douyin_id=args.douyin_id or "")
    print(f"tenant upserted: {args.tenant_key} app_id={args.app_id} "
          f"bind_douyin_id={args.douyin_id or '(未绑定)'} "
          f"初始积分={config.INITIAL_CREDITS}")


def cmd_serve(_args):
    db.init_db()
    master_key = crypto_util.load_or_create_master_key()

    # 迟绑定：ws.reload() 调用 factory 时 router 必然已存在
    holder: dict = {}
    def p2_factory(tenant: dict):
        return holder["router"].make_p2_handler(tenant)

    ws = WsManager(master_key, p2_factory)
    holder["router"] = Router(ws)
    _STATE["ws"] = ws
    ws.reload()

    worker = Worker(ws)
    worker.start()

    httpd = ThreadingHTTPServer(("127.0.0.1", config.HTTP_PORT), _HealthHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="health-http").start()
    log.info("daren-bot serving: health=http://127.0.0.1:%d/healthz", config.HTTP_PORT)

    try:
        import time
        while True:
            time.sleep(5)
            # 运行期新增租户每 5s 收敛一次（SOP 凭证入库后无需重启）
            ws.reload()
    except KeyboardInterrupt:
        log.info("shutting down")
    finally:
        worker.stop()
        httpd.shutdown()


def main():
    p = argparse.ArgumentParser(prog="daren_bot")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    at = sub.add_parser("add-tenant")
    at.add_argument("tenant_key")
    at.add_argument("app_id")
    at.add_argument("app_secret")
    at.add_argument("douyin_id", nargs="?", default="")
    sub.add_parser("serve")
    args = p.parse_args()
    {"init-db": cmd_init_db,
     "add-tenant": cmd_add_tenant,
     "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    main()
