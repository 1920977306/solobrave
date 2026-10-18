"""异步任务 worker —— 重活全在这里（spec 3 秒硬约束的反面）。

内测接入顺序：douyin（已接 buyin_worker 管道）→ product → image（vision）。
未接入的类型返回 NotImplementedError，由 Worker 统一走
「任务失败 → 自动返还积分 → 卡片告知」路径（spec 4.1 失败分支）。
"""
import json
import logging
import threading
import time

from . import db
from . import executors

log = logging.getLogger("daren-bot.worker")


EXECUTORS = {
    "douyin": executors.execute_douyin_collect,
    "product": executors.execute_product_collect,
    "image": executors.execute_image_vision,
    "qa": executors.execute_qa,
    "video": executors.execute_video,
}


def _weekly_executor(task: dict) -> str:
    from . import weekly
    return weekly.execute_weekly_report(task)


EXECUTORS["weekly_report"] = _weekly_executor


class Worker(threading.Thread):
    def __init__(self, ws_manager, poll_interval: float = 2.0):
        super().__init__(daemon=True, name="daren-worker")
        self.wm = ws_manager
        self.poll_interval = poll_interval
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        n = db.reclaim_stale_collecting()
        if n:
            log.warning("reclaimed %d stale collecting task(s) -> pending", n)
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
                # 周报快照 + 自动采集系统账（weekly.py，失败不阻塞主流程）
                try:
                    from . import weekly
                    weekly.post_collect(tk, task["target"].strip(), task)
                except Exception:
                    log.exception("weekly post_collect failed: %s", task["target"])
            if task["type"] == "douyin" and task.get("chat_id"):
                # 报告推送：优先可视化长图卡片，渲染失败回退纯文字
                from .executors import notify_report
                notify_report(self.wm.get_sender(tk), task["chat_id"],
                              task["target"], result_summary)
            elif task["type"] == "qa" and task.get("chat_id"):
                # 问答推送：优先可视化卡片，渲染失败回退纯文字
                executors.notify_qa_card(self.wm.get_sender(tk), task["chat_id"],
                                         task["target"], result_summary, tk)
            elif task["type"] in ("product", "image") and task.get("chat_id"):
                # 商品分析推送（链接抓取/截图识别共用）：分节卡片 + 文字版 + 商品推荐记录表
                # executor 返回结构化信封 {"title","stats","md","source"?}
                title, stats, source, md = result_summary, {}, "", ""
                try:
                    env = json.loads(result_summary)
                    title = env.get("title") or "商品分析"
                    stats = env.get("stats") or {}
                    source = env.get("source") or ""
                    md = env.get("md") or ""
                except Exception:
                    title, _, md = (result_summary or "").partition("\n")
                executors.notify_product_card(self.wm.get_sender(tk), task["chat_id"],
                                              title, md, tk, stats, source)
            elif task["type"] == "video" and task.get("chat_id"):
                # 视频拆解推送：优先分节卡片长图，渲染失败回退纯文本
                executors.notify_video_card(self.wm.get_sender(tk), task["chat_id"],
                                            result_summary, tk)
            elif task["type"] == "weekly_report" and task.get("chat_id"):
                # 周报推送：长图卡片 + 摘要 + 文字版（OpenAPI，不走 ws）
                from . import weekly
                weekly.notify_weekly(self.wm.get_sender(tk), task["chat_id"],
                                     result_summary)
            else:
                # qa/video 是对话型/报告型推送，不带「任务完成：」前缀
                prefix = "" if task["type"] in ("qa", "video") else "任务完成："
                self._notify(tk, f"{prefix}{result_summary}", task.get("chat_id", ""))

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
