"""长连接管理：一租户一条 ws.Client，由本服务主动外连飞书（spec v1.1 第 1/2 章）。

- 每个 TenantConnection 独立 EventDispatcher（闭包绑定 tenant_key，
  因为飞书事件体本身不带 app_id，路由租户靠连接归属）。
- SDK 自带断线重连；重连不丢消息的验收口径见 spec 第 8 章 P0 用例。

多租户注意：lark-oapi SDK 的 ws.Client 使用【模块级全局 EventLoop】（client.py
顶部 `loop = asyncio.get_event_loop()`），两条长连接各跑一个线程时，
第二个 client.start() 会在已在运行的 loop 上 run_until_complete 直接炸
（"This event loop is already running"）。补丁：把模块全局 loop 换成
线程本地代理，每个 ws 线程拿到自己的真实 EventLoop。
"""
import asyncio
import logging
import threading

import lark_oapi as lark
import lark_oapi.ws.client as _ws_client_mod

from . import db

log = logging.getLogger("daren-bot.ws")


class _ThreadLocalLoop:
    """把 SDK 模块级 loop 换成线程本地：每个 ws 线程独立 EventLoop。"""

    def __init__(self):
        self._local = threading.local()

    def _real(self):
        lo = getattr(self._local, "loop", None)
        if lo is None:
            lo = asyncio.new_event_loop()
            asyncio.set_event_loop(lo)
            self._local.loop = lo
        return lo

    def __getattr__(self, item):
        return getattr(self._real(), item)


_ws_client_mod.loop = _ThreadLocalLoop()


class Sender:
    """按租户自己的凭证发消息（硬约束：严禁混用）。线程安全队列发送。"""

    def __init__(self, app_id: str, app_secret: str):
        self.client = (lark.Client.builder()
                       .app_id(app_id)
                       .app_secret(app_secret)
                       .build())
        self._queue: list = []
        self._cv = threading.Condition()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name=f"sender-{app_id[-6:]}")
        self._thread.start()

    def send_text(self, chat_id: str, text: str) -> None:
        with self._cv:
            self._queue.append((chat_id, text))
            self._cv.notify()

    def send_image_path(self, chat_id: str, image_path: str) -> None:
        """先传图拿 image_key 再发图消息（同步执行，worker 线程调用）。"""
        from lark_oapi.api.im.v1 import (CreateImageRequest, CreateImageRequestBody,
                                         CreateMessageRequest, CreateMessageRequestBody)
        import json as _json
        with open(image_path, "rb") as f:
            up = (CreateImageRequest.builder()
                  .request_body(CreateImageRequestBody.builder()
                                .image_type("message")
                                .image(f)
                                .build())
                  .build())
            resp = self.client.im.v1.image.create(up)
        if not resp.success():
            raise RuntimeError(f"image.create failed code={resp.code} msg={resp.msg}")
        image_key = resp.data.image_key
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(CreateMessageRequestBody.builder()
                             .receive_id(chat_id)
                             .msg_type("image")
                             .content(_json.dumps({"image_key": image_key}))
                             .build())
               .build())
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"message.create(image) failed code={resp.code} msg={resp.msg}")

    def send_card(self, chat_id: str, card: dict) -> str:
        """发一张飞书交互卡片（卡片 1.0）。返回 message_id（失败抛异常）。

        ★ 只支持**卡片 1.0**（2.0 的 action/behaviour 这个应用不认），
          约束见 `daren_bot/feishu_card.py` 文件头。
        """
        from lark_oapi.api.im.v1 import (CreateMessageRequest,
                                         CreateMessageRequestBody)
        import json as _json
        req = (CreateMessageRequest.builder()
               .receive_id_type("chat_id")
               .request_body(CreateMessageRequestBody.builder()
                             .receive_id(chat_id)
                             .msg_type("interactive")
                             .content(_json.dumps(card, ensure_ascii=False))
                             .build())
               .build())
        resp = self.client.im.v1.message.create(req)
        if not resp.success():
            raise RuntimeError(f"message.create(card) failed code={resp.code} msg={resp.msg}")
        return getattr(resp, "message_id", "") or ""

    def update_card(self, message_id: str, card: dict) -> str:
        """更新已发出的卡片（点按钮后展开详情用）。"""
        from lark_oapi.api.im.v1 import (PatchMessageRequest,
                                         PatchMessageRequestBody)
        import json as _json
        req = (PatchMessageRequest.builder()
               .message_id(message_id)
               .request_body(PatchMessageRequestBody.builder()
                             .content(_json.dumps(card, ensure_ascii=False))
                             .build())
               .build())
        resp = self.client.im.v1.message.patch(req)
        if not resp.success():
            raise RuntimeError(f"message.patch(card) failed code={resp.code} msg={resp.msg}")
        return message_id

    def download_image(self, message_id: str, image_key: str) -> bytes:
        """用本租户凭证下载消息图片（handler 3 秒约束内落盘用）。"""
        from lark_oapi.api.im.v1 import GetMessageResourceRequest
        req = (GetMessageResourceRequest.builder()
               .message_id(message_id)
               .file_key(image_key)
               .type("image")
               .build())
        resp = self.client.im.v1.message_resource.get(req)
        if not resp.success():
            raise RuntimeError(f"resource.get failed code={resp.code} msg={resp.msg}")
        data = getattr(resp, "file", None)
        if data is None:
            raise RuntimeError("resource.get returned empty body")
        return data

    def _loop(self):
        while not self._closed:
            with self._cv:
                while not self._queue and not self._closed:
                    self._cv.wait()
                if self._closed:
                    return
                chat_id, text = self._queue.pop(0)
            try:
                from lark_oapi.api.im.v1 import (CreateMessageRequest,
                                                 CreateMessageRequestBody)
                import json as _json
                req = (CreateMessageRequest.builder()
                       .receive_id_type("chat_id")
                       .request_body(CreateMessageRequestBody.builder()
                                     .receive_id(chat_id)
                                     .msg_type("text")
                                     .content(_json.dumps({"text": text}, ensure_ascii=False))
                                     .build())
                       .build())
                resp = self.client.im.v1.message.create(req)
                if not resp.success():
                    log.warning("send failed chat=%s code=%s msg=%s",
                                chat_id, resp.code, resp.msg)
            except Exception:
                log.exception("send exception")

    def close(self):
        self._closed = True
        with self._cv:
            self._cv.notify_all()


