"""异步任务 worker —— 重活全在这里（spec 3 秒硬约束的反面）。

内测接入顺序：douyin → product → image（vision）。
每个任务类型一个 executor；未接入的类型返回 NotImplementedError，
由 Worker 统一走「任务失败 → 自动返还积分 → 卡片告知」路径（spec 4.1 失败分支）。
"""
import logging
import threading
import time

from . import db

log = logging.getLogger("daren-bot.worker")


def execute_douyin_collect(task: dict) -> str:
    """抖音号采集：Kimi 浏览器 → 巨量团长系统（开发中，spec 4.1）。"""
    raise NotImplementedError("浏览器采集链路开发中")


def execute_product_collect(task: dict) -> str:
    """商品链接抓取：浏览器 + 共享缓存（开发中，spec 4.3）。"""
    raise NotImplementedError("浏览器采集链路开发中")


def execute_image_vision(task: dict) -> str:
    """商品截图 vision 识别（开发中，spec 4.2；废图路径=识别不出→返还）。"""
    raise NotImplementedError("vision 识别链路开发中")


EXECUTORS = {
    "douyin": execute_douyin_collect,
    "product": execute_product_collect,
    "image": execute_image_vision,
}


class Worker(threading.Thread):
    def __init__(self, ws_manager, poll_interval: float = 2.0):
        super().__init__(daemon=True, name="daren-worker")
        self.wm = ws_manager
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        log.info("worker started")
        while not self._stop_event.is_set():
            task = db.claim_next_task()
            if not task:
                self._stop_event.wait(self.poll_interval)
                continue
            self._process(task)

    def _process(self, task: dict) -> None:
        tk = task["tenant_key"]
        log.info("task claimed: id=%s type=%s target=%s tenant=%s",
                 task["id"], task["type"], task["target"], tk)
        executor = EXECUTORS.get(task["type"])
        try:
            if executor is None:
                raise NotImplementedError(f"未知任务类型 {task['type']}")
            result_summary = executor(task)
        except NotImplementedError as e:
            self._fail(task, str(e))
        except Exception as e:
            log.exception("task failed: id=%s", task["id"])
            self._fail(task, f"{type(e).__name__}: {e}")
        else:
            db.mark_task(task["id"], "done")
            if task["type"] == "douyin":
                db.incr_week_count(tk, task["target"])
            self._notify(tk, f"任务完成：{result_summary}", task.get("chat_id", ""))

    def _fail(self, task: dict, reason: str) -> None:
        db.mark_task(task["id"], "failed", reason)
        cost = int(task.get("credits_cost") or 0)
        if cost > 0:
            db.refund(task["tenant_key"], cost, "refund", ref_task_id=task["id"])
        self._notify(task["tenant_key"],
                     f"任务失败：{reason}（已返还 {cost} 积分，说「重新采集」可重试）",
                     task.get("chat_id", ""))

    def _notify(self, tenant_key: str, text: str, chat_id: str = '') -> None:
        """完成/失败推送。内测先用文本消息，卡片格式接入时替换（spec 第 6 章）。"""
        sender = self.wm.get_sender(tenant_key)
        if sender is None:
            log.warning("notify skipped, sender absent: tenant=%s", tenant_key)
            return
        if not chat_id:
            log.info("[notify-nochat→%s] %s", tenant_key, text)
            return
        sender.send_text(chat_id, text)