class TenantConnection:
    def __init__(self, tenant: dict, app_secret: str, p2_handler_factory,
                 card_action_factory=None):
        self.tenant = tenant
        self.tenant_key = tenant["tenant_key"]
        builder = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(
                       p2_handler_factory(tenant)))
        # ★ 卡片动作回调（card.action.trigger）——
        #   走**长连接** ⇒ 不需要公网地址，也不用在飞书后台填「卡片请求网址」。
        if card_action_factory is not None:
            try:
                builder = builder.register_p2_card_action_trigger(
                    card_action_factory(tenant))
            except Exception:
                log.exception("register card action handler failed tenant=%s",
                              self.tenant_key)
        dispatcher = builder.build()
        self._client = lark.ws.Client(app_id=tenant["app_id"],
                                      app_secret=app_secret,
                                      event_handler=dispatcher,
                                      log_level=lark.LogLevel.DEBUG)
        self.sender = Sender(tenant["app_id"], app_secret)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f"ws-{self.tenant_key}")
        self._thread.start()

    def _run(self):
        log.info("ws client starting: tenant=%s app_id=%s",
                 self.tenant_key, self.tenant["app_id"])
        try:
            self._client.start()  # 阻塞；SDK 内部断线自动重连
        except Exception:
            log.exception("ws client exited: tenant=%s", self.tenant_key)
        else:
            log.warning("ws client ended unexpectedly: tenant=%s", self.tenant_key)


class WsManager:
    """按 bot_tenants 表维持长连接集合，支持运行期 reload 新增租户。"""

    def __init__(self, master_key: bytes, p2_handler_factory,
                 card_action_factory=None):
        self._master_key = master_key
        self._p2_factory = p2_handler_factory
        self._card_action_factory = card_action_factory
        self._conns: dict[str, TenantConnection] = {}  # app_id -> conn
        self._lock = threading.Lock()

    def reload(self):
        from .crypto_util import decrypt_secret
        with self._lock:
            seen = set()
            for t in db.get_active_tenants():
                seen.add(t["app_id"])
                if t["app_id"] in self._conns:
                    continue
                try:
                    secret = decrypt_secret(self._master_key, t["app_secret_enc"])
                except Exception:
                    log.exception("decrypt secret failed tenant=%s", t["tenant_key"])
                    continue
                conn = TenantConnection(t, secret, self._p2_factory,
                                       self._card_action_factory)
                self._conns[t["app_id"]] = conn
                conn.start()
                log.info("tenant online requested: %s", t["tenant_key"])
            # status != active 的连接本进程不做热摘除（内测规模，重启即收敛）

    def get_sender(self, tenant_key: str) -> Sender:
        with self._lock:
            for conn in self._conns.values():
                if conn.tenant_key == tenant_key:
                    return conn.sender
        return None

    def stats(self) -> dict:
        with self._lock:
            return {"tenants": len(self._conns),
                    "detail": {c.tenant_key: (c._thread.is_alive() if c._thread else False)
                               for c in self._conns.values()}}
